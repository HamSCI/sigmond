# v3.83: three fixes before the build

Michael started the v3.83 build on 2026-10-28 and ruled that three fixes land first:
row 11 (`smd update` skips a component's build), row 3 (probe the magnetometer's I2C
addresses at install) and row 4 (keep the uploader keys across a reflash). The rows
live in `tasks/release-v383.md`. This document says how each fix works. Calls marked
**(Claude's call)** came from me, not from Michael.

## 1. `smd update` runs a component's deploy.toml build (row 11)

**The fault.** The update planner (`lib/sigmond/update.py`) asks one question of each
pulled component: does its checkout hold an `install.sh`? With no `install.sh` the
planner pulls and stops, and tells the operator "a library consumed editably through
its consumers' [tool.uv.sources], so the pull is the whole update". station-web and
hamsci-physics have no `install.sh`. Each builds its own venv through deploy.toml
`[build] steps`, which `smd install` runs (`installer.run_deploy_build_steps`) and
`smd update` never does. station-web gained httpx and websockets on 2026-10-28, so a
v3.82 station that updates to v3.83 would restart station-web into a crash loop.

**The fix** (Michael's ruling: the general fix).
- `cmd_update` records a second fact per component: whether its deploy.toml declares
  `[build] steps`.
- The planner emits a `build` action for a component with no `install.sh` and a
  `[build]`. The action runs after the pull and after any `install` actions. A
  component with a `[build]` counts as having an installer when the planner judges
  skew.
- The executor runs the steps as root, as `smd install` does. Then it applies the
  deploy.toml link steps, as `smd install` does. A failed step counts as a failed
  install. The executor skips the build when the component's pull failed.
- The build in an update ignores `produces`. `smd install` skips a build when every
  `produces` artefact exists. That check guards a first install. An update needs the
  build precisely when the venv already exists with old dependencies.
  `run_deploy_build_steps` gains a `force` argument.
- With neither `install.sh` nor `[build]`, the dry run says what it found: "no
  install.sh and no deploy.toml [build], so the pull is the whole update". It no
  longer claims "a library consumed editably".
- `smd update` restarts nothing today; it prints a restart hint. The build path keeps
  that rule. **(Claude's call:** a restart policy for update belongs in its own
  change.) A process already running keeps its loaded modules, so station-web stays up
  until its next restart, and then it starts on the rebuilt venv.

**Proof.** Planner tests: a no-`install.sh` component with `[build]` plans
pull → build; one with neither plans pull only, with the new wording. Executor test:
on the `_Rig` harness, an update of a component with `[build]` runs the step; with a
failed pull, it does not. Mutation: drop the `force` and watch a test fail.

## 2. Probe the magnetometer's I2C addresses at every install (row 3)

**The fault.** The address comes from mag-recorder's template
(`i2c_address = 0x23`) and from code defaults. The temperature address sits commented
out. `install.sh` copies the template only when no config exists. B4's RM3100 answers
at 0x20 and its MCP9808 at 0x19, so the v3.82 reflash logged all-zero samples. Michael
ruled on 2026-10-26: install probes for the addresses on every install, a reflash
included, and writes what answers. Nothing gets hard-coded or carried over. The
recorder also refuses to store all-zero readings.

**The fix**, in mag-recorder.
- A new verb, `mag-recorder probe-i2c [--config PATH]`, runs `mag-usb -S` (timeout
  10 s, stdout and stderr both read). It picks the one answering address in
  0x20–0x23 for the magnetometer and the one in 0x18–0x1F for the temperature sensor,
  then writes `i2c_address` and `remote_temp_address` into the config.
- The probe never blocks an install (Michael's earlier rule: mag never blocks). Without
  `/dev/ttyMAG0`, it exits 0 and says so. If `mag-usb` fails, it exits 0 and says so.
  With no answer in a range, or more than one, it leaves that key alone and prints a
  WARN that names what answered.
- `install.sh` runs the probe on every run, after it installs the config and the udev
  rule. mag-usb can freeze on an adapter that a running recorder holds. So when
  mag-recorder.service is active, `install.sh` stops it, probes, and starts it again.
- The template keeps 0x23 as the fallback the recorder uses when no probe has
  answered. **(Claude's call:** removing the default would stop a station with an
  unprobed but working 0x23 board.) The comment beside it says the probe overwrites it.
- The recorder drops any sample with x, y and z all exactly zero. It logs once when
  zeros start and once when they stop, with the count dropped, never once per sample.
  The temperature value plays no part in the test.
- sigmond's bring-up check (`hardware.rm3100_responds`) reads the configured address
  instead of its hard-coded 0x23, falling back to 0x23 when the config is unreadable.

**Proof.** Parser tests on the captured scan transcript in
`mag-usb/assets/mag-usb_output.txt`, plus no devices, two in one range, and a scan
error. A config-write test. A supervisor test that a zero sample never reaches the
spool and that the log fires on the transitions only. A sigmond test that the gate
check passes the configured address. On B4 after the build: a reflash writes 0x20 and
0x19 with no hand edit.

## 3. Keep the uploader keys across a reflash (row 4)

**What exists.** The install wizard already restores `site-keys.tar.gz` from the
stick's small EFI volume, after it renders the config, and then runs
`smd psws verify` when the operator gave a PSWS id. INSTALL.md §4, install-page.html
§7 and QUICKSTART.txt carry a tar one-liner to make that file on the old station. B4's
10-26 reflash had no file on the stick. The keys went back by hand, and nothing ran
`smd psws verify`, so PSWS refused uploads for a day unnoticed.

**The fix.**
- Two sigmond verbs, both root through smd's own elevation:
  - `smd psws save-keys [--output FILE]` writes `site-keys.tar.gz`, the same files as
    the one-liner (`/etc/hs-uploader/keys`, `/home/timestd/.ssh`), mode 600. The
    default output goes into the operator's home directory.
  - `smd psws restore-keys FILE` extracts only those two paths, sets owner and mode as
    the wizard does, refuses symlinks and paths outside the two trees, then runs
    `smd psws verify` and exits with its code.
- Docs name the reflash case. INSTALL.md §4, install-page.html §7 and QUICKSTART.txt
  become "Reflashing or replacing a station". Before the flash: `smd psws save-keys`,
  copy the file off the station, and put it on the stick's EFI volume after writing
  the stick. If the file missed the stick: `smd psws restore-keys FILE`. The section
  ends with `smd psws verify`, which must print verified.
- **(Claude's call:** the age-encrypted station-identity store in ops stays as it is.
  Its restore verb remains the planned "Plan 2". The keys travel in the format the
  wizard already reads.)

**Proof.** Round-trip test: save then restore in a scratch root gives the same bytes,
owners and modes. Restore refuses a tarball with `../`, a symlink, or a path outside
the two trees. Restore returns verify's exit code. The docs carry the same three
commands in all three files (a test greps them).

## What this change does not touch

T6 rows 7 and 8 (moved to v3.84), Rob's two open findings (`smd check` blind to failed
units on the host; `smd update` not resumable), and any automatic restart in
`smd update`.
