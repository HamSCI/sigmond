"""A replaced VM must come back as the same station.

The PM can install without the VM and can replace it outright, so adoption
decisions made inside the VM are disposable.  PSWS Station and Instrument IDs
are NOT rediscoverable — losing them means a station silently returns with a
different identity, or none.

The PM holds the manifest; the VM reads it.  Writing and mirroring it is the
PM spec's job, not this one.
"""

import tomllib

import pytest

from sigmond.station_identity import (
    ManifestHostnameMismatch, StationIdentity, identity_from_manifest,
    read_manifest,
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
    assert ident.psws_instrument is None


def test_a_manifest_cannot_promote_an_unmatched_hostname_to_a_site():
    """Membership comes from the hostname, never from an operator answer —
    and a manifest is close kin to an operator answer.  A manifest claiming
    dasi2_site for a name that isn't even DASI-shaped must be refused, not
    honored."""
    with pytest.raises(ManifestHostnameMismatch) as exc:
        identity_from_manifest(
            {"dasi2_site": True, "psws_station": "S1",
             "psws_instrument": "I1"},
            hostname="fargo-1",
        )
    msg = str(exc.value).lower()
    assert "fargo-1" in msg
    assert "hostname" in msg
    assert "manifest" in msg


def test_a_padded_hostname_is_stripped_in_manifest_identity():
    manifest = {"dasi2_site": True, "psws_station": "S000207",
                "psws_instrument": "I000207"}
    ident = identity_from_manifest(manifest, hostname=" DASI007 ")
    assert ident.hostname == "DASI007"


def test_a_missing_manifest_reads_as_empty_not_an_error():
    """A station with no PM manifest is an ordinary case, not a fault."""
    assert read_manifest("/nonexistent/station.toml") == {}


def test_a_malformed_manifest_raises_rather_than_reading_as_empty(tmp_path):
    """Corrupt is not the same as absent.  Folding this into the missing-file
    case would let a station come back with no identity and nobody notice —
    the pair of tests is the point: the distinction is the requirement."""
    p = tmp_path / "station.toml"
    p.write_text("this is [ not valid toml")
    with pytest.raises(tomllib.TOMLDecodeError):
        read_manifest(p)


def test_a_manifest_file_round_trips(tmp_path):
    p = tmp_path / "station.toml"
    p.write_text('dasi2_site = true\npsws_station = "S1"\n'
                 'psws_instrument = "I1"\n')
    assert read_manifest(p)["psws_station"] == "S1"
