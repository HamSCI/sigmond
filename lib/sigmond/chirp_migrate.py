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
   still owes), and a receiver that already holds a chirp-recorder config.
2. `chirp-recorder config init --from-codar` writes the receiver's config
   from codar-sounder's own file.
3. `chirp-recorder validate --json` reports the capacity admission.  When
   it refuses any channel, convert prints the reasons, removes the file
   step 2 wrote, and stops.  codar-sounder keeps running as it was.
4. `hs-uploader sink set chirp-recorder fill`: chirp-recorder stores its
   rows and sends none during the side-by-side night.
5. `smd enable chirp-recorder` records the client in topology, and
   `smd admin uploader manifest --write` renders its pipelines.  The
   manifest draws its pipelines from the clients topology enables.
6. `systemctl enable --now chirp-recorder@KEY.service`, then the state file
   `/var/lib/sigmond/chirp-migrate/KEY.json`, which Task 22 reads.

A failure before the unit starts removes the config step 2 wrote, so a
second run starts clean.  A receiver whose state file exists is done, and a
second run does nothing.  codar-sounder is never stopped, restarted or
edited here; radiod is never touched.

Every command runs through the injectable ``run(argv) -> (rc, output)``.
``etc_root`` (or ``root``, standing for ``/``) lets tests use a scratch
tree.  Stdlib only.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .instance import (InvalidReporterId, display_reporter_id, instance_key,
                       parse_user_reporter_id, systemd_escape)
from .instance_move import existing_instances
from .instance_split import (Step, config_radiods, config_reporter,
                             is_reporter_key, shared_config)

STATE_DIR = Path("/var/lib/sigmond/chirp-migrate")
CODAR = "codar-sounder"
CHIRP = "chirp-recorder"
SINK_REASON = "side by side with codar-sounder"
SINK_BY = "smd admin chirp convert"

Runner = Callable[..., "tuple[int, str]"]

# Where each bare program name in a Step resolves when the real system runs
# it.  Steps keep the bare names so `--dry-run` reads as the brief writes it.
_PROGRAMS = {
    CHIRP: "/usr/local/bin/chirp-recorder",
    "smd": "/usr/local/bin/smd",
}


def default_run(argv, env: Optional[dict] = None) -> "tuple[int, str]":
    """Run one Step's argv.  hs-uploader runs through the daemon's own
    interpreter, as every hs-uploader call sigmond makes does."""
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
        return 127, str(exc)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


class MigrateRefused(Exception):
    """A migration step that must not run; the verb prints it and exits 1.
    Task 22's `retire_steps` raises it."""


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


def state_path(etc_root: Path, key: str) -> Path:
    """`/var/lib/sigmond/chirp-migrate/<KEY>.json` beside ``etc_root``."""
    root = Path(etc_root).parent
    return root / STATE_DIR.relative_to("/") / f"{key}.json"


def chirp_config(etc_root: Path, key: str) -> Path:
    return Path(etc_root) / CHIRP / f"{key}.toml"


def chirp_unit(key: str) -> str:
    return f"{CHIRP}@{systemd_escape(key)}.service"


def codar_unit(key: str) -> str:
    return f"{CODAR}@{systemd_escape(key)}.service"


# --------------------------------------------------------------------------
# targets
# --------------------------------------------------------------------------

def _split_first(name: str) -> str:
    return (f"{CODAR}@{name} is keyed by its radiod, not a reporter: run "
            f"`smd admin instance split codar-sounder` first")


def convert_targets(etc_root: Path, run: Runner = default_run
                    ) -> "tuple[list[ConvertTarget], list[str]]":
    """(targets, refusals) over every codar-sounder instance under
    ``etc_root``.  A receiver already converted (its state file exists)
    appears in neither list.  Reads files only."""
    etc_root = Path(etc_root)
    targets: list = []
    refused: list = []
    shared = shared_config(CODAR, etc_root)
    if shared.exists() and config_radiods(shared):
        refused.append(f"{shared} still names "
                       f"{', '.join(config_radiods(shared))}: run "
                       f"`smd admin instance split codar-sounder` first")
    for e in existing_instances(CODAR, etc_root, run=run):
        cfg = etc_root / CODAR / f"{e.key}.toml"
        if cfg == shared:
            continue
        if not is_reporter_key(e.key):
            refused.append(_split_first(e.key))
            continue
        if state_path(etc_root, e.key).exists():
            continue
        rid = config_reporter(cfg)
        if not rid:
            refused.append(f"{cfg} has no [instance] reporter_id; run "
                           f"`smd admin instance split codar-sounder` first")
            continue
        try:
            display = display_reporter_id(parse_user_reporter_id(rid))
            rkey = instance_key(rid)
        except (InvalidReporterId, ValueError) as exc:
            refused.append(f"{cfg}: reporter_id {rid!r}: {exc}")
            continue
        if rkey != e.key:
            refused.append(f"{CODAR}@{e.key} records reporter {display}, "
                           f"whose instance is {rkey}: run `smd admin "
                           f"receiver apply` to move it first")
            continue
        radiods = config_radiods(cfg)
        radiod = e.radiod or (radiods[0] if len(radiods) == 1 else "")
        if not radiod:
            refused.append(f"{cfg} names "
                           f"{', '.join(radiods) if radiods else 'no radiod'};"
                           f" a receiver's instance reads one")
            continue
        mine = chirp_config(etc_root, e.key)
        if mine.exists():
            refused.append(f"{mine} exists already; move it aside so convert "
                           f"writes it from {cfg.name}")
            continue
        targets.append(ConvertTarget(e.key, display, radiod, cfg))
    return targets, refused


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------

def convert_steps(t: ConvertTarget) -> list:
    """Steps 2-6 for one receiver, in order.  The last carries no argv:
    `apply` writes the state file there."""
    return [
        Step("config", f"write {chirp_config(_etc_of(t), t.key)} from "
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
        Step("enable", "enable chirp-recorder in topology",
             ("smd", "enable", CHIRP), t),
        Step("manifest", "render chirp-recorder's uploader pipelines",
             ("smd", "admin", "uploader", "manifest", "--write"), t),
        Step("start", f"enable and start {chirp_unit(t.key)}",
             ("systemctl", "enable", "--now", chirp_unit(t.key)), t),
        Step("state", f"record the conversion in "
             f"{state_path(_etc_of(t), t.key)}", (), t),
    ]


def _capacity_refusals(output: str) -> "tuple[list[str], list[str]]":
    """(refusal lines, admitted channel names) from `validate --json`.  An
    answer that cannot be read refuses."""
    try:
        doc = json.loads(output)
    except ValueError:
        return [f"validate --json printed no JSON: "
                f"{output.strip()[:200] or 'nothing'}"], []
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


def _write_state(path: Path, t: ConvertTarget, channels: list) -> None:
    doc = {"key": t.key, "reporter_id": t.reporter_id, "radiod": t.radiod,
           "started_at": datetime.now(timezone.utc).strftime(
               "%Y-%m-%dT%H:%M:%SZ"),
           "codar_unit": codar_unit(t.key), "channels": list(channels),
           "verdict": None, "retired_at": None}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, path)


def apply(steps: list, run: Runner = default_run,
          say: Callable[[str], None] = print) -> int:
    """Run `convert_steps` output in order; 0 when every step succeeded.

    Stops at the first failure.  Until the unit starts, a failure removes
    the config the first step wrote; codar-sounder is never touched."""
    written: Optional[Path] = None
    channels: list = []

    def undo() -> None:
        if written is not None:
            try:
                written.unlink()
                say(f"removed {written}")
            except OSError:
                pass
        say(f"nothing else changed; {CODAR} still runs")

    for s in steps:
        t = s.target
        if s.phase == "state":
            try:
                _write_state(state_path(_etc_of(t), t.key), t, channels)
            except OSError as exc:
                say(f"FAILED: {s.text}: {exc}")
                return 1
            say(s.text)
            continue
        rc, out = run(list(s.argv))
        if s.phase == "validate":
            why, channels = _capacity_refusals(out)
            if why:
                say(f"FAILED: {CHIRP}@{t.key} cannot record every channel "
                    f"{CODAR} records:")
                for w in why:
                    say(f"    {w}")
                undo()
                return 1
            say(f"capacity admits {', '.join(channels)}")
            continue
        if rc != 0:
            say(f"FAILED: {' '.join(s.argv)} (exit {rc})")
            for line in out.strip().splitlines():
                say(f"    {line}")
            if s.phase != "start":
                undo()
            return 1
        if s.phase == "config":
            written = chirp_config(_etc_of(t), t.key)
            if not written.exists():
                say(f"FAILED: {' '.join(s.argv)} wrote no {written}")
                written = None
                undo()
                return 1
        say(s.text)
    return 0


def apply_convert(root: Path, *, key: Optional[str] = None,
                  run: Runner = default_run, dry_run: bool = False,
                  say: Callable[[str], None] = print) -> int:
    """Targets, steps and apply in one call.  ``root`` stands for ``/``;
    ``key`` (any spelling of a reporter id) limits the run to one receiver.
    Returns 0 when every target converted, or none needed it."""
    etc_root = Path(root) / "etc"
    targets, refused = convert_targets(etc_root, run=run)
    want = None
    if key:
        try:
            want = instance_key(key)
        except (InvalidReporterId, ValueError) as exc:
            say(f"FAILED: {key!r}: {exc}")
            return 1
        targets = [t for t in targets if t.key == want]
        # A station not yet split refuses whichever receiver is named.
        refused = [r for r in refused
                   if want in r or "instance split" in r]
    for r in refused:
        say(f"FAILED: {r}")
    if refused:
        say("refused; nothing changed")
        return 1
    if not targets:
        if want and state_path(etc_root, want).exists():
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
            for i, s in enumerate(steps, 2):
                say(f"  {i}. {s.text}")
                if s.argv:
                    say(f"       {' '.join(s.argv)}")
            continue
        if apply(steps, run=run, say=say) != 0:
            rc = 1
    if dry_run:
        say("dry run; nothing changed")
    return rc
