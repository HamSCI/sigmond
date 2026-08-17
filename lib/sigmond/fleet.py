"""Fleet inventory — where every sigmond host is, and how to reach it.

Answers "what hosts are there?"  It deliberately does **not** answer
"which credential reaches them" — that is the operator's ssh config.
The split is the same one ``ssh/config.fleet`` already makes: this file
records WHERE, ``~/.ssh/config`` supplies WHICH KEY.  Keeping them
separate is what lets two operators share one inventory without
stepping on each other's keys.

Layering, and why it is thinner than ``catalog.toml``'s
=======================================================

``catalog.py`` merges three layers by sparse overlay because a catalog
is a *description of software that could exist*, and new entries must
propagate to every host on ``git pull``.  An inventory is the opposite:
it is a *list of machines to act on*, and there is exactly one real one.

So there is **no repo-default layer here**.  ``etc/fleet.toml.example``
is documentation, not a layer — ``HamSCI/sigmond`` is public, and a
real inventory maps every field unit.  Resolution is first-match-wins,
highest precedence first:

    1. an explicit path (``--inventory``)
    2. ``$SIGMOND_FLEET``
    3. ``/etc/sigmond/fleet.toml``  (:data:`DEFAULT_FLEET_PATH`)

A *whole file* wins, rather than overlaying field-by-field.  Overlaying
inventories would mean an override file silently inherits hosts it did
not list — and a fan-out that touches a host the operator did not name
is precisely the failure this module exists to prevent.

Reach and hop
=============

``reach`` is an opaque ssh destination: anything ``ssh`` itself accepts,
including flags (``-J jump -p 2222 root@host``).  It is never parsed
into components — ssh is the parser.

``hop`` is the optional *inner* destination of a nested ssh, for hosts
whose credential does not live where the fan-out runs.  A sigmond guest
on a hypervisor is the live case: the hypervisor's own key is installed
in the guest, so the hypervisor must run the inner ssh.  This cannot be
expressed as ``-J``/ProxyJump, which would offer the *caller's* key on
the final hop.

Failure policy
==============

Loudly, always, naming what could not be parsed.  A host that is
silently skipped here is invisible in every later fan-out, which reads
exactly like a healthy fleet that happens not to include it.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


#: The documented default, mirroring ``catalog.toml``'s operator layer.
DEFAULT_FLEET_PATH = Path('/etc/sigmond/fleet.toml')

#: Escape hatch for hosts with no sigmond install — the devbox has no
#: ``/etc/sigmond`` at all, so the default path can never resolve there.
FLEET_PATH_ENV = 'SIGMOND_FLEET'


@dataclass(frozen=True)
class Host:
    """One fleet member.

    Attributes:
        name: Inventory key, e.g. ``b4``.
        reach: Opaque ssh destination. Never parsed.
        hop: Opaque inner ssh destination for a nested reach, or None.
        profile: Bring-up profile the host runs, e.g. ``dasi2``.
        role: Free-form grouping, e.g. ``field`` or ``server``.
        frozen: Why the host must not be changed, or None if it may be.
            A reason rather than a bool, so the skip can say *why* when
            it is reported.
    """

    name: str
    reach: str
    hop: Optional[str] = None
    profile: Optional[str] = None
    role: Optional[str] = None
    frozen: Optional[str] = None


def _resolve_path() -> Optional[Path]:
    """The inventory to read when no explicit path was given.

    Returns None when no inventory exists anywhere — a legitimate state
    on every host that is not the devbox.
    """
    env = os.environ.get(FLEET_PATH_ENV)
    if env:
        candidate = Path(env)
        if candidate.exists():
            return candidate
        # An operator who sets the variable means it; a typo there must
        # not degrade to "no fleet".
        raise FileNotFoundError(
            f"{FLEET_PATH_ENV} names a file that does not exist: {candidate}"
        )
    if DEFAULT_FLEET_PATH.exists():
        return DEFAULT_FLEET_PATH
    return None


def _host_from_block(name: str, block, source: Path) -> Host:
    if not isinstance(block, dict):
        raise ValueError(
            f"fleet inventory {source}: host '{name}' must be a table "
            f"([host.{name}]), got {type(block).__name__}"
        )
    reach = block.get('reach')
    if not reach or not isinstance(reach, str):
        raise ValueError(
            f"fleet inventory {source}: host '{name}' has no 'reach'. "
            f"Every host must record where it is, or it would be "
            f"invisible to every fan-out."
        )
    return Host(
        name=name,
        reach=reach,
        hop=block.get('hop'),
        profile=block.get('profile'),
        role=block.get('role'),
        frozen=block.get('frozen'),
    )


def load_fleet(path: Optional[str] = None) -> dict[str, Host]:
    """Load the inventory, keyed by host name.

    Args:
        path: Read this file specifically. When None, resolve per the
            precedence documented in the module docstring.

    Returns:
        Every host in the inventory. Empty when no inventory exists at
        all (no-path form only) — having no fleet is not an error.

    Raises:
        FileNotFoundError: An explicit ``path`` (or ``$SIGMOND_FLEET``)
            names a file that is not there. Returning an empty fleet for
            a typo'd path would report "no hosts" indistinguishably from
            a real empty inventory.
        ValueError: The file is not valid TOML, or a host block is
            malformed. Both name the file; host errors name the host.
    """
    if path is not None:
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(f"fleet inventory not found: {source}")
    else:
        resolved = _resolve_path()
        if resolved is None:
            return {}
        source = resolved

    try:
        with source.open('rb') as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"fleet inventory {source} is not valid TOML: {exc}") from exc

    hosts = raw.get('host') or {}
    if not isinstance(hosts, dict):
        raise ValueError(
            f"fleet inventory {source}: 'host' must be a table of hosts"
        )
    return {
        name: _host_from_block(name, block, source)
        for name, block in hosts.items()
    }


# ---------------------------------------------------------------------------
# Fan-out: reading the fleet
# ---------------------------------------------------------------------------

#: Every command the status fan-out is permitted to issue.
#:
#: This is the enforcement point for "``status`` must be INCAPABLE of
#: changing a host, not merely not changing one today". The fan-out
#: issues nothing that is not in this tuple, and a test asserts both
#: that membership holds and that no member can mutate. An edit that
#: adds a writing command has to add it here first, in plain sight.
_PROBE_CMD = 'true'
_VERSION_CMD = 'smd version'
_MANIFEST_CMD = 'cat /etc/sigmond-appliance/manifest.txt'
#: `smd doctor` with no --fix. The flag is not merely omitted at the
#: call site: it is absent from the only string the fan-out can send.
_DOCTOR_CMD = 'smd doctor'
READ_ONLY_COMMANDS: tuple[str, ...] = (_PROBE_CMD, _VERSION_CMD,
                                       _MANIFEST_CMD, _DOCTOR_CMD)


@dataclass(frozen=True)
class RunResult:
    """One remote command's outcome.

    ``rc`` is the exit status, ``out``/``err`` its streams. Reachability
    is decided by the dedicated probe rather than by inspecting ``rc``
    for ssh's 255 convention — a remote command may legitimately exit
    255 itself, and conflating the two would report a live host as
    unreachable.
    """

    rc: int
    out: str = ''
    err: str = ''


@dataclass(frozen=True)
class HostStatus:
    """What one host reports, and against what it was judged.

    Attributes:
        host: The inventory entry, so callers keep ``frozen``/``role``.
        reachable: Whether the fan-out got to the host at all.
        error: Why not, or what could not be read. None when clean.
        has_sigmond: Whether the host runs sigmond. A jump host or a
            data server legitimately does not; that is a fact, not a
            fault, and is reported rather than omitted.
        components: Live component SHAs, as ``smd version`` reports.
        blessed_source: What ``components`` was compared against —
            ``'manifest'`` (the blessed image pin), ``'main'`` (the
            fallback), or ``'none'`` (nothing to compare). Callers must
            surface this: "current with main" and "matches what we
            blessed" are different claims, and presenting the fallback
            as a blessed pin would erase the distinction the manifest
            exists to draw.
        drift: ``doctor.manifest_drift`` entries. Only meaningful when
            ``blessed_source == 'manifest'``.
        behind: Commits the host's sigmond is behind main. Only
            meaningful when ``blessed_source == 'main'``.
    """

    host: Host
    reachable: bool
    error: Optional[str] = None
    has_sigmond: bool = False
    components: dict = field(default_factory=dict)
    blessed_source: str = 'none'
    drift: list = field(default_factory=list)
    behind: Optional[int] = None


def fleet_status(fleet: dict, run, commits_behind=None) -> list:
    """Read every host's live pin and judge it against its blessed one.

    Read-only by construction: see :data:`READ_ONLY_COMMANDS`.

    Args:
        fleet: Hosts to read, as :func:`load_fleet` returns.
        run: Injected fan-out, ``run(host, command) -> RunResult``.
            Injected for the same reason ``doctor.venv_skew`` takes a
            ``probe`` — so tests never touch a real host.
        commits_behind: Optional ``(sha) -> int | None``, how far a
            sigmond SHA trails main. Supplies the fallback comparison
            for hosts with no blessed manifest, which today is all of
            them.

    Returns:
        One :class:`HostStatus` per host, in inventory order. Frozen
        hosts are included — freeze prevents changes, not visibility.

    Per-host failures stay per-host: one unreachable host must not
    abort the run and discard the others (``doctor.py:73``'s principle,
    and the defect Stage 3's Task 4 had to fix). A ``run`` that raises
    is contained the same way as one that returns a failure.
    """
    from .doctor import _parse_manifest_components, manifest_drift_text

    out = []
    for host in fleet.values():
        try:
            out.append(_status_for(host, run, commits_behind,
                                   _parse_manifest_components,
                                   manifest_drift_text))
        except Exception as exc:            # noqa: BLE001 — see docstring
            out.append(HostStatus(host=host, reachable=False,
                                  error=f'{type(exc).__name__}: {exc}'))
    return out


def _status_for(host, run, commits_behind, parse_components, drift_text):
    probe = run(host, _PROBE_CMD)
    if probe.rc != 0:
        return HostStatus(host=host, reachable=False,
                          error=(probe.err or probe.out or
                                 f'unreachable (exit {probe.rc})').strip())

    version = run(host, _VERSION_CMD)
    if version.rc != 0:
        # Reachable, but runs no sigmond. Not an error.
        return HostStatus(host=host, reachable=True, has_sigmond=False)

    live = parse_components(version.out)
    if not live:
        return HostStatus(
            host=host, reachable=True, has_sigmond=True,
            error="smd version produced no readable 'components (live):' block",
        )

    manifest = run(host, _MANIFEST_CMD)
    if manifest.rc == 0 and parse_components(manifest.out):
        return HostStatus(host=host, reachable=True, has_sigmond=True,
                          components=live, blessed_source='manifest',
                          drift=drift_text(live, manifest.out))

    behind = None
    if commits_behind is not None and live.get('sigmond'):
        behind = commits_behind(live['sigmond'])
    return HostStatus(host=host, reachable=True, has_sigmond=True,
                      components=live, blessed_source='main', behind=behind)


# ---------------------------------------------------------------------------
# The real fan-out, and presentation
# ---------------------------------------------------------------------------

#: Long enough for `smd version` to walk every component checkout on a
#: field unit at the end of a slow link; short enough that a dead host
#: does not stall the run.
SSH_TIMEOUT_SEC = 90

#: Fail fast on a host that is not answering at all. The fan-out reports
#: it as unreachable and moves on.
SSH_CONNECT_TIMEOUT_SEC = 10

#: BatchMode is not an optimisation. Without it ssh prompts for a
#: password on any host whose key is not authorized, and a fan-out that
#: blocks on a prompt hangs every remaining host behind it.
_SSH_OPTS = ('-o', 'BatchMode=yes',
             '-o', f'ConnectTimeout={SSH_CONNECT_TIMEOUT_SEC}')
_INNER_SSH_OPTS = f'-o BatchMode=yes -o ConnectTimeout={SSH_CONNECT_TIMEOUT_SEC}'


def _default_exec(argv, timeout):
    import subprocess
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return RunResult(rc=124, out='', err=f'timed out after {timeout}s')
    except OSError as exc:
        return RunResult(rc=127, out='', err=str(exc))
    return RunResult(rc=p.returncode, out=p.stdout, err=p.stderr)


def ssh_runner(exec_=None, timeout: int = SSH_TIMEOUT_SEC):
    """A fan-out that reaches hosts over ssh.

    ``exec_(argv, timeout) -> RunResult`` is injected so tests can
    assert on the argv without shelling out.

    A host with a ``hop`` is reached by a NESTED ssh — the outer host
    runs the inner one. This is deliberately not ProxyJump: ``-J`` makes
    the final hop with *our* key, and the whole reason a hop exists is
    that the credential for it lives on the outer host instead.
    """
    import shlex
    runner = exec_ or _default_exec

    def run(host: Host, command: str) -> RunResult:
        if host.hop:
            remote = f'ssh {_INNER_SSH_OPTS} {host.hop} {shlex.quote(command)}'
        else:
            remote = command
        argv = ['ssh', *_SSH_OPTS, *shlex.split(host.reach), remote]
        return runner(argv, timeout)

    return run


def format_status(statuses: list) -> str:
    """Render :func:`fleet_status` output for an operator.

    Every state gets words, not just a blank column: an unreachable host,
    a host with no sigmond, and a clean host must not look alike at a
    glance. The ``main`` fallback always carries "no manifest" so it can
    never be mistaken for a blessed pin.
    """
    if not statuses:
        return ('no hosts in the inventory — see etc/fleet.toml.example '
                'and $SIGMOND_FLEET')

    width = max(len(s.host.name) for s in statuses)
    lines = []
    for s in statuses:
        name = s.host.name.ljust(width)
        if not s.reachable:
            lines.append(f'{name}  unreachable — {s.error}')
        elif not s.has_sigmond:
            lines.append(f'{name}  no sigmond install '
                         f'(role: {s.host.role or "unset"})')
        elif s.error:
            lines.append(f'{name}  reachable, but {s.error}')
        elif s.blessed_source == 'manifest':
            if s.drift:
                moved = ', '.join(
                    f'{d["component"]} {d["manifest"]}→{d["live"]}'
                    for d in s.drift)
                lines.append(f'{name}  DRIFTED from blessed manifest: {moved}')
            else:
                lines.append(f'{name}  matches blessed manifest')
        else:
            sha = s.components.get('sigmond', '?')
            if s.behind is None:
                behind = 'position vs main unknown'
            elif s.behind == 0:
                behind = 'current with main'
            else:
                behind = f'{s.behind} behind main'
            lines.append(f'{name}  sigmond {sha}  {behind}  '
                         f'[no manifest — unblessed]')
        if s.host.frozen:
            lines.append(f'{" " * width}  FROZEN: {s.host.frozen}')
    return '\n'.join(lines)


def _default_git(argv):
    import subprocess
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, Exception) as exc:      # noqa: BLE001
        return RunResult(rc=127, out='', err=str(exc))
    return RunResult(rc=p.returncode, out=p.stdout, err=p.stderr)


def commits_behind_main(repo: str, git=None):
    """Build a ``(sha) -> int | None`` for the blessed-pin fallback.

    Counts commits between a host's live sigmond SHA and ``origin/main``
    in the local checkout. Used only when a host has no blessed
    manifest — which today is every host.

    Returns None, never 0, when the answer is not known: a SHA the local
    checkout has never seen, or output that does not parse. Zero would
    read as "current with main", so a stale host would be reported as up
    to date — the exact failure `smd fleet status` exists to prevent.
    """
    runner = git or _default_git

    def behind(sha: str):
        if not sha:
            return None
        result = runner(['git', '-C', str(repo), 'rev-list', '--count',
                         f'{sha}..origin/main'])
        if result.rc != 0:
            return None
        try:
            return int(result.out.strip())
        except ValueError:
            return None

    return behind


# ---------------------------------------------------------------------------
# Task 3 — smd fleet doctor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HostDoctor:
    """One host's `smd doctor` outcome.

    ``clean`` is deliberately tri-state. True and False mean the host
    was examined and the answer is known. **None means it was not
    examined** — unreachable, or running no sigmond. Collapsing that
    into False would libel a host nobody looked at; collapsing it into
    True would hide it, which is the failure mode this whole stage
    exists to remove.
    """

    host: Host
    reachable: bool
    clean: Optional[bool] = None
    has_sigmond: bool = False
    report: str = ''
    error: Optional[str] = None


def fleet_doctor(fleet: dict, run) -> list:
    """Run `smd doctor` across every reachable host and aggregate.

    Reports; never repairs. ``--fix`` is not passed — and cannot be,
    since :data:`READ_ONLY_COMMANDS` is the only vocabulary the fan-out
    has. Per-host failures stay per-host.

    A host not yet carrying Stage 3 runs an older `smd doctor` and
    reports less. That is expected until it updates, not a defect, and
    the summary says so rather than implying equal coverage.
    """
    out = []
    for host in fleet.values():
        try:
            out.append(_doctor_for(host, run))
        except Exception as exc:            # noqa: BLE001
            out.append(HostDoctor(host=host, reachable=False,
                                  error=f'{type(exc).__name__}: {exc}'))
    return out


def _doctor_for(host, run):
    probe = run(host, _PROBE_CMD)
    if probe.rc != 0:
        return HostDoctor(host=host, reachable=False,
                          error=(probe.err or probe.out or
                                 f'unreachable (exit {probe.rc})').strip())

    result = run(host, _DOCTOR_CMD)
    # 0 = clean, 1 = findings; anything else is smd failing to run at
    # all (absent, or erroring), which is not a verdict about the host.
    if result.rc == 0:
        return HostDoctor(host=host, reachable=True, clean=True,
                          has_sigmond=True, report=result.out.strip())
    if result.rc == 1:
        return HostDoctor(host=host, reachable=True, clean=False,
                          has_sigmond=True, report=result.out.strip())
    return HostDoctor(host=host, reachable=True, clean=None,
                      has_sigmond=False,
                      error=(result.err or result.out or
                             f'smd doctor exit {result.rc}').strip())


def format_doctor(results: list) -> str:
    """Aggregate without hiding.

    Every host appears, and the three outcomes — findings, clean, not
    examined — are told apart in words. Findings are shown, not merely
    counted: a count tells an operator something is wrong without
    telling them what, which is where fleet reports usually stop being
    useful.
    """
    if not results:
        return ('no hosts in the inventory — see etc/fleet.toml.example '
                'and $SIGMOND_FLEET')

    lines = []
    dirty = [r for r in results if r.clean is False]
    clean = [r for r in results if r.clean is True]
    skipped = [r for r in results if r.clean is None]

    for r in dirty:
        lines.append(f'{r.host.name}: findings')
        for line in (r.report or '(no detail)').splitlines():
            lines.append(f'    {line}')
        if r.host.frozen:
            lines.append(f'    FROZEN: {r.host.frozen}')
        lines.append('')

    if clean:
        lines.append('clean: ' + ', '.join(r.host.name for r in clean))
    for r in skipped:
        why = r.error or ('no sigmond install' if not r.has_sigmond
                          else 'not examined')
        state = 'unreachable' if not r.reachable else 'not examined'
        lines.append(f'{r.host.name}: {state} — {why}')

    lines.append('')
    lines.append('NOTE: a host that has not yet updated runs an older '
                 '`smd doctor` and reports fewer checks. Coverage is not '
                 'equal across the fleet until every host is current.')
    return '\n'.join(lines)
