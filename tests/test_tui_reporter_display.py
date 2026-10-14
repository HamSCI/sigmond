"""The TUI shows a person AC0G/B4 (tasks/spec-reporter-id-v372.md §4;
inventory rows S26-S28, S30, S32-S34)."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

try:
    import textual  # noqa: F401
    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False

SCREENS = REPO / "lib" / "sigmond" / "tui" / "screens"


def _instances(client):
    from sigmond.instance import Instance, instance_paths
    return lambda catalog_clients=None: [Instance(
        paths=instance_paths(client, "AC0G=B4"),
        has_config=True, has_env=True, has_sources=True)]


class FallbackCopiesTests(unittest.TestCase):
    """S32: no screen keeps its own copy of the conversion rules."""

    def test_no_import_fallback_left(self):
        for name in ("activity.py", "logs.py", "verifier.py", "psws.py",
                     "configuration.py"):
            with self.subTest(screen=name):
                self.assertNotIn("except ImportError", (SCREENS / name).read_text())


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class DropdownTests(unittest.TestCase):
    """S26-S28, S30: a dropdown shows AC0G/B4 and passes AC0G=B4 on."""

    def _patched(self, client):
        return (mock.patch("sigmond.instance.list_instances", _instances(client)),
                mock.patch("sigmond.instance.detect_migration_candidates", lambda: []))

    def test_activity(self):
        from sigmond.tui.screens import activity
        a, b = self._patched("psk-recorder")
        with a, b, mock.patch.dict(activity._TARGET_TO_CLIENT, {"psk": "psk-recorder"}):
            opts = activity.ActivityScreen._instance_options_for("psk")
        self.assertIn(("AC0G/B4", "AC0G=B4"), opts)

    def test_logs(self):
        from sigmond.tui.screens import logs
        a, b = self._patched("psk-recorder")
        with a, b:
            opts = logs._instance_options_for_component("psk-recorder")
        self.assertIn(("AC0G/B4", "AC0G=B4"), opts)

    def test_verifier(self):
        from sigmond.tui.screens import verifier
        a, b = self._patched("wspr-recorder")
        with a, b, mock.patch.dict(verifier._TARGET_TO_CLIENT, {"wspr": "wspr-recorder"}):
            opts = verifier.VerifierScreen._instance_options_for("wspr")
        self.assertIn(("AC0G/B4", "AC0G=B4"), opts)


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class LegacyDropdownTests(unittest.TestCase):
    """Final review 6: a double-escaped copy reads AC0G/B4, never AC0G\\x3dB4;
    the value the dropdown passes on stays the candidate's own name."""

    WANT = [("AC0G/B4 (double-escaped copy)", "AC0G\\x3dB4"),
            ("my-rx888 (legacy)", "my-rx888")]

    def _patched(self, client):
        from sigmond.instance import MigrationCandidate
        cands = [MigrationCandidate(client, "AC0G\\x3dB4", True, True, False),
                 MigrationCandidate(client, "my-rx888", True, True, False)]
        return (mock.patch("sigmond.instance.list_instances",
                           lambda catalog_clients=None: []),
                mock.patch("sigmond.instance.detect_migration_candidates",
                           lambda: cands))

    def test_activity(self):
        from sigmond.tui.screens import activity
        a, b = self._patched("psk-recorder")
        with a, b, mock.patch.dict(activity._TARGET_TO_CLIENT, {"psk": "psk-recorder"}):
            opts = activity.ActivityScreen._instance_options_for("psk")
        self.assertEqual(opts[1:], self.WANT)

    def test_logs(self):
        from sigmond.tui.screens import logs
        a, b = self._patched("psk-recorder")
        with a, b:
            opts = logs._instance_options_for_component("psk-recorder")
        self.assertEqual(opts[1:], self.WANT)

    def test_verifier(self):
        from sigmond.tui.screens import verifier
        a, b = self._patched("wspr-recorder")
        with a, b, mock.patch.dict(verifier._TARGET_TO_CLIENT, {"wspr": "wspr-recorder"}):
            opts = verifier.VerifierScreen._instance_options_for("wspr")
        self.assertEqual(opts[1:], self.WANT)


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class ConfigurationScreenTests(unittest.TestCase):
    """S34: the confirmation text and the command it runs."""

    def _capture(self):
        seen = {}

        def confirm_and_run(app, *, title, body, cmd, sudo, on_complete):
            seen.update(title=title, body=body, cmd=cmd)
        return seen, mock.patch("sigmond.tui.mutation.confirm_and_run", confirm_and_run)

    def test_remove(self):
        from sigmond.tui.screens.configuration import ConfigurationScreen
        seen, patch = self._capture()
        fake = SimpleNamespace(_selected=lambda: ("psk-recorder", "AC0G=B4"),
                               app=None, _after_mutation=None,
                               _set_last=lambda msg: None)
        with patch:
            ConfigurationScreen._do_remove(fake)
        self.assertEqual(seen["title"], "Remove psk-recorder@AC0G/B4?")
        self.assertIn("smd admin instance remove psk-recorder AC0G/B4 --yes", seen["body"])
        self.assertEqual(seen["cmd"][-3:], ["psk-recorder", "AC0G/B4", "--yes"])

    def test_add(self):
        from sigmond.tui.screens.configuration import ConfigurationScreen
        seen, patch = self._capture()
        values = {"#cf-add-client": "psk-recorder", "#cf-add-reporter": "ac0g/b4"}
        fake = SimpleNamespace(
            query_one=lambda sel, cls=None: SimpleNamespace(value=values[sel]),
            app=None, _after_mutation=None, _set_last=lambda msg: None)
        with patch:
            ConfigurationScreen._do_add(fake, dry_run=False)
        self.assertEqual(seen["title"], "Add instance psk-recorder@AC0G/B4?")
        self.assertIn("smd admin instance add psk-recorder AC0G/B4", seen["body"])
        self.assertEqual(seen["cmd"][-2:], ["psk-recorder", "AC0G/B4"])

    def test_unit_state_is_asked_for_by_the_systemd_name(self):
        # S33
        src = (SCREENS / "configuration.py").read_text()
        self.assertNotIn('f"{i.client}@{i.reporter_id}.service"', src)


if __name__ == "__main__":
    unittest.main()
