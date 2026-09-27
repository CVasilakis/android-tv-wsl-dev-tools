"""A throwaway machine for one test: the scripts run for real, but everything around them is fake.

Each Sandbox has its own copy of the tools (bin/ and lib/), a separate project folder the scripts
run in (so local.properties and friends can't leak in from a real checkout), its own $HOME, a
$PATH holding only basic system utilities plus the fakes from fake_tools.py, a fake KVM device and
/proc/version, and a state folder the fakes share. Nothing outside the sandbox's temporary folder
is read or written, so results don't depend on the host (its SDK, AVDs, emulators, kvm group or
WSL).
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]            # the real repository
BIN = TOOLS / "bin"                                     # the scripts users run
LIB = TOOLS / "lib" / "lib.sh"
SUPPORT = Path(__file__).resolve().parent

# Real system utilities the scripts may call. Anything else is missing on the sandbox's $PATH,
# which catches accidental dependencies on the host.
UTILITIES = ["bash", "sed", "grep", "awk", "tr", "head", "tail", "dirname", "basename", "readlink",
             "ls", "sort", "cat", "wc", "sleep", "nohup", "env", "rm", "mkdir", "printf", "kill",
             "stat", "timeout"]
HOST_FAKES = ["python3", "getent", "id", "sg"]         # host commands replaced by fakes
SDK_TOOLS = {"adb": "platform-tools/adb",
             "emulator": "emulator/emulator",
             "avdmanager": "cmdline-tools/latest/bin/avdmanager",
             # Only their presence matters: the scripts name them in install hints, never run
             # them. 'android' exists from cmdline-tools 22.0, 'sdkmanager' in every version.
             "android": "cmdline-tools/latest/bin/android",
             "sdkmanager": "cmdline-tools/latest/bin/sdkmanager"}


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def output(self):
        return self.out + self.err


class Sandbox:
    def __init__(self, root: Path):
        self.root = root
        self.tools = root / "tools"                    # this repository's bin/ and lib/
        self.project = root / "project"                # the folder the scripts run in by default
        self.home = root / "home"
        self.state = root / "state"
        self.bin = root / "bin"
        self.tmp = root / "tmp"
        self.kvm = root / "dev-kvm"
        self.proc_version = root / "proc-version"
        self.sdk = None                                # the last SDK made by install_sdk()
        self._processes = []
        for folder in (self.project, self.home, self.state / "running", self.bin, self.tmp):
            folder.mkdir(parents=True)
        for folder in ("bin", "lib"):
            shutil.copytree(TOOLS / folder, self.tools / folder,
                            ignore=shutil.ignore_patterns("__pycache__", "*.md"))
        self.kvm.write_text("")                        # exists and is writable: KVM available
        self.not_wsl()
        for name in UTILITIES:
            path = shutil.which(name)
            if path:
                (self.bin / name).symlink_to(path)
        for name in HOST_FAKES:
            self._write_fake(self.bin / name, name)

    # --- Arranging the machine --------------------------------------------------------------

    def install_sdk(self, sdk=None, tools=tuple(SDK_TOOLS), layout=None):
        """Creates a fake SDK (default: ~/Android/Sdk) with the given tools; returns its path.
        `layout` overrides where a tool goes, e.g. {"avdmanager": "cmdline-tools/9.0/bin/avdmanager"}."""
        sdk = Path(sdk) if sdk else self.home / "Android" / "Sdk"
        sdk.mkdir(parents=True, exist_ok=True)
        for tool in tools:
            self._write_fake(sdk / {**SDK_TOOLS, **(layout or {})}[tool], tool)
        self.sdk = sdk
        return sdk

    def add_avd(self, name, tv=True, home=None, image=None, tag=None):
        """Creates an AVD the way avdmanager lays it out (default folder: ~/.android/avd).
        `image` is the system image it was made from, e.g. "system-images/android-25/android-tv/x86/"
        (config.ini's image.sysdir.1, written as the emulator rewrites it). `tag` is the image's
        tag; default android-tv, or google_apis (a phone) without `tv`."""
        home = Path(home) if home else self.home / ".android" / "avd"
        folder = home / f"{name}.avd"
        folder.mkdir(parents=True)
        (home / f"{name}.ini").write_text(f"avd.ini.encoding=UTF-8\npath={folder}\n")
        tag = tag or ("android-tv" if tv else "google_apis")
        (folder / "config.ini").write_text(f"tag.id = {tag}\ntag.ids = {tag}\n"
                                           + (f"image.sysdir.1 = {image}\n" if image else ""))
        return folder

    def start_emulator(self, avd):
        """Starts a fake emulator directly (as if already running); returns its serial."""
        before = set(self.running())
        process = subprocess.Popen([str(self.sdk / SDK_TOOLS["emulator"]), "-avd", avd],
                                   env=self.env(FAKE_NO_LOG="1"), stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        self._processes.append(process)
        for _ in range(100):
            new = set(self.running()) - before
            if new:
                return new.pop()
            if process.poll() is not None:
                raise RuntimeError(f"fake emulator for '{avd}' exited")
            time.sleep(0.05)
        raise RuntimeError(f"fake emulator for '{avd}' didn't register")

    def connect_device(self, serial):
        """Simulates a physical device (phone, TV) connected over adb."""
        path = self.state / "devices.json"
        devices = json.loads(path.read_text()) if path.exists() else []
        path.write_text(json.dumps(devices + [serial]))

    def set_behavior(self, **knobs):
        """Changes how the fakes behave; see fake_tools.DEFAULT_BEHAVIOR."""
        path = self.state / "behavior.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        path.write_text(json.dumps({**data, **knobs}))

    def wsl(self):
        self.proc_version.write_text("Linux version 6.6.87.2-microsoft-standard-WSL2\n")

    def not_wsl(self):
        self.proc_version.write_text("Linux version 6.12.0-generic (Debian)\n")

    # --- Running and inspecting -------------------------------------------------------------

    def env(self, **overrides):
        # DISPLAY: a desktop session by default; DISPLAY=None simulates a CI runner or SSH.
        env = {"HOME": str(self.home), "PATH": str(self.bin), "TMPDIR": str(self.tmp),
               "USER": "tester", "DISPLAY": ":0", "LANG": "C", "FAKE_STATE": str(self.state),
               "ADT_KVM_DEVICE": str(self.kvm),
               "ADT_PROC_VERSION": str(self.proc_version)}
        env.update(overrides)
        return {k: v for k, v in env.items() if v is not None}   # None removes a variable

    def command(self, script, how="absolute", cwd=None):
        """How the user calls bin/<script>, and the $PATH that goes with it:
        absolute  its full path;
        relative  a path relative to `cwd`, e.g. ../tools/bin/<script>;
        path      its bare name, with the tools' bin/ on $PATH;
        symlink   its bare name, through symlinks to every script in ~/.local/bin, on $PATH."""
        script_path = self.tools / "bin" / script
        if how == "absolute":
            return str(script_path), self.bin
        if how == "relative":
            return os.path.relpath(script_path, cwd or self.project), self.bin
        if how == "path":
            return script, f"{self.tools / 'bin'}:{self.bin}"
        if how == "symlink":
            links = self.home / ".local" / "bin"
            links.mkdir(parents=True, exist_ok=True)
            for target in (self.tools / "bin").iterdir():
                if not (links / target.name).is_symlink():
                    (links / target.name).symlink_to(target)
            return script, f"{links}:{self.bin}"
        raise ValueError(how)

    def run(self, script, *args, input=None, env=None, timeout=30, how="absolute", cwd=None):
        """Runs bin/<script> (called as `how` says, see command()) in `cwd`, by default the
        project folder, with a closed stdin unless `input` is given."""
        command, path = self.command(script, how, cwd)
        completed = subprocess.run(
            [command, *args], input=input if input is not None else "", capture_output=True,
            text=True, env=self.env(**{"PATH": str(path), **(env or {})}), cwd=cwd or self.project,
            timeout=timeout)
        return Result(completed.returncode, completed.stdout, completed.stderr)

    def bash(self, snippet, env=None, cwd=None):
        """Runs a bash snippet with lib.sh sourced, e.g. to check what it discovered."""
        completed = subprocess.run(
            [shutil.which("bash"), "-c", f'source {self.tools / "lib" / "lib.sh"}\n{snippet}'],
            capture_output=True, text=True, env=self.env(**(env or {})),
            cwd=cwd or self.project, timeout=30)
        return Result(completed.returncode, completed.stdout, completed.stderr)

    def calls(self, tool=None):
        """The fakes' calls so far, in order: a list of {"tool", "argv", "android_serial"}."""
        path = self.state / "calls.jsonl"
        calls = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [c for c in calls if tool is None or c["tool"] == tool]

    def argvs(self, tool):
        return [c["argv"] for c in self.calls(tool)]

    def device_settings(self, serial):
        """The secure settings of a running fake emulator, as `adb shell settings` changed them."""
        return json.loads((self.state / "running" / f"{serial}.json").read_text())["settings"]

    def running(self):
        """{serial: avd name} of the fake emulators that are running."""
        result = {}
        for path in (self.state / "running").glob("emulator-*.json"):
            info = json.loads(path.read_text())
            try:
                os.kill(info["pid"], 0)
            except OSError:
                continue
            result[path.stem] = info["avd"]
        return result

    def emulator_pid(self, serial):
        """The process ID of a fake emulator, running or not."""
        return json.loads((self.state / "running" / f"{serial}.json").read_text())["pid"]

    @staticmethod
    def alive(pid):
        """Whether a process runs (a zombie, killed but not yet reaped, doesn't)."""
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except (OSError, IndexError):
            return False

    def stop_emulators(self):
        """Stops every fake emulator, as `adb emu kill` would."""
        for path in (self.state / "running").glob("emulator-*.json"):
            try:
                os.kill(json.loads(path.read_text())["pid"], signal.SIGTERM)
            except (ValueError, OSError):
                pass
            path.unlink(missing_ok=True)

    def cleanup(self):
        self.stop_emulators()
        for process in self._processes:
            process.kill()
            process.wait()
        shutil.rmtree(self.root, ignore_errors=True)

    # --- Internals ---------------------------------------------------------------------------

    def _write_fake(self, path, tool):
        path.parent.mkdir(parents=True, exist_ok=True)
        # The absolute interpreter matters: `env python3` would find the fake python3.
        path.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(SUPPORT)!r})\n"
                        f"import fake_tools\nfake_tools.main({tool!r})\n")
        path.chmod(0o755)


class ScriptTestCase(unittest.TestCase):
    """Base class: self.sandbox is a fresh Sandbox, removed after the test."""

    def setUp(self):
        self.sandbox = Sandbox(Path(tempfile.mkdtemp(prefix="adt-test-")))
        self.addCleanup(self.sandbox.cleanup)

    def assertSucceeded(self, result):
        self.assertEqual(result.code, 0, f"expected success, got {result.code}:\n{result.output}")

    def assertFailed(self, result, message, code=None):
        """Asserts a failure whose output contains `message`."""
        self.assertNotEqual(result.code, 0, f"expected failure, got success:\n{result.output}")
        if code is not None:
            self.assertEqual(result.code, code, result.output)
        self.assertIn(message, result.output)
