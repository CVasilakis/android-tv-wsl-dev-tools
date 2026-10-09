#!/usr/bin/env bash
# Creates an Android TV emulator for developing and testing TV apps (API 25 unless told otherwise).
#
# Usage:   create-avd.sh [avd-name]            (default: $ADT_AVD, else tv_api25)
#          create-avd.sh --api 30 [avd-name]   (default: tv_api30)
#          create-avd.sh --google-tv --api 30 [avd-name]   (Google TV image; default: gtv_api30)
#          create-avd.sh --size 1280x720 --density 213 tv_720p   (another screen; default 1080p)
#          create-avd.sh --if-missing [...]    (an existing AVD of that image is fine: CI caches)
#          create-avd.sh --help
# Result:  <avd-name>.avd and <avd-name>.ini in the folder the emulator looks for AVDs in
#          ($ANDROID_AVD_HOME if set, ~/.android/avd by default)
#
# Requires (see SETUP.md): cmdline-tools, and the SDK packages "emulator" and
# "system-images;android-<level>;android-tv;x86" (google-tv with --google-tv). lib.sh describes
# how the SDK is found.
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
#       With --if-missing, an existing AVD made from the same system image is accepted instead
#       (exit 0, left as it is), so a script or CI job can run this every time. An AVD of the same
#       name from another image, or with another --size or --density than the ones given, is
#       still an error: it isn't the AVD that was asked for.
#   'Error: Could not load devices from .../android-30/android-tv/x86/devices.xml'
#       Harmless: the images from API 29 on lack that file, avdmanager uses its own tv_1080p
#       profile. avdmanager prints it for every AVD once such an image is installed.
#   'the Android TV image of API 21 has no ranchu kernel'
#       Printed by this script: the emulator can't boot that image (see IMAGE_DIR below).
#   'there are no Google TV images before API 30'
#       Printed by this script: Google TV x86 images exist for API 30, 31, 33, 34 and 36.
#
# The hardware settings below are deliberate choices, each explained next to it. Several of them
# fix real problems (keyboard input, WSLg rendering, portrait orientation), so don't drop one
# just because the emulator seems to start without it.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: create-avd.sh [--api <level>] [--google-tv] [--size <W>x<H>] [--density <dpi>]
                     [--if-missing] [avd-name]

Creates an Android TV emulator (AVD) for developing TV apps: 1080p, landscape, D-pad and
keyboard input. Never overwrites an existing AVD.

  --api <level>  Android API level (default: 25, Android 7.1), e.g. 22 (Android 5.1),
                 28 (Android 9), 30 (Android 11), 33 (Android 13) or 36 (Android 16).
                 22 is the oldest the emulator can boot
  --google-tv    use the Google TV image of that level instead of the Android TV one:
                 Android TV with Google's home screen. Needs --api 30 or newer
  --size <W>x<H> screen size in pixels, landscape (default: 1920x1080), e.g. 1280x720
                 or 3840x2160
  --density <dpi>
                 screen density (default: 320), e.g. 213 for 720p or 640 for 4K
  --if-missing   if the AVD already exists and was made from the same system image (and
                 with the --size and --density given), leave it as it is and succeed
                 (for scripts and CI caches)
  avd-name       name of the new AVD. Default: tv_api<level> with --api (gtv_api<level>
                 with --google-tv), else $ADT_AVD, else tv_api25
  -h, --help     show this help

Needs the SDK packages "cmdline-tools;latest", "emulator" and
"system-images;android-<level>;android-tv;x86" ("...;google-tv;x86" with --google-tv; see
SETUP.md). The SDK is the first of:
$ANDROID_HOME, $ANDROID_SDK_ROOT, sdk.dir in the local.properties of the project you're in, the
SDK of the adb on $PATH, ~/Android/Sdk. The AVD goes where the emulator looks for AVDs
($ANDROID_AVD_HOME if set, ~/.android/avd by default).
EOF
}
API=""
AVD_NAME=""
IF_MISSING=""
WIDTH=""                                  # --size and --density; empty: the tv_1080p profile's
HEIGHT=""
DENSITY=""
TAG=android-tv                            # the system image's tag: android-tv or google-tv
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --api)     [[ "${2:-}" =~ ^[0-9]+$ ]] || { usage >&2; exit 2; }
                   API="$2"; shift 2 ;;
        --google-tv) TAG=google-tv; shift ;;
        --size)    [[ "${2:-}" =~ ^([1-9][0-9]*)x([1-9][0-9]*)$ ]] || { usage >&2; exit 2; }
                   WIDTH="${BASH_REMATCH[1]}"; HEIGHT="${BASH_REMATCH[2]}"; shift 2 ;;
        --density) [[ "${2:-}" =~ ^[1-9][0-9]*$ ]] || { usage >&2; exit 2; }
                   DENSITY="$2"; shift 2 ;;
        --if-missing) IF_MISSING=1; shift ;;
        -*)        usage >&2; exit 2 ;;
        *)         [ -z "$AVD_NAME" ] || { usage >&2; exit 2; }
                   AVD_NAME="$1"; shift ;;
    esac
done

# shellcheck source=../lib/lib.sh
source "$(dirname "$(readlink -f "$0")")/../lib/lib.sh"

# Google TV images exist only from API 30 on, so there's no default level to fall back on, and a
# level below 30 would fail in avdmanager with an install command for a package that doesn't exist.
if [ "$TAG" = google-tv ]; then
    [ -n "$API" ] || die "--google-tv needs --api <level>: there are Google TV images for API 30,
31, 33, 34 and 36."
    [ "$API" -ge 30 ] || die "there are no Google TV images before API 30. Use --api 30 or newer,
or the Android TV image of API $API (without --google-tv)."
fi

# A TV's screen is landscape. A taller size makes a portrait panel, and Android TV then rotates
# its landscape picture onto it, so the window shows it sideways.
if [ -n "$WIDTH" ] && [ "$WIDTH" -lt "$HEIGHT" ]; then
    die "--size ${WIDTH}x$HEIGHT is taller than wide; a TV screen is landscape: --size ${HEIGHT}x$WIDTH"
fi

# With --api, the default name follows the level (and gtv_ marks a Google TV image): $ADT_AVD
# usually names the everyday AVD, and an AVD of another level taking that name would be a surprise.
if [ -n "$API" ]; then
    if [ "$TAG" = google-tv ]; then AVD_NAME="${AVD_NAME:-gtv_api$API}"; fi
    AVD_NAME="${AVD_NAME:-tv_api$API}"
else
    API=25
    AVD_NAME="${AVD_NAME:-${ADT_AVD:-tv_api25}}"
fi
IMAGE="system-images;android-$API;$TAG;x86"
IMAGE_DIR="$SDK/system-images/android-$API/$TAG/x86"
require "$AVDMANAGER" "cmdline-tools;latest"
require "$EMULATOR" "emulator"

# The emulator boots only images with a ranchu kernel (kernel-ranchu, kernel-ranchu-64). The API 21
# Android TV image has just the goldfish kernel (kernel-qemu), for an emulator engine that was
# removed; avdmanager would create the AVD anyway, and the emulator would then refuse to start it.
if [ -f "$IMAGE_DIR/package.xml" ] && ! compgen -G "$IMAGE_DIR/kernel-ranchu*" > /dev/null; then
    die "the Android TV image of API $API has no ranchu kernel (only kernel-qemu), and the emulator
can no longer boot such images. Use an Android TV image of API 22 or newer."
fi

# Never overwrite an AVD: that would wipe its data. With --if-missing, one made from the same
# image counts as done (avd_image, lib.sh).
if existing="$(avd_dir "$AVD_NAME")"; then
    if [ -z "$IF_MISSING" ]; then
        die "AVD '$AVD_NAME' already exists ($existing). Delete it first with:
  $AVDMANAGER delete avd -n $AVD_NAME
or pass --if-missing to keep it."
    fi
    existing_image="$(avd_image "$existing")"
    existing_image="${existing_image%/}"
    if [ "$existing_image" != "${IMAGE//;//}" ]; then
        die "AVD '$AVD_NAME' already exists ($existing), but was made from
${existing_image:-an unknown system image}, not ${IMAGE//;//}. Pick another name, or delete it with:
  $AVDMANAGER delete avd -n $AVD_NAME"
    fi
    # Only the screen settings given are compared: without --size, any existing size is fine.
    existing_setting() {
        sed -n "s/^$1 *= *//p" "$existing/config.ini" | head -n 1 | tr -d '\r'
    }
    for pair in "hw.lcd.width=$WIDTH" "hw.lcd.height=$HEIGHT" "hw.lcd.density=$DENSITY"; do
        key="${pair%%=*}"; wanted="${pair#*=}"
        [ -n "$wanted" ] || continue
        have="$(existing_setting "$key")"
        if [ "$have" != "$wanted" ]; then
            die "AVD '$AVD_NAME' already exists ($existing), but its $key is ${have:-not set}, not
$wanted. Pick another name, or delete it with:
  $AVDMANAGER delete avd -n $AVD_NAME"
        fi
    done
    echo "AVD '$AVD_NAME' already exists ($existing), made from $IMAGE; left as it is."
    exit 0
fi

# The AVD goes in the folder the emulator looks in (the first of avd_homes), and avdmanager is
# told so through ANDROID_AVD_HOME. Left to itself, avdmanager can pick another one: cmdline-tools
# 12.0 (on GitHub's runners) uses $XDG_CONFIG_HOME/.android/avd when XDG_CONFIG_HOME is set, and
# the emulator never finds the AVD there. That avdmanager ignores ANDROID_AVD_HOME while the
# folder doesn't exist, so it's created first.
read -r AVD_HOME < <(avd_homes)
mkdir -p "$AVD_HOME"

# avdmanager asks "Do you wish to create a custom hardware profile? [no]" interactively;
# piping 'no' keeps the script non-interactive. --tag/--abi are explicit so avdmanager fails
# loudly instead of guessing if the image layout ever changes.
# tv_1080p = 1920x1080 at 320 dpi (960x540 dp), the resolution TV UIs are designed for.
# avdmanager's own error for a missing image lists every image it knows instead of naming the
# package to install, so the install command is added when the image's folder isn't there.
if ! echo no | ANDROID_AVD_HOME="$AVD_HOME" "$AVDMANAGER" create avd \
    --name "$AVD_NAME" \
    --package "$IMAGE" \
    --tag "$TAG" \
    --abi x86 \
    --device tv_1080p \
    --sdcard 512M; then                   # API 22's /sdcard, for `adb push`-ed test wallpapers/images
    if [ ! -f "$IMAGE_DIR/package.xml" ]; then
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
# Software GL: works on any host (no GPU driver or WSLg GPU passthrough needed); swangle_indirect
# where SELinux would make swiftshader_indirect crash (see lib.sh). start-emulator.sh passes -gpu
# too, unless you pass your own.
GPU_MODE=swiftshader_indirect
if selinux_denies_execheap; then
    GPU_MODE=swangle_indirect
    echo "$SELINUX_GPU_REASON, so the AVD renders with swangle_indirect (hw.gpu.mode)."
fi
set_prop hw.gpu.mode "$GPU_MODE"
set_prop hw.initialOrientation landscape  # the tv_1080p profile defaults to portrait, which is wrong for a TV
set_prop showDeviceFrame no               # no device skin around the screen
set_prop hw.audioInput no                 # no microphone needed
# --size/--density replace the tv_1080p profile's screen. No skin is set, so the emulator window
# takes hw.lcd.* as they are; landscape needs the width to be the larger side.
if [ -n "$WIDTH" ]; then
    set_prop hw.lcd.width "$WIDTH"
    set_prop hw.lcd.height "$HEIGHT"
fi
if [ -n "$DENSITY" ]; then set_prop hw.lcd.density "$DENSITY"; fi

echo "Created AVD '$AVD_NAME' in $AVD_DIR. Start it with: $(command_for start-emulator.sh) $AVD_NAME"
