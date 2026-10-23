"""`smd sink off|fill|upload|status` -- the site sink switch.

tasks/spec-sink-2b-part2.md §7: each verb sets the site switch, through
`cmd_config_uploads`, the one handler the legacy words share (`smd upload`,
`smd config uploads`).  With an hs-uploader that offers the feature word
`gate`, it writes `/etc/hs-uploader/sinks/site.toml` and the `[uploads]`
mirror together (D19); without it, `[uploads]` alone, as part 1 did, where
`fill` rides on the legacy hold.  The per-client form, `smd sink <client>`,
waits for step 5 (D18); the parser refuses it.
"""
from __future__ import annotations

import types
from typing import Optional

from .. import sink_state
from ..ui import err, info, ok, warn
from . import config as cfg


def cmd_sink(args) -> int:
    verb = getattr(args, 'sink_command', None) or 'status'
    if verb == 'status':
        return _status()
    legacy_verb = {'off': 'discard', 'fill': 'hold', 'upload': 'on'}[verb]
    return cfg.cmd_config_uploads(types.SimpleNamespace(
        uploads_command=legacy_verb,
        reason=getattr(args, 'reason', None),
        yes=getattr(args, 'yes', False),
        sink_words=True))


def _status() -> int:
    """Spec §7.5: the switch files once the marker stands, ``[uploads]``
    before (and when hs-uploader cannot say what the switches read)."""
    rc = switch_status()
    if rc is None:
        return _uploads_status()
    if rc == FELL_BACK:
        _uploads_status()
        return 1
    return rc


# switch_status()'s answer when it could not read the switches and the
# caller must show [uploads] (after the line saying so) and exit 1.
FELL_BACK = -1

# What each site state does, in part 1's words where part 1 had them.
_SITE_WORDS = {'upload': 'store and send',
               'fill': 'store and send nothing',
               'off': 'nothing ships and nothing new is stored for upload'}

_HELD_BY = {'site': 'set by the site switch',
            'client': 'set by its own switch',
            'fault': 'set by a fault, which reads off'}


def switch_status() -> Optional[int]:
    """What `smd sink status` and `smd upload status` show once the
    carry-over marker stands (spec §7.5; Ruling 12): hs-uploader's own
    reading of the switches, through ``sink status --json``.

    Returns None while the marker stands absent: the switches mean nothing
    yet, so the caller reads ``[uploads]`` and this prints nothing.
    Returns FELL_BACK after one line saying the read failed (every run
    says it again; Ruling 19).  Otherwise prints the site switch, each
    client's file and effective state and the switch that sets it, the
    held pipelines, the refused files, each in-process sender still armed
    below ``upload`` (UNMANAGED), each stale writer and any mirror
    mismatch, and returns 1 when any of those is a fault.

    Reads only: ``sink status`` and ``systemctl show``, as the calling
    user.  Nothing here writes, restarts or elevates."""
    from . import uploader as up_cmd
    roots = up_cmd._step_roots()
    if not sink_state.marker_stands(roots.marker):
        return None
    relayed: list = []
    with up_cmd._relayed_stderr(relayed):
        state = sink_state.read_state(
            python=roots.python, manifest=str(roots.manifest),
            sinks=str(roots.sinks), marker=str(roots.marker),
            store=None if roots.store is None else str(roots.store))
    if state is None:
        err('cannot read hs-uploader sink status ('
            f'{up_cmd._cause(relayed) or "no reason given"}); falling back to '
            '[uploads], which only mirrors the site sink switch')
        return FELL_BACK
    for ln in relayed:
        warn(ln)
    rc = _show_switches(state)
    rc |= _show_unmanaged(state, roots, up_cmd)
    _show_stale(roots, up_cmd)
    rc |= _show_mirror(roots)
    info('the heartbeat still goes out; the site sink switch never stops it')
    return rc


def _show_switches(state) -> int:
    rc = 0
    site = state.site
    if site.state in ('missing', 'error'):
        err(f'site sink: cannot read the site switch ({site.state}'
            + (f': {site.error}' if site.error else '')
            + '), so every client reads off')
        rc = 1
    else:
        # The part 1 phrase first, whole: the nested rig greps for
        # 'site sink: upload (store and send)' (Task 21 review I-1).
        words = _SITE_WORDS.get(site.state)
        line = (f'site sink: {site.state}' + (f' ({words})' if words else '')
                + (f'; set {site.set_at}' if site.set_at else '')
                + (f' by {site.set_by}' if site.set_by else '')
                + (f' — {site.reason}' if site.reason else ''))
        (ok if site.state == 'upload' else warn)(line)
    if not state.enforced:
        err('hs-uploader does not enforce the switches: it finds no '
            f'carry-over marker at {state.marker or "its marker path"}')
        rc = 1
    for name in sorted(state.clients):
        c = state.clients[name]
        line = (f'{name}: its file reads {c.file_state}'
                + (f' ({c.error})' if c.error else '')
                + f'; effective {c.effective}')
        if c.held_by:
            line += ', ' + _HELD_BY.get(c.held_by, f'set by {c.held_by}')
        (info if c.held_by != 'fault' else err)(line)
        if c.held_by == 'fault':
            rc = 1
    if state.held_pipelines:
        info('pipelines held: ' + ', '.join(
            f'{h.pipeline} ({h.held_by})' for h in state.held_pipelines))
    if state.no_client_pipelines:
        info('pipelines no switch governs (no client named): '
             + ', '.join(state.no_client_pipelines))
    for e in state.errors:
        err(f'hs-uploader could not read everything: {e}')
    for r in state.refused_files:
        what = r.file or (f'pipeline {r.pipeline}' if r.pipeline else 'a file')
        err(f'refused: {what}'
            + (f' ({r.client})' if r.client else '') + f': {r.reason}')
    if state.partial:
        warn('refusals unknown: hs-uploader could not read everything, so '
             'the list of refused switch files may be incomplete')
        rc = 1
    elif not state.refused_files:
        info('refused switch files: none')
    else:
        rc = 1
    return rc


def _topology(roots):
    from ..topology import load_topology
    return load_topology(roots.topology)


def _show_unmanaged(state, roots, up_cmd) -> int:
    """Each in-process sender still armed for a client that reads below
    upload: it sends past the daemon's gate (spec §8)."""
    from .. import sink_levers
    below = sink_state.below_upload(state)
    if not below:
        return 0
    sc = up_cmd._lever_systemctl
    try:
        plan = sink_levers.plan_levers(
            below, _topology(roots),
            roots=sink_levers.Roots(etc=roots.etc, writer_py=roots.writer_py),
            systemctl=sc)
        armed = sink_levers.verify_levers(plan, systemctl=sc)
    except Exception as exc:
        err(f'cannot check the in-process senders ({type(exc).__name__}: '
            f'{exc})')
        return 1
    for line in armed:
        if _UNREAD in line:
            # verify_levers could not read this one: it may or may not
            # send, so it is no UNMANAGED finding, but still a fault.
            err(f'cannot check: {line}')
        else:
            err(f'UNMANAGED: {line}; it sends outside the sink switches '
                'until `smd align` lowers it')
    return 1 if armed else 0


# verify_levers' lines for an instance it could not read say ": cannot ..."
# ("cannot read its environment", "cannot read its lever file or start
# time", "cannot enumerate its instances", "cannot read its state").
_UNREAD = ': cannot '


def _show_stale(roots, up_cmd) -> None:
    """Each recorder still running a writer older than the installed one:
    under off it would store rows (spec §4.3).  A warning, not a fault:
    the change to off restarts it first."""
    from .. import sink_levers
    notes: list = []
    try:
        units = sink_levers.stale_writer_units(
            _topology(roots), writer_py=roots.writer_py,
            systemctl=up_cmd._lever_systemctl, notes=notes)
    except Exception as exc:
        warn(f'cannot check for stale writers ({type(exc).__name__}: {exc})')
        return
    for unit in units:
        warn(f'stale writer: {unit} started before {roots.writer_py} last '
             'changed; it restarts before the site switch next reads off')
    for note in notes:
        warn(f'stale writer: {note}')


def _show_mirror(roots) -> int:
    """``[uploads]`` against ``site.toml`` (spec §7.2): what the manifest
    step would repair, said and left alone."""
    from .. import sink_mirror
    try:
        site_path, site, uploads = sink_mirror.read_inputs(
            roots.sinks, cfg.COORDINATION_PATH, cfg.SITE_PROFILE_PATH)
    except Exception as exc:
        err(f'cannot compare [uploads] with the site switch '
            f'({type(exc).__name__}: {exc})')
        return 1
    plan = sink_mirror.plan_mirror(site_path, site, uploads)
    if plan.action == 'rewrite':
        for path, mode in plan.files:
            err(f'mirror mismatch: {path} reads [uploads] mode = {mode}, but '
                f'the site switch reads {site.state} (mode = {plan.mode}); '
                'the next `smd align` rewrites [uploads] to match.  Set the '
                'site with `smd sink upload|fill|off`')
        return 1
    if plan.action == 'recreate':
        err(f'mirror mismatch: the site switch {site_path} is missing, which '
            'sets no limit; the next `smd align` recreates it at '
            f'{plan.state} from [uploads]')
        return 1
    if plan.action == 'broken':
        for line in plan.lines():
            err(f'mirror mismatch: {line}')
        return 1
    return 0


def _uploads_status() -> int:
    from .. import uploader_manifest as um
    from ..coordination import load_coordination
    coord = load_coordination(cfg.COORDINATION_PATH)
    up = coord.uploads
    why = f' — {up.reason}' if up.reason else ''
    if up.mode == 'upload':
        ok('site sink: upload (store and send)')
    elif up.mode == 'discard':
        warn(f'site sink: off — no data ships and no backlog builds{why}')
        if um.effective_mode(coord) != 'discard':
            err('this host\'s hs-uploader cannot discard, so the manifest renders '
                'the legacy hold instead; update hs-uploader')
    else:
        warn('site sink: hold (legacy) — stores for a while and sends no data; '
             f'FT8 spots last one hour and WSPR spots 24 hours{why}')
        info('`smd sink upload` ships what is still stored; `smd sink off` drops it')
    if up.mode != 'upload':
        try:
            held_back = um.suppressed_pipelines(coord=coord)
        except Exception as exc:  # pragma: no cover - defensive
            held_back = []
            warn(f'could not list the pipelines this affects: {exc}')
        if held_back:
            info('pipelines not sending: ' + ', '.join(held_back))
    info('the heartbeat still goes out; the site sink switch never stops it')
    return 0
