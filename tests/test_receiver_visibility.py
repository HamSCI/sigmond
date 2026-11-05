"""Task 7: `instance list` shows the radiod; `smd check` reports each receiver."""
import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "tests"))

from check_fakes import make_env, svc  # noqa: E402
from sigmond import instance, instance_report, receivers  # noqa: E402
from sigmond.commands import check  # noqa: E402
from test_instance_report import _fake_run, _inst, _load_smd  # noqa: E402


def _with_sources(client, rid, text, tmp):
    i = _inst(client, rid, src=text is not None)
    p = Path(tmp) / f"{client}@{rid}.sources.toml"
    if text is not None:
        p.write_text(text)
    i.paths.sources = p
    return i


class RadiodColumnTests(unittest.TestCase):

    def test_instance_rows_carry_the_radiod(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = _with_sources("psk-recorder", "AC0G=B1A",
                              'selected = ["radiod:ac0g-b1-a-status.local"]\n', tmp)
            b = _with_sources("psk-recorder", "AC0G=B1B", None, tmp)
            run = _fake_run({i.paths.unit_name: ("enabled", "active") for i in (a, b)})
            rows = instance_report.instance_rows([a, b], run=run)
        self.assertEqual(rows[0]["radiod"], "ac0g-b1-a-status.local")
        self.assertEqual(rows[1]["radiod"], "")

    def test_instance_list_prints_a_radiod_column(self):
        smd = _load_smd()
        with tempfile.TemporaryDirectory() as tmp:
            a = _with_sources("psk-recorder", "AC0G=B1A",
                              'selected = ["radiod:ac0g-b1-a-status.local"]\n', tmp)
            out = io.StringIO()
            with mock.patch("sigmond.instance.list_instances", return_value=[a]), \
                    contextlib.redirect_stdout(out):
                rc = smd.cmd_instance_list(argparse.Namespace(client=None, json=False))
        self.assertEqual(rc, 0)
        text = out.getvalue()
        header = [ln for ln in text.splitlines() if "REPORTER ID" in ln][0]
        self.assertLess(header.index("REPORTER ID"), header.index("RADIOD"))
        self.assertLess(header.index("RADIOD"), header.index("CONFIG"))
        self.assertIn("ac0g-b1-a-status.local", text)


def _recv(rid, radiod, **kw):
    return receivers.Receiver(reporter_id=rid, radiod=radiod, **kw)


def _env_for(states):
    """states: unit name -> Service state word; anything else is absent."""
    def service(unit):
        s = states.get(unit)
        return svc(s) if s else check.Service("absent", None, False)
    return make_env(service=service)


def _unit(client, rid):
    return instance.unit_name(client, instance.parse_user_reporter_id(rid))


CLIENTS = ("psk-recorder", "wspr-recorder")
TWO = [_recv("AC0G/B1A", "ac0g-b1-a-status.local", clients=CLIENTS),
       _recv("AC0G/B1B", "ac0g-b1-b-status.local", clients=CLIENTS)]


def _all_units():
    return [_unit(c, r.reporter_id) for r in TWO for c in CLIENTS]


def _line(states, recs=TWO):
    with mock.patch("sigmond.receivers.load_receivers", return_value=recs):
        return check.receivers_line(_env_for(states))


class ReceiversLineTests(unittest.TestCase):

    def test_receivers_line_absent_list_is_skipped(self):
        line = _line({}, recs=[])
        self.assertEqual(line.mark, check.ABSENT)

    def test_receivers_line_marks(self):
        up = {u: "active" for u in _all_units()}
        line = _line(up)
        self.assertEqual(line.mark, check.OK)
        self.assertIn("AC0G/B1A <- ac0g-b1-a: 2/2 active", line.text)
        self.assertIn("AC0G/B1B <- ac0g-b1-b: 2/2 active", line.text)

        one_down = dict(up)
        one_down[_unit("psk-recorder", "AC0G/B1B")] = "failed"
        line = _line(one_down)
        self.assertEqual(line.mark, check.WAIT)
        self.assertIn("AC0G/B1B <- ac0g-b1-b: 1/2 active (psk-recorder down)", line.text)

        line = _line({u: "active" for u in _all_units()[:2]})
        self.assertEqual(line.mark, check.FAIL)
        self.assertIn("0/2 active (psk-recorder, wspr-recorder down)", line.text)

    def test_bad_list_is_a_fail_line(self):
        with mock.patch("sigmond.receivers.load_receivers",
                        side_effect=receivers.ReceiverError("bad key")):
            line = check.receivers_line(make_env())
        self.assertEqual(line.mark, check.FAIL)
        self.assertIn("bad key", line.text)

    def test_check_json_carries_the_receivers_id(self):
        with mock.patch("sigmond.receivers.load_receivers", return_value=TWO):
            lines = check.run_checks(_env_for({}), [("receivers", check.receivers_line)])
        meta = check.CHECK_META["receivers"]
        self.assertEqual(meta[0], "receivers.instances")
        self.assertEqual(meta[1], "run")
        self.assertIn("receivers", [n for n, _ in check.CHECKS])
        self.assertEqual(lines[0].label, "receivers")


if __name__ == "__main__":
    unittest.main()
