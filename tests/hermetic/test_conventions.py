"""Rules every script in bin/ follows. New scripts are picked up automatically."""
import os
import py_compile
import re
import shutil
import subprocess
import tempfile
import unittest

from emulator import test_on_emulator as tier
from support.sandbox import BIN, LIB, TOOLS

RUNNABLE = sorted(p for p in BIN.iterdir() if p.suffix in (".sh", ".py"))
SHELL = sorted(BIN.glob("*.sh")) + [LIB]


class Conventions(unittest.TestCase):
    def test_runnable_scripts_are_executable_with_a_shebang(self):
        for script in RUNNABLE:
            with self.subTest(script.name):
                self.assertTrue(os.access(script, os.X_OK), "not executable (chmod +x)")
                self.assertTrue(script.read_text().startswith("#!/usr/bin/env "),
                                "must start with #!/usr/bin/env bash|python3")

    def test_only_runnable_scripts_are_in_bin(self):
        # Everything in bin/ becomes a command for users who put it on $PATH.
        for path in BIN.iterdir():
            with self.subTest(path.name):
                self.assertTrue(path in RUNNABLE or path.name == "README.md",
                                "bin/ holds only commands; helpers go in lib/")

    def test_the_library_is_not_executable(self):
        self.assertFalse(os.access(LIB, os.X_OK), "sourced, so not executable")

    def test_every_script_has_help(self):
        # --help must work on a machine with nothing installed, so it runs with an empty env.
        # It names the script without a folder: users call it by any path, or through $PATH.
        for script in RUNNABLE:
            for flag in ("--help", "-h"):
                with self.subTest(script.name, flag=flag):
                    result = subprocess.run([str(script), flag], capture_output=True, text=True,
                                            env={"PATH": "/usr/bin:/bin"}, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(result.stdout.startswith("Usage: " + script.name),
                                    f"help must start with 'Usage: {script.name}'")

    def test_every_script_is_described_in_the_readme(self):
        readme = (BIN / "README.md").read_text()
        for script in RUNNABLE:
            with self.subTest(script.name):
                self.assertIn(f"[`{script.name}`]({script.name})", readme)
        self.assertIn("[`lib/lib.sh`](lib/lib.sh)", (TOOLS / "README.md").read_text())

    def test_time_limits_are_checked_by_time_is_up(self):
        # $SECONDS counts whole seconds, so comparing it with a limit directly can end the limit
        # up to a second early; time_is_up (lib.sh) doesn't. Scripts only take start times from
        # it, and may show one in a message.
        for script in BIN.glob("*.sh"):
            for number, line in enumerate(script.read_text().splitlines(), 1):
                code = line.strip()
                if "SECONDS" in code and not code.startswith(("#", "echo ")):
                    with self.subTest(f"{script.name}:{number}"):
                        self.assertRegex(code, r"=\$SECONDS$",
                                         "check time limits with time_is_up")

    def test_the_emulator_tier_copies_the_scripts_current_time_limits(self):
        # The tier's limits for start-emulator.sh and stop-emulator.sh are built from these values
        # of the scripts. With a value grown in a script only, Python could stop the script
        # halfway and leave its emulator running.
        lib, start, stop = (path.read_text() for path in
                            (LIB, BIN / "start-emulator.sh", BIN / "stop-emulator.sh"))
        stop_emulator = re.search(r"^stop_emulator\(\) \{\n.*?^\}", start, re.M | re.S)
        scripts = {
            "ADB_TIMEOUT": (lib, r'^ADB_TIMEOUT="\$\{ADT_ADB_TIMEOUT:-(\d+)\}"'),
            "ADB_KILL_AFTER": (lib, r'timeout -k (\d+) "\$ADB_TIMEOUT"'),
            "SAVE_TIMEOUT": (start, r'^SAVE_TIMEOUT="\$\{ADT_SAVE_TIMEOUT:-(\d+)\}"'),
            "TERM_WAIT": (stop_emulator and stop_emulator.group(0), r'"\$waited" -lt (\d+) \]'),
            "KILL_WAIT": (stop, r"^KILL_WAIT=(\d+)"),
            "STOP_SAVE_WAIT": (stop, r'^SAVE_WAIT="\$\{ADT_SAVE_WAIT:-(\d+)\}"'),
        }
        for name, (text, pattern) in scripts.items():
            with self.subTest(name):
                found = re.search(pattern, text or "", re.M)
                self.assertIsNotNone(found, f"the scripts no longer match {pattern!r}: "
                                            "update this test and the tier's copy")
                self.assertEqual(getattr(tier, name), int(found.group(1)),
                                 f"tests/emulator/test_on_emulator.py's {name} differs from the "
                                 "script's")

    def test_shell_syntax(self):
        for script in SHELL:
            with self.subTest(script.name):
                result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_python_syntax(self):
        with tempfile.TemporaryDirectory() as out:
            for script in BIN.glob("*.py"):
                with self.subTest(script.name):
                    py_compile.compile(str(script), cfile=os.path.join(out, "x.pyc"), doraise=True)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck isn't installed")
    def test_shellcheck(self):
        result = subprocess.run(["shellcheck", "-x", "--source-path=SCRIPTDIR", *map(str, SHELL)],
                                capture_output=True, text=True, cwd=TOOLS)
        self.assertEqual(result.returncode, 0, result.stdout)
