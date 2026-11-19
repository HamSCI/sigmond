"""Build one drift report from the six collectors and the exceptions file.

``build(station, readers=...)`` runs every collector through
``run_collectors`` (one that raises becomes one fault item; the rest still
report), then applies ``/etc/sigmond/drift-exceptions.toml``.

``Readers`` bundles what the collectors read.  A data field may hold the
value itself or a zero-argument callable that returns it; a callable runs
inside its collector, so a topology or catalog that cannot be read becomes
that collector's fault instead of sinking the report.  ``station_readers``
fills the fields from the live station; the tests fill them with fakes.

``render_text`` lives here too, so every caller prints the same lines.
"""

from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from sigmond.drift import exceptions as drift_exceptions
from sigmond.drift.builds import build_items
from sigmond.drift.model import KINDS, Item, Report, run_collectors
from sigmond.drift.overrides import override_items
from sigmond.drift.source import host_items, source_items
from sigmond.drift.strays import stray_items, systemd_unit_env
from sigmond.drift.units import unit_items

DECODER_DIR = Path("/usr/local/libexec/sigmond/decoders")
DRIFT_JSON = Path("/var/lib/sigmond/drift.json")
FRESH_S = 36 * 3600


def _v(x):
    """A Readers data field: the value, or what its callable returns."""
    return x() if callable(x) else x


def _systemctl_enabled(unit: str) -> bool:
    try:
        r = subprocess.run(["systemctl", "is-enabled", "--quiet", unit],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


@dataclass
class Readers:
    """Inputs of the six collectors (Tasks 2-6) and the exceptions file
    (Task 7).  Data fields take a value or a zero-argument callable."""
    # source + host (Task 2)
    plan: Any = ()                       # align.Item list, classified
    release: Optional[str] = None        # baseline_of(aligned record)[0]
    baseline: str = "none"               # baseline_of(aligned record)[1]
    host_record: Any = None              # host-aligned.json, or None
    file_drift: Any = ()                 # align.image_file_drift's dicts
    # build (Task 3), unit (Task 4), stray (Task 6)
    base: Path = Path("/opt/git/sigmond")
    components: Any = ()                 # checkout names under base
    enabled: Any = field(default_factory=dict)   # topology name -> bool
    decoder_dir: Path = DECODER_DIR
    run: Callable = subprocess.run
    stamp_dir: Optional[Path] = None
    sigmond_repo: Optional[Path] = None  # default: base / "sigmond"
    unit_dir: Path = Path("/etc/systemd/system")
    is_enabled: Callable[[str], bool] = _systemctl_enabled
    # override (Task 5)
    client_configs: Any = field(default_factory=dict)
    catalog_override: Path = Path("/etc/sigmond/catalog.toml")
    pins_file: Path = Path("/etc/sigmond/component-pins.toml")
    coordination: Path = Path("/etc/sigmond/coordination.toml")
    manifest: Any = field(default_factory=dict)  # component -> commit
    # stray (Task 6)
    catalog_names: Any = field(default_factory=set)
    unit_refs: Any = ()
    unit_env: Callable = systemd_unit_env
    # exceptions (Task 7)
    exceptions_path: Optional[Path] = None  # default: the module's path
    now: Optional[datetime] = None


def build(station: str, *, readers: Readers) -> Report:
    r = readers
    base = Path(r.base)
    sigmond_repo = Path(r.sigmond_repo) if r.sigmond_repo else base / "sigmond"
    collectors = [
        ("source", lambda: source_items(list(_v(r.plan) or ()),
                                        baseline=r.baseline)),
        ("build", lambda: build_items(base, list(_v(r.components) or ()),
                                      enabled=_v(r.enabled) or {},
                                      decoder_dir=Path(r.decoder_dir),
                                      run=r.run, stamp_dir=r.stamp_dir)),
        ("unit", lambda: unit_items(base, _v(r.enabled) or {},
                                    sigmond_repo=sigmond_repo,
                                    unit_dir=Path(r.unit_dir),
                                    is_enabled=r.is_enabled)),
        ("override", lambda: override_items(
            client_configs=_v(r.client_configs) or {},
            catalog_override=r.catalog_override, pins_file=r.pins_file,
            coordination=r.coordination, manifest=_v(r.manifest) or {},
            base=base)),
        ("stray", lambda: stray_items(
            decoder_dir=Path(r.decoder_dir), base=base,
            catalog_names=set(_v(r.catalog_names) or ()),
            unit_refs=list(_v(r.unit_refs) or ()), unit_env=r.unit_env)),
        ("host", lambda: host_items(
            _v(r.host_record), list(_v(r.file_drift) or ()),
            vm_release=r.release, vm_candidate=(r.baseline == "candidate"))),
    ]
    report = Report(station=station, release=r.release, baseline=r.baseline,
                    items=run_collectors(collectors))
    now = r.now or datetime.now(timezone.utc)
    path = r.exceptions_path or drift_exceptions.DEFAULT_PATH
    excs, err = drift_exceptions.load_checked(path, now)
    if err is not None:
        # No exception applies while the file cannot be read; say so as a
        # fault instead of failing the whole report.
        report.items.append(Item(
            "override", "fault", str(path),
            f"drift exceptions file unreadable, so no exception applies: "
            f"{err}", f"repair or move {path}"))
    return drift_exceptions.apply(report, excs, now)


# ---------------------------------------------------------------------------
# The live station's readers
# ---------------------------------------------------------------------------

def station_name() -> str:
    """The reporter id the station uploads under, else the hostname."""
    try:
        from sigmond.coordination import load_coordination
        from sigmond.uploader_manifest import reporter_call
        name = reporter_call(load_coordination())
        if name:
            return name
    except Exception:  # noqa: BLE001 - a name is never worth a failure
        pass
    return socket.gethostname()


def _topology_enabled() -> dict:
    from sigmond.topology import load_topology
    topo = load_topology()
    return {name: bool(c.enabled) for name, c in topo.components.items()}


def _checkout_names(base: Path) -> list:
    from sigmond.doctor import component_checkouts
    return [Path(p).name for p in component_checkouts(base)]


def _client_configs(enabled: Callable[[], dict],
                    etc: Path = Path("/etc")) -> dict:
    """Each enabled component's TOML files under /etc/<name>/.  A file this
    user cannot read is left out (a dry run as a normal user would
    otherwise report every service-owned config as unreadable)."""
    out = {}
    for name, on in sorted(enabled().items()):
        if not on:
            continue
        try:
            paths = sorted((etc / name).glob("*.toml"))
        except OSError:
            continue
        paths = [p for p in paths if os.access(p, os.R_OK)]
        if paths:
            out[name] = paths
    return out


def _catalog_names() -> set:
    from sigmond.catalog import load_catalog
    return set(load_catalog())


def _unit_refs(enabled: Callable[[], dict]) -> list:
    from sigmond.lifecycle import resolve_units
    names = sorted(n for n, on in enabled().items() if on)
    return resolve_units(names, names)


def station_readers(*, plan, release: Optional[str], baseline: str,
                    host_record, file_drift, manifest,
                    base: Path = Path("/opt/git/sigmond")) -> Readers:
    """Readers for this station.  Everything not passed in is read lazily,
    inside the collector that needs it."""
    base = Path(base)
    cache: dict = {}

    def enabled() -> dict:
        if "enabled" not in cache:
            cache["enabled"] = _topology_enabled()
        return cache["enabled"]

    return Readers(
        plan=plan, release=release, baseline=baseline,
        host_record=host_record, file_drift=file_drift, manifest=manifest,
        base=base, components=lambda: _checkout_names(base),
        enabled=enabled,
        client_configs=lambda: _client_configs(enabled),
        catalog_names=_catalog_names,
        unit_refs=lambda: _unit_refs(enabled))


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def _ascii(s) -> str:
    return str(s).encode("ascii", "replace").decode("ascii")


def render_text(report: Report) -> list:
    """The report as lines: items by kind, faults first; then accepted
    items and reminders; then the counts."""
    label = {"blessed": f"blessed {report.release}",
             "candidate": f"candidate {report.release}",
             "none": "no release record"}.get(report.baseline,
                                              report.baseline)
    lines = [f"  drift report ({label}):"]
    items = sorted(report.items, key=lambda i: (
        0 if i.severity == "fault" else 1, KINDS.index(i.kind)))
    if not items:
        lines.append("    nothing differs from the release")
    for it in items:
        lines.append(_ascii(f"    {it.severity:<5} {it.kind:<8} {it.subject}: "
                            f"{it.detail}  fix: {it.fix}"))
    if report.accepted:
        lines.append("  accepted:")
        for a in report.accepted:
            lines.append(_ascii(
                f"    {a.get('kind', '?'):<8} {a.get('key')}: {a.get('reason')} "
                f"(by {a.get('by')}, until {a.get('until')}, "
                f"{a.get('days_left')} days left)"))
    if report.reminders:
        lines.append("  reminders:")
        for m in report.reminders:
            lines.append(_ascii(
                f"    test {m.get('key')} by {m.get('by')} ends {m.get('until')}: "
                f"restore baseline: {m.get('fix')}"))
    n_fault = sum(1 for i in report.items if i.severity == "fault")
    n_drift = len(report.items) - n_fault
    n_test = sum(1 for a in report.accepted if a.get("kind") == "test")
    lines.append(f"  drift: {n_fault} fault{'s' if n_fault != 1 else ''}, "
                 f"{n_drift} drift, {len(report.accepted)} accepted "
                 f"({n_test} test{'s' if n_test != 1 else ''}), "
                 f"{len(report.reminders)} reminder"
                 f"{'s' if len(report.reminders) != 1 else ''}")
    return lines
