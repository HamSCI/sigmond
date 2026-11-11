"""USB hubs measured with an RX-888: does a software port-off really cut VBUS?

Why this exists.  A hub that reports per-port power switching (uhubctl
"ppps") is not thereby a hub that cuts the 5 V.  Most of them only drop the
DATA link: the card leaves the bus and comes back, but it never loses power,
so a fault that needs a real power cycle survives the "reset".  The only
proof is to watch: the RX-888's LED, or a USB power meter in line, must go
dark while the port is off.

⛔ 04b4:00f3 ON RETURN IS NOT PROOF.  After a data-only cut the card comes
back STILL RUNNING its firmware (04b4:00f1), then often fails its
configuration (`can't set config #1, error -110`) and resets ITSELF to the
FX3 bootloader (04b4:00f3).  Counting a bootloader sighting as "power was
cut" called the RSHTECH hub a VBUS switcher on all seven ports (i5-6500T,
2026-11-11); the card's LED and an inline meter both stayed lit.  The
reliable software tell is the FIRST re-appearance after the port comes back
on: 00f1 means the firmware survived, so the power never went away.

Each entry records what was MEASURED, where, and how.  Matching is by the
hub chip's USB IDs, and the same chip ships in many products: a board's
power switch, not its hub chip, decides whether VBUS is cut.  So a match on
a generic chip ID (``product_specific=False``) says "this chip was measured
in <product>", never "your hub does this".  Lenovo's own vendor ID, by
contrast, identifies the product.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

SYSFS_USB = Path("/sys/bus/usb/devices")

CUTS_VBUS = "cuts-vbus"     # a software port-off took the 5 V away (watched)
DATA_ONLY = "data-only"     # the port reports off, the 5 V stays (watched)


@dataclass(frozen=True)
class KnownHub:
    name: str                   # what the operator bought
    chip: str                   # what lsusb shows
    ids: Tuple[str, ...]        # vid:pid of each half (USB3 first)
    vbus: str                   # CUTS_VBUS | DATA_ONLY
    per_port: bool              # False = ganged: one port off means all off
    evidence: str               # what was watched, where, when
    notes: Tuple[str, ...] = ()
    product_specific: bool = False   # the IDs name the product, not just a chip
    buy: str = ""               # where it was bought, when that helps


KNOWN_HUBS: Tuple[KnownHub, ...] = (
    KnownHub(
        name="Lenovo USB-C to 4-port USB-A hub (4X90X21427)",
        chip="VIA VL817",
        ids=("17ef:1039", "17ef:103a"),
        vbus=CUTS_VBUS,
        per_port=True,
        evidence="RX-888 LED went dark under uhubctl off (rob, B3-dev hub "
                 "test, 2026-10)",
        notes=("uhubctl refuses the SuperSpeed half's own location on some "
               "hosts: switch it with --exact (-e), or through its USB2 twin",),
        product_specific=True,
        buy="Amazon ASIN B08B6BHYR5 (availability varies)",
    ),
    KnownHub(
        name="RSHTECH 7-port USB 3 hub (per-port buttons)",
        chip="Realtek RTS5411, two cascaded",
        ids=("0bda:0411", "0bda:5411"),
        vbus=DATA_ONLY,
        per_port=True,
        evidence="uhubctl off on both halves: the RX-888 LED stayed lit for "
                 "33 s and an inline USB meter stayed lit for 21 s (AI6VN-2, "
                 "i5-6500T, 2026-11-11)",
        notes=("the per-port push buttons DO cut power; software does not",
               "the LEDs on the hub body show activity, not power",
               "ports are numbered from the far end: the port nearest the "
               "cable is port 4 of the first chip",
               "on its USB-C cable through a USB-C-to-A adapter it runs at "
               "5 Gb/s in one plug orientation only; flip the USB-C plug if "
               "the hub shows up at 480 Mb/s"),
        buy="Amazon ASIN B091YYTQJQ (~$28)",
    ),
    KnownHub(
        name="UGREEN USB-C to 4-port USB-A hub",
        chip="Genesys Logic",
        ids=("05e3:0626", "05e3:0610"),
        vbus=DATA_ONLY,
        per_port=False,
        evidence="ganged; forced uhubctl off on one port and on all four: the "
                 "RX-888 LED stayed lit (AI6VN, 2026-10-26)",
        notes=("uhubctl refuses it unless forced (-f), and then every port "
               "goes off together",
               "a whole-hub data reset through the upstream root port brings a "
               "healthy card back at 5 Gb/s (AI6VN, 2026-10-24)",
               "after the firmware loads, the card can stay at 480 Mb/s until "
               "a short ~2 s blip re-trains it"),
    ),
    KnownHub(
        name="VIA VL817-based hub (AC0G-B4)",
        chip="VIA VL817",
        ids=("2109:2817", "2109:0817"),
        vbus=DATA_ONLY,
        per_port=True,
        evidence="a meter read 4.98 V through every uhubctl off; the LEDs "
                 "stayed lit (AC0G-B4, 2026-10-14)",
        notes=("the same VIA chip in Lenovo's hub (17ef:1039) does cut VBUS: "
               "the board decides, not the chip",),
    ),
    KnownHub(
        name="Anker USB 3 hub (AC0G-B4)",
        chip="Anker 291a:8307",
        ids=("291a:8307",),
        vbus=DATA_ONLY,
        per_port=True,
        evidence="a meter read 4.98 V through every uhubctl off (AC0G-B4, "
                 "2026-10-14)",
        product_specific=True,
    ),
)


def lookup(ids: Iterable[str]) -> Optional[KnownHub]:
    """The inventory entry for a hub with any of these vid:pid IDs."""
    want = {i.lower() for i in ids if i}
    for hub in KNOWN_HUBS:
        if want & set(hub.ids):
            return hub
    return None


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def hub_ids(devpaths: Iterable[Optional[str]],
            root: Path = SYSFS_USB) -> Tuple[str, ...]:
    """vid:pid of each hub devpath ("5-1.1"; a bare bus number is a root hub
    and is skipped: no root hub is in the inventory)."""
    out = []
    for dp in devpaths:
        if not dp or ("-" not in dp and "." not in dp):
            continue
        vid, pid = _read(root / dp / "idVendor"), _read(root / dp / "idProduct")
        if vid and pid:
            out.append(f"{vid}:{pid}")
    return tuple(out)


def describe(hub: KnownHub) -> str:
    """One paragraph an operator can act on."""
    if hub.vbus == CUTS_VBUS:
        what = ("a software port-off REALLY CUTS the RX-888's power, so a "
                "reset can clear faults that only a power cycle clears")
        record = ("record it for this station with "
                  "[radiod] recover_port_verified = true")
    else:
        what = ("a software port-off drops the DATA LINK ONLY; the 5 V stays "
                "on, so a fault that needs a power cycle needs a hand at the "
                "hub")
        record = ("record it for this station with "
                  "[radiod] recover_port_data_only = true")
    scope = ("" if hub.product_specific else
             f"  That was measured on that product, and its chip ({hub.chip}) "
             f"ships in other hubs that may behave differently: watch the "
             f"RX-888's LED through `sigmond-sdr-recover --off` to be sure.")
    ganged = "" if hub.per_port else "  It is GANGED: one port off turns every port off."
    notes = "".join(f"  Note: {n}." for n in hub.notes)
    return (f"known hub: {hub.name} ({hub.chip}, {'/'.join(hub.ids)}): {what} "
            f"[{hub.evidence}]; {record}.{ganged}{scope}{notes}")


def first_return_verdict(product: Optional[str]) -> str:
    """What the card's FIRST re-appearance after a port-off says about VBUS.

    00f1 first means the FX3 kept its RAM firmware through the outage, which
    it cannot do without power: the port did NOT cut VBUS.  00f3 first is
    what a real cut looks like, but a card can also reset itself to 00f3, so
    it is consistent with a cut, not proof of one.
    """
    if product == "00f1":
        return ("the card came back STILL RUNNING its firmware (04b4:00f1), "
                "so it never lost power: this port did NOT cut VBUS, it "
                "dropped the data link only")
    if product == "00f3":
        return ("the card came back in its bootloader (04b4:00f3), which is "
                "what a real power cut looks like; a card can also reset "
                "itself to the bootloader, so only the LED or a meter proves "
                "it")
    return "the card's first return was not seen, so this run says nothing about VBUS"
