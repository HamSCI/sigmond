"""`smd admin instance list --json`: file facts plus unit enabled/active."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import instance_report  # noqa: E402


def _inst(client, rid, cfg=True, env=True, src=False):
    unit = f"{client}@{rid.replace('=', chr(92) + 'x3d')}.service"
    return SimpleNamespace(
        paths=SimpleNamespace(unit_name=unit), client=client,
        reporter_id=rid, has_config=cfg, has_env=env, has_sources=src)


def _fake_run(states):
    def run(argv, **kw):
        verb, unit = argv[1], argv[2]
        word = states[unit][0 if verb == "is-enabled" else 1]
        return subprocess.CompletedProcess(argv, 0 if word == "active" else 3,
                                           stdout=word + "\n", stderr="")
    return run


class InstanceReportTests(unittest.TestCase):

    def test_rows_carry_unit_state(self):
        a = _inst("psk-recorder", "AC0G=B4")
        b = _inst("wspr-recorder", "AC0G=ND", src=True)
        run = _fake_run({a.paths.unit_name: ("enabled", "active"),
                         b.paths.unit_name: ("disabled", "inactive")})
        rows = instance_report.instance_rows([a, b], run=run)
        self.assertEqual(rows[0], {
            "client": "psk-recorder", "reporter_id": "AC0G=B4",
            "display_id": "AC0G/B4", "unit": a.paths.unit_name,
            "has_config": True, "has_env": True, "has_sources": False,
            "enabled": "enabled", "active": "active"})
        self.assertEqual((rows[1]["enabled"], rows[1]["active"]),
                         ("disabled", "inactive"))
        self.assertTrue(rows[1]["has_sources"])

    def test_systemctl_failure_reads_unknown(self):
        def run(argv, **kw):
            raise OSError("no systemctl")
        rows = instance_report.instance_rows([_inst("psk-recorder", "AC0G=B4")],
                                             run=run)
        self.assertEqual((rows[0]["enabled"], rows[0]["active"]),
                         ("unknown", "unknown"))

    def test_timeout_and_empty_output_read_unknown(self):
        def hang(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 5)
        def empty(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
        for run in (hang, empty):
            r = instance_report.instance_rows([_inst("c", "AC0G=B4")], run=run)[0]
            self.assertEqual((r["enabled"], r["active"]), ("unknown", "unknown"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_instance_report", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CliJsonTests(unittest.TestCase):

    def test_cli_json(self):
        smd = _load_smd()
        a = _inst("psk-recorder", "AC0G=B4")
        out = io.StringIO()
        with mock.patch("sigmond.instance.list_instances", return_value=[a]), \
                mock.patch("subprocess.run", _fake_run(
                    {a.paths.unit_name: ("enabled", "active")})), \
                contextlib.redirect_stdout(out):
            rc = smd.cmd_instance_list(argparse.Namespace(client=None, json=True))
        self.assertEqual(rc, 0)
        rows = json.loads(out.getvalue())["instances"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["client"], rows[0]["enabled"], rows[0]["active"]),
                         ("psk-recorder", "enabled", "active"))


if __name__ == "__main__":
    unittest.main()
