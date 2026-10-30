"""Tests for sigmond-sdr-recover — RX888 port-reset recovery.

The RX-888 recurrently drops off the USB bus, or stays on it wedged, and a
reset of its hub port recovers it.  This helper switches the port off and on
(a port reset; whether the hub also drops VBUS is unverified), then
brings the station back in the one order that works: radiod first, then the
units that consume its RTP streams.

Two failure modes shape every test here, and both were observed on AC0G-B4:

  * A watchdog that fires on a downstream symptom restarts things forever
    without fixing anything.  timestd-hpps-watchdog restarted the recorder
    four times overnight on 2026-08-29 against a stale chrony refclock,
    while the actual cause was low C/N0.  So this helper fires ONLY on the
    device being absent from the bus, and never on a consequence of that.

  * A consumer left running against a restarted radiod fails SILENTLY.
    wspr-recorder ran two days against a stale RTP anchor, throwing a
    timing fault every two minutes that nothing acted on.  So the consumer
    set is declared, not discovered by an ad-hoc glob typed at the time.
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load():
    loader = importlib.machinery.SourceFileLoader(
        "sdr_recover_under_test", str(REPO / "bin" / "sigmond-sdr-recover"))
    spec = importlib.util.spec_from_file_location(
        "sdr_recover_under_test", str(REPO / "bin" / "sigmond-sdr-recover"),
        loader=loader)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: dataclasses resolves field types through
    # sys.modules, so a module-level @dataclass raises AttributeError if the
    # module is not yet there.
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


sdr = _load()


class TestShouldCycle(unittest.TestCase):
    """The trigger. Narrow on purpose."""

    def test_cycles_when_device_absent_long_enough(self):
        d = sdr.should_cycle(device_present=False, absent_for_s=120,
                             last_cycle_age_s=None)
        self.assertTrue(d.cycle)
        self.assertIn("absent", d.reason)

    def test_never_cycles_while_the_device_is_present(self):
        """Whatever else is wrong, a present device is not this tool's problem.
        Cycling a healthy card because something downstream looks unhappy is
        the hpps-watchdog failure repeated."""
        d = sdr.should_cycle(device_present=True, absent_for_s=0,
                             last_cycle_age_s=None)
        self.assertFalse(d.cycle)

    def test_waits_out_a_brief_absence(self):
        """A re-enumeration blips the device off the bus for a second or two.
        Cycling then would interrupt its own recovery."""
        d = sdr.should_cycle(device_present=False, absent_for_s=5,
                             last_cycle_age_s=None)
        self.assertFalse(d.cycle)
        self.assertIn("grace", d.reason)

    def test_rate_limits_repeat_cycles(self):
        """If a cycle did not fix it, cycling again immediately will not
        either — and a power-cycle loop is worse than a dead SDR."""
        d = sdr.should_cycle(device_present=False, absent_for_s=600,
                             last_cycle_age_s=30)
        self.assertFalse(d.cycle)
        self.assertIn("cooldown", d.reason)

    def test_cycles_again_once_the_cooldown_expires(self):
        d = sdr.should_cycle(device_present=False, absent_for_s=1800,
                             last_cycle_age_s=sdr.CYCLE_COOLDOWN_S + 1)
        self.assertTrue(d.cycle)


class TestConsumerExpansion(unittest.TestCase):
    """The declared set. A glob typed by hand is how wspr-recorder got missed."""

    INSTALLED = [
        "wspr-recorder@AC0G\\x3dB4.service",
        "psk-recorder@AC0G\\x3dB4.service",
        "timestd-core-recorder.service",
        "timestd-metrology@SHARED_5000.service",
        "timestd-metrology@WWV_20000.service",
        "mag-recorder.service",
        "timestd-web-api.service",
    ]

    def test_expands_instance_patterns(self):
        got = sdr.expand_consumers(["wspr-recorder@*.service"], self.INSTALLED)
        self.assertEqual(got, ["wspr-recorder@AC0G\\x3dB4.service"])

    def test_expands_a_template_to_every_instance(self):
        got = sdr.expand_consumers(["timestd-metrology@*.service"], self.INSTALLED)
        self.assertEqual(len(got), 2)

    def test_preserves_declared_order(self):
        """radiod's consumers are restarted in the declared order; a set or a
        dict comprehension would silently reorder them."""
        got = sdr.expand_consumers(
            ["timestd-core-recorder.service", "wspr-recorder@*.service"],
            self.INSTALLED)
        self.assertEqual(got[0], "timestd-core-recorder.service")

    def test_omits_units_that_do_not_consume_radiod(self):
        got = sdr.expand_consumers(
            ["wspr-recorder@*.service", "psk-recorder@*.service"], self.INSTALLED)
        self.assertNotIn("mag-recorder.service", got)
        self.assertNotIn("timestd-web-api.service", got)

    def test_a_pattern_matching_nothing_is_reported_not_swallowed(self):
        """A consumer that stops matching — renamed unit, changed instance —
        must surface. Silently restarting fewer things is the failure this
        whole module exists to prevent."""
        got, missing = sdr.expand_consumers_checked(
            ["wspr-recorder@*.service", "hfdl-recorder@*.service"], self.INSTALLED)
        self.assertEqual(missing, ["hfdl-recorder@*.service"])
        self.assertEqual(len(got), 1)


if __name__ == "__main__":
    unittest.main()


class TestDiscovery(unittest.TestCase):
    """Learning the hub port while the card is present (AI6VN 2026-09-05)."""

    def test_devpath_behind_a_hub(self):
        self.assertEqual(sdr.split_devpath("4-1.4"), ("4-1", "4"))

    def test_devpath_behind_a_nested_hub(self):
        self.assertEqual(sdr.split_devpath("4-1.2.3"), ("4-1.2", "3"))

    def test_devpath_straight_on_the_root_hub_names_the_bus(self):
        self.assertEqual(sdr.split_devpath("6-2"), ("6", "2"))

    def test_interfaces_and_root_hubs_are_not_devices(self):
        self.assertIsNone(sdr.split_devpath("4-1.4:1.0"))
        self.assertIsNone(sdr.split_devpath("usb4"))
        self.assertIsNone(sdr.split_devpath(""))

    def test_uhubctl_listing_the_port_means_switchable(self):
        out = ("Current status for hub 4-1 [17ef:1039 USB3.0 Hub, USB 3.00, "
               "4 ports, ppps]\n  Port 4: 0203 power 5gbps U0 enable connect "
               "[04b4:00f1 RX888mk2]\n")
        self.assertTrue(sdr.parse_uhubctl(out, "4"))

    def test_uhubctl_with_no_compatible_hub_means_not_switchable(self):
        out = "No compatible devices detected!\nRun with -h to get usage info.\n"
        self.assertFalse(sdr.parse_uhubctl(out, "4"))

    def test_uhubctl_permission_problem_is_unknown_not_false(self):
        """Not being allowed to look must never be reported as 'cannot
        switch' — that would tell an operator to move a card that is fine."""
        out = ("There were permission problems while accessing USB.\n"
               "No compatible devices detected!\n")
        self.assertIsNone(sdr.parse_uhubctl(out, "4"))

    def test_locate_reads_the_card_out_of_sysfs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, vid, pid in (("2-1", "17ef", "103a"),
                                   ("4-1", "17ef", "1039"),
                                   ("4-1.4", "04b4", "00f1")):
                d = root / name
                d.mkdir()
                (d / "idVendor").write_text(vid + "\n")
                (d / "idProduct").write_text(pid + "\n")
            (root / "4-1.4" / "serial").write_text("000908250A081311\n")
            (root / "4-1.4" / "speed").write_text("5000\n")
            (root / "4-1.4:1.0").mkdir()          # interface dir, no ids
            loc = sdr.locate_rx888(root)
        self.assertEqual((loc.hub, loc.port, loc.serial, loc.speed),
                         ("4-1", "4", "000908250A081311", "5000"))

    def test_peer_half_of_a_usb3_hub_port(self):
        """uhubctl refused `-l 4-1` on the Lenovo dock but took `-l 2-1`, the
        USB2 companion sysfs names as the port's peer (AI6VN 2026-09-05)."""
        import os, tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portdir = root / "4-1" / "4-1:1.0" / "4-1-port4"
            portdir.mkdir(parents=True)
            os.symlink("../../../../usb2/2-1/2-1:1.0/2-1-port4", portdir / "peer")
            self.assertEqual(sdr.peer_of("4-1.4", root), ("2-1", "4"))
            self.assertIsNone(sdr.peer_of("6-2", root))

    def test_locate_with_no_card_is_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "usb1").mkdir()
            self.assertIsNone(sdr.locate_rx888(Path(td)))


class _CP:
    """Stand-in for subprocess.CompletedProcess, enough for _run() callers."""
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


# ── bootstrap: recovering a card that was NEVER seen ────────────────────────
# A fresh install has no learned hub/port, so the helper used to refuse every
# run ("no hub/port to cycle") while the card sat latched on a hub port --
# AI6VN on v3.40, 2026-09-17.  A latched RX-888 is visible as a port with no
# device on it, so those are the bootstrap candidates.

_UHUBCTL_AI6VN = """Current status for hub 4-1 [17ef:1039 VIA Labs, Inc. USB3.0 Hub 000000000, USB 3.10, 4 ports, ppps]
  Port 1: 02a0 power 5gbps Rx.Detect
  Port 2: 02a0 power 5gbps Rx.Detect
  Port 3: 02a0 power 5gbps Rx.Detect
  Port 4: 02a0 power 5gbps Rx.Detect
Current status for hub 2-1 [17ef:103a VIA Labs, Inc. USB2.0 Hub 000000000, USB 2.10, 5 ports, ppps]
  Port 1: 0103 power enable connect [239a:801e Adafruit Trinket M0 650670C7]
  Port 2: 0103 power enable connect [1dd2:2211 Leo Bodnar Electronics mini GPS Reference Clock 9DC7A57A42]
  Port 3: 0103 power enable connect [1ffb:2503 Pololu Corporation Pololu Isolated USB-to-I2C Adapter 7A00]
  Port 4: 0101 power connect []
  Port 5: 0503 power highspeed enable connect [17ef:103b VIA Labs, Inc. USB Billboard Device 0000]
Current status for hub 3 [1d6b:0002 Linux 6.1 xhci-hcd, USB 2.00, 1 ports]
  Port 1: 0100 power
"""


def test_bootstrap_picks_only_device_less_ports(monkeypatch):
    """The latched port and the SuperSpeed half, and nothing that is in use."""
    mod = _load()
    monkeypatch.setattr(mod, "_run", lambda *a, **k: _CP(_UHUBCTL_AI6VN))
    # every candidate is addressable in its plain form for this test
    monkeypatch.setattr(mod, "_uhubctl_sees", lambda h, p, e: not e)
    got = mod.bootstrap_locations()
    assert ("2-1", "4", False) in got, "the latched port must be a candidate"
    assert all(h == "4-1" for h, p, e in got if h != "2-1")
    # the working peripherals are never cycled
    for busy in ("1", "2", "3", "5"):
        assert not any(h == "2-1" and p == busy for h, p, _ in got), \
            f"2-1 port {busy} has a device on it and must never be cycled"


def test_bootstrap_skips_hubs_without_power_switching(monkeypatch):
    """A hub with no ppps flag cannot switch power, so it is not a candidate."""
    mod = _load()
    monkeypatch.setattr(mod, "_run", lambda *a, **k: _CP(_UHUBCTL_AI6VN))
    monkeypatch.setattr(mod, "_uhubctl_sees", lambda h, p, e: not e)
    assert not any(h == "3" for h, p, e in mod.bootstrap_locations())


def test_bootstrap_uses_exact_when_plain_is_refused(monkeypatch):
    """uhubctl rejects some USB3 hubs' own location unless told --exact; a
    cycle whose commands all fail leaves the card latched while looking fine."""
    mod = _load()
    monkeypatch.setattr(mod, "_run", lambda *a, **k: _CP(_UHUBCTL_AI6VN))
    # 4-1 answers only with exact=True, 2-1 only with exact=False
    monkeypatch.setattr(mod, "_uhubctl_sees",
                        lambda h, p, e: (h == "4-1") == bool(e))
    got = mod.bootstrap_locations()
    assert ("4-1", "4", True) in got
    assert ("2-1", "4", False) in got


# ─── final review / I-3: restart_station must run under the lifecycle lock ──
# `smd admin radiod restart-stale-consumers` and this helper's restart_station
# both restart radiod's consumers; without a shared lock they can race and
# double-restart the same unit.  sdr-recover holds smd's own lifecycle lock
# (the same file sigmond.lifecycle.LIFECYCLE_LOCK names) around the call —
# waiting, never failing: a vanished card must still be recovered even if smd
# is mid-operation, so a lock that stays busy through the deadline is logged
# loudly and the recovery proceeds without it.

def test_lifecycle_lock_is_held_around_restart_station(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setattr(mod, "LIFECYCLE_LOCK", tmp_path / "lifecycle.lock")
    events = []
    real_flock = mod.fcntl.flock

    def spy_flock(fd, op):
        if op & mod.fcntl.LOCK_UN:
            events.append("unlocked")
        elif op & mod.fcntl.LOCK_EX:
            events.append("locked")
        return real_flock(fd, op)

    monkeypatch.setattr(mod.fcntl, "flock", spy_flock)
    monkeypatch.setattr(mod, "_run", lambda cmd, timeout=60: _CP(returncode=0))

    with mod._with_lifecycle_lock():
        events.append("restart_station")
        ok = mod.restart_station("radiod@x.service", [])

    assert ok
    assert events == ["locked", "restart_station", "unlocked"]


def test_proceeds_without_the_lock_if_still_busy_at_the_deadline(monkeypatch, tmp_path, capsys):
    import fcntl as _fcntl
    import os as _os

    mod = _load()
    lock_path = tmp_path / "lifecycle.lock"
    monkeypatch.setattr(mod, "LIFECYCLE_LOCK", lock_path)
    monkeypatch.setattr(mod, "LIFECYCLE_LOCK_WAIT_S", 0)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    other_fd = _os.open(str(lock_path), _os.O_CREAT | _os.O_RDWR, 0o644)
    _fcntl.flock(other_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    try:
        entered = False
        with mod._with_lifecycle_lock():
            entered = True
        assert entered
    finally:
        _fcntl.flock(other_fd, _fcntl.LOCK_UN)
        _os.close(other_fd)

    out = capsys.readouterr().out
    assert "proceeding" in out.lower()


class DetectRadiodUnitTests(unittest.TestCase):
    """⛔ The resolver must work when radiod is DOWN — that is the whole case.

    This helper runs because the RX-888 vanished, and radiod cannot serve
    without the card, so it is never `active` then: it sits in
    activating/auto-restart, or failed.  The resolver used to filter on
    `--state=active`, so it returned nothing and the helper exited — once a
    minute, indefinitely, in exactly the situation it exists for.

    AI6VN, 2026-09-29: card gone, radiod@AI6VN in auto-restart with
    NRestarts=15, hub and port already learned and persisted, uhubctl present,
    the hub advertising per-port power switching — and every run logged
    "not exactly one active radiod@ instance" and did nothing.
    """

    ACTIVE = "radiod@AI6VN.service   loaded active running AI6VN radio receiver\n"
    RESTARTING = ("radiod@AI6VN.service   loaded activating auto-restart "
                  "AI6VN radio receiver\n")
    TWO = (RESTARTING +
           "radiod@OTHER.service   loaded active running OTHER radio receiver\n")

    def _resolve(self, by_state):
        """by_state: {tuple(extra_args): stdout}"""
        calls = []

        class R:
            def __init__(self, out): self.stdout = out

        def fake_run(cmd, *a, **k):
            extra = tuple(x for x in cmd if x.startswith("--state") or x == "--all")
            calls.append(extra)
            return R(by_state.get(extra, ""))

        orig = sdr._run
        sdr._run = fake_run
        try:
            return sdr.detect_radiod_unit(), calls
        finally:
            sdr._run = orig

    def test_single_active_instance_is_used(self):
        unit, _ = self._resolve({("--state=active",): self.ACTIVE})
        self.assertEqual(unit, "radiod@AI6VN.service")

    def test_restarting_instance_is_found_when_none_is_active(self):
        """The regression: a card-less radiod is activating, never active."""
        unit, calls = self._resolve({
            ("--state=active",): "",            # nothing active — card is gone
            ("--all",): self.RESTARTING,
        })
        self.assertEqual(unit, "radiod@AI6VN.service")
        self.assertIn(("--all",), calls, "must fall back past --state=active")

    def test_active_is_preferred_over_the_broader_query(self):
        unit, _ = self._resolve({
            ("--state=active",): self.ACTIVE,
            ("--all",): self.TWO,
        })
        self.assertEqual(unit, "radiod@AI6VN.service")

    def test_two_instances_stay_ambiguous(self):
        """Guessing which radiod to restart is worse than asking topology."""
        unit, _ = self._resolve({("--state=active",): "", ("--all",): self.TWO})
        self.assertIsNone(unit)

    def test_no_instances_at_all(self):
        unit, _ = self._resolve({})
        self.assertIsNone(unit)


class PowerOnMustNotLeavePortDeadTests(unittest.TestCase):
    """⛔ A failed `on` is worse than never cycling.

    The port stays unpowered and the card cannot return until a human reaches
    the hub — the exact outcome this helper exists to avoid.  On AI6VN
    (2026-09-29) the `--exact` form of `on` HUNG and was killed at 60 s
    (rc=124) while the plain form returned instantly, so a single-form attempt
    could have left the port dark.
    """

    def _cycle(self, results):
        """results: {(port, action, exact): returncode} → (ok, calls, log)"""
        calls, log = [], []

        class R:
            def __init__(self, rc): self.returncode = rc; self.stdout = ""; self.stderr = "boom"

        def fake_run(cmd, *a, **k):
            exact = "-e" in cmd
            action = cmd[cmd.index("-a") + 1]
            port = cmd[cmd.index("-p") + 1]
            calls.append((port, action, exact))
            return R(results.get((port, action, exact), 0))

        orig_run, orig_sleep, orig_log = sdr._run, sdr.time.sleep, sdr._log
        sdr._run = fake_run
        sdr.time.sleep = lambda *_: None
        sdr._log = lambda m: log.append(m)
        try:
            ok = sdr.cycle_port([("5-1", "4", True)], off_seconds=6)
        finally:
            sdr._run, sdr.time.sleep, sdr._log = orig_run, orig_sleep, orig_log
        return ok, calls, log

    def test_exact_on_that_hangs_is_retried_plain(self):
        ok, calls, log = self._cycle({("4", "on", True): 124})
        self.assertTrue(ok, "a working fallback must count as success")
        self.assertIn(("4", "on", False), calls, "must retry the other form")
        self.assertTrue(any("switched back on" in m for m in log))

    def test_both_forms_failing_is_reported_loudly(self):
        ok, _, log = self._cycle({("4", "on", True): 124, ("4", "on", False): 1})
        self.assertFalse(ok)
        self.assertTrue(any("PORT LEFT OFF" in m for m in log),
                        "a dark port must be stated, not buried in an rc")

    def test_a_clean_cycle_does_not_retry(self):
        ok, calls, _ = self._cycle({})
        self.assertTrue(ok)
        self.assertEqual([c for c in calls if c[1] == "on"], [("4", "on", True)])


class StaleBusPathTests(unittest.TestCase):
    """⛔ Learned locations are BUS PATHS, and bus paths move across a reboot.

    AI6VN, 2026-09-29.  The card was learned at hub 4-1 port 4 while 4-1 WAS
    the USB3 hub.  After a reboot the buses renumbered — 4-1 became the USB2
    hub, and the card's real home became 5-1 — and because the card had already
    vanished before the system came up, there was never a moment with it
    present in which to re-learn.  The helper cut power on the USB2 hub's port
    4, which was EMPTY, while the card sat on the SuperSpeed half untouched.
    rob replugged by hand and it returned instantly at 5 Gbps.

    So an absent card must cycle the learned path AND every device-less port.
    """

    SRC = (REPO / "bin" / "sigmond-sdr-recover").read_text()

    def test_absent_card_unions_the_learned_path_with_bootstrap(self):
        self.assertIn("extra = [l for l in bootstrap_locations() "
                      "if l not in targets]", self.SRC)

    def test_the_union_happens_only_when_the_card_is_absent(self):
        """A present card is never cycled — that rule is not relaxed here."""
        i = self.SRC.index("targets = list(locations or [])")
        self.assertIn("if not present:", self.SRC[i:i + 200])

    def test_bootstrap_still_refuses_ports_with_devices(self):
        """The union is only safe because bootstrap skips populated ports."""
        self.assertIn("if dev and dev.group(1).strip():           "
                      "# a real device -- never touch", self.SRC)

class MissingHubIsNotADarkPortTests(unittest.TestCase):
    """⛔ "That hub is not here" must never be reported as "I left power off".

    Bus paths renumber across boots — AI6VN saw three different numberings in
    three boots (4-1/3-1, then 5-1/4-1, then 5-1/3-1).  A stale location
    therefore routinely names a hub that does not exist this boot, uhubctl says
    "No compatible devices detected at location 4-1", and NOTHING was switched
    off there.  Reporting that as PORT LEFT OFF sent rob to the bench to look
    at an RX-888 that was powered and streaming (2026-09-29):

        PORT LEFT OFF on hub 4-1 port 4 — both forms failed (rc=1)
        hub 5-1 Port 4: 0203 power 5gbps U0 enable connect [RX888mk2]
    """

    def _cycle(self, locations, results):
        log = []

        class R:
            def __init__(self, rc, err=""):
                self.returncode = rc; self.stdout = ""; self.stderr = err

        def fake_run(cmd, *a, **k):
            hub = cmd[cmd.index("-l") + 1]
            action = cmd[cmd.index("-a") + 1]
            rc, err = results.get((hub, action), (0, ""))
            return R(rc, err)

        orig_run, orig_sleep, orig_log = sdr._run, sdr.time.sleep, sdr._log
        sdr._run, sdr.time.sleep, sdr._log = fake_run, (lambda *_: None), log.append
        try:
            ok = sdr.cycle_port(locations, off_seconds=6)
        finally:
            sdr._run, sdr.time.sleep, sdr._log = orig_run, orig_sleep, orig_log
        return ok, log

    ABSENT = (1, "No compatible devices detected at location 4-1!")

    def test_absent_hub_is_not_reported_as_power_left_off(self):
        ok, log = self._cycle([("4-1", "4", False)],
                              {("4-1", "off"): self.ABSENT})
        self.assertFalse(any("PORT LEFT OFF" in m for m in log),
                         "a hub that is not on the bus left nothing dark")

    def test_absent_hub_does_not_fail_the_cycle(self):
        """The union with device-less ports is what finds the card now."""
        ok, _ = self._cycle([("4-1", "4", False), ("5-1", "4", True)],
                            {("4-1", "off"): self.ABSENT})
        self.assertTrue(ok)

    def test_absent_hub_is_explained_not_just_dumped(self):
        _, log = self._cycle([("4-1", "4", False)],
                             {("4-1", "off"): self.ABSENT})
        self.assertTrue(any("not on this bus" in m for m in log))

    def test_a_port_we_really_switched_off_still_reports_when_left_dark(self):
        """The real warning must survive — it is the one that needs a human."""
        _, log = self._cycle([("5-1", "4", True)],
                             {("5-1", "on"): (124, "timeout")})
        self.assertTrue(any("PORT LEFT OFF" in m for m in log))

    def test_on_is_not_attempted_for_a_hub_that_was_never_off(self):
        calls = []

        class R:
            def __init__(self, rc, err=""):
                self.returncode = rc; self.stdout = ""; self.stderr = err

        def fake_run(cmd, *a, **k):
            hub = cmd[cmd.index("-l") + 1]; action = cmd[cmd.index("-a") + 1]
            calls.append((hub, action))
            if hub == "4-1" and action == "off":
                return R(*self.ABSENT)
            return R(0)

        orig_run, orig_sleep, orig_log = sdr._run, sdr.time.sleep, sdr._log
        sdr._run, sdr.time.sleep, sdr._log = fake_run, (lambda *_: None), (lambda m: None)
        try:
            sdr.cycle_port([("4-1", "4", False)], off_seconds=1)
        finally:
            sdr._run, sdr.time.sleep, sdr._log = orig_run, orig_sleep, orig_log
        self.assertNotIn(("4-1", "on"), calls,
                         "do not try to power on a hub that is not there")



class RecoveryTimerScheduleTests(unittest.TestCase):
    """⛔ The card is most often missing at COLD BOOT, so the first check must
    not be three minutes late.

    AI6VN, 2026-09-29, uncontrolled power cycle:

        20:07:07  VM booted
        20:08:20  radiod's first attempt — rx888_usb_init() failed
        20:10:07  this timer's FIRST RUN        <- OnBootSec=3min
        20:11:07  "device absent 60s — cutting VBUS"
        20:12:19  station restored

    6 min 5 s from power-on, of which the recovery itself was 72 s.  Nothing
    watched for the 107 s between radiod failing and the first check, and the
    60 s tick turned a 30 s grace window into a 60 s wait.  rob: "why did it
    take so long to come up."

    Checking sooner cannot cycle a healthy card: ABSENT_GRACE_S is what
    protects a card that is merely slow to enumerate, not this schedule.
    """

    TIMER = (Path(__file__).resolve().parent.parent
             / "systemd" / "sigmond-sdr-recover.timer")

    def _val(self, key):
        for line in self.TIMER.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1]
        return None

    def test_first_check_is_not_minutes_late(self):
        v = self._val("OnBootSec")
        self.assertEqual(v, "45s",
                         "a 3min first check left radiod failing unwatched")

    def test_tick_is_finer_than_the_grace_window(self):
        """A 60s tick makes a 30s grace window behave like 60s."""
        tick = self._val("OnUnitActiveSec")
        self.assertEqual(tick, "30s")
        self.assertLessEqual(int(tick.rstrip("s")), sdr.ABSENT_GRACE_S)

    def test_the_grace_window_still_guards_a_slow_card(self):
        """Tightening the schedule must not remove the real protection."""
        self.assertGreaterEqual(sdr.ABSENT_GRACE_S, 30)


class PeerHalfIsNotAnEmptyPortTests(unittest.TestCase):
    """⛔ A port is not empty just because THIS half of it is.

    A USB3 hub presents every physical port twice.  A USB2-only device shows up
    on the SuperSpeed half as an empty port — while sharing that port's VBUS.
    Cutting the "empty" half cuts the device.

    AI6VN, 2026-09-29: recovering the RX-888 cut 5-1 ports 1, 2 and 3 because
    uhubctl listed them empty, and took the GPSDO, the TS-1 and the
    magnetometer with them — all three live on physical ports 1-3 and enumerate
    on the USB2 half (3-1).  rob watched them drop.  They returned after the
    ~6 s cut, but knocking a GPSDO off its lock on every recovery is not
    acceptable collateral for recovering a different device.
    """

    UHUBCTL = (
        "Current status for hub 5-1 [17ef:1039 VIA Labs USB3.0 Hub, USB 3.10, 4 ports, ppps]\n"
        "  Port 1: 02a0 power 5gbps Rx.Detect\n"
        "  Port 2: 02a0 power 5gbps Rx.Detect\n"
        "  Port 3: 02a0 power 5gbps Rx.Detect\n"
        "  Port 4: 0100 power\n"
        "Current status for hub 3-1 [17ef:103a VIA Labs USB2.0 Hub, USB 2.10, 5 ports, ppps]\n"
        "  Port 1: 0103 power enable connect [1dd2:2211 Leo Bodnar mini GPS Reference Clock]\n"
        "  Port 2: 0103 power enable connect [239a:801e Adafruit Trinket M0]\n"
        "  Port 3: 0103 power enable connect [1ffb:2503 Pololu Isolated USB-to-I2C]\n"
        "  Port 4: 0000 off\n"
    )
    # physical ports 1-3 are paired; port 4 holds the RX-888 (USB3, so its
    # USB2 half is genuinely empty)
    PEERS = {("5-1", "1"): ("3-1", "1"), ("5-1", "2"): ("3-1", "2"),
             ("5-1", "3"): ("3-1", "3"), ("5-1", "4"): ("3-1", "4"),
             ("3-1", "4"): ("5-1", "4")}

    def _bootstrap(self):
        class R:
            stdout = PeerHalfIsNotAnEmptyPortTests.UHUBCTL
            stderr = ""
            returncode = 0

        def fake_peer(devpath, root=None):
            hp = sdr.split_devpath(devpath)
            return self.PEERS.get(hp) if hp else None

        orig = (sdr._run, sdr.peer_of, sdr._uhubctl_sees, sdr._log)
        sdr._run = lambda *a, **k: R()
        sdr.peer_of = fake_peer
        sdr._uhubctl_sees = lambda h, p, e: not e     # plain form works
        sdr._log = lambda m: None
        try:
            return sdr.bootstrap_locations()
        finally:
            sdr._run, sdr.peer_of, sdr._uhubctl_sees, sdr._log = orig

    def test_does_not_cut_the_gpsdo_s_superspeed_half(self):
        got = {(h, p) for h, p, _ in self._bootstrap()}
        for port in ("1", "2", "3"):
            self.assertNotIn(("5-1", port), got,
                             f"5-1 port {port} shares VBUS with a live device")

    def test_still_finds_the_card_s_own_port(self):
        """The whole point — the RX-888's port must stay in the set."""
        got = {(h, p) for h, p, _ in self._bootstrap()}
        self.assertTrue(("5-1", "4") in got or ("3-1", "4") in got,
                        "the vanished card's port must still be cycled")


class EpisodeAndQuietTests(unittest.TestCase):
    """v3.71.  A power cut leaves one event for `smd doctor` to read, and a
    card that stays away says "holding" once per change.  This helper runs
    every 30 s, and the old line on every tick made two lines a minute for
    hours."""

    CFG = {"recover_hub": "1-1", "recover_port": "2",
           "unit": "radiod@ac0g.service", "consumers": []}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.log, self.events = [], []
        self.present, self.returns = False, True
        self.t = 1_760_000_000.0
        self._saved = {}
        stand_ins = {
            "STATE_PATH": self.tmp / "sdr-recover.json",
            "read_config": lambda: dict(self.CFG),
            "device_present": lambda: self.present,
            "bootstrap_locations": lambda: [],
            "cycle_port": lambda targets, off: True,
            "wait_for_device": lambda timeout_s=90: self.returns,
            "installed_units": lambda: [],
            "restart_station": lambda unit, consumers, **kw: True,
            "_with_lifecycle_lock": contextlib.nullcontext,
            "_log": self.log.append,
            "note_event": lambda kind, detail: self.events.append((kind, detail)),
            "radiod_is_wedged": lambda unit, state, now, **k: False,
            "locate_rx888": lambda *a, **k: sdr.Located(
                "1-1.2", "1-1", "2", "S", "5000", product="00f1"),
            "kernel_latch_errors": lambda since, prefixes=None: [],
            "foreign_devices": lambda targets, **k: [],
        }
        for name, value in stand_ins.items():
            self._saved[name] = getattr(sdr, name)
            setattr(sdr, name, value)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(sdr, name, value)

    def tick(self, *argv, dt=30):
        with mock.patch.object(sdr.time, "time", return_value=self.t), \
             mock.patch.object(sdr.time, "sleep", lambda s: None):
            rc = sdr.main(list(argv))
        self.t += dt
        return rc

    def holding(self):
        return [m for m in self.log if m.startswith("holding:")]

    def test_a_power_cut_leaves_one_event_naming_the_port(self):
        self.tick()            # absent: the grace window opens
        self.tick()            # 30 s later: cut
        self.assertEqual(self.events, [("port-reset", "1-1p2")])

    def test_a_dry_run_records_nothing(self):
        for _ in range(3):
            self.tick("--dry-run")
        self.assertEqual(self.events, [])

    def test_a_present_card_records_nothing_and_says_nothing(self):
        self.present = True
        for _ in range(5):
            self.tick()
        self.assertEqual(self.events, [])
        self.assertEqual(self.holding(), [])

    def test_a_card_that_stays_away_says_holding_once_per_change(self):
        self.returns = False
        for _ in range(22):    # 11 minutes: the grace window, one cut, then cooldown
            self.tick()
        self.assertEqual([k for k, _ in self.events], ["port-reset", "needs-replug"])
        h = self.holding()
        self.assertEqual(len(h), 2, h)
        self.assertIn("grace", h[0])
        self.assertIn("hand replug", h[1])

    def test_holding_speaks_again_after_the_card_came_back_and_left(self):
        self.tick()            # absent, grace: holding
        self.present = True
        self.tick()            # back
        self.present = False
        self.tick()            # absent again: holding again
        self.assertEqual(len(self.holding()), 2)

    def test_a_dry_run_still_answers_every_time(self):
        for _ in range(3):
            self.tick("--dry-run", dt=5)
        self.assertEqual(len(self.holding()), 3)

    def test_an_unswitchable_hub_says_so_once(self):
        sdr.read_config = lambda: {"unit": "radiod@ac0g.service", "consumers": []}
        (self.tmp / "sdr-recover.json").write_text(json.dumps(
            {"hub": "1-1", "port": "2", "switchable": False,
             "locations": [["1-1", "2", False]]}))
        for _ in range(5):
            self.tick()
        said = [m for m in self.log if "cannot be switched" in m]
        self.assertEqual(len(said), 1, self.log)

    def test_a_record_that_fails_never_fails_the_run(self):
        sdr.note_event = self._saved["note_event"]     # the real one
        with mock.patch.object(sdr.sdr_events, "record", side_effect=OSError("read-only")):
            self.tick()
            rc = self.tick()
        self.assertEqual(rc, 0)
        self.assertTrue(any("could not record the port-reset" in m for m in self.log), self.log)

    def _no_hub(self):
        sdr.read_config = lambda: {"unit": "radiod@ac0g.service", "consumers": []}

    def _unswitchable(self):
        self._no_hub()
        (self.tmp / "sdr-recover.json").write_text(json.dumps(
            {"hub": "1-1", "port": "2", "switchable": False,
             "locations": [["1-1", "2", False]]}))

    def _two_dry_runs_then_a_real_one(self, needle):
        for _ in range(2):
            self.tick("--dry-run")
        self.assertEqual(len([m for m in self.log if needle in m]), 2, self.log)
        self.tick()
        self.tick()
        self.assertEqual(len([m for m in self.log if needle in m]), 3, self.log)

    def test_a_dry_run_answers_every_time_for_an_unswitchable_hub(self):
        self._unswitchable()
        self._two_dry_runs_then_a_real_one("cannot be switched")

    def test_a_dry_run_answers_every_time_with_no_hub_to_cycle(self):
        self._no_hub()
        self._two_dry_runs_then_a_real_one("no hub/port to cycle")


# ─── v3.72 #13: restart_station skips a consumer radiod's restart already freshened ──

def _station_run(monkeypatch, mod, mono, stopped=()):
    """Fake _run: ActiveEnterTimestampMonotonic per unit from ``mono`` (secs)."""
    calls = []

    def fake(cmd, timeout=60):
        calls.append(list(cmd))
        if cmd[:2] == ["systemctl", "show"]:
            secs = mono.get(cmd[-1])
            us = 0 if secs is None else int(secs * 1e6)
            state = "active" if secs is not None and cmd[-1] not in stopped else "inactive"
            return _CP(stdout=f"ActiveState={state}\nActiveEnterTimestampMonotonic={us}\n")
        if cmd[:2] == ["systemctl", "is-active"]:
            return _CP(stdout="active\n")
        return _CP()
    monkeypatch.setattr(mod, "_run", fake)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return calls


def test_restart_station_skips_a_consumer_already_fresh(monkeypatch, capsys):
    mod = _load()
    calls = _station_run(monkeypatch, mod, {
        "radiod@x.service": 500.0, "fresh.service": 530.0, "old.service": 100.0})
    assert mod.restart_station("radiod@x.service", ["fresh.service", "old.service"])
    restarted = [c[-1] for c in calls if c[:3] == ["systemctl", "restart", "--no-block"]]
    assert restarted == ["old.service"]
    assert "already fresh" in capsys.readouterr().out


def test_a_consumer_that_crashed_after_radiod_is_still_restarted(monkeypatch):
    # systemd keeps ActiveEnterTimestampMonotonic after a unit stops.
    mod = _load()
    calls = _station_run(monkeypatch, mod,
                         {"radiod@x.service": 500.0, "crashed.service": 510.0},
                         stopped={"crashed.service"})
    mod.restart_station("radiod@x.service", ["crashed.service"])
    assert ["systemctl", "restart", "--no-block", "crashed.service"] in calls


def test_a_consumer_inside_the_settle_window_is_not_fresh(monkeypatch):
    mod = _load()
    calls = _station_run(monkeypatch, mod,
                         {"radiod@x.service": 500.0, "early.service": 505.0})
    mod.restart_station("radiod@x.service", ["early.service"])
    assert ["systemctl", "restart", "--no-block", "early.service"] in calls


def test_a_start_limited_consumer_is_reset_before_its_restart(monkeypatch):
    mod = _load()
    calls = _station_run(monkeypatch, mod, {"radiod@x.service": 500.0})
    mod.restart_station("radiod@x.service", ["down.service"])
    assert calls.index(["systemctl", "reset-failed", "down.service"]) < \
        calls.index(["systemctl", "restart", "--no-block", "down.service"])


def test_restart_station_restarts_a_consumer_that_is_not_running(monkeypatch):
    mod = _load()
    calls = _station_run(monkeypatch, mod, {"radiod@x.service": 500.0})
    mod.restart_station("radiod@x.service", ["down.service"])
    assert ["systemctl", "restart", "--no-block", "down.service"] in calls


# ─── v3.72 #3: a data-only "port reset", the wedged card, the latched return ─
#
# AC0G-B4 2026-10-14: both hubs report ppps and neither cuts VBUS (4.98 V
# through every `uhubctl -a off`).  The action reloads the FX3 firmware and
# cleared a stream wedge on 10-11; it never clears the cold-start latch.

def _sysfs(root, devices):
    """devices: {devpath: (vid, pid)} -> a sysfs tree under root."""
    for name, (vid, pid) in devices.items():
        d = Path(root) / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "idVendor").write_text(vid + "\n")
        (d / "idProduct").write_text(pid + "\n")


def test_a_gpsdo_behind_the_port_forbids_the_reset(tmp_path):
    mod = _load()
    _sysfs(tmp_path, {"4-1": ("2109", "0817"), "4-1.4": ("04b4", "00f1"),
                      "2-1.4": ("1dd2", "2210")})
    (tmp_path / "4-1.4:1.0").mkdir()
    found = mod.foreign_devices([("4-1", "4", False), ("2-1", "4", False)], root=tmp_path)
    assert found == [("2-1.4", "1dd2:2210")]


def test_a_lone_rx888_on_both_halves_is_not_foreign(tmp_path):
    mod = _load()
    _sysfs(tmp_path, {"4-1.4": ("04b4", "00f1")})
    assert mod.foreign_devices([("4-1", "4", False), ("2-1", "4", False)], root=tmp_path) == []


def test_a_device_behind_a_hub_on_the_port_is_foreign(tmp_path):
    mod = _load()
    _sysfs(tmp_path, {"4-1.4": ("05e3", "0610"), "4-1.4.2": ("1ffb", "0089")})
    got = mod.foreign_devices([("4-1", "4", False)], root=tmp_path)
    assert [d for d, _ in got] == ["4-1.4", "4-1.4.2"]


def _loc(speed="5000", pid="00f1"):
    return sdr.Located("4-1.4", "4-1", "4", "SER", speed, product=pid)


def test_clean_return_is_recovered():
    verdict, why = sdr.classify_return(_loc("5000", "00f1"), [])
    assert verdict == "recovered"


def test_usb2_only_return_is_latched():
    verdict, why = sdr.classify_return(_loc("480", "00f1"), [])
    assert verdict == "latched" and "SuperSpeed" in why


def test_kernel_minus_110_is_latched_even_at_speed():
    verdict, _ = sdr.classify_return(_loc("5000", "00f1"),
                                     ["usb 4-1.4: can't set config #1, error -110"])
    assert verdict == "latched"


def test_never_back_is_reported_as_such():
    verdict, _ = sdr.classify_return(None, [])
    assert verdict == "never-back"


def test_the_bootloader_id_alone_proves_nothing():
    verdict, _ = sdr.classify_return(_loc("480", "00f3"), [])
    assert verdict != "recovered"
    verdict, _ = sdr.classify_return(_loc("5000", "00f3"), [])
    assert verdict != "recovered"


def test_kernel_latch_errors_reads_minus_71_and_110(monkeypatch):
    mod = _load()
    out = ("usb 4-1.4: device descriptor read/64, error -71\n"
           "usb 4-1.4: can't set config #1, error -110\n"
           "usb 4-1.4: new SuperSpeed USB device number 9\n")
    monkeypatch.setattr(mod, "_run", lambda cmd, timeout=60: _CP(stdout=out))
    assert len(mod.kernel_latch_errors(1_760_000_000.0, ["4-1.4"])) == 2


def test_journalctl_since_is_an_epoch_not_a_local_time_string(monkeypatch):
    # journalctl --since reads a date string as LOCAL time even with --utc
    # (systemd 257: same string, 4 lines under UTC, 0 under Chicago).
    mod = _load()
    argvs = []
    monkeypatch.setattr(mod, "_run",
                        lambda cmd, timeout=60: argvs.append(list(cmd)) or _CP(stdout=""))
    mod.kernel_latch_errors(1_760_000_123.9, ["4-1.4"])
    mod.radiod_wedge_signals("radiod@x.service", 1_760_000_700.0)
    assert argvs[0][argvs[0].index("--since") + 1] == "@1760000123"
    assert argvs[1][argvs[1].index("--since") + 1] == "@%d" % (1_760_000_700 - mod.WEDGE_WINDOW_S)


def test_kernel_latch_errors_keep_only_the_cards_own_port(monkeypatch):
    mod = _load()
    out = ("usb 4-1.4: can't set config #1, error -110\n"
           "usb 2-1.4: device descriptor read/64, error -71\n"
           "usb 2-1.3: device descriptor read/64, error -71\n"      # the GPSDO's port
           "usb 3-2: can't set config #1, error -110\n")
    monkeypatch.setattr(mod, "_run", lambda cmd, timeout=60: _CP(stdout=out))
    got = mod.kernel_latch_errors(1.0, ["4-1.4", "2-1.4"])
    assert len(got) == 2 and all("2-1.3" not in l and "3-2" not in l for l in got)


def test_foreign_check_sees_the_peer_half_behind_a_downstream_hub(tmp_path):
    mod = _load()
    portdir = tmp_path / "4-1" / "4-1:1.0" / "4-1-port4"
    portdir.mkdir(parents=True)
    os.symlink("../../../../usb2/2-1/2-1:1.0/2-1-port4", portdir / "peer")
    _sysfs(tmp_path, {"4-1.4": ("04b4", "00f1"), "2-1.4": ("05e3", "0610"),
                      "2-1.4.2": ("1dd2", "2210")})
    got = mod.foreign_devices([("4-1", "4", False)], root=tmp_path)
    assert [d for d, _ in got] == ["2-1.4", "2-1.4.2"]


def test_an_occupied_target_is_dropped_and_the_clean_one_kept(tmp_path):
    mod = _load()
    _sysfs(tmp_path, {"4-1.4": ("04b4", "00f1"), "6-1.2": ("1dd2", "2210")})
    clean, dirty = mod.partition_targets(
        [("4-1", "4", False), ("6-1", "2", False)], root=tmp_path)
    assert clean == [("4-1", "4", False)]
    assert [t for t, _ in dirty] == [("6-1", "2", False)]


def test_port_prefixes_cover_both_halves(tmp_path):
    mod = _load()
    portdir = tmp_path / "4-1" / "4-1:1.0" / "4-1-port4"
    portdir.mkdir(parents=True)
    os.symlink("../../../../usb2/2-1/2-1:1.0/2-1-port4", portdir / "peer")
    assert mod.port_prefixes([("4-1", "4", False)], root=tmp_path) == ["4-1.4", "2-1.4"]


def test_wedge_needs_stall_lines_and_restart_evidence():
    n = sdr.WEDGE_MIN_HITS
    assert not sdr.is_wedged(stall_hits=1, exit_hits=3, restarts_delta=3)
    assert not sdr.is_wedged(stall_hits=n, exit_hits=0, restarts_delta=0)   # streaming, odd counter
    assert not sdr.is_wedged(stall_hits=0, exit_hits=0, restarts_delta=9)   # restarts, no USB signal
    assert sdr.is_wedged(stall_hits=n, exit_hits=1, restarts_delta=0)
    assert sdr.is_wedged(stall_hits=n, exit_hits=0, restarts_delta=1)


def test_wedge_hits_ignore_a_zero_xfer_failure_counter(monkeypatch):
    mod = _load()
    out = "\n".join(["rx888: USB xfer failures +0"] * 20
                    + ["No rx888 data for 5 seconds, quitting"] * 2
                    + ["rx888: USB xfer failures +3"])
    out += "\nradiod@x.service: Main process exited, code=dumped, status=6/ABRT"
    monkeypatch.setattr(mod, "_run", lambda cmd, timeout=60: _CP(stdout=out))
    assert mod.radiod_wedge_signals("radiod@x.service", 1_760_000_000.0) == (3, 1)


def test_a_stale_restart_delta_is_ignored():
    # a sample from an hour ago must not read as "restarting now"
    mod = _load()
    T = 1_760_000_000.0
    mod._run = lambda cmd, timeout=60: _CP(stdout="50\n")
    st = {"nrestarts_sample": [T - 3600, 1]}
    assert mod.sample_nrestarts("radiod@x.service", st, T) == 0
    assert st["nrestarts_sample"] == [T, 50]
    st = {"nrestarts_sample": [T - 30, 47]}
    assert mod.sample_nrestarts("radiod@x.service", st, T) == 3


def test_streaming_with_a_nonzero_xfer_counter_is_not_a_wedge(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod.sdr_events, "uptime_s", lambda: 99999.0)
    out = "\n".join(["rx888: USB xfer failures +2"] * 6)

    def fake(cmd, timeout=60):
        if cmd[0] == "journalctl":
            return _CP(stdout=out)
        return _CP(stdout="7\n")                      # NRestarts unchanged
    monkeypatch.setattr(mod, "_run", fake)
    st = {"nrestarts_sample": [1_760_000_000.0 - 30, 7]}
    assert not mod.radiod_is_wedged("radiod@x.service", st, 1_760_000_000.0)
    out2 = out + "\nScheduled restart job, restart counter is at 8."
    monkeypatch.setattr(mod, "_run", lambda c, timeout=60:
                        _CP(stdout=out2) if c[0] == "journalctl" else _CP(stdout="7\n"))
    reason = mod.radiod_is_wedged("radiod@x.service", st, 1_760_000_000.0)
    assert reason and "stall" in reason


class PortResetHarness(unittest.TestCase):
    """The watchdog's loop with the new inputs injected."""

    CFG = {"recover_hub": "1-1", "recover_port": "2",
           "unit": "radiod@ac0g.service", "consumers": []}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.log, self.events, self.cycles, self.restarts = [], [], [], []
        self.returns = True
        self.present, self.wedged = True, True
        self.card = sdr.Located("1-1.2", "1-1", "2", "S", "5000", product="00f1")
        self.kernel = []
        self.foreign = []
        self.t = 1_760_000_000.0
        self._saved = {}
        stand_ins = {
            "STATE_PATH": self.tmp / "sdr-recover.json",
            "read_config": lambda: dict(self.CFG),
            "device_present": lambda: self.present,
            "bootstrap_locations": lambda: [],
            "cycle_port": self._cycle,
            "wait_for_device": lambda timeout_s=90: self.present,
            "installed_units": lambda: [],
            "restart_station": lambda unit, consumers, **kw: self.restarts.append(unit) or True,
            "_with_lifecycle_lock": contextlib.nullcontext,
            "_log": self.log.append,
            "note_event": lambda kind, detail: self.events.append((kind, detail)),
            "radiod_is_wedged": lambda unit, state, now, **k:
                ("3 stall lines and a restart" if self.wedged else ""),
            "sample_nrestarts": lambda unit, state, now: 0,
            "locate_rx888": lambda *a, **k: self.card if self.present else None,
            "kernel_latch_errors": lambda since, prefixes=None: list(self.kernel),
            "foreign_devices": lambda targets, **k: list(self.foreign),
        }
        for name, value in stand_ins.items():
            self._saved[name] = getattr(sdr, name, None)
            setattr(sdr, name, value)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(sdr, name, value)

    def _cycle(self, targets, off):
        self.cycles.append(list(targets))
        if not self.returns:
            self.present = False
        return True

    def tick(self, *argv, dt=30):
        with mock.patch.object(sdr.time, "time", return_value=self.t), \
             mock.patch.object(sdr.time, "sleep", lambda s: None):
            rc = sdr.main(list(argv))
        self.t += dt
        return rc

    def kinds(self):
        return [k for k, _ in self.events]

    def holding(self):
        return [m for m in self.log if m.startswith("holding:")]


class PortResetLoopTests(PortResetHarness):
    def test_a_wedged_card_gets_one_reset_and_is_rate_limited(self):
        self.tick()
        self.assertEqual(len(self.cycles), 1)
        self.assertEqual(self.kinds()[0], "port-reset")
        for _ in range(10):                         # 5 min of ticks, still wedged
            self.tick()
        self.assertEqual(len(self.cycles), 1)
        self.t += sdr.WEDGE_RESET_COOLDOWN_S
        self.tick()
        self.assertEqual(len(self.cycles), 2)

    def test_a_healthy_present_card_is_never_reset(self):
        self.wedged = False
        for _ in range(5):
            self.tick()
        self.assertEqual(self.cycles, [])

    def test_a_clean_return_says_recovered_by_port_reset(self):
        self.tick()
        self.assertTrue(any("recovered by port reset" in m for m in self.log), self.log)
        self.assertNotIn("needs-replug", self.kinds())

    def test_a_latched_return_alarms_once_and_stops_resetting(self):
        self.card = sdr.Located("1-1.2", "1-1", "2", "S", "480", product="00f1")
        self.kernel = ["usb 1-1.2: can't set config #1, error -110"]
        self.tick()
        self.assertEqual(self.kinds().count("needs-replug"), 1)
        self.assertTrue(any("needs a hand replug at the RX888" in m for m in self.log))
        for _ in range(8):
            self.t += sdr.WEDGE_RESET_COOLDOWN_S
            self.tick()
        self.assertEqual(len(self.cycles), 1)
        self.assertEqual(self.kinds().count("needs-replug"), 1)

    def test_a_card_that_never_returns_alarms_once_and_stops_resetting(self):
        self.returns = False
        sdr.wait_for_device = lambda timeout_s=90: self.present
        self.tick()
        for _ in range(8):
            self.t += sdr.CYCLE_COOLDOWN_S + sdr.WEDGE_RESET_COOLDOWN_S
            self.tick()
        self.assertEqual(self.kinds(), ["port-reset", "needs-replug"])
        self.assertIn("never returned", self.events[1][1])
        self.assertEqual(len(self.cycles), 1)

    def test_a_latched_card_that_stays_present_logs_once(self):
        self.card = sdr.Located("1-1.2", "1-1", "2", "S", "480", product="00f1")
        self.kernel = ["usb 1-1.2: can't set config #1, error -110"]
        self.tick()
        self.log.clear()
        for _ in range(10):
            self.tick()
        self.assertLessEqual(len(self.holding()), 1, self.log)

    def test_a_clean_return_still_restarts_the_station(self):
        self.tick()
        self.assertEqual(self.restarts, ["radiod@ac0g.service"])

    def test_a_refused_reset_is_not_an_attempt_and_logs_once(self):
        self.foreign = [("2-1.2", "1dd2:2210")]
        for _ in range(6):
            self.tick()
        self.assertEqual(self.cycles, [])
        st = json.loads((self.tmp / "sdr-recover.json").read_text())
        self.assertNotIn("last_cycle_at", st)
        self.assertEqual(len([m for m in self.log if "REFUSING" in m]), 1, self.log)

    def test_a_wedge_held_by_the_cooldown_says_so_once(self):
        self.tick()
        self.log.clear()
        for _ in range(6):
            self.tick()
        self.assertEqual(len(self.holding()), 1, self.log)

    def test_replugged_healthy_card_clears_the_latch(self):
        self.card = sdr.Located("1-1.2", "1-1", "2", "S", "480", product="00f1")
        self.kernel = ["usb 1-1.2: can't set config #1, error -71"]
        self.tick()
        self.card = sdr.Located("1-1.2", "1-1", "2", "S", "5000", product="00f1")
        self.kernel = []
        self.wedged = False
        self.tick()
        self.wedged = True
        self.t += sdr.WEDGE_RESET_COOLDOWN_S
        self.tick()
        self.assertEqual(len(self.cycles), 2)

    def test_a_gpsdo_on_the_port_refuses_the_reset_and_says_why(self):
        self.foreign = [("2-1.2", "1dd2:2210")]
        self.tick()
        self.assertEqual(self.cycles, [])
        self.assertTrue(any("1dd2:2210" in m and "refus" in m.lower() for m in self.log), self.log)

    def test_bootloader_id_on_return_is_not_called_a_power_cut(self):
        self.card = sdr.Located("1-1.2", "1-1", "2", "S", "480", product="00f3")
        self.tick()
        for m in self.log:
            low = m.lower()
            self.assertNotIn("power cut", low)
            self.assertNotIn("power cycle", low)
            self.assertNotIn("power-cycle", low)
        self.assertFalse(any("recovered by port reset" in m for m in self.log))

    def test_a_dry_run_resets_nothing(self):
        self.tick("--dry-run")
        self.assertEqual(self.cycles, [])

    def test_the_port_claim_defaults_to_unverified(self):
        self.assertIn("switching unverified", sdr.port_claim({}))
        self.assertIn("verified", sdr.port_claim({"recover_port_verified": True}))
        self.assertNotIn("unverified", sdr.port_claim({"recover_port_verified": True}))


# ─── fix round 2: B4's real cold-boot latch signature, bounded window ────────

B4_LATCH_JOURNAL = "\n".join(
    ["rx888_usb_init() failed",
     "device setup returned -5",
     "radiod@AC0G-B4.service: Main process exited, code=exited, status=66/NOINPUT",
     "radiod@AC0G-B4.service: Failed with result 'exit-code'."] * 6)


def test_b4_latch_lines_count_as_stall_and_restart_evidence(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "_run", lambda cmd, timeout=60: _CP(stdout=B4_LATCH_JOURNAL))
    stall, exits = mod.radiod_wedge_signals("radiod@AC0G-B4.service", 1_760_000_000.0)
    assert stall >= mod.WEDGE_MIN_HITS and exits >= 1
    assert mod.is_wedged(stall, exits, 0)


def test_a_usb2_card_with_radiod_failing_init_is_wedged_on_one_line():
    assert not sdr.is_wedged(1, 1, 0)
    assert sdr.is_wedged(1, 1, 0, usb2=True)


def test_the_wedge_window_starts_after_the_last_reset_and_the_card_return(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod.sdr_events, "uptime_s", lambda: 99999.0)
    argvs = []

    def fake(cmd, timeout=60):
        argvs.append(list(cmd))
        return _CP(stdout="7\n" if cmd[0] == "systemctl" else "")
    monkeypatch.setattr(mod, "_run", fake)
    now = 1_760_001_000.0
    since = lambda: [a for a in argvs if a[0] == "journalctl"][-1][
        [a for a in argvs if a[0] == "journalctl"][-1].index("--since") + 1]
    mod.radiod_is_wedged("radiod@x.service", {}, now)
    assert since() == "@%d" % (now - mod.WEDGE_WINDOW_S)
    mod.radiod_is_wedged("radiod@x.service", {"last_cycle_at": now - 100}, now)
    assert since() == "@%d" % (now - 100)
    mod.radiod_is_wedged("radiod@x.service",
                         {"last_cycle_at": now - 100, "card_return_at": now - 40}, now)
    assert since() == "@%d" % (now - 40)


class B4ColdBootTimelineTests(PortResetHarness):
    """B4 2026-10-14: card latched on the USB2 half, radiod failing init
    every 10 s with these exact lines.  One reset, one latched alarm, silence."""

    def setUp(self):
        super().setUp()
        sdr.radiod_is_wedged = self._saved["radiod_is_wedged"]   # the real detector
        sdr.sample_nrestarts = self._saved["sample_nrestarts"]
        self._more = {}
        for name, value in {
            "_run": lambda cmd, timeout=60: _CP(
                stdout=B4_LATCH_JOURNAL if cmd[0] == "journalctl" else "3\n"),
        }.items():
            self._more[name] = getattr(sdr, name)
            setattr(sdr, name, value)
        self._uptime = sdr.sdr_events.uptime_s
        sdr.sdr_events.uptime_s = lambda: 99999.0
        self.card = sdr.Located("1-1.2", "1-1", "2", "S", "480", product="00f1")
        self.kernel = ["usb 1-1.2: can't set config #1, error -110"]

    def tearDown(self):
        for name, value in self._more.items():
            setattr(sdr, name, value)
        sdr.sdr_events.uptime_s = self._uptime
        super().tearDown()

    def test_one_reset_one_alarm_then_silence(self):
        for _ in range(12):
            self.tick()
        self.assertEqual(len(self.cycles), 1)
        self.assertEqual(self.kinds(), ["port-reset", "needs-replug"])
        quiet = len(self.log)
        for _ in range(10):
            self.t += 600
            self.tick()
        self.assertEqual(len(self.log), quiet, self.log[quiet:])
        self.assertEqual(len(self.cycles), 1)

    def test_a_hand_replug_stamps_the_card_return(self):
        self.card = sdr.Located("1-1.2", "1-1", "2", "S", "5000", product="00f1", devnum="7")
        self.wedged = False
        self.tick()
        self.card = sdr.Located("1-1.2", "1-1", "2", "S", "5000", product="00f1", devnum="9")
        self.tick()
        st = json.loads((self.tmp / "sdr-recover.json").read_text())
        self.assertEqual(st["devnum"], "9")
        self.assertIn("card_return_at", st)


def test_the_lowered_usb2_threshold_needs_two_consecutive_ticks(monkeypatch):
    # a healthy card passes through 00f3 at 480 Mb/s while it re-enumerates;
    # one init failure on that tick must not cost it a reset
    mod = _load()
    monkeypatch.setattr(mod.sdr_events, "uptime_s", lambda: 99999.0)
    out = "rx888_usb_init() failed\nradiod@x.service: Main process exited, code=exited, status=66/NOINPUT"
    monkeypatch.setattr(mod, "_run", lambda c, timeout=60:
                        _CP(stdout=out if c[0] == "journalctl" else "0\n"))
    card = lambda dn: mod.Located("1-1.2", "1-1", "2", "S", "480", product="00f3", devnum=dn)
    T = 1_760_000_000.0
    st = {}
    assert not mod.radiod_is_wedged("radiod@x.service", st, T, card=card("7"))
    assert mod.radiod_is_wedged("radiod@x.service", st, T + 30, card=card("7"))
    st = {}
    assert not mod.radiod_is_wedged("radiod@x.service", st, T, card=card("7"))
    assert not mod.radiod_is_wedged("radiod@x.service", st, T + 30, card=card("8"))   # re-enumerated
    st = {}
    mod.radiod_is_wedged("radiod@x.service", st, T, card=card("7"))
    ss = mod.Located("1-1.2", "1-1", "2", "S", "5000", product="00f1", devnum="7")
    assert not mod.radiod_is_wedged("radiod@x.service", st, T + 30, card=ss)
    assert not mod.radiod_is_wedged("radiod@x.service", st, T + 60, card=card("7"))


# ─── v3.73 row 9: which hubs can switch both halves ──────────────────────────

class PortHalvesTests(unittest.TestCase):
    """Which halves of the card's physical port uhubctl can switch.  The
    RX888 runs on the SuperSpeed half; a reset that reaches only the USB2
    half leaves its latched firmware in place (AC0G-B4, 10-15)."""

    SPEEDS = {"10-1": "10000", "8-1": "480", "3-1": "480", "6": "5000"}

    def halves(self, loc, accepted):
        return sdr.port_halves(loc, accepted, speed_of=self.SPEEDS.get)

    def card(self, hub="10-1", peer=("8-1", "4")):
        return sdr.Located(f"{hub}.4", hub, "4", "S", "5000",
                           peer[0] if peer else None, peer[1] if peer else None,
                           product="00f1")

    def test_both_halves_reached(self):
        self.assertEqual(self.halves(self.card(), [("10-1", "4", True), ("8-1", "4", False)]),
                         {"ss": True, "usb2": True})

    def test_only_the_usb2_half_reached_and_only_with_exact(self):
        self.assertEqual(self.halves(self.card(), [("8-1", "4", True)]),
                         {"ss": False, "usb2": True})

    def test_a_plain_usb2_answer_reaches_the_superspeed_half_by_duality(self):
        # uhubctl's USB3 duality: a plain `-l <usb2 hub>` also switches the
        # SuperSpeed companion, so that port is not usb2-only.
        self.assertEqual(self.halves(self.card(), [("8-1", "4", False)]),
                         {"ss": None, "usb2": True})

    def test_a_usb2_only_hub_has_no_superspeed_half(self):
        loc = self.card(hub="3-1", peer=None)
        self.assertEqual(self.halves(loc, [("3-1", "4", False)]),
                         {"ss": None, "usb2": True})

    def test_a_root_hub_reads_its_speed_from_the_bus(self):
        loc = sdr.Located("6-2", "6", "2", "S", "5000", product="00f1")
        self.assertEqual(self.halves(loc, [("6", "2", False)]),
                         {"ss": True, "usb2": None})

    def test_an_unreadable_speed_counts_as_unknown(self):
        loc = sdr.Located("9-1.4", "9-1", "4", "S", "5000", product="00f1")
        self.assertEqual(sdr.port_halves(loc, [("9-1", "4", False)],
                                         speed_of=lambda h: None),
                         {"ss": None, "usb2": None})

    def test_hub_speed_reads_sysfs(self):
        root = Path(tempfile.mkdtemp())
        (root / "10-1").mkdir()
        (root / "10-1" / "speed").write_text("10000\n")
        (root / "usb6").mkdir()
        (root / "usb6" / "speed").write_text("5000\n")
        self.assertEqual(sdr.hub_speed("10-1", root), "10000")
        self.assertEqual(sdr.hub_speed("6", root), "5000")
        self.assertIsNone(sdr.hub_speed("7-1", root))


class PortPowerTests(unittest.TestCase):

    def test_usb2_half_only(self):
        kind, why = sdr.port_power({"ss": False, "usb2": True}, {})
        self.assertEqual(kind, "usb2-only")
        self.assertIn("USB2 half", why)

    def test_both_halves_reached_but_no_cut_watched_is_unverified(self):
        self.assertEqual(sdr.port_power({"ss": True, "usb2": True}, {})[0], "unverified")

    def test_a_watched_cut_reads_both(self):
        self.assertEqual(sdr.port_power({"ss": True, "usb2": True},
                                        {"recover_port_verified": True})[0], "both")

    def test_a_measured_data_only_hub_wins_over_everything(self):
        kind, why = sdr.port_power({"ss": True, "usb2": True},
                                   {"recover_port_data_only": True,
                                    "recover_port_verified": True})
        self.assertEqual(kind, "data-only")
        self.assertIn("recover_port_data_only", why)

    def test_unknown_halves_stay_unverified(self):
        self.assertEqual(sdr.port_power({}, {})[0], "unverified")
        self.assertEqual(sdr.port_power(None, {})[0], "unverified")

    def test_what_a_reset_can_help(self):
        can = sdr.reset_can_help
        for trig in ("absent", "latched", "wedge"):
            self.assertTrue(can("both", trig))
            self.assertTrue(can("unverified", trig))
            self.assertFalse(can("usb2-only", trig))
        # A data-only cut still drops the link, which reloads the FX3 and
        # cleared B4's 10-11 stream wedge; it never cleared a latch.
        self.assertTrue(can("data-only", "wedge"))
        self.assertFalse(can("data-only", "latched"))
        self.assertFalse(can("data-only", "absent"))


class ReplugAtOnceTests(PortResetHarness):
    """On a port no reset can clear, ask for the hand replug at once."""

    def data_only(self):
        self.CFG = dict(PortResetHarness.CFG, recover_port_data_only=True)

    def test_a_vanished_card_on_a_data_only_hub_asks_for_a_replug_at_once(self):
        self.data_only()
        self.present = False
        self.tick()                                   # starts the grace window
        self.t += sdr.ABSENT_GRACE_S + 5
        self.tick()
        self.assertEqual(self.cycles, [])
        self.assertEqual(self.kinds(), ["needs-replug"])
        self.assertTrue(any("needs a hand replug" in m and "no port reset tried" in m
                            for m in self.log), self.log)
        for _ in range(6):
            self.t += sdr.CYCLE_COOLDOWN_S
            self.tick()
        self.assertEqual(self.cycles, [])
        self.assertEqual(self.kinds(), ["needs-replug"])

    def test_a_wedge_on_a_data_only_hub_still_gets_its_reset(self):
        self.data_only()
        self.tick()
        self.assertEqual(len(self.cycles), 1)
        self.assertNotIn("needs-replug", self.kinds())

    def test_a_latched_present_card_on_a_data_only_hub_asks_at_once(self):
        self.data_only()
        self.card = sdr.Located("1-1.2", "1-1", "2", "S", "480", product="00f1")
        self.tick()
        self.assertEqual(self.cycles, [])
        self.assertEqual(self.kinds(), ["needs-replug"])

    def test_ensure_present_fails_at_once_on_a_data_only_hub(self):
        self.data_only()
        self.present = False
        rc = self.tick("--ensure-present", "--wait-seconds", "5")
        self.assertEqual(rc, 1)
        self.assertEqual(self.cycles, [])
        self.assertEqual(self.kinds(), ["needs-replug"])

    def test_a_dry_run_answers_and_records_nothing(self):
        self.data_only()
        self.present = False
        self.tick()
        self.t += sdr.ABSENT_GRACE_S + 5
        self.tick("--dry-run")
        self.assertEqual(self.cycles, [])
        self.assertEqual(self.kinds(), [])
        self.assertTrue(any("hand replug" in m for m in self.log), self.log)

    def test_the_unverified_default_still_resets_a_vanished_card(self):
        self.present = False
        self.tick()
        self.t += sdr.ABSENT_GRACE_S + 5
        self.tick()
        self.assertEqual(len(self.cycles), 1)


class LearnedUsb2OnlyTests(PortResetHarness):
    """The learn path: uhubctl answers only for the USB2 half, as B4's
    StarTech hub did for `-l 6-1` (10-15)."""

    CFG = {"unit": "radiod@ac0g.service", "consumers": []}

    def setUp(self):
        super().setUp()
        self.card = sdr.Located("10-1.4", "10-1", "4", "S", "5000", "8-1", "4",
                                product="00f1")
        self.wedged = False
        self.accepted = [("8-1", "4", True)]
        for name, value in {
            "hub_switchable": lambda loc: (True, list(self.accepted)),
            "hub_speed": lambda hub, root=None: {"10-1": "10000", "8-1": "480"}.get(hub),
        }.items():
            self._saved[name] = getattr(sdr, name)
            setattr(sdr, name, value)

    def test_the_learn_line_names_the_usb2_half(self):
        self.tick()
        self.assertTrue(any("USB2 half" in m and "hand replug" in m for m in self.log),
                        self.log)
        st = json.loads((self.tmp / "sdr-recover.json").read_text())
        self.assertEqual(st["power"], "usb2-only")

    def test_a_vanish_then_asks_for_the_replug_and_resets_nothing(self):
        self.tick()
        self.present = False
        self.tick()
        self.t += sdr.ABSENT_GRACE_S + 5
        self.tick()
        self.assertEqual(self.cycles, [])
        self.assertEqual(self.kinds(), ["needs-replug"])
        self.assertIn("USB2 half", self.events[0][1])

    def test_a_wedge_there_asks_for_the_replug_too(self):
        self.tick()
        self.wedged = True
        self.tick()
        self.assertEqual(self.cycles, [])
        self.assertEqual(self.kinds(), ["needs-replug"])

    def test_both_halves_reached_resets_as_before(self):
        self.accepted = [("10-1", "4", True), ("8-1", "4", False)]
        self.tick()
        st = json.loads((self.tmp / "sdr-recover.json").read_text())
        self.assertEqual(st["power"], "unverified")
        self.wedged = True
        self.tick()
        self.assertEqual(len(self.cycles), 1)


class DualityProbeTests(PortResetHarness):
    """Fix round 1: the real hub_switchable, with uhubctl answering as B4's
    StarTech does.  A latched card sits on the USB2 hub (8-1), and its peer
    10-1 answers only with --exact."""

    CFG = {"unit": "radiod@ac0g.service", "consumers": []}

    def setUp(self):
        super().setUp()
        self.card = sdr.Located("8-1.4", "8-1", "4", "S", "480", "10-1", "4",
                                product="00f1")
        self.answers = set()          # (hub, exact) pairs uhubctl accepts
        self.uhub_calls = []

        def fake_run(cmd, timeout=60):
            if cmd and cmd[0] == sdr.UHUBCTL:
                hub, exact = cmd[cmd.index("-l") + 1], "-e" in cmd
                self.uhub_calls.append((hub, exact))
                if (hub, exact) in self.answers:
                    return _CP(stdout=f"Current status for hub {hub} [14b0:0131 x, ppps]\n"
                                      f"  Port 4: 0103 power enable connect\n")
                return _CP(stderr=f"No compatible devices detected at location {hub}!",
                           returncode=1)
            return _CP()
        for name, value in {
            "_run": fake_run,
            "hub_speed": lambda hub, root=None: {"10-1": "10000", "8-1": "480"}.get(hub),
        }.items():
            self._saved[name] = getattr(sdr, name)
            setattr(sdr, name, value)

    def state(self):
        return json.loads((self.tmp / "sdr-recover.json").read_text())

    def test_the_peer_is_probed_with_exact_too(self):
        self.answers = {("8-1", True), ("10-1", True)}
        self.tick()
        self.assertIn(("10-1", True), self.uhub_calls)
        self.assertNotEqual(self.state()["power"], "usb2-only")
        self.assertEqual(len(self.cycles), 1)          # latched + wedged: reset proceeds
        self.assertNotIn("needs-replug", self.kinds()[:1])

    def test_a_plain_usb2_answer_is_not_usb2_only(self):
        self.answers = {("8-1", False)}
        self.tick()
        self.assertNotEqual(self.state()["power"], "usb2-only")
        self.assertEqual(len(self.cycles), 1)

    def test_usb2_only_needs_no_superspeed_answer_and_no_plain_usb2_answer(self):
        self.answers = {("8-1", True)}
        self.tick()
        self.assertEqual(self.state()["power"], "usb2-only")
        self.assertEqual(self.cycles, [])
        self.assertEqual(self.kinds(), ["needs-replug"])


class ManualHoldNamesTheCardsOwnRadiodTests(unittest.TestCase):
    """I6: on a two-card host with [radiod].unit set, --off --serial <other>
    must stop and record the radiod that opens THAT card, not the designated
    one, or the watchdog's "unit wins over serial" rule protects the wrong
    radiod."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hold = Path(self.tmp.name) / "sdr-hold.json"
        mock.patch.object(sdr, "HOLD_PATH", self.hold).start()
        self.stop = mock.patch.object(sdr, "_stop_station").start()
        mock.patch.object(sdr, "_uhubctl_set", return_value=True).start()
        mock.patch.object(sdr, "detect_radiod_unit", return_value=None).start()
        mock.patch.object(sdr, "installed_units", return_value=[]).start()
        mock.patch.object(sdr, "time").start().time.return_value = 1000.0
        from sigmond import sdr_map
        mock.patch.object(sdr_map, "instance_serials",
                          return_value={"north": "RX0001",
                                        "south": "RX0002"}).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(self.tmp.cleanup)

    def _off(self, serial, cfg):
        card = sdr.Located("4-1.4", "4-1", "4", serial, "5000",
                           "3-1", "4", "00f1", "7")
        other = sdr.Located("4-1.3", "4-1", "3", "RX0001", "5000",
                            "3-1", "3", "00f1", "6")
        with mock.patch.object(sdr, "all_rx888", return_value=[other, card]), \
             mock.patch.object(sdr, "hub_switchable",
                               return_value=(True, [("4-1", "4", True)])), \
             mock.patch.object(sdr, "port_halves",
                               return_value={"ss": True, "usb2": True}), \
             mock.patch.object(sdr, "partition_targets",
                               return_value=([("4-1", "4", True)], [])), \
             mock.patch.object(sdr, "port_power",
                               return_value=("unverified", "unverified")), \
             mock.patch.object(sdr, "locate_rx888", return_value=None):
            return sdr.manual_off(serial, cfg)

    def test_off_records_the_radiod_mapped_to_the_serial(self):
        rc = self._off("RX0002", {"unit": "radiod@north.service"})
        self.assertEqual(rc, 0)
        hold = json.loads(self.hold.read_text())["RX0002"]
        self.assertEqual(hold["radiod_unit"], "radiod@south.service")
        self.assertEqual(self.stop.call_args.args[0], "radiod@south.service")

    def test_off_falls_back_to_cfg_unit_for_an_unmapped_serial(self):
        rc = self._off("RX0009", {"unit": "radiod@north.service"})
        self.assertEqual(rc, 0)
        hold = json.loads(self.hold.read_text())["RX0009"]
        self.assertEqual(hold["radiod_unit"], "radiod@north.service")

    def test_on_without_a_recorded_unit_uses_the_serial_map(self):
        self.hold.write_text(json.dumps({"RX0002": {
            "serial": "RX0002", "devpath": "4-1.4",
            "locations": [["4-1", "4", True]], "consumers": [],
            "since": 1000.0}}))
        card = sdr.Located("4-1.4", "4-1", "4", "RX0002", "5000",
                           "3-1", "4", "00f1", "7")
        with mock.patch.object(sdr, "wait_for_running_card",
                               return_value=(card, "ready")), \
             mock.patch.object(sdr, "restart_station",
                               return_value=True) as restart:
            rc = sdr.manual_on("RX0002", {"unit": "radiod@north.service"})
        self.assertEqual(rc, 0)
        restart.assert_called_once_with("radiod@south.service", [])
