"""Drift items for the unit kind.

* every unit a running component's deploy.toml declares must exist in the
  unit directory; a missing template (``x@.service``) is a fault only
  while an instance of it is enabled or active (a second radiod's
  ka9q-web@<id>); a listed timer must be enabled.
* a component topology enables but whose main service is stopped (an
  operator stopped it on purpose, as KFS-OMNI does hf-timestd) raises no
  fault for its units, only one drift item naming the stopped service.
  ``component_running`` is the one place that rule lives.
* sigmond's own systemd/sigmond-*.{service,timer,path} must match the
  installed copies byte for byte.  Whether they are enabled stays out of
  scope: several install on purpose without enabling.
* a unit enabled for a component that topology disables is drift.

install_own_units copies sigmond's own units the way install.sh does, so
`smd align --apply` can land units added after a station's install.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from sigmond import component_state
from sigmond.drift.model import Item

_APPLY = "smd align --apply"
_OWN_GLOBS = ("sigmond-*.service", "sigmond-*.timer", "sigmond-*.path")

# The sigmond timers install.sh enables, and so `smd align --apply` enables
# where a station lacks one (a timer that arrived after its install).
# tests/test_align_enables_units.py parses install.sh and holds the two
# equal.  The rest stay off by design: heartbeat (fails until [heartbeat]
# is set), decode-health-collect (the operator's call), the per-client
# trims (trim-all covers them), the timing/radiod watchdogs and netguard.
INSTALL_ENABLED_TIMERS = (
    "sigmond-t6-stuck-watchdog.timer",
    "sigmond-mdns.timer",
    "sigmond-time-sync.timer",
    "sigmond-link.timer",
    "sigmond-service-events.timer",
    "sigmond-ts1-advertise.timer",
    "sigmond-link-rac.timer",
    "sigmond-sdr-recover.timer",
    "sigmond-storage-trim-all.timer",
    "sigmond-gap-hourly.timer",
    "sigmond-drift.timer",
    "sigmond-location-check.timer",
    "sigmond-timing-reconcile.timer",
    "sigmond-snapshot.timer",
    "sigmond-component-fetch.timer",
    "sigmond-storage.timer",
)
# `smd archive add` enables it, `smd archive remove --forget` disables it:
# align enables it only while /etc/sigmond/archive.toml names a drive.
ARCHIVE_HEALTH_TIMER = "sigmond-archive-health.timer"


def _declared(base: Path, name: str) -> list:
    """Unit names from deploy.toml [systemd] (units, then templated_units)."""
    deploy = None
    path = Path(base) / name / "deploy.toml"
    try:
        with open(path, "rb") as f:
            deploy = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        deploy = None
    if deploy is None:
        shim = component_state.fallback_shim_path(name)
        if shim is not None:
            try:
                with open(shim, "rb") as f:
                    deploy = tomllib.load(f)
            except (OSError, tomllib.TOMLDecodeError):
                deploy = None
    return component_state._systemd_unit_names(deploy or {})


declared_units = _declared


def main_service(units) -> "str | None":
    """The first declared non-template ``.service``, or None."""
    for u in units:
        if u.endswith(".service") and "@." not in u:
            return u
    return None


def component_running(units, enabled: bool,
                      service_active: Callable[[str], bool]) -> bool:
    """True when topology enables the component and its main service
    (``main_service(units)``) is active, or it declares no such service.

    A component that declares only templated units counts as running when
    enabled.  A declared main service that is missing reads as not running:
    this checks no unit file, so a caller that needs the install state
    checks existence itself."""
    if not enabled:
        return False
    svc = main_service(units)
    return svc is None or bool(service_active(svc))


def _exists(unit_dir: Path, unit: str) -> bool:
    p = Path(unit_dir) / unit
    return p.exists() or p.is_symlink()


def own_unit_files(sigmond_repo: Path) -> list:
    out = []
    for pat in _OWN_GLOBS:
        out.extend(sorted((Path(sigmond_repo) / "systemd").glob(pat)))
    return [p for p in out if p.is_file()]


def _differs(src: Path, dst: Path) -> bool:
    try:
        return dst.read_bytes() != src.read_bytes()
    except OSError:
        return True


def unit_items(base: Path, enabled: Mapping[str, bool], *,
               sigmond_repo: Path,
               unit_dir: Path = Path("/etc/systemd/system"),
               is_enabled: Callable[[str], bool],
               service_active: Callable[[str], bool],
               template_instances: Callable[[str], int]) -> list:
    out = []
    unit_dir = Path(unit_dir)
    for name in sorted(enabled):
        units = _declared(base, name)
        if enabled[name]:
            svc = main_service(units)
            # A stop is deliberate only when the service is installed; a
            # missing main service is a failed install and faults below.
            if (svc is not None and _exists(unit_dir, svc)
                    and not component_running(units, True, service_active)):
                out.append(Item("unit", "drift", svc,
                                f"{name} is enabled but {svc} is not "
                                "running", f"smd start {name}"))
                continue
            for u in units:
                if not _exists(unit_dir, u):
                    if "@." in u and template_instances(u) <= 0:
                        continue  # no instance needs the template
                    out.append(Item("unit", "fault", u,
                                    f"{name} declares {u}; not installed in "
                                    f"{unit_dir}", _APPLY))
                elif (u.endswith(".timer") and "@." not in u
                      and not is_enabled(u)):
                    out.append(Item("unit", "fault", u,
                                    f"{name} timer {u} is installed but not "
                                    "enabled", _APPLY))
        else:
            for u in units:
                if ("@." not in u and _exists(unit_dir, u)
                        and is_enabled(u)):
                    out.append(Item(
                        "unit", "drift", u,
                        f"{u} is enabled; topology disables {name}",
                        f"smd disable {name} (stops and disables its units)"))
    for src in own_unit_files(sigmond_repo):
        dst = unit_dir / src.name
        if not _exists(unit_dir, src.name):
            out.append(Item("unit", "fault", src.name,
                            f"sigmond unit {src.name} is not installed",
                            _APPLY))
        elif _differs(src, dst):
            out.append(Item("unit", "fault", src.name,
                            f"installed {src.name} differs from sigmond's "
                            "copy", _APPLY))
    return out


def excepted_units(exceptions_path, now) -> tuple:
    """(unit names a live drift exception covers, the read error or None).
    A live exception has not passed its ``until``; an expired test is the
    restore the fault asks for, so it covers nothing."""
    from sigmond.drift import exceptions as drift_exceptions
    path = Path(exceptions_path or drift_exceptions.DEFAULT_PATH)
    now = now or datetime.now(timezone.utc)
    excs, err = drift_exceptions.load_checked(path, now)
    today = now.astimezone(timezone.utc).date() if now.tzinfo else now.date()
    names = {e.key[len("unit:"):] for e in excs
             if e.key.startswith("unit:") and today <= e.until}
    return names, err


def install_own_units(sigmond_repo: Path, unit_dir: Path, *,
                      run=subprocess.run, exceptions_path=None, now=None,
                      say: Callable[[str], None] = print) -> list:
    """Copy missing or changed sigmond units into unit_dir (0644, atomic).

    Final review I3: a unit a drift exception covers (``unit:<name>``,
    standing or test, not yet ended) stays as it is, installed or not
    (one line through ``say``), and so does every
    installed unit that differs while the exceptions file cannot be read.
    An installed unit this replaces is kept first as
    ``<unit>.bak-drift-<UTC stamp>``; ``say`` gets one line per
    replacement.

    Enables and starts nothing.  Returns the names written.  ``run`` is
    kept for callers that inject a runner; the copy itself needs none.
    """
    unit_dir = Path(unit_dir)
    excepted, err = excepted_units(exceptions_path, now)
    stamp = (now or datetime.now(timezone.utc)).astimezone(
        timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if err is not None:
        say(f"  sigmond-units: {err}; no installed unit is replaced")
    wrote = []
    for src in own_unit_files(sigmond_repo):
        dst = unit_dir / src.name
        present = dst.is_file()
        if present and not _differs(src, dst):
            continue
        if src.name in excepted:
            # Missing or hand-edited on purpose: either way, left alone.
            say(f"  sigmond-units: left {src.name} as it is: a drift "
                f"exception covers unit:{src.name}")
            continue
        if present and err is not None:
            continue
        bak = None
        if present:
            bak = dst.with_name(f"{src.name}.bak-drift-{stamp}")
            shutil.copy2(dst, bak)
        fd, tmp = tempfile.mkstemp(dir=unit_dir, prefix=f".{src.name}.")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(src.read_bytes())
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o644)
            os.replace(tmp, dst)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        if bak is not None:
            say(f"  sigmond-units: replaced {src.name}; the old copy is "
                f"{bak}")
        wrote.append(src.name)
    return wrote
