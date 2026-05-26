"""GPSDO live screen — coordinator view + per-device deep dive.

Two views, one screen.

  Main view
    Devices DataTable + Outputs DataTable, populated from
    ``/run/gpsdo/<serial>.json`` (schema v1, written by
    gpsdo-monitor).  Per-device row: serial, model, A-level hint,
    PLL lock, GPS fix, sats used, antenna OK, governed radiod(s).
    Outputs row: OUT1 / OUT2 frequencies + PPS state.

  Deep dive (per-device)
    Click "Deep dive" with a device row selected, OR double-click
    a device row.  Renders the full schema-v1 payload (firmware
    info, PPS-study percentiles, fix age, signal-loss counter,
    A-level reason, etc.) for that one device.  Read-only — there
    is no API for setting GPSDO state from sigmond (the operator
    uses the Leo Bodnar configuration tool for that).

Pre-fix the Deep-dive button shelled out to ``gpsdo-monitor tui``
via ``app.suspend()``.  That binary lives at
``/usr/local/bin/gpsdo-monitor`` which is a system-Python script;
the system Python doesn't have the ``[tui]`` extra installed, so
the subprocess printed "TUI unavailable: No module named
'textual'" and exited non-zero.  Operators saw a flash + red
error message.  The in-screen panel removes that dependency.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, ContentSwitcher, DataTable, Static

GPSDO_RUN_DIR = Path("/run/gpsdo")


def _load_reports() -> list[dict]:
    """Return a list of per-device report dicts from /run/gpsdo/*.json."""
    if not GPSDO_RUN_DIR.is_dir():
        return []
    reports: list[dict] = []
    for path in sorted(GPSDO_RUN_DIR.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("schema") == "v1":
            reports.append(data)
    return reports


# Window (s) during which two RowSelected events on the same row
# count as a "double-click" and open the deep-dive panel.  Same
# threshold as the radiod screen.
_DOUBLE_SELECT_WINDOW_S = 0.6


def _yn(val: object) -> str:
    if val is True:
        return "[green]yes[/]"
    if val is False:
        return "[red]no[/]"
    return "[dim]—[/]"


def _hz_mhz(hz: object) -> str:
    if isinstance(hz, (int, float)) and hz:
        return f"{hz / 1e6:.6f} MHz"
    return "[dim]—[/]"


def _fmt_str(v: Any) -> str:
    if v is None:
        return "—"
    return str(v)


def _fmt_age(sec: Any) -> str:
    if not isinstance(sec, (int, float)):
        return "—"
    if sec < 0:
        return "—"
    if sec < 60:
        return f"{sec:.1f} s"
    if sec < 3600:
        return f"{sec / 60:.1f} min"
    if sec < 86400:
        return f"{sec / 3600:.1f} h"
    return f"{sec / 86400:.1f} d"


class GpsdoScreen(Vertical):
    """Per-device GPSDO live status + per-device deep dive."""

    DEFAULT_CSS = """
    GpsdoScreen {
        padding: 1;
    }
    GpsdoScreen .section-title {
        text-style: bold;
        margin-top: 1;
        margin-bottom: 0;
    }
    GpsdoScreen #gpsdo-status {
        margin-top: 1;
        color: $text-muted;
    }
    GpsdoScreen #gpsdo-main-buttons,
    GpsdoScreen #dd-buttons {
        height: 3;
        margin-top: 1;
        margin-bottom: 1;
    }
    GpsdoScreen #gpsdo-main-buttons Button,
    GpsdoScreen #dd-buttons Button {
        margin-right: 1;
    }
    GpsdoScreen #gpsdo-devices,
    GpsdoScreen #gpsdo-outputs {
        height: auto;
        min-height: 4;
    }
    GpsdoScreen #dd-readout {
        margin-top: 1;
        color: $text-muted;
    }
    GpsdoScreen .dd-section {
        text-style: bold;
        margin-top: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._reports: list[dict] = []
        self._dd_serial: Optional[str] = None
        # Double-select tracking — same threshold/semantics as
        # RadiodScreen.
        self._last_select_ts: float = 0.0
        self._last_select_serial: Optional[str] = None

    def compose(self):
        yield Static("GPSDO live", classes="section-title")
        yield Static("", id="gpsdo-status", markup=True)

        with ContentSwitcher(initial="main-view", id="gpsdo-switcher"):
            with Vertical(id="main-view"):
                yield Static(
                    "Devices  [dim](select a row + 'Deep dive' "
                    "→ full schema-v1 read-out; double-click a row "
                    "→ same thing)[/]",
                    classes="section-title", markup=True,
                )
                # Buttons ABOVE the table so they stay on-screen
                # when the table fills (same fix as radiod screen).
                with Horizontal(id="gpsdo-main-buttons"):
                    yield Button("Deep dive", id="gpsdo-dive",
                                 variant="primary")
                    yield Button("Refresh", id="gpsdo-refresh",
                                 variant="default")
                devices = DataTable(id="gpsdo-devices", cursor_type="row",
                                    zebra_stripes=True)
                devices.add_columns(
                    "Serial", "Model", "A-level", "PLL", "GPS fix",
                    "Sats", "Antenna", "Governs",
                )
                yield devices

                yield Static("Outputs", classes="section-title")
                outputs = DataTable(id="gpsdo-outputs", zebra_stripes=True)
                outputs.add_columns("Serial", "OUT1", "OUT2", "PPS")
                yield outputs

            with Vertical(id="deep-dive-view"):
                yield Static("Deep dive — (select a device first)",
                             id="dd-title", classes="section-title")
                yield Static("", id="dd-device",     classes="dd-section")
                yield Static("", id="dd-health",     classes="dd-section")
                yield Static("", id="dd-outputs",    classes="dd-section")
                yield Static("", id="dd-pps",        classes="dd-section")
                yield Static("", id="dd-alevel",    classes="dd-section")
                yield Static("", id="dd-governs",    classes="dd-section")
                yield Static("", id="dd-readout", markup=True)
                with Horizontal(id="dd-buttons"):
                    yield Button("Refresh", id="dd-refresh",
                                 variant="default")
                    yield Button("◀ Back", id="dd-back", variant="warning")

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self._refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "gpsdo-dive":
            self._enter_deep_dive()
        elif bid == "gpsdo-refresh":
            self._refresh()
        elif bid == "dd-refresh":
            self._refresh_deep_dive()
        elif bid == "dd-back":
            self._exit_deep_dive()

    def on_data_table_row_selected(
            self, event: DataTable.RowSelected) -> None:
        """Two RowSelected events on the same device row within
        _DOUBLE_SELECT_WINDOW_S open the deep-dive panel on that
        device.  Lets the operator drill in without reaching for
        the Deep-dive button."""
        if event.data_table.id != "gpsdo-devices":
            return
        try:
            row = event.data_table.get_row(event.row_key)
            serial = str(row[0]) if row else None
        except (TypeError, KeyError):
            return
        if not serial:
            return
        now = time.monotonic()
        within = (now - self._last_select_ts) <= _DOUBLE_SELECT_WINDOW_S
        same = (self._last_select_serial == serial)
        if within and same:
            self._last_select_ts = 0.0
            self._last_select_serial = None
            self._dd_serial = serial
            self._enter_deep_dive()
        else:
            self._last_select_ts = now
            self._last_select_serial = serial

    # ------------------------------------------------------------------
    # main view
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        status = self.query_one("#gpsdo-status", Static)
        dev_table = self.query_one("#gpsdo-devices", DataTable)
        out_table = self.query_one("#gpsdo-outputs", DataTable)
        dev_table.clear()
        out_table.clear()

        if not GPSDO_RUN_DIR.is_dir():
            status.update(
                "[yellow]/run/gpsdo not present — is gpsdo-monitor "
                "running?[/]"
            )
            return

        reports = _load_reports()
        self._reports = reports
        if not reports:
            status.update(
                "[yellow]No gpsdo reports published yet in /run/gpsdo/[/]"
            )
            return

        for r in reports:
            dev = r.get("device") or {}
            health = r.get("health") or {}
            outputs = r.get("outputs") or {}
            serial = dev.get("serial", "?")
            governs = ", ".join(r.get("governs") or []) or "[dim]—[/]"

            a_level = r.get("a_level_hint", "?")
            a_badge = (f"[green]{a_level}[/]" if a_level == "A1"
                       else f"[yellow]{a_level}[/]")

            dev_table.add_row(
                serial,
                dev.get("model", "?"),
                a_badge,
                _yn(health.get("pll_locked")),
                str(health.get("gps_fix") or "—"),
                str(health.get("sats_used") or "—"),
                _yn(health.get("antenna_ok")),
                governs,
            )
            out_table.add_row(
                serial,
                _hz_mhz(outputs.get("out1_hz")),
                _hz_mhz(outputs.get("out2_hz")),
                _yn(outputs.get("pps_enabled")),
            )

        # If the deep-dive view is currently open, also re-render
        # it from the new payload.
        if self._dd_serial is not None:
            self._render_deep_dive()

        n = len(reports)
        status.update(
            f"[green]{n} device{'s' if n != 1 else ''} reporting[/]"
        )

    def _selected_serial(self) -> Optional[str]:
        table = self.query_one("#gpsdo-devices", DataTable)
        if table.row_count == 0 or table.cursor_row is None:
            return None
        try:
            key = table.coordinate_to_cell_key(
                (table.cursor_row, 0)).row_key
            row = table.get_row(key)
        except Exception:
            return None
        return str(row[0]) if row else None

    # ------------------------------------------------------------------
    # deep dive
    # ------------------------------------------------------------------

    def _enter_deep_dive(self) -> None:
        if self._dd_serial is None:
            self._dd_serial = self._selected_serial()
        if not self._dd_serial:
            self.query_one("#gpsdo-status", Static).update(
                "[yellow]Select a device row first, then Deep dive[/]")
            return
        self.query_one("#gpsdo-switcher", ContentSwitcher).current = \
            "deep-dive-view"
        self._render_deep_dive()

    def _exit_deep_dive(self) -> None:
        self._dd_serial = None
        self.query_one("#gpsdo-switcher", ContentSwitcher).current = \
            "main-view"

    def _refresh_deep_dive(self) -> None:
        self._refresh()
        # _refresh already calls _render_deep_dive when _dd_serial is set.

    def _render_deep_dive(self) -> None:
        if not self._dd_serial:
            return
        report = next(
            (r for r in self._reports
             if (r.get("device") or {}).get("serial") == self._dd_serial),
            None,
        )
        if report is None:
            self.query_one("#dd-readout", Static).update(
                f"[red]No report for serial {self._dd_serial}[/]")
            return

        dev     = report.get("device")    or {}
        health  = report.get("health")    or {}
        outputs = report.get("outputs")   or {}
        pps     = report.get("pps_study") or {}
        host    = report.get("host", "?")
        probe   = report.get("probe_interval_sec", "?")
        written = report.get("written_utc", "?")

        self.query_one("#dd-title", Static).update(
            f"Deep dive — {dev.get('model', '?')} serial={self._dd_serial}"
        )
        self.query_one("#dd-device", Static).update(
            "[bold]Device[/]\n"
            f"  model:           {_fmt_str(dev.get('model'))}\n"
            f"  serial:          {_fmt_str(dev.get('serial'))}\n"
            f"  pid:             {_fmt_str(dev.get('pid'))}\n"
            f"  hid_path:        {_fmt_str(dev.get('hid_path'))}\n"
            f"  firmware:        {_fmt_str(dev.get('firmware'))}  "
            f"(source: {_fmt_str(dev.get('firmware_source'))})\n"
            f"  host:            {host}\n"
            f"  probe interval:  {probe} s\n"
            f"  written:         {written}"
        )
        self.query_one("#dd-health", Static).update(
            "[bold]Health[/]\n"
            f"  PLL locked:        {_yn(health.get('pll_locked'))}\n"
            f"  outputs enabled:   {_yn(health.get('outputs_enabled'))}\n"
            f"  FLL mode:          {_yn(health.get('fll_mode'))}\n"
            f"  GPS fix:           {_fmt_str(health.get('gps_fix'))}  "
            f"sats used: {_fmt_str(health.get('sats_used'))}  "
            f"fix age: {_fmt_age(health.get('fix_age_sec'))}\n"
            f"  GPS locked:        {_yn(health.get('gps_locked'))}\n"
            f"  antenna OK:        {_yn(health.get('antenna_ok'))}\n"
            f"  signal-loss count: {_fmt_str(health.get('signal_loss_count'))}"
        )
        self.query_one("#dd-outputs", Static).update(
            "[bold]Outputs[/]\n"
            f"  OUT1: {_hz_mhz(outputs.get('out1_hz'))}  "
            f"power={_fmt_str(outputs.get('out1_power'))}\n"
            f"  OUT2: {_hz_mhz(outputs.get('out2_hz'))}  "
            f"power={_fmt_str(outputs.get('out2_power'))}\n"
            f"  PPS:  {_yn(outputs.get('pps_enabled'))}  "
            f"drive: {_fmt_str(outputs.get('drive_ma'))} mA"
        )
        if pps.get("enabled"):
            self.query_one("#dd-pps", Static).update(
                "[bold]PPS study[/]  [dim]"
                f"window {pps.get('window_sec','?')} s, "
                f"{pps.get('edges','?')} edges  •  "
                f"{pps.get('note', '')}[/]\n"
                f"  period p50: {_fmt_str(pps.get('period_ms_p50'))} ms\n"
                f"  period p95: {_fmt_str(pps.get('period_ms_p95'))} ms\n"
                f"  last edge:  {_fmt_str(pps.get('last_edge_utc'))}"
            )
        else:
            self.query_one("#dd-pps", Static).update(
                "[bold]PPS study[/]  [dim](disabled)[/]"
            )
        a_level = report.get("a_level_hint", "?")
        a_reason = report.get("a_level_reason", "")
        a_color = "green" if a_level == "A1" else "yellow"
        self.query_one("#dd-alevel", Static).update(
            f"[bold]A-level hint[/]  [{a_color}]{a_level}[/]  "
            f"[dim]{a_reason}[/]"
        )
        governs = report.get("governs") or []
        self.query_one("#dd-governs", Static).update(
            "[bold]Governs[/]\n  "
            + ("\n  ".join(governs) if governs else "[dim]—[/]")
        )
        fa = report.get("firmware_advisory")
        if fa:
            self.query_one("#dd-readout", Static).update(
                f"[yellow]firmware advisory:[/] {fa}"
            )
        else:
            self.query_one("#dd-readout", Static).update("")
