"""Tests for the pre-flight requirements check."""

from __future__ import annotations

import io
from unittest import mock

import pytest

from sigmond import preflight
from sigmond.environment import Observation


# ---------------------------------------------------------------------------
# Fake catalog entry — CatalogEntry is frozen, so instance-level
# monkeypatching of is_installed() isn't possible.  This stand-in
# duck-types the attributes preflight + catalog helpers actually read.
# ---------------------------------------------------------------------------

class _FakeEntry:
    def __init__(self, name, *, requires=(), kind="client",
                 install_script="", installed=False):
        self.name = name
        self.kind = kind
        self.requires = tuple(requires)
        self.install_script = install_script
        self.topology_alias = ""
        self._installed = installed

    def is_installed(self) -> bool:
        return self._installed


def _entry(name, **kwargs):
    return _FakeEntry(name, **kwargs)


@pytest.fixture
def catalog_with_unmet_ka9q():
    """wspr-recorder requires ka9q-radio; ka9q-radio is NOT installed."""
    return {
        "wspr-recorder": _entry("wspr-recorder",
                                 requires=["ka9q-python", "ka9q-radio"],
                                 installed=False),
        "ka9q-python":   _entry("ka9q-python", kind="library", installed=True),
        "ka9q-radio":    _entry("ka9q-radio", kind="server", installed=False),
    }


@pytest.fixture
def catalog_all_met():
    """All deps installed — pre-flight should pass without prompting."""
    return {
        "wspr-recorder": _entry("wspr-recorder",
                                 requires=["ka9q-python", "ka9q-radio"]),
        "ka9q-python":   _entry("ka9q-python", kind="library", installed=True),
        "ka9q-radio":    _entry("ka9q-radio", kind="server", installed=True),
    }


# ---------------------------------------------------------------------------
# _unmet_requires
# ---------------------------------------------------------------------------

class TestUnmetRequires:
    def test_returns_empty_when_all_satisfied(self, catalog_all_met):
        assert preflight._unmet_requires("wspr-recorder", catalog_all_met) == []

    def test_returns_missing_dep(self, catalog_with_unmet_ka9q):
        missing = preflight._unmet_requires("wspr-recorder",
                                             catalog_with_unmet_ka9q)
        names = [name for name, _ in missing]
        assert names == ["ka9q-radio"]

    def test_unknown_client_returns_empty(self, catalog_all_met):
        assert preflight._unmet_requires("nonesuch", catalog_all_met) == []


# ---------------------------------------------------------------------------
# check_requires — happy path
# ---------------------------------------------------------------------------

class TestCheckRequiresHappyPath:
    def test_returns_true_when_nothing_missing(self, catalog_all_met):
        assert preflight.check_requires("wspr-recorder",
                                         catalog_all_met,
                                         yes=False) is True

    def test_unknown_client_returns_true(self, catalog_all_met):
        # Unknown clients should fall through so the install path can
        # surface its own clearer error.
        assert preflight.check_requires("nonesuch",
                                         catalog_all_met,
                                         yes=True) is True


# ---------------------------------------------------------------------------
# check_requires — unmet deps
# ---------------------------------------------------------------------------

class TestCheckRequiresUnmet:
    def test_yes_bypass_returns_true(self, catalog_with_unmet_ka9q):
        with mock.patch.object(preflight.mdns, "probe", return_value=[]), \
             mock.patch.object(preflight.usb_sdr, "probe", return_value=[]):
            assert preflight.check_requires("wspr-recorder",
                                             catalog_with_unmet_ka9q,
                                             yes=True) is True

    def test_non_tty_no_yes_aborts(self, catalog_with_unmet_ka9q,
                                    monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        # io.StringIO has isatty()==False by default.
        with mock.patch.object(preflight.mdns, "probe", return_value=[]), \
             mock.patch.object(preflight.usb_sdr, "probe", return_value=[]):
            assert preflight.check_requires("wspr-recorder",
                                             catalog_with_unmet_ka9q,
                                             yes=False) is False

    def test_tty_user_confirms(self, catalog_with_unmet_ka9q, monkeypatch):
        fake_stdin = io.StringIO("y\n")
        fake_stdin.isatty = lambda: True
        monkeypatch.setattr("sys.stdin", fake_stdin)
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        with mock.patch.object(preflight.mdns, "probe", return_value=[]), \
             mock.patch.object(preflight.usb_sdr, "probe", return_value=[]):
            assert preflight.check_requires("wspr-recorder",
                                             catalog_with_unmet_ka9q,
                                             yes=False) is True

    def test_tty_user_declines(self, catalog_with_unmet_ka9q, monkeypatch):
        fake_stdin = io.StringIO("n\n")
        fake_stdin.isatty = lambda: True
        monkeypatch.setattr("sys.stdin", fake_stdin)
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        with mock.patch.object(preflight.mdns, "probe", return_value=[]), \
             mock.patch.object(preflight.usb_sdr, "probe", return_value=[]):
            assert preflight.check_requires("wspr-recorder",
                                             catalog_with_unmet_ka9q,
                                             yes=False) is False


# ---------------------------------------------------------------------------
# Context probes (mDNS / USB) are only run when ka9q-radio is the unmet dep
# ---------------------------------------------------------------------------

class TestContextProbes:
    def test_ka9q_unmet_runs_probes(self, catalog_with_unmet_ka9q):
        with mock.patch.object(preflight.mdns, "probe",
                                return_value=[]) as m_mdns, \
             mock.patch.object(preflight.usb_sdr, "probe",
                                return_value=[]) as m_usb:
            preflight.check_requires("wspr-recorder",
                                      catalog_with_unmet_ka9q,
                                      yes=True)
            assert m_mdns.called
            assert m_usb.called

    def test_non_ka9q_unmet_skips_probes(self):
        # Build a catalog where the missing dep is NOT ka9q-radio.
        catalog = {
            "foo": _entry("foo", requires=["bar"], installed=False),
            "bar": _entry("bar", kind="server", installed=False),
        }
        with mock.patch.object(preflight.mdns, "probe",
                                return_value=[]) as m_mdns, \
             mock.patch.object(preflight.usb_sdr, "probe",
                                return_value=[]) as m_usb:
            preflight.check_requires("foo", catalog, yes=True)
            assert not m_mdns.called
            assert not m_usb.called

    def test_probe_exceptions_dont_abort(self, catalog_with_unmet_ka9q):
        # If avahi-browse blows up, the pre-flight should still complete.
        with mock.patch.object(preflight.mdns, "probe",
                                side_effect=RuntimeError("avahi crashed")), \
             mock.patch.object(preflight.usb_sdr, "probe",
                                side_effect=RuntimeError("lsusb crashed")):
            assert preflight.check_requires("wspr-recorder",
                                             catalog_with_unmet_ka9q,
                                             yes=True) is True


# ---------------------------------------------------------------------------
# _explain_radiod_gap message branches (smoke test — output goes to stderr)
# ---------------------------------------------------------------------------

class TestExplainRadiod:
    def test_remote_radiod_path(self, capsys):
        obs = [Observation(source="mdns", kind="radiod", id=None,
                           endpoint="bee5.local:5006",
                           fields={"name": "bee5"})]
        preflight._explain_radiod_gap(obs, [])
        captured = capsys.readouterr().err
        assert "bee5" in captured
        assert "no local radiod install needed" in captured

    def test_local_sdr_no_remote_path(self, capsys):
        obs = [Observation(source="usb_sdr", kind="sdr", id=None,
                           endpoint="bus 3 dev 8",
                           fields={"sdr_type": "RX-888 DFU",
                                   "bus": "003", "device": "008"})]
        preflight._explain_radiod_gap([], obs)
        captured = capsys.readouterr().err
        assert "RX-888" in captured
        assert "smd install ka9q-radio" in captured

    def test_nothing_path(self, capsys):
        preflight._explain_radiod_gap([], [])
        captured = capsys.readouterr().err
        assert "no SDR" in captured.lower() or "no local sdr" in captured.lower()
