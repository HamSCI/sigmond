"""Tests for sigmond.upload — the per-instance upload-enable helper that
backs `smd config upload` and rule_upload_enabled."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond import upload


class TestUploadEnable(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())

    def _env(self, client, inst):
        return self.base / client / "env" / f"{inst}.env"

    def test_storage_form_slash_to_equals(self):
        base = str(self.base)
        for typed in ("AC0G/S", "ac0g/s", "AC0G=S"):
            with self.subTest(typed=typed):
                self.assertEqual(upload.env_path_for("x", typed, base=base).name, "AC0G=S.env")

    def test_escaped_instance_finds_the_storage_env_file(self):
        # S19: systemd's spelling must reach AC0G=B4.env, not AC0G\x3dB4.env.
        env = self._env("psk-recorder", "AC0G=B4")
        env.parent.mkdir(parents=True)
        env.write_text("X=1\n")
        self.assertEqual(
            upload.env_path_for("psk-recorder", "AC0G\\x3dB4", base=str(self.base)), env)

    def test_escaped_instance_writes_no_shadow_file(self):
        path, *_ = upload.apply_enable(
            "wspr-recorder", "AC0G\\x3dB4", True, base=str(self.base))
        self.assertEqual(path.name, "AC0G=B4.env")
        self.assertEqual([p.name for p in path.parent.iterdir()], ["AC0G=B4.env"])

    def test_lower_case_reaches_the_upper_case_file(self):
        # Review Focus 1: reporter ids are upper case only.
        env = self._env("psk-recorder", "W1ABC-5")
        env.parent.mkdir(parents=True)
        env.write_text("")
        self.assertEqual(
            upload.env_path_for("psk-recorder", "w1abc-5", base=str(self.base)), env)

    def _legacy(self, name, typed):
        env = self._env("psk-recorder", name)
        env.parent.mkdir(parents=True)
        env.write_text("")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = upload.env_path_for("psk-recorder", typed, base=str(self.base))
        return env, got, err.getvalue()

    def test_a_legacy_file_named_exactly_as_typed_is_used_with_a_hint(self):
        env, got, err = self._legacy("my-rx888", "my-rx888")
        self.assertEqual(got, env)
        self.assertIn("legacy instance name my-rx888; run: smd admin instance migrate", err)

    def test_a_legacy_name_that_is_no_reporter_id_is_used_with_a_hint(self):
        env, got, err = self._legacy("bee1-status.local", "bee1-status.local")
        self.assertEqual(got, env)
        self.assertIn("legacy instance name bee1-status.local", err)

    def test_without_a_legacy_file_the_storage_form_wins(self):
        self.assertEqual(
            upload.env_path_for("psk-recorder", "my-rx888", base=str(self.base)).name,
            "MY-RX888.env")
        with self.assertRaises(ValueError):
            upload.env_path_for("psk-recorder", "bee1-status.local", base=str(self.base))

    def test_apply_enable_creates_and_sets_flag(self):
        path, flag, dests, delivery = upload.apply_enable(
            "wspr-recorder", "AC0G/S", True, base=str(self.base))
        self.assertEqual(flag, "WSPR_USE_HS_UPLOADER")
        self.assertEqual(path, self._env("wspr-recorder", "AC0G=S"))
        self.assertIn("WSPR_USE_HS_UPLOADER=1", path.read_text())
        self.assertIn("wsprdaemon.org", dests)
        self.assertIsNone(delivery)  # wspr has no delivery-pipeline knob

    def test_psk_enable_sets_direct_pipeline(self):
        # Enabling psk must ALSO set the direct pipeline — the boolean alone
        # leaves the default server-merge path, which delivers nothing on a
        # standalone node.
        path, _, _, delivery = upload.apply_enable(
            "psk-recorder", "AC0G/S", True, base=str(self.base))
        body = path.read_text()
        self.assertIn("PSK_USE_HS_UPLOADER=1", body)
        self.assertIn("PSK_DELIVERY_PIPELINES=direct", body)
        self.assertEqual(delivery, ("PSK_DELIVERY_PIPELINES", "direct"))

    def test_psk_enable_via_server_merge(self):
        path, _, _, delivery = upload.apply_enable(
            "psk-recorder", "AC0G/S", True, base=str(self.base),
            delivery="server-merge")
        self.assertIn("PSK_DELIVERY_PIPELINES=server-merge", path.read_text())
        self.assertEqual(delivery, ("PSK_DELIVERY_PIPELINES", "server-merge"))

    def test_psk_invalid_via_raises(self):
        with self.assertRaises(ValueError):
            upload.apply_enable("psk-recorder", "AC0G/S", True,
                                base=str(self.base), delivery="bogus")

    def test_via_on_client_without_knob_raises(self):
        with self.assertRaises(ValueError):
            upload.apply_enable("wspr-recorder", "AC0G/S", True,
                                base=str(self.base), delivery="direct")

    def test_disable_leaves_pipeline_untouched(self):
        upload.apply_enable("psk-recorder", "AC0G/S", True, base=str(self.base))
        upload.apply_enable("psk-recorder", "AC0G/S", False, base=str(self.base))
        body = self._env("psk-recorder", "AC0G=S").read_text()
        self.assertIn("PSK_USE_HS_UPLOADER=0", body)
        self.assertIn("PSK_DELIVERY_PIPELINES=direct", body)  # not reverted

    def test_apply_preserves_other_keys(self):
        env = self._env("wspr-recorder", "AC0G=S")
        env.parent.mkdir(parents=True)
        env.write_text("# header\nWD_DECODE_VIA_DB=1\n")
        upload.apply_enable("wspr-recorder", "AC0G/S", True, base=str(self.base))
        body = env.read_text()
        self.assertIn("WD_DECODE_VIA_DB=1", body)        # untouched
        self.assertIn("# header", body)                  # comment preserved
        self.assertIn("WSPR_USE_HS_UPLOADER=1", body)

    def test_off_sets_zero_and_updates_in_place(self):
        upload.apply_enable("psk-recorder", "AC0G/S", True, base=str(self.base))
        upload.apply_enable("psk-recorder", "AC0G/S", False, base=str(self.base))
        body = self._env("psk-recorder", "AC0G=S").read_text()
        self.assertIn("PSK_USE_HS_UPLOADER=0", body)
        self.assertEqual(body.count("PSK_USE_HS_UPLOADER"), 1)  # updated, not appended

    def test_unknown_client_raises(self):
        with self.assertRaises(KeyError):
            upload.apply_enable("hfdl-recorder", "x", True, base=str(self.base))

    def test_edits_existing_noncanonical_spelling(self):
        # If a file already exists under the raw reporter id, edit it in place
        # rather than shadowing it with a new canonical file.
        env = self._env("wspr-recorder", "AC0G=S")
        env.parent.mkdir(parents=True)
        env.write_text("WSPR_USE_HS_UPLOADER=0\n")
        path, _, _, _ = upload.apply_enable(
            "wspr-recorder", "AC0G/S", True, base=str(self.base))
        self.assertEqual(path, env)
        self.assertIn("WSPR_USE_HS_UPLOADER=1", env.read_text())


if __name__ == "__main__":
    unittest.main()


class TestConfigUploadCommand(unittest.TestCase):
    """The `smd config upload` wrapper (arg handling + delegation)."""

    def _args(self, **kw):
        import argparse
        ns = argparse.Namespace(client=None, instance=None, on=False, off=False)
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_unknown_client_returns_1(self):
        from sigmond.commands.config import cmd_config_upload
        self.assertEqual(
            cmd_config_upload(self._args(client="hfdl-recorder")), 1)

    def test_change_without_instance_returns_1(self):
        from sigmond.commands.config import cmd_config_upload
        self.assertEqual(
            cmd_config_upload(self._args(client="wspr-recorder", on=True)), 1)

    def test_enable_delegates_to_apply(self):
        from sigmond.commands import config as C
        from sigmond import upload
        calls = []
        orig = upload.apply_enable
        upload.apply_enable = (lambda c, i, on, base="/etc", delivery=None:
                               (calls.append((c, i, on))
                                or (Path("/x.env"), "WSPR_USE_HS_UPLOADER",
                                    ["wsprnet.org"], None)))
        try:
            rc = C.cmd_config_upload(
                self._args(client="wspr-recorder", instance="AC0G/S", on=True))
        finally:
            upload.apply_enable = orig
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [("wspr-recorder", "AC0G/S", True)])
