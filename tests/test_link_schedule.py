"""Tests for sigmond-link — the South Pole satellite-pass schedule
(tasks/plan-constrained-link-delivery.md, Phase 4).

The fixtures are the USAP issue of 2026-11-05 (the PDF's UTC table as
pdftotext renders it) and cel.txt as generated 26/310.  They are pinned:
the schedule drifts ~4 min/day, so a live copy would fail these within a week
(plan trap 7).
"""
from __future__ import annotations

import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / "tests" / "fixtures" / "link"
UTC = dt.timezone.utc


def _load():
    p = str(REPO / "bin" / "sigmond-link")
    loader = importlib.machinery.SourceFileLoader("sigmond_link_under_test", p)
    spec = importlib.util.spec_from_file_location("sigmond_link_under_test", p, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


m = _load()
ROWS, PROBLEMS = m.parse_spsat((FIX / "spsat-20261105-utc.txt").read_text())
CEL = m.parse_cel((FIX / "cel-26310.txt").read_text())


def T(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


class TestParse(unittest.TestCase):
    def test_every_row_parses(self):
        self.assertEqual(PROBLEMS, [])
        self.assertEqual(len(ROWS), 15)
        self.assertEqual(min(ROWS), "2026-11-05")
        self.assertEqual(max(ROWS), "2026-11-19")

    def test_pinned_row_with_two_tdrs_passes(self):
        self.assertEqual(ROWS["2026-11-11"], [
            ["TDRS-6", "2026-11-11T14:40:00+00:00", "2026-11-11T19:10:00+00:00"],
            ["TDRS-6", "2026-11-11T19:12:00+00:00", "2026-11-11T20:42:00+00:00"],
            ["DSCS-B13", "2026-11-11T20:29:00+00:00", "2026-11-12T02:24:00+00:00"],
            ["DSCS-B11", "2026-11-11T05:21:00+00:00", "2026-11-11T10:33:00+00:00"],
            ["DSCS-A03", "2026-11-11T23:11:00+00:00", "2026-11-12T03:51:00+00:00"],
        ])

    def test_no_pass_days_have_no_tdrs(self):
        self.assertFalse(any(s == "TDRS-6" for s, *_ in ROWS["2026-11-05"]))
        self.assertEqual(sum(s == "TDRS-6" for s, *_ in ROWS["2026-11-12"]), 1)

    def test_cel_reads_utc_day_of_year(self):
        # 26/315/144000 191000 — day 315 of 2026 is 11 Nov
        self.assertIn(("2026-11-11T14:40:00+00:00", "2026-11-11T19:10:00+00:00"), CEL)

    def test_pdf_agrees_with_cel_so_both_are_utc(self):
        """The plan's trap 6: read the table in the wrong zone and the station
        talks only when it cannot.  The PDF's UTC table and cel.txt are
        independent sources; most TDRS passes must match exactly."""
        agree, differ = m.cross_check(ROWS, CEL)
        self.assertEqual((agree, differ), (18, 4))   # 4 = two real re-plans, 11-16 and 11-18

    def test_a_shifted_table_fails_the_cross_check(self):
        shifted = {d: [[s, (T(a[:19]) + dt.timedelta(hours=13)).isoformat(),
                        (T(b[:19]) + dt.timedelta(hours=13)).isoformat()] for s, a, b in p]
                   for d, p in ROWS.items()}
        self.assertEqual(m.cross_check(shifted, CEL)[0], 0)

    def test_only_the_utc_table_is_read(self):
        with self.assertRaises(ValueError):
            m.parse_spsat("South Pole Satellite Schedule (EST)\n11/05/26 No Pass\n")


class TestBuild(unittest.TestCase):
    def test_overlapping_passes_merge_and_gaps_are_the_published_ones(self):
        doc = m.build(ROWS, "all", 0)
        wins = [(T(w["start"][:19]), T(w["end"][:19])) for w in doc["windows"]]
        day = [w for w in wins if w[0].date() == dt.date(2026, 11, 12)]
        gaps = [(a[1].strftime("%H:%M"), b[0].strftime("%H:%M")) for a, b in zip(day, day[1:])]
        self.assertEqual(gaps, [("10:29", "14:36"), ("19:06", "20:25")])
        self.assertEqual(doc["horizon"], ["2026-11-05T00:00:00+00:00", "2026-11-20T00:00:00+00:00"])

    def test_satellite_filter_and_margin(self):
        doc = m.build(ROWS, ["TDRS-6"], 10)
        self.assertTrue(all(w["sats"] == ["TDRS-6"] for w in doc["windows"]))
        first = [w for w in doc["windows"] if w["start"].startswith("2026-11-11")][0]
        self.assertEqual(first["start"], "2026-11-11T14:50:00+00:00")
        self.assertEqual(first["end"], "2026-11-11T19:00:00+00:00")


def _doc(fetched="2026-11-12T00:00:00+00:00"):
    d = m.build(ROWS, "all", 0)
    d.update(version=1, fetched_at=fetched)
    return d


class TestEvaluate(unittest.TestCase):
    def test_in_a_pass(self):
        st = m.evaluate(_doc(), T("2026-11-12T16:00:00"))
        self.assertTrue(st["up"] and st["scheduled"])
        self.assertEqual(st["until"], "2026-11-12T19:06:00+00:00")

    def test_in_a_gap(self):
        st = m.evaluate(_doc(), T("2026-11-12T12:00:00"))
        self.assertFalse(st["up"])
        self.assertEqual(st["until"], "2026-11-12T14:36:00+00:00")

    def test_fail_open(self):
        gap = T("2026-11-12T12:00:00")
        for doc, t in [(None, gap),
                       (_doc(fetched="2026-11-01T00:00:00+00:00"), gap),   # stale
                       (_doc(), T("2026-11-25T12:00:00")),                  # past the horizon
                       ({"fetched_at": "x"}, gap),                          # garbage
                       (_doc(fetched="2026-11-12T00:00:00"), gap)]:         # no timezone
            self.assertTrue(m.evaluate(doc, t)["up"], doc and doc.get("fetched_at"))


class TestGateAndApply(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        m.SCHEDULE = self.tmp / "link-schedule.json"
        self.cfg = m.read_settings(self.tmp / "absent.toml")
        self.cfg["enabled"] = True

    def test_gate_exit_codes(self):
        m.SCHEDULE.write_text(json.dumps(_doc()))
        m.now = lambda: T("2026-11-12T12:00:00")
        self.assertEqual(m.cmd_gate(self.cfg), 1)
        m.now = lambda: T("2026-11-12T16:00:00")
        self.assertEqual(m.cmd_gate(self.cfg), 0)
        self.cfg["enabled"] = False
        m.now = lambda: T("2026-11-12T12:00:00")
        self.assertEqual(m.cmd_gate(self.cfg), 0)

    def test_gate_crash_lets_the_unit_run(self):
        m.SCHEDULE.write_text("{")      # corrupt file
        orig = m.read_settings
        m.read_settings = lambda *a: dict(self.cfg)
        try:
            self.assertEqual(m.main(["gate"]), 0)
        finally:
            m.read_settings = orig

    def test_fetch_never_tries_in_a_gap(self):
        m.SCHEDULE.write_text(json.dumps(_doc(fetched="2026-11-11T00:00:00+00:00")))
        m.now = lambda: T("2026-11-12T12:00:00")
        called = []
        m.fetch_usap = lambda rows: called.append(1) or (_ for _ in ()).throw(AssertionError)
        self.assertEqual(m.cmd_fetch(self.cfg, force=False), 0)
        self.assertEqual(called, [])

    def test_disabling_removes_the_schedule(self):
        m.SCHEDULE.write_text(json.dumps(_doc()))
        self.cfg["enabled"] = False
        m.cmd_fetch(self.cfg, force=False)
        self.assertFalse(m.SCHEDULE.exists())

    def test_apply_writes_and_removes_dropins(self):
        root = self.tmp / "systemd"
        self.cfg["gated_units"] = ["a.service", "b.service"]
        m.cmd_apply(self.cfg, root)
        p = root / "a.service.d" / m.DROPIN_NAME
        self.assertIn("ExecCondition=/usr/local/sbin/sigmond-link gate", p.read_text())
        self.cfg["gated_units"] = ["b.service"]
        m.cmd_apply(self.cfg, root)
        self.assertFalse(p.exists())
        self.cfg["enabled"] = False
        m.cmd_apply(self.cfg, root)
        self.assertEqual(list(root.glob("*.d/*")), [])



class FakeSystemd:
    """Stands in for systemctl/systemd-run: tunnel units, and an armed timer."""

    def __init__(self, units):
        self.active = dict(units)          # unit -> active
        self.calls = []
        self.deadman = None

    def systemctl(self, *args):
        self.calls.append(args)
        out = ""
        if args[0] == "list-units":
            out = "".join(f"{u} loaded {'active' if a else 'inactive'} running x\n"
                          for u, a in self.active.items())
        elif args[0] in ("start", "stop"):
            for u in args[1:]:
                if u in self.active:
                    self.active[u] = args[0] == "start"
                if u == f"{m.DEADMAN}.timer" and args[0] == "stop":
                    self.deadman = None
        return type("R", (), {"stdout": out, "returncode": 0})()

    def run(self, argv, **kw):
        assert argv[0] == "systemd-run"
        secs = int(next(a for a in argv if a.startswith("--on-active=")).split("=")[1][:-1])
        self.deadman = (secs, argv[argv.index("start") + 1:])
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()


class TestRacGuard(unittest.TestCase):
    GAP = T("2026-11-12T11:00:00")          # gap 10:29 -> 14:36

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        m.SCHEDULE = self.tmp / "link-schedule.json"
        m.RAC_STATE = self.tmp / "link-rac.json"
        m.PROBE_STATE = self.tmp / "link-probe.json"
        m.SCHEDULE.write_text(json.dumps(_doc()))
        self.sd = FakeSystemd({"sigmond-rac-host@gw2.service": True})
        self._orig = (m._systemctl, m.subprocess.run, m.frpc_gateways)
        m._systemctl = self.sd.systemctl
        m.subprocess.run = self.sd.run
        m.frpc_gateways = lambda *a: [("gw2.wsprdaemon.org", 35736)]
        self.cfg = m.read_settings(self.tmp / "absent.toml")
        self.cfg["enabled"] = True
        self.probes = []

    def tearDown(self):
        m._systemctl, m.subprocess.run, m.frpc_gateways = self._orig

    def tick(self, t, answer=False):
        def probe(h, p):
            self.probes.append((t, h))
            return answer
        m.probe_tick(self.cfg, t, probe=probe)
        return m.rac_tick(self.cfg, t)

    def up(self, t):
        return m.evaluate(m.load(), t)["up"]

    def running(self):
        return self.sd.active["sigmond-rac-host@gw2.service"]

    def test_suspends_in_a_gap_and_arms_the_dead_man_for_its_end(self):
        self.tick(self.GAP)
        self.assertFalse(self.running())
        secs, units = self.sd.deadman
        self.assertEqual(secs, int((T("2026-11-12T14:36:00") - self.GAP).total_seconds()) + 60)
        self.assertEqual(units, ["sigmond-rac-host@gw2.service"])

    def test_resumes_when_the_pass_opens(self):
        self.tick(self.GAP)
        self.tick(T("2026-11-12T14:36:30"))
        self.assertTrue(self.running())
        self.assertIsNone(self.sd.deadman)

    def test_never_suspends_inside_a_pass_or_without_a_schedule(self):
        self.tick(T("2026-11-12T16:00:00"))
        self.assertTrue(self.running())
        m.SCHEDULE.unlink()
        self.tick(self.GAP)
        self.assertTrue(self.running())

    def test_probes_every_interval_at_all_times(self):
        t0 = T("2026-11-12T16:00:00")               # inside a pass
        for i in range(0, 30):
            self.tick(t0 + dt.timedelta(minutes=i), answer=True)
        self.assertEqual([t for t, _ in self.probes],
                         [t0 + dt.timedelta(minutes=i) for i in (0, 5, 10, 15, 20, 25)])
        self.assertTrue(self.running())

    def test_an_answer_in_a_gap_opens_the_link_until_the_answers_stop(self):
        self.tick(self.GAP)
        self.assertFalse(self.running())
        self.assertFalse(self.up(self.GAP))
        t = self.GAP + dt.timedelta(minutes=5)
        self.tick(t, answer=True)                    # the gateway answers in the gap
        self.assertTrue(self.up(t))
        self.assertTrue(self.running())
        self.assertIsNone(self.sd.deadman)
        st = m.evaluate(m.load(), t)
        self.assertIn("gateway answered (gw2.wsprdaemon.org:35736)", st["reason"])
        self.tick(t + dt.timedelta(minutes=5), answer=True)   # each answer extends it
        last = t + dt.timedelta(minutes=5)
        self.tick(last + dt.timedelta(minutes=5))             # then silence
        self.tick(last + dt.timedelta(minutes=10))
        self.assertTrue(self.up(last + dt.timedelta(minutes=10)))   # 2 rounds + 1 min
        self.tick(last + dt.timedelta(minutes=11))
        self.assertFalse(self.up(last + dt.timedelta(minutes=11)))
        self.assertFalse(self.running())                      # the gap is back
        # the schedule's own windows are untouched
        self.assertEqual(m.load()["sched_windows"], _doc()["windows"])

    def test_probe_tries_gateways_in_turn_and_stops_at_the_first_answer(self):
        m.frpc_gateways = lambda *a: []
        seen = []
        m.probe_tick(self.cfg, self.GAP, probe=lambda h, p: seen.append(h) or h == "vpn2.hamsci.org")
        self.assertEqual(seen, ["vpn1.hamsci.org", "vpn2.hamsci.org"])
        self.cfg["probe_targets"] = ["[fd00::1]:7000"]
        seen.clear()
        m.probe_tick(self.cfg, self.GAP + dt.timedelta(minutes=5), probe=lambda h, p: seen.append((h, p)))
        self.assertEqual(seen, [("fd00::1", 7000)])

    def test_probe_min_zero_never_probes(self):
        self.cfg["probe_min"] = 0
        for i in range(0, 60, 5):
            self.tick(self.GAP + dt.timedelta(minutes=i), answer=True)
        self.assertEqual(self.probes, [])
        self.assertFalse(self.running())
        self.assertNotIn("probe_windows", m.load())

    def test_probe_mode_needs_no_schedule(self):
        m.SCHEDULE.unlink()
        self.cfg["mode"] = "probe"
        t0 = T("2026-11-12T16:00:00")
        self.tick(t0, answer=True)
        self.assertTrue(self.up(t0) and self.running())
        self.assertEqual(m.cmd_fetch(self.cfg, force=True), 0)   # never fetches
        for i in (5, 10):
            self.tick(t0 + dt.timedelta(minutes=i))
        self.assertTrue(self.up(t0 + dt.timedelta(minutes=10)))
        self.tick(t0 + dt.timedelta(minutes=12))
        self.assertFalse(self.up(t0 + dt.timedelta(minutes=12)))
        self.assertFalse(self.running())
        self.assertEqual(self.sd.deadman[0], 6 * 3600 + 60)    # no gap end: the ceiling
        self.tick(t0 + dt.timedelta(minutes=15), answer=True)
        self.assertTrue(self.running())
        # if the ticks stop, the horizon runs out and everything fails open
        self.assertTrue(self.up(t0 + dt.timedelta(minutes=31)))

    def test_fetch_keeps_the_probe_windows(self):
        self.tick(self.GAP + dt.timedelta(minutes=5), answer=True)
        pw = m.load()["probe_windows"]
        self.assertEqual(len(pw), 1)
        m.now = lambda: self.GAP + dt.timedelta(minutes=6)
        self.cfg["source"] = "https://example.invalid/windows.json"
        orig = m.http_get
        m.http_get = lambda url, binary=False: json.dumps(_doc())
        try:
            self.assertEqual(m.cmd_fetch(self.cfg, force=True), 0)
        finally:
            m.http_get = orig
        self.assertEqual(m.load()["probe_windows"], pw)
        self.assertTrue(self.up(self.GAP + dt.timedelta(minutes=6)))

    def test_ceiling_runs_rac_after_max_suspend_whatever_the_schedule(self):
        long_gap = {"version": 1, "fetched_at": "2026-11-12T00:00:00+00:00",
                    "horizon": ["2026-11-12T00:00:00+00:00", "2026-11-13T00:00:00+00:00"],
                    "windows": [{"start": "2026-11-12T23:00:00+00:00",
                                 "end": "2026-11-12T23:30:00+00:00", "sats": []}]}
        m.SCHEDULE.write_text(json.dumps(long_gap))
        t0 = T("2026-11-12T01:00:00")
        self.tick(t0)
        self.assertFalse(self.running())
        self.assertEqual(self.sd.deadman[0], 6 * 3600 + 60)   # capped
        self.tick(t0 + dt.timedelta(hours=6))
        self.assertTrue(self.running())
        self.tick(t0 + dt.timedelta(hours=6, minutes=29))
        self.assertTrue(self.running())                        # the 30-min hold
        self.tick(t0 + dt.timedelta(hours=6, minutes=31))
        self.assertFalse(self.running())                       # then suspends again

    def test_disabling_resumes(self):
        self.tick(self.GAP)
        self.cfg["enabled"] = False
        self.tick(self.GAP + dt.timedelta(minutes=1))
        self.assertTrue(self.running())

    def test_a_fault_starts_the_tunnels(self):
        self.tick(self.GAP)
        m.RAC_STATE.write_text('{"suspended": true, "since": "garbage", '
                               '"units": ["sigmond-rac-host@gw2.service"]}')
        orig = m.now
        m.now = lambda: self.GAP + dt.timedelta(minutes=1)
        try:
            self.assertEqual(m.cmd_rac(self.cfg), 0)
        finally:
            m.now = orig
        self.assertTrue(self.running())

    def test_no_dead_man_no_suspension(self):
        self.sd.run = lambda argv, **kw: type("R", (), {"returncode": 1})()
        m.subprocess.run = self.sd.run
        self.tick(self.GAP)
        self.assertTrue(self.running())


class TestTargets(unittest.TestCase):
    def test_frpc_configs_name_the_gateways(self):
        d = Path(tempfile.mkdtemp())
        (d / "frpc-host-gw2.toml").write_text('serverAddr = "gw2.wsprdaemon.org"\nserverPort = 35736\n')
        (d / "frpc-host.toml").write_text('serverAddr = "gw2.wsprdaemon.org"\nserverPort = 35736\n')
        (d / "frpc-host-vpn1.toml").write_text('  serverAddr = "vpn1.hamsci.org"\n  serverPort = 35736\n')
        (d / "frpc.toml.template").write_text('serverAddr = "{{X}}"\n')
        self.assertEqual(m.frpc_gateways(d), [("gw2.wsprdaemon.org", 35736), ("vpn1.hamsci.org", 35736)])

    def test_settings(self):
        d = Path(tempfile.mkdtemp())
        f = d / "link.toml"
        f.write_text('[link_schedule]\nenabled = true\nrac_probe_min = 10\n')
        self.assertEqual(m.read_settings(f)["probe_min"], 10)
        f.write_text('[link_schedule]\nenabled = true\nprobe_min = 0\nmode = "probe"\n'
                     'probe_targets = ["vpn1.hamsci.org:35736"]\n')
        c = m.read_settings(f)
        self.assertEqual((c["probe_min"], c["mode"], c["probe_targets"]),
                         (0, "probe", ["vpn1.hamsci.org:35736"]))
        f.write_text('[link_schedule]\nmode = "sometimes"\n')
        with self.assertRaises(ValueError):
            m.read_settings(f)


if __name__ == "__main__":
    unittest.main()


# ── a Proxmox host follows its VM's [link_schedule] (plan 2026-11-14 satellite-host-guard) ──

import subprocess as _sp


class TestFollowVM(unittest.TestCase):
    VM_TOPO = '[link_schedule]\nenabled = true\nmode = "probe"\nrac_units = ["vm-only@*"]\n'

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.host = self.tmp / "link.toml"
        self.host.write_text('follow_vm = 100\n[link_schedule]\nenabled = false\n'
                             'rac_units = ["sigmond-rac-host@*"]\n')
        self.cache = self.tmp / "link-vm.toml"
        self.calls = []

    def run_ok(self, text=None, rc=0):
        text = self.VM_TOPO if text is None else text

        def run(argv, **kw):
            self.calls.append(argv)
            return _sp.CompletedProcess(argv, rc, json.dumps({"exitcode": 0, "out-data": text}), "")
        return run

    def run_down(self, argv, **kw):
        self.calls.append(argv)
        return _sp.CompletedProcess(argv, 255, "", "QEMU guest agent is not running")

    def test_follow_vm_takes_vm_schedule(self):
        cfg = m.read_settings(self.host, run=self.run_ok(), cache=self.cache)
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["mode"], "probe")
        self.assertEqual(cfg["followed_vm"], 100)
        self.assertEqual(self.calls[0][:5], ["qm", "guest", "exec", "100", "--timeout"])

    def test_host_only_keys_stay_host(self):
        cfg = m.read_settings(self.host, run=self.run_ok(), cache=self.cache)
        self.assertEqual(cfg["rac_units"], ["sigmond-rac-host@*"])

    def test_vm_down_uses_cache(self):
        m.read_settings(self.host, run=self.run_ok(), cache=self.cache)
        cfg = m.read_settings(self.host, run=self.run_down, cache=self.cache, now=0, refresh_s=-1)
        self.assertTrue(cfg["enabled"])

    def test_vm_bad_toml_keeps_cache(self):
        m.read_settings(self.host, run=self.run_ok(), cache=self.cache)
        cfg = m.read_settings(self.host, run=self.run_ok("[link_schedule\nenabled="),
                              cache=self.cache, refresh_s=-1)
        self.assertTrue(cfg["enabled"])
        self.assertIn("enabled = true", self.cache.read_text())

    def test_no_vm_no_cache_disabled(self):
        cfg = m.read_settings(self.host, run=self.run_down, cache=self.cache)
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["followed_vm"], 100)

    def test_vm_without_table_is_disabled(self):
        cfg = m.read_settings(self.host, run=self.run_ok('[host]\nhostname = "x"\n'), cache=self.cache)
        self.assertFalse(cfg["enabled"])

    def test_cache_refreshed_only_after_refresh_hours(self):
        m.read_settings(self.host, run=self.run_ok(), cache=self.cache)
        m.read_settings(self.host, run=self.run_ok(), cache=self.cache)
        self.assertEqual(len(self.calls), 1)

    def test_without_follow_vm_unchanged(self):
        self.host.write_text('[link_schedule]\nenabled = true\n')
        cfg = m.read_settings(self.host, run=self.run_ok(), cache=self.cache)
        self.assertTrue(cfg["enabled"])
        self.assertNotIn("followed_vm", cfg)
        self.assertEqual(self.calls, [])


class TestFollowVMReviewFixes(TestFollowVM):
    """Final review 2026-11-14: the host's own keys and limits hold."""

    def test_vm_host_only_keys_never_leak(self):
        self.host.write_text('follow_vm = 100\n')
        cfg = m.read_settings(self.host, run=self.run_ok(), cache=self.cache)
        self.assertEqual(cfg["rac_units"], list(m.DEFAULT_RAC_UNITS))

    def test_follow_vm_inside_the_table_counts(self):
        self.host.write_text('[link_schedule]\nenabled = false\nfollow_vm = 100\n')
        cfg = m.read_settings(self.host, run=self.run_ok(), cache=self.cache)
        self.assertEqual(cfg["followed_vm"], 100)
        self.assertTrue(cfg["enabled"])

    def test_vm_cannot_lift_the_hosts_ceiling(self):
        cfg = m.read_settings(self.host, run=self.run_ok(self.VM_TOPO + 'max_suspend_hours = 60\n'),
                              cache=self.cache)
        self.assertEqual(cfg["max_suspend_hours"], 6.0)

    def test_failed_read_backs_off(self):
        m.read_settings(self.host, run=self.run_down, cache=self.cache, now=1000)
        m.read_settings(self.host, run=self.run_down, cache=self.cache, now=1100)
        self.assertEqual(len(self.calls), 1)
        m.read_settings(self.host, run=self.run_down, cache=self.cache, now=1000 + 301)
        self.assertEqual(len(self.calls), 2)

    def test_future_cache_is_not_fresh(self):
        m.read_settings(self.host, run=self.run_ok(), cache=self.cache)
        os.utime(self.cache, (4e9, 4e9))
        m.read_settings(self.host, run=self.run_ok(), cache=self.cache)
        self.assertEqual(len(self.calls), 2)

    def test_status_names_followed_vm(self):
        import contextlib, io
        cfg = m.read_settings(self.host, run=self.run_ok(), cache=self.cache)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            m.cmd_status(cfg, False)
        self.assertIn("following VM 100", out.getvalue())
