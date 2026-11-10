"""topology.toml states what runs here; drift is where systemd disagrees
(Michael, 2026-11-10: hfdl-recorder ran outside topology at five stations)."""
from types import SimpleNamespace as NS

from sigmond.topology_drift import Drift, topology_drift


def _cat(**entries):
    return {n: NS(name=n, topology_alias=None, services=e.get('services', ()))
            for n, e in entries.items()}


def _state(table):
    def state(name, topology, alias):
        active, enabled, installed = table[name]
        return NS(active=active, enabled=enabled, installed=installed)
    return state


def _drift(table, catalog=None, units=lambda n, a: True):
    catalog = catalog or _cat(**{n: {} for n in table})
    return topology_drift(None, catalog, state=_state(table), has_units=units)


def test_running_not_enabled():
    d = _drift({'hfdl-recorder': (True, False, True)})
    assert d.running_not_enabled == ['hfdl-recorder']
    assert d.enabled_not_running == []
    assert d


def test_enabled_installed_stopped():
    d = _drift({'psk-recorder': (False, True, True)})
    assert d.enabled_not_running == ['psk-recorder']


def test_agreement_is_clean():
    d = _drift({'a': (True, True, True), 'b': (False, False, True),
                'c': (False, False, False)})
    assert d == Drift() and not d


def test_enabled_not_installed_is_next_steps_business():
    assert not _drift({'x': (False, True, False)})


def test_component_without_units_never_counts_as_stopped():
    assert not _drift({'callhash': (False, True, True)}, units=lambda n, a: False)


def test_declared_services_entry_runs_outside_topology_by_design():
    cat = _cat(**{'hs-uploader': {'services': ('hs-uploader.service',)}})
    assert not _drift({'hs-uploader': (True, False, True)}, catalog=cat)
