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
        for args in (("--bogus",), ("--all", "tv_api25"), ("tv_api25", "tv_api30")):
            with self.subTest(args=args):
                result = self.sandbox.run("stop-emulator.sh", *args)
                self.assertEqual(result.code, 2, result.output)
                self.assertIn("Usage: stop-emulator.sh", result.err)


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

    def test_kills_an_emulator_that_does_not_exit_in_time(self):
        self.sandbox.set_behavior(emulator_stuck=True)
        serial = self.sandbox.start_emulator("tv_api25")
        pid = self.sandbox.emulator_pid(serial)
        result = self.sandbox.run("stop-emulator.sh", env={"ADT_STOP_TIMEOUT": "1"})
        self.assertSucceeded(result)
        self.assertFalse(self.sandbox.alive(pid))
        self.assertIn(f"'tv_api25' ({serial}) didn't exit within 1 s of adb emu kill, so it was "
                      "killed (SIGKILL); its Quick Boot snapshot wasn't saved.", result.err)

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
        self.sandbox.set_behavior(adb_hangs={"avd_name": 99, "emu_kill": 99})
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("stop-emulator.sh", serial,
                                  env={"ADT_ADB_TIMEOUT": "1", "ADT_STOP_TIMEOUT": "1"})
        self.assertFailed(result, f"'{serial}' didn't answer adb emu kill, and its process wasn't "
                          "found to kill it. It's still running.", code=1)
        self.assertIn("Name its AVD instead", result.err)
        self.assertEqual(self.sandbox.running(), {serial: "tv_api25"})

    def test_fails_when_the_emulator_is_still_running(self):
        # Without its lock file, the stuck emulator's process can't be found to kill it.
        self.sandbox.set_behavior(emulator_stuck=True)
        serial = self.sandbox.start_emulator("tv_api25")
        self.lock(self.tv).unlink()
        result = self.sandbox.run("stop-emulator.sh", env={"ADT_STOP_TIMEOUT": "1"})
        self.assertFailed(result, f"'tv_api25' ({serial}) didn't exit within 1 s of adb emu kill, "
                          "and its process wasn't found to kill it. It's still running.", code=1)
        self.assertEqual(self.sandbox.running(), {serial: "tv_api25"})

    def test_all_reports_a_failure_but_stops_the_others(self):
        self.sandbox.set_behavior(emulator_stuck=True)
        stuck = self.sandbox.start_emulator("tv_api25")
        self.lock(self.tv).unlink()
        self.sandbox.set_behavior(emulator_stuck=False)
        other = self.sandbox.start_emulator("tv_api30")
        result = self.sandbox.run("stop-emulator.sh", "--all", env={"ADT_STOP_TIMEOUT": "1"})
        self.assertEqual(result.code, 1, result.output)
        self.assertIn(f"Stopped 'tv_api30' ({other}).", result.err)
        self.assertEqual(self.sandbox.running(), {stuck: "tv_api25"})

    def test_rejects_a_time_limit_that_is_not_a_number(self):
        result = self.sandbox.run("stop-emulator.sh", env={"ADT_STOP_TIMEOUT": "1m"})
        self.assertFailed(result, "ADT_STOP_TIMEOUT must be a number of seconds, not '1m'", code=1)
