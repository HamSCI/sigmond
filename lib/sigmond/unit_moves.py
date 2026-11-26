"""Unit moves: a component takes over a systemd unit another component ran.

A gaining component declares the move in its deploy.toml::

    [[systemd.moves]]
    from    = ["timestd-vtec.service"]          # a string or a list
    to      = "hamsci-physics-vtec.service"     # must sit in [systemd].units
    migrate = ["/opt/git/sigmond/hamsci-physics/venv/bin/hamsci-physics", "vtec", "adopt"]

``apply_move`` carries one move out on a station, in this order:

1. migrate  -- the gaining component copies the old unit's configuration.
               A non-zero exit stops the move here, with the old unit still
               running: the station keeps its product while someone looks.
2. stop     -- every old unit that runs.
3. disable  -- every old unit that is enabled, then clear what ``disable``
               cannot: the wants-links and the unit entry of an old unit
               whose file already left with its old checkout.
4. enable   -- the new unit; start it only when an old unit ran.

The old unit stops before the new one starts, so two copies never run.  Some
of these services read a serial receiver, and a serial port tolerates one
reader: two copies would fight over it.

A move whose old units are neither enabled nor active reports "unchanged",
so a second run, and every station that never ran the old unit, changes
nothing.  Every process runs through an injected ``run`` (a subprocess.run
stand-in) as an argv list, never through a shell.  Stdlib only.
"""
from __future__ import annotations

import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from sigmond.align_apply import Step

SYSTEMCTL_TIMEOUT = 120
MIGRATE_TIMEOUT = 600
# A unit-file symlink into a sigmond checkout belongs to sigmond, so a move
# may clear it even while it still resolves.
CHECKOUT_ROOT = "/opt/git/sigmond/"

_ACTIVE = ("active", "activating", "reloading")


@dataclass(frozen=True)
class UnitMove:
    component: str
    from_units: tuple
    to_unit: str
    migrate: tuple = ()


def parse_moves(component: str, deploy: dict) -> list:
    """The ``[[systemd.moves]]`` of one component's deploy.toml.

    Raises ValueError, naming the component, for a move without ``from`` or
    ``to``, a ``to`` listed in its own ``from``, or a ``to`` the component
    does not list in ``[systemd].units``.
    """
    systemd = deploy.get("systemd") or {}
    units = set(systemd.get("units") or ())
    moves = []
    for i, raw in enumerate(systemd.get("moves") or ()):
        where = f"{component}: deploy.toml [[systemd.moves]] #{i + 1}"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} is not a table")
        src = raw.get("from")
        to = raw.get("to")
        if isinstance(src, str):
            src = (src,)
        if not src or not all(isinstance(u, str) and u for u in src):
            raise ValueError(f"{where} names no 'from' unit")
        if not isinstance(to, str) or not to:
            raise ValueError(f"{where} names no 'to' unit")
        if to in src:
            raise ValueError(f"{where} moves {to} onto itself")
        if to not in units:
            raise ValueError(f"{where} moves to {to}, which {component} "
                             "does not list in [systemd].units")
        migrate = raw.get("migrate") or ()
        if isinstance(migrate, str) or not all(isinstance(a, str) for a in migrate):
            raise ValueError(f"{where} 'migrate' must be a list of strings (an argv)")
        moves.append(UnitMove(component, tuple(src), to, tuple(migrate)))
    return moves


def declared_moves(base: Path, names: Iterable[str]) -> list:
    """The moves each ``<base>/<name>/deploy.toml`` declares.

    An unreadable or unparsable file yields none; a malformed move still
    raises (parse_moves), because a bad declaration must not pass silently.
    """
    moves = []
    for name in names:
        try:
            with open(Path(base) / name / "deploy.toml", "rb") as fh:
                deploy = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        moves.extend(parse_moves(name, deploy))
    return moves


def declared_moves_at(repo: Path, component: str, commit: str,
                      run: Callable = subprocess.run) -> list:
    """The moves ``deploy.toml`` declares at ``commit`` in ``repo``."""
    try:
        cp = run(["git", "-C", str(repo), "show", f"{commit}:deploy.toml"],
                 capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    if cp.returncode != 0:
        return []
    try:
        deploy = tomllib.loads(cp.stdout)
    except tomllib.TOMLDecodeError:
        return []
    return parse_moves(component, deploy)


def _systemctl(run: Callable, *args: str):
    """Run systemctl; a process that cannot start reads as exit 1, no output."""
    argv = ["systemctl", *args]
    try:
        return run(argv, capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(argv, 1, "", str(e))


def _wants_links(unit: str, unit_dir: Path) -> list:
    """Every ``<unit_dir>/*.wants/<unit>``, dangling links included (a glob
    on the full pattern skips a link whose target is gone)."""
    links = []
    for wants in sorted(Path(unit_dir).glob("*.wants")):
        p = wants / unit
        if p.is_symlink() or p.exists():
            links.append(p)
    return links


def unit_state(unit: str, *, unit_dir: Path, run: Callable) -> tuple:
    """``(enabled, active)`` for one unit.

    A wants-link counts as enabled: a unit whose file vanished with its
    checkout still has one, and ``systemctl is-enabled`` then prints nothing.
    """
    word = (_systemctl(run, "is-enabled", unit).stdout or "").strip()
    enabled = word.startswith("enabled") or bool(_wants_links(unit, unit_dir))
    state = (_systemctl(run, "is-active", unit).stdout or "").strip()
    return enabled, state in _ACTIVE


def _clear_unit(unit: str, unit_dir: Path, backup_dir: Path, now: datetime) -> None:
    """Remove what ``systemctl disable`` leaves of an old unit."""
    for link in _wants_links(unit, unit_dir):
        if link.is_symlink():
            link.unlink()
    entry = Path(unit_dir) / unit
    if entry.is_symlink():
        target = str(entry.readlink())
        if not entry.exists() or target.startswith(CHECKOUT_ROOT):
            entry.unlink()
    elif entry.is_file():
        stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        Path(backup_dir).mkdir(parents=True, exist_ok=True)
        shutil.move(str(entry), str(Path(backup_dir) / f"{unit}.{stamp}"))


def _after_state(text: str) -> tuple:
    """(ActiveState, ConditionResult) from ``systemctl show --value``.

    The two value sets do not overlap, so the order systemctl prints them in
    does not matter.
    """
    active, condition = "", ""
    for line in (text or "").split():
        if line in ("yes", "no"):
            condition = line
        else:
            active = line
    return active, condition


def apply_move(move: UnitMove, *, apply: bool, owner_enabled: bool, unit_dir: Path,
               backup_dir: Path, run: Callable, now: datetime) -> Step:
    """Carry out one move; see the module docstring for the order."""
    label = f"{move.component}: {', '.join(move.from_units)} -> {move.to_unit}"
    states = {u: unit_state(u, unit_dir=unit_dir, run=run) for u in move.from_units}
    live = [u for u, (en, act) in states.items() if en or act]
    if not live:
        return Step(label, "unchanged", "")
    old = ", ".join(live)
    if not owner_enabled:
        return Step(label, "ran",
                    f"held: topology disables {move.component}; {old} keeps running")
    running = [u for u in live if states[u][1]]
    if not apply:
        plan = []
        if move.migrate:
            plan.append(f"run {' '.join(move.migrate)}")
        plan.append(f"stop and disable {old}")
        plan.append(f"enable{' and start' if running else ''} {move.to_unit}")
        return Step(label, "would move", "; ".join(plan))

    if move.migrate:
        argv = list(move.migrate)
        try:
            cp = run(argv, capture_output=True, text=True, timeout=MIGRATE_TIMEOUT)
            rc = cp.returncode
        except (OSError, subprocess.SubprocessError) as e:
            rc = f"error ({e})"
        if rc != 0:
            return Step(label, "failed", f"{' '.join(argv)} exit {rc}; {old} left running")

    for unit in move.from_units:   # every old unit: a timer's .service link goes too
        enabled, active = states[unit]
        if active:
            cp = _systemctl(run, "stop", unit)
            if cp.returncode != 0:
                return Step(label, "failed",
                            f"systemctl stop {unit} exit {cp.returncode}; "
                            f"{move.to_unit} not started")
        if enabled:
            _systemctl(run, "disable", unit)   # a dangling unit fails here; cleared below
        _clear_unit(unit, unit_dir, backup_dir, now)
    _systemctl(run, "daemon-reload")

    _systemctl(run, "enable", move.to_unit)
    if running:
        _systemctl(run, "start", move.to_unit)
    shown = _systemctl(run, "show", "-p", "ActiveState,ConditionResult", "--value",
                       move.to_unit)
    active, condition = _after_state(shown.stdout)
    if active == "active":
        return Step(label, "moved", f"{old} stopped; {move.to_unit} active")
    if active == "inactive" and condition == "no":
        return Step(label, "moved", f"{move.to_unit} waits for its condition")
    if active == "inactive" and not running:
        # Nothing ran before the move (an enabled timer between firings, or
        # a stopped service), so nothing should run now: enabled is the goal.
        return Step(label, "moved", f"{old} disabled; {move.to_unit} enabled")
    return Step(label, "failed",
                f"{old} stopped but {move.to_unit} is {active or 'unknown'}: "
                "nothing runs this job now")
