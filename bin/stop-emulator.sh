#!/usr/bin/env bash
# Stops a running emulator and returns once its process has exited, so the same AVD can be started
# again right away, and a script or agent never waits on the emulator without a time limit.
#
# Usage:   stop-emulator.sh [--all] [avd-name|serial]
#            stop-emulator.sh                    # $ANDROID_SERIAL, else the only running emulator
#            stop-emulator.sh tv_api25           # by AVD name
#            stop-emulator.sh emulator-5556      # by serial
#            stop-emulator.sh --all              # every running emulator
#          stop-emulator.sh --help
# Exit:    0 once the emulator has exited (or, when its process can't be found, once adb no
#          longer lists it, which it then says), and also when it wasn't running, so it's safe to
#          call just in case; 1 if it couldn't be stopped; 2 for wrong arguments. Physical devices
#          are never touched. Messages go to stderr; stdout stays empty.
#
# Why not only `adb emu kill`: it returns at once, and the emulator exits afterwards (after saving
# its Quick Boot snapshot). Waiting for that with `adb wait-for-disconnect` has no time limit, and
# neither command can stop an emulator whose console no longer answers: a script then hangs. This
# script waits for the emulator's own process, with a time limit, and kills it if it has to.
#
# Which process: the emulator's console names the file it advertises itself in,
# pid_<PID>.ini (`adb emu avd discoverypath`), so that instance's PID. Without an answer (a hung
# console, or one that doesn't know the command), the script reads those files itself
# (DISCOVERY_DIR): each holds its emulator's console port, the number in its serial, and its AVD's
# name, so the serial's file names its PID and AVD. Else the PID comes from hardware-qemu.ini.lock
# in the AVD's folder (the folder `adb emu avd path` names), which the emulator writes when it
# starts and deletes when it exits. A -read-only emulator writes none, and several of them can run
# for one AVD (a normal instance can't run next to them). Named by its AVD alone, such an emulator
# is found through the discovery files of that AVD; when they name several instances, the script
# stops none and asks for a serial. It waits for a PID only after checking that the process's
# command line holds the AVD's name, so a stale lock or discovery file (a killed emulator leaves
# both behind), whose PID a new process may have taken, can't make it kill the wrong process.
# Searching processes by name (pgrep -f, pkill -f) could match others, including the shell that runs
# the search, so it isn't used. When no PID is found, the script can only wait until adb no longer
# lists the emulator, which can be before its process has exited, and says so. That's also how it
# recognizes an emulator that was exiting already (e.g. sent SIGTERM with a test run that was
# killed): its console answers nothing, and adb lists it for a moment after its exit, the last
# moment as offline. A zombie (an exited process its parent hasn't reaped yet) has exited.
#
# Time limit: the emulator gets $ADT_STOP_TIMEOUT seconds (default 60) to exit after
# `adb emu kill`, and is then killed (SIGKILL), which loses its Quick Boot snapshot. When its
# console doesn't answer `adb emu kill` at all (a hung emulator), or adb doesn't list it (e.g. it's
# stuck early in its boot), it's sent SIGTERM instead, which lets it shut down, and gets as long
# again before SIGKILL; without its PID, it gets as long to leave adb's list by itself. The script
# says which of these it did. A hung console doesn't tell the AVD's name either: named by its
# serial, such an emulator's process is found only through its discovery file, so without one,
# name its AVD.
# Each limit lasts at least its number of seconds, and at most one more (time_is_up in lib.sh).
#
# Stopping doesn't save recent changes: `adb emu kill` doesn't shut Android down, so a setting or
# an app's enabled state changed just before is lost (bin/README.md, "Stopping doesn't save
# recent changes"). This script doesn't change that.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: stop-emulator.sh [--all] [avd-name|serial]

Stops a running emulator and returns once its process has exited, so the AVD can be started
again right away. If it doesn't exit in time, it's killed. Never touches physical devices.

  avd-name|serial  the emulator to stop, e.g. tv_api25 or emulator-5554. Default:
                   $ANDROID_SERIAL, else the only running emulator
  --all            stop every running emulator
  -h, --help       show this help

Environment:
  ADT_STOP_TIMEOUT  seconds to wait for the emulator to exit after `adb emu kill` before
                    killing it, which loses its Quick Boot snapshot (default: 60)

Exits 0 once the emulator has exited, and also if it wasn't running; 1 if it couldn't be
stopped.
EOF
}
ALL=""
TARGET=""
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --all)     ALL=1; shift ;;
        -*)        usage >&2; exit 2 ;;
        *)         [ -z "$TARGET" ] || { usage >&2; exit 2; }
                   TARGET="$1"; shift ;;
    esac
done
if [ -n "$ALL" ] && [ -n "$TARGET" ]; then usage >&2; exit 2; fi

# shellcheck source=../lib/lib.sh
source "$(dirname "$(readlink -f "$0")")/../lib/lib.sh"
require "$ADB" "platform-tools"

STOP_TIMEOUT="${ADT_STOP_TIMEOUT:-60}"    # seconds (see the header)
if ! [[ "$STOP_TIMEOUT" =~ ^[0-9]+$ ]]; then
    die "ADT_STOP_TIMEOUT must be a number of seconds, not '$STOP_TIMEOUT'."
fi
KILL_WAIT=30                              # seconds for a killed emulator to exit, and for adb
                                          # to notice (tests/emulator/test_on_emulator.py
                                          # copies it)

# is_avd_process <pid> <avd-name>: whether that process runs and is the emulator of that AVD,
# started as `-avd <name>` or `@<name>` (the emulator passes its arguments on to qemu). A zombie,
# or a process that has exited, has an empty command line. 2>/dev/null comes before the <, which
# fails when no process has that PID: after it, bash would already have printed that failure.
is_avd_process() {
    local args=() i
    mapfile -d '' -t args 2>/dev/null < "/proc/$1/cmdline" || return 1
    for ((i = 0; i < ${#args[@]}; i++)); do
        if [ "${args[i]}" = "@$2" ]; then return 0; fi
        if [ "${args[i]}" = -avd ] && [ "${args[i + 1]:-}" = "$2" ]; then return 0; fi
    done
    return 1
}

# The folder where each running emulator advertises itself in a file pid_<PID>.ini, which holds
# its console port (port.serial=, the serial's number), its AVD's name (avd.name=) and its command
# line. The emulator picks it the same way.
DISCOVERY_DIR="${XDG_RUNTIME_DIR:-/run/user/$UID}/avd/running"

# discovered <key> <value>: "<PID> <avd-name> <serial>" for each discovery file holding
# <key>=<value> whose process is that AVD's emulator (a killed emulator leaves its file behind).
discovered() {
    local file pid name port
    for file in "$DISCOVERY_DIR"/pid_*.ini; do
        pid="${file##*/pid_}"
        pid="${pid%.ini}"
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        grep -qxF -- "$1=$2" "$file" 2>/dev/null || continue
        name="$(sed -n 's/^avd\.name=//p' "$file" 2>/dev/null | head -n 1)" || continue
        port="$(sed -n 's/^port\.serial=//p' "$file" 2>/dev/null | head -n 1)" || continue
        if [ -n "$name" ] && is_avd_process "$pid" "$name"; then
            echo "$pid $name emulator-$port"
        fi
    done
}

# emulator_pid <serial> <avd-folder> <avd-name>: "<PID> <avd-name>" of that emulator (see the
# header): as its console names it, else from the discovery file of its serial, else from the
# AVD's lock file, else, without a serial, from the discovery files of the AVD's name, which can
# name several instances (one line each). A line from a discovery file has a third field, the
# serial. Any of the three arguments may be empty. Fails if none is found.
emulator_pid() {
    local pid="" found=""
    if [ -n "$1" ] && [ -n "$3" ]; then
        pid="$(adb_bounded -s "$1" emu avd discoverypath 2>/dev/null | tr -d '\r' \
            | sed -n 's|.*/pid_\([0-9][0-9]*\)\.ini$|\1|p' | head -n 1)" || true
        if [ -n "$pid" ] && is_avd_process "$pid" "$3"; then echo "$pid $3"; return 0; fi
    fi
    if [ -n "$1" ]; then found="$(discovered port.serial "${1#emulator-}")"; fi
    if [ -z "$found" ] && [ -n "$3" ]; then
        pid="$(tr -dc '0-9' 2>/dev/null < "$2/hardware-qemu.ini.lock")" || true
        if [ -n "$pid" ] && is_avd_process "$pid" "$3"; then echo "$pid $3"; return 0; fi
        if [ -z "$1" ]; then found="$(discovered avd.name "$3")"; fi
    fi
    [ -n "$found" ] && echo "$found"
}

# Whether a process still runs, from its state: its command line is empty for a process that's
# exiting as well as for one that has exited. A zombie has exited; only its entry is left until
# its parent reaps it.
is_running() {
    local stat
    { read -r stat < "/proc/$1/stat"; } 2>/dev/null || return 1
    stat="${stat##*) }"
    [ "${stat%% *}" != Z ]
}

is_listed() { running_emulators | grep -xF -- "$1" > /dev/null; }

# The AVD name of a running emulator, empty when its console doesn't answer. Never fails, so a
# hung console can't end the script through set -e.
avd_of() { emulator_avd "$1" || true; }

# wait_until_gone <seconds> <pid> <serial>: until that process has exited (when a PID is given),
# else until adb no longer lists the serial. Fails when the time is up (time_is_up, lib.sh).
wait_until_gone() {
    local started=$SECONDS
    while if [ -n "$2" ]; then is_running "$2"; else is_listed "$3"; fi; do
        ! time_is_up "$started" "$1" || return 1
        sleep 0.2
    done
}

# stop <serial> <avd-name>: stops one emulator; either may be empty, not both. Prints what
# happened; fails if it's still running.
stop() {
    local serial="$1" name="$2" dir="" pid="" label problem="" how="" instances=() line serials=""
    if [ -n "$serial" ]; then
        dir="$(adb_bounded -s "$serial" emu avd path 2>/dev/null | head -n 1 | tr -d '\r' || true)"
    fi
    if [ ! -d "$dir" ] && [ -n "$name" ]; then dir="$(avd_dir "$name" || true)"; fi
    mapfile -t instances < <(emulator_pid "$serial" "$dir" "$name" || true)
    if [ ${#instances[@]} -gt 1 ]; then
        # Several -read-only instances of the AVD, none of whose consoles said its name.
        for line in "${instances[@]}"; do serials+="${serials:+, }${line##* }"; done
        echo "'$name' runs as several emulators whose consoles don't answer ($serials):" \
             "name the one to stop by its serial." >&2
        return 1
    fi
    # A discovery file also names the AVD of an emulator whose console doesn't.
    if [ ${#instances[@]} -eq 1 ]; then read -r pid name line <<< "${instances[0]}"; fi
    label="'${name:-$serial}'${name:+${serial:+ ($serial)}}"
    if [ -z "$serial" ] && [ -z "$pid" ]; then
        echo "$label isn't running." >&2
        return 0
    fi

    if [ -n "$serial" ] && adb_bounded -s "$serial" emu kill > /dev/null 2>&1; then
        if ! wait_until_gone "$STOP_TIMEOUT" "$pid" "$serial"; then
            problem="didn't exit within $STOP_TIMEOUT s of adb emu kill"
        fi
    elif ! wait_until_gone 0 "$pid" "$serial"; then
        # A hung console, or an emulator adb doesn't list: SIGTERM still lets it shut down. Found
        # by AVD name without a serial, it's either not listed or its console doesn't say its name.
        if [ -n "$serial" ]; then problem="didn't answer adb emu kill"
        else problem="isn't listed by adb, or its console doesn't answer"; fi
        if [ -n "$pid" ]; then
            kill -TERM "$pid" 2>/dev/null || true
            if wait_until_gone "$STOP_TIMEOUT" "$pid" ""; then how="stopped with SIGTERM"; fi
        elif wait_until_gone "$STOP_TIMEOUT" "" "$serial"; then
            # An emulator that's exiting (e.g. sent SIGTERM with a test run that was killed)
            # answers no console command, and adb lists it until a moment after its exit, the
            # last moment as offline. Without its PID, adb's list is all there is to wait on.
            echo "$label $problem, and adb no longer lists it: it was exiting already. Its" \
                 "process wasn't found, so it may not have exited yet." >&2
            return 0
        fi
    fi
    if [ -n "$problem" ] && [ -z "$how" ]; then
        if [ -z "$pid" ]; then
            # No discovery file names it (e.g. it's in another folder than DISCOVERY_DIR), and
            # without the AVD's name its lock file can't be found; a -read-only emulator has none.
            echo "$label $problem, and its process wasn't found to kill it. It's still running." >&2
            if [ -z "$name" ]; then
                echo "Name its AVD instead, so its process can be found:" \
                     "$(command_for stop-emulator.sh) <avd-name>" >&2
            fi
            return 1
        fi
        kill -KILL "$pid" 2>/dev/null || true
        if ! wait_until_gone "$KILL_WAIT" "$pid" ""; then
            echo "$label $problem, and couldn't be killed (PID $pid). It's still running." >&2
            return 1
        fi
        how="killed (SIGKILL); its Quick Boot snapshot wasn't saved"
    fi
    # adb notices a moment after the emulator's exit that it's gone, and lists it as offline until
    # then. Waiting for that too means a following `adb devices` no longer shows it.
    if [ -n "$serial" ]; then wait_until_gone "$KILL_WAIT" "" "$serial" || true; fi
    if [ -n "$problem" ]; then echo "$label $problem, so it was $how." >&2
    elif [ -z "$pid" ]; then
        echo "adb no longer lists $label, but its process wasn't found, so it may still be" \
             "exiting." >&2
    else echo "Stopped $label." >&2; fi
}

# What to stop: "<serial> <avd-name>" entries. The serial is "-" for an AVD adb doesn't list (by
# name); the name is empty when the emulator's console didn't say it.
mapfile -t SERIALS < <(running_emulators)
TARGETS=()
describe() {                              # "emulator-5554 (tv_api25), emulator-5556 (…)"
    local serial sep=""
    for serial in "${SERIALS[@]}"; do
        printf '%s%s (%s)' "$sep" "$serial" "$(avd_of "$serial")"
        sep=", "
    done
}
if [ -n "$ALL" ]; then
    for serial in "${SERIALS[@]}"; do TARGETS+=("$serial $(avd_of "$serial")"); done
else
    TARGET="${TARGET:-${ANDROID_SERIAL:-}}"
    if [ -z "$TARGET" ]; then
        case ${#SERIALS[@]} in
            0) ;;
            1) TARGETS+=("${SERIALS[0]} $(avd_of "${SERIALS[0]}")") ;;
            *) die "several emulators are running: $(describe).
Pass one: $(command_for stop-emulator.sh) <avd-name|serial>, or stop them all with --all." ;;
        esac
    elif [[ "$TARGET" =~ ^emulator-[0-9]+$ ]]; then
        TARGETS+=("$TARGET $(if is_listed "$TARGET"; then avd_of "$TARGET"; fi)")
    elif adb_bounded devices 2>/dev/null | awk 'NR > 1 { print $1 }' \
            | grep -xF -- "$TARGET" > /dev/null; then
        die "'$TARGET' is a device, not an emulator; this script only stops emulators."
    else
        found=""
        for serial in "${SERIALS[@]}"; do
            if [ "$(avd_of "$serial")" = "$TARGET" ]; then found="$serial"; break; fi
        done
        if [ -z "$found" ] && ! avd_dir "$TARGET" > /dev/null; then
            die "there's no AVD named '$TARGET', and no running emulator of that name."
        fi
        TARGETS+=("${found:--} $TARGET")
    fi
fi
if [ ${#TARGETS[@]} -eq 0 ]; then
    echo "No emulator is running." >&2
    exit 0
fi

STATUS=0
for target in "${TARGETS[@]}"; do
    read -r serial name <<< "$target"
    if [ "$serial" = - ]; then serial=""; fi
    if [ -n "$serial" ] && [ -z "${name:-}" ] && ! is_listed "$serial"; then
        echo "'$serial' isn't running." >&2
        continue
    fi
    stop "$serial" "${name:-}" || STATUS=1
done
exit "$STATUS"
