"""install-host-link.sh: every new Proxmox host gets sigmond-link, following its VM
(plan 2026-11-14 satellite-host-guard, Task 2)."""
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "proxmox" / "install-host-link.sh"


@pytest.fixture
def root(tmp_path):
    bin_ = tmp_path / "stubs"
    bin_.mkdir()
    for name, body in (("systemctl", "exit 0"), ("hostname", "echo testhost"),
                       ("apt-get", "exit 100"), ("pdftotext", None)):
        if body is None:
            continue
        p = bin_ / name
        p.write_text(f"#!/bin/sh\n{body}\n")
        p.chmod(0o755)
    return tmp_path


def _run(root, **env):
    e = {"PATH": f"{root / 'stubs'}:/usr/bin:/bin", "SIGMOND_ROOT": str(root / "r"), **env}
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=e, timeout=60)


def test_new_link_toml_follows_vm(root):
    r = _run(root, VMID="100")
    assert r.returncode == 0, r.stderr
    text = (root / "r" / "etc/sigmond/link.toml").read_text()
    assert "follow_vm = 100" in text
    assert (root / "r" / "usr/local/sbin/sigmond-link").exists()
    assert (root / "r" / "etc/systemd/system/sigmond-link-rac.timer").exists()


def test_without_vmid_no_follow(root):
    assert _run(root).returncode == 0
    assert "follow_vm" not in (root / "r" / "etc/sigmond/link.toml").read_text()


def test_existing_link_toml_kept(root):
    p = root / "r" / "etc/sigmond/link.toml"
    p.parent.mkdir(parents=True)
    p.write_text("[link_schedule]\nenabled = true\n")
    assert _run(root, VMID="100").returncode == 0
    assert p.read_text() == "[link_schedule]\nenabled = true\n"


@pytest.mark.skipif(bool(__import__("shutil").which("pdftotext")), reason="pdftotext installed here")
def test_poppler_failure_is_a_warning(root):
    r = _run(root, VMID="100")
    assert r.returncode == 0 and "poppler-utils not installed" in r.stderr
