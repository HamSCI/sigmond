"""Instance keys (rob, 2026-11-11): unit and per-instance file names read
like radiod's -- psk-recorder@AI6VN-1 -- and an old station named the '='
way is moved by instance_rekey.  The true id stays in each config's
[instance] reporter_id, which is what list_instances() reports."""
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import instance as I            # noqa: E402
from sigmond import instance_rekey as R      # noqa: E402


class KeyTests(unittest.TestCase):
    def test_every_spelling_reaches_one_key(self):
        for text in ("AI6VN/1", "AI6VN=1", "AI6VN\\x3d1", "ai6vn/1", "AI6VN-1"):
            with self.subTest(text=text):
                self.assertEqual(I.instance_key(text), "AI6VN-1")

    def test_real_hyphens_stay(self):
        self.assertEqual(I.instance_key("KP4MD-RPI-4"), "KP4MD-RPI-4")

    def test_unit_and_files_use_the_key_identity_stays(self):
        p = I.instance_paths("psk-recorder", "AI6VN=1")
        self.assertEqual(p.reporter_id, "AI6VN=1")
        self.assertEqual(p.unit_name, "psk-recorder@AI6VN-1.service")
        self.assertEqual(p.config.name, "AI6VN-1.toml")
        self.assertEqual(p.sources.name, "psk-recorder@AI6VN-1.sources.toml")
        self.assertEqual(str(p.state_dir), "/var/lib/psk-recorder/AI6VN-1")

    def test_unit_instance_keeps_legacy_names(self):
        self.assertEqual(I.unit_instance("AI6VN=1"), "AI6VN-1")
        self.assertEqual(I.unit_instance("my-rx888"), "my-rx888")

    def test_junk_is_refused(self):
        with self.assertRaises(I.InvalidReporterId):
            I.instance_key("AI6VN/")


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "psk-recorder").mkdir()

    def test_identity_comes_from_the_config(self):
        (self.tmp / "psk-recorder/AI6VN-1.toml").write_text(
            '[instance]\nreporter_id = "AI6VN/1"\n')
        self.assertEqual(I.identity_for_key("psk-recorder", "AI6VN-1",
                                            etc_root=self.tmp), "AI6VN=1")

    def test_a_real_hyphen_id_stays_a_hyphen(self):
        (self.tmp / "psk-recorder/W1ABC-5.toml").write_text(
            '[instance]\nreporter_id = "W1ABC-5"\n')
        self.assertEqual(I.identity_for_key("psk-recorder", "W1ABC-5",
                                            etc_root=self.tmp), "W1ABC-5")

    def test_old_names_decode_without_a_config(self):
        self.assertEqual(I.identity_for_key("psk-recorder", "AI6VN\\x3d1",
                                            etc_root=self.tmp), "AI6VN=1")

    def test_list_instances_reports_the_true_id(self):
        etc = self.tmp
        (etc / "psk-recorder/AI6VN-1.toml").write_text(
            '[instance]\nreporter_id = "AI6VN/1"\n')
        real = Path

        def P(*parts):
            p = real(*parts)
            s = str(p)
            return real(str(etc) + s[len("/etc"):]) if s == "/etc" or s.startswith("/etc/") else p
        with mock.patch.object(I, "Path", P):
            got = I.list_instances(["psk-recorder"])
        self.assertEqual([(i.client, i.reporter_id) for i in got],
                         [("psk-recorder", "AI6VN=1")])
        self.assertEqual(I.display_reporter_id(got[0].reporter_id), "AI6VN/1")


class CollisionTests(unittest.TestCase):
    def test_two_ids_one_key_is_refused_even_with_force(self):
        with tempfile.TemporaryDirectory() as t:
            etc = Path(t)
            (etc / "psk-recorder").mkdir()
            (etc / "psk-recorder/AC0G-B4.toml").write_text(
                '[instance]\nreporter_id = "AC0G/B4"\n')
            with self.assertRaises(I.InvalidReporterId) as cm:
                I.create_instance("psk-recorder", "AC0G-B4", force=True,
                                  etc_root=etc)
            self.assertIn("would both be psk-recorder@AC0G-B4", str(cm.exception))

    def test_the_same_id_again_is_not_a_collision(self):
        with tempfile.TemporaryDirectory() as t:
            etc = Path(t)
            (etc / "psk-recorder").mkdir()
            (etc / "psk-recorder/AC0G-B4.toml").write_text(
                '[instance]\nreporter_id = "AC0G/B4"\n')
            I.create_instance("psk-recorder", "AC0G=B4", force=True,
                              dry_run=True, etc_root=etc)


class _Systemctl:
    def __init__(self, enabled=(), active=()):
        self.enabled, self.active, self.calls = set(enabled), set(active), []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv[1:]))
        out = ""
        if argv[1] == "is-enabled":
            out = "enabled" if argv[2] in self.enabled else "disabled"
        elif argv[1] == "is-active":
            out = "active" if argv[2] in self.active else "inactive"
        return subprocess.CompletedProcess(argv, 0, out + "\n", "")


class RekeyTests(unittest.TestCase):
    OLD_UNIT = "psk-recorder@AI6VN\\x3d1.service"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        r = self.root
        for d in ("etc/psk-recorder/env", "etc/sigmond/clients",
                  "etc/systemd/system/" + self.OLD_UNIT + ".d",
                  "var/lib/psk-recorder/AI6VN\\x3d1"):
            (r / d).mkdir(parents=True)
        (r / "etc/psk-recorder/AI6VN=1.toml").write_text(
            '[[radiod]]\nstatus = "ai6vn-1-status.local"\n')
        (r / "etc/psk-recorder/env/AI6VN=1.env").write_text("PSK_USE_HS_UPLOADER=1\n")
        (r / "etc/sigmond/clients/psk-recorder@AI6VN=1.sources.toml").write_text(
            'selected = ["radiod:ai6vn-1-status.local"]\n')
        (r / "var/lib/psk-recorder/AI6VN\\x3d1/spool.dat").write_text("data")
        (r / "etc/systemd/system" / (self.OLD_UNIT + ".d") / "x.conf").write_text("[Service]\n")
        self.units = {"psk-recorder": {"AI6VN\\x3d1"}}

    def test_everything_moves_to_the_key_and_the_unit_follows(self):
        sc = _Systemctl(enabled={self.OLD_UNIT}, active={self.OLD_UNIT})
        steps = R.apply(self.root, run=sc, units=self.units)
        r = self.root
        self.assertTrue((r / "etc/psk-recorder/AI6VN-1.toml").exists())
        self.assertFalse((r / "etc/psk-recorder/AI6VN=1.toml").exists())
        self.assertEqual((r / "etc/psk-recorder/env/AI6VN-1.env").read_text(),
                         "PSK_USE_HS_UPLOADER=1\n")
        self.assertTrue((r / "etc/sigmond/clients/psk-recorder@AI6VN-1.sources.toml").exists())
        self.assertEqual((r / "var/lib/psk-recorder/AI6VN-1/spool.dat").read_text(), "data")
        self.assertTrue((r / "etc/systemd/system/psk-recorder@AI6VN-1.service.d/x.conf").exists())
        # the true id is written into the moved config
        cfg = tomllib.loads((r / "etc/psk-recorder/AI6VN-1.toml").read_text())
        self.assertEqual(cfg["instance"]["reporter_id"], "AI6VN/1")
        self.assertEqual(cfg["radiod"][0]["status"], "ai6vn-1-status.local")
        # old unit stopped + disabled, new one enabled + started, in that order
        acts = [c for c in sc.calls if c[0] in ("stop", "disable", "enable", "start", "daemon-reload")]
        self.assertEqual(acts, [["stop", self.OLD_UNIT], ["disable", self.OLD_UNIT],
                                ["daemon-reload"],
                                ["enable", "psk-recorder@AI6VN-1.service"],
                                ["start", "psk-recorder@AI6VN-1.service"]])
        # every moved file has a backup
        backups = list((r / R.BACKUP_DIR).rglob("*"))
        self.assertTrue(any(p.name == "AI6VN=1.toml" for p in backups))
        self.assertTrue(any(p.name == "AI6VN=1.env" for p in backups))
        self.assertFalse([s for s in steps if s.startswith("WARN")], steps)

    def test_second_run_does_nothing(self):
        R.apply(self.root, run=_Systemctl(), units=self.units)
        sc = _Systemctl()
        self.assertEqual(R.apply(self.root, run=sc, units={}), [])
        self.assertEqual(sc.calls, [])

    def test_a_stopped_disabled_unit_stays_off(self):
        sc = _Systemctl()
        R.apply(self.root, run=sc, units=self.units)
        self.assertFalse([c for c in sc.calls if c[0] in ("enable", "start")])

    def test_dry_run_changes_nothing(self):
        steps = R.apply(self.root, dry_run=True, run=_Systemctl(), units=self.units)
        self.assertTrue((self.root / "etc/psk-recorder/AI6VN=1.toml").exists())
        self.assertTrue(any(s.startswith("would mv") for s in steps))

    def test_existing_different_target_is_refused_and_kept(self):
        (self.root / "etc/psk-recorder/AI6VN-1.toml").write_text(
            '[instance]\nreporter_id = "AI6VN-1"\n')
        sc = _Systemctl(enabled={self.OLD_UNIT}, active={self.OLD_UNIT})
        steps = R.apply(self.root, run=sc, units=self.units)
        self.assertTrue((self.root / "etc/psk-recorder/AI6VN=1.toml").exists())
        self.assertTrue(any("already exists" in s for s in steps), steps)
        # nothing touched: the old unit keeps running, nothing renamed
        self.assertFalse([c for c in sc.calls
                          if c[0] in ("stop", "disable", "enable", "start")])
        self.assertTrue((self.root / "etc/psk-recorder/env/AI6VN=1.env").exists())
        self.assertFalse((self.root / "etc/psk-recorder/env/AI6VN-1.env").exists())

    def test_an_existing_reporter_id_is_kept(self):
        (self.root / "etc/psk-recorder/AI6VN=1.toml").write_text(
            '[instance]\nreporter_id = "AI6VN/1"\n\n[[radiod]]\nstatus = "x"\n')
        R.apply(self.root, run=_Systemctl(), units=self.units)
        txt = (self.root / "etc/psk-recorder/AI6VN-1.toml").read_text()
        self.assertEqual(txt.count("reporter_id"), 1)

    def test_names_without_equals_are_left_alone(self):
        (self.root / "etc/psk-recorder/W1ABC-5.toml").write_text("")
        (self.root / "etc/psk-recorder/env/my-rx888.env").write_text("")
        items = R.plan(self.root, {})
        self.assertEqual([(i.client, i.storage) for i in items],
                         [("psk-recorder", "AI6VN=1")])


if __name__ == "__main__":
    unittest.main()
