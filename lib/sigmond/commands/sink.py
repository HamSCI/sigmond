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
