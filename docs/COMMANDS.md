# smd commands, monitoring and debugging

> **Audience:** operator, contributor
> **Status:** current (moved from the root README 2026-11-21, not re-verified)
> **Verified against:** sigmond 4006b0a on 2026-11-21 — docs (text moved verbatim from README.md)
> **Canonical for:** the smd command reference for operators and administrators

This page held the root README's command reference, monitoring and
debugging sections until 2026-11-21.  `smd --help` and `smd <verb> --help`
remain the authority on options; [contributor/orchestration.md](contributor/orchestration.md)
maps each verb to the code that implements it.

## Command reference

`smd`'s verbs are grouped the same way `smd --help` and the TUI present
them. Run `smd <verb> --help` (or `smd admin <verb> --help`) for the full
options on any verb.

**Lifecycle** — operate on running services. Without a component name they
act on all enabled components; a positional component (and optional
instance) narrows the scope.

| Command | Description |
|---------|-------------|
| `smd start [<component> [<instance>]]` | Start managed services |
| `smd stop [<component> [<instance>]]` | Stop managed services |
| `smd restart [<component> [<instance>]]` | Restart with reset-failed |
| `smd reload [--via=auto\|systemd\|socket] [<component>]` | Reload via SIGHUP or restart |
| `smd status [<component>]` | Service health + client inventory |
| `smd enable <name>...` | Set `enabled = true` in topology |
| `smd disable <name>...` | Set `enabled = false` and stop the units |
| `smd apply` | Reconcile running services with current config |

**Install & components**

| Command | Description |
|---------|-------------|
| `smd install [<client>]` | Install + configure a client, or run the full-suite install |
| `smd bringup` | Guided station bring-up from a catalog profile (installs dormant when hardware is absent) |
| `smd adopt <name>` | Adopt detected hardware or a discovered source — the only verb that starts anything; asks first (`--dry-run`, `--yes`) |
| `smd component list [--catalog]` | Per-component status (lifecycle + git ref + version policy); `--catalog` lists what's installable |
| `smd component update [<name>]` | Pull latest and reapply per topology version policy (root) |
| `smd component add <git-url>` / `smd component remove <name>...` | Clone a component repo; remove one and its topology entry (`--purge`, `--dry-run`) |
| `smd config show\|migrate` | Inspect or migrate coordination config |
| `smd config init\|edit <client>` | Run a client's first-run wizard or edit flow |
| `smd update [--apply] [--latest]` | Plan an update (dry run by default); `--apply` performs it.  `--latest` takes every component to the tip of its main branch and pins the station there; it serves test stations |
| `smd version` | Each component's commit, against the image and upstream |
| `smd sink status\|upload\|fill\|off` | The site sink switch: whether this station sends its data |
| `smd config reporters [--uploader-call NAME]` | The name this machine uploads under |
| `smd remote status\|on\|off` | Remote access for the fleet admin |

**Observe & diagnose** — most diagnostics now live under the `admin` group.

| Command | Description |
|---------|-------------|
| `smd watch <target>` | Live tail for `wspr`, `psk`, `hfdl`, `codar`, `hf-tec`, `mag`, `meteor`, `superdarn`, `ka9q`, `radiod`, `uploads`, `verifier`, `usb`, `gpsdo` |
| `smd admin log [<client>] [--files] [--level L]` | Follow journal (or `--files` for file logs); `--level` sets the client's level via `coordination.env` + SIGHUP |
| `smd admin diag [net\|cpu-affinity\|cpu-freq]` | Network / CPU diagnostics; `cpu-freq --radiod-max-mhz N --other-max-mhz N --save` keeps the caps across reboots |
| `smd admin validate` | Cross-client harmonization rules |
| `smd admin environment list\|probe\|describe` | Situational awareness of network peers (KIWISDRs, GPSDOs, NTP) |
| `smd admin verifier\|storage\|sources\|timing\|radiod\|instance\|rac …` | Specialized maintenance — see `smd admin --help` |
| `smd tui` | Launch the interactive TUI configurator |

> **CLI v2 note.** The verb surface was reorganized into `component` /
> `admin` / `config` groups. The old flat verbs **`smd list`, `smd log`,
> `smd diag`, `smd validate`, and `smd environment` no longer exist** — use
> `smd component list` and `smd admin {log,diag,validate,environment}`
> instead. `smd install` and `smd apply` remain available at the top level.
> Lifecycle scope is now positional (`smd start psk-recorder`); the old
> `--components X,Y` flag still works but is deprecated. Full mapping in
> [docs/CLI-V2-SPEC.md](CLI-V2-SPEC.md).

## Monitoring

### Live status

```bash
smd status
```

For each installed client, status shows the systemd unit state plus data
from the client's contract inventory: version, git commit, number of
channels, active modes, and any reported issues.

### Log viewing

Follow the systemd journal for a client:

```bash
smd admin log psk-recorder
```

Tail the client's file logs (spot logs, decode output):

```bash
smd admin log psk-recorder --files
```

### Runtime log level

Change a client's verbosity without restarting:

```bash
smd admin log psk-recorder --level DEBUG
```

This writes `PSK_RECORDER_LOG_LEVEL=DEBUG` to `/etc/sigmond/coordination.env`
and sends SIGHUP to the client's systemd units.  The client re-reads the
environment and adjusts its logger.  A raised level lasts 24 hours unless
`--for DURATION` says otherwise; sigmond then restores the routine level.

To set a default level for all clients (no SIGHUP — restart or SIGHUP each
client to apply):

```bash
smd admin log --level WARNING
```

## Debugging

### Run diagnostics

```bash
smd admin diag
```

Checks:
- Network reachability (wsprnet.org, wsprdaemon services)
- Dependency versions against pinned commits
- Per-client self-validation (`<client> validate --json`)
- Service health

### Check multicast / IGMP readiness

```bash
smd admin diag net --listen 0   # unprivileged; reads /proc/net/igmp state only
smd admin diag net              # also listens 130 s for queriers (asks for root itself)
```

Classifies your network for multi-host radiod safety and tells you
whether to stay host-local (`ttl=0`), enable a querier, or avoid a
Wi-Fi path. See [docs/networking.md](networking.md) for the full
guide.

### Cross-client validation

```bash
smd admin validate
```

Runs harmonization rules across all enabled clients: CPU core isolation,
frequency coverage vs. radiod sample rate, radiod reference resolution,
and timing chain verification.

## Adding a discretionary client later

The additional clients (hf-tec, superdarn-sounder)
are installed at your discretion. **Installing a client enables it** — there is
no separate enable step:

```bash
# See what's available
smd component list --catalog

# Install it — this also enables it in topology
smd install hf-tec

# Configure it for your station (first-run wizard, or $EDITOR fallback)
smd config init hf-tec

# Start it
smd start hf-tec

# Verify
smd status
```

The forward path is just **install → configure → start** — there is no
separate `enable` step. `smd install` enables the component, and naming a
component to `smd start` enables it too if it was installed-but-disabled. The
only off-switch you need is `smd disable <name>`, which takes a component
offline reversibly (stops its units and clears the topology flag); `smd start
<name>` brings it back. Pass `smd install --no-enable` to install without
enabling (e.g. to stage config first).
