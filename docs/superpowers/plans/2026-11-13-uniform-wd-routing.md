# Uniform Routing Through wsprdaemon.org Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every WSPR, FT4/FT8 and MSK144 spot from a sigmond station travels only to wsprdaemon.org, and no sigmond station posts to wsprnet.org or pskreporter.info itself.

**Architecture:** Four levers move together.  The upload verb writes only the server routes (`server-merge` for psk-recorder, `deposit` for meteor-scatter) and refuses the direct ones.  The manifest always flags `forward_to_wsprnet` on the tar and never renders `wspr-wsprnet`.  A new module, `uniform_routing`, moves running instances onto the server routes, and `smd sink upload`, `smd update --apply` and a new `smd admin uploader routing` verb run it.  `wsprnet_proxy` retires: sigmond still reads it, ignores it and warns once.

**Tech Stack:** Python 3 stdlib (sigmond core), pytest via `.venv/bin/pytest`, systemd.

**Spec:** `/home/mjh/hamsci/tasks/uniform-wd-routing/plan.md` (Michael, 2026-11-12), with the decision of 2026-11-13: every spot goes only to wsprdaemon.org, which stores it and forwards copies to wsprnet.org and pskreporter.info under the spot's own reporter id.  Only a merge (`merge_into`, declared on the station, carried out by the server) rewrites the reporter id.  Operators get no notice.  Rob's server side (forward every spot whatever its flags, except merges) has not shipped, so sigmond keeps setting the per-row forward flags.

## Global Constraints

- Stdlib only in sigmond core (`lib/sigmond/`).
- Work on `main`.  Commit after each task; never push.
- Run tests from `/home/mjh/hamsci/repos/sigmond` with `.venv/bin/pytest`.  `tests/conftest.py` sets `SIGMOND_TEST_NO_ELEVATE=1`, so tests never elevate.
- End every commit message with exactly these two lines:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z
  ```
- The carrier rule holds everywhere: psk and MSK rows reach wsprdaemon.org only inside the `wspr-wsprdaemon` tar (wspr-recorder's `deploy.toml`: source `wspr_cycle`, `include_psk = true`).  No code may move a station onto a server route when that station has no enabled wspr-recorder.
- Restarts never touch radiod.  Restart through `sink_levers.restart_units`, which refuses radiod units and raises `RadiodMoved` if radiod's start time changes.
- A sink lever value stays put.  Below `upload` (site or client reads `fill` or `off`), the lever step writes `PSK_DELIVERY_PIPELINES=server-raw` and `METEOR_SCATTER_DELIVERY_MODE=off`.  No code in this plan raises one.
- Merge behaviour stays byte-for-byte: `apply_merge`, `MERGE_KEYS`, `two_merges_problems` and `delivery_mismatches` keep their output.

Three facts the code taught, which the tasks rely on:

1. The lever step keeps no undo record for these two env levers, and a raise restores nothing (`sink_levers` module docstring; only a linked wspr-uploader's links come back).  A station raised to `upload` therefore keeps `server-raw` / `off` until something rewrites them.  The migration does that, and it tells a lever value from an operator's choice by reading the sink state, not a record.
2. At `upload`, `server-raw` (psk) and `off` (meteor) both post directly.  The rows carry `forward_to_pskreporter = 0`, and the station's own `*-pskreporter` daemon pipeline selects exactly those rows.  An unset `METEOR_SCATTER_DELIVERY_MODE` reads `direct` (`inprocess_senders._ms_delivery_mode`), and meteor-scatter's `deploy.toml` seeds new instances with `off`.
3. Keeping a station's direct `*-pskreporter` pipeline does not rescue `server-merge` rows, because that pipeline selects only `forward_to_pskreporter = 0`.  On a station with no carrier, only leaving the env alone keeps psk spots flowing.

## Review Focus

1. **A station with psk-recorder or meteor-scatter but no wspr-recorder.**  Nothing carries its rows to wsprdaemon.org.  The migration must leave each instance as it stands and print an ERROR line naming `smd enable wspr-recorder`.  `smd config upload ... --on` writes the standard value and prints the same ERROR.  The manifest keeps the direct pipeline and logs ERROR.  Tests: Task 3 `test_no_carrier_leaves_every_instance_and_says_error`, Task 2 `test_no_carrier_keeps_the_direct_psk_pipelines`, Task 1 `test_on_without_wspr_recorder_says_error`.
2. **A station whose sink reads `fill` or `off`.**  The migration must not touch `server-raw` / `off`; it reports them.  `smd sink fill` and `smd sink off` never run it.  A sink state that cannot be read changes nothing.  Tests: Task 3 `test_a_lowered_client_is_left_alone_and_reported`, `test_unknown_sink_state_changes_nothing`; Task 4 `test_sink_fill_never_routes`.
3. **A merge fleet (`merge_into`).**  A merged station already proxied renders the same tar transport and pipeline list as before, and the migration changes nothing on its `server-merge` / `deposit` instances.  Tests: Task 2 `test_a_merged_proxied_station_renders_as_before`, Task 3 `test_merged_instances_already_routed_change_nothing`.
4. **An instance whose environment cannot be read.**  `systemctl show` fails or an override wins over the env file.  The migration names it and neither writes nor restarts it; the other instances still move.  Tests: Task 3 `test_unreadable_env_is_named_and_untouched`, `test_an_override_that_still_posts_is_a_failure_not_a_restart`.
5. **A second run of the migration.**  It writes nothing, restarts nothing and prints only the "already deliver" summary.  Test: Task 3 `test_a_second_run_changes_nothing`.

---

### Task 1: Upload verb writes only server routes

**Files:**
- Modify: `lib/sigmond/upload.py:22-53` (`UPLOAD_ENABLE`, `DELIVERY_ON_ENABLE`, comment), `:124-157` (`apply_enable`)
- Modify: `lib/sigmond/commands/config.py:2108-2188` (`cmd_config_upload`)
- Modify: `lib/sigmond/bringup.py:553-575`
- Modify: `lib/sigmond/tui/screens/psws.py:234-239` (`_VIA_CHOICES`)
- Modify: `bin/smd:31781-31787` (`--via` help)
- Modify: `docs/greenfield-runbook.md:284-301`
- Test: `tests/test_upload.py`, `tests/test_bringup.py:498-506`, `tests/test_bringup_receivers.py:110-122`

**Interfaces:**
- Produces: `upload.DirectRouteRefused(ValueError)`; `upload.REFUSED_VIA: dict[str, tuple[str, ...]]` = `{"psk-recorder": ("direct", "server-raw", "both"), "meteor-scatter": ("direct", "off")}`; `upload.STANDARD_ROUTE: dict[str, tuple[str, str]]` = `{"psk-recorder": ("PSK_DELIVERY_PIPELINES", "server-merge"), "meteor-scatter": ("METEOR_SCATTER_DELIVERY_MODE", "deposit")}`; `upload.STANDARD_NOTE: str` (the sentence below).  Task 3 reads `STANDARD_ROUTE`.

- [ ] **Step 1: Write the failing tests** in `tests/test_upload.py` (replace `test_psk_enable_sets_direct_pipeline`, `test_psk_enable_via_server_merge`, `test_meteor_on_alone_leaves_the_mode_as_it_stands`):

```python
def test_psk_on_writes_server_merge(self):
    path, _, dests, delivery = upload.apply_enable(
        "psk-recorder", "AC0G/S", True, base=str(self.base))
    self.assertIn("PSK_DELIVERY_PIPELINES=server-merge", path.read_text())
    self.assertEqual(delivery, ("PSK_DELIVERY_PIPELINES", "server-merge"))
    self.assertEqual(dests, ["wsprdaemon.org (forwards to pskreporter.info)"])

def test_meteor_on_writes_deposit_over_a_seeded_off(self):
    p = self._env("meteor-scatter", "KFS-NW")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("METEOR_SCATTER_DELIVERY_MODE=off\n")
    upload.apply_enable("meteor-scatter", "KFS/NW", True, base=str(self.base))
    self.assertIn("METEOR_SCATTER_DELIVERY_MODE=deposit", p.read_text())

def test_every_direct_via_is_refused_and_writes_nothing(self):
    for client, vias in upload.REFUSED_VIA.items():
        for via in vias:
            with self.assertRaises(upload.DirectRouteRefused) as cm:
                upload.apply_enable(client, "AC0G/S", True,
                                    base=str(self.base), delivery=via)
            self.assertIn("wsprdaemon.org", str(cm.exception))
            self.assertIn(via, str(cm.exception))
            self.assertFalse(upload.env_path_for(
                client, "AC0G/S", base=str(self.base)).exists())

def test_the_standard_via_is_accepted(self):
    upload.apply_enable("psk-recorder", "AC0G/S", True,
                        base=str(self.base), delivery="server-merge")
    upload.apply_enable("meteor-scatter", "AC0G/S", True,
                        base=str(self.base), delivery="deposit")

def test_wspr_labels_name_wsprdaemon_only(self):
    self.assertEqual(upload.UPLOAD_ENABLE["wspr-recorder"][1],
                     ["wsprdaemon.org (forwards to wsprnet.org)"])
```

Add to the `cmd_config_upload` test class:

```python
def test_a_refused_via_returns_1_and_names_the_standard(self):
    # capture stderr; rc == 1; output contains upload.STANDARD_NOTE
def test_on_without_wspr_recorder_says_error(self):
    # patch config's topology loader to enabled_components() == ["psk-recorder"];
    # rc == 0, the env file reads server-merge, stderr contains
    # "no wspr-recorder" and "smd enable wspr-recorder"
def test_status_view_names_the_meteor_unset_default_as_direct(self):
    # an env file with no METEOR_SCATTER_DELIVERY_MODE; stdout contains
    # "unset (reads direct: posts from this station)"
```

In `tests/test_bringup.py` rename `test_bringup_leaves_the_daemon_as_the_only_pskreporter_sender` to `test_bringup_enables_psk_without_via` and assert `'--via' not in ups['psk-recorder']`.  In `tests/test_bringup_receivers.py:120` assert `not any('--via' in a for a in on)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_upload.py tests/test_bringup.py tests/test_bringup_receivers.py -q`
Expected: FAIL (`DirectRouteRefused` not defined; bring-up still passes `--via server-raw`).

- [ ] **Step 3: Implement**

In `upload.py`: `DELIVERY_ON_ENABLE` becomes `{"psk-recorder": ("PSK_DELIVERY_PIPELINES", "server-merge", ["server-merge"]), "meteor-scatter": ("METEOR_SCATTER_DELIVERY_MODE", "deposit", ["deposit"])}`.  `UPLOAD_ENABLE` labels: wspr-recorder `["wsprdaemon.org (forwards to wsprnet.org)"]`, psk-recorder and meteor-scatter `["wsprdaemon.org (forwards to pskreporter.info)"]`.  Rewrite the comment above `DELIVERY_ON_ENABLE` to state the standard (no "standalone direct" lever).  `apply_enable` checks `delivery in REFUSED_VIA.get(client, ())` first and raises `DirectRouteRefused` before any write, with this text:

`STANDARD_NOTE = "every spot goes to wsprdaemon.org, which forwards it to wsprnet.org and pskreporter.info; no station posts to them itself"`, so the message reads `f"--via {via} would post from this station; {STANDARD_NOTE}. Use --on alone."`

In `cmd_config_upload`: on `DirectRouteRefused`, print `err(str(e))` and no "valid --via choices" line.  After a successful `--on` for psk-recorder or meteor-scatter, when `"wspr-recorder" not in load_topology().enabled_components()`, print `err(f"no wspr-recorder is enabled on this host, so nothing carries {client}'s spots to wsprdaemon.org; run: smd enable wspr-recorder")`.  Replace the `delivery:` info line's "standalone direct-to-pskreporter" branch with `"  (wsprdaemon.org forwards it)"`.  The status view prints the unset default per client: psk-recorder `unset (default server-merge)`, meteor-scatter `unset (reads direct: posts from this station)`.

`bringup.py`: drop the `if client == 'psk-recorder': argv += ['--via', 'server-raw']` lines and rewrite the comment above them (`--on` alone writes the server route; the sink switch governs the tar).  `psws.py`: `_VIA_CHOICES = [("(default)", "")]`.  `bin/smd` `--via` keeps every old choice (so argparse lets the refusal speak) and its help reads `'delivery when enabling: server-merge (psk-recorder) or deposit (meteor-scatter), the defaults.  direct, server-raw and off are refused: wsprdaemon.org forwards every spot'`.

`docs/greenfield-runbook.md:284-301`: the psk line's comment becomes `# enable (wsprdaemon.org forwards to pskreporter)`, and the "psk-recorder delivery pipeline" note says `--on` writes `server-merge` (`deposit` for meteor-scatter), names the refusal and the wspr-recorder carrier requirement.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_upload.py tests/test_bringup.py tests/test_bringup_receivers.py tests/test_wizard_uploads.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/upload.py lib/sigmond/commands/config.py lib/sigmond/bringup.py lib/sigmond/tui/screens/psws.py bin/smd docs/greenfield-runbook.md tests/test_upload.py tests/test_bringup.py tests/test_bringup_receivers.py
git commit -m "upload: --on writes the wsprdaemon.org route; direct --via refused

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z"
```

---

### Task 2: Manifest always proxies wsprnet; psk/meteor carrier guard

**Files:**
- Modify: `lib/sigmond/uploader_manifest.py:330-425` (`_Proxied`, `PROXIED_SERVICES`, `apply_proxying`), `:1286-1304` (`drop_server_delivered_pipelines`)
- Test: `tests/test_uploader_manifest_proxy.py` (rewrite the module docstring and `_coord`), `tests/test_uploader_manifest_site_routing.py`, `tests/test_uploader_manifest_merge.py`

**Interfaces:**
- Produces: `apply_proxying(pipelines: list, coord: Coordination) -> list` (same signature; ignores `wsprnet_proxy`); `drop_server_delivered_pipelines(pipelines: list, producers: set, *, store_version: int) -> list` (same signature, new guard); module flag `_WARNED_PROXY_RETIRED: bool`.  `_Proxied` loses `enabled_attr`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_uploader_manifest_proxy.py` delete `test_off_by_default_changes_NOTHING` and `test_off_emits_NOTHING_not_false`; keep the carrier, malformed-carrier, credit, wsprnet_call and no-mutation tests with `_coord()` now taking no `proxy` argument.  Add:

```python
def test_every_station_flags_the_tar_and_drops_wsprnet():
    out = apply_proxying(_pipes(), _coord())
    assert "wspr-wsprnet" not in _names(out)
    tar = next(p for p in out if p["name"] == "wspr-wsprdaemon")
    assert tar["transport"]["forward_to_wsprnet"] is True

def test_wsprnet_proxy_false_is_ignored():
    out = apply_proxying(_pipes(), _coord(proxy_key=False))
    assert "wspr-wsprnet" not in _names(out)

def test_a_set_wsprnet_proxy_warns_once_per_process(caplog, monkeypatch):
    monkeypatch.setattr(um, "_WARNED_PROXY_RETIRED", False)
    apply_proxying(_pipes(), _coord(proxy_key=True))
    apply_proxying(_pipes(), _coord(proxy_key=True))
    hits = [r for r in caplog.records if "wsprnet_proxy" in r.getMessage()
            and "retired" in r.getMessage()]
    assert len(hits) == 1 and hits[0].levelname == "WARNING"
```

`_coord(proxy_key=None)` builds `Station(wsprnet_proxy=bool(proxy_key))`.  Keep `test_A_MISSING_CARRIER_KEEPS_THE_DIRECT_PIPELINE` asserting `wspr-wsprnet` stays and an ERROR record names `wspr-wsprdaemon`.

In `tests/test_uploader_manifest_site_routing.py` add to the drop class:

```python
def test_no_carrier_keeps_the_direct_psk_pipelines(self):
    pipes = [{'name': PSK_OWN}, {'name': MS_OWN}]
    with self.assertLogs('sigmond.uploader_manifest', 'ERROR') as cm:
        out = um.drop_server_delivered_pipelines(
            pipes, {'psk-recorder', 'meteor-scatter'},
            store_version=um.SPLIT_STORE_VERSION)
    self.assertEqual([p['name'] for p in out], [PSK_OWN, MS_OWN])
    self.assertIn('wspr-wsprdaemon', '\n'.join(cm.output))
```

Existing drop tests gain a `{'name': 'wspr-wsprdaemon'}` entry in their input lists so the carrier stands.

In `tests/test_uploader_manifest_merge.py` add:

```python
def test_a_merged_proxied_station_renders_as_before(monkeypatch, tmp_path, units):
    # deploy.toml declares wspr-wsprdaemon and wspr-wsprnet; receivers
    # [_rx("KFS/SE", merge_into="KFS"), _rx("KFS/NW", merge_into="KFS")]
    # pinned expectation (what today's code renders with wsprnet_proxy=true):
    # names == ["wspr-wsprdaemon"]; tar transport == {"type": "wsprdaemon_tar",
    #   "forward_to_wsprnet": True, "wsprnet_reporter_call": "KFS",
    #   "wsprnet_reporter_grid": "CN87", "psk_reporter_call": "KFS",
    #   "psk_reporter_grid": "CN87"}
```

Write this test against the pre-change code first with `Station(wsprnet_proxy=True)` and confirm it passes; then switch `_coord()` to leave the key unset and it must still pass after Step 3.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_uploader_manifest_proxy.py tests/test_uploader_manifest_site_routing.py tests/test_uploader_manifest_merge.py -q`
Expected: FAIL (`wspr-wsprnet` still rendered without the key; no carrier guard).

- [ ] **Step 3: Implement**

`apply_proxying`: drop the `enabled_attr` test; every call flags the carrier and drops the direct pipeline, keeping the "two halves" guard, its ERROR text (reworded to `"the %s pipeline is not being rendered — keeping %s so this station still posts for itself; nothing would post otherwise"`), the `wsprnet_call` warning and the no-identity comment.  When `coord.station.wsprnet_proxy` reads true and `_WARNED_PROXY_RETIRED` is false, log once at WARNING: `"uploader-manifest: [station] wsprnet_proxy is retired and ignored — every station's WSPR spots reach wsprnet.org through wsprdaemon.org"`.  Rewrite the docstring: the standard, the guard, and why no `false` ever renders (the flag now always reads true).

`drop_server_delivered_pipelines`: when `producers` is non-empty and no pipeline named `MERGE_CARRIER` (`"wspr-wsprdaemon"`) stands in `pipelines`, log one ERROR per producer, `"uploader-manifest: %s delivers via the WD servers but no wspr-wsprdaemon pipeline carries its rows — keeping its direct pipeline; enable wspr-recorder"`, and return `pipelines` unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_uploader_manifest*.py tests/test_uploader_render_forms.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/uploader_manifest.py tests/test_uploader_manifest_proxy.py tests/test_uploader_manifest_site_routing.py tests/test_uploader_manifest_merge.py
git commit -m "uploader manifest: every station's WSPR reaches wsprnet via wsprdaemon.org

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z"
```

---

### Task 3: The migration, `uniform_routing.migrate_routing`

**Files:**
- Create: `lib/sigmond/uniform_routing.py`
- Test: `tests/test_uniform_routing.py`

**Interfaces:**
- Consumes: `upload.STANDARD_ROUTE`, `upload.UPLOAD_ENABLE`, `upload.env_path_for`, `upload.set_env_flag`, `upload.is_truthy` (Task 1); `inprocess_senders._psk_delivery_pipelines`, `_ms_delivery_mode`, `_instance_units`, `_systemctl_show`; `sink_levers.restart_units`, `RadiodMoved`, `RestartFailed`, `_instance_of`; `sink_state.SinkState`, `below_upload`, `UPLOAD`.
- Produces:
  - `ROUTED_CLIENTS = ("psk-recorder", "meteor-scatter")`; `CARRIER_CLIENT = "wspr-recorder"`
  - `via_server(client: str, env: dict) -> bool` — psk: `_psk_delivery_pipelines(env) == ("server-merge",)`; meteor: `_ms_delivery_mode(env) == "deposit"`.
  - `lowered_from_state(state: SinkState) -> frozenset[str]` — `below_upload(state)`, plus each of `ROUTED_CLIENTS` absent from `state.clients` while `state.site.state != UPLOAD`.
  - `@dataclass class RoutingResult` with fields `applied: bool`, `changed: list[tuple[str, str, str, str]]` (unit, key, old, new; old reads `"unset"` when absent), `restarted: list[str]`, `lowered: list[tuple[str, str]]` (unit, "KEY=value"), `upload_off: list[str]`, `already: list[str]`, `no_carrier: list[str]`, `unreadable: list[tuple[str, str]]`, `failures: list[tuple[str, str]]`, `undecided: str = ""`; method `lines() -> list[str]`; property `ok -> bool` (no failures, no_carrier, unreadable or undecided).
  - `migrate_routing(topology, *, lowered: Optional[frozenset], apply: bool = True, etc: Path = Path("/etc"), instance_units=ips._instance_units, show=ips._systemctl_show, restart=sink_levers.restart_units, undecided_why: str = "") -> RoutingResult`

Rules `migrate_routing` follows, in order, for each enabled client in `ROUTED_CLIENTS` and each unit `instance_units(client, topology)` returns:

1. `lowered is None`: touch nothing; `undecided = undecided_why or "no reason given"`; return at once.
2. `show(unit)` raises `OSError`: add to `unreadable`.
3. `via_server(client, env)`: add to `already`.
4. `client in lowered`: add to `lowered` with the effective value (`server-raw` / `off`).
5. The upload flag (`UPLOAD_ENABLE[client][0]`) does not read true: add to `upload_off`.
6. `CARRIER_CLIENT` not enabled in topology: add to `no_carrier`.
7. Otherwise, with `apply`: `set_env_flag(env_path_for(client, _instance_of(unit), base=str(etc)), key, value)`; an `OSError`/`ValueError` goes to `failures` as `"not written: …"`.  Re-read `show(unit)`; if `via_server` still reads false, add `(path, f"written, but {unit}'s environment still overrides {key}; not restarted")` to `failures`.  Otherwise record in `changed` and queue the unit.  Without `apply`, record in `changed` only.

Then, with `apply`, `restart(queued)` once; `RadiodMoved` and `RestartFailed` extend `failures` with their `.failures` (and `RadiodMoved` adds its message); `restarted` lists the queued units not in a failure.  An enumeration `OSError` from `instance_units` adds `(client, f"cannot enumerate its instances: {exc}")` to `unreadable`.

Report lines (`lines()`), exact text, in this order:

```
routing: cannot read the sink switches ({undecided}); no instance changed
routing: {unit}: {key} {old} -> {new}                  (applied)
routing: would set {unit}: {key} {old} -> {new}        (dry run)
routing: restarted {unit}
routing: left alone {unit} ({KEY=value}: its sink reads below upload; `smd sink upload` routes it)
routing: left alone {unit} (its upload is off)
routing: ERROR {unit} posts from this station: no wspr-recorder is enabled to carry its spots to wsprdaemon.org; left as it stands (smd enable wspr-recorder)
routing: cannot read {unit}'s environment ({why}); left as it stands
routing: FAILED {what}: {why}
routing: {n} instance(s) already deliver via wsprdaemon.org
```

When no unit was seen at all and nothing is undecided, `lines()` returns `["routing: no psk-recorder or meteor-scatter instance on this host"]`.

- [ ] **Step 1: Write the failing tests** in `tests/test_uniform_routing.py`.  Fixtures: a `Topo(enabled)` with `enabled_components()`; env files under `tmp_path/etc/<client>/env/<KEY>.env`; `show` reads the unit's env file into a dict (`KEY=VAL` lines) or raises `OSError` for units listed in a `broken` set; `restart` records its argument.

```python
def test_direct_psk_moves_to_server_merge_and_restarts(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="direct")
    res = rig.run(["wspr-recorder", "psk-recorder"], lowered=frozenset())
    assert "PSK_DELIVERY_PIPELINES=server-merge" in rig.text("psk-recorder", "AC0G-S")
    assert res.restarted == ["psk-recorder@AC0G-S.service"]
    assert "routing: psk-recorder@AC0G-S.service: PSK_DELIVERY_PIPELINES direct -> server-merge" in res.lines()

def test_legacy_both_and_server_raw_at_upload_move(rig): ...
    # PSK_DELIVERY_MODE=both (no PIPELINES) and PIPELINES=server-raw each end server-merge

def test_meteor_unset_and_off_at_upload_move_to_deposit(rig): ...
    # old shows "unset" for the unset instance

def test_a_lowered_client_is_left_alone_and_reported(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="server-raw")
    before = rig.text("psk-recorder", "AC0G-S")
    res = rig.run(["wspr-recorder", "psk-recorder"],
                  lowered=frozenset({"psk-recorder"}))
    assert rig.text("psk-recorder", "AC0G-S") == before
    assert res.restarted == [] and rig.restarts == []
    assert any("left alone psk-recorder@AC0G-S.service (PSK_DELIVERY_PIPELINES=server-raw" in l
               for l in res.lines())

def test_unknown_sink_state_changes_nothing(rig):
    res = rig.run([...], lowered=None, undecided_why="hs-uploader timed out")
    assert res.lines() == ["routing: cannot read the sink switches (hs-uploader timed out); no instance changed"]
    assert not res.ok and rig.restarts == []

def test_no_carrier_leaves_every_instance_and_says_error(rig):
    # enabled = ["psk-recorder", "meteor-scatter"]; both direct; files unchanged,
    # no restart, two "routing: ERROR" lines, res.ok is False

def test_upload_off_instance_is_left_alone(rig): ...

def test_unreadable_env_is_named_and_untouched(rig):
    # two psk instances, one in rig.broken; the readable one moves and restarts,
    # the broken one's file is unchanged and named in "cannot read ..."

def test_an_override_that_still_posts_is_a_failure_not_a_restart(rig):
    # show() for this unit always returns PSK_DELIVERY_PIPELINES=direct
    # whatever the file says; res.failures names it; rig.restarts == []

def test_merged_instances_already_routed_change_nothing(rig):
    # server-merge + deposit instances: no write (mtime unchanged), no restart,
    # lines() == ["routing: 2 instance(s) already deliver via wsprdaemon.org"]

def test_a_second_run_changes_nothing(rig):
    # run once (moves and restarts), run again: changed == [], restarted == [],
    # lines() == ["routing: 1 instance(s) already deliver via wsprdaemon.org"]

def test_dry_run_writes_and_restarts_nothing(rig):
    # apply=False: "routing: would set ..." line, files unchanged, no restart

def test_radiod_moved_is_a_failure(rig):
    # restart raises sink_levers.RadiodMoved("radiod moved", failures=[]); res.ok False

def test_lowered_from_state_counts_a_missing_client_under_a_lowered_site():
    # SinkState(site.state="fill", clients={"wspr-recorder": effective "fill"})
    # -> {"wspr-recorder", "psk-recorder", "meteor-scatter"};
    # site "upload", clients {"psk-recorder": effective "off"} -> {"psk-recorder"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_uniform_routing.py -q`
Expected: FAIL with `ModuleNotFoundError: sigmond.uniform_routing`

- [ ] **Step 3: Implement `lib/sigmond/uniform_routing.py`** per the Interfaces and rules above.  Module docstring: the standard in three sentences, the carrier rule, and why a lever value stays (no undo record; the sink state decides).  Stdlib only; import `sink_state` lazily inside `lowered_from_state` (it imports `uploader_manifest`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_uniform_routing.py tests/test_sink_levers.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/uniform_routing.py tests/test_uniform_routing.py
git commit -m "uniform_routing: move running psk/meteor instances onto the wsprdaemon.org route

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z"
```

---

### Task 4: Run the migration from `smd sink upload`, `smd update --apply` and a new verb

**Files:**
- Modify: `lib/sigmond/commands/uploader.py` (new `cmd_uploader_routing`, `routing_lowered`)
- Modify: `lib/sigmond/commands/config.py:1554-1706` (`cmd_config_uploads`, part 1 path) and `:1919-2030` (`_site_change`, gate path); new helper `_route_via_wsprdaemon`
- Modify: `bin/smd:31046-31063` (parser: `admin uploader routing`), the `admin uploader` dispatch, `:5972-5987` (`cmd_update` tail)
- Test: `tests/test_uploader_routing_cmd.py` (new), `tests/test_uploader_cmd_policy.py`

**Interfaces:**
- Consumes: `uniform_routing.migrate_routing`, `lowered_from_state`, `RoutingResult` (Task 3); `sink_state.read_state`, `marker_stands`, `update_notice`; `um.hs_uploader_features`, `um.GATE_FEATURE`; `up_cmd._step_roots`, `_relayed_stderr`, `_cause`; `_run_smd_child` in `bin/smd`.
- Produces:
  - `commands.uploader.routing_lowered(roots, coord) -> tuple[Optional[frozenset], str]` — with the gate offered and the marker standing: `lowered_from_state(read_state(...))`, or `(None, cause)` when the read fails.  Otherwise: `frozenset()` when `coord.uploads.mode == "upload"`, else `frozenset(uniform_routing.ROUTED_CLIENTS)`.
  - `commands.uploader.cmd_uploader_routing(args) -> int` — `args.apply: bool`.  Dry run prints `migrate_routing(..., apply=False).lines()` and returns 0.  `--apply` needs root (refuse with `_err` and 1 otherwise, as `cmd_uploader_manifest` does), migrates, prints the lines, then: when `sink_state.update_notice()` returns a line, prints `"routing: the manifest render waits for the next `smd align` (no carry-over marker yet)"` and skips the render; otherwise runs `cmd_uploader_manifest(SimpleNamespace(write=True, enable=False))`.  Returns 1 when `not result.ok` or the render fails.
  - `commands.config._route_via_wsprdaemon(lowered: Optional[frozenset], why: str = "") -> int` — runs `migrate_routing(load_topology(), lowered=lowered, undecided_why=why)`, prints each line (`err` for lines holding `ERROR`, `FAILED` or `cannot read`, `info` otherwise), returns `0 if result.ok else 1`.  Isolated so tests stub it.

- [ ] **Step 1: Write the failing tests** in `tests/test_uploader_routing_cmd.py`:

```python
def test_sink_upload_routes_before_the_manifest_step(monkeypatch):
    # part 1 path (no gate): stub _regenerate_uploader_manifest and
    # _route_via_wsprdaemon to append to `calls`; cmd_config_uploads(verb "on")
    # -> calls == ["route:frozenset()", "manifest"]

def test_sink_upload_with_the_gate_routes_with_the_change_below(monkeypatch):
    # _site_change(mode="upload") with a stubbed state where meteor-scatter's
    # own file reads fill: _route_via_wsprdaemon got frozenset({"meteor-scatter"}),
    # and it ran before _regenerate_uploader_manifest

def test_sink_upload_with_an_unreadable_state_routes_nothing(monkeypatch):
    # _site_status returns None -> _route_via_wsprdaemon(None, ...) and rc 1

def test_sink_fill_never_routes(monkeypatch):
    # cmd_config_uploads verbs "hold" and "discard" (both paths): the stub
    # _route_via_wsprdaemon is never called

def test_routing_verb_dry_run_writes_nothing(monkeypatch, capsys):
    # migrate_routing stub asserts apply is False; rc 0; no manifest call

def test_routing_verb_apply_renders_after_migrating(monkeypatch):
    # euid 0, update_notice -> None: calls == ["migrate", "manifest write"]

def test_routing_verb_defers_the_render_without_a_marker(monkeypatch, capsys):
    # update_notice -> a line: no manifest call; stdout names `smd align`

def test_update_apply_runs_the_routing_child(monkeypatch):
    # load bin/smd as tests/test_update_sink_notice.py does;
    # stub the step loop to one applied pull and _run_smd_child to record argv:
    # recorded == [["admin", "uploader", "routing", "--apply"]]; a non-zero
    # child return makes cmd_update exit 1

def test_update_dry_run_never_routes(monkeypatch): ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_uploader_routing_cmd.py -q`
Expected: FAIL (`_route_via_wsprdaemon` and `cmd_uploader_routing` not defined)

- [ ] **Step 3: Implement**

`cmd_config_uploads`, part 1 path: when `mode == 'upload'`, call `_route_via_wsprdaemon(*routing_lowered(roots, load_coordination(COORDINATION_PATH)))` after `_patch_uploads_block` and before `_regenerate_uploader_manifest()`; fold its return into the exit code.  `_site_change`: when `new == 'upload'`, call it after step 4 and `_raise_report`, before step 5, with `frozenset(change.below)` when `change` stands, `(None, "the sink status could not be read")` when the marker stands but `state` is None, and `frozenset()` when no marker stands yet.  Update the `_site_change` docstring's numbered steps (4a: route via wsprdaemon.org).

`bin/smd`: add `routing` under `admin uploader` with `--apply` (help: `'move psk-recorder and meteor-scatter instances onto the wsprdaemon.org route, then render the manifest; dry run without --apply'`) and dispatch it to `cmd_uploader_routing`.  In `cmd_update`, after the step loop and before the history pointer, when `apply_` and not `failed`: `rc = _run_smd_child(['admin', 'uploader', 'routing', '--apply'], 'sigmond', 'routing')`; a non-zero `rc` adds 1 to `failed` and prints `_err(f'routing: exit {rc}; re-run: sudo smd admin uploader routing --apply')`.  The child runs the smd on disk, so the update that ships this code also runs it (this process preloaded the old modules).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_uploader_routing_cmd.py tests/test_uploader_cmd_policy.py tests/test_uploader_manifest_step.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/commands/uploader.py lib/sigmond/commands/config.py bin/smd tests/test_uploader_routing_cmd.py
git commit -m "sink upload, update --apply and admin uploader routing move stations onto the wsprdaemon.org route

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z"
```

---

### Task 5: Retire `wsprnet_proxy` in the config verbs and the coordination render

**Files:**
- Modify: `lib/sigmond/commands/config.py:1115-1170` (`cmd_config_reporters`), `:885-900` (render builds `Station`), `:1252-1280` (`_patch_station_block`)
- Modify: `lib/sigmond/coordination.py:46-58` (field comment), `:510-515` (`write_station_identity`)
- Modify: `lib/sigmond/site_profile.py:60-69` (template), `:136-144` (field comment)
- Modify: `bin/smd:31750-31760` (`config reporters` help)
- Modify: `README.md:489`
- Test: `tests/test_uploader_call.py`, `tests/test_coordination.py`

**Interfaces:**
- Consumes: nothing new.  `Station.wsprnet_proxy` and `SiteProfile.wsprnet_proxy` stay as read-only fields so old files still load.

- [ ] **Step 1: Write the failing tests**

```python
def test_reporters_wsprnet_proxy_writes_nothing_and_says_why(tmp_path, capsys):
    # site-profile with [reporters] uploader_call = "X"; args.wsprnet_proxy="on",
    # no uploader_call, no_render=True -> rc 0, file bytes unchanged, stderr holds
    # "wsprnet_proxy is retired" and "wsprdaemon.org"

def test_reporters_wsprnet_proxy_beside_uploader_call_writes_only_the_name(...):
    # both given: uploader_call written, no wsprnet_proxy line added or removed

def test_render_never_writes_wsprnet_proxy(tmp_path):
    # _patch_station_block with Station(wsprnet_proxy=True) -> no
    # "wsprnet_proxy" in the written [station] block

def test_write_station_identity_drops_an_old_wsprnet_proxy(tmp_path):
    # coordination.toml [station] holds wsprnet_proxy = true; after
    # write_station_identity(reporter_id="AC0G/B4") the key is gone

def test_render_warns_once_about_a_profile_wsprnet_proxy(tmp_path, capsys):
    # profile [reporters] wsprnet_proxy = true; cmd_config_render prints one
    # warn line naming the key as retired and telling the operator to delete it

def test_profile_template_has_no_wsprnet_proxy():
    # "wsprnet_proxy" not in site_profile's template text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_uploader_call.py tests/test_coordination.py -q -k "wsprnet_proxy or template"`
Expected: FAIL

- [ ] **Step 3: Implement**

`cmd_config_reporters`: `--wsprnet-proxy` no longer enters `keys`; it prints `warn("--wsprnet-proxy is retired and writes nothing: every station's WSPR spots reach wsprnet.org through wsprdaemon.org")`.  With `--wsprnet-proxy` alone, return 0 after that line.  The success line drops `wsprnet_proxy=`.  Docstring and `bin/smd` help name only `--uploader-call` and say the flag is retired.  The render path: `Station(..., wsprnet_proxy=False)` always, and one `warn` when `profile.wsprnet_proxy` reads true.  `_patch_station_block` and `write_station_identity` stop writing the key (delete the two blocks and their comments).  Remove `wsprnet_proxy` and its comment from the site-profile template; field comments in both dataclasses say "retired 2026-11-13; read so old files load, ignored".  `README.md:489` becomes `smd config reporters [--uploader-call NAME]` with the description "The name this machine uploads under".

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q`
Expected: PASS (whole suite: Tasks 1-5 together)

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/commands/config.py lib/sigmond/coordination.py lib/sigmond/site_profile.py bin/smd README.md tests/
git commit -m "reporters: wsprnet_proxy retired; render stops writing it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z"
```

---

### Task 6: sigmond CLAUDE.md states the standard

**Files:**
- Modify: `CLAUDE.md:326-387` ("Proxied uploads" section)

- [ ] **Step 1: Rewrite the section** under the heading `## Uniform routing through wsprdaemon.org`.  Keep the measured table and the fleet arithmetic.  Replace the opt-in text with the standard:
  - Every WSPR, FT4/FT8 and MSK144 spot goes only to wsprdaemon.org, which stores it and forwards copies to wsprnet.org and pskreporter.info under the spot's own reporter id.  No station posts to either itself.  Only a merge (`merge_into`) rewrites the reporter id, and the server carries it out.
  - What enforces it: `smd config upload --on` writes `server-merge` / `deposit` and refuses `direct`, `server-raw`, `both` and meteor `off`; `apply_proxying` flags every tar and never renders `wspr-wsprnet`; `uniform_routing.migrate_routing` moves running instances, run by `smd sink upload`, `smd update --apply` and `smd admin uploader routing --apply`.
  - Keep the three rules, revised: the two halves move together (now also for psk/meteor, whose carrier is the same tar); a lever value below `upload` stays, and the sink state, not a record, tells it from a choice; no identity is sent, and `wsprnet_call` stays unread.
  - Drop the "operator's decision, never a site-profile default" paragraph.  Say instead that `[reporters] wsprnet_proxy` and `smd config reporters --wsprnet-proxy` retired on 2026-11-13 and that sigmond reads the key, ignores it and warns once.
  - Name the interim: the server forwards only flagged rows until Rob's change lands, so sigmond keeps setting the per-row flags.

Follow the author's two rules (few forms of *to be*, active voice with a real agent) and keep sentences short.

- [ ] **Step 2: Verify no stale guidance remains**

Run: `grep -n "wsprnet_proxy\|operator's decision\|--via direct\|standalone direct" CLAUDE.md docs/greenfield-runbook.md README.md`
Expected: only the retirement sentence in `CLAUDE.md`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "CLAUDE.md: uniform routing through wsprdaemon.org replaces the opt-in proxy

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z"
```
