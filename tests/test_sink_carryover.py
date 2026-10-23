"""The carry-over: ``[uploads]`` into the switch files, once, marker last
(spec §6, test 16; plan Rulings 8, 11, 12; V8; D12).

Each station class of spec §6.6 has a tree under
``tests/fixtures/carryover/<class>/``: ``etc/`` (coordination, topology,
instance env files, PSWS configs), ``deploy/<client>/deploy.toml``,
an optional ``sink.sql`` (rows for ``sink.db``) and ``expect.toml`` (what
§6.6 predicts).

Every switch file and the marker are written by the branch hs-uploader
itself, ``../hs-uploader/.venv/bin/python -I -m hs_uploader.cli``, through
a runner that records each call (global constraint 4: real output).  The
lever step runs against a fake ``systemctl`` (the one test_sink_levers
uses), so no real unit is touched.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sigmond import inprocess_senders as ips
from sigmond import sink_carryover as co
from sigmond import sink_state as ss
from sigmond import uploader_manifest as um
from sigmond.hamsci_sink import _bundled

from test_sink_levers import FakeSystemctl

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "carryover"
CLASSES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
REAL_PY = (Path(__file__).resolve().parent.parent.parent
           / "hs-uploader" / ".venv" / "bin" / "python")
RADIOD = "radiod@rx888.service"
GOVERNED = ("hamsci-physics", "mag-recorder", "meteor-scatter",
            "psk-recorder", "wspr-recorder")

needs_real = unittest.skipUnless(REAL_PY.exists(),
                                 f"no hs-uploader checkout at {REAL_PY}")


# ---------------------------------------------------------------------------
# scaffolding
# ---------------------------------------------------------------------------

class Runner:
    """Runs the branch hs-uploader, recording every call in ``log``.

    ``fail_on(args)`` true makes that call fail (exit 1) without running."""

    def __init__(self, log=None, fail_on=None):
        self.calls = []
        self.log = log if log is not None else []
        self.fail_on = fail_on

    def __call__(self, args, stdin=None):
        args = list(args)
        self.calls.append((args, stdin))
        self.log.append(("cli",) + tuple(args[1:3] if args[1] == "set"
                                         else args[1:2]))
        if self.fail_on and self.fail_on(args):
            return subprocess.CompletedProcess(args, 1, "", "injected failure")
        return um.run_hs_uploader(args, stdin=stdin, python=REAL_PY, timeout=30)

    def sets(self):
        return [a[2] for a, _ in self.calls if a[:2] == ["sink", "set"]]


def build_sink(path: Path, sql: str = "", *, producer_column=True):
    con = sqlite3.connect(path)
    ddl = _bundled.PENDING_UPLOADS_DDL if producer_column else (
        "CREATE TABLE pending_uploads (id INTEGER PRIMARY KEY, target_db TEXT,"
        " target_table TEXT, schema_version INTEGER DEFAULT 0,"
        " payload_json TEXT, queued_at TEXT)")
    con.executescript(ddl)
    if sql:
        con.executescript(sql)
    con.commit()
    con.close()


class Station:
    """A scratch copy of one fixture tree (or an empty one), its roots, a
    fake systemctl with one running unit per instance env file, and the
    lever step's instance enumeration pointed at those units."""

    def __init__(self, test, fixture=None):
        self.tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.tmp, True)
        if fixture:
            src = FIXTURES / fixture
            for part in ("etc", "deploy"):
                if (src / part).exists():
                    shutil.copytree(src / part, self.tmp / part)
            self.expect = (tomllib.loads((src / "expect.toml").read_text())
                           if (src / "expect.toml").exists() else {})
            sql = src / "sink.sql"
            if sql.exists():
                build_sink(self.tmp / "sink.db", sql.read_text())
        (self.tmp / "etc" / "sigmond").mkdir(parents=True, exist_ok=True)
        (self.tmp / "deploy").mkdir(exist_ok=True)
        (self.tmp / "state").mkdir()
        self.writer = self.tmp / "writer.py"
        self.writer.write_text("# writer\n")
        os.utime(self.writer, (3000, 3000))
        self.roots = co.CarryRoots(
            etc=self.tmp / "etc", sinks=self.tmp / "etc" / "hs-uploader" / "sinks",
            marker=self.tmp / "state" / ss.MARKER_NAME,
            sink_db=self.tmp / "sink.db", deploy_dir=self.tmp / "deploy",
            writer_py=self.writer, python=REAL_PY)
        self.log = []
        self.units = {RADIOD: {"ActiveState": "active",
                               "ActiveEnterTimestamp": "@1000",
                               "ActiveEnterTimestampMonotonic": "1000000000"}}
        self.sc = FakeSystemctl(self.units)
        self.sc.on_restart = lambda unit: self.log.append(("restart", unit))
        self.refresh_units()
        p = mock.patch.object(ips, "_instance_units",
                              side_effect=self._instance_units)
        p.start()
        test.addCleanup(p.stop)

    # -- tree editing --------------------------------------------------------
    def write(self, rel, text):
        f = self.tmp / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return f

    def coordination(self, text):
        self.write("etc/sigmond/coordination.toml", text)

    def topology(self, *enabled):
        body = "".join(f"[component.{c}]\nenabled = true\n\n" for c in enabled)
        self.write("etc/sigmond/topology.toml", body)

    def deploy(self, client, *pipelines):
        body = "".join(f'[[hs_uploader.pipeline]]\nname = "{p}"\n\n'
                       for p in pipelines) or '[package]\nname = "x"\n'
        self.write(f"deploy/{client}/deploy.toml", body)

    def env(self, client, rid, text):
        f = self.write(f"etc/{client}/env/{rid}.env", text)
        self.refresh_units()
        return f

    def refresh_units(self):
        """One running unit per env file, started after the file and after
        the writer (so nothing is stale unless a test says so)."""
        for f in sorted(self.tmp.glob("etc/*/env/*.env")):
            os.utime(f, (4000, 4000))
            unit = f"{f.parent.parent.name}@{f.stem}.service"
            self.units.setdefault(unit, {
                "env_files": [str(f)], "ActiveState": "active",
                "ActiveEnterTimestamp": "@5000",
                "ActiveEnterTimestampMonotonic": str(5000 * 10**6)})

    def _instance_units(self, client, topology):
        return sorted(u for u in self.units
                      if u.startswith(client + "@"))

    # -- running -------------------------------------------------------------
    def plan(self):
        return co.plan(self.roots)

    def run(self, runner=None):
        runner = runner or Runner(self.log)
        p = self.plan()
        return p, co.apply(p, runner=runner, systemctl=self.sc), runner

    # -- reading -------------------------------------------------------------
    def switch(self, name):
        return tomllib.loads((self.roots.sinks / f"{name}.toml").read_text())

    def files(self):
        d = self.roots.sinks
        return sorted(p.stem for p in d.glob("*.toml")) if d.exists() else []

    def snapshot(self):
        return {p.name: p.read_bytes() for p in sorted(self.roots.sinks.glob("*.toml"))}


def basic(st, uploads="[uploads]\nenabled = true\n", enabled=("wspr-recorder",
          "psk-recorder"), **_):
    """A small station: the five governed clients declared, hf-timestd with
    no outbound route, the given clients enabled."""
    st.coordination('[host]\ncall = "AC0G"\ngrid = "EM38ww"\nlat = 38.92\n'
                    'lon = -92.33\n\n' + uploads)
    st.topology(*enabled)
    st.deploy("wspr-recorder", "wspr-wsprdaemon", "wspr-wsprnet")
    st.deploy("psk-recorder", "psk-pskreporter")
    st.deploy("meteor-scatter", "psk-pskreporter")
    st.deploy("mag-recorder", "mag-psws")
    st.deploy("hamsci-physics", "grape-psws")
    st.deploy("hf-timestd")


def states(plan):
    return {w.name: w.state for w in plan.clients}


# ---------------------------------------------------------------------------
# §6.3: one case per row of each table (plan only)
# ---------------------------------------------------------------------------

class SiteTable(unittest.TestCase):

    def site(self, uploads):
        st = Station(self)
        basic(st, uploads)
        return st.plan()

    def test_mode_discard_reads_off(self):
        p = self.site('[uploads]\nenabled = false\nmode = "discard"\n')
        self.assertEqual(p.site.state, "off")
        self.assertEqual(p.site.reason, "carried over from [uploads] mode = discard")

    def test_enabled_false_without_mode_reads_fill(self):
        p = self.site("[uploads]\nenabled = false\n")
        self.assertEqual(p.site.state, "fill")
        self.assertEqual(p.site.reason, "carried over from [uploads] mode = hold")

    def test_mode_hold_reads_fill(self):
        self.assertEqual(self.site('[uploads]\nmode = "hold"\n').site.state, "fill")

    def test_enabled_true_reads_upload(self):
        p = self.site("[uploads]\nenabled = true\n")
        self.assertEqual(p.site.state, "upload")
        self.assertEqual(p.site.reason, "carried over from [uploads] mode = upload")

    def test_no_block_reads_upload(self):
        self.assertEqual(self.site("").site.state, "upload")


class ClientTable(unittest.TestCase):

    def setUp(self):
        self.st = Station(self)
        basic(self.st)
        self.st.env("wspr-recorder", "AC0G=S", "WSPR_USE_HS_UPLOADER=1\n")
        self.st.env("psk-recorder", "AC0G=S", "PSK_DELIVERY_PIPELINES=direct\n")

    def test_otherwise_upload(self):
        p = self.st.plan()
        self.assertEqual(states(p), {"psk-recorder": "upload",
                                     "wspr-recorder": "upload"})
        for w in p.clients:
            self.assertEqual(w.reason, "carried over from [uploads] mode = upload")

    def test_psk_flag_reads_fill(self):
        self.st.env("psk-recorder", "AC0G=T", "PSK_USE_HS_UPLOADER=0\n")
        w = {w.name: w for w in self.st.plan().clients}["psk-recorder"]
        self.assertEqual(w.state, "fill")
        self.assertEqual(w.reason, "carried over from PSK_USE_HS_UPLOADER=0")

    def test_meteor_flag_reads_fill(self):
        self.st.topology("wspr-recorder", "psk-recorder", "meteor-scatter")
        self.st.env("meteor-scatter", "AC0G=S",
                    "METEOR_SCATTER_USE_HS_UPLOADER=0\n")
        w = {w.name: w for w in self.st.plan().clients}["meteor-scatter"]
        self.assertEqual(w.state, "fill")
        self.assertEqual(w.reason,
                         "carried over from METEOR_SCATTER_USE_HS_UPLOADER=0")

    def test_wspr_flag_ignored(self):
        """WSPR_USE_HS_UPLOADER=0 marks a decode-only role (design §10.1).
        Positive control: the same harness reads PSK_USE_HS_UPLOADER=0."""
        self.st.env("wspr-recorder", "AC0G=S", "WSPR_USE_HS_UPLOADER=0\n")
        self.st.env("psk-recorder", "AC0G=S", "PSK_USE_HS_UPLOADER=0\n")
        s = states(self.st.plan())
        self.assertEqual(s["wspr-recorder"], "upload")
        self.assertEqual(s["psk-recorder"], "fill")

    def test_governed_not_enabled_with_rows_reads_fill(self):
        """meteor-scatter is off in topology, but sink.db holds an MSK144 row
        stored with producer '' (D12 infers meteor-scatter)."""
        build_sink(self.st.roots.sink_db,
                   "INSERT INTO pending_uploads(target_db,target_table,"
                   "payload_json,queued_at,producer) VALUES ('psk','spots',"
                   "'{\"mode\":\"MSK144\"}','t','');")
        w = {w.name: w for w in self.st.plan().clients}["meteor-scatter"]
        self.assertEqual(w.state, "fill")
        self.assertEqual(w.reason, "carried over: not enabled in topology.toml, "
                                   "with rows in sink.db")

    def test_governed_not_enabled_without_rows_gets_no_file(self):
        self.assertNotIn("meteor-scatter", states(self.st.plan()))

    def test_no_outbound_route_gets_no_file(self):
        """hf-timestd (enabled, rows) and codar-sounder (rows, no deploy.toml)
        declare no pipeline.  Control: wspr-recorder gets its file."""
        self.st.topology("wspr-recorder", "psk-recorder", "hf-timestd")
        build_sink(self.st.roots.sink_db,
                   "INSERT INTO pending_uploads(target_db,target_table,"
                   "payload_json,queued_at,producer) VALUES "
                   "('timestd','events','{}','t',''),"
                   "('codar','x','{}','t','codar-sounder');")
        s = states(self.st.plan())
        self.assertNotIn("hf-timestd", s)
        self.assertNotIn("codar-sounder", s)
        self.assertIn("wspr-recorder", s)

    def test_mag_without_psws_id_still_upload(self):
        self.st.topology("mag-recorder")
        self.st.write("etc/mag-recorder/mag-recorder-config.toml",
                      '[station]\npsws_station_id = "<YOUR_STATION>"\n')
        w = {w.name: w for w in self.st.plan().clients}["mag-recorder"]
        self.assertEqual(w.state, "upload")
        self.assertNotIn("psws", w.identity["mag-recorder"])

    def test_mag_with_psws_id_records_it(self):
        self.st.topology("mag-recorder")
        self.st.write("etc/mag-recorder/mag-recorder-config.toml",
                      '[station]\npsws_station_id = "S000171"\n'
                      'instrument_id = "MAG1"\n')
        w = {w.name: w for w in self.st.plan().clients}["mag-recorder"]
        self.assertEqual(w.identity, {"mag-recorder": {
            "callsign": "AC0G", "grid": "EM38ww", "lat": 38.92, "lon": -92.33,
            "psws": {"instrument": "MAG1", "station": "S000171"}}})

    def test_identity_one_table_per_instance(self):
        self.st.env("psk-recorder", "AC0G=T", "PSK_DELIVERY_PIPELINES=direct\n")
        w = {w.name: w for w in self.st.plan().clients}["psk-recorder"]
        self.assertEqual(w.identity, {
            "AC0G=S": {"callsign": "AC0G/S", "reporter_id": "AC0G/S",
                       "grid": "EM38ww", "lat": 38.92, "lon": -92.33},
            "AC0G=T": {"callsign": "AC0G/T", "reporter_id": "AC0G/T",
                       "grid": "EM38ww", "lat": 38.92, "lon": -92.33}})

    def test_plan_prints_before_and_after(self):
        self.st.env("psk-recorder", "AC0G=T", "PSK_USE_HS_UPLOADER=0\n")
        text = "\n".join(self.st.plan().lines())
        self.assertIn("carry-over: site missing -> upload (carried over from "
                      "[uploads] mode = upload)", text)
        self.assertIn("carry-over: psk-recorder missing -> fill (carried over "
                      "from PSK_USE_HS_UPLOADER=0)", text)
        self.assertIn("carry-over: wspr-recorder missing -> upload", text)


# ---------------------------------------------------------------------------
# §6.6: one tree per station class, end to end
# ---------------------------------------------------------------------------

@needs_real
class StationClasses(unittest.TestCase):

    def test_classes_present(self):
        self.assertEqual(CLASSES, ["b4", "fresh", "k3lr", "legacy", "nd"])

    def check(self, name):
        st = Station(self, name)
        exp = st.expect
        p, res, _ = st.run()
        self.assertTrue(res.ok, res.lines())
        self.assertEqual(p.site.state, exp["site"])
        self.assertEqual(states(p), exp["clients"])
        self.assertEqual(list(p.below), exp["below"])
        self.assertEqual(p.to_off, exp["to_off"])
        for c in exp["no_file"]:
            self.assertNotIn(c, st.files())
        self.assertEqual(st.files(), sorted(["site", *exp["clients"]]))
        self.assertEqual(st.switch("site")["state"], exp["site"])
        for c, state in exp["clients"].items():
            f = st.switch(c)
            self.assertEqual(f["state"], state)
            self.assertEqual(len(f["history"]), 1)
            self.assertEqual(f["history"][0]["by"], "carry-over")
        self.assertEqual(st.sc.restarts(), exp["restarted"])
        self.assertNotIn(RADIOD, st.sc.restarts())
        self.assertTrue(st.roots.marker.exists())
        return st, p, res

    def test_b4(self):
        st, p, res = self.check("b4")
        self.assertEqual(st.sc.verbs(), [])          # no lever, no restart
        ident = st.switch("mag-recorder")["history"][0]["identity"]
        self.assertEqual(ident["mag-recorder"]["psws"],
                         {"station": "S000171", "instrument": "MAG1"})

    def test_fresh(self):
        st, p, res = self.check("fresh")
        env = (st.tmp / "etc/psk-recorder/env/N0CALL.env").read_text()
        self.assertIn("PSK_DELIVERY_PIPELINES=server-raw", env)
        mag = tomllib.loads((st.tmp / "etc/mag-recorder/"
                             "mag-recorder-config.toml").read_text())
        self.assertIs(mag["uploader"]["enabled"], False)

    def test_legacy(self):
        st, p, res = self.check("legacy")
        self.assertEqual(st.sc.verbs(), [])

    def test_nd(self):
        st, p, res = self.check("nd")
        ident = st.switch("hamsci-physics")["history"][0]["identity"]
        self.assertNotIn("psws", ident["hamsci-physics"])

    def test_k3lr(self):
        """Site fill, psk-recorder fill; the lever moves each psk instance to
        server-raw and restarts each alone, by systemctl restart <unit>."""
        st, p, res = self.check("k3lr")
        for rid in ("K3LR", "K3LR=2"):
            env = (st.tmp / f"etc/psk-recorder/env/{rid}.env").read_text()
            self.assertIn("PSK_DELIVERY_PIPELINES=server-raw", env)
        self.assertEqual([c for c in st.sc.verbs()],
                         [["restart", "psk-recorder@K3LR.service"],
                          ["restart", "psk-recorder@K3LR=2.service"]])
        psk = st.switch("psk-recorder")["history"][0]
        self.assertEqual(psk["reason"], "carried over from PSK_USE_HS_UPLOADER=0")
        self.assertEqual(psk["identity"], {
            "K3LR": {"callsign": "K3LR", "reporter_id": "K3LR", "grid": "EN91"},
            "K3LR=2": {"callsign": "K3LR/2", "reporter_id": "K3LR/2",
                       "grid": "EN91"}})
        site = st.switch("site")
        self.assertEqual(site["history"][0]["reason"],
                         "carried over from [uploads] mode = hold")
        self.assertNotIn("identity", site["history"][0])


# ---------------------------------------------------------------------------
# the calls: identity on stdin, order, marker last
# ---------------------------------------------------------------------------

@needs_real
class Calls(unittest.TestCase):

    def test_identity_passes_on_stdin(self):
        """Ruling 11: the identity tables go on stdin, never in a file, as
        deterministic JSON (sorted keys)."""
        st = Station(self, "k3lr")
        _, res, runner = st.run()
        (args, stdin), = [(a, s) for a, s in runner.calls
                          if a[:3] == ["sink", "set", "psk-recorder"]]
        self.assertEqual(args[args.index("--identity-json") + 1], "-")
        self.assertEqual(stdin, json.dumps(json.loads(stdin), sort_keys=True))
        self.assertEqual(set(json.loads(stdin)), {"K3LR", "K3LR=2"})
        site_args, site_stdin = runner.calls[0]
        self.assertEqual(site_args[:3], ["sink", "set", "site"])
        self.assertNotIn("--identity-json", site_args)
        self.assertIsNone(site_stdin)

    def test_order_site_clients_levers_stale_marker(self):
        """The order of §6.4: site, then each client, then the lever step,
        then the stale-writer restarts (some state reads off), then the
        marker last."""
        st = Station(self, "fresh")
        st.env("meteor-scatter", "N0CALL", "METEOR_SCATTER_DELIVERY_MODE=off\n")
        st.topology("wspr-recorder", "psk-recorder", "mag-recorder",
                    "hamsci-physics", "hf-timestd", "meteor-scatter")
        # the writer changed after every instance started (5000), after the
        # env files (4000): meteor-scatter, which the lever leaves alone, is
        # a stale writer and only the stale-writer pass restarts it
        os.utime(st.writer, (6000, 6000))
        _, res, _ = st.run()
        self.assertTrue(res.ok, res.lines())
        self.assertEqual(st.log, [
            ("cli", "set", "site"),
            ("cli", "set", "hamsci-physics"),
            ("cli", "set", "mag-recorder"),
            ("cli", "set", "meteor-scatter"),
            ("cli", "set", "psk-recorder"),
            ("cli", "set", "wspr-recorder"),
            ("restart", "psk-recorder@N0CALL.service"),
            ("restart", "wspr-recorder@N0CALL.service"),
            ("restart", "meteor-scatter@N0CALL.service"),
            ("cli", "mark-carried"),
        ])
        self.assertIn("stale writer: restarted meteor-scatter@N0CALL.service",
                      res.lines())

    def test_no_stale_restart_without_off(self):
        """K3LR reads fill, never off: no stale-writer pass.  Control: the
        same stale unit restarts under a site that reads off (above)."""
        st = Station(self, "k3lr")
        os.utime(st.writer, (6000, 6000))     # every instance is stale
        _, res, _ = st.run()
        self.assertTrue(res.ok, res.lines())
        self.assertNotIn("wspr-recorder@K3LR.service", st.sc.restarts())

    def test_marker_holds_the_files_written(self):
        st = Station(self, "legacy")
        st.run()
        text = st.roots.marker.read_text()
        self.assertTrue(text.startswith("carried over at "))
        self.assertIn("carry-over wrote: site psk-recorder wspr-recorder", text)


# ---------------------------------------------------------------------------
# idempotence, failures, rerun after a rollback, late clients
# ---------------------------------------------------------------------------

@needs_real
class Idempotence(unittest.TestCase):

    def test_second_run_with_marker_writes_nothing(self):
        st = Station(self, "k3lr")
        _, first, r1 = st.run()
        self.assertTrue(first.ok)
        self.assertGreater(len(r1.calls), 0)          # control: run 1 wrote
        before = st.snapshot()
        marker = st.roots.marker.read_bytes()
        n = len(st.sc.verbs())
        p, res, r2 = st.run()
        self.assertTrue(p.marker_stands)
        self.assertEqual(r2.calls, [])
        self.assertEqual(st.sc.verbs()[n:], [])
        self.assertEqual(st.snapshot(), before)
        self.assertEqual(st.roots.marker.read_bytes(), marker)
        self.assertTrue(res.ok)

    def test_lever_failure_leaves_no_marker_then_completes(self):
        st = Station(self, "k3lr")
        env = st.tmp / "etc/psk-recorder/env/K3LR=2.env"
        real = st.tmp / "elsewhere.env"
        real.write_text(env.read_text())
        env.unlink()
        env.symlink_to(real)                 # set_env_flag refuses a symlink
        _, res, r1 = st.run()
        self.assertFalse(res.ok)
        self.assertEqual(res.failed[0], "lever step")
        # the write failure itself, named with its file, not only verify's
        # later "still armed"
        self.assertIn("K3LR=2.env: not written", res.failed[1])
        self.assertFalse(st.roots.marker.exists())
        self.assertEqual(res.written, ["site", "psk-recorder", "wspr-recorder"])
        self.assertTrue(any("FAILED" in ln for ln in res.lines()))
        # the operator fixes the file; the second run writes only the marker
        env.unlink()
        shutil.copy(real, env)
        os.utime(env, (4000, 4000))
        files = st.snapshot()
        _, res2, r2 = st.run()
        self.assertTrue(res2.ok, res2.lines())
        self.assertEqual(r2.sets(), [])
        self.assertEqual([a[1] for a, _ in r2.calls], ["mark-carried"])
        self.assertEqual(st.snapshot(), files)
        self.assertTrue(st.roots.marker.exists())
        # a third run, marker standing, does nothing at all
        _, res3, r3 = st.run()
        self.assertEqual(r3.calls, [])

    def test_failed_cli_call_leaves_no_marker_then_writes_only_missing(self):
        st = Station(self, "b4")
        runner = Runner(st.log, fail_on=lambda a: a[:3] == ["sink", "set",
                                                            "meteor-scatter"])
        _, res, _ = st.run(runner)
        self.assertFalse(res.ok)
        self.assertEqual(res.failed[0], "hs-uploader sink set meteor-scatter")
        self.assertIn("injected failure", res.failed[1])
        self.assertEqual(res.written, ["site", "hamsci-physics", "mag-recorder"])
        self.assertFalse(st.roots.marker.exists())
        self.assertEqual(st.files(), ["hamsci-physics", "mag-recorder", "site"])
        before = st.snapshot()
        _, res2, r2 = st.run()
        self.assertTrue(res2.ok, res2.lines())
        self.assertEqual(r2.sets(), ["meteor-scatter", "psk-recorder",
                                     "wspr-recorder"])
        for name, data in before.items():
            self.assertEqual(st.snapshot()[name], data, name)
        self.assertTrue(st.roots.marker.exists())

    def test_radiod_moved_leaves_no_marker(self):
        st = Station(self, "k3lr")

        def bump(unit):
            st.units[RADIOD]["ActiveEnterTimestamp"] = "@9999"
        st.sc.on_restart = bump
        _, res, _ = st.run()
        self.assertFalse(res.ok)
        self.assertIn("radiod", res.failed[1])
        self.assertFalse(st.roots.marker.exists())
        self.assertEqual(res.written, ["site", "psk-recorder", "wspr-recorder"])
        self.assertTrue(any("restarted psk-recorder@K3LR.service" in ln
                            for ln in res.lines()))

    def test_verify_failure_leaves_no_marker(self):
        """A lever that lands but whose environment still arms the sender
        (another setting overrides it) stops the carry-over."""
        st = Station(self, "k3lr")
        st.units["psk-recorder@K3LR.service"]["Environment"] = \
            "PSK_DELIVERY_MODE=direct"
        st.write("etc/psk-recorder/override.env",
                 "PSK_DELIVERY_PIPELINES=direct\n")
        st.units["psk-recorder@K3LR.service"]["env_files"].append(
            str(st.tmp / "etc/psk-recorder/override.env"))
        _, res, _ = st.run()
        self.assertFalse(res.ok)
        self.assertFalse(st.roots.marker.exists())


@needs_real
class RerunAfterRollback(unittest.TestCase):
    """V8, Ruling 8: marker moved aside, files present, [uploads] changed
    meanwhile: site.toml rewritten from [uploads], every client file kept
    byte for byte."""

    def test_site_rewritten_clients_kept(self):
        st = Station(self, "b4")
        _, res, _ = st.run()
        self.assertTrue(res.ok)
        st.roots.marker.rename(st.roots.marker.with_name(
            ss.MARKER_NAME + ".rolled-back-20261023T000000Z"))
        coord = st.tmp / "etc/sigmond/coordination.toml"
        coord.write_text(coord.read_text().replace(
            'enabled = true\nmode = "upload"', 'enabled = false\nmode = "hold"'))
        clients = {k: v for k, v in st.snapshot().items() if k != "site.toml"}
        p, res2, r2 = st.run()
        self.assertTrue(res2.ok, res2.lines())
        self.assertEqual(r2.sets(), ["site"])
        self.assertEqual({k: v for k, v in st.snapshot().items()
                          if k != "site.toml"}, clients)
        site = st.switch("site")
        self.assertEqual(site["state"], "fill")
        self.assertEqual([h["to"] for h in site["history"]], ["upload", "fill"])
        self.assertEqual(site["history"][1]["reason"],
                         "carried over from [uploads] mode = hold")
        self.assertEqual(p.kept, (("hamsci-physics", "upload"),
                                  ("mag-recorder", "upload"),
                                  ("meteor-scatter", "upload"),
                                  ("psk-recorder", "upload"),
                                  ("wspr-recorder", "upload")))
        self.assertTrue(st.roots.marker.exists())

    def test_matching_site_is_left_alone(self):
        """A failed earlier run left a site.toml that matches: no write."""
        st = Station(self, "b4")
        st.run()
        st.roots.marker.unlink()
        before = st.snapshot()
        _, res, r = st.run()
        self.assertTrue(res.ok)
        self.assertEqual(r.sets(), [])
        self.assertEqual(st.snapshot(), before)


@needs_real
class LateClients(unittest.TestCase):
    """§6.5, Michael's answer 2: with the marker standing, a governed client
    with no file gets one by §6.3's table, under the site switch."""

    def test_meteor_scatter_enabled_later(self):
        st = Station(self, "legacy")
        st.run()
        marker = st.roots.marker.read_bytes()
        before = st.snapshot()
        st.topology("wspr-recorder", "psk-recorder", "meteor-scatter")
        st.env("meteor-scatter", "W1AW", "METEOR_SCATTER_DELIVERY_MODE=direct\n")
        p = st.plan()
        self.assertTrue(p.marker_stands)
        self.assertIsNone(p.site)
        late = [ln for ln in p.lines() if "meteor-scatter" in ln]
        self.assertEqual(late, ["carry-over: meteor-scatter has no switch file; "
                                "missing -> upload (carried over after the "
                                "marker: a client new since the carry-over)"])
        res = co.apply(p, runner=Runner(st.log), systemctl=st.sc)
        self.assertTrue(res.ok, res.lines())
        self.assertEqual(res.written, ["meteor-scatter"])
        f = st.switch("meteor-scatter")
        self.assertEqual(f["state"], "upload")
        self.assertEqual(f["history"][0]["by"], "carry-over")
        self.assertEqual(st.roots.marker.read_bytes(), marker)
        after = st.snapshot()
        del after["meteor-scatter.toml"]
        self.assertEqual(after, before)
        self.assertEqual(co.late_clients(st.roots).clients, ())

    def test_late_client_under_a_lowered_site_is_levered(self):
        st = Station(self, "k3lr")
        st.run()
        st.topology("wspr-recorder", "psk-recorder", "meteor-scatter")
        st.env("meteor-scatter", "K3LR", "METEOR_SCATTER_DELIVERY_MODE=direct\n")
        p = co.late_clients(st.roots)
        self.assertEqual(states(p), {"meteor-scatter": "upload"})
        self.assertEqual(p.below, ("meteor-scatter",))
        res = co.apply(p, runner=Runner(st.log), systemctl=st.sc)
        self.assertTrue(res.ok, res.lines())
        env = (st.tmp / "etc/meteor-scatter/env/K3LR.env").read_text()
        self.assertIn("METEOR_SCATTER_DELIVERY_MODE=off", env)


# ---------------------------------------------------------------------------
# sink.db: producers read-only (D12)
# ---------------------------------------------------------------------------

def _digest(paths):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.exists()}


class SinkProducers(unittest.TestCase):

    def test_distinct_producers_with_inference(self):
        st = Station(self)
        build_sink(st.roots.sink_db,
                   "INSERT INTO pending_uploads(target_db,target_table,"
                   "payload_json,queued_at,producer) VALUES "
                   "('psk','spots','{\"mode\":\" msk144 \"}','t',''),"
                   "('psk','spots','{\"mode\":\"FT8\"}','t',''),"
                   "('wspr','spots','{}','t','wspr-recorder'),"
                   "('mystery','x','{}','t','');")
        prods, notes = co.sink_producers(st.roots.sink_db)
        self.assertEqual(prods, {"meteor-scatter", "psk-recorder",
                                 "wspr-recorder"})
        self.assertEqual(notes, [])

    def test_producer_that_is_no_client_name_is_ignored(self):
        """Recorder accounts can write sink.db; a producer such as
        '../../etc' must never become a path.  Control: a valid one counts."""
        st = Station(self)
        build_sink(st.roots.sink_db,
                   "INSERT INTO pending_uploads(target_db,target_table,"
                   "payload_json,queued_at,producer) VALUES "
                   "('x','y','{}','t','../../etc'),"
                   "('x','y','{}','t','psk-recorder');")
        prods, notes = co.sink_producers(st.roots.sink_db)
        self.assertEqual(prods, {"psk-recorder"})
        self.assertEqual(len(notes), 1)

    def test_sink_without_producer_column(self):
        st = Station(self)
        build_sink(st.roots.sink_db,
                   "INSERT INTO pending_uploads(target_db,target_table,"
                   "payload_json,queued_at) VALUES "
                   "('psk','spots','{\"mode\":\"MSK144\"}','t');",
                   producer_column=False)
        self.assertEqual(co.sink_producers(st.roots.sink_db)[0],
                         {"meteor-scatter"})

    def test_missing_sink_is_never_created(self):
        st = Station(self)
        basic(st)
        prods, notes = co.sink_producers(st.roots.sink_db)
        self.assertEqual(prods, set())
        st.plan()
        self.assertFalse(st.roots.sink_db.exists())
        self.assertEqual(list(st.roots.sink_db.parent.glob("sink.db*")), [])

    def test_sink_unchanged_by_the_read(self):
        """A WAL with committed rows not yet checkpointed: a read-write open
        would checkpoint them into sink.db and delete the WAL on close.  The
        read must leave both byte for byte.  Positive control: the producer
        whose only row sits in the WAL is found."""
        st = Station(self)
        basic(st)
        src = st.tmp / "src"
        src.mkdir()
        w = sqlite3.connect(src / "sink.db")
        w.execute("PRAGMA journal_mode=WAL")
        w.execute("PRAGMA wal_autocheckpoint=0")
        w.executescript(_bundled.PENDING_UPLOADS_DDL)
        w.execute("INSERT INTO pending_uploads(target_db,target_table,"
                  "payload_json,queued_at,producer) VALUES "
                  "('psk','spots','{\"mode\":\"MSK144\"}','t','')")
        w.commit()
        for f in src.iterdir():
            shutil.copy2(f, st.tmp / f.name)
        w.close()
        files = [st.tmp / "sink.db", st.tmp / "sink.db-wal"]
        self.assertTrue(files[1].exists())
        before = _digest(files)
        p = st.plan()
        self.assertEqual(_digest(files), before)
        self.assertEqual(states(p)["meteor-scatter"], "fill")

    def test_unreadable_sink_is_a_note_not_a_stop(self):
        st = Station(self)
        basic(st)
        st.roots.sink_db.write_text("not a database at all, " * 100)
        p = st.plan()
        self.assertIn("wspr-recorder", states(p))
        self.assertTrue(any("sink.db" in n for n in p.notes), p.notes)


# ---------------------------------------------------------------------------
# the shared marker helper, and the lock rule
# ---------------------------------------------------------------------------

class Marker(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_marker_path_is_hs_uploaders(self):
        self.assertEqual(ss.MARKER_PATH,
                         Path("/var/lib/hs-uploader/carry-over-v1.done"))

    def test_stands_by_lstat(self):
        m = self.tmp / ss.MARKER_NAME
        self.assertFalse(ss.marker_stands(m))
        m.symlink_to(self.tmp / "nowhere")
        self.assertTrue(ss.marker_stands(m))      # a dangling link counts
        m.unlink()
        m.write_text("x")
        self.assertTrue(ss.marker_stands(m))

    @unittest.skipIf(os.geteuid() == 0, "root reads through mode 000")
    def test_unreadable_directory_reads_as_standing(self):
        d = self.tmp / "state"
        d.mkdir()
        os.chmod(d, 0)
        self.addCleanup(os.chmod, d, 0o755)
        self.assertTrue(ss.marker_stands(d / ss.MARKER_NAME))


class NoLockReentry(unittest.TestCase):
    """Forward note on Task 14: the carry-over runs under the manifest
    step's install lock and must never re-enter it."""

    def test_source_takes_no_lock_and_calls_no_manifest_step(self):
        src = Path(co.__file__).read_text()
        for word in ("cmd_uploader_manifest", "flock", "install_lock",
                     "hs-uploader-install"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main()
