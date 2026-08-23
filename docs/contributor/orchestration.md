# How sigmond works — orchestration in one page

> **Audience:** contributor
> **Status:** current
> **Verified against:** sigmond 978c80a on 2026-08-23 — code (bin/smd, lib/sigmond, CLAUDE.md)
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
`capture_prep.py`, the sink default in `hamsci_sink/writer.py`, and
`upload-wake.sock` in ka9q-python's wspr_recorder.

| Path | What it is |
|---|---|
| `/opt/git/sigmond/<name>/` | every component's checkout, sigmond's own included (`/opt/git/sigmond/sigmond`) |
| `/usr/local/bin/smd` | symlink to the checkout's `bin/smd` — so an edit in the repo is live, no reinstall |
| `/etc/sigmond/topology.toml` | what is enabled on this host |
| `/etc/sigmond/coordination.toml` + `coordination.env` | station identity, radiod registration, per-client env bag |
| `/etc/sigmond/site-profile.toml` | per-site profile written by `personalize` |
| `/etc/sigmond/catalog.toml` | operator overrides only — the sparse top layer of the catalog |
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
| `apply` | after a config edit | reconcile running units with current config: re-render radiod fragments + firmware, regenerate the uploader manifest, restart what changed | `cmd_apply` → `lifecycle.py`, `commands/radiod_fragments.py`, `commands/radiod_firmware.py`, `commands/uploader.py`, `coordination.py` | yes (root + lock) |
| `bringup` | first install | guided station bring-up from a catalog profile; `--with-optional` adds the discretionary set | `cmd_bringup` → `bringup.py` (`build_plan`), `catalog.py`, `coordination.py`, `site_profile.py`, `psws.py`; the plan's steps shell out to sub-`smd` calls, which is how `topology.py` is reached (`smd enable <comp>`) | yes (root) |
| `component` | installer | per-component catalog + status: `list`, `install`, `update`, `add`, `remove`, `enable`, `disable` | dispatch branch in `main()` → `cmd_list`, `cmd_install`, `cmd_add`, `cmd_remove`, `cmd_enable`, `cmd_disable`; `catalog.py`, `installer.py` | `install`/`update`/`add`/`remove` do (root) |
| `config` | installer | show / migrate / init / render / edit station and per-client config; also `catalog-prune`, `backup`, `restore`, `uploads`, `register-radiod` | dispatch branch → `commands/config.py`, `commands/client_config.py`, `commands/radiod_config.py`, `catalog_prune.py`, `psws.py` | writing subverbs do (root) |
| `disable` | operator | take a component offline reversibly — stop its units, clear the topology flag | `cmd_disable` → `topology.py`, `catalog.py` | yes (root) |
| `doctor` | station-inward update | checkout health: ownership, venv skew, dirty trees; `--fix` repairs ownership only | `cmd_doctor` → `doctor.py` | `--fix` does (root) |
| `enable` | scripting | set `enabled = true` in topology — rarely typed, install and start do it for you | `cmd_enable` → `topology.py`, `catalog.py` | yes (root) |
| `fleet` | fleet-outward | read-only fan-out over the inventory: `status`, `doctor`, `roster`, `pubkeys`. `--apply` is structurally impossible here, by design | `cmd_fleet` → `fleet.py` | no — the wall between the two chairs |
| `install` | installer | install + configure + enable one component, or walk catalog × topology for the whole suite | `cmd_install` → `installer.py`, `catalog.py`, `discover.py`, `preflight.py`, `topology.py` | yes (root + lock) |
| `notify` | operator | fault-notification outbox: `test`, `status`, `list`, spooling to `/var/lib/sigmond/notify/` | `cmd_notify` — self-contained in `bin/smd` | `test` writes a spool record |
| `psws` | registration | station-level PSWS enrolment: `status`, `enroll`, `verify`, `motd` | dispatch branch → `psws.py` | `enroll`/`verify` do (root) |
| `reload` | operator | reload config in place via the control socket or SIGHUP, falling back to restart | `cmd_reload` → `control_socket.py`, `lifecycle.py` | yes (root + lock) |
| `restart` | operator | restart managed units, `reset-failed` first | `cmd_restart` → `lifecycle.py`, `component_state.py`, `catalog.py` | yes (root + lock) |
| `status` | daily | service health snapshot plus each client's own inventory: version, channels, frequencies, timing judge, PSWS state | `cmd_status` → `catalog.py`, `cpu.py`, `psws.py`, `timing_judge.py`, `ui.py` | no |
| `start` | operator | start managed units in `order_units()` order; also enables an installed-but-disabled named component | `cmd_start` → `lifecycle.py`, `component_state.py`, `harmonize.py` | yes (root + lock) |
| `stop` | operator | stop managed units, reverse start order | `cmd_stop` → `lifecycle.py` | yes (root + lock) |
| `timing` | daily | timing authority in one line — tier, offset, σ, judge age, GPSDO state | `cmd_timing_show` → `commands/timing_show.py` (reads `/run/hf-timestd/authority.json`) | no |
| `tui` | operator | launch the three-panel Textual configurator; re-execs into the production venv if Textual is missing | `cmd_tui` → `tui/` (lazy import) | via its screens |
| `update` | station-inward | the pull-orientation updater: plan first, `--apply` executes; refuses rather than discarding a colliding local change | `cmd_update` → `update.py`, `provenance.py`, `catalog.py`, `doctor.py`, `wisdom.py` | `--apply` does (root) |
| `version` | station-inward | image lineage plus each component's commit read live from its checkout | `cmd_version` → `provenance.py`, `doctor.py` | no |
| `watch` | daily | live tail for one target: `wspr`, `psk`, `hfdl`, `codar`, `superdarn`, `meteor`, `hf-tec`, `mag`, `ka9q`, `radiod`, `gpsdo`, `uploads`, `verifier` | dispatch branch → `cmd_<target>_watch` in `bin/smd`; `ka9q` alone is `commands/ka9q_watch.py` | no |
| `admin capture-prep` | image build | strip per-site identity, secrets, and data before golden-image capture — the inverse of `personalize`; plan-first | `cmd_capture_prep` → `capture_prep.py`, `site_profile.py`, `instance.py`, `psws.py`, `coordination.py` | `--yes` does (root) |
| `admin completion` | setup | emit the bash tab-completion script | `cmd_completion` — self-contained | no |
| `admin diag` | debugging | cross-component diagnostics; sub-targets `cpu-affinity`, `cpu-freq`, `net`, `drop-in` | `cmd_diag`, `cmd_diag_cpu_affinity`, `cmd_diag_cpu_freq`, `cmd_diag_net`, `cmd_diag_drop_in` → `cpu.py`, `net_diag.py`, `diag_drop_in.py` | read-only unless `--apply` (root) |
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
| `admin sources` | multi-SDR hosts | which SDR source each client consumes from: `list`, `add`, `remove`, `apply` | `cmd_sources_*` → `sources.py`, `instance.py` | `add`/`remove`/`apply` (root) |
| `admin storage` | maintenance | the local sink backend: `migrate-to-sqlite`, `trim`, `tune-timestd` | `cmd_storage_migrate_to_sqlite`, `cmd_storage_trim`, `cmd_timestd_tune_storage` → `storage_migrate.py`, `storage_trim.py` | `--yes`/`--apply` (root) |
| `admin timing` | debugging | timing-chain reconciler across GPSDO → gpsd → chrony → hf-timestd | `cmd_timing` → `commands/timing.py` | plan-first; applying needs root |
| `admin uninstall` | teardown | tear down the whole sigmond install; plan-first, `--yes` to execute | `cmd_uninstall` → `uninstall.py` | `--yes` does (root) |
| `admin uploader` | uploads | generate `/etc/hs-uploader/pipelines.toml` from each client's `deploy.toml` (`manifest`) | `cmd_uploader_manifest` → `commands/uploader.py`, `uploader_manifest.py` | `--write`/`--enable` (root) |
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
only ask questions — its command vocabulary is a test-enforced whitelist, and
B4 is the declared canary.

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
