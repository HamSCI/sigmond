# radiod consumer restart hook — restart what a radiod restart left stale

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every time radiod starts — a crash-restart by systemd included — the services that read its RTP streams and started BEFORE this radiod are restarted once, after radiod is serving, so none runs on the old radiod's RTP→UTC anchor.

**Architecture:** A companion oneshot `sigmond-radiod-consumers@%i.service`, pulled in on every radiod start by a `Wants=` drop-in on `radiod@.service` — the pattern `sigmond-radiod-park@` already proves on B4 and ND (it fired on both of ND's crash-restarts, 09-25 09:39Z and 09-26 04:40Z). Its ExecStart runs a new verb, `smd admin radiod restart-stale-consumers --unit radiod@%i.service`, which waits for radiod to serve, takes the lifecycle lock (waiting while another smd operation holds it), and restarts only the radiod consumers whose earliest active unit started before radiod's current start — using the consumer rule and restart machinery `smd align` already has (`_align_services`, `_align_staleness`, `_align_restart`).

**Tech Stack:** Python 3.11 stdlib (bin/smd), systemd units/drop-ins, bash (install.sh). Tests: `.venv/bin/pytest` in the sigmond repo.

**Spec:** none separate — this plan is the design; its motivation and evidence are in "Why" below. Decision owner: mjh, 2026-09-26 ("The radiod consumers need the restart hook").

## Why

- AC0G-ND's RX888 stalled twice (09-25 09:39Z, 09-26 04:40Z); each time radiod quit ("No rx888 data for 5 seconds"), aborted in libusb, and systemd restarted it. psk-recorder kept running and decoded ~0 spots for the rest of the day (`decodes=40/40 spots=0`): its channels came back through ka9q-python's recovery ladder, its timing anchor did not. meteor-scatter, hf-timestd and ka9q-web were also left older than radiod. wspr-recorder alone recovered, by its own TIMING FAULT self-restart.
- The 2026-08-31 decision in `systemd/sigmond-radiod-consumer.conf.in` removed `PartOf=` so recorders would "self-heal". The ND evidence shows channel recovery is not timing recovery. This plan restores the restart WITHOUT `PartOf=` — whose costs that file records (a manual `systemctl start radiod` left every recorder down) — by restarting consumers after radiod is back, never stopping them when it goes away.
- `ExecStartPost` is not used: `systemd/sigmond-radiod-park@.service` records that an ExecStartPost hook silently no-oped four times on real restarts (B4 2026-08-29/30).

## Global Constraints

- Consumers = running services whose catalog entry `requires` ka9q-radio (absent from the catalog → counted), exactly `_align_staleness`'s `radiod_consumers`. No `[radiod].consumers` declaration is required (ND has none).
- Stale = a consumer's earliest active unit `ActiveEnterTimestamp` is BEFORE the named radiod unit's `ActiveEnterTimestamp`. At boot consumers start after radiod, so nothing is stale and the hook is a no-op.
- Never start a stopped consumer; "activating" (restart backoff) counts as running (the align rule, `_align_running_units`).
- Each component restarts in ONE `systemctl restart` call (hf-timestd's units together), in `order_units` order — `_align_restart(False, comps, services)`.
- Wait for radiod to serve first: `/usr/local/sbin/sigmond-radiod-ready --unit <unit> 90`; not ready → restart nothing, exit 2.
- Lifecycle lock: wait for it (poll every 5 s, up to 600 s) rather than fail; re-read staleness AFTER acquiring it (an `smd align` that just restarted radiod restarts its consumers itself — afterwards nothing is stale). Lock still busy at the deadline → exit 3, restart nothing.
- `--dry-run` prints what would restart and changes nothing.
- Exit codes: 0 nothing stale or all restarted; 1 a restart failed; 2 radiod not ready / unit unknown; 3 lock not obtained.
- radiod itself is never restarted by this verb.
- Every test is watched failing first; each behaviour change gets a mutation noted under "Mutations:" in the commit body.

---

### Task 1: `smd admin radiod restart-stale-consumers`

**Files:**
- Modify: `bin/smd` — new `cmd_radiod_restart_stale_consumers(args)` beside `cmd_radiod_migrate` (~line 20604); a `restart-stale-consumers` parser under `radiod_sub` (~line 22498); dispatch in the `args.command == 'radiod'` block (~line 24029).
- Create: `tests/test_radiod_consumer_hook.py` (unittest style; load bin/smd by path exactly as tests/test_align_cli.py does at its top: `importlib.machinery.SourceFileLoader`).

**Interfaces:**
- Consumes (existing, in bin/smd): `_align_services() -> {component: [unit]}`; `_align_staleness(base, services, catalog=None, *, run) -> {'running', 'radiod_consumers', 'started_at', ...}`; `_align_restart(radiod_first: bool, components: list, services: dict, *, run, say, units_out=None, nrestarts_out=None) -> list[Step]`; `_align_run(argv, *, run, timeout, action)`; `lifecycle_lock(reason)` (from sigmond.lifecycle — raises SystemExit when busy); `_ALIGN_RADIOD_READY = '/usr/local/sbin/sigmond-radiod-ready'`.
- Produces: `_radiod_unit_started_at(unit, *, run) -> Optional[float]` (ActiveEnterTimestamp as unix seconds when ActiveState is active, else None); `_radiod_stale_consumers(radiod_start: float, staleness: dict) -> list[str]` (sorted component names in `staleness['radiod_consumers']` whose `started_at` is not None and < radiod_start); `_wait_lifecycle_lock(reason, *, deadline_s=600, poll_s=5, sleep=time.sleep, clock=time.monotonic)` (context manager that retries `lifecycle_lock` on SystemExit until the deadline; raises TimeoutError at the deadline); `cmd_radiod_restart_stale_consumers(args, *, run=subprocess.run, sleep=time.sleep) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
class RadiodStaleConsumersTests(unittest.TestCase):
    def _st(self, started, consumers):
        return {"radiod_consumers": set(consumers), "started_at": dict(started),
                "running": {c for c, t in started.items() if t is not None}}

    def test_only_consumers_older_than_radiod_are_stale(self):
        st = self._st({"psk-recorder": 100.0, "wspr-recorder": 300.0,
                       "hf-timestd": 150.0, "stopped": None}, 
                      {"psk-recorder", "wspr-recorder", "hf-timestd", "stopped"})
        self.assertEqual(smd._radiod_stale_consumers(200.0, st), ["hf-timestd", "psk-recorder"])

    def test_non_consumers_are_never_stale(self):
        st = self._st({"station-web": 100.0}, set())
        self.assertEqual(smd._radiod_stale_consumers(200.0, st), [])


def _show(active, ts):
    return f"ActiveState={active}\nActiveEnterTimestamp=@{ts}\n"


class RadiodUnitStartedAtTests(unittest.TestCase):
    def test_active_unit_reads_its_start(self):
        run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, _show("active", 1790000000), "")
        self.assertEqual(smd._radiod_unit_started_at("radiod@x.service", run=run), 1790000000.0)

    def test_inactive_unit_reads_none(self):
        run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, _show("inactive", 1), "")
        self.assertIsNone(smd._radiod_unit_started_at("radiod@x.service", run=run))


class WaitLifecycleLockTests(unittest.TestCase):
    def test_retries_until_the_lock_frees(self):
        attempts = []
        @contextlib.contextmanager
        def lock(reason=None):
            attempts.append(1)
            if len(attempts) < 3:
                raise SystemExit("busy")
            yield
        t = [0.0]
        with mock.patch.object(smd, "lifecycle_lock", lock):
            with smd._wait_lifecycle_lock("x", sleep=lambda s: t.__setitem__(0, t[0] + s),
                                          clock=lambda: t[0]):
                pass
        self.assertEqual(len(attempts), 3)

    def test_gives_up_at_the_deadline(self):
        @contextlib.contextmanager
        def lock(reason=None):
            raise SystemExit("busy")
            yield
        t = [0.0]
        with mock.patch.object(smd, "lifecycle_lock", lock):
            with self.assertRaises(TimeoutError):
                with smd._wait_lifecycle_lock("x", deadline_s=20, poll_s=5,
                                              sleep=lambda s: t.__setitem__(0, t[0] + s),
                                              clock=lambda: t[0]):
                    pass


class RestartStaleConsumersCmdTests(unittest.TestCase):
    """The verb end to end, with every system call faked."""
    SERVICES = {"ka9q-radio": ["radiod@x.service"], "psk-recorder": ["psk.service"],
                "wspr-recorder": ["wspr.service"]}

    def _run(self, *, ready_rc=0, radiod_active=True):
        calls = []
        def run(argv, **kw):
            calls.append(list(argv))
            if argv[0] == "/usr/local/sbin/sigmond-radiod-ready":
                return subprocess.CompletedProcess(argv, ready_rc, "", "")
            if argv[:2] == ["systemctl", "show"] and "radiod@x.service" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, _show("active" if radiod_active else "inactive", 200), "")
            if argv[:2] == ["systemctl", "is-active"]:
                return subprocess.CompletedProcess(argv, 0, "active\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return run, calls

    def _cmd(self, run, *, dry_run=False, stale_started=None):
        started = stale_started or {"psk-recorder": 100.0, "wspr-recorder": 300.0}
        st = {"radiod_consumers": {"psk-recorder", "wspr-recorder"}, "started_at": started,
              "running": set(started)}
        args = argparse.Namespace(unit="radiod@x.service", dry_run=dry_run, wait_ready=90)
        with mock.patch.object(smd, "_align_services", return_value=self.SERVICES), \
             mock.patch.object(smd, "_align_staleness", return_value=st), \
             mock.patch.object(smd, "lifecycle_lock", lambda reason=None: contextlib.nullcontext()), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            rc = smd.cmd_radiod_restart_stale_consumers(args, run=run, sleep=lambda s: None)
        return rc, out.getvalue()

    def test_restarts_only_the_stale_consumer_after_readiness(self):
        run, calls = self._run()
        rc, out = self._cmd(run)
        self.assertEqual(rc, 0)
        restarts = [c for c in calls if c[:2] == ["systemctl", "restart"]]
        self.assertEqual(restarts, [["systemctl", "restart", "psk.service"]])
        ready = next(i for i, c in enumerate(calls) if c[0] == "/usr/local/sbin/sigmond-radiod-ready")
        self.assertLess(ready, calls.index(restarts[0]))
        self.assertNotIn(["systemctl", "restart", "radiod@x.service"], calls)

    def test_radiod_not_ready_restarts_nothing(self):
        run, calls = self._run(ready_rc=1)
        rc, _ = self._cmd(run)
        self.assertEqual(rc, 2)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])

    def test_dry_run_restarts_nothing_and_names_the_stale(self):
        run, calls = self._run()
        rc, out = self._cmd(run, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertIn("psk-recorder", out)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])

    def test_nothing_stale_is_a_quiet_no_op(self):
        run, calls = self._run()
        rc, out = self._cmd(run, stale_started={"psk-recorder": 300.0, "wspr-recorder": 300.0})
        self.assertEqual(rc, 0)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])
```

(The test module's imports: argparse, contextlib, io, subprocess, unittest, unittest.mock as mock, plus the smd loader.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest -q tests/test_radiod_consumer_hook.py`
Expected: FAIL — `AttributeError: module 'smd_under_test_…' has no attribute '_radiod_stale_consumers'`.

- [ ] **Step 3: Implement**

```python
def _radiod_unit_started_at(unit, *, run=subprocess.run):
    """radiod ``unit``'s current ActiveEnterTimestamp (unix s), or None when
    it is not active — the moment consumers must be younger than."""
    r = _align_run(['systemctl', 'show', '--timestamp=unix', '-p', 'ActiveState',
                    '-p', 'ActiveEnterTimestamp', unit],
                   run=run, timeout=15, action=f'systemctl show {unit}')
    state = ts = None
    for line in (r.stdout or '').splitlines():
        key, _, val = line.partition('=')
        if key == 'ActiveState':
            state = val.strip()
        elif key == 'ActiveEnterTimestamp' and val.strip().startswith('@'):
            try:
                ts = float(val.strip()[1:])
            except ValueError:
                ts = None
    return ts if state == 'active' else None


def _radiod_stale_consumers(radiod_start, staleness) -> list:
    """The radiod consumers still running from before ``radiod_start`` — they
    hold the previous radiod's RTP→UTC anchor (AC0G-ND 2026-09-25/26: psk
    decoded 40/40 slots, 0 spots, for a day after each radiod crash)."""
    started = staleness.get('started_at', {})
    return sorted(c for c in staleness.get('radiod_consumers', ())
                  if started.get(c) is not None and started[c] < radiod_start)


@contextlib.contextmanager
def _wait_lifecycle_lock(reason, *, deadline_s=600, poll_s=5,
                         sleep=time.sleep, clock=time.monotonic):
    """lifecycle_lock, waited for: a radiod restart made by another smd
    operation (align, restart) holds the lock while that operation restarts
    the consumers itself — wait, then re-read staleness, rather than fail or
    restart them a second time."""
    end = clock() + deadline_s
    while True:
        try:
            cm = lifecycle_lock(reason)
            cm.__enter__()
        except SystemExit:
            if clock() >= end:
                raise TimeoutError(f'lifecycle lock still held after {deadline_s} s')
            sleep(poll_s)
            continue
        try:
            yield
        finally:
            cm.__exit__(None, None, None)
        return
```

`cmd_radiod_restart_stale_consumers(args, *, run=subprocess.run, sleep=time.sleep)`:
1. `unit = args.unit` (required). Run `[_ALIGN_RADIOD_READY, '--unit', unit, str(args.wait_ready)]` via `_align_run(..., timeout=args.wait_ready + 30)`; rc ≠ 0 → print `radiod-consumers: <unit> not serving — nothing restarted` and return 2.
2. Unless `args.dry_run`, enter `_wait_lifecycle_lock('radiod consumer restart', sleep=sleep)`; TimeoutError → print and return 3. (Dry run takes no lock.)
3. Inside: `radiod_start = _radiod_unit_started_at(unit, run=run)`; None → print not active, return 2. `services = _align_services()`; `st = _align_staleness('/opt/git/sigmond', services, run=run)`; `stale = _radiod_stale_consumers(radiod_start, st)`.
4. None stale → print `radiod-consumers: nothing older than <unit>'s start — nothing to do`, return 0.
5. Print one line per stale component (`  <comp>: started before <unit> — restarting`). Dry run → print `(dry run — nothing restarted)`, return 0.
6. `steps = _align_restart(False, stale, services, run=run, say=print)`; print each step; return 1 if any step outcome is `failed`, else 0.

Parser: `p_r_rsc = radiod_sub.add_parser('restart-stale-consumers', help='restart the radiod consumers still running from before radiod\'s current start (run by sigmond-radiod-consumers@.service on every radiod start)')`; `--unit` (required), `--wait-ready` (int, default 90), `--dry-run`. Dispatch: `if rc == 'restart-stale-consumers': return cmd_radiod_restart_stale_consumers(args)`. The root check lives in the DISPATCH, not in the function (tests call the function directly and must never reach sudo): in the `rc == 'restart-stale-consumers'` branch, `if not args.dry_run and _need_root('admin radiod restart-stale-consumers'): return 1` before calling it. Under systemd the verb already runs as root, so `_need_root` returns False at once.

- [ ] **Step 4: Run the tests; full suite once; mutation**

Run: `.venv/bin/pytest -q tests/test_radiod_consumer_hook.py` → pass; then `.venv/bin/pytest -q -p no:cacheprovider`.
Mutations: (a) `<` → `<=` in `_radiod_stale_consumers` must fail the "only older" test if a started==radiod_start case is added — add that case (started 200.0 == radiod 200.0 → not stale); (b) skip the readiness check → `test_radiod_not_ready_restarts_nothing` fails.

- [ ] **Step 5: Commit** (`smd admin radiod restart-stale-consumers: restart what a radiod restart left stale`, body with Why + Mutations).

---

### Task 2: The companion unit and its wiring

**Files:**
- Create: `systemd/sigmond-radiod-consumers@.service`
- Create: `systemd/radiod-consumers.conf`
- Modify: `install.sh` (beside the park drop-in, ~line 913)
- Modify: `systemd/sigmond-radiod-consumer.conf.in` (append a short note to its PartOf history pointing at the hook)
- Test: `tests/test_radiod_consumer_hook.py` (static checks)

**Interfaces:**
- Consumes: Task 1's verb, invoked as `/usr/local/bin/smd admin radiod restart-stale-consumers --unit radiod@%i.service`.

- [ ] **Step 1: Write the failing static tests**

```python
REPO = Path(__file__).resolve().parent.parent

class ConsumerHookUnitTests(unittest.TestCase):
    UNIT = (REPO / "systemd" / "sigmond-radiod-consumers@.service")
    DROP = (REPO / "systemd" / "radiod-consumers.conf")

    def test_companion_runs_the_verb_after_radiod_and_returns_to_inactive(self):
        t = self.UNIT.read_text()
        self.assertIn("After=radiod@%i.service", t)
        self.assertIn("Type=oneshot", t)
        self.assertNotIn("RemainAfterExit=yes", t)
        self.assertIn("ExecStart=/usr/local/bin/smd admin radiod restart-stale-consumers "
                      "--unit radiod@%i.service", t)

    def test_drop_in_pulls_the_companion_on_every_radiod_start(self):
        self.assertIn("Wants=sigmond-radiod-consumers@%i.service", self.DROP.read_text())

    def test_install_sh_installs_both(self):
        t = (REPO / "install.sh").read_text()
        self.assertIn("systemd/radiod-consumers.conf", t)
        self.assertIn("radiod@.service.d/45-sigmond-consumers.conf", t)
        self.assertIn("systemd/sigmond-radiod-consumers@.service", t)

    def test_no_partof_anywhere(self):
        for p in (self.UNIT, self.DROP):
            self.assertNotIn("PartOf=", p.read_text())
```

- [ ] **Step 2: Run → fail** (files absent).

- [ ] **Step 3: Write the files**

`systemd/sigmond-radiod-consumers@.service`:
```ini
[Unit]
Description=Restart radiod %i's consumers that predate its current start
Documentation=file:/opt/git/sigmond/sigmond/docs/superpowers/plans/2026-09-26-radiod-consumer-restart-hook.md
# Companion to radiod@%i — pulled in by a Wants= drop-in on the radiod
# template, so it runs on EVERY radiod start, systemd's own crash-restarts
# included, in a normal runtime context (the pattern sigmond-radiod-park@
# proved; an ExecStartPost hook silently no-oped on real restarts).
#
# Why: a recorder left running across a radiod restart keeps the old
# radiod's RTP->UTC anchor.  ka9q-python's recovery ladder brings its
# channels back; it does not bring its timing back.  AC0G-ND 2026-09-25/26:
# psk-recorder decoded 40/40 slots and 0 spots for a day after each crash.
# The verb waits for radiod to serve, then restarts only consumers older
# than this radiod — at boot that is none.  It never stops anything when
# radiod stops (that was PartOf=, removed 2026-08-31 for good reasons).
After=radiod@%i.service

[Service]
Type=oneshot
# RemainAfterExit deliberately OFF: the unit must return to inactive so the
# next radiod start triggers it again.
ExecStart=/usr/local/bin/smd admin radiod restart-stale-consumers --unit radiod@%i.service
TimeoutStartSec=1200
```

`systemd/radiod-consumers.conf`:
```ini
[Unit]
# Managed by sigmond install.sh — every radiod instance pulls its consumer
# restart companion on every start.  See sigmond-radiod-consumers@.service.
Wants=sigmond-radiod-consumers@%i.service
```

install.sh, right after the park drop-in install (before its `daemon-reload`):
```bash
    # Consumer-restart companion: on every radiod start, restart the RTP
    # consumers that predate it (they hold the old radiod's anchor).
    $SUDO install -m 0644 "$REPO_DIR/systemd/sigmond-radiod-consumers@.service" \
        /etc/systemd/system/sigmond-radiod-consumers@.service
    $SUDO install -m 0644 "$REPO_DIR/systemd/radiod-consumers.conf" \
        /etc/systemd/system/radiod@.service.d/45-sigmond-consumers.conf
```
and extend the following `ok` message to name it. Run `bash -n install.sh`.

sigmond-radiod-consumer.conf.in: append two comment lines under the PartOf history: `# 2026-09-26: channel recovery proved not to be timing recovery (AC0G-ND psk, 0 spots for a day per radiod crash); consumers older than radiod are now restarted AFTER radiod is back by sigmond-radiod-consumers@.service — still no PartOf=.`

- [ ] **Step 4: Tests, `systemd-analyze verify` if available on the devbox (`systemd-analyze verify systemd/sigmond-radiod-consumers@.service` may complain about the template instance — record what it says), commit with Mutations (delete the Wants= line → the drop-in test fails).**

---

### Task 3: Live — B4 first, then AC0G-ND (controller, with Michael)

Not a subagent task.
1. Push sigmond; bus announcement (B4 = test bench; a deliberate radiod restart there costs about a minute of recording).
2. B4: move sigmond to main; install the two files by hand exactly as install.sh does + daemon-reload (staged script, Michael runs). `smd admin radiod restart-stale-consumers --unit radiod@<b4>.service --dry-run` → expect "nothing to do".
3. B4: `systemctl restart radiod@<b4>.service` (announced). Expect: `sigmond-radiod-consumers@<b4>` runs, waits for ready, restarts every radiod consumer once; `smd align` afterwards shows nothing stale; psk/wspr decode normally within two cycles.
4. Then a crash-restart test on B4: `systemctl kill -s ABRT radiod@<b4>.service` (what ND's libusb abort does) → same outcome via systemd's automatic restart. This is the case the feature exists for.
5. ND: the same install; its current stale consumers are restarted by the first run of the verb (`--unit radiod@AC0G-ND.service`), no radiod restart needed.
6. Record on the bus and in memory.
