"""Tests for scripts/proxmox/pm-align.py — the Proxmox-HOST alignment tool.

A PM runs no sigmond and has no git; pm-align is one stdlib file, loaded
here by path (its filename has a dash).  Every host path is joined under a
temp root and every command goes through an injected ``run``.
"""
import hashlib
import http.client
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


def test_not_touched_lists_every_global_constraint_area():
    text = " ".join(pm_align.NOT_TOUCHED)
    for phrase in ("/var/lib/vz/snippets", "initramfs", "modules", "sigmond-netfix.service"):
        assert phrase in text


def _fake_run(calls, rc=0, stdout=""):
    def run(argv, **kw):
        calls.append(list(argv))
        # tunnel_apply's active check comes first and needs "active" to
        # proceed; nothing outside the tunnel tests calls is-active, so this
        # is a no-op for every other caller of _fake_run.
        if argv[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        return subprocess.CompletedProcess(argv, rc, stdout, "")
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
    assert p == tmp_path / "etc/sigmond-appliance/host-aligned.json"


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
