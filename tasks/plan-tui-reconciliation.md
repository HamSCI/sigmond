# TUI reconciliation — execution plan

Derived from `docs/TUI-RECONCILIATION-DESIGN.md` (the spec) after the Phase 0
ground-truth audit. The spec's Decisions table governs; this file turns its
phases into numbered, independently-executable tasks.

## Context

`lib/sigmond/tui/` is a Textual three-panel app launched by `smd tui`. Left
panel is a nav tree (`widgets/component_tree.py`), centre mounts a screen
module from `screens/`, right panel is contextual help. Screens either shell
out to `smd <verb>` via a per-module `_smd_binary()` helper, or call
`sigmond.*` library functions in-process.

Ground truth at HEAD (`1f063cd`), established by audit:

- 138 smd verbs (`verbs.tsv`), 18 top-level.
- 37 screen modules; 29 reachable as nav-tree leaves, 2 only by key binding
  (`topology` = `t`, `client_config` = `C`), 3 only by composition
  (`authority`, `timing`, `textual_wizard`), 3 fully orphaned
  (`instance`, `config_show`, `placeholder`).
- 117 TUI tests pass in `.venv` (textual 8.2.8). 19 of 37 screens untested.
- Drift since inventory baseline `01d6bb7`: 186 commits touched `bin/smd`,
  69 touched the TUI; last TUI commit 2026-07-24.

## Global Constraints

These bind every task. A reviewer should treat a violation as a defect.

1. **Branch:** work on `main`. This project commits to main and tags
   releases; no feature branches. (Operator rule, overrides skill default.)
2. **Core smd stays stdlib-only.** `bin/smd` and `lib/sigmond/` outside
   `lib/sigmond/tui/` must not import `textual` or `rich` at module scope.
   Textual is a lazy import inside the TUI only. `lib/sigmond/tui/format.py`
   is deliberately Textual-free and is imported by `smd timing` — keep it
   that way.
3. **Tests must be hermetic.** Never read real host state. Follow the
   `dc48e80` pattern exactly: `mock.patch.object` the specific host-touching
   call site with an inline canned value scoped to the test. No new shared
   fixture unless a task says otherwise. Host calls that must be mocked
   include `shutil.disk_usage`, `load_help_toml`, `Topology.enabled_components`,
   `subprocess.run`/`Popen`, and any read under `/etc`, `/run`, `/var`, `/sys`,
   `/proc`.
4. **Run the suite with the repo venv:** `.venv/bin/python -m pytest tests/ -q`.
   Report the tail verbatim. A task is not done while any test fails.
5. **No ASCII/Unicode regressions in CLI output.** `smd` output is ASCII-only
   (commit `eda0fc6`: C-locale consoles render Unicode as mojibake). TUI
   screens may use Unicode; anything printed by `bin/smd` or
   `lib/sigmond/commands/` may not.
6. **Don't widen scope.** Each task touches only what its brief names. Do not
   "improve" adjacent screens, reformat untouched code, or add features the
   brief does not request.
7. **Commit per task** with a conventional prefix (`tui:`, `tests:`, `docs:`).
   Do not push — the controller handles integration.

## Phase 0 rulings — keep / fix / kill

Ruled against the two personas in the spec: (1) on-site control operator,
(2) developer/administrator.

### KILL (8 screens)

| Screen | Why |
|---|---|
| `kiwisdr.py` | Spec kill-default. No KiwiSDR component in any catalog profile (`dasi2`/`base`/`client`); KiwiSDR exists only as an environment-discovery source. No smd verb. |
| `fft_wisdom.py` | Spec kill-default. `bringup` generates wisdom as part of the radiod stack; `smd admin wisdom plan\|status` remains for the rare manual case. No standing operator workflow. |
| `sources.py` | Spec kill-default. Per-client sensor-feed wiring is set by `bringup` under the local-radiod DASI model; `smd admin sources` remains CLI-only. |
| `placeholder.py` | Spec kill-default. Fully orphaned — `_mount_placeholder()` in `app.py` has zero call sites. |
| `topology.py` | Spec kill-default (the "components/topology overlap with the current install model"). Reachable only by the undocumented `t` binding, and it **writes `/etc/sigmond/topology.toml` directly in-process**, bypassing `smd enable`/`smd disable` entirely. Under install-implies-enable (`_enable_after_install`) manual topology editing is obsolete. |
| `client_config.py` | Superseded by `configuration.py`, whose own docstring says it "consolidates the legacy Instance / Client-config / Config-show screens". Reachable only by the undocumented `C` binding. |
| `instance.py` | Orphaned; superseded by `configuration.py` per the same docstring. |
| `config_show.py` | Orphaned; superseded by `configuration.py` per the same docstring. |

Every kill target is imported **only** by a lazy `from .screens.X import Y`
inside an `action_show_*` method in `app.py` (plus `placeholder` in
`tests/test_tui_navigation.py`). No screen imports another kill target.

### KEEP + FIX (4 screens)

| Screen | Fix |
|---|---|
| `overview.py` | Task 4 — gains a timing section (T-level / offset / sigma / chrony). |
| `greenfield.py` | Task 6 — parity with what `bringup`/the wizard do now. |
| `timing_authority.py`, `annotation_quality.py` | Task 4 — reconcile with the new overview timing section so the three don't contradict each other. |

### KEEP as-is (25 screens)

`activity`, `apply`, `authority`, `backup`, `components`, `configuration`,
`cpu_affinity`, `cpu_freq`, `diag_net`, `environment`, `gpsdo`, `install`,
`ka9q_watch`, `lifecycle`, `logs`, `rac`, `radiod`,
`receiver_channels`, `resources`, `restore`, `sdr_inventory`,
`textual_wizard`, `timing`, `validate`, `verifier`.

### ADD (1 screen)

`psws.py` — Task 5. The PSWS enrolment arc plus per-instance upload toggles.

### Flagged for the operator walkthrough (NOT killed this round)

- `radiod.py` (883 LOC) — the spec's boundary decision assigns "per-host
  receiver detail" to ka9q-web, which would make this screen redundant. But
  `radiod.py` is **not** on the spec's kill-default list and it serves the
  developer persona (LAN discovery → channel table → per-SSRC get/set). Kept.
  Raise at the Task 7 walkthrough.
- `receiver_channels.py` (399 LOC) — same tension, same ruling.
- Landing-screen edge case: `app.py on_mount()` sets `greenfield = False` in
  the `except` branch, so a topology **load failure** lands on Overview, not
  Guided bring-up. Arguably backwards, but Overview is the safer default for a
  configured station with a corrupt topology. Documented, not changed.

---

## Task 1 — textual as a dev/test dependency

Phase 1 of the spec: stop the silent drift.

The TUI suite skipped on any interpreter without `textual`. The repo `.venv`
has it (via `scripts/dev-setup.sh`, which installs `.[tui,dev]`), so the
suite does run there — but `pip install -e '.[dev]'` alone yields a
green-looking run that silently skipped every TUI test.

Steps:

1. In `pyproject.toml`, add `textual>=8.0.0` and `rich>=13.0.0` to the
   `[project.optional-dependencies] dev` extra (which currently holds only
   `pytest>=7.0.0`).
2. Fix the stale comment above the `tui` extra. It reads
   `# Keep in sync with _TUI_DEPS in bin/smd.` — `_TUI_DEPS` no longer exists
   anywhere in `bin/smd`; `cmd_tui` probes `import textual` directly. Replace
   it with a comment that states what is actually true.
3. Replace the hand-rolled skip guard with a loud one. Each of the 11
   `tests/test_tui_*.py` files defines its own
   `try: import textual; _HAS_TEXTUAL = True / except ImportError: _HAS_TEXTUAL = False`
   and decorates every class with
   `@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")`. Leave that
   mechanism in place (it is per-class and works), but add to
   `tests/conftest.py` a session-scoped check that **prints a loud warning
   banner to stderr** when `textual` is absent, naming how many TUI test
   files will skip and telling the reader to run `scripts/dev-setup.sh`.
   Silence is what made the June–August blindness possible.

Verification: `.venv/bin/python -m pytest tests/ -q` still passes (1205
collected, 117 TUI). Additionally prove the banner fires: run the suite with
textual hidden, e.g.
`.venv/bin/python -m pytest tests/ -k tui -q -p no:cacheprovider` under an
interpreter where you have monkeypatched the import — or simply assert the
banner function's behaviour in a unit test that fakes the ImportError. A unit
test is preferred over a second interpreter.

## Task 2 — prune the 8 killed screens

Phase 2 of the spec: one commit, recoverable by revert.

Delete these 8 modules from `lib/sigmond/tui/screens/`:
`kiwisdr.py`, `fft_wisdom.py`, `sources.py`, `placeholder.py`,
`topology.py`, `client_config.py`, `instance.py`, `config_show.py`.

Then remove every reference to them:

1. **`lib/sigmond/tui/app.py`** — delete the `action_show_*` methods that
   mount them: `action_show_kiwisdr`, `action_show_fft_wisdom` (confirm the
   exact names by reading the file), `action_show_sources`,
   `action_show_topology`, `action_show_client_config`,
   `action_show_instance`, `action_show_config`, and the `_mount_placeholder`
   helper (line ~582/585). Their lazy imports go with them.
2. **`SigmondApp.BINDINGS`** — remove the `t` (`show_topology`) and `C`
   (`show_client_config`) entries. Leave the other 9 bindings alone.
3. **`lib/sigmond/tui/widgets/component_tree.py`** — remove the tree leaves
   for KiwiSDR live, FFT Wisdom, and Sources from `populate()`, and remove
   the dead `elif` branches in `on_tree_node_selected` for the ids
   `topology`, `instance`, `config_show`, `client_config` (these ids are
   never set by `populate()`). Keep the `authority` and `timing` branches
   only if `populate()` sets them — verify before deleting; the audit says it
   does not, so they go too.
4. **Group hygiene** — after removing Sources, the Maintenance group still
   has Apply/Backup/Restore, and Advanced loses FFT Wisdom, Monitoring loses
   KiwiSDR live. Do not restructure the groups; just remove the leaves.
5. **`tests/test_tui_navigation.py`** — it imports `PlaceholderScreen` (line
   39) and `test_no_stale_placeholders_remain` iterates 14 `action_show_*`
   calls, some of which you just deleted. Rewrite that test to iterate only
   the surviving actions, and replace the placeholder assertion with one that
   asserts the **expected screen class** mounted for each action — the
   current version only proves "not a placeholder", which is a weaker claim
   and becomes meaningless once `PlaceholderScreen` is gone. Also update
   `ComponentTreeStructureTests` for the removed leaves.

Do not delete `lib/sigmond/topology.py`, `lib/sigmond/instance.py`, or
`lib/sigmond/commands/client_config.py` — those are **library** modules with
the same names, unrelated to the screens, and are imported widely.

Verification: `.venv/bin/python -m pytest tests/ -q` passes; grep proves zero
remaining references to the 8 deleted modules under `lib/` and `tests/`.

## Task 3 — screen ↔ verb parity lint

Phase 1 of the spec: "every kept screen maps to an smd verb that still
exists, so an smd rename breaks CI, not the operator."

Add `tests/test_tui_parity.py` plus a declared mapping. Design:

1. Add a module-level mapping in `lib/sigmond/tui/screens/__init__.py` (or a
   new `lib/sigmond/tui/parity.py` if that reads better — implementer's
   call, but it must live in the package, not in the test) of the form
   `SCREEN_VERBS: dict[str, tuple[str, ...]]` — screen module basename to the
   smd command paths it drives, e.g.
   `"apply": ("apply",)`, `"verifier": ("admin verifier report", "admin verifier rehabilitate")`.
   Screens that call sigmond library functions in-process rather than
   shelling out (`environment`, `validate`, `receiver_channels`, `gpsdo`,
   `timing`, `authority`, `annotation_quality`, `radiod`, `resources`,
   `overview`) still declare the verb they are the TUI face of, or the
   sentinel `None` when there genuinely is no counterpart. Use the
   `smd verb(s)` column of `audit-screens.md` as the source data — it is
   already correct at HEAD.
2. The test asserts three things:
   - Every module in `lib/sigmond/tui/screens/` (excluding `__init__.py`) has
     an entry in `SCREEN_VERBS`. A new screen with no entry fails CI.
   - Every non-`None` verb string in `SCREEN_VERBS` resolves against the live
     `bin/smd` argparse tree. Build the verb set by importing `bin/smd` and
     walking the parser — the controller's extraction script is at
     `.superpowers/sdd/TUI-RECONCILIATION-DESIGN/` and works by patching
     `argparse.ArgumentParser.parse_args` to raise with `self`, then calling
     `main()`. Do **not** hardcode a verb list in the test; that would defeat
     the point.
   - Every screen named in `SCREEN_VERBS` actually exists as a module. A
     deleted screen with a stale entry fails CI.
3. The test must be hermetic and must not execute any smd subcommand — only
   build the parser.

Verification: the test passes; then prove it bites — temporarily rename a
verb in `bin/smd`, show the test fails, revert. Include that evidence in the
report.

## Task 4 — timing on the landing screen

Phase 3.1 of the spec. **No new plumbing:** `lib/sigmond/commands/timing_show.py`
(the `smd timing` implementation) already imports `read_authority_snapshot`,
`snapshot_age_seconds`, `format_offset_ns`, `format_sigma_ns`,
`format_age_seconds` and `AUTHORITY_JSON_PATH` **from `sigmond.tui.format`**.
The shared layer is the TUI's own format module; `smd timing` is its consumer.

1. Add a timing section to `OverviewScreen` (`screens/overview.py`, 435 LOC,
   which currently renders data / actions / services / inventory / cpu via
   `_render_*` methods). Add `_render_timing` in the same style, populated
   from `_OverviewData`.
2. Content: the adjudicated T-level (active, with available/witnesses), the
   RTP→UTC offset and sigma, the governor, and the snapshot age — plus the
   chrony side (reference, stratum, offset). Source the authority half from
   `sigmond.tui.format`; source the chrony half by importing
   `_chrony_tracking` from `sigmond.commands.timing_show` **or** by promoting
   that helper into `sigmond.tui.format` if the leading underscore makes the
   import distasteful. If you promote it, `timing_show.py` must import it from
   the new home rather than keeping a copy — no duplicated logic.
3. Colour the verdict using the existing `format_timing_line` tier logic in
   `sigmond.tui.format` (17 tests already cover it in
   `tests/test_tui_overview.py`) rather than inventing a second colour scheme.
4. Reconcile `timing_authority.py` and `annotation_quality.py` with the new
   section: the overview is the **shallow** verdict, those screens are the
   depth. Make sure the three cannot disagree — they must all read the same
   snapshot via the same helpers. If a screen currently re-derives a verdict
   locally, route it through the shared helper instead.
5. Handle the no-authority case exactly as `smd timing` does: a host without
   hf-timestd shows an explanatory line, never a traceback or a fake tier.

Verification: extend `tests/test_tui_overview.py` with hermetic tests that
mock `read_authority_snapshot` and the chrony helper for at least: a healthy
T3 host, a host with no authority snapshot, and a stale snapshot. Assert on
rendered content, not just that the screen mounted.

## Task 5 — PSWS + uploads screen

Phase 3.2 of the spec. Create `lib/sigmond/tui/screens/psws.py`.

Two panes:

1. **Enrolment arc** — `smd psws status`, `smd psws enroll`, `smd psws verify`,
   and `smd config register-radiod`. Show where the station is in that arc
   and offer the next action. Read `smd psws --help` and the `psws`
   implementation in `bin/smd` first; note that `smd psws status` has **no
   `--json` flag**, so either parse its text output defensively or call the
   underlying library function in-process. Prefer the library call. Do not
   add a `--json` flag to `bin/smd` in this task — if you conclude one is
   needed, say so in your report and stop rather than widening scope.
2. **Per-instance upload toggles** — `smd config upload <client> [instance]`
   with `--on` / `--off` / `--via {direct,server-merge,server-raw}` for
   `wspr-recorder`, `psk-recorder`, `meteor-scatter`. Show current state per
   instance and let the operator toggle it. Mutations go through
   `sigmond.tui.mutation.confirm_and_run` like every other mutating screen —
   never a bare `subprocess.run` for a state change.
3. Wire it into the nav tree under **Installation** (the enrolment arc is
   bring-up work), as a leaf after "③ Enable / start / stop", with a matching
   `action_show_psws` in `app.py` and a `SCREEN_VERBS` entry (Task 3).

Do not invent a delivery-verdict backend. The spec mentions "delivery
verdicts"; `smd admin verifier report` already produces them and
`screens/verifier.py` already surfaces them. Link the operator there rather
than duplicating.

Verification: hermetic tests in a new `tests/test_tui_psws.py` mocking the
psws status source and `confirm_and_run`; assert the toggle produces the
exact argv `['smd', 'config', 'upload', <client>, <instance>, '--on']`
(matching the shape asserted in `tests/test_tui_mutation.py`).

## Task 6 — greenfield ↔ bringup / readiness parity

Phase 3.3 of the spec. `screens/greenfield.py` (430 LOC) must match what
`smd bringup` and the image wizard actually do now — `bringup` took 20
commits since the inventory baseline, the most of any area.

1. **Profiles:** the screen must offer the real catalog profiles and default
   to `dasi2`, matching `smd bringup`'s own default. The three profiles in
   `etc/catalog.toml` are `dasi2`, `base`, `client`. Show each one's
   `description` from the catalog rather than a hardcoded string.
2. **Readiness gate:** surface `smd admin readiness`. It has `--json` and a
   `--gate {auto,capture,site}` selector, and exits 0 when ready. Show the
   gate verdict before and after bring-up so the operator knows whether the
   station is actually complete.
3. **Equipment detection panel, including TS-1.** `lib/sigmond/hardware_detect.py`
   exposes `detect_all(components) -> {component: Presence}` and is currently
   used only by the screen being deleted in Task 2 (`topology.py`) — so this
   task inherits its only caller. Separately, `bin/smd` has its **own**
   detection helpers (`_detect_local_sdr`, `_detect_gpsdo`,
   `_detect_magnetometer`, `_rm3100_responds`, around lines 3126-3289) which
   `bringup` uses for its `--require-hardware` checks. **These are two
   parallel implementations.** Read both. The screen must report what
   `bringup` will actually decide, so drive the panel from the same source
   `bringup` uses; if that means moving `bin/smd`'s helpers into
   `lib/sigmond/hardware_detect.py` and having `bin/smd` import them, do that
   — but keep `bin/smd` stdlib-only (Global Constraint 2) and preserve
   current behaviour exactly. If the two implementations disagree in a way
   you cannot reconcile without changing behaviour, stop and report.
4. **TS-1 specifically:** the spec calls out the wizard's TS-1 probe flow.
   Find how TS-1 presence is determined (`grep -rn "TS-1\|TS1"` finds it in
   `screens/timing.py` and `app.py` today) and surface TS-1 detected /
   not-detected in the panel. Note from operator history: a fresh install
   does **not** arm the T6 fine stage (`t6_pps.enabled=false` in the image),
   so "TS-1 detected" and "T6 armed" are different facts — do not conflate
   them.
5. Keep the existing identity-once flow (reporter / grid / callsign /
   PSWS station id) and the `--non-interactive` invocation shape.

Verification: hermetic tests extending the greenfield coverage in
`tests/test_tui_navigation.py` or a new `tests/test_tui_greenfield.py` —
mock `detect_all`/the bringup helpers and the readiness call; assert the
panel renders each Presence state and that the bringup argv is unchanged
from today's `[smd, "bringup", <profile>, "--non-interactive"]` shape.

## Task 7 — regenerate the inventory doc

Phase 4 of the spec: "Regenerated inventory doc committed as the new IA
record."

Rewrite `docs/TUI-FUNCTION-INVENTORY.md` against HEAD-after-Task-6. It is
currently a snapshot of `01d6bb7` (2026-05-24) and is the stale artifact that
made the drift invisible. The new version must:

1. State its source commit, as the old one did.
2. Carry the full CLI verb surface from the live argparse tree, not a
   hand-maintained table. the workspace `verbs.tsv`
   has 138 verbs extracted mechanically — but regenerate it after Task 6 in
   case `bin/smd` changed, and say in the doc how to regenerate it.
3. Carry the post-prune screen list and nav tree.
4. Carry the screen ↔ verb mapping — pointing at `SCREEN_VERBS` (Task 3) as
   the machine-checked source of truth, so the doc cannot silently rot again.
5. Record the keep/fix/kill rulings and, explicitly, the verb families that
   are **deliberately CLI-only** with no TUI counterpart: `admin secrets`,
   `admin personalize`, `admin capture-prep`, `admin uploader manifest`,
   `notify`, `config catalog-prune`, `config location`, `config render`,
   `admin storage`, `admin completion`. The spec's decision is "no 1:1 verb
   parity; rare/expert verbs stay CLI-only and TUI help names them" — so this
   list is a deliverable, not an omission.
6. Record the two flagged-for-walkthrough screens (`radiod.py`,
   `receiver_channels.py`) and the landing-screen `except`-branch note from
   the rulings above, so the next session inherits the open questions.

Verification: the doc's screen list matches `ls lib/sigmond/tui/screens/`
exactly; every verb it names appears in the regenerated `verbs.tsv`.
