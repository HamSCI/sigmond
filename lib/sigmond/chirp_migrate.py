"""`smd admin chirp convert|check|retire`: a receiver's codar-sounder
instance gains a chirp-recorder instance beside it, one night compares the
two, and codar-sounder then retires.  Convert is described here; check and
retire under "Task 22" below.

chirp-recorder replaces codar-sounder.  A station moves one receiver at a
time: convert starts chirp-recorder on the channels codar-sounder records,
both run side by side for at least one night, `check` compares their
arrivals, and `retire` stops codar-sounder (plan Task 22).  Nobody retires
codar-sounder by hand.

Convert takes these steps, in order:

1. `convert_targets` refuses an instance keyed by its radiod (the station
   has not run `smd admin instance split codar-sounder`), an instance whose
   reporter id differs from its key (a move `smd admin receiver apply`
   still owes), a receiver that already holds a chirp-recorder config, a
   state file it cannot trust, and a radiod-only host.
2. `chirp-recorder config init --from-codar` writes the receiver's config
   from codar-sounder's own file.
3. `chirp-recorder validate --json` reports the capacity admission on
   stdout.  When it refuses any channel, convert prints the reasons,
   removes the file step 2 wrote, and stops.  stderr carries the client's
   log lines; convert prints them and never parses them.
4. `hs-uploader sink set chirp-recorder fill`: chirp-recorder stores its
   rows and sends none during the side-by-side night.
5. Convert sets ``[component.chirp-recorder] enabled = true`` in topology
   itself (not through `smd enable`, which would also enable at boot every
   chirp-recorder unit it finds), and `smd admin uploader manifest --write`
   renders the pipelines of the clients topology enables.
6. `systemctl enable --now chirp-recorder@KEY.service`, then the state file
   `/var/lib/sigmond/chirp-migrate/KEY.json`, which Task 22 reads.

Any failure rolls back what this run did: it disables and stops the unit if
step 6 began, removes the config step 2 wrote, clears the topology flag if
this run set it, and renders the manifest again if step 5 rendered it.  The
sink switch stays at ``fill``: hs-uploader has no verb that removes a
switch, and with this run's unit gone no chirp-recorder rows come from this
receiver; the next convert sets it again.  So a second run starts clean.
A receiver whose state file exists is done, and a second run does nothing.
codar-sounder is never stopped, restarted or edited here; radiod is never
touched.

Root and /var/lib/sigmond (root:sigmond 2775): the state directory is
reached by descriptor through `instance_move._open_private`, which refuses
a link, a foreign owner, or group or other write.  The state file is read
with O_NOFOLLOW and checked for owner and mode, and written as a fresh
O_EXCL temporary file renamed within that directory.  A lock file there
keeps two runs apart.  The config removal goes through a descriptor on
/etc/chirp-recorder after checking that only root can write it.

Every command runs through the injectable
``run(argv) -> (rc, stdout, stderr)``.  ``etc_root`` (or ``root``, standing
for ``/``) lets tests use a scratch tree.  Stdlib only.
"""
from __future__ import annotations

import fcntl
import json
import os
import secrets
import shutil
import stat
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from . import instance_move as _im
from .instance import (InvalidReporterId, display_reporter_id, instance_key,
                       parse_user_reporter_id, systemd_escape)
from .instance_move import JournalError, existing_instances
from .instance_split import (Step, config_radiods, config_reporter,
                             is_reporter_key, shared_config)

STATE_DIR = Path("/var/lib/sigmond/chirp-migrate")
#: Stands for ``/``.  `check` and `retire_steps` default to it; tests point
#: it at a scratch tree.
ROOT = Path("/")
CODAR = "codar-sounder"
CHIRP = "chirp-recorder"
SINK_REASON = "side by side with codar-sounder"
SINK_BY = "smd admin chirp convert"
_LOCK = ".lock"
#: What lifts the ``fill`` convert sets, named wherever a message leaves it.
LIFT_FILL = ("`smd admin chirp retire KEY` lifts it to upload after a passing "
             "check; by hand: hs-uploader sink set chirp-recorder upload "
             "--reason TEXT --by NAME")
_MANIFEST = ("smd", "admin", "uploader", "manifest", "--write")

#: The brief's step number for each phase, as `--dry-run` prints them.
BRIEF_STEP = {"config": 2, "validate": 3, "sink": 4, "topology": 5,
              "manifest": 5, "start": 6, "state": 6}

# (argv, env=None) -> (rc, stdout, stderr); a 2-tuple (rc, output) is read
# as stdout alone.
Runner = Callable[..., tuple]

# Where each bare program name in a Step resolves when the real system runs
# it.  Steps keep the bare names so `--dry-run` reads as the brief writes it.
_PROGRAMS = {
    CHIRP: "/usr/local/bin/chirp-recorder",
    "smd": "/usr/local/bin/smd",
}


def default_run(argv, env: Optional[dict] = None) -> tuple:
    """Run one Step's argv; (rc, stdout, stderr).  hs-uploader runs through
    the daemon's own interpreter, as every hs-uploader call sigmond makes
    does."""
    import subprocess
    argv = list(argv)
    try:
        if argv[0] == "hs-uploader":
            from .uploader_manifest import run_hs_uploader
            p = run_hs_uploader(argv[1:], timeout=60)
        else:
            prog = _PROGRAMS.get(argv[0])
            if prog and not Path(prog).exists():
                prog = shutil.which(argv[0]) or prog
            p = subprocess.run([prog or argv[0], *argv[1:]],
                               capture_output=True, text=True, check=False,
                               env=env, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return p.returncode, p.stdout or "", p.stderr or ""


def _call(run: Runner, argv) -> tuple:
    r = run(list(argv))
    if len(r) == 2:
        return r[0], r[1], ""
    return r[0], r[1], r[2]


class MigrateRefused(Exception):
    """A migration step that must not run; the verb prints it and exits 1.
    Task 22's `retire_steps` raises it too."""


class StateUnreadable(MigrateRefused):
    """The state directory exists but this user may not open it: a non-root
    `--dry-run` once root's 0700 directory stands."""


@dataclass(frozen=True)
class ConvertTarget:
    key: str              # AC0G-B4
    reporter_id: str      # AC0G/B4
    radiod: str           # b4-status.local
    codar_config: Path    # /etc/codar-sounder/AC0G-B4.toml


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def _etc_of(t: ConvertTarget) -> Path:
    return Path(t.codar_config).parent.parent


def _real(etc_root: Path) -> bool:
    return Path(etc_root) == Path("/etc")


def state_path(etc_root: Path, key: str) -> Path:
    """`/var/lib/sigmond/chirp-migrate/<KEY>.json` beside ``etc_root``.
    For messages and tests; the code reaches the file by descriptor."""
    return Path(etc_root).parent / STATE_DIR.relative_to("/") / f"{key}.json"


def chirp_config(etc_root: Path, key: str) -> Path:
    return Path(etc_root) / CHIRP / f"{key}.toml"


def chirp_unit(key: str) -> str:
    return f"{CHIRP}@{systemd_escape(key)}.service"


def codar_unit(key: str) -> str:
    return f"{CODAR}@{systemd_escape(key)}.service"


def topology_path(etc_root: Path) -> Path:
    if _real(etc_root):
        from .paths import TOPOLOGY_PATH
        return TOPOLOGY_PATH
    return Path(etc_root) / "sigmond" / "topology.toml"


def _role_path(etc_root: Path) -> Path:
    from . import station_role
    if _real(etc_root):
        return station_role.ROLE_PATH
    return Path(etc_root) / "sigmond" / "station-role"


# --------------------------------------------------------------------------
# the state directory, by descriptor
# --------------------------------------------------------------------------

def _state_dir_fd(etc_root: Path, create: bool = False):
    """A descriptor on the state directory, or None when it is absent and
    ``create`` is off.  Raises MigrateRefused on a link, a foreign owner,
    or group or other write, and StateUnreadable when the caller may not
    open it (root's 0700 directory, opened by anyone else)."""
    var_root = Path(etc_root).parent / "var"
    where = state_path(etc_root, "").parent
    try:
        os.close(os.open(where, _im._DIR_FLAGS))
    except PermissionError:
        raise StateUnreadable(
            f"{where} is root's alone (mode 0700): run as root to read the "
            f"conversion state") from None
    except OSError:
        pass                  # absent, or a link: _open_private says which
    try:
        return _im._open_private(_im._sigmond_var(var_root), STATE_DIR.name,
                                 "migration state directory", create=create)
    except JournalError as exc:
        raise MigrateRefused(str(exc)) from None


def read_state(etc_root: Path, key: str) -> Optional[dict]:
    """The receiver's state, or None when it has none.  Raises
    MigrateRefused for a file it cannot trust: a link, not a regular file,
    not owned by root, writable by group or other, or not a state record
    for ``key``."""
    where = state_path(etc_root, key)
    dfd = _state_dir_fd(etc_root)
    if dfd is None:
        return None
    try:
        name = f"{key}.json"
        try:
            os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                         | os.O_CLOEXEC, dir_fd=dfd)
        except OSError as exc:
            raise MigrateRefused(f"{where}: {exc.strerror} (a link or not a "
                                 f"plain file); remove it by hand") from None
        with os.fdopen(fd, "r") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise MigrateRefused(f"{where} is not a regular file")
            if st.st_uid != _im._trusted_uid():
                raise MigrateRefused(f"{where} is not owned by root")
            if st.st_mode & 0o022:
                raise MigrateRefused(f"{where} is writable by group or other")
            raw = fh.read(1 << 16)
    finally:
        os.close(dfd)
    try:
        doc = json.loads(raw)
    except ValueError:
        raise MigrateRefused(f"{where} holds no JSON") from None
    if not isinstance(doc, dict) or doc.get("key") != key:
        raise MigrateRefused(f"{where} is not the state record of {key}")
    return doc


def _write_state(etc_root: Path, key: str, doc: dict) -> None:
    """Write the state file whole: a fresh O_EXCL|O_NOFOLLOW temporary
    file in the state directory, renamed over ``KEY.json`` within it."""
    dfd = _state_dir_fd(etc_root, create=True)
    tmp = f".{key}.json.{os.getpid()}.{secrets.token_hex(4)}.part"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                     | os.O_CLOEXEC, 0o644, dir_fd=dfd)
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps(doc, indent=2) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, f"{key}.json", src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def _lock(etc_root: Path) -> int:
    """Hold the state directory's lock; returns its descriptor.  Raises
    MigrateRefused when another run holds it."""
    dfd = _state_dir_fd(etc_root, create=True)
    try:
        try:
            fd = os.open(_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
                         | os.O_CLOEXEC, 0o600, dir_fd=dfd)
        except OSError as exc:
            raise MigrateRefused(f"{state_path(etc_root, '').parent / _LOCK}:"
                                 f" {exc.strerror}") from None
    finally:
        os.close(dfd)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise MigrateRefused("another `smd admin chirp` run holds the lock; "
                             "wait for it to finish") from None
    return fd


def _remove_config(path: Path) -> Optional[str]:
    """Unlink ``path`` through a descriptor on its directory, when only
    root can write that directory.  None on success (or nothing there),
    else why it stayed."""
    try:
        pfd = os.open(path.parent, _im._DIR_FLAGS)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"could not open {path.parent}: {exc.strerror}; remove {path} by hand"
    try:
        st = os.fstat(pfd)
        if st.st_uid != _im._trusted_uid() or st.st_mode & 0o022:
            return (f"refusing to remove {path}: others than root can write "
                    f"{path.parent}; remove it by hand")
        try:
            os.unlink(path.name, dir_fd=pfd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            return f"could not remove {path}: {exc.strerror}; remove it by hand"
    finally:
        os.close(pfd)
    return None


# --------------------------------------------------------------------------
# targets
# --------------------------------------------------------------------------

_SPLIT_FIRST = "run `smd admin instance split codar-sounder` first"


def _scan(etc_root: Path, run: Runner, dry_run: bool = False
          ) -> "tuple[list, list, list]":
    """(targets, [(key or None, refusal)], notes).  A None key refuses for
    the whole station.  A dry run that may not open the state directory
    (root's 0700, read by anyone else) lists every instance as a target and
    says, in ``notes``, that some may have converted already."""
    from . import station_role
    etc_root = Path(etc_root)
    targets: list = []
    refused: list = []
    notes: list = []
    blind = False
    why = station_role.refusal(CHIRP, station_role.role(_role_path(etc_root)))
    if why:
        refused.append((None, why))
    shared = shared_config(CODAR, etc_root)
    if shared.exists() and config_radiods(shared):
        refused.append((None, f"{shared} still names "
                              f"{', '.join(config_radiods(shared))}: "
                              f"{_SPLIT_FIRST}"))
    try:
        dfd = _state_dir_fd(etc_root)
        if dfd is not None:
            os.close(dfd)
    except StateUnreadable as exc:
        if not dry_run:
            refused.append((None, str(exc)))
            return targets, refused, notes
        blind = True
        notes.append(f"{exc}; this dry run cannot see which receivers "
                     f"converted already, so it lists every one")
    except MigrateRefused as exc:
        refused.append((None, str(exc)))
        return targets, refused, notes
    for e in existing_instances(CODAR, etc_root, run=run):
        cfg = etc_root / CODAR / f"{e.key}.toml"
        if cfg == shared:
            continue
        if not is_reporter_key(e.key):
            refused.append((None, f"{CODAR}@{e.key} is keyed by its radiod, "
                                  f"not a reporter: {_SPLIT_FIRST}"))
            continue
        try:
            if not blind and read_state(etc_root, e.key) is not None:
                continue
        except MigrateRefused as exc:
            refused.append((e.key, str(exc)))
            continue
        rid = config_reporter(cfg)
        if not rid:
            refused.append((e.key, f"{cfg} has no [instance] reporter_id; "
                                   f"{_SPLIT_FIRST}"))
            continue
        try:
            display = display_reporter_id(parse_user_reporter_id(rid))
            rkey = instance_key(rid)
        except (InvalidReporterId, ValueError) as exc:
            refused.append((e.key, f"{cfg}: reporter_id {rid!r}: {exc}"))
            continue
        if rkey != e.key:
            refused.append((e.key, f"{CODAR}@{e.key} records reporter "
                                   f"{display}, whose instance is {rkey}: run"
                                   f" `smd admin receiver apply` to move it "
                                   f"first"))
            continue
        radiods = config_radiods(cfg)
        radiod = e.radiod or (radiods[0] if len(radiods) == 1 else "")
        if not radiod:
            refused.append((e.key, f"{cfg} names "
                                   f"{', '.join(radiods) or 'no radiod'}; a "
                                   f"receiver's instance reads one"))
            continue
        mine = chirp_config(etc_root, e.key)
        if os.path.lexists(mine):
            refused.append((e.key, f"{mine} exists already; if "
                                   f"{chirp_unit(e.key)} runs, stop and "
                                   f"disable it, then move the file aside so "
                                   f"convert writes it from {cfg.name}"))
            continue
        targets.append(ConvertTarget(e.key, display, radiod, cfg))
    return targets, refused, notes


def convert_targets(etc_root: Path, run: Runner = default_run
                    ) -> "tuple[list[ConvertTarget], list[str]]":
    """(targets, refusals) over every codar-sounder instance under
    ``etc_root``.  A receiver already converted (its state file exists)
    appears in neither list.  Reads files only."""
    targets, refused, _ = _scan(etc_root, run)
    return targets, [m for _, m in refused]


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------

def convert_steps(t: ConvertTarget) -> list:
    """Steps 2-6 for one receiver, in order.  The topology and state steps
    carry no argv: `apply` does them in process."""
    etc = _etc_of(t)
    return [
        Step("config", f"write {chirp_config(etc, t.key)} from "
             f"{t.codar_config.name}",
             (CHIRP, "config", "init", "--non-interactive",
              "--instance", t.key, "--reporter-id", t.reporter_id,
              "--radiod-status", t.radiod,
              "--from-codar", str(t.codar_config)), t),
        Step("validate", "check capacity for every channel",
             (CHIRP, "validate", "--json", "--instance", t.key), t),
        Step("sink", "hold chirp-recorder's rows on this station (sink fill)",
             ("hs-uploader", "sink", "set", CHIRP, "fill",
              "--reason", SINK_REASON, "--by", SINK_BY), t),
        Step("topology", f"enable chirp-recorder in {topology_path(etc)}",
             (), t),
        Step("manifest", "render chirp-recorder's uploader pipelines",
             _MANIFEST, t),
        Step("start", f"enable and start {chirp_unit(t.key)}",
             ("systemctl", "enable", "--now", chirp_unit(t.key)), t),
        Step("state", f"record the conversion in {state_path(etc, t.key)}",
             (), t),
    ]


def _capacity_refusals(stdout: str) -> "tuple[list[str], list[str]]":
    """(refusal lines, admitted channel names) from `validate --json`'s
    stdout.  An answer that cannot be read refuses."""
    try:
        doc = json.loads(stdout)
    except ValueError:
        return [f"validate --json printed no JSON on stdout: "
                f"{stdout.strip()[:200] or 'nothing'}"], []
    if not isinstance(doc, dict):
        return ["validate --json printed no JSON object"], []
    why: list = []
    for i in doc.get("issues") or []:
        if isinstance(i, dict) and i.get("severity") == "fail":
            why.append(str(i.get("message", "")))
    cap = doc.get("capacity")
    if not isinstance(cap, dict):
        why.append("validate --json reports no capacity block")
        return why, []
    for item in cap.get("refused") or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            why.append(f"channel {item[0]}: {item[1]}")
        else:
            why.append(f"channel refused: {item}")
    admitted = [str(n) for n in cap.get("admitted") or []]
    if not admitted and not why:
        why.append("the capacity block admits no channel")
    return why, admitted


def _state_doc(t: ConvertTarget, channels: list) -> dict:
    return {"key": t.key, "reporter_id": t.reporter_id, "radiod": t.radiod,
            "started_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "codar_unit": codar_unit(t.key), "channels": list(channels),
            "verdict": None, "verdicts": [], "retired_at": None}


def apply(steps: list, run: Runner = default_run,
          say: Callable[[str], None] = print) -> int:
    """Run `convert_steps` output in order; 0 when every step succeeded.

    Stops at the first failure and rolls back what this run did (see the
    module docstring); codar-sounder is never touched."""
    from .topology import load_topology, set_component_enabled
    did = {"config": None, "sink": False, "topology": None,
           "manifest": False, "unit": None}
    channels: list = []

    def rollback() -> None:
        reverted = False
        keep_config = False
        if did["unit"]:
            rc, out, err = _call(run, ("systemctl", "disable", "--now",
                                       did["unit"]))
            if rc == 0:
                say(f"reverted: disabled and stopped {did['unit']}")
                reverted = True
            else:
                keep_config = True
                say(f"FAILED: systemctl disable --now {did['unit']} (exit "
                    f"{rc}); kept {did['config']} so the unit keeps its "
                    f"config: disable it by hand, then remove the file")
        if did["config"] is not None and not keep_config:
            why = _remove_config(did["config"])
            if why:
                say(f"FAILED: {why}")
            else:
                say(f"reverted: removed {did['config']}")
                reverted = True
        if did["topology"] is not None:
            try:
                set_component_enabled(CHIRP, False, did["topology"])
                say("reverted: chirp-recorder disabled in topology again")
                reverted = True
            except OSError as exc:
                say(f"FAILED: could not clear chirp-recorder in "
                    f"{did['topology']}: {exc}")
        if did["manifest"]:
            rc, out, err = _call(run, _MANIFEST)
            if rc == 0:
                say("reverted: uploader manifest rendered again")
            else:
                say(f"FAILED: {' '.join(_MANIFEST)} (exit {rc}); run it by "
                    f"hand")
        if did["sink"]:
            say("left: the chirp-recorder sink switch stays at fill "
                "(hs-uploader has no verb that removes a switch; no unit of "
                "this run records, and the next convert sets it again).  "
                f"{LIFT_FILL}")
        say(f"{'nothing else' if reverted else 'nothing'} changed; "
            f"{CODAR} still runs")

    def failed(s, rc, out, err) -> int:
        say(f"FAILED: {' '.join(s.argv) or s.text} (exit {rc})")
        for line in (out + err).strip().splitlines():
            say(f"    {line}")
        rollback()
        return 1

    for s in steps:
        t = s.target
        etc = _etc_of(t)
        if s.phase == "topology":
            path = topology_path(etc)
            try:
                if not load_topology(path).is_enabled(CHIRP):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    set_component_enabled(CHIRP, True, path)
                    did["topology"] = path
                    say(s.text)
                else:
                    say(f"chirp-recorder already enabled in {path}")
            except (OSError, ValueError) as exc:
                say(f"FAILED: {s.text}: {exc}")
                rollback()
                return 1
            continue
        if s.phase == "state":
            try:
                _write_state(etc, t.key, _state_doc(t, channels))
            except (OSError, MigrateRefused) as exc:
                say(f"FAILED: {s.text}: {exc}")
                rollback()
                return 1
            say(s.text)
            continue
        if s.phase == "start":
            did["unit"] = chirp_unit(t.key)       # enable --now may half-succeed
        rc, out, err = _call(run, s.argv)
        if s.phase == "config" and os.path.lexists(chirp_config(etc, t.key)):
            did["config"] = chirp_config(etc, t.key)   # targets saw none there
        if s.phase == "validate":
            for line in err.strip().splitlines():
                say(f"    {CHIRP} (stderr): {line}")
            why, channels = _capacity_refusals(out)
            if why:
                say(f"FAILED: {CHIRP}@{t.key} cannot record every channel "
                    f"{CODAR} records:")
                for w in why:
                    say(f"    {w}")
                rollback()
                return 1
            say(f"capacity admits {', '.join(channels)}")
            continue
        if rc != 0:
            return failed(s, rc, out, err)
        if s.phase == "config" and did["config"] is None:
            say(f"FAILED: {' '.join(s.argv)} wrote no "
                f"{chirp_config(etc, t.key)}")
            rollback()
            return 1
        if s.phase in ("sink", "manifest"):
            did[s.phase] = True
        say(s.text)
    return 0


def apply_convert(root: Path, *, key: Optional[str] = None,
                  run: Runner = default_run, dry_run: bool = False,
                  say: Callable[[str], None] = print) -> int:
    """Targets, steps and apply in one call.  ``root`` stands for ``/``;
    ``key`` (any spelling of a reporter id) limits the run to one receiver.
    Returns 0 when every target converted, or none needed it."""
    etc_root = Path(root) / "etc"
    want = None
    if key:
        try:
            want = instance_key(key)
        except (InvalidReporterId, ValueError) as exc:
            say(f"FAILED: {key!r}: {exc}")
            return 1
    lock = None
    if not dry_run:
        try:
            lock = _lock(etc_root)
        except MigrateRefused as exc:
            say(f"FAILED: {exc}")
            say("refused; nothing changed")
            return 1
    try:
        return _convert(etc_root, want, run, dry_run, say)
    finally:
        if lock is not None:
            os.close(lock)


def _convert(etc_root, want, run, dry_run, say) -> int:
    targets, refused, notes = _scan(etc_root, run, dry_run)
    for n in notes:
        say(f"note: {n}")
    if want:
        targets = [t for t in targets if t.key == want]
        refused = [(k, m) for k, m in refused if k in (want, None)]
    for _, m in refused:
        say(f"FAILED: {m}")
    if refused:
        say("refused; nothing changed")
        return 1
    if not targets:
        if want and not notes and read_state(etc_root, want) is not None:
            say(f"{want}: converted already "
                f"({state_path(etc_root, want)}); nothing to do")
            return 0
        if want:
            say(f"FAILED: no {CODAR} instance {want} under {etc_root / CODAR}")
            return 1
        say(f"nothing to convert: every {CODAR} instance has a "
            f"{CHIRP} beside it, or none exists")
        return 0
    rc = 0
    for t in targets:
        steps = convert_steps(t)
        say(f"{t.reporter_id} ({t.key}) on {t.radiod}"
            + (" (dry run)" if dry_run else ""))
        if dry_run:
            say("  1. checks passed: a reporter-keyed instance, no "
                "chirp-recorder config yet")
            for s in steps:
                say(f"  {BRIEF_STEP[s.phase]}. {s.text}")
                if s.argv:
                    say(f"       {' '.join(s.argv)}")
            continue
        if apply(steps, run=run, say=say) != 0:
            rc = 1
    if dry_run:
        say("dry run; nothing changed")
    return rc


# ==========================================================================
# Task 22: `smd admin chirp check` and `smd admin chirp retire`
# ==========================================================================
#
# check: after a side-by-side night, `chirp-recorder compare` sets
# chirp-recorder's rows against codar-sounder's over that night, and the
# verdict lands in the state file, one per night.
#
# retire: once the last N stored verdicts pass on consecutive covered
# nights, codar-sounder goes for that receiver, in this order:
#   1. systemctl disable --now codar-sounder@KEY.service;
#   2. /etc/codar-sounder/KEY.toml and env/KEY.env move aside to
#      *.retired-YYYYMMDD, after a copy into instance_move's /etc-side
#      backup (data under /var/lib/codar-sounder stays);
#   3. codar-sounder leaves topology, by the same set_component_enabled
#      convert uses, only when no codar-sounder instance remains on the host
#      (validate_receivers keeps codar-sounder out of a receiver's
#      `clients`, so the site profile holds nothing to remove);
#   4. smd admin uploader manifest --write;
#   5. hs-uploader sink set chirp-recorder upload: the held rows ship;
#   6. retired_at in the state file.
# Before step 1, retire reads chirp-recorder's switch through
# `hs-uploader sink status --json` and records what it found in the state
# file (`sink_before_retire`).  A switch an operator set `off` refuses: the
# raise to upload would override that choice.  A read that fails refuses
# too, before anything changes.
#
# A step that fails stops the run.  What ran stays (codar-sounder down,
# chirp-recorder still holding its rows at fill), and a second run resumes:
# a disabled unit disables again, a file already aside is skipped, the
# recorded switch state is kept.
#
# None of the commands these verbs run takes the lifecycle lock that
# `bin/smd` holds around them: systemctl, chirp-recorder and hs-uploader
# never touch it, and `smd admin uploader manifest --write` dispatches
# outside main()'s locked set and takes hs-uploader's install lock alone.

CHECK_STATUSES = ("pass", "fail", "insufficient")
RETIRE_REASON = "codar-sounder retired"
RETIRE_BY = "smd admin chirp retire"
_NIGHT = "00:00-06:00 local solar time"

#: The brief's step number for each retire phase, as `--dry-run` prints them.
RETIRE_STEP = {"disable": 1, "aside": 2, "topology": 3, "manifest": 3,
               "sink": 4, "state": 5}


def _root(root) -> Path:
    return Path(root) if root is not None else Path(ROOT)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(text: str) -> datetime:
    t = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _key(key: str) -> str:
    try:
        return instance_key(key)
    except (InvalidReporterId, ValueError) as exc:
        raise MigrateRefused(f"{key!r}: {exc}") from None


# ---- the night ----------------------------------------------------------

def _solar_offset(lon: float) -> timedelta:
    return timedelta(hours=float(lon) / 15.0)


def _first_midnight(t: datetime, lon: float) -> datetime:
    """The first local solar midnight at or after ``t``, in UTC."""
    off = _solar_offset(lon)
    local = t + off
    day = datetime(local.year, local.month, local.day, tzinfo=timezone.utc)
    if day < local:
        day += timedelta(days=1)
    return day - off


def _last_midnight(now: datetime, lon: float) -> datetime:
    """The latest local solar midnight whose 00:00-06:00 closed by ``now``."""
    off = _solar_offset(lon)
    local = now - timedelta(hours=6) + off
    return datetime(local.year, local.month, local.day,
                    tzinfo=timezone.utc) - off


def night_covered(started_at: datetime, now: datetime,
                  receiver_lon: float) -> bool:
    """True once the window from ``started_at`` to ``now`` holds a whole
    00:00-06:00 local solar time at ``receiver_lon`` and spans at least
    12 h (Michael, 2026-11-19, decision 3)."""
    if now - started_at < timedelta(hours=12):
        return False
    return _first_midnight(started_at, receiver_lon) + timedelta(hours=6) <= now


def _night_window(started_at: datetime, now: datetime, lon: float):
    """(night date, since, until) of the latest covered night: local solar
    noon to noon around its midnight, cut to the side-by-side window.  The
    night carries the local solar date of its midnight.  None when no night
    is covered yet."""
    if not night_covered(started_at, now, lon):
        return None
    m = _last_midnight(now, lon)
    if m < started_at:
        return None
    since = max(started_at, m - timedelta(hours=12))
    until = min(now, m + timedelta(hours=12))
    return (m + _solar_offset(lon)).date().isoformat(), since, until


def _grid_lon(grid: str) -> float:
    g = grid.strip()
    if len(g) not in (4, 6) or not g[:2].isalpha() or not g[2:4].isdigit():
        raise ValueError(f"bad grid square {grid!r}")
    lon = (ord(g[0].upper()) - 65) * 20 - 180 + int(g[2]) * 2
    if len(g) == 6:
        if not g[4].isalpha():
            raise ValueError(f"bad grid square {grid!r}")
        return lon + (ord(g[4].lower()) - 97) * (2 / 24) + 1 / 24
    return lon + 1.0


def receiver_lon(etc_root: Path, key: str) -> float:
    """The receiver's longitude from chirp-recorder's own config:
    ``[station] receiver_lon``, else the centre of ``grid_square``.  Read
    without following a link."""
    import tomllib
    from . import safe_write
    path = chirp_config(etc_root, key)
    try:
        text = safe_write.read_nofollow(path)
    except OSError as exc:
        raise MigrateRefused(f"{path}: {exc}") from None
    if text is None:
        raise MigrateRefused(f"{path} is missing: the receiver's longitude "
                             f"places its night")
    try:
        st = tomllib.loads(text).get("station") or {}
    except ValueError as exc:
        raise MigrateRefused(f"{path}: {exc}") from None
    lon = st.get("receiver_lon")
    if isinstance(lon, (int, float)) and -180 <= lon <= 180:
        return float(lon)
    try:
        return _grid_lon(str(st.get("grid_square") or ""))
    except ValueError:
        raise MigrateRefused(f"{path}: [station] gives neither receiver_lon "
                             f"nor a grid_square") from None


# ---- verdicts -----------------------------------------------------------

def _passing_run(verdicts) -> int:
    """How many passing verdicts end the list, on consecutive covered
    nights."""
    n = 0
    prev = None
    for v in reversed(verdicts if isinstance(verdicts, list) else []):
        if not isinstance(v, dict) or v.get("status") != "pass" \
                or v.get("covered") is False:
            break
        try:
            night = date.fromisoformat(str(v.get("night")))
        except ValueError:
            break
        if prev is not None and night != prev - timedelta(days=1):
            break
        prev = night
        n += 1
    return n


def _print_verdict(d: dict, say) -> None:
    rows = [r for r in d.get("rows") or [] if isinstance(r, dict)]
    for gating in (True, False):
        for r in rows:
            if bool(r.get("gating")) != gating:
                continue
            say(f"  {str(r.get('name')):10s} {r.get('value')!s:>12}  limit "
                f"{r.get('limit')!s:>8}  "
                f"{'gates' if gating else 'reported'}  "
                f"{'ok' if r.get('passed') else 'FAIL'}")
    for label, field in (("matched", "matched"), ("explained", "explained"),
                         ("unmatched (codar-sounder)", "codar_unmatched"),
                         ("chirp-recorder only", "chirp_only")):
        items = d.get(field) or []
        say(f"  {label}: {len(items)}")
        for it in items:
            if field == "matched" and isinstance(it, list) and len(it) == 4:
                say(f"    chirp {it[0]} ns  codar {it[1]} ns  diff {it[2]} ns"
                    f"  {it[3]}")
            elif field == "explained" and isinstance(it, list) and len(it) == 2:
                say(f"    codar {it[0]} ns: {it[1]}")
            else:
                say(f"    {it} ns")


def check(key: str, *, run: Runner = default_run, now=None, root=None,
          say: Callable[[str], None] = print) -> dict:
    """Compare chirp-recorder with codar-sounder over the latest covered
    night and append the verdict to the state file's ``verdicts`` (one per
    night; a second check of the same night replaces the first).

    Returns ``{"status": ...}``: ``window`` while no night is covered,
    ``error`` when the comparison gave no verdict (nothing stored), else
    the verdict's own ``pass``, ``fail`` or ``insufficient``.  Raises
    MigrateRefused for a receiver it cannot check."""
    key = _key(key)
    etc_root = _root(root) / "etc"
    now = now or datetime.now(timezone.utc)
    lock = _lock(etc_root)
    try:
        return _check(etc_root, key, run, now, say)
    finally:
        os.close(lock)


def _check(etc_root, key, run, now, say) -> dict:
    st = read_state(etc_root, key)
    if st is None:
        raise MigrateRefused(f"{key} has no conversion state: run `smd admin "
                             f"chirp convert {key}` first")
    if st.get("retired_at"):
        raise MigrateRefused(f"{key}: codar-sounder retired already "
                             f"({st['retired_at']}); nothing to compare")
    try:
        started = _parse_iso(st["started_at"])
    except (KeyError, ValueError):
        raise MigrateRefused(f"{state_path(etc_root, key)}: no readable "
                             f"started_at") from None
    lon = receiver_lon(etc_root, key)
    win = _night_window(started, now, lon)
    if win is None:
        first = max(_first_midnight(started, lon) + timedelta(hours=6),
                    started + timedelta(hours=12))
        msg = (f"{key}: no whole night yet; the comparison waits for a window "
               f"that holds {_NIGHT} (longitude {lon:.2f}) and spans 12 h: "
               f"from {_iso(first)}")
        say(msg)
        return {"status": "window", "message": msg, "ready_at": _iso(first)}
    night, since, until = win
    argv = (CHIRP, "compare", "--instance", key, "--since", _iso(since),
            "--until", _iso(until), "--json")
    say(f"{key}: night {night}, {_iso(since)} to {_iso(until)}")
    rc, out, err = _call(run, argv)
    for line in err.strip().splitlines():
        say(f"    {CHIRP} (stderr): {line}")
    try:
        d = json.loads(out)
    except ValueError:
        d = None
    status = d.get("status") if isinstance(d, dict) else None
    if status not in CHECK_STATUSES or not isinstance(d.get("rows"), list) \
            or rc not in (0, 1):
        msg = (f"FAILED: {' '.join(argv)} (exit {rc}) gave no verdict"
               + (f" (status {status!r})" if status else ""))
        say(msg)
        return {"status": "error", "message": msg, "night": night}
    entry = {"night": night, "status": status, "rows": d["rows"],
             "since": _iso(since), "until": _iso(until), "covered": True,
             "checked_at": _iso(now)}
    if "mode" in d:
        entry["mode"] = d["mode"]
    verdicts = [v for v in st.get("verdicts") or []
                if isinstance(v, dict) and v.get("night") != night]
    verdicts.append(entry)
    verdicts.sort(key=lambda v: str(v.get("night")))
    st["verdicts"] = verdicts
    st["verdict"] = status
    _write_state(etc_root, key, st)
    say(f"{key}: {status} for the night of {night}"
        + (f", match {d['mode']}" if "mode" in d else ""))
    _print_verdict(d, say)
    run_n = _passing_run(verdicts)
    say(f"{key}: {run_n} passing night{'s' if run_n != 1 else ''} in a row "
        f"stored in {state_path(etc_root, key)}")
    return {"status": status, "night": night, "rows": d["rows"],
            "message": f"{status} for the night of {night}"}


# ---- retire -------------------------------------------------------------

@dataclass(frozen=True)
class RetireTarget:
    key: str
    etc_root: Path


def _codar_files(etc_root: Path, key: str) -> list:
    d = Path(etc_root) / CODAR
    return [d / f"{key}.toml", d / "env" / f"{key}.env"]


def _states(etc_root: Path) -> list:
    """Every state record in the state directory that reads cleanly."""
    dfd = _state_dir_fd(etc_root)
    if dfd is None:
        return []
    try:
        names = sorted(os.listdir(dfd))
    finally:
        os.close(dfd)
    out = []
    for n in names:
        if not n.endswith(".json") or n.startswith("."):
            continue
        try:
            st = read_state(etc_root, n[:-len(".json")])
        except MigrateRefused:
            continue
        if st is not None:
            out.append(st)
    return out


def retire_steps(key: str, *, nights: int = 1,
                 force_reason: Optional[str] = None, root=None,
                 record: bool = True) -> list:
    """Retire's steps for one receiver, in order; raises MigrateRefused
    unless its last ``nights`` stored verdicts pass on consecutive covered
    nights.

    ``force_reason`` serves one case (Michael, 2026-11-19, decision 6):
    this receiver's own latest check reads ``insufficient`` and a partner
    receiver on this host holds ``nights`` passing verdicts.  ``record``
    writes the reason and the partner's key into the state file."""
    key = _key(key)
    etc_root = _root(root) / "etc"
    if nights < 1:
        raise MigrateRefused("--nights must be at least 1")
    st = read_state(etc_root, key)
    if st is None:
        raise MigrateRefused(f"{key} has no conversion state: run `smd admin "
                             f"chirp convert {key}` first")
    if st.get("retired_at"):
        raise MigrateRefused(f"{key}: codar-sounder retired already "
                             f"({st['retired_at']})")
    verdicts = st.get("verdicts") or []
    have = _passing_run(verdicts)
    if force_reason is None:
        if have == 0:
            raise MigrateRefused(
                f"{key}: no passing check ends its record; run `smd admin "
                f"chirp check {key}` after a covered night ({_NIGHT}, 12 h)")
        if have < nights:
            raise MigrateRefused(
                f"{key} holds {have} passing night{'s' if have != 1 else ''} "
                f"in a row; retire wants {nights}")
    else:
        rule = (f"--force serves one case only: {key}'s own latest check "
                f"reads \"insufficient\" (hf-timestd ties another radiod on "
                f"this host) while a partner receiver here holds {nights} "
                f"passing night{'s' if nights != 1 else ''} in a row")
        reason = force_reason.strip()
        if not reason:
            raise MigrateRefused(f"{rule}; --force needs --reason TEXT")
        last = verdicts[-1] if verdicts and isinstance(verdicts[-1], dict) \
            else {}
        if last.get("status") != "insufficient":
            raise MigrateRefused(
                f"{rule}; {key}'s latest check reads "
                f"{last.get('status') or 'nothing'}, not \"insufficient\"")
        partners = [p["key"] for p in _states(etc_root)
                    if p.get("key") != key
                    and _passing_run(p.get("verdicts")) >= nights]
        if not partners:
            raise MigrateRefused(f"{rule}; no partner receiver on this host "
                                 f"holds them")
        if record:
            st["forced"] = reason
            st["forced_partner"] = partners[0]
            _write_state(etc_root, key, st)
    t = RetireTarget(key, etc_root)
    cfg, env = _codar_files(etc_root, key)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return [
        Step("disable", f"disable and stop {codar_unit(key)}",
             ("systemctl", "disable", "--now", codar_unit(key)), t),
        Step("aside", f"move {cfg} and {env} aside to *.retired-{stamp}, "
             f"after a backup; /var/lib/{CODAR} stays", (), t),
        Step("topology", f"disable {CODAR} in {topology_path(etc_root)} "
             f"once no {CODAR} instance remains", (), t),
        Step("manifest", "render the uploader pipelines again", _MANIFEST, t),
        Step("sink", "let chirp-recorder's held rows ship (sink upload)",
             ("hs-uploader", "sink", "set", CHIRP, "upload",
              "--reason", RETIRE_REASON, "--by", RETIRE_BY), t),
        Step("state", f"record retired_at in {state_path(etc_root, key)}",
             (), t),
    ]


def _sink_found(run: Runner) -> "tuple[Optional[dict], str]":
    """(chirp-recorder's switch as `hs-uploader sink status --json` reads
    it, why not)."""
    argv = ("hs-uploader", "sink", "status", "--json")
    rc, out, err = _call(run, argv)
    try:
        doc = json.loads(out)
    except ValueError:
        doc = None
    if rc != 0 or not isinstance(doc, dict) or doc.get("schema") != 1:
        why = (err or out).strip().splitlines()
        return None, (f"{' '.join(argv)} (exit {rc}) could not read the "
                      f"switches" + (f": {why[0]}" if why else ""))
    for c in doc.get("clients") or []:
        if isinstance(c, dict) and c.get("client") == CHIRP:
            return {"file_state": c.get("file_state"),
                    "effective": c.get("effective")}, ""
    return {"file_state": "missing", "effective": None}, ""


def _move_aside(paths: list, stamp: str, etc_root: Path, say) -> Optional[str]:
    """Back up, then rename each existing path to ``*.retired-<stamp>``
    within its directory, by descriptor, where only root can write that
    directory.  A path already aside (a resumed run) is skipped.  None on
    success, else why it stopped."""
    var_root = Path(etc_root).parent / "var"
    todo = []
    for p in paths:
        new = f"{p.name}.retired-{stamp}"
        try:
            pfd = os.open(p.parent, _im._DIR_FLAGS)
        except FileNotFoundError:
            continue
        except OSError as exc:
            return f"could not open {p.parent}: {exc.strerror}; move {p} by hand"
        try:
            st = os.fstat(pfd)
            if st.st_uid != _im._trusted_uid() or st.st_mode & 0o022:
                return (f"refusing to move {p}: others than root can write "
                        f"{p.parent}; move it aside by hand")
            here = there = True
            try:
                os.stat(p.name, dir_fd=pfd, follow_symlinks=False)
            except FileNotFoundError:
                here = False
            try:
                os.stat(new, dir_fd=pfd, follow_symlinks=False)
            except FileNotFoundError:
                there = False
        finally:
            os.close(pfd)
        if here and there:
            return f"{p.parent / new} exists already; move {p} aside by hand"
        if here:
            todo.append((p, new))
        elif there:
            say(f"{p} is aside already ({new})")
    if not todo:
        return None
    try:
        backup = _im._backup([p for p, _ in todo], var_root,
                             datetime.now(timezone.utc).strftime(
                                 "%Y%m%dT%H%M%SZ"), Path(etc_root))
    except (OSError, JournalError) as exc:
        return f"backup failed: {exc}"
    say(f"backed up to {backup}")
    for p, new in todo:
        try:
            _im._rename_at(p, new)
        except OSError as exc:
            return f"could not move {p}: {exc.strerror}"
        say(f"{p} -> {new}")
    return None


def _codar_left(etc_root: Path) -> list:
    shared = shared_config(CODAR, etc_root)
    return [e.key for e in existing_instances(CODAR, etc_root)
            if Path(etc_root) / CODAR / f"{e.key}.toml" != shared]


def apply_retire(root, *, key: str, nights: int = 1,
                 force_reason: Optional[str] = None,
                 run: Runner = default_run, dry_run: bool = False,
                 say: Callable[[str], None] = print) -> int:
    """Refusals, the switch read, then `retire_steps` in order.  ``root``
    stands for ``/``.  0 when the receiver retired (or had already)."""
    etc_root = Path(root) / "etc"
    try:
        k = _key(key)
    except MigrateRefused as exc:
        say(f"FAILED: {exc}")
        return 1
    lock = None
    try:
        if not dry_run:
            lock = _lock(etc_root)
        return _retire(etc_root, k, nights, force_reason, run, dry_run, say)
    except MigrateRefused as exc:
        say(f"FAILED: {exc}")
        say("refused; nothing changed")
        return 1
    finally:
        if lock is not None:
            os.close(lock)


def _retire(etc_root, key, nights, force_reason, run, dry_run, say) -> int:
    from .topology import load_topology, set_component_enabled
    st = read_state(etc_root, key)
    if st is not None and st.get("retired_at"):
        say(f"{key}: codar-sounder retired already ({st['retired_at']}); "
            f"nothing to do")
        return 0
    steps = retire_steps(key, nights=nights, force_reason=force_reason,
                         root=Path(etc_root).parent, record=not dry_run)
    say(f"{key}: retire codar-sounder" + (" (dry run)" if dry_run else ""))
    if dry_run:
        say("  0. read chirp-recorder's sink switch and record it")
        say("       hs-uploader sink status --json")
        for s in steps:
            say(f"  {RETIRE_STEP[s.phase]}. {s.text}")
            if s.argv:
                say(f"       {' '.join(s.argv)}")
        say("dry run; nothing changed")
        return 0
    found, why = _sink_found(run)
    if found is None:
        raise MigrateRefused(why)
    if found["file_state"] == "off":
        raise MigrateRefused(
            f"chirp-recorder's sink switch reads off, which an operator set; "
            f"retire would raise it to upload.  Set it to fill or upload "
            f"first (hs-uploader sink set chirp-recorder fill --reason TEXT "
            f"--by NAME), or leave codar-sounder running")
    st = read_state(etc_root, key)
    if "sink_before_retire" not in st:
        st["sink_before_retire"] = dict(found, read_at=_iso(
            datetime.now(timezone.utc)))
        _write_state(etc_root, key, st)
    say(f"chirp-recorder's sink switch reads {found['file_state']} "
        f"(effective {found['effective']}); recorded")

    def stop(msg: str) -> int:
        say(f"FAILED: {msg}")
        say(f"stopped; what ran stays.  Run `smd admin chirp retire {key}` "
            f"again to finish: each step resumes")
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    for s in steps:
        if s.phase == "aside":
            why = _move_aside(_codar_files(etc_root, key), stamp, etc_root,
                              say)
            if why:
                return stop(why)
            continue
        if s.phase == "topology":
            left = _codar_left(etc_root)
            path = topology_path(etc_root)
            if left:
                say(f"{CODAR} stays in topology: {', '.join(left)} remain")
                continue
            try:
                if load_topology(path).is_enabled(CODAR):
                    set_component_enabled(CODAR, False, path)
                    say(f"{CODAR} disabled in {path}: no instance remains")
            except (OSError, ValueError) as exc:
                return stop(f"{s.text}: {exc}")
            continue
        if s.phase == "state":
            st = read_state(etc_root, key)
            st["retired_at"] = _iso(datetime.now(timezone.utc))
            try:
                _write_state(etc_root, key, st)
            except (OSError, MigrateRefused) as exc:
                return stop(f"{s.text}: {exc}")
            say(s.text)
            continue
        rc, out, err = _call(run, s.argv)
        if rc != 0:
            for line in (out + err).strip().splitlines():
                say(f"    {line}")
            return stop(f"{' '.join(s.argv)} (exit {rc})")
        say(s.text)
    return 0
