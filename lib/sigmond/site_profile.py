"""site-profile.toml — the single non-secret per-site source of truth.

One operator-edited file at ``/etc/sigmond/site-profile.toml`` captures the
per-installation *identity* (station, PSWS ids, reporter calls, hardware hints)
that would otherwise be entered into several client wizards. ``smd config
render`` translates it into ``coordination.toml`` / ``coordination.env`` — the
established distribution channel every client already reads.

This file holds **no secrets** (credentials live in their own 0600 paths and are
delivered via ``smd admin secrets``). See docs/PROVISIONING-INPUTS.md §8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from .paths import COORDINATION_PATH

SITE_PROFILE_PATH = COORDINATION_PATH.parent / "site-profile.toml"

TEMPLATE = """\
# /etc/sigmond/site-profile.toml — single non-secret per-site source of truth.
# Edit, then run:  sudo smd config render
# NO secrets here — credentials are installed via `smd admin secrets`.
schema_version = 1

[station]
callsign     = "<YOUR_CALL>"       # e.g. AC0G
grid_square  = "<YOUR_GRID>"       # e.g. EM38ww  (or set latitude/longitude)
# latitude   = 38.93
# longitude  = -92.33
# elevation_m = 200
description  = ""                  # free text (antenna / receiver)

[psws]                             # HamSCI PSWS / GRAPE (optional)
enabled       = false
station_id    = ""                 # e.g. S000082 — one per site
instrument_id = ""                 # legacy single id (= hf-timestd/GRAPE's)

[psws.instruments]                 # per-recorder instrument/device ids
# "hf-timestd"   = "172"           # GRAPE instrument id from the portal
# "mag-recorder" = "84"            # magnetometer instrument id from the portal

# NOTE: [psws.stations] (per-recorder station overrides) was REMOVED
# 2026-09-23.  One site, one PSWS station id; instruments differ by
# instrument id, not by station.  A leftover [psws.stations] in an existing
# profile is IGNORED and warned about, never silently honoured.

[reporters]                        # default to [station].callsign when blank
reporter_id      = ""              # WSPR/PSK reporter instance id, e.g. AC0G/S
wsprnet_call     = ""
pskreporter_call = ""
# Let the WsprDaemon servers post this station's WSPR spots to wsprnet.org
# instead of this station posting them itself.  The spots already travel in
# the wsprdaemon tar, so the second upload stops existing.
# ⚠ This puts THIS station's callsign on uploads sent from a WD server's
# address, so it is the operator's decision, never a site-profile default.
wsprnet_proxy    = false

[host]
hostname = ""                      # radiod instance/mDNS names derive from this

[hardware]                         # hints; radiod config remains authoritative
sdr            = ""                # e.g. rx888
sdr_serial     = ""
radiod_status  = ""                # e.g. sigma-rx888mk2-status.local
timing         = ""                # e.g. gps_pps
gnss_vtec_host = ""

[secrets]
# Declared so `smd admin validate` knows which delivered secrets to expect.
require = []                       # e.g. ["earthdata", "rac"]

# [heartbeat]                      # fleet situational-awareness heartbeat (optional)
# enabled       = false            # opt in explicitly — false until you flip this
# station       = ""               # blank defaults to [reporters].reporter_id
# host          = "wd30.wsprdaemon.org"
# port          = 38222            # operator's router-forward to the fleet server;
#                                   # public/self-hosted installs point this at
#                                   # wherever they run their own fleetboard
# sftp_user     = "hamsci-hb"
# remote_path   = "incoming"
# interval_sec  = 300

# [uploads]                        # outbound-uploads POLICY (sigmond#53); absent = enabled
# enabled = false                  # false: uploader manifest is heartbeat-only — a station
#                                  # with no antenna / no PSWS ids must not ship junk upstream
# reason  = "no HF antenna"        # echoed on the fleetboard and in pipelines.toml
# mode    = "discard"              # upload | hold | discard (bench provisioning: ship
#                                  # nothing, keep no backlog); overrides enabled

# [components]                     # checkouts someone at this site looks after by hand
# locally_managed    = ["ka9q-radio"]   # checkout names under /opt/git/sigmond
# locally_managed_by = "Phil"           # optional; named in every message
#                                  # sigmond never fetches, pulls, aligns, pins,
#                                  # resets or rebuilds these, never restarts a
#                                  # service because one moved, and doctor reports
#                                  # no drift for them.  Absent = sigmond manages
#                                  # every checkout (the default).
"""


#: The recorders that upload to the GRAPE (RX888) PSWS instrument.  Since the
#: 2026-08-24 split hamsci-physics uploads GRAPE; hf-timestd's config keeps the
#: field.  One instrument, two names: a lookup under either finds it.
GRAPE_RECORDERS = ("hamsci-physics", "hf-timestd")


@dataclass
class SiteProfile:
    call: str = ""
    grid: str = ""
    lat: float = 0.0
    lon: float = 0.0
    elevation_m: float = 0.0
    description: str = ""
    psws_enabled: bool = False
    psws_station_id: str = ""
    psws_instrument_id: str = ""
    psws_instruments: dict = field(default_factory=dict)
    reporter_id: str = ""
    wsprnet_call: str = ""
    pskreporter_call: str = ""
    wsprnet_proxy: bool = False
    hostname: str = ""
    sdr: str = ""
    sdr_serial: str = ""
    radiod_status: str = ""
    timing: str = ""
    gnss_vtec_host: str = ""
    secrets_require: list = field(default_factory=list)
    # [heartbeat] — sigmond#task-8.  heartbeat_declared distinguishes "the
    # operator opened the block" from "every field happens to be default":
    # `smd config render` only writes coordination.toml's [heartbeat]
    # when this is True, so an untouched profile never seeds a block a
    # future edit would then have to notice and remove.
    heartbeat_declared:     bool = False
    heartbeat_enabled:      bool = False
    heartbeat_station:      str = ""
    heartbeat_host:         str = ""
    heartbeat_port:         int = 22
    heartbeat_sftp_user:    str = "hamsci-hb"
    heartbeat_remote_path:  str = "incoming"
    heartbeat_interval_sec: int = 300
    # [uploads] — sigmond#53 outbound-uploads policy. uploads_declared
    # distinguishes "block present" from "enabled": `smd config render`
    # only writes coordination.toml's [uploads] when the profile declares
    # one. Absent == enabled (no behaviour change for existing hosts).
    uploads_declared:       bool = False
    uploads_enabled:        bool = True
    uploads_reason:         str = ""
    uploads_mode:           str = ""        # upload | hold | discard; "" = from enabled
    # [components] -- checkouts the site manages by hand (Phil's ka9q-radio
    # at KFS).  Empty, the default, means sigmond manages every checkout.
    locally_managed:        list = field(default_factory=list)
    locally_managed_by:     str = ""
    source_path: Optional[Path] = None

    @property
    def effective_wsprnet_call(self) -> str:
        return self.wsprnet_call or self.call

    @property
    def effective_pskreporter_call(self) -> str:
        return self.pskreporter_call or self.call

    @property
    def effective_reporter_id(self) -> str:
        return self.reporter_id or self.call

    @property
    def effective_heartbeat_station(self) -> str:
        """The station identifier the heartbeat attributes to on the
        fleetboard.  An explicit [heartbeat].station wins; blank falls
        back to the same reporter identity coordination [station] already
        uses (write_tick sanitizes any '/' at write time)."""
        return self.heartbeat_station or self.effective_reporter_id

    def instrument_for(self, recorder: str) -> str:
        """Per-recorder PSWS instrument/device id.

        ``[psws.instruments]`` wins; the legacy single
        ``[psws].instrument_id`` remains the GRAPE (hf-timestd) id for
        profiles written before the map existed.
        """
        v = str(self.psws_instruments.get(recorder, "") or "").strip()
        if v:
            return v
        if recorder in GRAPE_RECORDERS:
            # hamsci-physics took over the GRAPE uploads from hf-timestd
            # (2026-08-24); both name the same PSWS instrument.  The wizard
            # files that id under "hf-timestd", so either name finds it.
            for other in GRAPE_RECORDERS:
                v = str(self.psws_instruments.get(other, "") or "").strip()
                if v:
                    return v
            return self.psws_instrument_id
        return ""

    def station_for(self, recorder: str) -> str:
        """PSWS station id for a recorder: the SITE station, always.

        Until 2026-09-23 a ``[psws.stations]`` table let a recorder claim
        its own portal station, added for AC0G's magnetometer (station
        S000082 / instrument 84 against the receiver's S000170).  No
        station ever used it -- AC0G-B4 and AC0G-ND both upload their
        magnetometer under the site station id -- and carrying two station
        identities per site made every "which station is this?" question
        ambiguous for no benefit.  One site, one station id; instruments
        are distinguished by instrument id.
        """
        return self.psws_station_id


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def load_site_profile(path: Optional[Path] = None) -> Optional[SiteProfile]:
    """Parse the site profile, or return None if the file does not exist.
    ``path`` defaults to SITE_PROFILE_PATH, read at call time."""
    if path is None:
        path = SITE_PROFILE_PATH
    if not path.is_file():
        return None
    with open(path, "rb") as fh:
        data = tomllib.load(fh)

    st = data.get("station", {}) or {}
    psws = data.get("psws", {}) or {}
    rep = data.get("reporters", {}) or {}
    host = data.get("host", {}) or {}
    hw = data.get("hardware", {}) or {}
    sec = data.get("secrets", {}) or {}
    hb = data.get("heartbeat", {}) or {}
    up = data.get("uploads", {}) or {}
    comps = data.get("components", {}) or {}
    if not isinstance(comps, dict):
        comps = {}

    def _clean_early(v) -> str:
        v = str(v or "").strip()
        return "" if (v.startswith("<") and v.endswith(">")) else v

    # [psws.stations] was removed 2026-09-23 (one site, one station id).  A
    # profile that still carries it would otherwise have its uploads quietly
    # redirected to the site station -- say so, since AI6VN was unreachable
    # when this landed and nobody could confirm its magnetometer config.
    _dead = psws.get("stations") or {}
    if _dead:
        import sys as _sys
        print(
            f"WARNING: {path}: [psws.stations] is no longer supported and was "
            f"IGNORED ({', '.join(sorted(map(str, _dead)))}). Every recorder "
            f"now uploads under [psws].station_id "
            f"({_clean_early(psws.get('station_id')) or '<unset>'}). Remove the "
            f"section, or open a portal ticket to move the instrument.",
            file=_sys.stderr,
        )

    def _clean(s) -> str:
        s = str(s or "").strip()
        # treat unfilled <...> placeholders as empty
        return "" if (s.startswith("<") and s.endswith(">")) else s

    return SiteProfile(
        call=_clean(st.get("callsign")).upper(),
        grid=_clean(st.get("grid_square")),
        lat=_f(st.get("latitude")),
        lon=_f(st.get("longitude")),
        elevation_m=_f(st.get("elevation_m")),
        description=_clean(st.get("description")),
        psws_enabled=bool(psws.get("enabled", False)),
        psws_station_id=_clean(psws.get("station_id")),
        psws_instrument_id=_clean(psws.get("instrument_id")),
        psws_instruments={
            str(k): _clean(v)
            for k, v in (psws.get("instruments", {}) or {}).items()
            if _clean(v)
        },
        reporter_id=_clean(rep.get("reporter_id")).upper(),
        wsprnet_call=_clean(rep.get("wsprnet_call")).upper(),
        wsprnet_proxy=bool(rep.get("wsprnet_proxy", False)),
        pskreporter_call=_clean(rep.get("pskreporter_call")).upper(),
        hostname=_clean(host.get("hostname")),
        sdr=_clean(hw.get("sdr")),
        sdr_serial=_clean(hw.get("sdr_serial")),
        radiod_status=_clean(hw.get("radiod_status")),
        timing=_clean(hw.get("timing")),
        gnss_vtec_host=_clean(hw.get("gnss_vtec_host")),
        secrets_require=list(sec.get("require", []) or []),
        heartbeat_declared="heartbeat" in data,
        heartbeat_enabled=bool(hb.get("enabled", False)),
        heartbeat_station=_clean(hb.get("station")),
        heartbeat_host=_clean(hb.get("host")),
        heartbeat_port=int(hb.get("port", 22) or 22),
        heartbeat_sftp_user=_clean(hb.get("sftp_user")) or "hamsci-hb",
        heartbeat_remote_path=_clean(hb.get("remote_path")) or "incoming",
        heartbeat_interval_sec=int(hb.get("interval_sec", 300) or 300),
        uploads_declared="uploads" in data,
        uploads_enabled=bool(up.get("enabled", True)),
        uploads_reason=_clean(up.get("reason")),
        uploads_mode=_clean(up.get("mode")).lower(),
        locally_managed=_name_list(comps.get("locally_managed"), path),
        locally_managed_by=_clean(comps.get("locally_managed_by")),
        source_path=path,
    )


def _name_list(value, path) -> list:
    """[components].locally_managed as a list of names.  A lone string counts
    as one name.  Anything else is warned about and read as no names, never
    as an error."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        import sys as _sys
        print(f"WARNING: {path}: [components].locally_managed must be a list "
              f"of component names; ignored", file=_sys.stderr)
        return []
    out = []
    for v in value:
        v = v.strip() if isinstance(v, str) else ""
        if v and v not in out:
            out.append(v)
    return out


@dataclass(frozen=True)
class LocallyManaged:
    """The checkouts this site manages by hand ([components].locally_managed).

    sigmond leaves each one exactly as it stands: no fetch, pull, checkout,
    reset, pin, install or rebuild, and no restart because it moved.  Empty
    (the default) is today's behaviour."""

    names: frozenset = frozenset()
    by: str = ""

    def __contains__(self, name) -> bool:
        return name in self.names

    def __bool__(self) -> bool:
        return bool(self.names)

    def __iter__(self):
        return iter(sorted(self.names))

    def label(self) -> str:
        """`locally managed`, plus who manages it when the profile says."""
        return "locally managed" + (f" ({self.by})" if self.by else "")

    def note(self, name: str, sha: Optional[str] = None) -> str:
        """The one line every command prints for such a component."""
        at = f" at {sha[:8]}" if sha else ""
        return f"{name}: {self.label()} — left as is{at}"


NOT_LOCALLY_MANAGED = LocallyManaged()

#: (profile path, name) pairs already warned about, so a command that asks
#: several times warns once.
_WARNED_UNKNOWN: set = set()


def _known_component_names() -> Optional[set]:
    """Every name a checkout could carry here: the catalog's entries, the
    checkouts on disk, and sigmond.  None when neither can be read; then no
    name is called unknown."""
    names: set = set()
    readable = False
    try:
        from .catalog import load_catalog
        names |= set(load_catalog())
        readable = True
    except Exception:                                   # noqa: BLE001
        pass
    try:
        from .doctor import component_checkouts
        from .installer import GIT_BASE
        found = component_checkouts(GIT_BASE)
        names |= {d.name for d in found}
        readable = readable or bool(found)
    except Exception:                                   # noqa: BLE001
        pass
    if not readable:
        return None
    names.add("sigmond")
    return names


def locally_managed_components(path: Optional[Path] = None,
                               known=None) -> LocallyManaged:
    """The site's locally managed checkouts, read from site-profile.toml.

    No profile, or no [components] block, reads as none.  A name that names
    no known component is warned about (once) and kept: it matches nothing,
    so it changes nothing.  A profile that does not parse is warned about and
    reads as none, so every component is managed as usual; the warning says
    so, since on a site that relies on the list sigmond may then touch a
    checkout it was told to leave alone."""
    import sys as _sys
    if path is None:
        path = SITE_PROFILE_PATH
    try:
        prof = load_site_profile(path)
    except Exception as exc:                            # noqa: BLE001
        print(f"WARNING: {path} unreadable ({exc}); [components] "
              f"locally_managed NOT applied -- every component is treated as "
              f"managed by sigmond", file=_sys.stderr)
        return NOT_LOCALLY_MANAGED
    if prof is None or not prof.locally_managed:
        return NOT_LOCALLY_MANAGED
    if known is None:
        known = _known_component_names()
    if known is not None:
        for name in prof.locally_managed:
            if name not in known and (str(path), name) not in _WARNED_UNKNOWN:
                _WARNED_UNKNOWN.add((str(path), name))
                print(f"WARNING: {path}: [components] locally_managed names "
                      f"{name!r}, which is no component here; it matches "
                      f"nothing", file=_sys.stderr)
    return LocallyManaged(frozenset(prof.locally_managed),
                          prof.locally_managed_by)


def _split_block(text: str, name: str) -> tuple:
    """(text without the [name] table and its [name.*] subtables, that block).
    Only a header at line start counts; a commented example does not.  A
    header may carry a trailing comment, as TEMPLATE's example does once
    uncommented: `[uploads]   # outbound-uploads POLICY`.  The split assumes the
    block holds only scalar values: a multi-line array whose line starts with
    `[` would end the block early.  [uploads] holds only scalars today."""
    header = re.compile(r'\[\s*' + re.escape(name) + r'\s*(\]|\.)')
    keep, block, inside = [], [], False
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith('['):
            inside = bool(header.match(s))
        (block if inside else keep).append(line)
    return ''.join(keep), ''.join(block)


def carry_uploads_block(new_text: str, old_text, name: str = 'uploads') -> str:
    """The profile a wizard run should install: ``new_text``, except that an
    [uploads] block the old profile declares survives unchanged.  The wizard
    rewrites site-profile whole on every run, and `smd align` installs new
    wizards on stations whose operator already set the site sink switch;
    a rewrite must never reset it (tasks/plan-sink-control.md §10.3 item 2).
    ``name`` carries another operator-only block the same way."""
    if not old_text:
        return new_text
    _, old_block = _split_block(old_text, name)
    if not old_block.strip():
        return new_text
    rest, _ = _split_block(new_text, name)
    return rest.rstrip('\n') + '\n\n' + old_block.strip('\n') + '\n'


#: Blocks only an operator writes, which a wizard rewrite of site-profile
#: keeps.  [components] holds the locally managed list: losing it would let
#: the next `smd update` pull a checkout the site manages by hand.
OPERATOR_BLOCKS = ('uploads', 'components')


def carry_operator_blocks(new_text: str, old_text) -> str:
    """``carry_uploads_block`` for each block in OPERATOR_BLOCKS."""
    for name in OPERATOR_BLOCKS:
        new_text = carry_uploads_block(new_text, old_text, name)
    return new_text
