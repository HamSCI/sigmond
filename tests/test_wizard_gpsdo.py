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


def ask_grid() -> str:
    text, keep, out = WIZARD.read_text().splitlines(True), False, []
    for ln in text:
        if ln.startswith("ask_grid() {"):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, "ask_grid not found"
    return "".join(out)


def run(gpsdo_id: str, model: str, grid_from_device: str = "") -> str:
    stub = (f'gpsdo_grid(){{ printf "%s\\n" "{grid_from_device}"; '
            f'[ -n "{grid_from_device}" ]; }}')
    script = textwrap.dedent(f"""
        set -u
        rd(){{ read "$@" || true; }}
        say(){{ printf '%s\\n' "$*"; }}
        HAVE_GPSDO=1
        GPSDO_ID="{gpsdo_id}"
        GPSDO_MODEL="{model}"
        GRID=""
        {stub}
        {ask_grid()}
        ask_grid
        printf 'GRID=%s\\n' "$GRID"
    """)
    p = subprocess.run(["bash", "-c", script], input=b"CM87tj\n",
                       capture_output=True, timeout=30)
    return p.stdout.decode()


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

    def test_both_no_grid_cases_point_at_the_status_command(self):
        for gid, model in ((self.MINI, "LBE-Mini"), (self.SERIAL, "LBE-1421")):
            with self.subTest(model=model):
                out = run(gid, model)
                self.assertIn("smd gpsdo status", out)
                self.assertIn("only used until then", out)


if __name__ == "__main__":
    unittest.main()
