"""Tests for scripts/proxmox/pm-align.py — the Proxmox-HOST alignment tool.

A PM runs no sigmond and has no git; pm-align is one stdlib file, loaded
here by path (its filename has a dash).  Every host path is joined under a
temp root and every command goes through an injected ``run``.
"""
import hashlib
import importlib.util
import io
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROXMOX = REPO / "scripts" / "proxmox"
_spec = importlib.util.spec_from_file_location("pm_align", PROXMOX / "pm-align.py")
pm_align = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pm_align)

FB = "#!/bin/bash\necho @@VERSION@@\ncat > /usr/local/lib/sigmond-net.sh <<'NETLIBEOF'\nnet\nNETLIBEOF\n"
FB_RENDERED = FB.replace("@@VERSION@@", "v3.53")


def _manifest(sha=None, wiz="d" * 40):
    return ("image_version: v3.53\n"
            f"appliance_commit: {'a' * 40}\n"
            "appliance_tag: v3.53\n"
            f"wizard_commit: {wiz}\n"
            f"firstboot_sha256: {sha or hashlib.sha256(FB_RENDERED.encode()).hexdigest()}\n")


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _urlopen(routes):
    def op(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        for key, body in routes.items():
            if key in url:
                return _Resp(body if isinstance(body, bytes) else body.encode())
        raise AssertionError(f"unexpected URL {url}")
    return op


def _release_routes(manifest=None):
    meta = {"tag_name": "v3.53", "draft": False, "prerelease": False,
            "assets": [{"name": "x.manifest.txt", "browser_download_url": "https://dl/m"}]}
    return {"/releases/latest": json.dumps(meta), "https://dl/m": manifest or _manifest()}


def test_fetch_release_reads_the_manifest_preamble():
    rel = pm_align.fetch_release(urlopen=_urlopen(_release_routes()))
    assert (rel.tag, rel.appliance_commit, rel.wizard_commit) == ("v3.53", "a" * 40, "d" * 40)


def test_fetch_release_refuses_a_preamble_tag_mismatch():
    routes = _release_routes(_manifest().replace("image_version: v3.53", "image_version: v3.52"))
    with pytest.raises(pm_align.PmAlignError):
        pm_align.fetch_release(urlopen=_urlopen(routes))


def test_fetch_release_refuses_a_short_commit():
    routes = _release_routes(_manifest(wiz="daba1f6"))
    with pytest.raises(pm_align.PmAlignError):
        pm_align.fetch_release(urlopen=_urlopen(routes))


def _source_routes(fb=FB, wizard='VMID="${SIGMOND_VMID:-120}"\n'):
    r = _release_routes()
    r["sigmond-appliance/" + "a" * 40 + "/firstboot-v3.sh"] = fb
    r["sigmond/" + "d" * 40 + "/scripts/proxmox/sigmond-wizard.sh"] = wizard
    for n in pm_align.PROXMOX_FILES:
        r["sigmond/" + "d" * 40 + "/scripts/proxmox/" + n] = f"#{n}\n"
    return r


def test_fetch_sources_renders_and_verifies_firstboot():
    op = _urlopen(_source_routes())
    src = pm_align.fetch_sources(pm_align.fetch_release(urlopen=op), vmid=100, urlopen=op)
    assert "echo v3.53" in src.firstboot


def test_fetch_sources_refuses_a_firstboot_whose_hash_differs():
    op = _urlopen(_source_routes(fb=FB + "tampered\n"))
    with pytest.raises(pm_align.PmAlignError, match="firstboot_sha256"):
        pm_align.fetch_sources(pm_align.fetch_release(urlopen=op), vmid=100, urlopen=op)


def test_fetch_sources_renders_the_wizard_vmid_as_the_build_does():
    op = _urlopen(_source_routes())
    src = pm_align.fetch_sources(pm_align.fetch_release(urlopen=op), vmid=100, urlopen=op)
    assert src.wizard == 'VMID="${SIGMOND_VMID:-100}"\n'
