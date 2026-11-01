"""smd check on a radiod-only host: one line per radiod, no upload lines."""
from sigmond import radiod_publish as rp
from sigmond import station_role
from sigmond.commands import check
from tests.check_fakes import make_env


def _env(diag, serials=None, present=None, journal=None):
    serials = {'bee': 'SN1'} if serials is None else serials
    present = {'SN1'} if present is None else present
    return make_env(
        radiod_diagnose=lambda: diag,
        radiod_serials=lambda: serials,
        radiod_status=lambda: {'bee': 'se-status.local'},
        present_serials=lambda: present,
        journal=journal or (lambda units, minutes: []))


def _only(monkeypatch, tmp_path, role=station_role.RADIOD_ONLY):
    p = tmp_path / 'role'
    p.write_text(role + '\n')
    monkeypatch.setattr(station_role, 'ROLE_PATH', p)


def _lines(env):
    return check.run_checks(env)


def test_healthy(monkeypatch, tmp_path):
    _only(monkeypatch, tmp_path)
    env = _env({'bee': ''})
    (ln,) = _lines(env)
    assert (ln.mark, ln.label) == (check.OK, 'radiod bee')
    assert ln.text == 'card SN1 present, unit active, streaming, se-status.local published'


def test_card_absent_fails_naming_serial(monkeypatch, tmp_path):
    _only(monkeypatch, tmp_path)
    (ln,) = _lines(_env({'bee': ''}, present=set()))
    assert ln.mark == check.FAIL and 'SN1' in ln.text


def test_unit_down_fails(monkeypatch, tmp_path):
    _only(monkeypatch, tmp_path)
    (ln,) = _lines(_env({'bee': rp.UNIT_DOWN}))
    assert ln.mark == check.FAIL and 'unit' in ln.text


def test_unresolved_warns_with_hint(monkeypatch, tmp_path):
    _only(monkeypatch, tmp_path)
    (ln,) = _lines(_env({'bee': rp.UNRESOLVED}))
    assert ln.mark == check.WAIT and 'avahi' in ln.text and 'nss-mdns' in ln.text


def test_sample_loss_warns(monkeypatch, tmp_path):
    _only(monkeypatch, tmp_path)
    (ln,) = _lines(_env({'bee': ''}, journal=lambda u, m: ['x', 'No rx888 data']))
    assert ln.mark == check.WAIT and 'streaming' in ln.text


def test_no_upload_or_spots_lines(monkeypatch, tmp_path):
    _only(monkeypatch, tmp_path)
    for diag in ({'bee': ''}, {'bee': rp.UNIT_DOWN}, {'bee': rp.UNRESOLVED}):
        for ln in _lines(_env(diag)):
            assert 'spots' not in (ln.label + ln.text).lower()
            assert 'upload' not in (ln.label + ln.text).lower()


def test_full_role_keeps_checks(monkeypatch, tmp_path):
    _only(monkeypatch, tmp_path, role='full')
    labels = {ln.label for ln in _lines(_env({'bee': ''}))}
    assert not any(l.startswith('radiod ') for l in labels)


def test_explicit_checks_win(monkeypatch, tmp_path):
    _only(monkeypatch, tmp_path)
    ln = check.Line(check.OK, 'x', 'y')
    assert check.run_checks(_env({}), [('x', lambda e: ln)]) == [ln]
