"""The prune drop-in no longer moves raw days to /mnt/timestd-archive: the
archiver copies to the archive drive, and the cleanup only deletes from the
SSD (archive-drive design 2026-11-12, 'Retiring the old move')."""
import importlib.util
import inspect
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    spec = importlib.util.spec_from_loader("smd_tune", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = str(REPO / "bin" / "smd")
    exec(compile((REPO / "bin" / "smd").read_text(), "bin/smd", "exec"), mod.__dict__)
    return mod


def test_tune_timestd_dropin_has_no_archive_root():
    src = inspect.getsource(_smd().cmd_timestd_tune_storage)
    assert "--threshold" in src
    assert "--archive-root" not in src
