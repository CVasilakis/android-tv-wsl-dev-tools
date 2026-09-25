#!/usr/bin/env bash
# A TV remote in the terminal: each key press is injected into the emulator's input device.
#
# Usage:   remote.sh [adb-serial]
#          remote.sh --help
# Target:  the serial given, else $ANDROID_SERIAL, else the only running emulator (phones and
#          TVs connected at the same time are ignored, since this only works with emulators)
# Keys:    arrows = D-pad, Enter = OK, Esc/Backspace = Back, h = Home, m = Menu,
#          p = Play/Pause, +/- = Volume, q = quit
#
# Why not `adb shell input keyevent`? Each call starts a Java process on the device (~0.2 s per
# key on this AVD). `adb emu event send` goes through the emulator console (~0.02 s).
# It also works whatever window has focus, which matters under WSLg (see wslg-toolbar.py).
# The trade-off: it only works with emulators, not physical TVs (use `adb shell input keyevent` there).
#
# Codes sent are *Linux input scan codes* (not Android KEYCODE_* values). The guest translates them
# with /system/usr/keylayout/qwerty.kl, e.g. scan code 232 -> DPAD_CENTER, 158 -> BACK. To check a
# mapping: adb shell cat /system/usr/keylayout/qwerty.kl | grep -w <code>
# To see what Android actually received:
#   adb shell dumpsys input | sed -n '/RecentQueue/,/PendingEvent/p'
#
# Troubleshooting:
#   "several emulators are running"  -> pass the serial: remote.sh emulator-5554
#   an error, or nothing happens     -> the target isn't an emulator, or its console is
#                                       unreachable (restart the emulator)
#
# Deliberately no `set -e`: one failed adb call (e.g. the emulator is briefly busy) must not
# kill the remote in the middle of a session.
set -uo pipefail

usage() {
    cat <<'EOF'
Usage: remote.sh [adb-serial]

A TV remote in the terminal: each key press is sent to a running emulator.

  adb-serial  the emulator to control, e.g. emulator-5554. Default: $ANDROID_SERIAL, else the
              only running emulator
  -h, --help  show this help

Keys: arrows = D-pad, Enter = OK, Esc/Backspace = Back, h = Home, m = Menu,
      p = Play/Pause, +/- = Volume, q = quit

Works with emulators only; for a physical TV use: adb shell input keyevent <KEY>
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
[[ "$SERIAL" == emulator-* ]] || die "'$SERIAL' isn't an emulator. For a physical device use: adb shell input keyevent <KEY>"
ADB=("$ADB" -s "$SERIAL")

# Key down + key up in one console command, i.e. a full key press.
press() { "${ADB[@]}" emu event send "EV_KEY:$1:1" "EV_KEY:$1:0" > /dev/null; }

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
                "[A") press 103 ;;      # KEY_UP        -> DPAD_UP
                "[B") press 108 ;;      # KEY_DOWN      -> DPAD_DOWN
                "[C") press 106 ;;      # KEY_RIGHT     -> DPAD_RIGHT
                "[D") press 105 ;;      # KEY_LEFT      -> DPAD_LEFT
                "")   press 158 ;;      # KEY_BACK      -> BACK
            esac ;;
        "")        press 232 ;;         # KEY_REPLY     -> DPAD_CENTER, what a real remote's OK
                                        #                  button sends (ENTER=28 is a keyboard key)
        $'\x7f')   press 158 ;;         # Backspace     -> BACK
        h)         press 102 ;;         # KEY_HOME      -> HOME
        m)         press 139 ;;         # KEY_MENU      -> MENU
        p)         press 164 ;;         # KEY_PLAYPAUSE -> MEDIA_PLAY_PAUSE
        +|=)       press 115 ;;         # KEY_VOLUMEUP  -> VOLUME_UP   ('=' is '+' without Shift)
        -)         press 114 ;;         # KEY_VOLUMEDOWN -> VOLUME_DOWN
        q)         break ;;
    esac
done
