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
    ManifestHostnameMismatch, StationIdentity, UnreadableManifest,
    UnrosteredDasiName, identity_from_manifest, read_manifest,
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


# ---------------------------------------------------------------------------
# The reader is wired: `smd`'s own `_station_identity()` consults the manifest
# ---------------------------------------------------------------------------

import importlib.util
import os
import socket

import sigmond.station_identity as _si


def _smd():
    # bin/smd re-execs into <repo>/venv/bin/python at module scope; left
    # unset, importing it here would replace the running pytest process.
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    spec = importlib.util.spec_from_loader("smd_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = "bin/smd"
    exec(compile(open("bin/smd").read(), "bin/smd", "exec"), mod.__dict__)
    return mod


def _at(monkeypatch, tmp_path, hostname, text=None):
    """Point `_station_identity()` at a fabricated host and manifest."""
    path = tmp_path / "station.toml"
    if text is not None:
        path.write_text(text)
    monkeypatch.setattr(_si, "DEFAULT_MANIFEST", path)
    monkeypatch.setattr(socket, "gethostname", lambda: hostname)
    return path


def test_a_replaced_vm_keeps_the_psws_identity_the_pm_recorded(
        monkeypatch, tmp_path):
    """The defect this closes: `read_manifest` had no caller, so a VM whose
    PM had written station.toml re-derived everything from the roster and, on
    a hostname the roster does not name, came back with no PSWS identity at
    all.  The IDs are the one fact a replaced VM cannot rediscover."""
    _at(monkeypatch, tmp_path, "fargo-1",
        'psws_station = "S000171"\npsws_instrument = "I000171"\n')
    ident = _smd()._station_identity()
    assert ident.hostname == "fargo-1"
    assert ident.dasi2_site is False        # membership still from the name
    assert ident.psws_station == "S000171"
    assert ident.psws_instrument == "I000171"


def test_no_manifest_leaves_the_hostname_rule_exactly_as_it_was(
        monkeypatch, tmp_path):
    """A station without a PM manifest is the ordinary case."""
    _at(monkeypatch, tmp_path, "fargo-1")           # no file written
    assert _smd()._station_identity() == _si.identify("fargo-1")


def test_the_manifest_cannot_promote_an_ordinary_station_to_a_site(
        monkeypatch, tmp_path):
    """`identity_from_manifest` refuses this; the refusal has to be REACHED.
    Delivered-but-unreachable is how the check would rot."""
    _at(monkeypatch, tmp_path, "fargo-1",
        'dasi2_site = true\npsws_station = "S1"\npsws_instrument = "I1"\n')
    with pytest.raises(ManifestHostnameMismatch):
        _smd()._station_identity()


def test_the_hostname_outranks_a_manifest_that_demotes_it(
        monkeypatch, tmp_path, capsys):
    """A rostered DASI name whose manifest says `dasi2_site = false` is a
    stale manifest, not a demotion: membership comes from the hostname.  The
    contradiction is said out loud rather than resolved in silence."""
    _at(monkeypatch, tmp_path, "DASI001",
        'dasi2_site = false\npsws_station = "S000999"\n')
    ident = _smd()._station_identity()
    assert ident.dasi2_site is True                 # the hostname decides
    assert ident.psws_station == "S000999"          # the PM's ID still wins
    assert "stale" in capsys.readouterr().out.lower()


def test_a_manifest_does_not_rescue_an_unrostered_dasi_name(
        monkeypatch, tmp_path):
    """Refused rather than guessed, manifest or no manifest.  Letting the PM's
    file add a machine to the fleet would make the roster advisory, which is
    new policy — not the wiring this change is."""
    _at(monkeypatch, tmp_path, "DASI099",
        'dasi2_site = true\npsws_station = "S1"\npsws_instrument = "I1"\n')
    with pytest.raises(UnrosteredDasiName):
        _smd()._station_identity()


# ---------------------------------------------------------------------------
# "Cannot read it" is not the same fact as "it is not there"
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.geteuid() == 0, reason="root reads an unreadable file")
def test_an_unreadable_manifest_is_loud_rather_than_absent(tmp_path):
    """`smd adopt` reads identity BEFORE it elevates, so a root-only
    station.toml is read by an unprivileged process.  Treating that as
    "absent" would silently fall back to the roster — exactly the silent
    identity loss the manifest exists to prevent."""
    p = tmp_path / "station.toml"
    p.write_text('psws_station = "S1"\n')
    p.chmod(0o000)
    try:
        with pytest.raises(UnreadableManifest):
            read_manifest(p)
    finally:
        p.chmod(0o600)


def test_a_directory_where_the_manifest_should_be_is_loud(tmp_path):
    """A broken install, not an absence."""
    d = tmp_path / "station.toml"
    d.mkdir()
    with pytest.raises(UnreadableManifest):
        read_manifest(d)
