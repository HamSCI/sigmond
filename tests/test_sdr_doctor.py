"""smd doctor's sdr family (v3.71): did this station lose its RX888 in the
last 24 h, and does that fail doctor?

A loss that recovered within 30 minutes reports for 24 h and leaves the exit
code at 0: the station works, and a script that runs doctor must not read a
recovered stall as a broken host.  A card off the bus now, a loss that stayed
open longer than 30 minutes, or three losses in a day, fail it.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import sdr_events as ev  # noqa: E402
from sigmond.doctor import Finding, SOFT_FINDING_KINDS  # noqa: E402


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader("smd_under_test_sdr_doctor", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_sdr_doctor", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()
NOW = 1_760_000_000.0


def usb_tree(root: Path, card: bool) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if card:
        d = root / "8-1.2"
        d.mkdir()
        (d / "idVendor").write_text("04b4\n")
        (d / "idProduct").write_text("00f1\n")
    return root


class SdrFamilyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = self.tmp / "sdr-events.jsonl"

    def find(self, card=True):
        return smd._sdr_findings(path=self.p, sysfs=usb_tree(self.tmp / "sys", card), now=NOW)

    def test_no_file_no_finding(self):
        self.assertEqual(self.find(), [])
        self.assertEqual(self.find(card=False), [])

    def test_one_stall_with_the_card_on_the_bus_reads_sdr_recovered(self):
        ev.record("radiod-exit", "ac0g core-dump dumped/ABRT", now=NOW - 3600, path=self.p)
        (f,) = self.find()
        self.assertEqual((f.component, f.kind, f.fixable), ("ka9q-radio", "sdr-recovered", False))
        self.assertIn("1 time(s) in 24 h", f.detail)

    def test_nds_three_stalls_in_a_day_read_sdr_flapping(self):
        for ago in (22.6 * 3600, 18 * 3600, 60):
            ev.record("radiod-exit", now=NOW - ago, path=self.p)
        (f,) = self.find()
        self.assertEqual(f.kind, "sdr-flapping")

    def test_the_card_off_the_bus_reads_sdr_lost(self):
        ev.record("usb-remove", "8-1.2", now=NOW - 120, path=self.p)
        (f,) = self.find(card=False)
        self.assertEqual(f.kind, "sdr-lost")

    def test_a_restart_loop_past_30_minutes_reads_sdr_down_long(self):
        for i in range(100):     # 100 radiod exits 20 s apart: one episode, 33 min
            ev.record("radiod-exit", now=NOW - 3600 + 20 * i, path=self.p)
        (f,) = self.find()
        self.assertEqual(f.kind, "sdr-down-long")
        self.assertIn("33 min", f.detail)

    def test_a_watchdog_restart_reads_like_any_other_loss(self):
        ev.record("radiod-wedge", "radiod@ac0g.service no journal output for 700s (heartbeat lost)",
                  now=NOW - 600, path=self.p)
        (f,) = self.find()
        self.assertEqual(f.kind, "sdr-recovered")
        self.assertIn("radiod-wedge", f.detail)

    def test_a_symlinked_record_reads_as_a_hard_finding(self):
        target = self.tmp / "elsewhere"
        target.write_text("")
        self.p.symlink_to(target)
        (f,) = self.find()
        self.assertEqual((f.component, f.kind, f.fixable), ("ka9q-radio", "sdr-record-unreadable", False))
        self.assertIn("cannot read the RX888 event record", f.detail)
        self.assertNotIn(f.kind, SOFT_FINDING_KINDS)

    def test_a_dangling_symlink_is_not_silence(self):
        self.p.symlink_to(self.tmp / "nowhere")
        (f,) = self.find()
        self.assertEqual(f.kind, "sdr-record-unreadable")

    def test_a_fifo_record_reads_as_a_hard_finding_and_never_blocks(self):
        os.mkfifo(self.p)
        (f,) = self.find()
        self.assertEqual(f.kind, "sdr-record-unreadable")
        self.assertIn("cannot read the RX888 event record", f.detail)

    def test_an_episode_older_than_24_h_makes_no_finding(self):
        ev.record("radiod-exit", now=NOW - 90_000, path=self.p)
        self.assertEqual(self.find(), [])

    def test_the_family_runs_inside_collect_findings(self):
        f = Finding("ka9q-radio", "sdr-recovered", "x")
        with mock.patch.object(smd, "_sdr_findings", return_value=[f]):
            _, findings = smd.collect_findings(base=tempfile.mkdtemp(), strict=False)
        self.assertIn(f, findings)


class ExitCodeTests(unittest.TestCase):
    SOFT = Finding("ka9q-radio", "sdr-recovered",
                   "RX888 lost 1 time(s) in 24 h, last at 2026-10-10 14:48Z (radiod-exit, 1 event(s)); recovered")

    def doctor(self, findings, clean=False):
        args = types.SimpleNamespace(base=tempfile.mkdtemp(), fix=False)
        out = io.StringIO()
        with mock.patch.object(smd, "collect_findings", return_value=(clean, findings)), \
             contextlib.redirect_stdout(out):
            rc = smd.cmd_doctor(args)
        return rc, out.getvalue()

    def test_a_recovered_rx888_reports_and_exits_0(self):
        rc, out = self.doctor([self.SOFT])
        self.assertEqual(rc, 0)
        self.assertIn("sdr-recovered", out)

    def test_beside_a_hard_finding_doctor_still_exits_1(self):
        rc, _ = self.doctor([self.SOFT, Finding("sigmond", "dirty", "1 modified file(s)")])
        self.assertEqual(rc, 1)

    def test_lost_flapping_and_down_long_exit_1(self):
        for kind in ("sdr-lost", "sdr-flapping", "sdr-down-long"):
            rc, _ = self.doctor([Finding("ka9q-radio", kind, "x")])
            self.assertEqual(rc, 1, kind)

    def test_an_unreadable_record_exits_1(self):
        rc, _ = self.doctor([Finding("ka9q-radio", "sdr-record-unreadable", "x")])
        self.assertEqual(rc, 1)

    def test_a_clean_doctor_still_exits_0(self):
        rc, _ = self.doctor([], clean=True)
        self.assertEqual(rc, 0)

    def test_the_update_rigs_parser_still_reads_the_kind(self):
        _, out = self.doctor([self.SOFT])
        kinds = re.findall(r"^    ([A-Za-z][A-Za-z0-9_-]*):", out, re.M)
        self.assertEqual(kinds, ["sdr-recovered"])
