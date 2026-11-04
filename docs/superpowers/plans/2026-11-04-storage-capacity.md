# Storage Capacity Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give sigmond a capacity ledger. It measures every store a station depends on, the host thin pool included, and divides the space among clients. It climbs a four-stage ladder of alarms, trims, clean-up, client final policies and, as the last lever, stopping a client. Its aim: no station ever again stops on a full disk or a full pool.

**Architecture:** A new stdlib-only package `lib/sigmond/storage/` holds pure decision code: the model, the allotment arithmetic and the ladder. Thin, injectable runners do the measuring and acting. A 2-minute timer runs `smd storage tick`. Each tick writes `/run/sigmond/storage-status.json`, which `smd storage status` and the heartbeat both read. On the Proxmox host, a separate stdlib script, `scripts/proxmox/pm-storage.py`, reports the thin pool into the guest through the guest agent. It also reaps the expired snapshots that sigmond's own tools took, and raises the host's own alarm. Clients join through a contract revision (v0.9): new `data_sinks` fields plus three CLI verbs, `storage holdings|trim|final`.

**Tech Stack:** Python 3.11 stdlib (`os.statvfs`, `tomllib`, `subprocess`, `json`, `fcntl`), systemd timers, Proxmox `lvs`/`pvesh`/`qm`, pytest via `.venv/bin/pytest`.

**Spec:** `docs/superpowers/specs/2026-11-04-storage-capacity-design.md`

## Global Constraints

- The core stays stdlib-only. `scripts/proxmox/pm-storage.py` imports nothing from `sigmond`; constants it shares get a drift-guard test, as `pm-heartbeat.py` does.
- Classes, most valuable first: `pinned`, `raw`, `derived`, `sent`. Trims consume from the end.
- Default stage levels, as percent of true capacity: Watch 80, Tighten 85, Demand 90, Final 95. A client also reaches Watch when it holds more than 90 % of its allotment.
- Headroom = 100 − final level (5 %), with a floor of 2 GiB on guest and archive stores and 10 GiB on a thin pool.
- Reserve for state: 1 GiB, on the root store only.
- Allotments split evenly per **client** (not per sink or instance) until an operator sets shares. Floors come first. When floors no longer fit, raise a critical alarm and go straight to Final.
- sigmond never deletes a client's files. Its clean-up covers only the journal, the apt cache, and files under `/var/lib/sigmond/tmp`.
- Stop lever: only at Final; only against a client that failed to answer a trim request (an error, a timeout or non-JSON); or against an unmanaged client still over its target 900 s after Final began for it. Never on a station marked hands-off; there it alarms only. Never against `radiod`/`ka9q-radio`. Stopping deletes nothing, and sigmond never restarts the client itself.
- A stale (> 900 s) or missing host report counts as a fault and raises an alarm, never "unknown". The report is expected only when `systemd-detect-virt` names a VM and `[pool] expect_report` is not `false`.
- Snapshots: the host deletes automatically only those whose description carries `sigmond-snapshot expires=<ISO-8601 UTC>`, once past expiry, or at once when the pool reaches the Demand stage (90 %). It never deletes `pristine`, nor any operator snapshot; it alarms about operator snapshots instead. A snapshot's lifetime is half the pool's projected time to full (free ÷ measured churn), capped at 48 h (Michael, 2026-11-04: the 11-03 pools filled in ~29 h). Taking a snapshot is refused when pool data ≥ 70 %.
- Alarms follow the persistent-condition rule: one line when an episode starts, one when it clears, and a reminder every 6 h while it lasts. Never a line per tick.
- Hands-off defaults to 24 h, with a maximum of 14 days. A raised log level expires after 24 h unless `--for` says otherwise; sigmond restores the routine level and sends SIGHUP.
- No byte above 0x7F in anything sent through the guest agent (`json.dumps(..., ensure_ascii=True)`).
- Work on `main` (repo convention). Tests run with `.venv/bin/pytest`.

## Rulings made while planning (check these)

1. **Transport: the CLI, not §13 sockets.** The spec's call was "the same surface as §13". But `lib/sigmond/control_socket.py` records that no client serves a §13 socket. So the trim, final and holdings calls become `<client> storage …` CLI verbs, like `inventory --json`. Task 3 updates the spec line.
2. **sigmond, not the client, reverts an expired log level.** It already writes `coordination.env` and sends SIGHUP (§11), so clients need no change.
3. **Clean-up of stale mounts reports only.** Unmounting something sigmond cannot prove it made could break an operator's work, so the "unclaimed" report lists stale mounts and leaves them alone.
4. **An unmanaged client gets a 900 s grace at Final before the stop lever applies.** During migration that gives hf-timestd's own guardian, which evicts at 95 % with a 600 s grace, the first chance.
5. **hf-timestd and hs-uploader adopt in their own plans.** This plan builds sigmond's side and the host side. Until a client adopts, the ledger counts it as unmanaged, measured with `du`.

## Review Focus

1. **A bare-metal station, or one with no PM reporter.** The ledger must expect no host report there and must not alarm forever. Test in Task 4.
2. **A store vanishes mid-life** (archive drive pulled, `statvfs` raises). The store gets marked missing with an alarm, and the tick completes. Test in Task 4.
3. **A malformed `storage.toml`** (levels out of order, an unparseable hands-off date, a negative share). The ledger falls back to defaults per bad key, raises an alarm, and never crashes. Test in Task 2.
4. **A client CLI that hangs, exits non-zero or prints non-JSON on `storage trim`.** That counts as a failed answer; the tick finishes inside its timeout budget. Test in Task 5.
5. **Overlapping ticks, or a tick after reboot with old state.** A second tick skips on the flock. Trim records older than 3600 s get dropped, so a stale record can't trigger a stop. Test in Task 9.

---

### Task 1: Model and allotment arithmetic

**Files:**
- Create: `lib/sigmond/storage/__init__.py` (empty docstring module), `lib/sigmond/storage/model.py`
- Test: `tests/test_storage_model.py`

**Interfaces:**
- Produces:
  - `CLASSES: tuple[str, ...] = ("pinned", "raw", "derived", "sent")`
  - `class Stage(IntEnum): OK=0, WATCH=1, TIGHTEN=2, DEMAND=3, FINAL=4`
  - `@dataclass(frozen=True) class Levels: watch: float = 80.0; tighten: float = 85.0; demand: float = 90.0; final: float = 95.0; client_watch: float = 90.0`
  - `@dataclass(frozen=True) class Store: name: str; mount: str; kind: str  # "guest"|"archive"|"pool"; capacity: int; used: int` with property `used_pct -> float`
  - `@dataclass(frozen=True) class Registration: client: str; store: str; paths: tuple[str, ...]; floor_bytes: int; policy: Optional[str]; managed: bool`
  - `@dataclass(frozen=True) class Holding: client: str; store: str; bytes_by_class: Mapping[str, int]; estimated: bool` with property `total -> int`
  - `@dataclass(frozen=True) class Allotments: per_client: Mapping[str, int]; floors_fit: bool; allottable: int`
  - `HEADROOM_FLOOR_BYTES = {"guest": 2 << 30, "archive": 2 << 30, "pool": 10 << 30}`; `STATE_RESERVE_BYTES = 1 << 30`
  - `headroom_bytes(store: Store, levels: Levels) -> int`
  - `allottable_bytes(store: Store, levels: Levels, registered_bytes: int, state_reserve: int) -> int`
  - `pool_room_bytes(pool: Store, levels: Levels, share: float) -> int`
  - `apply_pool_limit(allottable: int, registered_bytes: int, pool_room: int) -> int`
  - `allot(registrations: Sequence[Registration], allottable: int, shares: Mapping[str, float]) -> Allotments`

- [ ] **Step 1: Write the failing tests**

```python
GiB = 1 << 30
def test_headroom_is_space_above_final_with_a_floor():
    assert headroom_bytes(Store("root", "/", "guest", 100 * GiB, 0), Levels()) == 5 * GiB
    assert headroom_bytes(Store("root", "/", "guest", 20 * GiB, 0), Levels()) == 2 * GiB
    assert headroom_bytes(Store("pool", "pve/data", "pool", 100 * GiB, 0), Levels()) == 10 * GiB

def test_unregistered_consumers_shrink_the_allottable_space():
    s = Store("root", "/", "guest", 100 * GiB, 60 * GiB)
    # 60 used, 40 of it registered -> 20 unregistered; 100 - 1 - 5 - 20 = 74
    assert allottable_bytes(s, Levels(), registered_bytes=40 * GiB, state_reserve=GiB) == 74 * GiB
    assert allottable_bytes(Store("r", "/", "guest", 10 * GiB, 10 * GiB), Levels(), 0, GiB) == 0

def test_pool_room_is_this_vms_share_of_space_below_final():
    pool = Store("pool", "pve/data", "pool", 200 * GiB, 150 * GiB)
    assert pool_room_bytes(pool, Levels(), share=0.5) == 20 * GiB   # (190 - 150) * 0.5
    assert pool_room_bytes(Store("pool", "p", "pool", 100 * GiB, 99 * GiB), Levels(), 1.0) == 0

def test_a_filling_pool_caps_growth_not_holdings():
    assert apply_pool_limit(allottable=74 * GiB, registered_bytes=40 * GiB, pool_room=10 * GiB) == 50 * GiB
    assert apply_pool_limit(74 * GiB, 40 * GiB, 100 * GiB) == 74 * GiB

def test_even_split_above_floors():
    regs = [Registration("a", "root", ("/a",), 10 * GiB, None, True),
            Registration("b", "root", ("/b",), 0, None, False)]
    a = allot(regs, 50 * GiB, {})
    assert a.per_client == {"a": 30 * GiB, "b": 20 * GiB} and a.floors_fit

def test_operator_shares_weight_the_split():
    regs = [Registration("a", "root", (), 0, None, True), Registration("b", "root", (), 0, None, True)]
    assert allot(regs, 30 * GiB, {"a": 2.0}).per_client == {"a": 20 * GiB, "b": 10 * GiB}

def test_floors_that_no_longer_fit_say_so():
    regs = [Registration("a", "root", (), 30 * GiB, None, True), Registration("b", "root", (), 30 * GiB, None, True)]
    a = allot(regs, 50 * GiB, {})
    assert not a.floors_fit and a.per_client == {"a": 30 * GiB, "b": 30 * GiB}

def test_one_client_with_several_registrations_gets_one_share():
    regs = [Registration("a", "root", ("/x",), GiB, None, True), Registration("a", "root", ("/y",), GiB, None, True),
            Registration("b", "root", (), 0, None, True)]
    assert set(allot(regs, 20 * GiB, {}).per_client) == {"a", "b"}
```

- [ ] **Step 2: Run them; expect ImportError.** `.venv/bin/pytest tests/test_storage_model.py -q`
- [ ] **Step 3: Implement `model.py`.** Integer arithmetic throughout, flooring at 0. `allot` sums the floors per client, so one client with several registrations has one floor, the sum of them. It divides the remainder by weight; `shares.get(client, 1.0)`, and a weight ≤ 0 counts as 1.0. Any rounding remainder goes to the first client in sorted order.
- [ ] **Step 4: Run them; expect PASS.**
- [ ] **Step 5: Commit** `storage: model and allotment arithmetic`

---

### Task 2: `storage.toml`, directives and hands-off

**Files:**
- Create: `lib/sigmond/storage/config.py`
- Test: `tests/test_storage_config.py`

**Interfaces:**
- Consumes: `Levels` (Task 1).
- Produces:
  - `CONFIG_PATH = Path("/etc/sigmond/storage.toml")`
  - `POLICY_RE = r"^(give-up-oldest:(pinned|raw|derived|sent)|stop-writing|refuse-new-rows|custom:[a-z0-9-]+)$"`
  - `@dataclass(frozen=True) class StorageConfig: levels: Levels; shares: Mapping[str, float]; directives: Mapping[str, str]; hands_off_until: Optional[datetime]; hands_off_reason: str; pool_share: Optional[float]; expect_host_report: Optional[bool]; problems: tuple[str, ...]`
  - `load_storage_config(path: Path = CONFIG_PATH) -> StorageConfig`. It never raises; a missing file gives the defaults.
  - `hands_off_active(cfg: StorageConfig, now: datetime) -> bool`
  - `render_storage_toml(cfg: StorageConfig) -> str` (hand-written TOML; stdlib has no writer)
  - `with_share(cfg, client: str, weight: Optional[float]) -> StorageConfig`. `None` clears.
  - `with_directive(cfg, client: str, policy: Optional[str]) -> StorageConfig`. Raises `ValueError` when the policy fails `POLICY_RE`.
  - `with_hands_off(cfg, until: Optional[datetime], reason: str, now: datetime) -> StorageConfig`. Raises `ValueError` past 14 days.

File shape: `[levels] watch tighten demand final client_watch`, `[shares] <client> = <float>`, `[directives] <client> = "<policy>"`, `[hands_off] until = "<ISO-8601Z>" reason = "…"`, `[pool] share = <0..1> expect_report = <bool>`.

- [ ] **Step 1: Write the failing tests**

```python
def test_missing_file_gives_defaults(tmp_path):
    c = load_storage_config(tmp_path / "none.toml")
    assert c.levels == Levels() and c.problems == () and c.shares == {}

def test_levels_out_of_order_fall_back_and_say_why(tmp_path):
    p = tmp_path / "s.toml"; p.write_text("[levels]\nwatch = 90\ntighten = 85\n")
    c = load_storage_config(p)
    assert c.levels == Levels() and any("levels" in m for m in c.problems)

def test_bad_keys_drop_singly(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('[shares]\na = -1\nb = 2\n[directives]\nx = "delete-everything"\ny = "stop-writing"\n'
                 '[hands_off]\nuntil = "tomorrow"\n')
    c = load_storage_config(p)
    assert c.shares == {"b": 2.0} and c.directives == {"y": "stop-writing"}
    assert c.hands_off_until is None and len(c.problems) == 3

def test_unparseable_toml_gives_defaults_and_a_problem(tmp_path):
    p = tmp_path / "s.toml"; p.write_text("[levels\n")
    assert load_storage_config(p).problems

def test_hands_off_expires(tmp_path):
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    c = with_hands_off(load_storage_config(tmp_path / "n"), now + timedelta(hours=24), "A/B run", now)
    assert hands_off_active(c, now) and not hands_off_active(c, now + timedelta(hours=25))
    with pytest.raises(ValueError):
        with_hands_off(c, now + timedelta(days=15), "", now)

def test_render_round_trips(tmp_path):
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    c = with_directive(with_share(load_storage_config(tmp_path / "n"), "hf-timestd", 2.0), "codar-sounder", "stop-writing")
    c = with_hands_off(c, now + timedelta(hours=3), "test", now)
    p = tmp_path / "s.toml"; p.write_text(render_storage_toml(c))
    assert load_storage_config(p) == c
```

- [ ] **Step 2: Run them; expect FAIL.**
- [ ] **Step 3: Implement `config.py`.** Use `tomllib`. Levels must satisfy watch < tighten < demand < final < 100, and 0 < client_watch ≤ 100; otherwise use the default levels whole, with one problem line. Each bad share, directive or date drops alone, with one problem line each.
- [ ] **Step 4: Run them; expect PASS.**
- [ ] **Step 5: Commit** `storage: storage.toml, directives, hands-off`

---

### Task 3: Registration from inventory, and contract v0.9

**Files:**
- Modify: `lib/sigmond/clients/base.py` (add `DataSink` and `InstanceView.data_sinks`), `lib/sigmond/clients/contract.py` (`_instance_from_contract` parses `data_sinks`)
- Create: `lib/sigmond/storage/registry.py`
- Modify: `docs/CLIENT-CONTRACT.md`: bump the version to 0.9; add §17.6 "Storage capacity (v0.9)"; add an expiry note to §11; add a v0.9 entry under "Migration and versioning"
- Modify: `docs/superpowers/specs/2026-11-04-storage-capacity-design.md`. Replace the designer's call "the same surface as §13's control surface, so no new transport" with: CLI verbs beside `inventory`, because no client serves a §13 socket yet. Replace "The client restores its routine level when it passes" with: sigmond restores the routine level and sends SIGHUP.
- Test: `tests/test_storage_registry.py`

**Interfaces:**
- Consumes: `Registration`, `Store` (Task 1); `ClientView`, `InstanceView`, `DiskWrite` (existing).
- Produces:
  - `@dataclass class DataSink: kind: str; path: str; mb_per_day: float = 0.0; retention_days: float = 0.0; floor_bytes: int = 0; final_policy: Optional[str] = None; classes: tuple[str, ...] = ()`
  - `CONTRACT_STORAGE_VERSION = (0, 9)`
  - `store_for_path(path: str, stores: Sequence[Store]) -> Optional[Store]` (longest `mount` prefix; component-wise, so `/var/lib/sigmond/archive` never matches `/var/lib/sigmond/archived`)
  - `registrations_from_views(views: Mapping[str, ClientView], stores: Sequence[Store]) -> list[Registration]`

A client is **managed** when its `contract_version` ≥ 0.9 and at least one file sink declares `classes`. A file sink without a store match drops out, with a warning in the view's `issues`. `disk_writes` entries are promoted as unmanaged sinks, with floor 0 and no policy. The result holds one `Registration` per (client, store), merging paths and summing floors.

§17.6 text to add, written in Michael's voice (active, few "is"). Each file sink adds `floor_bytes`, `final_policy` (the `POLICY_RE` set), and `classes` (a subset of `CLASSES`). The CLI verbs, each printing JSON on stdout and exiting 0:
- `<client> storage holdings --json` → `{"bytes_by_class": {"raw": n, …}, "estimated": bool}` (client-wide, summed over its sinks)
- `<client> storage trim --fit-bytes N --json` → `{"held_bytes": n, "bytes_by_class": {…}, "refused": bool, "reason": str}`. The client trims in its own order, from `sent` upward, never below its floor.
- `<client> storage final --fit-bytes N [--policy P] --json`, same shape. The client applies `P` when given, else its declared `final_policy`, and may go below its floor.
- Unknown verb → exit 2 (an older client). Holdings answer within 10 s, trim and final within 120 s.

- [ ] **Step 1: Write the failing tests**

```python
def test_store_for_path_takes_the_longest_component_prefix():
    s = [Store("root", "/", "guest", 1, 0), Store("archive", "/var/lib/sigmond/archive", "archive", 1, 0)]
    assert store_for_path("/var/lib/sigmond/archive/iq", s).name == "archive"
    assert store_for_path("/var/lib/sigmond/archived", s).name == "root"

def test_v09_client_with_classes_is_managed():
    view = _view("hf-timestd", "0.9", sinks=[DataSink("file", "/var/lib/timestd/raw", floor_bytes=3 << 30,
                                                      final_policy="give-up-oldest:raw", classes=("raw", "sent"))])
    (r,) = registrations_from_views({"hf-timestd": view}, [Store("root", "/", "guest", 1, 0)])
    assert r.managed and r.floor_bytes == 3 << 30 and r.policy == "give-up-oldest:raw"

def test_disk_writes_promote_as_unmanaged():
    view = _view("codar-sounder", "0.8", disk_writes=[DiskWrite(path="/var/lib/codar", mb_per_day=1.0, retention_days=7)])
    (r,) = registrations_from_views({"codar-sounder": view}, [Store("root", "/", "guest", 1, 0)])
    assert not r.managed and r.floor_bytes == 0 and r.policy is None

def test_two_sinks_on_one_store_merge():
    view = _view("x", "0.9", sinks=[DataSink("file", "/a", floor_bytes=1, classes=("raw",)),
                                    DataSink("file", "/b", floor_bytes=2, classes=("raw",))])
    (r,) = registrations_from_views({"x": view}, [Store("root", "/", "guest", 1, 0)])
    assert r.paths == ("/a", "/b") and r.floor_bytes == 3

def test_inventory_json_parses_data_sinks():
    iv = _instance_from_contract({"instance": "default", "data_sinks": [
        {"kind": "file", "path": "/a", "floor_bytes": 5, "final_policy": "stop-writing", "classes": ["raw"]}]})
    assert iv.data_sinks[0].floor_bytes == 5 and iv.data_sinks[0].classes == ("raw",)
```
(`_view` is a test-local helper that builds a `ClientView` with one `InstanceView`.)

- [ ] **Step 2: Run them; expect FAIL.**
- [ ] **Step 3: Implement** the dataclass, the parser and `registry.py`, then write the contract text and the spec edits.
- [ ] **Step 4: Run them, plus the existing contract and docs tests; expect PASS.** `.venv/bin/pytest tests/test_storage_registry.py tests -q -k "contract or docs or storage_registry"`
- [ ] **Step 5: Commit** `storage: registrations from inventory; client contract v0.9 §17.6`

---

### Task 4: Measuring stores and reading the host report

**Files:**
- Create: `lib/sigmond/storage/measure.py`
- Test: `tests/test_storage_measure.py`

**Interfaces:**
- Consumes: `Store` (Task 1).
- Produces:
  - `measure_store(name: str, mount: str, kind: str, statvfs=os.statvfs) -> Store`. capacity = `f_blocks*f_frsize`; used = capacity − `f_bavail*f_frsize`, so ext4's reserved blocks count as used.
  - `discover_stores(paths: Sequence[str], archive_mount: str, proc_mounts: str = "/proc/mounts", statvfs=os.statvfs) -> tuple[list[Store], list[str]]`. It returns the stores and the faults. Root is always `"root"`/`"guest"`. The archive mount, when mounted, becomes `"archive"`. Any other mount holding a registered path becomes `"mnt:<mountpoint>"`/`"guest"`. A `statvfs` failure leaves the store out and adds a fault line naming it.
  - `path_bytes(paths: Sequence[str]) -> int`. It sums `st_blocks*512`, does not follow symlinks, counts each inode once, and ignores vanished files.
  - `HOST_REPORT_PATH = "/run/sigmond/host-pool.json"`; `HOST_REPORT_MAX_AGE_S = 900`
  - `@dataclass(frozen=True) class HostReport: generated_at: datetime; pool: Store; vms_on_pool: tuple[int, ...]; snapshots: tuple[dict, ...]; host_alarm: str`
  - `read_host_report(path: str, now: datetime) -> tuple[Optional[HostReport], Optional[str]]`. The second value is the fault: `"missing"`, `"stale"` or `"unreadable"`, or None.
  - `host_report_expected(cfg_expect: Optional[bool], detect_virt=_detect_virt) -> bool`. True when cfg says so. False when cfg says false. With no setting, it follows `systemd-detect-virt --vm` (exit 0 and output other than `none`).

Host report JSON (schema 1, written by Task 11): `{"schema": 1, "generated_at": "<ISO Z>", "vmid": 100, "pool": {"name": "pve/data", "size_bytes": n, "data_pct": f, "metadata_pct": f}, "vms_on_pool": [100, 101], "snapshots": [{"vmid": 100, "name": "pre-v385", "taken": "<ISO Z>", "sigmond": false, "expires": null}], "host_alarm": ""}`. The pool `Store` takes used = size × max(data_pct, metadata_pct) / 100, because a full metadata area stops the pool as surely as full data.

- [ ] **Step 1: Write the failing tests**

```python
def test_reserved_blocks_count_as_used():
    fake = lambda p: SimpleNamespace(f_blocks=1000, f_frsize=4096, f_bavail=100, f_bfree=150)
    s = measure_store("root", "/", "guest", statvfs=fake)
    assert s.capacity == 4096000 and s.used == 900 * 4096

def test_a_vanished_store_is_a_fault_not_a_crash(tmp_path):
    def sv(p):
        if p == "/mnt/gone": raise OSError(5, "EIO")
        return SimpleNamespace(f_blocks=10, f_frsize=1, f_bavail=5, f_bfree=5)
    mounts = tmp_path / "m"; mounts.write_text("/dev/sda1 / ext4 rw 0 0\n/dev/sdb1 /mnt/gone ext4 rw 0 0\n")
    stores, faults = discover_stores(["/mnt/gone/x"], "/var/lib/sigmond/archive", str(mounts), sv)
    assert [s.name for s in stores] == ["root"] and "/mnt/gone" in faults[0]

def test_path_bytes_counts_hardlinks_once(tmp_path):
    f = tmp_path / "a"; f.write_bytes(b"x" * 10000); os.link(f, tmp_path / "b")
    assert path_bytes([str(tmp_path)]) == os.stat(f).st_blocks * 512

def test_host_report_faults(tmp_path):
    now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
    p = tmp_path / "h.json"
    assert read_host_report(str(p), now) == (None, "missing")
    p.write_text("{"); assert read_host_report(str(p), now)[1] == "unreadable"
    p.write_text(json.dumps(_report(now - timedelta(seconds=901)))); assert read_host_report(str(p), now)[1] == "stale"
    p.write_text(json.dumps(_report(now)))
    rep, fault = read_host_report(str(p), now)
    assert fault is None and rep.pool.kind == "pool"

def test_metadata_fill_counts_as_pool_fill(tmp_path):
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    p = tmp_path / "h.json"; p.write_text(json.dumps(_report(now, data_pct=40.0, metadata_pct=96.0)))
    assert read_host_report(str(p), now)[0].pool.used_pct == pytest.approx(96.0)

def test_bare_metal_expects_no_host_report():
    assert not host_report_expected(None, detect_virt=lambda: "none")
    assert host_report_expected(None, detect_virt=lambda: "kvm")
    assert not host_report_expected(False, detect_virt=lambda: "kvm")
```

- [ ] **Step 2: Run them; expect FAIL.**
- [ ] **Step 3: Implement `measure.py`.**
- [ ] **Step 4: Run them; expect PASS.**
- [ ] **Step 5: Commit** `storage: measure stores; read the host pool report`

---

### Task 5: Calling clients, and the stop lever

**Files:**
- Create: `lib/sigmond/storage/clientcall.py`
- Test: `tests/test_storage_clientcall.py`

**Interfaces:**
- Consumes: the CLI verbs defined in Task 3; `lifecycle.resolve_units` (existing).
- Produces:
  - `HOLDINGS_TIMEOUT_S = 10`; `TRIM_TIMEOUT_S = 120`
  - `@dataclass(frozen=True) class ClientAnswer: ok: bool; held_bytes: Optional[int]; bytes_by_class: Mapping[str, int]; refused: bool; reason: str; unsupported: bool`
  - `call_holdings(binary: str, run=subprocess.run) -> ClientAnswer`
  - `call_trim(binary: str, fit_bytes: int, run=subprocess.run) -> ClientAnswer`
  - `call_final(binary: str, fit_bytes: int, policy: Optional[str], run=subprocess.run) -> ClientAnswer`
  - `NEVER_STOP = frozenset({"radiod", "ka9q-radio", "ka9q-web", "sigmond"})`
  - `stop_client(component: str, resolve=lifecycle.resolve_units, run=subprocess.run) -> tuple[bool, str]`. It runs `systemctl stop` on each resolved unit and returns `(False, reason)` for anything in `NEVER_STOP`. It never disables a unit and never deletes anything.

`ok` is True only when the exit code is 0 and the output parses as JSON of the right shape. Exit 2 → `unsupported=True, ok=False`. Timeouts, other exit codes and non-JSON → `ok=False` with `reason` naming the failure.

- [ ] **Step 1: Write the failing tests**

```python
def _run(stdout="", rc=0, exc=None):
    def run(argv, **kw):
        if exc: raise exc
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr="")
    return run

def test_trim_answer_parses():
    a = call_trim("x", 5, run=_run(json.dumps({"held_bytes": 4, "bytes_by_class": {"raw": 4}, "refused": False, "reason": ""})))
    assert a.ok and a.held_bytes == 4

def test_hang_garbage_and_old_clients_are_failed_answers():
    assert not call_trim("x", 5, run=_run(exc=subprocess.TimeoutExpired("x", 120))).ok
    assert not call_trim("x", 5, run=_run("not json")).ok
    old = call_trim("x", 5, run=_run(rc=2)); assert old.unsupported and not old.ok

def test_final_passes_the_directive():
    seen = []
    def run(argv, **kw): seen.append(argv); return SimpleNamespace(returncode=0, stdout='{"held_bytes":1,"bytes_by_class":{},"refused":false,"reason":""}', stderr="")
    call_final("x", 5, "stop-writing", run=run)
    assert seen[0][1:] == ["storage", "final", "--fit-bytes", "5", "--policy", "stop-writing", "--json"]

def test_stop_never_touches_radiod():
    ok, why = stop_client("radiod", resolve=lambda c: [], run=_run())
    assert not ok and "radiod" in why

def test_stop_stops_each_unit_and_nothing_else():
    calls = []
    units = [SimpleNamespace(name="codar-sounder@a.service"), SimpleNamespace(name="codar-sounder@b.service")]
    def run(argv, **kw): calls.append(argv); return SimpleNamespace(returncode=0, stdout="", stderr="")
    assert stop_client("codar-sounder", resolve=lambda c: units, run=run)[0]
    assert calls == [["systemctl", "stop", "codar-sounder@a.service"], ["systemctl", "stop", "codar-sounder@b.service"]]
```
(Check the field name `UnitRef` really uses for the unit name in `lib/sigmond/lifecycle.py`, and match it.)

- [ ] **Step 2: Run them; expect FAIL.**
- [ ] **Step 3: Implement `clientcall.py`.** Resolve the binary the same way `lib/sigmond/clients/contract.py` does for `inventory`; reuse its helper and don't copy it.
- [ ] **Step 4: Run them; expect PASS.**
- [ ] **Step 5: Commit** `storage: client trim/final/holdings calls; the stop lever`

---

### Task 6: The ladder

**Files:**
- Create: `lib/sigmond/storage/ladder.py`
- Test: `tests/test_storage_ladder.py`

**Interfaces:**
- Consumes: Task 1 types.
- Produces:
  - `DEMAND_DEADLINE_S = 600`; `UNMANAGED_FINAL_GRACE_S = 900`
  - `class Severity(IntEnum): WARNING=4, ERROR=3, CRITICAL=2` (the values are syslog priorities)
  - `@dataclass class TrimRecord: requested_at: float; answered_at: Optional[float] = None; ok: bool = False; final_since: Optional[float] = None`
  - `@dataclass(frozen=True) class Action: kind: str  # "trim"|"final"|"stop"|"cleanup"; store: str; client: Optional[str] = None; fit_bytes: Optional[int] = None; policy: Optional[str] = None`
  - `@dataclass class Decision: stages: dict[str, Stage]; actions: list[Action]; alarms: dict[str, tuple[Severity, str]]; targets: dict[str, dict[str, int]]`
  - `store_stage(store: Store, levels: Levels) -> Stage`
  - `tightened_targets(store: Store, levels: Levels, holdings: Mapping[str, int], floors: Mapping[str, int], allotments: Mapping[str, int]) -> dict[str, int]`
  - `decide(*, stores: Mapping[str, Store], allotments: Mapping[str, Allotments], holdings: Mapping[tuple[str, str], Holding], registrations: Sequence[Registration], levels: Levels, trims: Mapping[str, TrimRecord], directives: Mapping[str, str], hands_off: bool, host_fault: Optional[str], now: float) -> Decision`

`tightened_targets` lowers the allotments toward the floors. It finds the reduction R = used − capacity × watch/100. It shares R among clients in proportion to (holding − floor), clamped to 0, and returns for each client min(allotment, holding − its portion), never below its floor.

Rules for `decide`. Each alarm key is stable, so the AlarmBook can track episodes:
- Stage per store comes from `store_stage`. Floors that don't fit force FINAL, with alarm `floors:<store>` at CRITICAL.
- At every stage, a client holding more than `client_watch`% of its allotment gets `client-watch:<client>` at WARNING.
- WATCH and above: `store:<name>` alarm. The message names the store, its percent, its stage, and the three largest holders with their bytes. Severity: WATCH and TIGHTEN WARNING, DEMAND ERROR, FINAL CRITICAL.
- TIGHTEN and above: one `cleanup` action per store, and one `trim` action for each managed client over its target (`targets` from `tightened_targets`).
- DEMAND and above: a managed client whose trim has gone unanswered, or answered `ok=False`, for longer than `DEMAND_DEADLINE_S` gets `demand-overdue:<client>` at ERROR.
- FINAL:
  - Each client over its target gets a `final` action with `policy=directives.get(client)`, if managed.
  - Each client that failed to answer gets a `stop` action. Failing to answer means a record exists, `ok=False`, and it's older than `DEMAND_DEADLINE_S`. An unmanaged client over its target with `final_since` older than `UNMANAGED_FINAL_GRACE_S` counts as failing too.
  - With `hands_off`, a client that would be stopped gets no stop; it gets `hands-off-held:<client>` at CRITICAL instead.
- A pool store (kind `"pool"`) gets stages and alarms but no client actions. Its clients are the guest's, and the pool limit already shrank their allotments.
- `host_fault` → `host-report:<fault>` at ERROR.

- [ ] **Step 1: Write the failing tests**

```python
GiB = 1 << 30
def test_stage_thresholds():
    lv = Levels()
    assert [store_stage(Store("r", "/", "guest", 100, u), lv) for u in (79, 80, 85, 90, 95)] == \
        [Stage.OK, Stage.WATCH, Stage.TIGHTEN, Stage.DEMAND, Stage.FINAL]

def test_tightened_targets_reach_the_watch_level_and_respect_floors():
    s = Store("root", "/", "guest", 100 * GiB, 86 * GiB)          # R = 6 GiB
    t = tightened_targets(s, Levels(), {"a": 40 * GiB, "b": 20 * GiB}, {"a": 30 * GiB, "b": 0}, {"a": 50 * GiB, "b": 50 * GiB})
    assert t["a"] >= 30 * GiB and sum(40 * GiB + 20 * GiB - v for v in (t["a"], t["b"])) == pytest.approx(6 * GiB, abs=2)

def test_tighten_asks_managed_clients_to_trim_and_clears_clutter():
    d = _decide(used_pct=86, managed=True)
    assert {a.kind for a in d.actions} == {"trim", "cleanup"}

def test_final_with_a_directive_passes_it():
    d = _decide(used_pct=96, managed=True, directives={"a": "stop-writing"})
    assert any(a.kind == "final" and a.client == "a" and a.policy == "stop-writing" for a in d.actions)

def test_unanswered_client_gets_stopped_at_final_only():
    rec = {"a": TrimRecord(requested_at=0.0, ok=False)}
    assert not any(a.kind == "stop" for a in _decide(used_pct=91, managed=True, trims=rec, now=700).actions)
    assert any(a.kind == "stop" and a.client == "a" for a in _decide(used_pct=96, managed=True, trims=rec, now=700).actions)

def test_a_refusal_is_an_answer_not_a_failure():
    rec = {"a": TrimRecord(requested_at=0.0, answered_at=5.0, ok=True)}
    assert not any(a.kind == "stop" for a in _decide(used_pct=96, managed=True, trims=rec, now=700).actions)

def test_unmanaged_gets_grace_then_stop():
    early = {"a": TrimRecord(requested_at=0.0, final_since=0.0)}
    assert not any(a.kind == "stop" for a in _decide(used_pct=96, managed=False, trims=early, now=899).actions)
    assert any(a.kind == "stop" for a in _decide(used_pct=96, managed=False, trims=early, now=901).actions)

def test_hands_off_turns_a_stop_into_a_critical_alarm():
    rec = {"a": TrimRecord(requested_at=0.0, ok=False)}
    d = _decide(used_pct=96, managed=True, trims=rec, now=700, hands_off=True)
    assert not any(a.kind == "stop" for a in d.actions) and d.alarms["hands-off-held:a"][0] == Severity.CRITICAL

def test_floors_that_no_longer_fit_go_straight_to_final():
    d = _decide(used_pct=50, managed=True, floors_fit=False)
    assert d.stages["root"] == Stage.FINAL and "floors:root" in d.alarms

def test_host_fault_alarms():
    assert "host-report:stale" in _decide(used_pct=10, managed=True, host_fault="stale").alarms

def test_watch_alarm_names_the_biggest_holders():
    msg = _decide(used_pct=81, managed=True).alarms["store:root"][1]
    assert "a" in msg and "81" in msg
```
(`_decide` is a test-local builder: one store `root` of 100 GiB, one client `a` holding most of the used space, an allotment of 40 GiB, defaults otherwise. Write it with explicit keyword defaults so each test reads as data.)

- [ ] **Step 2: Run them; expect FAIL.**
- [ ] **Step 3: Implement `ladder.py`.** It stays pure: no I/O, no clock reads.
- [ ] **Step 4: Run them; expect PASS.**
- [ ] **Step 5: Commit** `storage: the four-stage ladder`

---

### Task 7: The alarm book

**Files:**
- Create: `lib/sigmond/storage/alarms.py`
- Test: `tests/test_storage_alarms.py`

**Interfaces:**
- Consumes: `Severity` (Task 6).
- Produces:
  - `REMIND_S = 6 * 3600`
  - `@dataclass(frozen=True) class Emission: key: str; kind: str  # "raise"|"remind"|"clear"; severity: Severity; message: str`
  - `class AlarmBook` with `update(self, active: Mapping[str, tuple[Severity, str]], now: float) -> list[Emission]`, `to_dict(self) -> dict` and `from_dict(cls, d: dict) -> AlarmBook`. `from_dict` takes `{}` and malformed input and returns an empty book.
  - `journal_line(e: Emission) -> str`. It returns `f"<{prio}>storage {kind}: {message}"`; a clear uses priority 6 (info).

A severity change (say WARNING → CRITICAL) under the same key counts as a new `raise`.

- [ ] **Step 1: Write the failing tests**

```python
def test_one_line_per_episode_with_reminders():
    b = AlarmBook(); a = {"store:root": (Severity.WARNING, "root 81 %")}
    assert [e.kind for e in b.update(a, 0)] == ["raise"]
    assert b.update(a, 120) == [] and b.update(a, REMIND_S - 1) == []
    assert [e.kind for e in b.update(a, REMIND_S + 1)] == ["remind"]
    assert [e.kind for e in b.update({}, REMIND_S + 2)] == ["clear"]

def test_escalation_raises_again():
    b = AlarmBook(); b.update({"k": (Severity.WARNING, "m")}, 0)
    assert [e.kind for e in b.update({"k": (Severity.CRITICAL, "m")}, 1)] == ["raise"]

def test_round_trip_and_garbage():
    b = AlarmBook(); b.update({"k": (Severity.ERROR, "m")}, 0)
    assert AlarmBook.from_dict(json.loads(json.dumps(b.to_dict()))).update({"k": (Severity.ERROR, "m")}, 1) == []
    assert AlarmBook.from_dict({"nonsense": 1}).update({}, 0) == []

def test_journal_priority_prefix():
    assert journal_line(Emission("k", "raise", Severity.CRITICAL, "m")).startswith("<2>storage raise: m")
    assert journal_line(Emission("k", "clear", Severity.CRITICAL, "m")).startswith("<6>")
```

- [ ] **Step 2: Run them; expect FAIL.**  **Step 3: Implement.**  **Step 4: Run them; expect PASS.**
- [ ] **Step 5: Commit** `storage: alarm book, once per episode`

---

### Task 8: Clean-up sigmond does itself, and expiring log levels

**Files:**
- Create: `lib/sigmond/storage/cleanup.py`
- Modify: `lib/sigmond/log_cmd.py`: `set_log_level` gains `until: Optional[datetime] = None`. Add `expire_log_levels`. Also modify the `smd log --level` parser in `bin/smd` (find it with `grep -n "set_log_level(" bin/smd`): add `--for DURATION`, defaulting to 24 h for any level above INFO.
- Test: `tests/test_storage_cleanup.py`, `tests/test_log_cmd.py` (extend)

**Interfaces:**
- Produces:
  - `JOURNAL_TARGET = "500M"`; `SIGMOND_TMP = Path("/var/lib/sigmond/tmp")`; `TMP_MAX_AGE_S = 7 * 86400`; `UNCLAIMED_MIN_BYTES = 1 << 30`; `UNCLAIMED_ROOTS = ("/var/tmp", "/tmp", "/home", "/srv", "/var/lib", "/opt", "/mnt")`
  - `@dataclass(frozen=True) class CleanupResult: step: str; ok: bool; detail: str`
  - `run_cleanup(run=subprocess.run, now: float = None, tmp_root: Path = SIGMOND_TMP) -> list[CleanupResult]`. It runs `journalctl --vacuum-size=500M`, then `apt-get clean`, then deletes regular files under `tmp_root` older than 7 days. One step failing never stops the next.
  - `report_unclaimed(claimed: Sequence[str], roots=UNCLAIMED_ROOTS, size_of=path_bytes, proc_mounts: str = "/proc/mounts") -> list[tuple[str, int]]`. It looks at directories at depth ≤ 2 under each root that sit outside every claimed path and the sigmond state dirs, sorted by size, and keeps those ≥ 1 GiB. Loop-device mounts under the roots get listed too. It only reports.
  - `log_cmd.set_log_level(..., until=None)`. It writes `<KEY>=<level>`. With `until`, it also writes `<KEY>_UNTIL=<ISO Z>` and, when no routine level is recorded yet, `<KEY>_ROUTINE=<previous value or empty>`.
  - `log_cmd.expire_log_levels(env_path: Path, now: datetime) -> list[str]`. It returns the keys restored. Restoring writes back `_ROUTINE`, or removes the key when `_ROUTINE` is empty, and drops `_UNTIL` and `_ROUTINE`.

- [ ] **Step 1: Write the failing tests**

```python
def test_cleanup_runs_every_step_even_after_a_failure(tmp_path):
    calls = []
    def run(argv, **kw):
        calls.append(argv[0]); return SimpleNamespace(returncode=1 if argv[0] == "journalctl" else 0, stdout="", stderr="x")
    old = tmp_path / "old"; old.write_text("x"); os.utime(old, (0, 0))
    new = tmp_path / "new"; new.write_text("x")
    res = run_cleanup(run=run, now=10 * 86400, tmp_root=tmp_path)
    assert calls == ["journalctl", "apt-get"] and not res[0].ok and res[1].ok
    assert not old.exists() and new.exists()

def test_unclaimed_skips_claimed_paths(tmp_path):
    (tmp_path / "srv" / "big").mkdir(parents=True); (tmp_path / "srv" / "client").mkdir()
    sizes = {str(tmp_path / "srv" / "big"): 2 << 30, str(tmp_path / "srv" / "client"): 5 << 30}
    got = report_unclaimed([str(tmp_path / "srv" / "client")], roots=(str(tmp_path / "srv"),),
                           size_of=lambda ps: sizes.get(ps[0], 0), proc_mounts=str(tmp_path / "none"))
    assert got == [(str(tmp_path / "srv" / "big"), 2 << 30)]

def test_raised_level_expires_back_to_routine(tmp_path):
    env = tmp_path / "coordination.env"; env.write_text("HF_TIMESTD_LOG_LEVEL=INFO\n")
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    set_log_level("DEBUG", "hf-timestd", env_path=env, until=now + timedelta(hours=2))
    assert expire_log_levels(env, now + timedelta(hours=1)) == []
    assert expire_log_levels(env, now + timedelta(hours=3)) == ["HF_TIMESTD_LOG_LEVEL"]
    assert env.read_text() == "HF_TIMESTD_LOG_LEVEL=INFO\n"
```
(Match `set_log_level`'s existing parameter names; read its signature first.)

- [ ] **Step 2: Run them; expect FAIL.**  **Step 3: Implement.**  **Step 4: Run them, plus the existing `tests/test_log_cmd.py`; expect PASS.**
- [ ] **Step 5: Commit** `storage: sigmond's own clean-up; raised log levels expire`

---

### Task 9: The tick, `smd storage`, and the timer

**Files:**
- Create: `lib/sigmond/storage/tick.py`, `lib/sigmond/commands/storage.py`, `systemd/sigmond-storage.service`, `systemd/sigmond-storage.timer`
- Modify: `bin/smd`. Add the `storage` parser beside `archive` (near line 28235), add `storage` to `top_cmds` (line 17106), and add a usage line in the header block (line 38). Modify the install path that ships `sigmond-heartbeat.timer` so it ships and enables the two new units too (`grep -rn "sigmond-heartbeat.timer" install.sh scripts lib`). Add a tmpfiles line creating `/run/sigmond` 0755, if none exists yet.
- Modify: `CLAUDE.md`, "Architecture layers": add item 14, "Storage capacity ledger", with two lines pointing at the spec.
- Test: `tests/test_storage_tick.py`, `tests/test_storage_cmd.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `STATE_PATH = Path("/var/lib/sigmond/storage/state.json")`; `STATUS_PATH = Path("/run/sigmond/storage-status.json")`; `LOCK_PATH = Path("/run/sigmond/storage.lock")`; `DU_CACHE_S = 1800`; `STALE_TRIM_S = 3600`; `UNCLAIMED_EVERY_S = 6 * 3600`
  - `@dataclass class Runners: load_views; discover_stores; path_bytes; read_host_report; host_report_expected; call_holdings; call_trim; call_final; stop_client; run_cleanup; report_unclaimed; expire_log_levels; sighup; journal; binary_for`. Each field takes the matching function from the tasks above. `default_runners()` wires the real ones.
  - `run_tick(now: datetime, runners: Runners, *, config_path=CONFIG_PATH, state_path=STATE_PATH, status_path=STATUS_PATH, lock_path=LOCK_PATH) -> int`. Exit codes: 0 ran, 75 (`EX_TEMPFAIL`) skipped on a held lock, 1 ledger fault (alarm raised).
  - Status file: `{"schema": 1, "generated_at", "worst_stage", "stores": [{"name","kind","capacity","used","used_pct","stage","allottable"}], "clients": [{"client","store","held","allotment","target","floor","managed","stopped_by_storage"}], "host_report": {"expected","fault","age_s"}, "hands_off": {"until","reason"}|null, "alarms": [{"key","severity","message","since"}], "unclaimed": [[path, bytes]], "config_problems": [...]}`
  - `cmd_storage(args) -> int` with subcommands `status [--json]`, `share <client> <weight>|--clear <client>`, `directive <client> <policy>|--clear <client>`, `directive hands-off [--for 24h] [--reason TEXT] | --clear`, and `tick`.

Tick order: take the flock without blocking, or exit 75 → load config (each config problem becomes alarm `config:<n>` at WARNING) → expire log levels and SIGHUP their clients → load views → registrations → stores (+ the pool from the host report; faults become alarms `store-missing:<path>` at ERROR) → holdings (managed: `call_holdings`; unmanaged: `path_bytes`, cached for `DU_CACHE_S` in state) → per store: registered bytes, allottable, pool limit for guest stores (share = `cfg.pool_share` or 1/len(vms_on_pool)), `allot` → `decide` → act: trim/final calls update the `TrimRecord`s; `stop` calls `stop_client`, records `stopped_by_storage`, and raises alarm `stopped:<client>` at CRITICAL, held until an operator starts the client; cleanup at most once per hour → unclaimed report every 6 h → AlarmBook update → journal lines → write state and status atomically (`lib/sigmond/safe_write.py`). Trim records older than `STALE_TRIM_S` get dropped on load. Any exception after the lock: raise alarm `ledger-fault` at CRITICAL, write a status with `worst_stage: "FAULT"`, and return 1.

Privilege: find out how `smd archive add` gets root (`lib/sigmond/commands/archive.py`). Copy that pattern for `share` and `directive`; smd refuses `sudo smd`. The service runs as root, mirroring `systemd/sigmond-heartbeat.service`. The timer fires `OnCalendar=*:0/2` with `RandomizedDelaySec=20`.

- [ ] **Step 1: Write the failing tests**

```python
def test_tick_writes_status_and_raises_once(tmp_path):
    r = _fake_runners(used_pct=86)                          # one store, one managed client over target
    assert run_tick(T0, r, **_paths(tmp_path)) == 0
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["worst_stage"] == "TIGHTEN" and r.calls["call_trim"] == 1
    run_tick(T0 + timedelta(minutes=2), r, **_paths(tmp_path))
    assert sum("raise" in l for l in r.journal_lines) == 1

def test_held_lock_skips(tmp_path):
    p = _paths(tmp_path)
    with open(p["lock_path"], "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        assert run_tick(T0, _fake_runners(used_pct=10), **p) == 75

def test_stale_trim_records_never_cause_a_stop(tmp_path):
    p = _paths(tmp_path)
    p["state_path"].write_text(json.dumps({"trims": {"a": {"requested_at": (T0 - timedelta(hours=2)).timestamp(), "ok": False}}}))
    r = _fake_runners(used_pct=96)
    run_tick(T0, r, **p)
    assert r.calls["stop_client"] == 0

def test_failed_answer_then_stop_at_final(tmp_path):
    r = _fake_runners(used_pct=96, trim_ok=False); p = _paths(tmp_path)
    run_tick(T0, r, **p); run_tick(T0 + timedelta(seconds=601), r, **p)
    assert r.calls["stop_client"] == 1
    assert json.loads(p["status_path"].read_text())["clients"][0]["stopped_by_storage"]

def test_an_exception_is_a_loud_fault_not_silence(tmp_path):
    r = _fake_runners(used_pct=10); r.load_views = lambda: 1 / 0
    assert run_tick(T0, r, **_paths(tmp_path)) == 1
    assert json.loads((tmp_path / "status.json").read_text())["worst_stage"] == "FAULT"
    assert any(l.startswith("<2>") for l in r.journal_lines)

def test_bare_metal_has_no_host_alarm(tmp_path):
    r = _fake_runners(used_pct=10, virt=False, host_report=(None, "missing"))
    run_tick(T0, r, **_paths(tmp_path))
    assert not any("host-report" in a["key"] for a in json.loads((tmp_path / "status.json").read_text())["alarms"])
```
`tests/test_storage_cmd.py`: `status --json` prints the status file unchanged. Plain `status` prints one line per store and one per client, with nothing above 0x7F in the output. `share`/`directive` write a file that `load_storage_config` reads back. A bad policy exits 2 with the allowed set in the message. `directive hands-off --for 15d` exits 2.

- [ ] **Step 2: Run them; expect FAIL.**  **Step 3: Implement.**  **Step 4: Run them, plus the full suite.** `.venv/bin/pytest -q`; expect PASS.
- [ ] **Step 5: Smoke test on the dev tree:** `PYTHONPATH=lib ./bin/smd storage status` with no status file → prints "no ledger tick yet" and exits 0.
- [ ] **Step 6: Commit** `storage: the tick, smd storage, the 2-minute timer`

---

### Task 10: The heartbeat carries the ledger

**Files:**
- Modify: `lib/sigmond/heartbeat.py`: `_read_resources` adds a `storage` key read from `STATUS_PATH`; `_map_resources` folds it into the verdict.
- Test: `tests/test_heartbeat.py` (extend)

Mapping. A status file older than 600 s, or missing → the storage part reads INDETERMINATE. Worst stage OK → VALID; WATCH, TIGHTEN or DEMAND → INCONCLUSIVE; FINAL or FAULT → INVALID. The resources verdict becomes the worse of the existing disk judgment and this, by the `PRECEDENCE` already in `heartbeat_schema.py`. `data["storage"] = {"worst_stage", "stores": [{"name","used_pct","stage"}], "alarms": n}`. The block schema stays unchanged, so the fleetboard needs no change.

- [ ] **Step 1: Write the failing tests:** `test_storage_final_makes_resources_invalid`, `test_storage_watch_is_inconclusive`, `test_missing_ledger_status_is_indeterminate_not_valid`, `test_disk_invalid_beats_storage_valid`. Assert on `_map_resources(raw)["verdict"]` for each.
- [ ] **Step 2: Run them; expect FAIL.**  **Step 3: Implement.**  **Step 4: Run them, plus `tests/test_heartbeat*.py`; expect PASS.**
- [ ] **Step 5: Commit** `heartbeat: resources carries the storage ledger's stage`

---

### Task 11: The host side: pool report, snapshot lifetimes, host alarm

**Files:**
- Create: `scripts/proxmox/pm-storage.py` (stdlib only, no sigmond imports), `scripts/proxmox/pm-storage.service`, `scripts/proxmox/pm-storage.timer` (`OnCalendar=*:0/5`, `RandomizedDelaySec=30`)
- Modify: `scripts/proxmox/pm-heartbeat-setup.sh`, so it installs and enables the pm-storage pair too. Modify `scripts/proxmox/pm-heartbeat.py`: `build_resources_raw` adds `pool_pct` (read from `/run/sigmond/pm-storage.json`, which pm-storage writes), and `_map_resources` returns INVALID at ≥ 95. Modify `scripts/proxmox/README.md`, adding a short section.
- Leave `sigmond-appliance/firstboot-v3.sh` alone: `pristine` stays unlabelled, and the reaper skips it by name. The README says so.
- Test: `tests/test_pm_storage.py`

**Interfaces:**
- Produces, inside `pm-storage.py`:
  - `LEVELS = {"watch": 80.0, "tighten": 85.0, "demand": 90.0, "final": 95.0}`, duplicated from `sigmond.storage.model.Levels` on purpose, with a drift-guard test.
  - `SNAP_TAG = "sigmond-snapshot expires="`; `SNAP_LIFE_H = 48` (the cap); `SNAP_EARLY_REAP_PCT = 90.0`; `CHURN_WINDOW_S = 6 * 3600`; `SNAP_REFUSE_PCT = 70.0`; `NEVER_REAP = frozenset({"pristine"})`; `GUEST_REPORT_PATH = "/run/sigmond/host-pool.json"`; `LOCAL_STATE = "/run/sigmond/pm-storage.json"`
  - `parse_storage_cfg(text: str) -> dict[str, tuple[str, str]]` maps a storage id to (vgname, thinpool) for `lvmthin:` blocks in `/etc/pve/storage.cfg`.
  - `pool_for_vm(qm_config_text: str, storage_map) -> Optional[tuple[str, str]]` reads `scsi0`/`virtio0`/`sata0` → storage id → pool.
  - `read_pool(lvs_json: str, vg: str, pool: str) -> dict` returns `{"name": "vg/pool", "size_bytes", "data_pct", "metadata_pct"}`, from `lvs --reportformat json --units b --nosuffix -o lv_name,vg_name,lv_size,data_percent,metadata_percent,lv_attr`.
  - `parse_snapshots(pvesh_json: str, vmid: int) -> list[dict]` reads `pvesh get /nodes/<node>/qemu/<vmid>/snapshot --output-format json`, drops `current`, and fills `sigmond`/`expires` from the description tag.
  - `expired(snaps: list[dict], now: datetime, pool_pct: float = 0.0) -> list[dict]` returns the labelled snapshots past expiry — or every labelled one when `pool_pct >= SNAP_EARLY_REAP_PCT` — never a name in `NEVER_REAP`.
  - `churn_bytes_per_h(history: list[tuple[float, float]]) -> Optional[float]` takes (epoch, used_bytes) samples of the pool, kept in `LOCAL_STATE` over the last `CHURN_WINDOW_S`, and returns the growth rate by least squares; None with fewer than 2 samples an hour apart. Shrinking counts as 0.
  - `snapshot_life_h(free_bytes: int, churn: Optional[float]) -> float` returns min(`SNAP_LIFE_H`, 0.5 × free_bytes / churn hours); `SNAP_LIFE_H` when churn is None or 0. Floor 1 h; below 2 h of projected room the snapshot is refused instead.
  - `host_alarms(pool: dict, snaps: list[dict], now: datetime) -> list[str]`. It gives the pool stage when ≥ watch, using max(data_pct, metadata_pct). It also names each unlabelled snapshot other than `pristine` older than `SNAP_LIFE_H`, with its age. `pristine` gets named only when the pool sits at or above watch.
  - `guest_report(vmid, pool, vms_on_pool, snaps, alarms, now) -> str`. JSON per Task 4's schema, `ensure_ascii=True`.
  - `main(argv) -> int`, with subcommands:
    - `report` (the timer): for each VM on this host, find its pool, then reap expired labelled snapshots (`qm delsnapshot`). Write each running guest's report via `pvesh create /nodes/<node>/qemu/<vmid>/agent/file-write --file /run/sigmond/host-pool.json --content <json>`. Write `LOCAL_STATE`. Log alarms to the journal with `<N>` priority prefixes, once per episode, with the episode state kept in `LOCAL_STATE`. A guest that fails the file-write gets a host alarm, "guest <vmid> unreachable", and the run goes on.
    - `snapshot <vmid> <name> [--hours N]`: refuse (exit 3, saying why) when the VM's pool sits at ≥ 70 %, or when the projected time to full is under 2 h. Otherwise the lifetime is `snapshot_life_h(...)` (an explicit `--hours` may shorten it, never lengthen it past that), and it runs `qm snapshot <vmid> <name> --description "sigmond-snapshot expires=<ISO Z>"`, printing the lifetime and why.

- [ ] **Step 1: Write the failing tests**

```python
def test_levels_match_the_ledger():
    from sigmond.storage.model import Levels
    L = Levels(); assert pm.LEVELS == {"watch": L.watch, "tighten": L.tighten, "demand": L.demand, "final": L.final}

def test_storage_cfg_maps_local_lvm():
    m = pm.parse_storage_cfg("lvmthin: local-lvm\n\tthinpool data\n\tvgname pve\n\tcontent rootdir,images\n")
    assert m == {"local-lvm": ("pve", "data")}
    assert pm.pool_for_vm("scsi0: local-lvm:vm-100-disk-0,size=255G\n", m) == ("pve", "data")

def test_metadata_counts():
    lvs = json.dumps({"report": [{"lv": [{"lv_name": "data", "vg_name": "pve", "lv_size": "100", "data_percent": "40.0",
                                           "metadata_percent": "96.0", "lv_attr": "twi-aotz--"}]}]})
    assert any("96" in a for a in pm.host_alarms(pm.read_pool(lvs, "pve", "data"), [], NOW))

def test_only_labelled_expired_snapshots_reap():
    snaps = pm.parse_snapshots(json.dumps([
        {"name": "current"},
        {"name": "pristine", "snaptime": 1, "description": ""},
        {"name": "pre-v385", "snaptime": 1, "description": "operator"},
        {"name": "align-1", "snaptime": 1, "description": "sigmond-snapshot expires=2026-11-01T00:00:00Z"},
        {"name": "align-2", "snaptime": 1, "description": "sigmond-snapshot expires=2026-12-01T00:00:00Z"}]), 100)
    assert [s["name"] for s in pm.expired(snaps, NOW)] == ["align-1"]

def test_old_operator_snapshot_alarms_but_never_reaps():
    snaps = [{"vmid": 100, "name": "pre-v385", "taken": "2026-11-02T12:25:00Z", "sigmond": False, "expires": None}]
    assert pm.expired(snaps, NOW) == [] and any("pre-v385" in a for a in pm.host_alarms(_pool(50), snaps, NOW))

def test_guest_report_is_ascii_and_matches_the_guest_reader(tmp_path):
    from sigmond.storage.measure import read_host_report
    text = pm.guest_report(100, _pool(50), [100, 101], [], ["pool 50 % — fine"], NOW)
    assert all(ord(c) < 128 for c in text)
    p = tmp_path / "h.json"; p.write_text(text)
    rep, fault = read_host_report(str(p), NOW)
    assert fault is None and rep.vms_on_pool == (100, 101)

def test_lifetime_follows_the_pools_time_to_full():
    GB = 10**9
    # 11-03: ~120 GB free, ~3.7 GB/h churn -> ~32 h to full -> ~16 h life
    assert pm.snapshot_life_h(120 * GB, 3.7 * GB) == pytest.approx(16.2, abs=0.2)
    assert pm.snapshot_life_h(500 * GB, 1 * GB) == 48          # the cap
    assert pm.snapshot_life_h(120 * GB, None) == 48             # no churn measured yet

def test_churn_from_pool_history():
    h = [(0, 100e9), (3600, 103.7e9), (7200, 107.4e9)]
    assert pm.churn_bytes_per_h(h) == pytest.approx(3.7e9, rel=0.01)
    assert pm.churn_bytes_per_h([(0, 1e9)]) is None
    assert pm.churn_bytes_per_h([(0, 5e9), (3600, 4e9)]) == 0

def test_pressure_reaps_labelled_snapshots_early_but_never_operators():
    snaps = [{"vmid": 100, "name": "align-2", "sigmond": True, "expires": "2026-12-01T00:00:00Z"},
             {"vmid": 100, "name": "pre-v385", "sigmond": False, "expires": None},
             {"vmid": 100, "name": "pristine", "sigmond": False, "expires": None}]
    assert pm.expired(snaps, NOW, pool_pct=89.9) == []
    assert [s["name"] for s in pm.expired(snaps, NOW, pool_pct=90.0)] == ["align-2"]

def test_snapshot_refused_on_a_full_pool():
    assert pm.main(["snapshot", "100", "x"], _fake_host(pool_pct=72)) == 3
```
(`NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)`. Load `pm` with `importlib` from its path, as `tests/test_pm_heartbeat.py` does. `main` takes an injectable host-runner bundle as its second argument, defaulting to the real subprocess calls.)

- [ ] **Step 2: Run them; expect FAIL.**  **Step 3: Implement.**  **Step 4: Run them, plus `tests/test_pm_heartbeat.py`; expect PASS.**
- [ ] **Step 5: Commit** `pm-storage: host pool report into the guest, snapshot lifetimes, host alarm`

---

### Task 12: The client kit, and the guide for new clients

Every client that writes local data has to adopt v0.9. This task keeps that small: each client writes one function that lists its files with their classes. The kit does the rest.

**Files:**
- Create: `lib/sigmond/storage/client_kit.py` (stdlib only). Clients import sigmond lazily with a fallback, so the kit must import nothing beyond the stdlib and `sigmond.storage.model.CLASSES`.
- Modify: `docs/ADD-A-CLIENT.md`. Add row 8 to the TL;DR table, "Storage: `data_sinks` floor/policy/classes, and the `storage` verbs", plus a section showing a client wiring the kit in about 20 lines. Also check `docs/REQUIREMENTS-TEMPLATE.md` and `docs/EVENT-CLIENT-PLAYBOOK.md`: where either lists contract surfaces or load budgeting, add storage in one line each.
- Test: `tests/test_storage_client_kit.py`

**Interfaces:**
- Consumes: `CLASSES` (Task 1); the verb shapes in §17.6 (Task 3).
- Produces:
  - `@dataclass(frozen=True) class Item: path: str; bytes: int; cls: str; mtime: float`
  - `holdings(items: Sequence[Item]) -> dict[str, int]`
  - `written_per_day(items: Sequence[Item], now: float) -> int`: the bytes of items whose mtime falls in the last 24 h. The `holdings` verb reports it as `bytes_written_per_day` (Task 13 reads it).
  - `choose_trim(items: Sequence[Item], fit_bytes: int, floor_bytes: int, *, final: bool = False) -> list[Item]`. It takes from `sent`, then `derived`, then `raw`, oldest first within each class, and never takes `pinned`. It stops once the remainder fits `fit_bytes`. Without `final` it stops at `floor_bytes`; with `final` it may go below the floor, but still never takes `pinned`.
  - `add_storage_verbs(subparsers, *, list_items: Callable[[], list[Item]], delete: Callable[[Item], None], floor_bytes: int, final_policy: str, apply_policy: Optional[Callable[[str, int], dict]] = None) -> None`. It wires `storage holdings|trim|final` onto a client's argparse, with JSON output exactly as §17.6 specifies. With no `apply_policy`, `final` handles `give-up-oldest:<class>` through `choose_trim` restricted to that class and refuses every other policy with a reason. A `delete` that raises leaves that item counted as held, and the answer reports it.

- [ ] **Step 1: Write the failing tests**

```python
def _i(n, cls, t): return Item(f"/d/{n}", 10, cls, t)
def test_trim_takes_sent_then_derived_then_raw_oldest_first():
    items = [_i("r1", "raw", 1), _i("d1", "derived", 2), _i("s2", "sent", 3), _i("s1", "sent", 0)]
    assert [x.path for x in choose_trim(items, fit_bytes=10, floor_bytes=0)] == ["/d/s1", "/d/s2", "/d/d1"]

def test_trim_stops_at_the_floor_unless_final():
    items = [_i("r1", "raw", 1), _i("r2", "raw", 2)]
    assert choose_trim(items, 0, floor_bytes=20) == []
    assert len(choose_trim(items, 0, floor_bytes=20, final=True)) == 2

def test_pinned_never_goes():
    assert choose_trim([_i("p", "pinned", 0)], 0, 0, final=True) == []

def test_verbs_print_the_contract_shape(capsys):
    deleted = []
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd")
    add_storage_verbs(sub, list_items=lambda: [_i("s", "sent", 0), _i("r", "raw", 1)],
                      delete=deleted.append, floor_bytes=0, final_policy="give-up-oldest:raw")
    args = ap.parse_args(["storage", "trim", "--fit-bytes", "10", "--json"]); assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"held_bytes": 10, "bytes_by_class": {"raw": 10}, "refused": False, "reason": ""}
    assert [x.path for x in deleted] == ["/d/s"]
```

- [ ] **Step 2: Run them; expect FAIL.**  **Step 3: Implement, then write the doc changes.**  **Step 4: Run them; expect PASS.**
- [ ] **Step 5: Commit** `storage: client kit; ADD-A-CLIENT step 8`

---

### Task 13: Days of coverage (Michael, 2026-11-04)

Allotments become equal days of coverage instead of equal bytes. Spec: "Registration and allotments", the paragraphs on days of coverage and on rates.

**Files:**
- Modify: `lib/sigmond/storage/model.py`: replace `allot`'s byte split.
- Create: `lib/sigmond/storage/rates.py`
- Modify: `lib/sigmond/storage/ladder.py`: `tightened_targets` takes days, and a time-to-full Watch trigger.
- Modify: `lib/sigmond/storage/tick.py`, `lib/sigmond/commands/storage.py`, `lib/sigmond/heartbeat.py`: status, CLI and heartbeat carry D and per-client days.
- Modify: `docs/CLIENT-CONTRACT.md` §17.7: `holdings` gains optional `bytes_written_per_day`; `retention_days` caps a client's days.
- Test: `tests/test_storage_model.py`, `tests/test_storage_rates.py`, `tests/test_storage_ladder.py`, `tests/test_storage_tick.py`, and the CLI and heartbeat tests.

**Interfaces:**
- `@dataclass(frozen=True) class Demand: client: str; floor_bytes: int; rate_bytes_per_day: float; weight: float = 1.0; max_days: Optional[float] = None`
- `@dataclass(frozen=True) class Allotments: per_client: Mapping[str, int]; floors_fit: bool; allottable: int; days: Optional[float]`. `days` is None when no client has a rate.
- `allot_days(demands: Sequence[Demand], allottable: int) -> Allotments`. Floors first. Then it finds D by water-filling, so that Σ min(rate·weight·D, rate·weight·max_days) = allottable − Σ floors. A client with zero rate gets its floor only. If every client hits max_days, the leftover stays unallotted. Results are integer bytes, with the remainder going to the first client in sorted order.
- `rates.rate_for(client, *, reported: Optional[int], recent_bytes: Optional[int], declared_mb_per_day: Optional[float]) -> tuple[float, str]` returns bytes/day and its source ("reported" | "measured" | "declared" | "none").
- `rates.recent_bytes(paths, now, window_s=86400, stat=os.lstat) -> int`: the st_blocks·512 of regular files with mtime in the window, each inode once, no symlinks. It is cached per client for 30 min, like the du cache.
- `ladder.tightened_targets(...)` becomes allotments recomputed with `allot_days` for the space that brings the store to the Watch level, so every client loses days alike and keeps its floor.
- `ladder.store_stage(store, levels, churn_bytes_per_h=None, horizon_h=24.0)` gives at least WATCH when `churn > 0` and the projected time to full is under `horizon_h`.
- Status JSON: top level `"days": D` per store. Each client gains `"rate_bytes_per_day"`, `"rate_source"`, `"days_held"`.

- [ ] **Step 1: Write the failing tests**

```python
GiB = 1 << 30
def test_equal_days_not_equal_bytes():
    # I/Q writes 80 GiB/day, the magnetometer 0.01 GiB/day, 100 GiB to share, no floors
    a = allot_days([Demand("hf-timestd", 0, 80 * GiB), Demand("mag", 0, 0.01 * GiB)], 100 * GiB)
    assert a.days == pytest.approx(100 / 80.01, rel=1e-6)
    assert a.per_client["mag"] < 0.02 * GiB and sum(a.per_client.values()) == 100 * GiB

def test_retention_cap_gives_days_to_others():
    a = allot_days([Demand("iq", 0, 10 * GiB), Demand("spots", 0, 10 * GiB, max_days=1.0)], 100 * GiB)
    assert a.per_client["spots"] == 10 * GiB and a.per_client["iq"] == 90 * GiB

def test_floors_first_then_days():
    a = allot_days([Demand("a", 5 * GiB, 10 * GiB), Demand("b", 0, 10 * GiB)], 25 * GiB)
    assert a.days == pytest.approx(1.0) and a.per_client["a"] == 15 * GiB

def test_operator_weight_doubles_days():
    a = allot_days([Demand("a", 0, 10 * GiB, weight=2.0), Demand("b", 0, 10 * GiB)], 30 * GiB)
    assert a.per_client["a"] == 20 * GiB

def test_a_ring_at_steady_state_still_has_a_rate(tmp_path):
    # holdings flat, but files keep being rewritten: recent_bytes sees them
    f = tmp_path / "x"; f.write_bytes(b"x" * 8192)
    assert rates.recent_bytes([str(tmp_path)], now=os.stat(f).st_mtime + 10) >= 8192
    assert rates.rate_for("c", reported=None, recent_bytes=5, declared_mb_per_day=1.0) == (5.0, "measured")
    assert rates.rate_for("c", reported=7, recent_bytes=5, declared_mb_per_day=1.0) == (7.0, "reported")

def test_fast_growth_reaches_watch_before_its_percentage():
    s = Store("pool", "p", "pool", 365 * 10**9, int(0.67 * 365 * 10**9))
    assert store_stage(s, Levels()) == Stage.OK
    assert store_stage(s, Levels(), churn_bytes_per_h=3.7e9, horizon_h=48) == Stage.WATCH
```

Also: the tick test shows status `days` and per-client `days_held`; the `smd storage status` plain output leads with "holds D days"; the heartbeat `data["storage"]` gains `days`.

- [ ] **Step 2: Run them; expect FAIL.**  **Step 3: Implement.** Keep `allot` as a thin wrapper, or remove it and update its callers; don't leave two division rules.  **Step 4: Run the storage, CLI and heartbeat tests, then the full suite; expect PASS.**
- [ ] **Step 5: Commit** `storage: allot equal days of coverage, not equal bytes`

---

## After this plan

- **Each client adopts v0.9, one small plan per repo, using the Task 12 kit.** Order:
  - hf-timestd: the most data.
  - hs-uploader: owns `sink.db`.
  - psk-recorder: the reference client named in ADD-A-CLIENT.
  - Then codar-sounder, hfdl-recorder, mag-recorder, hf-tec, meteor-scatter, superdarn-sounder and hamsci-physics.
  - wspr-recorder writes only to `sink.db`, so it needs a declaration and no verbs.

  Until a client adopts, the ledger measures it with `du`, and the stop lever can reach it at the final stage.
- **hf-timestd** (its own plan, in its repo): implement the three `storage` verbs and the v0.9 inventory fields (floor about 1 h of raw IQ; policy `give-up-oldest:raw`). Move `resource_guardian`'s eviction to 98 %, and fold `timestd-prune.timer` into the trim.
- **hs-uploader** (its own plan): own `sink.db`'s registration, and answer "confirmed sent".
- **Rollout:** B4 first, announced on the claude-bus beforehand. The PM scripts are image-frozen, so `pm-heartbeat-setup.sh` has to run on each PM by hand. KFS-OMNI-SW-PM, which carries two VMs, needs `[pool] share` set on each guest.
- **Pins** arrive with `smd archive keep` (planned in `tasks/plan-external-storage.md`). The ledger then lists active pins in the status file, and clients read them from there. Nothing in this plan blocks that.
- **Asking for room beyond an allotment** (spec, "Who trims"): a fourth verb, once a client needs it. Until then an operator raises that client's share.
- **Phase 2:** `discard=on` and a weekly `fstrim` with the radiods stopped.
