# The guest agent's memory cap, and an alarm for a lost RX888 (v3.71) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A request through the guest agent can no longer take the agent down with it, no `smd` the wizard or the
update rig sends runs under the agent's 128 MB cap, and a station that loses its RX888 (a stream stall, a USB
removal, a power cut) says so on `smd doctor` and on the fleet board for 24 h.

**Architecture:** Change A adds `OOMPolicy=continue` to the agent's drop-in, lets `smd doctor --fix` keep that
drop-in current the way it keeps radiod's, and moves the wizard's five `smd` requests and the rig's
`smd update --apply` into transient units (`systemd-run --wait --pipe`). Change B adds one small library,
`lib/sigmond/sdr_events.py`, that keeps one line per loss episode in `/var/lib/sigmond/sdr-events.jsonl`; four
callers feed it (a udev rule, `sigmond-sdr-recover`, a radiod `ExecStopPost=`, `sigmond-radiod-watchdog`), and
one new doctor family reads the last 24 h. A loss that recovered within 30 minutes reports, reads INCONCLUSIVE on
the heartbeat, and leaves doctor's exit code at 0. A loss that stayed open longer reads INVALID.

**Tech Stack:** Python 3.11, standard library only; bash; systemd and udev unit files; pytest (sigmond); hermetic
bash tests (sigmond-appliance).

**Spec:** `/home/mjh/hamsci/ops/scratch-investigations/v371-memcap-rx888-20261011.md` (research of 2026-10-11),
plus Michael's decisions of 2026-10-11, copied into Global Constraints below. Where the research and those
decisions disagree, the decisions win. Where this plan departs from the research, it says so in the task.

**Status:** written 2026-10-11 against sigmond `16f47bc` and sigmond-appliance `957096d`. Every file:line the
research cites was checked against that tree; the ones that moved are named in "Verified anchors" below.
Amended the same day, after Michael answered the plan's four open questions (see "Decided by Michael"): the
30-minute rule entered Task 4 and Task 8, and a new Task 7 makes the radiod watchdog a fourth writer. The old
Task 7 (doctor) became Task 8, and the old Task 8 (words) became Task 9. The amendment re-checked its anchors
against sigmond `2a24e4d` and sigmond-appliance `f134582`.

## How this plan carries its code

Unlike its sibling `tasks/plan-guest-agent-requests-v371.md`, **no line of code in this plan has run.** The
planner read every file it edits and wrote each hunk against the text it read, but built no scratch copy and ran
no test. One exception came with the amendment of 2026-10-11: Task 4's module and tests, and Task 7's watchdog
hunks and tests, ran in a scratch copy (54 passed), and mutations 13, 14 and 16 of Task 4 and 2 and 6 of Task 7
turned the named tests red. Nothing else ran. So treat each test's first red run as a real check: if a test fails for a reason the step does not name,
stop and read before you change anything. If an anchor quoted in a step no longer matches the tree, find where it
went, make the same edit by hand, and say so in your report.

## Runs after the guest-agent plan; files both plans touch

This plan runs only after `tasks/plan-guest-agent-requests-v371.md` has finished all five tasks. Before each task,
re-read the anchors in the files below, since that plan edits them too:

| File | Repository | That plan | This plan |
|---|---|---|---|
| `scripts/proxmox/sigmond-wizard.sh` | sigmond | Task 2 (committed `beeb5fa`): `qga_b64`, `QGA_RUN`, `qga_reset`, base64 senders | Task 2 adds `gexec_unit` beside `gexec` and edits five call sites |
| `tests/test_wizard_guest_senders.py` | sigmond | Task 2 created it | Task 2 imports its harness and must keep its count of `qm guest exec` at 8 |
| `docs/operator/troubleshooting.md` | sigmond | Task 5 (words) | Task 9 adds a block to the RX888 section and one triage row |
| `tasks/plan-sink-control-v370.md` | sigmond | Task 5 edits the carry list | Task 9 marks carry items 1 and 3 done |
| `test-update-v3.sh` | sigmond-appliance | none named; its Task 4 edits `test-nested-v3.sh` | Task 3 edits line ~876 and the comment above ~1453 |

This plan never edits `test-nested-v3.sh`, `firstboot-v3.sh`, `pm-heartbeat.py` or `pm-align.py`.

## Verified anchors (2026-10-11, against the commits above)

- `systemd/qemu-guest-agent-selfheal.conf:1-19`, `install.sh:1144-1158` (the agent's drop-in): as cited.
- `bin/smd`: `collect_findings` at 7296, `cmd_doctor` at 7665, its `--fix` call of `_ensure_radiod_dropins` at
  7722, `_RADIOD_DROPINS` at 10713, `_ensure_radiod_dropins` at 10783, `radiod-dropins-missing` at 10876 (the
  research said 10864), the native install's call at 9684.
- `lib/sigmond/heartbeat.py:466-501` (`_map_doctor`); `lib/sigmond/heartbeat_schema.py:26-78`: as cited.
- `bin/sigmond-sdr-recover`: grace 65, cooldown 70, state path 73, `device_present` 332-334, the two absence lines
  729 and 733, "holding" 756-757, `last_cycle_at` 765: as cited.
- `scripts/proxmox/sigmond-wizard.sh`: at `16f47bc` the five `smd` requests sat at 2666, 2667, 2686, 2707, 3567,
  as cited. Commit `7580f49` moved them down seven lines: at `2a24e4d` they sit at 2673, 2674, 2693, 2714, 3574,
  with `gexec` at 667 and `guest_out` at 707. Task 2 quotes the text of each line, so the edits still match.
- `bin/sigmond-radiod-watchdog` (at `2a24e4d`): `DRY_RUN` 57, `STATE_DIR` 59, `_mark_restarted` 119-121,
  `_restart` 160-191 (its dry-run return 163-164, `_mark_restarted(unit, now)` 165, the stop 177-185, the start
  191), `check_unit` 194-231 (warm-up return 200-201, the cooldown line 202-204, detector 1 at 222-226,
  detector 2 at 229-231), `_honor_restart_request` 237-274 (its own `lib` path at 249-251), `main` 277-291. The
  service runs as root (`systemd/sigmond-radiod-watchdog.service` sets no `User=`); its timer fires every 2 min
  (`systemd/sigmond-radiod-watchdog.timer:6`). `smd`'s `_install_radiod_watchdog` (`bin/smd:2805`) links
  `/usr/local/bin/sigmond-radiod-watchdog` into the repo, so a `git` update reaches it.
- `tests/test_radiod_watchdog.py` (at `2a24e4d`): one class, `RestartAlwaysStarts`, `setUp` 45-54, `tearDown`
  56-60.
- `systemd/radiod-restart-forever.conf` sets `StartLimitIntervalSec=0` and `RestartSec=10`: radiod keeps
  restarting every 10 s for as long as its card stays away, so an episode keeps growing while the loss lasts.
- `test-update-v3.sh`: `e3-update-apply` at 876, `g3-restore-apply` at 1453 with its comment at 1436-1452.
- **Research gap:** radiod runs as `User=radio` (`ka9q-radio/service/radiod@.service.in`), which cannot write
  `/var/lib/sigmond`. Task 5 therefore gives the `ExecStopPost=` line the `+` prefix (full privileges).
- **Research gap:** one RX888 vanish produces three loss events of different kinds within a minute (udev
  removal, radiod's failing restarts, sdr-recover's power cut). With the research's "same kind" merge rule, one
  vanish would count as three episodes and read INVALID at once. Task 4 merges a loss of **any** kind into the
  open episode.

## Global Constraints

- **Keep `MemoryMax=128M`.** Add `OOMPolicy=continue` to the same drop-in. Set nothing else in it.
- **All five wizard `smd` requests run in their own transient unit**, through `systemd-run --wait --pipe`:
  `profile-install --help`, `profile-install`, `admin personalize --reset-identity --yes`, `config render`,
  `psws verify`. So does the update rig's `smd update --apply`.
- **The agent's drop-in reaches stations in the field by `smd doctor --fix` and the install path**, the route
  radiod's drop-ins already use. pm-align never touches the VM's configuration and cannot carry it.
- **A stream stall with the card still on the bus counts as losing the RX888**, as do a USB removal and a power
  cut by `sigmond-sdr-recover`.
- **One writer, one line per episode**, in `/var/lib/sigmond/sdr-events.jsonl`. Four callers: a udev
  remove/add rule, `sigmond-sdr-recover`, radiod's `ExecStopPost=` on an exit nobody asked for, and
  `sigmond-radiod-watchdog` when it restarts a wedged radiod (a USB-stall zombie, or a silent one). Each event
  merges into the open episode, whoever wrote it.
- **The finding family reads the last 24 h:** `sdr-lost` (card off the bus now) INVALID; `sdr-down-long` (an
  episode that stayed open longer than 30 minutes) INVALID; three or more episodes in 24 h INVALID, under the kind
  `sdr-flapping`; otherwise `sdr-recovered`, INCONCLUSIVE.
- **`sdr-recovered` reports but does not change `smd doctor`'s exit code.** Every other finding still exits 1.
  The update rig parses doctor's kinds from lines of the form `"    <kind>: <detail>"`; that shape stays.
- **The heartbeat's wire contract stays as it stands**: `KIND`, `SCHEMA_VERSION`, `VERDICTS`, `PRECEDENCE`, the
  block names. A finding's kind travels as free text inside the doctor block's reason.
- **No log line per tick for a condition that persists.** `sigmond-sdr-recover` says each of its three
  absent-card lines once per change. The watchdog's hook adds no line when it records; it speaks once, and only
  when the record fails.
- **No byte above 0x7F may cross to the guest agent**, in any request these tasks add or touch.
- **Two repositories, both on `main`.** Tasks 1, 2 and 4-9 commit in `/home/mjh/hamsci/repos/sigmond`; Task 3
  in `/home/mjh/hamsci/repos/sigmond-appliance`. Commit by path. Never `git add -A`. Never push. End each commit
  message with the attribution lines your own session's instructions give.
- **Tests:** in sigmond, `.venv/bin/pytest` (never a bare `pytest`). The appliance's tests take the file under
  test as their first argument. Try every mutation on a COPY of the file, never in the checkout: copy the repo
  file to `/tmp`, edit the copy, and point the test at it (the appliance tests by argument; the sigmond tests by
  temporarily copying the mutated file over the original inside a throwaway `git worktree`, then removing it).
- **Reach nothing outside this machine.** No station, no rig. Run no `smd`, `sigmond-setup`, `pm-align` or
  `systemctl` against this machine.
- **Prose** (comments, test names, documents, commit messages): short sentences, active voice that names who
  acts, few forms of "to be", times in UTC. A private style document exists on this machine; do not open it, quote
  it or point to it from any file.
- After the last commit of a task that changed code: `graphify update /home/mjh/hamsci/repos`. Never
  `graphify update .`.

## Review Focus

What the spec implies and a plain reading of the tasks could miss, most likely first. Each line names the test
that pins it.

1. **One vanish, three kinds of event.** A card that leaves the bus brings a udev removal, radiod exits every
   10 s while it stays gone, and sdr-recover cuts power after 30 s. All of that must make **one** episode, or a
   single vanish reads `sdr-flapping`. Task 4:
   `test_one_vanish_is_one_episode_whatever_its_kinds`.
2. **A stop someone asked for.** `smd apply`, sdr-recover and the radiod watchdog all restart radiod; radiod
   answers SIGTERM with `EX_OK` (`ka9q-radio/src/main.c:200`), and systemd then sets `SERVICE_RESULT=success`. A
   slow stop that systemd ends by SIGKILL sets `timeout`. Neither may count as a loss through radiod's own hook.
   Task 5: `test_an_exit_someone_asked_for_writes_nothing`, `test_a_slow_stop_writes_nothing`. The watchdog's
   restart still marks a stall, so the watchdog writes its own event (Task 7), and radiod's failing starts after
   it join the same episode: `test_a_restart_and_radiods_failing_starts_make_one_episode`.
3. **A host with no RX888 at all** (the nested rig, any VM without a card). No file means no finding, so the
   update rig's "no new doctor kinds" check stays quiet. Task 8: `test_no_file_no_finding`. The rig itself must
   still confirm that no radiod instance runs and dies in the nest (rig check R5).
4. **A record that some other program damaged, or a hostile detail.** The reader must skip bad lines, and the
   text that reaches the fleet board must stay printable ASCII of bounded length. Task 4:
   `test_damaged_and_hostile_lines_are_skipped`, `test_detail_is_printable_ascii_and_bounded`; Task 8:
   `test_an_sdr_detail_reaches_the_reason_printable_and_bounded`.
5. **A wizard step whose request times out while its unit keeps running.** `gexec` retries once after an agent
   failure; the retry then meets a unit of the same name still running, and `systemd-run` refuses it. The step
   fails loudly instead of running a second `smd` beside the first. No hermetic test can stage that; rig check
   R6 covers it. Task 2 pins the fixed unit name: `test_the_command_runs_in_a_transient_unit_that_waits_and_pipes`.
6. **The 30-minute edge, and where an episode ends.** An episode closes at the card's return when one came, and
   at its last event otherwise. Exactly 30 minutes stays `sdr-recovered`; one second more reads `sdr-down-long`.
   A return long after the last radiod exit still counts, and a long episode early in the day keeps the verdict
   for 24 h even when a short one followed it. Task 4: `test_exactly_30_minutes_stays_recovered`,
   `test_a_return_after_30_minutes_counts_as_long`, `test_a_long_episode_earlier_in_the_day_still_reads_down_long`.
7. **A watchdog restart that records in the wrong place.** The hook belongs after the dry-run return and before
   the stop, inside `_restart`, which only the two wedge detectors call. Placed in `check_unit` before the
   cooldown, it would write every 2 min; placed before the dry-run return, a dry run would write. Task 7:
   `test_a_unit_in_cooldown_records_nothing`, `test_a_dry_run_records_nothing`, `test_the_event_lands_before_the_stop`.

---

## File structure

| File | Repository | Task | What changes |
|---|---|---|---|
| `systemd/qemu-guest-agent-selfheal.conf` | sigmond | 1 | Adds `OOMPolicy=continue`; header rewritten |
| `bin/smd` | sigmond | 1, 5, 8 | 1: `pairs`/`gate`/`label` on the radiod drop-in helpers; `_QGA_DROPIN` and its ensure/finding pair; the `qga-dropin` family; `--fix` calls it. 5: one pair in `_RADIOD_DROPINS`. 8: `_sdr_findings`, the `sdr-events` family, the exit code |
| `install.sh` | sigmond | 1, 5 | 1: the agent block's message. 5: the writer's symlink, the udev rule, the radiod drop-in |
| `tests/test_qga_dropin.py` (new) | sigmond | 1 | The shipped drop-in, ensure, finding, `--fix`, the family |
| `scripts/proxmox/sigmond-wizard.sh` | sigmond | 2 | `QGA_SDRUN`, `gexec_unit`; five call sites |
| `tests/test_wizard_smd_units.py` (new) | sigmond | 2 | `gexec_unit` against a stub `qm` and a stand-in `systemd-run`; the scan |
| `test-update-v3.sh` | sigmond-appliance | 3 | `e3-update-apply` runs in `rig-update-apply`; two comments |
| `test-update-agent-cap.sh` (new) | sigmond-appliance | 3 | Scans the rig's heavy requests |
| `lib/sigmond/sdr_events.py` (new) | sigmond | 4 | The episode record: `record`, `read_episodes`, `card_present`, `episode_span`, `assess`, `radiod_exit_is_loss`; the loss kind `radiod-wedge`; `LONG_EPISODE_S` |
| `tests/test_sdr_events.py` (new) | sigmond | 4 | The record's rules, the 30-minute rule |
| `bin/sigmond-sdr-event` (new) | sigmond | 5 | The command-line writer for udev and radiod |
| `udev/91-sigmond-sdr-event.rules` (new) | sigmond | 5 | Remove and add of 04b4:00f1 |
| `systemd/radiod-sdr-event.conf` (new) | sigmond | 5 | `ExecStopPost=-+/usr/local/sbin/sigmond-sdr-event radiod-exit %i` |
| `tests/test_sdr_event_hooks.py` (new) | sigmond | 5 | The writer, the rule, the drop-in, install.sh |
| `tests/test_radiod_dropins.py` | sigmond | 5 | The fixture lists gain the new pair |
| `bin/sigmond-sdr-recover` | sigmond | 6 | `note_event`, `log_on_change`; one call before the cut; three log lines quieted |
| `tests/test_sdr_recover.py` | sigmond | 6 | One class driving `main()` |
| `bin/sigmond-radiod-watchdog` | sigmond | 7 | `SDR_EVENTS_PATH`, `_note_event`; one call in `_restart` |
| `tests/test_radiod_watchdog.py` | sigmond | 7 | The old class stubs `_note_event`; one class appended |
| `lib/sigmond/doctor.py` | sigmond | 8 | `SOFT_FINDING_KINDS`, `hard_findings` |
| `lib/sigmond/heartbeat.py` | sigmond | 8 | `_map_doctor`: INCONCLUSIVE for soft kinds alone; the sdr detail in the reason |
| `tests/test_sdr_doctor.py` (new) | sigmond | 8 | The family and the exit code |
| `tests/test_heartbeat.py` | sigmond | 8 | Six tests appended |
| `docs/operator/troubleshooting.md` | sigmond | 9 | One triage row; one block in the RX888 section |
| `tasks/plan-sink-control-v370.md` | sigmond | 9 | Carry items 1 and 3 marked done |

---

### Task 1: the guest agent's drop-in keeps its cap, survives a killed child, and doctor keeps it current

**Files:**
- Modify: `/home/mjh/hamsci/repos/sigmond/systemd/qemu-guest-agent-selfheal.conf` (whole file)
- Modify: `/home/mjh/hamsci/repos/sigmond/bin/smd` (`_radiod_dropins_missing` ~10744, `_ensure_radiod_dropins`
  ~10783, after `_radiod_dropins_findings` ~10880; inside `collect_findings` ~7640-7656; `cmd_doctor` ~7718-7723)
- Modify: `/home/mjh/hamsci/repos/sigmond/install.sh:1144-1158` (comment and `ok` line only)
- Create: `/home/mjh/hamsci/repos/sigmond/tests/test_qga_dropin.py`

**Interfaces:**
- Consumes: nothing from another task.
- Produces: in `bin/smd`, `_QGA_DROPIN` (a `(src_name, rel_dest)` pair), `_qga_unit_installed(run) -> bool`,
  `_ensure_qga_dropin(*, systemd_dir, src_dir, run) -> list[str]`,
  `_qga_dropin_findings(*, systemd_dir, src_dir, run) -> list[Finding]` (kind `qga-dropin-stale`, fixable). The
  radiod helpers `_radiod_dropins_missing` and `_ensure_radiod_dropins` gain keyword-only `pairs=None`,
  `gate=None` (and `label=` on ensure); left out, they behave as today. Task 5 relies on `_RADIOD_DROPINS` staying
  the default `pairs`.

**What you need to know:**
- Debian's `qemu-guest-agent.service` sets `Restart=always` and no memory limit. sigmond's drop-in adds
  `MemoryMax=128M`. Under systemd's default `OOMPolicy=stop`, when the kernel's cgroup OOM killer kills any
  process in the unit, systemd stops the whole unit; the agent then restarts under whoever called it. That
  happened on the update rig at 2026-10-08 23:46:26Z. With `continue`, systemd leaves the unit alone after a kill:
  the kernel takes the largest process (a fat child), the child's request fails, and the agent keeps serving. When
  qemu-ga itself holds the runaway buffer, the kernel kills qemu-ga, its main process dies, and `Restart=always`
  brings it back.
- `smd update` re-runs `install.sh` only when `install.sh`, `pyproject.toml` or `uv.lock` changed between the two
  commits (`lib/sigmond/align_apply.py`). A change to the `.conf` alone would never reach a station. So `smd
  doctor` reports a stale copy and `--fix` rewrites it, through the same machinery as radiod's drop-ins.
- Neither ensure function ever restarts the unit it serves. A restart of the agent kills the request it answers.
  `systemctl daemon-reload` applies both settings to the running agent (INFERRED; rig check R1).

- [ ] **Step 1: Write the failing tests.** Create `tests/test_qga_dropin.py`:

```python
"""The guest agent's drop-in (v3.71): the 128 MB cap stays, a killed child no
longer takes the agent with it, and `smd doctor` keeps the file current.

The cap covers every process a `qm guest exec` starts.  Under systemd's
default OOMPolicy=stop, a child that outgrew it made systemd stop the whole
unit, and the agent restarted under its caller (the update rig, 2026-10-08
23:46:26Z).  OOMPolicy=continue lets the kernel kill the child alone.

install.sh writes the drop-in only when install.sh runs, and `smd update`
re-runs install.sh only when install.sh, pyproject.toml or uv.lock changed.
So doctor reports a stale copy and --fix rewrites it, as for radiod's.
"""
import importlib.machinery
import importlib.util
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
CONF = REPO / "systemd" / "qemu-guest-agent-selfheal.conf"
REL = "qemu-guest-agent.service.d/50-sigmond-selfheal.conf"


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_qga_dropin", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_qga_dropin", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def service_settings(text):
    """KEY=VALUE lines under [Service], comments and blanks dropped."""
    out, section = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line
            continue
        if section == "[Service]":
            out.append(line)
    return out


def fake_run(calls, agent_installed=True):
    """Stands in for smd's _run: answers `systemctl cat` and `daemon-reload`,
    and performs `install` without the chown.  Anything else fails the test."""
    def run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[:2] == ["systemctl", "cat"]:
            return types.SimpleNamespace(returncode=0 if agent_installed else 1,
                                         stdout="", stderr="")
        if cmd[:2] == ["systemctl", "daemon-reload"]:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd and cmd[0] == "install":
            if "-d" in cmd:
                Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
            else:
                Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command {cmd}")
    return run


class ShippedDropinTests(unittest.TestCase):
    def test_the_cap_stays_at_128M(self):
        self.assertIn("MemoryMax=128M", service_settings(CONF.read_text()))

    def test_a_killed_child_leaves_the_agent_running(self):
        self.assertIn("OOMPolicy=continue", service_settings(CONF.read_text()))

    def test_it_sets_nothing_else(self):
        self.assertEqual(sorted(service_settings(CONF.read_text())),
                         ["MemoryMax=128M", "OOMPolicy=continue"])

    def test_the_file_holds_ascii_only(self):
        CONF.read_bytes().decode("ascii")

    def test_doctor_writes_it_where_install_sh_does(self):
        self.assertEqual(smd._QGA_DROPIN, ("qemu-guest-agent-selfheal.conf", REL))
        self.assertIn("/etc/systemd/system/" + REL, (REPO / "install.sh").read_text())


class EnsureAndFindingTests(unittest.TestCase):
    def setUp(self):
        self.tdir = Path(tempfile.mkdtemp())
        self.src = self.tdir / "src"
        self.src.mkdir()
        (self.src / "qemu-guest-agent-selfheal.conf").write_bytes(
            b"[Service]\nMemoryMax=128M\nOOMPolicy=continue\n")
        self.etc = self.tdir / "etc"
        self.etc.mkdir()

    def kw(self, calls, **k):
        return dict(systemd_dir=self.etc, src_dir=self.src, run=fake_run(calls, **k))

    def test_no_agent_unit_writes_nothing_and_finds_nothing(self):
        calls = []
        self.assertEqual(smd._ensure_qga_dropin(**self.kw(calls, agent_installed=False)), [])
        self.assertEqual(smd._qga_dropin_findings(**self.kw(calls, agent_installed=False)), [])
        self.assertEqual(list(self.etc.rglob("*")), [])

    def test_the_gate_asks_about_the_agent_unit(self):
        calls = []
        smd._qga_dropin_findings(**self.kw(calls))
        self.assertIn(["systemctl", "cat", "qemu-guest-agent.service"], calls)

    def test_a_missing_dropin_gets_written_then_one_reload(self):
        calls = []
        written = smd._ensure_qga_dropin(**self.kw(calls))
        self.assertEqual(written, [str(self.etc / REL)])
        self.assertEqual((self.etc / REL).read_bytes(),
                         (self.src / "qemu-guest-agent-selfheal.conf").read_bytes())
        self.assertEqual(sum(c[:2] == ["systemctl", "daemon-reload"] for c in calls), 1)

    def test_a_current_dropin_writes_nothing_and_reloads_nothing(self):
        smd._ensure_qga_dropin(**self.kw([]))
        calls = []
        self.assertEqual(smd._ensure_qga_dropin(**self.kw(calls)), [])
        self.assertFalse(any(c[:2] == ["systemctl", "daemon-reload"] for c in calls))

    def test_ensure_never_restarts_or_stops_the_agent(self):
        calls = []
        smd._ensure_qga_dropin(**self.kw(calls))
        for c in calls:
            self.assertNotIn("restart", c)
            self.assertNotIn("stop", c)

    def test_a_stale_dropin_makes_one_fixable_finding_naming_its_path(self):
        (self.etc / REL).parent.mkdir(parents=True)
        (self.etc / REL).write_bytes(b"[Service]\nMemoryMax=128M\n")
        (f,) = smd._qga_dropin_findings(**self.kw([]))
        self.assertEqual(f.kind, "qga-dropin-stale")
        self.assertTrue(f.fixable)
        self.assertIn(str(self.etc / REL), f.detail)
        self.assertIn("smd doctor --fix", f.detail)

    def test_a_current_dropin_makes_no_finding(self):
        smd._ensure_qga_dropin(**self.kw([]))
        self.assertEqual(smd._qga_dropin_findings(**self.kw([])), [])


class DoctorWiringTests(unittest.TestCase):
    def test_fix_calls_ensure_qga_dropin(self):
        calls = []
        args = types.SimpleNamespace(base=tempfile.mkdtemp(), fix=True)
        with mock.patch.object(smd, "_ensure_radiod_dropins", return_value=[]), \
             mock.patch.object(smd, "_ensure_qga_dropin",
                               side_effect=lambda **kw: calls.append(True) or []), \
             mock.patch.object(smd, "collect_findings", return_value=(True, [])):
            smd.cmd_doctor(args)
        self.assertEqual(calls, [True])

    def test_collect_findings_runs_the_qga_family(self):
        from sigmond.doctor import Finding
        f = Finding("sigmond", "qga-dropin-stale", "x", fixable=True)
        with mock.patch.object(smd, "_qga_dropin_findings", return_value=[f]):
            _, findings = smd.collect_findings(base=tempfile.mkdtemp(), strict=False)
        self.assertIn(f, findings)
```

- [ ] **Step 2: Run the tests; watch them fail.**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_qga_dropin.py -q --override-ini addopts=`
Expected: FAIL. `test_the_cap_stays_at_128M` passes already. The rest fail: `OOMPolicy=continue` missing, and
`AttributeError: ... has no attribute '_QGA_DROPIN'` / `'_ensure_qga_dropin'` / `'_qga_dropin_findings'`.

- [ ] **Step 3: Rewrite the drop-in.** Replace the whole of `systemd/qemu-guest-agent-selfheal.conf` with:

```ini
# sigmond drop-in for qemu-guest-agent.service.  install.sh, and
# `smd doctor --fix`, write it to
# /etc/systemd/system/qemu-guest-agent.service.d/50-sigmond-selfheal.conf
# on a VM guest.
#
# Why the cap: on AC0G-B4, 2026-08-24, a `qm guest exec` that ran a large
# journalctl sweep left the agent wedged: process alive, channel dead,
# `qm agent ping` failing while systemd still showed it active.
# Restart=always never fired, because nothing died.  MemoryMax turns a
# runaway agent into a death that systemd sees, and Debian's
# Restart=always brings it back.  (qemu-ga bounds each captured stream on
# its own; whether the 08-24 wedge took memory at all stays an open
# question.)
#
# Why OOMPolicy=continue: the cap covers every process a request starts.
# Under systemd's default (stop), a request whose child outgrew the cap
# made systemd stop the whole unit, and the agent restarted under its
# caller (the update rig, 2026-10-08 23:46:26Z).  With continue, the
# kernel kills the largest process in the cgroup and systemd leaves the
# rest alone: a fat child fails its own request, and the agent keeps
# serving.  When the agent itself holds the runaway buffer, the kernel
# kills the agent, and Restart=always still brings it back.
#
# Heavy work does not belong under this cap.  The wizard runs each smd in
# a transient unit of its own (systemd-run --wait --pipe).
#
# pm-heartbeat's guest-exec check (guest-agent-wedged, INVALID on the
# fleet board) stays the alarm for anything this does not catch.
[Service]
MemoryMax=128M
OOMPolicy=continue
```

- [ ] **Step 4: Let the radiod drop-in helpers take another set.** In `bin/smd`, change the signature and the two
  lines that name `_RADIOD_DROPINS` and `_radiod_template_installed`, in both functions.

  In `_radiod_dropins_missing`, replace:

```python
def _radiod_dropins_missing(*, systemd_dir: Path = Path('/etc/systemd/system'),
                            src_dir: Path = None, run=None) -> list:
```

  with:

```python
def _radiod_dropins_missing(*, systemd_dir: Path = Path('/etc/systemd/system'),
                            src_dir: Path = None, run=None,
                            pairs=None, gate=None) -> list:
```

  and, in its body, replace:

```python
    if not _radiod_template_installed(run):
        return []

    missing = []
    for src_name, rel_dest in _RADIOD_DROPINS:
```

  with:

```python
    # pairs/gate default to radiod's set; the guest agent's drop-in passes
    # its own (_qga_dropin_findings).
    pairs = _RADIOD_DROPINS if pairs is None else pairs
    gate = gate or _radiod_template_installed
    if not gate(run):
        return []

    missing = []
    for src_name, rel_dest in pairs:
```

  In `_ensure_radiod_dropins`, replace:

```python
def _ensure_radiod_dropins(*, systemd_dir: Path = Path('/etc/systemd/system'),
                           src_dir: Path = None, run=None) -> list:
```

  with:

```python
def _ensure_radiod_dropins(*, systemd_dir: Path = Path('/etc/systemd/system'),
                           src_dir: Path = None, run=None,
                           pairs=None, gate=None,
                           label: str = 'radiod drop-ins') -> list:
```

  and, in its body, replace:

```python
    if not _radiod_template_installed(run):
        return []

    written = []
    for src_name, rel_dest in _RADIOD_DROPINS:
```

  with:

```python
    pairs = _RADIOD_DROPINS if pairs is None else pairs
    gate = gate or _radiod_template_installed
    if not gate(run):
        return []

    written = []
    for src_name, rel_dest in pairs:
```

  Then, in the same function, change each of the five `_err(f'radiod drop-ins: ...` messages to
  `_err(f'{label}: ...` (the text after the colon stays).

- [ ] **Step 5: Add the agent's pair, its ensure and its finding.** In `bin/smd`, directly after the end of
  `_radiod_dropins_findings` (the line `        fixable=True)]` that closes its `return`), insert:

```python


# The guest agent's drop-in, by the same machinery as radiod's.  install.sh
# writes it only when install.sh runs, and `smd update` re-runs install.sh
# only when install.sh, pyproject.toml or uv.lock changed
# (lib/sigmond/align_apply.py), so a change to the .conf alone would never
# reach a station.  pm-align cannot carry it either: it never touches the
# VM's configuration.  The gate: the agent's unit exists, which holds on VM
# guests only.
_QGA_DROPIN = ('qemu-guest-agent-selfheal.conf',
               'qemu-guest-agent.service.d/50-sigmond-selfheal.conf')


def _qga_unit_installed(run) -> bool:
    """True when qemu-guest-agent.service exists on this host."""
    return run(['systemctl', 'cat', 'qemu-guest-agent.service'],
               capture=True).returncode == 0


def _ensure_qga_dropin(*, systemd_dir: Path = Path('/etc/systemd/system'),
                       src_dir: Path = None, run=None) -> list:
    """Write the guest agent's drop-in when missing or stale, then
    daemon-reload once.  Never restarts the agent: a restart kills whatever
    request it serves, and daemon-reload applies the new settings to the
    running unit."""
    return _ensure_radiod_dropins(systemd_dir=systemd_dir, src_dir=src_dir,
                                  run=run, pairs=(_QGA_DROPIN,),
                                  gate=_qga_unit_installed,
                                  label='guest-agent drop-in')


def _qga_dropin_findings(*, systemd_dir: Path = Path('/etc/systemd/system'),
                         src_dir: Path = None, run=None) -> list:
    """`smd doctor` finding: the guest agent's drop-in missing or stale."""
    from sigmond.doctor import Finding
    missing = _radiod_dropins_missing(systemd_dir=systemd_dir, src_dir=src_dir,
                                      run=run, pairs=(_QGA_DROPIN,),
                                      gate=_qga_unit_installed)
    if not missing:
        return []
    return [Finding(
        'sigmond', 'qga-dropin-stale',
        f'{missing[0]} missing or stale: a request that outgrows the guest '
        f'agent\'s 128 MB cap may still stop the whole agent (smd doctor --fix)',
        fixable=True)]
```

- [ ] **Step 6: Run the family inside `collect_findings`, and fix it under `--fix`.** In `collect_findings`,
  after the `_radiod_dropins` inner function, add:

```python
    def _qga_dropin() -> None:
        # The guest agent's drop-in (VM guests only): its cap and its
        # OOMPolicy.  See _qga_dropin_findings / _ensure_qga_dropin.
        findings.extend(_qga_dropin_findings())
```

  and after `_guarded('radiod-dropins', _radiod_dropins)` add:

```python
    _guarded('qga-dropin', _qga_dropin)
```

  In the docstring's first sentence, change "the ten `smd doctor` check families" to "the eleven `smd doctor`
  check families", and change "and radiod's systemd drop-ins." to "radiod's systemd drop-ins, and the guest
  agent's drop-in."

  In `cmd_doctor`, after:

```python
        for p in _ensure_radiod_dropins():
            _ok(f'radiod drop-in: wrote {p}')
```

  add:

```python
        # The guest agent's drop-in: idempotent, never restarts the agent.
        for p in _ensure_qga_dropin():
            _ok(f'guest-agent drop-in: wrote {p}')
```

  In `cmd_doctor`'s docstring, change "Ownership and radiod's systemd drop-ins are the only classes --fix
  repairs" to "Ownership, radiod's systemd drop-ins and the guest agent's drop-in are the only classes --fix
  repairs", and "The ten check families" to "The eleven check families".

- [ ] **Step 7: Correct install.sh's words.** In `install.sh`, in the comment above the agent block, replace:

```bash
# invisible to Restart=always (nothing died).  MemoryMax converts that
# wedge into an OOM-kill + clean restart.  See the drop-in's header and
```

  with:

```bash
# invisible to Restart=always (nothing died).  MemoryMax converts that
# wedge into an OOM-kill + clean restart; OOMPolicy=continue keeps a killed
# child from taking the agent with it.  `smd doctor --fix` rewrites a stale
# copy on stations this script no longer reaches.  See the drop-in's header and
```

  and replace `ok "qemu-guest-agent drop-in installed (MemoryMax self-heal)"` with
  `ok "qemu-guest-agent drop-in installed (MemoryMax=128M, OOMPolicy=continue)"`.

- [ ] **Step 8: Run the tests; watch them pass, and the neighbours stay green.**

Run: `.venv/bin/pytest tests/test_qga_dropin.py tests/test_radiod_dropins.py tests/test_smd_doctor_glue.py -q --override-ini addopts=`
Expected: PASS, all three files.

- [ ] **Step 9: Watch each guard fail (mutations, on a copy).**
  1. Drop the `OOMPolicy=continue` line → `test_a_killed_child_leaves_the_agent_running` and
     `test_it_sets_nothing_else` turn red.
  2. Write `OOMPolicy=stop` instead → the same two turn red.
  3. In `_ensure_radiod_dropins`, change `if written:` to `if True:` before the reload →
     `test_a_current_dropin_writes_nothing_and_reloads_nothing` turns red.
  4. In `_qga_unit_installed`, return `True` always → `test_no_agent_unit_writes_nothing_and_finds_nothing` turns
     red.
  5. Remove `_guarded('qga-dropin', _qga_dropin)` → `test_collect_findings_runs_the_qga_family` turns red.
  6. Remove the `_ensure_qga_dropin()` loop from `cmd_doctor` → `test_fix_calls_ensure_qga_dropin` turns red.
  7. In `_ensure_radiod_dropins`, change `gate = gate or _radiod_template_installed` to
     `gate = _radiod_template_installed` → `test_the_gate_asks_about_the_agent_unit` turns red.

- [ ] **Step 10: Commit.**

```bash
cd /home/mjh/hamsci/repos/sigmond
git add systemd/qemu-guest-agent-selfheal.conf bin/smd install.sh tests/test_qga_dropin.py
git commit -m "agent: keep the 128 MB cap, let a killed child fail alone, and let doctor keep the drop-in current"
```

---

### Task 2: the wizard runs each smd in a transient unit of its own

**Files:**
- Modify: `/home/mjh/hamsci/repos/sigmond/scripts/proxmox/sigmond-wizard.sh` (after `gexec`, ~line 703 at `2a24e4d`;
  call sites ~2673, ~2674, ~2693, ~2714, ~3574)
- Create: `/home/mjh/hamsci/repos/sigmond/tests/test_wizard_smd_units.py`

**Interfaces:**
- Consumes: the wizard's `gexec`, `qga_b64` and `QGA_RUN` from the guest-agent plan's Task 2; the test harness
  `harness`, `bash`, `guest_execs`, `shell_func`, `shell_var`, `TEXT` from `tests/test_wizard_guest_senders.py`.
- Produces: `QGA_SDRUN="${SIGMOND_QGA_SDRUN:-systemd-run}"` and `gexec_unit <timeout-s> <name> <command...>`,
  which returns as `gexec` does (0 when the command exited 0, 1 otherwise).

**What you need to know:**
- The agent's cap covers every process a request starts. One smd needs about 55 MB, and the agent idles near
  10 MB, so the margin under 128 MB fits one smd and not two (`tasks/plan-sink-control-v370.md`, the carry list).
- `systemd-run --wait` returns the command's exit status; `--pipe` hands the request's own output streams to the
  unit, so the output still comes back through `qm guest exec`. The update rig's `g3-restore-apply` already runs
  this way (sigmond-appliance `a0d7268`).
- `gexec` base64-encodes what it sends. `gexec_unit` encodes the inner command a second time and hands it to the
  unit's `bash` as an argument, so no quote in the command can break the outer line.
- The test `test_every_direct_request_in_the_wizard_is_ascii_and_counted` counts the words `qm guest exec` in the
  wizard and expects 8. Do not write those three words in `gexec_unit`'s comment.
- The wizard reaches stations in the field through pm-align, which installs it on the Proxmox host from the
  release (`pm-align.py:155-177`).
- `QGA_SDRUN` exists only so the tests can stand in a recording `systemd-run`, the same way `SIGMOND_QGA_DIR`
  serves the reset's tests.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_wizard_smd_units.py`:

```python
"""Every smd the wizard sends runs in a transient unit of its own (v3.71).

sigmond caps qemu-guest-agent.service at MemoryMax=128M, and the cap covers
every process a request starts.  One smd needs about 55 MB; two pass the cap.
So the wizard's five smd requests go through gexec_unit, which wraps each in
`systemd-run --wait --pipe`: the command runs outside the agent's cgroup, its
output still comes back through the request, and its exit still decides.

The stub qm from tests/test_wizard_guest_senders.py runs each request in a
local shell; a stand-in systemd-run records its arguments and runs what it
was handed.
"""
import re
from pathlib import Path

import pytest

from tests.test_wizard_guest_senders import (
    TEXT, bash, guest_execs, harness, shell_func, shell_var)

FIVE = [
    ("30", "profile-probe", "smd config profile-install --help >/dev/null 2>&1"),
    ("30", "profile-install", "smd config profile-install /etc/sigmond/site-profile.toml.new"),
    ("600", "personalize", "smd admin personalize --reset-identity --yes"),
    ("600", "render", "smd config render"),
    ("90", "psws-verify", "smd psws verify"),
]


def unit_harness(tmp_path: Path) -> str:
    sdrun = tmp_path / "systemd-run"
    sdrun.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\0' \"$@\" > \"{tmp_path}/sdrun.$(date +%s%N)\"\n"
        "while [ $# -gt 0 ]; do case \"$1\" in --*) shift ;; *) break ;; esac; done\n"
        "exec \"$@\"\n")
    sdrun.chmod(0o755)
    return (harness(tmp_path)
            + f"export SIGMOND_QGA_SDRUN={sdrun}\n"
            + shell_var("QGA_SDRUN")
            + shell_func("gexec_unit"))


def sdrun_calls(tmp_path: Path):
    return [p.read_bytes().split(b"\0")[:-1] for p in sorted(tmp_path.glob("sdrun.*"))]


@pytest.mark.parametrize("cmd,want", [("exit 0", "rc=0"), ("true", "rc=0"),
                                      ("exit 3", "rc=1"), ("false", "rc=1")])
def test_gexec_unit_answers_with_the_commands_own_exit(tmp_path, cmd, want):
    r = bash(unit_harness(tmp_path) + 'gexec_unit 15 render "$CMD"; echo "rc=$?"\n',
             env={"CMD": cmd})
    assert r.stdout.splitlines()[-1] == want


def test_the_command_runs_in_a_transient_unit_that_waits_and_pipes(tmp_path):
    bash(unit_harness(tmp_path) + 'gexec_unit 15 render "true"\n')
    (call,) = sdrun_calls(tmp_path)
    for flag in (b"--quiet", b"--wait", b"--pipe", b"--collect",
                 b"--unit=sigmond-wizard-render"):
        assert flag in call, call
    assert call[call.index(b"--unit=sigmond-wizard-render") + 1] == b"/bin/bash"


def test_the_command_arrives_whole(tmp_path):
    out = tmp_path / "out"
    cmd = f"printf '%s\\n' \"it's naïve — $((1+2))\" > {out}"
    r = bash(unit_harness(tmp_path) + 'gexec_unit 15 render "$CMD"; echo "rc=$?"\n',
             env={"CMD": cmd})
    assert r.stdout.splitlines()[-1] == "rc=0"
    assert out.read_text() == "it's naïve — 3\n"


def test_the_request_stays_ascii(tmp_path):
    bash(unit_harness(tmp_path) + 'gexec_unit 15 render "$CMD"\n',
         env={"CMD": "echo ─»"})
    sent = guest_execs(tmp_path)
    assert sent, "gexec_unit made no request"
    for c in sent:
        assert all(b < 0x80 for b in c), c


def test_each_of_the_five_smd_requests_goes_through_gexec_unit():
    for t, name, cmd in FIVE:
        assert f'gexec_unit {t} {name} "{cmd}"' in TEXT, name
    assert len(re.findall(r"\bgexec_unit\s+\d+\s", TEXT)) == 5


SENT = re.compile(r'\b(?:gexec|guest_out)\s+\d+\s+(?:"((?:[^"\\]|\\[\s\S])*)"|\'([^\']*)\')')


def test_no_smd_crosses_the_agent_outside_systemd_run():
    bad = []
    for m in SENT.finditer(TEXT):
        body = m.group(1) if m.group(1) is not None else m.group(2)
        at = re.search(r"\bsmd\b", body)
        if at and "systemd-run" not in body[:at.start()]:
            bad.append(body)
    assert not bad, "\n---\n".join(bad)
```

- [ ] **Step 2: Run the tests; watch them fail.**

Run: `.venv/bin/pytest tests/test_wizard_smd_units.py -q --override-ini addopts=`
Expected: FAIL. The harness raises `AssertionError: the wizard sets no QGA_SDRUN`; the two scans list the five
`gexec ... "smd ..."` lines.

- [ ] **Step 3: Add `gexec_unit`.** In `scripts/proxmox/sigmond-wizard.sh`, directly after the closing `}` of
  `gexec()` (before the comment `# Run a command in the guest and hand back its STDOUT.`), insert:

```bash
# gexec_unit <timeout-s> <name> <command...> : gexec, but the command runs in
# a transient unit of its own, sigmond-wizard-<name>, outside the guest
# agent's cgroup.  sigmond's drop-in caps the agent at MemoryMax=128M, and the
# cap covers every process a request starts: one smd needs about 55 MB, two
# pass it.  So every smd the wizard runs goes through here.  `systemd-run
# --wait` hands back the command's own exit, and --pipe passes its output
# through to the request.  The command travels base64-encoded a second time,
# so no quote inside it can break the line.  A retry after a dead agent meets
# the unit's name still in use and fails, rather than start a second smd.
QGA_SDRUN="${SIGMOND_QGA_SDRUN:-systemd-run}"
gexec_unit(){
    local t="$1" name="$2"; shift 2
    gexec "$t" "$QGA_SDRUN --quiet --wait --pipe --collect --unit=sigmond-wizard-$name /bin/bash -lc '$QGA_RUN' bash $(qga_b64 "$*")"
}
```

- [ ] **Step 4: Move the five requests.** Make exactly these five edits; keep every other character of each line.

  - `if gexec 30 "smd config profile-install --help >/dev/null 2>&1"; then` →
    `if gexec_unit 30 profile-probe "smd config profile-install --help >/dev/null 2>&1"; then`
  - `    gexec 30 "smd config profile-install /etc/sigmond/site-profile.toml.new" \` →
    `    gexec_unit 30 profile-install "smd config profile-install /etc/sigmond/site-profile.toml.new" \`
  - `gexec 600 "smd admin personalize --reset-identity --yes" \` →
    `gexec_unit 600 personalize "smd admin personalize --reset-identity --yes" \`
  - `gexec 600 "smd config render" \` → `gexec_unit 600 render "smd config render" \`
  - `    if gexec 90 "smd psws verify"; then` → `    if gexec_unit 90 psws-verify "smd psws verify"; then`

- [ ] **Step 5: Run the tests; watch them pass, and the senders' tests stay green.**

Run: `.venv/bin/pytest tests/test_wizard_smd_units.py tests/test_wizard_guest_senders.py tests/test_wizard_pair_vm.py tests/test_qga_reset.py -q --override-ini addopts=`
Expected: PASS. If `test_every_direct_request_in_the_wizard_is_ascii_and_counted` fails, the new comment holds
the words it counts.

- [ ] **Step 6: Watch each guard fail (mutations, on a copy; point `SIGMOND_TEST_WIZARD` at it).**
  1. Remove `--wait` from `gexec_unit` → `test_the_command_runs_in_a_transient_unit_that_waits_and_pipes` turns
     red.
  2. Replace `bash $(qga_b64 "$*")` with `"$*"` and `'$QGA_RUN'` with `'eval "$1"'` → `test_the_command_arrives_whole`
     turns red (the apostrophe in `it's` breaks the line).
  3. Change `gexec_unit 600 render "smd config render"` back to `gexec 600 "smd config render"` →
     `test_each_of_the_five_smd_requests_goes_through_gexec_unit` and
     `test_no_smd_crosses_the_agent_outside_systemd_run` turn red.
  4. Append `; true` inside `gexec_unit`'s outer command string → the `exit 3` and `false` cases of
     `test_gexec_unit_answers_with_the_commands_own_exit` turn red.

- [ ] **Step 7: Commit.**

```bash
git add scripts/proxmox/sigmond-wizard.sh tests/test_wizard_smd_units.py
git commit -m "wizard: every smd it sends runs in a transient unit of its own, outside the guest agent's cap"
```

---

### Task 3: the update rig runs `smd update --apply` in a transient unit

**Files:**
- Modify: `/home/mjh/hamsci/repos/sigmond-appliance/test-update-v3.sh` (~873-876; comment ~1436-1452)
- Create: `/home/mjh/hamsci/repos/sigmond-appliance/test-update-agent-cap.sh` (mode 755)

**Interfaces:**
- Consumes: nothing from another task.
- Produces: the rig line `gx_ok 3600 e3-update-apply "systemd-run --quiet --wait --pipe --collect
  --unit=rig-update-apply /bin/bash -lc 'smd update --apply'" "smd update --apply"`.

**What you need to know:**
- `gx_ok` wraps its snippet in a script, base64-encodes it, and sends it through `qm guest exec`. The snippet
  runs in a subshell whose output lands in a file; `--pipe` hands that file to the unit.
- The rig judges `UPD_RC` after the call: 0 passes, 3 (HELD) warns, anything else fails. `systemd-run --wait`
  returns the command's exit status (INFERRED from systemd-run's documentation and from `g3-restore-apply`; rig
  check R2 confirms that 3 comes through as 3).
- The rig says it tests "the channel that crashed on runuser/PATH". A transient unit gives the command the same
  kind of environment the agent gives it: a child of PID 1 with systemd's default PATH and no login session,
  then `bash -l`. INFERRED; rig check R2 covers it.
- The dry `smd update` (`e4-update-dry`), `smd version`, `smd doctor` and the adopts stay as they are. Michael's
  decision names `smd update --apply` alone; the restore already runs in a unit.

- [ ] **Step 1: Write the failing test.** Create `test-update-agent-cap.sh`:

```bash
#!/bin/bash
# test-update-agent-cap.sh -- the update rig runs each heavy smd request in a
# transient unit of its own, outside the guest agent's 128 MB cap.  No VM, no
# root; well under a second.
#
# Why: sigmond caps qemu-guest-agent.service at MemoryMax=128M, and the cap
# covers every process a `qm guest exec` starts.  On 2026-10-08 23:46:26Z the
# kernel killed restore's child under that cap, and the agent restarted under
# the rig.  `smd update --apply` can run install.sh, uv sync and a native
# build; it passed on the rig only because those runs moved little.
#
#   usage: ./test-update-agent-cap.sh [path/to/test-update-v3.sh]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:-$HERE/test-update-v3.sh}"
pass=0; fail=0
ok(){  printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad(){ printf '  FAIL  %s\n        %s\n' "$1" "${2:-}"; fail=$((fail+1)); }
UNIT='systemd-run --quiet --wait --pipe --collect --unit=rig-'

# check <label> <ERE>: every gx_ok line whose text matches runs inside a unit.
check(){
    local label="$1" re="$2" lines l
    lines="$(grep -E '^[[:space:]]*gx_ok [0-9]+ ' "$SRC" | grep -E -- "$re")"
    if [ -z "$lines" ]; then
        bad "$label: the rig sends it" "no gx_ok line matches /$re/ in $SRC; did the rig move?"
        return
    fi
    while IFS= read -r l; do
        case "$l" in
            *"$UNIT"*) ok "$label runs in a unit of its own ($(printf '%s' "$l" | awk '{print $3}'))" ;;
            *)         bad "$label runs in a unit of its own" "$l" ;;
        esac
    done <<< "$lines"
}
check 'smd update --apply' 'smd update --apply'
check 'manifest restore --apply' 'manifest restore [^"]*--apply'

# The dry run still goes straight through the agent: the rig keeps proving
# that the production channel answers smd.
dry="$(grep -E '^[[:space:]]*gx_ok [0-9]+ e4-update-dry ' "$SRC")"
case "$dry" in
    "")            bad "the dry smd update still goes straight through the agent" "no e4-update-dry line" ;;
    *systemd-run*) bad "the dry smd update still goes straight through the agent" "$dry" ;;
    *)             ok  "the dry smd update still goes straight through the agent" ;;
esac

# HELD (exit 3) must reach the rig: nothing on the line may swallow the exit.
apply="$(grep -E '^[[:space:]]*gx_ok [0-9]+ e3-update-apply ' "$SRC")"
case "$apply" in
    *'|| true'*|*'; true'*|*'exit 0'*) bad "nothing swallows smd update's exit" "$apply" ;;
    *)                                 ok  "nothing swallows smd update's exit" ;;
esac

echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
```

  Then `chmod 755 test-update-agent-cap.sh`.

- [ ] **Step 2: Run it; watch it fail.**

Run: `cd /home/mjh/hamsci/repos/sigmond-appliance && ./test-update-agent-cap.sh test-update-v3.sh`
Expected: FAIL on `smd update --apply runs in a unit of its own`, naming the `e3-update-apply` line; the restore,
dry-run and exit checks pass.

- [ ] **Step 3: Move the update into a unit.** In `test-update-v3.sh`, replace:

```bash
say "── smd update --apply (this is the channel that crashed on runuser/PATH)"
gx_ok 3600 e3-update-apply 'smd update --apply' "smd update --apply"
```

  with:

```bash
say "── smd update --apply (this is the channel that crashed on runuser/PATH)"
# ⛔ NOT INSIDE THE GUEST AGENT'S OWN CGROUP.  sigmond caps
# qemu-guest-agent.service at MemoryMax=128M, and that cap covers every
# process a `qm guest exec` starts.  An update that moves several components
# runs install.sh, uv sync and maybe a native build; it passed here only
# because those runs moved little.  An operator runs `smd update --apply`
# from an ssh session, which no such cap covers.  A transient unit gives the
# command the environment the agent gave it -- a child of PID 1, then
# `bash -l` -- so the runuser/PATH check above still means what it meant.
# `systemd-run --wait` hands back smd's own exit, so HELD (3) still reads 3.
gx_ok 3600 e3-update-apply "systemd-run --quiet --wait --pipe --collect --unit=rig-update-apply /bin/bash -lc 'smd update --apply'" "smd update --apply"
```

- [ ] **Step 4: Bring the restore's comment up to date.** In the comment above `g3-restore-apply`, replace:

```bash
# goes through the agent as before.
```

  with:

```bash
# goes through the agent as before.  From v3.71 the drop-in also sets
# OOMPolicy=continue, so a killed child no longer takes the agent down; the
# restore keeps its unit all the same, since a killed restore would still
# fail this phase.
```

  If that sentence no longer ends the comment, append the four new lines at the comment's end and say so in
  your report.

- [ ] **Step 5: Run the test; watch it pass, and the rig still parse.**

Run: `./test-update-agent-cap.sh test-update-v3.sh && ./test-update-smd-lines.sh test-update-v3.sh && bash -n test-update-v3.sh`
Expected: PASS; `bash -n` prints nothing.

- [ ] **Step 6: Watch each guard fail (mutations, on a copy in `/tmp`, passed as `$1`).**
  1. Restore the old `e3-update-apply` line → `smd update --apply runs in a unit of its own` turns red.
  2. Append ` || true` inside the `e3` snippet → `nothing swallows smd update's exit` turns red.
  3. Wrap the `e4-update-dry` snippet in `systemd-run` → the dry-run check turns red.
  4. Remove `systemd-run ... --unit=rig-restore-apply` from `g3-restore-apply` → `manifest restore --apply runs
     in a unit of its own` turns red.
  5. Change `--wait` to `--no-block` in the `e3` line → `smd update --apply runs in a unit of its own` turns red
     (the line no longer holds the exact prefix the test asks for).

- [ ] **Step 7: Commit.**

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance
git add test-update-v3.sh test-update-agent-cap.sh
git commit -m "rigs: the update rig runs smd update --apply in a transient unit, outside the guest agent's cap"
```

---

### Task 4: the episode record

**Files:**
- Create: `/home/mjh/hamsci/repos/sigmond/lib/sigmond/sdr_events.py`
- Create: `/home/mjh/hamsci/repos/sigmond/tests/test_sdr_events.py`

**Interfaces:**
- Consumes: nothing.
- Produces, in `sigmond.sdr_events`:
  - constants `EVENTS_PATH = Path("/var/lib/sigmond/sdr-events.jsonl")`, `SYSFS_USB`, `EPISODE_GAP_S = 600`,
    `SETTLE_S = 120`, `KEEP_S = 604800`, `MAX_LINES = 200`, `WINDOW_S = 86400`, `FLAP_EPISODES = 3`,
    `LONG_EPISODE_S = 1800`, `DETAIL_MAX = 120`,
    `LOSS_KINDS = ("usb-remove", "power-cycle", "radiod-exit", "radiod-wedge")`, `ARRIVAL_KIND = "usb-add"`,
    `KINDS`, `RADIOD_LOSS_RESULTS`, `RX888_IDS`;
  - `@dataclass Episode(opened: float, last: float, kinds: list[str], count: int, detail: str = "", back:
    Optional[float] = None)`;
  - `record(kind: str, detail: str = "", *, now: float, path: Path = EVENTS_PATH) -> Optional[str]` returning
    `"opened"`, `"grown"`, `"back"` or `None`; raises `ValueError` on an unknown kind;
  - `read_episodes(path: Path = EVENTS_PATH, *, now: float, window_s: float = WINDOW_S) -> list[Episode]`;
  - `card_present(root: Path = SYSFS_USB) -> bool`;
  - `episode_span(ep: Episode) -> float`, the seconds an episode stayed open;
  - `assess(episodes: Sequence[Episode], present: bool) -> Optional[tuple[str, str]]` returning
    `("sdr-lost"|"sdr-down-long"|"sdr-flapping"|"sdr-recovered", detail)`;
  - `radiod_exit_is_loss(service_result: str) -> bool`;
  - `clean_text(s, limit: int = DETAIL_MAX) -> str`.

**What you need to know:**
- **Episodes merge across kinds** (a departure from the research, which merged within one kind). A single vanish
  brings a udev removal, a radiod exit every 10 s while the card stays away, and sdr-recover's power cut after
  30 s. Those make one episode.
- **A loss joins the open episode** when it comes within `EPISODE_GAP_S` of that episode's last event, and the
  card has not come back, or came back less than `SETTLE_S` ago. A `usb-add` marks the open episode `back` and
  opens nothing. A `usb-remove` that joins an episode clears its `back`; other kinds leave it.
- So ND's loop of 2026-10-05 (1,708 radiod restarts about 20 s apart over 9.5 h) makes one line with count 1708,
  and a card that drops, returns and drops again five minutes later makes two episodes.
- **The 30-minute rule (Michael, 2026-10-11).** An episode that stayed open longer than `LONG_EPISODE_S` (1800 s)
  reads INVALID under the kind `sdr-down-long`, for as long as it sits in the 24 h window. `episode_span`
  measures it: from the first event to the card's return when one came, else to the last event. A stall with the
  card on the bus never brings a return, but radiod restarts every 10 s while it lasts
  (`systemd/radiod-restart-forever.conf`), so its last event tracks the outage. ND's loop reads `sdr-down-long`.
  Exactly 1800 s stays `sdr-recovered`.
- **Why a new kind, not `sdr-lost`.** `sdr-lost` means one thing an operator can check by hand: the card sits off
  the bus right now. A 40-minute stall that ended an hour ago leaves the card on the bus. Folding it into
  `sdr-lost` would send the operator to look for a card that sits in plain view. So `assess` tries `sdr-lost`
  first (the present tense wins), then `sdr-down-long`, then `sdr-flapping`, then `sdr-recovered`.
- **`radiod-wedge`** joins the loss kinds for Task 7: the radiod watchdog restarts a radiod that said
  "quitting" yet stayed active, or went silent. It merges like any other loss.
- Every boot brings a `usb-add` of the running card. With no open episode it writes nothing.
- The file lives in `/var/lib`: a lost card clusters around reboots and cold starts, and `/run` forgets across
  them. Writers run as root (udev, sdr-recover, radiod's drop-in with `+`); `smd doctor` may run as a user, so
  the file stays mode 0644. A lock file beside it serialises the writers.
- `card_present` reads sysfs, not `lsusb`: the same two ids sdr-recover treats as present (04b4:00f1 running,
  04b4:00f3 bootloader).

- [ ] **Step 1: Write the failing tests.** Create `tests/test_sdr_events.py`:

```python
"""The record of a station losing its RX888 (lib/sigmond/sdr_events.py).

One line per EPISODE, never per event: ND's loop of 2026-10-05 (1,708 radiod
restarts in 9.5 h) must make one line, and one vanish -- a udev removal,
radiod's failing restarts, sdr-recover's power cut -- must make one episode,
or a single loss would read as flapping.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond import sdr_events as ev  # noqa: E402

T0 = 1_760_000_000.0


def lines(p):
    return [json.loads(l) for l in p.read_text().splitlines()]


def good_line(opened=T0, last=T0, kinds=("radiod-exit",), count=1):
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    from datetime import datetime, timezone
    iso = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).strftime(fmt)
    return json.dumps({"opened": iso(opened), "last": iso(last), "kinds": list(kinds),
                       "count": count, "detail": "", "back": None})


# -- the write side --------------------------------------------------------------------

def test_a_first_loss_opens_one_episode(tmp_path):
    p = tmp_path / "e.jsonl"
    assert ev.record("radiod-exit", "ac0g core-dump dumped/ABRT", now=T0, path=p) == "opened"
    (d,) = lines(p)
    assert d["kinds"] == ["radiod-exit"] and d["count"] == 1 and d["back"] is None
    assert d["opened"].endswith("Z") and d["opened"] == d["last"]


def test_a_second_loss_inside_ten_minutes_grows_the_same_line(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-exit", now=T0, path=p)
    assert ev.record("radiod-exit", now=T0 + 599, path=p) == "grown"
    (d,) = lines(p)
    assert d["count"] == 2


def test_a_loss_after_ten_minutes_opens_another(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-exit", now=T0, path=p)
    assert ev.record("radiod-exit", now=T0 + 601, path=p) == "opened"
    assert len(lines(p)) == 2


def test_the_nd_restart_loop_makes_one_line(tmp_path):
    p = tmp_path / "e.jsonl"
    for i in range(1708):
        ev.record("radiod-exit", now=T0 + 20 * i, path=p)
    (d,) = lines(p)
    assert d["count"] == 1708


def test_one_vanish_is_one_episode_whatever_its_kinds(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("usb-remove", "8-1.2", now=T0, path=p)
    ev.record("radiod-exit", now=T0 + 1, path=p)
    ev.record("radiod-exit", now=T0 + 11, path=p)
    ev.record("power-cycle", "8-1p2", now=T0 + 30, path=p)
    assert ev.record("usb-add", "8-1.2", now=T0 + 60, path=p) == "back"
    (d,) = lines(p)
    assert d["kinds"] == ["power-cycle", "radiod-exit", "usb-remove"]
    assert d["count"] == 4
    assert d["back"] is not None


def test_a_watchdog_restart_joins_the_episode_like_any_loss(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-wedge", "radiod@ac0g.service no journal output for 700s", now=T0, path=p)
    assert ev.record("radiod-exit", now=T0 + 20, path=p) == "grown"
    (d,) = lines(p)
    assert d["kinds"] == ["radiod-exit", "radiod-wedge"] and d["count"] == 2
    assert ev.read_episodes(p, now=T0 + 30)[0].kinds == ["radiod-exit", "radiod-wedge"]


def test_a_card_that_drops_again_after_it_settled_opens_a_new_episode(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("usb-remove", now=T0, path=p)
    ev.record("usb-add", now=T0 + 5, path=p)
    assert ev.record("usb-remove", now=T0 + 300, path=p) == "opened"
    assert len(lines(p)) == 2


def test_a_radiod_exit_just_after_the_return_joins_and_keeps_the_return(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("usb-remove", now=T0, path=p)
    ev.record("usb-add", now=T0 + 40, path=p)
    assert ev.record("radiod-exit", now=T0 + 45, path=p) == "grown"
    assert lines(p)[0]["back"] is not None
    assert ev.record("usb-remove", now=T0 + 300, path=p) == "opened"


def test_an_arrival_with_no_loss_before_it_writes_nothing(tmp_path):
    p = tmp_path / "e.jsonl"
    assert ev.record("usb-add", "8-1.2", now=T0, path=p) is None
    assert not p.exists()


def test_episodes_older_than_seven_days_drop_on_the_next_write(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("usb-remove", now=T0, path=p)
    ev.record("usb-remove", now=T0 + ev.KEEP_S + 1, path=p)
    assert len(lines(p)) == 1


def test_the_file_never_passes_200_lines(tmp_path):
    p = tmp_path / "e.jsonl"
    for i in range(250):
        ev.record("usb-remove", now=T0 + 700 * i, path=p)
    got = lines(p)
    assert len(got) == ev.MAX_LINES
    assert got[0]["opened"] == ev._iso(T0 + 700 * 50)      # the oldest 50 went
    assert got[-1]["opened"] == ev._iso(T0 + 700 * 249)    # the newest stayed


def test_detail_is_printable_ascii_and_bounded(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-exit", "\x1b[31m" + "é" * 500, now=T0, path=p)
    d = lines(p)[0]["detail"]
    assert d.isascii() and d.isprintable() and len(d) <= ev.DETAIL_MAX


def test_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(ValueError):
        ev.record("rm -rf", now=T0, path=tmp_path / "e.jsonl")


def test_the_file_stays_world_readable(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("usb-remove", now=T0, path=p)
    assert os.stat(p).st_mode & 0o777 == 0o644


# -- the read side ---------------------------------------------------------------------

def test_damaged_and_hostile_lines_are_skipped(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text("\n".join([
        "not json",
        json.dumps([1, 2]),
        good_line(kinds=("rm -rf",)),
        good_line(count=True),
        good_line(count=0),
        '{"kinds": ["usb-remove"], "count": 1}',
        good_line(),
    ]) + "\n")
    eps = ev.read_episodes(p, now=T0 + 10)
    assert len(eps) == 1 and eps[0].kinds == ["radiod-exit"]
    ev.record("usb-remove", now=T0 + 3600, path=p)
    assert len(lines(p)) == 2


def test_read_episodes_keeps_the_last_24_hours(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-exit", now=T0, path=p)
    ev.record("radiod-exit", now=T0 + 90_000, path=p)
    eps = ev.read_episodes(p, now=T0 + 90_010)
    assert len(eps) == 1


def test_read_of_a_missing_file_is_empty(tmp_path):
    assert ev.read_episodes(tmp_path / "none.jsonl", now=T0) == []


# -- is the card on the bus ------------------------------------------------------------

def make_dev(root, name, vid, pid):
    d = root / name
    d.mkdir(parents=True)
    (d / "idVendor").write_text(vid + "\n")
    (d / "idProduct").write_text(pid + "\n")


def test_the_running_card_counts_as_present(tmp_path):
    make_dev(tmp_path, "8-1.2", "04b4", "00f1")
    assert ev.card_present(tmp_path)


def test_the_bootloader_counts_as_present(tmp_path):
    make_dev(tmp_path, "8-1.2", "04b4", "00f3")
    assert ev.card_present(tmp_path)


def test_other_devices_do_not_count(tmp_path):
    make_dev(tmp_path, "6-1.3", "239a", "80cb")
    (tmp_path / "usb6").mkdir()
    assert not ev.card_present(tmp_path)


def test_no_usb_tree_means_no_card(tmp_path):
    assert not ev.card_present(tmp_path / "none")


# -- the verdict -----------------------------------------------------------------------

def ep(opened, kinds=("radiod-exit",), count=1, detail="", span=0, back=None):
    return ev.Episode(opened=opened, last=opened + span, kinds=list(kinds), count=count,
                      detail=detail, back=back)


def test_nothing_to_assess_without_episodes():
    assert ev.assess([], True) is None
    assert ev.assess([], False) is None


def test_one_or_two_episodes_with_the_card_back_read_recovered():
    assert ev.assess([ep(T0)], True)[0] == "sdr-recovered"
    assert ev.assess([ep(T0), ep(T0 + 3600)], True)[0] == "sdr-recovered"


def test_three_episodes_read_flapping():
    assert ev.assess([ep(T0), ep(T0 + 3600), ep(T0 + 7200)], True)[0] == "sdr-flapping"


def test_the_card_off_the_bus_reads_lost_whatever_the_count():
    assert ev.assess([ep(T0, kinds=("usb-remove",))], False)[0] == "sdr-lost"


def test_the_detail_names_how_often_and_when_in_utc():
    kind, detail = ev.assess([ep(T0), ep(T0 + 3600, detail="ac0g core-dump dumped/ABRT")], True)
    assert "2 time(s) in 24 h" in detail
    assert "Z" in detail and "ABRT" in detail


# -- the 30-minute rule (Michael, 2026-10-11) -------------------------------------------

def test_an_episode_open_longer_than_30_minutes_reads_down_long():
    kind, detail = ev.assess([ep(T0, span=1801)], True)
    assert kind == "sdr-down-long"
    assert "30 min" in detail


def test_exactly_30_minutes_stays_recovered():
    assert ev.assess([ep(T0, span=1800)], True)[0] == "sdr-recovered"


def test_a_return_after_30_minutes_counts_as_long():
    # The card left, radiod failed a few starts, then nothing came until the
    # card returned 31 minutes after the loss.
    long = ep(T0, kinds=("usb-remove",), span=60, back=T0 + 1860)
    assert ev.episode_span(long) == 1860
    assert ev.assess([long], True)[0] == "sdr-down-long"


def test_a_long_episode_earlier_in_the_day_still_reads_down_long():
    assert ev.assess([ep(T0, span=3600), ep(T0 + 7200)], True)[0] == "sdr-down-long"


def test_a_long_episode_names_itself_before_flapping():
    eps = [ep(T0), ep(T0 + 3600, span=2400), ep(T0 + 7200)]
    assert ev.assess(eps, True)[0] == "sdr-down-long"


def test_the_card_off_the_bus_reads_lost_even_when_the_episode_ran_long():
    assert ev.assess([ep(T0, kinds=("usb-remove",), span=3600)], False)[0] == "sdr-lost"


def test_the_nd_restart_loop_reads_down_long(tmp_path):
    p = tmp_path / "e.jsonl"
    for i in range(1708):
        ev.record("radiod-exit", now=T0 + 20 * i, path=p)
    eps = ev.read_episodes(p, now=T0 + 20 * 1708)
    assert ev.assess(eps, True)[0] == "sdr-down-long"


@pytest.mark.parametrize("result,loss", [
    ("success", False), ("timeout", False), ("", False), ("start-limit-hit", False),
    ("exit-code", True), ("signal", True), ("core-dump", True), ("watchdog", True)])
def test_which_radiod_exits_count_as_a_loss(result, loss):
    assert ev.radiod_exit_is_loss(result) is loss
```

- [ ] **Step 2: Run the tests; watch them fail.**

Run: `.venv/bin/pytest tests/test_sdr_events.py -q --override-ini addopts=`
Expected: FAIL at collection: `ImportError: cannot import name 'sdr_events' from 'sigmond'`.

- [ ] **Step 3: Write the module.** Create `lib/sigmond/sdr_events.py`:

```python
"""The station's record of losing its RX888: one line per episode.

Three things lose the RX888 for a station, and each ends the same way: radiod
loses its samples, the station recovers by itself, and until v3.71 nobody
heard about it.

* The card leaves the USB bus (udev: usb-remove), and maybe returns (usb-add).
* sigmond-sdr-recover cuts power to bring a vanished card back (power-cycle).
* The stream stalls with the card still on the bus: radiod logs "No rx888
  data for 5 seconds, quitting" and dies on a libusb assertion
  (radiod-exit).  AC0G-ND did this three times between 2026-10-10 10:13Z and
  2026-10-11 08:49Z, and its card never left the bus.
* radiod says "quitting" yet hangs, or falls silent, and
  sigmond-radiod-watchdog restarts it (radiod-wedge).  systemd reads that
  restart as asked for, so radiod's own hook writes nothing; the watchdog
  writes the event itself.

Writers call record(); `smd doctor` calls read_episodes() and assess().  The
file lives in /var/lib, not /run: a lost card clusters around reboots and
cold starts, and /run forgets across exactly those.

One line per EPISODE, never per event.  A loss of any kind within
EPISODE_GAP_S of the open episode's last event joins it, unless the card
came back more than SETTLE_S before.  ND's loop of 2026-10-05 (1,708 radiod
restarts in 9.5 h) makes one line with count 1708; one vanish -- removal,
radiod's failing restarts, the power cut -- makes one episode; a card that
drops, returns and drops again minutes later makes two.

An episode that stayed open longer than LONG_EPISODE_S reads INVALID
(sdr-down-long), even after the card came back (Michael's rule,
2026-10-11).  Shorter, a lone episode reads sdr-recovered.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

EVENTS_PATH = Path("/var/lib/sigmond/sdr-events.jsonl")
SYSFS_USB = Path("/sys/bus/usb/devices")

EPISODE_GAP_S = 600          # a loss this soon after the last one joins its episode
SETTLE_S = 120               # ... and so does one this soon after the card came back
KEEP_S = 7 * 86400           # episodes older than this drop on the next write
MAX_LINES = 200              # the file never holds more
WINDOW_S = 86400             # what `smd doctor` reads
FLAP_EPISODES = 3            # this many episodes in WINDOW_S read INVALID
LONG_EPISODE_S = 1800        # an episode open longer than this reads INVALID
DETAIL_MAX = 120

LOSS_KINDS = ("usb-remove", "power-cycle", "radiod-exit", "radiod-wedge")
ARRIVAL_KIND = "usb-add"
KINDS = LOSS_KINDS + (ARRIVAL_KIND,)

# What systemd sets $SERVICE_RESULT to when radiod died on its own.  A stop
# that someone asked for reads "success": radiod answers SIGTERM with EX_OK
# (ka9q-radio src/main.c, closedown).  "timeout" means an asked-for stop that
# ran long and ended in SIGKILL; it loses nothing.
RADIOD_LOSS_RESULTS = frozenset({"exit-code", "signal", "core-dump", "watchdog"})

# The running card (00f1) and its FX3 bootloader (00f3): either one sits on the
# bus.  The same pair sigmond-sdr-recover looks for.
RX888_IDS = (("04b4", "00f1"), ("04b4", "00f3"))

_FMT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass
class Episode:
    opened: float
    last: float
    kinds: List[str]
    count: int
    detail: str = ""
    back: Optional[float] = None


def clean_text(s, limit: int = DETAIL_MAX) -> str:
    """Printable ASCII only, at most `limit` characters."""
    return "".join(c if " " <= c <= "~" else "?" for c in str(s or ""))[:limit]


def radiod_exit_is_loss(service_result: str) -> bool:
    return service_result in RADIOD_LOSS_RESULTS


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime(_FMT)


def _epoch(s) -> float:
    return datetime.strptime(str(s), _FMT).replace(tzinfo=timezone.utc).timestamp()


def _parse(line: str) -> Optional[Episode]:
    try:
        d = json.loads(line)
        kinds, count, back = d["kinds"], d["count"], d.get("back")
        if (not isinstance(kinds, list) or not kinds
                or any(k not in LOSS_KINDS for k in kinds)):
            return None
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            return None
        return Episode(opened=_epoch(d["opened"]), last=_epoch(d["last"]),
                       kinds=sorted(set(kinds)), count=count,
                       detail=clean_text(d.get("detail", "")),
                       back=_epoch(back) if back else None)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _dump(ep: Episode) -> str:
    return json.dumps({"opened": _iso(ep.opened), "last": _iso(ep.last),
                       "kinds": ep.kinds, "count": ep.count, "detail": ep.detail,
                       "back": _iso(ep.back) if ep.back is not None else None},
                      sort_keys=True)


def _load(path: Path) -> List[Episode]:
    try:
        text = Path(path).read_text(errors="replace")
    except FileNotFoundError:
        return []
    return [ep for ep in map(_parse, text.splitlines()) if ep is not None]


def _write(path: Path, episodes: Sequence[Episode]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("".join(_dump(ep) + "\n" for ep in episodes))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record(kind: str, detail: str = "", *, now: float,
           path: Path = EVENTS_PATH) -> Optional[str]:
    """Note one event.  Returns "opened" when it began an episode, "grown"
    when it joined the open one, "back" when the card returned to an open
    episode, and None when it changed nothing (an arrival with no loss
    before it, as at every boot)."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {', '.join(KINDS)}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / (path.name + ".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        episodes = [ep for ep in _load(path) if ep.last >= now - KEEP_S]
        cur = episodes[-1] if episodes else None
        what = None
        if kind == ARRIVAL_KIND:
            if cur is not None and cur.back is None and now - cur.last <= WINDOW_S:
                cur.back = now
                what = "back"
        elif (cur is not None and now - cur.last <= EPISODE_GAP_S
              and (cur.back is None or now - cur.back <= SETTLE_S)):
            cur.last = now
            cur.count += 1
            cur.kinds = sorted(set(cur.kinds) | {kind})
            if kind == "usb-remove":
                cur.back = None
            if detail:
                cur.detail = clean_text(detail)
            what = "grown"
        else:
            episodes.append(Episode(opened=now, last=now, kinds=[kind], count=1,
                                    detail=clean_text(detail)))
            what = "opened"
        if what is not None:
            _write(path, episodes[-MAX_LINES:])
    return what


def read_episodes(path: Path = EVENTS_PATH, *, now: float,
                  window_s: float = WINDOW_S) -> List[Episode]:
    """The episodes whose last event falls inside the window, oldest first."""
    return [ep for ep in _load(Path(path)) if ep.last >= now - window_s]


def card_present(root: Path = SYSFS_USB) -> bool:
    """True when a running RX888 or its bootloader sits on the USB bus."""
    try:
        entries = list(Path(root).iterdir())
    except OSError:
        return False
    for d in entries:
        try:
            vid = (d / "idVendor").read_text().strip().lower()
            pid = (d / "idProduct").read_text().strip().lower()
        except OSError:
            continue
        if (vid, pid) in RX888_IDS:
            return True
    return False


def _hm(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def episode_span(ep: Episode) -> float:
    """How long the episode stayed open, in seconds: from its first event to
    the card's return when one came, else to its last event.  A stall with
    the card on the bus brings no return, but radiod restarts every 10 s while
    it lasts, so the last event tracks the outage."""
    end = ep.last if ep.back is None else max(ep.last, ep.back)
    return end - ep.opened


def _tail(ep: Episode) -> str:
    tail = f"{'+'.join(ep.kinds)}, {ep.count} event(s)"
    if ep.detail:
        tail += f", {ep.detail}"
    return tail


def assess(episodes: Sequence[Episode], present: bool) -> Optional[Tuple[str, str]]:
    """`smd doctor`'s verdict on the window: (kind, detail), or None.

    The present tense wins: a card off the bus now reads sdr-lost.  Then an
    episode open longer than LONG_EPISODE_S reads sdr-down-long, then three
    episodes read sdr-flapping.  Only what remains reads sdr-recovered."""
    if not episodes:
        return None
    n = len(episodes)
    last = episodes[-1]
    tail = _tail(last)
    if not present:
        return ("sdr-lost",
                f"RX888 off the bus now; lost at {_hm(last.opened)} ({tail}); "
                f"{n} loss episode(s) in 24 h")
    long = [ep for ep in episodes if episode_span(ep) > LONG_EPISODE_S]
    if long:
        worst = max(long, key=episode_span)
        return ("sdr-down-long",
                f"RX888 lost for {int(episode_span(worst) // 60)} min from "
                f"{_hm(worst.opened)} ({_tail(worst)}); {n} loss episode(s) in 24 h")
    if n >= FLAP_EPISODES:
        return ("sdr-flapping",
                f"RX888 lost {n} times in 24 h, last at {_hm(last.opened)} ({tail}); "
                f"back each time, but something keeps taking it")
    return ("sdr-recovered",
            f"RX888 lost {n} time(s) in 24 h, last at {_hm(last.opened)} ({tail}); recovered")
```

- [ ] **Step 4: Run the tests; watch them pass.**

Run: `.venv/bin/pytest tests/test_sdr_events.py -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 5: Watch each guard fail (mutations, on a copy).**
  1. Replace the `elif (cur is not None and now - cur.last <= EPISODE_GAP_S ...` condition with `elif False:` →
     `test_a_second_loss_inside_ten_minutes_grows_the_same_line`, `test_the_nd_restart_loop_makes_one_line` and
     `test_one_vanish_is_one_episode_whatever_its_kinds` turn red.
  2. Add `and kind in cur.kinds` to that condition (the research's same-kind rule) →
     `test_one_vanish_is_one_episode_whatever_its_kinds` turns red.
  3. Drop `(cur.back is None or now - cur.back <= SETTLE_S)` →
     `test_a_card_that_drops_again_after_it_settled_opens_a_new_episode` turns red.
  4. Clear `back` on every merge, not only on `usb-remove` →
     `test_a_radiod_exit_just_after_the_return_joins_and_keeps_the_return` turns red.
  5. Remove the `ep.last >= now - KEEP_S` filter → `test_episodes_older_than_seven_days_drop_on_the_next_write`
     turns red.
  6. Write `episodes` instead of `episodes[-MAX_LINES:]` → `test_the_file_never_passes_200_lines` turns red.
  7. Store `detail` without `clean_text` → `test_detail_is_printable_ascii_and_bounded` turns red.
  8. Let an arrival with nothing open append an episode → `test_an_arrival_with_no_loss_before_it_writes_nothing`
     turns red.
  9. Set `FLAP_EPISODES = 4` → `test_three_episodes_read_flapping` turns red.
  10. Remove `os.chmod(tmp, 0o644)` → `test_the_file_stays_world_readable` turns red (mkstemp makes 0600).
  11. Add `"timeout"` to `RADIOD_LOSS_RESULTS` → `test_which_radiod_exits_count_as_a_loss[timeout-False]` turns
      red.
  12. Set `LONG_EPISODE_S = 3600` → `test_an_episode_open_longer_than_30_minutes_reads_down_long` and
      `test_a_return_after_30_minutes_counts_as_long` turn red.
  13. Change `episode_span(ep) > LONG_EPISODE_S` to `>=` → `test_exactly_30_minutes_stays_recovered` turns red.
  14. In `episode_span`, ignore `back` (`end = ep.last`) → `test_a_return_after_30_minutes_counts_as_long` turns
      red.
  15. Test only the newest episode (`long = [last] if episode_span(last) > LONG_EPISODE_S else []`) →
      `test_a_long_episode_earlier_in_the_day_still_reads_down_long` and
      `test_a_long_episode_names_itself_before_flapping` turn red.
  16. Move the `sdr-down-long` check above the `if not present:` block →
      `test_the_card_off_the_bus_reads_lost_even_when_the_episode_ran_long` turns red.
  17. Drop `"radiod-wedge"` from `LOSS_KINDS` → `test_a_watchdog_restart_joins_the_episode_like_any_loss` turns
      red (`record` refuses the kind).

- [ ] **Step 6: Commit.**

```bash
git add lib/sigmond/sdr_events.py tests/test_sdr_events.py
git commit -m "sdr: one line per episode when the station loses its RX888, read back over 24 h; past 30 minutes it reads INVALID"
```

---

### Task 5: the writer and its hooks: udev, radiod's exit, install

**Files:**
- Create: `/home/mjh/hamsci/repos/sigmond/bin/sigmond-sdr-event` (mode 755)
- Create: `/home/mjh/hamsci/repos/sigmond/udev/91-sigmond-sdr-event.rules`
- Create: `/home/mjh/hamsci/repos/sigmond/systemd/radiod-sdr-event.conf`
- Modify: `/home/mjh/hamsci/repos/sigmond/bin/smd` (`_RADIOD_DROPINS` ~10713)
- Modify: `/home/mjh/hamsci/repos/sigmond/install.sh` (~932-934, ~962-964, ~1015-1017)
- Modify: `/home/mjh/hamsci/repos/sigmond/tests/test_radiod_dropins.py` (`_DROPIN_RELPATHS` ~40; both
  `src_names` lists ~108 and ~230)
- Create: `/home/mjh/hamsci/repos/sigmond/tests/test_sdr_event_hooks.py`

**Interfaces:**
- Consumes: `sigmond.sdr_events.record`, `KINDS`, `EVENTS_PATH`, `radiod_exit_is_loss` (Task 4);
  `_RADIOD_DROPINS` and the ensure/finding machinery (Task 1).
- Produces: the command `/usr/local/sbin/sigmond-sdr-event <kind> [detail...]`. It exits 0 always, except 2 for
  an unknown kind. With `SIGMOND_SDR_EVENTS` set it writes there and speaks on stderr; without it, it writes
  `EVENTS_PATH` and speaks to syslog.

**What you need to know:**
- **udev** runs `RUN+=` programs with no `PATH` worth trusting, so the rule names `/usr/bin/python3` and the
  script's full path. udev discards a program's output, hence syslog. On `remove`, udev holds no `ATTR{}` any
  more; the kernel's `PRODUCT` (vendor/product/bcd in hex, no leading zeros) still names the card (INFERRED; rig
  check R3). The rule matches `00f1` only: the bootloader `00f3` leaves the bus at every firmware load, at every
  boot.
- **radiod** runs as `User=radio` with `NoNewPrivileges` and `ProtectHome`. `ExecStopPost=` would run the writer
  as `radio`, which cannot write `/var/lib/sigmond`. The `+` prefix runs it with full privileges, outside the
  sandbox; the `-` prefix keeps a writer failure from marking radiod failed. systemd sets `SERVICE_RESULT`,
  `EXIT_CODE` and `EXIT_STATUS` for `ExecStopPost=`.
- **A radiod restart does not re-enumerate the card**: ND's card kept device 004 through seven restarts. So the
  udev rule stays quiet on a healthy station.
- The radiod drop-in reaches stations through `_RADIOD_DROPINS` (so `smd doctor --fix` and the native radiod
  install) and through install.sh's radiod block. The writer's symlink and the udev rule reach them through
  install.sh only. This release changes install.sh, so `smd update` re-runs it everywhere.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_sdr_event_hooks.py`:

```python
"""The writer and the hooks that feed it (v3.71).

udev notes the RX888 leaving and rejoining the bus, and radiod's drop-in
notes an exit nobody asked for.  sigmond-sdr-recover notes its power cuts
(tests/test_sdr_recover.py), and sigmond-radiod-watchdog its restarts
(tests/test_radiod_watchdog.py), both in-process.  The writer must never
fail its caller.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "bin" / "sigmond-sdr-event"
RULE = REPO / "udev" / "91-sigmond-sdr-event.rules"
DROPIN = REPO / "systemd" / "radiod-sdr-event.conf"


def run_cli(tmp_path, *args, events=None, **env):
    e = {k: v for k, v in os.environ.items()
         if k not in ("SERVICE_RESULT", "EXIT_CODE", "EXIT_STATUS")}
    e["SIGMOND_SDR_EVENTS"] = str(events or tmp_path / "sdr-events.jsonl")
    e.update(env)
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True,
                          text=True, env=e, timeout=30)


def lines(p):
    return [json.loads(l) for l in p.read_text().splitlines()]


# -- the writer, as radiod's drop-in runs it --------------------------------------------

def test_an_exit_someone_asked_for_writes_nothing(tmp_path):
    r = run_cli(tmp_path, "radiod-exit", "ac0g", SERVICE_RESULT="success",
                EXIT_CODE="exited", EXIT_STATUS="0")
    assert r.returncode == 0
    assert not (tmp_path / "sdr-events.jsonl").exists()


def test_a_slow_stop_writes_nothing(tmp_path):
    r = run_cli(tmp_path, "radiod-exit", "ac0g", SERVICE_RESULT="timeout",
                EXIT_CODE="killed", EXIT_STATUS="KILL")
    assert r.returncode == 0
    assert not (tmp_path / "sdr-events.jsonl").exists()


def test_a_run_by_hand_outside_systemd_writes_nothing(tmp_path):
    assert run_cli(tmp_path, "radiod-exit", "ac0g").returncode == 0
    assert not (tmp_path / "sdr-events.jsonl").exists()


def test_a_stall_writes_one_episode_naming_how_radiod_died(tmp_path):
    r = run_cli(tmp_path, "radiod-exit", "ac0g", SERVICE_RESULT="core-dump",
                EXIT_CODE="dumped", EXIT_STATUS="ABRT")
    assert r.returncode == 0
    (d,) = lines(tmp_path / "sdr-events.jsonl")
    assert d["kinds"] == ["radiod-exit"]
    assert d["detail"] == "ac0g core-dump dumped/ABRT"


def test_the_opening_line_speaks_once_per_episode(tmp_path):
    env = dict(SERVICE_RESULT="signal", EXIT_CODE="killed", EXIT_STATUS="ABRT")
    first = run_cli(tmp_path, "radiod-exit", "ac0g", **env)
    second = run_cli(tmp_path, "radiod-exit", "ac0g", **env)
    assert "RX888 lost" in first.stderr
    assert second.stderr == ""


# -- the writer, as udev runs it --------------------------------------------------------

def test_a_removal_then_a_return_make_one_episode(tmp_path):
    assert run_cli(tmp_path, "usb-remove", "8-1.2").returncode == 0
    assert run_cli(tmp_path, "usb-add", "8-1.2").returncode == 0
    (d,) = lines(tmp_path / "sdr-events.jsonl")
    assert d["kinds"] == ["usb-remove"] and d["back"] is not None


def test_an_unknown_kind_exits_2_and_writes_nothing(tmp_path):
    r = run_cli(tmp_path, "unplugged")
    assert r.returncode == 2
    assert not (tmp_path / "sdr-events.jsonl").exists()


def test_a_record_it_cannot_write_still_exits_0(tmp_path):
    (tmp_path / "file").write_text("")
    r = run_cli(tmp_path, "usb-remove", "8-1.2", events=tmp_path / "file" / "sdr-events.jsonl")
    assert r.returncode == 0
    assert "could not record" in r.stderr


# -- the udev rule ----------------------------------------------------------------------

def rule_lines():
    return [l for l in RULE.read_text().splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def test_the_rule_file_holds_ascii_only():
    RULE.read_bytes().decode("ascii")


def test_exactly_two_rules():
    assert len(rule_lines()) == 2


def test_a_removal_matches_the_running_card_by_the_kernels_product():
    (rm,) = [l for l in rule_lines() if 'ACTION=="remove"' in l]
    assert 'ENV{DEVTYPE}=="usb_device"' in rm
    assert 'ENV{PRODUCT}=="4b4/f1/*"' in rm
    assert "ATTR{" not in rm
    assert 'RUN+="/usr/bin/python3 /usr/local/sbin/sigmond-sdr-event usb-remove %k"' in rm


def test_an_arrival_matches_the_same_card():
    (add,) = [l for l in rule_lines() if 'ACTION=="add"' in l]
    assert 'ENV{PRODUCT}=="4b4/f1/*"' in add
    assert 'RUN+="/usr/bin/python3 /usr/local/sbin/sigmond-sdr-event usb-add %k"' in add


def test_the_bootloader_never_counts():
    for l in rule_lines():
        assert "f3" not in l, l


# -- radiod's drop-in -------------------------------------------------------------------

def test_the_dropin_runs_the_writer_as_root_and_never_fails_radiod():
    text = DROPIN.read_text()
    text.encode("ascii")
    body = [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert body == ["[Service]",
                    "ExecStopPost=-+/usr/local/sbin/sigmond-sdr-event radiod-exit %i"]


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader("smd_under_test_sdr_hooks", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_sdr_hooks", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_doctor_fix_and_the_native_install_deliver_the_dropin():
    smd = _load_smd()
    assert ("radiod-sdr-event.conf", "radiod@.service.d/50-sigmond-sdr-event.conf") in smd._RADIOD_DROPINS


# -- install.sh -------------------------------------------------------------------------

def test_install_sh_lays_down_the_writer_the_rule_and_the_dropin():
    sh = (REPO / "install.sh").read_text()
    assert 'ln -sf "$REPO_DIR/bin/sigmond-sdr-event"' in sh
    assert "/usr/local/sbin/sigmond-sdr-event" in sh
    assert "udev/91-sigmond-sdr-event.rules" in sh
    assert "/etc/udev/rules.d/91-sigmond-sdr-event.rules" in sh
    assert "systemd/radiod-sdr-event.conf" in sh
    assert "radiod@.service.d/50-sigmond-sdr-event.conf" in sh
```

- [ ] **Step 2: Run the tests; watch them fail.**

Run: `.venv/bin/pytest tests/test_sdr_event_hooks.py -q --override-ini addopts=`
Expected: FAIL. The writer tests fail with `can't open file '.../bin/sigmond-sdr-event'`; the rule and drop-in
tests with `FileNotFoundError`; the `smd` and install.sh tests on missing text.

- [ ] **Step 3: Write the writer.** Create `bin/sigmond-sdr-event`, then `chmod 755 bin/sigmond-sdr-event`:

```python
#!/usr/bin/env python3
"""sigmond-sdr-event -- note that this station lost its RX888, once per episode.

    sigmond-sdr-event usb-remove|usb-add <devpath>   (udev: 91-sigmond-sdr-event.rules)
    sigmond-sdr-event radiod-exit <instance>         (radiod's 50-sigmond-sdr-event.conf)

sigmond-sdr-recover notes its power cuts, and sigmond-radiod-watchdog its
restarts, in-process, through the same library.
The record: /var/lib/sigmond/sdr-events.jsonl, one line per episode
(lib/sigmond/sdr_events.py).  `smd doctor` reads it.

This program never fails its caller.  udev waits for it, and radiod's drop-in
runs it after every exit, so it exits 0 whatever happens and says what went
wrong in one line.  Only an unknown kind exits 2, since only a person types
one.  A radiod exit counts only when systemd's $SERVICE_RESULT says radiod
died on its own; a stop someone asked for writes nothing.

SIGMOND_SDR_EVENTS, when set, names another file and sends the one line to
stderr instead of syslog; the tests use it.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))
from sigmond import sdr_events  # noqa: E402


def _say(msg: str, to_syslog: bool) -> None:
    if not to_syslog:
        print(f"sigmond-sdr-event: {msg}", file=sys.stderr, flush=True)
        return
    try:
        import syslog
        syslog.openlog("sigmond-sdr-event")
        syslog.syslog(syslog.LOG_WARNING, msg)
    except Exception:  # noqa: BLE001 -- the record matters, the line does not
        pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in sdr_events.KINDS:
        print(f"usage: sigmond-sdr-event {{{'|'.join(sdr_events.KINDS)}}} [detail]",
              file=sys.stderr)
        return 2
    kind, detail = argv[0], " ".join(argv[1:])
    override = os.environ.get("SIGMOND_SDR_EVENTS")
    path = Path(override) if override else sdr_events.EVENTS_PATH
    to_syslog = not override
    if kind == "radiod-exit":
        result = os.environ.get("SERVICE_RESULT", "")
        if not sdr_events.radiod_exit_is_loss(result):
            return 0
        detail = (f"{detail} {result} {os.environ.get('EXIT_CODE', '?')}/"
                  f"{os.environ.get('EXIT_STATUS', '?')}").strip()
    try:
        what = sdr_events.record(kind, detail, now=time.time(), path=path)
    except Exception as exc:  # noqa: BLE001 -- never fail udev or radiod
        _say(f"could not record {kind}: {exc}", to_syslog)
        return 0
    if what == "opened":
        _say(f"RX888 lost ({kind}: {detail}); one new episode in {path}", to_syslog)
    elif what == "back":
        _say(f"RX888 back on the bus ({detail})", to_syslog)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Write the udev rule.** Create `udev/91-sigmond-sdr-event.rules`:

```
# The RX888 left the USB bus, or came back.  sigmond-sdr-event keeps one line
# per episode in /var/lib/sigmond/sdr-events.jsonl, and `smd doctor` reads it
# (sdr-lost, sdr-down-long, sdr-flapping, sdr-recovered).
#
# Only 04b4:00f1, the card with its firmware loaded.  The FX3 bootloader
# (00f3) leaves the bus at every firmware load, at every boot; counting it
# would raise an alarm per boot.  A radiod restart does not re-enumerate the
# card, so a healthy station stays quiet here.
#
# On remove, udev holds no ATTR{} any more.  The kernel's PRODUCT (vendor,
# product and bcd in hex, no leading zeros) still names the card.  Both
# rules match it, so they cannot disagree about which device they mean.
# udev trusts no PATH and discards the program's output: full paths, and the
# writer speaks to syslog.
ACTION=="remove", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ENV{PRODUCT}=="4b4/f1/*", RUN+="/usr/bin/python3 /usr/local/sbin/sigmond-sdr-event usb-remove %k"
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ENV{PRODUCT}=="4b4/f1/*", RUN+="/usr/bin/python3 /usr/local/sbin/sigmond-sdr-event usb-add %k"
```

- [ ] **Step 5: Write radiod's drop-in.** Create `systemd/radiod-sdr-event.conf`:

```ini
# sigmond drop-in for radiod@.service -- note an exit nobody asked for.
#
# A stream stall takes the RX888 from the station with the card still on the
# bus: radiod logs "No rx888 data for 5 seconds, quitting", dies on a libusb
# assertion, and systemd restarts it 10 s later.  AC0G-ND did that three
# times between 2026-10-10 10:13Z and 2026-10-11 08:49Z, and nothing but the
# journal knew.  sigmond-sdr-event writes one line per episode to
# /var/lib/sigmond/sdr-events.jsonl, and only when $SERVICE_RESULT says radiod
# died on its own: radiod answers a stop with EX_OK, which systemd reads as
# success.
#
# '+' runs the writer with full privileges: radiod runs as User=radio, which
# cannot write /var/lib/sigmond.  '-' keeps a failed write from marking radiod
# failed.
[Service]
ExecStopPost=-+/usr/local/sbin/sigmond-sdr-event radiod-exit %i
```

- [ ] **Step 6: Deliver the drop-in with radiod's set.** In `bin/smd`, in `_RADIOD_DROPINS`, after the line
  `    ('sigmond-radiod-consumers@.service', 'sigmond-radiod-consumers@.service'),` add:

```python
    # A radiod exit nobody asked for leaves one line per episode in
    # sdr-events.jsonl (sigmond-sdr-event; smd doctor's sdr family).
    ('radiod-sdr-event.conf',
     'radiod@.service.d/50-sigmond-sdr-event.conf'),
```

  In `tests/test_radiod_dropins.py`, append `'radiod@.service.d/50-sigmond-sdr-event.conf',` as the last entry of
  `_DROPIN_RELPATHS`, and `'radiod-sdr-event.conf',` as the last entry of **both** `self.src_names` lists.

- [ ] **Step 7: Install all three.** In `install.sh`:

  (a) Replace:

```bash
$SUDO chmod a+x "$REPO_DIR/bin/sigmond-sdr-recover" "$REPO_DIR/bin/sigmond-radiod-ready"
$SUDO ln -sf "$REPO_DIR/bin/sigmond-sdr-recover"  /usr/local/sbin/sigmond-sdr-recover
```

  with:

```bash
$SUDO chmod a+x "$REPO_DIR/bin/sigmond-sdr-recover" "$REPO_DIR/bin/sigmond-radiod-ready" \
    "$REPO_DIR/bin/sigmond-sdr-event"
$SUDO ln -sf "$REPO_DIR/bin/sigmond-sdr-recover"  /usr/local/sbin/sigmond-sdr-recover
# One line per RX888-loss episode, for udev and radiod's drop-in to call.
$SUDO ln -sf "$REPO_DIR/bin/sigmond-sdr-event"    /usr/local/sbin/sigmond-sdr-event
```

  (b) Replace:

```bash
$SUDO install -m 0644 "$REPO_DIR/udev/90-sigmond-sdr-arrival.rules" \
     /etc/udev/rules.d/90-sigmond-sdr-arrival.rules
$SUDO udevadm control --reload-rules 2>/dev/null || true
```

  with:

```bash
$SUDO install -m 0644 "$REPO_DIR/udev/90-sigmond-sdr-arrival.rules" \
     /etc/udev/rules.d/90-sigmond-sdr-arrival.rules
# The RX888 leaving or rejoining the bus: one line per episode in
# /var/lib/sigmond/sdr-events.jsonl, which smd doctor reads.
$SUDO install -m 0644 "$REPO_DIR/udev/91-sigmond-sdr-event.rules" \
     /etc/udev/rules.d/91-sigmond-sdr-event.rules
$SUDO udevadm control --reload-rules 2>/dev/null || true
```

  (c) In the radiod block, after:

```bash
    $SUDO install -m 0644 "$REPO_DIR/systemd/radiod-consumers.conf" \
        /etc/systemd/system/radiod@.service.d/45-sigmond-consumers.conf
```

  add:

```bash
    # Note a radiod exit nobody asked for (a stream stall, a card gone).
    $SUDO install -m 0644 "$REPO_DIR/systemd/radiod-sdr-event.conf" \
        /etc/systemd/system/radiod@.service.d/50-sigmond-sdr-event.conf
```

- [ ] **Step 8: Run the tests; watch them pass, and radiod's drop-in tests stay green.**

Run: `.venv/bin/pytest tests/test_sdr_event_hooks.py tests/test_radiod_dropins.py tests/test_bringup_no_sdr.py tests/test_sdr_events.py -q --override-ini addopts= && bash -n install.sh`
Expected: PASS; `bash -n` prints nothing.

- [ ] **Step 9: Watch each guard fail (mutations, on a copy).**
  1. In the writer, delete the `if not sdr_events.radiod_exit_is_loss(result): return 0` guard →
     `test_an_exit_someone_asked_for_writes_nothing`, `test_a_slow_stop_writes_nothing` and
     `test_a_run_by_hand_outside_systemd_writes_nothing` turn red.
  2. Change the writer's `except` branch to `return 1` → `test_a_record_it_cannot_write_still_exits_0` turns red.
  3. Speak on every write, not only on `"opened"` → `test_the_opening_line_speaks_once_per_episode` turns red.
  4. Change the remove rule to `ENV{PRODUCT}=="4b4/f3/*"` → `test_the_bootloader_never_counts` and
     `test_a_removal_matches_the_running_card_by_the_kernels_product` turn red.
  5. Change the remove rule to match `ATTR{idProduct}=="00f1"` → the same test turns red.
  6. Drop the `+` from `ExecStopPost=-+` → `test_the_dropin_runs_the_writer_as_root_and_never_fails_radiod` turns
     red.
  7. Remove the pair from `_RADIOD_DROPINS` → `test_doctor_fix_and_the_native_install_deliver_the_dropin` turns
     red.
  8. Remove install.sh hunk (b) → `test_install_sh_lays_down_the_writer_the_rule_and_the_dropin` turns red.

- [ ] **Step 10: Commit.**

```bash
git add bin/sigmond-sdr-event udev/91-sigmond-sdr-event.rules systemd/radiod-sdr-event.conf \
    bin/smd install.sh tests/test_sdr_event_hooks.py tests/test_radiod_dropins.py
git commit -m "sdr: udev and radiod's unasked exits note each RX888 loss, once per episode"
```

---

### Task 6: sdr-recover notes its power cuts and says "holding" once per change

**Files:**
- Modify: `/home/mjh/hamsci/repos/sigmond/bin/sigmond-sdr-recover` (import ~60; after `save_state` ~355; in
  `main` ~695-765)
- Modify: `/home/mjh/hamsci/repos/sigmond/tests/test_sdr_recover.py` (imports at top; one class appended)

**Interfaces:**
- Consumes: `sigmond.sdr_events.record` (Task 4).
- Produces: in `sigmond-sdr-recover`, `note_event(kind: str, detail: str) -> None` and
  `log_on_change(state: dict, msg: str) -> None`; the state file gains the key `said`.

**What you need to know:**
- The timer runs this helper every 30 s. While the card stays away it logged `holding: <reason>` on every tick:
  two lines a minute for hours, the flood that buries the line that mattered. Two other lines for an absent card
  ran per tick too: "no hub/port to cycle ... cannot recover" and "card absent but hub ... cannot switch power".
  This task quiets all three; Michael confirmed all three on 2026-10-11. A dry run still answers every time,
  since a person asked.
- The reasons carry changing numbers ("absent 45s", "cycled 300s ago"), so `log_on_change` compares them with
  the digits blanked. The key lives in the state file, which persists between ticks. The card's return and each
  power cut clear it, so the next "holding" counts as news.
- `note_event` runs just before the cut, after the dry-run return. The `--ensure-present` gate cuts power too,
  so a fresh install whose card latched records one episode, which reads `sdr-recovered` for a day. That holds
  true and stays as designed.

- [ ] **Step 1: Write the failing tests.** In `tests/test_sdr_recover.py`, add to the imports at the top:

```python
import contextlib
import json
import tempfile
from unittest import mock
```

  and append at the end of the file:

```python
class EpisodeAndQuietTests(unittest.TestCase):
    """v3.71.  A power cut leaves one event for `smd doctor` to read, and a
    card that stays away says "holding" once per change.  This helper runs
    every 30 s, and the old line on every tick made two lines a minute for
    hours."""

    CFG = {"recover_hub": "1-1", "recover_port": "2",
           "unit": "radiod@ac0g.service", "consumers": []}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.log, self.events = [], []
        self.present, self.returns = False, True
        self.t = 1_760_000_000.0
        self._saved = {}
        stand_ins = {
            "STATE_PATH": self.tmp / "sdr-recover.json",
            "read_config": lambda: dict(self.CFG),
            "device_present": lambda: self.present,
            "bootstrap_locations": lambda: [],
            "cycle_port": lambda targets, off: True,
            "wait_for_device": lambda timeout_s=90: self.returns,
            "installed_units": lambda: [],
            "restart_station": lambda unit, consumers, **kw: True,
            "_with_lifecycle_lock": contextlib.nullcontext,
            "_log": self.log.append,
            "note_event": lambda kind, detail: self.events.append((kind, detail)),
        }
        for name, value in stand_ins.items():
            self._saved[name] = getattr(sdr, name)
            setattr(sdr, name, value)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(sdr, name, value)

    def tick(self, *argv, dt=30):
        with mock.patch.object(sdr.time, "time", return_value=self.t):
            rc = sdr.main(list(argv))
        self.t += dt
        return rc

    def holding(self):
        return [m for m in self.log if m.startswith("holding:")]

    def test_a_power_cut_leaves_one_event_naming_the_port(self):
        self.tick()            # absent: the grace window opens
        self.tick()            # 30 s later: cut
        self.assertEqual(self.events, [("power-cycle", "1-1p2")])

    def test_a_dry_run_records_nothing(self):
        for _ in range(3):
            self.tick("--dry-run")
        self.assertEqual(self.events, [])

    def test_a_present_card_records_nothing_and_says_nothing(self):
        self.present = True
        for _ in range(5):
            self.tick()
        self.assertEqual(self.events, [])
        self.assertEqual(self.holding(), [])

    def test_a_card_that_stays_away_says_holding_once_per_change(self):
        self.returns = False
        for _ in range(22):    # 11 minutes: the grace window, one cut, then cooldown
            self.tick()
        self.assertEqual(len(self.events), 1)
        h = self.holding()
        self.assertEqual(len(h), 2, h)
        self.assertIn("grace", h[0])
        self.assertIn("cooldown", h[1])

    def test_holding_speaks_again_after_the_card_came_back_and_left(self):
        self.tick()            # absent, grace: holding
        self.present = True
        self.tick()            # back
        self.present = False
        self.tick()            # absent again: holding again
        self.assertEqual(len(self.holding()), 2)

    def test_a_dry_run_still_answers_every_time(self):
        for _ in range(3):
            self.tick("--dry-run", dt=5)
        self.assertEqual(len(self.holding()), 3)

    def test_an_unswitchable_hub_says_so_once(self):
        sdr.read_config = lambda: {"unit": "radiod@ac0g.service", "consumers": []}
        (self.tmp / "sdr-recover.json").write_text(json.dumps(
            {"hub": "1-1", "port": "2", "switchable": False,
             "locations": [["1-1", "2", False]]}))
        for _ in range(5):
            self.tick()
        said = [m for m in self.log if "cannot switch power" in m]
        self.assertEqual(len(said), 1, self.log)

    def test_a_record_that_fails_never_fails_the_run(self):
        sdr.note_event = self._saved["note_event"]     # the real one
        with mock.patch.object(sdr.sdr_events, "record", side_effect=OSError("read-only")):
            self.tick()
            rc = self.tick()
        self.assertEqual(rc, 0)
        self.assertTrue(any("could not record the power-cycle" in m for m in self.log), self.log)
```

- [ ] **Step 2: Run the tests; watch them fail.**

Run: `.venv/bin/pytest tests/test_sdr_recover.py -q --override-ini addopts=`
Expected: FAIL. `setUp` raises `AttributeError: module 'sdr_recover_under_test' has no attribute 'note_event'`
for every test in the new class; the older classes pass.

- [ ] **Step 3: Import the library.** In `bin/sigmond-sdr-recover`, after
  `from sigmond.paths import LIFECYCLE_LOCK  # noqa: E402`, add:

```python
from sigmond import sdr_events  # noqa: E402
```

- [ ] **Step 4: Add the two helpers.** Directly after the end of `save_state` (the line
  `        _log(f"could not persist state: {exc}")`), insert:

```python


def note_event(kind: str, detail: str) -> None:
    """One line per episode in /var/lib/sigmond/sdr-events.jsonl, which
    `smd doctor` reads.  Bookkeeping never fails a recovery."""
    try:
        sdr_events.record(kind, detail, now=time.time())
    except Exception as exc:            # noqa: BLE001
        _log(f"could not record the {kind}: {exc}")


def log_on_change(state: dict, msg: str) -> None:
    """Log `msg` only when it says something new.  The timer runs this helper
    every 30 s, and a card that stays away used to log "holding: ..." on
    every tick.  Numbers carry no news ("absent 45s" and "absent 75s" say the
    same thing), so the comparison blanks them.  The key lives in the state
    file; the card's return and each power cut clear it."""
    key = re.sub(r"\d+", "#", msg)
    if state.get("said") != key:
        _log(msg)
        state["said"] = key
```

- [ ] **Step 5: Quiet the three per-tick lines, and note the cut.** In `main`:

  (a) Replace:

```python
    if not (hub and port):
        save_state(state)
        _log("no hub/port to cycle: nothing in [radiod] and the card was never "
             "seen on a bus this helper could inspect — "
             + ("it is absent right now; cannot recover" if not present
                else "unexpected while present"))
        return 0
```

  with:

```python
    if not (hub and port):
        log_on_change(state, "no hub/port to cycle: nothing in [radiod] and the "
                      "card was never seen on a bus this helper could inspect — "
                      + ("it is absent right now; cannot recover" if not present
                         else "unexpected while present"))
        save_state(state)
        return 0
```

  (b) Replace:

```python
    if state.get("switchable") is False and not cfg.get("recover_hub"):
        save_state(state)
        if not present:
            _log(f"card absent but hub {hub} port {port} cannot switch power — "
                 f"a manual replug is the only recovery")
        return 0
```

  with:

```python
    if state.get("switchable") is False and not cfg.get("recover_hub"):
        if not present:
            log_on_change(state, f"card absent but hub {hub} port {port} cannot "
                                 f"switch power — a manual replug is the only recovery")
        else:
            state.pop("said", None)
        save_state(state)
        return 0
```

  (c) Replace:

```python
    if present:
        if state.get("absent_since"):
            _log("device returned to the bus")
        state["absent_since"] = None
```

  with:

```python
    if present:
        if state.get("absent_since"):
            _log("device returned to the bus")
        state["absent_since"] = None
        state.pop("said", None)
```

  (d) Replace:

```python
    if not decision.cycle:
        save_state(state)
        # Report when the card is missing, and always under --dry-run: someone
        # who asks what this would do deserves an answer even when the answer
        # is "nothing".  Silence reads as a broken tool.
        if not present or args.dry_run:
            _log(f"holding: {decision.reason}")
        return 0
```

  with:

```python
    if not decision.cycle:
        # Report when the card is missing, and always under --dry-run: someone
        # who asks what this would do deserves an answer even when the answer
        # is "nothing".  Silence reads as a broken tool.  The timer's own runs
        # say it once per change, never once per 30 s tick.
        if args.dry_run:
            _log(f"holding: {decision.reason}")
        elif not present:
            log_on_change(state, f"holding: {decision.reason}")
        save_state(state)
        return 0
```

  (e) Replace:

```python
    state["last_cycle_at"] = now
    save_state(state)
```

  with:

```python
    state["last_cycle_at"] = now
    state.pop("said", None)     # after a cut, the next "holding" carries news
    save_state(state)
```

  (f) Replace:

```python
    if not cycle_port(targets, args.off_seconds):
```

  with:

```python
    note_event("power-cycle", " ".join(f"{h}p{p}" for h, p, _ in targets))
    if not cycle_port(targets, args.off_seconds):
```

- [ ] **Step 6: Run the tests; watch them pass.**

Run: `.venv/bin/pytest tests/test_sdr_recover.py tests/test_sdr_events.py -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 7: Watch each guard fail (mutations, on a copy).**
  1. Delete the `note_event("power-cycle", ...)` line → `test_a_power_cut_leaves_one_event_naming_the_port` turns
     red.
  2. Move that line to just before `if args.dry_run:` (after `_log(decision.reason)`), with `targets` replaced by
     `locations` → `test_a_dry_run_records_nothing` turns red (the second dry-run tick decides to cut, and records).
  3. In (d), restore `if not present or args.dry_run: _log(...)` →
     `test_a_card_that_stays_away_says_holding_once_per_change` turns red.
  4. Delete `state.pop("said", None)` in (c) → `test_holding_speaks_again_after_the_card_came_back_and_left` turns
     red.
  5. In (b), restore the plain `_log` → `test_an_unswitchable_hub_says_so_once` turns red.
  6. Remove the `try`/`except` in `note_event` → `test_a_record_that_fails_never_fails_the_run` turns red.
  7. In `log_on_change`, compare `msg` itself, not the blanked key →
     `test_a_card_that_stays_away_says_holding_once_per_change` turns red.

- [ ] **Step 8: Commit.**

```bash
git add bin/sigmond-sdr-recover tests/test_sdr_recover.py
git commit -m "sdr-recover: note each power cut for doctor, and say holding once per change, never per tick"
```

---

### Task 7: the radiod watchdog notes each restart it makes

**Files:**
- Modify: `/home/mjh/hamsci/repos/sigmond/bin/sigmond-radiod-watchdog` (after `STATE_DIR` at 59; after
  `_mark_restarted` at 119-121; in `_restart` at 163-165)
- Modify: `/home/mjh/hamsci/repos/sigmond/tests/test_radiod_watchdog.py` (imports 16-23; `setUp` 45-54 and
  `tearDown` 56-60 of `RestartAlwaysStarts`; one class appended)

**Interfaces:**
- Consumes: `sigmond.sdr_events.record` and the loss kind `radiod-wedge` (Task 4).
- Produces: in `sigmond-radiod-watchdog`, the module setting `SDR_EVENTS_PATH = None` and
  `_note_event(unit: str, reason: str, now: float) -> None`.

**What you need to know:**
- Michael decided on 2026-10-11 that the watchdog becomes the fourth writer. It restarts a radiod that said
  "No rx888 data ... quitting" yet stayed active (detector 1), or that logged nothing for `RWD_SILENCE_SEC`
  (detector 2). Each such restart marks a stall of the RX888. systemd reads the watchdog's `systemctl stop` as a
  stop someone asked for: radiod exits `EX_OK` and `SERVICE_RESULT` reads `success`, or `timeout` when a wedged
  radiod dies slowly. So radiod's own hook (Task 5) writes nothing for it, and the watchdog must write the event
  itself.
- The hook sits in `_restart`, after the dry-run return and `_mark_restarted`, before the stop. Only the two
  detectors in `check_unit` call `_restart`. `_honor_restart_request` restarts radiod through
  `sigmond.timing_judge.process_restart_request` and never calls `_restart`, so a restart that hf-timestd asked
  for records nothing: it marks a timing fault, not a lost RX888.
- The event lands before the stop. The episode then opens with its cause, and the `radiod-exit` events of a
  radiod that fails its next starts join it.
- The existing rules stay. The cooldown (`RWD_COOLDOWN_SEC`, 600 s) and the warm-up (`RWD_WARMUP_SEC`, 300 s)
  already gate `_restart`, so the watchdog records at most one event per restart, never one per 2-minute tick.
  `_restart` already prints one line per restart; the hook adds none. It speaks once, on stderr, only when the
  record fails.
- The watchdog runs as root (its service sets no `User=`), so it can write `/var/lib/sigmond`. It reaches its
  library the way `_honor_restart_request` does: `/usr/local/bin/sigmond-radiod-watchdog` links into the repo,
  and `lib/` sits beside `bin/` once the link resolves. The import stays lazy, inside `_note_event`, so a tick
  that restarts nothing pays no import.
- The episode opens when the watchdog acts, not when the stall began. A quitting radiod waits out
  `RWD_GRACE_SEC` (120 s) plus up to one tick; a silent one waits at least `RWD_SILENCE_SEC` (600 s). So
  `episode_span` (Task 4) undercounts a wedge by that much. "What this plan could not verify" names the cost.
- `SDR_EVENTS_PATH` exists only so the tests can point the record at a scratch file, the way
  `SIGMOND_SDR_EVENTS` serves the writer's tests.

- [ ] **Step 1: Write the failing tests.** In `tests/test_radiod_watchdog.py`, replace the imports:

```python
import importlib.machinery
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
```

  with:

```python
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import sdr_events as ev  # noqa: E402
```

  In `RestartAlwaysStarts`, so its tests never write the station's real record, replace:

```python
        self._sleep = wd.time.sleep
        wd._reset_usb_devices = lambda: None
        wd._mark_restarted = lambda unit, now: None
        wd.time.sleep = lambda s: None
        wd.DRY_RUN = False

    def tearDown(self):
        wd._run = self._run
        wd._reset_usb_devices = self._reset
        wd._mark_restarted = self._mark
        wd.time.sleep = self._sleep
```

  with:

```python
        self._sleep = wd.time.sleep
        self._note = wd._note_event
        wd._reset_usb_devices = lambda: None
        wd._mark_restarted = lambda unit, now: None
        wd._note_event = lambda unit, reason, now: None
        wd.time.sleep = lambda s: None
        wd.DRY_RUN = False

    def tearDown(self):
        wd._run = self._run
        wd._reset_usb_devices = self._reset
        wd._mark_restarted = self._mark
        wd._note_event = self._note
        wd.time.sleep = self._sleep
```

  Then insert this class directly above the closing `if __name__ == "__main__":`:

```python
T0 = 1_760_000_000.0
UNIT = "radiod@ac0g.service"


class RestartNotesTheLoss(unittest.TestCase):
    """v3.71.  Each restart the watchdog makes leaves one event for `smd
    doctor`.  systemd reads the watchdog's stop as asked for, so radiod's own
    ExecStopPost= hook writes nothing; without this, a wedge that the
    watchdog cleared would leave no trace."""

    SAVED = ("_run", "_reset_usb_devices", "_mark_restarted", "_cooldown_ok",
             "_unit_prop", "_epoch", "_journal_since_invocation",
             "SDR_EVENTS_PATH", "DRY_RUN")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.events = self.tmp / "sdr-events.jsonl"
        self.calls = []
        self._saved = {n: getattr(wd, n) for n in self.SAVED}
        self._sleep = wd.time.sleep

        def fake(cmd, timeout=60):
            self.calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        wd._run = fake
        wd._reset_usb_devices = lambda: None
        wd._mark_restarted = lambda unit, now: None
        wd.time.sleep = lambda s: None
        wd.SDR_EVENTS_PATH = self.events
        wd.DRY_RUN = False

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(wd, n, v)
        wd.time.sleep = self._sleep

    def lines(self):
        return [json.loads(l) for l in self.events.read_text().splitlines()]

    def verbs(self):
        return [c[1] for c in self.calls if c and c[0] == "systemctl"]

    def a_unit_an_hour_old(self, journal):
        wd._unit_prop = lambda unit, prop: {"InvocationID": "abc",
                                            "ActiveEnterTimestamp": "x"}[prop]
        wd._epoch = lambda ts: T0 - 3600
        wd._journal_since_invocation = lambda unit, inv: journal

    def test_a_restart_leaves_one_event_naming_the_unit_and_the_reason(self):
        wd._restart(UNIT, "no journal output for 700s (heartbeat lost)", T0)
        (d,) = self.lines()
        self.assertEqual(d["kinds"], ["radiod-wedge"])
        self.assertIn(UNIT, d["detail"])
        self.assertIn("heartbeat lost", d["detail"])

    def test_the_quitting_detector_records_through_restart(self):
        self.a_unit_an_hour_old([
            f"{T0 - 300:.6f} host radiod[1]: No rx888 data for 5 seconds, quitting",
            f"{T0 - 10:.6f} host radiod[1]: measured sample rate 64800000"])
        wd._cooldown_ok = lambda unit, now: True
        wd.check_unit(UNIT, T0)
        (d,) = self.lines()
        self.assertIn("quitting", d["detail"])
        self.assertEqual(self.verbs(), ["stop", "start"])

    def test_a_unit_in_cooldown_records_nothing(self):
        self.a_unit_an_hour_old([f"{T0 - 900:.6f} host radiod[1]: last words"])
        wd._cooldown_ok = lambda unit, now: False
        with contextlib.redirect_stdout(io.StringIO()):
            wd.check_unit(UNIT, T0)
        self.assertFalse(self.events.exists())
        self.assertEqual(self.verbs(), [])

    def test_the_event_lands_before_the_stop(self):
        seen = []

        def fake(cmd, timeout=60):
            seen.append((cmd[1], self.events.exists()))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        wd._run = fake
        wd._restart(UNIT, "wedged", T0)
        self.assertEqual(seen[0], ("stop", True))

    def test_a_dry_run_records_nothing(self):
        wd.DRY_RUN = True
        with contextlib.redirect_stdout(io.StringIO()):
            wd._restart(UNIT, "wedged", T0)
        self.assertFalse(self.events.exists())

    def test_a_restart_and_radiods_failing_starts_make_one_episode(self):
        wd._restart(UNIT, 'declared "no rx888 data, quitting" 300s ago', T0)
        ev.record("radiod-exit", "ac0g core-dump dumped/ABRT", now=T0 + 20, path=self.events)
        (d,) = self.lines()
        self.assertEqual(d["kinds"], ["radiod-exit", "radiod-wedge"])
        self.assertEqual(d["count"], 2)

    def test_a_record_that_fails_still_restarts(self):
        (self.tmp / "file").write_text("")
        wd.SDR_EVENTS_PATH = self.tmp / "file" / "sdr-events.jsonl"
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            wd._restart(UNIT, "wedged", T0)
        self.assertEqual(self.verbs(), ["stop", "start"])
        self.assertIn("could not record", err.getvalue())

    def test_a_restart_adds_no_line_of_its_own(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            wd._restart(UNIT, "wedged", T0)
        self.assertEqual(len(out.getvalue().splitlines()), 1, out.getvalue())
```

- [ ] **Step 2: Run the tests; watch them fail.**

Run: `.venv/bin/pytest tests/test_radiod_watchdog.py -q --override-ini addopts=`
Expected: FAIL, every test. The five older tests fail in `setUp` with `AttributeError: module
'radiod_watchdog_under_test' has no attribute '_note_event'`; the new class fails in `setUp` on
`'SDR_EVENTS_PATH'`. Both pass again after Step 4.

- [ ] **Step 3: Name the record's path.** In `bin/sigmond-radiod-watchdog`, directly after
  `STATE_DIR = Path('/run/sigmond-radiod-watchdog')`, insert:

```python

# The station's record of losing its RX888 (v3.71, lib/sigmond/sdr_events.py),
# which `smd doctor` reads.  None means the library's own path,
# /var/lib/sigmond/sdr-events.jsonl; the tests point it at a scratch file.
SDR_EVENTS_PATH = None
```

- [ ] **Step 4: Add the hook, and call it once per restart.** Directly after the end of `_mark_restarted` (the
  line `    (STATE_DIR / f'{unit}.last-restart').write_text(f'{now}\n')`), insert:

```python


def _note_event(unit: str, reason: str, now: float) -> None:
    """Note this restart in the station's record of RX888 losses (v3.71).

    A wedged radiod has lost its RX888 as surely as one that died.  But
    systemd reads the stop in _restart as one someone asked for, so radiod's
    own ExecStopPost= hook writes nothing.  The event merges into any open
    episode, as the other writers' events do.

    Bookkeeping never blocks the restart.  It adds no line of its own, since
    _restart already prints one; only a failed record speaks."""
    lib = Path(__file__).resolve().parent.parent / 'lib'
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    try:
        from sigmond import sdr_events
        kw = {} if SDR_EVENTS_PATH is None else {'path': Path(SDR_EVENTS_PATH)}
        sdr_events.record('radiod-wedge', f'{unit} {reason}', now=now, **kw)
    except Exception as exc:        # noqa: BLE001 -- never block the restart
        print(f'  {unit}: could not record the restart for smd doctor: {exc}',
              file=sys.stderr)
```

  In `_restart`, replace:

```python
    if DRY_RUN:
        return
    _mark_restarted(unit, now)
```

  with:

```python
    if DRY_RUN:
        return
    _mark_restarted(unit, now)
    # Note the loss before the stop: the episode opens with its cause, and
    # the exits of a radiod that fails its next starts join it.
    _note_event(unit, reason, now)
```

  In the module docstring, after the paragraph that ends `reports without acting.`, add:

```
Each restart either detector makes also leaves one event, radiod-wedge, in
/var/lib/sigmond/sdr-events.jsonl, which `smd doctor` reads (v3.71).  A
restart that hf-timestd requested leaves none: it marks a timing fault,
not a lost RX888.
```

- [ ] **Step 5: Run the tests; watch them pass.**

Run: `.venv/bin/pytest tests/test_radiod_watchdog.py tests/test_sdr_events.py tests/test_timing_judge.py -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 6: Watch each guard fail (mutations, on a copy).**
  1. Delete the `_note_event(unit, reason, now)` call →
     `test_a_restart_leaves_one_event_naming_the_unit_and_the_reason`,
     `test_the_quitting_detector_records_through_restart`, `test_the_event_lands_before_the_stop` and
     `test_a_restart_and_radiods_failing_starts_make_one_episode` turn red.
  2. Move the call above `if DRY_RUN:` → `test_a_dry_run_records_nothing` turns red.
  3. Move the call after `_run(['systemctl', 'start', unit])` → `test_the_event_lands_before_the_stop` turns red.
  4. Move the call to the top of `check_unit`, before the cooldown check, and out of `_restart` →
     `test_a_unit_in_cooldown_records_nothing` turns red.
  5. Remove the `try`/`except` in `_note_event` → `test_a_record_that_fails_still_restarts` turns red.
  6. Print `recorded radiod-wedge` after a successful `record` → `test_a_restart_adds_no_line_of_its_own` turns
     red.
  7. Write the kind `radiod-exit` instead of `radiod-wedge` →
     `test_a_restart_leaves_one_event_naming_the_unit_and_the_reason` turns red.

- [ ] **Step 7: Commit.**

```bash
git add bin/sigmond-radiod-watchdog tests/test_radiod_watchdog.py
git commit -m "radiod-watchdog: each restart of a wedged radiod notes the RX888 loss for doctor, once per restart"
```

---

### Task 8: smd doctor's sdr family, its exit code, and the heartbeat

**Files:**
- Modify: `/home/mjh/hamsci/repos/sigmond/lib/sigmond/doctor.py` (after `class Finding`, ~46)
- Modify: `/home/mjh/hamsci/repos/sigmond/bin/smd` (after `_qga_dropin_findings` from Task 1; `collect_findings`;
  `cmd_doctor`)
- Modify: `/home/mjh/hamsci/repos/sigmond/lib/sigmond/heartbeat.py` (imports ~43; `_map_doctor` ~466-501)
- Create: `/home/mjh/hamsci/repos/sigmond/tests/test_sdr_doctor.py`
- Modify: `/home/mjh/hamsci/repos/sigmond/tests/test_heartbeat.py` (tests appended)

**Interfaces:**
- Consumes: `sdr_events.EVENTS_PATH`, `SYSFS_USB`, `read_episodes`, `card_present`, `assess` (Task 4); the
  `qga-dropin` family (Task 1).
- Produces: `sigmond.doctor.SOFT_FINDING_KINDS = frozenset({"sdr-recovered"})` and
  `sigmond.doctor.hard_findings(findings) -> list`; in `bin/smd`, `_sdr_findings(*, path=None, sysfs=None,
  now=None) -> list[Finding]` (component `ka9q-radio`; kinds `sdr-lost`, `sdr-down-long`, `sdr-flapping`,
  `sdr-recovered`; never fixable).

**What you need to know:**
- `collect_findings` serves both `smd doctor` and the heartbeat (`smd admin heartbeat emit`, with
  `strict=False`). The family reads; it never consumes the record. The fleet board shows only the latest
  heartbeat, so a 24 h window keeps a loss visible for hours, where a take-once record would vanish five minutes
  later.
- Today any finding makes `smd doctor` exit 1, and the update rig parses doctor's kinds from lines
  `"    <kind>: <detail>"` (`doctor_kinds` in `test-update-v3.sh`). `summarise` still prints `sdr-recovered` in
  that shape; only the exit code changes. The extra `_info` line starts with five spaces, so the rig's
  four-space pattern never reads it as a kind.
- The heartbeat maps any finding to INVALID today. A block whose findings all carry soft kinds now reads
  INCONCLUSIVE, the twin of pm-heartbeat's `SOFT_FINDING_KINDS`. The board prints the block's reason and drops
  `data`, so the reason carries the sdr finding's detail ("lost 2 time(s) in 24 h, last at ...").
- The wire contract stays: no new block, verdict or field.
- Only `sdr-recovered` counts as soft. `sdr-down-long` (Task 4's 30-minute rule) fails doctor and reads INVALID on
  the heartbeat, like `sdr-lost` and `sdr-flapping`. So a single loss that recovered inside 30 minutes leaves the
  exit code at 0, and a longer one sets it to 1 for the 24 h the episode stays in the window.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_sdr_doctor.py`:

```python
"""smd doctor's sdr family (v3.71): did this station lose its RX888 in the
last 24 h, and does that fail doctor?

A loss that recovered within 30 minutes reports for 24 h and leaves the exit
code at 0: the station works, and a script that runs doctor must not read a
recovered stall as a broken host.  A card off the bus now, a loss that stayed
open longer than 30 minutes, or three losses in a day, fail it.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import sdr_events as ev  # noqa: E402
from sigmond.doctor import Finding  # noqa: E402


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader("smd_under_test_sdr_doctor", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_sdr_doctor", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()
NOW = 1_760_000_000.0


def usb_tree(root: Path, card: bool) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if card:
        d = root / "8-1.2"
        d.mkdir()
        (d / "idVendor").write_text("04b4\n")
        (d / "idProduct").write_text("00f1\n")
    return root


class SdrFamilyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = self.tmp / "sdr-events.jsonl"

    def find(self, card=True):
        return smd._sdr_findings(path=self.p, sysfs=usb_tree(self.tmp / "sys", card), now=NOW)

    def test_no_file_no_finding(self):
        self.assertEqual(self.find(), [])
        self.assertEqual(self.find(card=False), [])

    def test_one_stall_with_the_card_on_the_bus_reads_sdr_recovered(self):
        ev.record("radiod-exit", "ac0g core-dump dumped/ABRT", now=NOW - 3600, path=self.p)
        (f,) = self.find()
        self.assertEqual((f.component, f.kind, f.fixable), ("ka9q-radio", "sdr-recovered", False))
        self.assertIn("1 time(s) in 24 h", f.detail)

    def test_nds_three_stalls_in_a_day_read_sdr_flapping(self):
        for ago in (22.6 * 3600, 18 * 3600, 60):
            ev.record("radiod-exit", now=NOW - ago, path=self.p)
        (f,) = self.find()
        self.assertEqual(f.kind, "sdr-flapping")

    def test_the_card_off_the_bus_reads_sdr_lost(self):
        ev.record("usb-remove", "8-1.2", now=NOW - 120, path=self.p)
        (f,) = self.find(card=False)
        self.assertEqual(f.kind, "sdr-lost")

    def test_a_restart_loop_past_30_minutes_reads_sdr_down_long(self):
        for i in range(100):     # 100 radiod exits 20 s apart: one episode, 33 min
            ev.record("radiod-exit", now=NOW - 3600 + 20 * i, path=self.p)
        (f,) = self.find()
        self.assertEqual(f.kind, "sdr-down-long")
        self.assertIn("33 min", f.detail)

    def test_a_watchdog_restart_reads_like_any_other_loss(self):
        ev.record("radiod-wedge", "radiod@ac0g.service no journal output for 700s (heartbeat lost)",
                  now=NOW - 600, path=self.p)
        (f,) = self.find()
        self.assertEqual(f.kind, "sdr-recovered")
        self.assertIn("radiod-wedge", f.detail)

    def test_an_episode_older_than_24_h_makes_no_finding(self):
        ev.record("radiod-exit", now=NOW - 90_000, path=self.p)
        self.assertEqual(self.find(), [])

    def test_the_family_runs_inside_collect_findings(self):
        f = Finding("ka9q-radio", "sdr-recovered", "x")
        with mock.patch.object(smd, "_sdr_findings", return_value=[f]):
            _, findings = smd.collect_findings(base=tempfile.mkdtemp(), strict=False)
        self.assertIn(f, findings)


class ExitCodeTests(unittest.TestCase):
    SOFT = Finding("ka9q-radio", "sdr-recovered",
                   "RX888 lost 1 time(s) in 24 h, last at 2026-10-10 14:48Z (radiod-exit, 1 event(s)); recovered")

    def doctor(self, findings, clean=False):
        args = types.SimpleNamespace(base=tempfile.mkdtemp(), fix=False)
        out = io.StringIO()
        with mock.patch.object(smd, "collect_findings", return_value=(clean, findings)), \
             contextlib.redirect_stdout(out):
            rc = smd.cmd_doctor(args)
        return rc, out.getvalue()

    def test_a_recovered_rx888_reports_and_exits_0(self):
        rc, out = self.doctor([self.SOFT])
        self.assertEqual(rc, 0)
        self.assertIn("sdr-recovered", out)

    def test_beside_a_hard_finding_doctor_still_exits_1(self):
        rc, _ = self.doctor([self.SOFT, Finding("sigmond", "dirty", "1 modified file(s)")])
        self.assertEqual(rc, 1)

    def test_lost_flapping_and_down_long_exit_1(self):
        for kind in ("sdr-lost", "sdr-flapping", "sdr-down-long"):
            rc, _ = self.doctor([Finding("ka9q-radio", kind, "x")])
            self.assertEqual(rc, 1, kind)

    def test_a_clean_doctor_still_exits_0(self):
        rc, _ = self.doctor([], clean=True)
        self.assertEqual(rc, 0)

    def test_the_update_rigs_parser_still_reads_the_kind(self):
        _, out = self.doctor([self.SOFT])
        kinds = re.findall(r"^    ([A-Za-z][A-Za-z0-9_-]*):", out, re.M)
        self.assertEqual(kinds, ["sdr-recovered"])
```

  Append to `tests/test_heartbeat.py`:

```python
# ---------------------------------------------------------------------------
# v3.71: a lost RX888 on the doctor block
# ---------------------------------------------------------------------------

def test_doctor_with_only_sdr_recovered_is_inconclusive_and_says_how_often():
    env = assemble(NOW, CONFIG, rich_readers(doctor=lambda: (False, [
        FakeFinding("ka9q-radio", "sdr-recovered",
                    "RX888 lost 2 time(s) in 24 h, last at 2026-10-10 14:48Z "
                    "(radiod-exit, 1 event(s)); recovered")])))
    block = env["blocks"]["doctor"]
    assert block["verdict"] == "INCONCLUSIVE"
    assert "sdr-recovered" in block["reason"]
    assert "lost 2 time(s) in 24 h" in block["reason"]
    assert heartbeat_schema.validate(env) == []


def test_sdr_recovered_beside_any_other_finding_is_invalid():
    env = assemble(NOW, CONFIG, rich_readers(doctor=lambda: (False, [
        FakeFinding("ka9q-radio", "sdr-recovered", "lost 1 time(s)"),
        FakeFinding("sigmond", "dirty", "1 modified file"),
    ])))
    assert env["blocks"]["doctor"]["verdict"] == "INVALID"


@pytest.mark.parametrize("kind", ["sdr-lost", "sdr-flapping", "sdr-down-long"])
def test_a_lost_flapping_or_long_down_rx888_is_invalid(kind):
    env = assemble(NOW, CONFIG, rich_readers(doctor=lambda: (False, [
        FakeFinding("ka9q-radio", kind, "RX888 off the bus now")])))
    block = env["blocks"]["doctor"]
    assert block["verdict"] == "INVALID"
    assert kind in block["reason"] and "RX888 off the bus now" in block["reason"]


def test_an_sdr_detail_reaches_the_reason_printable_and_bounded():
    env = assemble(NOW, CONFIG, rich_readers(doctor=lambda: (False, [
        FakeFinding("ka9q-radio", "sdr-recovered", "\x1b[31m" + "é" * 1000)])))
    reason = env["blocks"]["doctor"]["reason"]
    assert reason.isascii() and reason.isprintable()
    assert len(reason) < 300


def test_the_wire_contract_stands():
    assert heartbeat_schema.KIND == "station_heartbeat"
    assert heartbeat_schema.SCHEMA_VERSION == 1
    assert heartbeat_schema.VERDICTS == ("VALID", "INVALID", "INCONCLUSIVE", "INDETERMINATE")
    assert heartbeat_schema.PRECEDENCE == {"VALID": 0, "INCONCLUSIVE": 1,
                                           "INDETERMINATE": 2, "INVALID": 3}


def test_only_sdr_recovered_counts_as_soft():
    from sigmond.doctor import SOFT_FINDING_KINDS
    assert SOFT_FINDING_KINDS == frozenset({"sdr-recovered"})
```

- [ ] **Step 2: Run the tests; watch them fail.**

Run: `.venv/bin/pytest tests/test_sdr_doctor.py tests/test_heartbeat.py -q --override-ini addopts=`
Expected: FAIL. `_sdr_findings` missing (`AttributeError`); `test_a_recovered_rx888_reports_and_exits_0` gets 1;
the heartbeat's INCONCLUSIVE test reads INVALID; `test_only_sdr_recovered_counts_as_soft` fails on the import.
`test_the_wire_contract_stands` passes already, and must keep passing.

- [ ] **Step 3: Name the soft kinds.** In `lib/sigmond/doctor.py`, directly after the `Finding` dataclass, add:

```python


#: Findings of these kinds report, but they fail nothing: `smd doctor` still
#: exits 0, and the heartbeat's doctor block reads INCONCLUSIVE when they
#: stand alone.  The station works again, and someone should know what it
#: took.  sdr-recovered: the RX888 went away and came back (sdr_events.py).
SOFT_FINDING_KINDS = frozenset({"sdr-recovered"})


def hard_findings(findings) -> list:
    """The findings that fail `smd doctor`: every one whose kind is not soft."""
    return [f for f in findings if getattr(f, "kind", None) not in SOFT_FINDING_KINDS]
```

- [ ] **Step 4: Add the family.** In `bin/smd`, directly after `_qga_dropin_findings` (Task 1), insert:

```python


def _sdr_findings(*, path=None, sysfs=None, now=None) -> list:
    """Did this station lose its RX888 in the last 24 h?  Read-only: it
    never consumes the record, since `smd doctor` and the heartbeat both
    read it.  No file, no finding: a host with no RX888, or one that never
    lost it, has nothing to say.  See lib/sigmond/sdr_events.py."""
    from sigmond import sdr_events
    from sigmond.doctor import Finding
    path = Path(path) if path is not None else sdr_events.EVENTS_PATH
    if not path.exists():
        return []
    now = time.time() if now is None else now
    episodes = sdr_events.read_episodes(path, now=now)
    if not episodes:
        return []
    present = sdr_events.card_present(
        Path(sysfs) if sysfs is not None else sdr_events.SYSFS_USB)
    verdict = sdr_events.assess(episodes, present)
    if verdict is None:
        return []
    kind, detail = verdict
    return [Finding('ka9q-radio', kind, detail)]
```

  In `collect_findings`, after the `_qga_dropin` inner function from Task 1, add:

```python
    def _sdr() -> None:
        # Did this station lose its RX888 in the last 24 h?  A stall, a
        # vanish, a power cut: each recovers by itself, and nothing else
        # says so.  sdr-recovered reports without failing doctor
        # (sigmond.doctor.SOFT_FINDING_KINDS).  See _sdr_findings.
        findings.extend(_sdr_findings())
```

  and after `_guarded('qga-dropin', _qga_dropin)` add:

```python
    _guarded('sdr-events', _sdr)
```

  In the docstrings of `collect_findings` and `cmd_doctor`, change "eleven" to "twelve", and in
  `collect_findings`'s first sentence add ", and whether the RX888 went away in the last 24 h" before the
  closing period.

- [ ] **Step 5: Let a recovered loss leave the exit code at 0.** In `cmd_doctor`, change the import line
  `from sigmond.doctor import component_checkouts, foreign_owned, summarise` to:

```python
    from sigmond.doctor import component_checkouts, foreign_owned, hard_findings, summarise
```

  and replace the function's last lines:

```python
    print(text)
    fixable = sum(1 for f in findings if f.fixable)
    if fixable and not getattr(args, 'fix', False):
        _info(f'{fixable} finding(s) repairable with: smd doctor --fix')
    return 1
```

  with:

```python
    print(text)
    fixable = sum(1 for f in findings if f.fixable)
    if fixable and not getattr(args, 'fix', False):
        _info(f'{fixable} finding(s) repairable with: smd doctor --fix')
    if not hard_findings(findings):
        # An RX888 that came back (sdr-recovered) reports for 24 h and fails
        # nothing: the station works, and a script that runs doctor must not
        # read a recovered stall as a broken host.
        _info('nothing above fails doctor: it reports what the station recovered from')
        return 0
    return 1
```

- [ ] **Step 6: Let the heartbeat read soft kinds as INCONCLUSIVE.** In `lib/sigmond/heartbeat.py`, after
  `from .heartbeat_schema import BLOCK_NAMES, KIND, PRECEDENCE, SCHEMA_VERSION`, add:

```python
from .doctor import SOFT_FINDING_KINDS
```

  Directly above `def _map_doctor(raw) -> dict:`, add:

```python
# The board prints a block's reason and drops its data, so an RX888 finding
# carries its detail into the reason: how often, and when, in UTC.
_SDR_REASON_MAX = 200


def _printable(text, limit: int) -> str:
    return "".join(c if " " <= c <= "~" else "?" for c in str(text or ""))[:limit]


```

  In `_map_doctor`, replace:

```python
    if findings:
        return _block("INVALID",
                      f"{len(findings)} doctor finding(s): {', '.join(kinds)}",
                      data)
```

  with:

```python
    if findings:
        # A block whose findings are all soft (an RX888 that came back) reads
        # INCONCLUSIVE: the station works, and someone should know what it
        # took.  Any other finding beside it keeps INVALID.
        verdict = ("INCONCLUSIVE" if all(k in SOFT_FINDING_KINDS for k in kinds)
                   else "INVALID")
        reason = f"{len(findings)} doctor finding(s): {', '.join(kinds)}"
        sdr = [f for f in findings if str(getattr(f, "kind", "")).startswith("sdr-")]
        if sdr:
            reason += f" ({_printable(getattr(sdr[0], 'detail', ''), _SDR_REASON_MAX)})"
        return _block(verdict, reason, data)
```

- [ ] **Step 7: Run the tests; watch them pass, and the whole suite stay green.**

Run: `.venv/bin/pytest tests/test_sdr_doctor.py tests/test_heartbeat.py tests/test_heartbeat_cli.py tests/test_smd_doctor_glue.py tests/test_qga_dropin.py -q --override-ini addopts=`
Expected: PASS.

Then the full suite: `.venv/bin/pytest -q --override-ini addopts=`
Expected: no failures. Compare the count with a run on the commit before Task 1; the difference should equal the
tests this plan added.

- [ ] **Step 8: Watch each guard fail (mutations, on a copy).**
  1. In `cmd_doctor`, drop the `if not hard_findings(findings):` block → `test_a_recovered_rx888_reports_and_exits_0`
     turns red.
  2. Change `hard_findings` to return `list(findings)` → the same test turns red.
  3. Add `"sdr-flapping"` to `SOFT_FINDING_KINDS` → `test_lost_flapping_and_down_long_exit_1`,
     `test_a_lost_flapping_or_long_down_rx888_is_invalid[sdr-flapping]` and
     `test_only_sdr_recovered_counts_as_soft` turn red.
  3a. Add `"sdr-down-long"` to `SOFT_FINDING_KINDS` → `test_lost_flapping_and_down_long_exit_1`,
     `test_a_lost_flapping_or_long_down_rx888_is_invalid[sdr-down-long]` and
     `test_only_sdr_recovered_counts_as_soft` turn red.
  3b. In `sdr_events.py` (a copy), set `LONG_EPISODE_S = 7200` →
     `test_a_restart_loop_past_30_minutes_reads_sdr_down_long` turns red. It reads `sdr-recovered`.
  4. In `_map_doctor`, change `all(` to `any(` → `test_sdr_recovered_beside_any_other_finding_is_invalid` turns red.
  5. Drop `_printable(...)` and append the raw detail → `test_an_sdr_detail_reaches_the_reason_printable_and_bounded`
     turns red.
  6. Remove `_guarded('sdr-events', _sdr)` → `test_the_family_runs_inside_collect_findings` turns red.
  7. In `_sdr_findings`, pass `True` to `assess` instead of `present` → `test_the_card_off_the_bus_reads_sdr_lost`
     turns red.
  8. Change the `_info` text to start with a word and a colon at four spaces (`print('    note: ...')`) →
     `test_the_update_rigs_parser_still_reads_the_kind` turns red.

- [ ] **Step 9: Commit.**

```bash
git add lib/sigmond/doctor.py lib/sigmond/heartbeat.py bin/smd tests/test_sdr_doctor.py tests/test_heartbeat.py
git commit -m "doctor: a lost RX888 reports for 24 h; one back within 30 minutes reads INCONCLUSIVE and fails nothing"
```

---

### Task 9: the words

**Files:**
- Modify: `/home/mjh/hamsci/repos/sigmond/docs/operator/troubleshooting.md` (triage table ~49-57, the
  waterfall row at 55; RX888 section 526, before its `**When to stop and ask**` at 587)
- Modify: `/home/mjh/hamsci/repos/sigmond/tasks/plan-sink-control-v370.md` (the carry list, item 3 at 132)

**Interfaces:**
- Consumes: the kinds and the file path from Tasks 4-8.
- Produces: nothing code reads.

**What you need to know:**
- The guest-agent plan's Task 5 edits both files. Re-read the anchors before you edit.
- Operator pages speak to a person at the station. Keep each sentence short; name who acts.

- [ ] **Step 1: Add one triage row.** In `docs/operator/troubleshooting.md`, in the "What you saw | Go to" table,
  after the row that begins `| The waterfall is blank or empty |`, add:

```markdown
| `smd doctor` names `sdr-lost`, `sdr-down-long`, `sdr-flapping` or `sdr-recovered` | [RX888 not found, or the waterfall is blank](#rx888-not-found-or-the-waterfall-is-blank) |
```

- [ ] **Step 2: Add the block.** In the section `### RX888 not found, or the waterfall is blank`, directly before
  `**When to stop and ask**`, insert:

````markdown
**When `smd doctor` names the RX888**

From v3.71 the station keeps a record each time it loses the RX888. The card
leaves the USB bus, `sigmond-sdr-recover` cuts its power, `radiod` dies on a
stalled stream with the card still on the bus, or the radiod watchdog restarts
a `radiod` that hung. `smd doctor` reads the last 24 h of that record, and the
fleet board shows the same finding.

| Finding | What happened | `smd doctor` exits |
|---|---|---|
| `sdr-recovered` | The RX888 went away once or twice, each time for 30 minutes or less, and came back by itself. | 0 |
| `sdr-down-long` | It went away for more than 30 minutes at a stretch. It may sit back on the bus now. | 1 |
| `sdr-flapping` | It went away three or more times in 24 h. It came back each time, but something keeps taking it. | 1 |
| `sdr-lost` | It sits off the bus right now. | 1 |

To read the record itself, `[VM]`:

```bash
cat /var/lib/sigmond/sdr-events.jsonl
```

Each line holds one episode: when it opened and when its last event came (UTC),
which kinds of event it held (`usb-remove`, `power-cycle`, `radiod-exit`,
`radiod-wedge`), how many, and when the card came back. An episode with
`radiod-exit` and no `usb-remove` means the stream stalled while the card
stayed on the bus. `radiod-wedge` means the watchdog found `radiod` alive but
stuck, and restarted it.

- `sdr-recovered` → nothing to do now. If it comes back day after day, treat it
  as `sdr-flapping`.
- `sdr-down-long` → the station went without its receiver for more than half an
  hour. Read the
  record to see which kind of event kept it down, then check the same things
  as for `sdr-flapping`.
- `sdr-flapping` → check the hub's own power supply, the USB cable and how it
  sits in the port. Then send the record with your request for help.
- `sdr-lost` → follow **What to do** above.
````

- [ ] **Step 3: Mark the carry items done.** In `tasks/plan-sink-control-v370.md`, after the carry list's item
  `3. Decide whether a killed child should take the agent with it. The cap exists for the agent's own buffer.`,
  add:

```markdown
  Items 1 and 3 landed in `tasks/plan-sdr-alarm-memcap-v371.md` (2026-10-11): the drop-in sets
  `OOMPolicy=continue`, so a killed child fails its own request and the agent keeps serving, and the wizard's five
  `smd` requests and the update rig's `smd update --apply` run in transient units. Item 2 stays open.
```

- [ ] **Step 4: Check the links.**

Run: `.venv/bin/pytest -q --override-ini addopts= -k "docs or conformance"` and, if the repository has it,
`.venv/bin/python scripts/docs-conformance.py`.
Expected: no new failure.

- [ ] **Step 5: Commit.**

```bash
git add docs/operator/troubleshooting.md tasks/plan-sink-control-v370.md
git commit -m "docs: what smd doctor's sdr findings mean at the station, and the v3.70 carry list closes two items"
```

---

## How each part reaches a station in the field

| Part | Route |
|---|---|
| The wizard's `gexec_unit` | pm-align, from the release, on the Proxmox host |
| The update rig | the appliance repository; no station runs it |
| `lib/sigmond/sdr_events.py`, `bin/smd`, `bin/sigmond-sdr-recover`, `bin/sigmond-sdr-event`, `lib/sigmond/heartbeat.py` | `smd update` (git) |
| `bin/sigmond-radiod-watchdog`'s hook | `smd update` (git): `/usr/local/bin/sigmond-radiod-watchdog` links into the repo, and the timer runs the new code at its next tick |
| The agent's drop-in | `install.sh` (which `smd update` re-runs, since this release changes `install.sh`), and `smd doctor --fix` afterwards |
| radiod's `50-sigmond-sdr-event.conf` | `install.sh`'s radiod block, `smd doctor --fix`, and the native radiod install |
| The udev rule, the writer's symlink | `install.sh` only |

A radiod instance picks up its new drop-in at its next start; nothing here restarts it.

## What this plan could not verify (rig checks)

Run these on the nested rig, and the hardware ones on AC0G-B4 (announce on the claude-bus first).

- **R1.** `OOMPolicy=continue` after `daemon-reload`, on a running agent: `systemctl show -p OOMPolicy
  qemu-guest-agent` reads `continue`; a request whose child allocates 200 MB fails with a signal, and `NRestarts`
  stays put. Then a request whose output passes qemu-ga's per-stream bound comes back `out-truncated`, and the
  agent still answers.
- **R2.** Through `qm guest exec`, `systemd-run --quiet --wait --pipe --collect --unit=x /bin/bash -lc 'exit 3'`
  returns 3, and a child killed by a signal returns non-zero. `smd update --apply` under the rig's new line
  prints the same runuser/PATH-free output it printed before.
- **R3.** A hub cycle (`uhubctl ... -a cycle`) of the RX888's port: `udevadm monitor --property` shows
  `PRODUCT=4b4/f1/<bcd>` on both remove and add; the record gains one episode with `usb-remove` and `back`, and
  nothing more.
- **R4.** `systemctl kill -s ABRT radiod@<name>` writes one `radiod-exit` episode whose detail ends
  `core-dump dumped/ABRT` (or `signal killed/ABRT`), the file stays mode 0644 and root-owned, and the next
  heartbeat shows the doctor block INCONCLUSIVE with `sdr-recovered` in its reason. `systemctl restart radiod@<name>`
  writes nothing.
- **R5.** In the nest (no RX888), `/var/lib/sigmond/sdr-events.jsonl` never appears across the whole run, so
  `e7-doctor-post` gains no new kind.
- **R6.** A fresh wizard run in the nest: each `sigmond-wizard-*` unit appears and goes, `personalize` finishes
  under its unit despite the new machine-id, and the agent's `MemoryPeak` stays under 64 MB.
- **R7.** qemu-ga 10.0's per-stream bound (research A1): read `GUEST_EXEC_MAX_OUTPUT` in the 10.0 source.
- **R8. The watchdog's hook, on AC0G-B4 only** (the nest runs no radiod, R5). This takes B4's receiver down for
  about 15 minutes, so announce it on the claude-bus and in `/etc/sigmond/CLAUDE-COORDINATION.md` first.
  `systemctl kill -s STOP radiod@<name>` freezes radiod, so its journal falls silent. After
  `RWD_SILENCE_SEC` (600 s) the watchdog's next tick restarts it. Expect: the watchdog's journal shows one
  "restarting" line and no other new line; `/var/lib/sigmond/sdr-events.jsonl` gains exactly one episode with
  kinds `["radiod-wedge"]` and a detail naming `heartbeat lost`; radiod's own hook adds nothing (systemd sends
  SIGCONT with the stop's SIGTERM, radiod exits `EX_OK`, and `SERVICE_RESULT` reads `success`; INFERRED, which
  this check confirms); `smd doctor` names `sdr-recovered` and exits 0. Through the next 10 minutes of cooldown,
  the record stays at one episode.
- **R9. The 30-minute rule, end to end, on any station with its RX888 on the bus** (B4: announce first). No rig
  can stage a real half-hour loss cheaply, so this check plants one line. Save the record if one exists, then
  write one episode with `opened` 40 minutes ago, `last` 5 minutes ago, `kinds` `["radiod-exit"]`, `count` 100,
  `back` null. `smd doctor` names `sdr-down-long` with `lost for 35 min` and exits 1; the next heartbeat shows
  the doctor block INVALID with `sdr-down-long` in its reason. Change `last` to 25 minutes after `opened`:
  doctor names `sdr-recovered` and exits 0. Restore the saved record. ND's history serves as the real case:
  rebuild its 2026-10-05 loop from its journal into a scratch file, and `assess` must read `sdr-down-long`.
- **A wedge's span starts late.** The watchdog records when it acts, not when radiod stalled: a quitting radiod
  waits out `RWD_GRACE_SEC` (120 s) plus up to one 2-minute tick, and a silent one at least `RWD_SILENCE_SEC`
  (600 s). So a wedge that kept the station dark for 35 minutes may record as a 20-minute episode and read
  `sdr-recovered`. This plan accepts that; a later release could pass the stall's own time to `record`.
- **Repeated silent wedges.** When a restart does not cure a silent wedge and radiod logs no exit in between,
  the watchdog restarts again only after its cooldown (600 s) and another `RWD_SILENCE_SEC` of silence after
  radiod's last line, so 10 minutes or more later. A gap past `EPISODE_GAP_S` (600 s) opens a new episode each
  time, and the third reads `sdr-flapping` (INVALID) rather than `sdr-down-long`. Both fail doctor. No test pins
  this; R8 run twice would show it.

## Decided by Michael (2026-10-11)

1. **An episode that stays open longer than 30 minutes reads INVALID.** It takes a new kind, `sdr-down-long`,
   rather than folding into `sdr-lost`: `sdr-lost` keeps its present-tense meaning (the card sits off the bus
   now), which an operator can check by hand, while a long stall leaves the card on the bus. At 30 minutes or
   less, a single recovered episode stays `sdr-recovered`, INCONCLUSIVE, and leaves doctor's exit code alone.
   Landed in: Task 4 (`LONG_EPISODE_S`, `episode_span`, `assess` and seven tests), Task 8 (the exit code, the
   heartbeat, two tests and two widened ones), Task 9 (the table and the advice), Global Constraints, Review
   Focus 6, rig check R9.
2. **`sigmond-radiod-watchdog` becomes the fourth writer.** When it restarts a wedged radiod (a USB-stall zombie,
   or a silent one) it records a `radiod-wedge` event through the same library, merged into the open episode.
   It keeps its cooldown and warm-up, and adds no log line of its own. Landed in: new Task 7; Task 4
   (`radiod-wedge` in `LOSS_KINDS`); Task 9 (the words); Global Constraints, Review Focus 2 and 7; rig check R8.
3. **sdr-recover's three per-tick lines become once-per-change**, as planned. Landed in: Task 6, unchanged.
4. **Research A1 stays open**: did the 2026-08-24 wedge on B4 take memory at all? It changes nothing in this
   plan. The drop-in's header (Task 1) says the question stands open; R7 answers half of it.
