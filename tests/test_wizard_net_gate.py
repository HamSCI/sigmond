"""The wizard must not proceed without a working uplink.

An install with no Internet cannot succeed, and it fails LATE: every question
gets answered, the VM is created, and the failure finally surfaces inside
bring-up as a github clone error, minutes later, with nobody standing by.
rob, 2026-10-11 — a working uplink is "a condition for proceeding through the
wizard ... if that isn't possible the installation should abort".

These run the real functions out of the real script with `ip`, `getent` and
`curl` shimmed on PATH, rather than grepping it for prose: the thing being
tested is a decision, and a prose test cannot tell "refused" from "described".

⛔ The stdin case is the dangerous one, for the same reason it is in
test_wizard_wifi.py.  bash suppresses `read -p` prompts on a non-tty, so a
prompt added here would eat the answer meant for the NEXT question and desync
every prompt after it, invisibly — the 2026-08-11 nested-test hunt.
test_unattended_consumes_no_stdin is the guard on that.
"""
import os
import pty
import select
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"

START = "# ── uplink gate ─"
END = "# ── Wi-Fi ─"


def gate_block() -> str:
    text = WIZARD.read_text()
    i, j = text.index(START), text.index(END)
    assert i < j, "the uplink gate must precede the Wi-Fi block"
    return text[i:j]


# @@BLOCK@@ is substituted, not %-formatted: the extracted shell is full of
# printf format strings and % would collide with every one of them.
HARNESS = """set -u
rd(){ read "$@"; }
say(){ printf '[wizard] %s\\n' "$*"; }
ask_wifi(){ printf 'ASK_WIFI_CALLED\\n'; }
@@BLOCK@@
require_uplink
printf 'PROCEEDED\\n'
if read -r -t 2 LEFTOVER; then printf 'LEFTOVER=%s\\n' "$LEFTOVER"; else printf 'LEFTOVER=\\n'; fi
"""

SHIMS = {
    # addr: prints a global address unless ADDR=none
    "ip": """#!/bin/bash
case "$*" in
  *"addr show scope global"*) [ "${FAKE_ADDR:-yes}" = none ] || echo "2: enp2s0    inet 10.22.23.47/24 scope global enp2s0";;
  *"-4 route show default"*)  [ "${FAKE_V4:-yes}"  = none ] || echo "default via 10.22.23.1 dev enp2s0";;
  *"-6 route show default"*)  [ "${FAKE_V6:-none}" = none ] || echo "default via fe80::1 dev enp2s0";;
esac
exit 0
""",
    "getent": """#!/bin/bash
[ "${FAKE_DNS:-ok}" = fail ] && exit 2
echo "140.82.121.4  STREAM github.com"
exit 0
""",
    # curl fails while a counter file is below FAKE_EGRESS_OK_AFTER.
    # ⛔ It also REFUSES a forced address family: without this the shim would
    # ignore its arguments, and "never force -4" would be an untested comment.
    "curl": """#!/bin/bash
for a in "$@"; do case "$a" in -4|--ipv4|-6|--ipv6)
  echo "FORCED-FAMILY $a" >&2; exit 97;; esac; done
n=0; [ -f "$FAKE_COUNT" ] && n=$(cat "$FAKE_COUNT")
n=$((n+1)); echo "$n" > "$FAKE_COUNT"
if [ "$n" -ge "${FAKE_EGRESS_OK_AFTER:-1}" ]; then exit 0; fi
echo "curl: (6) Could not resolve host" >&2
exit 6
""",
}


class UplinkGateTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        for name, body in SHIMS.items():
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)
        self.count = str(Path(self.tmp.name) / "count")

    def run_gate(self, env_extra=None, stdin=b"", tty=False):
        script = HARNESS.replace("@@BLOCK@@", gate_block())
        env = dict(os.environ,
                   PATH=f"{self.bin}:{os.environ['PATH']}",
                   FAKE_COUNT=self.count,
                   SIGMOND_NET_PROBE_HOST="github.com")
        env.update(env_extra or {})
        if not tty:
            p = subprocess.run(["bash", "-c", script], input=stdin,
                               capture_output=True, env=env, timeout=60)
            return p.stdout.decode() + p.stderr.decode(), p.returncode
        # ⛔ BOUND THE READ.  A bare os.read() on a pty blocks forever, so a
        # gate that can never pass would HANG the suite instead of failing it —
        # which is what happened when the force4 mutation was first run
        # (2026-10-11): the retry loop re-prompted, nobody answered, and the
        # test sat there rather than reporting.  A test that can hang is not a
        # test, so the deadline is part of the harness, not a convenience.
        m, s = pty.openpty()
        p = subprocess.Popen(["bash", "-c", script], stdin=s, stdout=s, stderr=s,
                             env=env, close_fds=True)
        os.close(s)
        os.write(m, stdin)
        out, deadline = b"", time.monotonic() + 20
        try:
            while time.monotonic() < deadline:
                if not select.select([m], [], [], 0.5)[0]:
                    if p.poll() is not None:
                        break
                    continue
                try:
                    chunk = os.read(m, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            else:
                out += b"\nHARNESS-TIMEOUT: the gate never terminated"
        finally:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=20)
            os.close(m)
        return out.decode(errors="replace"), p.returncode

    # ── the happy path ───────────────────────────────────────────────────

    def test_a_working_uplink_proceeds_without_prompting(self):
        out, rc = self.run_gate(stdin=b"NEXT-ANSWER\n")

        self.assertIn("uplink OK", out)
        self.assertIn("PROCEEDED", out)
        self.assertEqual(rc, 0)
        # a connected station must not be questioned at all
        self.assertIn("LEFTOVER=NEXT-ANSWER", out)

    def test_an_ipv6_only_station_passes(self):
        """⛔ This fleet runs IPv6-only stations behind NAT64/CLAT.  A probe
        that forced -4 would abort a perfectly good install."""
        out, rc = self.run_gate({"FAKE_V4": "none", "FAKE_V6": "yes"},
                                stdin=b"NEXT-ANSWER\n")

        self.assertIn("uplink OK", out)
        self.assertEqual(rc, 0)

    def test_the_probe_never_forces_an_address_family(self):
        """The other half of IPv6-only support, and the half a route check
        cannot cover: curl must resolve through getaddrinfo and use whichever
        family works.  The shim refuses -4/-6, so forcing one fails here."""
        out, rc = self.run_gate(stdin=b"NEXT-ANSWER\n")

        self.assertNotIn("FORCED-FAMILY", out)
        self.assertIn("uplink OK", out)

    # ── each stage names ITSELF ──────────────────────────────────────────

    def test_no_address_is_reported_as_a_link_fault(self):
        out, rc = self.run_gate({"FAKE_ADDR": "none"})

        self.assertIn("link:", out)
        self.assertIn("global address", out)
        self.assertNotIn("PROCEEDED", out)
        self.assertNotEqual(rc, 0)

    def test_an_address_with_no_default_route_is_refused(self):
        """The vmbr1 case: the host-only /30 to the decoder VM always carries a
        global address, so an address check alone passes on a station with no
        uplink at all."""
        out, rc = self.run_gate({"FAKE_V4": "none", "FAKE_V6": "none"})

        self.assertIn("no default route", out)
        self.assertNotIn("PROCEEDED", out)

    def test_dns_failure_is_named_as_dns(self):
        out, rc = self.run_gate({"FAKE_DNS": "fail"})

        self.assertIn("DNS:", out)
        self.assertIn("cannot resolve", out)
        self.assertNotIn("PROCEEDED", out)

    def test_egress_failure_carries_the_real_error(self):
        """'No internet' is useless at a console; the operator needs to know
        whether it was TLS, a timeout or a refusal."""
        out, rc = self.run_gate({"FAKE_EGRESS_OK_AFTER": "99"})

        self.assertIn("egress:", out)
        self.assertIn("Could not resolve host", out)
        self.assertNotIn("PROCEEDED", out)

    # ── the retry loop ───────────────────────────────────────────────────

    def test_r_rechecks_and_proceeds_once_the_cable_is_in(self):
        out, rc = self.run_gate({"FAKE_EGRESS_OK_AFTER": "2"},
                                stdin=b"r\n", tty=True)

        self.assertIn("egress:", out)
        self.assertIn("uplink OK", out)
        self.assertIn("PROCEEDED", out)
        self.assertEqual(rc, 0)

    def test_q_aborts_nonzero(self):
        out, rc = self.run_gate({"FAKE_EGRESS_OK_AFTER": "99"},
                                stdin=b"q\n", tty=True)

        self.assertIn("aborted", out)
        self.assertIn("nothing was applied", out)
        self.assertNotIn("PROCEEDED", out)
        self.assertNotEqual(rc, 0)

    def test_w_hands_off_to_the_wifi_step(self):
        out, rc = self.run_gate({"FAKE_EGRESS_OK_AFTER": "2"},
                                stdin=b"w\n", tty=True)

        self.assertIn("ASK_WIFI_CALLED", out)
        self.assertIn("PROCEEDED", out)

    # ── the guard ────────────────────────────────────────────────────────

    def test_unattended_consumes_no_stdin(self):
        """⛔ A piped answer file must reach the next question intact.  A
        prompt here would eat it and desync every prompt after, invisibly."""
        out, rc = self.run_gate({"FAKE_EGRESS_OK_AFTER": "99"},
                                stdin=b"NEXT-ANSWER\n")

        self.assertIn("unattended", out)
        self.assertNotEqual(rc, 0)
        self.assertNotIn("re-check", out)

    def test_unattended_does_not_loop_forever(self):
        """One probe, then abort — a non-tty run has nobody to fix anything."""
        out, rc = self.run_gate({"FAKE_EGRESS_OK_AFTER": "99"})
        self.assertEqual(out.count("No working Internet connection"), 1)


if __name__ == "__main__":
    unittest.main()
