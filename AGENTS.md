# AGENTS.md

Guidance for AI coding agents (and humans) working on this repository.

## Project in one paragraph

android-cli-dev-tools: command-line tools for developing Android apps without Android Studio.
Bash scripts plus one Python script (standard library only) that create and boot an Android TV
emulator, send remote-control keys to it, and fix its input under WSLg. They're used from other
repositories' projects, called by a relative path or through `PATH`, so they must never depend
on the current folder or on any particular app. Start with [`README.md`](README.md), then
[`bin/README.md`](bin/README.md) and [`tests/README.md`](tests/README.md); [`CI.md`](CI.md) covers
GitHub Actions.

## Commands

```bash
tests/run.py                  # hermetic tests, no SDK or emulator needed
tests/run.py -k remote -v     # a subset, one line per test
tests/run.py --emulator       # real-emulator tier: boots your AVD headless
bin/start-emulator.sh         # cold boot the TV emulator (returns when booted); --quick: from its snapshot
bin/remote.sh                 # TV remote in the terminal
adb emu kill                  # stop the emulator (with several devices: adb -s <serial> emu kill)
```

## Rules

- **Location-independent.** Scripts find `lib/lib.sh` and each other through
  `readlink -f "$0"`/`$BIN_DIR`, never through the current folder or `$PATH`. The only thing read
  from the current folder is the project's `local.properties`. Messages that suggest another
  script use `command_for <script>` (bare name if it's on `PATH`, else the path the user typed).
- **`bin/` holds only commands**: users may put it on `PATH`. Helpers go in `lib/`.
- **App-agnostic.** No app names, package names or project paths in scripts or docs. Environment
  variables of these tools start with `ADT_`.
- **Every script change or new option gets a test** in `tests/hermetic/test_<script>.py`; a bug fix
  starts with a failing test. New scripts need `--help` starting with `Usage: <name>`, a shebang,
  and a line in `bin/README.md` (enforced by `test_conventions.py`). Check that a new test can
  fail by breaking the behavior once.
- **No dependencies**: bash, coreutils and the Python standard library only. New tools (even
  test-only) need the user's agreement.
- **Docs describe the current state, not history.** When you change something a README
  describes, update that README in the same change.
- **No machine-specific measurements in docs.** How long a boot, build, test run or key press
  takes depends on the host, so describe it relatively ("slower", "faster than a cold boot").
  Sizes, RAM needs and counts are fine.
- **No personal information in tracked files:** no names, e-mail addresses, usernames or absolute
  home paths (write `~` or `/home/<user>`), no machine-specific config. The exceptions are
  `LICENSE` and this repository's address (`CVasilakis/android-cli-dev-tools`), which contains
  the owner's username, in examples that check it out.
- **The public interface is versioned** ([`README.md`](README.md#versions-and-compatibility)):
  command names, options, the environment variables they read, defaults and exit statuses.
  Breaking it needs the user's agreement and means a new major version; when you report a change,
  say whether it breaks, extends or only fixes that interface, for the release notes. Only the
  user creates tags and releases.
- Downloads can be large (the emulator is ~354 MB, a system image ~700 MB). Ask before
  triggering big SDK downloads.
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
| No `set -e`, Esc read timeout | `bin/remote.sh` | Keep the remote alive; tell Esc from arrow keys. |
| `input keyevent` instead of the faster `adb emu event send`, `< /dev/null` on `adb shell` | `bin/remote.sh` | Console key events are emulator-only and vanish on the API 30 TV image; `adb shell` swallows the keys typed after it. |
| Only the serial on stdout, every message on stderr | `bin/start-emulator.sh` | Scripts and CI capture the serial with `serial="$(start-emulator.sh)"`; it's part of the public interface. |
| `-no-window` added when `$DISPLAY` is empty | `bin/start-emulator.sh` | Without a display the emulator aborts, and its log doesn't say why (CI runners, SSH). |
| `id -nG "$(id -un)"` for kvm membership, not `$USER` or the `getent` line | `bin/start-emulator.sh` | Counts kvm as a primary group, doesn't mistake `ci` for `ci-bot`, and works where `$USER` is unset (containers). |
| `ADT_KVM_DEVICE`, `ADT_PROC_VERSION`, `WSLG_TOOLBAR_TIMEOUT`, `ADT_OFFLINE_TIMEOUT` | `lib/lib.sh`, `bin/wslg-toolbar.py`, `bin/start-emulator.sh` | Let the tests simulate other machines and not wait 30 s ([`tests/README.md`](tests/README.md)). |

## Emulator facts that save time

(Details in [`bin/README.md`](bin/README.md).)

- The TV emulator has **no touchscreen**: clicks on the screen do nothing by design.
- Sending keys from a script: `adb shell input keyevent DPAD_DOWN`, like `remote.sh` (any device,
  any API level). `adb emu event send EV_KEY:108:1 EV_KEY:108:0` is faster but emulator-only, and
  its keys are lost on the API 30 TV image. Host-level simulated input (xdotool/XTest,
  XSetInputFocus) does **not** reach the emulator under WSLg.
- To check which keys Android received: `adb shell dumpsys input | sed -n '/RecentQueue/,/PendingEvent/p'`
  (key codes up to API 29; API 30 prints only `KeyEvent, age=…`).
  `adb shell getevent > file` doesn't work for this (output is buffered without a terminal).
- AVDs of several API levels (`create-avd.sh --api 22`, `--api 36`) run side by side; the tested
  ones are listed in [`README.md`](README.md#several-android-versions), the one place to update
  when that changes. API 21's Android TV image can't boot (goldfish kernel only), so
  `create-avd.sh --api 21` refuses it.
- In the emulator window, Esc and F1 don't reach Android; Back is Ctrl+Backspace, Home Ctrl+H, Menu Ctrl+M.
- From API 25 on, the stock TV launcher's HOME filter has priority 2, so `set-home-activity` and
  the home chooser don't work on these images; another home app only takes over while the stock
  one is disabled (`adb shell pm disable-user --user 0 <package>`). On API 22 it has no priority:
  with another home app installed, Home opens the chooser. The stock launcher is
  `com.google.android.leanbacklauncher` on API 22 and 25 and `com.google.android.tvlauncher` on
  API 28, 30, 33 and 36.
