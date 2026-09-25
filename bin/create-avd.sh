#!/usr/bin/env bash
# Creates an Android TV emulator for developing and testing TV apps (API 25 unless told otherwise).
#
# Usage:   create-avd.sh [avd-name]            (default: $ADT_AVD, else tv_api25)
#          create-avd.sh --api 30 [avd-name]   (default: tv_api30)
#          create-avd.sh --help
# Result:  <avd-name>.avd and <avd-name>.ini in the folder avdmanager keeps AVDs in
#          ($ANDROID_AVD_HOME if set, ~/.android/avd by default)
#
# Requires (see setup.md): cmdline-tools, and the SDK packages "emulator" and
# "system-images;android-<level>;android-tv;x86". lib.sh describes how the SDK is found.
#
# Common errors:
#   'Error: "emulator" package must be installed!'
#       avdmanager refuses to create any AVD without the emulator package:
#       android sdk install --no-metrics "emulator"
#   'Package path is not valid' / 'Invalid --tag android-tv for the selected package'
#       The system image is missing or incomplete. `android sdk list --no-metrics` must list
#       system-images/android-<level>/android-tv/x86 (its folder must contain package.xml).
#       The script prints the install command when the image's folder is missing.
#   'AVD ... already exists'
#       Printed by this script on purpose; it never overwrites an AVD (that would wipe its data).
#   'Error: Could not load devices from .../android-30/android-tv/x86/devices.xml'
#       Harmless: the API 30 image lacks that file, avdmanager uses its own tv_1080p profile.
#
# The hardware settings below are deliberate choices, each explained next to it. Several of them
# fix real problems (keyboard input, WSLg rendering, portrait orientation), so don't drop one
# just because the emulator seems to start without it.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: create-avd.sh [--api <level>] [avd-name]

Creates an Android TV emulator (AVD) for developing TV apps: 1080p, landscape, D-pad and
keyboard input. Never overwrites an existing AVD.

  --api <level>  Android API level (default: 25, Android 7.1), e.g. 28 (Android 9) or
                 30 (Android 11)
  avd-name       name of the new AVD. Default: tv_api<level> with --api, else $ADT_AVD,
                 else tv_api25
  -h, --help     show this help

Needs the SDK packages "cmdline-tools;latest", "emulator" and
"system-images;android-<level>;android-tv;x86" (see setup.md). The SDK is the first of:
$ANDROID_HOME, $ANDROID_SDK_ROOT, sdk.dir in the local.properties of the project you're in, the
SDK of the adb on $PATH, ~/Android/Sdk. The AVD goes where avdmanager keeps AVDs
($ANDROID_AVD_HOME if set, ~/.android/avd by default).
EOF
}
API=""
AVD_NAME=""
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --api)     [[ "${2:-}" =~ ^[0-9]+$ ]] || { usage >&2; exit 2; }
                   API="$2"; shift 2 ;;
        -*)        usage >&2; exit 2 ;;
        *)         [ -z "$AVD_NAME" ] || { usage >&2; exit 2; }
                   AVD_NAME="$1"; shift ;;
    esac
done

# shellcheck source=../lib/lib.sh
source "$(dirname "$(readlink -f "$0")")/../lib/lib.sh"

# With --api, the default name follows the level: $ADT_AVD usually names the everyday AVD, and an
# AVD of another level taking that name would be a surprise.
if [ -n "$API" ]; then
    AVD_NAME="${AVD_NAME:-tv_api$API}"
else
    API=25
    AVD_NAME="${AVD_NAME:-${ADT_AVD:-tv_api25}}"
fi
IMAGE="system-images;android-$API;android-tv;x86"
require "$AVDMANAGER" "cmdline-tools;latest"
require "$EMULATOR" "emulator"

if existing="$(avd_dir "$AVD_NAME")"; then
    echo "AVD '$AVD_NAME' already exists ($existing). Delete it first with:"
    echo "  $AVDMANAGER delete avd -n $AVD_NAME"
    exit 1
fi

# avdmanager asks "Do you wish to create a custom hardware profile? [no]" interactively;
# piping 'no' keeps the script non-interactive. --tag/--abi are explicit so avdmanager fails
# loudly instead of guessing if the image layout ever changes.
# tv_1080p = 1920x1080 at 320 dpi (960x540 dp), the resolution TV UIs are designed for.
# avdmanager's own error for a missing image lists every image it knows instead of naming the
# package to install, so the install command is added when the image's folder isn't there.
if ! echo no | "$AVDMANAGER" create avd \
    --name "$AVD_NAME" \
    --package "$IMAGE" \
    --tag android-tv \
    --abi x86 \
    --device tv_1080p \
    --sdcard 512M; then                   # storage for `adb push`-ed test wallpapers/images
    if [ ! -f "$SDK/system-images/android-$API/android-tv/x86/package.xml" ]; then
        die "avdmanager failed: the system image isn't installed in $SDK. Install it with:
  $(install_hint "$IMAGE")"
    fi
    die "avdmanager failed (see above)."
fi

# avdmanager has no flags for hardware settings, so config.ini is patched directly. Where
# avdmanager put the AVD depends on the user's environment variables; avd_dir finds it.
AVD_DIR="$(avd_dir "$AVD_NAME")" || die "created '$AVD_NAME', but found no $AVD_NAME.ini in any of:
$(avd_homes)
Set \$ANDROID_AVD_HOME to the folder avdmanager uses, then run this script again."
CONFIG="$AVD_DIR/config.ini"
# set_prop replaces an existing key or appends it, so re-running it is harmless.
set_prop() {
    if grep -q "^$1=" "$CONFIG"; then
        sed -i "s|^$1=.*|$1=$2|" "$CONFIG"
    else
        echo "$1=$2" >> "$CONFIG"
    fi
}
set_prop hw.keyboard yes                  # expose the PC keyboard to Android as a hardware keyboard, so
                                          # arrows/Enter in the emulator window act as the remote
set_prop hw.dPad yes                      # device reports D-pad navigation (Configuration.navigation), like a real TV
set_prop hw.ramSize 2048                  # enough for the TV images; more starves Gradle and the other emulators
set_prop hw.cpu.ncore 4
set_prop disk.dataPartition.size 4G       # the image's default userdata is ~550 MB: too small for many test apps
set_prop hw.gpu.enabled yes
set_prop hw.gpu.mode swiftshader_indirect # software GL: works on any host (no GPU driver or WSLg GPU passthrough
                                          # needed); start-emulator.sh passes -gpu too, unless you pass your own
set_prop hw.initialOrientation landscape  # the tv_1080p profile defaults to portrait, which is wrong for a TV
set_prop showDeviceFrame no               # no device skin around the screen
set_prop hw.audioInput no                 # no microphone needed

echo "Created AVD '$AVD_NAME' in $AVD_DIR. Start it with: $(command_for start-emulator.sh) $AVD_NAME"
