"""smd admin release-list: compute_list, repo_urls, and the --write/--check verb."""
import argparse
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sigmond.catalog import CatalogEntry
from sigmond.release_list import (
    ROWS, ReleaseListError, compute_list, format_list, load_list, repo_urls,
)

REPO = Path(__file__).resolve().parent.parent
SMD = REPO / "bin" / "smd"

DERIVED = {"onion", "wsjtx", "ubersdr_wsprd", "ka9q-radio", "ft8_lib"}


def sha(i):
    return "%040x" % (0xdead0000 + i)


def _entry(name):
    return CatalogEntry(name=name, kind="client", description="", repo=f"https://github.com/HamSCI/{name}",
                        uses=(), requires=(), contract=None, install_script=None)


CATALOG = {n: _entry(n) for n in ROWS if n not in DERIVED | {"ka9q-python", "sigmond-proxmox"}}
CATALOG["sigmond"] = _entry("sigmond")
FAKE_URLS = repo_urls(CATALOG)
HEADS = {u: sha(i) for i, u in enumerate(sorted(FAKE_URLS.values()))}
ONION, WSJTX, UBER = "a" * 40, "b" * 40, "c" * 40
COMPAT, FT8 = "d" * 40, "e" * 40
SMD_FIXTURE = (f"_ONION_COMMIT = '{ONION}'   # x\n_WSJTX_COMMIT = '{WSJTX}'\n"
               f"_UBERSDR_COMMIT = '{UBER}'\n")


def fake_files(url, rev, path):
    if path == "ka9q_radio_compat":
        assert url == FAKE_URLS["ka9q-python"] and rev == HEADS[url]
        return f"# pin\n{COMPAT}\n"
    assert path == "scripts/install.sh"
    assert url == FAKE_URLS["psk-recorder"] and rev == HEADS[url]
    return f'FT8LIB_PIN="{FT8}"\n'


def compute(**over):
    kw = dict(urls=FAKE_URLS, smd_text=SMD_FIXTURE,
              remote_head=lambda u: HEADS[u], fetch_file=fake_files)
    kw.update(over)
    return compute_list("v3.94", **kw)


def test_repo_urls_shape():
    assert set(FAKE_URLS) == set(ROWS) - DERIVED
    assert FAKE_URLS["ka9q-python"] == "https://github.com/HamSCI/ka9q-python"
    # The old name answers before the rename and redirects after it; the new
    # name 404s before it, which would stop --write.
    assert FAKE_URLS["sigmond-proxmox"] == "https://github.com/HamSCI/sigmond-appliance"
    assert "sigmond" not in FAKE_URLS


def test_compute_before_the_rename_reads_the_image_repo_by_its_old_name():
    """GitHub before the rename: the new name does not exist, so a list that
    asked for it would die.  The image repo row still gets its head."""
    def before_rename(url):
        if url == "https://github.com/HamSCI/sigmond-proxmox":
            raise ReleaseListError("git ls-remote: repository not found")
        return HEADS[url]
    rl = compute(remote_head=before_rename)
    assert rl.components["sigmond-proxmox"] == HEADS["https://github.com/HamSCI/sigmond-appliance"]


def test_compute_takes_heads_and_owners():
    rl = compute()
    assert rl.components["hf-timestd"] == HEADS[FAKE_URLS["hf-timestd"]]
    assert rl.components["onion"] == ONION
    assert rl.components["wsjtx"] == WSJTX and rl.components["ubersdr_wsprd"] == UBER
    assert rl.components["ka9q-radio"] == COMPAT
    assert rl.components["ft8_lib"] == FT8
    assert "sigmond" not in rl.components and set(rl.components) == set(ROWS)


def test_compute_unreachable_head_refuses():
    bad = FAKE_URLS["gmag-webui"]
    with pytest.raises(ReleaseListError, match="gmag-webui"):
        compute(remote_head=lambda u: "" if u == bad else HEADS[u])


def load_smd():
    loader = importlib.machinery.SourceFileLoader("smd_rl_cmd", str(SMD))
    spec = importlib.util.spec_from_loader("smd_rl_cmd", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def ns(**kw):
    d = dict(write=False, check=False, version=None, list=None, manifest=None,
             base=None, require_all=False)
    d.update(kw)
    return argparse.Namespace(**d)


def test_write_then_check_round_trip(tmp_path, monkeypatch, capsys):
    smd = load_smd()
    monkeypatch.setattr(smd, "load_catalog", lambda *a, **k: CATALOG)
    monkeypatch.setattr(smd, "_remote_head", lambda u: HEADS[u])
    monkeypatch.setattr(smd, "_release_raw_file", fake_files)
    out = tmp_path / "rel" / "components.toml"
    assert smd.cmd_admin_release_list(ns(write=True, version="v3.94", list=str(out))) == 0
    printed = capsys.readouterr().out
    assert "hf-timestd" in printed and "not committed" in printed
    rl = load_list(out)
    assert rl.version == "v3.94" and set(rl.components) == set(ROWS)
    # a manifest carrying the same rows
    body = "".join(f"    {n:<16} {c[:8]}\n" for n, c in sorted(rl.components.items())
                   if n != "sigmond-proxmox")
    text = ("image_version: v3.94\nappliance_commit: %s\nappliance_tag: v3.94\n"
            "\ncomponents (live):\n%s    sigmond         daba1f6\n"
            % (rl.components["sigmond-proxmox"], body))
    mf = tmp_path / "m.txt"
    mf.write_text(text)
    assert smd.cmd_admin_release_list(ns(check=True, list=str(out), manifest=str(mf))) == 0


def test_write_needs_version(tmp_path, capsys):
    smd = load_smd()
    assert smd.cmd_admin_release_list(ns(write=True, list=str(tmp_path / "x.toml"))) == 2


def git(path, *a):
    return subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True,
                          text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}).stdout.strip()


def run_check(*args):
    env = {**os.environ, "PYTHONPATH": str(REPO / "lib")}
    env.pop("SUDO_USER", None)
    return subprocess.run([sys.executable, str(SMD), "admin", "release-list", *args],
                          capture_output=True, text=True, env=env, cwd=REPO)


def test_check_live_against_fake_base(tmp_path):
    base = tmp_path / "base"
    comps = {}
    for n in ROWS:
        if n == "sigmond-proxmox":
            comps[n] = sha(99)
            continue
        d = base / n
        d.mkdir(parents=True)
        git(d, "init", "-q")
        git(d, "commit", "-q", "--allow-empty", "-m", "c")
        comps[n] = git(d, "rev-parse", "HEAD")
    from sigmond.release_list import ReleaseList
    lst = tmp_path / "components.toml"
    lst.write_text(format_list(ReleaseList("v3.94", comps)))
    r = run_check("--check", "--list", str(lst), "--base", str(base))
    assert r.returncode == 0, r.stdout + r.stderr
    git(base / "callhash", "commit", "-q", "--allow-empty", "-m", "moved")
    r = run_check("--check", "--list", str(lst), "--base", str(base))
    assert r.returncode == 1
    assert "callhash" in r.stdout and "wsjtx" not in r.stdout
    # a list that does not exist is an error, not a mismatch
    r = run_check("--check", "--list", str(tmp_path / "none.toml"), "--base", str(base))
    assert r.returncode == 2


def test_cli_output_is_ascii(tmp_path):
    r = run_check("--check", "--list", str(tmp_path / "none.toml"))
    assert r.returncode == 2
    (r.stdout + r.stderr).encode("ascii")
    r = run_check("--help")
    r.stdout.encode("ascii")
