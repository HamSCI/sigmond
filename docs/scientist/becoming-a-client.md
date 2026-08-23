# Becoming a client — Tier 1

> **Audience:** scientist
> **Status:** current
> **Verified against:** sigmond e1c4452 on 2026-08-23 — code (bin/smd, writer.py, ADD-A-CLIENT, CLIENT-CONTRACT, Costas-array, meteor-scatter)
> **Canonical for:** graduating a capture to a sigmond client (Tier 1)

[Tier 0](capture-quickstart.md) ends with bytes on a disk and a person who
knows where they are. **Tier 1** is what you build when that is no longer
enough: a *client* — a repo the station installs, a systemd unit the station
starts, and a JSON self-description sigmond reads so the thing appears in
`smd status`, gets pulled by fleet updates, and can hand its rows to the
station's uploader.

This page is the bridge. It does not restate the contract: the rules live in
[CLIENT-CONTRACT.md](../CLIENT-CONTRACT.md) and the checklist in
[ADD-A-CLIENT.md](../ADD-A-CLIENT.md), both written for a contributor who
already knows the system. What you get here is the scientist's route through
them — what to build first, which decisions are real decisions, and the place
where the station will not meet you yet.

The scaffold is [`skeleton/`](skeleton/README.md) — a copyable, MIT-licensed
minimal client you can run on your laptop before you go near a station.

---

## When to graduate — and when not to

Tier 0 is not a lesser thing. The
[2026-08-12 eclipse listener](costas-14110-worked-example.md) captured 22 hours
of I/Q, produced a result that settled an open question, and never became a
client: it ran under
`systemd-run` per job, shipped no unit and no permanent channel, and that was
[the right call for a one-off](costas-14110-worked-example.md#a-scheduler-systemd-run-and-deliberately-no-unit).
A client is *more* machinery, and machinery you do not need is machinery that
can be wrong.

Graduate when one of these becomes true:

| Trigger | What Tier 1 gives you |
|---|---|
| **It recurs, or it runs indefinitely.** Not one eclipse — every eclipse, or every day | systemd restarts it after a crash or a reboot; sigmond starts it *after* radiod and stops it before ([§5.4](../CLIENT-CONTRACT.md#54-start--stop-ordering-v05)); an operator can `smd start` / `smd stop` it by name |
| **Somebody else has to be able to see it.** The person watching the station is not you | your `inventory --json` and `validate --json` become the `smd status` block and the `smd admin diag` view — a health surface an operator can read without knowing what your client does ([§3](../CLIENT-CONTRACT.md#3-self-describe-cli)) |
| **You want the fleet lifecycle.** Updates, provenance, disk budgeting | your repo is pulled by the station's update path, its checkout is checked by `smd doctor`, and its version rides the station heartbeat (`smd admin heartbeat` builds its component block from every git checkout under `/opt/git/sigmond`, so a client appears there as soon as it is cloned — `lib/sigmond/heartbeat.py`, `doctor.component_checkouts`) |
| **Your output should leave the station.** Rows to an aggregator, files to PSWS | the shared sink and `hs-uploader` — see [Writing rows to the sink](#writing-rows-to-the-sink) and [Shipping it upstream](#shipping-it-upstream), and read the second of those before you count on it |

Stay at Tier 0 when the experiment is one-shot, when the analysis is still
changing daily, or when you cannot yet say what "healthy" means for your
client — because `validate` is exactly the question "what does unhealthy look
like?", and a client that cannot answer it adds a green tick to the operator's
screen that means nothing.

## The seven things you must ship

This is [ADD-A-CLIENT.md's TL;DR](../ADD-A-CLIENT.md#tldr--the-seven-things-you-must-ship)
with a scientist's gloss on each. That page is the checklist; keep it open.

| # | Thing | What it actually means |
|---|---|---|
| 1 | **A repo** the station can clone to `/opt/git/sigmond/<your-client>/` | any git remote the station can reach. It is your code, under your license |
| 2 | **[`deploy.toml`](../ADD-A-CLIENT.md#2-deploytoml--the-sigmond-manifest)** | the manifest: name, version, how to build, what to link where, which units you own. Sigmond discovers a client from this file alone — no sigmond-side edit needed ([§5](../CLIENT-CONTRACT.md#5-deploy-manifest-repodeploytoml)) |
| 3 | **A templated systemd unit** `<your-client>@.service` | templated is mandatory: `%i` is the reporter identity, so one host can run one process per identity ([§4](../CLIENT-CONTRACT.md#4-systemd-units), [MULTI-INSTANCE-ARCHITECTURE.md](../MULTI-INSTANCE-ARCHITECTURE.md)) |
| 4 | **Contract subcommands** `version` / `inventory --json` / `validate --json` / `daemon` | the entire interface. Sigmond shells them out and never imports your code. `inventory` must exit 0 *always*; `validate` exits nonzero when you are unhealthy; both print JSON on stdout and nothing else ([§3](../CLIENT-CONTRACT.md#3-self-describe-cli), [ADD-A-CLIENT §4](../ADD-A-CLIENT.md#4-contract-subcommands-client-contractmd-3)). `quality --json` is optional ([§17](../CLIENT-CONTRACT.md#17-output-sinks-v06)) |
| 5 | **A config template and a render step** in `deploy.toml` | so the station gets a working config at install time, and re-running the installer never overwrites an operator's edits (`if_absent = true`) |
| 6 | **`[client_features]` blocks**, one per TUI screen you want | pure registration ([ADD-A-CLIENT §5](../ADD-A-CLIENT.md#5-client_features--tui-registration-drop-in)). Omit a block and you are simply absent from that screen; nothing breaks |
| 7 | **A catalog entry** in sigmond, *when ready* | only needed so hosts that have **not** cloned your repo can find it. A cloned repo is auto-discovered ([ADD-A-CLIENT §6](../ADD-A-CLIENT.md#6-catalog-entry-often-optional)) |

Alongside (4), the wizard surface: `config init` / `config edit` (interactive),
`config show --json [--defaults]` and `config apply --json -` (machine-readable)
— what `smd config init|edit <client>` and the TUI wizard drive
([§14](../CLIENT-CONTRACT.md#14-configuration-interview-v05)). Write these when
you have a config worth editing; sigmond falls back to `$EDITOR` until then
([§14.4](../CLIENT-CONTRACT.md#144-fallback-when-no-contractconfig)).

## Start from the skeleton

`docs/scientist/skeleton/` is the smallest thing that satisfies (2), (3), (4)
and the wizard surface. It is modelled on the eclipse listener's real
manifest and contract module
([mijahauan/Costas-array](https://github.com/mijahauan/Costas-array)) and on
[meteor-scatter](https://github.com/HamSCI/meteor-scatter/blob/main/systemd/meteor-scatter@.service)'s
unit, simplified to what a first client needs.

```
skeleton/
├── deploy.toml              the manifest (§5)
├── my-recorder@.service     the templated unit (§4)
├── my_recorder/cli.py       version / inventory / validate / daemon / config show (§3, §14)
└── config/help.toml         the wizard sidecar (ADD-A-CLIENT §8)
```

Copy it, rename it, and run the verbs by hand — on your own laptop, with no
station and no sigmond, because that is all the contract needs:

```bash
cp -r docs/scientist/skeleton ~/my-recorder
python3 ~/my-recorder/my_recorder/cli.py inventory --json | python3 -m json.tool
python3 ~/my-recorder/my_recorder/cli.py validate  --json ; echo "exit=$?"
```

[`skeleton/README.md`](skeleton/README.md) walks the rename, shows the real
output of every verb, and lists what is stubbed. Two habits from it are worth
carrying into whatever you write instead:

- **Nothing but JSON on stdout.** Configure logging to stderr *before*
  argparse runs. One stray byte — a banner, a "logging configured" line —
  makes the payload unparseable for sigmond and your client reads as *not
  installed*. This has bitten shipped clients ([§3](../CLIENT-CONTRACT.md#3-self-describe-cli)).
- **`inventory` degrades, it does not fail.** Client configs are commonly
  mode 0640 and owned by the service user, so an operator running
  `my-recorder inventory --json` cannot read yours. Catch that and print a
  contract-shaped payload with the failure in `issues`, still exiting 0.
- **Spell a hard problem `"fail"`.** [ADD-A-CLIENT §4](../ADD-A-CLIENT.md#4-contract-subcommands-client-contractmd-3)
  requires exactly that word for a degraded inventory, and it is the only one
  that carries: `bin/smd` prints an issue as an error when the severity is
  `fail` and as a warning for every other string, so `"error"` — which reads
  like the more serious word — puts your worst failure on the operator's
  screen looking like a nag. Nothing states the full vocabulary
  ([ledger row 42](../contributor/docs-gap-ledger.md)); use `fail` and `warn`.

## How it gets onto a station

Two commands, in this order, on the station's decoder VM (the `[VM]` half of
a two-machines-in-one-box appliance — see the operator
[glossary](../operator/glossary.md)):

```bash
smd component add https://github.com/<org>/my-recorder.git
smd install my-recorder
```

`smd component add` derives the name from the URL, clones to
`/opt/git/sigmond/<name>/`, fast-forward-pulls if it is already there, and
**refuses the repo if it has no `deploy.toml`** — telling you where it put the
directory so you can remove it. It does not touch topology
(`cmd_add` in `bin/smd`). `smd install <name>` then runs your `[build]` steps,
applies your `[[install.steps]]`, and enables the component in topology, so
`smd start <name>` runs it (`--no-enable` opts out). Both are mutating verbs;
`smd` re-executes itself under `sudo` when it needs to, so you type `smd`, never
`sudo smd`.

Then the ordinary path: `smd config init my-recorder`, `smd start my-recorder`
— download → install → configure → start, with no separate `enable` step,
because `smd install` already did it. ([install-quickstart.md](../install-quickstart.md)
walks the same ground for an installing operator, but its headless section
still tells you to hand-edit `topology.toml` to set `enabled = true`; that
predates install-implies-enable — [ledger row 45](../contributor/docs-gap-ledger.md).)
`smd admin diag drop-in my-recorder` lints every surface at once and is the
fastest way to find what you got wrong
([ADD-A-CLIENT §7](../ADD-A-CLIENT.md#7-verify-the-drop-in)).

**On an appliance station you do not run these — the fleet admin does.**
Anything that installs, pulls, edits or repins goes through them, by the
station's own rules
([do-not-touch.md](../operator/do-not-touch.md#the-one-rule-behind-all-of-them)).
Develop against your own host or a testbed, hand over a repo URL and a tested
`deploy.toml`, and expect to be asked what your client costs in channels, CPU
and disk — which is what `inventory --json` is for
([station-capabilities.md](station-capabilities.md#how-many-channels-you-may-add--the-load-budget)).

## Channels: permanent fragment, or dynamic with a lifetime

This is the first real design decision, and it is not about code.

A **`[[radiod.fragment]]`** ([§15](../CLIENT-CONTRACT.md#15-radiod-channel-contributions-v05))
is a declaration in your `deploy.toml` that names a template file; sigmond
renders it into `radiod@<id>.conf.d/<NN>-<your-client>.conf` and applies it.
The channel then exists because the *station* is configured to carry it —
whether your client is running or not, across restarts and reboots, until
someone removes the fragment.

A **dynamic channel** is one you create at runtime through
`ka9q-python`'s `ensure_channel()` with an explicit `lifetime`, and radiod
reclaims it when the lifetime expires. This is the Tier-0 pattern
([capture-quickstart.md](capture-quickstart.md#the-script)), and it stays
correct at Tier 1.

| Choose | When |
|---|---|
| **Fragment** | the station should carry this channel as part of what it *is* — a standing observation other clients or archives depend on, that must survive your client being stopped |
| **Dynamic + `lifetime`** | the channel exists for a window: an event, a scheduled job, a duty cycle. Also the safer default — a `lifetime` is the promise that a crashed client does not leave a channel running forever |

The eclipse listener declared no fragment and said why in the file: its
channel exists for the duration of a capture window, so "a permanent fragment
would be wrong — it would hold a channel open forever for a one-off capture"
(`Costas-array/deploy.toml`). The skeleton carries the same comment. Either
way, `lifetime` is mandatory on every dynamic channel
([station-capabilities.md](station-capabilities.md#frequency-and-bandwidth--what-radiod-will-hand-you)),
and every added channel is load on radiod's 20 ms block budget — settle the
count with the operator before you ship
([§the load budget](station-capabilities.md#how-many-channels-you-may-add--the-load-budget)).

## Writing rows to the sink

If your client produces **rows** — spots, detections, per-cycle measurements —
write them to the station's shared local sink rather than inventing a file
format. The sink is a store-and-forward queue that `hs-uploader` drains
([§17](../CLIENT-CONTRACT.md#17-output-sinks-v06)); the writer is
[`sigmond.hamsci_sink.Writer`](../../lib/sigmond/hamsci_sink/writer.py).

```python
from sigmond.hamsci_sink import Writer

# mode -> the sink's database name; table -> the table name.  Rows land in
# sink.db's pending_uploads queue tagged with that (target_db, target_table).
writer = Writer.from_env("events", mode="my_recorder", schema_version=1)

writer.insert([                      # a LIST of dicts, never a bare dict
    {"time": "2026-08-23T17:47:06Z", # ISO8601 UTC — the queue carries an
                                     # expression index on `$.time`
     "reporter_id": "MY-RECORDER-1", # §19.3 — every row carries it
     "frequency": 14_110_000,
     "snr_db": 14.1},
])
writer.flush()                       # or let it auto-flush; close() on shutdown
```

What the code above is really doing, from
[`writer.py`](../../lib/sigmond/hamsci_sink/writer.py)'s own docstring:

- **One queue table for everybody.** Rows go into `pending_uploads` in
  `/var/lib/sigmond/sink.db` as `(target_db, target_table, schema_version,
  payload_json, queued_at)`. Your row is JSON, so **the uploader owns schema
  translation, not you** — that is what keeps a producer decoupled from the
  upstream's column shape.
- **`mode` is the per-mode key** (`wspr`, `psk`, `timestd`, …) and `database`
  defaults to it, letting an operator redirect a mode per host via
  `SIGMOND_SQLITE_DB_<MODE>`; pass `database=` to bypass the alias. Keep the
  mode name alphanumeric-plus-underscore — it is upper-cased straight into
  that env var name, so a hyphen produces a variable no shell can set. Name
  your table for what it holds (`events`, `spots`, `noise`) and keep it
  stable: with the database it is the uploader's cursor key.
- **`schema_version` is a promise about the payload's shape.** Start at 1 and
  bump it when a field changes meaning; a pipeline declares
  `accepted_schema_versions`, so an unversioned change is how you silently
  stop being shipped.
- **It is a no-op when it cannot write.** With `SIGMOND_SQLITE_PATH` unset and
  `/var/lib/sigmond` not writable, the writer becomes a silent no-op by design
  — a client running standalone, outside a sigmond install, stays safe instead
  of erroring on every flush. The trap: a hardened systemd unit does exactly
  that to itself. `ProtectSystem=strict` without `/var/lib/sigmond` in
  `ReadWritePaths` makes the directory read-only, the Writer no-ops, and every
  row is dropped before it is queued, with a healthy-looking unit. The comment
  saying so is in
  [meteor-scatter's unit](https://github.com/HamSCI/meteor-scatter/blob/main/systemd/meteor-scatter@.service),
  and the skeleton's unit carries the same warning.
- **Not threadsafe.** One writer per producer thread, or serialize the calls.
- Declare it: report the sink in `inventory --json`'s `data_sinks`
  ([§17.3](../CLIENT-CONTRACT.md#173-self-disclosure-data_sinks-in-inventory)) so
  sigmond can budget disk and surface backpressure.

If your product is **files** — I/Q, SigMF, HDF5 — the sink is not for you.
Write to `/var/lib/<your-client>/` and declare it in `disk_writes`; sigmond
promotes that into the same `data_sinks` view
([§17.4](../CLIENT-CONTRACT.md#174-backwards-compatibility-with-disk_writes-v05)).

## Shipping it upstream

Getting rows *out of* the station is a separate declaration, in the same
`deploy.toml`. You describe a source and a transport; sigmond substitutes the
station's identity and concatenates every enabled client's blocks into
`/etc/hs-uploader/pipelines.toml`
([PER-SITE-SETUP.md §7](https://github.com/HamSCI/hs-uploader/blob/main/docs/PER-SITE-SETUP.md)):

```toml
[[hs_uploader.pipeline]]
name = "my-recorder-somewhere"
batch_limit = 500
[hs_uploader.pipeline.source]
type = "sqlite"
database = "my_recorder"
table = "events"
accepted_schema_versions = [1]
[hs_uploader.pipeline.transport]
type = "pskreporter"          # must be one of the transports that exist
```

Placeholders sigmond fills in from the station's own config — `{call}`,
`{call_pathsafe}`, `{grid}`, `{radiod_status}`, `{sink_path}`,
`{ssh_key_file}`, `{station_id}`, `{instrument_id}` — so you never hard-code a
callsign or a key path. A pipeline whose required identity is missing is
**skipped with a warning**, not failed. Render and check with
`smd admin uploader manifest --check` (read-only diff) / `--write` / `--enable`.

**What exists today** (`hs_uploader/pipeline_factory.py`, 2026-08-23):

| Source `type` | Reads |
|---|---|
| `sqlite` | rows from `sink.db` |
| `filetree` | files under a directory you write |
| `wspr_cycle` | WSPR-cycle-aligned bundles from `sink.db` |

| Transport `type` | Ships to |
|---|---|
| `wsprnet` | wsprnet.org |
| `pskreporter` | pskreporter.info |
| `wsprdaemon_tar` | wsprdaemon.org (SFTP/FTP tar) |
| `psws_dataset` | PSWS, by SFTP, **as a file tree** |
| `heartbeat_sftp` | the fleet heartbeat endpoint |

**The gap you will hit: there is no PSWS path for a new client's product.**
`psws_dataset` is the only PSWS transport and it uploads *files* discovered by
a `filetree` source — there is no `sqlite` → PSWS pairing, so rows in the sink
cannot be shipped to PSWS at all.

And the identity placeholders will not save you either. `{station_id}` /
`{instrument_id}` are added to the substitution map **only** for a client
sigmond already knows as a PSWS recorder — today `hf-timestd` and
`mag-recorder`, hard-coded in `psws.RECORDERS` (`lib/sigmond/psws.py`;
`uploader_manifest.resolve_tokens`). For anybody else the keys are not in the
map at all, so the substitution pass never sees them, never records them as
missing, and **writes the literal string `{instrument_id}` straight into
`/etc/hs-uploader/pipelines.toml`** — which is worse than being skipped,
because the pipeline then runs against a nonsense destination. (The
skip-with-a-warning behaviour that PER-SITE-SETUP describes applies to a
*declared* PSWS recorder whose id is merely unset.)

**Today you ship your own files**: write them, arrange the PSWS-side
registration yourself, and treat upstream delivery as your problem, not the
station's. This is
[docs-gap ledger row 3a](../contributor/docs-gap-ledger.md); the fix it names
is a generic sqlite→PSWS SFTP transport plus a way for a client to declare its
own PSWS ids.

## Timing authority: say what your timestamps are worth

Every instance in `inventory --json` reports three timing fields, and they are
not decoration — they are how a downstream analyst learns how much a
timestamp is worth ([§18](../CLIENT-CONTRACT.md#18-timing-authority-and-the-rtp-default-fallback-v07)).

The safe default is **RTP-default mode**: you convert samples to UTC using
radiod's own published anchor and nominal rate, you subscribe to no timing
authority, and you say so —

```json
"uses_timing_calibration":     false,
"provides_timing_calibration": false,
"timing_authority_applied":    null
```

`timing_authority_applied: null` means "RTP-default"; a non-null object names
the authority, its **tier**, its sigma and the snapshot's age
([§18.5](../CLIENT-CONTRACT.md#185-client-obligations)). Set it only when you
actually apply the correction. Claiming an authority you do not apply
advertises a precision your data does not have, and nothing downstream can
detect the lie.

`uses_timing_calibration` describes *capability* — true if you would subscribe
were an authority available — while `timing_authority_applied` describes the
*current* mode. The tier vocabulary (T6 … T1) and what each is worth on this
station are in
[station-capabilities.md §Timing](station-capabilities.md#timing-you-can-rely-on--tiers).
**Record the tier alongside your data**, not just in the inventory: an
archive whose timestamps cannot be traced to a tier cannot be re-analysed
against a better one later. The mechanics of carrying an anchor through to a
product are the subject of data-and-timing.md *(being written)*.

## Instances and `reporter_id`

If your client can meaningfully run more than once on a host — two antennas,
two radiods, two configurations — it is a *per-instance* client, and §19 fixes
the vocabulary so every client spells it the same way
([§19](../CLIENT-CONTRACT.md#19--per-reporter-instance-and-reporter_id-row-tag-v08)).
One reporter id, matching `[A-Z0-9][A-Z0-9-]*[A-Z0-9]` (`AC0G-B1`,
`KP4MD-RPI4`), serves as the systemd template instance, the config-file stem,
the env-file stem, the data directory and the log directory — it is path-safe
by construction, which is why the format is a MUST
([§19.1](../CLIENT-CONTRACT.md#191--reporter-id-format-must)). Your unit passes
`%i`, your CLI takes `--instance <reporter-id>`, and your config resolves from
`/etc/<client>/<reporter-id>.toml` in preference to the legacy shared path
([§19.2](../CLIENT-CONTRACT.md#192--per-instance-config-must-when-client-supports)).
Every row you write to the sink carries the id as a tag
([§19.3](../CLIENT-CONTRACT.md#193--reporter_id-row-tag-must-when-client-emits-spotsrows)).
A singleton client — one per host, like a magnetometer reader — still uses a
templated unit with a single instance; that costs nothing and means you never
have to retrofit it.

One trap that has been fixed twice in this fleet: in the unit file use `%i`,
never `%I`. `%I` un-escapes systemd's escaping and turns every dash into a
slash, so `AC0G-B4` becomes the path `AC0G/B4` and your `EnvironmentFile` never
matches — silently, because the leading `-` makes a missing file legal.

## Done when

Four checks, in order. Each one fails differently, which is the point:

1. **`my-recorder inventory --json` exits 0 and parses**, run as an
   unprivileged user, with the config unreadable. If it does not, everything
   below reports you as *not installed*.
2. **`smd admin diag drop-in my-recorder` is green** — it walks repo presence,
   `deploy.toml`, contract subcommands, `[client_features]`, catalog, topology
   and the end-to-end adapter view, and exits nonzero on anything red
   ([ADD-A-CLIENT §7](../ADD-A-CLIENT.md#7-verify-the-drop-in)).
3. **Your block appears in `smd status`**, showing your version, your
   frequencies and your issues — the operator's view of you, built from the
   two JSON payloads.
4. **`smd admin validate` passes** — sigmond's cross-client harmonization
   rules (CPU isolation, frequency coverage, radiod resolution, timing chain),
   read-only. This is the check that catches a client that is fine alone and
   wrong next to its neighbours.

Then the slower one: **the station heartbeat carries your version**, because
its component block is assembled from every git checkout under
`/opt/git/sigmond`. That is the fleet noticing you exist.

## Next

- The envelope you must design inside: [station-capabilities.md](station-capabilities.md)
- The capture loop that goes inside `daemon`: [capture-quickstart.md](capture-quickstart.md)
- A real client end to end, and why it stayed Tier 0: [costas-14110-worked-example.md](costas-14110-worked-example.md)
- The scaffold: [skeleton/README.md](skeleton/README.md)
- The rules, in full: [ADD-A-CLIENT.md](../ADD-A-CLIENT.md) and [CLIENT-CONTRACT.md](../CLIENT-CONTRACT.md)
- Where data lands and how time is carried through it: data-and-timing.md *(being written)*
