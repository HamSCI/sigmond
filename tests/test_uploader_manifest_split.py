"""Tests for the pskreporter producer split in sigmond.uploader_manifest
(tasks/spec-key-split-v373.md §5.1, D17 answer 2).

psk-recorder and meteor-scatter both declare the shared ``psk-pskreporter``
pipeline in their deploy.toml.  Against an hs-uploader whose store reports
schema 2 or more, the renderer turns each declaration into its producer's own
pipeline; against an older one it keeps the shared pipeline and warns.
"""

import os
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond import uploader_manifest as um
from sigmond.coordination import Coordination, Heartbeat, Host, Radiod


# The block both clients ship today (psk-recorder/deploy.toml,
# meteor-scatter/deploy.toml), verbatim but for comments.
SHARED_BLOCK = """
[[hs_uploader.pipeline]]
name = "psk-pskreporter"
batch_limit = 500
[hs_uploader.pipeline.source]
type = "sqlite"
database = "psk"
table = "spots"
accepted_schema_versions = [2]
start_at = "now"
delete_on_commit = false
select_columns = ["time", "frequency", "mode", "snr_db", "tx_call", "grid", "score", "message", "rx_source", "frequency_bucket_hz"]
extra_where = [["tx_call", "!=", ""], ["mode", "IN", ["ft8", "ft4", "msk144"]], ["forward_to_pskreporter", "=", 0]]
[hs_uploader.pipeline.transport]
type = "pskreporter"
decoding_software = "psk-recorder/0.1 (radiod={radiod_status})"
[hs_uploader.pipeline.retry]
base = 2.0
cap_sec = 300.0
"""

GRAPE = """
[[hs_uploader.pipeline]]
name = "grape-psws"
[hs_uploader.pipeline.source]
type = "filetree"
root = "/var/lib/timestd/upload"
table = "grape.dataset"
[hs_uploader.pipeline.transport]
type = "psws_dataset"
instrument_id = "{instrument_id}"
sftp_user = "{station_id}"
name = "psws-grape-sftp:host:{station_id}"
"""


def _coord(**kw):
    return Coordination(
        host=Host(call="AC0G", grid="EM38ww"),
        radiods={"sigma-status.local": Radiod(id="sigma-status.local")},
        **kw)


class _Topo:
    def __init__(self, names):
        self._names = names

    def enabled_components(self, only=None):
        return list(self._names)


class _State:
    def __init__(self, station="", instrument=""):
        self.station = station
        self.instrument = instrument


def _deploys(tmp: Path, bodies: dict):
    paths = {}
    for client, body in bodies.items():
        p = tmp / f"{client}.toml"
        p.write_text(body)
        paths[client] = p
    return lambda client: paths.get(client)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(um, "list_instances", return_value=[]).start()
        mock.patch.object(um, "host_key_file", return_value="/k").start()

    def collect(self, clients, store_version, bodies=None):
        bodies = bodies or {c: SHARED_BLOCK for c in clients}
        with mock.patch.object(um, "find_deploy_toml",
                               side_effect=_deploys(self.tmp, bodies)):
            return um.collect_pipelines(_Topo(clients), _coord(),
                                        store_version=store_version)


class SplitTests(_Base):
    def _block_source(self):
        return tomllib.loads(SHARED_BLOCK)["hs_uploader"]["pipeline"][0]

    def test_both_clients_render_two_producer_pipelines(self):
        pls = self.collect(["psk-recorder", "meteor-scatter"], 2)
        by = {p["name"]: p for p in pls}
        self.assertEqual(sorted(by), ["meteor-scatter-pskreporter",
                                      "psk-recorder-pskreporter"])
        self.assertNotIn("psk-pskreporter", by)

        psk = by["psk-recorder-pskreporter"]
        ms = by["meteor-scatter-pskreporter"]
        self.assertEqual(psk["client"], "psk-recorder")
        self.assertEqual(ms["client"], "meteor-scatter")
        self.assertEqual(psk["source"]["producer"], "psk-recorder")
        self.assertEqual(ms["source"]["producer"], "meteor-scatter")
        self.assertEqual(psk["source"]["extra_where"],
                         [["tx_call", "!=", ""], ["mode", "IN", ["ft8", "ft4"]],
                          ["forward_to_pskreporter", "=", 0]])
        self.assertEqual(ms["source"]["extra_where"],
                         [["tx_call", "!=", ""], ["mode", "IN", ["msk144"]],
                          ["forward_to_pskreporter", "=", 0]])

        # Everything else copies from the block.
        block = self._block_source()
        for p in (psk, ms):
            self.assertEqual(p["batch_limit"], block["batch_limit"])
            self.assertEqual(p["retry"], block["retry"])
            self.assertEqual(p["transport"]["type"], "pskreporter")
            self.assertEqual(p["transport"]["decoding_software"],
                             "psk-recorder/0.1 (radiod=sigma-status.local)")
            src = dict(p["source"])
            src.pop("producer")
            src.pop("extra_where")
            want = dict(block["source"])
            want.pop("extra_where")
            self.assertEqual(src, want)
            self.assertEqual(set(p) - {"client"}, set(block))

    def test_the_block_is_not_mutated_between_clients(self):
        """One client's rewrite must not leak into the other's dict."""
        pls = self.collect(["meteor-scatter", "psk-recorder"], 2)
        modes = {p["name"]: p["source"]["extra_where"][1][2] for p in pls}
        self.assertEqual(modes, {"meteor-scatter-pskreporter": ["msk144"],
                                 "psk-recorder-pskreporter": ["ft8", "ft4"]})

    def test_psk_recorder_alone_renders_only_its_pipeline(self):
        pls = self.collect(["psk-recorder"], 2)
        self.assertEqual([p["name"] for p in pls], ["psk-recorder-pskreporter"])
        self.assertEqual(pls[0]["source"]["producer"], "psk-recorder")

    def test_meteor_scatter_alone_renders_only_its_pipeline(self):
        pls = self.collect(["meteor-scatter"], 2)
        self.assertEqual([p["name"] for p in pls],
                         ["meteor-scatter-pskreporter"])
        self.assertEqual(pls[0]["source"]["producer"], "meteor-scatter")
        self.assertEqual(pls[0]["source"]["extra_where"][1],
                         ["mode", "IN", ["msk144"]])

    def test_a_later_store_version_also_splits(self):
        pls = self.collect(["psk-recorder", "meteor-scatter"], 3)
        self.assertEqual(sorted(p["name"] for p in pls),
                         ["meteor-scatter-pskreporter",
                          "psk-recorder-pskreporter"])


class LaggingUploaderTests(_Base):
    def _assert_shared(self, store_version):
        with self.assertLogs(um.logger, level="WARNING") as cm:
            pls = self.collect(["psk-recorder", "meteor-scatter"], store_version)
        self.assertEqual([p["name"] for p in pls], ["psk-pskreporter"])
        self.assertNotIn("producer", pls[0]["source"])
        self.assertEqual(pls[0]["source"]["extra_where"],
                         [["tx_call", "!=", ""],
                          ["mode", "IN", ["ft8", "ft4", "msk144"]],
                          ["forward_to_pskreporter", "=", 0]])
        lag = [m for m in cm.output if "lags sigmond" in m]
        self.assertEqual(len(lag), 1, cm.output)

    def test_store_version_1_renders_the_shared_pipeline_with_one_warning(self):
        self._assert_shared(1)

    def test_no_capabilities_subcommand_renders_the_shared_pipeline(self):
        self._assert_shared(0)

    def test_no_warning_when_no_shared_block_is_declared(self):
        bodies = {"hf-timestd": GRAPE}
        with mock.patch.object(um.psws, "is_psws_recorder", return_value=True), \
             mock.patch.object(um.psws, "read_state",
                               return_value=_State("S000418", "367")), \
             self.assertNoLogs(um.logger, level="WARNING"):
            self.collect(["hf-timestd"], 1, bodies)


class ClientFieldTests(_Base):
    def test_every_other_pipeline_carries_its_declaring_client(self):
        bodies = {"hf-timestd": GRAPE, "psk-recorder": SHARED_BLOCK}
        with mock.patch.object(um.psws, "is_psws_recorder",
                               side_effect=lambda c: c == "hf-timestd"), \
             mock.patch.object(um.psws, "read_state",
                               return_value=_State("S000418", "367")):
            pls = self.collect(["hf-timestd", "psk-recorder"], 2, bodies)
        clients = {p["name"]: p["client"] for p in pls}
        self.assertEqual(clients, {"grape-psws": "hf-timestd",
                                   "psk-recorder-pskreporter": "psk-recorder"})

    def test_the_shared_pipeline_names_its_first_declarer(self):
        with self.assertLogs(um.logger, level="WARNING"):
            pls = self.collect(["psk-recorder", "meteor-scatter"], 1)
        self.assertEqual(pls[0]["client"], "psk-recorder")

    def test_heartbeat_pipeline_belongs_to_sigmond(self):
        coord = _coord(heartbeat=Heartbeat(enabled=True, station="S000418",
                                           host="drop.hamsci.org"))
        self.assertEqual(um.heartbeat_pipeline(coord)["client"], "sigmond")


class GenerateTests(_Base):
    def _generate(self, clients, **kw):
        with mock.patch.object(um, "find_deploy_toml",
                               side_effect=_deploys(
                                   self.tmp, {c: SHARED_BLOCK for c in clients})):
            return um.generate(_Topo(clients), _coord(), **kw)

    def test_probe_failure_raises_probe_failed(self):
        with mock.patch.object(um, "hs_uploader_store_version",
                               return_value=None):
            with self.assertRaises(um.ProbeFailed):
                self._generate(["psk-recorder"])

    def test_generate_probes_once_and_renders_the_split(self):
        with mock.patch.object(um, "hs_uploader_store_version",
                               return_value=2) as probe:
            text = self._generate(["psk-recorder", "meteor-scatter"])
        self.assertEqual(probe.call_count, 1)
        parsed = tomllib.loads(text)
        by = {p["name"]: p for p in parsed["pipeline"]}
        # producer lands inside [pipeline.source]; client at the entry level.
        self.assertEqual(by["psk-recorder-pskreporter"]["source"]["producer"],
                         "psk-recorder")
        self.assertEqual(by["psk-recorder-pskreporter"]["client"],
                         "psk-recorder")
        self.assertEqual(by["meteor-scatter-pskreporter"]["source"]["producer"],
                         "meteor-scatter")

    def test_explicit_store_version_skips_the_probe(self):
        with mock.patch.object(um, "hs_uploader_store_version",
                               side_effect=AssertionError("probed")):
            text = self._generate(["psk-recorder"], store_version=2)
        self.assertIn('producer = "psk-recorder"', text)


class ProbeTests(unittest.TestCase):
    """The probe asks the service's own interpreter for
    ``hs-uploader capabilities``."""

    def _fake_python(self, body: str) -> Path:
        d = Path(tempfile.mkdtemp())
        p = d / "python"
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        return p

    def test_reports_the_integer(self):
        py = self._fake_python('echo 2; exit 0')
        self.assertEqual(um.hs_uploader_store_version(py), 2)

    def test_an_older_uploader_without_the_subcommand_answers_zero(self):
        py = self._fake_python(
            "echo \"cli.py: error: argument cmd: invalid choice: "
            "'capabilities' (choose from 'status', 'serve')\" >&2; exit 2")
        self.assertEqual(um.hs_uploader_store_version(py), 0)

    def test_unparsable_output_is_a_failure(self):
        py = self._fake_python('echo two; exit 0')
        self.assertIsNone(um.hs_uploader_store_version(py))

    def test_other_nonzero_exit_is_a_failure(self):
        py = self._fake_python('echo "No module named hs_uploader" >&2; exit 1')
        self.assertIsNone(um.hs_uploader_store_version(py))

    def test_missing_interpreter_is_a_failure(self):
        missing = Path(tempfile.mkdtemp()) / "no-such-python"
        self.assertIsNone(um.hs_uploader_store_version(missing))

    def test_timeout_is_a_failure(self):
        with mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired("python", 20)):
            self.assertIsNone(um.hs_uploader_store_version(Path("/x")))

    def test_asks_the_real_hs_uploader_checkout(self):
        """Cross-repo: the sibling hs-uploader answers its own
        SCHEMA_VERSION through this probe, so a bump there needs no edit
        here.  It runs the checkout's own venv interpreter, as the runner
        runs the daemon's: under ``-I`` no PYTHONPATH reaches the child, and
        the venv's editable install resolves the package (ruling C14-2)."""
        py = (Path(__file__).resolve().parent.parent.parent / "hs-uploader"
              / ".venv" / "bin" / "python")
        if not py.exists():
            raise unittest.SkipTest(f"no hs-uploader venv at {py}")
        out = subprocess.run(
            [str(py), "-I", "-c",
             "from hs_uploader.watermark.schema import SCHEMA_VERSION;"
             "print(SCHEMA_VERSION)"],
            capture_output=True, text=True, check=True, cwd="/").stdout
        SCHEMA_VERSION = int(out.strip())
        self.assertEqual(um.hs_uploader_store_version(py), SCHEMA_VERSION)


class CrossRepoBuildTests(_Base):
    """The rendered split builds in hs-uploader's factory with the producer
    and client it reads."""

    def test_hs_uploader_builds_the_rendered_split(self):
        src = Path(__file__).resolve().parent.parent.parent / "hs-uploader" / "src"
        if not (src / "hs_uploader" / "pipeline_factory.py").is_file():
            raise unittest.SkipTest(f"no hs-uploader at {src}")
        sys.path.insert(0, str(src))
        from hs_uploader.pipeline_factory import build_pipelines
        from hs_uploader.watermark.sqlite import SqliteWatermarkStore

        pls = self.collect(["psk-recorder", "meteor-scatter"], 2)
        text = um.render_manifest(pls, {"call": "AC0G", "grid": "EM38ww",
                                        "ssh_key_file": "/k"})
        built = {p.name: p for p in build_pipelines(
            tomllib.loads(text), watermark=SqliteWatermarkStore(":memory:"))}
        self.assertEqual(sorted(built), ["meteor-scatter-pskreporter",
                                         "psk-recorder-pskreporter"])
        self.assertEqual(built["psk-recorder-pskreporter"].client,
                         "psk-recorder")
        self.assertEqual(built["meteor-scatter-pskreporter"].client,
                         "meteor-scatter")
        self.assertEqual(built["psk-recorder-pskreporter"].source.source_id(),
                         "sqlite:psk.spots@psk-recorder")
        self.assertEqual(built["meteor-scatter-pskreporter"].source.source_id(),
                         "sqlite:psk.spots@meteor-scatter")


if __name__ == "__main__":
    unittest.main()
