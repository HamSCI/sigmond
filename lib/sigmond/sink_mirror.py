"""Mirror repair: ``[uploads]`` follows the site switch (spec §7.2; plan
Ruling 9).

Once the carry-over marker stands, ``/etc/hs-uploader/sinks/site.toml``
holds the site's sink state, and ``[uploads]`` in coordination.toml and
site-profile.toml only mirrors it (D19), for the readers that still read
``[uploads]``.  ``smd sink`` writes both; when one write fails, or someone
edits ``[uploads]`` by hand (and ``smd config render`` copies
site-profile's block into coordination.toml), the two disagree.  The
manifest step then repairs the mirror, in one direction only:

- ``site.toml`` parses and names a known state: each file whose
  ``[uploads]`` mode differs is rewritten to the mode the switch means
  (``upload`` -> upload, ``fill`` -> hold, ``off`` -> discard), through
  sigmond's own ``[uploads]`` writer, and one line names the edit and
  points at ``smd sink``.  site-profile.toml counts only when it exists and
  declares the block (``smd config render`` copies nothing else).
- ``site.toml`` missing: a missing site file sets no limit (design §3.4),
  so clients would ship under a site the operator lowered.  It is
  recreated from coordination.toml's ``[uploads]``, through
  ``hs-uploader sink set site`` with ``by = "smd (site file missing)"``.
- ``site.toml`` broken: hs-uploader holds everything (fail closed).  Both
  files stay as they stand; the lines name the file and tell the operator
  to remove it and run ``smd sink <state>``; the step exits 1.

:func:`plan_mirror` decides and reads nothing; :func:`read_inputs` reads;
:func:`apply_mirror` writes.  The caller holds the install lock; nothing
here takes it or calls back into the manifest step.  Stdlib only.
"""
from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from .coordination import Uploads, load_coordination
from .safe_write import read_nofollow
from .site_profile import load_site_profile

STATES = ("off", "fill", "upload")
MODE_FOR_STATE = {"upload": "upload", "fill": "hold", "off": "discard"}
STATE_FOR_MODE = {m: s for s, m in MODE_FOR_STATE.items()}
BY_MISSING = "smd (site file missing)"
REASON_MISSING = ("recreated from [uploads] mode = {mode}: the site switch "
                  "file was missing, which sets no limit")


@dataclass(frozen=True)
class SiteRead:
    """What ``site.toml`` says: a state, ``missing`` or ``error``."""
    state: str
    reason: str = ""
    error: str = ""


@dataclass(frozen=True)
class MirrorPlan:
    action: str                 # none, rewrite, recreate or broken
    site_path: Path
    site: SiteRead
    mode: str = ""              # rewrite: the [uploads] mode to write
    state: str = ""             # recreate: the site state to write
    files: tuple = ()           # rewrite: (path, mode it read)
    reason: str = ""            # the reason written with it

    def lines(self) -> list:
        """What the step prints (without its ``uploader: `` prefix)."""
        if self.action == "rewrite":
            read = ", ".join(f"{p} read mode = {m}" for p, m in self.files)
            return [f"[uploads] disagreed with the site sink switch "
                    f"({self.site_path} reads {self.site.state}): {read}; "
                    f"rewrote [uploads] to mode = {self.mode} to match.  "
                    "[uploads] only mirrors the switch, so a hand edit there "
                    "changes nothing; set the site with `smd sink "
                    "upload|fill|off`"]
        if self.action == "recreate":
            return [f"the site sink switch {self.site_path} was missing, "
                    "which sets no limit; recreated it at "
                    f"{self.state} from [uploads] mode = "
                    f"{MODE_FOR_STATE[self.state]} (by {BY_MISSING})"]
        if self.action == "broken":
            return [f"the site sink switch {self.site_path} cannot be read "
                    f"({self.site.error}), so hs-uploader holds every "
                    "pipeline; [uploads] and the file are left as they "
                    f"stand.  Remove {self.site_path}, then run `smd sink "
                    "upload|fill|off`"]
        return []


def read_site(path: Path) -> SiteRead:
    """``site.toml``'s state and reason, read without following a link."""
    try:
        text = read_nofollow(path)
    except FileNotFoundError:
        return SiteRead("missing")
    except OSError as exc:
        return SiteRead("error", error=exc.strerror or str(exc))
    if text is None:
        return SiteRead("missing")
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        return SiteRead("error", error=f"not TOML: {exc}")
    state = data.get("state")
    if state not in STATES:
        return SiteRead("error", error=f"state {state!r} is no known state")
    reason = data.get("reason")
    return SiteRead(state, reason=reason if isinstance(reason, str) else "")


def plan_mirror(site_path: Path, site: SiteRead,
                uploads: Sequence[tuple]) -> MirrorPlan:
    """Decide the repair.  `uploads` lists ``(path, mode)`` for
    coordination.toml first, then site-profile.toml; a mode of ``None``
    means the file declares no ``[uploads]`` block and stays out.  Reads
    nothing and writes nothing."""
    if site.state == "error":
        return MirrorPlan("broken", site_path, site)
    if site.state == "missing":
        mode = uploads[0][1] or "upload"
        state = STATE_FOR_MODE.get(mode, "fill")
        return MirrorPlan("recreate", site_path, site, state=state,
                          reason=REASON_MISSING.format(mode=mode))
    want = MODE_FOR_STATE[site.state]
    files = tuple((p, m) for p, m in uploads if m is not None and m != want)
    if not files:
        return MirrorPlan("none", site_path, site)
    return MirrorPlan("rewrite", site_path, site, mode=want, files=files,
                      reason="" if want == "upload" else site.reason)


def read_inputs(sinks: Path, coordination: Path, profile: Path):
    """``(site_path, SiteRead, uploads)`` for :func:`plan_mirror`."""
    site_path = Path(sinks) / "site.toml"
    uploads = [(Path(coordination), load_coordination(coordination).uploads.mode)]
    prof = load_site_profile(profile) if Path(profile).exists() else None
    if prof is not None and prof.uploads_declared:
        mode = Uploads(enabled=prof.uploads_enabled,
                       mode=prof.uploads_mode).mode
        uploads.append((Path(profile), mode))
    else:
        uploads.append((Path(profile), None))
    return site_path, read_site(site_path), uploads


def apply_mirror(plan: MirrorPlan, *, runner: Callable, sinks: Path,
                 patch: Optional[Callable] = None) -> list:
    """Carry the repair out; returns ``(file, why)`` for each failure.

    ``runner(args)`` runs ``hs-uploader <args>`` (the recreate);
    ``patch(path, Uploads)`` rewrites one file's ``[uploads]`` block
    (default: ``commands.config._patch_uploads_block``, the writer
    ``smd sink`` uses).  ``broken`` and ``none`` write nothing."""
    failures = []
    if plan.action == "recreate":
        args = ["sink", "set", "site", plan.state, "--reason", plan.reason,
                "--by", BY_MISSING, "--sinks", str(sinks)]
        try:
            r = runner(args)
        except (OSError, subprocess.SubprocessError) as exc:
            failures.append((str(plan.site_path),
                             f"{type(exc).__name__}: {exc}"))
        else:
            if r.returncode != 0:
                err = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
                failures.append((str(plan.site_path),
                                 f"hs-uploader sink set exited {r.returncode}: "
                                 f"{err[-1].strip() if err else 'no message'}"))
    elif plan.action == "rewrite":
        if patch is None:
            from .commands.config import _patch_uploads_block as patch
        up = Uploads(mode=plan.mode, reason=plan.reason)
        for path, _ in plan.files:
            try:
                patch(path, up)
            except OSError as exc:
                failures.append((str(path), exc.strerror or str(exc)))
    return failures
