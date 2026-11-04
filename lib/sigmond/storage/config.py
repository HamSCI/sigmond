"""storage.toml: level overrides, the time-to-full horizon, shares,
directives, hands-off window.

Loading never raises.  A bad key drops alone and leaves one problem line;
a bad [levels] table falls back whole.
"""

import re
import tomllib
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Optional

from sigmond.storage.model import Levels

CONFIG_PATH = Path("/etc/sigmond/storage.toml")
POLICY_RE = r"^(give-up-oldest:(pinned|raw|derived|sent)|stop-writing|refuse-new-rows|custom:[a-z0-9-]+)$"
MAX_HANDS_OFF = timedelta(days=14)
_LEVEL_KEYS = ("watch", "tighten", "demand", "final", "client_watch")
DEFAULT_HORIZON_H = 24.0
MAX_HORIZON_H = 24.0 * 365
_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class StorageConfig:
    levels: Levels = Levels()
    shares: Mapping[str, float] = field(default_factory=dict)
    directives: Mapping[str, str] = field(default_factory=dict)
    hands_off_until: Optional[datetime] = None
    hands_off_reason: str = ""
    pool_share: Optional[float] = None
    expect_host_report: Optional[bool] = None
    problems: tuple = ()
    horizon_h: float = 24.0     # [levels] horizon_h: time to full under this -> WATCH


def _num(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _parse_levels(tbl, problems: list) -> Levels:
    vals = {}
    for k, v in tbl.items():
        if k not in _LEVEL_KEYS:
            problems.append(f"levels: unknown key {k!r}; using default levels")
            return Levels()
        n = _num(v)
        if n is None:
            problems.append(f"levels: {k} is not a number; using default levels")
            return Levels()
        vals[k] = n
    lv = Levels(**vals)
    if not (lv.watch < lv.tighten < lv.demand < lv.final < 100) or not (0 < lv.client_watch <= 100):
        problems.append("levels: need watch < tighten < demand < final < 100 and "
                        "0 < client_watch <= 100; using default levels")
        return Levels()
    return lv


def _parse_when(v) -> Optional[datetime]:
    if isinstance(v, datetime):
        d = v
    elif isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if d.tzinfo is None:
        return None
    return d.astimezone(timezone.utc)


def load_storage_config(path: Path = CONFIG_PATH) -> StorageConfig:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return StorageConfig()
    except OSError as e:
        return StorageConfig(problems=(f"{path}: cannot read: {e}",))
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        return StorageConfig(problems=(f"{path}: cannot parse, using defaults: {e}",))

    problems: list = []
    def table(name):
        t = data.get(name, {})
        if not isinstance(t, dict):
            problems.append(f"{name}: not a table; ignored")
            return {}
        return t

    lv_tbl = dict(table("levels"))
    horizon_h = DEFAULT_HORIZON_H
    if "horizon_h" in lv_tbl:
        n = _num(lv_tbl.pop("horizon_h"))
        if n is None or not (0 < n <= MAX_HORIZON_H):
            problems.append(f"levels.horizon_h: not a number of hours within 0..{MAX_HORIZON_H:g}; "
                            f"using {DEFAULT_HORIZON_H:g}")
        else:
            horizon_h = n
    levels = _parse_levels(lv_tbl, problems) if lv_tbl else Levels()

    shares = {}
    for k, v in table("shares").items():
        n = _num(v)
        if n is None or n <= 0:
            problems.append(f"shares.{k}: {v!r} is not a positive number; dropped")
        else:
            shares[k] = n

    directives = {}
    for k, v in table("directives").items():
        if isinstance(v, str) and re.match(POLICY_RE, v):
            directives[k] = v
        else:
            problems.append(f"directives.{k}: {v!r} is not a known policy; dropped")

    until, reason = None, ""
    ho = table("hands_off")
    if "until" in ho:
        until = _parse_when(ho["until"])
        if until is None:
            problems.append(f"hands_off.until: {ho['until']!r} is not an ISO-8601 time with zone; dropped")
        else:
            r = ho.get("reason", "")
            reason = r if isinstance(r, str) else ""

    pool = table("pool")
    pool_share = None
    if "share" in pool:
        n = _num(pool["share"])
        if n is None or not (0 <= n <= 1):
            problems.append(f"pool.share: {pool['share']!r} is not within 0..1; dropped")
        else:
            pool_share = n
    expect = None
    if "expect_report" in pool:
        if isinstance(pool["expect_report"], bool):
            expect = pool["expect_report"]
        else:
            problems.append("pool.expect_report: not a boolean; dropped")

    return StorageConfig(levels, shares, directives, until, reason,
                         pool_share, expect, tuple(problems), horizon_h)


def hands_off_active(cfg: StorageConfig, now: datetime) -> bool:
    return cfg.hands_off_until is not None and now < cfg.hands_off_until


def _key(k: str) -> str:
    return k if _BARE.match(k) else '"' + k.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _str(s: str) -> str:
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    out = "".join(c if ord(c) >= 0x20 and ord(c) != 0x7F else "\\u%04x" % ord(c) for c in out)
    return f'"{out}"'


def render_storage_toml(cfg: StorageConfig) -> str:
    lv = cfg.levels
    out = ["[levels]"]
    out += [f"{k} = {getattr(lv, k)!r}" for k in _LEVEL_KEYS]
    out.append(f"horizon_h = {float(cfg.horizon_h)!r}")
    if cfg.shares:
        out += ["", "[shares]"] + [f"{_key(k)} = {float(v)!r}" for k, v in sorted(cfg.shares.items())]
    if cfg.directives:
        out += ["", "[directives]"] + [f"{_key(k)} = {_str(v)}" for k, v in sorted(cfg.directives.items())]
    if cfg.hands_off_until is not None:
        u = cfg.hands_off_until.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        out += ["", "[hands_off]", f'until = "{u}"', f"reason = {_str(cfg.hands_off_reason)}"]
    if cfg.pool_share is not None or cfg.expect_host_report is not None:
        out += ["", "[pool]"]
        if cfg.pool_share is not None:
            out.append(f"share = {float(cfg.pool_share)!r}")
        if cfg.expect_host_report is not None:
            out.append(f"expect_report = {'true' if cfg.expect_host_report else 'false'}")
    return "\n".join(out) + "\n"


def with_share(cfg: StorageConfig, client: str, weight: Optional[float]) -> StorageConfig:
    shares = dict(cfg.shares)
    if weight is None:
        shares.pop(client, None)
    else:
        shares[client] = float(weight)
    return replace(cfg, shares=shares)


def with_directive(cfg: StorageConfig, client: str, policy: Optional[str]) -> StorageConfig:
    d = dict(cfg.directives)
    if policy is None:
        d.pop(client, None)
    else:
        if not re.match(POLICY_RE, policy):
            raise ValueError(f"unknown policy {policy!r}")
        d[client] = policy
    return replace(cfg, directives=d)


def with_hands_off(cfg: StorageConfig, until: Optional[datetime], reason: str,
                   now: datetime) -> StorageConfig:
    if until is None:
        return replace(cfg, hands_off_until=None, hands_off_reason="")
    if until.tzinfo is None:
        raise ValueError("hands-off time needs a timezone")
    if until - now > MAX_HANDS_OFF:
        raise ValueError("hands-off may not run past 14 days")
    until = until.astimezone(timezone.utc).replace(microsecond=0)
    return replace(cfg, hands_off_until=until, hands_off_reason=reason)
