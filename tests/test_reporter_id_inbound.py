"""What systemd hands back carries its own escapes (AC0G\\x3dB4).  Every
reader turns it into the storage form (AC0G=B4) before it validates,
compares or builds a path (tasks/spec-reporter-id-v372.md §2, boundary 2;
inventory rows S5-S7, S13, S15, S16, S44)."""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import harmonize as H  # noqa: E402
from sigmond import instance as I  # noqa: E402


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_reporter_inbound", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def _completed(stdout):
    """A subprocess.run stand-in that answers every call with `stdout`."""
    return lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout, "")


def _rooted(tmp):
    """A stand-in for pathlib.Path that moves /etc/... and /opt/... under tmp."""
    real = Path

    def P(*parts):
        p = real(*parts)
        s = str(p)
        if s in ("/etc", "/opt") or s.startswith(("/etc/", "/opt/")):
            return real(tmp + s)
        return p
    return P


class ListUnitsFallbackTests(unittest.TestCase):
    """S5: without read access to /etc/<client>/, the instance list comes
    from systemctl, which spells the instance AC0G\\x3dB4."""

    def test_an_escaped_unit_still_names_its_instance(self):
        out = "psk-recorder@AC0G\\x3dB4.service loaded active running PSK\n"
        with mock.patch("subprocess.run", _completed(out)):
            self.assertEqual(I._list_units_for_client("psk-recorder"), ["AC0G=B4"])


class MigrationCandidateTests(unittest.TestCase):
    """S6, S7: `smd admin instance migrate --yes` stops and disables every
    candidate, so the live reporter-keyed unit must never be one."""

    def _detect(self, units_out, env_names=()):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "etc" / "wspr-recorder" / "env"
            env.mkdir(parents=True)
            for name in env_names:
                (env / f"{name}.env").write_text("")
            with mock.patch("subprocess.run", _completed(units_out)), \
                 mock.patch.object(I, "Path", _rooted(tmp)):
                return [(c.client, c.old_instance)
                        for c in I.detect_migration_candidates()]

    def test_the_live_escaped_unit_is_not_legacy(self):
        out = "wspr-recorder@AC0G\\x3dB4.service loaded active running WSPR\n"
        self.assertEqual(self._detect(out), [])

    def test_a_real_legacy_unit_is_still_found(self):
        out = "wspr-recorder@my-rx888.service loaded inactive dead WSPR\n"
        self.assertEqual(self._detect(out), [("wspr-recorder", "my-rx888")])

    def test_an_env_file_in_systemd_spelling_is_not_legacy(self):
        self.assertEqual(self._detect("", env_names=["AC0G\\x3dB4"]), [])


class HarmonizeInboundTests(unittest.TestCase):
    """S13, S15, S16: harmonize reads unit names and finds env files."""

    def test_active_instances_come_back_in_storage_form(self):
        out = "wspr-recorder@AC0G\\x3dB4.service loaded active running WSPR\n"
        with mock.patch("subprocess.run", _completed(out)):
            self.assertEqual(H._active_instances("wspr-recorder"), ["AC0G=B4"])

    def test_resolve_instance_env_reads_the_storage_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "etc" / "wspr-recorder" / "env"
            env.mkdir(parents=True)
            (env / "AC0G=B4.env").write_text("WD_DECODE_VIA_DB=1\n")
            with mock.patch.object(H, "Path", _rooted(tmp)):
                for inst in ("AC0G\\x3dB4", "AC0G=B4"):
                    with self.subTest(inst=inst):
                        self.assertEqual(H._resolve_instance_env("wspr-recorder", inst),
                                         {"WD_DECODE_VIA_DB": "1"})

    def test_decode_rule_finds_the_env_through_the_escaped_unit(self):
        # Passed before this change too: it guards the rewrite of _decode_on.
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "etc" / "wspr-recorder" / "env"
            env.mkdir(parents=True)
            (env / "AC0G=B4.env").write_text("WD_DECODE_VIA_DB=1\n")
            out = "wspr-recorder@AC0G\\x3dB4.service loaded active running WSPR\n"
            with mock.patch.object(H, "Path", _rooted(tmp)), \
                 mock.patch("subprocess.run", _completed(out)):
                self.assertEqual(H.rule_wspr_decode_enabled(None).severity, "pass")

    def test_no_private_unescape_is_left(self):
        src = (REPO / "lib" / "sigmond" / "harmonize.py").read_text()
        self.assertFalse("unicode_escape" in src,
                         "harmonize.py still decodes unit names with unicode_escape")


class WatchConfigLookupTests(unittest.TestCase):
    """S44: hfdl-watch and codar-watch take the instance from a unit name."""

    def test_config_lookup_unescapes_the_unit_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "etc" / "hfdl-recorder" / "AC0G=B4.toml"
            cfg.parent.mkdir(parents=True)
            cfg.write_text('[[radiod]]\nstatus = "b4-status.local"\n')
            with mock.patch.object(smd, "Path", _rooted(tmp)):
                self.assertEqual(
                    smd._radiod_status_from_per_instance_config(
                        "hfdl-recorder", "AC0G\\x3dB4"),
                    "b4-status.local")


if __name__ == "__main__":
    unittest.main()
