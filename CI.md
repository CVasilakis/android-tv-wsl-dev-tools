# Using the tools in CI (GitHub Actions)

How to boot an Android TV emulator with these scripts on a GitHub-hosted runner and run a
project's instrumented tests on it. For a workstation, see [`SETUP.md`](SETUP.md) instead.

**Runners:** GitHub's Linux x86_64 runners (`ubuntu-*`), which have KVM. Not arm64 runners, which
can't run the x86 system images at hardware speed, and not macOS or Windows: the scripts need
Linux, bash 4+ and GNU coreutils.

## What the runner needs

The same as a workstation ([`SETUP.md`](SETUP.md)), without the WSL parts:

| Need | On a GitHub runner |
|---|---|
| Write access to `/dev/kvm` | `/dev/kvm` exists, but the runner user can't write it; a udev rule fixes that (below). |
| `libpulse0` ([`SETUP.md`](SETUP.md), step 1) | `apt-get install` it. |
| The SDK packages `emulator`, `platform-tools` and the system image ([`SETUP.md`](SETUP.md), steps 4 and 5) | The runner image has an SDK in `$ANDROID_HOME`, which the scripts find; install the missing packages with its `sdkmanager`. |
| Disk space for the system images and AVDs ([sizes](SETUP.md#sizes)) | Not enough, or only just, once a system image is installed: delete preinstalled toolchains first (below). |

Use `sdkmanager` rather than the newer `android` CLI here: the `android` CLI downloads itself on
its first run, which on a fresh runner is every run.

The scripts need nothing else from the runner: without `$DISPLAY`, `start-emulator.sh` runs the
emulator without a window by itself ([`bin/README.md`](bin/README.md#start-emulatorsh)).

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
          repository: CVasilakis/android-tv-wsl-dev-tools
          ref: v1.6.1                                  # a release tag, or a full commit SHA
          path: .android-tv-wsl-dev-tools
      - name: Put the tools on PATH
        run: echo "$GITHUB_WORKSPACE/.android-tv-wsl-dev-tools/bin" >> "$GITHUB_PATH"

      - name: Free disk space for the system image and the AVD
        run: |
          sudo rm -rf /usr/share/dotnet /opt/ghc /usr/local/.ghcup /usr/local/share/boost \
            /opt/hostedtoolcache/CodeQL "$ANDROID_HOME/ndk"
          df -h /
      - name: Let the runner user open /dev/kvm
        run: |
          echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' \
            | sudo tee /etc/udev/rules.d/99-kvm4all.rules
          sudo udevadm control --reload-rules
          sudo udevadm trigger --name-match=kvm
      - name: Install the emulator's system library
        run: sudo apt-get update && sudo apt-get install -y libpulse0
      - name: Install the emulator and the system image
        run: |
          sdkmanager="$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager"
          yes 2> /dev/null | "$sdkmanager" --licenses > /dev/null
          "$sdkmanager" "emulator" "platform-tools" "system-images;android-${{ matrix.api }};android-tv;x86" \
            > "$RUNNER_TEMP/sdkmanager.log" || { cat "$RUNNER_TEMP/sdkmanager.log"; exit 1; }

      - name: Boot the emulator
        run: |
          create-avd.sh --api ${{ matrix.api }} --if-missing
          serial="$(start-emulator.sh --wait-for-home tv_api${{ matrix.api }} -no-snapshot-save)"
          echo "ANDROID_SERIAL=$serial" >> "$GITHUB_ENV"
      - run: ./gradlew connectedDebugAndroidTest

      - name: Show the emulator's log
        if: failure()
        run: cat "/tmp/emulator-tv_api${{ matrix.api }}.log" || true
```

Your project's own build setup (JDK, Gradle caching) goes before the Gradle step as usual.

Why the steps look like this:

- **Pin a release** (`ref`), never `main`: see [Versions](README.md#versions).
- **`serial="$(…)"` on a line of its own.** As an assignment by itself, a failed boot fails the
  step; inside `echo "…$(start-emulator.sh)" >> …` the step would carry on with an empty serial.
- **`ANDROID_SERIAL`** makes adb and Gradle use that emulator in every later step.
- **`--wait-for-home`** (from v1.5.0 on): `start-emulator.sh` returns once Android has booted
  (`sys.boot_completed`), which on a runner comes before the device has settled: the home app can
  still be starting, and no window may have the focus yet, so the tests' first keys would reach no
  app. With `--wait-for-home` it also waits until the home app's screen is in front, has finished
  starting and has the focus, presses Back for a screen that stays in front of it (on API 23 and
  29, a new AVD's first boot opens "USB drive connected"), and fails, naming what was in front, if
  the home app doesn't settle within `ADT_HOME_TIMEOUT` seconds
  ([`bin/README.md`](bin/README.md#start-emulatorsh)).
- **Freeing disk space.** On its first boot the emulator creates the AVD's data partition, and
  it needs 7.2 GB free for that. Once the system image is installed, the runner's disk has about
  that or less: with API 26 and 27 (3.2 GB images) it was 65 MB short, with API 36 (8.2 GB)
  4.6 GB short, and the emulator stopped with "Not enough space to create userdata partition".
  So the step runs for every level. It deletes toolchains the runner image preinstalls and an
  Android TV project doesn't use (.NET, Haskell, Boost, CodeQL, the NDK); drop a path from the
  list if your build needs it.
- **`sdkmanager --licenses` first.** The runner image has accepted only the licenses of the
  packages it installed itself, and the images of API 26 to 30 are under another one
  ([`SETUP.md`](SETUP.md#the-android-cli)). Without it, sdkmanager refuses to install them.
  `yes` complains when sdkmanager stops reading, hence its `2> /dev/null`.
- **sdkmanager's output in a file.** It's mostly progress bars, so the job log shows it only
  when the install fails, where it says why.
- **`-no-snapshot-save`**: the emulator saves a Quick Boot snapshot when it's stopped, which is
  slow and of no use on a runner that's thrown away. Boots are cold by default anyway.
- **Time limits.** A stuck boot fails after `ADT_BOOT_TIMEOUT` seconds, and a device that doesn't
  settle after `ADT_HOME_TIMEOUT` more ([`bin/README.md`](bin/README.md#start-emulatorsh)); set
  them in `env:`. `timeout-minutes` bounds the whole job, including the tests.
- **The log.** The emulator logs to `${TMPDIR:-/tmp}/emulator-<avd>.log`; the last step prints it
  when something failed.
- **One emulator per job.** A matrix keeps each job small. Several emulators in one job work too:
  boot each with its own `start-emulator.sh`, keep each serial, and pass it to adb with `-s`
  (Gradle's `connected…` tasks run on every booted emulator).
- **Stopping.** Not needed: the runner stops what's left when the job ends. To stop it earlier,
  e.g. before booting the next one in the same job: `stop-emulator.sh "$ANDROID_SERIAL"`, which
  returns once it has exited.

## Caching

The system image is the big download. To skip it on later runs, cache
`$ANDROID_HOME/system-images/android-<level>` (for example with `actions/cache`), keyed on the
level, and restore it before the `sdkmanager` step, which then finds it installed.

Caching the AVD (`~/.android/avd`) saves little, since `create-avd.sh` is quick, and brings the
previous run's data partition along: apps installed and settings changed by earlier tests. If you
cache it anyway, `create-avd.sh --if-missing` keeps the cached AVD
([`bin/README.md`](bin/README.md#create-avdsh)), and `start-emulator.sh … -wipe-data` starts it
from a clean data partition.
