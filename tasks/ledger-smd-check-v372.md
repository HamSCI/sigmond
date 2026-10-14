# SDD ledger — plan: /home/mjh/hamsci/repos/sigmond/tasks/plan-smd-check-v372.md
Spec: tasks/spec-smd-check-v372.md (approved 10-14, ASCII marks amended 0a15930). Work on main (Michael's convention; #2 same). Start HEAD 42a0987.

## Preflight scan
| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T1->T2..T7 | T1 Env fields (service, reader, journal, exists, disk, timestd_config, coordination, backlog, http) declared; later tasks fill | consistent |
| T2->T3,T4,T5 | Service/Reader/read_journal/service_problem/is_young/minutes | consistent names in Consumes |
| T6 | consumes coordination/heartbeat helpers (existing code) | ok |
| T7->T8 | page labels, `Fix: `/`run: `, columns 4/15/22 | T8 Consumes repeats them verbatim; consistent |
| T8->T9 | sigmond-check name, marks, Fix: | consistent |
| T1 self | ASCII test + mark constants | agrees |
| T4 self | freeze rule rt==0.0 all OR x,y,z constant | agrees with spec 4.1; DASI-011 10-14 freeze (axes constant, rt junk) is caught by the axes clause |
| T5 self | GRAPE reads hot+disk roots, YYYYMMDD | plan decision 1, departs from spec text for correctness |
| T10 | manual, needs B4 + bus | controller/human |
Plan was executed task-by-task on a scratch copy by its author (4455 passed). No conflicts found.
Ruling: plan decisions 1-11 (GRAPE two roots, sigmond-vm prefix on fixes, host drops VM page lines, heartbeat cursor excluded + 30-min wait after switch-on, journal via sudo -n for non-journal users, ASCII marks) accepted as refinements of the spec — each fixes a measured defect in the spec text — cost if wrong: a line reads differently from spec section 4's wording.
Task 1: dispatched (BASE 42a0987, implementer a0deb17ea503cc6dd, sonnet)
Task 1: implementer DONE_WITH_CONCERNS be4452f (mutation count 7 vs 6, observation); review dispatched on 42a0987..be4452f
Task 1: review approved with 1 Important (judge poison test unproven; package-attribute bypass) + JSON ensure_ascii=False (promoted from Minor: ASCII-everywhere rule); fix round 1 sent to implementer (FIX_BASE be4452f)
Task 1: minor (deferred): sys.stdout.reconfigure is a no-op under ASCII output (brief-mandated)
Task 1: minor (deferred): MARKS constant unused; Env None placeholders until later tasks
Task 1: minor (deferred): fix-line rendering for labels >=15 chars untested with a fix
Task 1: fix round 1/5 implementer done (95afc7b); scoped re-review dispatched on be4452f..95afc7b
Task 1: fix round 1/5 (2 addressed, 0 open; commits be4452f..95afc7b)
Task 1: complete (commits 42a0987..95afc7b, review clean)
Task 2: dispatched (BASE 95afc7b, implementer a88a131a5f8eb30e4, sonnet)
Task 2: implementer DONE a566051; review dispatched on 95afc7b..a566051
Task 2: review approved; 2 Important, both plan-mandated.
Task 2: Ruling: a glob with any failed instance reports failed, and the window age uses the YOUNGEST active instance — spec 4 says FAIL "when the service has stopped or failed"; the plan's priority loop let a healthy @Y mask a dead @X — cost if wrong: a station with one deliberately-failed extra instance reads FAIL until someone disables it.
Task 2: Ruling: SubState auto-restart counts as failed ("restarting repeatedly"), and `activating` is young only while InactiveExitTimestampMonotonic is inside the window — a crash-looping Restart=always unit otherwise reads wait forever — cost if wrong: a slow-starting unit (wspr waiting on radiod) past its window reads FAIL instead of wait.
Task 2: minors folded into the fix (cheap, prevent false FAIL): add os.getgid() to _my_groups; LC_ALL=C for journalctl.
Task 2: minor (deferred): newest_mtime per-entry vanish; Reader.tail parent-dir refusal gives Unreadable not None; service_state ignores systemctl rc; sudo branches of listdir/newest_mtime untested; ReaderTests skip as root.
Task 2: fix round 1 sent (FIX_BASE a566051)
Task 2: fix round 1 implementer done (0ffbacc); re-review dispatched on a566051..0ffbacc (doubt: activating+unknown age counted young)
Task 2: fix round 1/5 (3 addressed, 1 open — activating with unknown age still counted young; commits a566051..0ffbacc). Fix round 2 sent.
Task 2: fix round 2/5 (1 addressed, 0 open; commits 0ffbacc..9ad33e3)
Task 2: complete (commits 95afc7b..9ad33e3, review clean)
Task 3: dispatched (BASE 9ad33e3)
Task 3: implementer DONE 2799559 (brief's PSK mutation row names wrong 2nd test); review dispatched
Task 3: complete (commits 9ad33e3..2799559, review clean)
Task 3: minor (deferred): PSK spot-count window only bracketed at -14/-16 min; add -14:59/-15:01 and now+61s cases
Task 3: minor (deferred): plan mutation row for PSK_WINDOW_S names test_young_service_with_no_spots_waits, which does not catch it (old_service test and counting test do)
Task 4: dispatched (BASE 2799559)
Task 4: implementer DONE 18a1bbc (+DASI-011 freeze test); review dispatched
Task 4: complete (commits 2799559..18a1bbc, review clean)
Task 4: minor (deferred): mag freshness uses > not >= at 10 s; future-dated sample passes (plan-mandated)
Task 4: minor (deferred): exact-float axes clause — keep (matches verbatim-repeat freeze); add a comment
Task 4: minor (deferred): '60 s' text assumes 1 Hz
Task 5: dispatched (BASE 18a1bbc)
Task 5: implementer DONE db60d10; review dispatched
Task 5: complete (commits 18a1bbc..db60d10, review clean)
Task 5: minor (deferred): non-canonical channel description dir mapping only replaces spaces
Task 5: minor (deferred): no test for unreadable config or [recorder]-level archive default
Task 6: dispatched (BASE db60d10)
Note for final review: uploads line does not report an unverified PSWS key (DASI-011 10-14: switch upload, key not yet registered, GRAPE/mag queue silently) — spec silent; consider a follow-up
Task 6: implementer DONE 4c2596f; review dispatched (asked to verify summarize() keys)
Task 6: complete (commits db60d10..4c2596f, review clean). Cannot-verify 'new station' writer resolved by controller: sigmond-wizard.sh:1745-1748, seen on DASI-011 10-14
Task 6: minor (deferred): grace wait keys on coordination.toml mtime, so any unrelated edit masks a stale pipeline for 30 min (plan decision 4 proxy)
Task 6: minor (deferred): tests for default coordination (age None, no ack -> FAIL), Z/naive timestamps, failed sudo path
Task 7: HELD — Task 6 reopened
Task 6: REOPENED — security review: sudo python -m runs as root with caller cwd on sys.path (import hijack). Fix round 1 sent (FIX_BASE 4c2596f): -P + cwd='/'
Task 6: fix round 1 implementer done (ba74dd7: -P + cwd=/, decoy test); re-review dispatched incl. other root-load paths
Task 6: fix round 1/5 (1 addressed, 0 open; commits 4c2596f..ba74dd7)
Task 6: complete (commits db60d10..ba74dd7, review clean)
Task 6: Ruling: root fallback loading hs-uploader from the sigmond-writable /opt/git/sigmond tree is accepted — sigmond already holds NOPASSWD sudo, so no escalation; revisit if the sudo grant is ever narrowed — cost if wrong: a sigmond-account compromise reaches root through this path too (it already does through sudo).
Task 6: minor (deferred): heartbeat._read_backlog runs python -m without -P (invoking user, no escalation)
Task 7: dispatched (BASE ba74dd7)
Task 7: implementer DONE 368165b (full suite 1 warning — check source); review dispatched
Task 7: complete (commits ba74dd7..368165b, review clean). Suite warning is pre-existing: DeprecationWarning catalog.py:474 from test_install_target_classifier, not this change.
Task 7: minor (deferred): http_status HTTPError not closed (possible ResourceWarning at runtime)
Task 7: minor (deferred): empty exception text gives 'status None'
Task 7: minor (deferred): timeout value not asserted in http_status tests
Task 8: dispatched (BASE 368165b)
Task 8: implementer DONE 633fd1a; review dispatched (opus: shell + root-adjacent host script)
Task 8: review approved (opus), no Critical/Important; controller promotes two Minors.
Task 8: Ruling: partial VM output (timeout 124 / ssh 255 / 126/127 after some lines) must add `FAIL decoder VM did not finish` — a false PASS with lines missing defeats the command's purpose — cost if wrong: one extra FAIL line on a slow VM that did finish late.
Task 8: Ruling: VM output with no check lines (old smd without `check`) says "answered, but has no smd check (update the VM)" — pm-align ships sigmond-check to hosts whose VM may lag — cost if wrong: none beyond message text.
Task 8: also folded in: iptables missing (127) -> "iptables not found"; IPv6 pick prefers a non-temporary GUA, then any global (ULA counts), then temporary.
Task 8: minor (deferred): `run: ` insertion is global in the line text; stderr merged into parsed output (2>&1) can drop a fix line.
Task 8: fix round 1 sent (FIX_BASE 633fd1a)
Task 8: fix round 1/5 (4 addressed, 0 open; commits 633fd1a..591f339)
Task 8: complete (commits 368165b..591f339, review clean)
Task 8: minor (deferred): 'update the VM' branch could also catch an unprefixed traceback; harden by requiring 'invalid choice'/'usage:'; 126/127 partial-output not separately tested
Task 9: dispatched (sigmond-appliance, BASE 1759fc1)
Task 9: implementer DONE (appliance 9cacd9f: INSTALL.md + panel line); review dispatched: code-vs-doc check, esp. 'uploads line now says on'
Task 9: review: 1 Important (uploads line after switch-on reads wait/WAIT, doc said "on") + 3 minors folded (antenna advice only for no-fix FAIL; wait "usually" gives a time; ssh paragraph wording). Fix round 1 sent (FIX_BASE appliance 9cacd9f)
Task 9: minor (deferred): INSTALL.md section 12 item 4 still uses smd sink status/upload in the VM (still valid; outside brief)
Task 9: fix round 1/5 (4 addressed, 0 open; appliance commits 9cacd9f..6eb675e)
Task 9: complete (appliance 1759fc1..6eb675e, review clean)
Task 10: manual on B4 — needs push (Michael) first; deferred until after final review
Final review: dispatched (sigmond 42a0987..591f339 + appliance 1759fc1..6eb675e), opus
Final review: With fixes — C1 uploads ok at switch-on (discard acks), C2 no-mag station never passes; I3 bring-up race, I4 ssh password stall, I5 dead radio blamed on antenna, I6 Task 10 not run; minors m1-m4 folded. Ledger triage: all deferred minors 'can wait' except newest_mtime (folded as m2). PSWS-key note: can wait (needs outbound probe).
Final fix wave: dispatched (opus) from .superpowers/sdd/plan-smd-check-v372/final-findings.md
Final fix wave: done (sigmond 591f339..de8d295 excl. f7daa00; appliance 6eb675e..20ca321); suite 4514 passed. Scoped re-review dispatched.
Final fix wave re-review: all findings ADDRESSED. Residual, load-bearing (surface to Michael; no second wave without his OK):
  R1 sticky FAIL: bring-up marker result=gave-up is final and `smd bringup` never rewrites it, so a hand-finished station reads FAIL bring-up forever.
  R2 false `--`: a reflashed/new station WITH a magnetometer whose adapter is missing reads `-- no magnetometer` (empty /var/lib/mag-recorder) -> can PASS. Durable signal: site-profile [psws.instruments]."mag-recorder" (A-).
  R3 awaiting-sdr gets no bring-up line; clients then show useless service fixes.
  R4 check.py ~668 comment cites a ConditionPathExists that no unit carries.
  R5 (out of scope) magnetometer page line runs whenever mag-recorder is installed, even with no magnetometer; gmag-webui may not answer -> no-mag station could still FAIL.
  Deploy note: read /var/lib/sigmond/.firstrun-bringup-done on each fleet VM before v3.72 lands.
Ruling: C1 residual window (seconds; self-correcting) accepted; follow-up: reference max(coordination mtime, hs-uploader ActiveEnterTimestamp) — cost if wrong: a discard ack inside a few-second window reads ok for up to 30 min.
Residual pass (Michael approved): dispatched from .superpowers/sdd/plan-smd-check-v372/residual-findings.md (R1-R5 + docs)
Residual pass: done (sigmond 3500076, 0ac4178; appliance d85b6d3), suite 4535 passed; re-review dispatched
Residual re-review: R1-R5 addressed; R1 has a regression. Ruling: settle ONLY result=gave-up (retry self-completes on next boot), ONLY after `sigmond-site-timing` exits 0 (warn that isolation activates at next reboot), and skip when firstrun is the caller (env var firstrun exports); guard the sudo-cat FileNotFoundError; keep the marker root-owned (sudo path whenever the original's uid != euid) — cost if wrong: a hand-finished gave-up station whose site-timing fails keeps reading FAIL bring-up until someone runs site-timing.
Residual R1 correction: sent to implementer (FIX_BASE 0ac4178)
Residual R1 correction: done 5a407fe, suite 4539; re-review dispatched
Residual R1 correction: re-review clean, ready to merge (minor deferred: unbound st on non-root stat failure; 'exited [Errno 2]' wording)
Tasks 1-9 + final fix wave + residual pass: COMPLETE. Task 10 (B4) pending push.
Task 10: complete on AC0G-B4 23:3x-23:47Z — smd check PASS 2.3 s; sigmond-check PASS 7.9 s after re-pinning the host's stale vm-known_hosts (identity restore 10-13 put back old VM host keys); 0 non-ASCII both; mag stop -> FAIL both sides with correct Fix prefix, start -> ok in 5 s.
