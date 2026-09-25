# shellcheck shell=bash
# shellcheck disable=SC2034  # the variables set here are used by the scripts that source this file
# Shared helpers for the scripts in bin/. Sourced, not run.
#
# Finds the Android SDK, its tools and AVDs wherever the user keeps them, so the scripts work with
# any setup, not only the one in setup.md. After sourcing: $TOOLS_DIR (this repository), $BIN_DIR,
# $SDK, $ADB, $EMULATOR, $AVDMANAGER (each tool is empty if it isn't installed) and the functions
# below.
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

# Exits with an install hint if a tool wasn't found. $1 = variable value, $2 = SDK package name.
# Without any SDK, sdkmanager doesn't exist either, so that case points to setup.md instead.
require() {
    [ -n "$1" ] && return 0
    [ -d "$SDK" ] || die "no Android SDK found. Set \$ANDROID_HOME to your SDK, or install one
as described in $TOOLS_DIR/setup.md (steps 2-5). Looked in: \$ANDROID_HOME, \$ANDROID_SDK_ROOT,
sdk.dir in local.properties, the adb on \$PATH, $SDK."
    die "'$2' not found in the SDK ($SDK) or on \$PATH. Install it with:
  sdkmanager \"$2\"
or point \$ANDROID_HOME at your SDK (see $TOOLS_DIR/setup.md)."
}

# How to call one of the scripts in bin/, for messages that suggest a command: its bare name when
# that name on $PATH is this very script, else the way the running script was called (e.g.
# ../android-cli-dev-tools/bin/start-emulator.sh), so the suggestion can be pasted as is.
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

# Names of all AVDs, one per line. Newer emulators also print log lines here; drop them.
list_avds() {
    "$EMULATOR" -list-avds 2>/dev/null | grep -E '^[A-Za-z0-9._-]+$' || true
}

# Serials of the running emulators, one per line (physical devices are left out).
running_emulators() {
    "$ADB" devices 2>/dev/null | awk '$1 ~ /^emulator-[0-9]+$/ { print $1 }'
}

# The AVD name of a running emulator, asked through its console. Empty if it doesn't answer yet.
emulator_avd() {
    "$ADB" -s "$1" emu avd name 2>/dev/null | head -n 1 | tr -d '\r'
}
