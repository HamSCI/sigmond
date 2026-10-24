"""The lever step (spec §4.3, §8; tests 19, 20).

A client whose effective state reads below ``upload`` loses its in-process
senders: sigmond writes each lever of §8.2, restarts each touched instance
alone with ``systemctl restart <unit>``, and checks radiod never moved.
Stale writers restart before a change to ``off``.

Every test runs on a scratch tree (``Roots``) with a fake ``systemctl`` that
records each call and serves ``show`` from a table.  The env files the fake
lists under ``EnvironmentFiles`` sit in the scratch tree, so the module's
own reader (``inprocess_senders._systemctl_show``) reads them for real.
"""

import ast
import fnmatch
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond import inprocess_senders as ips
from sigmond import sink_levers as L

MODULE = Path(L.__file__)

PSK_A = "psk-recorder@AC0G=S.service"
PSK_B = "psk-recorder@AC0G=T.service"
MS_A = "meteor-scatter@AC0G=S.service"
WSPR_A = "wspr-recorder@AC0G=S.service"
RADIOD = "radiod@rx888.service"
UPLOADER = "wspr-uploader.service"


class Topo:
    def __init__(self, names):
        self._names = list(names)

    def enabled_components(self, only=None):
        return list(self._names)


class FakeSystemctl:
    """A ``subprocess.run`` stand-in for ``systemctl``.

    ``units`` maps a unit name to its properties.  ``show`` answers the
    asked properties for each named unit, expanding a glob over the table
    as systemd does over loaded units; a named unit missing from the table
    answers empty properties.  ``Environment`` and ``EnvironmentFiles`` come
    from ``env_files`` (a list of scratch paths).  ``restart`` stamps a new
    start time; ``disable --now`` stops and disables.
    """

    def __init__(self, units):
        self.units = units
        self.calls = []
        self.clock = None
        self.on_restart = None

    def __call__(self, cmd, **kw):
        assert cmd[0] == "systemctl", cmd
        args = list(cmd[1:])
        self.calls.append(args)
        verb = args[0]
        if verb == "show":
            return self._show(cmd, args[1:])
        if verb == "restart":
            unit = args[1]
            u = self.units.setdefault(unit, {})
            if u.get("fail_restart"):
                return subprocess.CompletedProcess(cmd, 1, "", "failed")
            # systemd prints whole seconds: stamp the real current second,
            # so a lever written in this second compares as systemd would.
            now = int(time.time()) if self.clock is None else self.clock
            u["ActiveState"] = "active"
            u["ActiveEnterTimestamp"] = f"@{now}"
            u["ActiveEnterTimestampMonotonic"] = str(time.monotonic_ns() // 1000)
            if self.on_restart:
                self.on_restart(unit)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if verb == "stop":
            u = self.units.setdefault(args[1], {})
            u["ActiveState"] = "inactive"
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if verb == "disable" and args[1] == "--now":
            u = self.units.setdefault(args[2], {})
            u["ActiveState"] = "inactive"
            u["UnitFileState"] = "disabled"
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected systemctl verb {args}")

    def _show(self, cmd, rest):
        props, names, i = [], [], 0
        while i < len(rest):
            if rest[i] == "-p":
                props.append(rest[i + 1])
                i += 2
            elif rest[i].startswith("-"):
                i += 1
            else:
                names.append(rest[i])
                i += 1
        blocks = []
        for name in names:
            if any(c in name for c in "*?["):
                matched = sorted(n for n in self.units
                                 if fnmatch.fnmatchcase(n, name))
            else:
                matched = [name]
            for unit in matched:
                u = self.units.get(unit, {})
                lines = []
                for p in props:
                    if p == "Id":
                        lines.append(f"Id={unit}")
                    elif p == "Environment":
                        lines.append("Environment=" + u.get("Environment", ""))
                    elif p == "EnvironmentFiles":
                        files = u.get("env_files", [])
                        if not files:
                            lines.append("EnvironmentFiles=")
                        for f in files:
                            lines.append(f"EnvironmentFiles={f} (ignore_errors=yes)")
                    else:
                        lines.append(f"{p}={u.get(p, '')}")
                blocks.append("\n".join(lines))
        return subprocess.CompletedProcess(cmd, 0, "\n\n".join(blocks) + "\n", "")

    # call-list views
    def verbs(self):
        return [c for c in self.calls if c[0] != "show"]

    def restarts(self):
        return [c[1] for c in self.calls if c[0] == "restart"]


class Base(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.etc = self.tmp / "etc"
        self.writer = self.tmp / "writer.py"
        self.writer.write_text("# writer\n")
        os.utime(self.writer, (3000, 3000))
        self.roots = L.Roots(etc=self.etc, writer_py=self.writer)
        self.inst = {"psk-recorder": [], "meteor-scatter": [],
                     "wspr-recorder": []}
        mock.patch.object(
            ips, "_instance_units",
            side_effect=lambda client, topo: list(self.inst.get(client, []))
        ).start()
        self.units = {RADIOD: {"ActiveState": "active",
                               "ActiveEnterTimestamp": "@1000",
                               "ActiveEnterTimestampMonotonic": "1000000000"}}
        self.sc = FakeSystemctl(self.units)
        self.topo = Topo(["radiod", "psk-recorder", "meteor-scatter",
                          "wspr-recorder", "mag-recorder"])

    def instance(self, client, unit, env_text, started=2000):
        """An enabled instance whose env file holds ``env_text``."""
        name = unit.partition("@")[2][:-len(".service")]
        d = self.etc / client / "env"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{name}.env"
        f.write_text(env_text)
        os.utime(f, (started - 500, started - 500))   # written before it started
        self.inst[client].append(unit)
        self.units[unit] = {"env_files": [str(f)], "ActiveState": "active",
                            "ActiveEnterTimestamp": f"@{started}",
                            "ActiveEnterTimestampMonotonic": str(started * 10**6)}
        return f

    def mag(self, text):
        d = self.etc / "mag-recorder"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "mag-recorder-config.toml"
        f.write_text(text)
        return f

    def uploader(self, active="active", state="enabled"):
        self.units[UPLOADER] = {"LoadState": "loaded", "ActiveState": active,
                                "UnitFileState": state}

    def plan(self, below):
        return L.plan_levers(below, self.topo, roots=self.roots,
                             systemctl=self.sc)

    def lever(self, below):
        plan = self.plan(below)
        return plan, L.apply_levers(plan, systemctl=self.sc)


def env_of(path):
    return dict(ln.split("=", 1) for ln in path.read_text().splitlines()
                if "=" in ln and not ln.startswith("#"))


class EachLever(Base):
    """Test 19: each lever of §8.2, written for a client below upload."""

    def test_psk_direct_goes_server_raw(self):
        f = self.instance("psk-recorder", PSK_A,
                          "# keep me\nPSK_DELIVERY_PIPELINES=direct\nX=1\n")
        _, res = self.lever(["psk-recorder"])
        self.assertEqual(env_of(f)["PSK_DELIVERY_PIPELINES"], "server-raw")
        self.assertIn("# keep me", f.read_text())
        self.assertEqual(env_of(f)["X"], "1")
        self.assertEqual(res.restarted, [PSK_A])
        self.assertEqual(res.changed, [f])
        self.assertEqual(res.failures, [])

    def test_env_file_keeps_its_mode(self):
        f = self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=direct\n")
        os.chmod(f, 0o600)
        self.lever(["psk-recorder"])
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)

    def test_result_lines_name_each_file_unit_and_failure(self):
        f = self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=direct\n")
        g = self.instance("psk-recorder", PSK_B, "PSK_DELIVERY_PIPELINES=server-raw\n")
        _, res = self.lever(["psk-recorder"])
        res.failures.append(("/x.env", "why"))
        text = "\n".join(res.lines())
        self.assertIn(f"changed {f}", text)
        self.assertIn(f"restarted {PSK_A}", text)
        self.assertIn(f"left alone {PSK_B}", text)
        self.assertIn("FAILED /x.env: why", text)
        self.assertNotIn(str(g), text)

    def test_psk_server_merge_goes_server_raw(self):
        f = self.instance("psk-recorder", PSK_A,
                          "PSK_DELIVERY_PIPELINES=server-merge\n")
        self.lever(["psk-recorder"])
        self.assertEqual(env_of(f)["PSK_DELIVERY_PIPELINES"], "server-raw")

    def test_psk_default_unset_is_server_merge_and_goes_server_raw(self):
        f = self.instance("psk-recorder", PSK_A, "")
        self.lever(["psk-recorder"])
        self.assertEqual(env_of(f)["PSK_DELIVERY_PIPELINES"], "server-raw")

    def test_psk_legacy_mode(self):
        f = self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_MODE=both\n")
        self.lever(["psk-recorder"])
        self.assertEqual(env_of(f)["PSK_DELIVERY_PIPELINES"], "server-raw")
        # PSK_DELIVERY_PIPELINES outranks the legacy key at psk-recorder.
        self.assertEqual(ips._psk_delivery_pipelines(env_of(f)),
                         ("server-raw",))

    def test_psk_direct_plus_server_raw_still_sends(self):
        f = self.instance("psk-recorder", PSK_A,
                          "PSK_DELIVERY_PIPELINES=direct,server-raw\n")
        self.lever(["psk-recorder"])
        self.assertEqual(env_of(f)["PSK_DELIVERY_PIPELINES"], "server-raw")

    def test_meteor_direct_unset_deposit_go_off(self):
        for text in ("METEOR_SCATTER_DELIVERY_MODE=direct\n", "",
                     "METEOR_SCATTER_DELIVERY_MODE=deposit\n"):
            with self.subTest(text=text):
                self.setUp()
                f = self.instance("meteor-scatter", MS_A, text)
                _, res = self.lever(["meteor-scatter"])
                self.assertEqual(env_of(f)["METEOR_SCATTER_DELIVERY_MODE"],
                                 "off")
                self.assertEqual(res.restarted, [MS_A])

    def test_wspr_shim_goes_zero(self):
        f = self.instance("wspr-recorder", WSPR_A, "WSPR_USE_HS_UPLOADER=1\n")
        _, res = self.lever(["wspr-recorder"])
        self.assertEqual(env_of(f)["WSPR_USE_HS_UPLOADER"], "0")
        self.assertEqual(res.restarted, [WSPR_A])

    def test_wspr_uploader_disable_now(self):
        self.uploader()
        _, res = self.lever(["wspr-recorder"])
        self.assertIn(["disable", "--now", UPLOADER], self.sc.calls)
        self.assertNotIn(UPLOADER, self.sc.restarts())
        self.assertEqual(res.disabled, [UPLOADER])

    def test_wspr_uploader_enabled_but_stopped_still_disabled(self):
        self.uploader(active="inactive", state="enabled")
        self.lever(["wspr-recorder"])
        self.assertIn(["disable", "--now", UPLOADER], self.sc.calls)

    def test_wspr_uploader_enabled_runtime_stopped_still_armed(self):
        self.uploader(active="inactive", state="enabled-runtime")
        plan, res = self.lever(["wspr-recorder"])
        self.assertIn(["disable", "--now", UPLOADER], self.sc.calls)
        self.assertEqual(res.disabled, [UPLOADER])

    # Final batch (a): linked, static, indirect and generated name no
    # enablement of their own; such a unit is armed only while it runs,
    # and `disable --now` never touches it while it stands inactive (on a
    # linked unit, disable removes the link itself).
    NOT_ENABLED = ("linked", "linked-runtime", "static", "indirect",
                   "generated")

    def test_inactive_linked_and_kin_left_alone(self):
        for state in self.NOT_ENABLED:
            with self.subTest(state=state):
                self.setUp()
                self.uploader(active="inactive", state=state)
                plan, res = self.lever(["wspr-recorder"])
                self.assertNotIn(["disable", "--now", UPLOADER], self.sc.calls)
                self.assertIn(UPLOADER, res.left_alone)
                self.assertEqual(L.verify_levers(plan, systemctl=self.sc), [])

    def test_active_linked_and_kin_stopped_never_disabled(self):
        """C27-2: running, such a unit is armed, and the lever stops it
        only: `disable` would unlink a linked unit.  Verify then reads it
        quiet, and its UnitFileState stays as it stood."""
        for state in self.NOT_ENABLED:
            with self.subTest(state=state):
                self.setUp()
                self.uploader(active="active", state=state)
                plan, res = self.lever(["wspr-recorder"])
                self.assertIn(["stop", UPLOADER], self.sc.calls)
                self.assertFalse(any(c[0] == "disable" for c in self.sc.calls),
                                 self.sc.calls)
                self.assertEqual(res.stopped, [UPLOADER])
                self.assertEqual(res.disabled, [])
                self.assertIn(f"lever: stopped {UPLOADER}", "\n".join(res.lines()))
                self.assertEqual(self.sc.units[UPLOADER]["UnitFileState"], state)
                self.assertEqual(L.verify_levers(plan, systemctl=self.sc), [])

    def test_active_enabled_keeps_disable_now(self):
        self.uploader(active="active", state="enabled")
        plan, res = self.lever(["wspr-recorder"])
        self.assertIn(["disable", "--now", UPLOADER], self.sc.calls)
        self.assertNotIn(["stop", UPLOADER], self.sc.calls)
        self.assertEqual(res.disabled, [UPLOADER])
        self.assertEqual(res.stopped, [])

    def test_inactive_linked_gets_no_call(self):
        self.uploader(active="inactive", state="linked")
        self.lever(["wspr-recorder"])
        self.assertEqual([c for c in self.sc.calls if c[0] != "show"], [])

    def test_verify_names_a_running_uploader_in_other_states(self):
        for state in self.NOT_ENABLED + ("enabled",):
            with self.subTest(state=state):
                self.setUp()
                self.uploader(active="inactive", state="disabled")
                plan = self.plan(["wspr-recorder"])
                self.uploader(active="active", state=state)
                self.assertTrue(any(UPLOADER in m for m in
                                    L.verify_levers(plan, systemctl=self.sc)))
        for state in ("disabled", "masked"):           # control
            with self.subTest(state=state):
                self.setUp()
                self.uploader(active="inactive", state=state)
                plan = self.plan(["wspr-recorder"])
                self.assertEqual(L.verify_levers(plan, systemctl=self.sc), [])

    def test_wspr_uploader_already_off_left_alone(self):
        self.uploader(active="inactive", state="disabled")
        _, res = self.lever(["wspr-recorder"])
        self.assertNotIn(["disable", "--now", UPLOADER], self.sc.calls)
        self.assertIn(UPLOADER, res.left_alone)

    def test_mag_enabled_true_goes_boolean_false(self):
        f = self.mag("[station]\ncallsign = \"AC0G\"\n\n[uploader]\n"
                     "enabled = true  # fallback\n")
        _, res = self.lever(["mag-recorder"])
        got = tomllib.loads(f.read_text())
        self.assertIs(got["uploader"]["enabled"], False)
        self.assertEqual(got["station"]["callsign"], "AC0G")
        self.assertIn(f, res.changed)
        # The timer reads the config each run: no restart for mag.
        self.assertEqual(self.sc.restarts(), [])

    def test_mag_absent_key_goes_boolean_false(self):
        f = self.mag("[station]\ncallsign = \"AC0G\"\n")
        self.lever(["mag-recorder"])
        self.assertIs(tomllib.loads(f.read_text())["uploader"]["enabled"],
                      False)

    def test_mag_already_false_left_alone(self):
        f = self.mag("[uploader]\nenabled = false\n")
        before = f.read_bytes()
        _, res = self.lever(["mag-recorder"])
        self.assertEqual(f.read_bytes(), before)
        self.assertIn(str(f), res.left_alone)

    def test_already_lowered_left_alone(self):
        self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=server-raw\n")
        self.instance("meteor-scatter", MS_A, "METEOR_SCATTER_DELIVERY_MODE=off\n")
        self.instance("wspr-recorder", WSPR_A, "WSPR_USE_HS_UPLOADER=0\n")
        _, res = self.lever(["psk-recorder", "meteor-scatter", "wspr-recorder"])
        self.assertEqual(self.sc.verbs(), [])
        self.assertEqual(res.changed, [])
        self.assertEqual(sorted(res.left_alone), sorted([PSK_A, MS_A, WSPR_A]))
        # positive control: the same harness re-armed sees the write and restart
        self.instance("psk-recorder", PSK_B, "PSK_DELIVERY_PIPELINES=direct\n")
        _, res = self.lever(["psk-recorder"])
        self.assertEqual(self.sc.verbs(), [["restart", PSK_B]])


class OnlyTouched(Base):
    """Test 19: only touched units restart, each once, alone; no radiod."""

    def full_station(self):
        self.p1 = self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=direct\n")
        self.p2 = self.instance("psk-recorder", PSK_B, "PSK_DELIVERY_PIPELINES=server-merge\n")
        self.m1 = self.instance("meteor-scatter", MS_A, "")
        self.w1 = self.instance("wspr-recorder", WSPR_A, "WSPR_USE_HS_UPLOADER=1\n")
        self.uploader()
        self.cfg = self.mag("[uploader]\nenabled = true\n")

    def test_site_upload_psk_fill_lowers_psk_alone(self):
        self.full_station()
        _, res = self.lever(["psk-recorder"])
        self.assertEqual(self.sc.verbs(), [["restart", PSK_A], ["restart", PSK_B]])
        self.assertEqual(env_of(self.m1), {})
        self.assertEqual(env_of(self.w1)["WSPR_USE_HS_UPLOADER"], "1")
        self.assertIs(tomllib.loads(self.cfg.read_text())["uploader"]["enabled"], True)
        self.assertEqual(res.restarted, [PSK_A, PSK_B])

    def test_all_below_each_touched_unit_restarts_once_by_systemctl_restart(self):
        self.full_station()
        self.instance("psk-recorder", "psk-recorder@K3LR.service",
                      "PSK_DELIVERY_PIPELINES=server-raw\n")
        self.lever(["psk-recorder", "meteor-scatter", "wspr-recorder",
                    "mag-recorder"])
        verbs = self.sc.verbs()
        self.assertEqual(verbs, [["disable", "--now", UPLOADER],
                                 ["restart", PSK_A], ["restart", PSK_B],
                                 ["restart", MS_A], ["restart", WSPR_A]])
        flat = [w for c in self.sc.calls for w in c]
        self.assertFalse(any(w.startswith("radiod") and c[0] != "show"
                             for c in self.sc.calls for w in c))
        self.assertNotIn("psk-recorder@K3LR.service", self.sc.restarts())
        # positive control: the harness does see radiod, in the read-only show
        self.assertTrue(any("radiod@*" == w for w in flat))

    def test_every_write_lands_before_the_first_restart(self):
        self.full_station()
        seen = []

        def at_restart(unit):
            if not seen:
                seen.append((env_of(self.p2)["PSK_DELIVERY_PIPELINES"],
                             env_of(self.w1)["WSPR_USE_HS_UPLOADER"],
                             tomllib.loads(self.cfg.read_text())["uploader"]["enabled"]))
        self.sc.on_restart = at_restart
        self.lever(["psk-recorder", "wspr-recorder", "mag-recorder"])
        self.assertEqual(seen, [("server-raw", "0", False)])

    def test_second_run_writes_nothing_and_restarts_nothing(self):
        self.full_station()
        below = ["psk-recorder", "meteor-scatter", "wspr-recorder", "mag-recorder"]
        self.lever(below)
        snap = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                for p in (self.p1, self.p2, self.m1, self.w1, self.cfg)}
        n = len(self.sc.verbs())
        self.assertGreater(n, 0)                      # positive control
        _, res = self.lever(below)
        self.assertEqual(len(self.sc.verbs()), n)
        self.assertEqual(res.changed, [])
        self.assertEqual(res.restarted, [])
        for p, v in snap.items():
            self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), v, p)

    def test_no_client_below_touches_nothing(self):
        self.full_station()
        _, res = self.lever([])
        self.assertEqual(self.sc.calls, [])
        self.assertEqual(res.changed, [])
        self.lever(["psk-recorder"])                   # positive control
        self.assertIn(["restart", PSK_A], self.sc.verbs())


class Failures(Base):
    """A refused lever file names itself, stops no other lever, and its
    unit does not restart (the file did not change)."""

    def test_symlinked_env_file_refused_others_proceed(self):
        f = self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=direct\n")
        target = self.tmp / "elsewhere.env"
        target.write_text("PSK_DELIVERY_PIPELINES=direct\n")
        f.unlink()
        f.symlink_to(target)
        g = self.instance("psk-recorder", PSK_B, "PSK_DELIVERY_PIPELINES=direct\n")
        _, res = self.lever(["psk-recorder"])
        self.assertEqual(target.read_text(), "PSK_DELIVERY_PIPELINES=direct\n")
        self.assertEqual([x[0] for x in res.failures], [str(f)])
        self.assertEqual(env_of(g)["PSK_DELIVERY_PIPELINES"], "server-raw")
        self.assertEqual(res.restarted, [PSK_B])

    def test_hard_link_refused(self):
        f = self.instance("wspr-recorder", WSPR_A, "WSPR_USE_HS_UPLOADER=1\n")
        os.link(f, self.tmp / "other-name")
        _, res = self.lever(["wspr-recorder"])
        self.assertEqual([x[0] for x in res.failures], [str(f)])
        self.assertEqual(res.restarted, [])

    def test_mag_missing_config_reads_not_armed(self):
        """m8: no config, no fallback to run: nothing written, nothing
        failed, verify quiet.  Control: test_mag_enabled_true_goes_boolean_false."""
        plan, res = self.lever(["mag-recorder"])
        cfg = self.etc / "mag-recorder" / "mag-recorder-config.toml"
        self.assertIsNone(plan.mag)
        self.assertEqual(res.failures, [])
        self.assertEqual(res.changed, [])
        self.assertFalse(cfg.exists())
        self.assertEqual(L.verify_levers(plan, systemctl=self.sc), [])

    def test_mag_failure_named(self):
        f = self.mag("[uploader]\nenabled = true\n")
        real = self.tmp / "real.toml"
        real.write_text("[uploader]\nenabled = true\n")
        f.unlink()
        f.symlink_to(real)
        w = self.instance("wspr-recorder", WSPR_A, "WSPR_USE_HS_UPLOADER=1\n")
        _, res = self.lever(["mag-recorder", "wspr-recorder"])
        self.assertEqual([x[0] for x in res.failures], [str(f)])
        self.assertEqual(env_of(w)["WSPR_USE_HS_UPLOADER"], "0")

    def test_override_elsewhere_reported_and_not_restarted(self):
        # A later EnvironmentFiles entry re-arms the sender: the write lands
        # but the effective environment still sends, so no restart.
        f = self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=direct\n")
        late = self.tmp / "late.env"
        late.write_text("PSK_DELIVERY_PIPELINES=direct\n")
        self.units[PSK_A]["env_files"].append(str(late))
        _, res = self.lever(["psk-recorder"])
        self.assertEqual(env_of(f)["PSK_DELIVERY_PIPELINES"], "server-raw")
        self.assertEqual(res.restarted, [])
        self.assertEqual([x[0] for x in res.failures], [str(f)])
        self.assertIn("still", res.failures[0][1])

    def test_unreadable_environment_named(self):
        self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=direct\n")
        # a file systemd must read (no ignore_errors) that is missing
        self.units[PSK_A]["env_files"] = [str(self.tmp / "missing.env")]
        orig = self.sc._show

        def show(cmd, rest):
            r = orig(cmd, rest)
            return subprocess.CompletedProcess(
                cmd, 0, r.stdout.replace(" (ignore_errors=yes)", ""), "")
        self.sc._show = show
        _, res = self.lever(["psk-recorder"])
        self.assertEqual([x[0] for x in res.failures], [PSK_A])
        self.assertEqual(self.sc.verbs(), [])

    def test_failed_restart_named_and_the_rest_go_on(self):
        self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=direct\n")
        self.instance("psk-recorder", PSK_B, "PSK_DELIVERY_PIPELINES=direct\n")
        self.units[PSK_A]["fail_restart"] = True
        _, res = self.lever(["psk-recorder"])
        self.assertEqual(self.sc.restarts(), [PSK_A, PSK_B])
        self.assertEqual(res.restarted, [PSK_B])
        self.assertEqual([x[0] for x in res.failures], [PSK_A])


class Unrestarted(Base):
    """Ruling C17-2: a lever written but never restarted is not forgotten."""

    def test_next_run_restarts_what_the_last_wrote_but_did_not_restart(self):
        f = self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=direct\n")
        self.units[PSK_A]["fail_restart"] = True
        plan, res = self.lever(["psk-recorder"])
        self.assertEqual(env_of(f)["PSK_DELIVERY_PIPELINES"], "server-raw")
        self.assertEqual(res.restarted, [])
        # the env reads lowered, but the process predates the file
        msgs = L.verify_levers(plan, systemctl=self.sc)
        self.assertEqual(len(msgs), 1)
        self.assertIn(PSK_A, msgs[0])
        del self.units[PSK_A]["fail_restart"]
        n = len(self.sc.restarts())
        plan2, res2 = self.lever(["psk-recorder"])
        self.assertEqual(plan2.restart_only, (PSK_A,))
        self.assertEqual(plan2.env_levers, ())
        self.assertEqual(res2.changed, [])               # no write
        self.assertEqual(self.sc.restarts()[n:], [PSK_A])
        self.assertEqual(res2.restarted, [PSK_A])
        self.assertIn(f"restarted {PSK_A}", "\n".join(res2.lines()))
        self.assertEqual(L.verify_levers(plan2, systemctl=self.sc), [])
        # and a third run, now current, restarts nothing
        _, res3 = self.lever(["psk-recorder"])
        self.assertEqual(res3.restarted, [])

    def test_verify_checks_untouched_in_scope_instance(self):
        f = self.instance("wspr-recorder", WSPR_A, "WSPR_USE_HS_UPLOADER=0\n")
        plan = self.plan(["wspr-recorder"])
        self.assertEqual(L.verify_levers(plan, systemctl=self.sc), [])  # control
        os.utime(f, (4000, 4000))                    # lowered after it started
        msgs = L.verify_levers(plan, systemctl=self.sc)
        self.assertEqual(len(msgs), 1)
        self.assertIn(WSPR_A, msgs[0])

    def test_apply_skip_does_not_restart_again(self):
        self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=direct\n")
        self.instance("psk-recorder", PSK_B, "PSK_DELIVERY_PIPELINES=direct\n")
        plan = self.plan(["psk-recorder"])
        res = L.apply_levers(plan, systemctl=self.sc, skip=[PSK_A])
        self.assertEqual(self.sc.restarts(), [PSK_B])
        self.assertEqual(res.restarted, [PSK_B])


class Radiod(Base):
    """Test 19: radiod's start time is read before the first restart and
    after the last; a move raises RadiodMoved naming the unit."""

    def test_radiod_moved_raises_naming_unit(self):
        self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=direct\n")

        def bump(unit):
            self.units[RADIOD]["ActiveEnterTimestamp"] = "@9999"
            self.units[RADIOD]["ActiveEnterTimestampMonotonic"] = "9999000000"
        self.sc.on_restart = bump
        with self.assertRaises(L.RadiodMoved) as cm:
            self.lever(["psk-recorder"])
        self.assertIn(RADIOD, str(cm.exception))

    def test_radiod_moved_carries_partial_result(self):
        f = self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=direct\n")
        self.instance("psk-recorder", PSK_B, "PSK_DELIVERY_PIPELINES=direct\n")
        self.units[PSK_B]["fail_restart"] = True

        def bump(unit):
            self.units[RADIOD]["ActiveEnterTimestamp"] = "@9999"
        self.sc.on_restart = bump
        with self.assertRaises(L.RadiodMoved) as cm:
            self.lever(["psk-recorder"])
        r = cm.exception.result
        self.assertIsNotNone(r)
        self.assertIn(f, r.changed)
        self.assertEqual(r.restarted, [PSK_A])
        self.assertEqual([x[0] for x in r.failures], [PSK_B])
        self.assertEqual([x[0] for x in cm.exception.failures], [PSK_B])
        self.assertIn(f"changed {f}", "\n".join(r.lines()))

    def test_radiod_still_raises_when_only_one_of_several_moves(self):
        self.units["radiod@second.service"] = {
            "ActiveState": "active", "ActiveEnterTimestamp": "@1100",
            "ActiveEnterTimestampMonotonic": "1100000000"}

        def bump(unit):
            self.units["radiod@second.service"]["ActiveEnterTimestamp"] = "@9999"
        self.sc.on_restart = bump
        with self.assertRaises(L.RadiodMoved) as cm:
            L.restart_units([PSK_A], systemctl=self.sc)
        self.assertIn("radiod@second.service", str(cm.exception))
        self.assertNotIn(RADIOD, str(cm.exception))

    def test_radiod_appearing_counts_as_moved(self):
        def appear(unit):
            self.units["radiod@new.service"] = {"ActiveState": "active",
                                                "ActiveEnterTimestamp": "@7"}
        self.sc.on_restart = appear
        with self.assertRaises(L.RadiodMoved) as cm:
            L.restart_units([PSK_A], systemctl=self.sc)
        self.assertIn("radiod@new.service", str(cm.exception))

    def test_radiod_steady_no_raise_and_read_twice(self):
        L.restart_units([PSK_A, PSK_B], systemctl=self.sc)
        shows = [i for i, c in enumerate(self.sc.calls)
                 if c[0] == "show" and "radiod@*" in c]
        rs = [i for i, c in enumerate(self.sc.calls) if c[0] == "restart"]
        self.assertEqual(len(shows), 2)
        self.assertLess(shows[0], rs[0])
        self.assertGreater(shows[1], rs[-1])
        self.assertEqual(self.sc.restarts(), [PSK_A, PSK_B])

    def test_restart_units_refuses_radiod(self):
        with self.assertRaises(ValueError):
            L.restart_units([PSK_A, RADIOD], systemctl=self.sc)
        self.assertEqual(self.sc.calls, [])
        L.restart_units([PSK_A], systemctl=self.sc)    # positive control
        self.assertEqual(self.sc.restarts(), [PSK_A])

    def test_restart_units_one_at_a_time_each_once(self):
        L.restart_units([PSK_A, PSK_A, MS_A], systemctl=self.sc)
        self.assertEqual([c for c in self.sc.calls if c[0] == "restart"],
                         [["restart", PSK_A], ["restart", MS_A]])

    def test_nothing_to_restart_reads_nothing(self):
        L.restart_units([], systemctl=self.sc)
        self.assertEqual(self.sc.calls, [])
        L.restart_units([PSK_A], systemctl=self.sc)    # positive control
        self.assertEqual(len(self.sc.calls), 3)        # show, restart, show


class Verify(Base):
    """§8.4: verify_levers names whatever still arms a sender."""

    def test_clean_after_lever(self):
        self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=direct\n")
        self.instance("meteor-scatter", MS_A, "")
        self.instance("wspr-recorder", WSPR_A, "WSPR_USE_HS_UPLOADER=yes\n")
        self.uploader()
        self.mag("[uploader]\nenabled = true\n")
        plan, _ = self.lever(["psk-recorder", "meteor-scatter",
                              "wspr-recorder", "mag-recorder"])
        self.assertEqual(L.verify_levers(plan, systemctl=self.sc), [])

    def test_names_instance_still_armed(self):
        f = self.instance("psk-recorder", PSK_A, "PSK_DELIVERY_PIPELINES=direct\n")
        plan = self.plan(["psk-recorder"])
        # positive control: before any lever, verify names it
        self.assertTrue(any(PSK_A in m for m in
                            L.verify_levers(plan, systemctl=self.sc)))
        L.apply_levers(plan, systemctl=self.sc)
        self.assertEqual(L.verify_levers(plan, systemctl=self.sc), [])
        f.write_text("PSK_DELIVERY_PIPELINES=direct\n")      # re-armed
        msgs = L.verify_levers(plan, systemctl=self.sc)
        self.assertEqual(len(msgs), 1)
        self.assertIn(PSK_A, msgs[0])

    def test_names_unit_not_restarted_since_its_lever(self):
        f = self.instance("wspr-recorder", WSPR_A, "WSPR_USE_HS_UPLOADER=1\n")
        plan = self.plan(["wspr-recorder"])
        f.write_text("WSPR_USE_HS_UPLOADER=0\n")      # written, never restarted
        os.utime(f, (4000, 4000))
        msgs = L.verify_levers(plan, systemctl=self.sc)
        self.assertEqual(len(msgs), 1)
        self.assertIn(WSPR_A, msgs[0])

    def test_names_uploader_and_mag(self):
        self.uploader()
        cfg = self.mag("[uploader]\nenabled = \"false\"\n")
        plan = self.plan(["wspr-recorder", "mag-recorder"])
        msgs = L.verify_levers(plan, systemctl=self.sc)
        self.assertTrue(any(UPLOADER in m for m in msgs))
        self.assertTrue(any(str(cfg) in m for m in msgs))

    def test_mag_lever_reads_back_boolean(self):
        cfg = self.mag("[uploader]\nenabled = \"false\"\n")   # the string trap
        plan, res = self.lever(["mag-recorder"])
        self.assertIs(tomllib.loads(cfg.read_text())["uploader"]["enabled"], False)
        self.assertEqual(L.verify_levers(plan, systemctl=self.sc), [])


class StaleWriters(Base):
    """Test 20: stale writers restart before the first change to off, not
    for fill; fresh ones stay; one the lever step restarted stays."""

    def station(self):
        # writer mtime 3000: started 2000 is stale, 4000 fresh
        self.instance("psk-recorder", PSK_A, "", started=2000)
        self.instance("psk-recorder", PSK_B, "", started=4000)
        self.instance("meteor-scatter", MS_A, "", started=2500)
        self.instance("wspr-recorder", WSPR_A, "", started=2999)

    def test_stale_units(self):
        self.station()
        self.assertEqual(
            L.stale_writer_units(self.topo, writer_py=self.writer,
                                 systemctl=self.sc),
            [PSK_A, MS_A, WSPR_A])

    def test_inactive_unit_is_not_stale(self):
        self.station()
        self.units[MS_A]["ActiveState"] = "inactive"
        self.assertNotIn(MS_A, L.stale_writer_units(
            self.topo, writer_py=self.writer, systemctl=self.sc))

    def test_disabled_client_not_in_scope(self):
        self.station()
        self.topo = Topo(["psk-recorder"])
        self.assertEqual(L.stale_writer_units(
            self.topo, writer_py=self.writer, systemctl=self.sc), [PSK_A])

    def test_off_restarts_stale_before_the_change(self):
        self.station()
        order = []
        self.sc.on_restart = lambda u: order.append(("restart", u))
        done = L.restart_stale_writers(self.topo, to_off=True,
                                       writer_py=self.writer,
                                       systemctl=self.sc)
        order.append(("change", "off"))
        self.assertEqual(done.restarted, [PSK_A, MS_A, WSPR_A])
        self.assertEqual(done.notes, [])
        self.assertEqual(order, [("restart", PSK_A), ("restart", MS_A),
                                 ("restart", WSPR_A), ("change", "off")])
        self.assertNotIn(PSK_B, self.sc.restarts())
        # The first change to off restarted them; the next finds none stale.
        again = L.restart_stale_writers(self.topo, to_off=True,
                                        writer_py=self.writer,
                                        systemctl=self.sc)
        self.assertEqual(again.restarted, [])
        self.assertEqual(len(self.sc.restarts()), 3)

    def test_fill_restarts_nothing(self):
        self.station()
        done = L.restart_stale_writers(self.topo, to_off=False,
                                       writer_py=self.writer,
                                       systemctl=self.sc)
        self.assertEqual(done.restarted, [])
        self.assertEqual(self.sc.calls, [])
        # positive control: the same station going off restarts the stale ones
        done = L.restart_stale_writers(self.topo, to_off=True,
                                       writer_py=self.writer,
                                       systemctl=self.sc)
        self.assertEqual(done.restarted, [PSK_A, MS_A, WSPR_A])

    def test_lever_restarted_instance_not_restarted_twice(self):
        self.station()
        self.units[PSK_A]["env_files"] = [str(self.etc / "psk-recorder/env/AC0G=S.env")]
        (self.etc / "psk-recorder/env/AC0G=S.env").write_text(
            "PSK_DELIVERY_PIPELINES=direct\n")
        res = L.apply_levers(self.plan(["psk-recorder"]), systemctl=self.sc)
        self.assertIn(PSK_A, res.restarted)
        L.restart_stale_writers(self.topo, to_off=True, writer_py=self.writer,
                                systemctl=self.sc, skip=res.restarted)
        self.assertEqual(self.sc.restarts().count(PSK_A), 1)
        self.assertIn(MS_A, self.sc.restarts())

    def test_lever_restart_skipped_even_if_still_reads_stale(self):
        # skip holds by name, not by a fresh timestamp
        self.station()
        L.restart_stale_writers(self.topo, to_off=True, writer_py=self.writer,
                                systemctl=self.sc, skip=[PSK_A, MS_A])
        self.assertEqual(self.sc.restarts(), [WSPR_A])
        # control: without skip, the same stale units restart
        self.sc.calls.clear()
        self.units[PSK_A]["ActiveEnterTimestamp"] = "@2000"
        L.restart_stale_writers(self.topo, to_off=True, writer_py=self.writer,
                                systemctl=self.sc)
        self.assertIn(PSK_A, self.sc.restarts())

    def test_unreadable_writer_means_nothing_stale_and_says_so(self):
        self.station()
        absent = self.tmp / "absent.py"
        done = L.restart_stale_writers(self.topo, to_off=True,
                                       writer_py=absent, systemctl=self.sc)
        self.assertEqual(done.restarted, [])
        self.assertEqual(self.sc.restarts(), [])
        self.assertEqual(len(done.notes), 1)
        self.assertIn(str(absent), done.notes[0])
        self.assertIn(str(absent), "\n".join(done.lines()))
        # control: the readable writer finds the stale ones
        self.assertEqual(L.stale_writer_units(
            self.topo, writer_py=self.writer, systemctl=self.sc),
            [PSK_A, MS_A, WSPR_A])

    def test_same_second_counts_as_after(self):
        self.station()
        self.units[PSK_A]["ActiveEnterTimestamp"] = "@3000"
        os.utime(self.writer, (3000.9, 3000.9))
        self.assertNotIn(PSK_A, L.stale_writer_units(
            self.topo, writer_py=self.writer, systemctl=self.sc))
        os.utime(self.writer, (3001.0, 3001.0))        # control: next second
        self.assertIn(PSK_A, L.stale_writer_units(
            self.topo, writer_py=self.writer, systemctl=self.sc))

    def test_composed_lever_first_then_stale_then_change(self):
        # Task 20's order (ruling C17-5): the lever step, then the stale
        # writers skipping what it restarted, then the site write.
        self.station()
        (self.etc / "psk-recorder/env/AC0G=S.env").write_text(
            "PSK_DELIVERY_PIPELINES=direct\n")
        os.utime(self.etc / "psk-recorder/env/AC0G=S.env", (1000, 1000))
        (self.etc / "psk-recorder/env/AC0G=T.env").write_text(
            "PSK_DELIVERY_PIPELINES=server-raw\n")
        os.utime(self.etc / "psk-recorder/env/AC0G=T.env", (1000, 1000))
        order = []
        self.sc.on_restart = lambda u: order.append(("restart", u))
        res = L.apply_levers(self.plan(["psk-recorder"]), systemctl=self.sc)
        order.append(("levers done", None))
        st = L.restart_stale_writers(self.topo, to_off=True,
                                     writer_py=self.writer, systemctl=self.sc,
                                     skip=res.restarted)
        order.append(("change", "off"))
        self.assertEqual(res.restarted, [PSK_A])
        self.assertEqual(st.restarted, [MS_A, WSPR_A])
        self.assertEqual(order, [("restart", PSK_A), ("levers done", None),
                                 ("restart", MS_A), ("restart", WSPR_A),
                                 ("change", "off")])
        restarts = self.sc.restarts()
        self.assertEqual(len(restarts), len(set(restarts)))


class Static(unittest.TestCase):
    """The RESTART GATE's source scan: no lifecycle restart helper, no
    cmd_restart, and only the verbs restart, disable --now and show."""

    def setUp(self):
        self.src = MODULE.read_text()
        self.tree = ast.parse(self.src)

    def test_no_lifecycle_or_cmd_restart_import(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names]
                self.assertNotIn("lifecycle", node.module or "")
                self.assertFalse(any("restart" in n or n == "lifecycle"
                                     for n in names), names)
            if isinstance(node, ast.Import):
                self.assertFalse(any("lifecycle" in a.name for a in node.names))
        self.assertNotIn("cmd_restart", self.src)
        self.assertNotIn("lifecycle", self.src)

    def test_systemctl_verbs_restricted(self):
        consts = {n.value for n in ast.walk(self.tree)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        for bad in ("start", "kill", "reload", "try-restart",
                    "reload-or-restart", "is-active", "is-enabled", "enable",
                    "mask", "daemon-reload", "isolate", "reset-failed"):
            self.assertNotIn(bad, consts)
        # every systemctl call goes through _systemctl with a literal verb
        verbs = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_systemctl":
                first = node.args[1]
                self.assertIsInstance(first, ast.Constant)
                verbs.append(first.value)
        self.assertEqual(set(verbs), {"restart", "disable", "show", "stop"})
        # C27-2: `stop` passes for wspr-uploader alone
        self.assertEqual(L.ALLOWED_VERBS,
                         {("restart",), ("disable", "--now"), ("show",),
                          ("stop", L.WSPR_UPLOADER)})
        # the only literal "systemctl" sits in the one helper
        self.assertEqual(self.src.count('"systemctl"'), 1)

    def test_helper_refuses_other_verbs(self):
        sc = FakeSystemctl({})
        with self.assertRaises(ValueError):
            L._systemctl(sc, "stop", "x.service")
        with self.assertRaises(ValueError):
            L._systemctl(sc, "stop", L.WSPR_UPLOADER, "x.service")
        with self.assertRaises(ValueError):
            L._systemctl(sc, "disable", "x.service")
        with self.assertRaises(ValueError):
            L._systemctl(sc, "restart", "a.service", "b.service")
        self.assertEqual(sc.calls, [])


if __name__ == "__main__":
    unittest.main()
