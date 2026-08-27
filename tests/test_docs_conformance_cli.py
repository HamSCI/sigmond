import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "docs-conformance.py"


def _run(root):
    return subprocess.run([sys.executable, str(SCRIPT), str(root)],
                          capture_output=True, text=True)


def _conformant(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "INDEX.md").write_text(
        "# x\n> **Verified against:** x abc1234 on 2026-08-27 — docs\n")
    (tmp_path / "docs" / "REQUIREMENTS.md").write_text("# x\n")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "docs-check.yml").write_text(
        "uses: HamSCI/sigmond/.github/workflows/docs-check.yml@main\n")
    return tmp_path


def test_clean_repo_exits_zero(tmp_path):
    r = _run(_conformant(tmp_path))
    assert r.returncode == 0
    assert "0 finding(s)" in r.stderr


def test_findings_exit_one_and_print_rule_and_path(tmp_path):
    (tmp_path / "docs").mkdir()
    r = _run(tmp_path)
    assert r.returncode == 1
    assert "docs/INDEX.md: [20.1]" in r.stdout
    assert "3 finding(s)" in r.stderr


def test_script_runs_without_sigmond_on_syspath(tmp_path):
    """CI checks out sigmond to .sigmond-tools and runs the script by path;
    it must bootstrap its own lib/ rather than needing PYTHONPATH."""
    r = subprocess.run([sys.executable, str(SCRIPT), str(_conformant(tmp_path))],
                       capture_output=True, text=True, cwd="/tmp")
    assert r.returncode == 0
