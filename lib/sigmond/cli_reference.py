"""The CLI reference: every client program's command tree, as JSON files.

`smd admin cli-reference build` walks each program the clients install and
writes /var/lib/sigmond/cli-reference/<program>.json.  `show` reads those
files back, marks the ones whose program has moved on since, and prints
them; the `cli-reference` snapshot hands that to station-web.

sigmond finds a client's programs only from the client's own deploy.toml:
each [[install.steps]] of kind "link" whose `src` sits in a venv's bin/
directory is one program.  No client is named here.

A walk runs cli_walk.py by path inside the client's venv.  It never runs a
command.  As root it runs as `nobody`, with no environment beyond PATH and
LANG, cwd `/`, and a 30 s limit.  A failed walk is recorded in the program's
file; it does not fail the build.  smd walks itself in process, from the
parser bin/smd builds, because running smd from inside smd would re-enter it.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Callable, NamedTuple, Optional

DEFAULT_OUT = Path("/var/lib/sigmond/cli-reference")
DEFAULT_BASE = Path("/opt/git/sigmond")
CLI_WALK = Path(__file__).with_name("cli_walk.py")
SMD_CHECKOUT = Path(__file__).resolve().parents[2]
TIMEOUT_S = 30
VERSION_TIMEOUT_S = 5
SCHEMA = 1

_VERSION_CODE = (
    "import importlib.metadata as m, sys\n"
    "for ep in m.entry_points(group='console_scripts'):\n"
    "    if ep.name == sys.argv[1]:\n"
    "        print(m.version(ep.dist.name))\n"
    "        break\n"
)


class Program(NamedTuple):
    name: str
    component: str
    python: Optional[Path]
    script: str


def discover_programs(base: Path, catalog: dict) -> list:
    """Programs the installed components declare, plus smd itself."""
    base = Path(base)
    found: dict = {}
    for comp in sorted(catalog):
        deploy = base / comp / "deploy.toml"
        try:
            with open(deploy, "rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        steps = (data.get("install") or {}).get("steps") or []
        for step in steps:
            if not isinstance(step, dict) or step.get("kind") != "link":
                continue
            src = step.get("src")
            if not isinstance(src, str) or not src:
                continue
            path = Path(src)
            if not path.is_absolute():
                path = base / comp / path
            if path.parent.name != "bin" or path.parent.parent.name not in ("venv", ".venv"):
                continue
            python = path.parent / "python"
            if not python.exists():
                continue
            dst = step.get("dst")
            name = Path(dst).name if isinstance(dst, str) and dst else path.name
            found.setdefault(name, Program(name, comp, python, path.name))
    programs = [found[n] for n in sorted(found)]
    programs.append(Program("smd", "sigmond", None, "smd"))
    return programs


def run_bounded(cmd: list, *, timeout: float, **kw) -> "subprocess.CompletedProcess":
    """subprocess.run, except the child leads its own process group and a
    timeout kills the whole group, so nothing the program forked outlives
    the limit.  Raises TimeoutExpired after the group is dead and reaped."""
    capture = kw.pop("capture_output", False)
    if capture:
        kw["stdout"] = kw["stderr"] = subprocess.PIPE
    with subprocess.Popen(cmd, start_new_session=True, **kw) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.communicate()
            raise
        except BaseException:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _runuser() -> str:
    for cand in ("/usr/sbin/runuser", "/sbin/runuser", "/usr/bin/runuser"):
        if os.path.exists(cand):
            return cand
    return shutil.which("runuser") or "/usr/sbin/runuser"


def _command(python: Path, tail: list) -> list:
    cmd = [str(python), "-I"] + tail
    if os.geteuid() == 0:
        cmd = [_runuser(), "-u", "nobody", "--"] + cmd
    return cmd


def _run_kwargs(timeout: float = TIMEOUT_S) -> dict:
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    if os.environ.get("LANG"):
        env["LANG"] = os.environ["LANG"]
    return dict(capture_output=True, text=True, timeout=timeout, cwd="/", env=env)


def smd_version() -> str:
    """smd's version: the short git HEAD of the checkout smd runs from, so a
    pull marks the record stale.  The package version when git cannot say."""
    from sigmond import __version__
    try:
        proc = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(SMD_CHECKOUT),
             "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=VERSION_TIMEOUT_S, cwd="/")
    except (subprocess.SubprocessError, OSError):
        return __version__
    sha = (proc.stdout or "").strip()
    return sha if proc.returncode == 0 and sha else __version__


def installed_version(python: Optional[Path], script: str, *,
                      run: Callable = run_bounded) -> Optional[str]:
    """The version of the distribution that provides console script `script`,
    read in that program's own venv.  None when it cannot be read."""
    if python is None:
        return smd_version()
    cmd = _command(python, ["-c", _VERSION_CODE, script])
    try:
        proc = run(cmd, **_run_kwargs(VERSION_TIMEOUT_S))
    except (subprocess.SubprocessError, OSError):
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    out = (proc.stdout or "").strip()
    return out or None


def _failed(name: str, message: str) -> dict:
    return {"schema": SCHEMA, "program": name, "version": None, "entry": None,
            "commands": [], "error": message}


def _walk_in_venv(prog: Program, run: Callable) -> dict:
    cmd = _command(prog.python, [str(CLI_WALK), "--script", prog.script,
                                  "--program", prog.name])
    try:
        proc = run(cmd, **_run_kwargs())
    except subprocess.TimeoutExpired:
        return _failed(prog.name, f"walk timed out after {TIMEOUT_S} s")
    except OSError as exc:
        return _failed(prog.name, f"walk could not start: {exc}")
    try:
        result = json.loads(proc.stdout)
        if not isinstance(result, dict):
            raise ValueError("not an object")
    except (ValueError, TypeError):
        tail = ((getattr(proc, "stderr", "") or "").strip().splitlines() or [""])[-1]
        return _failed(prog.name, "walker printed no JSON"
                       + (f": {tail[:200]}" if tail else ""))
    result.setdefault("program", prog.name)
    result.setdefault("commands", [])
    result.setdefault("error", None)
    return result


def _smd_parser() -> "object":
    """smd's parser, loaded from bin/smd when the caller passes none."""
    import importlib.machinery
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "bin" / "smd"
    had = "SIGMOND_NO_VENV_REEXEC" in os.environ
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    try:
        loader = importlib.machinery.SourceFileLoader("smd_cli_reference", str(path))
        spec = importlib.util.spec_from_loader("smd_cli_reference", loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
    finally:
        if not had:
            os.environ.pop("SIGMOND_NO_VENV_REEXEC", None)
    return module._build_parser()


def _walk_smd(build_parser: Optional[Callable]) -> dict:
    from sigmond.cli_walk import walk_parser
    result = {"schema": SCHEMA, "program": "smd", "version": smd_version(),
              "entry": "smd:_build_parser", "commands": [], "error": None}
    try:
        result["commands"] = walk_parser((build_parser or _smd_parser)(), "smd")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _write_atomic(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _is_own_record(path: Path) -> bool:
    """True for a file this tool wrote: schema 1 and a matching program."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return (isinstance(data, dict) and data.get("schema") == SCHEMA
            and data.get("program") == path.stem)


def _collapse_twins(walked: list) -> list:
    """Programs of one component that walked to the same entry point are one
    program under several names (hf-timestd and timestd).  Keep the name that
    equals the component, else the first alphabetically; list the others as
    `aliases`.  A failed walk has no entry, so it never collapses."""
    groups: dict = {}
    for prog, record in walked:
        entry = record.get("entry")
        if record.get("error") is None and entry:
            groups.setdefault((prog.component, entry), []).append(prog.name)
    keeper: dict = {}
    for (component, _entry), names in groups.items():
        names = sorted(names)
        keep = component if component in names else names[0]
        for n in names:
            keeper[(component, n)] = (keep, [x for x in names if x != keep])
    out = []
    for prog, record in walked:
        keep, aliases = keeper.get((prog.component, prog.name), (prog.name, []))
        if keep != prog.name:
            continue
        record["aliases"] = aliases
        out.append((prog, record))
    return out


def build(out_dir: Path = DEFAULT_OUT, *, run: Callable = run_bounded,
          now: Callable = time.time, base: Path = DEFAULT_BASE,
          catalog: Optional[dict] = None,
          smd_parser: Optional[Callable] = None) -> list:
    """Walk every program and write its file.  One result per program."""
    out_dir = Path(out_dir)
    if catalog is None:
        from sigmond.catalog import load_catalog
        try:
            catalog = load_catalog()
        except (OSError, ValueError):
            catalog = {}
    programs = discover_programs(base, catalog)
    created = not out_dir.exists()
    out_dir.mkdir(parents=True, exist_ok=True)
    if created:
        os.chmod(out_dir, 0o755)
    built_at = datetime.datetime.fromtimestamp(
        now(), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    walked = []
    for prog in programs:
        if prog.python is None:
            record = _walk_smd(smd_parser)
        else:
            record = _walk_in_venv(prog, run)
        record.update({"component": prog.component, "script": prog.script,
                       "python": str(prog.python) if prog.python else None,
                       "built_at": built_at})
        walked.append((prog, record))
    walked = _collapse_twins(walked)
    programs = [prog for prog, _ in walked]
    results = []
    for prog, record in walked:
        error = record.get("error")
        try:
            _write_atomic(out_dir / f"{prog.name}.json", record)
        except OSError as exc:
            error = error or f"could not write: {exc}"
        results.append({"program": prog.name, "ok": error is None, "error": error})
    keep = {f"{p.name}.json" for p in programs}
    for old in out_dir.glob("*.json"):
        if old.name not in keep and _is_own_record(old):
            try:
                old.unlink()
            except OSError:
                pass
    return results


def show(out_dir: Path = DEFAULT_OUT, *, run: Callable = run_bounded) -> dict:
    """The stored files, each marked stale or not.  Runs no walk."""
    programs = []
    for path in sorted(Path(out_dir).glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        python = record.pop("python", None)
        script = record.get("script") or record.get("program") or path.stem
        installed = installed_version(Path(python) if python else None, script, run=run)
        # Stale means built before the program moved; a failed walk has no
        # version to compare, and a rebuild would not make it current.
        record["stale"] = (record.get("error") is None
                           and record.get("version") is not None
                           and installed is not None
                           and installed != record["version"])
        record.setdefault("component", "")
        record.setdefault("built_at", "")
        programs.append(record)
    return {"schema": SCHEMA, "programs": programs}
