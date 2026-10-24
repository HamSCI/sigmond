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
            unit_dir = u.get("unit_dir")
            if unit_dir is not None:
                # As systemd does (Task 28 F2): disable removes every link
                # to the unit file, the unit's own link included, so the
                # unit vanishes and the stop half exits 5.
                for link in Path(unit_dir).rglob(args[2]):
                    if link.is_symlink():
                        link.unlink()
                u.update(LoadState="not-found", UnitFileState="",
                         ActiveState="inactive")
                return subprocess.CompletedProcess(cmd, 5, "", "not loaded")
            u["ActiveState"] = "inactive"
            u["UnitFileState"] = "disabled"
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if verb == "daemon-reload":
            # A unit with a scratch unit dir re-reads its enablement from
            # the links there, as systemd's reload does.
            for u in self.units.values():
                unit_dir = u.get("unit_dir")
                if unit_dir is None:
                    continue
                name = Path(u["FragmentPath"]).name
                wanted = [p for pat in ("*.wants", "*.requires")
                          for p in Path(unit_dir).glob(f"{pat}/{name}")
                          if p.is_symlink()]
                u["UnitFileState"] = "enabled" if wanted else "linked"
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

    def linked_uploader(self, active="active", wants=("multi-user.target",),
                        requires=(), fragment_outside=False):
        """wspr-uploader as wspr-recorder's installer leaves it
        (``systemctl link``), then ``systemctl enable``d: the unit's own
        link in the scratch /etc/systemd/system, plus one enablement link
        per target in ``wants`` and ``requires``.  systemd 257 reports
        FragmentPath as the link's own path; ``fragment_outside`` reports
        the checkout's copy instead, with no link at /etc, as an older
        systemd may."""
        self.unit_dir = self.etc / "systemd" / "system"
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        self.unit_src = self.tmp / "opt" / UPLOADER
        self.unit_src.parent.mkdir(parents=True, exist_ok=True)
        self.unit_src.write_text("[Service]\nExecStart=/bin/true\n")
        self.unit_link = self.unit_dir / UPLOADER
        if not fragment_outside:
            self.unit_link.symlink_to(self.unit_src)
        links = []
        for kind, targets in (("wants", wants), ("requires", requires)):
            for t in targets:
                d = self.unit_dir / f"{t}.{kind}"
                d.mkdir(exist_ok=True)
                (d / UPLOADER).symlink_to(self.unit_src)
                links.append(d / UPLOADER)
        self.units[UPLOADER] = {
            "LoadState": "loaded", "ActiveState": active,
            "UnitFileState": "enabled" if links else "linked",
            "FragmentPath": str(self.unit_src if fragment_outside
                                else self.unit_link),
            "unit_dir": str(self.unit_dir)}
        return links

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

    def test_inactive_linked_with_unit_link_gets_no_call(self):
        """B4 and ND today: linked by the installer, never enabled."""
        self.linked_uploader(active="inactive", wants=())
        plan, res = self.lever(["wspr-recorder"])
        self.assertEqual(self.sc.verbs(), [])
        self.assertIn(UPLOADER, res.left_alone)
        self.assertTrue(self.unit_link.is_symlink())
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


class LinkedEnabledUploader(Base):
    """Task 28 F2: wspr-uploader linked by its installer and later enabled.
    `disable` would remove the unit's own link and lose the unit, so the
    lever removes only the enablement links, reloads systemd and stops the
    unit, and records each link it removed; the raise to upload puts back
    exactly those."""

    def record(self):
        return self.etc / L.UPLOADER_LINKS_RECORD

    def test_running_linked_enabled_never_disabled(self):
        links = self.linked_uploader(active="active")
        plan, res = self.lever(["wspr-recorder"])
        self.assertFalse(any(c[0] == "disable" for c in self.sc.calls),
                         self.sc.calls)
        self.assertEqual(self.sc.verbs(), [["daemon-reload"],
                                           ["stop", UPLOADER]])
        self.assertTrue(self.unit_link.is_symlink())          # the unit stays
        self.assertFalse(any(os.path.lexists(p) for p in links))
        self.assertEqual(res.unwanted, links)
        self.assertEqual(res.stopped, [UPLOADER])
        self.assertEqual(res.disabled, [])
        self.assertEqual(res.failures, [])
        u = self.sc.units[UPLOADER]
        self.assertEqual((u["LoadState"], u["UnitFileState"], u["ActiveState"]),
                         ("loaded", "linked", "inactive"))
        self.assertEqual(L.verify_levers(plan, systemctl=self.sc), [])
        self.assertIn(f"lever: removed {links[0]}", "\n".join(res.lines()))
        self.assertIn(f"lever: stopped {UPLOADER} (its unit file is a link: "
                      "its enablement links removed, the link kept)",
                      res.lines())
        self.assertEqual(self.record().read_text(),
                         f"{links[0]}\t{self.unit_src}\n")

    def test_stopped_linked_enabled_loses_its_enablement(self):
        links = self.linked_uploader(active="inactive")
        plan, res = self.lever(["wspr-recorder"])
        self.assertFalse(any(c[0] == "disable" for c in self.sc.calls))
        self.assertFalse(any(os.path.lexists(p) for p in links))
        self.assertTrue(self.unit_link.is_symlink())
        self.assertEqual(self.sc.units[UPLOADER]["UnitFileState"], "linked")
        self.assertEqual(L.verify_levers(plan, systemctl=self.sc), [])

    def test_wants_and_requires_go_other_units_links_stay(self):
        links = self.linked_uploader(wants=("multi-user.target",),
                                     requires=("hs-uploader.target",))
        other = self.unit_dir / "multi-user.target.wants" / "radiod@rx888.service"
        other.symlink_to("/nonexistent/radiod@.service")
        _, res = self.lever(["wspr-recorder"])
        self.assertEqual(sorted(res.unwanted), sorted(links))
        self.assertFalse(any(os.path.lexists(p) for p in links))
        self.assertTrue(other.is_symlink())

    def test_fragment_outside_the_unit_dirs_counts_as_linked(self):
        links = self.linked_uploader(fragment_outside=True)
        _, res = self.lever(["wspr-recorder"])
        self.assertFalse(any(c[0] == "disable" for c in self.sc.calls))
        self.assertEqual(res.unwanted, links)

    def test_plain_enabled_unit_keeps_disable_now(self):
        """Control: a unit file in the unit dirs, not a link."""
        self.uploader(active="active", state="enabled")
        self.units[UPLOADER]["FragmentPath"] = "/usr/lib/systemd/system/" + UPLOADER
        _, res = self.lever(["wspr-recorder"])
        self.assertIn(["disable", "--now", UPLOADER], self.sc.calls)
        self.assertEqual(res.unwanted, [])

    def test_no_record_no_removal(self):
        """A record that cannot be written removes nothing: the raise could
        never put the links back.  The unit still stops; the fill fails
        loudly."""
        links = self.linked_uploader(active="active")
        self.record().parent.mkdir(parents=True)
        self.record().mkdir()                       # not a file
        plan, res = self.lever(["wspr-recorder"])
        self.assertTrue(all(p.is_symlink() for p in links))
        self.assertFalse(any(c[0] == "disable" for c in self.sc.calls))
        self.assertIn(["stop", UPLOADER], self.sc.calls)
        self.assertTrue(any(x == UPLOADER and "record" in why
                            for x, why in res.failures), res.failures)
        self.assertTrue(any(UPLOADER in m for m in
                            L.verify_levers(plan, systemctl=self.sc)))

    def test_enabled_but_no_link_found_is_named(self):
        """A link that reads enabled through a link this lever does not
        look in (say, /run) is never disabled and is named."""
        self.linked_uploader(active="active", wants=())
        self.sc.units[UPLOADER]["UnitFileState"] = "enabled-runtime"
        _, res = self.lever(["wspr-recorder"])
        self.assertFalse(any(c[0] == "disable" for c in self.sc.calls))
        self.assertTrue(self.unit_link.is_symlink())
        self.assertTrue(any(x == UPLOADER for x, _ in res.failures),
                        res.failures)

    def test_second_run_touches_nothing(self):
        self.linked_uploader(active="active")
        self.lever(["wspr-recorder"])
        n = len(self.sc.verbs())
        _, res = self.lever(["wspr-recorder"])
        self.assertEqual(len(self.sc.verbs()), n)
        self.assertIn(UPLOADER, res.left_alone)

    def test_restore_puts_back_exactly_what_was_removed(self):
        links = self.linked_uploader(wants=("multi-user.target",),
                                     requires=("hs-uploader.target",))
        self.lever(["wspr-recorder"])
        self.sc.calls.clear()
        res = L.restore_uploader_links(roots=self.roots, systemctl=self.sc)
        self.assertEqual(sorted(res.restored), sorted(links))
        for p in links:
            self.assertTrue(p.is_symlink())
            self.assertEqual(Path(os.readlink(p)), self.unit_src)
        self.assertEqual(self.sc.verbs(), [["daemon-reload"]])
        self.assertEqual(self.sc.units[UPLOADER]["UnitFileState"], "enabled")
        self.assertFalse(os.path.lexists(self.record()))
        self.assertEqual(res.failures, [])
        self.assertIn(f"lever: restored {links[0]}", "\n".join(res.lines()))
        # it starts nothing: the operator's enable acts at the next boot
        self.assertEqual(self.sc.units[UPLOADER]["ActiveState"], "inactive")
        self.sc.calls.clear()
        again = L.restore_uploader_links(roots=self.roots, systemctl=self.sc)
        self.assertEqual((again.restored, again.failures), ([], []))
        self.assertEqual(self.sc.verbs(), [])

    def test_restore_after_two_fills_puts_back_both(self):
        first = self.linked_uploader(wants=("multi-user.target",))
        self.lever(["wspr-recorder"])
        later = self.unit_dir / "timers.target.wants" / UPLOADER
        later.parent.mkdir()
        later.symlink_to(self.unit_src)                 # enabled again
        self.sc.units[UPLOADER]["UnitFileState"] = "enabled"
        self.lever(["wspr-recorder"])
        self.assertFalse(os.path.lexists(later))
        res = L.restore_uploader_links(roots=self.roots, systemctl=self.sc)
        self.assertEqual(sorted(res.restored), sorted(first + [later]))

    def test_restore_refuses_a_record_line_outside_the_enablement_dirs(self):
        self.linked_uploader(wants=())
        self.record().parent.mkdir(parents=True)
        bad = self.unit_dir / UPLOADER            # the unit's own link path
        self.unit_link.unlink()
        self.record().write_text(f"{bad}\t/tmp/evil.service\n"
                                 f"{self.tmp}/x.wants/{UPLOADER}\t{self.unit_src}\n")
        res = L.restore_uploader_links(roots=self.roots, systemctl=self.sc)
        self.assertEqual(res.restored, [])
        self.assertEqual(len(res.failures), 2, res.failures)
        self.assertFalse(os.path.lexists(bad))
        self.assertTrue(self.record().exists())      # kept for the operator
        self.assertEqual(self.sc.verbs(), [])

    def test_restore_with_no_record_does_nothing(self):
        res = L.restore_uploader_links(roots=self.roots, systemctl=self.sc)
        self.assertEqual((res.restored, res.failures, res.lines()), ([], [], []))
        self.assertEqual(self.sc.calls, [])


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
    cmd_restart, and only the verbs restart, disable --now, show, stop
    (wspr-uploader alone) and daemon-reload."""

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
                    "mask", "isolate", "reset-failed"):
            self.assertNotIn(bad, consts)
        # every systemctl call goes through _systemctl with a literal verb
        verbs = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_systemctl":
                first = node.args[1]
                self.assertIsInstance(first, ast.Constant)
                verbs.append(first.value)
        self.assertEqual(set(verbs), {"restart", "disable", "show", "stop",
                                      "daemon-reload"})
        # C27-2: `stop` passes for wspr-uploader alone; Task 28 F2:
        # `daemon-reload` alone, with no argument, after the lever removes
        # or restores wspr-uploader's enablement links
        self.assertEqual(L.ALLOWED_VERBS,
                         {("restart",), ("disable", "--now"), ("show",),
                          ("stop", L.WSPR_UPLOADER), ("daemon-reload",)})
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
        with self.assertRaises(ValueError):
            L._systemctl(sc, "daemon-reload", "x.service")
        self.assertEqual(sc.calls, [])


if __name__ == "__main__":
    unittest.main()
