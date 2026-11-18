"""Drift items for the build kind.

Three checks, all reusing what sigmond already records:

* a component whose deploy.toml declares a ``[build]`` carries a stamp; a
  stale stamp means its venv came from other code than its checkout holds.
  Components with an install.sh own their build and carry no stamp: skipped.
* the UberSDR decoder build: its ``.provenance`` commit against the
  ubersdr_wsprd checkout, and ``--msk144`` in its jt9 while meteor-scatter
  runs.
* the live decoder: wsjtx while a parity-verified ubersdr build exists.

An operator override of the decoder choice suppresses the live-decoder and
MSK144 items; the override kind reports it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterable, Mapping, Optional

from sigmond import build_stamp, decoder_choice
from sigmond.drift.model import Item

_APPLY = "smd align --apply"
_UBERSDR_SRC = "ubersdr_wsprd"
_HEX40 = re.compile(r"\b[0-9a-f]{40}\b")


def _short(sha: Optional[str]) -> str:
    return sha[:7] if sha else "none"


def _stamp_items(base: Path, components: Iterable[str],
                 stamp_dir: Optional[Path]) -> list:
    out = []
    for name in components:
        repo = Path(base) / name
        # Same predicate as build_stamp.needs_build: an install.sh owns its
        # build and smd stamps only the no-installer branch.
        if (build_stamp.has_installer(repo)
                or not build_stamp.declares_build(repo)):
            continue
        head = build_stamp.head_of(repo)
        if not build_stamp.is_stale(repo, head=head, directory=stamp_dir):
            continue
        stamp = build_stamp.read_stamp(name, stamp_dir)
        detail = (f"built {_short(stamp)}, checkout at {_short(head)}"
                  if stamp else f"never built; checkout at {_short(head)}")
        out.append(Item("build", "fault", name, detail, _APPLY))
    return out


def _provenance_item(base: Path, decoder_dir: Path) -> Optional[Item]:
    head = build_stamp.head_of(Path(base) / _UBERSDR_SRC)
    ubersdr = Path(decoder_dir) / "ubersdr"
    if head is None or not ubersdr.is_dir():
        return None  # no checkout to compare, or no ubersdr build here
    try:
        m = _HEX40.search((ubersdr / ".provenance").read_text())
    except OSError:
        m = None
    if not m:
        return Item("build", "fault", "ubersdr-decoders",
                    "UberSDR decoders have no provenance commit; "
                    f"checkout at {_short(head)}", _APPLY)
    if m.group(0) != head:
        return Item("build", "fault", "ubersdr-decoders",
                    f"UberSDR decoders built from {_short(m.group(0))}, "
                    f"checkout at {_short(head)}", _APPLY)
    return None


def _msk144_item(decoder_dir: Path, run) -> Optional[Item]:
    jt9 = Path(decoder_dir) / "ubersdr" / "jt9"
    try:
        r = run([str(jt9), "--help"], capture_output=True, text=True,
                timeout=10)
        ok = "--msk144" in ((r.stdout or "") + (r.stderr or ""))
    except (OSError, subprocess.SubprocessError):
        ok = False
    if ok:
        return None
    return Item("build", "fault", "ubersdr-jt9",
                "UberSDR jt9 lacks MSK144; meteor-scatter falls back to "
                "the WSJT-X build", _APPLY)


def _live_item(decoder_dir: Path) -> Optional[Item]:
    if decoder_choice.live() != "wsjtx":
        return None
    if decoder_choice.parity("ubersdr", decoder_dir) != "verified":
        return None
    if not (Path(decoder_dir) / "ubersdr" / "wsprd").exists():
        return None
    return Item("build", "drift", "decoders",
                "wsjtx decoders are live although the UberSDR build "
                "passed its parity check",
                "smd admin decoders --select ubersdr, then smd restart "
                "wspr-recorder psk-recorder meteor-scatter")


def build_items(base: Path, components: Iterable[str], *,
                enabled: Mapping[str, bool],
                decoder_dir: Path = decoder_choice.DECODER_DIR,
                run=subprocess.run,
                stamp_dir: Optional[Path] = None) -> list:
    items = _stamp_items(base, components, stamp_dir)
    held = decoder_choice.operator_override()
    # A wrong-commit build is wrong whichever decoder the operator chose;
    # the override silences only the MSK144 and live-decoder items.
    candidates = [_provenance_item(base, decoder_dir)]
    if not held:
        if enabled.get("meteor-scatter"):
            candidates.append(_msk144_item(decoder_dir, run))
        candidates.append(_live_item(decoder_dir))
    items.extend(i for i in candidates if i is not None)
    return items
