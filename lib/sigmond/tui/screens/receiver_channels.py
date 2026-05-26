"""Receiver channels screen — per-client view of live radiod channels.

For a chosen ``<client>@<reporter_id>`` instance, this screen shows
the radiod the client is consuming from and the receiver channels
(unique SSRCs) currently active for that client's configured
frequencies.  The point: "what is wspr-recorder@AC0G-B1 actually
processing right now, and are all expected channels up?"

Filtering strategy:
  * Read the client's per-instance config (or the hf-timestd
    singleton config) to extract the radiod status address and the
    set of configured frequencies.
  * Run ka9q-python's ``discover_channels(status, ...)`` to fetch
    every live channel on that radiod.
  * Match by frequency: each client uses a distinct frequency set
    (WSPR sub-bands vs FT8 sub-bands vs HFDL bands vs CODAR
    sub-bands), so freq alone disambiguates which group belongs to
    the selected client.  Per-channel multicast destination is
    shown so the operator can also see the per-client RTP grouping.

This is purely read-only; the screen never mutates radiod state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Select, Static
from textual.worker import Worker, WorkerState

from ...instance import list_instances


# Map encoding int → human-friendly name.  Mirrors ka9q-python's
# Encoding enum so the operator sees "s16be" instead of "4".
_ENCODING_NAMES = {
    0: "unknown",
    1: "s16le",
    2: "s16be",
    3: "f32le",
    4: "s16be",
    5: "f32",
}


def _decode_encoding(enc: int | None) -> str:
    if enc is None:
        return "?"
    return _ENCODING_NAMES.get(int(enc), str(enc))


# HFDL band center frequencies (Hz).  Mirrors
# hfdl_recorder.bands.HFDL_BANDS; we duplicate it here because the
# sigmond TUI runs in its own venv and can't import from the
# hfdl-recorder package.  The HFDL band plan is stable
# (ICAO/ARINC 635) — these are well-known frequencies that have
# not changed in a decade and aren't expected to.
_HFDL_BAND_CENTERS_HZ = {
    "HFDL2":  2998000,    "HFDL3":  4654000,    "HFDL4":  5544000,
    "HFDL5":  5814000,    "HFDL6":  6529000,    "HFDL8":  8927000,
    "HFDL10": 10027000,   "HFDL11": 10081000,   "HFDL13": 11184000,
    "HFDL15": 13264000,   "HFDL17": 15025000,   "HFDL21": 21997000,
}


def _parse_wspr_freq(token: str) -> Optional[int]:
    """Parse a WSPR frequency token in plain-Hz, MHz, or kHz notation.

    Examples:
        "14095600"   → 14095600
        "14m095600"  → 14095600
        "474k200"    → 474200
    Returns None on malformed input.
    """
    if not isinstance(token, str):
        return None
    s = token.strip().replace("_", "")
    try:
        if "m" in s:
            mhz, _, rest = s.partition("m")
            return int(mhz) * 1_000_000 + (int(rest) if rest else 0)
        if "k" in s:
            khz, _, rest = s.partition("k")
            return int(khz) * 1_000 + (int(rest) if rest else 0)
        return int(s)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Per-instance config readers — extract (status_dns, configured_freqs_hz).
# ---------------------------------------------------------------------------


def _read_toml(path: Path) -> dict:
    import tomllib
    with open(path, "rb") as f:
        return tomllib.load(f)


def _client_config_path(client: str, instance: str) -> Optional[Path]:
    """Return the canonical per-instance config path, or fall back to
    the singleton config for hf-timestd."""
    if client == "hf-timestd":
        return Path("/etc/hf-timestd/timestd-config.toml")
    per_instance = Path(f"/etc/{client}/{instance}.toml")
    if per_instance.exists():
        return per_instance
    return None


def _extract_status_and_freqs(client: str, cfg: dict) -> tuple[str, set[int]]:
    """Return (status_dns, configured_freqs_hz) for the given client.

    Each client lays out its config differently; the canonical mDNS
    status name and the frequencies it tunes are at different keys.
    Returns ("", set()) on parse failure so the caller can degrade
    gracefully (the worker shows the radiod's full channel list).
    """
    if client == "psk-recorder":
        blocks = cfg.get("radiod") or []
        if isinstance(blocks, dict):
            blocks = [blocks]
        status = ""
        freqs: set[int] = set()
        for b in blocks:
            if not status:
                status = str(b.get("status") or "")
            for mode in ("ft8", "ft4"):
                m = b.get(mode) or {}
                for hz in m.get("freqs_hz", []) or []:
                    freqs.add(int(hz))
        return status, freqs

    if client == "wspr-recorder":
        rad = cfg.get("radiod") or {}
        status = str(rad.get("status") or "")
        freqs: set[int] = set()
        # The wsprdaemon-style config keeps frequencies as a list of
        # strings under [frequencies].bands (each string in plain-Hz
        # / MHz / kHz notation).  The newer [[band]] array-of-tables
        # form is also accepted in case a host uses that layout.
        for tok in (cfg.get("frequencies") or {}).get("bands", []) or []:
            hz = _parse_wspr_freq(tok)
            if hz is not None:
                freqs.add(hz)
        for band in cfg.get("band", []) or []:
            hz = _parse_wspr_freq(str(band.get("frequency", "")))
            if hz is not None:
                freqs.add(hz)
        return status, freqs

    if client == "hfdl-recorder":
        blocks = cfg.get("radiod") or []
        if isinstance(blocks, dict):
            blocks = [blocks]
        status = ""
        freqs = set()
        for b in blocks:
            if not status:
                status = str(b.get("status") or "")
            bands_block = (b.get("bands") or {}).get("enabled", []) or []
            for name in bands_block:
                hz = _HFDL_BAND_CENTERS_HZ.get(name)
                if hz is not None:
                    freqs.add(hz)
        return status, freqs

    if client == "codar-sounder":
        blocks = cfg.get("radiod") or []
        if isinstance(blocks, dict):
            blocks = [blocks]
        status = ""
        freqs = set()
        for b in blocks:
            if not status:
                status = str(b.get("status") or "")
            for tx in (b.get("transmitter") or []):
                hz = tx.get("center_freq_hz")
                if hz is not None:
                    try:
                        freqs.add(int(hz))
                    except (TypeError, ValueError):
                        pass
        return status, freqs

    if client == "hf-timestd":
        ka9q = cfg.get("ka9q") or {}
        status = str(ka9q.get("status") or "")
        freqs = set()
        recorder = cfg.get("recorder") or {}
        for group in (recorder.get("channel_group") or {}).values():
            for ch in (group.get("channels") or []):
                hz = ch.get("frequency_hz")
                if hz is not None:
                    try:
                        freqs.add(int(hz))
                    except (TypeError, ValueError):
                        pass
        return status, freqs

    return "", set()


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------


_SUPPORTED_CLIENTS = (
    "psk-recorder",
    "wspr-recorder",
    "hfdl-recorder",
    "codar-sounder",
    "hf-timestd",
)


def _instance_options() -> list[tuple[str, str]]:
    """Build (label, value) pairs for the client@instance Select.

    Values are encoded as ``<client>|<reporter_id>`` so the screen
    can split them on dispatch.  hf-timestd is special-cased (one
    singleton entry) since it doesn't currently follow the
    reporter-keyed templated pattern.
    """
    options: list[tuple[str, str]] = []

    for client in ("psk-recorder", "wspr-recorder",
                   "hfdl-recorder", "codar-sounder"):
        try:
            for inst in list_instances(catalog_clients=[client]):
                label = f"{client}@{inst.reporter_id}"
                value = f"{client}|{inst.reporter_id}"
                options.append((label, value))
        except Exception:
            continue

    # hf-timestd singleton — one entry, no reporter suffix.
    if Path("/etc/hf-timestd/timestd-config.toml").exists():
        options.append(("hf-timestd (singleton)", "hf-timestd|"))

    return options


class ReceiverChannelsScreen(Vertical):
    """Per-client live view of radiod source + receiver channels."""

    DEFAULT_CSS = """
    ReceiverChannelsScreen {
        padding: 1;
    }
    ReceiverChannelsScreen .rc-title {
        text-style: bold;
        margin-top: 1;
        margin-bottom: 0;
    }
    ReceiverChannelsScreen #rc-controls {
        height: 3;
        margin-top: 1;
    }
    ReceiverChannelsScreen #rc-controls Select {
        width: 50;
    }
    ReceiverChannelsScreen #rc-summary {
        margin-top: 1;
        color: $text-muted;
    }
    ReceiverChannelsScreen #rc-status {
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._options = _instance_options()

    def compose(self):
        yield Static("Receiver channels — per-client view of live radiod state",
                     classes="rc-title")
        yield Static(
            "stage: client per-instance config + live radiod channel "
            "discovery (read-only).  Shows the radiod the selected "
            "client is consuming from and every channel (SSRC) the "
            "client's configured frequencies are mapped to.",
            classes="rc-body")
        with Horizontal(id="rc-controls"):
            opts = self._options or [("(no instances configured)", "")]
            yield Select(
                opts, value=opts[0][1], id="rc-instance",
                allow_blank=False,
            )
            yield Button("Refresh", id="rc-refresh", variant="default")

        yield Static("", id="rc-summary")
        table = DataTable(id="rc-channels", zebra_stripes=True)
        table.add_columns(
            "SSRC", "Freq (MHz)", "Preset", "Rate", "Encoding",
            "SNR (dB)", "Multicast dest",
        )
        yield table
        yield Static("idle — select a client to populate", id="rc-status")

    def on_mount(self) -> None:
        if self._options:
            self._refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rc-refresh":
            self._refresh()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "rc-instance":
            self._refresh()

    def _refresh(self) -> None:
        sel = self.query_one("#rc-instance", Select).value
        if not sel:
            self.query_one("#rc-status", Static).update(
                "[yellow]No client instance configured on this host.[/]")
            return
        self.query_one("#rc-status", Static).update(
            "[dim]Querying radiod (≤ 10 s)…[/]")
        self.run_worker(
            lambda: self._fetch(str(sel)),
            thread=True, group="rc", exclusive=True,
        )

    @staticmethod
    def _fetch(sel: str) -> dict:
        """Worker thread: read config, discover channels, filter, return."""
        result: dict = {"sel": sel}
        try:
            client, _, reporter = sel.partition("|")
            result["client"] = client
            result["reporter"] = reporter

            cfg_path = _client_config_path(client, reporter)
            if cfg_path is None or not cfg_path.exists():
                result["error"] = (
                    f"no per-instance config at /etc/{client}/{reporter}.toml"
                )
                return result
            result["config_path"] = str(cfg_path)
            cfg = _read_toml(cfg_path)
            status_dns, configured_freqs = _extract_status_and_freqs(
                client, cfg,
            )
            result["status_dns"] = status_dns
            result["configured_freqs"] = sorted(configured_freqs)
            if not status_dns:
                result["error"] = (
                    "no radiod status address in config (look for "
                    "[radiod] status / [[radiod]] status / [ka9q] "
                    "status)"
                )
                return result

            try:
                from ka9q import discover_channels  # type: ignore
            except ImportError:
                result["error"] = "ka9q-python not installed"
                return result

            try:
                channels = discover_channels(
                    status_dns, listen_duration=10.0,
                )
            except Exception as exc:
                result["error"] = f"discover_channels: {exc}"
                return result

            # Build a frequency → list-of-channels map so we can show
            # the unique-by-mcast group when the same freq has multiple
            # consumers.
            rows: list[dict] = []
            for ssrc, ch in channels.items():
                try:
                    freq_hz = int(round(float(ch.frequency)))
                except (TypeError, ValueError):
                    continue
                if configured_freqs and freq_hz not in configured_freqs:
                    continue
                rows.append({
                    "ssrc": int(ssrc),
                    "frequency_hz": freq_hz,
                    "preset": getattr(ch, "preset", "?"),
                    "sample_rate": int(getattr(ch, "sample_rate", 0) or 0),
                    "encoding": getattr(ch, "encoding", None),
                    "snr": getattr(ch, "snr", None),
                    "multicast_address": getattr(ch, "multicast_address", ""),
                    "port": getattr(ch, "port", 0),
                })

            rows.sort(key=lambda r: (r["frequency_hz"], r["ssrc"]))
            result["rows"] = rows
            result["total_channels"] = len(channels)
            return result
        except Exception as exc:
            result["error"] = f"unexpected: {exc}"
            return result

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.state != WorkerState.SUCCESS:
            return
        if event.worker.group != "rc":
            return
        data = event.worker.result or {}
        if not isinstance(data, dict):
            return

        status_widget = self.query_one("#rc-status", Static)
        summary = self.query_one("#rc-summary", Static)
        table = self.query_one("#rc-channels", DataTable)
        table.clear()

        if "error" in data:
            status_widget.update(f"[red]{data['error']}[/]")
            summary.update("")
            return

        configured_n = len(data.get("configured_freqs") or [])
        rows = data.get("rows") or []
        # Group by multicast destination to surface per-client RTP
        # grouping (helps the operator confirm channels really do
        # belong to this client and aren't another peer on the same
        # frequency).
        mcast_groups: dict[tuple, int] = {}
        for r in rows:
            key = (r["multicast_address"], r["port"])
            mcast_groups[key] = mcast_groups.get(key, 0) + 1

        summary.update(
            f"radiod = [bold]{data.get('status_dns', '?')}[/]  •  "
            f"config = [dim]{data.get('config_path', '?')}[/]\n"
            f"{len(rows)} matching channel(s) "
            f"({configured_n} configured / "
            f"{data.get('total_channels', 0)} live on radiod)  "
            f"across {len(mcast_groups)} multicast destination(s)"
        )

        for r in rows:
            ssrc = r["ssrc"]
            freq_mhz = f"{r['frequency_hz'] / 1_000_000:.6f}"
            preset = str(r["preset"])
            rate = f"{r['sample_rate']:,}"
            enc = _decode_encoding(r["encoding"])
            snr = r["snr"]
            if snr is None or snr == float("-inf"):
                snr_str = "—"
            else:
                try:
                    snr_str = f"{float(snr):+.1f}"
                except (TypeError, ValueError):
                    snr_str = "?"
            mcast = (
                f"{r['multicast_address']}:{r['port']}"
                if r["multicast_address"] else "—"
            )
            table.add_row(str(ssrc), freq_mhz, preset, rate, enc,
                          snr_str, mcast)

        if not rows:
            status_widget.update(
                "[yellow]no live channels match this client's configured "
                "frequencies — is the daemon running?[/]"
            )
        else:
            status_widget.update("")
