"""Tests for scripts/proxmox/pm-align.py — the Proxmox-HOST alignment tool.

A PM runs no sigmond and has no git; pm-align is one stdlib file, loaded
here by path (its filename has a dash).  Every host path is joined under a
temp root and every command goes through an injected ``run``.
"""
import base64
import hashlib
import http.client
import importlib.util
import inspect
import io
import json
import os
import re
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

WIZARD = (PROXMOX / "sigmond-wizard.sh").read_text()

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
    r["sigmond-appliance/" + "a" * 40 + "/sigmond-vm6proxy"] = VM6_BODY
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


def test_not_touched_lists_every_global_constraint_area():
    text = " ".join(pm_align.NOT_TOUCHED)
    for phrase in ("/var/lib/vz/snippets", "initramfs", "modules", "sigmond-netfix.service"):
        assert phrase in text


def _fake_run(calls, rc=0, stdout="", stderr=""):
    def run(argv, **kw):
        calls.append(list(argv))
        # tunnel_apply's active check comes first and needs "active" to
        # proceed; nothing outside the tunnel tests calls is-active, so this
        # is a no-op for every other caller of _fake_run.
        if argv[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        return subprocess.CompletedProcess(argv, rc, stdout, stderr)
    return run


def test_apply_files_backs_up_then_writes_with_mode(tmp_path):
    old = tmp_path / "usr/local/sbin/sigmond-issue"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    f = pm_align.HostFile("/usr/local/sbin/sigmond-issue", b"new", 0o755, "t")
    written = pm_align.apply_files([pm_align.Change(f, "changed")], tmp_path, tmp_path / "bk")
    assert written == ["/usr/local/sbin/sigmond-issue"]
    assert old.read_bytes() == b"new"
    assert oct(old.stat().st_mode & 0o777) == oct(0o755)
    assert (tmp_path / "bk/usr/local/sbin/sigmond-issue").read_bytes() == b"old"


def test_apply_files_skips_current(tmp_path):
    f = pm_align.HostFile("/a", b"x", 0o644, "t")
    assert pm_align.apply_files([pm_align.Change(f, "current")], tmp_path, tmp_path / "bk") == []
    assert not (tmp_path / "a").exists()


def test_enable_new_relays_enables_only_sockets_that_were_missing():
    calls = []
    ch = [pm_align.Change(pm_align.HostFile(
              "/etc/systemd/system/sigmond-vm-station-relay.socket", b"", 0o644, "t"), "missing"),
          pm_align.Change(pm_align.HostFile(
              "/etc/systemd/system/sigmond-vm-ssh-relay.socket", b"", 0o644, "t"), "changed")]
    enabled = pm_align.enable_new_relays(ch, run=_fake_run(calls))
    assert enabled == ["sigmond-vm-station-relay.socket"]
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "--now", "sigmond-vm-station-relay.socket"] in calls
    assert not any("sigmond-vm-ssh-relay.socket" in c for c in calls if "enable" in c)


def test_write_record_names_the_release(tmp_path):
    rel = pm_align.Release("v3.53", "a" * 40, "d" * 40, "f" * 64, "")
    p = pm_align.write_record(tmp_path, rel, ["/x"], "2026-09-25T12:00:00Z")
    doc = json.loads(p.read_text())
    assert doc["release"] == "v3.53" and doc["files_written"] == ["/x"]
    assert doc["wizard_commit"] == "d" * 40
    assert doc["tunnel"] == "n/a"                  # default when the caller says nothing
    assert p == tmp_path / "etc/sigmond-appliance/host-aligned.json"


def test_write_record_carries_an_explicit_tunnel_value(tmp_path):
    rel = pm_align.Release("v3.53", "a" * 40, "d" * 40, "f" * 64, "")
    p = pm_align.write_record(tmp_path, rel, ["/x"], "2026-09-25T12:00:00Z", tunnel="pending")
    assert json.loads(p.read_text())["tunnel"] == "pending"


def test_main_apply_refuses_when_not_root(tmp_path, monkeypatch):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 1000)
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.53", "a" * 40, "d" * 40, "f" * 64, ""))
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: _src())
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run([]))
    assert rc == 2
    assert not (tmp_path / "etc/sigmond-appliance/host-aligned.json").exists()


def test_main_apply_writes_files_and_record(tmp_path, monkeypatch):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.53", "a" * 40, "d" * 40, "f" * 64, ""))
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: _src())
    calls = []
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run(calls))
    assert rc == 0
    assert (tmp_path / "usr/local/sbin/sigmond-setup").exists()
    assert json.loads((tmp_path / "etc/sigmond-appliance/host-aligned.json").read_text())["release"] == "v3.53"


def test_main_dry_run_exit_codes(tmp_path, monkeypatch):
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.53", "a" * 40, "d" * 40, "f" * 64, ""))
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: _src())
    assert pm_align.main([], root=tmp_path) == 1

    for f in pm_align.desired_files(_src(), pm_align.host_vmid(tmp_path)):
        dest = pm_align._under(tmp_path, f.path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f.content)
    assert pm_align.main([], root=tmp_path) == 0

    def _raise(tag=None, urlopen=None):
        raise pm_align.PmAlignError("no release")
    monkeypatch.setattr(pm_align, "fetch_release", _raise)
    assert pm_align.main([], root=tmp_path) == 2


# ---------------------------------------------------------------------------
# Task 4: the tunnel step

TOML4 = '''serverAddr = "gw2.wsprdaemon.org"
serverPort = 35736
user = "dd986638365fd1d7"

[webServer]
addr = "127.0.0.1"
port = 7500

[[proxies]]
name = "AC0G_ND-vm-ssh"
type = "tcp"
localIP = "127.0.0.1"
localPort = 12222
remotePort = 36305

[[proxies]]
name = "AC0G_ND-vm-web"
type = "tcp"
localIP = "127.0.0.1"
localPort = 12223
remotePort = 46305
'''


def test_tunnel_plan_appends_only_the_missing_channels():
    new, added = pm_align.tunnel_plan(TOML4, "505")
    assert added == ["AC0G_ND-vm-station", "AC0G_ND-vm-gmag"]
    assert new.startswith(TOML4)                       # existing text byte-for-byte
    assert 'name = "AC0G_ND-vm-station"' in new and "remotePort = 49305" in new
    assert "localPort = 12225" in new and "remotePort = 50305" in new


def test_tunnel_plan_is_a_no_op_when_complete():
    full, _ = pm_align.tunnel_plan(TOML4, "505")
    assert pm_align.tunnel_plan(full, "505") == (full, [])


def test_tunnel_plan_refuses_a_rac_number_that_disagrees_with_the_ports():
    with pytest.raises(pm_align.PmAlignError, match="RAC"):
        pm_align.tunnel_plan(TOML4, "506")


def test_tunnel_plan_refuses_a_non_integer_rac_marker():
    with pytest.raises(pm_align.PmAlignError, match="RAC"):
        pm_align.tunnel_plan(TOML4, "not-a-number")


def test_tunnel_plan_derives_the_rac_number_without_a_marker():
    _, added = pm_align.tunnel_plan(TOML4, None)
    assert added == ["AC0G_ND-vm-station", "AC0G_ND-vm-gmag"]


def _api(user, names, status="running"):
    return json.dumps({"tcp": [{"name": f"{user}.{n}", "status": status} for n in names]})


def test_proxies_running_needs_every_declared_name():
    names = ["A-vm-ssh", "A-vm-web"]
    assert pm_align.proxies_running(_api("u", names), "u", names)
    assert not pm_align.proxies_running(_api("u", names[:1]), "u", names)
    assert not pm_align.proxies_running(_api("u", names, "start error"), "u", names)


def _tunnel_root(tmp_path, text=TOML4):
    (tmp_path / "etc/sigmond").mkdir(parents=True)
    (tmp_path / "etc/sigmond-appliance").mkdir(parents=True)
    (tmp_path / "etc/sigmond/frpc-host.toml").write_text(text)
    (tmp_path / "etc/sigmond-appliance/rac-number").write_text("505\n")
    return tmp_path


def test_tunnel_apply_installs_when_every_proxy_comes_up(tmp_path):
    root = _tunnel_root(tmp_path)
    calls = []
    names = pm_align.declared_proxies(pm_align.tunnel_plan(TOML4, "505")[0])
    out = pm_align.tunnel_apply(root, run=_fake_run(calls),
                                fetch_status=lambda: _api("dd986638365fd1d7", names),
                                sleep=lambda s: None)
    assert out["outcome"] == "applied"
    assert "AC0G_ND-vm-gmag" in (root / "etc/sigmond/frpc-host.toml").read_text()
    assert any(c[:2] == ["/usr/local/sbin/frpc", "verify"] for c in calls)
    assert ["systemctl", "restart", "sigmond-rac-host.service"] in calls


def test_tunnel_apply_rolls_back_when_a_proxy_stays_down(tmp_path):
    root = _tunnel_root(tmp_path)
    calls = []
    out = pm_align.tunnel_apply(root, run=_fake_run(calls),
                                fetch_status=lambda: _api("dd986638365fd1d7",
                                                          ["AC0G_ND-vm-ssh", "AC0G_ND-vm-web"]),
                                sleep=lambda s: None, wait_s=10)
    assert out["outcome"] == "rolled-back"
    assert (root / "etc/sigmond/frpc-host.toml").read_text() == TOML4
    assert calls.count(["systemctl", "restart", "sigmond-rac-host.service"]) == 2


_IS_ACTIVE = ["systemctl", "is-active", pm_align.RAC_UNIT]
_OLD_NAMES = ["AC0G_ND-vm-ssh", "AC0G_ND-vm-web"]


def _is_active_run(calls, extra=None):
    """A run() that answers is-active with "active" and defers everything
    else to ``extra`` (default: rc 0, empty output)."""
    def run(argv, **kw):
        calls.append(list(argv))
        if argv[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        if extra is not None:
            return extra(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    return run


def test_tunnel_apply_refuses_a_config_frpc_rejects(tmp_path):
    root = _tunnel_root(tmp_path)
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        if argv[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        rc = 1 if argv[:2] == ["/usr/local/sbin/frpc", "verify"] else 0
        return subprocess.CompletedProcess(argv, rc, "", "bad")
    out = pm_align.tunnel_apply(root, run=run,
                                fetch_status=lambda: _api("dd986638365fd1d7", _OLD_NAMES),
                                sleep=lambda s: None)
    assert out["outcome"] == "failed"
    assert (root / "etc/sigmond/frpc-host.toml").read_text() == TOML4
    assert not any(c[:2] == ["systemctl", "restart"] for c in calls)


def test_tunnel_apply_fails_without_touching_anything_when_no_user_line(tmp_path):
    text = TOML4.replace('user = "dd986638365fd1d7"\n', "")
    root = _tunnel_root(tmp_path, text=text)
    calls = []
    out = pm_align.tunnel_apply(root, run=_fake_run(calls), fetch_status=lambda: "{}",
                                sleep=lambda s: None)
    assert out["outcome"] == "failed"
    assert (root / "etc/sigmond/frpc-host.toml").read_text() == text
    assert not any(c[:2] in (["systemctl", "restart"], [pm_align.FRPC, "verify"]) for c in calls)


def test_tunnel_apply_current_when_every_channel_already_declared(tmp_path):
    full, _ = pm_align.tunnel_plan(TOML4, "505")
    root = _tunnel_root(tmp_path, text=full)
    calls = []
    names = pm_align.declared_proxies(full)
    out = pm_align.tunnel_apply(root, run=_fake_run(calls),
                                fetch_status=lambda: _api("dd986638365fd1d7", names),
                                sleep=lambda s: None)
    assert out == {"outcome": "current", "detail": "every channel already declared"}
    assert (root / "etc/sigmond/frpc-host.toml").read_text() == full
    assert not any(c[:2] == ["systemctl", "restart"] for c in calls)


# --- final review item 1: the two proxy-name parsers must agree ---

def test_proxies_running_with_no_names_is_false():
    assert not pm_align.proxies_running(_api("u", []), "u", [])
    assert not pm_align.proxies_running("{}", "u", [])


def test_tunnel_apply_refuses_when_the_vm_ssh_name_is_missed_by_one_parser(tmp_path):
    # A trailing comment on the vm-ssh name line: with a SHARED regex both
    # declared_proxies and _remote_ports miss it identically (so they don't
    # disagree with each other), but the -vm-ssh name then appears in
    # NEITHER set -- which is exactly the case that must still refuse,
    # since a missed vm-ssh entry means the tunnel's basic identity can't
    # be established.
    text = TOML4.replace('name = "AC0G_ND-vm-ssh"', 'name = "AC0G_ND-vm-ssh"  # trailing comment')
    root = _tunnel_root(tmp_path, text=text)
    calls = []
    out = pm_align.tunnel_apply(root, run=_fake_run(calls), fetch_status=lambda: "{}",
                                sleep=lambda s: None)
    assert out["outcome"] == "failed"
    assert "disagree" in out["detail"] or "vm-ssh" in out["detail"]
    assert (root / "etc/sigmond/frpc-host.toml").read_text() == text
    assert not any(c[:2] == ["systemctl", "restart"] for c in calls)


def test_tunnel_apply_refuses_when_a_proxy_is_declared_with_no_remoteport(tmp_path):
    # declared_proxies sees the name; _remote_ports never records it because
    # no remotePort line follows -- a genuine disagreement between the two
    # parses, not just a shared miss.
    text = TOML4 + '\n[[proxies]]\nname = "AC0G_ND-vm-extra"\ntype = "tcp"\n'
    root = _tunnel_root(tmp_path, text=text)
    calls = []
    out = pm_align.tunnel_apply(root, run=_fake_run(calls), fetch_status=lambda: "{}",
                                sleep=lambda s: None)
    assert out["outcome"] == "failed"
    assert "disagree" in out["detail"]
    assert (root / "etc/sigmond/frpc-host.toml").read_text() == text
    assert not any(c[:2] == ["systemctl", "restart"] for c in calls)


# --- fix round 1: active check, baseline check, guaranteed rollback ---

def test_tunnel_apply_skips_when_the_tunnel_is_off(tmp_path):
    root = _tunnel_root(tmp_path)
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "inactive\n", "")

    def _boom():
        raise AssertionError("fetch_status must not be called when the tunnel is off")

    out = pm_align.tunnel_apply(root, run=run, fetch_status=_boom, sleep=lambda s: None)
    assert out == {"outcome": "skipped", "detail": "tunnel is off — not changed"}
    assert (root / "etc/sigmond/frpc-host.toml").read_text() == TOML4
    assert calls == [_IS_ACTIVE]


def test_tunnel_apply_refuses_when_the_baseline_is_not_up(tmp_path):
    root = _tunnel_root(tmp_path)
    calls = []
    run = _is_active_run(calls)
    # vm-web missing from the fake API response: the existing tunnel isn't
    # fully up before any change is even planned.
    out = pm_align.tunnel_apply(root, run=run,
                                fetch_status=lambda: _api("dd986638365fd1d7", _OLD_NAMES[:1]),
                                sleep=lambda s: None)
    assert out == {"outcome": "failed", "detail": "tunnel not fully up before the change — refused"}
    assert (root / "etc/sigmond/frpc-host.toml").read_text() == TOML4
    assert not any(c[:2] == ["systemctl", "restart"] for c in calls)


def test_tunnel_apply_rolls_back_when_the_first_restart_raises(tmp_path):
    root = _tunnel_root(tmp_path)
    calls = []
    restarts = {"n": 0}

    def extra(argv):
        if argv[:2] == ["systemctl", "restart"]:
            restarts["n"] += 1
            if restarts["n"] == 1:
                raise subprocess.TimeoutExpired(argv, 60)
        return subprocess.CompletedProcess(argv, 0, "", "")

    run = _is_active_run(calls, extra)
    out = pm_align.tunnel_apply(root, run=run,
                                fetch_status=lambda: _api("dd986638365fd1d7", _OLD_NAMES),
                                sleep=lambda s: None)
    assert out["outcome"] != "applied"
    assert "TimeoutExpired" in out["detail"]
    assert (root / "etc/sigmond/frpc-host.toml").read_text() == TOML4
    assert restarts["n"] == 2                     # the failed attempt, then the rollback restart


def test_tunnel_apply_rolls_back_when_fetch_status_raises_after_install(tmp_path):
    root = _tunnel_root(tmp_path)
    calls = []
    seen = {"n": 0}

    def fetch_status():
        seen["n"] += 1
        if seen["n"] == 1:                        # the baseline check, before any change
            return _api("dd986638365fd1d7", _OLD_NAMES)
        raise http.client.IncompleteRead(b"")

    run = _is_active_run(calls)
    out = pm_align.tunnel_apply(root, run=run, fetch_status=fetch_status, sleep=lambda s: None)
    assert out["outcome"] != "applied"
    assert "IncompleteRead" in out["detail"]
    assert (root / "etc/sigmond/frpc-host.toml").read_text() == TOML4


def test_rollback_recovers_on_the_third_retry_attempt(tmp_path):
    # frpc's loginFailExit defaults true: the first login after a restart
    # can fail even though the config is fine, so a single rollback
    # restart+wait isn't guaranteed to bring frpc back. This exercises
    # _rollback directly.
    path = tmp_path / "frpc-host.toml"
    path.write_text("new-config\n")
    backup = tmp_path / "frpc-host.toml.pm-align-prev"
    backup.write_text("old-config\n")
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    seen = {"n": 0}

    def fetch_status():
        seen["n"] += 1
        if seen["n"] == 3:                 # the 3rd rollback attempt
            return _api("u", ["A"])
        return "{}"

    out = pm_align._rollback(path, backup, ["A"], "u", run, fetch_status, lambda s: None, 0,
                             "reason")
    assert out["outcome"] == "rolled-back"
    assert path.read_text() == "old-config\n"
    assert calls.count(["systemctl", "restart", pm_align.RAC_UNIT]) == 3


def test_rollback_gives_up_after_1_plus_3_restarts(tmp_path):
    path = tmp_path / "frpc-host.toml"
    path.write_text("new-config\n")
    backup = tmp_path / "frpc-host.toml.pm-align-prev"
    backup.write_text("old-config\n")
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    out = pm_align._rollback(path, backup, ["A"], "u", run, lambda: "{}", lambda s: None, 0,
                             "reason")
    assert out["outcome"] == "failed"
    assert "needs hands" in out["detail"]
    assert path.read_text() == "old-config\n"
    assert calls.count(["systemctl", "restart", pm_align.RAC_UNIT]) == 4


def test_rollback_retry_restart_raising_stops_immediately(tmp_path):
    """Each retry attempt is guarded like the first -- an exception on a
    LATER restart attempt must still return "failed" immediately, not loop
    past it."""
    path = tmp_path / "frpc-host.toml"
    path.write_text("new-config\n")
    backup = tmp_path / "frpc-host.toml.pm-align-prev"
    backup.write_text("old-config\n")
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        if len(calls) == 2:                # the first retry's restart
            raise subprocess.TimeoutExpired(argv, 60)
        return subprocess.CompletedProcess(argv, 0, "", "")

    out = pm_align._rollback(path, backup, ["A"], "u", run, lambda: "{}", lambda s: None, 0,
                             "reason")
    assert out["outcome"] == "failed"
    assert "TimeoutExpired" in out["detail"]
    assert calls.count(["systemctl", "restart", pm_align.RAC_UNIT]) == 2


def test_write_private_narrows_a_pre_existing_wider_mode_file(tmp_path):
    """os.open's mode argument only applies when it CREATES the file -- a
    pre-existing file at a wider mode (e.g. left over at 0o644 from some
    other writer) would otherwise keep that mode through _write_private.
    The explicit fchmod after os.open narrows it to 0o600 either way."""
    p = tmp_path / "frpc-host.toml"
    p.write_text("old")
    os.chmod(p, 0o644)
    pm_align._write_private(p, "new")
    assert oct(p.stat().st_mode & 0o777) == oct(0o600)
    assert p.read_text() == "new"


def test_tunnel_apply_writes_the_candidate_and_backup_private_from_the_start(tmp_path, monkeypatch):
    """The frpc config carries an auth token (``user``); a create-then-chmod
    pattern leaves the file briefly at the process's default (umask-masked)
    mode. Both files must be created 0o600 via os.open, so no separate
    os.chmod call on either is needed -- catch a regression by making any
    os.chmod call raise."""
    root = _tunnel_root(tmp_path)

    def _no_chmod(path, mode):
        raise AssertionError(f"unexpected os.chmod({path!r}, {mode!r})")

    monkeypatch.setattr(pm_align.os, "chmod", _no_chmod)
    calls = []
    names = pm_align.declared_proxies(pm_align.tunnel_plan(TOML4, "505")[0])
    out = pm_align.tunnel_apply(root, run=_fake_run(calls),
                                fetch_status=lambda: _api("dd986638365fd1d7", names),
                                sleep=lambda s: None)
    assert out["outcome"] == "applied"
    p = root / "etc/sigmond/frpc-host.toml"
    assert oct(p.stat().st_mode & 0o777) == oct(0o600)
    backup = root / "etc/sigmond/frpc-host.toml.pm-align-prev"
    assert oct(backup.stat().st_mode & 0o777) == oct(0o600)


def test_tunnel_apply_rolls_back_when_the_install_rename_itself_raises(tmp_path, monkeypatch):
    """The install (os.replace(cand, path)) must itself be inside the
    guarded region — an exception right there must still produce a result
    dict, not propagate, and must leave no .pm-align-new litter behind."""
    root = _tunnel_root(tmp_path)
    calls = []
    run = _is_active_run(calls)
    real_replace = os.replace

    def flaky_replace(src, dst):
        if str(src).endswith(".pm-align-new"):
            raise OSError("simulated rename failure")
        return real_replace(src, dst)

    monkeypatch.setattr(pm_align.os, "replace", flaky_replace)
    out = pm_align.tunnel_apply(root, run=run,
                                fetch_status=lambda: _api("dd986638365fd1d7", _OLD_NAMES),
                                sleep=lambda s: None)
    assert out["outcome"] != "applied"
    assert (root / "etc/sigmond/frpc-host.toml").read_text() == TOML4
    assert not list(root.glob("etc/sigmond/frpc-host.toml.pm-align-new"))


# ---------------------------------------------------------------------------
# Task 5: the host heartbeat

_HB_FULL_CONFIG = (
    'station = "b4-pm"\n'
    "vmid = 100\n"
    'dest_host = "h.example"\n'
    "dest_port = 1234\n"
    'sftp_user = "hamsci-hb"\n'
    'remote_path = "incoming"\n'
    "interval_sec = 300\n"
    'key_path = "/etc/pm-heartbeat/id_ed25519"\n'
    "expect_cat = false\n"
)


def _src_with_heartbeat():
    return pm_align.Sources(FB_FULL, pm_align.render_wizard(WIZ, 100),
                            {n: f"#{n}\n".encode() for n in pm_align.HB_FILES})


def test_heartbeat_absent_and_not_opted_in_is_skipped(tmp_path):
    assert pm_align.heartbeat_args(tmp_path, opt_in=False, vmid=100, dest=None) is None


def test_heartbeat_opt_in_derives_station_from_the_configured_reporter(tmp_path):
    (tmp_path / "etc/sigmond-appliance").mkdir(parents=True)
    (tmp_path / "etc/sigmond-appliance/.configured").write_text("AC0G/ND EN16ov 2026-09-02\n")
    args = pm_align.heartbeat_args(tmp_path, opt_in=True, vmid=100, dest=None)
    assert args == ["--station", "AC0G/ND-pm", "--vmid", "100",
                    "--dest-host", "wd30.wsprdaemon.org", "--dest-port", "38222"]


def test_heartbeat_opt_in_uses_an_explicit_dest(tmp_path):
    (tmp_path / "etc/sigmond-appliance").mkdir(parents=True)
    (tmp_path / "etc/sigmond-appliance/.configured").write_text("AC0G/ND EN16ov 2026-09-02\n")
    args = pm_align.heartbeat_args(tmp_path, opt_in=True, vmid=100, dest="h.example:9999")
    assert args == ["--station", "AC0G/ND-pm", "--vmid", "100",
                    "--dest-host", "h.example", "--dest-port", "9999"]


def test_heartbeat_opt_in_without_a_reporter_refuses(tmp_path):
    with pytest.raises(pm_align.PmAlignError, match="reporter"):
        pm_align.heartbeat_args(tmp_path, opt_in=True, vmid=100, dest=None)


def test_existing_heartbeat_config_is_rerun_with_its_own_values(tmp_path):
    (tmp_path / "etc/pm-heartbeat").mkdir(parents=True)
    (tmp_path / "etc/pm-heartbeat/config.toml").write_text(_HB_FULL_CONFIG)
    args = pm_align.heartbeat_args(tmp_path, opt_in=False, vmid=100, dest=None)
    assert args == ["--station", "b4-pm", "--vmid", "100",
                    "--dest-host", "h.example", "--dest-port", "1234"]


def test_existing_heartbeat_config_is_rerun_even_without_opt_in_or_dest_being_honored(tmp_path):
    """A configured host is re-run with ITS OWN values -- opt_in/dest passed
    by the caller are for the never-configured path only."""
    (tmp_path / "etc/pm-heartbeat").mkdir(parents=True)
    (tmp_path / "etc/pm-heartbeat/config.toml").write_text(_HB_FULL_CONFIG)
    args = pm_align.heartbeat_args(tmp_path, opt_in=True, vmid=999, dest="other:1")
    assert args == ["--station", "b4-pm", "--vmid", "100",
                    "--dest-host", "h.example", "--dest-port", "1234"]


def test_heartbeat_args_passes_expect_cat_when_the_config_wants_it(tmp_path):
    (tmp_path / "etc/pm-heartbeat").mkdir(parents=True)
    (tmp_path / "etc/pm-heartbeat/config.toml").write_text(
        _HB_FULL_CONFIG.replace("expect_cat = false", "expect_cat = true"))
    args = pm_align.heartbeat_args(tmp_path, opt_in=False, vmid=100, dest=None)
    assert args[-1] == "--expect-cat"


def test_heartbeat_args_omits_expect_cat_when_the_config_says_false(tmp_path):
    (tmp_path / "etc/pm-heartbeat").mkdir(parents=True)
    (tmp_path / "etc/pm-heartbeat/config.toml").write_text(_HB_FULL_CONFIG)
    args = pm_align.heartbeat_args(tmp_path, opt_in=False, vmid=100, dest=None)
    assert "--expect-cat" not in args


def test_heartbeat_args_refuses_a_non_default_key_path(tmp_path):
    (tmp_path / "etc/pm-heartbeat").mkdir(parents=True)
    (tmp_path / "etc/pm-heartbeat/config.toml").write_text(
        _HB_FULL_CONFIG.replace('key_path = "/etc/pm-heartbeat/id_ed25519"',
                                'key_path = "/root/other-key"'))
    with pytest.raises(pm_align.PmAlignError, match="config.toml") as exc_info:
        pm_align.heartbeat_args(tmp_path, opt_in=False, vmid=100, dest=None)
    assert "/root/other-key" in str(exc_info.value)


def test_heartbeat_args_accepts_a_missing_key_path_key_as_the_default(tmp_path):
    (tmp_path / "etc/pm-heartbeat").mkdir(parents=True)
    text = "\n".join(l for l in _HB_FULL_CONFIG.splitlines() if not l.startswith("key_path"))
    (tmp_path / "etc/pm-heartbeat/config.toml").write_text(text)
    args = pm_align.heartbeat_args(tmp_path, opt_in=False, vmid=100, dest=None)
    assert args[:2] == ["--station", "b4-pm"]


def test_heartbeat_args_on_a_config_missing_a_required_key_names_the_file(tmp_path):
    (tmp_path / "etc/pm-heartbeat").mkdir(parents=True)
    (tmp_path / "etc/pm-heartbeat/config.toml").write_text('station = "b4-pm"\nvmid = 100\n')
    with pytest.raises(pm_align.PmAlignError, match="config.toml") as exc_info:
        pm_align.heartbeat_args(tmp_path, opt_in=False, vmid=100, dest=None)
    assert "dest_host" in str(exc_info.value)


def test_heartbeat_step_runs_the_release_setup_from_a_staged_dir(tmp_path):
    src = pm_align.Sources("", "", {n: f"#{n}\n".encode() for n in pm_align.PROXMOX_FILES})
    calls = []
    out = pm_align.heartbeat_step(tmp_path, src, ["--station", "x-pm"], run=_fake_run(calls),
                                  tmpdir=tmp_path / "stage")
    assert out == "set up"
    assert calls[0][0] == str(tmp_path / "stage" / "pm-heartbeat-setup.sh")
    assert (tmp_path / "stage" / "pm-heartbeat.py").read_bytes() == b"#pm-heartbeat.py\n"


def test_heartbeat_step_reports_re_run_when_the_config_already_existed(tmp_path):
    (tmp_path / "etc/pm-heartbeat").mkdir(parents=True)
    (tmp_path / "etc/pm-heartbeat/config.toml").write_text(_HB_FULL_CONFIG)
    out = pm_align.heartbeat_step(tmp_path, _src_with_heartbeat(), ["--station", "b4-pm"],
                                  run=_fake_run([]), tmpdir=tmp_path / "stage")
    assert out == "re-run"


def test_heartbeat_step_skips_when_the_release_carries_no_heartbeat_files(tmp_path):
    src = pm_align.Sources("", "", {})
    out = pm_align.heartbeat_step(tmp_path, src, ["--station", "x-pm"], run=_fake_run([]),
                                  tmpdir=tmp_path / "stage")
    assert out == "skipped: the release carries no pm-heartbeat.py"


def test_heartbeat_step_reports_failed_with_the_setup_scripts_last_line(tmp_path):
    calls = []
    run = _fake_run(calls, rc=1, stdout="step one\nno destination — pass --cohort dasi2|public\n")
    out = pm_align.heartbeat_step(tmp_path, _src_with_heartbeat(), ["--station", "x-pm"],
                                  run=run, tmpdir=tmp_path / "stage")
    assert out == "failed: no destination — pass --cohort dasi2|public"


# --- dry run / --apply / --tunnel-step / --status wiring ---

def _src_with_pm_align():
    return pm_align.Sources(FB_FULL, pm_align.render_wizard(WIZ, 100),
                            {"pm-heartbeat.py": b"hb", "pm-align.py": b"#!/usr/bin/env python3\n"})


def _mock_release(monkeypatch, src_factory=_src):
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.53", "a" * 40, "d" * 40, "f" * 64, ""))
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: src_factory())


def test_main_dry_run_reports_no_host_tunnel_configured(tmp_path, monkeypatch, capsys):
    _mock_release(monkeypatch)
    pm_align.main([], root=tmp_path)
    assert "tunnel: no host tunnel configured" in capsys.readouterr().out


def test_main_dry_run_reports_would_add_and_counts_as_pending(tmp_path, monkeypatch, capsys):
    _mock_release(monkeypatch)
    _tunnel_root(tmp_path)
    rc = pm_align.main([], root=tmp_path)
    out = capsys.readouterr().out
    assert "tunnel: would add AC0G_ND-vm-station, AC0G_ND-vm-gmag" in out
    assert rc == 1


def test_main_dry_run_reports_every_channel_declared(tmp_path, monkeypatch, capsys):
    _mock_release(monkeypatch)
    full, _ = pm_align.tunnel_plan(TOML4, "505")
    _tunnel_root(tmp_path, text=full)
    pm_align.main([], root=tmp_path)
    assert "tunnel: every channel declared" in capsys.readouterr().out


def test_main_dry_run_reports_refused_on_rac_disagreement(tmp_path, monkeypatch, capsys):
    _mock_release(monkeypatch)
    _tunnel_root(tmp_path)
    (tmp_path / "etc/sigmond-appliance/rac-number").write_text("506\n")
    rc = pm_align.main([], root=tmp_path)
    out = capsys.readouterr().out
    assert "tunnel: REFUSED — " in out and "RAC" in out
    assert rc == 1


def test_main_apply_skips_tunnel_when_not_configured(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    calls = []
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run(calls))
    assert rc == 0
    assert "tunnel: no host tunnel configured — skipped" in capsys.readouterr().out
    assert not any(c[0] == "systemd-run" for c in calls)


def test_main_apply_prints_refused_and_still_succeeds_on_rac_disagreement(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    _tunnel_root(tmp_path)
    (tmp_path / "etc/sigmond-appliance/rac-number").write_text("506\n")
    calls = []
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run(calls))
    assert rc == 0
    out = capsys.readouterr().out
    assert "tunnel: REFUSED — " in out and "RAC" in out
    assert not any(c[0] == "systemd-run" for c in calls)


def test_main_apply_launches_the_detached_tunnel_step_via_the_real_script(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)          # _src(): no pm-align.py among proxmox sources
    _tunnel_root(tmp_path)
    calls = []
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run(calls))
    assert rc == 0
    launch = next(c for c in calls if c[0] == "systemd-run")
    assert launch[:5] == ["systemd-run", "--unit", "pm-align-tunnel", "--collect", "--quiet"]
    assert launch[5] == sys.executable
    assert launch[6] == str((PROXMOX / "pm-align.py").resolve())
    assert launch[7] == "--tunnel-step"
    out = capsys.readouterr().out
    assert "tunnel: adding AC0G_ND-vm-station, AC0G_ND-vm-gmag" in out
    assert "pm-align --status" in out


def test_main_apply_reconnect_message_mentions_rollback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    _tunnel_root(tmp_path)
    pm_align.main(["--apply"], root=tmp_path, run=_fake_run([]))
    out = capsys.readouterr().out
    assert "~5 minutes (longer if it rolls back)" in out


def test_main_apply_moves_a_stale_tunnel_result_aside_before_launching(tmp_path, monkeypatch):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    root = _tunnel_root(tmp_path)
    tres = root / "var/lib/pm-align/tunnel-result.json"
    tres.parent.mkdir(parents=True)
    tres.write_text('{"outcome": "applied", "at": "stale"}\n')
    rc = pm_align.main(["--apply"], root=root, run=_fake_run([]))
    assert rc == 0
    assert not tres.exists()
    prev = root / "var/lib/pm-align/tunnel-result.json.prev"
    assert '"outcome": "applied"' in prev.read_text()


def test_main_apply_continues_launch_when_moving_the_stale_result_raises(tmp_path, monkeypatch,
                                                                          capsys):
    """_apply_tunnel's os.replace(TUNNEL_RESULT -> .prev) is a courtesy move
    so a stale result from a previous launch is never read as this run's --
    an OSError there (e.g. a permissions hiccup) must warn and continue on
    to launch the tunnel step, never abort --apply."""
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    root = _tunnel_root(tmp_path)
    tres = root / "var/lib/pm-align/tunnel-result.json"
    tres.parent.mkdir(parents=True)
    tres.write_text('{"outcome": "applied", "at": "stale"}\n')

    real_replace = os.replace

    def flaky_replace(src, dst):
        if str(dst).endswith(".prev"):
            raise OSError("permission denied")
        return real_replace(src, dst)
    monkeypatch.setattr(pm_align.os, "replace", flaky_replace)

    calls = []
    rc = pm_align.main(["--apply"], root=root, run=_fake_run(calls))
    assert rc == 0
    out = capsys.readouterr().out
    assert "WARN" in out
    assert tres.exists()                              # never moved aside
    assert any(c[0] == "systemd-run" for c in calls)   # launch still happened


def test_main_apply_does_not_touch_tunnel_result_when_nothing_to_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    root = tmp_path
    tres = root / "var/lib/pm-align/tunnel-result.json"
    tres.parent.mkdir(parents=True)
    tres.write_text('{"outcome": "applied", "at": "stale"}\n')
    rc = pm_align.main(["--apply"], root=root, run=_fake_run([]))
    assert rc == 0
    assert tres.exists()
    assert not (root / "var/lib/pm-align/tunnel-result.json.prev").exists()


def test_status_mentions_the_detached_step_log(tmp_path, capsys):
    pm_align.main(["--status"], root=tmp_path)
    out = capsys.readouterr().out
    assert "journalctl -u pm-align-tunnel" in out


def test_main_apply_launches_the_detached_tunnel_step_via_the_installed_script(tmp_path, monkeypatch):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch, src_factory=_src_with_pm_align)
    _tunnel_root(tmp_path)
    calls = []
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run(calls))
    assert rc == 0
    launch = next(c for c in calls if c[0] == "systemd-run")
    assert launch[6] == str(tmp_path / "usr/local/sbin/pm-align")


def test_main_apply_returns_1_when_systemd_run_itself_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    _tunnel_root(tmp_path)
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        rc = 1 if argv[0] == "systemd-run" else 0
        return subprocess.CompletedProcess(argv, rc, "", "no systemd")
    rc = pm_align.main(["--apply"], root=tmp_path, run=run)
    assert rc == 1
    assert "no systemd" in capsys.readouterr().out


def test_tunnel_step_writes_the_result_and_returns_matching_exit_code(tmp_path, monkeypatch):
    root = _tunnel_root(tmp_path)
    monkeypatch.setattr(pm_align, "tunnel_apply", lambda root, run, **kw:
                        {"outcome": "applied", "detail": "added everything"})
    rc = pm_align.main(["--tunnel-step"], root=root, run=_fake_run([]))
    assert rc == 0
    doc = json.loads((root / "var/lib/pm-align/tunnel-result.json").read_text())
    assert doc["outcome"] == "applied" and doc["detail"] == "added everything" and "at" in doc

    monkeypatch.setattr(pm_align, "tunnel_apply", lambda root, run, **kw:
                        {"outcome": "rolled-back", "detail": "x did not come up"})
    rc = pm_align.main(["--tunnel-step"], root=root, run=_fake_run([]))
    assert rc == 1

    monkeypatch.setattr(pm_align, "tunnel_apply", lambda root, run, **kw:
                        {"outcome": "skipped", "detail": "tunnel is off — not changed"})
    rc = pm_align.main(["--tunnel-step"], root=root, run=_fake_run([]))
    assert rc == 0


def test_tunnel_step_writes_a_failed_result_when_tunnel_apply_raises(tmp_path, monkeypatch):
    root = _tunnel_root(tmp_path)

    def _raise(root, run, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(pm_align, "tunnel_apply", _raise)
    rc = pm_align.main(["--tunnel-step"], root=root, run=_fake_run([]))
    assert rc == 1
    doc = json.loads((root / "var/lib/pm-align/tunnel-result.json").read_text())
    assert doc["outcome"] == "failed"
    assert "boom" in doc["detail"]


def test_status_prints_tunnel_result_and_record_or_none_yet(tmp_path, capsys):
    rc = pm_align.main(["--status"], root=tmp_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "none yet" in out

    rel = pm_align.Release("v3.53", "a" * 40, "d" * 40, "f" * 64, "")
    pm_align.write_record(tmp_path, rel, ["/x"], "2026-09-25T12:00:00Z")
    pm_align.write_tunnel_result(tmp_path, {"outcome": "current", "detail": "d"},
                                 "2026-09-25T12:01:00Z")
    pm_align.main(["--status"], root=tmp_path)
    out = capsys.readouterr().out
    assert '"release": "v3.53"' in out
    assert '"outcome": "current"' in out


# --- Task 5: --heartbeat / --heartbeat-dest wiring ---

def test_apply_refuses_a_heartbeat_dest_with_no_colon_before_anything_happens(tmp_path, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("fetch_release must not run")
    monkeypatch.setattr(pm_align, "fetch_release", _boom)
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    rc = pm_align.main(["--apply", "--heartbeat", "--heartbeat-dest", "noport"], root=tmp_path)
    assert rc == 2
    assert not (tmp_path / "etc/sigmond-appliance/host-aligned.json").exists()


def test_apply_refuses_a_heartbeat_dest_with_a_non_numeric_port(tmp_path, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("fetch_release must not run")
    monkeypatch.setattr(pm_align, "fetch_release", _boom)
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    rc = pm_align.main(["--apply", "--heartbeat", "--heartbeat-dest", "h.example:abc"], root=tmp_path)
    assert rc == 2
    assert not (tmp_path / "etc/sigmond-appliance/host-aligned.json").exists()


def _hb_setup_run(calls, hb_rc=0, hb_stdout="", hb_stderr="", side_effect=None):
    def run(argv, **kw):
        calls.append(list(argv))
        if argv[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        if argv[0].endswith("pm-heartbeat-setup.sh"):
            if side_effect:
                side_effect()
            return subprocess.CompletedProcess(argv, hb_rc, hb_stdout, hb_stderr)
        return subprocess.CompletedProcess(argv, 0, "", "")
    return run


def _configured(tmp_path, reporter="AC0G/ND EN16ov 2026-09-02\n"):
    (tmp_path / "etc/sigmond-appliance").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/sigmond-appliance/.configured").write_text(reporter)


def test_main_apply_prints_not_set_up_when_never_configured_and_not_opted_in(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run([]))
    assert rc == 0
    assert "heartbeat: not set up — add --heartbeat to set it up" in capsys.readouterr().out


def test_main_apply_sets_up_heartbeat_when_opted_in_and_prints_the_pubkey(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch, src_factory=_src_with_heartbeat)
    _configured(tmp_path)
    calls = []

    def _write_pubkey():
        d = tmp_path / "etc/pm-heartbeat"
        d.mkdir(parents=True, exist_ok=True)
        (d / "id_ed25519.pub").write_text("ssh-ed25519 AAAAfake pm-heartbeat@AC0G/ND-pm\n")

    rc = pm_align.main(["--apply", "--heartbeat"], root=tmp_path,
                       run=_hb_setup_run(calls, side_effect=_write_pubkey))
    assert rc == 0
    out = capsys.readouterr().out
    assert "heartbeat: set up" in out
    assert "ssh-ed25519 AAAAfake" in out
    assert "authorize-stations.sh" in out
    setup_call = next(c for c in calls if c[0].endswith("pm-heartbeat-setup.sh"))
    assert setup_call[1:] == ["--station", "AC0G/ND-pm", "--vmid", "100",
                              "--dest-host", "wd30.wsprdaemon.org", "--dest-port", "38222"]


def test_main_apply_heartbeat_orders_after_record_and_before_tunnel(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch, src_factory=_src_with_heartbeat)
    _tunnel_root(tmp_path)
    _configured(tmp_path)
    rc = pm_align.main(["--apply", "--heartbeat"], root=tmp_path, run=_hb_setup_run([]))
    assert rc == 0
    out = capsys.readouterr().out
    assert out.index("record:") < out.index("heartbeat:") < out.index("tunnel:")


def test_main_apply_removes_the_staged_heartbeat_tmpdir_after_running(tmp_path, monkeypatch):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch, src_factory=_src_with_heartbeat)
    _configured(tmp_path)
    staged = tmp_path / "hb-stage"

    def _fake_mkdtemp(prefix=""):
        return str(staged)
    monkeypatch.setattr(pm_align.tempfile, "mkdtemp", _fake_mkdtemp)
    rc = pm_align.main(["--apply", "--heartbeat"], root=tmp_path, run=_hb_setup_run([]))
    assert rc == 0
    assert not staged.exists()


def test_main_apply_heartbeat_skip_is_informational_and_does_not_affect_rc(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)                    # default _src(): only pm-heartbeat.py present
    _configured(tmp_path)
    rc = pm_align.main(["--apply", "--heartbeat"], root=tmp_path, run=_fake_run([]))
    assert rc == 0
    assert "heartbeat: skipped: the release carries no pm-heartbeat.service" in capsys.readouterr().out


def test_main_apply_heartbeat_failure_warns_sets_rc_1_but_still_runs_the_tunnel(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch, src_factory=_src_with_heartbeat)
    _tunnel_root(tmp_path)
    _configured(tmp_path)
    calls = []
    rc = pm_align.main(["--apply", "--heartbeat"], root=tmp_path,
                       run=_hb_setup_run(calls, hb_rc=1, hb_stdout="no destination\n"))
    assert rc == 1
    out = capsys.readouterr().out
    assert "heartbeat: WARN" in out and "no destination" in out
    assert any(c[0] == "systemd-run" for c in calls)      # the tunnel step still launched


def test_main_apply_heartbeat_refuses_when_opted_in_without_a_configured_reporter(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch, src_factory=_src_with_heartbeat)
    calls = []
    rc = pm_align.main(["--apply", "--heartbeat"], root=tmp_path, run=_fake_run(calls))
    assert rc == 1
    out = capsys.readouterr().out
    assert "heartbeat: REFUSED —" in out and "reporter" in out
    # the refusal must not block the (unconfigured, so skipped) tunnel step
    assert "tunnel: no host tunnel configured — skipped" in out


def test_main_apply_heartbeat_refuses_when_the_existing_config_is_missing_a_key(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch, src_factory=_src_with_heartbeat)
    (tmp_path / "etc/pm-heartbeat").mkdir(parents=True)
    (tmp_path / "etc/pm-heartbeat/config.toml").write_text('station = "b4-pm"\nvmid = 100\n')
    calls = []
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run(calls))
    assert rc == 1
    out = capsys.readouterr().out
    assert "heartbeat: REFUSED —" in out
    assert "config.toml" in out and "dest_host" in out
    assert "tunnel: no host tunnel configured — skipped" in out


def test_main_dry_run_reports_heartbeat_not_set_up(tmp_path, monkeypatch, capsys):
    _mock_release(monkeypatch)
    pm_align.main([], root=tmp_path)
    assert "heartbeat: not set up — add --heartbeat to set it up" in capsys.readouterr().out


def test_main_dry_run_reports_heartbeat_configured(tmp_path, monkeypatch, capsys):
    _mock_release(monkeypatch)
    (tmp_path / "etc/pm-heartbeat").mkdir(parents=True)
    (tmp_path / "etc/pm-heartbeat/config.toml").write_text('station = "b4-pm"\n')
    pm_align.main([], root=tmp_path)
    assert "heartbeat: configured (re-run on --apply)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Task 6: push the host record into the VM

def test_push_record_writes_the_same_path_in_the_vm(tmp_path):
    rec = tmp_path / "host-aligned.json"
    rec.write_text('{"release": "v3.53"}\n')
    calls = []
    assert pm_align.push_record(rec, 100, run=_fake_run(calls, stdout='{"exitcode": 0}'))
    argv = calls[0]
    assert argv[:4] == ["qm", "guest", "exec", "100"]
    assert "/etc/sigmond-appliance/host-aligned.json" in argv[-1]
    assert base64.b64encode(rec.read_bytes()).decode() in argv[-1]


def test_push_record_returns_false_on_a_nonzero_guest_exitcode(tmp_path):
    rec = tmp_path / "host-aligned.json"
    rec.write_text('{"release": "v3.53"}\n')
    calls = []
    assert not pm_align.push_record(rec, 100, run=_fake_run(calls, stdout='{"exitcode": 1}'))


def test_main_apply_pushes_the_record_into_the_vm(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    calls = []
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run(calls, stdout='{"exitcode": 0}'))
    assert rc == 0
    qm_call = next(c for c in calls if c[:3] == ["qm", "guest", "exec"])
    assert qm_call[3] == "100"
    out = capsys.readouterr().out
    assert "recorded (VM copy: yes)" in out


def test_main_apply_push_failure_is_informational_and_does_not_affect_rc(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        if argv[:3] == ["qm", "guest", "exec"]:
            raise subprocess.TimeoutExpired(argv, 30)
        if argv[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    rc = pm_align.main(["--apply"], root=tmp_path, run=run)
    assert rc == 0
    out = capsys.readouterr().out
    assert "recorded (VM copy: no — " in out
    assert "TimeoutExpired" in out


def test_main_apply_pushes_even_when_nothing_new_was_written(tmp_path, monkeypatch, capsys):
    """The record already exists and every file is current -- still push
    (the VM may have been reinstalled since the last alignment)."""
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    pm_align.main(["--apply"], root=tmp_path, run=_fake_run([], stdout='{"exitcode": 0}'))
    calls = []
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run(calls, stdout='{"exitcode": 0}'))
    assert rc == 0
    assert any(c[:3] == ["qm", "guest", "exec"] for c in calls)
    out = capsys.readouterr().out
    assert "recorded (VM copy: yes)" in out


def test_main_apply_record_push_orders_after_record_and_before_heartbeat(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run([], stdout='{"exitcode": 0}'))
    assert rc == 0
    out = capsys.readouterr().out
    assert out.index("record:") < out.index("recorded (VM copy:") < out.index("heartbeat:")


# ---------------------------------------------------------------------------
# final review item 3: the record's `tunnel` field, and the --tunnel-step merge

def _record_tunnel(root):
    return json.loads((root / "etc/sigmond-appliance/host-aligned.json").read_text())["tunnel"]


def test_apply_record_tunnel_is_n_a_when_no_host_tunnel(tmp_path, monkeypatch):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run([]))
    assert rc == 0
    assert _record_tunnel(tmp_path) == "n/a"


def test_apply_record_tunnel_is_n_a_when_every_channel_declared(tmp_path, monkeypatch):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    full, _ = pm_align.tunnel_plan(TOML4, "505")
    _tunnel_root(tmp_path, text=full)
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run([]))
    assert rc == 0
    assert _record_tunnel(tmp_path) == "n/a"


def test_apply_record_tunnel_is_pending_when_a_channel_will_be_added(tmp_path, monkeypatch):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    _tunnel_root(tmp_path)
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run([]))
    assert rc == 0
    assert _record_tunnel(tmp_path) == "pending"


def test_apply_record_tunnel_is_refused_on_a_rac_disagreement(tmp_path, monkeypatch):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    _tunnel_root(tmp_path)
    (tmp_path / "etc/sigmond-appliance/rac-number").write_text("506\n")
    rc = pm_align.main(["--apply"], root=tmp_path, run=_fake_run([]))
    assert rc == 0
    assert _record_tunnel(tmp_path) == "refused"


def test_apply_record_tunnel_becomes_launch_failed_when_systemd_run_fails(tmp_path, monkeypatch):
    """write_record stamps "pending" before the detached step is launched
    (final review item 3). When systemd-run itself fails, that launch
    never happened -- leaving "pending" on the record would misreport a
    launch in flight forever. --apply must rewrite it to "launch-failed"."""
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    _mock_release(monkeypatch)
    _tunnel_root(tmp_path)

    def run(argv, **kw):
        rc = 1 if argv[0] == "systemd-run" else 0
        return subprocess.CompletedProcess(argv, rc, "", "no systemd")
    rc = pm_align.main(["--apply"], root=tmp_path, run=run)
    assert rc == 1
    assert _record_tunnel(tmp_path) == "launch-failed"


def test_tunnel_step_merges_the_outcome_into_the_existing_record_and_repushes(tmp_path, monkeypatch):
    root = _tunnel_root(tmp_path)
    (root / "etc/systemd/system").mkdir(parents=True)
    (root / "etc/systemd/system/sigmond-vm-ssh-relay@.service").write_text(
        "Environment=SIGMOND_VMID=100\n")
    rel = pm_align.Release("v3.53", "a" * 40, "d" * 40, "f" * 64, "")
    pm_align.write_record(root, rel, ["/x"], "2026-09-25T12:00:00Z", tunnel="pending")
    monkeypatch.setattr(pm_align, "tunnel_apply", lambda root, run, **kw:
                        {"outcome": "applied", "detail": "added everything"})
    calls = []
    rc = pm_align.main(["--tunnel-step"], root=root, run=_fake_run(calls, stdout='{"exitcode": 0}'))
    assert rc == 0
    assert _record_tunnel(root) == "applied"
    qm_call = next(c for c in calls if c[:3] == ["qm", "guest", "exec"])
    assert qm_call[3] == "100"


def test_tunnel_step_merge_is_a_no_op_when_there_is_no_existing_record(tmp_path, monkeypatch):
    root = _tunnel_root(tmp_path)
    monkeypatch.setattr(pm_align, "tunnel_apply", lambda root, run, **kw:
                        {"outcome": "applied", "detail": "added everything"})
    calls = []
    rc = pm_align.main(["--tunnel-step"], root=root, run=_fake_run(calls))
    assert rc == 0
    assert not (root / "etc/sigmond-appliance/host-aligned.json").exists()
    assert not any(c[:3] == ["qm", "guest", "exec"] for c in calls)


def test_tunnel_step_merge_push_failure_is_informational(tmp_path, monkeypatch):
    root = _tunnel_root(tmp_path)
    rel = pm_align.Release("v3.53", "a" * 40, "d" * 40, "f" * 64, "")
    pm_align.write_record(root, rel, ["/x"], "2026-09-25T12:00:00Z", tunnel="pending")
    monkeypatch.setattr(pm_align, "tunnel_apply", lambda root, run, **kw:
                        {"outcome": "applied", "detail": "added everything"})

    def run(argv, **kw):
        if argv[:3] == ["qm", "guest", "exec"]:
            raise subprocess.TimeoutExpired(argv, 30)
        return subprocess.CompletedProcess(argv, 0, "", "")

    rc = pm_align.main(["--tunnel-step"], root=root, run=run)
    assert rc == 0
    assert _record_tunnel(root) == "applied"       # the local merge still landed


def test_tunnel_step_merge_local_write_failure_never_crashes_out_from_under_the_outcome(
        tmp_path, monkeypatch, capsys):
    """write_tunnel_result has already landed the real outcome by the time
    _merge_tunnel_result runs (main() writes TUNNEL_RESULT first) -- an
    OSError updating the record's local copy (its own tmp.write_text +
    os.replace) must never propagate out of --tunnel-step and crash it;
    it exits by the outcome it already wrote, with a WARN printed."""
    root = _tunnel_root(tmp_path)
    rel = pm_align.Release("v3.53", "a" * 40, "d" * 40, "f" * 64, "")
    pm_align.write_record(root, rel, ["/x"], "2026-09-25T12:00:00Z", tunnel="pending")
    monkeypatch.setattr(pm_align, "tunnel_apply", lambda root, run, **kw:
                        {"outcome": "applied", "detail": "added everything"})

    real_replace = os.replace

    def flaky_replace(src, dst):
        if str(src).endswith("host-aligned.json.tmp"):
            raise OSError("disk full")
        return real_replace(src, dst)
    monkeypatch.setattr(pm_align.os, "replace", flaky_replace)

    rc = pm_align.main(["--tunnel-step"], root=root, run=_fake_run([]))
    assert rc == 0
    out = capsys.readouterr().out
    assert "WARN" in out
    doc = json.loads((root / "var/lib/pm-align/tunnel-result.json").read_text())
    assert doc["outcome"] == "applied"              # the real result still landed
    assert _record_tunnel(root) == "pending"         # local merge never got applied


# ── wizard static-text checks: sigmond-wizard.sh, not pm-align.py ──────────
#
# These read the real wizard shell script as text (no execution).  Two checks
# that once lived here moved with their code into the switch script, and
# tests/test_rac_switch.py now runs them: both spellings of the switch name
# all four relay sockets, and the wait counts every proxy frpc-host.toml
# declares, never a fixed four.

def test_wizard_records_the_rac_number_only_once_the_configuration_stands():
    # rac_write_config writes its marks after it has renamed the tunnel
    # configuration into place.  A host with a RAC number and no configuration
    # reads on the console panel as "assigned, but frpc-host.toml is missing".
    i = WIZARD.index("rac_write_config(){")
    body = WIZARD[i:WIZARD.index("\n}\n", i)]
    assert body.index('mv -f "$RAC_TOML.new" "$RAC_TOML"') < body.index("rac-number")


def test_wizard_installs_pm_align_on_the_host():
    assert "install -m 755 /root/sigmond-appliance/sigmond/scripts/proxmox/pm-align.py " \
           "/usr/local/sbin/pm-align" in WIZARD


# ── remote access: the switch, its units and the pairing ───────────────────
#
# sigmond tasks/plan-remote-access.md.  From v3.71 the wizard carries the
# switch script and five units as heredocs of its own, and pm-align brings
# them to a station already in the field, then pairs its decoder VM.

WIZ_V371 = WIZ + '''rac_install_host_files(){
    rm -f /usr/local/sbin/sigmond-rac-switch
    cat > /usr/local/sbin/sigmond-rac-switch <<'RACSWEOF'
#!/bin/bash
echo "Remote access: off"
RACSWEOF
    cat > /etc/systemd/system/sigmond-rac-pair.path <<'RACPAIRPATHEOF'
[Path]
PathChanged=/etc/ssh/ssh_host_ed25519_key.pub
RACPAIRPATHEOF
}
case "${1:-}" in
    --pair-vm)
        exit 0;;
esac
'''


# The line `sigmond-setup --pair-vm` prints when it has paired, through the wizard's `say`.
PAIRED_LINE = "[wizard] paired: the decoder VM can run smd remote on, smd remote off and smd remote status\n"


def _src_v371():
    return pm_align.Sources(FB_FULL, pm_align.render_wizard(WIZ_V371, 100),
                            {"pm-heartbeat.py": b"hb"})


def _apply_v371(tmp_path, monkeypatch, run):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.71", "a" * 40, "d" * 40, "f" * 64, ""))
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: _src_v371())
    return pm_align.main(["--apply"], root=tmp_path, run=run)


def test_desired_files_takes_the_switch_and_its_units_from_a_v371_wizard():
    files = {f.path: f for f in pm_align.desired_files(_src_v371(), 100)}
    sw = files["/usr/local/sbin/sigmond-rac-switch"]
    assert sw.mode == 0o755 and sw.content == b'#!/bin/bash\necho "Remote access: off"\n'
    unit = files["/etc/systemd/system/sigmond-rac-pair.path"]
    assert unit.mode == 0o644 and b"PathChanged=/etc/ssh/ssh_host_ed25519_key.pub" in unit.content


def test_desired_files_accepts_a_wizard_from_before_v371():
    # `--release v3.70` must keep working: nothing the new wizard adds is required.
    paths = {f.path for f in pm_align.desired_files(_src(), 100)}
    assert not any("sigmond-rac" in p for p in paths)
    assert "/usr/local/bin/sigmond-vm" in paths


def test_the_real_wizard_carries_every_file_pm_align_delivers_from_it():
    src = pm_align.Sources(FB_FULL, pm_align.render_wizard(WIZARD, 100), {})
    files = {f.path: f for f in pm_align.desired_files(src, 100)}
    for path, mode in {**pm_align.WIZARD_HEREDOCS, **pm_align.WIZARD_HEREDOCS_SINCE_V371}.items():
        assert path in files, f"the wizard no longer writes {path} from a quoted heredoc"
        assert files[path].mode == mode
    switch = files["/usr/local/sbin/sigmond-rac-switch"].content.decode()
    assert switch.startswith("#!/bin/bash\n")
    assert subprocess.run(["bash", "-n"], input=switch, text=True,
                          capture_output=True).returncode == 0
    assert f"    {pm_align.PAIR_FLAG})" in WIZARD


# What every pm-align already in the field demands of a release's wizard.  A
# station runs the pm-align its last alignment installed, and that copy reads
# the NEW release's wizard.  It refuses the whole release, with "the release's
# wizard no longer writes ...", when one of these has gone.
#
# The test reads the wizard with ITS OWN copy of the reader a fielded pm-align
# uses: the regex and the line scan below stand as literals, so the test does
# not move when pm-align.py does.  (A test that read through
# `pm_align.extract_heredocs` would pass a change that loosened the reader and
# the wizard together, while every pm-align in the field refused the release.)
FIELDED_QUOTED = ("/usr/local/bin/sigmond-vm", "/usr/local/lib/sigmond/vm-port-relay.py")
FIELDED_TEMPLATES = ("/etc/systemd/system/sigmond-vm-$RNAME-relay.socket",
                     "/etc/systemd/system/sigmond-vm-$RNAME-relay@.service")
FIELDED_RELAYS = (("ssh", 12222, 22), ("web", 12223, 8081),
                  ("station", 12224, 8000), ("gmag", 12225, 8082))
FIELDED_HEREDOC_START = re.compile(
    r"""^\s*cat\s+>\s*"?(/[^"\s]+)"?\s+<<\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\2\s*$""")


def _fielded_heredocs(text, quoted_only=True):
    """{path: body} the way a fielded pm-align reads a wizard: a top-level
    ``cat > PATH <<'DELIM'`` whose opener ends right after the delimiter, up to
    the delimiter alone on a line."""
    out, lines, i = {}, text.splitlines(keepends=True), 0
    while i < len(lines):
        m = FIELDED_HEREDOC_START.match(lines[i].rstrip("\n"))
        if not m:
            i += 1
            continue
        path, quote, delim = m.groups()
        j = i + 1
        while j < len(lines) and lines[j].rstrip("\n") != delim:
            j += 1
        assert j < len(lines), f"the heredoc for {path} (<<{delim}) never closes"
        if quote or not quoted_only:
            out[path] = "".join(lines[i + 1:j])
        i = j + 1
    return out


def test_the_real_wizard_still_satisfies_every_pm_align_in_the_field():
    quoted = _fielded_heredocs(WIZARD)
    for path in FIELDED_QUOTED:
        assert quoted.get(path, "").strip(), f"a fielded pm-align cannot read {path} from the wizard"
    templates = _fielded_heredocs(WIZARD, quoted_only=False)
    for path in FIELDED_TEMPLATES:
        assert path in templates, f"a fielded pm-align cannot read {path} from the wizard"
    sock = templates["/etc/systemd/system/sigmond-vm-$RNAME-relay.socket"]
    svc = templates["/etc/systemd/system/sigmond-vm-$RNAME-relay@.service"]
    units = {}
    for name, lport, vport in FIELDED_RELAYS:
        for kind, tmpl in (("socket", sock), ("service", svc)):
            units[(name, kind)] = (tmpl.replace("$RNAME", name).replace("$RLPORT", str(lport))
                                   .replace("$RVPORT", str(vport)).replace("$VMID", "100"))
    assert len(units) == 8
    for name, lport, vport in FIELDED_RELAYS:
        assert f"ListenStream=127.0.0.1:{lport}\n" in units[(name, "socket")]
        assert f"ExecStart=/usr/local/lib/sigmond/vm-port-relay.py {vport}\n" in units[(name, "service")]
        assert "Environment=SIGMOND_VMID=100\n" in units[(name, "service")]


def test_the_frozen_reader_is_still_the_reader_pm_align_has_today():
    # If this fails, someone changed pm-align's reader.  Do NOT edit the frozen
    # copy above to match: every pm-align already in the field keeps the OLD
    # reader, and the frozen test above must keep reading the wizard through it.
    # A change to the reader needs a migration plan for the release that ships
    # it (the wizard must still parse under the old reader, or the release must
    # reach the fleet some other way), and only then does this expected pattern
    # move.
    assert FIELDED_HEREDOC_START.pattern == pm_align._HEREDOC_START.pattern


def _setup(tmp_path, text):
    p = tmp_path / "usr/local/sbin/sigmond-setup"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_pairing_step_runs_the_installed_wizards_pair_vm(tmp_path):
    _setup(tmp_path, WIZ_V371)
    calls = []
    assert pm_align.pairing_step(tmp_path, run=_fake_run(calls, stdout=PAIRED_LINE)) == "paired"
    assert calls == [["/usr/local/sbin/sigmond-setup", "--pair-vm"]]


def test_pairing_step_skips_a_wizard_that_cannot_pair(tmp_path):
    calls = []
    assert pm_align.pairing_step(tmp_path, run=_fake_run(calls)).startswith("skipped: no sigmond-setup")
    _setup(tmp_path, WIZ + "# it mentions --pair-vm in a comment, and has no such branch\n")
    assert "predates v3.71" in pm_align.pairing_step(tmp_path, run=_fake_run(calls))
    assert calls == []


def test_pairing_step_reports_the_wizards_last_line_on_failure(tmp_path):
    _setup(tmp_path, WIZ_V371)
    out = pm_align.pairing_step(tmp_path, run=_fake_run(
        [], rc=1, stdout="[wizard] ...\n[wizard] NOT paired: the guest agent in VM 100 does not answer\n"))
    assert out == "failed: [wizard] NOT paired: the guest agent in VM 100 does not answer"


def test_main_apply_pairs_after_the_files_and_before_heartbeat_and_tunnel(tmp_path, monkeypatch, capsys):
    _tunnel_root(tmp_path)                 # a tunnel short of two channels: the step launches
    (tmp_path / "etc/pm-heartbeat").mkdir(parents=True)
    (tmp_path / "etc/pm-heartbeat/config.toml").write_text(
        'station = "b4-pm"\nvmid = 100\ndest_host = "wd30"\n')
    order = []

    def run(argv, **kw):
        argv = list(argv)
        if argv[:2] == ["/usr/local/sbin/sigmond-setup", "--pair-vm"]:
            # the new wizard and the switch script stand on disk by now
            assert (tmp_path / "usr/local/sbin/sigmond-rac-switch").exists()
            order.append("pair")
            return subprocess.CompletedProcess(argv, 0, PAIRED_LINE, "")
        elif argv[:3] == ["qm", "guest", "exec"]:
            order.append("push")
        elif argv and argv[0].endswith("pm-heartbeat-setup.sh"):
            order.append("heartbeat")
        elif argv[0] == "systemd-run":
            order.append("tunnel")
        return subprocess.CompletedProcess(argv, 0, '{"exitcode": 0}', "")

    src = _src_v371()
    for name in pm_align.HB_FILES:
        src.proxmox[name] = b"#!/bin/sh\n"
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.71", "a" * 40, "d" * 40, "f" * 64, ""))
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: src)
    assert pm_align.main(["--apply"], root=tmp_path, run=run) == 0
    assert order == ["push", "pair", "heartbeat", "tunnel"]
    assert "pairing: the decoder VM can run smd remote on, smd remote off and smd remote status" \
        in capsys.readouterr().out


def test_main_apply_with_a_failed_pairing_exits_1_and_still_finishes(tmp_path, monkeypatch, capsys):
    def run(argv, **kw):
        if list(argv)[:2] == ["/usr/local/sbin/sigmond-setup", "--pair-vm"]:
            return subprocess.CompletedProcess(argv, 1, "[wizard] NOT paired: this host has no sudo\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert _apply_v371(tmp_path, monkeypatch, run) == 1
    out = capsys.readouterr().out
    assert "pairing: WARN — failed: [wizard] NOT paired: this host has no sudo" in out
    assert "retry: /usr/local/sbin/sigmond-setup --pair-vm" in out
    assert (tmp_path / "usr/local/sbin/sigmond-rac-switch").exists()       # the files landed
    assert json.loads((tmp_path / "etc/sigmond-appliance/host-aligned.json").read_text())["release"] == "v3.71"
    assert "tunnel: no host tunnel configured" in out                      # the later steps ran


def test_main_apply_survives_a_pairing_step_that_raises(tmp_path, monkeypatch, capsys):
    def run(argv, **kw):
        if list(argv)[:2] == ["/usr/local/sbin/sigmond-setup", "--pair-vm"]:
            raise subprocess.TimeoutExpired(argv, 900)
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert _apply_v371(tmp_path, monkeypatch, run) == 1
    assert "pairing: WARN — failed: raised TimeoutExpired" in capsys.readouterr().out


def test_main_dry_run_names_the_pairing_only_for_a_wizard_that_can_pair(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.71", "a" * 40, "d" * 40, "f" * 64, ""))
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: _src_v371())
    calls = []
    pm_align.main([], root=tmp_path, run=_fake_run(calls))
    assert "pairing: --apply pairs the decoder VM with this host" in capsys.readouterr().out
    assert calls == []                                 # a dry run pairs nothing
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: _src())
    pm_align.main([], root=tmp_path, run=_fake_run(calls))
    assert "pairing:" not in capsys.readouterr().out


def test_not_touched_names_the_one_package_the_pairing_may_install():
    text = " ".join(pm_align.NOT_TOUCHED)
    assert "every apt package" in text and "sudo" in text


# The pairing has a third outcome.  When another run holds the pairing's lock,
# `sigmond-setup --pair-vm` exits 0 and says "another pairing is running;
# nothing done": the run that holds the lock does the work, so this one claims
# no pairing.  It may also print a `WARN: ` line first, when the pairing's
# re-assertion units did not start.

def test_pairing_step_skips_when_another_pairing_holds_the_lock(tmp_path):
    _setup(tmp_path, WIZ_V371)
    calls = []
    out = pm_align.pairing_step(tmp_path, run=_fake_run(
        calls, stdout="[wizard] another pairing is running; nothing done\n"))
    assert out == "skipped: another pairing is running on this host; it does the work"
    assert calls == [["/usr/local/sbin/sigmond-setup", "--pair-vm"]]


def test_pairing_step_fails_a_nonzero_exit_even_when_it_names_another_pairing(tmp_path):
    _setup(tmp_path, WIZ_V371)
    out = pm_align.pairing_step(tmp_path, run=_fake_run(
        [], rc=1, stdout="[wizard] NOT paired: another pairing is running\n"))
    assert out == "failed: [wizard] NOT paired: another pairing is running"


def test_main_apply_with_another_pairing_running_exits_0_and_claims_no_pairing(tmp_path, monkeypatch, capsys):
    def run(argv, **kw):
        if list(argv)[:2] == ["/usr/local/sbin/sigmond-setup", "--pair-vm"]:
            return subprocess.CompletedProcess(
                argv, 0, "[wizard] another pairing is running; nothing done\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert _apply_v371(tmp_path, monkeypatch, run) == 0
    out = capsys.readouterr().out
    assert "  pairing: skipped: another pairing is running on this host; it does the work" in out
    assert "the decoder VM can run" not in out
    assert "retry:" not in out


def test_main_apply_prints_the_pairings_warn_line_and_still_exits_0(tmp_path, monkeypatch, capsys):
    def run(argv, **kw):
        if list(argv)[:2] == ["/usr/local/sbin/sigmond-setup", "--pair-vm"]:
            return subprocess.CompletedProcess(argv, 0, (
                "[wizard] WARN: the pairing's re-assertion units did not start (systemctl enable --now failed)\n"
                "[wizard] paired: the decoder VM can run smd remote on, smd remote off and smd remote status\n"), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert _apply_v371(tmp_path, monkeypatch, run) == 0
    out = capsys.readouterr().out
    assert "  pairing: WARN — the pairing's re-assertion units did not start (systemctl enable --now failed)" in out
    assert "  pairing: the decoder VM can run smd remote on, smd remote off and smd remote status" in out
    assert "retry:" not in out


# pm-align reads a file out of the wizard only where the opener line ends right
# after its delimiter (`_HEREDOC_START`): text after the delimiter, such as
# ` || return 1`, hides the heredoc from it.  Literals on purpose, so this test
# does not move when pm-align.py does.
V371_HEREDOC_PATHS = (
    "/usr/local/sbin/sigmond-rac-switch",
    "/etc/systemd/system/sigmond-rac-register.service",
    "/etc/systemd/system/sigmond-rac-register.timer",
    "/etc/systemd/system/sigmond-rac-pair.service",
    "/etc/systemd/system/sigmond-rac-pair.timer",
    "/etc/systemd/system/sigmond-rac-pair.path",
)


def test_pm_align_reads_the_switch_and_all_five_units_out_of_the_real_wizard():
    found = pm_align.extract_heredocs(WIZARD)
    missing = [p for p in V371_HEREDOC_PATHS if not found.get(p, "").strip()]
    assert not missing, f"extract_heredocs cannot read {missing} from the real wizard"


# ── fix round 1: an answer must say paired; a host with no private link stays
#    quiet; the step says what it runs; a partial wizard gets a note ─────────

SETUP_ARGV = ["/usr/local/sbin/sigmond-setup", "--pair-vm"]
LAN_INTERFACES = "auto vmbr0\niface vmbr0 inet dhcp\n"       # no vmbr1: the decoder VM sits on the LAN
NO_LINK = ("skipped: this host has no private link to its decoder VM (the pre-v3.4x layout); "
           "smd remote needs that link")
RUNNING = "pairing: running /usr/local/sbin/sigmond-setup --pair-vm (it can take some minutes)"


def _lan_host(tmp_path):
    (tmp_path / "etc/network").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/network/interfaces").write_text(LAN_INTERFACES)


def _patch_release(monkeypatch, src):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.71", "a" * 40, "d" * 40, "f" * 64, ""))
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: src)


# -- "paired" means the wizard said so ---------------------------------------------------------

@pytest.mark.parametrize("stdout,want", [
    ("", "failed: unexpected answer: no output"),
    ("[wizard] already configured (/etc/sigmond/rac-switch.key.pub)\n",
     "failed: unexpected answer: [wizard] already configured (/etc/sigmond/rac-switch.key.pub)"),
    ("\n[wizard] already configured\n\n", "failed: unexpected answer: [wizard] already configured"),
    ("[wizard] NOT paired: the guest agent in VM 100 does not answer\n",
     "failed: unexpected answer: [wizard] NOT paired: the guest agent in VM 100 does not answer"),
    (PAIRED_LINE + "[wizard] done\n", "failed: unexpected answer: [wizard] done"),
], ids=["no-output", "old-wizard-says-configured", "blank-lines-do-not-count",
        "exit-0-with-a-not-paired-line", "paired-is-not-the-last-line"])
def test_pairing_step_fails_an_exit_0_that_does_not_end_on_the_paired_line(tmp_path, stdout, want):
    _setup(tmp_path, WIZ_V371)
    assert pm_align.pairing_step(tmp_path, run=_fake_run([], stdout=stdout)) == want


def test_pairing_step_accepts_the_paired_line_after_a_warn_line(tmp_path):
    _setup(tmp_path, WIZ_V371)
    out = pm_align.pairing_step(tmp_path, run=_fake_run(
        [], stdout="[wizard] WARN: the units did not start\n" + PAIRED_LINE))
    assert out == "paired"


@pytest.mark.parametrize("stdout,want", [
    ("", "failed: unexpected answer: no output"),
    ("[wizard] already configured (/etc/sigmond/rac-switch.key.pub)\n",
     "failed: unexpected answer: [wizard] already configured (/etc/sigmond/rac-switch.key.pub)"),
], ids=["no-output", "old-wizard-says-configured"])
def test_main_apply_exits_1_for_an_exit_0_that_does_not_say_paired(tmp_path, monkeypatch, capsys,
                                                                   stdout, want):
    def run(argv, **kw):
        if list(argv)[:2] == SETUP_ARGV:
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert _apply_v371(tmp_path, monkeypatch, run) == 1
    out = capsys.readouterr().out
    assert f"pairing: WARN — {want}" in out
    assert "retry: /usr/local/sbin/sigmond-setup --pair-vm" in out
    assert "the decoder VM can run" not in out
    assert "tunnel: no host tunnel configured" in out                      # the later steps ran


# -- a host with no private link stays quiet -----------------------------------------------------

def test_pairing_step_skips_a_host_with_no_private_link_and_never_runs_the_wizard(tmp_path):
    _setup(tmp_path, WIZ_V371)
    _lan_host(tmp_path)
    calls = []
    assert pm_align.pairing_step(tmp_path, run=_fake_run(calls, stdout=PAIRED_LINE)) == NO_LINK
    assert calls == []


def test_pairing_step_pairs_a_host_whose_interfaces_declare_vmbr1(tmp_path):
    _setup(tmp_path, WIZ_V371)
    (tmp_path / "etc/network").mkdir(parents=True)
    (tmp_path / "etc/network/interfaces").write_text("auto vmbr0\nauto vmbr1\n")
    assert pm_align.pairing_step(tmp_path, run=_fake_run([], stdout=PAIRED_LINE)) == "paired"


def test_main_apply_on_a_host_with_no_private_link_exits_0_and_runs_the_later_steps(
        tmp_path, monkeypatch, capsys):
    _tunnel_root(tmp_path)
    _lan_host(tmp_path)
    (tmp_path / "etc/pm-heartbeat").mkdir(parents=True)
    (tmp_path / "etc/pm-heartbeat/config.toml").write_text(
        'station = "b4-pm"\nvmid = 100\ndest_host = "wd30"\n')
    order = []

    def run(argv, **kw):
        argv = list(argv)
        if argv[:2] == SETUP_ARGV:
            order.append("pair")
        elif argv[:3] == ["qm", "guest", "exec"]:
            order.append("push")
        elif argv and argv[0].endswith("pm-heartbeat-setup.sh"):
            order.append("heartbeat")
        elif argv[0] == "systemd-run":
            order.append("tunnel")
        return subprocess.CompletedProcess(argv, 0, '{"exitcode": 0}', "")

    src = _src_v371()
    for name in pm_align.HB_FILES:
        src.proxmox[name] = b"#!/bin/sh\n"
    _patch_release(monkeypatch, src)
    assert pm_align.main(["--apply"], root=tmp_path, run=run) == 0
    assert order == ["push", "heartbeat", "tunnel"]                       # the wizard never ran
    out = capsys.readouterr().out
    assert f"  pairing: {NO_LINK}" in out
    assert RUNNING not in out and "WARN" not in out


def test_main_dry_run_on_a_host_with_no_private_link_prints_the_skip_instead_of_the_offer(
        tmp_path, monkeypatch, capsys):
    _lan_host(tmp_path)
    _patch_release(monkeypatch, _src_v371())
    pm_align.main([], root=tmp_path, run=_fake_run([]))
    out = capsys.readouterr().out
    assert f"  pairing: {NO_LINK}" in out
    assert "--apply pairs" not in out


# -- the step says what it runs, and keeps the wizard's own complaint --------------------------

def test_the_pairing_step_says_what_it_runs_before_it_starts_the_wizard(tmp_path, monkeypatch, capsys):
    seen = []

    def run(argv, **kw):
        if list(argv)[:2] == SETUP_ARGV:
            seen.append(capsys.readouterr().out)          # everything printed up to this call
            return subprocess.CompletedProcess(argv, 0, PAIRED_LINE, "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert _apply_v371(tmp_path, monkeypatch, run) == 0
    assert len(seen) == 1 and f"  {RUNNING}\n" in seen[0]
    after = capsys.readouterr().out
    assert RUNNING not in after and "pairing: the decoder VM can run" in after


@pytest.mark.parametrize("stdout,stderr,want", [
    ("[wizard] starting\n", "/usr/local/sbin/sigmond-setup: line 1236: pm: unbound variable\n",
     "failed: [wizard] starting | /usr/local/sbin/sigmond-setup: line 1236: pm: unbound variable"),
    ("[wizard] starting\n", "first\n\nthe last one\n\n", "failed: [wizard] starting | the last one"),
    ("[wizard] NOT paired: this host has no sudo\n", "noise\n",
     "failed: [wizard] NOT paired: this host has no sudo"),
    ("", "bash: boom\n", "failed: bash: boom"),
    ("", "", "failed: exit 1"),
], ids=["stderr-follows", "last-nonempty-stderr-line", "not-paired-line-stands-alone",
        "stderr-only", "nothing-at-all"])
def test_a_failed_pairing_keeps_the_wizards_stderr_line_unless_it_said_why(tmp_path, stdout, stderr, want):
    _setup(tmp_path, WIZ_V371)
    assert pm_align.pairing_step(tmp_path, run=_fake_run([], rc=1, stdout=stdout, stderr=stderr)) == want


# -- a wizard that carries some, but not all, of the six remote-access files -------------------

def test_desired_files_notes_a_wizard_that_carries_some_of_the_six():
    notes = []
    pm_align.desired_files(_src_v371(), 100, notes)             # the switch and pair.path: 2 of 6
    assert notes == ["this release's wizard carries 2 of the 6 remote-access files "
                     "(sigmond-rac-register.service, sigmond-rac-register.timer, "
                     "sigmond-rac-pair.service, sigmond-rac-pair.timer)"]


def test_desired_files_notes_nothing_for_none_or_all_of_the_six():
    for src in (_src(), pm_align.Sources(FB_FULL, pm_align.render_wizard(WIZARD, 100), {})):
        notes = []
        pm_align.desired_files(src, 100, notes)
        assert notes == []


def test_the_dry_run_and_apply_print_the_note_for_a_partial_wizard(tmp_path, monkeypatch, capsys):
    _patch_release(monkeypatch, _src_v371())
    pm_align.main([], root=tmp_path, run=_fake_run([]))
    dry = capsys.readouterr().out
    assert "  note: this release's wizard carries 2 of the 6 remote-access files (sigmond-rac-register.service," in dry
    _apply_v371(tmp_path / "applied", monkeypatch, _fake_run([], stdout=PAIRED_LINE))
    assert "  note: this release's wizard carries 2 of the 6 remote-access files" in capsys.readouterr().out


def test_the_dry_run_prints_no_note_for_a_wizard_with_none_or_all_of_the_six(tmp_path, monkeypatch, capsys):
    for src in (_src(), pm_align.Sources(FB_FULL, pm_align.render_wizard(WIZARD, 100), {})):
        _patch_release(monkeypatch, src)
        pm_align.main([], root=tmp_path, run=_fake_run([]))
        assert "remote-access files" not in capsys.readouterr().out


# -- the words an operator reads ------------------------------------------------------------------

def test_the_texts_an_operator_reads_name_the_pairing_step(capsys):
    assert "pairing step" in pm_align.__doc__ and "sudo" in pm_align.__doc__
    assert "pairing" in pm_align.main.__doc__
    with pytest.raises(SystemExit):
        pm_align.main(["--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "pair the decoder VM" in help_text and "sudo" in help_text


# --- a Ctrl-C during the pairing ends --apply ---

def test_main_apply_ends_at_a_keyboard_interrupt_during_the_pairing(tmp_path, monkeypatch, capsys):
    # The pairing can run some minutes with its output captured, and an admin
    # who presses Ctrl-C there means "stop".  --apply must not walk on to the
    # heartbeat and to the tunnel step, which restarts the tunnel.
    _tunnel_root(tmp_path)                 # a tunnel short of two channels: the step would launch
    later = []

    def run(argv, **kw):
        argv = list(argv)
        if argv[:2] == ["/usr/local/sbin/sigmond-setup", "--pair-vm"]:
            raise KeyboardInterrupt
        if argv[0] == "systemd-run" or argv[0].endswith("pm-heartbeat-setup.sh"):
            later.append(argv[0])
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises(KeyboardInterrupt):
        _apply_v371(tmp_path, monkeypatch, run)
    assert later == []
    out = capsys.readouterr().out
    assert "tunnel:" not in out and "heartbeat:" not in out
    assert "pairing: running /usr/local/sbin/sigmond-setup --pair-vm" in out      # it had begun


def test_the_pairing_step_still_turns_an_ordinary_failure_into_a_warning():
    src = inspect.getsource(pm_align._apply_pairing)
    assert "except Exception as exc:" in src and "BaseException" not in src


# --- --status shows the pairing's recorded outcome ---

def _pairing_line(tmp_path, capsys, content=None):
    if content is not None:
        p = tmp_path / "etc/sigmond-appliance/rac-pairing"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    assert pm_align.main(["--status"], root=tmp_path) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("pairing")]
    assert len(lines) == 1, lines
    return lines[0]


def test_status_says_no_pairing_recorded_on_a_host_that_never_ran_one(tmp_path, capsys):
    assert _pairing_line(tmp_path, capsys) == "pairing: no pairing recorded (/etc/sigmond-appliance/rac-pairing)"
    assert _pairing_line(tmp_path, capsys, b"") == "pairing: no pairing recorded (/etc/sigmond-appliance/rac-pairing)"


def test_status_prints_the_recorded_outcome_of_the_last_pairing(tmp_path, capsys):
    assert _pairing_line(tmp_path, capsys, b"paired 2026-10-10T01:02:03Z\n") == \
        "pairing (/etc/sigmond-appliance/rac-pairing): paired 2026-10-10T01:02:03Z"
    line = _pairing_line(tmp_path, capsys,
                         b"not-paired 2026-10-10T07:02:03Z the guest agent in VM 100 does not answer\n")
    assert line.endswith(": not-paired 2026-10-10T07:02:03Z the guest agent in VM 100 does not answer")


def test_status_prints_one_printable_line_whatever_the_file_holds(tmp_path, capsys):
    line = _pairing_line(tmp_path, capsys, b"not-paired \x1b[2J\x00 x\xff" + b"y" * 600 + b"\nsecond line\n")
    assert "\x1b" not in line and "\x00" not in line and "second line" not in line
    assert len(line) < 400


def test_the_file_status_reads_is_the_file_the_wizard_writes():
    assert pm_align.PAIRING_RECORD == "/etc/sigmond-appliance/rac-pairing"
    assert '"$MARK_DIR/rac-pairing"' in WIZARD and "\nMARK_DIR=/etc/sigmond-appliance\n" in WIZARD


# ── the on-site check (sigmond tasks/spec-smd-check-v372.md §6) ─────────────
#
# From v3.72 the wizard writes /usr/local/bin/sigmond-check beside sigmond-vm,
# and pm-align brings it to a station set up before that.

WIZ_V372 = WIZ + '''cat > /usr/local/bin/sigmond-check <<'CHKEOF'
#!/bin/bash
echo PASS
CHKEOF
'''


def test_desired_files_takes_sigmond_check_from_a_v372_wizard():
    src = pm_align.Sources(FB_FULL, pm_align.render_wizard(WIZ_V372, 100), {"pm-heartbeat.py": b"hb"})
    files = {f.path: f for f in pm_align.desired_files(src, 100)}
    f = files["/usr/local/bin/sigmond-check"]
    assert f.mode == 0o755 and f.content == b"#!/bin/bash\necho PASS\n"


def test_desired_files_accepts_a_wizard_from_before_v372():
    paths = {f.path for f in pm_align.desired_files(_src(), 100)}
    assert "/usr/local/bin/sigmond-check" not in paths
    assert "/usr/local/bin/sigmond-vm" in paths


def test_the_real_wizard_carries_sigmond_check():
    src = pm_align.Sources(FB_FULL, pm_align.render_wizard(WIZARD, 100), {})
    files = {f.path: f for f in pm_align.desired_files(src, 100)}
    for path, mode in pm_align.WIZARD_HEREDOCS_SINCE_V372.items():
        assert path in files, f"the wizard no longer writes {path} from a quoted heredoc"
        assert files[path].mode == mode
    body = files["/usr/local/bin/sigmond-check"].content.decode()
    assert body.startswith("#!/bin/bash\n")
    assert "sigmond-vm smd check --no-verdict" in body


# -- the IPv6 relay on every host (v3.72 #15) --------------------------------
#
# The decoder VM is IPv4-only; the host relays its ports over IPv6 with
# sigmond-vm6proxy (sigmond-appliance).  Up to v3.71 only the IPv6-only gateway
# installed it.  pm-align now fetches the script at the release's
# appliance_commit, installs it as a host file, and runs its `install` when a
# unit is missing, aimed at the VM address the IPv4 DNAT rules already use.

VM6 = "/usr/local/sbin/sigmond-vm6proxy"
VM6_BODY = ('#!/bin/bash\nPORTS="8000:8000 8081:8081 8082:8082 8765:8765 2222:22"\n'
            'IPAddressDeny=any\n')
# A relay from before the local-networks-only restriction (every tag back to
# 2afd8f0): its socket units carry no IPAddressDeny.
VM6_OLD = '#!/bin/bash\nPORTS="8000:8000 8081:8081 8082:8082 8765:8765 2222:22"\n'
VM6_DROPIN = "etc/systemd/system/sigmond-vm6proxy@.socket.d/10-allow-local.conf"
_VM6_KEY = "sigmond-appliance/" + "a" * 40 + "/sigmond-vm6proxy"
NAT_S = ("-P PREROUTING ACCEPT\n"
         "-A PREROUTING -p tcp -m tcp --dport 8000 -j DNAT --to-destination 10.99.0.2:8000\n"
         "-A PREROUTING -p tcp -m tcp --dport 8081 -j DNAT --to-destination 10.99.0.2:8081\n"
         "-A PREROUTING -p tcp -m tcp --dport 2222 -j DNAT --to-destination 10.99.0.2:22\n")


def _vm6_run(calls, *, nat=NAT_S, install_rc=0, install_out="  :8000 -> ...\n"):
    """A fake run: iptables answers with ``nat``; the relay's install exits
    ``install_rc``.  Each call is recorded as (argv, env-or-None)."""
    def run(argv, **kw):
        calls.append((list(argv), kw.get("env")))
        if argv[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        if argv[:1] == ["iptables"]:
            return subprocess.CompletedProcess(argv, 0, nat, "")
        if argv[:2] == [VM6, "install"]:
            return subprocess.CompletedProcess(argv, install_rc, install_out,
                                               "" if install_rc == 0 else "boom\n")
        return subprocess.CompletedProcess(argv, 0, "", "")
    return run


def _vm6_installs(calls):
    return [(argv, env) for argv, env in calls if argv[:2] == [VM6, "install"]]


def _write_vm6_units(root, ports=(8000, 8081, 8082, 8765, 2222)):
    d = root / "etc/systemd/system"
    d.mkdir(parents=True, exist_ok=True)
    for p in ports:
        (d / f"sigmond-vm6proxy@{p}.socket").write_text("[Socket]\nIPAddressDeny=any\n")
    dropin = root / VM6_DROPIN
    dropin.parent.mkdir(parents=True, exist_ok=True)
    dropin.write_text("[Socket]\nIPAddressDeny=any\nIPAddressAllow=localhost link-local\n")


def test_fetch_sources_carries_the_relay_from_the_appliance_commit():
    op = _urlopen(_source_routes())
    src = pm_align.fetch_sources(pm_align.fetch_release(urlopen=op), vmid=100, urlopen=op)
    assert src.vm6proxy == VM6_BODY.encode()


def test_fetch_sources_treats_a_404_on_the_relay_as_absent():
    routes = _source_routes()
    routes[_VM6_KEY] = urllib.error.HTTPError(_VM6_KEY, 404, "Not Found", None, None)
    op = _urlopen(routes)
    src = pm_align.fetch_sources(pm_align.fetch_release(urlopen=op), vmid=100, urlopen=op)
    assert src.vm6proxy is None


def test_fetch_sources_refuses_a_non_404_failure_on_the_relay():
    routes = _source_routes()
    routes[_VM6_KEY] = urllib.error.HTTPError(_VM6_KEY, 500, "Server Error", None, None)
    op = _urlopen(routes)
    with pytest.raises(pm_align.PmAlignError):
        pm_align.fetch_sources(pm_align.fetch_release(urlopen=op), vmid=100, urlopen=op)


def _vm6_manifest_routes(sha):
    routes = _source_routes()
    routes["https://dl/m"] = _manifest() + f"vm6proxy_sha256: {sha}\n"
    return routes


def test_fetch_sources_checks_the_relay_against_the_manifest_hash():
    good = hashlib.sha256(VM6_BODY.encode()).hexdigest()
    op = _urlopen(_vm6_manifest_routes(good))
    rel = pm_align.fetch_release(urlopen=op)
    assert rel.vm6proxy_sha256 == good
    assert pm_align.fetch_sources(rel, vmid=100, urlopen=op).vm6proxy == VM6_BODY.encode()

    op = _urlopen(_vm6_manifest_routes("0" * 64))
    with pytest.raises(pm_align.PmAlignError, match="vm6proxy_sha256"):
        pm_align.fetch_sources(pm_align.fetch_release(urlopen=op), vmid=100, urlopen=op)


def test_fetch_release_refuses_a_malformed_relay_hash():
    op = _urlopen(_vm6_manifest_routes("not-a-hash"))
    with pytest.raises(pm_align.PmAlignError, match="vm6proxy_sha256"):
        pm_align.fetch_release(urlopen=op)


def test_fetch_release_without_a_relay_hash_leaves_it_unset():
    assert pm_align.fetch_release(urlopen=_urlopen(_release_routes())).vm6proxy_sha256 is None


def test_desired_files_installs_the_relay_when_the_release_carries_it():
    src = _src()
    src.vm6proxy = VM6_BODY.encode()
    f = {f.path: f for f in pm_align.desired_files(src, 100)}[VM6]
    assert f.mode == 0o755 and f.content == VM6_BODY.encode()
    assert VM6 not in {f.path for f in pm_align.desired_files(_src(), 100)}


def test_vm6proxy_ports_follow_the_script():
    assert pm_align.vm6proxy_ports(VM6_BODY) == [8000, 8081, 8082, 8765, 2222]
    assert pm_align.vm6proxy_ports('PORTS="9000:80"\n') == [9000]


def test_vm6proxy_ports_match_the_real_script():
    real = REPO.parent / "sigmond-appliance" / "sigmond-vm6proxy"
    if not real.exists():
        pytest.skip("no sigmond-appliance checkout beside this one")
    assert pm_align.vm6proxy_ports(real.read_text()) == [8000, 8081, 8082, 8765, 2222]


def test_vm_dnat_target_reads_the_ipv4_relay_rule():
    assert pm_align.vm_dnat_target(_vm6_run([])) == "10.99.0.2"
    assert pm_align.vm_dnat_target(_vm6_run([], nat="-P PREROUTING ACCEPT\n")) is None


def _host_with_relay_script(root):
    p = root / VM6.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(VM6_BODY)


def test_vm6proxy_step_installs_when_a_unit_is_missing(tmp_path):
    _host_with_relay_script(tmp_path)
    _write_vm6_units(tmp_path, ports=(8000, 8081))
    calls = []
    out = pm_align.vm6proxy_step(tmp_path, run=_vm6_run(calls))
    assert out.startswith("installed")
    (argv, env), = _vm6_installs(calls)
    assert argv == [VM6, "install"]
    assert env["SIGMOND_MGMT_VM"] == "10.99.0.2"


def test_vm6proxy_step_leaves_existing_units_alone(tmp_path):
    _host_with_relay_script(tmp_path)
    _write_vm6_units(tmp_path)
    calls = []
    assert pm_align.vm6proxy_step(tmp_path, run=_vm6_run(calls)) == "current"
    assert _vm6_installs(calls) == []
    assert not any(argv[:1] == ["systemctl"] for argv, _ in calls)


def test_vm6proxy_step_skips_without_the_script(tmp_path):
    calls = []
    assert pm_align.vm6proxy_step(tmp_path, run=_vm6_run(calls)).startswith("skipped")
    assert calls == []


def test_vm6proxy_step_skips_without_an_ipv4_relay_rule(tmp_path):
    _host_with_relay_script(tmp_path)
    calls = []
    out = pm_align.vm6proxy_step(tmp_path, run=_vm6_run(calls, nat="-P PREROUTING ACCEPT\n"))
    assert out.startswith("skipped") and "DNAT" in out
    assert _vm6_installs(calls) == []


def test_vm6proxy_step_reports_a_failed_install(tmp_path):
    _host_with_relay_script(tmp_path)
    out = pm_align.vm6proxy_step(tmp_path, run=_vm6_run([], install_rc=1,
                                                         install_out="cannot write x\n"))
    assert out.startswith("failed") and "cannot write x" in out


def _apply_vm6(tmp_path, monkeypatch, run):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.72", "a" * 40, "d" * 40, "f" * 64, ""))
    src = _src()
    src.vm6proxy = VM6_BODY.encode()
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: src)
    return pm_align.main(["--apply"], root=tmp_path, run=run)


def test_apply_writes_the_relay_then_installs_its_units(tmp_path, monkeypatch, capsys):
    calls = []
    rc = _apply_vm6(tmp_path, monkeypatch, _vm6_run(calls))
    assert rc == 0
    p = tmp_path / VM6.lstrip("/")
    assert p.read_text() == VM6_BODY and oct(p.stat().st_mode & 0o777) == oct(0o755)
    assert len(_vm6_installs(calls)) == 1
    assert "ipv6 relay: installed" in capsys.readouterr().out


def test_apply_warns_but_does_not_fail_when_the_relay_install_fails(tmp_path, monkeypatch, capsys):
    rc = _apply_vm6(tmp_path, monkeypatch, _vm6_run([], install_rc=1, install_out="nope\n"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "ipv6 relay: WARN" in out and "nope" in out


def test_apply_does_not_rerun_the_install_when_every_unit_exists(tmp_path, monkeypatch):
    _write_vm6_units(tmp_path)
    _host_with_relay_script(tmp_path)              # the same bytes the release carries
    calls = []
    assert _apply_vm6(tmp_path, monkeypatch, _vm6_run(calls)) == 0
    assert _vm6_installs(calls) == []


def test_dry_run_says_it_would_install_the_relay(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.72", "a" * 40, "d" * 40, "f" * 64, ""))
    src = _src()
    src.vm6proxy = VM6_BODY.encode()
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: src)
    (tmp_path / "etc/network").mkdir(parents=True)
    (tmp_path / "etc/network/interfaces").write_text("auto vmbr1\n")
    calls = []
    for f in pm_align.desired_files(src, pm_align.host_vmid(tmp_path)):
        dest = pm_align._under(tmp_path, f.path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f.content)
    assert pm_align.main([], root=tmp_path, run=_vm6_run(calls)) == 1
    assert "ipv6 relay: --apply would install it" in capsys.readouterr().out
    assert calls == []                                   # a dry run runs nothing

    _write_vm6_units(tmp_path)
    assert pm_align.main([], root=tmp_path, run=_vm6_run(calls)) == 0
    assert ("ipv6 relay: every socket unit carries IPAddressDeny=any and the allow-list "
            "drop-in is present") in capsys.readouterr().out


# A host that already has every unit still needs the new unit text when the
# relay script itself changed (the local-networks-only restriction, 10-15).

def test_vm6proxy_step_reinstalls_when_the_script_changed(tmp_path):
    _host_with_relay_script(tmp_path)
    _write_vm6_units(tmp_path)
    calls = []
    out = pm_align.vm6proxy_step(tmp_path, run=_vm6_run(calls), script_changed=True)
    assert out.startswith("installed") and "changed" in out
    (argv, env), = _vm6_installs(calls)
    assert env["SIGMOND_MGMT_VM"] == "10.99.0.2"


def test_apply_reinstalls_when_the_host_has_an_older_relay(tmp_path, monkeypatch, capsys):
    _write_vm6_units(tmp_path)
    p = tmp_path / VM6.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/bash\n# the v3.72-rc relay, no allow list\n")
    calls = []
    assert _apply_vm6(tmp_path, monkeypatch, _vm6_run(calls)) == 0
    assert len(_vm6_installs(calls)) == 1
    assert "ipv6 relay: installed" in capsys.readouterr().out


def test_dry_run_says_it_would_reinstall_a_changed_relay_and_runs_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.72", "a" * 40, "d" * 40, "f" * 64, ""))
    src = _src()
    src.vm6proxy = VM6_BODY.encode()
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: src)
    _write_vm6_units(tmp_path)
    p = tmp_path / VM6.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/bash\n# older\n")
    calls = []
    assert pm_align.main([], root=tmp_path, run=_vm6_run(calls)) == 1
    assert "ipv6 relay: --apply would re-run install" in capsys.readouterr().out
    assert calls == []


# -- final review (2026-10-15): never run a relay that cannot filter ---------
#
# Allow without Deny filters nothing.  A relay script from before the
# restriction writes socket units without IPAddressDeny, so pm-align neither
# carries such a script to a host nor runs one it finds there, and it counts
# a unit current only when it denies and the allow-list drop-in exists.

def test_vm6proxy_step_refuses_a_script_without_deny(tmp_path):
    p = tmp_path / VM6.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(VM6_OLD)
    calls = []
    out = pm_align.vm6proxy_step(tmp_path, run=_vm6_run(calls), script_changed=True)
    assert out.startswith("refused") and "IPAddressDeny=any" in out
    assert _vm6_installs(calls) == []


def test_apply_warns_on_a_refused_relay_and_never_says_installed(tmp_path, monkeypatch, capsys):
    p = tmp_path / VM6.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(VM6_OLD)
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.70", "a" * 40, "d" * 40, "f" * 64, ""))
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: _src())
    calls = []
    assert pm_align.main(["--apply"], root=tmp_path, run=_vm6_run(calls)) == 0
    out = capsys.readouterr().out
    assert "ipv6 relay: WARN" in out and "ipv6 relay: installed" not in out
    assert _vm6_installs(calls) == []


def test_a_downgrade_neither_carries_nor_runs_the_old_relay(tmp_path, monkeypatch, capsys):
    _host_with_relay_script(tmp_path)               # the restricted relay
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.70", "a" * 40, "d" * 40, "f" * 64, ""))
    old = _src()
    old.vm6proxy = VM6_OLD.encode()                  # what an older release carries
    assert VM6 not in {f.path for f in pm_align.desired_files(old, 100)}
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: old)
    calls = []
    assert pm_align.main(["--apply"], root=tmp_path, run=_vm6_run(calls)) == 0
    assert (tmp_path / VM6.lstrip("/")).read_text() == VM6_BODY     # kept
    for argv, env in _vm6_installs(calls):                         # only the kept one runs
        assert argv == [VM6, "install"]
    assert "does not restrict" in capsys.readouterr().out


def test_a_unit_without_deny_or_a_missing_dropin_is_not_current(tmp_path):
    _host_with_relay_script(tmp_path)
    _write_vm6_units(tmp_path)
    assert pm_align.vm6proxy_step(tmp_path, run=_vm6_run([])) == "current"

    (tmp_path / "etc/systemd/system/sigmond-vm6proxy@2222.socket").write_text("[Socket]\n")
    calls = []
    assert pm_align.vm6proxy_step(tmp_path, run=_vm6_run(calls)).startswith("installed")
    assert len(_vm6_installs(calls)) == 1

    _write_vm6_units(tmp_path)
    (tmp_path / VM6_DROPIN).unlink()
    calls = []
    assert pm_align.vm6proxy_step(tmp_path, run=_vm6_run(calls)).startswith("installed")
    assert len(_vm6_installs(calls)) == 1


def test_the_relay_runs_with_a_minimal_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGMOND_VM6PROXY_UNITDIR", "/tmp/elsewhere")
    monkeypatch.setenv("SIGMOND_VM6PROXY_TEST", "1")
    monkeypatch.setenv("PYTHONPATH", "/tmp/decoy")
    _host_with_relay_script(tmp_path)
    calls = []
    pm_align.vm6proxy_step(tmp_path, run=_vm6_run(calls))
    (argv, env), = _vm6_installs(calls)
    assert set(env) == {"PATH", "LANG", "SIGMOND_MGMT_VM"}
    assert env["PATH"] == "/usr/sbin:/usr/bin:/sbin:/bin"


def test_the_real_relay_restricts_and_writes_the_dropin_pm_align_checks():
    real = REPO.parent / "sigmond-appliance" / "sigmond-vm6proxy"
    if not real.exists():
        pytest.skip("no sigmond-appliance checkout beside this one")
    text = real.read_text()
    assert pm_align.vm6proxy_restricts(text)
    assert 'DROPDIR="${UNITDIR}/sigmond-vm6proxy@.socket.d"' in text
    assert 'DROPIN="${DROPDIR}/10-allow-local.conf"' in text
    assert pm_align.VM6PROXY_DROPIN.endswith("/sigmond-vm6proxy@.socket.d/10-allow-local.conf")
