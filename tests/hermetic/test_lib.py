"""lib.sh: finding the SDK, its tools and AVDs on any setup, time limits, and waiting for the home
screen."""
import textwrap

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
        self.assertIn(f"{self.sandbox.tools}/SETUP.md", result.output, "a path that works anywhere")
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


class TimeLimits(ScriptTestCase):
    def test_a_limit_lasts_at_least_its_seconds(self):
        # Started late in a whole second of $SECONDS, which ticks with the clock's seconds: a
        # limit counted as `$SECONDS - start >= 1` would be up at the next tick, 0.1 s later.
        result = self.sandbox.bash("""
            tick=$SECONDS; while [ "$SECONDS" = "$tick" ]; do sleep 0.01; done
            sleep 0.85
            start=$SECONDS started=$EPOCHREALTIME
            until time_is_up "$start" 1; do sleep 0.01; done
            echo "$started $EPOCHREALTIME"
        """)
        started, ended = map(float, result.out.split())
        self.assertGreaterEqual(ended - started, 1)

    def test_a_limit_of_zero_is_up_at_once(self):
        self.assertEqual(self.sandbox.bash("time_is_up $SECONDS 0").code, 0)


def dumps(window, activities):
    """A look at a device: `dumpsys window` and `dumpsys activity activities` as the fake adb
    shows them (a `front` item, fake_tools.py), here trimmed to the lines wait_for_home reads."""
    return {"window": textwrap.dedent(window).lstrip(), "activities": textwrap.dedent(activities).lstrip()}


# The fake device's home app (fake_tools.py's default), and Google TV's.
HOME = "com.google.android.tvlauncher/.MainActivity"
HOME_WINDOW = "com.google.android.tvlauncher/com.google.android.tvlauncher.MainActivity"
LAUNCHERX = "com.google.android.apps.tv.launcherx"
LAUNCHERX_HOME = f"{LAUNCHERX}/.home.HomeActivity"
LEANBACK = "com.google.android.leanbacklauncher"
# Google TV (API 31) after a boot starved of CPU: the launcher's trampoline in front, in a task of
# its own, its home task below.
DISPATCH = dumps("""
    mCurrentFocus=Window{b99fc88 u0 com.google.android.apps.tv.launcherx/com.google.android.apps.tv.launcherx.coreservices.bootmode.DispatchActivity}
    mFocusedApp=ActivityRecord{d2732d0 u0 com.google.android.apps.tv.launcherx/.coreservices.bootmode.DispatchActivity t4}
    """, """
    * Hist #0: ActivityRecord{d2732d0 u0 com.google.android.apps.tv.launcherx/.coreservices.bootmode.DispatchActivity t4}
      state=RESUMED stopped=false delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=true mStartingWindowState=STARTING_WINDOW_SHOWN
    * Hist #0: ActivityRecord{97a36c0 u0 com.google.android.apps.tv.launcherx/.home.HomeActivity t3}
      state=STOPPING stopped=false delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=true mStartingWindowState=STARTING_WINDOW_NOT_SHOWN
    """)
# Google TV (API 33, starved), back from Settings: the home screen resumed, its main thread still
# busy, its sign-in screen to come.
HOME_RESUMING = dumps("""
    mCurrentFocus=Window{667f478 u0 com.google.android.apps.tv.launcherx/com.google.android.apps.tv.launcherx.home.HomeActivity}
    mFocusedApp=ActivityRecord{af5a038 u0 com.google.android.apps.tv.launcherx/.home.HomeActivity} t54}
    """, """
    * Hist  #0: ActivityRecord{af5a038 u0 com.google.android.apps.tv.launcherx/.home.HomeActivity} t54}
      state=RESUMED stopped=false delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=false
    * Hist  #0: ActivityRecord{5bce39 u0 com.android.tv.settings/.MainSettings} t56}
      state=PAUSED stopped=false delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=true
    * Hist  #0: ActivityRecord{adc0b13 u0 com.google.android.apps.tv.launcherx/.coreservices.bootmode.DispatchActivity} t55}
      state=STOPPED stopped=true delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=true
    """)
# Google TV (API 31, starved): the sign-in screen already on top, the home screen's window still
# focused.
CHOOSER_COMING = dumps("""
    mCurrentFocus=Window{92bd30e u0 com.google.android.apps.tv.launcherx/com.google.android.apps.tv.launcherx.home.HomeActivity}
    mFocusedApp=ActivityRecord{4c189ad u0 com.google.android.apps.tv.launcherx/.home.HomeActivity t123}
    """, """
    * Hist #1: ActivityRecord{17486f9 u0 com.google.android.apps.tv.launcherx/.profile.chooser.ProfileChooserActivity t123}
      state=RESUMED stopped=false delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=false mStartingWindowState=STARTING_WINDOW_NOT_SHOWN
    * Hist #0: ActivityRecord{4c189ad u0 com.google.android.apps.tv.launcherx/.home.HomeActivity t123}
      state=PAUSED stopped=false delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=false mStartingWindowState=STARTING_WINDOW_NOT_SHOWN
    * Hist #0: ActivityRecord{5ef996 u0 com.android.tv.settings/.MainSettings t127}
      state=PAUSED stopped=false delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=true mStartingWindowState=STARTING_WINDOW_REMOVED
    """)
# Google TV (API 33): the sign-in screen the home screen opened over itself, in its task.
CHOOSER = dumps("""
    mCurrentFocus=Window{436542c u0 com.google.android.apps.tv.launcherx/com.google.android.apps.tv.launcherx.profile.chooser.ProfileChooserActivity}
    mFocusedApp=ActivityRecord{5e6ffcc u0 com.google.android.apps.tv.launcherx/.profile.chooser.ProfileChooserActivity} t88}
    """, """
    * Hist  #1: ActivityRecord{5e6ffcc u0 com.google.android.apps.tv.launcherx/.profile.chooser.ProfileChooserActivity} t88}
      state=RESUMED stopped=false delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=true
    * Hist  #0: ActivityRecord{d04d35a u0 com.google.android.apps.tv.launcherx/.home.HomeActivity} t88}
      state=STOPPED stopped=true delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=true
    * Hist  #0: ActivityRecord{c1960b2 u0 com.android.tv.settings/.MainSettings} t84}
      state=STOPPED stopped=true delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=true
    """)
# Google TV (API 31, starved), after an input ANR: `dumpsys window` starts with the state at that
# time, focus included (the home screen, before its sign-in screen came); the live state follows.
ANR_COPY = """
    WINDOW MANAGER LAST ANR (dumpsys window lastanr)
      ANR time: 10/1/26 14:02
      Application at fault: com.android.tv.settings
      WINDOW MANAGER WINDOWS (dumpsys window windows)
        mCurrentFocus=Window{59570 u0 com.google.android.apps.tv.launcherx/com.google.android.apps.tv.launcherx.home.HomeActivity}
        mFocusedApp=ActivityRecord{97a36c0 u0 com.google.android.apps.tv.launcherx/.home.HomeActivity t3}

    WINDOW MANAGER WINDOWS (dumpsys window windows)
    """
CHOOSER_AFTER_ANR = dumps(ANR_COPY + """
      mCurrentFocus=Window{1947a15 u0 com.google.android.apps.tv.launcherx/com.google.android.apps.tv.launcherx.profile.chooser.ProfileChooserActivity}
      mFocusedApp=ActivityRecord{8d251c4 u0 com.google.android.apps.tv.launcherx/.profile.chooser.ProfileChooserActivity t3}
    """, """
    * Hist #1: ActivityRecord{8d251c4 u0 com.google.android.apps.tv.launcherx/.profile.chooser.ProfileChooserActivity t3}
      state=RESUMED stopped=false delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=true mStartingWindowState=STARTING_WINDOW_NOT_SHOWN
    * Hist #0: ActivityRecord{97a36c0 u0 com.google.android.apps.tv.launcherx/.home.HomeActivity t3}
      state=STOPPING stopped=false delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=true mStartingWindowState=STARTING_WINDOW_NOT_SHOWN
    """)
# The same copy, with Settings in front now (made up from the two).
SETTINGS_AFTER_ANR = dumps(ANR_COPY + """
      mCurrentFocus=Window{af46328 u0 com.android.tv.settings/com.android.tv.settings.MainSettings}
      mFocusedApp=ActivityRecord{5ef996 u0 com.android.tv.settings/.MainSettings t127}
    """, """
    * Hist #0: ActivityRecord{5ef996 u0 com.android.tv.settings/.MainSettings t127}
      state=RESUMED stopped=false delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=true
    * Hist #0: ActivityRecord{97a36c0 u0 com.google.android.apps.tv.launcherx/.home.HomeActivity t3}
      state=STOPPED stopped=true delayedResume=false finishing=false
      keysPaused=false inHistory=true idle=true
    """)
# API 22 with a second home app installed: Home asks which one to use, in a task the HOME intent
# started.
API22_CHOOSER = dumps("""
    mFocusedApp=Token{af0e5cd ActivityRecord{179a7f64 u0 android/com.android.internal.app.ResolverActivity t5}}
    mCurrentFocus=Window{25456fc u0 android/com.android.internal.app.ResolverActivity}
    mFocusedApp=AppWindowToken{18247e82 token=Token{af0e5cd ActivityRecord{179a7f64 u0 android/com.android.internal.app.ResolverActivity t5}}}
    """, """
    * TaskRecord{83f5bda #5 I=android/com.android.internal.app.ResolverActivity U=0 sz=1}
      intent={act=android.intent.action.MAIN cat=[android.intent.category.HOME] flg=0x10a00000 cmp=android/com.android.internal.app.ResolverActivity}
      * Hist #0: ActivityRecord{179a7f64 u0 android/com.android.internal.app.ResolverActivity t5}
        Intent { act=android.intent.action.MAIN cat=[android.intent.category.HOME] flg=0x10a00000 cmp=android/com.android.internal.app.ResolverActivity }
        frontOfTask=true task=TaskRecord{83f5bda #5 I=android/com.android.internal.app.ResolverActivity U=0 sz=1}
        state=RESUMED stopped=false delayedResume=false finishing=false
        keysPaused=false inHistory=true visible=true sleeping=false idle=true
    * TaskRecord{287db0af #2 A=com.android.tv.settings U=0 sz=1}
      intent={act=android.settings.SETTINGS flg=0x10000000 cmp=com.android.tv.settings/.MainSettings}
      * Hist #0: ActivityRecord{1971a648 u0 com.android.tv.settings/.MainSettings t2}
        state=STOPPED stopped=true delayedResume=false finishing=false
        keysPaused=false inHistory=true visible=true sleeping=false idle=true
      mFocusedActivity: ActivityRecord{179a7f64 u0 android/com.android.internal.app.ResolverActivity t5}
    """)
# API 22: the stock launcher, just started by a HOME intent, its main thread still busy.
API22_LAUNCHER_STARTING = dumps("""
    mFocusedApp=Token{10d155f9 ActivityRecord{27eb24c0 u0 com.google.android.leanbacklauncher/.MainActivity t3}}
    mCurrentFocus=Window{38c69fb4 u0 com.google.android.leanbacklauncher/com.google.android.leanbacklauncher.MainActivity}
    mFocusedApp=AppWindowToken{28e9a93e token=Token{10d155f9 ActivityRecord{27eb24c0 u0 com.google.android.leanbacklauncher/.MainActivity t3}}}
    """, """
    * TaskRecord{b796e5a #3 A=com.google.android.leanbacklauncher U=0 sz=1}
      intent={act=android.intent.action.MAIN cat=[android.intent.category.HOME] flg=0x10000000 cmp=com.google.android.leanbacklauncher/.MainActivity}
      * Hist #0: ActivityRecord{27eb24c0 u0 com.google.android.leanbacklauncher/.MainActivity t3}
        state=RESUMED stopped=false delayedResume=false finishing=false
        keysPaused=false inHistory=true visible=true sleeping=false idle=false
    * TaskRecord{287db0af #2 A=com.android.tv.settings U=0 sz=1}
      intent={act=android.settings.SETTINGS flg=0x10000000 cmp=com.android.tv.settings/.MainSettings}
      * Hist #0: ActivityRecord{1971a648 u0 com.android.tv.settings/.MainSettings t2}
        state=PAUSED stopped=false delayedResume=false finishing=false
        keysPaused=false inHistory=true visible=false sleeping=false idle=true
      mFocusedActivity: ActivityRecord{27eb24c0 u0 com.google.android.leanbacklauncher/.MainActivity t3}
    """)
# API 23 after a first boot: the stock launcher, in a task a HOME intent started, over "USB drive
# connected".
API23_LAUNCHER = dumps("""
    mFocusedApp=Token{4e9b05d ActivityRecord{ac49a34 u0 com.google.android.leanbacklauncher/.MainActivity t2}}
    mCurrentFocus=Window{44b339a u0 com.google.android.leanbacklauncher/com.google.android.leanbacklauncher.MainActivity}
    mFocusedApp=AppWindowToken{2a9dd2 token=Token{4e9b05d ActivityRecord{ac49a34 u0 com.google.android.leanbacklauncher/.MainActivity t2}}}
    """, """
    * TaskRecord{78919e4 #2 A=com.google.android.leanbacklauncher U=0 sz=1}
      intent={act=android.intent.action.MAIN cat=[android.intent.category.HOME] flg=0x10000000 cmp=com.google.android.leanbacklauncher/.MainActivity}
      * Hist #0: ActivityRecord{ac49a34 u0 com.google.android.leanbacklauncher/.MainActivity t2}
        state=RESUMED stopped=false delayedResume=false finishing=false
        keysPaused=false inHistory=true visible=true sleeping=false idle=true
    * TaskRecord{59bae4d #1 I=com.android.tv.settings/.device.storage.NewStorageActivity U=0 sz=1}
      intent={act=com.android.tv.settings.device.storage.NewStorageActivity.NEW_STORAGE flg=0x10008000 cmp=com.android.tv.settings/.device.storage.NewStorageActivity}
      * Hist #0: ActivityRecord{6572a0 u0 com.android.tv.settings/.device.storage.NewStorageActivity t1}
        state=STOPPED stopped=true delayedResume=false finishing=false
        keysPaused=false inHistory=true visible=false sleeping=false idle=true
      mFocusedActivity: ActivityRecord{ac49a34 u0 com.google.android.leanbacklauncher/.MainActivity t2}
    """)


def idle(look):
    """The same look, once the top activity's main thread has gone idle."""
    return {**look, "activities": look["activities"].replace("idle=false", "idle=true", 1)}


class WaitForHome(ScriptTestCase):
    """wait_for_home <serial> <seconds> [<backs>] (what start-emulator.sh --wait-for-home runs) on
    dumps the emulators produced: the home screen is settled when the top activity is in the home
    app's task, resumed and idle, the focused activity, and its own window has the focus, at two
    looks in a row. ADT_BACK_AFTER=1 here, so another app's screen gets Back after 1 s, not 10."""

    def setUp(self):
        super().setUp()
        self.sandbox.install_sdk()
        self.sandbox.add_avd("tv_api25")
        self.serial = self.sandbox.start_emulator("tv_api25")

    def wait(self, limit=20, backs=0, env=None):
        return self.sandbox.bash(f"wait_for_home {self.serial} {limit} {backs}",
                                 env={"ADT_BACK_AFTER": "1", **(env or {})})

    def google_tv(self, api_level, front):
        self.sandbox.set_behavior(api_level=api_level, home_resolves=[LAUNCHERX_HOME], front=front)

    def looks(self):
        return [call for call in self.sandbox.calls("adb") if call["argv"][-2:] == ["dumpsys", "window"]]

    def backs(self):
        return [call for call in self.sandbox.calls("adb") if call["argv"][-3:] == ["input", "keyevent", "BACK"]]

    def test_waits_past_google_tvs_boot_trampoline(self):
        # DispatchActivity is the home app's, but in a task of its own: the home task comes over it.
        self.google_tv(31, [DISPATCH] * 3 + [CHOOSER_AFTER_ANR])
        result = self.wait()
        self.assertSucceeded(result)
        self.assertEqual(result.out, f"{LAUNCHERX}\n")
        self.assertEqual(len(self.looks()), 5, "took DispatchActivity for the home screen")

    def test_never_presses_back_on_a_screen_of_the_home_app(self):
        # A Back on DispatchActivity could end the launcher's decision what to show.
        self.google_tv(31, [DISPATCH])
        result = self.wait(limit=3, backs=2)
        self.assertEqual(result.code, 1)
        self.assertEqual(self.backs(), [])
        self.assertIn(f"activity {LAUNCHERX}/.coreservices.bootmode.DispatchActivity, focused window "
                      f"{LAUNCHERX}/{LAUNCHERX}.coreservices.bootmode.DispatchActivity; a HOME intent "
                      f"resolves to {LAUNCHERX_HOME}; the top activity is "
                      f"{LAUNCHERX}/.coreservices.bootmode.DispatchActivity in task 4, RESUMED, idle",
                      result.out)

    def test_waits_until_the_home_app_has_finished_starting(self):
        # Resumed and focused, but its main thread still busy: Google TV's opens its sign-in screen.
        self.google_tv(33, [HOME_RESUMING] * 3 + [idle(HOME_RESUMING)])
        self.assertSucceeded(self.wait())
        self.assertEqual(len(self.looks()), 5)

    def test_waits_while_the_sign_in_screen_comes_up(self):
        # On top, but the home screen's window still has the focus.
        self.google_tv(31, [CHOOSER_COMING] * 2 + [CHOOSER_AFTER_ANR])
        self.assertSucceeded(self.wait())
        self.assertEqual(len(self.looks()), 4)

    def test_takes_a_screen_the_home_app_opened_in_its_task_for_the_home_screen(self):
        # Google TV's sign-in screen without an account.
        self.google_tv(33, [CHOOSER])
        result = self.wait()
        self.assertSucceeded(result)
        self.assertEqual(result.out, f"{LAUNCHERX}\n")
        self.assertEqual(len(self.looks()), 2, "two looks in a row")

    def test_reads_the_live_focus_after_an_anr(self):
        # The copy at the dump's start names the home screen, the live lines Settings.
        self.google_tv(31, [SETTINGS_AFTER_ANR])
        result = self.wait(limit=2)
        self.assertEqual(result.code, 1, "took the focus at the last ANR for the current one")
        self.assertIn("activity com.android.tv.settings/.MainSettings, focused window "
                      "com.android.tv.settings/com.android.tv.settings.MainSettings;", result.out)
        # And the other way around: the live lines name the sign-in screen.
        self.sandbox.set_behavior(front=[CHOOSER_AFTER_ANR])
        self.assertSucceeded(self.wait())

    def test_a_new_window_of_the_home_screen_needs_two_looks_again(self):
        # Google TV's launcher restarts after a boot (a GMS update), with a new window.
        again = {**CHOOSER, "window": CHOOSER["window"].replace("436542c", "5d10e2f")}
        self.google_tv(33, [CHOOSER, again, again])
        self.assertSucceeded(self.wait())
        self.assertEqual(len(self.looks()), 3)

    def test_before_api_24_the_chooser_in_the_task_home_started_is_the_home_screen(self):
        self.sandbox.set_behavior(api_level=22, front=[API22_CHOOSER])
        result = self.wait()
        self.assertSucceeded(result)
        self.assertEqual(result.out, "android\n")

    def test_before_api_24_waits_until_the_launcher_has_finished_starting(self):
        self.sandbox.set_behavior(api_level=22, front=[API22_LAUNCHER_STARTING] * 2
                                  + [idle(API22_LAUNCHER_STARTING)])
        result = self.wait()
        self.assertSucceeded(result)
        self.assertEqual(result.out, f"{LEANBACK}\n")
        self.assertEqual(len(self.looks()), 4)

    def test_before_api_24_the_launcher_over_another_task_is_the_home_screen(self):
        self.sandbox.set_behavior(api_level=23, front=[API23_LAUNCHER])
        result = self.wait()
        self.assertSucceeded(result)
        self.assertEqual(result.out, f"{LEANBACK}\n")

    def test_presses_back_once_another_apps_screen_has_kept_the_focus_back_after_seconds(self):
        usb = ["com.android.tv.settings/.device.storage.NewStorageActivity",
               "com.android.tv.settings/com.android.tv.settings.device.storage.NewStorageActivity"]
        self.sandbox.set_behavior(front=[usb, "BACK", [HOME, HOME_WINDOW]])
        self.assertSucceeded(self.wait(backs=2, env={"ADT_BACK_AFTER": "2"}))
        [back] = self.backs()
        self.assertGreaterEqual(back["time"] - self.looks()[0]["time"], 2)
