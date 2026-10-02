"""stop-emulator.sh: which emulator it stops, that it returns only once the emulator's process has
exited, and what it does when the emulator doesn't exit."""
import subprocess
import time

from support.sandbox import ScriptTestCase


class StopTestCase(ScriptTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.install_sdk()
        self.tv = self.sandbox.add_avd("tv_api25")
        self.sandbox.add_avd("tv_api30")
        self.sandbox.add_avd("phone", tv=False)

    def lock(self, folder):
        return folder / "hardware-qemu.ini.lock"

    def kills(self):
        """Serials that got `adb emu kill`."""
        return [argv[1] for argv in self.sandbox.argvs("adb") if argv[2:] == ["emu", "kill"]]

    def a_process(self):
        """A process that isn't an emulator, stopped at the end of the test."""
        process = subprocess.Popen(["sleep", "60"])
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return process


class WhichEmulator(StopTestCase):
    def test_stops_the_only_running_emulator(self):
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("stop-emulator.sh")
        self.assertSucceeded(result)
        self.assertEqual(self.sandbox.running(), {})
        self.assertEqual(self.kills(), [serial])
        self.assertIn(f"Stopped 'tv_api25' ({serial}).", result.err)
        self.assertEqual(result.out, "", "stdout stays empty")

    def test_by_avd_name_leaves_the_others_running(self):
        phone = self.sandbox.start_emulator("phone")
        tv = self.sandbox.start_emulator("tv_api25")
        self.assertSucceeded(self.sandbox.run("stop-emulator.sh", "tv_api25"))
        self.assertEqual(self.sandbox.running(), {phone: "phone"})
        self.assertEqual(self.kills(), [tv])

    def test_by_serial(self):
        first = self.sandbox.start_emulator("tv_api25")
        second = self.sandbox.start_emulator("tv_api30")
        self.assertSucceeded(self.sandbox.run("stop-emulator.sh", second))
        self.assertEqual(self.sandbox.running(), {first: "tv_api25"})

    def test_android_serial_picks_one(self):
        first = self.sandbox.start_emulator("tv_api25")
        second = self.sandbox.start_emulator("tv_api30")
        self.assertSucceeded(self.sandbox.run("stop-emulator.sh", env={"ANDROID_SERIAL": first}))
        self.assertEqual(self.sandbox.running(), {second: "tv_api30"})

    def test_refuses_to_guess_between_several_emulators(self):
        first = self.sandbox.start_emulator("tv_api25")
        second = self.sandbox.start_emulator("tv_api30")
        for how in ("absolute", "path"):
            with self.subTest(how=how):
                result = self.sandbox.run("stop-emulator.sh", how=how)
                self.assertFailed(result, f"{first} (tv_api25), {second} (tv_api30)", code=1)
                command = "stop-emulator.sh" if how == "path" else str(
                    self.sandbox.tools / "bin" / "stop-emulator.sh")
                self.assertIn(f"Pass one: {command} <avd-name|serial>, or stop them all with --all",
                              result.err)
        self.assertEqual(len(self.sandbox.running()), 2)
        self.assertEqual(self.kills(), [])

    def test_all_stops_every_emulator_and_no_device(self):
        self.sandbox.start_emulator("tv_api25")
        self.sandbox.start_emulator("phone")
        self.sandbox.connect_device("R58M123ABC")
        result = self.sandbox.run("stop-emulator.sh", "--all")
        self.assertSucceeded(result)
        self.assertEqual(self.sandbox.running(), {})
        self.assertEqual(result.err.count("Stopped "), 2)
        self.assertNotIn("R58M123ABC", [argv[1] for argv in self.sandbox.argvs("adb")
                                        if argv[:1] == ["-s"]])

    def test_refuses_a_physical_device(self):
        self.sandbox.start_emulator("tv_api25")
        self.sandbox.connect_device("R58M123ABC")
        for env in ({}, {"ANDROID_SERIAL": "R58M123ABC"}):
            with self.subTest(env=env):
                args = () if env else ("R58M123ABC",)
                result = self.sandbox.run("stop-emulator.sh", *args, env=env)
                self.assertFailed(result, "'R58M123ABC' is a device, not an emulator", code=1)
        self.assertEqual(self.kills(), [])

    def test_an_unknown_avd_name_is_an_error(self):
        # A typo must not pass as "not running".
        result = self.sandbox.run("stop-emulator.sh", "tv_api2")
        self.assertFailed(result, "there's no AVD named 'tv_api2'", code=1)

    def test_wrong_arguments(self):
        for args in (("--bogus",), ("--all", "tv_api25"), ("tv_api25", "tv_api30"),
                     ("--no-save", "--all", "tv_api25")):
            with self.subTest(args=args):
                result = self.sandbox.run("stop-emulator.sh", *args)
                self.assertEqual(result.code, 2, result.output)
                self.assertIn("Usage: stop-emulator.sh", result.err)


class SavesFirst(StopTestCase):
    """Before `adb emu kill`, which doesn't shut Android down, the script has Android save what it
    hasn't yet: up to API 31 `dumpsys package write`, from API 33 on (where that command writes
    nothing) a wait for Android's own write of the app states, then `sync` for the filesystem's
    journal. Best effort, never on a -read-only emulator."""

    def shell_calls(self, serial):
        """The adb shell commands sent to serial before its `adb emu kill`, in order."""
        calls = []
        for argv in self.sandbox.argvs("adb"):
            if argv[:2] == ["-s", serial] and argv[2:] == ["emu", "kill"]:
                return calls
            if argv[:2] == ["-s", serial] and argv[2:3] == ["shell"]:
                calls.append(" ".join(argv[3:]))
        self.fail(f"{serial} got no adb emu kill")

    def test_up_to_api_31_writes_at_once_then_syncs(self):
        self.sandbox.set_behavior(api_level=31)
        serial = self.sandbox.start_emulator("tv_api30")
        result = self.sandbox.run("stop-emulator.sh", "tv_api30")
        self.assertSucceeded(result)
        self.assertEqual(self.shell_calls(serial),
                         ["getprop sys.boot_completed", "dumpsys package write", "sync"])
        self.assertIn(f"Saved what Android hadn't saved yet on 'tv_api30' ({serial}).", result.err)
        self.assertNotIn("Waiting", result.err)
        self.assertEqual(self.sandbox.running(), {})

    def test_from_api_33_waits_for_androids_own_write(self):
        self.sandbox.set_behavior(api_level=36, pending_package_write=2)
        serial = self.sandbox.start_emulator("tv_api30")
        started = time.monotonic()
        result = self.sandbox.run("stop-emulator.sh", "tv_api30", env={"ADT_SAVE_WAIT": "10"})
        took = time.monotonic() - started
        self.assertSucceeded(result)
        self.assertIn("Waiting up to 10 s for Android to save app states changed in the last 10 s",
                      result.err)
        self.assertGreaterEqual(took, 2)
        self.assertLess(took, 8, "the write ends the wait")
        calls = self.shell_calls(serial)
        self.assertEqual(calls[-1], "sync")
        self.assertIn("logcat -b events -d -v epoch -s commit_sys_config_file", calls)
        self.assertIn("Saved what Android hadn't saved yet", result.err)

    def test_from_api_33_waits_save_wait_when_nothing_is_pending(self):
        self.sandbox.set_behavior(api_level=34)
        serial = self.sandbox.start_emulator("tv_api30")
        started = time.monotonic()
        result = self.sandbox.run("stop-emulator.sh", "tv_api30", env={"ADT_SAVE_WAIT": "2"})
        self.assertSucceeded(result)
        self.assertGreaterEqual(time.monotonic() - started, 2)
        self.assertEqual(self.shell_calls(serial)[-1], "sync")
        self.assertEqual(self.sandbox.running(), {})

    def test_nothing_to_save_on_a_read_only_emulator(self):
        serial = self.sandbox.start_emulator("tv_api30", "-read-only")
        result = self.sandbox.run("stop-emulator.sh", serial)
        self.assertSucceeded(result)
        self.assertEqual(self.shell_calls(serial), [])
        self.assertIn("is -read-only, so it keeps no changes: nothing to save", result.err)

    def test_an_emulator_that_does_not_answer_is_stopped_unsaved(self):
        self.sandbox.set_behavior(adb_hangs={"getprop": 1})
        serial = self.sandbox.start_emulator("tv_api30")
        result = self.sandbox.run("stop-emulator.sh", "tv_api30", env={"ADT_ADB_TIMEOUT": "1"})
        self.assertSucceeded(result)
        self.assertEqual(self.shell_calls(serial), ["getprop sys.boot_completed"])
        self.assertIn("didn't answer as a booted device, so changes Android hasn't saved yet are "
                      "lost", result.err)
        self.assertEqual(self.sandbox.running(), {})

    def test_says_so_when_sync_fails(self):
        self.sandbox.set_behavior(sync_error="sync: permission denied")
        self.sandbox.start_emulator("tv_api30")
        result = self.sandbox.run("stop-emulator.sh", "tv_api30")
        self.assertSucceeded(result)
        self.assertIn("(sync failed); they may be lost", result.err)

    def test_no_save_stops_without_saving(self):
        self.sandbox.set_behavior(api_level=31)
        for how in ("name", "serial"):
            with self.subTest(how=how):
                serial = self.sandbox.start_emulator("tv_api30")
                target = "tv_api30" if how == "name" else serial
                result = self.sandbox.run("stop-emulator.sh", "--no-save", target)
                self.assertSucceeded(result)
                self.assertEqual(self.shell_calls(serial), [], "no getprop, dumpsys or sync")
                self.assertIn(f"Not saving on 'tv_api30' ({serial}) (--no-save): changes Android "
                              "hasn't saved yet are lost.", result.err)
                self.assertNotIn("Saved what Android", result.err)
                self.assertEqual(self.sandbox.running(), {})

    def test_no_save_with_all_skips_the_wait_from_api_33_on(self):
        self.sandbox.set_behavior(api_level=36, pending_package_write=8)
        first = self.sandbox.start_emulator("tv_api25")
        second = self.sandbox.start_emulator("tv_api30")
        started = time.monotonic()
        result = self.sandbox.run("stop-emulator.sh", "--all", "--no-save",
                                  env={"ADT_SAVE_WAIT": "10"})
        self.assertSucceeded(result)
        self.assertLess(time.monotonic() - started, 6, "no wait for Android's write")
        for serial in (first, second):
            self.assertEqual(self.shell_calls(serial), [])
        self.assertEqual(result.err.count("(--no-save)"), 2)
        self.assertNotIn("Waiting", result.err)
        self.assertEqual(self.sandbox.running(), {})

    def test_rejects_a_save_wait_that_is_not_a_number(self):
        result = self.sandbox.run("stop-emulator.sh", env={"ADT_SAVE_WAIT": "soon"})
        self.assertFailed(result, "ADT_SAVE_WAIT must be a number of seconds, not 'soon'", code=1)


class NotRunning(StopTestCase):
    """Nothing to stop is success, so the script can be called just in case."""

    def test_no_emulator_at_all(self):
        for args in ((), ("--all",)):
            with self.subTest(args=args):
                result = self.sandbox.run("stop-emulator.sh", *args)
                self.assertSucceeded(result)
                self.assertIn("No emulator is running.", result.err)

    def test_an_avd_or_serial_that_is_not_running(self):
        self.sandbox.start_emulator("tv_api30")
        for target in ("tv_api25", "emulator-5560"):
            with self.subTest(target=target):
                result = self.sandbox.run("stop-emulator.sh", target)
                self.assertSucceeded(result)
                self.assertIn(f"'{target}' isn't running.", result.err)
        self.assertEqual(self.kills(), [])
        self.assertEqual(list(self.sandbox.running().values()), ["tv_api30"])

    def test_a_stale_lock_file_is_left_alone(self):
        # An emulator killed with SIGKILL leaves its lock file behind, and its PID may since
        # belong to another process: that process must not be touched.
        other = self.a_process()
        self.lock(self.tv).write_bytes(str(other.pid).encode().ljust(8, b"\0"))
        result = self.sandbox.run("stop-emulator.sh", "tv_api25", env={"ADT_STOP_TIMEOUT": "1"})
        self.assertSucceeded(result)
        self.assertIn("'tv_api25' isn't running.", result.err)
        self.assertIsNone(other.poll(), "a process that isn't the emulator was stopped")

    def test_a_stale_discovery_file_is_left_alone(self):
        # An emulator killed with SIGKILL leaves its discovery file behind too, and its PID may
        # since belong to another process. The other process is left alone whether the file is
        # found by the AVD's name or by the serial of a running emulator whose console hangs.
        self.sandbox.set_behavior(emulator_lock_file=False, emulator_discovery_file=False,
                                  emulator_console_hangs=True)
        serial = self.sandbox.start_emulator("tv_api30")
        other = self.a_process()
        stale = self.sandbox.discovery_file(other.pid)
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text(f"avd.name=tv_api25\nport.serial={serial[9:]}\n")
        env = {"ADT_ADB_TIMEOUT": "1", "ADT_STOP_TIMEOUT": "1"}
        result = self.sandbox.run("stop-emulator.sh", "tv_api25", env=env)
        self.assertSucceeded(result)
        self.assertIn("'tv_api25' isn't running.", result.err)
        result = self.sandbox.run("stop-emulator.sh", serial, env=env)
        self.assertFailed(result, "its process wasn't found to kill it. It's still running.",
                          code=1)
        self.assertIsNone(other.poll(), "a process that isn't the emulator was stopped")

    def test_a_stale_lock_file_of_a_process_that_is_gone(self):
        gone = subprocess.Popen(["true"])
        gone.wait()
        self.lock(self.tv).write_bytes(str(gone.pid).encode().ljust(8, b"\0"))
        result = self.sandbox.run("stop-emulator.sh", "tv_api25")
        self.assertSucceeded(result)
        self.assertEqual(result.err, "'tv_api25' isn't running.\n")


class AlreadyExiting(StopTestCase):
    """An emulator that's exiting, or has exited, when the script gets to it (e.g. killed with the
    test run that started it): its console no longer answers, and adb lists it, as offline, until
    it notices a moment later. That's no failure to stop it, and it isn't "still running"."""

    def setUp(self):
        super().setUp()
        self.sandbox.set_behavior(adb_lists_exited=10)

    def assertNoLongerListed(self, serial):
        listed = self.sandbox.bash("running_emulators", env={"FAKE_NO_LOG": "1"}).out
        self.assertNotIn(serial, listed, "returned while adb still listed it")

    def test_by_serial_once_its_process_has_exited(self):
        for reap in (False, True):   # a zombie, or a process that's gone
            with self.subTest(reap=reap):
                serial = self.sandbox.start_emulator("tv_api25")
                self.sandbox.kill_emulator(serial, reap=reap)
                result = self.sandbox.run("stop-emulator.sh", serial)
                self.assertSucceeded(result)
                self.assertEqual(result.err, f"'{serial}' didn't answer adb emu kill, and adb no "
                                 "longer lists it: it was exiting already. Its process wasn't "
                                 "found, so it may not have exited yet.\n")
                self.assertNoLongerListed(serial)

    def test_all_once_its_process_has_exited(self):
        exited = self.sandbox.start_emulator("tv_api25")
        other = self.sandbox.start_emulator("tv_api30")
        self.sandbox.kill_emulator(exited)
        result = self.sandbox.run("stop-emulator.sh", "--all")
        self.assertSucceeded(result)
        self.assertIn(f"'{exited}' didn't answer adb emu kill, and adb no longer lists it",
                      result.err)
        self.assertIn(f"Stopped 'tv_api30' ({other}).", result.err)
        self.assertNoLongerListed(exited)
        self.assertEqual(self.sandbox.running(), {})

    def test_by_avd_name_while_it_exits(self):
        # Its console still says its name, but no longer answers adb emu kill; a -read-only
        # emulator whose console can't name its PID, and whose discovery file isn't found, leaves
        # adb as the only thing to wait on.
        self.sandbox.set_behavior(emulator_already_exiting=True, emulator_lock_file=False,
                                  emulator_discoverable=False, emulator_discovery_file=False)
        serial = self.sandbox.start_emulator("tv_api25")
        pid = self.sandbox.emulator_pid(serial)
        result = self.sandbox.run("stop-emulator.sh", "tv_api25")
        self.assertSucceeded(result)
        self.assertFalse(self.sandbox.alive(pid))
        self.assertIn(f"'tv_api25' ({serial}) didn't answer adb emu kill, and adb no longer lists "
                      "it: it was exiting already.", result.err)
        self.assertNoLongerListed(serial)


class WaitsForTheProcess(StopTestCase):
    def test_returns_only_once_the_emulator_has_exited(self):
        # The emulator keeps running for a while after `adb emu kill`, saving its snapshot.
        self.sandbox.set_behavior(emulator_exit_delay=1)
        serial = self.sandbox.start_emulator("tv_api25")
        pid = self.sandbox.emulator_pid(serial)
        self.assertTrue(self.lock(self.tv).exists())
        self.assertSucceeded(self.sandbox.run("stop-emulator.sh", "tv_api25"))
        self.assertFalse(self.sandbox.alive(pid), "returned while the emulator still ran")
        self.assertFalse(self.lock(self.tv).exists())
        self.assertEqual(self.sandbox.running(), {})

    def test_a_read_only_emulator_without_a_lock_file(self):
        # Started with -read-only, the emulator writes no lock file: its console names its PID.
        self.sandbox.set_behavior(emulator_lock_file=False, emulator_exit_delay=1)
        serial = self.sandbox.start_emulator("tv_api25", "-read-only")
        pid = self.sandbox.emulator_pid(serial)
        result = self.sandbox.run("stop-emulator.sh", "tv_api25")
        self.assertSucceeded(result)
        self.assertFalse(self.sandbox.alive(pid), "returned while the emulator still ran")
        self.assertEqual(result.err, f"'tv_api25' ({serial}) is -read-only, so it keeps no changes: "
                                     f"nothing to save.\nStopped 'tv_api25' ({serial}).\n")

    def test_waits_for_the_instance_it_stops_when_an_avd_runs_twice(self):
        # Several -read-only instances of one AVD can run: only the one stopped is waited for.
        self.sandbox.set_behavior(emulator_lock_file=False, emulator_exit_delay=1)
        first = self.sandbox.start_emulator("tv_api25", "-read-only")
        second = self.sandbox.start_emulator("tv_api25", "-read-only")
        pid = self.sandbox.emulator_pid(second)
        result = self.sandbox.run("stop-emulator.sh", second)
        self.assertSucceeded(result)
        self.assertFalse(self.sandbox.alive(pid), "returned while the emulator still ran")
        self.assertEqual(result.err, f"'tv_api25' ({second}) is -read-only, so it keeps no changes: "
                                     f"nothing to save.\nStopped 'tv_api25' ({second}).\n")
        self.assertEqual(self.sandbox.running(), {first: "tv_api25"})

    def test_says_so_when_it_cannot_confirm_the_exit(self):
        # No lock file, no discovery file, and a console that can't name the PID: adb is all
        # that's left to wait on.
        self.sandbox.set_behavior(emulator_lock_file=False, emulator_discoverable=False,
                                  emulator_discovery_file=False)
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("stop-emulator.sh", "tv_api25")
        self.assertSucceeded(result)
        self.assertIn(f"adb no longer lists 'tv_api25' ({serial}), but its process wasn't found, "
                      "so it may still be exiting.", result.err)
        self.assertNotIn("Stopped", result.err)

    def test_kills_an_emulator_that_does_not_exit_in_time(self):
        self.sandbox.set_behavior(emulator_stuck=True)
        serial = self.sandbox.start_emulator("tv_api25")
        pid = self.sandbox.emulator_pid(serial)
        result = self.sandbox.run("stop-emulator.sh", env={"ADT_STOP_TIMEOUT": "1"})
        self.assertSucceeded(result)
        self.assertFalse(self.sandbox.alive(pid))
        self.assertIn(f"'tv_api25' ({serial}) didn't exit within 1 s of adb emu kill, so it was "
                      "killed (SIGKILL); its Quick Boot snapshot wasn't saved.", result.err)

    def test_waits_at_least_the_whole_time_limit(self):
        # $SECONDS counts whole seconds: from `adb emu kill` late in one, a 1 s limit counted in
        # it would end at the next.
        self.sandbox.set_behavior(emulator_stuck=True, adb_late=["emu_kill"])
        self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("stop-emulator.sh", env={"ADT_STOP_TIMEOUT": "1"})
        self.assertSucceeded(result)
        self.assertIn("killed (SIGKILL)", result.err)
        calls = self.sandbox.calls("adb")
        [kill] = [i for i, call in enumerate(calls) if call["argv"][2:] == ["emu", "kill"]]
        # The next adb call comes after the kill (SIGKILL): until then only the process is watched.
        self.assertGreaterEqual(calls[kill + 1]["time"] - calls[kill]["time"], 1)

    def test_sends_sigterm_when_the_console_does_not_answer(self):
        self.sandbox.set_behavior(adb_hangs={"emu_kill": 1})
        serial = self.sandbox.start_emulator("tv_api25")
        pid = self.sandbox.emulator_pid(serial)
        started = time.monotonic()
        result = self.sandbox.run("stop-emulator.sh", "tv_api25",
                                  env={"ADT_ADB_TIMEOUT": "1", "ADT_STOP_TIMEOUT": "20"})
        self.assertSucceeded(result)
        self.assertFalse(self.sandbox.alive(pid))
        self.assertLess(time.monotonic() - started, 15, "waited for the time limit")
        self.assertIn(f"'tv_api25' ({serial}) didn't answer adb emu kill, so it was stopped with "
                      "SIGTERM.", result.err)

    def test_stops_an_emulator_adb_does_not_list_by_its_avd_name(self):
        self.sandbox.set_behavior(emulator_hidden=True)
        serial = self.sandbox.start_emulator("tv_api25")
        pid = self.sandbox.emulator_pid(serial)
        result = self.sandbox.run("stop-emulator.sh", "tv_api25")
        self.assertSucceeded(result)
        self.assertFalse(self.sandbox.alive(pid))
        self.assertIn("'tv_api25' isn't listed by adb, or its console doesn't answer, so it was "
                      "stopped with SIGTERM.", result.err)

    def test_a_hung_console_by_avd_name(self):
        # The console answers nothing, not even the AVD's name: the lock file still finds it.
        self.sandbox.set_behavior(adb_hangs={"avd_name": 99, "emu_kill": 99})
        serial = self.sandbox.start_emulator("tv_api25")
        pid = self.sandbox.emulator_pid(serial)
        result = self.sandbox.run("stop-emulator.sh", "tv_api25",
                                  env={"ADT_ADB_TIMEOUT": "1", "ADT_STOP_TIMEOUT": "1"})
        self.assertSucceeded(result)
        self.assertFalse(self.sandbox.alive(pid))

    def test_a_hung_console_by_serial_asks_for_the_avd_name(self):
        # Without a discovery file, only the AVD's name leads to its process (the lock file).
        self.sandbox.set_behavior(adb_hangs={"avd_name": 99, "emu_kill": 99},
                                  emulator_discovery_file=False)
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("stop-emulator.sh", serial,
                                  env={"ADT_ADB_TIMEOUT": "1", "ADT_STOP_TIMEOUT": "1"})
        self.assertFailed(result, f"'{serial}' didn't answer adb emu kill, and its process wasn't "
                          "found to kill it. It's still running.", code=1)
        self.assertIn("Name its AVD instead", result.err)
        self.assertEqual(self.sandbox.running(), {serial: "tv_api25"})

    def test_a_hung_read_only_emulator(self):
        # No lock file, and a console that answers nothing: its discovery file names its PID,
        # by its AVD's name and by its serial alike.
        for target in ("tv_api25", "emulator-5554", None):   # None: the only running emulator
            with self.subTest(target=target):
                self.sandbox.set_behavior(emulator_lock_file=False, emulator_console_hangs=True)
                serial = self.sandbox.start_emulator("tv_api25")
                pid = self.sandbox.emulator_pid(serial)
                result = self.sandbox.run("stop-emulator.sh", *[target] if target else [],
                                          env={"ADT_ADB_TIMEOUT": "1", "ADT_STOP_TIMEOUT": "20"})
                self.assertSucceeded(result)
                self.assertFalse(self.sandbox.alive(pid))
                self.assertIn("so it was stopped with SIGTERM.", result.err)
                self.assertFalse(self.sandbox.discovery_file(pid).exists())

    def test_a_hung_read_only_emulator_by_serial_says_its_avd(self):
        self.sandbox.set_behavior(emulator_lock_file=False, emulator_console_hangs=True)
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("stop-emulator.sh", serial,
                                  env={"ADT_ADB_TIMEOUT": "1", "ADT_STOP_TIMEOUT": "20"})
        self.assertSucceeded(result)
        self.assertIn(f"'tv_api25' ({serial}) didn't answer adb emu kill, so it was stopped with "
                      "SIGTERM.", result.err)

    def test_stops_only_the_hung_read_only_instance_named_by_serial(self):
        self.sandbox.set_behavior(emulator_lock_file=False, emulator_console_hangs=True)
        hung = self.sandbox.start_emulator("tv_api25")
        self.sandbox.set_behavior(emulator_console_hangs=False)
        other = self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("stop-emulator.sh", hung,
                                  env={"ADT_ADB_TIMEOUT": "1", "ADT_STOP_TIMEOUT": "20"})
        self.assertSucceeded(result)
        self.assertEqual(self.sandbox.running(), {other: "tv_api25"})
        self.assertEqual(self.kills(), [hung])

    def test_does_not_guess_between_hung_read_only_instances_by_avd_name(self):
        # The AVD's name alone matches each of them: stopping one picked at random, or all of
        # them, isn't what was asked.
        self.sandbox.set_behavior(emulator_lock_file=False, emulator_console_hangs=True)
        first = self.sandbox.start_emulator("tv_api25")
        second = self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("stop-emulator.sh", "tv_api25",
                                  env={"ADT_ADB_TIMEOUT": "1", "ADT_STOP_TIMEOUT": "1"})
        self.assertFailed(result, f"'tv_api25' runs as several emulators whose consoles don't "
                          f"answer ({first}, {second}): name the one to stop by its serial.",
                          code=1)
        self.assertEqual(self.sandbox.running(), {first: "tv_api25", second: "tv_api25"})

    def test_fails_when_the_emulator_is_still_running(self):
        # Without its lock file, its discovery file or its console naming it, the stuck
        # emulator's process can't be found to kill it.
        self.sandbox.set_behavior(emulator_stuck=True, emulator_discoverable=False,
                                  emulator_discovery_file=False)
        serial = self.sandbox.start_emulator("tv_api25")
        self.lock(self.tv).unlink()
        result = self.sandbox.run("stop-emulator.sh", env={"ADT_STOP_TIMEOUT": "1"})
        self.assertFailed(result, f"'tv_api25' ({serial}) didn't exit within 1 s of adb emu kill, "
                          "and its process wasn't found to kill it. It's still running.", code=1)
        self.assertEqual(self.sandbox.running(), {serial: "tv_api25"})

    def test_all_reports_a_failure_but_stops_the_others(self):
        self.sandbox.set_behavior(emulator_stuck=True, emulator_discoverable=False,
                                  emulator_discovery_file=False)
        stuck = self.sandbox.start_emulator("tv_api25")
        self.lock(self.tv).unlink()
        self.sandbox.set_behavior(emulator_stuck=False, emulator_discoverable=True,
                                  emulator_discovery_file=True)
        other = self.sandbox.start_emulator("tv_api30")
        result = self.sandbox.run("stop-emulator.sh", "--all", env={"ADT_STOP_TIMEOUT": "1"})
        self.assertEqual(result.code, 1, result.output)
        self.assertIn(f"Stopped 'tv_api30' ({other}).", result.err)
        self.assertEqual(self.sandbox.running(), {stuck: "tv_api25"})

    def test_rejects_a_time_limit_that_is_not_a_number(self):
        result = self.sandbox.run("stop-emulator.sh", env={"ADT_STOP_TIMEOUT": "1m"})
        self.assertFailed(result, "ADT_STOP_TIMEOUT must be a number of seconds, not '1m'", code=1)
