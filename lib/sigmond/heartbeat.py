"""Station heartbeat assembler — the honest per-station envelope.

Eight fleet defects in a single session were SILENT while some metric
reported success (``docs/PRODUCER-THREAT-MODEL.md``, "Metrics that lie").
The heartbeat is the answer: one JSON envelope per station per interval,
assembled ONLY from signals that cannot lie, in which every block is
able to say "I don't know", and in which "nothing was measured" can
never render as healthy.

Three rules shape everything below.

1. **The assembler's only jobs are honesty and atomicity.**  It does not
   decide whether a station is up — the server derives availability from
   heartbeat ARRIVAL times, never from envelope content, because a host
   that has stopped cannot report that it stopped.
2. **A reader that fails makes its block INDETERMINATE, never absent and
   never healthy.**  Every reader call is wrapped; an exception becomes
   ``verdict=INDETERMINATE`` with the exception in the reason.  A block
   that vanished on failure would let a broken probe read as a clean
   bill of health, which is the exact defect class this exists to catch.
3. **Verdicts are worst-wins.**  The rollup is the worst block, named,
   with that block's reason — so the one line an operator reads always
   points at the evidence.

``sigmond.heartbeat_schema`` holds the wire contract and is stdlib-only
because it is rsynced verbatim to the server host; THIS module is the
producer side and may use the rest of sigmond freely.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .heartbeat_schema import BLOCK_NAMES, KIND, PRECEDENCE, SCHEMA_VERSION

log = logging.getLogger(__name__)

# --- defaults for the production wiring -----------------------------------

AUTHORITY_PATH = "/run/hf-timestd/authority.json"
# Same freshness window as hamsci_dsp.timing.DEFAULT_FRESHNESS_SEC.
AUTHORITY_FRESHNESS_SEC = 60.0

GAP_TSV_PATH = "/var/log/gap-hourly.tsv"
# 2x the sampler's hourly cadence: one missed run is a hiccup, two means
# the sampler is not running and the last row is no longer evidence.
GAP_ROW_MAX_AGE_SEC = 7200
GAP_ROW_UTC_FORMAT = "%Y-%m-%dT%H:%MZ"

WATERMARKS_DB = "/var/lib/hs-uploader/watermarks.db"
HS_UPLOADER_SRC = "/opt/git/sigmond/hs-uploader/src"
BACKLOG_TIMEOUT_SEC = 20

SIGMOND_BASE = "/opt/git/sigmond"
MANIFEST_PATH = "/etc/sigmond-appliance/manifest.txt"

# Config contract default.  assemble() substitutes it rather than
# emitting an envelope that fails its own validate(): a heartbeat that
# cannot be validated is dropped at the server, which is silence — the
# exact failure mode this feature exists to remove.
DEFAULT_INTERVAL_SEC = 300

SPOOL_DIR = "/var/lib/sigmond/heartbeat"
SPOOL_MAX_AGE_SEC = 24 * 3600

_EMITTED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_FILENAME_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


class ReaderUnavailable(Exception):
    """A reader could not measure, and knows exactly why.

    Distinct from every other exception on purpose: ``assemble`` renders
    this one's message VERBATIM as the block's reason (no exception class
    name, no traceback noise), because a reader raising this has already
    written the sentence an operator needs to read.  Anything else that
    escapes a reader is a surprise, and gets the class name attached so
    the surprise is visible.
    """


@dataclass
class HeartbeatPaths:
    """Where the production readers look.  Every path is overridable so
    tests never touch a real host path."""
    base: str = SIGMOND_BASE
    manifest_path: str = MANIFEST_PATH
    authority_json: str = AUTHORITY_PATH
    gap_tsv: str = GAP_TSV_PATH
    watermarks_db: str = WATERMARKS_DB
    hs_uploader_src: str = HS_UPLOADER_SRC
    spool_dir: str = SPOOL_DIR


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------

def assemble(now, config: dict, readers, uptime_s: Optional[float] = None) -> dict:
    """Build one heartbeat envelope.  Pure, given its inputs.

    ``now``      datetime (naive is treated as UTC) or epoch seconds.
    ``config``   ``{"station": str, "callsign": str|None, "grid": str|None,
                 "interval_sec": int}``.  ``station`` is REQUIRED and a
                 missing one raises ``ValueError``: a station that does
                 not know its own name cannot be attributed on the board,
                 and a systemd unit failing loudly at that is the design.
                 ``grid`` is truncated to 6 characters — a full Maidenhead
                 grid locates a private residence to a few tens of metres,
                 and the board never needs that.
    ``readers``  bundle of 7 callables (dict or object with attributes),
                 one per name in ``BLOCK_NAMES``.  Each returns its
                 block's raw data or raises.
    ``uptime_s`` passed IN, never read here — ``assemble`` performs no
                 I/O at all, which is what makes it testable.  See
                 ``read_uptime()`` for the production value.
    """
    config = config or {}
    station = config.get("station")
    if not isinstance(station, str) or not station.strip():
        raise ValueError(
            "heartbeat config requires a non-empty 'station' — an "
            "unattributable heartbeat is worse than none")

    emitted = _as_utc(now)

    blocks: Dict[str, dict] = {}
    for name in BLOCK_NAMES:
        blocks[name] = _assemble_block(name, readers)

    grid = config.get("grid")
    if isinstance(grid, str):
        grid = grid[:6]                     # PII rule; see docstring

    if uptime_s is None:
        uptime_s = config.get("uptime_s")

    interval_sec = config.get("interval_sec")
    if interval_sec is None:
        interval_sec = DEFAULT_INTERVAL_SEC

    return {
        "kind": KIND,
        "schema_version": SCHEMA_VERSION,
        "station": station,
        "callsign": config.get("callsign"),
        "grid": grid,
        "emitted_at": emitted.strftime(_EMITTED_AT_FORMAT),
        "interval_sec": interval_sec,
        "uptime_s": uptime_s,
        "rollup": rollup(blocks),
        "blocks": blocks,
    }


def rollup(blocks: dict) -> dict:
    """Worst verdict across ``blocks``, naming the block that earned it.

    Ties resolve to the first block in ``BLOCK_NAMES`` order — a stable,
    declared order, so the same host state always produces the same
    headline rather than one that shuffles with dict iteration.
    """
    present = [n for n in BLOCK_NAMES if isinstance(blocks.get(n), dict)]
    if not present:
        return {"verdict": "INDETERMINATE", "reason": "no blocks assembled"}
    worst = max(_rank(blocks[n].get("verdict")) for n in present)
    for name in present:
        verdict = blocks[name].get("verdict")
        if _rank(verdict) != worst:
            continue
        if verdict not in PRECEDENCE:
            # An uninterpretable verdict is reported AS uninterpretable,
            # not passed through: passing it through would put a value
            # outside VERDICTS in the rollup and fail validate(), and
            # dropping it would be worse still.
            return {
                "verdict": "INDETERMINATE",
                "reason": f"{name}: unknown verdict {verdict!r} — "
                          f"{blocks[name].get('reason')}",
            }
        return {
            "verdict": verdict,
            "reason": f"{name}: {blocks[name].get('reason')}",
        }
    # unreachable: `worst` came from this same list
    return {"verdict": "INDETERMINATE", "reason": "no blocks assembled"}


def _rank(verdict) -> int:
    """Precedence of ``verdict``, FAILING CLOSED.

    A verdict this schema does not know — a typo, a block from a newer
    producer, a missing key — ranks as INDETERMINATE, never as VALID.
    Ranking the unknown at 0 (the old default) meant a single misspelt
    verdict could carry a whole station to a green rollup, which is the
    defect class the heartbeat exists to catch, reintroduced inside the
    heartbeat itself.  INDETERMINATE rather than INVALID because "I
    cannot interpret this" is genuinely "could not assess", not
    "measured and bad" — and it is still, correctly, not healthy.
    """
    return PRECEDENCE.get(verdict, PRECEDENCE["INDETERMINATE"])


def _assemble_block(name: str, readers) -> dict:
    try:
        reader = _reader(readers, name)
        raw = reader()
        return _MAPPERS[name](raw)
    except ReaderUnavailable as exc:
        # Verbatim: the reader already said the useful thing.
        return _block("INDETERMINATE", str(exc))
    except Exception as exc:                      # noqa: BLE001 - deliberate
        return _block(
            "INDETERMINATE",
            f"{name} reader raised {exc.__class__.__name__}: {exc}")


def _reader(readers, name: str) -> Callable[[], Any]:
    if readers is None:
        raise ReaderUnavailable(f"no {name} reader wired")
    if isinstance(readers, dict):
        fn = readers.get(name)
    else:
        fn = getattr(readers, name, None)
    if not callable(fn):
        raise ReaderUnavailable(f"no {name} reader wired")
    return fn


def _block(verdict: str, reason: str, data: Optional[dict] = None) -> dict:
    block = {"verdict": verdict, "reason": reason}
    if data is not None:
        block["data"] = data
    return block


def _as_utc(now) -> datetime:
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)
    return datetime.fromtimestamp(float(now), tz=timezone.utc)


# ---------------------------------------------------------------------------
# per-block verdict mappings
#
# Every reason string states the EVIDENCE, not a mood: an operator must
# be able to act on the one line the rollup shows without opening the
# envelope.
# ---------------------------------------------------------------------------

def _map_versions(raw) -> dict:
    components = dict((raw or {}).get("components") or {})
    if not components:
        # No versions read is not "no drift" — it is no evidence.
        return _block("INDETERMINATE", "no component versions readable",
                      {"components": {}})
    return _block("VALID",
                  f"{len(components)} component versions read",
                  {"components": components})


def _map_manifest(raw) -> dict:
    raw = raw or {}
    data = {
        "present": bool(raw.get("present")),
        "blessed_source": raw.get("blessed_source"),
        "drift": list(raw.get("drift") or []),
    }
    if not data["present"]:
        # Mirrors smd doctor's honest "unassessed" finding: a host with
        # no usable manifest has no baseline, and reporting silence there
        # would read as "verified clean" when it is really "never
        # checked" — a lie of omission on exactly the boxes least likely
        # to have been looked at recently.
        return _block("INDETERMINATE",
                      "manifest unassessed — host not on a blessed image",
                      data)
    if data["drift"]:
        names = ", ".join(_drift_names(data["drift"]))
        return _block(
            "INVALID",
            f"{len(data['drift'])} component(s) drifted from the image "
            f"manifest: {names}",
            data)
    source = data["blessed_source"] or "the image manifest"
    return _block("VALID", f"live components match {source}", data)


def _drift_names(drift) -> list:
    out = []
    for d in drift:
        if isinstance(d, dict):
            out.append(str(d.get("component", d)))
        else:
            out.append(str(d))
    return out


def _map_timing(raw) -> dict:
    if raw is None:
        # missing == stale == not running, from the consumer's seat.
        return _block("INDETERMINATE", "no timing authority snapshot")
    data = {
        "tier": raw.get("t_level_active"),
        "sigma_ns": raw.get("sigma_ns"),
        "t_level_witnesses": list(raw.get("t_level_witnesses") or []),
        "disagreement_flags": list(raw.get("disagreement_flags") or []),
        "t6_authority_state": raw.get("t6_authority_state"),
        "snapshot_age_s": raw.get("snapshot_age_s"),
        "source": raw.get("source"),
    }
    if data["disagreement_flags"]:
        flags = ", ".join(str(f) for f in data["disagreement_flags"])
        return _block("INVALID", f"timing sources disagree: {flags}", data)
    if data["source"] == "standalone-fallback":
        # Measured, but not conclusive: the clock is running unwitnessed.
        return _block(
            "INCONCLUSIVE",
            "standalone fallback — no adjudicated timing authority", data)
    # hf-timestd really publishes a fully-formed authority.json with no
    # adjudicated tier and no witnesses while the bootstrap coordinator
    # has probing gated (authority_manager._build_bootstrap_pending_state:
    # t_level_active None, t_level_witnesses [], sigma_ns None).  That is
    # a live file, fresh, schema-v1, with empty disagreement_flags — it
    # would sail straight through every check above and render VALID.
    # It is measured but NOT conclusive: nothing is adjudicating time.
    missing = []
    if data["tier"] in (None, "T0"):
        missing.append("no adjudicated tier")
    if not data["t_level_witnesses"]:
        missing.append("no witnesses")
    if missing:
        return _block("INCONCLUSIVE",
                      f"authority present but {' and '.join(missing)}", data)

    sigma = data["sigma_ns"]
    sigma_txt = f"sigma {sigma} ns" if sigma is not None else "sigma unreported"
    return _block(
        "VALID",
        f"{data['tier']} active, {sigma_txt}, "
        f"{len(data['t_level_witnesses'])} witness(es)",
        data)


def _map_gaps(raw) -> dict:
    raw = raw or {}
    gaps = _as_int(raw.get("gaps"))
    rate = _as_float(raw.get("rate"))
    data = {
        "row_utc": raw.get("row_utc"),
        "gaps": gaps,
        "channel_hours": _as_float(raw.get("channel_hours")),
        "gap_rate": rate,
        "row_age_s": raw.get("row_age_s"),
    }

    age = raw.get("row_age_s")
    if age is None:
        return _block("INDETERMINATE",
                      "gap-hourly row age unknown (unparseable row timestamp)",
                      data)
    if age < 0:
        # A row stamped in the future means the sampler's clock and ours
        # disagree; its age is not evidence of anything, and a negative
        # age would sail through the freshness gate below.
        return _block("INDETERMINATE",
                      "gap row is future-stamped (clock step?)", data)
    if age > GAP_ROW_MAX_AGE_SEC:
        return _block(
            "INDETERMINATE",
            f"gap-hourly row is {float(age):.0f}s old "
            f"(> {GAP_ROW_MAX_AGE_SEC}s, 2x the hourly cadence) — the "
            f"sampler is not running",
            data)
    if gaps is None:
        # The sampler writes literal "NA" rather than a lying 0.00 when
        # the window held no sidecars at all.  Honour that.
        return _block("INDETERMINATE",
                      "no channels measured last sampled hour", data)
    if gaps > 0:
        rate_txt = f"{rate:.2f}" if rate is not None else "NA"
        return _block("INVALID",
                      f"{gaps} gap events (rate {rate_txt}/ch-hr)", data)
    hours = data["channel_hours"]
    hours_txt = f"{hours:.2f}" if hours is not None else "?"
    return _block("VALID",
                  f"0 gap events over {hours_txt} channel-hours", data)


def _map_uploads(raw) -> dict:
    raw = raw or {}
    # readable:false responses OMIT pipelines/cursors entirely — this
    # branch MUST fire before anything touches them.
    if not raw.get("readable"):
        return _block("INDETERMINATE",
                      raw.get("reason") or "upload backlog unreadable")
    pipelines = list(raw.get("pipelines") or [])
    data = {"pipelines": pipelines, "cursors": list(raw.get("cursors") or [])}

    dead = [p for p in pipelines if _as_int(p.get("dead_letter_count")) or 0]
    if dead:
        named = ", ".join(
            f"{p.get('name')}({_as_int(p.get('dead_letter_count'))})"
            for p in dead)
        return _block("INVALID", f"dead letters in {named}", data)

    retrying = [p for p in pipelines if _as_int(p.get("deliverable_count")) or 0]
    if retrying:
        total = sum(_as_int(p.get("deliverable_count")) or 0 for p in retrying)
        named = ", ".join(str(p.get("name")) for p in retrying)
        return _block("INCONCLUSIVE",
                      f"{total} deliverables retrying ({named})", data)
    # A fully-idle pipeline is ABSENT from `pipelines`, so an empty list
    # on a readable store genuinely means nothing is stuck.
    return _block("VALID", "no backlog, no dead letters", data)


def _map_doctor(raw) -> dict:
    clean, findings = raw
    findings = list(findings or [])
    data = {
        "clean": clean,
        "findings": [
            {
                "component": getattr(f, "component", None),
                "kind": getattr(f, "kind", None),
                "detail": getattr(f, "detail", None),
            }
            for f in findings
        ],
    }
    kinds = _unique([str(getattr(f, "kind", "?")) for f in findings])
    if clean is None:
        # At least one check family could not be examined at all.  That
        # outranks the findings we DID collect: an incomplete sweep can
        # never be reported as a complete one.  The findings still ride
        # along in `data` so nothing collected is lost.
        unassessed = _unique([k for k in kinds if k.endswith("-unassessed")])
        detail = f" ({', '.join(unassessed)})" if unassessed else ""
        return _block("INDETERMINATE",
                      f"doctor could not assess every check family{detail}",
                      data)
    if findings:
        return _block("INVALID",
                      f"{len(findings)} doctor finding(s): {', '.join(kinds)}",
                      data)
    if clean is False:
        # Contradictory input: not-clean is only ever set alongside at
        # least one finding.  Fail toward caution rather than pick the
        # half of the answer that reads healthy.
        return _block("INDETERMINATE",
                      "doctor reported not-clean but no findings", data)
    return _block("VALID", "deploy trees clean — no doctor findings", data)


def _map_resources(raw) -> dict:
    raw = raw or {}
    llc_raw = raw.get("llc") or {}
    llc = {"available": bool(llc_raw.get("available"))}
    if llc_raw.get("radiod_occupancy_mib") is not None:
        llc["radiod_occupancy_mib"] = llc_raw["radiod_occupancy_mib"]

    irqs = raw.get("irqs") or {}
    # First probe after boot has no previous snapshot, so there is no
    # delta and observed_cores is empty.  Report that rather than
    # guessing; a cumulative count reads a boot transient as permanent
    # drift (see local_resources._summarise_irq).
    irq_delta = bool(irqs) and all(
        bool((h or {}).get("delta_available")) for h in irqs.values())

    data = {
        "llc": llc,
        "irq": {"delta_available": irq_delta},
        # Context only.  The `gaps` BLOCK is the honest loss verdict for
        # this station; this number is here to correlate loss with cache
        # and IRQ placement, and must never be read as the verdict.
        "gap_rate_per_channel_hour":
            (raw.get("radiod") or {}).get("gap_rate_per_channel_hour"),
    }

    errors = list(raw.get("errors") or [])
    if errors:
        return _block("INCONCLUSIVE",
                      f"resource probe incomplete: {'; '.join(errors)}", data)
    return _block(
        "VALID",
        f"host resource counters read (llc "
        f"{'available' if llc['available'] else 'unavailable'}, irq delta "
        f"{'available' if irq_delta else 'unavailable'})",
        data)


_MAPPERS = {
    "versions": _map_versions,
    "manifest": _map_manifest,
    "timing": _map_timing,
    "gaps": _map_gaps,
    "uploads": _map_uploads,
    "doctor": _map_doctor,
    "resources": _map_resources,
}


def _as_int(value) -> Optional[int]:
    """int(value), or None for "NA"/None/anything unparseable.

    Never 0 on failure: a zero here is the lie the whole feature exists
    to remove.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique(items) -> list:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# gap-hourly TSV
# ---------------------------------------------------------------------------

def parse_gap_row(text: str, now: Optional[float] = None) -> dict:
    """Parse the LAST data row of a gap-hourly TSV.

    Format contract with ``sigmond.gap_hourly`` (its ``HEADER`` and
    ``build_row``)::

        # utc\tgaps\tchannel_hours\tgaps_per_ch_hr\tgrape_running
        2026-08-20T13:00Z\t0\t6.00\t0.00\t1
        2026-08-20T14:00Z\tNA\t0.00\tNA\t0

    ``gaps`` and ``gaps_per_ch_hr`` are the literal string ``NA`` when
    the trailing hour contained no sidecars at all — the sampler refuses
    to write a 0.00 rate it did not measure, and this parser preserves
    that by returning ``None`` for both.

    Raises ``ReaderUnavailable`` when there is no data row to read.
    """
    now = time.time() if now is None else now
    rows = [
        line for line in (text or "").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not rows:
        return _no_data_rows()

    fields = rows[-1].split("\t")
    if len(fields) < 4:
        raise ReaderUnavailable(
            f"gap-hourly row malformed ({len(fields)} field(s), expected 5)")

    row_utc = fields[0].strip()
    try:
        stamp = datetime.strptime(row_utc, GAP_ROW_UTC_FORMAT).replace(
            tzinfo=timezone.utc)
        row_age_s = now - stamp.timestamp()
    except ValueError:
        row_age_s = None

    return {
        "row_utc": row_utc,
        "gaps": _as_int(fields[1]),
        "channel_hours": _as_float(fields[2]),
        "rate": _as_float(fields[3]),
        "row_age_s": row_age_s,
    }


def _no_data_rows() -> dict:
    raise ReaderUnavailable("gap-hourly TSV has no data rows")


# ---------------------------------------------------------------------------
# default (production) readers
# ---------------------------------------------------------------------------

def default_readers(paths: Optional[HeartbeatPaths] = None,
                    doctor_reader: Optional[Callable[[], Any]] = None) -> dict:
    """The seven production readers.

    ``doctor_reader`` is INJECTED, not built here: ``collect_findings()``
    lives in ``bin/smd`` (it is coupled to smd-local helpers — see
    task-6's report), and ``bin/smd`` is not an importable module.
    heartbeat.py must never import it or load it via SourceFileLoader:
    that would make the library depend on the CLI.  The production caller
    is ``smd admin heartbeat emit``, which runs INSIDE bin/smd and passes
    ``collect_findings`` straight in.  With no reader wired the doctor
    block reads INDETERMINATE, which is the truth.
    """
    paths = paths or HeartbeatPaths()
    return {
        "versions": lambda: _read_versions(paths),
        "manifest": lambda: _read_manifest(paths),
        "timing": lambda: _read_authority(paths.authority_json),
        "gaps": lambda: _read_gap_tsv(paths.gap_tsv),
        "uploads": lambda: _read_backlog(paths),
        "doctor": doctor_reader or _doctor_not_wired,
        "resources": _read_resources,
    }


def _doctor_not_wired():
    raise ReaderUnavailable("doctor findings not wired")


def _read_versions(paths: HeartbeatPaths) -> dict:
    """Live component HEADs — the same call ``smd version`` makes."""
    from .doctor import component_checkouts
    from .provenance import component_versions
    return {"components": component_versions(component_checkouts(paths.base))}


def _read_manifest(paths: HeartbeatPaths) -> dict:
    """Manifest drift, with smd doctor's honest "unassessed" semantics.

    ``present`` is NOT "the file exists" — it is "the manifest can be
    trusted enough to compare against", the same test
    ``manifest_drift()`` applies internally
    (``doctor._parse_manifest_components``: header missing, or fewer than
    the row floor, both count as untrustworthy).  Reused rather than
    re-derived, so this block and ``smd doctor`` can never disagree about
    the same file.
    """
    from .doctor import (component_checkouts, _parse_manifest_components,
                         manifest_drift_text)
    from .provenance import component_versions

    try:
        text = Path(paths.manifest_path).read_text()
    except OSError:
        text = None

    usable = text is not None and _parse_manifest_components(text) is not None
    if not usable:
        return {"present": False, "blessed_source": paths.manifest_path,
                "drift": []}
    live = component_versions(component_checkouts(paths.base))
    return {
        "present": True,
        "blessed_source": paths.manifest_path,
        "drift": manifest_drift_text(live, text),
    }


def _read_authority(path: str = AUTHORITY_PATH,
                    freshness_sec: float = AUTHORITY_FRESHNESS_SEC,
                    now: Optional[float] = None) -> dict:
    """Read /run/hf-timestd/authority.json directly (stdlib only).

    Ports the semantics of ``hamsci_dsp.timing.AuthorityReader`` (see
    ``hamsci-dsp/src/hamsci_dsp/timing.py``), which is the REFERENCE
    IMPLEMENTATION for this file's schema-v1 contract
    (``hf-timestd/docs/METROLOGY.md`` §4.5.2).  We do not import it:
    sigmond stays stdlib-only so the heartbeat can be emitted on a host
    where hamsci-dsp is absent or broken — the heartbeat has to work
    hardest exactly when other things do not.

    One deliberate difference from the reference: AuthorityReader returns
    None on every failure path, which is right for a client that just
    needs to fall back to the system clock.  Here the DIFFERENCE between
    "no file" and "stale" is the operator's whole diagnosis, so each
    failure raises ``ReaderUnavailable`` with its own sentence.

    Freshness is measured from the file's mtime rather than the
    reference's ``utc_published`` field: a writer that hangs mid-publish
    leaves a fresh timestamp inside a file nobody is updating, and mtime
    cannot be faked by stale content.
    """
    now = time.time() if now is None else now
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        raise ReaderUnavailable("authority.json missing")
    except OSError as exc:
        raise ReaderUnavailable(
            f"authority.json unreadable ({exc.__class__.__name__})")

    age = now - stat.st_mtime
    if age > freshness_sec:
        raise ReaderUnavailable(f"authority.json stale ({age:.0f}s old)")

    # Unparseable is NOT ReaderUnavailable: a corrupt authority file is a
    # surprise, and assemble() attaches the exception class so it reads
    # as one instead of as a tidy expected state.
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    if data.get("schema") != "v1":
        raise ReaderUnavailable(
            f"authority.json unsupported schema {data.get('schema')!r}")

    return {
        "source": "hf-timestd-authority",
        "t_level_active": data.get("t_level_active"),
        "sigma_ns": data.get("sigma_ns"),
        "t_level_witnesses": list(data.get("t_level_witnesses") or []),
        "disagreement_flags": list(data.get("disagreement_flags") or []),
        # Additive v1 extension; absent when the producer publishes none.
        "t6_authority_state": data.get("t6_authority_state"),
        "snapshot_age_s": age,
    }


def _read_gap_tsv(path: str = GAP_TSV_PATH, now: Optional[float] = None) -> dict:
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        raise ReaderUnavailable(f"{path} missing — gap sampler never ran")
    except OSError as exc:
        raise ReaderUnavailable(
            f"{path} unreadable ({exc.__class__.__name__})")
    return parse_gap_row(text, now)


def _read_backlog(paths: HeartbeatPaths, timeout: int = BACKLOG_TIMEOUT_SEC) -> dict:
    """Ask hs-uploader's own backlog module, out-of-process.

    ``python -m hs_uploader.backlog <db>`` is stdlib-only by design and
    prints exactly one JSON object, so this needs no venv and no import
    of hs-uploader into sigmond's interpreter — the uploader can be a
    different Python, mid-upgrade, or broken, without taking the whole
    heartbeat down with it.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = paths.hs_uploader_src
    cmd = [sys.executable, "-m", "hs_uploader.backlog", str(paths.watermarks_db)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired:
        raise ReaderUnavailable(f"hs_uploader.backlog timed out after {timeout}s")
    except OSError as exc:
        raise ReaderUnavailable(
            f"hs_uploader.backlog could not run ({exc.__class__.__name__}: {exc})")
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else "no stderr"
        raise ReaderUnavailable(
            f"hs_uploader.backlog exited {proc.returncode}: {tail}")
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise ReaderUnavailable(f"hs_uploader.backlog output unparseable: {exc}")


def _read_resources() -> dict:
    """Fields dict from the local_resources probe.

    NOTE the probe persists a /proc snapshot for the next run's delta
    math; that is its normal contract (rates need two samples) and the
    heartbeat's steady cadence is a good driver for it.
    """
    from .discovery import local_resources
    from .environment import load_environment

    observations = local_resources.probe(load_environment())
    if not observations:
        raise ReaderUnavailable("local_resources probe returned nothing")
    return observations[0].fields


def read_uptime(path: str = "/proc/uptime") -> Optional[float]:
    """Host uptime in seconds, or None.  None, never 0: a fabricated
    zero would read as "just rebooted" on the board."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# spool
# ---------------------------------------------------------------------------

def write_tick(envelope: dict, spool_dir=SPOOL_DIR) -> Path:
    """Atomically write one heartbeat to the spool; return its path.

    Atomic because a separate uploader reads this directory: a reader
    that catches a half-written file would ship a truncated envelope, and
    a truncated envelope is a lie in the shape of a fact.  Write to
    ``<name>.tmp``, flush, fsync, ``os.replace`` — the rename is the
    commit point, so a crash leaves either nothing or a complete file.

    Also prunes: see ``prune_spool``.
    """
    spool = Path(spool_dir)
    spool.mkdir(parents=True, exist_ok=True)

    station = str(envelope.get("station") or "unknown")
    stamp = _stamp_for(envelope)
    name = f"{_safe_component(station)}_{stamp}.json"
    final = spool / name
    tmp = spool / f"{name}.tmp"

    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(envelope, fh, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)
    except Exception:
        # Never leave a .tmp behind un-noted: an orphan accumulates
        # silently and the next operator inherits a spool full of
        # fragments with no record of why.
        try:
            tmp.unlink()
        except OSError as unlink_exc:
            log.warning("heartbeat: orphaned temp file %s (%s)", tmp, unlink_exc)
        raise

    pruned = prune_spool(spool)
    if pruned:
        log.info("heartbeat: pruned %d spooled tick(s) older than %dh",
                 pruned, SPOOL_MAX_AGE_SEC // 3600)
    return final


def prune_spool(spool_dir, max_age_s: int = SPOOL_MAX_AGE_SEC,
                now: Optional[float] = None) -> int:
    """Delete spooled ticks older than ``max_age_s``; return the count.

    The spool must stay bounded across a long server outage, and when it
    has to be bounded the NEWEST ticks are the valuable ones — nobody
    diagnoses a live station from yesterday's heartbeat, but a full
    filesystem takes the station itself down.  Age is by mtime, which
    for a spool file is its write time.
    """
    now = time.time() if now is None else now
    cutoff = now - max_age_s
    pruned = 0
    try:
        entries = list(Path(spool_dir).glob("*.json"))
    except OSError:
        return 0
    for path in entries:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                pruned += 1
        except OSError:
            continue        # raced with the uploader; it will age out again
    return pruned


def _stamp_for(envelope: dict) -> str:
    emitted = envelope.get("emitted_at")
    try:
        return datetime.strptime(
            str(emitted), _EMITTED_AT_FORMAT).strftime(_FILENAME_STAMP_FORMAT)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).strftime(_FILENAME_STAMP_FORMAT)


def _safe_component(value: str) -> str:
    """Filename-safe station name.

    A station name arrives from config and lands in a path; a '/' in it
    would write outside the spool.  Substitute rather than reject, so a
    surprising name costs a mangled filename and not a lost heartbeat.
    """
    return "".join(
        c if (c.isalnum() or c in "._-") else "-" for c in value) or "unknown"
