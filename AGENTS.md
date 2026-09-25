# AGENTS.md

Guidance for AI coding agents (and humans) working on this repository. `CLAUDE.md` is a symlink
to this file; edit this one.

## Project in one paragraph

android-cli-dev-tools: command-line tools for developing Android apps without Android Studio.
Bash scripts plus one Python script (standard library only) that create and boot an Android TV
emulator, send remote-control keys to it, and fix its input under WSLg. They're used from other
repositories' projects, called by a relative path or through `PATH`, so they must never depend
on the current folder or on any particular app. Start with [`README.md`](README.md), then
[`bin/README.md`](bin/README.md) and [`tests/README.md`](tests/README.md).

## Commands

```bash
tests/run.py                  # hermetic tests (~50 s), no SDK or emulator needed
tests/run.py -k remote -v     # a subset, one line per test
tests/run.py --emulator       # real-emulator tier: boots your AVD headless
bin/start-emulator.sh         # boot the TV emulator (returns when booted)
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
- **No personal information in tracked files:** no names, e-mail addresses, usernames or absolute
  home paths (write `~` or `/home/<user>`), no machine-specific config.
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
| `install_hint`'s fallback to `sdkmanager` | `lib/lib.sh` | `android` only ships from cmdline-tools 22.0; on older SDKs the hint would name a command the user doesn't have. |
| Toolbar hidden by default under WSL | `bin/start-emulator.sh`, `bin/wslg-toolbar.py` | Otherwise typed keys never reach Android. |
| No `set -e`, Esc read timeout | `bin/remote.sh` | Keep the remote alive; tell Esc from arrow keys. |
| `ADT_KVM_DEVICE`, `ADT_PROC_VERSION`, `WSLG_TOOLBAR_TIMEOUT` | `lib/lib.sh`, `bin/wslg-toolbar.py` | Let the tests simulate other machines ([`tests/README.md`](tests/README.md)). |

## Emulator facts that save time

(Details in [`bin/README.md`](bin/README.md).)

- The TV emulator has **no touchscreen**: clicks on the screen do nothing by design.
- Sending keys from a script: `adb shell input keyevent DPAD_DOWN` (any device) or
  `adb emu event send EV_KEY:108:1 EV_KEY:108:0` (emulator only, faster). Host-level simulated
  input (xdotool/XTest, XSetInputFocus) does **not** reach the emulator under WSLg.
- To check which keys Android received: `adb shell dumpsys input | sed -n '/RecentQueue/,/PendingEvent/p'`.
  `adb shell getevent > file` doesn't work for this (output is buffered without a terminal).
- In the emulator window, Esc and F1 don't reach Android; Back is Ctrl+Backspace, Home Ctrl+H, Menu Ctrl+M.
- The stock TV launcher's HOME filter has priority 2, so `set-home-activity` and the home chooser
  don't work on this image; another home app only takes over while the stock one is disabled
  (`adb shell pm disable-user --user 0 com.google.android.leanbacklauncher`).
