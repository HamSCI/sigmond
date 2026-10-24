"""The lever step: turn off in-process senders below ``upload`` (spec §4.3, §8).

A client answers for every route its data takes.  So when a client's
effective sink state reads below ``upload`` (``sink_state.below_upload``),
sigmond turns off each sender that runs inside the client's own process,
outside the hs-uploader daemon's gate (spec §8.2):

====================  ===========================================  ============
sender                lever                                        written when
====================  ===========================================  ============
psk-recorder          ``PSK_DELIVERY_PIPELINES=server-raw``        delivery reads
                                                                   anything but
                                                                   server-raw alone
meteor-scatter        ``METEOR_SCATTER_DELIVERY_MODE=off``         mode starts a
                                                                   sender or marks
                                                                   rows (deposit)
wspr-recorder shim    ``WSPR_USE_HS_UPLOADER=0``                   the flag reads true
wspr-uploader         ``systemctl disable --now``                  it runs or stands
                                                                   enabled
mag-recorder          ``[uploader] enabled = false`` (a boolean)   it reads true or
                                                                   stays absent
====================  ===========================================  ============

The step reads each instance's effective environment with
``inprocess_senders``' own readers, so the rule that decides "this
instance sends" lives in one place.  It never raises a lever back:
raising restores nothing (spec §8.2); step 4 retires these senders.

Order (spec §8.3): every lever file is written first; then
``wspr-uploader`` stops through its own ``disable --now``; then each touched
instance restarts alone, by ``systemctl restart <unit>``, one at a time.  A
lever that fails (a symlink or hard link at the env file, an override that
still arms the sender) is named in ``LeverResult.failures`` and stops no
other lever.  Its unit does not restart: a restart reloads only what
changed, and nothing did, so it would cost a WSPR cycle or a psk slot and
stop nothing.  ``verify_levers`` then names the instance.

Restarts never touch radiod.  ``restart_units`` refuses a radiod unit, and
it reads every ``radiod@*`` unit's ``ActiveEnterTimestamp`` before the first
restart and after the last; a change raises ``RadiodMoved`` naming the unit.
On station B4, an ``smd apply`` that restarted radiod cost 5 of 6 channels.

Stale writers (spec §4.3): before any effective state first reads ``off``,
each running psk-recorder, meteor-scatter and wspr-recorder instance that
started before the installed hs-uploader ``sink/writer.py`` changed
restarts, so no part 1 writer stores rows during ``off``.  An instance the
lever step already restarted does not restart twice (the lever step runs
first; pass ``LeverResult.restarted`` as ``skip``).  A writer file whose
time cannot be read makes nothing stale and says so.

Start times compare at whole seconds, as systemd prints them.  An instance
lowered on disk that still runs from before its env file changed (a lever
written by an earlier run whose restart failed) restarts with no write, and
``verify_levers`` names it until it does.

This module calls systemctl through ``_systemctl`` alone, with the verbs
``restart``, ``disable --now`` and ``show`` (the RESTART GATE's static test
reads it).  Stdlib only.
"""
from __future__ import annotations

import math
import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import inprocess_senders as ips
from .safe_write import read_nofollow, write_toml_bool
from .upload import env_path_for, set_env_flag

WRITER_PY = Path("/opt/git/sigmond/hs-uploader/src/hs_uploader/sink/writer.py")
WSPR_UPLOADER = "wspr-uploader.service"
MAG_CONFIG = Path("mag-recorder") / "mag-recorder-config.toml"
# The clients that write governed sink rows (spec §4.3).
WRITER_CLIENTS = ("psk-recorder", "meteor-scatter", "wspr-recorder")
ALLOWED_VERBS = {("restart",), ("disable", "--now"), ("show",)}
RADIOD_PATTERNS = ("radiod@*", "radiod.service")
_RUNNING = ("active", "activating", "reloading", "deactivating")
_TRUE = ("1", "true", "yes", "on")
_MS_QUIET = ("off", "none", "disabled")
# wspr-uploader counts as armed while it runs, and while stopped in any
# UnitFileState that enables it (enabled, enabled-runtime...); "" means
# systemd did not say.
_UNIT_FILE_OFF = ("disabled", "masked", "masked-runtime", "not-found", "")
# These name no enablement of their own, so a unit in one of them counts
# as armed only while it runs.  `disable --now` never touches it stopped:
# on a linked unit, disable would remove the link itself (Task 27 (a)).
_UNIT_FILE_IF_RUNNING = ("linked", "linked-runtime", "static", "indirect",
                         "generated")
_RESTART_TIMEOUT = 300


class RadiodMoved(RuntimeError):
    """A radiod unit's start time changed across the restarts.

    ``failures`` lists the ``(unit, reason)`` restarts that failed before
    the check; ``result``, set by ``apply_levers``, holds the partial
    LeverResult (what was written and restarted before the error)."""

    def __init__(self, msg, failures=()):
        super().__init__(msg)
        self.failures = list(failures)
        self.result = None


class RestartFailed(RuntimeError):
    """One or more ``systemctl restart`` calls failed; ``failures`` lists
    ``(unit, reason)``."""

    def __init__(self, failures):
        self.failures = list(failures)
        self.result = None      # set by restart_stale_writers: StaleResult
        super().__init__("; ".join(f"{u}: {why}" for u, why in self.failures))


@dataclass(frozen=True)
class Roots:
    """Where the step reads and writes, so a scratch tree can stand in."""
    etc: Path = Path("/etc")
    writer_py: Path = WRITER_PY


# ---- systemctl -------------------------------------------------------------

def _systemctl(systemctl: Callable, *args: str, timeout: int = 30):
    """Run ``systemctl <args>``; only the verbs in ALLOWED_VERBS pass, and
    ``restart`` and ``disable --now`` take exactly one unit."""
    one_unit = {"restart": 2, "disable": 3}
    if not args or not any(tuple(args[:len(v)]) == v for v in ALLOWED_VERBS) \
            or len(args) != one_unit.get(args[0], len(args)):
        raise ValueError(f"sink_levers: systemctl {' '.join(args)} is not "
                         "allowed here")
    return systemctl(["systemctl", *args], capture_output=True, text=True,
                     stdin=subprocess.DEVNULL, timeout=timeout, check=False)


def _show_props(systemctl, names, props) -> list:
    """One dict per unit block of ``systemctl show -p ... <names>``."""
    argv = []
    for p in props:
        argv += ["-p", p]
    try:
        r = _systemctl(systemctl, "show", *argv, "--timestamp=unix", *names)
    except subprocess.SubprocessError as exc:
        raise OSError(f"systemctl show {' '.join(names)}: {exc}") from exc
    if r.returncode != 0:
        raise OSError(f"systemctl show {' '.join(names)} exited {r.returncode}")
    blocks, cur = [], {}
    for line in (r.stdout or "").splitlines():
        if not line.strip():
            if cur:
                blocks.append(cur)
            cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v
    if cur:
        blocks.append(cur)
    return blocks


def _unit_props(systemctl, unit, props) -> dict:
    blocks = _show_props(systemctl, [unit], props)
    return blocks[0] if blocks else {}


def _env(systemctl, unit) -> dict:
    """The unit's effective environment (``inprocess_senders``' reader)."""
    return ips._systemctl_show(unit, run=systemctl)


def _radiod_times(systemctl) -> dict:
    out = {}
    for b in _show_props(systemctl, RADIOD_PATTERNS,
                         ("Id", "ActiveEnterTimestamp",
                          "ActiveEnterTimestampMonotonic")):
        uid = b.get("Id", "")
        if uid:
            out[uid] = (b.get("ActiveEnterTimestamp", ""),
                        b.get("ActiveEnterTimestampMonotonic", ""))
    return out


def _runs_from_before(systemctl, unit, mtime: float) -> bool:
    """True when ``unit`` runs and started before ``mtime``.

    systemd prints ``ActiveEnterTimestamp`` in whole seconds, so the
    comparison runs at whole seconds: a unit that started in the same
    second a file changed counts as started after it (``started <
    floor(mtime)``).  A running unit whose start time cannot be read counts
    as started before."""
    p = _unit_props(systemctl, unit, ("ActiveState", "ActiveEnterTimestamp"))
    if p.get("ActiveState", "") not in _RUNNING:
        return False
    try:
        started = math.floor(float(p.get("ActiveEnterTimestamp", "").lstrip("@")))
    except ValueError:
        return True
    return started < math.floor(mtime)


def _is_radiod(unit: str) -> bool:
    return unit == "radiod" or unit.startswith(("radiod@", "radiod."))


def restart_units(units, *, systemctl=subprocess.run) -> None:
    """Restart each unit alone, once, in order, by ``systemctl restart``.

    Refuses any radiod unit before doing anything.  Reads every radiod
    unit's start time before the first restart and after the last, and
    raises RadiodMoved naming each unit whose time changed, appeared or
    vanished.  A failed restart does not stop the others; after the radiod
    check, RestartFailed lists them.
    """
    todo = list(dict.fromkeys(units))
    bad = [u for u in todo if _is_radiod(u)]
    if bad:
        raise ValueError(f"sink_levers: refusing to restart {', '.join(bad)}")
    if not todo:
        return
    before = _radiod_times(systemctl)
    failed = []
    for unit in todo:
        try:
            r = _systemctl(systemctl, "restart", unit, timeout=_RESTART_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            failed.append((unit, f"systemctl restart: {exc}"))
            continue
        if r.returncode != 0:
            why = (r.stderr or "").strip().splitlines()
            failed.append((unit, f"systemctl restart exited {r.returncode}"
                                 + (f": {why[-1]}" if why else "")))
    after = _radiod_times(systemctl)
    moved = sorted(u for u in set(before) | set(after)
                   if before.get(u) != after.get(u))
    if moved:
        raise RadiodMoved(
            "radiod moved during the lever step's restarts: "
            + ", ".join(f"{u} (ActiveEnterTimestamp {before.get(u, ('absent',))[0] or 'none'}"
                        f" -> {after.get(u, ('absent',))[0] or 'none'})"
                        for u in moved), failures=failed)
    if failed:
        raise RestartFailed(failed)


# ---- the plan ----------------------------------------------------------------

def _psk_armed(env: dict) -> bool:
    return ips._psk_delivery_pipelines(env) != ("server-raw",)


def _ms_armed(env: dict) -> bool:
    return ips._ms_delivery_mode(env) not in _MS_QUIET


def _wspr_armed(env: dict) -> bool:
    return (env.get("WSPR_USE_HS_UPLOADER") or "").strip().lower() in _TRUE


# client -> (armed(env), lever key, lever value)
_ENV_LEVERS = {
    "psk-recorder": (_psk_armed, "PSK_DELIVERY_PIPELINES", "server-raw"),
    "meteor-scatter": (_ms_armed, "METEOR_SCATTER_DELIVERY_MODE", "off"),
    "wspr-recorder": (_wspr_armed, "WSPR_USE_HS_UPLOADER", "0"),
}


@dataclass(frozen=True)
class EnvLever:
    client: str
    unit: str
    path: Path
    key: str
    value: str


@dataclass(frozen=True)
class LeverPlan:
    below: tuple = ()
    instances: tuple = ()          # (client, unit) for every instance checked
    env_levers: tuple = ()         # EnvLever to write, then restart its unit
    mag: Optional[Path] = None     # mag config to set [uploader] enabled=false
    mag_checked: Optional[Path] = None
    wspr_uploader: bool = False    # disable --now wspr-uploader
    wspr_uploader_checked: bool = False
    left_alone: tuple = ()         # units and files already lowered
    problems: tuple = ()           # (unit or file, why) the plan cannot act on
    restart_only: tuple = ()       # lowered on disk, still running from before
    etc: Path = Path("/etc")

    @property
    def restart(self) -> list:
        return list(dict.fromkeys([lv.unit for lv in self.env_levers]
                                  + list(self.restart_only)))


@dataclass
class LeverResult:
    changed: list = field(default_factory=list)      # files written
    restarted: list = field(default_factory=list)
    disabled: list = field(default_factory=list)
    left_alone: list = field(default_factory=list)
    failures: list = field(default_factory=list)     # (file or unit, why)

    def lines(self) -> list:
        out = [f"lever: changed {p}" for p in self.changed]
        out += [f"lever: stopped and disabled {u}" for u in self.disabled]
        out += [f"lever: restarted {u}" for u in self.restarted]
        out += [f"lever: left alone {x} (already lowered)" for x in self.left_alone]
        out += [f"lever: FAILED {x}: {why}" for x, why in self.failures]
        return out


def _instance_of(unit: str) -> str:
    inst = unit.partition("@")[2]
    return inst[:-len(".service")] if inst.endswith(".service") else inst


def _units_of(client, topology) -> list:
    if client not in set(topology.enabled_components()):
        return []
    return ips._instance_units(client, topology)


def _mag_armed(path: Path) -> bool:
    """True when mag-recorder's fallback uploader would run.  A missing
    config reads not armed: the fallback has nothing to run from, and a
    disabled mag-recorder with no config must not block the step."""
    try:
        text = read_nofollow(path)
    except FileNotFoundError:            # no /etc/mag-recorder at all
        return False
    if text is None:
        return False
    up = tomllib.loads(text).get("uploader", {})
    return not (isinstance(up, dict) and up.get("enabled", True) is False)


def _uploader_armed(systemctl) -> Optional[bool]:
    """True when wspr-uploader runs or stands enabled; None when systemd
    does not know the unit (wspr-recorder never installed it).  A linked,
    static, indirect or generated unit counts only while it runs."""
    p = _unit_props(systemctl, WSPR_UPLOADER,
                    ("LoadState", "ActiveState", "UnitFileState"))
    if p.get("LoadState", "") in ("", "not-found"):
        return None
    if p.get("ActiveState", "") in _RUNNING:
        return True
    state = p.get("UnitFileState", "")
    return state not in _UNIT_FILE_OFF and state not in _UNIT_FILE_IF_RUNNING


def plan_levers(clients_below, topology, *, roots: Roots = Roots(),
                systemctl=subprocess.run) -> LeverPlan:
    """What the lever step would change for ``clients_below``.  Reads only:
    each instance's effective environment, the mag config, and
    ``wspr-uploader``'s state."""
    below = tuple(sorted(set(clients_below)))
    instances, levers, alone, problems, restart_only = [], [], [], [], []
    for client in (c for c in _ENV_LEVERS if c in below):
        armed, key, value = _ENV_LEVERS[client]
        try:
            units = _units_of(client, topology)
        except OSError as exc:
            problems.append((client, f"cannot enumerate its instances: {exc}"))
            continue
        for unit in units:
            instances.append((client, unit))
            try:
                path = env_path_for(client, _instance_of(unit),
                                    base=str(roots.etc))
                if armed(_env(systemctl, unit)):
                    levers.append(EnvLever(client, unit, path, key, value))
                elif _lever_unloaded(systemctl, unit, path):
                    # lowered on disk, but the process predates the file:
                    # a lever written earlier whose restart never ran.
                    restart_only.append(unit)
                else:
                    alone.append(unit)
            except (OSError, ValueError) as exc:
                problems.append((unit, f"cannot read or place its lever: {exc}"))
    wspr_uploader = checked_uploader = False
    if "wspr-recorder" in below:
        checked_uploader = True
        try:
            armed = _uploader_armed(systemctl)
        except OSError as exc:
            problems.append((WSPR_UPLOADER, f"cannot read its state: {exc}"))
        else:
            wspr_uploader = bool(armed)
            if armed is False:
                alone.append(WSPR_UPLOADER)
    mag = mag_checked = None
    if "mag-recorder" in below:
        mag_checked = roots.etc / MAG_CONFIG
        try:
            if _mag_armed(mag_checked):
                mag = mag_checked
            else:
                alone.append(str(mag_checked))
        except (OSError, ValueError) as exc:      # TOMLDecodeError: ValueError
            problems.append((str(mag_checked), f"cannot read it: {exc}"))
    return LeverPlan(below=below, instances=tuple(instances),
                     env_levers=tuple(levers), mag=mag, mag_checked=mag_checked,
                     wspr_uploader=wspr_uploader,
                     wspr_uploader_checked=checked_uploader,
                     left_alone=tuple(alone), problems=tuple(problems),
                     restart_only=tuple(restart_only), etc=roots.etc)


# ---- applying it -------------------------------------------------------------

def apply_levers(plan: LeverPlan, *, systemctl=subprocess.run,
                 skip=()) -> LeverResult:
    """Write every lever, stop ``wspr-uploader``, then restart each touched
    instance alone, and each instance the plan found lowered on disk but
    running from before its env file.  Units in ``skip`` (already restarted
    after their lever landed) do not restart again; ``verify_levers`` still
    checks their start time.  ``LeverResult.restarted`` lists what this
    call restarted, for ``restart_stale_writers(skip=...)``.

    Raises RadiodMoved, with ``exc.result`` holding the partial result;
    every other failure lands in ``LeverResult.failures`` and stops no
    other lever."""
    res = LeverResult(left_alone=list(plan.left_alone),
                      failures=list(plan.problems))
    to_restart = []
    for lv in plan.env_levers:
        armed = _ENV_LEVERS[lv.client][0]
        try:
            set_env_flag(lv.path, lv.key, lv.value)
        except (OSError, ValueError) as exc:
            res.failures.append((str(lv.path), f"not written: {exc}"))
            continue
        res.changed.append(lv.path)
        try:
            still = armed(_env(systemctl, lv.unit))
        except OSError as exc:
            res.failures.append((lv.unit, f"cannot re-read its environment "
                                          f"after the write: {exc}"))
            continue
        if still:
            res.failures.append((str(lv.path),
                                 f"written, but {lv.unit}'s environment still "
                                 f"arms its sender (another setting overrides "
                                 f"{lv.key}); not restarted"))
            continue
        to_restart.append(lv.unit)
    to_restart += list(plan.restart_only)
    skip = set(skip)
    to_restart = [u for u in dict.fromkeys(to_restart) if u not in skip]
    if plan.mag is not None:
        try:
            write_toml_bool(plan.mag, "uploader", "enabled", False)
        except (OSError, ValueError) as exc:
            res.failures.append((str(plan.mag), f"not written: {exc}"))
        else:
            res.changed.append(plan.mag)
    if plan.wspr_uploader:
        try:
            r = _systemctl(systemctl, "disable", "--now", WSPR_UPLOADER,
                           timeout=_RESTART_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            res.failures.append((WSPR_UPLOADER, f"disable --now: {exc}"))
        else:
            if r.returncode == 0:
                res.disabled.append(WSPR_UPLOADER)
            else:
                res.failures.append((WSPR_UPLOADER, "disable --now exited "
                                                    f"{r.returncode}"))
    try:
        restart_units(to_restart, systemctl=systemctl)
    except RadiodMoved as exc:
        bad = {u for u, _ in exc.failures}
        res.failures.extend(exc.failures)
        res.restarted = [u for u in to_restart if u not in bad]
        exc.result = res
        raise
    except RestartFailed as exc:
        bad = {u for u, _ in exc.failures}
        res.failures.extend(exc.failures)
        res.restarted = [u for u in to_restart if u not in bad]
    else:
        res.restarted = list(dict.fromkeys(to_restart))
    return res


# ---- checking it (spec §8.4) ---------------------------------------------------

def _lever_unloaded(systemctl, unit, path: Path) -> bool:
    """True when ``unit`` runs from before ``path`` last changed (whole
    seconds), so whatever the file says has not reached the process.  A
    missing file loads nothing new."""
    try:
        mtime = os.stat(path).st_mtime
    except FileNotFoundError:
        return False
    return _runs_from_before(systemctl, unit, mtime)


def verify_levers(plan: LeverPlan, *, systemctl=subprocess.run) -> list:
    """One line per sender still armed in the plan's scope; empty means
    every sender stopped.  An instance lowered on disk that still runs from
    before its env file changed counts as armed."""
    msgs = []
    for client, unit in plan.instances:
        armed, key, value = _ENV_LEVERS[client]
        try:
            env = _env(systemctl, unit)
        except OSError as exc:
            msgs.append(f"{unit}: cannot read its environment: {exc}")
            continue
        if armed(env):
            msgs.append(f"{unit}: its environment still arms its sender "
                        f"({key}={env.get(key, '')!r}; the lever is {key}={value})")
            continue
        try:
            path = env_path_for(client, _instance_of(unit), base=str(plan.etc))
            stale = _lever_unloaded(systemctl, unit, path)
        except (OSError, ValueError) as exc:
            msgs.append(f"{unit}: cannot read its lever file or start time: {exc}")
            continue
        if stale:
            msgs.append(f"{unit}: still runs from before its lever was written "
                        f"to {path}; restart it")
    rechecked = {u for _, u in plan.instances} | {WSPR_UPLOADER,
                                                   str(plan.mag_checked)}
    for name, why in plan.problems:
        if name not in rechecked:          # a client it could not enumerate
            msgs.append(f"{name}: {why}")
    if plan.wspr_uploader_checked:
        try:
            if _uploader_armed(systemctl):
                msgs.append(f"{WSPR_UPLOADER}: still running or enabled")
        except OSError as exc:
            msgs.append(f"{WSPR_UPLOADER}: cannot read its state: {exc}")
    if plan.mag_checked is not None:
        try:
            if _mag_armed(plan.mag_checked):
                msgs.append(f"{plan.mag_checked}: [uploader] enabled does not "
                            "read the boolean false")
        except (OSError, ValueError) as exc:
            msgs.append(f"{plan.mag_checked}: cannot read it: {exc}")
    return msgs


# ---- stale writers (spec §4.3) --------------------------------------------------

@dataclass
class StaleResult:
    restarted: list = field(default_factory=list)
    notes: list = field(default_factory=list)   # e.g. an unreadable writer file

    def lines(self) -> list:
        return ([f"stale writer: restarted {u}" for u in self.restarted]
                + [f"stale writer: {n}" for n in self.notes])


def stale_writer_units(topology, *, writer_py: Path = WRITER_PY,
                       systemctl=subprocess.run, notes=None) -> list:
    """The running psk-recorder, meteor-scatter and wspr-recorder instances
    that started before ``writer_py`` last changed (whole seconds).

    A writer whose time cannot be read makes nothing stale (ruling C17-4:
    the accepted restarts are one-time ones, never a mass restart on a
    guess); a line naming the file goes to ``notes`` when given.  Raises
    OSError when a client's instances or a unit cannot be read."""
    try:
        mtime = os.stat(writer_py).st_mtime
    except OSError as exc:
        if notes is not None:
            notes.append(f"cannot read {writer_py} ({exc.strerror or exc}); "
                         "no instance counted stale, none restarted")
        return []
    stale = []
    for client in WRITER_CLIENTS:
        for unit in _units_of(client, topology):
            if _runs_from_before(systemctl, unit, mtime):
                stale.append(unit)
    return stale


def restart_stale_writers(topology, *, to_off: bool,
                          writer_py: Path = WRITER_PY,
                          systemctl=subprocess.run, skip=()) -> StaleResult:
    """Before a change that makes a state first read ``off``: restart each
    stale writer alone (``restart_units``), skipping the units in ``skip``
    (``LeverResult.restarted``: the lever step runs first).  For any other
    change it does nothing.  RadiodMoved and RestartFailed carry the
    partial StaleResult as ``exc.result``."""
    res = StaleResult()
    if not to_off:
        return res
    skip = set(skip)
    units = [u for u in stale_writer_units(topology, writer_py=writer_py,
                                           systemctl=systemctl, notes=res.notes)
             if u not in skip]
    try:
        restart_units(units, systemctl=systemctl)
    except (RadiodMoved, RestartFailed) as exc:
        bad = {u for u, _ in exc.failures}
        res.restarted = [u for u in units if u not in bad]
        exc.result = res          # the partial result rides the exception
        raise
    res.restarted = units
    return res
