# smd align — Plan 2b: `--apply` makes the moved code live

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** after `smd align --apply` moves the checkouts, it also rebuilds radiod when ka9q-radio moved, refreshes the image-carried files, re-runs what bring-up runs, restarts exactly the services the move made stale (radiod first when it restarts), checks at once that they came back, and offers `smd align --verify` for the hourly verdicts.

**Architecture:** a new pure module `lib/sigmond/align_live.py` decides *what* is stale and in what order it restarts. It takes plain dicts of timestamps and names and imports no `subprocess`. Every station reader and actor lives in `bin/smd` helpers named `_align_*`, outside `cmd_align`'s own body. Those readers are git reflog, `systemctl show`, file mtimes and the heartbeat readers; the actors are `systemctl restart`, the radiod build, the file refresh and the bring-up commands. File writes that need no station context (image-file refresh, the `live` record) join `lib/sigmond/align_apply.py` with an injected `urlopen`/`now`, as 2a's helpers take an injected `run`.

**Tech Stack:** Python 3.11+ standard library; systemd (`systemctl show --timestamp=unix`, systemd ≥ 251, Debian 13 ships 257); git reflog; the existing `lifecycle.resolve_units` / `order_units`, `_build_ka9q_radio`, `_editable_siblings`, `heartbeat` readers.

**Spec:** `docs/superpowers/specs/2026-09-24-smd-align-design.md`. §3 steps 5–8, the radiod paragraph, and "Decisions for Plan 2b" all bind.

## How the tasks specify tests

- **Tasks 2 and 4** give complete code for their pure functions and complete test code.
- **The other tasks** state each test as exact assertions (inputs, calls, expected results). The implementer writes each test out in full and watches it fail before the code exists.
- **Mutation:** every task names at least one mutation that its tests must catch.

## Global Constraints

- `lib/sigmond/align.py` never imports `subprocess`, and the new `lib/sigmond/align_live.py` never imports it either. Add the same guard test for `align_live.py`.
- `cmd_align`'s own body carries none of "checkout", "'fetch'", '"fetch"', "install.sh", "systemctl", "_run_git". The existing guard test `test_dry_run_cannot_move_anything` must pass unchanged, so every new actor lives in an `_align_*` helper.
- The dry run changes nothing and takes no root and no lock. Reading `systemctl show`, git reflog and file mtimes is allowed there.
- `--apply` keeps 2a's order and adds four stages after the moves: moves → image-file refresh → bring-up re-runs → records → restarts → fast checks → live record. If anything before the restarts fails, nothing restarts.
- A service that is not running is never started by `align`, because starting services is not alignment. Only running units that are stale get restarted.
- A restart runs in-process: `systemctl reset-failed <unit>`, then `systemctl restart <unit>`. It never shells out to `smd restart`, since the held `lifecycle_lock` would make that child exit ("another lifecycle operation is in progress").
- When radiod restarts, every running radiod consumer restarts after it, once `/usr/local/sbin/sigmond-radiod-ready 90` returns 0. A recorder left running across a radiod restart reports healthy and produces nothing.
- install.sh stays root. Afterwards the ownership repair covers `<checkout>/venv` (top level only) as well as `*.egg-info`. It never follows a symlink and never reaches outside `base`.
- Stdlib only. Commits go on main, and each message ends with the session's attribution lines. Nothing gets pushed by this plan.

## File map

| File | Change |
|---|---|
| `lib/sigmond/align_live.py` | NEW, pure: `stale_components`, `predicted_restarts`, `restart_sequence`, `RADIOD_GAP_ESTIMATE_S` |
| `lib/sigmond/align_apply.py` | `_refresh_pin` exclude fix; moved-detail SHA width; `Ctx.build` hook + `BUILT_COMPONENTS`; drop `_RADIOD_REFUSAL`; `refresh_image_file`; `record_live` |
| `bin/smd` | dry-run `left` wording/exit; install-line indent; venv ownership repair; readers `_align_head_moved_at`, `_align_units_started_at`, `_align_consumes`, `_align_services`, `_align_radiod_built_at`; actors `_align_build`, `_align_refresh_image_files`, `_align_bringup`, `_align_restart`, `_align_fast_checks`; `--no-restart`; `smd align --verify` |
| `tests/test_align_live.py` | NEW |
| `tests/test_align_apply.py`, `tests/test_align_cli.py` | extended |
| `docs/superpowers/specs/2026-09-24-smd-align-design.md` | already amended ("Decisions for Plan 2b") |

---

### Task 1: the B4 findings

**Files:** Modify `lib/sigmond/align_apply.py` (`_refresh_pin` ~403-422, `_move` step 7 ~398), `bin/smd` (`_align_repair_ownership` ~4666-4707, `cmd_align` dry-run loop ~5009-5073, the "running install.sh" line). Test: `tests/test_align_apply.py`, `tests/test_align_cli.py`.

**Interfaces — produces:** `align_apply.ensure_pin_excluded(repo) -> bool`. It returns True when it had to add `.pin` to `.git/info/exclude`.

Four fixes, each with its own failing test first:

1. **`_refresh_pin` leaves the exclude unwritten when `.pin` already matches.** B4's ft8_lib `.pin` shows as untracked for this reason.
   - Split the exclude half out of `write_pin` into `ensure_pin_excluded(repo)`, and have `write_pin` call it.
   - In `_refresh_pin`, on the matching-pin early return, call `ensure_pin_excluded`. When it returns True, call `_chown_pin` and return `Step(name, "current", "pin excluded from git")`.
   - Tests:
     - a tmp repo with `.git/info/exclude` lacking `.pin` and a matching `.pin` → the exclude afterwards contains `.pin`, and the Step detail is "pin excluded from git";
     - a second call → detail "" (already excluded).
   - Mutation: skip the call; the first test must FAIL.
2. **The two SHAs in a `moved` detail are shortened to different widths** (`58459c0 -> 851ff473`). Use `[:8]` for both; `Item.live` can be a 7-char short SHA, so resolve it first. `_move` already holds `from_full`, so use `from_full[:8]`.
   - Test: live given as 7 chars → detail `"<8> -> <8>"`.
3. **The in-checkout venv is left root-owned.** `_align_repair_ownership` now also repairs `<checkout>/venv` when that is a real directory (not a symlink) holding `pyvenv.cfg`: every path under it whose uid/gid differs from the checkout directory's. It never descends into symlinked directories and never repairs `.venv` or any deeper venv.
   - Tests (tmp tree, `os.chown` and `os.lstat` patched to record; the existing `_align_repair_ownership` tests show the pattern):
     - `repo/venv/pyvenv.cfg` + `repo/venv/bin/python` with a foreign uid → both chowned to the repo dir's uid/gid;
     - `repo/venv` as a symlink → nothing under it touched;
     - `repo/sub/venv` → untouched.
   - Mutation: drop the venv branch; test 1 must FAIL.
4. **The dry run shows an ahead component as `move … would roll back` and exits 1.**
   - When `commit_distance` reports target behind live (`behind > 0 and ahead == 0` in the existing code's naming), print `left — ahead by N; --apply leaves it (--allow-rollback moves it back)`.
   - Count it under a new `ahead` key, so `summary:` reads `… to move, N left ahead, …`, and leave it out of the exit-code `pending`.
   - A distance that cannot be measured (rate limit, no slug) stays a `move` and still counts as pending. Guessing "ahead" would hide a real move.
   - Also indent `running install.sh …` to match the step list (two spaces), in both `_move`'s `say` call and `_align_install_missing`.
   - Tests: a dry run with only an ahead sigmond and everything else current → rc 0, and the output contains "left — ahead by"; the same run with the distance unknown → rc 1.
   - Mutation: count ahead as pending; the rc-0 test must FAIL.

- [ ] Steps: failing tests, then implement, then pass. Do each mutation, then run the full suite (2676 at plan time). Commit: `align: B4 findings — pin exclude on current, venv ownership repair, dry run leaves ahead`.

---

### Task 2: what is stale (pure — `lib/sigmond/align_live.py`)

**Interfaces — produces** (consumed by Tasks 3, 5 and 7): `stale_components`, `predicted_restarts`, `RADIOD`, `RADIOD_GAP_ESTIMATE_S`, exactly as below.

- [ ] **Step 1: write `tests/test_align_live.py`:**

```python
import inspect
import unittest

from sigmond import align_live as L


class StaleTests(unittest.TestCase):
    def test_own_checkout_moved_after_start_is_stale(self):
        out = L.stale_components({"codar-sounder"}, {"codar-sounder": 200.0},
                                 {"codar-sounder": 100.0}, {})
        self.assertEqual(out, {"codar-sounder": "its checkout moved after it started"})

    def test_moved_before_start_is_not_stale(self):
        self.assertEqual(L.stale_components({"a"}, {"a": 100.0}, {"a": 200.0}, {}), {})

    def test_consumed_library_moved_after_start_is_stale(self):
        out = L.stale_components({"psk-recorder"}, {"ka9q-python": 300.0, "psk-recorder": 50.0},
                                 {"psk-recorder": 100.0}, {"psk-recorder": {"ka9q-python"}})
        self.assertEqual(out, {"psk-recorder": "ka9q-python moved after it started"})

    def test_both_reasons_are_named(self):
        out = L.stale_components({"x"}, {"x": 300.0, "lib": 300.0}, {"x": 100.0}, {"x": {"lib"}})
        self.assertEqual(out, {"x": "its checkout moved; lib moved after it started"})

    def test_not_running_is_never_stale(self):
        self.assertEqual(L.stale_components({"a"}, {"a": 300.0}, {"a": None}, {}), {})

    def test_unknown_move_time_is_not_stale(self):
        self.assertEqual(L.stale_components({"a"}, {}, {"a": 100.0}, {}), {})

    def test_only_services_are_considered(self):
        self.assertEqual(L.stale_components(set(), {"ka9q-python": 300.0},
                                            {"ka9q-python": 100.0}, {}), {})


class PredictedTests(unittest.TestCase):
    def test_moving_component_and_consumers_of_moving_library(self):
        got = L.predicted_restarts(
            moving={"codar-sounder", "ka9q-python"},
            running={"codar-sounder", "psk-recorder", "hf-timestd", "radiod"},
            consumes={"psk-recorder": {"ka9q-python"}, "hf-timestd": {"hamsci-dsp"}},
            radiod_consumers={"psk-recorder", "hf-timestd", "codar-sounder"})
        self.assertEqual(got, (False, ["codar-sounder", "psk-recorder"]))

    def test_ka9q_radio_moving_restarts_radiod_and_every_running_consumer(self):
        got = L.predicted_restarts(
            moving={L.RADIOD}, running={"radiod", "psk-recorder", "hf-timestd"},
            consumes={}, radiod_consumers={"psk-recorder", "hf-timestd", "wspr-recorder"})
        self.assertEqual(got, (True, ["hf-timestd", "psk-recorder"]))


class GuardTests(unittest.TestCase):
    def test_align_live_never_imports_subprocess(self):
        self.assertNotIn("subprocess", inspect.getsource(L))
```

- [ ] **Step 2:** run `.venv/bin/python -m pytest tests/test_align_live.py --override-ini addopts=`. It must FAIL: module not found.
- [ ] **Step 3: write `lib/sigmond/align_live.py`:**

```python
"""What an alignment made stale, and the order to restart it — pure.

A running unit is stale when it started before the code it runs moved:
its own checkout's HEAD, or an editable sibling it imports (ka9q-python,
hamsci-dsp, …). radiod runs a BUILT binary, so its "moved" time is the
binary's mtime, not its checkout's. Every time comes from the station
itself (reflog, systemd, mtime), so a later run finds restarts an earlier
run left undone. No I/O here; bin/smd reads the station and calls these.
"""
from typing import Iterable, Mapping, Optional

RADIOD = "ka9q-radio"          # the checkout; its service component is radiod
RADIOD_GAP_ESTIMATE_S = 60     # restart + sigmond-radiod-ready, as printed in the dry run


def stale_components(services: Iterable[str], moved_at: Mapping[str, Optional[float]],
                     started_at: Mapping[str, Optional[float]],
                     consumes: Mapping[str, Iterable[str]]) -> dict:
    """{component: reason} for every running service whose code moved after it started.

    ``started_at[c]`` is the EARLIEST start among c's active units (None when
    none is active); ``moved_at[x]`` is when x's code last moved (None unknown).
    """
    out = {}
    for name in sorted(services):
        start = started_at.get(name)
        if start is None:
            continue
        reasons = []
        own = moved_at.get(name)
        if own is not None and own > start:
            reasons.append("its checkout moved")
        for lib in sorted(consumes.get(name, ())):
            t = moved_at.get(lib)
            if t is not None and t > start:
                reasons.append(f"{lib} moved")
        if reasons:
            out[name] = "; ".join(reasons) + " after it started"
    return out


def predicted_restarts(*, moving: Iterable[str], running: Iterable[str],
                       consumes: Mapping[str, Iterable[str]],
                       radiod_consumers: Iterable[str]) -> tuple:
    """(radiod_restarts, [components]) an --apply of ``moving`` would restart — for the dry run.

    radiod restarts when ka9q-radio moves, and then every running radiod
    consumer follows it. Otherwise: each running component that moves, and
    each running component that imports a moving library. radiod itself is
    never in the list; the bool carries it.
    """
    moving, running = set(moving), set(running)
    radiod = RADIOD in moving and "radiod" in running
    names = set()
    if radiod:
        names |= set(radiod_consumers) & running
    for c in running - {"radiod"}:
        if c in moving or set(consumes.get(c, ())) & moving:
            names.add(c)
    return radiod, sorted(names)
```

- [ ] **Step 4:** PASS.
- [ ] **Step 5: mutations**, one at a time, then restore:
  - `>` → `>=` in the own-checkout test: `test_moved_before_start_is_not_stale` still passes. So add `test_moved_at_same_second_is_not_stale` with `{"a": 100.0}, {"a": 100.0}` → `{}`, then re-run the mutation and watch it FAIL.
  - Drop the `radiod_consumers` union: the radiod test must FAIL.
- [ ] **Step 6:** commit `align_live: what an alignment made stale, and what the dry run predicts`.

---

### Task 3: reading the station (bin/smd readers)

**Files:** `bin/smd` (new helpers beside `_align_live_state`). Test: `tests/test_align_cli.py`.

**Interfaces — produces:**
- `_align_head_moved_at(repo, run=subprocess.run) -> float | None`
  - argv: `git --no-optional-locks -c safe.directory=<repo> -C <repo> log -g -1 --format=%ct HEAD`;
  - returns a float of stdout; None when the call fails or prints nothing.
  - The reflog's newest entry is the last HEAD move: a checkout, a pull or a clone.
- `_align_units_started_at(units, run=subprocess.run) -> float | None`
  - per unit: `systemctl show --timestamp=unix -p ActiveState -p ActiveEnterTimestamp <unit>`;
  - parse `ActiveEnterTimestamp=@<int>`, and count only units with `ActiveState=active`;
  - returns the MIN over active units, or None when none is active.
- `_align_consumes(base, names) -> dict[str, set[str]]`
  - for each name, `_editable_siblings(base/name)` yields package→path;
  - map each path to its checkout directory name, keeping only names that are directories under `base`.
- `_align_services(topology_path=None) -> dict[str, list[str]]`
  - `{component: [unit names]}` for enabled components, via `lifecycle.resolve_units(enabled, enabled)`, dropping `orphaned` units;
  - libraries have no units and are therefore absent;
  - a component `resolve_units` rejects is skipped with a printed note, never raised.
- `_align_radiod_built_at() -> float | None`: the mtime of the installed radiod binary. Read `_build_ka9q_radio`'s install step for the exact path (`make install` prefix `/usr/local`, so `/usr/local/sbin/radiod`; verify it, and if the build installs elsewhere use that path). None when absent.

Tests, each against a fake `run` recording argv:
- reflog → `"1790270000\n"` → 1790270000.0; returncode 128 → None; the argv contains `-g` and `--no-optional-locks`.
- two units, `active @100` and `active @50` → 50.0; one `inactive` and one `active @70` → 70.0; all inactive → None; a malformed timestamp line → that unit is ignored.
- `_align_consumes` with `_editable_siblings` patched: `{'ka9q-python': base/'ka9q-python'}` → `{'psk-recorder': {'ka9q-python'}}`; a path outside base → dropped.
- `_align_services` with `resolve_units` patched to return two UnitRefs, one orphaned → only the non-orphaned unit is kept.

Mutation: take MAX instead of MIN in `_align_units_started_at`; its first test must FAIL.

- [ ] Steps as usual. Commit: `align: read unit start times, reflog move times and library consumers`.

---

### Task 4: restart in the right order (pure sequence + bin/smd actor)

**Interfaces — produces:**
- `align_live.restart_sequence(stale: dict, radiod_stale: bool, running: set, radiod_consumers: set) -> tuple[bool, list[str]]`. It returns the same shape as `predicted_restarts`: when radiod is stale, every running radiod consumer joins the list, whether or not it is stale on its own.
- `_align_restart(radiod_first: bool, components: list[str], services: dict, *, run=subprocess.run, say=print, sleep=time.sleep) -> list[Step]` in bin/smd.

Complete code for the pure half (add to `align_live.py`, with tests in `test_align_live.py`):

```python
def restart_sequence(stale: Mapping[str, str], radiod_stale: bool, running: Iterable[str],
                     radiod_consumers: Iterable[str]) -> tuple:
    """(radiod_first, [components]) to restart now. radiod first when it is stale,
    then every running radiod consumer; otherwise just the stale ones."""
    running = set(running)
    names = set(stale) & running
    if radiod_stale and "radiod" in running:
        names |= set(radiod_consumers) & running
    names.discard("radiod")
    return bool(radiod_stale and "radiod" in running), sorted(names)
```

Tests (full): stale `{"a": "..."}`, not radiod → `(False, ["a"])`; radiod stale with consumers `{"a","b"}`, running `{"radiod","a","b","c"}` → `(True, ["a","b"])`; radiod stale but not running → `(False, [...stale only])`.

`_align_restart` behaviour:
1. **radiod first.** If `radiod_first`, for each unit in `services['radiod']`: `systemctl reset-failed <u>`, then `systemctl restart <u>`. Then run `/usr/local/sbin/sigmond-radiod-ready 90`.
   - A non-zero restart or readiness result → `Step('radiod', 'failed', ...)`, and every listed component becomes `skipped` "radiod did not come back". Return.
   - On success → `Step('radiod', 'restarted', 'ready')`.
2. **Then the components.** `lifecycle.order_units` over the listed components' units (build the UnitRefs from `_align_services`' resolution, or re-resolve; do not re-add orphans). For each unit, reset-failed then restart.
   - A component whose unit fails to restart → `Step(c, 'failed', 'systemctl restart <u>: <last stderr line>')`. Keep restarting the rest: one failed restart must not leave the others stale.
   - Otherwise → `Step(c, 'restarted')`.
3. `say` one line per unit before restarting it: `  restarting <unit> …`.

Tests: the fake `run` records argv.
- radiod_first → radiod's restart argv comes before the ready argv, which comes before any consumer's restart argv.
- the ready call returns 1 → consumers `skipped`, with no consumer restart argv.
- the second of two components fails → the first `restarted`, the second `failed`, and the third is still restarted.
- the argv never contains `smd` (no child smd).

Mutations: restart consumers before the readiness wait → the ordering test FAILS; stop after the first failure → the third-component test FAILS.

- [ ] Commit: `align: restart what is stale — radiod first, readiness, then consumers`.

---

### Task 5: radiod rebuilds when ka9q-radio moves

**Files:** `lib/sigmond/align_apply.py` (`apply_plan`, `_move`, `Ctx`), `bin/smd` (`_align_build`, dry run). Tests: both test files.

**Interfaces — produces:**
- `align_apply.BUILT_COMPONENTS = {"ka9q-radio"}`
- `Ctx.build: Optional[Callable[[str, Path], int]] = None`, the last field.

Behaviour:
- **`apply_plan`:** stop refusing a forward or ahead ka9q-radio. Remove `_RADIOD_REFUSAL`'s use for those statuses, but keep the missing-ka9q-radio refusal (`smd install ka9q-radio`).
- **`_move`:** for a component in `BUILT_COMPONENTS`, after the checkout and `.pin`, call `ctx.build(name, repo)` instead of the install-trigger check.
  - When `ctx.build` is None → treat it as a failure, "no builder wired", and roll back.
  - A non-zero result → roll back exactly as an install failure (2a's C1 path), detail `build exit N — rolled back to <from8>`.
- **`_align_build(name, repo) -> int`** in bin/smd: `0 if _build_ka9q_radio(repo, force=True) else 1`, printing `  building ka9q-radio (radiod) …` first. Never restart radiod here; Task 7's restart stage does that, from staleness.
- **Dry run:** when ka9q-radio would move, its line reads `… move … — REBUILDS and RESTARTS radiod — recording gap of about {RADIOD_GAP_ESTIMATE_S} s`, replacing the 2a note "2a will not move it".

Tests:
- a ka9q-radio forward with a fake build returning 0 → `moved`, and the build is called with `(name, repo)`;
- the build returns 2 → `failed` "build exit 2 — rolled back", with the rollback checkout argv present;
- `build=None` → failed with a rollback;
- a missing ka9q-radio → still `refused`;
- dry run: a moving ka9q-radio line contains "REBUILDS and RESTARTS radiod".

Mutation: skip the rollback on build failure → FAIL.

- [ ] Commit: `align: rebuild radiod when ka9q-radio moves (rolled back on a failed build)`.

---

### Task 6: image-carried files and bring-up re-runs

**Interfaces — produces:**
- `align_apply.refresh_image_file(tag: str, name: str, dest: Path, *, urlopen=urllib.request.urlopen, timeout=20.0) -> Step`
  - fetches `f"{align.RAW_BASE}/{tag}/{name}"`;
  - refuses an empty body or a non-200 → `Step(str(dest), 'failed', …)`;
  - writes a tmp file in `dest.parent`, `chmod 0o755`, then `os.replace`, and chowns root:root (`os.chown(tmp, 0, 0)`);
  - result → `Step(str(dest), 'refreshed')`.
- `_align_refresh_image_files(tag) -> list[Step]` in bin/smd: for each `align.image_file_drift(tag)` entry with status `differs` or `absent`, call `refresh_image_file`. An `unknown` status → `Step(path, 'refused', 'could not compare — not refreshed')`.
- `_align_bringup(run=subprocess.run, say=print) -> list[Step]` in bin/smd runs, in order, stopping at the first failure:
  1. `/usr/local/sbin/sigmond-site-timing`, if it is executable. It is idempotent and skips wiring already done; it may restart chrony itself.
  2. `[sys.executable, <this smd>, 'config', 'render']`. `config` is not a lifecycle-lock verb, so a child smd is safe here. Verify that in `main()`'s `_MUTATING` set and say so in a comment.
  3. `[sys.executable, <this smd>, 'admin', 'uploader', 'manifest', '--write']`, with no `--enable`, so it never restarts the uploader itself. Compare `/etc/hs-uploader/pipelines.toml` bytes before and after. When they changed, the returned Step detail says `manifest changed`, and Task 7 adds `hs-uploader` to the restart list when it is running.
  4. `[sys.executable, <this smd>, 'doctor', '--fix']`. Its exit code counts only when the run crashed (returncode ≥ 2). Findings are not failures here.
  - Each step → `Step('<command name>', 'ran' | 'failed', <last output line>)`.
  - Every child gets `stdin=subprocess.DEVNULL` and a timeout: 300 s for site-timing, 120 s for the others. A timeout becomes a failed Step.

Tests:
- `refresh_image_file` with a fake urlopen (200, body) in a tmp dir → the file is written with mode 0o755 and the tmp file is gone; with a 404 → failed, and the dest is untouched.
- `_align_bringup` with a fake run:
  - the order is site-timing, render, manifest, doctor;
  - site-timing returning 1 → render never called;
  - doctor returning 1 (findings) → still `ran`;
  - pipelines.toml bytes changed across the manifest step → detail "manifest changed".

Mutations: `chmod 0o644` → FAIL; continue after a failure → FAIL.

- [ ] Commit: `align: refresh image-carried files and re-run bring-up steps`.

---

### Task 7: wiring — the stages, `--no-restart`, fast checks, the live record, `--verify`

**Interfaces — produces:**
- `align_apply.record_live(aligned_path: Path, live: dict) -> None`: merges `{"live": live}` into the existing aligned.json atomically. When aligned.json is absent, it writes nothing.
- `_align_fast_checks(restarted_units, *, run, sleep, now, hf_timestd_running) -> dict`:
  - snapshot `NRestarts` and `ActiveState` for each restarted unit;
  - `sleep(120)`;
  - check each is still `active` with `NRestarts` unchanged;
  - when hf-timestd runs, `/run/hf-timestd/authority.json` is readable and its timestamp is ≤ 60 s old. Use `heartbeat._read_authority` and its existing freshness notion, `AUTHORITY_FRESHNESS_SEC`;
  - `systemctl start sigmond-heartbeat.service` returns 0; it is a oneshot, so `start` waits for the emit.
  - Returns `{"units_stable": bool, "authority_fresh": bool | None, "heartbeat_sent": bool, "notes": [...]}`.
- `smd align --verify`, read-only with no root:
  - reads aligned.json's `live.at`, or the top-level `at` when `live` is absent;
  - gaps: `heartbeat._read_gap_tsv()` → `_map_gaps`;
  - uploads: `heartbeat._read_backlog(HeartbeatPaths())` → `_map_uploads`;
  - prints each verdict with its note;
  - exit 0 when both are VALID and the gap row is newer than the alignment time; 1 otherwise; 2 when there is no aligned record.
  - A gap row older than the alignment prints `gap sampler has not run since the alignment — re-run --verify after the next hour`, then exits 1.

The `_align_apply` flow after this task:
1. The moves (2a, unchanged). Any blocking outcome → stop, as today.
2. `_align_refresh_image_files(rel.tag)` → steps.
3. `_align_bringup()` → steps. A `failed` or `refused` from stage 2 or 3 → no records, no restarts; exit 1.
4. `record(...)` (2a). Move the call here from right after the moves, so a failed image refresh or bring-up blocks the record. `refused` from stage 2 counts as blocking.
5. Unless `--no-restart`:
   - build the staleness inputs from Task 3's readers: `moved_at` from each checkout's reflog; `moved_at['radiod']` from `_align_radiod_built_at()`;
   - `started_at` per service;
   - `consumes`;
   - radiod consumers: every running service except libraries and radiod itself. Consumer detection beyond "every running client" is out of scope; say so in a comment.
   - Then `stale_components` over the running services, including `radiod`, whose own `moved_at` is its binary's mtime. `radiod_stale = 'radiod' in stale`. Then `restart_sequence(stale, radiod_stale, running, radiod_consumers)`, adding `hs-uploader` when the manifest changed and it runs → `_align_restart` → steps.
6. When anything restarted: `_align_fast_checks` → `record_live(ALIGNED_RECORD, {"at": now, "restarted": [...], **checks})`, plus one history line per restarted component.
7. Exit 0 only when every step succeeded and every fast check passed (`authority_fresh` None counts as pass).
8. The Plan-2b notice line is gone. With `--no-restart` and stale services, print `N services still run old code: <names> — re-run smd align --apply to restart them`.

The dry run also prints a `restarts:` section:
- `predicted_restarts` over the components that would move;
- plus whatever is stale now (the current staleness, so B4's already-moved components show);
- plus the radiod line when it applies.

Tests (CLI, with the patching pattern of `tests/test_align_cli.py`):
- stage order: moves → images → bring-up → record → restart → checks;
- a bring-up failure → no `record`, no `_align_restart`;
- `--no-restart` → no `_align_restart`, and the "still run old code" line printed;
- fast checks with one unit whose `NRestarts` rose → `units_stable` False and rc 1;
- `record_live` merges while keeping `components` and `left`;
- `--verify`: a gap row older than `live.at` → rc 1 with the "has not run since" line; both verdicts VALID with a newer row → rc 0; no aligned.json → rc 2;
- dry run: an already-moved component whose unit started earlier appears under `restarts:`;
- the guard test is unchanged and passes.

Mutations: record before bring-up → the bring-up-failure test FAILS; skip the NRestarts comparison → FAIL.

- [ ] Commit: `align --apply: bring-up, restarts, fast checks and the live record; smd align --verify`.

---

### Task 8: live on B4 (controller task, with Michael)

B4 moved 5 components at 19:14Z with no restarts, so B4's services are stale now. ka9q-python moved, so every consumer of ka9q-python is stale too, **hf-timestd included**.

- [ ] **Push, check, announce.**
  - Michael pushes sigmond, then runs the bootstrap pull (the checkout sits on main, so it is a plain pull as the owner).
  - Read the claude-bus for any live experiment on B4's hf-timestd before announcing.
  - The bus note lists the dry run's `restarts:` section verbatim.
- [ ] **Dry run.** `smd align --release v3.53`. Expect: every component current, sigmond left ahead, exit 0 for code, and a `restarts:` list naming the 5 moved components that run plus ka9q-python's running consumers.
- [ ] **Apply.** `smd align --apply --release v3.53`. Nothing moves, so the output reads:
  - image files current;
  - bring-up ran;
  - the restarts, in order;
  - the fast checks after 120 s;
  - exit 0.
- [ ] **Verify the ownership repair.** `find /opt/git/sigmond -xdev ! -user sigmond` shows only the known pre-existing wsjtx/ft8_lib entries.
- [ ] **Hourly verdicts.** After the next gap-sampler hour, run `smd align --verify` and expect exit 0.
- [ ] **Record.** Report on the bus; update memory `project_smd_align.md`.

## Self-review notes

- **Spec coverage:**
  - §3 step 5 (image files) → Task 6;
  - step 6 (bring-up re-runs) → Task 6;
  - step 7 (record, now after bring-up) → Task 7;
  - step 8 (verify by production) → Task 7, fast checks plus `--verify`;
  - the radiod paragraph → Tasks 4 and 5;
  - Decisions for 2b: restart by default + `--no-restart` → Task 7; staleness from the station → Tasks 2 and 3; radiod only when ka9q-radio moved → Tasks 4 and 5; bring-up before restarts → Task 7; two-speed verification → Task 7; venv ownership → Task 1.
- **B4 findings:** Task 1 covers all four (pin exclude, venv ownership, dry-run ahead, cosmetics).
- **Out of scope, said where it matters:**
  - radiod-consumer discovery finer than "every running client";
  - the wizard-generated `sigmond-location-tick` and its units, which live in sigmond's wizard, not the appliance tree;
  - install_missing's origin-history and byte budget (parked in 2a);
  - the Proxmox host (Plan 3).
- **Type names:** `stale_components`, `predicted_restarts`, `restart_sequence`, `RADIOD`, `RADIOD_GAP_ESTIMATE_S`, `ensure_pin_excluded`, `BUILT_COMPONENTS`, `Ctx.build`, `refresh_image_file`, `record_live`; the bin/smd `_align_*` helpers are named once each in Tasks 3–7.
