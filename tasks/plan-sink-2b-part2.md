# Sink control step 2b part 2: the switches take effect — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task.  Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the switch files decide what leaves a station.  The daemon asks a gate before every send.  The sink writer stores nothing for a repository while its producer reads `off`.  The WSPR tar drops each psk row whose producer does not read `upload`.  sigmond carries each station's `[uploads]` setting into switch files once, writes the marker that turns enforcement on, and from then on writes `site.toml` and `[uploads]` together.  When a client falls below `upload`, sigmond turns off that client's in-process senders and restarts each touched recorder instance alone, never radiod.  With every switch at `upload`, every pipeline ships the same bytes as under part 1.

**Architecture:** hs-uploader gains a `SinkGate` that the pump consults in three places, a starting record for pipelines held from their first pump, a switch check inside `Writer.insert()`, a rider filter in `WsprCycleSource`, two CLI verbs (`sink set`, `sink mark-carried`), a richer `sink status --json`, and the feature word `gate`.  sigmond gains a carry-over module, a lever module, two render forms, the heartbeat file `pipelines.d/sigmond.toml`, the site form of `smd sink` with `fill`, readers of the switch state, and a restore that moves the marker aside.  sigmond never re-implements the switch read rules: it asks `hs-uploader sink status --json`.  sigmond-appliance gains rig assertions.

**Tech Stack:** Python 3.11; SQLite; `tomllib`; `fcntl.flock`; systemd (`systemctl show`, `restart`, `disable --now`); pytest; bash.  sigmond's core stays stdlib-only.  hs-uploader's `sinkswitch` and sink writer stay stdlib-only.

**Spec:** `sigmond/tasks/spec-sink-2b-part2.md`, binding, with "Michael's answers, 2026-10-23" at its end: (1) the rider checks at ship time only; (2) a client that arrives after the carry-over gets `upload` under the site switch; (3) the B4 bench runs the short form, 2 h `fill`, 1 h `off`, 30 min rider; (4) the one-time stale-writer restarts stand accepted, one at a time, never radiod, announced to Rob first.  Design: `plan-sink-control.md` D1, D2, D9, D10, D12, D13, D17-D21, §3.4, §6.2, §6.6, §10.1-§10.3, §11.  Evidence: `research-sink-2b-v377.md`, `ledger-sink-2b-part1.md`.

**Name and number.**  This plan calls the work "step 2b part 2".  It promises no release number.  Whoever builds the image takes the next free number.  "Part 1" means the code v3.78 carries (hs-uploader 29a0806).

## Global Constraints

### Where the work lives

- **Branch, never main.**  Rob builds images from `origin/main` of every component.  In each repo this plan touches, create `sink-2b-part2` from `origin/main` and commit there: hs-uploader, sigmond, sigmond-appliance.  Keep `../hs-uploader` and `../sigmond` checked out on the branch while you work, because sigmond's tests read `../hs-uploader`, and the bundled-copy test compares against it.
- **Push the branch only as a backup**, after each task's review passes.  Merge to main, fast-forward only, only after the rig passes and Michael says go (Task 30).  Merge order: hs-uploader, then sigmond, then sigmond-appliance (part 1 ruling C30).
- **No client repository changes** (spec §2).  wspr-recorder, psk-recorder, meteor-scatter and mag-recorder get no branch and no commit.  Their suites still run against the branch hs-uploader (Task 11), since their venvs import `../hs-uploader` as an editable install.
- Bases at start: hs-uploader 29a0806, sigmond be91e22, sigmond-appliance 734afa3.  The spec reads sigmond 10eca2f; only `tasks/` moved since.  Client anchors stand unchanged where the spec cites them: wspr-recorder b523524 (shim lines 243-259, 739-746 hold), psk-recorder 84e81aa (`recorder.py:206` holds), meteor-scatter 6156e53, mag-recorder 55a450c.
- Do not edit `sigmond/tasks/` except this plan's ledger.  No unit file changes.  Touch no station except in Tasks 26, 29 and 30, each after its own bus post.

### Runners, from each repo's root

- hs-uploader: `.venv/bin/python -m pytest -q`.
- sigmond: `.venv/bin/pytest -q`.
- wspr-recorder, psk-recorder, meteor-scatter, mag-recorder: `.venv/bin/pytest -q`.  Never `uv run` or `uv sync` there: `uv sync` drops sigmond from wspr-recorder's venv, and that once killed WSPR on B4 and ND.
- sigmond-appliance: `bash -n <script>` and each static `test-*.sh` the task names.  `test-nested-v3.sh` and `test-update-v3.sh` run end to end only on the rig.

### Commits

Each commit message has a subject, a body that names the mutation the implementer watched fail, and these two trailers, exactly:

```
Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01H4HYgCZ3dRtK4XWA1Xyoc3
```

Part 1's ruling C5 holds: the trailer stays verbatim whichever model writes the code.

### Lessons from part 1, built in from the start

These five rules answer what part 1's reviews caught again and again.  Every task carries them; reviewers check each one.

1. **Every test must be able to fail.**  The implementer watches each new test fail before the change.  Then, on the finished code, the implementer applies the task's named mutation and watches the test fail again, reverts, and names the mutation in the commit body.  A test that passes at the base (a pin of part 1 behaviour) still needs its mutation.  A test that asserts an absence (nothing shipped, nothing logged, nothing restarted) must also show, in the same test, a positive control: the same harness seeing the thing when it does happen.  A test with no mutation that bites does not count, and the spec reviewer refuses the task.

2. **No log floods.**  A condition that can persist across pumps, cycles, inserts or manifest steps logs on its transition only.  Two kinds exist.  An *order* (an operator set `fill` or `off`) logs once at the transition and never again while it holds (spec §9.2).  A *fault* (a broken or unreadable switch file, an unknown state, `sinks/` missing while the marker stands, an exception inside the gate) logs at ERROR once per file signature and then a reminder at most every 6 hours while it lasts (Ruling 6).  Every task that logs inside a loop carries a test that runs the loop at least 100 times and counts lines.  Mutation: log inside the loop.

3. **Root never writes blindly into a directory another account can write.**  Part 1's security review found root-owned code writing into shared directories.  Part 2 writes as root into four such places: `/var/lib/hs-uploader` (02775 root:sigmond, writable by every recorder account), each client's `/etc/<client>/env/` (owned by the service account), `/etc/mag-recorder/`, and `/run/lock` (1777).  (Amended by ruling C14-3: the install lock moved to `/run/hs-uploader-install/lock`, a directory only root can write, so `/run/lock` drops off this list.)  Every such write goes through one helper (Task 13 in sigmond, `_atomic_write` in hs-uploader's `sinkswitch`): `lstat` the target and refuse a symlink or a non-regular file; create the temporary file in the same directory with `O_CREAT|O_EXCL|O_NOFOLLOW`, mode 0600; write; `fchmod` and `fchown` on the open descriptor, never by path; `fsync`; `os.replace`; `fsync` the directory.  Never `Path.write_text`, never `os.chown(path)`, never a predictable name in `/tmp`.  Each helper has a test that plants a symlink at the target and at the temporary name, and shows the link's target untouched.  Mutation: `Path.write_text`.  Where the data passes to a subprocess, pass it on stdin, not through a file (Ruling 11).

4. **Static tests read real output.**  Part 1's rig parser matched lines without the `uploader: ` prefix that smd prints, and only review caught it.  Every parser in a rig script, and every sigmond parser of hs-uploader output, gets its test input from the real program, never typed by hand:
   - sigmond parses `hs-uploader sink status --json` and `sink set` output.  Its fixtures come from running the branch hs-uploader (`../hs-uploader/.venv/bin/python -P -m hs_uploader ...`) on a fixture tree; a capture script under `tests/fixtures/` regenerates them, and a contract test runs the real checkout and compares (as part 1's `RealCheckoutTests` did).
   - sigmond-appliance's static tests take lines captured from the branch smd running its manifest step and `smd sink` against a fake root (the capture command sits in each test's header), wrapped the way `qm guest exec` returns them (one JSON `out-data` line, double-encoded non-ASCII).  After the rig run (Task 28), the real guest transcript goes through the same static tests.
5. **Every version pairing has a named test.**  The matrix below lists each row of spec §10 plus the pairings this plan found.  The final reviewer walks it line by line.

### The version-pairing matrix

| # | Pairing | What must hold | Proven by |
|---|---|---|---|
| V1 | both part 2 | carry-over, marker, render drops `discard`, heartbeat moves, daemon restarts once, gates | Task 19 step tests; Task 24 E-bis; Task 29 deploy |
| V2 | part 2 sigmond, part 1 hs-uploader (no `gate`) | no carry-over, no marker, part 1 render, heartbeat in legacy manifest, `smd sink fill` writes the legacy hold | Task 16 test 15; Task 20 no-gate case |
| V3 | part 2 sigmond, v3.73-v3.76 hs-uploader (empty feature set) | as V2 | Task 14 probe tests; Task 16 |
| V4 | part 1 or older sigmond, part 2 hs-uploader | no marker, so rule 3 sends all, the writer stores, the rider keeps all | Task 3 rule 3; Task 7, Task 8 no-marker cases; Task 26 run 1 |
| V5 | rollback of both, through restore | marker moved aside, `sigmond.toml` removed before the restored smd renders | Task 22 test 22; Task 24 G-bis |
| V6 | rollback of hs-uploader alone | sigmond renders the old way, removes `sigmond.toml`, and moves the marker aside (Ruling 10) | Task 19 no-gate-with-marker case |
| V7 | rollback of sigmond alone | as V5 for marker and `sigmond.toml`; daemon passes everything by rule 3 | Task 22 |
| V8 | update after a rollback | no marker, carry-over runs again, re-derives `site.toml` from `[uploads]`, keeps client files | Task 18 rerun case; Task 24 G-ter |
| V9 | v3.69-v3.72 render (no `client`) under a part 2 daemon with the marker | rule 4 sends, one INFO line per pipeline | Task 3; Task 26 run 5 (ND) |
| V10 | recorder still running the part 1 writer after an update | stale-writer restarts before any state first reads `off` | Task 17 test 20; Task 29 step 4 |
| V11 | marker appears or vanishes while the daemon runs | gate, writer and rider read the marker live; no restart needed; the starting-record pass runs on its appearance | Tasks 3, 5, 7, 8 |
| V12 | part 1 daemon meets `pipelines.d/sigmond.toml` | precedence silences the legacy heartbeat; one heartbeat runs | Task 16 (fixture built by the real part 1 loader at 29a0806) |
| V13 | bundled writer, or a venv whose `hs_uploader` lacks the part 2 names | no enforcement, no exception | Task 7 test 9 |
| V14 | sigmond reads a `sink status --json` from an hs-uploader with another schema number | sigmond says it cannot read it, falls back to `[uploads]`, enforces nothing new | Task 15 |
| V15 | wspr-recorder's shim (its own `Uploader`, no gate) | behaves as part 1 except the rider, which defaults on | Task 4 test 5; Task 8; Task 11 wspr-recorder suite |

### Ledger and bus

- The controller keeps a ledger in the SDD workspace in the shape of `ledger-sink-2b-part1.md`: a preflight scan, numbered rulings each with its cost if wrong, one line per dispatch, review and fix round, and the deferred minors.  At the end it copies the ledger to `sigmond/tasks/ledger-sink-2b-part2.md`.
- **Read the claude-bus** at the start of every session and at each task boundary (`sudo -n cat /srv/hamsci/claude-bus/README.md`; list with the glob inside `sudo -n bash -c`).  During station work (Tasks 26, 29, 30), keep a background watcher polling about every 2 minutes.
- **Tell Rob of every change, as a partner keeping current, never as a request.**  No repo has an exclusive owner.  After each task's commit passes review and the branch is pushed, post one short note: repo, branch, sha, what changed, and what it means for his recorders, if anything.  Before any station step, post first and act after.  Write each message this way:

```bash
sudo -n bash -c 'f=/srv/hamsci/claude-bus/$(date -u +%Y%m%dT%H%M%SZ)-mjh.md; cat > "$f"; chown mjh:hamsci "$f"; chmod 660 "$f"' < /path/to/note.md
```

- After each push, refresh the code graph: `graphify update /home/mjh/hamsci/repos` (never from `/home/mjh/hamsci`).

## Rulings this plan makes on the spec

The spec leaves these open, or says two things.  The controller records each in the ledger as a numbered ruling.

1. **`gate` lands last** (spec §9.3).  Task 11 adds the word after every task it promises: the pump gate, the starting record, the writer check, the rider, `sink set`, `sink mark-carried`.  Cost if wrong: none; the word only waits.
2. **Rule 1 trusts `governed = false` only from `pipelines.d/sigmond.toml`.**  Part 1's loader and factory accept `governed = false` from any file and from the legacy manifest (`pipelines_d.py:193`, `pipeline_factory.py:286`).  The spec says only `sigmond.toml` declares it.  Left open, a client file that writes `governed = false` would bypass every switch: a leak.  So rule 1 applies when `governed` reads false and the pipeline's origin reads `pipelines.d/sigmond.toml`.  Anywhere else the gate ignores the flag and logs one WARNING per pipeline.  Cost if wrong: a future client that legitimately wants an ungoverned pipeline gets held until the rule widens.
3. **Rule 2 applies to legacy entries only** (origin `legacy`).  A pipeline file cannot claim the heartbeat exemption by naming `client = "sigmond"` or a `heartbeat_sftp` transport.  Cost if wrong: none today; only sigmond renders the heartbeat.
4. **The marker counts live, and by `lstat`.**  The gate, the writer and the rider each ask on every decision whether anything exists at the marker path (`os.path.lexists`), with no cache beyond one call.  Presence enforces; absence passes.  Anything at the path counts, a symlink included, so a planted object can only make the station stricter.  When the gate first sees the marker appear, the daemon runs the starting-record pass (spec §3.5) before any held pump.  Cost if wrong: one `lstat` per decision.
5. **The marker's directory stays group-writable**, and a recorder account could delete the marker and turn enforcement off.  The spec fixes the path, and moving it needs an installer change that §10 rules out.  Those accounts already write the send records in the same directory, so the marker adds no new power.  The plan records this for v3.79: move the marker under `/etc/hs-uploader/sinks/` (root-owned).  Cost if wrong: a compromised recorder account can lift enforcement, as it can already rewind send records.
6. **Faults log once, then every 6 hours; orders log once** (Global Constraint 2).  Spec §9.2 says a held pipeline logs nothing after its first line; that covers orders.  A broken `site.toml` that holds the whole station for a week deserves a reminder.  Cost if wrong: four ERROR lines a day while a fault lasts.
7. **sigmond takes the install lock itself.**  Design §3.2 has hs-uploader's installer hold an install lock; the installer at 29a0806 takes no lock.  (Amended by ruling C14-3: the lock lives at `/run/hs-uploader-install/lock`, in a directory only root can write, not at `/run/lock/hs-uploader-install.lock`; the v3.79 installer must use the new path.)  The manifest step takes it around carry-over, render and land.  It opens the file with `O_NOFOLLOW`, mode 0600, refuses a file owned by anyone but root, and uses `LOCK_NB` with a bounded retry of about 30 s, then exits 1 with a clear line (part 1 ruling C13's pattern).  The installer learning the lock waits for v3.79.  Cost if wrong: two concurrent manifest steps serialize; a stuck holder makes the step fail loudly.
8. **The carry-over derives `site.toml` from `[uploads]` whenever the marker stands absent.**  §6.4 writes `site.toml` only if absent; §6.7 rewrites it after a rollback when the two differ.  One rule covers both: without the marker, write `site.toml` when absent or different from `[uploads]`; write each client file only when absent.  A failed earlier run left a `site.toml` that matches, so nothing changes.  Cost if wrong: none; `[uploads]` holds the truth while the marker stands absent.
9. **Mirror repair needs a readable `site.toml`.**  With the marker standing, the manifest step rewrites `[uploads]` from `site.toml` only when `site.toml` parses and names a known state.  A missing `site.toml` sets no limit (design §3.4), which would let clients ship under a site the operator lowered, so the step recreates it from `[uploads]` with `by = "smd (site file missing)"` and says so.  A broken `site.toml` holds everything (fail closed); the step leaves both files alone, names the file, tells the operator to remove it and run `smd sink <state>`, and exits 1.  Cost if wrong: a station whose site file vanished picks up its last `[uploads]` setting, which D19 kept current.
10. **A no-`gate` probe moves the marker aside.**  Spec §10's "rollback of hs-uploader alone" row keeps the marker.  But while hs-uploader lacks the gate, `smd sink` writes `[uploads]` alone.  When hs-uploader returns, the marker would still stand, and mirror repair would copy the stale `site.toml` over the newer `[uploads]`: an operator's `fill` would turn back into `upload`, a leak.  So when the probe returns a set without `gate` (not `None`) and the marker stands, the manifest step moves the marker aside exactly as restore does (`carry-over-v1.done.rolled-back-<UTC>`), and the carry-over runs again once the gate returns.  Michael confirmed this ruling on 2026-10-23 (answer 5).  Cost if wrong: one extra carry-over run, which keeps every client file and rewrites `site.toml` from `[uploads]`.
11. **`sink set --identity-json -` reads stdin.**  sigmond passes the identity tables on stdin, so root never writes a temporary file.  A path still works.  Cost if wrong: none.
12. **One implementation of the read rules.**  sigmond learns every effective state from `hs-uploader sink status --json`, through the daemon's interpreter with `-P` and `cwd=/` (part 1 ruling C23).  The JSON carries `"schema": 1`; sigmond refuses any other number, says so, and falls back to `[uploads]` (V14).  The carry-over's own mapping (§6.3) computes the states it intends to write, which differs from reading them.  Cost if wrong: one subprocess per command.
13. **The read-only manifest step previews the post-carry-over render.**  `smd admin uploader manifest` without `--write`, marker absent, `gate` offered: it prints the carry-over it would make and diffs the gate render, since that is what `--write` would land.  Cost if wrong: none; it writes nothing.
14. **`sigmond.toml` lands in the no-change branch too.**  Spec §3.6 lands the file in step 7.  When the legacy manifest stays the same but `sigmond.toml` needs writing or removing (for example, a probe that newly lacks `gate`, Ruling 10), the step's no-change branch (step 4) lands or removes it with no daemon restart: both part 1 and part 2 daemons rebuild from `pipelines.d/` within one pump.  Cost if wrong: one more file operation.
15. **The neutrality tool predicts HELD by its own reader.**  The tool computes which pipelines the switches hold with its own small reader (`tomllib`, stricter-of, fail closed), never through `hs_uploader.sinkswitch`, as spec §11.2 already requires for D12's inference.  A defect in the library then cannot hide itself.  Cost if wrong: about 30 lines of duplicated logic in a tool.
16. **`Writer` gains keyword-only `sinks_dir=None, marker=None`.**  `None` means the library defaults, so `from_env` and every recorder behave as the spec says; tests pass scratch paths.  The writer imports the switch names inside a `try` that catches `ImportError` and `AttributeError` (a venv whose `hs_uploader` predates a name), and either means no enforcement.  Cost if wrong: two unused keywords in the bundled copy.
17. **The lever step writes through the safe writer**, and `upload.set_env_flag` moves onto it too, so `smd config upload` gains the same protection.  Cost if wrong: none; the file contents stay identical (pinned by test).
18. **Rig first, then the bench, then merge.**  Part 1 ran the B4 bench before the rig on Michael's explicit word (C31) and still owes its rig runs.  Here the rig comes first (spec §13.3: "after the rig passes"), the bench runs from the branch by checkout, and the merge waits for Michael's go after both.  Cost if wrong: the bench waits for a rig slot.
19. **Persistent faults in sigmond print every time a command runs**, since a command runs once and exits; the rule against floods applies to daemons and recorder processes, not to one-shot commands.  Cost if wrong: none.

## Review Focus

Reviewers check spec compliance and code quality on every task (the standard pair).  Three gates add a third reviewer, on the most capable model, with the questions below.

**ENFORCEMENT GATE** (Tasks 3, 4, 6, 7, 8, 10, 12).  The gate never leaks: list every path by which a byte leaves the daemon (source batch, replay, drain-mode batch, the rider, the heartbeat) and show each one asks the gate or carries an exemption by rule 1 or 2, and that rule 1 and rule 2 accept nothing else (Rulings 2, 3).  The gate never wrongly holds: show that the heartbeat, a client-less pipeline, a station without the marker, and every pipeline under all-`upload` switches send; show the proof's run 3 AGREE.  Show that a held pipeline moves no record, pops no retry and writes no attempt row.

**CARRY-OVER GATE** (Tasks 18, 19, 22, 24).  The carry-over runs exactly once: a second manifest step with the marker standing writes nothing and restarts nothing.  A failure anywhere before the marker leaves no marker, and the next step runs it again.  Restore moves the marker aside, and the next update runs it again, keeps every client file's history, and re-derives `site.toml` from `[uploads]`.  The marker lands last.

**RESTART GATE** (Tasks 17, 20, 29).  Every restart names one unit by `systemctl restart <unit>`; no path reaches `cmd_restart`, a client-wide restart, or any `radiod` unit.  radiod's `ActiveEnterTimestamp` reads the same before and after, and the step fails loudly when it moves.  A second run restarts nothing.  Each restart appears once in the output, named.

## Task order

| # | Task | Repo | Spec tests |
|---|---|---|---|
| 1 | Branches, ledger, Rob's heads-up | all | — |
| 2 | Marker helpers; emitter errors name the file | hs-uploader | 12 (part) |
| 3 | `SinkGate`: rules 1-5 | hs-uploader | 2, 11 (gate half) |
| 4 | The gate in the pump; the held path | hs-uploader | 3, 4, 5 |
| 5 | The starting record | hs-uploader | 6 |
| 6 | The daemon passes the gate; transition logging; the switch report | hs-uploader | 7 |
| 7 | The writer's switch check; the bundled copy | hs-uploader, sigmond | 8, 9 |
| 8 | The psk rider gate | hs-uploader | 10 |
| 9 | `sink set`, `sink mark-carried`, `sink status` enforced | hs-uploader | 12, 13 (status half) |
| 10 | The leak harness | hs-uploader | 1, 11 (ship half) |
| 11 | The feature word `gate`; client suites against the branch | hs-uploader (+ four client suites, read-only) | 13 (features half) |
| 12 | The neutrality tool: `--sinks`, `--marker`, HELD | hs-uploader | §11.2 |
| 13 | Safe writes as root; the boolean TOML setter | sigmond | 19 (mag half) |
| 14 | The hs-uploader runner, the probe first, the install lock | sigmond | 15 (`None` half) |
| 15 | Reading `sink status --json` | sigmond | 23 (reader half) |
| 16 | Two render forms; `pipelines.d/sigmond.toml` | sigmond | 14, 15 |
| 17 | The lever step, single-instance restarts, stale writers | sigmond | 19, 20 |
| 18 | The carry-over | sigmond | 16 |
| 19 | The manifest step wires it together | sigmond | 14-16 (step), V1, V6 |
| 20 | The site form of `smd sink`, `fill`, the aliases, D19's dual write | sigmond | 17, 18 |
| 21 | Readers of the site state; the `smd update` notice | sigmond | 21, 23 |
| 22 | Restore moves the marker aside | sigmond | 22 |
| 23 | Nested rig assertions | sigmond-appliance | §13.1 |
| 24 | Update rig assertions (E-bis, G-bis, G-ter) | sigmond-appliance | §13.1 |
| 25 | Docs | all three | — |
| 26 | The proof on fresh B4 and ND copies | devbox (stations read-only) | §11.3 runs 1-7 |
| 27 | Final cross-repo review | all three | — |
| 28 | Rig runs: nested install and update in place | rig | §13.1 |
| 29 | The B4 bench, short form | B4 | §13.3 |
| 30 | Merge to main, with Michael's go | all three | — |

Order notes.  Task 5 lands before Task 6: a daemon that gates without the starting record would skip a fresh station's `fill` backlog.  Task 7 needs sigmond's branch for the bundled copy, so Task 1 creates all three branches.  Task 11 lands after Tasks 2-10 (Ruling 1).  Task 15 lands before every sigmond task that reads a state.  Task 17 lands before Task 18, since the carry-over runs the lever step.  Task 26 needs Tasks 12 and 18.

---

### Task 1: Branches, ledger, Rob's heads-up

**Files:** none in a repo.  The ledger in the SDD workspace.

- [ ] **Step 1: Read the bus** (CLAUDE.md commands).  Note anything Rob has in flight on hs-uploader, sigmond, sigmond-appliance or the recorders.
- [ ] **Step 2: Create the branches.**

```bash
cd /home/mjh/hamsci/repos
for r in hs-uploader sigmond sigmond-appliance; do
  git -C $r fetch origin && git -C $r switch -c sink-2b-part2 origin/main
done
git -C hs-uploader rev-parse --short HEAD   # expect 29a0806
git -C sigmond rev-parse --short HEAD       # expect be91e22
git -C sigmond-appliance rev-parse --short HEAD  # expect 734afa3
```

  If a head moved, record the new base in the ledger and re-check the spec's anchors in the files that moved.
- [ ] **Step 3: Run each suite at the base and record the counts**, so later tasks can tell a new failure from an old one: hs-uploader, sigmond, and the four client suites.
- [ ] **Step 4: Post the heads-up to Rob** (spec §13.2), as news, not a request.  It covers: the writer check runs inside psk-recorder, meteor-scatter and wspr-recorder processes; sigmond edits their env files when a client drops below `upload` (the levers of §8.2); each touched instance restarts alone, never radiod; before any state first reads `off`, sigmond restarts each recorder instance still on the old writer, one at a time (Michael accepted this, answer 4); the rider filter runs inside wspr-recorder's shim.  It asks one factual question: does any station run a WSPR merge fleet whose merge instance ships through the in-process shim?  Also name the branch, and that nothing merges to main before the rig passes and Michael says go.
- [ ] **Step 5: Start the ledger** with the bases, Rulings 1-19 of this plan, and the preflight scan (pair each task with its neighbours, as part 1's ledger did).

**Review:** the controller alone.

### Task 2: Marker helpers; emitter errors name the file

**Files:**
- Modify: `hs-uploader/src/hs_uploader/sinkswitch.py` (new names; `write_switch` error text; extract `_atomic_write`)
- Test: `hs-uploader/tests/test_sinkswitch.py`

**Interfaces:**
- Produces: `MARKER_NAME = "carry-over-v1.done"`; `marker_path() -> Path` (reads `HS_UPLOADER_STATE_DIR`, default `/var/lib/hs-uploader`, the same rule as `watermark.sqlite.default_path`, without importing it); `marker_stands(path: Path) -> bool` (`os.path.lexists`, Ruling 4); `write_marker(path: Path, lines: Sequence[str]) -> None` (UTC time plus one line per file, through `_atomic_write`, mode 0644); `_atomic_write(path, data: bytes, mode: int)` (Global Constraint 3).  `write_switch` re-raises any emitter `TypeError` as `ValueError(f"{path}: cannot re-emit ...")`.

- [ ] **Step 1: Write the failing tests.**
  - `marker_path()` honours `HS_UPLOADER_STATE_DIR`; equals `default_path().parent / MARKER_NAME`.
  - `marker_stands` reads true for a file, a dangling symlink and a directory; false when nothing exists.
  - `write_marker` writes atomically: a reader looping during the write never sees a partial file; mode 0644; the text names each line given.
  - Symlink safety: a symlink planted at the marker path, and another at a predictable temporary name, point at a victim file; after `write_marker` the victim's bytes stand unchanged and the marker path holds a regular file.
  - A switch file holding a hand-written TOML datetime makes `write_switch` raise `ValueError` whose text names the file.
- [ ] **Step 2: Run and watch them fail** — `.venv/bin/python -m pytest -q tests/test_sinkswitch.py -k "marker or emitter"`.
- [ ] **Step 3: Implement.**  Move `write_switch`'s temporary-file code into `_atomic_write` and use it for both.
- [ ] **Step 4: Mutations.**  `marker_stands` uses `Path.exists` (follows links) → the dangling-symlink case fails.  `write_marker` uses `Path.write_text` → the symlink test fails.  Drop the re-raise → the emitter test fails.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `sinkswitch: marker helpers; emitter errors name the file (spec §1, §6.4, §9.1)`.

**Review:** per-task pair.  The quality reviewer checks the temporary file opens with `O_EXCL|O_NOFOLLOW` and that `fchmod` runs on the descriptor.

### Task 3: `SinkGate`, rules 1-5

**Files:**
- Create: `hs-uploader/src/hs_uploader/gate.py`
- Modify: `hs-uploader/src/hs_uploader/daemon.py` (re-export `from .gate import SinkGate`, so the neutrality tool finds `hs_uploader.daemon.SinkGate`, spec §11.2)
- Test: `hs-uploader/tests/test_gate.py` (new)

**Interfaces:**
- Consumes: `sinkswitch.effective_state`, `marker_stands` (Task 2); `Pipeline.governed`, `Pipeline.origin`, `Pipeline.client`, the transport's type name.
- Produces: `class SinkGate(sinks_dir: Path, marker: Path)`; `SinkGate.may_send(pipe) -> bool`; `SinkGate.decide(pipe) -> Decision(send: bool, rule: int, reason: str)`, where `reason` reads like "sink filling (site)", "sink off (client)", "switch unreadable (site)", "no client"; `SINK_GATE_ORIGIN = "pipelines.d/sigmond.toml"`.  The gate holds no cache of the marker (Ruling 4).  An exception inside `decide` holds the pipeline and logs as a fault (Ruling 6).

- [ ] **Step 1: Write the failing tests** (spec test 2, and the gate half of test 11).
  - Rule 1: `governed = false` with origin `pipelines.d/sigmond.toml` sends, with the marker standing, no `sigmond` switch file, and `site.toml` at `off`.
  - Ruling 2: `governed = false` from `pipelines.d/psk-recorder.toml`, and from the legacy manifest, gets judged by rule 5, so a client at `fill` holds.  One WARNING per pipeline, not per call (call 100 times).
  - Rule 2: a legacy entry with `client = "sigmond"` sends; a legacy entry with transport `heartbeat_sftp` and another client sends; a `pipelines.d` entry naming either does not (Ruling 3).
  - Rule 3: no marker sends, with `site.toml` at `off`.  Positive control in the same test: create the marker and the same pipeline holds.
  - Rule 4: `client = ""` sends with the marker standing and the site at `off`.
  - Rule 5: all nine site/client pairs; missing client file, broken file, unknown state, missing `sinks/`: each holds.  Missing site file with client `upload` sends.
  - An exception from `effective_state` holds.
- [ ] **Step 2: Run; all fail** (module absent).
- [ ] **Step 3: Implement.**  Rules in the spec's order; the first that applies decides.
- [ ] **Step 4: Mutations.**  Drop the heartbeat exemption → rule 2 tests fail.  Ignore the marker → rule 3's positive control fails.  Hold a client-less pipeline → rule 4 fails.  Swap `off` < `fill` → the nine-pair test fails.  Accept `governed = false` from any origin → the Ruling 2 test fails.  Each in turn; revert each.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `gate: SinkGate answers may_send by rules 1-5 (spec §3.1, tests 2, 11)`.

**ENFORCEMENT GATE.**  The third reviewer reads design §3.4's read rules beside `effective_state` and confirms the four fail-closed cases.

### Task 4: The gate in the pump; the held path

**Files:**
- Modify: `hs-uploader/src/hs_uploader/core.py:279-311` (`Uploader.__init__` gains `gate`), `:410-420` (`_pump_one`), `:432-464` (`_drain_deliverables`, before each pop), `:466-529` (`_drain_source`, before each batch)
- Test: `hs-uploader/tests/test_core_gate.py` (new)

**Interfaces:**
- Produces: `Uploader(..., gate: Optional[Callable[[Pipeline], bool]] = None)`; `None` sends always.  The per-replay check runs before `pop_due_deliverable`, so a held pipeline never pops.

- [ ] **Step 1: Write the failing tests** (spec tests 3, 4, 5), with a stand-in transport that records every call, as `test_core_orchestration.py` does.
  - Test 3: a held pipeline with a queued retry and a source holding rows: no transport call, no attempt row, `deliverable_count` unchanged, attempt count and `next_attempt_at` unchanged, cursor unchanged.  Release the gate: the retry replays first, then the source.  Positive control: the same harness with the gate open records the send.
  - Test 4: a gate that flips to hold after the first replay stops the second; one that flips after the first drain-mode batch (`max_records_per_pump` set) stops the second.
  - Test 5: `Uploader()` with no gate, against a sinks directory and a marker that would hold everything, ships as part 1.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**  The held path returns `False` from `_pump_one` and logs nothing (Task 6 logs transitions in the gate).
- [ ] **Step 4: Mutations.**  Let the held path pop the retry → test 3 fails.  Drop the per-replay check → test 4's first case fails.  Drop the per-batch check → its second case fails.  Default the gate to the system's switch files → test 5 fails.
- [ ] **Step 5: Run the suite three times; all pass** (the pump uses threads).
- [ ] **Step 6: Commit** `core: ask the gate before every send; a held pipeline touches nothing (spec §3.2, §3.3, tests 3-5)`.

**ENFORCEMENT GATE.**  The third reviewer greps `core.py` for every `transport.ship`, `transport.replay` and `send` call and shows each sits behind a gate check in the same pump.

### Task 5: The starting record

**Files:**
- Modify: `hs-uploader/src/hs_uploader/sources/sqlite.py:344-347` (new `initial_cursor()`), `sources/wspr_cycle.py:121-123` (new `initial_cursor()`)
- Modify: `hs-uploader/src/hs_uploader/daemon.py` (`Rebuilder`, `:561-698`: the pass at each build while the marker stands, and when the gate first sees the marker appear, Ruling 4)
- Test: `hs-uploader/tests/test_starting_record.py` (new)

**Interfaces:**
- Produces: `SqliteSource.initial_cursor() -> Optional[bytes]` (highest id within its scope, with the `local = 0` clause and the producer scope, in the source's own cursor encoding); `WsprCycleSource.initial_cursor() -> Optional[bytes]` (the current cycle floor, encoded as the source encodes it); `daemon.write_starting_records(pipelines, store) -> list[str]` (the keys written).  The pass touches only governed pipelines whose source starts at `now` and whose key holds no record, and writes through `advance_cursor`.

- [ ] **Step 1: Write the failing tests** (spec test 6).
  - For each source: `initial_cursor()` equals, byte for byte, the cursor a part 1 pump writes after shipping exactly the rows present now (drive a real pump against a stand-in transport, then compare).
  - A held `now` pipeline with no record gets one at build; a key that holds a record stays untouched (compare bytes); a `beginning` pipeline gets none; a `governed = false` pipeline gets none.
  - The `off` → `fill` → `upload` walk on a fresh store: rows stored during `fill` ship at `upload`, oldest first.
  - The marker appearing while the daemon runs triggers the pass before the next held pump.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**  Reuse each source's cursor encoder; never re-implement the encoding.
- [ ] **Step 4: Mutations.**  Skip the pass (anchor at the first unheld pump, part 1's behaviour) → the walk test fails.  Write over an existing record → the untouched-record test fails.  Skip the marker-appearance trigger → that test fails.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `daemon: a starting record for each now-pipeline held from its first pump (spec §3.5, test 6)`.

**Review:** per-task pair.  The quality reviewer confirms the cursor bytes come from the source's own encoder.

### Task 6: The daemon passes the gate; transition logging; the switch report

**Files:**
- Modify: `hs-uploader/src/hs_uploader/daemon.py:721-865` (`run` builds `SinkGate(sinks_dir_for(manifest), marker_path())` and passes it to `Uploader`), `:626-647` (`report_switches`)
- Modify: `hs-uploader/src/hs_uploader/gate.py` (per-pipeline last decision; transition lines; fault reminders)
- Test: `hs-uploader/tests/test_daemon_gate.py` (new); update `tests/test_daemon_rebuild.py` where it pins "not enforced"

**Interfaces:**
- Produces: INFO "pipeline X held: sink filling (site)" and "pipeline X sending: sink uploading", once per transition; at start, one INFO line per held pipeline; once per client-less pipeline, "pipeline X names no client; its manifest's policy governs it"; faults at ERROR once per signature plus a reminder every 6 h (Ruling 6).  `report_switches` drops "(not enforced in this release)" and says "enforced" when the marker stands.

- [ ] **Step 1: Write the failing tests** (spec test 7).
  - One INFO line per pipeline per transition; a hundred held pumps add none.  Positive control: a transition after those hundred adds exactly one.
  - At start, one line per held pipeline.
  - Rule 4's line appears once per pipeline for the daemon's life, not per pump or per rebuild.
  - A broken `site.toml`: one ERROR at first sight; none over 100 pumps; one reminder after the clock passes 6 h (inject the clock).
  - The daemon built from a manifest with the marker standing gates (an integration test through `run`'s builder, with a stand-in transport).
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Mutations.**  Log inside the held branch of `_pump_one` → the hundred-pump test fails.  Log the rule-4 line per pump → its test fails.  Remove the reminder → the 6 h case fails.  Build `Uploader` without the gate → the integration test fails.
- [ ] **Step 5: Run the suite three times; all pass.**
- [ ] **Step 6: Commit** `daemon: enforce the sink switches; log each transition once (spec §3.2, §9.2, test 7)`.

**ENFORCEMENT GATE.**  The third reviewer checks that the gate the daemon builds reads the same `sinks/` directory that `sink status` and the writer read, and the same marker path.

### Task 7: The writer's switch check; the bundled copy

**Files:**
- Modify: `hs-uploader/src/hs_uploader/sink/writer.py:263-283` (`infer_producer`, unchanged, reused), `:304` (`__init__` gains `sinks_dir`, `marker`), `:402-435` (`insert`)
- Modify: `sigmond/lib/sigmond/hamsci_sink/_bundled.py` (refreshed by recipe only)
- Test: `hs-uploader/tests/test_sink_writer_switch.py` (new); sigmond `tests/test_hamsci_sink_compat.py`

**Interfaces:**
- Consumes: `sinkswitch.effective_state`, `marker_stands`, `marker_path`, `DEFAULT_SINKS_DIR`, imported inside `try/except (ImportError, AttributeError)` (Ruling 16).
- Produces: `Writer(..., *, sinks_dir=None, marker=None)`; `Writer.dropped` (a per-producer count; `health` stays green); one INFO line per producer when its decision changes.

- [ ] **Step 1: Write the failing tests** (spec tests 8, 9).
  - `off` drops; `fill` and `upload` store.
  - A mixed psk batch (FT8 from psk-recorder, MSK144 from meteor-scatter) with meteor-scatter at `off`: MSK144 rows drop, FT8 rows store.
  - Rows with `producer = ''` get judged by `infer_producer`.
  - A producer with no switch file stores; no marker stores; `local=True` stores.  Positive control in each: change the one condition and the row drops.
  - A switch change counts at the next `insert()` with no restart.
  - 1,000 inserts at `off` log one INFO line.
  - Test 9: the bundled copy matches; with `hs_uploader` unimportable (monkeypatch `sys.modules`), and with a `sinkswitch` that lacks `marker_stands`, the writer stores and raises nothing.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement** in hs-uploader.  Judge the producer as `flush()` does: `self.producer or infer_producer(...)`.  Never log per row.
- [ ] **Step 4: Mutations.**  Remove the check → the `off` test fails.  Invert the marker test → the no-marker test fails.  Judge by `self.producer` alone → the mixed-batch test fails.  Check local writers too → the `local=True` test fails.  Let the `ImportError` propagate → test 9 fails.  Log per row → the 1,000-insert test fails.
- [ ] **Step 5: Run the hs-uploader suite; all pass.  Commit in hs-uploader** `sink writer: store nothing for a repository while the producer reads off (spec §4, tests 8, 9)`.
- [ ] **Step 6: Refresh sigmond's bundled copy by the recipe in sigmond's CLAUDE.md, never by hand.**

```bash
cd /home/mjh/hamsci/repos/sigmond
.venv/bin/pytest -q tests/test_hamsci_sink_compat.py   # must FAIL: the copies differ
{ sed '/^# ---- END sigmond bundled-copy header/q' lib/sigmond/hamsci_sink/_bundled.py
  cat ../hs-uploader/src/hs_uploader/sink/writer.py; } > lib/sigmond/hamsci_sink/_bundled.py.new
mv lib/sigmond/hamsci_sink/_bundled.py.new lib/sigmond/hamsci_sink/_bundled.py
.venv/bin/pytest -q tests/test_hamsci_sink_compat.py   # must PASS
.venv/bin/pytest -q
git diff --stat lib/sigmond/hamsci_sink/_bundled.py
```

- [ ] **Step 7: Commit in sigmond** `hamsci_sink: refresh the bundled writer from hs-uploader <sha> (switch check)`.

**ENFORCEMENT GATE.**  The third reviewer confirms the check runs at `insert()`, not at `flush()`, and that a `fill` or `upload` producer never drops.

### Task 8: The psk rider gate

**Files:**
- Modify: `hs-uploader/src/hs_uploader/sources/wspr_cycle.py:75-125` (`__init__` gains `sinks_dir`, `marker`), `:358-401` (the rider select adds `producer`), `:419-422` (the per-cycle line adds the count left out)
- Modify: `hs-uploader/src/hs_uploader/pipeline_factory.py` (pass the daemon's `sinks_dir`)
- Test: `hs-uploader/tests/test_wspr_rider_gate.py` (new)

**Interfaces:**
- Produces: `WsprCycleSource(..., sinks_dir=DEFAULT_SINKS_DIR, marker=None)`, where `None` means `marker_path()`; the default stays on, so wspr-recorder's shim, which passes neither, filters too.  A sink without the `producer` column infers for every row.

- [ ] **Step 1: Write the failing tests** (spec test 10).
  - A cycle with FT8 from psk-recorder at `fill` and MSK144 from meteor-scatter at `upload` ships the MSK144 rows only; the cycle's WSPR spots and noise stay byte-identical to the unfiltered batch.
  - `producer = ''` rows get inferred.
  - No marker keeps every row.  Positive control: add the marker and the FT8 rows drop.
  - A source built as the shim builds it (`WsprCycleSource(include_psk=True)` plus the shim's other arguments, copied from `wspr-recorder/wspr_recorder/hs_uploader_shim.py:739-746`) still filters, given the default paths monkeypatched to scratch.
  - A sink without the `producer` column still filters by inference.
  - 100 cycles with the same decision log one INFO line per producer.
  - Ship-time rule only (Michael's answer 1): a row stored while its producer read `fill` ships in the tar once the producer reads `upload`.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Mutations.**  Drop the rider gate → the first test fails.  Skip inference for `''` → its test fails.  Default the rider gate off → the shim test fails.  Log per cycle → the 100-cycle test fails.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `wspr_cycle: a psk row rides only while its producer reads upload (spec §5, test 10)`.

**ENFORCEMENT GATE.**  The third reviewer shows the cycle's send record still advances when the tar ships, so a dropped rider never rides later.

### Task 9: `sink set`, `sink mark-carried`, `sink status` enforced

**Files:**
- Modify: `hs-uploader/src/hs_uploader/cli.py:138-147`, `:213-273`
- Test: `hs-uploader/tests/test_cli_sink.py` (new); `tests/test_cli.py`
- Create: `hs-uploader/tests/fixtures/sink_status/` (fixture trees whose real output sigmond's tests will capture, Task 15)

**Interfaces:**
- Produces:
  - `hs-uploader sink set <site|client> <off|fill|upload> --reason TEXT --by TEXT [--identity-json FILE|-] [--sinks DIR | --manifest PATH]`, through `write_switch()`; exits 1 with the reason on a bad state, a bad name or a non-root caller on the real directory; prints one line, `sink set: <name> <old> -> <new> (<path>)`.
  - `hs-uploader sink mark-carried [--note TEXT] [--marker PATH]`, through `write_marker()`.
  - `hs-uploader sink status [--json]`.  The JSON carries `"schema": 1`, `"enforced"`, `"marker"`, `"sinks_dir"`, `"site": {state, error, set_at, set_by, reason}`, `"clients": [{client, file_state, effective, held_by: "site"|"client"|"fault"|null, error, pipelines}]`, `"held_pipelines": [{pipeline, client, reason}]`, `"refused_files": [{file, client, reason}]`, `"no_client_pipelines": [...]`.  The text form says "enforced" when the marker stands.

- [ ] **Step 1: Write the failing tests** (spec tests 12, 13 status half).
  - `sink set` writes through `write_switch` (monkeypatch it and see the call, and the lock taken) and refuses `bogus`, `../x`, `A`.
  - `--identity-json -` reads stdin into identity tables (Ruling 11).
  - `mark-carried` writes atomically, mode 0644, with the note.
  - `sink status` reads "enforced" with the marker; lists held pipelines with client and reason; lists a client whose pipeline file the daemon refused (part 1 deferred minor); `--json` matches the schema above key for key.
  - An emitter error from `sink set` names the file.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Mutations.**  Write the file directly, bypassing `write_switch` and its lock → the lock test fails.  Write the marker with `write_text` → Task 2's symlink case, reused here, fails.  Drop a key from the JSON → the schema test fails.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `cli: sink set, sink mark-carried; sink status says enforced (spec §9.1, §9.2, tests 12, 13)`.

**Review:** per-task pair.  The spec reviewer checks the JSON against §7.5's list of what sigmond shows.

### Task 10: The leak harness

**Files:**
- Test: `hs-uploader/tests/test_leak_harness.py` (new); no source change expected.  A defect found here goes back to its task as a fix round.

- [ ] **Step 1: Write the harness** (design §11; spec test 1, and the ship half of test 11).  A real `Writer` on a scratch `sink.db`, the daemon's `Uploader` with `SinkGate`, stand-in transports that record every byte, a heartbeat pipeline from `pipelines.d/sigmond.toml`, and a scratch `sinks/` and marker.  Drive it through every site state and client state and each transition, writing rows between steps, and flipping a switch from inside a transport callback, between two replays and between two drain-mode batches.  Assert:
  - nothing leaves in `off` or `fill`;
  - `fill` → `upload` ships the backlog once, oldest first;
  - nothing stored during `off` ever ships;
  - the heartbeat ships at every step;
  - with every switch at `upload`, the recorded bytes equal those of a part 1 `Uploader` with no gate fed the same rows (the positive control).
- [ ] **Step 2: Run it** against the branch; it must pass.  It must have failed on any commit before Task 7 (check out Task 6's commit in a worktree and watch it fail).
- [ ] **Step 3: Mutations, each in turn.**  Remove the gate from `_pump_one`; from the per-replay check; from the per-batch check; remove the writer check; accept `governed = false` from any origin.  Each must fail the harness.  Record which assertion caught which mutation in the commit body.
- [ ] **Step 4: Commit** `tests: the leak harness drives every switch state through the daemon (spec test 1)`.

**ENFORCEMENT GATE.**  The third reviewer runs the five mutations again from the commit body and confirms each fails.

### Task 11: The feature word `gate`; client suites against the branch

**Files:**
- Modify: `hs-uploader/src/hs_uploader/cli.py:37`
- Test: `hs-uploader/tests/test_cli.py`

- [ ] **Step 1: Write the failing test** (spec test 13, features half): `--features` prints `gate`, sorted with the others; bare `capabilities` still prints exactly `3\n`, and `int(stdout.strip())` gives 3.
- [ ] **Step 2: Run; it fails.**
- [ ] **Step 3: Implement**: `FEATURES = ("gate", "pipelines.d", "send-log-v3", "switch-files")`.
- [ ] **Step 4: Mutation.**  Print `gate` on the bare call → the bare test fails.
- [ ] **Step 5: Run the hs-uploader suite; all pass.  Then run each client suite against the branch hs-uploader**, with `../hs-uploader` on the branch:

```bash
cd /home/mjh/hamsci/repos
for r in wspr-recorder psk-recorder meteor-scatter mag-recorder; do
  (cd $r && .venv/bin/python -c "import hs_uploader,os;print(os.path.dirname(hs_uploader.__file__))" \
     && .venv/bin/pytest -q) ; done
```

  Each must print `/home/mjh/hamsci/repos/hs-uploader/src/hs_uploader` and match its Task 1 count.  A new failure there means part 2 changed a recorder's behaviour; it goes back to its task as a fix round, and the bus note says so.
- [ ] **Step 6: Commit** `capabilities: gate (spec §9.3, test 13)`.

**Review:** per-task pair.  Lands only after Tasks 2-10 (Ruling 1).

### Task 12: The neutrality tool: `--sinks`, `--marker`, HELD

**Files:**
- Modify: `hs-uploader/tools/neutrality_check.py` (worker builds each `Uploader` with the tree's `SinkGate` when `hs_uploader.daemon` offers it; per-side `--new-sinks`, `--new-marker`; a third verdict; the rider's expected batch)
- Test: `hs-uploader/tests/test_neutrality_check.py`

**Interfaces:**
- Produces: `--sinks DIR` and `--marker PATH` for the NEW side; verdicts AGREE, HELD, DISAGREE.  HELD means NEW produced no batch, popped no retry and wrote no record, where the tool's own reader says the switches hold (Ruling 15).  For `wspr-wsprdaemon` the tool computes NEW's expected batch: OLD's batch minus the psk rows whose producer does not read `upload`, by its own copy of D12's inference.  The final RESULT line counts each verdict, and reads PASS only when no DISAGREE appears and every HELD was expected.

- [ ] **Step 1: Write the failing tests.**
  - All switches at `upload`, marker standing: every pipeline AGREE.
  - One client at `fill`: its pipelines HELD, the others AGREE.
  - psk-recorder at `fill`: `wspr-wsprdaemon` matches the expected batch.
  - A NEW tree whose gate holds a pipeline the switches allow ends DISAGREE (a held pipeline where AGREE was expected).
  - A NEW tree with the gate removed ends DISAGREE when a client sits at `fill`.
  - A NEW tree with the rider gate removed ends DISAGREE with psk-recorder at `fill`.
  - An OLD tree without `SinkGate` runs as before.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**  Keep `_TapStore`'s overrides and their signatures.
- [ ] **Step 4: Mutations.**  Make the tool use `hs_uploader.sinkswitch` for its expectation, then break `effective_state` the same way on both → the tool must still catch it (this proves Ruling 15).  Ignore `--sinks` → the `fill` test fails.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `neutrality_check: HELD verdict; the rider's expected batch (spec §11.2)`.

**ENFORCEMENT GATE.**  This tool proves the release; the third reviewer confirms HELD can never stand in for a DISAGREE.

### Task 13: Safe writes as root; the boolean TOML setter

**Files:**
- Create: `sigmond/lib/sigmond/safe_write.py`
- Modify: `sigmond/lib/sigmond/upload.py:78-105` (`set_env_flag` and `_chown_to_env_dir_owner` move onto the helper, Ruling 17)
- Test: `sigmond/tests/test_safe_write.py` (new); `tests/test_upload.py`

**Interfaces:**
- Produces: `write_owned(path: Path, data: str, *, mode=0o644, owner_from_dir=True) -> None` (Global Constraint 3; `fchown` to the directory's owner when root); `set_toml_bool(text: str, section: str, key: str, value: bool) -> str` (writes `false` bare, never `"false"`); `write_toml_bool(path, section, key, value) -> None`, which writes through `write_owned` and re-reads with `tomllib` to confirm a boolean, raising otherwise.

- [ ] **Step 1: Write the failing tests.**
  - `write_owned` with a symlink planted at the target pointing at a victim: refuses, victim unchanged.  With a symlink at a predictable temporary name: victim unchanged.  A directory at the target: refuses.
  - Ownership goes to the directory's owner (run as non-root with a monkeypatched `os.fchown` that records the call; assert it got a descriptor, not a path).
  - `set_env_flag` keeps every other line byte for byte (pin today's output on a fixture).
  - `set_toml_bool` on `[uploader]\nenabled = true` and on a file with no `[uploader]`, and with `enabled = "true"`: `tomllib` reads `False` as a boolean.  The mag config fixture comes from `mag-recorder`'s shipped template (copy the real file into `tests/fixtures/`, naming its source and commit).
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**  Do not reuse `psws._set_toml_field`.
- [ ] **Step 4: Mutations.**  `Path.write_text` inside `write_owned` → the symlink test fails.  `os.chown(path)` → the descriptor test fails.  Write `"false"` → the boolean test fails.
- [ ] **Step 5: Run the sigmond suite; all pass.**
- [ ] **Step 6: Commit** `safe_write: root writes into service-owned dirs never follow a link (spec §8.2; part 1 security finding)`.

**Review:** per-task pair, plus an automated security review of the diff (part 1 ran one on Tasks 7-8 and 14 and found MEDIUM issues both times).

### Task 14: The hs-uploader runner, the probe first, the install lock

**Files:**
- Modify: `sigmond/lib/sigmond/uploader_manifest.py:443-510` (one runner for every hs-uploader call: daemon interpreter, `-P`, `cwd="/"`, timeout, optional stdin)
- Create: `sigmond/lib/sigmond/install_lock.py`
- Modify: `sigmond/lib/sigmond/commands/uploader.py:363-430` (probe features once, first, before `_render_inputs`; keep the printed line)
- Test: `sigmond/tests/test_uploader_features_probe.py`, `tests/test_install_lock.py` (new), `tests/test_uploader_manifest_step.py`

**Interfaces:**
- Produces: `uploader_manifest.run_hs_uploader(args: list[str], *, stdin: Optional[str] = None, timeout: float = 20) -> CompletedProcess`; `install_lock.held(path=Path("/run/hs-uploader-install/lock"), wait=30) -> ContextManager` (Ruling 7, as amended by C14-3).  `cmd_uploader_manifest` holds `features` from its first line on.

- [ ] **Step 1: Write the failing tests.**
  - The runner passes `-P` and `cwd="/"`; a planted `hs_uploader/` in the caller's cwd never loads (part 1 C23's test, extended to the new runner).
  - The features probe runs before the render inputs (a recording fake shows the order).
  - The lock: a second holder waits, then gets it; a holder that never releases makes the second exit after the bound with a clear message; a symlink at the lock path refuses; a lock file owned by another uid refuses.
  - Test 15's `None` half: a probe that times out still renders part 1's form and prints one warning.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Mutations.**  Drop `-P` → the planted-module test fails.  Probe after the render → the order test fails.  Open the lock without `O_NOFOLLOW` → the symlink test fails.  Block with no bound → the stuck-holder test hangs past its timeout and fails.
- [ ] **Step 5: Run the sigmond suite; all pass.**
- [ ] **Step 6: Commit** `uploader manifest: one hs-uploader runner; probe features first; the install lock (spec §6.1, §7.4)`.

**Review:** per-task pair.

### Task 15: Reading `sink status --json`

**Files:**
- Create: `sigmond/lib/sigmond/sink_state.py`
- Create: `sigmond/tests/fixtures/sink_status/` (real JSON captured from the branch hs-uploader, with `capture.sh` that regenerates it)
- Test: `sigmond/tests/test_sink_state.py` (new)

**Interfaces:**
- Consumes: `run_hs_uploader` (Task 14); the JSON of Task 9.
- Produces: `read_state() -> Optional[SinkState]`, with `SinkState(enforced, site, clients: dict[str, ClientState], held_pipelines, refused_files, no_client_pipelines)`; `ClientState(file_state, effective, held_by, error)`; `below_upload(state) -> list[str]`.  `None` when the call fails, times out, or returns a schema other than 1 (Ruling 12, V14); the caller then falls back to `[uploads]` and says so.

- [ ] **Step 1: Capture the fixtures** by running `../hs-uploader/.venv/bin/python -P -m hs_uploader sink status --json --sinks <tree> ...` on each fixture tree from Task 9 (all `upload`; site `off`; psk-recorder `fill`; broken `site.toml`; no `sinks/`; marker absent).  `capture.sh` holds the exact commands.
- [ ] **Step 2: Write the failing tests.**
  - Each fixture parses to the expected `SinkState`.
  - A contract test runs the real `../hs-uploader` checkout and compares its output with the fixture, key for key; it skips only when no checkout sits beside sigmond.
  - `"schema": 2` reads as `None`, with one printed line naming the number.
  - A timeout reads as `None`.
- [ ] **Step 3: Run; they fail.**
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Mutations.**  Accept any schema → the schema-2 test fails.  Read `file_state` where `effective` belongs → the psk-recorder `fill` under site `off` case fails.
- [ ] **Step 6: Commit** `sink_state: read the switches through hs-uploader sink status --json (spec §7.5; Ruling 12)`.

**Review:** per-task pair.  The quality reviewer checks the fixtures came from `capture.sh`, not from a hand edit (re-run it; `git diff` stays empty).

### Task 16: Two render forms; `pipelines.d/sigmond.toml`

**Files:**
- Modify: `sigmond/lib/sigmond/uploader_manifest.py:321-370` (heartbeat), `:513-559` (`effective_mode` and `policy_banner` serve only the old form), `:620-679` (`generate` gains `features` and `marker_stands`)
- Create: function `sigmond_pipeline_file(coord) -> str` and `land_sigmond_file(text: Optional[str], dir: Path) -> bool` (creates `pipelines.d/` root:sigmond 0755 when missing; writes 0644 through `write_owned`; `None` removes)
- Test: `sigmond/tests/test_uploader_render_forms.py` (new)

**Interfaces:**
- Produces: `generate(..., features: Optional[frozenset], marker_stands: bool) -> RenderResult(legacy_text: str, sigmond_file: Optional[str])`.  With `gate` and the marker: every client pipeline with `client`, no `discard`, no hold, no banner, the heartbeat only in `sigmond_file` with `governed = false` and no `start_at`.  Otherwise (no `gate`, empty set, `None`, or `gate` with the marker absent): exactly part 1's render and `sigmond_file = None`.

- [ ] **Step 1: Write the failing tests** (spec tests 14, 15).
  - Test 14: with `gate` and the marker, the legacy bytes stay identical for `[uploads]` at upload, hold and discard; no `discard = true` anywhere; the heartbeat appears only in `sigmond_file`.
  - Test 15: without `gate`, with the empty set, and with `None`: byte-identical to part 1's render for each `[uploads]` mode (pin it by rendering the same inputs with sigmond be91e22, the branch base, imported from a worktree), heartbeat in the legacy manifest, `sigmond_file = None`.
  - `sigmond_file` parses through the real part 1 loader (`../hs-uploader` at 29a0806 in a worktree): one `[[pipeline]]`, accepted, `governed = false`; and precedence then silences a legacy heartbeat with `client = "sigmond"` (V12).
  - `land_sigmond_file` creates the directory with the right mode and writes atomically; `None` removes the file; a symlink planted at the target refuses.
  - D13 holds: a producer's daemon pskreporter pipeline stays out while its in-process sender runs.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Mutations.**  Render `discard` for site `off` under the gate → test 14 fails.  Trust the daemon whenever the probe fails → test 15's `None` case fails.  Leave the heartbeat in the legacy text under the gate → the heartbeat test fails.
- [ ] **Step 5: Run the sigmond suite; all pass.**
- [ ] **Step 6: Commit** `uploader manifest: the gate render, and the heartbeat in pipelines.d/sigmond.toml (spec §3.6, §7.4, tests 14, 15)`.

**Review:** per-task pair.

### Task 17: The lever step, single-instance restarts, stale writers

**Files:**
- Create: `sigmond/lib/sigmond/sink_levers.py`
- Modify: `sigmond/lib/sigmond/inprocess_senders.py:119-192` (expose what each instance would need, reused, not copied)
- Test: `sigmond/tests/test_sink_levers.py` (new)

**Interfaces:**
- Consumes: `sink_state.below_upload` (Task 15); `inprocess_senders._instance_units`, `_psk_recorder_sends`, `_meteor_scatter_sends`, `_systemctl_show`; `upload.env_path_for`, `set_env_flag`; `safe_write.write_toml_bool` (Task 13).
- Produces: `plan_levers(clients_below: list[str], topology, *, roots) -> LeverPlan` (pure: files to change, units to restart, units left alone, `wspr-uploader` action, mag action); `apply_levers(plan, *, systemctl) -> LeverResult`; `stale_writer_units(topology, *, writer_py: Path, systemctl) -> list[str]`; `restart_units(units, *, systemctl) -> None` (one `systemctl restart <unit>` at a time; records radiod's `ActiveEnterTimestamp` for every `radiod@*` unit before the first and compares after the last; raises `RadiodMoved`); `verify_levers(plan, *, systemctl) -> list[str]` (spec §8.4; empty means every sender stopped).  `roots` holds `etc` and the writer path, so the proof and the tests run on a scratch tree.

- [ ] **Step 1: Write the failing tests** (spec tests 19, 20), with a fake `systemctl` that records every call and serves `show` from a table.
  - Each lever of §8.2 gets written for a client below `upload`: psk direct and `server-merge` → `PSK_DELIVERY_PIPELINES=server-raw` (and the legacy `PSK_DELIVERY_MODE` case); meteor-scatter `direct`, unset and `deposit` → `METEOR_SCATTER_DELIVERY_MODE=off`; the wspr shim → `WSPR_USE_HS_UPLOADER=0`; `wspr-uploader` → `disable --now`; mag → `[uploader] enabled = false` as a boolean.
  - Site at `upload`, psk-recorder at `fill`: psk-recorder's instances alone change and restart.
  - Only touched units restart, each once, by `systemctl restart <unit>`; the call list holds no `radiod` unit and nothing from `cmd_restart`.
  - radiod's start time moving between the reads raises `RadiodMoved`, and the message names the unit.
  - A second run with nothing to change writes nothing and restarts nothing.
  - `verify_levers` names an instance whose effective environment still arms a sender.
  - Test 20: stale writers (start before the writer file's mtime) restart before the first change to `off`, not for `fill`; fresh ones stay; an instance the lever step already restarted does not restart twice.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**  Raising restores nothing (spec §8.2).
- [ ] **Step 4: Mutations.**  Route through `cmd_restart` → the call-list test fails.  Write `"false"` as a string → the mag test fails.  Restart untouched units → the psk-alone test fails.  Skip the stale-writer restart → test 20 fails.  Skip the radiod comparison → the `RadiodMoved` test fails.
- [ ] **Step 5: Run the sigmond suite; all pass.**
- [ ] **Step 6: Commit** `sink_levers: turn off in-process senders below upload; restart each instance alone (spec §4.3, §8, tests 19, 20)`.

**RESTART GATE.**  The third reviewer greps the module for `restart`, `cmd_restart`, `radiod` and `stop`, and reads each call.  A static test also asserts the module never imports `lifecycle` restart helpers.

### Task 18: The carry-over

**Files:**
- Create: `sigmond/lib/sigmond/sink_carryover.py`
- Test: `sigmond/tests/test_sink_carryover.py` (new); fixtures under `tests/fixtures/carryover/` (one tree per §6.6 station class)

**Interfaces:**
- Consumes: `uploader_manifest._pipeline_decls` (`:191-201`); `coordination` (`[uploads]`, `[host]`, `[station]`); `instance.py:116-140` (reporter ids from env-file names); `psws.read_state`; `inprocess_senders` env reads; `sink_levers` (Task 17); `run_hs_uploader` (Task 14); `sink_state` (Task 15).
- Produces: `plan(roots) -> CarryPlan` (pure: the site state with its reason, each client file to write with its state, reason and identity tables, each existing file kept, levers to run, stale-writer restarts); `apply(plan, *, runner, systemctl) -> CarryResult`; `late_clients(roots) -> CarryPlan` for the marker-standing case (§6.5; Michael's answer 2: `upload` under the site switch, by §6.3's table).

- [ ] **Step 1: Write the failing tests** (spec test 16).
  - One case per row of §6.3's tables: `mode = "discard"` → site `off`; `enabled = false`, no mode → `fill`; `enabled = true` and no block → `upload`; `PSK_USE_HS_UPLOADER=0` → psk-recorder `fill`; `METEOR_SCATTER_USE_HS_UPLOADER=0` → meteor-scatter `fill`; a governed client not enabled but with sink rows → `fill`; otherwise `upload`.
  - K3LR's case: site `fill`, psk-recorder `fill`, the lever moves psk to `server-raw` and restarts each psk instance alone.
  - `WSPR_USE_HS_UPLOADER=0` ignored; a client with no outbound route gets no file; mag-recorder without a PSWS id still `upload`.
  - Each first history entry carries `by = "carry-over"`, the reason text of §6.3, and one identity table per instance, passed on stdin (Ruling 11).
  - Order: site, then clients, then levers and stale writers when any reads `off`, then the marker last (a recording runner shows the sequence).
  - Idempotence: a second run with the marker standing writes nothing; a failure at the lever step leaves no marker, and a third run completes and writes only what was missing.
  - Rerun after a rollback (marker absent, files present, `[uploads]` changed meanwhile): `site.toml` rewritten from `[uploads]`, every client file kept with its history unchanged (Ruling 8, V8).
  - Late client: marker standing, meteor-scatter newly enabled with no file → `upload`, `by = "carry-over"`, one printed line.
  - Producers come from `SELECT DISTINCT producer` on a read-only (`mode=ro`) connection, with D12's inference for `''`; the carry-over writes nothing into `sink.db` (a checksum before and after matches).
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**  The plan prints each client's before and after (design §10.1).
- [ ] **Step 4: Mutations.**  Read the wspr flag → the ignore test fails.  Write the marker first → the order test fails.  Rewrite an existing client file → the idempotence and rerun tests fail.  Write `site.toml` only when absent → the rerun test fails.  Open `sink.db` read-write → the checksum test fails if anything writes (pair it with a planted trigger that would write).
- [ ] **Step 5: Run the sigmond suite; all pass.**
- [ ] **Step 6: Commit** `sink_carryover: carry [uploads] into switch files once, marker last (spec §6, test 16)`.

**CARRY-OVER GATE.**  The third reviewer walks §6.6's table against the fixtures, one class at a time, and confirms the B4 and K3LR predictions.

### Task 19: The manifest step wires it together

**Files:**
- Modify: `sigmond/lib/sigmond/commands/uploader.py:363` onward (`cmd_uploader_manifest`, `_stop_migrate_land_start`)
- Test: `sigmond/tests/test_uploader_manifest_step.py`; `tests/test_uploader_manifest_part2.py` (new)

**Interfaces:**
- Produces, in this order inside the write path, under the install lock: probe features (Task 14) → if `gate` and no marker, the carry-over (Task 18) → if `gate` and the marker, mirror repair (Ruling 9), late clients, and the lever step (idempotent) → if no `gate` (a set, not `None`) and the marker stands, move the marker aside (Ruling 10) → render (Task 16) → steps 3-8 as today, landing `sigmond.toml` beside the legacy manifest in step 7, or in the no-change branch (Ruling 14).  The read-only form prints the carry-over plan and diffs the gate render (Ruling 13).  A failed carry-over renders part 1's form and exits 1.

- [ ] **Step 1: Write the failing tests**, with fakes for the runner, `systemctl` and the daemon, as the existing step tests do.
  - V1: `gate`, no marker: carry-over, marker, gate render, `sigmond.toml` written, daemon restarted once, radiod untouched.
  - The same again: nothing written, nothing restarted, "already current".
  - A carry-over failure: no marker, part 1's render, exit 1.
  - Mirror repair: marker standing, `site.toml` at `fill`, `[uploads]` at upload → `[uploads]` rewritten in both files, one line naming the edit and pointing at `smd sink`.  `site.toml` missing → recreated from `[uploads]`.  `site.toml` broken → both files left alone, exit 1, the file named.
  - V6: probe without `gate`, marker standing → marker moved aside, `sigmond.toml` removed, part 1's render.  Probe `None` → nothing moved, one warning.
  - Ruling 14: legacy manifest unchanged, `sigmond.toml` needs removing → removed, no daemon restart.
  - The read-only form with no marker prints the carry-over and the gate diff, and writes nothing (a checksum of the fake root before and after).
  - Every printed line carries the `uploader: ` prefix.  Capture the full output of the V1 run into `tests/fixtures/manifest_step_part2.txt` through a capture script; Task 24 feeds it to the rig's static test.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Mutations.**  Run the carry-over with the marker standing → the second-run test fails.  Repair in the wrong direction → the mirror test fails.  Skip the move-aside → V6 fails.  Write during the read-only form → its checksum test fails.
- [ ] **Step 5: Run the sigmond suite; all pass.**
- [ ] **Step 6: Commit** `uploader manifest: carry over, repair the mirror, lower levers, render by the gate (spec §6.1, §6.4, §7.2, §7.4)`.

**CARRY-OVER GATE.**  The third reviewer confirms the carry-over runs once across align, apply, bring-up, `smd sink` and restore, since each reaches this one step.

### Task 20: The site form of `smd sink`, `fill`, the aliases, D19's dual write

**Files:**
- Modify: `sigmond/lib/sigmond/commands/sink.py:1-64` (`fill` arrives; the `FILL_NOT_YET` refusal goes)
- Modify: `sigmond/lib/sigmond/commands/config.py:1122-1126` (`UPLOAD_VERBS`), `:1146-1253` (`cmd_config_uploads`), `:1016-1047` (`_close_doors`, unchanged, called on every exit from `off`)
- Test: `sigmond/tests/test_sink_cli.py`, `tests/test_sink_doors.py`, `tests/test_sink_site_form.py` (new)

**Interfaces:**
- Produces: with `gate`, in order: stale-writer restarts before a change to `off` (Task 17) → `hs-uploader sink set site <state> --reason ... --by "smd sink (<user>)"` → `_patch_uploads_block` on both files → lever step for each client now below `upload` → the manifest step.  Without `gate`: `[uploads]` alone, as part 1, plus one line saying `site.toml` waits for an hs-uploader with the gate.  `smd sink <client> ...` keeps refusing.

- [ ] **Step 1: Write the failing tests** (spec tests 17, 18).
  - Each verb of §7.1 writes `site.toml` (through the runner) and both `[uploads]` blocks with the mapping of §7.1; legacy `off` and `hold` map to `fill`; `discard` maps to `off`.
  - A failed `sink set` exits 1 and names `site.toml`; a failed `[uploads]` write exits 1 and names that file.
  - Test 18: leaving `off` for `fill` or `upload` closes the doors; `fill` → `upload` and `upload` → `fill` do not.
  - `off` keeps `--reason` and the confirmation; with `gate` it no longer asks `hs_uploader_supports_discard`; its words match §7.3.
  - `fill` prints the trim's limits.
  - Raising to `upload` prints, for each client it lifts, callsign, reporter ids, grid and PSWS ids, then "nothing refused", and lists each client that stays below.
  - Lowering runs the lever step for clients below `upload`, and the stale-writer restarts before `off` only.
  - Without `gate`: `[uploads]` alone, part 1's bytes.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Mutations.**  Map the legacy `off` to `off` → the alias test fails.  Close the doors only on `upload` → test 18 fails.  Write `[uploads]` before `site.toml` → the order test fails.  Run the stale-writer restarts for `fill` → its test fails.
- [ ] **Step 5: Run the sigmond suite; all pass.**
- [ ] **Step 6: Commit** `smd sink: off, fill, upload write site.toml and [uploads] together (spec §7.1-§7.3, tests 17, 18)`.

**RESTART GATE.**  The third reviewer traces every restart this command can cause back to Task 17's one function.

### Task 21: Readers of the site state; the `smd update` notice

**Files:**
- Modify: `sigmond/lib/sigmond/commands/sink.py` (`_status`), `commands/config.py` (`status` branch), `commands/check.py:933` (`uploads_line`), `harmonize.py:1215` (`rule_upload_enabled`)
- Modify: the `smd update` code in `sigmond/bin/smd` (find it with `grep -n "def _cmd_update\|def cmd_update" bin/smd lib/sigmond -r`)
- Test: `sigmond/tests/test_sink_cli.py`, `tests/test_check_uploads.py`, `tests/test_harmonize.py`, a new update-notice test

**Interfaces:**
- Consumes: `sink_state.read_state` (Task 15), using the real-output fixtures.
- Produces: `smd sink status` and `smd upload status` read `sink status --json` when the marker stands, `[uploads]` otherwise, and show the site switch, each governed client's file and effective state, which switch sets it, held pipelines, each in-process sender still armed (UNMANAGED), each stale writer, and any mirror mismatch.  `uploads_line` dates the last change by `site.toml`'s `set_at`.  `rule_upload_enabled` stays quiet for an instance whose client reads below `upload`.  `smd update` prints "the sink switches take effect at the next `smd align`" when the marker stands absent and the installed hs-uploader offers `gate`.

- [ ] **Step 1: Write the failing tests** (spec tests 21, 23).
  - Test 23: with the marker, `smd sink status` reads the JSON (a recording runner sees the call); without it, `[uploads]`.
  - Armed senders and stale writers appear by name.
  - `uploads_line` uses `set_at`.
  - Test 21: `rule_upload_enabled` stays quiet below `upload`; positive control: the same instance at `upload` with uploads off still warns.
  - The update notice: printed when the marker stands absent and `gate` offered; silent otherwise.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Mutations.**  Read `[uploads]` only → test 23 fails.  Keep the part 1 rule → test 21 fails.  Print the notice regardless of the marker → its test fails.
- [ ] **Step 5: Run the sigmond suite; all pass.**
- [ ] **Step 6: Commit** `smd: readers follow the switch files once the marker stands (spec §6.1, §7.5, tests 21, 23)`.

**Review:** per-task pair.

### Task 22: Restore moves the marker aside

**Files:**
- Modify: `sigmond/bin/smd:4385-4444` (`_restore_rerender_uploader`)
- Test: `sigmond/tests/test_manifest_restore.py`

**Interfaces:**
- Produces: before the restored smd renders, when the restored sigmond tree lacks `lib/sigmond/sink_carryover.py`, rename the marker to `carry-over-v1.done.rolled-back-<UTC>` and remove `pipelines.d/sigmond.toml`.  Restore to a part 2 tree leaves both.  Switch files stay.

- [ ] **Step 1: Write the failing tests** (spec test 22): restore below part 2 moves the marker aside (the new name carries the UTC time) and removes `sigmond.toml`; restore to part 2 leaves both; every switch file survives byte for byte; a symlink planted at the rolled-back name does not redirect the rename.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**  Rename with `os.rename` inside the same directory, after `lstat` checks.
- [ ] **Step 4: Mutation.**  Keep the marker → the first test fails.
- [ ] **Step 5: Run the sigmond suite; all pass.**
- [ ] **Step 6: Commit** `restore: below part 2, move the marker aside and remove sigmond.toml (spec §6.7, §10, test 22)`.

**CARRY-OVER GATE.**  The third reviewer runs the sequence carry-over → restore → carry-over in one test and confirms the second carry-over writes the marker again and keeps every client file's history.

### Task 23: Nested rig assertions

**Files:**
- Modify: `sigmond-appliance/test-nested-v3.sh:813-860` (Phase D: `mode = "discard"` checks stay), `:1175-1221` (banner and per-pipeline `discard` checks move to `site.toml` and `hs-uploader sink status`)
- Create or extend: a static test, `test-nested-sink-part2.sh`, that `eval`s the new parser functions between markers, as `test-update-upl-mig3.sh` does
- Fixtures: lines captured from the branch smd and hs-uploader (Global Constraint 4), wrapped as `qm guest exec` returns them

- [ ] **Step 1: Write the failing static test first.**  Feed it real output: Task 19's `manifest_step_part2.txt`, Task 15's JSON fixtures, and a capture of `smd sink fill` from Task 20's tests.  It checks the parser finds: site `off`, each client `upload`, the marker, `gate` in the features line (`uploader: hs-uploader features: gate pipelines.d send-log-v3 switch-files`), no `discard` in the manifest, the heartbeat in `sigmond.toml`.
- [ ] **Step 2: Extend the rig.**  After bring-up, the checks above.  Then plant in the guest a direct psk-recorder, an unset meteor-scatter mode with callsign and grid, an enabled `wspr-uploader` and an enabled mag fallback; run `smd sink fill --reason rig`; check each lever written, each touched unit restarted once (by its `ActiveEnterTimestamp`), radiod's start time unchanged.  Every exit code comes from a file or a variable, never `cmd | tail` (the script's own rule).  Every guest read that returns nothing says "not a verdict" and fails, as Phase D does.
- [ ] **Step 3: Run** `bash -n test-nested-v3.sh` and the static test.  The rig run waits for Task 28; the ledger records it as owed.
- [ ] **Step 4: Mutation.**  Match the features line without the `uploader: ` prefix → the static test fails.
- [ ] **Step 5: Commit** `test-nested-v3: assert the switch files, the marker and the levers (step 2b part 2)`.

**Review:** per-task pair.

### Task 24: Update rig assertions (E-bis, G-bis, G-ter)

**Files:**
- Modify: `sigmond-appliance/test-update-v3.sh` (E-bis, G-bis, G-ter)
- Create or extend: `test-update-sink-part2.sh` (static), fed real lines as Task 23's is

- [ ] **Step 1: Write the failing static test first**, from Task 19's captured output and a captured restore run from Task 22's tests.
- [ ] **Step 2: Extend the rig**, updating in place from the v3.78 image.
  - E-bis: the carry-over ran before the render (line order in the step's output), the marker stands, `site.toml` matches `[uploads]`, the heartbeat lives in `sigmond.toml`.
  - G-bis: restore to v3.78 moves the marker aside (the `rolled-back-` name exists), removes `sigmond.toml`, renders the heartbeat in the legacy manifest, and `[uploads]` still matches.
  - G-ter: the next update runs the carry-over again, writes the marker, and leaves every client file's `[[history]]` as it stood (compare a checksum of each client file across G-bis and G-ter).
- [ ] **Step 3: Run** `bash -n test-update-v3.sh` and every update static test (`test-update-upl-mig3.sh`, `test-update-smd-lines.sh`, `test-update-upl-journal.sh`, the new one).
- [ ] **Step 4: Mutation.**  Let G-ter accept a changed client-file checksum → the static test, fed a transcript where a client file changed, fails.
- [ ] **Step 5: Commit** `test-update-v3: the carry-over runs once, rolls back, and runs again (step 2b part 2)`.

**CARRY-OVER GATE.**  The third reviewer checks that G-ter's checksum covers every client file, not only the ones the station had at E-bis.

### Task 25: Docs

**Files:**
- Modify: `hs-uploader/README.md`, `hs-uploader/CLAUDE.md` (the gate, the starting record, the writer check, the rider, `sink set`, `sink mark-carried`, `sink status --json` schema 1, `gate`)
- Modify: sigmond's docs page for the manifest step and `smd sink` (find with `grep -rln "uploader manifest\|smd sink" sigmond/docs`); sigmond's CLAUDE.md sink section (the writer now drops rows at `off`)
- Modify: `sigmond-appliance` docs only where they describe the manifest step's output or `smd sink`
- Modify: release notes draft: "roll back only with `smd admin manifest restore`" (spec §10)

- [ ] **Step 1:** Describe current behaviour only.  Short sentences, active voice.  Say plainly what `fill` keeps until v3.79 (FT8 one hour, WSPR 24 hours).  No pointer to any private file in a pushed repo.
- [ ] **Step 2: Commit** in each repo `docs: sink control step 2b part 2`.

**Review:** one reviewer for prose accuracy against the code.

### Task 26: The proof on fresh B4 and ND copies (spec §11)

Devbox for every run.  The stations give read-only copies only.

**Inputs:** fresh copies, taken in Step 1.  Part 1's copies of 2026-10-21 predate B4's store version 3, so they do not serve.

**Files:**
- Create: `/home/mjh/hamsci/proofs/sink-2b-part2-copies-<date>/{b4,ac0g-nd}/` (read-only once written) and `/home/mjh/hamsci/proofs/sink-2b-part2-<date>/` (`$W`, the work directory).  Nothing in a repo.  A defect found here goes back to its task as a fix round.

- [ ] **Step 1: Announce on the bus, then copy.**  Post first: which stations, what you will read, that you write nothing on them except a temporary copy you delete, and the time window.  Start the 2-minute bus watcher.  Reach each station with `ops/bin/fleet-ssh`, never a retyped address.  On each station, as the operator account (auto mode blocks root exec there; if a file needs root to read, stage a script in `~/` and ask Michael):
  - `sqlite3 -readonly <db> ".backup <copy>"` for `sink.db` and `watermarks.db`;
  - copy `pipelines.toml`, any `pipelines.d/` and `sinks/`, `topology.toml`, `coordination.toml`, `site-profile.toml`, every `/etc/{psk-recorder,meteor-scatter,wspr-recorder}/env/*.env`, `/etc/wspr-recorder/uploader.env`, the mag-recorder config;
  - record `systemctl is-enabled wspr-uploader`, `systemctl is-active wspr-uploader`, `ActiveEnterTimestamp` for each recorder instance and each `radiod@*` unit, the mtime of `/opt/git/sigmond/hs-uploader/src/hs_uploader/sink/writer.py`, the running hs-uploader and sigmond commits, the store version, and the copy time in `taken_at`;
  - checksum on the station, copy to the devbox, compare checksums, write `SHA256SUMS`, delete the station copies.  Post the end on the bus.

  No heavy work on the station: two `.backup` calls and file reads, nothing else.
- [ ] **Step 2: Predict each station's carry-over** by §6.3 and §6.6 from the copied inputs, before running any code.  Write the prediction into `$W/README.md`: site state, each client's file, each lever, each stale-writer restart.  This also serves §6.6's survey for B4 and ND.
- [ ] **Step 3: Set up.**  `sha256sum -c SHA256SUMS` in each copy directory, before and after the task.  Extract trees: `git -C hs-uploader archive 29a0806 | tar -x -C $W/old`, the branch head into `$W/new`.  Each run passes `--now $(cat taken_at)`.
- [ ] **Run 1, as copied.**  No `sinks/`, no marker.  Every pipeline AGREE.
- [ ] **Run 2, the carry-over on the copies.**  Run `sink_carryover.plan(roots)` from the branch sigmond against the copied inputs, with a scratch root, then `apply` with a recording runner and a fake `systemctl` that writes into the scratch `sinks/`.  Compare files and levers with Step 2's prediction.  Any difference stops the task: either the prediction or the code is wrong, and the ledger says which.
- [ ] **Run 3, marker, every switch at `upload`.**  NEW reads the scratch `sinks/` and marker.  Every pipeline AGREE, as copied and with send records moved back (part 1's two rewinds).  The starting-record pass writes nothing, since each key holds a record; the tool prints each key's record.
- [ ] **Run 4, one client held.**  For each governed client in turn, at `fill` then at `off`: exactly that client's pipelines HELD, every other AGREE.  With psk-recorder or meteor-scatter held, `wspr-wsprdaemon` matches the tool's expected batch.  With wspr-recorder held, both WSPR pipelines HELD.
- [ ] **Run 5, the site held**, at `fill` and at `off`: every governed pipeline HELD, the heartbeat AGREE.  If ND's manifest names no client (a v3.69 render), every pipeline AGREE by rule 4; record which case ND's copy shows.
- [ ] **Run 6, fail closed.**  Marker standing with `sinks/` missing, then with a broken `site.toml`: every governed pipeline HELD.
- [ ] **Run 7, the tool still catches a difference.**  A NEW tree with the gate removed from `_pump_one` and the per-batch check ends DISAGREE on run 4.  A NEW tree with the rider gate removed ends DISAGREE on run 4 with psk-recorder held.  A NEW tree that ignores the marker ends DISAGREE on run 1.  A run that cannot fail proves nothing.
- [ ] **Step 8: Write the record.**  `$W/README.md` lists the prediction, every run, its command, its RESULT line, the batches compared and their digests, and a re-run block a reviewer can paste.  Note what the copies cannot show: file-tree pipelines read spools on the station, so the tool sees their keys, records and HELD verdicts but not their batches (spec §11.4).  The proof's summary goes into the ledger.

**REVIEW GATE (proof):** a reviewer re-runs runs 1, 3 and 7 from the README's commands alone and gets the same RESULT lines.

### Task 27: Final cross-repo review

- [ ] **Step 1: Dispatch one reviewer, on the most capable model,** over hs-uploader 29a0806..HEAD, sigmond be91e22..HEAD and sigmond-appliance 734afa3..HEAD, with the spec, this plan, the ledger and Task 26's README.  Ask for:
  - spec compliance, section by section, with §12's 23 tests mapped to test names and their recorded mutations;
  - the version-pairing matrix walked row by row against the code (V1-V15);
  - the three gates again across the seams: the gate to the writer to the rider (one marker path, one `sinks/`); the carry-over to the step to restore to the rig; every restart path;
  - every root write into a shared directory (Global Constraint 3);
  - every loop that logs (Global Constraint 2);
  - every parser of another program's output and where its test input came from (Global Constraint 4);
  - §14's inheritance list: each item v3.79 needs exists with the named interface;
  - anything that changes a byte a station sends while every switch reads `upload`.
- [ ] **Step 2: Fix wave.**  Important findings get fixed, each by TDD with a mutation.  Minors get fixed or deferred in the ledger with a reason.  Re-run every suite (hs-uploader, sigmond, the four client suites, the appliance static tests) and Task 26's runs 1, 3 and 7.
- [ ] **Step 3: Push the branches** as backup.  Post the state on the bus.

### Task 28: Rig runs: nested install and update in place

These runs need an image built from the branch.  Check the bus and the rig first: no other build or test may run there.

- [ ] **Step 0: Find how the rig builds from a branch.**  Part 1's ledger notes that the golden VM pulls `origin/main`.  Read the build scripts for the ref they fetch (`grep -rn "origin/main\|--branch\|git pull\|git fetch" ` across the build tree on the rig and in sigmond-appliance).  If a branch knob exists, use it and record it.  If none does, stop and ask Michael.  Merging first to make the build work would break the branch rule, and changing the build touches Rob's pipeline; either needs his word.
- [ ] **Step 1: Sync and build** on the rig (`root@192.168.1.182`, synced with `sync-rig.sh`, never from a devbox), by `project_image_build_rig` practice.  Announce the rig slot on the bus.
- [ ] **Step 2: Nested install test.**  `test-nested-v3.sh` through Phase D with Task 23's assertions.  Save the guest's raw output to the ledger workspace, and feed it to Task 23's static test.
- [ ] **Step 3: Update-in-place test.**  `test-update-v3.sh` from the v3.78 image to the branch head: E, E-bis, F, G, G-bis and G-ter must PASS.  Save the raw output and feed it to Task 24's static test.
- [ ] **Step 4: Free the rig on the bus.**  Record both runs' log lines in the ledger.

**Review:** the controller reads both transcripts for any assertion that reported NOT EVALUATED, and treats it as a failure.

### Task 29: The B4 bench, short form

Only after the rig passes, with Michael's go, and after a bus post that names each step and its time.  B4 serves as the test bench.  Keep the bus watcher running.  Record every change to B4's services in `/etc/sigmond/CLAUDE-COORDINATION.md` on the VM (hyphen) before making it.  Reach B4 with `ops/bin/fleet-ssh`.  ND, on its current release, serves as the control.

- [ ] **Step 1: Read the levers first** (spec §13.3 step 6): B4's env files, `wspr-uploader`'s state, the mag config, each recorder instance's `ActiveEnterTimestamp`, radiod's, and the installed writer's mtime.  Write down what the lowerings in Steps 4 and 5 must touch.  D17 predicts no lever; the stale-writer restarts will touch every psk, meteor and WSPR instance once if their writers predate the deploy (Michael's answer 4).
- [ ] **Step 2: Deploy** by checkout of the branch and the manifest step, as part 1 did.  Expect: `gate` in the features line; the carry-over plan of §6.6 (site `upload`; wspr-recorder, psk-recorder, mag-recorder and hamsci-physics `upload`, meteor-scatter if enabled); the marker; one daemon restart; no recorder restart; radiod untouched; the heartbeat reaching the fleetboard from `sigmond.toml`.
- [ ] **Step 3: A night at `upload`**, ND as control: pskreporter, wsprnet and wd30 counts per hour within the night-to-night spread; no held line in the journal; a `last_send` row for every pipeline that sent.  This also clears part 1's owed first-night comparison.
- [ ] **Step 4: `smd sink fill` for 2 hours** (Michael's answer 3).  On the wire (TCP 4739 to pskreporter, SFTP to gw1, gw2 and PSWS, HTTPS to wsprnet) nothing leaves but the heartbeat; count by connection logs and the journal, not by a capture that loads the VM.  wd30 shows no B4 rows after the switch.  Then `smd sink upload`: the backlog ships once, oldest first; wd30 receives the held cycles; pskreporter receives at most the trim's last hour.
- [ ] **Step 5: `smd sink off` for 1 hour.**  Post on the bus first that the stale-writer restarts will run now, one recorder instance at a time, never radiod.  Read-only counts by producer in `sink.db` show no new psk or WSPR rows; codar and hfdl rows keep growing.  Then `smd sink upload`: the doors run, and wd30 shows a gap of exactly that hour.
- [ ] **Step 6: The rider, 30 minutes.**  `hs-uploader sink set psk-recorder fill --reason "bench rider" --by mjh`.  wd30 shows B4's WSPR cycles with no B4 FT8 rows; pskreporter shows no B4 FT8 reports; MSK144 carries on.  Then set it back to `upload`.  Note: this lowers psk-recorder, so the lever step runs at the next manifest step; Step 1 predicted what it touches.
- [ ] **Step 7: Throughout:** radiod's `ActiveEnterTimestamp` never moves; each recorder restart appears in the journal once, named.  Post the end on the bus and record the results in the ledger.

**RESTART GATE.**  The controller compares Step 1's prediction with the journal's restarts, unit by unit.  Any restart not predicted stops the bench.

### Task 30: Merge to main, with Michael's go

- [ ] **Step 1: Ask Michael**, with the rig results, the bench results and the open minors.  Merge only on his word.
- [ ] **Step 2: Check main.**  If `origin/main` moved in any repo since the branch base, rebase the branch onto it, re-run every suite and Task 26's runs 1 and 7, and tell Rob on the bus what moved.
- [ ] **Step 3: Merge fast-forward only**, in the order hs-uploader, sigmond, sigmond-appliance.  Push.  Refresh the code graph.
- [ ] **Step 4: Post on the bus**: the merged heads, that Rob's next image carries part 2, what changes on update (the carry-over at the next `smd align`), and the rollback rule (only through `smd admin manifest restore`).
- [ ] **Step 5: Copy the ledger** to `sigmond/tasks/ledger-sink-2b-part2.md` and commit it on main with the trailers.
