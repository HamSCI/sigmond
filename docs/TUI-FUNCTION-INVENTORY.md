# sigmond function inventory + TUI alignment audit

Snapshot of every capability sigmond exposes, mapped to the CLI verbs
that drive it and the TUI screens that surface it.

**This document is a generated artifact, not a source of truth.** The
previous version (a snapshot of commit `01d6bb7`, 2026-05-24) sat
unregenerated for months while `bin/smd` grew ~20 commits of verb
changes and the TUI's screen list diverged from it — nobody noticed
because nothing mechanically checked doc vs. reality. That drift is
what the `TUI-RECONCILIATION` plan (`.superpowers/sdd/plan-tui-reconciliation/`)
fixed. Two structural changes make the same drift harder to repeat
silently this time:

1. **`tests/test_tui_parity.py`** (added in that plan's Task 3) fails
   CI the moment a screen loses its `SCREEN_VERBS` entry, a mapping
   entry names a verb that no longer resolves against the live
   `bin/smd` argparse tree, or the nav tree's leaves and dispatch
   branches stop being a perfect bijection. The CLI-verb ↔ TUI-screen
   correspondence is now enforced by a test, not by this document.
2. **This document still needs a human to re-run it** after any
   `bin/smd` or screen-set change — the parity test catches
   *inconsistency*, not *staleness of this file's prose*. See §0 for
   the regeneration recipe. Do not hand-edit the verb tables or the
   screen list without re-running the extraction; every number below
   was checked against HEAD at the commit in §1, not carried forward
   from memory.

---

## 0. How to regenerate this document

1. **CLI verb surface** — walk the live `bin/smd` argparse tree
   (never execute a subcommand). The exact recipe already lives in
   the repo as `tests/test_tui_parity.py::_live_verb_paths()` — reuse
   that function rather than re-deriving the argparse-patching trick.
   A standalone dump script that does the same walk and prints
   `path<TAB>help` for every node is checked in as
   `.superpowers/sdd/TUI-RECONCILIATION-DESIGN/verbs.tsv`'s generator;
   regenerate with:

   ```
   .venv/bin/python - <<'PY'
   import argparse, importlib.util, importlib.machinery, sys, io, contextlib
   sys.argv = ['smd']
   class Caught(Exception):
       def __init__(self, parser): self.parser = parser
   argparse.ArgumentParser.parse_args = lambda self, *a, **k: (_ for _ in ()).throw(Caught(self))
   loader = importlib.machinery.SourceFileLoader('smd_probe', 'bin/smd')
   spec = importlib.util.spec_from_loader('smd_probe', loader)
   m = importlib.util.module_from_spec(spec)
   root = None
   try:
       with contextlib.redirect_stdout(io.StringIO()):
           loader.exec_module(m)
           m.main()
   except Caught as c:
       root = c.parser
   def walk(p, path):
       for act in p._actions:
           if not isinstance(act, argparse._SubParsersAction):
               continue
           seen = {}
           for name, sp in act.choices.items():
               seen.setdefault(id(sp), (sp, []))[1].append(name)
           for sp, names in seen.values():
               full = path + [names[0]]
               help_ = next((ca.help or '' for ca in act._choices_actions if ca.dest == names[0]), '')
               print('\t'.join(['smd ' + ' '.join(full), help_]))
               walk(sp, full)
   walk(root, [])
   PY
   ```

   Diff the output against `.superpowers/sdd/TUI-RECONCILIATION-DESIGN/verbs.tsv`;
   if it differs, `bin/smd`'s verb tree changed and §2 below needs a
   refresh.

2. **TUI screen list** — `ls lib/sigmond/tui/screens/*.py`, excluding
   `__init__.py`. Cross-check every module name appears as a key in
   `lib/sigmond/tui/parity.SCREEN_VERBS` (`tests/test_tui_parity.py`
   already asserts this both directions in CI).

3. **Nav tree** — read `lib/sigmond/tui/widgets/component_tree.py`'s
   `populate()` (leaves) and `on_tree_node_selected()` (dispatch
   branches) directly; don't trust this doc's copy of it.

4. **Screen ↔ verb mapping** — read `lib/sigmond/tui/parity.py`'s
   `SCREEN_VERBS` dict directly; §4 below is a snapshot of it for
   readability, not the source of truth.

---

## 1. Source state

Regenerated against commit `b5dc3cd` (2026-08-11), the tip of the
`TUI-RECONCILIATION` plan branch (`main`, ahead of `origin/main` by 12
commits at the time of writing). Verified against a HEAD checkout, not
inferred from commit messages.

---

## 2. CLI verb surface (`bin/smd`)

138 verb paths, extracted mechanically (§0 recipe) and byte-identical
to the tracked `.superpowers/sdd/TUI-RECONCILIATION-DESIGN/verbs.tsv` —
`bin/smd`'s verb tree did not change across Tasks 6–7 of this plan.
Full detail (138 rows, one per node, with help text) lives in that
file; the tables below are the top-level shape.

### Top-level (18 verbs)

| Verb | Notes |
|---|---|
| `admin` | umbrella for diagnostic / maintenance / rare verbs (see below) |
| `apply` | reconcile services with topology/coordination |
| `bringup` | guided station bring-up (`--profile <name>`, default `dasi2`) |
| `component` | catalog ops: `list / install / update / add / remove / enable / disable` |
| `config` | inspect/migrate coordination config (subverbs below) |
| `disable` | disable component(s), stop their units |
| `enable` | enable component(s) in topology.toml |
| `install` | install and configure components (same behaviour as `component install`) |
| `notify` | site fault notifier (`run / test / status / list`) |
| `psws` | PSWS enrollment (`status / enroll / verify / motd`) |
| `reload` | reload managed services (signal or restart) |
| `restart` | restart managed services |
| `start` | start managed services |
| `status` | show service health |
| `stop` | stop managed services |
| `timing` | show timing authority (tier/offset/sigma) + system-clock accuracy |
| `tui` | launch this TUI |
| `watch` | unified live-tail: `wspr / psk / hfdl / codar / superdarn / meteor / hf-tec / mag / ka9q / radiod / gpsdo / uploads / verifier` |

`_SMD_MUTATING_VERBS` / `_MUTATING` in `bin/smd` currently list exactly
`{install, apply, start, stop, restart, reload}` — no dead entries as
of this snapshot (the earlier doc's note about a stale `'update'`
entry no longer applies; verify against `bin/smd` directly if this
matters to you, since `_MUTATING` is not covered by the parity test).

### `admin` umbrella (`smd admin <subverb>`)

| Subverb | Sub-subverbs |
|---|---|
| `capture-prep` | — (plan-first, `--yes` to execute) |
| `completion` | `bash` |
| `diag` | `cpu-affinity / cpu-freq / net / drop-in` |
| `environment` | `list / probe / describe` |
| `instance` | `list / show / add / remove / edit / enable / disable / migrate` |
| `log` | (`set-level` handled by a pre-argparse intercept — see §4 note) |
| `personalize` | — (plan-first, `--yes` to execute) |
| `public-ip` | — |
| `rac` | `status / start / stop / restart / register / install / configure / reconfigure` |
| `radiod` | `migrate` |
| `readiness` | — (bring-up/station fitness gate) |
| `secrets` | `status / template / install / bundle` |
| `sources` | `list / add / remove / apply` |
| `storage` | `migrate-to-sqlite / trim / tune-timestd` |
| `timing` | — (reconciler: GPSDO/gpsd/chrony/hf-timestd) |
| `uninstall` | — (plan-first, `--yes` to execute) |
| `uploader` | `manifest` |
| `validate` | — |
| `verifier` | `report / rehabilitate` |
| `wisdom` | `plan / status` |

### `config` subverbs

`show / identity / location / refresh / render / upload /
catalog-prune / migrate / backup / restore / init /
register-radiod / edit / hf-timestd {status,validate,edit} /
mag-recorder {status,validate,edit}`

---

## 3. TUI screen surface (`lib/sigmond/tui/screens/`)

**30 screen modules** (`ls lib/sigmond/tui/screens/*.py`, minus
`__init__.py` — verified by directory listing, not carried over from
a prior count).

### What changed this plan

- **Pruned 8** (Task 2, commit `2d16a1e`): `kiwisdr`, `fft_wisdom`,
  `sources`, `placeholder`, `topology`, `client_config`, `instance`,
  `config_show`. Ruled dead or superseded by the `configuration`
  screen's consolidation. `lib/sigmond/topology.py`,
  `lib/sigmond/instance.py`, and `lib/sigmond/commands/client_config.py`
  are unrelated **library** modules that share names with three of the
  deleted screens and were **not** touched.
- **Added 1** (Task 5, commit `540c6a6`): `psws` — PSWS enrolment arc
  + per-instance upload toggles, previously CLI-only.
- Net this plan: 37 → 30. The 37 is the count immediately before
  Task 2's prune (`git ls-tree 2d16a1e~1`); eleven screens were added
  between the old doc's source commit and this plan (`activity`,
  `annotation_quality`, `configuration`, `greenfield`, `instance`,
  `receiver_channels`, `resources`, `sources`, `textual_wizard`,
  `timing_authority`, `verifier` — `git diff --name-only 01d6bb7
  2d16a1e~1 -- lib/sigmond/tui/screens/`), which is most of why the
  old doc drifted so far: normal feature work kept landing against a
  doc nobody was regenerating, including one of the very screens
  (`instance`) that this plan later pruned as superseded. For the
  record, the old doc's own claim of "31 screen modules" at its
  stated source commit `01d6bb7` doesn't even match: `git ls-tree
  01d6bb7` shows 26. The count was already wrong the day it was
  written — one more reason not to trust an inherited number without
  recounting it (§0.2).

### Screen → role

| Screen module | Action | One-line role |
|---|---|---|
| `activity` | `action_show_activity` | Live tail of `smd watch <target>`; one screen, target selector, covers wspr/psk/hfdl/codar/superdarn/meteor/hf-tec/mag/ka9q/uploads/verifier |
| `annotation_quality` | `action_show_annotation_quality` | Per-consumer science verdict: each running recorder's attached σ/tier + green/yellow/red threshold |
| `apply` | `action_show_apply` | Reconcile services with topology/coordination (`smd apply`); dry-run pane + confirm-gated apply |
| `authority` | `action_show_authority` | Substrate view of `authority.json` (active tier, σ, witnesses); composed into `timing_authority`, not a nav-tree leaf on its own |
| `backup` | `action_show_backup` | Snapshot all config to `sigmond-config-*.tar.gz` |
| `components` | `action_show_components` | Catalog: install status, git ref, version policy per component |
| `configuration` | `action_show_configuration` | Instance-centric consolidation of the former Instance/Client-config/Config-view screens: list/add/remove/edit per-reporter instances |
| `cpu_affinity` | `action_show_cpu_affinity` | Hardware topology + affinity plan + observed state + confirm-gated Apply |
| `cpu_freq` | `action_show_cpu_freq` | Per-CPU `scaling_max_freq` vs `[cpu_freq]` policy + confirm-gated Apply |
| `diag_net` | `action_show_diag_net` | IGMP classification for multicast safety |
| `environment` | `action_show_environment` | Declared vs. observed peers (mDNS / ka9q / NTP / KiwiSDR / GPSDO) |
| `gpsdo` | `action_show_gpsdo` | Live GPSDO status: coordinator view + per-device deep dive |
| `greenfield` | `action_show_greenfield` | Guided, CLI-free bring-up wizard over `smd bringup`; now also renders catalog-sourced profile descriptions, a hardware-gate equipment panel (`sigmond.hardware.gate_checks`), and the before/after `admin readiness` verdict |
| `install` | `action_show_install` | Catalog install picker (single / all-missing) |
| `ka9q_watch` | `action_show_ka9q_watch` | Compare pinned ka9q-radio commit vs `origin/main` |
| `lifecycle` | `action_show_lifecycle` | Multi-select start/stop/restart across lifecycle-managed instances |
| `logs` | `action_show_logs` | Follow journal or tail file-logs per component; log-level mutation gated by confirm modal |
| `overview` | `action_show_overview` | Landing dashboard: service health, client inventory, CPU-affinity summary, **and (new this plan) a Timing section** — T-level verdict, offset ± σ, governor, snapshot age, chrony reference — sharing `_tier_colour`/`read_authority_snapshot` with Authority/Annotation Quality/`smd timing` so none of the four can disagree |
| `psws` | `action_show_psws` | **New.** PSWS enrolment arc (status/enroll/verify) + per-instance upload on/off, previously CLI-only |
| `rac` | `action_show_rac` | Configure/monitor the frpc reverse tunnel to the WD gateway |
| `radiod` | `action_show_radiod` | Live ka9q-python status: LAN-wide radiod discovery → per-radiod → per-SSRC drill-down |
| `receiver_channels` | `action_show_receiver_channels` | Per-client view of which live radiod channels an instance is actually consuming |
| `resources` | `action_show_resources` | System + sigmond storage summary (memory/load/uptime, filesystem capacity) |
| `restore` | `action_show_restore` | Browse + extract a config backup over the live system |
| `sdr_inventory` | `action_show_sdr_inventory` | Unified USB SDR / KiwiSDR / ka9q-frontend inventory + labelling |
| `textual_wizard` | (opened from `configuration`) | In-TUI Textual renderer for a client's `config show/apply` JSON contract; not a nav-tree leaf, reached only via the Configuration screen |
| `timing` | `action_show_timing` | Chrony-facade view: source comparison vs HPPS, root dispersion; composed into `timing_authority`, not its own nav-tree leaf |
| `timing_authority` | `action_show_timing_authority` | Composes `AuthorityScreen` (substrate) above `TimingScreen` (chrony facade) for one "is timing healthy?" surface |
| `validate` | `action_show_validate` | Cross-client harmonization rules |
| `verifier` | `action_show_verifier` | wsprnet upload audit (`verifier report`) + per-callsign suppression clear (`verifier rehabilitate`) on one screen |

Three modules — `authority`, `timing`, `textual_wizard` — have
`action_show_*` methods and are directly tested, but are **not**
nav-tree leaves in their own right: `authority` and `timing` are
composed into `timing_authority` (their standalone
`on_tree_node_selected` dispatch branches were deliberately removed in
the Task 2 prune, commit `2d16a1e`, as dead code the tree never
emitted), and `textual_wizard` is opened only as a sub-view from the
`configuration` screen. This is intentional, not a bijection gap — the
nav-tree bijection test in `test_tui_parity.py` only checks
`component_tree.py`'s own leaves against its own dispatch branches,
and both of those stay silent about these three by design.

### Nav tree (5 groups + the Overview root leaf)

Read from `lib/sigmond/tui/widgets/component_tree.py::populate()`
(27 leaves total: 1 root + 5+9+3+5+4 across the groups below):

```
▣ Overview                         [root leaf — landing]

Installation                       [first-time setup, rarely revisited]
    ✨ Guided bring-up             greenfield
    ① Download & install          install
    ② Configure                   configuration
    ③ Enable / start / stop       lifecycle
    ✒ PSWS enrolment              psws

Monitoring                         [day-to-day "is it working"]
    ⌖ Environment                  environment
    ⽵ Timing & Authority          timing_authority
    ⊙ Annotation Quality          annotation_quality
    ⚡ Activity                    activity
    ◐ GPSDO live                  gpsdo
    ◉ ka9q-radio live             radiod
    ⌖ Receiver channels           receiver_channels
    ⇆ RAC tunnel                  rac
    ⬢ Resources                   resources

Maintenance                        [routine changes]
    ⇄ Apply                       apply
    ↓ Backup                      backup
    ↑ Restore                     restore

Debugging                          [diagnose when something looks wrong]
    ≡ Logs                        logs
    ⚒ Verifier                    verifier
    ✔ Validate                    validate
    ✦ Diag: net                   diag_net
    ◎ ka9q-watch                  ka9q_watch

Advanced                           [rare, under-the-hood knobs]
    ⚑ Software versions           components
    ⊞ SDR inventory               sdr_inventory
    ⚙ CPU affinity                cpu_affinity
    ⇵ CPU frequency               cpu_freq
```

Components do not appear as top-level nav entries; they show up
inside screens (Overview rollup, Radiod live, Lifecycle, Logs) — per
the module docstring in `component_tree.py`.

---

## 4. Screen ↔ verb mapping

**Source of truth: `SCREEN_VERBS` in `lib/sigmond/tui/parity.py`.**
`tests/test_tui_parity.py` asserts, on every test run, that (a) every
screen module has an entry, (b) every entry names a real screen
module, and (c) every declared verb still resolves against the live
`bin/smd` argparse tree. If the table below and that module ever
disagree, the module is right and this table is stale — regenerate
(§0.4).

| Screen | Verb(s) | Screen | Verb(s) |
|---|---|---|---|
| `activity` | `watch` | `overview` | `status`, `timing` |
| `annotation_quality` | *(none — TUI-only)* | `psws` | `psws status`, `psws enroll`, `psws verify`, `config register-radiod`, `config upload` |
| `apply` | `apply` | `rac` | `admin rac` |
| `authority` | *(none — composed view)* | `radiod` | *(none — TUI-only)* |
| `backup` | `config backup` | `receiver_channels` | *(none — TUI-only)* |
| `components` | `component list`, `install`, `component update` | `resources` | *(none — TUI-only)* |
| `configuration` | `config edit`, `admin instance add`, `admin instance remove`, `admin instance migrate` | `restore` | `config restore` |
| `cpu_affinity` | `admin diag cpu-affinity` | `sdr_inventory` | `config register-radiod` |
| `cpu_freq` | `admin diag cpu-freq` | `textual_wizard` | *(none — TUI-only)* |
| `diag_net` | `admin diag net` | `timing` | `timing` |
| `environment` | `admin environment list`, `admin environment probe`, `admin environment describe` | `timing_authority` | *(none — composed view)* |
| `gpsdo` | `watch gpsdo` | `validate` | `admin validate` |
| `greenfield` | `bringup`, `admin readiness`, `config edit` | `verifier` | `admin verifier report`, `admin verifier rehabilitate` |
| `install` | `install` | | |
| `ka9q_watch` | `watch ka9q` | | |
| `lifecycle` | `start`, `stop`, `restart` | | |
| `logs` | `admin log` *(see note)* | | |

**`logs` note:** this is coarser than the screen's real behaviour.
`admin log set-level` is handled by a pre-argparse `sys.argv`
intercept near the top of `bin/smd`'s `main()` — matched before the
argparse tree is even built (so it can accept both `set-level <level>`
and `set-level <client> <level>`) — not an argparse subparser. The
lint's parser walk only sees `ArgumentParser`/`_SubParsersAction`
objects, so it can never observe `admin log set-level` as a live path.
`admin log` (the journal/file-tail verb, a real subparser) is the
closest resolvable verb and stands in for the whole screen. This is
documented in `parity.py` itself, at the mapping entry.

`None` in `SCREEN_VERBS` marks a screen with genuinely no `smd` CLI
counterpart: a pure TUI-only surface (live radiod control-plane views,
in-memory dashboards, or a screen composed purely of other screens).

---

## 5. Verb families deliberately CLI-only

The plan's governing decision: **no 1:1 verb parity.** The TUI is a
curated operator console, not a GUI shell over every `smd` verb; rare
or expert verbs stay CLI-only and TUI help text names them where
relevant. This is a deliberate scope boundary, not an oversight — each
entry below was checked against the live verb tree (§2/§0) before
listing:

| Verb family | Why CLI-only |
|---|---|
| `admin secrets {status,template,install,bundle}` | Delivered-secret plumbing (Earthdata netrc, RAC token) — install-time, root-only, not a repeated operator action |
| `admin personalize` | First-boot re-identification of a cloned golden image — a one-shot provisioning step, plan-first/`--yes` |
| `admin capture-prep` | Inverse of `personalize`: strip identity before golden-image capture — an image-building step, not a station operation |
| `admin uploader manifest` | Renders the hs-uploader pipeline manifest from per-client `deploy.toml` — regenerated automatically by `apply`; direct invocation is a debugging/dev action |
| `notify {run,test,status,list}` | Site fault notifier — timer-invoked in normal operation; `test`/manual `run` are debugging actions |
| `config catalog-prune` | Trims `/etc/sigmond/catalog.toml` to only entries diverging from the in-repo catalog — maintenance-of-config-hygiene, rare |
| `config location` | Derives station location from the GPSDO GPS fix — one-shot at bring-up, folded into the bring-up flow rather than a standing screen |
| `config render` | Renders `coordination.toml`/`.env` from `site-profile.toml` — an internal regeneration step, not an operator-facing action |
| `admin storage {migrate-to-sqlite,trim,tune-timestd}` | Storage-backend janitor work — one-shot migration or timer-driven trim, not a routine console action |
| `admin completion bash` | Shell tab-completion emitter — meaningless inside a TUI |

---

## 6. `hardware_detect.py` deletion

`lib/sigmond/hardware_detect.py` was **deleted** in this plan
(commit `ead4ee4`) and its role folded into `lib/sigmond/hardware.py`.
Worth recording plainly: it probed USB vendor ID `1d50` for the
station's GPSDO, but the real Leo Bodnar GPSDO (LBE-1420/1421/mini,
the device sigmond actually ships with) is VID `1dd2` — confirmed
against `bin/smd`'s own `_gpsdo_fix_direct` udev match, which is what
`hardware.py` uses. `hardware_detect.py` could never have detected the
station's GPSDO. It had no live caller (its only caller,
`screens/topology.py`, was itself deleted in Task 2) and no tests. It
was deleted outright rather than merged, because merging a probe table
that cannot see the shipped GPSDO into the bring-up hardware gate
would silently change what `bringup --require-hardware` hard-stops on
— and that gate is safety-relevant.

---

## 7. Open questions inherited by the next session

Recorded here so they are not lost between plan sessions — none of
these are resolved by this doc; they are decisions or investigations
for whoever picks this up next.

1. **`radiod.py` (883 lines) and `receiver_channels.py`** sit on the
   boundary the plan drew between "TUI = control" and "web = depth"
   (ka9q-web is meant to own per-host receiver detail). Both screens
   were kept — neither was on the spec's kill-default list, and both
   serve the developer/debugging persona — but they were flagged for
   an operator walkthrough decision rather than killed. Next session:
   watch an actual operator use (or not use) these screens before
   deciding whether they stay, shrink, or get replaced by a link out
   to ka9q-web.
2. **Landing-screen failure path.** `app.py`'s `on_mount()` computes
   `greenfield = not self.topology.enabled_components()` inside a
   `try`, and the `except` branch sets `greenfield = False` — so a
   topology **load failure** lands the operator on Overview rather
   than Guided bring-up. This is deliberate (Overview is judged safer
   for a configured station with a corrupt topology file than dropping
   into a bring-up wizard), but it was a judgment call, not something
   derived from a test, and is worth revisiting if it ever surprises
   an operator.
3. **`logs` screen's coarser mapping** — see §4's note. `admin log
   set-level` is a pre-argparse `sys.argv` intercept in `bin/smd`,
   structurally invisible to any argparse-tree walk (including the
   parity lint), so `SCREEN_VERBS["logs"]` can only ever point at the
   coarser `admin log`. Not fixable without either restructuring
   `bin/smd`'s argument handling or teaching the lint a special case.
4. **Two hand-synced SDR device tables.** `lib/sigmond/hardware.py`'s
   `_sdr_present()` regex and
   `lib/sigmond/discovery/usb_sdr.py::KNOWN_SDR_DEVICES` are two
   independent tables of USB VID:PID → SDR type. `usb_sdr.py` already
   carries a comment noting they'd drifted once (it lacked `00f0`,
   `hardware.py`'s regex lacked `00bc`, since resynced). Nothing
   currently keeps them mechanically in sync — a repeat drift is
   possible. Candidate follow-up: derive one from the other, or add a
   parity test analogous to `test_tui_parity.py`.
5. **Guided bring-up does not elevate privileges** where `bin/smd`
   does. The equipment panel's probes (lsusb, GPSDO NMEA sample,
   RM3100 bus poke) run as whatever the TUI process is running as; an
   unprivileged operator may see a GPSDO probe fail ("no fix" / not
   found) that an elevated `smd bringup` run — which does the same
   probe with root — would see fine. Not yet reconciled; the panel
   should either elevate the same way `bringup` does, or say
   explicitly when a negative result might be a privilege artifact
   rather than a real hardware absence.

---

## 8. Verification performed for this snapshot

- **Screen list**: `ls lib/sigmond/tui/screens/*.py` (minus
  `__init__.py`) diffed directly against every key in
  `lib/sigmond/tui/parity.SCREEN_VERBS` and against every row in §3's
  table — 30 in, 30 accounted for, both directions. This is the same
  check `tests/test_tui_parity.py::test_every_screen_module_has_a_mapping_entry`
  and `::test_every_mapping_entry_names_an_existing_screen_module` run
  in CI.
- **Verb list**: re-ran the §0 argparse-walk recipe against HEAD
  `b5dc3cd`, got 138 verb paths, and `diff`'d the output byte-for-byte
  against the tracked `.superpowers/sdd/TUI-RECONCILIATION-DESIGN/verbs.tsv`
  — identical, so no drift since that file was generated. Every verb
  named in §2 and §5 of this document was grepped out of that fresh
  138-line output before being written down (not typed from memory).
- **Full test suite**: `.venv/bin/python -m pytest tests/ -q` — 1266
  passed, 0 failed, before and after this doc-only change (this file
  and the one `CLAUDE.md` line noted below are not imported by
  anything the suite runs).
