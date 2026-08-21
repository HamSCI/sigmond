#!/usr/bin/env python3
"""Derive fleet availability from heartbeat ARRIVALS and render the board.

RUNS ON THE CENTRAL BOARD (wd30), NOT ON A STATION.  Rsynced to
/opt/hamsci-fleetboard/ next to a verbatim copy of ``heartbeat_schema.py``
and imported as a sibling.  STDLIB ONLY; no imports from ``sigmond``.

The keystone: absence beats self-report
---------------------------------------
Every block verdict in a heartbeat is the station's own account of
itself.  That account is useful right up to the moment the station stops
producing one — and then it freezes, permanently, at whatever it last
said.  Eight fleet defects went unnoticed while something reported
success (docs/PRODUCER-THREAT-MODEL.md); a board that showed the last
stored rollup for a host that died an hour ago would be the ninth.

So the roster — not the database — decides which rows exist, and arrival
time — not the envelope — decides whether a row can be green.  A station
missing from the drop entirely still gets a row, coloured by its silence.
``derive_status`` is where that inversion lives; the ``top`` verdict a
row displays is the availability verdict whenever availability is not
VALID, and only otherwise the station's own rollup.
"""

import argparse
import html
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import heartbeat_schema

DEFAULT_DB = "/var/lib/hamsci-fleetboard/heartbeats.db"
DEFAULT_ROSTER = "/opt/hamsci-fleetboard/roster.json"
DEFAULT_PORT = 8090

#: Fallback cadence when an envelope does not declare one.  Producers
#: always emit interval_sec (assemble() substitutes 300), so this only
#: covers an older producer or a hand-written envelope.
DEFAULT_INTERVAL_SEC = 300

#: How many missed ticks before a station is called absent.  Three is
#: deliberately forgiving: one missed tick is a hiccup, two is a pattern,
#: three is an outage.  The transport retries, so genuine network blips
#: land inside this window.
DEFAULT_SILENT_TICKS = 3

#: Upper bound on an envelope-declared interval_sec.  interval_sec is
#: SELF-REPORTED — the one number a station gets to hand the server that
#: directly sets the silent-window threshold this board uses to detect
#: that station going dark.  Left unbounded, a station (mistakenly or
#: not) declaring interval_sec=86400 would buy itself a 3-day window of
#: silence before absence detection fires — self-report re-entering the
#: one derivation (arrival time vs. roster) that exists specifically to
#: beat self-report.  Bounds the lie a station can tell about its own
#: cadence; slow public installs still get absence detection within 3h.
MAX_INTERVAL_SEC = 3600

#: Window for "who has been talking to us lately" — both the unexpected
#: -stations list and the rejects counter.
DEFAULT_WINDOW_S = 86400


# ---------------------------------------------------------------------------
# derivation
# ---------------------------------------------------------------------------

def _verdict(verdict, reason):
    return {"verdict": verdict, "reason": reason}


def _unknown_blocks(reason):
    """A full block set that says, honestly, that nothing was measured."""
    return {name: _verdict("INDETERMINATE", reason)
            for name in heartbeat_schema.BLOCK_NAMES}


#: Board-only sentinel, deliberately NOT one of heartbeat_schema.VERDICTS.
#: A block missing from an envelope that DID arrive and parse is a
#: producer's honest declaration that it has no view of that block (a PM
#: heartbeat carries only versions/doctor/resources — it has no way to
#: see timing/gaps/uploads/manifest at all).  That is a different fact
#: from INDETERMINATE ("I looked and could not tell"), and painting both
#: the same grey would make an honest partial producer read as several
#: failed measurements instead of the blocks it never claimed.
NOT_CLAIMED = "N/A"


def _latest(conn, station):
    row = conn.execute(
        "SELECT received_at, emitted_at, rollup_verdict, payload"
        "  FROM heartbeats WHERE station = ?"
        "  ORDER BY received_at DESC, id DESC LIMIT 1", (station,)).fetchone()
    return row


def _blocks_from_payload(payload):
    """Per-block verdict+reason, with ``data`` deliberately dropped.

    A block may carry no ``data`` key at all (the unknown paths never
    produce one), so nothing here may assume it exists.  The board shows
    verdicts and reasons; the full payload stays in the database for
    anyone who needs the numbers.
    """
    blocks = payload.get("blocks")
    if not isinstance(blocks, dict):
        return _unknown_blocks("heartbeat carried no blocks")
    out = {}
    for name in heartbeat_schema.BLOCK_NAMES:
        block = blocks.get(name)
        if not isinstance(block, dict):
            # The envelope arrived and parsed; this block simply was never
            # in it.  See NOT_CLAIMED above — this is "not claimed", not
            # "measured nothing".
            out[name] = _verdict(NOT_CLAIMED, "not claimed by this producer")
            continue
        verdict = block.get("verdict")
        if verdict not in heartbeat_schema.VERDICTS:
            # An uninterpretable verdict is "I cannot read this", which is
            # INDETERMINATE — never healthy, and never a fabricated fault.
            out[name] = _verdict(
                "INDETERMINATE",
                f"unknown verdict {verdict!r} — {block.get('reason') or ''}"
                .strip())
            continue
        out[name] = _verdict(verdict, block.get("reason") or "")
    return out


def derive_status(conn, roster, now, interval_default=DEFAULT_INTERVAL_SEC,
                  silent_ticks=DEFAULT_SILENT_TICKS):
    """One status row per ROSTER station, in roster order.

    The roster is the canonical membership list (``smd fleet roster
    --json``); a station that has never been heard from is still a row,
    because a host that was provisioned and never reported is exactly
    the failure this board exists to make visible.
    """
    statuses = []
    for entry in roster:
        station = entry.get("name")
        status = {
            "station": station,
            "profile": entry.get("profile"),
            "role": entry.get("role"),
            "frozen": entry.get("frozen"),
            "canary": bool(entry.get("canary")),
            "last_received_at": None,
            "silent_for_s": None,
            "emitted_at": None,
            # The envelope's own top-level "role" (e.g. "pm"), NOT the
            # roster's free-form "role" field above.  None until a
            # parseable envelope has actually arrived — a station never
            # heard from gets no tag, same as a plain station.
            "envelope_role": None,
        }
        row = _latest(conn, station)
        if row is None:
            status["availability"] = _verdict("INDETERMINATE", "never heard")
            status["top"] = _verdict("INDETERMINATE", "never heard")
            status["blocks"] = _unknown_blocks("no heartbeat received")
            statuses.append(status)
            continue

        received_at, emitted_at, rollup_verdict, payload_text = row
        status["last_received_at"] = received_at
        status["emitted_at"] = emitted_at
        silent_for_s = float(now) - float(received_at)
        status["silent_for_s"] = silent_for_s

        try:
            payload = json.loads(payload_text)
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
        except (TypeError, ValueError):
            payload = None

        if payload is None:
            status["blocks"] = _unknown_blocks("stored payload unreadable")
            envelope_top = _verdict("INDETERMINATE", "stored payload unreadable")
            interval = interval_default
        else:
            status["blocks"] = _blocks_from_payload(payload)
            envelope_top = _envelope_rollup(payload, rollup_verdict)
            interval = _interval_of(payload, interval_default)
            role = payload.get("role")
            if isinstance(role, str) and role.strip():
                status["envelope_role"] = role.strip()

        if silent_for_s > silent_ticks * interval:
            reason = f"silent {int(silent_for_s // 60)}m"
            status["availability"] = _verdict("INVALID", reason)
            # THE inversion: absence outranks whatever the station last
            # claimed about itself.  Self-report can never mask absence.
            status["top"] = _verdict("INVALID", reason)
        else:
            status["availability"] = _verdict(
                "VALID", f"heard {int(silent_for_s)}s ago")
            status["top"] = envelope_top
        statuses.append(status)
    return statuses


def _interval_of(payload, default):
    interval = payload.get("interval_sec")
    try:
        interval = float(interval)
    except (TypeError, ValueError):
        return default
    # A zero or negative cadence would make every station instantly
    # absent; treat it as "not declared".
    if interval <= 0:
        return default
    # Clamp the top end too: see MAX_INTERVAL_SEC.
    return min(interval, MAX_INTERVAL_SEC)


def _envelope_rollup(payload, rollup_verdict):
    rollup = payload.get("rollup")
    if isinstance(rollup, dict):
        verdict = rollup.get("verdict")
        if verdict in heartbeat_schema.VERDICTS:
            return _verdict(verdict, rollup.get("reason") or "")
    if rollup_verdict in heartbeat_schema.VERDICTS:
        return _verdict(rollup_verdict, "")
    return _verdict("INDETERMINATE", "heartbeat carried no usable rollup")


def unexpected_stations(conn, roster, now, window_s=DEFAULT_WINDOW_S):
    """Stations that reported recently and are NOT on the roster.

    Rendered in a section of its own and NEVER merged into the roster
    table: a host nobody declared is a provisioning question, not a
    fleet member, and letting it pad the roster view would make the
    fleet look larger and healthier than it was declared to be.
    """
    known = {e.get("name") for e in roster}
    cutoff = float(now) - float(window_s)
    rows = conn.execute(
        "SELECT station, COUNT(*), MAX(received_at) FROM heartbeats"
        "  WHERE received_at >= ? GROUP BY station ORDER BY station",
        (cutoff,)).fetchall()
    return [{"station": s, "count": n, "last_seen": last}
            for (s, n, last) in rows if s not in known]


def rejects_summary(conn, now, window_s=DEFAULT_WINDOW_S, limit=10):
    """How much unreadable traffic arrived in the window, and from whom.

    A rising reject count is a station shipping something the server
    cannot read — which looks exactly like silence on the board unless
    it is counted separately.
    """
    cutoff = float(now) - float(window_s)
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM rejects WHERE received_at >= ?",
        (cutoff,)).fetchone()
    rows = conn.execute(
        "SELECT station_guess, received_at, reason, filename FROM rejects"
        "  WHERE received_at >= ? ORDER BY received_at DESC LIMIT ?",
        (cutoff, limit)).fetchall()
    return {
        "count": count,
        "window_s": window_s,
        "recent": [{"station_guess": g, "received_at": t,
                    "reason": r, "filename": f} for (g, t, r, f) in rows],
    }


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

CSS = """
:root { color-scheme: light dark; }
body { font: 14px/1.45 system-ui, sans-serif; margin: 1.5rem;
       background: #fff; color: #111; }
h1 { font-size: 1.3rem; margin: 0 0 .2rem 0; }
p.sub { margin: 0 0 1.2rem 0; color: #555; }
table { border-collapse: collapse; width: 100%; margin-bottom: 1.6rem; }
th, td { border: 1px solid #ccc; padding: .3rem .45rem; text-align: left;
         vertical-align: top; }
th { background: #f0f0f0; font-weight: 600; }
td.station { font-weight: 600; white-space: nowrap; }
td.num { text-align: right; white-space: nowrap; }
span.tag { font-size: .78rem; color: #555; font-weight: normal; }
td.verdict { white-space: nowrap; font-weight: 600; }
td.verdict small { display: block; font-weight: normal; color: #333;
                   white-space: normal; max-width: 26rem; }
.v-VALID         { background: #d8f2d8; }
.v-INVALID       { background: #f6cccc; }
.v-INCONCLUSIVE  { background: #fbe8c0; }
.v-INDETERMINATE { background: #dedede; }
.v-NA            { background: #fafafa; color: #888; font-style: italic; }
footer { color: #555; font-size: .85rem; border-top: 1px solid #ccc;
         padding-top: .5rem; }
"""


def iso_utc(epoch):
    """Render an epoch as an unambiguous UTC stamp (never local time)."""
    if epoch is None:
        return "—"
    return datetime.fromtimestamp(float(epoch), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def human_age(seconds):
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _e(value):
    """Escape for HTML.  Everything here came off the network."""
    return html.escape("" if value is None else str(value), quote=True)


#: Per-block cells are narrow, so they carry a short label rather than a
#: truncated verdict ("VALI" and "INVA" are one keystroke apart and mean
#: opposite things).  Colour is never the only channel: the label and the
#: hover title carry the same information for anyone who cannot see it.
VERDICT_LABEL = {
    "VALID": "ok",
    "INVALID": "FAIL",
    "INCONCLUSIVE": "unsure",
    "INDETERMINATE": "unknown",
    NOT_CLAIMED: "n/a",
}

#: NOT_CLAIMED gets its own muted CSS class ("NA" — "N/A" is not a legal
#: CSS class token). Deliberately distinct from v-INDETERMINATE: "not
#: claimed" must never look like "measured nothing".
_CSS_SUFFIX = {NOT_CLAIMED: "NA"}


def _verdict_cell(block, full=False):
    verdict = block.get("verdict") if isinstance(block, dict) else None
    if verdict not in heartbeat_schema.VERDICTS and verdict != NOT_CLAIMED:
        verdict = "INDETERMINATE"
    reason = (block.get("reason") if isinstance(block, dict) else "") or ""
    css = _CSS_SUFFIX.get(verdict, verdict)
    if full:
        return (f'<td class="verdict v-{css}">{_e(verdict)}'
                f'<small>{_e(reason)}</small></td>')
    return (f'<td class="verdict v-{css}" title="{_e(reason)}">'
            f'{_e(VERDICT_LABEL[verdict])}</td>')


def render_html(statuses, unexpected, rejects, generated_at):
    """The whole board as one self-contained page.

    No JavaScript and no external assets: the board has to be readable
    from a phone on a bad link during exactly the incident it is
    reporting.  Refresh is a meta tag for the same reason.
    """
    out = []
    out.append("<!doctype html>")
    out.append('<html lang="en"><head><meta charset="utf-8">')
    out.append('<meta name="viewport" content="width=device-width,'
               ' initial-scale=1">')
    out.append('<meta http-equiv="refresh" content="60">')
    out.append("<title>HamSCI fleet board</title>")
    out.append(f"<style>{CSS}</style>")
    out.append("</head><body>")
    out.append("<h1>HamSCI fleet board</h1>")
    out.append('<p class="sub">Availability is derived from heartbeat '
               'arrival times against the roster — never from anything a '
               'station says about itself.</p>')

    out.append("<table><thead><tr>")
    out.append("<th>station</th><th>status</th><th>availability</th>"
               "<th>silent for</th><th>last arrival</th><th>emitted at</th>")
    for name in heartbeat_schema.BLOCK_NAMES:
        out.append(f"<th>{_e(name)}</th>")
    out.append("</tr></thead><tbody>")

    for status in statuses:
        tags = []
        if status.get("envelope_role") == "pm":
            tags.append("pm")
        if status.get("canary"):
            tags.append("canary")
        if status.get("frozen"):
            tags.append(f"frozen: {status['frozen']}")
        tag_html = (f'<br><span class="tag">{_e(", ".join(tags))}</span>'
                    if tags else "")
        out.append("<tr>")
        out.append(f'<td class="station">{_e(status.get("station"))}'
                   f'{tag_html}</td>')
        out.append(_verdict_cell(status.get("top") or {}, full=True))
        out.append(_verdict_cell(status.get("availability") or {}, full=True))
        out.append(f'<td class="num">'
                   f'{_e(human_age(status.get("silent_for_s")))}</td>')
        out.append(f'<td class="num">'
                   f'{_e(iso_utc(status.get("last_received_at")))}</td>')
        out.append(f'<td class="num">{_e(status.get("emitted_at") or "—")}'
                   f'</td>')
        blocks = status.get("blocks") or {}
        for name in heartbeat_schema.BLOCK_NAMES:
            out.append(_verdict_cell(blocks.get(name) or {}))
        out.append("</tr>")
    if not statuses:
        # 6 fixed columns (station, status, availability, silent for,
        # last arrival, emitted at) + one per block name — kept in sync
        # with the <thead> above rather than hand-counted.
        colspan = 6 + len(heartbeat_schema.BLOCK_NAMES)
        out.append(f'<tr><td colspan="{colspan}">roster is empty — nothing '
                   'is being watched</td></tr>')
    out.append("</tbody></table>")

    out.append("<h2>Unexpected stations</h2>")
    out.append('<p class="sub">Heard from, but not on the roster — never '
               'counted as fleet members. A name here paired with a roster '
               'row reading &ldquo;never heard&rdquo; is almost always one '
               'host with two names: <code>[heartbeat].station</code> in its '
               'coordination.toml must equal its roster name.</p>')
    if unexpected:
        out.append("<table><thead><tr><th>station</th><th>heartbeats</th>"
                   "<th>last seen</th></tr></thead><tbody>")
        for item in unexpected:
            out.append("<tr>")
            out.append(f'<td class="station">{_e(item.get("station"))}</td>')
            out.append(f'<td class="num">{_e(item.get("count"))}</td>')
            out.append(f'<td class="num">'
                       f'{_e(iso_utc(item.get("last_seen")))}</td>')
            out.append("</tr>")
        out.append("</tbody></table>")
    else:
        out.append('<p class="sub">None in the last '
                   f'{_e(human_age((rejects or {}).get("window_s")))}.</p>')

    rejects = rejects or {}
    out.append("<h2>Rejected uploads</h2>")
    out.append(f'<p class="sub"><strong>{_e(rejects.get("count", 0))}</strong>'
               f' file(s) quarantined in the last '
               f'{_e(human_age(rejects.get("window_s")))} — unreadable '
               f'traffic looks like silence unless it is counted.</p>')
    recent = rejects.get("recent") or []
    if recent:
        out.append("<table><thead><tr><th>station (guess)</th><th>when</th>"
                   "<th>why</th></tr></thead><tbody>")
        for item in recent:
            out.append("<tr>")
            out.append(f'<td class="station">'
                       f'{_e(item.get("station_guess"))}</td>')
            out.append(f'<td class="num">'
                       f'{_e(iso_utc(item.get("received_at")))}</td>')
            out.append(f"<td>{_e(item.get('reason'))}</td>")
            out.append("</tr>")
        out.append("</tbody></table>")

    out.append(f"<footer>generated {_e(iso_utc(generated_at))} · "
               f"{len(statuses)} roster station(s) · page refreshes every "
               f"60 s</footer>")
    out.append("</body></html>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# roster + page assembly
# ---------------------------------------------------------------------------

def load_roster(path):
    """Read ``smd fleet roster --json`` output.

    Refuses anything that is not a non-empty list of named entries: an
    empty roster silently renders an empty board, which is the most
    dangerous page this service could serve — it looks like a healthy
    fleet of zero hosts.
    """
    with open(path, "r", encoding="utf-8") as handle:
        try:
            data = json.load(handle)
        except ValueError as exc:
            raise ValueError(f"{path}: not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{path}: roster is {type(data).__name__}, "
                         "expected a JSON array")
    if not data:
        raise ValueError(f"{path}: roster is empty — refusing to render a "
                         "board that watches nothing")
    for entry in data:
        if not isinstance(entry, dict) or not entry.get("name"):
            raise ValueError(f"{path}: roster entry {entry!r} has no name")
    return data


def build_page(db_path, roster, now=None):
    """Render the live board from sqlite in one read-only pass."""
    now = time.time() if now is None else now
    # Read-only URI: the board must never be able to write the ingest
    # database, whatever a request does.
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise RuntimeError(f"cannot open {db_path}: {exc}") from exc
    try:
        statuses = derive_status(conn, roster, now)
        unexpected = unexpected_stations(conn, roster, now)
        rejects = rejects_summary(conn, now)
    finally:
        conn.close()
    return render_html(statuses, unexpected, rejects, now)


class _BoardHandler(BaseHTTPRequestHandler):
    server_version = "hamsci-fleetboard/1"

    def do_GET(self):                                   # noqa: N802 (stdlib)
        path = self.path.split("?", 1)[0]
        if path not in ("/", "/index.html"):
            self.send_error(404, "not found")
            return
        try:
            body = build_page(self.server.db_path,
                              self.server.roster_loader()).encode("utf-8")
        except Exception as exc:                        # noqa: BLE001
            # Never serve a blank 200: a board that fails to render must
            # say so, or it reads as "no problems".
            self.log_error("render failed: %s: %s", type(exc).__name__, exc)
            self.send_error(500, "board render failed")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("fleetboard: %s\n" % (fmt % args))


def serve(bind, port, db_path, roster_path):
    """Serve the board until interrupted.  Returns an exit code."""
    # Re-read the roster per request so a redeploy takes effect without a
    # restart, but fall back to the last good one rather than 500-ing the
    # board because someone was mid-rsync.
    cache = {"roster": load_roster(roster_path)}

    def roster_loader():
        try:
            cache["roster"] = load_roster(roster_path)
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"fleetboard: keeping last good roster: {exc}\n")
        return cache["roster"]

    httpd = ThreadingHTTPServer((bind, port), _BoardHandler)
    httpd.db_path = db_path
    httpd.roster_loader = roster_loader
    sys.stderr.write(f"fleetboard: serving http://{bind}:{port}/ "
                     f"({len(cache['roster'])} roster stations)\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render the HamSCI fleet board from ingested heartbeats.")
    parser.add_argument(
        "--bind", default=None,
        help="address to serve on — REQUIRED when serving. There is "
             "deliberately no 0.0.0.0 default: the operator names the LAN "
             "or WireGuard address the board should be reachable on.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"TCP port (default {DEFAULT_PORT})")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"sqlite database (default {DEFAULT_DB})")
    parser.add_argument("--roster", default=DEFAULT_ROSTER,
                        help=f"roster JSON (default {DEFAULT_ROSTER})")
    parser.add_argument("--once", metavar="PATH", default=None,
                        help="write the board to PATH and exit (no server)")
    args = parser.parse_args(argv)

    try:
        roster = load_roster(args.roster)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if args.once:
        try:
            html_text = build_page(args.db, roster)
        except (RuntimeError, sqlite3.Error, OSError) as exc:
            sys.stderr.write(f"fleetboard: {exc}\n")
            return 1
        with open(args.once, "w", encoding="utf-8") as handle:
            handle.write(html_text)
        return 0

    if not args.bind:
        parser.error("--bind is required when serving (no default: bind the "
                     "LAN/WireGuard address deliberately, never 0.0.0.0)")
    return serve(args.bind, args.port, args.db, args.roster)


if __name__ == "__main__":
    sys.exit(main())
