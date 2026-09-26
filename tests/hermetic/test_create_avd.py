"""create-avd.sh: creating the TV emulator with the right settings, wherever AVDs live."""
from support.sandbox import ScriptTestCase

EXPECTED_SETTINGS = {
    "hw.keyboard": "yes", "hw.dPad": "yes", "hw.ramSize": "2048", "hw.cpu.ncore": "4",
    "disk.dataPartition.size": "4G", "hw.gpu.enabled": "yes", "hw.gpu.mode": "swiftshader_indirect",
    "hw.initialOrientation": "landscape", "showDeviceFrame": "no", "hw.audioInput": "no",
}


API25_IMAGE = "system-images/android-25/android-tv/x86/"


def read_config(folder):
    lines = (folder / "config.ini").read_text().splitlines()
    return [tuple(line.split("=", 1)) for line in lines if "=" in line]


def add_tv_image(sandbox, level, kernel, tag="android-tv"):
    """Installs a system image in the sandbox's SDK: its package.xml, a system.img and `kernel`."""
    image = sandbox.sdk / f"system-images/android-{level}/{tag}/x86"
    image.mkdir(parents=True)
    for name in ("package.xml", "system.img", kernel):
        (image / name).write_text("")


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

    def test_api_picks_the_tv_image_of_that_level_and_names_the_avd_after_it(self):
        for level in ("22", "28", "30", "33", "36"):
            with self.subTest(level):
                result = self.sandbox.run("create-avd.sh", "--api", level)
                self.assertSucceeded(result)
                argv = self.sandbox.argvs("avdmanager")[-1]
                self.assertEqual(argv[argv.index("--name") + 1], f"tv_api{level}")
                self.assertEqual(argv[argv.index("--package") + 1],
                                 f"system-images;android-{level};android-tv;x86")
                self.assertIn(("hw.keyboard", "yes"),
                              read_config(self.sandbox.home / f".android/avd/tv_api{level}.avd"))
                self.assertIn(f"start-emulator.sh tv_api{level}", result.out)

    def test_accepts_a_tv_image_with_a_ranchu_kernel(self):
        # API 36 names its kernel kernel-ranchu-64.
        add_tv_image(self.sandbox, "36", "kernel-ranchu-64")
        self.assertSucceeded(self.sandbox.run("create-avd.sh", "--api", "36"))
        self.assertTrue((self.sandbox.home / ".android/avd/tv_api36.avd").is_dir())

    def test_api_with_a_name(self):
        self.assertSucceeded(self.sandbox.run("create-avd.sh", "--api", "28", "pie_tv"))
        [argv] = self.sandbox.argvs("avdmanager")
        self.assertEqual(argv[argv.index("--name") + 1], "pie_tv")
        self.assertEqual(argv[argv.index("--package") + 1], "system-images;android-28;android-tv;x86")

    def test_name_before_api(self):
        self.assertSucceeded(self.sandbox.run("create-avd.sh", "pie_tv", "--api", "28"))
        [argv] = self.sandbox.argvs("avdmanager")
        self.assertEqual(argv[argv.index("--name") + 1], "pie_tv")
        self.assertEqual(argv[argv.index("--package") + 1], "system-images;android-28;android-tv;x86")

    def test_api_names_the_avd_after_its_level_even_with_adt_avd_set(self):
        # ADT_AVD usually names the everyday AVD; an AVD of another level must not take its name.
        self.assertSucceeded(self.sandbox.run("create-avd.sh", "--api", "30", env={"ADT_AVD": "den_tv"}))
        self.assertTrue((self.sandbox.home / ".android/avd/tv_api30.avd").is_dir())

    def test_google_tv_picks_the_google_tv_image_and_names_the_avd_gtv(self):
        for level in ("30", "36"):
            with self.subTest(level):
                result = self.sandbox.run("create-avd.sh", "--google-tv", "--api", level)
                self.assertSucceeded(result)
                argv = self.sandbox.argvs("avdmanager")[-1]
                for flag, value in {"--name": f"gtv_api{level}", "--tag": "google-tv", "--abi": "x86",
                                    "--package": f"system-images;android-{level};google-tv;x86"}.items():
                    self.assertEqual(argv[argv.index(flag) + 1], value, flag)
                config = read_config(self.sandbox.home / f".android/avd/gtv_api{level}.avd")
                self.assertIn(("hw.dPad", "yes"), config)
                self.assertIn(("tag.id", "google-tv"), config)
                self.assertIn(f"start-emulator.sh gtv_api{level}", result.out)

    def test_google_tv_with_a_name_in_any_order(self):
        self.assertSucceeded(self.sandbox.run("create-avd.sh", "den_gtv", "--api", "33", "--google-tv"))
        [argv] = self.sandbox.argvs("avdmanager")
        self.assertEqual(argv[argv.index("--name") + 1], "den_gtv")
        self.assertEqual(argv[argv.index("--package") + 1], "system-images;android-33;google-tv;x86")

    def test_google_tv_names_the_avd_after_its_level_even_with_adt_avd_set(self):
        self.assertSucceeded(self.sandbox.run("create-avd.sh", "--google-tv", "--api", "34",
                                              env={"ADT_AVD": "den_tv"}))
        self.assertTrue((self.sandbox.home / ".android/avd/gtv_api34.avd").is_dir())

    def test_accepts_a_google_tv_image(self):
        add_tv_image(self.sandbox, "36", "kernel-ranchu-64", tag="google-tv")
        self.assertSucceeded(self.sandbox.run("create-avd.sh", "--google-tv", "--api", "36"))
        self.assertTrue((self.sandbox.home / ".android/avd/gtv_api36.avd").is_dir())

    def test_keeps_the_1080p_screen_of_the_tv_profile_by_default(self):
        self.assertSucceeded(self.sandbox.run("create-avd.sh"))
        config = read_config(self.sandbox.home / ".android/avd/tv_api25.avd")
        for setting in (("hw.lcd.width", "1920"), ("hw.lcd.height", "1080"), ("hw.lcd.density", "320")):
            self.assertIn(setting, config)

    def test_size_and_density_replace_the_profiles_screen(self):
        self.assertSucceeded(self.sandbox.run("create-avd.sh", "--size", "1280x720", "--density", "213",
                                              "tv_720p"))
        config = read_config(self.sandbox.home / ".android/avd/tv_720p.avd")
        keys = [key for key, _ in config]
        for key, value in {"hw.lcd.width": "1280", "hw.lcd.height": "720", "hw.lcd.density": "213",
                           "hw.initialOrientation": "landscape"}.items():
            with self.subTest(key):
                self.assertIn((key, value), config)
                self.assertEqual(keys.count(key), 1, "a setting must replace, not duplicate, a key")

    def test_size_and_density_each_on_their_own(self):
        self.assertSucceeded(self.sandbox.run("create-avd.sh", "--size", "3840x2160", "tv_4k"))
        self.assertSucceeded(self.sandbox.run("create-avd.sh", "--density", "480", "dense_tv"))
        config = read_config(self.sandbox.home / ".android/avd/tv_4k.avd")
        self.assertIn(("hw.lcd.width", "3840"), config)
        self.assertIn(("hw.lcd.density", "320"), config, "the profile's density is kept")
        config = read_config(self.sandbox.home / ".android/avd/dense_tv.avd")
        self.assertIn(("hw.lcd.density", "480"), config)
        self.assertIn(("hw.lcd.width", "1920"), config, "the profile's size is kept")

    def test_size_and_density_with_the_other_options_in_any_order(self):
        self.assertSucceeded(self.sandbox.run("create-avd.sh", "--density", "213", "--google-tv", "gtv_720p",
                                              "--size", "1280x720", "--api", "36"))
        [argv] = self.sandbox.argvs("avdmanager")
        self.assertEqual(argv[argv.index("--package") + 1], "system-images;android-36;google-tv;x86")
        config = read_config(self.sandbox.home / ".android/avd/gtv_720p.avd")
        self.assertIn(("hw.lcd.height", "720"), config)
        self.assertIn(("hw.lcd.density", "213"), config)

    def test_patches_the_avd_in_a_custom_avd_home(self):
        home = self.sandbox.root / "avds"
        self.assertSucceeded(self.sandbox.run("create-avd.sh", env={"ANDROID_AVD_HOME": str(home)}))
        self.assertIn(("hw.keyboard", "yes"), read_config(home / "tv_api25.avd"))
        self.assertFalse((self.sandbox.home / ".android").exists(), "nothing written to ~/.android")

    def test_creates_the_avd_where_the_emulator_looks_when_avdmanager_prefers_xdg(self):
        self.sandbox.set_behavior(avdmanager_xdg=True)
        xdg = self.sandbox.home / ".config"
        result = self.sandbox.run("create-avd.sh", env={"XDG_CONFIG_HOME": str(xdg)})
        self.assertSucceeded(result)
        self.assertIn(("hw.keyboard", "yes"), read_config(self.sandbox.home / ".android/avd/tv_api25.avd"))
        self.assertFalse((xdg / ".android").exists(), "nothing written to $XDG_CONFIG_HOME")
        self.assertSucceeded(self.sandbox.run("start-emulator.sh"))

    def test_creates_a_missing_custom_avd_home_when_avdmanager_prefers_xdg(self):
        self.sandbox.set_behavior(avdmanager_xdg=True)
        home = self.sandbox.root / "avds"
        self.assertSucceeded(self.sandbox.run("create-avd.sh", env={
            "XDG_CONFIG_HOME": str(self.sandbox.home / ".config"), "ANDROID_AVD_HOME": str(home)}))
        self.assertIn(("hw.keyboard", "yes"), read_config(home / "tv_api25.avd"))


class RefusesOrExplains(ScriptTestCase):
    def test_never_overwrites_an_existing_avd(self):
        self.sandbox.install_sdk()
        folder = self.sandbox.add_avd("tv_api25", image=API25_IMAGE)
        before = (folder / "config.ini").read_text()
        result = self.sandbox.run("create-avd.sh")
        self.assertFailed(result, "already exists", code=1)
        self.assertIn("avdmanager delete avd -n tv_api25", result.err)
        self.assertIn("--if-missing", result.err)
        self.assertEqual(self.sandbox.argvs("avdmanager"), [], "avdmanager must not be called")
        self.assertEqual((folder / "config.ini").read_text(), before)

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

    def test_api_must_be_a_number_and_one_name_at_most(self):
        for args in (["--api"], ["--api", "pie"], ["--api", "-1"], ["one_tv", "two_tv"]):
            with self.subTest(args):
                self.assertFailed(self.sandbox.run("create-avd.sh", *args), "Usage:", code=2)
        self.assertEqual(self.sandbox.argvs("avdmanager"), [])

    def test_explains_a_missing_system_image(self):
        self.sandbox.install_sdk()
        self.sandbox.set_behavior(avdmanager_error="Error: Package path is not valid. Valid system image paths are:")
        result = self.sandbox.run("create-avd.sh", "--api", "30")
        self.assertFailed(result, 'android sdk install --no-metrics "system-images;android-30;android-tv;x86"')

    def test_refuses_a_tv_image_the_emulator_cannot_boot(self):
        # The API 21 image has only the goldfish kernel; the emulator would refuse to start it.
        self.sandbox.install_sdk()
        add_tv_image(self.sandbox, "21", "kernel-qemu")
        result = self.sandbox.run("create-avd.sh", "--api", "21")
        self.assertFailed(result, "API 22 or newer")
        self.assertIn("no ranchu kernel", result.output)
        self.assertEqual(self.sandbox.argvs("avdmanager"), [], "avdmanager must not be called")

    def test_google_tv_needs_a_level_from_30_on(self):
        # There's no Google TV image of the default level 25, or of any level before 30.
        self.sandbox.install_sdk()
        for args, message in ((["--google-tv"], "needs --api"), (["--google-tv", "--api", "28"], "before API 30")):
            with self.subTest(args):
                self.assertFailed(self.sandbox.run("create-avd.sh", *args), message)
        self.assertEqual(self.sandbox.argvs("avdmanager"), [], "avdmanager must not be called")

    def test_explains_a_missing_google_tv_image(self):
        self.sandbox.install_sdk()
        self.sandbox.set_behavior(avdmanager_error="Error: Package path is not valid. Valid system image paths are:")
        result = self.sandbox.run("create-avd.sh", "--google-tv", "--api", "31")
        self.assertFailed(result, 'android sdk install --no-metrics "system-images;android-31;google-tv;x86"')

    def test_size_and_density_must_be_positive_numbers(self):
        for args in (["--size"], ["--size", "1280"], ["--size", "1280x"], ["--size", "1280*720"],
                     ["--size", "0x720"], ["--size", "1280x720p"], ["--density"], ["--density", "hdpi"],
                     ["--density", "0"], ["--density", "-213"]):
            with self.subTest(args):
                self.assertFailed(self.sandbox.run("create-avd.sh", *args), "Usage:", code=2)
        self.assertEqual(self.sandbox.argvs("avdmanager"), [])

    def test_refuses_a_portrait_size(self):
        # A TV is landscape: Android TV would rotate its picture onto a portrait panel, sideways.
        self.sandbox.install_sdk()
        result = self.sandbox.run("create-avd.sh", "--size", "720x1280")
        self.assertFailed(result, "--size 1280x720", code=1)
        self.assertIn("taller than wide", result.err)
        self.assertEqual(self.sandbox.argvs("avdmanager"), [], "avdmanager must not be called")

    def test_unknown_option_prints_usage(self):
        result = self.sandbox.run("create-avd.sh", "--force")
        self.assertFailed(result, "Usage:", code=2)


class IfMissing(ScriptTestCase):
    """--if-missing: succeed on an AVD that already exists from the same image, so a script or
    CI job (with the AVD cached) can run create-avd.sh every time."""

    def setUp(self):
        super().setUp()
        self.sandbox.install_sdk()

    def test_creates_a_missing_avd(self):
        result = self.sandbox.run("create-avd.sh", "--if-missing")
        self.assertSucceeded(result)
        self.assertIn(("hw.keyboard", "yes"), read_config(self.sandbox.home / ".android/avd/tv_api25.avd"))

    def test_accepts_an_existing_avd_of_the_same_image_and_leaves_it_alone(self):
        folder = self.sandbox.add_avd("tv_api25", image=API25_IMAGE)
        before = (folder / "config.ini").read_text()
        result = self.sandbox.run("create-avd.sh", "--if-missing")
        self.assertSucceeded(result)
        self.assertIn("already exists", result.out)
        self.assertEqual(self.sandbox.argvs("avdmanager"), [], "avdmanager must not be called")
        self.assertEqual((folder / "config.ini").read_text(), before)

    def test_running_it_twice(self):
        # The second run finds the AVD the first one created, as avdmanager wrote it.
        for _ in range(2):
            self.assertSucceeded(self.sandbox.run("create-avd.sh", "--api", "30", "--if-missing"))
        self.assertEqual(len(self.sandbox.argvs("avdmanager")), 1)

    def test_with_a_name_and_api_in_any_order(self):
        self.sandbox.add_avd("pie_tv", image="system-images/android-28/android-tv/x86/")
        for args in (["--if-missing", "--api", "28", "pie_tv"], ["pie_tv", "--api", "28", "--if-missing"]):
            with self.subTest(args):
                self.assertSucceeded(self.sandbox.run("create-avd.sh", *args))
        self.assertEqual(self.sandbox.argvs("avdmanager"), [])

    def test_refuses_an_existing_avd_of_another_image(self):
        folder = self.sandbox.add_avd("tv_api25", image="system-images/android-28/android-tv/x86/")
        before = (folder / "config.ini").read_text()
        result = self.sandbox.run("create-avd.sh", "--if-missing")
        self.assertFailed(result, "system-images/android-28/android-tv/x86, not "
                                  "system-images/android-25/android-tv/x86", code=1)
        self.assertIn("avdmanager delete avd -n tv_api25", result.err)
        self.assertEqual(self.sandbox.argvs("avdmanager"), [])
        self.assertEqual((folder / "config.ini").read_text(), before)

    def test_tells_the_google_tv_and_android_tv_images_of_a_level_apart(self):
        self.sandbox.add_avd("gtv_api30", image="system-images/android-30/android-tv/x86/")
        result = self.sandbox.run("create-avd.sh", "--google-tv", "--api", "30", "--if-missing")
        self.assertFailed(result, "system-images/android-30/android-tv/x86, not "
                                  "system-images/android-30/google-tv/x86", code=1)
        self.assertEqual(self.sandbox.argvs("avdmanager"), [])

    def add_720p_avd(self):
        # As the emulator rewrites config.ini: "key = value".
        folder = self.sandbox.add_avd("tv_720p", image=API25_IMAGE)
        with (folder / "config.ini").open("a") as config:
            config.write("hw.lcd.width = 1280\nhw.lcd.height = 720\nhw.lcd.density = 213\n")
        return folder

    def test_accepts_an_existing_avd_with_the_size_and_density_given(self):
        self.add_720p_avd()
        for args in ([], ["--size", "1280x720"], ["--density", "213"], ["--size", "1280x720", "--density", "213"]):
            with self.subTest(args):
                self.assertSucceeded(self.sandbox.run("create-avd.sh", "--if-missing", "tv_720p", *args))
        self.assertEqual(self.sandbox.argvs("avdmanager"), [])

    def test_refuses_an_existing_avd_with_another_size_or_density(self):
        folder = self.add_720p_avd()
        before = (folder / "config.ini").read_text()
        for args, message in ((["--size", "1920x1080"], "hw.lcd.width is 1280, not\n1920"),
                              (["--size", "1280x800"], "hw.lcd.height is 720, not\n800"),
                              (["--density", "320"], "hw.lcd.density is 213, not\n320")):
            with self.subTest(args):
                result = self.sandbox.run("create-avd.sh", "--if-missing", "tv_720p", *args)
                self.assertFailed(result, message, code=1)
                self.assertIn("avdmanager delete avd -n tv_720p", result.err)
        self.assertEqual(self.sandbox.argvs("avdmanager"), [])
        self.assertEqual((folder / "config.ini").read_text(), before)

    def test_refuses_an_existing_avd_with_no_screen_setting_when_one_is_given(self):
        self.sandbox.add_avd("tv_api25", image=API25_IMAGE)
        self.assertFailed(self.sandbox.run("create-avd.sh", "--if-missing", "--density", "213"),
                          "hw.lcd.density is not set", code=1)

    def test_refuses_an_existing_avd_whose_image_it_cannot_tell(self):
        self.sandbox.add_avd("tv_api25")
        self.assertFailed(self.sandbox.run("create-avd.sh", "--if-missing"), "an unknown system image")
