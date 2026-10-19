"""D13 at render time (tasks/spec-key-split-v373.md §5.3): a client's own
in-process pskreporter sender, read from each instance's effective
environment, removes only the matching daemon pipeline; a harmonize rule warns
when the manifest on disk disagrees."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sigmond import harmonize as H
from sigmond import inprocess_senders as ips
from sigmond import uploader_manifest as um
from test_uploader_manifest_split import (SHARED_BLOCK, _Topo, _coord,
                                          _deploys)

PSK = ("psk-recorder", "pskreporter")
MS = ("meteor-scatter", "pskreporter")


def _fake_show(envs):
    """show(unit) answering from a dict; an OSError value is raised."""
    def show(unit):
        v = envs[unit]
        if isinstance(v, Exception):
            raise v
        return v
    return show


class Senders(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        self.units = {"psk-recorder": ["psk-recorder@a.service"],
                      "meteor-scatter": ["meteor-scatter@a.service"]}
        mock.patch.object(
            ips, "_instance_units",
            side_effect=lambda client, topo: self.units.get(client, [])).start()

    def run_(self, psk_env=None, ms_env=None, clients=("psk-recorder",
                                                       "meteor-scatter")):
        envs = {"psk-recorder@a.service": {} if psk_env is None else psk_env,
                "meteor-scatter@a.service": {} if ms_env is None else ms_env}
        return ips.inprocess_senders(_Topo(list(clients)), show=_fake_show(envs))

    # psk-recorder
    def test_psk_pipelines_direct(self):
        self.assertEqual(self.run_({"PSK_DELIVERY_PIPELINES": "direct"},
                                   {"METEOR_SCATTER_DELIVERY_MODE": "off"}), {PSK})

    def test_psk_pipelines_list_with_spaces_and_case(self):
        self.assertIn(PSK, self.run_({"PSK_DELIVERY_PIPELINES": " Server-Raw , DIRECT "}))

    def test_psk_server_raw_none(self):
        self.assertNotIn(PSK, self.run_({"PSK_DELIVERY_PIPELINES": "server-raw"}))

    def test_psk_legacy_direct(self):
        self.assertIn(PSK, self.run_({"PSK_DELIVERY_MODE": "direct"}))

    def test_psk_legacy_both(self):
        self.assertIn(PSK, self.run_({"PSK_DELIVERY_MODE": "both"}))

    def test_psk_legacy_server_none(self):
        self.assertNotIn(PSK, self.run_({"PSK_DELIVERY_MODE": "server"}))

    def test_psk_pipelines_beat_legacy(self):
        self.assertNotIn(PSK, self.run_({"PSK_DELIVERY_PIPELINES": "server-raw",
                                         "PSK_DELIVERY_MODE": "direct"}))

    def test_psk_unknown_pipelines_fall_to_legacy(self):
        self.assertIn(PSK, self.run_({"PSK_DELIVERY_PIPELINES": "bogus",
                                      "PSK_DELIVERY_MODE": "direct"}))

    def test_psk_unset_none(self):
        self.assertNotIn(PSK, self.run_({}))

    # meteor-scatter
    def test_ms_direct(self):
        self.assertIn(MS, self.run_(None, {"METEOR_SCATTER_DELIVERY_MODE": "direct"}))

    def test_ms_unset_is_direct(self):
        self.assertEqual(self.run_({}, {}), {MS})

    def test_ms_off_values_none(self):
        for v in ("off", "none", "disabled", "deposit", " OFF "):
            self.assertNotIn(MS, self.run_(None, {"METEOR_SCATTER_DELIVERY_MODE": v}), v)

    def test_ms_empty_value_reads_as_direct(self):
        self.assertIn(MS, self.run_(None, {"METEOR_SCATTER_DELIVERY_MODE": ""}))

    # unreadable
    def test_unreadable_environment_gives_no_pair_for_that_client(self):
        envs = {"psk-recorder@a.service": OSError("denied"),
                "meteor-scatter@a.service": {}}
        got = ips.inprocess_senders(_Topo(["psk-recorder", "meteor-scatter"]),
                                    show=_fake_show(envs))
        self.assertEqual(got, {MS})

    def test_one_unreadable_instance_voids_the_client(self):
        self.units["meteor-scatter"] = ["meteor-scatter@a.service",
                                        "meteor-scatter@b.service"]
        envs = {"meteor-scatter@a.service": {},
                "meteor-scatter@b.service": OSError("x")}
        self.assertEqual(ips.inprocess_senders(
            _Topo(["meteor-scatter"]), show=_fake_show(envs)), set())

    def test_a_mixed_client_keeps_its_daemon_pipeline(self):
        self.units["psk-recorder"] = ["psk-recorder@a.service",
                                      "psk-recorder@b.service"]
        envs = {"psk-recorder@a.service": {"PSK_DELIVERY_PIPELINES": "direct"},
                "psk-recorder@b.service": {}}
        self.assertEqual(ips.inprocess_senders(
            _Topo(["psk-recorder"]), show=_fake_show(envs)), set())

    def test_client_not_enabled_or_without_instances(self):
        self.assertEqual(self.run_({"PSK_DELIVERY_PIPELINES": "direct"},
                                   clients=("meteor-scatter",)) & {PSK}, set())
        self.units["meteor-scatter"] = []
        self.assertEqual(self.run_(), set())


class SystemctlShow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _run(self, stdout, rc=0):
        def run(cmd, **kw):
            self.cmd = cmd
            return subprocess.CompletedProcess(cmd, rc, stdout, "")
        return run

    def test_asks_for_both_properties(self):
        ips._systemctl_show("u.service", run=self._run(""))
        self.assertEqual(self.cmd[:2], ["systemctl", "show"])
        self.assertIn("Environment", self.cmd)
        self.assertIn("EnvironmentFiles", self.cmd)
        self.assertEqual(self.cmd[-1], "u.service")

    def test_later_file_overrides_earlier_and_files_override_environment(self):
        a = self.tmp / "coordination.env"
        b = self.tmp / "i.env"
        a.write_text('METEOR_SCATTER_DELIVERY_MODE=off\nX="1"\n')
        b.write_text("X=2\n# c\n")
        out = (f'Environment=X=0 Y="a b"\n'
               f'EnvironmentFiles={a} (ignore_errors=yes)\n'
               f'EnvironmentFiles={b} (ignore_errors=yes)\n')
        env = ips._systemctl_show("u", run=self._run(out))
        self.assertEqual(env["METEOR_SCATTER_DELIVERY_MODE"], "off")
        self.assertEqual(env["X"], "2")
        self.assertEqual(env["Y"], "a b")

    def test_missing_ignored_file_is_skipped(self):
        out = f"EnvironmentFiles={self.tmp}/nope.env (ignore_errors=yes)\n"
        self.assertEqual(ips._systemctl_show("u", run=self._run(out)), {})

    def test_missing_required_file_raises(self):
        out = f"EnvironmentFiles={self.tmp}/nope.env (ignore_errors=no)\n"
        with self.assertRaises(OSError):
            ips._systemctl_show("u", run=self._run(out))

    def test_unreadable_file_raises(self):
        with mock.patch.object(Path, "read_text", side_effect=PermissionError):
            out = f"EnvironmentFiles={self.tmp}/x.env (ignore_errors=yes)\n"
            (self.tmp / "x.env").write_text("A=1\n")
            with self.assertRaises(OSError):
                ips._systemctl_show("u", run=self._run(out))

    def test_systemctl_failure_raises(self):
        with self.assertRaises(OSError):
            ips._systemctl_show("u", run=self._run("", rc=1))

    def test_no_systemctl_raises_oserror(self):
        def run(cmd, **kw):
            raise FileNotFoundError("systemctl")
        with self.assertRaises(OSError):
            ips._systemctl_show("u", run=run)


class Generate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(um, "list_instances", return_value=[]).start()
        mock.patch.object(um, "host_key_file", return_value="/k").start()

    def gen(self, senders, store_version, clients=("psk-recorder", "meteor-scatter")):
        bodies = {c: SHARED_BLOCK for c in clients}
        with mock.patch.object(um, "find_deploy_toml",
                               side_effect=_deploys(self.tmp, bodies)), \
             mock.patch("sigmond.inprocess_senders.inprocess_senders",
                        return_value=set(senders)):
            return um.generate(_Topo(list(clients)), _coord(),
                               supports_discard=True, store_version=store_version)

    def test_none_keeps_both(self):
        t = self.gen([], 2)
        self.assertIn('name = "psk-recorder-pskreporter"', t)
        self.assertIn('name = "meteor-scatter-pskreporter"', t)

    def test_psk_pair_omits_only_psk_pipeline(self):
        t = self.gen([PSK], 2)
        self.assertNotIn('name = "psk-recorder-pskreporter"', t)
        self.assertIn('name = "meteor-scatter-pskreporter"', t)

    def test_ms_pair_omits_only_ms_pipeline(self):
        t = self.gen([MS], 2)
        self.assertIn('name = "psk-recorder-pskreporter"', t)
        self.assertNotIn('name = "meteor-scatter-pskreporter"', t)

    def test_unsplit_manifest_keeps_shared_and_says_so(self):
        with self.assertLogs("sigmond.uploader_manifest", "WARNING") as cm:
            t = self.gen([PSK], 1)
        self.assertIn('name = "psk-pskreporter"', t)
        self.assertTrue(any("in-process" in m and "psk-recorder" in m
                            for m in cm.output), cm.output)


class DriftRule(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(mock.patch.stopall)

    def run_(self, senders, names, clients=("psk-recorder", "meteor-scatter")):
        manifest = self.tmp / "pipelines.toml"
        manifest.write_text("[identity]\n" + "".join(
            f'\n[[pipeline]]\nname = "{n}"\n' for n in names))
        mock.patch.object(um, "MANIFEST_PATH", manifest).start()
        mock.patch("sigmond.inprocess_senders.inprocess_senders",
                   return_value=set(senders)).start()
        view = mock.Mock(topology=_Topo(list(clients)))
        return H.rule_uploader_inprocess_drift(view)

    def test_in_agreement_passes(self):
        r = self.run_([PSK], ["meteor-scatter-pskreporter", "heartbeat"])
        self.assertEqual((r.rule, r.severity), ("uploader-inprocess-drift", "pass"))

    def test_pipeline_present_that_should_be_left_out_warns(self):
        r = self.run_([PSK], ["psk-recorder-pskreporter",
                              "meteor-scatter-pskreporter"])
        self.assertEqual(r.severity, "warn")
        self.assertIn("psk-recorder", r.message)

    def test_pipeline_missing_that_should_be_present_warns(self):
        r = self.run_([], ["meteor-scatter-pskreporter"])
        self.assertEqual(r.severity, "warn")
        self.assertIn("psk-recorder-pskreporter", r.message)

    def test_shared_pipeline_with_a_pair_is_the_known_lag_not_drift(self):
        r = self.run_([PSK], ["psk-pskreporter"])
        self.assertEqual(r.severity, "pass")

    def test_hold_manifest_with_no_data_pipelines_passes(self):
        r = self.run_([], ["heartbeat"])
        self.assertEqual(r.severity, "pass")

    def test_no_manifest_passes_skipped(self):
        mock.patch.object(um, "MANIFEST_PATH", self.tmp / "none.toml").start()
        mock.patch("sigmond.inprocess_senders.inprocess_senders",
                   return_value={PSK}).start()
        r = H.rule_uploader_inprocess_drift(mock.Mock(topology=_Topo(["psk-recorder"])))
        self.assertEqual(r.severity, "pass")

    def test_registered_as_runtime_rule(self):
        self.assertIn(H.rule_uploader_inprocess_drift, H.ALL_RUNTIME_RULES)


if __name__ == "__main__":
    unittest.main()
