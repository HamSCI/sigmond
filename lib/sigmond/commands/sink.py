"""`smd sink off|upload|status` -- the site sink switch.

tasks/plan-sink-control.md §10.3 item 1: v3.69 ships the site form only, as a
thin wrapper over the legacy `[uploads] mode` handler (`off` writes discard,
`upload` writes upload).  `fill` waits for step 2 of §10.2, when it truly keeps
data: the legacy hold it would ride on keeps FT8 spots for one hour and WSPR
spots for 24 hours.  The per-client form, `smd sink <client>`, waits for step 5.
"""
from __future__ import annotations

import types

from ..ui import err, info, ok, warn
from . import config as cfg

FILL_NOT_YET = (
    '`smd sink fill` is not available yet.  The legacy hold it would use keeps FT8 '
    'spots for one hour and WSPR spots for 24 hours, so it cannot keep data for '
    'later sending.  Use `smd sink off` (send no data, keep no backlog) or '
    '`smd sink upload` (store and send).')


def cmd_sink(args) -> int:
    verb = getattr(args, 'sink_command', None) or 'status'
    if verb == 'status':
        return _status()
    if verb == 'fill':
        err(FILL_NOT_YET)
        return 2
    legacy_verb = {'off': 'discard', 'upload': 'on'}[verb]
    return cfg.cmd_config_uploads(types.SimpleNamespace(
        uploads_command=legacy_verb,
        reason=getattr(args, 'reason', None),
        yes=getattr(args, 'yes', False),
        sink_words=True))


def _status() -> int:
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
