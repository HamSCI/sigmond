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

import re
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
# The values of the three properties apply_move reads back; the sets do not
# overlap, so a value names its property whatever order systemctl prints.
_ACTIVE_STATES = {"active", "reloading", "inactive", "failed", "activating",
                  "deactivating", "maintenance", "refreshing"}
_LOAD_STATES = {"stub", "loaded", "not-found", "bad-setting", "error", "merged", "masked"}
# A unit name becomes a path under unit_dir and an argv word for systemctl,
# as root: no "/", no leading "-" or ".", and a .service or .timer suffix.
_UNIT_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_:.@-]*\.(service|timer)")


@dataclass(frozen=True)
class UnitMove:
    component: str
    from_units: tuple
    to_unit: str
    migrate: tuple = ()


def parse_moves(component: str, deploy: dict) -> list:
    """The ``[[systemd.moves]]`` of one component's deploy.toml.

    Raises ValueError, naming the component, for a move without ``from`` or
    ``to``, a unit name that could escape the unit directory or reach
    systemctl as an option, a ``to`` listed in its own ``from``, or a ``to``
    the component does not list in ``[systemd].units``.
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
        for name in (*src, to):
            if not _UNIT_NAME.fullmatch(name):
                raise ValueError(f"{where} names {name!r}, not a unit name "
                                 "(no '/', no leading '-' or '.', .service or .timer)")
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
    """(ActiveState, ConditionResult, LoadState) from ``systemctl show --value``.

    The value sets do not overlap, so the order systemctl prints them in
    does not matter.
    """
    active, condition, load = "", "", ""
    for word in (text or "").split():
        if word in ("yes", "no"):
            condition = word
        elif word in _LOAD_STATES:
            load = word
        elif word in _ACTIVE_STATES:
            active = word
    return active, condition, load


def _last_line(cp) -> str:
    """The last non-empty line of a process's stderr, else its stdout."""
    for text in (getattr(cp, "stderr", ""), getattr(cp, "stdout", "")):
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        if lines:
            return lines[-1][:200]
    return ""


def apply_move(move: UnitMove, *, apply: bool, owner_enabled: bool, unit_dir: Path,
               backup_dir: Path, run: Callable, now: datetime) -> Step:
    """Carry out one move; see the module docstring for the order."""
    label = _label(move)
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
        why = ""
        try:
            cp = run(argv, capture_output=True, text=True, timeout=MIGRATE_TIMEOUT)
            rc = cp.returncode
            why = _last_line(cp)
        except (OSError, subprocess.SubprocessError) as e:
            rc = f"error ({e})"
        if rc != 0:
            return Step(label, "failed", f"{' '.join(argv)} exit {rc}"
                        + (f" ({why})" if why else "") + f"; {old} left running")

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
    # From here on the old units are stopped and cleared: a failure names
    # both units, because nothing runs this job until someone looks.
    def broken(what: str) -> Step:
        return Step(label, "failed",
                    f"{what}; {old} already stopped and cleared, so nothing runs "
                    f"this job: run 'systemctl status {move.to_unit}'")

    verbs = [("daemon-reload",), ("enable", move.to_unit)]
    if running:
        verbs.append(("start", move.to_unit))
    for verb in verbs:
        cp = _systemctl(run, *verb)
        if cp.returncode != 0:
            tail = _last_line(cp)
            return broken(f"systemctl {' '.join(verb)} exit {cp.returncode}"
                          + (f" ({tail})" if tail else ""))
    shown = _systemctl(run, "show", "-p", "ActiveState,ConditionResult,LoadState",
                       "--value", move.to_unit)
    active, condition, load = _after_state(shown.stdout)
    if load != "loaded":
        # A missing unit also reads inactive with ConditionResult=no.
        return broken(f"{move.to_unit} LoadState={load or 'unknown'}")
    if active == "active":
        return Step(label, "moved", f"{old} stopped; {move.to_unit} active")
    if active == "inactive" and condition == "no":
        return Step(label, "moved", f"{move.to_unit} waits for its condition")
    if active == "inactive" and not running:
        # Nothing ran before the move (an enabled timer between firings, or
        # a stopped service), so nothing should run now: enabled is the goal.
        return Step(label, "moved", f"{old} disabled; {move.to_unit} enabled")
    return broken(f"{move.to_unit} is {active or 'unknown'}")


def _label(move: UnitMove) -> str:
    return f"{move.component}: {', '.join(move.from_units)} -> {move.to_unit}"


def hold_move(move: UnitMove, reason: str, *, unit_dir: Path, run: Callable) -> Step:
    """The Step for a move sigmond holds back: "unchanged" when no old unit
    is enabled or active, else "ran" with a ``held:`` line that names why.
    It reads systemctl and changes nothing."""
    live = [u for u in move.from_units if any(unit_state(u, unit_dir=unit_dir, run=run))]
    if not live:
        return Step(_label(move), "unchanged", "")
    return Step(_label(move), "ran", f"held: {reason}; {', '.join(live)} keeps running")


SYSTEM_UNIT_DIR = Path("/etc/systemd/system")


def move_units(move: UnitMove) -> tuple:
    """The new unit, and the .service a .timer starts."""
    if move.to_unit.endswith(".timer"):
        return (move.to_unit, move.to_unit[:-len(".timer")] + ".service")
    return (move.to_unit,)


def _link(entry: Path, src: Path) -> bool:
    """Point ``entry`` at ``src``; leave a regular file alone.  True when
    ``entry`` now resolves to ``src``."""
    if entry.is_symlink() and entry.resolve() == src:
        return True
    if entry.exists() and not entry.is_symlink():
        return False
    if entry.is_symlink():
        entry.unlink()
    entry.symlink_to(src)
    return True


def link_units(repo: Path, units: Iterable[str], *, unit_dir: Path) -> list:
    """Link ``units`` the way ``installer.apply_deploy_toml_links`` links them.

    First the explicit steps: a ``kind = "link"`` step whose ``dst`` sits in
    /etc/systemd/system and names one of ``units`` becomes
    ``<unit_dir>/<unit>`` -> ``<repo>/<src>``.  Then, like the installer, a
    unit no step names links from ``<repo>/systemd/<unit>`` when that file
    exists (hamsci-physics declares no unit link steps).  It replaces a
    missing entry or a symlink that points elsewhere, and leaves a regular
    file alone.  Returns the units now linked into the repo."""
    repo = Path(repo)
    try:
        with open(repo / "deploy.toml", "rb") as fh:
            deploy = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        deploy = {}
    wanted = set(units)
    linked = []
    stepped = set()
    for step in (deploy.get("install") or {}).get("steps") or ():
        if not isinstance(step, dict) or step.get("kind") != "link":
            continue
        dst = Path(str(step.get("dst") or ""))
        if dst.parent != SYSTEM_UNIT_DIR or dst.name not in wanted:
            continue
        src = (repo / str(step.get("src") or "")).resolve()
        if not step.get("src") or not src.is_file():
            continue
        stepped.add(dst.name)
        if _link(Path(unit_dir) / dst.name, src):
            linked.append(dst.name)
    for unit in sorted(wanted - stepped):
        shipped = repo / "systemd" / unit
        if not shipped.is_file():
            continue
        if _link(Path(unit_dir) / unit, shipped.resolve()):
            linked.append(unit)
    return linked


def drift_items(base: Path, enabled, *, unit_dir: Path,
                is_enabled: Callable[[str], bool],
                service_active: Callable[[str], bool]) -> list:
    """Drift faults for a move left half done.

    For each move a component declares (a component ``enabled``, the
    topology map, names, or any checkout under ``base``), every old unit
    still enabled or active faults.  When topology
    enables the gaining component, align should have moved it; when topology
    disables it, align held the move, and enabling the component lets the
    next align finish it.  A wants-link counts as enabled (``unit_state``).
    """
    from sigmond.drift.model import Item
    out = []
    # Every checkout too: align reads them, and holds the move of an owner
    # topology does not name.
    try:
        checkouts = {p.name for p in Path(base).iterdir() if (p / "deploy.toml").is_file()}
    except OSError:
        checkouts = set()
    for move in declared_moves(base, sorted(set(enabled) | checkouts)):
        for old in move.from_units:
            if not (is_enabled(old) or bool(_wants_links(old, unit_dir))
                    or service_active(old)):
                continue
            if enabled.get(move.component):
                out.append(Item("unit", "fault", old,
                                f"{old} still runs; {move.component} took it over "
                                f"as {move.to_unit}", "smd align --apply"))
            else:
                how = "disables" if move.component in enabled else "does not name"
                out.append(Item("unit", "fault", old,
                                f"{old} moved to {move.to_unit} in {move.component}, "
                                f"which topology {how} here",
                                f"smd enable {move.component}"))
    return out
