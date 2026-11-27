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
4. enable   -- the new unit; start it only when an old unit ran (active,
               activating, reloading, or failed: a unit that crashed ran
               the job too).  Clearing that raises after the old unit
               stopped still enables and starts the new unit, and the move
               then reads "failed", naming both units.

The old unit stops before the new one starts, so two copies never run.  Some
of these services read a serial receiver, and a serial port tolerates one
reader: two copies would fight over it.

Retired units: a move with no new owner.  A component declares, inside its
``[systemd]`` table and before any ``[[systemd.moves]]`` (TOML puts a key
written after a moves table into that move, and ``parse_moves`` refuses it
there)::

    [systemd]
    units         = [...]
    retired_units = ["timestd-prune.timer", "timestd-prune.service"]

and sigmond lists its own in ``RETIRED_OWN``.  ``apply_retire`` stops each
unit (timers before services, so a timer cannot start its service again),
disables it, then clears it by the move rule (``_clear_unit``) and runs one
daemon-reload.  It holds, and touches nothing, while the unit's entry is a
link that resolves outside a sigmond checkout: an operator's own unit of
that name.  ``drift_items`` faults a retired unit still enabled, active or
installed, and ``smd align`` retries a retire at every run.

A move whose old units are neither enabled nor active reports "unchanged"
(``in_service``: a failed unit that is disabled counts as off),
so a second run, and every station that never ran the old unit, changes
nothing.  Every process runs through an injected ``run`` (a subprocess.run
stand-in) as an argv list, never through a shell.  Stdlib only.
"""
from __future__ import annotations

import os
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
# What apply_move counts as "the old unit ran the job": a unit that crashed
# (failed, as ND's timestd-vtec did until 039bd554) ran it too, so the new
# unit starts in its place.
_RAN = _ACTIVE + ("failed",)


def in_service(enabled: bool, word: str) -> bool:
    """The one definition of "this unit still holds its job", shared by moves
    and retires: enabled (a wants-link counts), or active, activating or
    reloading.  A failed unit that is also disabled does not: nothing starts
    it again, so it reads as the operator's off.  A failed unit that is
    enabled does, and a move then starts the new unit in its place (_RAN).
    Retires differ in one way only: they also clear a unit whose file still
    sits in the unit directory (``retire_live``)."""
    return bool(enabled) or word in _ACTIVE
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
        if "retired_units" in raw:
            # TOML put a key written after this table into the move.
            raise ValueError(f"{where} holds retired_units: put retired_units "
                             "in [systemd], before the first [[systemd.moves]]")
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


def _unloaded(run: Callable, unit: str) -> bool:
    """systemd has dropped ``unit``: a release that deletes the file it
    retires leaves the disable's reload nothing to keep, and reset-failed on
    a unit no longer loaded exits 1 with nothing left to reset."""
    cp = _systemctl(run, "show", "-p", "LoadState", "--value", unit)
    return cp.returncode == 0 and (cp.stdout or "").strip() == "not-found"


def _wants_links(unit: str, unit_dir: Path) -> list:
    """Every ``<unit_dir>/*.wants/<unit>``, dangling links included (a glob
    on the full pattern skips a link whose target is gone)."""
    links = []
    for wants in sorted(Path(unit_dir).glob("*.wants")):
        p = wants / unit
        if p.is_symlink() or p.exists():
            links.append(p)
    return links


def _unit_words(unit: str, *, unit_dir: Path, run: Callable) -> tuple:
    """``(enabled, is-active word)`` for one unit."""
    word = (_systemctl(run, "is-enabled", unit).stdout or "").strip()
    enabled = word.startswith("enabled") or bool(_wants_links(unit, unit_dir))
    return enabled, (_systemctl(run, "is-active", unit).stdout or "").strip()


def unit_state(unit: str, *, unit_dir: Path, run: Callable) -> tuple:
    """``(enabled, active)`` for one unit.

    A wants-link counts as enabled: a unit whose file vanished with its
    checkout still has one, and ``systemctl is-enabled`` then prints nothing.
    """
    enabled, state = _unit_words(unit, unit_dir=unit_dir, run=run)
    return enabled, state in _ACTIVE


def _clear_unit(unit: str, unit_dir: Path, backup_dir: Path, now: datetime,
                checkout_root: str = CHECKOUT_ROOT) -> None:
    """Remove what ``systemctl disable`` leaves of an old unit: its
    wants-links, and its entry when that dangles or links into a sigmond
    checkout.  A regular file (an install.sh copy) goes to ``backup_dir``."""
    for link in _wants_links(unit, unit_dir):
        if link.is_symlink():
            link.unlink()
    entry = Path(unit_dir) / unit
    if entry.is_symlink():
        target = str(entry.readlink())
        if not entry.exists() or target.startswith(checkout_root):
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
    words = {u: _unit_words(u, unit_dir=unit_dir, run=run) for u in move.from_units}
    # (enabled, ran): among live units, a failed one counts as having run the job.
    states = {u: (en, word in _RAN) for u, (en, word) in words.items()}
    live = [u for u, (en, word) in words.items() if in_service(en, word)]
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

    unclear = []
    for unit in move.from_units:   # every old unit: a timer's .service link goes too
        enabled, active = states[unit]
        if active and unit in live:
            cp = _systemctl(run, "stop", unit)
            if cp.returncode != 0:
                return Step(label, "failed",
                            f"systemctl stop {unit} exit {cp.returncode}; "
                            f"{move.to_unit} not started")
        if enabled:
            _systemctl(run, "disable", unit)   # a dangling unit fails here; cleared below
        if unit in live:
            _systemctl(run, "reset-failed", unit)   # a crashed old unit stays "failed" otherwise
        try:
            _clear_unit(unit, unit_dir, backup_dir, now)
        except Exception as exc:                                  # noqa: BLE001
            # The old unit already stopped: bring the new one up all the
            # same, so the job does not go dark, and fail the move below.
            unclear.append(f"clearing {unit} raised {type(exc).__name__}: {exc}")
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
    if unclear:
        return Step(label, "failed",
                    f"{'; '.join(unclear)}; {old} stopped, {move.to_unit} "
                    f"{active or 'unknown'}: clear what is left of {old} by hand")
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
    live = [u for u in move.from_units
            if in_service(*_unit_words(u, unit_dir=unit_dir, run=run))]
    if not live:
        return Step(_label(move), "unchanged", "")
    return Step(_label(move), "ran", f"held: {reason}; {', '.join(live)} keeps running")


# --- retired units: a move with no new owner ---------------------------------

@dataclass(frozen=True)
class UnitRetire:
    """Units ``component`` no longer ships.  ``links`` (sigmond's own only)
    names helper links its install made, by absolute path: retiring the
    unit removes each one that still links into a sigmond checkout or
    dangles."""
    component: str
    units: tuple
    links: tuple = ()


SIGMOND = "sigmond"
# sigmond's own retired units, with the /usr/local/sbin links install.sh
# made for them.  A unit is recognised by its path in the unit directory and
# its place in this list, never by its source file: the release that
# retires a unit deletes that file from systemd/.
RETIRED_OWN: tuple = (
    # v3.96: sigmond-recorder-watchdog took over the T6-stuck rule.  install.sh
    # copied both units into /etc/systemd/system (they go to the backup
    # directory) and linked the helper into /usr/local/sbin.
    UnitRetire(SIGMOND, ("sigmond-t6-stuck-watchdog.timer",
                         "sigmond-t6-stuck-watchdog.service"),
               ("/usr/local/sbin/sigmond-t6-stuck-watchdog",)),
)
# A retire that did not finish.  Not "failed": that word holds the release's
# restarts and record (bin/smd _ALIGN_BLOCKING), which a retire, starting
# nothing, has no reason to do.  Align still exits 1 on it.
RETIRE_FAILED = "retire failed"


def parse_retired(component: str, deploy: dict):
    """The ``[systemd].retired_units`` of one deploy.toml, as a UnitRetire,
    or None when it names none.

    Raises ValueError, naming the component, for a value that is not a list
    of strings, a name that is not a plain .service or .timer (a template
    has no single unit to stop), or a unit the same file still lists in
    ``units`` or ``templated_units``."""
    systemd = deploy.get("systemd") or {}
    raw = systemd.get("retired_units")
    if raw is None:
        return None
    where = f"{component}: deploy.toml [systemd].retired_units"
    if not isinstance(raw, list) or not all(isinstance(u, str) for u in raw):
        raise ValueError(f"{where} must be a list of unit names")
    shipped = set(systemd.get("units") or ()) | set(systemd.get("templated_units") or ())
    for name in raw:
        if not _UNIT_NAME.fullmatch(name) or "@." in name:
            raise ValueError(f"{where} names {name!r}, not a unit name "
                             "(no '/', no leading '-' or '.', no template, "
                             ".service or .timer)")
        if name in shipped:
            raise ValueError(f"{where} retires {name}, which {component} still lists "
                             "in [systemd]")
    units = tuple(dict.fromkeys(raw))
    return UnitRetire(component, units) if units else None


def declared_retired(base: Path, names: Iterable[str]) -> list:
    """The UnitRetire each ``<base>/<name>/deploy.toml`` declares; an
    unreadable file yields none, a malformed key raises (parse_retired)."""
    out = []
    for name in names:
        try:
            with open(Path(base) / name / "deploy.toml", "rb") as fh:
                deploy = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        retire = parse_retired(name, deploy)
        if retire is not None:
            out.append(retire)
    return out


def declared_retired_at(repo: Path, component: str, commit: str,
                        run: Callable = subprocess.run) -> list:
    """The retired units ``deploy.toml`` declares at ``commit`` in ``repo``."""
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
    retire = parse_retired(component, deploy)
    return [retire] if retire is not None else []


def _retire_label(retire: UnitRetire) -> str:
    return f"{retire.component}: retire {', '.join(retire.units)}"


def _link_target(entry: Path) -> str:
    """Where a symlink points, made absolute against its directory."""
    return os.path.normpath(os.path.join(str(entry.parent), str(entry.readlink())))


def foreign_entry(unit: str, unit_dir: Path, checkout_root: str = CHECKOUT_ROOT):
    """Why ``<unit_dir>/<unit>`` is not sigmond's to retire, or None.

    A link that resolves to a file outside every sigmond checkout belongs
    to whoever made it (an operator's own unit of that name).  A dangling
    link, a link into a checkout, a regular file (a component's install.sh
    copies its units; the move rule keeps it in the backup directory) and
    no entry at all are sigmond's."""
    entry = Path(unit_dir) / unit
    if not entry.is_symlink() or not entry.exists():
        return None
    target = _link_target(entry)
    if target.startswith(checkout_root):
        return None
    return f"{entry} links to {target}, outside a sigmond checkout"


def _own_link(path: str, checkout_root: str = CHECKOUT_ROOT) -> bool:
    """A helper link sigmond's install made: a symlink that dangles or
    resolves into a sigmond checkout.  A regular file stays (it is not ours)."""
    p = Path(path)
    if not p.is_symlink():
        return False
    return not p.exists() or _link_target(p).startswith(checkout_root)


def _retire_order(units) -> list:
    """Timers first: a timer left running could start its service again."""
    return sorted(units, key=lambda u: 0 if u.endswith(".timer") else 1)


def retire_live(retire: UnitRetire, *, unit_dir: Path, run: Callable,
                checkout_root: str = CHECKOUT_ROOT) -> tuple:
    """``(states, live, foreign, links)`` for one retire.

    ``states`` maps unit -> (enabled, is-active word); ``live`` lists the
    units ``in_service`` (the move rule), plus any whose entry still sits in
    ``unit_dir``; ``foreign`` maps a live unit to why it is not sigmond's.
    A timer and its service go together: when one is foreign, both hold,
    since an operator's timer would start the service again.  ``links``
    lists the helper links still there."""
    states, live, foreign = {}, [], {}
    for unit in _retire_order(retire.units):
        enabled, word = _unit_words(unit, unit_dir=unit_dir, run=run)
        states[unit] = (enabled, word)
        entry = Path(unit_dir) / unit
        if in_service(enabled, word) or entry.is_symlink() or entry.exists():
            live.append(unit)
            why = foreign_entry(unit, unit_dir, checkout_root)
            if why is not None:
                foreign[unit] = why
    for unit in list(foreign):
        mate = _mate(unit)
        if mate in live and mate not in foreign:
            foreign[mate] = f"its pair {unit} is not sigmond's"
    links = [p for p in retire.links if _own_link(p, checkout_root)]
    return states, live, foreign, links


def _mate(unit: str) -> str:
    """x.timer <-> x.service."""
    stem, _, kind = unit.rpartition(".")
    return f"{stem}.service" if kind == "timer" else f"{stem}.timer"


def apply_retire(retire: UnitRetire, *, apply: bool, unit_dir: Path, backup_dir: Path,
                 run: Callable, now: datetime, checkout_root: str = CHECKOUT_ROOT) -> Step:
    """Stop, disable and clear the units ``retire`` names; see the module
    docstring.  "unchanged" when none is enabled, ran or installed and no
    helper link remains; "ran" with a ``held:`` line for a unit whose entry
    is not sigmond's (the others still go); "would retire" in the dry run;
    "retired" or RETIRE_FAILED on apply.  RETIRE_FAILED does not hold the
    release (a retire starts nothing): align exits 1, drift keeps the
    fault, and the next align tries again."""
    label = _retire_label(retire)
    states, live, foreign, links = retire_live(retire, unit_dir=unit_dir, run=run,
                                               checkout_root=checkout_root)
    ours = [u for u in live if u not in foreign]
    held = "; ".join(f"held: {why}; {u} left as it is" for u, why in foreign.items())
    if not ours and not links:
        return Step(label, "ran", held) if held else Step(label, "unchanged", "")
    if not apply:
        plan = []
        running = [u for u in ours if states[u][1] in _ACTIVE]
        if running:
            plan.append(f"stop and disable {', '.join(running)}")
        rest = [u for u in ours if u not in running]
        if rest:
            plan.append(f"disable and clear {', '.join(rest)}")
        if links:
            plan.append(f"remove {', '.join(links)}")
        if held:
            plan.append(held)
        return Step(label, "would retire", "; ".join(plan))

    errors, done, kept = [], [], set()
    for unit in ours:                 # timers first (_retire_order)
        enabled, word = states[unit]
        if _mate(unit) in kept:
            # Its timer still runs and would start it again.
            errors.append(f"{unit} left as it is: {_mate(unit)} did not stop")
            continue
        if word in _ACTIVE:
            cp = _systemctl(run, "stop", unit)
            if cp.returncode != 0:
                tail = _last_line(cp)
                errors.append(f"systemctl stop {unit} exit {cp.returncode}"
                              + (f" ({tail})" if tail else "") + f"; {unit} left as it is")
                kept.add(unit)
                continue
        if enabled:
            _systemctl(run, "disable", unit)   # a dangling unit fails here; cleared below
        if word in _RAN:
            cp = _systemctl(run, "reset-failed", unit)
            if cp.returncode != 0 and not _unloaded(run, unit):
                # Left "failed", it would read as retired again at every align.
                tail = _last_line(cp)
                errors.append(f"systemctl reset-failed {unit} exit {cp.returncode}"
                              + (f" ({tail})" if tail else ""))
        try:
            _clear_unit(unit, unit_dir, backup_dir, now, checkout_root)
        except Exception as exc:                                  # noqa: BLE001
            errors.append(f"clearing {unit} raised {type(exc).__name__}: {exc}")
            continue
        done.append(unit)
    for path in links:
        try:
            Path(path).unlink()
            done.append(path)
        except OSError as exc:
            errors.append(f"removing {path} raised {type(exc).__name__}: {exc}")
    cp = _systemctl(run, "daemon-reload")
    if cp.returncode != 0:
        errors.append(f"systemctl daemon-reload exit {cp.returncode}")
    if errors:
        return Step(label, RETIRE_FAILED, "; ".join(errors + ([held] if held else []))
                    + "; the next align retries")
    detail = f"stopped, disabled and cleared {', '.join(done)}"
    if held:
        return Step(label, "ran", f"{detail}; {held}")
    return Step(label, "retired", detail)


def unit_shippers(base: Path, names: Iterable[str]) -> dict:
    """unit -> the components among ``names`` whose deploy.toml lists it
    in [systemd] (units or templated_units), read from the working trees."""
    from sigmond.drift.units import declared_units
    out: dict = {}
    for name in names:
        for unit in declared_units(base, name):
            out.setdefault(unit, []).append(name)
    return out


def retire_hold(retire: UnitRetire, *, local, shippers: dict):
    """Why align leaves this retire alone, or None.  Align and drift share
    it, so drift never faults a retire align holds on purpose.

    The site manages the component by hand (``local``), or another
    component still lists one of its units."""
    if retire.component in set(local or ()):
        return f"this site manages {retire.component} by hand (locally_managed)"
    still = []
    for unit in retire.units:
        others = sorted(set(shippers.get(unit, ())) - {retire.component})
        if others:
            still.append(f"{', '.join(others)} still ships {unit}")
    return "; ".join(still) or None


def hold_retire(retire: UnitRetire, reason: str, *, unit_dir: Path, run: Callable,
                checkout_root: str = CHECKOUT_ROOT) -> Step:
    """The Step for a retire sigmond holds back: "unchanged" when nothing
    of it remains, else "ran" with a ``held:`` line.  Changes nothing."""
    _, live, _, links = retire_live(retire, unit_dir=unit_dir, run=run,
                                    checkout_root=checkout_root)
    if not live and not links:
        return Step(_retire_label(retire), "unchanged", "")
    left = ", ".join(live + links)
    return Step(_retire_label(retire), "ran", f"held: {reason}; {left} left as it is")


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
                service_active: Callable[[str], bool],
                own=None, checkout_root: str = CHECKOUT_ROOT, local=None) -> list:
    """Drift faults for a move left half done, and for a retired unit left.

    A retired unit (``retired_units``, or ``own``, default RETIRED_OWN)
    still enabled, active or installed faults, fixed by ``smd align
    --apply``; one whose entry links outside a sigmond checkout (or whose
    timer/service pair does) names the hand fix instead, since align leaves
    an operator's unit alone.  A helper link of sigmond's own retired units
    faults the same way.  A retire align holds on purpose (``retire_hold``:
    ``local``, default site-profile's locally_managed, or another checkout
    still ships the unit) reads as "drift", never a fault, and names what
    would lift the hold.  A malformed ``retired_units`` faults by itself and
    leaves the other items standing.

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
    names = sorted(set(enabled) | checkouts)
    retires = []
    for name in names:
        try:
            retires += declared_retired(base, [name])
        except ValueError as exc:
            out.append(Item("unit", "fault", f"{name}: retired_units", str(exc),
                            f"fix [systemd].retired_units in {name}'s deploy.toml"))
    retires += list(RETIRED_OWN if own is None else own)
    if local is None:
        try:
            from sigmond.site_profile import locally_managed_components
            local = locally_managed_components()
        except Exception:                                         # noqa: BLE001
            local = ()
    shippers = unit_shippers(base, sorted(checkouts)) if retires else {}
    for retire in retires:
        hold = retire_hold(retire, local=local, shippers=shippers)
        states = {}
        for unit in _retire_order(retire.units):
            entry = Path(unit_dir) / unit
            states[unit] = ("enabled" if is_enabled(unit) or _wants_links(unit, unit_dir)
                            else "active" if service_active(unit)
                            else "installed" if entry.is_symlink() or entry.exists()
                            else None)
        foreign = {u: foreign_entry(u, unit_dir, checkout_root) for u in states}
        for unit, state in states.items():
            if state is None:
                continue
            entry = Path(unit_dir) / unit
            why = foreign[unit]
            if why is None and foreign.get(_mate(unit)) and states.get(_mate(unit)):
                why = f"its pair {_mate(unit)} is not sigmond's"
            if hold is not None:
                fix = (f"retire {unit} by hand, or drop {retire.component} from "
                       "locally_managed" if "locally_managed" in hold else
                       f"drop {unit} from {retire.component}'s retired_units or from "
                       "the other component's [systemd] units")
                out.append(Item("unit", "drift", unit,
                                f"{unit} retired by {retire.component}, still {state}; "
                                f"align holds it: {hold}", fix))
            elif why is not None:
                out.append(Item("unit", "fault", unit,
                                f"{unit} retired by {retire.component}, but {why}",
                                f"remove or rename {entry} by hand"))
            else:
                out.append(Item("unit", "fault", unit,
                                f"{unit} retired by {retire.component}, still {state}",
                                "smd align --apply"))
        for path in retire.links:
            if hold is None and _own_link(path, checkout_root):
                out.append(Item("unit", "fault", path,
                                f"{path} links a unit {retire.component} retired",
                                "smd align --apply"))
    return out
