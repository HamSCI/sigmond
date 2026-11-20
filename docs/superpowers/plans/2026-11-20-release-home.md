# Release home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** sigmond holds the version: a list file `release/components.toml` that the image build installs and checks, align reading Releases from HamSCI/sigmond, bless living in sigmond with a bare-metal install gate, and the image repo renamed sigmond-proxmox.

**Architecture:** One stdlib module, `sigmond.release_list`, owns the list format and every comparison; smd verbs, the rig's shell scripts (through `python3 -m sigmond.release_list`) and bless all call it. A second module, `sigmond.bare_metal_doc`, turns `docs/install-bare-metal.md` into the bare-metal test's script and judges its `smd check --json`, so the test and the bless gate read the doc the same way. align and pm-align read HamSCI/sigmond first and fall back, on a 404 alone, to the proxmox repo.

**Tech Stack:** Python 3.11 stdlib (`tomllib`, `urllib`, `subprocess`), pytest via `.venv/bin/pytest`; bash for the rig scripts and their hermetic `test-*.sh`.

**Spec:** `docs/superpowers/specs/2026-11-20-release-home-design.md` (binding). Read it whole before any task, including "Calls from the builder check" and "The bare-metal pass rule".

## Global Constraints

- smd core stays stdlib-only, prints ASCII, refuses sudo. New modules import nothing outside the stdlib.
- On-station paths stay: `/etc/sigmond-appliance/{version,manifest.txt,aligned.json,host-aligned.json,rac-mode,golden-image}`, `/root/sigmond-appliance/`, the image's FQDN `sigmond-appliance-<vtag>.local`. New code keeps writing them.
- The manifest keeps today's format, including `image_version`, `appliance_commit`, `appliance_tag`, `wizard_commit` and the `components (live):` block.
- `components.toml` rows hold full 40-character commits; sigmond has no row; sigmond-proxmox has one.
- Native pins have one owner each: onion, wsjtx, ubersdr_wsprd = `_ONION_COMMIT`, `_WSJTX_COMMIT`, `_UBERSDR_COMMIT` in `bin/smd`; ka9q-radio = ka9q-python's `ka9q_radio_compat` at the listed ka9q-python commit; ft8_lib = `FT8LIB_PIN` in psk-recorder's `scripts/install.sh` at the listed psk-recorder commit. `--write` copies them and never rewrites an owner.
- Image file stem: `sigmond-<version>-<YYYYMMDD>-release` (was `sigmond-appliance-...`). Readers of image names accept both stems.
- Slugs: `HamSCI/sigmond` (releases, tags), `HamSCI/sigmond-proxmox` (image repo, mirror, host files). Until the mirror ends, align and pm-align also try `HamSCI/sigmond-appliance` when `HamSCI/sigmond-proxmox` answers 404, so each commit works on either side of the rename.
- Prose that names the system says "sigmond"; the component is "the sigmond manager" or `smd`.
- Each repo: develop on main; commits stay local; stage named files only (never `git add -A` or `-a`); never stash. Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z
  ```
- sigmond tests: from `/home/mjh/hamsci/repos/sigmond`, `.venv/bin/pytest -q -p no:cacheprovider --basetemp=$HOME/.cache/pytest-tmp/rh-<task> <files>`. Five TUI tests (test_tui_psws, test_tui_mutation, test_tui_cpu_affinity) fail before this work; leave them.
- sigmond-proxmox: the local checkout keeps its directory name `/home/mjh/hamsci/repos/sigmond-appliance` until the operator steps. Its tests are `bash test-*.sh`, hermetic, no network.
- No subagent contacts a remote host, pushes, tags, or renames anything. Those belong to "Operator steps".

## Review Focus

1. **A transient failure on HamSCI/sigmond during `smd align`.** Anything but a 404 must refuse. A fallback to the proxmox repo on a timeout would aim a v3.94 station at v3.93's latest, a silent rollback target. (Task 5: `test_network_error_on_sigmond_does_not_fall_back`; Task 6 the same for pm-align.)
2. **A rate-limited API while HamSCI/sigmond has no Release yet.** The web page `/releases/latest` then lands on `/releases`, not `/releases/tag/vN`; that must count as "not found" and fall back, not refuse. (Task 5: `test_web_path_no_release_on_sigmond_falls_back`.)
3. **`smd install --release-list` on a machine whose checkout is dirty or sits ahead of its row** (a dev box, a station someone edited). It must refuse before it changes anything, never reset the work. (Task 3: `test_dirty_checkout_refuses_before_any_change`.)
4. **A radiod that failed, as against one that waits.** With the SDR absent and radiod `failed`, or the SDR present and radiod stopped, the consumers keep their FAIL. "Waits on dormant radiod" applies only when radiod is installed, not running, not failed, and no SDR sits on the bus. (Task 4: `test_failed_radiod_still_fails`, `test_sdr_present_is_not_dormant`.)
5. **A flasher a test site already holds.** It knows only the `sigmond-appliance-*` stem. The new flasher must accept both stems, and its `FLASH_VERSION` must rise so held copies replace themselves. (Task 10: `test-flash-latest.sh` scenes `both stems` and `version bumped`.)

## Shared contracts

These formats cross tasks. Each task's Interfaces block names the ones it uses.

**C1. The list, `release/components.toml`:**
```toml
# sigmond release list -- written by `smd admin release-list --write`, reviewed, committed.
# The tag vN on HamSCI/sigmond names sigmond's own commit; sigmond has no row here.
version = "v3.94"

[components]
callhash = "<40 hex>"
# ... one row per name in release_list.ROWS, sorted
```
`release_list.ROWS` (today's manifest rows, sigmond excluded, sigmond-proxmox added):
`callhash codar-sounder ft8_lib gmag-webui gpsdo-monitor hamsci-dsp hamsci-physics hf-tec hf-timestd hfdl-recorder hs-uploader igmp-querier ka9q-python ka9q-radio ka9q-web mag-recorder meteor-scatter onion psk-recorder sigmond-proxmox sigmond-rac station-web superdarn-sounder ubersdr_wsprd wsjtx wspr-recorder`.

**C2. The bare-metal record**, one file per run, `$RIG_DIR/bare-metal/<version>-<YYYYmmddTHHMMSSZ>.record`, `key: value` lines:
```
bare_metal_record: 1
version: v3.94
sigmond_commit: <40 hex>
doc_commands_sha256: <64 hex>
release_list_check: PASS|FAIL
smd_check_exit: <int>
radiod_consumers: PASS|FAIL
result: PASS|FAIL
finished_utc: 2026-11-21T12:00:00Z
```
`result: PASS` only when `release_list_check`, `radiod_consumers` are PASS and `smd_check_exit` is 0 or 2.

**C3. Image names:** `sigmond-<version>-<YYYYMMDD>-release.img`, `.sha256`, `.manifest.txt`, `.INSTALL.md`, `.INSTALL.html`, `.CHANGELOG.md`.

**C4. The golden VM's sigmond commit** goes into `$RIG_ROOT/v3/golden-v3-sigmond-commit.txt` (one full commit, newline), written beside `golden-v3-revs.txt` by `build-golden-vm.sh`.

---

### Task 1: `sigmond.release_list`, the list's one owner (sigmond)

**Files:**
- Create: `lib/sigmond/release_list.py`
- Test: `tests/test_release_list.py`

**Interfaces:**
- Produces:
  - `class ReleaseListError(ValueError)`
  - `@dataclass(frozen=True) class ReleaseList: version: str; components: dict` (name -> 40-hex)
  - `@dataclass(frozen=True) class Mismatch: component: str; want: Optional[str]; have: Optional[str]; note: str` (`want` None = unlisted, `have` None = absent)
  - `ROWS: tuple[str, ...]` (C1), `PROXMOX_ROW = "sigmond-proxmox"`, `NATIVE_CONSTANTS = {"onion": "_ONION_COMMIT", "wsjtx": "_WSJTX_COMMIT", "ubersdr_wsprd": "_UBERSDR_COMMIT"}`
  - `parse_list(text: str) -> ReleaseList`, `load_list(path) -> ReleaseList`, `format_list(rl: ReleaseList) -> str`
  - `native_constants(smd_text: str) -> dict` (regex `^_ONION_COMMIT\s*=\s*'([0-9a-f]{40})'` per name; missing -> ReleaseListError)
  - `ka9q_radio_from_compat(text: str) -> str` (first non-blank line not starting with `#`; must be 40-hex)
  - `ft8lib_from_install_sh(text: str) -> str` (`^FT8LIB_PIN="([0-9a-f]{40})"`)
  - `compare_live(rl, live: dict, *, require_all: bool = False) -> list[Mismatch]`
  - `compare_manifest(rl, manifest_text: str) -> list[Mismatch]`
  - `main(argv: list[str]) -> int`: `python3 -m sigmond.release_list version --list FILE|-` prints the version; `check --list FILE|- --manifest FILE` prints one ASCII line per mismatch and exits 0 equal, 1 differ, 2 unreadable.

Rules `parse_list` enforces (each a ReleaseListError naming the row): `version` matches `align.RELEASE_TAG_RE`; `[components]` present; every name matches `align.COMPONENT_NAME_RE`; every value is exactly 40 lowercase hex; no `sigmond` row; a `sigmond-proxmox` row present. `compare_live` skips `sigmond` and `sigmond-proxmox`, compares by `doctor._sha_equal`, reports a listed row with no checkout as absent only when `require_all`, and always reports a live checkout missing from the list (except `sigmond`) as unlisted. `compare_manifest` parses with `doctor._parse_manifest_components`, drops the `sigmond` row, maps the `appliance_commit` preamble to `sigmond-proxmox`, and demands equality both ways.

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_round_trip():            # format_list(parse_list(t)) == t for a fixture with every ROWS name
def test_abbreviated_sha_refuses():     # "callhash = 'adc9d9d'" -> ReleaseListError, message names callhash
def test_sigmond_row_refuses():         # message contains "sigmond has no row"
def test_missing_proxmox_row_refuses():
def test_bad_version_refuses():         # version = "3.94" and "v3.94-rc1"
def test_native_constants_reads_bin_smd():
    text = (REPO / "bin" / "smd").read_text()
    assert native_constants(text) == {"onion": <_ONION_COMMIT>, "wsjtx": <_WSJTX_COMMIT>,
                                      "ubersdr_wsprd": <_UBERSDR_COMMIT>}   # values read via regex on the same file in the test
def test_compat_skips_comments():       # "# x\n# y\naa52...47\n" -> "aa52...47"
def test_ft8lib_pin():                  # 'FT8LIB_PIN="0dfd...dec"   # note' -> "0dfd...dec"
def test_compare_live_equal_by_prefix():# live short shas equal to rows -> []
def test_compare_live_absent_only_with_require_all():
def test_compare_live_unlisted_checkout_reported():  # live has "chirp-recorder" -> Mismatch(want=None)
def test_compare_manifest_maps_appliance_commit():   # appliance_commit != proxmox row -> one Mismatch("sigmond-proxmox")
def test_compare_manifest_extra_row():  # manifest row not in list -> Mismatch(want=None)
def test_main_check_exit_codes(tmp_path):  # 0 equal, 1 differ, 2 unreadable list; output ASCII
def test_real_list_owners_agree():
    # Skips only when release/components.toml does not exist yet.
    # natives == native_constants(bin/smd); ka9q-radio and ft8_lib checked against
    # ../ka9q-python and ../psk-recorder via `git show <row>:<path>` when those sibling
    # checkouts exist (skip that half otherwise, as test_hamsci_sink_compat does).
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest -q -p no:cacheprovider --basetemp=$HOME/.cache/pytest-tmp/rh-1 tests/test_release_list.py`
Expected: FAIL, `ModuleNotFoundError: sigmond.release_list`

- [ ] **Step 3: Implement `lib/sigmond/release_list.py`** with the interfaces above. `format_list` writes the C1 header comment, `version`, a blank line, `[components]`, rows sorted by name, `name = "sha"`. `main` uses `argparse`; `--list -` reads stdin.

- [ ] **Step 4: Run the tests**

Run: same command. Expected: PASS (the owners test skips: no list yet).

- [ ] **Step 5: Commit**

```bash
git -C /home/mjh/hamsci/repos/sigmond add lib/sigmond/release_list.py tests/test_release_list.py
git -C /home/mjh/hamsci/repos/sigmond commit -m "release: the list's format and comparisons (release_list)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z"
```

---

### Task 2: `smd admin release-list --write | --check` (sigmond)

**Files:**
- Modify: `lib/sigmond/release_list.py` (add `compute_list`, `repo_urls`)
- Modify: `bin/smd` (parser under `admin_sub`, near `p_admin` at about line 32382; handler `cmd_admin_release_list`)
- Test: `tests/test_release_list_cmd.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `repo_urls(catalog: dict) -> dict` name -> clone URL for every ROWS name with a head of its own: catalog `.repo` for catalog names; `ka9q-python` -> `https://github.com/HamSCI/ka9q-python`; `sigmond-proxmox` -> `https://github.com/HamSCI/sigmond-proxmox`. Owner-derived rows (`onion wsjtx ubersdr_wsprd ka9q-radio ft8_lib`) have no entry.
  - `compute_list(version: str, *, urls: dict, smd_text: str, remote_head: Callable[[str], str], fetch_file: Callable[[str, str, str], str]) -> ReleaseList` where `fetch_file(repo_url, sha, path)` returns file text. Head rows from `remote_head(url)` ('' -> ReleaseListError naming the row); natives from `native_constants(smd_text)`; `ka9q-radio` from `fetch_file(ka9q-python url, row, "ka9q_radio_compat")`; `ft8_lib` from `fetch_file(psk-recorder url, row, "scripts/install.sh")`.
  - CLI: `smd admin release-list --write --version vN [--list PATH]` writes PATH (default `<sigmond checkout>/release/components.toml`, the checkout `bin/smd` sits in) and prints the rows; it never commits. `smd admin release-list --check [--list PATH] [--manifest FILE] [--base /opt/git/sigmond] [--require-all]` exits as `main` does. No root needed; refuse sudo as every verb does.
- The real `remote_head` is smd's `_remote_head`; the real `fetch_file` reads `https://raw.githubusercontent.com/<slug>/<sha>/<path>` through `align._get` (slug from `align.github_slug`).

- [ ] **Step 1: Write the failing tests**

```python
def test_compute_takes_heads_and_owners():
    rl = compute_list("v3.94", urls=FAKE_URLS, smd_text=SMD_FIXTURE,
                      remote_head=lambda u: HEADS[u], fetch_file=FAKE_FILES)
    assert rl.components["hf-timestd"] == HEADS[FAKE_URLS["hf-timestd"]]
    assert rl.components["onion"] == ONION_IN_FIXTURE
    assert rl.components["ka9q-radio"] == COMPAT_AT_KA9Q_PYTHON_ROW
    assert rl.components["ft8_lib"] == FT8_AT_PSK_ROW
    assert "sigmond" not in rl.components and set(rl.components) == set(ROWS)
def test_compute_unreachable_head_refuses():   # remote_head -> '' for one url -> ReleaseListError naming it
def test_write_then_check_round_trip(tmp_path):  # cmd writes, --check --manifest with matching rows -> 0
def test_check_live_against_fake_base(tmp_path): # git repos in tmp base at listed shas -> 0; one moved -> 1, line names it
def test_cli_output_is_ascii(tmp_path):
```
Drive the CLI with `subprocess.run([sys.executable, "bin/smd", "admin", "release-list", ...], env={"PYTHONPATH": "lib", ...})` for `--check`; test `--write` through `compute_list` plus a monkeypatched handler, never the network.

- [ ] **Step 2: Run them to see them fail** — `... rh-2 tests/test_release_list_cmd.py`; Expected: FAIL (`compute_list` undefined, unknown admin verb).

- [ ] **Step 3: Implement** `compute_list`, `repo_urls`, and `cmd_admin_release_list` in `bin/smd` (live mode reads HEADs with `provenance.component_versions(doctor.component_checkouts(base))`).

- [ ] **Step 4: Run the tests** — Expected: PASS. Also run `tests/test_release_list.py`.

- [ ] **Step 5: Commit** `lib/sigmond/release_list.py bin/smd tests/test_release_list_cmd.py` — "release: smd admin release-list --write and --check".

---

### Task 3: `smd install` and `smd bringup` take `--release-list PATH` (sigmond)

**Files:**
- Modify: `bin/smd` (`_install_full_suite` ~14632, `_install_single_client_run` ~14417, `_install_radiod_native` ~15030, install parser ~32559, bringup parser ~32571, `cmd_bringup` ~13250)
- Modify: `lib/sigmond/bringup.py` (`build_plan(..., release_list: Optional[str] = None)`)
- Test: `tests/test_install_release_list.py` (new; model its fakes on `tests/test_install_no_pull.py`), `tests/test_bringup.py` (add cases); `tests/test_bringup_plan_snapshot.py` must pass unchanged

**Interfaces:**
- Consumes: `release_list.load_list`, `ReleaseList`, `align_apply.write_pin`, `align_apply.resolve`, smd's `_align_chown`, `_native_constants`.
- Produces:
  - `_release_refs(path: str) -> ReleaseList` in `bin/smd`: loads the list or prints one ERROR line and returns None.
  - `_catalog_at_release(catalog: dict, rl: ReleaseList) -> dict`: a copy whose entries carry `pin = rl.components[name]` (`dataclasses.replace`) for every listed catalog name. The installer already clones at `entry.pin`, source-only deps included.
  - `_seat_listed_checkouts(rl, base: Path) -> list[str]` (errors): for every listed row whose checkout already exists under `base` and is not sigmond (ka9q-python, which sigmond's own `install.sh` clones at main, is the case that needs it): refuse when the checkout is dirty (tracked changes, `.pin` excepted), otherwise fetch and check out the row detached, as the checkout's owner.
  - `_write_release_pins(rl, base: Path) -> list[str]` (errors): for each listed row whose checkout exists and is not in `_native_build_pins()`: HEAD must equal the row, then `write_pin` and chown as `_align_install_missing` does. HEAD differing is an error.
  - `smd install [--components X] --release-list PATH` and `smd bringup ... --release-list PATH`. bringup resolves PATH to absolute, loads it before planning (exit 1 on error), and appends `--release-list <abs>` to every `smd install` argv `build_plan` emits.
- Behaviour with a list:
  - All refusals happen before the first change: list unreadable; `SIGMOND_TRACK_MAIN=1` set (exit 2, "--release-list and SIGMOND_TRACK_MAIN=1 name different commits; pick one"); a catalog component to install has no row; the list's onion/wsjtx/ubersdr_wsprd differ from `_native_constants()` ("list and bin/smd disagree on <name>"); a dirty existing checkout.
  - radiod builds at the list's `ka9q-radio` row in place of `_ka9q_radio_pin()`.
  - Without `--release-list` nothing changes.

- [ ] **Step 1: Write the failing tests**

```python
def test_catalog_at_release_sets_pins():         # every listed catalog entry's .pin == row; unlisted untouched
def test_unlisted_component_refuses_before_change(): # fake catalog_queue with "chirp-recorder" -> rc 1, no clone call
def test_track_main_conflict_refuses(monkeypatch):    # SIGMOND_TRACK_MAIN=1 + --release-list -> rc 2
def test_native_disagreement_refuses():          # list onion != _ONION_COMMIT -> rc 1, message names onion
def test_dirty_checkout_refuses_before_any_change(tmp_path):  # Review Focus 3: no fetch, no checkout run
def test_seat_moves_clean_existing_checkout(tmp_path):        # ka9q-python at main -> at row, detached
def test_write_pins_after_install(tmp_path):     # .pin == row for listed non-native checkouts; onion has none
def test_write_pins_reports_head_mismatch(tmp_path)
def test_radiod_builds_at_list_row(monkeypatch): # _clone_repo called with ref == list ka9q-radio row
def test_bringup_plan_forwards_release_list():   # every Step with argv[1]=="install" ends with ["--release-list", ABS]
def test_bringup_plan_without_list_unchanged():  # argv equal to today's
```
Reuse the fakes in `tests/test_install_no_pull.py`; git work happens in `tmp_path` repos.

- [ ] **Step 2: Run them to see them fail** — `... rh-3 tests/test_install_release_list.py tests/test_bringup.py`.

- [ ] **Step 3: Implement** the helpers and flag plumbing above.

- [ ] **Step 4: Run** the two files, then `tests/test_bringup*.py tests/test_install_*.py`. Expected: PASS.

- [ ] **Step 5: Commit** `bin/smd lib/sigmond/bringup.py tests/test_install_release_list.py tests/test_bringup.py` — "install: --release-list installs the list's commits and pins them".

---

### Task 4: `smd check` reads a dormant radiod as waiting (sigmond)

**Files:**
- Modify: `lib/sigmond/commands/check.py`
- Modify: `tests/check_fakes.py` (default `sdr_present=lambda: None`)
- Test: `tests/test_check_radiod_dormant.py`

**Interfaces:**
- Produces:
  - `RADIOD_DORMANT = 'waits on dormant radiod'`
  - `Env.sdr_present: Callable[[], Optional[bool]] = None`; `default_env()` fills it with `hardware._sdr_present`.
  - `radiod_dormant(env: Env) -> bool`: True when `env.installed('ka9q-radio')`, `env.service(RADIOD_UNITS).state in ('inactive', 'absent')`, and `env.sdr_present()` is False. None (unknown) or a missing probe is False.
  - `bringup_hold(env, radio=True)` returns `RADIOD_DORMANT` when `radio and radiod_dormant(env)` and no earlier hold applies.
  - `page_check` gains `radio: bool = False`; the receiver page (ka9q-web) passes True: with `radiod_dormant(env)` and no answer it returns `Line(WAIT, label, f'{url} does not answer; {RADIOD_DORMANT}')`.
  - `receivers_line`: with `radiod_dormant(env)`, a receiver with no instance up counts WAIT, and the line ends `; waits on dormant radiod`.

- [ ] **Step 1: Write the failing tests**

```python
DORMANT = dict(service=lambda p: svc('inactive', None, enabled=False), sdr_present=lambda: False)

@pytest.mark.parametrize("fn,label", [(check.psk_line, 'PSK'), (check.wspr_line, 'WSPR'),
                                      (check.meteor_line, 'meteor scatter'), (check.grape_line, 'GRAPE')])
def test_consumer_waits_on_dormant_radiod(fn, label):
    line = fn(make_env(**DORMANT, timestd_config=lambda: {}))
    assert line.mark == check.WAIT and 'waits on dormant radiod' in line.text
def test_receiver_page_waits(): ...                 # http -> (None, 'refused') -> WAIT, text ends RADIOD_DORMANT
def test_failed_radiod_still_fails():               # radiod 'failed', sdr False -> PSK FAIL   (Review Focus 4)
def test_sdr_present_is_not_dormant():              # radiod inactive, sdr True -> PSK FAIL     (Review Focus 4)
def test_unknown_sdr_is_not_dormant():              # sdr_present -> None -> PSK FAIL
def test_radiod_not_installed_is_not_dormant():     # installed('ka9q-radio') False -> unchanged FAIL
def test_mag_line_ignores_radiod():                 # mag uses radio=False: unchanged
def test_verdict_is_wait_exit_2():                  # run_checks over the four lines -> verdict 'WAIT', EXIT_CODES 2
```

- [ ] **Step 2: Run them to see them fail** — `... rh-4 tests/test_check_radiod_dormant.py`.

- [ ] **Step 3: Implement** as above. The GRAPE line must reach `service_problem` before reading timestd config; check its order and keep the hold first.

- [ ] **Step 4: Run** `tests/test_check_radiod_dormant.py` and every `tests/test_check_*.py`. Expected: PASS.

- [ ] **Step 5: Commit** `lib/sigmond/commands/check.py tests/check_fakes.py tests/test_check_radiod_dormant.py` — "check: a consumer of a dormant radiod waits, not fails".

---

### Task 5: align reads HamSCI/sigmond first (sigmond)

**Files:**
- Modify: `lib/sigmond/align.py`, `lib/sigmond/align_apply.py`
- Test: `tests/test_align.py`, `tests/test_align_apply.py` (new classes; update the URL constants the old cases build from)

**Interfaces:**
- Produces (align.py):
  - `RELEASE_SLUG = "HamSCI/sigmond"`; `PROXMOX_SLUGS = ("HamSCI/sigmond-proxmox", "HamSCI/sigmond-appliance")` (the second goes with the mirror; comment says so).
  - `releases_api(slug) -> str`, `releases_web(slug) -> str`, `manifest_href_re(slug) -> re.Pattern`. `RELEASES_API`/`RELEASES_WEB` stay as names for sigmond's.
  - `class ReleaseNotFound(LookupError_)`: `_get` raises it for a 404 on any `/releases/` URL; `_release_from_web` raises it when `/releases/latest` does not land on `/releases/tag/<tag>`, or a named tag is absent.
  - `Release.source: str = "HamSCI/sigmond-proxmox"` (new field, last, defaulted).
  - `fetch_release(tag=None, urlopen=None, timeout=20.0) -> Release`: try `RELEASE_SLUG`; on `ReleaseNotFound` alone try each of `PROXMOX_SLUGS` in order; any other `LookupError_` (network, 5xx, malformed manifest) refuses at once. The API-then-web order applies within each slug. A Release from sigmond whose manifest has no `sigmond` row refuses.
  - `RAW_BASES = tuple(f"https://raw.githubusercontent.com/{s}" for s in PROXMOX_SLUGS)`; `image_file_drift` tries each base on 404 only. `RAW_BASE` stays as `RAW_BASES[0]`.
- Produces (align_apply.py):
  - `SIGMOND_REPO_URL = "https://github.com/HamSCI/sigmond"`; `PROXMOX_REPO_URL = "https://github.com/HamSCI/sigmond-proxmox"` (replaces `APPLIANCE_REPO`; ls-remote follows GitHub's redirect for the old name, so one URL serves).
  - `verify_release(rel, run)`: candidates unchanged. `rel.source == RELEASE_SLUG`: the sigmond tag `rel.tag` must peel to a commit `_sha_equal` to `rel.components["sigmond"]`, and the proxmox tag must equal `rel.appliance_commit`. Any other source: today's proxmox check alone.
  - `refresh_image_file` tries `RAW_BASES` in order on a 404.
- Docstrings that say "sigmond-appliance" now say "sigmond-proxmox" (the image repo).

- [ ] **Step 1: Write the failing tests**

```python
def test_latest_from_sigmond():                      # sigmond /latest answers -> Release.source == "HamSCI/sigmond"
def test_no_release_on_sigmond_falls_back():         # sigmond /latest 404 -> proxmox /latest -> source proxmox
def test_tag_absent_on_sigmond_falls_back():         # --release v3.93: sigmond /tags/v3.93 404 -> proxmox
def test_proxmox_404_tries_legacy_name():            # sigmond 404, sigmond-proxmox 404 -> sigmond-appliance answers
def test_network_error_on_sigmond_does_not_fall_back():   # Review Focus 1: URLError -> LookupError_, no proxmox URL in fake.seen
def test_5xx_on_sigmond_does_not_fall_back():
def test_web_path_no_release_on_sigmond_falls_back():     # Review Focus 2: rate-limited; web latest lands on .../releases
def test_sigmond_release_without_sigmond_row_refuses():
def test_new_stem_manifest_asset_found():            # asset "sigmond-v3.94-20261121-release.manifest.txt"
def test_verify_sigmond_release_checks_both_tags():  # fake run: sigmond tag peels to the row's commit, proxmox tag == appliance_commit -> ok
def test_verify_sigmond_tag_elsewhere_refuses():     # sigmond tag -> other commit -> ApplyError naming HamSCI/sigmond
def test_verify_proxmox_source_unchanged():          # source proxmox -> only one ls-remote, at PROXMOX_REPO_URL
def test_image_file_drift_falls_back_on_404():
```

- [ ] **Step 2: Run them to see them fail** — `... rh-5 tests/test_align.py tests/test_align_apply.py`.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run** `tests/test_align*.py`. Expected: PASS.

- [ ] **Step 5: Commit** `lib/sigmond/align.py lib/sigmond/align_apply.py tests/test_align.py tests/test_align_apply.py` — "align: read releases from HamSCI/sigmond, fall back to the image repo on 404".

---

### Task 6: pm-align follows (sigmond)

**Files:**
- Modify: `scripts/proxmox/pm-align.py` (constants ~46-53, `fetch_release` ~206, `_release_from_api/_web`, `fetch_sources` ~315, the raw reader ~361)
- Test: `tests/test_pm_align.py`

**Interfaces:**
- pm-align stays a standalone script (it runs on the Proxmox host and imports nothing from sigmond). It carries its own copies: `RELEASE_SLUG`, `PROXMOX_SLUGS`, the 404-only fallback, `Release.source` (defaulted). `fetch_sources` and the host-file reader fetch from `raw.githubusercontent.com/<slug>/<appliance_commit>/<file>` trying `PROXMOX_SLUGS` in order on a 404.
- `_RELEASE_TAG` checks and `load_candidate` stay as they are.

- [ ] **Step 1: Write the failing tests** — the same six cases as Task 5's fetch cases (`test_pm_latest_from_sigmond`, `test_pm_no_release_on_sigmond_falls_back`, `test_pm_network_error_does_not_fall_back`, `test_pm_web_no_release_falls_back`, `test_pm_new_stem_asset_found`, `test_pm_raw_falls_back_on_404`); update the `WEB` constant and hrefs the old cases use.

- [ ] **Step 2: Run them to see them fail** — `... rh-6 tests/test_pm_align.py`.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run** `tests/test_pm_align.py`. Expected: PASS.

- [ ] **Step 5: Commit** `scripts/proxmox/pm-align.py tests/test_pm_align.py` — "pm-align: read releases from HamSCI/sigmond, fall back on 404".

---

### Task 7: the bare-metal doc as a test script; pointers to both install paths (sigmond)

**Files:**
- Create: `lib/sigmond/bare_metal_doc.py`
- Modify: `docs/install-bare-metal.md` (step 3 command gains `--release-list /opt/git/sigmond/sigmond/release/components.toml` and one sentence saying it installs the commits of the release checked out in step 2), `README.md` (an "Install" paragraph naming both paths: bare metal -> `docs/install-bare-metal.md`; Proxmox image -> sigmond-proxmox `INSTALL.md`), `docs/operator/install.md` (the spec's `docs/install.md` does not exist; this page holds the pointer), and every live `HamSCI/sigmond-appliance` URL under `README.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `docs/` (not `docs/superpowers/`, `docs/archive/`), `lib/`, `bin/`, `scripts/` -> `HamSCI/sigmond-proxmox`. Prose naming the image repo says "sigmond-proxmox".
- Test: `tests/test_bare_metal_doc.py`, `tests/test_release_home_urls.py`

**Interfaces:**
- Produces:
  - `class DocError(ValueError)`
  - `@dataclass(frozen=True) class DocSteps: install: tuple[str, ...]; bringup: str; checkout_version: Optional[str]`
  - `extract(md: str) -> DocSteps`: `install` = command lines of the fenced ```` ```bash ```` blocks between the heading starting `## 2.` and the next `## `; `bringup` = the first fenced bash block under `## 3.`, backslash continuations joined into one line, which must start `smd bringup dasi2` and carry `--release-list` (else DocError). `checkout_version` = the version of the one install line matching `^git checkout (v\d+\.\d+(\.\d+)?)$`; None when absent; two such lines -> DocError.
  - `commands_digest(steps: DocSteps) -> str`: sha256 hex of `"\n".join(steps.install + (steps.bringup,)) + "\n"`.
  - `TEST_IDENTITY = {"--callsign": "N0CALL", "--grid": "EM38ww", "--reporter": "N0CALL/BM"}`
  - `test_script(steps, *, sigmond_commit: str) -> tuple[str, str]` (install part, bringup part): requires `checkout_version` (DocError "the doc checks out no release"); replaces that line with `git checkout <sigmond_commit>`; in bringup replaces the three identity values, drops every `--psws-*` option and its value, appends `--non-interactive`. Each part opens with `set -euo pipefail`.
  - `RADIOD_CONSUMERS = ('PSK', 'WSPR', 'meteor scatter', 'GRAPE')`
  - `judge_check(doc: dict) -> tuple[bool, list[str]]` over `smd check --json`: False when any line's mark is `FAIL`, or a `RADIOD_CONSUMERS` label is missing or not `wait` with `waits on dormant radiod` in its text; the list says why, one ASCII line each.
  - `main(argv)`: `script --doc F --commit C --part install|bringup`, `digest --doc F`, `checkout-version --doc F`, `judge --check-json F` (exit 0 pass / 1 fail).

- [ ] **Step 1: Write the failing tests**

```python
def test_extract_current_doc():            # the repo's own doc: bringup carries --release-list; checkout_version is None
def test_extract_with_checkout_line():     # fixture doc with "git checkout v3.94" -> "v3.94"
def test_two_checkout_lines_refuse():
def test_bringup_without_release_list_refuses():
def test_digest_ignores_prose_and_verified_line():   # changing "Verified against" or prose keeps the digest
def test_digest_changes_with_a_command():
def test_script_substitutes_commit_and_identity():
    inst, br = test_script(steps, sigmond_commit="a"*40)
    assert "git checkout " + "a"*40 in inst and "git checkout v3.94" not in inst
    assert "--callsign N0CALL" in br and "--psws" not in br and br.rstrip().endswith("--non-interactive")
def test_judge_passes_dormant_waits():     # four consumers wait w/ RADIOD_DORMANT, others ok/-- -> (True, [])
def test_judge_fails_on_any_fail():
def test_judge_fails_on_missing_consumer():
# test_release_home_urls.py
def test_no_live_reference_to_old_repo():  # grep the paths named in Files; allow /etc/sigmond-appliance,
                                           # /root/sigmond-appliance, sigmond-appliance-<vtag>.local, and the
                                           # PROXMOX_SLUGS legacy entry in align.py and pm-align.py
```

- [ ] **Step 2: Run them to see them fail** — `... rh-7 tests/test_bare_metal_doc.py tests/test_release_home_urls.py`.

- [ ] **Step 3: Implement** the module, then the doc and URL edits. Keep `docs/install-bare-metal.md`'s step 2 cloning main for now: the release checkout line (`git checkout vN`) arrives per release in the operator steps. Read `~/AUTHOR_VOICE.md` before the prose edits.

- [ ] **Step 4: Run** both files plus `tests/test_check_docs.py` and `tests/test_docs_conformance.py`. Expected: PASS.

- [ ] **Step 5: Commit** the module, the two tests and each edited doc by name — "docs: bare-metal doc drives its own test; both install paths named; image repo is sigmond-proxmox".

---

### Task 8: release lives in sigmond: list home, bless, RELEASE.md, CHANGELOG (sigmond)

**Files:**
- Create: `release/bless-release.sh` (from sigmond-proxmox `bless-release.sh`, then changed as below), `release/test-bless-instructions.sh`, `release/test-bless-native-pins.sh` (moved, adapted), `release/test-bless-release-home.sh` (new), `release/RELEASE.md` (from sigmond-proxmox `docs/RELEASE.md`, rewritten for this design), `CHANGELOG.md` (sigmond-proxmox `CHANGELOG.md`'s content, whole; old `## sigmond-appliance vN` headings stay as history).
- Read first: sigmond-proxmox `bless-release.sh`, `test-bless-instructions.sh`, `test-bless-native-pins.sh`, `docs/RELEASE.md`, `publish-lib.sh`.

**Interfaces:**
- Consumes: C2, C3; `python3 -m sigmond.release_list check|version` (Task 1); `python3 -m sigmond.bare_metal_doc digest|checkout-version` (Task 7), both run as `PYTHONPATH=$SIGMOND_REPO/lib python3 -m ...`.
- Env: `SIGMOND_REPO` (default: the checkout holding the script), `PROXMOX_REPO` (default `$HOME/appliance/repos/sigmond-proxmox`), `RIG_DIR` (default `$HOME/appliance/v3`), `GH_REPO_SLUG` (default `HamSCI/sigmond`), `GH_MIRROR_SLUG` (default `HamSCI/sigmond-proxmox`), `PUBLISH_LIB` (default `$PROXMOX_REPO/publish-lib.sh`).
- Usage: `bless-release.sh <version> (--mirror | --no-mirror) [--apply]`.
- Gates (dry run reports each; `--apply` acts only when all pass):
  - 0: real release version (unchanged).
  - 1 (tags): `SIG_COMMIT` = the manifest's `sigmond` row resolved to full in `SIGMOND_REPO`; `PX_COMMIT` = `appliance_commit`. Each must be reachable from its repo's `origin/main`. Where tag `<version>` exists (locally or by `git ls-remote --tags origin`), it must point at that commit, else FAIL "tag <v> already exists elsewhere in <repo>". Absent: PASS "cut at --apply".
  - 2: both checkouts clean.
  - 3: image `sigmond-<version>-*-release.img` and its `.sha256` (C3).
  - 4: manifest has a components block **and** `release_list check` passes against `git show $SIG_COMMIT:release/components.toml`, **and** that list's `version` equals `<version>`.
  - 5: nested test evidence (unchanged).
  - 5b: firstboot read at `PX_COMMIT` (was the tag).
  - 6: no Release `<version>` on `GH_REPO_SLUG`; with `--mirror`, none on `GH_MIRROR_SLUG` either. 6b: FAIL when neither `--mirror` nor `--no-mirror` was given ("choose --mirror while any station or host runs a pre-v3.94 smd or pm-align").
  - 7: proxmox `origin/main:INSTALL.md` Status `current` and Verified against holds `sigmond <version>` as a token; `origin/main:docs/install-page.html` names the image file; sigmond `origin/main:docs/install-bare-metal.md` Verified against holds `sigmond <version>` and `bare_metal_doc checkout-version` prints `<version>`.
  - 8: sigmond `origin/main:CHANGELOG.md` holds exactly one `## sigmond <version>` heading, not `(draft)`, with text.
  - 9: native pins (unchanged; reads `bin/smd` at `SIG_COMMIT`).
  - 10 (bare metal): the newest C2 record in `$RIG_DIR/bare-metal/` with `version: <version>` and `sigmond_commit: SIG_COMMIT` reads `result: PASS`, and its `doc_commands_sha256` equals `bare_metal_doc digest` of sigmond `origin/main:docs/install-bare-metal.md`.
- `--apply`: after the confirmation prompt, create annotated tag `<version>` at `SIG_COMMIT` in `SIGMOND_REPO` and at `PX_COMMIT` in `PROXMOX_REPO` (each only if absent), push both (`git push origin refs/tags/<version>`), then `gh release create <version> --repo $GH_REPO_SLUG --verify-tag` with the manifest and `.sha256`; with `--mirror`, the same command, notes and assets on `$GH_MIRROR_SLUG`. Then the publish to targets as today.

- [ ] **Step 1: Write the failing test** `release/test-bless-release-home.sh` in the style of `test-bless-instructions.sh` (scratch origins for both repos, stub `gh`/`rclone`/`ssh`/`scp`, `has`/`lacks`/`chk`):
  - `g1 absent` -> `PASS 1` with "cut at --apply"; `g1 elsewhere` (sigmond tag at another commit) -> `FAIL 1`, "already exists elsewhere".
  - `g4 row differs` -> `FAIL 4` naming the component; `g4 version` (list says v3.95) -> `FAIL 4`.
  - `g6 sigmond release exists` -> `FAIL 6`; `g6 mirror release exists` with `--mirror` -> `FAIL 6`; `g6b no choice` -> `FAIL 6b`.
  - `g10 none` -> FAIL; `g10 later fail` (PASS then a newer FAIL for the commit) -> FAIL; `g10 other commit` -> FAIL; `g10 digest drift` (doc command changed after the record) -> FAIL; `g10 pass` -> PASS.
  - `apply`: both scratch origins hold tag `v3.70` at the expected commits; the `gh` stub log shows `release create v3.70 --repo HamSCI/sigmond` and, with `--mirror`, `--repo HamSCI/sigmond-proxmox`, each with the `.manifest.txt` and `.sha256`.

- [ ] **Step 2: Run it to see it fail** — `bash release/test-bless-release-home.sh`; Expected: FAIL (no `release/bless-release.sh`).

- [ ] **Step 3: Implement** the moved script with the gates above; adapt the two moved tests to the new env names, image stem, tag timing (tags cut at bless, not before the test), C2 records, and `--mirror`. Write `release/RELEASE.md` (read `~/AUTHOR_VOICE.md` first; standard header lines Audience/Status/Verified against). Its ladder, in order: write the list (`smd admin release-list --write --version vN`), review, commit, push with the file list on the bus first; candidate build at that sigmond commit; nested image test; update test; bare-metal test (`test-bare-metal.sh vN`); canaries B4 and SIGMA by `smd align --manifest`; a sigmond bug means a new candidate at the fixed commit, no tag moves; release doc lines (INSTALL.md, install page, the bare-metal doc's `git checkout vN` and Verified line, CHANGELOG heading); `bless-release.sh vN --mirror --apply`. It states when `--mirror` ends: the fleet board shows no station VM on a pre-v3.94 smd and no host on a pre-v3.94 pm-align. Copy `CHANGELOG.md`; rename its draft heading from `## sigmond v3.94 (draft)` only at release time.

- [ ] **Step 4: Run** all three: `bash release/test-bless-release-home.sh && bash release/test-bless-instructions.sh && bash release/test-bless-native-pins.sh`. Expected: each prints 0 FAIL.

- [ ] **Step 5: Commit** the six files by name — "release: bless, RELEASE.md and CHANGELOG move into sigmond; bare-metal gate; tags cut at bless".

---

### Task 9: the image build installs the list (sigmond-proxmox)

**Files:**
- Create: `release-home-lib.sh`, `test-release-home-lib.sh`
- Modify: `provision.sh` (check out `$SIGMOND_COMMIT` detached after the clone; FATAL if unset or not a 40-hex commit), `build-golden-vm.sh` (resolve and record C4; pass `SIGMOND_COMMIT` to both stages; replace the freshness block at ~226-290 with `smd admin release-list --check --require-all` run in the VM, saved to `golden-v3-freshness.txt`, FATAL on non-zero), `provision-components.sh` (drop `SIGMOND_TRACK_MAIN=1`; run `smd install --yes --release-list /opt/git/sigmond/sigmond/release/components.toml`; FATAL when the list is absent), `build-usb-v3.sh` (below).

**Interfaces:**
- Consumes: Task 1 CLI, Task 2 `--check`, Task 3 `--release-list`, C4.
- Produces (`release-home-lib.sh`, sourced by the build scripts):
  - `rh_sigmond_commit <rig_v3_dir>` -> prints C4's commit; fails when absent or malformed.
  - `rh_list_text <sigmond_repo> <commit>` -> `git show <commit>:release/components.toml`; fails when absent.
  - `rh_version <sigmond_repo> <commit>` -> the list's version via `python3 -m sigmond.release_list version --list -`.
  - `rh_check_manifest <sigmond_repo> <commit> <manifest>` -> exit of `release_list check`.
  - `rh_proxmox_row <sigmond_repo> <commit>` -> the list's `sigmond-proxmox` row.
- `build-golden-vm.sh`: `SIGMOND_COMMIT="${SIGMOND_COMMIT:-$(git ls-remote https://github.com/HamSCI/sigmond refs/heads/main | cut -f1)}"`, refused unless 40-hex; written to `golden-v3-sigmond-commit.txt.new`, renamed with `golden-v3-revs.txt.new` once the template is written.
- `build-usb-v3.sh`:
  - `REPO` default `$HOME/appliance/repos/sigmond-proxmox`.
  - `VERSION` = `rh_version` at C4's commit (`--dev` keeps `v0.0-dev+<sha>`); the "HEAD is not tagged" refusal goes.
  - Refuse unless `git -C $REPO rev-parse HEAD` equals `rh_proxmox_row` (skipped under `--dev`).
  - The wizard gate compares `$SIGMOND_REPO` HEAD with C4's commit, in place of `origin/main`.
  - After `manifest-host.txt` is written: `rh_check_manifest` must pass, else die "the image differs from release/components.toml" with the mismatch lines.
  - `appliance_tag: $VERSION` and `image_version: $VERSION` stay.

- [ ] **Step 1: Write the failing test** `test-release-home-lib.sh` (scratch sigmond repo holding `lib/sigmond/` copied from `../sigmond/lib/sigmond` and a commit carrying a fixture list): `rh_sigmond_commit` on a good / missing / short file; `rh_version` prints `v3.94`; `rh_proxmox_row`; `rh_check_manifest` 0 on a matching fixture manifest, 1 on one moved row (output names it), non-zero when the commit has no list. Plus `grep` assertions on the scripts: `provision-components.sh` lacks `SIGMOND_TRACK_MAIN=1` and holds `--release-list`; `build-usb-v3.sh` lacks `describe --tags --exact-match` and calls `rh_check_manifest`; `bash -n` on all four scripts.

- [ ] **Step 2: Run it to see it fail** — `bash test-release-home-lib.sh`.

- [ ] **Step 3: Implement** the lib and the four script changes.

- [ ] **Step 4: Run** `bash test-release-home-lib.sh`, then `bash test-journald-cap.sh` and `bash test-offline-debs.sh` (they read `provision*.sh`). Expected: 0 FAIL each.

- [ ] **Step 5: Commit** in `/home/mjh/hamsci/repos/sigmond-appliance`: `release-home-lib.sh test-release-home-lib.sh provision.sh provision-components.sh build-golden-vm.sh build-usb-v3.sh` — "build: install release/components.toml at the golden VM's sigmond commit; refuse a capture that differs".

---

### Task 10: image files become `sigmond-<v>-<stamp>` (sigmond-proxmox)

**Files:**
- Modify: `build-usb-v3.sh` (`IMG=` ~534), `publish-lib.sh` (`_pub_version` ~118 and any prune glob), `sigmond-flash-latest.sh` (~120-340: list, prune and pick both stems; prefer the highest version, then the newer date; `FLASH_VERSION=2026.11.20.1`), `rig-flash.sh` (~36), `flash-usb.sh` (~41), `test-nested-v3.sh` (~1286: newest of `sigmond-v3*.img` and `sigmond-appliance-v3*.img`)
- Test: `test-publish-lib.sh`, `test-flash-latest.sh`, `test-rig-flash.sh`, `test-publish-prune.sh` (add scenes)

**Interfaces:**
- Consumes: C3.
- Produces: `_pub_version` maps both `sigmond-v3.94-20261121-release.img` and `sigmond-appliance-v3.93-...` to `3.94` / `3.93`.

- [ ] **Step 1: Write the failing scenes**: `_pub_version` on both stems; prune keeps the newest N across both stems; flasher scene `both stems` (folder with `sigmond-appliance-v3.93-...` and `sigmond-v3.94-...` pairs picks v3.94); `version bumped` (`FLASH_VERSION` sorts above `2026.11.13.1` with `sort -V`); `rig-flash` finds a new-stem image.

- [ ] **Step 2: Run them to see them fail.**

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run** the four tests plus `bash test-publish-fetchurl.sh`. Expected: 0 FAIL.

- [ ] **Step 5: Commit** the changed scripts and tests by name — "images: name them sigmond-<version>; every reader accepts both stems".

---

### Task 11: `test-bare-metal.sh`, the rig's bare-metal install test (sigmond-proxmox)

**Files:**
- Create: `test-bare-metal.sh`, `bare-metal-lib.sh`, `test-bare-metal-lib.sh`
- Modify: `sync-rig.sh` only if a new top-level file needs it (it copies every tracked top-level file already; confirm and leave it).

**Interfaces:**
- Consumes: C2, C4; Task 7's `python3 -m sigmond.bare_metal_doc script|digest|checkout-version|judge`; Task 2's `smd admin release-list --check`; Task 4's reading.
- Produces:
  - `test-bare-metal.sh <version> [--commit SHA] [--dry-run]`. Env: `APPLIANCE_RIG_ROOT` (default `$HOME/appliance`), `SIGMOND_REPO` (default `$RIG_ROOT/repos/sigmond`), `APPLIANCE_VMBUILD_DIR` (default `$RIG_ROOT/vmbuild`), `APPLIANCE_BUILD_DIR` (key `applkey`), `RIG_DIR` (default `$RIG_ROOT/v3`).
  - Flow: commit = `--commit` or C4; `git -C $SIGMOND_REPO fetch origin`; the list's version at the commit must equal `<version>`; doc = `origin/main:docs/install-bare-metal.md`; its `checkout-version` must equal `<version>`; build both script parts. `--dry-run` prints both parts and the planned record path, then exits 0 before any VM work. Otherwise: copy `debian-13-genericcloud-amd64.qcow2` to `bare-metal-test.qcow2`, resize 40G, boot with qemu as `build-golden-vm.sh` does (`-name baremetal`, same `seed.iso`, ssh on `127.0.0.1:5558`), never touching the `goldenv3` VM; write `/etc/sigmond-appliance/rac-endpoint.env` pinning RAC to 127.0.0.1 exactly as `test-nested-v3.sh` ~1704 does, so the VM never registers with the fleet; run the install part in one ssh session and the bringup part in a fresh one; then `smd admin release-list --check` and `smd check --json` in the VM; judge; write the C2 record; stop the VM; exit 0 on `result: PASS`. Log to `$RIG_DIR/bare-metal/<version>-<stamp>.log`.
  - `bare-metal-lib.sh`: `bm_record <dir> <version> <commit> <digest> <list_rc> <check_exit> <judge_rc>` writes C2 and prints its path; `bm_newest <dir> <version> <commit>` prints the newest matching record.

- [ ] **Step 1: Write the failing test** `test-bare-metal-lib.sh`: `bm_record` writes every C2 key; `result: PASS` only for (0, 2, 0) and (0, 0, 0); FAIL for list_rc 1, check_exit 1, judge_rc 1; `bm_newest` picks the later of two; `test-bare-metal.sh v3.94 --commit <sha> --dry-run` against a scratch sigmond repo (fixture list and a doc with `git checkout v3.94`) prints `git checkout <sha>` and `--non-interactive`; with the doc lacking the checkout line it exits non-zero naming it; `bash -n test-bare-metal.sh`.

- [ ] **Step 2: Run it to see it fail.**

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run** `bash test-bare-metal-lib.sh`. Expected: 0 FAIL.

- [ ] **Step 5: Commit** `test-bare-metal.sh bare-metal-lib.sh test-bare-metal-lib.sh` — "rig: bare-metal install test from the doc's own commands".

---

### Task 12: sigmond-proxmox hands release over and follows the rename (sigmond-proxmox)

**Files:**
- Delete: `bless-release.sh`, `test-bless-instructions.sh`, `test-bless-native-pins.sh` (now in sigmond `release/`)
- Modify: `docs/RELEASE.md` (a short pointer: release lives in sigmond `release/RELEASE.md`; this repo builds and tests the image), `CHANGELOG.md` (a pointer to sigmond's `CHANGELOG.md`; history moved there whole), `README.md`, `INSTALL.md` (flasher URL ~81 and repo name ~532 -> `HamSCI/sigmond-proxmox`; the Verified line's wording changes at release, not here), `docs/install-page.html` (~170, ~490: flasher URL -> sigmond-proxmox; changelog link -> `github.com/HamSCI/sigmond/blob/main/CHANGELOG.md`), every other live `HamSCI/sigmond-appliance` or `repos/sigmond-appliance` outside `docs/superpowers/`.
- Create: `test-release-home-urls.sh`

**Interfaces:**
- Consumes: Task 8 (bless exists in sigmond), Task 10.

- [ ] **Step 1: Write the failing test** `test-release-home-urls.sh`: no `HamSCI/sigmond-appliance` and no `repos/sigmond-appliance` in tracked files outside `docs/superpowers/`; `bless-release.sh` absent; `docs/RELEASE.md` and `CHANGELOG.md` name `HamSCI/sigmond`.

- [ ] **Step 2: Run it to see it fail.**

- [ ] **Step 3: Implement** (`git rm` the three files by name; read `~/AUTHOR_VOICE.md` before the prose).

- [ ] **Step 4: Run** `bash test-release-home-urls.sh`, `bash test-reflash-docs.sh`, `bash test-instructions-remote.sh`. Expected: 0 FAIL.

- [ ] **Step 5: Commit** the deleted and changed files by name — "release moves to sigmond; this repo is sigmond-proxmox".

---

### Task 13: ops follows (ops)

**Files:**
- Modify: `/home/mjh/hamsci/ops/bin/prune-build-artifacts.sh` (~7, 32, 170-171: match `sigmond-*.img[.xz]` and `sigmond-appliance-*.img[.xz]`), `/home/mjh/hamsci/ops/bin/site-identity` (~14, 24, 67: `OPERATORS` = `REPOS/"sigmond-proxmox"/"operators"` when that directory exists, else `REPOS/"sigmond-appliance"/"operators"`; prose says sigmond-proxmox). `bin/fleet-ssh` names no image repo (checked); leave it.

- [ ] **Step 1: Write the failing check**: `bash -n bin/prune-build-artifacts.sh`; a scratch-dir dry run of `prune-build-artifacts.sh --images` lists one file of each stem; `python3 -c` importing `bin/site-identity` as a module resolves `OPERATORS` under a fake `REPOS` holding only `sigmond-proxmox/operators`. If ops carries tests for these (`ls tests/ 2>/dev/null`), add the cases there.

- [ ] **Step 2: Run it to see it fail.**

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run it to see it pass.**

- [ ] **Step 5: Commit** in `/home/mjh/hamsci/ops`: the two files (and any test) by name — "ops: image names and operator keys follow sigmond-proxmox".

---

## Operator steps after the code lands

Not subagent tasks. Each step on B4 or the rig is announced on the claude-bus first, and the ledger item `sigmond-release-home` moves with it.

1. **Push.** Post the file list of every commit (sigmond, sigmond-proxmox, ops) on the bus; then push each main. Before pushing sigmond, confirm `gh release list --repo HamSCI/sigmond` is empty: align now treats any Release there as the newest.
2. **Rename** HamSCI/sigmond-appliance to HamSCI/sigmond-proxmox (Michael, repo admin), as soon after the push as possible. Between the two, a main-branch smd still finds releases through the legacy fallback slug; renaming first would close even that gap.
3. **Rig checkout follows.** On the rig: move `$HOME/appliance/repos/sigmond-appliance` to `.../sigmond-proxmox`, `git remote set-url origin` to the new URL, pull; pull `.../sigmond`; run `./sync-rig.sh`. Rename the devbox checkout the same way (sigmond's tests look for `../sigmond-proxmox` first).
4. **Candidate build.** On the devbox: `smd admin release-list --write --version v3.94`, review every row against the canaries' state, commit `release/components.toml`, post the file list, push. On the rig: `build-golden-vm.sh` (records C4), then `build-usb-v3.sh`.
5. **Bare-metal test.** Commit and push the bare-metal doc's `git checkout v3.94` line (step 2 of that doc), then on the rig `./test-bare-metal.sh v3.94`. A FAIL record blocks the bless; fix, new candidate, repeat.
6. **Image and update tests.** `test-nested-v3.sh` on the new image, then `test-update-v3.sh`.
7. **Canaries.** B4 (announce first; record in `/etc/sigmond/CLAUDE-COORDINATION.md` what touches affinity, IRQs or cache) and SIGMA: `smd align --manifest <candidate manifest> --apply`, plus pm-align with `--manifest ... --release-tag v3.94` on B4's host. A sigmond bug found here means a fix, a new list commit and a new candidate; no tag moves.
8. **Bless.** Commit the release lines: INSTALL.md Verified `sigmond v3.94`, the install page's image name, the bare-metal doc's Verified `sigmond v3.94`, CHANGELOG `## sigmond v3.94`. Push, bus first. On the rig: `$HOME/appliance/repos/sigmond/release/bless-release.sh v3.94 --mirror` (dry run), then `--mirror --apply`. Watch one v3.93 station's `smd align` find v3.94 through the old name.
