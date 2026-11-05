"""A first install from the repos holds uploads (spec 6.3, Michael 2026-11-05).

AC0G-B2 (2026-11-05): bench spots and spots under a wrong call must not leave a
fresh repo install by surprise.  The image keeps its own behaviour.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import tempfile
from pathlib import Path
from unittest import mock

from sigmond.bringup import build_plan, should_hold_uploads, UPLOAD_HOLD_REASON
from sigmond.catalog import Profile

REPO = Path(__file__).resolve().parent.parent


def _profile():
    return Profile(name='dasi2', clients=('hf-timestd', 'wspr-recorder'),
                   local_radiod_infra=(), optional=())


def _load_smd():
    loader = importlib.machinery.SourceFileLoader('smd_hold', str(REPO / 'bin' / 'smd'))
    spec = importlib.util.spec_from_loader('smd_hold', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _coord(text):
    d = tempfile.mkdtemp()
    p = Path(d) / 'coordination.toml'
    if text is not None:
        p.write_text(text)
    return p


def test_a_repo_install_holds_uploads_before_the_manifest():
    plan = build_plan(_profile(), local_radiod=True, hold_uploads=True)
    argvs = [s.argv for s in plan.steps]
    hold = [i for i, a in enumerate(argvs)
            if a[1:4] == ['config', 'uploads', 'disable']]
    manifest = [i for i, a in enumerate(argvs) if a[1:4] == ['admin', 'uploader', 'manifest']]
    assert len(hold) == 1 and manifest and hold[0] < manifest[0]
    assert argvs[hold[0]][-2:] == ['--reason', UPLOAD_HOLD_REASON]
    assert UPLOAD_HOLD_REASON == 'first install from the repos: held until checks pass'
    assert not any(a[1:4] == ['config', 'uploads', 'disable']
                   for a in [s.argv for s in build_plan(_profile(), local_radiod=True).steps])


def test_an_image_install_does_not_hold_uploads(tmp_path):
    root = tmp_path / 'root'
    (root / 'etc/sigmond-appliance').mkdir(parents=True)
    (root / 'etc/sigmond-appliance/version').write_text('3.85\n')
    assert should_hold_uploads(_coord(None), root=str(root)) is False
    assert should_hold_uploads(_coord(None), root=str(tmp_path / 'bare')) is True


def test_an_existing_uploads_block_is_respected(tmp_path):
    bare = str(tmp_path)
    assert should_hold_uploads(_coord('[host]\ncall = "AC0G"\n'), root=bare) is True
    assert should_hold_uploads(_coord('[uploads]\nenabled = true\n'), root=bare) is False
    assert should_hold_uploads(
        _coord('[uploads]\nenabled = false\nreason = "x"\n'), root=bare) is False


def _finish(smd, hold, answer):
    plan = build_plan(_profile(), local_radiod=True, hold_uploads=hold)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
            mock.patch.object(smd, '_ask_optional_yes_no', return_value=answer) as ask, \
            mock.patch.object(smd, '_run', return_value=mock.Mock(returncode=0)) as run, \
            mock.patch.object(smd, '_settle_firstrun_marker'):
        rc = smd._bringup_finish(plan, 'dasi2', [], smd='/x/smd')
    return rc, ask, run, out.getvalue()


def test_a_complete_held_run_releases_uploads_on_yes():
    smd = _load_smd()
    rc, ask, run, _ = _finish(smd, True, True)
    assert rc == 0
    assert ask.call_args.args[0] == 'Send uploads now? [y/N] '
    assert run.call_args.args[0] == ['/x/smd', 'sink', 'upload']


def test_a_complete_held_run_keeps_the_hold_on_no_and_prints_the_command():
    smd = _load_smd()
    for answer in (False, None):          # no, or nobody to ask
        rc, ask, run, out = _finish(smd, True, answer)
        assert rc == 0
        run.assert_not_called()
        assert '/x/smd sink upload' in out


def test_no_prompt_without_a_hold_or_after_a_failed_step():
    smd = _load_smd()
    rc, ask, run, _ = _finish(smd, False, True)
    ask.assert_not_called()
    plan = build_plan(_profile(), local_radiod=True, hold_uploads=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
            mock.patch.object(smd, '_ask_optional_yes_no') as ask2:
        smd._bringup_finish(plan, 'dasi2', ['x (exit 1)'], smd='/x/smd')
    ask2.assert_not_called()


# --- a rerun after a partial/incomplete first run (fix round 1) ---------------

def _with_coord(smd, mode_text):
    from sigmond.coordination import load_coordination
    p = _coord(mode_text)
    return mock.patch('sigmond.coordination.load_coordination',
                      lambda *a, **k: load_coordination(p))


def _held_block(reason):
    return f'[uploads]\nenabled = false\nreason = "{reason}"\n'


def test_a_complete_rerun_offers_the_release_when_the_first_install_hold_stands():
    smd = _load_smd()
    for answer in (True, False):
        with _with_coord(smd, _held_block(UPLOAD_HOLD_REASON)):
            rc, ask, run, out = _finish(smd, False, answer)
        ask.assert_called_once()
        if answer:
            assert run.call_args.args[0] == ['/x/smd', 'sink', 'upload']
        else:
            run.assert_not_called()
            assert '/x/smd sink upload' in out


def test_an_operators_own_hold_is_never_offered():
    smd = _load_smd()
    with _with_coord(smd, _held_block('bench, do not send')):
        rc, ask, run, out = _finish(smd, False, True)
    ask.assert_not_called()
    run.assert_not_called()


def test_a_partial_run_with_the_hold_says_uploads_stay_held():
    smd = _load_smd()
    plan = build_plan(_profile(), local_radiod=True, hold_uploads=True,
                      sdr_absent=True)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = smd._bringup_finish(plan, 'dasi2', [], smd='/x/smd')
    assert rc == smd.BRINGUP_PARTIAL_RC
    assert 'uploads stay held until a complete run' in out.getvalue()
    assert '/x/smd sink upload' in out.getvalue()
