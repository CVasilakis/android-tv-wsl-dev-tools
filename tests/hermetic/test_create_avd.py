"""create-avd.sh: creating the TV emulator with the right settings, wherever AVDs live."""
from support.sandbox import ScriptTestCase

EXPECTED_SETTINGS = {
    "hw.keyboard": "yes", "hw.dPad": "yes", "hw.ramSize": "2048", "hw.cpu.ncore": "4",
    "disk.dataPartition.size": "4G", "hw.gpu.enabled": "yes", "hw.gpu.mode": "swiftshader_indirect",
    "hw.initialOrientation": "landscape", "showDeviceFrame": "no", "hw.audioInput": "no",
}


def read_config(folder):
    lines = (folder / "config.ini").read_text().splitlines()
    return [tuple(line.split("=", 1)) for line in lines if "=" in line]


class CreatesTheTvAvd(ScriptTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.install_sdk()

    def test_creates_tv_api25_from_the_api25_tv_image(self):
        self.assertSucceeded(self.sandbox.run("create-avd.sh"))
        [argv] = self.sandbox.argvs("avdmanager")
        for flag, value in {"--name": "tv_api25", "--package": "system-images;android-25;android-tv;x86",
                            "--tag": "android-tv", "--abi": "x86", "--device": "tv_1080p"}.items():
            self.assertEqual(argv[argv.index(flag) + 1], value, flag)

    def test_applies_every_hardware_setting_exactly_once(self):
        self.assertSucceeded(self.sandbox.run("create-avd.sh"))
        config = read_config(self.sandbox.home / ".android/avd/tv_api25.avd")
        keys = [key for key, _ in config]
        for key, value in EXPECTED_SETTINGS.items():
            with self.subTest(key):
                self.assertIn((key, value), config)
                self.assertEqual(keys.count(key), 1, "a setting must replace, not duplicate, a key")
        self.assertIn(("tag.id", "android-tv"), config, "unrelated keys are kept")

    def test_uses_the_given_name(self):
        result = self.sandbox.run("create-avd.sh", "my_tv", how="relative")
        self.assertSucceeded(result)
        self.assertTrue((self.sandbox.home / ".android/avd/my_tv.avd/config.ini").exists())
        self.assertIn("Start it with: ../tools/bin/start-emulator.sh my_tv", result.out)

    def test_suggests_the_bare_command_when_on_path(self):
        result = self.sandbox.run("create-avd.sh", how="path")
        self.assertIn("Start it with: start-emulator.sh tv_api25", result.out)

    def test_default_name_from_adt_avd(self):
        self.assertSucceeded(self.sandbox.run("create-avd.sh", env={"ADT_AVD": "den_tv"}))
        self.assertTrue((self.sandbox.home / ".android/avd/den_tv.avd").is_dir())

    def test_patches_the_avd_in_a_custom_avd_home(self):
        home = self.sandbox.root / "avds"
        self.assertSucceeded(self.sandbox.run("create-avd.sh", env={"ANDROID_AVD_HOME": str(home)}))
        self.assertIn(("hw.keyboard", "yes"), read_config(home / "tv_api25.avd"))
        self.assertFalse((self.sandbox.home / ".android").exists(), "nothing written to ~/.android")


class RefusesOrExplains(ScriptTestCase):
    def test_never_overwrites_an_existing_avd(self):
        self.sandbox.install_sdk()
        folder = self.sandbox.add_avd("tv_api25")
        result = self.sandbox.run("create-avd.sh")
        self.assertFailed(result, "already exists")
        self.assertIn("avdmanager delete avd -n tv_api25", result.output)
        self.assertEqual(self.sandbox.argvs("avdmanager"), [], "avdmanager must not be called")
        self.assertEqual((folder / "config.ini").read_text(), "tag.id = android-tv\ntag.ids = android-tv\n")

    def test_explains_when_it_cannot_find_what_avdmanager_created(self):
        self.sandbox.install_sdk()
        self.sandbox.set_behavior(avd_home=str(self.sandbox.root / "somewhere-unexpected"))
        self.assertFailed(self.sandbox.run("create-avd.sh"), "ANDROID_AVD_HOME")

    def test_stops_when_avdmanager_fails(self):
        self.sandbox.install_sdk()
        self.sandbox.set_behavior(avdmanager_error="Error: Package path is not valid.")
        self.assertFailed(self.sandbox.run("create-avd.sh"), "Package path is not valid")

    def test_names_the_missing_package(self):
        self.sandbox.install_sdk(tools=["adb", "emulator"])
        self.assertFailed(self.sandbox.run("create-avd.sh"),
                          'android sdk install --no-metrics "cmdline-tools;latest"')

    def test_without_any_sdk(self):
        self.assertFailed(self.sandbox.run("create-avd.sh"), "no Android SDK found")

    def test_unknown_option_prints_usage(self):
        result = self.sandbox.run("create-avd.sh", "--force")
        self.assertFailed(result, "Usage:", code=2)
