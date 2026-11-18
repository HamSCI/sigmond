"""`smd version` carries `smd component list`'s lifecycle state and version
policy; plain `smd component list` prints the version table under a note
(drift-report spec, "One command per question")."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import types
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
NOTE = ("smd component list now shows what smd version shows; use smd "
        "version (this form retires in v3.95)")


def _smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_version_absorbs", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_version_absorbs", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _smd()


def _main(argv, monkeypatch):
    monkeypatch.setattr("sys.argv", ["smd", *argv])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = smd.main()
    return rc, out.getvalue()


def test_component_list_prints_version_table_with_note(monkeypatch):
    seen = {}

    def fake_version(args):
        seen["args"] = args
        print("VERSION TABLE")
        return 0

    monkeypatch.setattr(smd, "cmd_version", fake_version)
    monkeypatch.setattr(smd, "cmd_list", lambda a: (_ for _ in ()).throw(
        AssertionError("plain component list reached cmd_list")))
    rc, out = _main(["component", "list"], monkeypatch)
    assert rc == 0
    lines = out.splitlines()
    assert lines[0] == NOTE
    assert "VERSION TABLE" in lines
    assert lines.index("VERSION TABLE") > 0
    assert seen["args"].hashes is False


def test_component_list_catalog_and_json_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(smd, "cmd_version", lambda a: (_ for _ in ()).throw(
        AssertionError("--catalog/--json reached cmd_version")))
    monkeypatch.setattr(smd, "cmd_list", lambda a: calls.append(a) or 0)
    for argv in (["component", "list", "--catalog"],
                 ["component", "list", "--json", "--no-fetch"]):
        rc, out = _main(argv, monkeypatch)
        assert rc == 0
        assert NOTE not in out
    assert len(calls) == 2


def test_version_shows_lifecycle_and_policy(monkeypatch):
    catalog = {"hf-timestd": types.SimpleNamespace(name="hf-timestd"),
               "ka9q-python": types.SimpleNamespace(name="ka9q-python")}
    monkeypatch.setattr(smd, "load_catalog", lambda: catalog)
    states = {"hf-timestd": ("enabled, running — 4 units", "", "latest"),
              "ka9q-python": ("library", "", "v3.30.0")}
    monkeypatch.setattr(smd, "_component_lifecycle",
                        lambda name, entry, topo, cs: states[name])
    rows = dict(smd._version_states())
    assert rows["sigmond"] == ("self (smd)", "latest")
    assert rows["hf-timestd"] == ("enabled, running", "latest")
    assert rows["ka9q-python"] == ("library", "v3.30.0")

    args = argparse.Namespace(base="/nonexistent/base", hashes=False, offline=True)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert smd.cmd_version(args) == 0
    text = out.getvalue()
    assert "\nlifecycle and version policy:\n" in text
    row = next(ln for ln in text.splitlines() if ln.strip().startswith("ka9q-python"))
    assert row.split() == ["ka9q-python", "library", "v3.30.0"]
    # the block stands after a blank line, apart from `components (live):`
    head, _sep, _tail = text.partition("lifecycle and version policy:")
    assert head.endswith("\n\n")


def test_version_survives_an_unreadable_catalog(monkeypatch):
    def boom():
        raise FileNotFoundError("no catalog")
    monkeypatch.setattr(smd, "load_catalog", boom)
    assert smd._version_states() == []
