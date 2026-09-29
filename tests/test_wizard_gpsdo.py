"""What the wizard says about a GPSDO that is present but not locked.

⛔ "not readable here" conflated two unrelated situations, and on the commonest
DASI GPSDO it was actively misleading.

The LBE-Mini (1dd2:2211) is HID-only — UBX on interrupt-IN, with NO serial node
at all — so the wizard can never read a position from it, with a perfect sky
view or none.  Telling the operator it was "not readable" invited them to hunt
for a fault that does not exist.  rob, 2026-09-29: his LBE-Mini was attached,
powered and flashing red purely for want of antenna signal, and the wizard's
wording gave him no way to tell that from a broken read.

Confirmed on that station via `gpsdo-monitor status`, which speaks UBX properly:

    gps_fix: "no_fix"   sats_used: 0    gps_locked: false
    pll_locked: true    out1_hz: 27000000    drive_ma: 32
    t_acc_ns: 4294967295        # 0xFFFFFFFF — time accuracy invalid

i.e. the device was healthy and still clocking the RX888 at 27 MHz; only the
GPS discipline was missing.  The wizard must distinguish "cannot be read by
this wizard" from "has no fix yet".
"""
import subprocess
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"


def shell_func(name: str) -> str:
    """Lift a top-level shell function verbatim out of the wizard."""
    text, keep, out = WIZARD.read_text().splitlines(True), False, []
    for ln in text:
        if ln.startswith(f"{name}() {{") or ln.startswith(f"{name}(){{"):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, f"{name} not found in the wizard"
    return "".join(out)


def ask_grid() -> str:
    return shell_func("ask_grid")


def run(gpsdo_id: str, model: str, grid_from_device: str = "",
        monitor=None) -> str:
    """monitor: None = gpsdo-monitor does not answer; else (fix, sats, grid)."""
    stub = (f'gpsdo_grid(){{ printf "%s\\n" "{grid_from_device}"; '
            f'[ -n "{grid_from_device}" ]; }}')
    if monitor is None:
        report = "gpsdo_lock_report(){ return 1; }"
    else:
        fix, sats, grid = monitor
        report = ('gpsdo_lock_report(){ '
                  f'GPSDO_FIX="{fix}"; GPSDO_SATS="{sats}"; '
                  f'GPSDO_GRID_UBX="{grid}"; return 0; }}')
    script = textwrap.dedent(f"""
        set -u
        rd(){{ read "$@" || true; }}
        say(){{ printf '%s\\n' "$*"; }}
        HAVE_GPSDO=1
        PREFLIGHT_SRC=vm
        GPSDO_ID="{gpsdo_id}"
        GPSDO_MODEL="{model}"
        GPSDO_FIX=""; GPSDO_SATS=""; GPSDO_GRID_UBX=""
        GRID=""
        {stub}
        {report}
        {shell_func("gpsdo_say_lock")}
        {ask_grid()}
        ask_grid
        printf 'GRID=%s\\n' "$GRID"
    """)
    p = subprocess.run(["bash", "-c", script], input=b"CM87tj\n",
                       capture_output=True, timeout=30)
    return p.stdout.decode()


def preflight_note(monitor) -> str:
    """Run the equipment-list annotation with a stubbed monitor reading."""
    if monitor is None:
        report = "gpsdo_lock_report(){ return 1; }"
    else:
        fix, sats, grid = monitor
        report = ('gpsdo_lock_report(){ '
                  f'GPSDO_FIX="{fix}"; GPSDO_SATS="{sats}"; '
                  f'GPSDO_GRID_UBX="{grid}"; return 0; }}')
    script = textwrap.dedent(f"""
        set -u
        GPSDO_FIX=""; GPSDO_SATS=""; GPSDO_GRID_UBX=""
        {report}
        {shell_func("gpsdo_preflight_note")}
        gpsdo_preflight_note
    """)
    p = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, timeout=30)
    return p.stdout


class GpsdoMessageTests(unittest.TestCase):
    MINI = "1dd2:2211"
    SERIAL = "1dd2:2444"          # LBE-1421, has a real NMEA serial node

    def test_mini_is_not_called_unreadable(self):
        out = run(self.MINI, "LBE-Mini")
        self.assertNotIn("not readable", out)

    def test_mini_says_why_and_that_it_is_not_a_fault(self):
        out = run(self.MINI, "LBE-Mini")
        self.assertIn("USB HID", out)
        self.assertIn("is not a fault", out)

    def test_mini_does_not_claim_to_be_reading_the_device(self):
        """It cannot read it, so it must not print 'Reading position...'."""
        out = run(self.MINI, "LBE-Mini")
        self.assertNotIn("Reading position", out)

    def test_serial_gpsdo_without_a_fix_says_no_fix(self):
        out = run(self.SERIAL, "LBE-1421")
        self.assertIn("Reading position", out)
        self.assertIn("not holding a GPS fix", out)

    def test_no_fix_explains_the_flashing_red_led(self):
        """The operator is looking at the LED; name what it means."""
        out = run(self.SERIAL, "LBE-1421")
        self.assertIn("red", out)
        self.assertIn("sky", out)

    def test_a_real_fix_is_used_and_not_second_guessed(self):
        out = run(self.SERIAL, "LBE-1421", grid_from_device="EM38ww")
        self.assertIn("EM38ww", out)
        self.assertNotIn("not holding a GPS fix", out)

    # ── lock state must actually be REPORTED ────────────────────────────────
    # rob, 2026-09-29: "Where does it pronounce the LBE-Mini doesn't have a
    # lock? I see it listed as present, but no mention of it having no lock.
    # This is the only place that would present that information."
    NO_FIX = ("no_fix", 0, "")
    LOCKED = ("3d", 11, "CM87tj")

    def test_mini_says_it_has_no_lock(self):
        out = run(self.MINI, "LBE-Mini", monitor=self.NO_FIX)
        self.assertIn("NO GPS LOCK", out)
        self.assertIn("0 in use", out)

    def test_mini_no_lock_warns_the_clock_is_undisciplined(self):
        """A station in this state still captures — against a free clock."""
        out = run(self.MINI, "LBE-Mini", monitor=self.NO_FIX)
        self.assertIn("undisciplined", out)
        self.assertIn("free-running clock", out)

    def test_mini_reports_a_lock_when_it_has_one(self):
        out = run(self.MINI, "LBE-Mini", monitor=self.LOCKED)
        self.assertIn("GPS lock: 3d (11 satellites)", out)
        self.assertNotIn("NO GPS LOCK", out)

    def test_mini_can_now_supply_a_grid_via_the_monitor(self):
        """The NMEA path never could; going through gpsdo-monitor can."""
        out = run(self.MINI, "LBE-Mini", monitor=self.LOCKED)
        self.assertIn("position from the GPSDO: CM87tj", out)
        self.assertIn("GRID=CM87tj", out)

    def test_mini_says_so_when_the_monitor_cannot_be_reached(self):
        """Unknown must not be reported as 'no lock' — they differ."""
        out = run(self.MINI, "LBE-Mini", monitor=None)
        self.assertIn("LOCK STATE UNKNOWN", out)
        self.assertNotIn("NO GPS LOCK", out)

    def test_serial_model_also_reports_lock_when_nmea_is_silent(self):
        out = run(self.SERIAL, "LBE-1421", monitor=self.NO_FIX)
        self.assertIn("NO GPS LOCK", out)

    def test_equipment_list_flags_an_unlocked_gpsdo(self):
        """⚡ rob looked for it here, where device state belongs.

        "where in the dialog will it print out the gpsdo has no sats? I don't
        see that in the attached equipment list."  A bare "✓ GPSDO (LBE-Mini)"
        reads as all-good for a unit flashing red with zero satellites.
        """
        out = preflight_note(("no_fix", 0, ""))
        self.assertIn("NO GPS LOCK", out)
        self.assertIn("0 satellites in use", out)
        self.assertIn("undisciplined", out)

    def test_equipment_list_reports_a_good_lock(self):
        out = preflight_note(("3d", 11, "CM87tj"))
        self.assertIn("GPS lock 3d, 11 satellites", out)
        self.assertNotIn("NO GPS LOCK", out)

    def test_equipment_list_stays_quiet_when_lock_is_unknown(self):
        """Unknown is explained at the grid step; don't half-say it twice."""
        self.assertEqual(preflight_note(None).strip(), "")

    def test_both_no_grid_cases_point_at_the_status_command(self):
        for gid, model in ((self.MINI, "LBE-Mini"), (self.SERIAL, "LBE-1421")):
            with self.subTest(model=model):
                out = run(gid, model)
                self.assertIn("smd gpsdo status", out)
                self.assertIn("only used until then", out)


class PreflightWiringTests(unittest.TestCase):
    """⛔ A helper that is never called is the same as one that does not exist.

    This is the third time in one session that shape has bitten: the Wi-Fi step
    existed but was never called; the failed-join branch existed but its status
    was thrown away; and here, removing the call site from preflight_devices
    left every behaviour test green.  Test the wiring, not only the function.
    """

    def setUp(self):
        self.text = WIZARD.read_text()
        self.preflight = shell_func("preflight_devices")

    def test_the_equipment_list_actually_calls_the_note(self):
        self.assertIn("gpsdo_preflight_note", self.preflight,
                      "gpsdo_preflight_note is defined but preflight_devices "
                      "never calls it — the equipment list would say nothing "
                      "about lock state")

    def test_the_note_follows_the_gpsdo_line_it_annotates(self):
        gpsdo_line = self.preflight.index('_dev_line "$HAVE_GPSDO"')
        note = self.preflight.index("gpsdo_preflight_note")
        self.assertLess(gpsdo_line, note)

    def test_the_note_is_guarded_on_a_gpsdo_being_present(self):
        """No GPSDO must not produce a stray blank annotation."""
        self.assertIn('[ "$HAVE_GPSDO" = 1 ] && gpsdo_preflight_note',
                      self.preflight)


if __name__ == "__main__":
    unittest.main()
