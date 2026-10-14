"""Per-instance upload enablement — the shared source of truth for which
env flag turns a client's upstream upload on, used by both the
``smd config upload`` command and harmonize's ``rule_upload_enabled``.

Identity is owned by site-profile.toml / ``smd config render`` and
credentials by ``smd admin secrets`` — this module only owns the
per-instance ENABLE flag (the otherwise-invisible decode->upload gap).
"""
from __future__ import annotations

import os
import pwd
import sys
from pathlib import Path
from typing import List, Tuple

from .instance import (
    InvalidReporterId, legacy_instance_hint, parse_user_reporter_id,
    unescape_systemd_instance,
)

# client -> (enable-flag env var, [destination labels]).  Adding a row here
# wires the client into `smd config upload` AND rule_upload_enabled.
UPLOAD_ENABLE: dict[str, Tuple[str, List[str]]] = {
    "wspr-recorder":  ("WSPR_USE_HS_UPLOADER",
                       ["wsprnet.org", "wsprdaemon.org"]),
    "psk-recorder":   ("PSK_USE_HS_UPLOADER", ["pskreporter.info"]),
    "meteor-scatter": ("METEOR_SCATTER_USE_HS_UPLOADER", ["pskreporter.info"]),
}

# Clients whose upload needs a delivery-pipeline selection, not just the
# boolean enable flag.  psk-recorder's default pipeline is ``server-merge``,
# which ships every row to a wsprdaemon server that forwards to
# pskreporter.info on the node's behalf — so on a standalone node (no
# wsprdaemon-server SFTP) the boolean alone delivers nothing.  ``direct`` is
# the standalone-correct lever: the client POSTs straight to pskreporter.info.
# Enabling sets the default; merge-fleet nodes override (`--via server-merge`).
# meteor-scatter is direct-only by design (the knob was removed), and
# wspr-recorder's wsprnet path needs no pipeline selection — so neither
# appears here.  client -> (env var, default pipeline, valid choices).
DELIVERY_ON_ENABLE: dict[str, Tuple[str, str, List[str]]] = {
    "psk-recorder": ("PSK_DELIVERY_PIPELINES", "direct",
                     ["direct", "server-merge", "server-raw"]),
}


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
    canonical = env_dir / f"{storage}.env"
    if (not canonical.exists() and exact != storage
            and legacy is not None and legacy.exists()):
        print(legacy_instance_hint(exact), file=sys.stderr)
        return legacy
    return canonical


def set_env_flag(path: Path, key: str, value: str) -> None:
    """Set ``key=value`` in an env file, preserving every other line
    (comments, ordering, the decode flag).  Creates the file if absent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
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
    path.write_text("\n".join(out) + "\n")


def _chown_to_env_dir_owner(path: Path) -> None:
    """chown the env file to the env dir's owner (the service user) so the
    daemon can read it; no-op if not root / dir missing."""
    try:
        st = path.parent.stat()
        os.chown(path, st.st_uid, st.st_gid)
        os.chmod(path, 0o644)
    except (PermissionError, FileNotFoundError):
        pass


def apply_enable(client: str, instance: str, on: bool, base: str = "/etc",
                 delivery: "str | None" = None):
    """Flip the upload enable flag for one instance.  Returns
    (env_path, flag, destinations, delivery_set) where delivery_set is
    ``(env_key, value)`` if a delivery pipeline was also written, else None.

    When enabling a client that needs a delivery-pipeline selection (see
    DELIVERY_ON_ENABLE), the pipeline is set to ``delivery`` or the client's
    standalone-correct default — so the boolean flag alone never leaves the
    node routing to an unreachable path.  Disabling leaves the pipeline
    untouched.  Raises KeyError for an unknown client, ValueError for an
    invalid ``delivery`` choice."""
    if client not in UPLOAD_ENABLE:
        raise KeyError(client)
    flag, dests = UPLOAD_ENABLE[client]
    path = env_path_for(client, instance, base=base)
    set_env_flag(path, flag, "1" if on else "0")
    delivery_set = None
    if on and client in DELIVERY_ON_ENABLE:
        dkey, ddefault, dchoices = DELIVERY_ON_ENABLE[client]
        val = delivery or ddefault
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
    _chown_to_env_dir_owner(path)
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
