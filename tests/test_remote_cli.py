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
from datetime import datetime, timedelta, timezone
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
# The switch's own NOTE line, as sigmond-wizard.sh prints it for off with the tunnel service running.
NOTE = "  NOTE: the tunnel service runs all the same. Someone started it outside this switch; 'off' again stops it.\n"
OFF_OPEN = OFF.replace("  Turn it on:", NOTE + "  Turn it on:")
HOSTKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostHostHostHostHostHostHostHostHostHostHost"
NOW = datetime(2026, 10, 9, 20, 0, 0, tzinfo=timezone.utc)      # the banner tests' clock


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
        self.assertEqual(argv[argv.index("-i") + 1], str(self.key))
        for opt in ("BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=yes",
                    f"UserKnownHostsFile={self.known}", "GlobalKnownHostsFile=/dev/null"):
            self.assertIn(opt, argv)
        self.assertEqual(kw["stdin"], subprocess.DEVNULL)
        self.assertEqual(kw["timeout"], 60)

    def test_ssh_reads_no_config_file_and_keeps_its_timeout_and_quiet(self):
        # A `Host *` block in an ssh config would add an identity file, SendEnv and GSSAPI
        # to this call.  -F /dev/null stops every config file, user and system.
        self._pair()
        self._run("status", stdout=OFF)
        argv = self.calls[0][0]
        self.assertEqual(argv[:4], ["ssh", "-T", "-F", "/dev/null"])
        self.assertIn("ConnectTimeout=8", argv)
        self.assertIn("LogLevel=ERROR", argv)

    def test_the_hosts_answer_is_decoded_leniently(self):
        # A non-UTF-8 byte in the answer must not raise inside subprocess.run.
        self._pair()
        self._run("status", stdout=OFF)
        self.assertEqual(self.calls[0][1]["errors"], "replace")

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
        self.assertIn("sigmond-setup --rac-on", errs)
        self.assertNotIn("sudo smd", errs)

    def test_the_not_paired_message_leads_with_the_switch_that_works_on_any_host(self):
        # The reader took `smd update` before the host reached v3.71.  That host knows no
        # --pair-vm: it answers "already configured" and pairs nothing.
        self.mark.write_text("v3.70\n")
        _, _, errs = self._run("on")
        self.assertIn("v3.71", errs)
        self.assertIn("already configured", errs)
        self.assertLess(errs.index("sigmond-setup --rac-off"), errs.index("sigmond-setup --pair-vm"))
        self.assertLess(errs.index("sigmond-setup --rac-on"), errs.index("sigmond-setup --pair-vm"))
        self.assertLess(errs.index("sigmond-setup --pair-vm"), errs.index("already configured"))
        self.assertNotIn("within", errs)                 # no promise about when a host pairs

    def test_a_station_without_a_proxmox_host_is_pointed_at_its_own_tunnel(self):
        code, _, errs = self._run("status")
        self.assertEqual(code, remote.EXIT_NOT_PAIRED)
        self.assertIn("smd admin rac status", errs)
        self.assertNotIn("--pair-vm", errs)

    def test_a_key_without_a_usable_known_hosts_line_counts_as_not_paired(self):
        self._pair()
        for bad in ("", "# comment only\n", "$(reboot) ssh-ed25519 AAAA\n",
                    "host.example.org ssh-ed25519 AAAA\n", "10.99.0.1\n",
                    "cafe.de ssh-ed25519 AAAA\n", "beef ssh-ed25519 AAAA\n",
                    "... ssh-ed25519 AAAA\n", "10.99.0.1 foo bar\n",
                    "-oProxyCommand=x ssh-ed25519 AAAA\n",
                    "# a comment first\n10.99.0.1 ssh-ed25519 AAAA\n"):
            self.known.write_text(bad)
            self.assertEqual(self._run("status")[0], remote.EXIT_NOT_PAIRED, bad)
        self.assertEqual(self.calls, [])

    def test_a_known_hosts_file_that_cannot_be_decoded_counts_as_not_paired(self):
        self._pair()
        self.known.write_bytes(b"10.99.0.1 ssh-ed25519 AAAA\xff\xfe\n")
        self.assertIsNone(remote.paired_host())
        self.assertEqual(self._run("status")[0], remote.EXIT_NOT_PAIRED)
        self.assertEqual(self.calls, [])

    def test_an_address_and_a_key_type_make_a_pairing_ipv4_or_ipv6(self):
        for line, host in (("10.99.0.1 ssh-ed25519 AAAA\n", "10.99.0.1"),
                           ("10.99.0.1 ssh-ed25519 AAAA host-comment\n", "10.99.0.1"),
                           ("fd99::1 ssh-ed25519 AAAA\n", "fd99::1"),
                           ("10.99.0.1 ecdsa-sha2-nistp256 AAAA\n", "10.99.0.1")):
            self.known.write_text(line)
            self.assertEqual(remote.paired_host(), host, line)

    # -- the host does not answer -------------------------------------------

    def test_a_changed_host_key_says_the_host_pairs_again_by_itself(self):
        self._pair()
        code, out, errs = self._run("status", rc=255, stderr=(
            "@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\n"
            "Host key verification failed.\n"))
        self.assertEqual(code, remote.EXIT_UNREACHABLE)
        self.assertIn("shows an sshd key this VM was not paired with", errs)
        self.assertNotIn("within minutes", errs)          # true only for a host at v3.71 whose agent answers
        self.assertIn("v3.71", errs)
        self.assertIn("sshd key file changes", errs)
        self.assertIn("sigmond-setup --pair-vm", errs)

    def test_a_changed_host_key_that_outlasts_a_pairing_names_the_sshd_restart(self):
        # The pairing pins the key in the host's key FILE.  A running sshd keeps
        # serving the key it loaded at its start, so after the file changed the
        # two differ until sshd restarts, and pairing again cannot help.
        self._pair()
        _, _, errs = self._run("status", rc=255, stderr="Host key verification failed.\n")
        self.assertIn("shows an sshd key this VM was not paired with", errs)
        self.assertEqual(errs.count("sigmond-setup --pair-vm"), 2)
        first = errs.index("sigmond-setup --pair-vm")
        restart = errs.index("systemctl restart ssh")
        self.assertLess(first, restart)
        self.assertLess(restart, errs.index("sigmond-setup --pair-vm", first + 1))
        self.assertIn("on the Proxmox host", errs[first - 60:restart + 60])
        self.assertNotIn("systemctl restart sshd", errs)     # Debian names the unit ssh.service
        self.assertNotIn("sudo ", errs)

    def test_only_the_changed_key_message_names_the_sshd_restart(self):
        self._pair()
        for stderr in ("sigmond-remote@10.99.0.1: Permission denied (publickey).\n",
                       "ssh: connect to host 10.99.0.1 port 22: No route to host\n"):
            _, _, errs = self._run("status", rc=255, stderr=stderr)
            self.assertNotIn("systemctl restart ssh", errs)

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
        self.assertIn("could not reach the Proxmox host at 10.99.0.1", errs)
        self.assertIn("No route to host", errs)
        self.assertIn("private link", errs)
        self.assertIn("smd remote status", errs)          # the way to ask again

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

    def test_a_zero_status_without_a_state_is_still_a_failure(self):
        self._pair()
        code, out, errs = self._run("status", rc=0, stdout="something else entirely\n")
        self.assertEqual(code, 1)
        self.assertIn("gave no state (exit 0)", errs)
        self.assertFalse(self.cache.exists())

    def test_a_sudo_failure_on_the_host_names_the_pairing_as_the_cure(self):
        self._pair()
        for last in ("sudo: a password is required", "sudo: not found",
                     "sh: 1: sudo: not found"):
            code, _, errs = self._run("status", rc=1, stderr=f"noise first\n{last}\n")
            self.assertEqual(code, 1, last)
            self.assertIn("pairing account could not run the switch", errs, last)
            self.assertIn("sigmond-setup --pair-vm", errs, last)
            self.assertNotIn("gave no state", errs, last)
        self.assertFalse(self.cache.exists())

    def test_a_line_that_merely_mentions_sudo_is_no_sudo_failure(self):
        self._pair()
        code, _, errs = self._run("status", rc=1, stderr="the switch uses sudo to act\n")
        self.assertEqual(code, 1)
        self.assertIn("gave no state (exit 1)", errs)
        self.assertNotIn("pairing account", errs)

    def test_a_connection_that_ends_after_the_state_line_is_unreachable_but_kept(self):
        self._pair()
        code, out, errs = self._run("on", rc=255, stdout=ON,
                                    stderr="Connection to 10.99.0.1 closed by remote host.\n")
        self.assertEqual(code, remote.EXIT_UNREACHABLE)
        self.assertEqual(out, ON)                        # the state it did print stands
        self.assertIn("ended before its switch finished", errs)
        self.assertEqual(json.loads(self.cache.read_text())["state"], "on")

    # -- a cache write that fails ------------------------------------------------

    def test_a_cache_write_that_fails_leaves_no_temp_file_and_says_so(self):
        self._pair()
        self.cache.parent.mkdir(parents=True)
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            code, out, errs = self._run("status", rc=0, stdout=OFF)
        self.assertEqual(code, 0)                        # the host's status, not the cache's
        self.assertEqual(out, OFF)
        self.assertEqual(list(self.cache.parent.glob(".remote-access.*")), [])
        self.assertFalse(self.cache.exists())
        self.assertIn("banner keeps its older answer", errs)
        self.assertIn("disk full", errs)

    # -- what the login banner reads -------------------------------------------

    def test_a_recognised_answer_is_kept_for_the_banner(self):
        self._pair()
        for answer, state in ((ON, "on"), (OFF, "off"), (DOWN, "down")):
            self._run("status", stdout=answer)
            doc = json.loads(self.cache.read_text())
            self.assertEqual(doc["state"], state)
            self.assertRegex(doc["checked_at"], r"^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(oct(self.cache.stat().st_mode & 0o777), "0o644")

    # -- off, and the tunnel service still runs ----------------------------------

    def test_the_note_this_verb_looks_for_is_the_switchs_own_line(self):
        wizard = (REPO / "scripts" / "proxmox" / "sigmond-wizard.sh").read_text()
        self.assertTrue(NOTE.startswith("  " + remote.TUNNEL_RUNS_NOTE))
        self.assertIn('echo "' + NOTE.rstrip("\n") + '"', wizard)

    def test_an_off_with_the_switchs_note_is_kept_as_a_tunnel_that_still_runs(self):
        self._pair()
        code, out, _ = self._run("off", rc=4, stdout=OFF_OPEN)
        self.assertEqual((code, out), (4, OFF_OPEN))
        doc = json.loads(self.cache.read_text())
        self.assertEqual(doc["state"], "off")
        self.assertIs(doc["tunnel_runs"], True)

    def test_every_other_answer_leaves_the_key_out(self):
        self._pair()
        for answer in (OFF, ON, DOWN,
                       ON + NOTE,                                        # the note belongs to off alone
                       OFF + "  it says NOTE: the tunnel service runs all the same. inside a line\n"):
            self._run("status", stdout=answer)
            self.assertEqual(sorted(json.loads(self.cache.read_text())), ["checked_at", "state"], answer)

    def test_the_banner_says_so_when_off_stands_over_a_tunnel_that_still_runs(self):
        self._pair()
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text(json.dumps({"state": "off", "checked_at": "2026-10-09T18:22:05Z",
                                          "tunnel_runs": True}))
        self.assertEqual(self._motd(),
                         "Remote access: OFF, but the tunnel service still runs "
                         "(as the host reported 2026-10-09 18:22 UTC).  Close it:  smd remote off\n")

    def test_the_banner_reads_a_missing_or_odd_flag_as_false(self):
        # Any member of group `sigmond` can write the cache file: only a JSON true counts.
        self._pair()
        self.cache.parent.mkdir(parents=True)
        plain = "Remote access: OFF (as the host reported 2026-10-09 18:22 UTC).  Turn it on:  smd remote on\n"
        for flag in ({}, {"tunnel_runs": False}, {"tunnel_runs": "true"}, {"tunnel_runs": 1},
                     {"tunnel_runs": None}, {"tunnel_runs": ["x"]}):
            self.cache.write_text(json.dumps({"state": "off", "checked_at": "2026-10-09T18:22:05Z", **flag}))
            self.assertEqual(self._motd(), plain, flag)

    def test_the_flag_changes_no_other_banner_line(self):
        self._pair()
        self.cache.parent.mkdir(parents=True)
        for state, begins in (("on", "Remote access: on (as"), ("down", "Remote access: wanted on, NOT UP (as")):
            self.cache.write_text(json.dumps({"state": state, "checked_at": "2026-10-09T18:22:05Z",
                                              "tunnel_runs": True}))
            line = self._motd()
            self.assertTrue(line.startswith(begins), line)
            self.assertNotIn("still runs", line)
        # An answer too old to trust says how old it is, and claims nothing about the tunnel.
        self.cache.write_text(json.dumps({"state": "off", "checked_at": "2026-10-09T18:22:05Z",
                                          "tunnel_runs": True}))
        line = self._motd(datetime(2026, 10, 10, 2, 22, 5, tzinfo=timezone.utc))
        self.assertIn("8 hours old", line)
        self.assertNotIn("still runs", line)

    def test_the_two_writers_of_the_cache_spell_the_key_alike(self):
        wizard = (REPO / "scripts" / "proxmox" / "sigmond-wizard.sh").read_text()
        self.assertIn('"tunnel_runs": true}', wizard)
        self._pair()
        self._run("off", rc=4, stdout=OFF_OPEN)
        self.assertIn('"tunnel_runs": true', self.cache.read_text())

    def test_the_state_reads_from_the_exact_line_wherever_it_stands(self):
        self.assertEqual(remote.state_of(ON), "on")                  # after the waiting line
        self.assertEqual(remote.state_of("  Remote access: on\n"), None)
        self.assertEqual(remote.state_of("Remote access: on \n"), None)
        self.assertEqual(remote.state_of("Remote access: online\n"), None)
        self.assertEqual(remote.state_of(""), None)

    def _motd(self, now=NOW):
        # The clock stands still at `now`, so an answer on file keeps the age the test gives it.
        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return now
        out = io.StringIO()
        with mock.patch.object(remote, "datetime", Frozen), contextlib.redirect_stdout(out):
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

    def test_the_banner_takes_only_a_state_and_a_time_it_can_read(self):
        # Any member of group `sigmond` can write the cache file.
        self._pair()
        self.cache.parent.mkdir(parents=True)
        for doc in ({"state": "unknown", "checked_at": "2026-10-09T18:22:05Z"},
                    {"state": None, "checked_at": "2026-10-09T18:22:05Z"},
                    {"state": "on", "checked_at": "yesterday"},
                    {"state": "on", "checked_at": "2026-10-09 18:22:05"},
                    {"state": "on", "checked_at": 20261009},
                    {"state": "on", "checked_at": None},
                    {"state": ["on"], "checked_at": "2026-10-09T18:22:05Z"}):
            self.cache.write_text(json.dumps(doc))
            self.assertIn("state not read yet", self._motd(), doc)

    def test_an_answer_up_to_seven_hours_old_prints_as_a_fresh_one(self):
        self._pair()
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text(json.dumps({"state": "on", "checked_at": "2026-10-09T18:22:05Z"}))
        want = "Remote access: on (as the host reported 2026-10-09 18:22 UTC).  Turn it off:  smd remote off\n"
        then = datetime(2026, 10, 9, 18, 22, 5, tzinfo=timezone.utc)
        for hours in (0, 6, 7):                           # seven hours exactly still counts
            self.assertEqual(self._motd(then + timedelta(hours=hours)), want, hours)

    def test_an_answer_over_seven_hours_old_says_how_old_and_how_to_ask_again(self):
        self._pair()
        self.cache.parent.mkdir(parents=True)
        for state, word in (("on", "on"), ("off", "OFF"), ("down", "wanted on, NOT UP")):
            self.cache.write_text(json.dumps({"state": state, "checked_at": "2026-10-09T18:22:05Z"}))
            line = self._motd(datetime(2026, 10, 10, 2, 22, 5, tzinfo=timezone.utc))     # 8 h on
            self.assertIn("8 hours old", line)
            self.assertIn(word, line)
            self.assertIn("2026-10-09 18:22 UTC", line)
            self.assertTrue(line.endswith("Ask again:  smd remote status\n"), line)
            self.assertEqual(line.count("\n"), 1)                                        # one line
        # whole hours: seven hours and a second is "7", twenty-six hours and a half is "26"
        self.assertIn("7 hours old", self._motd(datetime(2026, 10, 10, 1, 22, 6, tzinfo=timezone.utc)))
        self.assertIn("26 hours old", self._motd(datetime(2026, 10, 10, 20, 52, 5, tzinfo=timezone.utc)))

    def test_an_answer_dated_ahead_of_the_clock_is_marked(self):
        self._pair()
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text(json.dumps({"state": "on", "checked_at": "2026-10-09T18:22:05Z"}))
        line = self._motd(datetime(2026, 10, 9, 10, 0, 0, tzinfo=timezone.utc))
        self.assertIn("future", line)
        self.assertIn("2026-10-09 18:22 UTC", line)
        self.assertTrue(line.endswith("Ask again:  smd remote status\n"), line)

    def test_cmd_motd_takes_the_time_it_is_given(self):
        self._pair()
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text(json.dumps({"state": "off", "checked_at": "2026-10-09T18:22:05Z"}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(remote.cmd_motd(now=datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)), 0)
        self.assertIn("53 hours old", out.getvalue())

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
