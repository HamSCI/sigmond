# v3.69 — first sink-control image — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the small v3.69 that §10.3 of the spec defines.  A fresh station starts with its site
sink switch at `off`.  The operator sets it to `upload` with `smd sink upload` after checking the
station.  psk-recorder and meteor-scatter post through the daemon alone, and GRAPE stops sending a
day twice.

**Architecture:** Every change rides on mechanisms that already exist.  `smd sink` wraps the legacy
`[uploads] mode` handler, `cmd_config_uploads`.  A new module, `sink_doors.py`, sets data aside
whenever the written mode leaves `off`, and refuses while GRAPE or the magnetometer packages a day.
A new `smd config profile-install` lets the wizard rewrite `site-profile.toml` without resetting
the switch.  Bring-up and meteor-scatter's seed choose the daemon as the one pskreporter sender.
hamsci-physics writes the marker its catch-up sweep has looked for since June.

**Tech Stack:** Python 3.11, stdlib-only for sigmond's core; bash for the appliance wizard; pytest
(with `unittest.TestCase` classes in sigmond); TOML.

**Spec:** `sigmond/tasks/plan-sink-control.md` (f8c0a9b, plus the 2026-10-06 corrections to §10.3
items 3 and 6), §2.1 Terms and §10.3.  Read both before starting.

## Global Constraints

- Vocabulary, from spec §2.1, in every new message, help string, comment and doc line: *sink*,
  *sink switch*, *site sink switch*, *sink state* (`sink off` / `sink filling` / `sink uploading`).
  A sink names a client's store; only the switch takes a setting, so write "set the site sink
  switch to `upload`", never "turn the sink on".  Name the database file only as `sink.db`.
  Legacy words (`smd upload on|hold|discard`, `[uploads] mode`) appear only when naming existing
  code, with the mapping upload → `upload`, hold → `fill`, discard → `off`.
- v3.69 ships the SITE form only: `smd sink off|upload|status`.  `smd sink fill` refuses with an
  explanation and exit code 2.  No per-client `smd sink <client>`.
- Existing stations keep shipping as they do.  No code path in this plan rewrites an existing
  station's `[uploads]` block or recorder env file.  The wizard writes `off` only when the host
  carries no `/etc/sigmond-appliance/.configured` mark.
- The heartbeat still goes out whatever the site sink switch says.  No message may claim that
  nothing at all leaves the station.
- sigmond's core stays stdlib-only (sigmond/CLAUDE.md "Key constraints").
- Develop on `main` in each repo; one commit per task; never push without Michael's explicit go.
  Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN
  ```
- Station work (Tasks 12 and 13) starts only after reading and posting on the claude-bus
  (`/srv/hamsci/claude-bus/`, see /home/mjh/hamsci/CLAUDE.md).  Nothing in Tasks 1-11 touches a
  station.
- Test runners: sigmond `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest <file> -v`;
  hamsci-physics `cd /home/mjh/hamsci/repos/hamsci-physics && .venv/bin/pytest <file> -v --override-ini="addopts="`;
  meteor-scatter `cd /home/mjh/hamsci/repos/meteor-scatter && .venv/bin/pytest <file> -v`.
- Prose (docs, messages): short sentences, active voice, few uses of the verb "to be".
- Times in messages and docs name UTC.  Never write "tonight".
- Line numbers in a task name the file as it stood before that task began; find each edit by its
  quoted anchor text.

## Review Focus

1. **A reconfigure on a station whose operator already ran `smd sink upload`.**  The wizard rewrites
   `site-profile.toml` whole.  The operator expects uploads to keep flowing.  Pinned by Task 7
   (`test_reconfigure_keeps_an_upload_block`) and Task 8 (`test_configured_host_writes_no_block`).
2. **`smd sink upload` on a station that never sat in `off`, or run twice.**  Expect no data set
   aside and no markers written.  Pinned by Task 5 (`test_on_from_upload_or_hold_leaves_the_doors_alone`).
3. **A spool on a different filesystem from its parent.**  Expect a refusal that moves nothing,
   names the spool and the next step (move it onto its parent's filesystem, or ask the fleet
   admin), and leaves the site sink switch at `off`.  Pinned by Task 4
   (`test_set_aside_refuses_across_filesystems`) and Task 5 (`test_a_door_failure_leaves_the_site_sink_off`).
4. **A day directory that root creates before GRAPE packages that day.**  The packager runs as
   `timestd` and must still write inside it.  Pinned by Task 4
   (`test_new_day_dirs_and_markers_take_the_spool_owner`).
5. **A station on the legacy hold, such as K3LR, running `smd sink status` or `smd sink upload`.**
   Expect status to say `hold (legacy)`, never `fill`, and upload to say plainly that the stored
   backlog ships.  Pinned by Task 6 (`test_status_names_a_legacy_hold`) and Task 5
   (`test_leaving_a_discard_rendered_as_hold_says_the_backlog_ships`).
6. **Leaving `off` through the legacy hold** (`smd upload discard`, then `smd upload hold`, then
   `smd upload on`).  The doors close at the first step away from discard, and only once.  Pinned
   by Task 5 (`test_leaving_off_through_hold_closes_the_doors`).
7. **`smd sink upload` while GRAPE or the magnetometer packages a day** (about 01:00 to 04:00 UTC).
   Expect a refusal that names the unit, moves nothing, says to run the command again when the
   unit finishes, and leaves the switch at `off`.  Both units run `Type=oneshot`, so systemd reports
   a running one as `activating`, never `active`.  Pinned by Task 4
   (`test_packaging_running_counts_a_oneshot_that_is_activating`) and Task 5
   (`test_the_config_hook_refuses_while_packaging_runs`).
8. **An `[uploads]` header with a trailing comment**, the line that uncommenting TEMPLATE's example
   produces.  `carry_uploads_block` must carry that block, and `_patch_uploads_block` must replace
   it rather than append a second table.  Pinned by Task 7
   (`test_a_header_with_a_trailing_comment_is_carried`,
   `test_a_header_with_a_trailing_comment_is_replaced_not_duplicated`).

---

### Task 1: hamsci-physics marks a GRAPE day packaged

Nothing has written `upload/<day>/.upload_complete` since hf-timestd `af45a6a` (2026-06-30), so the
catch-up sweep (`src/hamsci_physics/cli.py:576-591`) re-packages days 2-7 back every night.  Each
re-package moves the OBS directory's modification time past GRAPE's send record, which re-sends it.

The retry test also counts a day as packaged when `upload/<day>/` already holds an OBS* dataset,
the same test Gate 3 applies.  Days packaged before this change carry no marker, so without that
second test the first night after deploy would re-send them all once more.  The trade: a day whose
packaging crashed after it created an OBS directory no longer re-runs.  Gate 3 already treats such
a directory as a finished dataset.

**Files:**
- Modify: `hamsci-physics/src/hamsci_physics/grape/spool.py` (add two functions at the end)
- Modify: `hamsci-physics/src/hamsci_physics/cli.py` (after Gate 3, line 473; sweep comment, lines
  572-573; sweep loop, lines 582-589)
- Test: `hamsci-physics/tests/test_grape_packaged_marker.py` (create)

**Interfaces:**
- Produces: `PACKAGED_MARKER: str = ".upload_complete"`; `mark_day_packaged(day_dir: Path) -> Path`;
  `day_needs_retry(data_root: Path, day: str) -> bool`.  Task 4 writes the same marker name by
  literal; the first test pins it.

- [ ] **Step 1: Write the failing tests**

```python
"""GRAPE marks a day packaged, so the catch-up sweep never re-packages it.

Nothing wrote upload/<day>/.upload_complete after hf-timestd af45a6a
(2026-06-30) retired SFTPUpload.  The sweep then re-packaged days 2-7 back
every night, and each re-package moved the OBS mtime past the send record,
so hs-uploader re-sent the day (sigmond tasks/plan-sink-control.md §10.3).
"""
import ast
import inspect
from pathlib import Path

from hamsci_physics import cli
from hamsci_physics.grape.spool import (
    PACKAGED_MARKER, day_needs_retry, mark_day_packaged,
)


def _source(root: Path, day: str) -> None:
    (root / "raw_buffer" / "WWV_10" / day).mkdir(parents=True)


def test_mark_day_packaged_writes_the_marker_and_repeats_safely(tmp_path):
    day_dir = tmp_path / "upload" / "20261001"
    first = mark_day_packaged(day_dir)
    second = mark_day_packaged(day_dir)
    # sigmond sink_doors (Task 4) writes this name by literal; keep them equal.
    assert first == second == day_dir / ".upload_complete"
    assert first.is_file()


def test_an_unmarked_day_with_source_needs_a_retry(tmp_path):
    _source(tmp_path, "20261001")
    assert day_needs_retry(tmp_path, "20261001")


def test_a_marked_day_is_never_retried(tmp_path):
    _source(tmp_path, "20261001")
    mark_day_packaged(tmp_path / "upload" / "20261001")
    assert not day_needs_retry(tmp_path, "20261001")


def test_a_day_with_only_decimated_source_needs_a_retry(tmp_path):
    dec = tmp_path / "products" / "WWV_10" / "decimated"
    dec.mkdir(parents=True)
    (dec / "20261001.bin").write_bytes(b"")
    assert day_needs_retry(tmp_path, "20261001")


def test_a_day_without_source_is_not_retried(tmp_path):
    assert not day_needs_retry(tmp_path, "20261001")


def test_a_marker_deeper_in_the_day_still_counts(tmp_path):
    _source(tmp_path, "20261001")
    mark_day_packaged(tmp_path / "upload" / "20261001" / "AC0G_EM38ww")
    assert not day_needs_retry(tmp_path, "20261001")


def test_a_day_packaged_before_the_marker_existed_is_not_retried(tmp_path):
    # Existing stations hold OBS* datasets with no marker.  Gate 3's own test
    # counts them as packaged, so the first night after deploy re-sends nothing.
    _source(tmp_path, "20261001")
    obs = tmp_path / "upload" / "20261001" / "AC0G_EM38ww" / "OBS2026-10-01T00-00"
    obs.mkdir(parents=True)
    assert not day_needs_retry(tmp_path, "20261001")


def test_the_marker_never_looks_like_a_dataset(tmp_path):
    # grape-psws ships OBS* directories; the marker must never match.
    marker = mark_day_packaged(tmp_path / "upload" / "20261001")
    assert not marker.name.startswith("OBS")


def test_grape_daily_marks_the_day_after_gate_3_and_the_sweep_uses_the_test():
    src = inspect.getsource(cli)
    gate = src.index("GATE PASSED: {len(obs_dirs)} dataset(s) ready")
    window = [line.strip() for line in src[gate:gate + 600].splitlines()]
    assert "mark_day_packaged(upload_dir)" in window
    assert "if not day_needs_retry(data_root, d):" in [
        line.strip() for line in src.splitlines()]


def test_a_marker_that_cannot_be_written_never_fails_the_product():
    # An OSError writing the marker costs one sweep retry.  Unguarded, it
    # would exit grape-daily before Stage 4/5: no status save, no cleanup,
    # no sweep.
    guarded = [
        call
        for node in ast.walk(ast.parse(inspect.getsource(cli)))
        if isinstance(node, ast.Try)
        and any(isinstance(h.type, ast.Name) and h.type.id == "OSError"
                for h in node.handlers)
        for stmt in node.body
        for call in ast.walk(stmt)
        if isinstance(call, ast.Call)
        and getattr(call.func, "id", None) == "mark_day_packaged"
    ]
    assert guarded, "mark_day_packaged(upload_dir) must sit inside try/except OSError"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/mjh/hamsci/repos/hamsci-physics && .venv/bin/pytest tests/test_grape_packaged_marker.py -v --override-ini="addopts="`
Expected: collection ERROR, `ImportError: cannot import name 'PACKAGED_MARKER'`.

- [ ] **Step 3: Add the two functions to `grape/spool.py`** (append at the end of the module;
  `spool.py` already imports `Path` at line 44)

```python
PACKAGED_MARKER = ".upload_complete"


def mark_day_packaged(day_dir: Path) -> Path:
    """Record that ``day_dir`` holds a finished GRAPE package.

    grape-daily's catch-up sweep skips any day whose ``upload/<day>/`` carries
    this marker.  Nothing wrote it after hf-timestd af45a6a retired SFTPUpload,
    so the sweep re-packaged days 2-7 back every night, and every re-package
    moved the OBS mtime past the send record, which re-sent the day.
    """
    day_dir.mkdir(parents=True, exist_ok=True)
    marker = day_dir / PACKAGED_MARKER
    marker.touch(exist_ok=True)
    return marker


def day_needs_retry(data_root: Path, day: str) -> bool:
    """True when the catch-up sweep should re-run ``day``.

    A day counts as packaged when ``upload/<day>/`` holds the marker anywhere
    below it, or already holds an OBS* dataset, the test Gate 3 applies.  The
    second test covers days packaged before the marker existed, so the first
    night after this change re-sends nothing.  An unpackaged day re-runs only
    while its source (raw_buffer or decimated) remains on disk."""
    day_dir = data_root / "upload" / day
    if day_dir.exists() and (any(day_dir.rglob(PACKAGED_MARKER))
                             or any(day_dir.rglob("OBS*"))):
        return False
    return (any((data_root / "raw_buffer").glob(f"*/{day}"))
            or any((data_root / "products").glob(f"*/decimated/{day}.bin")))
```

- [ ] **Step 4: Wire them into `cli.py`**

After the line `            print(f"   ✅ GATE PASSED: {len(obs_dirs)} dataset(s) ready")` (line 473,
twelve-space indent), add:

```python
            try:
                from .grape.spool import mark_day_packaged
                mark_day_packaged(upload_dir)
            except OSError as exc:  # a missing marker costs one retry, never the product
                print(f"   ⚠️  could not mark {upload_dir} packaged (continuing): {exc}")
```

In the sweep loop, replace lines 582-589 (twenty-space indent):

```python
                    day_dir = data_root / 'upload' / d
                    if day_dir.exists() and list(day_dir.rglob('.upload_complete')):
                        continue
                    has_src = (any((data_root / 'raw_buffer').glob(f'*/{d}'))
                               or any((data_root / 'products')
                                      .glob(f'*/decimated/{d}.bin')))
                    if not has_src:
                        continue
```

with:

```python
                    from .grape.spool import day_needs_retry
                    if not day_needs_retry(data_root, d):
                        continue
```

In the sweep comment (lines 572-573 before this step, 577-578 after the Gate 3 insert), replace:

```python
            # the normal run, retry any of the previous 7 days that never
            # reached .upload_complete and still have source data, by
```

with:

```python
            # the normal run, retry any of the previous 7 days that
            # spool.day_needs_retry finds unpackaged with source data, by
```

- [ ] **Step 5: Run the tests to verify they pass, then the whole suite**

Run: `.venv/bin/pytest tests/test_grape_packaged_marker.py -v --override-ini="addopts="`
Expected: 10 passed.
Run: `.venv/bin/pytest tests/ -q --override-ini="addopts="`
Expected: no new failures.  The dry run counted 334 passed, 1 skipped, 9 subtests before; expect
344 passed after.  `tests/test_grape_sweep_retry.py` must stay green.

- [ ] **Step 6: Commit**

```bash
cd /home/mjh/hamsci/repos/hamsci-physics
git add src/hamsci_physics/grape/spool.py src/hamsci_physics/cli.py tests/test_grape_packaged_marker.py
git commit -m "grape: mark a day packaged so the sweep stops re-sending it

Nothing wrote upload/<day>/.upload_complete after hf-timestd af45a6a, so the
catch-up sweep re-packaged days 2-7 back every night and each re-package
re-sent the day through hs-uploader (sigmond tasks/plan-sink-control.md §10.3).
A day that already holds an OBS* dataset also counts as packaged, so the
first night after deploy re-sends nothing.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---

### Task 2: meteor-scatter seeds new instances with no in-process sender

**Files:**
- Modify: `meteor-scatter/deploy.toml:14-22` (the seed) and the comment at `deploy.toml:123-126`
- Modify: `meteor-scatter/README.md:89-91`, `docs/CONFIG.md:139`,
  `docs/ARCHITECTURE.md:111, 113-120, 379-382`, `docs/OPERATIONS.md:162-166`, 253-258 (the body
  under the heading at 251), 283-286 (the four diagram lines between the fences at 282 and 287);
  line 5 (the header) of each of the three `docs/` pages
- Modify: `meteor-scatter/CLAUDE.md:105-111` (the delivery-mode list)
- Modify: `meteor-scatter/src/meteor_scatter/core/hs_uploader_shim.py:330-334` (comment),
  `src/meteor_scatter/core/recorder.py:426` (comment), 580-581 (`_delivery_mode` docstring)
- Test: `meteor-scatter/tests/test_deploy_seed.py` (create)

**Interfaces:**
- Produces: new instances get `METEOR_SCATTER_DELIVERY_MODE=off`, so their rows carry
  `forward_to_pskreporter = false` and the daemon's pskreporter pipeline posts them.  The daemon's
  `psk-pskreporter` pipeline (psk-recorder/deploy.toml:133, meteor-scatter/deploy.toml:146,
  line 147 after Step 3)
  selects `forward_to_pskreporter = 0`.  Under the old `deposit` seed the flag read true, so the
  daemon never posted MSK144; only wd30's forwarder did.

- [ ] **Step 1: Write the failing tests**

```python
"""New meteor-scatter instances start with no in-process sender.

`off` starts no sender of its own and marks each row forward_to_pskreporter
false, so the station's hs-uploader daemon -- the one pskreporter sender per
set of rows -- posts MSK144 under the site sink switch
(sigmond tasks/plan-sink-control.md §10.2 step 1, §10.3 item 4).
"""
import os
import tomllib
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _seed() -> dict:
    with open(REPO / "deploy.toml", "rb") as f:
        return tomllib.load(f)["contract"]["instance_env"]


def test_new_instances_are_seeded_off():
    assert _seed()["METEOR_SCATTER_DELIVERY_MODE"] == "off"


def test_the_seed_starts_no_sender_and_leaves_rows_to_the_daemon():
    # What the seed must DO, not only what it reads: no in-process sender,
    # and rows flagged false, which the daemon's psk-pskreporter selects.
    from meteor_scatter.core import recorder as r
    rec = r.MeteorScatterRecorder.__new__(r.MeteorScatterRecorder)
    rec._station = {"callsign": "AC0G", "grid_square": "EM38ww"}
    rec._paths, rec._radiod_id, rec._uploaders = {}, "test", []
    rx = mock.Mock()
    rec._receivers, rec._cycle_batcher = [rx], mock.Mock()
    env = {"METEOR_SCATTER_DELIVERY_MODE": _seed()["METEOR_SCATTER_DELIVERY_MODE"]}
    with mock.patch.dict(os.environ, env), \
            mock.patch.object(r, "HsPskReporterUploader") as sender:
        rec._start_uploaders()
        rec._start_all_ch_tailers()
    sender.assert_not_called()
    assert rx.start_ch_tailers.call_args.kwargs["forward_flag"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/mjh/hamsci/repos/meteor-scatter && .venv/bin/pytest tests/test_deploy_seed.py -v`
Expected: both tests FAIL.  The seed reads `'deposit'`, and deposit flags rows `forward_flag` True.

- [ ] **Step 3: Replace `deploy.toml` lines 14-22** (from `# Greenfield per-instance env` through
  `METEOR_SCATTER_DELIVERY_MODE = "deposit"`)

```toml
# Greenfield per-instance env sigmond seeds into
# /etc/meteor-scatter/env/<reporter_id>.env when it creates an instance.
# "off" starts no in-process PSKReporter sender and marks every row
# forward_to_pskreporter = false, so the station's hs-uploader daemon -- the
# one sender per set of rows -- posts MSK144 under the site sink switch
# (sigmond tasks/plan-sink-control.md §10.2 step 1).  "deposit" would mark
# rows for wd30's forwarder, which drops spots older than 50 minutes.  A
# standalone host with no hs-uploader daemon sets "direct" by hand.
[contract.instance_env]
METEOR_SCATTER_DELIVERY_MODE = "off"
```

Then replace the comment that read lines 123-126 before this step (it now sits one line lower):

```toml
# Fold onto the daemon on a host: [contract.instance_env] below defaults meteor
# to METEOR_SCATTER_DELIVERY_MODE=deposit (decode/sink-only; the daemon's
# unified psk pipeline delivers), then `smd admin uploader manifest
# --write --enable`.
```

with:

```toml
# Fold onto the daemon on a host: [contract.instance_env] above seeds new
# instances with METEOR_SCATTER_DELIVERY_MODE=off (decode/sink-only, rows
# flagged forward_to_pskreporter=0, which this pipeline selects), then
# `smd admin uploader manifest --write --enable`.
```

- [ ] **Step 4: Make the docs and comments describe the new seed**

`README.md:91`: change `` (`METEOR_SCATTER_DELIVERY_MODE = "deposit"` on a `` to
`` (`METEOR_SCATTER_DELIVERY_MODE = "off"` on a ``.

`docs/CONFIG.md:139`, in the `METEOR_SCATTER_DELIVERY_MODE` row, replace
`` `off`/`none`/`disabled` publish nothing. sigmond seeds `deposit` from `deploy.toml [contract.instance_env]`. ``
with
`` `off`/`none`/`disabled` start no sender and flag rows `False`, so the host's hs-uploader daemon posts them through `psk-pskreporter`; a host without the daemon publishes nothing. sigmond seeds `off` from `deploy.toml [contract.instance_env]`. ``

`docs/ARCHITECTURE.md:111`, in the `off` / `none` / `disabled` row, replace the last cell
`nobody — rows sit in the sink and ride the cycle tar` with
`` the host's hs-uploader daemon (`psk-pskreporter` selects rows flagged `False`); nobody on a host without it ``.

`docs/ARCHITECTURE.md:113-120`, replace the paragraph that starts `A sigmond-managed host lands on`
with:

```markdown
A sigmond-managed host lands on **`off`**: `deploy.toml`'s
`[contract.instance_env]` seeds `METEOR_SCATTER_DELIVERY_MODE = "off"`
into `/etc/meteor-scatter/env/<reporter_id>.env` when sigmond creates the
instance, because on such a host the single hs-uploader daemon owns
egress for every client and posts MSK144 under the site sink switch.
Instances created earlier keep the `deposit` they were seeded with.
Flip to `direct` on a standalone host with no hs-uploader daemon. The
uploader also refuses to start when `[station].callsign` or `grid_square`
is empty (it logs a warning and returns).
```

`docs/ARCHITECTURE.md:379-382` (data-flow step 9; it reads 380-383 once the 113-120 edit has run),
replace:

```markdown
9. Either the in-process uploader POSTs it to pskreporter.info (mode
   `msk144` → `MSK144`), or — in `deposit` mode — the row carries
   `forward_to_pskreporter=True` and the wsprdaemon server's forwarder
   owns that hop.
```

with:

```markdown
9. In `direct` the in-process uploader POSTs it to pskreporter.info (mode
   `msk144` → `MSK144`). In `off`, what sigmond seeds, the row carries
   `forward_to_pskreporter=False` and the host's hs-uploader daemon posts
   it through `psk-pskreporter`. In `deposit` the row carries
   `forward_to_pskreporter=True` and the wsprdaemon server's forwarder
   owns that hop.
```

`docs/OPERATIONS.md:162-166`, replace step 5 (`5. **Delivery consistent with the mode you
configured** — in `deposit` …`) with:

```markdown
5. **Delivery consistent with the mode you configured** — in `off` (what
   sigmond seeds) and in `deposit` the journal says so at startup
   ("PSKReporter uploader disabled … deposit to the psk.spots sink only").
   `off` rows carry `forward_to_pskreporter=0`, and the host's hs-uploader
   daemon posts them through its `psk-pskreporter` pipeline; `deposit` rows
   carry `forward_to_pskreporter=1` for the wsprdaemon server's forwarder.
   In `direct` an `HsPskReporterUploader` line appears and it pumps every
   30 s.
```

`docs/OPERATIONS.md:253-258` (the body under the heading at 251), replace the section body under
`### Rows in the sink but nothing at PSKReporter` with:

```markdown
Check the delivery mode first (`grep DELIVERY
/etc/meteor-scatter/env/<instance>.env`). In `off`, the host's hs-uploader
daemon posts these rows: check that the site sink switch lets it send
(`smd sink status` should say `site sink: upload`) and that
`hs-uploader.service` runs. In `deposit` this is correct behaviour — the
wsprdaemon server's forwarder owns that hop, and the row's
`forward_to_pskreporter=1` is what tells it so. In `direct`, confirm the
uploader started at all: it refuses (with a warning) when
`[station].callsign` or `grid_square` is empty.
```

`docs/OPERATIONS.md:283-286` (the four diagram lines between the fences at 282 and 287), replace
the diagram inside the fence under `## Where the spots go` with (the `┼` and `┘` sit in column 63,
as before):

```
<radiod_id>-msk144.log  →  ChTailer  →  cycle batcher  →  psk.spots (mode="msk144")
                                                              │
                                     DELIVERY_MODE=direct ────┼──► pskreporter.info (this process)
                                     DELIVERY_MODE=off ───────┼──► hs-uploader daemon ──► pskreporter.info
                                     DELIVERY_MODE=deposit ───┘    (server forwarder)
```

`src/meteor_scatter/core/hs_uploader_shim.py:332`: change `(METEOR_SCATTER_DELIVERY_MODE=deposit)`
to `(METEOR_SCATTER_DELIVERY_MODE=off)`.

`src/meteor_scatter/core/recorder.py:426`, in `_start_all_ch_tailers`, replace
`        # "off"/"none"/"disabled" publish nothing externally (False).` with:

```python
        # "off"/"none"/"disabled" start no sender here and flag False, so a
        # host's hs-uploader daemon (psk-pskreporter) posts the rows.
```

`src/meteor_scatter/core/recorder.py:580-581`, in the `_delivery_mode` docstring, replace the
`"off"/"none"/"disabled"` entry with:

```python
        "off"/"none"/"disabled" — no in-process sender; rows carry
                    forward_to_pskreporter=False, so a host's hs-uploader
                    daemon (psk-pskreporter) posts them.  Without the daemon
                    nothing publishes; rows still ride the tar.
```

`CLAUDE.md:105-111` lists only `direct` and server-forwarded.  Replace
`` Two delivery modes selected by `METEOR_SCATTER_DELIVERY_MODE`: `` with
`` Three delivery modes selected by `METEOR_SCATTER_DELIVERY_MODE`: ``, and after the line
`  (Phase D, gw1-elected) does the upload.` add:

```markdown
- **off** (what sigmond seeds for new instances) — no in-process sender;
  rows carry `forward_to_pskreporter=False` and the host's hs-uploader
  daemon posts them through `psk-pskreporter`.
```

- [ ] **Step 4b: Bump each touched page's header.**  In `docs/ARCHITECTURE.md`, `docs/CONFIG.md`
  and `docs/OPERATIONS.md`, set line 5 to
  `> **Verified against:** meteor-scatter <sha> on <date> — delivery-mode text checked against deploy.toml and core/recorder.py`,
  where `<sha>` comes from `git rev-parse --short HEAD` before you commit (docs-freshness accepts
  the parent of the commit that edits the page) and `<date>` is today's UTC date.

- [ ] **Step 5: Run the tests to verify they pass, then the suite**

Run: `.venv/bin/pytest tests/test_deploy_seed.py -v` → 2 passed.
Run: `.venv/bin/pytest tests/ -q` → no new failures (dry run: 273 passed, 1 skipped before; 275
passed, 1 skipped after).
Run: `python3 /home/mjh/hamsci/repos/sigmond/scripts/docs-freshness.py docs README.md && python3 /home/mjh/hamsci/repos/sigmond/scripts/docs-linkcheck.py docs README.md`
→ the freshness report lists only `docs/SIGMOND-CONTRACT.md` (stale before this task); 0 broken
links.

- [ ] **Step 6: Commit**

```bash
cd /home/mjh/hamsci/repos/meteor-scatter
git add deploy.toml tests/test_deploy_seed.py README.md CLAUDE.md docs/ARCHITECTURE.md docs/CONFIG.md docs/OPERATIONS.md src/meteor_scatter/core/hs_uploader_shim.py src/meteor_scatter/core/recorder.py
git commit -m "deploy: seed new instances off, so the daemon posts MSK144 alone

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---

### Task 3: bring-up leaves the daemon as the only pskreporter sender

`smd config upload psk-recorder <rep> --on` writes `PSK_DELIVERY_PIPELINES=direct` unless `--via`
says otherwise (`lib/sigmond/upload.py:97-130`).  Bring-up must pass `--via server-raw`.

**Files:**
- Modify: `sigmond/lib/sigmond/bringup.py:339-350`
- Test: `sigmond/tests/test_bringup.py` (append one test)

**Interfaces:**
- Consumes: `build_plan(...)`, `Step.argv` (existing).

- [ ] **Step 1: Write the failing test** (append to `tests/test_bringup.py`)

```python
def test_bringup_leaves_the_daemon_as_the_only_pskreporter_sender():
    # tasks/plan-sink-control.md §10.2 step 1: server-raw starts no in-process
    # sender, so the daemon's psk-pskreporter posts these rows alone.
    p = build_plan(_dasi2(), local_radiod=True, reporter='AC0G/S')
    ups = {s.argv[3]: s.argv for s in p.steps
           if s.argv and s.argv[:3] == ['smd', 'config', 'upload']}
    assert ups['psk-recorder'][-2:] == ['--via', 'server-raw']
    assert '--via' not in ups['wspr-recorder']
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_bringup.py::test_bringup_leaves_the_daemon_as_the_only_pskreporter_sender -v`
Expected: FAIL (`['AC0G/S', '--on'] != ['--via', 'server-raw']`).

- [ ] **Step 3: Replace the step at `bringup.py:339-350`**

```python
            # Enable the instance's upstream upload.  For psk-recorder, pass
            # --via server-raw: it starts no in-process sender and leaves
            # forward_to_pskreporter false, so the daemon's psk-pskreporter
            # pipeline is the one sender for these rows, under the site sink
            # switch (tasks/plan-sink-control.md §10.2 step 1).  Without --via,
            # `--on` writes direct, and the in-process sender would post beside
            # the daemon.  Idempotent.
            argv = [smd, 'config', 'upload', client, reporter, '--on']
            if client == 'psk-recorder':
                argv += ['--via', 'server-raw']
            steps.append(Step(STAGE3A,
                              f'enable upload {client}@{reporter}',
                              'config',
                              argv=argv))
```

- [ ] **Step 4: Run the test and the bring-up suite**

Run: `.venv/bin/pytest tests/test_bringup.py tests/test_upload.py -v`
Expected: all pass (`test_upload.py` still pins `direct` as the manual default; that stays).

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/bringup.py tests/test_bringup.py
git commit -m "bringup: psk-recorder via server-raw, so the daemon posts alone

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---

### Task 4: `sink_doors.py` — set aside data stored while the site sink switch read `off`

`off` rides on the legacy `discard`, which acts only when the daemon ships.  GRAPE packages and
magnetometer zips built during `off` can therefore still ship later, above all where their
pipelines dropped out of the manifest for missing PSWS ids.  This module moves them out of reach,
and marks earlier GRAPE days packaged so the sweep never rebuilds them.  It also asks systemd
whether GRAPE or the magnetometer packages a day right now, so Task 5 can refuse rather than move
a package that a unit still writes.

**Files:**
- Create: `sigmond/lib/sigmond/sink_doors.py`
- Test: `sigmond/tests/test_sink_doors.py` (create)

**Interfaces:**
- Produces: `GRAPE_SPOOL`, `MAG_SPOOL` (Path); `PACKAGING_UNITS: tuple[str, ...]`;
  `class DoorError(RuntimeError)`;
  `@dataclass DoorReport(moved: list[tuple[Path, Path]], marked: list[Path])`;
  `packaging_running(units=PACKAGING_UNITS, *, run=subprocess.run) -> list[str]`;
  `held_dir(spool: Path, stamp: str) -> Path`;
  `set_aside(spool: Path, pattern: str, *, dirs: bool, stamp: str) -> list[tuple[Path, Path]]`;
  `mark_days_complete(spool: Path, today: date, *, days: int = 7, chown=os.chown) -> list[Path]`;
  `close_doors(now: datetime, *, grape: Path = GRAPE_SPOOL, mag: Path = MAG_SPOOL, chown=os.chown) -> DoorReport`.
  Task 5 calls `packaging_running` and then `close_doors`.

- [ ] **Step 1: Write the failing tests**

```python
"""Set aside data stored while the site sink switch read `off`
(tasks/plan-sink-control.md §10.3 item 3)."""
import os
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from sigmond import sink_doors as sd

NOW = datetime(2026, 10, 7, 15, 30, tzinfo=timezone.utc)


def _grape(root: Path):
    spool = root / "timestd" / "upload"
    obs = spool / "20261005" / "AC0G_EM38ww" / "OBS2026-10-05T00-00"
    (obs / "ch0").mkdir(parents=True)
    (obs / "ch0" / "rf@1.h5").write_bytes(b"x")
    return spool, obs


def _mag(root: Path):
    spool = root / "mag-recorder" / "upload"
    spool.mkdir(parents=True)
    z = spool / "mag_20261005.zip"
    z.write_bytes(b"z")
    return spool, z


def _systemctl(states):
    """A stand-in for subprocess.run that answers `systemctl is-active`."""
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=states[cmd[-1]] + "\n", stderr="")
    return run


def test_packaging_running_counts_a_oneshot_that_is_activating():
    # grape-daily and mag-recorder-upload run Type=oneshot: while one works,
    # systemd reports it `activating`, never `active`.
    run = _systemctl({"grape-daily.service": "activating",
                      "mag-recorder-upload.service": "inactive"})
    assert sd.packaging_running(run=run) == ["grape-daily.service"]


def test_packaging_running_on_a_host_without_systemd_reports_nothing():
    def run(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    assert sd.packaging_running(run=run) == []


def test_packaging_running_counts_a_unit_systemd_does_not_answer_for():
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 10)
    assert sd.packaging_running(run=run) == list(sd.PACKAGING_UNITS)


def test_set_aside_moves_obs_dirs_outside_the_spool_root(tmp_path):
    spool, obs = _grape(tmp_path)
    moved = sd.set_aside(spool, "OBS*", dirs=True, stamp="S")
    held = (tmp_path / "timestd" / "upload-held" / "S" / "20261005" / "AC0G_EM38ww"
            / "OBS2026-10-05T00-00")
    assert moved == [(obs, held)]
    assert held.is_dir() and not obs.exists()
    # The file source searches the whole tree; nothing may stay under the root.
    assert not list(spool.rglob("OBS*"))


def test_set_aside_moves_zips(tmp_path):
    spool, z = _mag(tmp_path)
    moved = sd.set_aside(spool, "*.zip", dirs=False, stamp="S")
    assert moved == [(z, tmp_path / "mag-recorder" / "upload-held" / "S" / "mag_20261005.zip")]
    assert not list(spool.rglob("*.zip"))


def test_set_aside_takes_only_the_outermost_match(tmp_path):
    spool, obs = _grape(tmp_path)
    (obs / "OBS-inner").mkdir()
    moved = sd.set_aside(spool, "OBS*", dirs=True, stamp="S")
    assert [src for src, _ in moved] == [obs]


def test_set_aside_on_a_missing_spool_does_nothing(tmp_path):
    assert sd.set_aside(tmp_path / "absent", "*.zip", dirs=False, stamp="S") == []


def test_set_aside_refuses_across_filesystems(tmp_path):
    spool, z = _mag(tmp_path)
    real_stat = os.stat

    def fake_stat(p, *a, **k):
        st = real_stat(p, *a, **k)
        if Path(p) == spool:
            fields = list(tuple(st))
            fields[2] = st.st_dev + 1          # st_dev
            return os.stat_result(fields)
        return st

    with mock.patch("sigmond.sink_doors.os.stat", side_effect=fake_stat):
        with pytest.raises(sd.DoorError) as caught:
            sd.set_aside(spool, "*.zip", dirs=False, stamp="S")
    msg = str(caught.value)
    # The refusal names the spool and the next step (§10.3 item 3).
    assert f"Move {spool} onto the same filesystem as {spool.parent}" in msg
    assert "fleet admin" in msg and "stays off" in msg
    assert z.exists()                           # nothing moved


def test_mark_days_complete_writes_seven_markers(tmp_path):
    spool = tmp_path / "upload"
    spool.mkdir()
    marks = sd.mark_days_complete(spool, date(2026, 10, 7), chown=mock.Mock())
    assert [m.parent.name for m in marks] == [
        "20261006", "20261005", "20261004", "20261003", "20261002", "20261001", "20260930"]
    assert all(m.name == ".upload_complete" and m.is_file() for m in marks)


def test_new_day_dirs_and_markers_take_the_spool_owner(tmp_path):
    spool = tmp_path / "upload"
    spool.mkdir()
    (spool / "20261006").mkdir()               # GRAPE already made this one
    st = spool.stat()
    chown = mock.Mock()
    sd.mark_days_complete(spool, date(2026, 10, 7), days=2, chown=chown)
    owned = [c.args for c in chown.call_args_list]
    assert (spool / "20261005", st.st_uid, st.st_gid) in owned          # created here
    assert (spool / "20261006" / ".upload_complete", st.st_uid, st.st_gid) in owned
    assert (spool / "20261005" / ".upload_complete", st.st_uid, st.st_gid) in owned
    assert all(args[0] != spool / "20261006" for args in owned)      # existing dir untouched


def test_mark_days_complete_on_a_missing_spool_does_nothing(tmp_path):
    assert sd.mark_days_complete(tmp_path / "absent", date(2026, 10, 7)) == []


def test_close_doors_covers_both_spools_under_one_utc_stamp(tmp_path):
    g, obs = _grape(tmp_path)
    m, z = _mag(tmp_path)
    report = sd.close_doors(NOW, grape=g, mag=m, chown=mock.Mock())
    assert {src for src, _ in report.moved} == {obs, z}
    assert all("20261007T153000Z" in str(dst) for _, dst in report.moved)
    assert len(report.marked) == 7
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_sink_doors.py -v`
Expected: FAIL with `ImportError: cannot import name 'sink_doors'`.

- [ ] **Step 3: Create `lib/sigmond/sink_doors.py`**

```python
"""Set aside data stored while the site sink switch read `off`.

In v3.69 the site sink switch's `off` rides on the legacy `[uploads] mode =
"discard"`, which acts only when hs-uploader ships.  GRAPE packages and
magnetometer zips built while it read `off` can therefore still ship once it
leaves `off` -- above all where their pipelines dropped out of the manifest
for missing PSWS ids, so nothing discarded them.  Before the written mode
leaves discard, sigmond moves them into a sibling directory OUTSIDE each
spool root (hs-uploader's file source searches the whole tree, hidden
directories included) and marks earlier GRAPE days packaged so the catch-up
sweep never rebuilds them.  The caller first asks `packaging_running()` and
refuses while a packaging unit runs, so sigmond never moves a package that
a unit still writes.  tasks/plan-sink-control.md §10.3 item 3.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Tuple

GRAPE_SPOOL = Path("/var/lib/timestd/upload")
MAG_SPOOL = Path("/var/lib/mag-recorder/upload")
PACKAGED_MARKER = ".upload_complete"     # hamsci-physics grape/spool.py, same name

# The units that write into those spools.  Both run Type=oneshot, so while
# one works systemd reports it `activating`, never `active`.
PACKAGING_UNITS = ("grape-daily.service", "mag-recorder-upload.service")
_RUNNING_STATES = ("active", "activating", "deactivating", "reloading")


class DoorError(RuntimeError):
    """A set-aside that refused, or stopped partway.  The caller leaves the
    site sink switch at `off`; anything moved before a stop stays in the
    held directory."""


@dataclass
class DoorReport:
    moved: List[Tuple[Path, Path]] = field(default_factory=list)
    marked: List[Path] = field(default_factory=list)


def packaging_running(units=PACKAGING_UNITS, *, run=subprocess.run) -> List[str]:
    """The packaging units systemd reports running right now.  A unit that
    systemd does not answer for counts as running; a host without systemctl
    runs none."""
    busy: List[str] = []
    for unit in units:
        try:
            r = run(["systemctl", "is-active", unit],
                    capture_output=True, text=True, timeout=10)
        except FileNotFoundError:
            return []
        except (OSError, subprocess.SubprocessError):
            busy.append(unit)
            continue
        if r.stdout.strip() in _RUNNING_STATES:
            busy.append(unit)
    return busy


def held_dir(spool: Path, stamp: str) -> Path:
    """`<spool>-held/<stamp>/`, a sibling of the spool root, outside it."""
    return spool.parent / f"{spool.name}-held" / stamp


def set_aside(spool: Path, pattern: str, *, dirs: bool, stamp: str) -> List[Tuple[Path, Path]]:
    """Move every outermost match of `pattern` under `spool` into
    `held_dir(spool, stamp)`, keeping its path relative to the spool.
    `dirs` picks directory matches (GRAPE OBS*) or file matches (*.zip).
    Refuses before moving anything when the spool and its parent sit on
    different filesystems, since the move must stay a rename."""
    if not spool.is_dir():
        return []
    if os.stat(spool).st_dev != os.stat(spool.parent).st_dev:
        raise DoorError(
            f"{spool} sits on a different filesystem from {spool.parent}, so "
            "sigmond cannot set its packages aside by rename.  Move "
            f"{spool} onto the same filesystem as {spool.parent}, or ask your "
            "fleet admin; the site sink switch stays off.")
    chosen: List[Path] = []
    for p in sorted(spool.rglob(pattern)):
        if p.is_dir() != dirs:
            continue
        if any(c in p.parents for c in chosen):
            continue
        chosen.append(p)
    dest_root = held_dir(spool, stamp)
    moved: List[Tuple[Path, Path]] = []
    for src in chosen:
        dst = dest_root / src.relative_to(spool)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(src, dst)
        except OSError as exc:
            raise DoorError(
                f"could not move {src} to {dst}: {exc}.  The site sink switch "
                f"stays off; whatever moved before this sits in {dest_root}.") from exc
        moved.append((src, dst))
    return moved


def mark_days_complete(spool: Path, today: date, *, days: int = 7,
                       chown=os.chown) -> List[Path]:
    """Write `<spool>/<YYYYMMDD>/.upload_complete` for each of the `days`
    days before `today`.  A day directory this creates, and every marker,
    takes the spool's owner: GRAPE's packager runs as that owner and must
    still write inside a day directory that root created."""
    if not spool.is_dir():
        return []
    st = spool.stat()
    marks: List[Path] = []
    for back in range(1, days + 1):
        day_dir = spool / (today - timedelta(days=back)).strftime("%Y%m%d")
        if not day_dir.exists():
            day_dir.mkdir()
            chown(day_dir, st.st_uid, st.st_gid)
        marker = day_dir / PACKAGED_MARKER
        if not marker.exists():
            marker.touch()
            chown(marker, st.st_uid, st.st_gid)
        marks.append(marker)
    return marks


def close_doors(now: datetime, *, grape: Path = GRAPE_SPOOL, mag: Path = MAG_SPOOL,
                chown=os.chown) -> DoorReport:
    """Everything §10.3 item 3 does before the written mode leaves discard.
    The caller checks `packaging_running()` first."""
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    report = DoorReport()
    report.moved += set_aside(grape, "OBS*", dirs=True, stamp=stamp)
    report.moved += set_aside(mag, "*.zip", dirs=False, stamp=stamp)
    report.marked = mark_days_complete(grape, now.date(), chown=chown)
    return report
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sink_doors.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/sink_doors.py tests/test_sink_doors.py
git commit -m "sink_doors: set aside data stored while the site sink switch read off

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---

### Task 5: leaving `off` closes the doors, and the messages tell the truth

The hook fires whenever the WRITTEN mode leaves `discard`, whatever the new setting, so
`discard → hold → on` cannot slip past the doors (spec §10.3 item 3).  The rendered mode,
`um.effective_mode`, chooses only the message.  Every line that `smd sink off|upload` prints comes
from the sink half of `_WORDS`.

**Files:**
- Modify: `sigmond/lib/sigmond/commands/config.py` (below `_regenerate_uploader_manifest`, which ends
  at line 1001: add `_close_doors`, `_SINK_SETTING`, `_WORDS`; `_confirm_discard`, lines 1013-1027;
  `cmd_config_uploads`, lines 1030-1122)
- Test: `sigmond/tests/test_site_profile.py` (class `ConfigUploadsVerbTests`, lines 391-524);
  `sigmond/tests/test_sink_doors.py` (append two tests of the hook itself)

**Interfaces:**
- Consumes: `sink_doors.packaging_running`, `sink_doors.close_doors`, `sink_doors.DoorError`
  (Task 4); `um.effective_mode(coord)`.
- Produces: `_close_doors() -> int` (module level, stubbable); `_WORDS: dict[str, dict[str, str]]`
  keyed `"legacy"` / `"sink"`, each with the keys `need_reason`, `need_yes`, `cannot_discard`,
  `confirm`, `confirm_word`, `unconfirmed`, `written`, `denied`, `on`, `nothing_ships`,
  `rendered_hold`, `hold_backlog`, `off`, `hold`; `cmd_config_uploads` reads
  `getattr(args, "sink_words", False)`.  Task 6 passes `sink_words=True`.

- [ ] **Step 1: Write the failing tests**

In `ConfigUploadsVerbTests.setUp`, after the `self.regen` lines, add a default stub so no test ever
touches `/var/lib`:

```python
        self.doors = mock.patch.object(cfg, "_close_doors", return_value=0)
        self.doors_mock = self.doors.start(); self.addCleanup(self.doors.stop)
```

Then append these tests to the class:

```python
    # -- leaving the site sink switch's `off` (tasks/plan-sink-control.md §10.3) --

    def test_leaving_discard_closes_the_doors_before_the_manifest(self):
        self._supports(True)
        self._run(uploads_command="discard", reason="bench", yes=True)
        order = []
        self.doors_mock.side_effect = lambda: order.append("doors") or 0
        self.regen_mock.side_effect = lambda: order.append("regen") or 0
        rc, _ = self._run(uploads_command="on")
        self.assertEqual(rc, 0)
        self.assertEqual(order, ["doors", "regen"])

    def test_a_door_failure_leaves_the_site_sink_off(self):
        from sigmond.coordination import load_coordination
        self._supports(True)
        self._run(uploads_command="discard", reason="bench", yes=True)
        self.regen_mock.reset_mock()
        self.doors_mock.return_value = 1
        rc, _ = self._run(uploads_command="on")
        self.assertEqual(rc, 1)
        self.assertEqual(load_coordination(self.coord).uploads.mode, "discard")
        self.regen_mock.assert_not_called()

    def test_on_from_upload_or_hold_leaves_the_doors_alone(self):
        self._run(uploads_command="on")
        self._run(uploads_command="hold", reason="x")
        self._run(uploads_command="on")
        self.doors_mock.assert_not_called()

    def test_leaving_off_through_hold_closes_the_doors(self):
        # discard -> hold -> on: the hook keys on the WRITTEN mode leaving
        # discard, whatever the new setting, so the doors close at the first
        # step and never again.
        self._supports(True)
        self._run(uploads_command="discard", reason="bench", yes=True)
        self._run(uploads_command="hold", reason="pause")
        self.doors_mock.assert_called_once()
        self._run(uploads_command="on")
        self.doors_mock.assert_called_once()

    def test_leaving_a_discard_rendered_as_hold_says_the_backlog_ships(self):
        # The policy reads discard, but this host's hs-uploader cannot discard,
        # so the manifest rendered hold and a backlog built up.
        from sigmond.commands.config import _patch_uploads_block
        from sigmond.coordination import Uploads
        _patch_uploads_block(self.coord, Uploads(mode="discard", reason="wizard"))
        self._supports(False)
        rc, out = self._run(uploads_command="on")
        self.assertEqual(rc, 0)
        self.assertNotIn("nothing recorded during discard will ship", out)
        self.assertIn("ships now", out)
        self.doors_mock.assert_called_once()     # the WRITTEN mode left discard
```

The existing `test_on_after_discard_restores_upload_and_drops_mode` keeps asserting
`"nothing recorded during discard will ship"`; it still passes, because `_supports(True)` holds and
the new legacy text keeps that phrase before its packaging caveat.

Every `ConfigUploadsVerbTests` and `SinkCliTests` test stubs `_close_doors`.  So append two tests of
the hook itself to `tests/test_sink_doors.py`; they use that file's `sd` and `mock` imports:

```python
# -- the config hook around close_doors (Task 5) ----------------------------

def test_the_config_hook_refuses_on_a_door_error_and_stamps_utc():
    import sigmond.commands.config as cfg
    seen = {}

    def fake(now, **kw):
        seen["now"] = now
        return sd.DoorReport()

    with mock.patch.object(sd, "packaging_running", return_value=[]):
        with mock.patch.object(sd, "close_doors", side_effect=sd.DoorError("cross-fs")):
            assert cfg._close_doors() == 1
        with mock.patch.object(sd, "close_doors", side_effect=fake):
            assert cfg._close_doors() == 0
    assert seen["now"].utcoffset() is not None
    assert seen["now"].utcoffset().total_seconds() == 0


def test_the_config_hook_refuses_while_packaging_runs(capsys):
    # §10.3 item 3: never move a package that a unit still writes.
    import sigmond.commands.config as cfg
    with mock.patch.object(sd, "packaging_running",
                           return_value=["grape-daily.service"]), \
            mock.patch.object(sd, "close_doors") as close:
        assert cfg._close_doors() == 1
    close.assert_not_called()
    out = "".join(capsys.readouterr())
    assert "grape-daily.service" in out
    assert "again" in out and "stays off" in out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_site_profile.py -k ConfigUploadsVerbTests -v`
Expected: every test in the class fails in setUp (`AttributeError: ... does not have the attribute
'_close_doors'`).
Run: `.venv/bin/pytest tests/test_sink_doors.py -v`
Expected: the two new tests fail (`AttributeError: module 'sigmond.commands.config' has no
attribute '_close_doors'`); the twelve from Task 4 pass.

- [ ] **Step 3: Implement in `commands/config.py`**

Below `_regenerate_uploader_manifest` (line 1001), add:

```python
def _close_doors() -> int:
    """Before the written mode leaves discard (the site sink switch's `off`),
    whatever the new setting: refuse while GRAPE or the magnetometer packages
    a day; otherwise set aside the GRAPE packages and magnetometer zips stored
    while it read off, and mark earlier GRAPE days packaged
    (tasks/plan-sink-control.md §10.3 item 3).  Isolated so tests can stub
    it.  Returns 0 on success, 1 on refusal; on 1 the caller writes nothing."""
    from datetime import datetime, timezone
    from .. import sink_doors
    busy = sink_doors.packaging_running()
    if busy:
        err(f'refusing while packaging runs ({", ".join(busy)}): setting its '
            'spool aside now could split a package.  Run the command again '
            'when it finishes; GRAPE packs from about 01:00 UTC for up to '
            'three hours, the magnetometer at about 03:00 UTC.  The site sink '
            'switch stays off.')
        return 1
    try:
        report = sink_doors.close_doors(datetime.now(timezone.utc))
    except sink_doors.DoorError as exc:
        err('could not set aside data stored while the site sink switch read off: '
            f'{exc}')
        return 1
    for src, dst in report.moved:
        info(f'set aside {src} -> {dst}')
    if report.marked:
        info(f'marked {len(report.marked)} earlier GRAPE days packaged, so the '
             'catch-up sweep never rebuilds them')
    return 0


# The two vocabularies one handler speaks: the legacy `smd upload` words, and
# the sink words of tasks/plan-sink-control.md §2.1 for `smd sink`.  Every
# line `smd sink off|upload` prints comes from the 'sink' half.
_SINK_SETTING = {'upload': 'upload', 'discard': 'off', 'hold': 'hold (legacy)'}

_WORDS = {
    'legacy': {
        'need_reason': 'discard needs --reason, e.g. --reason "bench provisioning '
                       'for DASI-021"; the fleetboard shows it',
        'need_yes': 'discard needs --yes when not run at a terminal',
        'cannot_discard': '{python} cannot import hs_uploader.transports.discard: '
                          'this hs-uploader would ignore discard and SHIP.  Update '
                          'hs-uploader first; policy unchanged.',
        'confirm': 'Discard: data recorded from now on will never ship. '
                   'Type "discard" to confirm: ',
        'confirm_word': 'discard',
        'unconfirmed': 'discard not confirmed; policy unchanged',
        'written': '{path}: [uploads] mode = {mode}',
        'denied': 'permission denied writing the policy; re-run smd as root',
        'on': 'uploads on — outbound data pipelines restored in the manifest',
        'nothing_ships': 'nothing recorded during discard will ship, except the '
                         'GRAPE and magnetometer packages for this UTC day, and for '
                         'the day before if its packaging has not yet run',
        'rendered_hold': 'this host rendered hold, not discard: the backlog stored '
                         'since then ships now, oldest first',
        'hold_backlog': 'the backlog stored during hold ships now, oldest first',
        'off': 'discard mode — data pipelines ack without shipping',
        'hold': 'uploads held — manifest is heartbeat-only',
    },
    'sink': {
        'need_reason': 'smd sink off needs --reason, e.g. --reason "bench checkout"; '
                       'the fleetboard shows it',
        'need_yes': 'smd sink off needs --yes when not run at a terminal',
        'cannot_discard': "this host's hs-uploader ({python}) cannot discard and "
                          'would send everything; update hs-uploader first.  The '
                          'site sink switch is unchanged.',
        'confirm': 'Site sink switch to off: data recorded from now on will never ship. '
                   'Type "off" to confirm: ',
        'confirm_word': 'off',
        'unconfirmed': 'not confirmed; the site sink switch is unchanged',
        'written': '{path}: site sink switch set to {setting}',
        'denied': 'permission denied writing the site sink switch; re-run smd as root',
        'on': 'site sink: upload — every data pipeline is back in the manifest',
        'nothing_ships': 'nothing recorded while the site sink switch read off will '
                         'ship, except the GRAPE and magnetometer packages for this '
                         'UTC day, and for the day before if its packaging (01:00 '
                         'to about 04:00 UTC) has not yet run',
        'rendered_hold': 'this host could not discard, so it stored data while the '
                         'site sink switch read off; that backlog ships now, oldest '
                         'first',
        'hold_backlog': 'the backlog stored under the legacy hold ships now, oldest first',
        'off': 'site sink: off — no data ships and no backlog builds',
        'hold': 'site sink: hold (legacy) — the manifest is heartbeat-only',
    },
}
```

Replace `_confirm_discard` (lines 1013-1027) with:

```python
def _confirm_discard(args, words=None) -> bool:
    """Discard throws data away, so a person at a terminal confirms it.
    Scripts pass --yes; a non-interactive caller without it is refused."""
    import sys
    words = words or _WORDS['legacy']
    if getattr(args, 'yes', False):
        return True
    if not sys.stdin.isatty():
        err(words['need_yes'])
        return False
    try:
        answer = input(words['confirm'])
    except EOFError:
        return False
    return answer.strip().lower() == words['confirm_word']
```

In `cmd_config_uploads`, right after `    coord = load_coordination(COORDINATION_PATH)`, add:

```python
    words = _WORDS['sink' if getattr(args, 'sink_words', False) else 'legacy']
```

Leave the `status` branch as it is; `smd sink status` has its own (Task 6).  Replace the rest of
the function, from `    mode = UPLOAD_VERBS.get(verb)` through the final `    return rc`, with:

```python
    mode = UPLOAD_VERBS.get(verb)
    if mode is None:
        err(f'upload: unknown verb {verb!r} (status|on|hold|off|discard)')
        return 2

    reason = (getattr(args, 'reason', None) or '').strip()
    if mode == 'discard':
        if not reason:
            err(words['need_reason'])
            return 2
        if not um.hs_uploader_supports_discard():
            err(words['cannot_discard'].format(python=um.HS_UPLOADER_PYTHON))
            return 1
        if not _confirm_discard(args, words):
            info(words['unconfirmed'])
            return 1
    elif mode == 'hold' and not reason:
        warn('no --reason given; the board will say "held by policy" '
             'with no why — consider re-running with --reason')

    # Leaving the written discard -- the site sink switch's `off` -- for ANY
    # setting closes the doors first, so discard -> hold -> on cannot slip
    # past them (tasks/plan-sink-control.md §10.3 item 3).  _close_doors
    # prints its own refusal, which says the switch stays off.
    if mode != 'discard' and coord.uploads.mode == 'discard':
        if _close_doors():
            return 1

    up = Uploads(mode=mode, reason='' if mode == 'upload' else reason)

    try:
        for path in (SITE_PROFILE_PATH, COORDINATION_PATH):
            if path is COORDINATION_PATH or path.exists():
                _patch_uploads_block(path, up)
                ok(words['written'].format(path=path, mode=mode,
                                           setting=_SINK_SETTING[mode]))
    except PermissionError:
        err(words['denied'])
        return 1

    rc = _regenerate_uploader_manifest()
    if mode == 'upload':
        ok(words['on'])
        if coord.uploads.mode == 'discard':
            # The mode the manifest rendered decides what truly ships.
            if um.effective_mode(coord) == 'discard':
                info(words['nothing_ships'])
            else:
                warn(words['rendered_hold'])
        elif coord.uploads.mode == 'hold':
            info(words['hold_backlog'])
    elif mode == 'discard':
        ok(words['off'] + (f' ({reason})' if reason else ''))
    else:
        ok(words['hold'] + (f' ({reason})' if reason else ''))
    return rc
```

`str.format` ignores keyword arguments a template does not name, so one `written` call serves both
vocabularies.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_site_profile.py tests/test_sink_doors.py tests/test_coordination.py tests/test_uploader_manifest.py -v`
Expected: all pass, including the five new `ConfigUploadsVerbTests` tests, the two new hook tests,
and every existing `ConfigUploadsVerbTests` test.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/commands/config.py tests/test_site_profile.py tests/test_sink_doors.py
git commit -m "upload: leaving off sets data aside first; say truly what ships

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---

### Task 6: `smd sink off|upload|status`, the site form

Besides the new command, this task makes the legacy surfaces truthful.  `smd upload --help` stops
promising that hold keeps everything and that ending discard ships nothing.  The `pipelines.toml`
banner, the manifest generator's log lines and `smd admin uploader manifest`'s summary line all
print during `smd sink off|upload`, so they switch to sink words and teach `smd sink upload`
instead of `smd upload on`.

**Files:**
- Create: `sigmond/lib/sigmond/commands/sink.py`
- Modify: `sigmond/bin/smd` (module docstring, line 36; `_add_upload_verbs`, lines 678-698;
  `p_upload` help, lines 24649-24651; parser beside `p_upload`, ≈ line 24652; dispatch beside
  `args.command == 'upload'`, ≈ line 24788)
- Modify: `sigmond/lib/sigmond/uploader_manifest.py` (`policy_banner`, lines 400-429; the three
  log lines in `generate()`, lines 464-476)
- Modify: `sigmond/lib/sigmond/commands/uploader.py` (the two summary prints, lines 114-120)
- Modify: `sigmond/docs/contributor/orchestration.md` (new `sink` row before `status`, line 151;
  the `upload` row, line 157)
- Test: `sigmond/tests/test_sink_cli.py` (create); `tests/test_uploader_manifest.py` (lines 401,
  410, 453, 460); `tests/test_uploader_cmd_policy.py` (lines 33, 41-42, 47);
  `tests/test_docs_cli_table.py` (existing) must stay green

**Interfaces:**
- Consumes: `cmd_config_uploads(args)` with `args.sink_words = True` (Task 5); `um.effective_mode`,
  `um.suppressed_pipelines`.
- Produces: `cmd_sink(args) -> int` reading `args.sink_command` in `{None,'status','off','upload','fill'}`,
  `args.reason`, `args.yes`.  The manifest banner's first line reads
  `*** SITE SINK OFF: DATA PIPELINES ACK WITHOUT SENDING ***` when it renders discard; Task 9 greps
  `SITE SINK OFF`.

- [ ] **Step 1: Write the failing tests** (`tests/test_sink_cli.py`)

```python
"""`smd sink off|upload|status` -- the site sink switch in the sink vocabulary
(tasks/plan-sink-control.md §2.1, §10.3 item 1)."""
import contextlib
import io
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import sigmond.commands.config as cfg
from sigmond.commands import sink


class SinkCliTests(unittest.TestCase):
    def setUp(self):
        d = TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.coord = Path(d.name) / "coordination.toml"
        self.coord.write_text('[host]\ncall = "DASI002"\ngrid = "FN21ok"\n')
        self.profile = Path(d.name) / "site-profile.toml"
        self.profile.write_text('[station]\ncallsign = "DASI002"\ngrid_square = "FN21ok"\n')
        for name, val in (("COORDINATION_PATH", self.coord), ("SITE_PROFILE_PATH", self.profile)):
            p = mock.patch.object(cfg, name, val); p.start(); self.addCleanup(p.stop)
        self.regen = mock.patch.object(cfg, "_regenerate_uploader_manifest", return_value=0).start()
        self.doors = mock.patch.object(cfg, "_close_doors", return_value=0).start()
        self.addCleanup(mock.patch.stopall)
        mock.patch("sigmond.uploader_manifest.hs_uploader_supports_discard", return_value=True).start()
        mock.patch("sigmond.uploader_manifest.suppressed_pipelines",
                   return_value=["wspr-wsprnet"]).start()

    def _run(self, **kw):
        args = types.SimpleNamespace(**{"sink_command": None, "reason": None, "yes": False, **kw})
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = sink.cmd_sink(args)
        return rc, out.getvalue()

    def _mode(self):
        from sigmond.coordination import load_coordination
        return load_coordination(self.coord).uploads.mode

    def test_off_writes_the_legacy_discard_and_speaks_sink(self):
        rc, out = self._run(sink_command="off", reason="bench", yes=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._mode(), "discard")
        self.assertIn("site sink: off", out)
        self.assertIn("site sink switch set to off", out)
        # No legacy words under `smd sink` (spec §2.1).
        self.assertNotIn("discard mode", out)
        self.assertNotIn("[uploads] mode", out)

    def test_off_needs_a_reason(self):
        rc, out = self._run(sink_command="off", reason="", yes=True)
        self.assertEqual(rc, 2)
        self.assertIn("smd sink off needs --reason", out)
        self.regen.assert_not_called()

    def test_off_unconfirmed_off_a_terminal_changes_nothing(self):
        with mock.patch("sys.stdin.isatty", return_value=False):
            rc, out = self._run(sink_command="off", reason="bench", yes=False)
        self.assertEqual(rc, 1)
        self.assertEqual(self._mode(), "upload")
        self.assertIn("smd sink off needs --yes", out)

    def test_off_refuses_an_hs_uploader_that_would_ship(self):
        # §10.3 item 1: smd sink off keeps smd upload discard's refusal.
        with mock.patch("sigmond.uploader_manifest.hs_uploader_supports_discard",
                        return_value=False):
            rc, out = self._run(sink_command="off", reason="bench", yes=True)
        self.assertEqual(rc, 1)
        self.assertEqual(self._mode(), "upload")
        self.regen.assert_not_called()
        self.assertIn("cannot discard", out)
        self.assertNotIn("policy unchanged", out)

    def test_upload_after_off_closes_the_doors(self):
        self._run(sink_command="off", reason="bench", yes=True)
        rc, out = self._run(sink_command="upload")
        self.assertEqual(rc, 0)
        self.doors.assert_called_once()
        self.assertEqual(self._mode(), "upload")
        self.assertIn("site sink: upload", out)
        self.assertIn("read off will ship", out)
        self.assertNotIn("during discard", out)

    def test_fill_is_refused_and_changes_nothing(self):
        rc, out = self._run(sink_command="fill")
        self.assertEqual(rc, 2)
        self.assertIn("not available yet", out)
        self.regen.assert_not_called()
        self.assertEqual(self._mode(), "upload")

    def test_status_reports_upload(self):
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("site sink: upload", out)

    def test_status_reports_off_and_what_it_holds_back(self):
        self._run(sink_command="off", reason="bench", yes=True)
        rc, out = self._run(sink_command="status")
        self.assertEqual(rc, 0)
        self.assertIn("site sink: off", out)
        self.assertIn("bench", out)
        self.assertIn("wspr-wsprnet", out)

    def test_status_warns_when_this_host_cannot_discard(self):
        self._run(sink_command="off", reason="bench", yes=True)
        with mock.patch("sigmond.uploader_manifest.hs_uploader_supports_discard",
                        return_value=False):
            rc, out = self._run(sink_command="status")
        self.assertEqual(rc, 0)
        self.assertIn("cannot discard", out)

    def test_status_names_a_legacy_hold(self):
        from sigmond.commands.config import _patch_uploads_block
        from sigmond.coordination import Uploads
        _patch_uploads_block(self.coord, Uploads(mode="hold", reason="station under test"))
        rc, out = self._run(sink_command="status")
        self.assertEqual(rc, 0)
        self.assertIn("hold (legacy)", out)
        self.assertIn("station under test", out)
        self.assertNotIn("filling", out)


class LegacyUploadHelpTests(unittest.TestCase):
    """`smd upload` stays as the legacy alias.  Its help must not promise what
    the legacy modes do not do (tasks/plan-sink-control.md §10.3 item 1)."""

    def test_upload_help_points_at_smd_sink_and_says_what_hold_keeps(self):
        repo = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=str(repo / "lib"), SIGMOND_NO_VENV_REEXEC="1")
        out = subprocess.run([sys.executable, str(repo / "bin" / "smd"), "upload", "--help"],
                             capture_output=True, text=True, env=env, timeout=60).stdout
        out = " ".join(out.split())          # argparse wraps help text
        self.assertIn("smd sink upload", out)
        self.assertIn("smd sink off", out)
        self.assertIn("about 24 h", out)
        self.assertIn("except the GRAPE and magnetometer packages", out)
        self.assertNotIn("store everything", out)
        self.assertNotIn("keeps the data", out)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_sink_cli.py -v`
Expected: FAIL with `ImportError: cannot import name 'sink'`.

- [ ] **Step 3: Create `lib/sigmond/commands/sink.py`**

```python
"""`smd sink off|upload|status` -- the site sink switch.

tasks/plan-sink-control.md §10.3 item 1: v3.69 ships the site form only, as a
thin wrapper over the legacy `[uploads] mode` handler (`off` writes discard,
`upload` writes upload).  `fill` waits for step 2 of §10.2, when it truly keeps
data: the legacy hold it would ride on keeps FT8 spots for one hour and WSPR
spots for 24 hours.  The per-client form, `smd sink <client>`, waits for step 5.
"""
from __future__ import annotations

import types

from ..ui import err, info, ok, warn
from . import config as cfg

FILL_NOT_YET = (
    '`smd sink fill` is not available yet.  The legacy hold it would use keeps FT8 '
    'spots for one hour and WSPR spots for 24 hours, so it cannot keep data for '
    'later sending.  Use `smd sink off` (send no data, keep no backlog) or '
    '`smd sink upload` (store and send).')


def cmd_sink(args) -> int:
    verb = getattr(args, 'sink_command', None) or 'status'
    if verb == 'status':
        return _status()
    if verb == 'fill':
        err(FILL_NOT_YET)
        return 2
    legacy_verb = {'off': 'discard', 'upload': 'on'}[verb]
    return cfg.cmd_config_uploads(types.SimpleNamespace(
        uploads_command=legacy_verb,
        reason=getattr(args, 'reason', None),
        yes=getattr(args, 'yes', False),
        sink_words=True))


def _status() -> int:
    from .. import uploader_manifest as um
    from ..coordination import load_coordination
    coord = load_coordination(cfg.COORDINATION_PATH)
    up = coord.uploads
    why = f' — {up.reason}' if up.reason else ''
    if up.mode == 'upload':
        ok('site sink: upload (store and send)')
    elif up.mode == 'discard':
        warn(f'site sink: off — no data ships and no backlog builds{why}')
        if um.effective_mode(coord) != 'discard':
            err('this host\'s hs-uploader cannot discard, so the manifest renders '
                'the legacy hold instead; update hs-uploader')
    else:
        warn('site sink: hold (legacy) — stores for a while and sends no data; '
             f'FT8 spots last one hour and WSPR spots 24 hours{why}')
        info('`smd sink upload` ships what is still stored; `smd sink off` drops it')
    if up.mode != 'upload':
        try:
            held_back = um.suppressed_pipelines(coord=coord)
        except Exception as exc:  # pragma: no cover - defensive
            held_back = []
            warn(f'could not list the pipelines this affects: {exc}')
        if held_back:
            info('pipelines not sending: ' + ', '.join(held_back))
    info('the heartbeat still goes out; the site sink switch never stops it')
    return 0
```

- [ ] **Step 4: Register the command in `bin/smd`, and make the legacy help truthful**

Right after the `p_upload` block (`_add_upload_verbs(p_upload)`, ≈ line 24652), add:

```python
    p_sink = sub.add_parser('sink',
        help='the site sink switch: status | off (send no data, keep no backlog) | '
             'upload (store and send)')
    sink_sub = p_sink.add_subparsers(dest='sink_command')
    sink_sub.add_parser('status', help='show the site sink switch and what it holds back')
    s_off = sink_sub.add_parser('off',
        help='send no data and keep no backlog (a new or staged station)')
    s_off.add_argument('--reason', required=True,
                       help='why (on the fleetboard), e.g. "bench checkout"')
    s_off.add_argument('--yes', action='store_true',
                       help='skip the typed confirmation (for scripts)')
    sink_sub.add_parser('upload',
        help='store and send.  Leaving off first sets aside the GRAPE packages '
             'and magnetometer zips stored while off, and refuses while their '
             'packaging runs')
    sink_sub.add_parser('fill', help='not available yet (a later release)')
```

Right before `if args.command == 'upload':` (≈ line 24788), add:

```python
    if args.command == 'sink':
        verb = getattr(args, 'sink_command', None) or 'status'
        if verb not in ('status', 'fill') and _need_root(f'sink {verb}'):
            return 1
        from sigmond.commands.sink import cmd_sink
        return cmd_sink(args)
```

In the module docstring, replace line 36
(`    smd upload      status | on | hold | off | discard [--reason R]  what the station ships (sudo)`)
with:

```
    smd sink        status | off --reason R | upload                 the site sink switch (sudo)
    smd upload      status | on | hold | off | discard [--reason R]  legacy words for the same switch (sudo)
```

Replace the `p_upload` help (lines 24649-24651) with:

```python
    p_upload = sub.add_parser('upload',
        help='legacy words for the site sink switch (`smd sink`): status | on | '
             'hold / off | discard')
```

Replace `_add_upload_verbs` (lines 678-698) with:

```python
def _add_upload_verbs(p) -> None:
    """The legacy upload-mode verbs, shared by `smd upload` and the older
    `smd config uploads` (tasks/plan-upload-control.md).  `smd sink` sets the
    same switch in the sink words (tasks/plan-sink-control.md §2.1)."""
    vs = p.add_subparsers(dest='uploads_command')
    vs.add_parser('status', help='show the mode and the pipelines it affects; '
                                 '`smd sink status` says it in the sink words')
    for verb, text in (('on', 'store and send (normal operation); same as '
                              '`smd sink upload`'),
                       ('enable', 'same as on'),
                       ('hold', 'send no data; clients keep FT8 spots about 1 h '
                                'and WSPR spots about 24 h, then drop them; '
                                '`on` sends what is still stored'),
                       ('off', 'same as hold'),
                       ('disable', 'same as hold')):
        v = vs.add_parser(verb, help=text)
        if verb not in ('on', 'enable'):
            v.add_argument('--reason', help='why (on the fleetboard and in the '
                                            'manifest), e.g. "no HF antenna"')
    d = vs.add_parser('discard',
                      help='send no data and keep no backlog; same as '
                           '`smd sink off`.  Ending it sends nothing recorded '
                           'before, except the GRAPE and magnetometer packages '
                           'for that UTC day, and for the day before until its '
                           'packaging runs')
    d.add_argument('--reason', required=True,
                   help='why, e.g. "bench provisioning for DASI-021"')
    d.add_argument('--yes', action='store_true',
                   help='skip the typed confirmation (for scripts)')
```

- [ ] **Step 5: The manifest speaks sink words**

In `uploader_manifest.py`, replace the body of `policy_banner` after `why = …` (lines 410-429) with:

```python
    if mode == "discard":
        return [
            "#",
            "# *** SITE SINK OFF: DATA PIPELINES ACK WITHOUT SENDING ***",
            f"# coordination.toml [uploads] mode = \"discard\" sets the site sink switch to off{why}",
            "# Nothing leaves this host except the heartbeat, and no backlog builds.",
            "# `smd sink upload` sends nothing recorded before it, except the GRAPE and",
            "# magnetometer packages for that UTC day, and for the day before if its",
            "# packaging (01:00 to about 04:00 UTC) has not yet run.",
            "#   smd sink upload    (then the manifest regenerates)",
        ]
    lines = [
        "#",
        "# *** SITE SINK HOLD (LEGACY): DATA PIPELINES NOT RENDERED ***",
        f"# coordination.toml [uploads] enabled = false{why}",
        "# Only the station heartbeat leaves this host.  Clients keep FT8 spots",
        "# for one hour and WSPR spots for 24 hours.",
        "#   smd sink upload    (then the manifest regenerates; what is still stored ships)",
    ]
    if up.mode == "discard":
        lines[2:2] = ["# The site sink switch reads off ([uploads] mode = \"discard\"), but",
                      "# the hs-uploader this host runs cannot discard, so sigmond renders",
                      "# the legacy hold instead."]
    return lines
```

In `generate()`, replace the three log calls and the lines between them (464-476, from `logger.warning("uploader-manifest: DISCARD MODE%s` through `— rendering heartbeat only", reason)`) with:

```python
        logger.warning("uploader-manifest: SITE SINK OFF%s — data pipelines "
                       "ack without sending", reason)
    else:
        pipelines = []
        if coord.uploads.mode == "discard":
            logger.error(
                "uploader-manifest: the site sink switch reads off ([uploads] mode = "
                "discard), but %s cannot import hs_uploader.transports.discard — "
                "rendering the legacy hold (heartbeat only) instead; update "
                "hs-uploader", HS_UPLOADER_PYTHON)
        else:
            logger.warning(
                "uploader-manifest: SITE SINK HOLD (LEGACY) ([uploads] enabled = "
                "false%s) — rendering heartbeat only", reason)
```

In `commands/uploader.py`, replace the `if mode == "discard":` block and its two prints (lines 114-120) with:

```python
        if mode == "discard":
            print("uploader: SITE SINK OFF — these pipelines ack without "
                  "sending: " + ", ".join(suppressed))
        else:
            print("uploader: SITE SINK HOLD (LEGACY) ([uploads] enabled = false) "
                  "— not rendered: " + ", ".join(suppressed))
```

Update the tests that pin the old text:

- `tests/test_uploader_manifest.py:401`: `self.assertIn("DISABLED BY POLICY", text)` →
  `self.assertIn("SITE SINK HOLD (LEGACY)", text)`, and add `self.assertNotIn("smd upload on", text)`
  after it.
- `tests/test_uploader_manifest.py:410`: `self.assertNotIn("DISABLED BY POLICY", text)` →
  `self.assertNotIn("SITE SINK", text)`.
- `tests/test_uploader_manifest.py:453`: `self.assertIn("DISCARD MODE", text)` →
  `self.assertIn("SITE SINK OFF", text)`, and add `self.assertIn("smd sink upload", text)` and
  `self.assertNotIn("smd upload on", text)` after it.
- `tests/test_uploader_manifest.py:460`: `self.assertIn("predates discard", text)` →
  `self.assertIn("cannot discard", text)`.
- `tests/test_uploader_cmd_policy.py:33`: `self.assertIn("HELD BY POLICY", out)` →
  `self.assertIn("SITE SINK HOLD (LEGACY)", out)`.
- `tests/test_uploader_cmd_policy.py:41-42`: `self.assertIn("DISCARD MODE", out)` →
  `self.assertIn("SITE SINK OFF", out)`; `self.assertNotIn("HELD", out)` → `self.assertNotIn("HOLD", out)`.
- `tests/test_uploader_cmd_policy.py:47`: `self.assertNotIn("POLICY", out)` →
  `self.assertNotIn("SITE SINK", out)`.

`heartbeat.py:455-456` keeps its fleetboard string, `uploads disabled by policy`, because the
fleetboard parses it and `smd sink` never prints it.

- [ ] **Step 6: Docs rows** in `docs/contributor/orchestration.md`.  Immediately before the
  `` | `status` | `` row (line 151), add:

```markdown
| `sink` | operator | the site sink switch, in the sink words of tasks/plan-sink-control.md §2.1: `status`; `off` (send no data and keep no backlog; needs `--reason` and a typed confirmation or `--yes`); `upload` (store and send). Leaving `off`, by any verb, first sets the GRAPE packages and magnetometer zips stored while off aside, outside their spools, and refuses while their packaging runs. `fill` waits for a later release. It drives the same handler as `upload` | `cmd_sink` → `commands/sink.py`, `commands/config.py` (`cmd_config_uploads`, `_close_doors`), `sink_doors.py` | all but `status` and `fill` do (root) |
```

Replace the `` | `upload` | `` row (line 157 at f8c0a9b, 158 once the `sink` row sits above it) with:

```markdown
| `upload` | operator | the legacy words for the site sink switch, kept as an alias: `status`, `on` (store and send; same as `sink upload`), `hold`/`off` (send no data; clients keep FT8 spots about an hour and WSPR spots about a day, and `on` then sends what is still stored), `discard` (same as `sink off`: send no data and keep no backlog; needs `--reason` and a typed confirmation or `--yes`). `config uploads` reaches the same handler | `cmd_config_uploads` → `commands/config.py`, `coordination.py` (`Uploads.mode`), `uploader_manifest.py` (`effective_mode`, which renders the legacy hold when the hs-uploader the service runs cannot honour discard), `sink_doors.py` | all but `status` do (root) |
```

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/pytest tests/test_sink_cli.py tests/test_docs_cli_table.py tests/test_site_profile.py tests/test_uploader_manifest.py tests/test_uploader_cmd_policy.py -v`
Expected: all pass (the docs test now sees `sink` both in `smd --help` and in the table).
Run: `PYTHONPATH=lib SIGMOND_NO_VENV_REEXEC=1 python3 bin/smd sink --help`
Expected: lists `status`, `off`, `upload`, `fill`.
Run: `PYTHONPATH=lib SIGMOND_NO_VENV_REEXEC=1 python3 bin/smd upload --help`
Expected: no `store everything`, no `ships nothing from before`; `on` and `discard` name `smd sink`.

- [ ] **Step 8: Commit**

```bash
git add lib/sigmond/commands/sink.py bin/smd lib/sigmond/uploader_manifest.py lib/sigmond/commands/uploader.py docs/contributor/orchestration.md tests/test_sink_cli.py tests/test_uploader_manifest.py tests/test_uploader_cmd_policy.py
git commit -m "smd sink: the site sink switch -- off, upload, status

The legacy smd upload help, the pipelines.toml banner and the manifest
log lines now say what the modes truly do, in the sink words.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---

### Task 7: `smd config profile-install` keeps the site sink switch across a wizard run

The wizard rewrites `site-profile.toml` whole (`scripts/proxmox/sigmond-wizard.sh:1338`) on every run,
`--reconfigure` included, and `smd align` installs new wizards on existing stations.  This command
installs the wizard's new profile but carries any `[uploads]` block the old one declared.

Both header parsers must also accept a header with a trailing comment.  Uncommenting TEMPLATE's
example yields `[uploads]                        # outbound-uploads POLICY …`.  Today
`_patch_uploads_block` misses that header, appends a second `[uploads]` table, and leaves a file
tomllib rejects ("Cannot declare ('uploads',) twice").

**Files:**
- Modify: `sigmond/lib/sigmond/site_profile.py` (add `import re`; add `_split_block`, `carry_uploads_block`)
- Modify: `sigmond/lib/sigmond/commands/config.py` (add `import re`; header test in
  `_patch_uploads_block`, line 974; add `cmd_config_profile_install` right after `cmd_config_uploads`)
- Modify: `sigmond/bin/smd` (import at line 119 at f8c0a9b, 120 once Task 6 has landed; parser in
  the `config_sub` group after `render`'s arguments; dispatch in the `config` branch before
  `if sub_cmd == 'render':`, line 25100 at f8c0a9b, ≈ 25133 once Task 6 has landed)
- Modify: `sigmond/docs/contributor/orchestration.md` (the `config` row, line 141)
- Test: `sigmond/tests/test_site_profile.py` (append class `CarryUploadsTests`, class
  `ProfileInstallTests`; one test in the existing `UploadsBlockWriterTests`)

**Interfaces:**
- Produces: `carry_uploads_block(new_text: str, old_text: str | None) -> str`;
  `cmd_config_profile_install(args) -> int` reading `args.path`.  Task 8 calls
  `smd config profile-install /etc/sigmond/site-profile.toml.new`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_site_profile.py`)

```python
class CarryUploadsTests(unittest.TestCase):
    """A wizard rewrite never resets the site sink switch
    (tasks/plan-sink-control.md §10.3 item 2)."""

    NEW = '[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n\n[reporters]\nreporter_id = "AC0G/B4"\n'
    OFF = '\n[uploads]\nenabled = false\nmode    = "discard"\nreason  = "new station"\n'
    UPLOAD = '[station]\ncallsign = "OLD"\n\n[uploads]\nenabled = true\nreason = ""\n\n[psws]\nenabled = false\n'

    def _parse(self, text):
        import tomllib
        return tomllib.loads(text)

    def test_reconfigure_keeps_an_upload_block(self):
        from sigmond.site_profile import carry_uploads_block
        out = self._parse(carry_uploads_block(self.NEW, self.UPLOAD))
        self.assertEqual(out["uploads"]["enabled"], True)
        self.assertEqual(out["station"]["callsign"], "AC0G")      # the new profile wins elsewhere
        self.assertNotIn("psws", out)                              # old blocks do not ride along

    def test_a_first_install_keeps_its_off_block(self):
        from sigmond.site_profile import carry_uploads_block
        new = self.NEW + self.OFF
        self.assertEqual(carry_uploads_block(new, None), new)
        self.assertEqual(carry_uploads_block(new, '[station]\ncallsign = "x"\n'), new)

    def test_an_old_block_wins_over_a_new_one(self):
        from sigmond.site_profile import carry_uploads_block
        out = self._parse(carry_uploads_block(self.NEW + self.OFF, self.UPLOAD))
        self.assertEqual(out["uploads"].get("mode", "upload"), "upload")
        self.assertTrue(out["uploads"]["enabled"])

    def test_a_commented_example_is_not_a_block(self):
        from sigmond.site_profile import TEMPLATE, carry_uploads_block
        self.assertEqual(carry_uploads_block(self.NEW, TEMPLATE), self.NEW)
        # TEMPLATE's own example header carries a trailing comment, so it alone
        # cannot tell a commented header from a live one; this one can.
        commented = '[station]\ncallsign = "OLD"\n\n# [uploads]\n# enabled = false\n'
        self.assertEqual(carry_uploads_block(self.NEW, commented), self.NEW)

    def test_a_header_with_a_trailing_comment_is_carried(self):
        # Uncommenting TEMPLATE's example yields exactly this header line.
        from sigmond.site_profile import carry_uploads_block
        old = ('[station]\ncallsign = "OLD"\n\n'
               '[uploads]                        # outbound-uploads POLICY\n'
               'enabled = false\nreason = "pause"\n')
        out = self._parse(carry_uploads_block(self.NEW, old))
        self.assertEqual(out["uploads"], {"enabled": False, "reason": "pause"})
        self.assertEqual(out["station"]["callsign"], "AC0G")

    def test_an_absent_block_stays_absent(self):
        from sigmond.site_profile import carry_uploads_block
        out = self._parse(carry_uploads_block(self.NEW, '[station]\ncallsign = "OLD"\n'))
        self.assertNotIn("uploads", out)


class ProfileInstallTests(unittest.TestCase):
    def setUp(self):
        import sigmond.commands.config as cfg
        self.cfg = cfg
        d = TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        self.profile = self.dir / "site-profile.toml"
        p = mock.patch.object(cfg, "SITE_PROFILE_PATH", self.profile)
        p.start(); self.addCleanup(p.stop)

    def _run(self, path):
        import io, contextlib, types
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = self.cfg.cmd_config_profile_install(types.SimpleNamespace(path=str(path)))
        return rc, out.getvalue()

    def test_installs_carries_the_block_and_removes_the_new_file(self):
        self.profile.write_text(CarryUploadsTests.UPLOAD)
        new = self.dir / "site-profile.toml.new"
        new.write_text(CarryUploadsTests.NEW)
        rc, out = self._run(new)
        self.assertEqual(rc, 0, out)
        self.assertFalse(new.exists())
        self.assertIn("[uploads]", self.profile.read_text())
        self.assertIn("kept the site sink switch", out)

    def test_refuses_invalid_toml_and_leaves_the_old_profile(self):
        self.profile.write_text(CarryUploadsTests.UPLOAD)
        new = self.dir / "site-profile.toml.new"
        new.write_text("[station\nbroken")
        rc, _ = self._run(new)
        self.assertEqual(rc, 1)
        self.assertEqual(self.profile.read_text(), CarryUploadsTests.UPLOAD)
```

Append this test to the existing class `UploadsBlockWriterTests`:

```python
    def test_a_header_with_a_trailing_comment_is_replaced_not_duplicated(self):
        # Uncommenting TEMPLATE's example yields this header.  The patcher used
        # to miss it, append a second [uploads] table, and leave a file that
        # tomllib rejects: "Cannot declare ('uploads',) twice".
        from sigmond.commands.config import _patch_uploads_block
        from sigmond.coordination import Uploads, load_coordination
        p = self._tmp(text='[host]\ncall = "AC0G"\n\n'
                           '[uploads]                        # outbound-uploads POLICY\n'
                           'enabled = false\nreason = "pause"\n')
        _patch_uploads_block(p, Uploads(enabled=True))
        self.assertEqual(p.read_text().count("[uploads]"), 1)
        self.assertTrue(load_coordination(p).uploads.enabled)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_site_profile.py -k "CarryUploads or ProfileInstall or UploadsBlockWriter" -v`
Expected: FAIL.  The six `CarryUploadsTests` fail with `ImportError: cannot import name
'carry_uploads_block'`; the two `ProfileInstallTests` with `AttributeError: module
'sigmond.commands.config' has no attribute 'cmd_config_profile_install'`;
`test_a_header_with_a_trailing_comment_is_replaced_not_duplicated` with `AssertionError: 2 != 1`.
The four older `UploadsBlockWriterTests` pass.

- [ ] **Step 3: Add to `site_profile.py`**

Add `import re` to the module imports (after `from __future__ import annotations`).  Append:

```python
def _split_block(text: str, name: str) -> tuple:
    """(text without the [name] table and its [name.*] subtables, that block).
    Only a header at line start counts; a commented example does not.  A
    header may carry a trailing comment, as TEMPLATE's example does once
    uncommented: `[uploads]   # outbound-uploads POLICY`."""
    header = re.compile(r'\[\s*' + re.escape(name) + r'\s*(\]|\.)')
    keep, block, inside = [], [], False
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith('['):
            inside = bool(header.match(s))
        (block if inside else keep).append(line)
    return ''.join(keep), ''.join(block)


def carry_uploads_block(new_text: str, old_text) -> str:
    """The profile a wizard run should install: ``new_text``, except that an
    [uploads] block the old profile declares survives unchanged.  The wizard
    rewrites site-profile whole on every run, and `smd align` installs new
    wizards on stations whose operator already set the site sink switch;
    a rewrite must never reset it (tasks/plan-sink-control.md §10.3 item 2)."""
    if not old_text:
        return new_text
    _, old_block = _split_block(old_text, 'uploads')
    if not old_block.strip():
        return new_text
    rest, _ = _split_block(new_text, 'uploads')
    return rest.rstrip('\n') + '\n\n' + old_block.strip('\n') + '\n'
```

- [ ] **Step 4: Fix `_patch_uploads_block`'s header test, and add `cmd_config_profile_install`
  to `commands/config.py`**

Add `import re` to the module imports (after `import os`).  In `_patch_uploads_block`, replace
line 974, `        if s == '[uploads]':`, with:

```python
        # A header may carry a trailing comment ("[uploads]   # policy ...").
        # Only the [uploads] table itself starts the block; a [uploads.x]
        # subtable (none exists today) ends it and stays, as before.
        if re.match(r'\[\s*uploads\s*\]', s):
```

Right after `cmd_config_uploads`, add:

```python
def cmd_config_profile_install(args) -> int:
    """`smd config profile-install <path>`: install a new site-profile.toml
    (the wizard writes it beside the old one) and keep any [uploads] block the
    old profile declared (tasks/plan-sink-control.md §10.3 item 2)."""
    import shutil
    import tomllib
    from ..site_profile import carry_uploads_block
    new_path = Path(args.path)
    try:
        new_text = new_path.read_text()
    except OSError as exc:
        err(f'cannot read {new_path}: {exc}')
        return 1
    old_text = SITE_PROFILE_PATH.read_text() if SITE_PROFILE_PATH.exists() else None
    text = carry_uploads_block(new_text, old_text)
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        err(f'{new_path} is not valid TOML: {exc}; site-profile.toml unchanged')
        return 1
    tmp = SITE_PROFILE_PATH.with_name(SITE_PROFILE_PATH.name + '.tmp')
    tmp.write_text(text)
    if SITE_PROFILE_PATH.exists():
        shutil.copymode(SITE_PROFILE_PATH, tmp)
    os.replace(tmp, SITE_PROFILE_PATH)
    new_path.unlink(missing_ok=True)
    if text != new_text:
        info('kept the site sink switch already set in site-profile.toml')
    ok(f'installed {SITE_PROFILE_PATH}')
    return 0
```

(`os`, `Path`, `err`, `info`, `ok` and `SITE_PROFILE_PATH` already reach `config.py` through its
module imports; the dry run needed no other import.)

- [ ] **Step 5: Register it in `bin/smd`**

Add `cmd_config_profile_install` to the import at line 119 at f8c0a9b, 120 once Task 6 has
landed.  After the `render` parser's last
argument (`--if-present`) in the `config_sub` group, add:

```python
    p = config_sub.add_parser('profile-install',
                              help='install a new site-profile.toml (the wizard writes '
                                   'it beside the old one), keeping a site sink switch '
                                   'already set in [uploads]')
    p.add_argument('path', help='the new profile, e.g. /etc/sigmond/site-profile.toml.new')
```

In the `config` dispatch, right before `if sub_cmd == 'render':` (≈ line 25133 after Task 6), add:

```python
        if sub_cmd == 'profile-install':
            if _need_root('config profile-install'):
                return 1
            return cmd_config_profile_install(args)
```

- [ ] **Step 6: Docs row.**  In `docs/contributor/orchestration.md` line 141, the `config` row, change
  `` also `catalog-prune`, `backup`, `restore`, `uploads`, `register-radiod` `` to
  `` also `catalog-prune`, `backup`, `restore`, `uploads`, `profile-install` (the wizard installs a new site-profile.toml through it, keeping a site sink switch already set), `register-radiod` ``,
  and add `` `site_profile.py` (`carry_uploads_block`) `` to that row's module cell after
  `` `commands/config.py` ``.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/pytest tests/test_site_profile.py tests/test_docs_cli_table.py -v`
Expected: all pass (`profile-install` sits under `config`, which the docs test does not enumerate).
Run: `PYTHONPATH=lib SIGMOND_NO_VENV_REEXEC=1 python3 bin/smd config profile-install --help` → exits 0.

- [ ] **Step 8: Commit**

```bash
git add lib/sigmond/site_profile.py lib/sigmond/commands/config.py bin/smd docs/contributor/orchestration.md tests/test_site_profile.py
git commit -m "config profile-install: a wizard rewrite keeps the site sink switch

Both [uploads] header parsers now accept a trailing comment; the
block patcher used to append a second table that tomllib rejects.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---

### Task 8: the wizard starts a fresh install with the site sink switch at `off`

**Files:**
- Modify: `sigmond/scripts/proxmox/sigmond-wizard.sh` (function after `gexec`'s closing brace,
  line 405; profile heredoc 1313-1339; summary heredoc ≈ 2405-2427)
- Test: `sigmond/tests/test_wizard_uploads.py` (create)

**Interfaces:**
- Consumes: `smd config profile-install` (Task 7), `$CONF_MARK` (line 219).  The wizard writes
  `$CONF_MARK` at line 2375, after the profile write, so a fresh run sees no mark when
  `uploads_toml` runs.
- Produces: shell function `uploads_toml` (prints the block on a fresh host, nothing otherwise).

- [ ] **Step 1: Write the failing tests**

```python
"""A fresh install starts with the site sink switch at `off`; a configured
host never gets the block (tasks/plan-sink-control.md §10.3 item 2)."""
import subprocess
import textwrap
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"


def shell_func(name: str) -> str:
    out, keep = [], False
    for ln in WIZARD.read_text().splitlines(True):
        if ln.startswith(f"{name}(){{"):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, f"{name} not found in the wizard"
    return "".join(out)


def run_uploads_toml(conf_mark: Path) -> str:
    script = textwrap.dedent(f"""
        set -u
        CONF_MARK={conf_mark}
        {shell_func("uploads_toml")}
        uploads_toml
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=30).stdout


def test_fresh_host_writes_the_off_block(tmp_path):
    out = run_uploads_toml(tmp_path / ".configured")          # mark absent
    up = tomllib.loads(out)["uploads"]
    assert up["enabled"] is False
    assert up["mode"] == "discard"
    assert "smd sink upload" in up["reason"]


def test_configured_host_writes_no_block(tmp_path):
    mark = tmp_path / ".configured"
    mark.write_text("AC0G/B4 EM38ww 2026-10-01\n")
    assert run_uploads_toml(mark).strip() == ""


def test_the_profile_carries_the_block_and_installs_through_profile_install():
    text = WIZARD.read_text()
    assert 'UPLOADS_TOML=$(uploads_toml)' in text
    heredoc = text[text.index("PROFILE=$(cat <<PEOF"):text.index("PEOF\n)")]
    assert "$UPLOADS_TOML" in heredoc
    assert "smd config profile-install /etc/sigmond/site-profile.toml.new" in text
    # The wizard writes the new profile BESIDE the old one; a write straight
    # over site-profile.toml would reset the switch before profile-install ran.
    assert 'base64 -d > /etc/sigmond/site-profile.toml.new"' in text
    assert 'base64 -d > /etc/sigmond/site-profile.toml"' not in text


def test_the_summary_tells_a_fresh_install_how_to_set_the_switch_to_upload():
    text = WIZARD.read_text()
    summary = text[text.index("SUMMARY=$(cat <<SEOF"):text.index("SEOF\n)")]
    assert "$SINK_NOTE" in summary
    note = text[text.index('SINK_NOTE=""'):text.index("SUMMARY=$(cat <<SEOF")]
    assert "smd sink upload" in note
    # Only a fresh install (a non-empty UPLOADS_TOML) gets the note.
    for uploads, shown in (("", False), ("x", True)):
        out = subprocess.run(["bash", "-c", f"set -u\nUPLOADS_TOML={uploads!r}\n{note}"
                              'printf %s "$SINK_NOTE"'],
                             capture_output=True, text=True, timeout=30).stdout
        assert ("smd sink upload" in out) is shown, (uploads, out)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_wizard_uploads.py -v`
Expected: FAIL.  `test_fresh_host_writes_the_off_block` and `test_configured_host_writes_no_block`
fail with `AssertionError: uploads_toml not found in the wizard`; the other two fail on their first
assert (`'UPLOADS_TOML=$(uploads_toml)'` and `'$SINK_NOTE'`).

- [ ] **Step 3: Add the function** right after the closing `}` of `gexec(){` (line 405):

```bash
# uploads_toml — the [uploads] block a FRESH install writes: the site sink
# switch starts `off` (stored as the legacy discard) until the operator checks
# the station and runs `smd sink upload` (tasks/plan-sink-control.md §10.3
# item 2).  A configured host prints nothing; `smd config profile-install`
# then keeps whatever block the station already carries.
uploads_toml(){
    [ -e "$CONF_MARK" ] && return 0
    printf '\n[uploads]\nenabled = false\nmode    = "discard"\nreason  = "new station: the site sink switch stays off until someone runs smd sink upload"\n'
}
```

- [ ] **Step 4: Use it in the profile write** (lines 1313-1339)

After the `HB_TOML` block (`fi` closing it, line 1321), add:

```bash
UPLOADS_TOML=$(uploads_toml)
```

In the `PROFILE=$(cat <<PEOF` heredoc, add `$UPLOADS_TOML` on its own line right after `$HB_TOML`.
Then replace the lines that write the profile (1337-1339):

```bash
B64=$(echo "$PROFILE" | base64 -w0)
gexec 30 "echo $B64 | base64 -d > /etc/sigmond/site-profile.toml" \
    || { say "ERROR: could not write site-profile.toml in guest"; exit 1; }
```

with:

```bash
B64=$(echo "$PROFILE" | base64 -w0)
gexec 30 "echo $B64 | base64 -d > /etc/sigmond/site-profile.toml.new" \
    || { say "ERROR: could not write site-profile.toml in guest"; exit 1; }
# profile-install keeps a site sink switch the station already set; a plain
# overwrite would reset it on every --reconfigure (§10.3 item 2).  Ask first
# whether this VM's smd knows the verb, so that a refusal of the new file
# (invalid TOML, say, from a double quote in an answer) never falls through
# to installing it whole.
if gexec 30 "smd config profile-install --help >/dev/null 2>&1"; then
    gexec 30 "smd config profile-install /etc/sigmond/site-profile.toml.new" \
        || { say "ERROR: smd config profile-install refused the new site-profile.toml — see $LOG"; exit 1; }
else
    say "WARN: this VM's sigmond predates 'smd config profile-install', so the wizard"
    say "      installs the profile whole, as earlier wizards did.  An [uploads] block"
    say "      the old profile carried is gone: check 'smd upload status' and set it again."
    gexec 30 "mv -f /etc/sigmond/site-profile.toml.new /etc/sigmond/site-profile.toml" \
        || { say "ERROR: could not install site-profile.toml in guest"; exit 1; }
fi
```

(An older sigmond has no `smd sink`, so that warning names the legacy `smd upload status`.)

- [ ] **Step 5: Tell the operator** — right before `SUMMARY=$(cat <<SEOF` (≈ line 2405), add:

```bash
SINK_NOTE=""
[ -n "${UPLOADS_TOML:-}" ] && SINK_NOTE=" Site sink switch: off — the station records and decodes but sends
            no data; only its heartbeat goes out.  Check reception and
            identity, then run in the VM: smd sink upload"
```

and add `$SINK_NOTE` on its own line in the summary heredoc, right after the
` PSWS:      $PSWS_STATE` line.  On a configured host the empty `$SINK_NOTE` leaves one blank line
in the summary; that is cosmetic.

- [ ] **Step 6: Run the tests and the existing wizard suite**

Run: `.venv/bin/pytest tests/test_wizard_uploads.py tests/test_wizard_vmid.py tests/test_wizard_wifi.py tests/test_wizard_gpsdo.py tests/test_wizard_adoption_prose.py tests/test_pm_align.py -v`
Expected: all pass (dry run: 174 passed, 7 subtests).
Run: `bash -n scripts/proxmox/sigmond-wizard.sh` → no output (syntax OK).

- [ ] **Step 7: Commit**

```bash
git add scripts/proxmox/sigmond-wizard.sh tests/test_wizard_uploads.py
git commit -m "wizard: a fresh install starts with the site sink switch off

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---

### Task 9: the nested image test asserts the new start

**Files:**
- Modify: `sigmond-appliance/test-nested-v3.sh` (after the `HBLEAK` probe, line 397; after the
  heartbeat assertions, line 531; after the bring-up timeout WARN, line 824; after
  `say "bring-up completed (marker present)"`, line 826; at the end of that same `else` branch,
  after the metrology verdict's closing `fi`, line 860)

The nested test has no RX888 and makes no decodes, so it can only show the configuration.  It does
not exercise `--reconfigure`; Task 7's and Task 8's unit tests carry that.

`test-update-v3.sh:492` reuses these phases on the PREVIOUS blessed image, whose wizard writes no
`[uploads]` and whose sigmond has no `smd sink`.  The `SINKWIZ` gate below asks the host's wizard
what it does, keeps that rig green, and says loudly what it did not check.

`qm guest exec` returns JSON whose `"out-data"` carries every output line on ONE line, joined by a
literal `\n` (the file's own `phase_d_verdict` comment, lines 42-44).  `grep -c` over that reply can
never count past 1, so each probe prints one `KEY:N` token per fact and reads the tokens back.  A
dead guest agent yields no token, which the probes report as "no answer", never as a verdict.

- [ ] **Step 1: Golden-template leak probe** — after the `HBLEAK` `case ... esac` (line 397), add:

```bash
# The site sink switch belongs to the station.  The wizard writes it on a
# FRESH install only, and `smd sink status` reads coordination.toml, so
# neither file in the golden template may carry a live [uploads].
UPLEAK=$($SSHN "qm guest exec $VMID --timeout 30 -- bash -lc 'grep -qsE \"^[[:space:]]*\\[uploads\\]\" /etc/sigmond/site-profile.toml /etc/sigmond/coordination.toml && echo LEAKED || echo CLEAN'" 2>&1)
case "$UPLEAK" in
    *CLEAN*)  say "golden template carries no [uploads] ✓" ;;
    *LEAKED*) say "FATAL: a live [uploads] section leaked into the golden template"; echo "$UPLEAK" | head -6; exit 1 ;;
    *) say "FATAL: uploads leak probe returned neither CLEAN nor LEAKED"; echo "$UPLEAK" | head -6; exit 1 ;;
esac
```

- [ ] **Step 2: Post-reboot assertion** — after `say "heartbeat enabled, host and port 38222 written ✓"`
  (line 531), add:

```bash
# test-update-v3.sh runs these phases against the PREVIOUS blessed image,
# whose wizard writes no [uploads] and whose sigmond has no `smd sink`.  Ask
# the host's wizard what it does; when the answer rules the checks out, say
# so loudly rather than pass in silence.
case "$($SSHN "grep -q '^uploads_toml()' /usr/local/sbin/sigmond-setup && echo SINKWIZ:YES || echo SINKWIZ:NO" 2>&1)" in
    *SINKWIZ:YES*) SINKWIZ=1 ;;
    *SINKWIZ:NO*)  SINKWIZ=0
                   say "⚠ SITE SINK ASSERTIONS NOT EVALUATED — this image's wizard predates"
                   say "  the site sink switch (pre-v3.69); expected only on the update rig's base image" ;;
    *) say "FATAL: could not read the host's wizard to decide the site sink checks"; exit 1 ;;
esac
if [ "$SINKWIZ" = 1 ]; then
    say "verifying the site sink switch starts off on a fresh install"
    # One KEY:N token per file.  qm guest exec returns JSON whose "out-data"
    # carries every output line on ONE line, so grep -c over $SINK_OUT can never
    # count past 1; read tokens instead, as phase_d_verdict does.
    SINK_OUT=$($SSHN "qm guest exec $VMID --timeout 60 -- bash -lc 'for f in site-profile coordination; do echo \"\$f:\$(grep -A3 \"^\\[uploads\\]\" /etc/sigmond/\$f.toml 2>/dev/null | grep -cE \"^mode *= *.discard.\")\"; done; smd sink status 2>&1'" 2>&1)
    echo "$SINK_OUT" | tail -12
    _sp=$(echo "$SINK_OUT" | grep -oE 'site-profile:[0-9]+' | head -1 | cut -d: -f2)
    _co=$(echo "$SINK_OUT" | grep -oE 'coordination:[0-9]+' | head -1 | cut -d: -f2)
    [ -n "$_sp" ] && [ -n "$_co" ] \
        || { say "FATAL: the site sink probe returned no answer (guest exec failed, not a verdict)"; exit 1; }
    [ "$_sp" -ge 1 ] || { say "FATAL: site-profile.toml carries no [uploads] mode = \"discard\" after a fresh install"; exit 1; }
    [ "$_co" -ge 1 ] || { say "FATAL: coordination.toml carries no [uploads] mode = \"discard\"; smd config render did not copy it"; exit 1; }
    echo "$SINK_OUT" | grep -q 'site sink: off' \
        || { say "FATAL: smd sink status does not report site sink: off"; exit 1; }
    say "site sink switch off in both files and in smd sink status ✓"
fi
```

Do not add a "cannot discard" check here.  At line 531 bring-up may still be installing
hs-uploader, so `smd sink status` could print that error for a reason that goes away.  Step 3
checks hs-uploader's discard support after bring-up, through the manifest banner.

- [ ] **Step 3: After bring-up completes** — right after `say "bring-up completed (marker present)"`
  (line 826), inside its `else` branch, add:

```bash
    if [ "${SINKWIZ:-0}" = 1 ]; then
        # The manifest shows what hs-uploader will do.  Its banner names the mode
        # it rendered: SITE SINK OFF, or the legacy hold that an hs-uploader too
        # old to discard falls back to.  Every [[pipeline]] but the heartbeat
        # must carry discard = true.
        DISC=$($SSHN "qm guest exec $VMID --timeout 30 -- bash -lc '
            M=/etc/hs-uploader/pipelines.toml
            [ -r \$M ] || { echo NOMANIFEST; exit 0; }
            echo BANNER:\$(grep -c \"SITE SINK OFF\" \$M)
            echo PIPES:\$(grep -c \"^\\[\\[pipeline\\]\\]\" \$M)
            echo HB:\$(grep -c \"^name = .heartbeat.\" \$M)
            echo DISC:\$(grep -c \"^discard = true\" \$M)
        '" 2>&1)
        _b=$(echo "$DISC" | grep -oE 'BANNER:[0-9]+' | head -1 | cut -d: -f2)
        _p=$(echo "$DISC" | grep -oE 'PIPES:[0-9]+'  | head -1 | cut -d: -f2)
        _h=$(echo "$DISC" | grep -oE 'HB:[0-9]+'     | head -1 | cut -d: -f2)
        _d=$(echo "$DISC" | grep -oE 'DISC:[0-9]+'   | head -1 | cut -d: -f2)
        case "$DISC" in
          *NOMANIFEST*) say "FATAL: no /etc/hs-uploader/pipelines.toml after a completed bring-up"; exit 1 ;;
        esac
        [ -n "$_b" ] && [ -n "$_p" ] && [ -n "$_h" ] && [ -n "$_d" ] \
            || { say "FATAL: the manifest probe returned no answer (guest exec failed, not a verdict)"; echo "$DISC" | head -6; exit 1; }
        [ "$_b" -ge 1 ] \
            || { say "FATAL: pipelines.toml does not render SITE SINK OFF while the site sink switch reads off"
                 say "  (an hs-uploader that cannot discard makes sigmond render the legacy hold)"; exit 1; }
        _data=$(( _p - _h ))
        if [ "$_data" -eq 0 ]; then
            say "WARN: the manifest renders SITE SINK OFF but holds no data pipeline in this nest;"
            say "  the per-pipeline discard = true was NOT evaluated"
        elif [ "$_d" -eq "$_data" ]; then
            say "all ${_data} data pipeline(s) render discard = true while the site sink switch reads off ✓"
        else
            say "FATAL: ${_d} of ${_data} data pipeline(s) carry discard = true"; exit 1
        fi
    fi
```

The `BANNER` probe reads the banner text Task 6 writes into `policy_banner()`.  If a later change
rewords that banner, change this grep with it.

- [ ] **Step 3b: Say what a bring-up timeout leaves unchecked.**  When bring-up does not finish,
  Steps 3 and 4 never run, and the existing WARN (lines 822-824) names only the metrology check.
  After line 824 (`say "    show whether the station wires its timing chain."`), inside the same
  `if` branch, add:

```bash
    [ "${SINKWIZ:-0}" = 1 ] && say "  ⚠ THE SITE SINK MANIFEST AND smd sink upload CHECKS WERE NOT EVALUATED either."
```

- [ ] **Step 4: Raise the switch the way an operator will** — at the end of the same `else` branch,
  after the metrology verdict's closing `fi` (line 860) and before the `fi` that closes the branch
  (line 861), add.  This runs after the metrology checks, so raising the switch cannot disturb them.

```bash
    if [ "${SINKWIZ:-0}" = 1 ]; then
        # Show that `smd sink upload` works as root in the image and leaves no
        # pipeline discarding.  The nest has no RX888, so nothing real ships.
        say "raising the site sink switch: smd sink upload"
        UP=$($SSHN "qm guest exec $VMID --timeout 120 -- bash -lc 'smd sink upload 2>&1; echo DISCLEFT:\$(grep -c \"^discard = true\" /etc/hs-uploader/pipelines.toml); smd sink status 2>&1'" 2>&1)
        echo "$UP" | tail -12
        case "$UP" in
          *"refusing while packaging runs"*)
            say "WARN: smd sink upload refused because GRAPE or magnetometer packaging ran in the nest;"
            say "  raising the switch was NOT evaluated" ;;
          *)
            _dl=$(echo "$UP" | grep -oE 'DISCLEFT:[0-9]+' | head -1 | cut -d: -f2)
            [ -n "$_dl" ] || { say "FATAL: the smd sink upload probe returned no answer (guest exec failed, not a verdict)"; exit 1; }
            [ "$_dl" = 0 ] || { say "FATAL: ${_dl} pipeline(s) still carry discard = true after smd sink upload"; exit 1; }
            echo "$UP" | grep -q 'site sink: upload' \
                || { say "FATAL: smd sink status does not report upload after smd sink upload"; exit 1; }
            say "smd sink upload raised the site sink switch; no pipeline discards ✓" ;;
        esac
    fi
```

- [ ] **Step 5: Syntax check and commit**

Run: `cd /home/mjh/hamsci/repos/sigmond-appliance && bash -n test-nested-v3.sh && bash -n test-update-v3.sh` → no output.

```bash
git add test-nested-v3.sh
git commit -m "test-nested: a fresh install starts with the site sink switch off

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---

### Task 10: sigmond's operator docs speak the sink words

Six operator pages teach `smd upload status`, quote its old lines, or claim that `--reconfigure`
keeps every answer.  The glossary's `sink` row defines the word as the database, which spec §2.1
forbids.  Each edit below names its lines at sigmond f8c0a9b and quotes enough of the current text
to find them.  Work bottom-up within each page, so the line numbers above an edit stay right.
Where a page names the legacy modes, it maps them once: "`smd upload hold` and
`smd upload discard` still work; they set the legacy modes that `smd sink status` reports as
`hold (legacy)` and `off`."

**Files:**
- Modify: `sigmond/docs/operator/day-2.md` (L5, 343, 352-353, 355, 360, 362-367, 391-403, 642-647,
  664-666, 702-704)
- Modify: `sigmond/docs/operator/registration.md` (L5, 84-87, 111-113, 139-141, 144, 148, 153,
  362-363, 442-458, 493-497, 511)
- Modify: `sigmond/docs/operator/troubleshooting.md` (L5, 160, 165-166, 191-192, 197, 211-220,
  231-233, 237, 259-261, 328, 400, 420, 428, 432-433, 497-498, 955-956, 1161)
- Modify: `sigmond/docs/operator/glossary.md` (L5, 56 replaced, one new row after 56, 118)
- Modify: `sigmond/docs/operator/do-not-touch.md` (L5, 55, 151)
- Modify: `sigmond/docs/operator/remote-access.md` (L5, 220-222, 336)

Ten passages claim that `--reconfigure` keeps every answer or that you can press Enter through
it.  The wizard asks for the reporter ID afresh (`sigmond-wizard.sh:718-721`), pressing Enter at the
PSWS station ID skips PSWS (`:1070-1073`), and only the RAC number, station class and DASI number
carry over.  Every one of those passages changes below.

- [ ] **Step 1: `day-2.md`**

L702-704 (now `If you typed a wrong answer to the setup wizard … press Enter through everything you want
to keep.`) →

```markdown
If you typed a wrong answer to the setup wizard — reporter ID, grid square,
PSWS ids — you do not reinstall: run `sigmond-setup --reconfigure` from the
`[host]`. It asks for your reporter ID and PSWS ids afresh, so have them at
hand; pressing Enter at the PSWS station ID skips PSWS.
```

L664-666 (now `- **It is never switched off by the upload policy.** A station whose outbound …`) →

```markdown
- **The site sink switch never stops it.** A station that sends no data
  (dasi002, deliberately, and every new station until `smd sink upload`)
  still heartbeats, so it still appears on the board.
```

L642-647 (now `**Moving the station to a new location** … press Enter through everything else …`) →

```markdown
**Moving the station to a new location** — a different [grid square](glossary.md) — calls
for a documented procedure, not a reinstall: log into the `[host]`, run
`sigmond-setup --reconfigure`, and type the new grid square. The wizard asks
for your reporter ID and PSWS ids afresh, and pressing Enter at the PSWS
station ID skips PSWS, so have them at hand. It keeps your remote-access
number and the site sink switch as they were. Full steps:
[INSTALL.md §12](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#12-moving-a-station-staged-in-one-place-deployed-in-another).
```

L391-403 (now `**Except on a station whose uploads are switched off** …` through the
`registration.md §6` link) →

~~~markdown
**Except on a station whose site sink switch holds uploads back.** Then a
week with zero spots means nothing went wrong. A new station starts that way,
and a testbed with no antenna, or a station still under construction, stays
out of the public databases on purpose. Check once, and you never have to
wonder again — `[VM]`:

```bash
smd sink status
```

If it answers `⚠ site sink: off` and the reason reads `new station: …`, nobody has
raised the switch since the install: finish
[INSTALL.md §9](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#9-fifteen-minutes-later--check-its-alive)
and run `smd sink upload`. Any other `⚠ site sink: off` or `⚠ site sink: hold (legacy)`
means somebody set it on purpose: **expect zero spots and report nothing**, and do not
change it yourself. `smd upload hold` and `smd upload discard` still work; they set the
legacy modes that `smd sink status` reports as `hold (legacy)` and `off`. The full
explanation sits in the closing blockquote of
[registration.md §6 — Confirming everything flows](registration.md#6-confirming-everything-flows).
~~~

L362-367 (now `So read the readiness block as **"nothing is missing …` through the ledger link) →

```markdown
So read the readiness block as **"nothing missing would stop these paths once
the site sink switch lets them send"**, and `smd sink status` as **whether it
lets them**. A station whose site sink switch reads `off` stands fully equipped
and deliberately silent. The readiness block knows nothing about the site sink
switch, a gap that
[docs-gap ledger row 29](../contributor/docs-gap-ledger.md) records.
```

L360 →

```markdown
| `⚠ site sink: off` or `⚠ site sink: hold (legacy)` in `smd sink status` | *"May this station send data right now?"* The site sink switch answers that for the whole station. |
```

L355 → `**Both hold true, and the site sink line wins.** They answer different questions:`

L352-353 →

```markdown
while `smd sink status` on the very same station says
`⚠ site sink: off` or `⚠ site sink: hold (legacy)`.
```

L343 → `contradicts the site sink switch:`.  Leave the L340 heading alone; a changed heading changes
its anchor.

- [ ] **Step 2: `registration.md`**

L511 →

```markdown
| **Uploads pending, and the number keeps growing** | `smd sink status` first (§6); the site sink switch may hold uploads back | [troubleshooting.md → *Uploads pending and growing*](troubleshooting.md#uploads-pending-and-growing) |
```

L493-497 (now `Type the new grid square; press Enter through everything else. Your reporter …`) →

```markdown
Type the new grid square. The wizard asks for your reporter ID and PSWS ids
afresh, and pressing Enter at the PSWS station ID skips PSWS, so have them at
hand. It keeps your remote-access number, your PSWS key registration and the
site sink switch. Then check the identity in the `[VM]` with
`grep -E 'reporter_id|callsign|grid' /etc/sigmond/site-profile.toml`. If
`smd sink status` still says `site sink: off`, run `smd sink upload`. Check
wsprnet about fifteen minutes later
([INSTALL.md §12](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#12-moving-a-station-staged-in-one-place-deployed-in-another)).
```

L442-458 (the blockquote from `> **One switch that stops everything at once.**` through
`> nothing still shows up on the fleet board.`; the `>` paragraph at L460 on `unresolved identity`
stays) →

~~~markdown
> **One switch that stops every upload at once.** The site sink switch decides what
> leaves the station, and someone may have set it to send no data on purpose. Check it
> with `smd sink status` — `[VM]`:
>
> ```bash
> smd sink status
> ```
>
> | It says | Meaning |
> |---|---|
> | `✓ site sink: upload (store and send)` | normal |
> | `⚠ site sink: off — no data ships and no backlog builds` | no data leaves; only the heartbeat goes out. A new station starts here, with the reason `new station: …`, until someone checks it and runs `smd sink upload`; a bench machine being provisioned sits here too. `smd sink upload` sends nothing recorded before it, except the GRAPE and magnetometer packages for that UTC day, and for the day before if that night's packing has not finished |
> | `⚠ site sink: hold (legacy) — stores for a while and sends no data; …` | an older pause, set with `smd upload hold`. The station keeps FT8 spots for one hour and WSPR spots for 24 hours, then drops them. `smd sink upload` sends what remains; `smd sink off` drops it. DASI002 reads `no HF antenna; no PSWS station/instrument ids` |
>
> `smd upload hold` and `smd upload discard` still work; they set the legacy modes
> that `smd sink status` reports as `hold (legacy)` and `off`. Ask your fleet admin
> before changing the switch, unless the reason reads `new station: …` and you have
> just finished INSTALL.md §9. The station's 5-minute [heartbeat](glossary.md) ignores
> the switch, so a station that sends no data still shows up on the fleet board.
~~~

L362-363, inside the blockquote (now "… the one in §5a — `sigmond-setup --reconfigure` from the
`[host]`, pressing Enter through the answers that are already right. The") →

```markdown
> PSWS ids in is the one in §5a — `sigmond-setup --reconfigure` from the
> `[host]`, typing your reporter ID and the PSWS ids at their questions. The
```

L153 (now `If instead uploads are **enabled** and you still get this line, that is a real`) →
`` If instead `smd sink status` says `site sink: upload` and you still get this line, that is a real ``

L148 → `` `⚠ site sink: off` or `⚠ site sink: hold (legacy)` in the first, or a LIFECYCLE of ``

L144 → `smd sink status`

L139-141 →

```markdown
station like DASI002 that is **correct and deliberate** — no HF antenna, so the
spot recorders are not [enabled](glossary.md) and its site sink switch holds
uploads back (§6). Confirm which it is — `[VM]`:
```

L111-113 (now "**Database** tab, … should appear within about fifteen minutes of the station
being up …"; L110, "**How to confirm:** go to [wsprnet.org](…), open the", stays) →

```markdown
**Database** tab, and search for your reporter ID in the *Reporter* field. Rows
should appear about fifteen minutes after you run `smd sink upload`
([INSTALL.md §9](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#9-fifteen-minutes-later--check-its-alive)).
```

L84-87 (now `> **If you skipped the PSWS questions and now have IDs**, … re-asks everything with
your old answers pre-filled …`) →

```markdown
> **If you skipped the PSWS questions and now have IDs**, don't re-run the whole
> wizard from scratch: from the **host**, run `sigmond-setup --reconfigure`. It
> asks the questions again but pre-fills only some answers: type your reporter
> ID again, and type the PSWS IDs at their questions
> ([INSTALL.md §12](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#12-moving-a-station-staged-in-one-place-deployed-in-another)).
```

- [ ] **Step 3: `troubleshooting.md`**

L1161 → `| Anything about spots or uploads | `smd sink status`, and a minute of `smd watch uploads` |`

L955-956 (now "Press Enter through everything; only the remote-access step needs to re-run. /
Your reporter ID, grid square, RAC number and PSWS registration all stick.") →

```markdown
Only the remote-access step needs to change, but the wizard asks every question
again: type your reporter ID and PSWS ids afresh (pressing Enter at the PSWS
station ID skips PSWS) and check the grid square on its review screen. It keeps
your RAC number and the site sink switch.
```

L497-498 (now "- PSWS disabled and you now have IDs → run `sigmond-setup --reconfigure` from /
the `[host]` and press Enter through everything else") →

```markdown
- PSWS disabled and you now have IDs → run `sigmond-setup --reconfigure` from
  the `[host]` and type your reporter ID and the PSWS IDs at their questions
```

L432-433 →

```markdown
*Good:* `✓ site sink: upload`, then counters moving each cycle. *Bad:*
`⚠ site sink: hold (legacy) — … <reason>` or `⚠ site sink: off — … <reason>` (deliberate unless the reason reads `new station: …`; ask before changing), or
```

L428 → `smd sink status`

L420 → `2. **The site sink switch holds uploads back** (`hold (legacy)` on a testbed or a station with no antenna).`

L400 (now `The station keeps a queue of things to upload in its [sink](glossary.md), and`; a sink
belongs to a client, spec §2.1) → `Each client keeps what it will upload in its [sink](glossary.md), and`

L328 (now `5. **Uploads switched off** — see [No spots on wsprnet](#no-spots-on-wsprnet).`) →
`5. **The site sink switch holds uploads back** — see [No spots on wsprnet](#no-spots-on-wsprnet).`

L259-261 →

```markdown
   `enabled, running`) and `smd sink status`; if it says `site sink: off` or
   `site sink: hold (legacy)`, this station does not report FT8/FT4 on purpose and
   there is nothing to fix
```

L237 → `` `site sink: upload`, `radiod` active, signals visible on the waterfall, cycles ``

L231-233 (now "- `HOLD` or `DISCARD` → **do not turn it back on yourself.** …" through "Ask your [fleet admin](glossary.md).") →

```markdown
- `site sink: off` with the reason `new station: …` → nobody has raised the switch
  since the install. Check the station
  ([INSTALL.md §9](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#9-fifteen-minutes-later--check-its-alive)),
  then run `smd sink upload`.
- `site sink: off` or `site sink: hold (legacy)` with any other reason →
  **do not change it yourself.** Somebody set it for a reason
  ([registration.md §6](registration.md#6-confirming-everything-flows)).
  Ask your [fleet admin](glossary.md).
```

L211-220 (from `Then check the upload switch — `[VM]`:` through `— DASI002's answer, and correct for
that station.`; L217 quotes a line the code no longer prints) →

~~~markdown
Then check the site sink switch — `[VM]`:

```bash
smd sink status
```

*Good:* `✓ site sink: upload (store and send)`, b4's answer. *Silent on purpose:*
`⚠ site sink: hold (legacy) — stores for a while and sends no data; … — no HF antenna; no PSWS station/instrument ids`,
DASI002's answer and correct for that station. A new station answers
`⚠ site sink: off — no data ships and no backlog builds — new station: …` until
someone checks it and runs `smd sink upload`.
~~~

L197 → `If `smd sink status` says `site sink: upload` and you still get it, that is a real finding for your`

L191-192 →

```markdown
with the next two commands: if `smd sink status` says
`site sink: off` or `site sink: hold (legacy)`, or `smd component list` shows `wspr-recorder` and
```

L165-166 →

```markdown
3. **The site sink switch holds uploads back.** A new station starts at `off`
   until someone runs `smd sink upload`. A station with no antenna, or one still
   under construction, stays out of the public databases on purpose.
```

L160 (now "   after the station is up, and you must search the **Reporter** field with your") → two lines:

```markdown
   after the station is up and its site sink switch reads `upload`, and you
   must search the **Reporter** field with your
```

- [ ] **Step 4: `glossary.md`, `do-not-touch.md`, `remote-access.md`**

`glossary.md` L118 →

```markdown
| **`uploader-manifest`, pipeline, unresolved identity** (`smd sink status`) | See [registration.md §6](registration.md#6-confirming-everything-flows) — a *pipeline* is one product-to-destination route, and *unresolved identity* names the ids it has not been given. |
```

`glossary.md` L56: REPLACE the `**sink**` row (it now defines sink as the database) with the row
below, then insert the `**site sink switch**` row right after it, before `**smd**`.  Add no second
`sink` row.

```markdown
| **sink** | A client's store of data bound for a repository: its rows in `sink.db` and any upload spool waiting to ship. `sink.db` names only the database file, `/var/lib/sigmond/sink.db`, which every recorder writes into and the uploader reads from. |
| **site sink switch** (`smd sink`) | The station-wide setting for what leaves the site: `off` sends no data and keeps no backlog; `upload` stores and sends. A new station starts at `off`; `smd sink upload` raises it once you have checked reception and identity. A station set with the older `smd upload hold` reports `hold (legacy)`. `fill` arrives in a later release. The heartbeat ignores the switch. |
```

`do-not-touch.md` L151 → `- **`smd psws status`**, **`smd sink status`** — status verbs, all read-only.`

`do-not-touch.md` L55, last cell: replace
`` Run it now, on the `[host]`: `sigmond-setup --reconfigure`, type the new grid square, press Enter through the rest. ``
with
`` Run it now, on the `[host]`: `sigmond-setup --reconfigure`. Type the new grid square, and type your reporter ID and PSWS ids again, since the wizard asks for them afresh. ``
(the cell's closing sentence about the old-grid days stays).

`remote-access.md` L336 (now "Press Enter through everything; only the remote-access step needs to
re-run.") → the same four lines as troubleshooting.md L955-956 above.

`remote-access.md` L220-222 (now `Press Enter through every question you want to keep; your reporter
ID, grid square, RAC number and PSWS registration all stick`) →

```markdown
The wizard keeps your RAC number and the site sink switch. It asks for your
reporter ID and PSWS ids afresh, and pressing Enter at the PSWS station ID
skips PSWS, so have them at hand; check the grid square on its review screen
([INSTALL.md §12](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#12-moving-a-station-staged-in-one-place-deployed-in-another)).
```

- [ ] **Step 5: Bump each touched page's header.**  In all six pages, set line 5 to
  `> **Verified against:** sigmond <sha> on <date> — sink words checked against commands/sink.py and commands/config.py`,
  where `<sha>` comes from `git rev-parse --short HEAD` before you commit (docs-freshness accepts
  the parent of the commit that edits the page) and `<date>` is today's UTC date.

- [ ] **Step 6: Run the docs checks**

Run: `python3 scripts/docs-linkcheck.py docs README.md && .venv/bin/pytest tests/test_docs_links.py tests/test_docs_cli_table.py tests/test_docs_freshness.py tests/test_docs_conformance.py -v`
Expected: 0 broken links; tests pass.
Run: `python3 scripts/docs-freshness.py docs/operator`
Expected: none of the six pages appears in its report.
Run: `grep -n "smd upload status\|uploads: HOLD\|uploads: DISCARD\|Enter through\|registration all stick\|answers pre-filled\|Uploads switched off\|uploads are \*\*enabled\*\*\|the upload switch" docs/operator/*.md`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add docs/operator/
git commit -m "docs(operator): teach smd sink status and the sink words

Also correct the claim that --reconfigure keeps every answer: the wizard
asks for the reporter ID and PSWS ids afresh.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---

### Task 11: INSTALL.md and QUICKSTART.txt describe what v3.69 does

**Files:**
- Modify: `sigmond-appliance/INSTALL.md` (§9 lines 243-254; §11 row line 294; §12 lines 308-323;
  header lines 3-6)
- Modify: `sigmond-appliance/QUICKSTART.txt` (section 8, AFTERWARDS: a new paragraph after the PSWS
  paragraph, which ends at line 209)

Keep every `## N.` heading byte-for-byte: sigmond's operator docs link to #4, #7, #8, #9, #10, #11
and #12.  `docs-linkcheck.py` does not check anchors in github.com URLs into this repo, so a changed
heading would pass it unnoticed; Step 5 checks the headings directly.

Station pages (port 8000, station-web) show the callsign and grid and the timing and GRAPE pages.
They show no WSPR or FT8 spots, so §9 points at `smd watch wspr` and `smd watch psk` for decodes.

- [ ] **Step 1: Replace §9** (lines 243-254, from the heading through `pages for it.`; the `---`
  rule at line 256 stays):

~~~markdown
## 9. Fifteen minutes later — check it's alive

From any computer on the same network (addresses are on the console panel):

- **Live receiver:** `http://<host address>:8081` — a waterfall with signals.
- **Station pages:** `http://<host address>:8000` — the station's callsign and grid,
  and its timing and GRAPE pages.
- **Magnetometer:** `http://<host address>:8082` (if you have one)

The decoder VM sits on a private link behind the host, so the host relays these pages for it.

**No data leaves the station yet.**  A new station starts with its site sink switch at
`off`.  It records and decodes, but sends no spots or data to wsprnet, pskreporter.info,
wsprdaemon.org or PSWS.  Only its heartbeat, a five-minute health report for the fleet
board, goes out.

Log in to the decoder VM (§10) and check three things:

```
smd watch wspr      # a line per 2-minute cycle counting the WSPR spots it decoded; Ctrl-C stops it
smd watch psk       # the same for FT8 and FT4, every 15 seconds
grep -E 'reporter_id|callsign|grid' /etc/sigmond/site-profile.toml
```

When the waterfall shows signals, the watchers count spots, and the reporter ID, callsign
and grid read right, set the site sink switch to `upload`:

```
smd sink upload
smd sink status     # should say: site sink: upload
```

Nothing recorded before `smd sink upload` leaves the station, with one exception.  The
station packs each UTC day's GRAPE and magnetometer data after that day ends, between
01:00 and about 04:00 UTC.  So the packages for the UTC day you run it still go out, and
so do the previous day's if you run it before that packing has finished.  Run it after
about 04:00 UTC if you can.  While a packing job runs, `smd sink upload` refuses, names
the job, and asks you to run it again when the job ends.  About fifteen minutes after
`smd sink upload`, search your reporter ID at wsprnet.org (Database) and your callsign at
pskreporter.info.
~~~

(Task 12 Step 1 measures grape-daily's run time.  If it finishes well before 03:15 UTC, change
"about 04:00 UTC" to "about 03:15 UTC" in every place that names the end of packing:

- both hours in §9 above, "between 01:00 and about 04:00 UTC" and "Run it after about 04:00 UTC";
- Task 5's sink `nothing_ships` message, and its refusal, which says "for up to three hours";
- Task 6's banner;
- Review Focus 7;
- Task 12 Step 6(e) and (g);
- the last line of Task 13's K3LR step.)

- [ ] **Step 2: §11 row** — replace line 294, which reads today:

```markdown
| No spots after 30 minutes | Antenna actually connected? Then log in to the VM and run `smd status` — send its output to your fleet admin |
```

with:

```markdown
| No spots after 30 minutes | Expected on a new station until you run `smd sink upload` (§9). Log in to the VM and run `smd sink status`. If it says `site sink: off`, check the station as §9 describes, then run `smd sink upload`. If it says `site sink: upload`: antenna actually connected? Then run `smd status` and send its output to your fleet admin |
```

- [ ] **Step 3: Replace §12** (lines 308-323, the heading, the intro and all four steps).  The new
  step 1 happens at the staging site, so the old intro, which ends "After the station is
  physically installed at its destination:", goes too.  The wizard asks for the reporter ID afresh,
  pressing Enter at the PSWS station ID skips PSWS, and a GPSDO fix fills in the grid without
  asking (wizard 718-721, 811-815, 1070-1073).  Only the RAC number, station class and DASI
  number carry over.  station-web never re-reads the grid after its first install, so step 4
  checks `site-profile.toml`, the file `sigmond-location-check` also edits.

~~~markdown
## 12. Moving a station (staged in one place, deployed in another)

Stations are often built and tested at one site (wrong grid square!) and
then shipped to their permanent home. Keep the site sink switch at `off`
until the station knows where it stands:

1. At the staging site, leave the site sink switch at `off`; a new station
   starts that way. Check reception there with the waterfall and
   `smd watch wspr` (§9). No data leaves the station; only its heartbeat
   goes out.
2. At the destination, log into the **Proxmox host** (`ssh root@<host address>`,
   or the console before the USB controllers were handed to the VM) and run
   `sigmond-setup --reconfigure`.
3. Answer the questions again. The wizard asks for your reporter ID and PSWS
   ids afresh, and pressing Enter at the PSWS station ID skips PSWS, so have
   them at hand. Type the **new grid square**. A station whose GPSDO has a fix
   fills in the grid by itself, and may already have moved it; check the grid
   on the review screen. The wizard keeps your remote-access number and the
   site sink switch as they were.
4. Log in to the decoder VM and check the identity:
   `grep -E 'reporter_id|callsign|grid' /etc/sigmond/site-profile.toml`.
   (The station pages keep the grid from the first install.) Then run
   `smd sink upload`, and verify on wsprnet after about 15 minutes.
~~~

- [ ] **Step 4: Header.**  Line 4 reads `> **Status:** current` today, and line 5 still claims v3.65.
  In this commit set line 4 to `> **Status:** draft (v3.69 build pending)` and line 5 to
  `> **Verified against:** pending — v3.69 not yet built`.  Task 12 Step 7 sets line 4 back to
  `current` and line 5 to
  `> **Verified against:** sigmond-appliance v3.69 (<commit>, <date>) and its wizard, sigmond <commit> — <how>`.
- [ ] **Step 4b: QUICKSTART.txt.**  QUICKSTART is the guide that ships on the stick
  (`build-usb-v3.sh:178` and `:426`; `firstboot-v3.sh:1718` copies it to the host).  It never
  mentions the site sink switch, so an operator who reads only the stick never learns to run
  `smd sink upload`.  In `8. AFTERWARDS`, after the PSWS paragraph (it ends
  `register the same key on each.`) and before `This stick is appliance version`, insert this
  paragraph, with a blank line on each side:

```
NO DATA LEAVES YET: a new station starts with its site sink switch at off.
It records and decodes but sends no data; only its heartbeat goes out.
Log in to the VM, watch decode counts with  smd watch wspr  and  smd watch psk,
check reporter ID, callsign and grid, then run:  smd sink upload
(INSTALL.md section 9 has the details.)
```

- [ ] **Step 5: Check and commit**

Run: `cd /home/mjh/hamsci/repos/sigmond-appliance && python3 /home/mjh/hamsci/repos/sigmond/scripts/docs-linkcheck.py INSTALL.md` → 0 broken.
Run: `cd /home/mjh/hamsci/repos/sigmond-appliance && git diff -U0 INSTALL.md | grep '^[-+]## '` → no output (no heading changed).
Run: `cd /home/mjh/hamsci/repos/sigmond-appliance && grep -n "smd upload on\|turn the sink on" INSTALL.md QUICKSTART.txt` → no output.

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance
git add INSTALL.md QUICKSTART.txt
git commit -m "INSTALL, QUICKSTART: a new station starts with the site sink switch off

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---

### Task 12: verify, build and bless v3.69 (each external step waits for Michael's go)

Nothing here is code.  Each step reaches outside the devbox, so each one waits for Michael's
explicit go, and station steps start with a claude-bus read and post.

- [ ] **Step 1: Confirm the GRAPE fault on one station before release** (read-only; claude-bus read
  and post first).  Pick a station whose GRAPE has run for at least three UTC days since its last
  reflash.  ND qualifies only after about 04:00 UTC on 2026-10-07, because `ops/fleet.toml`
  records its reflash on 2026-10-05, and the sweep retries only days 2-7 back.  Run:
  `ops/bin/fleet-ssh <station> "journalctl -u grape-daily --since -3d --no-pager 2>&1 | awk '/dataset\\(s\\) ready/{p++} /sweep: retrying incomplete day/{r++} END{print \"journal lines\", NR, \"days packaged\", p+0, \"sweep retries\", r+0}'; systemctl show grape-daily -p ExecMainStartTimestamp -p ExecMainExitTimestamp"`
  Sweep retries above 0 confirm §10.3 item 5.  A 0 counts as evidence only when "days packaged"
  exceeds 0; a journal of one or two lines means this login (fleet-ssh logs in as `sigmond`) cannot
  read the journal.  The two timestamps give the length of the night's packaging run, which sets
  the hour INSTALL.md §9 quotes (see the note under Task 11 Step 1).  Record all of it in the
  spec's §13.
- [ ] **Step 2: Full suites green in every touched repo.**
  - sigmond: `.venv/bin/pytest -q` (3308 passed, 0 skipped, 50 subtests at f8c0a9b in the real
    tree, where ../hs-uploader/src exists; after Tasks 3-8 and 10, 3352 passed, 0 skipped.  A
    scratch copy without the hs-uploader sibling skips 2 tests in test_uploader_manifest.py and
    shows 3306 and 3350 instead).
  - hamsci-physics: `.venv/bin/pytest tests/ -q --override-ini="addopts="` (expect 344 passed, 1 skipped).
  - meteor-scatter: `.venv/bin/pytest tests/ -q` (expect 275 passed, 1 skipped).
  - sigmond-appliance: `for f in *.sh; do bash -n "$f" || echo "FAIL $f"; done`, then, one at a
    time, `./test-phase-d-verdict.sh`, `./test-unit-quoting.sh`, `./test-publish-lib.sh`,
    `./test-publish-fetchurl.sh`, `./test-publish-prune.sh`, `./test-rig-flash.sh`,
    `./test-netsel.sh`, `./test-netwatch.sh`, `./test-site-timing-metrology.sh`,
    `./test-site-timing-t4.sh`, `./test-vm-console.sh`.  Read each header first and skip any that
    asks for a VM.  `test-nested-v2.sh`, `test-nested-v3.sh`, `test-update-v3.sh`,
    `test-wifi-boot.sh` and `test-console-storm.sh` need the rig; Steps 5 and 5b run the two that
    matter here.
- [ ] **Step 3: Push** sigmond, hamsci-physics, meteor-scatter and sigmond-appliance `main`
  (Michael's go).  The golden VM clones each repo's latest `main` and fails when one sits behind.
- [ ] **Step 4: Tag and build on the rig** (root@192.168.1.182): tag `v3.69` in sigmond-appliance
  (`git tag -a v3.69`, push the tag), pull sigmond-appliance and sigmond on the rig, run
  `./sync-rig.sh`, `./build-golden-vm.sh` (≈ 16 min), `./build-usb-v3.sh --release`.
- [ ] **Step 5: Nested test** — `./test-nested-v3.sh` on the rig.  Phase D must print
  `PHASE D PASS — NESTED TEST COMPLETE`, `golden template carries no [uploads] ✓` and
  `site sink switch off in both files and in smd sink status ✓`.  It must also print either
  `all N data pipeline(s) render discard = true while the site sink switch reads off ✓` or the explicit
  WARN for a nest with no data pipeline, and either
  `smd sink upload raised the site sink switch; no pipeline discards ✓` or the explicit WARN that
  packaging ran.  It must NOT print `SITE SINK ASSERTIONS NOT EVALUATED`.
- [ ] **Step 5b: Update test** — `./test-update-v3.sh` on the rig.  It boots the last blessed image,
  rolls it forward to `main` (PHASE E), adopts the manifest (PHASE F), and rolls back (PHASE G).
  All three must print their PASS lines.  The sibling log, test-v3.log in the rig directory
  (test-update-v3.sh:120), shows `⚠ SITE SINK ASSERTIONS NOT EVALUATED`, as expected on a
  pre-v3.69 base.  After the run, from the nested PM, run exactly this (VMID 100, per
  test-update-v3.sh:163):

  ```bash
  qm guest exec 100 -- bash -lc "grep -A3 '^\[uploads\]' /etc/sigmond/coordination.toml; grep -c '^discard = true' /etc/hs-uploader/pipelines.toml"
  ```

  The `[uploads]` block it prints must show no `mode = "discard"` and no `enabled = false`, and
  the count must read 0.  Keep the outer double quotes.  With outer single quotes, the grep's own
  quotes close them early.  The PM shell then strips the backslashes, the guest greps for the
  character class `^[uploads]`, and the empty output looks like a pass.  Neither the forward roll
  nor the rollback touched the switch, so an updated station keeps shipping.  (PHASE G restores
  the blessed sigmond, so `smd sink` no longer exists in the guest when the run ends; check the
  files, not the command.)
- [ ] **Step 6: Hardware test on a station Michael picks** (claude-bus first; for B4, also
  `/etc/sigmond/CLAUDE-COORDINATION.md`, with a HYPHEN).  Flash and run the wizard.  Then, in order:
  - (a) `smd sink status` prints `site sink: off — … new station: …`.
  - (b) `smd watch wspr` and `smd watch psk` show decode counts that rise each cycle.
  - (c) On wd30 (`ssh wd30`, then `clickhouse-client -q "<query>"`):
    `SELECT max(time) FROM wsprdaemon.spots` and `SELECT max(time) FROM wspr.rx` both sit within
    15 minutes of now; otherwise stop, because a zero from a frozen table proves nothing.  Then
    `SELECT count() FROM wsprdaemon.spots WHERE rx_sign = '<reporter id>' AND time > now() - INTERVAL 30 MINUTE`
    and the same query against `wspr.rx` both return 0.  `rx_sign` holds the reporter ID, e.g.
    `AC0G/B4`, not the bare callsign; `smd admin instance list` shows it.  pskreporter.info shows no
    spots with the station's callsign as receiver.
  - (d) Before the night: `systemctl is-active hs-uploader` prints `active`, and
    `systemctl list-timers grape-daily.timer mag-recorder-upload.timer --no-pager` shows both
    timers with a next run.  Note `systemctl show hs-uploader -p ActiveEnterTimestamp -p NRestarts`.
    While hs-uploader runs, `mag-recorder-upload.service` only packages; whenever it does not,
    that unit ships zips itself and bypasses the site sink switch (spec §2; a route v3.69 leaves
    open).  The next morning the same `systemctl show` must print the same values; if they moved,
    read `/var/log/mag-recorder/upload.log` for a direct upload during the outage and record it.
  - (e) Leave the station at `off` through one night, past 04:00 UTC, so GRAPE and the magnetometer
    pack a day while off.  Note the UTC time T and run `smd sink upload`.  Expect `set aside …`
    lines and `marked 7 earlier GRAPE days packaged`.
    `ls /var/lib/timestd/upload-held/*/ /var/lib/mag-recorder/upload-held/*/` holds yesterday's
    OBS directory, and its zip when the station has a magnetometer.
    `ls -ln /var/lib/timestd/upload/*/.upload_complete` shows the spool owner's uid (timestd).
    If the command refuses because packaging still runs, that refusal itself checks Review
    Focus 7: confirm it names the unit, then run the command again after the unit finishes.
  - (f) Within 15 minutes, wd30 and pskreporter.info show spots.
    `SELECT min(time) FROM wsprdaemon.spots WHERE rx_sign = '<reporter id>' AND time > now() - INTERVAL 2 DAY`
    returns no time earlier than T minus 2 minutes; that allows one WSPR cycle already in flight
    when the command ran.  Run the same check on `wspr.rx`.
  - (g) After the next 04:00 UTC, PSWS holds the day of T and nothing older.
- [ ] **Step 7: Finish INSTALL.md** (Task 11 Step 4): set `Status` back to `current` and
  `Verified against` to the image commit and the hardware result; commit, push (Michael's go).
- [ ] **Step 8: Bless** — `./bless-release.sh v3.69`, read its gates, then `--apply` (Michael's go).

### Task 13 (optional, Michael's call): bring ND and K3LR to one sender

This touches stations, so it follows the same rules as Task 12.

- [ ] **Before either edit:** claude-bus read and post, and Michael's go.
- [ ] **The measurement.**  The 2026-10-06 read (spec §10.2 step 1) compared the FT8/FT4 rows a
  pskreporter sender may post with the records hs-uploader acked over a 40-minute window inside
  psk's ~60-minute retention.  On ND it found 12,124 eligible and 12,270 acked (+1.2 %), in two
  interleaved cadences: about 144 rows every 30 s from the daemon, about 8 from psk-recorder's
  in-process sender.  Save this as `acked-vs-eligible.sh` in your scratchpad:

```bash
# acked-vs-eligible.sh -- the 2026-10-06 read, for the 40 minutes that ended
# 10 minutes ago.  Acked attempts land one pump (30 s) after their rows.
E1=$(( $(date -u +%s) / 60 * 60 - 600 )); E0=$(( E1 - 2400 ))
W0=$(date -u -d @$E0 +%Y-%m-%dT%H:%M:%S); W1=$(date -u -d @$E1 +%Y-%m-%dT%H:%M:%S)
A0=$(date -u -d @$((E0 + 30)) +%Y-%m-%dT%H:%M:%S); A1=$(date -u -d @$((E1 + 30)) +%Y-%m-%dT%H:%M:%S)
DB=/var/lib/sigmond/sink.db; WM=/var/lib/hs-uploader/watermarks.db
echo "host $(hostname)  rows $W0..$W1  acks $A0..$A1"
sudo -n sqlite3 -readonly $DB "SELECT 'eligible', count(*) FROM pending_uploads WHERE target_db='psk' AND target_table='spots' AND id > (SELECT max(id) FROM pending_uploads) - 25000 AND queued_at >= '$W0' AND queued_at < '$W1' AND json_extract(payload_json,'\$.tx_call') != '' AND json_extract(payload_json,'\$.mode') IN ('ft8','ft4') AND json_extract(payload_json,'\$.forward_to_pskreporter') = 0"
sudo -n sqlite3 -readonly $WM "SELECT 'acked', count(*), sum(records) FROM attempts WHERE table_name='psk.spots' AND outcome='acked' AND ts >= '$A0' AND ts < '$A1'"
sudo -n sqlite3 -readonly $WM "SELECT 'big(>=50)', count(*), sum(records) FROM attempts WHERE table_name='psk.spots' AND outcome='acked' AND records>=50 AND ts >= '$A0' AND ts < '$A1'"
sudo -n sqlite3 -readonly $WM "SELECT 'small(<50)', count(*), sum(records) FROM attempts WHERE table_name='psk.spots' AND outcome='acked' AND records<50 AND ts >= '$A0' AND ts < '$A1'"
```

  Run it with `ops/bin/fleet-ssh ac0g-nd "$(cat acked-vs-eligible.sh)"`.  If 25,000 rows no
  longer reach back 50 minutes, widen that bound.
- [ ] **ND.**  Read first:
  `ops/bin/fleet-ssh ac0g-nd "ls /etc/psk-recorder/env/; systemctl list-units 'psk-recorder@*' --no-legend --plain; grep -H PSK_DELIVERY_PIPELINES /etc/psk-recorder/env/*.env"`.
  Nobody has read the instance list since the 2026-10-05 reflash.  Run the measurement.  Then use
  the verb bring-up uses, which writes the same key through `env_path_for`:
  `ops/bin/fleet-ssh ac0g-nd "smd config upload psk-recorder AC0G/ND --on --via server-raw && sudo systemctl restart 'psk-recorder@AC0G\x3dND.service'"`.
  (`storage_instance('AC0G/ND')` gives `AC0G=ND`, and `systemd-escape 'AC0G=ND'` gives
  `AC0G\x3dND`; the escape must reach systemctl inside single quotes.)  An hour later, run the
  measurement again.  Expect one cadence, the large batches, and acked records within a pump's
  worth of eligible: the ≈1 % duplicates go, and nothing else changes.
- [ ] **K3LR.**  The same read, with `k3lr` and `/etc/psk-recorder/env/K3LR.env`; then
  `ops/bin/fleet-ssh k3lr "smd config upload psk-recorder K3LR --on --via server-raw && sudo systemctl restart psk-recorder@K3LR.service"`.
  K3LR has sat on the legacy hold since 2026-10-01 21:26Z because its antenna is not connected;
  nobody chose that hold as policy.  Its manifest renders the heartbeat alone, so the measurement
  shows no acks; skip it there.  The `--via server-raw` edit itself works on today's sigmond.

  Leave K3LR's hold in place.  Pick a backlog option only after its antenna is connected and
  Michael says so.  A backlog recorded with no antenna is junk, so dropping it is the likely
  choice.  The backlog options use v3.69's `smd sink`, so run them only after `smd align` brings
  K3LR to the blessed v3.69.  On the older sigmond, `smd upload on` closes no doors.  Before
  choosing what the stored backlog does, list it:
  `ls /var/lib/timestd/upload/ /var/lib/mag-recorder/upload/`.  `smd sink upload` straight from
  the hold closes no doors either (the doors close only when the written mode leaves discard), and
  the hold leaves file spools alone.  Two options:
  - `smd sink upload` alone sends about 24 hours of WSPR, the last hour of FT8, and every GRAPE
    and magnetometer package listed.
  - To drop it: `smd sink off --reason "drop the K3LR hold backlog" --yes`, then wait until
    `smd watch uploads` shows the queues drained (several pump cycles; DiscardTransport acks per
    pump, so an immediate `upload` would still ship the backlog), then `smd sink upload`.  That
    run sets every listed GRAPE and magnetometer package aside under `*/upload-held/<UTC time>/`,
    where it stays recoverable.  Run it after about 04:00 UTC, or it refuses while packaging runs.
