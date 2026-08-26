"""Tests for sigmond.uploader_manifest — the Stage 6 manifest generator.

Covers the TOML serializer round-trip, placeholder substitution (incl. the
skip-on-missing-identity path), and an end-to-end generate that reproduces the
pipeline shape hs_uploader.pipeline_factory consumes.
"""

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond import uploader_manifest as um
from sigmond.coordination import Coordination, Heartbeat, Host, Radiod


class SerializerTests(unittest.TestCase):
    def test_roundtrip_nested_and_scalars(self):
        identity = {"call": "AC0G/S", "grid": "EM38ww",
                    "ssh_key_file": "/etc/hs-uploader/keys/id_ed25519_host"}
        pipelines = [
            {
                "name": "wspr-wsprnet",
                "batch_limit": 900,
                "source": {
                    "type": "sqlite",
                    "accepted_schema_versions": [1, 2],
                    "delete_on_commit": False,
                    "dedup_partition_by": ["time", "callsign", "band"],
                },
                "transport": {"type": "wsprnet", "version": "4.0"},
                "retry": {"base": 2.0, "cap_sec": 900.0},
            },
            {
                "name": "psk-pskreporter",
                "source": {
                    "type": "sqlite",
                    "extra_where": [["tx_call", "!=", ""],
                                    ["mode", "IN", ["ft8", "ft4"]]],
                },
                "transport": {
                    "type": "wsprdaemon_tar",
                    "servers": ["gw1", "gw2"],
                    "ftp_fallback": {"servers": ["gw2"], "ftp_user": "x"},
                },
            },
        ]
        text = um.render_manifest(pipelines, identity)
        parsed = tomllib.loads(text)

        self.assertEqual(parsed["identity"], identity)
        self.assertEqual(parsed["daemon"]["pump_interval_sec"], 30)
        self.assertEqual([p["name"] for p in parsed["pipeline"]],
                         ["wspr-wsprnet", "psk-pskreporter"])
        p0 = parsed["pipeline"][0]
        self.assertEqual(p0["source"]["accepted_schema_versions"], [1, 2])
        self.assertIs(p0["source"]["delete_on_commit"], False)
        self.assertEqual(p0["retry"]["cap_sec"], 900.0)
        # array-of-arrays
        self.assertEqual(parsed["pipeline"][1]["source"]["extra_where"],
                         [["tx_call", "!=", ""], ["mode", "IN", ["ft8", "ft4"]]])
        # nested-nested table
        self.assertEqual(
            parsed["pipeline"][1]["transport"]["ftp_fallback"]["ftp_user"], "x")

    def test_value_escaping(self):
        self.assertEqual(um._toml_value('a"b\\c'), '"a\\"b\\\\c"')
        self.assertEqual(um._toml_value(True), "true")
        self.assertEqual(um._toml_value(2.0), "2.0")


class SubstitutionTests(unittest.TestCase):
    def test_subst_tracks_used_and_missing(self):
        used, missing = set(), set()
        out = um._subst(
            {"a": "{call}", "b": ["{grid}", "x"], "c": {"d": "radiod={radiod_status}"}},
            {"call": "AC0G", "grid": "EM38ww", "radiod_status": None},
            used, missing)
        self.assertEqual(out["a"], "AC0G")
        self.assertEqual(out["b"], ["EM38ww", "x"])
        # unresolved token is left intact and flagged
        self.assertEqual(out["c"]["d"], "radiod={radiod_status}")
        self.assertEqual(missing, {"radiod_status"})
        self.assertEqual(used, {"call", "grid", "radiod_status"})

    def test_unknown_token_is_treated_as_missing(self):
        """A placeholder the token map never offers must be flagged.

        Regression: the 2026-08-24 hamsci-physics split moved the GRAPE
        pipeline declaration to a client that is not in psws.RECORDERS, so
        resolve_tokens() never put station_id/instrument_id in the map at
        all.  _subst() only flagged tokens that were present-but-None, so
        the literal "{station_id}" was written into the manifest and the
        daemon sftp'd to a user named "{station_id}" for two days.
        """
        used, missing = set(), set()
        out = um._subst({"u": "{station_id}", "n": "psws:{instrument_id}"},
                        {"call": "AC0G"}, used, missing)
        self.assertEqual(out["u"], "{station_id}")
        self.assertEqual(missing, {"station_id", "instrument_id"})

    def test_subst_leaves_non_placeholder_braces_alone(self):
        used, missing = set(), set()
        out = um._subst({"a": "{}", "b": "{Not A Token}", "c": "{9x}"},
                        {"call": "AC0G"}, used, missing)
        self.assertEqual(out, {"a": "{}", "b": "{Not A Token}", "c": "{9x}"})
        self.assertEqual(missing, set())


def _coord():
    return Coordination(
        host=Host(call="AC0G", grid="EM38ww"),
        radiods={"sigma-status.local": Radiod(id="sigma-status.local")},
    )


class _Topo:
    def __init__(self, names):
        self._names = names

    def enabled_components(self, only=None):
        return list(self._names)


class _State:
    def __init__(self, station="", instrument=""):
        self.station = station
        self.instrument = instrument


class CollectTests(unittest.TestCase):
    def _write_deploy(self, body: str) -> Path:
        f = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
        f.write(body)
        f.close()
        return Path(f.name)

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

    def test_skip_when_identity_missing(self):
        deploy = self._write_deploy(self.GRAPE)
        with mock.patch.object(um, "find_deploy_toml", return_value=deploy), \
             mock.patch.object(um, "list_instances", return_value=[]), \
             mock.patch.object(um.psws, "is_psws_recorder", return_value=True), \
             mock.patch.object(um.psws, "read_state",
                               return_value=_State(station="", instrument="")):
            pls = um.collect_pipelines(_Topo(["hf-timestd"]), _coord())
        self.assertEqual(pls, [])

    def test_skip_when_client_is_not_a_psws_recorder(self):
        """Unknown-client => no identity tokens => skip, never pass through."""
        deploy = self._write_deploy(self.GRAPE)
        with mock.patch.object(um, "find_deploy_toml", return_value=deploy), \
             mock.patch.object(um, "list_instances", return_value=[]), \
             mock.patch.object(um.psws, "is_psws_recorder", return_value=False):
            pls = um.collect_pipelines(_Topo(["hamsci-physics"]), _coord())
        self.assertEqual(pls, [])

    def test_substituted_when_identity_present(self):
        deploy = self._write_deploy(self.GRAPE)
        with mock.patch.object(um, "find_deploy_toml", return_value=deploy), \
             mock.patch.object(um, "list_instances", return_value=[]), \
             mock.patch.object(um.psws, "is_psws_recorder", return_value=True), \
             mock.patch.object(um.psws, "read_state",
                               return_value=_State(station="S000418",
                                                   instrument="367")):
            pls = um.collect_pipelines(_Topo(["hf-timestd"]), _coord())
        self.assertEqual(len(pls), 1)
        t = pls[0]["transport"]
        self.assertEqual(t["instrument_id"], "367")
        self.assertEqual(t["sftp_user"], "S000418")
        self.assertEqual(t["name"], "psws-grape-sftp:host:S000418")

    def test_generate_endtoend_parses(self):
        deploy = self._write_deploy(self.GRAPE)
        with mock.patch.object(um, "find_deploy_toml", return_value=deploy), \
             mock.patch.object(um, "list_instances", return_value=[]), \
             mock.patch.object(um.psws, "is_psws_recorder", return_value=True), \
             mock.patch.object(um.psws, "read_state",
                               return_value=_State(station="S000418",
                                                   instrument="367")), \
             mock.patch.object(um, "host_key_file", return_value="/k"):
            text = um.generate(_Topo(["hf-timestd"]), _coord())
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["identity"]["call"], "AC0G")  # no wspr instance
        self.assertEqual(parsed["identity"]["grid"], "EM38ww")
        self.assertEqual(parsed["identity"]["station_id"], "S000418")
        self.assertEqual([p["name"] for p in parsed["pipeline"]], ["grape-psws"])

    # Shared pipeline both psk-recorder and meteor-scatter declare (MSK144
    # rides the psk.spots stream) — must dedup to exactly one.
    SHARED = """
[[hs_uploader.pipeline]]
name = "psk-pskreporter"
batch_limit = 500
[hs_uploader.pipeline.source]
type = "sqlite"
database = "psk"
table = "spots"
extra_where = [["mode", "IN", ["ft8", "ft4", "msk144"]]]
[hs_uploader.pipeline.transport]
type = "pskreporter"
decoding_software = "psk-recorder/0.1 (radiod={radiod_status})"
"""

    def test_dedup_shared_pipeline_by_name(self):
        deploy = self._write_deploy(self.SHARED)
        # both clients resolve to identical declarations
        with mock.patch.object(um, "find_deploy_toml", return_value=deploy), \
             mock.patch.object(um, "list_instances", return_value=[]):
            pls = um.collect_pipelines(
                _Topo(["psk-recorder", "meteor-scatter"]), _coord())
        self.assertEqual([p["name"] for p in pls], ["psk-pskreporter"])
        self.assertEqual(
            pls[0]["source"]["extra_where"],
            [["mode", "IN", ["ft8", "ft4", "msk144"]]])
        # {radiod_status} substituted from coordination's radiod
        self.assertIn("sigma-status.local",
                      pls[0]["transport"]["decoding_software"])


class HeartbeatPipelineTests(unittest.TestCase):
    """sigmond#task-10 — the station-level heartbeat pipeline, rendered from
    [heartbeat] in coordination.toml (not any client's deploy.toml — the
    heartbeat is a station product, no client declares it)."""

    def _coord_hb(self, **hb_kwargs) -> Coordination:
        return Coordination(
            host=Host(call="AC0G", grid="EM38ww"),
            radiods={"sigma-status.local": Radiod(id="sigma-status.local")},
            heartbeat=Heartbeat(**hb_kwargs),
        )

    def test_present_when_enabled_and_resolved(self):
        coord = self._coord_hb(enabled=True, station="S000418",
                               host="drop.hamsci.org")
        pl = um.heartbeat_pipeline(coord)
        self.assertEqual(pl, {
            "name": "heartbeat",
            "source": {
                "type": "filetree",
                "root": "/var/lib/sigmond/heartbeat",
                "patterns": ["*.json"],
                "table": "station.heartbeat",
                "retention": "delete_on_ack",
            },
            "transport": {
                "type": "heartbeat_sftp",
                "host": "drop.hamsci.org",
                "port": 22,
                "sftp_user": "hamsci-hb",
                "remote_path": "incoming",
            },
            "retry": {"base": 2.0, "cap_sec": 300.0},
        })

    def test_present_with_non_default_transport_values(self):
        coord = self._coord_hb(enabled=True, station="S000418",
                               host="drop.hamsci.org", port=2222,
                               sftp_user="hb2", remote_path="drop")
        pl = um.heartbeat_pipeline(coord)
        self.assertEqual(pl["transport"], {
            "type": "heartbeat_sftp",
            "host": "drop.hamsci.org",
            "port": 2222,
            "sftp_user": "hb2",
            "remote_path": "drop",
        })

    def test_missing_station_skipped_with_warning(self):
        coord = self._coord_hb(enabled=True, station="", host="drop.hamsci.org")
        with self.assertLogs(um.logger, level="WARNING") as cm:
            pl = um.heartbeat_pipeline(coord)
        self.assertIsNone(pl)
        self.assertTrue(any("heartbeat" in msg for msg in cm.output))

    def test_missing_host_skipped_with_warning(self):
        coord = self._coord_hb(enabled=True, station="S000418", host="")
        with self.assertLogs(um.logger, level="WARNING") as cm:
            pl = um.heartbeat_pipeline(coord)
        self.assertIsNone(pl)
        self.assertTrue(any("heartbeat" in msg for msg in cm.output))

    def test_disabled_is_absent_no_warning(self):
        coord = self._coord_hb(enabled=False, station="S000418",
                               host="drop.hamsci.org")
        with self.assertNoLogs(um.logger, level="WARNING"):
            pl = um.heartbeat_pipeline(coord)
        self.assertIsNone(pl)

    def test_block_absent_is_absent_no_warning(self):
        coord = Coordination(
            host=Host(call="AC0G", grid="EM38ww"),
            radiods={"sigma-status.local": Radiod(id="sigma-status.local")},
        )
        with self.assertNoLogs(um.logger, level="WARNING"):
            pl = um.heartbeat_pipeline(coord)
        self.assertIsNone(pl)

    def test_generate_appends_heartbeat_after_client_pipelines(self):
        coord = self._coord_hb(enabled=True, station="S000418",
                               host="drop.hamsci.org")
        with mock.patch.object(um, "host_key_file", return_value="/k"), \
             mock.patch.object(um, "list_instances", return_value=[]):
            text = um.generate(_Topo([]), coord)
        parsed = tomllib.loads(text)
        self.assertEqual([p["name"] for p in parsed["pipeline"]], ["heartbeat"])


class HeartbeatCrossRepoShapeTests(unittest.TestCase):
    """CROSS-REPO SHAPE TEST: the rendered heartbeat pipeline must build via
    hs_uploader.pipeline_factory.build_pipelines, using its already-registered
    "heartbeat_sftp" transport (hs-uploader HEAD b3941bb). No cross-repo
    import precedent exists in this test module, so sys.path.insert the
    hs-uploader src dir here, scoped to this test class."""

    @classmethod
    def setUpClass(cls):
        hs_uploader_src = (Path(__file__).resolve().parent.parent.parent
                           / "hs-uploader" / "src")
        if not hs_uploader_src.is_dir():
            raise unittest.SkipTest(
                f"hs-uploader checkout not found at {hs_uploader_src}")
        sys.path.insert(0, str(hs_uploader_src))

    def test_rendered_manifest_builds_with_heartbeat_sftp_transport(self):
        from hs_uploader.pipeline_factory import build_pipelines
        from hs_uploader.transports.heartbeat_sftp import HeartbeatSftp
        from hs_uploader.watermark.sqlite import SqliteWatermarkStore

        coord = Coordination(
            host=Host(call="AC0G", grid="EM38ww"),
            radiods={"sigma-status.local": Radiod(id="sigma-status.local")},
            heartbeat=Heartbeat(enabled=True, station="S000418",
                                host="drop.hamsci.org"),
        )
        pipelines = [um.heartbeat_pipeline(coord)]
        identity = {"call": "AC0G", "grid": "EM38ww",
                   "ssh_key_file": "/etc/hs-uploader/keys/id_ed25519_host"}
        text = um.render_manifest(pipelines, identity)
        manifest = tomllib.loads(text)

        built = build_pipelines(manifest, watermark=SqliteWatermarkStore(":memory:"))
        self.assertEqual(len(built), 1)
        p = built[0]
        self.assertIsInstance(p.transport, HeartbeatSftp)
        self.assertEqual(p.transport.host, "drop.hamsci.org")
        self.assertEqual(p.transport.primary_table(), "station.heartbeat")


if __name__ == "__main__":
    unittest.main()


class UploadsPolicyRenderTests(unittest.TestCase):
    """sigmond#53: `[uploads] enabled = false` renders the heartbeat pipeline
    ONLY and says so loudly in the manifest header; enabled (the default)
    renders exactly as before."""

    def _coord(self, *, enabled: bool, reason: str = "") -> Coordination:
        from sigmond.coordination import Uploads
        return Coordination(
            host=Host(call="DASI002", grid="FN21ok"),
            heartbeat=Heartbeat(enabled=True, station="dasi002",
                                host="drop.example.org"),
            uploads=Uploads(enabled=enabled, reason=reason),
        )

    def _generate(self, coord):
        deploy = Path(tempfile.mkdtemp()) / "deploy.toml"
        deploy.write_text(CollectTests.GRAPE)
        with mock.patch.object(um, "find_deploy_toml", return_value=deploy), \
             mock.patch.object(um, "list_instances", return_value=[]), \
             mock.patch.object(um.psws, "is_psws_recorder", return_value=True), \
             mock.patch.object(um.psws, "read_state",
                               return_value=_State(station="S000418",
                                                   instrument="367")), \
             mock.patch.object(um, "host_key_file", return_value="/k"):
            return um.generate(_Topo(["hf-timestd"]), coord)

    def test_disabled_renders_heartbeat_only_with_policy_header(self):
        text = self._generate(self._coord(enabled=False, reason="no HF antenna"))
        parsed = tomllib.loads(text)
        self.assertEqual([p["name"] for p in parsed["pipeline"]], ["heartbeat"])
        self.assertIn("DISABLED BY POLICY", text)
        self.assertIn("no HF antenna", text)
        self.assertIn("[uploads] enabled = false", text)

    def test_enabled_is_unchanged(self):
        text = self._generate(self._coord(enabled=True))
        parsed = tomllib.loads(text)
        self.assertEqual([p["name"] for p in parsed["pipeline"]],
                         ["grape-psws", "heartbeat"])
        self.assertNotIn("DISABLED BY POLICY", text)

    def test_suppressed_pipelines_are_reported(self):
        """The command layer needs the names it suppressed, to print them."""
        coord = self._coord(enabled=False, reason="x")
        deploy = Path(tempfile.mkdtemp()) / "deploy.toml"
        deploy.write_text(CollectTests.GRAPE)
        with mock.patch.object(um, "find_deploy_toml", return_value=deploy), \
             mock.patch.object(um, "list_instances", return_value=[]), \
             mock.patch.object(um.psws, "is_psws_recorder", return_value=True), \
             mock.patch.object(um.psws, "read_state",
                               return_value=_State(station="S000418",
                                                   instrument="367")), \
             mock.patch.object(um, "host_key_file", return_value="/k"):
            self.assertEqual(um.suppressed_pipelines(_Topo(["hf-timestd"]), coord),
                             ["grape-psws"])
            self.assertEqual(um.suppressed_pipelines(
                _Topo(["hf-timestd"]), self._coord(enabled=True)), [])
