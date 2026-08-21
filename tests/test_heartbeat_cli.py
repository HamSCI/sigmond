"""Tests for `smd admin heartbeat emit`/`show` — the CLI wrapper that
wires sigmond.heartbeat (sigmond#task-7's assembler) into a runnable
verb (sigmond#task-8).

Mirrors tests/test_manifest_adopt.py's CLI pattern: bin/smd is loaded
via SourceFileLoader and the cmd_* functions are called directly with a
fabricated argparse Namespace, so no shelling out and no real /run or
/var access. Readers are faked at the sigmond.heartbeat.default_readers
boundary (mock.patch); the spool directory is redirected via the
module-level HEARTBEAT_SPOOL_DIR constant (same technique
test_manifest_adopt.py uses for smd.MANIFEST_PATH).
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from sigmond.coordination import Coordination, Heartbeat, Host
from sigmond.heartbeat_schema import BLOCK_NAMES, validate

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_heartbeat_cli", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_heartbeat_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def _fake_readers():
    """A readers dict whose mappers never raise and produce an envelope
    that validates cleanly — the exact rollup verdict does not matter
    to these CLI tests (sigmond.heartbeat's own suite covers that)."""
    return {
        "versions": lambda: {"components": {"sigmond": "abc1234"}},
        "manifest": lambda: {"present": False, "blessed_source": "/x", "drift": []},
        "timing": lambda: None,
        "gaps": lambda: {},
        "uploads": lambda: {"readable": True, "pipelines": [], "cursors": []},
        "doctor": lambda: (True, []),
        "resources": lambda: {},
    }


def _coord(*, enabled=True, station="AC0G-B4", call="AC0G", grid="EM38ww",
          interval_sec=300):
    return Coordination(
        host=Host(call=call, grid=grid),
        heartbeat=Heartbeat(enabled=enabled, station=station,
                            interval_sec=interval_sec),
    )


class HeartbeatCliTestCase(unittest.TestCase):
    def setUp(self):
        self.tdir = Path(tempfile.mkdtemp())
        self.spool = self.tdir / "heartbeat"
        self._orig_spool = smd.HEARTBEAT_SPOOL_DIR
        smd.HEARTBEAT_SPOOL_DIR = self.spool

    def tearDown(self):
        smd.HEARTBEAT_SPOOL_DIR = self._orig_spool

    def _run_emit(self, coord, *, dry_run=False):
        args = types.SimpleNamespace(dry_run=dry_run)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(smd, 'load_coordination', return_value=coord), \
             mock.patch('sigmond.heartbeat.default_readers',
                        return_value=_fake_readers()), \
             mock.patch('sigmond.uploader_manifest.reporter_call',
                        return_value=coord.host.call or None):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = smd.cmd_admin_heartbeat_emit(args)
        return rc, out.getvalue(), err.getvalue()

    def _run_show(self, *, as_json=False):
        args = types.SimpleNamespace(json=as_json)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = smd.cmd_admin_heartbeat_show(args)
        return rc, out.getvalue(), err.getvalue()


class EmitNotEnabledTests(HeartbeatCliTestCase):

    def test_absent_heartbeat_block_exits_2(self):
        rc, out, err = self._run_emit(Coordination())  # default Heartbeat(): disabled
        self.assertEqual(rc, 2)
        self.assertIn('heartbeat: not enabled', out + err)
        self.assertIn('coordination.toml', out + err)
        self.assertFalse(self.spool.exists() and any(self.spool.iterdir()))

    def test_explicit_enabled_false_exits_2(self):
        rc, out, err = self._run_emit(_coord(enabled=False))
        self.assertEqual(rc, 2)
        self.assertIn('heartbeat: not enabled', out + err)


class EmitStationMissingTests(HeartbeatCliTestCase):

    def test_blank_station_exits_2(self):
        rc, out, err = self._run_emit(_coord(enabled=True, station=''))
        self.assertEqual(rc, 2)
        self.assertIn('station', (out + err).lower())

    def test_whitespace_only_station_exits_2(self):
        rc, out, err = self._run_emit(_coord(enabled=True, station='   '))
        self.assertEqual(rc, 2)


class EmitDryRunTests(HeartbeatCliTestCase):

    def test_dry_run_prints_valid_json_and_writes_nothing(self):
        rc, out, err = self._run_emit(_coord(), dry_run=True)
        self.assertEqual(rc, 0)
        envelope = json.loads(out)          # must be the ONLY thing on stdout
        self.assertEqual(envelope['station'], 'AC0G-B4')
        self.assertEqual(validate(envelope), [])
        self.assertFalse(self.spool.exists())

    def test_dry_run_stdout_is_json_only(self):
        rc, out, err = self._run_emit(_coord(), dry_run=True)
        self.assertEqual(rc, 0)
        # The whole of stdout must parse — no banner/log lines mixed in,
        # since a script may pipe this straight to `jq`/`json.loads`.
        json.loads(out)


class EmitDefaultWriteTests(HeartbeatCliTestCase):

    def test_writes_exactly_one_file_and_prints_its_path(self):
        rc, out, err = self._run_emit(_coord())
        self.assertEqual(rc, 0)
        files = list(self.spool.glob('*.json'))
        self.assertEqual(len(files), 1)
        self.assertIn(str(files[0]), out)

    def test_written_tick_is_valid(self):
        self._run_emit(_coord())
        files = list(self.spool.glob('*.json'))
        envelope = json.loads(files[0].read_text())
        self.assertEqual(validate(envelope), [])
        self.assertEqual(envelope['station'], 'AC0G-B4')
        self.assertEqual(envelope['callsign'], 'AC0G')
        self.assertEqual(envelope['grid'], 'EM38ww')


class EmitInvalidEnvelopeNeverWritesTests(HeartbeatCliTestCase):
    """errs from validate() -> print them, exit 1, and never write —
    the self-check the brief calls out explicitly."""

    def test_validate_failure_exits_1_and_never_writes(self):
        args = types.SimpleNamespace(dry_run=False)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(smd, 'load_coordination', return_value=_coord()), \
             mock.patch('sigmond.heartbeat.default_readers',
                        return_value=_fake_readers()), \
             mock.patch('sigmond.uploader_manifest.reporter_call',
                        return_value='AC0G'), \
             mock.patch('sigmond.heartbeat_schema.validate',
                        return_value=['envelope is bogus']):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = smd.cmd_admin_heartbeat_emit(args)
        self.assertEqual(rc, 1)
        self.assertIn('envelope is bogus', out.getvalue() + err.getvalue())
        self.assertFalse(self.spool.exists())


class ShowEmptySpoolTests(HeartbeatCliTestCase):

    def test_no_ticks_exits_1(self):
        rc, out, err = self._run_show()
        self.assertEqual(rc, 1)
        self.assertIn('no heartbeat ticks in', out + err)
        self.assertIn(str(self.spool), out + err)

    def test_no_ticks_when_spool_dir_does_not_exist(self):
        # spool dir was never created at all (not even mkdir'd)
        self.assertFalse(self.spool.exists())
        rc, out, err = self._run_show()
        self.assertEqual(rc, 1)


class ShowRendersLatestTests(HeartbeatCliTestCase):

    def _seed(self, station, verdict, reason, mtime_offset=0):
        self.spool.mkdir(parents=True, exist_ok=True)
        envelope = {
            "kind": "station_heartbeat", "schema_version": 1,
            "station": station, "callsign": None, "grid": None,
            "emitted_at": "2026-08-20T14:00:00Z", "interval_sec": 300,
            "uptime_s": 100.0,
            "rollup": {"verdict": verdict, "reason": reason},
            "blocks": {
                name: {"verdict": verdict, "reason": f"{name} reason"}
                for name in BLOCK_NAMES
            },
        }
        path = self.spool / f"{station}_2026082{mtime_offset}T140000Z.json"
        path.write_text(json.dumps(envelope))
        t = 1_000_000 + mtime_offset
        os.utime(path, (t, t))
        return path

    def test_renders_the_newest_tick(self):
        self._seed('OLD-STATION', 'VALID', 'all good', mtime_offset=0)
        self._seed('NEW-STATION', 'INVALID', 'manifest drifted', mtime_offset=5)
        rc, out, err = self._run_show()
        self.assertEqual(rc, 0)
        self.assertIn('NEW-STATION', out)
        self.assertIn('INVALID', out)
        self.assertIn('manifest drifted', out)
        self.assertNotIn('OLD-STATION', out)
        # per-block table: every block name shows up
        for name in BLOCK_NAMES:
            self.assertIn(name, out)

    def test_json_flag_prints_raw_envelope(self):
        self._seed('AC0G-B4', 'VALID', 'all good')
        rc, out, err = self._run_show(as_json=True)
        self.assertEqual(rc, 0)
        envelope = json.loads(out)
        self.assertEqual(envelope['station'], 'AC0G-B4')
        self.assertEqual(envelope['rollup']['verdict'], 'VALID')


if __name__ == '__main__':
    unittest.main()


class UploadsPolicyWiringTests(HeartbeatCliTestCase):
    """sigmond#53: `smd admin heartbeat emit` hands the site's [uploads]
    policy to default_readers so the uploads block can say 'disabled by
    policy' on the board."""

    def test_emit_passes_uploads_policy_to_default_readers(self):
        from sigmond.coordination import Uploads
        coord = _coord()
        coord.uploads = Uploads(enabled=False, reason="no HF antenna")
        args = types.SimpleNamespace(dry_run=True)
        with mock.patch.object(smd, 'load_coordination', return_value=coord), \
             mock.patch('sigmond.heartbeat.default_readers',
                        return_value=_fake_readers()) as dr, \
             mock.patch('sigmond.uploader_manifest.reporter_call',
                        return_value="AC0G"), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            smd.cmd_admin_heartbeat_emit(args)
        self.assertEqual(dr.call_args.kwargs.get("uploads_policy"),
                         {"enabled": False, "reason": "no HF antenna"})
