# Event response, first slice: B4's flare response and the Geminids check — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every M1+ flare that strikes B4's sunlit paths lands B4's six time-channel windows on wd30 as Digital RF, with no copy left on B4; and meteor-scatter can count its MSQP totals for the Geminids.

**Architecture:** A stdlib-only job on wd30 turns the GOES flare list into orders in one `orders.json`.  On B4, an hf-timestd pass every five minutes pulls that file, cuts each closed window out of the raw buffer, converts it to Digital RF, uploads it to wd30 with a `SHA256SUMS`, and deletes its staging copy once wd30's verifier writes a `RECEIPT`.  meteor-scatter gains `msqp totals` and prefers the UberSDR jt9 when it decodes MSK144.

**Tech Stack:** Python 3.11 (hf-timestd venv: numpy, zstandard, digital_rf 2.6.14), stdlib-only Python 3.13 on wd30, rsync over ssh restricted by `rrsync`, systemd timers.

**Spec:** `docs/superpowers/specs/2026-11-06-event-response-design.md` (sigmond 32cd850): "The first tests", "The flare trigger", "Getting the data home", "Order of work" items 1–2.

## Global Constraints

- A flare makes an order only if its class is M1.0 or above (`max_class`), its `end_time` is known, and at its `max_time` the station's sun elevation lies above 0° and the sun stands above 0° at the great-circle midpoint of at least one monitored path.  B4 monitors WWV (40.6807, −105.0407) and WWVH (21.9872, −159.7636).  Coordinates come from `hf_timestd/core/broadcast_specs.py` `STATION_COORDINATES`.
- Window: `begin_time − 30 min` to `end_time + 90 min`.
- GOES feed: `https://services.swpc.noaa.gov/json/goes/primary/xray-flares-7-day.json`, read hourly, only on wd30.
- The storm-day (Kp) trigger stays off.  B4 alone (`AC0G-B4`, 38.9187367, −92.1277335) for this slice.
- wd30 tree: `/srv/wd_data/events/<event_id>/<station_id>/hf-timestd/<channel>/`; orders at `/srv/wd_data/events/orders/orders.json`.  `/srv/wd_data` is the 4 TB disk; never write events to wd30's 120 GB root.
- The station deletes its staging copy only after wd30's `RECEIPT` confirms every file's sha256.  The raw buffer itself prunes as before; this slice never deletes from it.
- Digital RF carries complex64 at the buffer's sample rate (24 kHz), one channel directory per buffer channel.  Each 10-minute buffer file's JSON sidecar goes, verbatim, into Digital Metadata at that file's first sample, together with the order.
- B4's unit runs as `timestd`, `Nice=19`, `IOSchedulingClass=idle`, `CPUSchedulingPolicy=idle`, and joins sigmond's `AFFINITY_UNITS` as `'other'`.  At most one window is staged at a time.
- Any change on B4 is announced on the claude-bus first; any root step on B4 or wd30 is staged as a script for Michael to run.
- hf-timestd tests: `.venv/bin/pytest <file> -o addopts=""` (the repo's `-q` addopts hides failures; see its CLAUDE.md).

## Review Focus

1. A flare still in progress (`end_time` null) → no order yet; the order appears on the first run after `end_time` arrives (Task 1, `test_open_flare_waits`).
2. A window crossing UTC midnight → files from both day directories, in time order (Task 2, `test_window_across_midnight`).
3. Buffer files missing inside the window (pruned, gap, recorder down) → upload what exists, list the missing file times in the metadata, never wait forever (Task 2, `test_missing_files_recorded`; Task 3, `test_partial_window_still_ships`).
4. wd30 unreachable or an rsync cut short → staging kept, retried next pass, never deleted, never restaged (Task 3, `test_upload_failure_keeps_staging`).
5. An order still listed after its upload → the state file marks it done; no second upload (Task 3, `test_done_order_not_redone`).

---

### Task 1: Flare orders (hf-timestd, stdlib only, runs on wd30)

**Files:**
- Create: `src/hf_timestd/events/__init__.py` (empty), `src/hf_timestd/events/flare_orders.py`
- Test: `tests/unit/test_flare_orders.py`

**Interfaces:**
- Produces, in `flare_orders.py`, importing only the standard library (it runs on wd30 as a plain file: `python3 flare_orders.py --stations <json> --out <orders.json>`):
  - `flare_class_value(cls: str) -> float`: `"M1.0"` → 1e-5, `"X2.3"` → 2.3e-4, `"C3.7"` → 3.7e-6 (W/m², the GOES scale)
  - `solar_elevation_deg(when: datetime, lat: float, lon: float) -> float`
  - `path_midpoint(lat1, lon1, lat2, lon2) -> tuple[float, float]` (great circle)
  - `flare_strikes(peak: datetime, station: dict) -> bool`; `station` = `{"id", "lat", "lon", "paths": [[lat, lon], …]}`
  - `orders_from_flares(flares: list[dict], stations: list[dict], now: datetime) -> list[dict]`
  - `merge_orders(existing: dict, new: list[dict]) -> dict`: idempotent by `event_id`
  - The orders file: `{"version": 1, "orders": [order, …]}`.  One order:
    `{"event_id": "20261106T1524Z-flare-M1.2", "name": "GOES M1.2 flare, peak 2026-11-06 15:24Z", "kind": "flare", "ordered_by": "goes-trigger", "reason": "GOES M1.2, B4's paths sunlit at peak", "start_utc": "...Z", "end_utc": "...Z", "stations": ["AC0G-B4"], "record": [{"client": "hf-timestd", "keep": "time-channels"}], "destination": "wd30", "created_utc": "...Z", "source": {<the GOES record, verbatim>}}`

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_flare_orders.py`:
  - `test_class_values`: `flare_class_value("M1.0") == 1e-5`, `"X2.3"` → `2.3e-4`, `"C9.9" < flare_class_value("M1.0")`.
  - `test_solar_elevation_matches_hamsci_dsp`: within 0.5° of `hamsci_dsp.ionosphere.solar.solar_position` (skip if hamsci_dsp is absent) at B4 for 2026-11-06 18:00Z (sun up) and 06:00Z (down).
  - `test_midpoint_wwv_b4`: the midpoint of B4–WWV lies between the two, with latitude between 38.9 and 40.7.
  - `test_night_flare_makes_no_order`: an M5 flare peaking 2026-11-06T06:00Z gives no order for B4.
  - `test_day_flare_makes_order`: M1.2 peaking 18:00Z, begin 17:50Z, end 18:20Z → one order, `start_utc == "2026-11-06T17:20:00Z"`, `end_utc == "2026-11-06T19:50:00Z"`, `event_id == "20261106T1800Z-flare-M1.2"`.
  - `test_c_flare_ignored`: C9.9 at noon → no order.
  - `test_open_flare_waits`: `end_time: null` → no order; the same flare with `end_time` set → an order.
  - `test_merge_is_idempotent`: merging the same orders twice leaves one copy each and keeps the first `created_utc`.
- [ ] **Step 2:** `.venv/bin/pytest tests/unit/test_flare_orders.py -o addopts=""` → FAIL (module missing).
- [ ] **Step 3: Implement.** For `solar_elevation_deg`, use NOAA's low-precision solar position (declination and equation of time from the day of year), as `hamsci_dsp/ionosphere/solar.py` does.  `main()` reads the stations JSON, fetches the feed with `urllib.request` (60 s timeout), merges with the existing orders file, and writes it atomically (tmp + `os.replace`).  A fetch failure leaves the file untouched and exits 1.
- [ ] **Step 4:** Same command → PASS.
- [ ] **Step 5: Commit** `events: GOES flare orders with the daylight test (stdlib, for wd30)`.

### Task 2: A window from the raw buffer to Digital RF (hf-timestd)

**Files:**
- Create: `src/hf_timestd/events/window_drf.py`
- Test: `tests/unit/test_window_drf.py`

**Interfaces:**
- Consumes: the buffer layout `/var/lib/timestd/raw_buffer/<CHANNEL>/<YYYYMMDD>/<start_unix>.bin.zst` + `<start_unix>.json` (complex64, 600 s files; sidecar keys include `sample_rate`, `frequency_hz`, `samples_written`, `station`, `timing`, `gap_count`), read through `hf_timestd.core.binary_archive_writer.BinaryArchiveReader` where it fits.
- Produces:
  - `window_files(raw_root: Path, channel: str, start: datetime, end: datetime) -> tuple[list[int], list[int]]`: the file start times overlapping the window that exist, and those that should exist but don't
  - `write_window_drf(raw_root: Path, channels: list[str], order: dict, out_dir: Path) -> dict`: writes `out_dir/<channel>/` Digital RF and `out_dir/<channel>/metadata/`, then `out_dir/order.json` and `out_dir/SHA256SUMS` (sha256 of every file under `out_dir` but itself, relative paths, sorted).  Returns `{"channels": {ch: {"files": n, "missing": [..]}}, "bytes": total}`.

- [ ] **Step 1: Write the failing tests**, with a synthetic buffer built in `tmp_path` (a few short zstd complex64 files and sidecars, made by a fixture):
  - `test_window_files_overlap`: a window from 00:05 to 00:25 over files at 00:00, 00:10 and 00:20 picks all three.
  - `test_window_across_midnight`: a window from 23:55 to 00:15 picks files from both day directories, in time order.
  - `test_missing_files_recorded`: delete the middle file → `missing == [its start]`.  The DRF still holds the other two at the right sample indexes.
  - `test_drf_roundtrip`: `digital_rf.DigitalRFReader` reads back exactly the samples written, at index `start_unix × sample_rate`.  The Digital Metadata at that index carries the sidecar dict and the `event_id`.
  - `test_sha256sums_cover_all`: every file under `out_dir` except `SHA256SUMS` appears once in it, with a correct hash.
- [ ] **Step 2:** `.venv/bin/pytest tests/unit/test_window_drf.py -o addopts=""` → FAIL.
- [ ] **Step 3: Implement.**
  - Write one `DigitalRFWriter` per channel: `is_complex=True`, `num_subchannels=1`, `dtype=np.complex64`, `subdir_cadence_secs=3600`, `file_cadence_millisecs=60000`, `start_global_index = first file start × rate`.
  - Write each buffer file at its own index (`rf_write(samples, next_sample=…)`), so gaps stay gaps.
  - Write the Digital Metadata with `DigitalMetadataWriter`, as `hamsci-physics/src/hamsci_physics/grape/packager.py` does.
- [ ] **Step 4:** Same command → PASS.
- [ ] **Step 5: Commit** `events: cut a window from the raw buffer into Digital RF`.

### Task 3: The keeper pass on the station (hf-timestd)

**Files:**
- Create: `src/hf_timestd/events/keeper.py`, `systemd/timestd-events.service`, `systemd/timestd-events.timer`, `config/events.toml.template`
- Modify: `src/hf_timestd/cli.py` (an `events` subparser with a `run` verb), `scripts/install.sh` (install the two units beside `timestd-prune.*`, enabled only when `/etc/hf-timestd/events.toml` exists)
- Test: `tests/unit/test_events_keeper.py`

**Interfaces:**
- Consumes: Task 1's orders file format, and Task 2's `write_window_drf`.
- Produces:
  - `/etc/hf-timestd/events.toml`:
    ```toml
    station_id  = "AC0G-B4"
    remote      = "wsprdaemon@192.168.1.164"
    remote_root = "/srv/wd_data/events"
    ssh_key     = "/var/lib/timestd/.ssh/events_ed25519"
    staging     = "/var/lib/timestd/events-staging"
    raw_root    = "/var/lib/timestd/raw_buffer"
    settle_minutes = 15
    bwlimit_kbps = 0
    ```
  - `run_once(cfg: dict, now: datetime, transport: Transport) -> list[str]`: a log line per action.
  - `class Transport`, with methods `pull(remote_path, local_path) -> bool`, `push(local_dir, remote_dir) -> bool` and `fetch_receipt(remote_dir, local_path) -> bool`.  `RsyncTransport` implements it with `rsync -a --partial -e "ssh -i <key> -o BatchMode=yes"`, plus `--bwlimit` when it isn't zero.  Tests use `LocalTransport`, which does the same rsync against local paths.
  - State: `<staging>/state.json`: `{"<event_id>": "staged" | "uploaded" | "done" | "failed:<reason>"}`.
  - The command: `hf-timestd events run [--config PATH]`, which exits 0 even when wd30 can't be reached and logs it.

- [ ] **Step 1: Write the failing tests**, with the Task 2 fixture buffer, a fake remote directory, and a `LocalTransport`:
  - `test_not_ready_before_settle`: an order whose `end_utc + settle_minutes` lies after `now` → nothing staged.
  - `test_ready_window_ships`: the order is staged, pushed to `<remote>/<event_id>/AC0G-B4/hf-timestd/`, its state is `uploaded`, and the staging copy stays (no receipt yet).
  - `test_receipt_deletes_staging`: with a matching `RECEIPT` on the remote, the next run deletes the staging copy and sets the state to `done`.
  - `test_bad_receipt_keeps_staging`: a `RECEIPT` whose hash differs from local `SHA256SUMS` → staging kept, state `failed:receipt-mismatch`, logged.
  - `test_upload_failure_keeps_staging`: a transport whose `push` returns False → staging kept, state `staged`; the next run pushes again without restaging.
  - `test_done_order_not_redone`: state `done` → that order never restages.
  - `test_other_station_ignored`: an order for `"AC0G-ND"` does nothing on B4.
  - `test_partial_window_still_ships`: missing buffer files still ship, and `order.json`'s `missing` lists them.
  - `test_one_window_at_a_time`: two ready orders → only one is staged per run while the first awaits its receipt.
- [ ] **Step 2:** `.venv/bin/pytest tests/unit/test_events_keeper.py -o addopts=""` → FAIL.
- [ ] **Step 3: Implement.**
  - `RECEIPT` holds one line: `sha256 of SHA256SUMS` + two spaces + `verified <UTC>`.  The keeper compares that hash with its own `SHA256SUMS`.
  - Unit: `Type=oneshot`, `User=timestd`, `Nice=19`, `IOSchedulingClass=idle`, `CPUSchedulingPolicy=idle`, `EnvironmentFile=-/etc/sigmond/coordination.env`.
  - Timer: `OnCalendar=*:0/5`, `Persistent=true`.
- [ ] **Step 4:** Same command → PASS.  Then run the existing suite's quick files touched by `cli.py`: `.venv/bin/pytest tests/unit -o addopts="" -k "cli"` → no new failure.
- [ ] **Step 5: Commit** `events: keeper pass ships closed windows to the server and deletes after receipt`.

### Task 4: The server side on wd30 (hf-timestd scripts, stdlib only)

**Files:**
- Create: `scripts/events/events_server.py`, `scripts/events/systemd/events-orders.{service,timer}`, `scripts/events/systemd/events-verify.{service,timer}`, `scripts/events/stations.json` (B4 only, with its WWV and WWVH paths), `scripts/events/README.md`
- Test: `tests/unit/test_events_server.py`

**Interfaces:**
- Consumes: `flare_orders.py` (Task 1), imported by path, so wd30 needs only the two files.  Also the station's `SHA256SUMS`, written by Task 2.
- Produces:
  - `events_server.py orders --stations stations.json --out /srv/wd_data/events/orders/orders.json`
  - `events_server.py verify --root /srv/wd_data/events`: for every `<event>/<station>/<client>/` holding a `SHA256SUMS` and no `RECEIPT`, it checks each listed file.  If all match, it writes `RECEIPT` (the format from Task 3), atomically.  If any mismatch, it writes `FAILED` with the bad paths, and the station keeps its copy.  A directory still receiving files, with any file younger than 60 s, is skipped.
  - Units run as `wsprdaemon`, at `Nice=19`.  Orders run hourly (`OnCalendar=hourly`); verify runs every five minutes.

- [ ] **Step 1: Write the failing tests:**
  - `test_verify_writes_receipt`: hashes that match produce a `RECEIPT` that equals the station's expected line.
  - `test_verify_flags_corruption`: one byte flipped produces `FAILED` naming that file, and no `RECEIPT`.
  - `test_verify_skips_fresh`: a file modified 10 s ago leaves the directory without a `RECEIPT`.
  - `test_orders_cli_writes_file`: with a fake feed (a local JSON path through `--feed`), the subcommand writes a valid orders file.
- [ ] **Step 2:** `.venv/bin/pytest tests/unit/test_events_server.py -o addopts=""` → FAIL.
- [ ] **Step 3: Implement.**  The README gives the wd30 setup:
  - `mkdir -p /srv/wd_data/events/orders`
  - the `authorized_keys` line for B4's key: `restrict,command="rrsync /srv/wd_data/events" ssh-ed25519 …`
  - the four units
- [ ] **Step 4:** Same command → PASS.
- [ ] **Step 5: Commit** `events: wd30 order job and upload verifier`.

### Task 5: sigmond knows the unit

**Files:**
- Modify (sigmond): `lib/sigmond/cpu.py` `AFFINITY_UNITS` gains `'timestd-events.service': 'other'`
- Test (sigmond): the existing affinity tests (`.venv/bin/pytest tests -k affinity`)

- [ ] **Step 1:** Add the entry next to `timestd-vtec.service`.
- [ ] **Step 2:** `.venv/bin/pytest tests -k affinity -q` → PASS.
- [ ] **Step 3: Commit** (sigmond) `cpu: timestd-events joins the worker cores`.

### Task 6: B4 and wd30, end to end (controller, with Michael)

No code.  Every root step goes into a staged script that Michael runs with `!`.

- [ ] **Step 1:** Post on the claude-bus: the change on B4 (a new timer and unit, an ssh key for `timestd`, `/etc/hf-timestd/events.toml`, nothing else restarted) and on wd30 (a directory, two timers, one restricted key).
- [ ] **Step 2:** On wd30, stage and run the README's setup.  On B4, stage and run: `git -C /opt/git/sigmond/hf-timestd pull --ff-only` (hf-timestd's deploy mechanics, never a reinstall), the key with `ssh-keygen -t ed25519 -N '' -f /var/lib/timestd/.ssh/events_ed25519` as timestd, the config, the units, and `systemctl enable --now timestd-events.timer`.
- [ ] **Step 3: A hand-made order proves the path.**  On wd30, add an order for B4 over a closed window still in the buffer, say the last two hours before the test.  Set `ordered_by` to `"mjh"`, kind `other`, and name it as a test.  Expected within 20 minutes: `/srv/wd_data/events/<id>/AC0G-B4/hf-timestd/` holds six channel directories, a `RECEIPT` appears, B4's staging is empty, and its state reads `done`.
- [ ] **Step 4:** Check the copy on wd30 with `digital_rf.DigitalRFReader`: six channels, 24 kHz, the bounds equal to the window, the Digital Metadata carrying the sidecars and the order.
- [ ] **Step 5:** Leave it running.  Report the first real flare's window to Michael and Rob, with its size on wd30: the measurement the design asked for.

### Task 7: `msqp totals` (meteor-scatter)

**Files:**
- Modify: `src/meteor_scatter/msqp_cli.py` (a `totals` subparser), `src/meteor_scatter/core/archive.py` (reuse `recordings(first, last)`)
- Test: `tests/test_msqp_cli.py`

**Interfaces:**
- Produces `meteor-scatter msqp totals [--first YYYY-MM-DD] [--last YYYY-MM-DD] [--json]`.  It prints one row per band and UTC day (kept slots, bytes), the totals, and the projected package size against Zenodo's 50 GB record limit, as a percentage.  The default span is the last 14 days.

- [ ] **Step 1: Write the failing tests:** an archive fixture with two bands over three days.
  - `test_totals_counts_slots_and_bytes`: counts and byte sums per band and day.
  - `test_totals_json_shape`: the keys `days`, `total_slots`, `total_bytes`, `zenodo_limit_bytes == 50_000_000_000`, `zenodo_fraction`.
  - `test_totals_empty_archive`: zero slots, exit 0, a line saying so.
- [ ] **Step 2:** `uv run pytest tests/test_msqp_cli.py -k totals -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Same command → PASS.
- [ ] **Step 5: Commit** `msqp: totals per band and day, against Zenodo's record limit`.

### Task 8: meteor-scatter prefers the UberSDR jt9, and `-Y` told straight

**Files:**
- Modify: `src/meteor_scatter/core/decoder.py` (`resolve_jt9_binary` candidate order; the `build_jt9_cmd` docstring)
- Test: `tests/test_decoder.py` (or the existing resolver tests' file)

**Interfaces:**
- The candidate order becomes: the explicit override, then `/usr/local/libexec/sigmond/decoders/ubersdr/jt9`, then `WSJTX_JT9`, then jt9 on PATH.  The first whose `-h` lists `--msk144` wins, as now.  Today's madpsy build lists no `--msk144`, so stations fall through to 3.0.2 until sigmond builds the new one, and nothing changes before then.

- [ ] **Step 1: Write the failing test** `test_prefers_ubersdr_when_it_decodes_msk144`.  With fake candidates where both the UberSDR and the WSJT-X paths probe true, the resolver returns the UberSDR path.  `test_falls_back_when_ubersdr_lacks_msk144`: the UberSDR probe false → the WSJT-X path.
- [ ] **Step 2:** `uv run pytest tests -k resolve -v` → FAIL.
- [ ] **Step 3: Implement**, and correct the docstring: in jt9, `-Y` selects FST4W with hash22 output.  Before `--msk144` it does nothing, because the last mode named wins.  An unresolved MSK144 hash prints `<...>`.  Keep `-Y` in the argv; removing it changes nothing and costs a test.
- [ ] **Step 4:** Same command → PASS; then `uv run pytest tests/test_decoder*.py tests/test_slot.py -q` → no new failure.
- [ ] **Step 5: Commit** `decoder: prefer sigmond's UberSDR jt9 once it decodes MSK144; say what -Y does`.
