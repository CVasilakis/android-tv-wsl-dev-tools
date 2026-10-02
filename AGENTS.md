# AGENTS.md

Guidance for AI coding agents (and humans) working on this repository.

## Project in one paragraph

android-tv-wsl-dev-tools: command-line tools for developing Android TV apps without Android Studio,
on Ubuntu under WSL2. Bash scripts plus one Python script (standard library only) that create and
boot an Android TV emulator, send remote-control keys to it, and fix its input under WSLg. What's
supported and tested is in [`README.md`](README.md#scope). They're used from other
repositories' projects, called by a relative path or through `PATH`, so they must never depend
on the current folder or on any particular app. Start with [`README.md`](README.md), then
[`bin/README.md`](bin/README.md) and [`tests/README.md`](tests/README.md); [`CI.md`](CI.md) covers
GitHub Actions.

## Commands

```bash
tests/run.py                  # hermetic tests, no SDK or emulator needed
tests/run.py -k remote -v     # a subset, one line per test
tests/run.py --strict         # fail on any skipped test, as CI does
tests/run.py --emulator       # real-emulator tier: boots your AVD headless
bin/start-emulator.sh         # cold boot the TV emulator (returns when booted); --quick: from its snapshot;
                              # --wait-for-home: also until its home app has settled in front
bin/remote.sh                 # TV remote in the terminal
bin/stop-emulator.sh          # stop it, returns once it has exited (<avd-name|serial> or --all with several);
                              # --no-save: without having Android save its recent changes first
```

## Rules

- **Location-independent.** Scripts find `lib/lib.sh` and each other through
  `readlink -f "$0"`/`$BIN_DIR`, never through the current folder or `$PATH`. The only thing read
  from the current folder is the project's `local.properties`. Messages that suggest another
  script use `command_for <script>` (bare name if it's on `PATH`, else the path the user typed).
- **`bin/` holds only commands**: users may put it on `PATH`. Helpers go in `lib/`.
- **Scope** ([`README.md`](README.md#scope)): Android TV apps, on Ubuntu under WSL2. Phones and
  other device types are out of scope. Don't claim support for a distribution, image or API level
  that hasn't been tested, and update the Scope section, which lists the tested images, when
  that changes.
- **App-agnostic.** No app names, package names or project paths in scripts or docs. Environment
  variables of these tools start with `ADT_`.
- **Every script change or new option gets a test**; a bug fix starts with a failing test. How to
  write them: [`tests/README.md`](tests/README.md#writing-tests).
- **No dependencies**: bash, coreutils and the Python standard library (3.10+) only. New tools
  (even test-only) need the user's agreement.
- **Before a release, run the emulator tier locally** on every tested API level
  ([`tests/README.md`](tests/README.md)): CI runs only the hermetic tier.
- **Each fact is in one doc; the others link to it.** [`README.md`](README.md): overview, scope,
  versions. [`SETUP.md`](SETUP.md): installing, sizes. [`bin/README.md`](bin/README.md): using
  the scripts. [`CI.md`](CI.md): GitHub Actions. [`tests/README.md`](tests/README.md): the
  tests. This file: rules for changes, and pointers.
- **Docs describe the current state, not history.** When you change something a README
  describes, update that README in the same change.
- **No machine-specific measurements in docs.** How long a boot, build, test run or key press
  takes depends on the host, so describe it relatively ("slower", "faster than a cold boot").
  Sizes, RAM needs and counts are fine.
- **Paths in docs use `~`**, never an absolute home path, so examples work on any machine.
- **The public interface is versioned** ([`README.md`](README.md#versions)). Breaking it needs
  the user's agreement and means a new major version; when you report a change, say whether it
  breaks, extends or only fixes that interface, for the release notes. Only the user creates tags
  and releases.
- Downloads are large ([`SETUP.md`](SETUP.md#sizes)). Ask before triggering big SDK downloads.
- `sudo` needs a password and there's no terminal to type it into, so ask the user to run sudo
  commands themselves in a regular terminal.

## Things that look removable but aren't

Each of these fixes a real problem. The reasons are in the linked file; read them before changing anything.

| What | Where | Why |
|---|---|---|
| `readlink -f` when sourcing `lib.sh` and in `TOOLS_DIR` | `bin/*.sh`, `lib/lib.sh` | Scripts called through a symlink on `PATH` must still find the repository. |
| AVD settings (`hw.keyboard`, `swiftshader_indirect`, landscape, …) | `bin/create-avd.sh` | Keyboard input, WSLg rendering, orientation. |
| `sg kvm` re-exec (by the real path), waiting for `sys.boot_completed` | `bin/start-emulator.sh` | KVM access without restarting WSL; installing too early fails. |
| Comparing `/dev/kvm`'s gid with the `kvm` group's | `bin/start-emulator.sh` | On WSL the device often belongs to no group, and then `usermod -aG kvm` can never help. |
| Cold boot by default, the offline watchdog only with `--quick` | `bin/start-emulator.sh` | A restored snapshot can leave adb `offline` for good; a cold boot is offline for a while normally. |
| `install_hint`'s fallback to `sdkmanager` | `lib/lib.sh` | `android` only ships from cmdline-tools 22.0; on older SDKs the hint would name a command the user doesn't have. |
| Toolbar hidden by default under WSL | `bin/start-emulator.sh`, `bin/wslg-toolbar.py` | Otherwise typed keys never reach Android. |
| Picking the toolbar by its shape (taller than wide) | `bin/wslg-toolbar.py` | A 620x21 window, hidden after boot, has the toolbar's title, group and hints, and comes first in the search once "show" has put the toolbar on top; a later "hide" then left the toolbar shown (its docstring). |
| The X error handler that ignores BadWindow while the toolbar is searched for | `bin/wslg-toolbar.py` | Any window on the display can close while the search reads it, and Xlib's default handler then ends the script, leaving the toolbar shown (its docstring). |
| No `set -e`, Esc read timeout | `bin/remote.sh` | Keep the remote alive; tell Esc from arrow keys. |
| `input keyevent` instead of the faster `adb emu event send`, `< /dev/null` on `adb shell` | `bin/remote.sh` | Console key events are emulator-only and vanish on the API 30 TV image; `adb shell` swallows the keys typed after it. |
| `--long-press`: `monkey` before API 30, `input keyevent --longpress` from 30 on; monkey's `-c …HOME`, one down time for all three events, putting the rotation settings back (deleting unset ones), success read from its output | `bin/remote.sh` | `--longpress` doesn't hold the key before API 30, and on API 36 monkey adds a touchscreen that makes the app in front drop the keys; monkey won't start without a category some activity has (TVs have no LAUNCHER ones); a real key's repeat and release keep the down time; monkey unlocks the rotation when it exits, and API 26–28 have no `user_rotation` until it's set; before API 24 `adb shell` always exits 0. |
| Only the serial on stdout, every message on stderr | `bin/start-emulator.sh` | Scripts and CI capture the serial with `serial="$(start-emulator.sh)"`; it's part of the public interface. |
| `-no-window` added when `$DISPLAY` is empty | `bin/start-emulator.sh` | Without a display the emulator aborts, and its log doesn't say why (CI runners, SSH). |
| The PID from the console (`adb emu avd discoverypath`), else from the discovery file `pid_<PID>.ini` of the serial's port, else from `hardware-qemu.ini.lock`, else from the discovery files of the AVD's name (failing when they name several), each checked against the process's command line, instead of `pgrep`; `2>/dev/null` before `<`; without a PID, waiting for adb to stop listing an emulator whose console doesn't answer, rather than failing at once | `bin/stop-emulator.sh` | `pgrep -f` also matches the shell running it; a hung console names nothing, a `-read-only` emulator writes no lock file, and several can run for one AVD; a stale lock or discovery file's PID may belong to another process; after a failed `<`, the `2>/dev/null` comes too late to hide the error; an emulator that's exiting (e.g. with a test run that was killed) answers no console command, so neither its AVD's name nor its PID can be learned, while adb lists it until a moment after its exit ([`bin/README.md`](bin/README.md#stop-emulatorsh)). |
| `time_is_up` instead of comparing `$SECONDS` with a limit | `lib/lib.sh`, `bin/start-emulator.sh`, `bin/stop-emulator.sh` | `$SECONDS` counts whole seconds, so a direct comparison can end a limit up to a second early (a 1 s limit at once). |
| `id -nG "$(id -un)"` for kvm membership, not `$USER` or the `getent` line | `bin/start-emulator.sh` | Counts kvm as a primary group, doesn't mistake `ci` for `ci-bot`, and works where `$USER` is unset (containers). |
| Xvfb's `-noreset` | `tests/support/x11.py` | Without it the server resets between two tests, and the next test's connection fails under load ([`tests/README.md`](tests/README.md#tiers)). |
| `RecentInput` reading the uptime before and after `dumpsys`, and taking an overlap anywhere in that range (of two that fit at the very same time, the one dropping fewer); a look that fails when no overlap fits; the wait before the first look checking, at two looks with no new event between them, that the screen in front has settled and that the dispatcher has no pending event, nothing in its inbound queue or the windows' outbound and wait queues, and the focus on the window `dumpsys window` names, instead of waiting for a quiet second; reading only the dispatcher's live state, not the copy at the last ANR after it; `key_events` skipping keys timed before the first look | `tests/support/input_dump.py`, `tests/emulator/test_on_emulator.py` | Evenly spaced events (API 29+ prints every key as a bare `KeyEvent`) fit several overlaps, and only the clock tells them apart, except when a press's down and up, of one moment, are all that's left of the look before: then no look could tell, and no key sends a third event at that moment; a fixed, narrower range threw out the right one when `dumpsys` was slow, and counted a key twice; counting every event as new hides those pushed out unseen; a key held for a starting window, or a release a slow device sends late, joins the recent queue later, with an old age, and a focus change once the dispatcher gets it, however long things were quiet before; after an input ANR, `dumpsys input` adds a copy of the dispatcher's state at that time, queues included, after the live one. |
| The emulator tier's long time limits; its limits for `start-emulator.sh` and `stop-emulator.sh` computed from the scripts' own; the class cleanup that stops its emulator, registered before the boot | `tests/emulator/test_on_emulator.py` | On an emulator starved of CPU a key, a look or an app's start takes many times longer, and every failure there was a limit; a script Python stops on the way can't stop the emulator it started (`start-emulator.sh` runs it with `nohup`), and `tearDownClass` isn't called when `setUpClass` fails ([`tests/README.md`](tests/README.md#tiers)). |
| `wait_for_home` checking what a HOME intent resolves to, not only what's in front; the top activity being in the home app's task, resumed and idle, rather than any activity of the home app's package in front; the focused activity's record and its own window, not their packages; two looks in a row; the last focus lines of `dumpsys window`, not the first; before API 24, the intent that started the top activity's task, not its stack; Back after `BACK_AFTER` (10 s) of another app's window with the focus, never on the home app's own, twice at most, not before the user is unlocked, and only on an emulator whose boot `start-emulator.sh` waited for | `lib/lib.sh`, `bin/start-emulator.sh` | Until the user is unlocked, Settings' `FallbackHome` is in front with the focus, looking like a settled home app; Google TV's launcher (API 30–33) first shows `DispatchActivity`, in a task of its own, for minutes on a slow host, then brings its home task over whatever is in front; a home app still starting, or its window focused while its sign-in screen is already on top, is about to change; an activity in front without a focused window gets no keys, and another app's dialog (an ANR) takes them; a look can catch a state that's gone a moment later; after an ANR the dump starts with a copy of the focus at that time; API 22 and 23 have no `cmd`, and on API 22 an app started from the home chooser joins the chooser's stack; on API 23 and 29 a new AVD's first boot (every CI boot) shows "USB drive connected" until Back; a Back on the launcher's `DispatchActivity` could end its decision; an emulator that had booted before may be in use ([`bin/README.md`](bin/README.md#start-emulatorsh), `wait_for_home`'s comment). |
| The emulator tier booting with `--wait-for-home`; the Home test bringing the home app to the front with a HOME intent and waiting for it with `wait_for_home`, then opening Settings again until it has settled in front (`home_look` given its activity: at the top, done starting, with its own window focused) at two looks in a row, with `am start` without `-W`; then waiting for the focused activity (`mFocusedApp`, not the focused window) to be of the home app's package, rather than for Settings to go | `tests/emulator/test_on_emulator.py` | Right after `sys.boot_completed` the device hasn't settled: from API 24 on, until the user is unlocked, `FallbackHome` holds the screen and a HOME intent resolves to it, and on a starved device no window had the focus long after the boot, so keys reached no app; after that the home app or Google TV's sign-in screen can still come over an app by itself, and `FallbackHome` leaves by itself: either passed for Home's work; an ANR dialog or no focus at all counted as leaving Settings; on a starved device the home app's window can lack focus for over 30 s after Home; on API 22 `am start -W` of an app open behind the home chooser never returns ([`tests/README.md`](tests/README.md#tiers)). |
| After setting `tv_user_setup_complete` on API 26 and 27, looking as root (`su`) at `settings_secure.xml` until it holds the value and no `settings_secure.xml.bak` is left, then `sync`, rather than `settings get` or a fixed wait | `bin/start-emulator.sh` | `settings get` answers from memory at once; Android writes the file about 0.2 s later, and a boot reads the `.bak` while it's there, which also holds once Android has deleted it, until the filesystem's journal records that, up to 5 s later: a kill before lost the setting ([`bin/README.md`](bin/README.md#start-emulatorsh)). |
| Before `adb emu kill`, `dumpsys package write` (up to API 31) or a wait of up to `SAVE_WAIT` (12 s) for Android's own write of the app states (from API 33 on), then `sync`; skipped on a `-read-only` emulator and with `--no-save` | `bin/stop-emulator.sh` | `adb emu kill` doesn't shut Android down; Android writes an app's enabled state 10 s after the change and a setting 0.2 s after it, and a boot reads the old file's backup until the filesystem's journal records the new one, up to 5 s later; from API 33 on `dumpsys package write` writes nothing, and the pending write was scheduled before the stop began, so it comes within 10 s ([`bin/README.md`](bin/README.md#stop-emulatorsh)). |
| `ADT_KVM_DEVICE`, `ADT_PROC_VERSION`, `WSLG_TOOLBAR_TIMEOUT`, `ADT_OFFLINE_TIMEOUT`, `ADT_PROGRESS_INTERVAL`, `ADT_ADB_TIMEOUT`, `ADT_BACK_AFTER`, `ADT_SAVE_TIMEOUT`, `ADT_SAVE_WAIT` | `lib/lib.sh`, `bin/wslg-toolbar.py`, `bin/start-emulator.sh` | Let the tests simulate other machines and not wait 30 or 60 s ([`tests/README.md`](tests/README.md)). |

## Emulator facts that save time

Read these parts of [`bin/README.md`](bin/README.md) before driving the emulator:

- [Controlling the TV](bin/README.md#controlling-the-tv): there's no touchscreen; send keys with
  `adb shell input keyevent`, since host-level input (xdotool) doesn't reach the emulator under
  WSLg.
- [Checking what the emulator is doing](bin/README.md#checking-what-the-emulator-is-doing): the
  activity in front, screenshots, the keys Android received.
- [Differences between API levels](bin/README.md#differences-between-api-levels): stock
  launchers and their HOME priority, key codes in the input dump, first-boot screens.
