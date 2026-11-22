"""`smd admin chirp convert`: a receiver's codar-sounder instance gains a
chirp-recorder instance beside it.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import instance_move as _im
from .instance import (InvalidReporterId, display_reporter_id, instance_key,
                       parse_user_reporter_id, systemd_escape)
from .instance_move import JournalError, existing_instances
from .instance_split import (Step, config_radiods, config_reporter,
                             is_reporter_key, shared_config)

STATE_DIR = Path("/var/lib/sigmond/chirp-migrate")
CODAR = "codar-sounder"
CHIRP = "chirp-recorder"
SINK_REASON = "side by side with codar-sounder"
SINK_BY = "smd admin chirp convert"
_LOCK = ".lock"
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
    or group or other write."""
    var_root = Path(etc_root).parent / "var"
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


def _scan(etc_root: Path, run: Runner) -> "tuple[list, list]":
    """(targets, [(key or None, refusal)]).  A None key refuses for the
    whole station."""
    from . import station_role
    etc_root = Path(etc_root)
    targets: list = []
    refused: list = []
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
    except MigrateRefused as exc:
        refused.append((None, str(exc)))
        return targets, refused
    for e in existing_instances(CODAR, etc_root, run=run):
        cfg = etc_root / CODAR / f"{e.key}.toml"
        if cfg == shared:
            continue
        if not is_reporter_key(e.key):
            refused.append((None, f"{CODAR}@{e.key} is keyed by its radiod, "
                                  f"not a reporter: {_SPLIT_FIRST}"))
            continue
        try:
            if read_state(etc_root, e.key) is not None:
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
    return targets, refused


def convert_targets(etc_root: Path, run: Runner = default_run
                    ) -> "tuple[list[ConvertTarget], list[str]]":
    """(targets, refusals) over every codar-sounder instance under
    ``etc_root``.  A receiver already converted (its state file exists)
    appears in neither list.  Reads files only."""
    targets, refused = _scan(etc_root, run)
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
            "verdict": None, "retired_at": None}


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
                "this run records, and the next convert sets it again)")
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
    targets, refused = _scan(etc_root, run)
    if want:
        targets = [t for t in targets if t.key == want]
        refused = [(k, m) for k, m in refused if k in (want, None)]
    for _, m in refused:
        say(f"FAILED: {m}")
    if refused:
        say("refused; nothing changed")
        return 1
    if not targets:
        if want and read_state(etc_root, want) is not None:
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
