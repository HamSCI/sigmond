# tests/test_bringup_ungated.py
"""An install with nothing attached must complete.

Fargo installs the PM and VM on a Beelink with NO GPSDO, NO RX888 and NO
magnetometer, reboots, and only then gains a hub, an RX888 and a Bodnar
miniGPS.  Refusing that install makes the machine un-buildable at the moment
it is being built.

The warning must stay — losing radiod means NOTHING decodes — but a warning
is not a refusal.
"""

import subprocess
import sys


def _smd_source():
    return open("bin/smd").read()


def test_the_missing_sdr_no_longer_returns_a_failure():
    """The abort was `_err(...)` then `return 1` inside the local branch."""
    src = _smd_source()
    i = src.index("no RX888/SDR on the USB bus")
    window = src[i:i + 900]
    assert "return 1" not in window, (
        "bringup still aborts when no SDR is attached")


def test_the_consequence_is_still_stated_loudly():
    src = _smd_source()
    i = src.index("no RX888/SDR on the USB bus")
    window = src[i - 200:i + 900]
    assert "NOTHING decodes" in window
    assert "_warn(" in window, "the consequence must still be warned about"


def test_it_says_the_station_can_be_completed_later():
    src = _smd_source()
    i = src.index("no RX888/SDR on the USB bus")
    window = src[i:i + 900]
    assert "adopt" in window.lower(), (
        "the operator should be told how to finish once hardware arrives")


def test_smd_still_parses():
    subprocess.run([sys.executable, "-c",
                    "import ast; ast.parse(open('bin/smd').read())"],
                   check=True)
