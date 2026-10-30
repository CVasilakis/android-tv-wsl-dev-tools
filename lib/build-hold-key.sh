#!/usr/bin/env bash
# Builds HoldKey.dex from HoldKey.java: the program remote.sh --long-press runs on a device before
# API 30. Run it after changing HoldKey.java, and commit both. Needs a JDK's javac and, from the
# Android SDK (found as the other scripts find it), build-tools' d8 and a platform's android.jar.
set -euo pipefail
LIB_DIR="$(dirname "$(readlink -f "$0")")"
# shellcheck source=lib.sh
. "$LIB_DIR/lib.sh"

SDK="$(find_sdk)" || die "no Android SDK found (see bin/README.md, Finding your setup)"
d8="$(ls -d "$SDK"/build-tools/*/d8 2>/dev/null | sort -V | tail -n 1)"
jar="$(ls -d "$SDK"/platforms/android-*/android.jar 2>/dev/null | sort -V | tail -n 1)"
[ -n "$d8" ] || die "no d8 under $SDK/build-tools: build something with Gradle once, or install build-tools"
[ -n "$jar" ] || die "no android.jar under $SDK/platforms: install a platform"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
javac --release 8 -cp "$jar" -d "$work" "$LIB_DIR/HoldKey.java"
"$d8" --min-api 21 --output "$work" "$work"/HoldKey*.class
cp "$work/classes.dex" "$LIB_DIR/HoldKey.dex"
echo "Built $LIB_DIR/HoldKey.dex ($(wc -c < "$LIB_DIR/HoldKey.dex") bytes)"
