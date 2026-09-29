"""Android's input dispatcher as `dumpsys input` shows it: the events it handled lately, and the
ones still waiting. Read by the emulator tier; tested with made-up dumps in the hermetic tier
(hermetic/test_input_dump.py)."""
import re
from collections import Counter

QUEUE_SIZE = 10   # the recent queue keeps the last 10 handled events, on every API level
# How far the time between two looks, as the events' ages tell it, may be outside what the uptime
# reads around them allow: /proc/uptime counts in steps of 10 ms, ages are rounded (to whole ms
# from API 29 on), and a margin.
CLOCK_SLACK_MS = 20
LOOKS_TO_DECIDE = 5   # looks one look() may take to tell what's new (see RecentInput)


def recent_queue(dump):
    """(age in ms, event without its age) of the recent queue's events, oldest first; None if the
    dump has none (on a slow device dumpsys gives up on a service after 10 s)."""
    if "RecentQueue" not in dump:
        return None
    queue = dump.split("RecentQueue", 1)[1].split("PendingEvent", 1)[0].splitlines()[1:]
    events = []
    for line in filter(str.strip, queue):
        age = re.search(r"age=(-?[\d.]+)ms", line)
        if not age:   # skipping it would shift the events after it
            raise AssertionError(f"an event without an age in the recent queue: {line.strip()!r}")
        events.append((float(age.group(1)), re.sub(r",? ?age=-?[\d.]+ms", "", line.strip())))
    return events


def input_is_quiet(dump, quiet_ms):
    """Whether no event is waiting to be handled, and the newest one handled is quiet_ms old. A
    key waits, as the pending event or in the inbound queue, while a window it's going to starts
    (up to 5 s): it enters the recent queue only then, with the age it had all along."""
    queue = recent_queue(dump)
    if queue is None or "PendingEvent: <none>" not in dump or "InboundQueue: <empty>" not in dump:
        return False
    return all(age >= quiet_ms for age, _ in queue)


class RecentInput:
    """The events Android's input dispatcher handles from now on, from repeated looks at its
    recent queue (`dumpsys input`). The queue keeps only the last 10 events, window focus changes
    included, and Android holds keys for up to 5 s while a window it's opening starts. A key that
    opens windows (OK on Google TV's profile or sign-in screen) brings several focus changes, so
    keys sent right before it can be pushed out before a look sees them. So each look is merged
    into what the ones before it saw, and tests look again after every key or long press: between
    two looks, only one key's worth of events has to fit.

    The queue is first in, first out: a look is the one before it without its oldest events, and
    with the new ones after (a key held for a new window comes after the events handled while it
    waited, with an older age). Where they overlap, each event is of the same kind, and each one's
    age has grown by the same amount, the time between the looks: an overlap that fits. Several
    can fit when events repeat evenly (API 29 and newer print every key event as a bare
    "KeyEvent"), and the fewest dropped isn't always right: with a held key's repeats, the
    overlap one repeat further on fits as well as the right one. So the device's clock decides:
    each look reads the uptime before and after `dumpsys`, whose own clock reading is somewhere
    in between, so the time between two looks is known give or take how long the two took. Only
    an overlap whose time falls in that range counts, and its time is the exact one, from the
    ages. The range must be all of it: `dumpsys` can read its clock long after the uptime read
    before it, and a narrower range throws out the right overlap. The clock can't tell two
    overlaps apart that fit at the very same time: all that's left of the look before is then
    alike events of one moment, a press's down and up (API 29 and newer), and the other overlap
    takes the up for a new event of that moment. No key the tests send has a third event at the
    moment of its press, so of those, the one that drops fewest is right.

    A look fails the test, rather than miscount, when no overlap fits the clock: every event the
    look before saw is gone, so 10 or more came in between and some may have been missed. It
    looks again when more than one fits, which a quicker look can settle, or when dumpsys gave up
    before the queue, and fails after LOOKS_TO_DECIDE looks that don't settle it."""

    def __init__(self, adb_shell):
        self.adb_shell = adb_shell
        # (ms since the first look, event without its age), in the order Android handled them,
        # which is the queue's: a key's release can have the time of its press (input keyevent
        # --longpress from API 30 on), and a key held for a new window comes after later events.
        self.seen = []
        self.last = []          # the latest look: (age, event), oldest first
        self.last_time = None   # the latest look's moment (dumpsys's clock), in ms since the first
        self.last_clock = None  # the device's uptime before and after the latest look, in ms
        self.look()
        self.before = Counter(self.seen)

    def look(self):
        """Takes one more look at the queue, adding the events no look showed before; fails the
        test if it can't tell which those are (see the class)."""
        for _ in range(LOOKS_TO_DECIDE):
            out = self.adb_shell("cat /proc/uptime; dumpsys input; cat /proc/uptime")
            lines = out.strip().splitlines()
            clock = (float(lines[0].split()[0]) * 1000, float(lines[-1].split()[0]) * 1000)
            events = recent_queue(out)
            if events is None:
                continue   # a look that saw nothing
            if self.last_clock is None:   # the first look: its moment is time 0
                self.add(events, 0, events, clock)
                return self
            # The time between the looks' two moments, as far as the clock can tell.
            low = clock[0] - self.last_clock[1] - CLOCK_SLACK_MS
            high = clock[1] - self.last_clock[0] + CLOCK_SLACK_MS
            if self.last or len(events) == QUEUE_SIZE:
                fits = [(dropped, gap) for dropped, gap in self.overlaps(events)
                        if low <= gap <= high]
            else:   # nothing to overlap, and nothing dropped: all new, at the clock's time
                fits = [(0, (clock[0] + clock[1] - sum(self.last_clock)) / 2)]
            if not fits:
                raise AssertionError(
                    f"a look missed events: none that the look before saw is left in the queue "
                    f"as the device's clock says it should be ({low:.0f}-{high:.0f} ms later), so "
                    f"{QUEUE_SIZE} or more came between two looks.\n"
                    f"Before: {self.last}\nThen: {events}")
            # Of overlaps that fit at the very same time, the one dropping fewest (see the class).
            fits = [(dropped, gap) for dropped, gap in fits
                    if not any(abs(other - gap) < 0.01 and fewer < dropped for fewer, other in fits)]
            if len(fits) == 1:
                [(dropped, gap)] = fits
                self.add(events[len(self.last) - dropped:], self.last_time + gap, events, clock)
                return self
        if events is None:
            raise AssertionError(f"no recent queue in {LOOKS_TO_DECIDE} looks at "
                                 f"`dumpsys input`:\n{out[-500:]}")
        raise AssertionError(
            f"can't tell how many events came between two looks: overlaps that drop "
            f"{' or '.join(str(dropped) for dropped, _ in fits)} of the look before's events all "
            f"fit the device's clock ({low:.0f}-{high:.0f} ms later), in {LOOKS_TO_DECIDE} looks. "
            f"Events come too evenly for looks that take this long.\n"
            f"Before: {self.last}\nThen: {events}")

    def overlaps(self, events):
        """(events of the last look that dropped out, time between the looks) of each overlap
        of this look with the last one that fits."""
        fits = []
        for dropped in range(len(self.last)):
            overlap = list(zip(self.last[dropped:], events))
            if len(overlap) < len(self.last) - dropped:
                continue   # this look is shorter than the rest of the last one
            if dropped and len(events) < QUEUE_SIZE:
                continue   # events drop out only of a full queue
            gaps = [new_age - old_age for (old_age, old), (new_age, new) in overlap if old == new]
            if len(gaps) == len(overlap) and max(gaps) - min(gaps) <= 1.5:
                fits.append((dropped, sum(gaps) / len(gaps)))
        return fits

    def add(self, new, now, events, clock):
        self.seen += [(round(now - age, 1), event) for age, event in new]
        self.last, self.last_time, self.last_clock = events, now, clock

    def key_events(self):
        """(ms since the first look, event) of the key events that happened since then, in
        order. A key from before can join the queue later, with its old time: one held for a
        window, or a release `input keyevent` sends seconds after the press, on a slow device.
        Android 5-8 print a key-down as action=0, 9 as action=DOWN, and 10 and newer print no
        details ("KeyEvent")."""
        before = Counter(self.before)
        events = []
        for event in self.seen:
            if before[event]:
                before[event] -= 1
            elif event[1].startswith("KeyEvent") and event[0] >= 0:
                events.append(event)
        return events

    def key_downs(self):
        """keyCodes of the key-downs since the first look, oldest first; where Android prints no
        details, one None per press (a down and an up)."""
        events = [event for _, event in self.key_events()]
        if events and "keyCode=" not in events[0]:
            return [None] * (len(events) // 2)
        return [int(re.search(r"keyCode=(\d+)", e).group(1)) for e in events
                if re.search(r"action=(?:0|DOWN),", e)]
