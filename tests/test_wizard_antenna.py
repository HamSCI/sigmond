"""The wizard's free-text antenna answer cannot break site-profile.toml.

The wizard pastes $ANTENNA between double quotes in a TOML basic string
(`description = "$ANTENNA"`).  A `"` or `\\` in the answer made the new
profile unparseable, `smd config profile-install` refused it, and first boot
aborted.  ask_antenna strips both, and control characters too (a bare `read`
takes an arrow key as raw ESC bytes, which TOML also refuses).  The
profile-install refusal stays as a backstop.
"""
import subprocess
import textwrap
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"


def ask_antenna_source() -> str:
    out, keep = [], False
    for ln in WIZARD.read_text().splitlines(True):
        if ln.startswith("ask_antenna() {"):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, "ask_antenna not found in the wizard"
    return "".join(out)


def answer(text: bytes) -> str:
    script = textwrap.dedent("""
        set -u
        rd(){ read "$@"; }
        @@FUNC@@
        ask_antenna
        printf '%s' "$ANTENNA"
    """).replace("@@FUNC@@", ask_antenna_source())
    return subprocess.run(["bash", "-c", script], input=text, capture_output=True,
                          timeout=30).stdout.decode()


def profile_line(antenna: str) -> dict:
    # The heredoc's own line, filled as bash fills it.
    return tomllib.loads(f'[station]\ndescription = "{antenna}"\n')["station"]


def test_quotes_and_backslashes_cannot_break_the_profile():
    got = answer(b'Hy-Gain "6BTV" vertical \\ 40 m\n')
    assert '"' not in got and "\\" not in got
    assert profile_line(got)["description"] == "Hy-Gain 6BTV vertical  40 m"


def test_an_arrow_key_cannot_break_the_profile():
    got = answer(b"EFHW\x1b[D 49:1\n")
    assert profile_line(got)["description"] == "EFHW[D 49:1"


def test_a_plain_answer_passes_through():
    assert answer(b"80 m doublet, 12 m up\n") == "80 m doublet, 12 m up"


def test_enter_skips():
    assert answer(b"\n") == ""


def test_the_profile_heredoc_still_quotes_the_answer():
    # The guard above only matters while the heredoc pastes $ANTENNA inside
    # a TOML basic string.
    assert 'description = "$ANTENNA"' in WIZARD.read_text()
