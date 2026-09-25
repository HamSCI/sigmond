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
import sys
import urllib.error
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROXMOX = REPO / "scripts" / "proxmox"
_spec = importlib.util.spec_from_file_location("pm_align", PROXMOX / "pm-align.py")
pm_align = importlib.util.module_from_spec(_spec)
# Register before exec: pm-align.py's dataclasses (with `from __future__
# import annotations`) resolve field annotations against
# sys.modules[__name__], which a bare exec_module never populates.
sys.modules[_spec.name] = pm_align
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
    """routes maps a URL substring to a body (str/bytes) or a BaseException
    instance to raise (e.g. urllib.error.HTTPError, OSError) -- so a fake
    route can stand in for a real fetch failure, not just a real fetch."""
    def op(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        for key, body in routes.items():
            if key in url:
                if isinstance(body, BaseException):
                    raise body
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


_PM_ALIGN_KEY = "sigmond/" + "d" * 40 + "/scripts/proxmox/pm-align.py"


def test_fetch_sources_treats_a_404_on_pm_align_as_absent():
    routes = _source_routes()
    routes[_PM_ALIGN_KEY] = urllib.error.HTTPError(_PM_ALIGN_KEY, 404, "Not Found", None, None)
    op = _urlopen(routes)
    src = pm_align.fetch_sources(pm_align.fetch_release(urlopen=op), vmid=100, urlopen=op)
    assert "pm-align.py" not in src.proxmox


def test_fetch_sources_refuses_a_non_404_http_failure_on_pm_align():
    routes = _source_routes()
    routes[_PM_ALIGN_KEY] = urllib.error.HTTPError(_PM_ALIGN_KEY, 500, "Server Error", None, None)
    op = _urlopen(routes)
    with pytest.raises(pm_align.PmAlignError):
        pm_align.fetch_sources(pm_align.fetch_release(urlopen=op), vmid=100, urlopen=op)


def test_fetch_sources_refuses_a_raised_timeout_on_pm_align():
    routes = _source_routes()
    routes[_PM_ALIGN_KEY] = TimeoutError("timed out")
    op = _urlopen(routes)
    with pytest.raises(pm_align.PmAlignError):
        pm_align.fetch_sources(pm_align.fetch_release(urlopen=op), vmid=100, urlopen=op)


WIZ = '''#!/bin/bash
VMID="${SIGMOND_VMID:-120}"
cat > /usr/local/bin/sigmond-vm <<'VMEOF'
#!/bin/bash
VMID="${SIGMOND_VMID:-120}"
VMEOF
            cat > /usr/local/lib/sigmond/vm-port-relay.py <<'RLEOF'
#!/usr/bin/env python3
print("relay")
RLEOF
            for spec in "ssh:12222:22" "web:12223:8081" "station:12224:8000" "gmag:12225:8082"; do
                IFS=: read -r RNAME RLPORT RVPORT <<<"$spec"
                cat > "/etc/systemd/system/sigmond-vm-$RNAME-relay.socket" <<SOCKEOF
[Unit]
Description=Relay 127.0.0.1:$RLPORT → decoder VM :$RVPORT (IP via guest agent)
[Socket]
ListenStream=127.0.0.1:$RLPORT
Accept=yes
[Install]
WantedBy=sockets.target
SOCKEOF
                cat > "/etc/systemd/system/sigmond-vm-$RNAME-relay@.service" <<SVCEOF
[Unit]
Description=decoder-VM $RNAME relay (%i)
CollectMode=inactive-or-failed
[Service]
Type=simple
Environment=SIGMOND_VMID=$VMID
StandardInput=socket
StandardOutput=socket
StandardError=journal
ExecStart=/usr/local/lib/sigmond/vm-port-relay.py $RVPORT
SVCEOF
            done
'''
FB_FULL = ("cat > /usr/local/lib/sigmond-net.sh <<'NETLIBEOF'\nnet lib\nNETLIBEOF\n"
           "cat > /usr/local/sbin/sigmond-import.sh <<'IMPEOF'\n"
           "cat > /nested/inside <<'X'\nnope\nX\nIMPEOF\n"
           "cat > /usr/local/sbin/sigmond-issue <<'ISSEOF'\nissue\nISSEOF\n"
           "cat > /etc/systemd/system/sigmond-issue.service <<'ISVCEOF'\nsvc\nISVCEOF\n"
           "cat > /etc/systemd/system/sigmond-issue.timer <<'ITEOF'\ntimer\nITEOF\n"
           "cat > /etc/sigmond-appliance/version <<EOF\n$VERSION\nEOF\n")


def _src():
    return pm_align.Sources(FB_FULL, pm_align.render_wizard(WIZ, 100),
                            {"pm-heartbeat.py": b"hb"})


def test_extract_heredocs_takes_quoted_top_level_bodies_only():
    docs = pm_align.extract_heredocs(FB_FULL)
    assert docs["/usr/local/lib/sigmond-net.sh"] == "net lib\n"
    assert "/nested/inside" not in docs            # inside another heredoc's body
    assert "/etc/sigmond-appliance/version" not in docs   # unquoted: site config


def test_extract_heredocs_allows_an_indented_start():
    assert pm_align.extract_heredocs(WIZ)["/usr/local/lib/sigmond/vm-port-relay.py"] \
        == '#!/usr/bin/env python3\nprint("relay")\n'


def test_relay_units_match_the_wizard_template_for_all_four():
    units = {f.path: f.content.decode() for f in pm_align.relay_units(WIZ, 100)}
    assert len(units) == 8
    sock = units["/etc/systemd/system/sigmond-vm-gmag-relay.socket"]
    assert "ListenStream=127.0.0.1:12225" in sock and ":8082" in sock
    svc = units["/etc/systemd/system/sigmond-vm-station-relay@.service"]
    assert "Environment=SIGMOND_VMID=100" in svc
    assert "vm-port-relay.py 8000" in svc


def test_desired_files_is_exactly_the_refresh_set():
    paths = {f.path for f in pm_align.desired_files(_src(), 100)}
    assert "/usr/local/sbin/sigmond-setup" in paths
    assert "/usr/local/bin/sigmond-vm" in paths
    assert "/usr/local/sbin/sigmond-import.sh" not in paths
    assert not any("netfix" in p or "setnet" in p for p in paths)


def test_desired_files_renders_sigmond_vm_with_the_host_vmid():
    f = next(f for f in pm_align.desired_files(_src(), 100) if f.path == "/usr/local/bin/sigmond-vm")
    assert b"SIGMOND_VMID:-100" in f.content


def test_plan_files_states(tmp_path):
    a = pm_align.HostFile("/x/current", b"same", 0o644, "t")
    b = pm_align.HostFile("/x/changed", b"new", 0o644, "t")
    c = pm_align.HostFile("/x/missing", b"m", 0o644, "t")
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "current").write_bytes(b"same")
    (tmp_path / "x" / "changed").write_bytes(b"old")
    states = {ch.file.path: ch.state for ch in pm_align.plan_files([a, b, c], tmp_path)}
    assert states == {"/x/current": "current", "/x/changed": "changed", "/x/missing": "missing"}


def test_host_vmid_reads_the_existing_relay_unit(tmp_path):
    d = tmp_path / "etc/systemd/system"
    d.mkdir(parents=True)
    (d / "sigmond-vm-ssh-relay@.service").write_text("Environment=SIGMOND_VMID=107\n")
    assert pm_align.host_vmid(tmp_path) == 107


def test_topology_note_names_a_vm_on_the_lan(tmp_path):
    (tmp_path / "etc/network").mkdir(parents=True)
    (tmp_path / "etc/network/interfaces").write_text("auto vmbr0\niface vmbr0 inet dhcp\n")
    assert "LAN" in pm_align.topology_note(tmp_path)
    (tmp_path / "etc/network/interfaces").write_text("auto vmbr0\nauto vmbr1\n")
    assert pm_align.topology_note(tmp_path) is None
