"""Operator input enters through the parser, then leaves for systemd in
systemd's spelling (tasks/spec-reporter-id-v372.md §2; inventory rows S37,
S39, S41, S42, S46, S48, S50, S54, S55)."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_watch_instance", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class WatchInstanceArgTests(unittest.TestCase):

    def setUp(self):
        # Which units "exist" on this pretend host.
        self.known = set()
        patch = mock.patch.object(smd, "_unit_known", lambda unit: unit in self.known)
        patch.start()
        self.addCleanup(patch.stop)

    def _watch(self, text, client):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            got = smd._watch_instance(text, client)
        return got, out.getvalue()

    def test_every_spelling_names_the_same_unit(self):
        for text in ("AC0G/B4", "AC0G=B4", "AC0G\\x3dB4", "ac0g/b4"):
            with self.subTest(text=text):
                self.assertEqual(self._watch(text, "psk-recorder")[0],
                                 ("AC0G=B4", "psk-recorder@AC0G\\x3dB4.service"))

    def test_lower_and_mixed_case_reach_the_upper_case_instance(self):
        # Review Focus 1.
        self.known = {"psk-recorder@W1ABC-5.service"}
        for text in ("w1abc-5", "W1abc-5"):
            with self.subTest(text=text):
                got, said = self._watch(text, "psk-recorder")
                self.assertEqual(got, ("W1ABC-5", "psk-recorder@W1ABC-5.service"))
                self.assertNotIn("legacy", said)

    def test_hyphens_stay_hyphens(self):
        # S37: systemd-escape turned W1ABC-5 into W1ABC\x2d5, a unit nobody enabled.
        self.assertEqual(self._watch("W1ABC-5", "meteor-scatter")[0],
                         ("W1ABC-5", "meteor-scatter@W1ABC-5.service"))

    def test_a_legacy_unit_named_exactly_as_typed_is_reached_with_a_hint(self):
        # Review Focus 1: MY-RX888 has no unit; my-rx888 has one.
        self.known = {"wspr-recorder@my-rx888.service"}
        got, said = self._watch("my-rx888", "wspr-recorder")
        self.assertEqual(got, ("my-rx888", "wspr-recorder@my-rx888.service"))
        self.assertIn("legacy instance name my-rx888; run: smd admin instance migrate", said)

    def test_a_legacy_name_that_is_no_reporter_id_is_reached_with_a_hint(self):
        self.known = {"psk-recorder@bee1-status.local.service"}
        got, said = self._watch("bee1-status.local", "psk-recorder")
        self.assertEqual(got, ("bee1-status.local", "psk-recorder@bee1-status.local.service"))
        self.assertIn("legacy instance name bee1-status.local", said)

    def test_the_reporter_unit_wins_over_a_legacy_unit_when_both_exist(self):
        self.known = {"wspr-recorder@MY-RX888.service",
                      "wspr-recorder@my-rx888.service"}
        got, said = self._watch("my-rx888", "wspr-recorder")
        self.assertEqual(got, ("MY-RX888", "wspr-recorder@MY-RX888.service"))
        self.assertEqual(said, "")

    def test_when_no_unit_exists_the_reporter_unit_is_named(self):
        got, said = self._watch("my-rx888", "wspr-recorder")
        self.assertEqual(got, ("MY-RX888", "wspr-recorder@MY-RX888.service"))
        self.assertEqual(said, "")

    def test_no_instance(self):
        self.assertEqual(smd._watch_instance(None, "psk-recorder"), (None, None))

    def test_junk_is_refused(self):
        for text in ("AC0G/", "bee1-status.local"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                smd._watch_instance(text, "psk-recorder")

    def test_the_private_copies_are_gone(self):
        self.assertFalse(hasattr(smd, "_systemd_escape_reporter"))
        self.assertFalse(hasattr(smd, "_display_reporter"))
        self.assertFalse(hasattr(smd, "_parse_instance_arg"))


class SuperdarnInstanceDirTests(unittest.TestCase):
    """superdarn-sounder spools under --instance %i, systemd's spelling
    (/var/lib/superdarn-sounder/AC0G\\x3dB4/); a storage-form directory
    (AC0G=B4) is the second choice."""

    ESCAPED = "AC0G\\x3dB4"

    def setUp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def _dir(self, text):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
            got = smd._superdarn_instance_dir(text, self.root)
        return got, err.getvalue()

    def test_every_spelling_reaches_the_directory_superdarn_writes(self):
        (self.root / self.ESCAPED).mkdir()
        for text in ("AC0G/B4", "AC0G=B4", "AC0G\\x3dB4", "ac0g/b4"):
            with self.subTest(text=text):
                got, said = self._dir(text)
                self.assertEqual(got, ("AC0G=B4", self.root / self.ESCAPED))
                self.assertNotIn("legacy", said)

    def test_the_systemd_spelling_wins_over_the_storage_form(self):
        (self.root / self.ESCAPED).mkdir()
        (self.root / "AC0G=B4").mkdir()
        self.assertEqual(self._dir("AC0G/B4")[0], ("AC0G=B4", self.root / self.ESCAPED))

    def test_a_storage_form_directory_is_the_second_choice(self):
        (self.root / "AC0G=B4").mkdir()
        for text in ("AC0G/B4", "AC0G=B4", "AC0G\\x3dB4"):
            with self.subTest(text=text):
                got, said = self._dir(text)
                self.assertEqual(got, ("AC0G=B4", self.root / "AC0G=B4"))
                self.assertNotIn("legacy", said)

    def test_with_no_directory_the_one_superdarn_would_write_is_named(self):
        self.assertEqual(self._dir("AC0G/B4")[0], ("AC0G=B4", self.root / self.ESCAPED))

    def test_a_legacy_directory_named_as_typed_is_reached_with_a_hint(self):
        (self.root / "my-rx888").mkdir()
        got, said = self._dir("my-rx888")
        self.assertEqual(got, ("my-rx888", self.root / "my-rx888"))
        self.assertIn("legacy instance name my-rx888", said)

    def test_the_reporter_directory_wins_when_both_exist(self):
        (self.root / "my-rx888").mkdir()
        (self.root / "MY-RX888").mkdir()
        got, said = self._dir("my-rx888")
        self.assertEqual(got, ("MY-RX888", self.root / "MY-RX888"))
        self.assertNotIn("legacy", said)

    def test_junk_is_refused(self):
        with self.assertRaises(ValueError):
            smd._superdarn_instance_dir("AC0G/", self.root)

    def test_without_instance_the_unit_names_the_directory_as_systemd_spells_it(self):
        # The pre-Task-5 behaviour: the active unit's %i IS the spool key.
        units = "superdarn-sounder@AC0G\\x3dB4.service loaded active running x\n"
        out = io.StringIO()
        with mock.patch.object(smd.subprocess, "run", lambda argv, **kw:
                               subprocess.CompletedProcess(argv, 0, units, "")), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = smd.cmd_superdarn_watch(argparse.Namespace(
                instance=None, recent=0, verbose=False))
        self.assertEqual(rc, 1)          # no such directory on this machine
        self.assertIn("/var/lib/superdarn-sounder/AC0G\\x3dB4", out.getvalue())


class SpoolWatchInstanceTests(unittest.TestCase):
    """Final review 4: hfdl-watch and codar-watch take --instance in any
    spelling (spec §7), find the per-instance config by its storage name,
    and fall back to the data dir the unit's %i names (AC0G\\x3dB4)."""

    def setUp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.known = set()

    def _rooted(self):
        real = Path

        def P(*parts):
            p = real(*parts)
            s = str(p)
            if s == "/etc" or s.startswith("/etc/"):
                return real(self.tmp + s)
            return p
        return P

    def _run(self, cmd, text):
        out = io.StringIO()
        with mock.patch.object(smd, "Path", self._rooted()), \
             mock.patch.object(smd, "_unit_known", lambda unit: unit in self.known), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = cmd(argparse.Namespace(instance=text, recent=0, verbose=False))
        self.assertEqual(rc, 1)          # no data dir on this machine
        return out.getvalue()

    def _config(self, client, name):
        cfg = Path(self.tmp) / "etc" / client / f"{name}.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('[[radiod]]\nstatus = "b4-status.local"\n')

    CMDS = (("hfdl-recorder", "cmd_hfdl_watch"), ("codar-sounder", "cmd_codar_watch"))

    def test_every_spelling_finds_the_storage_config(self):
        for client, cmd in self.CMDS:
            self._config(client, "AC0G=B4")
            for text in ("AC0G/B4", "AC0G=B4", "AC0G\\x3dB4"):
                with self.subTest(client=client, text=text):
                    said = self._run(getattr(smd, cmd), text)
                    self.assertIn(f"/var/lib/{client}/b4-status.local", said)

    def test_without_a_config_the_unit_spelling_names_the_data_dir(self):
        for client, cmd in self.CMDS:
            for text in ("AC0G/B4", "AC0G=B4", "AC0G\\x3dB4"):
                with self.subTest(client=client, text=text):
                    said = self._run(getattr(smd, cmd), text)
                    self.assertIn(f"/var/lib/{client}/AC0G\\x3dB4 ", said)

    def test_a_legacy_unit_named_as_typed_is_reached_with_a_hint(self):
        for client, cmd in self.CMDS:
            self.known = {f"{client}@my-rx888.service"}
            with self.subTest(client=client):
                said = self._run(getattr(smd, cmd), "my-rx888")
                self.assertIn("legacy instance name my-rx888", said)
                self.assertIn(f"/var/lib/{client}/my-rx888 ", said)

    def test_junk_is_refused(self):
        for client, cmd in self.CMDS:
            with self.subTest(client=client):
                said = self._run(getattr(smd, cmd), "AC0G/")
                self.assertIn("--instance", said)
                self.assertNotIn("/var/lib/", said)


class WatchCommandUnitTests(unittest.TestCase):
    """S39, S41, S42, S46: each watch asks systemd about the right unit."""

    def _patterns(self, cmd, instance="AC0G/B4"):
        seen = []

        def preflight(label, pattern, hint=''):
            seen.append(pattern)
            return 1
        args = argparse.Namespace(instance=instance, recent=0, verbose=False,
                                  backlog=0)
        with mock.patch.object(smd, "_watch_preflight", preflight), \
             mock.patch.object(smd, "_unit_known", lambda unit: False), \
             contextlib.redirect_stdout(io.StringIO()):
            rc = cmd(args)
        self.assertEqual(rc, 1)
        return seen

    def test_psk_watch(self):
        self.assertEqual(self._patterns(smd.cmd_psk_watch),
                         ["psk-recorder@AC0G\\x3dB4.service"])

    def test_wspr_watch(self):
        self.assertEqual(self._patterns(smd.cmd_wspr_watch),
                         ["wspr-recorder@AC0G\\x3dB4.service"])

    def test_verifier_watch(self):
        self.assertEqual(self._patterns(smd.cmd_verifier_watch),
                         ["wspr-recorder@AC0G\\x3dB4.service"])

    def test_meteor_watch(self):
        self.assertEqual(self._patterns(smd.cmd_meteor_watch),
                         ["meteor-scatter@AC0G\\x3dB4.service"])

    def test_a_bad_instance_never_reaches_systemd(self):
        for cmd in (smd.cmd_psk_watch, smd.cmd_wspr_watch,
                    smd.cmd_verifier_watch, smd.cmd_meteor_watch):
            with self.subTest(cmd=cmd.__name__):
                self.assertEqual(self._patterns(cmd, instance="AC0G/"), [])

    def test_preflight_names_the_unit_for_a_person(self):
        out = io.StringIO()
        with mock.patch.object(smd.subprocess, "run", lambda argv, **kw:
                               subprocess.CompletedProcess(argv, 0, "", "")), \
             contextlib.redirect_stdout(out):
            rc = smd._watch_preflight("psk-watch", "psk-recorder@AC0G\\x3dB4.service")
        self.assertEqual(rc, 1)
        self.assertIn("psk-recorder@AC0G/B4", out.getvalue())
        self.assertNotIn("\\x3d", out.getvalue())


class SourcesKeyTests(unittest.TestCase):
    """S48 and `smd admin sources remove`, which never normalised its key."""

    def test_every_spelling_is_a_valid_per_instance_key(self):
        for key in ("wspr-recorder@AC0G/B4", "wspr-recorder@AC0G=B4",
                    "wspr-recorder@AC0G\\x3dB4"):
            with self.subTest(key=key):
                self.assertTrue(smd._is_valid_per_instance_client(key, {"wspr-recorder"}))

    def test_normalize_instance_client(self):
        self.assertEqual(smd._normalize_instance_client("wspr-recorder@AC0G\\x3dB4"),
                         "wspr-recorder@AC0G=B4")
        self.assertEqual(smd._normalize_instance_client("wspr-recorder"), "wspr-recorder")

    def test_sources_remove_loads_the_storage_key(self):
        loaded = []

        class FakeSources:
            selected = []

            @classmethod
            def load(cls, key):
                loaded.append(key)
                return cls()
        with mock.patch.object(smd, "_need_root", lambda *a, **k: False), \
             mock.patch("sigmond.sources.ClientSources", FakeSources), \
             contextlib.redirect_stdout(io.StringIO()):
            rc = smd.cmd_sources_remove(argparse.Namespace(
                client="wspr-recorder@AC0G/B4", source="radiod:x"))
        self.assertEqual(rc, 0)
        self.assertEqual(loaded, ["wspr-recorder@AC0G=B4"])

    def test_restart_hint(self):
        # S50: the instance for a person, then a command systemctl accepts.
        self.assertEqual(
            smd._restart_hint("wspr-recorder", "AC0G=B4"),
            "wspr-recorder@AC0G/B4  "
            "(sudo systemctl restart 'wspr-recorder@AC0G\\x3dB4.service')")


class SshHostTests(unittest.TestCase):
    """S54, S55: reporter ids are not ssh aliases."""

    UNITS = ("wspr-recorder@AC0G\\x3dB4.service loaded active running x\n"
             "psk-recorder@W1ABC-5.service loaded active running x\n"
             "wspr-recorder@bee1.service loaded active running x\n")

    def test_radiod_watch_offers_only_peer_names(self):
        with mock.patch.object(smd, "_get_radiod_instances", lambda: []), \
             mock.patch.object(smd.subprocess, "run", lambda argv, **kw:
                               subprocess.CompletedProcess(argv, 0, self.UNITS, "")):
            self.assertEqual(smd._radiod_watch_candidate_hosts(), ["localhost", "bee1"])

    def test_bash_completion_offers_only_peer_names(self):
        line = next(l for l in smd._BASH_COMPLETION.splitlines() if "_rhosts=" in l)
        pipeline = line.split("2>/dev/null | ", 1)[1].rsplit(" | sort -u)", 1)[0]
        r = subprocess.run(["bash", "-c", pipeline], input=self.UNITS,
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.split(), ["bee1"])


if __name__ == "__main__":
    unittest.main()
