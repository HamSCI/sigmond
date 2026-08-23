# Documentation Program — Phase 2 (scientist guide + hardware/character.md) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A scientist with Python and ≤ 1 week goes from "I need to listen on 14.110 MHz from Friday" to a running capture on a DASI2 station, and knows how to graduate it to a sigmond client — using only `sigmond/docs/scientist/` + `docs/hardware/character.md` and what they link.

**Architecture:** Six narrative pages under `docs/scientist/` plus one shared `docs/hardware/character.md`, all following the Phase 0/1 conventions (header block, ★-canonical, linked-not-restated). Two explicit tiers: **Tier 0 capture-only** (a standalone ka9q-python script or the existing `event-recorder` tool, no sigmond contract) and **Tier 1 conformant client** (the ADD-A-CLIENT path + a copyable skeleton). The worked example is the real 2026-08-12 eclipse Costas listener, whose code is the public repo `mijahauan/Costas-array` (package `event_recorder`). Every numeric claim is cited to code, a doc, or a dated live reading; the Tier-0 recipe is proven by a fresh-context agent on the DASI002 testbed.

**Tech Stack:** Markdown; ka9q-python (PyPI `ka9q-python`, 3.20.x) for the recipe; read-only `smd` + one ephemeral radiod channel on DASI002 for proof; the existing `scripts/docs-linkcheck.py` + `tests/test_docs_links.py`.

**Spec:** `sigmond/docs/superpowers/specs/2026-08-23-documentation-program-design.md` — §4 (scientist), §6 (`character.md`), §2 (structure/rules), §9 (verification), §10 (Phase 2 "done when").

## Global Constraints

- **No product code changes.** Only `*.md` under `sigmond/docs/` (+ the skeleton files under `docs/scientist/skeleton/`, which are documentation scaffolding, and INDEX/front-door/ledger edits). Software gaps → one row in `docs/contributor/docs-gap-ledger.md` (rows 1–32 exist; next is 33), never a code edit.
- **Workspace:** repos at `/home/mjh/hamsci/repos/<repo>/`; the worked-example repo is cloned read-only at `/tmp/claude-1000/-home-mjh-hamsci/c34744cd-f838-4226-981e-840c532862e2/scratchpad/Costas-array` (re-clone from `https://github.com/mijahauan/Costas-array` if missing). Ops memory notes at `/home/mjh/.claude/projects/-home-mjh-hamsci/memory/` are LEADS, never citations — re-verify each fact against code/doc/live before it lands in a page.
- **Commit to `main` in sigmond; no branches; no push** (owner pushes). Commit message trailer, verbatim:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_012XQRNXmBj87SxR5H5UxZqt
  ```
- **Header block** (verbatim shape) under the H1 of every page created/edited:
  ```
  > **Audience:** scientist
  > **Status:** current
  > **Verified against:** sigmond <sha> on <YYYY-MM-DD> — <how: live dasi002 / live b4 / code / docs>
  > **Canonical for:** <topic>
  ```
  (`character.md`: `**Audience:** scientist, contributor`.)
- **Conventions:** `/home/mjh/hamsci/repos/sigmond/docs/contributor/docs-conventions.md` §3 §4 §7 — link glossary/operator pages for jargon the scientist may not know; **link ka9q-python docs rather than restating them**; every code fence that runs on a station is preceded by a line saying WHERE (`[VM]` / your laptop); no bare `[VM]` token inside a fence.
- **Links & checks before every commit:** `cd /home/mjh/hamsci/repos/sigmond && python3 scripts/docs-linkcheck.py docs README.md CONTRIBUTING.md CLAUDE.md` → exit 0, and `.venv/bin/pytest tests/test_docs_links.py -q` → pass. Cross-repo links use `https://github.com/HamSCI/<repo>/blob/main/<path>` (the checker validates them against the local checkout) — the Costas-array repo is under `mijahauan`, link it as `https://github.com/mijahauan/Costas-array` (external; not validated).
- **Live verification is read-only with ONE exception.** Helper: `/tmp/claude-1000/-home-mjh-hamsci/c34744cd-f838-4226-981e-840c532862e2/scratchpad/fleet-ro.sh b4|dasi002 '<cmd>'` runs as `sigmond` inside the VM (PM → VM nested ssh). Never sudo/restart/stop/--apply/--fix/edits/config init/edit on any host. **Exception (ruled):** on **DASI002 only** (the plumbing testbed: no antenna, data useless), the Tier-0 recipe may create ONE ephemeral radiod channel via ka9q-python with an explicit `lifetime` (≤ 6000 frames ≈ 120 s) and record ≤ 2 minutes into the `sigmond` user's home or `/tmp` — never into `/var/lib`, never a systemd unit, never on B4. Radiod's status/control multicast name on DASI002 is `DASI002-status.local` (from `/etc/radio/radiod@DASI002.conf` line 16); on B4 it is the analogous `AC0G-B4-status.local` (verify before use). System `python3` on the VMs has no `ka9q` module — the recipe uses its own venv (`python3 -m venv ~/tier0 && ~/tier0/bin/pip install ka9q-python`); if PyPI is unreachable from the VM, fall back to `pip install -e /opt/git/sigmond/ka9q-python` and say so in the page.
- Ports/paths known (verify): ka9q-web `:8081`; hf-timestd raw IQ `/var/lib/timestd/raw_buffer/<CHAN>/<YYYYMMDD>/`; sink `/var/lib/sigmond/sink.db` (`pending_uploads`); event-recorder output `/var/lib/event-recorder/<job>/*.sigmf-{data,meta}` (B4 holds the eclipse capture: 51 files).

---

## File map

**Create (sigmond):**
- `docs/scientist/README.md` (replaces the Phase-0 stub) — the five-step path, the two tiers, required reading
- `docs/scientist/station-capabilities.md` ★ — the envelope in one place
- `docs/scientist/capture-quickstart.md` ★ — Tier 0: the 30-line recipe + `event-recorder run` + pre-flight + prove-against-WWV
- `docs/scientist/costas-14110-worked-example.md` ★ — the eclipse listener as narrative
- `docs/scientist/becoming-a-client.md` ★ — Tier 1 bridge + sink/uploader hand-off
- `docs/scientist/skeleton/{README.md,deploy.toml,my-recorder@.service,my_recorder/cli.py,config/help.toml}` — minimal conformant scaffold (documentation scaffolding, MIT header)
- `docs/scientist/data-and-timing.md` ★ — where data lands; RTP↔UTC; tiers; caveats
- `docs/hardware/character.md` ★ — how the hardware behaves, each claim with evidence

**Modify (sigmond):** `docs/INDEX.md` (§2 Scientist rows, §4 Hardware row; Verified bump), `docs/README.md` (scientist door: drop "(Phase 2 — until then …)"), `docs/hardware/shopping-list.md` (one link to `character.md`), `docs/EVENT-CLIENT-PLAYBOOK.md` (header block only + one "see scientist guide" line under the H1), `docs/contributor/docs-gap-ledger.md` (new rows from 33).

---

### Task 1: `docs/scientist/station-capabilities.md`

**Files:** Create `docs/scientist/station-capabilities.md`; modify `docs/INDEX.md` (§2 row ★).

Sources (read, then cite): `docs/EVENT-CLIENT-PLAYBOOK.md` §"The channel envelope" (l.66-85: the `ensure_channel` knob table — frequency, preset iq/usb/lsb/am/fm/cw, sample_rate, encoding S16/F32/F16/µ-law/A-law/Opus, low/high edge, agc/gain, kaiser_beta, **lifetime mandatory**, never pass destination) and §"Budget the load" (l.85-120: the hf-timestd-vs-WSPR A/B); `/home/mjh/hamsci/repos/ka9q-python/docs/RTP_TIMING_SUPPORT.md` + `GETTING_STARTED.md` §4 stream layers; `/home/mjh/hamsci/repos/hf-timestd/docs/METROLOGY.md` §4.5 and the tier table around l.227-229 (T6 TS-1 BPSK-PPS ns-class; T5 USB GPS+PPS µs–ms; T4 LAN GPS/NTP; T3 HF fusion ±0.5 ms when locked; T2 internet NTP; T1 GPSDO rate only) and l.318-334 (witness/disagreement rules); `docs/hardware/shopping-list.md` (RX888 2 Gbit/s, USB-3, 10 kHz–64 MHz span per ka9q-radio rx888.md; storage measured 15 GB/ch/day raw at hf-timestd's rate); `docs/operator/day-2.md` §3 (disk guardian 80/95/600 s/90); `docs/PRODUCER-THREAT-MODEL.md` (20 ms block deadline inside radiod, zero-fill loss event, ±25.6 s GRAPE amplification — cite its section); ops memory leads to RE-VERIFY: `reference_rx888_frontend_agc.md` (front-end AGC is real: `agc_rx888()` in ka9q-radio `src/rx888.c`, AD8370 VGA — grep the fork checkout `/home/mjh/hamsci/repos/ka9q-radio/src/rx888.c` for `agc_rx888`), `reference_grape_spectrogram_gaps.md` (`gap_count` honest, byte counts lie — cite the sidecar fields under `/var/lib/timestd/raw_buffer/...json` on b4, read-only), `reference_t6_holdover_coast.md` (1.44 µs/h — cite `t6_residual_rate` evidence path if present in hf-timestd docs; else phrase as "measured on B4 2026-08-16, hf-timestd#… " only if you find the doc/issue), `reference_b4_wspr_vs_hftimestd_radiod_overload.md` (radiod limited by L1/L2 cache, all its threads on one sibling pair — cite `docs/PRODUCER-THREAT-MODEL.md` / `docs/proxmox/*` if they say it). Live (read-only): `fleet-ro.sh b4 'smd status | sed -n "1,200p" | grep -i -E "channels|ch,|freqs"'` for the live channel counts; `fleet-ro.sh b4 'grep -n -E "samprate|samplerate|encoding|agc|gain|fft-threads|blocktime" /etc/radio/radiod@*.conf | head -30'`.

- [ ] **Step 1: Write the page** with these sections (each claim → `(source: …)`):
  ```
  # What this station can give you — the capability envelope
  (header block; Canonical for: the DASI2 station capability envelope for a new client)
  ## The one-paragraph version
  ## Frequency and bandwidth — what radiod will hand you (span; the knob table summarised + link to the playbook's table; presets; sample-rate menu — state which sample rates radiod actually serves for IQ vs audio presets (read ka9q-radio docs/ka9q-radio.md or presets.conf.md) ; low/high edge; encoding and the F32 create-path caveat — link character.md)
  ## How many channels you may add — the load budget (today's live counts on B4 (date); the A/B lesson; radiod is cache-bound not core-bound; the `blocktime`/20 ms deadline; how to measure the load you add: `smd status` CPU line + radiod's own status; "ask before adding more than N" — derive N from the playbook's calibration section l.315-334 and say it is a rule of thumb)
  ## Timing you can rely on — tiers (table T6…T1 with accuracy class and availability; which tier labels your data and where to read it (data-and-timing.md); holdover; LBE-1421 PPS over USB = T5 (µs–ms), its gpsdo-monitor statistics are liveness only)
  ## Storage per channel-hour (15 GB/ch/day raw F32 at 16 kHz? — NO: compute from the live measurement in day-2/shopping-list (15.07 GB/day for hf-timestd's WWV channel: state the sample rate + encoding that produced it — find it in hf-timestd config on b4 read-only: `grep -rn "sample_rate\|encoding" /etc/hf-timestd/*.toml | head`), then give the formula bytes/s = sample_rate × 8 (F32 complex) or × 4 (S16 complex) and a 3-row table 12 kHz / 24 kHz / 96 kHz)
  ## AGC and gain — science posture (AGC off + fixed gain per channel; the RX888 FRONT-END AGC exists and is ON on B4 — what that means for absolute amplitude; how to see the current gain)
  ## Loss semantics — what "a gap" is (radiod zero-fills a dropped block; `gap_count`/events in the hf-timestd sidecar are honest, byte counts are not; ±25.6 s amplification for spectrogram products; your recorder must record its OWN gap evidence — link playbook "Assume every failure will be silent")
  ## What the station cannot do (no second RX888; no Wi-Fi; no inbound ports; B4 is local-rx888-only — new channels mean a radiod preset + a client, not a remote receiver; timing below T6 needs the TS-1 which may be absent)
  ## Next: capture-quickstart.md
  ```
- [ ] **Step 2: Verify** every number on the page against its source; run the two live commands; fill `Verified against:` with the sha + "live b4 (smd status, radiod conf) + code/docs".
- [ ] **Step 3:** INDEX §2 row (★); checks; commit `docs(scientist): station capability envelope`.

---

### Task 2: `docs/hardware/character.md`

**Files:** Create `docs/hardware/character.md`; modify `docs/hardware/shopping-list.md` (one sentence + link under "The one-paragraph version": "How the hardware *behaves* — dynamic range, AGC, loss modes, timing roles: [character.md](character.md)"); `docs/INDEX.md` §4 row ★.

Sources: spec §6 list. Evidence to read and cite: `/home/mjh/hamsci/repos/ka9q-radio/src/rx888.c` (`agc_rx888`, gain range — grep `agc`, `gain`), `ka9q-radio/docs/SDR/rx888.md` (stale "no front-end AGC" sentence — say the code contradicts it and link it), `docs/PRODUCER-THREAT-MODEL.md` (block deadline, zero-fill, USB starvation, ±25.6 s), `docs/PACKET-LOSS-DIAGNOSTICS.md`, `bin/sigmond-timing-watchdog` docstring (RX888 sample-rate glitch ⇒ RTP anchor corruption ⇒ FT8 dies silently; watchdog re-anchors — already summarised in `docs/operator/troubleshooting.md` §Spots stopped), `scripts/proxmox/sigmond-wizard.sh` RX888 block ("Only removing power — or physically replugging the RX888 — resets it" — the FX3 latch), `docs/hardware/shopping-list.md` (GPSDO OUT1 10 MHz / OUT2 27 MHz live reading; RX888 27 MHz ext-ref per rx888.md; **jack mapping unrecorded — ledger row 5**; TS-1 has its own GPS per hf-timestd docs; LBE-1421 PPS over USB = T5/liveness), `hf-timestd/docs/METROLOGY.md` (T6 via TS-1 BPSK-PPS in the RX path; `t6_residual_rate` / holdover if documented), `/home/mjh/hamsci/repos/mag-recorder/docs/PROVENANCE.md` + `README.md` (RM3100 over Pololu USB-I²C; the NACK-⇒-frozen-constant failure — find the 2026-08-18 incident in mag-recorder's CHANGELOG/issues or docs; else phrase "observed on B4 2026-08-18: a NACKing sensor records a frozen constant — replug, then restart mag-recorder"), `docs/proxmox/*.md` + `docs/operator/do-not-touch.md` (radiod on isolated cores; vfio USB passthrough ⇒ host keyboard dies; `-smp 14,cores=7,threads=2` live on b4), `ka9q-radio/src/radio_status.c` `encode_radio_status()` (GPS_TIME live vs RTP_TIMESNAP cached — the anchor pair is not atomic; re-read the code), ka9q-python `ka9q/control.py` (OUTPUT_ENCODING sent as a separate command after create; `ensure_channel` may return before it lands ⇒ "F32 requested, S16 seen" race — re-read), hf-timestd issue #22 / `reference_fuse_cochannel_capture` (WWV/WWVH/BPM co-channel capture on shared MHz — cite the hf-timestd issue or doc if found; else omit).

- [ ] **Step 1: Write the page** — sections: The RX888 (stream rate, dynamic range/ADC bits from rx888.md, front-end AGC real and on, USB-3 only, the 20 ms deadline and zero-fill, USB sample loss ⇒ anchor steps + watchdog, FX3 latch needs power-off, one per host); The GPSDO (LBE-1421: what disciplines what — as far as is PROVABLE; OUT1/OUT2; holdover 1.44 µs/h with its evidence; PPS over USB = T5, gpsdo-monitor stats = liveness); The TS-1 (own GPS; BPSK-PPS injected into the RX path ⇒ T6 ns-class after chain-delay calibration; what you lose without it); The magnetometer (RM3100 + Pololu; 1 Hz JSONL; NACK failure mode; `smd watch mag` caveat → ledger row 19); The host (Proxmox VM; radiod isolated cores; CPU count change ⇒ sample loss; USB passthrough consequences); Timing-chain caveats (anchor pair not atomic ⇒ treat `GPS_TIME`–`RTP_TIMESNAP` pairs as block-grid + emission-lateness; encoding race; co-channel capture if sourced). Each claim ends `(source: path:symbol | live host date)`.
- [ ] **Step 2:** shopping-list link; INDEX row; checks; commit `docs(hardware): how the station hardware behaves (character)`.

---

### Task 3: `docs/scientist/capture-quickstart.md` (Tier 0)

**Files:** Create `docs/scientist/capture-quickstart.md`; modify `docs/INDEX.md`.

Sources: `ka9q-python/docs/GETTING_STARTED.md` (RadiodControl, `ensure_channel`, ChannelInfo, stream layers, ManagedStream), `RECIPES.md` Recipe 2 (fixed channel sets), `RTP_TIMING_SUPPORT.md` + `examples/test_timing_fields.py` + `examples/rtp_recorder_example.py` (RTPRecorder, on_packet(header, payload, wallclock)), `docs/EVENT-CLIENT-PLAYBOOK.md` (Rule 1 capture-first; envelope; lifetime; never destination; pre-flight checklist l.282-300; station traps l.301-314), the Costas-array README "Capturing" section (job TOML: name/frequency_hz/preset iq/sample_rate 12000/encoding f32/low_edge −5000/high_edge 5000/lead_in_sec/segment_sec/start_utc/stop_utc/out_dir; `event-recorder run --job …`; output SigMF `.sigmf-data` + `.sigmf-meta` with the absolute UTC of sample 0).

- [ ] **Step 1: Write the recipe page** with sections:
  ```
  # Capture first — the Tier-0 recipe
  ## Before you start (pre-flight — the playbook's checklist, condensed; B4 vs a testbed; talk to the fleet admin about load; decide the envelope with station-capabilities.md)
  ## Option A — use event-recorder (fastest): clone https://github.com/mijahauan/Costas-array, `python3 -m venv venv && ./venv/bin/pip install '.[capture]'`, write the job TOML (copy the block), run `event-recorder run --job …` — say what it produces (SigMF + meta with UTC of sample 0), that it creates its radiod channel dynamically with a lifetime and lets radiod reclaim it, and that it was the tool used for the eclipse (link the worked example)
  ## Option B — 30 lines of ka9q-python (the script below, verbatim; runs on the VM in your own venv)
  ## Prove it against a known signal first (WWV 10/15 MHz USB/AM: you must SEE the tick; the playbook says why)
  ## Run it unattended (user-level `systemd-run --user` or a `screen`; why a watchdog; write your own gap evidence)
  ## What you have now (files, timing anchor, where NOT to write on a production station — /var/lib belongs to the clients; use your home or an agreed dir)
  ## Next: data-and-timing.md, becoming-a-client.md
  ```
  The Option-B script (write it to the page; it MUST run — Task 8 proves it on DASI002). Shape:
  ```python
  #!/usr/bin/env python3
  """tier0_capture.py — record one radiod channel to raw complex-F32 with a UTC anchor.
  Usage: tier0_capture.py --status DASI002-status.local --freq 10000000 --preset iq \
         --rate 12000 --seconds 60 --out ~/capture
  """
  import argparse, json, struct, time, pathlib
  from ka9q import RadiodControl                      # verify the import path in GETTING_STARTED.md
  from ka9q.rtp_recorder import RTPRecorder           # verify: examples/rtp_recorder_example.py

  def main():
      ap = argparse.ArgumentParser(); ap.add_argument("--status", required=True)
      ap.add_argument("--freq", type=float, required=True); ap.add_argument("--preset", default="iq")
      ap.add_argument("--rate", type=int, default=12000); ap.add_argument("--seconds", type=int, default=60)
      ap.add_argument("--out", default="~/capture"); a = ap.parse_args()
      out = pathlib.Path(a.out).expanduser(); out.mkdir(parents=True, exist_ok=True)
      ctl = RadiodControl(a.status, client_id="tier0")
      ch = ctl.ensure_channel(frequency_hz=a.freq, preset=a.preset, sample_rate=a.rate,
                              encoding="F32LE", lifetime=6000, agc_enable=0)   # verify kwarg names against the library
      # … open RTPRecorder on ch's multicast destination, write payload bytes to out/<name>.f32,
      #     and write a sidecar JSON with the FIRST packet's (rtp_timestamp, gps_time/wallclock) pair
      #     + ch.ssrc + a.rate + encoding — per RTP_TIMING_SUPPORT.md
  ```
  The implementer MUST fill the `…` with working code read from the ka9q-python examples (no placeholders may remain on the page), run it on DASI002 per the Global-Constraints exception (60 s, `~/capture`, lifetime 6000), paste the trimmed real output and the sidecar JSON into the page, and delete the capture files afterwards (`rm -rf ~/capture` on DASI002 — the only delete allowed, of your own files).
- [ ] **Step 2:** checks; INDEX row; commit `docs(scientist): Tier-0 capture quickstart (proven on DASI002)`. Report must include the exact commands run on DASI002 and their output.

---

### Task 4: `docs/scientist/costas-14110-worked-example.md`

**Files:** Create the page; modify `docs/INDEX.md`.

Sources: the Costas-array repo (README §"It works: 2026-08-12 results" l.73-101, `docs/eclipse-reception-report.html`, `docs/figures/*.svg`, `deploy.toml` notes on why no `[[radiod.fragment]]` and no unit yet, `src/event_recorder/{scheduler,writer,sigmfmeta,contract,cli}.py` for what it does), `docs/EVENT-CLIENT-PLAYBOOK.md` §"What good cost, for calibration" (l.315-334) and traps, and the ops memory `project_eclipse_costas_14110.md` as a LEAD for the narrative facts — each fact stated on the page must be re-verified in the repo/report (dates 2026-08-11 23:11:20Z → 08-12 21:14:11Z, 22.23 h, 7.68 GB, 26 segments, 0 gaps, 25/25 anchored; envelope: 14.110 MHz iq 12 kHz f32 ±5 kHz; frame: 24 × 40 ms slots, pilot slot 1, 21 Costas tones `300 + 120·k` Hz, 960 ms + 40 ms guard, GPS-locked; result: detection in the 19:14–20:14Z segment, 20/21 slots on the peak frame, rank metric beats score, pilot measured ≈695–700 Hz — the published 1000 Hz was wrong; negative control 03:14–04:14Z rank 1.00). State what is NOT confirmed (TX schedule/locations) as such.

- [ ] **Step 1: Write the narrative**: The ask (one day's notice); Decisions (capture-first; envelope and why; load check; SigMF; scheduler; systemd-run; no fragment/unit — and why that was right for a one-off); What we verified before the event (WWV?) — say honestly what the repo/record shows; The night of (timeline); The result (the 2026-08-12 "no detection" → 2026-08-13 detection, and the lesson about negative controls and rank vs score); What we'd change (earlier TX confirmation; promote to a `@.service` + fragment only if it becomes recurring; write gap evidence; a watchdog); Where the data and code are (B4 `/var/lib/event-recorder/eclipse-costas-14110/` 51 files; repo link; report link). Link the playbook's lessons by section.
- [ ] **Step 2:** ledger row: "the worked-example repo lives under a personal GitHub account (mijahauan/Costas-array); transfer to HamSCI/event-recorder so the docs can link it by the org" (owner: HamSCI org). Checks; INDEX; commit `docs(scientist): Costas 14.110 MHz worked example`.

---

### Task 5: `docs/scientist/becoming-a-client.md` + `docs/scientist/skeleton/`

**Files:** Create `becoming-a-client.md`, `skeleton/README.md`, `skeleton/deploy.toml`, `skeleton/my-recorder@.service`, `skeleton/my_recorder/cli.py`, `skeleton/config/help.toml`; modify `docs/INDEX.md`, `docs/contributor/docs-gap-ledger.md`.

Sources: `docs/ADD-A-CLIENT.md` (TL;DR seven things; §2 deploy.toml; §3 unit; §4 the contract subcommands `version`/`inventory --json`/`validate --json`/`daemon` (+ optional `quality`) and the wizard subcommands `config init|edit|show --json|apply --json -`; §5 `[client_features]`; §6 catalog; §7 verify; §8 help.toml); `docs/CLIENT-CONTRACT.md` §3 §5 §12 §14 §15 §16 §17 §18 §19 (cite by section, do not restate); the Costas-array `deploy.toml` + `src/event_recorder/contract.py` (a real minimal conformant surface — copy its shape, simplify); `meteor-scatter/deploy.toml` + `systemd/meteor-scatter@.service` + `config/help.toml` (a full client, for the unit/help shapes); `lib/sigmond/hamsci_sink/writer.py` (`Writer.from_env(table, mode=..., database=..., schema_version=...)`, `insert(rows)`, `flush()`; `pending_uploads` JSON queue; no-op when no sigmond sink dir — copy the docstring's facts); `hs-uploader/docs/PER-SITE-SETUP.md` §7 (`[[hs_uploader.pipeline]]` declaration in the client's deploy.toml; placeholders; `smd admin uploader manifest --check|--write|--enable`); `hs-uploader/src/hs_uploader/transports/` (wsprnet, pskreporter, wsprdaemon, psws_magnetometer, heartbeat_sftp — i.e. **no generic PSWS transport for a new product** → ledger row 3a); `smd component add <git-url>` (`--help` text: "git URL of the component repo").

- [ ] **Step 1: Write `becoming-a-client.md`**: When to graduate (recurring; wants sigmond lifecycle/updates/heartbeat; wants hs-uploader); The seven things (ADD-A-CLIENT TL;DR, one line each + link); Start from the skeleton (what each file is; how to rename; how to run `validate/inventory/version` by hand; how sigmond installs it: `smd component add <url>` then `smd install <name>` — what those do per `bin/smd`/docs, and that on an appliance station the fleet admin does it); Channels: a `[[radiod.fragment]]` for a permanent channel vs dynamic channel with lifetime (when each); Writing rows to the sink (`Writer.from_env` example, 10 lines; table naming; schema_version); Shipping upstream (declare a pipeline; which transports exist today; **PSWS for a new product is not there — today you ship your own files; ledger row 3a**); Timing authority (§18: label rows with the tier; link data-and-timing); Instances/reporter_id (§19, one paragraph); Done when (the `smd status` block appears; `smd admin validate` passes; heartbeat shows it).
- [ ] **Step 2: Write the skeleton** — `deploy.toml` modelled on Costas-array's (name `my-recorder`, contract_version `0.8`, `[contract.cli] inventory/validate`, `[build]` venv steps, `[[install.steps]]` link+mkdir, `[[deps.pypi]] ka9q-python`), `my-recorder@.service` modelled on meteor-scatter's (User/Group, ExecStart `/usr/local/bin/my-recorder daemon %i`, Restart, EnvironmentFile `/etc/my-recorder/env/%i.env`), `my_recorder/cli.py` (argparse with `version`, `inventory --json`, `validate --json`, `daemon`, `config show --json` — each returning contract-shaped JSON with `issues: []`; `daemon` = a 20-line loop that calls the Tier-0 capture function or sleeps; **stdout cleanliness: JSON only on the contract verbs**), `config/help.toml` (three-tier audit shape from ADD-A-CLIENT §8 with two example keys), `skeleton/README.md` (how to copy: `cp -r docs/scientist/skeleton ~/my-recorder && …`; what to rename; what is stubbed; license MIT). Run the contract verbs locally on the devbox (`python3 skeleton/my_recorder/cli.py inventory --json | python3 -m json.tool`) and paste the output into skeleton/README.md.
- [ ] **Step 3:** ledger row 3a cross-check (exists from Phase 1 close-out: "3a PSWS transport for new clients") — add "needs: a generic sqlite→PSWS SFTP transport" if absent; checks; INDEX rows (becoming-a-client ★, skeleton/ as one row); commit `docs(scientist): becoming a client + minimal skeleton`.

---

### Task 6: `docs/scientist/data-and-timing.md`

**Files:** Create the page; modify `docs/INDEX.md`.

Sources: `ka9q-python/docs/RTP_TIMING_SUPPORT.md` (GPS_TIME, RTP_TIMESNAP → wallclock), `docs/CLIENT-CONTRACT.md` §17 (sinks) §18 (timing authority + RTP-default fallback), `hf-timestd/docs/METROLOGY.md` tiers + `ARCHITECTURE-FIRST-PRINCIPLES.md` ("RTP is the ruler"; substrate vs annotation), `docs/timing-chain-architecture.md`, `docs/operator/glossary.md` timing tier row, `character.md` (Task 2: anchor pair non-atomic; holdover), live read-only: `fleet-ro.sh b4 'ls /var/lib/timestd/raw_buffer/ | head; ls /var/lib/timestd/raw_buffer/WWV_25000/ | tail -2; ls /var/lib/timestd/raw_buffer/WWV_25000/$(ls /var/lib/timestd/raw_buffer/WWV_25000/ | tail -1) | head -4; cat $(ls /var/lib/timestd/raw_buffer/WWV_25000/*/*.json | head -1) | head -40'` (the sidecar shape), `fleet-ro.sh b4 'ls /var/lib/event-recorder/eclipse-costas-14110 | head -3; head -c 1200 /var/lib/event-recorder/eclipse-costas-14110/$(ls /var/lib/event-recorder/eclipse-costas-14110 | grep meta | head -1)'` (SigMF meta shape), `fleet-ro.sh b4 'ls -la /var/lib/sigmond/sink.db; sqlite3 /var/lib/sigmond/sink.db ".schema pending_uploads" 2>/dev/null'` (if sqlite3 absent, cite writer.py's schema).

- [ ] **Step 1: Write**: Where data lands (per-client dirs; the sink; SigMF for event captures; naming conventions observed); The clock story in five sentences (radiod stamps RTP timestamps on a sample grid; it publishes GPS_TIME (host clock) + RTP_TIMESNAP pairs; the host clock is disciplined by chrony from HPPS = T6 when TS-1 is present (timing-independence loop — say it plainly: GPS_TIME is host-clock-relative); hf-timestd publishes the tier and the RTP↔UTC offset; label your rows with the tier); The tiers again in one table (link capabilities); How to stamp your own capture (the anchor pair per RTP_TIMING_SUPPORT; the non-atomic caveat; record first-packet anchor + re-anchor on sequence gap); How hf-timestd's sidecars do it (the JSON fields, from the live read); Pitfalls (relying on wall-clock `time.time()`; F32 encoding race; radiod restart ⇒ anchor steps).
- [ ] **Step 2:** checks; INDEX; commit `docs(scientist): data locations and timing semantics`.

---

### Task 7: `docs/scientist/README.md` (replace stub), front door, playbook link

**Files:** Modify `docs/scientist/README.md` (replace the Phase-0 stub), `docs/README.md` (scientist door text — drop "(Phase 2 — until then …)"; keep Phase 3 wording), `docs/EVENT-CLIENT-PLAYBOOK.md` (add the header block if missing: Audience scientist, contributor; Status current; Canonical for: design judgment for an event/capture client; plus one line under the H1: "New here? Start at the scientist guide …"), `docs/INDEX.md`.

- [ ] **Step 1: Write README.md**: The path (1 read playbook 15 min → 2 station-capabilities 10 min → 3 capture-quickstart: Tier 0 running in an afternoon → 4 data-and-timing → 5 becoming-a-client when it recurs) with time estimates; **Tier 0 vs Tier 1** table (what you get, what you owe, when to upgrade); What you need from the fleet admin (station access, load approval, agreed output dir); What the station will NOT do for you (see capabilities §what it cannot do); Worked example link; Glossary link.
- [ ] **Step 2:** front door + playbook edits; checks; commit `docs(scientist): guide front page; front door; playbook cross-link`.

---

### Task 8: Fresh-eyes Tier-0 walk-through on DASI002 + fixes

- [ ] **Step 1: Dispatch a fresh-context subagent** (general-purpose) with ONLY this brief:
  > You are a physicist with good Python who has never seen this project. Your only documentation is `/home/mjh/hamsci/repos/sigmond/docs/scientist/` (start at README.md) and whatever it links to (GitHub links to HamSCI/<repo> → read the local checkout under /home/mjh/hamsci/repos/<repo>/; the Costas-array repo is cloned at /tmp/claude-1000/-home-mjh-hamsci/c34744cd-f838-4226-981e-840c532862e2/scratchpad/Costas-array). Your station is the testbed DASI002: run VM commands as `/tmp/claude-1000/-home-mjh-hamsci/c34744cd-f838-4226-981e-840c532862e2/scratchpad/fleet-ro.sh dasi002 '<cmd>'` (you are the `sigmond` user). RULES: you may create ONE radiod channel with an explicit lifetime ≤ 6000 and record ≤ 120 s into `~/capture` (delete it when done); you may `python3 -m venv ~/tier0` and pip install into it; NOTHING else may change state (no sudo, no smd mutating verbs, no systemd units, no /var/lib writes). Task: following the docs literally, (1) get a Tier-0 capture running on 10.000 MHz (WWV) with the recipe's Option B script, (2) show the files + timing sidecar it produced, (3) say how you would graduate it to a client per becoming-a-client.md (do not actually install anything), (4) list every point where you had to guess, a command failed, a term was undefined, two pages disagreed, or a claim looked wrong. Rate BLOCKER/CONFUSING/NIT. Write to `/tmp/claude-1000/-home-mjh-hamsci/c34744cd-f838-4226-981e-840c532862e2/scratchpad/scientist-walkthrough.md`; final message = path + counts.
- [ ] **Step 2: Triage** — every finding → doc fix now / ledger row / by-design sentence; apply; checks; commit `docs(scientist): walk-through fixes`.
- [ ] **Step 3:** If a BLOCKER remains after the fix round, run the brief once more; stop when no finding is a doc defect.

---

### Task 9: Phase 2 close-out

- [ ] **Step 1:** `docs/INDEX.md` §2 lists all six scientist pages + skeleton row, ★ on capabilities/quickstart/worked-example/becoming-a-client/data-and-timing; §4 has character ★; `docs/README.md` scientist door final; every touched page's `Verified against:` = the close-out sha.
- [ ] **Step 2:** `.venv/bin/pytest -q | tail -2` green; checker exit 0; `grep -rn '(being written)\|Phase 2' docs/README.md docs/scientist` → nothing stale.
- [ ] **Step 3:** `cd /home/mjh/hamsci && graphify update /home/mjh/hamsci/repos`.
- [ ] **Step 4:** ledger top note "Phase 2 done <date>; rows 33–N added"; commit `docs: phase 2 close-out`; report unpushed status (`git -C /home/mjh/hamsci/repos/sigmond status -sb | head -1`) — **do not push**.

---

## Self-review notes (plan-writing time)

- Spec §4 pages → Tasks 1,3,4,5,6,7 (README, station-capabilities, capture-quickstart, costas example, becoming-a-client + skeleton, data-and-timing); §6 `character.md` → Task 2; §9 verification → live recipe + Task 8 (fresh agent produces a Tier-0 capture on dasi002); §10 Phase 2 "done when" → Task 8/9.
- The only state change permitted on a station is the ephemeral DASI002 channel (Global Constraints) — ruled, bounded, testbed-only.
- The worked example's code is `mijahauan/Costas-array` (package `event_recorder`); its transfer to the HamSCI org is a ledger row, not an action.
- No placeholders: the Task 3 script sketch marks the lines the implementer must fill FROM the ka9q-python examples and requires the finished script to be proven on DASI002 before commit.
