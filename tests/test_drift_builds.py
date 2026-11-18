import subprocess

import pytest

from sigmond import build_stamp, decoder_choice
from sigmond.drift import builds

SHA_A = "a" * 40
SHA_B = "b" * 40


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(decoder_choice, "COORDINATION_TOML",
                        tmp_path / "none.toml")
    monkeypatch.setattr(decoder_choice, "BIN_DIR", tmp_path / "bin")
    (tmp_path / "bin").mkdir()


def _repo(base, name, build=False):
    d = base / name
    (d / ".git").mkdir(parents=True)
    if build:
        (d / "deploy.toml").write_text('[build]\nsteps = ["make"]\n')
    return d


def _heads(monkeypatch, mapping):
    monkeypatch.setattr(build_stamp, "head_of",
                        lambda p: mapping.get(p.name))


def _help(text):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=text, stderr="")
    return run


def _dec(tmp_path, parity="verified", sha=SHA_A):
    d = tmp_path / "dec"
    (d / "ubersdr").mkdir(parents=True)
    (d / "ubersdr" / "wsprd").write_text("")
    (d / "ubersdr" / ".provenance").write_text(f"{sha} parity={parity}\n")
    return d


def test_stale_build_is_fault(tmp_path, monkeypatch):
    _repo(tmp_path, "station-web", build=True)
    _heads(monkeypatch, {"station-web": SHA_A})
    stamps = tmp_path / "stamps"
    stamps.mkdir()
    (stamps / "station-web").write_text(SHA_B + "\n")
    items = builds.build_items(tmp_path, ["station-web"], enabled={},
                               decoder_dir=tmp_path / "nodec",
                               stamp_dir=stamps)
    assert [(i.kind, i.severity, i.subject, i.fix) for i in items] == [
        ("build", "fault", "station-web", "smd align --apply")]


def test_fresh_stamp_and_installer_components_are_quiet(tmp_path, monkeypatch):
    _repo(tmp_path, "station-web", build=True)
    _repo(tmp_path, "hf-timestd")  # install.sh owns its build: no stamp
    _heads(monkeypatch, {"station-web": SHA_A, "hf-timestd": SHA_A})
    stamps = tmp_path / "stamps"
    stamps.mkdir()
    (stamps / "station-web").write_text(SHA_A + "\n")
    assert builds.build_items(tmp_path, ["station-web", "hf-timestd"],
                              enabled={}, decoder_dir=tmp_path / "nodec",
                              stamp_dir=stamps) == []


def test_ubersdr_without_msk144_is_fault_when_meteor_enabled(tmp_path):
    items = builds.build_items(tmp_path, [], enabled={"meteor-scatter": True},
                               decoder_dir=_dec(tmp_path),
                               run=_help("usage: jt9 --ft8"))
    assert len(items) == 1
    assert items[0].severity == "fault"
    assert "lacks MSK144" in items[0].detail
    assert items[0].fix == "smd align --apply"


def test_ubersdr_with_msk144_is_quiet(tmp_path):
    assert builds.build_items(tmp_path, [], enabled={"meteor-scatter": True},
                              decoder_dir=_dec(tmp_path),
                              run=_help("--msk144 --ft8")) == []


def test_ubersdr_without_msk144_ignored_when_meteor_disabled(tmp_path):
    def boom(*a, **k):
        raise AssertionError("must not probe")
    assert builds.build_items(tmp_path, [], enabled={"meteor-scatter": False},
                              decoder_dir=_dec(tmp_path), run=boom) == []


def test_help_probe_timeout_is_fault_not_crash(tmp_path):
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 10)
    items = builds.build_items(tmp_path, [], enabled={"meteor-scatter": True},
                               decoder_dir=_dec(tmp_path), run=run)
    assert [i.severity for i in items] == ["fault"]


def test_wsjtx_live_while_ubersdr_good_is_drift(tmp_path):
    (tmp_path / "bin" / "wsprd").symlink_to(
        "/usr/local/libexec/sigmond/decoders/wsjtx/wsprd")
    items = builds.build_items(tmp_path, [], enabled={},
                               decoder_dir=_dec(tmp_path))
    assert [(i.severity, i.subject) for i in items] == [("drift", "decoders")]


def test_wsjtx_live_with_failed_parity_is_quiet(tmp_path):
    (tmp_path / "bin" / "wsprd").symlink_to(
        "/usr/local/libexec/sigmond/decoders/wsjtx/wsprd")
    assert builds.build_items(tmp_path, [], enabled={},
                              decoder_dir=_dec(tmp_path, parity="FAILED")) == []


def test_provenance_commit_differs_from_checkout(tmp_path, monkeypatch):
    _repo(tmp_path, "ubersdr_wsprd")
    _heads(monkeypatch, {"ubersdr_wsprd": SHA_B})
    items = builds.build_items(tmp_path, [], enabled={},
                               decoder_dir=_dec(tmp_path, sha=SHA_A))
    assert len(items) == 1
    assert items[0].detail == (
        "UberSDR decoders built from aaaaaaa, checkout at bbbbbbb")


def test_missing_provenance_with_folder_is_fault(tmp_path, monkeypatch):
    _repo(tmp_path, "ubersdr_wsprd")
    _heads(monkeypatch, {"ubersdr_wsprd": SHA_B})
    d = tmp_path / "dec"
    (d / "ubersdr").mkdir(parents=True)
    items = builds.build_items(tmp_path, [], enabled={}, decoder_dir=d)
    assert [i.severity for i in items] == ["fault"]


def test_operator_override_suppresses_decoder_items(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text("[decoders]\noperator_override = true\n")
    monkeypatch.setattr(decoder_choice, "COORDINATION_TOML", cfg)
    assert builds.build_items(tmp_path, [], enabled={"meteor-scatter": True},
                              decoder_dir=_dec(tmp_path),
                              run=_help("nothing")) == []
