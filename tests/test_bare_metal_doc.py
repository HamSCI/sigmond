"""The bare-metal doc drives the rig's test: extract its commands, rewrite
them for a throwaway identity, judge the `smd check --json` that follows."""
import json
from pathlib import Path

import pytest

from sigmond import bare_metal_doc as bmd
from sigmond.commands.check import RADIOD_DORMANT

ROOT = Path(__file__).resolve().parent.parent
RL = "--release-list /opt/git/sigmond/sigmond/release/components.toml"

DOC = f"""# Installing

> **Verified against:** sigmond abc on 2026-11-18

## 1. Before you start

```bash
echo not-a-step
```

## 2. Install sigmond

Prose here.

```bash
git clone https://github.com/HamSCI/sigmond /opt/git/sigmond/sigmond
cd /opt/git/sigmond/sigmond
git checkout v3.94
./install.sh
```

## 3. Bring up the station

```bash
smd bringup dasi2 --callsign AC0G --grid EM38ww --reporter AC0G/S \\
                  {RL} \\
                  --psws-station-id S000418 \\
                  --psws-instrument hf-timestd=367 \\
                  --psws-instrument mag-recorder=401
```

## 4. Next
"""


def test_extract_current_doc():
    steps = bmd.extract((ROOT / "docs" / "install-bare-metal.md").read_text())
    assert "--release-list" in steps.bringup
    # The release ladder adds `git checkout vN` before the bare-metal test.
    assert steps.checkout_version and steps.checkout_version.startswith("v")
    assert "./install.sh" in steps.install


def test_extract_with_checkout_line():
    steps = bmd.extract(DOC)
    assert steps.checkout_version == "v3.94"
    assert steps.install[-1] == "./install.sh"
    assert "echo not-a-step" not in steps.install
    assert "\\" not in steps.bringup and "\n" not in steps.bringup
    assert steps.bringup.startswith("smd bringup dasi2")


def test_two_checkout_lines_refuse():
    doc = DOC.replace("git checkout v3.94\n", "git checkout v3.94\ngit checkout v3.95\n")
    with pytest.raises(bmd.DocError):
        bmd.extract(doc)


def test_bringup_without_release_list_refuses():
    with pytest.raises(bmd.DocError):
        bmd.extract(DOC.replace(f"{RL} \\\n", "").replace(RL, ""))


def test_other_profile_or_none_passes():
    assert bmd.extract(DOC.replace("smd bringup dasi2", "smd bringup other")).bringup.startswith("smd bringup other ")
    sensed = DOC.replace("smd bringup dasi2 ", "smd bringup ")
    assert bmd.extract(sensed).bringup.startswith("smd bringup --callsign")


def test_non_bringup_command_refuses():
    with pytest.raises(bmd.DocError):
        bmd.extract(DOC.replace("smd bringup dasi2", "smd status"))


def test_digest_ignores_prose_and_verified_line():
    other = DOC.replace("2026-11-18", "2026-12-01").replace("Prose here.", "Other words.")
    assert bmd.commands_digest(bmd.extract(DOC)) == bmd.commands_digest(bmd.extract(other))


def test_digest_changes_with_a_command():
    other = DOC.replace("./install.sh", "./install.sh --fast")
    assert bmd.commands_digest(bmd.extract(DOC)) != bmd.commands_digest(bmd.extract(other))


def test_script_substitutes_commit_and_identity():
    steps = bmd.extract(DOC)
    inst, br = bmd.test_script(steps, sigmond_commit="a" * 40)
    assert "git checkout " + "a" * 40 in inst and "git checkout v3.94" not in inst
    assert inst.startswith("set -euo pipefail") and br.startswith("set -euo pipefail")
    assert "--callsign N0CALL" in br and "--grid EM38ww" in br
    assert "--reporter N0CALL/BM" in br and "AC0G" not in br
    assert "--psws" not in br and "S000418" not in br and "hf-timestd=367" not in br
    assert RL in br
    assert br.rstrip().endswith("--non-interactive")


def test_script_install_part_records_the_head_last():
    inst, _ = bmd.test_script(bmd.extract(DOC), sigmond_commit="a" * 40)
    lines = inst.rstrip("\n").splitlines()
    assert lines[-1] == "git -C /opt/git/sigmond/sigmond rev-parse HEAD > /tmp/bm-sigmond-head"
    assert lines[-2] == "./install.sh"


def test_script_head_line_stays_out_of_the_digest():
    steps = bmd.extract(DOC)
    assert not any("bm-sigmond-head" in c for c in steps.install)


def test_current_doc_installs_git_first():
    steps = bmd.extract((ROOT / "docs" / "install-bare-metal.md").read_text())
    assert steps.install[0] == "sudo apt-get install -y git"
    assert [c for c in steps.install if c.startswith("git clone")]


def test_script_needs_a_release_checkout():
    with pytest.raises(bmd.DocError, match="the doc checks out no release"):
        bmd.test_script(bmd.extract(DOC.replace("git checkout v3.94\n", "")),
                        sigmond_commit="a" * 40)


def _doc(**over):
    marks = {c: ("wait", f"{c} idle; {RADIOD_DORMANT}") for c in bmd.RADIOD_CONSUMERS}
    marks.update(over)
    lines = [{"mark": m, "label": c, "text": t} for c, (m, t) in marks.items()]
    lines.append({"mark": "ok", "label": "services", "text": "fine"})
    lines.append({"mark": "--", "label": "magnetometer", "text": "absent"})
    return {"lines": lines, "verdict": "WAIT"}


def test_judge_passes_dormant_waits():
    assert bmd.judge_check(_doc()) == (True, [])


def test_judge_fails_on_any_fail():
    d = _doc()
    d["lines"].append({"mark": "FAIL", "label": "drift", "text": "bad"})
    ok, why = bmd.judge_check(d)
    assert not ok and any("drift" in w for w in why)


def test_judge_fails_on_consumer_not_waiting():
    ok, why = bmd.judge_check(_doc(PSK=("ok", "214 FT8")))
    assert not ok and any("PSK" in w for w in why)
    ok, why = bmd.judge_check(_doc(WSPR=("wait", "no spots yet")))
    assert not ok and any("WSPR" in w for w in why)


def test_judge_fails_on_missing_consumer():
    d = _doc()
    d["lines"] = [ln for ln in d["lines"] if ln["label"] != "GRAPE"]
    ok, why = bmd.judge_check(d)
    assert not ok and any("GRAPE" in w for w in why)


def test_main_cli(tmp_path, capsys):
    f = tmp_path / "d.md"
    f.write_text(DOC)
    assert bmd.main(["checkout-version", "--doc", str(f)]) == 0
    assert capsys.readouterr().out.strip() == "v3.94"
    assert bmd.main(["script", "--doc", str(f), "--commit", "b" * 40, "--part", "install"]) == 0
    assert "git checkout " + "b" * 40 in capsys.readouterr().out
    assert bmd.main(["digest", "--doc", str(f)]) == 0
    assert len(capsys.readouterr().out.strip()) == 64
    j = tmp_path / "c.json"
    j.write_text(json.dumps(_doc()))
    assert bmd.main(["judge", "--check-json", str(j)]) == 0
    j.write_text(json.dumps(_doc(PSK=("ok", "x"))))
    assert bmd.main(["judge", "--check-json", str(j)]) == 1
