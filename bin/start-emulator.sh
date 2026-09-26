#!/usr/bin/env bash
# Boots the development emulator and returns once Android has finished booting, so it can be
# chained: start-emulator.sh && ./gradlew installDebug
#
# Usage:   start-emulator.sh [--quick] [avd-name] [extra emulator flags...]
#            start-emulator.sh                       # the default AVD (see below), cold boot
#            start-emulator.sh --quick               # boot from the Quick Boot snapshot instead
#            start-emulator.sh -wipe-data            # default AVD, factory reset
#            start-emulator.sh my_tv -gpu host       # another AVD, hardware rendering
#          EMULATOR_TOOLBAR=show start-emulator.sh   # keep a clickable side toolbar (WSL)
#          start-emulator.sh --help
# Which AVD: the name given, else $ADT_AVD, else tv_api25 if it exists, else the only
#          Android TV AVD. Anything else is an error that lists the AVDs.
# Log:     ${TMPDIR:-/tmp}/emulator-<avd-name>.log  (look here first if the window never appears)
# Stop:    adb -s <serial> emu kill   (saves a Quick Boot snapshot, which --quick boots from)
#
# Cold boot or Quick Boot: a cold boot (the default, -no-snapshot-load) starts Android from scratch
# (~10-30 s). --quick restores the snapshot saved when the emulator was last stopped (~6-16 s), but
# that snapshot also holds adbd's old connection, and sometimes adb then lists the emulator as
# "offline" for good. So with --quick, an emulator that stays offline for $ADT_OFFLINE_TIMEOUT
# seconds (default 30) gets `adb reconnect offline`, and if it's still offline that long after,
# the script gives up instead of waiting forever. A cold boot is offline for a while too, until
# adbd starts, which is normal, so it's never cut short.
#
# Common errors:
#   "No access to /dev/kvm"
#       The user isn't in the kvm group: sudo usermod -aG kvm $USER. When the message says the
#       device belongs to another group, the kvm group can't help; chgrp the device instead (the
#       message shows how). If /dev/kvm doesn't exist at all, hardware virtualization is off
#       (in the firmware, or nested virtualization for WSL).
#   "the emulator exited"
#       It failed to start or crashed; the log's last lines are printed. Hangs at "Emulator
#       starting..." without that error mean Android can't boot (try -wipe-data).
#   "adb can't reach it" (--quick only)
#       The restored snapshot left adb offline, even after a reconnect: stop the emulator and
#       start it without --quick.
#
# Everything below exists for a reason; see the comment on each step before removing one.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: start-emulator.sh [--quick] [avd-name] [emulator flags...]

Boots an Android TV emulator in the background and returns once Android is ready, so it can be
chained: start-emulator.sh && ./gradlew installDebug. Cold boot by default (~10-30 s).

  --quick         boot from the Quick Boot snapshot saved when the emulator was last stopped
                  (~6-16 s); if adb can't reach the restored emulator for 30 s, reconnect adb once,
                  then give up
  avd-name        the AVD to boot. Default: $ADT_AVD, else tv_api25 if it exists, else the
                  only Android TV AVD
  emulator flags  passed on to the emulator, e.g. -wipe-data, -no-window,
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

# --quick is this script's own flag, allowed anywhere; the emulator's flags have a single dash.
QUICK=""
ARGS=()
for arg in "$@"; do
    if [ "$arg" = --quick ]; then QUICK=1; else ARGS+=("$arg"); fi
done
set -- ${ARGS[@]+"${ARGS[@]}"}

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
# requires hardware acceleration"). Three things can be wrong, and they need different fixes, so
# they're told apart here rather than reported as one "no access":
#   1. the device is missing entirely -> virtualization is off;
#   2. it belongs to a group that isn't the kvm group -> joining kvm can never help (seen on WSL,
#      where /dev/kvm is created before udev applies 50-udev-default.rules);
#   3. the user is in the kvm group but this session predates it -> re-run through 'sg kvm', which
#      grants the group right away, instead of making the user restart the session.
# _IN_SG_KVM prevents an endless re-exec loop if the device is still not writable inside sg. The
# getent membership check matters: 'sg' asks for a group password when the user isn't a member.
if [ ! -e "$KVM_DEVICE" ]; then
    die "$KVM_DEVICE doesn't exist: enable hardware virtualization (VT-x/AMD-V in the firmware
settings; nested virtualization when running inside WSL or a VM)."
fi
if [ ! -w "$KVM_DEVICE" ]; then
    KVM_GROUP_LINE="$(getent group kvm || true)"
    DEVICE_GID="$(stat -c %g "$KVM_DEVICE")"
    KVM_GID="$(printf '%s' "$KVM_GROUP_LINE" | awk -F: '{print $3}')"
    if [ -n "$KVM_GID" ] && [ "$DEVICE_GID" != "$KVM_GID" ]; then
        die "No access to $KVM_DEVICE: it belongs to group $DEVICE_GID, but the kvm group is
$KVM_GID, so joining the kvm group can't grant access. Give the device to the kvm group:
    sudo chgrp kvm $KVM_DEVICE && sudo chmod 660 $KVM_DEVICE
and to keep that across restarts:
    echo 'z $KVM_DEVICE 0660 root kvm -' | sudo tee /etc/tmpfiles.d/kvm.conf"
    fi
    if printf '%s' "$KVM_GROUP_LINE" | grep -qw "$USER" && [ -z "${_IN_SG_KVM:-}" ]; then
        exec sg kvm -c "_IN_SG_KVM=1 $(printf '%q ' "$BIN_DIR/start-emulator.sh" "$AVD_NAME" \
            ${QUICK:+--quick} "$@")"
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
# -no-snapshot-load: cold boot, unless --quick (see the header). The emulator still saves a
#   snapshot when it's stopped, for a later --quick.
# nohup + & keeps the emulator running after this script (and the terminal) exits.
GPU=(-gpu swiftshader_indirect)
if in_list -gpu "$@"; then GPU=(); fi
BOOT=(-no-snapshot-load)
BOOT_KIND="cold boot"
if [ -n "$QUICK" ]; then BOOT=(); BOOT_KIND="Quick Boot"; fi
if in_list -no-snapshot-load "$@"; then BOOT=(); fi
LOG="${TMPDIR:-/tmp}/emulator-$AVD_NAME.log"
nohup "$EMULATOR" -avd "$AVD_NAME" \
    "${GPU[@]}" \
    ${BOOT[@]+"${BOOT[@]}"} \
    -no-boot-anim \
    -no-audio \
    "$@" > "$LOG" 2>&1 &
EMULATOR_PID=$!
echo "Emulator '$AVD_NAME' starting ($BOOT_KIND, log: $LOG)..."

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

# With --quick, an emulator adb keeps listing as offline gets one `adb reconnect offline` after
# OFFLINE_TIMEOUT seconds, and after as long again the script gives up (see the header).
# The reconnect only touches offline devices, which adb can't use anyway.
OFFLINE_TIMEOUT="${ADT_OFFLINE_TIMEOUT:-30}"
offline_since=""
reconnected=""
check_offline() {
    [ -n "$QUICK" ] || return 0
    if [ "$("$ADB" devices 2>/dev/null | awk -v s="$SERIAL" '$1 == s { print $2 }')" != offline ]; then
        offline_since=""
        return 0
    fi
    [ -n "$offline_since" ] || offline_since=$SECONDS
    [ $((SECONDS - offline_since)) -ge "$OFFLINE_TIMEOUT" ] || return 0
    if [ -z "$reconnected" ]; then
        echo "adb has seen $SERIAL as offline for ${OFFLINE_TIMEOUT} s; reconnecting adb..."
        "$ADB" reconnect offline > /dev/null 2>&1 || true
        reconnected=1
        offline_since=""
        return 0
    fi
    die "'$AVD_NAME' ($SERIAL) was restored from its Quick Boot snapshot, but adb can't reach it
(still offline after adb reconnect). Stop it and start it without --quick, for a cold boot:
  adb -s $SERIAL emu kill && $(command_for start-emulator.sh) $AVD_NAME"
}

# adbd answers long before the package manager is up, and installing at that point fails, so wait
# for sys.boot_completed. tr strips the '\r' that adb shell appends; </dev/null keeps adb shell
# from using up the stdin of whatever called this script.
until [ "$("$ADB" -s "$SERIAL" shell getprop sys.boot_completed < /dev/null 2>/dev/null \
        | tr -d '\r')" = "1" ]; do
    check_alive
    check_offline
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
