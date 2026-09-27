"""Fake versions of the external programs the scripts call: adb, emulator, avdmanager, and the
host commands python3, getent, id and sg.

Each fake is a small wrapper file (written by sandbox.py) that calls main(<tool name>). The fakes
behave like the real tools as far as the scripts can tell (same output formats, exit codes and
side effects) and share their state through files in $FAKE_STATE, so a test can arrange a
situation (AVDs, running emulators, connected devices, failures) and afterwards check every call
that was made. They never touch the real SDK, emulator or devices.

State directory ($FAKE_STATE):
  calls.jsonl             one JSON line per call: {"tool", "argv", "android_serial"}
  behavior.json           knobs set by the test, see DEFAULT_BEHAVIOR
  running/<serial>.json   a running fake emulator: {"avd", "pid", "polls", "offline", "hidden",
                          "stop_at"}
  devices.json            serials of connected physical devices
  counters.json           per-knob counters (e.g. how many key events failed so far)
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_BEHAVIOR = {
    "boot_polls": 0,              # getprop sys.boot_completed answers "" this many times, then "1"
    "adb_offline": None,          # a booting emulator shows as "offline" in adb devices and its
                                  # adb shell calls fail: for this many boot polls (a slow cold
                                  # boot), "until_reconnect" (a stale Quick Boot snapshot, fixed by
                                  # adb reconnect offline) or "forever"
    "emulator_crash": None,       # emulator prints this and exits 1 instead of booting
    "emulator_hidden": False,     # emulator keeps running but never shows up in adb devices
    "emulator_exit_delay": 0,     # seconds an emulator keeps running after `adb emu kill` (saving
                                  # its Quick Boot snapshot) while adb devices no longer lists it:
                                  # the case where waiting for adb alone would return too early
    "emulator_stuck": False,      # a started emulator answers `adb emu kill` with OK but keeps
                                  # running, and ignores SIGTERM: only SIGKILL stops it
    "emulator_noise": True,       # emulator -list-avds prints a log line before the names
    "keyevent_failures": 0,       # the first N `adb shell input keyevent` calls fail
    "device_settings": {},        # secure settings a newly booted emulator starts with, e.g.
                                  # {"tv_user_setup_complete": "1"}; unset ones read as "null"
    "settings_put_error": None,   # `adb shell settings put` prints this and exits 1
    "adb_hangs": {},              # {"getprop": N, "avd_name": N, "emu_kill": N}: the first N
                                  # `adb shell getprop sys.boot_completed`, `adb emu avd name` or
                                  # `adb emu kill` calls never return, like adb on a half-booted
                                  # emulator starved of CPU, or on a hung one
    "avd_home": None,             # avdmanager/emulator use this AVD folder, ignoring the env vars
    "avdmanager_xdg": False,      # avdmanager acts like cmdline-tools 12.0 (GitHub's runners): with
                                  # $XDG_CONFIG_HOME set, it creates AVDs in
                                  # $XDG_CONFIG_HOME/.android/avd, where the emulator doesn't look,
                                  # unless $ANDROID_AVD_HOME names an existing folder
    "avdmanager_error": None,     # avdmanager create prints this and exits 1
    "user": "tester",             # the user the scripts run as (`id -un`)
    "kvm_group_members": [],      # users listed by `getent group kvm`
    "kvm_primary_group": False,   # kvm is the user's primary group: `id -nG` lists it, getent
                                  # doesn't list the user as a member
    "kvm_group_gid": None,        # gid of the kvm group; None = the gid of $ADT_KVM_DEVICE,
                                  # i.e. the device belongs to the kvm group
    "python3_exit": 0,            # exit code of the fake python3 (the WSLg toolbar script)
}

STATE = Path(os.environ["FAKE_STATE"])
RUNNING = STATE / "running"
FIRST_PORT = 5554


def behavior():
    path = STATE / "behavior.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    return {**DEFAULT_BEHAVIOR, **data}


def take(counter, limit):
    """Counts calls for a knob: True for the first `limit` calls, then False."""
    path = STATE / "counters.json"
    counts = json.loads(path.read_text()) if path.exists() else {}
    counts[counter] = counts.get(counter, 0) + 1
    path.write_text(json.dumps(counts))
    return counts[counter] <= limit


def hang_if(call):
    """Never returns for the first adb_hangs[call] calls, until something kills this process."""
    if take(f"hang_{call}", behavior()["adb_hangs"].get(call, 0)):
        time.sleep(3600)


def log_call(tool, argv):
    if os.environ.get("FAKE_NO_LOG"):    # set by the sandbox for calls that only arrange a test
        return
    with open(STATE / "calls.jsonl", "a") as f:
        f.write(json.dumps({"tool": tool, "argv": argv,
                            "android_serial": os.environ.get("ANDROID_SERIAL")}) + "\n")


def fail(message, code=1):
    print(message, file=sys.stderr)
    sys.exit(code)


# --- AVDs -----------------------------------------------------------------------------------

def avd_home():
    """The folder AVDs are created in and listed from, following the real tools' variables."""
    override = behavior()["avd_home"]
    if override:
        return Path(override)
    env = os.environ
    if env.get("ANDROID_AVD_HOME"):
        return Path(env["ANDROID_AVD_HOME"])
    if env.get("ANDROID_EMULATOR_HOME"):
        return Path(env["ANDROID_EMULATOR_HOME"]) / "avd"
    if env.get("ANDROID_USER_HOME"):
        return Path(env["ANDROID_USER_HOME"]) / "avd"
    if env.get("ANDROID_SDK_HOME"):
        return Path(env["ANDROID_SDK_HOME"]) / ".android" / "avd"
    return Path(env["HOME"]) / ".android" / "avd"


def avdmanager_home():
    """The folder avdmanager creates AVDs in: the emulator's, except with avdmanager_xdg."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    avd_env = os.environ.get("ANDROID_AVD_HOME")
    if behavior()["avdmanager_xdg"] and xdg and not (avd_env and Path(avd_env).is_dir()):
        return Path(xdg) / ".android" / "avd"
    return avd_home()


def avd_folder(name):
    """The <name>.avd folder of an AVD, as its <name>.ini's path= line says."""
    ini = avd_home() / f"{name}.ini"
    for line in ini.read_text().splitlines():
        if line.startswith("path="):
            return Path(line[len("path="):])
    return avd_home() / f"{name}.avd"


def avd_names():
    home = avd_home()
    return sorted(p.stem for p in home.glob("*.ini")) if home.is_dir() else []


# --- Running emulators and devices ----------------------------------------------------------

def alive(pid):
    """Whether a process runs; a killed one its parent hasn't reaped yet (a zombie) doesn't."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def running():
    """{serial: info} of the running fake emulators whose process is still alive."""
    result = {}
    for path in sorted(RUNNING.glob("emulator-*.json")):
        try:
            info = json.loads(path.read_text())
        except ValueError:
            continue
        if not alive(info["pid"]):
            continue
        if not info.get("hidden"):
            result[path.stem] = info
    return result


def physical_devices():
    path = STATE / "devices.json"
    return json.loads(path.read_text()) if path.exists() else []


def is_offline(info):
    state = info.get("offline")
    return state in ("until_reconnect", "forever") or (isinstance(state, int) and state > 0)


def write_running(serial, info):
    tmp = RUNNING / f".{serial}.tmp"
    tmp.write_text(json.dumps(info))
    tmp.rename(RUNNING / f"{serial}.json")   # atomic: adb never sees a half-written file


# --- adb ------------------------------------------------------------------------------------

def adb(args):
    serial = None
    if args[:1] == ["-s"]:
        serial, args = args[1], args[2:]
    if args[:1] == ["devices"]:
        print("List of devices attached")
        for s, info in running().items():
            print(f"{s}\t{'offline' if is_offline(info) else 'device'}")
        for s in physical_devices():
            print(f"{s}\tdevice")
        print()
        return
    if args == ["reconnect", "offline"]:
        for s, info in running().items():
            if is_offline(info):
                print(f"reconnecting {s}")
                if info["offline"] == "until_reconnect":
                    info["offline"] = None
                    write_running(s, info)
        return
    if args[:1] in (["start-server"], ["kill-server"]):
        return

    emulators = running()
    devices = list(emulators) + physical_devices()
    serial = serial or os.environ.get("ANDROID_SERIAL")
    if serial is None:
        if not devices:
            fail("adb: no devices/emulators found")
        if len(devices) > 1:
            fail("adb: more than one device/emulator")
        serial = devices[0]
    if serial not in devices:
        fail(f"adb: device '{serial}' not found")

    if args[:1] == ["wait-for-device"]:
        return
    if args[:1] == ["shell"] and not sys.stdin.isatty():
        sys.stdin.read()   # the real adb shell forwards its stdin to the device, using it up
    if args[:1] == ["shell"] and serial in emulators and is_offline(emulators[serial]):
        info = emulators[serial]
        if isinstance(info["offline"], int):
            info["offline"] -= 1
            write_running(serial, info)
        fail("error: device offline")
    if args[:1] == ["emu"]:
        if serial not in emulators:
            fail(f"error: {serial} is not an emulator")
        adb_emu(serial, emulators[serial], args[1:])
    elif args[:3] == ["shell", "getprop", "sys.boot_completed"]:
        hang_if("getprop")
        if serial in emulators:
            info = emulators[serial]
            info["polls"] += 1
            write_running(serial, info)
            ready = info["polls"] > behavior()["boot_polls"]
        else:
            ready = True
        sys.stdout.write("1\r\n" if ready else "\r\n")   # real adb shell output ends in \r\n
    elif args[:4] == ["shell", "settings", "get", "secure"] and len(args) == 5:
        settings = emulators[serial]["settings"] if serial in emulators else {}
        sys.stdout.write(f"{settings.get(args[4], 'null')}\r\n")
    elif args[:4] == ["shell", "settings", "put", "secure"] and len(args) == 6:
        error = behavior()["settings_put_error"]
        if error:
            fail(error)
        if serial in emulators:
            info = emulators[serial]
            info["settings"][args[4]] = args[5]
            write_running(serial, info)
    elif args[:3] == ["shell", "input", "keyevent"] and len(args) == 4:
        if take("keyevent", behavior()["keyevent_failures"]):
            fail("error: closed")
    else:
        fail(f"fake adb: unsupported command {args}")


def adb_emu(serial, info, args):
    if args == ["avd", "name"]:
        hang_if("avd_name")
        sys.stdout.write(f"{info['avd']}\r\nOK\r\n")
    elif args == ["avd", "path"]:
        sys.stdout.write(f"{avd_folder(info['avd'])}\r\nOK\r\n")
    elif args == ["kill"]:
        hang_if("emu_kill")
        print("OK: killing emulator, bye bye")
        if info.get("stuck"):
            return
        delay = behavior()["emulator_exit_delay"]
        if delay:
            # Still running until it has saved its snapshot (see boot()), but gone from adb.
            info["stop_at"] = time.time() + delay
            info["hidden"] = True
            write_running(serial, info)
        else:
            (RUNNING / f"{serial}.json").unlink(missing_ok=True)
            os.kill(info["pid"], signal.SIGTERM)
    else:
        fail(f"fake adb: unsupported emu command {args}")


# --- emulator -------------------------------------------------------------------------------

def emulator(args):
    if args == ["-list-avds"]:
        if behavior()["emulator_noise"]:
            print("INFO    | Storing crashdata in: /tmp/android-unknown/emu-crash-37.db")
        for name in avd_names():
            print(name)
        return
    if args[:1] != ["-avd"] or len(args) < 2:
        fail(f"fake emulator: unsupported arguments {args}")
    print("INFO         | Android emulator version 37.1.11.0 (fake)")
    crash = behavior()["emulator_crash"]
    if crash:
        fail(crash)
    name = args[1]
    if name not in avd_names():
        fail(f"ERROR        | Unknown AVD name [{name}], use -list-avds to see valid list.")
    boot(name)


def boot(name):
    """Registers as a running emulator on the first free port, then runs until killed.
    Like the real one, it writes its PID into hardware-qemu.ini.lock in the AVD's folder
    (NUL-padded) and deletes that file when it exits, unless it's killed with SIGKILL."""
    taken = set(running())
    port = FIRST_PORT
    while f"emulator-{port}" in taken:
        port += 2
    serial = f"emulator-{port}"
    stuck = behavior()["emulator_stuck"]
    signal.signal(signal.SIGTERM, signal.SIG_IGN if stuck else lambda *_: sys.exit(0))
    lock = avd_folder(name) / "hardware-qemu.ini.lock"
    lock.write_bytes(str(os.getpid()).encode().ljust(8, b"\0"))
    write_running(serial, {"avd": name, "pid": os.getpid(), "polls": 0,
                           "settings": dict(behavior()["device_settings"]),
                           "offline": behavior()["adb_offline"],
                           "hidden": behavior()["emulator_hidden"], "stuck": stuck})
    print(f"INFO         | Booted {name} as {serial} (fake)", flush=True)
    path = RUNNING / f"{serial}.json"
    deadline = time.time() + 120   # never outlive a test run, even if cleanup is skipped
    try:
        while time.time() < deadline and path.exists():
            try:
                stop_at = json.loads(path.read_text()).get("stop_at")
            except (OSError, ValueError):
                stop_at = None
            if stop_at and time.time() >= stop_at:
                path.unlink(missing_ok=True)
                break
            time.sleep(0.1)
    finally:
        lock.unlink(missing_ok=True)


# --- avdmanager -----------------------------------------------------------------------------

def option(args, name):
    return args[args.index(name) + 1] if name in args else None


def avdmanager(args):
    if args[:2] == ["create", "avd"]:
        sys.stdin.read()   # the script pipes "no" to the custom-hardware-profile question
        error = behavior()["avdmanager_error"]
        if error:
            fail(error)
        name = option(args, "--name") or option(args, "-n")
        home = avdmanager_home()
        if (home / f"{name}.ini").exists():
            fail(f"Error: Android Virtual Device '{name}' already exists.")
        folder = home / f"{name}.avd"
        folder.mkdir(parents=True)
        (home / f"{name}.ini").write_text(
            f"avd.ini.encoding=UTF-8\npath={folder}\npath.rel=avd/{name}.avd\n"
            "target=android-25\n")
        # The keys the script changes are present with other values, as in a real config.ini.
        image = option(args, "--package") or option(args, "-k")
        tag = option(args, "--tag") or option(args, "-g") or "default"
        (folder / "config.ini").write_text(
            "avd.ini.encoding=UTF-8\nhw.keyboard=no\nhw.ramSize=1536\n"
            f"image.sysdir.1={image.replace(';', '/')}/\n"
            f"hw.initialOrientation=portrait\ntag.id={tag}\ntag.ids={tag}\n"
            "hw.lcd.width=1920\nhw.lcd.height=1080\nhw.lcd.density=320\n")
    elif args[:2] == ["list", "avd"]:
        print("\n".join(avd_names()))
    else:
        fail(f"fake avdmanager: unsupported arguments {args}")


# --- host commands --------------------------------------------------------------------------

def getent(args):
    if args == ["group", "kvm"]:
        # The gid decides whether the kvm group owns the device: start-emulator.sh compares it
        # with the device's own gid, and a mismatch means joining the group can't grant access.
        gid = behavior()["kvm_group_gid"]
        if gid is None:
            gid = os.stat(os.environ["ADT_KVM_DEVICE"]).st_gid
        print(f"kvm:x:{gid}:" + ",".join(behavior()["kvm_group_members"]))
    else:
        sys.exit(2)


def android(args):
    # Presence-only: the scripts suggest "android sdk install ..." in messages but never run it.
    fail("fake android: the scripts must not run the android CLI")


def sdkmanager(args):
    fail("fake sdkmanager: the scripts must not run sdkmanager")


def sg(args):
    # sg <group> -c <command>: the real one runs the command with the group added. The fake
    # can't grant anything, so the command runs as is; tests see whether and how it was called.
    if len(args) == 3 and args[1] == "-c":
        os.execvp("bash", ["bash", "-c", args[2]])
    fail(f"fake sg: unsupported arguments {args}")


def id_(args):
    # id -un: the user's name. id -nG <user>: that user's groups from the user database, primary
    # group first (the primary group's getent line doesn't list its users).
    b = behavior()
    if args == ["-un"]:
        print(b["user"])
    elif args[:1] == ["-nG"] and len(args) == 2:
        user = args[1]
        primary = "kvm" if user == b["user"] and b["kvm_primary_group"] else user
        others = ["kvm"] if user in b["kvm_group_members"] and primary != "kvm" else []
        print(" ".join([primary, *others]))
    else:
        fail(f"fake id: unsupported arguments {args}")


def python3(args):
    # Stands in for wslg-toolbar.py, which reports what it did on stdout.
    print("wslg-toolbar: toolbar hidden")
    sys.exit(behavior()["python3_exit"])


TOOLS = {"adb": adb, "emulator": emulator, "avdmanager": avdmanager,
         "android": android, "sdkmanager": sdkmanager,
         "getent": getent, "id": id_, "sg": sg, "python3": python3}


def main(tool):
    args = sys.argv[1:]
    log_call(tool, args)
    TOOLS[tool](args)
