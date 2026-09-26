# android-tv-wsl-dev-tools

> **Not an official Google or Microsoft product.** This is an independent project, not affiliated
> with, sponsored or endorsed by Google LLC or Microsoft Corporation. Android, Android TV and
> Google TV are trademarks of Google LLC; Windows and WSL are trademarks of Microsoft Corporation.
> They're named here only to say what the tools work with.

Command-line tools for developing Android TV apps without Android Studio, on Ubuntu under WSL2:
create and boot an Android TV emulator, drive it with a TV remote in the terminal, and work around
the emulator's input problems under WSLg. They work with any Gradle project, and find the SDK, AVDs
and emulators on their own.

## Documentation

- [`SETUP.md`](SETUP.md): installing the toolchain (JDK, Android SDK, emulator) on WSL2, and how
  much disk and memory it takes.
- [`bin/README.md`](bin/README.md): the scripts. What each one does, how they find your setup,
  controlling the TV, other Android versions and screen sizes, troubleshooting.
- [`CI.md`](CI.md): using the scripts in GitHub Actions.
- [`tests/README.md`](tests/README.md): the tests, for working on the scripts.
- [`AGENTS.md`](AGENTS.md): rules for changing this repository.

## Scope

**Where:** Ubuntu on WSL2 (Windows 11, or a recent Windows 10 build, with WSLg), tested on Ubuntu
24.04. Other Debian-based distributions, and Ubuntu outside WSL, may work but are untested; the
WSLg workarounds are skipped outside WSL. GitHub's Ubuntu runners are covered for headless use in
CI ([`CI.md`](CI.md)). macOS, native Windows and non-Debian distributions aren't supported.

**What:** Android TV apps, controlled with a D-pad remote and no touchscreen. The AVD settings,
the default AVD and the remote in the terminal are built around that. Phone, tablet, Wear OS and
Automotive apps are out of scope: they need touch input and other hardware settings that these
tools don't provide.

**Tested emulator images:**

- **Android TV x86: API 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 33, 34 and 36**, every one from
  API 22 on (there are none for 32 and 35). API 22 is the oldest the emulator can boot.
- **Google TV x86: API 30, 31, 33, 34 and 36**, every one there is. Google TV is Android TV with
  Google's home screen. Its x86_64 and 16 KB page size (`google-tv-ps16k`) variants are untested.

## Getting started

1. Clone this repository anywhere, for example next to your projects.
2. Follow [`SETUP.md`](SETUP.md), skipping the parts you already have (an Android Studio SDK or
   AVD works as is). It ends with booting the emulator from your project.
3. Call the scripts by their path from any folder (`../android-tv-wsl-dev-tools/bin/remote.sh`),
   or put them on your `PATH`.

### Optional: put the scripts on your `PATH`

To type `start-emulator.sh` instead of its path, either add `bin/` to your `PATH` (in `~/.bashrc`):

```bash
export PATH="$PATH:$HOME/android-tv-wsl-dev-tools/bin"
```

or symlink the scripts into a folder that's already on it:

```bash
ln -s ~/android-tv-wsl-dev-tools/bin/* ~/.local/bin/
```

Both work the same way: the scripts resolve symlinks to find the rest of this repository. `bin/`
holds only the commands, so nothing else lands on your `PATH`. Messages that suggest a command
(e.g. "Start one with: …") show its bare name when it's on your `PATH`, and otherwise the path
you called the script by, so they can be pasted as is.

## Versions

Releases are git tags named `vMAJOR.MINOR.PATCH` ([semantic versioning](https://semver.org)), with
release notes on GitHub. `main` can change at any time, so anything automated should pin a release
tag, or a full commit SHA.

The version covers the scripts' names, options and defaults as their `--help` shows them
(including the AVD `create-avd.sh` creates), where they look for the SDK and AVDs, the environment
variables they read, their exit statuses, and `start-emulator.sh`'s stdout (the serial alone). Breaking any of these is a new major version; new options or tested API levels are
a new minor one. Messages, [`lib/lib.sh`](lib/lib.sh) and [`tests/`](tests/README.md) can change in
any release.
