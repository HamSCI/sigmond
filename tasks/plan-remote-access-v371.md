# v3.71 — remote access that every station sets up, and its operator can switch: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every station registers for remote access when it installs and keeps its tunnel on by default; its operator
turns the tunnel off and on from the decoder VM with `smd remote`; the station and the fleet both show which state holds.

**Architecture:** One script on the Proxmox host, `sigmond-rac-switch`, holds the only code that turns the tunnel on or
off, and it takes exactly one word: `on`, `off` or `status`. The wizard registers without asking, through a step that
also runs alone and that a timer retries. It then pairs the decoder VM with the host by one ssh key that can run only
that script, and `smd remote` in the VM asks through that key. The console panel, the VM's login banner, the host's
heartbeat and the fleet board all read the state the switch reports.

**Tech Stack:** bash (the wizard, the switch script, the console panel, the rigs), Python 3.11 standard library (`smd`,
`pm-align`, `pm-heartbeat`, the fleet board), pytest, OpenSSH (a key line with `restrict` and a forced command), sudo,
systemd units.

**Spec:** `sigmond/tasks/plan-remote-access.md` — the design Michael Hauan agreed on 2026-10-09. Sections 1 to 5 of
its "Design", "Decided on review", and phases 1 to 5. Research map behind the files and lines cited here:
`sigmond/.superpowers/research/v371-remote-access-map.json`.

## Global Constraints

- **Decided, and not reopened here** (spec, "Decided on review" and "Why"): every install registers, with no way to
  skip. The tunnel starts on, for every station class. The wizard carries no remote-access question and no option.
  `on` carries no time limit. The operator's command reads `smd remote on|off|status`.
- **One switch.** `/usr/local/sbin/sigmond-rac-switch` alone enables, disables, starts or stops
  `sigmond-rac-host.service` and the four relay sockets. `sigmond-setup --rac-on` and `--rac-off` run that script.
  Registration may `try-restart` a tunnel that already runs, and nothing more.
- **The switch reads its word strictly.** It accepts exactly `on`, `off` or `status`, and refuses everything else,
  extra words included, without printing what it was asked.
- **The VM asks; the host alone acts.** One key in the VM. One host account whose key line carries `restrict` and a
  forced command. One sudoers rule, for exactly the switch script with no argument. No code in the VM may create or
  repair the host's half of the pairing.
- **Stations already in the field whose owner said No stay as they stand** until the owner types `smd remote on`.
  No task registers such a station, records a wish for it, or starts anything on it.
- **No non-ASCII character in any argument sent through `qm guest exec` or `gexec`.** Proxmox sends such a request
  short, and the guest agent can then go deaf. Keys and scripts travel as ASCII, base64 where needed. Output that
  comes back through the guest agent arrives double-encoded: match it on ASCII only, under `LC_ALL=C`.
- **The guest agent's 128 MB cap covers every command run through it**, and one `smd` needs about 55 MB. The pairing
  therefore runs `ssh-keygen` and `ssh` through the agent, never `smd`.
- **The wizard keeps what every pm-align in the field reads out of it.** A station runs the pm-align its last
  alignment installed. That copy refuses a whole release unless the release's wizard still writes, from heredocs,
  `/usr/local/bin/sigmond-vm` and `/usr/local/lib/sigmond/vm-port-relay.py` (quoted), and the two relay unit
  templates `sigmond-vm-$RNAME-relay.socket` and `sigmond-vm-$RNAME-relay@.service` (unquoted). `firstboot-v3.sh`
  must likewise keep its four: `sigmond-net.sh`, `sigmond-issue`, `sigmond-issue.service`, `sigmond-issue.timer`.
  Task 6 and Task 9 each pin their half.
- **The fleet board's server learns the `rac` block before any host sends it.** An unknown block name quarantines
  the whole heartbeat, and a quarantined host looks silent. Task 12 deploys wd30 before any image or alignment.
- **Host-side files reach a station through the image or through `pm-align`, never through `smd update`.** A
  station whose VM took `smd update` and whose host did not must keep working: Tasks 5, 7 and 9 each cover that.
- sigmond's core (`smd`, `lib/sigmond/`) stays Python 3.11 standard library only. `pm-align.py`, `pm-heartbeat.py`
  and the fleet board import nothing from sigmond.
- Never tell anyone to run `sudo smd`. `smd` elevates itself and refuses to run under sudo.
- Develop on `main` in each repo, with one commit per task and per repo. Never push without Michael's explicit go.
  **No v3.71 code may reach `origin/main` of sigmond or sigmond-appliance before AC0G-B4 and AC0G-ND have taken
  v3.70**, because `smd update` on a station follows `origin/main`.
- Every commit message ends with the two attribution lines the executing session's reminder supplies
  (`Co-Authored-By:` and `Claude-Session:`). Once per repo, before the first commit, write those two lines into a
  file git never tracks, and check it:
  ```bash
  cd /home/mjh/hamsci/repos/sigmond            # then the same in ../sigmond-appliance
  printf '%s\n' 'Co-Authored-By: (the line your reminder gives)' 'Claude-Session: (the line your reminder gives)' > .git/ATTRIBUTION
  grep -c -e '^Co-Authored-By: .*<.*@.*>$' -e '^Claude-Session: https://' .git/ATTRIBUTION      # 2
  ```
  Put your reminder's own two lines where the brackets stand: the check prints 2 only for real ones. Each commit
  step below reads that file. A commit step that finds it missing stops.
- Tests first. Watch each test fail for the right reason before you write the code that satisfies it, and measure
  each test's power by mutation where a step says so.
- Test runners: sigmond `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest <file> -q` (baseline 3498 passed,
  1 warning, 65 subtests passed at 7fe58c7); sigmond-appliance `bash -n` on each changed script plus its hermetic
  `test-*.sh` (at a0d7268 each prints its own PASS line; Task 9 lists them).
- Tasks 1 to 11 touch no station, no rig and no remote host. Every step of Task 12 reaches outside the devbox:
  each one waits for Michael's go, and every station step starts with a claude-bus read and post.
- Prose in messages and documents: short sentences, active voice, few uses of "to be", times in UTC.
- Line numbers in a task name the file as it stands at that task's base. Find each edit by its quoted anchor. The
  wizard at Task 1's base hashes `becb1285…` (`sha256sum scripts/proxmox/sigmond-wizard.sh`); if yours differs,
  the anchors still decide.

## Review Focus

1. **An admin who came in through the tunnel types `smd remote off`.** The tunnel stops under the session that
   asked, and the terminal goes away mid-run. The host must still finish: wish recorded, relays and tunnel
   disabled. Pinned by Task 2 (`test_off_finishes_when_its_terminal_has_gone_away`).
2. **A station that installs with no internet, or while the gateway is down.** The install must finish, the wish
   must read on, and the host must keep trying quietly: one journal line per reason, never one per try. An operator
   who turns the switch off in the meantime must stay off once the host registers. Pinned by Task 3
   (`test_a_first_install_with_no_gateway_still_finishes`,
   `test_a_try_that_finds_no_gateway_says_so_once_and_leaves_the_timer_armed`,
   `test_a_try_that_registers_stops_there_when_the_operator_holds_it_off`).
3. **The host's sshd key changes after the pairing** (an identity restore did this to AC0G-B4). The VM then holds a
   stale pin, and `smd remote` would fail until someone noticed. The host must re-assert both halves by itself.
   Pinned by Task 4 (`test_a_second_run_keeps_the_key_and_takes_up_a_new_host_key`,
   `test_the_pairing_is_re_asserted_at_boot_and_when_the_hosts_sshd_key_changes`) and on the rig by Task 10
   (check 5).
4. **The decoder VM answers the host with something other than one public key**: two lines, trailing options,
   the wrong key type, bytes mangled by the guest agent. Nothing but one restricted key line may reach the host's
   `authorized_keys`; a second line there would stand as an unrestricted key. Pinned by Task 4
   (`test_anything_but_one_ed25519_public_key_writes_no_key_line`,
   `test_only_the_keys_two_fields_reach_the_key_line`).
5. **A station in the field between its `smd update` and its `pm-align`.** The VM knows `smd remote`; the host
   does not know the switch yet, or holds only the files an older pm-align delivers. `smd remote` must name the
   one host command; the panel must keep its old text; the host's heartbeat must claim no `rac` block; and the
   older pm-align must still accept the release. Pinned by Task 5
   (`test_an_appliance_vm_without_a_pairing_names_the_host_command`), Task 9 (the scenes without a switch
   script), Task 7 (`test_a_host_without_the_switch_script_claims_no_rac_block`) and Task 6
   (`test_the_real_wizard_still_satisfies_every_pm_align_in_the_field`).

---

## Rulings where the spec stays silent

The spec says what the software must do. Each line below records a choice this plan made where the spec left room,
with the reason and what it costs if wrong. Michael reviews these before execution starts.

**Reviewed by Michael on 2026-10-09.** The rulings stand. On the three he had to settle: ruling 9 keeps the
dedicated account and sudo; v3.71 ships while the registrar still answers over plain HTTP (ruling 8), and the
gateway's side goes to Rob; and Michael, who stands at AC0G-ND, types the off, the power cycle and the on of
Task 12 Step 8 himself. Execution runs subagent-driven.

1. **The switch script, its units and the pairing live inside the wizard file, as heredocs**, the way `sigmond-vm`
   and `vm-port-relay.py` already do (Task 2). Reason: the wizard is the one host file that every delivery path
   already carries, so any host that holds the v3.71 wizard can install the rest, and `sigmond-setup --rac-off`
   never meets a missing switch. Cost if wrong: a wizard near 3000 lines, and tests that cut the script out of it.
2. **Registration stays in the wizard, as `sigmond-setup --rac-register`** (Task 1). The relay templates must stay
   in the wizard for pm-align's sake, and registration needs them beside it. Cost: none seen.
3. **A station from before v3.71 holds no wish on file, and the switch reads its wish from what stands**:
   registered and enabled means on, anything else off. Nothing adopts a wish for it (Task 2). Cost if wrong: a
   hand-made `systemctl disable` on such a host reads as the owner's off.
4. **A wizard rerun keeps the switch where its operator left it.** A first install turns it on. A rerun on a
   registered station that wants it on registers afresh, as before, because the reporter id may have changed
   (Task 3). Cost if wrong: an owner who wanted `--reconfigure` to turn it back on must type `smd remote on`.
5. **A reflash turns remote access on again.** The wish lives in `/etc/sigmond-appliance/rac-desired`, which no
   identity capture carries. Cost: an owner who keeps it off types `smd remote off` once more after a reflash.
6. **The retry timer fires every 10 minutes and writes one journal line per reason** (Task 3). Cost if wrong: a
   gateway outage ends up to 10 minutes later on a station than it needed to.
7. **A pinned gateway persists** in `/etc/sigmond-appliance/rac-endpoint.env` (Task 1). A registration that runs
   alone carries no environment from the wizard run that pinned it. The rigs rely on this: without it a nested
   install would register with the real gateway. Cost: one more host file to know about.
8. **The registration request can verify TLS; the gateway ladder still names plain HTTP** (Task 1). Both gateways
   serve their registrar over HTTP only, so flipping the ladder needs the gateway first. The spec's "check first
   what the token alone allows" stays undone. Cost: the token still crosses the internet in the clear once per
   registration, as today.
9. **sudo.** Stock Proxmox carries none, and nothing in `firstboot-v3.sh` or `offline-debs/` installs it. The
   image gains it as an offline package (Task 10). On a station installed earlier, `sigmond-setup --pair-vm`
   installs it with apt (Task 4), and pm-align's "never touches a package" gains that one exception (Task 6).
   Cost if wrong: one more package on the hypervisor, and apt on a station in the field.
10. **The key line also carries `from="<the VM's address on the private link>"`** (Task 4). A copy of the key then
    works from nowhere but the VM. Cost: a station whose VM sits on another address must pair again.
11. **The pairing re-asserts itself 4 minutes after every boot, every 6 hours, and whenever the host's sshd key
    file changes** (Task 4). The spec names boot and identity restore; the 6 hours cover a guest agent that stood
    deaf at boot. Cost: four small guest-agent requests a day.
12. **The login banner prints the host's last answer and its time; a login never asks the host** (Tasks 4 and 5).
    Every `smd remote` and every re-assertion refreshes it. Cost: after a change made at the host console the
    banner can lag by up to 6 hours, and it says how old its answer is.
13. **On a station with no Proxmox host, `smd remote` points at `smd admin rac`** and drives nothing (Task 5). The
    spec says it "can" drive the VM-side tunnel. Cost: such an operator learns two commands until someone adds it.
14. **The `rac` block joins the heartbeat as a host-only block** (`HOST_BLOCK_NAMES`), so no decoder VM ever
    reports a block it cannot see. An operator's off reads `VALID`, with a label of its own on the board; a tunnel
    short of channels reads `INCONCLUSIVE`; wanted on and not up reads `INVALID` (Tasks 7 and 8).
15. **The RAC number goes on record when the station registers**, not when the tunnel first proves itself
    (Task 1). A registered station whose tunnel stands off still has a number to show.
16. **Exit codes.** The switch: 0 done, 2 refused, 3 wanted on and not up, 4 off and the tunnel still runs, and 1
    when it could not start its work and changed nothing. `smd remote` passes those through and adds 5 (this VM
    holds no pairing) and 6 (the host did not answer). Cost if wrong: a script that reads these numbers must change.
17. **The update rig places the v3.71 wizard on its v3.70 host by hand** (Task 10), because pm-align reads only a
    blessed release. Cost: the rig proves the pairing of an older host, and not pm-align's own delivery of it.

## What this plan could not verify on the devbox

- **The `sigmond-rac` repo** (the frpc payload, `install-host.sh`, `sigmond-rac-host.service`) has no checkout
  here. The plan relies only on what the spec and the wizard state about it: the unit's name, its
  `ConditionPathExists=/etc/sigmond/frpc-host.toml`, `Restart=always`, `RestartSec=30s`, and frpc's admin API on
  127.0.0.1:7500. Whether `install-host.sh` enables the unit stays unknown, so no task relies on it.
- **No sudo on a Proxmox host** follows from what the repos lack, not from a look at a host. Task 12 checks it.
- **sshd on the Proxmox host**: that it accepts a key for an account whose password field holds `*`, and honours
  `restrict` with a forced command. Task 10's rig checks prove it, on a nested host.
- **systemd's `PathChanged=`** firing when a key file gets replaced by rename. Task 10's check 5 proves it.
- **Which key a running sshd presents after its key file changes and before it restarts.** The pairing pins the
  key on file. If sshd keeps presenting the old key until a restart, `smd remote` fails from the re-assertion
  until sshd restarts or the host reboots, and works from then on. The rig restarts sshd at once, so it cannot
  show this window. Task 12 Step 8 watches for it on AC0G-ND.
- **The rig code of Task 10 has never run.** It parses (`bash -n`), and its two readers have a hermetic test. Every
  other line of it meets a real nested host for the first time in Task 12.
- **apt on the nested host and on stations in the field**, which the pairing of an older host needs for sudo.
- **The gateway's registrar over HTTPS, and what a station's token alone allows.**

## File structure

sigmond:

| File | Task | What it holds after this plan |
|---|---|---|
| `scripts/proxmox/sigmond-wizard.sh` | 1, 2, 3, 4 | Registration as functions and `--rac-register`. `rac_install_host_files`, which writes the switch script and five units from heredocs. `--rac-on`/`--rac-off` as one line each. No remote-access question; `rac_activate`. `--rac-retry`. The pairing, `--pair-vm`. |
| `/usr/local/sbin/sigmond-rac-switch` (a heredoc in the wizard) | 2, 3 | The one switch: `on`, `off`, `status`; the wish file; the state file. |
| `lib/sigmond/commands/remote.py` | 5 | `smd remote on|off|status`, and `motd` for the login banner. |
| `bin/smd` | 5 | The `remote` verb: its parser, its dispatch, its lines of help. |
| `scripts/proxmox/pm-align.py` | 6 | Delivers the switch and its units from a v3.71 wizard; pairs after the files land. |
| `lib/sigmond/heartbeat_schema.py` | 7 | `HOST_BLOCK_NAMES = ("rac",)`, `ALL_BLOCK_NAMES`. |
| `scripts/proxmox/pm-heartbeat.py` | 7 | The `rac` block of the host's heartbeat. |
| `server/heartbeat/fleetboard.py` | 8 | A `rac` column; "off by the operator" as a decision. |
| `docs/operator/remote-access.md` and five neighbours | 11 | The operator's page, rewritten around `smd remote`. |
| `docs/contributor/orchestration.md` | 5 | One row for the new verb. |
| `tests/test_wizard_rac_register.py` | 1 | new |
| `tests/test_rac_switch.py` | 2, 3 | new |
| `tests/test_wizard_remote_default.py` | 3 | new |
| `tests/test_wizard_pair_vm.py` | 4 | new |
| `tests/test_remote_cli.py` | 5 | new |
| `tests/test_docs_remote_access.py` | 11 | new |
| `tests/test_pm_align.py`, `test_pm_heartbeat.py`, `test_heartbeat.py`, `test_fleetboard.py` | 1, 2, 6, 7, 8 | extended |

sigmond-appliance:

| File | Task | What it holds after this plan |
|---|---|---|
| `firstboot-v3.sh` | 9 | The console panel's remote-access block: three states. |
| `test-panel-remote.sh` | 9 | new, hermetic |
| `offline-debs/sudo_*.deb`, `offline-debs/README.md` | 10 | sudo on the stick. |
| `test-nested-v3.sh` | 10 | The wizard's answers without the question; five remote-access checks. |
| `test-update-v3.sh` | 10 | PHASE E-ter: a v3.70 host pairs with one command. |
| `test-remote-rig-lines.sh` | 10 | new, hermetic |
| `INSTALL.md`, `docs/install-page.html`, `QUICKSTART.txt` | 11 | No question; how to turn remote access off and on. |
| `test-instructions-remote.sh` | 11 | new, hermetic |

Files on a station that this plan introduces:

| Where | Path | Written by |
|---|---|---|
| host | `/usr/local/sbin/sigmond-rac-switch` | the wizard, pm-align |
| host | `/etc/sigmond-appliance/rac-desired` (`on` or `off`), `rac-changed` (`<wish> <UTC time> <who>`) | the switch |
| host | `/etc/sigmond-appliance/rac-site`, `rac-endpoint.env` | registration, the wizard |
| host | `/run/sigmond/rac-state` (one `key=value` per line) | the switch, at every run |
| host | `sigmond-rac-register.service`, `.timer`; `sigmond-rac-pair.service`, `.timer`, `.path` | the wizard, pm-align |
| host | account `sigmond-remote`, `/var/lib/sigmond-remote/.ssh/authorized_keys`, `/etc/sudoers.d/sigmond-remote` | the pairing |
| VM | `/etc/sigmond/rac-switch.key`, `.key.pub`, `rac-switch.known_hosts` | the pairing, through the guest agent |
| VM | `/etc/update-motd.d/72-sigmond-remote`, `/var/lib/sigmond/remote-access.json` | the pairing; `smd remote` |

---

### Task 1: registration becomes a step that runs alone (`sigmond-setup --rac-register`)

Today the lines that register a station sit inside the wizard's interactive flow
(`scripts/proxmox/sigmond-wizard.sh`, lines 1941-2400) and read its shell variables. Only a person answering the
wizard can register a station. Spec section 3 moves them "into a step the host can run by itself": the switch script
(Task 2) calls it for a station that never registered, and a timer (Task 3) retries it.

This task moves the gateway ladder and the configuration writer into functions that sit above the wizard's
subcommand dispatch, and adds `--rac-register`. The wizard's own run calls the same functions, so one copy of the
registration exists. Nothing changes for an operator yet: the wizard still asks its question, and still starts and
proves the tunnel itself. Task 2 and Task 3 change those.

Three things change in behaviour, each on purpose:

- **The functions read what a wizard run recorded.** The station's name comes from `$REPORTER` while a wizard
  runs, else from a new mark `rac-site`, else from the reporter id in `.configured`.
- **Registration starts nothing.** It writes the configuration and its marks, and it `try-restart`s the tunnel: a
  tunnel that runs takes up the new configuration, and one that stands stopped stays stopped.
- **The RAC number goes on record at registration** (ruling 15), and the configuration lands by rename under a
  closed umask, since it holds the station's token.

The relay installer stays where it stands and keeps its bytes: pm-align reads those templates out of the wizard
(Global Constraints). It only gains a function wrapper.

**Files:**
- Modify: `sigmond/scripts/proxmox/sigmond-wizard.sh` — the header (lines 24-25); a pin loader before
  `build_rac_tiers` (line 214); the registration functions above `# ── RAC on/off switches` (line 225); the
  `--rac-register` branch (line 226); the activation block (lines 1941-2401)
- Create: `sigmond/tests/test_wizard_rac_register.py`
- Modify: `sigmond/tests/test_pm_align.py` — one static check of the wizard (lines 1527-1536)

**Interfaces:**
- Consumes: the gateway's registrar, unchanged: a POST of `{"site", "pubkey"}`, answered with `user`, `token`,
  `rac`, `server_addr`, `server_port` and `ports.{vm_ssh,vm_web,host_ssh,host_ui}`. The marks under
  `/etc/sigmond-appliance/`: `rac-profile`, `dasi-number`, `.configured`.
- Produces, in `sigmond-wizard.sh`, all defined above the `case "${1:-}" in` dispatch:
  - `rac_register ensure|fresh` — returns 0 once `/etc/sigmond/frpc-host.toml` holds a registration; returns 1
    with the reason in `$RAC_FAIL`. `ensure` leaves a registered host alone. `fresh` registers again, and a failed
    fresh run keeps the old configuration. It holds the lock `/run/sigmond-rac-register.lock` while it runs.
  - `rac_site_name` — prints the station's name as the gateway files it.
  - `rac_payload_ready`, `rac_ladder`, `rac_write_config`, `rac_register_locked` — the parts of `rac_register`.
  - `rac_load_pin`, `rac_save_pin` and the file `/etc/sigmond-appliance/rac-endpoint.env`, which holds lines
    `SIGMOND_RAC_SERVER=`, `SIGMOND_RAC_REGISTRAR=`, `SIGMOND_RAC_TLS=`, `SIGMOND_RAC_FRPS_PORT=`. The wizard reads
    it as data, never as shell.
  - `rac_install_relays` — the relay installer, as a function defined just above the activation block.
  - Variables: `RAC_FRPC`, `RAC_CA`, `RAC_TOML`, `RAC_HOSTKEY`, `RAC_LOCK`, `RAC_RELAY_UNIT`, `RAC_PIN_FILE`,
    `RAC_FAIL`.
  - `sigmond-setup --rac-register` — exit 0 and `registered: RAC #<n> via <tier>`, or exit 1 and
    `not registered: <reason>`. It asks nothing and waits for no VM.
  - New marks: `/etc/sigmond-appliance/rac-site`; `rac-number` now written at registration.
  - `$RAC_FAIL` after a ladder that found nothing reads `no gateway tier answered (tried: <labels>)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wizard_rac_register.py`:

```python
"""Registration with the gateway, as a step that runs alone
(tasks/plan-remote-access.md section 3).

Before v3.71 the registration lines sat inside the wizard's interactive flow
and read its shell variables, so only a person answering the wizard could
register a station.  `sigmond-setup --rac-register` now runs them with no
question and no decoder VM: the switch script and the retry timer both need
that.  These tests run the wizard's own functions, cut out of the shipped
script by name, against a registrar that listens on this machine.
"""
import http.server
import json
import os
import shutil
import stat
import subprocess
import textwrap
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"
TEXT = WIZARD.read_text()

FUNCS = ("build_rac_tiers", "rac_load_pin", "rac_save_pin", "rac_site_name",
         "rac_payload_ready", "rac_ladder", "rac_write_config",
         "rac_register_locked", "rac_register")

PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFake root@test"

REPLY = {"user": "0123456789abcdef", "token": "tok3nTOKEN42", "rac": 507,
         "server_addr": "127.0.0.1", "server_port": 35735,
         "ports": {"vm_ssh": 36307, "vm_web": 46307, "host_ssh": 51307, "host_ui": 56307}}


def shell_func(name: str) -> str:
    out, keep = [], False
    for ln in TEXT.splitlines(True):
        if ln.startswith(f"{name}(){{") or ln.startswith(f"{name}() {{"):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, f"{name} not found in the wizard"
    return "".join(out)


class Registrar:
    """A gateway registrar on 127.0.0.1.  It records every request body."""

    def __init__(self, reply=None, status=200):
        self.bodies = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                outer.bodies.append(json.loads(self.rfile.read(n)))
                body = json.dumps(reply if reply is not None else REPLY).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def registrar():
    r = Registrar()
    yield r
    r.close()


def host(tmp_path, *, tiers, relays=True, frpc=True, configured="AC0G/B4 EM38ww 2026-10-09"):
    """A scratch Proxmox host: marks, key, payload and a systemctl that only
    writes down what it was asked."""
    mark = tmp_path / "marks"
    mark.mkdir(exist_ok=True)
    if configured:
        (mark / ".configured").write_text(configured + "\n")
    (tmp_path / "id_ed25519").write_text("PRIVATE\n")
    (tmp_path / "id_ed25519.pub").write_text(PUBKEY + "\n")
    (tmp_path / "ca.crt").write_text("CA\n")
    if frpc:
        f = tmp_path / "frpc"
        f.write_text("#!/bin/sh\nexit 0\n")
        f.chmod(0o755)
    if relays:
        (tmp_path / "relay.socket").write_text("[Socket]\n")
    return textwrap.dedent(f"""
        set -u
        MARK_DIR={mark}
        CONF_MARK={mark}/.configured
        LOG={tmp_path}/wizard.log
        RAC_FRPC={tmp_path}/frpc
        RAC_CA={tmp_path}/ca.crt
        RAC_TOML={tmp_path}/frpc-host.toml
        RAC_HOSTKEY={tmp_path}/id_ed25519
        RAC_LOCK={tmp_path}/register.lock
        RAC_RELAY_UNIT={tmp_path}/relay.socket
        RAC_PIN_FILE={mark}/rac-endpoint.env
        RAC_FAIL=""
        RAC_BASE_VMSSH=35800; RAC_BASE_VMWEB=45800
        RAC_BASE_HSSH=50800;  RAC_BASE_HUI=55800
        RAC_BASE_VMSTN=48800; RAC_BASE_VMGMAG=49800
        DASI_NUM=""; HAMSCI_TOKEN=""
        RAC_SERVER=""; RAC_TLS="on"; RAC_TIER_LABEL=""
        RAC_TIERS='{tiers}'
        say(){{ echo "[wizard] $*"; }}
        systemctl(){{ echo "$*" >> {tmp_path}/systemctl.calls; }}
    """) + "".join(shell_func(n) for n in FUNCS)


def run(script, tmp_path, env=None):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          timeout=120, env=dict(os.environ, **(env or {})))


def calls(tmp_path):
    p = tmp_path / "systemctl.calls"
    return p.read_text().splitlines() if p.exists() else []


def tier(port, label="test gateway", tls="on"):
    return f"{label}|127.0.0.1|http://127.0.0.1:{port}/register|{tls}|{port}"


# -- the name the gateway files the station under -----------------------------

def test_site_name_comes_from_the_running_wizards_reporter(tmp_path):
    pre = host(tmp_path, tiers="")
    r = run(pre + 'REPORTER="n0call/t1"\nrac_site_name\n', tmp_path)
    assert r.stdout == "N0CALL_T1"


def test_site_name_without_a_wizard_reads_the_recorded_name_then_the_configured_mark(tmp_path):
    pre = host(tmp_path, tiers="")
    assert run(pre + "rac_site_name\n", tmp_path).stdout == "AC0G_B4"
    (tmp_path / "marks" / "rac-site").write_text("W3USR_9\n")
    assert run(pre + "rac_site_name\n", tmp_path).stdout == "W3USR_9"


def test_site_name_drops_what_the_gateway_would_not_accept(tmp_path):
    pre = host(tmp_path, tiers="")
    r = run(pre + 'REPORTER=\'ac0g/b4";rm -rf $HOME\'\nrac_site_name\n', tmp_path)
    assert r.stdout == "AC0G_B4RM-RFHOME"


# -- registration -------------------------------------------------------------

def test_register_sends_only_the_name_and_the_public_key(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + 'rac_register ensure; echo "rc=$? fail=$RAC_FAIL"\n', tmp_path)
    assert "rc=0 fail=" in r.stdout, r.stdout + r.stderr
    assert registrar.bodies == [{"site": "AC0G_B4", "pubkey": PUBKEY}]
    assert "this sends the station's name (AC0G_B4) and this host's public key" in r.stdout


def test_register_writes_the_tunnel_configuration_and_its_marks(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port, label="wsprdaemon (secure)"))
    r = run(pre + "rac_register ensure\n", tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    toml = (tmp_path / "frpc-host.toml").read_text()
    assert 'serverAddr = "127.0.0.1"' in toml
    assert f"serverPort = {registrar.port}" in toml          # the tier's port, not the reply's
    assert 'user = "0123456789abcdef"' in toml and 'token = "tok3nTOKEN42"' in toml
    assert f'trustedCaFile = "{tmp_path}/ca.crt"' in toml
    names = [ln.split('"')[1] for ln in toml.splitlines() if ln.startswith("name = ")]
    assert names == [f"AC0G_B4-{c}" for c in
                     ("vm-ssh", "vm-web", "vm-station", "vm-gmag", "host-ssh", "host-ui")]
    ports = [int(ln.split("=")[1]) for ln in toml.splitlines() if ln.startswith("remotePort")]
    assert ports == [36307, 46307, 48800 + 507, 49800 + 507, 51307, 56307]
    assert stat.S_IMODE((tmp_path / "frpc-host.toml").stat().st_mode) == 0o600
    assert not (tmp_path / "frpc-host.toml.new").exists()
    marks = tmp_path / "marks"
    assert (marks / "rac-number").read_text() == "507\n"
    assert (marks / "rac-tier").read_text() == "wsprdaemon (secure)\n"
    assert (marks / "rac-registrar").read_text() == f"http://127.0.0.1:{registrar.port}/register\n"
    assert (marks / "rac-site").read_text() == "AC0G_B4\n"


def test_registration_never_starts_stops_or_enables_the_tunnel(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    run(pre + "rac_register ensure\n", tmp_path)
    # A running tunnel takes up the new configuration; a stopped one stays
    # stopped.  try-restart does exactly that and nothing else may appear.
    assert calls(tmp_path) == ["try-restart sigmond-rac-host.service"]
    body = "".join(shell_func(n) for n in FUNCS)
    for verb in ("systemctl enable", "systemctl start", "systemctl restart",
                 "systemctl stop", "systemctl disable"):
        assert verb not in body, verb


def test_ensure_leaves_a_registered_host_alone(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    (tmp_path / "frpc-host.toml").write_text('user = "old"\n')
    r = run(pre + 'rac_register ensure; echo "rc=$?"\n', tmp_path)
    assert "rc=0" in r.stdout
    assert registrar.bodies == []
    assert (tmp_path / "frpc-host.toml").read_text() == 'user = "old"\n'
    assert calls(tmp_path) == []


def test_fresh_registers_again_over_an_existing_configuration(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    (tmp_path / "frpc-host.toml").write_text('user = "old"\n')
    r = run(pre + 'REPORTER=W3USR/9\nrac_register fresh; echo "rc=$?"\n', tmp_path)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert registrar.bodies[0]["site"] == "W3USR_9"
    assert 'name = "W3USR_9-vm-ssh"' in (tmp_path / "frpc-host.toml").read_text()


def test_a_failed_fresh_run_keeps_the_old_configuration(tmp_path):
    dead = Registrar()
    port = dead.port
    dead.close()                                   # nothing listens there now
    pre = host(tmp_path, tiers=tier(port, label="gone"))
    (tmp_path / "frpc-host.toml").write_text('user = "old"\n')
    r = run(pre + 'rac_register fresh; echo "rc=$? fail=$RAC_FAIL"\n', tmp_path)
    assert "rc=1 fail=no gateway tier answered (tried: gone)" in r.stdout
    assert (tmp_path / "frpc-host.toml").read_text() == 'user = "old"\n'
    assert not (tmp_path / "marks" / "rac-number").exists()
    assert calls(tmp_path) == []


def test_the_ladder_walks_past_a_dead_tier_to_one_that_answers(tmp_path, registrar):
    dead = Registrar()
    port = dead.port
    dead.close()
    tiers = tier(port, label="first") + "\n" + tier(registrar.port, label="second", tls="off")
    pre = host(tmp_path, tiers=tiers)
    r = run(pre + 'rac_register ensure; echo "rc=$? tried=$RAC_TRIED"\n', tmp_path)
    assert "rc=0 tried=first, second" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "marks" / "rac-tier").read_text() == "second\n"
    toml = (tmp_path / "frpc-host.toml").read_text()
    assert "[transport.tls]\nenable = false" in toml and "trustedCaFile" not in toml


def test_a_malformed_reply_registers_nothing(tmp_path):
    bad = Registrar(reply=dict(REPLY, user="$(reboot)"))
    try:
        pre = host(tmp_path, tiers=tier(bad.port))
        r = run(pre + 'rac_register ensure; echo "rc=$?"\n', tmp_path)
    finally:
        bad.close()
    assert "rc=1" in r.stdout
    assert not (tmp_path / "frpc-host.toml").exists()
    assert "malformed registration" in (tmp_path / "wizard.log").read_text()


@pytest.mark.parametrize("kw,reason", [
    (dict(configured=""), "records no station name yet"),
    (dict(frpc=False), "sigmond-rac payload missing"),
    (dict(relays=False), "the VM relays are not installed on this host"),
], ids=["no-name", "no-payload", "no-relays"])
def test_register_refuses_a_host_that_is_not_ready_and_says_why(tmp_path, registrar, kw, reason):
    pre = host(tmp_path, tiers=tier(registrar.port), **kw)
    r = run(pre + 'rac_register ensure; echo "rc=$? fail=$RAC_FAIL"\n', tmp_path)
    assert "rc=1" in r.stdout and reason in r.stdout, r.stdout
    assert registrar.bodies == []
    assert not (tmp_path / "frpc-host.toml").exists()


def test_the_lock_is_free_again_after_a_run(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + f'rac_register ensure\nflock -n {tmp_path}/register.lock true && echo FREE\n',
            tmp_path)
    assert "FREE" in r.stdout, r.stdout + r.stderr


def test_a_second_registration_waits_for_the_first(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    script = pre + textwrap.dedent(f"""
        flock {tmp_path}/register.lock sleep 2 &
        sleep 0.3
        t0=$(date +%s%N)
        rac_register ensure
        echo "waited_ms=$(( ($(date +%s%N) - t0) / 1000000 ))"
        wait
    """)
    r = run(script, tmp_path)
    waited = int(r.stdout.split("waited_ms=")[1].split()[0])
    assert waited >= 1200, r.stdout


# -- https: the reply carries the station's token ------------------------------

def _tls_registrar(tmp_path, name):
    """A registrar behind TLS with a self-signed certificate; (port, cert, close)."""
    import ssl
    key, crt = tmp_path / f"{name}.key", tmp_path / f"{name}.crt"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-keyout", str(key), "-out", str(crt), "-subj", "/CN=127.0.0.1",
                    "-addext", "subjectAltName=IP:127.0.0.1"],
                   check=True, capture_output=True, timeout=60)
    reg = Registrar()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    reg.httpd.socket = ctx.wrap_socket(reg.httpd.socket, server_side=True)
    return reg, crt


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl CLI")
def test_an_https_registrar_must_prove_itself_against_the_pinned_ca(tmp_path):
    reg, crt = _tls_registrar(tmp_path, "gateway")
    other, other_crt = _tls_registrar(tmp_path, "stranger")
    try:
        tiers = f"tls|127.0.0.1|https://127.0.0.1:{reg.port}/register|on|{reg.port}"
        pre = host(tmp_path, tiers=tiers)
        # Pinned to a CA that did not sign the registrar: no registration.
        shutil.copy(other_crt, tmp_path / "ca.crt")
        r = run(pre + 'rac_register ensure; echo "rc=$?"\n', tmp_path)
        assert "rc=1" in r.stdout
        assert reg.bodies == []
        assert "CERTIFICATE_VERIFY_FAILED" in (tmp_path / "wizard.log").read_text()
        # Pinned to the registrar's own certificate: it registers.
        shutil.copy(crt, tmp_path / "ca.crt")
        r = run(pre + 'rac_register ensure; echo "rc=$?"\n', tmp_path)
        assert "rc=0" in r.stdout, r.stdout + (tmp_path / "wizard.log").read_text()
        assert reg.bodies == [{"site": "AC0G_B4", "pubkey": PUBKEY}]
    finally:
        reg.close()
        other.close()


# -- a pinned gateway persists -------------------------------------------------

def _tiers_with(tmp_path, env, pin=None):
    pre = host(tmp_path, tiers="")
    if pin is not None:
        (tmp_path / "marks" / "rac-endpoint.env").write_text(pin)
    script = pre + textwrap.dedent("""
        RAC_PROFILE=standard; DASI_NUM=""
        RAC_REG_PORT=35737; RAC_PORT_SECURE=35736; RAC_PORT_UNSEC=35735
        _hs=vpn.hamsci.org; _wd=gw2.wsprdaemon.org
        rac_load_pin
        build_rac_tiers
        printf '%s\\n' "$RAC_TIERS"
    """)
    return run(script, tmp_path, env=env).stdout.strip()


def test_without_a_pin_the_ladder_stands(tmp_path):
    assert _tiers_with(tmp_path, {}).startswith("wsprdaemon (secure)|gw2.wsprdaemon.org|")


def test_a_pin_file_replaces_the_ladder_for_a_run_with_no_environment(tmp_path):
    pin = ("SIGMOND_RAC_SERVER=127.0.0.1\nSIGMOND_RAC_REGISTRAR=http://127.0.0.1:35737/register\n"
           "SIGMOND_RAC_FRPS_PORT=35736\nPATH=/evil\nSIGMOND_RAC_TLS=off\n")
    assert _tiers_with(tmp_path, {}, pin) == \
        "pinned|127.0.0.1|http://127.0.0.1:35737/register|off|35736"


def test_this_runs_environment_wins_over_the_pin_file(tmp_path):
    pin = "SIGMOND_RAC_SERVER=127.0.0.1\n"
    out = _tiers_with(tmp_path, {"SIGMOND_RAC_SERVER": "gw.example.org"}, pin)
    assert out.startswith("pinned|gw.example.org|")


def test_the_pin_file_never_runs_as_code(tmp_path):
    marker = tmp_path / "ran"
    pin = f"SIGMOND_RAC_SERVER=$(touch {marker})\ntouch {marker}\n"
    out = _tiers_with(tmp_path, {}, pin)
    assert not marker.exists()
    assert out.startswith("pinned|$(touch ")           # kept as text, never expanded


def test_a_wizard_run_with_a_pinned_environment_records_the_pin(tmp_path):
    pre = host(tmp_path, tiers="")
    env = {"SIGMOND_RAC_SERVER": "gw.example.org", "SIGMOND_RAC_FRPS_PORT": "35736"}
    run(pre + "rac_save_pin\n", tmp_path, env=env)
    assert (tmp_path / "marks" / "rac-endpoint.env").read_text() == \
        "SIGMOND_RAC_SERVER=gw.example.org\nSIGMOND_RAC_FRPS_PORT=35736\n"


def test_a_pin_of_the_server_alone_is_recorded_too(tmp_path):
    # The loop that writes the file must not end on the status of an unset
    # variable's test: that once skipped the rename and recorded nothing.
    pre = host(tmp_path, tiers="")
    run(pre + "rac_save_pin\n", tmp_path, env={"SIGMOND_RAC_SERVER": "127.0.0.1"})
    assert (tmp_path / "marks" / "rac-endpoint.env").read_text() == "SIGMOND_RAC_SERVER=127.0.0.1\n"


def test_a_wizard_run_without_a_pin_writes_no_pin_file(tmp_path):
    pre = host(tmp_path, tiers="")
    run(pre + "rac_save_pin\n", tmp_path, env={})
    assert not (tmp_path / "marks" / "rac-endpoint.env").exists()


# -- where the step sits in the wizard ------------------------------------------

def test_rac_register_runs_before_the_wizard_waits_for_a_vm_or_asks_anything():
    case_at = TEXT.index('case "${1:-}" in\n    --rac-register)')
    branch = TEXT[case_at:TEXT.index("    --rac-off", case_at)]
    assert "rac_register ensure" in branch and "exit 0" in branch and "exit 1;;" in branch
    assert case_at < TEXT.index("waiting for decoder VM")
    assert case_at < TEXT.index("ask_reporter() {")
    for name in FUNCS:
        assert TEXT.index(f"{name}()") < case_at, f"{name} must be defined above the dispatch"


def test_the_pin_loads_before_the_ladder_is_built():
    assert TEXT.index("\nrac_load_pin\n") < TEXT.index("\nbuild_rac_tiers\nRAC_SERVER=")


def test_the_wizard_run_registers_through_the_same_function():
    block = TEXT[TEXT.index("rac_install_relays(){"):TEXT.index('PSWS_STATE="skipped')]
    assert "rac_register fresh" in block
    assert "urllib.request" not in block           # one copy of the request, in rac_ladder
    assert TEXT.count("urllib.request.urlopen(req") == 1
    assert TEXT.count("<<TOMLEOF") == 1


def test_the_relay_templates_keep_the_shape_pm_align_reads():
    # pm-align renders a station's relay units from these very lines of the
    # release's wizard (pm-align.py relay_units, WIZARD_HEREDOCS).
    for start in ("cat > /usr/local/lib/sigmond/vm-port-relay.py <<'RLEOF'",
                  'cat > "/etc/systemd/system/sigmond-vm-$RNAME-relay.socket" <<SOCKEOF',
                  'cat > "/etc/systemd/system/sigmond-vm-$RNAME-relay@.service" <<SVCEOF'):
        assert TEXT.count(start) == 1, start
```

The tests cut each function out of the shipped wizard by name and run it under bash against a scratch directory.
A registrar on 127.0.0.1 stands in for the gateway and records every request. The https test builds two
self-signed certificates with the `openssl` command and skips where that command is missing.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_wizard_rac_register.py -q`
Expected: `27 failed, 1 passed`. Nearly every failure reads `AssertionError: <name> not found in the wizard`: the
functions do not exist yet. `test_the_relay_templates_keep_the_shape_pm_align_reads` passes already; it pins what
Step 3 must not disturb.

- [ ] **Step 3: Implement in `scripts/proxmox/sigmond-wizard.sh`**

Seven edits, top to bottom.

(a) Find (lines 24-25):

```bash
#              --rac-on        bring it back up
#              --rac-upgrade   climb to a more secure gateway tier if one
```

and replace it with:

```bash
#              --rac-on        bring it back up
#              --rac-register  register with the gateway and write the tunnel
#                              configuration; asks nothing, starts nothing
#              --rac-upgrade   climb to a more secure gateway tier if one
```

(b) Find (lines 212-214):

```bash
}

build_rac_tiers
```

and replace it with:

```bash
}

# A pinned gateway persists.  A registration that runs alone (the switch
# script, the retry timer) carries no environment from the wizard run that
# pinned it, so it reads the pin from this file.  The image rig writes one,
# so that a nested install never registers with a real gateway.
RAC_PIN_FILE=/etc/sigmond-appliance/rac-endpoint.env
rac_load_pin(){
    local k v
    [ -r "$RAC_PIN_FILE" ] || return 0
    while IFS='=' read -r k v; do
        case "$k" in
            SIGMOND_RAC_SERVER|SIGMOND_RAC_REGISTRAR|SIGMOND_RAC_TLS|SIGMOND_RAC_FRPS_PORT)
                # the environment of this run wins over the file
                [ -n "${!k:-}" ] || printf -v "$k" '%s' "$v" ;;
        esac
    done < "$RAC_PIN_FILE"
}
rac_save_pin(){
    local k
    [ -n "${SIGMOND_RAC_SERVER:-}" ] || [ -n "${SIGMOND_RAC_REGISTRAR:-}" ] || return 0
    for k in SIGMOND_RAC_SERVER SIGMOND_RAC_REGISTRAR SIGMOND_RAC_TLS SIGMOND_RAC_FRPS_PORT; do
        if [ -n "${!k:-}" ]; then printf '%s=%s\n' "$k" "${!k}"; fi
    done > "$RAC_PIN_FILE.new" && mv -f "$RAC_PIN_FILE.new" "$RAC_PIN_FILE"
}
rac_load_pin
build_rac_tiers
```

(c) Find (lines 225-226):

```bash
# ── RAC on/off switches (no wizard rerun needed) ────────────────────────────
case "${1:-}" in
```

and replace it with:

```bash
# ── registration, as a step that runs alone ────────────────────────────────
# tasks/plan-remote-access.md section 3.  Before v3.71 these lines sat inside
# the interactive flow and read its shell variables, so only a person
# answering the wizard could register a station.  They now read what a wizard
# run recorded under $MARK_DIR, and `sigmond-setup --rac-register` runs them
# with no question and no decoder VM.
#
# Nothing here starts or stops the tunnel.
RAC_FRPC=/usr/local/sbin/frpc
RAC_CA=/etc/sigmond/frps-ca.crt
RAC_TOML=/etc/sigmond/frpc-host.toml
RAC_HOSTKEY=/root/.ssh/id_ed25519
RAC_LOCK=/run/sigmond-rac-register.lock
RAC_RELAY_UNIT=/etc/systemd/system/sigmond-vm-ssh-relay.socket
RAC_FAIL=""

rac_site_name(){
    # The name the gateway files this station under: the reporter id in upper
    # case, with / as _.  A running wizard knows $REPORTER.  A run without it
    # reads the name the last registration recorded, then the reporter id the
    # last wizard run left in the configured mark.
    local r="${REPORTER:-}"
    [ -n "$r" ] || r=$(cat "$MARK_DIR/rac-site" 2>/dev/null)
    [ -n "$r" ] || r=$(awk 'NR==1{print $1}' "$CONF_MARK" 2>/dev/null)
    printf '%s' "$r" | tr '[:lower:]' '[:upper:]' | tr '/' '_' | tr -cd 'A-Z0-9_-'
}

rac_payload_ready(){
    # frpc, the pinned CA and the host unit come from the sigmond-rac payload.
    if [ ! -x "$RAC_FRPC" ] && [ -x /root/sigmond-appliance/sigmond-rac/install-host.sh ]; then
        (cd /root/sigmond-appliance/sigmond-rac && ./install-host.sh) >>"$LOG" 2>&1
    fi
    [ -x "$RAC_FRPC" ] && [ -f "$RAC_CA" ]
}

rac_ladder(){
    # Walk $RAC_TIERS, best first, and stop at the first tier that registers
    # this station.  Sets REG (empty when none did), RAC_TRIED, and for the
    # tier that answered RAC_SERVER, RAC_REGISTRAR, RAC_TLS, RAC_PORT and
    # RAC_TIER_LABEL.  Reads SITE, DASI_NUM, HAMSCI_TOKEN and $RAC_HOSTKEY.pub.
    local _lbl _cand _cand_reg _tls _fport
    local _racn _pvs _pvw _phs _phu _pst _pgm _duser _tf _dtls _spec _n _lp _rp
    local _tpid _drun _derr _api _i
    REG=""
    RAC_TRIED=""
    while IFS='|' read -r _lbl _cand _cand_reg _tls _fport; do
        [ -n "$_cand" ] || continue
        RAC_TRIED="${RAC_TRIED:+$RAC_TRIED, }$_lbl"
        say "trying $_lbl — $_cand:$_fport (registrar $_cand_reg)"
        case "$_tls" in
            off) say "  NOTE: this tier is UNSECURED (no TLS, legacy frps)." ;;
            opp) say "  NOTE: encrypted (TLS) but the server is unauthenticated" ;;
        esac
        # The tier IS its tunnel port: if no frps listens there, the tier is
        # down no matter what the (shared) registrar would say — probe it
        # first so a dead secure stack can't register and then fail late.
        if ! timeout 5 bash -c "exec 3<>/dev/tcp/$_cand/$_fport" 2>/dev/null; then
            say "  $_lbl did not answer (no frps on $_cand:$_fport)"
            continue
        fi
        if [ "$_cand_reg" = "DIRECT" ]; then
            # Registrar-less DASI rung: deterministic RAC + ports, verified
            # by ATTEMPT — a throwaway frpc claims all 4 proxies and the
            # frps itself refuses a taken name/port.  The live tunnel (if
            # any) is untouched: separate config, separate admin port.
            _racn=$((220 + DASI_NUM))
            _pvs=$((RAC_BASE_VMSSH+_racn)); _pvw=$((RAC_BASE_VMWEB+_racn))
            _phs=$((RAC_BASE_HSSH+_racn)); _phu=$((RAC_BASE_HUI+_racn))
            _pst=$((RAC_BASE_VMSTN+_racn)); _pgm=$((RAC_BASE_VMGMAG+_racn))
            _duser="DASI-$(printf '%03d' "$DASI_NUM")"
            say "  deterministic mapping: $_duser -> RAC #$_racn (attempting, not probing)"
            _tf=$(mktemp /tmp/frpc-dasi-XXXXXX.toml)
            _dtls=true; [ "$_tls" = "off" ] && _dtls=false
            {
                printf 'serverAddr = "%s"\nserverPort = %s\nuser = "%s"\nloginFailExit = true\n' "$_cand" "$_fport" "$_duser"
                # the pubkey rides EVERY login as metadata — today the server
                # ignores it; a future Login plugin on the VPN turns it into
                # per-unit admission (TOFU) with no Linux accounts (rob).
                printf '[metadatas]\npubkey = "%s"\nsite = "%s"\ndasi = "%s"\n' "$(cat "$RAC_HOSTKEY.pub")" "$SITE" "$_duser"
                # TOFU: the pubkey (above) is the credential; the server's
                # auth.token is empty, so the client sends an empty token too.
                printf '[auth]\nmethod = "token"\ntoken = ""\n'
                printf '[transport.tls]\nenable = %s\n' "$_dtls"
                printf '[webServer]\naddr = "127.0.0.1"\nport = 7502\n'
                for _spec in "vm-ssh:12222:$_pvs" "vm-web:12223:$_pvw" "host-ssh:22:$_phs" "host-ui:8006:$_phu" \
                             "vm-station:12224:$_pst" "vm-gmag:12225:$_pgm"; do
                    IFS=: read -r _n _lp _rp <<<"$_spec"
                    printf '[[proxies]]\nname = "%s-%s"\ntype = "tcp"\nlocalIP = "127.0.0.1"\nlocalPort = %s\nremotePort = %s\n' "$SITE" "$_n" "$_lp" "$_rp"
                done
            } > "$_tf"
            "$RAC_FRPC" -c "$_tf" >>"$LOG" 2>&1 &
            _tpid=$!
            _drun=0; _derr=""
            for _i in 1 2 3 4 5 6 7 8; do
                sleep 3
                kill -0 "$_tpid" 2>/dev/null || break
                _api=$(curl -s http://127.0.0.1:7502/api/status 2>/dev/null)
                _drun=$(echo "$_api" | grep -o '"status":"running"' | wc -l)
                _derr=$(echo "$_api" | grep -o '"err":"[^"]\{1,\}"' | head -3)
                [ "$_drun" -ge 4 ] && break
            done
            kill "$_tpid" 2>/dev/null; wait "$_tpid" 2>/dev/null; rm -f "$_tf"
            if [ "$_drun" -ge 4 ]; then
                say "  RAC #$_racn is free on $_cand — claiming it"
                REG="RACN=$_racn RUSER=$_duser RTOKEN=$HAMSCI_TOKEN P_VMSSH=$_pvs P_VMWEB=$_pvw P_HSSH=$_phs P_HUI=$_phu SRV=$_cand SPORT=$_fport"
            elif echo "$_derr" | grep -qi "port already"; then
                say "  ** RAC #$_racn ports are TAKEN on $_cand — is another unit already"
                say "  ** using DASI number $DASI_NUM?  Choose a different number via"
                say "  ** sigmond-setup --reconfigure, or let the ladder continue below."
            else
                say "  HamSCI direct attempt failed (${_derr:-login refused — check the token}); see $LOG"
            fi
        else
            REG=$(python3 - "$SITE" "$(cat "$RAC_HOSTKEY.pub")" "$_cand_reg" "$RAC_CA" <<'PYEOF' 2>>"$LOG"
import json, re, ssl, sys, urllib.error, urllib.request
site, pub, registrar, cafile = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
# The reply carries this station's token.  Over https, accept it only from a
# registrar that proves itself against the CA the image pins.  Both gateways
# serve their registrar over plain http today (see the ladder comment above),
# so an http address still goes out in the clear.
ctx = ssl.create_default_context(cafile=cafile) if registrar.startswith("https://") else None
req = urllib.request.Request(
    registrar,
    data=json.dumps({"site": site, "pubkey": pub}).encode(),
    headers={"Content-Type": "application/json"})
try:
    r = json.load(urllib.request.urlopen(req, timeout=30, context=ctx))
except urllib.error.HTTPError as e:
    try: msg = json.load(e)["error"]
    except Exception: msg = str(e)
    sys.exit("gateway refused registration: %s" % msg)
except Exception as e:
    sys.exit("cannot reach the gateway registrar: %s" % e)
ok = (re.fullmatch(r"[0-9a-f]{16}", str(r.get("user", "")))
      and re.fullmatch(r"[0-9a-zA-Z]{8,64}", str(r.get("token", "")))
      and re.fullmatch(r"[a-z0-9.-]+", str(r.get("server_addr", "")))
      and isinstance(r.get("rac"), int)
      and all(isinstance(r.get("ports", {}).get(k), int)
              for k in ("vm_ssh", "vm_web", "host_ssh", "host_ui")))
if not ok:
    sys.exit("gateway returned a malformed registration")
print("RACN=%d RUSER=%s RTOKEN=%s P_VMSSH=%d P_VMWEB=%d P_HSSH=%d P_HUI=%d SRV=%s SPORT=%d" % (
    r["rac"], r["user"], r["token"], r["ports"]["vm_ssh"], r["ports"]["vm_web"],
    r["ports"]["host_ssh"], r["ports"]["host_ui"],
    r["server_addr"], int(r["server_port"])))
PYEOF
)
        fi
        if [ -n "$REG" ]; then
            RAC_SERVER="$_cand"
            RAC_REGISTRAR="$_cand_reg"
            RAC_TLS="$_tls"
            RAC_PORT="$_fport"
            RAC_TIER_LABEL="$_lbl"
            break
        fi
        say "  $_lbl frps answered on :$_fport but its registrar did not"
    done <<TIEREOF
$RAC_TIERS
TIEREOF
}

rac_write_config(){
    # Turn one registration ($REG, from rac_ladder) into this host's tunnel
    # configuration and its marks.  It leaves RACN, SRV and the four gateway
    # ports set for the caller's summary line.
    local RAC_TLS_BLOCK
    RACN=""; P_VMSTN=""; P_VMGMAG=""
    eval "$REG"
    [ -n "$RACN" ] || { RAC_FAIL="the registration carried no RAC number"; return 1; }
    # The two newest channels are DERIVED, never supplied.  The deterministic
    # path computes them; the registrar path cannot, because the gateway's
    # JSON only carries the four original ports.  Every band is base+rac by
    # construction, so deriving is the same rule, applied here.
    P_VMSTN=$((RAC_BASE_VMSTN+RACN))
    P_VMGMAG=$((RAC_BASE_VMGMAG+RACN))
    # transport follows the tier that actually answered: a secure tier pins
    # the fleet CA, opportunistic (opp) encrypts without pinning (server cert
    # is self-signed — vpn.hamsci.org today), and an unsecured one has no TLS.
    case "$RAC_TLS" in
        off) RAC_TLS_BLOCK="[transport.tls]
enable = false" ;;
        opp) RAC_TLS_BLOCK="[transport.tls]
enable = true" ;;
        *)   RAC_TLS_BLOCK="[transport.tls]
enable = true
trustedCaFile = \"$RAC_CA\"" ;;
    esac
    say "writing $RAC_TOML (6 channels, one tunnel)"
    # Write beside the live file under a closed umask, then rename: the file
    # holds the station's token, and frpc must never read half of it.
    if ! ( umask 077; cat > "$RAC_TOML.new" <<TOMLEOF
# Written by sigmond-setup $(date -u +%F) — RAC #$RACN, site $SITE.
# ONE frpc on the Proxmox host carries every channel; the vm-* channels
# ride the local relays (12222-12225), so this file never needs the VM's
# address.
# serverPort comes from the TIER that answered (35736 TLS / 35735 legacy),
# not from the registrar's reply — the registrar only knows the legacy stack.
serverAddr = "$SRV"
serverPort = ${RAC_PORT:-$SPORT}
user = "$RUSER"

# The station's pubkey rides every login as metadata (any tier): servers
# ignore it today; a Login plugin can enforce per-unit admission (TOFU)
# from it later — no per-client accounts needed (rob 2026-08-09).
[metadatas]
pubkey = "$(cat "$RAC_HOSTKEY.pub")"
site = "$SITE"
dasi = "${DASI_NUM:+DASI-$(printf '%03d' "$DASI_NUM")}"

[auth]
method = "token"
token = "$RTOKEN"

$RAC_TLS_BLOCK

[webServer]
addr = "127.0.0.1"
port = 7500

[[proxies]]
name = "$SITE-vm-ssh"
type = "tcp"
localIP = "127.0.0.1"
localPort = 12222
remotePort = $P_VMSSH

[[proxies]]
name = "$SITE-vm-web"
type = "tcp"
localIP = "127.0.0.1"
localPort = 12223
remotePort = $P_VMWEB

[[proxies]]
name = "$SITE-vm-station"
type = "tcp"
localIP = "127.0.0.1"
localPort = 12224
remotePort = $P_VMSTN

[[proxies]]
name = "$SITE-vm-gmag"
type = "tcp"
localIP = "127.0.0.1"
localPort = 12225
remotePort = $P_VMGMAG

[[proxies]]
name = "$SITE-host-ssh"
type = "tcp"
localIP = "127.0.0.1"
localPort = 22
remotePort = $P_HSSH

[[proxies]]
name = "$SITE-host-ui"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8006
remotePort = $P_HUI
TOMLEOF
    ) || ! mv -f "$RAC_TOML.new" "$RAC_TOML"; then
        rm -f "$RAC_TOML.new"
        RAC_FAIL="could not write $RAC_TOML"
        return 1
    fi
    # The marks come last: a station counts as registered once the tunnel
    # configuration stands, and never before.
    printf '%s\n' "$RAC_TIER_LABEL" > "$MARK_DIR/rac-tier"
    printf '%s\n' "$RAC_REGISTRAR" > "$MARK_DIR/rac-registrar"
    printf '%s\n' "$RACN" > "$MARK_DIR/rac-number"
    printf '%s\n' "$SITE" > "$MARK_DIR/rac-site"
}

rac_register_locked(){
    local mode="$1"
    if [ "$mode" = ensure ] && [ -s "$RAC_TOML" ]; then
        return 0
    fi
    SITE=$(rac_site_name)
    if [ -z "$SITE" ]; then
        RAC_FAIL="this host records no station name yet; run: sigmond-setup --reconfigure"
        return 1
    fi
    if ! rac_payload_ready; then
        RAC_FAIL="sigmond-rac payload missing (frpc/CA); run install-host.sh, then sigmond-setup --reconfigure"
        return 1
    fi
    if [ ! -f "$RAC_RELAY_UNIT" ]; then
        RAC_FAIL="the VM relays are not installed on this host; run: pm-align --apply (or sigmond-setup --reconfigure)"
        return 1
    fi
    [ -f "$RAC_HOSTKEY" ] || ssh-keygen -q -t ed25519 -N "" -f "$RAC_HOSTKEY"
    say "registering with the gateway: this sends the station's name ($SITE) and this host's public key"
    rac_ladder
    if [ -z "$REG" ]; then
        RAC_FAIL="no gateway tier answered (tried: $RAC_TRIED)"
        return 1
    fi
    rac_write_config || return 1
    # A tunnel that runs takes up the new configuration.  One that stands
    # stopped stays stopped.
    systemctl try-restart sigmond-rac-host.service >>"$LOG" 2>&1
    return 0
}

rac_register(){
    # rac_register ensure|fresh
    #   ensure  register only a host that holds no tunnel configuration yet
    #   fresh   register again even so.  A wizard run does, because the
    #           reporter id may have changed.  A failed fresh run leaves the
    #           old configuration as it stands.
    # Returns 0 once $RAC_TOML holds a registration.  Returns 1 otherwise, with
    # the reason in $RAC_FAIL.  One registration at a time: the lock covers a
    # wizard run, the switch script and the retry timer.
    local rc
    RAC_FAIL=""
    exec 8>"$RAC_LOCK"
    if ! flock -w 300 8; then
        exec 8>&-
        RAC_FAIL="another registration is still running"
        return 1
    fi
    rac_register_locked "${1:-ensure}"; rc=$?
    exec 8>&-
    return $rc
}

# ── RAC on/off switches (no wizard rerun needed) ────────────────────────────
case "${1:-}" in
    --rac-register)
        # Registration alone.  No question, no decoder VM, and no start:
        # this branch exits before the wizard waits for anything.
        if rac_register ensure; then
            say "registered: RAC #$(cat "$MARK_DIR/rac-number" 2>/dev/null) via $(cat "$MARK_DIR/rac-tier" 2>/dev/null)"
            exit 0
        fi
        say "not registered: $RAC_FAIL"
        exit 1;;
```

(d) Find the lines that open the block (lines 1942-2084):

```bash
if [ -n "$RAC_NUM" ]; then
    say "activating remote access — registering with the gateway"
    # frpc binary, TLS CA and the host unit come from the sigmond-rac payload
```

and the lines that close it:

```bash
                : "${P_VMSTN:=$((RAC_BASE_VMSTN+RACN))}"
                : "${P_VMGMAG:=$((RAC_BASE_VMGMAG+RACN))}"
            }
```

Delete from the first of them through the last, 143 lines in all, and put in their place:

```bash
# The relays that carry the VM's channels.  A function, so that the lines
# below keep their bytes: pm-align reads these very templates out of the
# release's wizard to refresh a station's relay units.
rac_install_relays(){
```

(e) Find the lines that open the block (lines 2272-2367):

```bash
            systemctl enable --now sigmond-vm-ssh-relay.socket sigmond-vm-web-relay.socket \
                sigmond-vm-station-relay.socket sigmond-vm-gmag-relay.socket >>"$LOG" 2>&1

            install -m 755 /root/sigmond-appliance/sigmond/scripts/proxmox/pm-align.py /usr/local/sbin/pm-align 2>/dev/null \
```

and the lines that close it:

```bash
remotePort = $P_HUI
TOMLEOF
            chmod 600 /etc/sigmond/frpc-host.toml
```

Delete from the first of them through the last, 96 lines in all, and put in their place:

```bash
}
if [ -n "$RAC_NUM" ]; then
    say "activating remote access"
    if ! rac_payload_ready; then
        RAC_STATE="FAILED — sigmond-rac payload missing (frpc/CA); run install-host.sh, then sigmond-setup --reconfigure"
        say "WARN: $RAC_STATE"
    else
        rac_install_relays
        systemctl enable --now sigmond-vm-ssh-relay.socket sigmond-vm-web-relay.socket \
            sigmond-vm-station-relay.socket sigmond-vm-gmag-relay.socket >>"$LOG" 2>&1

        install -m 755 /root/sigmond-appliance/sigmond/scripts/proxmox/pm-align.py /usr/local/sbin/pm-align 2>/dev/null \
            || say "WARN: pm-align not installed (sigmond tree lacks it)"

        rac_save_pin
        if ! rac_register fresh; then
            RAC_STATE="FAILED — $RAC_FAIL (see $LOG), rerun: sigmond-setup --reconfigure"
            say "WARN: $RAC_STATE"
        else
```

(f) Find (lines 2372-2373):

```bash
            # reports per-proxy status once the server has accepted them.
            # _want is however many proxies the writer above actually put in
```

and replace it with:

```bash
            # reports per-proxy status once the server has accepted them.
            # _want is however many proxies rac_write_config actually put in
```

(g) Find (lines 2394-2396):

```bash
                echo "$RACN" > "$MARK_DIR/rac-number"
            else
                RAC_STATE="FAILED — registered, but only $RUNNING/$_want channels came up; journalctl -u sigmond-rac-host"
```

and replace it with:

```bash
            else
                RAC_STATE="FAILED — registered as RAC #$RACN, but only $RUNNING/$_want channels came up; journalctl -u sigmond-rac-host"
```

The first long deletion removes the old inline ladder, which now lives in `rac_ladder`. The lines between the two
deletions, from `# DHCP-proof relays` through `systemctl daemon-reload`, stay exactly as they stand, indentation
included: they form the body of `rac_install_relays`. The second long deletion removes the old configuration
writer, which now lives in `rac_write_config`.

Check the shape of the result:

```bash
cd /home/mjh/hamsci/repos/sigmond
bash -n scripts/proxmox/sigmond-wizard.sh && echo parses
for t in RLEOF SOCKEOF SVCEOF TOMLEOF PYEOF TIEREOF; do printf '%s=%s ' $t "$(grep -c "^$t\$" scripts/proxmox/sigmond-wizard.sh)"; done; echo
```

Expected: `parses`, then `RLEOF=1 SOCKEOF=1 SVCEOF=1 TOMLEOF=1 PYEOF=1 TIEREOF=2`.

- [ ] **Step 4: Run the tests to verify they pass, and find the one check this task makes false**

Run: `.venv/bin/pytest tests/test_wizard_rac_register.py -q`
Expected: `28 passed`.

Run: `.venv/bin/pytest tests/test_pm_align.py -q`
Expected: `1 failed, 112 passed`. `test_wizard_rac_number_gated_on_the_same_dynamic_threshold` fails with
`ValueError: substring not found`. It demanded that the wizard write `rac-number` only after the tunnel had proved
itself. Registration now records the number (ruling 15), so the check must follow the code.

- [ ] **Step 5: Replace that check in `tests/test_pm_align.py`**

(a) Find (lines 1527-1536):

```python
def test_wizard_rac_number_gated_on_the_same_dynamic_threshold():
    # The success branch that records rac-number must be gated by the SAME
    # "$_want" threshold the loop waits on -- not a bare "$RUNNING" check
    # that would still record success on a partial (e.g. 4-of-6) tunnel.
    i = WIZARD.index("PROVE the tunnel")
    block = WIZARD[i:i + 2500]
    want_i = block.index('_want=$(grep')
    gate_i = block.index('if [ "$RUNNING" -ge "$_want" ]')
    rac_number_i = block.index('rac-number')
    assert want_i < gate_i < rac_number_i
```

and replace it with:

```python
def test_wizard_records_the_rac_number_only_once_the_configuration_stands():
    # rac_write_config writes its marks after it has renamed the tunnel
    # configuration into place.  A host with a RAC number and no configuration
    # reads on the console panel as "assigned, but frpc-host.toml is missing".
    i = WIZARD.index("rac_write_config(){")
    body = WIZARD[i:WIZARD.index("\n}\n", i)]
    assert body.index('mv -f "$RAC_TOML.new" "$RAC_TOML"') < body.index("rac-number")
```

Run: `.venv/bin/pytest tests/test_pm_align.py -q`
Expected: `113 passed`.

- [ ] **Step 6: Run the wider suites**

Run: `.venv/bin/pytest tests/test_wizard_uploads.py tests/test_wizard_vmid.py tests/test_wizard_wifi.py tests/test_wizard_gpsdo.py tests/test_wizard_antenna.py tests/test_wizard_adoption_prose.py -q`
Expected: no failures. These cut other functions out of the same wizard.

Run: `bash tests/test-dasi-derive.sh | tail -1; bash tests/test-preflight-ctrl.sh | tail -1`
Expected: `DASI DERIVATION OK` and `PREFLIGHT REPORTING OK`.

Run: `.venv/bin/pytest -q`
Expected: `3526 passed, 1 warning, 65 subtests passed` (the baseline's 3498, and 28 new).

- [ ] **Step 7: Watch five guards fail, then restore**

Each mutation must turn the named tests red. The helper restores the file before each one.

```bash
cd /home/mjh/hamsci/repos/sigmond
F=scripts/proxmox/sigmond-wizard.sh
cp $F $F.keep
mut(){ cp $F.keep $F; python3 - "$F" "$1" "$2" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
assert s.count(sys.argv[2]) == 1, "the mutation's anchor must occur exactly once"
p.write_text(s.replace(sys.argv[2], sys.argv[3]))
PY
.venv/bin/pytest tests/test_wizard_rac_register.py -q 2>&1 | grep -E '^FAILED|passed|failed' | cut -c1-160; }

# 1. `ensure` no longer tells a registered host from an unregistered one, so a fresh run never registers again
mut '    if [ "$mode" = ensure ] && [ -s "$RAC_TOML" ]; then' \
    '    if [ -s "$RAC_TOML" ]; then'
# -> 2 failed, 26 passed: test_fresh_registers_again_over_an_existing_configuration, test_a_failed_fresh_run_keeps_the_old_configuration

# 2. registration restarts the tunnel outright, which would turn on a tunnel its operator turned off
mut '    systemctl try-restart sigmond-rac-host.service >>"$LOG" 2>&1' \
    '    systemctl restart sigmond-rac-host.service >>"$LOG" 2>&1'
# -> 1 failed, 27 passed: test_registration_never_starts_stops_or_enables_the_tunnel

# 3. the lock outlives the run, so the next registration waits five minutes
mut '    rac_register_locked "${1:-ensure}"; rc=$?
    exec 8>&-
' \
    '    rac_register_locked "${1:-ensure}"; rc=$?
'
# -> 1 failed, 27 passed: test_the_lock_is_free_again_after_a_run

# 4. an https registrar goes unverified against the pinned CA
mut 'ctx = ssl.create_default_context(cafile=cafile) if registrar.startswith("https://") else None' \
    'ctx = None'
# -> 1 failed, 27 passed: test_an_https_registrar_must_prove_itself_against_the_pinned_ca

# 5. the pin file runs as shell code
mut '                [ -n "${!k:-}" ] || printf -v "$k" '"'"'%s'"'"' "$v" ;;' \
    '                [ -n "${!k:-}" ] || eval "$k=$v" ;;'
# -> 1 failed, 27 passed: test_the_pin_file_never_runs_as_code

mv $F.keep $F
.venv/bin/pytest tests/test_wizard_rac_register.py -q | tail -1      # 28 passed
```

- [ ] **Step 8: Commit**

```bash
cd /home/mjh/hamsci/repos/sigmond
test "$(grep -c . .git/ATTRIBUTION)" = 2 || { echo "write .git/ATTRIBUTION first (Global Constraints)"; exit 1; }
git add scripts/proxmox/sigmond-wizard.sh tests/test_wizard_rac_register.py tests/test_pm_align.py
git commit -m "wizard: registration runs alone, as sigmond-setup --rac-register

The gateway ladder and the configuration writer move out of the
interactive flow into functions, and a wizard run calls the same ones.
They read the station's name and class from marks, so the switch script
and a retry timer can register a station with nobody at the console.
Registration starts nothing: it writes frpc-host.toml by rename under a
closed umask, records the RAC number, and try-restarts a tunnel that
runs.  A pinned gateway persists in rac-endpoint.env.  The request
verifies TLS against the pinned CA when the registrar's address says
https; the ladder still names http, the only thing a gateway serves.

tasks/plan-remote-access.md section 3.

$(cat .git/ATTRIBUTION)"
```

### Task 2: one switch script on the host, and the wish it records

Spec section 1: "`/usr/local/sbin/sigmond-rac-switch` becomes the only code that turns the tunnel on or off. It
takes exactly three words: `on`, `off`, `status`." Today two handlers in the wizard do that work with `systemctl`
lines of their own (lines 227-233 and 274-283 at 7fe58c7), and the wizard's install path does it a third time.

This task writes the script, has the wizard install it, and routes all three paths through it. The script ships
inside the wizard as a heredoc (ruling 1), the way `sigmond-vm` does, so that every host holding this wizard can
install it. `--rac-on` and `--rac-off` shrink to one line each: install the script, then run it.

What the script does, word by word (spec section 1):

- `on` records the wish, registers first when the station never did (Task 1's `--rac-register`), enables and starts
  the four relay sockets and the tunnel, waits for every channel the configuration declares, and prints the state.
- `off` records the wish, then disables and stops the relays and the tunnel, the tunnel last. It keeps the
  registration. It ignores a terminal that goes away, because the caller may sit at the far end of the tunnel it
  stops (Review Focus 1).
- `status` prints the state and changes nothing.

The wish lives in `/etc/sigmond-appliance/rac-desired`. "The units' enabled state cannot carry it alone: a station
that has not registered yet has nothing to enable, and its wish still reads `on`" (spec section 1).

**Decisions this task makes** (the spec left them open):

1. **The answer names its state on one exact line**: `Remote access: on`, `Remote access: off` or
   `Remote access: wanted on, not up`. `status` prints it first; `on` prints it after the registration's lines.
   `smd remote` (Task 5), the wizard's summary and the rigs read the state from that line.
2. **Every run leaves the facts in `/run/sigmond/rac-state`**, one `key=value` per line. The console panel
   (Task 9) and the host's heartbeat (Task 7) read that file, so one script holds the truth about the three states.
3. **A tunnel with at least one accepted channel counts as on**, and the answer says how many of how many. A
   tunnel that runs with none, or does not run, reads "wanted on, not up".
4. **Exit codes** (ruling 16): 0 done, 2 refused, 3 wanted on and not up, 4 off and the tunnel still runs, and
   1 when the switch could not start its work: it never got its lock, or it could not record the wish. It then
   changes nothing.
5. **The word arrives as the one argument, or, with no argument at all, in `SSH_ORIGINAL_COMMAND`.** The second
   way belongs to the pairing's forced command (Task 4). In that mode the script reads nothing else from its
   environment: the path overrides that the tests use have no effect there.
6. **A station with no wish on file** (installed before v3.71) reads on when it holds a registration and its unit
   stands enabled, and off otherwise (ruling 3). `status` never writes a wish for it.
7. **Who and when go on record only when the wish changes**, in `/etc/sigmond-appliance/rac-changed`:
   `<wish> <UTC time> <who>`, with who one of `vm`, `host`, `install`, `timer`.

**Files:**
- Modify: `sigmond/scripts/proxmox/sigmond-wizard.sh` — the header; a new block above
  `# ── RAC on/off switches`; the two handlers; the activation block. Line numbers below name the file at Task 1's
  head.
- Create: `sigmond/tests/test_rac_switch.py`
- Modify: `sigmond/tests/test_pm_align.py` — two static checks move out

**Interfaces:**
- Consumes: Task 1's `sigmond-setup --rac-register` (exit 0 once registered, 1 otherwise; it prints its own
  lines) and `rac_register fresh`; the marks `rac-number`, `rac-tier`; `/etc/sigmond/frpc-host.toml`; frpc's admin
  API at `http://127.0.0.1:7500/api/status`; `sigmond-issue.service`.
- Produces:
  - `/usr/local/sbin/sigmond-rac-switch on|off|status`, with the state line, exit codes and files above.
  - `/run/sigmond/rac-state` keys, in this order: `state` (`on`, `off`, `down`), `wish` (`on`, `off`),
    `wish_source` (`file`, `units`), `registered` (`yes`, `no`), `tunnel` (`running`, `stopped`), `channels_up`,
    `channels_declared`, `rac`, `gateway`, `tier`, `changed_at`, `changed_by`, `checked_at`.
  - Overrides, honoured only when the word arrives as an argument: `SIGMOND_RAC_MARK_DIR`, `SIGMOND_RAC_TOML`,
    `SIGMOND_RAC_RUN_DIR`, `SIGMOND_RAC_SETUP`, `SIGMOND_RAC_API`, `SIGMOND_RAC_WAIT_S` (60), `SIGMOND_RAC_WAIT_STEP`
    (5), `SIGMOND_RAC_LOCK_WAIT_S` (120), and `SIGMOND_RAC_BY` (`install` or `timer`).
  - In the wizard: `rac_install_host_files` (returns 0, or 1 when the script could not be written or does not
    parse); `RAC_SWITCH=/usr/local/sbin/sigmond-rac-switch`; `RAC_STATE_FILE=/run/sigmond/rac-state`; the branch
    `--rac-off|--rac-on)`.
  - The heredoc line `cat > /usr/local/sbin/sigmond-rac-switch <<'RACSWEOF'`, which Task 6 teaches pm-align to
    read.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rac_switch.py`:

```python
"""sigmond-rac-switch: the one switch for the remote-access tunnel
(tasks/plan-remote-access.md section 1).

The script ships inside sigmond-wizard.sh as a heredoc, the way sigmond-vm
does, so these tests cut it out of the wizard and run what ships.  A scratch
directory stands in for the Proxmox host: stub `systemctl`, `curl` and
`logger` sit first on PATH and answer from files in it.
"""
import fcntl
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WIZARD = (REPO / "scripts" / "proxmox" / "sigmond-wizard.sh").read_text()

SOCKETS = [f"sigmond-vm-{n}-relay.socket" for n in ("ssh", "web", "station", "gmag")]
UNIT = "sigmond-rac-host.service"
ON, OFF, DOWN = "Remote access: on", "Remote access: off", "Remote access: wanted on, not up"

TOML = ('serverAddr = "gw2.wsprdaemon.org"\nuser = "0123456789abcdef"\n'
        + "".join(f'[[proxies]]\nname = "AC0G_B4-{c}"\nremotePort = {p}\n' for c, p in (
            ("vm-ssh", 36307), ("vm-web", 46307), ("vm-station", 49307),
            ("vm-gmag", 50307), ("host-ssh", 51307), ("host-ui", 56307))))


def switch_text() -> str:
    m = re.search(r"cat > /usr/local/sbin/sigmond-rac-switch <<'RACSWEOF'\n(.*?)\nRACSWEOF\n",
                  WIZARD, re.S)
    assert m, "the wizard carries no sigmond-rac-switch heredoc"
    return m.group(1) + "\n"


SYSTEMCTL = r"""#!/bin/bash
# stub: write down every call; keep `active` and `enabled` as files
echo "$*" >> "$HOSTDIR/systemctl.calls"
verb="$1"; shift
case "$verb" in
    is-active)  [ -e "$HOSTDIR/active" ] ;;
    is-enabled) [ -e "$HOSTDIR/enabled" ] ;;
    enable)  case " $* " in *" sigmond-rac-host.service "*)
                 touch "$HOSTDIR/enabled"
                 [ -e "$HOSTDIR/unit-refuses-to-start" ] || touch "$HOSTDIR/active" ;; esac ;;
    disable) case " $* " in *" sigmond-rac-host.service "*)
                 rm -f "$HOSTDIR/enabled"
                 [ -e "$HOSTDIR/unit-refuses-to-stop" ] || rm -f "$HOSTDIR/active" ;; esac ;;
esac
"""
CURL = r"""#!/bin/bash
# stub: frpc's admin API, with as many running proxies as the file says
n=$(cat "$HOSTDIR/channels" 2>/dev/null || echo 0)
printf '{"tcp":['
for i in $(seq 1 "$n"); do printf '{"name":"p%s","status":"running"},' "$i"; done
printf '{"name":"x","status":"new"}]}\n'
"""
SETUP = r"""#!/bin/bash
# stub: sigmond-setup --rac-register
echo "$*" >> "$HOSTDIR/setup.calls"
[ "$1" = "--rac-register" ] || exit 9
[ -e "$HOSTDIR/gateway-answers" ] || { echo "[wizard] not registered: no gateway tier answered"; exit 1; }
cp "$HOSTDIR/toml.reply" "$HOSTDIR/frpc-host.toml"
echo 507 > "$HOSTDIR/marks/rac-number"
echo "[wizard] registered: RAC #507"
"""


class Host:
    def __init__(self, tmp_path, *, registered=True, enabled=False, active=False,
                 channels=6, wish=None):
        self.d = tmp_path
        (tmp_path / "bin").mkdir()
        (tmp_path / "marks").mkdir()
        (tmp_path / "run").mkdir()
        for name, body in (("systemctl", SYSTEMCTL), ("curl", CURL), ("sigmond-setup", SETUP),
                           ("logger", "#!/bin/sh\nexit 0\n"), ("sleep", "#!/bin/sh\nexit 0\n")):
            p = tmp_path / "bin" / name
            p.write_text(body)
            p.chmod(0o755)
        self.script = tmp_path / "sigmond-rac-switch"
        self.script.write_text(switch_text())
        self.script.chmod(0o755)
        (tmp_path / "toml.reply").write_text(TOML)
        (tmp_path / "channels").write_text(str(channels))
        if registered:
            (tmp_path / "frpc-host.toml").write_text(TOML)
            (tmp_path / "marks" / "rac-number").write_text("507\n")
            (tmp_path / "marks" / "rac-tier").write_text("wsprdaemon (secure)\n")
        if enabled:
            (tmp_path / "enabled").touch()
        if active:
            (tmp_path / "active").touch()
        if wish:
            (tmp_path / "marks" / "rac-desired").write_text(wish + "\n")

    def run(self, *args, env=None, **kw):
        e = {"PATH": f"{self.d}/bin:{os.environ['PATH']}", "HOSTDIR": str(self.d),
             "SIGMOND_RAC_MARK_DIR": f"{self.d}/marks",
             "SIGMOND_RAC_TOML": f"{self.d}/frpc-host.toml",
             "SIGMOND_RAC_RUN_DIR": f"{self.d}/run",
             "SIGMOND_RAC_SETUP": f"{self.d}/bin/sigmond-setup",
             "SIGMOND_RAC_WAIT_S": "10", "SIGMOND_RAC_WAIT_STEP": "5"}
        e.update(env or {})
        return subprocess.run([str(self.script), *args], capture_output=True, text=True,
                              timeout=60, env=e, **kw)

    def calls(self, name="systemctl"):
        p = self.d / f"{name}.calls"
        return p.read_text().splitlines() if p.exists() else []

    def changes(self):
        """Every systemctl call that changes something."""
        return [c for c in self.calls() if c.split()[0] not in ("is-active", "is-enabled")]

    def wish(self):
        p = self.d / "marks" / "rac-desired"
        return p.read_text().strip() if p.exists() else None

    def state(self):
        p = self.d / "run" / "rac-state"
        return dict(ln.split("=", 1) for ln in p.read_text().splitlines())


# -- the word -------------------------------------------------------------------

REFUSED = [
    [], [""], ["ON"], ["Status"], ["start"], ["--help"], ["-h"], ["on", "off"],
    ["status", "extra"], ["status extra"], ["on;reboot"], [" on"], ["on "], ["on\n"],
    ["off", ""], ["$(reboot)"], ["status", "--json"],
]


@pytest.mark.parametrize("args", REFUSED, ids=[repr(a) for a in REFUSED])
def test_anything_but_one_of_the_three_words_is_refused_and_changes_nothing(tmp_path, args):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    r = h.run(*args)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert r.stdout == ""
    assert "exactly one word: on, off or status" in r.stderr
    assert h.calls() == []                       # not even a read
    assert h.wish() == "on"
    assert not (tmp_path / "run" / "rac-state").exists()


REFUSAL = "sigmond-rac-switch: refused. It takes exactly one word: on, off or status.\n"


@pytest.mark.parametrize("asked", ["status extra", "off; reboot", "on\noff", "", " off",
                                   "sigmond-rac-switch off", "\x1b[2Joff"])
def test_a_request_through_the_forced_command_is_read_as_strictly(tmp_path, asked):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    r = h.run(env={"SSH_ORIGINAL_COMMAND": asked})
    assert r.returncode == 2
    assert h.calls() == [] and h.wish() == "on"
    assert r.stderr == REFUSAL                   # never a byte of what was asked


def test_the_forced_command_passes_the_word_in_ssh_original_command(tmp_path):
    # No argument: the word arrives in SSH_ORIGINAL_COMMAND, and in that mode
    # the script honours no override.  It therefore reads this machine's real
    # paths, so only the shape of the answer can be checked here.
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    r = h.run(env={"SSH_ORIGINAL_COMMAND": "status"})
    assert r.returncode in (0, 3), (r.stdout, r.stderr)
    assert r.stdout.splitlines()[0] in (ON, OFF, DOWN)
    assert not (tmp_path / "run" / "rac-state").exists()     # SIGMOND_RAC_RUN_DIR ignored
    assert not (tmp_path / "setup.calls").exists()


def test_a_request_from_the_pairing_account_is_recorded_as_from_the_vm(tmp_path):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    h.run("off", env={"SUDO_USER": "sigmond-remote"})
    assert (tmp_path / "marks" / "rac-changed").read_text().split()[2] == "vm"
    assert "from the decoder VM" in h.run("status").stdout


# -- off ---------------------------------------------------------------------------

def test_off_records_the_wish_and_disables_the_relays_then_the_tunnel(tmp_path):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    r = h.run("off")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.splitlines()[0] == OFF
    assert h.wish() == "off"
    assert h.changes() == ["disable --now " + " ".join(SOCKETS),
                           f"disable --now {UNIT}",
                           "start --no-block sigmond-issue.service"]
    assert (tmp_path / "frpc-host.toml").read_text() == TOML        # the registration stays
    assert "The registration stays (RAC 507 on gw2.wsprdaemon.org, wsprdaemon (secure))" in r.stdout
    assert "Turn it on:   smd remote on" in r.stdout
    st = h.state()
    assert (st["state"], st["wish"], st["tunnel"], st["registered"]) == ("off", "off", "stopped", "yes")


def test_off_says_who_and_when(tmp_path):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    h.run("off")
    word, when, who = (tmp_path / "marks" / "rac-changed").read_text().split()
    assert (word, who) == ("off", "host")
    assert re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ", when)
    r = h.run("status")
    assert f"Set to off at {when} (UTC) on the Proxmox host." in r.stdout
    assert h.state()["changed_by"] == "host" and h.state()["changed_at"] == when


def test_off_finishes_when_its_terminal_has_gone_away(tmp_path):
    # An admin who came in through the tunnel types `smd remote off`: the
    # tunnel stops, the session dies, and the script must still finish.
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    rd, wr = os.pipe()
    os.close(rd)                                 # every write now meets a broken pipe
    try:
        e = {"PATH": f"{tmp_path}/bin:{os.environ['PATH']}", "HOSTDIR": str(tmp_path),
             "SIGMOND_RAC_MARK_DIR": f"{tmp_path}/marks",
             "SIGMOND_RAC_TOML": f"{tmp_path}/frpc-host.toml",
             "SIGMOND_RAC_RUN_DIR": f"{tmp_path}/run"}
        p = subprocess.run([str(h.script), "off"], stdout=wr, stderr=wr, env=e, timeout=60)
    finally:
        os.close(wr)
    assert p.returncode == 0
    assert h.wish() == "off"
    assert f"disable --now {UNIT}" in h.changes()
    assert not (tmp_path / "active").exists()


def test_off_that_leaves_the_tunnel_running_says_so_and_exits_4(tmp_path):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    (tmp_path / "unit-refuses-to-stop").touch()
    r = h.run("off")
    assert r.returncode == 4
    assert r.stdout.splitlines()[0] == OFF
    assert "the tunnel service runs all the same" in r.stdout


def test_off_on_a_station_that_never_registered_still_records_the_wish(tmp_path):
    h = Host(tmp_path, registered=False)
    r = h.run("off")
    assert r.returncode == 0 and h.wish() == "off"
    assert "The station has not registered yet." in r.stdout


# -- on ----------------------------------------------------------------------------

def test_a_change_that_cannot_get_the_lock_gives_up_and_changes_nothing(tmp_path):
    # The retry timer and an operator can ask in the same moment.  One lock
    # puts them in a row; a change that never gets it says so and touches nothing.
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    with open(tmp_path / "run" / "rac-switch.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        r = h.run("off", env={"SIGMOND_RAC_LOCK_WAIT_S": "1"})
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "another change is still running; nothing changed" in r.stderr
    assert r.stdout == ""
    assert h.changes() == []
    assert h.wish() == "on"
    # The positive control: the lock released, the same request goes through.
    r = h.run("off", env={"SIGMOND_RAC_LOCK_WAIT_S": "1"})
    assert r.returncode == 0 and h.wish() == "off", (r.stdout, r.stderr)


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only directory")
@pytest.mark.parametrize("word,stood", [("off", "on"), ("on", "off")])
def test_a_wish_that_cannot_be_recorded_changes_nothing(tmp_path, word, stood):
    h = Host(tmp_path, enabled=(stood == "on"), active=(stood == "on"), wish=stood)
    marks = tmp_path / "marks"
    marks.chmod(0o555)
    try:
        r = h.run(word)
    finally:
        marks.chmod(0o755)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "could not record the wish; nothing changed" in r.stderr
    assert h.changes() == []                     # nothing enabled, nothing stopped
    assert h.wish() == stood


def test_on_enables_relays_and_tunnel_and_waits_for_every_declared_channel(tmp_path):
    h = Host(tmp_path, wish="off", channels=6)
    r = h.run("on")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.splitlines()[-4] == ON or ON in r.stdout.splitlines()
    assert h.wish() == "on"
    assert h.changes() == ["enable --now " + " ".join(SOCKETS),
                           f"enable --now {UNIT}",
                           "start --no-block sigmond-issue.service"]
    assert h.calls("setup") == []                # registered already: nothing asked of the gateway
    assert "The gateway accepted 6 of 6 channels" in r.stdout
    assert "Turn it off:  smd remote off" in r.stdout
    st = h.state()
    assert (st["state"], st["channels_up"], st["channels_declared"]) == ("on", "6", "6")


def test_on_registers_first_when_the_station_never_did(tmp_path):
    h = Host(tmp_path, registered=False)
    (tmp_path / "gateway-answers").touch()
    r = h.run("on")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert h.calls("setup") == ["--rac-register"]
    assert "registered: RAC #507" in r.stdout    # the registration's own lines reach the caller
    assert f"enable --now {UNIT}" in h.changes()
    assert ON in r.stdout.splitlines()


def test_on_with_no_gateway_keeps_the_wish_and_reports_not_up(tmp_path):
    h = Host(tmp_path, registered=False)
    r = h.run("on")
    assert r.returncode == 3
    assert h.wish() == "on"
    assert DOWN in r.stdout.splitlines()
    assert "has not registered with a gateway yet" in r.stdout
    assert not any(c.startswith("enable") for c in h.changes())
    assert h.state()["state"] == "down" and h.state()["registered"] == "no"


def test_on_whose_channels_never_come_up_exits_3_after_the_wait(tmp_path):
    h = Host(tmp_path, wish="off", channels=0)
    r = h.run("on")
    assert r.returncode == 3
    assert DOWN in r.stdout.splitlines()
    assert "the gateway has accepted none of its 6 channels" in r.stdout
    assert h.wish() == "on"


def test_on_whose_service_will_not_start_names_the_journal(tmp_path):
    h = Host(tmp_path, wish="off")
    (tmp_path / "unit-refuses-to-start").touch()
    r = h.run("on")
    assert r.returncode == 3
    assert "The tunnel service does not run" in r.stdout
    assert f"journalctl -u {UNIT} -n 50" in r.stdout


def test_on_counts_what_the_configuration_declares_not_a_fixed_number(tmp_path):
    # Four of six is still a tunnel, and the answer says four of six.
    h = Host(tmp_path, wish="off", channels=4)
    r = h.run("on")
    assert r.returncode == 0
    assert "The gateway accepted 4 of 6 channels" in r.stdout
    assert h.calls().count("is-active --quiet " + UNIT) >= 1
    body = switch_text()
    assert "-ge 4" not in body and "-ge 6" not in body
    assert "grep -c '^name *=' \"$TOML\"" in body


def test_on_when_the_wish_already_reads_on_keeps_who_and_when(tmp_path):
    h = Host(tmp_path, wish="on")
    earlier = "on 2026-01-01T00:00:00Z install\n"
    (tmp_path / "marks" / "rac-changed").write_text(earlier)
    h.run("on")
    assert (tmp_path / "marks" / "rac-changed").read_text() == earlier


def test_the_installer_and_the_vm_are_named_as_such(tmp_path):
    h = Host(tmp_path, registered=True)
    h.run("on", env={"SIGMOND_RAC_BY": "install"})
    assert (tmp_path / "marks" / "rac-changed").read_text().split()[2] == "install"
    r = h.run("status")
    assert "by the installer" in r.stdout
    h.run("off", env={"SIGMOND_RAC_BY": "anything else"})
    assert (tmp_path / "marks" / "rac-changed").read_text().split()[2] == "host"


# -- status ------------------------------------------------------------------------

@pytest.mark.parametrize("kw,first,rc", [
    (dict(wish="on", enabled=True, active=True, channels=6), ON, 0),
    (dict(wish="off"), OFF, 0),
    (dict(wish="on", enabled=True, active=True, channels=0), DOWN, 3),
    (dict(wish="on", enabled=True, active=False), DOWN, 3),
    (dict(wish="on", registered=False), DOWN, 3),
], ids=["on", "off", "running-no-channel", "not-running", "not-registered"])
def test_status_prints_one_of_three_first_lines_and_changes_nothing(tmp_path, kw, first, rc):
    h = Host(tmp_path, **kw)
    before = h.wish()
    r = h.run("status")
    assert r.returncode == rc, (r.stdout, r.stderr)
    assert r.stdout.splitlines()[0] == first
    assert h.changes() == []
    assert h.wish() == before
    assert not (tmp_path / "marks" / "rac-changed").exists()


def test_status_writes_the_state_file_the_panel_and_the_heartbeat_read(tmp_path):
    h = Host(tmp_path, wish="on", enabled=True, active=True, channels=5)
    h.run("status")
    st = h.state()
    assert list(st) == ["state", "wish", "wish_source", "registered", "tunnel", "channels_up",
                        "channels_declared", "rac", "gateway", "tier", "changed_at",
                        "changed_by", "checked_at"]
    assert st["state"] == "on" and st["wish_source"] == "file"
    assert (st["channels_up"], st["channels_declared"]) == ("5", "6")
    assert (st["rac"], st["gateway"], st["tier"]) == ("507", "gw2.wsprdaemon.org", "wsprdaemon (secure)")
    assert re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ", st["checked_at"])
    assert oct((tmp_path / "run" / "rac-state").stat().st_mode & 0o777) == "0o644"


# -- a station installed before v3.71 holds no wish on file ---------------------------

@pytest.mark.parametrize("kw,first,line", [
    (dict(registered=True, enabled=True, active=True), ON, "The gateway accepted 6 of 6"),
    (dict(registered=True, enabled=False), OFF, "The operator holds it off. The registration stays"),
    (dict(registered=False, enabled=False), OFF, "This station never registered for remote access."),
    (dict(registered=False, enabled=True), OFF, "This station never registered for remote access."),
], ids=["said-yes", "rac-off-earlier", "said-no", "said-no-unit-enabled"])
def test_without_a_wish_on_file_the_wish_reads_from_what_stands(tmp_path, kw, first, line):
    h = Host(tmp_path, **kw)
    r = h.run("status")
    assert r.stdout.splitlines()[0] == first
    assert line in r.stdout
    assert h.wish() is None                      # status adopts nothing
    assert h.state()["wish_source"] == "units"


def test_a_station_whose_owner_declined_stays_as_it_stands_until_on(tmp_path):
    h = Host(tmp_path, registered=False)
    h.run("status")
    assert h.calls("setup") == [] and h.changes() == []
    (tmp_path / "gateway-answers").touch()
    r = h.run("on")
    assert r.returncode == 0 and h.calls("setup") == ["--rac-register"]


# -- one switch ---------------------------------------------------------------------

def test_the_wizards_two_spellings_run_the_switch_and_nothing_else():
    i = WIZARD.index("    --rac-off|--rac-on)")
    branch = WIZARD[i:WIZARD.index("    --rac-upgrade)", i)]
    assert 'exec "$RAC_SWITCH" "${1#--rac-}"' in branch
    assert "\nRAC_SWITCH=/usr/local/sbin/sigmond-rac-switch\n" in WIZARD
    assert "systemctl" not in branch
    assert "    --rac-on)\n" not in WIZARD and "    --rac-off)\n" not in WIZARD


def test_no_wizard_line_outside_the_switch_turns_the_tunnel_on_or_off():
    outside = WIZARD.replace(switch_text(), "")
    for verb in ("enable", "disable", "start", "stop", "restart"):
        assert not re.search(rf"systemctl {verb}( --now)? [^\n]*sigmond-rac-host", outside), verb
        assert not re.search(rf"systemctl {verb}( --now)? [^\n]*sigmond-vm-[a-z]+-relay", outside), verb
    assert "systemctl try-restart sigmond-rac-host.service" in outside     # registration only


def test_the_wizard_run_brings_the_tunnel_up_through_the_switch():
    block = WIZARD[WIZARD.index("rac_install_relays(){"):WIZARD.index('PSWS_STATE="skipped')]
    assert 'SIGMOND_RAC_BY=install "$RAC_SWITCH" on' in block
    assert "127.0.0.1:7500" not in block          # the switch does the proving now
    assert block.index("rac_install_host_files") < block.index('"$RAC_SWITCH" on')


def test_the_switch_installs_from_the_wizard_as_an_ascii_script():
    body = switch_text()
    assert body.startswith("#!/bin/bash\n")
    assert all(ord(c) < 128 for c in body)
    assert subprocess.run(["bash", "-n"], input=body, text=True, capture_output=True).returncode == 0
    i = WIZARD.index("rac_install_host_files(){")
    fn = WIZARD[i:WIZARD.index("\nRACSWEOF\n", i) + 400]
    assert "rm -f /usr/local/sbin/sigmond-rac-switch\n" in fn
    assert "chmod 755 /usr/local/sbin/sigmond-rac-switch" in fn
    assert WIZARD.index("rac_install_host_files(){") < WIZARD.index('case "${1:-}" in')
```

The tests cut the script out of the wizard and run it for real. Stub `systemctl`, `curl`, `logger` and `sleep`
stand first on `PATH`; they answer from files in a scratch directory and write down every call.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_rac_switch.py -q`
Expected: `57 failed`, most with `AssertionError: the wizard carries no sigmond-rac-switch heredoc`.

- [ ] **Step 3: Implement in `scripts/proxmox/sigmond-wizard.sh`**

(a) Find (lines 23-24):

```bash
#              --rac-off       drop the remote-access tunnel (config kept)
#              --rac-on        bring it back up
```

and replace it with:

```bash
#              --rac-off       turn the remote-access tunnel off (config kept)
#              --rac-on        turn it on again
#                              (both run /usr/local/sbin/sigmond-rac-switch,
#                              the one switch; the decoder VM reaches the
#                              same script with `smd remote`)
```

(b) Find (lines 578-580):

```bash
}

# ── RAC on/off switches (no wizard rerun needed) ────────────────────────────
```

and replace it with:

```bash
}

# ── the one switch, and the host files that go with it ─────────────────────
# tasks/plan-remote-access.md section 1.  /usr/local/sbin/sigmond-rac-switch
# holds the only code on the station that turns the tunnel on or off.  The
# wizard carries it, so that every host with this wizard can install it:
# firstboot stages the wizard on a new station, and pm-align brings it to a
# station already in the field.
RAC_SWITCH=/usr/local/sbin/sigmond-rac-switch
RAC_STATE_FILE=/run/sigmond/rac-state
rac_install_host_files(){
    # Unlink before writing: a copy that runs at this moment keeps its file.
    rm -f /usr/local/sbin/sigmond-rac-switch
    cat > /usr/local/sbin/sigmond-rac-switch <<'RACSWEOF'
#!/bin/bash
# sigmond-rac-switch - the one switch for this station's remote-access tunnel.
#
#   sigmond-rac-switch on       record the wish; register first if this station
#                               never did; bring the relays and the tunnel up;
#                               wait for the channels; print the state
#   sigmond-rac-switch off      record the wish; stop and disable the tunnel and
#                               the relays.  The registration stays, so a later
#                               `on` needs nothing from the gateway
#   sigmond-rac-switch status   print the state and change nothing
#
# It takes exactly one of those three words and nothing after it.  The decoder
# VM reaches this script through one ssh key whose forced command runs it
# under sudo with no argument at all; the word then arrives in
# SSH_ORIGINAL_COMMAND.  Nothing else on the station turns the tunnel on or
# off: `sigmond-setup --rac-on` and `--rac-off` run this script too.
#
# The first line it prints reads exactly one of
#     Remote access: on
#     Remote access: off
#     Remote access: wanted on, not up
# and `smd remote` in the VM reads the state from that line.
#
# Exit codes: 0 done (for status: on, or off)   2 refused, nothing changed
#             3 wanted on, not up               4 off, yet the tunnel still runs
#             1 the switch could not start its work, and changed nothing
#
# Written to the host by sigmond-wizard.sh (rac_install_host_files).
# Design: sigmond tasks/plan-remote-access.md.
set -u
LC_ALL=C; export LC_ALL

# -- the word ----------------------------------------------------------------
if [ "$#" -eq 0 ]; then
    # The pairing's way in: the forced command passes no argument.  In this
    # mode the script reads one variable from its environment and no other.
    unset SIGMOND_RAC_MARK_DIR SIGMOND_RAC_TOML SIGMOND_RAC_RUN_DIR SIGMOND_RAC_SETUP \
          SIGMOND_RAC_API SIGMOND_RAC_WAIT_S SIGMOND_RAC_WAIT_STEP SIGMOND_RAC_LOCK_WAIT_S \
          SIGMOND_RAC_BY
    WORD="${SSH_ORIGINAL_COMMAND-}"
elif [ "$#" -eq 1 ]; then
    WORD="$1"
else
    WORD=""
fi
case "$WORD" in
    on|off|status) ;;
    *)  # Never print what was asked: it came from another machine.
        echo "sigmond-rac-switch: refused. It takes exactly one word: on, off or status." >&2
        exit 2 ;;
esac

# Root on this host, and the tests, may point these elsewhere.
MARK_DIR="${SIGMOND_RAC_MARK_DIR:-/etc/sigmond-appliance}"
TOML="${SIGMOND_RAC_TOML:-/etc/sigmond/frpc-host.toml}"
RUN_DIR="${SIGMOND_RAC_RUN_DIR:-/run/sigmond}"
SETUP="${SIGMOND_RAC_SETUP:-/usr/local/sbin/sigmond-setup}"
API="${SIGMOND_RAC_API:-http://127.0.0.1:7500/api/status}"
WAIT_S="${SIGMOND_RAC_WAIT_S:-60}"
WAIT_STEP="${SIGMOND_RAC_WAIT_STEP:-5}"
LOCK_WAIT_S="${SIGMOND_RAC_LOCK_WAIT_S:-120}"
UNIT=sigmond-rac-host.service
SOCKETS="sigmond-vm-ssh-relay.socket sigmond-vm-web-relay.socket sigmond-vm-station-relay.socket sigmond-vm-gmag-relay.socket"
PAIR_USER=sigmond-remote

# Who asks: the decoder VM (through the pairing account), or root on this
# host.  A wizard run and the retry timer name themselves.
if [ "${SUDO_USER:-}" = "$PAIR_USER" ]; then
    BY=vm
else
    case "${SIGMOND_RAC_BY:-}" in install|timer) BY="$SIGMOND_RAC_BY" ;; *) BY=host ;; esac
fi

# -- reading what stands -----------------------------------------------------
registered(){ [ -s "$TOML" ]; }
tunnel_running(){ systemctl is-active --quiet "$UNIT" 2>/dev/null; }
declared(){ local n; n=$(grep -c '^name *=' "$TOML" 2>/dev/null); echo "${n:-0}"; }
channels_up(){
    # grep -o | wc -l, not grep -c: the API answers with one line of JSON.
    local n
    n=$(curl -s --max-time 3 "$API" 2>/dev/null | grep -o '"status":"running"' | wc -l | tr -d ' ')
    echo "${n:-0}"
}
wish_on_file(){
    local w
    w=$(head -1 "$MARK_DIR/rac-desired" 2>/dev/null)
    case "$w" in on|off) echo "$w" ;; esac
}
wish(){
    local w
    w=$(wish_on_file)
    if [ -n "$w" ]; then echo "$w"; return; fi
    # No wish on file: a station installed before v3.71.  Read the wish from
    # what stands.  Registered and enabled means on; anything else means off,
    # so a station whose owner declined at install stays as it stands.
    if registered && systemctl is-enabled --quiet "$UNIT" 2>/dev/null; then echo on; else echo off; fi
}
set_wish(){
    # Record who and when only when the wish changes.
    local new="$1"
    [ "$(wish_on_file)" = "$new" ] && return 0
    mkdir -p "$MARK_DIR" || return 1
    printf '%s\n' "$new" > "$MARK_DIR/rac-desired.new" \
        && mv -f "$MARK_DIR/rac-desired.new" "$MARK_DIR/rac-desired" || return 1
    printf '%s %s %s\n' "$new" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$BY" > "$MARK_DIR/rac-changed"
    logger -t sigmond-rac-switch "remote access wish set to $new by $BY" 2>/dev/null
    return 0
}
read_state(){
    W=$(wish)
    if [ -n "$(wish_on_file)" ]; then WSRC=file; else WSRC=units; fi
    if registered; then REGD=yes; else REGD=no; fi
    DECL=$(declared)
    UP=0; RUNS=stopped
    if tunnel_running; then RUNS=running; UP=$(channels_up); fi
    if [ "$W" = off ]; then STATE=off
    elif [ "$RUNS" = running ] && [ "$UP" -ge 1 ]; then STATE=on
    else STATE=down
    fi
    RACN=$(head -1 "$MARK_DIR/rac-number" 2>/dev/null)
    TIER=$(head -1 "$MARK_DIR/rac-tier" 2>/dev/null)
    GW=$(awk -F'"' '/^serverAddr/{print $2; exit}' "$TOML" 2>/dev/null)
    CHANGED_AT=""; CHANGED_BY=""
    if [ -r "$MARK_DIR/rac-changed" ]; then
        read -r _ CHANGED_AT CHANGED_BY < "$MARK_DIR/rac-changed"
    fi
}
write_state(){
    # One line per fact, key=value, for the console panel and the host
    # heartbeat.  They read this file; they never run it.
    mkdir -p "$RUN_DIR" 2>/dev/null
    {
        printf 'state=%s\n' "$STATE"
        printf 'wish=%s\n' "$W"
        printf 'wish_source=%s\n' "$WSRC"
        printf 'registered=%s\n' "$REGD"
        printf 'tunnel=%s\n' "$RUNS"
        printf 'channels_up=%s\n' "$UP"
        printf 'channels_declared=%s\n' "$DECL"
        printf 'rac=%s\n' "$RACN"
        printf 'gateway=%s\n' "$GW"
        printf 'tier=%s\n' "$TIER"
        printf 'changed_at=%s\n' "$CHANGED_AT"
        printf 'changed_by=%s\n' "$CHANGED_BY"
        printf 'checked_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "$RUN_DIR/rac-state.new" 2>/dev/null \
        && chmod 644 "$RUN_DIR/rac-state.new" 2>/dev/null \
        && mv -f "$RUN_DIR/rac-state.new" "$RUN_DIR/rac-state" 2>/dev/null
}
who_when(){
    [ -n "$CHANGED_AT" ] || return 0
    local who
    case "$CHANGED_BY" in
        vm)      who="from the decoder VM" ;;
        install) who="by the installer" ;;
        timer)   who="by the host's retry timer" ;;
        *)       who="on the Proxmox host" ;;
    esac
    echo "  Set to $W at $CHANGED_AT (UTC) $who."
}
print_state(){
    local where="RAC ${RACN:-?} on ${GW:-the gateway}${TIER:+, $TIER}"
    case "$STATE" in
        on)
            echo "Remote access: on"
            echo "  The gateway accepted $UP of $DECL channels ($where)."
            who_when
            echo "  Turn it off:  smd remote off   (in the decoder VM)" ;;
        off)
            echo "Remote access: off"
            if [ "$REGD" = yes ]; then
                echo "  The operator holds it off. The registration stays ($where),"
                echo "  so turning it on needs nothing from the gateway."
            elif [ "$WSRC" = units ]; then
                echo "  This station never registered for remote access."
            else
                echo "  The operator holds it off. The station has not registered yet."
            fi
            [ "$RUNS" = running ] && echo "  NOTE: the tunnel service runs all the same. Someone started it outside this switch; 'off' again stops it."
            who_when
            echo "  Turn it on:   smd remote on    (in the decoder VM)" ;;
        *)
            echo "Remote access: wanted on, not up"
            if [ "$REGD" = no ]; then
                echo "  The station has not registered with a gateway yet."
                echo "  Try again:  smd remote on   (in the decoder VM)"
            elif [ "$RUNS" = running ]; then
                echo "  The tunnel service runs, and the gateway has accepted none of its $DECL channels ($where)."
                echo "  It keeps trying every 30 seconds. Look at:  journalctl -u $UNIT -n 50   (on the Proxmox host)"
            else
                echo "  The tunnel service does not run ($where)."
                echo "  Look at:  journalctl -u $UNIT -n 50   (on the Proxmox host)"
            fi
            who_when
            echo "  Turn it off:  smd remote off   (in the decoder VM)" ;;
    esac
}
wait_for_channels(){
    # Every channel the configuration declares, never a fixed count.
    local want waited=0
    want=$(declared)
    [ "$want" -gt 0 ] || want=1
    while :; do
        [ "$(channels_up)" -ge "$want" ] && return 0
        [ "$waited" -ge "$WAIT_S" ] && return 1
        sleep "$WAIT_STEP"
        waited=$((waited + WAIT_STEP))
    done
}
repaint_panel(){ systemctl start --no-block sigmond-issue.service >/dev/null 2>&1; return 0; }

# -- status ------------------------------------------------------------------
if [ "$WORD" = status ]; then
    read_state
    write_state
    print_state
    [ "$STATE" = down ] && exit 3
    exit 0
fi

# -- on, off -----------------------------------------------------------------
# The caller may sit at the far end of the very tunnel this script stops, and
# its terminal then goes away mid-run.  Finish regardless.
trap '' HUP PIPE
mkdir -p "$RUN_DIR" 2>/dev/null
if ! exec 9>"$RUN_DIR/rac-switch.lock"; then
    echo "sigmond-rac-switch: cannot write under $RUN_DIR; nothing changed." >&2
    exit 1
fi
if ! flock -w "$LOCK_WAIT_S" 9; then
    echo "sigmond-rac-switch: another change is still running; nothing changed." >&2
    exit 1
fi

if [ "$WORD" = off ]; then
    trap '' INT TERM
    set_wish off || { echo "sigmond-rac-switch: could not record the wish; nothing changed." >&2; exit 1; }
    # The relays first and the tunnel last: stopping the tunnel may end the
    # caller's own session.
    # shellcheck disable=SC2086
    systemctl disable --now $SOCKETS >/dev/null 2>&1
    systemctl disable --now "$UNIT" >/dev/null 2>&1
    repaint_panel
    read_state
    write_state
    print_state 2>/dev/null
    [ "$RUNS" = running ] && exit 4
    exit 0
fi

# on
set_wish on || { echo "sigmond-rac-switch: could not record the wish; nothing changed." >&2; exit 1; }
if ! registered; then
    echo "This station has not registered for remote access yet. Registering now."
    "$SETUP" --rac-register
fi
if registered; then
    # shellcheck disable=SC2086
    systemctl enable --now $SOCKETS >/dev/null 2>&1
    systemctl enable --now "$UNIT" >/dev/null 2>&1
    echo "Waiting up to ${WAIT_S}s for the gateway to accept the channels."
    wait_for_channels
fi
repaint_panel
read_state
write_state
print_state
[ "$STATE" = down ] && exit 3
exit 0
RACSWEOF
    chmod 755 /usr/local/sbin/sigmond-rac-switch || return 1
    bash -n /usr/local/sbin/sigmond-rac-switch
}

# ── RAC on/off switches (no wizard rerun needed) ────────────────────────────
```

(c) Find (lines 591-597):

```bash
    --rac-off)
        systemctl disable --now sigmond-rac-host.service \
            sigmond-vm-ssh-relay.socket sigmond-vm-web-relay.socket \
            sigmond-vm-station-relay.socket sigmond-vm-gmag-relay.socket 2>/dev/null
        say "remote access (RAC) disabled — tunnel is down, config kept."
        say "re-enable any time:  sigmond-setup --rac-on"
        exit 0;;
```

and replace it with:

```bash
    --rac-off|--rac-on)
        # One switch.  These two spellings from before v3.71 run the script
        # that `smd remote` reaches from the decoder VM.
        rac_install_host_files || { say "could not install $RAC_SWITCH"; exit 1; }
        exec "$RAC_SWITCH" "${1#--rac-}";;
```

(d) Find (lines 638-647):

```bash
    --rac-on)
        if [ ! -f /etc/sigmond/frpc-host.toml ]; then
            say "no RAC config on this host yet — run: sigmond-setup --reconfigure"
            exit 1
        fi
        systemctl enable --now sigmond-vm-ssh-relay.socket sigmond-vm-web-relay.socket \
            sigmond-vm-station-relay.socket sigmond-vm-gmag-relay.socket 2>/dev/null
        systemctl enable --now sigmond-rac-host.service
        say "remote access (RAC) re-enabled$( [ -s "$MARK_DIR/rac-number" ] && echo " (RAC #$(cat "$MARK_DIR/rac-number"))" )"
        exit 0;;
```

and delete it.

(e) Find (lines 2505-2506):

```bash
        systemctl enable --now sigmond-vm-ssh-relay.socket sigmond-vm-web-relay.socket \
            sigmond-vm-station-relay.socket sigmond-vm-gmag-relay.socket >>"$LOG" 2>&1
```

and delete it.

(f) Find (lines 2516-2536):

```bash
            systemctl enable sigmond-rac-host.service >>"$LOG" 2>&1
            systemctl restart sigmond-rac-host.service >>"$LOG" 2>&1

            # PROVE the tunnel (don't just claim it): frpc's local admin API
            # reports per-proxy status once the server has accepted them.
            # _want is however many proxies rac_write_config actually put in
            # frpc-host.toml (6 today: vm-ssh/vm-web/vm-station/vm-gmag/
            # host-ssh/host-ui) — NOT a hardcoded count that silently accepts
            # a partial tunnel once a proxy is added or removed.
            _want=$(grep -c '^name *=' /etc/sigmond/frpc-host.toml 2>/dev/null)
            [ -n "$_want" ] && [ "$_want" -gt 0 ] || _want=6
            RUNNING=0
            for i in $(seq 1 12); do
                # grep -o|wc -l, NOT grep -c: the API is one line of JSON and
                # grep -c counts LINES — it reported 1/4 with all 4 running.
                RUNNING=$(curl -s http://127.0.0.1:7500/api/status 2>/dev/null | grep -o '"status":"running"' | wc -l)
                [ "$RUNNING" -ge "$_want" ] && break
                sleep 5
            done
            if [ "$RUNNING" -ge "$_want" ]; then
                RAC_STATE="#$RACN live on $SRV via $RAC_TIER_LABEL — VM ssh :$P_VMSSH · VM web :$P_VMWEB · host ssh :$P_HSSH · Proxmox UI :$P_HUI (off: sigmond-setup --rac-off)"
```

and replace it with:

```bash
            # The switch turns the tunnel on, waits for every channel the
            # configuration declares, and reports what stands.
            rac_install_host_files
            _sw=$(SIGMOND_RAC_BY=install "$RAC_SWITCH" on 2>&1); _swrc=$?
            printf '%s\n' "$_sw" | sed 's/^/  /' | tee -a "$LOG"
            _up=$(sed -n 's/^channels_up=//p' "$RAC_STATE_FILE" 2>/dev/null)
            _decl=$(sed -n 's/^channels_declared=//p' "$RAC_STATE_FILE" 2>/dev/null)
            if [ "$_swrc" = 0 ] && [ -n "$_decl" ] && [ "${_up:-0}" -ge "$_decl" ]; then
                RAC_STATE="#$RACN live on $SRV via $RAC_TIER_LABEL — VM ssh :$P_VMSSH · VM web :$P_VMWEB · host ssh :$P_HSSH · Proxmox UI :$P_HUI
            (turn it off or on from the decoder VM: smd remote off / smd remote on)"
```

(g) Find (lines 2542-2543):

```bash
            else
                RAC_STATE="FAILED — registered as RAC #$RACN, but only $RUNNING/$_want channels came up; journalctl -u sigmond-rac-host"
```

and replace it with:

```bash
            else
                RAC_STATE="FAILED — registered as RAC #$RACN, but only ${_up:-0}/${_decl:-?} channels came up; journalctl -u sigmond-rac-host"
```

Check: `bash -n scripts/proxmox/sigmond-wizard.sh && echo parses`
Expected: `parses`.

The script between `<<'RACSWEOF'` and `RACSWEOF` holds ASCII only. Keep it so: its words travel to the decoder VM,
and Task 4 sends some of them through the guest agent.

- [ ] **Step 4: Run the tests to verify they pass, and find the two checks that moved**

Run: `.venv/bin/pytest tests/test_rac_switch.py -q`
Expected: `57 passed`.

Run: `.venv/bin/pytest tests/test_pm_align.py -q`
Expected: `2 failed, 111 passed`. `test_wizard_rac_toggles_name_all_four_relay_sockets` and
`test_wizard_verify_counts_every_declared_proxy_not_four` read the two handlers and the wizard's own proof of the
tunnel, and both now live in the switch script. `tests/test_rac_switch.py` checks the same two things by running
the script: `test_off_records_the_wish_and_disables_the_relays_then_the_tunnel` and
`test_on_counts_what_the_configuration_declares_not_a_fixed_number`.

- [ ] **Step 5: Remove the two checks from `tests/test_pm_align.py`**

(a) Find (lines 1506-1527):

```python
# These read the real wizard shell script as text (no execution) and check
# for the three defects Task 7 fixes: the --rac-off/--rac-on toggles only
# touching 2 of 4 relay sockets, the tunnel-verify loop hardcoding "4" when
# frpc-host.toml actually carries 6 proxies, and the wizard never installing
# pm-align onto the host.

def test_wizard_rac_toggles_name_all_four_relay_sockets():
    for flag in ("--rac-off", "--rac-on"):
        i = WIZARD.index(f"{flag})") if f"{flag})" in WIZARD else WIZARD.index(flag)
        block = WIZARD[i:i + 1500]
        for name in ("ssh", "web", "station", "gmag"):
            assert f"sigmond-vm-{name}-relay.socket" in block, (flag, name)


def test_wizard_verify_counts_every_declared_proxy_not_four():
    i = WIZARD.index("PROVE the tunnel")
    block = WIZARD[i:i + 2500]
    assert "-ge 4" not in block
    assert "grep -c '^name *=' /etc/sigmond/frpc-host.toml" in block


def test_wizard_records_the_rac_number_only_once_the_configuration_stands():
```

and replace it with:

```python
# These read the real wizard shell script as text (no execution).  Two checks
# that once lived here moved with their code into the switch script, and
# tests/test_rac_switch.py now runs them: both spellings of the switch name
# all four relay sockets, and the wait counts every proxy frpc-host.toml
# declares, never a fixed four.

def test_wizard_records_the_rac_number_only_once_the_configuration_stands():
```

Run: `.venv/bin/pytest tests/test_pm_align.py tests/test_wizard_rac_register.py -q`
Expected: `139 passed` (111 and Task 1's 28).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: `3581 passed, 1 warning, 65 subtests passed` (3526 after Task 1, 57 new, 2 removed).

- [ ] **Step 7: Watch nine guards fail, then restore**

```bash
cd /home/mjh/hamsci/repos/sigmond
F=scripts/proxmox/sigmond-wizard.sh
cp $F $F.keep
mut(){ cp $F.keep $F; python3 - "$F" "$1" "$2" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
assert s.count(sys.argv[2]) == 1, "the mutation's anchor must occur exactly once"
p.write_text(s.replace(sys.argv[2], sys.argv[3]))
PY
.venv/bin/pytest tests/test_rac_switch.py -q 2>&1 | grep -E '^FAILED|passed|failed' | cut -c1-160; }

# 1. a word that only begins right gets through
mut '    on|off|status) ;;' \
    '    on*|off*|status*) ;;'
# -> 7 failed, 50 passed: test_anything_but_one_of_the_three_words_is_refused_and_changes_nothing, test_a_request_through_the_forced_command_is_read_as_strictly

# 2. `off` stops the tunnel before the relays
mut '    systemctl disable --now $SOCKETS >/dev/null 2>&1
    systemctl disable --now "$UNIT" >/dev/null 2>&1' \
    '    systemctl disable --now "$UNIT" >/dev/null 2>&1
    systemctl disable --now $SOCKETS >/dev/null 2>&1'
# -> 1 failed, 56 passed: test_off_records_the_wish_and_disables_the_relays_then_the_tunnel

# 3. a terminal that goes away kills `off` half way
mut 'trap '"'"''"'"' HUP PIPE
' \
    '
'
# -> 1 failed, 56 passed: test_off_finishes_when_its_terminal_has_gone_away

# 4. `status` adopts a wish for a station that holds none
mut 'if [ "$WORD" = status ]; then
    read_state' \
    'if [ "$WORD" = status ]; then
    set_wish "$(wish)"; read_state'
# -> 4 failed, 53 passed: test_without_a_wish_on_file_the_wish_reads_from_what_stands

# 5. a station from before v3.71 with its unit enabled and no registration reads on
mut '    if registered && systemctl is-enabled --quiet "$UNIT" 2>/dev/null; then echo on; else echo off; fi' \
    '    if systemctl is-enabled --quiet "$UNIT" 2>/dev/null; then echo on; else echo off; fi'
# -> 1 failed, 56 passed: test_without_a_wish_on_file_the_wish_reads_from_what_stands

# 6. the forced command's way in honours a test override
mut '    unset SIGMOND_RAC_MARK_DIR SIGMOND_RAC_TOML SIGMOND_RAC_RUN_DIR SIGMOND_RAC_SETUP \
          SIGMOND_RAC_API SIGMOND_RAC_WAIT_S SIGMOND_RAC_WAIT_STEP SIGMOND_RAC_LOCK_WAIT_S \
          SIGMOND_RAC_BY
    WORD=' \
    '    WORD='
# -> 1 failed, 56 passed: test_the_forced_command_passes_the_word_in_ssh_original_command

# 7. every `on` rewrites who changed the switch and when
mut '    [ "$(wish_on_file)" = "$new" ] && return 0
' \
    ''
# -> 1 failed, 56 passed: test_on_when_the_wish_already_reads_on_keeps_who_and_when

# 8. a change that cannot get the lock goes ahead all the same
mut '    echo "sigmond-rac-switch: another change is still running; nothing changed." >&2
    exit 1
' \
    '    echo "sigmond-rac-switch: another change is still running; nothing changed." >&2
'
# -> 1 failed, 56 passed: test_a_change_that_cannot_get_the_lock_gives_up_and_changes_nothing

# 9. `off` carries on when it could not record the wish
mut '    set_wish off || { echo "sigmond-rac-switch: could not record the wish; nothing changed." >&2; exit 1; }
' \
    '    set_wish off || echo "sigmond-rac-switch: could not record the wish; nothing changed." >&2
'
# -> 1 failed, 56 passed: test_a_wish_that_cannot_be_recorded_changes_nothing

mv $F.keep $F
.venv/bin/pytest tests/test_rac_switch.py -q | tail -1      # 57 passed
```

- [ ] **Step 8: Commit**

```bash
cd /home/mjh/hamsci/repos/sigmond
test "$(grep -c . .git/ATTRIBUTION)" = 2 || { echo "write .git/ATTRIBUTION first (Global Constraints)"; exit 1; }
git add scripts/proxmox/sigmond-wizard.sh tests/test_rac_switch.py tests/test_pm_align.py
git commit -m "wizard: one switch script turns the remote-access tunnel on or off

/usr/local/sbin/sigmond-rac-switch takes exactly on, off or status and
refuses anything else.  on records the wish, registers a station that
never did, brings the relays and the tunnel up and waits for every
declared channel.  off records the wish and disables them, the tunnel
last, and finishes even when its terminal goes away.  status changes
nothing.  The wish lives in /etc/sigmond-appliance/rac-desired; every
run leaves the facts in /run/sigmond/rac-state.

The wizard carries the script as a heredoc and installs it.
sigmond-setup --rac-on and --rac-off now run it, and so does the
wizard's own install path.

tasks/plan-remote-access.md section 1.

$(cat .git/ATTRIBUTION)"
```

### Task 3: the wizard asks nothing; every install turns remote access on; a timer retries

Spec, "Decided on review": "The wizard loses its remote-access question and option altogether." Spec section 3:
"The wizard no longer asks. It says what registration sends (the station's name and the host's public key),
registers, brings the tunnel up, and names the command that turns it off." And: "An install with no internet, or
with the gateway down, still finishes. The wish reads `on`; a timer retries registration until it succeeds and
then starts the tunnel. An operator who has turned the switch off in the meantime stays off: the timer registers
and stops there."

Four pieces change.

- **`ask_rac`** keeps the two questions that pick the gateway (DASI or not, and the unit number) and loses
  `Enable remote access? [Y/n]`. In its place it prints what registration sends and the three `smd remote`
  commands. The variable `RAC_NUM` disappears with the question.
- **`rac_activate`** replaces the activation block. A first install turns the switch on. A later run asks the
  switch for the wish and leaves it alone (ruling 4), so a station whose owner declined before v3.71, or turned
  the switch off since, stays as it stands. The function never fails the wizard.
- **A retry timer.** `sigmond-rac-register.timer` fires every 10 minutes and runs `sigmond-setup --rac-retry`.
  The switch arms it when `on` finds no gateway, and the run that registers disarms it. A try that fails writes
  one line to the journal when the reason changes, and nothing otherwise (ruling 6): a station that sits offline
  for a week must not write a thousand copies of one line.
- **The switch script** gains those two `systemctl` lines and says, for a station that has not registered, that
  the host keeps trying.

The wizard registers a first install *through the switch*, in one attempt: the switch runs
`sigmond-setup --rac-register` in a process of its own, exactly as it will for `smd remote on` and for the timer.
So the install exercises the path the field uses. That process knows nothing of the wizard run's variables, which
is why `rac_record_inputs` writes the station's name, class and any pinned gateway to disk first.

**Files:**
- Modify: `sigmond/scripts/proxmox/sigmond-wizard.sh` — the header; `rac_install_host_files` (two units);
  `rac_retry` and the `--rac-retry` branch; three places in the switch script; `ask_rac`; the review screen; the
  activation block. Line numbers below name the file at Task 2's head.
- Create: `sigmond/tests/test_wizard_remote_default.py`
- Modify: `sigmond/tests/test_rac_switch.py` — two tests change with the switch, two join them

**Interfaces:**
- Consumes: Task 1's `rac_register ensure|fresh`, `rac_site_name`, `rac_save_pin`, `rac_install_relays`; Task 2's
  `rac_install_host_files`, `$RAC_SWITCH`, `$RAC_STATE_FILE` (its `wish=` line), the switch's state line and
  exit 3.
- Produces:
  - The wizard reads one answer fewer. After the DASI question (and the unit number, for a DASI station) the next
    answer it reads is the PSWS station ID. Task 10's rig depends on this.
  - `rac_activate` — sets `$RAC_STATE` for the summary and always returns 0. `rac_record_inputs`.
  - `rac_retry` and `sigmond-setup --rac-retry` (always exit 0).
  - `/etc/systemd/system/sigmond-rac-register.service` and `.timer`, written by `rac_install_host_files`.
    `RAC_RETRY_NOTE=/run/sigmond/rac-retry.reason`.
  - The switch: `on` runs `systemctl disable --now sigmond-rac-register.timer` once the station holds a
    registration, and `systemctl enable --now` on it when registration found no gateway. `off` leaves the timer
    alone. For an unregistered station the answer's second and third lines read
    `The station has not registered yet: no gateway has answered.` and
    `This host tries again every 10 minutes and brings the tunnel up when one does.`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wizard_remote_default.py`:

```python
"""Every install registers for remote access and brings the tunnel up; the
wizard asks nothing about it (tasks/plan-remote-access.md, "Decided on
review").  A later wizard run leaves the switch where its operator put it,
and a timer retries a registration that found no gateway.

These tests run the wizard's own functions, cut out of the shipped script by
name, with the host's commands stubbed.
"""
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"
TEXT = WIZARD.read_text()

ON_LINES = ("Remote access: on\n"
            "  The gateway accepted 6 of 6 channels (RAC 507 on gw2.wsprdaemon.org, wsprdaemon (secure)).\n"
            "  Turn it off:  smd remote off   (in the decoder VM)\n")
DOWN_LINES = ("Remote access: wanted on, not up\n"
              "  The station has not registered yet: no gateway has answered.\n"
              "  This host tries again every 10 minutes and brings the tunnel up when one does.\n")


def shell_func(name: str) -> str:
    out, keep = [], False
    for ln in TEXT.splitlines(True):
        if ln.startswith(f"{name}(){{") or ln.startswith(f"{name}() {{"):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, f"{name} not found in the wizard"
    return "".join(out)


def heredoc(path: str) -> str:
    m = re.search(r"cat > %s <<'([A-Z]+)'\n(.*?)\n\1\n" % re.escape(path), TEXT, re.S)
    assert m, f"the wizard writes no {path}"
    return m.group(2) + "\n"


def bash(script, stdin="", env=None):
    return subprocess.run(["bash", "-c", script], input=stdin, capture_output=True, text=True,
                          timeout=60, env=dict(os.environ, **(env or {})))


# -- the wizard asks nothing about remote access --------------------------------

ASK_PRELUDE = textwrap.dedent("""
    set -u
    RAC_BASE_VMSSH=35800; RAC_BASE_VMWEB=45800; RAC_BASE_HSSH=50800; RAC_BASE_HUI=55800
    RAC_REG_PORT=35737; RAC_PORT_SECURE=35736; RAC_PORT_UNSEC=35735
    _hs=vpn.hamsci.org; _wd=gw2.wsprdaemon.org
    RAC_SERVER=gw2.wsprdaemon.org
    HAVE_RX888=1; HAVE_GPSDO=1; HAVE_TS1=1; HAVE_MAG=1
    rd(){ read "$@" || { echo "STDIN-RAN-DRY"; exit 9; }; }
""")


def ask_rac(stdin, profile="dasi", dasi="", missing=False):
    script = (ASK_PRELUDE + f'RAC_PROFILE={profile}; DASI_NUM="{dasi}"\n'
              + ("HAVE_RX888=0\n" if missing else "")
              + shell_func("build_rac_tiers") + shell_func("ask_rac")
              + 'ask_rac\nIFS= read -r rest; echo "LEFT=$rest"; echo "PROFILE=$RAC_PROFILE DASI=$DASI_NUM"\n')
    return bash(script, stdin=stdin)


def test_an_ordinary_station_answers_one_question_and_none_about_remote_access():
    r = ask_rac("n\nNEXT-ANSWER\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "LEFT=NEXT-ANSWER" in r.stdout          # exactly one line consumed
    assert "PROFILE=standard" in r.stdout
    assert "Enable remote access" not in r.stdout


def test_a_dasi_station_answers_its_class_and_number_and_nothing_more():
    r = ask_rac("y\n3\nNEXT-ANSWER\n")
    assert "LEFT=NEXT-ANSWER" in r.stdout, r.stdout
    assert "PROFILE=dasi DASI=3" in r.stdout
    assert "DASI-003 maps deterministically to RAC #223" in r.stdout


def test_the_wizard_says_what_registration_sends_and_how_to_switch_it():
    out = ask_rac("n\n\n").stdout
    assert "Remote access comes up by itself; this wizard asks nothing about it." in out
    assert "this station's name" in out and "this host's public key" in out
    for cmd in ("smd remote off", "smd remote on", "smd remote status"):
        assert cmd in out, cmd
    assert "sigmond-setup --rac-off" not in out    # the host has no keyboard after install


def test_missing_equipment_still_gets_its_note_without_a_decision():
    out = ask_rac("n\n\n", missing=True).stdout
    assert "Some equipment was not detected" in out
    assert "strongly recommended" not in out       # nothing left to recommend: it comes up


def test_no_trace_of_the_question_or_its_answer_remains():
    for gone in ("Enable remote access?", "RAC_NUM", "RAC_EN", "4) Remote:    disabled"):
        assert gone not in TEXT, gone
    review = TEXT[TEXT.index("── Review — nothing is applied yet"):TEXT.index('rd -r -p "Apply?')]
    assert "smd remote off / on" in review
    assert "4) ask_rac;;" in TEXT                  # entry 4 still re-asks the station class


# -- what a wizard run does about the tunnel ---------------------------------------

def activate(tmp_path, *, configured=False, wish=None, registered=False, switch_wish="on",
             on_rc=0, on_out=ON_LINES, install_rc=0, register_rc=0, tier=None, env=None):
    mark = tmp_path / "marks"
    mark.mkdir()
    if configured:
        (mark / ".configured").write_text("N0CALL/T1 EM00aa 2026-10-01\n")
    if wish:
        (mark / "rac-desired").write_text(wish + "\n")
    if registered:
        (tmp_path / "frpc-host.toml").write_text('user = "x"\n')
    if tier:
        (mark / "rac-tier").write_text(tier + "\n")
    (tmp_path / "on.out").write_text(on_out)
    switch = tmp_path / "switch"
    switch.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        echo "switch[${{SIGMOND_RAC_BY:-}}] $* site=$(cat {mark}/rac-site 2>/dev/null)" >> {tmp_path}/calls
        case "$1" in
            status) echo "wish={switch_wish}" > {tmp_path}/rac-state; exit 0 ;;
            on) cat {tmp_path}/on.out; exit {on_rc} ;;
        esac
        """))
    switch.chmod(0o755)
    script = textwrap.dedent(f"""
        set -u
        MARK_DIR={mark}
        CONF_MARK={mark}/.configured
        LOG={tmp_path}/wizard.log
        RAC_TOML={tmp_path}/frpc-host.toml
        RAC_SWITCH={switch}
        RAC_STATE_FILE={tmp_path}/rac-state
        RAC_PIN_FILE={mark}/rac-endpoint.env
        RAC_PROFILE=standard; DASI_NUM=""; REPORTER=N0CALL/T1; RAC_FAIL=""; RAC_STATE=""
        say(){{ echo "[wizard] $*"; }}
        install(){{ :; }}
        rac_install_host_files(){{ echo install-host-files >> {tmp_path}/calls; return {install_rc}; }}
        rac_install_relays(){{ echo install-relays >> {tmp_path}/calls; }}
        rac_register(){{ echo "register $1" >> {tmp_path}/calls; RAC_FAIL="no gateway tier answered"; return {register_rc}; }}
    """) + "".join(shell_func(n) for n in ("rac_site_name", "rac_save_pin", "rac_record_inputs",
                                           "rac_activate"))
    script += 'rac_activate; echo "rc=$?"; printf "STATE<<%s>>\\n" "$RAC_STATE"\n'
    r = bash(script, env=env)
    calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
    state = r.stdout.split("STATE<<", 1)[1].rsplit(">>", 1)[0] if "STATE<<" in r.stdout else None
    return r, calls, state


def test_a_first_install_turns_remote_access_on_and_asks_the_switch_once(tmp_path):
    r, calls, state = activate(tmp_path)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert calls == ["install-host-files", "install-relays",
                     "switch[install] on site=N0CALL_T1"]
    assert state.splitlines()[0] == "on"
    assert "The gateway accepted 6 of 6 channels" in state
    assert "smd remote off" in state


def test_the_inputs_a_lone_registration_needs_are_on_disk_before_the_switch_runs(tmp_path):
    r, calls, _ = activate(tmp_path, env={"SIGMOND_RAC_SERVER": "127.0.0.1"})
    assert calls[-1].endswith("site=N0CALL_T1")    # the switch stub read it at call time
    marks = tmp_path / "marks"
    assert (marks / "rac-profile").read_text() == "standard\n"
    assert (marks / "rac-endpoint.env").read_text() == "SIGMOND_RAC_SERVER=127.0.0.1\n"


def test_a_first_install_with_no_gateway_still_finishes(tmp_path):
    r, calls, state = activate(tmp_path, on_rc=3, on_out=DOWN_LINES)
    assert "rc=0" in r.stdout                      # the wizard carries on
    assert state.splitlines()[0] == "wanted on, not up"
    assert "tries again every 10 minutes" in state
    assert "register fresh" not in calls           # the switch registers; one attempt, not two


def test_a_later_run_keeps_a_switch_its_operator_turned_off(tmp_path):
    r, calls, state = activate(tmp_path, configured=True, wish="off", registered=True,
                               switch_wish="off")
    assert "rc=0" in r.stdout
    assert [c for c in calls if c.startswith("switch")] == ["switch[] status site=N0CALL_T1"]
    assert "register fresh" not in calls
    assert state == "off, as its operator left it. Turn it on from the decoder VM: smd remote on"


def test_a_station_from_before_v371_whose_owner_declined_stays_as_it_stands(tmp_path):
    # Configured, no wish on file, never registered: the switch reads "off".
    r, calls, state = activate(tmp_path, configured=True, switch_wish="off")
    assert not any(" on " in c for c in calls)
    assert state.startswith("off, as its operator left it")


def test_a_later_run_registers_afresh_before_it_asks_the_switch(tmp_path):
    r, calls, state = activate(tmp_path, configured=True, wish="on", registered=True)
    assert calls == ["install-host-files", "install-relays", "switch[] status site=N0CALL_T1",
                     "register fresh", "switch[install] on site=N0CALL_T1"]
    assert state.splitlines()[0] == "on"


def test_a_failed_fresh_registration_warns_and_keeps_the_tunnel(tmp_path):
    r, calls, state = activate(tmp_path, configured=True, wish="on", registered=True,
                               register_rc=1)
    assert "could not register afresh (no gateway tier answered); the earlier registration stays" in r.stdout
    assert calls[-1] == "switch[install] on site=N0CALL_T1"
    assert state.splitlines()[0] == "on"


def test_a_host_that_cannot_take_the_switch_script_says_so_and_carries_on(tmp_path):
    r, calls, state = activate(tmp_path, install_rc=1)
    assert "rc=0" in r.stdout
    assert calls == ["install-host-files"]
    assert state.startswith("FAILED: could not install")


def test_an_unsecured_tier_keeps_its_warning(tmp_path):
    _, _, state = activate(tmp_path, tier="wsprdaemon (UNSECURED)")
    assert "UNSECURED (no TLS)" in state and "sigmond-setup --rac-upgrade" in state
    _, _, state = activate(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path,
                           tier="wsprdaemon (secure)")
    assert "UNSECURED" not in state


def test_the_wizard_calls_the_activation_once_and_before_it_marks_the_host_configured():
    assert TEXT.count("\nrac_activate\n") == 1
    assert TEXT.index("\nrac_activate\n") < TEXT.index('> "$CONF_MARK"')
    assert TEXT.index("\nrac_activate\n") > TEXT.index("rac_install_relays(){")


# -- the retry timer ------------------------------------------------------------------

def retry(tmp_path, *, register_rc, fail="no gateway tier answered (tried: wsprdaemon (secure))",
          wish=None, note=None):
    mark = tmp_path / "marks"
    mark.mkdir(exist_ok=True)
    (mark / "rac-number").write_text("507\n")
    if wish:
        (mark / "rac-desired").write_text(wish + "\n")
    if note is not None:
        (tmp_path / "reason").write_text(note + "\n")
    calls = tmp_path / "calls"
    if calls.exists():
        calls.unlink()
    switch = tmp_path / "switch"
    switch.write_text(f'#!/bin/bash\necho "switch[${{SIGMOND_RAC_BY:-}}] $*" >> {calls}\n'
                      'echo "Remote access: on"\n')
    switch.chmod(0o755)
    script = textwrap.dedent(f"""
        set -u
        MARK_DIR={mark}
        LOG={tmp_path}/wizard.log
        RAC_SWITCH={switch}
        RAC_RETRY_NOTE={tmp_path}/reason
        RAC_FAIL=""
        say(){{ echo "[wizard] $*"; }}
        systemctl(){{ echo "systemctl $*" >> {calls}; }}
        rac_register(){{ say "narration that must stay out of the journal"; RAC_FAIL='{fail}'; return {register_rc}; }}
    """) + shell_func("rac_retry") + 'rac_retry; echo "rc=$?"\n'
    r = bash(script)
    return r, (calls.read_text().splitlines() if calls.exists() else [])


def test_a_try_that_finds_no_gateway_says_so_once_and_leaves_the_timer_armed(tmp_path):
    r, calls = retry(tmp_path, register_rc=1)
    assert r.stdout.count("not registered yet: no gateway tier answered") == 1
    assert "rc=0" in r.stdout                      # a failed try is not a failed unit
    assert calls == []
    assert "narration" not in r.stdout
    r2, _ = retry(tmp_path, register_rc=1)
    assert "not registered yet" not in r2.stdout   # the same reason: silence
    r3, _ = retry(tmp_path, register_rc=1, fail="sigmond-rac payload missing (frpc/CA)")
    assert "not registered yet: sigmond-rac payload missing" in r3.stdout


def test_a_try_that_registers_disarms_the_timer_and_brings_a_wanted_tunnel_up(tmp_path):
    r, calls = retry(tmp_path, register_rc=0, wish="on", note="no gateway tier answered")
    assert calls == ["systemctl disable --now sigmond-rac-register.timer", "switch[timer] on"]
    assert "registered as RAC #507" in r.stdout
    assert not (tmp_path / "reason").exists()


@pytest.mark.parametrize("wish", ["off", None], ids=["turned-off-meanwhile", "no-wish-on-file"])
def test_a_try_that_registers_stops_there_when_the_operator_holds_it_off(tmp_path, wish):
    r, calls = retry(tmp_path, register_rc=0, wish=wish)
    assert calls == ["systemctl disable --now sigmond-rac-register.timer"]
    assert "the operator holds it off, so the tunnel stays down" in r.stdout


def test_the_timer_runs_the_retry_and_nothing_fixed_to_a_clock():
    service = heredoc("/etc/systemd/system/sigmond-rac-register.service")
    timer = heredoc("/etc/systemd/system/sigmond-rac-register.timer")
    assert "ExecStart=/usr/local/sbin/sigmond-setup --rac-retry\n" in service
    assert "Type=oneshot\n" in service
    assert "OnUnitInactiveSec=10min\n" in timer and "OnCalendar" not in timer
    assert "WantedBy=timers.target\n" in timer
    i = TEXT.index("    --rac-retry)")
    assert "rac_retry" in TEXT[i:TEXT.index("    --rac-off|--rac-on)", i)]
    assert i < TEXT.index("waiting for decoder VM")


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_the_retry_units_parse(tmp_path):
    for name in ("sigmond-rac-register.service", "sigmond-rac-register.timer"):
        (tmp_path / name).write_text(heredoc(f"/etc/systemd/system/{name}"))
    r = subprocess.run(["systemd-analyze", "verify", str(tmp_path / "sigmond-rac-register.timer")],
                       capture_output=True, text=True, timeout=60)
    noise = [ln for ln in (r.stdout + r.stderr).splitlines()
             if "sigmond-rac-register" in ln and "sigmond-setup" not in ln]
    assert noise == [], noise
```

Then change `tests/test_rac_switch.py`. The switch now touches the retry timer, so two tests expect more of it,
and two new ones pin the timer's part:

(a) Find (lines 22-23):

```python
UNIT = "sigmond-rac-host.service"
ON, OFF, DOWN = "Remote access: on", "Remote access: off", "Remote access: wanted on, not up"
```

and replace it with:

```python
UNIT = "sigmond-rac-host.service"
RETRY_TIMER = "sigmond-rac-register.timer"
ON, OFF, DOWN = "Remote access: on", "Remote access: off", "Remote access: wanted on, not up"
```

(b) Find (lines 285-286):

```python
    assert h.wish() == "on"
    assert h.changes() == ["enable --now " + " ".join(SOCKETS),
```

and replace it with:

```python
    assert h.wish() == "on"
    assert h.changes() == [f"disable --now {RETRY_TIMER}",       # nothing left to retry
                           "enable --now " + " ".join(SOCKETS),
```

(c) Find (lines 313-315):

```python
    assert "has not registered with a gateway yet" in r.stdout
    assert not any(c.startswith("enable") for c in h.changes())
    assert h.state()["state"] == "down" and h.state()["registered"] == "no"
```

and replace it with:

```python
    assert "has not registered yet: no gateway has answered" in r.stdout
    assert "tries again every 10 minutes and brings the tunnel up when one does" in r.stdout
    # The wish stands and the timer keeps trying; nothing else gets enabled.
    assert h.changes() == [f"enable --now {RETRY_TIMER}",
                           "start --no-block sigmond-issue.service"]
    assert h.state()["state"] == "down" and h.state()["registered"] == "no"


def test_on_that_registers_disarms_the_retry_timer(tmp_path):
    h = Host(tmp_path, registered=False, wish="on")
    (tmp_path / "gateway-answers").touch()
    r = h.run("on")
    assert r.returncode == 0
    assert h.changes()[0] == f"disable --now {RETRY_TIMER}"
    assert not any(c.startswith("enable") and RETRY_TIMER in c for c in h.changes())


def test_off_leaves_the_retry_timer_alone(tmp_path):
    # A station that turned the switch off before it could register still
    # registers when a gateway answers, and stops there.  So `off` never
    # disarms the timer.
    h = Host(tmp_path, registered=False, wish="on")
    h.run("off")
    assert not any(RETRY_TIMER in c for c in h.changes())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_wizard_remote_default.py -q`
Expected: `21 failed`.

Run: `.venv/bin/pytest tests/test_rac_switch.py -q`
Expected: `3 failed, 56 passed`. The three: `test_on_enables_relays_and_tunnel_and_waits_for_every_declared_channel`,
`test_on_with_no_gateway_keeps_the_wish_and_reports_not_up` and `test_on_that_registers_disarms_the_retry_timer`.
`test_off_leaves_the_retry_timer_alone` passes already; it pins `off` against Step 3.

- [ ] **Step 3: Implement in `scripts/proxmox/sigmond-wizard.sh`**

(a) Find (lines 11-20):

```bash
#          remote access (RAC) — just Y/n. The RAC registrar ($RAC_SERVER,
#              configurable via SIGMOND_RAC_SERVER/_REGISTRAR) auto-assigns
#              a free RAC number (sites are identified by reporter ID;
#              the number is plumbing) and the wizard brings up ONE
#              host-side frpc carrying FOUR channels: VM ssh (35800+n),
#              VM ka9q-web (45800+n), host ssh (50800+n), Proxmox UI
#              (55800+n). The vm-* channels go through local relays that
#              ask the guest agent for the VM's CURRENT IP per connection,
#              so they survive DHCP changes AND the host stays reachable
#              even when the VM is down.
```

and replace it with:

```bash
#          station class — DASI or not, and the DASI unit number.  It picks
#              the gateway ladder for remote access.
#
# Remote access (RAC) carries no question.  Every install registers with the
#          gateway and brings the tunnel up (tasks/plan-remote-access.md).
#          The registrar ($RAC_SERVER, configurable via SIGMOND_RAC_SERVER/
#          _REGISTRAR) assigns a free RAC number, and ONE host-side frpc
#          carries six channels: VM ssh (35800+n), VM ka9q-web (45800+n),
#          station-web (48800+n), magnetometer (49800+n), host ssh (50800+n)
#          and the Proxmox UI (55800+n).  The vm-* channels go through local
#          relays, so the host stays reachable even when the VM is down.
#          The operator holds the switch, in the decoder VM: smd remote off,
#          smd remote on, smd remote status.
```

(b) Find (lines 29-30):

```bash
#                              configuration; asks nothing, starts nothing
#              --rac-upgrade   climb to a more secure gateway tier if one
```

and replace it with:

```bash
#                              configuration; asks nothing, starts nothing
#              --rac-retry     what sigmond-rac-register.timer runs: register a
#                              station that could not at install, then bring
#                              the tunnel up if its operator still wants it up
#              --rac-upgrade   climb to a more secure gateway tier if one
```

(c) Find (lines 657-658):

```bash
SOCKETS="sigmond-vm-ssh-relay.socket sigmond-vm-web-relay.socket sigmond-vm-station-relay.socket sigmond-vm-gmag-relay.socket"
PAIR_USER=sigmond-remote
```

and replace it with:

```bash
SOCKETS="sigmond-vm-ssh-relay.socket sigmond-vm-web-relay.socket sigmond-vm-station-relay.socket sigmond-vm-gmag-relay.socket"
RETRY_TIMER=sigmond-rac-register.timer
PAIR_USER=sigmond-remote
```

(d) Find (lines 779-780):

```bash
                echo "  The station has not registered with a gateway yet."
                echo "  Try again:  smd remote on   (in the decoder VM)"
```

and replace it with:

```bash
                echo "  The station has not registered yet: no gateway has answered."
                echo "  This host tries again every 10 minutes and brings the tunnel up when one does."
```

(e) Find (lines 851-852):

```bash
if registered; then
    # shellcheck disable=SC2086
```

and replace it with:

```bash
if registered; then
    systemctl disable --now "$RETRY_TIMER" >/dev/null 2>&1
    # shellcheck disable=SC2086
```

(f) Find (lines 856-857):

```bash
    wait_for_channels
fi
```

and replace it with:

```bash
    wait_for_channels
else
    # No gateway answered.  The wish stands, and the timer keeps trying.
    systemctl enable --now "$RETRY_TIMER" >/dev/null 2>&1
fi
```

(g) Find (lines 865-866):

```bash
    chmod 755 /usr/local/sbin/sigmond-rac-switch || return 1
    bash -n /usr/local/sbin/sigmond-rac-switch
```

and replace it with:

```bash
    chmod 755 /usr/local/sbin/sigmond-rac-switch || return 1
    bash -n /usr/local/sbin/sigmond-rac-switch || return 1
    # The retry timer.  The switch arms it when a station wants remote access
    # and no gateway answers, and the run that registers disarms it.
    cat > /etc/systemd/system/sigmond-rac-register.service <<'RACREGSVCEOF'
[Unit]
Description=Sigmond remote access: register with the gateway, then bring the tunnel up if wanted
[Service]
Type=oneshot
TimeoutStartSec=900
ExecStart=/usr/local/sbin/sigmond-setup --rac-retry
RACREGSVCEOF
    cat > /etc/systemd/system/sigmond-rac-register.timer <<'RACREGTMREOF'
[Unit]
Description=Sigmond remote access: retry the gateway registration until it succeeds
[Timer]
OnActiveSec=10min
OnUnitInactiveSec=10min
RandomizedDelaySec=60
[Install]
WantedBy=timers.target
RACREGTMREOF
    systemctl daemon-reload
}

RAC_RETRY_NOTE=/run/sigmond/rac-retry.reason
rac_retry(){
    # What sigmond-rac-register.timer runs.  Register a station that could
    # not at install.  Then bring the tunnel up, but only if its operator
    # still wants it up.  A try that fails ends quietly: the timer tries
    # again, and the journal gets one line per reason, never one per try.
    local last
    say(){ :; }
    LOG=/dev/null
    if ! rac_register ensure; then
        last=$(cat "$RAC_RETRY_NOTE" 2>/dev/null)
        if [ "$RAC_FAIL" != "$last" ]; then
            mkdir -p "$(dirname "$RAC_RETRY_NOTE")" 2>/dev/null
            printf '%s\n' "$RAC_FAIL" > "$RAC_RETRY_NOTE" 2>/dev/null
            echo "remote access: not registered yet: $RAC_FAIL. This host tries again every 10 minutes; this line returns only when the reason changes."
        fi
        return 0
    fi
    rm -f "$RAC_RETRY_NOTE"
    systemctl disable --now sigmond-rac-register.timer >/dev/null 2>&1
    echo "remote access: registered as RAC #$(cat "$MARK_DIR/rac-number" 2>/dev/null)"
    if [ "$(head -1 "$MARK_DIR/rac-desired" 2>/dev/null)" = on ]; then
        SIGMOND_RAC_BY=timer "$RAC_SWITCH" on
        return 0
    fi
    echo "remote access: the operator holds it off, so the tunnel stays down"
    return 0
```

(h) Find (lines 879-880):

```bash
        exit 1;;
    --rac-off|--rac-on)
```

and replace it with:

```bash
        exit 1;;
    --rac-retry)
        rac_retry
        exit 0;;
    --rac-off|--rac-on)
```

(i) Find (lines 1689-1692):

```bash
    echo "Remote access (RAC) is a reverse tunnel to the fleet gateway so the"
    echo "admin can reach this station for support.  Keys, credentials and"
    echo "channel numbers are all handled automatically, and you can turn it"
    echo "off any time with:  sigmond-setup --rac-off"
```

and replace it with:

```bash
    echo "Remote access comes up by itself; this wizard asks nothing about it."
    echo "It gives your fleet admin the only effective way to support this"
    echo "station: a tunnel the station dials out, with nothing opened on your"
    echo "router."
    echo "  Registering sends two things to the gateway: this station's name"
    echo "  and this host's public key."
    echo "  You hold the switch. In the decoder VM, at any time:"
    echo "      smd remote off      turn it off (it stays off across reboots)"
    echo "      smd remote on       turn it on again"
    echo "      smd remote status   show the state"
```

(j) Find (lines 1703-1714):

```bash
    # When equipment is missing this is the single most valuable thing the
    # install can still achieve: it is how someone remote reaches the station
    # to help finish it.  Say so at the moment of the decision.
    if [ "$HAVE_RX888" = 0 ] || [ "$HAVE_GPSDO" = 0 ] || [ "$HAVE_TS1" = 0 ] || [ "$HAVE_MAG" = 0 ]; then
        echo ""
        echo "  ** Some equipment was not detected (see above).  Enabling remote"
        echo "     access is strongly recommended: it lets the fleet admin connect"
        echo "     and help finish the install once the missing parts are fitted. **"
    fi
    rd -r -p "Enable remote access? [Y/n] " RAC_EN
    RAC_NUM=""
    case "${RAC_EN:-Y}" in [Nn]*) ;; *) RAC_NUM="auto";; esac
```

and replace it with:

```bash
    # When equipment is missing, the tunnel is the most valuable thing the
    # install can still achieve: it lets someone remote help finish the build.
    if [ "$HAVE_RX888" = 0 ] || [ "$HAVE_GPSDO" = 0 ] || [ "$HAVE_TS1" = 0 ] || [ "$HAVE_MAG" = 0 ]; then
        echo ""
        echo "  ** Some equipment was not detected (see above).  Remote access lets"
        echo "     the fleet admin connect and help finish the install once the"
        echo "     missing parts are fitted. **"
    fi
```

(k) Find (lines 1851-1861):

```bash
    if [ -n "$RAC_NUM" ]; then
        _rgw="$(echo "$RAC_TIERS" | head -1 | cut -d'|' -f2)"
        if [ -n "$DASI_NUM" ]; then
            _rwhich="DASI-$(printf '%03d' "$DASI_NUM") → RAC $((220 + DASI_NUM))"
        else
            _rwhich="number auto-assigned by the gateway"
        fi
        echo "  4) Remote:    enabled via ${_rgw} ($_rwhich)"
    else
        echo "  4) Remote:    disabled"
    fi
```

and replace it with:

```bash
    _rgw="$(echo "$RAC_TIERS" | head -1 | cut -d'|' -f2)"
    if [ -n "$DASI_NUM" ] && [ "$RAC_PROFILE" != "standard" ]; then
        _rwhich="DASI-$(printf '%03d' "$DASI_NUM") → RAC $((220 + DASI_NUM))"
    else
        _rwhich="number auto-assigned by the gateway"
    fi
    echo "  4) Remote:    via ${_rgw} ($_rwhich)"
    echo "                (comes up by itself; switch it in the decoder VM: smd remote off / on)"
```

(l) Find (lines 2580-2582):

```bash
fi

RAC_STATE="skipped"
```

and replace it with:

```bash
fi

RAC_STATE=""
```

(m) Find the lines that open the block (lines 2775-2812):

```bash
if [ -n "$RAC_NUM" ]; then
    say "activating remote access"
    if ! rac_payload_ready; then
```

and the lines that close it:

```bash
        fi
    fi
fi
```

Delete from the first of them through the last, 38 lines in all, and put in their place:

```bash
rac_record_inputs(){
    # What a registration that runs alone must know.  Written before the
    # switch runs, because the switch registers in a process of its own.
    printf '%s\n' "$RAC_PROFILE" > "$MARK_DIR/rac-profile"
    [ -n "$DASI_NUM" ] && printf '%s\n' "$DASI_NUM" > "$MARK_DIR/dasi-number"
    rac_site_name > "$MARK_DIR/rac-site"
    rac_save_pin
}
rac_activate(){
    # Remote access at the end of a wizard run (tasks/plan-remote-access.md).
    # A first install turns it on, for every station class, and asks nothing.
    # A later run leaves the switch where its operator put it.  Sets
    # RAC_STATE for the summary.  It never fails the wizard: a station with
    # no gateway in reach still finishes its install, and the host keeps
    # trying.
    local want out rc
    if ! rac_install_host_files; then
        RAC_STATE="FAILED: could not install $RAC_SWITCH on this host"
        say "WARN: $RAC_STATE"
        return 0
    fi
    rac_install_relays
    install -m 755 /root/sigmond-appliance/sigmond/scripts/proxmox/pm-align.py /usr/local/sbin/pm-align 2>/dev/null \
        || say "WARN: pm-align not installed (sigmond tree lacks it)"
    rac_record_inputs
    if [ ! -e "$CONF_MARK" ] && [ ! -e "$MARK_DIR/rac-desired" ]; then
        want=on
    else
        "$RAC_SWITCH" status >/dev/null 2>&1
        want=$(sed -n 's/^wish=//p' "$RAC_STATE_FILE" 2>/dev/null | head -1)
    fi
    if [ "$want" != on ]; then
        RAC_STATE="off, as its operator left it. Turn it on from the decoder VM: smd remote on"
        say "remote access stays off, as its operator left it"
        return 0
    fi
    say "remote access: bringing it up (turn it off later from the decoder VM: smd remote off)"
    # A station that registered before registers again: its reporter id may
    # have changed.  A station that never did registers inside the switch.
    if [ -s "$RAC_TOML" ] && ! rac_register fresh; then
        say "WARN: could not register afresh ($RAC_FAIL); the earlier registration stays"
    fi
    out=$(SIGMOND_RAC_BY=install "$RAC_SWITCH" on 2>&1); rc=$?
    printf '%s\n' "$out" | sed 's/^/  /' | tee -a "$LOG"
    RAC_STATE=$(printf '%s\n' "$out" | sed -n '/^Remote access: /,$p' \
                | sed -e '1s/^Remote access: //' -e '2,$s/^/          /')
    [ -n "$RAC_STATE" ] || RAC_STATE="FAILED: $RAC_SWITCH gave no answer (exit $rc); try: sigmond-setup --rac-on"
    case "$(head -1 "$MARK_DIR/rac-tier" 2>/dev/null)" in
        *UNSECURED*|*unsecured*)
            RAC_STATE="$RAC_STATE
              ** this tunnel is UNSECURED (no TLS): the secure gateways did
                 not answer.  Once one is available run: sigmond-setup --rac-upgrade **" ;;
    esac
    return 0
}
rac_activate
```

Check: `bash -n scripts/proxmox/sigmond-wizard.sh && echo parses; grep -c 'RAC_NUM\|RAC_EN\|Enable remote access' scripts/proxmox/sigmond-wizard.sh`
Expected: `parses`, then `0`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_wizard_remote_default.py tests/test_rac_switch.py -q`
Expected: `80 passed` (21 and 59).

- [ ] **Step 5: Run the wider suites**

Run: `.venv/bin/pytest tests/test_wizard_rac_register.py tests/test_pm_align.py tests/test_wizard_wifi.py tests/test_wizard_uploads.py -q`
Expected: no failures. `tests/test_wizard_wifi.py` anchors on the line `ask_rac() {`, which stays.

Run: `.venv/bin/pytest -q`
Expected: `3604 passed, 1 warning, 65 subtests passed` (3581 after Task 2, and 23 new).

- [ ] **Step 6: Watch five guards fail, then restore**

```bash
cd /home/mjh/hamsci/repos/sigmond
F=scripts/proxmox/sigmond-wizard.sh
cp $F $F.keep
mut(){ cp $F.keep $F; python3 - "$F" "$1" "$2" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
assert s.count(sys.argv[2]) == 1, "the mutation's anchor must occur exactly once"
p.write_text(s.replace(sys.argv[2], sys.argv[3]))
PY
.venv/bin/pytest tests/test_wizard_remote_default.py tests/test_rac_switch.py -q 2>&1 | grep -E '^FAILED|passed|failed' | cut -c1-160; }

# 1. a wizard rerun turns on a switch its operator turned off
mut '    if [ "$want" != on ]; then' \
    '    if false; then'
# -> 2 failed, 78 passed: test_a_later_run_keeps_a_switch_its_operator_turned_off, test_a_station_from_before_v371_whose_owner_declined_stays_as_it_stands

# 2. the retry timer writes its line at every try
mut '        if [ "$RAC_FAIL" != "$last" ]; then' \
    '        if true; then'
# -> 1 failed, 79 passed: test_a_try_that_finds_no_gateway_says_so_once_and_leaves_the_timer_armed

# 3. the timer starts the tunnel of an operator who turned it off meanwhile
mut '    if [ "$(head -1 "$MARK_DIR/rac-desired" 2>/dev/null)" = on ]; then
        SIGMOND_RAC_BY=timer "$RAC_SWITCH" on' \
    '    if true; then
        SIGMOND_RAC_BY=timer "$RAC_SWITCH" on'
# -> 2 failed, 78 passed: test_a_try_that_registers_stops_there_when_the_operator_holds_it_off

# 4. the wizard asks about remote access again
mut '    echo "      smd remote status   show the state"
    echo ""
    if [ "$RAC_PROFILE" = "standard" ]; then' \
    '    echo "      smd remote status   show the state"
    echo ""
    rd -r -p "Enable remote access? [Y/n] " RAC_EN
    if [ "$RAC_PROFILE" = "standard" ]; then'
# -> 3 failed, 77 passed: test_an_ordinary_station_answers_one_question_and_none_about_remote_access, test_a_dasi_station_answers_its_class_and_number_and_nothing_more, test_no_trace_of_the_question_or_its_answer_remains

# 5. an `on` that finds no gateway arms no timer
mut '    systemctl enable --now "$RETRY_TIMER" >/dev/null 2>&1
' \
    '    :
'
# -> 1 failed, 79 passed: test_on_with_no_gateway_keeps_the_wish_and_reports_not_up

mv $F.keep $F
.venv/bin/pytest tests/test_wizard_remote_default.py tests/test_rac_switch.py -q | tail -1      # 80 passed
```

- [ ] **Step 7: Commit**

```bash
cd /home/mjh/hamsci/repos/sigmond
test "$(grep -c . .git/ATTRIBUTION)" = 2 || { echo "write .git/ATTRIBUTION first (Global Constraints)"; exit 1; }
git add scripts/proxmox/sigmond-wizard.sh tests/test_wizard_remote_default.py tests/test_rac_switch.py
git commit -m "wizard: every install turns remote access on, and asks nothing about it

The wizard loses 'Enable remote access? [Y/n]'.  It says what
registration sends, names smd remote off / on / status, and brings the
tunnel up through the switch script.  A first install turns the switch
on; a later run leaves it where its operator put it, so a station whose
owner declined before v3.71 stays as it stands.

An install with no gateway in reach still finishes.  The switch arms
sigmond-rac-register.timer, which retries every 10 minutes, writes one
journal line per reason, and brings the tunnel up once the station
registers, unless its operator turned the switch off meanwhile.

tasks/plan-remote-access.md section 3 and 'Decided on review'.

$(cat .git/ATTRIBUTION)"
```

### Task 4: the pairing — the VM asks, the host alone acts (`sigmond-setup --pair-vm`)

Spec section 2: "The wizard pairs the two machines at every install." It creates a key pair inside the VM, through
the guest agent. It creates a host account that can do one thing: the account's `authorized_keys` line carries
`restrict` and a forced command, and one sudoers rule allows that account exactly the switch script. And it writes
the host's public key into the VM "as the only host that key may talk to".

The spec's reasons shape every step here:

- **"The VM can never pair itself."** The host's half lives in files that only root on the host writes. This task
  adds no code to the VM at all; the VM's half arrives through the guest agent.
- **"The pairing can be re-asserted."** On AC0G-B4 an identity restore replaced a host key eight minutes after the
  wizard had authorized the old one, and the fleet's login broke for a week. So `--pair-vm` writes both halves
  again from what holds at that moment, and three units run it: after every boot, every 6 hours, and whenever
  the host's sshd key file changes (ruling 11).
- **"It does not ride the guest agent."** The pairing does, once per run, because nothing else reaches into the
  VM as root. The switch never does: `smd remote` (Task 5) uses ssh over the private link.

The guest agent taught three lessons on 2026-10-08, and each binds this code (Global Constraints). Every byte sent
into the guest travels base64-encoded, so the request holds ASCII only. What comes back counts as data from
another machine: the host accepts one ed25519 public key and nothing else, matched as a whole string. And the
pairing runs `ssh-keygen` and `ssh` through the agent, never `smd`, which would take 55 MB of the agent's 128.

**Decisions this task makes:**

1. **The account is `sigmond-remote`**, a system account with home `/var/lib/sigmond-remote`, shell `/bin/sh`
   (sshd needs a shell to run the forced command) and password field `*` (no password can match). Its home, its
   `.ssh` and its `authorized_keys` belong to root, so the account cannot change its own key.
2. **The key line** reads
   `restrict,from="<VM address>",command="sudo -n /usr/local/sbin/sigmond-rac-switch" <key> sigmond-rac-switch`
   (ruling 10 for `from=`). The file holds that one line; a later pairing replaces it and never appends.
3. **The sudoers file** `/etc/sudoers.d/sigmond-remote` resets the environment, keeps `SSH_ORIGINAL_COMMAND` and
   nothing else, and allows `/usr/local/sbin/sigmond-rac-switch ""`: the script with no argument. `visudo` checks
   a candidate before it takes effect.
4. **sudo.** A host without it gets it from apt, once (ruling 9). A host that cannot fetch it stays unpaired and
   says which command to run.
5. **The VM's half:** `/etc/sigmond/rac-switch.key` (mode 600, root), `rac-switch.key.pub`, and
   `rac-switch.known_hosts` holding one line: the host's address on the private link and the host's sshd key. The
   same script writes the login banner's hook, `/etc/update-motd.d/72-sigmond-remote`, which Task 5 answers.
6. **The pairing proves itself** before it reports success: one `status` request from the VM, through sshd and
   sudo, to the switch. That request also leaves the answer in `/var/lib/sigmond/remote-access.json`, where the
   login banner reads it (ruling 12).
7. **The pairing key stays out of the station identity** (`lib/sigmond/identity.py`). A reflash mints a new one
   and the wizard pairs again.

**Files:**
- Modify: `sigmond/scripts/proxmox/sigmond-wizard.sh` — the header; three helper functions move up; the pairing
  functions; three units in `rac_install_host_files`; the `--pair-vm` branch; the wizard run and its summary. Line
  numbers below name the file at Task 3's head.
- Create: `sigmond/tests/test_wizard_pair_vm.py`

**Interfaces:**
- Consumes: Task 2's `rac_install_host_files`, `$RAC_SWITCH`, and the switch's `status` (exit 0 or 3, state line
  first). The wizard's own `gexec <timeout> <command>` (returns 0 only when the guest answered with exit 0),
  `guest_out <timeout> <command>` (prints the guest's stdout) and `mgmt_vm_ip` (prints the VM's address on the
  private link).
- Produces:
  - `rac_pair_vm` — returns 0 once a request from the VM reached the switch; returns 1 with the reason in
    `$RAC_PAIR_FAIL`. Safe to run at any time.
  - `rac_pair_key_ok`, `rac_have_sudo`, `rac_pair_sudoers`, `rac_pair_authorized_line`, `rac_pair_guest_script`,
    `rac_pair_verify_script`; the variables `RAC_PAIR_USER`, `RAC_PAIR_HOME`, `RAC_PAIR_SUDOERS`,
    `RAC_PAIR_HOSTPUB`, `RAC_PAIR_FAIL`.
  - `sigmond-setup --pair-vm` — exit 0 and
    `paired: the decoder VM can run smd remote on, smd remote off and smd remote status`, or exit 1 and
    `NOT paired: <reason>`. It runs before the wizard waits for a VM or asks anything.
  - Units, written by `rac_install_host_files`: `sigmond-rac-pair.service`, `sigmond-rac-pair.timer`,
    `sigmond-rac-pair.path`.
  - In the VM: the three `rac-switch.*` files, the motd hook, and `/var/lib/sigmond/remote-access.json` as
    `{"state": "on"|"off"|"down", "checked_at": "<UTC, to the second>"}`.
  - The summary gains a line ` Switch:    $PAIR_STATE`.
  - `gexec`, `guest_out` and `mgmt_vm_ip` now stand above the dispatch, unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wizard_pair_vm.py`:

```python
"""The pairing: the decoder VM asks, the Proxmox host alone acts
(tasks/plan-remote-access.md section 2).

`sigmond-setup --pair-vm` gives the VM one ssh key, and gives that key one
thing it can run on the host: sigmond-rac-switch, with one of its three words.
These tests run the wizard's own functions, cut out of the shipped script by
name.  The two scripts the host sends into the guest run here for real, in a
scratch directory.  The rig proves the rest against a real sshd and a real
sudo (sigmond-appliance test-nested-v3.sh).
"""
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"
TEXT = WIZARD.read_text()

HOSTKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostHostHostHostHostHostHostHostHostHostHost"
VMKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIVmVmVmVmVmVmVmVmVmVmVmVmVmVmVmVmVmVmVmVmVm"
PAIR_FUNCS = ("rac_pair_key_ok", "rac_have_sudo", "rac_pair_sudoers", "rac_pair_authorized_line",
              "rac_pair_guest_script", "rac_pair_verify_script", "rac_pair_vm")
VISUDO = shutil.which("visudo") or ("/usr/sbin/visudo" if os.path.exists("/usr/sbin/visudo") else None)


def shell_func(name: str) -> str:
    out, keep = [], False
    for ln in TEXT.splitlines(True):
        if ln.startswith(f"{name}(){{") or ln.startswith(f"{name}() {{"):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}" or (len(out) == 1 and ln.rstrip().endswith("}")):
                break
    assert out, f"{name} not found in the wizard"
    return "".join(out)


def heredoc(path: str) -> str:
    m = re.search(r"cat > %s <<'([A-Z]+)'\n(.*?)\n\1\n" % re.escape(path), TEXT, re.S)
    assert m, f"the wizard writes no {path}"
    return m.group(2) + "\n"


PRELUDE = textwrap.dedent("""
    set -u
    RAC_SWITCH=/usr/local/sbin/sigmond-rac-switch
    RAC_PAIR_USER=sigmond-remote
    RAC_PAIR_FAIL=""
""")


def bash(script, env=None, **kw):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, **(env or {})), **kw)


def funcs(*names):
    return PRELUDE + "".join(shell_func(n) for n in names)


# -- the key line: one key, one script ----------------------------------------------

def test_the_key_line_restricts_the_key_to_the_switch_and_to_the_vms_address():
    r = bash(funcs("rac_pair_key_ok", "rac_pair_authorized_line")
             + f"rac_pair_authorized_line 10.99.0.2 '{VMKEY}'\n")
    assert r.stdout == ('restrict,from="10.99.0.2",command="sudo -n /usr/local/sbin/sigmond-rac-switch" '
                        f"{VMKEY} sigmond-rac-switch\n")


BAD_KEYS = [
    "", "ssh-ed25519", "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC7" + "A" * 60,
    VMKEY + ' x" command="/bin/sh', VMKEY + "\n" + VMKEY, "no-pty " + VMKEY,
    "ssh-ed25519 AAAA\u00c3\u00a2\u00c2\u0080\u00c2\u0094" + "A" * 60,     # mojibake from the agent
    "ssh-ed25519 " + "A" * 300,
]


@pytest.mark.parametrize("key", BAD_KEYS, ids=range(len(BAD_KEYS)))
def test_anything_but_one_ed25519_public_key_writes_no_key_line(key):
    r = bash(funcs("rac_pair_key_ok", "rac_pair_authorized_line")
             + 'rac_pair_authorized_line 10.99.0.2 "$KEY"; echo "rc=$?"\n', env={"KEY": key})
    assert r.stdout == "rc=1\n"


# -- the sudoers lines: one account, one script, no argument ---------------------------

def test_the_sudoers_lines_allow_the_switch_with_no_argument_and_keep_one_variable():
    out = bash(funcs("rac_pair_sudoers") + "rac_pair_sudoers\n").stdout
    rules = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
    assert rules == [
        "Defaults:sigmond-remote env_reset",
        'Defaults:sigmond-remote env_keep = "SSH_ORIGINAL_COMMAND"',
        "Defaults:sigmond-remote !requiretty",
        'sigmond-remote ALL=(root) NOPASSWD: /usr/local/sbin/sigmond-rac-switch ""',
    ]


@pytest.mark.skipif(VISUDO is None, reason="needs visudo")
def test_visudo_accepts_the_sudoers_lines(tmp_path):
    f = tmp_path / "sigmond-remote"
    f.write_text(bash(funcs("rac_pair_sudoers") + "rac_pair_sudoers\n").stdout)
    f.chmod(0o440)
    r = subprocess.run([VISUDO, "-c", "-q", "-f", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# -- what the host sends into the guest ----------------------------------------------------

def guest_script(host="10.99.0.1", key=HOSTKEY):
    return bash(funcs("rac_pair_guest_script") + f"rac_pair_guest_script '{host}' '{key}'\n").stdout


def sandbox(tmp_path, script):
    """Run a guest script against a scratch /etc/sigmond; chown needs root, so stub it."""
    d, m, b = tmp_path / "etc-sigmond", tmp_path / "motd.d", tmp_path / "bin"
    for p in (m, b):
        p.mkdir(exist_ok=True)
    (b / "chown").write_text("#!/bin/sh\nexit 0\n")
    (b / "chown").chmod(0o755)
    script = script.replace("D=/etc/sigmond\n", f"D={d}\n").replace("M=/etc/update-motd.d\n", f"M={m}\n")
    script = script.replace("C=/var/lib/sigmond/remote-access.json\n", f"C={tmp_path}/cache/remote-access.json\n")
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, PATH=f"{b}:{os.environ['PATH']}"))
    return r, d, m


def test_every_byte_sent_into_the_guest_is_ascii():
    # Proxmox sends a guest-agent request that holds a non-ASCII character
    # short, and about one time in six the agent then answers nothing more.
    for script in (guest_script(),
                   bash(funcs("rac_pair_verify_script") + "rac_pair_verify_script 10.99.0.1\n").stdout):
        assert script and all(ord(c) < 128 for c in script)
    body = "".join(shell_func(n) for n in PAIR_FUNCS)
    assert all(ord(c) < 128 for c in body), "a pairing function holds a non-ASCII byte"
    assert body.count("| base64 -w0) | base64 -d | bash") == 2      # both scripts travel encoded


def test_the_guest_script_makes_a_root_only_key_and_pins_the_host(tmp_path):
    r, d, m = sandbox(tmp_path, guest_script())
    assert r.returncode == 0, r.stdout + r.stderr
    assert stat.S_IMODE((d / "rac-switch.key").stat().st_mode) == 0o600
    assert (d / "rac-switch.key.pub").read_text().startswith("ssh-ed25519 ")
    assert (d / "rac-switch.known_hosts").read_text() == f"10.99.0.1 {HOSTKEY}\n"
    hook = (m / "72-sigmond-remote").read_text()
    assert "smd remote motd" in hook and os.access(m / "72-sigmond-remote", os.X_OK)


def test_a_second_run_keeps_the_key_and_takes_up_a_new_host_key(tmp_path):
    sandbox(tmp_path, guest_script())
    first = (tmp_path / "etc-sigmond" / "rac-switch.key.pub").read_text()
    restored = HOSTKEY.replace("Host", "Rest")              # an identity restore changed it
    r, d, _ = sandbox(tmp_path, guest_script(key=restored))
    assert r.returncode == 0
    assert (d / "rac-switch.key.pub").read_text() == first
    assert (d / "rac-switch.known_hosts").read_text() == f"10.99.0.1 {restored}\n"


def test_half_a_key_pair_gets_replaced_by_a_whole_one(tmp_path):
    sandbox(tmp_path, guest_script())
    d = tmp_path / "etc-sigmond"
    (d / "rac-switch.key.pub").unlink()
    r, d, _ = sandbox(tmp_path, guest_script())
    assert r.returncode == 0
    pub = subprocess.run(["ssh-keygen", "-y", "-f", str(d / "rac-switch.key")],
                         capture_output=True, text=True).stdout.split()[:2]
    assert (d / "rac-switch.key.pub").read_text().split()[:2] == pub


VERIFY_CASES = [
    (0, "Remote access: on\n  ...\n", 0, "on"),
    (0, "Remote access: off\n  ...\n", 0, "off"),
    (3, "Remote access: wanted on, not up\n  ...\n", 0, "down"),
    (255, "Host key verification failed.\n", 1, None),
    (255, "sigmond-remote@10.99.0.1: Permission denied (publickey).\n", 1, None),
    (2, "sigmond-rac-switch: refused.\n", 1, None),
    (1, "Remote access: on\n", 1, None),              # the right words, the wrong exit
    (0, "something else entirely\n", 1, None),
]


@pytest.mark.parametrize("ssh_rc,ssh_out,want_rc,want_state", VERIFY_CASES,
                         ids=[c[3] or f"rc{c[0]}-{i}" for i, c in enumerate(VERIFY_CASES)])
def test_the_verify_script_passes_only_on_an_answer_from_the_switch(tmp_path, ssh_rc, ssh_out,
                                                                    want_rc, want_state):
    script = bash(funcs("rac_pair_verify_script") + "rac_pair_verify_script 10.99.0.1\n").stdout
    (tmp_path / "bin").mkdir()
    ssh = tmp_path / "bin" / "ssh"
    (tmp_path / "ssh.out").write_text(ssh_out)
    ssh.write_text(f"#!/bin/bash\necho \"$*\" > {tmp_path}/ssh.args\ncat {tmp_path}/ssh.out\nexit {ssh_rc}\n")
    ssh.chmod(0o755)
    r, _, _ = sandbox(tmp_path, script)
    assert r.returncode == want_rc, r.stdout + r.stderr
    cache = tmp_path / "cache" / "remote-access.json"
    if want_state is None:
        assert not cache.exists()
    else:
        doc = json.loads(cache.read_text())
        assert doc["state"] == want_state
        assert re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ", doc["checked_at"])
    args = (tmp_path / "ssh.args").read_text()
    for must in ("BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=yes",
                 "GlobalKnownHostsFile=/dev/null", "sigmond-remote@10.99.0.1 status"):
        assert must in args, must


# -- the whole pairing, with the host's commands stubbed ---------------------------------------

def pair(tmp_path, *, slash30="10.99.0.1/30", agent=0, sudo="yes", apt_installs=False,
         vmkey=VMKEY, gexec_rcs="0 0", install_rc=0, hostpub=HOSTKEY + " root@pm\n", user_exists=False):
    (tmp_path / "home").mkdir(exist_ok=True)
    for stale in ("calls", "gexec.args"):
        (tmp_path / stale).unlink(missing_ok=True)
    if hostpub is not None:
        (tmp_path / "host.pub").write_text(hostpub)
    script = funcs(*PAIR_FUNCS) + textwrap.dedent(f"""
        VMID=100
        LOG={tmp_path}/wizard.log
        RAC_PAIR_HOME={tmp_path}/home
        RAC_PAIR_SUDOERS={tmp_path}/sudoers.d-sigmond-remote
        RAC_PAIR_HOSTPUB={tmp_path}/host.pub
        C={tmp_path}/calls
        say(){{ echo "[wizard] $*"; }}
        sleep(){{ :; }}
        timeout(){{ shift; "$@"; }}
        mgmt_vm_ip(){{ echo 10.99.0.2; }}
        ip(){{ [ -n "{slash30}" ] && echo "7: vmbr1    inet {slash30} brd 10.99.0.3 scope global vmbr1"; }}
        qm(){{ echo "qm $*" >> $C; return {agent}; }}
        rac_have_sudo(){{ [ -e {tmp_path}/sudo-present ]; }}
        apt-get(){{ echo "apt-get $*" >> $C; case "$*" in *install*) [ {int(apt_installs)} = 1 ] && touch {tmp_path}/sudo-present;; esac; }}
        rac_install_host_files(){{ echo install-host-files >> $C; return {install_rc}; }}
        id(){{ [ {int(user_exists)} = 1 ]; }}
        useradd(){{ echo "useradd $*" >> $C; }}
        usermod(){{ echo "usermod $*" >> $C; }}
        chown(){{ echo "chown $*" >> $C; }}
        visudo(){{ echo "visudo $*" >> $C; {VISUDO or 'true'} "$@"; }}
        systemctl(){{ echo "systemctl $*" >> $C; }}
        N=0
        gexec(){{
            N=$((N+1)); shift
            printf '%s\\n' "$*" >> {tmp_path}/gexec.args
            echo "gexec $N" >> $C
            set -- {gexec_rcs}
            eval "return \\${{$N}}"
        }}
        guest_out(){{ echo "guest_out $2" >> $C; printf '%s\\n' "$VMKEY_LINE"; }}
    """)
    if sudo == "yes":
        (tmp_path / "sudo-present").touch()
    r = bash(script + 'rac_pair_vm; echo "rc=$? fail=$RAC_PAIR_FAIL"\n',
             env={"VMKEY_LINE": vmkey})
    calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
    return r, calls


def test_pairing_writes_both_halves_and_then_proves_them(tmp_path):
    r, calls = pair(tmp_path)
    assert "rc=0 fail=" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "home" / ".ssh" / "authorized_keys").read_text() == (
        'restrict,from="10.99.0.2",command="sudo -n /usr/local/sbin/sigmond-rac-switch" '
        f"{VMKEY} sigmond-rac-switch\n")
    assert 'NOPASSWD: /usr/local/sbin/sigmond-rac-switch ""' in (tmp_path / "sudoers.d-sigmond-remote").read_text()
    assert not (tmp_path / "sudoers.d-sigmond-remote.new").exists()
    order = [c.split()[0] + (" " + c.split()[1] if c.startswith(("gexec", "systemctl")) else "")
             for c in calls]
    assert order == ["qm", "install-host-files", "useradd", "usermod", "chown", "visudo",
                     "gexec 1", "guest_out", "chown", "gexec 2", "systemctl enable"]
    assert calls[-1] == "systemctl enable --now sigmond-rac-pair.timer sigmond-rac-pair.path"
    assert "useradd --system --home-dir" in calls[2] and "--shell /bin/sh sigmond-remote" in calls[2]
    assert calls[3] == "usermod -p * -s /bin/sh sigmond-remote"


def test_what_travels_through_the_guest_agent_is_ascii_and_carries_the_hosts_key(tmp_path):
    pair(tmp_path, hostpub=HOSTKEY + " root@caf\u00e9-PM \u2014 the host\n")
    args = (tmp_path / "gexec.args").read_text().splitlines()
    assert len(args) == 2
    for a in args:
        assert all(ord(c) < 128 for c in a)
        assert re.fullmatch(r"echo [A-Za-z0-9+/=]+ \| base64 -d \| bash", a), a[:60]
    sent = base64.b64decode(args[0].split()[1]).decode()
    assert f"printf '%s %s\\n' '10.99.0.1' '{HOSTKEY}'" in sent


def test_pairing_again_changes_nothing_but_the_keys_it_reads(tmp_path):
    pair(tmp_path)
    newkey = VMKEY.replace("Vm", "Nw")
    r, calls = pair(tmp_path, vmkey=newkey, user_exists=True)
    assert "rc=0" in r.stdout
    assert not any(c.startswith("useradd") for c in calls)
    ak = (tmp_path / "home" / ".ssh" / "authorized_keys").read_text()
    assert ak.count("\n") == 1 and newkey in ak and VMKEY not in ak      # one key, never two


@pytest.mark.parametrize("kw,reason,touched", [
    (dict(slash30=""), "no private link to its decoder VM", []),
    (dict(hostpub=None), "cannot read this host's sshd key", []),
    (dict(hostpub="ssh-ed25519 AAAA\u00e9\u00e9 x\n"), "cannot read this host's sshd key", []),
    (dict(agent=1), "the guest agent in VM 100 does not answer", ["qm"]),
    (dict(sudo="no"), "this host has no sudo and could not install it", ["qm", "apt-get", "apt-get"]),
    (dict(install_rc=1), "could not install /usr/local/sbin/sigmond-rac-switch", ["qm", "install-host-files"]),
], ids=["no-slash-30", "no-host-key", "host-key-not-ascii", "agent-silent", "no-sudo", "no-switch"])
def test_pairing_refuses_early_and_says_why(tmp_path, kw, reason, touched):
    r, calls = pair(tmp_path, **kw)
    assert "rc=1" in r.stdout and reason in r.stdout, r.stdout
    assert sorted(set(c.split()[0] for c in calls)) == sorted(set(touched))
    assert not (tmp_path / "home" / ".ssh" / "authorized_keys").exists()
    assert not (tmp_path / "gexec.args").exists()


def test_a_host_without_sudo_installs_it_once_and_pairs(tmp_path):
    r, calls = pair(tmp_path, sudo="no", apt_installs=True)
    assert "rc=0" in r.stdout, r.stdout
    assert [c for c in calls if c.startswith("apt-get")] == ["apt-get -qq update", "apt-get -y -qq install sudo"]
    assert "this host has no sudo; installing it" in r.stdout


def test_only_the_keys_two_fields_reach_the_key_line(tmp_path):
    # Whatever follows the key in the VM's answer never reaches the file.
    r, _ = pair(tmp_path, vmkey=VMKEY + ' x",command="/bin/sh')
    assert "rc=0" in r.stdout
    ak = (tmp_path / "home" / ".ssh" / "authorized_keys").read_text()
    assert ak.endswith(f"{VMKEY} sigmond-rac-switch\n") and "/bin/sh" not in ak


@pytest.mark.parametrize("vmkey", ["", "ssh-rsa AAAA" + "B" * 80, "ssh-ed25519 short"],
                         ids=["nothing", "rsa", "too-short"])
def test_a_bad_answer_from_the_vm_leaves_the_earlier_key_line_in_place(tmp_path, vmkey):
    pair(tmp_path)
    before = (tmp_path / "home" / ".ssh" / "authorized_keys").read_text()
    r, calls = pair(tmp_path, vmkey=vmkey, user_exists=True)
    assert "rc=1 fail=VM 100 returned no usable public key" in r.stdout
    assert (tmp_path / "home" / ".ssh" / "authorized_keys").read_text() == before
    assert "gexec 2" not in calls and not any(c.startswith("systemctl") for c in calls)


def test_a_pairing_that_does_not_answer_is_reported_as_not_paired(tmp_path):
    r, calls = pair(tmp_path, gexec_rcs="0 1")
    assert "rc=1" in r.stdout
    assert "a request from the VM did not reach the switch" in r.stdout
    assert not any(c.startswith("systemctl") for c in calls)


def test_a_vm_that_will_not_take_its_half_stops_the_pairing(tmp_path):
    r, calls = pair(tmp_path, gexec_rcs="1 0")
    assert "rc=1 fail=could not write the pairing into VM 100" in r.stdout
    assert "guest_out" not in " ".join(calls)


# -- where it sits, and what re-asserts it ------------------------------------------------------

def test_pair_vm_runs_before_the_wizard_waits_or_asks():
    i = TEXT.index("    --pair-vm)")
    branch = TEXT[i:TEXT.index("    --rac-off|--rac-on)", i)]
    assert "rac_pair_vm" in branch and "exit 0" in branch and "exit 1;;" in branch
    assert i < TEXT.index("waiting for decoder VM") and i < TEXT.index("ask_reporter() {")
    for name in PAIR_FUNCS + ("gexec", "guest_out", "mgmt_vm_ip", "rac_install_host_files"):
        j = TEXT.find(f"\n{name}(){{")
        j = j if j >= 0 else TEXT.index(f"\n{name}() {{")
        assert j < i, f"{name} must be defined above the dispatch"


def test_the_wizard_run_pairs_after_it_has_brought_remote_access_up():
    i = TEXT.index("\nrac_activate\n")
    j = TEXT.index("if ! rac_pair_vm; then", i)
    assert j < TEXT.index('> "$CONF_MARK"')
    assert " Switch:    $PAIR_STATE\n" in TEXT
    assert "sigmond-setup --pair-vm" in TEXT[j:j + 400]


def test_the_pairing_is_re_asserted_at_boot_and_when_the_hosts_sshd_key_changes():
    service = heredoc("/etc/systemd/system/sigmond-rac-pair.service")
    timer = heredoc("/etc/systemd/system/sigmond-rac-pair.timer")
    path = heredoc("/etc/systemd/system/sigmond-rac-pair.path")
    assert "ExecStart=/usr/local/sbin/sigmond-setup --pair-vm\n" in service
    assert "OnBootSec=4min\n" in timer
    assert "PathChanged=/etc/ssh/ssh_host_ed25519_key.pub\n" in path
    assert "Unit=sigmond-rac-pair.service\n" in path


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_the_pairing_units_parse(tmp_path):
    for name in ("sigmond-rac-pair.service", "sigmond-rac-pair.timer", "sigmond-rac-pair.path"):
        (tmp_path / name).write_text(heredoc(f"/etc/systemd/system/{name}"))
    for name in ("sigmond-rac-pair.timer", "sigmond-rac-pair.path"):
        r = subprocess.run(["systemd-analyze", "verify", str(tmp_path / name)],
                           capture_output=True, text=True, timeout=60)
        noise = [ln for ln in (r.stdout + r.stderr).splitlines()
                 if "sigmond-rac-pair" in ln and "sigmond-setup" not in ln]
        assert noise == [], noise


def test_the_pairing_key_stays_out_of_the_station_identity():
    # A reflash mints a new pairing key and the wizard pairs again.  The key
    # records whom the VM may ask, not who the station is.
    identity = (REPO / "lib" / "sigmond" / "identity.py").read_text()
    assert "rac-switch" not in identity and "sigmond-remote" not in identity
```

Two kinds of test. The scripts the host sends into the guest run here for real, against a scratch directory, with
`chown` stubbed (it needs root). The pairing as a whole runs with the host's commands replaced by shell functions
that write down what they were asked. Neither reaches a real sshd or a real sudo; Task 10's rig does.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_wizard_pair_vm.py -q`
Expected: `43 failed, 1 passed`. `test_the_pairing_key_stays_out_of_the_station_identity` passes already and pins
`identity.py` against a later change.

- [ ] **Step 3: Implement in `scripts/proxmox/sigmond-wizard.sh`**

The steps below move three functions upward without changing a byte of them: `mgmt_vm_ip` with the comment above
it, `gexec`, and `guest_out` with its two comment lines. Each appears once as a deletion at its old place and once
inside the insertion.

(a) Find (lines 35-36):

```bash
#                              the tunnel up if its operator still wants it up
#              --rac-upgrade   climb to a more secure gateway tier if one
```

and replace it with:

```bash
#                              the tunnel up if its operator still wants it up
#              --pair-vm       pair the decoder VM with this host, so that
#                              `smd remote on|off|status` works there; also
#                              re-asserts a pairing that stands
#              --rac-upgrade   climb to a more secure gateway tier if one
```

(b) Find (lines 587-589):

```bash
}

# ── the one switch, and the host files that go with it ─────────────────────
```

and replace it with:

```bash
}

# ── reaching the decoder VM ─────────────────────────────────────────────────
# These three stood further down, among the wizard's questions.  They sit
# here so that `sigmond-setup --pair-vm` can use them: that subcommand runs
# before the wizard waits for anything or asks anything.
# The VM's address on the host-only /30 (PM .1, VM .2).  Needed because the
# repair below must NOT go through the guest agent — that is the thing that is
# broken.  ssh over the private link is the independent path.
mgmt_vm_ip() {
    [ -n "${SIGMOND_MGMT_VM:-}" ] && { printf '%s\n' "${SIGMOND_MGMT_VM}"; return; }
    local a
    a=$(ip -4 -o addr show 2>/dev/null \
        | awk '$4 ~ /\/30$/ {split($4,p,"/"); print p[1]; exit}')
    if [ -n "$a" ]; then
        awk -v ip="$a" 'BEGIN{n=split(ip,o,"."); printf "%s.%s.%s.%d\n",o[1],o[2],o[3],o[4]+1}'
        return
    fi
    printf '10.99.0.2\n'
}

gexec(){ # gexec <timeout-s> <command...>  → runs in guest, echoes exitcode
    # A guest agent that dies mid-wizard used to poison every LATER step:
    # each one returned "QEMU guest agent is not running", each printed its
    # own WARN, and the wizard walked on to declare success.  That is how
    # v3.39/v3.40 shipped a VM with no password and no ssh policy — the two
    # steps that happen to sit right after the one that killed the agent
    # (2026-09-16).  A dead agent is now a transient to wait out, not a
    # silent skip: wait for it to answer again and retry the step ONCE.
    # A command that merely exits non-zero is NOT retried — that is a real
    # answer from a live agent.
    local t="$1"; shift
    local out rc try i
    for try in 1 2; do
        out=$(qm guest exec "$VMID" --timeout "$t" -- bash -lc "$*" 2>&1 </dev/null)
        rc=$(echo "$out" | grep -o '"exitcode" *: *[0-9-]*' | grep -o '[0-9-]*$' | head -1)
        echo "$out" >> "$LOG"
        [ "${rc:-1}" = "0" ] && return 0
        case "$out" in
            *"guest agent is not running"*|*"got timeout"*|*"QGA"*)
                [ "$try" = 2 ] && return 1
                say "guest agent stopped answering — waiting for it before retrying"
                for i in $(seq 1 24); do
                    qm agent "$VMID" ping >/dev/null 2>&1 </dev/null && break
                    sleep 5
                done
                ;;
            *) return 1 ;;
        esac
    done
    return 1
}

# Run a command in the guest and hand back its STDOUT.  gexec returns only an
# exit code; preflight_devices already needed the output and open-coded this.
guest_out() {
    local t="$1"; shift
    qm guest exec "$VMID" --timeout "$t" -- bash -lc "$*" 2>/dev/null </dev/null \
      | python3 -c 'import json,sys
try:  print(json.load(sys.stdin).get("out-data",""))
except Exception: pass' 2>/dev/null || true
}

# ── the one switch, and the host files that go with it ─────────────────────
```

(c) Find (lines 897-898):

```bash
RACREGTMREOF
    systemctl daemon-reload
```

and replace it with:

```bash
RACREGTMREOF
    # The pairing's re-assertion: after every boot, four times a day, and
    # whenever this host's sshd key changes (an identity restore does that).
    cat > /etc/systemd/system/sigmond-rac-pair.service <<'RACPAIRSVCEOF'
[Unit]
Description=Sigmond remote access: re-assert the pairing between this host and its decoder VM
After=pve-guests.service
[Service]
Type=oneshot
TimeoutStartSec=600
ExecStart=/usr/local/sbin/sigmond-setup --pair-vm
RACPAIRSVCEOF
    cat > /etc/systemd/system/sigmond-rac-pair.timer <<'RACPAIRTMREOF'
[Unit]
Description=Sigmond remote access: re-assert the VM pairing after boot and four times a day
[Timer]
OnBootSec=4min
OnUnitInactiveSec=6h
[Install]
WantedBy=timers.target
RACPAIRTMREOF
    cat > /etc/systemd/system/sigmond-rac-pair.path <<'RACPAIRPATHEOF'
[Unit]
Description=Sigmond remote access: re-assert the VM pairing when this host's sshd key changes
[Path]
PathChanged=/etc/ssh/ssh_host_ed25519_key.pub
Unit=sigmond-rac-pair.service
[Install]
WantedBy=paths.target
RACPAIRPATHEOF
    systemctl daemon-reload
```

(d) Find (lines 928-930):

```bash
}

# ── RAC on/off switches (no wizard rerun needed) ────────────────────────────
```

and replace it with:

```bash
}

# ── the pairing: the decoder VM asks, this host alone acts ─────────────────
# tasks/plan-remote-access.md section 2.  The operator knows the VM, and this
# host has no keyboard once the install ends.  So the VM holds one ssh key,
# and that key buys one thing: sigmond-rac-switch, with one of its three
# words.  No shell, no forwarding, no other command.
#
# The VM can never create this pairing: the host's half lives in files that
# only root on this host writes.  And the host re-asserts both halves, at
# every boot and whenever its own sshd key changes.  On AC0G-B4 an identity
# restore replaced a host key eight minutes after the wizard had authorized
# the old one, and the fleet's login stayed broken for a week.
RAC_PAIR_USER=sigmond-remote
RAC_PAIR_HOME=/var/lib/sigmond-remote
RAC_PAIR_SUDOERS=/etc/sudoers.d/sigmond-remote
RAC_PAIR_HOSTPUB=/etc/ssh/ssh_host_ed25519_key.pub
RAC_PAIR_FAIL=""

rac_pair_key_ok(){
    # One ed25519 public key, as "type base64", and not a byte more.  What
    # comes back through the guest agent is data from another machine.  The
    # match covers the whole string, so a second line cannot ride along: in
    # an authorized_keys file a second line would stand as a key of its own,
    # with no restriction on it.
    local LC_ALL=C
    [[ "$1" =~ ^ssh-ed25519\ [A-Za-z0-9+/]{60,100}={0,2}$ ]]
}

rac_have_sudo(){ command -v sudo >/dev/null 2>&1 && command -v visudo >/dev/null 2>&1; }

rac_pair_sudoers(){
    cat <<SUDOEOF
# Written by sigmond-setup --pair-vm (sigmond tasks/plan-remote-access.md).
# The decoder VM holds one ssh key.  That key reaches this account, and this
# account may run one script as root, with no argument.  The script reads the
# requested word (on, off or status) from SSH_ORIGINAL_COMMAND.
Defaults:$RAC_PAIR_USER env_reset
Defaults:$RAC_PAIR_USER env_keep = "SSH_ORIGINAL_COMMAND"
Defaults:$RAC_PAIR_USER !requiretty
$RAC_PAIR_USER ALL=(root) NOPASSWD: $RAC_SWITCH ""
SUDOEOF
}

rac_pair_authorized_line(){
    # rac_pair_authorized_line <VM address> <VM public key>
    rac_pair_key_ok "$2" || return 1
    printf 'restrict,from="%s",command="sudo -n %s" %s sigmond-rac-switch\n' "$1" "$RAC_SWITCH" "$2"
}

rac_pair_guest_script(){
    # rac_pair_guest_script <host address> <host sshd public key>
    # The script the guest runs.  It travels base64-encoded, and it holds
    # ASCII only: Proxmox sends a guest-agent request with a non-ASCII
    # character short, and the agent can then go deaf.
    cat <<GUESTEOF
set -e
umask 077
D=/etc/sigmond
M=/etc/update-motd.d
K=\$D/rac-switch.key
install -d -m 755 "\$D"
if [ ! -s "\$K" ] || [ ! -s "\$K.pub" ]; then
    rm -f "\$K" "\$K.pub"
    ssh-keygen -q -t ed25519 -N '' -C sigmond-rac-switch -f "\$K"
fi
chown root:root "\$K" "\$K.pub"
chmod 600 "\$K"
chmod 644 "\$K.pub"
printf '%s %s\n' '$1' '$2' > "\$D/rac-switch.known_hosts.new"
chown root:root "\$D/rac-switch.known_hosts.new"
chmod 644 "\$D/rac-switch.known_hosts.new"
mv -f "\$D/rac-switch.known_hosts.new" "\$D/rac-switch.known_hosts"
if [ -d "\$M" ]; then
    printf '%s\n' '#!/bin/sh' \\
        '# One line on the state of remote access.  Written from the Proxmox host' \\
        '# by sigmond-setup --pair-vm.  Prints nothing where smd lacks the verb.' \\
        'command -v smd >/dev/null 2>&1 && smd remote motd 2>/dev/null' \\
        'exit 0' > "\$M/72-sigmond-remote"
    chmod 755 "\$M/72-sigmond-remote"
fi
GUESTEOF
}

rac_pair_verify_script(){
    # rac_pair_verify_script <host address>
    # One request from the VM, through sshd and sudo, to the switch.  It also
    # leaves the answer where the VM's login banner reads it.
    cat <<VERIFYEOF
D=/etc/sigmond
C=/var/lib/sigmond/remote-access.json
out=\$(ssh -T -i "\$D/rac-switch.key" -o BatchMode=yes -o IdentitiesOnly=yes \\
          -o StrictHostKeyChecking=yes -o UserKnownHostsFile="\$D/rac-switch.known_hosts" \\
          -o GlobalKnownHostsFile=/dev/null -o ConnectTimeout=8 -o LogLevel=ERROR \\
          $RAC_PAIR_USER@$1 status 2>&1)
rc=\$?
case "\$rc" in 0|3) ;; *) printf '%s\n' "\$out" | tail -3; exit 1 ;; esac
case "\$(printf '%s\n' "\$out" | head -1)" in
    "Remote access: on") s=on ;;
    "Remote access: off") s=off ;;
    "Remote access: wanted on, not up") s=down ;;
    *) printf '%s\n' "\$out" | head -3; exit 1 ;;
esac
mkdir -p "\$(dirname "\$C")"
printf '{"state": "%s", "checked_at": "%s"}\n' "\$s" "\$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "\$C"
chmod 644 "\$C"
exit 0
VERIFYEOF
}

rac_pair_vm(){
    # Pair the decoder VM with this host, or re-assert a pairing that stands.
    # Safe at any time: it writes both halves again from what holds now.
    #   this host's half   the account, its sudoers lines, the VM's public key
    #   the VM's half      its key pair (made in the VM; the private half
    #                      never leaves it), and this host's sshd key as the
    #                      only host that key talks to
    # Returns 0 once a request from the VM has reached the switch script.
    # Otherwise it returns 1 with the reason in RAC_PAIR_FAIL.
    local pm vm hostkey vmkey line tmp i
    RAC_PAIR_FAIL=""
    vm=$(mgmt_vm_ip)
    pm=$(ip -4 -o addr show 2>/dev/null | awk '$4 ~ /\/30$/ {split($4,p,"/"); print p[1]; exit}')
    if [ -z "$pm" ]; then
        RAC_PAIR_FAIL="this host has no private link to its decoder VM (no /30 address)"
        return 1
    fi
    hostkey=$(awk '{print $1" "$2; exit}' "$RAC_PAIR_HOSTPUB" 2>/dev/null)
    if ! rac_pair_key_ok "$hostkey"; then
        RAC_PAIR_FAIL="cannot read this host's sshd key ($RAC_PAIR_HOSTPUB)"
        return 1
    fi
    for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
        qm agent "$VMID" ping >/dev/null 2>&1 </dev/null && break
        if [ "$i" = 12 ]; then
            RAC_PAIR_FAIL="the guest agent in VM $VMID does not answer"
            return 1
        fi
        sleep 5
    done
    # Stock Proxmox carries no sudo.  A v3.71 install brings it on the stick;
    # a station installed earlier fetches it here, once.
    if ! rac_have_sudo; then
        say "this host has no sudo; installing it (the pairing account needs its one sudoers line)"
        apt-get -qq update >>"$LOG" 2>&1
        DEBIAN_FRONTEND=noninteractive timeout 300 apt-get -y -qq install sudo >>"$LOG" 2>&1
        if ! rac_have_sudo; then
            RAC_PAIR_FAIL="this host has no sudo and could not install it; run: apt-get install sudo, then: sigmond-setup --pair-vm"
            return 1
        fi
    fi
    if ! rac_install_host_files; then
        RAC_PAIR_FAIL="could not install $RAC_SWITCH"
        return 1
    fi

    # This host's half, part one: an account that can do one thing.
    if ! id "$RAC_PAIR_USER" >/dev/null 2>&1 \
       && ! useradd --system --home-dir "$RAC_PAIR_HOME" --shell /bin/sh "$RAC_PAIR_USER"; then
        RAC_PAIR_FAIL="could not create the account $RAC_PAIR_USER"
        return 1
    fi
    # No password that could ever match, and a shell sshd can run the forced
    # command with.
    usermod -p '*' -s /bin/sh "$RAC_PAIR_USER" >/dev/null 2>&1
    install -d -m 755 "$RAC_PAIR_HOME" "$RAC_PAIR_HOME/.ssh"
    chown root:root "$RAC_PAIR_HOME" "$RAC_PAIR_HOME/.ssh"
    # sudo skips a file whose name holds a dot, so the candidate cannot take
    # effect before visudo has passed it.
    tmp="$RAC_PAIR_SUDOERS.new"
    if ! { rac_pair_sudoers > "$tmp" && chmod 440 "$tmp" \
           && visudo -c -q -f "$tmp" >/dev/null 2>&1 && mv -f "$tmp" "$RAC_PAIR_SUDOERS"; }; then
        rm -f "$tmp"
        RAC_PAIR_FAIL="could not install the sudoers lines ($RAC_PAIR_SUDOERS)"
        return 1
    fi

    # The VM's half.
    if ! gexec 60 "echo $(rac_pair_guest_script "$pm" "$hostkey" | base64 -w0) | base64 -d | bash"; then
        RAC_PAIR_FAIL="could not write the pairing into VM $VMID (see $LOG)"
        return 1
    fi
    vmkey=$(guest_out 20 "cat /etc/sigmond/rac-switch.key.pub" | awk 'NF>=2{print $1" "$2; exit}')
    if ! line=$(rac_pair_authorized_line "$vm" "$vmkey"); then
        RAC_PAIR_FAIL="VM $VMID returned no usable public key"
        return 1
    fi

    # This host's half, part two: that one key, in a file the account cannot
    # write.
    tmp="$RAC_PAIR_HOME/.ssh/authorized_keys.new"
    if ! { printf '%s\n' "$line" > "$tmp" && chown root:root "$tmp" && chmod 644 "$tmp" \
           && mv -f "$tmp" "$RAC_PAIR_HOME/.ssh/authorized_keys"; }; then
        rm -f "$tmp"
        RAC_PAIR_FAIL="could not write $RAC_PAIR_HOME/.ssh/authorized_keys"
        return 1
    fi

    # Prove it.
    if ! gexec 60 "echo $(rac_pair_verify_script "$pm" | base64 -w0) | base64 -d | bash"; then
        RAC_PAIR_FAIL="both halves stand on disk, but a request from the VM did not reach the switch (see $LOG)"
        return 1
    fi
    systemctl enable --now sigmond-rac-pair.timer sigmond-rac-pair.path >/dev/null 2>&1
    return 0
}

# ── RAC on/off switches (no wizard rerun needed) ────────────────────────────
```

(e) Find (lines 943-944):

```bash
        exit 0;;
    --rac-off|--rac-on)
```

and replace it with:

```bash
        exit 0;;
    --pair-vm)
        # Pair the decoder VM with this host, or re-assert the pairing.  A
        # station installed before v3.71 runs this once, by hand or through
        # pm-align.  sigmond-rac-pair runs it after every boot and whenever
        # this host's sshd key changes.
        if rac_pair_vm; then
            say "paired: the decoder VM can run smd remote on, smd remote off and smd remote status"
            exit 0
        fi
        say "NOT paired: $RAC_PAIR_FAIL"
        exit 1;;
    --rac-off|--rac-on)
```

(f) Find (lines 1016-1031):

```bash
fi
# The VM's address on the host-only /30 (PM .1, VM .2).  Needed because the
# repair below must NOT go through the guest agent — that is the thing that is
# broken.  ssh over the private link is the independent path.
mgmt_vm_ip() {
    [ -n "${SIGMOND_MGMT_VM:-}" ] && { printf '%s\n' "${SIGMOND_MGMT_VM}"; return; }
    local a
    a=$(ip -4 -o addr show 2>/dev/null \
        | awk '$4 ~ /\/30$/ {split($4,p,"/"); print p[1]; exit}')
    if [ -n "$a" ]; then
        awk -v ip="$a" 'BEGIN{n=split(ip,o,"."); printf "%s.%s.%s.%d\n",o[1],o[2],o[3],o[4]+1}'
        return
    fi
    printf '10.99.0.2\n'
}
_vmip=$(mgmt_vm_ip)
```

and replace it with:

```bash
fi
_vmip=$(mgmt_vm_ip)
```

(g) Find (lines 1078-1112):

```bash
rd(){ read "$@" || { echo; say "stdin closed (EOF) — aborting wizard; nothing applied. Rerun: sigmond-setup"; exit 1; }; }

gexec(){ # gexec <timeout-s> <command...>  → runs in guest, echoes exitcode
    # A guest agent that dies mid-wizard used to poison every LATER step:
    # each one returned "QEMU guest agent is not running", each printed its
    # own WARN, and the wizard walked on to declare success.  That is how
    # v3.39/v3.40 shipped a VM with no password and no ssh policy — the two
    # steps that happen to sit right after the one that killed the agent
    # (2026-09-16).  A dead agent is now a transient to wait out, not a
    # silent skip: wait for it to answer again and retry the step ONCE.
    # A command that merely exits non-zero is NOT retried — that is a real
    # answer from a live agent.
    local t="$1"; shift
    local out rc try i
    for try in 1 2; do
        out=$(qm guest exec "$VMID" --timeout "$t" -- bash -lc "$*" 2>&1 </dev/null)
        rc=$(echo "$out" | grep -o '"exitcode" *: *[0-9-]*' | grep -o '[0-9-]*$' | head -1)
        echo "$out" >> "$LOG"
        [ "${rc:-1}" = "0" ] && return 0
        case "$out" in
            *"guest agent is not running"*|*"got timeout"*|*"QGA"*)
                [ "$try" = 2 ] && return 1
                say "guest agent stopped answering — waiting for it before retrying"
                for i in $(seq 1 24); do
                    qm agent "$VMID" ping >/dev/null 2>&1 </dev/null && break
                    sleep 5
                done
                ;;
            *) return 1 ;;
        esac
    done
    return 1
}

# uploads_toml — the [uploads] block a FRESH install writes: the site sink
```

and replace it with:

```bash
rd(){ read "$@" || { echo; say "stdin closed (EOF) — aborting wizard; nothing applied. Rerun: sigmond-setup"; exit 1; }; }


# uploads_toml — the [uploads] block a FRESH install writes: the site sink
```

(h) Find (lines 1298-1309):

```bash
# sigmond-location-check ("location authority first (GPSDO definitive)").
# Run a command in the guest and hand back its STDOUT.  gexec returns only an
# exit code; preflight_devices already needed the output and open-coded this.
guest_out() {
    local t="$1"; shift
    qm guest exec "$VMID" --timeout "$t" -- bash -lc "$*" 2>/dev/null </dev/null \
      | python3 -c 'import json,sys
try:  print(json.load(sys.stdin).get("out-data",""))
except Exception: pass' 2>/dev/null || true
}

# ── is the GPSDO actually locked? ───────────────────────────────────────────
```

and replace it with:

```bash
# sigmond-location-check ("location authority first (GPSDO definitive)").

# ── is the GPSDO actually locked? ───────────────────────────────────────────
```

(i) Find (lines 2893-2895):

```bash
rac_activate

PSWS_STATE="skipped — add later: sigmond-setup --reconfigure"
```

and replace it with:

```bash
rac_activate

# The switch works from the decoder VM only once the two machines pair.
PAIR_STATE="paired: in the decoder VM, smd remote on / off / status"
if ! rac_pair_vm; then
    PAIR_STATE="NOT PAIRED ($RAC_PAIR_FAIL).
            Until it pairs, smd remote cannot reach this host. Later, on this host: sigmond-setup --pair-vm"
    say "WARN: remote-access switch: $PAIR_STATE"
fi

PSWS_STATE="skipped — add later: sigmond-setup --reconfigure"
```

(j) Find (lines 2962-2963):

```bash
 RAC:       $RAC_STATE
 PSWS:      $PSWS_STATE
```

and replace it with:

```bash
 RAC:       $RAC_STATE
 Switch:    $PAIR_STATE
 PSWS:      $PSWS_STATE
```

Check that the three moved functions kept their bytes:

```bash
cd /home/mjh/hamsci/repos/sigmond
bash -n scripts/proxmox/sigmond-wizard.sh && echo parses
for f in 'gexec(){' 'guest_out() {' 'mgmt_vm_ip() {'; do
    diff <(git show HEAD:scripts/proxmox/sigmond-wizard.sh | awk -v f="$f" 'index($0,f)==1{k=1} k{print} k&&$0=="}"{exit}') \
         <(awk -v f="$f" 'index($0,f)==1{k=1} k{print} k&&$0=="}"{exit}' scripts/proxmox/sigmond-wizard.sh) >/dev/null \
        && echo "same: $f" || echo "CHANGED: $f"
done
```

Expected: `parses`, then three lines that begin `same:`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_wizard_pair_vm.py -q`
Expected: `44 passed`.

- [ ] **Step 5: Run the wider suites**

Run: `.venv/bin/pytest tests/test_wizard_remote_default.py tests/test_rac_switch.py tests/test_wizard_rac_register.py tests/test_pm_align.py tests/test_wizard_vmid.py tests/test_wizard_gpsdo.py tests/test_wizard_uploads.py tests/test_wizard_wifi.py tests/test_wizard_antenna.py tests/test_wizard_adoption_prose.py -q`
Expected: no failures. `tests/test_wizard_vmid.py` cuts `mgmt_vm_ip` out of the wizard by name, wherever it stands.

Run: `.venv/bin/pytest -q`
Expected: `3648 passed, 1 warning, 65 subtests passed` (3604 after Task 3, and 44 new).

- [ ] **Step 6: Watch six guards fail, then restore**

```bash
cd /home/mjh/hamsci/repos/sigmond
F=scripts/proxmox/sigmond-wizard.sh
cp $F $F.keep
mut(){ cp $F.keep $F; python3 - "$F" "$1" "$2" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
assert s.count(sys.argv[2]) == 1, "the mutation's anchor must occur exactly once"
p.write_text(s.replace(sys.argv[2], sys.argv[3]))
PY
.venv/bin/pytest tests/test_wizard_pair_vm.py -q 2>&1 | grep -E '^FAILED|passed|failed' | cut -c1-160; }

# 1. the key check reads line by line, so a second line rides along
mut '    [[ "$1" =~ ^ssh-ed25519\ [A-Za-z0-9+/]{60,100}={0,2}$ ]]' \
    '    printf '"'"'%s'"'"' "$1" | grep -Eqx '"'"'ssh-ed25519 [A-Za-z0-9+/]{60,100}={0,2}'"'"''
# -> 1 failed, 43 passed: test_anything_but_one_ed25519_public_key_writes_no_key_line

# 2. a second pairing appends its key beside the first
mut '           && mv -f "$tmp" "$RAC_PAIR_HOME/.ssh/authorized_keys"; }; then' \
    '           && cat "$tmp" >> "$RAC_PAIR_HOME/.ssh/authorized_keys"; }; then'
# -> 1 failed, 43 passed: test_pairing_again_changes_nothing_but_the_keys_it_reads

# 3. the sudoers rule allows the switch any argument
mut '$RAC_PAIR_USER ALL=(root) NOPASSWD: $RAC_SWITCH ""' \
    '$RAC_PAIR_USER ALL=(root) NOPASSWD: $RAC_SWITCH'
# -> 2 failed, 42 passed: test_the_sudoers_lines_allow_the_switch_with_no_argument_and_keep_one_variable, test_pairing_writes_both_halves_and_then_proves_them

# 4. every re-assertion makes the VM a new key
mut 'if [ ! -s "\$K" ] || [ ! -s "\$K.pub" ]; then' \
    'if true; then'
# -> 1 failed, 43 passed: test_a_second_run_keeps_the_key_and_takes_up_a_new_host_key

# 5. the re-assertion units come up before the pairing has proved itself
mut '    # Prove it.
' \
    '    systemctl enable --now sigmond-rac-pair.timer sigmond-rac-pair.path >/dev/null 2>&1
'
# -> 2 failed, 42 passed: test_pairing_writes_both_halves_and_then_proves_them, test_a_pairing_that_does_not_answer_is_reported_as_not_paired

# 6. the proof accepts an answer with the wrong exit status
mut 'case "\$rc" in 0|3) ;; *) printf' \
    'case "\$rc" in *) ;; 99) printf'
# -> 1 failed, 43 passed: test_the_verify_script_passes_only_on_an_answer_from_the_switch

mv $F.keep $F
.venv/bin/pytest tests/test_wizard_pair_vm.py -q | tail -1      # 44 passed
```

- [ ] **Step 7: Commit**

```bash
cd /home/mjh/hamsci/repos/sigmond
test "$(grep -c . .git/ATTRIBUTION)" = 2 || { echo "write .git/ATTRIBUTION first (Global Constraints)"; exit 1; }
git add scripts/proxmox/sigmond-wizard.sh tests/test_wizard_pair_vm.py
git commit -m "wizard: pair the decoder VM with its host, so the VM can ask for the switch

sigmond-setup --pair-vm makes one ssh key inside the VM, through the
guest agent, and one account on the host, sigmond-remote.  The account's
key line carries restrict, from= and a forced command; its sudoers rule
allows the switch script with no argument and keeps one variable.  The
VM holds the host's sshd key as the only host its key talks to.  The VM
cannot create this pairing, and the key buys no shell.

The wizard pairs at every install.  Three units re-assert the pairing:
after boot, every 6 hours, and when the host's sshd key changes, which
an identity restore does.  Every byte sent into the guest travels
base64-encoded, and the host accepts back exactly one ed25519 key.

tasks/plan-remote-access.md section 2.

$(cat .git/ATTRIBUTION)"
```

### Task 5: `smd remote on|off|status` in the decoder VM

Spec section 2: "In the VM the operator types `smd remote off`, `smd remote on`, `smd remote status`. `smd remote`
elevates the way every changing `smd` verb does, opens ssh to the host on the private link (10.99.0.1) with that
key, and prints the host's answer." And on the name: "`smd remote`, not `smd rac`. `smd admin rac` already names
the VM-side tunnel, and two 'rac' commands that manage different tunnels would mislead."

The verb holds almost no logic. It asks, prints, and passes the host's exit status back. Four things it must get
right, because the day someone types it something else has often failed:

- **Every word elevates**, `status` too: the pairing key belongs to root alone.
- **The host's address comes from the pairing**, never from a default. The host wrote one line into
  `rac-switch.known_hosts`; its first field names the only host the key may talk to.
- **When it cannot ask, it says why, and names the next step.** A VM that holds no pairing (a station between its
  `smd update` and its host's alignment, Review Focus 5) hears the one host command, `sigmond-setup --pair-vm`.
  A changed host key, a refused key and a silent host each get their own words.
- **The login banner never asks the host.** `smd remote motd` prints one line from the last answer on file, with
  its age (ruling 12). A login must stay fast when the host does not answer.

On a station with no Proxmox host the verb points at `smd admin rac` and drives nothing (ruling 13).

**Files:**
- Create: `sigmond/lib/sigmond/commands/remote.py`
- Create: `sigmond/tests/test_remote_cli.py`
- Modify: `sigmond/bin/smd` — the usage header (line 36), the epilog (line 23416), the parser (line 24851), the
  dispatch (line 24992)
- Modify: `sigmond/docs/contributor/orchestration.md` — one row of the CLI table (line 151);
  `tests/test_docs_cli_table.py` fails without it

**Interfaces:**
- Consumes: Task 2's state lines and exit codes. Task 4's files in the VM (`/etc/sigmond/rac-switch.key`,
  `/etc/sigmond/rac-switch.known_hosts`), its account `sigmond-remote`, its cache
  `/var/lib/sigmond/remote-access.json` and its motd hook, which runs `smd remote motd`.
  `/etc/sigmond-appliance/version`, which the wizard stamps into every appliance VM. `bin/smd`'s `_need_root`.
- Produces, in `sigmond.commands.remote`:
  - `cmd_remote(args, *, run=subprocess.run) -> int` — `args.remote_command` is `on`, `off`, `status`, `motd` or
    None (status). Returns the host's exit status (0, 1, 3, 4), or 2 for another word, 5 (`EXIT_NOT_PAIRED`), 6
    (`EXIT_UNREACHABLE`).
  - `cmd_motd(now=None) -> int`, `paired_host(known_hosts=None) -> str | None`, `ssh_argv(host, word) -> list`,
    `state_of(answer) -> 'on' | 'off' | 'down' | None`.
  - `KEY`, `KNOWN_HOSTS`, `CACHE`, `APPLIANCE_MARK`, `HOST_ACCOUNT`, `WORDS`, `STATE_LINES`, `TIMEOUT_S`.
  - Messages that Task 10's rig and Task 11's page quote: `this VM holds no pairing with its Proxmox host`,
    `shows an sshd key this VM was not paired with`, `did not accept this VM's pairing key`,
    `could not reach the Proxmox host at <address>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_remote_cli.py`:

```python
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
```

A recorded `run` stands in for ssh, so no test opens a connection.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_remote_cli.py -q`
Expected: `1 error`, with `ImportError: cannot import name 'remote' from 'sigmond.commands'`.

- [ ] **Step 3: Create `lib/sigmond/commands/remote.py`**

```python
"""`smd remote on|off|status` -- the operator's switch for remote access.

On an appliance station the tunnel runs on the Proxmox host, and the host
alone turns it on or off (tasks/plan-remote-access.md).  This verb asks.  It
opens ssh to the host over the private link between the two machines, with
the one key the host's wizard made in this VM, and prints the host's answer.
On the host that key can run exactly one script, sigmond-rac-switch, with one
of three words.

`smd remote motd` prints one line for the login banner.  It reads the last
answer the host gave and never asks the host, so a login stays fast.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..ui import err, info

KEY = Path('/etc/sigmond/rac-switch.key')
KNOWN_HOSTS = Path('/etc/sigmond/rac-switch.known_hosts')
CACHE = Path('/var/lib/sigmond/remote-access.json')
APPLIANCE_MARK = Path('/etc/sigmond-appliance/version')
HOST_ACCOUNT = 'sigmond-remote'

WORDS = ('on', 'off', 'status')
# The switch script prints exactly one of these as a line of its answer.
STATE_LINES = {
    'Remote access: on': 'on',
    'Remote access: off': 'off',
    'Remote access: wanted on, not up': 'down',
}
# `on` may register with a gateway first and then waits for the channels.
TIMEOUT_S = {'on': 600, 'off': 120, 'status': 60}

EXIT_NOT_PAIRED = 5
EXIT_UNREACHABLE = 6


def paired_host(known_hosts: Path | None = None) -> str | None:
    """The one host the pairing key may talk to.

    The host wrote a single line into the known-hosts file: its address on
    the private link, then its sshd key.  Returns the address, or None when
    the file is missing or holds anything but an address in that place.
    """
    try:
        text = (known_hosts or KNOWN_HOSTS).read_text()
    except OSError:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and re.fullmatch(r'[0-9A-Fa-f:.]{3,45}', parts[0]):
            return parts[0]
    return None


def ssh_argv(host: str, word: str) -> list:
    return ['ssh', '-T', '-i', str(KEY),
            '-o', 'BatchMode=yes',
            '-o', 'IdentitiesOnly=yes',
            '-o', 'StrictHostKeyChecking=yes',
            '-o', f'UserKnownHostsFile={KNOWN_HOSTS}',
            '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'ConnectTimeout=8',
            '-o', 'LogLevel=ERROR',
            f'{HOST_ACCOUNT}@{host}', word]


def state_of(answer: str) -> str | None:
    """'on', 'off' or 'down' from the host's answer; None when it names none."""
    for line in answer.splitlines():
        if line in STATE_LINES:
            return STATE_LINES[line]
    return None


def _remember(state: str, now: datetime | None = None) -> None:
    """Keep the answer for the login banner.  A failure here costs a banner
    line and nothing else."""
    now = now or datetime.now(timezone.utc)
    doc = {'state': state, 'checked_at': now.strftime('%Y-%m-%dT%H:%M:%SZ')}
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(CACHE.parent), prefix='.remote-access.')
        with os.fdopen(fd, 'w') as fh:
            fh.write(json.dumps(doc) + '\n')
        os.chmod(tmp, 0o644)
        os.replace(tmp, CACHE)
    except OSError:
        pass


def _not_paired() -> int:
    if APPLIANCE_MARK.exists():
        err('this VM holds no pairing with its Proxmox host, so it cannot ask '
            'the host to switch remote access')
        info('A station installed before v3.71 pairs once.  On the Proxmox host '
             '(ssh root@<host address>):')
        info('    sigmond-setup --pair-vm')
        info('Until then the host itself works the switch:  sigmond-setup --rac-off  '
             'and  sigmond-setup --rac-on')
    else:
        err('smd remote switches the tunnel of an appliance station through its '
            'Proxmox host, and this machine has none')
        info('A station without a Proxmox host runs its tunnel here:  smd admin rac status')
    return EXIT_NOT_PAIRED


def _unreachable(host: str, stderr: str) -> int:
    last = (stderr or '').strip().splitlines()[-1:] or ['ssh gave no reason']
    low = (stderr or '').lower()
    if 'host key verification failed' in low or 'identification has changed' in low:
        err(f'the Proxmox host at {host} shows an sshd key this VM was not paired with')
        info('The host pairs again by itself within minutes of a key change; try again shortly.')
        info('If it stays so, on the Proxmox host:  sigmond-setup --pair-vm')
    elif 'permission denied' in low:
        err(f'the Proxmox host at {host} did not accept this VM\'s pairing key')
        info('On the Proxmox host:  sigmond-setup --pair-vm')
    else:
        err(f'could not reach the Proxmox host at {host}: {last[0]}')
        info('The private link between this VM and its host carries this request; '
             'the station\'s uplink plays no part in it.')
    return EXIT_UNREACHABLE


def cmd_motd(now: datetime | None = None) -> int:
    """One line for the login banner; nothing on a VM that holds no pairing."""
    if not KEY.exists():
        return 0
    try:
        doc = json.loads(CACHE.read_text())
        state, when = doc['state'], str(doc['checked_at'])
    except (OSError, ValueError, KeyError, TypeError):
        print('Remote access: state not read yet.  Check it:  smd remote status')
        return 0
    as_of = f' (as the host reported {when[:16].replace("T", " ")} UTC)'
    if state == 'on':
        print(f'Remote access: on{as_of}.  Turn it off:  smd remote off')
    elif state == 'off':
        print(f'Remote access: OFF{as_of}.  Turn it on:  smd remote on')
    else:
        print(f'Remote access: wanted on, NOT UP{as_of}.  Details:  smd remote status')
    return 0


def cmd_remote(args, *, run=subprocess.run) -> int:
    word = getattr(args, 'remote_command', None) or 'status'
    if word == 'motd':
        return cmd_motd()
    if word not in WORDS:
        err('smd remote takes one word: on, off or status')
        return 2
    host = paired_host() if KEY.exists() else None
    if host is None:
        return _not_paired()
    if word == 'on':
        info('asking the Proxmox host; this can take a minute while the tunnel comes up')
    try:
        r = run(ssh_argv(host, word), capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=TIMEOUT_S[word])
    except subprocess.TimeoutExpired:
        err(f'the Proxmox host at {host} gave no answer within {TIMEOUT_S[word]} s')
        info('Ask again for the state:  smd remote status')
        return EXIT_UNREACHABLE
    except OSError as exc:
        err(f'could not run ssh: {exc}')
        return EXIT_UNREACHABLE
    answer = r.stdout or ''
    if answer:
        print(answer, end='' if answer.endswith('\n') else '\n')
    state = state_of(answer)
    if state is None:
        if r.returncode == 255:
            return _unreachable(host, r.stderr)
        tail = (r.stderr or '').strip().splitlines()[-1:]
        err(f'the host\'s switch gave no state (exit {r.returncode})'
            + (f': {tail[0]}' if tail else ''))
        return r.returncode or 1
    _remember(state)
    return r.returncode
```

Run: `.venv/bin/pytest tests/test_remote_cli.py -q`
Expected: `3 failed, 21 passed`. The three belong to `RemoteWiringTests`: `bin/smd` does not know the verb yet.

- [ ] **Step 4: Register the verb in `bin/smd`, and give it its row in the CLI table**

(a) Find (lines 36-37):

```python
    smd sink        status | off --reason R | upload                 the site sink switch (sudo)
    smd upload      status | on | hold | off | discard [--reason R]  legacy words for the same switch (sudo)
```

and replace it with:

```python
    smd sink        status | off --reason R | upload                 the site sink switch (sudo)
    smd remote      status | off | on                                remote access for the fleet admin (sudo)
    smd upload      status | on | hold | off | discard [--reason R]  legacy words for the same switch (sudo)
```

(b) Find (lines 23415-23417):

```python
                apply      reconcile running services with current config

              Installer / maintenance:
```

and replace it with:

```python
                apply      reconcile running services with current config
                remote     remote access for the fleet admin: status | off | on

              Installer / maintenance:
```

(c) Find (lines 24850-24852):

```python
    sink_sub.add_parser('fill', help='not available yet (a later release)')

    p_psws = sub.add_parser('psws',
```

and replace it with:

```python
    sink_sub.add_parser('fill', help='not available yet (a later release)')

    p_remote = sub.add_parser('remote',
        help='remote access for the fleet admin: status | off | on.  It asks '
             'the Proxmox host, which alone switches the tunnel')
    remote_sub = p_remote.add_subparsers(dest='remote_command')
    remote_sub.add_parser('status',
        help='show whether remote access stands on, off, or wanted on and not up')
    remote_sub.add_parser('off',
        help='turn remote access off; it stays off across reboots')
    remote_sub.add_parser('on', help='turn remote access on again')
    remote_sub.add_parser('motd',
        help='login-banner hook (invoked from /etc/update-motd.d)')

    p_psws = sub.add_parser('psws',
```

(d) Find (lines 24991-24993):

```python
        return cmd_sink(args)

    if args.command == 'upload':
```

and replace it with:

```python
        return cmd_sink(args)

    if args.command == 'remote':
        verb = getattr(args, 'remote_command', None) or 'status'
        # The pairing key belongs to root alone, so every word elevates but
        # the login banner's, which reads no key.
        if verb != 'motd' and _need_root(f'remote {verb}'):
            return 1
        from sigmond.commands.remote import cmd_remote
        return cmd_remote(args)

    if args.command == 'upload':
```

Then, in `docs/contributor/orchestration.md`:

(e) Find (lines 150-151):

```markdown
| `reload` | operator | reload config in place via the control socket or SIGHUP, falling back to restart | `cmd_reload` → `control_socket.py`, `lifecycle.py` | yes (root + lock) |
| `restart` | operator | restart managed units, `reset-failed` first | `cmd_restart` → `lifecycle.py`, `component_state.py`, `catalog.py` | yes (root + lock) |
```

and replace it with:

```markdown
| `reload` | operator | reload config in place via the control socket or SIGHUP, falling back to restart | `cmd_reload` → `control_socket.py`, `lifecycle.py` | yes (root + lock) |
| `remote` | operator | the operator's switch for remote access on an appliance station: `status`, `off` (stays off across reboots), `on`. It asks the Proxmox host over the private link, with a key that can run one script there (`sigmond-rac-switch`), and prints the host's answer; the host alone turns the tunnel on or off (tasks/plan-remote-access.md). `motd` prints the login banner's one line from the last answer | dispatch branch → `commands/remote.py` | all but `motd` do (root: the pairing key belongs to root) |
| `restart` | operator | restart managed units, `reset-failed` first | `cmd_restart` → `lifecycle.py`, `component_state.py`, `catalog.py` | yes (root + lock) |
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_remote_cli.py tests/test_docs_cli_table.py -q`
Expected: `27 passed`.

Run: `SIGMOND_NO_VENV_REEXEC=1 PYTHONPATH=lib .venv/bin/python bin/smd remote --help`
Expected: the usage line `usage: smd remote [-h] {status,off,on,motd} ...` and one help line for each word.

Run: `SIGMOND_NO_VENV_REEXEC=1 PYTHONPATH=lib .venv/bin/python bin/smd remote motd; echo "rc=$?"`
Expected: `rc=0` and no other line: the devbox holds no pairing key. Do not run `smd remote status` here. It
elevates, and sudo would wait for a password that nobody types.

Run: `.venv/bin/pytest -q`
Expected: `3672 passed, 1 warning, 65 subtests passed` (3648 after Task 4, and 24 new).

- [ ] **Step 6: Watch five guards fail, then restore**

```bash
cd /home/mjh/hamsci/repos/sigmond
F=lib/sigmond/commands/remote.py
cp $F $F.keep
mut(){ cp $F.keep $F; python3 - "$F" "$1" "$2" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
assert s.count(sys.argv[2]) == 1, "the mutation's anchor must occur exactly once"
p.write_text(s.replace(sys.argv[2], sys.argv[3]))
PY
rm -rf "$(dirname $F)/__pycache__"      # same size, same second: a stale .pyc would hide the mutation
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/test_remote_cli.py -q 2>&1 | grep -E '^FAILED|passed|failed' | cut -c1-160; }

# 1. ssh would accept a host whose key the pairing never pinned
mut '            '"'"'-o'"'"', '"'"'StrictHostKeyChecking=yes'"'"',
' \
    ''
# -> 1 failed, 23 passed: test_status_asks_the_paired_host_with_the_one_key_and_prints_its_answer

# 2. a refusal from the switch gets remembered as a state
mut '    state = state_of(answer)
    if state is None:' \
    '    state = state_of(answer)
    _remember(state or '"'"'on'"'"')
    if state is None:'
# -> 1 failed, 23 passed: test_a_refusal_from_the_switch_is_not_mistaken_for_a_state

# 3. the state reads from any line that begins right
mut '        if line in STATE_LINES:
            return STATE_LINES[line]' \
    '        for k, v in STATE_LINES.items():
            if line.strip().startswith(k):
                return v'
# -> 1 failed, 23 passed: test_the_state_reads_from_the_exact_line_wherever_it_stands

# 4. anything in the known-hosts file counts as a host to call
mut 're.fullmatch(r'"'"'[0-9A-Fa-f:.]{3,45}'"'"', parts[0])' \
    'parts[0]'
# -> 1 failed, 23 passed: test_a_key_without_a_usable_known_hosts_line_counts_as_not_paired

# 5. the host's exit status gets lost
mut '    _remember(state)
    return r.returncode' \
    '    _remember(state)
    return 0'
# -> 1 failed, 23 passed: test_the_hosts_exit_code_comes_back

mv $F.keep $F
rm -rf "$(dirname $F)/__pycache__"
.venv/bin/pytest tests/test_remote_cli.py -q | tail -1      # 24 passed
```

- [ ] **Step 7: Commit**

```bash
cd /home/mjh/hamsci/repos/sigmond
test "$(grep -c . .git/ATTRIBUTION)" = 2 || { echo "write .git/ATTRIBUTION first (Global Constraints)"; exit 1; }
git add lib/sigmond/commands/remote.py tests/test_remote_cli.py bin/smd docs/contributor/orchestration.md
git commit -m "smd remote: the operator's switch for remote access, in the decoder VM

smd remote on, off and status ask the Proxmox host over the private
link, with the one key the host's pairing placed in the VM, and print
the host's answer.  The host alone turns the tunnel on or off.  Every
word elevates, because the key belongs to root.  A VM that holds no
pairing hears the one host command that makes one.  smd remote motd
prints the login banner's line from the last answer and never asks.

tasks/plan-remote-access.md section 2.

$(cat .git/ATTRIBUTION)"
```

### Task 6: pm-align brings the switch to a station in the field, and pairs it

Spec section 2: "A station installed before v3.71 pairs with one command at the host console,
`sigmond-setup --pair-vm`, or when `pm-align` brings the host to v3.71." Spec, "Phases": "AC0G-B4 and AI6VN
receive the host's half through `pm-align`."

`pm-align` runs on the Proxmox host. It reads the blessed release from GitHub, cuts the host's files out of the
release's `firstboot-v3.sh` and wizard by their heredoc lines, and writes what differs. Two facts about it bind
this task.

**A station runs the pm-align its last alignment installed.** That older copy knows nothing of this plan. It still
does the important part in one pass: it installs the v3.71 wizard as `/usr/local/sbin/sigmond-setup`, the new
console panel and the new `pm-heartbeat.py`, and a new copy of itself. The wizard carries the switch script and
installs it whenever asked (ruling 1), so `sigmond-setup --pair-vm` after that one pass completes the job. The new
copy does everything in one run from then on. The older copy refuses the whole release, though, if the release's
wizard has lost any heredoc it expects. A frozen list in the tests guards that (Review Focus 5).

**`--release` may name a release older than v3.71.** So the six new files are never required: pm-align takes them
from a wizard that carries them and skips them otherwise.

After the files land, pm-align runs the installed wizard's own `--pair-vm`. A failure warns, sets exit 1, and
stops nothing: the heartbeat and tunnel steps still run. The pairing step goes before the tunnel step, which may
drop the operator's session.

pm-align's promise list says it never touches a package. The pairing may install sudo on a host that lacks it
(ruling 9), so the list now names that one exception.

**Files:**
- Modify: `sigmond/scripts/proxmox/pm-align.py` — constants after `WIZARD_HEREDOCS` (line 228); `NOT_TOUCHED`
  (line 236); `desired_files` (line 277); `pairing_step` and `_apply_pairing` above `_say` (line 766); `_do_apply`
  (lines 883-903); the dry run's notes in `main` (line 1008)
- Modify: `sigmond/tests/test_pm_align.py` — twelve tests appended

**Interfaces:**
- Consumes: the wizard's heredoc lines from Tasks 2, 3 and 4
  (`cat > /usr/local/sbin/sigmond-rac-switch <<'RACSWEOF'` and five units), the branch line `    --pair-vm)`, and
  `sigmond-setup --pair-vm` itself: exit 0, or exit 1 with `NOT paired: <reason>` as its last line.
- Produces, in `pm-align.py`:
  - `WIZARD_HEREDOCS_SINCE_V371` (path to mode), `SETUP = "/usr/local/sbin/sigmond-setup"`, `PAIR_FLAG`.
  - `pairing_step(root, *, run) -> str`: `"paired"`, `"skipped: <why>"` or `"failed: <last line>"`.
  - `_apply_pairing(root, run) -> int` (0, or 1 on a failed pairing).
  - `--apply` now runs: files, relays, record, push to VM, pairing, heartbeat, tunnel. Its exit code takes the
    worst of the last three.
  - Lines of output: `  pairing: the decoder VM can run smd remote on, smd remote off and smd remote status`,
    `  pairing: WARN — failed: <last line>`, `  pairing: skipped: <why>`; and on a dry run
    `  pairing: --apply pairs the decoder VM with this host, or re-asserts it (/usr/local/sbin/sigmond-setup --pair-vm)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pm_align.py`:

(a) Find (lines 1523-1524):

```python
           "/usr/local/sbin/pm-align" in WIZARD
```

and replace it with:

```python
           "/usr/local/sbin/pm-align" in WIZARD


# ── remote access: the switch, its units and the pairing ───────────────────
#
# sigmond tasks/plan-remote-access.md.  From v3.71 the wizard carries the
# switch script and five units as heredocs of its own, and pm-align brings
# them to a station already in the field, then pairs its decoder VM.

WIZ_V371 = WIZ + '''rac_install_host_files(){
    rm -f /usr/local/sbin/sigmond-rac-switch
    cat > /usr/local/sbin/sigmond-rac-switch <<'RACSWEOF'
#!/bin/bash
echo "Remote access: off"
RACSWEOF
    cat > /etc/systemd/system/sigmond-rac-pair.path <<'RACPAIRPATHEOF'
[Path]
PathChanged=/etc/ssh/ssh_host_ed25519_key.pub
RACPAIRPATHEOF
}
case "${1:-}" in
    --pair-vm)
        exit 0;;
esac
'''


def _src_v371():
    return pm_align.Sources(FB_FULL, pm_align.render_wizard(WIZ_V371, 100),
                            {"pm-heartbeat.py": b"hb"})


def _apply_v371(tmp_path, monkeypatch, run):
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.71", "a" * 40, "d" * 40, "f" * 64, ""))
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: _src_v371())
    return pm_align.main(["--apply"], root=tmp_path, run=run)


def test_desired_files_takes_the_switch_and_its_units_from_a_v371_wizard():
    files = {f.path: f for f in pm_align.desired_files(_src_v371(), 100)}
    sw = files["/usr/local/sbin/sigmond-rac-switch"]
    assert sw.mode == 0o755 and sw.content == b'#!/bin/bash\necho "Remote access: off"\n'
    unit = files["/etc/systemd/system/sigmond-rac-pair.path"]
    assert unit.mode == 0o644 and b"PathChanged=/etc/ssh/ssh_host_ed25519_key.pub" in unit.content


def test_desired_files_accepts_a_wizard_from_before_v371():
    # `--release v3.70` must keep working: nothing the new wizard adds is required.
    paths = {f.path for f in pm_align.desired_files(_src(), 100)}
    assert not any("sigmond-rac" in p for p in paths)
    assert "/usr/local/bin/sigmond-vm" in paths


def test_the_real_wizard_carries_every_file_pm_align_delivers_from_it():
    src = pm_align.Sources(FB_FULL, pm_align.render_wizard(WIZARD, 100), {})
    files = {f.path: f for f in pm_align.desired_files(src, 100)}
    for path, mode in {**pm_align.WIZARD_HEREDOCS, **pm_align.WIZARD_HEREDOCS_SINCE_V371}.items():
        assert path in files, f"the wizard no longer writes {path} from a quoted heredoc"
        assert files[path].mode == mode
    switch = files["/usr/local/sbin/sigmond-rac-switch"].content.decode()
    assert switch.startswith("#!/bin/bash\n")
    assert subprocess.run(["bash", "-n"], input=switch, text=True,
                          capture_output=True).returncode == 0
    assert f"    {pm_align.PAIR_FLAG})" in WIZARD


# What every pm-align already in the field demands of a release's wizard.  A
# station runs the pm-align its last alignment installed, and that copy reads
# the NEW release's wizard.  It refuses the whole release, with "the release's
# wizard no longer writes ...", when one of these has gone.  Frozen here as
# literals on purpose: this test must not move when pm-align.py does.
FIELDED_QUOTED = ("/usr/local/bin/sigmond-vm", "/usr/local/lib/sigmond/vm-port-relay.py")
FIELDED_TEMPLATES = ("/etc/systemd/system/sigmond-vm-$RNAME-relay.socket",
                     "/etc/systemd/system/sigmond-vm-$RNAME-relay@.service")


def test_the_real_wizard_still_satisfies_every_pm_align_in_the_field():
    quoted = pm_align.extract_heredocs(WIZARD)
    for path in FIELDED_QUOTED:
        assert quoted.get(path, "").strip(), path
    templates = pm_align.extract_heredocs(WIZARD, quoted_only=False)
    for path in FIELDED_TEMPLATES:
        assert path in templates, path
    units = {f.path: f.content.decode() for f in pm_align.relay_units(WIZARD, 100)}
    assert len(units) == 8
    for name, lport, vport in (("ssh", 12222, 22), ("web", 12223, 8081),
                               ("station", 12224, 8000), ("gmag", 12225, 8082)):
        assert f"ListenStream=127.0.0.1:{lport}\n" in units[f"/etc/systemd/system/sigmond-vm-{name}-relay.socket"]
        svc = units[f"/etc/systemd/system/sigmond-vm-{name}-relay@.service"]
        assert f"ExecStart=/usr/local/lib/sigmond/vm-port-relay.py {vport}\n" in svc
        assert "Environment=SIGMOND_VMID=100\n" in svc


def _setup(tmp_path, text):
    p = tmp_path / "usr/local/sbin/sigmond-setup"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_pairing_step_runs_the_installed_wizards_pair_vm(tmp_path):
    _setup(tmp_path, WIZ_V371)
    calls = []
    assert pm_align.pairing_step(tmp_path, run=_fake_run(calls)) == "paired"
    assert calls == [["/usr/local/sbin/sigmond-setup", "--pair-vm"]]


def test_pairing_step_skips_a_wizard_that_cannot_pair(tmp_path):
    calls = []
    assert pm_align.pairing_step(tmp_path, run=_fake_run(calls)).startswith("skipped: no sigmond-setup")
    _setup(tmp_path, WIZ + "# it mentions --pair-vm in a comment, and has no such branch\n")
    assert "predates v3.71" in pm_align.pairing_step(tmp_path, run=_fake_run(calls))
    assert calls == []


def test_pairing_step_reports_the_wizards_last_line_on_failure(tmp_path):
    _setup(tmp_path, WIZ_V371)
    out = pm_align.pairing_step(tmp_path, run=_fake_run(
        [], rc=1, stdout="[wizard] ...\n[wizard] NOT paired: the guest agent in VM 100 does not answer\n"))
    assert out == "failed: [wizard] NOT paired: the guest agent in VM 100 does not answer"


def test_main_apply_pairs_after_the_files_and_before_heartbeat_and_tunnel(tmp_path, monkeypatch, capsys):
    _tunnel_root(tmp_path)                 # a tunnel short of two channels: the step launches
    (tmp_path / "etc/pm-heartbeat").mkdir(parents=True)
    (tmp_path / "etc/pm-heartbeat/config.toml").write_text(
        'station = "b4-pm"\nvmid = 100\ndest_host = "wd30"\n')
    order = []

    def run(argv, **kw):
        argv = list(argv)
        if argv[:2] == ["/usr/local/sbin/sigmond-setup", "--pair-vm"]:
            # the new wizard and the switch script stand on disk by now
            assert (tmp_path / "usr/local/sbin/sigmond-rac-switch").exists()
            order.append("pair")
        elif argv[:3] == ["qm", "guest", "exec"]:
            order.append("push")
        elif argv and argv[0].endswith("pm-heartbeat-setup.sh"):
            order.append("heartbeat")
        elif argv[0] == "systemd-run":
            order.append("tunnel")
        return subprocess.CompletedProcess(argv, 0, '{"exitcode": 0}', "")

    src = _src_v371()
    for name in pm_align.HB_FILES:
        src.proxmox[name] = b"#!/bin/sh\n"
    monkeypatch.setattr(pm_align, "_geteuid", lambda: 0)
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.71", "a" * 40, "d" * 40, "f" * 64, ""))
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: src)
    assert pm_align.main(["--apply"], root=tmp_path, run=run) == 0
    assert order == ["push", "pair", "heartbeat", "tunnel"]
    assert "pairing: the decoder VM can run smd remote on, smd remote off and smd remote status" \
        in capsys.readouterr().out


def test_main_apply_with_a_failed_pairing_exits_1_and_still_finishes(tmp_path, monkeypatch, capsys):
    def run(argv, **kw):
        if list(argv)[:2] == ["/usr/local/sbin/sigmond-setup", "--pair-vm"]:
            return subprocess.CompletedProcess(argv, 1, "[wizard] NOT paired: this host has no sudo\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert _apply_v371(tmp_path, monkeypatch, run) == 1
    out = capsys.readouterr().out
    assert "pairing: WARN — failed: [wizard] NOT paired: this host has no sudo" in out
    assert "retry: /usr/local/sbin/sigmond-setup --pair-vm" in out
    assert (tmp_path / "usr/local/sbin/sigmond-rac-switch").exists()       # the files landed
    assert json.loads((tmp_path / "etc/sigmond-appliance/host-aligned.json").read_text())["release"] == "v3.71"
    assert "tunnel: no host tunnel configured" in out                      # the later steps ran


def test_main_apply_survives_a_pairing_step_that_raises(tmp_path, monkeypatch, capsys):
    def run(argv, **kw):
        if list(argv)[:2] == ["/usr/local/sbin/sigmond-setup", "--pair-vm"]:
            raise subprocess.TimeoutExpired(argv, 900)
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert _apply_v371(tmp_path, monkeypatch, run) == 1
    assert "pairing: WARN — failed: raised TimeoutExpired" in capsys.readouterr().out


def test_main_dry_run_names_the_pairing_only_for_a_wizard_that_can_pair(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm_align, "fetch_release", lambda tag=None, urlopen=None:
                        pm_align.Release("v3.71", "a" * 40, "d" * 40, "f" * 64, ""))
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: _src_v371())
    calls = []
    pm_align.main([], root=tmp_path, run=_fake_run(calls))
    assert "pairing: --apply pairs the decoder VM with this host" in capsys.readouterr().out
    assert calls == []                                 # a dry run pairs nothing
    monkeypatch.setattr(pm_align, "fetch_sources", lambda rel, vmid, urlopen=None: _src())
    pm_align.main([], root=tmp_path, run=_fake_run(calls))
    assert "pairing:" not in capsys.readouterr().out


def test_not_touched_names_the_one_package_the_pairing_may_install():
    text = " ".join(pm_align.NOT_TOUCHED)
    assert "every apt package" in text and "sudo" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_pm_align.py -q`
Expected: `10 failed, 113 passed`. Two of the twelve new tests pass already, and both pin what must stay true:
`test_desired_files_accepts_a_wizard_from_before_v371` and
`test_the_real_wizard_still_satisfies_every_pm_align_in_the_field`.

- [ ] **Step 3: Implement in `scripts/proxmox/pm-align.py`**

(a) Find (lines 228-229):

```python
}
RELAYS = (("ssh", 12222, 22), ("web", 12223, 8081),
```

and replace it with:

```python
}
# The remote-access switch and its units (sigmond tasks/plan-remote-access.md).
# A wizard from before v3.71 carries none of them, and `--release` may name
# such a release, so nothing here is ever required.
WIZARD_HEREDOCS_SINCE_V371 = {
    "/usr/local/sbin/sigmond-rac-switch": 0o755,
    "/etc/systemd/system/sigmond-rac-register.service": 0o644,
    "/etc/systemd/system/sigmond-rac-register.timer": 0o644,
    "/etc/systemd/system/sigmond-rac-pair.service": 0o644,
    "/etc/systemd/system/sigmond-rac-pair.timer": 0o644,
    "/etc/systemd/system/sigmond-rac-pair.path": 0o644,
}
SETUP = "/usr/local/sbin/sigmond-setup"
PAIR_FLAG = "--pair-vm"
RELAYS = (("ssh", 12222, 22), ("web", 12223, 8081),
```

(b) Find (lines 235-236):

```python
    "resctrl (CAT), and the VM's CPU-pinning hookscript (/var/lib/vz/snippets/*)",
    "Proxmox itself and every apt package",
```

and replace it with:

```python
    "resctrl (CAT), and the VM's CPU-pinning hookscript (/var/lib/vz/snippets/*)",
    "Proxmox itself and every apt package (one exception: on a host with no sudo, "
    "the pairing step installs sudo, which its one sudoers line needs)",
```

(c) Find (lines 277-278):

```python
        out.append(HostFile(path, wz[path].encode(), mode, "sigmond-wizard.sh"))
    out.extend(relay_units(src.wizard, vmid))
```

and replace it with:

```python
        out.append(HostFile(path, wz[path].encode(), mode, "sigmond-wizard.sh"))
    for path, mode in WIZARD_HEREDOCS_SINCE_V371.items():
        if path in wz:
            out.append(HostFile(path, wz[path].encode(), mode, "sigmond-wizard.sh"))
    out.extend(relay_units(src.wizard, vmid))
```

(d) Find (lines 763-766):

```python
    return "re-run" if existed else "set up"


def _say(msg=""):
```

and replace it with:

```python
    return "re-run" if existed else "set up"


def pairing_step(root: Path, *, run) -> str:
    """Pair the decoder VM with this host, or re-assert the pairing, by
    running the installed wizard's own `--pair-vm` (sigmond
    tasks/plan-remote-access.md section 2).  The VM then holds one key that
    can run one script here, and its operator can type `smd remote off`.

    "paired"; "skipped: <why>" when the installed wizard predates v3.71; or
    "failed: <the wizard's last line>".  Never raises for a wizard that ran."""
    try:
        text = _under(root, SETUP).read_text()
    except OSError:
        return "skipped: no sigmond-setup on this host"
    if f"    {PAIR_FLAG})" not in text:
        return "skipped: this release's wizard cannot pair (it predates v3.71)"
    r = run([SETUP, PAIR_FLAG], capture_output=True, text=True, timeout=900)
    if r.returncode == 0:
        return "paired"
    tail = (r.stdout or r.stderr or "").strip().splitlines()[-1:]
    return f"failed: {tail[0] if tail else 'exit ' + str(r.returncode)}"


def _apply_pairing(root: Path, run) -> int:
    """The pairing step of --apply.  A failure is a WARN and makes --apply's
    exit code 1; it never stops the heartbeat and tunnel steps that follow."""
    try:
        out = pairing_step(root, run=run)
    except BaseException as exc:
        out = f"failed: raised {exc!r}"
    if out.startswith("failed"):
        _say(f"  pairing: WARN — {out}")
        _say(f"  until it pairs, `smd remote` in the VM cannot reach this host; retry: {SETUP} {PAIR_FLAG}")
        return 1
    if out == "paired":
        _say("  pairing: the decoder VM can run smd remote on, smd remote off and smd remote status")
    else:
        _say(f"  pairing: {out}")
    return 0


def _say(msg=""):
```

(e) Find (lines 884-885):

```python
    heartbeat, then the tunnel launch LAST (it may drop the operator's
    ssh session)."""
```

and replace it with:

```python
    pairing, heartbeat, then the tunnel launch LAST (it may drop the
    operator's ssh session)."""
```

(f) Find (lines 900-903):

```python
    hb_rc = _apply_heartbeat(root, src, run=run, vmid=vmid, opt_in=heartbeat_opt_in,
                             dest=heartbeat_dest)
    tunnel_rc = _apply_tunnel(root, run)
    return max(hb_rc, tunnel_rc)
```

and replace it with:

```python
    pair_rc = _apply_pairing(root, run)
    hb_rc = _apply_heartbeat(root, src, run=run, vmid=vmid, opt_in=heartbeat_opt_in,
                             dest=heartbeat_dest)
    tunnel_rc = _apply_tunnel(root, run)
    return max(pair_rc, hb_rc, tunnel_rc)
```

(g) Find (lines 1008-1009):

```python
    _say(f"  {_dry_run_heartbeat_note(root)}")
    _say(f"  summary: {len(pending)} file(s) to refresh; re-run with --apply as root")
```

and replace it with:

```python
    _say(f"  {_dry_run_heartbeat_note(root)}")
    if f"    {PAIR_FLAG})" in src.wizard:
        _say(f"  pairing: --apply pairs the decoder VM with this host, or re-asserts it ({SETUP} {PAIR_FLAG})")
    _say(f"  summary: {len(pending)} file(s) to refresh; re-run with --apply as root")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pm_align.py -q`
Expected: `123 passed`.

Run: `.venv/bin/pytest -q`
Expected: `3684 passed, 1 warning, 65 subtests passed` (3672 after Task 5, and 12 new).

- [ ] **Step 5: Watch four guards fail, then restore**

```bash
cd /home/mjh/hamsci/repos/sigmond
F=scripts/proxmox/pm-align.py
cp $F $F.keep
mut(){ cp $F.keep $F; python3 - "$F" "$1" "$2" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
assert s.count(sys.argv[2]) == 1, "the mutation's anchor must occur exactly once"
p.write_text(s.replace(sys.argv[2], sys.argv[3]))
PY
rm -rf "$(dirname $F)/__pycache__"      # same size, same second: a stale .pyc would hide the mutation
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/test_pm_align.py -q 2>&1 | grep -E '^FAILED|passed|failed' | cut -c1-160; }

# 1. the pairing runs after the tunnel step, which may drop the session
mut '    pair_rc = _apply_pairing(root, run)
    hb_rc = _apply_heartbeat(root, src, run=run, vmid=vmid, opt_in=heartbeat_opt_in,
                             dest=heartbeat_dest)
    tunnel_rc = _apply_tunnel(root, run)' \
    '    hb_rc = _apply_heartbeat(root, src, run=run, vmid=vmid, opt_in=heartbeat_opt_in,
                             dest=heartbeat_dest)
    tunnel_rc = _apply_tunnel(root, run)
    pair_rc = _apply_pairing(root, run)'
# -> 1 failed, 122 passed: test_main_apply_pairs_after_the_files_and_before_heartbeat_and_tunnel

# 2. a failed pairing leaves `--apply` at exit 0
mut '    return max(pair_rc, hb_rc, tunnel_rc)' \
    '    return max(hb_rc, tunnel_rc)'
# -> 2 failed, 121 passed: test_main_apply_with_a_failed_pairing_exits_1_and_still_finishes, test_main_apply_survives_a_pairing_step_that_raises

# 3. a wizard that only mentions the flag counts as able to pair
mut '    if f"    {PAIR_FLAG})" not in text:' \
    '    if PAIR_FLAG not in text:'
# -> 1 failed, 122 passed: test_pairing_step_skips_a_wizard_that_cannot_pair

# 4. a pairing step that raises takes `--apply` down with it
mut '    try:
        out = pairing_step(root, run=run)
    except BaseException as exc:
        out = f"failed: raised {exc!r}"' \
    '    out = pairing_step(root, run=run)'
# -> 1 failed, 122 passed: test_main_apply_survives_a_pairing_step_that_raises

mv $F.keep $F
rm -rf "$(dirname $F)/__pycache__"
.venv/bin/pytest tests/test_pm_align.py -q | tail -1      # 123 passed
```

- [ ] **Step 6: Commit**

```bash
cd /home/mjh/hamsci/repos/sigmond
test "$(grep -c . .git/ATTRIBUTION)" = 2 || { echo "write .git/ATTRIBUTION first (Global Constraints)"; exit 1; }
git add scripts/proxmox/pm-align.py tests/test_pm_align.py
git commit -m "pm-align: deliver the remote-access switch, then pair the decoder VM

pm-align --apply now takes the switch script and its five units from a
wizard that carries them, and afterwards runs the installed wizard's
--pair-vm, before the heartbeat and tunnel steps.  A failed pairing
warns and sets exit 1; nothing else stops for it.  None of the new
files is required, so --release can still name a release before v3.71.
The list of what pm-align never touches names its one exception: on a
host with no sudo the pairing installs it.

A frozen list in the tests holds what every pm-align already in the
field reads out of a release's wizard.

tasks/plan-remote-access.md section 2.

$(cat .git/ATTRIBUTION)"
```

### Task 7: the host's heartbeat reports the switch (`rac` block)

Spec section 4: "The host's heartbeat gains a `rac` block: the wish, whether the tunnel runs, channels up of
declared, since when. The fleet board then shows 'off by the operator' as a decision, never as a station to
chase. A schema change on the station and on wd30."

The schema names a fixed set of blocks, and its `validate()` refuses an envelope that carries any other name. Two
consumers walk that set: the station's own heartbeat in the decoder VM (`lib/sigmond/heartbeat.py`,
`for name in BLOCK_NAMES`) and the fleet board. Adding `rac` to `BLOCK_NAMES` would therefore make every decoder VM
report a block it has no view of. So the schema gains a second, host-only set (ruling 14):
`HOST_BLOCK_NAMES = ("rac",)`, and `ALL_BLOCK_NAMES` for the two places that must know every name: `validate()`
here, and the board in Task 8. The station's seven stay the station's seven.

The emitter, `scripts/proxmox/pm-heartbeat.py`, runs on the host with no sigmond beside it. It asks the switch
script for `status` and reads the file the script leaves (Task 2), so the three states have one author.

| What the switch reports | Verdict | Reason |
|---|---|---|
| on, every declared channel accepted | VALID | `on: 6 of 6 channels` |
| on, fewer channels than declared | INCONCLUSIVE | `on, short of channels: 4 of 6` |
| off, wish on file | VALID | `off by the operator since <time>` |
| off, never registered, no wish on file | VALID | `off: this station never registered for remote access` |
| off, yet the tunnel service runs | INCONCLUSIVE | `off by the operator since <time>, yet the tunnel service runs` |
| wanted on, not registered | INVALID | `wanted on, not up: not registered yet (the host retries)` |
| wanted on, service stopped | INVALID | `wanted on, not up: the tunnel service does not run` |
| wanted on, no channel accepted | INVALID | `wanted on, not up: the gateway accepted none of 6 channels` |

An operator's off reads VALID on purpose. It must never worsen the host's rollup: the spec wants a decision shown,
"never a station to chase". Task 8 gives it a look of its own on the board.

**A host without the switch script claims no `rac` block at all.** That covers a host installed before v3.71, and
one that an older pm-align has brought only part of the way (Review Focus 5). The board draws a missing block as
"n/a". An INDETERMINATE there would grey the rollup of every host not yet aligned.

**The order of deployment matters, and Task 12 keeps it**: wd30's copy of the schema must know `rac` before any
host sends it. An older `validate()` quarantines the whole envelope, and a quarantined host looks silent.
`tests/test_fleetboard.py` gains a control for exactly that in Task 8.

**Files:**
- Modify: `sigmond/lib/sigmond/heartbeat_schema.py` — two constants after `BLOCK_NAMES` (line 67); `validate`
  (lines 129-131)
- Modify: `sigmond/scripts/proxmox/pm-heartbeat.py` — the module docstring; `BLOCK_NAMES` (line 97); `assemble`
  (line 153); the `rac` builders above `_MAPPERS` (line 496); `build_production_checks` (line 596)
- Modify: `sigmond/tests/test_pm_heartbeat.py`, `sigmond/tests/test_heartbeat.py`

**Interfaces:**
- Consumes: Task 2's `/usr/local/sbin/sigmond-rac-switch status` (exit 0 or 3 with an answer) and the keys of
  `/run/sigmond/rac-state`: `state`, `wish`, `wish_source`, `registered`, `tunnel`, `channels_up`,
  `channels_declared`, `changed_at`, `changed_by`.
- Produces:
  - `heartbeat_schema.HOST_BLOCK_NAMES`, `heartbeat_schema.ALL_BLOCK_NAMES`; `validate()` accepts any subset of
    the latter. `BLOCK_NAMES` keeps its seven names.
  - `pm_heartbeat.BLOCK_NAMES == ("versions", "doctor", "resources", "rac")`, `RAC_SWITCH`, `RAC_STATE_FILE`.
  - `build_rac_raw(switch_present, run_switch_status, read_state) -> dict | None`; `_map_rac(raw) -> dict | None`.
    `None` means "not claimed", and `assemble()` then leaves the block out.
  - The block's `data`: `wish`, `wish_source`, `registered` (bool), `tunnel`, `channels_up` (int),
    `channels_declared` (int), `since`, `by`. Task 8 reads `data["wish"]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_pm_heartbeat.py`:

(a) Find (lines 54-55):

```python
def rich_checks(**overrides):
    """The five top-level check runners a fully-healthy PM would wire.
```

and replace it with:

```python
#: What sigmond-rac-switch leaves in /run/sigmond/rac-state for a tunnel that
#: runs with every channel accepted, already split into key and value.
RAC_ON = {
    "state": "on", "wish": "on", "wish_source": "file", "registered": "yes",
    "tunnel": "running", "channels_up": "6", "channels_declared": "6",
    "rac": "507", "gateway": "gw2.wsprdaemon.org", "tier": "wsprdaemon (secure)",
    "changed_at": "2026-10-09T18:22:05Z", "changed_by": "install",
    "checked_at": "2026-10-09T19:00:02Z",
}


def rac(**changes):
    return dict(RAC_ON, **changes)


def rich_checks(**overrides):
    """The six top-level check runners a fully-healthy PM would wire.
```

(b) Find (lines 77-78):

```python
        },
    }
```

and replace it with:

```python
        },
        "rac": lambda: rac(),
    }
```

(c) Find (lines 90-91):

```python
    assert errors == []
    assert set(envelope["blocks"]) == {"versions", "doctor", "resources"}
```

and replace it with:

```python
    assert errors == []
    assert set(envelope["blocks"]) == {"versions", "doctor", "resources", "rac"}
```

(d) Find (lines 104-105):

```python
    assert pm_heartbeat.BLOCK_NAMES == ("versions", "doctor", "resources")
    assert set(pm_heartbeat.BLOCK_NAMES) <= set(heartbeat_schema.BLOCK_NAMES)
```

and replace it with:

```python
    assert pm_heartbeat.BLOCK_NAMES == ("versions", "doctor", "resources", "rac")
    assert set(pm_heartbeat.BLOCK_NAMES) <= set(heartbeat_schema.ALL_BLOCK_NAMES)
    # `rac` belongs to the host alone: the station's own block set lacks it.
    assert "rac" in heartbeat_schema.HOST_BLOCK_NAMES
    assert "rac" not in heartbeat_schema.BLOCK_NAMES
```

(e) Find (lines 358-359):

```python
# ---------------------------------------------------------------------------
# forbidden lying keys — the same recursive key-walk ban as test_heartbeat.py
```

and replace it with:

```python
# ---------------------------------------------------------------------------
# rac block — the operator's switch for remote access
# (sigmond tasks/plan-remote-access.md section 4)
# ---------------------------------------------------------------------------

RAC_CASES = [
    (rac(), "VALID", "on: 6 of 6 channels"),
    (rac(channels_up="4"), "INCONCLUSIVE", "on, short of channels: 4 of 6"),
    (rac(state="off", wish="off", tunnel="stopped", channels_up="0", changed_by="vm"),
     "VALID", "off by the operator since 2026-10-09T18:22:05Z"),
    (rac(state="off", wish="off", wish_source="units", registered="no", tunnel="stopped",
         channels_up="0", channels_declared="0", changed_at="", changed_by=""),
     "VALID", "off: this station never registered for remote access"),
    (rac(state="off", wish="off"), "INCONCLUSIVE",
     "off by the operator since 2026-10-09T18:22:05Z, yet the tunnel service runs"),
    (rac(state="down", registered="no", tunnel="stopped", channels_up="0", channels_declared="0"),
     "INVALID", "wanted on, not up: not registered yet (the host retries)"),
    (rac(state="down", tunnel="stopped", channels_up="0"),
     "INVALID", "wanted on, not up: the tunnel service does not run"),
    (rac(state="down", channels_up="0"),
     "INVALID", "wanted on, not up: the gateway accepted none of 6 channels"),
    (rac(state="sideways"), "INDETERMINATE", "the switch reported no usable state ('sideways')"),
    (rac(channels_up="lots", channels_declared=""), "INCONCLUSIVE", "on, short of channels: 0 of 0"),
]


@pytest.mark.parametrize("raw,verdict,reason", RAC_CASES,
                         ids=[f"{c[1]}-{i}" for i, c in enumerate(RAC_CASES)])
def test_rac_block_verdicts(raw, verdict, reason):
    block = pm_heartbeat._map_rac(raw)
    assert (block["verdict"], block["reason"]) == (verdict, reason)
    assert set(block["data"]) == {"wish", "wish_source", "registered", "tunnel", "channels_up",
                                  "channels_declared", "since", "by"}


def test_an_operators_off_never_worsens_the_hosts_rollup():
    off = rac(state="off", wish="off", tunnel="stopped", channels_up="0", changed_by="vm")
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(rac=lambda: off))
    assert envelope["blocks"]["rac"]["verdict"] == "VALID"
    assert envelope["blocks"]["rac"]["data"]["wish"] == "off"
    assert envelope["blocks"]["rac"]["data"]["by"] == "vm"
    assert envelope["rollup"]["verdict"] == "VALID"
    assert heartbeat_schema.validate(envelope) == []


def test_wanted_on_and_not_up_turns_the_hosts_rollup_invalid():
    down = rac(state="down", tunnel="stopped", channels_up="0")
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(rac=lambda: down))
    assert envelope["rollup"]["verdict"] == "INVALID"
    assert envelope["rollup"]["reason"].startswith("rac: wanted on, not up")


def test_a_host_without_the_switch_script_claims_no_rac_block():
    # A host installed before v3.71, or one pm-align has only half brought up.
    raw = pm_heartbeat.build_rac_raw(lambda: False, lambda: 1 / 0, lambda: 1 / 0)
    assert raw is None
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(rac=lambda: None))
    assert set(envelope["blocks"]) == {"versions", "doctor", "resources"}
    assert envelope["rollup"]["verdict"] == "VALID"
    assert heartbeat_schema.validate(envelope) == []


def test_build_rac_raw_reads_the_file_the_switch_leaves():
    text = "".join(f"{k}={v}\n" for k, v in RAC_ON.items())
    for rc in (0, 3):                              # 3: wanted on, not up; still an answer
        raw = pm_heartbeat.build_rac_raw(lambda: True, lambda: rr(rc), lambda: text)
        assert raw == RAC_ON
    assert raw["tier"] == "wsprdaemon (secure)"     # a value with a space and brackets


@pytest.mark.parametrize("status,state_text,needle", [
    (rr(2, "", "sigmond-rac-switch: refused."), "state=on\n", "exited 2"),
    (rr(127, "", "not found"), "state=on\n", "exited 127"),
    (rr(0), "", "names no state"),
    (rr(0), "nonsense without an equals sign\n", "names no state"),
], ids=["refused", "not-runnable", "empty-file", "no-state-line"])
def test_a_switch_that_gives_no_answer_reads_indeterminate_never_valid(status, state_text, needle):
    check = lambda: pm_heartbeat.build_rac_raw(lambda: True, lambda: status, lambda: state_text)
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(rac=check))
    block = envelope["blocks"]["rac"]
    assert block["verdict"] == "INDETERMINATE" and needle in block["reason"]
    assert envelope["rollup"]["verdict"] != "VALID"


def test_a_state_file_that_cannot_be_read_reads_indeterminate():
    def unreadable():
        raise FileNotFoundError("/run/sigmond/rac-state")
    check = lambda: pm_heartbeat.build_rac_raw(lambda: True, lambda: rr(0), unreadable)
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(rac=check))
    assert envelope["blocks"]["rac"]["verdict"] == "INDETERMINATE"


def test_the_emitter_reads_the_keys_the_switch_writes():
    # The switch script ships inside the wizard; its write_state() names the
    # keys.  A renamed key there would turn this block INDETERMINATE fleet-wide.
    wizard = (PROXMOX / "sigmond-wizard.sh").read_text()
    for key in ("state", "wish", "wish_source", "registered", "tunnel", "channels_up",
                "channels_declared", "changed_at", "changed_by"):
        assert f"printf '{key}=%s\\n'" in wizard, key
    assert f"RUN_DIR=\"${{SIGMOND_RAC_RUN_DIR:-{pm_heartbeat.RAC_STATE_FILE.rsplit('/', 1)[0]}}}\"" in wizard
    assert pm_heartbeat.RAC_SWITCH == "/usr/local/sbin/sigmond-rac-switch"


# ---------------------------------------------------------------------------
# forbidden lying keys — the same recursive key-walk ban as test_heartbeat.py
```

In `tests/test_heartbeat.py`:

(f) Find (lines 1189-1192):

```python
    assert heartbeat_schema.validate(envelope) == []


# --- sigmond#53: uploads policy -------------------------------------------
```

and replace it with:

```python
    assert heartbeat_schema.validate(envelope) == []


def test_validate_accepts_the_hosts_rac_block():
    """A Proxmox host reports the operator's switch for remote access in a
    block of its own (tasks/plan-remote-access.md section 4).  The server's
    copy of validate() must know the name before any host sends it: an
    unknown block name quarantines the whole envelope, and a quarantined
    host looks silent on the board."""
    envelope = {
        "kind": heartbeat_schema.KIND,
        "schema_version": heartbeat_schema.SCHEMA_VERSION,
        "station": "b4-pm",
        "emitted_at": "2026-10-09T19:00:02Z",
        "interval_sec": 300,
        "rollup": {"verdict": "VALID", "reason": "versions: 2 read"},
        "blocks": {
            "versions": {"verdict": "VALID", "reason": "2 read"},
            "rac": {"verdict": "VALID", "reason": "off by the operator since 2026-10-09T18:22:05Z",
                    "data": {"wish": "off"}},
        },
    }
    assert heartbeat_schema.validate(envelope) == []
    envelope["blocks"]["tunnel"] = {"verdict": "VALID", "reason": "x"}
    assert any("unknown block 'tunnel'" in e for e in heartbeat_schema.validate(envelope))


def test_the_stations_own_block_set_holds_no_host_block():
    """sigmond.heartbeat assembles BLOCK_NAMES.  A host block there would
    make every decoder VM report a block it has no view of."""
    assert heartbeat_schema.HOST_BLOCK_NAMES == ("rac",)
    assert not set(heartbeat_schema.HOST_BLOCK_NAMES) & set(heartbeat_schema.BLOCK_NAMES)
    assert heartbeat_schema.ALL_BLOCK_NAMES == (
        heartbeat_schema.BLOCK_NAMES + heartbeat_schema.HOST_BLOCK_NAMES)
    assert len(heartbeat_schema.BLOCK_NAMES) == 7


# --- sigmond#53: uploads policy -------------------------------------------
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_pm_heartbeat.py tests/test_heartbeat.py -q`
Expected: `24 failed, 190 passed`.

- [ ] **Step 3: Implement the schema, in `lib/sigmond/heartbeat_schema.py`**

(a) Find (lines 67-70):

```python
)


def validate(envelope: dict) -> list:
```

and replace it with:

```python
)

# Blocks only a Proxmox HOST can speak to.  A station's own heartbeat
# (sigmond.heartbeat) assembles BLOCK_NAMES and never these: the decoder VM
# has no view of what runs on the host under it.
#   rac   remote access: the operator's switch and the tunnel behind it
#         (tasks/plan-remote-access.md section 4)
HOST_BLOCK_NAMES = ("rac",)

# Every block name the wire knows, in report order.  validate() accepts any
# subset of these; the board draws one column for each.
ALL_BLOCK_NAMES = BLOCK_NAMES + HOST_BLOCK_NAMES


def validate(envelope: dict) -> list:
```

(b) Find (lines 129-131):

```python
            if name not in BLOCK_NAMES:
                errors.append(
                    f"unknown block {name!r}, expected one of {BLOCK_NAMES}")
```

and replace it with:

```python
            if name not in ALL_BLOCK_NAMES:
                errors.append(
                    f"unknown block {name!r}, expected one of {ALL_BLOCK_NAMES}")
```

- [ ] **Step 4: Implement the block, in `scripts/proxmox/pm-heartbeat.py`**

(a) Find (lines 34-45):

```python
``BLOCK_NAMES`` here is NOT the schema's full 7-name set — it is the
THREE blocks a PM can honestly speak to (``versions``, ``doctor``,
``resources``).  ``heartbeat_schema.validate()`` deliberately accepts any
SUBSET of its block names (see its docstring: additive/partial producers
must not be rejected), which is what makes a heartbeat with only these
three blocks a valid ``station_heartbeat`` envelope.  Emitting
``timing``/``gaps``/``uploads``/``manifest`` from a PM would be a lie —
a Proxmox host has no view of any of those.

Architecture (mirrors lib/sigmond/heartbeat.py's shape on purpose)
-------------------------------------------------------------------
* ``assemble(config, checks)`` — pure.  ``checks`` is a bundle of five
```

and replace it with:

```python
``BLOCK_NAMES`` here is NOT the schema's full name set — it is the FOUR
blocks a PM can honestly speak to (``versions``, ``doctor``,
``resources``, and ``rac``, which only a host can see).
``heartbeat_schema.validate()`` deliberately accepts any SUBSET of its
block names (see its docstring: additive/partial producers must not be
rejected), which is what makes a heartbeat with only these blocks a
valid ``station_heartbeat`` envelope.  Emitting ``timing``/``gaps``/
``uploads``/``manifest`` from a PM would be a lie — a Proxmox host has
no view of any of those.

``rac`` reports the operator's switch for remote access and the tunnel
behind it (sigmond tasks/plan-remote-access.md section 4).  A host that
carries no switch script, one installed before v3.71, leaves the block
out: the board then draws "n/a", never "measured nothing".

Architecture (mirrors lib/sigmond/heartbeat.py's shape on purpose)
-------------------------------------------------------------------
* ``assemble(config, checks)`` — pure.  ``checks`` is a bundle of six
```

(b) Find (lines 95-97):

```python
# The subset of the schema's 7 block names a PM can honestly speak to, in
# report order.  See module docstring: validate() accepts any subset.
BLOCK_NAMES = ("versions", "doctor", "resources")
```

and replace it with:

```python
# The subset of the schema's block names a PM can honestly speak to, in
# report order.  See module docstring: validate() accepts any subset.
BLOCK_NAMES = ("versions", "doctor", "resources", "rac")

# The one switch for the remote-access tunnel, and the file its `status`
# leaves behind: one "key=value" line per fact.
RAC_SWITCH = "/usr/local/sbin/sigmond-rac-switch"
RAC_STATE_FILE = "/run/sigmond/rac-state"
```

(c) Find (lines 131-132):

```python
                ``sigmond.heartbeat.assemble``).
    ``checks``  five zero-arg callables: "now" (-> datetime, required),
```

and replace it with:

```python
                ``sigmond.heartbeat.assemble``).
    ``checks``  six zero-arg callables: "now" (-> datetime, required),
```

(d) Find (lines 153-155):

```python
    blocks = {name: _assemble_block(name, checks) for name in BLOCK_NAMES}

    interval_sec = config.get("interval_sec") or DEFAULT_INTERVAL_SEC
```

and replace it with:

```python
    blocks = {name: _assemble_block(name, checks) for name in BLOCK_NAMES}
    # A mapper answers None for a block this host cannot claim (`rac` on a
    # host without the switch script).  Leaving the block out says "not
    # claimed".  An INDETERMINATE would say "looked and could not tell",
    # and would grey every host that has not yet been brought up to v3.71.
    blocks = {name: block for name, block in blocks.items()
              if block is not None}

    interval_sec = config.get("interval_sec") or DEFAULT_INTERVAL_SEC
```

(e) Find (lines 493-496):

```python
    return _block("VALID", "host resource counters read", data)


_MAPPERS = {
```

and replace it with:

```python
    return _block("VALID", "host resource counters read", data)


# ---------------------------------------------------------------------------
# rac block — the operator's switch for remote access
# ---------------------------------------------------------------------------

def build_rac_raw(switch_present, run_switch_status, read_state):
    """Ask the switch script for the state, then read the file it leaves.

    ``None`` when this host carries no switch script: the block is then not
    claimed.  The script exits 0 for on or off and 3 for "wanted on, not
    up"; all three are answers.  Any other exit raises, and so does a
    state file that names no state, so that neither can read as healthy.
    """
    if not switch_present():
        return None
    res = run_switch_status()
    if res.rc not in (0, 3):
        raise RuntimeError(
            f"sigmond-rac-switch status exited {res.rc}: "
            f"{(res.stderr or '').strip()[:200]}")
    raw = {}
    for line in (read_state() or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            raw[key.strip()] = value.strip()
    if "state" not in raw:
        raise RuntimeError(f"{RAC_STATE_FILE} names no state")
    return raw


def _as_count(value) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _map_rac(raw):
    """off by the operator is a decision, and reads VALID.  Wanted on and
    not up is the fault, and reads INVALID.  ``None`` passes through: not
    claimed (see assemble)."""
    if raw is None:
        return None
    state = raw.get("state")
    up = _as_count(raw.get("channels_up"))
    declared = _as_count(raw.get("channels_declared"))
    registered = raw.get("registered") == "yes"
    running = raw.get("tunnel") == "running"
    data = {
        "wish": raw.get("wish"),
        "wish_source": raw.get("wish_source"),
        "registered": registered,
        "tunnel": raw.get("tunnel"),
        "channels_up": up,
        "channels_declared": declared,
        "since": raw.get("changed_at") or None,
        "by": raw.get("changed_by") or None,
    }
    since = f" since {data['since']}" if data["since"] else ""
    if state == "off":
        if running:
            return _block(
                "INCONCLUSIVE",
                f"off by the operator{since}, yet the tunnel service runs",
                data)
        if not registered and raw.get("wish_source") == "units":
            return _block(
                "VALID", "off: this station never registered for remote access",
                data)
        return _block("VALID", f"off by the operator{since}", data)
    if state == "on":
        if declared and up >= declared:
            return _block("VALID", f"on: {up} of {declared} channels", data)
        return _block(
            "INCONCLUSIVE", f"on, short of channels: {up} of {declared}", data)
    if state == "down":
        if not registered:
            why = "not registered yet (the host retries)"
        elif not running:
            why = "the tunnel service does not run"
        else:
            why = f"the gateway accepted none of {declared} channels"
        return _block("INVALID", f"wanted on, not up: {why}", data)
    return _block(
        "INDETERMINATE", f"the switch reported no usable state ({state!r})",
        data)


_MAPPERS = {
```

(f) Find (lines 499-500):

```python
    "resources": _map_resources,
}
```

and replace it with:

```python
    "resources": _map_resources,
    "rac": _map_rac,
}
```

(g) Find (lines 593-596):

```python
    return None


def build_production_checks(config: dict) -> dict:
```

and replace it with:

```python
    return None


def _rac_switch_present() -> bool:
    return os.access(RAC_SWITCH, os.X_OK)


def _run_rac_switch_status() -> RunResult:
    proc = subprocess.run([RAC_SWITCH, "status"], capture_output=True,
                          text=True, timeout=30, check=False)
    return RunResult(proc.returncode, proc.stdout, proc.stderr)


def _read_rac_state() -> str:
    with open(RAC_STATE_FILE, "r", encoding="utf-8") as fh:
        return fh.read()


def build_production_checks(config: dict) -> dict:
```

(h) Find (lines 608-609):

```python
            _read_temperature),
    }
```

and replace it with:

```python
            _read_temperature),
        "rac": lambda: build_rac_raw(
            _rac_switch_present, _run_rac_switch_status, _read_rac_state),
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pm_heartbeat.py tests/test_heartbeat.py -q`
Expected: `216 passed` (two more than Step 2 counted: two older tests run once per block of the host, and the host
now has four).

Run: `.venv/bin/pytest tests/test_fleetboard.py tests/test_heartbeat_cli.py -q`
Expected: no failures. The board still walks the station's seven blocks; Task 8 changes that.

Run: `.venv/bin/pytest -q`
Expected: `3708 passed, 1 warning, 65 subtests passed` (3684 after Task 6, and 24 new).

- [ ] **Step 6: Watch four guards fail, then restore**

```bash
cd /home/mjh/hamsci/repos/sigmond
F=scripts/proxmox/pm-heartbeat.py
cp $F $F.keep
mut(){ cp $F.keep $F; python3 - "$F" "$1" "$2" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
assert s.count(sys.argv[2]) == 1, "the mutation's anchor must occur exactly once"
p.write_text(s.replace(sys.argv[2], sys.argv[3]))
PY
rm -rf "$(dirname $F)/__pycache__"      # same size, same second: a stale .pyc would hide the mutation
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/test_pm_heartbeat.py -q 2>&1 | grep -E '^FAILED|passed|failed' | cut -c1-160; }

# 1. an operator's off reads as a fault
mut '        return _block("VALID", f"off by the operator{since}", data)' \
    '        return _block("INVALID", f"off by the operator{since}", data)'
# -> 2 failed, 66 passed: test_rac_block_verdicts, test_an_operators_off_never_worsens_the_hosts_rollup

# 2. a host without the switch script reports a block it cannot fill
mut '    blocks = {name: block for name, block in blocks.items()
              if block is not None}' \
    '    blocks = {name: (block or _block("INDETERMINATE", "none")) for name, block in blocks.items()}'
# -> 1 failed, 67 passed: test_a_host_without_the_switch_script_claims_no_rac_block

# 3. the switch's exit 3 no longer counts as an answer
mut '    if res.rc not in (0, 3):' \
    '    if res.rc != 0:'
# -> 1 failed, 67 passed: test_build_rac_raw_reads_the_file_the_switch_leaves

# 4. a tunnel short of channels reads healthy
mut '        if declared and up >= declared:' \
    '        if up >= 1:'
# -> 1 failed, 67 passed: test_rac_block_verdicts

mv $F.keep $F
rm -rf "$(dirname $F)/__pycache__"
.venv/bin/pytest tests/test_pm_heartbeat.py -q | tail -1      # 68 passed
```

- [ ] **Step 7: Commit**

```bash
cd /home/mjh/hamsci/repos/sigmond
test "$(grep -c . .git/ATTRIBUTION)" = 2 || { echo "write .git/ATTRIBUTION first (Global Constraints)"; exit 1; }
git add lib/sigmond/heartbeat_schema.py scripts/proxmox/pm-heartbeat.py tests/test_pm_heartbeat.py tests/test_heartbeat.py
git commit -m "heartbeat: the host reports the remote-access switch in a rac block

The schema gains a host-only block set (rac), so no decoder VM reports a
block it cannot see; validate() accepts either set.  pm-heartbeat asks
the switch script for its status and maps the answer: an operator's off
reads VALID and never worsens the rollup, a tunnel short of channels
reads INCONCLUSIVE, and wanted on and not up reads INVALID.  A host that
carries no switch script claims no rac block.

The server's copy of the schema must reach wd30 before any host sends
the block: an unknown block name quarantines the whole heartbeat.

tasks/plan-remote-access.md section 4.

$(cat .git/ATTRIBUTION)"
```

### Task 8: the fleet board shows "off by the operator" as a decision

Spec section 4: "The fleet board then shows 'off by the operator' as a decision, never as a station to chase."

The board's code lives in this repo (`server/heartbeat/fleetboard.py`) and runs on wd30. It draws one column per
block and one short label per verdict: `ok`, `FAIL`, `unsure`, `unknown`, `n/a`. It drops each block's `data` on
purpose and shows verdict and reason alone.

Task 7 reports an operator's off as a VALID `rac` block. Painted `ok`, it would hide that nobody can reach the
station through the gateway. Painted `FAIL`, it would send someone to chase a station whose owner chose this. So
the board gains one label, `off`, with a colour of its own, and a tag on the row that needs no hover:
`remote access off by the operator`. Only a VALID `rac` block whose data says `wish = off` wears the label. That
makes the one place where the board reads a block's data.

The board also starts to walk `ALL_BLOCK_NAMES`, so a `rac` column appears. A station's own row shows `n/a` there:
only a Proxmox host can claim the block.

Nothing here changes the board's keystone. A host that falls silent still turns INVALID by its silence, whatever
its last `rac` block said.

**Files:**
- Modify: `sigmond/server/heartbeat/fleetboard.py` — `_unknown_blocks` (line 79); `OPERATOR_OFF` after
  `NOT_CLAIMED` (line 90); `_blocks_from_payload` and `_wish_of` (lines 113-131); `derive_status` (lines 171-200);
  the CSS (line 322); `_verdict_cell` (line 377); `render_html` (lines 409-445)
- Modify: `sigmond/tests/test_fleetboard.py`

**Interfaces:**
- Consumes: Task 7's `heartbeat_schema.ALL_BLOCK_NAMES`, and the `rac` block's `data["wish"]`.
- Produces, in `fleetboard.py`: `OPERATOR_OFF = "off"`; `_wish_of(block) -> str | None`; each status row's
  `remote_access_off` (bool); `blocks["rac"]["label"] == "off"` on such a row; the CSS class `v-OFF`; the row tag
  `remote access off by the operator`. `server/heartbeat/deploy-wd30.sh` already carries `fleetboard.py`,
  `ingest.py` and `heartbeat_schema.py` to wd30 together; Task 12 runs it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_fleetboard.py`:

(a) Find (lines 486-487):

```python
    # mistaken for health.
    assert set(d2["blocks"]) == set(heartbeat_schema.BLOCK_NAMES)
```

and replace it with:

```python
    # mistaken for health.
    assert set(d2["blocks"]) == set(heartbeat_schema.ALL_BLOCK_NAMES)
```

(b) Find (lines 650-652):

```python
    """Regression pin: an ordinary station's full 7-block envelope must
    never pick up the n/a class — n/a is only for a genuinely absent
    block."""
```

and replace it with:

```python
    """Regression pin: none of an ordinary station's seven blocks may pick
    up the n/a class — n/a is only for a genuinely absent block.  `rac`
    is such a block on a station row: only a Proxmox host can claim it."""
```

(c) Find (lines 662-664):

```python
    assert set(b4["blocks"]) == set(heartbeat_schema.BLOCK_NAMES)
    assert all(b["verdict"] != fleetboard.NOT_CLAIMED
              for b in b4["blocks"].values())
```

and replace it with:

```python
    assert set(b4["blocks"]) == set(heartbeat_schema.ALL_BLOCK_NAMES)
    assert all(b4["blocks"][n]["verdict"] != fleetboard.NOT_CLAIMED
              for n in heartbeat_schema.BLOCK_NAMES)
    assert b4["blocks"]["rac"]["verdict"] == fleetboard.NOT_CLAIMED
    assert b4["remote_access_off"] is False
```

(d) Find (lines 747-750):

```python
    assert "fine" not in pm["top"]["reason"]


def test_colspan_is_computed_not_hardcoded():
```

and replace it with:

```python
    assert "fine" not in pm["top"]["reason"]


# ---------------------------------------------------------------------------
# 2c. remote access — "off by the operator" is a decision, never a fault
#     (sigmond tasks/plan-remote-access.md section 4)
# ---------------------------------------------------------------------------

RAC_OFF = {"verdict": "VALID", "reason": "off by the operator since 2026-10-09T18:22:05Z",
           "data": {"wish": "off", "tunnel": "stopped", "channels_up": 0,
                    "channels_declared": 6, "since": "2026-10-09T18:22:05Z", "by": "vm"}}
RAC_ON = {"verdict": "VALID", "reason": "on: 6 of 6 channels",
          "data": {"wish": "on", "tunnel": "running", "channels_up": 6, "channels_declared": 6}}
RAC_DOWN = {"verdict": "INVALID", "reason": "wanted on, not up: the tunnel service does not run",
            "data": {"wish": "on", "tunnel": "stopped", "channels_up": 0, "channels_declared": 6}}


def pm_with_rac(rac, station="AC0G-B4", rollup="VALID", reason="versions: host versions read"):
    env = make_pm_envelope(station=station, rollup=rollup, reason=reason)
    env["blocks"]["rac"] = rac
    assert heartbeat_schema.validate(env) == []
    return env


def pm_with_rac_unchecked(rac):
    """The same envelope with no validate() call: for a schema that predates `rac`."""
    env = make_pm_envelope(station="AC0G-B4-PM")
    env["blocks"]["rac"] = rac
    return env


def _row(db_path, envelope, age=10):
    seed(db_path, "AC0G-B4", NOW - age, envelope)
    conn = ingest.open_db(str(db_path))
    try:
        statuses = fleetboard.derive_status(conn, ROSTER, NOW)
    finally:
        conn.close()
    row = {s["station"]: s for s in statuses}["AC0G-B4"]
    html = fleetboard.render_html([row], [], {"count": 0, "window_s": 86400, "recent": []}, NOW)
    return row, html


def test_remote_access_off_by_the_operator_shows_as_a_decision(db_path):
    row, html = _row(db_path, pm_with_rac(RAC_OFF))
    assert row["top"]["verdict"] == "VALID"              # nothing to chase
    assert row["blocks"]["rac"]["label"] == fleetboard.OPERATOR_OFF
    assert row["remote_access_off"] is True
    assert ('<td class="verdict v-OFF" title="off by the operator since '
            '2026-10-09T18:22:05Z">off</td>') in html
    assert "remote access off by the operator" in html   # the row's tag: no hover needed
    assert html.count('class="verdict v-INVALID"') == 0


def test_a_tunnel_that_runs_reads_ok_and_carries_no_off_label(db_path):
    row, html = _row(db_path, pm_with_rac(RAC_ON))
    assert "label" not in row["blocks"]["rac"]
    assert row["remote_access_off"] is False
    assert '<td class="verdict v-VALID" title="on: 6 of 6 channels">ok</td>' in html
    assert "remote access off" not in html
    assert 'class="verdict v-OFF"' not in html           # the CSS defines it; no cell uses it


def test_wanted_on_and_not_up_stays_a_fault(db_path):
    env = pm_with_rac(RAC_DOWN, rollup="INVALID",
                      reason="rac: wanted on, not up: the tunnel service does not run")
    row, html = _row(db_path, env)
    assert row["top"]["verdict"] == "INVALID"
    assert row["remote_access_off"] is False
    assert ('<td class="verdict v-INVALID" title="wanted on, not up: the tunnel '
            'service does not run">FAIL</td>') in html


@pytest.mark.parametrize("rac", [
    dict(RAC_OFF, verdict="INVALID"),                    # a fault never wears the label
    dict(RAC_OFF, verdict="INCONCLUSIVE"),               # "off, yet the tunnel service runs"
    {"verdict": "VALID", "reason": "x"},                 # no data at all
    {"verdict": "VALID", "reason": "x", "data": "off"},
    {"verdict": "VALID", "reason": "x", "data": {"wish": ["off"]}},
    {"verdict": "VALID", "reason": "x", "data": {"wish": "OFF"}},
], ids=["invalid", "inconclusive", "no-data", "data-not-a-dict", "wish-not-a-string", "wish-misspelt"])
def test_only_a_valid_block_whose_wish_reads_off_wears_the_label(db_path, rac):
    row, html = _row(db_path, pm_with_rac(rac))
    assert "label" not in row["blocks"]["rac"]
    assert row["remote_access_off"] is False
    assert 'class="verdict v-OFF"' not in html


def test_a_silent_host_stays_invalid_whatever_its_last_rac_block_said(db_path):
    row, html = _row(db_path, pm_with_rac(RAC_OFF), age=3 * 3600)
    assert row["availability"]["verdict"] == "INVALID"
    assert row["top"]["verdict"] == "INVALID" and "silent" in row["top"]["reason"]


def test_ingest_takes_a_host_envelope_that_carries_rac(drop_dir, db_path, monkeypatch):
    drop(drop_dir, "AC0G-B4-PM_20261009T190002Z.json", pm_with_rac(RAC_OFF, station="AC0G-B4-PM"))
    result = ingest.ingest_once(str(drop_dir), str(db_path))
    assert (result["ingested"], result["quarantined"]) == (1, 0)
    # Control: a server whose schema copy predates `rac` quarantines the very
    # same file, and a quarantined host looks silent.  So the server learns
    # the block before any host sends it (the release order says the same).
    monkeypatch.setattr(heartbeat_schema, "ALL_BLOCK_NAMES", heartbeat_schema.BLOCK_NAMES)
    drop(drop_dir, "AC0G-B4-PM_20261009T190502Z.json",
         dict(pm_with_rac_unchecked(RAC_OFF), emitted_at="2026-10-09T19:05:02Z"))
    result = ingest.ingest_once(str(drop_dir), str(db_path))
    assert (result["ingested"], result["quarantined"]) == (0, 1)


def test_the_board_draws_one_column_for_every_block_the_wire_knows():
    html = fleetboard.render_html(
        [], [], {"count": 0, "window_s": 86400, "recent": []}, 1.0)
    for name in heartbeat_schema.ALL_BLOCK_NAMES:
        assert f"<th>{name}</th>" in html
    assert html.index("<th>resources</th>") < html.index("<th>rac</th>")


def test_colspan_is_computed_not_hardcoded():
```

(e) Find (lines 754-755):

```python
    assert match, "the empty-roster row must declare a colspan"
    assert int(match.group(1)) == 6 + len(heartbeat_schema.BLOCK_NAMES)
```

and replace it with:

```python
    assert match, "the empty-roster row must declare a colspan"
    assert int(match.group(1)) == 6 + len(heartbeat_schema.ALL_BLOCK_NAMES)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_fleetboard.py -q`
Expected: `13 failed, 83 passed`.

- [ ] **Step 3: Implement in `server/heartbeat/fleetboard.py`**

(a) Find (lines 78-79):

```python
    return {name: _verdict("INDETERMINATE", reason)
            for name in heartbeat_schema.BLOCK_NAMES}
```

and replace it with:

```python
    return {name: _verdict("INDETERMINATE", reason)
            for name in heartbeat_schema.ALL_BLOCK_NAMES}
```

(b) Find (lines 90-93):

```python
NOT_CLAIMED = "N/A"


def _latest(conn, station):
```

and replace it with:

```python
NOT_CLAIMED = "N/A"

#: Board-only label for the one block state that is a decision and neither
#: health nor a fault: the station's operator turned remote access off
#: (sigmond tasks/plan-remote-access.md section 4).  The host reports it as a
#: VALID `rac` block whose data says wish = off.  Painted "ok" it would hide
#: that nobody can reach the station; painted "FAIL" it would send someone to
#: chase a station whose owner chose this.  So it gets a label and a colour
#: of its own, and a tag on the row.
OPERATOR_OFF = "off"


def _latest(conn, station):
```

(c) Find (lines 112-113):

```python
    out = {}
    for name in heartbeat_schema.BLOCK_NAMES:
```

and replace it with:

```python
    out = {}
    for name in heartbeat_schema.ALL_BLOCK_NAMES:
```

(d) Find (lines 130-131):

```python
        out[name] = _verdict(verdict, block.get("reason") or "")
    return out
```

and replace it with:

```python
        out[name] = _verdict(verdict, block.get("reason") or "")
        if name == "rac" and verdict == "VALID" and _wish_of(block) == "off":
            out[name]["label"] = OPERATOR_OFF
    return out


def _wish_of(block):
    """The operator's wish from a `rac` block's data, or None.  The one
    place the board reads a block's data: the verdict alone cannot tell a
    tunnel that runs from one its operator turned off."""
    data = block.get("data")
    wish = data.get("wish") if isinstance(data, dict) else None
    return wish if isinstance(wish, str) else None
```

(e) Find (lines 172-173):

```python
                          if heartbeat_station != station else None),
        }
```

and replace it with:

```python
                          if heartbeat_station != station else None),
            # True when the newest heartbeat says the operator turned
            # remote access off.  A decision: it tags the row and never
            # changes a verdict.
            "remote_access_off": False,
        }
```

(f) Find (lines 200-201):

```python
            status["blocks"] = _blocks_from_payload(payload)
            envelope_top = _envelope_rollup(payload, rollup_verdict)
```

and replace it with:

```python
            status["blocks"] = _blocks_from_payload(payload)
            status["remote_access_off"] = (
                status["blocks"]["rac"].get("label") == OPERATOR_OFF)
            envelope_top = _envelope_rollup(payload, rollup_verdict)
```

(g) Find (lines 322-323):

```python
.v-NA            { background: #fafafa; color: #888; font-style: italic; }
footer { color: #555; font-size: .85rem; border-top: 1px solid #ccc;
```

and replace it with:

```python
.v-NA            { background: #fafafa; color: #888; font-style: italic; }
.v-OFF           { background: #dbe7f5; }
footer { color: #555; font-size: .85rem; border-top: 1px solid #ccc;
```

(h) Find (lines 377-378):

```python
    css = _CSS_SUFFIX.get(verdict, verdict)
    if full:
```

and replace it with:

```python
    css = _CSS_SUFFIX.get(verdict, verdict)
    if not full and isinstance(block, dict) and block.get("label") == OPERATOR_OFF:
        return (f'<td class="verdict v-OFF" title="{_e(reason)}">'
                f'{_e(OPERATOR_OFF)}</td>')
    if full:
```

(i) Find (lines 408-409):

```python
               "<th>silent for</th><th>last arrival</th><th>emitted at</th>")
    for name in heartbeat_schema.BLOCK_NAMES:
```

and replace it with:

```python
               "<th>silent for</th><th>last arrival</th><th>emitted at</th>")
    for name in heartbeat_schema.ALL_BLOCK_NAMES:
```

(j) Find (lines 420-421):

```python
            tags.append(f"frozen: {status['frozen']}")
        reports_as = status.get("reports_as")
```

and replace it with:

```python
            tags.append(f"frozen: {status['frozen']}")
        if status.get("remote_access_off"):
            tags.append("remote access off by the operator")
        reports_as = status.get("reports_as")
```

(k) Find (lines 437-438):

```python
        blocks = status.get("blocks") or {}
        for name in heartbeat_schema.BLOCK_NAMES:
```

and replace it with:

```python
        blocks = status.get("blocks") or {}
        for name in heartbeat_schema.ALL_BLOCK_NAMES:
```

(l) Find (lines 444-445):

```python
        # with the <thead> above rather than hand-counted.
        colspan = 6 + len(heartbeat_schema.BLOCK_NAMES)
```

and replace it with:

```python
        # with the <thead> above rather than hand-counted.
        colspan = 6 + len(heartbeat_schema.ALL_BLOCK_NAMES)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_fleetboard.py -q`
Expected: `96 passed`.

Run: `.venv/bin/pytest -q`
Expected: `3720 passed, 1 warning, 65 subtests passed` (3708 after Task 7, and 12 new).

- [ ] **Step 5: Watch three guards fail, then restore**

```bash
cd /home/mjh/hamsci/repos/sigmond
F=server/heartbeat/fleetboard.py
cp $F $F.keep
mut(){ cp $F.keep $F; python3 - "$F" "$1" "$2" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
assert s.count(sys.argv[2]) == 1, "the mutation's anchor must occur exactly once"
p.write_text(s.replace(sys.argv[2], sys.argv[3]))
PY
rm -rf "$(dirname $F)/__pycache__"      # same size, same second: a stale .pyc would hide the mutation
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/test_fleetboard.py -q 2>&1 | grep -E '^FAILED|passed|failed' | cut -c1-160; }

# 1. the label rides on a fault too
mut 'if name == "rac" and verdict == "VALID" and _wish_of(block) == "off":' \
    'if name == "rac" and _wish_of(block) == "off":'
# -> 2 failed, 94 passed: test_only_a_valid_block_whose_wish_reads_off_wears_the_label

# 2. an operator's off gets painted as plain ok
mut '    if not full and isinstance(block, dict) and block.get("label") == OPERATOR_OFF:' \
    '    if False:'
# -> 1 failed, 95 passed: test_remote_access_off_by_the_operator_shows_as_a_decision

# 3. the row carries no tag
mut '            tags.append("remote access off by the operator")' \
    '            pass'
# -> 1 failed, 95 passed: test_remote_access_off_by_the_operator_shows_as_a_decision

mv $F.keep $F
rm -rf "$(dirname $F)/__pycache__"
.venv/bin/pytest tests/test_fleetboard.py -q | tail -1      # 96 passed
```

- [ ] **Step 6: Commit**

```bash
cd /home/mjh/hamsci/repos/sigmond
test "$(grep -c . .git/ATTRIBUTION)" = 2 || { echo "write .git/ATTRIBUTION first (Global Constraints)"; exit 1; }
git add server/heartbeat/fleetboard.py tests/test_fleetboard.py
git commit -m "fleetboard: show remote access off by the operator as a decision

The board draws a rac column.  A host whose operator turned remote
access off gets a label of its own there, off, in its own colour, and a
tag on its row.  It reads neither ok nor FAIL: nobody can reach that
station through the gateway, and nobody should chase it.  Wanted on and
not up stays a fault, and a silent host still turns INVALID by its
silence.

tasks/plan-remote-access.md section 4.

$(cat .git/ATTRIBUTION)"
```

### Task 9: the console panel shows three states (sigmond-appliance)

Spec section 4: "Today it prints 'OFFLINE — check: journalctl -u sigmond-rac-host' whenever the unit does not run,
so a deliberate off reads as a fault. Three states instead: on, with its channels; off by the operator, since
when, with the command that turns it on; and wanted but not up, which stays the fault."

The panel is the script `/usr/local/sbin/sigmond-issue`, which `firstboot-v3.sh` writes from a quoted heredoc
(`<<'ISSEOF'`, line 2519). Its remote-access block spans lines 2773-2900. A station already in the field receives
the panel through `pm-align`, which cuts that heredoc out of the release's `firstboot-v3.sh`.

This task rewrites the block around the switch script. The panel runs `sigmond-rac-switch status`, bounded by a
timeout, and reads the state file the script leaves (Task 2). One script then holds the truth about the three
states for the panel, the heartbeat and the VM alike.

Three things the block must keep doing:

- **The host has no keyboard.** Every line gets read off a monitor and typed on another computer. So the panel
  prints the whole reach: `log in to the decoder VM (ssh -p 2222 sigmond@<host address>) and type:  smd remote on`.
- **The channel lists and the audience split stay**, byte for byte, when the tunnel stands on. A tunnel that
  stands off lists no channel: nobody can reach one.
- **A host without the switch script keeps the old text**, unchanged (Review Focus 5). An older pm-align delivers
  this panel before anything delivers the switch.

The block gains two markers, `# >>> rac_block` and `# <<< rac_block`, so that a test can cut it out and run what
ships. It reads four paths through variables with defaults, so that the test can point them at a scratch
directory; under systemd the defaults apply.

**Files:**
- Modify: `sigmond-appliance/firstboot-v3.sh` — the panel's remote-access block (lines 2773-2900)
- Create: `sigmond-appliance/test-panel-remote.sh`

**Interfaces:**
- Consumes: Task 2's `/usr/local/sbin/sigmond-rac-switch status` and these keys of `/run/sigmond/rac-state`:
  `state`, `registered`, `tunnel`, `channels_up`, `channels_declared`, `changed_at`. The marks `rac-number`,
  `rac-tier`, `rac-registrar`. `/etc/sigmond/frpc-host.toml`. The panel's own `$HOSTIP` and `ipurl`.
- Produces: `$RACBLOCK`, as before. Its first line reads one of:
  - ` Remote access   ON — RAC <n> on <gateway>  (<tier>)`
  - ` Remote access   OFF, by the operator's choice (since <UTC time>) — not a fault`
  - ` Remote access   off — this station has not registered for it`
  - ` Remote access   WANTED ON, BUT NOT UP — …`
  - ` Remote access   WANTED ON, NOT REGISTERED YET — no gateway has answered`
  - or the three texts of v3.70 on a host without the switch script.
  Task 10's rig greps `OFF, by the operator's choice` in `/etc/issue`.
- The four heredoc lines that pm-align needs from `firstboot-v3.sh` stay; the new test pins them.

- [ ] **Step 0: Take the baseline, and set up this repo's attribution file**

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance
git log -1 --format='%h %s'
for t in test-unit-quoting.sh test-console-storm.sh test-wifi-boot.sh test-vm-console.sh test-update-smd-lines.sh test-phase-d-verdict.sh test-bless-instructions.sh; do
    printf '%-28s ' "$t"; bash "$t" 2>&1 | tail -1
done
grep -c . .git/ATTRIBUTION 2>/dev/null || echo "write .git/ATTRIBUTION first (Global Constraints)"
```

Expected at a0d7268, in order: `PASS: 11 unit(s) parse cleanly`; `test-console-storm.sh: 11 passed, 0 failed`;
`108 passed, 0 failed`; `15 passed, 0 failed`; `PASS: held_names, restore_moved, PANEL_DEFECT_RE`;
`PASS: phase_d_verdict`; `195 passed, 0 failed`; then `2`.

- [ ] **Step 1: Write the failing test**

Create `test-panel-remote.sh` and make it executable (`chmod +x test-panel-remote.sh`):

```bash
#!/bin/bash
# test-panel-remote.sh — the console panel's remote-access block: three
# states, not two.  No VM, no root; about a second.
#
# Why: until v3.70 the panel printed "OFFLINE — check: journalctl -u
# sigmond-rac-host" whenever the tunnel's unit did not run.  An operator who
# had turned remote access off on purpose read a fault on the console, and so
# did whoever came to chase it (sigmond tasks/plan-remote-access.md section 4).
# From v3.71 the panel asks the switch script, which tells a decision from a
# fault:
#     on     the tunnel runs; the channels follow
#     off    the operator holds it off; say since when, and how to turn it on
#     down   wanted on and not up; this one stays the fault
#
# The block under test is cut out of firstboot-v3.sh from between its two
# markers, so this runs what ships.  A stub stands in for the switch script
# and writes the state file the real one leaves.
#
#   usage: ./test-panel-remote.sh [path/to/firstboot-v3.sh]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:-$HERE/firstboot-v3.sh}"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok(){  printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad(){ printf '  FAIL  %s\n        %s\n' "$1" "${2:-}"; fail=$((fail+1)); }
has(){   case "$2" in *"$3"*) ok "$1" ;; *) bad "$1" "missing: $3" ;; esac; }
lacks(){ case "$2" in *"$3"*) bad "$1" "found: $3" ;; *) ok "$1" ;; esac; }

sed -n '/^# >>> rac_block/,/^# <<< rac_block/p' "$SRC" > "$T/block.sh"
[ -s "$T/block.sh" ] || { echo "FAIL: no rac_block between its markers in $SRC"; exit 1; }
bash -n "$T/block.sh" || { echo "FAIL: the rac_block does not parse"; exit 1; }

cat > "$T/frpc-host.toml" <<'TOML'
serverAddr = "gw2.wsprdaemon.org"
user = "0123456789abcdef"
[[proxies]]
name = "AC0G_B4-vm-ssh"
remotePort = 36307
[[proxies]]
name = "AC0G_B4-vm-web"
remotePort = 46307
[[proxies]]
name = "AC0G_B4-vm-station"
remotePort = 49307
[[proxies]]
name = "AC0G_B4-vm-gmag"
remotePort = 50307
[[proxies]]
name = "AC0G_B4-host-ssh"
remotePort = 51307
[[proxies]]
name = "AC0G_B4-host-ui"
remotePort = 56307
TOML

# panel <scene dir> : run the block as sigmond-issue would, print $RACBLOCK.
# A scene holds: marks/ (rac-number, rac-tier, ...), state (what the switch
# writes; absent = the switch leaves none), no-switch (this host has no
# switch script), no-toml, unit-active (for the text of v3.70 and before).
# $BLOCK names the block to run; the control below points it at a mutant.
BLOCK="$T/block.sh"
panel(){
    local d="$1"
    (
        export RAC_MARKS="$d/marks" RAC_STATE_FILE="$d/run/rac-state" RAC_TOML="$d/frpc-host.toml"
        export RAC_SWITCH="$d/switch"
        mkdir -p "$d/marks" "$d/run"
        [ -e "$d/no-toml" ] || cp "$T/frpc-host.toml" "$d/frpc-host.toml"
        if [ ! -e "$d/no-switch" ]; then
            { echo '#!/bin/bash'
              echo "echo \"\$*\" >> '$d/switch.calls'"
              echo "[ -e '$d/state' ] && cp '$d/state' '$d/run/rac-state'"
              echo 'exit 0'; } > "$d/switch"
            chmod 755 "$d/switch"
        fi
        HOSTIP=192.168.1.50
        ipurl(){ printf '%s\n' "${1:-}"; }
        systemctl(){ [ "$1" = is-active ] && [ -e "$d/unit-active" ]; }
        curl(){ printf '{"tcp":[{"status":"running"},{"status":"running"},{"status":"running"},{"status":"running"},{"status":"running"},{"status":"running"}]}\n'; }
        timeout(){ shift; "$@"; }
        # shellcheck disable=SC1090
        . "$BLOCK"
        printf '%s\n' "$RACBLOCK"
    )
}
scene(){   # scene <name> <rac-number|-> [state lines...]
    local d="$T/$1" n="$2"; shift 2
    mkdir -p "$d/marks"
    [ "$n" = - ] || { echo "$n" > "$d/marks/rac-number"; echo "wsprdaemon (secure)" > "$d/marks/rac-tier"; }
    [ $# -gt 0 ] && printf '%s\n' "$@" > "$d/state"
    echo "$d"
}

echo "── on: the tunnel runs, and the panel says how to turn it off ─────"
d=$(scene on 507 state=on registered=yes tunnel=running channels_up=6 channels_declared=6 changed_at=2026-10-09T18:22:05Z)
out=$(panel "$d")
has   "it reads ON with the RAC number and gateway" "$out" "Remote access   ON — RAC 507 on gw2.wsprdaemon.org  (wsprdaemon (secure))"
has   "it counts the channels the switch counted"   "$out" "status   6/6 channels up"
has   "it names the command, and where to type it"  "$out" "log in to the decoder VM (ssh -p 2222 sigmond@192.168.1.50) and type:  smd remote off"
has   "the admin channels keep their whole command" "$out" "ssh -p 51307 root@gw2.wsprdaemon.org"
has   "the audience split survives"                 "$out" "ADMINISTRATORS ONLY"
has   "all four VM channels are listed"             "$out" "magnetometer :50307"
lacks "it prints no fault word"                     "$out" "OFFLINE"
[ "$(cat "$d/switch.calls")" = status ] && ok "the panel asks the switch for status, once, and nothing else" \
    || bad "the panel asks the switch for status, once, and nothing else" "$(cat "$d/switch.calls" 2>/dev/null)"

d=$(scene on-partial 507 state=on registered=yes tunnel=running channels_up=4 channels_declared=6)
has   "a partial tunnel shows its real count"       "$(panel "$d")" "status   4/6 channels up"

echo "── off: a decision, never a fault ─────────────────────────────────"
d=$(scene off 507 state=off registered=yes tunnel=stopped channels_up=0 channels_declared=6 changed_at=2026-10-09T18:22:05Z changed_by=vm)
out=$(panel "$d")
has   "it says OFF, by the operator's choice"       "$out" "Remote access   OFF, by the operator's choice (since 2026-10-09T18:22:05Z) — not a fault"
has   "it says the registration stays"              "$out" "The registration stays (RAC 507 on gw2.wsprdaemon.org)."
has   "it names the command that turns it on"       "$out" "and type:  smd remote on"
lacks "it prints no fault word"                     "$out" "OFFLINE"
lacks "it sends nobody to the journal"              "$out" "journalctl"
lacks "it lists no channel nobody can reach"        "$out" "ADMINISTRATORS ONLY"

d=$(scene off-never - state=off registered=no tunnel=stopped wish_source=units)
touch "$d/no-toml"
out=$(panel "$d")
has   "a station that never registered says so"     "$out" "Remote access   off — this station has not registered for it"
has   "and still names the command"                 "$out" "and type:  smd remote on"
lacks "it does not send the reader to the wizard"   "$out" "sigmond-setup --reconfigure"

echo "── down: wanted on and not up stays the fault ─────────────────────"
d=$(scene down-stopped 507 state=down registered=yes tunnel=stopped channels_up=0 channels_declared=6)
out=$(panel "$d")
has   "a stopped tunnel reads WANTED ON, BUT NOT UP" "$out" "WANTED ON, BUT NOT UP — the tunnel service does not run (RAC 507)"
has   "it gives a whole command for another computer" "$out" "ssh root@192.168.1.50 'journalctl -u sigmond-rac-host -n 50'"

d=$(scene down-running 507 state=down registered=yes tunnel=running channels_up=0 channels_declared=6)
has   "a running tunnel with no channel says 0/6"   "$(panel "$d")" "WANTED ON, BUT NOT UP — RAC 507 on gw2.wsprdaemon.org: 0/6 channels accepted"

d=$(scene down-unregistered - state=down registered=no tunnel=stopped)
touch "$d/no-toml"
out=$(panel "$d")
has   "an unregistered station says so"             "$out" "WANTED ON, NOT REGISTERED YET — no gateway has answered"
has   "and says the host keeps trying"              "$out" "tries again every 10 minutes"
lacks "it does not send the reader to the wizard"   "$out" "sigmond-setup --reconfigure"

echo "── a host without the switch script keeps the v3.70 text ──────────"
d=$(scene legacy-up 507); touch "$d/no-switch" "$d/unit-active"
out=$(panel "$d")
has   "a running tunnel reads online, as before"    "$out" "status   online — 6/6 channels up"
d=$(scene legacy-down 507); touch "$d/no-switch"
has   "a stopped one reads OFFLINE, as before"      "$(panel "$d")" "status   OFFLINE — check: journalctl -u sigmond-rac-host -n 50"
d=$(scene legacy-none -); touch "$d/no-switch" "$d/no-toml"
has   "an unconfigured one reads LAN-only, as before" "$(panel "$d")" "not configured — this station is LAN-only"
d=$(scene switch-silent 507)          # a switch script that leaves no state file
has   "a switch that leaves no state falls back to the old text" "$(panel "$d")" "status   OFFLINE"

echo "── positive control ───────────────────────────────────────────────"
# The fixture must reproduce the defect, or the 'ok' lines above prove
# nothing about it.  This is the v3.70 panel's own expression: fed the very
# scene of a deliberate off, it must print the fault text.
old_rstat(){
    local d="$1" RSTAT
    systemctl(){ [ "$1" = is-active ] && [ -e "$d/unit-active" ]; }
    RSTAT="OFFLINE — check: journalctl -u sigmond-rac-host -n 50"
    if systemctl is-active --quiet sigmond-rac-host 2>/dev/null; then RSTAT="online"; fi
    printf '%s\n' "$RSTAT"
}
case "$(old_rstat "$T/off")" in
    OFFLINE*) ok "control: the v3.70 expression calls a deliberate off OFFLINE" ;;
    *) bad "control: the v3.70 expression calls a deliberate off OFFLINE" "this fixture does not reproduce the defect" ;;
esac
# And the checks above must be able to fail: against a block that ignores the
# switch, the off scene's first check must not hold.
sed 's/^case "\$RSTATE" in$/case "" in/' "$T/block.sh" > "$T/block.mut"
if cmp -s "$T/block.sh" "$T/block.mut"; then
    bad "control: the mutation applies" "the block no longer branches on \$RSTATE in the form this control edits"
else
    out=$(BLOCK="$T/block.mut" panel "$T/off")
    case "$out" in
        *"by the operator's choice"*) bad "control: a block that ignores the switch fails the off check" "it still passed" ;;
        *OFFLINE*) ok "control: a block that ignores the switch prints OFFLINE for a deliberate off" ;;
        *) bad "control: a block that ignores the switch fails the off check" "unexpected: $out" ;;
    esac
fi

echo "── what pm-align still needs from this file ───────────────────────"
# A station's pm-align cuts these four files out of firstboot-v3.sh by their
# heredoc lines (sigmond scripts/proxmox/pm-align.py FIRSTBOOT_FILES).  It
# refuses a release where one has gone.
for start in "cat > /usr/local/lib/sigmond-net.sh <<'NETLIBEOF'" \
             "cat > /usr/local/sbin/sigmond-issue <<'ISSEOF'" \
             "cat > /etc/systemd/system/sigmond-issue.service <<'ISVCEOF'" \
             "cat > /etc/systemd/system/sigmond-issue.timer <<'ITEOF'"; do
    if [ "$(grep -cxF "$start" "$SRC")" = 1 ]; then ok "firstboot still writes: ${start%% <<*}"
    else bad "firstboot still writes: ${start%% <<*}" "pm-align on every station would refuse this release"; fi
done
# The block sits inside the panel script, where pm-align picks it up.
awk '/^cat > \/usr\/local\/sbin\/sigmond-issue <<.ISSEOF.$/{f=1;next} /^ISSEOF$/{f=0} f' "$SRC" > "$T/issue"
grep -q '^# >>> rac_block' "$T/issue" && ok "the block ships inside sigmond-issue" \
    || bad "the block ships inside sigmond-issue" "the markers stand outside the ISSEOF heredoc"

echo
echo "test-panel-remote.sh: $pass passed, $fail failed"
[ "$fail" = 0 ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash test-panel-remote.sh; echo "rc=$?"`
Expected: `FAIL: no rac_block between its markers in /home/mjh/hamsci/repos/sigmond-appliance/firstboot-v3.sh`, then
`rc=1`.

- [ ] **Step 3: Rewrite the block in `firstboot-v3.sh`**

The lines from `# ── every declared channel, grouped by WHO CAN REACH IT` through `RACEOF` keep their bytes, but
for one path that becomes `"$RAC_TOML"`. The steps below change what stands around them.

(a) Find (lines 2771-2773):

```bash
esac

# Remote access (RAC).  The panel used to show LAN addresses only, so an
```

and replace it with:

```bash
esac

# >>> rac_block (test-panel-remote.sh loads exactly this block)
# Remote access (RAC).  The panel used to show LAN addresses only, so an
```

(b) Find (lines 2782-2794):

```bash
RACBLOCK=""
RACN=$(cat /etc/sigmond-appliance/rac-number 2>/dev/null)
if [ -n "$RACN" ] && [ -r /etc/sigmond/frpc-host.toml ]; then
    RSRV=$(awk -F'"' '/^serverAddr/{print $2; exit}' /etc/sigmond/frpc-host.toml)
    # ⚠ This is the frp CLIENT IDENTITY (e.g. DASI-009), not an SSH account.
    # The panel used to print it as the ssh username on both RAC lines, so the
    # console offered `ssh -p 51029 DASI-009@vpn.hamsci.org` -- which cannot
    # work, on the one line an operator needs when the VM is unreachable.  It
    # is kept only for display/diagnostics; the logins are root (Proxmox host)
    # and hamsci (decoder VM), exactly as the local sections above say.
    RUSR=$(awk -F'"' '/^user *=/{print $2; exit}' /etc/sigmond/frpc-host.toml)
    RTIER=$(cat /etc/sigmond-appliance/rac-tier 2>/dev/null)
    RREG=$(cat /etc/sigmond-appliance/rac-registrar 2>/dev/null)
```

and replace it with:

```bash
#
# ⛔ THREE STATES, NOT TWO (sigmond tasks/plan-remote-access.md section 4).
# Until v3.70 this block printed "OFFLINE — check: journalctl -u
# sigmond-rac-host" whenever the unit did not run.  An operator who had
# turned remote access off on purpose then read a fault on the console, and
# so did whoever came to chase it.  The switch script knows the difference:
#     on     the tunnel runs; the channels follow
#     off    the operator holds it off; say since when, and how to turn it on
#     down   wanted on and not up; this one stays the fault
# So ask the switch, then read the file it leaves.  A host that carries no
# switch script yet (pm-align has not brought it) keeps the old two-state
# text below, unchanged.
RAC_MARKS="${RAC_MARKS:-/etc/sigmond-appliance}"
RAC_TOML="${RAC_TOML:-/etc/sigmond/frpc-host.toml}"
RAC_SWITCH="${RAC_SWITCH:-/usr/local/sbin/sigmond-rac-switch}"
RAC_STATE_FILE="${RAC_STATE_FILE:-/run/sigmond/rac-state}"
RACBLOCK=""
RACN=$(cat "$RAC_MARKS/rac-number" 2>/dev/null)
RSTATE=""; RREGD=""; RRUNS=""; RUP=""; RDECL=""; RSINCE=""
if [ -x "$RAC_SWITCH" ]; then
    # Bounded: a panel refresh must never hang on anything it asks.
    timeout 20 "$RAC_SWITCH" status >/dev/null 2>&1
    _rs(){ sed -n "s/^$1=//p" "$RAC_STATE_FILE" 2>/dev/null | head -1; }
    RSTATE=$(_rs state); RREGD=$(_rs registered); RRUNS=$(_rs tunnel)
    RUP=$(_rs channels_up); RDECL=$(_rs channels_declared); RSINCE=$(_rs changed_at)
fi
RSRV=""; RTIER=""; RREG=""; RAC_ADMIN=""; RAC_USER=""
if [ -r "$RAC_TOML" ]; then
    RSRV=$(awk -F'"' '/^serverAddr/{print $2; exit}' "$RAC_TOML")
    RTIER=$(cat "$RAC_MARKS/rac-tier" 2>/dev/null)
    RREG=$(cat "$RAC_MARKS/rac-registrar" 2>/dev/null)
```

(c) Find (lines 2861-2883):

```bash
         /etc/sigmond/frpc-host.toml)
RACEOF
    # Live state, not a claim: frpc publishes per-proxy status on its local
    # admin API, and that is the only thing that proves the gateway accepted
    # the channels.  Fall back to the unit state if the API is not up.
    RSTAT="OFFLINE — check: journalctl -u sigmond-rac-host -n 50"
    if systemctl is-active --quiet sigmond-rac-host 2>/dev/null; then
        _run=$(curl -s --max-time 3 http://127.0.0.1:7500/api/status 2>/dev/null \
                 | grep -o '"status":"running"' | wc -l | tr -d ' ')
        # Count the channels this station ACTUALLY declares, never a literal.
        # It was hardcoded to 4 and the station now ships 6, so a fully
        # healthy host advertised "6/4 channels up" (rob, 2026-09-21).
        _decl=$(grep -c '^name *=' /etc/sigmond/frpc-host.toml 2>/dev/null)
        [ "${_decl:-0}" -gt 0 ] || _decl=$_run
        if [ "${_run:-0}" -gt 0 ]; then RSTAT="online — $_run/$_decl channels up"
        else RSTAT="service running, no channel accepted yet"; fi
    fi
    # The audience split is the one thing here that MUST survive condensing:
    # host ssh/UI are full control of the machine and are never published,
    # while the VM channels are per-person grants.  Shorter wording, same wall.
    RACBLOCK="
 Remote access   RAC $RACN on ${RSRV:-<no server>}${RTIER:+  ($RTIER)}
   status   $RSTAT${RREG:+ · registrar $RREG}${RAC_ADMIN:+
```

and replace it with:

```bash
         "$RAC_TOML")
RACEOF
fi
# This host has no keyboard, so every line below reads as something to type
# on ANOTHER computer.  The switch lives in the decoder VM.
_rvm="log in to the decoder VM (ssh -p 2222 sigmond@${HOSTIP:-<no-ip-yet>}) and type:"
# The channel lists.  The audience split is the one thing here that MUST
# survive condensing: host ssh/UI are full control of the machine and are
# never published, while the VM channels are per-person grants.
_rchannels="${RAC_ADMIN:+
```

(d) Find (lines 2890-2900):

```bash
     $RAC_USER}
"
elif [ -n "$RACN" ]; then
    RACBLOCK=" Remote access  RAC $RACN assigned, but /etc/sigmond/frpc-host.toml is missing
   ==> the tunnel is NOT configured; rerun: sigmond-setup --reconfigure
"
else
    RACBLOCK=" Remote access  not configured — this station is LAN-only
   ==> to enable off-site access: sigmond-setup --reconfigure
"
fi
```

and replace it with:

```bash
     $RAC_USER}"
case "$RSTATE" in
  on)
    RACBLOCK="
 Remote access   ON — RAC ${RACN:-?} on ${RSRV:-<no server>}${RTIER:+  ($RTIER)}
   status   $RUP/$RDECL channels up${RREG:+ · registrar $RREG}
   to turn it off, $_rvm  smd remote off${_rchannels}
"
    ;;
  off)
    if [ "$RREGD" = yes ]; then
        RACBLOCK=" Remote access   OFF, by the operator's choice${RSINCE:+ (since $RSINCE)} — not a fault
   The registration stays (RAC ${RACN:-?} on ${RSRV:-<no server>}).
   ==> to turn it on, $_rvm  smd remote on
"
    else
        RACBLOCK=" Remote access   off — this station has not registered for it
   ==> to turn it on, $_rvm  smd remote on
"
    fi
    ;;
  down)
    if [ "$RREGD" != yes ]; then
        RACBLOCK=" Remote access   WANTED ON, NOT REGISTERED YET — no gateway has answered
   This host tries again every 10 minutes and brings the tunnel up by itself.
   To stop it trying, $_rvm  smd remote off
"
    elif [ "$RRUNS" = running ]; then
        RACBLOCK=" Remote access   WANTED ON, BUT NOT UP — RAC ${RACN:-?} on ${RSRV:-<no server>}: 0/$RDECL channels accepted
   ==> check: ssh root@${HOSTIP:-<no-ip-yet>} 'journalctl -u sigmond-rac-host -n 50'
"
    else
        RACBLOCK=" Remote access   WANTED ON, BUT NOT UP — the tunnel service does not run (RAC ${RACN:-?})
   ==> check: ssh root@${HOSTIP:-<no-ip-yet>} 'journalctl -u sigmond-rac-host -n 50'
"
    fi
    ;;
  *)
    # No switch script on this host, or it left no state: the text every
    # panel printed up to v3.70.
    if [ -n "$RACN" ] && [ -r "$RAC_TOML" ]; then
        # Live state, not a claim: frpc publishes per-proxy status on its
        # local admin API, and that is the only thing that proves the gateway
        # accepted the channels.  Fall back to the unit state if the API is
        # not up.
        RSTAT="OFFLINE — check: journalctl -u sigmond-rac-host -n 50"
        if systemctl is-active --quiet sigmond-rac-host 2>/dev/null; then
            _run=$(curl -s --max-time 3 http://127.0.0.1:7500/api/status 2>/dev/null \
                     | grep -o '"status":"running"' | wc -l | tr -d ' ')
            # Count the channels this station ACTUALLY declares, never a
            # literal.  It was hardcoded to 4 and the station now ships 6, so
            # a fully healthy host advertised "6/4 channels up" (rob,
            # 2026-09-21).
            _decl=$(grep -c '^name *=' "$RAC_TOML" 2>/dev/null)
            [ "${_decl:-0}" -gt 0 ] || _decl=$_run
            if [ "${_run:-0}" -gt 0 ]; then RSTAT="online — $_run/$_decl channels up"
            else RSTAT="service running, no channel accepted yet"; fi
        fi
        RACBLOCK="
 Remote access   RAC $RACN on ${RSRV:-<no server>}${RTIER:+  ($RTIER)}
   status   $RSTAT${RREG:+ · registrar $RREG}${_rchannels}
"
    elif [ -n "$RACN" ]; then
        RACBLOCK=" Remote access  RAC $RACN assigned, but /etc/sigmond/frpc-host.toml is missing
   ==> the tunnel is NOT configured; rerun: sigmond-setup --reconfigure
"
    else
        RACBLOCK=" Remote access  not configured — this station is LAN-only
   ==> to enable off-site access: sigmond-setup --reconfigure
"
    fi
    ;;
esac
# <<< rac_block
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash -n firstboot-v3.sh && bash test-panel-remote.sh | tail -1`
Expected: `test-panel-remote.sh: 35 passed, 0 failed`.

Three older tests read the same panel. Run: `bash test-wifi-boot.sh | tail -1; bash test-console-storm.sh | tail -1; bash test-unit-quoting.sh | tail -1`
Expected: `108 passed, 0 failed`; `test-console-storm.sh: 11 passed, 0 failed`; `PASS: 11 unit(s) parse cleanly`.

- [ ] **Step 5: Watch two guards fail, then restore**

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance
F=firstboot-v3.sh
cp $F $F.keep
mut(){ cp $F.keep $F; python3 - "$F" "$1" "$2" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
assert s.count(sys.argv[2]) == 1, "the mutation's anchor must occur exactly once"
p.write_text(s.replace(sys.argv[2], sys.argv[3]))
PY
bash test-panel-remote.sh | grep -E 'FAIL|passed'; }

# 1. a deliberate off prints the fault text again
mut '        RACBLOCK=" Remote access   OFF, by the operator'"'"'s choice${RSINCE:+ (since $RSINCE)} — not a fault' \
    '        RACBLOCK=" Remote access   OFFLINE — check: journalctl -u sigmond-rac-host -n 50'
# -> 32 passed, 3 failed: it says OFF, by the operator's choice, it prints no fault word, it sends nobody to the journal

# 2. the panel changes the switch instead of reading it
mut '    timeout 20 "$RAC_SWITCH" status >/dev/null 2>&1' \
    '    timeout 20 "$RAC_SWITCH" on >/dev/null 2>&1'
# -> 34 passed, 1 failed: the panel asks the switch for status, once, and nothing else

mv $F.keep $F
bash test-panel-remote.sh | tail -1      # test-panel-remote.sh: 35 passed, 0 failed
```

- [ ] **Step 6: Commit**

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance
test "$(grep -c . .git/ATTRIBUTION)" = 2 || { echo "write .git/ATTRIBUTION first (Global Constraints)"; exit 1; }
git add firstboot-v3.sh test-panel-remote.sh
git commit -m "panel: remote access shows three states, and a deliberate off reads as one

The console panel printed OFFLINE and a journalctl hint whenever the
tunnel's unit did not run, so an operator who had turned remote access
off read a fault.  The panel now asks sigmond-rac-switch for its status
and shows on with its channels, off by the operator's choice with the
command that turns it on, or wanted on and not up, which stays the
fault.  Each line names the whole reach, for a host with no keyboard.
A host that carries no switch script yet keeps the v3.70 text.

sigmond tasks/plan-remote-access.md section 4.

$(cat .git/ATTRIBUTION)"
```

### Task 10: sudo on the stick, and the rigs prove the switch (sigmond-appliance)

Spec, "Phases": phase 1, "on the rig, off, reboot, still off, on". Phase 2, "the rig needs a registrar and a
tunnel server to stand in for the gateway; without them the nest can prove only 'wish on, not registered, timer
armed'". Phase 3, "on the rig, from the VM: status, off, the host's units stop; a second key, or a fourth word,
gets refused."

Three pieces.

**sudo rides the stick.** The pairing needs one sudoers rule, and stock Proxmox carries no sudo (ruling 9). A
greenfield install may have no network when the wizard pairs, so sudo joins the offline packages. Firstboot
already installs everything in `offline-debs/` with `dpkg -i`, and `sync-rig.sh` already carries that directory
to the rig, so no code changes for it.

**The nested rig** (`test-nested-v3.sh`) changes in three places.

- Its piped answers lose one line, but only for a wizard that no longer asks. The update rig runs these same
  phases on the v3.70 image, whose wizard still asks. So the rig reads the wizard under test and builds the
  answers to fit. One answer too many shifts every later one.
- **It pins the gateway before the wizard runs.** The nest reaches the internet through qemu's user network, and a
  v3.71 wizard registers without asking. Unpinned, every run would register `N0CALL_T1` with the real gateway.
  The pin (Task 1's `rac-endpoint.env`) names the nested host's own loopback.
- Five checks join Phase D, after the production reboot: (1) no gateway in reach, yet the install finished, wants
  remote access on, and armed its timer; sudo and the pairing stand. (2) From the VM, `status` answers; a fourth
  word, extra words, no word and a second key all meet a refusal. (3) A stand-in registrar on the nested host
  registers the station, and the request carries the station's name and the host's key and nothing else. (4)
  `off` stops the host's units and survives a reboot, the panel calls it the operator's choice, and `on` undoes
  it. (5) After the host's sshd key changes, the pairing re-asserts itself (Review Focus 3).

The stand-in answers the registration and then only accepts connections, so no channel ever comes up. The nest
therefore proves "registered, wanted on, not up" and never a tunnel that carries traffic. AC0G-ND proves that
(Task 12).

**The update rig** (`test-update-v3.sh`) gains PHASE E-ter, between E-bis and F. After the forward roll the guest
knows `smd remote` and the host still runs v3.70. That is a station in the field between its update and its
alignment. The phase checks that `smd remote status` exits 5 and names `sigmond-setup --pair-vm`; then places
the target's wizard on the host as pm-align would (ruling 17) and pairs with that one command; then checks that
the station still reads off, with nothing registered: its owner said No at install, and that answer stands.

**None of this rig code has run.** It parses, and its two readers have a hermetic test. Expect its first meeting
with a real nest, in Task 12, to find faults. v3.70's rig steps did the same; fix forward in this repo.

**Files:**
- Create: `sigmond-appliance/offline-debs/sudo_<version>_amd64.deb` (downloaded in Step 6)
- Modify: `sigmond-appliance/offline-debs/README.md`
- Modify: `sigmond-appliance/test-nested-v3.sh` — two readers after `phase_d_verdict` (line 66); the wizard's
  answers (lines 412-438); five checks before the closing notes (line 1212)
- Modify: `sigmond-appliance/test-update-v3.sh` — the header (lines 26 and 88); PHASE E-ter (line 1190)
- Create: `sigmond-appliance/test-remote-rig-lines.sh`

**Interfaces:**
- Consumes: Task 1's `rac-endpoint.env` and the registrar's request; Task 2's and 3's switch (its state line,
  exit codes, `rac-desired`, `rac-changed`, the retry timer's name); Task 3's wizard, which no longer prints
  `Enable remote access?`; Task 4's account, key line, sudoers file and `paired:` line; Task 5's `smd remote`
  (exit 3, exit 5, its unpaired message); Task 9's panel text.
- Produces:
  - In `test-nested-v3.sh`, between `# >>> remote_rig` and `# <<< remote_rig`: `wizard_answers <racq>` (prints
    13 answers for `1`, 12 for `0`) and `rr_state <file>` (prints `on`, `off`, `down` or nothing).
  - Phase D log lines that Task 12 reads: `wizard answers: 12 (remote-access question: not asked)`; five lines
    that begin `── remote access 1:` to `5:`, each followed by a line ending in `✓`; and, on an image whose
    wizard still asks, `⚠ REMOTE ACCESS CHECKS NOT EVALUATED`. `PHASE D PASS — NESTED TEST COMPLETE` keeps its
    words.
  - In `test-update-v3.sh`: `PHASE E-ter PASS — a host from <image> pairs with one command, and its owner's No
    still stands`, or `⚠ PHASE E-ter NOT EVALUATED` for a target before v3.71.

- [ ] **Step 1: Write the failing test of the rig's two readers**

Create `test-remote-rig-lines.sh` and make it executable:

```bash
#!/bin/bash
# test-remote-rig-lines.sh — the two readers test-nested-v3.sh uses for its
# remote-access checks, fed the bytes they meet.  No VM, no root; under a
# second.
#
# wizard_answers builds the answers the rig pipes into the wizard.  From v3.71
# the wizard asks nothing about remote access, and the update rig still runs
# these phases on a v3.70 image whose wizard asks.  One answer too many or too
# few shifts every later answer: on 2026-09-23 that made the wizard read a
# station designator as a heartbeat host, and Phase D failed with a message
# that named no prompt.
#
# rr_state reads the state out of `smd remote ...`'s answer.  `smd remote on`
# prints the registration's lines before the state line, so a reader that
# trusts the first line reads nothing.
#
#   usage: ./test-remote-rig-lines.sh [path/to/test-nested-v3.sh]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:-$HERE/test-nested-v3.sh}"
eval "$(sed -n '/^# >>> remote_rig/,/^# <<< remote_rig/p' "$SRC")"
for f in wizard_answers rr_state; do
    type "$f" >/dev/null 2>&1 || { echo "FAIL: $f not found between the remote_rig markers in $SRC"; exit 1; }
done
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0
chk(){ if [ "$2" = "$3" ]; then echo "  ok   $1"; else echo "  FAIL $1: got '$2', want '$3'"; fail=1; fi; }

# ── wizard_answers ──────────────────────────────────────────────────────
mapfile -t OLD < <(wizard_answers 1)
mapfile -t NEW < <(wizard_answers 0)
chk "a wizard that asks gets thirteen answers"      "${#OLD[@]}" 13
chk "a wizard that does not ask gets twelve"        "${#NEW[@]}" 12
chk "the old wizard's fifth answer declines"        "${OLD[4]}" n
chk "the old wizard's PSWS id follows it"           "${OLD[5]}" S000999
chk "the new wizard's PSWS id follows the class"    "${NEW[3]}/${NEW[4]}" n/S000999
chk "both end with apply"                           "${OLD[12]}/${NEW[11]}" Y/Y
chk "the four blank answers stay blank lines"       "$(wizard_answers 0 | grep -c '^$')" 4
# Drop the fifth answer from the old list and the new list must result.
chk "the two lists differ by that one answer alone" \
    "$(printf '%s|' "${OLD[@]:0:4}" "${OLD[@]:5}")" "$(printf '%s|' "${NEW[@]}")"

# ── rr_state ────────────────────────────────────────────────────────────
ans(){ printf '%s\n' "$@" > "$T/a"; rr_state "$T/a"; }
chk "status, on"   "$(ans 'Remote access: on' '  The gateway accepted 6 of 6 channels (RAC 507 on gw2.wsprdaemon.org).')" on
chk "status, off"  "$(ans 'Remote access: off' '  The operator holds it off.')" off
chk "status, down" "$(ans 'Remote access: wanted on, not up' '  The tunnel service does not run.')" down
# `smd remote on` on a station that registers first: the state line comes
# sixth, after smd's own line and the registration's.
ON_AFTER_REGISTERING=(
    '     asking the Proxmox host; this can take a minute while the tunnel comes up'
    'This station has not registered for remote access yet. Registering now.'
    '[wizard] registering with the gateway: this sends the station'"'"'s name (N0CALL_T1) and this host'"'"'s public key'
    '[wizard] trying pinned — 127.0.0.1:35735 (registrar http://127.0.0.1:35737/register)'
    '[wizard] registered: RAC #599 via pinned'
    'Waiting up to 60s for the gateway to accept the channels.'
    'Remote access: wanted on, not up'
    '  The tunnel service runs, and the gateway has accepted none of its 6 channels (RAC 599 on 127.0.0.1, pinned).'
)
chk "on, after the registration's lines" "$(ans "${ON_AFTER_REGISTERING[@]}")" down
chk "an indented state line names no state"        "$(ans '  Remote access: on')" ""
chk "a longer line names no state"                 "$(ans 'Remote access: online')" ""
chk "a refusal names no state"                     "$(ans 'sigmond-rac-switch: refused. It takes exactly one word: on, off or status.')" ""
chk "smd's not-paired message names no state"      "$(ans '  x  this VM holds no pairing with its Proxmox host')" ""
chk "an empty answer names no state"               "$(: > "$T/a"; rr_state "$T/a")" ""
chk "a missing file names no state"                "$(rr_state "$T/absent")" ""

# Positive control: the fixture must defeat the reader this one replaced, or
# the 'ok' above proves nothing.  A reader that trusts the first line finds no
# state in the answer of an `on` that registered first.
printf '%s\n' "${ON_AFTER_REGISTERING[@]}" > "$T/a"
case "$(head -1 "$T/a")" in
    "Remote access: "*) echo "  FAIL control: the first line of the fixture already names the state"; fail=1 ;;
    *) echo "  ok   control: a first-line reader finds no state in the same answer" ;;
esac

[ "$fail" = 0 ] && echo "PASS: wizard_answers, rr_state" || echo "FAILED: wizard_answers, rr_state"
exit "$fail"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/mjh/hamsci/repos/sigmond-appliance && bash test-remote-rig-lines.sh; echo "rc=$?"`
Expected: `FAIL: wizard_answers not found between the remote_rig markers in …/test-nested-v3.sh`, then `rc=1`.

- [ ] **Step 3: Change `test-nested-v3.sh`**

(a) Find (lines 66-67):

```bash
# <<< phase_d_verdict
KEY="$HOME/appliance/build/applkey"
```

and replace it with:

```bash
# <<< phase_d_verdict

# >>> remote_rig (test-remote-rig-lines.sh loads exactly this block)
# Two readers for the remote-access checks of Phase D (sigmond
# tasks/plan-remote-access.md).
#
# wizard_answers <racq>: the wizard's answers, one per line, in the order it
# asks.  racq=1 for a wizard that still asks "Enable remote access? [Y/n]"
# (v3.70 and earlier: the update rig's base image); racq=0 for one that asks
# nothing about it.  One answer too many or too few shifts every answer after
# it, and the wizard then reads a PSWS id as a heartbeat host.
wizard_answers() {
    local a=(
        'N0CALL/T1'          # reporter id
        'EM00aa'             # grid square
        'Tier2 test dipole'  # antenna description
        'n'                  # is this a DASI station?
    )
    # v3.70 and earlier asked "Enable remote access? [Y/n]" at this point.
    [ "$1" = 1 ] && a+=( 'n' )
    a+=(
        'S000999'            # PSWS station id
        '172'                # GRAPE instrument id
        '84'                 # magnetometer instrument id
        ''                   # heartbeat: send them? (blank = Y, the default)
        ''                   # heartbeat: collector host (blank = wd30.wsprdaemon.org)
        ''                   # heartbeat: collector port (blank = 38222)
        ''                   # station designator (blank = default from reporter)
        'Y'                  # apply
    )
    printf '%s\n' "${a[@]}"
}
# rr_state <file>: on | off | down from the switch's answer, or nothing.  The
# answer names its state on a line that reads exactly one of three strings.
# For `smd remote on` that line follows the registration's own lines, so the
# first line of the answer proves nothing.
rr_state() {
    LC_ALL=C awk '
        $0 == "Remote access: on"                { print "on";   exit }
        $0 == "Remote access: off"               { print "off";  exit }
        $0 == "Remote access: wanted on, not up" { print "down"; exit }' "$1" 2>/dev/null
}
# <<< remote_rig
KEY="$HOME/appliance/build/applkey"
```

(b) Find (lines 424-438):

```bash
WIZARD_ANSWERS=(
    'N0CALL/T1'          # reporter id
    'EM00aa'             # grid square
    'Tier2 test dipole'  # antenna description
    'n'                  # is this a DASI station?
    'n'                  # enable remote access (RAC)?
    'S000999'            # PSWS station id
    '172'                # GRAPE instrument id
    '84'                 # magnetometer instrument id
    ''                   # heartbeat: send them? (blank = Y, the default)
    ''                   # heartbeat: collector host (blank = wd30.wsprdaemon.org)
    ''                   # heartbeat: collector port (blank = 38222)
    ''                   # station designator (blank = default from reporter)
    'Y'                  # apply
)
```

and replace it with:

```bash
# From v3.71 the wizard asks nothing about remote access: every install
# registers and brings the tunnel up.  The update rig runs these phases on the
# previous blessed image, whose wizard still asks.  So ask the wizard under
# test which one it is, and answer accordingly (wizard_answers, above).
case "$($SSHN "grep -q 'Enable remote access?' /usr/local/sbin/sigmond-setup && echo RACQ:YES || echo RACQ:NO" 2>&1)" in
    *RACQ:YES*) RACQ=1 ;;
    *RACQ:NO*)  RACQ=0 ;;
    *) say "FATAL: could not read the host's wizard to decide its answers"; exit 1 ;;
esac
if [ "$RACQ" = 1 ] && [ "${SINK_BASE_IMAGE:-0}" != 1 ]; then
    say "FATAL: the image under test still asks 'Enable remote access?'"
    say "  only the update rig's base image may; test-update-v3.sh sets SINK_BASE_IMAGE=1 for that run"
    exit 1
fi
# ⛔ PIN THE GATEWAY BEFORE THE WIZARD RUNS.  This nest reaches the internet
# through qemu's user network, and a v3.71 wizard registers without asking.
# Unpinned, every run of this rig would register "N0CALL_T1" with the real
# gateway.  The pin names this host's own loopback, where nothing listens
# until the stand-in further down starts.  The wizard reads the pin at start,
# and so does the retry timer.  A wizard from before v3.71 ignores the file.
$SSHN "mkdir -p /etc/sigmond-appliance && printf '%s\n' SIGMOND_RAC_SERVER=127.0.0.1 SIGMOND_RAC_REGISTRAR=http://127.0.0.1:35737/register SIGMOND_RAC_TLS=off SIGMOND_RAC_FRPS_PORT=35735 > /etc/sigmond-appliance/rac-endpoint.env" \
    || { say "FATAL: could not pin the gateway on the nested host"; exit 1; }
mapfile -t WIZARD_ANSWERS < <(wizard_answers "$RACQ")
say "wizard answers: ${#WIZARD_ANSWERS[@]} (remote-access question: $([ "$RACQ" = 1 ] && echo asked || echo not asked))"
```

(c) Find (lines 1211-1213):

```bash
fi

# What the site sink checks left unchecked, said once more beside the verdict.
```

and replace it with:

```bash
fi

# ── remote access: every install registers, and the operator holds the switch ─
# sigmond tasks/plan-remote-access.md.  The nest has no gateway, so what it
# can prove stops short of a running tunnel:
#   1. an install with no gateway in reach finishes, wants remote access on,
#      and keeps trying;
#   2. the decoder VM can ask the host, and only for the three words;
#   3. a stand-in registrar registers the station;
#   4. off stops the host's units and stays off across a reboot; on undoes it;
#   5. the pairing survives a change of the host's sshd key.
# A tunnel that carries traffic is proven on hardware (AC0G-ND).
RAC_UNCHECKED=""
if [ "$RACQ" = 1 ]; then
    say "⚠ REMOTE ACCESS CHECKS NOT EVALUATED — this image's wizard predates v3.71"
    RAC_UNCHECKED="all of them (this image's wizard still asks 'Enable remote access?')"
else
    # gq <timeout> <tag> <snippet>: run the snippet as root in the decoder VM
    # through qm guest exec.  It travels base64-encoded, so the request holds
    # ASCII only.  Leaves the output in the file $GQ_OUT and the snippet's
    # exit status in $GQ_RC; returns 1 when the channel gave no status.
    gq(){
        local t="$1" raw="$PWD/rr-$2.json" b64
        GQ_OUT="$PWD/rr-$2.out"; GQ_RC=""
        b64=$(printf '(\n%s\n) > /tmp/rr.out 2>&1\nprintf "RRRC=%%s\\n" "$?"\ncat /tmp/rr.out\n' "$3" | base64 -w0)
        $SSHN "qm guest exec $VMID --timeout $t -- bash -lc \"echo $b64 | base64 -d | bash\"" > "$raw" 2>&1
        python3 - "$raw" "$GQ_OUT" <<'PYEOF'
import json, sys
raw = open(sys.argv[1], errors="replace").read()
try:
    d = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
except Exception:
    d = {}
open(sys.argv[2], "w").write((d.get("out-data") or "") + (d.get("err-data") or ""))
PYEOF
        GQ_RC=$(sed -n 's/^RRRC=\([0-9][0-9]*\)$/\1/p' "$GQ_OUT" | head -1)
        sed -i '/^RRRC=[0-9][0-9]*$/d' "$GQ_OUT"
        [ -n "$GQ_RC" ]
    }
    rr_fatal(){ say "FATAL: $1"; [ -f "${GQ_OUT:-}" ] && sed 's/^/      /' "$GQ_OUT" | tail -12; exit 1; }
    # remote <word> <tag>: smd remote <word> in the VM; sets RR (on|off|down) and GQ_RC.
    remote(){
        gq 240 "$2" "smd remote $1" || rr_fatal "smd remote $1: the guest-exec channel gave no answer (not a verdict)"
        RR=$(rr_state "$GQ_OUT")
    }
    PAIRSSH='ssh -T -i /etc/sigmond/rac-switch.key -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/etc/sigmond/rac-switch.known_hosts -o GlobalKnownHostsFile=/dev/null -o ConnectTimeout=8 -o LogLevel=ERROR sigmond-remote@10.99.0.1'

    say "── remote access 1: no gateway in reach, and the install finished all the same"
    R1=$($SSHN "echo WISH:\$(cat /etc/sigmond-appliance/rac-desired 2>/dev/null); test -e /etc/sigmond/frpc-host.toml && echo REG:1 || echo REG:0; echo TIMER:\$(systemctl is-enabled sigmond-rac-register.timer 2>&1); command -v sudo >/dev/null && command -v visudo >/dev/null && echo SUDO:1 || echo SUDO:0; visudo -c -q -f /etc/sudoers.d/sigmond-remote >/dev/null 2>&1 && echo SUDOERS:1 || echo SUDOERS:0; echo KEYLINES:\$(grep -c . /var/lib/sigmond-remote/.ssh/authorized_keys 2>/dev/null); grep -c '^restrict,from=\"10.99.0.2\",command=\"sudo -n /usr/local/sbin/sigmond-rac-switch\" ssh-ed25519 ' /var/lib/sigmond-remote/.ssh/authorized_keys 2>/dev/null | sed 's/^/KEYOK:/'; dpkg-query -W -f='\${Status}' sudo 2>/dev/null | grep -q 'install ok installed' && echo SUDOPKG:1 || echo SUDOPKG:0" 2>&1)
    echo "$R1" | sed 's/^/      /'
    echo "$R1" | grep -qx 'WISH:on'      || { say "FATAL: a fresh install does not want remote access on (/etc/sigmond-appliance/rac-desired)"; exit 1; }
    echo "$R1" | grep -qx 'REG:0'        || { say "FATAL: the nest registered with a gateway — the pin did not hold"; exit 1; }
    echo "$R1" | grep -qx 'TIMER:enabled' || { say "FATAL: the retry timer is not armed on an unregistered station that wants remote access"; exit 1; }
    echo "$R1" | grep -qx 'SUDOPKG:1'    || { say "FATAL: sudo is not installed on the host — the stick's offline-debs did not carry or configure it"; exit 1; }
    echo "$R1" | grep -qx 'SUDO:1'       || { say "FATAL: sudo or visudo missing from the host's PATH"; exit 1; }
    echo "$R1" | grep -qx 'SUDOERS:1'    || { say "FATAL: /etc/sudoers.d/sigmond-remote missing or refused by visudo"; exit 1; }
    echo "$R1" | grep -qx 'KEYLINES:1'   || { say "FATAL: the pairing account must hold exactly one key line"; exit 1; }
    echo "$R1" | grep -qx 'KEYOK:1'      || { say "FATAL: the pairing account's key line lacks restrict, from= or the forced command"; exit 1; }
    say "wish on, not registered, retry timer armed, sudo and the pairing in place ✓"

    say "── remote access 2: the VM asks, and only for the three words"
    remote status rr2-status
    [ "$RR" = down ] && [ "$GQ_RC" = 3 ] || rr_fatal "smd remote status: want 'wanted on, not up' and exit 3 on an unregistered station, got '${RR:-nothing}' exit $GQ_RC"
    grep -q 'tries again every 10 minutes' "$GQ_OUT" || rr_fatal "the answer does not say the host keeps trying"
    gq 60 rr2-fourth "$PAIRSSH restart" || rr_fatal "the refusal probe gave no answer"
    [ "$GQ_RC" = 2 ] || rr_fatal "a fourth word must be refused with exit 2, got $GQ_RC"
    gq 60 rr2-extra "$PAIRSSH 'status; id'" || rr_fatal "the refusal probe gave no answer"
    [ "$GQ_RC" = 2 ] && ! grep -q 'uid=' "$GQ_OUT" || rr_fatal "extra words after 'status' must be refused and must run nothing (exit $GQ_RC)"
    gq 60 rr2-none "$PAIRSSH </dev/null" || rr_fatal "the refusal probe gave no answer"
    [ "$GQ_RC" = 2 ] || rr_fatal "a login with no word must be refused with exit 2, got $GQ_RC (did it get a shell?)"
    gq 60 rr2-key2 "rm -f /tmp/rr-k2 /tmp/rr-k2.pub; ssh-keygen -q -t ed25519 -N '' -f /tmp/rr-k2; ${PAIRSSH/-i \/etc\/sigmond\/rac-switch.key/-i \/tmp\/rr-k2} status; rc=\$?; rm -f /tmp/rr-k2 /tmp/rr-k2.pub; exit \$rc" || rr_fatal "the second-key probe gave no answer"
    [ "$GQ_RC" = 255 ] && [ -z "$(rr_state "$GQ_OUT")" ] || rr_fatal "a second key must be refused by sshd (exit 255, no state), got exit $GQ_RC"
    say "status answered; a fourth word, extra words, no word and a second key all refused ✓"

    say "── remote access 3: a stand-in gateway registers the station"
    # A registrar on :35737 and a bare listener on :35735, on the nested host's
    # loopback.  The registrar answers as the real one does; the listener only
    # accepts, so frpc can never log in and no channel ever comes up.
    $SSHN "cat > /root/rac-standin.py" <<'STANDINEOF'
import http.server, json, socket, threading
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n))
        open("/root/rac-standin.seen", "a").write(json.dumps(sorted(body)) + "\n")
        out = json.dumps({"user": "0123456789abcdef", "token": "nestedrigtoken01", "rac": 599,
                          "server_addr": "127.0.0.1", "server_port": 35735,
                          "ports": {"vm_ssh": 36399, "vm_web": 46399,
                                    "host_ssh": 51399, "host_ui": 56399}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
def frps():
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 35735)); s.listen(8)
    while True:
        c, _ = s.accept(); c.close()
threading.Thread(target=frps, daemon=True).start()
http.server.ThreadingHTTPServer(("127.0.0.1", 35737), H).serve_forever()
STANDINEOF
    $SSHN "rm -f /root/rac-standin.seen; systemctl reset-failed rac-standin 2>/dev/null; systemd-run --unit=rac-standin --collect --quiet python3 /root/rac-standin.py && sleep 3 && systemctl is-active --quiet rac-standin" \
        || { say "FATAL: the stand-in gateway did not start on the nested host"; exit 1; }
    remote on rr3-on
    [ "$RR" = down ] && [ "$GQ_RC" = 3 ] || rr_fatal "smd remote on against the stand-in: want 'wanted on, not up' exit 3 (registered, no channel), got '${RR:-nothing}' exit $GQ_RC"
    R3=$($SSHN "echo RACN:\$(cat /etc/sigmond-appliance/rac-number 2>/dev/null); grep -c '^user = \"0123456789abcdef\"' /etc/sigmond/frpc-host.toml 2>/dev/null | sed 's/^/TOML:/'; grep -c '^name = ' /etc/sigmond/frpc-host.toml 2>/dev/null | sed 's/^/PROXIES:/'; echo UNIT:\$(systemctl is-enabled sigmond-rac-host 2>&1); echo TIMER:\$(systemctl is-enabled sigmond-rac-register.timer 2>&1); echo SEEN:\$(cat /root/rac-standin.seen 2>/dev/null | sort -u | tr -d ' ')" 2>&1)
    echo "$R3" | sed 's/^/      /'
    echo "$R3" | grep -qx 'RACN:599'      || { say "FATAL: the registration's RAC number did not reach /etc/sigmond-appliance/rac-number"; exit 1; }
    echo "$R3" | grep -qx 'TOML:1'        || { say "FATAL: frpc-host.toml does not carry the registered account"; exit 1; }
    echo "$R3" | grep -qx 'PROXIES:6'     || { say "FATAL: frpc-host.toml does not declare six channels"; exit 1; }
    echo "$R3" | grep -qx 'UNIT:enabled'  || { say "FATAL: sigmond-rac-host is not enabled after smd remote on"; exit 1; }
    echo "$R3" | grep -qx 'TIMER:disabled' || { say "FATAL: the retry timer is still armed after the station registered"; exit 1; }
    echo "$R3" | grep -qx 'SEEN:\["pubkey","site"\]' || { say "FATAL: registration sent something besides the station's name and the host's public key"; exit 1; }
    say "registered through the stand-in: RAC 599, six channels declared, name and key sent and nothing else ✓"

    say "── remote access 4: off stops the host's units and survives a reboot"
    remote off rr4-off
    [ "$RR" = off ] && [ "$GQ_RC" = 0 ] || rr_fatal "smd remote off: want 'off' exit 0, got '${RR:-nothing}' exit $GQ_RC"
    rac_units(){ $SSHN "echo WISH:\$(cat /etc/sigmond-appliance/rac-desired 2>/dev/null); echo BY:\$(awk '{print \$3}' /etc/sigmond-appliance/rac-changed 2>/dev/null); echo UNIT:\$(systemctl is-enabled sigmond-rac-host 2>&1)/\$(systemctl is-active sigmond-rac-host 2>&1); echo SOCK:\$(systemctl is-enabled sigmond-vm-ssh-relay.socket 2>&1)/\$(systemctl is-active sigmond-vm-ssh-relay.socket 2>&1); test -s /etc/sigmond/frpc-host.toml && echo TOMLKEPT:1 || echo TOMLKEPT:0" 2>&1; }
    R4=$(rac_units); echo "$R4" | sed 's/^/      /'
    echo "$R4" | grep -qx 'WISH:off' && echo "$R4" | grep -qx 'BY:vm' \
        || { say "FATAL: the host did not record the wish off, asked from the VM"; exit 1; }
    echo "$R4" | grep -qx 'UNIT:disabled/inactive' || { say "FATAL: sigmond-rac-host still enabled or running after smd remote off"; exit 1; }
    echo "$R4" | grep -qx 'SOCK:disabled/inactive' || { say "FATAL: a relay socket still enabled or listening after smd remote off"; exit 1; }
    echo "$R4" | grep -qx 'TOMLKEPT:1' || { say "FATAL: smd remote off removed the registration"; exit 1; }
    say "rebooting the nested host: the switch must still read off afterwards"
    $SSHN "systemctl stop rac-standin 2>/dev/null; reboot" >/dev/null 2>&1
    sleep 20
    for i in $(seq 1 90); do $SSHN true 2>/dev/null && break; sleep 5; done
    $SSHN true 2>/dev/null || { say "FATAL: nested host did not come back after the reboot"; exit 1; }
    for i in $(seq 1 30); do $SSHN "qm status $VMID 2>/dev/null | grep -q running" 2>/dev/null && break; sleep 10; done
    AOK=0; for i in $(seq 1 40); do if $SSHN "qm agent $VMID ping" >/dev/null 2>&1; then AOK=$((AOK+1)); [ "$AOK" -ge 2 ] && break; sleep 3; else AOK=0; sleep 10; fi; done
    [ "$AOK" -ge 2 ] || { say "FATAL: guest agent never answered after the reboot"; exit 1; }
    R4=$(rac_units); echo "$R4" | sed 's/^/      /'
    echo "$R4" | grep -qx 'UNIT:disabled/inactive' && echo "$R4" | grep -qx 'WISH:off' \
        || { say "FATAL: remote access did not stay off across a reboot"; exit 1; }
    RRTRY=""; for i in $(seq 1 12); do remote status rr4-status; RRTRY="$RR/$GQ_RC"; [ "$RRTRY" = off/0 ] && break; sleep 10; done
    [ "$RRTRY" = off/0 ] || rr_fatal "smd remote status after the reboot: want 'off' exit 0, got $RRTRY"
    $SSHN "SIGMOND_VMID=$VMID /usr/local/sbin/sigmond-issue >/dev/null 2>&1; grep -q \"OFF, by the operator's choice\" /etc/issue && ! grep -q 'OFFLINE' /etc/issue" \
        || { say "FATAL: the console panel does not show a deliberate off as the operator's choice"; exit 1; }
    say "off held across the reboot, and the panel calls it the operator's choice ✓"
    $SSHN "systemd-run --unit=rac-standin --collect --quiet python3 /root/rac-standin.py && sleep 3" >/dev/null 2>&1
    remote on rr4-on
    [ "$RR" = down ] && [ "$GQ_RC" = 3 ] || rr_fatal "smd remote on after the reboot: want 'wanted on, not up' exit 3, got '${RR:-nothing}' exit $GQ_RC"
    R4=$(rac_units)
    echo "$R4" | grep -qx 'WISH:on' && echo "$R4" | grep -q '^UNIT:enabled/' && echo "$R4" | grep -q '^SOCK:enabled/' \
        || { say "FATAL: smd remote on did not enable the host's units again"; echo "$R4" | sed 's/^/      /'; exit 1; }
    say "on enabled the tunnel and the relays again ✓"

    say "── remote access 5: the pairing survives a change of the host's sshd key"
    # What an identity restore does (AC0G-B4, 2026-10-01): the host's sshd key
    # changes under a VM that pinned the old one.  The host must re-assert the
    # pairing by itself; nobody types anything.
    $SSHN "rm -f /etc/ssh/ssh_host_ed25519_key /etc/ssh/ssh_host_ed25519_key.pub; ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/ssh_host_ed25519_key && systemctl restart ssh" \
        || { say "FATAL: could not replace the nested host's sshd key"; exit 1; }
    RRTRY=""; for i in $(seq 1 18); do
        gq 240 rr5-status "smd remote status" || true
        RRTRY="$(rr_state "$GQ_OUT")/$GQ_RC"
        case "$RRTRY" in down/3|on/0|off/0) break ;; esac
        sleep 10
    done
    case "$RRTRY" in
        down/3|on/0|off/0) say "the host re-asserted the pairing after its sshd key changed ($RRTRY) ✓" ;;
        *) rr_fatal "three minutes after the host's sshd key changed, smd remote status still fails ($RRTRY)" ;;
    esac
    $SSHN "systemctl stop rac-standin 2>/dev/null; true"
fi

# What the site sink checks left unchecked, said once more beside the verdict.
```

(d) Find (lines 1217-1218):

```bash
    say "⚠ SITE SINK CHECKS NOT EVALUATED: $SINK_UNCHECKED"
fi
```

and replace it with:

```bash
    say "⚠ SITE SINK CHECKS NOT EVALUATED: $SINK_UNCHECKED"
fi
if [ -n "${RAC_UNCHECKED:-}" ]; then
    say "⚠ REMOTE ACCESS CHECKS NOT EVALUATED: $RAC_UNCHECKED"
fi
```

- [ ] **Step 4: Run the hermetic tests**

Run: `bash -n test-nested-v3.sh && bash test-remote-rig-lines.sh | tail -1 && bash test-phase-d-verdict.sh | tail -1`
Expected: `PASS: wizard_answers, rr_state` and `PASS: phase_d_verdict`.

- [ ] **Step 5: Add PHASE E-ter to `test-update-v3.sh`**

(a) Find (lines 25-26):

```bash
#             daemon must start after the migrate and stay up.
#   PHASE F — adopt the release manifest with --allow-superset (the updated
```

and replace it with:

```bash
#             daemon must start after the migrate and stay up.
#   PHASE E-ter — remote access on a station already in the field: the
#             updated guest's `smd remote` names the one host command while
#             unpaired; the new wizard, placed on the old host as pm-align
#             places it, pairs with that command; and a station whose owner
#             said No at install stays as it stands.
#   PHASE F — adopt the release manifest with --allow-superset (the updated
```

(b) Find (lines 88-89):

```bash
#                                       cmd_uploader_manifest, check mode
#   "pipelines.toml re-rendered by the restored sigmond"
```

and replace it with:

```bash
#                                       cmd_uploader_manifest, check mode
#   "holds no pairing with its Proxmox host", exit 5
#                                       lib/sigmond/commands/remote.py
#                                       _not_paired (v3.71)
#   "paired: the decoder VM can run"    scripts/proxmox/sigmond-wizard.sh,
#                                       the --pair-vm branch (v3.71)
#   "Remote access: off" and            the switch script inside the wizard
#     "never registered"                (print_state), read through smd remote
#   "pipelines.toml re-rendered by the restored sigmond"
```

(c) Find (lines 1190-1191):

```bash
# ════════════════════════════════════════════════════════════════════════
say "════ PHASE F: adopt the blessed manifest (superset-tolerant) ════"
```

and replace it with:

```bash
# ════════════════════════════════════════════════════════════════════════
say "════ PHASE E-ter: remote access on a station already in the field ════"

# sigmond tasks/plan-remote-access.md.  `smd update` moved the guest to the
# target; nothing moved the Proxmox host, which still runs the base image's
# wizard.  A station in the field sits exactly so between its update and its
# next pm-align.  Three things must hold:
#   1. `smd remote` in the guest says what stands in the way and names the one
#      command that removes it;
#   2. the target's wizard, placed on the host the way pm-align places it,
#      pairs the two machines with that one command;
#   3. the owner of this station said No at install (the rig's own answer), so
#      remote access must still read off afterwards, with nothing registered.
# A guest whose smd has no `remote` verb is a target from before v3.71; the
# phase then says so and checks nothing.
gx_ok 120 et0-verb 'smd remote --help >/dev/null 2>&1; echo "RV:$?"' "E-ter: does this smd know remote?"
if [ "$(gx_tok "$GX_OUT" RV)" != 0 ]; then
    say "⚠ PHASE E-ter NOT EVALUATED — the target's smd has no 'remote' verb (a target before v3.71)"
else
    # ── 1. unpaired: the guest names the host command ───────────────────
    gx_ok 120 et1-unpaired 'smd remote status' "E-ter: smd remote status, unpaired"
    sed 's/^/      /' "$GX_OUT"
    [ "$GX_RC" -eq 5 ] || fatal "$GX_OUT" "smd remote status on an unpaired station exited $GX_RC, want 5"
    grep -q 'holds no pairing with its Proxmox host' "$GX_OUT" \
        || fatal "$GX_OUT" "the unpaired message does not say that the VM holds no pairing"
    grep -q 'sigmond-setup --pair-vm' "$GX_OUT" \
        || fatal "$GX_OUT" "the unpaired message does not name the host command"
    say "unpaired: smd remote exits 5 and names sigmond-setup --pair-vm ✓"

    # ── 2. the target's wizard on the old host pairs with one command ───
    # pm-align installs the release's wizard as /usr/local/sbin/sigmond-setup,
    # with this host's VMID rendered in.  It reads a blessed release, and
    # nobody has blessed the target yet, so the rig places the same file by
    # hand: from the guest's own checkout, over the private link.
    # The pin comes first: no step of this rig may register with a real gateway.
    $SSHN "printf '%s\n' SIGMOND_RAC_SERVER=127.0.0.1 SIGMOND_RAC_REGISTRAR=http://127.0.0.1:35737/register SIGMOND_RAC_TLS=off SIGMOND_RAC_FRPS_PORT=35735 > /etc/sigmond-appliance/rac-endpoint.env
        set -e
        cp -p /usr/local/sbin/sigmond-setup /root/sigmond-setup.base
        scp -q -o BatchMode=yes -o ConnectTimeout=10 sigmond@10.99.0.2:/opt/git/sigmond/sigmond/scripts/proxmox/sigmond-wizard.sh /root/sigmond-wizard.target
        sed -e 's/SIGMOND_VMID:-120/SIGMOND_VMID:-$VMID/g' -e 's/\"SIGMOND_VMID\", \"120\"/\"SIGMOND_VMID\", \"$VMID\"/g' /root/sigmond-wizard.target > /root/sigmond-setup.target
        bash -n /root/sigmond-setup.target
        grep -q '^    --pair-vm)' /root/sigmond-setup.target
        install -m 755 /root/sigmond-setup.target /usr/local/sbin/sigmond-setup" > "$WORK/et2-place.out" 2>&1 \
        || fatal "$WORK/et2-place.out" "could not place the target's wizard on the nested host (scp from the guest, render, install)"
    $SSHN "command -v sudo >/dev/null 2>&1 && echo HADSUDO:1 || echo HADSUDO:0" > "$WORK/et2-sudo.out" 2>&1
    say "the base host $(grep -q 'HADSUDO:1' "$WORK/et2-sudo.out" && echo 'already carries sudo' || echo 'carries no sudo: the pairing must fetch it')"
    $SSHN "SIGMOND_VMID=$VMID timeout 900 /usr/local/sbin/sigmond-setup --pair-vm" > "$WORK/et2-pair.out" 2>&1
    ET_RC=$?
    sed 's/^/      /' "$WORK/et2-pair.out" | tail -12
    [ "$ET_RC" -eq 0 ] || fatal "$WORK/et2-pair.out" "sigmond-setup --pair-vm exited $ET_RC on a host installed from the base image"
    grep -q 'paired: the decoder VM can run' "$WORK/et2-pair.out" \
        || fatal "$WORK/et2-pair.out" "sigmond-setup --pair-vm exited 0 without its 'paired:' line"
    say "the target's wizard paired a host installed from $IMGBASE ✓"

    # ── 3. the owner's No still stands ──────────────────────────────────
    gx_ok 120 et3-status 'smd remote status' "E-ter: smd remote status, paired"
    sed 's/^/      /' "$GX_OUT"
    [ "$GX_RC" -eq 0 ] || fatal "$GX_OUT" "smd remote status on the paired station exited $GX_RC, want 0 (off)"
    LC_ALL=C grep -qx 'Remote access: off' "$GX_OUT" \
        || fatal "$GX_OUT" "a station whose owner declined at install does not read 'Remote access: off' after pairing"
    LC_ALL=C grep -q 'never registered' "$GX_OUT" \
        || fatal "$GX_OUT" "the answer does not say that this station never registered"
    $SSHN "test ! -e /etc/sigmond/frpc-host.toml && test ! -e /etc/sigmond-appliance/rac-desired && ! systemctl is-active --quiet sigmond-rac-host && ! systemctl is-enabled --quiet sigmond-rac-register.timer 2>/dev/null" \
        || fatal - "pairing changed the station: it registered, recorded a wish, started the tunnel or armed the retry timer"
    say "paired, and still off: nothing registered, no wish recorded, no timer armed ✓"
    say "PHASE E-ter PASS — a host from $IMGBASE pairs with one command, and its owner's No still stands"
fi

# ════════════════════════════════════════════════════════════════════════
say "════ PHASE F: adopt the blessed manifest (superset-tolerant) ════"
```

Run: `bash -n test-update-v3.sh && bash test-update-smd-lines.sh | tail -1`
Expected: `PASS: held_names, restore_moved, PANEL_DEFECT_RE`.

- [ ] **Step 6: Put sudo among the offline packages**

The devbox runs Debian 13, the base of Proxmox 9, so its apt offers the right build.

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance/offline-debs
apt-get download sudo
ls sudo_*_amd64.deb
dpkg-deb -f sudo_*_amd64.deb Package Version Architecture Depends Pre-Depends
```

Expected: one new file, `sudo_1.9.16p2-3+deb13u2_amd64.deb` or a later Debian 13 revision, about 2 MB. Its
`Depends` line names `libapparmor1`, `libaudit1`, `libc6`, `libpam0g`, `libselinux1`, `libssl3t64`, `zlib1g` and
`libpam-modules`, and its `Pre-Depends` line `init-system-helpers`. A Proxmox 9 host carries every one of them, so
sudo ships alone, as the README's rule for leaf packages asks. The nested rig's check 1 proves it: it fails unless
dpkg reports sudo `install ok installed`.

Then record it in `offline-debs/README.md`:

(a) Find (lines 22-23):

```markdown
| `btop`, `tmux` | operator tooling on the PM — rob works from tmux and reads per-core load in btop, and a station on an IPv6-only site cannot apt them in. **Neither is in stock PVE**; every dependency of both already is |
| the rest | dependency closure of the above that stock PVE lacks |
```

and replace it with:

```markdown
| `btop`, `tmux` | operator tooling on the PM — rob works from tmux and reads per-core load in btop, and a station on an IPv6-only site cannot apt them in. **Neither is in stock PVE**; every dependency of both already is |
| `sudo` | the remote-access pairing: one account on the PM may run one script as root, by one sudoers line (sigmond `tasks/plan-remote-access.md` section 2). The wizard pairs at install, before any network is assumed. **Not in stock PVE**; every dependency of it already is |
| the rest | dependency closure of the above that stock PVE lacks |
```

(b) Find (lines 29-30):

````markdown
```bash
WANT="clatd tayga dnsmasq dnsmasq-base rdnssd ndisc6 wpasupplicant iw btop tmux"
````

and replace it with:

````markdown
```bash
WANT="clatd tayga dnsmasq dnsmasq-base rdnssd ndisc6 wpasupplicant iw btop tmux sudo"
````

Check that nothing else needs to change: `grep -n 'offline-debs/\*.deb' firstboot-v3.sh; grep -n 'offline-debs' sync-rig.sh`
Expected: firstboot's two `dpkg -i "$APP"/offline-debs/*.deb` lines and its `cp` of the same glob, and sync-rig's
`operators|offline-debs) ;;` line.

- [ ] **Step 7: Run every hermetic test once more**

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance
for t in test-panel-remote.sh test-remote-rig-lines.sh test-phase-d-verdict.sh test-update-smd-lines.sh test-unit-quoting.sh test-console-storm.sh test-wifi-boot.sh test-vm-console.sh test-bless-instructions.sh; do
    printf '%-28s ' "$t"; bash "$t" 2>&1 | tail -1
done
```

Expected, in order: `test-panel-remote.sh: 35 passed, 0 failed`; `PASS: wizard_answers, rr_state`;
`PASS: phase_d_verdict`; `PASS: held_names, restore_moved, PANEL_DEFECT_RE`; `PASS: 11 unit(s) parse cleanly`;
`test-console-storm.sh: 11 passed, 0 failed`; `108 passed, 0 failed`; `15 passed, 0 failed`;
`195 passed, 0 failed`.

- [ ] **Step 8: Commit**

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance
test "$(grep -c . .git/ATTRIBUTION)" = 2 || { echo "write .git/ATTRIBUTION first (Global Constraints)"; exit 1; }
git add offline-debs/ test-nested-v3.sh test-update-v3.sh test-remote-rig-lines.sh
git commit -m "rigs: prove the remote-access switch; sudo rides the stick

test-nested: the wizard's answers fit a wizard that no longer asks about
remote access, and the gateway gets pinned to the nest's own loopback
before the wizard runs, so no nested install registers with a real
gateway.  Five checks join Phase D: an install with no gateway finishes
and keeps trying; the VM can ask for three words and nothing else; a
stand-in registrar registers the station; off survives a reboot and on
undoes it; the pairing survives a change of the host's sshd key.

test-update: PHASE E-ter pairs a v3.70 host with one command and checks
that a station whose owner said No still reads off.

offline-debs gains sudo: the pairing needs one sudoers rule, and stock
Proxmox carries no sudo.

sigmond tasks/plan-remote-access.md, phases 1 to 3.

$(cat .git/ATTRIBUTION)"
```

### Task 11: the instructions say how to turn remote access off and on (both repos)

Spec, "Decided on review": "The installation documentation tells the operator, plainly, how to turn remote access
off and on." Spec section 5 names the pages: `docs/operator/remote-access.md` ("sections 3 and 4 rewritten around
`smd remote`; the 'ignore `smd admin rac`' section shrinks to a sentence"), the appliance's `INSTALL.md` and
`docs/install-page.html` ("the wizard step loses its question and gains three sentences: remote access comes up
by itself, what it sends, and how to switch it from the VM"), and the quick-start card ("one line").

Two repos, two commits. Each half starts with a test, because Michael's standing policy binds the instructions to
every build (`sigmond-appliance/docs/RELEASE.md`, rung 3), and a rule that nothing checks describes the last
release.

**The operator's page gets rewritten whole**, not patched. Its opening, its section 4 and its section 7 all rested
on two facts that no longer hold: that the wizard asked, and that "from inside the decoder VM there is no way to
tell whether remote access is enabled". The new page opens with the three commands. It also corrects the count of
channels, which has stood at six since station-web and the magnetometer joined the tunnel. Five neighbouring pages
point at headings or commands that change, and each gets the smallest edit that keeps it true.

**The appliance's three documents** lose the question from their lists of answers, say that remote access comes up
by itself and what registering sends, and name `smd remote off` and `smd remote on`. `INSTALL.md` keeps its
`Verified against` line as it stands: Task 12 moves that line after the hardware run, as rung 3 prescribes.

**Files (sigmond):**
- Modify: `docs/operator/remote-access.md` (whole file), `docs/operator/troubleshooting.md` (lines 951-984 and
  1145), `docs/operator/do-not-touch.md` (lines 52-53), `docs/operator/README.md` (lines 44 and 67-69),
  `docs/operator/install.md` (lines 22-25), `docs/operator/registration.md` (line 44),
  `docs/operator/glossary.md` (lines 34 and 50)
- Create: `tests/test_docs_remote_access.py`

**Files (sigmond-appliance):**
- Modify: `INSTALL.md`, `docs/install-page.html`, `QUICKSTART.txt`
- Create: `test-instructions-remote.sh`

**Interfaces:**
- Consumes: the words the code prints. From Task 2 and 3 the switch's three state lines and its second lines;
  from Task 3 the wizard's text in `ask_rac`; from Task 4 the `paired:` line; from Task 5 `smd remote`'s three
  failure messages and `remote.STATE_LINES`.
- Produces: headings that other pages link to. `remote-access.md` keeps `#1-what-it-is`,
  `#it-runs-on-the-host-not-on-the-vm`,
  `#5-how-the-admin-actually-connects--and-the-one-instruction-that-is-wrong` and
  `#6-privacy--and-switching-it-off-for-good`, and gains `#4-the-switch-off-on-status`,
  `#7-if-remote-access-does-not-come-up` and `#8-a-station-installed-before-v371`.

#### The sigmond half

- [ ] **Step 1: Write the failing test**

Create `tests/test_docs_remote_access.py`:

````python
"""The operator pages agree with the code about remote access
(tasks/plan-remote-access.md section 5).

From v3.71 the wizard asks nothing about remote access and the operator holds
the switch in the decoder VM.  A page that still describes the old question,
or quotes a message the code no longer prints, teaches a station that does
not exist.  These checks read the pages and the code side by side.
"""
import re
from pathlib import Path

from sigmond.commands import remote

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs" / "operator"
PAGE = (DOCS / "remote-access.md").read_text()
WIZARD = (REPO / "scripts" / "proxmox" / "sigmond-wizard.sh").read_text()
REMOTE_PY = (REPO / "lib" / "sigmond" / "commands" / "remote.py").read_text()


def anchors(text: str) -> set:
    """GitHub's anchor for every heading: lower case, punctuation dropped,
    each space a hyphen."""
    out = set()
    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.*?)\s*$", line)
        if m:
            kept = re.sub(r"[^\w\- ]", "", m.group(1).lower())
            out.add(kept.replace(" ", "-"))
    return out


def test_the_page_opens_with_the_three_commands():
    head = PAGE[:PAGE.index("## 1. What it is")]
    for cmd in ("smd remote off", "smd remote on", "smd remote status"):
        assert cmd in head, cmd
    assert "The setup wizard asks nothing about it." in head


def test_no_operator_page_describes_the_question_the_wizard_no_longer_asks():
    for md in sorted(DOCS.glob("*.md")):
        text = md.read_text()
        assert "Enable remote access?" not in text, md.name
        assert "If you said No at install time" not in text, md.name
        assert "Remote access says FAILED" not in text, md.name


def test_every_link_into_the_page_lands_on_a_heading():
    have = anchors(PAGE)
    for md in sorted(DOCS.glob("*.md")):
        for m in re.finditer(r"remote-access\.md#([A-Za-z0-9_-]+)", md.read_text()):
            assert m.group(1) in have, f"{md.name} links to #{m.group(1)}, which the page lacks"
    for m in re.finditer(r"\]\(#([A-Za-z0-9_-]+)\)", PAGE):
        assert m.group(1) in have


def test_only_the_remote_access_page_names_the_hosts_own_spelling_of_the_switch():
    # The host has no keyboard after install, so every other page sends the
    # operator to the VM.  The canonical page may say that the host's two
    # spellings run the same switch.
    for md in sorted(DOCS.glob("*.md")):
        if md.name != "remote-access.md":
            assert "sigmond-setup --rac-o" not in md.read_text(), md.name
    assert "sigmond-setup --rac-off" in PAGE and "sigmond-setup --pair-vm" in PAGE


def test_the_states_on_the_page_are_the_switchs_own_lines():
    for line in remote.STATE_LINES:
        assert f"\n{line}\n" in PAGE, line
        assert f'echo "{line}"' in WIZARD, line


def test_the_messages_the_page_quotes_are_messages_the_code_prints():
    for quoted in ("The station has not registered yet: no gateway has answered.",
                   "The tunnel service does not run",
                   "The operator holds it off. The registration stays",
                   "paired: the decoder VM can run smd remote on, smd remote off and smd remote status"):
        assert quoted in PAGE, quoted
        assert quoted in WIZARD, quoted
    for quoted in ("holds no pairing with its Proxmox host",
                   "shows an sshd key this VM was not paired with",
                   "could not reach the Proxmox host"):
        assert quoted in PAGE, quoted
        assert quoted in REMOTE_PY, quoted


def test_the_wizards_words_on_the_page_are_the_wizards():
    i = PAGE.index("Remote access comes up by itself; this wizard asks nothing about it.")
    block = PAGE[i:PAGE.index("```", i)]
    for line in block.strip().splitlines():
        assert f'echo "{line}"' in WIZARD, line


def test_the_page_lists_the_six_channels_the_tunnel_declares():
    declared = re.findall(r'name = "\$SITE-([a-z-]+)"', WIZARD)
    assert sorted(declared) == ["host-ssh", "host-ui", "vm-gmag", "vm-ssh", "vm-station", "vm-web"]
    assert "**Six channels**" in PAGE
    for base in ("35800", "45800", "48800", "49800", "50800", "55800"):
        assert f"{base} + your RAC number" in PAGE, base
        assert base in WIZARD
````

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_docs_remote_access.py -q`
Expected: `7 failed, 1 passed`. `test_every_link_into_the_page_lands_on_a_heading` passes already: today's links
land on today's headings. Step 4 must keep that true while three headings change.

- [ ] **Step 3: Replace `docs/operator/remote-access.md` with this text**

````markdown
# Remote access (RAC) — letting the fleet admin reach your station

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond @@SIGMOND_SHA@@ on @@DATE@@ — the switch script, registration and pairing in `scripts/proxmox/sigmond-wizard.sh`, `lib/sigmond/commands/remote.py`, and their tests; the image rig's nested install. A hardware install has not yet confirmed it.
> **Canonical for:** remote access (RAC) — what it is, what it exposes, how the operator switches it, how the admin connects

Every Sigmond station registers for remote access when it installs, and its
tunnel comes up and stays up. The setup wizard asks nothing about it. A tunnel
gives the only effective means of supporting a station, so every install
carries one.

You hold the switch. Log in to the decoder `[VM]` and type:

```bash
smd remote off        # turn remote access off; it stays off across reboots
smd remote on         # turn it on again
smd remote status     # show the state
```

This page says what the tunnel is, exactly what it lets someone reach, who that
someone is, and what each of those three commands does. Words you don't
recognise are in the [glossary](glossary.md).

**RAC** stands for *Remote Access Channel*.

A station installed before v3.71 needs one step before `smd remote` works there;
§8 gives it.

---

## 1. What it is

Your station **dials out** to a machine at HamSCI (a *gateway*) and holds that
connection open. When your fleet admin needs to reach your station, they connect
to the gateway, and the gateway hands them back down the connection your station
already made.

That direction is the whole point:

- **Nothing is opened on your router.** No port forwarding, no firewall rules,
  no dynamic-DNS name, nothing for you to configure. If you have ever been asked
  to "just open a port" for something, this is the opposite of that.
- **It works behind anything.** NAT, a carrier that hands you a shared address,
  a router you don't administer — none of it matters, because your station is
  the one placing the call.
- **You can hang up.** `smd remote off` stops it (§4), and the station keeps
  doing science exactly as before.

Concretely: a small program called `frpc` runs on the **Proxmox host** and keeps
one TLS connection open to the gateway, verified against a certificate authority
pinned into the image at `/etc/sigmond/frps-ca.crt` (source:
`/etc/sigmond/frpc-host.toml` on the `[host]`).

### It runs on the host, not on the VM

This is the part that confuses people, so it gets its own paragraph. The
[station is two machines in one box](README.md#two-machines-in-one-box) — the
Proxmox `[host]` and the decoder `[VM]` — and **the tunnel lives on the
`[host]`**. The first boot installs it there, and the wizard then registers the
station and brings the tunnel up.

Putting it on the host buys two things worth having: the admin can still reach
the station **when the decoder VM is down** — which is precisely when you most
need help — and one tunnel carries the VM's channels too.

The switch, though, sits in the VM, because that is the machine you log in to.
The VM asks; the host alone acts (§4).

---

## 2. What it exposes, and to whom

**Six channels**, and this is the complete list. Nothing else on your station
is reachable through the tunnel.

| Channel | What it reaches | Gateway port |
|---|---|---|
| **VM ssh** | a command line on the decoder `[VM]`, as `hamsci` or `sigmond` | 35800 + your RAC number |
| **VM web** | the [ka9q-web](glossary.md) live-receiver page (port 8081 on the VM) | 45800 + your RAC number |
| **station pages** | the station's own web pages (port 8000 on the VM) | 48800 + your RAC number |
| **magnetometer** | the magnetometer dashboard (port 8082 on the VM) | 49800 + your RAC number |
| **host ssh** | a command line on the Proxmox `[host]`, as `root` | 50800 + your RAC number |
| **host UI** | the Proxmox web GUI (port 8006 on the host) | 55800 + your RAC number |

(Source: `sigmond/scripts/proxmox/sigmond-wizard.sh`, the `RAC_BASE_*` values,
and each station's `/etc/sigmond/frpc-host.toml`. The console panel on the
station's monitor lists your station's actual ports.)

The **RAC number** is bookkeeping, not identity. The gateway assigns a free one
when the station registers. A HamSCI DASI station derives it from its unit
number instead: 220 + N, so DASI-001 is RAC 221. The host keeps it in
`/etc/sigmond-appliance/rac-number`, and it **sticks** across a later
`sigmond-setup --reconfigure`, so your ports never move.

### Who can actually get in

Not the public internet. Two locks:

- **The gateway does not publish these ports.** The admin reaches them over the
  gateway's private VPN. The one port open to the internet is the one your
  station dials *out* to.
- **Then they still need a key.** Getting through the gateway only puts someone
  at your station's ssh login prompt. Logging in needs an ssh key already
  installed on your station, or the password you set.

So "who can reach my station" is: **the fleet admins who hold both.** In
practice today that is the person who gave you the image, plus the volunteers
who operate the fleet gateway. There is no self-service portal and no public
listing.

What it is **not** for: it carries no science data. Your spots and PSWS products
travel their own upload paths ([registration.md](registration.md)) and keep
flowing with the tunnel switched off.

---

## 3. What the install does, and what it sends

The wizard asks whether this is a DASI station, because that decides which
gateway it tries. It asks nothing else about remote access. It tells you this
much on the console:

```text
Remote access comes up by itself; this wizard asks nothing about it.
It gives your fleet admin the only effective way to support this
station: a tunnel the station dials out, with nothing opened on your
router.
  Registering sends two things to the gateway: this station's name
  and this host's public key.
  You hold the switch. In the decoder VM, at any time:
      smd remote off      turn it off (it stays off across reboots)
      smd remote on       turn it on again
      smd remote status   show the state
```

(Source: `sigmond-wizard.sh`, `ask_rac()`.)

**What registering sends.** Two things: the station's name (your reporter ID,
with `/` written as `_`) and the host's public ssh key. The gateway answers with
an account, a token, a RAC number and ports, and the host writes them to
`/etc/sigmond/frpc-host.toml`. Where a registrar hands out the account, it
still answers over plain HTTP today, so the token crosses the internet
unencrypted once. The tunnel itself uses TLS wherever the gateway offers it.

**An install with no internet still finishes.** So does one that finds the
gateway down. The station then wants remote access on and has not registered.
The host tries again every 10 minutes, and brings the tunnel up by itself once a
gateway answers. If you turn the switch off in the meantime, the host still
registers when it can, and stops there.

The wizard's closing summary carries a `RAC:` line with the state, and a
`Switch:` line that says whether the VM can work the switch. The summary stays
at `/root/sigmond-setup-summary.txt` on the `[host]`.

---

## 4. The switch: off, on, status

Everything in this section runs in the decoder `[VM]`, after
`ssh sigmond@<VM address>` or `ssh -p 2222 sigmond@<host address>`.

**See the state** — `[VM]`:

```bash
smd remote status
```

It prints one of three states:

```text
Remote access: on
  The gateway accepted 6 of 6 channels (RAC 507 on gw2.wsprdaemon.org, wsprdaemon (secure)).
  Set to on at 2026-10-09T18:22:05Z (UTC) by the installer.
  Turn it off:  smd remote off   (in the decoder VM)
```

```text
Remote access: off
  The operator holds it off. The registration stays (RAC 507 on gw2.wsprdaemon.org, wsprdaemon (secure)),
  so turning it on needs nothing from the gateway.
  Set to off at 2026-10-12T03:10:44Z (UTC) from the decoder VM.
  Turn it on:   smd remote on    (in the decoder VM)
```

```text
Remote access: wanted on, not up
  The tunnel service does not run (RAC 507 on gw2.wsprdaemon.org, wsprdaemon (secure)).
  Look at:  journalctl -u sigmond-rac-host.service -n 50   (on the Proxmox host)
  Set to on at 2026-10-09T18:22:05Z (UTC) by the installer.
  Turn it off:  smd remote off   (in the decoder VM)
```

Only the third describes a fault. §7 covers it.

**Turn it off** — `[VM]`:

```bash
smd remote off
```

The host stops the tunnel and the four relays that carry the VM's channels, and
disables them. It keeps the registration, the key and the ports, so a later `on`
needs nothing from the gateway. Off stays off: across reboots, across updates,
and across a rerun of the wizard. Nothing on the station turns it back on, and
nobody outside the station can. Only a reinstall from the stick starts over,
with remote access on.

One thing to know before you type it from far away: if you reached the VM
*through* the tunnel, `smd remote off` cuts the connection you type on. The host
still finishes the job. You then need a way in that does not use the tunnel,
such as the station's own network, to turn it on again.

**Turn it on** — `[VM]`:

```bash
smd remote on
```

The host starts the tunnel and waits up to a minute for the gateway to accept
the channels. A station that never registered registers first. `on` carries no
time limit: it stays on until you turn it off.

**Where else the state shows.**

- The **console panel** on the station's monitor says `ON` with the channels,
  `OFF, by the operator's choice` with the command that turns it on, or
  `WANTED ON, BUT NOT UP`.
- The **login banner** of the VM prints one line with the state, the time the
  host last reported it, and the command.
- The **fleet board** shows a station whose operator turned remote access off
  as exactly that. Your admin sees a decision, not a station to chase.

**How the VM asks.** The host has no keyboard once the install ends, and you
know the VM, so the VM asks and the host alone acts. At install the wizard
*pairs* the two machines: the VM gets one ssh key, kept in a file only `root`
can read, and the host gets an account for it. On the host that key can run
exactly one program, with one of three words: `on`, `off` or `status`. It opens
no shell, forwards no port and runs nothing else. The VM cannot create this
pairing; only the host can. Someone who breaks into the VM as `root` can
therefore work the switch, and gains nothing more by it: turning the tunnel on
opens the station only to the gateway's users, who still need a key your
station accepts.

`smd remote` raises its own privileges, as every changing `smd` verb does. Type
it as shown, with no `sudo` in front.

**From the host.** `sigmond-setup --rac-off` and `sigmond-setup --rac-on` on the
`[host]` run the same switch and do the same thing.

**`smd admin rac` is a different thing.** Those verbs manage a tunnel inside
the VM, for a station that has no Proxmox host. On your station they report
"not configured", and that is correct. Use `smd remote`.

---

## 5. How the admin actually connects — and the one instruction that is wrong

Your admin reaches the gateway over its private VPN, then connects to your
station's channel port. For the decoder VM that is:

```bash
# run by the fleet admin, on the gateway's private network — not by you
ssh -p <your VM ssh port> hamsci@<the gateway's private address>
```

⛔ **The `root@` form does not work.** The fleet's RAC dashboard — the page an
admin uses to find your station — lists each station's channels and hands out a
command of the form `ssh -p <port> root@<gateway>` (observed on the dashboard
2026-08-17). That command can never succeed against a Sigmond station's decoder
VM: the VM sets `PermitRootLogin no` in
`/etc/ssh/sshd_config.d/10-sigmond-operator.conf`. The port answers, the host
key is exchanged, and then authentication fails every time — which looks
exactly like a broken tunnel and is not one.

**The truthful form is `hamsci@` or `sigmond@`** for the decoder VM. `root@` is
correct only for the **host ssh** channel, where root login is permitted.

If an admin tells you they "can reach the port but can't log in", this is the
first thing to suspect.

---

## 6. Privacy — and switching it off for good

Plainly:

- **It gives an admin a shell on your machine.** Not a limited view, not a
  read-only dashboard: a login, as `hamsci`/`sigmond` on the VM or `root` on the
  host. Someone using it can read and change anything on the station. That is
  the trade — it is what makes "just ask, they'll fix it"
  ([INSTALL.md §11](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#11-if-something-goes-wrong))
  possible.
- **The tunnel itself forwards nothing but those six ports.** It is not a
  route onto your home network: no other device of yours becomes reachable
  *through the tunnel*. (An admin who logs in is then on your LAN like any
  other machine in the house — that follows from the shell in the bullet above,
  not from the tunnel.)
- **Nothing about it is public.** The channel ports are not open to the internet
  (§2), and the station is not listed anywhere a stranger can browse.
- **It is not needed to produce or upload data.** Every upload path works with
  it off.
- **It is yours to end.** `smd remote off` stops it now, and it stays off until
  you type `smd remote on`.

Some owners will not grant standing access, yet will open the door when trouble
comes. The switch exists for exactly that: keep it off, and turn it on for the
hour your admin needs.

If you keep it off, your admin sees that on the fleet board, so you need not
tell anyone. Do not delete files under `/etc/sigmond/` by hand to achieve it
(→ [do-not-touch.md](do-not-touch.md)).

---

## 7. If remote access does not come up

`smd remote status` says `wanted on, not up`, and so does the console panel.
**This costs you nothing but support access.** The radio, the recorders and
every upload path are unaffected.

Read the second line of the answer; it names one of three cases.

- **"The station has not registered yet: no gateway has answered."** The
  install found no internet, or the gateway was down. Do nothing: the host tries
  again every 10 minutes and brings the tunnel up when a gateway answers. To
  try now, type `smd remote on`.
- **"The tunnel service runs, and the gateway has accepted none of its 6
  channels."** The station cannot reach its gateway at the moment. The service
  retries every 30 seconds, so a broadband outage or a DNS hiccup heals by
  itself.
- **"The tunnel service does not run."** Something stopped it outside the
  switch. Type `smd remote on`. If the state stays the same, send your fleet
  admin the output of this, run on the `[host]`:

  ```bash
  journalctl -u sigmond-rac-host.service -n 50
  ```

`smd remote` itself can fail to reach the host. It then says why:

- *"this VM holds no pairing with its Proxmox host"* — the station predates
  v3.71; see §8.
- *"shows an sshd key this VM was not paired with"* — the host's key changed.
  The host pairs again by itself within minutes; try again shortly.
- *"could not reach the Proxmox host"* — the host does not answer on the
  private link between the two machines. Tell your fleet admin.

---

## 8. A station installed before v3.71

Such a station keeps the answer its owner gave the old wizard. If the owner
said Yes, remote access stays on. If the owner said No, it stays off, and the
station registers only when its owner types `smd remote on`.

Before `smd remote` works there, the two machines must pair once. That takes one
command on the `[host]`:

```bash
# [host] — on the Proxmox host, not the VM
sigmond-setup --pair-vm
```

It answers:

```text
[wizard] paired: the decoder VM can run smd remote on, smd remote off and smd remote status
```

Your fleet admin's `pm-align` runs the same step when it brings the host up to
date, so you may never need to type it. Until the station pairs, the host's own
`sigmond-setup --rac-off` and `sigmond-setup --rac-on` work the switch.

---

## When to mention it

Tell your fleet admin if remote access stays `wanted on, not up` for more than a
day, or if they report being able to reach the port but not log in (§5).
Otherwise there is nothing here to check weekly — the weekly routine is
[day-2.md](day-2.md).
````

Then put the commit and the date into its header:

```bash
cd /home/mjh/hamsci/repos/sigmond
sed -i "s/@@SIGMOND_SHA@@/$(git rev-parse --short HEAD)/; s/@@DATE@@/$(date -u +%F)/" docs/operator/remote-access.md
sed -n 5p docs/operator/remote-access.md
```

Expected: the `Verified against` line, naming the commit of Task 8 and today's date in UTC.

- [ ] **Step 4: Bring the five neighbouring pages into step**

`docs/operator/troubleshooting.md`:

(a) Find (lines 951-972):

````markdown
### Remote access says FAILED

**A failed tunnel costs you nothing but support access.** The radio, the
recorders and every upload path are completely unaffected — remote access
carries no science data at all
([remote-access.md §7](remote-access.md#7-if-it-says-failed)).

**What to do** — one command, from the `[host]`, whenever you like:

```bash
# [host] — on the Proxmox host, not the VM
sigmond-setup --reconfigure
```

Only the remote-access step needs to change, but the wizard asks every question
again: type your reporter ID and PSWS ids afresh (pressing Enter at the PSWS
station ID skips PSWS) and check the grid square on its review screen. It keeps
your RAC number and the site sink switch.

**Two traps worth knowing before you chase this:**

- ⛔ **If your admin says "I can reach the port but cannot log in", that is
````

and replace it with:

````markdown
### Remote access says wanted on, not up

**This costs you nothing but support access.** The radio, the recorders and
every upload path are completely unaffected — remote access carries no science
data at all
([remote-access.md §7](remote-access.md#7-if-remote-access-does-not-come-up)).

**What to do** — look at the state, in the `[VM]`:

```bash
# [VM] — in the decoder VM
smd remote status
```

The second line of its answer names the case. A station that has not registered
yet needs nothing from you: the host tries again every 10 minutes and brings
the tunnel up when a gateway answers. A tunnel service that does not run starts
again with `smd remote on`.

**Three things worth knowing before you chase this:**

- **`Remote access: off` is not a fault.** The operator turned it off. `smd
  remote on` turns it on again
  ([remote-access.md §4](remote-access.md#4-the-switch-off-on-status)).
- ⛔ **If your admin says "I can reach the port but cannot log in", that is
````

(b) Find (lines 978-985):

```markdown
  `smd admin rac install` to "fix" it
  ([remote-access.md §4](remote-access.md#ignore-smd-admin-rac-on-an-appliance-station)).

**When to stop and ask**

`sigmond-setup --reconfigure` still reports FAILED. On an older image this step
used to fail for everybody; the answer is a newer image, not more retries
([remote-access.md §7](remote-access.md#7-if-it-says-failed)).
```

and replace it with:

```markdown
  `smd admin rac install` to "fix" it. The verb for your station is `smd remote`
  ([remote-access.md §4](remote-access.md#4-the-switch-off-on-status)).

**When to stop and ask**

`smd remote status` still says `wanted on, not up` a day later, or `smd remote`
says it holds no pairing with its Proxmox host
([remote-access.md §8](remote-access.md#8-a-station-installed-before-v371)).
```

(c) Find (lines 1145-1146):

```markdown
`sigmond-setup --reconfigure` on the `[host]` (for wrong answers, a new grid
square, or remote access).
```

and replace it with:

```markdown
`sigmond-setup --reconfigure` on the `[host]` (for wrong answers or a new grid
square).
```

`docs/operator/do-not-touch.md`:

(d) Find (lines 52-53):

```markdown
| **Edit or delete anything under `/etc/sigmond/`** | It is the station's identity and wiring — `topology.toml`, `coordination.toml`, and the `coordination.env` that sigmond renders from it and every client reads. It is written by the wizard and by `smd`, not by hand; deleting a file here does not turn a feature off, it makes the station disagree with itself | Tell your fleet admin. To turn something off there is always a supported verb — e.g. `sigmond-setup --rac-off` on the `[host]` for remote access ([remote-access.md §6](remote-access.md#6-privacy--and-switching-it-off-for-good)) |
| **`smd admin rac install`** inside the `[VM]` | Your remote-access tunnel runs on the Proxmox **host**, not in the VM; the VM's `smd admin rac` verbs manage a *different* tunnel and their "not configured" message is correct output, not a fault. Running the install would stand up a second, redundant tunnel ([remote-access.md §4](remote-access.md#ignore-smd-admin-rac-on-an-appliance-station); [docs-gap ledger row 13](../contributor/docs-gap-ledger.md)) | Tell your fleet admin so the extra tunnel gets removed from the gateway too |
```

and replace it with:

```markdown
| **Edit or delete anything under `/etc/sigmond/`** | It is the station's identity and wiring — `topology.toml`, `coordination.toml`, and the `coordination.env` that sigmond renders from it and every client reads. It is written by the wizard and by `smd`, not by hand; deleting a file here does not turn a feature off, it makes the station disagree with itself | Tell your fleet admin. To turn something off there is always a supported verb — e.g. `smd remote off` in the `[VM]` for remote access ([remote-access.md §6](remote-access.md#6-privacy--and-switching-it-off-for-good)) |
| **`smd admin rac install`** inside the `[VM]` | Your remote-access tunnel runs on the Proxmox **host**, not in the VM; the VM's `smd admin rac` verbs manage a *different* tunnel and their "not configured" message is correct output, not a fault. Running the install would stand up a second, redundant tunnel. The verb for your station's tunnel is `smd remote` ([remote-access.md §4](remote-access.md#4-the-switch-off-on-status)) | Tell your fleet admin so the extra tunnel gets removed from the gateway too |
```

`docs/operator/README.md`:

(e) Find (lines 43-44):

```markdown
| 4. Learn what "healthy" looks like | [day-2.md](day-2.md) | 15 min |
| 5. (optional) Let the fleet admin reach it | [remote-access.md](remote-access.md) | 10 min |
```

and replace it with:

```markdown
| 4. Learn what "healthy" looks like | [day-2.md](day-2.md) | 15 min |
| 5. Know who can reach your station, and how to switch that off | [remote-access.md](remote-access.md) | 10 min |
```

(f) Find (lines 67-69):

```markdown
to read the host address. Several later steps — the Proxmox window, every
remote-access check in [remote-access.md §4](remote-access.md#4-turning-it-on-off-and-checking-it--all-on-the-host) —
run on the `[host]` and are simply unreachable without it. If you have already
```

and replace it with:

```markdown
to read the host address. Several later steps — the Proxmox window, a rerun
of the setup wizard — run on the `[host]` and are simply unreachable without
it. (The switch for remote access needs only the VM:
[remote-access.md §4](remote-access.md#4-the-switch-off-on-status).) If you have already
```

`docs/operator/install.md`:

(g) Find (lines 24-25):

```markdown
window and for every remote-access check in
[remote-access.md](remote-access.md). The VM address alone is not enough.
```

and replace it with:

```markdown
window and for a rerun of the setup wizard. The VM address alone is not enough
for those. (It is enough for the remote-access switch:
[remote-access.md](remote-access.md).)
```

`docs/operator/registration.md`:

(h) Find (lines 43-44):

```markdown
| **PSWS station + instrument IDs** *(optional, skippable)* | `[psws]` and `[psws.instruments]` | PSWS only |
| **Remote access** *(optional)* | the RAC tunnel | your fleet admin, not a data path |
```

and replace it with:

```markdown
| **PSWS station + instrument IDs** *(optional, skippable)* | `[psws]` and `[psws.instruments]` | PSWS only |
| **Remote access** *(no question; every install registers)* | the RAC tunnel | your fleet admin, not a data path |
```

`docs/operator/glossary.md`:

(i) Find (lines 33-34):

```markdown
| **fleet admin** | The person who gave you the image and watches the fleet board. Your first contact whenever something looks wrong. |
| **frpc** | The small program that holds the *RAC* tunnel open. It runs on the `[host]`, dials out to the gateway and keeps the four channels alive; you meet the name in `smd admin rac status` output and in `/etc/sigmond/frpc-host.toml`. |
```

and replace it with:

```markdown
| **fleet admin** | The person who gave you the image and watches the fleet board. Your first contact whenever something looks wrong. |
| **frpc** | The small program that holds the *RAC* tunnel open. It runs on the `[host]`, dials out to the gateway and keeps the six channels alive; you meet the name in `/etc/sigmond/frpc-host.toml` and in the host's `journalctl -u sigmond-rac-host`. |
```

(j) Find (lines 49-50):

```markdown
| **PSWS** | HamSCI's Personal Space Weather Station network and its [data portal](https://pswsnetwork.eng.ua.edu/), where the daily time-standard and magnetometer products land. Needs a station ID plus per-instrument IDs. |
| **RAC** | Remote Access Channel — an outbound tunnel that lets your fleet admin reach the station from outside your router with no ports opened and no firewall changes. Optional. |
```

and replace it with:

```markdown
| **PSWS** | HamSCI's Personal Space Weather Station network and its [data portal](https://pswsnetwork.eng.ua.edu/), where the daily time-standard and magnetometer products land. Needs a station ID plus per-instrument IDs. |
| **RAC** | Remote Access Channel — an outbound tunnel that lets your fleet admin reach the station from outside your router with no ports opened and no firewall changes. Every install sets it up; you switch it with `smd remote off` and `smd remote on`. |
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_docs_remote_access.py -q`
Expected: `8 passed`.

Run: `.venv/bin/python scripts/docs-conformance.py . 2>&1 | tail -1`
Expected: `docs-conformance: 0 finding(s)`.

Run: `.venv/bin/pytest -q`
Expected: `3728 passed, 1 warning, 65 subtests passed` (3720 after Task 8, and 8 new).

- [ ] **Step 6: Commit the sigmond half**

```bash
cd /home/mjh/hamsci/repos/sigmond
test "$(grep -c . .git/ATTRIBUTION)" = 2 || { echo "write .git/ATTRIBUTION first (Global Constraints)"; exit 1; }
! grep -q '@@' docs/operator/remote-access.md || { echo "fill the page's header first (Step 3)"; exit 1; }
git add docs/operator/ tests/test_docs_remote_access.py
git commit -m "docs: the operator's page on remote access, rewritten around smd remote

Every install registers and brings the tunnel up, and the wizard asks
nothing; the operator holds the switch in the decoder VM.  The page
opens with smd remote off, on and status, says what registering sends,
shows the three states and where they appear, and tells a station
installed before v3.71 how to pair.  It lists the six channels the
tunnel carries.  Five neighbouring pages follow.  A test reads the
pages beside the code: the quoted messages must be ones the code
prints, and every link into the page must land on a heading.

tasks/plan-remote-access.md section 5.

$(cat .git/ATTRIBUTION)"
```

#### The sigmond-appliance half

- [ ] **Step 7: Write the failing test**

Create `test-instructions-remote.sh` and make it executable:

```bash
#!/bin/bash
# test-instructions-remote.sh — the three installation documents say how an
# operator turns remote access off and on, and none still describes the
# question the wizard no longer asks.  No VM, no root; under a second.
#
# Why: from v3.71 every install registers for remote access and brings the
# tunnel up, and the wizard asks nothing about it (sigmond
# tasks/plan-remote-access.md).  The operator then holds the switch, in the
# decoder VM: smd remote off, smd remote on.  An install guide that still
# lists "Enable remote access? Y" teaches a step that no longer exists, and
# one that never names the switch leaves the owner with standing access and
# no way known to them to end it.
#
# The documents: INSTALL.md, docs/install-page.html (both published beside
# every blessed image, bless-release.sh gate 7) and QUICKSTART.txt, which
# rides the stick.
#
#   usage: ./test-instructions-remote.sh
#          CONTROL_REF=<git ref> names the release whose documents must FAIL
#          these checks (default v3.70, the last one whose wizard asked).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
CONTROL_REF="${CONTROL_REF:-v3.70}"
DOCS="INSTALL.md docs/install-page.html QUICKSTART.txt"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0

# findings <file>: one line per thing wrong with it; nothing when it is right.
findings(){
    local f="$1"
    grep -q 'smd remote off' "$f"    || echo "does not name the command that turns remote access off (smd remote off)"
    grep -q 'smd remote on' "$f"     || echo "does not name the command that turns it on (smd remote on)"
    grep -qi 'enable remote access' "$f" && echo "still lists the wizard's old question (Enable remote access?)"
    grep -Eq 'Remote access +Y' "$f"  && echo "still lists remote access as an answer to prepare"
    grep -Eqi 'remote.access[^.|<]*(says|shows) FAILED' "$f" && echo "still describes the old FAILED line"
    grep -q 'sigmond-setup --rac-o' "$f" && echo "sends the operator to the host for the switch (sigmond-setup --rac-on/--rac-off); the host has no keyboard"
    return 0
}

for d in $DOCS; do
    [ -f "$HERE/$d" ] || { echo "  FAIL $d: missing"; fail=1; continue; }
    out=$(findings "$HERE/$d")
    if [ -z "$out" ]; then echo "  ok   $d"
    else fail=1; printf '%s\n' "$out" | sed "s|^|  FAIL $d: |"; fi
done

# The two published documents also say what registration sends.
for d in INSTALL.md docs/install-page.html; do
    if grep -q "station's name" "$HERE/$d" && grep -q 'public' "$HERE/$d" && grep -qi 'comes up by itself' "$HERE/$d"; then
        echo "  ok   $d says remote access comes up by itself, and what registering sends"
    else
        echo "  FAIL $d: does not say that remote access comes up by itself and sends the station's name and the host's public key"; fail=1
    fi
done

# Positive control: the documents of the last release whose wizard asked must
# fail the same checks, or an 'ok' above proves nothing.
for d in $DOCS; do
    if ! git -C "$HERE" show "$CONTROL_REF:$d" > "$T/control" 2>/dev/null; then
        echo "  FAIL control: cannot read $d at $CONTROL_REF (no such ref in this checkout?), so the defect cannot be reproduced here"
        fail=1; continue
    fi
    if [ -n "$(findings "$T/control")" ]; then echo "  ok   control: $d at $CONTROL_REF fails these checks"
    else echo "  FAIL control: $d at $CONTROL_REF passes; these checks cannot tell old instructions from new"; fail=1; fi
done

[ "$fail" = 0 ] && echo "PASS: the installation documents name the remote-access switch" \
                || echo "FAILED: the installation documents and the remote-access switch"
exit "$fail"
```

- [ ] **Step 8: Run the test to verify it fails**

Run: `cd /home/mjh/hamsci/repos/sigmond-appliance && bash test-instructions-remote.sh | grep -c FAIL`
Expected: `15`: fourteen findings across the three documents and the closing `FAILED:` line. The three control
lines already read `ok`: the documents at the tag `v3.70` fail these checks, as they must.

- [ ] **Step 9: Change the three documents**

`INSTALL.md`:

(a) Find (lines 51-52):

```markdown
| Is this a DASI station? Its unit number | `Y`, `3` (for DASI-003) |
| Enable remote access? | `Y` (default, recommended) |
```

and replace it with:

```markdown
| Is this a DASI station? Its unit number | `Y`, `3` (for DASI-003) |
```

(b) Find (lines 214-215):

```markdown
(If the remote-access line says FAILED, don't worry — the support server may be
busy. Turn it on later with `sigmond-setup --reconfigure`.)
```

and replace it with:

````markdown
**Remote access comes up by itself.** The wizard asks nothing about it: every
station registers with the fleet gateway and opens a tunnel, so that your fleet
admin can support it. Registering sends two things: the station's name and the
host's public key. You hold the switch, in the decoder VM (§10 says how to log
in):

```
smd remote off        # turn remote access off; it stays off across reboots
smd remote on         # turn it on again
smd remote status     # show the state
```

(If the summary's remote-access line says `wanted on, not up`, don't worry: the
gateway did not answer, and the station tries again by itself every ten
minutes.)
````

(c) Find (lines 316-318):

```markdown
the same key on each. (If you restored keys in §4, this is already done.)

---
```

and replace it with:

```markdown
the same key on each. (If you restored keys in §4, this is already done.)

**Remote access starts on, and you can turn it off.** Log in to the decoder VM
and run `smd remote off`. `smd remote on` turns it on again, and
`smd remote status` shows the state. Off stops the tunnel your fleet admin uses
and changes nothing else: every upload keeps running, and off stays off across
reboots. The console panel shows which state holds. More:
[remote access](https://github.com/HamSCI/sigmond/blob/main/docs/operator/remote-access.md).

---
```

(d) Find (lines 332-334):

```markdown
| Remote access shows FAILED | Later, from the host: `sigmond-setup --reconfigure` |
| No spots after 30 minutes | Expected on a new station until you run `smd sink upload` (§9). Log in to the VM and run `smd sink status`. If it says `site sink: off` with the reason `new station: …`, check the station as §9 describes, then run `smd sink upload`. If it says `site sink: off` with any other reason, or `hold (legacy)`, ask your fleet admin. If it says `site sink: upload`: antenna actually connected? Then run `smd status` and send its output to your fleet admin |
| Anything else | If you enabled remote access, your fleet admin can log in and fix it — just ask |
```

and replace it with:

```markdown
| Remote access says `wanted on, not up` | Nothing to do: the gateway did not answer, and the station tries again every ten minutes. In the VM, `smd remote status` shows the state |
| You want remote access off | In the VM: `smd remote off`. `smd remote on` turns it on again |
| No spots after 30 minutes | Expected on a new station until you run `smd sink upload` (§9). Log in to the VM and run `smd sink status`. If it says `site sink: off` with the reason `new station: …`, check the station as §9 describes, then run `smd sink upload`. If it says `site sink: off` with any other reason, or `hold (legacy)`, ask your fleet admin. If it says `site sink: upload`: antenna actually connected? Then run `smd status` and send its output to your fleet admin |
| Anything else | Your fleet admin can log in through remote access and fix it — just ask. If you turned remote access off, run `smd remote on` in the VM first |
```

(e) Find (lines 364-365):

```markdown
   on the review screen. The wizard keeps your remote-access number and the
   site sink switch as they were.
```

and replace it with:

```markdown
   on the review screen. The wizard keeps your remote-access number, the
   remote-access switch and the site sink switch as they were.
```

`QUICKSTART.txt`:

(f) Find (lines 28-29):

```text
  Remote access                      Y (the default)
        Lets your fleet admin reach the station for support.
```

and delete it.

(g) Find (lines 132-135):

```text
   6) Enable remote access?  [Y/n]
   7) PSWS station ID, then the GRAPE and magnetometer numbers
   8) Send status heartbeats?  [Y/n] — press Enter to accept
   9) Station designator — press Enter to accept the suggestion
```

and replace it with:

```text
   6) PSWS station ID, then the GRAPE and magnetometer numbers
   7) Send status heartbeats?  [Y/n] — press Enter to accept
   8) Station designator — press Enter to accept the suggestion

REMOTE ACCESS COMES UP BY ITSELF; the wizard asks nothing about it.  You
hold the switch, in the decoder VM:   smd remote off   and   smd remote on
```

(h) Find (lines 177-179):

```text
  Remote access says FAILED
        The support server may be busy.  Turn it on later with
        sigmond-setup --reconfigure
```

and replace it with:

```text
  Remote access says "wanted on, not up"
        The gateway did not answer.  The station works, and it tries again
        every ten minutes by itself.  In the VM:  smd remote status
```

`docs/install-page.html`:

(i) Find (lines 120-121):

```html
<tr><td>Remote access</td>
    <td>Y<br><small>lets your fleet admin reach the station for support; the default</small></td><td><span class="fill"></span></td></tr>
```

and delete it.

(j) Find (lines 235-236):

```html
  on your machine.</p>
</div>
```

and replace it with:

```html
  on your machine.</p>
  <p><b>Remote access comes up by itself.</b> The wizard asks nothing about it: every
  station registers with the fleet gateway and opens a tunnel, so that your fleet admin
  can support it. Registering sends two things: the station's name and the host's public
  key. You hold the switch, and section 6 says how to work it.</p>
</div>
```

(k) Find (lines 320-321):

```html
<tr><td>The receiver shows up, but nothing decodes and the log says <span class="mono">RX888 ADC clock not locked/running</span></td><td>Unplug the USB 3 cable <b>at the RX888</b> and plug it firmly back in. A badly seated plug looks exactly like a missing clock, and a power cycle does not cure it.</td></tr>
<tr><td>Remote access says FAILED</td><td>The support server may be busy. The station works; turn it on later with <span class="mono">sigmond-setup --reconfigure</span> on the host.</td></tr>
```

and replace it with:

```html
<tr><td>The receiver shows up, but nothing decodes and the log says <span class="mono">RX888 ADC clock not locked/running</span></td><td>Unplug the USB 3 cable <b>at the RX888</b> and plug it firmly back in. A badly seated plug looks exactly like a missing clock, and a power cycle does not cure it.</td></tr>
<tr><td>Remote access says "wanted on, not up"</td><td>The gateway did not answer. The station works, and it tries again every ten minutes by itself. <span class="mono">smd remote status</span> on the recorder shows the state.</td></tr>
```

(l) Find (lines 359-361):

```html
</div>

<h3>Register for PSWS uploads</h3>
```

and replace it with:

```html
</div>

<h3>Turning remote access off and on</h3>
<p>Remote access starts on. To change that, log in to the recorder and type:</p>
<pre>smd remote off        turn it off; it stays off across reboots
smd remote on         turn it on again
smd remote status     show the state</pre>
<p>Off stops the tunnel your fleet admin uses and changes nothing else: every upload
keeps running. The status panel on the machine's screen shows which state holds.</p>

<h3>Register for PSWS uploads</h3>
```

(m) Find (lines 394-395):

```html
changed it; check it on the review screen. The wizard keeps your remote-access number and
the site sink switch as they were, and restarts the recorders itself.</li>
```

and replace it with:

```html
changed it; check it on the review screen. The wizard keeps your remote-access number,
the remote-access switch and the site sink switch as they were, and restarts the recorders
itself.</li>
```

If v3.70's own instructions have moved the version and file names in these documents since a0d7268, the anchors
above still stand: none of them names a version.

- [ ] **Step 10: Run the tests to verify they pass**

Run: `bash test-instructions-remote.sh | tail -1`
Expected: `PASS: the installation documents name the remote-access switch`.

Run: `bash test-bless-instructions.sh | tail -1`
Expected: `195 passed, 0 failed`.

- [ ] **Step 11: Commit the sigmond-appliance half**

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance
test "$(grep -c . .git/ATTRIBUTION)" = 2 || { echo "write .git/ATTRIBUTION first (Global Constraints)"; exit 1; }
git add INSTALL.md QUICKSTART.txt docs/install-page.html test-instructions-remote.sh
git commit -m "instructions: remote access comes up by itself; smd remote turns it off and on

INSTALL.md, the install page and the quick-start card drop the wizard's
old question from their lists of answers.  They say that every station
registers and opens a tunnel, what registering sends, and how the
operator switches it from the decoder VM.  A test holds all three to
that, and checks itself against the v3.70 documents, which must fail.

sigmond tasks/plan-remote-access.md section 5.

$(cat .git/ATTRIBUTION)"
```

### Task 12: build, test, prove on AC0G-ND and bless v3.71 (each external step waits for Michael's go)

Nothing here is code. Each step reaches outside the devbox, so each one waits for Michael's explicit go. Every
station step starts with a claude-bus read and post, and for B4 also a read of `/etc/sigmond/CLAUDE-COORDINATION.md`
on the VM (with the hyphen).

Three orders bind this task, and each has a reason.

- **v3.70 first.** `smd update` on a station follows `origin/main`. So nothing of v3.71 reaches `origin/main` of
  sigmond or sigmond-appliance until AC0G-B4 and AC0G-ND have taken v3.70 (Step 1).
- **wd30 before any host.** A host that sends a `rac` block to a server that does not know the name gets its whole
  heartbeat quarantined, and looks silent. `deploy-wd30.sh` carries the board, the ingest and the schema from the
  devbox's checkout by rsync, so it needs no push (Step 3).
- **The bless before the stations in the field.** `pm-align` reads only a blessed release, so B4 and AI6VN
  receive the host's half after Step 9.

- [ ] **Step 1: Confirm that v3.70 has reached AC0G-B4 and AC0G-ND** (read-only; bus read first).
  Read the bus and `sigmond/tasks/plan-sink-control-v370.md` Task 10: its Steps 6 and 7 must stand checked. If
  either station still runs an older release, stop here. Tasks 1 to 11 may sit committed and unpushed for as long
  as that takes.
- [ ] **Step 2: Full suites green** in both repos.
  `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest -q` must print
  `3728 passed, 1 warning, 65 subtests passed`. In sigmond-appliance, the loop of Task 10 Step 7 plus
  `bash test-instructions-remote.sh | tail -1` must print each test's PASS line.
- [ ] **Step 3: Teach wd30 the `rac` block** (bus post first; wd30 belongs to Michael).
  From the devbox: `cd /home/mjh/hamsci/repos/sigmond && server/heartbeat/deploy-wd30.sh`. It builds the roster
  from `ops/fleet.toml`, rsyncs `ingest.py`, `fleetboard.py`, `roster_check.py` and `heartbeat_schema.py` to
  `/opt/hamsci-fleetboard/`, and restarts the board and the ingest timer. Then open the board
  (`http://wd30:8090/`, LAN only). Expected: a new last column `rac` that reads `n/a` on every row, the same
  rows green as before, and a count of rejected uploads that has not risen. No station sends `rac` yet.
- [ ] **Step 4: Push** sigmond and sigmond-appliance `main` (Michael's go; only after Step 1). The golden VM clones
  each repo's latest `main` and fails when one sits behind its remote.
- [ ] **Step 5: Run the pre-tag adversarial hunt**, as before v3.69 and v3.70. Look for anything that breaks the
  image build, a first boot, the rigs, or an existing station on `smd update`. Three questions this release adds:
  does a v3.70 host that takes `smd update` in its VM keep everything it had (the VM gains `smd remote`, which
  says it holds no pairing; nothing else may change)? Does the pm-align that B4 and AI6VN hold accept the v3.71
  wizard and firstboot (Task 6's frozen list says yes; read the pm-align each host actually holds, to be sure)?
  And does any path register a station whose owner said No? Then tag `v3.71` in sigmond-appliance
  (`git tag -a v3.71`, push the tag) and build on the rig (root@192.168.1.182): pull both checkouts,
  `./sync-rig.sh`, `./build-golden-vm.sh`, `./build-usb-v3.sh --release`. `sync-rig.sh` must print
  `NEW    offline-debs/sudo_…_amd64.deb`, and the build's `offline-debs:` line must count one package more than
  v3.70's build did.
- [ ] **Step 6: Nested test** — `./test-nested-v3.sh` on the rig. Phase D must print
  `wizard answers: 12 (remote-access question: not asked)`, then these six lines, each ending in `✓`
  (the last one names in brackets how many tries it took):
  `wish on, not registered, retry timer armed, sudo and the pairing in place`;
  `status answered; a fourth word, extra words, no word and a second key all refused`;
  `registered through the stand-in: RAC 599, six channels declared, name and key sent and nothing else`;
  `off held across the reboot, and the panel calls it the operator's choice`;
  `on enabled the tunnel and the relays again`;
  `the host re-asserted the pairing after its sshd key changed`. It must then print
  `PHASE D PASS — NESTED TEST COMPLETE`, and must not print `REMOTE ACCESS CHECKS NOT EVALUATED`.
  The remote-access checks have never met a nest before this run (Task 10). When one of them fails, decide first
  whether the product or the rig holds the fault. Fix a rig fault in sigmond-appliance past the tag, as v3.70's
  run did; a product fault means a new build. This step also settles three things the plan could not verify:
  that sshd on a Proxmox host honours the pairing's key line, that sudo's dependencies all sit on stock Proxmox,
  and that the path unit fires when a key file gets replaced.
- [ ] **Step 7: Update test** — `./test-update-v3.sh --image sigmond-appliance-v3.70-<date>-release.img`, with the
  blessed v3.70 image's own file name. It rolls blessed v3.70 forward to `main` and back. PHASE E, E-bis, E-ter,
  F, G and G-bis must each print their PASS line. E-ter's line reads
  `PHASE E-ter PASS — a host from <image> pairs with one command, and its owner's No still stands`. Read one more
  line of E-ter: `the base host carries no sudo: the pairing must fetch it`. It confirms what ruling 9 assumed. If
  it reads `already carries sudo` instead, say so to Michael: the offline package and the apt step then guard
  against nothing.
- [ ] **Step 8: Reflash AC0G-ND with the v3.71 image** — the hardware proof of spec phases 1 to 3 and 5 (bus
  first). Michael flashes and installs; follow `INSTALL.md` word for word and mark every place where the text and
  the machine disagree. Check, in this order:
  1. The wizard asks whether this is a DASI station and asks nothing about remote access. It prints the three
     `smd remote` commands and says what registering sends.
  2. The closing summary's `RAC:` line reads `on`, with `6 of 6 channels`, and its `Switch:` line reads
     `paired: in the decoder VM, smd remote on / off / status`.
  3. After the power cycle the console panel reads `Remote access   ON — RAC <n> on <gateway>`.
  4. A login to the VM prints the banner's line, `Remote access: on (as the host reported …)`.
  5. `smd remote status` in the VM answers `Remote access: on`.
  6. **From ND's own network, never through the tunnel**, type `smd remote off`. It must answer
     `Remote access: off` and exit 0. The gateway must then show none of ND's six channels, and the fleet's reach
     to ND through the gateway must fail. The panel must read `OFF, by the operator's choice … — not a fault`.
     Within ten minutes the fleet board's row for ND's host must show `off` in its `rac` column and the tag
     `remote access off by the operator`, with the row still green.
  7. Power-cycle the station. Remote access must still read off: panel, banner and `smd remote status`.
  8. `smd remote on`. It must answer `Remote access: on` with `6 of 6 channels`, and the fleet's reach must work
     again.
  9. Run the identity restore for ND, if this reflash calls for one. It replaces the host's sshd key. Once
     the host's sshd presents the new key (the restore's own restart, or a reboot), `smd remote status` must
     answer within two minutes, and nobody types a pairing command: the pairing re-asserted itself. Note
     whether `smd remote status` failed in between, and for how long (see "What this plan could not verify").
     This is the AC0G-B4 fault of 2026-10-01, met on hardware.
  Record each of the nine in spec `tasks/plan-remote-access.md`, under a new heading "v3.71 hardware proof".
- [ ] **Step 9: The instructions, then the bless.** Edit `INSTALL.md` in sigmond-appliance: keep
  `**Status:** current`, and make the `**Verified against:**` line name `sigmond-appliance v3.71`, the image's
  commit and sha256, the nested run and the AC0G-ND install of Step 8. Bring `docs/install-page.html` level with
  it: the image's exact file name wherever a file name stands, and the footer's version and date. Correct every
  place Step 8 marked. In sigmond, set the `Verified against` line of `docs/operator/remote-access.md` to the
  hardware run and drop its sentence "A hardware install has not yet confirmed it." Commit both, push both
  (Michael's go). Then `./bless-release.sh v3.71` on the rig: read its gates, gate 7 above all, and Michael
  runs `--apply` at a terminal there.
- [ ] **Step 10: Bring AC0G-B4's host and AI6VN's host to v3.71** (after the bless; bus first; for B4 the
  coordination doc first; AI6VN needs Rob's go as well). On each station:
  1. In the VM, bring sigmond to v3.71 the way Michael chooses for that station (`smd align` to the blessed
     release, or `smd update --apply`). `smd remote status` then exits 5 and names `sigmond-setup --pair-vm`:
     the VM holds no pairing yet.
  2. On the Proxmox host, read the plan first: `pm-align`. Then `pm-align --apply`. The copy that runs is the one
     the host already held, so it prints no `pairing:` line. It installs the v3.71 wizard, panel and heartbeat,
     and a new copy of itself.
  3. On the host: `sigmond-setup --pair-vm`. Expected: `paired: the decoder VM can run smd remote on, smd remote
     off and smd remote status`. A host whose guest agent stands deaf cannot pair; clear the agent first (one
     0xFF byte down its socket, as the v3.70 plan's carry list describes). A host without sudo fetches it here.
  4. In the VM: `smd remote status`. On B4 and AI6VN, whose owners said Yes, it must read `Remote access: on`,
     and nothing about the tunnel may have changed: same RAC number, same channels, no restart of
     `sigmond-rac-host` (`systemctl show sigmond-rac-host -p ActiveEnterTimestamp` before and after).
  5. Within ten minutes the fleet board's row for that host shows `ok` in its `rac` column.
  Do not type `smd remote off` on either station as a test. ND proved it; these two carry other work.
- [ ] **Step 11: Republish and record.** Rebuild the shared page "Installing a Sigmond Station"
  (https://claude.ai/artifact/WvLUettP4ViRFYu6sftctf) from the blessed `INSTALL.md` and
  `docs/install-page.html`. Set the Status line of `sigmond/tasks/plan-remote-access.md` to what shipped and
  what waits (the list below), and close the remote-access item in the v3.70 plan's "Carried to v3.71" list
  with a pointer to this plan.

---

## Follow-up work, outside this plan

- **The registrar over TLS, and what a station's token alone allows** (spec section 3). The gateway must serve
  its registrar over https first; that work belongs to the gateway. The station then needs two words changed in
  `build_rac_tiers`. Until someone checks the token, assume that whoever reads it in transit can open that
  station's tunnel account.
- **`smd remote` on a station with no Proxmox host** could drive the VM-side tunnel (`wd-rac.service`), as the
  spec allows. Today it points at `smd admin rac`.
- **A station's wish across a reflash.** `ops/bin/site-identity` and `ops/bin/site-restore.sh` live outside these
  two repos. Neither carries `/etc/sigmond-appliance/rac-desired`, so a reflash turns remote access on again
  (ruling 5). An owner who keeps it off may want the restore to put that back.
- **`ops/bin/site-restore.sh b4` carries the repair for the very key whose loss stopped it reaching B4** (spec,
  "Found on the way"). A repair that travels over the broken path needs a second way in.
- **Other stations in the field** (K3LR, the DASI units): each pairs through Step 10's three commands when its
  owner and its admin agree. Nothing in this plan touches them.
- **pm-align could restart itself after it installs a newer copy of itself.** Today the first run after a
  release uses the old code, which is why Step 10 types `sigmond-setup --pair-vm` by hand.
- **The wizard now runs to about 3000 lines** and carries a second script inside it (ruling 1). If that grows
  again, split the host files out and teach both delivery paths about them in one release.
- **`sigmond-setup --rac-upgrade`** still reruns the whole wizard to climb to a better gateway tier. With
  registration alone in hand, it could register afresh and ask nothing.
- **The other v3.71 items of the v3.70 plan's carry list** stay their own work: the guest agent's reset byte and
  the test that no guest-agent argument holds a non-ASCII byte, the agent's 128 MB cap, the RX888 alarm, the udev
  rules that `smd align` leaves behind. This plan obeys the first two for its own code and fixes none of them
  for the rest.
