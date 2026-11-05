"""`smd apply` leaves the uploader manifest alone until hs-uploader exists.

sigma, 2026-11-05: apply ran in bring-up Stage 1, hs-uploader does not land
until Stage 4, /var/lib/hs-uploader was missing, and apply exited 1.
"""
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_apply_uploader_absent", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(
        "smd_under_test_apply_uploader_absent", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def test_apply_skips_the_uploader_step_until_hs_uploader_exists(
        monkeypatch, capsys, tmp_path):
    from sigmond import uploader_manifest as um
    from sigmond.commands import uploader as up
    monkeypatch.setattr(um, 'HS_UPLOADER_PYTHON', tmp_path / 'no-python')
    called = []
    monkeypatch.setattr(up, 'cmd_uploader_manifest',
                        lambda a: called.append(a) or 1)
    assert smd._apply_uploader_manifest() is True
    assert called == []
    out = capsys.readouterr()
    assert 'stage 4' in (out.out + out.err).lower()


def test_apply_runs_the_uploader_step_once_hs_uploader_exists(
        monkeypatch, tmp_path):
    from sigmond import uploader_manifest as um
    from sigmond.commands import uploader as up
    py = tmp_path / 'python'
    py.write_text('')
    monkeypatch.setattr(um, 'HS_UPLOADER_PYTHON', py)
    monkeypatch.setattr(um, 'HS_UPLOADER_STATE_DIR', tmp_path)
    called = []
    monkeypatch.setattr(up, 'cmd_uploader_manifest',
                        lambda a: called.append(a) or 0)
    assert smd._apply_uploader_manifest() is True
    assert len(called) == 1


def test_a_venv_left_from_an_earlier_install_does_not_count(
        monkeypatch, tmp_path):
    # sigma and AC0G-B2, 2026-11-05: /opt/hs-uploader/venv survived from June,
    # but /var/lib/hs-uploader did not.  apply took the venv as an install,
    # ran the manifest step, and the carry-over marker had no directory.
    from sigmond import uploader_manifest as um
    from sigmond.commands import uploader as up
    py = tmp_path / 'python'
    py.write_text('')
    monkeypatch.setattr(um, 'HS_UPLOADER_PYTHON', py)
    monkeypatch.setattr(um, 'HS_UPLOADER_STATE_DIR', tmp_path / 'absent')
    called = []
    monkeypatch.setattr(up, 'cmd_uploader_manifest',
                        lambda a: called.append(a) or 1)
    assert smd._apply_uploader_manifest() is True
    assert called == []
