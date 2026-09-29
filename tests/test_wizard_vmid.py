"""The wizard must find its own decoder VM.

⛔ Why this exists.  `VMID="${SIGMOND_VMID:-120}"` is correct only when the
wizard is started by firstboot's systemd units, which set
`Environment=SIGMOND_VMID=<n>`.  An operator running `sigmond-setup
--reconfigure` from a root shell has no such environment, so the wizard fell
back to 120 — and on AI6VN-PM, whose decoder VM is 100, it then waited five
minutes for a VM that does not exist while printing nothing:

    root@AI6VN-PM:~# sigmond-setup --reconfigure
    [wizard] waiting for decoder VM 120 and its guest agent...

rob reported that as a hang, which is exactly what it looks like.

The resolver prefers the most authoritative source available and, crucially,
REFUSES TO GUESS when the host has more than one VM — a wrong guess would
reconfigure somebody else's VM, which is worse than stopping.
"""
import subprocess
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"


def resolver() -> str:
    """The resolve_vmid function exactly as shipped."""
    lines, keep, out = WIZARD.read_text().splitlines(True), False, []
    for ln in lines:
        if ln.startswith("resolve_vmid(){"):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, "resolve_vmid not found in the wizard"
    return "".join(out)


def run(body: str, env_unit: str = "/nonexistent", vmid_env=None) -> str:
    script = textwrap.dedent(f"""
        set -u
        export SIGMOND_IMPORT_UNIT={env_unit}
        {'export SIGMOND_VMID=' + vmid_env if vmid_env else 'unset SIGMOND_VMID || true'}
        {body}
        {resolver()}
        resolve_vmid
    """)
    p = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, timeout=30)
    return p.stdout.strip()


NO_QM = "qm(){ return 1; }"
ONE_VM = r"qm(){ printf 'VMID NAME STATUS\n 100 AI6VN running\n'; }"
TWO_VMS = r"qm(){ printf 'VMID NAME STATUS\n 100 A running\n 101 B running\n'; }"


class ResolveVmidTests(unittest.TestCase):
    def test_explicit_env_wins(self):
        self.assertEqual(run(ONE_VM, vmid_env="777"), "777")

    def test_reads_what_firstboot_recorded(self, ):
        with _unit("Environment=SIGMOND_VMID=100\n") as u:
            self.assertEqual(run(NO_QM, env_unit=u), "100")

    def test_unit_beats_a_single_vm_listing(self):
        """The recorded value is authoritative even if qm list disagrees."""
        with _unit("Environment=SIGMOND_VMID=131\n") as u:
            self.assertEqual(run(ONE_VM, env_unit=u), "131")

    def test_falls_back_to_the_only_vm_on_the_host(self):
        self.assertEqual(run(ONE_VM), "100")

    def test_refuses_to_guess_between_two_vms(self):
        """Guessing here would reconfigure the wrong VM."""
        self.assertEqual(run(TWO_VMS), "120")

    def test_last_resort_is_unchanged(self):
        self.assertEqual(run(NO_QM), "120")


class WaitLoopTests(unittest.TestCase):
    """A five-minute wait must not be silent, and must say what to try."""

    def setUp(self):
        self.text = WIZARD.read_text()

    def test_reports_progress_while_waiting(self):
        self.assertIn("still waiting for the guest agent", self.text)

    def test_missing_vm_is_diagnosed_before_the_wait(self):
        # listing the real VMs turns "hangs" into "you want a different id"
        self.assertIn("does not exist on this host", self.text)
        self.assertIn("SIGMOND_VMID=<id> sigmond-setup --reconfigure", self.text)

    def test_stale_agent_channel_is_named_in_the_failure(self):
        """Active inside the guest, 'not running' from the host — seen 09-29."""
        self.assertIn("systemctl restart qemu-guest-agent", self.text)


class _unit:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        import tempfile
        self.d = tempfile.mkdtemp()
        p = Path(self.d) / "sigmond-import.service"
        p.write_text(self.content)
        return str(p)

    def __exit__(self, *a):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
