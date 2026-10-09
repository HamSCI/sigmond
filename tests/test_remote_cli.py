"""`smd remote on|off|status` -- the operator's switch for remote access, in
the decoder VM (tasks/plan-remote-access.md section 2).

The verb asks the Proxmox host and prints its answer.  These tests stand a
recorded `run` in for ssh; no test opens a connection.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from sigmond.commands import remote

REPO = Path(__file__).resolve().parent.parent

ON = ("Waiting up to 60s for the gateway to accept the channels.\n"
      "Remote access: on\n"
      "  The gateway accepted 6 of 6 channels (RAC 507 on gw2.wsprdaemon.org, wsprdaemon (secure)).\n"
      "  Turn it off:  smd remote off   (in the decoder VM)\n")
OFF = ("Remote access: off\n"
       "  The operator holds it off. The registration stays (RAC 507 on gw2.wsprdaemon.org),\n"
       "  Turn it on:   smd remote on    (in the decoder VM)\n")
DOWN = ("Remote access: wanted on, not up\n"
        "  The station has not registered yet: no gateway has answered.\n")
HOSTKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostHostHostHostHostHostHostHostHostHostHost"


class RemoteCliTests(unittest.TestCase):
    def setUp(self):
        d = TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.d = Path(d.name)
        self.key = self.d / "rac-switch.key"
        self.known = self.d / "rac-switch.known_hosts"
        self.cache = self.d / "state" / "remote-access.json"
        self.mark = self.d / "appliance-version"
        for name, val in (("KEY", self.key), ("KNOWN_HOSTS", self.known),
                          ("CACHE", self.cache), ("APPLIANCE_MARK", self.mark)):
            p = mock.patch.object(remote, name, val); p.start(); self.addCleanup(p.stop)
        self.calls = []

    def _pair(self, host="10.99.0.1"):
        self.key.write_text("PRIVATE\n")
        self.known.write_text(f"{host} {HOSTKEY}\n")
        self.mark.write_text("v3.71\n")

    def _run(self, word, rc=0, stdout="", stderr="", raises=None):
        def fake_run(argv, **kw):
            self.calls.append((list(argv), kw))
            if raises:
                raise raises
            return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)
        out, errs = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errs):
            code = remote.cmd_remote(types.SimpleNamespace(remote_command=word), run=fake_run)
        return code, out.getvalue(), errs.getvalue()

    # -- the request ------------------------------------------------------

    def test_status_asks_the_paired_host_with_the_one_key_and_prints_its_answer(self):
        self._pair()
        code, out, _ = self._run("status", rc=0, stdout=OFF)
        self.assertEqual(code, 0)
        self.assertEqual(out, OFF)
        argv, kw = self.calls[0]
        self.assertEqual(argv[-2:], ["sigmond-remote@10.99.0.1", "status"])
        self.assertEqual(argv[:4], ["ssh", "-T", "-i", str(self.key)])
        for opt in ("BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=yes",
                    f"UserKnownHostsFile={self.known}", "GlobalKnownHostsFile=/dev/null"):
            self.assertIn(opt, argv)
        self.assertEqual(kw["stdin"], subprocess.DEVNULL)
        self.assertEqual(kw["timeout"], 60)

    def test_a_bare_smd_remote_means_status(self):
        self._pair()
        self._run(None, stdout=OFF)
        self.assertEqual(self.calls[0][0][-1], "status")

    def test_each_word_travels_as_typed_and_nothing_else_does(self):
        self._pair()
        for word in ("on", "off", "status"):
            self._run(word, stdout=ON)
        self.assertEqual([c[0][-1] for c in self.calls], ["on", "off", "status"])
        code, _, errs = self._run("restart")
        self.assertEqual(code, 2)
        self.assertEqual(len(self.calls), 3)             # nothing was sent
        self.assertIn("on, off or status", errs)

    def test_on_gets_room_to_register_and_wait(self):
        self._pair()
        _, _, errs = self._run("on", stdout=ON)
        self.assertEqual(self.calls[0][1]["timeout"], 600)
        self.assertIn("this can take a minute", errs)

    def test_the_hosts_exit_code_comes_back(self):
        self._pair()
        self.assertEqual(self._run("status", rc=3, stdout=DOWN)[0], 3)
        self.assertEqual(self._run("off", rc=4, stdout=OFF)[0], 4)

    def test_the_host_address_comes_from_the_pairing_never_from_a_default(self):
        self._pair(host="10.99.7.1")
        self._run("status", stdout=OFF)
        self.assertEqual(self.calls[0][0][-2], "sigmond-remote@10.99.7.1")

    # -- not paired --------------------------------------------------------

    def test_an_appliance_vm_without_a_pairing_names_the_host_command(self):
        self.mark.write_text("v3.70\n")
        code, out, errs = self._run("off")
        self.assertEqual(code, remote.EXIT_NOT_PAIRED)
        self.assertEqual(self.calls, [])
        self.assertIn("holds no pairing with its Proxmox host", errs)
        self.assertIn("sigmond-setup --pair-vm", errs)
        self.assertIn("sigmond-setup --rac-off", errs)   # the switch that works meanwhile
        self.assertNotIn("sudo smd", errs)

    def test_a_station_without_a_proxmox_host_is_pointed_at_its_own_tunnel(self):
        code, _, errs = self._run("status")
        self.assertEqual(code, remote.EXIT_NOT_PAIRED)
        self.assertIn("smd admin rac status", errs)
        self.assertNotIn("--pair-vm", errs)

    def test_a_key_without_a_usable_known_hosts_line_counts_as_not_paired(self):
        self._pair()
        for bad in ("", "# comment only\n", "$(reboot) ssh-ed25519 AAAA\n",
                    "host.example.org ssh-ed25519 AAAA\n", "10.99.0.1\n"):
            self.known.write_text(bad)
            self.assertEqual(self._run("status")[0], remote.EXIT_NOT_PAIRED, bad)
        self.assertEqual(self.calls, [])

    # -- the host does not answer -------------------------------------------

    def test_a_changed_host_key_says_the_host_pairs_again_by_itself(self):
        self._pair()
        code, out, errs = self._run("status", rc=255, stderr=(
            "@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\n"
            "Host key verification failed.\n"))
        self.assertEqual(code, remote.EXIT_UNREACHABLE)
        self.assertIn("an sshd key this VM was not paired with", errs)
        self.assertIn("pairs again by itself within minutes", errs)
        self.assertIn("sigmond-setup --pair-vm", errs)

    def test_a_refused_key_names_the_repair(self):
        self._pair()
        code, _, errs = self._run("on", rc=255,
                                  stderr="sigmond-remote@10.99.0.1: Permission denied (publickey).\n")
        self.assertEqual(code, remote.EXIT_UNREACHABLE)
        self.assertIn("did not accept this VM's pairing key", errs)

    def test_a_host_that_is_down_says_so_and_names_the_link(self):
        self._pair()
        code, _, errs = self._run("status", rc=255,
                                  stderr="ssh: connect to host 10.99.0.1 port 22: No route to host\n")
        self.assertEqual(code, remote.EXIT_UNREACHABLE)
        self.assertIn("No route to host", errs)
        self.assertIn("private link", errs)

    def test_a_host_that_never_answers_times_out_with_a_next_step(self):
        self._pair()
        code, _, errs = self._run("off", raises=subprocess.TimeoutExpired(["ssh"], 120))
        self.assertEqual(code, remote.EXIT_UNREACHABLE)
        self.assertIn("gave no answer within 120 s", errs)
        self.assertIn("smd remote status", errs)

    def test_a_refusal_from_the_switch_is_not_mistaken_for_a_state(self):
        self._pair()
        code, out, errs = self._run("status", rc=2, stderr=(
            "sigmond-rac-switch: refused. It takes exactly one word: on, off or status.\n"))
        self.assertEqual(code, 2)
        self.assertIn("gave no state (exit 2)", errs)
        self.assertFalse(self.cache.exists())

    # -- what the login banner reads -------------------------------------------

    def test_a_recognised_answer_is_kept_for_the_banner(self):
        self._pair()
        for answer, state in ((ON, "on"), (OFF, "off"), (DOWN, "down")):
            self._run("status", stdout=answer)
            doc = json.loads(self.cache.read_text())
            self.assertEqual(doc["state"], state)
            self.assertRegex(doc["checked_at"], r"^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(oct(self.cache.stat().st_mode & 0o777), "0o644")

    def test_the_state_reads_from_the_exact_line_wherever_it_stands(self):
        self.assertEqual(remote.state_of(ON), "on")                  # after the waiting line
        self.assertEqual(remote.state_of("  Remote access: on\n"), None)
        self.assertEqual(remote.state_of("Remote access: on \n"), None)
        self.assertEqual(remote.state_of("Remote access: online\n"), None)
        self.assertEqual(remote.state_of(""), None)

    def _motd(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = remote.cmd_remote(types.SimpleNamespace(remote_command="motd"),
                                     run=lambda *a, **k: self.fail("the banner must never ask the host"))
        self.assertEqual(code, 0)
        return out.getvalue()

    def test_the_banner_prints_nothing_on_a_vm_without_a_pairing(self):
        self.assertEqual(self._motd(), "")

    def test_the_banner_says_the_state_its_age_and_the_command(self):
        self._pair()
        self.assertEqual(self._motd(),
                         "Remote access: state not read yet.  Check it:  smd remote status\n")
        self.cache.parent.mkdir(parents=True)
        for state, want in (
            ("on", "Remote access: on (as the host reported 2026-10-09 18:22 UTC).  Turn it off:  smd remote off\n"),
            ("off", "Remote access: OFF (as the host reported 2026-10-09 18:22 UTC).  Turn it on:  smd remote on\n"),
            ("down", "Remote access: wanted on, NOT UP (as the host reported 2026-10-09 18:22 UTC).  Details:  smd remote status\n"),
        ):
            self.cache.write_text(json.dumps({"state": state, "checked_at": "2026-10-09T18:22:05Z"}))
            self.assertEqual(self._motd(), want)

    def test_the_banner_survives_a_cache_it_cannot_read(self):
        self._pair()
        self.cache.parent.mkdir(parents=True)
        for junk in ("", "{", "[]", '{"state": "on"}'):
            self.cache.write_text(junk)
            self.assertIn("state not read yet", self._motd())

    def test_the_banner_reads_what_the_hosts_pairing_writes(self):
        # sigmond-wizard.sh rac_pair_verify_script writes the same two keys.
        wizard = (REPO / "scripts" / "proxmox" / "sigmond-wizard.sh").read_text()
        self.assertIn("""printf '{"state": "%s", "checked_at": "%s"}\\n'""", wizard)
        self.assertIn("C=/var/lib/sigmond/remote-access.json", wizard)


class RemoteWiringTests(unittest.TestCase):
    """The verb in bin/smd: its words, and which of them elevate."""

    def _smd(self, *args):
        env = dict(os.environ, PYTHONPATH=str(REPO / "lib"), SIGMOND_NO_VENV_REEXEC="1")
        return subprocess.run([sys.executable, str(REPO / "bin" / "smd"), *args],
                              capture_output=True, text=True, env=env, timeout=60)

    def test_help_lists_the_three_words(self):
        out = self._smd("remote", "--help").stdout
        for word in ("status", "off", "on"):
            self.assertIn(word, out)
        self.assertIn("remote     remote access for the fleet admin: status | off | on",
                      self._smd("--help").stdout)

    def test_the_banner_word_runs_unprivileged_and_prints_nothing_unpaired(self):
        r = self._smd("remote", "motd")
        self.assertEqual(r.returncode, 0, r.stderr)
        if not remote.KEY.exists():
            self.assertEqual(r.stdout, "")

    def test_every_other_word_elevates_because_the_key_is_roots(self):
        text = (REPO / "bin" / "smd").read_text()
        i = text.index("if args.command == 'remote':")
        branch = text[i:text.index("if args.command == 'upload':", i)]
        self.assertIn("if verb != 'motd' and _need_root(f'remote {verb}'):", branch)
        self.assertIn("from sigmond.commands.remote import cmd_remote", branch)

    def test_the_constants_match_the_hosts_side(self):
        wizard = (REPO / "scripts" / "proxmox" / "sigmond-wizard.sh").read_text()
        self.assertIn(f"RAC_PAIR_USER={remote.HOST_ACCOUNT}\n", wizard)
        self.assertIn("K=\\$D/rac-switch.key\n", wizard)
        self.assertIn('"\\$D/rac-switch.known_hosts"', wizard)
        for line in remote.STATE_LINES:
            self.assertIn(f'echo "{line}"', wizard)


if __name__ == "__main__":
    unittest.main()
