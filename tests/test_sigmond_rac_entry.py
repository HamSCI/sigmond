"""sigmond-rac, the bare-metal entry point (scripts/rac/sigmond-rac).

A wrapper wizard stands in for the real one: it sources the real wizard as a
library, then points the registration paths and the host-file installer under
tmp_path.
"""
import os
import subprocess
import textwrap
from pathlib import Path

from tests import test_wizard_rac_register as rr

REPO = Path(__file__).resolve().parent.parent
ENTRY = REPO / "scripts" / "rac" / "sigmond-rac"
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"


def setup(tmp_path, tiers="", appliance=False, pairing=False):
    prelude = rr.host(tmp_path, tiers=tiers or "x|127.0.0.1|http://127.0.0.1:1/register|on|1",
                      relays=False, configured=None)
    stub = tmp_path / "switch-stub"
    stub.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        echo "$1 by=${{SIGMOND_RAC_BY:-}} link=$(readlink {tmp_path}/sigmond-rac)" >> {tmp_path}/switch.calls
        echo "Remote access: on"
        exit ${{SWITCH_RC:-0}}
        """))
    stub.chmod(0o755)
    # drop the prelude's `set -u`/say/systemctl stubs only where harmless
    wrapper = tmp_path / "wizard.sh"
    wrapper.write_text(textwrap.dedent(f"""\
        SIGMOND_WIZARD_AS_LIB=1 source {WIZARD} 2>/dev/null
        """) + prelude + textwrap.dedent(f"""
        RAC_SWITCH={tmp_path}/sigmond-rac-switch
        rac_install_host_files(){{
            printf '%s\\n' "$RAC_MODE" > "$MARK_DIR/rac-mode"
            cp {stub} "$RAC_SWITCH"
        }}
        return 0
        """))
    av, pk = tmp_path / "appliance-version", tmp_path / "switch.key"
    if appliance:
        av.write_text("3.85\n")
    if pairing:
        pk.write_text("k\n")
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path),
           "SIGMOND_WIZARD": str(wrapper),
           "SIGMOND_RAC_LOG": str(tmp_path / "rac.log"),
           "SIGMOND_RAC_LINK": str(tmp_path / "sigmond-rac"),
           "SIGMOND_RAC_APPLIANCE_VERSION": str(av),
           "SIGMOND_RAC_PAIR_KEY": str(pk),
           "SIGMOND_SITE_PROFILE": str(tmp_path / "absent.toml"),
           "SIGMOND_RAC_HOSTNAME": "sigma"}
    return env


def entry(tmp_path, env, *args, extra=None):
    e = dict(env, **(extra or {}))
    return subprocess.run([str(ENTRY), *args], capture_output=True, text=True,
                          env=e, cwd=tmp_path, timeout=120)


def mark(tmp_path):
    return tmp_path / "marks"


def test_activate_refuses_on_appliance_vm(tmp_path):
    env = setup(tmp_path, appliance=True)
    r = entry(tmp_path, env, "activate", "--site", "AC0G-X")
    assert r.returncode == 5, r.stdout + r.stderr
    assert "Proxmox host carries this machine's RAC" in r.stderr
    assert not (mark(tmp_path) / "rac-site").exists()
    assert not (mark(tmp_path) / "rac-mode").exists()
    assert not (tmp_path / "sigmond-rac").exists()


def test_activate_refuses_when_paired(tmp_path):
    env = setup(tmp_path, pairing=True)
    assert entry(tmp_path, env, "activate", "--site", "AC0G-X").returncode == 5


def test_activate_refuses_without_site(tmp_path):
    env = setup(tmp_path)
    r = entry(tmp_path, env, "activate")
    assert r.returncode == 4, r.stdout + r.stderr
    assert "[station] callsign in /etc/sigmond/site-profile.toml" in r.stderr
    assert "--site" in r.stderr
    assert not (mark(tmp_path) / "rac-desired").exists()


def test_activate_records_site_and_wish(tmp_path):
    env = setup(tmp_path)
    prof = tmp_path / "p.toml"
    prof.write_text('[station]\ncallsign = "AC0G"\n')
    r = entry(tmp_path, env, "activate", extra={"SIGMOND_SITE_PROFILE": str(prof)})
    assert r.returncode == 0, r.stdout + r.stderr
    m = mark(tmp_path)
    assert (m / "rac-site").read_text().strip() == "AC0G-SIGMA"
    assert (m / "rac-desired").read_text().strip() == "on"
    assert (m / "rac-mode").read_text().strip() == "station"
    assert (m / "rac-changed").read_text().split()[0] == "on"
    assert (m / "rac-changed").read_text().split()[2] == "install"
    assert "Remote access: on" in r.stdout
    calls = (tmp_path / "switch.calls").read_text()
    # the link existed, pointing at the real script, when the switch ran
    assert calls.startswith("on by=install link=") and str(ENTRY) in calls


def test_activate_site_flag_names_machine(tmp_path):
    env = setup(tmp_path)
    r = entry(tmp_path, env, "activate", "--site", "ac0g/b9")
    assert r.returncode == 0, r.stderr
    assert (mark(tmp_path) / "rac-site").read_text().strip() == "AC0G_B9"


def test_activate_refuses_differing_recorded_site(tmp_path):
    env = setup(tmp_path)
    m = mark(tmp_path)
    m.mkdir(exist_ok=True)
    (m / "rac-site").write_text("AC0G-OLD\n")
    r = entry(tmp_path, env, "activate", "--site", "AC0G-NEW")
    assert r.returncode == 6, r.stdout + r.stderr
    assert "AC0G-OLD" in r.stderr and "AC0G-NEW" in r.stderr
    assert "remove" in r.stderr and "rac-site" in r.stderr
    assert (m / "rac-site").read_text() == "AC0G-OLD\n"
    assert not (tmp_path / "switch.calls").exists()
    assert not (m / "rac-desired").exists()


def test_activate_same_recorded_site_proceeds(tmp_path):
    env = setup(tmp_path)
    m = mark(tmp_path)
    m.mkdir(exist_ok=True)
    (m / "rac-site").write_text("AC0G-OLD\n")
    r = entry(tmp_path, env, "activate", "--site", "ac0g-old")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (m / "rac-site").read_text().strip() == "AC0G-OLD"


def test_activate_propagates_switch_exit_code(tmp_path):
    env = setup(tmp_path)
    r = entry(tmp_path, env, "activate", "--site", "AC0G-X", extra={"SWITCH_RC": "3"})
    assert r.returncode == 3, r.stdout + r.stderr


def test_broken_wizard_is_an_error(tmp_path):
    env = setup(tmp_path)
    bad = tmp_path / "bad-wizard.sh"
    bad.write_text("echo nothing here\nreturn 0\n")
    r = entry(tmp_path, env, "register", extra={"SIGMOND_WIZARD": str(bad)})
    assert r.returncode == 1
    assert str(bad) in r.stderr


def test_activate_keeps_operator_off(tmp_path):
    env = setup(tmp_path)
    m = mark(tmp_path)
    m.mkdir(exist_ok=True)
    (m / "rac-desired").write_text("off\n")
    r = entry(tmp_path, env, "activate", "--site", "AC0G-SIGMA")
    assert r.returncode == 0, r.stderr
    assert (m / "rac-desired").read_text().strip() == "off"
    assert not (m / "rac-changed").exists()
    assert (tmp_path / "switch.calls").read_text().startswith("status ")


def test_register_station_end_to_end(tmp_path):
    reg = rr.Registrar(reply={"user": "0123456789abcdef", "token": "tok3nTOKEN42",
                              "rac": 17, "server_addr": "127.0.0.1", "server_port": 35735,
                              "ports": {"vm_ssh": 35817, "vm_web": 45817}})
    try:
        env = setup(tmp_path, tiers=rr.tier(reg.port))
        r = entry(tmp_path, env, "register", "--site", "AC0G-SIGMA")
    finally:
        reg.close()
    assert r.returncode == 0, r.stdout + r.stderr
    toml = (tmp_path / "frpc-host-gw2.toml").read_text()
    names = [ln.split('"')[1] for ln in toml.splitlines() if ln.startswith("name = ")]
    assert names == ["AC0G-SIGMA-vm-ssh", "AC0G-SIGMA-vm-web",
                     "AC0G-SIGMA-vm-station", "AC0G-SIGMA-vm-gmag"]


def test_register_failure_prints_reason(tmp_path):
    env = setup(tmp_path, tiers="x|127.0.0.1|http://127.0.0.1:1/register|on|1")
    r = entry(tmp_path, env, "register", "--site", "AC0G-SIGMA")
    assert r.returncode == 1
    assert "not registered:" in r.stdout


def test_bad_verb(tmp_path):
    env = setup(tmp_path)
    assert entry(tmp_path, env, "bogus").returncode == 2
