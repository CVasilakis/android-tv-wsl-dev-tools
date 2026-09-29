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
| hermetic | [`hermetic/`](hermetic) | Python 3; optional: Xvfb, shellcheck | Each script's behavior against fake tools and a fake machine, and how the emulator tier reads Android's input dump. Run it after every script change. |
| emulator | [`emulator/`](emulator) | Your SDK and AVD (`ADT_AVD` picks one) | What fakes can't show: booting really completes, and with `--wait-for-home` returns with the home app in front and focused, `remote.sh`'s keys arrive in Android as the right keys, its Home key takes the device from an app to its home app, `--long-press` holds the key for the device's long-press timeout and leaves the settings as they were, and `stop-emulator.sh` returns only once the AVD can start again, also for a `-read-only` emulator, which writes no lock file, and doesn't fail on one that's exiting already (sent SIGTERM). |

Some hermetic tests skip, with the reason printed, when an optional tool is missing:
- **Xvfb** (`sudo apt-get install -y xvfb`) for the `wslg-toolbar.py`
  tests, which create emulator-like windows on a private X server. It runs on the first free
  display from `:99` up, reachable only through an abstract socket. Under WSLg `/tmp/.X11-unix`
  is read-only, and a low display number would capture the desktop's own apps. It runs with
  `-noreset`: each test's windows live on their own connection, so between two tests the server
  has no clients; by default it resets then, and the next test's connection can fail while it
  does, more often the busier the machine.
  `SCRIPT_TESTS_DISPLAY=:0` runs the tests on an existing display instead; under WSLg you'll see
  small windows flash. Only use it to watch, with no Linux GUI app open that you'd mind losing:
  WSLg's compositor (weston) can crash during such a run, which closes every Linux GUI app and
  breaks the tests' X connections. Each crash seen came right after WSLg's RDP client had
  disconnected (`stopRdpNotifyEvent` in `/mnt/wslg/weston.log`), when a test created a window
  (`CreateWndow(): rdp_peer is not initalized`, then `terminated with signal 11` in
  `/mnt/wslg/stderr.log`); after the restart, the window manager can stop mapping new windows
  for a while.
- **shellcheck** (`sudo apt-get install -y shellcheck`) for static analysis of the shell scripts.

The emulator tier reuses the AVD if it's already running and leaves it running. Otherwise it boots
it (cold) with `--wait-for-home -no-window -no-snapshot-save`, so every test starts on a device
that has settled, and stops it at the end. The `stop-emulator.sh` tests
skip when the AVD was already running, since they would have to stop it. It tests one AVD per run, so to
cover every Android version you use:

```bash
for l in 22 23 24 25 26 27 28 29 30 31 33 34 36; do ADT_AVD=tv_api$l tests/run.py --emulator; done
for l in 30 31 33 34 36; do ADT_AVD=gtv_api$l tests/run.py --emulator; done   # Google TV
```

The Home test decides from what's in front (`dumpsys window`'s focus), which after a boot can still
change by itself: the home app, or Google TV's sign-in screen, comes to the front over an app just
opened, and from API 24 on Settings' own `FallbackHome` holds the screen, and is what a HOME intent
resolves to, until the user is unlocked ([Differences between API
levels](../bin/README.md#differences-between-api-levels)). So the test first brings the home app to
the front with a HOME intent (an emulator that was already running may show another app) and waits
for it with `wait_for_home` from `lib.sh`, what `start-emulator.sh --wait-for-home` runs, which
also names its package. On Google TV without an account that's the launcher showing its sign-in
screen, and on API 22 with a second home app installed it's the chooser (package `android`). Then
the test opens Settings again until it's in front at three looks in a row, seconds apart, sends
Home, and waits until the activity in front (`mFocusedApp`: on a starved device its window can
still lack focus 30 s later) is of that package, not merely until something other than Settings
is. A failure shows `dumpsys window`'s focus lines.

On API 29 and newer, Android's input dump doesn't show key codes, so there the tier checks only
that each key press arrived, and how far apart a long press's events are; which Android key it
was, and the long-press flag on the repeat, are still checked on older ones, and by the hermetic
tests for all of them. The hermetic tests' fake `monkey` and `input keyevent --longpress` record
the key events they send, with their times, as Android would receive them (`api_level` picks
which one `remote.sh` uses). The tier reads Android's input dump, whose recent queue keeps only
the last 10 events, window focus changes included, so it sends one key at a time, a moment after
the one before arrived, and looks after each, merging the looks into one history. The events' order and ages, and the device's uptime
read around each look, tell which events are new (`RecentInput` in
[`support/input_dump.py`](support/input_dump.py) says how). A look fails the test rather than
miscount: when 10 or more events came since the look before, so some may be missing, and when
events come too evenly for the clock to tell how many came. Before its first look, a test waits
until no event has come for a second and none is waiting: a key Android holds for a window that's
starting enters the queue only once the window is there, with the age it had all along. Only keys
that happened after the first look count, since on a slow device a key sent before can still
arrive later, with its old time (`input keyevent` can send the release seconds after the press).
[`hermetic/test_input_dump.py`](hermetic/test_input_dump.py) checks this reading against made-up
dumps, so it runs with the hermetic tier.

The tier's time limits hold for an emulator starved of CPU, where `adb shell input keyevent`, a
look at the input dump and an app's start each take many times longer than on an idle host. A
wait ends as soon as what it waits for is there, so a long limit only makes a failure slower. Its
limits for `start-emulator.sh` and `stop-emulator.sh` are longer than the scripts' own, computed
from them (`ADT_BOOT_TIMEOUT`, `ADT_HOME_TIMEOUT` and `ADT_STOP_TIMEOUT`, which it passes on): a script always gets to
its own limit, stops what it started and says why, whereas Python stopping `start-emulator.sh`
would leave its emulator booting. An emulator the tier boots is stopped at the end even when its
boot failed. To try the tier on a starved emulator, boot it on one host CPU shared with busy loops;
the tier then reuses it:

```bash
serial="$(taskset -c 3 bin/start-emulator.sh tv_api31 -no-window -no-snapshot-save -read-only -cores 1)"
for i in $(seq 9); do timeout 1800 taskset -c 3 bash -c 'while :; do :; done' & done
ADT_AVD=tv_api31 tests/run.py --emulator -k test_remote_keys; kill $(jobs -p); bin/stop-emulator.sh "$serial"
```

## Layout

| Path | Contents |
|---|---|
| [`run.py`](run.py) | Entry point: picks the tiers, filters, prints skip reasons. |
| [`support/sandbox.py`](support/sandbox.py) | `Sandbox`: a throwaway machine per test. `ScriptTestCase`: the base class. |
| [`support/fake_tools.py`](support/fake_tools.py) | Fakes for `adb`, `emulator`, `avdmanager`, `android`, `sdkmanager`, `python3`, `getent`, `id`, `sg`. |
| [`support/x11.py`](support/x11.py) | Private Xvfb server, fake emulator windows, and `WindowChurn`, a client that keeps opening and closing windows. |
| [`support/input_dump.py`](support/input_dump.py) | Reading Android's input dispatcher from `dumpsys input`, for the emulator tier. |
| `hermetic/test_<script>.py` | One file per script, plus [`test_conventions.py`](hermetic/test_conventions.py) for the rules all scripts follow, and [`test_input_dump.py`](hermetic/test_input_dump.py) for `support/input_dump.py`. |
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
first free port, writes its PID into its AVD's `hardware-qemu.ini.lock` as the real one does (or
writes none, as a real one started with `-read-only` does: `emulator_lock_file=False`), answers
`adb emu avd discoverypath` with its `pid_<PID>.ini` like the real console (or doesn't know the
command: `emulator_discoverable=False`), and stays alive until `adb emu kill`. All fakes share
state in the sandbox, so a test can arrange a situation and then check the result:

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
`fake_tools.py`; e.g. `front` and `home_resolves` for what the device shows in front over time, one item per look, for
`--wait-for-home` (a `"BACK"` item in `front`: what comes after it waits for a Back key), with
`ADT_HOME_STABLE=0` so the home app needn't stay in front 3 s, and another screen gets Back after
1 s rather than 10; `adb_offline` for an emulator adb can't reach, with `ADT_OFFLINE_TIMEOUT=1`
in the script's environment so `start-emulator.sh --quick` doesn't wait 30 s, or
`emulator_stuck` for one that won't exit, with `ADT_STOP_TIMEOUT=1` for `stop-emulator.sh`;
`adb_lists_exited` for adb's lag in unlisting an emulator whose process has exited, with
`sandbox.kill_emulator(serial)`, which leaves a zombie, or `emulator_already_exiting` for one
that's shutting down when `adb emu kill` comes), `connect_device()`, `wsl()` and file permissions on `sandbox.kvm`. Check the
results with `result.code/out/err`, `sandbox.calls()`/`argvs(tool)` (each call has the `time` it
was made), `sandbox.running()` and the
files in `sandbox.home`; `sandbox.emulator_pid(serial)` and `sandbox.alive(pid)` show whether an
emulator's process has exited (a zombie has: fake emulators are the test's children, so a killed
one stays a zombie until the cleanup reaps it).

## Writing tests

- **Test behavior, not lines.** Name each test after what a user relies on
  (`test_refuses_to_guess_between_several_tv_avds`) and assert on observable results. Don't
  source a script to call its internals. `lib.sh` is the exception, because its functions are its interface.
- **Every bug fix and every new option gets a test** in the script's `test_<script>.py`. A new
  script gets a new file; `test_conventions.py` already makes it provide `--help`, a shebang, and
  a line in [`../bin/README.md`](../bin/README.md).
- **Stay hermetic.** A hermetic test never reads the real `$HOME`, SDK or devices. If a script
  needs a new external command, add a fake to `fake_tools.py` (or the command to `UTILITIES` in
  `sandbox.py` if it's a basic system utility).
- **Wait for a condition, not for a time.** On a busy machine a script gets everywhere several
  times later, so a test never sleeps on a fixed timer when it can wait for a condition with
  `self.wait_until(condition)`. To type into `remote.sh` once it has handled a key, for example,
  wait until `sandbox.calls()` shows that key's `adb` call; to stop an emulator while
  `start-emulator.sh` waits for its boot, start the script with `start_in_background()`
  ([`test_start_emulator.py`](hermetic/test_start_emulator.py)) and wait until the calls show
  its boot polls. To act while `wslg-toolbar.py` is between two steps, wait until the windows
  show the first step, and grab the X server (`EmulatorWindows.grabbed()`): the script can't go
  on while the test checks that it's still there. A time limit a test sets
  (`ADT_BOOT_TIMEOUT=2`) must only end what never finishes by design, not race what the script
  does on the way, and a check on how long a
  script took belongs only where its output can't show which way it went. The scripts' own
  limits count whole seconds (`$SECONDS`, through `time_is_up` in `lib.sh`), so where a test
  checks that one lasts its full length, it makes the script start it late in a second
  (`adb_late`) and measures between the calls' times. To try a test on a
  busy machine, share one CPU with busy loops:
  ```bash
  for i in $(seq 29); do timeout 600 taskset -c 0 bash -c 'while :; do :; done' & done
  taskset -c 0 tests/run.py -k <test>; kill $(jobs -p)
  ```
- **Check that a new test can fail.** Break the behavior on purpose, see the test fail, then restore it.
- Anything that needs the real emulator goes in `emulator/`, and must leave the developer's
  emulator as it found it.
