# Using the tools in CI (GitHub Actions)

How to boot an Android TV emulator with these scripts on a GitHub-hosted runner and run a
project's instrumented tests on it. For a workstation, see [`SETUP.md`](SETUP.md) instead.

**Runners:** GitHub's Linux x86_64 runners (`ubuntu-*`), which have KVM. Not arm64 runners, which
can't run the x86 system images at hardware speed, and not macOS or Windows: the scripts need
Linux, bash 4+ and GNU coreutils.

## What the runner needs

The same as a workstation, without the WSL parts:

| Need | Why | On a GitHub runner |
|---|---|---|
| Write access to `/dev/kvm` | The x86 emulator won't start without hardware acceleration. | `/dev/kvm` exists, but the runner user can't write it; a udev rule fixes that (below). |
| `libpulse0` | The emulator's only system library it doesn't bundle; without it, it fails at start, even with `-no-audio`. | `apt-get install` it. |
| The SDK packages `emulator`, `platform-tools` and the system image | `create-avd.sh` and `start-emulator.sh` ([`SETUP.md`](SETUP.md), steps 4 and 5). | The runner image has an SDK in `$ANDROID_HOME`, which the scripts find; install the missing packages with its `sdkmanager`. |
| Disk space | A system image takes 1.4 to 8.2 GB on disk ([sizes](SETUP.md#sizes)), and each AVD 1 to 2.5 GB more. | Check with `df -h` before adding API levels; API 36 is the largest. |

Use `sdkmanager` rather than the newer `android` CLI here: the `android` CLI downloads itself
(~250 MB) on its first run, which on a fresh runner is every run.

The scripts need nothing else from the runner: without `$DISPLAY`, `start-emulator.sh` runs the
emulator without a window by itself.

## Example workflow

Runs `connectedDebugAndroidTest` on API 25, 30 and 36, one emulator per job:

```yaml
name: Instrumented tests
on: [push, pull_request]

jobs:
  connected-tests:
    runs-on: ubuntu-24.04
    timeout-minutes: 60
    strategy:
      fail-fast: false
      matrix:
        api: [25, 30, 36]
    steps:
      - uses: actions/checkout@v7                      # your project first: it empties the workspace
      - uses: actions/checkout@v7
        with:
          repository: CVasilakis/android-cli-dev-tools
          ref: v1.0.0                                  # a release tag, or a full commit SHA
          path: .android-cli-dev-tools
      - name: Put the tools on PATH
        run: echo "$GITHUB_WORKSPACE/.android-cli-dev-tools/bin" >> "$GITHUB_PATH"

      - name: Let the runner user open /dev/kvm
        run: |
          echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' \
            | sudo tee /etc/udev/rules.d/99-kvm4all.rules
          sudo udevadm control --reload-rules
          sudo udevadm trigger --name-match=kvm
      - name: Install the emulator's system library
        run: sudo apt-get update && sudo apt-get install -y libpulse0
      - name: Install the emulator and the system image
        run: >
          "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" "emulator" "platform-tools"
          "system-images;android-${{ matrix.api }};android-tv;x86" > /dev/null

      - name: Boot the emulator
        run: |
          create-avd.sh --api ${{ matrix.api }} --if-missing
          serial="$(start-emulator.sh tv_api${{ matrix.api }} -no-snapshot-save)"
          echo "ANDROID_SERIAL=$serial" >> "$GITHUB_ENV"
      - run: ./gradlew connectedDebugAndroidTest

      - name: Show the emulator's log
        if: failure()
        run: cat "/tmp/emulator-tv_api${{ matrix.api }}.log" || true
```

Your project's own build setup (JDK, Gradle caching) goes before the Gradle step as usual.

Why the steps look like this:

- **Pin a release** (`ref`), never `main`: see
  [Versions and compatibility](README.md#versions-and-compatibility).
- **`serial="$(…)"` on a line of its own.** `start-emulator.sh` prints the serial alone on stdout,
  and messages on stderr. As an assignment by itself, a failed boot fails the step; inside
  `echo "…$(start-emulator.sh)" >> …` the step would carry on with an empty serial.
- **`ANDROID_SERIAL`** makes adb and Gradle use that emulator in every later step.
- **`-no-snapshot-save`**: the emulator saves a Quick Boot snapshot when it's stopped, which is
  slow and of no use on a runner that's thrown away. Boots are cold by default anyway.
- **Time limits.** If Android doesn't finish booting, `start-emulator.sh` stops the emulator and
  fails after `ADT_BOOT_TIMEOUT` seconds (default 900); set it higher or lower in `env:`.
  `timeout-minutes` bounds the whole job, including the tests.
- **The log.** The emulator logs to `${TMPDIR:-/tmp}/emulator-<avd>.log`; the last step prints it
  when something failed. `start-emulator.sh` also prints its last lines when the emulator exits
  or times out.
- **One emulator per job.** A matrix keeps each job small. Several emulators in one job work too:
  boot each with its own `start-emulator.sh`, keep each serial, and pass it to adb with `-s`
  (Gradle's `connected…` tasks run on every booted emulator).
- **Stopping.** Not needed: the runner stops what's left when the job ends. To stop it earlier:
  `adb -s "$ANDROID_SERIAL" emu kill`.

## Caching

The system image is the big download. To skip it on later runs, cache
`$ANDROID_HOME/system-images/android-<level>` (for example with `actions/cache`), keyed on the
level, and restore it before the `sdkmanager` step, which then finds it installed.

Caching the AVD (`~/.android/avd`) saves little, since `create-avd.sh` is quick, and brings the
previous run's data partition along: apps installed and settings changed by earlier tests. If you
cache it anyway, `create-avd.sh --if-missing` keeps the cached AVD instead of failing on it (and
still fails if it was made from another image), and `start-emulator.sh … -wipe-data` starts it
from a clean data partition.
