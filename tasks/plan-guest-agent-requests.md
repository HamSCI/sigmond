# Plan — guest-agent requests that cannot deafen the agent

**Authors:** Michael Hauan (AC0G) + Claude
**Date:** 2026-10-10
**Status:** design, for Michael's review. No code yet. Target release: v3.71.
**Verified against:** sigmond bfd3add on 2026-10-10 — `scripts/proxmox/sigmond-wizard.sh`,
`scripts/proxmox/pm-align.py`, `scripts/proxmox/pm-heartbeat.py`; sigmond-appliance 5e8e23a `firstboot-v3.sh`;
the rig measurements of 2026-10-08 (Proxmox VE 9.1.1, qemu-server 9.0.30)

## Goal

No request that a station's Proxmox host sends to the decoder VM's guest agent carries a byte outside ASCII.
A host that finds the agent silent resets the agent's parser before it concludes anything, and says that it
did. The console panel's bring-up line, which has never once run, runs and tells the truth.

## Why

Proxmox writes a guest-agent request that holds non-ASCII characters short. It sends as many bytes as the
request has characters, so the closing bytes never arrive. The rig showed what follows, on 2026-10-08:

- Most of the time the cut lands inside a string. The agent then drops the next request, from anyone, and
  recovers.
- About one time in six the cut lands between two tokens. The agent's parser waits for the rest and never gets
  it. The agent keeps its process, reads every later request and answers none. Only a restart inside the VM,
  a reboot, or one reset byte from the host ends that.

Every host that runs the console panel's timer sends such a request every five minutes. The panel asks the VM
for the last bring-up step with `grep -E "^───|» "`, and those characters make the request non-ASCII. That
command has never run on any station.

A deaf agent costs more than a blank line on the panel. The wizard cannot configure the VM, `pm-align` cannot
copy its record into the VM, the host's heartbeat reports the station INVALID, and `qm shutdown` waits out a
timeout before it falls back to ACPI. AC0G-B4, AC0G-ND and K3LR have each shown it. On 2026-10-09 AC0G-ND's
agent failed 93 calls in one hour, until one reset byte from its host cleared it. AC0G-B4's host runs no panel
timer today, and its agent answers; a reflash gives B4 the panel.

Michael placed the fix in v3.71.

## What exists today (do not rebuild it)

- **Four programs on the host talk to the agent.** The console panel (`/usr/local/sbin/sigmond-issue`, written
  by a heredoc in `firstboot-v3.sh`) asks for the VM's address, the bring-up state, ka9q-web's state and the
  USB list. The wizard sends its configuration through two helpers, `gexec` and `guest_out` (48 calls), and
  through four direct calls. `pm-align` copies one record into the VM, already base64-encoded. `pm-heartbeat`
  runs `/usr/bin/true` in the VM as a check.
- **firstboot's import script** and the split console's push also call the agent, at install.
- **The reset exists in the protocol.** One byte, 0xFF, resets the agent's parser. Proxmox never sends it. It
  cured a deaf agent on the rig and on AC0G-ND (2026-10-09 13:13Z), with no restart inside the VM.
- **The wizard already heals a silent agent, the slow way.** After 30 seconds of silence it restarts the agent
  inside the VM, over ssh on the private link. Its comment records one trigger seen on AI6VN, an interrupted
  wizard. That cure needs the private link and a working key; the reset byte needs neither.
- **The heartbeat already reports a silent agent**: `guest-agent-wedged`, which the fleet board draws INVALID.
- **A drop-in inside the VM** caps the agent at 128 MB. It ends a different wedge, a runaway output buffer.
  It cannot see this one: nothing dies and nothing grows.
- **`pm-align` delivers** the panel, its unit and timer, `pm-heartbeat` and itself to a station in the field.
  It delivers no other host file out of `firstboot-v3.sh`.
- **The update rig** already holds a defective panel off during its update phases, and a logging watcher
  stands beside the base install.

## Non-goals

- Fixing Proxmox. This plan drafts a report for Proxmox's bug tracker; Michael decides whether to send it.
- The agent's 128 MB cap and what runs under it. That item has its own place on the v3.71 list.
- Any change to what the panel shows, beyond the bring-up line.
- A new file or a new service on the host.

## Design

### 1. The panel's request stays ASCII

The panel builds the two patterns inside the VM, from octal escapes that the VM's own `printf` turns into
bytes. The request that crosses the agent then holds ASCII alone.

Today the panel sends two requests for its bring-up line: one for the unit's state and the last step, and a
second for ka9q-web when the first reads `inactive`. One request can carry all of it: the unit's state, the
result that the bring-up marker records, ka9q-web's state, and the last step. That halves the traffic and
removes a window between the two answers.

The answer comes back as ASCII too. The agent's output reaches the host double-encoded, so the VM strips the
step line to printable ASCII before it sends it.

### 2. The bring-up line tells the truth

The command has never run, so none of the branches behind it has ever executed on a station. One of them can
raise a false alarm: on `inactive` it prints "BRING-UP FINISHED BUT ka9q-web IS NOT RUNNING" without looking
at the marker that its own comment names. Before the wizard runs, bring-up has not started, the unit reads
`inactive`, and ka9q-web does not run.

The bring-up unit reads `inactive` whenever it does not run, finished or not. The marker tells the two apart:
`sigmond-firstrun-bringup` writes its result there (`ok`, `retry`, `awaiting-sdr`, `gave-up`). The panel reads
that result.

| The VM answers | The panel prints |
|---|---|
| unit `activating` | "STATION IS STILL BUILDING", with the last step |
| unit `failed` | systemd ended bring-up (its 90-minute limit, or a kill), and where the log lies |
| no marker | nothing: bring-up has not run yet |
| marker `retry` | bring-up failed and tries again at the next boot |
| marker `awaiting-sdr` | bring-up ran without the radio and finishes when the SDR appears |
| marker `gave-up` | bring-up failed three times and has stopped trying |
| marker `ok`, ka9q-web active | nothing: the station stands finished |
| marker `ok`, ka9q-web not active | "BRING-UP FINISHED BUT ka9q-web IS NOT RUNNING" |
| no answer, after one reset (section 4) | "the decoder VM's guest agent does not answer", and nothing about building |

A marker from before results existed carries no result line; the panel reads it as `ok`, as the bring-up script
does.

Today's alarm also tells the operator to run `smd bringup` through the guest agent. The agent's 128 MB cap
covers every process that command starts, and a bring-up compiles some 25 components. When a process passes
the cap, the kernel kills it and the agent's unit stops with it; the rig showed that on 2026-10-08 with a
lighter command. The new lines send the operator into the VM (`sigmond-vm`) and to the log, whose last lines
name the command to run.

A hermetic test cuts the block out of the shipped file, as the remote-access test does, and runs it against a
stand-in `qm` for each row. The stand-in records every request and fails the test on any byte above 0x7F.

### 3. One sender per program, and it encodes

A list of forbidden characters would miss the next one. An operator's own answer can hold one too: a name
with an accent, a password. So wherever a variable enters a request, the request goes through one function,
and that function base64-encodes the command. The VM decodes and runs it. The request holds ASCII whatever
the command holds.

- **The wizard:** `gexec` and `guest_out` encode. Their 48 callers do not change. A trial of the encoded form
  against the direct form gave the same output and the same exit code on twelve commands; only the prefix of
  a syntax error's message differs.
- **`pm-align`** encodes already.
- **The panel, and the direct calls elsewhere,** send fixed ASCII text and stay as they stand. A guard test
  reads the shipped files, finds every `qm guest exec`, and fails when one outside a sender holds a byte above
  0x7F or any variable beyond the VM's number.

The decoded command arrives as an argument, never on the shell's standard input. The remote-access work
showed why: a command that reads its input swallows the rest of a script piped to the shell.

### 4. A silent agent gets one reset before any verdict

The panel, the wizard's waits, `pm-align`'s record copy and the heartbeat's check each do the same thing when
a ping fails: write 0xFF and one sync request to the agent's socket, then ping again. Only a second silence
counts as silence.

The wizard keeps its restart over ssh as the second resort, after the reset byte has failed.

The panel runs every five minutes on every station. So a station whose agent stands deaf today heals within
five minutes of its host's alignment, with nobody at the keyboard, and stays healed because the cause has gone.

No new program carries this. Each of the four holds the same few lines: two in bash with a `python3`
one-liner, two in Python. A test pins the bytes they send as equal. A trial against a stand-in socket showed
the bash form answer in under a second when the agent replies, and give up after four seconds when it does
not. The reason for four copies: `pm-align`
delivers no new host file out of firstboot, and `pm-heartbeat` must run with nothing beside it.

A reset must leave a trace, or the fleet learns nothing. The panel and the heartbeat both run every five
minutes, so the panel will often reset the agent before the heartbeat looks. The heartbeat would then report
a healthy agent, beat after beat, over a fault that returns each time.

- **Whoever resets the agent and gets its answer writes one line** to `/run/sigmond/qga-reset`: the time and
  its own name. The line replaces the one before, so the file never grows.
- **The heartbeat reads that line** and reports a reset since its last beat as a finding of its own. After
  this fix no station should show one. A station that does then names a new cause.
- **The panel** says in one line when the agent stays silent after a reset.
- **Nothing writes a log line per refresh.** A condition that persists gets one line per reason.

One risk: a reset that lands in the middle of another caller's request loses that request. That caller fails
once and tries again. A reset follows a failed ping only, so the case stays rare. Both proven resets ran while
nothing else held the agent's socket; the rig must show what a reset does while another request holds it.

### 5. The rigs prove it on real Proxmox

The nested rig gains three checks:

1. After a dozen forced panel refreshes, every ping answers. On 2026-10-08 the fault hit four of five base
   installs on the rig, in one form or the other.
2. The rig sends a non-ASCII request of its own until the agent stands deaf, within a bounded number of tries.
   It then runs the panel once and requires the agent to answer. That proves the reset where it counts.
3. During bring-up the panel says "still building"; after it, the panel says nothing.

The update rig keeps its hold on a v3.70 base's panel. It already tests a panel's text for the defect, so it
lets a clean panel run.

### 6. Stations in the field

The panel and the heartbeat reach a station when `pm-align` aligns its host. `smd update` never carries them.
Until a host aligns, its agent goes deaf as before, and one reset from the host cures it for about half an
hour.

### 7. A report for Proxmox

The plan drafts the report: the truncation, the versions, and a reproduction in three commands. It stays a
file until Michael sends it.

## Decisions for Michael

1. **Does the host reset a silent agent by itself, or only report it?** I recommend that it resets and
   reports. The reset costs nothing when the agent merely boots, and a station nobody can reach needs it most.
2. **Which colour does one reset earn on the fleet board?** I recommend amber for that beat (INCONCLUSIVE):
   the station works again, and someone should know it happened. A silence that survives the reset stays red.
3. **The panel reads the bring-up marker's result** and prints one short notice for each unfinished state
   (`retry`, `awaiting-sdr`, `gave-up`, and a unit that systemd ended). Today all of them share the one alarm.
4. **Before bring-up has run, the panel prints nothing** about it. The wizard's own prompt already holds the
   screen then.
5. **Do we send the report to Proxmox?**

## Phases

Each phase ships alone and leaves a station working.

1. **The panel's request and its bring-up line** (sigmond-appliance), with the hermetic test.
2. **The senders encode** (the wizard, the panel), with the guard test over both repositories' files.
3. **The reset** in the four programs, and the heartbeat's finding.
4. **The rigs.**
5. **The words:** the operator's troubleshooting page, the release notes, and the draft for Proxmox.

The hardware proof of v3.71 moves to AC0G-B4. Michael leaves AC0G-ND on 2026-10-12 and can reflash B4 at home.

## Found on the way, not part of this plan

- Several `qm agent` calls run with no timeout: the wizard's waits, firstboot's import script, and the
  `sigmond-vm` helper that the wizard writes. The panel's own comment records that such a call can hang.
  Phase 3 bounds the ones it touches; the rest want the same.
- The panel's comment blames a silent agent on "the VM is saturated building venvs". From 2026-09-27 to 2026-10-08
  that explanation hid this fault. Phase 1 corrects the comment.
- `pm-heartbeat.py`'s header describes the wedge as a reset connection. Phase 3 corrects it.
