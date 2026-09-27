# radiod drop-ins from smd, systemd jobs wait for the lock, and cleanups

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every station gets radiod's systemd drop-ins even when ka9q-radio is installed after sigmond; smd jobs started by systemd timers wait for the lifecycle lock instead of failing; and four small cleanups land.

**Architecture:** One idempotent function in bin/smd, `_ensure_radiod_dropins()`, owns the radiod template drop-ins and their companion units. It runs from the radiod install path (after the radiod unit exists), from `smd doctor --fix` (with a doctor finding when any is missing), and therefore from align's bring-up, which runs `doctor --fix`. install.sh's existing block stays for hosts where radiod already exists. Separately, `lifecycle_lock` gains a bounded wait used automatically when smd runs under systemd without a terminal.

**Tech Stack:** Python 3.11 stdlib (bin/smd, lib/sigmond/lifecycle.py), systemd drop-ins, bash (install.sh). Tests: `.venv/bin/pytest`.

**Spec:** none separate; decisions by mjh 2026-09-27 ("proceed with #1, 4, 5, small clean-ups 6"). Evidence:
- AC0G-B4 (rebuilt 2026-09-23) had NO `/etc/systemd/system/radiod@.service.d/`. install.sh (~line 903) writes the drop-ins only `if compgen -G "/etc/systemd/system/radiod@*.service"`, and on a fresh image sigmond's install.sh runs before ka9q-radio is installed. AC0G-ND has them only because install.sh was re-run on 09-11.
- On 2026-09-26 13:39:24Z B4's `sigmond-storage-trim-all.service` FAILED ("another lifecycle operation is in progress") because the consumer-restart hook held the lifecycle lock; `lifecycle_lock` uses LOCK_NB.

## Global Constraints

- The radiod template drop-in set, exactly (source in `systemd/` → installed name under `/etc/systemd/system/radiod@.service.d/`):
  - `radiod-restart-forever.conf` → `10-sigmond-restart.conf`
  - `radiod-park.conf` → `40-sigmond-park.conf`
  - `radiod-consumers.conf` → `45-sigmond-consumers.conf`
  and the companion units installed to `/etc/systemd/system/`: `sigmond-radiod-park@.service`, `sigmond-radiod-consumers@.service` (find how install.sh installs the park unit today and match it).
- `_ensure_radiod_dropins()` writes a file only when its bytes differ; runs `systemctl daemon-reload` once only if something changed; NEVER restarts radiod or anything else; returns the list of paths it wrote. It does nothing (returns []) when no `radiod@.service` template unit exists (`systemctl cat radiod@.service` fails / no FragmentPath).
- Doctor: a finding `radiod-dropins-missing` (or the codebase's naming style) listing each missing/stale path; `--fix` calls `_ensure_radiod_dropins()`.
- Lock wait: when `INVOCATION_ID` is set (systemd started us) AND stdin is not a TTY, a mutating verb waits for the lifecycle lock — poll every 5 s, up to 900 s, logging once when it starts waiting and once when it acquires — instead of exiting "another lifecycle operation is in progress". Interactive use keeps today's immediate refusal. `SIGMOND_LOCK_WAIT_S` overrides the bound (0 = never wait). The consumer-restart hook keeps its own run-wide budget and must not double-wait (it calls lifecycle_lock through `_wait_lifecycle_lock`; make sure the new wait does not nest inside it — e.g. the hook passes wait=0 or uses the primitive directly).
- Every test watched failing first; each behaviour change has a mutation noted under "Mutations:" in its commit body.

---

### Task 1: `_ensure_radiod_dropins()` + radiod install path + doctor

**Files:** Modify `bin/smd` (new helper beside `_ensure_radiod_affinity_drop_ins` ~line 10145; call it at the end of `_install_radiod_native` after the radiod unit is written; a doctor check + fix in `cmd_doctor`'s check list — follow an existing unit/drop-in check's shape). Create `tests/test_radiod_dropins.py`.

**Interfaces:** Produces `_ensure_radiod_dropins(*, systemd_dir=Path('/etc/systemd/system'), src_dir=<repo>/systemd, run=subprocess.run) -> list[str]` and `_radiod_dropins_missing(*, systemd_dir, src_dir, run) -> list[str]` (paths missing or differing). Both take injectable dirs/run so tests use a tmp tree.

- [ ] Tests (tmp dirs, fake run):
  - no radiod template → `[]`, nothing written, no daemon-reload;
  - template present, empty tree → all 5 files written with the source bytes, one daemon-reload, returned list names all 5;
  - second call → `[]`, no daemon-reload (idempotent);
  - one drop-in with different bytes → only it rewritten;
  - `_radiod_dropins_missing` lists exactly the missing/stale paths;
  - no call ever restarts a unit (assert no `systemctl restart`/`start` argv);
  - a doctor-level test: the finding appears when something is missing and `--fix` path calls the ensure function (patch at the seam the existing doctor tests use — find them).
- [ ] Watch them fail; implement; writes go through the same sudo-aware mechanism the neighbouring drop-in writers use (`_run([...], sudo=True)` / temp + mv), not bare `open()` on /etc.
- [ ] `_install_radiod_native` calls it after writing/enabling the radiod unit; print one line per written path.
- [ ] Full suite; mutation: make the ensure skip the byte comparison (always write) → the idempotence test fails; commit.

### Task 2: systemd-started smd waits for the lifecycle lock

**Files:** Modify `lib/sigmond/lifecycle.py` (`lifecycle_lock`), `bin/smd` only if the hook's call path needs `wait=0`; tests in `tests/test_lifecycle_lock_wait.py` (or the existing lifecycle test file — find it).

**Interfaces:** `lifecycle_lock(reason="", *, wait_s=None, poll_s=5.0, sleep=time.sleep, clock=time.monotonic, env=os.environ, isatty=sys.stdin.isatty)` — `wait_s=None` means "decide from the environment" (INVOCATION_ID set and not a TTY → `SIGMOND_LOCK_WAIT_S` or 900; otherwise 0). `wait_s=0` → today's immediate SystemExit.

- [ ] Tests: interactive (no INVOCATION_ID) + busy → immediate SystemExit (unchanged message); systemd + busy then free after N polls → acquires, logs "waiting" once and "acquired" once; systemd + busy past the bound → SystemExit with a message naming the wait; `SIGMOND_LOCK_WAIT_S=0` under systemd → immediate; the lock is never held while sleeping and the fd never leaks (use a real flock on a tmp file, a second fd holding it, like the existing lock tests if any).
- [ ] The consumer-restart hook (`_wait_lifecycle_lock` in bin/smd) must call `lifecycle_lock(..., wait_s=0)` so its own budget stays authoritative; add a test that it passes wait_s=0.
- [ ] Full suite; mutations: ignore INVOCATION_ID (always wait) → the interactive test fails; drop the hook's wait_s=0 → its test fails; commit.

### Task 3: Cleanups

**Files:** `bin/smd`, `scripts/proxmox/pm-align.py`, `tests/test_align_cli.py`, `tests/test_pm_align.py`, `tests/test_radiod_consumer_hook.py`.

- [ ] **smd align placeholder:** the dry run prints both `Proxmox host: level not measured from here — check it with pm-align (Plan 3)` and the newer `host: …` line. Remove the old placeholder line (and any test that pins it; the `_align_host_line` tests stay). Test: the dry-run output contains exactly one line starting with "  host:" and none containing "level not measured".
- [ ] **Hook's untested branch:** in `tests/test_radiod_consumer_hook.py`, add a test where the run-wide lock budget is already spent when a pass begins (`lock_deadline_at <= clock()`), asserting that pass returns 3 WITHOUT calling `_wait_lifecycle_lock` / lifecycle_lock. Mutation: remove the `remaining <= 0` short-circuit → the test fails.
- [ ] **pm-align parked minors** (scripts/proxmox/pm-align.py):
  - `_merge_tunnel_result`: guard the local `tmp.write_text` + `os.replace` so an OSError is reported and never propagates out of `--tunnel-step` after TUNNEL_RESULT landed (test: make os.replace raise → `--tunnel-step` still exits by outcome, a WARN line printed).
  - A record left at `"tunnel": "pending"` when `systemd-run` fails: `--apply` rewrites the record with `"tunnel": "launch-failed"` (test).
  - `_apply_tunnel`'s `os.replace(TUNNEL_RESULT → .prev)`: guard it; on OSError print a WARN and continue the launch (test).
  - `_write_private`: after `os.open(..., 0o600)`, `os.fchmod(fd, 0o600)` so a pre-existing wider-mode file is narrowed too (test: a pre-existing 0644 file ends 0600).
- [ ] Full suite; mutations per item where a behaviour changes; commit(s).

### Task 4: Live (controller)

B4 and ND already carry the drop-ins by hand; verify there that `smd doctor` reports no `radiod-dropins` finding and `_ensure_radiod_dropins()` is a no-op (dry path: doctor without --fix). Push; bus note. The next image build will then carry the fix for fresh installs.
