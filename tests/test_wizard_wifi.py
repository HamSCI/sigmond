"""The wizard's Wi-Fi step — behaviour, not prose.

rob asked for this three times and each pass widened it (tasks/plan-wizard-wifi.md):
probe for a radio during install, let the operator scan and join, and finally the
one that inverts the design — **prefer Wi-Fi over wired**, because an Ethernet
cable entering the shack is a conducted noise path into the HF receiver.  v3.55
shipped the `sigmond-wifi` CLI and the plan but never wired a step into the
wizard, so an operator installing AI6VN on 2026-09-29 was never offered it.

These tests run the real function out of the real script with injected paths,
rather than grepping it for strings: the failure that mattered here was a step
that did not exist, and a prose test cannot tell "offered" from "described".

⛔ The stdin case is the dangerous one.  The wizard's questions are answered in a
fixed order, and on piped stdin bash silently suppresses `read -p` prompts.  A
new prompt that reads when stdin is not a tty eats the answer meant for the NEXT
question and desyncs every prompt after it, invisibly — the 2026-08-11
nested-test hunt.  test_unattended_consumes_no_stdin is the guard on that.
"""
import os
import pty
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"

# The Wi-Fi block as shipped, lifted verbatim between its banner and the next
# question.  Extracting rather than duplicating means these tests exercise the
# code that actually runs; if the block is renamed or removed they fail loudly.
START = "# ── Wi-Fi ─"
END = "ask_rac() {"


def wifi_block() -> str:
    text = WIZARD.read_text()
    i, j = text.index(START), text.index(END)
    assert i < j, "Wi-Fi block must precede ask_rac"
    return text[i:j]


# @@BLOCK@@ is substituted, not %-formatted: the extracted shell is full of
# printf format strings and % would collide with every one of them.
HARNESS = """set -u
rd(){ read "$@"; }
say(){ printf '%s\\n' "$*"; }
@@BLOCK@@
ask_wifi
printf 'SUMMARY=%s\\n' "$WIFI_SUMMARY"
# -t 2, not a bare read: on a pty there is no EOF, so an unbounded read here
# would hang the interactive cases forever waiting for input nobody sends.
if read -r -t 2 LEFTOVER; then printf 'LEFTOVER=%s\\n' "$LEFTOVER"; else printf 'LEFTOVER=\\n'; fi
"""


def run(env_extra, stdin=b"", tty=False):
    script = HARNESS.replace("@@BLOCK@@", wifi_block())
    env = dict(os.environ, **env_extra)
    if not tty:
        p = subprocess.run(["bash", "-c", script], input=stdin,
                           capture_output=True, env=env, timeout=60)
        return p.stdout.decode()
    # A tty is the whole point of the interactive cases: the step deliberately
    # does nothing without one.
    m, s = pty.openpty()
    p = subprocess.Popen(["bash", "-c", script], stdin=s, stdout=s, stderr=s,
                         env=env, close_fds=True)
    os.close(s)
    os.write(m, stdin)
    out = b""
    try:
        while True:
            try:
                chunk = os.read(m, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    finally:
        p.wait(timeout=60)
        os.close(m)
    return out.decode(errors="replace")


class WifiStepTests(unittest.TestCase):
    def setUp(self):
        self.empty = str(Path(os.environ.get("PYTEST_TMPDIR", "/tmp")) / "no-such-sysfs")

    def test_no_radio_says_so_and_asks_nothing(self):
        out = run({"SIGMOND_WIFI_SYSFS": self.empty, "SIGMOND_WIFI_DEV": ""},
                  stdin=b"NEXT-ANSWER\n")
        self.assertIn("no Wi-Fi radio on this host", out)
        # a host with no radio must not be prompted at all
        self.assertIn("LEFTOVER=NEXT-ANSWER", out)

    def test_radio_but_tool_missing_is_reported(self):
        out = run({"SIGMOND_WIFI_DEV": "wlan0",
                   "SIGMOND_WIFI_TOOL": "/nonexistent/sigmond-wifi"},
                  stdin=b"NEXT-ANSWER\n")
        self.assertIn("sigmond-wifi is missing", out)
        self.assertIn("LEFTOVER=NEXT-ANSWER", out)

    def test_unattended_consumes_no_stdin(self):
        """The guard: a piped answer file must reach the next question intact."""
        with _fake_tool() as tool:
            out = run({"SIGMOND_WIFI_DEV": "wlan0", "SIGMOND_WIFI_TOOL": tool},
                      stdin=b"NEXT-ANSWER\n")
        self.assertIn("skipped (unattended run)", out)
        self.assertIn("LEFTOVER=NEXT-ANSWER", out)

    def test_offered_on_a_tty_and_declinable(self):
        with _fake_tool() as tool:
            out = run({"SIGMOND_WIFI_DEV": "wlan0", "SIGMOND_WIFI_TOOL": tool},
                      stdin=b"n\n", tty=True)
        self.assertIn("Wi-Fi radio found: wlan0", out)
        self.assertIn("Set up Wi-Fi now?", out)
        self.assertIn("declined", out)

    def test_states_the_noise_case_for_preferring_wifi(self):
        """rob's reason is physical, not convenience — the operator must see it."""
        with _fake_tool() as tool:
            out = run({"SIGMOND_WIFI_DEV": "wlan0", "SIGMOND_WIFI_TOOL": tool},
                      stdin=b"n\n", tty=True)
        self.assertIn("noise path", out)

    def test_scan_then_join_by_number(self):
        with _fake_tool() as tool:
            out = run({"SIGMOND_WIFI_DEV": "wlan0", "SIGMOND_WIFI_TOOL": tool},
                      stdin=b"y\n1\nsecret\n", tty=True)
        self.assertIn("HomeAP", out)
        self.assertIn('joined "HomeAP"', out)

    def test_weak_signal_is_flagged(self):
        with _fake_tool() as tool:
            out = run({"SIGMOND_WIFI_DEV": "wlan0", "SIGMOND_WIFI_TOOL": tool},
                      stdin=b"y\n\n", tty=True)
        self.assertIn("-70 dBm", out)


class RejectedPassphraseTests(unittest.TestCase):
    """⛔ A rejected passphrase must stop the wizard, not slip past it.

    rob, 2026-09-29, first real use: he picked sigmond-v6, guessed the
    passphrase, and "it says failed ... and then it went on. That's not what I
    wanted."  The join was reporting the failure correctly; the wizard threw
    the answer away, because it tested a PIPELINE:

        if printf '%s' "$_pass" | sigmond-wifi join "$_ssid" | sed 's/^/    /'

    A pipeline's status is its LAST command's, so this asked sed whether the
    join worked, and sed always says yes.  Every failed join was a success.

    The original stub here always succeeded, so the suite was green while the
    bug shipped — which is the real lesson: a stub that cannot fail cannot test
    a failure.
    """

    def test_failure_is_not_reported_as_success(self):
        with _fake_tool(fail_joins=1) as tool:
            out = run({"SIGMOND_WIFI_DEV": "wlan0", "SIGMOND_WIFI_TOOL": tool},
                      stdin=b"y\n1\nwrongpass\nq\n", tty=True)
        self.assertIn("could not join", out)
        self.assertNotIn('SUMMARY=joined', out)

    def test_rejection_is_named_as_such(self):
        with _fake_tool(fail_joins=1) as tool:
            out = run({"SIGMOND_WIFI_DEV": "wlan0", "SIGMOND_WIFI_TOOL": tool},
                      stdin=b"y\n1\nwrongpass\nq\n", tty=True)
        self.assertIn("rejected that passphrase", out)

    def test_can_retype_the_passphrase_without_rescanning(self):
        """rob: 'it should go back and prompt and repeat'."""
        with _fake_tool(fail_joins=1) as tool:
            out = run({"SIGMOND_WIFI_DEV": "wlan0", "SIGMOND_WIFI_TOOL": tool},
                      stdin=b"y\n1\nwrongpass\np\nrightpass\n", tty=True)
        self.assertIn('SUMMARY=joined "HomeAP"', out)

    def test_repeated_failures_keep_offering_the_choice(self):
        """'repeated opportunities' — not one retry and out."""
        with _fake_tool(fail_joins=3) as tool:
            out = run({"SIGMOND_WIFI_DEV": "wlan0", "SIGMOND_WIFI_TOOL": tool},
                      stdin=b"y\n1\nbad1\np\nbad2\np\nbad3\np\ngood\n", tty=True)
        self.assertIn('SUMMARY=joined "HomeAP"', out)

    def test_can_quit_out_of_a_failing_join(self):
        with _fake_tool(fail_joins=1) as tool:
            out = run({"SIGMOND_WIFI_DEV": "wlan0", "SIGMOND_WIFI_TOOL": tool},
                      stdin=b"y\n1\nwrongpass\nq\n", tty=True)
        self.assertIn("refused the passphrase", out)

    def test_can_go_back_and_pick_another_network(self):
        with _fake_tool(fail_joins=1) as tool:
            out = run({"SIGMOND_WIFI_DEV": "wlan0", "SIGMOND_WIFI_TOOL": tool},
                      stdin=b"y\n1\nwrongpass\ns\n2\ngood\n", tty=True)
        self.assertIn('SUMMARY=joined "FarAP"', out)


class WiringTests(unittest.TestCase):
    """⛔ The bug that started this was a step that existed but was never CALLED.

    v3.55 shipped `sigmond-wifi`, the console help text and a plan document; the
    wizard never ran any of it, so the operator was offered nothing.  A test of
    ask_wifi's behaviour cannot catch that — only a test of the wiring can.
    """

    def setUp(self):
        self.text = WIZARD.read_text()

    def test_asked_during_the_run(self):
        seq = self.text.index("preflight_devices\n")
        review = self.text.index("── Review —")
        self.assertIn("\nask_wifi\n", self.text[seq:review],
                      "ask_wifi is defined but never called in the question sequence")

    def test_asked_before_remote_access(self):
        """RAC is only meaningful over a link that works."""
        run = self.text[self.text.index("preflight_devices\n"):
                        self.text.index("── Review —")]
        # Match the CALL lines, not any mention: the comment above ask_wifi
        # explains why it precedes ask_rac and so names ask_rac first.
        self.assertLess(run.index("\nask_wifi\n"), run.index("\nask_rac\n"))

    def test_review_screen_lists_and_can_re_edit_it(self):
        review = self.text[self.text.index("── Review —"):]
        self.assertIn("8) Wi-Fi:", review)
        self.assertIn("8) ask_wifi;;", review)
        # the operator has to be told 8 is a valid choice, not just that it works
        self.assertIn("1-8", review)
        self.assertNotIn("entry number 1-7", review)


class _fake_tool:
    """A stand-in `sigmond-wifi` with a stable scan list."""

    SCAN = (
        "  HomeAP                       2.4GHz    -48 dBm  WPA2\n"
        "  FarAP                        5GHz      -78 dBm  WPA2"
        "  ⚠ weak — a cable may be more reliable\n"
    )

    def __init__(self, fail_joins=0):
        """fail_joins: how many join attempts fail before one succeeds.

        The first version of this stub always succeeded, which is exactly why
        the suite passed while every real failed join was being reported as a
        success (rob, 2026-09-29).  A stub that cannot fail cannot test a
        failure path.
        """
        self.fail_joins = fail_joins

    def __enter__(self):
        import tempfile
        self.d = tempfile.mkdtemp()
        p = Path(self.d) / "sigmond-wifi"
        p.write_text(
            "#!/bin/bash\n"
            f"N={self.fail_joins}\n"
            f"C={self.d}/attempts\n"
            "case \"$1\" in\n"
            f"  scan) printf '%s' {shquote(self.SCAN)} ;;\n"
            "  join) cat >/dev/null\n"
            "        n=$(cat \"$C\" 2>/dev/null || echo 0); n=$((n+1)); echo $n > \"$C\"\n"
            "        if [ \"$n\" -le \"$N\" ]; then\n"
            "            printf '  WRONG_KEY - the AP rejected the passphrase\\n'; exit 1\n"
            "        fi\n"
            "        printf '  joined ok\\n' ;;\n"
            "esac\n"
        )
        p.chmod(0o755)
        return str(p)

    def __exit__(self, *a):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)


def shquote(s: str) -> str:
    import shlex
    return shlex.quote(s)


if __name__ == "__main__":
    unittest.main()
