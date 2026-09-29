"""input_dump.py: how the emulator tier reads Android's input dispatcher, from made-up dumps."""
import math
import unittest

from support.input_dump import LOOKS_TO_DECIDE, RecentInput, input_is_quiet

KEY = "KeyEvent"   # how API 29 and newer print a key event


class Dispatcher:
    """Android's input dispatcher, as `cat /proc/uptime; dumpsys input; cat /proc/uptime` shows
    it. Times are the device's uptime in ms."""

    def __init__(self):
        self.events = []   # (handled at, event time, description), in the order handled
        self.looks = []    # (uptime read before, dumpsys's moment, uptime read after)

    def handle(self, description, at, since=None):
        """An event handled at `at` that happened at `since` (a key held for a starting window
        is handled later than it happened)."""
        self.events.append((at, at if since is None else since, description))

    def keys(self, *times):
        """A press (down and up, at the same time) at each time: `input keyevent`."""
        for t in times:
            self.handle(KEY, t)
            self.handle(KEY, t)

    def look(self, at, before=5, after=5):
        """A look whose dumpsys reads its clock at `at`, `before` ms after the first uptime read
        and `after` ms before the second."""
        self.looks.append((at - before, at, at + after))

    def dump(self, at):
        def age(t):   # ns2ms: whole ms, truncated towards 0
            return f"{math.trunc(at - t)}ms"
        handled = [e for e in self.events if e[0] <= at]
        pending = [e for e in self.events if e[1] <= at < e[0]]
        lines = [f"  RecentQueue: length={len(handled[-10:])}"]
        lines += [f"    {text}, age={age(t)}" for _, t, text in handled[-10:]]
        if pending:
            lines += ["  PendingEvent:", f"    {pending[0][2]}, age={age(pending[0][1])}"]
        else:
            lines += ["  PendingEvent: <none>"]
        lines += ["  InboundQueue: <empty>", "  ReplacedKeys: <empty>"]
        return "INPUT MANAGER (dumpsys input)\n" + "\n".join(lines) + "\n"

    def adb_shell(self, command):
        assert command == "cat /proc/uptime; dumpsys input; cat /proc/uptime", command
        before, at, after = self.looks.pop(0)

        def uptime(ms):   # /proc/uptime: seconds, in steps of 10 ms
            return f"{math.floor(ms / 10) / 100:.2f} 12.34\n"
        return uptime(before) + self.dump(at) + uptime(after)

    def watch(self):
        """RecentInput, after taking each look the test arranged."""
        received = RecentInput(self.adb_shell)
        while self.looks:
            received.look()
        return received


class RecentInputTest(unittest.TestCase):
    def setUp(self):
        self.device = Dispatcher()
        # Older events fill the queue, with nothing evenly spaced among them.
        for i, text in enumerate(["FocusEvent(hasFocus=false)", "FocusEvent(hasFocus=true)"] * 5):
            self.device.handle(text, 100 + 37 * i * i)

    def test_counts_evenly_spaced_identical_events(self):
        # A held key repeats every 50 ms: every repeat looks alike, and an overlap one repeat
        # off fits as well as the right one.
        for i in range(40):
            self.device.handle(KEY, 10_000 + 50 * i)
        for at in (10_460, 10_560, 10_760, 10_810):
            self.device.look(at)
        received = self.device.watch()
        self.assertEqual([t for t, _ in received.key_events()],
                         [40.0, 90.0, 140.0, 190.0, 240.0, 290.0, 340.0])

    def test_a_look_slower_than_its_uptime_reads_suggest_counts_a_key_once(self):
        # dumpsys can read its clock much later than the uptime read before it (API 31 image).
        self.device.keys(5_000, 5_700, 6_300)
        self.device.look(6_500)
        self.device.keys(6_800)
        self.device.look(7_400, before=600, after=10)
        self.device.keys(7_600)
        self.device.look(7_700, before=10, after=900)
        received = self.device.watch()
        self.assertEqual([t for t, _ in received.key_events()], [300.0, 300.0, 1100.0, 1100.0])
        self.assertEqual(received.key_downs(), [None, None])

    def test_a_key_held_for_a_starting_window_counts_once_after_the_events_it_waited_for(self):
        self.device.keys(5_000)
        self.device.look(5_100)
        self.device.handle("FocusEvent(hasFocus=false)", 5_300)
        self.device.handle("FocusEvent(hasFocus=true)", 5_690)
        self.device.handle(KEY, 5_700, since=5_250)   # held until the new window had focus
        self.device.handle(KEY, 5_700, since=5_250)
        self.device.look(5_500)   # the key is pending
        self.device.look(5_800)
        self.device.keys(6_000)
        self.device.look(6_100)
        received = self.device.watch()
        self.assertEqual(received.seen[-6:], [(200.0, "FocusEvent(hasFocus=false)"),
                                              (590.0, "FocusEvent(hasFocus=true)"),
                                              (150.0, KEY), (150.0, KEY),
                                              (900.0, KEY), (900.0, KEY)])
        self.assertEqual(received.key_downs(), [None, None])

    def test_a_key_from_before_the_first_look_that_arrives_later_does_not_count(self):
        # On a slow device `input keyevent` can send the release seconds after the press, with
        # the press's time: after a test's first look, for a key sent before it.
        self.device.handle(KEY, 5_000)
        self.device.look(5_100)
        self.device.handle(KEY, 5_300, since=5_000)
        self.device.keys(5_400)
        self.device.look(5_500)
        received = self.device.watch()
        self.assertEqual(received.seen[-3:], [(-100.0, KEY), (300.0, KEY), (300.0, KEY)])
        self.assertEqual([t for t, _ in received.key_events()], [300.0, 300.0])

    def test_reads_negative_ages(self):
        # An event timed after the moment dumpsys reads its clock has a negative age.
        self.device.keys(5_000)
        self.device.look(5_100)
        self.device.handle(KEY, 5_200, since=5_203)
        self.device.handle(KEY, 5_200, since=5_205)
        self.device.look(5_200)
        received = self.device.watch()
        self.assertIn("age=-3ms", self.device.dump(5_200))
        self.assertEqual([t for t, _ in received.key_events()], [103.0, 105.0])

    def test_an_event_without_an_age_fails_the_look(self):
        self.device.handle("KeyEvent, policyFlags=0x6b000000", 5_000)
        self.device.look(5_100)
        dump = self.device.dump
        self.device.dump = lambda at: dump(at).replace("0x6b000000, age=100ms", "0x6b000000")
        with self.assertRaisesRegex(AssertionError, "an event without an age"):
            self.device.watch()

    def test_more_than_10_events_between_two_looks_fail_the_look(self):
        self.device.keys(5_000)
        self.device.look(5_100)
        self.device.keys(*range(5_200, 5_800, 100))   # 12 events
        self.device.look(5_900)
        with self.assertRaisesRegex(AssertionError, "a look missed events: .* 10 or more came"):
            self.device.watch()

    def test_10_events_between_two_looks_fail_the_look_even_where_an_overlap_fits_by_chance(self):
        self.device.keys(5_000, 5_100, 5_200, 5_300, 5_400)
        self.device.look(5_500)
        self.device.keys(5_600, 5_700, 5_800, 5_900, 6_000)   # the same, 600 ms later
        self.device.look(6_100)
        with self.assertRaisesRegex(AssertionError, "a look missed events"):
            self.device.watch()

    def test_events_too_evenly_spaced_for_how_long_looks_take_fail_the_look(self):
        for i in range(40):
            self.device.handle(KEY, 10_000 + 50 * i)
        self.device.look(10_460)
        for _ in range(LOOKS_TO_DECIDE):
            self.device.look(10_560, before=60, after=60)
        with self.assertRaisesRegex(AssertionError, "can't tell how many events came between "
                                                    "two looks: overlaps that drop 1 or 2 or 3 "):
            self.device.watch()

    def test_a_press_that_is_all_left_of_the_look_before_counts_once(self):
        # 8 events came since: the look before's last two, a press's down and up at one moment,
        # are all that's left of it, and an overlap with only the up fits at the same time.
        self.device.keys(5_000)
        self.device.look(5_100)
        self.device.keys(5_300)
        for i, text in enumerate(["FocusEvent(hasFocus=false)", "FocusEvent(hasFocus=true)"] * 3):
            self.device.handle(text, 5_400 + 60 * i + 7 * i * i)
        for _ in range(LOOKS_TO_DECIDE):   # looking again doesn't help: both fit at the same time
            self.device.look(6_000)
        received = self.device.watch()
        self.assertEqual([t for t, _ in received.key_events()], [200.0, 200.0])

    def test_looks_again_until_the_clock_can_tell(self):
        for i in range(40):
            self.device.handle(KEY, 10_000 + 50 * i)
        self.device.look(10_460)
        self.device.look(10_560, before=60, after=60)
        self.device.look(10_600)
        received = self.device.watch()
        self.assertEqual([t for t, _ in received.key_events()], [40.0, 90.0, 140.0])

    def test_after_an_empty_queue_every_event_is_new_until_the_queue_is_full(self):
        self.device = Dispatcher()   # nothing handled yet since the boot
        self.device.look(1_000)
        self.device.keys(1_100)
        self.device.look(1_200)
        self.assertEqual([t for t, _ in self.device.watch().key_events()], [100.0, 100.0])
        self.device = Dispatcher()
        self.device.look(1_000)
        self.device.keys(*range(1_100, 1_600, 100))   # 10 events: maybe more, pushed out
        self.device.look(1_700)
        with self.assertRaisesRegex(AssertionError, "a look missed events"):
            self.device.watch()

    def test_events_drop_out_only_of_a_full_queue(self):
        # With the queue's first press dropped out, the rest would fit too.
        self.device = Dispatcher()
        self.device.keys(1_000)
        self.device.look(1_050)
        self.device.keys(1_100)
        self.device.look(1_150)
        self.assertEqual([t for t, _ in self.device.watch().key_events()], [50.0, 50.0])

    def test_uptime_counting_in_steps_of_10_ms_does_not_throw_out_the_right_overlap(self):
        # /proc/uptime says 1.00 s after the first look, and 1.01 s before the second, 1 ms later.
        self.device.keys(1_000)
        self.device.look(1_009, before=4, after=0.9)
        self.device.keys(1_010)
        self.device.look(1_010, before=0, after=4)
        self.assertEqual([t for t, _ in self.device.watch().key_events()], [1.0, 1.0])

    def test_looks_again_when_dumpsys_gives_up_before_the_recent_queue(self):
        self.device.look(5_000)
        self.device.keys(5_100)
        self.device.look(15_010, before=10_000)   # dumpsys took 10 s
        self.device.look(15_100)
        dump = self.device.dump
        timeout = "\n*** SERVICE 'input' DUMP TIMEOUT (10000ms) EXPIRED ***\n\n"
        self.device.dump = lambda at: timeout if at == 15_010 else dump(at)
        self.assertEqual([t for t, _ in self.device.watch().key_events()], [100.0, 100.0])
        self.device.looks = [(15_200, 15_200, 15_200)] * LOOKS_TO_DECIDE
        self.device.dump = lambda at: timeout
        with self.assertRaisesRegex(AssertionError, f"no recent queue in {LOOKS_TO_DECIDE} looks"):
            self.device.watch()

    def test_only_what_comes_after_the_first_look_counts(self):
        self.device.keys(5_000)
        self.device.look(5_100)
        self.device.look(5_300)
        self.assertEqual(self.device.watch().key_events(), [])

    def test_reads_the_details_android_9_and_older_print(self):
        def key(action, code):
            return (f"KeyEvent(deviceId=-1, source=0x00000101, action={action}, flags=0x00000000, "
                    f"keyCode={code}, scanCode=0, metaState=0x00000000, repeatCount=0), "
                    f"policyFlags=0x6b000000")
        self.device.look(5_000)
        for t, code in ((5_100, 19), (5_400, 23)):
            self.device.handle(key("DOWN", code), t)
            self.device.handle(key("UP", code), t)
            self.device.look(t + 50)
        self.assertEqual(self.device.watch().key_downs(), [19, 23])


class InputIsQuiet(unittest.TestCase):
    def setUp(self):
        self.device = Dispatcher()
        self.device.handle("FocusEvent(hasFocus=true)", 1_000)
        self.device.keys(5_000)

    def test_quiet_once_the_newest_event_is_old_enough(self):
        self.assertFalse(input_is_quiet(self.device.dump(5_999), 1000))
        self.assertTrue(input_is_quiet(self.device.dump(6_000), 1000))

    def test_not_quiet_while_a_key_waits_for_a_window(self):
        self.device.handle(KEY, 8_000, since=6_000)
        self.assertFalse(input_is_quiet(self.device.dump(7_500), 1000))
        self.assertIn("PendingEvent:\n    KeyEvent, age=1500ms", self.device.dump(7_500))

    def test_not_quiet_when_dumpsys_gives_up_before_the_recent_queue(self):
        self.assertFalse(input_is_quiet("*** SERVICE 'input' DUMP TIMEOUT (10000ms) EXPIRED ***",
                                        1000))

    def test_not_quiet_while_events_wait_in_the_inbound_queue(self):
        dump = self.device.dump(9_000).replace(
            "InboundQueue: <empty>", "InboundQueue: length=1\n    KeyEvent, age=2000ms")
        self.assertFalse(input_is_quiet(dump, 1000))

    def test_reads_the_queue_as_android_9_and_older_print_it(self):
        dump = self.device.dump(6_500).replace("age=1500ms", "age=1500.4ms")
        self.assertTrue(input_is_quiet(dump.replace("age=5500ms", "age=5500.4ms"), 1000))
        self.assertFalse(input_is_quiet(dump.replace("age=1500.4ms", "age=999.6ms"), 1000))
