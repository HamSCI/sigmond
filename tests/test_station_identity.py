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


def test_a_padded_hostname_is_stripped_before_it_lands_on_identity():
    """identify() already strips to build its roster lookup key; storing the
    same stripped value keeps the field consistent with the key it was
    matched by, rather than carrying padding forward on a technicality."""
    ident = identify(" DASI007 ", ROSTER)
    assert ident.hostname == "DASI007"


def test_the_shipped_roster_parses_and_is_well_formed():
    roster = load_roster()
    assert roster, "the shipped roster is empty"
    for name, entry in roster.items():
        assert name.upper() == name, f"{name} should be upper-case"
        assert entry["psws_station"], f"{name} has no psws_station"
        assert entry["psws_instrument"], f"{name} has no psws_instrument"
