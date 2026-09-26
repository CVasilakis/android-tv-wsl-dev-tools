# bin/

The scripts, and nothing else: users may put this folder on their `PATH`
(see [`../README.md`](../README.md#optional-put-the-scripts-on-your-path)), so helpers live in
[`../lib/`](../lib/lib.sh). Commands below are shown by name; without `PATH` set up, call them by
their path, e.g. `../android-cli-dev-tools/bin/start-emulator.sh`. Where they find the SDK, AVDs and
devices: [Finding your setup](../README.md#finding-your-setup).

| Script | Purpose |
|---|---|
| [`create-avd.sh`](create-avd.sh) | Creates an Android TV emulator with the right hardware settings (`tv_api25`, or `--api <level>`, or Google TV with `--google-tv`). |
| [`start-emulator.sh`](start-emulator.sh) | Boots it (cold boot, or Quick Boot with `--quick`), waits until Android is ready, applies the WSLg toolbar fix. |
| [`remote.sh`](remote.sh) | TV remote in the terminal (D-pad, OK, Back, Home, Menu, …). |
| [`wslg-toolbar.py`](wslg-toolbar.py) | Works around the emulator toolbar's input problems under WSLg. |

Each script has a header comment with usage, common errors and the reasons behind its less
obvious lines. Read it before changing a script, and run `tests/run.py` after changing one.
Every change needs a test in `tests/hermetic/test_<script>.py`; see [`../tests/README.md`](../tests/README.md).

## `create-avd.sh [--api <level>] [--google-tv] [--if-missing] [name]`

```bash
create-avd.sh                  # tv_api25: Android 7.1 (default name: $ADT_AVD, else tv_api25)
create-avd.sh --api 22         # tv_api22: Android 5.1, the oldest the emulator can boot
create-avd.sh --api 28         # tv_api28: Android 9
create-avd.sh --api 30         # tv_api30: Android 11
create-avd.sh --api 33         # tv_api33: Android 13
create-avd.sh --api 36         # tv_api36: Android 16
create-avd.sh --api 30 my_tv   # the same image under another name
create-avd.sh --google-tv --api 36   # gtv_api36: Google TV on Android 16
```

Creates the AVD from the Android TV image of that API level (default 25),
`system-images;android-<level>;android-tv;x86`, which must be installed first
([`../SETUP.md`](../SETUP.md), step 5; the script prints the install command if it's missing):

```bash
avdmanager create avd --name tv_api25 \
    --package "system-images;android-25;android-tv;x86" \
    --tag android-tv --abi x86 --device tv_1080p --sdcard 512M
```

and then sets these in the new AVD's `config.ini`, wherever avdmanager put it (by default
`~/.android/avd/tv_api25.avd/config.ini`; avdmanager has no flags for them):

| Setting | Value | Why |
|---|---|---|
| device | `tv_1080p` | 1920×1080 at 320 dpi = 960×540 dp, the resolution TV UIs are designed for |
| `--sdcard` | 512M | storage for test wallpapers/images (`adb push img.jpg /sdcard/Pictures/`) |
| `hw.keyboard` | yes | the PC keyboard acts as the remote in the emulator window |
| `hw.dPad` | yes | the device reports D-pad navigation, like a real TV |
| `hw.ramSize` | 2048 | enough for the TV images while leaving memory to Gradle and other emulators |
| `hw.cpu.ncore` | 4 | |
| `disk.dataPartition.size` | 4G | room for many test apps (the default is ~550 MB) |
| `hw.gpu.mode` | swiftshader_indirect | software rendering: works on any host, including WSLg |
| `hw.initialOrientation` | landscape | the tv_1080p profile defaults to portrait |
| `showDeviceFrame` | no | no device skin |

The TV profile has **no touchscreen** (`hw.screen=no-touch`), like a real TV.

With `--api`, the default name is `tv_api<level>` even when `$ADT_AVD` is set, so a second AVD
doesn't take the name of your everyday one. Any level from 22 on with an Android TV x86 image
works ([tested levels](../README.md#several-android-versions)). The images from API 29 on make
avdmanager print `Error: Could not load devices from …/android-30/android-tv/x86/devices.xml`:
that file is missing from the image, avdmanager uses its own `tv_1080p` profile instead, and the
AVD is fine. Once such an image is installed, avdmanager prints it for every AVD it creates.

**Google TV.** `--google-tv` uses the Google TV image of that level instead,
`system-images;android-<level>;google-tv;x86` (`--tag google-tv`), with the same settings; the
default name is `gtv_api<level>`. Google TV is Android TV with Google's home screen
(`com.google.android.apps.tv.launcherx`), which asks for a Google account. Its x86 images exist
for API 30, 31, 33, 34 and 36, all tested. There's no default level for it, so `--google-tv`
needs `--api`, and it refuses a level below 30.

**API 21 doesn't work.** Its Android TV image (Android 5.0) has only the old goldfish kernel,
`kernel-qemu`, and the emulator no longer has the engine that ran it: it boots only images with a
ranchu kernel (`kernel-ranchu`, or `kernel-ranchu-64` on newer images). `create-avd.sh --api 21`
says so instead of creating an AVD that can't start. API 22 is the oldest Android TV image with a
ranchu kernel.

It never overwrites an existing AVD: it fails instead (exit 1). To recreate one:
`avdmanager delete avd -n tv_api25 && create-avd.sh`. For scripts and CI jobs that cache their
AVDs, `create-avd.sh --if-missing` accepts an existing AVD made from the same system image and
leaves it as it is (exit 0); one of the same name from another image is still an error.

## `start-emulator.sh [--quick] [name] [emulator flags…]`

```bash
start-emulator.sh                          # cold boot the default AVD (see Finding your setup)
start-emulator.sh --quick                  # boot from its Quick Boot snapshot instead
start-emulator.sh -wipe-data               # factory reset (also re-enables a disabled stock launcher)
start-emulator.sh my_tv -gpu host          # another AVD, with hardware rendering
EMULATOR_TOOLBAR=show start-emulator.sh    # keep a clickable side toolbar (see below)
adb -s emulator-5554 emu kill              # stop it (saves a Quick Boot snapshot for --quick)
```

It runs `emulator -avd <name> -gpu swiftshader_indirect -no-snapshot-load -no-boot-anim -no-audio`
in the background (a `-gpu` flag of your own replaces the default), logs to `${TMPDIR:-/tmp}/emulator-<name>.log`,
finds the emulator's serial by asking each running emulator for its AVD name, waits until
`sys.boot_completed=1` (so it can be chained with `./gradlew installDebug`), and on WSL finishes
with `wslg-toolbar.py <name> hide`. If the emulator exits during boot, it stops waiting and prints
the end of the log.

**Already running.** An AVD that's already running isn't started again, but it may still be
booting, e.g. started by another call or CI step a moment ago. The script then waits for its boot
like for its own, with the same timeout, and prints its serial once Android is ready. It never
stops an emulator it didn't start: on a timeout it only fails, and if the emulator is stopped
meanwhile, it stops waiting.

**Output.** stdout holds the emulator's serial (e.g. `emulator-5554`) and nothing else; every
message goes to stderr. So scripts capture it without parsing messages:

```bash
serial="$(start-emulator.sh)" && export ANDROID_SERIAL="$serial"
```

**Without a display.** The emulator's window needs an X display: without `$DISPLAY` (a CI runner,
an SSH session) the emulator aborts, and its log doesn't say why. So there the script adds
`-no-window` and says so.

**Boot timeout.** If Android hasn't finished booting after `ADT_BOOT_TIMEOUT` seconds (default
900), the script stops the emulator it started, prints the end of the log and fails, so a stuck
boot can't keep it (or a CI job) waiting forever. Raise it on a slow host; `ADT_BOOT_TIMEOUT=0`
waits without limit.

**Cold boot or Quick Boot.** By default Android starts from scratch (`-no-snapshot-load`); newer
API levels take longer. `--quick` (anywhere on the command line) instead restores the snapshot the
emulator saved when it was last stopped, which is faster (except on API 22, where restoring it
is slower than a cold boot). A snapshot also restores `adbd`, the adb service inside Android, in
the middle of its old connection, and sometimes adb then lists the
emulator as `offline` and never gets through. So with `--quick`, if
the emulator stays `offline` for 30 s, the script runs `adb reconnect offline`, and if it's still
offline 30 s later it stops and says how to cold boot instead of waiting forever. A cold boot is
also `offline` until `adbd` starts, which is normal, so it's never cut short.

It checks KVM before starting anything, and distinguishes three cases: the device is missing
(virtualization is off), the device belongs to a group other than `kvm` (joining `kvm` can't
help, so it prints the `chgrp` fix), or the user is in the `kvm` group but this session predates
it — then it re-runs itself through `sg kvm` instead of asking for a new session.

## Controlling the TV

Clicking on the Android screen does nothing, because there's no touchscreen. Use the keyboard or the terminal remote:

| TV remote | Emulator window (default setup) | `remote.sh` in a terminal | adb |
|---|---|---|---|
| D-pad | Arrow keys | Arrow keys | `adb shell input keyevent DPAD_UP` (…DOWN/LEFT/RIGHT) |
| OK | Enter | Enter | `adb shell input keyevent DPAD_CENTER` |
| Back | Ctrl+Backspace | Esc or Backspace | `adb shell input keyevent BACK` |
| Home | Ctrl+H | h | `adb shell input keyevent HOME` |
| Menu | Ctrl+M | m | `adb shell input keyevent MENU` |
| Play/Pause, Volume | — | p, + / - | `adb shell input keyevent MEDIA_PLAY_PAUSE` |

Notes on the emulator window:
- Click the emulator screen once so it has keyboard focus.
- **Esc and F1 don't reach Android**: the emulator window consumes them. Use Ctrl+Backspace for Back and Ctrl+M for Menu.
- Holding Ctrl draws a ring of circles around the mouse pointer. That's the emulator's multi-touch
  simulator: cosmetic, it sends nothing to the TV and can't be turned off. `remote.sh` needs no Ctrl.

`remote.sh` sends each key with `adb shell input keyevent` (the adb column above), so it works
the same on every Android version, on emulators and physical devices, whatever window has focus.
Each key lags a little, because every call starts a process on the device; quick presses queue up and arrive in order. By default it picks the only running emulator,
ignoring physical devices; with several emulators, or for a physical TV, pass the serial
(`remote.sh emulator-5554`) or set `ANDROID_SERIAL`.

The emulator console (`adb emu event send`) would be much faster, but it only works on
emulators, and on the API 30 Android TV image its key events never arrive (the image has no
`goldfish_events` keyboard for the console to reach), so `remote.sh` doesn't use it.

## The side toolbar under WSLg (`wslg-toolbar.py`)

Under WSLg the emulator's side toolbar (power, volume, Back, Home, "⋯" → Extended controls) breaks input:

1. **Its buttons ignore clicks.** WSLg draws the toolbar next to the emulator but places its X11
   window at (-32768, -32768), where the mouse pointer can never go.
2. **It steals the keyboard.** While it's shown, keys typed into the emulator window move focus
   between the toolbar buttons instead of reaching Android.

You can't have both a clickable toolbar and a working keyboard, so `wslg-toolbar.py <avd> hide|show` picks one:

| Mode | Keyboard in emulator window | Toolbar |
|---|---|---|
| `hide` (default in `start-emulator.sh`) | works | hidden |
| `show` | goes to the toolbar (use `remote.sh`) | clickable, including "⋯" → Extended controls → *Directional pad* |

Switch at any time on a running emulator: `wslg-toolbar.py show`. Without an AVD
name it acts on the only emulator window; with several, pass the name: `wslg-toolbar.py tv_api25 show`.
Restarting the emulator undoes it. The script's docstring explains the mechanism, how to inspect the
windows (`xwininfo`, `xprop`) and which approaches don't work.

## Checking what the emulator is doing

```bash
adb shell dumpsys window | grep mCurrentFocus                       # activity in front
adb exec-out screencap -p > screen.png                             # screenshot
adb shell dumpsys input | sed -n '/RecentQueue/,/PendingEvent/p'   # last 10 input events (key codes up to API 28)
adb shell cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME   # API 24 on
```

Don't use `adb shell getevent > file` to check input: without a terminal its output is buffered and
the file stays empty. Simulated host input (xdotool/XTest) doesn't reach the emulator under WSLg, so
automated tests should send keys with `adb shell input keyevent`, like `remote.sh` does.

## Differences between API levels

The scripts work the same on every [tested level](../README.md#several-android-versions), Android
TV or Google TV; these are the differences in the images that you may run into:

| API | Difference |
|---|---|
| 22 | `--quick` is slower than a cold boot. The device has no `uname`. The stock launcher's HOME filter has no priority, so with another home app installed, Home opens the chooser. |
| 22–23 | No `cmd` on the device (`cmd package resolve-activity`, …); it exists from API 24 on. |
| 23, 29 | The first boot of a new AVD opens a "USB drive connected" screen (the AVD's SD card) in front of the launcher; later boots don't. |
| 23 | The AVD takes ~5 GB, twice the others: it keeps a copy of the image's `userdata.img`. |
| 23 on | The stock launcher's HOME filter has priority 2, so another home app only takes over while the stock one is disabled. |
| 26–29 | Two stock launchers: `tvlauncher` is the home screen; `leanbacklauncher` has no HOME filter. Cold boots of 26 and 27 are the slowest up to API 28. |
| 29 on | `dumpsys input` lists key events without key codes. avdmanager prints the harmless devices.xml error. An emulator takes 3–3.4 GB of RAM (1.8–2.5 GB up to API 28). |
| 30 | `remote.sh` keys lag the most; the emulator console's key events never arrive. |
| 34, 36 | The system image takes 8.2 GB on disk. |
| Google TV, all | The stock launcher is `com.google.android.apps.tv.launcherx`, with priority 2 like the others. Without a Google account it shows a sign-in screen instead of a home screen: "Add account" on 30–33, "Set up Google TV" on 34 and 36. RAM, disk use and the devices.xml error are as with the Android TV image of the same level. |
| Google TV, 30–34 | `tvlauncher` is installed too, without a HOME filter. |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Clicks on the Android screen do nothing | expected: no touchscreen, use the keys above |
| No side toolbar | hidden on purpose under WSL (see above) |
| Typed keys move focus between toolbar buttons | toolbar is shown: `wslg-toolbar.py hide` |
| Toolbar buttons ignore clicks | `wslg-toolbar.py show` |
| `wslg-toolbar: no toolbar window found` | wrong AVD name or emulator not running; inspect with `xwininfo -root -tree \| grep qemu-system` |
| `wslg-toolbar: no running emulator window found` | emulator not running, or started with `-no-window` |
| `start-emulator.sh: the emulator exited` | the printed log lines say why (e.g. an unknown flag); full log in `${TMPDIR:-/tmp}/emulator-<name>.log` |
| `start-emulator.sh: '<avd>' didn't finish booting within 900 s` | the host is slow: raise `ADT_BOOT_TIMEOUT`; or Android can't boot: without `--quick` if you used it, else try `-wipe-data` (factory reset) |
| `create-avd.sh: AVD '<name>' already exists` | it never overwrites one; `--if-missing` accepts it when it's from the same system image (see [`create-avd.sh`](#create-avdsh---api-level---google-tv---if-missing-name)) |
| `start-emulator.sh: … was stopped before it finished booting` | the AVD was already running and booting (another call started it), and was stopped while this one waited; start it again |
| `start-emulator.sh: … adb can't reach it` (with `--quick`) | the restored snapshot left adb offline: `adb -s <serial> emu kill`, then start it without `--quick` |
| `adb devices` shows `offline` for a running emulator | `adb reconnect offline`; if that doesn't help, `adb kill-server && adb start-server`; else stop the emulator and cold boot it |
| `can't tell which AVD to use` / `there's no AVD named …` | pass the AVD name or set `ADT_AVD`; the message lists the AVDs found |
| `'avdmanager' not found` (or `emulator`, `platform-tools`) | the SDK wasn't found, or lacks that package: set `ANDROID_HOME` (see [Finding your setup](../README.md#finding-your-setup)) |
| `adb: more than one device/emulator` | `export ANDROID_SERIAL=<serial>` (printed by `start-emulator.sh`) |
| `Error: Could not load devices from …/devices.xml` from `create-avd.sh` | harmless, the AVD is created correctly (see [`create-avd.sh`](#create-avdsh---api-level---google-tv---if-missing-name)) |
| `create-avd.sh: the Android TV image of API 21 has no ranchu kernel` | the emulator can't boot that image; use API 22 or newer (see [`create-avd.sh`](#create-avdsh---api-level---google-tv---if-missing-name)) |
| `This AVD's configuration is missing a kernel file! … "kernel-ranchu"` from the emulator | an AVD made from an image without a ranchu kernel (API 21), e.g. by avdmanager directly: it can't boot |
| `remote.sh` keys lag behind | expected: each key is an `adb shell input keyevent` call (see [Controlling the TV](#controlling-the-tv)) |
| Black emulator window | if started with `--quick`, start it without |
| `No access to /dev/kvm` | not in the `kvm` group, or the device belongs to another group: the message says which, see [`../SETUP.md`](../SETUP.md#make-devkvm-writable) |
| `error while loading shared libraries: libpulse.so.0` | `sudo apt-get install -y libpulse0` ([`../SETUP.md`](../SETUP.md), step 1) |
