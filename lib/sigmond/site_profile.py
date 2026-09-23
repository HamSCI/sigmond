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
"""


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
        if recorder == "hf-timestd":
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


def load_site_profile(path: Path = SITE_PROFILE_PATH) -> Optional[SiteProfile]:
    """Parse the site profile, or return None if the file does not exist."""
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
        source_path=path,
    )
