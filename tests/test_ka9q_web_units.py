"""One ka9q-web per LOCAL radiod.

AC0G-B1 (2026-09-30) runs two radiods, one per RX888.  SIGMOND_RADIOD_STATUS
is unset there by design (CLIENT-CONTRACT §14.2), so the old single-unit
writer installed no ka9q-web at all.  The first local radiod must keep
ka9q-web.service on 8081 (the RAC web proxy port) so single-radiod stations
see no change.
"""
import importlib.util
import os

import pytest

from sigmond import coordination
from sigmond.coordination import Radiod


def _smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    spec = importlib.util.spec_from_loader("smd_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = "bin/smd"
    exec(compile(open("bin/smd").read(), "bin/smd", "exec"), mod.__dict__)
    return mod


class _Coord:
    def __init__(self, radiods):
        self.radiods = {r.id: r for r in radiods}

    def local_radiods(self):
        return [r for r in self.radiods.values() if r.is_local]


@pytest.fixture
def smd():
    return _smd()


def _with(monkeypatch, radiods):
    monkeypatch.setattr(coordination, "load_coordination",
                        lambda *a, **k: _Coord(radiods))


def test_single_local_radiod_is_unchanged(smd, monkeypatch):
    _with(monkeypatch, [Radiod(id="b4", host="localhost", status_dns="AC0G-B4-status.local")])
    assert smd._ka9q_web_targets() == [("ka9q-web.service", "AC0G-B4-status.local", 8081)]


def test_two_local_radiods_get_two_units(smd, monkeypatch):
    _with(monkeypatch, [
        Radiod(id="ac0g-b1-b", host="localhost", status_dns="ac0g-b1-b-status.local"),
        Radiod(id="ac0g-b1-a", host="localhost", status_dns="ac0g-b1-a-status.local"),
    ])
    assert smd._ka9q_web_targets() == [
        ("ka9q-web.service", "ac0g-b1-a-status.local", 8081),
        ("ka9q-web@ac0g-b1-b.service", "ac0g-b1-b-status.local", 8082),
    ]


def test_remote_radiods_get_no_unit(smd, monkeypatch):
    _with(monkeypatch, [
        Radiod(id="here", host="localhost", status_dns="here-status.local"),
        Radiod(id="there", host="192.0.2.9", status_dns="there-status.local"),
    ])
    assert smd._ka9q_web_targets() == [("ka9q-web.service", "here-status.local", 8081)]


def test_template_reads_its_env_file(smd):
    t = smd._ka9q_web_unit_text("", 0, True)
    assert "EnvironmentFile=/etc/sigmond/ka9q-web/%i.env" in t
    assert "-m ${KA9Q_WEB_STATUS} -p ${KA9Q_WEB_PORT}" in t


def test_template_is_cpu_fenced():
    from sigmond.cpu import AFFINITY_UNITS
    assert "ka9q-web@.service" in AFFINITY_UNITS
