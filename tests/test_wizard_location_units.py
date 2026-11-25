"""The wizard installs sigmond's own location-check units, not a pair of its own.

sigmond ships systemd/sigmond-location-check.{service,timer} (b0503a3) and
drift compares the installed files with them.  The wizard used to write an
older pair over them, so every fresh v3.95 image showed a drift fault on both
units (AI6VN, AI6VN-2, 2026-11-25).  This renders the wizard's guest script
as the host does and runs it against a fake checkout."""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"


def _guest_script() -> str:
    text = WIZARD.read_text()
    start = text.index("LOCTICK_B64=$(base64 -w0 <<'LOCEOF'")
    end = text.index('gexec 60 "echo $(echo "$LOCINST"', start)
    render = text[start:end] + 'printf "%s" "$LOCINST"\n'
    return subprocess.run(["bash", "-c", render], capture_output=True, text=True,
                          check=True).stdout


def _run(tmp_path, with_repo_units: bool) -> Path:
    root = tmp_path / "root"
    units = root / "etc" / "systemd" / "system"
    units.mkdir(parents=True)
    (root / "usr" / "local" / "sbin").mkdir(parents=True)
    repo = root / "opt" / "git" / "sigmond" / "sigmond" / "systemd"
    if with_repo_units:
        repo.mkdir(parents=True)
        (repo / "sigmond-location-check.service").write_text("# repo service\n")
        (repo / "sigmond-location-check.timer").write_text("# repo timer\n")
    script = _guest_script()
    script = re.sub(r"(?<![\w/])/(etc|opt|usr)/", lambda m: f"{root}/{m.group(1)}/", script)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "systemctl").chmod(0o755)
    subprocess.run(["bash", "-c", script], check=True,
                   env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
    return units


def test_repo_units_win_when_the_checkout_has_them(tmp_path):
    units = _run(tmp_path, with_repo_units=True)
    assert (units / "sigmond-location-check.service").read_text() == "# repo service\n"
    assert (units / "sigmond-location-check.timer").read_text() == "# repo timer\n"


def test_wizard_pair_remains_the_fallback(tmp_path):
    units = _run(tmp_path, with_repo_units=False)
    assert "sigmond-location-tick" in (units / "sigmond-location-check.service").read_text()
    assert "OnUnitActiveSec=120" in (units / "sigmond-location-check.timer").read_text()
