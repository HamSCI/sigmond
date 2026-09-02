# Station Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a sigmond station install with no hardware attached, then adopt hardware and clients explicitly as they appear — including a radiod on another machine.

**Architecture:** Discovery (`sigmond.discovery.*`) and selection (`sigmond.sources`) already exist and already model this; neither is consulted by install. This plan adds one thin layer of pure functions — recognise a kit, offer what is adoptable, plan an adoption — wires it into `smd status` and a new `smd adopt`, and stops install from gating on hardware.

**Tech Stack:** Python ≥3.10, pytest. Consumes `sigmond.sources` (`SourceKey`, `InventoryRow`, `ClientSources`, `inventory`), `sigmond.hardware` (`detect_local_sdr`, `detect_gpsdo`, `detect_magnetometer`, `Presence`), `sigmond.discovery`.

**Spec:** `docs/superpowers/specs/2026-09-02-station-adoption-design.md`

## Global Constraints

- **Install activates nothing.** Every component installs present-and-dormant on every station, whatever is or is not attached. Install records what discovery saw and acts on none of it.
- **Adoption is explicit.** Hardware appearing is never an instruction. A device is reported detected-but-unadopted and nothing starts until asked. On 2026-09-01 a unit nobody asked to run took B4's timing chain down twice in one day.
- **Membership comes from the hostname, never from an operator answer.** `DASI\d{3}` **and** in the roster → DASI2 site. No pattern match → ordinary station. Pattern match but **not** in the roster → **REFUSE**, naming both remedies.
- **The kit pattern is not the membership.** DASI2 = rx888 + LBE GPSDO + RM3100. Anyone assembling that kit gets the identical configuration offered; they simply have no roster entry, no grant identity, and use wsprdaemon gw2.
- **Adoption composes; it adds no engine.** Use the existing install → configure → enable → start machinery and `sources.ClientSources`.
- Style: type hints throughout, one responsibility per module, tests under `tests/`.
- Run tests: `.venv/bin/python -m pytest <path> -q -p no:cacheprovider` from the repo root.
- ⛔ This plan is VM-side only. RAC registrar selection, ssh access, and manifest push/pull belong to the separate PM spec. Do not implement them here.

---

## File Structure

| File | Responsibility |
|---|---|
| `lib/sigmond/adoption.py` | Pure decision layer: `StationInventory`, `recognise`, `offers`, `plan`. No I/O. |
| `lib/sigmond/station_identity.py` | Hostname → membership → identity, against the shipped roster. Pure apart from reading the roster file. |
| `config/dasi2-roster.toml` | The twenty DASI2 sites and their PSWS IDs. Versioned, offline-readable. |
| `bin/smd` (≈5419, ≈8507) | Lift the rx888 abort; add the `status` adoptable section and the `adopt` verb. |
| `tests/test_adoption.py`, `tests/test_station_identity.py`, `tests/test_adopt_cli.py` | |

---

### Task 1: The decision layer — recognise and offers

**Files:**
- Create: `lib/sigmond/adoption.py`
- Test: `tests/test_adoption.py`

**Interfaces:**
- Consumes: `sigmond.sources.SourceKey` — a frozen dataclass `SourceKey(type: str, identifier: str)` where `type` is one of `"radiod"`, `"kiwisdr"`, `"usb"`; `str(key)` renders `"type:identifier"`; `SourceKey.parse("usb:abc")` returns one.
- Produces: `StationInventory`, `DASI2_KIT`, `Offer`, `recognise(inv) -> str | None`, `offers(inv, adopted) -> list[Offer]`. Tasks 3, 5 and 6 rely on these names.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_adoption.py
"""What can this station adopt, and does it look like a known kit?

Pure over an inventory, so the whole decision surface tests with no USB bus
and no LAN — which is what lets Fargo be dry-run before anyone travels.
"""

import pytest

from sigmond.adoption import DASI2_KIT, Offer, StationInventory, offers, recognise
from sigmond.sources import SourceKey


def _inv(hardware=(), sources=()):
    return StationInventory(hardware=frozenset(hardware), sources=tuple(sources))


DASI2 = ("rx888", "gpsdo", "magnetometer")
RX = SourceKey(type="usb", identifier="04b4:00f1:0009061C028B1629")
GPS = SourceKey(type="usb", identifier="1dd2:2211:mini01")
REMOTE = SourceKey(type="radiod", identifier="bee3-status.local")


class TestRecognise:

    def test_the_full_kit_is_recognised(self):
        assert recognise(_inv(DASI2)) == "dasi2"

    def test_fargo_is_not_a_kit(self):
        """rx888 + miniGPS, no magnetometer and no TS-1 for weeks."""
        assert recognise(_inv(("rx888", "gpsdo"))) is None

    def test_extra_hardware_still_recognises(self):
        """Someone may exceed the set; that is still a DASI2-alike kit."""
        assert recognise(_inv(DASI2 + ("kiwisdr",))) == "dasi2"

    def test_nothing_attached_is_not_a_kit(self):
        assert recognise(_inv()) is None

    def test_the_kit_is_stated_once(self):
        assert DASI2_KIT == frozenset({"rx888", "gpsdo", "magnetometer"})


class TestOffers:

    def test_a_matching_kit_is_offered_as_one_thing(self):
        got = offers(_inv(DASI2, [RX, GPS]), adopted=frozenset())
        assert [o.name for o in got] == ["dasi2"]
        assert got[0].kind == "kit"

    def test_without_a_kit_each_source_is_offered_separately(self):
        got = offers(_inv(("rx888", "gpsdo"), [RX, GPS]), adopted=frozenset())
        assert {o.name for o in got} == {str(RX), str(GPS)}
        assert all(o.kind == "source" for o in got)

    def test_an_adopted_source_stops_being_offered(self):
        got = offers(_inv(("rx888", "gpsdo"), [RX, GPS]), adopted=frozenset({RX}))
        assert [o.name for o in got] == [str(GPS)]

    def test_a_remote_radiod_is_offered_with_no_local_hardware(self):
        """The no-hardware station: radiod lives on another machine."""
        got = offers(_inv((), [REMOTE]), adopted=frozenset())
        assert [o.name for o in got] == [str(REMOTE)]
        assert got[0].kind == "source"

    def test_nothing_visible_offers_nothing(self):
        assert offers(_inv(), adopted=frozenset()) == []

    def test_a_fully_adopted_kit_offers_nothing(self):
        got = offers(_inv(DASI2, [RX, GPS]), adopted=frozenset({RX, GPS}))
        assert got == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adoption.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'sigmond.adoption'`

- [ ] **Step 3: Write minimal implementation**

```python
# lib/sigmond/adoption.py
"""What could this station adopt, and does it look like a kit we know?

Install lays the groundwork and activates nothing; the operator adopts.  This
module is the decision half of that: pure functions over an inventory, so the
whole surface tests without a USB bus or a LAN, and a station's behaviour can
be predicted before anyone travels to it.

⛔ Nothing here starts anything.  Hardware appearing is not an instruction.
On 2026-09-01 a unit that nobody asked to run was started by `smd apply` and
took B4's timing chain down twice in one day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Tuple

from .sources import SourceKey

#: The hardware that defines a DASI2 kit.  Membership in the funded programme
#: is decided by the HOSTNAME (see `station_identity`), never by this — anyone
#: who assembles the kit gets the identical configuration offered.
DASI2_KIT: FrozenSet[str] = frozenset({"rx888", "gpsdo", "magnetometer"})


@dataclass(frozen=True)
class StationInventory:
    """What is visible: hardware on the local bus, and selectable sources.

    `hardware` holds coarse kind names ("rx888", "gpsdo", "magnetometer") as
    reported by `sigmond.hardware`.  `sources` holds the stable keys
    `sigmond.sources.inventory()` produced, which include LAN radiods.
    """
    hardware: FrozenSet[str] = frozenset()
    sources: Tuple[SourceKey, ...] = ()


@dataclass(frozen=True)
class Offer:
    """Something adoptable that has not been adopted."""
    name: str            # "dasi2", or str(SourceKey)
    kind: str            # "kit" | "source"
    sources: Tuple[SourceKey, ...] = ()


def recognise(inv: StationInventory) -> Optional[str]:
    """Name the kit this station's hardware matches, if any.

    A superset still matches: a station may exceed the kit and remain a
    recognisable one.
    """
    if DASI2_KIT <= inv.hardware:
        return "dasi2"
    return None


def offers(inv: StationInventory,
           adopted: FrozenSet[SourceKey]) -> List[Offer]:
    """What could be adopted here that has not been.

    A recognised kit is offered as ONE thing, so provisioning a fleet is one
    decision rather than three.  Otherwise each unadopted source stands alone,
    which is the Fargo case and the no-local-hardware case both.
    """
    remaining = tuple(s for s in inv.sources if s not in adopted)
    if not remaining:
        return []
    kit = recognise(inv)
    if kit is not None:
        return [Offer(name=kit, kind="kit", sources=remaining)]
    return [Offer(name=str(s), kind="source", sources=(s,)) for s in remaining]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adoption.py -q -p no:cacheprovider`
Expected: PASS, 11 passed

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: 2085 baseline + 11 new = 2096 passed

- [ ] **Step 6: Commit**

```bash
git add lib/sigmond/adoption.py tests/test_adoption.py
git commit -m "adoption: what this station could adopt, as pure functions"
```

---

### Task 2: Station identity from the hostname

**Files:**
- Create: `lib/sigmond/station_identity.py`, `config/dasi2-roster.toml`
- Test: `tests/test_station_identity.py`

**Interfaces:**
- Produces: `StationIdentity` (fields `dasi2_site: bool`, `psws_station: str | None`, `psws_instrument: str | None`, `hostname: str`), `UnrosteredDasiName` (exception), `identify(hostname, roster) -> StationIdentity`, `load_roster(path) -> dict`. Tasks 3 and 6 rely on these.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_station_identity.py
"""A station's identity is read from its hostname, not asked for.

DASI2 machines are named DASI001-DASI020 and Michael pre-defines each one's
PSWS Station and Instrument IDs, so provisioning a fleet is: name the machine,
and registrar plus identity follow with nothing to type.

⛔ A DASI-named host ABSENT from the roster is refused.  Guessing is silently
wrong either way: called DASI2 it gets the wrong registrar and no PSWS IDs;
called ordinary it runs forever under a fleet name it does not own.
"""

import pytest

from sigmond.station_identity import (
    StationIdentity, UnrosteredDasiName, identify, load_roster,
)

ROSTER = {
    "DASI001": {"psws_station": "S000201", "psws_instrument": "I000201"},
    "DASI007": {"psws_station": "S000207", "psws_instrument": "I000207"},
}


def test_a_rostered_dasi_host_is_a_site():
    ident = identify("DASI007", ROSTER)
    assert ident.dasi2_site is True
    assert ident.psws_station == "S000207"
    assert ident.psws_instrument == "I000207"


def test_an_ordinary_hostname_is_not_a_site():
    ident = identify("fargo-1", ROSTER)
    assert ident.dasi2_site is False
    assert ident.psws_station is None
    assert ident.psws_instrument is None


def test_a_dasi_name_not_in_the_roster_is_refused():
    """A typo, a machine ahead of the roster, or someone imitating the fleet."""
    with pytest.raises(UnrosteredDasiName) as exc:
        identify("DASI019", ROSTER)
    msg = str(exc.value)
    assert "DASI019" in msg
    assert "roster" in msg.lower()


def test_the_refusal_names_both_remedies():
    with pytest.raises(UnrosteredDasiName) as exc:
        identify("DASI019", ROSTER)
    msg = str(exc.value).lower()
    assert "hostname" in msg          # fix the name
    assert "roster" in msg            # or add the entry


def test_matching_is_case_insensitive_on_the_prefix():
    """Hostnames are commonly lowercased by DHCP and by the installer."""
    ident = identify("dasi007", ROSTER)
    assert ident.dasi2_site is True
    assert ident.psws_station == "S000207"


def test_a_name_merely_containing_dasi_is_ordinary():
    """`DASI\\d{3}` must anchor — `mydasi001x` is not a fleet machine."""
    ident = identify("mydasi001x", ROSTER)
    assert ident.dasi2_site is False


def test_the_hostname_is_carried_through():
    assert identify("fargo-1", ROSTER).hostname == "fargo-1"


def test_the_shipped_roster_parses_and_is_well_formed():
    roster = load_roster()
    assert roster, "the shipped roster is empty"
    for name, entry in roster.items():
        assert name.upper() == name, f"{name} should be upper-case"
        assert entry["psws_station"], f"{name} has no psws_station"
        assert entry["psws_instrument"], f"{name} has no psws_instrument"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_station_identity.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'sigmond.station_identity'`

- [ ] **Step 3: Write the roster**

Twenty entries. The PSWS IDs below are PLACEHOLDER values in the shape Michael will supply; the file is the artifact he edits. Write all twenty so the shape is fixed and nothing has to guess later.

```toml
# config/dasi2-roster.toml
#
# The DASI2 fleet.  A host whose name matches DASI\d{3} is a funded site if and
# only if it appears here — an unrostered DASI name is REFUSED rather than
# guessed, because guessing either way is silently wrong.
#
# Shipped in the repo deliberately: a field install has no network when it
# needs this.
#
# ⚠ psws_station / psws_instrument below are PLACEHOLDERS in the correct shape.
# Michael pre-defines the real values; replace them before any fleet build.

[DASI001]
psws_station = "S000201"
psws_instrument = "I000201"

[DASI002]
psws_station = "S000202"
psws_instrument = "I000202"

[DASI003]
psws_station = "S000203"
psws_instrument = "I000203"

[DASI004]
psws_station = "S000204"
psws_instrument = "I000204"

[DASI005]
psws_station = "S000205"
psws_instrument = "I000205"

[DASI006]
psws_station = "S000206"
psws_instrument = "I000206"

[DASI007]
psws_station = "S000207"
psws_instrument = "I000207"

[DASI008]
psws_station = "S000208"
psws_instrument = "I000208"

[DASI009]
psws_station = "S000209"
psws_instrument = "I000209"

[DASI010]
psws_station = "S000210"
psws_instrument = "I000210"

[DASI011]
psws_station = "S000211"
psws_instrument = "I000211"

[DASI012]
psws_station = "S000212"
psws_instrument = "I000212"

[DASI013]
psws_station = "S000213"
psws_instrument = "I000213"

[DASI014]
psws_station = "S000214"
psws_instrument = "I000214"

[DASI015]
psws_station = "S000215"
psws_instrument = "I000215"

[DASI016]
psws_station = "S000216"
psws_instrument = "I000216"

[DASI017]
psws_station = "S000217"
psws_instrument = "I000217"

[DASI018]
psws_station = "S000218"
psws_instrument = "I000218"

[DASI019]
psws_station = "S000219"
psws_instrument = "I000219"

[DASI020]
psws_station = "S000220"
psws_instrument = "I000220"
```

- [ ] **Step 4: Write the module**

```python
# lib/sigmond/station_identity.py
"""Who is this station?  Read from the hostname, never asked for.

DASI2 machines are named DASI001-DASI020 (later DASI plus three digits
generally), and each one's PSWS Station and Instrument IDs are pre-defined in
the roster shipped beside this file.  So provisioning a fleet is: name the
machine, and identity follows with nothing to type.

This extends a decision the install redesign already locked — "radiod status
name is auto-composed from the hostname, zero operator input" — to the
station's identity as a whole.

Membership has exactly two consequences: the PSWS IDs read here, and the RAC
registrar chosen on the PM (`vpn.hamsci.org` for a site, wsprdaemon gw2 for
everyone else).  The PM side is a separate spec; both agree on
`/etc/sigmond/station.toml`.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

#: Anchored: `mydasi001x` is not a fleet machine.
_DASI_NAME = re.compile(r"^DASI(\d{3})$", re.IGNORECASE)

DEFAULT_ROSTER = Path(__file__).resolve().parents[2] / "config" / "dasi2-roster.toml"


class UnrosteredDasiName(Exception):
    """A DASI-named host that the roster does not know.

    Refused rather than guessed.  Called a site it would take the DASI2
    registrar with no PSWS identity; called ordinary it would run forever
    under a fleet name it does not own.
    """


@dataclass(frozen=True)
class StationIdentity:
    hostname: str
    dasi2_site: bool
    psws_station: Optional[str] = None
    psws_instrument: Optional[str] = None


def load_roster(path: Path = DEFAULT_ROSTER) -> Dict[str, dict]:
    """The shipped fleet roster, keyed by upper-case host name."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return {k.upper(): v for k, v in data.items()}


def identify(hostname: str, roster: Optional[Dict[str, dict]] = None
             ) -> StationIdentity:
    """Decide membership from the hostname.

    Raises `UnrosteredDasiName` when the name looks like a fleet machine and
    the roster does not list it.
    """
    if roster is None:
        roster = load_roster()
    match = _DASI_NAME.match(hostname.strip())
    if match is None:
        return StationIdentity(hostname=hostname, dasi2_site=False)

    key = hostname.strip().upper()
    entry = roster.get(key)
    if entry is None:
        raise UnrosteredDasiName(
            f"hostname {hostname!r} matches the DASI fleet pattern but is not "
            f"in the roster, so this machine's identity and RAC registrar "
            f"cannot be decided. Either correct the hostname, or add a "
            f"{key} entry to the roster."
        )
    return StationIdentity(
        hostname=hostname,
        dasi2_site=True,
        psws_station=entry["psws_station"],
        psws_instrument=entry["psws_instrument"],
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_station_identity.py -q -p no:cacheprovider`
Expected: PASS, 8 passed

- [ ] **Step 6: Commit**

```bash
git add lib/sigmond/station_identity.py config/dasi2-roster.toml tests/test_station_identity.py
git commit -m "identity: the hostname says which station this is, and the roster says whether it is ours"
```

---

### Task 3: The adoption plan

**Files:**
- Modify: `lib/sigmond/adoption.py`
- Test: `tests/test_adoption.py` (append)

**Interfaces:**
- Consumes: `Offer`, `StationInventory` (Task 1); `StationIdentity` (Task 2).
- Produces: `AdoptionPlan` (fields `components: tuple[str, ...]`, `prefills: dict`, `ask: tuple[str, ...]`), `plan(offer, inv, identity) -> AdoptionPlan`. Task 6 relies on it.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_adoption.py
from sigmond.adoption import AdoptionPlan, plan
from sigmond.station_identity import StationIdentity

SITE = StationIdentity(hostname="DASI007", dasi2_site=True,
                       psws_station="S000207", psws_instrument="I000207")
ALIKE = StationIdentity(hostname="fargo-1", dasi2_site=False)


class TestPlan:

    def test_a_rostered_kit_asks_for_nothing(self):
        """Fleet provisioning is meant to be zero-input."""
        inv = _inv(DASI2, [RX, GPS])
        p = plan(offers(inv, frozenset())[0], inv, SITE)
        assert p.ask == ()
        assert p.prefills["psws_station"] == "S000207"
        assert p.prefills["psws_instrument"] == "I000207"

    def test_an_alike_kit_gets_the_same_components_without_identity(self):
        """Same hardware, same configuration — simply not a funded site."""
        inv = _inv(DASI2, [RX, GPS])
        site_plan = plan(offers(inv, frozenset())[0], inv, SITE)
        alike_plan = plan(offers(inv, frozenset())[0], inv, ALIKE)
        assert alike_plan.components == site_plan.components
        assert "psws_station" not in alike_plan.prefills
        assert "psws_station" in alike_plan.ask

    def test_the_radiod_status_name_comes_from_the_hostname(self):
        inv = _inv(("rx888",), [RX])
        p = plan(offers(inv, frozenset())[0], inv, ALIKE)
        assert p.prefills["radiod_status_name"] == "fargo-1-status.local"

    def test_an_rx888_brings_radiod_and_its_infrastructure(self):
        inv = _inv(("rx888",), [RX])
        p = plan(offers(inv, frozenset())[0], inv, ALIKE)
        assert set(p.components) == {"ka9q-radio", "ka9q-web", "igmp-querier"}

    def test_a_gpsdo_brings_its_monitor(self):
        inv = _inv(("gpsdo",), [GPS])
        p = plan(offers(inv, frozenset())[0], inv, ALIKE)
        assert "gpsdo-monitor" in p.components

    def test_a_remote_radiod_brings_no_local_radiod(self):
        """The no-hardware station consumes someone else's radiod."""
        inv = _inv((), [REMOTE])
        p = plan(offers(inv, frozenset())[0], inv, ALIKE)
        assert "ka9q-radio" not in p.components

    def test_a_kit_plan_covers_every_kit_component(self):
        inv = _inv(DASI2, [RX, GPS])
        p = plan(offers(inv, frozenset())[0], inv, SITE)
        assert set(p.components) == {
            "ka9q-radio", "ka9q-web", "igmp-querier",
            "gpsdo-monitor", "mag-recorder"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adoption.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'AdoptionPlan'`

- [ ] **Step 3: Append the implementation to `lib/sigmond/adoption.py`**

```python
#: What each piece of hardware brings with it.  radiod needs ka9q-web and
#: igmp-querier because multicast will not work without the querier.
_HARDWARE_COMPONENTS = {
    "rx888": ("ka9q-radio", "ka9q-web", "igmp-querier"),
    "gpsdo": ("gpsdo-monitor",),
    "magnetometer": ("mag-recorder",),
}


@dataclass(frozen=True)
class AdoptionPlan:
    """What adopting an offer would do, and what it still needs to be told."""
    components: Tuple[str, ...] = ()
    prefills: dict = field(default_factory=dict)
    ask: Tuple[str, ...] = ()


def _hardware_for(offer: Offer, inv: StationInventory) -> FrozenSet[str]:
    if offer.kind == "kit":
        return inv.hardware
    # A single source: a local USB device implies its hardware kind; a remote
    # radiod implies none, because the SDR is not ours.
    kinds = set()
    for key in offer.sources:
        if key.type != "usb":
            continue
        kinds |= {k for k in inv.hardware if k in _HARDWARE_COMPONENTS}
    return frozenset(kinds)


def plan(offer: Offer, inv: StationInventory,
         identity: "StationIdentity") -> AdoptionPlan:
    """What adopting this offer entails.

    Prefills split the way identity does: anything derivable from the kit or
    the host is filled; grant identity comes from the roster on a site and is
    ASKED on a DASI2-alike, which is a first-class station with no roster
    entry rather than a degraded one.
    """
    hardware = _hardware_for(offer, inv)
    components: list = []
    for kind in sorted(hardware):
        for comp in _HARDWARE_COMPONENTS.get(kind, ()):
            if comp not in components:
                components.append(comp)

    prefills = {
        # Auto-composed from the hostname — zero operator input, the rule the
        # install redesign already locked for radiod.
        "radiod_status_name": f"{identity.hostname}-status.local",
    }
    ask: list = []
    if identity.dasi2_site:
        prefills["psws_station"] = identity.psws_station
        prefills["psws_instrument"] = identity.psws_instrument
    else:
        ask.extend(("psws_station", "psws_instrument"))

    return AdoptionPlan(components=tuple(components),
                        prefills=prefills, ask=tuple(ask))
```

Add the import at the top of `lib/sigmond/adoption.py`:

```python
from .station_identity import StationIdentity
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adoption.py -q -p no:cacheprovider`
Expected: PASS, 18 passed

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/adoption.py tests/test_adoption.py
git commit -m "adoption: what adopting an offer entails, and what it must still ask"
```

---

### Task 4: Install stops gating on hardware

**Files:**
- Modify: `bin/smd` around line 5419
- Test: `tests/test_bringup_ungated.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no new API. Behaviour change only.

- [ ] **Step 1: Read the current abort**

Run: `sed -n '5410,5430p' bin/smd`

You will see, inside the `elif prof.local_radiod_infra:` branch:

```python
        if not _detect_local_sdr():
            _err('bringup: no RX888/SDR on the USB bus — without it radiod '
                 'cannot run and NOTHING decodes (WSPR / PSK / GRAPE all dark). '
                 'Attach the RX888 and re-run, or use --remote-radiod / the '
                 '"client" profile to bind a remote radiod.')
            return 1
        local = True
```

- [ ] **Step 2: Write the failing test**

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_bringup_ungated.py -q -p no:cacheprovider`
Expected: FAIL on `test_the_missing_sdr_no_longer_returns_a_failure` — `return 1` is still there.

- [ ] **Step 4: Replace the abort with a warning**

In `bin/smd`, replace the block from Step 1 with:

```python
        if not _detect_local_sdr():
            # Install lays the groundwork and activates nothing.  Fargo
            # installs with NOTHING attached and gains the RX888 after a
            # reboot; refusing here makes the machine un-buildable at the
            # moment it is being built.  The consequence still gets said
            # loudly — a warning is not a refusal.
            _warn('bringup: no RX888/SDR on the USB bus — without it radiod '
                  'cannot run and NOTHING decodes (WSPR / PSK / GRAPE all '
                  'dark). Installing anyway; the station stays dormant until '
                  'an SDR is attached and adopted with `smd adopt`.')
        local = True
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_bringup_ungated.py -q -p no:cacheprovider`
Expected: PASS, 4 passed

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: all passing; no test asserted the old abort.

- [ ] **Step 7: Commit**

```bash
git add bin/smd tests/test_bringup_ungated.py
git commit -m "bringup: install with nothing attached, and say what is missing"
```

---

### Task 5: `smd status` shows what could be adopted

**Files:**
- Modify: `bin/smd` (`cmd_status`, ≈8507)
- Test: `tests/test_status_adoptable.py`

**Interfaces:**
- Consumes: `sigmond.adoption.offers`, `StationInventory` (Task 1).
- Produces: `_adoption_section(inv, adopted) -> list[str]` in `bin/smd`, returning renderable lines. Task 6 does not depend on it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_status_adoptable.py
"""Status must show what is present but not adopted.

A station that has hardware attached and is doing nothing with it should say
so plainly.  Silence there is indistinguishable from having no hardware, and
this project has spent a day on the cost of that particular confusion.
"""

import importlib.util
import pytest

from sigmond.adoption import StationInventory
from sigmond.sources import SourceKey


def _smd():
    spec = importlib.util.spec_from_loader("smd_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(open("bin/smd").read(), "bin/smd", "exec"), mod.__dict__)
    return mod


RX = SourceKey(type="usb", identifier="04b4:00f1:serial")


def test_an_unadopted_source_is_listed():
    lines = _smd()._adoption_section(
        StationInventory(hardware=frozenset({"rx888"}), sources=(RX,)),
        adopted=frozenset())
    text = "\n".join(lines)
    assert str(RX) in text
    assert "not adopted" in text.lower()


def test_an_adopted_source_is_not_offered():
    lines = _smd()._adoption_section(
        StationInventory(hardware=frozenset({"rx888"}), sources=(RX,)),
        adopted=frozenset({RX}))
    assert str(RX) not in "\n".join(lines)


def test_a_recognised_kit_is_named_as_one_offer():
    lines = _smd()._adoption_section(
        StationInventory(
            hardware=frozenset({"rx888", "gpsdo", "magnetometer"}),
            sources=(RX,)),
        adopted=frozenset())
    assert "dasi2" in "\n".join(lines).lower()


def test_nothing_adoptable_renders_nothing():
    lines = _smd()._adoption_section(
        StationInventory(), adopted=frozenset())
    assert lines == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_status_adoptable.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: module has no attribute '_adoption_section'`

- [ ] **Step 3: Add the renderer to `bin/smd`, immediately above `def cmd_status(`**

```python
def _adoption_section(inv, adopted) -> list:
    """Lines describing what is visible here and not yet adopted.

    Reports; never acts.  A station with hardware attached and nothing running
    should say so — silence is indistinguishable from having no hardware.
    """
    from sigmond.adoption import offers as _offers

    pending = _offers(inv, adopted)
    if not pending:
        return []
    lines = ['', 'adoptable:']
    for offer in pending:
        if offer.kind == 'kit':
            lines.append(
                f'  {offer.name:<28} recognised kit, not adopted '
                f'({len(offer.sources)} source(s))')
        else:
            lines.append(f'  {offer.name:<28} detected, not adopted')
    lines.append('  run `smd adopt <name>` to configure, enable and start it')
    return lines
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_status_adoptable.py -q -p no:cacheprovider`
Expected: PASS, 4 passed

- [ ] **Step 5: Commit**

```bash
git add bin/smd tests/test_status_adoptable.py
git commit -m "status: say what is attached and not adopted"
```

---

### Task 6: `smd adopt`

**Files:**
- Modify: `bin/smd` (add the verb and its parser)
- Test: `tests/test_adopt_cli.py`

**Interfaces:**
- Consumes: `adoption.offers`, `adoption.plan`, `StationInventory` (Tasks 1, 3); `station_identity.identify`, `UnrosteredDasiName` (Task 2); `sources.ClientSources` (existing).
- Produces: `cmd_adopt(args) -> int` in `bin/smd`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_adopt_cli.py
"""`smd adopt` turns an offer into a running configuration — and only then.

Adoption is the ONLY thing that starts anything in this design.  Everything
before it observes.
"""

import importlib.util
import types

import pytest


def _smd():
    spec = importlib.util.spec_from_loader("smd_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(open("bin/smd").read(), "bin/smd", "exec"), mod.__dict__)
    return mod


def test_adopting_an_unknown_name_fails_and_says_what_is_available():
    mod = _smd()
    mod._station_inventory = lambda: __import__(
        "sigmond.adoption", fromlist=["StationInventory"]).StationInventory()
    rc = mod.cmd_adopt(types.SimpleNamespace(name="nope", dry_run=True))
    assert rc != 0


def test_an_unrostered_dasi_hostname_refuses_before_doing_anything():
    """The refusal must come BEFORE any component is touched."""
    from sigmond.station_identity import UnrosteredDasiName, identify
    with pytest.raises(UnrosteredDasiName):
        identify("DASI019", {"DASI001": {"psws_station": "S1",
                                         "psws_instrument": "I1"}})


def test_dry_run_reports_the_plan_and_changes_nothing():
    mod = _smd()
    from sigmond.adoption import StationInventory
    from sigmond.sources import SourceKey
    rx = SourceKey(type="usb", identifier="04b4:00f1:s")
    mod._station_inventory = lambda: StationInventory(
        hardware=frozenset({"rx888"}), sources=(rx,))
    mod._adopted_sources = lambda: frozenset()
    mod._station_identity = lambda: __import__(
        "sigmond.station_identity", fromlist=["StationIdentity"]
    ).StationIdentity(hostname="fargo-1", dasi2_site=False)
    rc = mod.cmd_adopt(types.SimpleNamespace(name=str(rx), dry_run=True))
    assert rc == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adopt_cli.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: module has no attribute 'cmd_adopt'`

- [ ] **Step 3: Add the command to `bin/smd`, below `_adoption_section`**

```python
def _station_inventory():
    """What is visible here, as an adoption.StationInventory."""
    from sigmond.adoption import StationInventory
    from sigmond.hardware import (detect_gpsdo, detect_local_sdr,
                                  detect_magnetometer)
    from sigmond import sources as _sources
    from sigmond.discovery import dict_to_obs, load_cache

    hardware = set()
    if detect_local_sdr():
        hardware.add('rx888')
    if detect_gpsdo():
        hardware.add('gpsdo')
    if detect_magnetometer():
        hardware.add('magnetometer')

    # load_cache() returns a dict skeleton whose "observations" are
    # serialised; dict_to_obs rehydrates them into what inventory() wants.
    try:
        cache = load_cache() or {}
        observations = [dict_to_obs(d) for d in cache.get("observations", [])]
    except Exception:            # discovery must never stop adoption reporting
        observations = []
    rows = _sources.inventory(observations)
    return StationInventory(hardware=frozenset(hardware),
                            sources=tuple(r.key for r in rows))


def _adopted_sources():
    """Every source already selected by some client."""
    from sigmond import sources as _sources
    adopted = set()
    for _client, sel in _sources.load_all_selections().items():
        adopted |= set(sel.selected)
    return frozenset(adopted)


def _station_identity():
    import socket
    from sigmond.station_identity import identify
    return identify(socket.gethostname().split('.')[0])


def cmd_adopt(args) -> int:
    """Adopt an offer: configure, enable and start what it brings.

    The only verb in this design that starts anything.  Everything upstream
    observes and reports.
    """
    from sigmond.adoption import offers as _offers, plan as _plan
    from sigmond.station_identity import UnrosteredDasiName

    inv = _station_inventory()
    adopted = _adopted_sources()
    pending = _offers(inv, adopted)
    match = next((o for o in pending if o.name == args.name), None)
    if match is None:
        _err(f'adopt: nothing adoptable named {args.name!r}. '
             f'Available: {[o.name for o in pending] or "nothing"}')
        return 1

    try:
        identity = _station_identity()
    except UnrosteredDasiName as exc:
        # Refuse BEFORE touching a component: a machine whose identity cannot
        # be decided must not be half-configured under a name it may not own.
        _err(str(exc))
        return 1

    p = _plan(match, inv, identity)
    _info(f'adopt {match.name}: components {list(p.components)}')
    for key, value in sorted(p.prefills.items()):
        _info(f'  prefill {key} = {value}')
    for field_name in p.ask:
        _info(f'  needs {field_name}')
    if getattr(args, 'dry_run', False):
        _info('adopt: dry run — nothing changed')
        return 0

    from sigmond import sources as _sources
    for client in p.components:
        sel = _sources.ClientSources.load(client)
        for key in match.sources:
            sel.add(key)
        sel.save()
    _ok(f'adopt {match.name}: sources recorded; '
        f'run `smd install --components {",".join(p.components)}` '
        f'then `smd start --components {",".join(p.components)}`')
    return 0
```

Register the verb beside the other subparsers (search `add_parser('status'`) and add:

```python
    p_adopt = sub.add_parser('adopt', help='adopt detected hardware or a discovered source')
    p_adopt.add_argument('name', help='offer name, from `smd status`')
    p_adopt.add_argument('--dry-run', action='store_true',
                         help='report the plan and change nothing')
    p_adopt.set_defaults(func=cmd_adopt)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adopt_cli.py -q -p no:cacheprovider`
Expected: PASS, 3 passed

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add bin/smd tests/test_adopt_cli.py
git commit -m "adopt: the only verb that starts anything"
```

---

### Task 7: The VM reads the station manifest

**Files:**
- Modify: `lib/sigmond/station_identity.py`
- Test: `tests/test_station_manifest.py`

**Interfaces:**
- Consumes: `StationIdentity` (Task 2).
- Produces: `read_manifest(path) -> dict`, `identity_from_manifest(manifest, hostname) -> StationIdentity`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_station_manifest.py
"""A replaced VM must come back as the same station.

The PM can install without the VM and can replace it outright, so adoption
decisions made inside the VM are disposable.  PSWS Station and Instrument IDs
are NOT rediscoverable — losing them means a station silently returns with a
different identity, or none.

The PM holds the manifest; the VM reads it.  Writing and mirroring it is the
PM spec's job, not this one.
"""

import pytest

from sigmond.station_identity import (
    StationIdentity, identity_from_manifest, read_manifest,
)


def test_a_manifest_supplies_identity_without_the_roster():
    """A fresh VM must not need the roster to know who it is."""
    manifest = {"dasi2_site": True, "psws_station": "S000207",
                "psws_instrument": "I000207"}
    ident = identity_from_manifest(manifest, hostname="DASI007")
    assert ident == StationIdentity(hostname="DASI007", dasi2_site=True,
                                    psws_station="S000207",
                                    psws_instrument="I000207")


def test_a_non_site_manifest_carries_no_identity():
    ident = identity_from_manifest({"dasi2_site": False}, hostname="fargo-1")
    assert ident.dasi2_site is False
    assert ident.psws_station is None


def test_a_missing_manifest_reads_as_empty_not_an_error():
    """A station with no PM manifest is an ordinary case, not a fault."""
    assert read_manifest("/nonexistent/station.toml") == {}


def test_a_manifest_file_round_trips(tmp_path):
    p = tmp_path / "station.toml"
    p.write_text('dasi2_site = true\npsws_station = "S1"\n'
                 'psws_instrument = "I1"\n')
    assert read_manifest(p)["psws_station"] == "S1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_station_manifest.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'read_manifest'`

- [ ] **Step 3: Append to `lib/sigmond/station_identity.py`**

```python
#: The PM writes this; the VM reads it.  The PM is the machine that survives a
#: VM replacement, so the facts that cannot be rediscovered live there.
DEFAULT_MANIFEST = Path("/etc/sigmond/station.toml")


def read_manifest(path=DEFAULT_MANIFEST) -> Dict[str, object]:
    """The station manifest, or an empty dict when there is none.

    A station without a PM manifest is an ordinary case — a bare install, or a
    host with no PM — not a fault.
    """
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (FileNotFoundError, NotADirectoryError):
        return {}


def identity_from_manifest(manifest: Dict[str, object],
                           hostname: str) -> StationIdentity:
    """Identity as the PM recorded it.

    A fresh VM must not need the roster to know who it is: the PM already
    decided, and re-deciding risks a different answer.
    """
    if not manifest.get("dasi2_site"):
        return StationIdentity(hostname=hostname, dasi2_site=False)
    return StationIdentity(
        hostname=hostname,
        dasi2_site=True,
        psws_station=manifest.get("psws_station"),
        psws_instrument=manifest.get("psws_instrument"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_station_manifest.py -q -p no:cacheprovider`
Expected: PASS, 4 passed

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add lib/sigmond/station_identity.py tests/test_station_manifest.py
git commit -m "identity: a replaced VM reads who it is from the PM"
```

---

### Task 8: The Fargo dry run

**Files:**
- Test: `tests/test_fargo_dry_run.py`

**Interfaces:**
- Consumes: everything above. Adds no API.

- [ ] **Step 1: Write the test**

```python
# tests/test_fargo_dry_run.py
"""Predict what the Fargo Beelink will say, before anyone travels to it.

The sequence Michael described on 2026-09-02:
  1. install PM and VM with NOTHING attached
  2. reboot
  3. attach a uhubctl-capable hub, an RX888, and a Bodnar miniGPS
  4. no magnetometer and no TS-1 for weeks

A checklist says what to do.  This says what the machine will answer.
"""

from sigmond.adoption import StationInventory, offers, plan, recognise
from sigmond.sources import SourceKey
from sigmond.station_identity import StationIdentity

FARGO = StationIdentity(hostname="fargo-1", dasi2_site=False)

RX888 = SourceKey(type="usb", identifier="04b4:00f1:0009061C028B1629")
MINIGPS = SourceKey(type="usb", identifier="1dd2:2211:mini01")


def test_step_1_bare_install_offers_nothing():
    """Nothing attached: the install completes and offers nothing."""
    assert offers(StationInventory(), adopted=frozenset()) == []


def test_step_3_the_attached_kit_is_not_a_dasi2_match():
    """No magnetometer, so this is not the grant kit and must not claim it."""
    inv = StationInventory(hardware=frozenset({"rx888", "gpsdo"}),
                           sources=(RX888, MINIGPS),
                           source_kinds=((RX888, "rx888"),
                                         (MINIGPS, "gpsdo")))
    assert recognise(inv) is None


def test_step_3_offers_the_two_devices_separately():
    inv = StationInventory(hardware=frozenset({"rx888", "gpsdo"}),
                           sources=(RX888, MINIGPS),
                           source_kinds=((RX888, "rx888"),
                                         (MINIGPS, "gpsdo")))
    got = offers(inv, adopted=frozenset())
    assert {o.name for o in got} == {str(RX888), str(MINIGPS)}
    assert all(o.kind == "source" for o in got)


def test_adopting_the_rx888_brings_radiod_and_multicast():
    inv = StationInventory(hardware=frozenset({"rx888", "gpsdo"}),
                           sources=(RX888,),
                           source_kinds=((RX888, "rx888"),))
    p = plan(offers(inv, frozenset())[0], inv, FARGO)
    assert set(p.components) == {"ka9q-radio", "ka9q-web", "igmp-querier"}


def test_fargo_is_asked_for_psws_identity_and_prefilled_the_rest():
    """Not a funded site, so identity is asked; the hostname supplies radiod."""
    inv = StationInventory(hardware=frozenset({"rx888", "gpsdo"}),
                           sources=(RX888,),
                           source_kinds=((RX888, "rx888"),))
    p = plan(offers(inv, frozenset())[0], inv, FARGO)
    assert set(p.ask) == {"psws_station", "psws_instrument"}
    assert p.prefills["radiod_status_name"] == "fargo-1-status.local"


def test_after_adopting_both_nothing_remains_offered():
    inv = StationInventory(hardware=frozenset({"rx888", "gpsdo"}),
                           sources=(RX888, MINIGPS),
                           source_kinds=((RX888, "rx888"),
                                         (MINIGPS, "gpsdo")))
    assert offers(inv, adopted=frozenset({RX888, MINIGPS})) == []
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_fargo_dry_run.py -q -p no:cacheprovider`
Expected: PASS, 6 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_fargo_dry_run.py
git commit -m "tests: predict what the Fargo box will answer, before the trip"
```

---

## Done when

- All eight tasks committed, full sigmond suite green.
- A bare install completes with no hardware attached.
- `smd status` names what is detected and unadopted; `smd adopt` is the only verb that starts anything.
- A DASI-named host absent from the roster is refused, naming both remedies.
- The Fargo dry run passes, so the box's behaviour is predicted rather than discovered.
- ⛔ No RAC registrar selection, ssh provisioning, or manifest push/pull — those are the PM spec.
- ⚠ `config/dasi2-roster.toml` ships PLACEHOLDER PSWS IDs in the correct shape. The tests assert it is WELL-FORMED, not that it is CORRECT — no test can know the real values. Michael's figures must replace them before any fleet build, or twenty stations provision with wrong identities and nothing catches it.
