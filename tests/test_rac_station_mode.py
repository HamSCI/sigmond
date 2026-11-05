"""Library mode, RAC_MODE and the station site name.

SIGMOND_WIZARD_AS_LIB=1 lets another script source the wizard for its
functions: everything above the dispatch is defined, and nothing runs.  The
site name has two modes.  Host mode (the default) is the Proxmox behaviour.
Station mode names a bare-metal machine <CALL>-<HOSTNAME>.
"""
import os
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"


def run(tmp_path, body, env=None, args=""):
    mark = tmp_path / "mark"
    mark.mkdir(exist_ok=True)
    script = textwrap.dedent(f"""
        SIGMOND_WIZARD_AS_LIB=1 source {WIZARD} {args} 2>/dev/null
        MARK_DIR={mark}
        CONF_MARK={mark}/.configured
        {body}
    """)
    e = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    e.update(env or {})
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, env=e, cwd=tmp_path)


def profile(tmp_path, text):
    p = tmp_path / "site-profile.toml"
    p.write_text(text)
    return str(p)


def test_lib_mode_returns_before_dispatch(tmp_path):
    sentinel = tmp_path / "ran"
    r = run(tmp_path,
            f'rac_register(){{ touch {sentinel}; }}\n'
            'declare -F rac_site_name >/dev/null && echo defined',
            args="--rac-register")
    assert "defined" in r.stdout, r.stderr
    assert not sentinel.exists()


def test_recorded_site_wins_over_reporter(tmp_path):
    (tmp_path / "mark").mkdir()
    (tmp_path / "mark" / "rac-site").write_text("AC0G_B4\n")
    r = run(tmp_path, "REPORTER=AC0G/B9; rac_site_name")
    assert r.stdout == "AC0G_B4", r.stderr


def test_host_mode_falls_back_to_reporter(tmp_path):
    r = run(tmp_path, "REPORTER=ac0g/b4; rac_site_name")
    assert r.stdout == "AC0G_B4", r.stderr


def test_station_default_call_hostname(tmp_path):
    env = {"SIGMOND_SITE_PROFILE": profile(tmp_path, '[station]\ncallsign = "AC0G"\n'),
           "SIGMOND_RAC_HOSTNAME": "sigma"}
    r = run(tmp_path, "RAC_MODE=station; rac_site_name", env)
    assert r.stdout == "AC0G-SIGMA", r.stderr


def test_station_hostname_already_prefixed(tmp_path):
    env = {"SIGMOND_SITE_PROFILE": profile(tmp_path, '[station]\ncallsign = "AC0G"\n'),
           "SIGMOND_RAC_HOSTNAME": "ac0g-b1"}
    r = run(tmp_path, "RAC_MODE=station; rac_site_name", env)
    assert r.stdout == "AC0G-B1", r.stderr


def test_station_env_site(tmp_path):
    env = {"SIGMOND_RAC_SITE": "AC0G-X",
           "SIGMOND_SITE_PROFILE": profile(tmp_path, '[station]\ncallsign = "AC0G"\n'),
           "SIGMOND_RAC_HOSTNAME": "sigma"}
    r = run(tmp_path, "RAC_MODE=station; rac_site_name", env)
    assert r.stdout == "AC0G-X", r.stderr


def test_station_no_call_prints_nothing(tmp_path):
    env = {"SIGMOND_SITE_PROFILE": str(tmp_path / "absent.toml"),
           "SIGMOND_RAC_HOSTNAME": "sigma"}
    r = run(tmp_path, "RAC_MODE=station; rac_site_name", env)
    assert r.stdout == "", r.stderr
