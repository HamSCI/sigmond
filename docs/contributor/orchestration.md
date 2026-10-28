# How sigmond works — orchestration in one page

> **Audience:** contributor
> **Status:** current
> **Verified against:** sigmond 913bfed on 2026-10-23 — code (`smd sink --help`, `commands/sink.py`, `commands/config.py` `_site_change`, `commands/uploader.py`, `install_lock.py`; earlier rows: `smd fleet --help`, `fleet.py:700,705,1028`)
> **Canonical for:** sigmond's orchestration model and the smd verb→module map

## The shape

Sigmond does not record, decode, or upload anything. It is the **installer,
configurator, and lifecycle manager** that makes a pile of independent HamSCI
repos behave as one station: ka9q-radio's `radiod` produces multicast RTP from
an RX888, a set of Python clients (`wspr-recorder`, `psk-recorder`,
`hf-timestd`, `mag-recorder`, `meteor-scatter`, …) subscribe to it and write
products into a shared SQLite sink, and `hs-uploader` ships those products
outward. Every one of those clients lives in its own repo with its own
`install.sh` and its own systemd units; sigmond **delegates to them and never
duplicates them**. What sigmond owns is the seam — which clients this host
knows about, which are enabled, what units they resolve to, in what order they
start, whether their configs contradict each other, and what the station
reports about itself. The whole-suite picture is
[`../architecture.png`](../architecture.png); the seam itself is specified in
[`../CLIENT-CONTRACT.md`](../CLIENT-CONTRACT.md) ★.

The user-facing surface is one stdlib-only Python program, `bin/smd`
(20,221 lines today), plus the `lib/sigmond/` package it leans on. Core `smd` imports
nothing outside the standard library — Textual is a lazy import reached only by
`smd tui` — because it has to run on a freshly-imaged host before any venv
exists.

## Layers

The thirteen layers named in [`../../CLAUDE.md`](../../CLAUDE.md) §Architecture
layers, each with the module that implements it:

| # | Layer | Module | What it answers |
|---|---|---|---|
| 1 | Catalog | `lib/sigmond/catalog.py`, `etc/catalog.toml` | what *could* be installed here (three merged layers — see below) |
| 2 | Installer | `lib/sigmond/installer.py` | clone to `/opt/git/sigmond/<name>`, run the client's own `install.sh` |
| 3 | Lifecycle | `lib/sigmond/lifecycle.py` | which systemd units a client resolves to, from its `deploy.toml` |
| 4 | Logging | `lib/sigmond/log_cmd.py` | journal + file-log tailing, runtime log level via `coordination.env` + SIGHUP |
| 5 | Status/diag enrichment | `bin/smd` `_get_client_inventory`, `cmd_status`, `cmd_diag` | each client's own `inventory --json` / `validate --json` |
| 6 | Contract adapter | `lib/sigmond/clients/contract.py`, reached via `lib/sigmond/sysview.py` | translate a client's contract JSON into an internal `ClientView` |
| 7 | Harmonization | `lib/sigmond/harmonize.py` | cross-client rules: CPU isolation, frequency coverage, radiod resolution, timing chain |
| 8 | Lifecycle lock | `lib/sigmond/lifecycle.py` (`lifecycle_lock`) | flock on `/var/lib/sigmond/lifecycle.lock`; mutating verbs take it, read-only verbs never do |
| 9 | Start ordering | `lib/sigmond/lifecycle.py` (`order_units`) | radiod first, then clients in `coordination.toml` order; stop is reversed |
| 10 | Catalog-walk install | `bin/smd` (`cmd_install`, `_install_radiod_native`, `_build_ka9q_web_with_onion`) | bare `smd install` iterates catalog × topology; C projects build in-tree |
| 11 | TUI configurator | `lib/sigmond/tui/` (Textual, lazy import) | the three-panel interactive surface behind `smd tui` |
| 12 | Environment discovery | `lib/sigmond/commands/environment.py`, `lib/sigmond/discovery/` | mDNS/IGMP/NTP/HTTP probing of network peers |
| 13 | ka9q-radio drift watcher | `lib/sigmond/commands/ka9q_watch.py` | is the pinned ka9q-radio commit still stream-compatible with upstream |

Two cross-cutting concerns are not layers but shape everything: **CPU pinning**
(each local radiod owns one hyperthread sibling pair; decoder clients are
confined to the worker cores via `AFFINITY_UNITS` in `lib/sigmond/cpu.py` — add
a new decoder client there or it silently runs on radiod's cores) and the
**sink** (`sigmond.hamsci_sink.Writer.from_env()` picks `$SIGMOND_SQLITE_PATH`,
else `/var/lib/sigmond/sink.db` if writable, else a no-op writer so a client
stays standalone-safe).

## Production paths

The FHS anchors — `/etc/sigmond`, `/var/lib/sigmond`, `/var/log/sigmond`,
`/run/sigmond` and the files under them — live in
[`../../lib/sigmond/paths.py`](../../lib/sigmond/paths.py); the rest are named at
their point of use: `GIT_BASE` in `discover.py` / `installer.py`, the operator
catalog layer in `catalog.py` (`DEFAULT_CATALOG_PATHS`), `site-profile.toml` in
`capture_prep.py`, the sink default in hs-uploader's `sink/writer.py` (copied
into `hamsci_sink/_bundled.py`), and `upload-wake.sock` in ka9q-python's
wspr_recorder.

| Path | What it is |
|---|---|
| `/opt/git/sigmond/<name>/` | every component's checkout, sigmond's own included (`/opt/git/sigmond/sigmond`) |
| `/usr/local/bin/smd` | symlink to the checkout's `bin/smd` — so an edit in the repo is live, no reinstall |
| `/etc/sigmond/topology.toml` | what is enabled on this host |
| `/etc/sigmond/coordination.toml` + `coordination.env` | station identity, radiod registration, per-client env bag |
| `/etc/sigmond/site-profile.toml` | per-site profile written by `personalize` |
| `/etc/sigmond/catalog.toml` | operator overrides only — the sparse top layer of the catalog (sparse: only the keys present here override; everything else falls through to the layer below) |
| `/etc/sigmond/environment.toml` | declared network peers, checked against observation |
| `/var/lib/sigmond/sink.db` | the shared SQLite sink every producer writes into |
| `/var/lib/sigmond/upload-wake.sock` | stateless edge trigger: "something committed, go re-derive completeness" |
| `/var/lib/sigmond/lifecycle.lock` | the flock layer 8 takes |
| `/var/log/sigmond/` | sigmond's own logs (clients log to their own paths, declared in `deploy.toml`) |
| `/etc/<client>/`, `/var/lib/<client>/` | each client owns its own config and state; sigmond reads, rarely writes |

## How a client is discovered and enabled

The catalog is **three layers merged by sparse per-field overlay** — only keys
present in a higher layer override, the rest fall through:

1. **Discovery** — synthesized from each `/opt/git/sigmond/<name>/deploy.toml`.
   This is the drop-in path: clone a contract-conformant repo and sigmond knows
   about it with no sigmond-side edit.
2. **Repo default** — `etc/catalog.toml` shipped with sigmond. Adds what cannot
   be discovered (`radiod` has no checkout under `/opt/git/sigmond/`),
   source-only deps, and `[deprecated.<name>]` blocks that *exclude* a retired
   name so a stale `deploy.toml` cannot revive it.
3. **Operator override** — `/etc/sigmond/catalog.toml`, per host, ideally only
   the fields that genuinely diverge. `smd config catalog-prune` trims it back
   to those and runs automatically at the end of `install.sh`.

Sparse overlay is why a new client added to the repo catalog reaches every host
on a plain `git pull` — the older first-file-wins design let a stale operator
file shadow the whole catalog.

**Install implies enable.** `smd install <name>` sets `enabled = true`
(`_enable_after_install`; `--no-enable` opts out), and naming a component to
`smd start` auto-enables it if it is installed-but-disabled
(`_autoenable_named_on_start`). The operator model is **download → install →
configure → start**; `enable` is never a required step, and the off-switch is
`smd disable <name>` (stop + clear the flag, reversibly). Do not reintroduce a
mandatory manual `enable`.

**Core vs discretionary.** The split lives in the catalog profiles.
`[profile.dasi2].clients` installs by default; `[profile.dasi2].optional` is
added by `smd bringup dasi2 --with-optional` or the TUI's "Optional clients"
checkbox. Read the current membership out of
[`../../etc/catalog.toml`](../../etc/catalog.toml) rather than from prose — it
moves (ledger row 54).

## The verb map

Every top-level verb and every `admin` subverb, with the handler that runs it
and the `lib/sigmond/` module it leans on. Handlers live in `bin/smd` unless the module column names a `commands/…` file
(`cmd_uploader_manifest` is in `commands/uploader.py`, `cmd_timing_show` in
`commands/timing_show.py`).
**This table is CI-checked** — `tests/test_docs_cli_table.py` parses
`smd --help` and `smd admin --help` and fails if a verb is missing here or a
row names something that is not a verb. There are no deprecated top-level verbs
left to list: the v2 removals in
[`../CLI-V2-SPEC.md`](../CLI-V2-SPEC.md) §5 are gone from argparse entirely
(ledger row 52).

| Verb | Who / when | What it does | Implementation | Mutates? |
|---|---|---|---|---|
| `admin` | contributor | umbrella for diagnostics + maintenance; bare form prints the group help | no handler — `main()` rewrites `args.command = args.admin_command`, so every relocated verb keeps its old dispatch branch | via its subverb |
| `adopt` | after attaching hardware | adopt one offer named by `smd status`: record its sources for the components that consume them, then enable and start what the offer brings, after an explicit yes (`--yes` for scripted provisioning; an absent terminal is never read as consent). `--dry-run` reports the plan and starts nothing. It RECORDS a selection; it does not render one into a client's config — adopt prints `smd admin sources apply` as the next step, because that is the verb that writes `[[source]]` tables (and today only wspr-recorder is multi-source aware). The only verb in the adoption design that starts anything — detection alone never does. Distinct from `admin manifest adopt`, which adopts a blessed image manifest | `cmd_adopt` → `adoption.py` (`offers`, `plan`, `SOURCE_CONSUMERS`), `station_identity.py` (`identify` for membership from the hostname, `read_manifest` / `identity_from_manifest` for the PSWS IDs the PM recorded), `sources.py` (`ClientSources`), then `cmd_start` under `lifecycle_lock` | yes (root + lock), except `--dry-run` |
| `align` | station-inward | dry run only: how far this station sits from the latest blessed appliance release — target, per-component plan, commit distance, image-carried file drift, host recorded-vs-blessed level; `--no-cost` skips the commit-distance lookups | `cmd_align` → `align.py` (`fetch_release`, `plan_align`, `commit_distance`, `image_file_drift`, `recorded_release`), `doctor.py`, `provenance.py`, `catalog.py` | no |
| `apply` | after a config edit | reconcile running units with current config: re-render radiod fragments + firmware, regenerate the uploader manifest, restart what changed | `cmd_apply` → `lifecycle.py`, `commands/radiod_fragments.py`, `commands/radiod_firmware.py`, `commands/uploader.py`, `coordination.py` | yes (root + lock) |
| `bringup` | first install | guided station bring-up from a catalog profile; `--with-optional` adds the discretionary set | `cmd_bringup` → `bringup.py` (`build_plan`), `catalog.py`, `coordination.py`, `site_profile.py`, `psws.py`; the plan's steps shell out to sub-`smd` calls, which is how `topology.py` is reached (`smd enable <comp>`) | yes (root) |
| `check` | installer, daily | does this station work: one line per item (PSK, WSPR, magnetometer, GRAPE, storage, uploads, meteor scatter, the VM's own pages), each marked `ok`, `wait` (not yet), `FAIL` or `--` (not on this station), in ASCII for the host console, then PASS (exit 0), WAIT (exit 2) or FAIL (exit 1). `--no-verdict` drops the verdict line for `sigmond-check` on the Proxmox host, which adds the page lines through the host's addresses; `--json` serves tests and tools. It never calls the timing judge (tasks/spec-smd-check-v372.md) | `cmd_check` → `commands/check.py`, `component_state.py` (`compute_state`), `heartbeat.py` (`_read_backlog`), `coordination.py` | no |
| `component` | installer | per-component catalog + status: `list`, `install`, `update`, `add`, `remove`, `enable`, `disable` | dispatch branch in `main()`: `list` and `update` both reach `cmd_list` (`update` sets `args.update=True`); `update`'s actual work is `cmd_list` → `_apply_updates` (pull each catalog repo per its version policy) → `_rebuild_component` per repo that advanced (`install.sh`/`uv sync`, or `make install` for `ka9q-radio`/`ka9q-web`) → `cmd_apply` (reconcile); `install`/`add`/`remove`/`enable`/`disable` → `cmd_install`, `cmd_add`, `cmd_remove`, `cmd_enable`, `cmd_disable`; `catalog.py`, `installer.py` | `install`/`update`/`add`/`remove` do (root) |
| `config` | installer | show / migrate / init / render / edit station and per-client config; also `catalog-prune`, `backup`, `restore`, `uploads`, `profile-install` (the wizard installs a new site-profile.toml through it, keeping a site sink switch already set), `register-radiod` | dispatch branch → `commands/config.py`, `site_profile.py` (`carry_uploads_block`), `commands/client_config.py`, `commands/radiod_config.py`, `catalog_prune.py`, `psws.py` | writing subverbs do (root) |
| `disable` | operator | take a component offline reversibly — stop its units, clear the topology flag | `cmd_disable` → `topology.py`, `catalog.py` | yes (root) |
| `doctor` | station-inward update | checkout health: ownership, venv skew, dirty trees; `--fix` repairs ownership only | `cmd_doctor` → `doctor.py` | `--fix` does (root) |
| `enable` | scripting | set `enabled = true` in topology — rarely typed, install and start do it for you | `cmd_enable` → `topology.py`, `catalog.py` | yes (root) |
| `fleet` | fleet-outward | subverbs `status`, `doctor`, `roster`, `update`, `pubkeys`. The fan-out (`status`/`doctor`/`roster`/`pubkeys`) is read-only, enforced by a test-checked whitelist; `fleet update` multiplexes the station-inward update procedure host by host, canary first | `cmd_fleet` → `fleet.py` | `update --apply` does, one host at a time |
| `install` | installer | install + configure + enable one component, or walk catalog × topology for the whole suite | `cmd_install` → `installer.py`, `catalog.py`, `discover.py`, `preflight.py`, `topology.py` | yes (root + lock) |
| `notify` | operator | fault-notification outbox: `test`, `status`, `list`, spooling to `/var/lib/sigmond/notify/` | `cmd_notify` — self-contained in `bin/smd` | `test` writes a spool record |
| `psws` | registration | station-level PSWS enrolment: `status`, `enroll`, `verify`, `motd` | dispatch branch → `psws.py` | `enroll`/`verify` do (root) |
| `reload` | operator | reload config in place via the control socket or SIGHUP, falling back to restart | `cmd_reload` → `control_socket.py`, `lifecycle.py` | yes (root + lock) |
| `remote` | operator | the operator's switch for remote access on an appliance station: `status`, `off` (stays off across reboots), `on`. It asks the Proxmox host over the private link, with a key that can run one script there (`sigmond-rac-switch`), and prints the host's answer; the host alone turns the tunnel on or off (tasks/plan-remote-access.md). `motd` prints the login banner's one line from the last answer | dispatch branch → `commands/remote.py` | all but `motd` do (root: the pairing key belongs to root) |
| `restart` | operator | restart managed units, `reset-failed` first | `cmd_restart` → `lifecycle.py`, `component_state.py`, `catalog.py` | yes (root + lock) |
| `sink` | operator | the site sink switch, in the sink words of tasks/plan-sink-control.md §2.1. `status` shows it. `off` sends no data and stores nothing new for upload; it needs `--reason` and a typed confirmation or `--yes`. `fill` stores and sends no data; until v3.79, sigmond's trim keeps FT8 spots for one hour and WSPR spots for 24 hours, sent or not. `upload` stores and sends. With an hs-uploader that offers `gate`, each verb writes `site.toml` and the `[uploads]` mirror together ([The site sink switch](#the-site-sink-switch)); without it, `[uploads]` alone, as v3.78 did. Leaving `off` keeps today's and yesterday's (UTC) packages and moves older ones aside; it refuses while packaging runs. The verb is site-only: `smd sink <client>` refuses. It shares its handler with `upload` | `cmd_sink` → `commands/sink.py`, `commands/config.py` (`cmd_config_uploads`, `_site_change`, `_close_doors`), `sink_levers.py`, `sink_mirror.py`, `sink_state.py`, `sink_doors.py` | all but `status` do (root) |
| `status` | daily | service health snapshot plus each client's own inventory: version, channels, frequencies, timing judge, PSWS state | `cmd_status` → `catalog.py`, `cpu.py`, `psws.py`, `timing_judge.py`, `ui.py` | no |
| `start` | operator | start managed units in `order_units()` order; also enables an installed-but-disabled named component | `cmd_start` → `lifecycle.py`, `component_state.py`, `harmonize.py` | yes (root + lock) |
| `stop` | operator | stop managed units, reverse start order | `cmd_stop` → `lifecycle.py` | yes (root + lock) |
| `timing` | daily | timing authority in one line — tier, offset, σ, judge age, GPSDO state | `cmd_timing_show` → `commands/timing_show.py` (reads `/run/hf-timestd/authority.json`) | no |
| `tui` | operator | launch the three-panel Textual configurator; re-execs into the production venv if Textual is missing | `cmd_tui` → `tui/` (lazy import) | via its screens |
| `update` | station-inward | the pull-orientation updater: plan first, `--apply` executes; refuses rather than discarding a colliding local change | `cmd_update` → `update.py`, `provenance.py`, `catalog.py`, `doctor.py`, `wisdom.py` | `--apply` does (root) |
| `upload` | operator | the legacy words for the site sink switch, kept as an alias: `status`, `on` (store and send; same as `sink upload`), `hold`/`off` (same as `sink fill`: send no data; the trim keeps FT8 spots about an hour and WSPR spots about a day, and `on` then sends what is still stored), `discard` (same as `sink off`; needs `--reason` and a typed confirmation or `--yes`). Leaving `discard`, by any verb, keeps today's and yesterday's (UTC) packages and sets older ones aside, as `sink upload` does. `config uploads` reaches the same handler | `cmd_config_uploads` → `commands/config.py`, `coordination.py` (`Uploads.mode`), `uploader_manifest.py` (`effective_mode`, which serves only the render without `gate`, and renders the legacy hold when the hs-uploader the service runs cannot honour discard), `sink_doors.py` | all but `status` do (root) |
| `version` | station-inward | image lineage plus each component's commit read live from its checkout | `cmd_version` → `provenance.py`, `doctor.py` | no |
| `watch` | daily | live tail for one target: `wspr`, `psk`, `hfdl`, `codar`, `superdarn`, `meteor`, `hf-tec`, `mag`, `ka9q`, `radiod`, `gpsdo`, `usb`, `uploads`, `verifier` | dispatch branch → `cmd_<target>_watch` in `bin/smd`; `ka9q` alone is `commands/ka9q_watch.py` | no |
| `admin capture-prep` | image build | strip per-site identity, secrets, and data before golden-image capture — the inverse of `personalize`; plan-first | `cmd_capture_prep` → `capture_prep.py`, `site_profile.py`, `instance.py`, `psws.py`, `coordination.py` | `--yes` does (root) |
| `admin completion` | setup | emit the bash tab-completion script | `cmd_completion` — self-contained | no |
| `admin decoders` | tuning | show which WSPR/FT8 decoder build is live (UberSDR's or WSJT-X's), or `--select` the other; both stay installed so a fallback needs no rebuild | `cmd_decoders`, `_select_decoders`, `_decoder_findings` | `--select` does (root) |
| `admin diag` | debugging | cross-component diagnostics; sub-targets `cpu-affinity`, `cpu-freq`, `net`, `drop-in` | `cmd_diag`, `cmd_diag_cpu_affinity`, `cmd_diag_cpu_freq`, `cmd_diag_net`, `cmd_diag_drop_in` → `cpu.py`, `net_diag.py`, `diag_drop_in.py` | read-only unless `--apply` (root) |
| `admin docs` | station-web | `index` lists the documentation folders of each component checked out under `/opt/git/sigmond`: every page's title, whether its repo's `docs/INDEX.md` links it, and its last commit date; `--json` feeds the `docs` snapshot station-web reads. Run as root, it caches commit dates in `/var/lib/sigmond/docs-index-cache.json`; any other user computes them fresh | `cmd_admin_docs_index` → `docs_index.py` | no; root writes the date cache |
| `admin environment` | debugging | peers declared in `environment.toml` versus what is observed: `list`, `probe`, `describe` | `cmd_environment_*` → `commands/environment.py`, `discovery/`, `environment_kinds.py` | `probe` writes the observation cache |
| `admin heartbeat` | fleet | assemble + spool one 5-minute envelope (`emit`) or show the spool (`show`) | `cmd_admin_heartbeat_emit` / `cmd_admin_heartbeat_show` → `heartbeat.py`, `heartbeat_schema.py`, `uploader_manifest.py`, `provenance.py` | `emit` writes the spool |
| `admin instance` | multi-reporter hosts | per-reporter client instance lifecycle: `list`, `show`, `add`, `remove`, `edit`, `enable`, `disable`, `migrate` | `cmd_instance_*` → `instance.py` | all but `list`/`show` (root) |
| `admin log` | debugging | follow a client's journal, or its file logs with `--files`; `--level` sets the level via `coordination.env` + SIGHUP | `cmd_log` → `log_cmd.py`, `component_state.py`, `catalog.py` | `--level` writes `coordination.env` (ledger row 53) |
| `admin manifest` | after a release | adopt a blessed image manifest onto an already-running host (`adopt`), or restore one (`restore`) | `cmd_manifest_adopt`, `cmd_manifest_restore` → `manifest_adopt.py`, `manifest_restore.py`, `provenance.py` | `--apply` does (root) |
| `admin personalize` | first boot of a clone | re-identify a cloned image: hostname, identity, config render, secrets, validate; plan-first | `cmd_personalize` → `site_profile.py`; the rest of the sequence shells out to `smd config render`, `smd admin secrets status`, `smd admin validate` | `--yes` does (root) |
| `admin public-ip` | debugging | this host's outbound public IPv4 + IPv6 | `cmd_public_ip` — self-contained | no |
| `admin rac` | remote access | the wd-rac (frpc) tunnel — bare form is state-aware; also `status`, `configure`, `reconfigure`, `install`, `start`, `stop`, `restart`, `register` | `cmd_rac` + `_rac_configure` in `bin/smd`; the TUI path uses `rac_config.py` | configuring + lifecycle subverbs (root) |
| `admin radiod` | migration | radiod canonical-naming operations (`migrate`), per [`../RADIOD-IDENTIFICATION.md`](../RADIOD-IDENTIFICATION.md) | `cmd_radiod_migrate` → `radiod_migrate.py` | `--apply` does (root) |
| `admin readiness` | image build | machine-checkable gate: is this VM fit to capture as the golden image, or fit to run as a station? | `cmd_readiness` → `commands/readiness.py`, `readiness.py` | no |
| `admin secrets` | provisioning | install/validate delivered per-site secrets (Earthdata netrc, RAC token): `status`, `template`, `install`, `bundle` | `cmd_secrets_*` in `bin/smd`, driven by the in-file `_SECRETS` table (name, dest, mode, validator) | `template`/`install`/`bundle` (root) |
| `admin snapshot` | station-web | `write` runs a fixed set of read-only `--json` verbs and stores each as an envelope in `/run/sigmond/snapshot/` for station-web; run by `sigmond-snapshot.timer` (5 min) and, on station-web's request, `sigmond-snapshot-request.path` | `cmd_write` → `snapshot.py` | `write` (root) |
| `admin sources` | multi-SDR hosts | which SDR source each client consumes from: `list`, `add`, `remove`, `apply` | `cmd_sources_*` → `sources.py`, `instance.py` | `add`/`remove`/`apply` (root) |
| `admin storage` | maintenance | the local sink backend: `migrate-to-sqlite`, `trim`, `tune-timestd` | `cmd_storage_migrate_to_sqlite`, `cmd_storage_trim`, `cmd_timestd_tune_storage` → `storage_migrate.py`, `storage_trim.py` | `--yes`/`--apply` (root) |
| `admin timing` | debugging | timing-chain reconciler across GPSDO → gpsd → chrony → hf-timestd | `cmd_timing` → `commands/timing.py` | plan-first; applying needs root |
| `admin viewers` | debugging | list the station web viewers (ka9q-web per radiod, gmag-webui) with port, unit, state and port conflicts | `cmd_admin_viewers` → `viewers.py` | read-only |
| `admin uninstall` | teardown | tear down the whole sigmond install; plan-first, `--yes` to execute | `cmd_uninstall` → `uninstall.py` | `--yes` does (root) |
| `admin uploader` | uploads | generate `/etc/hs-uploader/pipelines.toml` from each client's `deploy.toml` (`manifest`); see [The uploader manifest step](#the-uploader-manifest-step) | `cmd_uploader_manifest` → `commands/uploader.py`, `uploader_manifest.py` | `--write`/`--enable` (root) |
| `admin validate` | before a change | run the cross-client harmonization rules, read-only | `cmd_validate` → `commands/validate.py`, `harmonize.py`, `sysview.py` | no |
| `admin verifier` | uploads | upload audit — uploaded → verified → lost, for `wspr`, `psk`, `timestd` (`report`, `rehabilitate`) | `commands/verifier_report.py`, `verifier_report_psk.py`, `verifier_report_timestd.py` | `rehabilitate` does (root) |
| `admin wisdom` | tuning | FFTW3 wisdom planner radiod reads from `/etc/fftw/wisdomf`: `status`, `plan` | `cmd_wisdom_status`, `cmd_wisdom_plan` → `wisdom.py` | `plan` does (root) |

Two conventions the table encodes. **Read-only verbs never take the lifecycle
lock and never elevate** — `status`, `component list`, `log`, `diag`, `validate` are safe
to run concurrently; the mutating set `{install, apply, start, stop, restart,
reload}` takes the flock in `main()`, and the rest self-elevate through
`_need_root()`. And **`smd` refuses to be run under `sudo`**: it re-execs
itself as root per privileged step, so a `sudo smd …` invocation exits with an
error telling you to drop the prefix.

## Updates

Every update procedure stands in one of two chairs, and mixing them is how
fleets break — [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §3 is canonical.
**Station-inward (pull)**: you are on the machine, it looks out to the
repositories for itself — `smd version`, `smd doctor`, `smd update` (plan),
`smd update --apply`, then `smd admin manifest adopt` to record which blessed
baseline it now satisfies. It needs git remotes and nothing else, which is why
it is the only orientation a public install will ever have.
**Fleet-outward (push)**: you are the fleet administrator deciding *when* and
in *what order*; the mutation is still the station-inward procedure, run on
each host in turn. The fan-out (`smd fleet status|doctor|roster|pubkeys`) can
only ask questions — its command vocabulary is a test-enforced whitelist.
`smd fleet update` is the administered multiplexer: it runs that same
station-inward procedure host by host, canary first (B4 is the declared
canary), and `--apply` is what mutates — a dry-run plan otherwise.

Because every consumer venv installs its siblings **editable**, a `git pull` of
a suite library propagates to every venv with no further action — that is what
`smd component update` exploits. The two layers to check are *source on disk*
(git) and *code loaded in memory* (a long-running service holds its start-time
bytecode until restarted; compare `systemctl show -p ActiveEnterTimestamp`
against the library's commit time). `smd doctor` detects the failure mode where
a venv has acquired a *copy* of a sibling instead of a link, as `venv-skew`.

Version provenance is its own trap: `/etc/sigmond-appliance/version` is written
once at firstboot and never updated, so it lies after any in-place update — read
`smd version`, which reports lineage separately from the live component commits.
The image/appliance side of that boundary gets its own page,
[`appliance-boundary.md`](appliance-boundary.md) ★.

## The uploader manifest step

`smd admin uploader manifest` renders `/etc/hs-uploader/pipelines.toml`.
Without `--write` it only compares: it prints "is up to date" or the drift,
exits non-zero on drift, and changes nothing (no root needed).  `--write` (root) makes the change.  `--enable` takes the same steps, installs the
daemon first when it is missing, and leaves it enabled and running.  `apply`, `align`,
bring-up, `sink` and `admin manifest restore` all run the write form.

**What the renderer asks the daemon.**  It asks the daemon's own interpreter
which store version it reads (`hs-uploader capabilities`).  At version 2 or
higher it splits the shared `psk-pskreporter` pipeline into
`psk-recorder-pskreporter` (modes ft8 and ft4) and
`meteor-scatter-pskreporter` (mode msk144).  Below version 2 it renders the
shared pipeline and prints a warning.  If the probe fails, the renderer
keeps the manifest as it stands and exits non-zero.

It then asks the same interpreter which features it offers
(`hs-uploader capabilities --features`, `uploader_manifest.hs_uploader_features`),
once and first, and prints one line: `uploader: hs-uploader features: gate
pipelines.d send-log-v3 switch-files`, the words sorted.  An hs-uploader
from v3.73 to v3.76 rejects the flag with exit 2, and the line then reads
`features: none`.  If the probe fails or times out, the step prints a
warning and renders v3.78's form.  Every call into hs-uploader runs the
daemon's own interpreter as `python -I -m hs_uploader.cli`, from `/`
(`uploader_manifest.run_hs_uploader`).  `-I`, isolated mode, keeps the
working directory, `PYTHONPATH` and the user's site directory out of
`sys.path`, so nothing can shadow `hs_uploader`.

**The install lock.**  `--write` (root) holds hs-uploader's install lock,
`/run/hs-uploader-install/lock`, from the features probe through the
landing (`install_lock.held`), so two manifest steps never interleave.
The lock lives in a directory only root can write, not in the
world-writable `/run/lock`, so no other account can plant a file or a link
at its name.  The step creates the directory at mode 0700.  It accepts an
existing one only when `lstat` shows a real directory owned by root that
neither group nor other can write, and checks it again on the open
descriptor.
The step waits up to 30 s for another holder, then exits 1 with one line.
hs-uploader's own installer does not take the lock yet; the v3.79
installer must use this path.  The read-only step takes no lock.

**The sink switches** (tasks/spec-sink-2b-part2.md §6, §7).  Before it
renders, under the lock, the write step does one of these:

- `gate` offered, no carry-over marker at
  `/var/lib/hs-uploader/carry-over-v1.done`: the carry-over
  (`sink_carryover.py`).  It writes `site.toml` from `[uploads]`
  (`discard` gives `off`, the legacy hold gives `fill`, anything else
  `upload`), with `[uploads]`'s own reason first, so a new station's
  `new station: …` survives, and a switch file for each governed client that lacks one,
  each through `hs-uploader sink set`.  A client goes to `fill` when an
  instance env sets `PSK_USE_HS_UPLOADER=0` or
  `METEOR_SCATTER_USE_HS_UPLOADER=0`, when an instance env file cannot be
  read or its name fails the reporter-id check, or when the client is not
  enabled; otherwise to `upload`.  It reads the states back through
  `hs-uploader sink status --json`, runs the lever step for each client
  below `upload`, restarts stale writers when any state reads `off`, and
  writes the marker last, through `hs-uploader sink mark-carried`.  The
  marker holds the UTC time and one note line, `carry-over wrote: site
  psk-recorder …`.  A failure before the marker leaves no marker; the step
  then renders v3.78's form and exits 1, and the next run tries again.
- `gate` offered, the marker standing: repair the `[uploads]` mirror from
  `site.toml` when the two differ (`sink_mirror.py`; a broken `site.toml`
  leaves both alone and exits 1), give a client installed since the
  carry-over its switch file by the same table, and run the lever step.
  With nothing to change, the lever step changes nothing and restarts
  nothing.  When `sink status` cannot be read, the step still renders and
  lands, skips the lever step with one line naming the cause, and exits 1.
- A feature list without `gate` while the marker stands: move the marker
  aside to `carry-over-v1.done.rolled-back-<UTC>` (`.1`, `.2` … when that
  name is taken), so the carry-over runs again once the gate returns.  A
  failed probe moves nothing.

With `gate` and the marker, the step renders every client pipeline with its
`client`, no `discard`, no hold and no policy banner, and writes the
heartbeat entry, with `governed = false`, to
`/etc/hs-uploader/pipelines.d/sigmond.toml` instead of the legacy manifest.
The legacy manifest's bytes then no longer depend on the site switch.
Without `gate`, or before a carry-over finishes, it renders exactly as
v3.78 did, by `[uploads]`, and removes `sigmond.toml`.  The read-only step
prints what `--write` would carry over, repair or move, and writes nothing.

**The lever step** (`sink_levers.py`, spec §8).  hs-uploader's gate governs
only the daemon.  So for each client whose effective state reads below
`upload`, sigmond turns off the senders that run outside it:
`PSK_DELIVERY_PIPELINES=server-raw`, `METEOR_SCATTER_DELIVERY_MODE=off`,
`WSPR_USE_HS_UPLOADER=0` in each instance's env file, `systemctl disable
--now wspr-uploader`, and `[uploader] enabled = false` in the mag-recorder
config.  A wspr-uploader whose unit file is a link (wspr-recorder's
installer runs `systemctl link`) never gets `disable`, which would remove
the unit's own link.  When it runs with no enablement, it gets `stop`
alone.  When someone has also enabled it, sigmond records its `.wants` and
`.requires` links in `/etc/sigmond/sink-levers/wspr-uploader.links`,
removes only those, reloads systemd and stops it.  Each env write goes through `safe_write.rewrite_owned`: an
existing file keeps its owner and mode, and a symlink, a hard link or a
file owned by anyone but root or the directory's owner refuses.  Each
touched recorder instance then restarts alone, by `systemctl restart
<unit>`, one at a time.  The step never restarts radiod; it reads radiod's
start time before the first restart and after the last, and fails loudly
if it moved.  It raises one lever back, and only one.  When hs-uploader
reads wspr-recorder at `upload` again, the step restores the recorded
enablement links, provided the unit link still names the recorded target.
It starts nothing.  Every other lever stays lowered.

**In-process senders.**  psk-recorder and meteor-scatter can each post to
pskreporter from their own process.  The renderer reads each instance's
environment and leaves out the daemon pipeline of a producer whose instances
all send in-process, so the same spots do not post twice.  A doubtful
reading keeps the pipeline.  On the shared form the pipeline stays and the
renderer warns.  The harmonize rule `uploader-inprocess-drift` flags a
manifest that no longer matches the environments.

**The write sequence.**  After the sink switch steps above:

1. Probe the store version, as above, and render.
2. Render to the side file, `pipelines.toml.new`.
3. Run `hs-uploader migrate --check`.  Exit 3 means a migrate would write;
   exit 0 means it would not.
4. If the manifest is unchanged and nothing is pending, return.  The daemon
   keeps running, unless `--enable` starts it, or restarts a daemon that
   started before the manifest file's last write.
5. Stop `hs-uploader.service`.
6. Run `hs-uploader migrate`, with `--sink` and `--keep-retry-for` set so a
   queued `psk-pskreporter` retry is deleted or moved.
7. Move the side file into place, keeping the old file as `.bak`.  In the
   gate form `pipelines.d/sigmond.toml` lands first; in v3.78's form it is
   removed after the legacy manifest lands.  When only `sigmond.toml`
   changes, step 4 lands or removes it with no daemon restart: the daemon
   rebuilds from `pipelines.d/` within one pump.
8. Run `reset-failed`, then start the daemon if it ran before the step or
   if `--enable` asked for it.

A failed migrate drops the side file, returns the daemon to the state it
had, and exits 1, so the old manifest and the old keys still match.  Any
other error after step 5 also restores the daemon before the command exits
non-zero.  The daemon stops for the migrate because the
migrate takes the store's write lock and must not meet a running pump.

A rollback (`smd admin manifest restore`) re-renders with the restored
smd.  An older smd knows nothing of the split, so it renders
`psk-pskreporter` again.  The store keeps its version, and the old send
record, frozen at the migrate, resumes the shared pipeline.

When the restored sigmond predates the sink switches (its tree carries no
`lib/sigmond/sink_carryover.py`), the restore first moves the carry-over
marker aside to `carry-over-v1.done.rolled-back-<UTC>` (`.1`, `.2` … when
the name is taken) and removes `pipelines.d/sigmond.toml`.  It does both
even when its re-verification fails.  Switch files stay.  The next
manifest step under a part 2 sigmond finds no marker and carries over
again from `[uploads]`, keeping every client file.  Roll back only with
`smd admin manifest restore`: after a hand `git checkout`, a part 2 daemon
keeps enforcing a `site.toml` that the old `smd upload` no longer writes.

A restore leaves each checkout it moved detached at the restored commit.
Any `.pin` it had stays, still naming the newer commit.  `smd update` pulls
nothing into such a checkout, and `smd update --unpin` does not release
it.  To move the station forward, run `smd align` (a dry run), then `smd
align --apply`, which moves each checkout to the latest blessed release,
pinned, and re-runs bring-up.

## The site sink switch

`smd sink off|fill|upload`, `smd upload on|hold|off|discard` and `smd config
uploads enable|disable` all reach `cmd_config_uploads`.  With an
hs-uploader that offers `gate`, `_site_change` runs, in order:

1. the lever step, for every client that reads below `upload` after the
   change;
2. before a change to `off`, the stale-writer restarts: each psk-recorder,
   meteor-scatter and wspr-recorder instance that started before the
   installed hs-uploader `sink/writer.py` last changed, skipping each
   instance step 1 restarted;
3. `hs-uploader sink set site <state> --reason … --by "smd sink (<user>)"`,
   which writes `/etc/hs-uploader/sinks/site.toml`;
4. `[uploads]` in site-profile.toml and coordination.toml, each line
   reading `<file>: [uploads] now mirrors the site sink switch (<state>)`;
5. the manifest step, which alone takes the install lock.

While no carry-over marker exists, steps 1 and 2 wait: the carry-over in
step 5 lowers and restarts once.  The command names a failure in steps
1-4 and still runs the later steps, so an `off` order always lands; it then
exits 1.  One exception: while no marker exists, a failed write of
coordination.toml's `[uploads]` stops the command before step 5, since the
carry-over would copy the old mode back into `site.toml`.  Leaving `off` closes the doors first (`_close_doors`).  The
command closes with one line per state:

- `site sink: off — nothing ships and nothing new is stored for upload;
  data stored earlier waits and ships when the switch rises`;
- ``site sink: fill — stores and sends no data; `smd sink upload` ships
  what it stored, oldest first``, then `until hs-uploader cleans the sink
  itself, sigmond's trim keeps FT8 spots for one hour and WSPR spots for
  24 hours, sent or not`;
- `site sink: upload — store and send`.  A raise also lists each client it
  lifts, with its callsign, reporter ids, grid and PSWS ids, and each
  client that stays below `upload` with the switch that keeps it there.

`smd sink` sets the site switch only.  No smd verb raises one client in
this release; a client the carry-over left at `fill` rises by `hs-uploader
sink set <client> upload --reason … --by …`, run as root.

`smd sink status` and `smd upload status` read `hs-uploader sink status
--json` once the marker stands, and `[uploads]` before.  They print the
site line, `site sink: upload (store and send); set <time> by <who> —
<reason>`, then each client's file and effective state and which switch
sets it, the held pipelines and the refused files.  Faults follow, each
marked ✗ and each making the command exit 1: `UNMANAGED: …` for an
in-process sender still armed below `upload`, `cannot check: …` for one
sigmond could not read, and `mirror mismatch: …` when `[uploads]`
disagrees with `site.toml`.  `stale writer: …` names a recorder still on
the old writer code; it prints as a warning (⚠) and leaves the exit code
alone, since the next change to `off` restarts that recorder first.  When
sigmond cannot read `sink status`, the command says so, shows `[uploads]`
and exits 1.  `smd update` never reaches the manifest step; while no
marker exists and the installed hs-uploader offers `gate`, it prints ``the sink switches take
effect at the next `smd align` ``.

## Heartbeat and the board

Every station assembles a heartbeat envelope every 5 minutes
(`smd admin heartbeat emit`, `lib/sigmond/heartbeat.py`), spools it to
`/var/lib/sigmond/heartbeat`, and hs-uploader's SFTP transport drains it to a
chrooted drop on the central server, which renders one board. The roster of
expected stations is declared in the fleet inventory, never inferred from
arrivals. Three rules are load-bearing and test-enforced, and are the reason
this exists at all — see [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §10
and [`../PSWS-HEARTBEAT-SPEC.md`](../PSWS-HEARTBEAT-SPEC.md):

- The envelope carries **only counters that move when the bad thing happens**
  ([`../PRODUCER-THREAT-MODEL.md`](../PRODUCER-THREAT-MODEL.md) ★). A test
  walks every serialized envelope and refuses the known liars by name
  (`completeness_pct`, `pending_uploads`, `seconds_detected`, …).
- Every block carries a four-state verdict — VALID / INVALID / INCONCLUSIVE /
  INDETERMINATE. A new field must be able to say "I don't know"; a fabricated
  zero is worse than a null.
- **Availability comes from arrival time at the server**, never from what a
  station says about itself. Silence past three intervals goes red regardless
  of how green the last self-report was.

The board is the entire administrative interface. Nothing pages, mails, or
pushes: no alarm ships without a named owner and a written response action.
Do not add notifications as a side effect of other work.

## Where to read next

- [`../CLIENT-CONTRACT.md`](../CLIENT-CONTRACT.md) ★ — the sigmond↔component
  interface every client implements. Start here before touching the seam.
- [`../MULTI-INSTANCE-ARCHITECTURE.md`](../MULTI-INSTANCE-ARCHITECTURE.md) ★ —
  the per-reporter instance shape behind `smd admin instance`.
- [`../RADIOD-IDENTIFICATION.md`](../RADIOD-IDENTIFICATION.md) ★ — canonical
  radiod multicast naming, which every client resolves against.
- [`../PRODUCER-THREAT-MODEL.md`](../PRODUCER-THREAT-MODEL.md) ★ and
  [`../PACKET-LOSS-DIAGNOSTICS.md`](../PACKET-LOSS-DIAGNOSTICS.md) ★ — what
  threatens data production and how to track an RTP gap to its layer.
- [`../networking.md`](../networking.md) ★ — the IGMP-snooping silent failure
  that kills multi-host multicast.
- [`../CLI-V2-SPEC.md`](../CLI-V2-SPEC.md) — why the verb surface has the shape
  the table above shows.
- [`../operator/day-2.md`](../operator/day-2.md) — the same update mechanics
  from the operator's chair.
- [`../scientist/becoming-a-client.md`](../scientist/becoming-a-client.md) —
  the client author's view of the sink, the contract, and shipping upstream.
- [`docs-conventions.md`](docs-conventions.md) ★ — how these pages are kept
  true, and the rule that a contributor page links the code path rather than
  restating it.
- [`dev-setup.md`](dev-setup.md) ★ — getting a working checkout, `.venv`, and
  the test suite.
- [`client-authoring.md`](client-authoring.md) — writing a new conformant
  client; starts with [`../ADD-A-CLIENT.md`](../ADD-A-CLIENT.md).
