# android-cli-dev-tools

Command-line tools for developing Android apps without Android Studio: create and boot an Android
TV emulator, drive it with a TV remote in the terminal, and work around the emulator's input
problems under WSLg. They work with any Linux setup and any Gradle project: they find the SDK,
AVDs and emulators on their own (see [Finding your setup](#finding-your-setup)).

| Script | Purpose |
|---|---|
| [`bin/create-avd.sh`](bin/create-avd.sh) | Creates an Android TV emulator with the right hardware settings: `tv_api25`, or another API level from 22 on with `--api` (e.g. `tv_api22`, `tv_api36`). |
| [`bin/start-emulator.sh`](bin/start-emulator.sh) | Boots it (cold boot, or Quick Boot with `--quick`), waits until Android is ready, applies the WSLg toolbar fix. |
| [`bin/remote.sh`](bin/remote.sh) | TV remote in the terminal (D-pad, OK, Back, Home, Menu, …). |
| [`bin/wslg-toolbar.py`](bin/wslg-toolbar.py) | Works around the emulator toolbar's input problems under WSLg. |

Each one prints its usage with `--help`. [`bin/README.md`](bin/README.md) describes them in detail.

## Getting started

1. Set up the SDK and the emulator: [`SETUP.md`](SETUP.md). Skip the parts you already have (an
   Android Studio SDK or AVD works as is).
2. Clone this repository anywhere, for example next to your projects.
3. Call the scripts by their path, from any folder:

   ```bash
   cd ~/my-app
   ../android-cli-dev-tools/bin/start-emulator.sh && ./gradlew installDebug
   ../android-cli-dev-tools/bin/remote.sh
   ```

The scripts never depend on the folder they're called from, except for one thing: they read
`sdk.dir` from the `local.properties` of the project you're in (the nearest one in the current
folder or above it), the way Gradle does.

### Optional: put the scripts on your `PATH`

To type `start-emulator.sh` instead of its path, either add `bin/` to your `PATH` (in `~/.bashrc`):

```bash
export PATH="$PATH:$HOME/android-cli-dev-tools/bin"
```

or symlink the scripts into a folder that's already on it:

```bash
ln -s ~/android-cli-dev-tools/bin/* ~/.local/bin/
```

Both work the same way: the scripts resolve symlinks to find the rest of this repository. `bin/`
holds only the commands, so nothing else lands on your `PATH`. Messages that suggest a command
(e.g. "Start one with: …") show its bare name when it's on your `PATH`, and otherwise the path
you called the script by, so they can be pasted as is.

## Finding your setup

Nothing about the SDK, AVD or device is hardcoded; each is taken from the first match below.

| What | Where from |
|---|---|
| Android SDK | `$ANDROID_HOME`, `$ANDROID_SDK_ROOT`, `sdk.dir` in the current project's `local.properties`, the SDK of the `adb` on `$PATH`, `~/Android/Sdk` |
| `adb`, `emulator`, `avdmanager`, `android` | inside that SDK (`cmdline-tools/latest`, else the newest `cmdline-tools/<version>`), else on `$PATH` |
| AVD folder | `$ANDROID_AVD_HOME`, `$ANDROID_EMULATOR_HOME/avd`, `$ANDROID_USER_HOME/avd`, `$ANDROID_SDK_HOME/.android/avd`, `~/.android/avd`; each AVD is located through its `<name>.ini` |
| AVD to create or boot | the name given on the command line, `$ADT_AVD`, `tv_api25` (`create-avd.sh --api <level>`: the name given, else `tv_api<level>`); `start-emulator.sh` then also accepts the only Android TV AVD |
| Emulator to talk to | `start-emulator.sh` matches running emulators by AVD name; `remote.sh` takes the serial given (any device), `$ANDROID_SERIAL`, or the only running emulator; `wslg-toolbar.py` the AVD given or the only emulator window |

To use your own TV AVD without typing its name each time: `export ADT_AVD=<name>`.

### Several Android versions

To test an app on more than one Android version, create one AVD per API level and boot the ones
you need; they run side by side, each on its own serial:

```bash
create-avd.sh --api 22 && create-avd.sh --api 36        # tv_api22 (Android 5.1), tv_api36 (Android 16)
for avd in tv_api22 tv_api25 tv_api36; do start-emulator.sh "$avd"; done
./gradlew connectedDebugAndroidTest                     # runs on every connected device
```

Each needs its system image first ([`SETUP.md`](SETUP.md), step 5). API 22 is the oldest Android
TV image the emulator can boot; 22, 25, 28, 30 and 36 are tested. Each emulator takes ~2 GB of
RAM. `remote.sh` needs the serial when several are running.

With several devices connected (another emulator, a phone, a TV over adb), `adb` refuses to guess
and `./gradlew installDebug` installs on all of them. `start-emulator.sh` prints the emulator's
serial; `export ANDROID_SERIAL=<serial>` makes both use only that one.

## Repository layout

| Path | Contents |
|---|---|
| [`bin/`](bin/README.md) | The scripts users run, and nothing else (it may be on a user's `PATH`). |
| [`lib/lib.sh`](lib/lib.sh) | Shared by the shell scripts (sourced, not run): finds the SDK, its tools and AVDs. |
| [`tests/`](tests/README.md) | Behavior tests for the scripts: `tests/run.py`. |
| [`SETUP.md`](SETUP.md) | Setting up the toolchain (WSL2, JDK, Android SDK, emulator). |
| [`AGENTS.md`](AGENTS.md) | Guidance for coding agents. |

Tests: `tests/run.py` (119 tests, no SDK or emulator needed); see
[`tests/README.md`](tests/README.md).
