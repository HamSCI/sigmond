"""Local-resources probe — gathers host-side counters relevant to
packet-loss diagnostics in the RX888 → radiod → RTP pipeline.

Counters captured (subset emitted depends on what the operator declared
in [local_system]):

  cpu_per_core    %usr / %sys / %soft / %idle deltas, per logical CPU,
                  computed against the previous run's /proc/stat snapshot.
  udp             RcvbufErrors / InErrors / InCsumErrors as rates over
                  the inter-run interval.
  nics            For each declared NIC: rx_missed_errors,
                  rx_no_buffer_count, rx_fifo_errors, rx_dropped,
                  multicast — current values from `ethtool -S`.
  irqs            For each declared handler in irq_pins: per-core
                  interrupt counts from /proc/interrupts, plus the list
                  of cores that received any interrupts (for drift
                  detection by the reconciler).
  usb             If any usb_devices declared: count of URB / overrun /
                  reset error lines in `dmesg --since -60sec`.  Coarse
                  by design — dmesg lines don't carry vendor:product
                  info, so per-device attribution waits for a follow-up
                  that talks to RX888 firmware control endpoints.

The probe is pure given its inputs: every external dependency
(/proc reads, ethtool, dmesg, the snapshot store) is injectable so
tests run with no network, no subprocess, no filesystem.

First-run behaviour: when no previous snapshot exists, the rate-based
fields (cpu, udp) emit zeros and `interval_s=0`.  Absolute counters
(nics, irqs, usb) emit normally.  Rates appear on the second run
onward.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from ..environment import Environment, Observation
from . import load_snapshot, save_snapshot


# ---------------------------------------------------------------------------
# Default transports
# ---------------------------------------------------------------------------

def _default_read_proc(path: str) -> str:
    return Path(path).read_text()


def _default_run_ethtool(iface: str, timeout: float) -> str:
    try:
        proc = subprocess.run(
            ["ethtool", "-S", iface],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        return proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _default_read_dmesg(since_seconds: int, timeout: float) -> str:
    try:
        proc = subprocess.run(
            ["dmesg", "--ctime", "--since", f"-{since_seconds}sec"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        return proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
        return ""


# ---------------------------------------------------------------------------
# probe entrypoint
# ---------------------------------------------------------------------------

# NIC counter names we keep from `ethtool -S`.  Anything else is dropped.
_NIC_COUNTERS = (
    "rx_missed_errors",
    "rx_no_buffer_count",
    "rx_fifo_errors",
    "rx_dropped",
    "multicast",
)

_DMESG_USB_PATTERNS = (
    re.compile(r"\burb\b.*\b(?:error|fail)", re.IGNORECASE),
    re.compile(r"\boverrun\b", re.IGNORECASE),
    re.compile(r"\busb\b.*\breset\b", re.IGNORECASE),
)


# --- radiod output block-drops -------------------------------------------
# gap_count is the ONLY honest loss field.  radiod emits a block of zeros
# when it drops a filter output block, and the recorder faithfully writes
# those zeros -- so samples_written and completeness_pct both still read
# 100% while data is genuinely missing.  Each gap also costs up to
# +/-25.6 s of GRAPE spectrogram validity, so the COUNT matters far more
# than the duration.

_SIDECAR_SECONDS = 300


def _summarise_gaps(records, now: float, window_s: int = 3600) -> dict:
    """Aggregate raw_buffer sidecars into gaps per channel-hour.

    Returns ``gap_rate_per_channel_hour = None`` when nothing was in the
    window: a silent 0.0 would read as "perfectly healthy" when in fact
    nothing was measured.
    """
    start = now - window_s
    n = 0
    gaps = 0
    for r in records or []:
        try:
            t = float(r.get("minute_boundary"))
        except (TypeError, ValueError):
            continue
        if start <= t < now:
            n += 1
            gaps += int(r.get("gap_count", 0) or 0)
    channel_hours = n * _SIDECAR_SECONDS / 3600.0
    rate = (gaps / channel_hours) if channel_hours else None
    return {
        "gaps": gaps,
        "channel_hours": channel_hours,
        "gap_rate_per_channel_hour": rate,
    }


def _read_resctrl(root) -> dict:
    """Read radiod's L3 occupancy from a resctrl tree.

    resctrl is HOST-only: RDT is not virtualised into guests, and plenty
    of hosts have no CAT at all.  Every failure path reports
    ``available: False`` rather than a zero, so a missing counter can
    never masquerade as an empty cache.
    """
    try:
        occ_file = Path(root) / "radiod" / "mon_data" / "mon_L3_00" / "llc_occupancy"
        raw = occ_file.read_text().strip()
        return {
            "available": True,
            "radiod_occupancy_mib": int(raw) / (1024.0 * 1024.0),
        }
    except (OSError, ValueError):
        return {"available": False}


_RAW_BUFFER = "/var/lib/timestd/raw_buffer"


def _default_gap_records(root: str = _RAW_BUFFER) -> list:
    """Read the last two days of raw_buffer sidecars.  Soft-fails to []."""
    import glob
    import json
    import os
    out: list = []
    try:
        channels = sorted(os.listdir(root))
    except OSError:
        return out
    for chan in channels:
        cdir = os.path.join(root, chan)
        try:
            days = sorted(os.listdir(cdir))[-2:]
        except OSError:
            continue
        for day in days:
            for f in glob.glob(os.path.join(cdir, day, "*.json")):
                try:
                    with open(f) as fh:
                        out.append(json.load(fh))
                except (OSError, ValueError):
                    continue
    return out



# sigmond#49: filesystems whose headroom decides whether the station can
# keep recording.  Deduplicated by filesystem at probe time, so a root that
# holds everything appears once.
DISK_PATHS = ("/", "/var/lib/timestd", "/var/lib/sigmond", "/var/lib/mag-recorder",
              "/opt/git/sigmond", "/var/log")


def disk_fields(paths=DISK_PATHS, *, statvfs=None, exists=None, errors=None) -> list:
    """[{path, total_bytes, avail_bytes, pct_used}] per DISTINCT filesystem
    among ``paths`` that exist.  ``avail_bytes`` is what a non-root writer
    gets (f_bavail): the recorders run unprivileged, and ext4's 5 % root
    reserve is exactly what made a 100 %-full disk look survivable to a
    root-run install.sh while every recorder was already failing."""
    import os
    statvfs = statvfs or os.statvfs
    exists = exists or os.path.exists
    out: list = []
    seen: set = set()
    for path in paths:
        try:
            if not exists(path):
                continue
            sv = statvfs(path)
        except OSError as exc:
            if errors is not None:
                errors.append(f"{path}: {type(exc).__name__}")
            continue
        key = getattr(sv, "f_fsid", None)
        if not key:
            key = (sv.f_blocks, sv.f_bfree, sv.f_frsize)
        if key in seen:
            continue
        seen.add(key)
        total = sv.f_frsize * sv.f_blocks
        avail = sv.f_frsize * sv.f_bavail
        used = total - sv.f_frsize * sv.f_bfree
        # df's Use%: used / (used + avail) — the root reserve is neither
        # used nor available to a non-root writer, so it is excluded, and
        # the number matches what an operator reads in `df -h`.
        denom = used + avail
        pct = (100.0 * used / denom) if denom else None
        out.append({"path": path, "total_bytes": total, "avail_bytes": avail,
                    "pct_used": None if pct is None else round(pct, 1)})
    return out

def probe(env: Environment, *,
          timeout: float = 5.0,
          limiter=None,
          read_proc: Callable[[str], str] = _default_read_proc,
          run_ethtool: Callable[[str, float], str] = _default_run_ethtool,
          read_dmesg: Callable[[int, float], str] = _default_read_dmesg,
          load_prev: Optional[Callable[[str], Optional[dict]]] = None,
          save_curr: Optional[Callable[[str, dict], None]] = None,
          clock: Callable[[], float] = time.time,
          dmesg_window_seconds: int = 60,
          read_gap_records: Optional[Callable[[], list]] = None,
          resctrl_root: str = "/sys/fs/resctrl",
          ) -> list[Observation]:
    """Gather host-side resource counters into a single Observation.

    See module docstring for the field shape.  ``load_prev`` and
    ``save_curr`` default to the cache-backed helpers in
    ``discovery/__init__.py``; tests inject in-memory equivalents.
    """
    declared = env.local_system
    now = clock()

    if load_prev is None:
        load_prev = load_snapshot
    if save_curr is None:
        save_curr = save_snapshot

    errors: list[str] = []

    # ---- /proc reads (all soft-fail) ----
    proc_stat = _safe_read(read_proc, "/proc/stat", errors)
    proc_net_snmp = _safe_read(read_proc, "/proc/net/snmp", errors)
    proc_interrupts = _safe_read(read_proc, "/proc/interrupts", errors)

    # ---- current raw snapshots ----
    cur_cpu = _parse_proc_stat(proc_stat)
    cur_udp = _parse_proc_net_snmp_udp(proc_net_snmp)
    cur_irq = _parse_proc_interrupts(
        proc_interrupts, declared.irq_pins.keys()
    )

    # ---- previous raw snapshot for delta math ----
    prev = load_prev("local_resources") or {}
    prev_at = float(prev.get("captured_at", 0.0) or 0.0)
    interval_s = max(0.0, now - prev_at) if prev_at > 0 else 0.0

    # ---- derived rates / drift ----
    cpu_per_core = _delta_cpu(prev.get("cpu", {}), cur_cpu)
    udp_rates = _delta_udp(prev.get("udp", {}), cur_udp, interval_s)
    irq_observed = _summarise_irq(cur_irq, declared.irq_pins,
                                  prev_irq=prev.get("irq", {}) or None)

    # ---- per-NIC ethtool snapshots (no rate; absolute counters) ----
    nic_fields: dict = {}
    for nic in declared.nics:
        nic_fields[nic] = _parse_ethtool(run_ethtool(nic, timeout))

    # ---- USB error count from dmesg (only if operator cares) ----
    if declared.usb_devices:
        dmesg_out = read_dmesg(dmesg_window_seconds, timeout)
        usb_fields = _parse_dmesg_usb(
            dmesg_out, declared.usb_devices, dmesg_window_seconds
        )
    else:
        usb_fields = {}

    # ---- radiod output block-drops (the honest loss signal) ----
    gap_records = read_gap_records() if read_gap_records else _default_gap_records()

    fields: dict = {
        "cpu_per_core": cpu_per_core,
        "udp": {**udp_rates, "interval_s": interval_s},
        "nics": nic_fields,
        "irqs": irq_observed,
        "usb": usb_fields,
        "radiod": _summarise_gaps(gap_records, now),
        "llc": _read_resctrl(resctrl_root),
        "disk": disk_fields(errors=errors),
    }
    if errors:
        fields["errors"] = errors

    # Persist current raw snapshot for the next run's delta math.  Done
    # last so a parser exception above doesn't leave a half-written
    # snapshot for the next run to misread.
    save_curr("local_resources", {
        "captured_at": now,
        "cpu": cur_cpu,
        "udp": cur_udp,
        "irq": cur_irq,
    })

    return [Observation(
        source="local_resources",
        kind="local_system",
        id="localhost",
        endpoint="localhost",
        fields=fields,
        observed_at=now,
        ok=not errors,
        error="; ".join(errors) if errors else "",
    )]


def _safe_read(reader: Callable[[str], str], path: str,
               errors: list[str]) -> str:
    try:
        return reader(path)
    except (OSError, ValueError) as e:
        errors.append(f"{path}: {e.__class__.__name__}")
        return ""


# ---------------------------------------------------------------------------
# /proc/stat — per-core jiffy counters
# ---------------------------------------------------------------------------

# Fields after the cpuN label, in order:
# user nice system idle iowait irq softirq steal guest guest_nice
_CPU_FIELDS = ("user", "nice", "system", "idle", "iowait",
               "irq", "softirq", "steal", "guest", "guest_nice")


def _parse_proc_stat(text: str) -> dict:
    """Return ``{"cpu0": {field: jiffies, ...}, ...}`` for per-core lines.

    The aggregate ``cpu`` line (no digit) is skipped — sigmond cares
    about per-core load, not the average.
    """
    out: dict = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        head = parts[0]
        if not head.startswith("cpu") or head == "cpu":
            continue
        try:
            int(head[3:])
        except ValueError:
            continue
        vals = []
        for token in parts[1:1 + len(_CPU_FIELDS)]:
            try:
                vals.append(int(token))
            except ValueError:
                vals.append(0)
        # Pad short rows (older kernels lack guest/guest_nice).
        vals.extend([0] * (len(_CPU_FIELDS) - len(vals)))
        out[head] = dict(zip(_CPU_FIELDS, vals))
    return out


def _delta_cpu(prev: dict, cur: dict) -> list[dict]:
    """Return one dict per core with derived percentages.

    First run (prev empty) yields zeros for the rates — without a
    baseline, computing percentages over absolute since-boot jiffy
    counts would produce lifetime-averages masquerading as
    interval-rates, which is misleading for packet-loss diagnostics.
    """
    out: list[dict] = []
    for label in sorted(cur.keys(),
                        key=lambda k: int(k[3:]) if k[3:].isdigit() else 0):
        c = cur[label]
        p = prev.get(label, {}) if prev else {}
        if not p:
            out.append({
                "core": int(label[3:]) if label[3:].isdigit() else 0,
                "usr": 0.0, "sys": 0.0, "soft": 0.0, "idle": 0.0,
                "total_jiffies": 0,
            })
            continue
        delta = {f: c.get(f, 0) - p.get(f, 0) for f in _CPU_FIELDS}
        total = sum(delta.values())
        if total <= 0:
            out.append({
                "core": int(label[3:]) if label[3:].isdigit() else 0,
                "usr": 0.0, "sys": 0.0, "soft": 0.0, "idle": 0.0,
                "total_jiffies": 0,
            })
            continue
        out.append({
            "core": int(label[3:]),
            "usr":  round(100.0 * delta["user"] / total, 2),
            "sys":  round(100.0 * delta["system"] / total, 2),
            "soft": round(100.0 * delta["softirq"] / total, 2),
            "idle": round(100.0 * delta["idle"] / total, 2),
            "total_jiffies": total,
        })
    return out


# ---------------------------------------------------------------------------
# /proc/net/snmp — UDP block
# ---------------------------------------------------------------------------

def _parse_proc_net_snmp_udp(text: str) -> dict:
    """Extract the ``Udp:`` data row, keyed by header field names."""
    header: list[str] = []
    for line in text.splitlines():
        if not line.startswith("Udp:"):
            continue
        rest = line[len("Udp:"):].strip().split()
        if not header:
            header = rest
            continue
        # Second Udp: line is the values.
        out: dict = {}
        for name, raw in zip(header, rest):
            try:
                out[name] = int(raw)
            except ValueError:
                pass
        return out
    return {}


def _delta_udp(prev: dict, cur: dict, interval_s: float) -> dict:
    """Return RcvbufErrors / InErrors rates plus absolute current values.

    On first run (interval_s=0 or no prev) the rates are 0 but the
    absolute counts come through, so the operator can still see whether
    *any* loss has occurred since boot.
    """
    keys = ("RcvbufErrors", "InErrors", "InCsumErrors")
    out: dict = {}
    for k in keys:
        cur_v = int(cur.get(k, 0) or 0)
        out[f"{_snake(k)}_total"] = cur_v
        if interval_s > 0 and prev:
            delta = cur_v - int(prev.get(k, 0) or 0)
            out[f"{_snake(k)}_rate"] = round(max(0, delta) / interval_s, 4)
        else:
            out[f"{_snake(k)}_rate"] = 0.0
    return out


def _snake(camel: str) -> str:
    """RcvbufErrors → rcvbuf_errors."""
    return re.sub(r"([a-z])([A-Z])", r"\1_\2", camel).lower()


# ---------------------------------------------------------------------------
# /proc/interrupts
# ---------------------------------------------------------------------------

def _parse_proc_interrupts(text: str, handler_names: Iterable[str]) -> dict:
    """Sum per-CPU counts per declared handler.

    Multiple IRQ lines may match a single handler name (e.g. ``xhci_hcd``
    appears once per MSI vector); their per-CPU columns are summed.
    Non-numeric tokens are skipped (the trailing chip/handler name is
    text, not a column).
    """
    handlers = set(handler_names)
    if not handlers:
        return {}

    lines = text.splitlines()
    if not lines:
        return {}

    # First line is the CPU header: "           CPU0       CPU1 ..."
    n_cpus = sum(1 for tok in lines[0].split() if tok.startswith("CPU"))
    if n_cpus == 0:
        return {}

    out: dict = {h: [0] * n_cpus for h in handlers}
    for line in lines[1:]:
        if ":" not in line:
            continue
        _, rest = line.split(":", 1)
        tokens = rest.split()
        if len(tokens) < n_cpus:
            continue
        # Identify handler from trailing tokens.
        trailing = " ".join(tokens[n_cpus:])
        matched = None
        for h in handlers:
            if h in trailing:
                matched = h
                break
        if matched is None:
            continue
        for i in range(n_cpus):
            try:
                out[matched][i] += int(tokens[i])
            except ValueError:
                pass
    return out


def _summarise_irq(cur_irq: dict, declared_pins: dict,
                   prev_irq: Optional[dict] = None) -> dict:
    """Return per-handler observation: which cores actually received
    interrupts vs. the cores the operator declared.

    ``observed_cores`` means "fired SINCE THE LAST PROBE", not "fired at
    some point since boot".  /proc/interrupts is cumulative, so the naive
    reading marks a host drifted forever over an unavoidable boot-time
    transient: on AC0G-B4 2026-08-18 the xhci vector took 60,020
    interrupts on CPU5 in the ~60 s between boot and
    sigmond-rx888-irq-affinity pinning it to 12-13, after which CPU5's
    delta was zero and every interrupt went to CPU13 -- yet cumulative
    counters reported cores [5, 13] indefinitely.

    With no previous snapshot there is no delta, so ``delta_available``
    is False and ``observed_cores`` is empty: the first probe after boot
    reports "not measured" rather than guessing.  Cumulative counts stay
    in ``per_core_count`` for diagnostics.

    The reconciler decides whether a mismatch is degraded — this layer
    only reports facts.
    """
    out: dict = {}
    prev_irq = prev_irq or {}
    for handler, counts in cur_irq.items():
        prev_counts = prev_irq.get(handler)
        if prev_counts is not None and len(prev_counts) == len(counts):
            observed = [i for i, n in enumerate(counts)
                        if n - prev_counts[i] > 0]
            delta_available = True
        else:
            observed = []
            delta_available = False
        out[handler] = {
            "expected_cores": list(declared_pins.get(handler, [])),
            "observed_cores": observed,
            "delta_available": delta_available,
            "per_core_count": list(counts),
        }
    return out


# ---------------------------------------------------------------------------
# ethtool -S
# ---------------------------------------------------------------------------

# `     rx_missed_errors: 0`
_ETHTOOL_LINE = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(\d+)\s*$")


def _parse_ethtool(text: str) -> dict:
    """Keep only the counters in ``_NIC_COUNTERS``."""
    out: dict = {}
    for line in text.splitlines():
        m = _ETHTOOL_LINE.match(line)
        if not m:
            continue
        name, val = m.group(1), m.group(2)
        if name in _NIC_COUNTERS:
            out[name] = int(val)
    return out


# ---------------------------------------------------------------------------
# dmesg-based USB error counters
# ---------------------------------------------------------------------------

def _parse_dmesg_usb(text: str, declared_devices: list,
                     window_s: int) -> dict:
    """Coarse-grained USB error tally from dmesg over ``window_s``.

    Lines aren't reliably attributable to specific vendor:product IDs,
    so the count is host-wide.  ``declared_devices`` is recorded for
    context (so the field's presence is a clear signal that the operator
    cares about USB) but not used for filtering yet.
    """
    counts = {"urb_errors": 0, "overruns": 0, "resets": 0}
    for line in text.splitlines():
        if _DMESG_USB_PATTERNS[0].search(line):
            counts["urb_errors"] += 1
        if _DMESG_USB_PATTERNS[1].search(line):
            counts["overruns"] += 1
        if _DMESG_USB_PATTERNS[2].search(line):
            counts["resets"] += 1
    counts["window_seconds"] = window_s
    counts["watched_devices"] = list(declared_devices)
    return counts
