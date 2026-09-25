"""lib.sh: finding the SDK, its tools and AVDs on any setup."""
from support.sandbox import ScriptTestCase


class SdkDiscovery(ScriptTestCase):
    def sdk(self, env=None):
        return self.sandbox.bash('echo "$SDK"', env=env).out.strip()

    def test_android_home_wins_over_every_other_source(self):
        a = self.sandbox.install_sdk(self.sandbox.root / "a")
        b = self.sandbox.install_sdk(self.sandbox.root / "b")
        self.sandbox.install_sdk()
        (self.sandbox.project / "local.properties").write_text(f"sdk.dir={b}\n")
        self.assertEqual(self.sdk({"ANDROID_HOME": str(a), "ANDROID_SDK_ROOT": str(b)}), str(a))

    def test_android_sdk_root_is_used_without_android_home(self):
        b = self.sandbox.install_sdk(self.sandbox.root / "b")
        self.assertEqual(self.sdk({"ANDROID_SDK_ROOT": str(b)}), str(b))

    def test_a_variable_pointing_nowhere_falls_through_to_the_next_source(self):
        default = self.sandbox.install_sdk()
        self.assertEqual(self.sdk({"ANDROID_HOME": "/nonexistent/sdk"}), str(default))

    def test_sdk_dir_from_local_properties_like_gradle(self):
        sdk = self.sandbox.install_sdk(self.sandbox.root / "studio:sdk")
        # Android Studio escapes ':' as '\:' in properties files.
        (self.sandbox.project / "local.properties").write_text(
            f"# written by Android Studio\nsdk.dir={str(sdk).replace(':', chr(92) + ':')}\n")
        self.assertEqual(self.sdk(), str(sdk))

    def test_local_properties_of_the_project_from_one_of_its_subfolders(self):
        sdk = self.sandbox.install_sdk(self.sandbox.root / "studio-sdk")
        self.sandbox.install_sdk()
        (self.sandbox.project / "local.properties").write_text(f"sdk.dir={sdk}\n")
        subfolder = self.sandbox.project / "app" / "src"
        subfolder.mkdir(parents=True)
        self.assertEqual(self.sandbox.bash('echo "$SDK"', cwd=subfolder).out.strip(), str(sdk))

    def test_local_properties_next_to_the_tools_is_not_used(self):
        # Only the project the user is in counts, not wherever this repository is cloned.
        other = self.sandbox.install_sdk(self.sandbox.root / "other")
        default = self.sandbox.install_sdk()
        (self.sandbox.tools / "local.properties").write_text(f"sdk.dir={other}\n")
        self.assertEqual(self.sdk(), str(default))

    def test_sdk_of_the_adb_on_path(self):
        sdk = self.sandbox.install_sdk(self.sandbox.root / "distro-sdk")
        (self.sandbox.bin / "adb").symlink_to(sdk / "platform-tools" / "adb")
        self.assertEqual(self.sdk(), str(sdk))

    def test_android_studio_default_location_last(self):
        self.assertEqual(self.sdk(), str(self.sandbox.install_sdk()))


class ToolDiscovery(ScriptTestCase):
    def tool(self, var, env=None):
        return self.sandbox.bash(f'echo "${var}"', env=env).out.strip()

    def test_tools_come_from_the_sdk(self):
        sdk = self.sandbox.install_sdk()
        self.assertEqual(self.tool("ADB"), str(sdk / "platform-tools/adb"))
        self.assertEqual(self.tool("EMULATOR"), str(sdk / "emulator/emulator"))
        self.assertEqual(self.tool("AVDMANAGER"), str(sdk / "cmdline-tools/latest/bin/avdmanager"))

    def test_newest_versioned_cmdline_tools_when_there_is_no_latest(self):
        # Version order, not text order: 19.0 is newer than 9.0.
        for version in ("9.0", "19.0", "12.0"):
            self.sandbox.install_sdk(tools=["avdmanager"],
                                     layout={"avdmanager": f"cmdline-tools/{version}/bin/avdmanager"})
        self.assertTrue(self.tool("AVDMANAGER").endswith("cmdline-tools/19.0/bin/avdmanager"))

    def test_tools_missing_from_the_sdk_are_taken_from_path(self):
        self.sandbox.install_sdk(tools=["adb"])
        elsewhere = self.sandbox.install_sdk(self.sandbox.root / "other", tools=["emulator"])
        (self.sandbox.bin / "emulator").symlink_to(elsewhere / "emulator/emulator")
        self.assertEqual(self.tool("EMULATOR"), str(self.sandbox.bin / "emulator"))

    def test_missing_tool_names_the_package_to_install(self):
        self.sandbox.install_sdk(tools=["adb", "android"])
        result = self.sandbox.bash('require "$EMULATOR" emulator')
        self.assertFailed(result, 'android sdk install --no-metrics "emulator"')

    def test_the_install_hint_falls_back_to_sdkmanager_before_cmdline_tools_22(self):
        # 'android' only ships from cmdline-tools 22.0; on older ones sdkmanager is all there is,
        # so suggesting the new command would be a command the user doesn't have.
        self.sandbox.install_sdk(tools=["adb", "sdkmanager"])
        result = self.sandbox.bash('require "$EMULATOR" emulator')
        self.assertFailed(result, 'sdkmanager "emulator"')
        self.assertNotIn("android sdk install", result.output)

    def test_the_install_hint_names_the_current_tool_when_the_sdk_has_neither(self):
        self.sandbox.install_sdk(tools=["adb"])
        result = self.sandbox.bash('require "$EMULATOR" emulator')
        self.assertFailed(result, 'android sdk install --no-metrics "emulator"')

    def test_the_install_hint_opts_out_of_telemetry(self):
        # The android CLI reports usage unless --no-metrics is passed, and it can only be turned
        # off per call, so a command we tell the user to paste must carry the flag.
        self.sandbox.install_sdk(tools=["adb", "android"])
        result = self.sandbox.bash('require "$EMULATOR" emulator')
        self.assertIn("--no-metrics", result.output)

    def test_no_sdk_at_all_points_to_setup_instead_of_an_install_command(self):
        result = self.sandbox.bash('require "$ADB" platform-tools')
        self.assertFailed(result, "no Android SDK found")
        self.assertIn(f"{self.sandbox.tools}/setup.md", result.output, "a path that works anywhere")
        self.assertNotIn("sdkmanager", result.output)
        self.assertNotIn("android sdk install", result.output)


class CalledFromAnywhere(ScriptTestCase):
    """Users call the scripts by a relative path from any folder, or put bin/ (or symlinks to its
    scripts) on $PATH. Every way must find lib.sh and the other scripts."""

    WAYS = ("absolute", "relative", "path", "symlink")

    def setUp(self):
        super().setUp()
        self.sandbox.install_sdk()
        self.sandbox.add_avd("tv_api25")

    def test_every_script_works_whichever_way_it_is_called(self):
        elsewhere = self.sandbox.root / "somewhere" / "else"
        elsewhere.mkdir(parents=True)
        for how in self.WAYS:
            for cwd in (self.sandbox.project, elsewhere):
                with self.subTest(how=how, cwd=cwd.name):
                    result = self.sandbox.run("start-emulator.sh", how=how, cwd=cwd)
                    self.assertSucceeded(result)
                    serial = next(iter(self.sandbox.running()))
                    self.assertSucceeded(self.sandbox.run("remote.sh", input="h", how=how, cwd=cwd))
                    self.sandbox.stop_emulators()
                    self.assertIn(serial, result.out)

    def test_suggested_commands_are_bare_names_when_on_path(self):
        for how in ("path", "symlink"):
            with self.subTest(how=how):
                result = self.sandbox.run("remote.sh", how=how)
                self.assertFailed(result, "Start one with: start-emulator.sh\n")

    def test_suggested_commands_use_the_path_the_user_typed(self):
        result = self.sandbox.run("remote.sh", how="relative")
        self.assertFailed(result, "Start one with: ../tools/bin/start-emulator.sh\n")

    def test_a_different_script_of_the_same_name_on_path_is_not_suggested(self):
        # Another project's start-emulator.sh on $PATH isn't this one, so the path is suggested.
        other = self.sandbox.root / "other-tools"
        other.mkdir()
        (other / "start-emulator.sh").write_text("#!/bin/sh\n")
        (other / "start-emulator.sh").chmod(0o755)
        result = self.sandbox.run("remote.sh", how="relative",
                                  env={"PATH": f"{other}:{self.sandbox.bin}"})
        self.assertFailed(result, "Start one with: ../tools/bin/start-emulator.sh\n")


class AvdDiscovery(ScriptTestCase):
    def avd_dir(self, name, env=None):
        return self.sandbox.bash(f'avd_dir {name}', env=env)

    def test_default_avd_folder(self):
        folder = self.sandbox.add_avd("tv")
        self.assertEqual(self.avd_dir("tv").out.strip(), str(folder))

    def test_every_variable_that_moves_the_avd_folder(self):
        root = self.sandbox.root
        cases = {"ANDROID_AVD_HOME": root / "avd-home",
                 "ANDROID_EMULATOR_HOME": root / "emu-home" / "avd",
                 "ANDROID_USER_HOME": root / "user-home" / "avd",
                 "ANDROID_SDK_HOME": root / "sdk-home" / ".android" / "avd"}
        for var, home in cases.items():
            with self.subTest(var):
                folder = self.sandbox.add_avd(f"tv_{var.lower()}", home=home)
                base = {"ANDROID_AVD_HOME": home, "ANDROID_EMULATOR_HOME": home.parent,
                        "ANDROID_USER_HOME": home.parent,
                        "ANDROID_SDK_HOME": home.parent.parent}[var]
                result = self.avd_dir(f"tv_{var.lower()}", env={var: str(base)})
                self.assertEqual(result.out.strip(), str(folder))

    def test_follows_the_ini_path_to_an_avd_stored_elsewhere(self):
        # The AVD's folder is on another disk; only its .ini is in the AVD folder.
        folder = self.sandbox.add_avd("tv", home=self.sandbox.root / "big-disk")
        (folder.parent / "tv.ini").unlink()
        home = self.sandbox.home / ".android" / "avd"
        home.mkdir(parents=True)
        (home / "tv.ini").write_text(f"path={folder}\n")
        self.assertEqual(self.avd_dir("tv").out.strip(), str(folder))

    def test_unknown_avd_fails(self):
        self.assertNotEqual(self.avd_dir("missing").code, 0)

    def test_list_avds_ignores_emulator_log_lines(self):
        self.sandbox.install_sdk()
        self.sandbox.add_avd("tv_api25")
        self.sandbox.add_avd("phone", tv=False)
        self.assertEqual(self.sandbox.bash("list_avds").out.split(), ["phone", "tv_api25"])
