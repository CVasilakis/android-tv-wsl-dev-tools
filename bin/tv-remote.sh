#!/usr/bin/env bash
# A TV remote in the terminal: each key press is sent to the device as an Android key. Also holds
# a key for scripts: a long press, which `adb shell input keyevent` can't do before API 30.
#
# Usage:   tv-remote.sh [adb-serial]
#          tv-remote.sh --long-press <key> [adb-serial]
#          tv-remote.sh --help
# Target:  the serial given, else $ANDROID_SERIAL, else the only running emulator. Phones and
#          TVs connected at the same time are only used when named, so keys never go to one by
#          accident.
# Keys:    arrows = D-pad, Enter = OK, Esc/Backspace = Back, h = Home, m = Menu,
#          p = Play/Pause, +/- = Volume, l then a key = long press of that key, q = quit
#
# Each key is sent with `adb shell input keyevent <KEY>`: one mechanism for every Android version,
# emulators and physical devices alike, whatever window has focus (which matters under WSLg, see
# tv-wslg-toolbar.py). Each call starts a Java process on the device, so each key lags a little;
# quick presses queue up and arrive in order. The emulator
# console (`adb emu event send`) would be faster, but it only exists on emulators and its key
# events never arrive on the API 30 TV image.
#
# A long press is `input keyevent --longpress` from API 30 on, and sent by a small program of ours
# before (lib/HoldKey.java, see long_press below): there `--longpress` sends the key's release at
# once, and views count a long press by how long the key stays down.
#
# Android sees these keys come from its virtual keyboard (deviceId -1, source keyboard), not from
# a D-pad device like a real remote; only code that checks a KeyEvent's device or source can tell.
# To see what Android received (Android 10 and newer list no key codes there):
#   adb shell dumpsys input | sed -n '/RecentQueue/,/PendingEvent/p'
#
# Troubleshooting:
#   "several emulators are running"  -> pass the serial: tv-remote.sh emulator-5554
#   an error for each key            -> the device is gone, offline or unauthorized: adb devices
#   "the long press failed"          -> the program's output follows; the device may be busy or gone
#
# Deliberately no `set -e`: one failed adb call (e.g. the device is briefly busy) must not
# kill the remote in the middle of a session.
set -uo pipefail

usage() {
    cat <<'EOF'
Usage: tv-remote.sh [adb-serial]
       tv-remote.sh --long-press <key> [adb-serial]

A TV remote in the terminal: each key press is sent to an emulator or Android device.

  adb-serial          the device to control, e.g. emulator-5554. Default: $ANDROID_SERIAL,
                      else the only running emulator
  --long-press <key>  hold one key as a long press, then exit, on any API level. <key> is an
                      Android key code number, or one of: DPAD_UP, DPAD_DOWN, DPAD_LEFT,
                      DPAD_RIGHT, DPAD_CENTER (OK), ENTER, BACK, HOME, MENU, SEARCH,
                      MEDIA_PLAY_PAUSE, VOLUME_UP, VOLUME_DOWN (KEYCODE_ prefix optional)
  -h, --help          show this help

Keys: arrows = D-pad, Enter = OK, Esc/Backspace = Back, h = Home, m = Menu,
      p = Play/Pause, +/- = Volume, l then a key = long press of that key, q = quit
EOF
}

# Android key codes (android.view.KeyEvent) of the keys a TV remote has; others by number.
declare -A KEY_CODES=(
    [HOME]=3 [BACK]=4 [DPAD_UP]=19 [DPAD_DOWN]=20 [DPAD_LEFT]=21 [DPAD_RIGHT]=22
    [DPAD_CENTER]=23 [VOLUME_UP]=24 [VOLUME_DOWN]=25 [ENTER]=66 [MENU]=82 [SEARCH]=84
    [MEDIA_PLAY_PAUSE]=85
)

LONG_PRESS_CODE=""
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --long-press)
        if [ $# -lt 2 ] || [ $# -gt 3 ]; then usage >&2; exit 2; fi
        key="${2#KEYCODE_}"
        if [[ "$key" =~ ^[0-9]+$ ]]; then
            LONG_PRESS_CODE="$key"
        elif [ -n "${KEY_CODES[$key]:-}" ]; then
            LONG_PRESS_CODE="${KEY_CODES[$key]}"
        else
            echo "tv-remote.sh: unknown key '$2'; use a key code number or one of:" \
                 "$(printf '%s\n' "${!KEY_CODES[@]}" | sort | tr '\n' ' ' | sed 's/ $//')" >&2
            exit 2
        fi
        shift 2 ;;
    -*)        usage >&2; exit 2 ;;
esac

LIB_DIR="$(dirname "$(readlink -f "$0")")/../lib"
# shellcheck source=../lib/lib.sh
source "$LIB_DIR/lib.sh"
require "$ADB" "platform-tools"

SERIAL="${1:-${ANDROID_SERIAL:-}}"
if [ -z "$SERIAL" ]; then
    mapfile -t EMULATORS < <(running_emulators)
    case ${#EMULATORS[@]} in
        0) die "no running emulator. Start one with: $(command_for tv-start-emulator.sh)" ;;
        1) SERIAL="${EMULATORS[0]}" ;;
        *) die "several emulators are running (${EMULATORS[*]}). Pass one: $(command_for tv-remote.sh) <serial>" ;;
    esac
fi
ADB=("$ADB" -s "$SERIAL")

# press <Android key>: one full key press (down + up). `adb shell` forwards its stdin to the
# device, so without </dev/null it would swallow the keys typed (or piped) after it.
press() { "${ADB[@]}" shell input keyevent "$1" < /dev/null > /dev/null; }

# device_setting <namespace> <name>: a setting's value on the device ("null" when it's unset).
device_setting() {
    "${ADB[@]}" shell settings get "$1" "$2" < /dev/null | tr -d '\r'
}

# long_press <Android key code>: holds the key the way a remote's button held down sends it: the
# key goes down; after the long-press timeout a repeat of it, which Android flags as a long press
# (FLAG_LONG_PRESS) as it does for a real remote; then the release. So both views, which count how
# long the key stays down, and onKeyLongPress() see a long press.
#
# From API 30 on, `input keyevent --longpress` does exactly that. Before, it sends all three events
# at once, so there held_key sends them instead. Monkey, which replayed them before, isn't used:
# on API 36 it adds a touchscreen ("Monkey touch") while it runs, a configuration change that
# recreates the app in front, whose window then drops the keys; and on API 28 a window that opens
# while it runs (a tile's menu, a panel) never gets its focus.
long_press() {
    local api
    api="$("${ADB[@]}" shell getprop ro.build.version.sdk < /dev/null | tr -d '\r')"
    if [[ "$api" =~ ^[0-9]+$ ]] && [ "$api" -ge 30 ]; then
        "${ADB[@]}" shell input keyevent --longpress "$1" < /dev/null > /dev/null && return 0
        echo "tv-remote.sh: the long press failed on $SERIAL" >&2
        return 1
    fi
    held_key "$1"
}

# held_key <Android key code>: long_press before API 30. HoldKey (lib/HoldKey.java, built into
# lib/HoldKey.dex) runs on the device as the shell user, through app_process, and injects the three
# events into the input system with the timing the long-press timeout gives (the repeat after
# it, the release as long after the repeat), all with the down time of the first, like a real key.
# - Before API 24 `adb shell` doesn't pass on exit statuses, so success is read from its output.
# - The dex is pushed for each press and removed after: nothing is left on the device.
held_key() {
    local code="$1" timeout output="" device_dex="/data/local/tmp/adt-hold-key-$$.dex"
    timeout="$(device_setting secure long_press_timeout)"
    [[ "$timeout" =~ ^[0-9]+$ ]] || timeout=500   # unset: Android's default before API 30
    "${ADB[@]}" push "$LIB_DIR/HoldKey.dex" "$device_dex" < /dev/null > /dev/null 2>&1 &&
        output="$("${ADB[@]}" shell "CLASSPATH=$device_dex app_process /system/bin HoldKey $code $timeout $((2 * timeout))" \
                  < /dev/null 2>&1)"
    "${ADB[@]}" shell rm -f "$device_dex" < /dev/null > /dev/null 2>&1
    if ! grep -q "Events injected: 3" <<<"$output"; then
        echo "tv-remote.sh: the long press failed on $SERIAL:" >&2
        tr -d '\r' <<<"${output:-adb push failed}" | grep -v '^[[:space:]]*$' | tail -n 5 >&2
        return 1
    fi
}

if [ -n "$LONG_PRESS_CODE" ]; then
    long_press "$LONG_PRESS_CODE"
    exit
fi

# send <Android key>: presses it, or holds it when the key before was l.
HOLD_NEXT=""
send() {
    if [ -n "$HOLD_NEXT" ]; then
        HOLD_NEXT=""
        long_press "${KEY_CODES[$1]}"
    else
        press "$1"
    fi
}

echo "Controlling $SERIAL"
cat <<'EOF'
Remote: arrows = D-pad, Enter = OK, Esc/Backspace = Back, h = Home, m = Menu,
        p = Play/Pause, +/- = Volume, l then a key = long press of that key, q = quit
EOF

# handle_key <key>: sends what one key read stands for; returns 1 for q (quit). Enter is the
# empty string, because newline is read's delimiter.
handle_key() {
    local next rest
    case "$1" in
        $'\e')
            # Arrow keys arrive as ESC [ A..D (ESC O A..D in a terminal's application mode). A
            # lone Esc has nothing after it, so the short timeout tells the two apart. Don't
            # remove the timeout, or Esc waits for more keys. Any other key right after an Esc
            # was typed ahead (while the key before was being sent) or piped: Esc is Back, and
            # that key is a key of its own. (So Alt+h, which terminals send as Esc h, is Back
            # then Home.)
            if ! IFS= read -rsn1 -t 0.05 next; then
                send BACK
            elif [ "$next" = "[" ] || [ "$next" = O ]; then
                IFS= read -rsn1 -t 0.05 rest || true
                case "$rest" in
                    A) send DPAD_UP ;;
                    B) send DPAD_DOWN ;;
                    C) send DPAD_RIGHT ;;
                    D) send DPAD_LEFT ;;
                    *) HOLD_NEXT="" ;;           # another key's sequence, e.g. Delete
                esac
            else
                send BACK
                handle_key "$next" || return 1
            fi ;;
        "")        send DPAD_CENTER ;;          # what a real remote's OK button sends
                                                # (ENTER is a keyboard key)
        $'\x7f')   send BACK ;;                 # Backspace
        h)         send HOME ;;
        m)         send MENU ;;
        p)         send MEDIA_PLAY_PAUSE ;;
        +|=)       send VOLUME_UP ;;            # '=' is '+' without Shift
        -)         send VOLUME_DOWN ;;
        l)         HOLD_NEXT=1 ;;
        q)         return 1 ;;
        *)         HOLD_NEXT="" ;;              # any other key cancels a pending l
    esac
    return 0   # a failed adb call doesn't end the remote
}

# read -n1 reads one key without waiting for Enter; -s hides it; IFS= keeps spaces and Enter.
while IFS= read -rsn1 key; do
    handle_key "$key" || break
done
