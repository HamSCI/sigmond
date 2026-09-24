"""What an alignment made stale, and the order to restart it — pure.

A running unit is stale when it started before the code it runs moved:
its own checkout's HEAD, or an editable sibling it imports (ka9q-python,
hamsci-dsp, …). radiod runs a BUILT binary, so its "moved" time is the
binary's mtime, not its checkout's. Every time comes from the station
itself (reflog, systemd, mtime), so a later run finds restarts an earlier
run left undone. No I/O here; bin/smd reads the station and calls these.
"""
from typing import Iterable, Mapping, Optional

RADIOD = "ka9q-radio"          # the checkout; its service component is radiod
RADIOD_GAP_ESTIMATE_S = 60     # restart + sigmond-radiod-ready, as printed in the dry run


def stale_components(services: Iterable[str], moved_at: Mapping[str, Optional[float]],
                     started_at: Mapping[str, Optional[float]],
                     consumes: Mapping[str, Iterable[str]]) -> dict:
    """{component: reason} for every running service whose code moved after it started.

    ``started_at[c]`` is the EARLIEST start among c's active units (None when
    none is active); ``moved_at[x]`` is when x's code last moved (None unknown).
    """
    out = {}
    for name in sorted(services):
        start = started_at.get(name)
        if start is None:
            continue
        reasons = []
        own = moved_at.get(name)
        if own is not None and own > start:
            reasons.append("its checkout moved")
        for lib in sorted(consumes.get(name, ())):
            t = moved_at.get(lib)
            if t is not None and t > start:
                reasons.append(f"{lib} moved")
        if reasons:
            out[name] = "; ".join(reasons) + " after it started"
    return out


def predicted_restarts(*, moving: Iterable[str], running: Iterable[str],
                       consumes: Mapping[str, Iterable[str]],
                       radiod_consumers: Iterable[str]) -> tuple:
    """(radiod_restarts, [components]) an --apply of ``moving`` would restart — for the dry run.

    radiod restarts when ka9q-radio moves, and then every running radiod
    consumer follows it. Otherwise: each running component that moves, and
    each running component that imports a moving library. radiod itself is
    never in the list; the bool carries it.
    """
    moving, running = set(moving), set(running)
    radiod = RADIOD in moving and "radiod" in running
    names = set()
    if radiod:
        names |= set(radiod_consumers) & running
    for c in running - {"radiod"}:
        if c in moving or set(consumes.get(c, ())) & moving:
            names.add(c)
    return radiod, sorted(names)
