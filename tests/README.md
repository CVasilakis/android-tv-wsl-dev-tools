# tests/

Behavior tests for the scripts in [`../bin/`](../bin/README.md) and [`../lib/`](../lib/lib.sh).
They run the real scripts and check what a user would see and what the scripts do to the machine:
exit codes, messages, the files they write, and every call they make to `adb`, `emulator` and
`avdmanager`. Python standard library only (Python 3.10+), nothing to install.

```bash
tests/run.py                  # hermetic tier, no SDK or emulator needed
tests/run.py -k remote -v     # only tests whose name contains "remote", one line each
tests/run.py --emulator       # real-emulator tier: boots your AVD headless (see below)
tests/run.py --all            # both
tests/run.py --strict         # fail if any test was skipped (what CI runs)
```

CI ([`../.github/workflows/tests.yml`](../.github/workflows/tests.yml)) runs the hermetic tier with
`--strict` on every push to `main` and every pull request, on Python 3.10 and the newest Python.
It installs the optional tools below, so there a skip fails the run instead of hiding a test that
no longer runs; a test that needs another optional tool must have it installed in the workflow
too. The emulator tier isn't run there; run it locally before a release.

## Tiers

| Tier | Folder | Needs | Checks |
|---|---|---|---|
| hermetic | [`hermetic/`](hermetic) | Python 3; optional: Xvfb, shellcheck | Each script's behavior against fake tools and a fake machine. Run it after every script change. |
| emulator | [`emulator/`](emulator) | Your SDK and AVD (`ADT_AVD` picks one) | What fakes can't show: booting really completes, `remote.sh`'s keys arrive in Android as the right keys, its Home key leaves an app, `--long-press` holds the key for the device's long-press timeout and leaves the settings as they were, and `stop-emulator.sh` returns only once the AVD can start again. |

Some hermetic tests skip, with the reason printed, when an optional tool is missing:
- **Xvfb** (`sudo apt-get install -y xvfb`) for the `wslg-toolbar.py`
  tests, which create emulator-like windows on a private X server. It runs on the first free
  display from `:99` up, reachable only through an abstract socket. Under WSLg `/tmp/.X11-unix`
  is read-only, and a low display number would capture the desktop's own apps. It runs with
  `-noreset`: each test's windows live on their own connection, so between two tests the server
  has no clients; by default it resets then, and the next test's connection can fail while it
  does, more often the busier the machine.
  `SCRIPT_TESTS_DISPLAY=:0` runs the tests on an existing display instead; under WSLg you'll see
  small windows flash.
- **shellcheck** (`sudo apt-get install -y shellcheck`) for static analysis of the shell scripts.

The emulator tier reuses the AVD if it's already running and leaves it running. Otherwise it boots
it (cold) with `-no-window -no-snapshot-save` and stops it at the end. The `stop-emulator.sh` test
skips when the AVD was already running, since it would have to stop it. It tests one AVD per run, so to
cover every Android version you use:

```bash
for l in 22 23 24 25 26 27 28 29 30 31 33 34 36; do ADT_AVD=tv_api$l tests/run.py --emulator; done
for l in 30 31 33 34 36; do ADT_AVD=gtv_api$l tests/run.py --emulator; done   # Google TV
```

On API 29 and newer, Android's input dump doesn't show key codes, so there the tier checks only
that each key press arrived, and how far apart a long press's events are; which Android key it
was, and the long-press flag on the repeat, are still checked on older ones, and by the hermetic
tests for all of them. The hermetic tests' fake `monkey` and `input keyevent --longpress` record
the key events they send, with their times, as Android would receive them (`api_level` picks
which one `remote.sh` uses). The tier reads Android's input dump, whose queue keeps only the last 10
events, window focus changes included, so it sends one key at a time and looks after each
(`RecentInput` in [`emulator/test_on_emulator.py`](emulator/test_on_emulator.py) says why).

## Layout

| Path | Contents |
|---|---|
| [`run.py`](run.py) | Entry point: picks the tiers, filters, prints skip reasons. |
| [`support/sandbox.py`](support/sandbox.py) | `Sandbox`: a throwaway machine per test. `ScriptTestCase`: the base class. |
| [`support/fake_tools.py`](support/fake_tools.py) | Fakes for `adb`, `emulator`, `avdmanager`, `android`, `sdkmanager`, `python3`, `getent`, `id`, `sg`. |
| [`support/x11.py`](support/x11.py) | Private Xvfb server and fake emulator windows. |
| `hermetic/test_<script>.py` | One file per script, plus [`test_conventions.py`](hermetic/test_conventions.py) for the rules all scripts follow. |
| [`emulator/test_on_emulator.py`](emulator/test_on_emulator.py) | The real-emulator tier. |

## How the hermetic tier works

Each test gets a `Sandbox` (`self.sandbox`), a temporary folder with:
- a copy of `bin/` and `lib/`, and a separate project folder the scripts run in, so a real
  `local.properties` can't leak in;
- its own `$HOME`, `$TMPDIR` and a `$PATH` with only basic utilities plus fakes, so the host's
  SDK, AVDs, emulators, kvm group and WSL don't change the results;
- a fake KVM device and `/proc/version`, passed to the scripts through `ADT_KVM_DEVICE` and
  `ADT_PROC_VERSION` (defined in `lib.sh` for this purpose only).

The fakes behave like the real tools as far as the scripts can see: same output formats (including
the `\r` adb adds), exit codes and side effects. For example, the fake emulator registers on the
first free port, writes its PID into its AVD's `hardware-qemu.ini.lock` as the real one does, and
stays alive until `adb emu kill`. All fakes share state in the sandbox, so a
test can arrange a situation and then check the result:

```python
class Example(ScriptTestCase):
    def test_boots_its_own_avd_next_to_another_emulator(self):
        self.sandbox.install_sdk()                        # fake SDK in ~/Android/Sdk
        self.sandbox.add_avd("tv_api25")                  # AVDs, as avdmanager lays them out
        self.sandbox.add_avd("phone", tv=False)
        self.sandbox.start_emulator("phone")              # already running, takes emulator-5554
        result = self.sandbox.run("start-emulator.sh")    # the real script
        self.assertSucceeded(result)
        self.assertIn("booted as emulator-5556", result.out)
        self.assertEqual(self.sandbox.running(), {"emulator-5554": "phone", "emulator-5556": "tv_api25"})
```

`sandbox.run(script, how=…, cwd=…)` calls a script the way a user would: by its absolute path
(the default), by a path relative to `cwd`, by its bare name with `bin/` on `PATH`, or through
symlinks in `~/.local/bin`. `cwd` defaults to the project folder. Behavior that depends on how a
script was called (finding `lib.sh`, suggested commands) is tested every way.

Arrange failures and odd situations with `sandbox.set_behavior(...)` (see `DEFAULT_BEHAVIOR` in
`fake_tools.py`; e.g. `adb_offline` for an emulator adb can't reach, with `ADT_OFFLINE_TIMEOUT=1`
in the script's environment so `start-emulator.sh --quick` doesn't wait 30 s, or
`emulator_stuck` for one that won't exit, with `ADT_STOP_TIMEOUT=1` for `stop-emulator.sh`), `connect_device()`, `wsl()` and file permissions on `sandbox.kvm`. Check the
results with `result.code/out/err`, `sandbox.calls()`/`argvs(tool)`, `sandbox.running()` and the
files in `sandbox.home`; `sandbox.emulator_pid(serial)` and `sandbox.alive(pid)` show whether an
emulator's process has exited.

## Writing tests

- **Test behavior, not lines.** Name each test after what a user relies on
  (`test_refuses_to_guess_between_several_tv_avds`) and assert on observable results. Don't
  source a script to call its internals. `lib.sh` is the exception, because its functions are its interface.
- **Every bug fix and every new option gets a test** in the script's `test_<script>.py`. A new
  script gets a new file; `test_conventions.py` already makes it provide `--help`, a shebang, and
  a line in [`../bin/README.md`](../bin/README.md).
- **Stay hermetic.** A hermetic test never reads the real `$HOME`, SDK or devices, and never
  sleeps on a fixed timer when it can wait for a condition: a script starts slower on a busy
  machine. To type into `remote.sh` once it has handled a key, for example, wait until
  `sandbox.calls()` shows that key's `adb` call. If a script needs a new external
  command, add a fake to `fake_tools.py` (or the command to `UTILITIES` in `sandbox.py` if it's
  a basic system utility).
- **Check that a new test can fail.** Break the behavior on purpose, see the test fail, then restore it.
- Anything that needs the real emulator goes in `emulator/`, and must leave the developer's
  emulator as it found it.
