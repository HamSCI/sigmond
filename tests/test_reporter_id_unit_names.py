"""Unit names leave sigmond in systemd's spelling (AC0G\\x3dB4), built by
the helpers in instance.py; the screens show them in the display form
(tasks/spec-reporter-id-v372.md §2, boundary 2; inventory rows S4, S8, S12,
S24, S29)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import instance as I  # noqa: E402
from sigmond import lifecycle  # noqa: E402

try:
    import textual  # noqa: F401
    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False


def _rooted(tmp):
    real = Path

    def P(*parts):
        p = real(*parts)
        s = str(p)
        if s in ("/etc", "/opt") or s.startswith(("/etc/", "/opt/")):
            return real(tmp + s)
        return p
    return P


class InstancePathsTests(unittest.TestCase):
    """S4: the one unit name sigmond builds for an instance."""

    def test_unit_name_is_in_systemd_spelling_and_files_keep_storage(self):
        p = I.instance_paths("psk-recorder", "AC0G=B4")
        self.assertEqual(p.unit_name, "psk-recorder@AC0G\\x3dB4.service")
        self.assertEqual(p.config, Path("/etc/psk-recorder/AC0G=B4.toml"))
        self.assertEqual(p.env, Path("/etc/psk-recorder/env/AC0G=B4.env"))


class MigrateUnitNamesTests(unittest.TestCase):
    """S8: migrate addresses both units by the names systemd gave them."""

    def test_dry_run_names_both_units_correctly(self):
        steps = I.migrate_one_instance("wspr-recorder", "my-rx888", "AC0G=B4",
                                       dry_run=True)
        self.assertIn("would systemctl stop wspr-recorder@my-rx888.service", steps)
        self.assertIn("would systemctl enable --now wspr-recorder@AC0G\\x3dB4.service",
                      steps)


class ExpandTemplateTests(unittest.TestCase):
    """S12: lifecycle's UnitRef.unit is what smd start/stop/restart pass on."""

    def _expand(self, listed, env_names=(), toml_names=(), conf_names=()):
        units_json = json.dumps([{"unit": u} for u in listed])

        def run(argv, **kw):
            out = units_json if argv[1] == "list-units" else ""
            return subprocess.CompletedProcess(argv, 0, out, "")
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "etc" / "psk-recorder" / "env"
            env.mkdir(parents=True)
            for name in env_names:
                (env / f"{name}.env").write_text("")
            for name in toml_names:
                (env.parent / f"{name}.toml").write_text("")
            conf = Path(tmp) / "etc" / "radio"
            conf.mkdir(parents=True)
            for name in conf_names:
                (conf / f"psk-recorder@{name}.conf").write_text("")
            with mock.patch.object(lifecycle, "Path", _rooted(tmp)), \
                 mock.patch.object(lifecycle.subprocess, "run", run):
                return lifecycle._expand_template(
                    "psk-recorder", "psk-recorder@.service", "test", "service",
                    conf_dir="/etc/radio")

    def test_unit_carries_systemd_spelling_instance_carries_storage(self):
        refs = self._expand(["psk-recorder@AC0G\\x3dB4.service",
                             "psk-recorder@bee1-status.local.service"])
        self.assertEqual(
            sorted((r.unit, r.instance) for r in refs),
            [("psk-recorder@AC0G\\x3dB4.service", "AC0G=B4"),
             ("psk-recorder@bee1-status.local.service", "bee1-status.local")])

    def test_a_double_escaped_orphan_keeps_its_own_unit_name(self):
        # Review Focus 2: an orphan left by an older double-escape bug must
        # never resolve to the live unit, or stopping the orphan stops it.
        refs = self._expand(["psk-recorder@AC0G\\x3dB4.service",
                             "psk-recorder@AC0G\\x5cx3dB4.service"],
                            env_names=["AC0G=B4"])
        got = {r.unit: r.orphaned for r in refs}
        self.assertEqual(got, {"psk-recorder@AC0G\\x3dB4.service": False,
                               "psk-recorder@AC0G\\x5cx3dB4.service": True})

    def _assert_one_live_unit(self, refs):
        live = [(r.unit, r.instance) for r in refs if not r.orphaned]
        self.assertEqual(live, [("psk-recorder@AC0G\\x3dB4.service", "AC0G=B4")])

    def test_a_shadow_env_file_does_not_mint_a_second_daemon(self):
        # Final review 1: a stray env/AC0G\x3dB4.env (literal backslash) once
        # became its own instance, escaped AGAIN to AC0G\x5cx3dB4, and smd
        # start launched a second daemon for the same reporter.
        refs = self._expand(["psk-recorder@AC0G\\x3dB4.service"],
                            env_names=["AC0G=B4", "AC0G\\x3dB4"])
        self._assert_one_live_unit(refs)

    def test_a_shadow_toml_file_does_not_mint_a_second_daemon(self):
        refs = self._expand(["psk-recorder@AC0G\\x3dB4.service"],
                            toml_names=["AC0G=B4", "AC0G\\x3dB4"])
        self._assert_one_live_unit(refs)

    def test_a_shadow_conf_file_does_not_mint_a_second_daemon(self):
        refs = self._expand(["psk-recorder@AC0G\\x3dB4.service"],
                            conf_names=["AC0G=B4", "AC0G\\x3dB4"])
        self._assert_one_live_unit(refs)


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class TuiUnitNameTests(unittest.TestCase):
    """S24, S29: the Lifecycle and Logs screens."""

    def test_lifecycle_row_shows_the_display_form(self):
        from sigmond.lifecycle import UnitRef
        from sigmond.tui.screens import lifecycle as screen
        ref = UnitRef(component="psk-recorder",
                      unit="psk-recorder@AC0G\\x3dB4.service",
                      template="psk-recorder@.service", instance="AC0G=B4",
                      kind="service", source="test")
        topo = SimpleNamespace(enabled_components=lambda: ["psk-recorder"])
        with mock.patch("sigmond.topology.load_topology", lambda *a, **k: topo), \
             mock.patch("sigmond.catalog.load_catalog", lambda *a, **k: {}), \
             mock.patch("sigmond.lifecycle.resolve_units", lambda *a, **k: [ref]), \
             mock.patch.object(screen, "_systemctl_show_batch",
                               lambda units, props: {u: {"ActiveState": "active"}
                                                     for u in units}):
            data = screen._gather()
        self.assertIsNone(data.error)
        self.assertEqual([r.display for r in data.rows], ["psk-recorder@AC0G/B4"])
        self.assertEqual([r.unit for r in data.rows], ["psk-recorder@AC0G\\x3dB4.service"])

    def test_logs_instance_unit_is_spelled_for_systemd(self):
        from sigmond.tui.screens import logs
        self.assertEqual(logs._instance_unit("psk-recorder", "AC0G=B4"),
                         "psk-recorder@AC0G\\x3dB4.service")
        self.assertEqual(logs._instance_unit("psk-recorder", "my-rx888"),
                         "psk-recorder@my-rx888.service")


if __name__ == "__main__":
    unittest.main()
