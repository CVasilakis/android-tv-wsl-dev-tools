# Development environment setup (WSL2)

How to set up the toolchain for developing Android apps from the command line on WSL2 (Ubuntu
24.04), with an Android TV emulator. Android Studio is not needed. For using the emulator once it's
set up, see [`bin/README.md`](bin/README.md).

Locations below are defaults, not requirements. An existing SDK (e.g. Android Studio's) or AVD
works as-is: Gradle and the scripts find it through `ANDROID_HOME`, `local.properties` and the
other places listed in [`README.md`](README.md#finding-your-setup), so only the missing pieces
need installing. The commands that run a script assume you're in this repository.

## Components

| Component | Version | Location |
|---|---|---|
| JDK | OpenJDK 21 (`openjdk-21-jdk-headless`) | `/usr/lib/jvm/java-21-openjdk-amd64` |
| Android SDK root | — | `~/Android/Sdk` |
| cmdline-tools | 22.0 | `~/Android/Sdk/cmdline-tools/latest` |
| `android` CLI | downloaded by `cmdline-tools/latest/bin/android` on its first run | `~/.android/cli`, `~/.android/bin` |
| platform-tools (adb) | 37.0.1 | `~/Android/Sdk/platform-tools` |
| SDK platform | the `compileSdk` of your project, e.g. android-36 | `~/Android/Sdk/platforms/android-36` |
| Build-tools | 36.0.0, installed automatically by the Android Gradle Plugin on the first build | `~/Android/Sdk/build-tools` |
| Emulator | 37.1.11 | `~/Android/Sdk/emulator` |
| System image | `system-images;android-25;android-tv;x86` rev 16 (Android 7.1.1 TV) | `~/Android/Sdk/system-images/android-25/android-tv/x86` |
| AVD | `tv_api25` (tv_1080p) | `~/.android/avd/tv_api25.avd` |
| Gradle | per project, through its wrapper (`./gradlew`) | `~/.gradle` |

### Sizes

Downloads are what goes over the network; "on disk" is what the finished install occupies.

| Item | Download | On disk |
|---|---|---|
| cmdline-tools zip | ~174 MB | 175 MB (`cmdline-tools/`) |
| JDK apt package | ~83 MB | |
| platform-tools | ~9 MB | 22 MB |
| platforms;android-36 | ~66 MB | 146 MB |
| build-tools (automatic, first build) | ~64 MB | 147 MB |
| emulator | ~354 MB | 821 MB |
| Android TV API 25 x86 system image | ~700 MB | 3.1 GB (can be copied instead, see step 5) |
| `libpulse0` and the optional `xvfb`, `shellcheck` packages | a few MB | |
| the `android` CLI, on its first run | ~250 MB | ~250 MB (`~/.android`) |
| **The SDK once everything above is installed** | | **~4.4 GB** (`~/Android/Sdk`) |
| The `tv_api25` AVD, once booted and used for tests | | 1–2.5 GB (`~/.android/avd`), it grows with snapshots |
| A Gradle distribution (first `./gradlew` of a project) | ~150 MB | |
| Gradle/Maven dependencies of a project (AGP, Kotlin, test libraries, …) | a few hundred MB | ~1.1 GB (`~/.gradle`) |

Plan for **~8 GB** in `$HOME` for a full first-time setup with one project built and its emulator
booted.

## Prerequisites

- WSL2 with **WSLg** (Windows 11, or a recent Windows 10 build): `echo $DISPLAY` prints `:0`.
- Nested virtualization enabled for WSL2 (the default), so `/dev/kvm` exists: `ls -l /dev/kvm`.
- `unzip`, `curl` and `python3` 3.9+ (used by `bin/wslg-toolbar.py` and the tests).

## Steps

### 1. System packages and KVM access (needs sudo)

This is the whole sudo part of the setup; everything after it runs as your own user.

```bash
sudo apt-get install -y openjdk-21-jdk-headless libpulse0
sudo usermod -aG kvm $USER
```

- A JRE alone (`openjdk-21-jre`) is **not** enough: the Android build needs `javac` and `jlink`.
- Current Android Gradle Plugin versions need JDK 17+; Robolectric tests on recent API levels
  need 21.
- `libpulse0` is the emulator's only system library that isn't bundled with it. Without it the
  emulator fails before showing anything, even with `-no-audio`:
  `qemu-system-x86_64: error while loading shared libraries: libpulse.so.0`.
- `sudo` needs a password, so run these in a regular terminal. Tools that run commands without a
  terminal (such as an AI agent's shell) can't prompt for it.

Check: `javac -version` prints 21.

#### Make `/dev/kvm` writable

The x86 emulator won't start without hardware acceleration, and `/dev/kvm` is only writable by
root and by the group that owns it. Two separate things have to be true, so check the device
rather than assuming `usermod` was enough:

```bash
ls -ln /dev/kvm            # the 4th column is the group id that owns the device
getent group kvm           # kvm:x:<gid>:<members>
```

- **The gids match** and you're a member: you're done for new sessions. The new group only
  applies to sessions started after `usermod` (on WSL: after `wsl --shutdown` from Windows, then
  reopening the terminal). `bin/start-emulator.sh` also works in an already-open session by
  re-running itself with `sg kvm`.
- **The gids differ**: the device belongs to a group that isn't `kvm`, so being in `kvm` grants
  nothing. This happens on WSL, where `/dev/kvm` is created before udev applies
  `50-udev-default.rules`. Hand the device to the `kvm` group, and keep that across restarts:

  ```bash
  sudo chgrp kvm /dev/kvm && sudo chmod 660 /dev/kvm
  echo 'z /dev/kvm 0660 root kvm -' | sudo tee /etc/tmpfiles.d/kvm.conf
  ```

`bin/start-emulator.sh` tells the two cases apart and prints the fix for the one you have.

Check: `test -w /dev/kvm && echo ok` in a session started after `usermod` (or under `sg kvm`).

### 2. Command-line tools

Download `commandlinetools-linux-*_latest.zip` from <https://developer.android.com/studio#command-tools>.
The tools must live in `cmdline-tools/latest`, or they can't work out which SDK they belong to.
Run this from the folder you downloaded the zip into:

```bash
mkdir -p ~/Android/Sdk/cmdline-tools
unzip -q commandlinetools-linux-*_latest.zip -d ~/Android/Sdk/cmdline-tools/
mv ~/Android/Sdk/cmdline-tools/cmdline-tools ~/Android/Sdk/cmdline-tools/latest
```

The zip unpacks to a folder named `cmdline-tools`, which is why the `mv` renames it rather than
moving it into place. Unpacked this way the package has no `package.xml`, so
`android sdk list --no-metrics` shows its version as `unknown`; that's harmless, everything still
works.

### 3. Environment variables and the `android` CLI

Put this in `~/.bashrc`, then `source ~/.bashrc`:

```bash
# Android SDK
export ANDROID_HOME="$HOME/Android/Sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export JAVA_HOME="/usr/lib/jvm/java-21-openjdk-amd64"
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator"
```

**Put it above the interactive check, not at the end of the file.** Ubuntu's stock `~/.bashrc`
starts with

```bash
case $- in
    *i*) ;;
      *) return;;
esac
```

so anything below it is skipped by non-interactive shells — build scripts, CI, and the shells AI
agents run commands in. Appending the block to the end of the file works when you type commands
yourself and then fails with `SDK location not found` from `bash -lc './gradlew …'`. Check both:

```bash
bash -lic 'echo $ANDROID_HOME'    # interactive
bash -lc  'echo $ANDROID_HOME'    # non-interactive: must print the same path
```

A shell that is neither interactive nor a login shell (`bash -c …`) reads no startup file at all,
so give those the variable explicitly or rely on the project's `local.properties` (step 7).

To also call this repository's scripts by name, see
[`README.md`](README.md#optional-put-the-scripts-on-your-path).

#### The `android` CLI

cmdline-tools 22.0 deprecates `sdkmanager`: running it prints a warning pointing at the `android`
CLI, whose `android sdk` subcommand replaces it. The steps below use `android`:

| Task | `android` | deprecated `sdkmanager` |
|---|---|---|
| install packages | `android sdk install <pkg>…` | `sdkmanager "<pkg>"…` |
| list what's installed | `android sdk list` | `sdkmanager --list_installed` |
| list everything available | `android sdk list --all` | `sdkmanager --list` |
| update packages | `android sdk update` | `sdkmanager --update` |
| remove a package | `android sdk remove <pkg>` | `sdkmanager --uninstall "<pkg>"` |
| accept licenses up front | — (see below) | `yes \| sdkmanager --licenses` |

Every `android` command in this document carries `--no-metrics`; see [Telemetry](#telemetry).

Notes before you use it:

- **The first run downloads the CLI itself** (~250 MB into `~/.android`), so it takes a while and
  needs network even when every package is already installed.
- **Package ids take `/` or `;`**: `android sdk list` prints `platforms/android-36`, and both
  `android sdk install "platforms;android-36"` and `.../android-36` work. This document keeps the
  `;` form, which is also what Gradle error messages and `package.xml` use.
- **It reports telemetry by default**, and the only way to turn that off is per call: see
  [Telemetry](#telemetry).
- **It exits 0 when a package doesn't exist**, printing only `Package … not found.` on stdout, so
  `android sdk install <typo> && …` carries on as if it had worked. Check with
  `android sdk list --no-metrics` rather than trusting the exit status.

#### Telemetry

The `android` CLI reports usage to Google unless told not to. Its first run prints a notice saying
it collects "commands, sub-commands, and flags used", and names `--no-metrics` as the way to
disable it. What that means in practice:

- **Each invocation writes one event** to `~/.android/cli/analytics/metrics/spool/*.bproto`, and a
  separate `upload-metrics` process sends spooled events to `https://play.google.com/log` on an
  interval. Read one with `strings`; a plain `android sdk list` records:

  ```
  android2  --sdk  <redacted>2  sdk2  list:  CLAUDECODE:  CLAUDE_CODE_ENTRYPOINT
  ```

  the command, the subcommand, the flag names, and **the names of environment variables that are
  set** (not their values; argument values are written as a literal `<redacted>`).
- **`--no-metrics` writes nothing at all**: no spool file appears. The flag works anywhere on the
  command line, and per its own help text disables "metrics/crash reports collection", so it
  covers crash reports too.
- **`~/.android/analytics.settings` doesn't stop it.** That file is the older SDK-wide opt-out
  (`"hasOptedIn":false`); events are spooled regardless of it. It may still gate the upload, but
  the recording on disk happens either way.
- **There is no persistent opt-out** — no config file, no subcommand, no environment variable.
  (`ANDROID_CLI_ANALYTICS_URL` and `ANDROID_CLI_CRASH_URL` only change where reports are sent.)
  So the flag has to be on every call, which is why every `android` command here carries it.

To opt in, drop `--no-metrics`. To discard what was collected before you opted out:

```bash
rm -f ~/.android/cli/analytics/metrics/spool/*.bproto
```

There is no `android` equivalent of `sdkmanager --licenses`, and you don't need one:
`android sdk install` accepts the license of each package it installs, writing it to
`$ANDROID_HOME/licenses/`. Every package in this setup is under `android-sdk-license`, so after
step 4 that file exists and the Android Gradle Plugin can install build-tools by itself. A package
under a different license (some Google add-ons) accepts its own on first install.

### 4. SDK packages

```bash
android sdk install --no-metrics "platform-tools" "platforms;android-36" "emulator"
```

(`android-36` is your project's `compileSdk`. On `--no-metrics`, see
[Telemetry](#telemetry) — drop it to let the CLI report your usage.)

Check: `android sdk list --no-metrics` lists `emulator`, `platform-tools` and
`platforms/android-36`. Check it rather than the exit status, which is 0 even for a package that
doesn't exist.

### 5. Android TV API 25 system image

**Option A: copy an existing one (no download).** A system image is a plain folder with a
`package.xml` inside, so a copy from another machine works as long as it keeps the SDK layout:

```bash
mkdir -p ~/Android/Sdk/system-images
cp -r /path/to/android-25 ~/Android/Sdk/system-images/   # must contain android-tv/x86/package.xml
android sdk list --no-metrics                             # must list system-images/android-25/android-tv/x86
```

The folder must contain `system.img`, `userdata.img`, `ramdisk.img`, `kernel-ranchu`,
`source.properties` and `package.xml`; `source.properties` must say `AndroidVersion.ApiLevel=25`,
`SystemImage.TagId=android-tv` and `SystemImage.Abi=x86`.

**Option B: download it (~700 MB, 3.1 GB on disk):**

```bash
android sdk install --no-metrics "system-images;android-25;android-tv;x86"
```

### 6. Emulator (AVD)

```bash
bin/create-avd.sh            # creates the AVD "tv_api25"
```

`create-avd.sh` only writes files, so it works without KVM access; booting the AVD (step 7) is the
first thing that needs it.

### 7. Use it from a project

From the project's folder (here cloned next to this repository):

```bash
./gradlew assembleDebug                                 # the first run downloads Gradle, AGP, Kotlin and build-tools
../android-cli-dev-tools/bin/start-emulator.sh          # boots tv_api25, returns when Android is ready
./gradlew installDebug
```

Typical timings: the first `assembleDebug` takes several minutes (mostly downloads), later builds
take seconds. A cold emulator boot takes ~20 s, a Quick Boot restart ~5 s.

`local.properties` (git-ignored in Android projects) is created by Gradle/IDEs, or by hand:

```bash
echo "sdk.dir=$HOME/Android/Sdk" > local.properties
```

It's redundant whenever `ANDROID_HOME` reaches the build, but it costs nothing and it's the only
thing that works in a shell that reads no startup file (step 3), so it's worth creating once per
project.

### 8. Test tools (only to work on this repository)

The tests of the scripts need only Python 3.9+. Two optional tools enable more of them:

| Tool | For | Install |
|---|---|---|
| Xvfb, a virtual X server | the `wslg-toolbar.py` tests (they skip without it) | `sudo apt-get install -y xvfb` / `sudo dnf install -y xorg-x11-server-Xvfb` |
| shellcheck | static analysis of the shell scripts (skips without it) | `sudo apt-get install -y shellcheck` / `sudo dnf install -y ShellCheck` |

```bash
tests/run.py                   # ~100 tests; prints what it skipped and why
```

## Troubleshooting

- **`emulator: ERROR: x86 emulation currently requires hardware acceleration`** or
  **`No access to /dev/kvm`**: not in the `kvm` group yet, or the device belongs to another
  group, or `/dev/kvm` doesn't exist (nested virtualization is off for WSL). See
  [Make `/dev/kvm` writable](#make-devkvm-writable).
- **`error while loading shared libraries: libpulse.so.0`** when the emulator starts:
  `sudo apt-get install -y libpulse0` (step 1).
- **`sudo: a terminal is required to read the password`**: run the command in a regular terminal (step 1).
- **`Error: "emulator" package must be installed!`** from `create-avd.sh`: step 4 is missing.
- **`Package … not found.`** from `android sdk install`, and the setup carries on regardless: a
  typo in a package id. The command exits 0 anyway, so check with `android sdk list --no-metrics`
  ([The `android` CLI](#the-android-cli)).
- **`WARNING: The SDK Manager CLI tool (sdkmanager) is deprecated`**: use `android sdk` instead;
  the table in [The `android` CLI](#the-android-cli) maps the old commands to the new ones.
- **`SDK location not found`** during a build, although `echo $ANDROID_HOME` works in your
  terminal: the build ran in a non-interactive shell that skipped the exports at the end of
  `~/.bashrc` (step 3), or in one that reads no startup file. Move the block above the
  interactive check, and create `local.properties` (step 7).
- **Build fails with an error about `javac` or `jlink`**: only a JRE is installed (step 1).
- **`adb devices` shows `unauthorized`/`offline`**: `adb kill-server && adb start-server`.
- **Emulator problems** (no toolbar, keys not arriving, clicks ignored, black window): see
  [`bin/README.md`](bin/README.md#troubleshooting).
- **`skipped …: Xvfb isn't installed`** or **`shellcheck isn't installed`** from `tests/run.py`:
  optional tools from step 8.
