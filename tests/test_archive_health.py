"""archive_health: SMART reading, bridge probing and verdict (design 2026-11-12, part 1)."""
import json
import subprocess
import unittest
from pathlib import Path

from sigmond import archive_health as H

FX = Path(__file__).parent / "fixtures" / "smart"


def _doc(name):
    return json.loads((FX / name).read_text())


class FakeRun:
    """smartctl stand-in: maps a -d type (None = no -d) to a JSON doc."""
    def __init__(self, by_type, stderr=""):
        self.by_type, self.calls, self.stderr = by_type, [], stderr

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        t = argv[argv.index("-d") + 1] if "-d" in argv else None
        doc = self.by_type.get(t, {"smartctl": {"exit_status": 2}})
        return subprocess.CompletedProcess(argv, doc["smartctl"]["exit_status"],
                                           json.dumps(doc), self.stderr)


class ParseTests(unittest.TestCase):
    def test_parse_ata(self):
        r = H.parse_smartctl(_doc("seagate_ok.json"), "sat")
        self.assertEqual((r.reallocated, r.pending, r.offline_uncorrectable), (0, 0, 0))
        self.assertEqual((r.power_on_hours, r.temperature_c), (1200, 34))
        self.assertEqual(r.disk_serial, "ZDZJHMBB")
        self.assertEqual(r.selftest_status, "passed")
        self.assertTrue(r.passed)
        self.assertEqual(r.dev_type, "sat")

    def test_parse_nvme(self):
        r = H.parse_smartctl(_doc("nvme_ok.json"), None)
        self.assertEqual((r.nvme_media_errors, r.nvme_percentage_used), (0, 3))
        self.assertEqual(r.power_on_hours, 800)

    def test_parse_selftest_running(self):
        r = H.parse_smartctl(_doc("selftest_running.json"), "sat")
        self.assertEqual((r.selftest_status, r.selftest_remaining_pct), ("running", 60))


class VerdictTests(unittest.TestCase):
    def test_verdict_fail_on_pending(self):
        v, why = H.verdict(H.parse_smartctl(_doc("pending.json"), "sat"))
        self.assertEqual(v, "fail")
        self.assertTrue(any("pending" in w for w in why))
        self.assertTrue(any("unreadable" in w for w in why))

    def test_verdict_healthy(self):
        self.assertEqual(H.verdict(H.parse_smartctl(_doc("seagate_ok.json"), "sat"))[0], "healthy")

    def test_verdict_warn_thresholds(self):
        d = _doc("seagate_ok.json")
        d["temperature"]["current"] = 56
        self.assertEqual(H.verdict(H.parse_smartctl(d, "sat"))[0], "warn")
        d = _doc("seagate_ok.json")
        d["power_on_time"]["hours"] = 43801
        self.assertEqual(H.verdict(H.parse_smartctl(d, "sat"))[0], "warn")
        d = _doc("nvme_ok.json")
        d["nvme_smart_health_information_log"]["percentage_used"] = 81
        self.assertEqual(H.verdict(H.parse_smartctl(d, None))[0], "warn")

    def test_verdict_failed_selftest(self):
        d = _doc("seagate_ok.json")
        d["ata_smart_self_test_log"]["standard"]["table"][0]["status"] = {
            "passed": False, "string": "Completed: read failure"}
        self.assertEqual(H.verdict(H.parse_smartctl(d, "sat"))[0], "fail")

    def test_no_smart_is_unknown(self):
        r, reason = H.probe("sdb", run=FakeRun({}))
        self.assertIsNone(r)
        v, why = H.verdict(None, reason)
        self.assertEqual(v, "unknown")
        self.assertIn(reason, why)


class ProbeTests(unittest.TestCase):
    def test_probe_order(self):
        run = FakeRun({"usbjmicron": _doc("seagate_ok.json")})
        r, _ = H.probe("sdb", run=run)
        types = [a[a.index("-d") + 1] if "-d" in a else None for a in run.calls]
        self.assertEqual(types, ["sat", None, "usbjmicron"])
        self.assertEqual(r.dev_type, "usbjmicron")

    def test_uas_blocked_reason(self):
        run = FakeRun({"sat": _doc("uas_blocked.json")})
        r, reason = H.probe("sdb", run=run, driver=lambda dev: "uas")
        self.assertIsNone(r)
        self.assertIn("blocks SMART under UAS", reason)

    def test_uas_quirk_line(self):
        self.assertEqual(H.uas_quirk_line("0bc2", "2344"), "options usb-storage quirks=0bc2:2344:u")


class BaselineTests(unittest.TestCase):
    def test_grown(self):
        base = H.parse_smartctl(_doc("seagate_ok.json"), "sat")
        d = _doc("seagate_ok.json")
        for a in d["ata_smart_attributes"]["table"]:
            if a["id"] == 5:
                a["raw"]["value"] = 3
        now = H.parse_smartctl(d, "sat")
        self.assertEqual(H.grown(base, now), ["remapped sectors 0 -> 3"])

    def test_health_doc_roundtrip(self):
        r = H.parse_smartctl(_doc("seagate_ok.json"), "sat")
        doc = H.health_doc(r, r, "2026-11-13T00:00:00Z")
        back = H.reading_from_dict(doc["baseline"])
        self.assertEqual(back, r)
        self.assertEqual(doc["latest_utc"], "2026-11-13T00:00:00Z")


class SelfTestTests(unittest.TestCase):
    def test_wait_self_test_polls_until_done(self):
        seq = [_doc("selftest_running.json"), _doc("seagate_ok.json")]
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            d = seq.pop(0) if seq else _doc("seagate_ok.json")
            return subprocess.CompletedProcess(argv, 0, json.dumps(d), "")
        out = []
        st = H.wait_self_test("sdb", "sat", run=run, sleep=lambda s: None, say=out.append)
        self.assertEqual(st, "passed")
        self.assertTrue(any("60" in line for line in out))


if __name__ == "__main__":
    unittest.main()
