#!/usr/bin/env bash
# A TV remote in the terminal: each key press is sent to the device as an Android key. Also holds
# a key for scripts: a long press, which `adb shell input keyevent` can't do before API 30.
#
# Usage:   remote.sh [adb-serial]
#          remote.sh --long-press <key> [adb-serial]
#          remote.sh --help
# Target:  the serial given, else $ANDROID_SERIAL, else the only running emulator. Phones and
#          TVs connected at the same time are only used when named, so keys never go to one by
#          accident.
# Keys:    arrows = D-pad, Enter = OK, Esc/Backspace = Back, h = Home, m = Menu,
#          p = Play/Pause, +/- = Volume, l then a key = long press of that key, q = quit
#
# Each key is sent with `adb shell input keyevent <KEY>`: one mechanism for every Android version,
# emulators and physical devices alike, whatever window has focus (which matters under WSLg, see
# wslg-toolbar.py). Each call starts a Java process on the device, so each key lags a little;
# quick presses queue up and arrive in order. The emulator
# console (`adb emu event send`) would be faster, but it only exists on emulators and its key
# events never arrive on the API 30 TV image.
#
# A long press is `input keyevent --longpress` from API 30 on, and replayed by `monkey` before
# (see long_press below): there `--longpress` sends the key's release at once, and views count a
# long press by how long the key stays down.
#
# Android sees these keys come from its virtual keyboard (deviceId -1, source keyboard), not from
# a D-pad device like a real remote; only code that checks a KeyEvent's device or source can tell.
# To see what Android received (Android 10 and newer list no key codes there):
#   adb shell dumpsys input | sed -n '/RecentQueue/,/PendingEvent/p'
#
# Troubleshooting:
#   "several emulators are running"  -> pass the serial: remote.sh emulator-5554
#   an error for each key            -> the device is gone, offline or unauthorized: adb devices
#   "the long press failed"          -> monkey's output follows; the device may be busy or gone
#
# Deliberately no `set -e`: one failed adb call (e.g. the device is briefly busy) must not
# kill the remote in the middle of a session.
set -uo pipefail

usage() {
    cat <<'EOF'
Usage: remote.sh [adb-serial]
       remote.sh --long-press <key> [adb-serial]

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
            echo "remote.sh: unknown key '$2'; use a key code number or one of:" \
                 "$(printf '%s\n' "${!KEY_CODES[@]}" | sort | tr '\n' ' ' | sed 's/ $//')" >&2
            exit 2
        fi
        shift 2 ;;
    -*)        usage >&2; exit 2 ;;
esac

# shellcheck source=../lib/lib.sh
source "$(dirname "$(readlink -f "$0")")/../lib/lib.sh"
require "$ADB" "platform-tools"

SERIAL="${1:-${ANDROID_SERIAL:-}}"
if [ -z "$SERIAL" ]; then
    mapfile -t EMULATORS < <(running_emulators)
    case ${#EMULATORS[@]} in
        0) die "no running emulator. Start one with: $(command_for start-emulator.sh)" ;;
        1) SERIAL="${EMULATORS[0]}" ;;
        *) die "several emulators are running (${EMULATORS[*]}). Pass one: $(command_for remote.sh) <serial>" ;;
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

# restore_setting <name> <value before> <value monkey leaves>: puts a system setting back as it was
# before monkey ran; "null" (never set) by deleting it.
restore_setting() {
    [ "$2" = "$3" ] && return 0
    if [ "$2" = null ]; then
        "${ADB[@]}" shell settings delete system "$1" < /dev/null > /dev/null
    elif [[ "$2" =~ ^[0-9]+$ ]]; then
        "${ADB[@]}" shell settings put system "$1" "$2" < /dev/null > /dev/null
    fi
}

# long_press <Android key code>: holds the key the way a remote's button held down sends it: the
# key goes down; after the long-press timeout a repeat of it, which Android flags as a long press
# (FLAG_LONG_PRESS) as it does for a real remote; then the release. So both views, which count how
# long the key stays down, and onKeyLongPress() see a long press.
#
# From API 30 on, `input keyevent --longpress` does exactly that. Before, it sends all three events
# at once, so there monkey_long_press replays them instead. Monkey isn't used from API 30 on: on
# API 36 it adds a touchscreen ("Monkey touch") while it runs, a configuration change that
# recreates the app in front, whose window then drops the keys.
long_press() {
    local api
    api="$("${ADB[@]}" shell getprop ro.build.version.sdk < /dev/null | tr -d '\r')"
    if [[ "$api" =~ ^[0-9]+$ ]] && [ "$api" -ge 30 ]; then
        "${ADB[@]}" shell input keyevent --longpress "$1" < /dev/null > /dev/null && return 0
        echo "remote.sh: the long press failed on $SERIAL" >&2
        return 1
    fi
    monkey_long_press "$1"
}

# monkey_long_press <Android key code>: long_press before API 30. `monkey -f` replays the events
# from a script, waiting between them as their event times say (the release comes one long-press
# timeout after the repeat), and gives the repeat and the release the down time of the first
# event, like a real key. It runs as the shell user, as `input` does. Its quirks:
# - It refuses to start unless some activity has the category it's given, LAUNCHER by default,
#   which TVs don't use: HOME exists on every device.
# - When it exits it locks the screen rotation at 0 and unlocks it again (user_rotation=0,
#   accelerometer_rotation=1), so the settings it changed are put back.
# - While it runs, ActivityManager.isUserAMonkey() is true.
# - Before API 24 `adb shell` doesn't pass on exit statuses, so success is read from its output.
monkey_long_press() {
    local code="$1" timeout rotation user_rotation script output=""
    local device_script="/data/local/tmp/adt-long-press-$$"
    timeout="$(device_setting secure long_press_timeout)"
    [[ "$timeout" =~ ^[0-9]+$ ]] || timeout=500   # unset: Android's default before API 30
    rotation="$(device_setting system accelerometer_rotation)"
    user_rotation="$(device_setting system user_rotation)"
    script="$(mktemp "${TMPDIR:-/tmp}/adt-long-press.XXXXXX")" || return 1
    # DispatchKey(downTime, eventTime, action, code, repeat, metaState, deviceId, scanCode)
    cat > "$script" <<EOF
type= raw events
count= 1
speed= 1.0
start data >>
DispatchKey(1,1,0,$code,0,0,-1,0)
DispatchKey(1,$((1 + timeout)),0,$code,1,0,-1,0)
DispatchKey(1,$((1 + 2 * timeout)),1,$code,0,0,-1,0)
EOF
    "${ADB[@]}" push "$script" "$device_script" < /dev/null > /dev/null 2>&1 &&
        output="$("${ADB[@]}" shell monkey -c android.intent.category.HOME -f "$device_script" 1 \
                  < /dev/null 2>&1)"
    "${ADB[@]}" shell rm -f "$device_script" < /dev/null > /dev/null 2>&1
    rm -f "$script"
    restore_setting accelerometer_rotation "$rotation" 1
    restore_setting user_rotation "$user_rotation" 0
    if ! grep -q "Events injected: 3" <<<"$output"; then
        echo "remote.sh: the long press failed on $SERIAL:" >&2
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
