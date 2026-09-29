"""Tests for sigmond-sdr-recover — RX888 power-cycle recovery.

The RX-888 recurrently drops off the USB bus and only a power cycle of the
card recovers it.  This helper cuts and restores VBUS on the hub port, then
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

import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path

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
        self.assertTrue(any("power restored" in m for m in log))

    def test_both_forms_failing_is_reported_loudly(self):
        ok, _, log = self._cycle({("4", "on", True): 124, ("4", "on", False): 1})
        self.assertFalse(ok)
        self.assertTrue(any("POWER LEFT OFF" in m for m in log),
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
    off there.  Reporting that as POWER LEFT OFF sent rob to the bench to look
    at an RX-888 that was powered and streaming (2026-09-29):

        POWER LEFT OFF on hub 4-1 port 4 — both forms failed (rc=1)
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
        self.assertFalse(any("POWER LEFT OFF" in m for m in log),
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
        self.assertTrue(any("POWER LEFT OFF" in m for m in log))

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
