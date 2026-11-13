"""Per-instance upload enablement — the shared source of truth for which
env flag turns a client's upstream upload on, used by both the
``smd config upload`` command and harmonize's ``rule_upload_enabled``.

Identity is owned by site-profile.toml / ``smd config render`` and
credentials by ``smd admin secrets`` — this module only owns the
per-instance ENABLE flag (the otherwise-invisible decode->upload gap).
"""
from __future__ import annotations

import pwd
import sys
from pathlib import Path
from typing import List, Tuple

from .safe_write import rewrite_owned
from .instance import (
    InvalidReporterId, instance_key, legacy_instance_hint, parse_user_reporter_id,
    unescape_systemd_instance,
)

# client -> (enable-flag env var, [destination labels]).  Adding a row here
# wires the client into `smd config upload` AND rule_upload_enabled.
UPLOAD_ENABLE: dict[str, Tuple[str, List[str]]] = {
    "wspr-recorder":  ("WSPR_USE_HS_UPLOADER",
                       ["wsprdaemon.org (forwards to wsprnet.org)"]),
    "psk-recorder":   ("PSK_USE_HS_UPLOADER",
                       ["wsprdaemon.org (forwards to pskreporter.info)"]),
    "meteor-scatter": ("METEOR_SCATTER_USE_HS_UPLOADER",
                       ["wsprdaemon.org (forwards to pskreporter.info)"]),
}

# The standard: every spot goes to wsprdaemon.org, which forwards copies
# under the spot's own reporter id.  Enabling therefore writes the server
# route and nothing else.  psk-recorder's PSK_DELIVERY_PIPELINES takes
# ``server-merge``; meteor-scatter's METEOR_SCATTER_DELIVERY_MODE takes
# ``deposit`` (the rows ride the wsprdaemon tar flagged for the server's
# forwarder).  The direct routes, which post from this station, have no
# lever here.  wspr-recorder needs no pipeline selection.
# client -> (env var, route written on enable, valid choices).
DELIVERY_ON_ENABLE: dict[str, Tuple[str, "str | None", List[str]]] = {
    "psk-recorder": ("PSK_DELIVERY_PIPELINES", "server-merge", ["server-merge"]),
    "meteor-scatter": ("METEOR_SCATTER_DELIVERY_MODE", "deposit", ["deposit"]),
}

# --via values that would post from this station.  Refused before any write.
REFUSED_VIA: dict[str, Tuple[str, ...]] = {
    "psk-recorder": ("direct", "server-raw", "both"),
    "meteor-scatter": ("direct", "off"),
}

# client -> (env var, value) that `--on` writes.
STANDARD_ROUTE: dict[str, Tuple[str, str]] = {
    "psk-recorder": ("PSK_DELIVERY_PIPELINES", "server-merge"),
    "meteor-scatter": ("METEOR_SCATTER_DELIVERY_MODE", "deposit"),
}

STANDARD_NOTE = ("every spot goes to wsprdaemon.org, which forwards it to "
                 "wsprnet.org and pskreporter.info; no station posts to "
                 "them itself")


class DirectRouteRefused(ValueError):
    """A --via value that would post from this station."""


def env_path_for(client: str, instance: str, base: str = "/etc") -> Path:
    """The per-instance env file for a reporter id typed in any spelling.

    AC0G/S, AC0G=S, AC0G\\x3dS and ac0g/s all name
    <base>/<client>/env/AC0G=S.env.  One fallback, for a host that still
    carries an instance under a legacy radiod-keyed name: when that file
    is absent (or the text is no reporter id, like bee1-status.local) and
    a file named exactly as typed exists (unescaped, case kept), use it
    and print one line saying so.  Never invents a second spelling: a
    missing file is created under the storage form.  Raises
    InvalidReporterId (a ValueError) when the text is no reporter id and
    no such legacy file exists.
    """
    env_dir = Path(base) / client / "env"
    exact = unescape_systemd_instance(instance.strip())
    legacy = env_dir / f"{exact}.env" if exact and "/" not in exact else None
    try:
        storage = parse_user_reporter_id(instance)
    except InvalidReporterId:
        if legacy is not None and legacy.exists():
            print(legacy_instance_hint(exact), file=sys.stderr)
            return legacy
        raise
    canonical = env_dir / f"{instance_key(storage)}.env"     # files carry the key
    pre_key = env_dir / f"{storage}.env"     # AC0G=S.env, not yet migrated
    if not canonical.exists() and pre_key != canonical and pre_key.exists():
        return pre_key
    if (not canonical.exists() and exact != storage
            and legacy is not None and legacy.exists()):
        print(legacy_instance_hint(exact), file=sys.stderr)
        return legacy
    return canonical


def set_env_flag(path: Path, key: str, value: str) -> None:
    """Set ``key=value`` in an env file, preserving every other line
    (comments, ordering, the decode flag).  Creates the file if absent.

    The env directory belongs to the service account, so the read and the
    write go through ``safe_write.rewrite_owned`` (plan Ruling 17): one
    directory descriptor for both.  An existing file keeps its owner and
    mode, so a root:root 0600 file never passes to the service account at
    0644 (systemd reads EnvironmentFile as root; no service needs to read
    its own env file).  A new file goes to the directory's owner at 0644,
    by descriptor.  A symlink, a hard link or (as
    root) a stranger's file at the env file raises OSError; a symlink is
    refused rather than replaced, since the service can re-arm its own
    lever anyway and replacing it would read its target or drop the
    service's settings unseen."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def edit(old):
        lines = old.splitlines() if old is not None else []
        out, found = [], False
        for ln in lines:
            s = ln.strip()
            if s and not s.startswith("#") and "=" in s \
                    and s.split("=", 1)[0].strip() == key:
                out.append(f"{key}={value}")
                found = True
            else:
                out.append(ln)
        if not found:
            out.append(f"{key}={value}")
        return "\n".join(out) + "\n"
    rewrite_owned(path, edit, mode=None, keep_owner=True)


def apply_enable(client: str, instance: str, on: bool, base: str = "/etc",
                 delivery: "str | None" = None):
    """Flip the upload enable flag for one instance.  Returns
    (env_path, flag, destinations, delivery_set) where delivery_set is
    ``(env_key, value)`` if a delivery pipeline was also written, else None.

    When enabling a client that needs a delivery-pipeline selection (see
    DELIVERY_ON_ENABLE), the pipeline is set to the standard server route.
    Disabling leaves the pipeline untouched.  Raises KeyError for an unknown
    client, DirectRouteRefused for a direct ``delivery`` (nothing written),
    ValueError for any other invalid ``delivery`` choice."""
    if client not in UPLOAD_ENABLE:
        raise KeyError(client)
    if delivery in REFUSED_VIA.get(client, ()):
        raise DirectRouteRefused(
            f"--via {delivery} would post from this station; "
            f"{STANDARD_NOTE}. Use --on alone.")
    flag, dests = UPLOAD_ENABLE[client]
    path = env_path_for(client, instance, base=base)
    set_env_flag(path, flag, "1" if on else "0")
    delivery_set = None
    if on and client in DELIVERY_ON_ENABLE:
        dkey, ddefault, dchoices = DELIVERY_ON_ENABLE[client]
        val = delivery or ddefault
        if val is not None:
            if val not in dchoices:
                raise ValueError(
                    f"{dkey} must be one of {', '.join(dchoices)}; got {val!r}")
            set_env_flag(path, dkey, val)
            delivery_set = (dkey, val)
    elif delivery is not None:
        # --via passed for a client with no pipeline knob (or while disabling).
        raise ValueError(
            f"{client} has no delivery-pipeline selection"
            if client not in DELIVERY_ON_ENABLE else
            "delivery pipeline only applies when enabling (--on)")
    return path, flag, dests, delivery_set


def is_truthy(v) -> bool:
    return v is not None and str(v).strip().lower() not in (
        "", "0", "false", "no", "off")


def read_flag(path: Path, key: str):
    """Current value of ``key`` in an env file, or None if unset/missing."""
    try:
        for ln in Path(path).read_text().splitlines():
            s = ln.strip()
            if s and not s.startswith("#") and "=" in s \
                    and s.split("=", 1)[0].strip() == key:
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None
