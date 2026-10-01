# shellcheck shell=bash
# shellcheck disable=SC2034  # the variables set here are used by the scripts that source this file
# Shared helpers for the scripts in bin/. Sourced, not run.
#
# Finds the Android SDK, its tools and AVDs wherever the user keeps them, so the scripts work with
# any setup, not only the one in SETUP.md. After sourcing: $TOOLS_DIR (this repository), $BIN_DIR,
# $SDK, $ADB, $EMULATOR, $AVDMANAGER, $ANDROID_CLI, $SDKMANAGER (each tool is empty if it isn't
# installed) and the functions below.
#
# The scripts are called by a relative path, through $PATH, or through a symlink to them, from any
# folder. So nothing here depends on the current folder, except local.properties, which belongs to
# the project the user works in. readlink -f resolves symlinks, so a symlinked lib.sh or script
# still finds the real repository.

TOOLS_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
BIN_DIR="$TOOLS_DIR/bin"

die() { echo "$(basename "$0"): $*" >&2; exit 1; }

# The local.properties of the Gradle project the user is in: the nearest one in the current folder
# or above it, so it's found from a project's subfolders too. Fails if there's none.
find_local_properties() {
    local dir="$PWD"
    while true; do
        if [ -f "$dir/local.properties" ]; then echo "$dir/local.properties"; return 0; fi
        if [ "$dir" = / ]; then return 1; fi
        dir="$(dirname "$dir")"
    done
}

# The SDK, searched in this order: $ANDROID_HOME, the deprecated $ANDROID_SDK_ROOT, sdk.dir in the
# project's local.properties (what Gradle uses, written by Android Studio), the SDK that the adb on
# $PATH belongs to, then Android Studio's default location. The first existing folder wins.
find_sdk() {
    local dir adb_on_path properties
    adb_on_path="$(command -v adb 2>/dev/null || true)"
    properties="$(find_local_properties || true)"
    for dir in \
        "${ANDROID_HOME:-}" \
        "${ANDROID_SDK_ROOT:-}" \
        "${properties:+$(sed -n 's/^sdk\.dir=//p' "$properties" | sed 's/\\\(.\)/\1/g')}" \
        "${adb_on_path:+$(dirname "$(dirname "$(readlink -f "$adb_on_path")")")}" \
        "$HOME/Android/Sdk"; do
        if [ -n "$dir" ] && [ -d "$dir" ]; then echo "$dir"; return 0; fi
    done
    echo "$HOME/Android/Sdk"   # doesn't exist: the tool checks below report what's missing
}

# The first candidate that is an executable file, else the command of that name on $PATH.
find_tool() {
    local name="$1" candidate
    shift
    for candidate in "$@"; do
        if [ -x "$candidate" ]; then echo "$candidate"; return 0; fi
    done
    command -v "$name" 2>/dev/null || true
}

SDK="$(find_sdk)"
ADB="$(find_tool adb "$SDK/platform-tools/adb")"
EMULATOR="$(find_tool emulator "$SDK/emulator/emulator")"
# cmdline-tools/latest is the standard layout; versioned folders (cmdline-tools/19.0) also occur,
# newest first.
mapfile -t _versioned_avdmanagers < <(printf "%s\n" "$SDK"/cmdline-tools/*/bin/avdmanager | sort -rV)
AVDMANAGER="$(find_tool avdmanager "$SDK/cmdline-tools/latest/bin/avdmanager" "${_versioned_avdmanagers[@]}")"
# The 'android' CLI replaces the deprecated sdkmanager, but only exists from cmdline-tools 22.0,
# so install hints fall back to sdkmanager on older ones (see install_hint).
mapfile -t _versioned_android_clis < <(printf "%s\n" "$SDK"/cmdline-tools/*/bin/android | sort -rV)
ANDROID_CLI="$(find_tool android "$SDK/cmdline-tools/latest/bin/android" "${_versioned_android_clis[@]}")"
mapfile -t _versioned_sdkmanagers < <(printf "%s\n" "$SDK"/cmdline-tools/*/bin/sdkmanager | sort -rV)
SDKMANAGER="$(find_tool sdkmanager "$SDK/cmdline-tools/latest/bin/sdkmanager" "${_versioned_sdkmanagers[@]}")"

# The command that installs an SDK package, for messages that suggest one. 'android sdk install'
# is the current tool; sdkmanager is deprecated but is all there is before cmdline-tools 22.0, so
# suggest whichever this SDK actually has, and the new one when it has neither. --no-metrics keeps
# the suggested command from reporting usage to Google, which the android CLI does by default and
# can only be turned off per call (SETUP.md, "Telemetry").
install_hint() {
    if [ -z "$ANDROID_CLI" ] && [ -n "$SDKMANAGER" ]; then
        echo "sdkmanager \"$1\""
    else
        echo "android sdk install --no-metrics \"$1\""
    fi
}

# Exits with an install hint if a tool wasn't found. $1 = variable value, $2 = SDK package name.
# Without any SDK there's nothing to install with either, so that case points to SETUP.md instead.
require() {
    [ -n "$1" ] && return 0
    [ -d "$SDK" ] || die "no Android SDK found. Set \$ANDROID_HOME to your SDK, or install one
as described in $TOOLS_DIR/SETUP.md (steps 2-5). Looked in: \$ANDROID_HOME, \$ANDROID_SDK_ROOT,
sdk.dir in local.properties, the adb on \$PATH, $SDK."
    die "'$2' not found in the SDK ($SDK) or on \$PATH. Install it with:
  $(install_hint "$2")
or point \$ANDROID_HOME at your SDK (see $TOOLS_DIR/SETUP.md)."
}

# How to call one of the scripts in bin/, for messages that suggest a command: its bare name when
# that name on $PATH is this very script, else the way the running script was called (e.g.
# ../android-tv-wsl-dev-tools/bin/start-emulator.sh), so the suggestion can be pasted as is.
command_for() {
    local on_path
    on_path="$(command -v "$1" 2>/dev/null || true)"
    if [ -n "$on_path" ] && [ "$(readlink -f "$on_path")" = "$BIN_DIR/$1" ]; then
        echo "$1"
    else
        echo "$(dirname "$0")/$1"
    fi
}

# Host facts. The overrides exist for the tests (tests/), which simulate other machines; nothing
# else should set them.
KVM_DEVICE="${ADT_KVM_DEVICE:-/dev/kvm}"
is_wsl() { grep -qi microsoft "${ADT_PROC_VERSION:-/proc/version}" 2>/dev/null; }

# The folders AVDs can live in, in the order the emulator and avdmanager look at them.
avd_homes() {
    [ -n "${ANDROID_AVD_HOME:-}" ] && echo "$ANDROID_AVD_HOME"
    [ -n "${ANDROID_EMULATOR_HOME:-}" ] && echo "$ANDROID_EMULATOR_HOME/avd"
    [ -n "${ANDROID_USER_HOME:-}" ] && echo "$ANDROID_USER_HOME/avd"
    [ -n "${ANDROID_SDK_HOME:-}" ] && echo "$ANDROID_SDK_HOME/.android/avd"
    echo "$HOME/.android/avd"
}

# Prints the <name>.avd folder of an existing AVD (the one holding config.ini); fails if none.
# Each AVD has a <name>.ini next to it whose path= line says where its folder is.
avd_dir() {
    local home path
    while IFS= read -r home; do
        [ -f "$home/$1.ini" ] || continue
        path="$(sed -n 's/^path=//p' "$home/$1.ini" | tr -d '\r')"
        [ -d "$path" ] || path="$home/$1.avd"
        if [ -f "$path/config.ini" ]; then echo "$path"; return 0; fi
    done < <(avd_homes)
    return 1
}

# The system image an AVD folder (from avd_dir) was made from, as its config.ini's image.sysdir.1
# names it: system-images/android-25/android-tv/x86/ ("key = value" once the emulator has
# rewritten the file). Empty if config.ini doesn't say.
avd_image() {
    sed -n 's/^image\.sysdir\.1 *= *//p' "$1/config.ini" | head -n 1 | tr -d '\r'
}

# Names of all AVDs, one per line. Newer emulators also print log lines here; drop them.
list_avds() {
    "$EMULATOR" -list-avds 2>/dev/null | grep -E '^[A-Za-z0-9._-]+$' || true
}

# adb with a time limit, for calls that are repeated until something changes. On an emulator
# that's half booted or starved of CPU (a small CI runner), `adb shell` or `adb emu` can hang
# without ever answering, and a loop waiting on it would never reach its own time limit. A call
# that runs out of time fails like one that got no answer. ADT_ADB_TIMEOUT exists for the tests.
# The emulator tier copies its default and the 5 s (tests/emulator/test_on_emulator.py).
ADB_TIMEOUT="${ADT_ADB_TIMEOUT:-15}"
adb_bounded() {
    timeout -k 5 "$ADB_TIMEOUT" "$ADB" "$@"
}

# time_is_up <start> <seconds>: whether at least that many seconds have passed since <start>, a
# value of $SECONDS. $SECONDS counts whole seconds of the clock, so `$SECONDS - start >= n` can be
# true only n - 1 s and a bit after the start (a limit of 1 s could end at once); more than n whole
# seconds is at least n s, and at most n + 1. A limit of 0 is up at once.
time_is_up() {
    [ "$2" -eq 0 ] || [ $((SECONDS - $1)) -gt "$2" ]
}

# Serials of the running emulators, one per line (physical devices are left out).
running_emulators() {
    adb_bounded devices 2>/dev/null | awk '$1 ~ /^emulator-[0-9]+$/ { print $1 }'
}

# The AVD name of a running emulator, asked through its console. Empty if it doesn't answer yet.
emulator_avd() {
    adb_bounded -s "$1" emu avd name 2>/dev/null | head -n 1 | tr -d '\r'
}

# wait_for_home <serial> <seconds> [<backs>]: waits until a device has settled after its boot, which
# sys.boot_completed doesn't mean: until its home app's activity is in front, a window of the home
# app has the focus (keys go only to the focused window; on a device short of CPU, none had it
# long after the boot), and, from API 24 on, a HOME intent resolves to the home app. Until the
# user is unlocked it resolves to Settings' FallbackHome, which holds the screen meanwhile. All
# that for more than HOME_STABLE s in a row, as the front changes a few times while the home app
# starts. API 22 and 23 have no `cmd` to ask, and no FallbackHome: there the home app is the
# activity in front when its task was started by a HOME intent. Google TV's home app without an
# account shows a sign-in screen, and on API 22 with another home app installed, Android's chooser
# (package android) is in front instead: each counts as the home app. Another screen can keep
# the focus: a new AVD's first boot shows "USB drive connected" (for its SD card) in front of the
# home app on API 23 and 29, until Back. So when a window of another app has kept the focus for
# BACK_AFTER s, once the user is unlocked, it presses Back, up to <backs> times (default 0: it
# only looks; never on a device someone may be using). Prints the home app's package. After
# <seconds> (0: no limit) it prints what was in front instead, and fails. ADT_HOME_STABLE exists
# for the tests (BACK_AFTER follows it). The emulator tier copies its default
# (tests/emulator/test_on_emulator.py).
HOME_STABLE="${ADT_HOME_STABLE:-3}"
BACK_AFTER=$((HOME_STABLE * 3 + 1))
wait_for_home() {
    local serial="$1" limit="$2" backs="${3:-0}" start=$SECONDS since="" sdk="" resolved home
    local dump window app package other="" other_since=""
    while true; do
        [ -n "$sdk" ] || sdk="$(adb_bounded -s "$serial" shell getprop ro.build.version.sdk \
            < /dev/null 2>/dev/null | tr -d '\r' || true)"
        [[ "$sdk" =~ ^[0-9]+$ ]] || sdk=""
        resolved=""
        if [ -n "$sdk" ] && [ "$sdk" -ge 24 ]; then
            resolved="$(adb_bounded -s "$serial" shell cmd package resolve-activity --brief \
                -a android.intent.action.MAIN -c android.intent.category.HOME < /dev/null \
                2>/dev/null | tr -d '\r' | grep -E '^[[:alnum:]_.]+/[[:alnum:]_.$]+$' | tail -n 1 \
                || true)"
        fi
        home="$resolved"
        # mCurrentFocus=Window{7a8a869 u0 <title>}, where an activity's window has the title
        # <package>/<class>, or mCurrentFocus=null. mFocusedApp names an ActivityRecord{<hash> u0
        # <package>/<class> t<task>}, alone or inside an AppWindowToken (before API 29).
        dump="$(adb_bounded -s "$serial" shell dumpsys window < /dev/null 2>/dev/null | tr -d '\r' \
            || true)"
        window="$(sed -n 's/^ *mCurrentFocus=Window{[^ ]* [^ ]* \(.*\)}$/\1/p' <<<"$dump" | head -n 1)"
        app="$(sed -n 's/^ *mFocusedApp=.*ActivityRecord{[^ ]* [^ ]* \([^ }]*\).*/\1/p' <<<"$dump" \
            | head -n 1)"
        # Before API 24: the focused activity, if its task was started by a HOME intent. The dump
        # lists each task as "* TaskRecord{<hash> #<id> ...", then its intent={...}, and at the end
        # mFocusedActivity: ActivityRecord{<hash> u0 <package>/<class> t<id>}.
        if [ -n "$sdk" ] && [ "$sdk" -lt 24 ]; then
            home="$(adb_bounded -s "$serial" shell dumpsys activity activities < /dev/null \
                2>/dev/null | tr -d '\r' | awk '
                    /^ *\* TaskRecord\{/ { task = substr($3, 2) }
                    /^ *intent=\{/ { home[task] = /android\.intent\.category\.HOME/ }
                    /^ *mFocusedActivity: / { t = $NF; gsub(/[^0-9]/, "", t); if (home[t]) print $(NF - 1) }' \
                || true)"
        fi
        package="${home%%/*}"
        if [ -n "$home" ] && [[ "$home" != *FallbackHome ]] && [ "${app%%/*}" = "$package" ] \
                && [[ "$window" == "$package/"* ]]; then
            [ -n "$since" ] || since=$SECONDS
            if time_is_up "$since" "$HOME_STABLE"; then echo "$package"; return 0; fi
            other=""
        else
            since=""
            # The same window of another app with the focus for BACK_AFTER s, once the user is
            # unlocked (from API 24 on, a HOME intent no longer resolves to FallbackHome): Back.
            # Without a focused window, a key would reach nothing.
            if [ -n "$window" ] && [[ "$window" != "$package/"* ]] && [ -n "$sdk" ] \
                    && { [ "$sdk" -lt 24 ] || { [ -n "$resolved" ] \
                    && [[ "$resolved" != *FallbackHome ]]; }; }; then
                if [ "$window" != "$other" ]; then
                    other="$window"
                    other_since=$SECONDS
                elif [ "$backs" -gt 0 ] && time_is_up "$other_since" "$BACK_AFTER"; then
                    adb_bounded -s "$serial" shell input keyevent BACK < /dev/null > /dev/null \
                        2>&1 || true
                    backs=$((backs - 1))
                    other=""
                fi
            else
                other=""
            fi
            if [ "$limit" -gt 0 ] && time_is_up "$start" "$limit"; then
                echo "activity ${app:-none}, focused window ${window:-none}${resolved:+; a HOME intent resolves to $resolved}"
                return 1
            fi
        fi
        sleep 1
    done
}
