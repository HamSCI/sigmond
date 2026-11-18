"""`smd admin instance split <client>`: one instance per receiver for
codar-sounder and hfdl-recorder.

A receiver (antenna + RX888 + radiod) names every row it produces; a
transporter (AC0G-B2, KFS-MAIN) that runs these clients for receivers on
another machine only sends the upload
(tasks/transporter-reporters/2026-11-18-design.md, part 3).  Before the
split, both clients ran one ``<client>@<radiod status>`` per radiod named in
their shared ``<client>-config.toml``, and their rows carried no reporter.
After it, each receiver in the site profile's ``[[receiver]]`` list gets
``<client>@<KEY>`` (KEY = the reporter id with ``/`` and ``=`` turned to
``-``: AC0G/B1A -> AC0G-B1A) reading ``/etc/<client>/<KEY>.toml``, which
the client's own ``config init`` writes with ``[instance] reporter_id`` and
that receiver's radiod alone.

`build_plan` reads the files and returns what the split would do; `steps`
turns a plan and the units' live state into the ordered changes; `apply`
runs them.  The order protects the station: every per-receiver config
comes first, and one refused ``config init`` removes what this run wrote
and stops before any legacy unit is touched.  Then the legacy units stop,
the shared config moves aside (so lifecycle's config-radiods expansion
names no radiod-keyed unit again), the new units start, and hfdl's
squitter timers follow.  radiod is never touched.  A second run finds
nothing to do.
"""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .instance import (InvalidReporterId, display_reporter_id, instance_key,
                       parse_user_reporter_id, systemd_escape,
                       unescape_systemd_instance, validate_reporter_id)

#: The clients this verb splits, and the timer template each instance
#: carries ('' for none).
SPLIT_CLIENTS = {
    "codar-sounder": "",
    "hfdl-recorder": "hfdl-recorder-squitters@{}.timer",
}
#: Only codar-sounder's config init takes --max-centres.
MAX_CENTRES_CLIENTS = frozenset({"codar-sounder"})
_PLACEHOLDER_PREFIX = "<"

# (argv, env) -> (returncode, combined output)
Runner = Callable[..., "tuple[int, str]"]


def default_run(argv: list, env: Optional[dict] = None) -> "tuple[int, str]":
    try:
        p = subprocess.run(argv, capture_output=True, text=True, check=False,
                           env=env)
    except OSError as exc:
        return 127, str(exc)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _key(rid: str) -> Optional[str]:
    try:
        return parse_user_reporter_id(rid)
    except (InvalidReporterId, ValueError):
        return None


def is_reporter_key(name: str) -> bool:
    """A unit instance or file stem spelled as a reporter key (AC0G-B1A),
    not as a radiod status name (ac0g-b1-a-status.local) or ``default``."""
    try:
        validate_reporter_id(name)
    except InvalidReporterId:
        return False
    return "=" not in name


def shared_config(client: str, etc_root: Path) -> Path:
    return Path(etc_root) / client / f"{client}-config.toml"


def config_radiods(path: Path) -> list:
    """Real radiod status names in a config's [[radiod]] blocks (the
    install-time placeholder left out), in file order."""
    try:
        data = tomllib.loads(Path(path).read_text())
    except (OSError, tomllib.TOMLDecodeError, UnicodeError):
        return []
    blocks = data.get("radiod") or []
    if isinstance(blocks, dict):
        blocks = [blocks]
    out: list = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        st = str(b.get("status") or b.get("radiod_status") or "").strip()
        if st and not st.startswith(_PLACEHOLDER_PREFIX) and st not in out:
            out.append(st)
    return out


def config_reporter(path: Path) -> str:
    try:
        data = tomllib.loads(Path(path).read_text())
    except (OSError, tomllib.TOMLDecodeError, UnicodeError):
        return ""
    inst = data.get("instance")
    return str(inst.get("reporter_id") or "") if isinstance(inst, dict) else ""


@dataclass(frozen=True)
class Target:
    """One receiver's instance."""
    reporter_id: str       # display form, AC0G/B1A
    key: str               # AC0G-B1A
    radiod: str
    config: Path
    unit: str              # codar-sounder@AC0G-B1A.service
    timer: str             # hfdl-recorder-squitters@AC0G-B1A.timer, or ''
    config_ok: bool        # its file already exists and names this receiver


@dataclass
class Plan:
    client: str
    targets: list = field(default_factory=list)
    skipped: list = field(default_factory=list)     # (reporter, why)
    legacy_units: list = field(default_factory=list)
    legacy_timers: list = field(default_factory=list)
    shared: Optional[Path] = None                   # to move aside
    shared_aside: Optional[Path] = None
    problems: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return bool(self.problems)


def _aside_path(shared: Path, today: str) -> Path:
    base = shared.with_name(f"{shared.name}.pre-instances-{today}")
    p, n = base, 1
    while p.exists():
        p = base.with_name(f"{base.name}.{n}")
        n += 1
    return p


def build_plan(client: str, receivers: list, *, station_id: str = "",
               etc_root: Path = Path("/etc"), known_instances=(),
               force: bool = False, today: Optional[str] = None) -> Plan:
    """What `split` would do for ``client`` on this station.

    ``receivers`` is the site profile's [[receiver]] list.  ``station_id``
    is the station's own reporter id (``[station] uploader_call``, else its
    reporter id); one receiver carrying it leaves nothing to split unless
    ``force``.  ``known_instances`` names the ``<client>@*.service``
    instances systemd knows (enabled or loaded), unescaped.  Reads files;
    writes nothing."""
    plan = Plan(client=client)
    if client not in SPLIT_CLIENTS:
        plan.problems.append(
            f"{client}: split handles {' and '.join(sorted(SPLIT_CLIENTS))} "
            f"only (psk-recorder, wspr-recorder and meteor-scatter already "
            f"run one instance per receiver)")
        return plan
    etc_root = Path(etc_root)
    timer_tpl = SPLIT_CLIENTS[client]

    # Which receivers get an instance: every one on a radiod.  A receiver
    # sharing another's antenna hears the same signals through the same
    # radiod; a second instance would record the same channels twice.
    chosen: list = []
    for r in receivers:
        if r.kiwisdr or not r.radiod:
            plan.skipped.append((r.reporter_id, "no radiod"))
        elif r.same_antenna_as:
            plan.skipped.append(
                (r.reporter_id, f"shares {r.same_antenna_as}'s antenna"))
        else:
            chosen.append(r)
    if not receivers:
        plan.problems.append("site-profile.toml declares no [[receiver]]; "
                             "nothing names the receivers to split into")
        return plan
    if not chosen:
        plan.problems.append("no [[receiver]] on a radiod of its own")
        return plan

    sk = _key(station_id) if station_id else None
    if (len(chosen) == 1 and sk is not None
            and _key(chosen[0].reporter_id) == sk and not force):
        plan.problems.append(
            f"one receiver, {chosen[0].reporter_id}, and it carries the "
            f"station's own id: nothing to split (--force splits anyway)")

    keys: set = set()
    for r in chosen:
        try:
            key = instance_key(r.reporter_id)
        except InvalidReporterId as exc:
            plan.problems.append(f"{r.reporter_id}: {exc}")
            continue
        if key in keys:
            plan.problems.append(f"{r.reporter_id}: two receivers share the "
                                 f"instance key {key}")
            continue
        keys.add(key)
        cfg = etc_root / client / f"{key}.toml"
        ok = False
        if cfg.exists():
            owner = config_reporter(cfg)
            have = config_radiods(cfg)
            if owner and _key(owner) != _key(r.reporter_id):
                plan.problems.append(
                    f"{cfg} belongs to {owner}, not {r.reporter_id}; "
                    f"move it aside by hand")
            elif [h.lower() for h in have] != [r.radiod.lower()]:
                plan.problems.append(
                    f"{cfg} records {', '.join(have) or 'no radiod'}, not "
                    f"{r.reporter_id}'s {r.radiod}; fix or remove it by hand")
            elif not owner:
                plan.problems.append(
                    f"{cfg} has no [instance] reporter_id; remove it by hand "
                    f"so config init can write it")
            else:
                ok = True
        plan.targets.append(Target(
            reporter_id=display_reporter_id(parse_user_reporter_id(
                r.reporter_id)),
            key=key, radiod=r.radiod, config=cfg,
            unit=f"{client}@{systemd_escape(key)}.service",
            timer=timer_tpl.format(systemd_escape(key)) if timer_tpl else "",
            config_ok=ok))

    # The legacy side: the shared config's radiods, and every other unit
    # instance systemd knows that is no reporter key with its own file
    # (radiod-keyed ones, and hfdl's stray @default).
    shared = shared_config(client, etc_root)
    radiods = config_radiods(shared) if shared.exists() else []
    if radiods:
        plan.shared = shared
        plan.shared_aside = _aside_path(
            shared, today or datetime.now(timezone.utc).strftime("%Y%m%d"))
        covered = {t.radiod.lower() for t in plan.targets}
        lost = [d for d in radiods if d.lower() not in covered]
        if lost:
            msg = (f"{shared.name} records {', '.join(lost)}, which no "
                   f"receiver names; the split would stop recording "
                   f"{'it' if len(lost) == 1 else 'them'}")
            if force:
                plan.notes.append(msg + " (--force)")
            else:
                plan.problems.append(msg + "; declare the receiver first, "
                                     "or --force")
        new = [t.radiod for t in plan.targets
               if t.radiod.lower() not in {d.lower() for d in radiods}]
        for d in new:
            plan.notes.append(f"{d}: not recorded by {shared.name} today; "
                              f"the split starts recording it")
    legacy: list = []
    for name in list(radiods) + sorted(
            unescape_systemd_instance(i) for i in known_instances):
        if name in keys or name in legacy:
            continue
        if is_reporter_key(name) and (etc_root / client / f"{name}.toml").exists():
            plan.notes.append(f"{client}@{name}: a per-receiver instance no "
                              f"receiver names; left as it is")
            continue
        legacy.append(name)
    plan.legacy_units = [f"{client}@{systemd_escape(n)}.service" for n in legacy]
    plan.legacy_timers = ([timer_tpl.format(systemd_escape(n)) for n in legacy]
                          if timer_tpl else [])
    return plan


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    phase: str          # config | retire | aside | start | timer
    text: str
    argv: tuple = ()
    target: Optional[Target] = None


def unit_state(unit: str, run: Runner = default_run) -> "tuple[bool, bool]":
    """(enabled, active) for one unit."""
    _, en = run(["systemctl", "is-enabled", unit])
    _, ac = run(["systemctl", "is-active", unit])
    return en.strip() == "enabled", ac.strip() == "active"


def known_instances(client: str, run: Runner = default_run) -> list:
    """Instances of ``<client>@*.service`` systemd has loaded (not dead)
    or enabled, unescaped."""
    pat = f"{client}@*.service"
    names: set = set()
    _, out = run(["systemctl", "list-units", pat, "--no-legend", "--plain",
                  "--no-pager"])
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0].startswith(f"{client}@"):
            names.add(parts[0])
    _, out = run(["systemctl", "list-unit-files", pat, "--no-legend",
                  "--no-pager"])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "enabled":
            names.add(parts[0])
    inst = set()
    for u in names:
        i = unescape_systemd_instance(u.split("@", 1)[1].rsplit(".", 1)[0])
        if i:
            inst.add(i)
    return sorted(inst)


def config_init_argv(base: list, client: str, t: Target,
                     max_centres: Optional[int] = None) -> list:
    """The client's own config init for one receiver, all by arguments:
    smd's sudo elevation drops environment variables."""
    argv = [*base, "--non-interactive", "--instance", t.key,
            "--reporter-id", t.reporter_id, "--radiod-status", t.radiod]
    if max_centres is not None and client in MAX_CENTRES_CLIENTS:
        argv += ["--max-centres", str(max_centres)]
    return argv


def steps(plan: Plan, *, state: Callable[[str], "tuple[bool, bool]"],
          init_base: list, max_centres: Optional[int] = None) -> list:
    """The ordered changes ``plan`` still needs, given each unit's live
    (enabled, active) ``state``.  Empty when the split is done."""
    out: list = []
    for t in plan.targets:
        if not t.config_ok:
            out.append(Step("config", f"write {t.config} for {t.reporter_id} "
                            f"on {t.radiod}",
                            tuple(config_init_argv(init_base, plan.client, t,
                                                   max_centres)), t))
    for unit in plan.legacy_timers + plan.legacy_units:
        en, ac = state(unit)
        if en or ac:
            out.append(Step("retire", f"stop and disable {unit}",
                            ("systemctl", "disable", "--now", unit)))
    if plan.shared is not None:
        out.append(Step("aside", f"move {plan.shared} to "
                        f"{plan.shared_aside.name}"))
    for t in plan.targets:
        en, ac = state(t.unit)
        if not (en and ac):
            out.append(Step("start", f"enable and start {t.unit}",
                            ("systemctl", "enable", "--now", t.unit), t))
    for t in plan.targets:
        if t.timer:
            en, ac = state(t.timer)
            if not (en and ac):
                out.append(Step("timer", f"enable and start {t.timer}",
                                ("systemctl", "enable", "--now", t.timer), t))
    return out


def _mirror_owner(src: Path, dst: Path) -> None:
    """Give a new per-receiver file the shared config's owner and mode, so
    the service user reads it as it read the shared one.  Best effort."""
    import os
    try:
        st = src.stat()
        os.chown(dst, st.st_uid, st.st_gid)
        os.chmod(dst, st.st_mode & 0o7777)
    except OSError:
        pass


def apply(plan: Plan, todo: list, *, run: Runner = default_run,
          env: Optional[dict] = None, say: Callable[[str], None] = print,
          manifest: Optional[Callable[[], int]] = None) -> int:
    """Run ``todo`` (from `steps`) in order; 0 when every step succeeded.

    ⛔ A refused config init removes the files this run wrote and returns
    before any legacy unit stops: the station keeps recording as it was.
    After the units move, ``manifest`` (the uploader manifest write) runs
    once; a failure there is reported, the instances stay split."""
    if plan.refused:
        return 1
    written: list = []
    for s in [s for s in todo if s.phase == "config"]:
        rc, out = run(list(s.argv), env=env)
        if rc != 0 or not s.target.config.exists():
            say(f"FAILED: {' '.join(s.argv)} (exit {rc})")
            for line in out.strip().splitlines():
                say(f"    {line}")
            for p in written:
                try:
                    p.unlink()
                except OSError:
                    pass
            if written:
                say("removed what this run wrote: "
                    + ", ".join(str(p) for p in written))
            say("nothing else changed; the legacy units still run")
            return 1
        written.append(s.target.config)
        if plan.shared is not None:
            _mirror_owner(plan.shared, s.target.config)
        say(f"wrote {s.target.config}")
    rc_all = 0
    for s in todo:
        if s.phase == "config":
            continue
        if s.phase == "aside":
            try:
                plan.shared.rename(plan.shared_aside)
            except OSError as exc:
                say(f"FAILED: {s.text}: {exc}")
                return 1          # never start new units beside the old config
            say(s.text)
            continue
        rc, out = run(list(s.argv))
        if rc != 0:
            say(f"FAILED: {s.text} (exit {rc})")
            for line in out.strip().splitlines():
                say(f"    {line}")
            if s.phase == "retire":
                # A legacy unit still running beside its replacement would
                # open every channel twice.  Stop here.
                return 1
            rc_all = 1
            continue
        say(s.text)
    for s in todo:
        if s.phase == "retire":
            # A unit that failed before it stopped stays listed; clear it
            # so lifecycle discovery forgets it.
            run(["systemctl", "reset-failed", s.argv[-1]])
    if manifest is not None:
        if manifest() != 0:
            say("FAILED: the uploader manifest write; run "
                "`smd admin uploader manifest --write`")
            rc_all = 1
    return rc_all
