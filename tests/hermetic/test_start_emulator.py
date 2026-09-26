"""start-emulator.sh: choosing the AVD, booting it, finding its serial and reporting problems."""
import os
import threading
import time

from support.sandbox import ScriptTestCase


class EmulatorTestCase(ScriptTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.install_sdk()

    def start(self, *args, env=None):
        return self.sandbox.run("start-emulator.sh", *args, env=env)

    def launched(self):
        """Argument lists the emulator was started with (not -list-avds queries)."""
        return [argv for argv in self.sandbox.argvs("emulator") if argv[:1] == ["-avd"]]


class ChoosesTheAvd(EmulatorTestCase):
    def booted_avd(self, result):
        self.assertSucceeded(result)
        [argv] = self.launched()
        return argv[1]

    def test_tv_api25_by_default(self):
        self.sandbox.add_avd("other_tv")
        self.sandbox.add_avd("tv_api25")
        self.assertEqual(self.booted_avd(self.start()), "tv_api25")

    def test_the_name_given(self):
        self.sandbox.add_avd("tv_api25")
        self.sandbox.add_avd("my_tv")
        self.assertEqual(self.booted_avd(self.start("my_tv")), "my_tv")

    def test_adt_avd_when_no_name_is_given(self):
        self.sandbox.add_avd("tv_api25")
        self.sandbox.add_avd("den_tv")
        self.assertEqual(self.booted_avd(self.start(env={"ADT_AVD": "den_tv"})), "den_tv")

    def test_the_only_tv_avd_when_there_is_no_tv_api25(self):
        self.sandbox.add_avd("pixel", tv=False)
        self.sandbox.add_avd("living_room")
        self.sandbox.add_avd("tablet", tv=False)
        self.assertEqual(self.booted_avd(self.start()), "living_room")

    def test_a_google_tv_avd_counts_as_a_tv_avd(self):
        self.sandbox.add_avd("pixel", tv=False)
        self.sandbox.add_avd("gtv_api36", tag="google-tv")
        self.assertEqual(self.booted_avd(self.start()), "gtv_api36")

    def test_leading_flags_go_to_the_emulator_and_keep_the_default_avd(self):
        self.sandbox.add_avd("tv_api25")
        self.assertSucceeded(self.start("-wipe-data", "-no-snapshot-load"))
        [argv] = self.launched()
        self.assertEqual(argv[1], "tv_api25")
        self.assertIn("-wipe-data", argv)
        self.assertEqual(argv.count("-no-snapshot-load"), 1, "not added a second time")

    def test_quick_before_or_after_the_name(self):
        self.sandbox.add_avd("tv_api25")
        self.sandbox.add_avd("my_tv")
        for args in (["--quick", "my_tv"], ["my_tv", "--quick", "-no-window"]):
            with self.subTest(args):
                self.assertSucceeded(self.start(*args))
                argv = self.launched()[-1]
                self.assertEqual(argv[1], "my_tv")
                self.assertNotIn("--quick", argv)
                self.sandbox.stop_emulators()

    def test_refuses_to_guess_between_several_tv_avds(self):
        self.sandbox.add_avd("tv_a")
        self.sandbox.add_avd("tv_b")
        result = self.start()
        self.assertFailed(result, "can't tell which AVD to use")
        self.assertIn("tv_a tv_b", result.output)
        self.assertEqual(self.launched(), [])

    def test_unknown_name_lists_the_avds(self):
        self.sandbox.add_avd("tv_api25")
        result = self.start("tv_typo")
        self.assertFailed(result, "no AVD named 'tv_typo'")
        self.assertIn("AVDs found: tv_api25", result.output)

    def test_no_avds_suggests_creating_one(self):
        result = self.sandbox.run("start-emulator.sh", how="relative")
        self.assertFailed(result, "AVDs found: none")
        self.assertIn("create one with\n../tools/bin/create-avd.sh", result.output)

    def test_avds_in_a_custom_avd_home(self):
        home = self.sandbox.root / "avds"
        self.sandbox.add_avd("tv_api25", home=home)
        result = self.start(env={"ANDROID_AVD_HOME": str(home)})
        self.assertEqual(self.booted_avd(result), "tv_api25")


class Boots(EmulatorTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def test_returns_only_once_android_has_booted(self):
        self.sandbox.set_behavior(boot_polls=2)
        result = self.start()
        self.assertSucceeded(result)
        self.assertIn("booted as emulator-5554", result.err)
        polls = [a for a in self.sandbox.argvs("adb") if "sys.boot_completed" in a]
        self.assertEqual(len(polls), 3, "polled until the third answer said booted")
        self.assertEqual(self.sandbox.running(), {"emulator-5554": "tv_api25"},
                         "the emulator keeps running after the script exits")

    def test_software_rendering_without_audio_or_boot_animation(self):
        self.assertSucceeded(self.start())
        [argv] = self.launched()
        self.assertEqual(argv[argv.index("-gpu") + 1], "swiftshader_indirect")
        self.assertIn("-no-audio", argv)
        self.assertIn("-no-boot-anim", argv)

    def test_own_gpu_flag_replaces_the_default(self):
        self.assertSucceeded(self.start("tv_api25", "-gpu", "host"))
        [argv] = self.launched()
        self.assertEqual(argv.count("-gpu"), 1)
        self.assertEqual(argv[argv.index("-gpu") + 1], "host")

    def test_logs_to_tmpdir(self):
        result = self.start()
        log = self.sandbox.tmp / "emulator-tv_api25.log"
        self.assertIn(str(log), result.err)
        self.assertIn("Android emulator version", log.read_text())

    def test_already_running_avd_is_reported_not_started_again(self):
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.start()
        self.assertSucceeded(result)
        self.assertIn(f"already running as {serial}", result.err)
        self.assertEqual(self.launched(), [])

    def test_crash_during_boot_is_reported_with_the_log(self):
        self.sandbox.set_behavior(emulator_crash="unknown option: -bogus")
        started = time.time()
        result = self.start("-bogus")
        self.assertFailed(result, "the emulator exited")
        self.assertIn("unknown option: -bogus", result.output, "the log's last lines are shown")
        self.assertLess(time.time() - started, 10, "must not keep waiting for a dead emulator")


class ColdOrQuickBoot(EmulatorTestCase):
    """Cold boot by default; --quick boots from the Quick Boot snapshot, whose restored adb
    connection can stay offline, so --quick watches for that (ADT_OFFLINE_TIMEOUT, 30 s)."""

    FAST = {"ADT_OFFLINE_TIMEOUT": "1"}

    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def reconnects(self):
        return [argv for argv in self.sandbox.argvs("adb") if argv[:1] == ["reconnect"]]

    def test_cold_boot_by_default(self):
        result = self.start()
        self.assertSucceeded(result)
        [argv] = self.launched()
        self.assertIn("-no-snapshot-load", argv)
        self.assertIn("cold boot", result.err)

    def test_quick_boots_from_the_snapshot(self):
        result = self.start("--quick")
        self.assertSucceeded(result)
        [argv] = self.launched()
        self.assertNotIn("-no-snapshot-load", argv)
        self.assertNotIn("--quick", argv, "the emulator doesn't know --quick")
        self.assertIn("Quick Boot", result.err)

    def test_quick_reconnects_adb_when_the_restored_emulator_stays_offline(self):
        self.sandbox.set_behavior(adb_offline="until_reconnect")
        result = self.start("--quick", env=self.FAST)
        self.assertSucceeded(result)
        self.assertIn("booted as emulator-5554", result.err)
        self.assertEqual(self.reconnects(), [["reconnect", "offline"]])

    def test_quick_gives_up_when_reconnecting_does_not_help(self):
        self.sandbox.set_behavior(adb_offline="forever")
        started = time.time()
        result = self.start("--quick", env=self.FAST)
        self.assertFailed(result, "adb can't reach it")
        self.assertIn("adb -s emulator-5554 emu kill", result.output)
        self.assertIn("without --quick", result.output)
        self.assertEqual(len(self.reconnects()), 1, "reconnects once, then gives up")
        self.assertLess(time.time() - started, 20)

    def test_a_slow_cold_boot_is_waited_for_without_reconnecting(self):
        # adbd isn't up yet early in a cold boot, so "offline" for a while is normal there.
        self.sandbox.set_behavior(adb_offline=2)
        result = self.start(env=self.FAST)
        self.assertSucceeded(result)
        self.assertEqual(self.reconnects(), [])

    def test_quick_does_not_reconnect_a_briefly_offline_emulator(self):
        self.sandbox.set_behavior(adb_offline=1)
        self.assertSucceeded(self.start("--quick"))
        self.assertEqual(self.reconnects(), [])


class BootTimeout(EmulatorTestCase):
    """An emulator that runs but never boots must not keep the script (or a CI job) waiting
    forever: after ADT_BOOT_TIMEOUT seconds it's stopped and the script fails."""

    FAST = {"ADT_BOOT_TIMEOUT": "2"}

    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def assertGaveUp(self, result, started):
        self.assertFailed(result, "didn't finish booting within 2 s")
        self.assertIn("Android emulator version", result.output, "the log's last lines are shown")
        self.assertIn("ADT_BOOT_TIMEOUT", result.output)
        self.assertIn("start-emulator.sh tv_api25 -wipe-data", result.output)
        self.assertEqual(self.sandbox.running(), {}, "the half-booted emulator is stopped")
        self.assertLess(time.time() - started, 15)

    def test_stops_an_emulator_that_never_finishes_booting(self):
        self.sandbox.set_behavior(boot_polls=10**9)
        started = time.time()
        self.assertGaveUp(self.start(env=self.FAST), started)

    def test_stops_an_emulator_that_adb_never_sees(self):
        self.sandbox.set_behavior(emulator_hidden=True)
        started = time.time()
        self.assertGaveUp(self.start(env=self.FAST), started)

    def test_a_retry_after_a_timeout_boots_again(self):
        # The stopped emulator must be gone, or the retry would report it as already running.
        self.sandbox.set_behavior(boot_polls=10**9)
        self.assertFailed(self.start(env=self.FAST), "didn't finish booting")
        self.sandbox.set_behavior(boot_polls=0)
        result = self.start()
        self.assertSucceeded(result)
        self.assertIn("booted as emulator-5554", result.err)
        self.assertEqual(len(self.launched()), 2)

    def test_zero_means_no_limit(self):
        self.sandbox.set_behavior(boot_polls=2)
        self.assertSucceeded(self.start(env={"ADT_BOOT_TIMEOUT": "0"}))

    def test_must_be_a_number_of_seconds(self):
        for value in ("10m", "-1", "1.5"):
            with self.subTest(value=value):
                self.assertFailed(self.start(env={"ADT_BOOT_TIMEOUT": value}),
                                  "ADT_BOOT_TIMEOUT must be a number of seconds")
        self.assertEqual(self.launched(), [], "nothing is started with a bad timeout")


class AlreadyRunning(EmulatorTestCase):
    """An AVD that's already running isn't started again, but it may still be booting (another
    call or CI step started it): the script returns only once Android has booted, as always."""

    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def boot_polls(self):
        return [a for a in self.sandbox.argvs("adb") if "sys.boot_completed" in a]

    def test_waits_until_a_still_booting_emulator_has_booted(self):
        self.sandbox.set_behavior(boot_polls=2)
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.start()
        self.assertSucceeded(result)
        self.assertEqual(result.out, f"{serial}\n")
        self.assertIn(f"already running as {serial}", result.err)
        self.assertEqual(len(self.boot_polls()), 3, "polled until the third answer said booted")
        self.assertEqual(self.launched(), [])

    def test_returns_at_once_when_it_has_booted(self):
        self.sandbox.start_emulator("tv_api25")
        self.assertSucceeded(self.start())
        self.assertEqual(len(self.boot_polls()), 1)

    def test_gives_up_on_one_that_never_boots_but_leaves_it_running(self):
        # It wasn't started here, so it's not this script's to stop.
        self.sandbox.set_behavior(boot_polls=10**9)
        serial = self.sandbox.start_emulator("tv_api25")
        started = time.time()
        result = self.start(env={"ADT_BOOT_TIMEOUT": "2"})
        self.assertFailed(result, "didn't finish booting within 2 s")
        self.assertIn("wasn't started by this script", result.output)
        self.assertEqual(result.out, "")
        self.assertEqual(self.sandbox.running(), {serial: "tv_api25"})
        self.assertLess(time.time() - started, 15)

    def test_stops_waiting_when_it_is_stopped_meanwhile(self):
        self.sandbox.set_behavior(boot_polls=10**9)
        self.sandbox.start_emulator("tv_api25")
        timer = threading.Timer(1, self.sandbox.stop_emulators)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.time()
        result = self.start()
        self.assertFailed(result, "stopped before it finished booting")
        self.assertLess(time.time() - started, 15, "must not wait for the boot timeout")


class PrintsTheSerial(EmulatorTestCase):
    """stdout holds the serial and nothing else, so serial="$(start-emulator.sh)" works; every
    message goes to stderr."""

    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def test_after_booting(self):
        result = self.start()
        self.assertSucceeded(result)
        self.assertEqual(result.out, "emulator-5554\n")
        self.assertIn("booted as emulator-5554", result.err)

    def test_when_it_is_already_running(self):
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.start()
        self.assertSucceeded(result)
        self.assertEqual(result.out, f"{serial}\n")

    def test_with_other_devices_connected_and_on_wsl(self):
        # The ANDROID_SERIAL hint and the toolbar script's report are messages too.
        self.sandbox.add_avd("phone", tv=False)
        self.sandbox.start_emulator("phone")
        self.sandbox.connect_device("R58M123ABC")
        self.sandbox.wsl()
        result = self.start()
        self.assertSucceeded(result)
        self.assertEqual(result.out, "emulator-5556\n")
        self.assertIn("export ANDROID_SERIAL=emulator-5556", result.err)
        self.assertIn("wslg-toolbar: toolbar hidden", result.err)

    def test_with_quick_after_a_reconnect(self):
        self.sandbox.set_behavior(adb_offline="until_reconnect")
        result = self.start("--quick", env={"ADT_OFFLINE_TIMEOUT": "1"})
        self.assertSucceeded(result)
        self.assertEqual(result.out, "emulator-5554\n")

    def test_nothing_on_failure(self):
        self.sandbox.set_behavior(emulator_crash="unknown option: -bogus")
        result = self.start("-bogus")
        self.assertFailed(result, "the emulator exited")
        self.assertEqual(result.out, "")


class WithoutADisplay(EmulatorTestCase):
    """Without $DISPLAY (a CI runner, SSH) the real emulator aborts with nothing in its log that
    says why, so -no-window is added."""

    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def test_runs_without_a_window(self):
        result = self.start(env={"DISPLAY": None})
        self.assertSucceeded(result)
        [argv] = self.launched()
        self.assertEqual(argv.count("-no-window"), 1)
        self.assertIn("No $DISPLAY", result.err)

    def test_an_empty_display_counts_as_none(self):
        self.assertSucceeded(self.start(env={"DISPLAY": ""}))
        [argv] = self.launched()
        self.assertIn("-no-window", argv)

    def test_own_no_window_is_not_repeated(self):
        result = self.start("-no-window", env={"DISPLAY": None})
        self.assertSucceeded(result)
        [argv] = self.launched()
        self.assertEqual(argv.count("-no-window"), 1)
        self.assertNotIn("No $DISPLAY", result.err)

    def test_with_a_display_the_window_stays(self):
        self.assertSucceeded(self.start())
        [argv] = self.launched()
        self.assertNotIn("-no-window", argv)

    def test_no_toolbar_fix_on_wsl_without_a_window(self):
        self.sandbox.wsl()
        self.assertSucceeded(self.start(env={"DISPLAY": None}))
        self.assertEqual([a for a in self.sandbox.argvs("python3")], [])


class FindsItsOwnEmulator(EmulatorTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")
        self.sandbox.add_avd("phone", tv=False)

    def test_serial_of_its_avd_when_another_emulator_runs(self):
        self.sandbox.start_emulator("phone")             # takes emulator-5554
        result = self.start()
        self.assertSucceeded(result)
        self.assertIn("booted as emulator-5556", result.err)
        self.assertIn("export ANDROID_SERIAL=emulator-5556", result.err)

    def test_hint_when_a_physical_device_is_connected(self):
        self.sandbox.connect_device("R58M123ABC")
        result = self.start()
        self.assertSucceeded(result)
        self.assertIn("export ANDROID_SERIAL=emulator-5554", result.err)

    def test_no_hint_when_it_is_the_only_device(self):
        result = self.start()
        self.assertSucceeded(result)
        self.assertNotIn("ANDROID_SERIAL", result.err)

    def test_adb_calls_always_name_the_serial(self):
        self.sandbox.connect_device("R58M123ABC")
        self.assertSucceeded(self.start())
        for argv in self.sandbox.argvs("adb"):
            if argv[:1] != ["devices"]:
                self.assertEqual(argv[:1], ["-s"], f"adb call without -s: {argv}")


class Kvm(EmulatorTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")
        if os.geteuid() == 0:
            self.skipTest("root can write any file, so KVM access can't be taken away")

    def test_missing_kvm_device(self):
        self.sandbox.kvm.unlink()
        result = self.start()
        self.assertFailed(result, "doesn't exist: enable hardware virtualization")
        self.assertEqual(self.launched(), [])

    def test_no_access_and_not_in_the_kvm_group(self):
        self.sandbox.kvm.chmod(0o444)
        result = self.start()
        self.assertFailed(result, "sudo usermod -aG kvm tester")
        self.assertEqual(self.sandbox.argvs("sg"), [])
        self.assertEqual(self.launched(), [])

    def test_a_member_whose_name_contains_the_users_is_not_the_user(self):
        # The group line "kvm:x:<gid>:ci-bot" must not count user "ci" as a member: sg would
        # then ask for a group password.
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(user="ci", kvm_group_members=["ci-bot"])
        result = self.start(env={"USER": "ci"})
        self.assertFailed(result, "sudo usermod -aG kvm ci")
        self.assertEqual(self.sandbox.argvs("sg"), [])

    def test_kvm_as_the_primary_group_reruns_through_sg(self):
        # A primary group's getent line doesn't list its users; the user database does.
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(kvm_primary_group=True)
        self.start()
        [argv] = self.sandbox.argvs("sg")
        self.assertIn("_IN_SG_KVM=1", argv[2])

    def test_without_user_set(self):
        # Containers often don't set $USER; the user comes from `id -un` instead.
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(kvm_group_members=["tester"])
        result = self.start(env={"USER": None})
        self.assertNotIn("unbound variable", result.output)
        self.assertEqual(len(self.sandbox.argvs("sg")), 1)

    def test_the_device_belongs_to_another_group_than_kvm(self):
        # Seen on WSL: /dev/kvm is created before udev applies the kvm group, so it keeps a gid
        # no group owns and joining kvm changes nothing. Say so instead of suggesting usermod.
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(kvm_group_gid=4242, kvm_group_members=["tester"])
        result = self.start()
        self.assertFailed(result, "but the kvm group is")
        self.assertIn("sudo chgrp kvm", result.output)
        self.assertEqual(self.sandbox.argvs("sg"), [], "sg can't help, so it isn't tried")
        self.assertEqual(self.launched(), [])

    def test_in_the_kvm_group_but_not_this_session_reruns_through_sg_once(self):
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(kvm_group_members=["someone", "tester"])
        result = self.start("tv_api25", "-no-window")
        # The fake sg can't grant the group, so the re-run still has no access and must stop
        # instead of re-running itself again.
        self.assertFailed(result, "No access to")
        [argv] = self.sandbox.argvs("sg")
        self.assertEqual(argv[:2], ["kvm", "-c"])
        self.assertIn("_IN_SG_KVM=1", argv[2])
        self.assertIn("tv_api25 -no-window", argv[2], "the re-run keeps the AVD and flags")

    def test_the_sg_rerun_keeps_quick(self):
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(kvm_group_members=["tester"])
        self.start("--quick")
        [argv] = self.sandbox.argvs("sg")
        self.assertIn("tv_api25 --quick", argv[2])

    def test_the_sg_rerun_finds_the_script_when_called_through_a_symlink(self):
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(kvm_group_members=["tester"])
        self.sandbox.run("start-emulator.sh", how="symlink")
        [argv] = self.sandbox.argvs("sg")
        self.assertIn(f"{self.sandbox.tools}/bin/start-emulator.sh tv_api25", argv[2],
                      "sg's shell may not have the same $PATH, so the real path is used")


class WslToolbar(EmulatorTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def toolbar_calls(self):
        return [argv for argv in self.sandbox.argvs("python3") if argv[0].endswith("wslg-toolbar.py")]

    def test_hidden_after_boot_on_wsl(self):
        self.sandbox.wsl()
        self.assertSucceeded(self.start())
        self.assertEqual([argv[1:] for argv in self.toolbar_calls()], [["tv_api25", "hide"]])

    def test_toolbar_script_found_when_called_through_a_symlink(self):
        self.sandbox.wsl()
        self.assertSucceeded(self.sandbox.run("start-emulator.sh", how="symlink"))
        [argv] = self.toolbar_calls()
        self.assertEqual(argv[0], str(self.sandbox.tools / "bin" / "wslg-toolbar.py"))

    def test_emulator_toolbar_show(self):
        self.sandbox.wsl()
        self.assertSucceeded(self.start(env={"EMULATOR_TOOLBAR": "show"}))
        self.assertEqual([argv[1:] for argv in self.toolbar_calls()], [["tv_api25", "show"]])

    def test_untouched_outside_wsl(self):
        self.assertSucceeded(self.start())
        self.assertEqual(self.toolbar_calls(), [])

    def test_untouched_without_a_window(self):
        self.sandbox.wsl()
        self.assertSucceeded(self.start("-no-window"))
        self.assertEqual(self.toolbar_calls(), [])

    def test_a_toolbar_failure_does_not_fail_the_booted_emulator(self):
        self.sandbox.wsl()
        self.sandbox.set_behavior(python3_exit=1)
        self.assertSucceeded(self.start())


class Usage(EmulatorTestCase):
    def test_missing_emulator_package(self):
        self.sandbox.install_sdk(self.sandbox.root / "partial", tools=["adb"])
        result = self.start(env={"ANDROID_HOME": str(self.sandbox.root / "partial")})
        self.assertFailed(result, 'android sdk install --no-metrics "emulator"')
