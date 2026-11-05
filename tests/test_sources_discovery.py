"""Radiod discovery, selection and naming (spec 4.4)."""
import pytest

from sigmond import instance, sources
from sigmond.sources import (
    DiscoveredRadiod, SourceSelectionError, advertised_call,
    reporter_id_for, select_radiods,
)


def R(dns, label, host=''):
    return DiscoveredRadiod(status_dns=dns, label=label, host=host or dns)


def test_reporter_id_from_the_status_name():
    assert reporter_id_for('AC0G', 'ac0g-b1-a-status.local') == 'AC0G/B1A'
    assert reporter_id_for('AC0G', 'sigma-status.local') == 'AC0G/SIGMA'


def test_advertised_call_is_the_first_token_upper_cased():
    assert advertised_call('ac0g @EM38ww x') == 'AC0G'
    assert advertised_call('') == ''
    # The token keeps its /suffix: "AC0G/S" is a reporter ID, not a call.
    assert advertised_call('AC0G/S RX-888 Mk2') == 'AC0G/S'


def test_a_radiod_advertising_a_reporter_id_is_not_kept():
    # Michael's LAN, 2026-11-05.  sigma and B4 run their own uploads under
    # AC0G/S and AC0G/B4 (sigmond's default description is
    # "<reporter_id> RX-888 Mk2"); binding them on B2 would duplicate
    # every spot they already send.
    found = [R('ac0g-b1-a-status.local', 'AC0G @EM38ww B1-a longwire'),
             R('ac0g-b1-b-status.local', 'AC0G @EM38ww B1-b T3FD'),
             R('sigma-status.local', 'AC0G/S RX-888 Mk2'),
             R('b4-status.local', 'AC0G/B4 RX-888 Mk2')]
    assert [r.status_dns for r in select_radiods(found, 'AC0G', [])] == \
        ['ac0g-b1-a-status.local', 'ac0g-b1-b-status.local']


def test_a_reporter_id_radiod_is_bound_when_named():
    found = [R('sigma-status.local', 'AC0G/S RX-888 Mk2')]
    got = select_radiods(found, 'AC0G', ['sigma-status.local'])
    assert [r.status_dns for r in got] == ['sigma-status.local']


def test_own_callsign_radiods_are_kept_others_listed_only():
    found = [R('ac0g-b1-a-status.local', 'AC0G @EM38ww B1-a longwire'),
             R('ac0g-b1-b-status.local', 'ac0g @EM38ww B1-b T3FD'),
             R('k3lr-status.local', 'K3LR @EN91 main'),
             R('x-status.local', 'with airspyhf')]
    assert [r.status_dns for r in select_radiods(found, 'AC0G', [])] == \
        ['ac0g-b1-a-status.local', 'ac0g-b1-b-status.local']


def test_a_named_radiod_overrides_the_callsign_rule():
    found = [R('ac0g-b1-a-status.local', 'AC0G @EM38ww B1-a'),
             R('k3lr-status.local', 'K3LR @EN91 main')]
    got = select_radiods(found, 'AC0G', ['k3lr-status.local'])
    assert [r.status_dns for r in got] == ['k3lr-status.local']


def test_a_named_radiod_that_did_not_answer_is_an_error():
    with pytest.raises(SourceSelectionError, match='gone-status.local'):
        select_radiods([R('a-status.local', 'AC0G x')], 'AC0G',
                       ['gone-status.local'])


def test_colliding_reporter_ids_are_an_error():
    found = [R('ac0g-b1-a-status.local', 'AC0G one'),
             R('ac0g-b1a-status.local', 'AC0G two')]
    with pytest.raises(SourceSelectionError) as e:
        select_radiods(found, 'AC0G', [])
    assert 'ac0g-b1-a-status.local' in str(e.value)
    assert 'ac0g-b1a-status.local' in str(e.value)


def test_a_new_instances_source_file_uses_the_key_the_reader_reads(
        tmp_path, monkeypatch):
    real = instance.instance_paths

    def fake(client, rid):
        p = real(client, rid)
        return instance.InstancePaths(**{
            **p.__dict__,
            'config': tmp_path / 'etc' / client / f'{rid}.toml',
            'env': tmp_path / 'etc' / client / 'env' / f'{rid}.env',
            'sources': tmp_path / 'clients' / f'{client}@{rid}.sources.toml',
        })
    monkeypatch.setattr(instance, 'instance_paths', fake)
    monkeypatch.setattr(instance, '_LEGACY_SHARED_CONFIG', {})
    instance.create_instance('psk-recorder', 'AC0G-B1A')
    text = (tmp_path / 'clients'
            / 'psk-recorder@AC0G-B1A.sources.toml').read_text()
    assert 'selected = []' in text and 'selections' not in text
    got = sources.ClientSources.load('psk-recorder@AC0G-B1A',
                                     root=tmp_path / 'clients')
    assert got.selected == []


@pytest.mark.parametrize('dns', ['-status.local', '---status.local'])
def test_an_empty_remainder_is_an_error(dns):
    # A status name with nothing alphanumeric left once the suffix is
    # stripped would give "AC0G/", a malformed reporter ID.
    with pytest.raises(SourceSelectionError, match=dns):
        reporter_id_for('AC0G', dns)
    with pytest.raises(SourceSelectionError, match=dns):
        select_radiods([R(dns, 'AC0G odd')], 'AC0G', [])
    with pytest.raises(SourceSelectionError, match=dns):
        select_radiods([R(dns, 'x')], 'AC0G', [dns])
