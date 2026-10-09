#!/usr/bin/env python3
"""Runs the tests of the scripts in bin/. Standard library only; see README.md here."""
import argparse
import os
import sys
import unittest
from collections import Counter
from pathlib import Path

TESTS = Path(__file__).resolve().parent
TIERS = {"hermetic": "fast, fake SDK and devices (default)",
         "emulator": "real SDK and emulator, boots the AVD (slow)"}


def main():
    parser = argparse.ArgumentParser(
        prog="tests/run.py",
        description="Runs the behavior tests of the scripts in bin/.",
        epilog="Environment: ADT_AVD picks the AVD for --emulator, ADT_TEST_EMULATOR_FLAGS adds "
               "emulator flags to the boots it makes; SCRIPT_TESTS_DISPLAY=:0 "
               "runs the X11 tests on an existing display instead of Xvfb.")
    tier = parser.add_mutually_exclusive_group()
    tier.add_argument("--emulator", action="store_true", help="only the real-emulator tests")
    tier.add_argument("--all", action="store_true", help="both tiers")
    parser.add_argument("-k", dest="patterns", action="append", default=[], metavar="PATTERN",
                        help="only tests whose name contains PATTERN (repeatable)")
    parser.add_argument("-v", "--verbose", action="store_true", help="one line per test")
    parser.add_argument("--strict", action="store_true",
                        help="fail if any test was skipped, e.g. for a missing optional tool (CI)")
    args = parser.parse_args()

    tiers = ["emulator"] if args.emulator else list(TIERS) if args.all else ["hermetic"]
    if "emulator" in tiers:
        os.environ["ADT_EMULATOR_TESTS"] = "1"
    loader = unittest.TestLoader()
    loader.testNamePatterns = [f"*{p}*" for p in args.patterns] or None
    sys.path.insert(0, str(TESTS))
    suite = unittest.TestSuite(loader.discover(str(TESTS / t), top_level_dir=str(TESTS))
                               for t in tiers)
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    if result.skipped and not args.verbose:
        reasons = Counter(reason for _, reason in result.skipped)
        for reason, count in reasons.items():
            print(f"skipped {count}: {reason}")
    # In CI a skip would pass unnoticed, e.g. the X11 tests once Xvfb is missing from the runner.
    if args.strict and result.skipped:
        sys.stdout.flush()   # after the reasons above
        print(f"--strict: {len(result.skipped)} tests were skipped", file=sys.stderr)
        sys.exit(1)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
