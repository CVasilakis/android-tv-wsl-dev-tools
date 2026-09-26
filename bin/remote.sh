#!/usr/bin/env bash
# A TV remote in the terminal: each key press is sent to the device as an Android key.
#
# Usage:   remote.sh [adb-serial]
#          remote.sh --help
# Target:  the serial given, else $ANDROID_SERIAL, else the only running emulator. Phones and
#          TVs connected at the same time are only used when named, so keys never go to one by
#          accident.
# Keys:    arrows = D-pad, Enter = OK, Esc/Backspace = Back, h = Home, m = Menu,
#          p = Play/Pause, +/- = Volume, q = quit
#
# Each key is sent with `adb shell input keyevent <KEY>`: one mechanism for every Android version,
# emulators and physical devices alike, whatever window has focus (which matters under WSLg, see
# wslg-toolbar.py). Each call starts a Java process on the device, so each key lags a little;
# quick presses queue up and arrive in order. The emulator
# console (`adb emu event send`) would be faster, but it only exists on emulators and its key
# events never arrive on the API 30 TV image.
#
# Android sees these keys come from its virtual keyboard (deviceId -1, source keyboard), not from
# a D-pad device like a real remote; only code that checks a KeyEvent's device or source can tell.
# To see what Android received (Android 10 and newer list no key codes there):
#   adb shell dumpsys input | sed -n '/RecentQueue/,/PendingEvent/p'
#
# Troubleshooting:
#   "several emulators are running"  -> pass the serial: remote.sh emulator-5554
#   an error for each key            -> the device is gone, offline or unauthorized: adb devices
#
# Deliberately no `set -e`: one failed adb call (e.g. the device is briefly busy) must not
# kill the remote in the middle of a session.
set -uo pipefail

usage() {
    cat <<'EOF'
Usage: remote.sh [adb-serial]

A TV remote in the terminal: each key press is sent to an emulator or Android device.

  adb-serial  the device to control, e.g. emulator-5554. Default: $ANDROID_SERIAL, else the
              only running emulator
  -h, --help  show this help

Keys: arrows = D-pad, Enter = OK, Esc/Backspace = Back, h = Home, m = Menu,
      p = Play/Pause, +/- = Volume, q = quit
EOF
}
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
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

echo "Controlling $SERIAL"
cat <<'EOF'
Remote: arrows = D-pad, Enter = OK, Esc/Backspace = Back, h = Home, m = Menu,
        p = Play/Pause, +/- = Volume, q = quit
EOF

# read -n1 reads one key without waiting for Enter; -s hides it; IFS= keeps spaces and Enter.
# Enter is read as the empty string, because newline is read's delimiter.
while IFS= read -rsn1 key; do
    case "$key" in
        $'\e')
            # Arrow keys arrive as ESC [ A..D. A lone Esc has nothing after it, so the short
            # timeout tells the two apart. Don't remove the timeout, or Esc waits for more keys.
            read -rsn2 -t 0.05 rest || true
            case "$rest" in
                "[A") press DPAD_UP ;;
                "[B") press DPAD_DOWN ;;
                "[C") press DPAD_RIGHT ;;
                "[D") press DPAD_LEFT ;;
                "")   press BACK ;;
            esac ;;
        "")        press DPAD_CENTER ;;         # what a real remote's OK button sends
                                                # (ENTER is a keyboard key)
        $'\x7f')   press BACK ;;                # Backspace
        h)         press HOME ;;
        m)         press MENU ;;
        p)         press MEDIA_PLAY_PAUSE ;;
        +|=)       press VOLUME_UP ;;           # '=' is '+' without Shift
        -)         press VOLUME_DOWN ;;
        q)         break ;;
    esac
done
