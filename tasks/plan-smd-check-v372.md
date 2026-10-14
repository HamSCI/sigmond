# One command that says whether a station works (v3.72 #14) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An installer at the station's console types `sigmond-check` and reads, in about fifteen seconds, one line
per part of the station and a verdict (PASS, WAIT or FAIL); inside the decoder VM, `smd check` gives the same lines
without the host's.

**Architecture:** A new library module, `sigmond/lib/sigmond/commands/check.py`, holds one small function per line.
Each function reads data that already exists and returns a `Line(mark, label, text)`.  Every probe of the outside
world (systemd, files, the journal, HTTP, the clock, the backlog) reaches the functions through an `Env`, so the
tests build a station in a temporary directory and inject the clock.  `bin/smd` registers `check` (read-only, no
`_need_root`).  On the Proxmox host a bash script, written by a new heredoc in `sigmond-wizard.sh` and carried by
`pm-align`, runs `sigmond-vm smd check --no-verdict`, adds the page lines through the host's own addresses, and prints
one verdict.  The appliance's install guide §9 shrinks to that one command.

**Tech Stack:** Python 3.11, standard library only; bash; pytest (unittest-style test classes); systemd and
journalctl as data sources; iptables, sysctl, ip and curl on the host.

**Spec:** `/home/mjh/hamsci/repos/sigmond/tasks/spec-smd-check-v372.md` (approved by Michael, 2026-10-14).  Where this
plan departs from the spec, it says so under "Decisions this plan made" and in the task.

**Status:** written 2026-10-14 against sigmond `2db0dc0`, sigmond-appliance `1759fc1`, hf-timestd, hs-uploader,
psk-recorder, meteor-scatter and mag-recorder as checked out on the devbox that day.

## How this plan carries its code

The planner built a scratch copy of sigmond and of sigmond-appliance and ran there every line of code and every test
in Tasks 1-9.  Each task's state ran on its own, in order: Task 1 alone, then Tasks 1-2, and so on to Task 7, with
that task's tests and every earlier task's tests passing at each step.  With all of Tasks 1-8 applied, the sigmond
full suite passed (4455 passed, 17 skipped, 171 subtests passed).  The planner also ran the mutation named in each task and watched the named test
fail; the results sit in each task's last step.  The appliance's `bash -n firstboot-v3.sh`,
`test-panel-remote.sh` (129 passed) and `test-panel-agent.sh` (58 passed) passed with Task 9's edits.  Task 10 did
not run: it needs AC0G-B4.

So treat each red run as a real check.  If a test fails for a reason its step does not name, stop and read before you
change anything.  If an anchor quoted in a step no longer matches the tree, find where it went, make the same edit by
hand, and say so in your report.

⚠ **Mutation checks and stale bytecode.**  The planner's first mutation pass reported two wrong results.  A mutant of
the same byte length as the original, written within the same second, left Python's `.pyc` in place, so the test ran
the old code.  Run every mutation with `PYTHONDONTWRITEBYTECODE=1` and clear `__pycache__` first; the mutation steps
below spell that out.

## Verified anchors (2026-10-14)

Each fact the tasks rely on, with the line the planner read.

- **Test runner.**  `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest <path> -q --override-ini addopts=`; never
  `uv run` (it rewrites `uv.lock`).  `tests/conftest.py:18-23` sets `SIGMOND_NO_VENV_REEXEC=1` and puts `lib/` first
  on `sys.path`.  `tests/__init__.py` makes `tests` a package, so a test imports a helper as
  `from tests.check_fakes import ...`.  `tests/test_align_cli.py:24-30` loads `bin/smd` with a `SourceFileLoader`
  under a module name unique to the file; Task 1 copies that loader.
- **Registration.**  `bin/smd` `main()` starts at 23657.  The `sink` parser sits at 25152-25168 and the `remote`
  parser starts at 25170; the `sink` dispatch branch at 25316 shows the shape (lazy import of the command module).
  `_need_root` (721) re-execs through sudo; a read-only verb never calls it.  The grouped help epilog lists
  `status` at 23711.  `tests/test_docs_cli_table.py:37-40` demands one row per top-level verb in
  `docs/contributor/orchestration.md`'s table; the `component` row sits at 141.
- **Library command shape.**  `lib/sigmond/commands/sink.py` `cmd_sink(args)` (23) and `_status()` (38) read
  `load_coordination(cfg.COORDINATION_PATH)` and print through `ui.py`, which writes to stderr (`ui.py:1-7`).  `smd
  check` prints its lines to stdout instead, so `sigmond-check` can capture them.
- **PSK.**  `cmd_psk_watch` (`bin/smd:20565`) reads `/var/log/psk-recorder/<radiod_id>-ft8.log` and `-ft4.log`, one
  pair per receiver (20629-20660: every `*-ft8.log`, so several radiod instances give several pairs).
  `_psk_watch_cycle_loop` (17188) matches a spot with `spot_re` (17307): `^\d{4}/\d{2}/\d{2}\s+(\d{2}:\d{2}:\d{2})\s`.
  The writer is decode_ft8, `"%4d/%02d/%02d %02d:%02d:%02d %3d %+4.2lf %'.1lf ~ %s"`, stamped in UTC
  (psk-recorder `src/psk_recorder/core/ch_tailer.py:16-17, 108-117`); the same log can carry jt9 lines
  (`ch_tailer.py:58-59`: `YYMMDD HHMMSS BAND_HZ`).  `_file_size` (17251-17263) falls back to `sudo -n stat`; a restart
  recreates the log (17269-17277).  No logrotate rule touches `/var/log/psk-recorder`.
- **WSPR.**  `cmd_wspr_watch` (18338) follows `journalctl -u 'wspr-recorder@*' -o cat --since '10 min ago'` through
  `sudo -n` when not root (18497, 18543-18547) and parses `cycle_re` (18432-18444): `cycle UTC HH:MM [rx=K] → N spots in
  wspr.spots[, M noise rows in wspr.noise] (B bands, write T ms)[ dt=… bands=[40:10 30:7 …]]`.
- **Meteor scatter.**  meteor-scatter `src/meteor_scatter/core/recorder.py:842-848` logs, every 60 s (860),
  `stats MSK144 rx=<id>: spots=N decodes=OK/ALL slots_empty=… (60s window)`; the unit is
  `meteor-scatter@.service`.
- **Magnetometer.**  `cmd_mag_watch` (`bin/smd:20847`) reads `/var/lib/mag-recorder/samples-<UTC date>.jsonl`.
  mag-recorder `core/supervisor.py:206-228` writes one JSON line per sample and rotates at UTC midnight;
  `_restamp` (257) sets `ts` to `2026-05-12T23:45:01.123Z`.  Keys: `ts`, `rt`, `x`, `y`, `z`, and `reporter_id` when
  set.  The device defaults to `/dev/ttyMAG0` (`config.py:132`).
- **GRAPE.**  hf-timestd names each raw-IQ directory after the channel's `description` (`SHARED_10000`,
  `WWV_20000`: `config/timestd-config.toml.template:110-133`; `paths.py:45-61` passes the canonical form through and
  turns spaces into underscores otherwise).  The day directory reads `YYYYMMDD`, not `YYYY-MM-DD`
  (`binary_archive_writer.py:864-870`).  `_expand_channel_groups` (`core_recorder_v2.py:7032-7059`) merges group
  defaults into each channel; `archive` defaults to `[recorder].archive`, then true (1496-1497).  In phase-engine mode
  (`[ka9q].source` or `[recorder].engine`, 362-368) a SHARED channel on 2.5, 5, 10 or 15 MHz becomes `WWV_`, `WWVH_`
  and `BPM_<kHz>` (1421-1433).  ⛔ With `tiered_storage = true` — the shipped template, line 88 — the writer puts each
  file first in `<hot_buffer_root>/raw_buffer/<channel>/<day>/` (default `/dev/shm/timestd`;
  `binary_archive_writer.py:233-236`, `tiered_storage.py:211-212`) and moves it to
  `<production_data_root>/raw_buffer/` minutes later.  Without tiered storage it writes to
  `<production_data_root>/<channel>/` (`binary_archive_writer.py:237-240`).  The config file is 0640
  timestd:timestd (`lib/sigmond/clients/hftimestd.py:102-118` reads it through `sudo -n cat`).
- **Sink switch.**  `smd sink off` stores `[uploads] mode = "discard"`; hold stays `"hold"`
  (`lib/sigmond/coordination.py:104-130`, `commands/sink.py:30`).  A fresh install writes
  `reason = "new station: the site sink switch stays off until someone runs smd sink upload"`
  (`scripts/proxmox/sigmond-wizard.sh:1745-1748`).  `load_coordination` (`coordination.py:223`) returns defaults
  (mode `upload`) when the file is absent.
- **Backlog.**  `heartbeat._read_backlog(paths)` (`lib/sigmond/heartbeat.py:887-915`) runs
  `python -m hs_uploader.backlog <db>` with `PYTHONPATH=/opt/git/sigmond/hs-uploader/src`; `WATERMARKS_DB` (60).
  `summarize()` (hs-uploader `backlog.py:60-155`) never raises: an unreadable store gives
  `{"readable": False, "reason": …}`.  It returns `pipelines[]` with `dead_letter_recent_count` (24 h) and
  `cursors[]` with `table_name` and `last_ack` (an ISO 8601 string with an offset: hs-uploader `core.py:462-468`).
  ⛔ The heartbeat ships through hs-uploader too (`uploader_manifest.py:250-285`, table `station.heartbeat`), every
  five minutes, whatever the switch says.
- **Client presence.**  `compute_state(name)` (`lib/sigmond/component_state.py:426`) costs a few `stat` calls and
  one `systemctl` call per declared unit (`_any_unit_active`, 200-226): well under a second for the five clients.  The
  catalog names: `psk-recorder`, `wspr-recorder`, `meteor-scatter`, `mag-recorder`, `hf-timestd`
  (`etc/catalog.toml`).
- **Service start time.**  `bin/smd` `_align_units_started_at` (4953-4993) reads `ActiveEnterTimestampMonotonic`
  (immune to a clock step) but lives in `bin/smd`; `commands/uploader.py:_service_started_at` (91-105) reads the
  wall-clock field for one unit.  Neither fits a unit glob, so Task 2 writes `service_state`.  hf-timestd's recorder
  unit: `timestd-core-recorder.service` (hf-timestd `deploy.toml:144-145`).
- **Web.**  `lib/sigmond/discovery/http_ka9q.py:15-16` injects `urlopen`.  Ports: 8081 ka9q-web, 8000 station-web,
  8082 gmag-webui (`gmag_webui.py:35-37`, which binds `0.0.0.0`).  The appliance measured all three answering on the
  VM's external address (`firstboot-v3.sh:2096-2101`).  ⚠ Unverified: that ka9q-web and station-web answer on
  `127.0.0.1` too.  Task 10, Step 4 shows it; if one binds only the external address, its VM line reads `FAIL` while
  the host's line passes, and the fix belongs in `page_check` (ask the VM's own address instead).
- **Host.**  `sigmond-wizard.sh:3284-3342` writes `/usr/local/bin/sigmond-vm`, which ends in `exec ssh … sigmond@"$IP"
  "$@"`, so the VM command's exit status comes back (255 when ssh itself fails; 1 when the VM is missing or stopped,
  3309-3316).  The wizard gives `sigmond` passwordless sudo in the VM (3197).  `pm-align.py` `extract_heredocs`
  (196-222) takes every top-level `cat > PATH <<'DELIM'`; `WIZARD_HEREDOCS` (245), `WIZARD_HEREDOCS_SINCE_V371` (252);
  `desired_files` (301-331) adds a v3.71 file only when the release's wizard writes it.
  `tests/test_pm_align.py:1528-1595` (the v3.71 block) shows the test style.  The appliance relays IPv4 with
  `iptables -t nat -A PREROUTING -p tcp --dport <p> -j DNAT --to-destination ${MGMT_VM}:<p>`
  (`firstboot-v3.sh:2042-2046`, 2105-2109; `MGMT_VM` defaults to 10.99.0.2, 757) and IPv6 through
  `sigmond-vm6proxy@<port>.socket` (`sigmond-vm6proxy:31`, `:37-63`).  `iptables -S` prints the rule as
  `-A PREROUTING -p tcp -m tcp --dport 8081 -j DNAT --to-destination 10.99.0.2:8081`.
- **Shell-test style.**  `tests/test_wizard_guest_senders.py` cuts functions out of the shipped wizard and runs them
  under stubs; Task 8's test cuts the heredoc out with `pm_align.extract_heredocs` and puts stub programs on `PATH`.
- **Docs.**  appliance `INSTALL.md` §8 sits at 235-262, §9 at 265-315, §11's "No spots after 30 minutes" row at 380,
  and §12's staging step at 400-403 points to §9's `smd watch wspr`; the panel's help line at
  `firstboot-v3.sh:3250`.  No appliance test pins those lines (`test-bless-instructions.sh` builds its own
  INSTALL.md fixtures).

## Decisions this plan made

1. **GRAPE looks in both storage tiers, and in `YYYYMMDD` day directories.**  Spec §4 names only
   `/var/lib/timestd/raw_buffer/<channel>/<UTC date>/`.  On every station built from the template, files land in
   `/dev/shm/timestd/raw_buffer/` first and reach `/var/lib` minutes later (K3LR's rehearsal found "tens of minutes";
   memory `project_k3lr_dasi018_gmag_20260930`).  A check that looked only on disk would fail a healthy station.  So
   the line takes the newest file of each channel across the hot root, the disk root and (tiered storage off)
   `<data_root>/<channel>`, in today's and yesterday's `YYYYMMDD` directories.  It builds the channel list from the
   config exactly as hf-timestd does: the `description`, the merged `archive` flag, and the phase-engine split.
2. **Commands in a line reach the VM.**  The spec's sample shows `Fix: sudo systemctl restart mag-recorder` in
   `sigmond-check`'s output; typed at the host's console, that restarts nothing (the host runs no mag-recorder).
   `smd check` writes every command after `Fix: ` or `run: `, and `sigmond-check` inserts `sigmond-vm ` after those
   words: `Fix: sigmond-vm sudo systemctl restart mag-recorder`.  The `sigmond` user has passwordless sudo in the VM
   (`sigmond-wizard.sh:3197`), so the command works over ssh without a terminal.  §9's sample shows the host form.
3. **The host prints the page lines; the VM's own are dropped.**  `smd check` prints `receiver page`, `station
   pages` and `magnetometer page` lines against `127.0.0.1`, as spec §4 asks.  `sigmond-check` removes them and
   prints its own through the host's addresses, as spec §2's sample shows; a `127.0.0.1` link means nothing to a
   person at another computer.  The magnetometer page line appears on the host exactly when the VM printed one.
4. **Uploads.**  The heartbeat's cursor does not count as a delivery: it ships every five minutes whatever else
   happens, so counting it would pass a station that ships no spot.  The plan adds one `wait` the spec lacks: the switch
   reads `upload`, no delivery yet, and `coordination.toml` changed less than 30 minutes ago.  Without it, the
   installer who runs `sigmond-check` right after `smd sink upload`, as §9 tells them, would read `FAIL`.
5. **The journal goes through `sudo -n` from the start** for a user outside `systemd-journal`, `adm` and `wheel`.
   journalctl shows such a user only their own messages and reports no error, so "try, then sudo" (spec §4.2) could
   never see the refusal.  Files keep "try, then sudo once".
6. **Service lines.**  A stopped or failed client gives `<client> is <state>.  Fix: smd start <client>`.  The `wait`
   windows count from the oldest active instance's `ActiveEnterTimestampMonotonic`, so a host clock step cannot open
   or close a window, and an unknown age never counts as young.
7. **Line order** follows spec §2's sample (meteor scatter after uploads), not §4's table.
8. **The CLI table.**  `docs/contributor/orchestration.md` gains a `check` row; `tests/test_docs_cli_table.py` fails
   without it.
9. **The panel line** drops `(VM console)` after `qm terminal $VMID` so the line, with `sigmond-check` added, stays
   under 128 columns (119).  INSTALL.md §8 also names `sigmond-check` beside the other host commands, and §12's
   staging step, which sent the reader to §9's old `smd watch wspr`, now names it too; the spec names only §9 and
   §11.
10. **Out of spec, left alone:** `docs/install-page.html` still describes the old §9 (its section 4).  The
    image-release rule (memory `feedback_update_install_page_each_image`) updates it with the next image.
11. **ASCII marks and ASCII output (Michael, 2026-10-14, measured).**  The Proxmox host's console font
    (`/etc/default/console-setup` CODESET=Lat15, Fixed 8x16) has no glyph for ✓ (U+2713) or ◌ (U+25CC), and shows ✗
    as a plain x.  The installer reads `sigmond-check` on exactly that console.  So the marks became `ok`, `wait`,
    `FAIL` and `--` in a fixed first column, a fix moved to its own line under the text, degrees print as `C`, and
    nothing either program prints leaves ASCII: `smd check` turns any other character in a line's text into `?`, and
    `sigmond-check` does the same to every line it takes from the VM or from `sigmond-vm`.  Task 1 and Task 8 each
    carry a test that fails on one character past 0x80.  The spec's sample and §2 table change with it (Michael
    amends the spec).

## Global Constraints

- Marks (Michael, 2026-10-14, replacing spec §2's symbols): `ok` works; `wait` not yet, normal for a new station,
  the line says how long to wait; `FAIL` broken, the line says what to do or whom to tell; `--` not on this station,
  nothing counts it.  Columns: a space, the word padded to 4, two spaces, the label padded to 15, the text.  A fix
  stands on its own line, indented to the text column (22 spaces), starting `Fix: `.
- Pure ASCII: nothing `smd check` or `sigmond-check` prints may carry a character at or past 0x80 (no `°`, no em
  dash, no arrow; degrees print as `C`).  The host console's font (Lat15, Fixed 8x16) shows none of them.  `--json`
  may carry the mark words as they are.
- "The verdict takes the worst line: any `FAIL` line gives `FAIL`, else any `wait` gives `WAIT`, else `PASS`.  The exit code
  follows it: 0 for PASS, 2 for WAIT, 1 for FAIL."
- "Neither program changes anything.  Neither calls the timing judge, `smd doctor`, or a watcher."  `smd check`
  "needs no root" and `bin/smd` "never calls `_need_root`" for it.
- "Each line reads data that already exists."  A client shows `--` unless `compute_state(name)` reports it installed.
- Windows, from spec §4: PSK 15 min (`wait` under 15 min); WSPR 10 min of journal (`wait` under 12 min); magnetometer newest
  sample under 10 s, last 60 samples, `wait` under 1 min; meteor scatter 5 min; GRAPE file under 11 min, `wait` under 12 min;
  storage 10 % free on `/var/lib`; uploads: a delivery in 30 min, no dead letter in 24 h; pages: GET, 3 s, status 200.
- Freeze, spec §4.1: "every one of the last 60 samples carries `rt == 0.0`, or … x, y and z each hold one value
  across all 60."  The fix reads `sudo systemctl restart mag-recorder`.
- Reading, spec §4.2: try as the user, then `sudo -n` once; if that fails, `FAIL … cannot read <path> as <user>`, never
  a pass.
- "A line that raises an exception becomes `FAIL` with the exception's text, never a crash and never a pass."
- Host, spec §5: IPv4 passes when "the DNAT rule for the port exists, `net.ipv4.ip_forward` reads 1, and a GET on
  the VM's address returns 200"; never a GET on the host's own IPv4.  IPv6: "a plain GET" on the host's global IPv6.
  IPv4 only: "`no IPv6 here` and counts as nothing".  IPv6 and no relay: "`FAIL … IPv6 http://[…]:8081 does not answer:
  the IPv6 relay is not installed`".
- Prose for INSTALL.md: short sentences, active voice, few forms of "to be", no bold inside sentences; one IPv4 and
  one IPv6 example (`http://192.168.1.50:8081`, `http://[2601:db8::50]:8081`).
- **Repositories and commits.**  Tasks 1-8 commit in `/home/mjh/hamsci/repos/sigmond`; Task 9 in
  `/home/mjh/hamsci/repos/sigmond-appliance`.  Work on `main`.  Commit by path, never `git add -A`.  Never push.
  End each commit message with the attribution lines your session's instructions give; the commit steps below show
  them as `<attribution lines>`.
- **Reach nothing outside this machine** until Task 10, and in Task 10 only after the claude-bus announcement.
- After the code tasks, run `graphify update /home/mjh/hamsci/repos` once (never `graphify update .`).

## Review Focus

1. **The UTC date rolls over at 00:00Z.**  At 00:00:30Z today's magnetometer file holds 30 samples and the other 30
   sit in yesterday's; at 00:03Z GRAPE's newest file sits in yesterday's `YYYYMMDD` directory; at 00:05Z the PSK
   window still covers 23:50Z.  A person expects the same verdict a minute before midnight and a minute after.
   Pinned: Task 4 `test_freeze_window_reaches_into_yesterdays_file`, Task 5 `test_files_from_before_midnight_still_count`,
   Task 3 `test_window_reaches_back_across_midnight`.
2. **The last JSONL line is half-written** when `smd check` reads it (mag-recorder writes once a second).  A person
   expects the check to skip that line, not to fail or crash.  Pinned: Task 4 `test_a_half_written_last_line_is_skipped`.
3. **A restart recreated the PSK log** a minute ago, so the file holds one minute of spots, or none.  A person
   expects `wait` while the recorder stays young, and `ok` as soon as a spot lands.  Pinned: Task 3
   `test_restart_recreated_the_log`.
4. **The `sigmond` user cannot read the journal**, and `sudo -n` refuses.  A person expects a `FAIL` that says so and
   names the user, never a WSPR `FAIL` that blames the antenna.  Pinned: Task 2 `JournalTests`, Task 3
   `test_journal_refused_fails_with_the_user`.
5. **Several radiod instances** feed one station (one log pair per receiver).  A person expects the PSK line to sum
   them all.  Pinned: Task 3 `test_sums_every_receiver`.

## File structure

| File | Task | Responsibility |
|---|---|---|
| `lib/sigmond/commands/check.py` (create) | 1-7 | the lines, the verdict, rendering, `cmd_check`, and the probes they read through |
| `bin/smd` (modify) | 1 | register `check`; dispatch without `_need_root`; one epilog line |
| `docs/contributor/orchestration.md` (modify) | 1 | the CLI table's `check` row |
| `tests/check_fakes.py` (create) | 1, 2 | `make_env()`, `svc()`, `NOW` for the check tests |
| `tests/test_check_core.py` (create) | 1 | verdict, exit code, rendering, guard, registration, the timing judge |
| `tests/test_check_probes.py` (create) | 2 | `service_state`, `Reader`, `read_journal`, `is_young`, `service_problem` |
| `tests/test_check_science.py` (create) | 3 | PSK, WSPR, meteor scatter |
| `tests/test_check_mag.py` (create) | 4 | magnetometer, freeze, adapter |
| `tests/test_check_grape.py` (create) | 5 | GRAPE channels, roots, freshness; storage |
| `tests/test_check_uploads.py` (create) | 6 | the sink switch, the backlog, `read_backlog` |
| `tests/test_check_pages.py` (create) | 7 | the VM's page lines, `http_status`, the full line order |
| `scripts/proxmox/sigmond-wizard.sh` (modify) | 8 | the `sigmond-check` heredoc after `sigmond-vm` |
| `scripts/proxmox/pm-align.py` (modify) | 8 | `WIZARD_HEREDOCS_SINCE_V372` |
| `tests/test_sigmond_check_host.py` (create) | 8 | the host script under stubs |
| `tests/test_pm_align.py` (modify) | 8 | pm-align carries `sigmond-check` |
| appliance `INSTALL.md`, `firstboot-v3.sh` (modify) | 9 | §8 sentence, §9, §11 row, panel help line |

---

### Task 1: The frame: lines, verdict, rendering, and `smd check` registered (sigmond)

**Files:**
- Create: `lib/sigmond/commands/check.py`
- Create: `tests/check_fakes.py`
- Create: `tests/test_check_core.py`
- Modify: `bin/smd:25170` (parser, before the `remote` parser), `bin/smd:25316` (dispatch, before the `sink`
  branch), `bin/smd:23711` (epilog)
- Modify: `docs/contributor/orchestration.md:141` (a row before `component`)

**Interfaces:**
- Consumes: `sigmond.component_state.compute_state(name).installed`.
- Produces, in `sigmond.commands.check`:
  - constants `OK='ok'`, `WAIT='wait'`, `FAIL='FAIL'`, `ABSENT='--'`, `EXIT_CODES={'PASS': 0, 'WAIT': 2, 'FAIL': 1}`,
    `FIX='Fix: '`, `RUN='run: '`, `MARK_WIDTH=4`, `LABEL_WIDTH=15`, `TEXT_COLUMN=22`.
  - `@dataclass(frozen=True) class Line: mark: str; label: str; text: str`.
  - `verdict(lines) -> str`, `summary(lines) -> str` (`'FAIL  (1 failing, 2 waiting)'`), `render_line(line) -> str`
    (one or two lines: a fix moves under the text; the text goes through `_ascii`, which turns anything past
    ASCII into `?`).
  - `class Unreadable(Exception)`: `Unreadable(what)` reads `cannot read <what> as <user>`.
  - `@dataclass class Env` with fields `now`, `installed`, `service`, `reader`, `journal`, `exists`, `disk`,
    `timestd_config`, `coordination`, `coordination_age_s`, `backlog`, `http` (later tasks fill them).
  - `default_env() -> Env`; `guarded(label, fn, env) -> Optional[Line]`; `run_checks(env, checks=None) -> List[Line]`;
    `cmd_check(args, env=None, checks=None) -> int`; `CHECKS: List[Tuple[str, Callable[[Env], Optional[Line]]]]`.
  - In `tests/check_fakes.py`: `NOW = 1_760_443_200.0` (2025-10-14 12:00:00 UTC), `make_env(**kw) -> Env`.

- [ ] **Step 1: Write the failing tests**

Create `tests/check_fakes.py`:

```python
"""Fakes for the `smd check` tests (tasks/spec-smd-check-v372.md §8).

make_env() builds an Env in which every client counts as installed.  A test
passes keyword arguments to change any field.  Task 2 adds services, files
and the journal.
"""
from sigmond.commands import check

NOW = 1_760_443_200.0          # 2025-10-14 12:00:00 UTC


def make_env(**kw):
    base = dict(now=NOW, installed=lambda name: True)
    base.update(kw)
    return check.Env(**base)
```

Create `tests/test_check_core.py`:

```python
"""`smd check`: lines, verdict, exit code, rendering, the exception guard and
the smd registration (tasks/spec-smd-check-v372.md §2, §3, §6, §8)."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from sigmond.commands import check
from sigmond.commands.check import ABSENT, FAIL, OK, WAIT, Line

from tests.check_fakes import make_env

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader("smd_under_test_check", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_check", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _run(args, lines):
    checks = [(ln.label, (lambda env, ln=ln: ln)) for ln in lines]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = check.cmd_check(args, env=make_env(), checks=checks)
    return rc, out.getvalue()


class VerdictTests(unittest.TestCase):
    def test_worst_mark_wins(self):
        self.assertEqual(check.verdict([Line(OK, 'a', ''), Line(ABSENT, 'b', '')]), 'PASS')
        self.assertEqual(check.verdict([Line(OK, 'a', ''), Line(WAIT, 'b', '')]), 'WAIT')
        self.assertEqual(check.verdict([Line(WAIT, 'a', ''), Line(FAIL, 'b', '')]), 'FAIL')

    def test_absent_lines_count_for_nothing(self):
        self.assertEqual(check.verdict([Line(ABSENT, 'a', '')]), 'PASS')
        self.assertEqual(check.summary([Line(ABSENT, 'a', '')]), 'PASS')

    def test_summary_counts_failing_and_waiting(self):
        lines = [Line(FAIL, 'a', ''), Line(WAIT, 'b', ''), Line(WAIT, 'c', ''), Line(OK, 'd', '')]
        self.assertEqual(check.summary(lines), 'FAIL  (1 failing, 2 waiting)')
        self.assertEqual(check.summary([Line(WAIT, 'b', '')]), 'WAIT  (1 waiting)')

    def test_exit_codes_follow_the_verdict(self):
        self.assertEqual(check.EXIT_CODES, {'PASS': 0, 'WAIT': 2, 'FAIL': 1})
        self.assertEqual(_run(types.SimpleNamespace(), [Line(OK, 'a', 'x')])[0], 0)
        self.assertEqual(_run(types.SimpleNamespace(), [Line(WAIT, 'a', 'x')])[0], 2)
        self.assertEqual(_run(types.SimpleNamespace(), [Line(FAIL, 'a', 'x')])[0], 1)


class RenderTests(unittest.TestCase):
    def test_columns_line_up_as_michael_drew_them(self):
        self.assertEqual(check.render_line(Line(OK, 'PSK', '214 FT8 and 31 FT4 spots')),
                         ' ok    PSK            214 FT8 and 31 FT4 spots')
        self.assertEqual(check.render_line(Line(ABSENT, 'meteor scatter', 'not on this station')),
                         ' --    meteor scatter not on this station')
        self.assertEqual(check.render_line(Line(OK, 'magnetometer page', 'IPv4 http://x:8082')),
                         ' ok    magnetometer page  IPv4 http://x:8082')

    def test_a_fix_moves_to_its_own_line_under_the_text(self):
        line = Line(FAIL, 'magnetometer', 'frozen: 0.00 C, no change in 60 s.  '
                                          'Fix: sudo systemctl restart mag-recorder')
        self.assertEqual(check.render_line(line).split('\n'), [
            ' FAIL  magnetometer   frozen: 0.00 C, no change in 60 s.',
            '                      Fix: sudo systemctl restart mag-recorder'])

    def test_text_output_ends_with_the_verdict(self):
        rc, out = _run(types.SimpleNamespace(json=False, no_verdict=False),
                       [Line(OK, 'PSK', 'fine'), Line(WAIT, 'GRAPE', 'soon')])
        self.assertEqual(out.splitlines(), [' ok    PSK            fine',
                                            ' wait  GRAPE          soon',
                                            'WAIT  (1 waiting)'])

    def test_no_verdict_drops_only_the_verdict_line(self):
        rc, out = _run(types.SimpleNamespace(json=False, no_verdict=True),
                       [Line(FAIL, 'PSK', 'broken')])
        self.assertEqual(out.splitlines(), [' FAIL  PSK            broken'])
        self.assertEqual(rc, 1)

    def test_json_carries_lines_verdict_and_exit(self):
        rc, out = _run(types.SimpleNamespace(json=True), [Line(WAIT, 'GRAPE', 'soon')])
        doc = json.loads(out)
        self.assertEqual(doc, {'lines': [{'mark': 'wait', 'label': 'GRAPE', 'text': 'soon'}],
                               'verdict': 'WAIT', 'exit': 2})


class AsciiTests(unittest.TestCase):
    """The host's console font (Lat15) shows nothing past ASCII (Michael,
    2026-10-14), so nothing smd check prints may leave it."""

    def test_every_kind_of_line_and_the_verdict_print_as_ascii(self):
        lines = [Line(OK, 'PSK', '214 FT8 and 31 FT4 spots in the last 15 min'),
                 Line(WAIT, 'uploads', 'off, new station: check the lines above, then run: smd sink upload'),
                 Line(FAIL, 'magnetometer', 'frozen: 0.00 C, no change in 60 s.  '
                                            'Fix: sudo systemctl restart mag-recorder'),
                 Line(ABSENT, 'meteor scatter', 'not on this station'),
                 Line(OK, 'magnetometer page', 'http://127.0.0.1:8082 answers'),
                 Line(FAIL, 'GRAPE', 'the check itself failed: OSError: caf\u00e9 \u2014 \u00b0')]
        rc, out = _run(types.SimpleNamespace(json=False, no_verdict=False), lines)
        bad = sorted({c for c in out if ord(c) >= 0x80})
        self.assertEqual(bad, [], out)
        self.assertIn('caf? ? ?', out)


class GuardTests(unittest.TestCase):
    def test_an_exception_becomes_a_failing_line_with_its_text(self):
        def boom(env):
            raise RuntimeError('disk on fire')
        lines = check.run_checks(make_env(), [('PSK', boom), ('WSPR', lambda env: Line(OK, 'WSPR', 'ok'))])
        self.assertEqual(lines[0], Line(FAIL, 'PSK', 'the check itself failed: RuntimeError: disk on fire'))
        self.assertEqual(lines[1].mark, OK)

    def test_unreadable_names_the_path_and_the_user(self):
        def refused(env):
            raise check.Unreadable('/var/log/psk-recorder')
        line = check.run_checks(make_env(), [('PSK', refused)])[0]
        self.assertEqual(line.mark, FAIL)
        self.assertRegex(line.text, r'^cannot read /var/log/psk-recorder as \S+$')

    def test_a_check_returning_none_prints_no_line(self):
        self.assertEqual(check.run_checks(make_env(), [('x', lambda env: None)]), [])


class SmdRegistrationTests(unittest.TestCase):
    """`smd check` is registered, read-only, and never reaches the timing judge."""

    @classmethod
    def setUpClass(cls):
        cls.smd = _load_smd()

    def _main(self, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['smd', *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = self.smd.main()
        return rc, out.getvalue()

    def test_check_never_asks_for_root(self):
        with mock.patch.object(self.smd, '_need_root', side_effect=AssertionError('elevated')), \
                mock.patch.object(check, 'default_env', return_value=make_env()), \
                mock.patch.object(check, 'CHECKS', [('PSK', lambda env: Line(OK, 'PSK', 'fine'))]):
            rc, out = self._main('check', '--no-verdict')
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), [' ok    PSK            fine'])

    def test_check_never_calls_the_timing_judge(self):
        poisoned = types.ModuleType('sigmond.timing_judge')

        def _raise(name):
            raise AssertionError(f'smd check touched the timing judge ({name})')
        poisoned.__getattr__ = _raise
        with mock.patch.dict(sys.modules, {'sigmond.timing_judge': poisoned}), \
                mock.patch.object(check, 'default_env', return_value=make_env()):
            rc, out = self._main('check', '--json')
        doc = json.loads(out)
        self.assertIn(rc, (0, 1, 2))
        self.assertEqual(doc['exit'], rc)
        self.assertFalse(any('timing judge' in ln['text'] for ln in doc['lines']), doc)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_check_core.py -q --override-ini addopts=`
Expected: a collection error, `ModuleNotFoundError: No module named 'sigmond.commands.check'`.

- [ ] **Step 3: Write the module**

Create `lib/sigmond/commands/check.py`:

```python
"""`smd check` -- one command that says whether this station works (v3.72 #14).

tasks/spec-smd-check-v372.md.  Each check reads data that already exists and
returns one Line: a mark, a label and a sentence.  The verdict takes the worst
mark.  Nothing here changes the station, and nothing here calls the timing
judge, `smd doctor` or a watcher.

Every probe of the outside world (files, systemd, the journal, HTTP, the
clock) comes in through an Env, so the tests build a station in a temporary
directory and inject the clock.
"""
from __future__ import annotations

import json
import os
import pwd
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Tuple

# ASCII on purpose: the Proxmox host's console font (Lat15, Fixed 8x16) has
# no glyph for a check mark or a dotted circle, and the installer reads this
# on exactly that console (Michael, 2026-10-14).  Everything smd check prints
# stays ASCII; render_line enforces it on the text.
OK, WAIT, FAIL, ABSENT = 'ok', 'wait', 'FAIL', '--'
MARKS = (OK, WAIT, FAIL, ABSENT)
EXIT_CODES = {'PASS': 0, 'WAIT': 2, 'FAIL': 1}
MARK_WIDTH = 4
LABEL_WIDTH = 15
TEXT_COLUMN = 1 + MARK_WIDTH + 2 + LABEL_WIDTH     # where a line's text starts
# Every command a line asks someone to type follows one of these two words.
# `sigmond-check` on the host writes `sigmond-vm ` after them, so the command
# reaches the VM from the host's console.
FIX = 'Fix: '
RUN = 'run: '


@dataclass(frozen=True)
class Line:
    mark: str
    label: str
    text: str


def verdict(lines: Sequence[Line]) -> str:
    marks = {ln.mark for ln in lines}
    if FAIL in marks:
        return 'FAIL'
    if WAIT in marks:
        return 'WAIT'
    return 'PASS'


def summary(lines: Sequence[Line]) -> str:
    failing = sum(1 for ln in lines if ln.mark == FAIL)
    waiting = sum(1 for ln in lines if ln.mark == WAIT)
    parts = []
    if failing:
        parts.append(f'{failing} failing')
    if waiting:
        parts.append(f'{waiting} waiting')
    word = verdict(lines)
    return f'{word}  ({", ".join(parts)})' if parts else word


def _ascii(text: str) -> str:
    """Text as the console can show it: any other character becomes '?'
    (an exception's message can carry anything)."""
    return text.encode('ascii', 'replace').decode('ascii')


def render_line(line: Line) -> str:
    """' ok    PSK            214 FT8 ...'.  A fix moves to a line of its
    own, under the text, so the command stands alone where a person reads it."""
    label = (line.label.ljust(LABEL_WIDTH) if len(line.label) < LABEL_WIDTH
             else line.label + '  ')
    head, sep, fix = _ascii(line.text).partition('  ' + FIX)
    out = f' {line.mark.ljust(MARK_WIDTH)}  {label}{head}'.rstrip()
    if sep:
        out += '\n' + ' ' * TEXT_COLUMN + FIX + fix
    return out


def _user() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return str(os.geteuid())


class Unreadable(Exception):
    """A file or the journal refused us, and `sudo -n` did too."""

    def __init__(self, what):
        super().__init__(f'cannot read {what} as {_user()}')


@dataclass
class Env:
    """What the checks may look at.  default_env() fills it with the real
    probes; the tests fill it with fakes.  The comment names the task of
    tasks/plan-smd-check-v372.md that first fills each field."""
    now: float = field(default_factory=time.time)                      # Task 1
    installed: Callable[[str], bool] = None                            # Task 1
    service: Callable[[str], Any] = None                               # Task 2
    reader: Any = None                                                 # Task 2
    journal: Callable[[Sequence[str], int], List[str]] = None          # Task 2
    exists: Callable[[str], bool] = os.path.exists                     # Task 4
    disk: Callable[[str], Tuple[int, int]] = None                      # Task 5
    timestd_config: Callable[[], Optional[dict]] = None                # Task 5
    coordination: Callable[[], Any] = None                             # Task 6
    coordination_age_s: Callable[[], Optional[float]] = None           # Task 6
    backlog: Callable[[], dict] = None                                 # Task 6
    http: Callable[[str], Tuple[Optional[int], str]] = None            # Task 7


def _installed(name: str) -> bool:
    from ..component_state import compute_state
    return compute_state(name).installed


def default_env() -> Env:
    return Env(installed=_installed)


def guarded(label: str, fn: Callable[[Env], Optional[Line]], env: Env) -> Optional[Line]:
    """Run one check.  An exception becomes a FAIL line with its text: never a
    crash, never a pass."""
    try:
        return fn(env)
    except Unreadable as exc:
        return Line(FAIL, label, str(exc))
    except Exception as exc:  # noqa: BLE001 -- the whole point
        return Line(FAIL, label, f'the check itself failed: {type(exc).__name__}: {exc}')


def run_checks(env: Env, checks=None) -> List[Line]:
    out = []
    for label, fn in (CHECKS if checks is None else checks):
        line = guarded(label, fn, env)
        if line is not None:
            out.append(line)
    return out


def cmd_check(args, env: Optional[Env] = None, checks=None) -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass
    lines = run_checks(env or default_env(), checks)
    word = verdict(lines)
    if getattr(args, 'json', False):
        print(json.dumps({'lines': [asdict(ln) for ln in lines], 'verdict': word,
                          'exit': EXIT_CODES[word]}, ensure_ascii=False))
    else:
        for ln in lines:
            print(render_line(ln))
        if not getattr(args, 'no_verdict', False):
            print(summary(lines))
    return EXIT_CODES[word]


# ---------------------------------------------------------------------------
# The lines, in the order the installer reads them (spec §2).  Each entry is
# (label, function); a function returns a Line, or None for a line that does
# not apply here at all.  Later tasks add their entries in place.
# ---------------------------------------------------------------------------

CHECKS: List[Tuple[str, Callable[[Env], Optional[Line]]]] = []
```

- [ ] **Step 4: Register `check` in `bin/smd`**

Before the line `    p_remote = sub.add_parser('remote',` (25170), insert:

```python
    # check -- read-only: does this station work?  (tasks/spec-smd-check-v372.md)
    p_check = sub.add_parser('check',
        help='does this station work?  One line per item and a verdict: PASS, WAIT '
             'or FAIL.  Read-only; needs no root')
    p_check.add_argument('--no-verdict', action='store_true',
        help='print the lines without the verdict line (sigmond-check on the host '
             'adds its own)')
    p_check.add_argument('--json', action='store_true',
        help='print the lines and the verdict as one JSON object')

```

Before the line `    if args.command == 'sink':` (25316), insert:

```python
    if args.command == 'check':
        # Read-only by design: never _need_root, never the timing judge.
        from sigmond.commands.check import cmd_check
        return cmd_check(args)

```

In the epilog, after the line `                status     service health snapshot` (23711), insert:

```
                check      does this station work?  one line per item, then PASS | WAIT | FAIL
```

- [ ] **Step 5: Add the CLI table row**

In `docs/contributor/orchestration.md`, insert before the row that starts `` | `component` | installer | `` (141):

```markdown
| `check` | installer, daily | does this station work: one line per item (PSK, WSPR, magnetometer, GRAPE, storage, uploads, meteor scatter, the VM's own pages), each marked `ok`, `wait` (not yet), `FAIL` or `--` (not on this station), in ASCII for the host console, then PASS (exit 0), WAIT (exit 2) or FAIL (exit 1). `--no-verdict` drops the verdict line for `sigmond-check` on the Proxmox host, which adds the page lines through the host's addresses; `--json` serves tests and tools. It never calls the timing judge (tasks/spec-smd-check-v372.md) | `cmd_check` → `commands/check.py`, `component_state.py` (`compute_state`), `heartbeat.py` (`_read_backlog`), `coordination.py` | no |
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `.venv/bin/pytest tests/test_check_core.py tests/test_docs_cli_table.py -q --override-ini addopts=`
Expected: `18 passed`.

Run: `SIGMOND_NO_VENV_REEXEC=1 PYTHONPATH=lib .venv/bin/python bin/smd check; echo rc=$?`
Expected: one line, `PASS`, and `rc=0` (no line is registered yet).

- [ ] **Step 7: Commit**

```bash
git add lib/sigmond/commands/check.py tests/check_fakes.py tests/test_check_core.py bin/smd docs/contributor/orchestration.md
git commit -m "smd check: the frame -- lines, verdict, exit code, rendering

One Line per item (mark, label, text); the worst mark gives PASS, WAIT
or FAIL, exit 0, 2 or 1.  A check that raises becomes a failing line
with the exception's text.  smd check registers read-only: no
_need_root, no timing judge (tasks/spec-smd-check-v372.md §2-§3, §8).

<attribution lines>"
```

- [ ] **Step 8: Mutation check**

```bash
find lib tests -name __pycache__ -prune -exec rm -rf {} +
export PYTHONDONTWRITEBYTECODE=1
```

In `cmd_check`, before `    lines = run_checks(env or default_env(), checks)`, add the line
`    from sigmond import timing_judge; timing_judge.judge`.  Run
`.venv/bin/pytest tests/test_check_core.py -q --override-ini addopts=`.
Expected: `test_check_never_calls_the_timing_judge` fails (the planner saw 6 failures: the poisoned module raises in
every test that runs `cmd_check`).  Then `git checkout -- lib/sigmond/commands/check.py`.

Put a check mark back: change `OK, WAIT, FAIL, ABSENT = 'ok', 'wait', 'FAIL', '--'` to
`OK, WAIT, FAIL, ABSENT = '✓', 'wait', 'FAIL', '--'`.  Expected: `test_every_kind_of_line_and_the_verdict_print_as_ascii`
fails (with three render tests beside it).  Restore.  In `render_line`, change `_ascii(line.text).partition(` to
`line.text.partition(`.  Expected: the same ASCII test fails alone (its exception text carries `é`, an em dash and
`°`).  Restore.

In `guarded`, change `    except Exception as exc:  # noqa: BLE001 -- the whole point` to
`    except ZeroDivisionError as exc:`.  Expected: `test_an_exception_becomes_a_failing_line_with_its_text` fails.
Restore with `git checkout -- lib/sigmond/commands/check.py`.

Note: Task 1's `CHECKS` stays empty, so the timing-judge test exercises only the frame.  Task 7 re-runs it with every
line registered (Task 7, Step 6).

---

### Task 2: The probes: systemd, files through `sudo -n`, the journal (sigmond)

**Files:**
- Modify: `lib/sigmond/commands/check.py` (imports; `default_env`; a new section after `cmd_check`)
- Modify: `tests/check_fakes.py`
- Create: `tests/test_check_probes.py`

**Interfaces:**
- Consumes: `Line`, `FAIL`, `FIX`, `Unreadable`, `Env` (Task 1).
- Produces, in `sigmond.commands.check`:
  - `@dataclass(frozen=True) class Service: state: str; age_s: Optional[float]; enabled: bool = False` — `state` is
    one of `active`, `activating`, `failed`, `inactive`, `absent`.
  - `service_state(pattern: str, run=subprocess.run, monotonic=time.monotonic) -> Service`.
  - `class Reader(run=subprocess.run, euid=os.geteuid)` with `tail(path, nbytes) -> Optional[str]`,
    `listdir(path) -> Optional[List[str]]`, `newest_mtime(path) -> Optional[float]`; each returns None for a missing
    path and raises `Unreadable(path)` when the file and `sudo -n` both refuse.
  - `read_journal(units, minutes, *, run=subprocess.run, euid=os.geteuid, groups=_my_groups) -> List[str]`.
  - `service_problem(label, svc, unit) -> Optional[Line]`; `is_young(svc, window_s) -> bool`;
    `minutes(seconds) -> int` (rounded, at least 1).
  - In `tests/check_fakes.py`: `HOUR = 3600.0`, `svc(state='active', age_s=HOUR, enabled=True) -> Service`, and
    `make_env()` now also fills `service`, `reader` (a `Reader` that never asks sudo) and `journal` (empty).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_check_probes.py`:

```python
"""`smd check` probes: systemd state and age, files through a sudo
fallback, the journal (tasks/spec-smd-check-v372.md §4, §4.2)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from sigmond.commands import check


def _cp(stdout='', returncode=0, stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class FakeRun:
    """Answers each argv by its first words; records every call."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        for prefix, result in self.answers:
            if list(argv[:len(prefix)]) == list(prefix):
                return result
        return _cp(returncode=1)


SHOW_ACTIVE = ('Id=psk-recorder@AC0G\\x3dB4.service\nActiveState=active\nLoadState=loaded\n'
               'UnitFileState=enabled\nActiveEnterTimestampMonotonic=100000000\n\n'
               'Id=psk-recorder@AC0G\\x3dB5.service\nActiveState=active\nLoadState=loaded\n'
               'UnitFileState=enabled\nActiveEnterTimestampMonotonic=400000000\n')


class ServiceStateTests(unittest.TestCase):
    def _state(self, listing, show, pattern='psk-recorder@*.service', mono=1000.0):
        run = FakeRun([(['systemctl', 'list-units'], _cp(listing)),
                       (['systemctl', 'show'], _cp(show))])
        return check.service_state(pattern, run=run, monotonic=lambda: mono), run

    def test_age_comes_from_the_oldest_active_instance(self):
        listing = ('psk-recorder@AC0G\\x3dB4.service loaded active running psk\n'
                   'psk-recorder@AC0G\\x3dB5.service loaded active running psk\n')
        svc, run = self._state(listing, SHOW_ACTIVE)
        self.assertEqual(svc, check.Service('active', 900.0, True))
        self.assertIn('ActiveEnterTimestampMonotonic', run.calls[1])

    def test_no_unit_means_absent(self):
        svc, _ = self._state('', '')
        self.assertEqual(svc.state, 'absent')

    def test_a_failed_unit_with_no_active_one_reads_failed(self):
        show = ('Id=mag-recorder.service\nActiveState=failed\nLoadState=loaded\n'
                'UnitFileState=enabled\nActiveEnterTimestampMonotonic=0\n')
        svc, _ = self._state('mag-recorder.service loaded failed failed mag\n', show,
                             pattern='mag-recorder.service')
        self.assertEqual(svc, check.Service('failed', None, True))

    def test_a_single_unit_not_loaded_is_shown_by_name(self):
        # list-units --all omits an inactive unit systemd has not loaded; show still knows it.
        show = ('Id=mag-recorder.service\nActiveState=inactive\nLoadState=loaded\n'
                'UnitFileState=disabled\nActiveEnterTimestampMonotonic=0\n')
        svc, run = self._state('', show, pattern='mag-recorder.service')
        self.assertEqual(svc, check.Service('inactive', None, False))
        self.assertEqual(run.calls[1][-1], 'mag-recorder.service')

    def test_a_unit_systemd_does_not_know_reads_absent(self):
        show = 'Id=mag-recorder.service\nActiveState=inactive\nLoadState=not-found\n'
        svc, _ = self._state('', show, pattern='mag-recorder.service')
        self.assertEqual(svc.state, 'absent')


class YoungTests(unittest.TestCase):
    def test_young_inside_the_window_only(self):
        self.assertTrue(check.is_young(check.Service('active', 59.0), 60))
        self.assertFalse(check.is_young(check.Service('active', 60.0), 60))

    def test_starting_counts_as_young_and_unknown_age_does_not(self):
        self.assertTrue(check.is_young(check.Service('activating', None), 60))
        self.assertFalse(check.is_young(check.Service('active', None), 60))

    def test_a_stopped_service_fails_with_a_fix(self):
        line = check.service_problem('PSK', check.Service('failed', None), 'psk-recorder')
        self.assertEqual(line, check.Line(check.FAIL, 'PSK',
                                          'psk-recorder is failed.  Fix: smd start psk-recorder'))
        self.assertIsNone(check.service_problem('PSK', check.Service('active', 5.0), 'psk-recorder'))


@unittest.skipIf(os.geteuid() == 0, 'root reads every file; the refusal cannot happen')
class ReaderTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)

    def test_tail_returns_the_last_bytes_and_none_for_a_missing_file(self):
        p = self.dir / 'a.log'
        p.write_text('one\ntwo\nthree\n')
        r = check.Reader()
        self.assertEqual(r.tail(p, 6), 'three\n')
        self.assertIsNone(r.tail(self.dir / 'missing.log', 6))

    def test_a_refused_file_goes_through_sudo_once(self):
        p = self.dir / 'secret.log'
        p.write_text('x\n')
        p.chmod(0)
        self.addCleanup(p.chmod, 0o600)
        run = FakeRun([(['sudo', '-n', 'tail'], _cp('via sudo\n'))])
        self.assertEqual(check.Reader(run=run).tail(p, 100), 'via sudo\n')
        self.assertEqual(run.calls, [['sudo', '-n', 'tail', '-c', '100', str(p)]])

    def test_a_refusal_sudo_also_refuses_raises_unreadable(self):
        p = self.dir / 'secret.log'
        p.write_text('x\n')
        p.chmod(0)
        self.addCleanup(p.chmod, 0o600)
        run = FakeRun([(['sudo'], _cp(returncode=1, stderr='a password is required'))])
        with self.assertRaisesRegex(check.Unreadable, r'cannot read .*secret\.log as '):
            check.Reader(run=run).tail(p, 100)

    def test_newest_mtime_sees_files_only(self):
        (self.dir / 'old.bin').write_text('x')
        (self.dir / 'new.bin').write_text('x')
        (self.dir / 'sub').mkdir()
        os.utime(self.dir / 'old.bin', (1000, 1000))
        os.utime(self.dir / 'new.bin', (2000, 2000))
        os.utime(self.dir / 'sub', (9000, 9000))
        r = check.Reader()
        self.assertEqual(r.newest_mtime(self.dir), 2000)
        self.assertIsNone(r.newest_mtime(self.dir / 'absent'))
        self.assertEqual(r.listdir(self.dir), ['new.bin', 'old.bin', 'sub'])


class JournalTests(unittest.TestCase):
    def test_a_journal_group_member_reads_directly(self):
        run = FakeRun([(['journalctl'], _cp('line one\nline two\n'))])
        out = check.read_journal(['wspr-recorder@*.service'], 10, run=run,
                                 euid=lambda: 1000, groups=lambda: {'adm'})
        self.assertEqual(out, ['line one', 'line two'])
        self.assertEqual(run.calls[0], ['journalctl', '--no-pager', '-q', '-o', 'cat',
                                        '--since', '10 min ago', '-u', 'wspr-recorder@*.service'])

    def test_anyone_else_asks_sudo_first(self):
        # journalctl shows such a user only their own messages, and says nothing.
        run = FakeRun([(['sudo', '-n', 'journalctl'], _cp('from sudo\n'))])
        out = check.read_journal(['x.service'], 5, run=run, euid=lambda: 1000, groups=set)
        self.assertEqual(out, ['from sudo'])

    def test_journal_refused_raises_unreadable(self):
        run = FakeRun([(['sudo'], _cp(returncode=1, stderr='sudo: a password is required'))])
        with self.assertRaisesRegex(check.Unreadable, r'^cannot read the journal as '):
            check.read_journal(['x.service'], 5, run=run, euid=lambda: 1000, groups=set)

    def test_a_glob_that_never_logged_is_an_empty_journal(self):
        run = FakeRun([(['journalctl'], _cp(returncode=1, stderr='Failed to add filter for units: No data available'))])
        self.assertEqual(check.read_journal(['x@*.service'], 5, run=run, euid=lambda: 0), [])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_check_probes.py -q --override-ini addopts=`
Expected: `16 failed`, each with `AttributeError: module 'sigmond.commands.check' has no attribute …` (`Service`,
`service_state`, `Reader`, `read_journal`, `is_young`, `service_problem`).

- [ ] **Step 3: Write the probes**

In `lib/sigmond/commands/check.py`, add `import subprocess` to the imports (between `import pwd` and `import sys`).

Replace `default_env` with:

```python
def default_env() -> Env:
    return Env(installed=_installed, service=service_state, reader=Reader(),
               journal=read_journal)
```

After `cmd_check` (before the `# The lines, in the order …` block), insert:

```python
# ---------------------------------------------------------------------------
# Probes: files with a sudo fallback, systemd, the journal
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Service:
    state: str                  # active | activating | failed | inactive | absent
    age_s: Optional[float]      # since the oldest active unit started; None if unknown
    enabled: bool = False


def service_state(pattern: str, run=subprocess.run, monotonic=time.monotonic) -> Service:
    """The state of the units matching `pattern` (a glob such as
    `psk-recorder@*.service`, or one unit name).  The age comes from
    ActiveEnterTimestampMonotonic, so a host clock step cannot fake it."""
    r = run(['systemctl', 'list-units', '--all', '--plain', '--no-legend', '--no-pager',
             '--type=service', pattern],
            capture_output=True, text=True, errors='replace', timeout=15, check=False)
    units = {}
    for ln in (r.stdout or '').splitlines():
        parts = ln.split()
        if len(parts) >= 3 and parts[0].endswith('.service'):
            units[parts[0]] = parts[2]
    if not units and '*' not in pattern:
        units = {pattern: None}
    if not units:
        return Service('absent', None, False)
    show = run(['systemctl', 'show', '-p', 'Id', '-p', 'ActiveState', '-p', 'LoadState',
                '-p', 'UnitFileState', '-p', 'ActiveEnterTimestampMonotonic', *sorted(units)],
               capture_output=True, text=True, errors='replace', timeout=15, check=False)
    blocks, cur = [], {}
    for ln in (show.stdout or '').splitlines() + ['']:
        if not ln.strip():
            if cur:
                blocks.append(cur)
            cur = {}
            continue
        key, _, val = ln.partition('=')
        cur[key] = val.strip()
    blocks = [b for b in blocks if b.get('LoadState') not in ('not-found', None)]
    if not blocks:
        return Service('absent', None, False)
    states = [b.get('ActiveState', '') for b in blocks]
    enabled = any(b.get('UnitFileState') in ('enabled', 'enabled-runtime', 'static', 'indirect')
                  for b in blocks)
    starts = []
    for b in blocks:
        if b.get('ActiveState') == 'active':
            try:
                us = int(b.get('ActiveEnterTimestampMonotonic') or 0)
            except ValueError:
                us = 0
            if us > 0:
                starts.append(us / 1e6)
    for state in ('active', 'activating', 'failed'):
        if state in states:
            age = (monotonic() - min(starts)) if (state == 'active' and starts) else None
            return Service(state, age, enabled)
    return Service('inactive', None, enabled)


class Reader:
    """Reads files as the user who runs smd, and through `sudo -n` once when
    the file refuses that user (as `_file_size` in bin/smd does)."""

    def __init__(self, run=subprocess.run, euid=os.geteuid):
        self._run = run
        self._euid = euid

    def _sudo(self, argv) -> Optional[str]:
        if self._euid() == 0:
            return None
        try:
            r = self._run(['sudo', '-n', *argv], capture_output=True, text=True,
                          errors='replace', timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    def tail(self, path, nbytes: int) -> Optional[str]:
        """The last `nbytes` of `path` as text, or None when it does not exist."""
        try:
            with open(path, 'rb') as fh:
                size = os.fstat(fh.fileno()).st_size
                fh.seek(max(0, size - nbytes))
                return fh.read().decode('utf-8', 'replace')
        except FileNotFoundError:
            return None
        except PermissionError:
            out = self._sudo(['tail', '-c', str(nbytes), str(path)])
            if out is None:
                raise Unreadable(path)
            return out

    def listdir(self, path) -> Optional[List[str]]:
        try:
            return sorted(os.listdir(path))
        except FileNotFoundError:
            return None
        except PermissionError:
            out = self._sudo(['find', str(path), '-mindepth', '1', '-maxdepth', '1',
                              '-printf', '%f\\n'])
            if out is None:
                raise Unreadable(path)
            return sorted(x for x in out.splitlines() if x)

    def newest_mtime(self, path) -> Optional[float]:
        """The newest modification time of a plain file directly in `path`,
        or None when `path` is absent or holds no file."""
        try:
            best = None
            with os.scandir(path) as it:
                for entry in it:
                    if entry.is_file(follow_symlinks=False):
                        m = entry.stat(follow_symlinks=False).st_mtime
                        best = m if best is None else max(best, m)
            return best
        except FileNotFoundError:
            return None
        except PermissionError:
            out = self._sudo(['find', str(path), '-maxdepth', '1', '-type', 'f',
                              '-printf', '%T@\\n'])
            if out is None:
                raise Unreadable(path)
            times = [float(x) for x in out.split()]
            return max(times) if times else None


JOURNAL_GROUPS = ('systemd-journal', 'adm', 'wheel')


def _my_groups() -> set:
    import grp
    names = set()
    for gid in os.getgroups():
        try:
            names.add(grp.getgrgid(gid).gr_name)
        except KeyError:
            pass
    return names


def read_journal(units: Sequence[str], minutes: int, *, run=subprocess.run,
                 euid=os.geteuid, groups=_my_groups) -> List[str]:
    """The last `minutes` of the journal of `units`, message text only.  A
    user outside the journal's groups sees only their own messages and no
    error, so such a user asks `sudo -n` from the start."""
    argv = ['journalctl', '--no-pager', '-q', '-o', 'cat', '--since', f'{minutes} min ago']
    for unit in units:
        argv += ['-u', unit]
    if euid() != 0 and not (set(JOURNAL_GROUPS) & groups()):
        argv = ['sudo', '-n', *argv]
    try:
        r = run(argv, capture_output=True, text=True, errors='replace', timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        raise Unreadable('the journal')
    if r.returncode != 0:
        # journalctl refuses a -u glob that matches no unit that ever logged.
        if 'No data available' in (r.stderr or ''):
            return []
        raise Unreadable('the journal')
    return (r.stdout or '').splitlines()


def service_problem(label: str, svc: Service, unit: str) -> Optional[Line]:
    """A FAIL line when the service runs not at all, else None."""
    if svc.state in ('active', 'activating'):
        return None
    if svc.state == 'absent':
        return Line(FAIL, label, f'{unit} is installed but has no service.  Tell the fleet admin.')
    return Line(FAIL, label, f'{unit} is {svc.state}.  {FIX}smd start {unit}')


def is_young(svc: Service, window_s: float) -> bool:
    """True while a service has run for less than `window_s`.  Starting
    counts as young; an unknown age does not, so it can never hide a fault."""
    if svc.state == 'activating':
        return True
    return svc.age_s is not None and svc.age_s < window_s


def minutes(seconds: float) -> int:
    return max(1, int(round(seconds / 60.0)))
```

Replace `tests/check_fakes.py` with:

```python
"""Fakes for the `smd check` tests (tasks/spec-smd-check-v372.md §8).

make_env() builds an Env in which every client counts as installed, every
service has run for an hour, files come from the real Reader (so the tests
build them in a temporary directory) and the journal is empty.  A test
passes keyword arguments to change any field.
"""
from sigmond.commands import check

NOW = 1_760_443_200.0          # 2025-10-14 12:00:00 UTC
HOUR = 3600.0


def svc(state='active', age_s=HOUR, enabled=True):
    return check.Service(state, age_s, enabled)


def make_env(**kw):
    base = dict(now=NOW, installed=lambda name: True, service=lambda pattern: svc(),
                reader=check.Reader(euid=lambda: 0), journal=lambda units, minutes: [])
    base.update(kw)
    return check.Env(**base)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `.venv/bin/pytest tests/test_check_core.py tests/test_check_probes.py -q --override-ini addopts=`
Expected: `31 passed` (as a non-root user; root skips the four `ReaderTests`, which need a refusal).

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/commands/check.py tests/check_fakes.py tests/test_check_probes.py
git commit -m "smd check: probes for systemd, files and the journal

service_state reads a unit glob's state and its age from
ActiveEnterTimestampMonotonic.  Reader tries a file as the user, then
sudo -n once, then raises Unreadable.  read_journal asks sudo -n from
the start for a user outside the journal groups, because journalctl
hides other users' messages without an error (spec §4.2).

<attribution lines>"
```

- [ ] **Step 6: Mutation check**

Clear bytecode as in Task 1, Step 8.  In `is_young`, change `return svc.age_s is not None and svc.age_s < window_s`
to `return svc.age_s is None or svc.age_s < window_s`.  Run `.venv/bin/pytest tests/test_check_probes.py -q
--override-ini addopts=`.  Expected: `test_starting_counts_as_young_and_unknown_age_does_not` fails.  Restore with
`git checkout -- lib/sigmond/commands/check.py`.

In `read_journal`, delete the two lines `    if euid() != 0 and not (set(JOURNAL_GROUPS) & groups()):` and
`        argv = ['sudo', '-n', *argv]`.  Expected: `test_anyone_else_asks_sudo_first` fails.  Restore.

---

### Task 3: PSK, WSPR and meteor scatter (sigmond)

**Files:**
- Modify: `lib/sigmond/commands/check.py` (imports; a new section; `CHECKS`)
- Create: `tests/test_check_science.py`

**Interfaces:**
- Consumes: `Env`, `Line`, the marks, `service_problem`, `is_young`, `minutes`, `Reader.listdir`, `Reader.tail`,
  `Env.journal` (Tasks 1-2).
- Produces: `psk_line(env, log_dir=PSK_LOG_DIR) -> Line`, `wspr_line(env) -> Line`, `meteor_line(env) -> Line`,
  `count_psk_spots(reader, log_dir, now, window_s=PSK_WINDOW_S) -> dict` (`{'ft8': n, 'ft4': n}`),
  `plural(n, word) -> str`; constants `PSK_LOG_DIR`, `PSK_WINDOW_S`, `WSPR_UNITS`, `WSPR_WINDOW_MIN`, `WSPR_YOUNG_S`,
  `METEOR_UNITS`, `METEOR_WINDOW_MIN`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_check_science.py`:

```python
"""`smd check` science lines: PSK, WSPR, meteor scatter
(tasks/spec-smd-check-v372.md §4)."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sigmond.commands import check
from sigmond.commands.check import ABSENT, FAIL, OK, WAIT

from tests.check_fakes import NOW, make_env, svc


def ft8(t, freq='14,074,131.2'):
    """A decode_ft8 spot line stamped at unix time t (UTC)."""
    stamp = datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y/%m/%d %H:%M:%S')
    return f'{stamp} -15 +0.50 {freq} ~ K1ABC W1XYZ EM26\n'


def jt9(t):
    stamp = datetime.fromtimestamp(t, tz=timezone.utc).strftime('%y%m%d %H%M%S')
    return f'{stamp} 14074000  -12  0.3 1234 ~  K1ABC W1XYZ EM26\n'


class PskTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.logs = Path(d.name)

    def line(self, **kw):
        return check.psk_line(make_env(**kw), log_dir=self.logs)

    def test_counts_spots_inside_15_minutes_only(self):
        (self.logs / 'B4-100-rx888mk2-status.local-ft8.log').write_text(
            ft8(NOW - 16 * 60) + ft8(NOW - 14 * 60) + ft8(NOW - 60) + 'garbage line\n')
        (self.logs / 'B4-100-rx888mk2-status.local-ft4.log').write_text(ft8(NOW - 30))
        self.assertEqual(self.line(), check.Line(OK, 'PSK', '2 FT8 and 1 FT4 spots in the last 15 min'))

    def test_sums_every_receiver(self):
        # Review Focus 5: two radiod instances, one log pair each.
        (self.logs / 'bee1-status.local-ft8.log').write_text(ft8(NOW - 60))
        (self.logs / 'bee2-status.local-ft8.log').write_text(ft8(NOW - 60) + ft8(NOW - 120))
        self.assertEqual(self.line().text, '3 FT8 and 0 FT4 spots in the last 15 min')

    def test_reads_jt9_lines_too(self):
        (self.logs / 'rx-ft8.log').write_text(jt9(NOW - 60))
        self.assertEqual(self.line().text, '1 FT8 and 0 FT4 spots in the last 15 min')

    def test_window_reaches_back_across_midnight(self):
        # Review Focus 1: at 00:05Z the window still counts 23:55Z spots.
        just_after = datetime(2025, 10, 15, 0, 5, tzinfo=timezone.utc).timestamp()
        (self.logs / 'rx-ft8.log').write_text(ft8(just_after - 10 * 60))
        self.assertEqual(check.psk_line(make_env(now=just_after), log_dir=self.logs).mark, OK)

    def test_young_service_with_no_spots_waits(self):
        line = self.line(service=lambda p: svc(age_s=14 * 60))
        self.assertEqual(line.mark, WAIT)
        self.assertIn('started 14 min ago', line.text)

    def test_restart_recreated_the_log(self):
        # Review Focus 3: a restart recreates <rx>-ft8.log, so older spots are gone.
        (self.logs / 'rx-ft8.log').write_text('')
        self.assertEqual(self.line(service=lambda p: svc(age_s=120)).mark, WAIT)
        (self.logs / 'rx-ft8.log').write_text(ft8(NOW - 30))
        self.assertEqual(self.line(service=lambda p: svc(age_s=120)).mark, OK)

    def test_old_service_with_no_spots_fails_and_names_the_antenna_first(self):
        line = self.line(service=lambda p: svc(age_s=15 * 60))
        self.assertEqual(line.mark, FAIL)
        self.assertTrue(line.text.startswith('no FT8 or FT4 spot in 15 min.  Check the antenna'))

    def test_stopped_service_fails_even_with_recent_spots(self):
        (self.logs / 'rx-ft8.log').write_text(ft8(NOW - 30))
        line = self.line(service=lambda p: svc(state='failed', age_s=None))
        self.assertEqual(line, check.Line(FAIL, 'PSK', 'psk-recorder is failed.  Fix: smd start psk-recorder'))

    def test_not_installed_is_absent(self):
        self.assertEqual(self.line(installed=lambda n: False).mark, ABSENT)

    def test_missing_log_dir_counts_as_no_spots(self):
        line = check.psk_line(make_env(), log_dir=self.logs / 'absent')
        self.assertEqual(line.mark, FAIL)


CYCLE = ('2026-10-14 11:52:31,897 - wspr_recorder.spot_sink - INFO - cycle UTC 11:50 '
         'rx=B4 → {n} spots in wspr.spots, 15 noise rows in wspr.noise (4 bands, write 2 ms) '
         'dt=+0.84 bands=[{bands}]')


class WsprTests(unittest.TestCase):
    def test_sums_spots_and_counts_bands(self):
        journal = [CYCLE.format(n=30, bands='40:10 30:7 20:13'),
                   'some other line',
                   CYCLE.format(n=18, bands='40:9 20:5 17:4 15:0')]
        env = make_env(journal=lambda units, minutes: journal)
        self.assertEqual(check.wspr_line(env), check.Line(OK, 'WSPR', '48 spots in the last 10 min, on 4 bands'))

    def test_old_shape_without_bands_list_uses_the_band_count(self):
        journal = ['cycle UTC 02:40 → 62 spots in wspr.spots (9 bands, write 4 ms)']
        line = check.wspr_line(make_env(journal=lambda u, m: journal))
        self.assertEqual(line.text, '62 spots in the last 10 min, on 9 bands')

    def test_asks_the_wspr_journal_for_10_minutes(self):
        asked = []
        check.wspr_line(make_env(journal=lambda u, m: asked.append((tuple(u), m)) or []))
        self.assertEqual(asked, [(('wspr-recorder@*.service',), 10)])

    def test_zero_spot_cycles_wait_then_fail(self):
        journal = [CYCLE.format(n=0, bands='')]
        young = make_env(journal=lambda u, m: journal, service=lambda p: svc(age_s=11 * 60))
        old = make_env(journal=lambda u, m: journal, service=lambda p: svc(age_s=12 * 60))
        self.assertEqual(check.wspr_line(young).mark, WAIT)
        self.assertEqual(check.wspr_line(old).mark, FAIL)
        self.assertIn('Check the antenna', check.wspr_line(old).text)

    def test_journal_refused_fails_with_the_user(self):
        # Review Focus 4: sigmond outside the journal groups and no sudo.
        def refused(units, minutes):
            raise check.Unreadable('the journal')
        line = check.run_checks(make_env(journal=refused), [('WSPR', check.wspr_line)])[0]
        self.assertEqual(line.mark, FAIL)
        self.assertRegex(line.text, r'^cannot read the journal as \S+$')


STATS = ('INFO:meteor_scatter.core.recorder:stats MSK144 rx=B4-status.local: spots={s} '
         'decodes={ok}/{all} slots_empty=0 freqs=2 inflight=0 oldest=0s (60s window)')


class MeteorTests(unittest.TestCase):
    def test_a_stats_line_passes_even_with_no_spots(self):
        journal = [STATS.format(s=0, ok=6, all=6), STATS.format(s=1, ok=4, all=5)]
        line = check.meteor_line(make_env(journal=lambda u, m: journal))
        self.assertEqual(line, check.Line(OK, 'meteor scatter',
                                          'decoding: 10 MSK144 decodes and 1 spot in the last 5 min'))

    def test_asks_for_5_minutes(self):
        asked = []
        check.meteor_line(make_env(journal=lambda u, m: asked.append(m) or []))
        self.assertEqual(asked, [5])

    def test_no_stats_line_waits_then_fails(self):
        self.assertEqual(check.meteor_line(make_env(service=lambda p: svc(age_s=240))).mark, WAIT)
        self.assertEqual(check.meteor_line(make_env(service=lambda p: svc(age_s=300))).mark, FAIL)

    def test_not_installed_is_absent(self):
        line = check.meteor_line(make_env(installed=lambda n: n != 'meteor-scatter'))
        self.assertEqual(line, check.Line(ABSENT, 'meteor scatter', 'not on this station'))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_check_science.py -q --override-ini addopts=`
Expected: every test fails with `AttributeError: module 'sigmond.commands.check' has no attribute 'psk_line'` (or
`wspr_line`, `meteor_line`).

- [ ] **Step 3: Write the lines**

Add to the imports of `lib/sigmond/commands/check.py`: `import re` (after `import pwd`),
`from datetime import datetime, timezone` (after the `dataclasses` import) and `from pathlib import Path` (after
that).  The import block then reads:

```python
import json
import os
import pwd
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple
```

After the probes section, insert:

```python
# ---------------------------------------------------------------------------
# Science: PSK, WSPR, meteor scatter
# ---------------------------------------------------------------------------


PSK_LOG_DIR = Path('/var/log/psk-recorder')
PSK_WINDOW_S = 15 * 60
PSK_TAIL_BYTES = 8 * 1024 * 1024
# decode_ft8:  "2026/05/07 12:34:56 -15 +0.50 14,074,131.2 ~ K1ABC W1XYZ EM26"
# jt9:         "260507 123456 14074000 ..."  (psk-recorder ch_tailer.py:58-59)
_FT8_SPOT = re.compile(r'^(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\s.*~')
_JT9_SPOT = re.compile(r'^(\d{2})(\d{2})(\d{2})\s+(\d{2})(\d{2})(\d{2})\s+\d+\s')

WSPR_UNITS = ('wspr-recorder@*.service',)
WSPR_WINDOW_MIN = 10
WSPR_YOUNG_S = 12 * 60
# spot_sink's cycle line, the one `smd watch wspr` parses (bin/smd cycle_re).
_WSPR_CYCLE = re.compile(
    r'cycle\s+UTC\s+\d{2}:\d{2}\s+(?:rx=\S+\s+)?\S+\s+(\d+)\s+spots\s+in\s+wspr\.spots'
    r'(?:,\s+\d+\s+noise\s+rows\s+in\s+wspr\.noise)?\s+\((\d+)\s+bands?,'
    r'[^)]*\)(?:.*?\bbands=\[([^\]]*)\])?')

METEOR_UNITS = ('meteor-scatter@*.service',)
METEOR_WINDOW_MIN = 5
# meteor-scatter core/recorder.py: "stats MSK144 rx=<id>: spots=N decodes=OK/ALL ..."
_METEOR_STATS = re.compile(r'\bstats MSK144 rx=\S+: spots=(\d+) decodes=(\d+)/(\d+)')


def plural(n: int, word: str) -> str:
    return f'{n} {word}' + ('' if n == 1 else 's')


def _spot_time(line: str) -> Optional[float]:
    m = _FT8_SPOT.match(line)
    if m:
        y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
    else:
        m = _JT9_SPOT.match(line)
        if not m:
            return None
        y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
        y += 2000
    try:
        return datetime(y, mo, d, hh, mm, ss, tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def count_psk_spots(reader, log_dir: Path, now: float,
                    window_s: float = PSK_WINDOW_S) -> dict:
    """{'ft8': n, 'ft4': n}: spot lines stamped (UTC) inside the window, summed
    over every receiver's `<radiod_id>-ft8.log` and `-ft4.log`."""
    counts = {'ft8': 0, 'ft4': 0}
    names = reader.listdir(log_dir)
    if names is None:
        return counts
    for name in names:
        for mode in counts:
            if not name.endswith(f'-{mode}.log'):
                continue
            text = reader.tail(log_dir / name, PSK_TAIL_BYTES) or ''
            for line in text.splitlines():
                t = _spot_time(line.strip())
                if t is not None and now - window_s <= t <= now + 60:
                    counts[mode] += 1
    return counts


def psk_line(env: Env, log_dir: Path = PSK_LOG_DIR) -> Line:
    label = 'PSK'
    if not env.installed('psk-recorder'):
        return Line(ABSENT, label, 'not on this station')
    svc = env.service('psk-recorder@*.service')
    bad = service_problem(label, svc, 'psk-recorder')
    if bad:
        return bad
    counts = count_psk_spots(env.reader, log_dir, env.now)
    if counts['ft8'] or counts['ft4']:
        return Line(OK, label, f"{counts['ft8']} FT8 and {counts['ft4']} FT4 spots "
                               f"in the last 15 min")
    if is_young(svc, PSK_WINDOW_S):
        return Line(WAIT, label, f'no spots yet; psk-recorder started '
                                 f'{minutes(svc.age_s or 0)} min ago, wait 15 min')
    return Line(FAIL, label, 'no FT8 or FT4 spot in 15 min.  Check the antenna and its '
                             'cable first; if they are fine, tell the fleet admin.')


def wspr_line(env: Env) -> Line:
    label = 'WSPR'
    if not env.installed('wspr-recorder'):
        return Line(ABSENT, label, 'not on this station')
    svc = env.service('wspr-recorder@*.service')
    bad = service_problem(label, svc, 'wspr-recorder')
    if bad:
        return bad
    spots, bands, band_count = 0, set(), 0
    for line in env.journal(WSPR_UNITS, WSPR_WINDOW_MIN):
        m = _WSPR_CYCLE.search(line)
        if not m:
            continue
        spots += int(m.group(1))
        band_count = max(band_count, int(m.group(2)))
        if m.group(3):
            bands.update(tok.split(':', 1)[0] for tok in m.group(3).split()
                         if tok.split(':', 1)[-1] not in ('0', ''))
    if spots:
        n_bands = len(bands) or band_count
        return Line(OK, label, f'{spots} spots in the last 10 min, on {n_bands} band'
                               f'{"" if n_bands == 1 else "s"}')
    if is_young(svc, WSPR_YOUNG_S):
        return Line(WAIT, label, f'no spots yet; wspr-recorder started '
                                 f'{minutes(svc.age_s or 0)} min ago, wait 12 min')
    return Line(FAIL, label, 'no WSPR spot in 10 min.  Check the antenna and its cable '
                             'first; if they are fine, tell the fleet admin.')


def meteor_line(env: Env) -> Line:
    label = 'meteor scatter'
    if not env.installed('meteor-scatter'):
        return Line(ABSENT, label, 'not on this station')
    svc = env.service('meteor-scatter@*.service')
    bad = service_problem(label, svc, 'meteor-scatter')
    if bad:
        return bad
    stats = [m for m in (_METEOR_STATS.search(x)
                         for x in env.journal(METEOR_UNITS, METEOR_WINDOW_MIN)) if m]
    if stats:
        spots = sum(int(m.group(1)) for m in stats)
        decodes = sum(int(m.group(2)) for m in stats)
        return Line(OK, label, f'decoding: {plural(decodes, "MSK144 decode")} and '
                               f'{plural(spots, "spot")} in the last 5 min')
    if is_young(svc, METEOR_WINDOW_MIN * 60):
        return Line(WAIT, label, f'meteor-scatter started {minutes(svc.age_s or 0)} min ago; '
                                 f'its first report comes within 5 min')
    return Line(FAIL, label, 'meteor-scatter has reported nothing in 5 min.  '
                             'Tell the fleet admin.')
```

Replace the `CHECKS` assignment at the end of the file with:

```python
CHECKS: List[Tuple[str, Callable[[Env], Optional[Line]]]] = [
    ('PSK', psk_line),
    ('WSPR', wspr_line),
    ('meteor scatter', meteor_line),
]
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `.venv/bin/pytest tests/test_check_core.py tests/test_check_probes.py tests/test_check_science.py -q --override-ini addopts=`
Expected: `50 passed`.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/commands/check.py tests/test_check_science.py
git commit -m "smd check: PSK, WSPR and meteor scatter lines

PSK counts decode_ft8 and jt9 spot lines stamped inside 15 min across
every receiver's -ft8.log and -ft4.log.  WSPR sums spot_sink's cycle
lines from 10 min of journal.  Meteor scatter wants one stats MSK144
line in 5 min.  Each waits while its service stays young and asks for
the antenna first when it fails (spec §4).

<attribution lines>"
```

- [ ] **Step 6: Mutation check**

Clear bytecode as in Task 1, Step 8.  Each change below, run alone and restored with
`git checkout -- lib/sigmond/commands/check.py`, must fail the named test in `tests/test_check_science.py`:

| Change | Fails |
|---|---|
| `PSK_WINDOW_S = 15 * 60` → `17 * 60` | `test_counts_spots_inside_15_minutes_only`, `test_young_service_with_no_spots_waits` |
| `WSPR_YOUNG_S = 12 * 60` → `13 * 60` | `test_zero_spot_cycles_wait_then_fail` |
| `METEOR_WINDOW_MIN = 5` → `6` | `test_asks_for_5_minutes`, `test_no_stats_line_waits_then_fails` |

---

### Task 4: The magnetometer, its freeze and its adapter (sigmond)

**Files:**
- Modify: `lib/sigmond/commands/check.py` (a new section; `CHECKS`)
- Create: `tests/test_check_mag.py`

**Interfaces:**
- Consumes: `Env.exists` (declared in Task 1, default `os.path.exists`), `Reader.tail`, `service_problem`,
  `is_young`, `FIX`.
- Produces: `mag_line(env, mag_dir=MAG_DIR, device=MAG_DEVICE) -> Line`,
  `last_mag_samples(reader, mag_dir, now, n=MAG_SAMPLES) -> List[dict]`,
  `mag_frozen(samples) -> Optional[str]`; constants `MAG_DIR`, `MAG_DEVICE`, `MAG_SAMPLES=60`, `MAG_FRESH_S=10`,
  `MAG_YOUNG_S=60`, `MAG_FIX`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_check_mag.py`:

```python
"""`smd check` magnetometer line, with the freeze rule of spec §4.1 and the
missing adapter of v3.72 #17 (tasks/spec-smd-check-v372.md §4)."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sigmond.commands import check
from sigmond.commands.check import ABSENT, FAIL, OK, WAIT

from tests.check_fakes import NOW, make_env, svc

FIX = 'Fix: sudo systemctl restart mag-recorder'


def sample(t, rt=23.31, x=-50.181, y=-4.442, z=15.945):
    ts = datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    return json.dumps({'ts': ts, 'rt': rt, 'x': x, 'y': y, 'z': z, 'reporter_id': 'AC0G=B4'})


class MagTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        self.device = self.dir / 'ttyMAG0'
        self.device.write_text('')

    def write(self, lines, day_t=NOW, mode='w'):
        name = f"samples-{datetime.fromtimestamp(day_t, tz=timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        with open(self.dir / name, mode) as fh:
            fh.write(''.join(ln + '\n' for ln in lines))

    def live(self, n=60, end=NOW - 1, **kw):
        return [sample(end - (n - 1 - i), x=-50.0 + i * 0.01, rt=23.0 + i * 0.001, **kw)
                for i in range(n)]

    def line(self, now=NOW, **kw):
        return check.mag_line(make_env(now=now, **kw), mag_dir=self.dir, device=str(self.device))

    def test_live_samples_pass(self):
        self.write(self.live())
        line = self.line()
        self.assertEqual(line.mark, OK)
        self.assertEqual(line.text, 'samples arriving, 23.1 C')

    def test_sixty_samples_at_zero_degrees_are_frozen(self):
        self.write([sample(NOW - 60 + i, rt=0.0, x=-50.0 + i) for i in range(60)])
        self.assertEqual(self.line(), check.Line(FAIL, 'magnetometer',
                                                 f'frozen: 0.00 C, no change in 60 s.  {FIX}'))

    def test_sixty_samples_with_unchanging_axes_are_frozen(self):
        self.write([sample(NOW - 60 + i, rt=23.0 + i * 0.01) for i in range(60)])
        line = self.line()
        self.assertEqual(line.mark, FAIL)
        self.assertTrue(line.text.startswith('frozen: x, y and z did not change in 60 s.'))

    def test_fifty_nine_frozen_then_one_live_passes(self):
        frozen = [sample(NOW - 61 + i, rt=0.0) for i in range(59)]
        self.write(frozen + [sample(NOW - 1, rt=23.4, x=-49.0)])
        self.assertEqual(self.line().mark, OK)

    def test_fewer_than_sixty_samples_cannot_be_called_frozen(self):
        self.write([sample(NOW - 30 + i, rt=0.0) for i in range(30)])
        self.assertEqual(self.line().mark, OK)

    def test_freeze_window_reaches_into_yesterdays_file(self):
        # Review Focus 1: at 00:00:30Z today's file holds 30 samples.
        now = datetime(2025, 10, 15, 0, 0, 30, tzinfo=timezone.utc).timestamp()
        self.write([sample(now - 60 + i, rt=0.0) for i in range(30)], day_t=now - 3600)
        self.write([sample(now - 30 + i, rt=0.0) for i in range(30)], day_t=now)
        self.assertEqual(self.line(now=now).mark, FAIL)

    def test_a_half_written_last_line_is_skipped(self):
        # Review Focus 2: mag-recorder is mid-write when smd check reads.
        self.write(self.live())
        with open(self.dir / f"samples-{datetime.fromtimestamp(NOW, tz=timezone.utc):%Y-%m-%d}.jsonl", 'a') as fh:
            fh.write('{"ts":"2025-10-14T12:00:00.0')
        self.assertEqual(self.line().mark, OK)

    def test_no_sample_for_ten_seconds_fails(self):
        self.write(self.live(end=NOW - 11))
        line = self.line()
        self.assertEqual(line, check.Line(FAIL, 'magnetometer', f'no sample for 11 s.  {FIX}'))

    def test_no_file_today_fails_unless_just_started(self):
        self.assertEqual(self.line().text, f'no sample today.  {FIX}')
        self.assertEqual(self.line(service=lambda p: svc(age_s=30)).mark, WAIT)

    def test_enabled_service_without_its_adapter_fails(self):
        self.device.unlink()
        self.write(self.live())
        line = self.line()
        self.assertEqual(line.mark, FAIL)
        self.assertEqual(line.text, f'no magnetometer adapter at {self.device}.  '
                                    f'Check its USB cable.  {FIX}')

    def test_a_disabled_service_without_its_adapter_reads_as_stopped(self):
        self.device.unlink()
        line = self.line(service=lambda p: svc(state='inactive', age_s=None, enabled=False))
        self.assertEqual(line.text, 'mag-recorder is inactive.  Fix: smd start mag-recorder')

    def test_not_installed_is_absent(self):
        self.assertEqual(self.line(installed=lambda n: False).mark, ABSENT)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_check_mag.py -q --override-ini addopts=`
Expected: every test fails with `AttributeError: module 'sigmond.commands.check' has no attribute 'mag_line'`.

- [ ] **Step 3: Write the line**

After the science section, insert:

```python
# ---------------------------------------------------------------------------
# Magnetometer
# ---------------------------------------------------------------------------

MAG_DIR = Path('/var/lib/mag-recorder')
MAG_DEVICE = '/dev/ttyMAG0'
MAG_SAMPLES = 60
MAG_FRESH_S = 10
MAG_YOUNG_S = 60
MAG_TAIL_BYTES = 64 * 1024
MAG_FIX = f'{FIX}sudo systemctl restart mag-recorder'


def _utc_day(t: float, days_back: int = 0) -> str:
    return datetime.fromtimestamp(t - days_back * 86400, tz=timezone.utc).strftime('%Y-%m-%d')


def _sample_time(sample: dict) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(sample['ts']).replace('Z', '+00:00')).timestamp()
    except (KeyError, ValueError):
        return None


def last_mag_samples(reader, mag_dir: Path, now: float, n: int = MAG_SAMPLES) -> List[dict]:
    """The newest `n` samples, oldest first.  Reads yesterday's file too when
    today's holds fewer (just after 00:00 UTC).  A line that does not parse
    (the half-written last line) is skipped."""
    samples: List[dict] = []
    for back in (0, 1):
        text = reader.tail(mag_dir / f'samples-{_utc_day(now, back)}.jsonl', MAG_TAIL_BYTES)
        got = []
        for line in (text or '').splitlines():
            try:
                s = json.loads(line)
            except ValueError:
                continue
            if isinstance(s, dict) and _sample_time(s) is not None:
                got.append(s)
        samples = got + samples
        if len(samples) >= n:
            break
    return samples[-n:]


def mag_frozen(samples: Sequence[dict]) -> Optional[str]:
    """Why the last 60 samples look frozen (spec §4.1), or None."""
    if len(samples) < MAG_SAMPLES:
        return None
    try:
        rts = [float(s['rt']) for s in samples]
        axes = {k: {float(s[k]) for s in samples} for k in ('x', 'y', 'z')}
    except (KeyError, TypeError, ValueError):
        return None
    if all(rt == 0.0 for rt in rts):
        return f'frozen: 0.00 C, no change in {MAG_SAMPLES} s'
    if all(len(v) == 1 for v in axes.values()):
        return f'frozen: x, y and z did not change in {MAG_SAMPLES} s'
    return None


def mag_line(env: Env, mag_dir: Path = MAG_DIR, device: str = MAG_DEVICE) -> Line:
    label = 'magnetometer'
    if not env.installed('mag-recorder'):
        return Line(ABSENT, label, 'not on this station')
    svc = env.service('mag-recorder.service')
    if svc.enabled and not env.exists(device):
        return Line(FAIL, label, f'no magnetometer adapter at {device}.  Check its USB cable.  '
                                 f'{MAG_FIX}')
    bad = service_problem(label, svc, 'mag-recorder')
    if bad:
        return bad
    samples = last_mag_samples(env.reader, mag_dir, env.now)
    newest = _sample_time(samples[-1]) if samples else None
    if newest is None or env.now - newest > MAG_FRESH_S:
        if is_young(svc, MAG_YOUNG_S):
            return Line(WAIT, label, 'mag-recorder just started; its first samples come '
                                     'within a minute')
        age = 'no sample today' if newest is None else f'no sample for {int(env.now - newest)} s'
        return Line(FAIL, label, f'{age}.  {MAG_FIX}')
    why = mag_frozen(samples)
    if why:
        return Line(FAIL, label, f'{why}.  {MAG_FIX}')
    return Line(OK, label, f"samples arriving, {float(samples[-1]['rt']):.1f} C")
```

Replace the `CHECKS` assignment with:

```python
CHECKS: List[Tuple[str, Callable[[Env], Optional[Line]]]] = [
    ('PSK', psk_line),
    ('WSPR', wspr_line),
    ('magnetometer', mag_line),
    ('meteor scatter', meteor_line),
]
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `.venv/bin/pytest tests/test_check_core.py tests/test_check_probes.py tests/test_check_science.py tests/test_check_mag.py -q --override-ini addopts=`
Expected: `62 passed`.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/commands/check.py tests/test_check_mag.py
git commit -m "smd check: the magnetometer line, its freeze and its adapter

Reads the newest 60 samples, from yesterday's file too just after
00:00 UTC, and skips a half-written line.  Frozen: all 60 at rt 0.0, or
x, y and z unchanged across all 60 (spec §4.1).  An enabled
mag-recorder without /dev/ttyMAG0 fails (v3.72 #17).

<attribution lines>"
```

- [ ] **Step 6: Mutation check**

Clear bytecode as in Task 1, Step 8.  Each change, alone and restored, must fail the named test:

| Change | Fails |
|---|---|
| `MAG_FRESH_S = 10` → `12` | `test_no_sample_for_ten_seconds_fails` |
| `if all(rt == 0.0 for rt in rts):` → `if any(…)` | `test_fifty_nine_frozen_then_one_live_passes` |
| `MAG_SAMPLES = 60` → `59` | `test_sixty_samples_at_zero_degrees_are_frozen`, `test_sixty_samples_with_unchanging_axes_are_frozen` |
| in `last_mag_samples`, `for back in (0, 1):` → `for back in (0,):` | `test_freeze_window_reaches_into_yesterdays_file` |
| `f'frozen: 0.00 C, no change` → `f'frozen: 0.00 °C, no change` (a degree sign back) | `test_sixty_samples_at_zero_degrees_are_frozen` |
| `if svc.enabled and not env.exists(device):` → `if False:` | `test_enabled_service_without_its_adapter_fails` |

---

### Task 5: GRAPE and storage (sigmond)

**Files:**
- Modify: `lib/sigmond/commands/check.py` (imports; `default_env`; a new section; `CHECKS`)
- Create: `tests/test_check_grape.py`

**Interfaces:**
- Consumes: `Reader.tail`, `Reader.newest_mtime`, `service_problem`, `is_young`, `minutes`; `Env.disk`,
  `Env.timestd_config`.
- Produces: `grape_line(env) -> Line`, `storage_line(env, path=STORAGE_PATH) -> Line`,
  `grape_channels(cfg: dict) -> List[str]`, `grape_roots(cfg: dict) -> List[Path]`,
  `read_timestd_config(reader=None, path=TIMESTD_CONFIG) -> Optional[dict]`, `_disk(path) -> (total, free)`;
  constants `TIMESTD_CONFIG`, `GRAPE_FRESH_S`, `GRAPE_YOUNG_S`, `STORAGE_PATH`, `STORAGE_MIN_FREE_PCT`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_check_grape.py`:

```python
"""`smd check` GRAPE and storage lines (tasks/spec-smd-check-v372.md §4).

GRAPE reads the channel list from hf-timestd's config and the newest file
under raw_buffer/<channel>/<YYYYMMDD>/, in the RAM tier and on disk."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sigmond.commands import check
from sigmond.commands.check import ABSENT, FAIL, OK, WAIT

from tests.check_fakes import NOW, make_env, svc

SIX = ['SHARED_2500', 'SHARED_5000', 'SHARED_10000', 'SHARED_15000', 'WWV_20000', 'WWV_25000']


def config(root, tiered=True, channels=SIX, engine='radiod', archive=True):
    chans = [{'frequency_hz': int(c.split('_')[1]) * 1000, 'description': c} for c in channels]
    return {'ka9q': {'source': engine},
            'recorder': {'production_data_root': str(root / 'var'),
                         'tiered_storage': tiered, 'hot_buffer_root': str(root / 'shm'),
                         'channel_group': {'timestd': {'archive': archive, 'channels': chans}}}}


class GrapeChannelTests(unittest.TestCase):
    def test_the_shipped_template_gives_six_channels(self):
        self.assertEqual(check.grape_channels(config(Path('/x'))), SIX)

    def test_a_channel_with_archive_off_is_not_expected(self):
        cfg = config(Path('/x'))
        cfg['recorder']['channel_group']['timestd']['channels'][0]['archive'] = False
        self.assertEqual(check.grape_channels(cfg), SIX[1:])

    def test_phase_engine_splits_shared_channels(self):
        cfg = config(Path('/x'), engine='phase-engine', channels=['SHARED_10000', 'WWV_20000'])
        self.assertEqual(check.grape_channels(cfg),
                         ['WWV_10000', 'WWVH_10000', 'BPM_10000', 'WWV_20000'])

    def test_roots_follow_tiered_storage(self):
        root = Path('/r')
        self.assertEqual(check.grape_roots(config(root)),
                         [root / 'shm' / 'raw_buffer', root / 'var' / 'raw_buffer'])
        self.assertEqual(check.grape_roots(config(root, tiered=False)),
                         [root / 'var' / 'raw_buffer', root / 'var'])


class GrapeLineTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        self.cfg = config(self.root)

    def put(self, channel, age_s, tier='shm', now=NOW):
        t = now - age_s
        day = datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y%m%d')
        d = self.root / tier / 'raw_buffer' / channel / day
        d.mkdir(parents=True, exist_ok=True)
        f = d / f'{int(t)}.bin.zst'
        f.write_text('x')
        os.utime(f, (t, t))

    def line(self, now=NOW, **kw):
        return check.grape_line(make_env(now=now, timestd_config=lambda: self.cfg, **kw))

    def test_every_channel_fresh_passes(self):
        for c in SIX:
            self.put(c, 300)
        self.assertEqual(self.line(), check.Line(OK, 'GRAPE', 'all 6 channels wrote a file in the last 11 min'))

    def test_a_file_already_moved_to_disk_counts(self):
        for c in SIX:
            self.put(c, 300, tier='var')
        self.assertEqual(self.line().mark, OK)

    def test_a_stale_channel_fails_by_name(self):
        for c in SIX[:-1]:
            self.put(c, 300)
        self.put('WWV_25000', 11 * 60 + 1)
        self.assertEqual(self.line(), check.Line(FAIL, 'GRAPE', 'no file in 11 min from WWV_25000.  '
                                                                'Tell the fleet admin.'))

    def test_young_recorder_waits_with_the_minutes_left(self):
        line = self.line(service=lambda p: svc(age_s=4 * 60))
        self.assertEqual(line, check.Line(WAIT, 'GRAPE',
                                          'first files due in about 8 min (hf-timestd started 4 min ago)'))

    def test_files_from_before_midnight_still_count(self):
        # Review Focus 1: at 00:03Z the newest file sits in yesterday's directory.
        now = datetime(2025, 10, 15, 0, 3, tzinfo=timezone.utc).timestamp()
        for c in SIX:
            self.put(c, 300, now=now)
        self.assertEqual(self.line(now=now).mark, OK)

    def test_no_config_fails_and_no_archived_channel_is_absent(self):
        self.cfg = None
        self.assertEqual(self.line().mark, FAIL)
        self.cfg = config(self.root, archive=False)
        self.assertEqual(self.line().mark, ABSENT)

    def test_stopped_recorder_fails(self):
        line = self.line(service=lambda p: svc(state='failed', age_s=None))
        self.assertEqual(line.text, 'hf-timestd is failed.  Fix: smd start hf-timestd')

    def test_asks_for_the_core_recorder(self):
        asked = []
        self.line(service=lambda p: asked.append(p) or svc())
        self.assertEqual(asked, ['timestd-core-recorder.service'])


class StorageTests(unittest.TestCase):
    def test_ten_percent_free_passes_and_less_fails(self):
        ok = check.storage_line(make_env(disk=lambda p: (1000, 100)))
        bad = check.storage_line(make_env(disk=lambda p: (1000, 99)))
        self.assertEqual(ok, check.Line(OK, 'storage', '/var/lib has 10 % free'))
        self.assertEqual(bad, check.Line(FAIL, 'storage',
                                         '/var/lib has only 9 % free.  Tell the fleet admin.'))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_check_grape.py -q --override-ini addopts=`
Expected: every test fails with `AttributeError: module 'sigmond.commands.check' has no attribute 'grape_channels'`
(or `grape_roots`, `grape_line`, `storage_line`).

- [ ] **Step 3: Write the lines**

Add `import shutil` to the imports (after `import re`).  Replace `default_env` with:

```python
def default_env() -> Env:
    reader = Reader()
    return Env(installed=_installed, service=service_state, reader=reader,
               journal=read_journal, disk=_disk,
               timestd_config=lambda: read_timestd_config(reader))
```

After the magnetometer section, insert:

```python
# ---------------------------------------------------------------------------
# GRAPE and storage
# ---------------------------------------------------------------------------


TIMESTD_CONFIG = Path('/etc/hf-timestd/timestd-config.toml')
GRAPE_FRESH_S = 11 * 60
GRAPE_YOUNG_S = 12 * 60
STORAGE_PATH = '/var/lib'
STORAGE_MIN_FREE_PCT = 10.0
_PHASE_ENGINE_SHARED_HZ = (2500000, 5000000, 10000000, 15000000)


def read_timestd_config(reader=None, path: Path = TIMESTD_CONFIG) -> Optional[dict]:
    """hf-timestd's config, or None when absent.  The file is 0640
    timestd:timestd (clients/hftimestd.py), so this reads it through the
    Reader's sudo fallback."""
    import tomllib
    text = (reader or Reader()).tail(path, 4 * 1024 * 1024)
    return None if text is None else tomllib.loads(text)


def _channel_dir(description: str) -> str:
    """hf_timestd.paths.channel_name_to_dir: canonical SHARED_/WWV_<kHz> names
    pass through; anything else has its spaces turned into underscores."""
    return description.replace(' ', '_')


def grape_channels(cfg: dict) -> List[str]:
    """The raw_buffer directory names hf-timestd archives, from its config:
    every [[recorder.channel_group.*.channels]] entry whose archive flag
    (channel, then group, then [recorder]) is true.  In phase-engine mode a
    SHARED channel on 2.5, 5, 10 or 15 MHz becomes WWV_, WWVH_ and BPM_
    (core_recorder_v2.py _expand_channel_groups and the expansion before it)."""
    rec = cfg.get('recorder', {}) or {}
    engine = (cfg.get('ka9q', {}) or {}).get('source') or rec.get('engine', 'radiod')
    out = []
    for group in (rec.get('channel_group', {}) or {}).values():
        defaults = {k: v for k, v in group.items() if k != 'channels'}
        for ch in group.get('channels', []) or []:
            spec = {**defaults, **ch}
            if not spec.get('archive', rec.get('archive', True)):
                continue
            freq = int(spec.get('frequency_hz') or 0)
            desc = spec.get('description') or f'{freq / 1e6:.3f} MHz'
            if (engine == 'phase-engine' and freq in _PHASE_ENGINE_SHARED_HZ
                    and desc.startswith('SHARED')):
                out += [f'{t}_{freq // 1000}' for t in ('WWV', 'WWVH', 'BPM')]
            else:
                out.append(_channel_dir(desc))
    return out


def grape_roots(cfg: dict) -> List[Path]:
    """Where hf-timestd writes raw IQ.  With tiered storage (the shipped
    default) a file lands first in <hot_buffer_root>/raw_buffer and moves to
    <data_root>/raw_buffer minutes later; without it the writer uses
    <data_root>/<channel> (binary_archive_writer.py archive_dir)."""
    rec = cfg.get('recorder', {}) or {}
    data_root = Path(rec.get('production_data_root', '/var/lib/timestd'))
    roots = [data_root / 'raw_buffer']
    if rec.get('tiered_storage', False):
        roots.insert(0, Path(rec.get('hot_buffer_root', '/dev/shm/timestd')) / 'raw_buffer')
    else:
        roots.append(data_root)
    return roots


def grape_line(env: Env) -> Line:
    label = 'GRAPE'
    if not env.installed('hf-timestd'):
        return Line(ABSENT, label, 'not on this station')
    cfg = env.timestd_config()
    if cfg is None:
        return Line(FAIL, label, f'hf-timestd has no config at {TIMESTD_CONFIG}.  '
                                 f'Tell the fleet admin.')
    channels = grape_channels(cfg)
    if not channels:
        return Line(ABSENT, label, 'hf-timestd archives no channel on this station')
    svc = env.service('timestd-core-recorder.service')
    bad = service_problem(label, svc, 'hf-timestd')
    if bad:
        return bad
    days = [datetime.fromtimestamp(env.now - back * 86400, tz=timezone.utc).strftime('%Y%m%d')
            for back in (0, 1)]
    stale = []
    for ch in channels:
        newest = None
        for root in grape_roots(cfg):
            for day in days:
                m = env.reader.newest_mtime(root / ch / day)
                if m is not None:
                    newest = m if newest is None else max(newest, m)
        if newest is None or env.now - newest > GRAPE_FRESH_S:
            stale.append(ch)
    if not stale:
        return Line(OK, label, f'all {len(channels)} channels wrote a file in the last 11 min')
    if is_young(svc, GRAPE_YOUNG_S):
        due = minutes(GRAPE_YOUNG_S - (svc.age_s or 0))
        return Line(WAIT, label, f'first files due in about {due} min (hf-timestd started '
                                 f'{minutes(svc.age_s or 0)} min ago)')
    return Line(FAIL, label, f'no file in 11 min from {", ".join(stale)}.  '
                             f'Tell the fleet admin.')


def storage_line(env: Env, path: str = STORAGE_PATH) -> Line:
    label = 'storage'
    total, free = env.disk(path)
    pct = 100.0 * free / total if total else 0.0
    if pct >= STORAGE_MIN_FREE_PCT:
        return Line(OK, label, f'{path} has {int(pct)} % free')
    return Line(FAIL, label, f'{path} has only {int(pct)} % free.  Tell the fleet admin.')


def _disk(path: str) -> Tuple[int, int]:
    usage = shutil.disk_usage(path)
    return usage.total, usage.free
```

Replace the `CHECKS` assignment with:

```python
CHECKS: List[Tuple[str, Callable[[Env], Optional[Line]]]] = [
    ('PSK', psk_line),
    ('WSPR', wspr_line),
    ('magnetometer', mag_line),
    ('GRAPE', grape_line),
    ('storage', storage_line),
    ('meteor scatter', meteor_line),
]
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `.venv/bin/pytest tests/test_check_core.py tests/test_check_probes.py tests/test_check_science.py tests/test_check_mag.py tests/test_check_grape.py -q --override-ini addopts=`
Expected: `75 passed`.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/commands/check.py tests/test_check_grape.py
git commit -m "smd check: GRAPE and storage lines

GRAPE builds the channel list from timestd-config.toml as hf-timestd
does (description, archive flag, phase-engine split) and wants a file
under 11 min old for each, in the RAM tier or on disk, in today's or
yesterday's YYYYMMDD directory.  Storage wants 10 % free on /var/lib.
The spec named only the disk path; the shipped tiered storage writes
to /dev/shm first (plan decision 1).

<attribution lines>"
```

- [ ] **Step 6: Mutation check**

Clear bytecode as in Task 1, Step 8.  Each change, alone and restored, must fail the named test:

| Change | Fails |
|---|---|
| `GRAPE_FRESH_S = 11 * 60` → `12 * 60` | `test_a_stale_channel_fails_by_name` |
| `GRAPE_YOUNG_S = 12 * 60` → `13 * 60` | `test_young_recorder_waits_with_the_minutes_left` |
| in `grape_line`, `for back in (0, 1)]` → `for back in (0,)]` | `test_files_from_before_midnight_still_count` |
| `STORAGE_MIN_FREE_PCT = 10.0` → `9.0` | `test_ten_percent_free_passes_and_less_fails` |

---

### Task 6: Uploads: the site sink switch and the backlog (sigmond)

**Files:**
- Modify: `lib/sigmond/commands/check.py` (`default_env`; a new section; `CHECKS`)
- Create: `tests/test_check_uploads.py`

**Interfaces:**
- Consumes: `sigmond.coordination.load_coordination`, `Coordination.uploads.mode` / `.reason`,
  `sigmond.paths.COORDINATION_PATH`, `sigmond.heartbeat.HeartbeatPaths`, `ReaderUnavailable`, `_read_backlog`.
- Produces: `uploads_line(env) -> Line`, `read_backlog(run=subprocess.run, euid=os.geteuid) -> dict`,
  `_coordination()`, `_coordination_age_s() -> Optional[float]`; constants `UPLOAD_ACK_S`, `NEW_STATION`,
  `HEARTBEAT_TABLE`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_check_uploads.py`:

```python
"""`smd check` uploads line: the site sink switch and hs-uploader's backlog
(tasks/spec-smd-check-v372.md §4)."""
import subprocess
import unittest
from datetime import datetime, timezone
from unittest import mock

from sigmond.commands import check
from sigmond.commands.check import FAIL, OK, WAIT
from sigmond.coordination import Coordination, Uploads

from tests.check_fakes import NOW, make_env

NEW = 'new station: the site sink switch stays off until someone runs smd sink upload'


def iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def coord(mode='upload', reason=''):
    return Coordination(uploads=Uploads(mode=mode, reason=reason))


def backlog(ack_age_s=60, dead=0, heartbeat_age_s=None):
    cursors = [{'source_id': 'psk', 'dest_id': 'pskreporter', 'table_name': 'psk.spots',
                'last_ack': iso(NOW - ack_age_s), 'cursor_len': 8}] if ack_age_s is not None else []
    if heartbeat_age_s is not None:
        cursors.append({'source_id': 'hb', 'dest_id': 'gw', 'table_name': 'station.heartbeat',
                        'last_ack': iso(NOW - heartbeat_age_s), 'cursor_len': 8})
    return {'readable': True, 'cursors': cursors,
            'pipelines': [{'name': 'psk-pskreporter', 'deliverable_count': 0, 'dead_letter_count': dead,
                           'dead_letter_recent_count': dead, 'dead_letter_newest': None}]}


def line(c=None, b=None, age=None):
    env = make_env(coordination=lambda: c or coord(), backlog=lambda: b or backlog(),
                   coordination_age_s=lambda: age)
    return check.uploads_line(env)


class UploadsTests(unittest.TestCase):
    def test_new_station_off_waits_and_names_the_command(self):
        self.assertEqual(line(coord('discard', NEW)),
                         check.Line(WAIT, 'uploads', 'off, new station: check the lines above, '
                                                     'then run: smd sink upload'))

    def test_off_for_any_other_reason_fails(self):
        out = line(coord('discard', 'bench provisioning for DASI-021'))
        self.assertEqual(out, check.Line(FAIL, 'uploads', 'off (bench provisioning for DASI-021).  '
                                                          'Ask the fleet admin.'))

    def test_hold_fails(self):
        self.assertEqual(line(coord('hold', 'antenna work')).text, 'hold (antenna work).  Ask the fleet admin.')

    def test_upload_with_a_recent_delivery_passes(self):
        self.assertEqual(line(), check.Line(OK, 'uploads', 'on; last delivery 1 min ago, no failure in 24 h'))

    def test_a_recent_dead_letter_fails(self):
        out = line(b=backlog(dead=3))
        self.assertEqual(out, check.Line(FAIL, 'uploads', '3 uploads failed for good in the last 24 h '
                                                          '(psk-pskreporter).  Tell the fleet admin.'))

    def test_no_delivery_in_30_minutes_fails(self):
        self.assertEqual(line(b=backlog(ack_age_s=31 * 60)).mark, FAIL)
        self.assertEqual(line(b=backlog(ack_age_s=30 * 60)).mark, OK)

    def test_the_heartbeat_alone_proves_nothing(self):
        out = line(b=backlog(ack_age_s=None, heartbeat_age_s=60))
        self.assertEqual(out, check.Line(FAIL, 'uploads', 'on, but nothing delivered in 30 min.  '
                                                          'Tell the fleet admin.'))

    def test_a_switch_turned_on_minutes_ago_waits(self):
        out = line(b=backlog(ack_age_s=None), age=5 * 60)
        self.assertEqual(out, check.Line(WAIT, 'uploads', 'on since 5 min ago; the first delivery '
                                                          'comes within 30 min'))

    def test_an_unreadable_backlog_fails_with_its_reason(self):
        out = line(b={'readable': False, 'reason': 'OperationalError: unable to open database file'})
        self.assertEqual(out.text, 'cannot read the upload backlog: OperationalError: unable to open '
                                   'database file')


class ReadBacklogTests(unittest.TestCase):
    def test_asks_sudo_once_when_the_store_refuses_this_user(self):
        refused = {'readable': False, 'reason': 'OperationalError: unable to open database file'}
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout='{"readable": true, "pipelines": [], "cursors": []}')
        with mock.patch('sigmond.heartbeat._read_backlog', return_value=refused), \
                mock.patch('os.path.exists', return_value=True), \
                mock.patch('os.access', return_value=False):
            out = check.read_backlog(run=run, euid=lambda: 1000)
        self.assertEqual(out, {'readable': True, 'pipelines': [], 'cursors': []})
        self.assertEqual(calls[0][:3], ['sudo', '-n', 'env'])
        self.assertIn('hs_uploader.backlog', calls[0])

    def test_a_reader_failure_becomes_unreadable_not_an_exception(self):
        from sigmond.heartbeat import ReaderUnavailable
        with mock.patch('sigmond.heartbeat._read_backlog', side_effect=ReaderUnavailable('timed out')):
            self.assertEqual(check.read_backlog(), {'readable': False, 'reason': 'timed out'})


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_check_uploads.py -q --override-ini addopts=`
Expected: every test fails with `AttributeError: module 'sigmond.commands.check' has no attribute 'uploads_line'`
(or `read_backlog`).

- [ ] **Step 3: Write the line**

Replace `default_env` with:

```python
def default_env() -> Env:
    reader = Reader()
    return Env(installed=_installed, service=service_state, reader=reader,
               journal=read_journal, disk=_disk,
               timestd_config=lambda: read_timestd_config(reader),
               coordination=_coordination, coordination_age_s=_coordination_age_s,
               backlog=read_backlog)
```

After the GRAPE and storage section, insert:

```python
# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------

UPLOAD_ACK_S = 30 * 60
NEW_STATION = 'new station'
# The heartbeat ships through hs-uploader too, every five minutes, whatever
# the site sink switch says.  Its cursor proves nothing about spots or data.
HEARTBEAT_TABLE = 'station.heartbeat'


def read_backlog(run=subprocess.run, euid=os.geteuid) -> dict:
    """hs_uploader.backlog's summary of watermarks.db, read out of process by
    heartbeat._read_backlog.  When the store refuses this user, ask once
    more through `sudo -n`."""
    from ..heartbeat import HeartbeatPaths, ReaderUnavailable, _read_backlog
    paths = HeartbeatPaths()
    try:
        out = _read_backlog(paths)
    except ReaderUnavailable as exc:
        return {'readable': False, 'reason': str(exc)}
    db = paths.watermarks_db
    if out.get('readable') or euid() == 0 or not os.path.exists(db) or os.access(db, os.R_OK):
        return out
    try:
        r = run(['sudo', '-n', 'env', f'PYTHONPATH={paths.hs_uploader_src}', sys.executable,
                 '-m', 'hs_uploader.backlog', db],
                capture_output=True, text=True, errors='replace', timeout=20, check=False)
        return json.loads(r.stdout) if r.returncode == 0 else out
    except (OSError, subprocess.SubprocessError, ValueError):
        return out


def _parse_ts(ts) -> Optional[float]:
    try:
        t = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp()


def uploads_line(env: Env) -> Line:
    label = 'uploads'
    up = env.coordination().uploads
    reason = (up.reason or '').strip()
    if up.mode == 'discard':
        if reason.startswith(NEW_STATION):
            return Line(WAIT, label, f'off, new station: check the lines above, then '
                                     f'{RUN}smd sink upload')
        return Line(FAIL, label, f'off ({reason or "no reason given"}).  '
                                 f'Ask the fleet admin.')
    if up.mode == 'hold':
        return Line(FAIL, label, f'hold ({reason or "no reason given"}).  Ask the fleet admin.')
    backlog = env.backlog()
    if not backlog.get('readable'):
        return Line(FAIL, label, f'cannot read the upload backlog: '
                                 f'{backlog.get("reason", "no reason given")}')
    dead = [(p.get('name', '?'), int(p.get('dead_letter_recent_count') or 0))
            for p in backlog.get('pipelines', [])]
    dead = [(n, c) for n, c in dead if c]
    if dead:
        total = sum(c for _, c in dead)
        names = ', '.join(n for n, _ in dead)
        return Line(FAIL, label, f'{total} upload{"s" if total != 1 else ""} failed for good '
                                 f'in the last 24 h ({names}).  Tell the fleet admin.')
    acks = [_parse_ts(c.get('last_ack')) for c in backlog.get('cursors', [])
            if c.get('table_name') != HEARTBEAT_TABLE]
    acks = [a for a in acks if a is not None]
    newest = max(acks) if acks else None
    if newest is not None and env.now - newest <= UPLOAD_ACK_S:
        return Line(OK, label, f'on; last delivery {minutes(env.now - newest)} min ago, '
                               f'no failure in 24 h')
    switched = env.coordination_age_s()
    if switched is not None and switched < UPLOAD_ACK_S:
        return Line(WAIT, label, f'on since {minutes(switched)} min ago; the first delivery '
                                 f'comes within 30 min')
    return Line(FAIL, label, 'on, but nothing delivered in 30 min.  Tell the fleet admin.')


def _coordination():
    from ..coordination import load_coordination
    from ..paths import COORDINATION_PATH
    return load_coordination(COORDINATION_PATH)


def _coordination_age_s() -> Optional[float]:
    """Seconds since coordination.toml last changed: how long the site sink
    switch has stood where it stands."""
    from ..paths import COORDINATION_PATH
    try:
        return time.time() - COORDINATION_PATH.stat().st_mtime
    except OSError:
        return None
```

Replace the `CHECKS` assignment with:

```python
CHECKS: List[Tuple[str, Callable[[Env], Optional[Line]]]] = [
    ('PSK', psk_line),
    ('WSPR', wspr_line),
    ('magnetometer', mag_line),
    ('GRAPE', grape_line),
    ('storage', storage_line),
    ('uploads', uploads_line),
    ('meteor scatter', meteor_line),
]
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `.venv/bin/pytest tests/test_check_core.py tests/test_check_probes.py tests/test_check_science.py tests/test_check_mag.py tests/test_check_grape.py tests/test_check_uploads.py -q --override-ini addopts=`
Expected: `86 passed`.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/commands/check.py tests/test_check_uploads.py
git commit -m "smd check: the uploads line

Off with the reason 'new station' waits and names smd sink upload; off
or hold for any other reason fails.  On, it wants a delivery in 30 min
and no dead letter in 24 h, from hs_uploader.backlog through
heartbeat._read_backlog.  The heartbeat's own cursor proves nothing,
and a switch turned on minutes ago waits (plan decision 4).

<attribution lines>"
```

- [ ] **Step 6: Mutation check**

Clear bytecode as in Task 1, Step 8.  Each change, alone and restored, must fail the named test:

| Change | Fails |
|---|---|
| `UPLOAD_ACK_S = 30 * 60` → `31 * 60` | `test_no_delivery_in_30_minutes_fails` |
| delete `if c.get('table_name') != HEARTBEAT_TABLE` from the `acks` comprehension | `test_the_heartbeat_alone_proves_nothing` |
| `if reason.startswith(NEW_STATION):` → `if True:` | `test_off_for_any_other_reason_fails` |

---

### Task 7: The VM's own pages, and the full list (sigmond)

**Files:**
- Modify: `lib/sigmond/commands/check.py` (imports; `default_env`; a new section; `CHECKS`)
- Create: `tests/test_check_pages.py`

**Interfaces:**
- Consumes: `Env.http`, `Env.installed`.
- Produces: `http_status(url, timeout=HTTP_TIMEOUT_S, urlopen=urllib.request.urlopen) -> (Optional[int], str)`,
  `page_check(label, port, needs) -> Callable[[Env], Optional[Line]]`; constants `HTTP_TIMEOUT_S = 3.0`,
  `PAGES = (('receiver page', 8081, None), ('station pages', 8000, None), ('magnetometer page', 8082, 'mag-recorder'))`.
  The final `CHECKS` order: PSK, WSPR, magnetometer, GRAPE, storage, uploads, meteor scatter, the three pages.
  Task 8's host script relies on those three page labels, on `Fix: ` / `run: `, and on `render_line`'s columns.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_check_pages.py`:

```python
"""`smd check` VM page lines and the full list of lines
(tasks/spec-smd-check-v372.md §2, §4)."""
import io
import unittest
import urllib.error

from sigmond.commands import check
from sigmond.commands.check import FAIL, OK

from tests.check_fakes import make_env


def pages(env):
    return [ln for ln in check.run_checks(env, [c for c in check.CHECKS if 'page' in c[0]])]


class PageTests(unittest.TestCase):
    def test_answering_pages_pass_on_127_0_0_1(self):
        asked = []
        env = make_env(http=lambda url: asked.append(url) or (200, ''))
        self.assertEqual(pages(env), [
            check.Line(OK, 'receiver page', 'http://127.0.0.1:8081 answers'),
            check.Line(OK, 'station pages', 'http://127.0.0.1:8000 answers'),
            check.Line(OK, 'magnetometer page', 'http://127.0.0.1:8082 answers')])
        self.assertEqual(asked, ['http://127.0.0.1:8081/', 'http://127.0.0.1:8000/',
                                 'http://127.0.0.1:8082/'])

    def test_no_magnetometer_page_line_without_mag_recorder(self):
        env = make_env(installed=lambda n: n != 'mag-recorder', http=lambda url: (200, ''))
        self.assertEqual([ln.label for ln in pages(env)], ['receiver page', 'station pages'])

    def test_a_refused_or_wrong_status_fails(self):
        env = make_env(http=lambda url: (None, '[Errno 111] Connection refused') if '8081' in url
                       else (503, ''))
        lines = pages(env)
        self.assertEqual(lines[0], check.Line(FAIL, 'receiver page',
                                              'http://127.0.0.1:8081 does not answer: '
                                              '[Errno 111] Connection refused'))
        self.assertEqual(lines[1].text, 'http://127.0.0.1:8000 does not answer: status 503')


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class HttpStatusTests(unittest.TestCase):
    def test_status_codes_and_reasons(self):
        self.assertEqual(check.http_status('http://x/', urlopen=lambda u, timeout: _Resp()), (200, ''))

        def http_error(u, timeout):
            raise urllib.error.HTTPError(u, 404, 'nf', {}, None)
        self.assertEqual(check.http_status('http://x/', urlopen=http_error), (404, ''))

        def refused(u, timeout):
            raise urllib.error.URLError(ConnectionRefusedError(111, 'Connection refused'))
        status, why = check.http_status('http://x/', urlopen=refused)
        self.assertIsNone(status)
        self.assertIn('Connection refused', why)


class AllLinesTests(unittest.TestCase):
    def test_the_lines_come_in_the_order_the_installer_reads(self):
        self.assertEqual([label for label, _ in check.CHECKS],
                         ['PSK', 'WSPR', 'magnetometer', 'GRAPE', 'storage', 'uploads',
                          'meteor scatter', 'receiver page', 'station pages', 'magnetometer page'])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_check_pages.py -q --override-ini addopts=`
Expected: `5 failed`.  `PageTests` get empty lists (no page check registered yet); `HttpStatusTests` fails with
`AttributeError: … no attribute 'http_status'`; `AllLinesTests` misses the three page labels.

- [ ] **Step 3: Write the lines**

Add `import urllib.error` and `import urllib.request` to the imports (after `import time`).  Replace `default_env`
with:

```python
def default_env() -> Env:
    reader = Reader()
    return Env(installed=_installed, service=service_state, reader=reader,
               journal=read_journal, disk=_disk,
               timestd_config=lambda: read_timestd_config(reader),
               coordination=_coordination, coordination_age_s=_coordination_age_s,
               backlog=read_backlog, http=http_status)
```

After the uploads section, insert:

```python
# ---------------------------------------------------------------------------
# The VM's own pages
# ---------------------------------------------------------------------------


HTTP_TIMEOUT_S = 3.0
PAGES = (('receiver page', 8081, None),
         ('station pages', 8000, None),
         ('magnetometer page', 8082, 'mag-recorder'))


def http_status(url: str, timeout: float = HTTP_TIMEOUT_S,
                urlopen=urllib.request.urlopen) -> Tuple[Optional[int], str]:
    """(status, '') when the server answers, (None, why) when it does not."""
    try:
        with urlopen(url, timeout=timeout) as resp:
            return resp.status, ''
    except urllib.error.HTTPError as exc:
        return exc.code, ''
    except Exception as exc:  # noqa: BLE001 -- refused, timed out, reset, ...
        reason = getattr(exc, 'reason', None) or exc
        return None, str(reason)


def page_check(label: str, port: int, needs: Optional[str]):
    def check(env: Env) -> Optional[Line]:
        if needs and not env.installed(needs):
            return None
        url = f'http://127.0.0.1:{port}'
        status, why = env.http(url + '/')
        if status == 200:
            return Line(OK, label, f'{url} answers')
        return Line(FAIL, label, f'{url} does not answer: '
                                 f'{why or f"status {status}"}')
    return check
```

Replace the `CHECKS` assignment with:

```python
CHECKS: List[Tuple[str, Callable[[Env], Optional[Line]]]] = [
    ('PSK', psk_line),
    ('WSPR', wspr_line),
    ('magnetometer', mag_line),
    ('GRAPE', grape_line),
    ('storage', storage_line),
    ('uploads', uploads_line),
    ('meteor scatter', meteor_line),
    *[(label, page_check(label, port, needs)) for label, port, needs in PAGES],
]
```

The finished import block reads:

```python
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `.venv/bin/pytest tests/test_check_core.py tests/test_check_probes.py tests/test_check_science.py tests/test_check_mag.py tests/test_check_grape.py tests/test_check_uploads.py tests/test_check_pages.py -q --override-ini addopts=`
Expected: `91 passed`.

- [ ] **Step 5: Run it on this machine**

Run: `time SIGMOND_NO_VENV_REEXEC=1 PYTHONPATH=lib .venv/bin/python bin/smd check; echo rc=$?`
Expected on the devbox (no clients, no web pages): five `--` lines, ` ok    storage`, ` FAIL  uploads        cannot
read the upload backlog: …` (no hs-uploader here), two ` FAIL` page lines with `Connection refused`,
`FAIL  (3 failing)`, `rc=1`, nothing past ASCII, well
under a second.  The planner saw exactly that in 0.3 s.

- [ ] **Step 6: Re-run the timing-judge test with every line registered**

Run: `.venv/bin/pytest tests/test_check_core.py::SmdRegistrationTests -q --override-ini addopts=`
Expected: `2 passed`.  Now `make_env()` runs every line function against empty fakes, so a line that imported the
timing judge would surface its `AssertionError` text in a line, and the test would fail.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest tests -q --override-ini addopts=`
Expected: all pass (the planner saw 4455 passed, 17 skipped, 171 subtests passed with Tasks 1-8 applied; about three minutes).

- [ ] **Step 8: Commit**

```bash
git add lib/sigmond/commands/check.py tests/test_check_pages.py
git commit -m "smd check: the VM's own pages, and the full line list

GET on 127.0.0.1:8081, :8000 and, with mag-recorder installed, :8082,
3 s each; 200 passes.  The lines come in the order of spec §2.

<attribution lines>"
```

- [ ] **Step 9: Mutation check**

Clear bytecode as in Task 1, Step 8.  In `page_check`, change `        if needs and not env.installed(needs):` to
`        if False:`.  Expected: `test_no_magnetometer_page_line_without_mag_recorder` fails.  Restore with
`git checkout -- lib/sigmond/commands/check.py`.

---

### Task 8: `sigmond-check` on the host, and pm-align carries it (sigmond)

**Files:**
- Modify: `scripts/proxmox/sigmond-wizard.sh:3342` (insert after `chmod +x /usr/local/bin/sigmond-vm`)
- Modify: `scripts/proxmox/pm-align.py:260` (a new set before `SETUP = …`), `scripts/proxmox/pm-align.py:327`
  (`desired_files`, before `out.extend(relay_units(src.wizard, vmid))`)
- Create: `tests/test_sigmond_check_host.py`
- Modify: `tests/test_pm_align.py` (append at the end)

**Interfaces:**
- Consumes: `smd check --no-verdict` (Tasks 1-7): lines that start ` ok    `, ` wait  `, ` FAIL  ` or ` --    `, each
  fix on the following line indented 22 spaces; labels
  `receiver page`, `station pages`, `magnetometer page`; commands after `Fix: ` and `run: `.  `sigmond-vm` (wizard
  3288-3342) passes the VM command's output and exit status through.  `pm_align.extract_heredocs`.
- Produces: `/usr/local/bin/sigmond-check` (mode 0755) on the host, exit 0/2/1; environment overrides
  `SIGMOND_CHECK_BRIDGE` (default `vmbr0`) and `SIGMOND_CHECK_VM_TIMEOUT` (default 60 s).
  `pm_align.WIZARD_HEREDOCS_SINCE_V372 = {"/usr/local/bin/sigmond-check": 0o755}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sigmond_check_host.py`:

```python
"""`sigmond-check`, the host half of the on-site check
(tasks/spec-smd-check-v372.md §3, §5, §8).

The script under test is the heredoc the wizard writes to
/usr/local/bin/sigmond-check, cut out of the shipped wizard with pm-align's
own reader.  Stubs on PATH play the VM (`sigmond-vm`), the network (`ip`,
`iptables`, `sysctl`, `systemctl`) and the browser (`curl`); a scene
directory tells each stub what to answer."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROXMOX = REPO / "scripts" / "proxmox"
_spec = importlib.util.spec_from_file_location("pm_align_for_check", PROXMOX / "pm-align.py")
pm_align = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pm_align
_spec.loader.exec_module(pm_align)

SCRIPT = pm_align.extract_heredocs((PROXMOX / "sigmond-wizard.sh").read_text())["/usr/local/bin/sigmond-check"]

VM_LINES = """\
 ok    PSK            214 FT8 and 31 FT4 spots in the last 15 min
 FAIL  magnetometer   frozen: 0.00 C, no change in 60 s.
                      Fix: sudo systemctl restart mag-recorder
 wait  uploads        off, new station: check the lines above, then run: smd sink upload
 ok    receiver page  http://127.0.0.1:8081 answers
 FAIL  station pages  http://127.0.0.1:8000 does not answer: [Errno 111] Connection refused
                      Fix: nothing here; the host line below says more
 ok    magnetometer page  http://127.0.0.1:8082 answers
"""
PSK_OK = " ok    PSK            fine\n"
V4 = "2: vmbr0    inet 192.168.1.50/24 brd 192.168.1.255 scope global vmbr0\\       valid_lft forever"
V6 = ("2: vmbr0    inet6 2601:db8::abcd/64 scope global temporary dynamic \\       valid_lft 1sec\n"
      "2: vmbr0    inet6 2601:db8::50/64 scope global dynamic mngtmpaddr \\       valid_lft 1sec")
NAT = "\n".join(["-P PREROUTING ACCEPT"] + [
    f"-A PREROUTING -p tcp -m tcp --dport {p} -j DNAT --to-destination 10.99.0.2:{p}"
    for p in (8000, 8081, 8082, 8765)])

STUBS = {
    "sigmond-vm": 'cat "$SCENE/vm.out"; exit "$(cat "$SCENE/vm.rc")"',
    "ip": 'case "$1" in -4) cat "$SCENE/ip4" ;; -6) cat "$SCENE/ip6" ;; esac',
    "iptables": 'cat "$SCENE/nat"; exit "$(cat "$SCENE/nat.rc")"',
    "sysctl": 'cat "$SCENE/fwd"',
    "curl": ('for url; do :; done\n'
             'code=$(awk -v u="$url" \'$1 == u {print $2}\' "$SCENE/curl")\n'
             'printf %s "${code:-000}"'),
    "systemctl": '[ -e "$SCENE/relay" ]',
}


def scene(tmp_path, vm=VM_LINES, vm_rc=1, ip4=V4, ip6="", nat=NAT, nat_rc=0, fwd="1",
          curl=(), relay=False):
    s = tmp_path / "scene"
    s.mkdir()
    for name, text in (("vm.out", vm), ("vm.rc", str(vm_rc)), ("ip4", ip4), ("ip6", ip6),
                       ("nat", nat), ("nat.rc", str(nat_rc)), ("fwd", fwd),
                       ("curl", "".join(f"{u} {c}\n" for u, c in curl))):
        (s / name).write_text(text)
    if relay:
        (s / "relay").write_text("")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in STUBS.items():
        (bin_ / name).write_text("#!/bin/bash\n" + body + "\n")
        (bin_ / name).chmod(0o755)
    script = tmp_path / "sigmond-check"
    script.write_text(SCRIPT)
    return s, bin_, script


def run(tmp_path, **kw):
    s, bin_, script = scene(tmp_path, **kw)
    env = dict(os.environ, PATH=f"{bin_}:/usr/bin:/bin", SCENE=str(s), LC_ALL="C.UTF-8")
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=60)
    return r.returncode, r.stdout.splitlines()


VM_200 = [(f"http://10.99.0.2:{p}/", "200") for p in (8081, 8000, 8082)]
V6_200 = [(f"http://[2601:db8::50]:{p}/", "200") for p in (8081, 8000, 8082)]


def test_the_wizard_writes_a_script_that_parses():
    assert SCRIPT.startswith("#!/bin/bash\n")
    assert subprocess.run(["bash", "-n"], input=SCRIPT, text=True).returncode == 0


def test_ipv4_only_site(tmp_path):
    rc, out = run(tmp_path, curl=VM_200)
    assert out == [
        " ok    PSK            214 FT8 and 31 FT4 spots in the last 15 min",
        " FAIL  magnetometer   frozen: 0.00 C, no change in 60 s.",
        "                      Fix: sigmond-vm sudo systemctl restart mag-recorder",
        " wait  uploads        off, new station: check the lines above, then run: sigmond-vm smd sink upload",
        " ok    receiver page  IPv4 http://192.168.1.50:8081   no IPv6 here",
        " ok    station pages  IPv4 http://192.168.1.50:8000   no IPv6 here",
        " ok    magnetometer page  IPv4 http://192.168.1.50:8082   no IPv6 here",
        "FAIL  (1 failing, 1 waiting)",
    ]
    assert rc == 1


def test_dual_stack_with_the_relay(tmp_path):
    vm = PSK_OK + " ok    magnetometer page  http://127.0.0.1:8082 answers\n"
    rc, out = run(tmp_path, vm=vm, vm_rc=0, ip6=V6, curl=VM_200 + V6_200, relay=True)
    assert out == [
        " ok    PSK            fine",
        " ok    receiver page  IPv4 http://192.168.1.50:8081   IPv6 http://[2601:db8::50]:8081",
        " ok    station pages  IPv4 http://192.168.1.50:8000   IPv6 http://[2601:db8::50]:8000",
        " ok    magnetometer page  IPv4 http://192.168.1.50:8082   IPv6 http://[2601:db8::50]:8082",
        "PASS",
    ]
    assert rc == 0


def test_dual_stack_without_the_relay(tmp_path):
    rc, out = run(tmp_path, vm=PSK_OK, vm_rc=0, ip6=V6, curl=VM_200, relay=False)
    assert out[1] == (" FAIL  receiver page  IPv4 http://192.168.1.50:8081   IPv6 http://[2601:db8::50]:8081 "
                      "does not answer: the IPv6 relay is not installed")
    assert out[-1] == "FAIL  (2 failing)"
    assert rc == 1
    # No magnetometer page line: the VM reported none.
    assert not any("magnetometer page" in ln for ln in out)


def test_vm_silent(tmp_path):
    rc, out = run(tmp_path, vm="sigmond-vm: VM 100 is not running — try: qm start 100\n",
                  vm_rc=1, curl=())
    assert out[0] == " FAIL  decoder VM     does not answer: sigmond-vm: VM 100 is not running ??? try: qm start 100"
    assert out[1] == (" FAIL  receiver page  IPv4 http://192.168.1.50:8081 does not answer: "
                      "the VM at 10.99.0.2:8081 gave no answer   no IPv6 here")
    assert out[-1] == "FAIL  (3 failing)"
    assert rc == 1


def test_a_get_on_the_hosts_own_ipv4_is_never_the_proof(tmp_path):
    # Spec §5: the host's own address answers nothing (PREROUTING), so only
    # the rule, the forwarding switch and the VM's address count.
    rc, out = run(tmp_path, vm=PSK_OK, vm_rc=0,
                  curl=[(f"http://192.168.1.50:{p}/", "200") for p in (8081, 8000)])
    assert out[1].startswith(" FAIL  receiver page  IPv4 http://192.168.1.50:8081 does not answer: the VM at")


def test_ipv4_rule_missing_or_forwarding_off(tmp_path):
    nat = NAT.replace("--dport 8081 ", "--dport 9999 ")
    rc, out = run(tmp_path, vm=PSK_OK, vm_rc=0, nat=nat, curl=VM_200)
    assert "does not answer: no relay rule for port 8081" in out[1]
    assert out[2].startswith(" ok    station pages")


def test_ipv4_forwarding_off(tmp_path):
    rc, out = run(tmp_path, vm=PSK_OK, vm_rc=0, fwd="0", curl=VM_200)
    assert "does not answer: IPv4 forwarding is off" in out[1]
    assert rc == 1


def test_relay_rules_unreadable_says_run_as_root(tmp_path):
    rc, out = run(tmp_path, vm=PSK_OK, vm_rc=0, nat="", nat_rc=4, curl=VM_200)
    assert "cannot read the relay rules (run sigmond-check as root)" in out[1]


def test_wait_only_gives_exit_2(tmp_path):
    rc, out = run(tmp_path, vm=" wait  GRAPE          first files due in about 7 min\n", vm_rc=2, curl=VM_200)
    assert out[-1] == "WAIT  (1 waiting)"
    assert rc == 2


def test_everything_it_prints_is_ascii(tmp_path):
    # The host console's font (Lat15) shows nothing past ASCII (Michael,
    # 2026-10-14).  The VM's complaint below carries an em dash on purpose.
    rc, out = run(tmp_path, vm_rc=1, ip6=V6, curl=VM_200,
                  vm="sigmond-vm: VM 100 is not running \u2014 try: qm start 100\n")
    bad = sorted({c for line in out for c in line if ord(c) >= 0x80})
    assert bad == [], out
    # Every kind of line: a VM line from an smd that still printed a degree
    # sign, its fix, a wait, and the host's own passing page lines.
    (tmp_path / "again").mkdir()
    rc, out = run(tmp_path / "again", vm=VM_LINES.replace("0.00 C", "0.00 \u00b0C"),
                  curl=VM_200 + V6_200, ip6=V6, relay=True)
    assert out[1] == " FAIL  magnetometer   frozen: 0.00 ??C, no change in 60 s."
    assert any(line.startswith(" ok    receiver page") for line in out), out
    assert sorted({c for line in out for c in line if ord(c) >= 0x80}) == [], out
```

Append to `tests/test_pm_align.py`:

```python
# ── the on-site check (sigmond tasks/spec-smd-check-v372.md §6) ─────────────
#
# From v3.72 the wizard writes /usr/local/bin/sigmond-check beside sigmond-vm,
# and pm-align brings it to a station set up before that.

WIZ_V372 = WIZ + '''cat > /usr/local/bin/sigmond-check <<'CHKEOF'
#!/bin/bash
echo PASS
CHKEOF
'''


def test_desired_files_takes_sigmond_check_from_a_v372_wizard():
    src = pm_align.Sources(FB_FULL, pm_align.render_wizard(WIZ_V372, 100), {"pm-heartbeat.py": b"hb"})
    files = {f.path: f for f in pm_align.desired_files(src, 100)}
    f = files["/usr/local/bin/sigmond-check"]
    assert f.mode == 0o755 and f.content == b"#!/bin/bash\necho PASS\n"


def test_desired_files_accepts_a_wizard_from_before_v372():
    paths = {f.path for f in pm_align.desired_files(_src(), 100)}
    assert "/usr/local/bin/sigmond-check" not in paths
    assert "/usr/local/bin/sigmond-vm" in paths


def test_the_real_wizard_carries_sigmond_check():
    src = pm_align.Sources(FB_FULL, pm_align.render_wizard(WIZARD, 100), {})
    files = {f.path: f for f in pm_align.desired_files(src, 100)}
    for path, mode in pm_align.WIZARD_HEREDOCS_SINCE_V372.items():
        assert path in files, f"the wizard no longer writes {path} from a quoted heredoc"
        assert files[path].mode == mode
    body = files["/usr/local/bin/sigmond-check"].content.decode()
    assert body.startswith("#!/bin/bash\n")
    assert "sigmond-vm smd check --no-verdict" in body
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_sigmond_check_host.py tests/test_pm_align.py -q --override-ini addopts=`
Expected: the run stops at collection, `KeyError: '/usr/local/bin/sigmond-check'` in
`tests/test_sigmond_check_host.py` (the wizard writes no such heredoc yet).  Then run
`.venv/bin/pytest tests/test_pm_align.py -q --override-ini addopts=` alone.  Expected: `2 failed, 159 passed`;
`test_desired_files_takes_sigmond_check_from_a_v372_wizard` (`KeyError`) and
`test_the_real_wizard_carries_sigmond_check` (`AttributeError: … no attribute 'WIZARD_HEREDOCS_SINCE_V372'`).

- [ ] **Step 3: Write the heredoc into the wizard**

In `scripts/proxmox/sigmond-wizard.sh`, after the line `chmod +x /usr/local/bin/sigmond-vm` (3342), insert this
block.  The `cat` line and the closing `CHKEOF` stand at column 0; pm-align's reader and every pm-align in the field
need that.

```bash
# -- sigmond-check: does this station work?  (v3.72 #14) ----------------------
# The installer types it at this console.  It asks the VM (`smd check`), adds
# the page lines through this host's addresses, and prints one verdict.
# pm-align carries this heredoc too (WIZARD_HEREDOCS_SINCE_V372).
say "installing the sigmond-check helper (is the station working?)"
cat > /usr/local/bin/sigmond-check <<'CHKEOF'
#!/bin/bash
# sigmond-check - does this station work?  Type it at the host's console.
# Installed by sigmond-setup; pm-align brings it to stations set up before
# v3.72.  sigmond tasks/spec-smd-check-v372.md.
#
# It asks the decoder VM for its lines (`smd check --no-verdict`), adds one
# line per web page as a person on this network reaches it, and ends with one
# verdict: PASS (exit 0), WAIT (exit 2) or FAIL (exit 1).  It changes nothing.
#
# IPv4: the host relays ports 8081, 8000 and 8082 to the VM with DNAT rules in
# PREROUTING.  Traffic the host sends to itself never passes PREROUTING, so a
# GET on the host's own IPv4 address proves nothing.  The line passes when the
# rule exists, net.ipv4.ip_forward reads 1, and the VM's address answers 200.
# IPv6: sigmond-vm6proxy relays in userspace, so a GET on the host's own
# global IPv6 address takes the same path a browser takes.
#
# Everything it prints stays ASCII: the console font (Lat15) shows nothing
# else (Michael, 2026-10-14).
set -u
case "${1:-}" in
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac
BRIDGE="${SIGMOND_CHECK_BRIDGE:-vmbr0}"
VM_TIMEOUT="${SIGMOND_CHECK_VM_TIMEOUT:-60}"
OKM='ok'; FAILM='FAIL'
LINES=()

add(){   # mark label text -- the same columns as smd check
    local lab="$2"
    if [ "${#lab}" -lt 15 ]; then lab=$(printf '%-15s' "$lab"); else lab="$lab  "; fi
    LINES+=("$(printf ' %-4s  %s%s' "$1" "$lab" "$3")")
}
ascii(){ printf '%s' "$1" | LC_ALL=C tr -c ' -~' '?'; }   # text from elsewhere: past ASCII becomes '?'

# -- the VM's lines ----------------------------------------------------------
MAG_PAGE=0
VM_OUT=$(timeout "$VM_TIMEOUT" sigmond-vm smd check --no-verdict 2>&1)
if printf '%s\n' "$VM_OUT" | grep -qE '^ (ok  |wait|FAIL|--  )  '; then
    KEEP=0
    while IFS= read -r l; do
        case "$l" in
            " ok    "*|" wait  "*|" FAIL  "*|" --    "*)
                KEEP=1
                case "${l:7}" in
                    "receiver page"*|"station pages"*) KEEP=0 ;;   # the host checks these below
                    "magnetometer page"*) MAG_PAGE=1; KEEP=0 ;;
                esac ;;
            "                      "*) ;;    # a fix, under the line before it
            *) KEEP=0 ;;
        esac
        [ "$KEEP" = 1 ] || continue
        # A command the VM names must reach the VM from this console.
        l=${l//Fix: /Fix: sigmond-vm }
        l=${l//run: /run: sigmond-vm }
        LINES+=("$(ascii "$l")")
    done <<<"$VM_OUT"
else
    WHY=$(printf '%s\n' "$VM_OUT" | grep -v '^[[:space:]]*$' | tail -1)
    add "$FAILM" "decoder VM" "does not answer: $(ascii "${WHY:-nothing within $VM_TIMEOUT s}")"
fi

# -- the page lines, through this host's addresses ---------------------------
V4=$(ip -4 -o addr show dev "$BRIDGE" scope global 2>/dev/null | awk '{split($4, a, "/"); print a[1]; exit}')
V6=$(ip -6 -o addr show dev "$BRIDGE" scope global 2>/dev/null \
     | grep -v -e temporary -e deprecated -e tentative | awk '{split($4, a, "/"); print a[1]; exit}')
NAT=$(iptables -t nat -S PREROUTING 2>/dev/null); NAT_RC=$?
FWD=$(sysctl -n net.ipv4.ip_forward 2>/dev/null)

code_of(){ curl -g -s -o /dev/null -m 3 -w '%{http_code}' "$1" 2>/dev/null; }
said(){ case "$1" in ''|000) echo "no answer" ;; *) echo "status $1" ;; esac; }
dnat_to(){   # port -> the ip:port its DNAT rule sends it to
    printf '%s\n' "$NAT" | awk -v p="$1" '
        $0 ~ ("--dport " p "( |$)") && / -j DNAT / {
            for (i = 1; i <= NF; i++) if ($i == "--to-destination") { print $(i + 1); exit } }'
}

page(){   # label port
    local label="$1" port="$2" mark="$OKM" text="" part to code
    if [ -n "$V4" ]; then
        part="IPv4 http://$V4:$port"
        to=$(dnat_to "$port")
        if [ "$NAT_RC" -ne 0 ]; then
            part="$part does not answer: cannot read the relay rules (run sigmond-check as root)"; mark="$FAILM"
        elif [ -z "$to" ]; then
            part="$part does not answer: no relay rule for port $port"; mark="$FAILM"
        elif [ "$FWD" != 1 ]; then
            part="$part does not answer: IPv4 forwarding is off"; mark="$FAILM"
        else
            code=$(code_of "http://$to/")
            [ "$code" = 200 ] || { part="$part does not answer: the VM at $to gave $(said "$code")"; mark="$FAILM"; }
        fi
        text="$part"
    fi
    if [ -n "$V6" ]; then
        part="IPv6 http://[$V6]:$port"
        code=$(code_of "http://[$V6]:$port/")
        if [ "$code" != 200 ]; then
            if systemctl is-active --quiet "sigmond-vm6proxy@$port.socket" 2>/dev/null; then
                part="$part does not answer: $(said "$code")"
            else
                part="$part does not answer: the IPv6 relay is not installed"
            fi
            mark="$FAILM"
        fi
        text="${text:+$text   }$part"
    elif [ -n "$V4" ]; then
        text="$text   no IPv6 here"
    fi
    if [ -z "$V4$V6" ]; then mark="$FAILM"; text="this host has no address on $BRIDGE"; fi
    add "$mark" "$label" "$text"
}
page "receiver page" 8081
page "station pages" 8000
[ "$MAG_PAGE" = 1 ] && page "magnetometer page" 8082

# -- one verdict from both sets ----------------------------------------------
NFAIL=0; NWAIT=0
for l in "${LINES[@]}"; do
    printf '%s\n' "$l"
    case "$l" in " FAIL  "*) NFAIL=$((NFAIL + 1)) ;; " wait  "*) NWAIT=$((NWAIT + 1)) ;; esac
done
PARTS=""
[ "$NFAIL" -gt 0 ] && PARTS="$NFAIL failing"
[ "$NWAIT" -gt 0 ] && PARTS="${PARTS:+$PARTS, }$NWAIT waiting"
if [ "$NFAIL" -gt 0 ]; then V=FAIL; RC=1; elif [ "$NWAIT" -gt 0 ]; then V=WAIT; RC=2; else V=PASS; RC=0; fi
if [ -n "$PARTS" ]; then echo "$V  ($PARTS)"; else echo "$V"; fi
exit "$RC"
CHKEOF
chmod +x /usr/local/bin/sigmond-check
```

- [ ] **Step 4: Teach pm-align the new file**

In `scripts/proxmox/pm-align.py`, before `SETUP = "/usr/local/sbin/sigmond-setup"` (260), insert:

```python
# The on-site check (sigmond tasks/spec-smd-check-v372.md).  A wizard from
# before v3.72 does not write it, so it is never required either.
WIZARD_HEREDOCS_SINCE_V372 = {
    "/usr/local/bin/sigmond-check": 0o755,
}
```

In `desired_files`, before `    out.extend(relay_units(src.wizard, vmid))` (327), insert:

```python
    for path, mode in WIZARD_HEREDOCS_SINCE_V372.items():
        if path in wz:
            out.append(HostFile(path, wz[path].encode(), mode, "sigmond-wizard.sh"))
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `bash -n scripts/proxmox/sigmond-wizard.sh && .venv/bin/pytest tests/test_sigmond_check_host.py tests/test_pm_align.py -q --override-ini addopts=`
Expected: no syntax error; `172 passed` (11 host tests and 161 pm-align tests).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest tests -q --override-ini addopts=`
Expected: all pass (4455 passed, 17 skipped, 171 subtests passed).

- [ ] **Step 7: Commit**

```bash
git add scripts/proxmox/sigmond-wizard.sh scripts/proxmox/pm-align.py tests/test_sigmond_check_host.py tests/test_pm_align.py
git commit -m "sigmond-check: the host half of the on-site check

The wizard writes /usr/local/bin/sigmond-check beside sigmond-vm, and
pm-align carries it (WIZARD_HEREDOCS_SINCE_V372).  It prints the VM's
lines from smd check --no-verdict with sigmond-vm put before every
command, then one line per page through this host's addresses: IPv4
passes on the DNAT rule, ip_forward=1 and a 200 from the VM's address;
IPv6 on a GET through sigmond-vm6proxy.  One verdict for both (spec §3,
§5).

<attribution lines>"
```

- [ ] **Step 8: Mutation check**

Each change to the heredoc in `scripts/proxmox/sigmond-wizard.sh`, alone and restored with
`git checkout -- scripts/proxmox/sigmond-wizard.sh`, must fail the named test in `tests/test_sigmond_check_host.py`:

| Change | Fails |
|---|---|
| `OKM='ok'; FAILM='FAIL'` → `OKM='✓'; FAILM='FAIL'` (a check mark back) | `test_everything_it_prints_is_ascii` (and three exact-output tests) |
| `        LINES+=("$(ascii "$l")")` → `        LINES+=("$l")` | `test_everything_it_prints_is_ascii` |
| `does not answer: $(ascii "${WHY:-nothing within $VM_TIMEOUT s}")` → `does not answer: ${WHY:-nothing within $VM_TIMEOUT s}` | `test_vm_silent`, `test_everything_it_prints_is_ascii` |
| `        l=${l//Fix: /Fix: sigmond-vm }` → `        :` | `test_ipv4_only_site` |
| `"                      "*) ;;    # a fix, …` → `"                      "*) KEEP=1 ;;` | `test_ipv4_only_site` (a dropped page line's fix leaks) |
| `            code=$(code_of "http://$to/")` → `            code=$(code_of "http://$V4:$port/")` | `test_a_get_on_the_hosts_own_ipv4_is_never_the_proof` (the planner saw 6 tests fail) |
| `        elif [ "$FWD" != 1 ]; then` → `        elif false; then` | `test_ipv4_forwarding_off` |
| the line `part="$part does not answer: the IPv6 relay is not installed"` → `part="$part does not answer: $(said "$code")"` | `test_dual_stack_without_the_relay` |

Note for Task 10: a station's own `pm-align` runs the code its last alignment installed.  A pm-align from before this
task copies the new `pm-align` into place but does not know `WIZARD_HEREDOCS_SINCE_V372`, so the first run after the
v3.72 release does not deliver `sigmond-check`; the second run does.

---

### Task 9: The install guide and the console panel (sigmond-appliance)

**Files:**
- Modify: `/home/mjh/hamsci/repos/sigmond-appliance/INSTALL.md:257-260` (§8 sentence), `:265-315` (§9),
  `:380` (§11 row), `:400-403` (§12, item 1, which sends the reader to §9's old watcher)
- Modify: `/home/mjh/hamsci/repos/sigmond-appliance/firstboot-v3.sh:3250` (panel help line)

**Interfaces:**
- Consumes: the command names and output of Tasks 1-8: `sigmond-check`, `sigmond-vm smd sink upload`,
  `ssh -p 2222 sigmond@<host address> smd check`, the four marks, `Fix:`.
- Produces: documentation only.

- [ ] **Step 1: Rewrite §9**

In `INSTALL.md`, replace everything from the heading `## 9. Fifteen minutes later — check it's alive` up to, not
including, the heading `## 10. Logins — and change the password` with:

````markdown
## 9. Fifteen minutes later — check it's alive

At the station's keyboard, press Enter and log in to the host, as §8 describes.  Then
type one command:

```
sigmond-check
```

It takes about fifteen seconds.  It prints one line for each part of the station and
ends with a verdict:

```
 ok    PSK            214 FT8 and 31 FT4 spots in the last 15 min
 ok    WSPR           48 spots in the last 10 min, on 6 bands
 FAIL  magnetometer   frozen: 0.00 C, no change in 60 s.
                      Fix: sigmond-vm sudo systemctl restart mag-recorder
 wait  GRAPE          first files due in about 7 min (hf-timestd started 4 min ago)
 ok    storage        /var/lib has 61 % free
 wait  uploads        off, new station: check the lines above, then run: sigmond-vm smd sink upload
 --    meteor scatter not on this station
 ok    receiver page  IPv4 http://192.168.1.50:8081   IPv6 http://[2601:db8::50]:8081
 ok    station pages  IPv4 http://192.168.1.50:8000   IPv6 http://[2601:db8::50]:8000
 ok    magnetometer page  IPv4 http://192.168.1.50:8082   IPv6 http://[2601:db8::50]:8082
FAIL  (1 failing, 2 waiting)
```

The word at the start of a line tells you how that part stands:

| Word | Meaning |
|---|---|
| `ok` | It works. |
| `wait` | Not yet.  A new station shows this for a while; the line says how long to wait. |
| `FAIL` | It does not work.  The line says what to do, or whom to tell. |
| `--` | This station does not run that part, and the verdict leaves it out. |

The last line gives the verdict.  `PASS` means every part works.  `WAIT` means some part
has not started yet: wait the time its line names, then type `sigmond-check` again.
`FAIL` means some part does not work.  Do what its line says.  A fix stands on its own
line under it, after the word `Fix:`; type it exactly as printed.  When a `FAIL` line
names no fix, send the whole output to your fleet admin.

A quiet band can look like a fault.  So a `FAIL` line about spots asks you to check the
antenna and its cable before anything else.

A new station sends nothing yet.  It starts with its site sink switch at `off`: it
records and decodes, but sends no spots or data to wsprnet, pskreporter.info,
wsprdaemon.org or PSWS.  Only its heartbeat goes out, a five-minute health report for
the fleet board.  So the uploads line shows `wait` and says `new station`.

When every other line shows `ok` or `--`, turn the uploads on:

```
sigmond-vm smd sink upload
```

Then type `sigmond-check` again.  The uploads line now says `on`, and within 30 minutes
it shows the first delivery.  An uploads line that shows `FAIL` with `off` or `hold`
and some other reason means someone set the switch on purpose.  Leave it alone and ask your
fleet admin.

Nothing recorded before `smd sink upload` leaves the station, with one exception.  The
station packs each UTC day's GRAPE and magnetometer data after that day ends, between
01:00 and about 05:00 UTC.  The packages for the day you turn the uploads on still go
out, and so do the previous day's if their packing has not finished.  So turn them on
after about 05:00 UTC if you can.  While a packing job runs, `smd sink upload` refuses,
names the job, and asks you to try again when it ends.  About fifteen minutes after,
search your reporter ID at wsprnet.org (Database) and your callsign at pskreporter.info.

If you like, open the pages from another computer on the same network.  `sigmond-check`
printed their addresses, for example `http://192.168.1.50:8081` over IPv4 or
`http://[2601:db8::50]:8081` over IPv6.  A browser needs the square brackets around an
IPv6 address.  The receiver page shows a waterfall with signals; the station pages show
the callsign, the grid, and the timing and GRAPE pages.

From another computer you can also ask the decoder VM alone.  This prints the VM's lines
without the host's page lines:

```
ssh -p 2222 sigmond@<host address> smd check
```

---

````

- [ ] **Step 2: Point §11's row at `sigmond-check`**

Replace the table row that starts `| No spots after 30 minutes |` (380) with:

```markdown
| No spots after 30 minutes | Expected on a new station until you turn the uploads on (§9). At the host's console, type `sigmond-check`. If the uploads line says `new station`, check the other lines as §9 describes, then type `sigmond-vm smd sink upload`. If it shows `off` or `hold` with any other reason, ask your fleet admin. If the PSK or WSPR line shows `FAIL`, check the antenna and its cable first, then send the whole output to your fleet admin |
```

- [ ] **Step 3: Name `sigmond-check` in §8**

Replace these four lines (257-260):

```
you type the password. With that login on the host you can type
`sigmond-setup --rac-off`, `sigmond-setup --rac-on` and
`sigmond-setup --pair-vm` (§10 says what they do). For everything else, use the
station from another computer. The panel lists every address and login;
```

with:

```
you type the password. With that login on the host you can type `sigmond-check`
(§9), and `sigmond-setup --rac-off`, `sigmond-setup --rac-on` and
`sigmond-setup --pair-vm` (§10 says what they do). For everything else, use the
station from another computer. The panel lists every address and login;
```

- [ ] **Step 4: §12 item 1**

Replace these four lines (400-403):

```
1. At the staging site, leave the site sink switch at `off`; a new station
   starts that way. Check reception there with the waterfall and
   `smd watch wspr` (§9). No data leaves the station; only its heartbeat
   goes out.
```

with:

```
1. At the staging site, leave the site sink switch at `off`; a new station
   starts that way. Check reception there with `sigmond-check` (§9): every
   line but uploads should show `ok` or `--`. No data leaves the station; only
   its heartbeat goes out.
```

- [ ] **Step 5: The panel's help line**

In `firstboot-v3.sh`, replace line 3250:

```
 From the host:  sigmond-vm (VM shell) · qm terminal $VMID (VM console) · sigmond-setup --reconfigure
```

with:

```
 From the host:  sigmond-check (is it working?) · sigmond-vm (VM shell) · qm terminal $VMID · sigmond-setup --reconfigure
```

(119 columns once `$VMID` expands to three digits; the panel's widest line today runs 103.  Decision 9.)

- [ ] **Step 6: Check**

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance
bash -n firstboot-v3.sh && echo parses
bash test-panel-remote.sh | tail -1
bash test-panel-agent.sh | tail -1
grep -c 'smd watch' INSTALL.md
grep -n 'sigmond-check' INSTALL.md | head
```

Expected: `parses`; `test-panel-remote.sh: 129 passed, 0 failed`; `passed 58, failed 0`; `0` (no `smd watch`
left in the guide); `sigmond-check` in §8, §9, §11 and §12.

- [ ] **Step 7: Commit**

```bash
git add INSTALL.md firstboot-v3.sh
git commit -m "install docs: §9 becomes one command, sigmond-check (v3.72 #14)

§9 now asks the installer to type sigmond-check at the console and
explains the four marks and the verdict; §8, §11 and §12 point at it,
and the console panel names it.  sigmond tasks/spec-smd-check-v372.md §7.

<attribution lines>"
```

---

### Task 10: Check it on AC0G-B4 (manual; spec §8, last bullet)

AC0G-B4 serves as the test bench, and Rob's Claude works on it too.  Nothing in this task runs until the claude-bus
carries the announcement, and nothing runs until Michael has pushed the commits of Tasks 1-9.  The planner did not run
this task.

Reach B4 only through `/home/mjh/hamsci/ops/bin/fleet-ssh b4 '<command>'` (the decoder VM) and
`/home/mjh/hamsci/ops/bin/fleet-ssh b4-pm '<command>'` (the Proxmox host).  Never retype a reach.  If the permission
system refuses a command that needs root on B4, write the command into a plain script under `/home/mjh/` and hand
Michael one line to run with the `!` prefix (memory `reference_auto_mode_blocks_station_root`).

**Files:** none in any repository.  One bus message before, one after.

- [ ] **Step 1: Read the bus and announce**

```bash
sudo -n cat /srv/hamsci/claude-bus/README.md
sudo -n bash -c 'ls -1 /srv/hamsci/claude-bus/ | grep "^2026" | sort | tail -10'
sudo -n bash -c 'for f in $(ls -1 /srv/hamsci/claude-bus/ | grep "^2026" | sort | tail -5); do echo "== $f"; cat "/srv/hamsci/claude-bus/$f"; done'
```

Read anything newer than this plan about AC0G-B4, above all an A/B experiment in progress.  If one runs, stop and ask
Michael.  Write the announcement to `/tmp/bus-draft-b4-smd-check.md`:

```markdown
# AC0G-B4: smd check + sigmond-check (v3.72 #14) — deploy and checks, starting now

From mjh's Claude.  Plan: sigmond tasks/plan-smd-check-v372.md, Task 10.

What changes on B4:
- VM: `smd update --apply` pulls sigmond (smd gains the read-only verb
  `check`).  Nothing else moves; no client restarts beyond what smd update
  itself advises.
- Host: one new file, /usr/local/bin/sigmond-check (0755), copied from the
  pushed wizard.  No unit, timer, firewall or relay change.
- Then a stop/start of mag-recorder for about a minute, to watch its line
  turn FAIL and back.  No CPU affinity, IRQ or cache change.
I will post the result.
```

Post it:

```bash
F=$(date -u +%Y%m%dT%H%M%SZ)-mjh.md
sudo -n cp /tmp/bus-draft-b4-smd-check.md "/srv/hamsci/claude-bus/$F"
sudo -n chown mjh:hamsci "/srv/hamsci/claude-bus/$F"
sudo -n chmod 660 "/srv/hamsci/claude-bus/$F"
```

- [ ] **Step 2: Deploy to the VM**

As the `hamsci` user (smd refuses to run under sudo):

```bash
F=/home/mjh/hamsci/ops/bin/fleet-ssh
$F b4 'smd update'
$F b4 'smd update --apply'
```

Expected: the plan lists a pull of sigmond; the apply finishes without error.  `smd update --apply` restarts
radiod's consumers only when its plan says so (memory `reference_smd_apply_restarts_radiod`): read the plan first,
and stop if it names radiod.

- [ ] **Step 3: Install `sigmond-check` on the host**

pm-align delivers only from a blessed release, so on the bench copy the heredoc by hand from the pushed wizard:

```bash
cd /home/mjh/hamsci/repos/sigmond
.venv/bin/python - <<'EOF' > /tmp/sigmond-check
import importlib.util, sys
spec = importlib.util.spec_from_file_location("pm_align", "scripts/proxmox/pm-align.py")
m = importlib.util.module_from_spec(spec); sys.modules["pm_align"] = m; spec.loader.exec_module(m)
sys.stdout.write(m.extract_heredocs(open("scripts/proxmox/sigmond-wizard.sh").read())["/usr/local/bin/sigmond-check"])
EOF
bash -n /tmp/sigmond-check && echo parses
```

Then put it on the host (stage this as a script for Michael if the permission system refuses):

```bash
/home/mjh/hamsci/ops/bin/fleet-ssh b4-pm 'cat > /usr/local/bin/sigmond-check && chmod 755 /usr/local/bin/sigmond-check' < /tmp/sigmond-check
```

- [ ] **Step 4: Run both commands and time them**

```bash
$F b4 'time smd check; echo rc=$?'
$F b4-pm 'time sigmond-check; echo rc=$?'
```

Expected: each finishes within 15 s.  `smd check` prints a line each for PSK, WSPR, magnetometer, GRAPE, storage,
uploads and meteor scatter, three page lines against `127.0.0.1`, and a verdict.  On B4 today: `ok` for PSK, WSPR,
magnetometer (if its sensor runs), GRAPE and storage; ` ok    uploads` (B4 ships; `on; last delivery N min ago`).
`sigmond-check` prints the same lines without the `127.0.0.1` page lines, each `Fix:` followed by `sigmond-vm`, and
three page lines through `http://192.168.1.244:<port>` (B4's host address; plus an IPv6 link if B4 has one).  If a
line shows `FAIL`, read it before you go on: either the station has a fault, or this plan read a source wrong.  Record
which in the bus report.

Check that both outputs hold nothing past ASCII, as the console font needs (decision 11):

```bash
$F b4 'smd check | LC_ALL=C grep -c "[^ -~]"'
$F b4-pm 'sigmond-check | LC_ALL=C grep -c "[^ -~]"'
```

Expected: `0` from each.  If Michael stands at B4, he reads `sigmond-check` on the console itself.

- [ ] **Step 5: Watch the magnetometer line turn FAIL and back**

```bash
$F b4 'sudo -n systemctl stop mag-recorder; smd check | grep -A1 " magnetometer  "; echo rc=$?'
$F b4-pm 'sigmond-check | grep -A1 " magnetometer  "'
$F b4 'sudo -n systemctl start mag-recorder; sleep 5; smd check | grep magnetometer'
$F b4 'sleep 60; smd check | grep magnetometer'
```

Expected, stopped (`rc=1`):

```
 FAIL  magnetometer   mag-recorder is inactive.
                      Fix: smd start mag-recorder
```

and on the host the same two lines with `Fix: sigmond-vm smd start mag-recorder`.  Five seconds after the start,
` wait  magnetometer   mag-recorder just started; …`; a minute later ` ok    magnetometer   samples arriving, … C`.

- [ ] **Step 6: Report on the bus**

Write the result (each command's output, its time, any `FAIL` and what caused it, the ASCII counts) to
`/tmp/bus-draft-b4-smd-check-result.md` and post it as in Step 1.  Then delete both drafts.

---

## Self-review (done while writing)

- **Spec coverage.**  §2 marks, verdict, exit codes, layout: Task 1.  §3 `smd check` no root, `--no-verdict`,
  `--json`, no timing judge: Task 1; `sigmond-check` and the silent VM: Task 8.  §4 table: PSK, WSPR, meteor
  (Task 3), magnetometer (Task 4), GRAPE, storage (Task 5), uploads (Task 6), VM pages (Task 7); `--` for a client not
  installed: each line's `installed` test.  §4.1 freeze: Task 4.  §4.2 sudo fallback and `cannot read … as …`:
  Task 2 (and decision 5).  §5 host IPv4 rule, IPv6 GET, the three address cases, mag page only with mag-recorder:
  Task 8.  §6 code locations and `WIZARD_HEREDOCS_SINCE_V372`: Tasks 1-8.  §7 install guide, panel line, §11 row:
  Task 9.  §8 tests, mutations, the B4 run: every task and Task 10.  §9 stays out.
- **Placeholders.**  None: every code step carries the code that ran on the scratch copy.  `<attribution lines>`
  stands for the session's own trailer, as the previous plan did.
- **Names.**  `Line`, `Env`, `Service`, `Reader`, `Unreadable`, `service_state`, `read_journal`, `service_problem`,
  `is_young`, `minutes`, `plural`, `count_psk_spots`, `psk_line`, `wspr_line`, `meteor_line`, `last_mag_samples`,
  `mag_frozen`, `mag_line`, `grape_channels`, `grape_roots`, `read_timestd_config`, `grape_line`, `storage_line`,
  `read_backlog`, `uploads_line`, `http_status`, `page_check`, `default_env`, `guarded`, `run_checks`, `cmd_check`,
  `CHECKS`; `make_env`, `svc`, `NOW`, `HOUR` (tests); `WIZARD_HEREDOCS_SINCE_V372` (pm-align).  Each later task uses
  them as the task that defines them does.
