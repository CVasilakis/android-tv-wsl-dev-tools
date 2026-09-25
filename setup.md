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
| platform-tools (adb) | 37.0.1 | `~/Android/Sdk/platform-tools` |
| SDK platform | the `compileSdk` of your project, e.g. android-36 | `~/Android/Sdk/platforms/android-36` |
| Build-tools | installed automatically by the Android Gradle Plugin on the first build | `~/Android/Sdk/build-tools` |
| Emulator | 37.1.11 | `~/Android/Sdk/emulator` |
| System image | `system-images;android-25;android-tv;x86` rev 16 (Android 7.1.1 TV) | `~/Android/Sdk/system-images/android-25/android-tv/x86` |
| AVD | `tv_api25` (tv_1080p) | `~/.android/avd/tv_api25.avd` |
| Gradle | per project, through its wrapper (`./gradlew`) | `~/.gradle` |

### Download sizes

| Item | Size |
|---|---|
| cmdline-tools zip | ~174 MB |
| JDK apt package | ~83 MB |
| platform-tools | ~9 MB |
| platforms;android-36 | ~66 MB |
| build-tools (automatic, first build) | ~64 MB |
| emulator | ~354 MB |
| Android TV API 25 x86 system image | ~700 MB zip, 3.1 GB unpacked (can be copied instead, see step 4) |
| A Gradle distribution (first `./gradlew` of a project) | ~150 MB |
| Gradle/Maven dependencies (AGP, Kotlin, …) | a few hundred MB, many small files |
| `xvfb`, `shellcheck` packages (optional, step 8) | a few MB |

## Prerequisites

- WSL2 with **WSLg** (Windows 11, or a recent Windows 10 build): `echo $DISPLAY` prints `:0`.
- Nested virtualization enabled for WSL2 (the default), so `/dev/kvm` exists: `ls -l /dev/kvm`.
- `unzip`, `curl` and `python3` 3.9+ (used by `bin/wslg-toolbar.py` and the tests).

## Steps

### 1. JDK and KVM access (needs sudo)

```bash
sudo apt-get install -y openjdk-21-jdk-headless
sudo usermod -aG kvm $USER
```

- A JRE alone (`openjdk-21-jre`) is **not** enough: the Android build needs `javac` and `jlink`.
- Current Android Gradle Plugin versions need JDK 17+; Robolectric tests on recent API levels
  need 21.
- The x86 emulator won't start without hardware acceleration, and `/dev/kvm` is only writable
  by root and the `kvm` group. The group applies to new sessions (`wsl --shutdown` from Windows,
  then reopen the terminal); `bin/start-emulator.sh` also works in an already-open session
  by re-running itself with `sg kvm`.
- `sudo` needs a password, so run these in a regular terminal. Tools that run commands without a
  terminal (such as an AI agent's shell) can't prompt for it.

Check: `javac -version` prints 21.

### 2. Command-line tools

Download `commandlinetools-linux-*_latest.zip` from <https://developer.android.com/studio#command-tools>.
The tools must live in `cmdline-tools/latest`, or `sdkmanager` can't work out the SDK root:

```bash
mkdir -p ~/Android/Sdk/cmdline-tools
unzip -q commandlinetools-linux-*_latest.zip -d ~/Android/Sdk/cmdline-tools/
mv ~/Android/Sdk/cmdline-tools/cmdline-tools ~/Android/Sdk/cmdline-tools/latest
```

### 3. Environment variables and licenses

Append this to `~/.bashrc`, then `source ~/.bashrc`:

```bash
# Android SDK
export ANDROID_HOME="$HOME/Android/Sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export JAVA_HOME="/usr/lib/jvm/java-21-openjdk-amd64"
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator"
```

To also call this repository's scripts by name, see
[`README.md`](README.md#optional-put-the-scripts-on-your-path).

Accept the SDK licenses. This also lets Gradle install build-tools on its own:

```bash
yes | sdkmanager --licenses
```

### 4. Android TV API 25 system image

**Option A: copy an existing one (no download).** A system image is a plain folder with a
`package.xml` inside, so a copy from another machine works as long as it keeps the SDK layout:

```bash
mkdir -p ~/Android/Sdk/system-images
cp -r /path/to/android-25 ~/Android/Sdk/system-images/   # must contain android-tv/x86/package.xml
sdkmanager --list_installed                               # must list system-images;android-25;android-tv;x86
```

The folder must contain `system.img`, `userdata.img`, `ramdisk.img`, `kernel-ranchu`,
`source.properties` and `package.xml`; `source.properties` must say `AndroidVersion.ApiLevel=25`,
`SystemImage.TagId=android-tv` and `SystemImage.Abi=x86`.

**Option B: download it (~700 MB):**

```bash
sdkmanager "system-images;android-25;android-tv;x86"
```

### 5. Remaining SDK packages

```bash
sdkmanager "platform-tools" "platforms;android-36" "emulator"   # android-36: your project's compileSdk
```

### 6. Emulator (AVD)

```bash
bin/create-avd.sh            # creates the AVD "tv_api25"
```

### 7. Use it from a project

From the project's folder (here cloned next to this repository):

```bash
./gradlew assembleDebug                                 # the first run downloads Gradle, AGP, Kotlin and build-tools
../android-cli-dev-tools/bin/start-emulator.sh          # boots tv_api25, returns when Android is ready
./gradlew installDebug
```

Typical timings: the first `assembleDebug` takes several minutes (mostly downloads), later builds
take seconds. A cold emulator boot takes ~20 s, a Quick Boot restart ~5 s.

`local.properties` (git-ignored in Android projects) is created by Gradle/IDEs, or can be created
by hand with `sdk.dir=/home/<user>/Android/Sdk`. It isn't needed while `ANDROID_HOME` is set.

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
  **`No access to /dev/kvm`**: not in the `kvm` group yet (step 1), or `/dev/kvm` doesn't exist
  (nested virtualization is off for WSL).
- **`sudo: a terminal is required to read the password`**: run the command in a regular terminal (step 1).
- **`Error: "emulator" package must be installed!`** from `create-avd.sh`: step 5 is missing.
- **`SDK location not found`** during a build: export `ANDROID_HOME` or create `local.properties` (step 7).
- **Build fails with an error about `javac` or `jlink`**: only a JRE is installed (step 1).
- **`adb devices` shows `unauthorized`/`offline`**: `adb kill-server && adb start-server`.
- **Emulator problems** (no toolbar, keys not arriving, clicks ignored, black window): see
  [`bin/README.md`](bin/README.md#troubleshooting).
- **`skipped …: Xvfb isn't installed`** or **`shellcheck isn't installed`** from `tests/run.py`:
  optional tools from step 8.
