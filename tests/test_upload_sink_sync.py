"""`smd config upload <client> --off/--on` also sets the client's sink switch.

PSK_USE_HS_UPLOADER=0 is the bring-up hold.  The hs-uploader gate follows
sinks/<client>.toml, not the env flag, so after the carry-over the verb
sets that switch too: --off -> fill, --on -> upload.  Clients whose flag
the carry-over reads as an order (sink_carryover.STOP_FLAGS: psk-recorder,
meteor-scatter) only; WSPR_USE_HS_UPLOADER marks a decode-only role.
"""
import argparse
import contextlib
import io
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from sigmond import sink_state as ss
from sigmond import upload
from sigmond import uploader_manifest as um
from sigmond.commands import config as C
from sigmond.commands import uploader as up_cmd
from sigmond.sink_state import ClientState, SinkState, SiteState


def _state(**clients):
    return SinkState(
        enforced=True,
        site=SiteState(state="upload", error="", set_at="", set_by="", reason=""),
        clients={n: ClientState(file_state=s, effective=s, held_by=None, error="")
                 for n, s in clients.items()})


class _Runner:
    def __init__(self, rc=0, err=""):
        self.calls, self.rc, self.err = [], rc, err

    def __call__(self, args, **kw):
        self.calls.append(args)
        return types.SimpleNamespace(returncode=self.rc, stdout="", stderr=self.err)


ROOTS = types.SimpleNamespace(python=Path("/x/python"), manifest=Path("/x/p.toml"),
                              sinks=Path("/x/sinks"), marker=Path("/x/marker"),
                              store=None)


class SyncTests(unittest.TestCase):

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())

    def _sync(self, client, on, state, *, marker=True, runner=None, instance="AC0G/S"):
        runner = runner or _Runner()
        with mock.patch.object(ss, "marker_stands", return_value=marker), \
             mock.patch.object(ss, "read_state", return_value=state), \
             mock.patch.object(um, "run_hs_uploader", runner):
            res = upload.sync_client_sink(client, instance, on,
                                          base=str(self.base), roots=ROOTS)
        return res, runner

    def test_off_sets_fill(self):
        res, r = self._sync("psk-recorder", False, _state(**{"psk-recorder": "upload"}))
        self.assertEqual(len(r.calls), 1)
        a = r.calls[0]
        self.assertEqual(a[:4], ["sink", "set", "psk-recorder", "fill"])
        self.assertIn("--sinks", a)

    def test_on_sets_upload(self):
        res, r = self._sync("psk-recorder", True, _state(**{"psk-recorder": "fill"}))
        self.assertEqual(r.calls[0][:4], ["sink", "set", "psk-recorder", "upload"])

    def test_already_so_writes_nothing(self):
        _, r = self._sync("psk-recorder", False, _state(**{"psk-recorder": "fill"}))
        self.assertEqual(r.calls, [])

    def test_meteor_scatter_shares_it(self):
        _, r = self._sync("meteor-scatter", False,
                          _state(**{"meteor-scatter": "upload"}))
        self.assertEqual(r.calls[0][:4], ["sink", "set", "meteor-scatter", "fill"])

    def test_wspr_recorder_is_left_alone(self):
        _, r = self._sync("wspr-recorder", False, _state(**{"wspr-recorder": "upload"}))
        self.assertEqual(r.calls, [])

    def test_before_the_marker_the_carry_over_reads_the_flag(self):
        _, r = self._sync("psk-recorder", False, None, marker=False)
        self.assertEqual(r.calls, [])

    def test_ungoverned_client_gets_no_file(self):
        _, r = self._sync("psk-recorder", False, _state(**{"wspr-recorder": "upload"}))
        self.assertEqual(r.calls, [])

    def test_unreadable_state_raises(self):
        with self.assertRaises(upload.SinkSyncError):
            self._sync("psk-recorder", False, None)

    def test_failed_set_raises_with_the_message(self):
        with self.assertRaises(upload.SinkSyncError) as cm:
            self._sync("psk-recorder", False, _state(**{"psk-recorder": "upload"}),
                       runner=_Runner(rc=1, err="belongs to root:sigmond"))
        self.assertIn("root:sigmond", str(cm.exception))

    def test_on_keeps_fill_while_another_instance_is_off(self):
        other = self.base / "psk-recorder/env/AC0G-T.env"
        other.parent.mkdir(parents=True)
        other.write_text("PSK_USE_HS_UPLOADER=0\n")
        _, r = self._sync("psk-recorder", True, _state(**{"psk-recorder": "fill"}))
        self.assertEqual(r.calls, [])


class CommandTests(unittest.TestCase):

    def _run(self, on, state):
        base = Path(tempfile.mkdtemp())
        runner = _Runner()
        topo = mock.Mock()
        topo.enabled_components.return_value = ["wspr-recorder", "psk-recorder"]
        real = upload.apply_enable
        args = argparse.Namespace(client="psk-recorder", instance="AC0G/S",
                                  on=on, off=not on, via=None)
        out, errb = io.StringIO(), io.StringIO()
        with mock.patch.object(up_cmd, "_step_roots", return_value=ROOTS), \
             mock.patch.object(ss, "marker_stands", return_value=True), \
             mock.patch.object(ss, "read_state", return_value=state), \
             mock.patch.object(um, "run_hs_uploader", runner), \
             mock.patch("sigmond.topology.load_topology", return_value=topo), \
             mock.patch.object(C, "_upload_on_lowered",
                               return_value=(frozenset(), "")), \
             mock.patch.object(upload, "apply_enable",
                               lambda c, i, on, delivery=None, hold_level=None:
                               real(c, i, on, base=str(base), delivery=delivery,
                                    hold_level=hold_level)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(errb):
            rc = C.cmd_config_upload(args)
        env = base / "psk-recorder/env/AC0G-S.env"
        return rc, runner, env.read_text() if env.exists() else None

    def test_off_writes_the_flag_and_sets_fill(self):
        rc, r, env = self._run(False, _state(**{"psk-recorder": "upload"}))
        self.assertEqual(rc, 0)
        self.assertIn("PSK_USE_HS_UPLOADER=0", env)
        self.assertEqual(r.calls[0][:4], ["sink", "set", "psk-recorder", "fill"])

    def test_on_writes_the_flag_and_sets_upload(self):
        rc, r, env = self._run(True, _state(**{"psk-recorder": "fill"}))
        self.assertEqual(rc, 0)
        self.assertIn("PSK_USE_HS_UPLOADER=1", env)
        self.assertEqual(r.calls[0][:4], ["sink", "set", "psk-recorder", "upload"])

    def test_a_failed_switch_writes_no_flag(self):
        base = Path(tempfile.mkdtemp())
        with mock.patch.object(upload, "sync_client_sink",
                               side_effect=upload.SinkSyncError("no root")):
            args = argparse.Namespace(client="psk-recorder", instance="AC0G/S",
                                      on=False, off=True, via=None)
            with mock.patch.object(upload, "apply_enable") as ae, \
                 contextlib.redirect_stderr(io.StringIO()), \
                 contextlib.redirect_stdout(io.StringIO()):
                rc = C.cmd_config_upload(args)
        self.assertEqual(rc, 1)
        ae.assert_not_called()


if __name__ == "__main__":
    unittest.main()
