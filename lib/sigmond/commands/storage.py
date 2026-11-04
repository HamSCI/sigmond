"""`smd storage status|share|directive|tick` -- the storage capacity ledger.

docs/superpowers/specs/2026-11-04-storage-capacity-design.md.  `status`
reads the file the tick writes and needs no root.  `share`, `directive` and
`tick` change /etc/sigmond/storage.toml or act on clients, so bin/smd
elevates them first (the `archive add` pattern: smd re-execs itself under
sudo; it refuses a human `sudo smd`).

Every line this module prints stays ASCII.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..storage.config import (CONFIG_PATH, POLICY_RE, load_storage_config, render_storage_toml,
                              with_directive, with_hands_off, with_share,
                              with_stop_lever)

ALLOWED_POLICIES = ("give-up-oldest:pinned", "give-up-oldest:raw",
                    "give-up-oldest:derived", "give-up-oldest:sent",
                    "stop-writing", "refuse-new-rows", "custom:<name>")
HANDS_OFF_DEFAULT = "24h"
_GiB = 1 << 30


def _a(text) -> str:
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def _say(text: str) -> None:
    print(_a(text))


def _complain(text: str) -> None:
    print(_a(f"smd storage: {text}"), file=sys.stderr)


def _status_path(args) -> Path:
    p = getattr(args, "status_path", None)
    if p:
        return Path(p)
    env = os.environ.get("SIGMOND_STORAGE_STATUS")
    if env:
        return Path(env)
    from ..storage.tick import STATUS_PATH
    return STATUS_PATH


def _config_path(args) -> Path:
    return Path(getattr(args, "config_path", None) or CONFIG_PATH)


def cmd_storage(args) -> int:
    verb = getattr(args, "storage_command", None) or "status"
    if verb == "status":
        return _status(args)
    if verb == "share":
        return _share(args)
    if verb == "directive":
        return _directive(args)
    if verb == "tick":
        from ..storage.tick import default_runners, run_tick
        return run_tick(datetime.now(timezone.utc), default_runners())
    _complain(f"unknown verb {verb!r}")
    return 2


# ---------------------------------------------------------------- status

def _gib(n) -> str:
    return "-" if n is None else f"{n / _GiB:.1f} GiB"


def _days(d) -> str:
    """Days of coverage; '-' when no write rate gives a number."""
    return "- days" if d is None else f"{d:.1f} days"


def _churn(n) -> str:
    return "" if not n else f", growing {n / _GiB:.1f} GiB/h"


def _status(args) -> int:
    from ..safe_write import read_nofollow
    path = _status_path(args)
    try:
        text = read_nofollow(path)      # no symlink, regular file, at most 1 MiB
    except FileNotFoundError:
        text = None                     # /run/sigmond itself is absent
    except OSError as exc:
        _complain(f"cannot read {path}: {exc.strerror or exc}")
        return 1
    if text is None:
        _say(f"no ledger tick yet ({path} does not exist)")
        return 0
    if getattr(args, "json", False):
        sys.stdout.write(text)
        return 0
    try:
        st = json.loads(text)
    except ValueError as exc:
        _complain(f"{path} is not JSON: {exc}")
        return 1
    _say(f"storage ledger, tick at {st.get('generated_at')}: worst stage {st.get('worst_stage')}")
    if st.get("fault"):
        _say(f"  FAULT  {st['fault']}")
    for s in st.get("stores", []):
        _say(f"  store  {s['name']} ({s['kind']}): holds {_days(s.get('days'))}, "
             f"{s['used_pct']:.1f}% {s['stage']}, "
             f"{_gib(s['used'])} of {_gib(s['capacity'])}, allottable {_gib(s.get('allottable'))}"
             + _churn(s.get("churn_bytes_per_h")))
    for c in st.get("clients", []):
        how = "managed" if c.get("managed") else "unmanaged"
        stop = ", STOPPED by the storage ledger" if c.get("stopped_by_storage") else ""
        _say(f"  client {c['client']} on {c['store']}: holds {_days(c.get('days_held'))} "
             f"({_gib(c.get('held'))}) at {_gib(c.get('rate_bytes_per_day'))}/day "
             f"({c.get('rate_source') or 'none'}), "
             f"target {_gib(c.get('target'))}, allotment {_gib(c.get('allotment'))}, "
             f"floor {_gib(c.get('floor'))}, {how}{stop}")
    for al in st.get("alarms", []):
        _say(f"  alarm  {al['severity']} {al['key']} (since {al.get('since')}): {al['message']}")
    hr = st.get("host_report")
    if hr:
        if not hr.get("expected") and hr.get("age_s") is None:
            _say("  host   no pool report expected (not a VM)")
        elif hr.get("fault"):
            _say(f"  host   pool report {hr['fault']}")
        else:
            _say(f"  host   pool report {hr.get('age_s')} s old")
    lever = st.get("stop_lever")
    if lever in ("on", "off"):
        _say(f"  stop lever {lever}" + ("" if lever == "on" else
             " (the ledger alarms would-stop and stops no client)"))
    ho = st.get("hands_off")
    if ho:
        if ho.get("until") is None:
            _say(f"  hands-off UNKNOWN, so the ledger stops no client: {ho.get('reason')}")
        else:
            _say(f"  hands-off until {ho['until']}: {ho.get('reason') or '(no reason given)'}")
    for p, b in st.get("unclaimed", []):
        _say(f"  unclaimed {p}: {_gib(b)}")
    for prob in st.get("config_problems", []):
        _say(f"  config {prob}")
    return 0


# ---------------------------------------------------------------- writers

def _load_for_edit(path: Path):
    """The config to edit, or an exit code: 1 when the file will not read
    or parse, 2 when its [hands_off] table does not parse (a rewrite would
    drop the mark, and an operator must say what it meant)."""
    cfg = load_storage_config(path)
    fatal = [p for p in cfg.problems if p.startswith(f"{path}:")]
    if fatal:
        _complain(f"refusing to rewrite {path}: {fatal[0]}")
        return 1
    if cfg.hands_off_unknown:
        _complain(f"refusing to rewrite {path}: its [hands_off] table does not parse "
                  f"({cfg.hands_off_unknown}); a rewrite would drop the mark.  Fix or "
                  f"remove the [hands_off] table by hand, then run this again")
        return 2
    for p in cfg.problems:
        _complain(f"note: {p} (it will not be written back)")
    return cfg


def _save(path: Path, cfg) -> int:
    from ..safe_write import write_owned
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_owned(path, render_storage_toml(cfg), mode=0o644)
    except OSError as exc:
        _complain(f"cannot write {path}: {exc}")
        return 1
    return 0


def _share(args) -> int:
    client = getattr(args, "target", None)
    if not client:
        _complain("share needs a client: share <client> <weight> | share --clear <client>")
        return 2
    weight = None
    if not args.clear:
        try:
            weight = float(args.value)
        except (TypeError, ValueError):
            _complain(f"weight {args.value!r} is not a number")
            return 2
        if not weight > 0 or weight != weight or weight == float("inf"):
            _complain(f"weight {args.value!r} must be a positive number")
            return 2
    path = _config_path(args)
    cfg = _load_for_edit(path)
    if isinstance(cfg, int):
        return cfg
    rc = _save(path, with_share(cfg, client, weight))
    if rc == 0:
        _say(f"{client}: share cleared (back to an even split)" if weight is None
             else f"{client}: share weight {weight:g} written to {path}")
    return rc


def _directive(args) -> int:
    target = getattr(args, "target", None)
    if not target:
        _complain("directive needs a client or hands-off")
        return 2
    if target == "hands-off":
        return _hands_off(args)
    if target == "stop-lever":
        return _stop_lever(args)
    policy = None
    if not args.clear:
        policy = getattr(args, "value", None)
        if not policy:
            _complain(f"directive {target} needs a policy, one of: {', '.join(ALLOWED_POLICIES)}")
            return 2
        if not re.match(POLICY_RE, policy):
            _complain(f"unknown policy {policy!r}; allowed: {', '.join(ALLOWED_POLICIES)}")
            return 2
    path = _config_path(args)
    cfg = _load_for_edit(path)
    if isinstance(cfg, int):
        return cfg
    rc = _save(path, with_directive(cfg, target, policy))
    if rc == 0:
        _say(f"{target}: directive cleared (its own final policy applies)" if policy is None
             else f"{target}: final-stage directive {policy} written to {path}")
    return rc


def _stop_lever(args) -> int:
    value = (getattr(args, "value", None) or "").strip().lower()
    if args.clear:
        value = "off"
    if value not in ("on", "off"):
        _complain("directive stop-lever needs on or off")
        return 2
    path = _config_path(args)
    cfg = _load_for_edit(path)
    if isinstance(cfg, int):
        return cfg
    rc = _save(path, with_stop_lever(cfg, value == "on"))
    if rc == 0:
        if value == "on":
            _say(f"stop lever on, written to {path}: at FINAL the ledger may stop a client "
                 f"that fails to answer (never while hands-off)")
        else:
            _say(f"stop lever off, written to {path}: the ledger alarms would-stop "
                 f"and stops no client")
    return rc


def _hands_off(args) -> int:
    from ..storage_trim import parse_duration
    now = datetime.now(timezone.utc)
    until = None
    if not args.clear:
        spec = getattr(args, "for_duration", None) or HANDS_OFF_DEFAULT
        try:
            until = now + timedelta(seconds=parse_duration(spec))
        except ValueError as exc:
            _complain(f"--for: {exc}")
            return 2
    path = _config_path(args)
    cfg = _load_for_edit(path)
    if isinstance(cfg, int):
        return cfg
    try:
        new = with_hands_off(cfg, until, getattr(args, "reason", None) or "", now)
    except ValueError as exc:
        _complain(f"hands-off: {exc}")
        return 2
    rc = _save(path, new)
    if rc == 0:
        if until is None:
            _say("hands-off cleared: the ledger may stop a client that fails to answer at FINAL")
        else:
            _say(f"hands-off until {new.hands_off_until:%Y-%m-%dT%H:%M:%SZ}: the ledger "
                 f"alarms but stops no client")
    return rc
