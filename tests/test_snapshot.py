"""`smd admin snapshot write`: smd's JSON, wrapped in envelopes for station-web."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import snapshot  # noqa: E402
from tests.smd_tree import live_verb_parsers  # noqa: E402

NOW = 1_800_000_000.0
SMD = "/usr/local/bin/smd"


def _proc(args, rc=0, out="", err=""):
    return subprocess.CompletedProcess(args, rc, stdout=out, stderr=err)


def _fake_run(overrides=None):
    """A stand-in for subprocess.run: each command prints {"cmd": [...]},
    unless `overrides` maps its snapshot name to a callable or a result."""
    overrides = overrides or {}
    by_args = {tuple(a): n for n, a in snapshot.SNAPSHOT_COMMANDS.items()}
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        assert cmd[0] == SMD
        name = by_args[tuple(cmd[1:])]
        if name in overrides:
            o = overrides[name]
            return o(cmd) if callable(o) else o
        return _proc(cmd, out=json.dumps({"cmd": cmd[1:]}))

    run.calls = calls
    return run


class WriteSnapshotsTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "snapshot"
        self.out.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, run, names=None):
        names = list(snapshot.SNAPSHOT_COMMANDS) if names is None else names
        return snapshot.write_snapshots(names, self.out, run=run,
                                        now=lambda: NOW, smd=SMD)

    def _read(self, name):
        return json.loads((self.out / f"{name}.json").read_text())

    def test_the_command_table_is_exactly_the_planned_one(self):
        self.assertEqual(snapshot.SNAPSHOT_COMMANDS, {
            "check": ["check", "--json"],
            "components": ["component", "list", "--json", "--no-fetch"],
            "catalog": ["component", "list", "--catalog", "--json"],
            "instances": ["admin", "instance", "list", "--json"],
            "validate": ["admin", "validate", "--json"],
            "viewers": ["admin", "viewers", "--json"],
            "manifest": ["admin", "manifest", "status", "--json"],
            "heartbeat": ["admin", "heartbeat", "show", "--json"],
        })

    def test_each_snapshot_gets_an_envelope(self):
        run = _fake_run()
        result = self._write(run)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         sorted(f"{n}.json" for n in snapshot.SNAPSHOT_COMMANDS))
        for name, args in snapshot.SNAPSHOT_COMMANDS.items():
            env = self._read(name)
            self.assertEqual(set(env), {"name", "command", "ran_at", "exit",
                                        "data", "error"})
            self.assertEqual(env["name"], name)
            self.assertEqual(env["command"], "smd " + " ".join(args))
            self.assertEqual(env["ran_at"], "2027-01-15T08:00:00+00:00")
            self.assertEqual(env["exit"], 0)
            self.assertEqual(env["data"], {"cmd": args})
            self.assertIsNone(env["error"])
            self.assertEqual(result[name], env)
        # Every command ran with a timeout and captured, text output.
        for _cmd, kwargs in run.calls:
            self.assertEqual(kwargs.get("timeout"), 60)
            self.assertTrue(kwargs.get("capture_output"))
            self.assertTrue(kwargs.get("text"))

    def test_bad_output_is_recorded_and_others_still_written(self):
        run = _fake_run({"viewers": lambda cmd: _proc(cmd, out="oops")})
        self._write(run)
        env = self._read("viewers")
        self.assertEqual(env["exit"], 0)
        self.assertIsNone(env["data"])
        self.assertEqual(env["error"], "output was not JSON")
        others = [n for n in snapshot.SNAPSHOT_COMMANDS if n != "viewers"]
        self.assertEqual(len(others), 7)
        for name in others:
            self.assertEqual(self._read(name)["data"]["cmd"],
                             snapshot.SNAPSHOT_COMMANDS[name])

    def test_check_exit_two_keeps_its_data(self):
        lines = {"verdict": "FAIL", "lines": [{"mark": "!!"}]}
        run = _fake_run({"check": lambda cmd: _proc(cmd, rc=2,
                                                    out=json.dumps(lines))})
        self._write(run, ["check"])
        env = self._read("check")
        self.assertEqual(env["exit"], 2)
        self.assertEqual(env["data"], lines)
        self.assertIsNone(env["error"])

    def test_a_failed_command_without_json_carries_its_stderr(self):
        # `smd admin heartbeat show --json` exits 1 when no tick exists.
        run = _fake_run({"heartbeat": lambda cmd: _proc(
            cmd, rc=1, err="  \x1b[31m\u2717\x1b[0m  admin heartbeat show: "
                     "no heartbeat ticks in /var/lib/sigmond/heartbeat\n")})
        self._write(run, ["heartbeat"])
        env = self._read("heartbeat")
        self.assertEqual(env["exit"], 1)
        self.assertIsNone(env["data"])
        self.assertEqual(env["error"],
                         "admin heartbeat show: no heartbeat ticks in "
                         "/var/lib/sigmond/heartbeat")

    def test_timeout_is_recorded(self):
        def hang(cmd):
            raise subprocess.TimeoutExpired(cmd, 60)
        self._write(_fake_run({"catalog": hang}))
        env = self._read("catalog")
        self.assertEqual(env["exit"], -1)
        self.assertIsNone(env["data"])
        self.assertEqual(env["error"], "timed out")
        self.assertEqual(self._read("check")["exit"], 0)

    def test_a_command_that_cannot_start_is_recorded(self):
        def missing(cmd):
            raise FileNotFoundError(2, "No such file or directory", cmd[0])
        self._write(_fake_run({"check": missing}), ["check"])
        env = self._read("check")
        self.assertEqual(env["exit"], -1)
        self.assertIsNone(env["data"])
        self.assertIn("could not run", env["error"])

    def test_files_are_0640_and_written_atomically(self):
        old = os.umask(0o022)
        try:
            with mock.patch.object(snapshot.os, "replace",
                                   wraps=os.replace) as replace:
                self._write(_fake_run())
        finally:
            os.umask(old)
        self.assertEqual(len(replace.call_args_list), 8)
        for call in replace.call_args_list:
            src, dst = (Path(a) for a in call.args)
            self.assertEqual(src.parent, self.out)
            self.assertEqual(dst.parent, self.out)
            self.assertNotEqual(src, dst)
        for p in self.out.iterdir():
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o640, p.name)
        # No temp file left behind.
        self.assertEqual(len(list(self.out.iterdir())), 8)

    def test_an_unknown_name_is_refused(self):
        with self.assertRaises(KeyError):
            self._write(_fake_run(), ["nope"])

    def test_the_real_subprocess_run_is_resolved_at_call_time(self):
        with mock.patch.object(snapshot.subprocess, "run",
                               _fake_run()) as run:
            snapshot.write_snapshots(["check"], self.out,
                                     now=lambda: NOW, smd=SMD)
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(self._read("check")["exit"], 0)


class RequestTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.req = Path(self._tmp.name) / "snapshot-request"
        self.req.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _ask(self, tag):
        (self.req / tag).touch()

    def test_requests_are_consumed_and_rate_limited(self):
        last = 0.0
        refreshes = []
        for t, tag in ((NOW, "a"), (NOW + 10, "b"), (NOW + 50, "c")):
            self._ask(tag)
            if snapshot.take_requests(self.req, last, t):
                last = t
                refreshes.append(t)
            self.assertEqual(list(self.req.iterdir()), [])
        self.assertEqual(refreshes, [NOW, NOW + 50])

    def test_no_request_means_no_refresh(self):
        self.assertFalse(snapshot.take_requests(self.req, 0.0, NOW))

    def test_a_missing_request_dir_means_no_refresh(self):
        self.assertFalse(snapshot.take_requests(self.req / "gone", 0.0, NOW))

    def test_write_requested_keeps_last_run_and_writes_check_only(self):
        out = Path(self._tmp.name) / "snapshot"
        out.mkdir()
        run = _fake_run()
        self._ask("a")
        self.assertTrue(snapshot.write_requested(
            self.req, out, run=run, now=lambda: NOW, smd=SMD))
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         [".last-request", "check.json"])
        self.assertEqual(float((out / ".last-request").read_text()), NOW)
        self._ask("b")
        self.assertFalse(snapshot.write_requested(
            self.req, out, run=run, now=lambda: NOW + 10, smd=SMD))
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(list(self.req.iterdir()), [])
        self._ask("c")
        self.assertTrue(snapshot.write_requested(
            self.req, out, run=run, now=lambda: NOW + 40, smd=SMD))
        self.assertEqual(len(run.calls), 2)


class CommandParityTests(unittest.TestCase):

    def test_every_snapshot_command_exists_in_smd(self):
        parsers = live_verb_parsers()
        for name, args in snapshot.SNAPSHOT_COMMANDS.items():
            verb = " ".join(a for a in args if not a.startswith("--"))
            self.assertTrue(verb in parsers, f"{name}: no `smd {verb}`")
            flags = parsers[verb]._option_string_actions
            for flag in (a for a in args if a.startswith("--")):
                self.assertTrue(flag in flags, f"{name}: `smd {verb}` has no {flag}")

    def test_the_snapshot_verb_itself_exists(self):
        parsers = live_verb_parsers()
        self.assertTrue("admin snapshot write" in parsers)
        flags = parsers["admin snapshot write"]._option_string_actions
        self.assertIn("--only", flags)
        self.assertIn("--request", flags)


if __name__ == "__main__":
    unittest.main()
