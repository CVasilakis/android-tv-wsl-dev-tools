# bin/

The scripts, and nothing else: users may put this folder on their `PATH`
(see [`../README.md`](../README.md#optional-put-the-scripts-on-your-path)), so helpers live in
[`../lib/`](../lib/lib.sh). Commands below are shown by name; without `PATH` set up, call them by
their path, e.g. `../android-tv-wsl-dev-tools/bin/start-emulator.sh`.

| Script | Purpose |
|---|---|
| [`create-avd.sh`](create-avd.sh) | Creates an Android TV emulator with the right hardware settings (`tv_api25`, or `--api <level>`, or Google TV with `--google-tv`; another screen with `--size`/`--density`). |
| [`start-emulator.sh`](start-emulator.sh) | Boots it (cold boot, or Quick Boot with `--quick`), waits until Android is ready (and with `--wait-for-home` until its home app has settled in front), applies the WSLg toolbar fix. |
| [`stop-emulator.sh`](stop-emulator.sh) | Has Android save its recent changes, stops it and returns once it has exited, with a time limit (one emulator, or `--all`). |
| [`remote.sh`](remote.sh) | TV remote in the terminal (D-pad, OK, Back, Home, Menu, …); holds a key as a long press on any API level (`--long-press`). |
| [`wslg-toolbar.py`](wslg-toolbar.py) | Works around the emulator toolbar's input problems under WSLg. |

Each one prints its usage with `--help`, and has a header comment with its usage, common errors
and the reasons behind its less obvious lines.

## Finding your setup

Nothing about the SDK, AVD or device is hardcoded; each is taken from the first match below.

| What | Where from |
|---|---|
| Android SDK | `$ANDROID_HOME`, `$ANDROID_SDK_ROOT`, `sdk.dir` in the `local.properties` of the project you're in (the nearest one in the current folder or above it, as Gradle does), the SDK of the `adb` on `$PATH`, `~/Android/Sdk` |
| `adb`, `emulator`, `avdmanager`, `android` | inside that SDK (`cmdline-tools/latest`, else the newest `cmdline-tools/<version>`), else on `$PATH` |
| AVD folder | `$ANDROID_AVD_HOME`, `$ANDROID_EMULATOR_HOME/avd`, `$ANDROID_USER_HOME/avd`, `$ANDROID_SDK_HOME/.android/avd`, `~/.android/avd`; each AVD is located through its `<name>.ini` |
| AVD to create or boot | the name given on the command line, `$ADT_AVD`, `tv_api25` (`create-avd.sh --api <level>`: the name given, else `tv_api<level>`, or `gtv_api<level>` with `--google-tv`); `start-emulator.sh` then also accepts the only Android TV or Google TV AVD |
| Emulator to talk to | `start-emulator.sh` matches running emulators by AVD name; `remote.sh` takes the serial given (any device), `$ANDROID_SERIAL`, or the only running emulator; `stop-emulator.sh` the AVD name or serial given, `$ANDROID_SERIAL`, or the only running emulator (never a physical device); `wslg-toolbar.py` the AVD given or the only emulator window |

`local.properties` is the only thing the scripts read from the folder they're called from. To use
your own TV AVD without typing its name each time: `export ADT_AVD=<name>`.

## `create-avd.sh`

`create-avd.sh [--api <level>] [--google-tv] [--size <W>x<H>] [--density <dpi>] [--if-missing] [name]`

```bash
create-avd.sh                  # tv_api25: Android 7.1 (default name: $ADT_AVD, else tv_api25)
create-avd.sh --api 22         # tv_api22: Android 5.1, the oldest the emulator can boot
create-avd.sh --api 28         # tv_api28: Android 9
create-avd.sh --api 30         # tv_api30: Android 11
create-avd.sh --api 33         # tv_api33: Android 13
create-avd.sh --api 36         # tv_api36: Android 16
create-avd.sh --api 30 my_tv   # the same image under another name
create-avd.sh --google-tv --api 36   # gtv_api36: Google TV on Android 16
create-avd.sh --size 1280x720 --density 213 tv_720p   # a 720p screen instead of 1080p
```

Creates the AVD from the Android TV image of that API level (default 25),
`system-images;android-<level>;android-tv;x86`, which must be installed first
([`../SETUP.md`](../SETUP.md), step 5; the script prints the install command if it's missing):

```bash
avdmanager create avd --name tv_api25 \
    --package "system-images;android-25;android-tv;x86" \
    --tag android-tv --abi x86 --device tv_1080p --sdcard 512M
```

and then sets these in the new AVD's `config.ini` (by default
`~/.android/avd/tv_api25.avd/config.ini`; avdmanager has no flags for them):

| Setting | Value | Why |
|---|---|---|
| device | `tv_1080p` | 1920×1080 at 320 dpi = 960×540 dp, the resolution TV UIs are designed for |
| `--sdcard` | 512M | API 22's `/sdcard`, for test wallpapers/images (`adb push img.jpg /sdcard/Pictures/`). From API 23 on, `/sdcard` is internal storage and the SD card a removable volume, which on API 23 and 29 opens a screen on the first boot ([Differences between API levels](#differences-between-api-levels)) |
| `hw.keyboard` | yes | the PC keyboard acts as the remote in the emulator window |
| `hw.dPad` | yes | the device reports D-pad navigation, like a real TV |
| `hw.ramSize` | 2048 | enough for the TV images while leaving memory to Gradle and other emulators |
| `hw.cpu.ncore` | 4 | |
| `disk.dataPartition.size` | 4G | room for many test apps (the default is ~550 MB) |
| `hw.gpu.mode` | swiftshader_indirect | software rendering: works on any host, including WSLg |
| `hw.initialOrientation` | landscape | the tv_1080p profile defaults to portrait |
| `showDeviceFrame` | no | no device skin |

The TV profile has **no touchscreen** (`hw.screen=no-touch`), like a real TV.

`--size <W>x<H>` and `--density <dpi>` replace the profile's screen (`hw.lcd.width`,
`hw.lcd.height`, `hw.lcd.density`); each one given alone keeps the profile's value for the other.
The size is landscape, width first: a taller one is refused, because Android TV would rotate its
picture onto a portrait panel and the window would show it sideways. See
[Other screen sizes and densities](#other-screen-sizes-and-densities) for common values and the
ways that need no extra AVD.

With `--api`, the default name is `tv_api<level>` even when `$ADT_AVD` is set, so a second AVD
doesn't take the name of your everyday one. Any level from 22 on with an Android TV x86 image
works ([tested levels](../README.md#scope)). The images from API 29 on make
avdmanager print `Error: Could not load devices from …/android-30/android-tv/x86/devices.xml`:
that file is missing from the image, avdmanager uses its own `tv_1080p` profile instead, and the
AVD is fine. Once such an image is installed, avdmanager prints it for every AVD it creates.

**Google TV.** `--google-tv` uses the Google TV image of that level instead,
`system-images;android-<level>;google-tv;x86` (`--tag google-tv`), with the same settings; the
default name is `gtv_api<level>`. There's no Google TV image below API 30 and no default level
for it, so `--google-tv` needs `--api`, and it refuses a level below 30.

**API 21 doesn't work.** Its Android TV image (Android 5.0) has only the old goldfish kernel,
`kernel-qemu`, and the emulator no longer has the engine that ran it: it boots only images with a
ranchu kernel (`kernel-ranchu`, or `kernel-ranchu-64` on newer images). `create-avd.sh --api 21`
says so instead of creating an AVD that can't start. API 22 is the oldest Android TV image with a
ranchu kernel.

**Where the AVD goes.** In the folder the emulator looks in, the first of the AVD folders in
[Finding your setup](#finding-your-setup). The script creates that folder and passes it to
avdmanager as `ANDROID_AVD_HOME`, because avdmanager left to itself can pick another one:
cmdline-tools 12.0, the version on GitHub's runners, uses `$XDG_CONFIG_HOME/.android/avd` when
`XDG_CONFIG_HOME` is set, and the emulator never finds an AVD there.

It never overwrites an existing AVD: it fails instead (exit 1). To recreate one:
`avdmanager delete avd -n tv_api25 && create-avd.sh`. For scripts and CI jobs that cache their
AVDs, `create-avd.sh --if-missing` accepts an existing AVD made from the same system image and
leaves it as it is (exit 0); one of the same name from another image, or with another `--size`
or `--density` than the ones given, is still an error. Without `--size` or `--density`, any
existing screen is accepted.

## Other screen sizes and densities

Every AVD `create-avd.sh` makes has a 1920×1080 screen at 320 dpi (960×540 dp) unless told
otherwise. To see how an app looks on other TVs there are three ways, from quickest to most
permanent. Common TV screens:

| Screen | Size | Density | dp |
|---|---|---|---|
| 720p | 1280x720 | 213 (tvdpi) | 960×540 |
| 1080p (default) | 1920x1080 | 320 (xhdpi) | 960×540 |
| 4K | 3840x2160 | 640 (xxxhdpi) | 960×540 |

Most TVs report 960×540 dp whatever their resolution, so the same layout is only scaled. A
density that doesn't match the size (e.g. 1920x1080 at 213 = 1440×810 dp) is how to check
that a layout copes with more or less room.

**On a running emulator, `adb shell wm`.** No new AVD and no reboot: Android redraws at once,
though an app that's already open may need a restart to pick it up.

```bash
adb shell wm size 1280x720 && adb shell wm density 213   # pretend to be a 720p TV
adb shell wm size; adb shell wm density                  # "Physical" and, while set, "Override"
adb shell wm size reset && adb shell wm density reset    # back to the AVD's own screen
```

The override stays across reboots until it's reset (a `-wipe-data` also clears it). Android
accepts at most twice the physical size in each direction, so 4K works on the default 1080p AVD
but not on a 720p one (asking for 3840x2160 there gives 2560x1440). It changes what apps see,
not the emulator window, which keeps its own size. With several emulators, add `-s <serial>`
after `adb`.

**At boot, `start-emulator.sh … -skin <W>x<H>`.** Extra flags go to the emulator, and `-skin`
sets the screen's physical size for that run only; the AVD keeps its own:

```bash
start-emulator.sh tv_api25 -skin 1280x720
```

It sets no density: the AVD's stays (320 by default, so 1280x720 would be 640×360 dp); follow it
with `adb shell wm density 213`.

**A separate AVD, `create-avd.sh --size --density`.** For a screen you test on often, or that
is more than twice the size of an existing AVD, which `wm` can't do. Give it a name so
it doesn't take the default one:

```bash
create-avd.sh --api 30 --size 1280x720 --density 213 tv30_720p
start-emulator.sh tv30_720p
```

## `start-emulator.sh`

`start-emulator.sh [--quick] [--wait-for-home] [name] [emulator flags…]`

```bash
start-emulator.sh                          # cold boot the default AVD (see Finding your setup)
start-emulator.sh --quick                  # boot from its Quick Boot snapshot instead
start-emulator.sh --wait-for-home          # also wait until the home app has settled (tests, CI)
start-emulator.sh -wipe-data               # factory reset (also re-enables a disabled stock launcher)
start-emulator.sh my_tv -gpu host          # another AVD, with hardware rendering
EMULATOR_TOOLBAR=show start-emulator.sh    # keep a clickable side toolbar (see below)
stop-emulator.sh                           # save recent changes, stop it (and a Quick Boot snapshot for --quick)
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

**Waiting for the home app (`--wait-for-home`).** `sys.boot_completed=1` comes before the device
has settled. From API 24 on, until the user is unlocked, Settings' `FallbackHome` holds the screen
and a HOME intent resolves to it; the home app comes to the front only then, which can be after
`sys.boot_completed`. And on a host short of CPU, no window had the focus long after the boot, so
no key reached any app: an instrumented test's first key, which waits for a focused
window, then fails. With `--wait-for-home`, once Android has booted, the script also waits until
the home app's screen is in front, has finished starting and has the focus:

- the activity at the top (`dumpsys activity activities`, which lists them from the top down) is
  in the home app's task. From API 24 on, that's the task of the activity a HOME intent resolves
  to (`cmd package resolve-activity`), once that's no longer `FallbackHome`. API 22 and 23 have no
  `cmd` and no `FallbackHome`: there it's a task a HOME intent started (the `intent=` of its
  `TaskRecord`);
- that activity is resumed and idle (`state=RESUMED`, `idle=true`): Android's own mark that it
  has finished starting, once its main thread has gone idle after the resume;
- it's the focused activity (`dumpsys window`'s `mFocusedApp`), and its own window has the focus
  (`mCurrentFocus`), so keys go to it;
- the same activity and window show at two looks in a row, a second apart, so a look doesn't
  catch a state that's gone a moment later.

A screen the home app opens over itself, in its task, counts once it's on top: Google TV's
sign-in screen without a Google account, and on API 22 with a second home app installed, Android's
home chooser (package `android`). The script says which package it found. Google TV's launcher on
API 30–33 first shows a screen of its own after a boot, in a task of its own, and then brings its
home task over it ([Differences between API levels](#differences-between-api-levels)). The script
waits past it, so there `--wait-for-home` takes longer than on other images, minutes on a slow
host. What no look can tell is a screen the home app decides to open later by
itself: after that first screen, Google TV's launcher can show its home screen, settled, for a
while before it opens its sign-in screen over it.

Another screen can keep the focus: on API 23 and 29 the first boot of a new AVD, which is every
boot in CI, opens "USB drive connected" in front of the home app, and it stays until Back. So when
a window of another app has kept the focus for 10 s, once the user is unlocked, the script presses
Back, twice at most, never on a screen of the home app's own, such as Google TV's first screen,
where a Back could end what the launcher is doing. It does so only on an emulator whose boot it
waited for (one it started, or one that was still booting); on one that had booted before, which
someone may be using, it only looks, and fails if an app stays in front: press Home first, or
leave out the flag. It never starts an app. With `--quick`, the restored device has usually
settled already, so the wait is short.

It waits at most `ADT_HOME_TIMEOUT` seconds (default 300; `0`: no limit), counted from when
Android has booted, on top of `ADT_BOOT_TIMEOUT`. When time's up it fails, naming what was in
front (the focused activity, the focused window, what a HOME intent resolves to, and the top
activity with its task, state and idle mark), and stops the emulator it started, as after a boot
timeout; one that was already running is left running.

**Home on API 26 and 27.** On the Android TV images of API 26 and 27 (Android 8.0 and 8.1), the
Home key never leaves an app until the TV setup wizard has set `tv_user_setup_complete`, and
these images never run that wizard (logcat: "Not starting activity because user setup is in
progress"). So once the emulator has booted, the script sets it, and says so, unless it's
already set. Then it waits until Android has saved it, so that stopping the emulator right away
doesn't lose it (see below): it reads Android's settings file as root (these images are debug
builds, with `su`) until the file holds the setting and Android's backup of the old file is gone,
then runs `sync`. That takes under a second; if it hasn't happened within 30 s, or the script
can't look, it warns and goes on. The setting stays in the AVD's data, so this happens once per
AVD. Other images are left as they are; on those, Home works without it. By hand:
`adb shell settings put secure tv_user_setup_complete 1`.

**Stopping and recent changes.** `adb emu kill` stops the emulator without shutting Android
down, and Android saves a change only a while after it's made, so a change made just before is
lost: the next cold boot starts as if the command had never run. Measured on these emulators:

| What | When it's safe from a kill |
|---|---|
| An app's enabled state (`pm enable`, `pm disable-user`) | Android writes it 10 s after the first unsaved change (10.35 s measured; on an emulator starved of CPU, 10 s after the command returned) |
| A setting (`settings put`) | Android writes it about 0.2 s later (0.34 s at most on a starved emulator) |
| Both on API 22 | written at once |
| Then, every file | up to 5 s more: Android keeps the old file as a backup until the new one is complete, and until the filesystem's journal has recorded that (every 5 s), a boot reads the backup |

So a change is safe 15 s after it at the latest. **Wait 30 s before stopping the emulator after
a change that should stay**: twice that. `adb reboot` and `adb shell reboot -p` don't save it
either. [`stop-emulator.sh`](#stop-emulatorsh) needs no wait: it has Android save first.

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
waits without limit. While it waits, it prints a line every minute on stderr, e.g.
`'tv_api25' is still booting (120 s so far; the limit is 900 s)...`, so a slow boot doesn't look
like a hang in a CI log. Each adb call that checks the boot gets 15 s, because on an emulator
that's half booted or short of CPU, adb can hang without answering; a call that runs out of time
counts as "not booted yet", so the limit still applies.

**Cold boot or Quick Boot.** By default Android starts from scratch (`-no-snapshot-load`); newer
API levels take longer. `--quick` (anywhere on the command line) instead restores the snapshot the
emulator saved when it was last stopped, which is faster (except on API 22, where restoring it
is slower than a cold boot). A snapshot also restores `adbd`, the adb service inside Android, in
the middle of its old connection, and sometimes adb then lists the
emulator as `offline` and never gets through. So with `--quick`, if
the emulator stays `offline` for 30 s, the script runs `adb reconnect offline`, and if it's still
offline 30 s later it stops and says how to cold boot instead of waiting forever. A cold boot is
also `offline` until `adbd` starts, which is normal, so it's never cut short.

**KVM.** It checks access to `/dev/kvm` before starting anything, and prints the fix for the
problem it finds ([`../SETUP.md`](../SETUP.md#make-devkvm-writable)). When you're in the `kvm`
group but this session predates it, it re-runs itself through `sg kvm` instead.

## `stop-emulator.sh`

`stop-emulator.sh [--all] [name|serial]`

```bash
stop-emulator.sh                   # $ANDROID_SERIAL, else the only running emulator
stop-emulator.sh tv_api25          # by AVD name
stop-emulator.sh emulator-5556     # by serial
stop-emulator.sh --all             # every running emulator
```

It runs `adb -s <serial> emu kill` and returns once the emulator's process has exited, so the
same AVD can be started again right away. It exits 0 also when the emulator wasn't running, so
it can be called just in case; it never stops a physical device, and a name that's neither a
running emulator nor an AVD is an error, so a typo doesn't pass as "not running". Messages go to
stderr; stdout stays empty.

**Saving first.** Before it stops the emulator, it has Android save the changes it hasn't saved
yet ([why](#start-emulatorsh), "Stopping and recent changes"), then commits them to the disk
image with `sync`:

| API level | How it has Android save | Time |
|---|---|---|
| 22 to 31 | `adb shell dumpsys package write` writes the app states at once ("Settings written."); a 1 s pause lets a setting changed just before be written | about 1 s |
| 33 and newer | that command no longer writes (it prints the whole package dump), so it waits for Android's own write, which Android scheduled at the change and logs in the events log (`commit_sys_config_file: [package-user-0,…]`), at most `ADT_SAVE_WAIT` seconds (default 12) | up to 12 s, the full 12 s when nothing was pending |

Each adb call has a time limit, and an emulator that doesn't answer as a booted device is
stopped anyway, unsaved; the script says what it did. A `-read-only` emulator keeps no change,
so there it skips the save.

`adb emu kill` alone returns at once, before the emulator has exited, and
`adb wait-for-disconnect` waits for that without a time limit. Neither can stop an emulator whose
console doesn't answer, so a script built on them can hang. `stop-emulator.sh` always ends:

| What happens | What it does |
|---|---|
| The emulator exits (it saves its Quick Boot snapshot first) | waits for its process, then until adb no longer lists it |
| It hasn't exited `ADT_STOP_TIMEOUT` seconds (default 60) after `adb emu kill` | kills it (SIGKILL): its Quick Boot snapshot isn't saved |
| Its console doesn't answer `adb emu kill`, or adb doesn't list it (e.g. stuck early in its boot; name it by its AVD) | sends SIGTERM, which lets it shut down, and SIGKILL if it's still running `ADT_STOP_TIMEOUT` seconds later |
| Its console doesn't answer, and its process can't be found (e.g. it was exiting already, sent SIGTERM with a test run that was killed) | waits up to `ADT_STOP_TIMEOUT` seconds for adb to stop listing it |
| Its process can't be found or killed, and it doesn't exit (adb keeps listing it) | fails (exit 1) and says it's still running |
| Its process can't be found, and adb stops listing it | exits 0, and says its process may still be exiting |

It says which of these happened. Each time limit lasts at least its number of seconds, and at
most a second longer. The process is found through its PID:

- The emulator's console names the file the emulator advertises itself in,
  `pid_<PID>.ini` (`adb emu avd discoverypath`, e.g.
  `/run/user/1000/avd/running/pid_12345.ini`), which it deletes when it exits. This names the very
  instance the serial belongs to.
- Without that answer (a console that's hung, or doesn't know the command), the script reads
  those files itself, in `$XDG_RUNTIME_DIR/avd/running/` (else `/run/user/<uid>/avd/running/`,
  where the emulator writes them then too). Each holds the emulator's console port (`port.serial=`,
  the number in its serial) and its AVD's name (`avd.name=`), so the serial's file names its
  instance, and its AVD, which a hung console doesn't tell.
- Else the PID is the one the emulator writes into `hardware-qemu.ini.lock` in the AVD's folder
  and deletes when it exits. An emulator started with `-read-only` writes no lock file, and
  several of them can run for one AVD. (A normal instance can't run next to them: the emulator
  refuses to start with "Another emulator instance is running".)
- Else, named by its AVD alone, the discovery files of that AVD. If they name several
  `-read-only` instances, whose consoles all don't answer, the script stops none of them: it fails
  and lists their serials, so you can name one.

A PID counts only if that process's command line names the AVD, so a lock file or discovery
file left behind by a killed emulator can't make the script stop another process. A hung
emulator whose discovery file isn't found (e.g. it was started with another `$XDG_RUNTIME_DIR`)
can only be found through its lock file, in its AVD's folder: to stop it, name its AVD rather
than its serial. Stopped by its AVD's name, a hung emulator may stay in adb's list for a moment
after the script returns, until adb notices it's gone.

An emulator that's exiting, or has exited, when the script gets to it isn't a failure to stop it.
Its console answers nothing from the moment it starts shutting down, so the script can't learn its
AVD's name (with `--all` or a serial) or its PID from it; adb lists it until a moment after its
process has exited (seen after a SIGTERM: listed as a device for 2 s while it shut down, then as
offline with qemu already a zombie, then gone). While its discovery file still names its process,
the script waits for that; otherwise it waits for adb to stop listing it. A zombie, an exited
process its parent hasn't reaped yet, has exited: its command line is empty, so its PID never counts
as the AVD's.

Don't wait for an emulator by searching for its process by name: `pgrep -f`/`pkill -f` match
their pattern against every command line, including the shell that runs them when the command
is passed as a string (`bash -c '…'`, as scripts and AI agents do). A loop like
`while pgrep -f 'qemu.*tv_api25'; do …; done` then finds itself and never ends.

## Several Android versions

To test an app on more than one Android version, create one AVD per API level and boot the ones
you need; they run side by side, each on its own serial:

```bash
create-avd.sh --api 22 && create-avd.sh --api 36        # tv_api22 (Android 5.1), tv_api36 (Android 16)
for avd in tv_api22 tv_api25 tv_api36; do start-emulator.sh "$avd"; done
./gradlew connectedDebugAndroidTest                     # runs on every connected device
```

Each level needs its system image first ([`../SETUP.md`](../SETUP.md), step 5). The tested levels
are in [`../README.md`](../README.md#scope), and what differs between them in
[Differences between API levels](#differences-between-api-levels).

With several devices connected (another emulator, a phone, a TV over adb), `adb` refuses to
guess, `remote.sh` needs the serial once several emulators run, and `./gradlew installDebug`
installs on all of them. Set `ANDROID_SERIAL` to the serial `start-emulator.sh` prints (see
[its output](#start-emulatorsh)) to use only that one.

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
| Long press (held) | Hold the key | l, then the key | `remote.sh --long-press DPAD_CENTER` ([below](#long-presses)) |

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

### Long presses

`remote.sh --long-press <key> [serial]` holds one key and exits, on every API level. It sends
what a remote's button sends while it's held: the key goes down; after the device's long-press
timeout (`adb shell settings get secure long_press_timeout`) a repeat, which Android flags as a
long press (`FLAG_LONG_PRESS`); then the release. So both views, which time how long the key
stays down, and `onKeyLongPress()` see a long press. `<key>`
is a key code number or the name of a remote's key (`DPAD_CENTER`, `BACK`, `HOME`, …; `--help`
lists them). In the interactive remote, `l` then a key does the same.

```bash
remote.sh --long-press DPAD_CENTER             # long press of OK
remote.sh --long-press BACK emulator-5556      # of Back, on another emulator
```

From API 30 on, `remote.sh` uses `adb shell input keyevent --longpress <key>`, which does just
that. Before API 30, `--longpress` sends the release at once, so the app sees a short press (OK
opens what it's on). There `remote.sh` has `monkey` (`adb shell monkey`) replay the three events
instead, from a script it pushes to `/data/local/tmp` and deletes afterwards, with the release
one long-press timeout (500 ms when unset) after the repeat. What that means before API 30:

- A long press takes a moment longer than the hold itself: monkey starts a process on the device.
- Monkey unlocks the screen rotation when it exits; `remote.sh` puts the rotation settings
  (`accelerometer_rotation`, `user_rotation`) back as they were.
- While the key is held, `ActivityManager.isUserAMonkey()` returns true to apps.

Monkey isn't an option from API 30 on: on API 36 it adds a virtual touchscreen while it runs,
and that configuration change recreates the app in front, which then drops the keys.

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

Switch at any time on a running emulator, as often as you like: `wslg-toolbar.py show`. Without an AVD
name it acts on the only emulator window; with several, pass the name: `wslg-toolbar.py tv_api25 show`.
The emulator has a second window titled "Emulator" (a 620x21 bar, hidden once it has booted);
the script tells the toolbar apart by its shape, a column taller than wide. Restarting the
emulator undoes it. The script's docstring describes the emulator's windows and explains the
mechanism, how to inspect them (`xwininfo`, `xprop`) and which approaches don't work.

## Checking what the emulator is doing

```bash
adb shell dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'     # focused window, activity in front
adb exec-out screencap -p > screen.png                             # screenshot
adb shell dumpsys input | sed -n '/RecentQueue/,/PendingEvent/p'   # last 10 input events (key codes up to API 28)
adb shell cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME   # API 24 on
```

The recent queue lists events in the order Android handled them, each with its age when the dump
was taken. A key Android holds while a window it's going to starts (up to 5 s; from API 31 on,
5 s times `ro.hw_timeout_multiplier`, which no tested image sets) is listed under
`PendingEvent` or `InboundQueue` meanwhile, and joins the recent queue only once handled: after
events that came later, with the age it had all along.

Don't use `adb shell getevent > file` to check input: without a terminal its output is buffered and
the file stays empty. Simulated host input (xdotool/XTest) doesn't reach the emulator under WSLg, so
automated tests should send keys with `adb shell input keyevent`, like `remote.sh` does, and
hold them with [`remote.sh --long-press`](#long-presses).

## Differences between API levels

The scripts work the same on every [tested level](../README.md#scope), Android
TV or Google TV; these are the differences in the images that you may run into:

| API | Difference |
|---|---|
| 22 | `--quick` is slower than a cold boot. The device has no `uname`. `/sdcard` is the AVD's SD card (`/storage/sdcard`), read-only without one; from API 23 on it's internal storage, and the SD card is a removable volume (`/storage/<id>`). The stock launcher's HOME filter has no priority, so with another home app installed, Home opens the chooser; while the chooser is in front, `adb shell am start -W` of an app open behind it never returns (without `-W` it's fine). |
| 22–23 | No `cmd` on the device (`cmd package resolve-activity`, …); it exists from API 24 on. `adb shell am start -W …` names the activity an intent opened, on its `Activity:` line (but it opens it). `dumpsys activity activities` shows the intent that started each task (`intent={…}`) and the focused activity (`mFocusedActivity`); `start-emulator.sh --wait-for-home` finds the home app that way. On API 22 an app started from the home chooser joins the chooser's stack (`stackId=0`), so the stack doesn't tell the home app. |
| 22–25 | The stock launcher is `com.google.android.leanbacklauncher`. |
| 22–29 | `adb shell input keyevent --longpress` doesn't hold the key, so it's a short press; `remote.sh --long-press` holds it on every level ([Long presses](#long-presses)). |
| 23, 29 | The first boot of a new AVD with an SD card, as `create-avd.sh` makes them, opens a "USB drive connected" screen (`com.android.tv.settings/.device.storage.NewStorageActivity`) in front of the launcher, until Back; later boots don't. It's Android TV's Settings announcing the SD card as a new USB drive; an AVD without an SD card image doesn't show it. `start-emulator.sh --wait-for-home` presses Back for it. |
| 23 on | The stock launcher's HOME filter has priority 2, so `set-home-activity` and the home chooser can't pick another home app: it only takes over while the stock one is disabled (`adb shell pm disable-user --user 0 <package>`). |
| 24 on | Until the user is unlocked after a boot, a HOME intent resolves to Settings' `com.android.tv.settings/.system.FallbackHome` (also for `cmd package resolve-activity`), which holds the screen until the home app has started; both can last past `sys.boot_completed` being 1, when `start-emulator.sh` returns (without [`--wait-for-home`](#start-emulatorsh)). |
| 26 on | The stock launcher is `com.google.android.tvlauncher`. On 26–29 `leanbacklauncher` is installed too, without a HOME filter. |
| 26, 27 | The slowest cold boots up to API 28. Home doesn't leave apps until `tv_user_setup_complete` is set, which `start-emulator.sh` does ([Home on API 26 and 27](#home-on-api-26-and-27)). |
| 29 on | `dumpsys input` lists key events without key codes. avdmanager prints the harmless devices.xml error. |
| 30 | `remote.sh` keys lag the most; the emulator console's key events never arrive. |
| 36 | `adb shell monkey` adds a virtual touchscreen while it runs; that configuration change recreates the app in front, which drops the keys monkey sends. |
| Google TV, all | The stock launcher is `com.google.android.apps.tv.launcherx`, with priority 2 like the others. Without a Google account it shows a sign-in screen instead of a home screen: "Add account" on 30–33, "Set up Google TV" on 34 and 36; Home opens it like a home screen. Right after a boot it can come to the front by itself, over an app just opened. |
| Google TV, 30–33 | After a boot the launcher first shows `.coreservices.bootmode.DispatchActivity`, in a task of its own, while it decides what to show (minutes on a slow host), then brings its home task to the front, over any app opened meanwhile; its home screen then opens the sign-in screen over itself, in that task. On 34 and 36 the launcher decides inside its home screen. |
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
| `wslg-toolbar: several windows look like the toolbar` | an emulator version with another tall window titled "Emulator": compare them with `xwininfo -root -tree \| grep qemu-system` and update the script's search |
| `wslg-toolbar.py`: `X Error of failed request: BadWindow` | the emulator's toolbar or window closed while the script changed it (the emulator exited or restarted): run it again once the emulator is up |
| `start-emulator.sh: the emulator exited` | the printed log lines say why (e.g. an unknown flag); full log in `${TMPDIR:-/tmp}/emulator-<name>.log` |
| `start-emulator.sh: '<avd>' didn't finish booting within 900 s` | the host is slow: raise `ADT_BOOT_TIMEOUT`; or Android can't boot: without `--quick` if you used it, else try `-wipe-data` (factory reset) |
| `create-avd.sh: AVD '<name>' already exists` | it never overwrites one; `--if-missing` accepts it when it's from the same system image (see [`create-avd.sh`](#create-avdsh)) |
| `start-emulator.sh: '<avd>' booted, but its home app wasn't in front with the focus within 300 s` (with `--wait-for-home`) | the next line says what was in front: nothing focused (a host short of CPU: raise `ADT_HOME_TIMEOUT`), another app (on an emulator that had booted before: press Home, or leave out the flag), or a screen that two Backs didn't close |
| `start-emulator.sh: … was stopped before it finished booting` | the AVD was already running and booting (another call started it), and was stopped while this one waited; start it again |
| `start-emulator.sh: … adb can't reach it` (with `--quick`) | the restored snapshot left adb offline: `stop-emulator.sh <serial>`, then start it without `--quick` |
| `… It's still running.` from `stop-emulator.sh` | the emulator couldn't be stopped, or, without its PID, adb still listed it after `ADT_STOP_TIMEOUT` seconds (see [`stop-emulator.sh`](#stop-emulatorsh)); kill its `qemu-system-…` process by the PID in the `pid_<PID>.ini` that `adb -s <serial> emu avd discoverypath` names (without an answer: the one in `$XDG_RUNTIME_DIR/avd/running/` whose `port.serial=` is the serial's number; a killed emulator's file stays behind, so check the PID's command line first), or in `hardware-qemu.ini.lock` in the AVD's folder, or reboot WSL (`wsl --shutdown` in Windows) |
| `adb devices` shows `offline` or `unauthorized` | `adb reconnect offline`; if that doesn't help, `adb kill-server && adb start-server`; else stop the emulator and cold boot it |
| `can't tell which AVD to use` / `there's no AVD named …` | pass the AVD name or set `ADT_AVD`; the message lists the AVDs found |
| `'avdmanager' not found` (or `emulator`, `platform-tools`) | the SDK wasn't found, or lacks that package: set `ANDROID_HOME` (see [Finding your setup](#finding-your-setup)) |
| `adb: more than one device/emulator` | `export ANDROID_SERIAL=<serial>` (printed by `start-emulator.sh`) |
| `Error: Could not load devices from …/devices.xml` from `create-avd.sh` | harmless, the AVD is created correctly (see [`create-avd.sh`](#create-avdsh)) |
| `create-avd.sh: the Android TV image of API 21 has no ranchu kernel` | the emulator can't boot that image; use API 22 or newer (see [`create-avd.sh`](#create-avdsh)) |
| `This AVD's configuration is missing a kernel file! … "kernel-ranchu"` from the emulator | an AVD made from an image without a ranchu kernel (API 21), e.g. by avdmanager directly: it can't boot |
| `remote.sh` keys lag behind | expected: each key is an `adb shell input keyevent` call (see [Controlling the TV](#controlling-the-tv)) |
| A long press with `adb shell input keyevent --longpress` acts as a short press | before API 30 it doesn't hold the key: use `remote.sh --long-press` ([Long presses](#long-presses)) |
| `remote.sh: the long press failed on …` | monkey's last lines of output follow it; usually the device is gone, offline or still booting: `adb devices` |
| Black emulator window | if started with `--quick`, start it without |
| `No access to /dev/kvm`, `libpulse.so.0`, `SDK location not found`, … | setup problems: [`../SETUP.md`](../SETUP.md#troubleshooting) |
