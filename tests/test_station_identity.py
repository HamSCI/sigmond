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

# Shaped like the real roster: one station id, one instrument PER RECORDER.
# A DASI2 site reports GRAPE/HF and magnetometer instruments under one station.
ROSTER = {
    "DASI001": {"psws_station": "S000201",
                "psws_instruments": {"hf-timestd": "201",
                                     "mag-recorder": "202"}},
    "DASI007": {"psws_station": "S000207",
                "psws_instruments": {"hf-timestd": "370",
                                     "mag-recorder": "371"}},
}


def test_a_rostered_dasi_host_is_a_site():
    ident = identify("DASI007", ROSTER)
    assert ident.dasi2_site is True
    assert ident.psws_station == "S000207"
    assert ident.instrument_for("hf-timestd") == "370"
    assert ident.instrument_for("mag-recorder") == "371"


def test_an_ordinary_hostname_is_not_a_site():
    ident = identify("fargo-1", ROSTER)
    assert ident.dasi2_site is False
    assert ident.psws_station is None
    assert ident.psws_instruments == ()
    assert ident.instrument_for("hf-timestd") == ""


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
        # Every DASI2 kit carries both an RX888 and an RM3100, so a rostered
        # site missing either instrument id is an incomplete entry, not a
        # station that happens to lack the hardware.
        instruments = entry.get("psws_instruments") or {}
        assert instruments.get("hf-timestd"), f"{name} has no HF instrument"
        assert instruments.get("mag-recorder"), f"{name} has no mag instrument"


# ---------------------------------------------------------------------------
# Placeholder IDs must be checkable by something other than a human's eye
# ---------------------------------------------------------------------------

def test_the_shipped_roster_is_no_longer_a_placeholder(tmp_path):
    """Michael's real ids landed 2026-09-02, so the sentinel came out with
    them.

    This test used to assert the OPPOSITE -- that `[_meta] placeholder = true`
    was present -- because the shipped ids sat in the same S0002NN namespace as
    real ones and only a sentinel could tell a program which it was holding.
    It is inverted rather than deleted so the pair stays enforced from both
    directions: placeholders cannot ship without a sentinel, and a sentinel
    cannot outlive the placeholders it described.  A stale sentinel would warn
    on every load forever, and a warning that is always wrong is one operators
    learn to ignore.
    """
    import tomllib
    from sigmond.station_identity import DEFAULT_ROSTER
    with open(DEFAULT_ROSTER, "rb") as fh:
        raw = tomllib.load(fh)
    assert "_meta" not in raw


def test_meta_is_not_a_station(tmp_path):
    """`_meta` describes the FILE.  A roster reader that returned it as a
    station would offer a machine named _META with no PSWS IDs."""
    p = tmp_path / "roster.toml"
    p.write_text('[_meta]\nplaceholder = true\n\n'
                 '[DASI001]\npsws_station = "S1"\npsws_instrument = "I1"\n')
    roster = load_roster(p)
    assert set(roster) == {"DASI001"}


def test_a_placeholder_roster_says_so_out_loud(tmp_path, capsys):
    """The moment anyone wires the prefills through, an unguarded placeholder
    reaches real config.  Loading one has to be audible."""
    p = tmp_path / "roster.toml"
    p.write_text('[_meta]\nplaceholder = true\n\n'
                 '[DASI001]\npsws_station = "S1"\npsws_instrument = "I1"\n')
    load_roster(p)
    err = capsys.readouterr().err.lower()
    assert "placeholder" in err
    assert str(p) in err or "roster" in err


def test_a_real_roster_is_quiet(tmp_path, capsys):
    p = tmp_path / "roster.toml"
    p.write_text('[DASI001]\npsws_station = "S000171"\n'
                 'psws_instrument = "I000171"\n')
    load_roster(p)
    assert capsys.readouterr().err == ""
