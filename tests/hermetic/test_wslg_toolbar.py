"""wslg-toolbar.py: hiding and showing the emulator toolbar on a real (virtual) X server."""
import subprocess
import sys
import unittest

from support import x11
from support.sandbox import BIN

SCRIPT = str(BIN / "wslg-toolbar.py")


def run(*args, display=None, timeout="0.5"):
    env = {"PATH": "/usr/bin:/bin", "WSLG_TOOLBAR_TIMEOUT": timeout}
    if display:
        env["DISPLAY"] = display
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True,
                          env=env, timeout=60)


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

    def run_script(self, *args):
        return run(*args, display=self.display)

    def test_hide_unmaps_only_the_toolbar(self):
        main, toolbar = self.windows.emulator("tv_api25")
        result = self.run_script("tv_api25", "hide")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("toolbar hidden", result.stdout)
        self.assertFalse(self.windows.is_mapped(toolbar))
        self.assertTrue(self.windows.is_mapped(main), "the emulator screen stays")

    def test_hide_is_the_default_mode(self):
        _, toolbar = self.windows.emulator("tv_api25")
        self.assertEqual(self.run_script("tv_api25").returncode, 0)
        self.assertFalse(self.windows.is_mapped(toolbar))

    def test_show_turns_the_toolbar_into_a_normal_window(self):
        _, toolbar = self.windows.emulator("tv_api25")
        result = self.run_script("tv_api25", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.windows.wait_until_mapped(toolbar))
        self.assertIsNone(self.windows.transient_for(toolbar), "no longer a transient utility")
        self.assertEqual(self.windows.window_types(toolbar), ["_NET_WM_WINDOW_TYPE_NORMAL"])

    def test_switching_back_and_forth(self):
        _, toolbar = self.windows.emulator("tv_api25")
        for mode, mapped in (("show", True), ("hide", False), ("show", True)):
            self.assertEqual(self.run_script("tv_api25", mode).returncode, 0)
            # Unmapping is immediate; mapping waits for the window manager, if there's one.
            now = self.windows.wait_until_mapped(toolbar) if mapped else self.windows.is_mapped(toolbar)
            self.assertEqual(now, mapped, mode)

    def test_without_a_name_acts_on_the_only_emulator(self):
        _, toolbar = self.windows.emulator("living_room")
        self.assertEqual(self.run_script("hide").returncode, 0)
        self.assertFalse(self.windows.is_mapped(toolbar))

    def test_with_a_name_acts_only_on_that_emulator(self):
        _, phone_toolbar = self.windows.emulator("phone", port=5554)
        _, tv_toolbar = self.windows.emulator("tv_api25", port=5556)
        self.assertEqual(self.run_script("tv_api25", "hide").returncode, 0)
        self.assertFalse(self.windows.is_mapped(tv_toolbar))
        self.assertTrue(self.windows.is_mapped(phone_toolbar))

    def test_several_emulators_without_a_name(self):
        _, phone_toolbar = self.windows.emulator("phone", port=5554)
        _, tv_toolbar = self.windows.emulator("tv_api25", port=5556)
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
