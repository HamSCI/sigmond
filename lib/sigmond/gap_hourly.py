"""Hourly radiod block-drop (gap_count) sampler.

Promoted from B4's live, uncommitted ``/usr/local/sbin/gap-hourly.sh`` (run
manually via ``gap-hourly.timer`` since before this repo tracked it) into
sigmond proper, so the only durable honest gap record in the fleet stops
depending on one host's un-versioned crontab.

Appends one TSV row per hour to ``/var/log/gap-hourly.tsv``:
    utc  gaps  channel_hours  gaps_per_ch_hr  grape_running
(header line ``# utc\\tgaps\\tchannel_hours\\tgaps_per_ch_hr\\tgrape_running``
written on first creation).

``gap_count`` is the ONLY honest loss field -- radiod zero-fills dropped
blocks, so ``samples_written`` / ``completeness_pct`` both read 100% while
data is genuinely missing.  Each gap costs up to +/-25.6 s of GRAPE
spectrogram validity, so COUNT matters far more than duration.

Baselines: 3.53 = radiod on core 0 | 0.68 = off core 0 | 0.00 = off core 0
           + L3 CAT 13/3
           15.60-20.82 = guest kernel isolation (harmful, reverted
           2026-08-18)

Data source: JSON sidecars under ``/var/lib/timestd/raw_buffer/
<channel>/<day>/*.json`` with ``minute_boundary`` (epoch sec) and
``gap_count`` fields.  Window = trailing 3600 s; channel_hours =
n_sidecars*300/3600 (each sidecar covers a 300 s / 5 min span).

The one behavior change vs. the .orig: the .orig wrote rate 0.00 when
channel_hours == 0 -- a lying zero.  When the window contains NO
sidecars, ``summarise_window`` now returns the honest "not measured"
fields (``gaps`` and ``gaps_per_ch_hr`` literal ``"NA"``, never 0)
instead of a healthy-looking 0.00.  This is a cross-repo contract: a
later heartbeat assembler parses the last data row of this TSV and
treats literal ``"NA"`` as "not measured" -- keep the column set and
header exactly as shipped here.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Sequence, Union

BASE_DEFAULT = "/var/lib/timestd/raw_buffer"
OUT_DEFAULT = "/var/log/gap-hourly.tsv"
WINDOW_SEC = 3600
# Each raw_buffer sidecar covers a 300s (5 min) span; n_sidecars*300/3600
# gives the trailing-window's channel-hours (mirrors the .orig exactly).
SIDECAR_SPAN_SEC = 300

HEADER = "# utc\tgaps\tchannel_hours\tgaps_per_ch_hr\tgrape_running"

# bracket trick so this pattern cannot match our own helper process.
GRAPE_PGREP_PATTERN = "[h]f_timestd.cli grape daily"

Record = Union[Dict[str, Any], "tuple[Any, Any]"]


def summarise_window(records: Iterable[Record], now: int,
                      window_sec: int = WINDOW_SEC) -> Dict[str, str]:
    """Pure summary of ``records`` falling in the trailing ``window_sec``.

    ``records`` is an iterable of either dicts with ``minute_boundary`` /
    ``gap_count`` keys (``gap_count`` defaulting to 0 if absent, matching
    the .orig's ``j.get('gap_count', 0)``), or ``(minute_boundary,
    gap_count)`` tuples.

    Returns a dict of already-formatted row fields: ``gaps``,
    ``channel_hours`` (``"%.2f"``), ``gaps_per_ch_hr``.  When no record
    falls in the window, ``gaps`` and ``gaps_per_ch_hr`` are the literal
    string ``"NA"`` (an honest "not measured" row) and ``channel_hours``
    is ``"0.00"`` -- never a lying 0.00 rate.
    """
    start = now - window_sec
    gaps = 0
    n_sidecars = 0
    for rec in records:
        if isinstance(rec, dict):
            boundary = rec.get("minute_boundary")
            gap_count = rec.get("gap_count", 0)
        else:
            boundary, gap_count = rec
        if boundary is None:
            continue
        try:
            boundary = int(boundary)
        except (TypeError, ValueError):
            continue
        if gap_count is None:
            gap_count = 0
        if start <= boundary < now:
            gaps += gap_count
            n_sidecars += 1

    channel_hours = n_sidecars * SIDECAR_SPAN_SEC / 3600
    if channel_hours == 0:
        return {"gaps": "NA", "channel_hours": "0.00", "gaps_per_ch_hr": "NA"}

    rate = gaps / channel_hours
    return {
        "gaps": gaps,
        "channel_hours": f"{channel_hours:.2f}",
        "gaps_per_ch_hr": f"{rate:.2f}",
    }


def collect_records(base: str, now: int) -> Iterator[Dict[str, Any]]:
    """Walk ``base``'s ``<channel>/<day>/*.json`` sidecars, soft-failing
    unreadable/unparseable files exactly like the .orig's bare
    ``try: json.load(...) except Exception: continue``.

    ``now`` is accepted for parity with ``summarise_window``'s contract
    (and future day-window pruning); the .orig doesn't filter by day
    here -- it walks the last 2 day-directories per channel unconditionally
    and lets the trailing-window filter in ``summarise_window`` do the
    real work.  Yields the parsed JSON object for each sidecar so callers
    decide which fields they need.
    """
    del now  # unused (see docstring) -- kept for signature parity.
    if not os.path.isdir(base):
        return
    for channel in sorted(os.listdir(base)):
        channel_dir = os.path.join(base, channel)
        if not os.path.isdir(channel_dir):
            continue
        for day in sorted(os.listdir(channel_dir))[-2:]:
            day_dir = os.path.join(channel_dir, day)
            if not os.path.isdir(day_dir):
                continue
            for f in glob.glob(os.path.join(day_dir, "*.json")):
                try:
                    with open(f) as fh:
                        record = json.load(fh)
                except Exception:
                    continue
                if isinstance(record, dict):
                    yield record


def append_row(path: Union[str, Path], fields: Sequence[str]) -> None:
    """Append one TSV row to ``path``, writing ``HEADER`` first iff the
    file doesn't exist yet."""
    path = Path(path)
    is_new = not path.exists()
    with open(path, "a") as fh:
        if is_new:
            fh.write(HEADER + "\n")
        fh.write("\t".join(str(f) for f in fields) + "\n")


def grape_running() -> int:
    """Count of running ``hf_timestd.cli grape daily`` processes.

    Uses the .orig's bracket trick (``[h]f_timestd...``) so pgrep's own
    process (which execs with the literal pattern as an argv) never
    matches itself.
    """
    try:
        proc = subprocess.run(
            ["pgrep", "-cf", GRAPE_PGREP_PATTERN],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    out = proc.stdout.strip()
    try:
        return int(out)
    except ValueError:
        return 0


def build_row(base: str = BASE_DEFAULT, now: int | None = None) -> List[str]:
    """Assemble one full TSV row (utc, gaps, channel_hours,
    gaps_per_ch_hr, grape_running) as already-formatted strings."""
    if now is None:
        now = int(time.time())
    records = collect_records(base, now)
    summary = summarise_window(records, now)
    utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    return [
        utc,
        str(summary["gaps"]),
        summary["channel_hours"],
        str(summary["gaps_per_ch_hr"]),
        str(grape_running()),
    ]


def main(argv: List[str] | None = None) -> int:
    del argv  # no CLI args, matches the .orig
    fields = build_row()
    append_row(Path(OUT_DEFAULT), fields)
    print("\t".join(fields))
    return 0


if __name__ == "__main__":
    sys.exit(main())
