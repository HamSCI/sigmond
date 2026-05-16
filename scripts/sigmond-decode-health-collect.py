#!/usr/bin/env python3
"""sigmond-decode-health-collect — hourly trend collector for decode/upload events.

Scrapes per-cycle decode events from psk-recorder + per-pump shipping events
from wspr-recorder's in-process hs-uploader (was wd-upload-hs@.service before
v3 Phase A 2026-05-16) and appends them to
``/var/lib/sigmond/decode_health.db``.
Designed to be cron/timer-driven, idempotent on overlapping windows
(primary key dedupes), and small enough that the sqlite file stays
manageable for years.

Driven by systemd timer:
    sigmond-decode-health-collect.timer   (hourly)
    sigmond-decode-health-collect.service (Type=oneshot)

Query examples once data accumulates:
  -- decode rate per hour for the last week
  SELECT strftime('%Y-%m-%d %H:00', ts) AS hour,
         source, mode,
         CAST(SUM(decodes_ok) AS REAL) / NULLIF(SUM(decodes_total), 0) AS decode_rate,
         SUM(spots) AS total_spots
  FROM cycle_snapshot
  WHERE ts > datetime('now', '-7 days') AND source = 'psk-recorder'
  GROUP BY hour, source, mode
  ORDER BY hour;
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


DEFAULT_DB = Path('/var/lib/sigmond/decode_health.db')
PSK_LOG_GLOB = '/var/log/psk-recorder/*.log'
# v3 Phase A (2026-05-16) moved the uploader in-process from the
# retired wd-upload-hs@.service into wspr-recorder.  We now scan
# wspr-recorder's journal for the same per-pump shipping line that
# the old standalone unit emitted (logger name was updated from
# wdlib.hs_uploader_shim to wspr_recorder.hs_uploader_shim).
UPLOAD_UNIT_GLOB = 'wspr-recorder@*.service'
WSPR_SPOOL_ROOT = Path('/var/spool/wsprdaemon/recording')
DEFAULT_LOOKBACK_HOURS = 2  # safe overlap window for cron @ hourly

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycle_snapshot (
    ts             TEXT    NOT NULL,
    source         TEXT    NOT NULL,
    mode           TEXT    NOT NULL,
    spots          INTEGER,
    decodes_ok     INTEGER,
    decodes_total  INTEGER,
    slots_empty    INTEGER,
    band_counts    TEXT,
    raw_line       TEXT,
    PRIMARY KEY (ts, source, mode)
);

CREATE INDEX IF NOT EXISTS idx_cycle_ts ON cycle_snapshot(ts);
CREATE INDEX IF NOT EXISTS idx_cycle_source_mode ON cycle_snapshot(source, mode);

-- Per-WAV-file capture-health snapshot.  wspr-recorder writes a rich
-- JSON sidecar alongside every WAV (drift, completeness, RTP/wall-clock
-- mapping, GPSDO + chrony state).  We slurp the fields most useful for
-- spotting slow degradation into a flat table; the original JSON stays
-- on disk for forensic deep-dives.
CREATE TABLE IF NOT EXISTS wav_snapshot (
    filename            TEXT    NOT NULL PRIMARY KEY,
    ts                  TEXT    NOT NULL,    -- ISO wallclock_start
    band_name           TEXT,
    frequency_hz        INTEGER,
    sample_rate         INTEGER,
    period_seconds      INTEGER,
    samples             INTEGER,
    total_gaps_filled   INTEGER,
    completeness_pct    REAL,
    drift_delta_ms      REAL,                -- drift at this WAV's close
    drift_cumulative_ms REAL,                -- drift_tracker running total
    gpsdo_locked        INTEGER,             -- 0/1 from radiod
    hf_locked           INTEGER,             -- 0/1 from hf-timestd
    system_offset_ms    REAL,                -- chrony's view of UTC offset
    chrony_stratum      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_wav_ts ON wav_snapshot(ts);
CREATE INDEX IF NOT EXISTS idx_wav_band ON wav_snapshot(band_name);
"""


# ---- parsers -----------------------------------------------------------------

# psk-recorder stats line format (see psk_recorder.core.recorder):
#   INFO:psk_recorder.core.recorder:stats FT8: spots=379 decodes=36/36 slots_empty=0 freqs=10 (60s window)
PSK_STATS_RE = re.compile(
    r'stats\s+(?P<mode>FT[48]):\s+'
    r'spots=(?P<spots>\d+)\s+'
    r'decodes=(?P<dec_ok>\d+)/(?P<dec_total>\d+)\s+'
    r'slots_empty=(?P<slots_empty>\d+)'
)
# Optional band-breakdown variant the psk-watch shows (cycle UTC line, not stats):
#   slot UTC 03:07:00  ft8=133 (80m:7 60m:7 40m:47 30m:17 20m:45 17m:5 15m:5)  ft4= 25 (20m:25)
PSK_CYCLE_RE = re.compile(
    r'slot\s+UTC\s+(?P<utc>\d{2}:\d{2}:\d{2})\s+'
    r'ft8=\s*(?P<ft8>\d+)\s*(?:\((?P<ft8_bands>[^)]*)\))?\s*'
    r'ft4=\s*(?P<ft4>\d+)\s*(?:\((?P<ft4_bands>[^)]*)\))?'
)
# Leading "INFO:" line timestamp form psk-recorder writes via its journal hook:
PSK_TS_RE = re.compile(
    r'^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}:\d{2})(?:[,\.]\d+)?'
)

# hs-uploader shipping line (journal, post-v3-Phase-A 2026-05-16):
#   2026-05-16 02:30:12 INFO wspr_recorder.hs_uploader_shim: wspr-uploader-hs: shipped
#     wsprdaemon=7 wsprnet=900 (total wsprdaemon=2346 wsprnet=9000, work=10)
# The logger-name prefix is matched permissively (``wdlib`` or
# ``wspr_recorder``) so pre-Phase-A archived journals still parse
# cleanly when an operator runs the collector with a wide lookback.
UPLOAD_SHIPPED_RE = re.compile(
    r'(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2}),\d+\s+INFO\s+'
    r'(?:wdlib|wspr_recorder)\.hs_uploader_shim:\s+wspr-uploader-hs:\s+shipped\s+'
    r'wsprdaemon=(?P<wd>\d+)\s+wsprnet=(?P<wn>\d+)'
)


def _parse_band_counts(s: Optional[str]) -> Optional[str]:
    """Convert '80m:7 60m:7 40m:47' → JSON string '{"80m":7,"60m":7,"40m":47}'."""
    if not s:
        return None
    out: dict = {}
    for tok in s.split():
        if ':' not in tok:
            continue
        band, count = tok.split(':', 1)
        try:
            out[band] = int(count)
        except ValueError:
            continue
    return json.dumps(out, separators=(',', ':')) if out else None


# ---- scrapers ----------------------------------------------------------------

def _scrape_psk_log(path: Path, since: datetime) -> Iterator[dict]:
    """Yield event dicts from one psk-recorder log file."""
    try:
        with open(path, 'r', errors='replace') as f:
            for raw in f:
                # psk-recorder log format is two-part: leading INFO: ... message.
                # Recent code writes "stats FT8: ..." as the message; the
                # timestamp is in the leading journal-style prefix if the
                # logger was configured that way, OR absent (in which case
                # we trust the file's recency window and ts the line by now).
                m_ts = PSK_TS_RE.search(raw)
                if m_ts:
                    ts = datetime.fromisoformat(
                        f"{m_ts.group('date')}T{m_ts.group('time')}+00:00"
                    )
                else:
                    # No timestamp in line — best-effort: use file mtime as
                    # a rough anchor.  These records get deduped on (ts,
                    # source, mode), so a small offset is fine.
                    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                if ts < since:
                    continue

                m = PSK_STATS_RE.search(raw)
                if m:
                    yield {
                        'ts':            ts.strftime('%Y-%m-%dT%H:%M:%SZ'),
                        'source':        'psk-recorder',
                        'mode':          m.group('mode').lower(),
                        'spots':         int(m.group('spots')),
                        'decodes_ok':    int(m.group('dec_ok')),
                        'decodes_total': int(m.group('dec_total')),
                        'slots_empty':   int(m.group('slots_empty')),
                        'band_counts':   None,
                        'raw_line':      raw.strip(),
                    }
                    continue
                # Skip cycle/band lines for now — they duplicate spot counts;
                # we already capture totals via stats.  Could capture them
                # separately under mode='ft8-band' if per-band trends matter.
    except FileNotFoundError:
        return


def _scrape_upload_journal(unit_glob: str, since: datetime) -> Iterator[dict]:
    """Yield event dicts from journalctl for the in-process uploader.

    Prior to v3 Phase A (2026-05-16) the uploader lived in
    ``wd-upload-hs@*.service``; from that point on it runs inside
    ``wspr-recorder@*.service``.  The same per-pump "shipped"
    log line is matched in either journal.
    """
    cmd = [
        'journalctl',
        '--since', since.strftime('%Y-%m-%d %H:%M:%S'),
        '-u', unit_glob,
        '--no-pager', '--output=short-iso-precise',
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.error("journalctl invocation failed: %s", e)
        return

    if proc.returncode != 0:
        logger.warning("journalctl rc=%d: %s", proc.returncode, proc.stderr[:200])

    for raw in proc.stdout.splitlines():
        m = UPLOAD_SHIPPED_RE.search(raw)
        if not m:
            continue
        ts = datetime.fromisoformat(
            f"{m.group('date')}T{m.group('time')}+00:00"
        )
        if ts < since:
            continue
        ts_str = ts.strftime('%Y-%m-%dT%H:%M:%SZ')
        # Two pseudo-events per shipping line (wsprdaemon-org + wsprnet)
        # so each upload target gets its own row.  Spots column carries
        # the count; decode_ok/decode_total/slots_empty unused for uploads.
        yield {
            'ts':            ts_str,
            'source':        'wspr-recorder',
            'mode':          'wsprdaemon',
            'spots':         int(m.group('wd')),
            'decodes_ok':    None,
            'decodes_total': None,
            'slots_empty':   None,
            'band_counts':   None,
            'raw_line':      raw.strip(),
        }
        yield {
            'ts':            ts_str,
            'source':        'wspr-recorder',
            'mode':          'wsprnet',
            'spots':         int(m.group('wn')),
            'decodes_ok':    None,
            'decodes_total': None,
            'slots_empty':   None,
            'band_counts':   None,
            'raw_line':      raw.strip(),
        }


def _scrape_wspr_sidecars(root: Path, since: datetime) -> Iterator[dict]:
    """Yield per-WAV-snapshot dicts from wspr-recorder's JSON sidecars.

    Every WAV file produced by wspr-recorder carries a peer ``.json``
    file in the same directory containing the recorder's own view of
    drift, completeness, GPSDO state, and timing.  We flatten the
    fields most useful for trend detection into ``wav_snapshot``.

    Walks ``root/<RX>/<band>/`` looking for ``*.json`` files modified
    after ``since`` — much cheaper than parsing everything every run.
    """
    if not root.exists():
        return
    cutoff = since.timestamp()
    for json_path in root.rglob('*.json'):
        try:
            if json_path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        # Skip the recorder's status file, which is in the recording root,
        # not under a per-band subdir.
        if json_path.name in ('wspr-recorder-status.json',):
            continue
        try:
            data = json.loads(json_path.read_text(errors='replace'))
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("skip %s: %s", json_path, e)
            continue
        try:
            ts_iso = data.get('wallclock_start') or data.get('written_at')
            if not ts_iso:
                continue
            # Normalise to "YYYY-MM-DDTHH:MM:SSZ" — strip the +00:00
            # offset wspr-recorder writes, since the rest of the
            # cycle_snapshot rows use the Z suffix and we want
            # lexically-sortable timestamps that compare cleanly.
            ts = datetime.fromisoformat(ts_iso.replace('Z', '+00:00'))
            ts_str = ts.strftime('%Y-%m-%dT%H:%M:%SZ')

            drift = data.get('drift') or {}
            timing = data.get('timing') or {}
            yield {
                'filename':            data['filename'],
                'ts':                  ts_str,
                'band_name':           data.get('band_name'),
                'frequency_hz':        data.get('frequency_hz'),
                'sample_rate':         data.get('sample_rate'),
                'period_seconds':      data.get('period_seconds'),
                'samples':             data.get('samples'),
                'total_gaps_filled':   data.get('total_gaps_filled'),
                'completeness_pct':    data.get('completeness_pct'),
                'drift_delta_ms':      drift.get('delta_ms'),
                'drift_cumulative_ms': drift.get('cumulative_drift_ms'),
                'gpsdo_locked':        1 if timing.get('gpsdo_locked') else 0,
                'hf_locked':           1 if timing.get('hf_locked') else 0,
                'system_offset_ms':    timing.get('system_clock_offset_ms'),
                'chrony_stratum':      timing.get('chrony_stratum'),
            }
        except (KeyError, TypeError, ValueError) as e:
            logger.debug("skip %s: parse error %s", json_path, e)
            continue


# ---- main --------------------------------------------------------------------

def collect(db_path: Path, since: datetime) -> dict:
    """Run all scrapers + insert new rows.  Returns stats dict."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    counts = {
        'psk-recorder': 0, 'wspr-upload': 0, 'wspr-wav': 0,
        'inserted': 0, 'duplicate': 0,
        'wav_inserted': 0, 'wav_duplicate': 0,
    }

    events: list[dict] = []
    for path in Path('/var/log/psk-recorder').glob('*.log'):
        for ev in _scrape_psk_log(path, since):
            events.append(ev)
            counts['psk-recorder'] += 1
    for ev in _scrape_upload_journal(UPLOAD_UNIT_GLOB, since):
        events.append(ev)
        counts['wspr-upload'] += 1

    for ev in events:
        try:
            conn.execute(
                """
                INSERT INTO cycle_snapshot
                    (ts, source, mode, spots, decodes_ok, decodes_total,
                     slots_empty, band_counts, raw_line)
                VALUES
                    (:ts, :source, :mode, :spots, :decodes_ok, :decodes_total,
                     :slots_empty, :band_counts, :raw_line)
                """,
                ev,
            )
            counts['inserted'] += 1
        except sqlite3.IntegrityError:
            counts['duplicate'] += 1

    # WSPR WAV sidecars — separate table so the schema stays clean.
    for wav in _scrape_wspr_sidecars(WSPR_SPOOL_ROOT, since):
        counts['wspr-wav'] += 1
        try:
            conn.execute(
                """
                INSERT INTO wav_snapshot
                    (filename, ts, band_name, frequency_hz, sample_rate,
                     period_seconds, samples, total_gaps_filled,
                     completeness_pct, drift_delta_ms, drift_cumulative_ms,
                     gpsdo_locked, hf_locked, system_offset_ms, chrony_stratum)
                VALUES
                    (:filename, :ts, :band_name, :frequency_hz, :sample_rate,
                     :period_seconds, :samples, :total_gaps_filled,
                     :completeness_pct, :drift_delta_ms, :drift_cumulative_ms,
                     :gpsdo_locked, :hf_locked, :system_offset_ms, :chrony_stratum)
                """,
                wav,
            )
            counts['wav_inserted'] += 1
        except sqlite3.IntegrityError:
            counts['wav_duplicate'] += 1

    conn.commit()
    conn.close()
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--db', type=Path, default=DEFAULT_DB,
                    help=f'sqlite path (default: {DEFAULT_DB})')
    ap.add_argument('--hours', type=int, default=DEFAULT_LOOKBACK_HOURS,
                    help=f'how many hours of history to scrape '
                         f'(default: {DEFAULT_LOOKBACK_HOURS})')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )

    since = datetime.now(tz=timezone.utc) - timedelta(hours=args.hours)
    logger.info("scraping since %s into %s", since.isoformat(), args.db)
    stats = collect(args.db, since)
    logger.info(
        "done: psk-recorder=%d  wspr-upload=%d  wspr-wav=%d  "
        "cycle_inserted=%d (dup=%d)  wav_inserted=%d (dup=%d)",
        stats['psk-recorder'], stats['wspr-upload'], stats['wspr-wav'],
        stats['inserted'], stats['duplicate'],
        stats['wav_inserted'], stats['wav_duplicate'],
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
