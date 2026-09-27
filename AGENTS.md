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
bin/start-emulator.sh         # cold boot the TV emulator (returns when booted); --quick: from its snapshot
bin/remote.sh                 # TV remote in the terminal
bin/stop-emulator.sh          # stop it, returns once it has exited (<avd-name|serial> or --all with several)
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
| No `set -e`, Esc read timeout | `bin/remote.sh` | Keep the remote alive; tell Esc from arrow keys. |
| `input keyevent` instead of the faster `adb emu event send`, `< /dev/null` on `adb shell` | `bin/remote.sh` | Console key events are emulator-only and vanish on the API 30 TV image; `adb shell` swallows the keys typed after it. |
| Only the serial on stdout, every message on stderr | `bin/start-emulator.sh` | Scripts and CI capture the serial with `serial="$(start-emulator.sh)"`; it's part of the public interface. |
| `-no-window` added when `$DISPLAY` is empty | `bin/start-emulator.sh` | Without a display the emulator aborts, and its log doesn't say why (CI runners, SSH). |
| The PID from `hardware-qemu.ini.lock`, checked against the process's command line, instead of `pgrep` | `bin/stop-emulator.sh` | `pgrep -f` also matches the shell running it, and a stale lock file's PID may belong to another process. |
| `id -nG "$(id -un)"` for kvm membership, not `$USER` or the `getent` line | `bin/start-emulator.sh` | Counts kvm as a primary group, doesn't mistake `ci` for `ci-bot`, and works where `$USER` is unset (containers). |
| `ADT_KVM_DEVICE`, `ADT_PROC_VERSION`, `WSLG_TOOLBAR_TIMEOUT`, `ADT_OFFLINE_TIMEOUT`, `ADT_PROGRESS_INTERVAL`, `ADT_ADB_TIMEOUT` | `lib/lib.sh`, `bin/wslg-toolbar.py`, `bin/start-emulator.sh` | Let the tests simulate other machines and not wait 30 or 60 s ([`tests/README.md`](tests/README.md)). |

## Emulator facts that save time

Read these parts of [`bin/README.md`](bin/README.md) before driving the emulator:

- [Controlling the TV](bin/README.md#controlling-the-tv): there's no touchscreen; send keys with
  `adb shell input keyevent`, since host-level input (xdotool) doesn't reach the emulator under
  WSLg.
- [Checking what the emulator is doing](bin/README.md#checking-what-the-emulator-is-doing): the
  activity in front, screenshots, the keys Android received.
- [Differences between API levels](bin/README.md#differences-between-api-levels): stock
  launchers and their HOME priority, key codes in the input dump, first-boot screens.
