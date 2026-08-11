"""PSWS enrolment + per-instance upload control — TUI counterpart to
``smd psws {status,enroll,verify}``, ``smd config register-radiod``
(read-only context only), and ``smd config upload <client> [<instance>]``.

Two panes on one screen because they share an operator workflow: a
freshly-installed dasi2 station has to (1) enroll with the PSWS portal
so hf-timestd / mag-recorder can upload, and (2) decide, per recorder
INSTANCE, whether wspr-recorder / psk-recorder / meteor-scatter push
their spots upstream at all.  Neither had a TUI counterpart before this
screen — the operator had to drop to a shell.

Enrolment pane:
  smd psws status                         (read-only; this pane IS that
                                            view, via the library call —
                                            `psws status` has no --json)
  smd psws enroll                         ensure the station SSH key
                                            exists; print the pubkey to
                                            register at the PSWS portal
  smd psws verify                         live SFTP login test; success
                                            is recorded in
                                            /etc/sigmond/.psws-verified
  smd config register-radiod              NOT invoked from here — this
                                            pane only checks (read-only)
                                            whether any radiod is
                                            registered in
                                            coordination.toml, since PSWS
                                            instrument data implies a
                                            working receiver upstream of
                                            it.  If none is registered,
                                            the operator is pointed at
                                            the SDR Inventory screen
                                            (which owns that mutation)
                                            rather than duplicating it
                                            here.

Per-instance upload pane:
  smd config upload <client> [instance] [--on|--off] [--via ...]
                                           wspr-recorder / psk-recorder /
                                           meteor-scatter only.  Flips
                                           ONE instance's upload-enable
                                           flag; delivery VERDICTS
                                           (did wsprnet/pskreporter
                                           actually receive it) are
                                           Verifier's job, not this
                                           screen's — see the Verifier
                                           screen (`smd admin verifier
                                           report`).

Reporter IDs are stored path-safe ('=' where the operator typed '/'),
but always shown here in the operator-facing slash form
(``sigmond.instance.display_reporter_id``) — writing the storage form
into a user-facing identity field has previously caused misattributed
uploads.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Label, Select, Static

from ..mutation import confirm_and_run

try:
    from ...instance import display_reporter_id as _display_reporter_id
except ImportError:
    def _display_reporter_id(rid: str) -> str:
        return rid.replace("=", "/")


def _smd_binary() -> str:
    """Resolve the smd CLI binary (same helper as the other mutation
    screens — see backup.py / apply.py / rac.py)."""
    argv0 = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if argv0 and os.path.isfile(argv0) and os.path.basename(argv0) == 'smd':
        return argv0
    return shutil.which('smd') or '/usr/local/bin/smd'


# ---------------------------------------------------------------------------
# Enrolment pane — data gathering (host-touching) + pure rendering
# ---------------------------------------------------------------------------

@dataclass
class RecorderStatus:
    recorder: str
    installed: bool
    station: str = ""
    instrument: str = ""
    key_present: bool = False
    issues: tuple = ()


@dataclass
class ArcData:
    has_profile: bool
    psws_enabled: bool
    station_id: str
    station_key_present: bool
    verified: bool
    verified_at: str
    radiod_registered: bool
    recorders: tuple = field(default_factory=tuple)


def gather_arc() -> ArcData:
    """The screen's only host-touching call for the enrolment pane.
    Mocked wholesale in tests via mock.patch.object (project pattern,
    dc48e80) rather than mocking each of the several library calls it
    makes."""
    from ... import psws
    from ...coordination import load_coordination
    from ...site_profile import load_site_profile

    profile = load_site_profile()
    has_profile = profile is not None
    psws_enabled = bool(getattr(profile, "psws_enabled", False))
    station_id = getattr(profile, "psws_station_id", "") or ""

    station_key_present = psws._exists(psws.STATION_KEY)
    verified = bool(station_id) and psws.is_verified(station_id)
    verified_at = ""
    if verified:
        verified_at = psws._read_marker().get("verified_at", "")

    try:
        radiod_registered = bool(load_coordination().radiods)
    except Exception:                                     # noqa: BLE001
        radiod_registered = False

    recorders = tuple(
        RecorderStatus(
            recorder=rec, installed=st.config_exists,
            station=st.station, instrument=st.instrument,
            key_present=st.key_present, issues=tuple(st.issues),
        )
        for rec in psws.RECORDERS
        for st in (psws.read_state(rec),)
    )

    return ArcData(
        has_profile=has_profile, psws_enabled=psws_enabled,
        station_id=station_id, station_key_present=station_key_present,
        verified=verified, verified_at=verified_at,
        radiod_registered=radiod_registered, recorders=recorders,
    )


def render_arc(data: ArcData) -> str:
    """Pure function: ArcData -> Rich-markup status body.  Kept separate
    from gather_arc() so tests can assert on exact rendered text for
    every enrolment state without touching the host."""
    if not data.has_profile:
        return ("[dim]No site profile (/etc/sigmond/site-profile.toml) — "
                "scaffold one with `smd config render --init`.[/]")
    if not data.psws_enabled:
        return "[dim][psws] disabled in site-profile.toml — nothing to enroll.[/]"
    if not data.station_id:
        return ("[yellow]⚠ PSWS enabled but no station id set in "
                "site-profile.toml.[/]")

    lines = [f"Station: [bold]{data.station_id}[/]"]
    if not data.radiod_registered:
        lines.append(
            "[dim]note: no radiod registered in coordination.toml on "
            "this host yet — see SDR Inventory "
            "(`smd config register-radiod`) if this station has local "
            "receiver hardware.[/]")
    if not data.station_key_present:
        lines.append(
            "[yellow]① station key not created yet — next: press "
            "Enroll (creates the key, prints the public key to "
            "register at the PSWS portal).[/]")
    elif not data.verified:
        lines.append(
            "[yellow]② station key present, not yet verified — "
            "register the public key at the PSWS portal (Enroll "
            "reprints it), then press Verify.[/]")
    else:
        suffix = f" ({data.verified_at})" if data.verified_at else ""
        lines.append(f"[green]✓ enrolment verified{suffix}[/]")

    for rec in data.recorders:
        if not rec.installed:
            continue
        if rec.issues:
            lines.append(
                f"[yellow]⚠ {rec.recorder}: {', '.join(rec.issues)}[/]  "
                f"→ `smd config {rec.recorder} edit`")
        else:
            lines.append(
                f"[green]✓ {rec.recorder}: station={rec.station} "
                f"instrument={rec.instrument}[/]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Upload pane — data gathering + rows
# ---------------------------------------------------------------------------

@dataclass
class UploadRow:
    client: str
    reporter_id: str      # storage form, e.g. "AC0G=S"
    enabled: bool
    delivery: str = ""    # only meaningful for psk-recorder; "" = n/a


def gather_upload_rows() -> list:
    """The screen's only host-touching call for the upload pane."""
    from ...instance import list_instances
    from ...upload import DELIVERY_ON_ENABLE, UPLOAD_ENABLE, env_path_for, is_truthy, read_flag

    rows: list = []
    for client, (flag, _dests) in UPLOAD_ENABLE.items():
        try:
            instances = list_instances(catalog_clients=[client])
        except Exception:                                 # noqa: BLE001
            instances = []
        for inst in instances:
            path = env_path_for(client, inst.reporter_id)
            val = read_flag(path, flag)
            delivery = ""
            if client in DELIVERY_ON_ENABLE:
                dkey, ddefault, _choices = DELIVERY_ON_ENABLE[client]
                delivery = read_flag(path, dkey) or ddefault
            rows.append(UploadRow(client=client, reporter_id=inst.reporter_id,
                                  enabled=is_truthy(val), delivery=delivery))
    return rows


_VIA_CHOICES = [
    ("(default)", ""),
    ("direct", "direct"),
    ("server-merge", "server-merge"),
    ("server-raw", "server-raw"),
]


class PswsScreen(Vertical):
    """PSWS enrolment status/actions + per-instance upload toggles."""

    DEFAULT_CSS = """
    PswsScreen {
        padding: 1;
    }
    PswsScreen .ps-title {
        text-style: bold;
        margin-bottom: 1;
    }
    PswsScreen .ps-section {
        text-style: bold;
        margin-top: 1;
        margin-bottom: 1;
    }
    PswsScreen .ps-body {
        margin-bottom: 1;
        color: $text-muted;
    }
    PswsScreen #ps-arc-status {
        margin-bottom: 1;
    }
    PswsScreen #ps-arc-controls Button {
        margin-right: 1;
    }
    PswsScreen #ps-arc-result {
        margin-top: 1;
        margin-bottom: 1;
    }
    PswsScreen #ps-upload-table {
        height: 12;
        border: solid $primary-background;
        margin-bottom: 1;
    }
    PswsScreen #ps-upload-controls {
        height: 3;
    }
    PswsScreen #ps-upload-controls Label {
        padding-top: 1;
        margin-right: 1;
    }
    PswsScreen #ps-upload-controls Select {
        width: 20;
        margin-right: 2;
    }
    PswsScreen #ps-upload-controls Button {
        margin-right: 1;
    }
    PswsScreen #ps-upload-result {
        margin-top: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._upload_rows: list = []

    # ------------------------------------------------------------------
    def compose(self):
        yield Static("PSWS Enrolment + Upload Control", classes="ps-title")

        yield Static("Enrolment", classes="ps-section")
        yield Static(
            "Registers this STATION's SSH key with the PSWS portal so "
            "hf-timestd / mag-recorder can upload.  Records stay queued "
            "locally either way — enrolment only gates upload, never "
            "capture.", classes="ps-body")
        yield Static("", id="ps-arc-status")
        with Horizontal(id="ps-arc-controls"):
            yield Button("Enroll", id="ps-enroll", variant="primary")
            yield Button("Verify", id="ps-verify", variant="success")
            yield Button("Refresh", id="ps-refresh", variant="default")
        yield Static("", id="ps-arc-result")

        yield Static("Per-instance upload", classes="ps-section")
        yield Static(
            "wspr-recorder / psk-recorder / meteor-scatter upload is a "
            "per-INSTANCE flag — a host running several instances of "
            "the same client (e.g. AC0G/S) can enable some and not "
            "others.  This only flips the enable flag; it does not "
            "verify delivery — see the Verifier screen for wsprnet / "
            "pskreporter delivery verdicts.", classes="ps-body")
        table = DataTable(id="ps-upload-table", zebra_stripes=True,
                          cursor_type="row")
        table.add_columns("Client", "Instance", "Upload", "Delivery")
        yield table
        with Horizontal(id="ps-upload-controls"):
            yield Label("Via (psk-recorder)")
            yield Select(_VIA_CHOICES, value="", allow_blank=False, id="ps-via")
            yield Button("Enable", id="ps-upload-on", variant="success")
            yield Button("Disable", id="ps-upload-off", variant="error")
        yield Static("", id="ps-upload-result")

    def on_mount(self) -> None:
        self._reload()

    # ------------------------------------------------------------------
    def _reload(self) -> None:
        self._refresh_arc()
        self._refresh_uploads()

    def _refresh_arc(self) -> None:
        try:
            data = gather_arc()
        except Exception as exc:                          # noqa: BLE001
            self.query_one("#ps-arc-status", Static).update(
                f"[red]error reading PSWS status: {exc}[/]")
            return
        self.query_one("#ps-arc-status", Static).update(render_arc(data))

    def _refresh_uploads(self) -> None:
        try:
            self._upload_rows = gather_upload_rows()
        except Exception as exc:                          # noqa: BLE001
            self._upload_rows = []
            self.query_one("#ps-upload-result", Static).update(
                f"[red]error reading upload status: {exc}[/]")
            return
        table = self.query_one("#ps-upload-table", DataTable)
        table.clear()
        for row in self._upload_rows:
            status_cell = "[green]ON[/]" if row.enabled else "[dim]off[/]"
            delivery_cell = row.delivery if row.delivery else "[dim]—[/]"
            table.add_row(
                row.client, _display_reporter_id(row.reporter_id),
                status_cell, delivery_cell,
                key=f"{row.client}:{row.reporter_id}",
            )
        if not self._upload_rows:
            self.query_one("#ps-upload-result", Static).update(
                "[dim]no upload-capable instances found — "
                "wspr-recorder / psk-recorder / meteor-scatter — run "
                "`smd admin instance add` first.[/]")

    # ------------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "ps-enroll":
            self._run_enroll()
        elif bid == "ps-verify":
            self._run_verify()
        elif bid == "ps-refresh":
            self._reload()
        elif bid == "ps-upload-on":
            self._toggle_upload(True)
        elif bid == "ps-upload-off":
            self._toggle_upload(False)

    def _run_enroll(self) -> None:
        confirm_and_run(
            self.app,
            title="Enroll this station with PSWS?",
            body=("Ensures the station SSH key exists and prints the "
                  "public key to register at the PSWS portal.  "
                  "Recorders keep recording locally regardless of "
                  "enrolment state."),
            cmd=[_smd_binary(), 'psws', 'enroll'],
            sudo=True,
            on_complete=self._after_arc_action,
        )

    def _run_verify(self) -> None:
        confirm_and_run(
            self.app,
            title="Verify PSWS enrolment?",
            body=("Live SFTP login test as the station id.  Success is "
                  "recorded in /etc/sigmond/.psws-verified; failure "
                  "usually means the public key isn't registered at "
                  "the portal yet."),
            cmd=[_smd_binary(), 'psws', 'verify'],
            sudo=True,
            on_complete=self._after_arc_action,
        )

    def _after_arc_action(self, result: subprocess.CompletedProcess) -> None:
        last = self.query_one("#ps-arc-result", Static)
        argv = ' '.join(result.args) if getattr(result, 'args', None) else ''
        if result.returncode == 0:
            last.update(f"[green]✔ exit 0[/]  {argv}")
        else:
            last.update(f"[red]✘ exit {result.returncode}[/]  {argv}")
        self._refresh_arc()

    # ------------------------------------------------------------------
    def _selected_row(self):
        table = self.query_one("#ps-upload-table", DataTable)
        idx = table.cursor_row
        if idx is None or not (0 <= idx < len(self._upload_rows)):
            return None
        return self._upload_rows[idx]

    def _toggle_upload(self, on: bool) -> None:
        from ...upload import DELIVERY_ON_ENABLE

        result_w = self.query_one("#ps-upload-result", Static)
        row = self._selected_row()
        if row is None:
            result_w.update("[red]select a row in the table first.[/]")
            return

        instance_disp = _display_reporter_id(row.reporter_id)
        cmd = [_smd_binary(), 'config', 'upload', row.client, instance_disp,
               '--on' if on else '--off']
        if on and row.client in DELIVERY_ON_ENABLE:
            via = self.query_one("#ps-via", Select).value
            if via:
                cmd += ['--via', str(via)]

        confirm_and_run(
            self.app,
            title=f"{'Enable' if on else 'Disable'} upload — "
                  f"{row.client}@{instance_disp}?",
            body=(f"Flips the upload-enable flag for "
                  f"{row.client}@{instance_disp} to "
                  f"{'ON' if on else 'off'}.  Does not verify delivery "
                  f"— see the Verifier screen for wsprnet/pskreporter "
                  f"delivery verdicts."),
            cmd=cmd, sudo=True,
            on_complete=self._after_upload_toggle,
        )

    def _after_upload_toggle(self, result: subprocess.CompletedProcess) -> None:
        result_w = self.query_one("#ps-upload-result", Static)
        argv = ' '.join(result.args) if getattr(result, 'args', None) else ''
        if result.returncode == 0:
            result_w.update(f"[green]✔ exit 0[/]  {argv}")
        else:
            result_w.update(f"[red]✘ exit {result.returncode}[/]  {argv}")
        self._refresh_uploads()
