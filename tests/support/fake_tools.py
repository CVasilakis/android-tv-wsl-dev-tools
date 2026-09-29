"""Fake versions of the external programs the scripts call: adb, emulator, avdmanager, and the
host commands python3, getent, id and sg.

Each fake is a small wrapper file (written by sandbox.py) that calls main(<tool name>). The fakes
behave like the real tools as far as the scripts can tell (same output formats, exit codes and
side effects) and share their state through files in $FAKE_STATE, so a test can arrange a
situation (AVDs, running emulators, connected devices, failures) and afterwards check every call
that was made. They never touch the real SDK, emulator or devices.

State directory ($FAKE_STATE):
  calls.jsonl             one JSON line per call: {"tool", "argv", "android_serial", "time"}
                          (time: time.time() when it was made)
  behavior.json           knobs set by the test, see DEFAULT_BEHAVIOR
  running/<serial>.json   a running fake emulator: {"avd", "pid", "polls", "offline", "hidden",
                          "stop_at", "stuck", "discoverable", "settings" (secure),
                          "system_settings"}
  device/<serial>.json    what's on a device, emulator or physical: {"files": {path: text}
                          (adb push), "key_events": [...] (injected by monkey)}
  devices.json            serials of connected physical devices
  counters.json           per-knob counters (e.g. how many key events failed so far)
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

HOME = "com.google.android.tvlauncher/.MainActivity"
HOME_WINDOW = "com.google.android.tvlauncher/com.google.android.tvlauncher.MainActivity"

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
    "emulator_already_exiting": False,  # `adb emu kill` finds a started emulator shutting down
                                  # already (sent SIGTERM by someone else, e.g. with a test run that
                                  # was killed): its console no longer answers that call, which
                                  # fails, and the emulator exits then
    "adb_lists_exited": 0,        # adb devices keeps listing an emulator whose process has exited
                                  # (a zombie, or gone) as offline for this many more calls, as adb
                                  # notices only a moment later; meanwhile its console refuses
                                  # connections
    "emulator_lock_file": True,   # a started emulator writes hardware-qemu.ini.lock; False: it
                                  # writes none, like a real one started with -read-only
    "emulator_discoverable": True,  # a started emulator's console answers `adb emu avd
                                  # discoverypath` with the path of its discovery file,
                                  # pid_<pid>.ini; False: like a console without that command,
                                  # it answers with its help and "KO:  bad sub-command"
    "emulator_noise": True,       # emulator -list-avds prints a log line before the names
    "keyevent_failures": 0,       # the first N `adb shell input keyevent` calls fail
    "device_settings": {},        # secure settings a newly booted emulator starts with, e.g.
                                  # {"tv_user_setup_complete": "1"}; unset ones read as "null"
    "device_system_settings": {"accelerometer_rotation": "0", "user_rotation": "0"},
                                  # system settings a newly booted emulator starts with (the TV
                                  # images' values)
    "settings_put_error": None,   # `adb shell settings put` prints this and exits 1
    "monkey_output": None,        # `adb shell monkey` prints this and injects nothing (it failed)
    "api_level": 25,              # every device's `getprop ro.build.version.sdk`; below 24 there's
                                  # no `cmd` (adb shell prints "cmd: not found" and exits 0)
    "home_resolves": [HOME],      # what `cmd package resolve-activity --brief` answers for a HOME
                                  # intent, one per call, the last one repeated: e.g. FallbackHome
                                  # (FALLBACK_HOME) first, as until the user is unlocked
    "front": [[HOME, HOME_WINDOW]],  # [focused activity (mFocusedApp), focused window's title
                                  # (mCurrentFocus)] that `dumpsys window` shows, one per call, the
                                  # last one repeated; None: null. A "BACK" item: the ones after it
                                  # come only once `input keyevent BACK` was sent (one per BACK).
                                  # Before API 24, `dumpsys activity activities` shows the same
                                  # activity focused, in a task that a HOME intent started if it's
                                  # of the last home_resolves' package
    "adb_hangs": {},              # {"getprop": N, "avd_name": N, "emu_kill": N}: the first N
                                  # `adb shell getprop sys.boot_completed`, `adb emu avd name` or
                                  # `adb emu kill` calls never return, like adb on a half-booted
                                  # emulator starved of CPU, or on a hung one
    "adb_late": [],               # ["emu_kill"]: `adb emu kill` is made (and logged) only in the
                                  # last 0.1 s before the clock's next whole second, where a time
                                  # limit counted in whole seconds ($SECONDS) would end early
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


def counters():
    path = STATE / "counters.json"
    return json.loads(path.read_text()) if path.exists() else {}


def take(counter, limit):
    """Counts calls for a knob: True for the first `limit` calls, then False."""
    counted = counters()
    counted[counter] = counted.get(counter, 0) + 1
    (STATE / "counters.json").write_text(json.dumps(counted))
    return counted[counter] <= limit


def step(knob):
    """For knobs that list what a device shows over time: this call's item, the last one once
    they're used up."""
    path = STATE / "counters.json"
    counts = json.loads(path.read_text()) if path.exists() else {}
    counts[knob] = counts.get(knob, 0) + 1
    path.write_text(json.dumps(counts))
    items = behavior()[knob]
    return items[min(counts[knob], len(items)) - 1]


def front(advance=True):
    """What's in front for this call, from the `front` knob: its items up to the next "BACK", one
    per call (the last one repeated), until as many Back keys as BACKs before them were sent."""
    parts = [[]]
    for item in behavior()["front"]:
        if item == "BACK":
            parts.append([])
        else:
            parts[-1].append(item)
    path = STATE / "counters.json"
    counts = json.loads(path.read_text()) if path.exists() else {}
    part = min(counts.get("backs", 0), len(parts) - 1)
    key = f"front_{part}"
    if advance:
        counts[key] = counts.get(key, 0) + 1
        path.write_text(json.dumps(counts))
    return parts[part][min(max(counts.get(key, 1), 1), len(parts[part])) - 1]


def hang_if(call):
    """Never returns for the first adb_hangs[call] calls, until something kills this process."""
    if take(f"hang_{call}", behavior()["adb_hangs"].get(call, 0)):
        time.sleep(3600)


def log_call(tool, argv):
    if os.environ.get("FAKE_NO_LOG"):    # set by the sandbox for calls that only arrange a test
        return
    with open(STATE / "calls.jsonl", "a") as f:
        f.write(json.dumps({"tool": tool, "argv": argv, "time": time.time(),
                            "android_serial": os.environ.get("ANDROID_SERIAL")}) + "\n")


LATE_CALLS = {"emu_kill": lambda tool, args: tool == "adb" and args[-2:] == ["emu", "kill"]}


def wait_if_late(tool, args):
    """For the calls adb_late names: waits until the last 0.1 s before the next whole second."""
    if any(LATE_CALLS[call](tool, args) for call in behavior()["adb_late"]):
        time.sleep((0.9 - time.time() % 1) % 1)


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


def listed_exited(poll=False):
    """{serial: info} of the fake emulators whose process has exited but that adb still lists
    (adb_lists_exited); poll=True counts this as one of those listings (adb devices)."""
    limit = behavior()["adb_lists_exited"]
    result = {}
    for path in sorted(RUNNING.glob("emulator-*.json")) if limit else ():
        try:
            info = json.loads(path.read_text())
        except ValueError:
            continue
        if alive(info["pid"]) or info.get("hidden"):
            continue
        counter = f"listed_exited_{info['pid']}"
        listed = take(counter, limit) if poll else counters().get(counter, 0) < limit
        if listed:
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
        for s in listed_exited(poll=True):
            print(f"{s}\toffline")
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
    if serial not in devices and serial in listed_exited():
        if args[:1] == ["emu"]:
            fail(f"error: could not connect to TCP port {serial.split('-')[1]}: Connection refused")
        fail("error: device offline")
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
    elif args[:3] == ["shell", "settings", "get"] and len(args) == 5 and args[3] in NAMESPACES:
        settings = emulators[serial][NAMESPACES[args[3]]] if serial in emulators else {}
        sys.stdout.write(f"{settings.get(args[4], 'null')}\r\n")
    elif args[:3] == ["shell", "settings", "put"] and len(args) == 6 and args[3] in NAMESPACES:
        error = behavior()["settings_put_error"]
        if error:
            fail(error)
        if serial in emulators:
            info = emulators[serial]
            info[NAMESPACES[args[3]]][args[4]] = args[5]
            write_running(serial, info)
    elif args[:1] == ["push"] and len(args) == 3:
        try:
            text = Path(args[1]).read_text()
        except OSError:
            fail(f"adb: error: cannot stat '{args[1]}': No such file or directory")
        state = device_state(serial)
        state["files"][args[2]] = text
        write_device_state(serial, state)
        print(f"{args[1]}: 1 file pushed, 0 skipped.")
    elif args[:3] == ["shell", "rm", "-f"] and len(args) == 4:
        state = device_state(serial)
        state["files"].pop(args[3], None)
        write_device_state(serial, state)
    elif args[:2] == ["shell", "monkey"]:
        monkey(serial, emulators, args[2:])
    elif args[:3] == ["shell", "input", "keyevent"] and len(args) == 4:
        if take("keyevent", behavior()["keyevent_failures"]):
            fail("error: closed")
        if args[3] in ("BACK", "4"):
            take("backs", 0)   # only counts them, for front()
    elif args[:4] == ["shell", "input", "keyevent", "--longpress"] and len(args) == 5:
        input_long_press(serial, emulators, int(args[4]))
    elif args == ["shell", "getprop", "ro.build.version.sdk"]:
        sys.stdout.write(f"{behavior()['api_level']}\r\n")
    elif args[:2] == ["shell", "cmd"] and behavior()["api_level"] < 24:
        sys.stdout.write("/system/bin/sh: cmd: not found\r\n")
    elif args[:4] == ["shell", "cmd", "package", "resolve-activity"] \
            and "android.intent.category.HOME" in args:
        sys.stdout.write("priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 "
                         f"isDefault=true\r\n{step('home_resolves')}\r\n")
    elif args == ["shell", "dumpsys", "window"]:
        dumpsys_window(*front())
    elif args == ["shell", "dumpsys", "activity", "activities"] and behavior()["api_level"] < 24:
        dumpsys_activities(front(advance=False)[0])
    elif args[:4] == ["shell", "settings", "delete", "system"] and len(args) == 5:
        if serial in emulators:
            info = emulators[serial]
            info["system_settings"].pop(args[4], None)
            write_running(serial, info)
    else:
        fail(f"fake adb: unsupported command {args}")


NAMESPACES = {"secure": "settings", "system": "system_settings"}   # -> key in running/<serial>.json


def dumpsys_window(activity, window):
    """The lines of `dumpsys window` on focus, among others, in the format of the API level."""
    record = f"ActivityRecord{{2df0e36e u0 {activity} t7}}" if activity else "null"
    if activity and behavior()["api_level"] < 29:
        record = f"AppWindowToken{{1c158a9c token=Token{{9f08a0f {record}}}}}"
    focus = f"Window{{299ed2cc u0 {window}}}" if window else "null"
    sys.stdout.write("WINDOW MANAGER WINDOWS (dumpsys window windows)\r\n"
                     f"  Window #0 Window{{5e1 u0 StatusBar}}:\r\n    mOwnerUid=10012\r\n"
                     f"  mCurrentFocus={focus}\r\n  mFocusedApp={record}\r\n")


def dumpsys_activities(activity):
    """`dumpsys activity activities` before API 24: the focused activity, in a task a HOME intent
    started if it's of the home package (the last of home_resolves), else in one of its own."""
    home = behavior()["home_resolves"][-1]
    if activity and activity.split("/")[0] == home.split("/")[0]:
        intent = "act=android.intent.action.MAIN cat=[android.intent.category.HOME] flg=0x10800000"
    else:
        intent = "act=android.settings.SETTINGS flg=0x10000000"
    focused = f"ActivityRecord{{2df0e36e u0 {activity} t7}}" if activity else "null"
    sys.stdout.write("ACTIVITY MANAGER ACTIVITIES (dumpsys activity activities)\r\n"
                     "  Stack #0:\r\n    Task id #7\r\n"
                     f"    * TaskRecord{{36c46df0 #7 I={activity} U=0 sz=1}}\r\n"
                     f"      intent={{{intent} cmp={activity}}}\r\n"
                     f"      * Hist #0: ActivityRecord{{2df0e36e u0 {activity} t7}}\r\n"
                     f"          Intent {{ {intent} }}\r\n"
                     f"  mFocusedActivity: {focused}\r\n"
                     "  mFocusedStack=ActivityStack{266f8dee stackId=0, 1 tasks}\r\n")


def device_state(serial):
    path = STATE / "device" / f"{serial}.json"
    return json.loads(path.read_text()) if path.exists() else {"files": {}, "key_events": []}


def write_device_state(serial, state):
    (STATE / "device").mkdir(exist_ok=True)
    (STATE / "device" / f"{serial}.json").write_text(json.dumps(state))


def input_long_press(serial, emulators, code):
    """`input keyevent --longpress <code>`: what it sends depends on the device's API level. From
    30 on: down; after the long-press timeout a repeat flagged as a long press; the release right
    after. Before: all three at once, which holds nothing."""
    setting = emulators[serial]["settings"].get("long_press_timeout", "") if serial in emulators else ""
    timeout = int(setting) if setting.isdigit() else 400
    if behavior()["api_level"] < 30:
        timeout = 0
    state = device_state(serial)
    state["key_events"] += [
        {"action": "down", "code": code, "repeat": 0, "at_ms": 0, "down_ms": 0, "long_press": False},
        {"action": "down", "code": code, "repeat": 1, "at_ms": timeout, "down_ms": 0, "long_press": True},
        {"action": "up", "code": code, "repeat": 0, "at_ms": timeout, "down_ms": 0, "long_press": False}]
    write_device_state(serial, state)


def monkey(serial, emulators, args):
    """`monkey [-c <category>]... -f <script> <count>`: replays the key events of a script pushed
    to the device, recording them as Android receives them, with times in ms from the first one.
    Like the real monkey, it waits between events as their event times say, gives each event the
    down time of the one before when their recorded down times match (else its recorded one),
    only starts if an activity has one of the categories (TVs have none in LAUNCHER, the default),
    and when it exits it locks the rotation at 0 and unlocks it again."""
    categories = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-c"]
    script = next((args[i + 1] for i, a in enumerate(args[:-1]) if a == "-f"), None)
    output = behavior()["monkey_output"]
    if output:
        print(output)
        return
    if "android.intent.category.HOME" not in categories:
        for category in categories or ["android.intent.category.LAUNCHER"]:
            print(f"// Warning: no activities found for category {category}")
        print("** No activities found to run, monkey aborted.")
        return
    state = device_state(serial)
    if script not in state["files"]:
        fail(f"** Error: script file {script} not found")   # made up: not what the scripts check
    lines = [line.strip() for line in state["files"][script].splitlines()]
    body = lines[lines.index("start data >>") + 1:] if "start data >>" in lines else []
    first = recorded_down = down = None
    injected = 0
    for line in body:
        event = re.fullmatch(r"DispatchKey\((-?\d+(?:,-?\d+){7})\)", line)
        if not event:
            continue
        recorded, at, action, code, repeat = map(int, event.group(1).split(",")[:5])
        if first is None:
            first, down = at, 0
        elif recorded != recorded_down:
            down = recorded - first
        recorded_down = recorded
        injected += 1
        state["key_events"].append({"action": "down" if action == 0 else "up", "code": code,
                                    "repeat": repeat, "at_ms": at - first, "down_ms": down,
                                    # set by Android's input dispatcher on a first repeat
                                    "long_press": action == 0 and repeat == 1})
    write_device_state(serial, state)
    if serial in emulators:
        info = emulators[serial]
        info["system_settings"].update(accelerometer_rotation="1", user_rotation="0")
        write_running(serial, info)
    print(f"Events injected: {injected}")


def adb_emu(serial, info, args):
    if args == ["avd", "name"]:
        hang_if("avd_name")
        sys.stdout.write(f"{info['avd']}\r\nOK\r\n")
    elif args == ["avd", "path"]:
        sys.stdout.write(f"{avd_folder(info['avd'])}\r\nOK\r\n")
    elif args == ["avd", "discoverypath"]:
        if info.get("discoverable", True):
            sys.stdout.write(f"{STATE / 'discovery'}/pid_{info['pid']}.ini\r\nOK\r\n")
        else:
            sys.stdout.write("allows you to control (e.g. start/stop) the execution of the virtual "
                             "device\r\n\r\navailable sub-commands:\r\n    name             "
                             "query virtual device name\r\n\r\nKO:  bad sub-command\r\n")
    elif args == ["kill"]:
        hang_if("emu_kill")
        if behavior()["emulator_already_exiting"]:
            os.kill(info["pid"], signal.SIGTERM)
            deadline = time.time() + 5
            while alive(info["pid"]) and time.time() < deadline:
                time.sleep(0.02)
            fail(f"error: could not connect to TCP port {serial.split('-')[1]}: Connection refused")
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
    (NUL-padded) and deletes that file when it exits, unless it's killed with SIGKILL, or writes
    none (emulator_lock_file). Several can run for one AVD, as real -read-only ones can."""
    taken = set(running())
    port = FIRST_PORT
    while f"emulator-{port}" in taken:
        port += 2
    serial = f"emulator-{port}"
    stuck = behavior()["emulator_stuck"]
    signal.signal(signal.SIGTERM, signal.SIG_IGN if stuck else lambda *_: sys.exit(0))
    lock = avd_folder(name) / "hardware-qemu.ini.lock" if behavior()["emulator_lock_file"] else None
    if lock:
        lock.write_bytes(str(os.getpid()).encode().ljust(8, b"\0"))
    write_running(serial, {"avd": name, "pid": os.getpid(), "polls": 0,
                           "settings": dict(behavior()["device_settings"]),
                           "system_settings": dict(behavior()["device_system_settings"]),
                           "offline": behavior()["adb_offline"],
                           "hidden": behavior()["emulator_hidden"], "stuck": stuck,
                           "discoverable": behavior()["emulator_discoverable"]})
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
        if lock:
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
    wait_if_late(tool, args)
    log_call(tool, args)
    TOOLS[tool](args)
