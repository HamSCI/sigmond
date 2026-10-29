"""The wizard derives the grid from gpsdo-monitor's own position.

AI6VN, 2026-10-29: the wizard asked for a Maidenhead locator while the GPSDO
in the decoder VM was holding a 3D fix on 12 satellites and reporting

    "latitude": 37.3743188, "longitude": -122.2226655, "grid": null

Two defects stacked.  The LBE-Mini HID read ran on the PROXMOX HOST, which
after passthrough owns no USB at all (both controllers vfio-pci, lsusb empty,
no /dev/hidraw*), so it could never succeed.  It fell back to gpsdo-monitor --
which answered -- but gpsdo_lock_report read only the "grid" field, and
gpsdo-monitor leaves that null.  The coordinates it was already holding were
thrown away and the operator typed a grid the hardware knew.

gpsdo-monitor owns that device on every station past passthrough, so asking it
is the primary path; the embedded HID helper is for the true first boot, when
the host still owns USB and gpsdo-monitor is not installed yet.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"
TEXT = WIZARD.read_text()


def shell_func(name: str) -> str:
    m = re.search(r"^%s\(\) \{\n(.*?)^\}\n" % re.escape(name), TEXT, re.S | re.M)
    assert m, "%s() is not in the wizard" % name
    return m.group(1)


def lock_report_snippet() -> str:
    """The python3 -c program inside gpsdo_lock_report()."""
    body = shell_func("gpsdo_lock_report")
    m = re.search(r"python3 -c '\n(.*?)\n' 2>/dev/null", body, re.S)
    assert m, "gpsdo_lock_report no longer embeds a python3 -c program"
    return m.group(1)


SNIPPET = lock_report_snippet()


def run(doc) -> str:
    """Feed the monitor's JSON to the snippet; return its 'fix|sats|grid' line."""
    p = subprocess.run([sys.executable, "-c", SNIPPET],
                       input=json.dumps(doc), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def health(**kw) -> dict:
    h = {"gps_fix": "3D", "sats_used": 12, "grid": None,
         "latitude": 37.3743188, "longitude": -122.2226655}
    h.update(kw)
    return {"model": "lbe-mini", "health": h}


# ── the fault as observed ───────────────────────────────────────────────────

def test_ai6vn_json_yields_the_grid_it_held():
    """The exact document AI6VN's gpsdo-monitor returned while we were asked."""
    assert run(health()) == "3D|12|CM87vi"


def test_an_explicit_grid_is_used_verbatim():
    """A monitor that fills in grid is believed -- we do not second-guess it."""
    assert run(health(grid="EM38ww")) == "3D|12|EM38ww"


def test_missing_position_still_reports_fix_and_satellites():
    out = run(health(latitude=None, longitude=None))
    assert out == "3D|12|"


# ── a position is only as good as the fix behind it ─────────────────────────

def test_no_fix_refuses_the_position():
    """Without a fix the coordinates are stale or zero; reporting them as the
    station's location would silently site the station somewhere it is not."""
    for fix in ("none", "no_fix", "", None):
        out = run(health(gps_fix=fix, gps_locked=False))
        assert out.rsplit("|", 1)[1] == "", "fix=%r leaked a position" % fix


def test_null_island_is_refused():
    assert run(health(latitude=0.0, longitude=0.0)).rsplit("|", 1)[1] == ""


def test_out_of_range_coordinates_are_refused():
    for lat, lon in ((91.0, 0.5), (-90.1, 0.5), (12.0, 180.5), (12.0, -180.1)):
        out = run(health(latitude=lat, longitude=lon))
        assert out.rsplit("|", 1)[1] == "", "(%s,%s) was accepted" % (lat, lon)


def test_non_numeric_coordinates_are_refused():
    assert run(health(latitude="37.4", longitude="-122.2")).rsplit("|", 1)[1] == ""


# ── agreement with the two implementations that already exist ───────────────

POINTS = [
    (37.3743188, -122.2226655),   # AI6VN
    (38.9, -92.3),                # AC0G
    (-33.8688, 151.2093),         # southern + eastern
    (0.0001, 0.0001),             # just off null island
    (89.9, 179.9),                # corner
]


def test_agrees_with_the_wizards_own_hid_helper():
    """One station, two code paths in the same script -- they must not drift."""
    # Load the sibling test module by PATH, never by name: an import that
    # fails here must be a hard error, not a quietly skipped comparison
    # (the 2026-10-29 lesson -- the first version of this check skipped on
    # ModuleNotFoundError and proved nothing for a day).
    import importlib.util
    sib = Path(__file__).with_name("test_wizard_mini_hid_grid.py")
    spec = importlib.util.spec_from_file_location("wizard_hid_helper_probe", sib)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    HELPER = mod.HELPER
    for lat, lon in POINTS:
        assert run(health(latitude=lat, longitude=lon)).rsplit("|", 1)[1] == \
            HELPER.maidenhead(lat, lon), "(%s,%s)" % (lat, lon)


# ── the embedding constraints ───────────────────────────────────────────────

def test_snippet_carries_no_single_quote():
    """It lives inside a single-quoted bash string; one quote ends the program."""
    assert "'" not in SNIPPET


def test_snippet_is_ascii():
    """It travels to no guest here, but the wizard's rule is fleet-wide:
    no byte above 0x7F in anything that may become a guest-agent request."""
    SNIPPET.encode("ascii")


# ── the HID read must not run where there is no USB ─────────────────────────

def test_hid_read_is_not_attempted_when_the_vm_owns_usb():
    """After passthrough the host has no USB; scanning /sys/class/hidraw there
    can only ever fail, and the failure reads as a GPSDO fault."""
    body = shell_func("gpsdo_mini_grid")
    assert "PREFLIGHT_SRC" in body, \
        "gpsdo_mini_grid does not check which side owns the USB controllers"


def test_wizard_does_not_advertise_a_command_that_does_not_exist():
    """`smd gpsdo status` was printed to every operator; smd has no gpsdo verb."""
    offenders = [ln.strip() for ln in TEXT.splitlines()
                 if "smd gpsdo" in ln and not ln.lstrip().startswith("#")]
    assert not offenders, offenders
