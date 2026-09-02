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
