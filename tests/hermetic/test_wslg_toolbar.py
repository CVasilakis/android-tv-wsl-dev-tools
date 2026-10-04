"""wslg-toolbar.py: hiding and showing the emulator toolbar on a real (virtual) X server."""
import subprocess
import sys
import time
import unittest

from support import x11
from support.sandbox import BIN

SCRIPT = str(BIN / "wslg-toolbar.py")


def env(display=None, timeout="0.5"):
    result = {"PATH": "/usr/bin:/bin", "WSLG_TOOLBAR_TIMEOUT": timeout}
    if display:
        result["DISPLAY"] = display
    return result


def run(*args, display=None, timeout="0.5"):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True,
                          env=env(display, timeout), timeout=60)


class WithoutX(unittest.TestCase):
    def test_help(self):
        result = run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith("Usage:"))

    def test_bad_arguments_print_usage(self):
        for args in (["a", "b", "hide"], ["--hide"]):
            with self.subTest(args=args):
                result = run(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Usage:", result.stderr)

    def test_no_display(self):
        result = run("hide")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot open X display", result.stderr)


@unittest.skipIf(x11.unavailable_reason(), x11.unavailable_reason())
class OnAnXServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = x11.XServer()
        cls.display = cls.server.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.server.__exit__(None, None, None)

    def setUp(self):
        self.windows = x11.EmulatorWindows(self.display)
        self.addCleanup(self.windows.close)            # destroys this test's windows

    def run_script(self, *args, timeout="0.5"):
        return run(*args, display=self.display, timeout=timeout)

    def start_script(self, *args):
        """Starts the script in the background, with a time limit that only ends what never
        finishes."""
        script = subprocess.Popen([sys.executable, SCRIPT, *args], text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env(self.display, timeout="30"))
        self.addCleanup(lambda: (script.kill(), script.communicate()))
        return script

    def manage(self, win):
        """Marks win as shown by a window manager (WM_STATE Normal), as WSLg's does, so that the
        test can play the window manager, which Xvfb doesn't have."""
        if self.windows.wm_state(win) is not None:
            self.skipTest("this display has a window manager, whose part the test would play")
        self.windows.set_wm_state(win, x11.NORMAL)

    def test_hide_unmaps_only_the_toolbar(self):
        main, toolbar, _ = self.windows.emulator("tv_api25")
        result = self.run_script("tv_api25", "hide")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("toolbar hidden", result.stdout)
        self.assertFalse(self.windows.is_mapped(toolbar))
        self.assertTrue(self.windows.is_mapped(main), "the emulator screen stays")

    def test_hide_is_the_default_mode(self):
        _, toolbar, _ = self.windows.emulator("tv_api25")
        self.assertEqual(self.run_script("tv_api25").returncode, 0)
        self.assertFalse(self.windows.is_mapped(toolbar))

    def test_show_turns_the_toolbar_into_a_normal_window(self):
        _, toolbar, _ = self.windows.emulator("tv_api25")
        result = self.run_script("tv_api25", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertShown(toolbar)

    def assertShown(self, toolbar):
        self.assertTrue(self.windows.wait_until_mapped(toolbar))
        self.assertIsNone(self.windows.transient_for(toolbar), "no longer a transient utility")
        self.assertEqual(self.windows.window_types(toolbar), ["_NET_WM_WINDOW_TYPE_NORMAL"])

    def test_show_waits_for_the_window_manager_to_withdraw_the_toolbar(self):
        # The window manager reads a window's hints when it's mapped: "show" changes them and
        # maps the toolbar again only once the window manager has handled the unmap, which it
        # marks by setting WM_STATE to Withdrawn. Here the test is the window manager, a slow one.
        main, toolbar, _ = self.windows.emulator("tv_api25")
        self.manage(toolbar)
        script = self.start_script("tv_api25", "show")
        self.assertTrue(self.windows.wait_until_mapped(toolbar, mapped=False))
        time.sleep(1)                            # the window manager's delay
        self.assertEqual(self.windows.transient_for(toolbar), main, "changed before the withdrawal")
        self.assertFalse(self.windows.is_mapped(toolbar), "mapped before the withdrawal")
        self.windows.set_wm_state(toolbar, x11.WITHDRAWN)
        out, err = script.communicate(timeout=60)
        self.assertEqual(script.returncode, 0, err)
        self.assertEqual(err, "")
        self.assertIn("toolbar shown", out)
        self.assertShown(toolbar)

    def test_show_without_a_window_manager_has_nothing_to_wait_for(self):
        # No WM_STATE: no window manager will withdraw the toolbar, as on Xvfb.
        _, toolbar, _ = self.windows.emulator("tv_api25")
        if self.windows.wm_state(toolbar) is not None:
            self.skipTest("this display has a window manager")
        result = self.run_script("tv_api25", "show", timeout="30")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", "waited for a withdrawal that can't come")
        self.assertShown(toolbar)

    def test_show_of_a_hidden_toolbar_has_nothing_to_wait_for(self):
        # As after start-emulator.sh's "hide": the window manager has withdrawn the toolbar
        # already, and the unmap changes nothing it would mark.
        _, toolbar, _ = self.windows.emulator("tv_api25")
        self.manage(toolbar)
        self.assertEqual(self.run_script("tv_api25", "hide").returncode, 0)
        self.windows.set_wm_state(toolbar, x11.WITHDRAWN)
        result = self.run_script("tv_api25", "show", timeout="30")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", "waited for a withdrawal that can't come")
        self.assertShown(toolbar)

    def test_show_goes_on_when_the_window_manager_never_withdraws_the_toolbar(self):
        # The X server hands the window manager the unmap before the map, so mapping the toolbar
        # anyway is the best the script can do; it says why it may not work.
        _, toolbar, _ = self.windows.emulator("tv_api25")
        self.manage(toolbar)
        result = self.run_script("tv_api25", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("the window manager didn't withdraw the toolbar within 0.5 s; "
                      "showing it anyway", result.stderr)
        self.assertShown(toolbar)

    def test_switching_back_and_forth(self):
        # After "show" the window manager puts the toolbar on top, above the hidden bar that is
        # also titled "Emulator": a "hide" then hid the bar and left the toolbar shown.
        _, toolbar, bar = self.windows.emulator("tv_api25")
        for mode, mapped in (("hide", False), ("show", True), ("hide", False), ("show", True),
                             ("show", True), ("hide", False)):
            self.assertEqual(self.run_script("tv_api25", mode).returncode, 0)
            # Unmapping is immediate; mapping waits for the window manager, if there's one.
            now = self.windows.wait_until_mapped(toolbar) if mapped else self.windows.is_mapped(toolbar)
            self.assertEqual(now, mapped, mode)
            self.assertFalse(self.windows.is_mapped(bar), f"{mode}: the bar stays hidden")
            if mapped:
                self.windows.raise_(toolbar)        # as a window manager does (Xvfb has none)

    def test_several_windows_like_the_toolbar(self):
        # Changing the wrong one would leave the toolbar as it was: the script doesn't guess.
        main, toolbar, _ = self.windows.emulator("tv_api25")
        other = self.windows.utility(main, 40, 300)
        result = self.run_script("tv_api25", "hide")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("several windows look like the toolbar", result.stderr)
        self.assertIn(hex(toolbar), result.stderr)
        self.assertIn(hex(other), result.stderr)
        self.assertTrue(self.windows.is_mapped(toolbar) and self.windows.is_mapped(other))

    def test_without_a_name_acts_on_the_only_emulator(self):
        _, toolbar, _ = self.windows.emulator("living_room")
        self.assertEqual(self.run_script("hide").returncode, 0)
        self.assertFalse(self.windows.is_mapped(toolbar))

    def test_with_a_name_acts_only_on_that_emulator(self):
        _, phone_toolbar, _ = self.windows.emulator("phone", port=5554)
        _, tv_toolbar, _ = self.windows.emulator("tv_api25", port=5556)
        self.assertEqual(self.run_script("tv_api25", "hide").returncode, 0)
        self.assertFalse(self.windows.is_mapped(tv_toolbar))
        self.assertTrue(self.windows.is_mapped(phone_toolbar))

    def test_several_emulators_without_a_name(self):
        _, phone_toolbar, _ = self.windows.emulator("phone", port=5554)
        _, tv_toolbar, _ = self.windows.emulator("tv_api25", port=5556)
        result = self.run_script("hide")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("several emulators are running", result.stderr)
        self.assertTrue(self.windows.is_mapped(phone_toolbar) and self.windows.is_mapped(tv_toolbar))

    def test_a_name_prefix_is_not_a_match(self):
        # "tv" must not match the window of "tv_api25".
        self.windows.emulator("tv_api25")
        result = self.run_script("tv", "hide")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no toolbar window found for AVD 'tv'", result.stderr)

    def test_no_emulator_window(self):
        result = self.run_script("hide")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no running emulator window found", result.stderr)

    def test_windows_closing_during_the_search_are_skipped(self):
        # The script reads every window on the display; one closing between being listed and
        # being read (a tooltip, a menu, the emulator's own startup windows) made Xlib end it
        # with "BadWindow", and the toolbar stayed shown.
        _, toolbar, _ = self.windows.emulator("tv_api25")
        with x11.WindowChurn(self.display):
            result = self.run_script("tv_api25", "hide")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.windows.is_mapped(toolbar))

    def test_an_error_on_the_toolbar_it_changes_is_still_reported(self):
        # Only the search skips windows that closed. "show" unmaps the toolbar and waits for the
        # window manager to withdraw it before it changes the toolbar and maps it again: the
        # toolbar closing meanwhile must make the script fail. Here the test is the window
        # manager, so the script waits until the test has closed the toolbar.
        _, toolbar, _ = self.windows.emulator("tv_api25")
        self.manage(toolbar)
        script = self.start_script("tv_api25", "show")
        self.assertTrue(self.windows.wait_until_mapped(toolbar, mapped=False))
        self.windows.destroy(toolbar)
        _, err = script.communicate(timeout=60)
        self.assertNotEqual(script.returncode, 0)
        self.assertIn("BadWindow", err)
