"""`smd admin log --level` and its --for expiry."""

import importlib.machinery
import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import sigmond.log_cmd as log_cmd

REPO = Path(__file__).resolve().parent.parent


def _smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader("smd_under_test_logexp", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_logexp", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _smd()


def _run(tmp_path, level, for_duration=None):
    env = tmp_path / "coordination.env"
    real = log_cmd.set_log_level
    args = SimpleNamespace(client=None, level=level, files=False, topology=None,
                           for_duration=for_duration)
    with mock.patch.object(smd, "set_log_level",
                           lambda c, l, until=None: real(c, l, env_path=env, until=until)), \
         mock.patch.object(smd, "_load_topology", return_value=None), \
         mock.patch.object(smd, "send_sighup", return_value=[]), \
         mock.patch.object(smd, "_enabled_components", return_value=[]):
        rc = smd.cmd_log(args)
    return rc, (env.read_text() if env.exists() else "")


def _until(text):
    for line in text.splitlines():
        if line.startswith("CLIENT_LOG_LEVEL_UNTIL="):
            return datetime.strptime(line.split("=")[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return None


def test_debug_defaults_to_24h(tmp_path):
    rc, text = _run(tmp_path, "DEBUG")
    delta = _until(text) - datetime.now(timezone.utc)
    assert rc == 0 and timedelta(hours=23, minutes=59) < delta <= timedelta(hours=24)


def test_info_without_for_has_no_expiry(tmp_path):
    rc, text = _run(tmp_path, "INFO")
    assert rc == 0 and "_UNTIL" not in text


def test_explicit_for_applies_to_any_level(tmp_path):
    rc, text = _run(tmp_path, "WARNING", "2h")
    delta = _until(text) - datetime.now(timezone.utc)
    assert rc == 0 and timedelta(hours=1, minutes=59) < delta <= timedelta(hours=2)


def test_bad_duration_returns_2(tmp_path):
    rc, text = _run(tmp_path, "DEBUG", "soon")
    assert rc == 2 and text == ""
