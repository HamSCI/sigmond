"""Greenfield (Guided bring-up) screen — parity with `smd bringup` /
`smd admin readiness`.

Task 6 of the TUI reconciliation plan.  Everything host-touching is
mocked at the screen's own module-level probe seams (`_gate_checks`,
`_ts1_state`, `_readiness_report`, `_load_profiles`) so these tests never
read real host state — hardware detection is the single most
host-dependent thing in this codebase and must never run here.

The assertions are on RENDERED CONTENT, not merely that the screen
mounted: an equipment panel that mounts but shows the wrong verdict is
exactly the failure mode this screen has to not have (an operator on
site reads it to decide whether to plug something in).
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

try:
    import textual  # noqa: F401
    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False


# --- canned, inline, test-scoped fixtures ---------------------------------

class _Prof:
    """Stand-in for sigmond.catalog.Profile (same attribute surface)."""

    def __init__(self, name, description, clients=(), infra=(), optional=()):
        self.name = name
        self.description = description
        self.clients = tuple(clients)
        self.local_radiod_infra = tuple(infra)
        self.optional = tuple(optional)


_PROFILES = {
    "dasi2": _Prof("dasi2", "CANNED dasi2 description",
                   clients=("hf-timestd", "wspr-recorder", "mag-recorder"),
                   infra=("igmp-querier", "gpsdo-monitor"),
                   optional=("hf-tec",)),
    "base": _Prof("base", "CANNED base description",
                  clients=("hf-timestd",), infra=("igmp-querier",)),
    "client": _Prof("client", "CANNED client description",
                    clients=("hf-timestd", "psk-recorder"), infra=()),
}

# One row per Presence state the panel has to render.
_GATE_ROWS = [
    ("yes", "RX888 SDR", "attach the RX888 (Cypress FX3) on a USB-3 port"),
    ("no", "GPSDO (Leo Bodnar)", "attach the GPSDO on USB"),
    ("unknown", "Magnetometer (RM3100)",
     "attach the RM3100 via the Pololu USB-I2C adapter"),
]

_TS1_YES = ("yes", "TS-1 witnessed detail",
            "T6 fine stage NOT armed - operating tier is T4.")

_READY = {
    "gate": "site", "profile": "dasi2", "ready": False,
    "counts": {"pass": 7, "warn": 1, "fail": 2, "skip": 0},
    "results": [
        {"name": "structural:ok", "status": "pass", "detail": "fine"},
        {"name": "site:validate", "status": "fail", "detail": "CANNED FAILURE"},
        {"name": "site:identity", "status": "warn", "detail": "CANNED WARNING"},
        {"name": "capture:clean", "status": "fail", "detail": "CANNED FAIL 2"},
    ],
}


def _patched(gate_rows=_GATE_ROWS, ts1=_TS1_YES, readiness=_READY):
    """Every host-touching seam of the screen, mocked with canned values."""
    from sigmond.tui.screens import greenfield as gf
    return (
        mock.patch.object(gf, "_load_profiles", lambda: dict(_PROFILES)),
        mock.patch.object(gf, "_gate_checks",
                          lambda prof, local: list(gate_rows)),
        mock.patch.object(gf, "_ts1_state", lambda: ts1),
        mock.patch.object(gf, "_readiness_report",
                          lambda profile, with_optional, gate="auto":
                          readiness),
    )


class _Harness:
    """Mount GreenfieldScreen alone in a bare Textual app."""

    @staticmethod
    def app():
        from textual.app import App
        from sigmond.tui.screens.greenfield import GreenfieldScreen

        class _App(App):
            def compose(self):
                yield GreenfieldScreen(id="gf")

            def action_show_validate(self):        # fix-it button target
                pass

        return _App()


async def _render(pilot, widget_id):
    from textual.widgets import Static
    for _ in range(12):
        await pilot.pause()
    return str(pilot.app.query_one(widget_id, Static).render())


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class GreenfieldEquipmentPanelTests(unittest.IsolatedAsyncioTestCase):

    async def test_panel_renders_every_presence_state(self):
        """present / MISSING / unconfirmed all reach the screen, in
        bring-up's own vocabulary, with the fix-it hint for anything that
        is not present."""
        with _stack(_patched()):
            app = _Harness.app()
            async with app.run_test(size=(120, 80)) as pilot:
                text = await _render(pilot, "#gf-equip")
        self.assertIn("RX888 SDR", text)
        self.assertIn("present", text)
        self.assertIn("GPSDO (Leo Bodnar)", text)
        self.assertIn("MISSING", text)
        self.assertIn("Magnetometer (RM3100)", text)
        self.assertIn("unconfirmed", text)
        # Hints appear for the not-present rows only.
        self.assertIn("attach the GPSDO on USB", text)
        self.assertIn("Pololu USB-I2C", text)
        self.assertNotIn("USB-3 port", text)

    async def test_ts1_is_separate_from_t6_armed(self):
        """TS-1 detected and T6 armed are DIFFERENT facts: a station can
        witness TS-1 with the fine stage disarmed (shipped images set
        t6_pps.enabled=false).  The panel must never imply otherwise."""
        with _stack(_patched()):
            app = _Harness.app()
            async with app.run_test(size=(120, 80)) as pilot:
                text = await _render(pilot, "#gf-equip")
        self.assertIn("TS-1 refclock", text)
        self.assertIn("TS-1 witnessed detail", text)
        self.assertIn("NOT armed", text)

    async def test_ts1_unknown_when_hf_timestd_not_publishing(self):
        ts1 = ("unknown", "hf-timestd is not publishing yet", "")
        with _stack(_patched(ts1=ts1)):
            app = _Harness.app()
            async with app.run_test(size=(120, 80)) as pilot:
                text = await _render(pilot, "#gf-equip")
        self.assertIn("TS-1 refclock", text)
        self.assertIn("unconfirmed", text)
        self.assertIn("hf-timestd is not publishing yet", text)

    async def test_no_required_devices_says_so(self):
        with _stack(_patched(gate_rows=[])):
            app = _Harness.app()
            async with app.run_test(size=(120, 80)) as pilot:
                text = await _render(pilot, "#gf-equip")
        self.assertIn("no external devices", text)

    async def test_probe_failure_is_surfaced_not_swallowed(self):
        from sigmond.tui.screens import greenfield as gf

        def _boom(prof, local):
            raise RuntimeError("CANNED PROBE EXPLOSION")

        with _stack(_patched()), mock.patch.object(gf, "_gate_checks", _boom):
            app = _Harness.app()
            async with app.run_test(size=(120, 80)) as pilot:
                text = await _render(pilot, "#gf-equip")
        self.assertIn("CANNED PROBE EXPLOSION", text)


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class GreenfieldReadinessPanelTests(unittest.IsolatedAsyncioTestCase):

    async def test_readiness_verdict_rendered_before_bringup(self):
        with _stack(_patched()):
            app = _Harness.app()
            async with app.run_test(size=(120, 80)) as pilot:
                text = await _render(pilot, "#gf-readiness")
        self.assertIn("site", text)          # the gate
        self.assertIn("dasi2", text)         # the profile
        self.assertIn("NOT READY", text)
        self.assertIn("7 pass, 1 warn, 2 fail", text)
        # Failing + warning checks are itemised; passes are not.
        self.assertIn("CANNED FAILURE", text)
        self.assertIn("CANNED WARNING", text)
        self.assertNotIn("structural:ok", text)

    async def test_ready_gate_says_ready(self):
        ok = dict(_READY, ready=True,
                  counts={"pass": 10, "warn": 0, "fail": 0, "skip": 0},
                  results=[{"name": "structural:ok", "status": "pass",
                            "detail": "fine"}])
        with _stack(_patched(readiness=ok)):
            app = _Harness.app()
            async with app.run_test(size=(120, 80)) as pilot:
                text = await _render(pilot, "#gf-readiness")
        self.assertIn("READY", text)
        self.assertNotIn("NOT READY", text)

    async def test_readiness_failure_is_reported_not_silent(self):
        with _stack(_patched(readiness=None)):
            app = _Harness.app()
            async with app.run_test(size=(120, 80)) as pilot:
                text = await _render(pilot, "#gf-readiness")
        self.assertIn("could not run", text)
        self.assertIn("smd admin readiness", text)

    async def test_verdict_reruns_the_gate_after_bringup(self):
        """The AFTER verdict is the point: exit 0 from bring-up is not the
        same claim as 'the station is complete'."""
        with _stack(_patched()):
            app = _Harness.app()
            async with app.run_test(size=(120, 80)) as pilot:
                gf = app.query_one("#gf")
                gf._render_verdict(0, {"profile": "dasi2"})
                text = await _render(pilot, "#gf-readiness")
        self.assertIn("after bring-up", text)


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class GreenfieldProfileTests(unittest.IsolatedAsyncioTestCase):

    async def test_profile_labels_come_from_the_catalog_description(self):
        from textual.widgets import RadioButton
        with _stack(_patched()):
            app = _Harness.app()
            async with app.run_test(size=(120, 80)) as pilot:
                await pilot.pause()
                labels = [str(b.label)
                          for b in app.query(RadioButton)]
                pressed = app.query_one("#gf-profile").pressed_button
        self.assertEqual(len(labels), 3)
        for name in ("dasi2", "base", "client"):
            self.assertTrue(
                any(f"CANNED {name} description" in lbl for lbl in labels),
                f"{name}'s catalog description is not on its radio button",
            )
        # dasi2 leads AND is pre-selected -- `smd bringup` with no profile
        # also defaults to dasi2, so the two must agree.
        self.assertTrue(labels[0].startswith("dasi2"))
        self.assertEqual(pressed.id, "gf-prof-dasi2")


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class GreenfieldArgvTests(unittest.IsolatedAsyncioTestCase):
    """The bringup invocation shape must not have drifted."""

    async def _argv(self, g, dry_run=False):
        with _stack(_patched()):
            app = _Harness.app()
            async with app.run_test(size=(120, 80)) as pilot:
                await pilot.pause()
                return app.query_one("#gf")._build_argv(g, dry_run=dry_run)

    async def test_minimal_argv_shape_unchanged(self):
        argv = await self._argv({"profile": "dasi2", "reporter": "", "grid": "",
                                 "callsign": "", "psws": "", "remote": "",
                                 "with_optional": False})
        self.assertEqual(argv[1:], ["bringup", "dasi2", "--non-interactive"])
        self.assertTrue(argv[0].endswith("smd"))

    async def test_full_argv_shape_unchanged(self):
        argv = await self._argv({"profile": "dasi2", "reporter": "AC0G/S",
                                 "grid": "EM38ww", "callsign": "AC0G",
                                 "psws": "S000170", "remote": "",
                                 "with_optional": True})
        self.assertEqual(
            argv[1:],
            ["bringup", "dasi2", "--non-interactive",
             "--reporter", "AC0G/S", "--grid", "EM38ww",
             "--callsign", "AC0G", "--psws-station-id", "S000170",
             "--with-optional"])

    async def test_remote_profile_argv_shape_unchanged(self):
        argv = await self._argv({"profile": "client", "reporter": "AC0G/S",
                                 "grid": "EM38ww", "callsign": "",
                                 "psws": "", "remote": "bee3-status.local",
                                 "with_optional": False}, dry_run=True)
        self.assertEqual(
            argv[1:],
            ["bringup", "client", "--non-interactive",
             "--reporter", "AC0G/S", "--grid", "EM38ww",
             "--remote-radiod", "bee3-status.local", "--dry-run"])


class HardwareGateConsolidationTests(unittest.TestCase):
    """The screen and `smd bringup` must share ONE detection implementation."""

    def test_screen_delegates_to_the_bringup_gate(self):
        """`_gate_checks` is a pass-through over
        sigmond.hardware.gate_checks -- the same function bin/smd's
        _bringup_hardware_gate assembles its rows from."""
        from sigmond import hardware
        from sigmond.tui.screens import greenfield as gf

        prof = _PROFILES["dasi2"]
        sentinel = [hardware.GateCheck("CANNED DEVICE", None, "CANNED HINT")]
        with mock.patch.object(hardware, "gate_checks",
                               lambda p, local: sentinel):
            rows = gf._gate_checks(prof, True)
        self.assertEqual(rows, [("unknown", "CANNED DEVICE", "CANNED HINT")])

    def test_only_one_detection_module_remains(self):
        """sigmond.hardware_detect was a SECOND, divergent implementation
        (it looked for GPSDO VID 1d50 where the shipped Leo Bodnar is
        1dd2, and a CP210x bridge where mag-recorder uses a Pololu
        USB-I2C).  It is deleted; do not reintroduce it."""
        import importlib
        with self.assertRaises(ImportError):
            importlib.import_module("sigmond.hardware_detect")

    def test_presence_maps_the_bringup_tristate(self):
        from sigmond.hardware import GateCheck, Presence
        self.assertIs(GateCheck("x", True, "").presence, Presence.YES)
        self.assertIs(GateCheck("x", False, "").presence, Presence.NO)
        self.assertIs(GateCheck("x", None, "").presence, Presence.UNKNOWN)


class _stack:
    """Enter a tuple of context managers as one."""

    def __init__(self, mgrs):
        self._mgrs = list(mgrs)

    def __enter__(self):
        for m in self._mgrs:
            m.__enter__()
        return self

    def __exit__(self, *exc):
        for m in reversed(self._mgrs):
            m.__exit__(*exc)
        return False


if __name__ == '__main__':
    unittest.main()
