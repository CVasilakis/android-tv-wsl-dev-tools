#!/usr/bin/env bash
# Creates an Android TV (API 25) emulator for developing and testing TV apps.
#
# Usage:   create-avd.sh [avd-name]      (default: $ADT_AVD, else tv_api25)
#          create-avd.sh --help
# Result:  <avd-name>.avd and <avd-name>.ini in the folder avdmanager keeps AVDs in
#          ($ANDROID_AVD_HOME if set, ~/.android/avd by default)
#
# Requires (see setup.md): cmdline-tools, and the SDK packages "emulator" and
# "system-images;android-25;android-tv;x86". lib.sh describes how the SDK is found.
#
# Common errors:
#   'Error: "emulator" package must be installed!'
#       avdmanager refuses to create any AVD without the emulator package:
#       sdkmanager "emulator"
#   'Package path is not valid' / 'Invalid --tag android-tv for the selected package'
#       The system image is missing or incomplete. `sdkmanager --list_installed` must list
#       system-images;android-25;android-tv;x86 (its folder must contain package.xml).
#   'AVD ... already exists'
#       Printed by this script on purpose; it never overwrites an AVD (that would wipe its data).
#
# The hardware settings below are deliberate choices, each explained next to it. Several of them
# fix real problems (keyboard input, WSLg rendering, portrait orientation), so don't drop one
# just because the emulator seems to start without it.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: create-avd.sh [avd-name]

Creates an Android TV emulator (AVD) for developing TV apps: Android 7.1 (API 25), 1080p,
landscape, D-pad and keyboard input. Never overwrites an existing AVD.

  avd-name    name of the new AVD (default: $ADT_AVD, else tv_api25)
  -h, --help  show this help

Needs the SDK packages "cmdline-tools;latest", "emulator" and
"system-images;android-25;android-tv;x86" (see setup.md). The SDK is the first of:
$ANDROID_HOME, $ANDROID_SDK_ROOT, sdk.dir in the local.properties of the project you're in, the
SDK of the adb on $PATH, ~/Android/Sdk. The AVD goes where avdmanager keeps AVDs
($ANDROID_AVD_HOME if set, ~/.android/avd by default).
EOF
}
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    -*)        usage >&2; exit 2 ;;
esac

# shellcheck source=../lib/lib.sh
source "$(dirname "$(readlink -f "$0")")/../lib/lib.sh"

AVD_NAME="${1:-${ADT_AVD:-tv_api25}}"
IMAGE="system-images;android-25;android-tv;x86"
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
echo no | "$AVDMANAGER" create avd \
    --name "$AVD_NAME" \
    --package "$IMAGE" \
    --tag android-tv \
    --abi x86 \
    --device tv_1080p \
    --sdcard 512M                         # storage for `adb push`-ed test wallpapers/images

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
set_prop hw.ramSize 2048                  # enough for API 25; more starves Gradle, which shares the machine's memory
set_prop hw.cpu.ncore 4
set_prop disk.dataPartition.size 4G       # the image's default userdata is ~550 MB: too small for many test apps
set_prop hw.gpu.enabled yes
set_prop hw.gpu.mode swiftshader_indirect # software GL: works on any host (no GPU driver or WSLg GPU passthrough
                                          # needed); start-emulator.sh passes -gpu too, unless you pass your own
set_prop hw.initialOrientation landscape  # the tv_1080p profile defaults to portrait, which is wrong for a TV
set_prop showDeviceFrame no               # no device skin around the screen
set_prop hw.audioInput no                 # no microphone needed

echo "Created AVD '$AVD_NAME' in $AVD_DIR. Start it with: $(command_for start-emulator.sh) $AVD_NAME"
