"""start-emulator.sh: choosing the AVD, booting it, finding its serial and reporting problems."""
import os
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

    def test_leading_flags_go_to_the_emulator_and_keep_the_default_avd(self):
        self.sandbox.add_avd("tv_api25")
        self.assertSucceeded(self.start("-wipe-data", "-no-snapshot-load"))
        [argv] = self.launched()
        self.assertEqual(argv[1], "tv_api25")
        self.assertIn("-wipe-data", argv)
        self.assertIn("-no-snapshot-load", argv)

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
        self.assertIn("booted as emulator-5554", result.out)
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
        self.assertIn(str(log), result.out)
        self.assertIn("Android emulator version", log.read_text())

    def test_already_running_avd_is_reported_not_started_again(self):
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.start()
        self.assertSucceeded(result)
        self.assertIn(f"already running as {serial}", result.out)
        self.assertEqual(self.launched(), [])

    def test_crash_during_boot_is_reported_with_the_log(self):
        self.sandbox.set_behavior(emulator_crash="unknown option: -bogus")
        started = time.time()
        result = self.start("-bogus")
        self.assertFailed(result, "the emulator exited")
        self.assertIn("unknown option: -bogus", result.output, "the log's last lines are shown")
        self.assertLess(time.time() - started, 10, "must not keep waiting for a dead emulator")


class FindsItsOwnEmulator(EmulatorTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")
        self.sandbox.add_avd("phone", tv=False)

    def test_serial_of_its_avd_when_another_emulator_runs(self):
        self.sandbox.start_emulator("phone")             # takes emulator-5554
        result = self.start()
        self.assertSucceeded(result)
        self.assertIn("booted as emulator-5556", result.out)
        self.assertIn("export ANDROID_SERIAL=emulator-5556", result.out)

    def test_hint_when_a_physical_device_is_connected(self):
        self.sandbox.connect_device("R58M123ABC")
        result = self.start()
        self.assertSucceeded(result)
        self.assertIn("export ANDROID_SERIAL=emulator-5554", result.out)

    def test_no_hint_when_it_is_the_only_device(self):
        result = self.start()
        self.assertSucceeded(result)
        self.assertNotIn("ANDROID_SERIAL", result.out)

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
        self.assertFailed(result, "sudo usermod -aG kvm")
        self.assertEqual(self.sandbox.argvs("sg"), [])
        self.assertEqual(self.launched(), [])

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
