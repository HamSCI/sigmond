"""Sigmond coordination config.

`/etc/sigmond/coordination.toml` is the authoritative source for everything
that no single client owns: station identity, the named radiod instances
(local or remote), CPU budget for the local host, and the registry of
client instances bound to those radiods.

A client's own native config file remains authoritative for that client.
Sigmond reads this file, runs harmonization rules, and writes the
flattened view to `/etc/sigmond/coordination.env` which clients consume
via systemd `EnvironmentFile=-`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .paths import COORDINATION_PATH


@dataclass
class Host:
    call: str = ""
    grid: str = ""
    lat:  float = 0.0
    lon:  float = 0.0


@dataclass
class Station:
    """Non-secret per-site identity beyond call/grid — published from
    site-profile.toml by `smd config render`. Additive to [host]; the existing
    identity path (config identity/refresh) never touches this block."""
    psws_id: str = ""
    instrument_id: str = ""
    # Site-wide reporter identity (e.g. "AC0G/S") — the ONE id every
    # upload path (wsprdaemon rx_sign, PSKReporter receiver, wsprnet)
    # defaults to.  Clients read STATION_REPORTER_ID; the per-service
    # *_call keys below remain narrower overrides.  HamSCI/sigmond#38.
    reporter_id: str = ""
    wsprnet_call: str = ""
    pskreporter_call: str = ""


@dataclass
class Radiod:
    id: str
    host: str = "localhost"
    status_dns: str = ""
    samprate_hz: int = 0
    cores: str = ""
    radio_conf: str = ""
    # USB iSerial of the locally-attached SDR this instance owns.  Empty
    # for remote radiods or when the operator declined to lock the
    # binding (radiod will then bind to the first matching frontend).
    # Lets `smd config init radiod` recognise on re-run that this
    # physical SDR is already named, and only prompt when a *new*
    # serial appears on the USB bus.
    sdr_serial: str = ""

    @property
    def effective_status_dns(self) -> str:
        """The radiod's status/control mDNS name.

        Normally the ``status_dns`` field.  But coordination keys radiods two
        ways: ``smd config register-radiod`` writes a short ``id`` WITH a
        ``status_dns`` field, while the bring-up path keys the block by the full
        status DNS and leaves the field empty.  In that second form the ``id``
        IS the status DNS — fall back to it.  Without this, single-radiod
        ``_resolve_radiod_status`` returned '' and config-init never learned the
        radiod address (greenfield: wspr-recorder came up with a placeholder)."""
        if self.status_dns:
            return self.status_dns
        return self.id if self.id.endswith(".local") else ""

    @property
    def is_local(self) -> bool:
        return self.host in ("localhost", "127.0.0.1", "::1", "")


@dataclass
class Heartbeat:
    """``[heartbeat]`` — fleet situational-awareness heartbeat config
    (sigmond#task-8, PRODUCER-THREAT-MODEL.md).  Keys are FIXED, shared
    with the manifest-rendering side that turns ``host``/``port``/
    ``sftp_user``/``remote_path`` into an actual sftp delivery pipeline;
    ``sigmond.heartbeat.assemble`` itself only consumes ``station`` and
    ``interval_sec`` (``enabled`` gates whether ``smd admin heartbeat
    emit`` runs at all). Absent ``[heartbeat]`` parses to every default
    below, i.e. disabled — greenfield hosts never emit until an operator
    opts in."""
    enabled:      bool = False
    station:      str  = ""
    host:         str  = ""
    port:         int  = 22
    sftp_user:    str  = "hamsci-hb"
    remote_path:  str  = "incoming"
    interval_sec: int  = 300


@dataclass
class Uploads:
    """``[uploads]`` — site-level OUTBOUND-UPLOADS POLICY (sigmond#53).
    ``enabled = false`` makes ``sigmond.uploader_manifest`` render the
    heartbeat pipeline ONLY (a station without an antenna or PSWS identity
    must not ship junk to shared gateways), and every surface that reads
    it says "disabled by policy" — never INCONCLUSIVE.  The heartbeat is
    never subject to this switch.  Absent ``[uploads]`` parses to enabled:
    today's behaviour on every existing host.  ``reason`` is free text,
    echoed verbatim on the fleetboard and in the manifest header."""
    enabled: bool = True
    reason:  str  = ""


@dataclass
class Cpu:
    suite_cores:           str = ""
    worker_cores:          str = ""
    reserved_cpus:         str = ""
    owns_timestd_affinity: bool = True


@dataclass
class ClientInstance:
    client_type: str              # "hf-timestd", "psk-recorder", "ka9q-web", ...
    instance:    str = "default"
    radiod_id:   Optional[str] = None
    extras:      dict = field(default_factory=dict)

    @property
    def unit_name(self) -> str:
        # Systemd sanitization: instance names should be %i-safe.
        return f"{self.client_type}@{self.instance}"


@dataclass
class DiskBudget:
    root_path:    str = "/var"
    warn_percent: int = 80


@dataclass
class TimingAuthority:
    """CLIENT-CONTRACT.md §18 — pointer to the timing-authority service
    a client may subscribe to for authority-corrected UTC labelling.

    The contract publishes two scopes:

    - **station-wide** (`TIMING_AUTHORITY*` env keys, used by non-radiod
      clients per §18.3 and as a fallback by radiod-substrate clients);
    - **per-radiod** (`RADIOD_<id>_TIMING_AUTHORITY*`, parallel to §8
      chain-delay distribution, used by radiod-substrate clients).

    Both are operator-declared in ``coordination.toml``.  A later phase
    may auto-populate from any client's inventory that reports
    ``provides_timing_calibration: true``; today's adapter just emits
    what the operator declared.  Empty fields → no env key emitted.

    The dataclass treats ``source`` and ``endpoint`` as the required
    pair: a TimingAuthority with one but not the other is incomplete
    and emits nothing (an operator declared the block but didn't
    finish filling it in — surfacing that as a validate-time warning
    is a future enhancement).  ``tier_min`` is optional — its absence
    means "the client decides" per §18.3.
    """
    source:   str = ""    # e.g. "hf-timestd@bee3"
    endpoint: str = ""    # URI: unix://, tcp://, etc.
    tier_min: str = ""    # operator's floor: "T4", "T5", "T6", ...

    @property
    def is_declared(self) -> bool:
        """True when the pair sigmond needs to emit env keys is present."""
        return bool(self.source and self.endpoint)


@dataclass
class Coordination:
    host:    Host                  = field(default_factory=Host)
    station: Station               = field(default_factory=Station)
    radiods: dict                  = field(default_factory=dict)   # id -> Radiod
    cpu:     Cpu                   = field(default_factory=Cpu)
    clients: list                  = field(default_factory=list)   # list[ClientInstance]
    disk_budget: DiskBudget        = field(default_factory=DiskBudget)
    timing_authority: TimingAuthority = field(default_factory=TimingAuthority)
    per_radiod_timing_authority: dict = field(default_factory=dict)  # radiod_id -> TimingAuthority
    heartbeat: Heartbeat            = field(default_factory=Heartbeat)
    uploads:   Uploads              = field(default_factory=Uploads)
    source_path: Optional[Path]    = None

    def instances_of(self, client_type: str) -> list:
        return [c for c in self.clients if c.client_type == client_type]

    def instances_bound_to(self, radiod_id: str) -> list:
        return [c for c in self.clients if c.radiod_id == radiod_id]

    def local_radiods(self) -> list:
        return [r for r in self.radiods.values() if r.is_local]

    def resolve_radiod(self, radiod_id: Optional[str]) -> Optional[Radiod]:
        if not radiod_id:
            return None
        return self.radiods.get(radiod_id)


def load_coordination(path: Path = COORDINATION_PATH) -> Coordination:
    """Load coordination.toml, or return empty defaults when absent."""
    import tomllib

    if not path.exists():
        return Coordination()

    with open(path, 'rb') as f:
        raw = tomllib.load(f)

    return parse_coordination(raw, source_path=path)


def parse_coordination(raw: dict, source_path: Optional[Path] = None) -> Coordination:
    """Parse a pre-loaded TOML dict into a Coordination object."""
    host_raw = raw.get('host', {}) or {}
    host = Host(
        call=host_raw.get('call', ''),
        grid=host_raw.get('grid', ''),
        lat=float(host_raw.get('lat', 0.0) or 0.0),
        lon=float(host_raw.get('lon', 0.0) or 0.0),
    )

    station_raw = raw.get('station', {}) or {}
    station = Station(
        psws_id=str(station_raw.get('psws_id', '') or ''),
        instrument_id=str(station_raw.get('instrument_id', '') or ''),
        reporter_id=str(station_raw.get('reporter_id', '') or ''),
        wsprnet_call=str(station_raw.get('wsprnet_call', '') or ''),
        pskreporter_call=str(station_raw.get('pskreporter_call', '') or ''),
    )

    radiods: dict = {}
    for rid, rcfg in (raw.get('radiod', {}) or {}).items():
        rcfg = rcfg or {}
        radiods[rid] = Radiod(
            id=rid,
            host=rcfg.get('host', 'localhost'),
            status_dns=rcfg.get('status_dns', ''),
            samprate_hz=int(rcfg.get('samprate_hz', 0) or 0),
            cores=rcfg.get('cores', ''),
            radio_conf=rcfg.get('radio_conf', ''),
            sdr_serial=str(rcfg.get('sdr_serial', '') or '').strip(),
        )

    cpu_raw = raw.get('cpu', {}) or {}
    cpu = Cpu(
        suite_cores=cpu_raw.get('suite_cores', ''),
        worker_cores=cpu_raw.get('worker_cores', ''),
        reserved_cpus=cpu_raw.get('reserved_cpus', ''),
        owns_timestd_affinity=bool(cpu_raw.get('owns_timestd_affinity', True)),
    )

    clients: list = []
    clients_raw = raw.get('clients', {}) or {}
    for ctype, entries in clients_raw.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            entry = entry or {}
            instance_name = entry.get('instance', 'default')
            radiod_id     = entry.get('radiod_id')
            extras = {k: v for k, v in entry.items()
                      if k not in ('instance', 'radiod_id')}
            clients.append(ClientInstance(
                client_type=ctype,
                instance=instance_name,
                radiod_id=radiod_id,
                extras=extras,
            ))

    disk_raw = raw.get('disk_budget', {}) or {}
    disk = DiskBudget(
        root_path=disk_raw.get('root_path', '/var'),
        warn_percent=int(disk_raw.get('warn_percent', 80) or 80),
    )

    # [timing_authority] — station-wide pointer (CLIENT-CONTRACT §18.3).
    # [timing_authority.per_radiod.<id>] — per-radiod overrides.  Both
    # default to empty TimingAuthority instances (is_declared = False),
    # which render_env treats as "do not emit."
    ta_raw = raw.get('timing_authority', {}) or {}
    station_wide = TimingAuthority(
        source=str(ta_raw.get('source', '') or ''),
        endpoint=str(ta_raw.get('endpoint', '') or ''),
        tier_min=str(ta_raw.get('tier_min', '') or ''),
    )
    per_radiod_raw = ta_raw.get('per_radiod', {}) or {}
    per_radiod: dict = {}
    for rid, entry in per_radiod_raw.items():
        entry = entry or {}
        per_radiod[rid] = TimingAuthority(
            source=str(entry.get('source', '') or ''),
            endpoint=str(entry.get('endpoint', '') or ''),
            tier_min=str(entry.get('tier_min', '') or ''),
        )

    hb_raw = raw.get('heartbeat', {}) or {}
    heartbeat = Heartbeat(
        enabled=bool(hb_raw.get('enabled', False)),
        station=str(hb_raw.get('station', '') or ''),
        host=str(hb_raw.get('host', '') or ''),
        port=int(hb_raw.get('port', 22) or 22),
        sftp_user=str(hb_raw.get('sftp_user', '') or '') or 'hamsci-hb',
        remote_path=str(hb_raw.get('remote_path', '') or '') or 'incoming',
        interval_sec=int(hb_raw.get('interval_sec', 300) or 300),
    )

    up_raw = raw.get('uploads', {}) or {}
    uploads = Uploads(
        enabled=bool(up_raw.get('enabled', True)),
        reason=str(up_raw.get('reason', '') or ''),
    )

    return Coordination(
        host=host,
        station=station,
        radiods=radiods,
        cpu=cpu,
        clients=clients,
        disk_budget=disk,
        timing_authority=station_wide,
        per_radiod_timing_authority=per_radiod,
        heartbeat=heartbeat,
        uploads=uploads,
        source_path=source_path,
    )


# ---------------------------------------------------------------------------
# Rendering — coordination.toml → coordination.env
# ---------------------------------------------------------------------------

def _env_key(*parts) -> str:
    """Build an ENV_KEY from pieces, uppercasing and sanitizing."""
    raw = '_'.join(str(p) for p in parts if p)
    out = []
    for ch in raw:
        if ch.isalnum():
            out.append(ch.upper())
        else:
            out.append('_')
    # Collapse runs of underscores.
    collapsed = []
    prev_underscore = False
    for ch in out:
        if ch == '_':
            if not prev_underscore:
                collapsed.append(ch)
            prev_underscore = True
        else:
            collapsed.append(ch)
            prev_underscore = False
    return ''.join(collapsed).strip('_')


def _passthrough_extras_for(client_type: str) -> list:
    """Read ``[client.coordination] passthrough_extras`` from a client's
    ``/opt/git/sigmond/<client>/deploy.toml``.  Returns ``[]`` when the file is
    absent, unreadable, or the key is unset — clients opt in by shipping
    the block in their deploy.toml.  Wave 2 will move the canonical
    deploy.toml lookup into ``discover.py``; this minimal reader keeps
    Wave 1 free of cross-module surgery.
    """
    import tomllib
    path = Path(f"/opt/git/sigmond/{client_type}/deploy.toml")
    try:
        if not path.exists():
            return []
        with open(path, 'rb') as f:
            data = tomllib.load(f)
    except (OSError, PermissionError, tomllib.TOMLDecodeError):
        return []
    coord_block = (data.get('client', {}) or {}).get('coordination', {}) or {}
    keys = coord_block.get('passthrough_extras', []) or []
    return [str(k) for k in keys if isinstance(k, str)]


def write_host_identity(call: str = '', grid: str = '', *,
                        lat: float = 0.0, lon: float = 0.0,
                        path: Path = COORDINATION_PATH) -> bool:
    """Merge a ``[host]`` identity section into coordination.toml.

    This is the source of station identity for the whole suite: client config
    builds STATION_CALL / STATION_GRID (and LAT/LON) from ``coord.host`` (see
    client_config._build_env_bag), so seeding ``[host]`` here propagates the
    callsign/grid to every client configurator — radiod, hf-timestd, wspr, psk.
    Without it, a greenfield host has no ``[host]`` section and every client
    falls back to its placeholder identity.

    Merges field-by-field (only non-empty arguments override existing values,
    so a later richer config isn't clobbered with blanks) and text-merges the
    section so existing ``[radiod.*]`` / ``[cpu]`` / ``[[clients]]`` blocks are
    preserved verbatim.  Idempotent: returns True only when the file changed.
    """
    cur = load_coordination(path).host
    call = call or cur.call
    grid = grid or cur.grid
    lat = lat or cur.lat
    lon = lon or cur.lon

    fields = []
    if call:
        fields.append(f'call = "{call}"')
    if grid:
        fields.append(f'grid = "{grid}"')
    if lat:
        fields.append(f'lat = {lat}')
    if lon:
        fields.append(f'lon = {lon}')
    if not fields:
        return False
    block = '[host]\n' + '\n'.join(fields) + '\n'

    existing = path.read_text() if path.exists() else ''
    lines = existing.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if ln.strip() == '[host]'), None)
    if start is None:
        # Host identity belongs at the top; keep a blank line before the rest.
        new_text = block + ('\n' + existing if existing.strip() else '')
    else:
        end = next((j for j in range(start + 1, len(lines))
                    if lines[j].lstrip().startswith('[')), len(lines))
        new_text = ''.join(lines[:start]) + block + ''.join(lines[end:])

    if new_text == existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text)
    return True


def write_station_identity(reporter_id: str = '',
                           path: Path = COORDINATION_PATH) -> bool:
    """Merge a ``[station]`` section into coordination.toml.

    Companion to write_host_identity for the station-scope keys rendered
    into coordination.env (STATION_REPORTER_ID etc.).  Without a
    ``[station]`` block STATION_REPORTER_ID never renders, and
    reporter-keyed clients fall back to their systemd instance name —
    the path-safe storage spelling (``AC0G=B4``), not the real reporter
    id (``AC0G/B4``) — misattributing every uploaded spot (sigmond#38).
    ``smd config render`` writes this from site-profile.toml; this writer
    covers the flags-only path (bring-up ``--reporter`` with no profile).

    Merge semantics match write_host_identity: non-empty arguments
    override, existing values are otherwise preserved, and all other
    sections pass through verbatim.  Returns True only when the file
    changed.
    """
    cur = load_coordination(path).station
    reporter_id = (reporter_id or '').strip() or cur.reporter_id

    fields = []
    if reporter_id:
        fields.append(f'reporter_id = "{reporter_id}"')
    for key, val in (('psws_id', cur.psws_id),
                     ('instrument_id', cur.instrument_id),
                     ('wsprnet_call', cur.wsprnet_call),
                     ('pskreporter_call', cur.pskreporter_call)):
        if val:
            fields.append(f'{key} = "{val}"')
    if not fields:
        return False
    block = '[station]\n' + '\n'.join(fields) + '\n'

    existing = path.read_text() if path.exists() else ''
    lines = existing.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip() == '[station]'), None)
    if start is None:
        new_text = block + ('\n' + existing if existing.strip() else '')
    else:
        end = next((j for j in range(start + 1, len(lines))
                    if lines[j].lstrip().startswith('[')), len(lines))
        new_text = ''.join(lines[:start]) + block + ''.join(lines[end:])

    if new_text == existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text)
    return True


def station_identity_env(coord: 'Coordination') -> dict:
    """The STATION_* identity bag (CLIENT-CONTRACT §14.2/§14.3), defined once.

    Returns name -> value for every identity field this host actually declares.
    A field the host has not set is OMITTED rather than emitted empty: an empty
    value would overwrite a value already sitting in a client's config.

    ⛔ Two places used to build this bag independently, and they drifted.
    `render_env()` (which writes coordination.env) emitted the full set;
    `commands.client_config._build_env_bag()` (which feeds `smd config
    init|edit`) read only ``coord.host`` and emitted CALL/GRID/LAT/LON.  So the
    identity a client's own wizard saw depended on which path reached it.

    AC0G-ND, 2026-09-04: enrolled with PSWS as S000111 / instrument 115, with
    both ids sitting in coordination.env, and `smd config init hamsci-physics`
    still wrote empty ids and reported "no PSWS station id — science runs
    locally, GRAPE upload is skipped."  A funded station computing GRAPE it
    could never upload, with nothing reporting a fault.

    Both consumers now read this function, so a field added here reaches both.
    """
    env: dict = {}
    if coord.host.call:
        env['STATION_CALL'] = coord.host.call
        # Compat alias: mag-recorder and hamsci-physics read STATION_CALLSIGN.
        # Emit both so every client auto-seeds the callsign regardless of which
        # name it expects.  STATION_CALL is canonical.
        env['STATION_CALLSIGN'] = coord.host.call
    if coord.host.grid:
        env['STATION_GRID'] = coord.host.grid
    if coord.host.lat:
        env['STATION_LAT'] = str(coord.host.lat)
    if coord.host.lon:
        env['STATION_LON'] = str(coord.host.lon)

    st = coord.station
    # Names match what clients already read (mag-recorder and hamsci-physics
    # both read STATION_PSWS_STATION_ID); the reporter-call keys are an
    # override hook for clients whose reporter call differs from STATION_CALL.
    for key, val in (('STATION_REPORTER_ID', st.reporter_id),
                     ('STATION_PSWS_STATION_ID', st.psws_id),
                     ('STATION_PSWS_INSTRUMENT_ID', st.instrument_id),
                     ('STATION_WSPRNET_CALL', st.wsprnet_call),
                     ('STATION_PSKREPORTER_CALL', st.pskreporter_call)):
        if val:
            env[key] = str(val)
    return env


def render_env(coord: Coordination,
               passthrough_lookup: Optional[Callable[[str], list]] = None) -> str:
    """Render the coordination as KEY=VALUE lines suitable for systemd
    EnvironmentFile=.

    ``passthrough_lookup`` is the function that, given a client_type,
    returns the list of ``extras`` keys that client wants piped into
    coordination.env.  Defaults to reading the client's deploy.toml.
    Tests inject a fake lookup to avoid touching ``/opt/git/sigmond``.
    """
    if passthrough_lookup is None:
        passthrough_lookup = _passthrough_extras_for

    lines = [
        '# Generated by sigmond — do not edit.',
        '# Source: /etc/sigmond/coordination.toml',
        '',
    ]
    # Host identity + the station block beyond call/grid, published from
    # site-profile.toml by `smd config render` (CLIENT-CONTRACT §14.2
    # extension).  Both groups come from station_identity_env() so this file
    # and `smd config init` cannot disagree about what a client is told.
    ident = station_identity_env(coord)
    host_keys = ('STATION_CALL', 'STATION_CALLSIGN', 'STATION_GRID',
                 'STATION_LAT', 'STATION_LON')
    host_lines = [f'{k}={ident[k]}' for k in host_keys if k in ident]
    if host_lines:
        lines.extend(host_lines)
        lines.append('')
    st_lines = [f'{k}={v}' for k, v in ident.items() if k not in host_keys]
    if st_lines:
        lines.extend(st_lines)
        lines.append('')

    for rid, r in sorted(coord.radiods.items()):
        prefix = _env_key('RADIOD', rid)
        lines.append(f'{prefix}_HOST={r.host}')
        if r.effective_status_dns:
            lines.append(f'{prefix}_STATUS={r.effective_status_dns}')
        if r.samprate_hz:
            lines.append(f'{prefix}_SAMPRATE={r.samprate_hz}')
        # CLIENT-CONTRACT §18.3 per-radiod scope.  Per-radiod overrides
        # are independent of the station-wide pointer below; if an
        # operator declares both, a radiod-substrate client takes the
        # per-radiod variant.  Emitted alongside RADIOD_<id>_STATUS /
        # RADIOD_<id>_SAMPRATE so all per-radiod facts cluster together
        # in the rendered env file.
        ta = coord.per_radiod_timing_authority.get(rid)
        if ta and ta.is_declared:
            lines.append(f'{prefix}_TIMING_AUTHORITY={ta.source}')
            lines.append(f'{prefix}_TIMING_AUTHORITY_ENDPOINT={ta.endpoint}')
            if ta.tier_min:
                lines.append(f'{prefix}_TIMING_AUTHORITY_TIER_MIN={ta.tier_min}')
        lines.append('')

    # CLIENT-CONTRACT §18.3 station-wide scope.  Non-radiod clients
    # (mag-recorder, KiwiSDR-based recorders) consume these.  Radiod
    # clients fall back here when no per-radiod entry is declared.
    if coord.timing_authority.is_declared:
        lines.append(f'TIMING_AUTHORITY={coord.timing_authority.source}')
        lines.append(f'TIMING_AUTHORITY_ENDPOINT={coord.timing_authority.endpoint}')
        if coord.timing_authority.tier_min:
            lines.append(f'TIMING_AUTHORITY_TIER_MIN={coord.timing_authority.tier_min}')
        lines.append('')

    # Cross-radiod summary for clients (CLIENT-CONTRACT v0.5 §14.2):
    # SIGMOND_RADIOD_COUNT is always set when any radiod is declared, so a
    # client knows whether it needs to disambiguate by instance.
    # SIGMOND_RADIOD_STATUS is set ONLY when exactly one radiod is
    # declared — that's the §14.2 resolution rule (2): "if exactly one
    # [radiod.<id>] is declared, use its status_dns".  With >1 radiod, we
    # leave it unset so clients have to consult RADIOD_<ID>_STATUS by
    # instance.  Rule (1) (per-instance lookup via [[clients.X]]) is a
    # config-init-time concern that the wizard handles separately —
    # there's no static answer that fits a host-wide env file.
    if coord.radiods:
        lines.append(f'SIGMOND_RADIOD_COUNT={len(coord.radiods)}')
        if len(coord.radiods) == 1:
            only = next(iter(coord.radiods.values()))
            if only.effective_status_dns:
                lines.append(f'SIGMOND_RADIOD_STATUS={only.effective_status_dns}')
        lines.append('')

    for c in coord.clients:
        if not c.radiod_id:
            continue
        prefix = _env_key(c.client_type, c.instance)
        lines.append(f'{prefix}_RADIOD={c.radiod_id}')
    if any(c.radiod_id for c in coord.clients):
        lines.append('')

    # Generic extras passthrough — clients opt in via deploy.toml
    # [client.coordination] passthrough_extras = [...].  Cache the lookup
    # per client_type so we don't re-read deploy.toml for every instance.
    keys_by_type: dict = {}
    emitted: set = set()                    # (client_type, instance, key)
    for c in coord.clients:
        if c.client_type not in keys_by_type:
            keys_by_type[c.client_type] = passthrough_lookup(c.client_type)
        wanted = keys_by_type[c.client_type]
        if not wanted:
            continue
        for key in wanted:
            if key not in c.extras:
                continue
            line = f'{_env_key(c.client_type, c.instance, key)}={c.extras[key]}'
            lines.append(line)
            emitted.add((c.client_type, c.instance, key))

    # Legacy ka9q-web port hardcode — kept as fallback for instances whose
    # deploy.toml hasn't yet declared passthrough_extras.  Removed in
    # Wave 2 once all clients ship the [client.coordination] block.
    for c in coord.clients:
        if c.client_type == 'ka9q-web' and 'port' in c.extras:
            if (c.client_type, c.instance, 'port') in emitted:
                continue
            prefix = _env_key('KA9Q_WEB', c.instance)
            lines.append(f'{prefix}_PORT={c.extras["port"]}')

    return '\n'.join(lines).rstrip() + '\n'
