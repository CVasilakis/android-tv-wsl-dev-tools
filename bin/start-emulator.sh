#!/usr/bin/env bash
# Boots the development emulator and returns once Android has finished booting, so it can be
# chained: start-emulator.sh && ./gradlew installDebug
#
# Usage:   start-emulator.sh [avd-name] [extra emulator flags...]
#            start-emulator.sh                       # the default AVD (see below)
#            start-emulator.sh -wipe-data            # default AVD, factory reset
#            start-emulator.sh my_tv -gpu host       # another AVD, hardware rendering
#          EMULATOR_TOOLBAR=show start-emulator.sh   # keep a clickable side toolbar (WSL)
#          start-emulator.sh --help
# Which AVD: the name given, else $ADT_AVD, else tv_api25 if it exists, else the only
#          Android TV AVD. Anything else is an error that lists the AVDs.
# Log:     ${TMPDIR:-/tmp}/emulator-<avd-name>.log  (look here first if the window never appears)
# Stop:    adb -s <serial> emu kill   (saves a Quick Boot snapshot, so the next start takes seconds)
#
# Common errors:
#   "No access to /dev/kvm"
#       The user isn't in the kvm group: sudo usermod -aG kvm $USER. If /dev/kvm doesn't exist
#       at all, hardware virtualization is off (in the firmware, or nested virtualization for WSL).
#   "the emulator exited"
#       It failed to start or crashed; the log's last lines are printed. Hangs at "Emulator
#       starting..." without that error mean Android can't boot (e.g. a corrupt snapshot:
#       add -no-snapshot-load).
#
# Everything below exists for a reason; see the comment on each step before removing one.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: start-emulator.sh [avd-name] [emulator flags...]

Boots an Android TV emulator in the background and returns once Android is ready, so it can be
chained: start-emulator.sh && ./gradlew installDebug

  avd-name        the AVD to boot. Default: $ADT_AVD, else tv_api25 if it exists, else the
                  only Android TV AVD
  emulator flags  passed on to the emulator, e.g. -wipe-data, -no-snapshot-load, -no-window,
                  -gpu host (replaces the default -gpu swiftshader_indirect)
  -h, --help      show this help

Environment:
  ADT_AVD           default AVD name
  EMULATOR_TOOLBAR  WSL only: hide (default) or show the emulator's side toolbar
                    (see bin/README.md)

If the AVD is already running, only prints its serial. The emulator keeps running after this
script exits; stop it with: adb -s <serial> emu kill
EOF
}
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

# shellcheck source=../lib/lib.sh
source "$(dirname "$(readlink -f "$0")")/../lib/lib.sh"
require "$EMULATOR" "emulator"
require "$ADB" "platform-tools"

# The first argument is the AVD name, unless it's already an emulator flag (e.g. just -wipe-data).
AVD_NAME=""
if [ $# -gt 0 ] && [[ "$1" != -* ]]; then
    AVD_NAME="$1"
    shift
fi

in_list() {                               # in_list <word> <list...>
    local word="$1" item
    shift
    for item in "$@"; do
        if [ "$item" = "$word" ]; then return 0; fi
    done
    return 1
}
is_tv_avd() {
    local dir
    dir="$(avd_dir "$1")" && grep -Eq '^tag\.ids? *=.*(android-tv|google-tv)' "$dir/config.ini"
}

# Pick the AVD. tv_api25 is what create-avd.sh makes; a collaborator's own TV AVD is used when
# it's the only one, so nobody has to rename theirs.
mapfile -t AVDS < <(list_avds)
[ -n "$AVD_NAME" ] || AVD_NAME="${ADT_AVD:-}"
if [ -z "$AVD_NAME" ]; then
    if in_list tv_api25 "${AVDS[@]}"; then
        AVD_NAME=tv_api25
    else
        TV_AVDS=()
        for avd in "${AVDS[@]}"; do
            if is_tv_avd "$avd"; then TV_AVDS+=("$avd"); fi
        done
        if [ ${#TV_AVDS[@]} -eq 1 ]; then AVD_NAME="${TV_AVDS[0]}"; fi
    fi
fi
if [ -z "$AVD_NAME" ] || ! in_list "$AVD_NAME" "${AVDS[@]}"; then
    if [ -n "$AVD_NAME" ]; then problem="there's no AVD named '$AVD_NAME'."
    else problem="can't tell which AVD to use: no tv_api25 and not exactly one Android TV AVD."; fi
    die "$problem
AVDs found: ${AVDS[*]:-none}
Pass a name ($(command_for start-emulator.sh) <avd-name>), set ADT_AVD, or create one with
$(command_for create-avd.sh)."
fi

# KVM is mandatory for x86 images (without it the emulator exits with "x86 emulation currently
# requires hardware acceleration"). After 'usermod -aG kvm' the new group only applies to new
# login sessions (on WSL: after `wsl --shutdown`), so re-run this script through 'sg kvm', which
# grants the group right away. _IN_SG_KVM prevents an endless re-exec loop if /dev/kvm is
# still not writable inside sg. The getent check matters: 'sg' asks for a group password
# when the user isn't a member.
if [ ! -e "$KVM_DEVICE" ]; then
    die "$KVM_DEVICE doesn't exist: enable hardware virtualization (VT-x/AMD-V in the firmware
settings; nested virtualization when running inside WSL or a VM)."
fi
if [ ! -w "$KVM_DEVICE" ]; then
    if getent group kvm | grep -qw "$USER" && [ -z "${_IN_SG_KVM:-}" ]; then
        exec sg kvm -c "_IN_SG_KVM=1 $(printf '%q ' "$BIN_DIR/start-emulator.sh" "$AVD_NAME" "$@")"
    fi
    die "No access to $KVM_DEVICE. Run: sudo usermod -aG kvm \$USER"
fi

# Starting an AVD twice fails (its files are locked), so an already running one is just reported.
# Emulators are matched by AVD name, never by "the only device": other emulators and phones may
# be connected.
for serial in $(running_emulators); do
    if [ "$(emulator_avd "$serial")" = "$AVD_NAME" ]; then
        echo "Emulator '$AVD_NAME' is already running as $serial."
        exit 0
    fi
done

# -gpu swiftshader_indirect: software rendering, works on any host including WSLg (see
#   create-avd.sh). Left out when the caller passes their own -gpu.
# -no-audio: sound is rarely needed; one less host integration (PulseAudio) to go wrong.
# -no-boot-anim: faster cold boots.
# nohup + & keeps the emulator running after this script (and the terminal) exits.
GPU=(-gpu swiftshader_indirect)
if in_list -gpu "$@"; then GPU=(); fi
LOG="${TMPDIR:-/tmp}/emulator-$AVD_NAME.log"
nohup "$EMULATOR" -avd "$AVD_NAME" \
    "${GPU[@]}" \
    -no-boot-anim \
    -no-audio \
    "$@" > "$LOG" 2>&1 &
EMULATOR_PID=$!
echo "Emulator '$AVD_NAME' starting (log: $LOG)..."

# Without this, a crashed emulator would leave the loops below waiting forever.
check_alive() {
    kill -0 "$EMULATOR_PID" 2>/dev/null || die "the emulator exited. Last lines of $LOG:
$(tail -n 15 "$LOG")"
}

# Find our emulator's serial (emulator-<port>): the emulator picks the first free port, so ask
# each running emulator for its AVD name.
SERIAL=""
while [ -z "$SERIAL" ]; do
    check_alive
    for serial in $(running_emulators); do
        if [ "$(emulator_avd "$serial")" = "$AVD_NAME" ]; then SERIAL="$serial"; fi
    done
    if [ -z "$SERIAL" ]; then sleep 1; fi
done

# adbd answers long before the package manager is up, and installing at that point fails, so wait
# for sys.boot_completed. tr strips the '\r' that adb shell appends.
until [ "$("$ADB" -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do
    check_alive
    sleep 2
done
echo "Emulator '$AVD_NAME' booted as $SERIAL."
# With several devices connected, adb refuses to guess and `./gradlew installDebug` installs on
# all of them. Both honor ANDROID_SERIAL.
if [ "$("$ADB" devices | awk 'NR > 1 && NF' | wc -l)" -gt 1 ]; then
    echo "Other devices are connected too. To make adb and Gradle use only this one:"
    echo "  export ANDROID_SERIAL=$SERIAL"
fi

# Under WSLg the side toolbar can't be clicked, and while it's shown it takes keyboard focus away
# from the emulator screen (typed keys never reach Android). Hiding it is the default because
# keyboard control matters more for a TV app. See wslg-toolbar.py for details. Skipped outside
# WSL (not needed) and with -no-window (there's no toolbar). '|| true': a failure here must not
# report the already-booted emulator as failed.
if is_wsl && ! in_list -no-window "$@"; then
    python3 "$BIN_DIR/wslg-toolbar.py" "$AVD_NAME" "${EMULATOR_TOOLBAR:-hide}" || true
fi
