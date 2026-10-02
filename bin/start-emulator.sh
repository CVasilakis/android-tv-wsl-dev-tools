#!/usr/bin/env bash
# Boots the development emulator and returns once Android has finished booting, so it can be
# chained: start-emulator.sh && ./gradlew installDebug
#
# Usage:   start-emulator.sh [--quick] [--wait-for-home] [avd-name] [extra emulator flags...]
#            start-emulator.sh                       # the default AVD (see below), cold boot
#            start-emulator.sh --quick               # boot from the Quick Boot snapshot instead
#            start-emulator.sh --wait-for-home       # return once the home app has settled in front
#            start-emulator.sh -wipe-data            # default AVD, factory reset
#            start-emulator.sh my_tv -gpu host       # another AVD, hardware rendering
#          EMULATOR_TOOLBAR=show start-emulator.sh   # keep a clickable side toolbar (WSL)
#          start-emulator.sh --help
# Which AVD: the name given, else $ADT_AVD, else tv_api25 if it exists, else the only
#          Android TV AVD. Anything else is an error that lists the AVDs.
# Output:  the emulator's serial (e.g. emulator-5554), alone on stdout, so scripts can capture it:
#          serial="$(start-emulator.sh)". Every message goes to stderr.
# Log:     ${TMPDIR:-/tmp}/emulator-<avd-name>.log  (look here first if the window never appears)
# Stop:    stop-emulator.sh <avd-name|serial>   (returns once it has exited; the emulator saves
#          a Quick Boot snapshot, which --quick boots from)
#
# Cold boot or Quick Boot: a cold boot (the default, -no-snapshot-load) starts Android from scratch
# (slower on newer API levels). --quick restores the snapshot saved when the emulator was last
# stopped, which is faster, but that snapshot also holds adbd's old connection, and sometimes adb
# then lists the emulator as "offline" for good. So with --quick, an emulator that stays offline for $ADT_OFFLINE_TIMEOUT
# seconds (default 30) gets `adb reconnect offline`, and if it's still offline that long after,
# the script gives up instead of waiting forever. A cold boot is offline for a while too, until
# adbd starts, which is normal, so it's never cut short.
#
# Boot timeout: an emulator that keeps running without ever finishing its boot (a stuck boot, a
# broken image or data partition) would otherwise keep this script, and a CI job that calls it,
# waiting forever. After $ADT_BOOT_TIMEOUT seconds (default 900; 0: no limit) the script stops the
# emulator it started, which is of no use half-booted, and fails. Each adb call that checks the
# boot gets its own time limit, since adb can hang on such an emulator. Meanwhile the script says
# every minute that the emulator is still booting, so a slow boot (in a CI log, say) doesn't look
# like a hang.
#
# Already running: an AVD that's running isn't started again (its files are locked), but it may
# still be booting, e.g. started by another call or CI step a moment ago. So the script waits for
# its boot like for its own, with the same timeout, but never stops it: it's not this script's.
#
# Waiting for the home app (--wait-for-home, opt-in): sys.boot_completed=1 comes before the device
# has settled. From API 24 on, Settings' FallbackHome holds the screen until the user is unlocked,
# and HOME resolves to it; the home app comes to the front only then, sometimes after
# sys.boot_completed; and on a host short of CPU no window had the focus long after it, so no key
# reached any app (an instrumented test's first key waits for a focused window, and fails). With
# --wait-for-home the script then also waits until the home app's screen is in front, has finished
# starting and has the focus, at two looks in a row (wait_for_home in lib.sh says exactly what it
# checks; on Google TV that comes after a screen the launcher shows first, minutes on a slow host),
# for at most $ADT_HOME_TIMEOUT seconds (default 300; 0: no limit), counted from when Android has
# booted. When time's up it fails, naming what was in front, and stops the emulator if it started
# it, as after a boot timeout. When another app's screen keeps the focus (a new AVD's first boot
# shows "USB drive connected" on API 23 and 29), it presses Back, twice at most, never on a screen
# of the home app's own, and only on an emulator whose boot it waited for: one that had booted
# before may be in use, so there it only looks, and fails if an app stays in front. With --quick
# the restored device has usually settled already.
#
# Home on Android TV 8.0 and 8.1: on the API 26 and 27 Android TV images, the Home key never
# leaves an app until tv_user_setup_complete is set, so after the boot the script sets it, and
# then waits until Android has saved it to disk, usually under a second (see the code below). Any
# other image is left as it is.
#
# No display: the emulator's window needs an X display (it ships only Qt's X11 plugin). Without
# $DISPLAY (a CI runner, an SSH session) it aborts, and its log doesn't say why, so the script
# adds -no-window.
#
# Common errors:
#   "No access to /dev/kvm"
#       The user isn't in the kvm group: sudo usermod -aG kvm $USER. When the message says the
#       device belongs to another group, the kvm group can't help; chgrp the device instead (the
#       message shows how). If /dev/kvm doesn't exist at all, hardware virtualization is off
#       (in the firmware, or nested virtualization for WSL).
#   "the emulator exited"
#       It failed to start or crashed; the log's last lines are printed.
#   "didn't finish booting within ... s"
#       Android didn't boot in time, and the emulator was stopped. On a slow host, raise
#       ADT_BOOT_TIMEOUT; if it never boots, try -wipe-data (factory reset).
#   "adb can't reach it" (--quick only)
#       The restored snapshot left adb offline, even after a reconnect: stop the emulator and
#       start it without --quick.
#   "its home app wasn't in front ... within ... s" (--wait-for-home only)
#       What was in front instead is named: something kept the focus, or nothing had it. On a
#       slow host, raise ADT_HOME_TIMEOUT.
#
# Everything below exists for a reason; see the comment on each step before removing one.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: start-emulator.sh [--quick] [--wait-for-home] [avd-name] [emulator flags...]

Boots an Android TV emulator in the background and returns once Android is ready, so it can be
chained: start-emulator.sh && ./gradlew installDebug. Cold boot by default.

Prints the emulator's serial (e.g. emulator-5554) on stdout and every message on stderr, so
serial="$(start-emulator.sh)" captures it. Without $DISPLAY it adds -no-window.

  --quick         boot from the Quick Boot snapshot saved when the emulator was last stopped
                  (faster); if adb can't reach the restored emulator for 30 s, reconnect adb once,
                  then give up
  --wait-for-home once Android has booted, also wait until the device has settled: its home
                  app's screen in front, done starting, holding the focus, at two looks in a
                  row; if another app's screen keeps the focus, press Back (twice at most),
                  except on an emulator that had booted before this call. If that takes over
                  $ADT_HOME_TIMEOUT s, fail, and stop the emulator if this script started it
  avd-name        the AVD to boot. Default: $ADT_AVD, else tv_api25 if it exists, else the
                  only Android TV AVD
  emulator flags  passed on to the emulator, e.g. -wipe-data, -no-window,
                  -gpu host (replaces the default -gpu swiftshader_indirect)
  -h, --help      show this help

Environment:
  ADT_AVD           default AVD name
  ADT_BOOT_TIMEOUT  seconds to wait for Android to boot before stopping the emulator and
                    failing (default: 900; 0: no limit)
  ADT_HOME_TIMEOUT  with --wait-for-home: seconds to wait for the home app after the boot
                    (default: 300; 0: no limit)
  EMULATOR_TOOLBAR  WSL only: hide (default) or show the emulator's side toolbar
                    (see bin/README.md)

If the AVD is already running, it isn't started again: the script waits until it has booted,
then prints its serial. On the Android TV images of API 26 and 27, it then marks the TV's
setup as complete (tv_user_setup_complete), without which the Home key doesn't leave apps, and
waits until Android has saved it (once per AVD). The emulator keeps running after this script
exits; stop it with: stop-emulator.sh <avd-name|serial>
EOF
}
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

# shellcheck source=../lib/lib.sh
source "$(dirname "$(readlink -f "$0")")/../lib/lib.sh"
require "$EMULATOR" "emulator"
require "$ADB" "platform-tools"

BOOT_TIMEOUT="${ADT_BOOT_TIMEOUT:-900}"   # seconds, 0 = no limit (see the header)
if ! [[ "$BOOT_TIMEOUT" =~ ^[0-9]+$ ]]; then
    die "ADT_BOOT_TIMEOUT must be a number of seconds (0: no limit), not '$BOOT_TIMEOUT'."
fi

# --quick and --wait-for-home are this script's own flags, allowed anywhere; the emulator's flags
# have a single dash.
QUICK=""
WAIT_FOR_HOME=""
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --quick) QUICK=1 ;;
        --wait-for-home) WAIT_FOR_HOME=1 ;;
        *) ARGS+=("$arg") ;;
    esac
done
set -- ${ARGS[@]+"${ARGS[@]}"}

HOME_TIMEOUT="${ADT_HOME_TIMEOUT:-300}"   # seconds, 0 = no limit (see the header)
if [ -n "$WAIT_FOR_HOME" ] && ! [[ "$HOME_TIMEOUT" =~ ^[0-9]+$ ]]; then
    die "ADT_HOME_TIMEOUT must be a number of seconds (0: no limit), not '$HOME_TIMEOUT'."
fi

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
# membership check matters: 'sg' asks for a group password when the user isn't a member. It asks
# the user database (id -nG <user>), not this session, so a group joined since login counts, and
# so does kvm as the user's primary group; $USER isn't used, as containers often don't set it.
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
    ME="$(id -un)"
    if [[ " $(id -nG "$ME" 2>/dev/null || true) " == *" kvm "* ]] && [ -z "${_IN_SG_KVM:-}" ]; then
        exec sg kvm -c "_IN_SG_KVM=1 $(printf '%q ' "$BIN_DIR/start-emulator.sh" "$AVD_NAME" \
            ${QUICK:+--quick} ${WAIT_FOR_HOME:+--wait-for-home} "$@")"
    fi
    die "No access to $KVM_DEVICE. Run: sudo usermod -aG kvm $ME"
fi

# The serial of the running emulator of this AVD, if any. Emulators are matched by AVD name, never
# by "the only device": other emulators and phones may be connected. The emulator picks the first
# free port, so each running emulator is asked for its AVD name.
avd_serial() {
    local serial
    for serial in $(running_emulators); do
        if [ "$(emulator_avd "$serial")" = "$AVD_NAME" ]; then echo "$serial"; return 0; fi
    done
    return 1
}

# Starting an AVD twice fails (its files are locked), so an already running one is only waited
# for (see the header). STARTED_HERE tells the two apart below: only an emulator started here is
# watched through its process, stopped on a timeout, and gets the WSLg toolbar fix.
SERIAL="$(avd_serial || true)"
STARTED_HERE=""
LOG="${TMPDIR:-/tmp}/emulator-$AVD_NAME.log"
if [ -n "$SERIAL" ]; then
    echo "Emulator '$AVD_NAME' is already running as $SERIAL." >&2
fi

# No X display, no window (see the header).
if [ -z "$SERIAL" ] && [ -z "${DISPLAY:-}" ] && ! in_list -no-window "$@"; then
    echo "No \$DISPLAY, so the emulator runs without a window (-no-window)." >&2
    set -- "$@" -no-window
fi

# -gpu swiftshader_indirect: software rendering, works on any host including WSLg (see
#   create-avd.sh). Left out when the caller passes their own -gpu.
# -no-audio: sound is rarely needed; one less host integration (PulseAudio) to go wrong.
# -no-boot-anim: faster cold boots.
# -no-snapshot-load: cold boot, unless --quick (see the header). The emulator still saves a
#   snapshot when it's stopped, for a later --quick.
# nohup + & keeps the emulator running after this script (and the terminal) exits.
if [ -z "$SERIAL" ]; then
    GPU=(-gpu swiftshader_indirect)
    if in_list -gpu "$@"; then GPU=(); fi
    BOOT=(-no-snapshot-load)
    BOOT_KIND="cold boot"
    if [ -n "$QUICK" ]; then BOOT=(); BOOT_KIND="Quick Boot"; fi
    if in_list -no-snapshot-load "$@"; then BOOT=(); fi
    nohup "$EMULATOR" -avd "$AVD_NAME" \
        "${GPU[@]}" \
        ${BOOT[@]+"${BOOT[@]}"} \
        -no-boot-anim \
        -no-audio \
        "$@" > "$LOG" 2>&1 &
    EMULATOR_PID=$!
    STARTED_HERE=1
    echo "Emulator '$AVD_NAME' starting ($BOOT_KIND, log: $LOG)..." >&2
fi
BOOT_STARTED=$SECONDS

# Without this, a crashed or stopped emulator would leave the loops below waiting forever. One
# started elsewhere has no process here to watch, so adb has to still list it.
check_alive() {
    if [ -n "$STARTED_HERE" ]; then
        kill -0 "$EMULATOR_PID" 2>/dev/null || die "the emulator exited. Last lines of $LOG:
$(tail -n 15 "$LOG")"
    # grep without -q reads all its input: exiting early could fail the pipeline (pipefail).
    elif ! running_emulators | grep -xF -- "$SERIAL" > /dev/null; then
        die "'$AVD_NAME' ($SERIAL) was stopped before it finished booting."
    fi
}

# Stops the emulator started above and waits until it's gone, so that a retry doesn't find it
# still running and report it as ready. SIGTERM lets it shut down cleanly; SIGKILL only if it
# doesn't within 30 s, which the emulator tier copies (tests/emulator/test_on_emulator.py).
stop_emulator() {
    local waited=0
    kill "$EMULATOR_PID" 2>/dev/null || return 0
    while kill -0 "$EMULATOR_PID" 2>/dev/null && [ "$waited" -lt 30 ]; do
        sleep 1
        waited=$((waited + 1))
    done
    kill -9 "$EMULATOR_PID" 2>/dev/null || true
}

# An emulator that runs but never boots would otherwise keep the loops below waiting forever
# (see the header). The log is read before stopping it, whose shutdown lines would hide the cause.
check_timeout() {
    local last_lines
    [ "$BOOT_TIMEOUT" -gt 0 ] && time_is_up "$BOOT_STARTED" "$BOOT_TIMEOUT" || return 0
    if [ -z "$STARTED_HERE" ]; then
        die "'$AVD_NAME' ($SERIAL) didn't finish booting within $BOOT_TIMEOUT s.
It wasn't started by this script, so it's left running. Stop it with:
  $(command_for stop-emulator.sh) $SERIAL
On a slow host, set ADT_BOOT_TIMEOUT to wait longer (0: no limit)."
    fi
    last_lines="$(tail -n 15 "$LOG")"
    stop_emulator
    die "'$AVD_NAME' didn't finish booting within $BOOT_TIMEOUT s, so it was stopped. Last lines
of $LOG:
$last_lines
On a slow host, set ADT_BOOT_TIMEOUT to wait longer (0: no limit). If it never boots, try a
factory reset: $(command_for start-emulator.sh) $AVD_NAME -wipe-data"
}

# A boot can take minutes with nothing to show, which in a CI log looks like a hang (see the
# header). ADT_PROGRESS_INTERVAL exists for the tests.
PROGRESS_INTERVAL="${ADT_PROGRESS_INTERVAL:-60}"
last_progress=$BOOT_STARTED
report_progress() {
    local limit=""
    time_is_up "$last_progress" "$PROGRESS_INTERVAL" || return 0
    if [ "$BOOT_TIMEOUT" -gt 0 ]; then limit="; the limit is $BOOT_TIMEOUT s"; fi
    echo "'$AVD_NAME' is still booting ($((SECONDS - BOOT_STARTED)) s so far$limit)..." >&2
    last_progress=$SECONDS
}

# Find the serial of the emulator started above (emulator-<port>), once it shows up in adb.
while [ -z "$SERIAL" ]; do
    check_alive
    check_timeout
    report_progress
    SERIAL="$(avd_serial || true)"
    if [ -z "$SERIAL" ]; then sleep 1; fi
done

# With --quick, an emulator adb keeps listing as offline gets one `adb reconnect offline` after
# OFFLINE_TIMEOUT seconds, and after as long again the script gives up (see the header).
# The reconnect only touches offline devices, which adb can't use anyway.
OFFLINE_TIMEOUT="${ADT_OFFLINE_TIMEOUT:-30}"
offline_since=""
reconnected=""
check_offline() {
    [ -n "$QUICK" ] && [ -n "$STARTED_HERE" ] || return 0
    if [ "$(adb_bounded devices 2>/dev/null | awk -v s="$SERIAL" '$1 == s { print $2 }')" != offline ]; then
        offline_since=""
        return 0
    fi
    [ -n "$offline_since" ] || offline_since=$SECONDS
    time_is_up "$offline_since" "$OFFLINE_TIMEOUT" || return 0
    if [ -z "$reconnected" ]; then
        echo "adb has seen $SERIAL as offline for ${OFFLINE_TIMEOUT} s; reconnecting adb..." >&2
        adb_bounded reconnect offline > /dev/null 2>&1 || true
        reconnected=1
        offline_since=""
        return 0
    fi
    die "'$AVD_NAME' ($SERIAL) was restored from its Quick Boot snapshot, but adb can't reach it
(still offline after adb reconnect). Stop it and start it without --quick, for a cold boot:
  $(command_for stop-emulator.sh) $SERIAL && $(command_for start-emulator.sh) $AVD_NAME"
}

# adbd answers long before the package manager is up, and installing at that point fails, so wait
# for sys.boot_completed. tr strips the '\r' that adb shell appends; </dev/null keeps adb shell
# from using up the stdin of whatever called this script. adb_bounded (lib.sh): a hung call
# counts as "not booted yet", so the boot timeout still applies.
until [ "$(adb_bounded -s "$SERIAL" shell getprop sys.boot_completed < /dev/null 2>/dev/null \
        | tr -d '\r')" = "1" ]; do
    check_alive
    check_timeout
    check_offline
    report_progress
    sleep 2
    WAITED=1
done
if [ -n "$STARTED_HERE" ] || [ -n "${WAITED:-}" ]; then
    echo "Emulator '$AVD_NAME' booted as $SERIAL." >&2
fi
# Android TV 8.0 and 8.1 (API 26, 27) ignore the Home key until the TV setup wizard has set
# tv_user_setup_complete ("Not starting activity because user setup is in progress" in logcat),
# and their emulator images never run that wizard: Home would never leave an app. A real TV has
# it set. The setting stays in the AVD's data, so it's set once per AVD; failing to set it
# doesn't make the boot fail.
# `adb emu kill` right after this script would lose it (a later boot through this script sets it
# again, but one started another way wouldn't). Android writes a changed setting to
# settings_secure.xml about 0.2 s later, keeping the old file as settings_secure.xml.bak until the
# new one is complete, and a boot that finds a .bak reads it instead. Even once Android has
# deleted it, the deletion is lost until the filesystem's journal records it, up to 5 s later. So
# the script waits until the file holds the setting and the .bak is gone, read as root (these
# images are debug builds, with su), then commits the journal (sync). It waits for at most
# ADT_SAVE_TIMEOUT seconds, then only warns, as when it can't look. The emulator tier copies the
# default (tests/emulator/test_on_emulator.py).
SAVE_TIMEOUT="${ADT_SAVE_TIMEOUT:-30}"
if ! [[ "$SAVE_TIMEOUT" =~ ^[0-9]+$ ]]; then
    die "ADT_SAVE_TIMEOUT must be a number of seconds, not '$SAVE_TIMEOUT'."
fi
SAVED_CHECK="su 0 sh -c 'cd /data/system/users/0 && grep -q \"name=.tv_user_setup_complete. value=.1.\" \
settings_secure.xml && ! [ -e settings_secure.xml.bak ] && sync && echo saved'"

# wait_until_saved: until Android has saved tv_user_setup_complete=1 (see above). Prints why and
# fails when time's up, or when the check prints anything else than "saved" (e.g. no su). An adb
# call that runs out of time (adb_bounded) counts as "not saved yet".
wait_until_saved() {
    local answer started=$SECONDS
    while true; do
        answer="$(adb_bounded -s "$SERIAL" shell "$SAVED_CHECK" < /dev/null 2>&1 | tr -d '\r')" || true
        case "$answer" in
            saved) return 0 ;;
            "") ;;
            *) echo "$answer"; return 1 ;;
        esac
        if time_is_up "$started" "$SAVE_TIMEOUT"; then
            echo "it wasn't saved within $SAVE_TIMEOUT s"
            return 1
        fi
        sleep 0.2
    done
}

AVD_IMAGE=""
if dir="$(avd_dir "$AVD_NAME")"; then AVD_IMAGE="$(avd_image "$dir")"; fi
case "$AVD_IMAGE" in
    *android-26/android-tv/*|*android-27/android-tv/*)
        if [ "$(adb_bounded -s "$SERIAL" shell settings get secure tv_user_setup_complete \
                < /dev/null 2>/dev/null | tr -d '\r')" != 1 ]; then
            if adb_bounded -s "$SERIAL" shell settings put secure tv_user_setup_complete 1 \
                    < /dev/null > /dev/null 2>&1; then
                echo "Marked Android TV's setup as complete (tv_user_setup_complete), so the Home key works." >&2
                echo "Waiting until Android has saved it, so that stopping the emulator doesn't lose it (once per AVD)..." >&2
                if ! problem="$(wait_until_saved)"; then
                    echo "Warning: couldn't see Android save tv_user_setup_complete ($problem). Stopped" >&2
                    echo "within seconds, the emulator may lose it; a later boot through this script sets it again." >&2
                fi
            else
                echo "Warning: couldn't set tv_user_setup_complete, so the Home key won't leave apps. Try:" >&2
                echo "  adb -s $SERIAL shell settings put secure tv_user_setup_complete 1" >&2
            fi
        fi
        ;;
esac

# The boot comes before the device has settled; with --wait-for-home, wait for that too (see the
# header). Back, for a screen that keeps the focus, only on an emulator whose boot the script saw:
# one that had booted before may be in use. A failure is handled like a boot timeout: an emulator
# started here is of no use unsettled.
if [ -n "$WAIT_FOR_HOME" ]; then
    limit=""
    if [ "$HOME_TIMEOUT" -gt 0 ]; then limit=" (up to $HOME_TIMEOUT s)"; fi
    echo "Waiting for the home app to be in front, with the focus$limit..." >&2
    BACKS=0
    if [ -n "$STARTED_HERE" ] || [ -n "${WAITED:-}" ]; then BACKS=2; fi
    if FRONT="$(wait_for_home "$SERIAL" "$HOME_TIMEOUT" "$BACKS")"; then
        echo "The home app ($FRONT) is in front." >&2
    elif [ -z "$STARTED_HERE" ]; then
        die "'$AVD_NAME' ($SERIAL) booted, but its home app wasn't in front with the focus within $HOME_TIMEOUT s.
In front: $FRONT.
It wasn't started by this script, so it's left running. On a slow host, set ADT_HOME_TIMEOUT to
wait longer (0: no limit)."
    else
        stop_emulator
        die "'$AVD_NAME' booted, but its home app wasn't in front with the focus within $HOME_TIMEOUT s,
so it was stopped. In front: $FRONT.
On a slow host, set ADT_HOME_TIMEOUT to wait longer (0: no limit)."
    fi
fi

# With several devices connected, adb refuses to guess and `./gradlew installDebug` installs on
# all of them. Both honor ANDROID_SERIAL.
if [ -n "$STARTED_HERE" ] && [ "$(adb_bounded devices | awk 'NR > 1 && NF' | wc -l)" -gt 1 ]; then
    echo "Other devices are connected too. To make adb and Gradle use only this one:" >&2
    echo "  export ANDROID_SERIAL=$SERIAL" >&2
fi

# Under WSLg the side toolbar can't be clicked, and while it's shown it takes keyboard focus away
# from the emulator screen (typed keys never reach Android). Hiding it is the default because
# keyboard control matters more for a TV app. See wslg-toolbar.py for details. Skipped outside
# WSL (not needed) and with -no-window (there's no toolbar). '|| true': a failure here must not
# report the already-booted emulator as failed.
if [ -n "$STARTED_HERE" ] && is_wsl && ! in_list -no-window "$@"; then
    python3 "$BIN_DIR/wslg-toolbar.py" "$AVD_NAME" "${EMULATOR_TOOLBAR:-hide}" >&2 || true
fi

# The only line on stdout (see the header).
echo "$SERIAL"
