"""The [[receiver]] list: read, check, write."""

import pytest

from sigmond.receivers import (
    Receiver, ReceiverError, effective_clients, load_receivers,
    upsert_receiver, validate_receivers, write_receivers)
from sigmond.site_profile import carry_uploads_block

B1A = 'ac0g-b1-a-status.local'
B1B = 'ac0g-b1-b-status.local'
PC = frozenset({'wspr-recorder', 'psk-recorder', 'meteor-scatter',
                'hf-timestd'})


def _v(rs):
    return validate_receivers(rs, profile_clients=PC)


def _f(tmp_path, body):
    p = tmp_path / 'site-profile.toml'
    p.write_text(body)
    return p


def test_no_file_or_no_array_gives_an_empty_list(tmp_path):
    assert load_receivers(tmp_path / 'none.toml') == []
    assert load_receivers(_f(tmp_path, '[station]\ncallsign = "X"\n')) == []


def test_a_receiver_round_trips(tmp_path):
    station = '[station]\ncallsign = "AC0G"  # keep\n'
    p = _f(tmp_path, station)
    rs = [Receiver('AC0G/B1A', radiod=B1A, clients=('wspr-recorder',),
                   injector=True, antenna='the "big" loop # north'),
          Receiver('AC0G/B1B', radiod=B1A, same_antenna_as='AC0G/B1A',
                   upload=True)]
    write_receivers(p, rs)
    assert load_receivers(p) == rs
    assert p.read_text().startswith(station)
    write_receivers(p, rs)                       # rewrite is stable
    assert load_receivers(p) == rs
    assert p.read_text().count('[[receiver]]') == 2
    write_receivers(p, [])
    assert p.read_text() == station


def test_write_creates_the_file(tmp_path):
    p = tmp_path / 'new.toml'
    write_receivers(p, [Receiver('AC0G/B1A', radiod=B1A)])
    assert load_receivers(p)[0].radiod == B1A


def test_reporter_spellings_store_the_display_form(tmp_path):
    p = _f(tmp_path, '[[receiver]]\nreporter_id = "ac0g=b1a"\nradiod = "x.local"\n'
                     '[[receiver]]\nreporter_id = "KFS-SE"\nradiod = "y.local"\n')
    assert [r.reporter_id for r in load_receivers(p)] == ['AC0G/B1A', 'KFS-SE']


def test_one_reporter_two_spellings_is_a_duplicate():
    msgs = _v([Receiver('AC0G/B1A', radiod=B1A),
               Receiver('ac0g=b1a', radiod=B1B)])
    assert any('AC0G/B1A' in m for m in msgs)


def test_shared_radiod_needs_same_antenna_as():
    a = Receiver('AC0G/B1A', radiod=B1A)
    msgs = _v([a, Receiver('AC0G/B1B', radiod=B1A)])
    assert any('AC0G/B1A' in m and 'AC0G/B1B' in m for m in msgs)
    assert _v([a, Receiver('AC0G/B1B', radiod=B1A,
                           same_antenna_as='AC0G/B1A')]) == []
    other = Receiver('AC0G/B1C', radiod=B1B)
    msgs = _v([a, other, Receiver('AC0G/B1B', radiod=B1A,
                                  same_antenna_as='AC0G/B1C')])
    assert msgs


def test_injector_on_two_receivers_is_refused():
    msgs = _v([Receiver('AA', radiod=B1A, injector=True),
               Receiver('BB', radiod=B1B, injector=True)])
    assert any('injector' in m for m in msgs)
    msgs = _v([Receiver('AA', radiod=B1A, injector=True),
               Receiver('BB', radiod=B1A, injector=True, same_antenna_as='AA')])
    assert any('injector' in m for m in msgs)


def test_kiwisdr_is_refused_until_phase_4c(tmp_path):
    p = _f(tmp_path, '[[receiver]]\nreporter_id = "AA"\nkiwisdr = "k.local"\n')
    msgs = _v(load_receivers(p))
    assert any('KiwiSDR receivers arrive with Phase 4c' in m for m in msgs)


def test_radiod_and_kiwisdr_together_is_refused():
    assert _v([Receiver('AA', radiod=B1A, kiwisdr='k.local')])


def test_an_unknown_key_names_itself(tmp_path):
    p = _f(tmp_path, '[[receiver]]\nreporter_id="AA"\nradiod="x.local"\n'
                     'colour="red"\n')
    with pytest.raises(ReceiverError, match='colour'):
        load_receivers(p)


def test_a_wrong_type_is_an_error(tmp_path):
    p = _f(tmp_path, '[[receiver]]\nreporter_id="AA"\ninjector="yes"\n')
    with pytest.raises(ReceiverError):
        load_receivers(p)


def test_a_bad_status_name_is_refused(tmp_path):
    p = _f(tmp_path, '[[receiver]]\nreporter_id="AA"\nradiod="evil\\nX=1"\n')
    assert _v(load_receivers(p))


def test_uploads_default_and_override():
    assert Receiver('AA', radiod=B1A).uploads is True
    assert Receiver('AA', same_antenna_as='BB').uploads is False
    assert Receiver('AA', same_antenna_as='BB', upload=True).uploads is True
    assert Receiver('AA', upload=False).uploads is False


def test_clients_default_to_the_profiles_reporter_keyed_clients():
    r = Receiver('AA', radiod=B1A)
    assert effective_clients(r, PC) == ('meteor-scatter', 'psk-recorder',
                                        'wspr-recorder')
    assert effective_clients(Receiver('AA', clients=('psk-recorder',)), PC) \
        == ('psk-recorder',)


def test_a_non_reporter_client_is_refused():
    msgs = _v([Receiver('AA', radiod=B1A, clients=('hf-timestd',))])
    assert any('hf-timestd is not a per-reporter client' in m for m in msgs)


def test_receivers_survive_carry_uploads_block(tmp_path):
    old = '[station]\ncallsign = "X"\n\n[uploads]\nmode = "x"\n'
    new = ('[station]\ncallsign = "X"\n\n[uploads]\nmode = "y"\n\n'
           '[[receiver]]\nreporter_id = "AA"\nradiod = "a.local"\n\n'
           '[[receiver]]\nreporter_id = "BB"\nradiod = "b.local"\n')
    p = _f(tmp_path, '')
    p.write_text(carry_uploads_block(new, old))
    assert [r.reporter_id for r in load_receivers(p)] == ['AA', 'BB']


def test_upsert_replaces_or_appends(tmp_path):
    p = tmp_path / 'p.toml'
    upsert_receiver(p, Receiver('AC0G/B1A', radiod=B1A))
    rs = upsert_receiver(p, Receiver('ac0g=b1a', radiod=B1B))
    assert [(r.reporter_id, r.radiod) for r in rs] == [('AC0G/B1A', B1B)]
    rs = upsert_receiver(p, Receiver('AC0G/B1B', radiod=B1A))
    assert len(rs) == 2 and load_receivers(p) == rs


def test_upsert_keeps_what_the_caller_did_not_set(tmp_path):
    p = tmp_path / 'p.toml'
    write_receivers(p, [Receiver('AC0G/B1A', radiod=B1A, antenna='loop',
                                 same_antenna_as='AC0G/B1B')])
    new = Receiver('AC0G/B1A', radiod=B1B, clients=('psk-recorder',),
                   injector=True)
    r, = upsert_receiver(p, new)
    assert (r.antenna, r.same_antenna_as) == ('loop', 'AC0G/B1B')
    assert (r.radiod, r.clients, r.injector) == (B1B, ('psk-recorder',), True)
    r, = upsert_receiver(p, new, keep_unset=False)
    assert (r.antenna, r.same_antenna_as) == ('', '')


def test_a_spaced_or_commented_header_is_replaced_once(tmp_path):
    p = _f(tmp_path, '[station]\ncallsign = "X"\n\n'
                     '[[ receiver ]]\nreporter_id = "AC0G/B1A"\n'
                     'radiod = "ac0g-b1-a-status.local"\n\n'
                     '[[receiver]]  # note\nreporter_id = "AC0G/B1B"\n'
                     'radiod = "ac0g-b1-b-status.local"\n')
    rs = load_receivers(p)
    assert len(rs) == 2
    write_receivers(p, rs[:1])
    text = p.read_text()
    assert text.count('reporter_id') == 1
    assert 'AC0G/B1B' not in text
    assert len(load_receivers(p)) == 1


def test_similar_headers_are_not_receiver_blocks():
    from sigmond.receivers import _without_receivers
    keep = '[[receiver.x]]\na = 1\n[[receiverX]]\nb = 2\n'
    assert _without_receivers(keep) == keep


def test_a_non_ascii_antenna_round_trips_as_utf8(tmp_path):
    p = tmp_path / 'sp.toml'
    r = Receiver(reporter_id='AC0G/B1A', radiod=B1A,
                 antenna='15 dBi at 135°')
    write_receivers(p, [r])
    assert '135°'.encode('utf-8') in p.read_bytes()
    assert load_receivers(p)[0].antenna == r.antenna


def test_a_syntax_error_is_a_receiver_error(tmp_path):
    with pytest.raises(ReceiverError):
        load_receivers(_f(tmp_path, '[[receiver]\nx = '))


def test_a_receiver_that_is_not_a_table_is_a_receiver_error(tmp_path):
    with pytest.raises(ReceiverError):
        load_receivers(_f(tmp_path, 'receiver = [1]\n'))


def test_an_indented_receiver_header_is_replaced_not_copied(tmp_path):
    from sigmond.receivers import Receiver, load_receivers, write_receivers
    p = tmp_path / 'site-profile.toml'
    p.write_text('[station]\ncall = "AC0G"\n\n'
                 '  [[receiver]]\n  reporter_id = "AC0G/B1A"\n'
                 '  radiod = "a-status.local"\n')
    write_receivers(p, load_receivers(p))
    assert p.read_text().count('[[receiver]]') == 1
    assert len(load_receivers(p)) == 1
