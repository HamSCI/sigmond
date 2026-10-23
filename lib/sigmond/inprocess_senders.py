"""Which clients run their own in-process pskreporter sender (D13).

psk-recorder and meteor-scatter can each post spots to pskreporter.info from
their own process.  When one does, the hs-uploader daemon pipeline for that
producer would post the same spots a second time, so the manifest renderer
leaves that pipeline out (tasks/spec-key-split-v373.md §5.3).

:func:`inprocess_senders` reads each enabled instance's effective environment
and applies the client's own rule, copied from the client:

* psk-recorder (``core/recorder.py``, ``_resolve_delivery_pipelines``):
  ``direct`` in ``PSK_DELIVERY_PIPELINES`` (comma list; unknown tokens drop),
  else the legacy ``PSK_DELIVERY_MODE`` (``direct`` or ``both`` send), else
  the default ``server-merge``, which runs no sender.
* meteor-scatter (``core/recorder.py``, ``_delivery_mode`` and
  ``_start_uploaders``): ``METEOR_SCATTER_DELIVERY_MODE``, read as
  ``direct`` when unset or empty; ``off``, ``none``, ``disabled`` and
  ``deposit`` run no sender.  In ``direct`` mode it starts its sender only
  when ``[station] callsign`` and ``grid_square`` in its config are both
  set.  It finds that config the way ``config.resolve_config_path`` does:
  ``METEOR_SCATTER_CONFIG`` from the unit's environment, else
  ``/etc/meteor-scatter/<instance>.toml`` when it exists (the instance in
  storage form), else ``/etc/meteor-scatter/meteor-scatter-config.toml``.
  An instance with no callsign or grid posts nothing, so it must keep its
  daemon pipeline (final review M1).  A config that cannot be read counts
  as "cannot tell".

Any doubt keeps the daemon pipeline, because a double post costs less than a
silent loss: an unreadable environment, a missing unit or an instance that
does not send gives no pair for that client.  A client with several instances
gets a pair only when every instance sends in-process.

Stdlib only.
"""

from __future__ import annotations

import logging
import shlex
import tomllib
import subprocess
from pathlib import Path
from typing import Callable, Optional

from .harmonize import parse_env_text

logger = logging.getLogger(__name__)

DESTINATION = "pskreporter"
CLIENTS = ("psk-recorder", "meteor-scatter")

_PSK_VALID = ("direct", "server-merge", "server-raw")
_PSK_LEGACY = {
    "server": ("server-merge",),
    "direct": ("direct",),
    "both": ("direct", "server-raw"),
}
_MS_NO_SENDER = ("deposit", "off", "none", "disabled")
# meteor-scatter's config directory (its config.PER_INSTANCE_CONFIG_DIR).
MS_CONFIG_DIR = Path("/etc/meteor-scatter")


def _systemctl_show(unit: str, *, run=subprocess.run) -> dict:
    """The unit's effective environment: ``Environment=`` first, then each
    ``EnvironmentFiles=`` entry in order (systemd applies files after
    ``Environment=``, and a later file wins).  Raises ``OSError`` when
    systemctl fails or a file cannot be read."""
    cmd = ["systemctl", "show", "-p", "Environment", "-p", "EnvironmentFiles",
           unit]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=10)
    except subprocess.SubprocessError as exc:
        raise OSError(f"systemctl show {unit}: {exc}") from exc
    if r.returncode != 0:
        raise OSError(f"systemctl show {unit} exited {r.returncode}")
    env: dict = {}
    files: list = []
    key = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("Environment="):
            key, line = "Environment", line[len("Environment="):]
        elif line.startswith("EnvironmentFiles="):
            key, line = "EnvironmentFiles", line[len("EnvironmentFiles="):]
        if key == "Environment":
            try:
                parts = shlex.split(line)
            except ValueError as exc:
                raise OSError(f"cannot parse Environment of {unit}") from exc
            for part in parts:
                k, sep, v = part.partition("=")
                if sep:
                    env[k] = v
        elif key == "EnvironmentFiles" and line.strip():
            path, _, opts = line.strip().partition(" (")
            files.append((path, "ignore_errors=yes" in opts))
    for path, ignore in files:
        try:
            text = Path(path).read_text()
        except FileNotFoundError:
            if ignore:
                continue
            raise
        env.update(parse_env_text(text))
    return env


def _instance_units(client: str, topology) -> list:
    """The enabled client's instance service units (sigmond lifecycle's own
    enumeration; orphans skipped)."""
    from .lifecycle import resolve_units
    try:
        refs = resolve_units([client], topology.enabled_components())
    except Exception as exc:                               # noqa: BLE001
        raise OSError(f"cannot enumerate {client} units: {exc}") from exc
    return [u.unit for u in refs
            if u.kind == "service" and u.instance and not u.orphaned]


def _psk_delivery_pipelines(env: dict) -> tuple:
    """psk-recorder's delivery pipelines for ``env``, in canonical order
    (its ``_resolve_delivery_pipelines``): the valid tokens of
    ``PSK_DELIVERY_PIPELINES``, else the legacy ``PSK_DELIVERY_MODE``,
    else the default ``server-merge``.  The sink lever step reads this too
    (``sink_levers``): anything but ``server-raw`` alone sends or marks rows
    for a server to forward."""
    raw = (env.get("PSK_DELIVERY_PIPELINES") or "").strip().lower()
    if raw:
        valid = {t.strip() for t in raw.split(",")} & set(_PSK_VALID)
        if valid:
            return tuple(p for p in _PSK_VALID if p in valid)
    mode = (env.get("PSK_DELIVERY_MODE") or "").strip().lower()
    if mode in _PSK_LEGACY:
        return tuple(p for p in _PSK_VALID if p in _PSK_LEGACY[mode])
    return ("server-merge",)


def _psk_recorder_sends(env: dict, unit: str) -> bool:
    return "direct" in _psk_delivery_pipelines(env)


def _ms_config_path(env: dict, unit: str) -> Path:
    """The config file meteor-scatter's daemon loads for ``unit``."""
    from .instance import unescape_systemd_instance
    override = env.get("METEOR_SCATTER_CONFIG")
    if override:
        return Path(override)
    instance = unit.partition("@")[2]
    if instance.endswith(".service"):
        instance = instance[:-len(".service")]
    if instance:
        per_instance = MS_CONFIG_DIR / f"{unescape_systemd_instance(instance)}.toml"
        if per_instance.exists():
            return per_instance
    return MS_CONFIG_DIR / "meteor-scatter-config.toml"


def _ms_delivery_mode(env: dict) -> str:
    """meteor-scatter's delivery mode for ``env`` (its ``_delivery_mode``):
    ``METEOR_SCATTER_DELIVERY_MODE``, ``direct`` when unset or empty.  Any
    mode but ``off``, ``none``, ``disabled`` and ``deposit`` starts the
    in-process sender; ``deposit`` marks rows for a server to forward."""
    return (env.get("METEOR_SCATTER_DELIVERY_MODE") or "direct").strip().lower()


def _meteor_scatter_sends(env: dict, unit: str) -> bool:
    mode = _ms_delivery_mode(env)
    if mode in _MS_NO_SENDER:
        return False
    path = _ms_config_path(env, unit)
    try:
        config = tomllib.loads(path.read_bytes().decode("utf-8"))
    except (ValueError, OSError) as exc:   # TOMLDecodeError is a ValueError
        raise OSError(f"cannot read {path}: {exc}") from exc
    station = config.get("station", {})
    if not isinstance(station, dict):
        raise OSError(f"{path}: [station] is not a table")
    return bool(station.get("callsign")) and bool(station.get("grid_square"))


_RULES = {"psk-recorder": _psk_recorder_sends,
          "meteor-scatter": _meteor_scatter_sends}


def inprocess_senders(topology, *,
                      show: Callable[[str], dict] = _systemctl_show) -> set:
    """``{(producer, "pskreporter")}`` for each enabled client whose every
    instance runs its own pskreporter sender."""
    enabled = set(topology.enabled_components())
    pairs: set = set()
    for client in CLIENTS:
        if client not in enabled:
            continue
        try:
            units = _instance_units(client, topology)
            if not units:
                continue
            sends = [_RULES[client](show(u), u) for u in units]
        except OSError as exc:
            logger.warning("inprocess-senders: cannot read %s environment or "
                           "config (%s) — keeping its daemon pipeline",
                           client, exc)
            continue
        if all(sends):
            pairs.add((client, DESTINATION))
        elif any(sends):
            logger.warning("inprocess-senders: some %s instances send "
                           "in-process and some do not — keeping its daemon "
                           "pipeline", client)
    return pairs
