"""Tests for the PSWS enrolment + per-instance upload screen (Task 5 of
the TUI reconciliation plan).

Follows the project pattern (dc48e80 / test_tui_overview.py): pure
rendering functions are tested directly with hand-built data (no
Textual, no host I/O); mount-level tests patch the screen's two
host-touching gather functions (`gather_arc`, `gather_upload_rows`)
via mock.patch.object at their call site and assert on RENDERED
CONTENT of the mounted widgets, not merely that the screen exists.
The upload-toggle argv shape is verified the same way
test_tui_mutation.py verifies Lifecycle's: patch
`sigmond.tui.mutation.suspend_and_run_sudo` and inspect the captured
argv.
"""

from __future__ import annotations

import subprocess
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


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class RenderArcPureTests(unittest.TestCase):
    """render_arc() is a pure function of ArcData -- no Textual, no
    host I/O.  These pin down the exact operator-visible text for
    every enrolment state so a regression here fails a *content*
    assertion, not just "did it mount"."""

    def _base(self, **overrides):
        from sigmond.tui.screens.psws import ArcData
        defaults = dict(
            has_profile=True, psws_enabled=True, station_id="S000170",
            station_key_present=True, verified=True,
            verified_at="2026-08-01T00:00:00Z", radiod_registered=True,
            recorders=(),
        )
        defaults.update(overrides)
        return ArcData(**defaults)

    def test_no_site_profile(self):
        from sigmond.tui.screens.psws import render_arc
        body = render_arc(self._base(has_profile=False))
        self.assertIn("No site profile", body)
        self.assertIn("site-profile.toml", body)

    def test_psws_disabled(self):
        from sigmond.tui.screens.psws import render_arc
        body = render_arc(self._base(psws_enabled=False))
        self.assertIn("disabled in site-profile.toml", body)

    def test_enabled_no_station_id(self):
        from sigmond.tui.screens.psws import render_arc
        body = render_arc(self._base(station_id=""))
        self.assertIn("no station id set", body)

    def test_station_key_missing_shows_step_one_next_action(self):
        from sigmond.tui.screens.psws import render_arc
        body = render_arc(self._base(
            station_key_present=False, verified=False, verified_at=""))
        self.assertIn("S000170", body)
        self.assertIn("station key not created yet", body)
        self.assertIn("Enroll", body)
        self.assertNotIn("not yet verified", body)

    def test_key_present_not_verified_shows_step_two_next_action(self):
        from sigmond.tui.screens.psws import render_arc
        body = render_arc(self._base(
            station_key_present=True, verified=False, verified_at=""))
        self.assertIn("station key present, not yet verified", body)
        self.assertIn("Verify", body)
        self.assertNotIn("station key not created yet", body)

    def test_verified_shows_verified_state_with_timestamp(self):
        from sigmond.tui.screens.psws import render_arc
        body = render_arc(self._base(
            verified=True, verified_at="2026-08-01T00:00:00Z"))
        self.assertIn("enrolment verified", body)
        self.assertIn("2026-08-01T00:00:00Z", body)
        self.assertNotIn("not yet verified", body)

    def test_no_radiod_registered_adds_note(self):
        from sigmond.tui.screens.psws import render_arc
        body = render_arc(self._base(radiod_registered=False))
        self.assertIn("no radiod registered", body)
        self.assertIn("SDR Inventory", body)
        self.assertIn("config register-radiod", body)

    def test_radiod_registered_omits_note(self):
        from sigmond.tui.screens.psws import render_arc
        body = render_arc(self._base(radiod_registered=True))
        self.assertNotIn("no radiod registered", body)

    def test_recorder_with_issues_renders_warning_and_fix_hint(self):
        from sigmond.tui.screens.psws import RecorderStatus, render_arc
        rec = RecorderStatus(
            recorder="hf-timestd", installed=True, station="",
            instrument="", key_present=False,
            issues=("station id not set", "instrument/device id not set",
                    "SSH key missing: /home/timestd/.ssh/id_rsa_psws"))
        body = render_arc(self._base(recorders=(rec,)))
        self.assertIn("hf-timestd", body)
        self.assertIn("station id not set", body)
        self.assertIn("smd config hf-timestd edit", body)

    def test_recorder_fully_configured_renders_ok_with_ids(self):
        from sigmond.tui.screens.psws import RecorderStatus, render_arc
        rec = RecorderStatus(
            recorder="mag-recorder", installed=True, station="S000082",
            instrument="84", key_present=True, issues=())
        body = render_arc(self._base(recorders=(rec,)))
        self.assertIn("mag-recorder", body)
        self.assertIn("station=S000082", body)
        self.assertIn("instrument=84", body)

    def test_uninstalled_recorder_is_omitted(self):
        from sigmond.tui.screens.psws import RecorderStatus, render_arc
        rec = RecorderStatus(recorder="mag-recorder", installed=False)
        body = render_arc(self._base(recorders=(rec,)))
        self.assertNotIn("mag-recorder", body)


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class ScreenMountRenderTests(unittest.IsolatedAsyncioTestCase):
    """Mounts the real PswsScreen with gather_arc/gather_upload_rows
    patched (the screen's only host-touching calls) and asserts on the
    rendered widget content -- never touches the real host."""

    async def test_arc_status_widget_shows_rendered_text(self):
        from textual.app import App
        from textual.widgets import Static
        from sigmond.tui.screens.psws import ArcData, PswsScreen

        class Harness(App):
            def compose(self):
                yield PswsScreen()

        data = ArcData(
            has_profile=True, psws_enabled=True, station_id="S000170",
            station_key_present=True, verified=True,
            verified_at="2026-08-01T00:00:00Z", radiod_registered=True,
            recorders=(),
        )
        with mock.patch("sigmond.tui.screens.psws.gather_arc",
                        return_value=data), \
             mock.patch("sigmond.tui.screens.psws.gather_upload_rows",
                        return_value=[]):
            app = Harness()
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.pause()
                widget = app.query_one("#ps-arc-status", Static)
                content = str(widget.render())

        self.assertIn("S000170", content)
        self.assertIn("enrolment verified", content)

    async def test_upload_table_shows_slash_form_and_state(self):
        """Reporter ids must render in operator-facing SLASH form
        (AC0G/S), never the '='-storage form -- misattribution risk
        flagged explicitly in the task brief."""
        from textual.app import App
        from textual.widgets import DataTable
        from sigmond.tui.screens.psws import PswsScreen, UploadRow

        class Harness(App):
            def compose(self):
                yield PswsScreen()

        rows = [
            UploadRow(client="wspr-recorder", reporter_id="AC0G=S",
                      enabled=True, delivery=""),
            UploadRow(client="psk-recorder", reporter_id="AC0G=B1",
                      enabled=False, delivery="direct"),
        ]
        with mock.patch("sigmond.tui.screens.psws.gather_arc",
                        side_effect=AssertionError(
                            "arc gather should be independently mocked")), \
             mock.patch("sigmond.tui.screens.psws.gather_upload_rows",
                        return_value=rows):
            app = Harness()
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.pause()
                table = app.query_one("#ps-upload-table", DataTable)
                row0 = tuple(str(c) for c in table.get_row_at(0))
                row1 = tuple(str(c) for c in table.get_row_at(1))

        self.assertEqual(table.row_count, 2)
        self.assertIn("wspr-recorder", row0)
        self.assertIn("AC0G/S", row0)
        self.assertNotIn("AC0G=S", row0)
        self.assertTrue(any("ON" in c for c in row0))

        self.assertIn("psk-recorder", row1)
        self.assertIn("AC0G/B1", row1)
        self.assertTrue(any("off" in c for c in row1))
        self.assertTrue(any("direct" in c for c in row1))


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class MutationArgvShapeTests(unittest.IsolatedAsyncioTestCase):
    """Verifies the upload-toggle and enroll/verify buttons build the
    exact argv smd expects, matching test_tui_mutation.py's
    accepting-invokes-runner-with-sudo-prefix style: patch
    suspend_and_run_sudo, press Yes, inspect captured argv."""

    async def test_enable_upload_builds_exact_argv(self):
        """The brief's required assertion: pressing Enable on a
        selected instance row builds
        ['smd', 'config', 'upload', <client>, <instance>, '--on']."""
        from sigmond.tui.app import SigmondApp
        from sigmond.tui.screens.psws import ArcData, PswsScreen, UploadRow

        captured_argv = []
        fake_result = subprocess.CompletedProcess(args=[], returncode=0)

        def fake_runner(app_, cmd):
            captured_argv.append(cmd)
            return fake_result

        empty_arc = ArcData(
            has_profile=False, psws_enabled=False, station_id="",
            station_key_present=False, verified=False, verified_at="",
            radiod_registered=False, recorders=(),
        )
        rows = [UploadRow(client="wspr-recorder", reporter_id="AC0G=S",
                          enabled=False, delivery="")]

        app = SigmondApp()
        with mock.patch("sigmond.tui.screens.psws.gather_arc",
                        return_value=empty_arc), \
             mock.patch("sigmond.tui.screens.psws.gather_upload_rows",
                        return_value=rows), \
             mock.patch("sigmond.tui.screens.psws._smd_binary",
                        return_value="smd"):
            async with app.run_test(size=(120, 50)) as pilot:
                app.action_show_psws()
                for _ in range(3):
                    await pilot.pause()

                with mock.patch(
                        "sigmond.tui.mutation.suspend_and_run_sudo",
                        side_effect=fake_runner):
                    app.query_one("#ps-upload-on").press()
                    await pilot.pause()
                    modal = app.screen
                    modal.query_one("#cm-yes").press()
                    await pilot.pause()

        self.assertEqual(len(captured_argv), 1,
                         f"expected one runner call; got {captured_argv}")
        argv = captured_argv[0]
        self.assertEqual(
            argv,
            ['smd', 'config', 'upload', 'wspr-recorder', 'AC0G/S', '--on'],
            f"argv={argv}")

    async def test_disable_upload_builds_exact_argv(self):
        from sigmond.tui.app import SigmondApp
        from sigmond.tui.screens.psws import ArcData, UploadRow

        captured_argv = []
        fake_result = subprocess.CompletedProcess(args=[], returncode=0)

        def fake_runner(app_, cmd):
            captured_argv.append(cmd)
            return fake_result

        empty_arc = ArcData(
            has_profile=False, psws_enabled=False, station_id="",
            station_key_present=False, verified=False, verified_at="",
            radiod_registered=False, recorders=(),
        )
        rows = [UploadRow(client="psk-recorder", reporter_id="AC0G=B1",
                          enabled=True, delivery="direct")]

        app = SigmondApp()
        with mock.patch("sigmond.tui.screens.psws.gather_arc",
                        return_value=empty_arc), \
             mock.patch("sigmond.tui.screens.psws.gather_upload_rows",
                        return_value=rows), \
             mock.patch("sigmond.tui.screens.psws._smd_binary",
                        return_value="smd"):
            async with app.run_test(size=(120, 50)) as pilot:
                app.action_show_psws()
                for _ in range(3):
                    await pilot.pause()

                with mock.patch(
                        "sigmond.tui.mutation.suspend_and_run_sudo",
                        side_effect=fake_runner):
                    app.query_one("#ps-upload-off").press()
                    await pilot.pause()
                    modal = app.screen
                    modal.query_one("#cm-yes").press()
                    await pilot.pause()

        self.assertEqual(len(captured_argv), 1,
                         f"expected one runner call; got {captured_argv}")
        argv = captured_argv[0]
        # --via must never be attached while disabling (apply_enable
        # raises if it is).
        self.assertEqual(
            argv,
            ['smd', 'config', 'upload', 'psk-recorder', 'AC0G/B1', '--off'],
            f"argv={argv}")

    async def test_no_selection_does_not_invoke_runner(self):
        """Pressing Enable with an empty upload table (no rows to
        select) must not call the runner -- guards against a crash or
        a bogus 'smd config upload' with no client/instance."""
        from sigmond.tui.app import SigmondApp
        from sigmond.tui.screens.psws import ArcData

        captured_argv = []

        def fake_runner(app_, cmd):
            captured_argv.append(cmd)
            return subprocess.CompletedProcess(args=[], returncode=0)

        empty_arc = ArcData(
            has_profile=False, psws_enabled=False, station_id="",
            station_key_present=False, verified=False, verified_at="",
            radiod_registered=False, recorders=(),
        )

        app = SigmondApp()
        with mock.patch("sigmond.tui.screens.psws.gather_arc",
                        return_value=empty_arc), \
             mock.patch("sigmond.tui.screens.psws.gather_upload_rows",
                        return_value=[]), \
             mock.patch("sigmond.tui.screens.psws._smd_binary",
                        return_value="smd"):
            async with app.run_test(size=(120, 50)) as pilot:
                app.action_show_psws()
                for _ in range(3):
                    await pilot.pause()

                with mock.patch(
                        "sigmond.tui.mutation.suspend_and_run_sudo",
                        side_effect=fake_runner):
                    app.query_one("#ps-upload-on").press()
                    await pilot.pause()

        self.assertEqual(captured_argv, [])

    async def test_enroll_button_builds_psws_enroll_argv(self):
        from sigmond.tui.app import SigmondApp
        from sigmond.tui.screens.psws import ArcData

        captured_argv = []
        fake_result = subprocess.CompletedProcess(args=[], returncode=0)

        def fake_runner(app_, cmd):
            captured_argv.append(cmd)
            return fake_result

        empty_arc = ArcData(
            has_profile=False, psws_enabled=False, station_id="",
            station_key_present=False, verified=False, verified_at="",
            radiod_registered=False, recorders=(),
        )

        app = SigmondApp()
        with mock.patch("sigmond.tui.screens.psws.gather_arc",
                        return_value=empty_arc), \
             mock.patch("sigmond.tui.screens.psws.gather_upload_rows",
                        return_value=[]), \
             mock.patch("sigmond.tui.screens.psws._smd_binary",
                        return_value="smd"):
            async with app.run_test(size=(120, 50)) as pilot:
                app.action_show_psws()
                for _ in range(3):
                    await pilot.pause()

                with mock.patch(
                        "sigmond.tui.mutation.suspend_and_run_sudo",
                        side_effect=fake_runner):
                    app.query_one("#ps-enroll").press()
                    await pilot.pause()
                    modal = app.screen
                    modal.query_one("#cm-yes").press()
                    await pilot.pause()

        self.assertEqual(captured_argv, [['smd', 'psws', 'enroll']])

    async def test_verify_button_builds_psws_verify_argv(self):
        from sigmond.tui.app import SigmondApp
        from sigmond.tui.screens.psws import ArcData

        captured_argv = []
        fake_result = subprocess.CompletedProcess(args=[], returncode=0)

        def fake_runner(app_, cmd):
            captured_argv.append(cmd)
            return fake_result

        empty_arc = ArcData(
            has_profile=False, psws_enabled=False, station_id="",
            station_key_present=False, verified=False, verified_at="",
            radiod_registered=False, recorders=(),
        )

        app = SigmondApp()
        with mock.patch("sigmond.tui.screens.psws.gather_arc",
                        return_value=empty_arc), \
             mock.patch("sigmond.tui.screens.psws.gather_upload_rows",
                        return_value=[]), \
             mock.patch("sigmond.tui.screens.psws._smd_binary",
                        return_value="smd"):
            async with app.run_test(size=(120, 50)) as pilot:
                app.action_show_psws()
                for _ in range(3):
                    await pilot.pause()

                with mock.patch(
                        "sigmond.tui.mutation.suspend_and_run_sudo",
                        side_effect=fake_runner):
                    app.query_one("#ps-verify").press()
                    await pilot.pause()
                    modal = app.screen
                    modal.query_one("#cm-yes").press()
                    await pilot.pause()

        self.assertEqual(captured_argv, [['smd', 'psws', 'verify']])


if __name__ == '__main__':
    unittest.main()
