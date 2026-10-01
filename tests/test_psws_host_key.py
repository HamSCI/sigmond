"""One PSWS upload key per UPLOADING MACHINE, reported where uploads use it.

mjh, 2026-10-01: "for security it should remain unique per uploading
machine."  PSWS accepts whatever key an authorized PSWS user registers for a
station, so that user registers this machine's one public key on every
station it uploads for.  A compromise exposes only the stations this machine
serves; revoking it touches only them.

That key is hs-uploader's host key, /etc/hs-uploader/keys/id_ed25519_host:
uploader_manifest.host_key_file() puts it on EVERY pipeline (GRAPE, the
magnetometer, wsprdaemon).  Until now psws.py reported on a different one --
a per-recorder key field, else a "shared" /etc/sigmond/psws/id_ed25519 that
no upload ever used -- so a host could read "PSWS configured" while
hs-uploader held no key, or "SSH key missing" while uploads worked.  On a
fresh install from hf-timestd d1af1dd (no [uploader] section) it would have
read "missing" for hf-timestd every time.

Supersedes test_psws_shared_key.py (2026-09-03): the one-key idea stands; the
key it named was the wrong file.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import psws, upload_creds, uploader_manifest       # noqa: E402


def _with_config(recorder, text):
    """Point a recorder's spec at a temp config holding `text`."""
    td = tempfile.TemporaryDirectory()
    cfg = Path(td.name) / "config.toml"
    cfg.write_text(text)
    spec = psws.RECORDERS[recorder]
    real = spec["config"]
    spec["config"] = cfg
    return td, (lambda: spec.__setitem__("config", real))


class OneHostKeyTest(unittest.TestCase):

    def setUp(self):
        self.keys = tempfile.TemporaryDirectory()
        self.host_key = Path(self.keys.name) / "id_ed25519_host"
        p = mock.patch.object(uploader_manifest, "KEYS_DIR", Path(self.keys.name))
        p.start(); self.addCleanup(p.stop); self.addCleanup(self.keys.cleanup)

    def test_every_recorder_reports_the_key_uploads_use(self):
        self.host_key.write_text("k")
        for rec in psws.RECORDERS:
            td, restore = _with_config(rec, "[station]\n")
            try:
                st = psws.read_state(rec)
            finally:
                restore(); td.cleanup()
            self.assertEqual(st.key_path, uploader_manifest.host_key_file(), rec)
            self.assertTrue(st.key_present, rec)

    def test_a_stale_per_recorder_key_field_is_not_consulted(self):
        # B4's v3.65 config named /home/timestd/.ssh/id_rsa_psws_S000170, a
        # file nothing creates; uploads used the host key regardless.
        self.host_key.write_text("k")
        td, restore = _with_config("hf-timestd",
            '[station]\nid = "S000170"\ninstrument_id = "171"\n'
            '[uploader.sftp]\nssh_key = "/nonexistent/id_rsa_psws_S000170"\n')
        try:
            st = psws.read_state("hf-timestd")
        finally:
            restore(); td.cleanup()
        self.assertTrue(st.configured, st.issues)
        self.assertEqual(st.key_path, uploader_manifest.host_key_file())

    def test_a_missing_host_key_names_it_and_the_command_that_makes_it(self):
        td, restore = _with_config("hf-timestd",
            '[station]\nid = "S000170"\ninstrument_id = "171"\n')
        try:
            st = psws.read_state("hf-timestd")
        finally:
            restore(); td.cleanup()
        self.assertFalse(st.configured)
        issue = " ".join(st.issues)
        self.assertIn(uploader_manifest.host_key_file(), issue)
        self.assertIn("smd psws enroll", issue)

    def test_no_per_recorder_key_and_no_shared_key_remain(self):
        for rec, spec in psws.RECORDERS.items():
            for field in ("ssh_key", "default_key", "key_type"):
                self.assertNotIn(field, spec, f"{rec} still carries {field}")
        for gone in ("SHARED_KEY", "SHARED_KEY_GROUP", "_gen_key",
                     "ensure_key_readable"):
            self.assertFalse(hasattr(psws, gone), f"psws.{gone} still exists")

    def test_validate_probes_as_the_station_with_the_host_key(self):
        self.host_key.write_text("k")
        td, restore = _with_config("mag-recorder",
            '[station]\npsws_station_id = "S000082"\ninstrument_id = "84"\n')
        try:
            with mock.patch.object(psws, "sftp_station_login_ok",
                                   return_value=(True, "ok")) as probe:
                ok, _ = psws.sftp_login_ok("mag-recorder")
        finally:
            restore(); td.cleanup()
        self.assertTrue(ok)
        probe.assert_called_once_with("S000082", uploader_manifest.host_key_file())


class UploadCredsUsesTheHostKeyTest(unittest.TestCase):

    def _paths(self, cfg_text, key_present):
        td = tempfile.TemporaryDirectory()
        cfg = Path(td.name) / "timestd-config.toml"
        cfg.write_text(cfg_text)
        key = Path(td.name) / "id_ed25519_host"
        if key_present:
            key.write_text("k")
        patches = [
            mock.patch.object(upload_creds, "HF_TIMESTD_CONFIG", cfg),
            mock.patch.object(upload_creds, "MAG_CONFIG", Path(td.name) / "absent"),
            mock.patch.object(upload_creds, "WSPR_ETC", Path(td.name) / "absent"),
            mock.patch.object(upload_creds, "PSK_ETC", Path(td.name) / "absent"),
            mock.patch.object(upload_creds, "HS_UPLOADER_KEY_HOST", key),
            mock.patch.object(upload_creds, "HS_UPLOADER_KEY", Path(td.name) / "legacy"),
            mock.patch.object(upload_creds, "HS_UPLOADER_MANIFEST", Path(td.name) / "absent"),
        ]
        for p in patches:
            p.start()
        try:
            return [p for p in upload_creds.upload_paths_status() if p.recorder == "hf-timestd"]
        finally:
            for p in patches:
                p.stop()
            td.cleanup()

    IDS = '[station]\nid = "S000170"\ninstrument_id = "171"\n'

    def test_ready_with_ids_and_the_host_key(self):
        (p,) = self._paths(self.IDS, key_present=True)
        self.assertTrue(p.ready, p.missing)

    def test_not_ready_without_the_host_key(self):
        (p,) = self._paths(self.IDS, key_present=False)
        self.assertFalse(p.ready)
        self.assertIn("id_ed25519_host", p.missing)
        self.assertIn("smd psws enroll", p.fix)
        self.assertNotIn("setup-psws-keys", p.fix)

    def test_a_dead_uploader_sftp_key_does_not_block_readiness(self):
        cfg = self.IDS + '[uploader.sftp]\nssh_key = "/nonexistent/id_rsa_psws"\n'
        (p,) = self._paths(cfg, key_present=True)
        self.assertTrue(p.ready, p.missing)


if __name__ == "__main__":
    unittest.main()
