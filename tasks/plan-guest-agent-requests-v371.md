# Guest-agent requests (v3.71) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No request from a station's Proxmox host can deafen the decoder VM's guest agent, a silent agent gets
one parser reset before any verdict, and the console panel's bring-up line tells the truth.

**Architecture:** The console panel builds its two non-ASCII patterns inside the VM and reads the bring-up
marker's result through one ASCII request. The wizard's two senders base64-encode every command. The panel, the
wizard, `pm-align` and `pm-heartbeat` each carry the same few lines that send one 0xFF byte down the agent's
socket; whoever gets an answer leaves one line in `/run/sigmond/qga-reset`, and the heartbeat reports it once.

**Tech Stack:** bash; Python 3.11, standard library only; pytest (sigmond); hermetic bash tests (sigmond-appliance).

**Spec:** `tasks/plan-guest-agent-requests.md` (design of 2026-10-10). Read it before Task 1. Where this plan and
the spec disagree, the spec wins.

**Status:** written 2026-10-10 against sigmond 28a5aac and sigmond-appliance c348e21. ✅ **Michael decided the design's five
questions on 2026-10-11, as recommended.** This plan follows the answers the design recommends to its five decisions: the host
resets by itself and reports; one reset reads INCONCLUSIVE on the fleet board; the panel reads the marker's
result; before bring-up the panel prints nothing; the report to Proxmox stays a draft. A different answer changes
Task 3 (decisions 1 and 2) or Task 1 (decisions 3 and 4), and nothing else.

## How this plan carries its code

Every line of code below has already run. The controller built the whole change in a scratch copy of both
repositories on 2026-10-10 and measured it:

- sigmond's full suite with the change applied: 4080 tests, none failing (3975 before, plus 105 new).
- The appliance's hermetic tests with the change applied: the two new ones (58 and 40 checks) and every
  existing one that reads a changed file.
- 48 mutations of the changed code, each on a copy; every one turned a named test red. Each task lists its own.

So each task comes as two patch files, tests first and code second, and the same diffs stand in this plan for
reading. The patch files sit in this plan's workspace, which git ignores:
`/home/mjh/hamsci/repos/sigmond/.superpowers/sdd/plan-guest-agent-requests-v371/proto/` (a second copy:
`~/v371-qga-proto/`). `git apply --check` passed for all eight against the commits named above.

Your work in each task: apply the tests, watch them fail for the reason this plan names, apply the code, watch
them pass, repeat the mutations, read the diff as a reviewer would, and commit. **If a patch no longer applies,
the tree has moved.** Do not force it. Read the hunk, find where its anchor went, and make the same edit by hand;
then say so in your report. If a test fails for a reason this plan does not name, stop and report it: the
prototype may rest on something that changed.

The rig checks of Task 4 form the one exception. Their probes ran against stand-ins. The rig's own lines that
call them have only passed `bash -n`. Nothing in this plan has met a real Proxmox host.

## Global Constraints

- **No byte above 0x7F may cross to the guest agent**, in any request these tasks add or touch.
- **The reset's bytes**, the same in all four programs:
  `b"\xff" + b'{"execute":"guest-sync-delimited","arguments":{"id":4242}}\n'`.
- **The reset's record**: `/run/sigmond/qga-reset`, one line `<UTC> <who>`, replaced at each write. `<UTC>` reads
  `YYYY-MM-DDTHH:MM:SSZ`. `<who>` reads one of `panel`, `wizard`, `pairing`, `pm-align`, `heartbeat`. Only a
  reset that the agent answered leaves the line.
- **A reset follows a failed ping, never a hunch.** A request that fails while the agent still answers a ping
  brings no reset and no record.
- **No new file on the host and no new unit.** Each program carries its own copy of the reset.
- **No log line per refresh, per beat or per try** for a condition that persists.
- **`pm-align` on stations in the field reads heredocs out of both scripts with a frozen reader.** An opener must
  end at its delimiter. The line `cat > /usr/local/sbin/sigmond-issue <<'ISSEOF'` in `firstboot-v3.sh` and the
  line `cat > /usr/local/sbin/sigmond-rac-switch <<'RACSWEOF'` in the wizard keep their bytes. These tasks add no
  heredoc to either script.
- **The heartbeat's wire contract stays as it stands**: `KIND`, `SCHEMA_VERSION`, `VERDICTS`, `PRECEDENCE`. A
  finding's `kind` travels as free text.
- **Nothing heavy goes through the guest agent**, and never `smd`: the agent's 128 MB cap covers every process a
  request starts.
- **Two repositories, both on `main`.** Tasks 1 and 4 commit in `/home/mjh/hamsci/repos/sigmond-appliance`; Tasks
  2 and 3 in `/home/mjh/hamsci/repos/sigmond`; Task 5 in both. Commit by path. Never `git add -A`. Never push.
- **Tests:** in sigmond, `.venv/bin/pytest` (never a bare `pytest`). Try a mutation on a COPY of the file, never
  in the checkout: the new tests read the copy's path from `SIGMOND_TEST_WIZARD`, `SIGMOND_TEST_PROXMOX` (a
  directory that holds `pm-heartbeat.py` and `pm-align.py`) and `SIGMOND_TEST_FIRSTBOOT`; the appliance's tests
  take the file as their first argument.
- **Reach nothing outside this machine.** No station, no rig, no gateway. Run no `smd`, `sigmond-setup` or
  `pm-align` from either checkout against this machine.
- **Prose** (comments, test names, documents, commit messages): short sentences, active voice that names who
  acts, few forms of "to be", times in UTC. A private style document exists on this machine; do not open it, quote
  it or point to it from any file.
- After the last commit of a task that changed code: `graphify update /home/mjh/hamsci/repos`. Never
  `graphify update .`.

## Review Focus

What the spec implies and a plain reading of the tasks could miss, most likely first. Each line names the test
that pins it.

1. **An operator's typed answer with a non-ASCII character inside a command.** The wizard must deliver it whole
   and keep the request ASCII. Task 2: `test_an_operators_answer_with_an_accent_reaches_the_guest_whole`.
2. **A request that times out while the agent still answers.** It must bring no reset and no record, or the
   record stops meaning "silent, then brought back". Task 2:
   `test_a_request_that_times_out_under_a_hearing_agent_brings_no_reset`.
3. **A reset that gets no word back**, as from a socket that another request occupies. Every caller must give up
   within seconds; the panel runs under a lock. Task 3:
   `test_a_reset_that_gets_no_word_gives_up_within_seconds`. What a reset does to that other request, no test
   here can show: the rig must (see "What this plan could not verify").
4. **A record written by another program, damaged or hostile.** The heartbeat must send printable text of bounded
   length, once. Task 3: `test_the_record_travels_as_printable_text_of_bounded_length`,
   `test_the_heartbeat_takes_the_record_once`.
5. **A bring-up marker from before results existed, and a unit state the panel does not know.** The first reads as
   `ok`; the second falls through to the marker. Task 1: "a marker with no result line reads as ok", "a unit state
   the panel does not know, no marker: prints nothing".

## File structure

| File | Repository | Task | What changes |
|---|---|---|---|
| `firstboot-v3.sh` | sigmond-appliance | 1 | The panel's guest-agent block: the reset, one ASCII status request, the bring-up line by the marker's result, a bounded `lsusb` request, two markers around the block |
| `test-panel-agent.sh` (new) | sigmond-appliance | 1 | Runs that block against a stub `qm` and a stand-in socket; scans every request in `firstboot-v3.sh` |
| `scripts/proxmox/sigmond-wizard.sh` | sigmond | 2 | `qga_b64`, `QGA_RUN`, `qga_reset`, `qga_note_reset`; `gexec` and `guest_out` encode; one reset in `gexec`'s wait, in the pairing's wait and in the wizard's own wait |
| `tests/test_wizard_guest_senders.py` (new) | sigmond | 2 | The senders against a stub `qm` that runs each request; the wizard's own wait; the scan of the wizard's direct requests |
| `tests/test_wizard_pair_vm.py` | sigmond | 2 | Two stubs in the harness, three tests of the pairing's reset |
| `scripts/proxmox/pm-heartbeat.py` | sigmond | 3 | The reset, the record, the finding `guest-agent-reset`, INCONCLUSIVE for it alone |
| `scripts/proxmox/pm-align.py` | sigmond | 3 | One reset before `push_record` gives up |
| `tests/test_qga_reset.py` (new) | sigmond | 3 | The four copies of the reset, the record, the heartbeat's and pm-align's behaviour around a reset |
| `test-nested-v3.sh` | sigmond-appliance | 4 | A marked block of five probes; three places that call them |
| `test-qga-rig-lines.sh` (new) | sigmond-appliance | 4 | Runs the five probes against stand-ins |
| operator pages, the three appliance documents, `CLAUDE.md`, the v3.70 plan's carry list, a draft report | both | 5 | The words |

---

### Task 1: the console panel asks in ASCII, resets a silent agent once, and reads the marker's result

**Files:**
- Create: `/home/mjh/hamsci/repos/sigmond-appliance/test-panel-agent.sh` (mode 755)
- Modify: `/home/mjh/hamsci/repos/sigmond-appliance/firstboot-v3.sh` (inside the heredoc that writes
  `/usr/local/sbin/sigmond-issue`; from the line `VMIP=""; VMNOTE=""` to the end of the RX888 notice)

**Interfaces:**
- Consumes: nothing from another task.
- Produces: in the panel script, `qga_reset <vmid>` (0 when the agent answered) and `qga_note_reset <who>`; the
  markers `# >>> agent_block` and `# <<< agent_block`; the three names a test may set: `SIGMOND_QGA_DIR`,
  `SIGMOND_QGA_NOTE`, `SIGMOND_RX888_MARK`. Task 3's test compares the body of this `qga_reset` with the
  wizard's, line for line. Task 4's probes grep the panel for the word `qga_reset` and for these sentences, so
  keep them to the letter: `STATION IS STILL BUILDING`, `BRING-UP FINISHED BUT ka9q-web IS NOT RUNNING`,
  `bring-up ran WITHOUT the radio`, `BRING-UP FAILED -- it tries again`, `BRING-UP FAILED THREE TIMES`,
  `BRING-UP WAS STOPPED BEFORE IT FINISHED`, `guest agent does not answer`.

**What you need to know:**
- The panel script runs on the Proxmox host every five minutes (`sigmond-issue.timer`) and paints `/etc/issue`.
  It lives in `firstboot-v3.sh` as a heredoc. `pm-align` delivers it to stations in the field by reading that
  heredoc, so the opener line must keep its bytes.
- Up to v3.70 the panel asked the VM for the last bring-up step with `grep -E "^───|» "`. Those characters made
  the request non-ASCII; Proxmox wrote it short; the command never once ran. So the code behind it has never run
  either, and one branch raised a false alarm (spec section 2).
- The bring-up unit (`sigmond-firstrun-bringup.service`, oneshot, `RemainAfterExit=no`) reads `inactive` whenever
  it does not run. Its script writes `/var/lib/sigmond/.firstrun-bringup-done` with a line `result=ok`,
  `result=retry`, `result=awaiting-sdr` or `result=gave-up`; a marker from before results existed carries no
  such line and counts as final (`sigmond/bin/sigmond-firstrun-bringup`).
- The old alarm told the operator to run `smd bringup` through the guest agent. The agent's 128 MB cap kills
  that. The new lines send the operator into the VM with `sigmond-vm` and to the log.
- The update rig holds a defective panel off by its text: `PANEL_DEFECT_RE='grep -E "\^[^ -~]'`
  (`test-update-v3.sh`). The new request reads `grep -E "$(printf ...` and must not match that pattern.

- [ ] **Step 1: Add the test.**

  ```bash
  cd /home/mjh/hamsci/repos/sigmond-appliance
  P=/home/mjh/hamsci/repos/sigmond/.superpowers/sdd/plan-guest-agent-requests-v371/proto
  git apply --check $P/task-1-tests.patch && git apply $P/task-1-tests.patch
  chmod 755 test-panel-agent.sh
  ```

  The file it adds:

  ````diff
  diff --git a/test-panel-agent.sh b/test-panel-agent.sh
  new file mode 100755
  --- /dev/null
  +++ b/test-panel-agent.sh
  @@ -0,0 +1,269 @@
  +#!/bin/bash
  +# test-panel-agent.sh — the console panel's guest-agent block: every request
  +# ASCII, one reset before any verdict, and a bring-up line that tells the
  +# truth.  No VM, no root; a few seconds.
  +#
  +# Why: up to v3.70 the panel asked the decoder VM for the last bring-up step
  +# with a grep for two box-drawing patterns.  Proxmox writes a request that
  +# holds non-ASCII characters short, and about one time in six the guest agent
  +# then stands deaf for good (sigmond tasks/plan-guest-agent-requests.md).
  +# That request never once ran, so nobody had watched the code behind it: its
  +# `inactive` branch raised "BRING-UP FINISHED BUT ka9q-web IS NOT RUNNING" on
  +# a station whose bring-up had not started.
  +#
  +# The block under test is cut out of firstboot-v3.sh from between its two
  +# markers, so this runs what ships.  A stub stands in for `qm` and records
  +# every argument; a stand-in socket plays the agent's side of a reset.
  +#
  +#   usage: ./test-panel-agent.sh [path/to/firstboot-v3.sh]
  +set -u
  +HERE="$(cd "$(dirname "$0")" && pwd)"
  +SRC="${1:-$HERE/firstboot-v3.sh}"
  +T=$(mktemp -d); trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$T"' EXIT
  +pass=0; fail=0
  +ok(){  printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
  +bad(){ printf '  FAIL  %s\n        %s\n' "$1" "${2:-}"; fail=$((fail+1)); }
  +has(){   case "$2" in *"$3"*) ok "$1" ;; *) bad "$1" "missing: $3" ;; esac; }
  +lacks(){ case "$2" in *"$3"*) bad "$1" "found: $3" ;; *) ok "$1" ;; esac; }
  +
  +sed -n '/^# >>> agent_block/,/^# <<< agent_block/p' "$SRC" > "$T/block.sh"
  +[ -s "$T/block.sh" ] || { echo "FAIL: no agent_block between its markers in $SRC"; exit 1; }
  +bash -n "$T/block.sh" || { echo "FAIL: the agent_block does not parse"; exit 1; }
  +
  +# The bytes every reset must send (the same literal stands in sigmond's
  +# tests/test_qga_reset.py).
  +RESET_HEX='ff7b2265786563757465223a2267756573742d73796e632d64656c696d69746564222c22617267756d656e7473223a7b226964223a343234327d7d0a'
  +
  +# agent <scene dir> : the agent's side of the socket.  It records what
  +# arrives, answers the sync, and from then on the scene's agent hears again.
  +agent(){
  +    local d="$1"; mkdir -p "$d/qga"
  +    python3 - "$d" <<'PY' &
  +import os, socket, sys
  +d = sys.argv[1]
  +srv = socket.socket(socket.AF_UNIX); srv.bind(d + "/qga/100.qga"); srv.listen(1)
  +srv.settimeout(5)          # a panel that never resets must not hang this test
  +open(d + "/agent.ready", "w").close()
  +try:
  +    c, _ = srv.accept()
  +except Exception:
  +    sys.exit(0)
  +c.settimeout(5)
  +data = b""
  +try:
  +    while not data.endswith(b"\n"):
  +        got = c.recv(4096)
  +        if not got: break
  +        data += got
  +except Exception:
  +    pass
  +open(d + "/reset.bytes", "wb").write(data)
  +try: os.unlink(d + "/deaf")
  +except FileNotFoundError: pass
  +c.sendall(b"\xff" + b'{"return": 4242}\n'); c.close()
  +PY
  +    local i; for i in $(seq 1 50); do [ -e "$d/agent.ready" ] && return 0; python3 -c 'import time; time.sleep(0.05)'; done
  +    echo "FAIL: the stand-in agent did not start"; exit 1
  +}
  +
  +# panel <scene dir> : run the block as sigmond-issue would; print what it set.
  +# A scene holds: deaf (the agent answers nothing), status (the four lines the
  +# VM would send), sdr (lsusb shows an RX888), rxmark (the wizard's marker).
  +BLOCK="$T/block.sh"
  +panel(){
  +    local d="$1"
  +    (
  +        VMID=100
  +        export SIGMOND_QGA_DIR="$d/qga" SIGMOND_QGA_NOTE="$d/run/qga-reset" SIGMOND_RX888_MARK="$d/rxmark"
  +        mkdir -p "$d/calls"
  +        qm(){
  +            printf '%s\0' "$@" > "$d/calls/$(date +%s%N)"
  +            case "$1" in
  +                config) echo 'net0: virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr1' ;;
  +                agent)  [ -e "$d/deaf" ] && return 255
  +                        [ "$3" = ping ] && return 0
  +                        echo '[{"name":"ens18","ip-addresses":[{"ip-address-type":"ipv4","ip-address":"10.99.0.2"}]}]' ;;
  +                guest)  [ -e "$d/deaf" ] && { echo "QEMU guest agent is not running" >&2; return 255; }
  +                        case "$*" in
  +                            *lsusb*) [ -e "$d/sdr" ] && echo '{"exitcode":0,"exited":1,"out-data":"Bus 002 Device 003: ID 04b4:00f1 Cypress\n"}' \
  +                                                      || echo '{"exitcode":0,"exited":1,"out-data":"Bus 001 Device 001: ID 1d6b:0002 Linux\n"}' ;;
  +                            *) python3 -c 'import json,sys; print(json.dumps({"exitcode":0,"exited":1,"out-data":open(sys.argv[1]).read()}))' "$d/status" ;;
  +                        esac ;;
  +            esac
  +        }
  +        timeout(){ printf '%s\n' "$1" >> "$d/timeout.calls"; shift; "$@"; }
  +        sleep(){ printf '%s\n' "$1" >> "$d/sleep.calls"; }
  +        ip(){ [ "$*" = "-4 neigh show" ] && echo '10.99.0.2 dev vmbr1 lladdr aa:bb:cc:dd:ee:ff REACHABLE'; }
  +        . "$BLOCK"
  +        printf 'VMIP=[%s]\nVMNOTE=[%s]\nBRINGUP=[%s]\nRXWARN=[%s]\n' "$VMIP" "$VMNOTE" "$BRINGUP" "$RXWARN"
  +    )
  +}
  +# scene <name> <unit> <result> <web> [step] : a scene whose agent hears.
  +scene(){
  +    local d="$T/$1"; mkdir -p "$d"
  +    printf 'unit=%s\nresult=%s\nweb=%s\nstep=%s\n' "$2" "$3" "$4" "${5:-}" > "$d/status"
  +    echo "$d"
  +}
  +# ascii <scene dir> : how many recorded requests hold a byte above 0x7F.
  +nonascii(){ local n=0 f; for f in "$1"/calls/*; do LC_ALL=C grep -q -P '[^\x00-\x7F]' "$f" && n=$((n+1)); done; echo "$n"; }
  +
  +echo "== the bring-up line, state by state"
  +d=$(scene building activating none inactive "Install hf-timestd"); out=$(panel "$d")
  +has   "activating: says the station still builds"      "$out" "STATION IS STILL BUILDING"
  +has   "activating: names the last step"                "$out" "last step: Install hf-timestd"
  +d=$(scene notyet inactive none inactive); out=$(panel "$d")
  +has   "no marker: prints nothing about bring-up"       "$out" "BRINGUP=[]"
  +lacks "no marker: raises no ka9q-web alarm"            "$out" "ka9q-web IS NOT RUNNING"
  +d=$(scene stopped failed none inactive); out=$(panel "$d")
  +has   "unit failed: says bring-up was stopped"         "$out" "BRING-UP WAS STOPPED BEFORE IT FINISHED"
  +d=$(scene retry inactive retry inactive); out=$(panel "$d")
  +has   "retry: says it tries again at the next boot"    "$out" "BRING-UP FAILED -- it tries again"
  +lacks "retry: does not claim bring-up finished"        "$out" "BRING-UP FINISHED"
  +d=$(scene nosdr inactive awaiting-sdr inactive); out=$(panel "$d")
  +has   "awaiting-sdr: says it ran without the radio"    "$out" "bring-up ran WITHOUT the radio"
  +lacks "awaiting-sdr: does not claim bring-up finished" "$out" "BRING-UP FINISHED"
  +d=$(scene gaveup inactive gave-up inactive); out=$(panel "$d")
  +has   "gave-up: says it stopped trying"                "$out" "BRING-UP FAILED THREE TIMES"
  +d=$(scene done inactive ok active); out=$(panel "$d")
  +has   "ok, ka9q-web active: prints nothing"            "$out" "BRINGUP=[]"
  +d=$(scene noweb inactive ok inactive); out=$(panel "$d")
  +has   "ok, ka9q-web down: raises the alarm"            "$out" "BRING-UP FINISHED BUT ka9q-web IS NOT RUNNING"
  +d=$(scene odd unknown none inactive); out=$(panel "$d")
  +has   "a unit state the panel does not know, no marker: prints nothing" "$out" "BRINGUP=[]"
  +d=$(scene noweb inactive ok inactive); out=$(panel "$d")
  +lacks "no line sends smd through the guest agent"      "$out" "guest exec 100 -- smd"
  +has   "the alarm sends the operator into the VM"       "$out" "sigmond-vm"
  +has   "a hearing agent gives the VM's address"         "$out" "VMIP=[10.99.0.2]"
  +[ ! -e "$d/run/qga-reset" ] && ok "a hearing agent gets no reset, and no record of one" \
  +                             || bad "a hearing agent gets no reset" "found $d/run/qga-reset"
  +[ ! -e "$d/sleep.calls" ] && ok "a hearing agent costs no wait" || bad "a hearing agent costs no wait" "$(cat "$d/sleep.calls")"
  +
  +echo "== every request stays ASCII"
  +for s in building notyet stopped retry nosdr gaveup done noweb odd; do
  +    n=$(nonascii "$T/$s")
  +    [ "$n" = 0 ] && ok "$s: no request holds a byte above 0x7F" || bad "$s: no request holds a byte above 0x7F" "$n request(s) do"
  +done
  +
  +echo "== the status request itself, run against a stand-in VM"
  +# Take the request the panel really sent, point its three paths at a scratch
  +# tree, and run it: this proves the shipped text, not the stub's answer.
  +d="$T/building"; req=""
  +for f in "$d"/calls/*; do
  +    if grep -qa 'firstrun-bringup' "$f"; then req=$(tr '\0' '\n' < "$f" | sed -n '/^u=\$(systemctl/,$p'); fi
  +done
  +[ -n "$req" ] && ok "found the status request among the recorded calls" || bad "found the status request" "none recorded"
  +G="$T/guest"; mkdir -p "$G/bin" "$G/lib" "$G/log"
  +cat > "$G/bin/systemctl" <<EOF
  +#!/bin/bash
  +case "\$2" in sigmond-firstrun-bringup) cat "$G/unit";; ka9q-web) cat "$G/web";; esac
  +EOF
  +chmod 755 "$G/bin/systemctl"
  +guest(){ PATH="$G/bin:$PATH" /bin/bash -c "$(printf '%s' "$req" | sed "s#/var/lib/sigmond/#$G/lib/#; s#/var/log/sigmond/#$G/log/#")"; }
  +echo activating > "$G/unit"; echo inactive > "$G/web"
  +printf '2026-10-10T01:00:00+00:00 first boot\n     \033[1m\302\273 Install hf-timestd\033[0m\n     noise\n\342\224\200\342\224\200\342\224\200 Stage 4: plan \342\224\200\342\224\200\342\224\200\n     more noise\n' > "$G/log/firstrun-bringup.log"
  +out=$(guest)
  +has   "it reports the unit's state"                    "$out" "unit=activating"
  +has   "no marker reads as result=none"                 "$out" "result=none"
  +has   "it finds the last step by its box-drawing rule" "$out" "step=Stage 4: plan"
  +[ "$(printf '%s' "$out" | LC_ALL=C grep -c -P '[^\x00-\x7F]')" = 0 ] \
  +    && ok "its answer holds no byte above 0x7F" || bad "its answer holds no byte above 0x7F" "$out"
  +printf '2026-10-10T01:00:00+00:00 first boot\n     \302\273 Start radiod \342\200\224 already configured\n' > "$G/log/firstrun-bringup.log"
  +has   "it finds a step by its guillemet"               "$(guest)" "step=Start radiod  already configured"
  +echo inactive > "$G/unit"; printf 'attempts=2\nresult=retry\n2026-10-10T01:00:00+00:00\n' > "$G/lib/.firstrun-bringup-done"
  +has   "it reads the marker's result"                   "$(guest)" "result=retry"
  +printf '2026-09-01T00:00:00+00:00\n' > "$G/lib/.firstrun-bringup-done"
  +has   "a marker with no result line reads as ok"       "$(guest)" "result=ok"
  +rm -f "$G/log/firstrun-bringup.log"
  +has   "a missing log leaves the step empty"            "$(guest)" "step="
  +
  +echo "== a deaf agent gets one reset, and hears again"
  +d=$(scene deaf inactive ok active); : > "$d/deaf"; agent "$d"; out=$(panel "$d"); wait
  +[ "$(od -An -tx1 "$d/reset.bytes" 2>/dev/null | tr -d ' \n')" = "$RESET_HEX" ] \
  +    && ok "the reset sends 0xFF and one sync request, to the byte" \
  +    || bad "the reset sends 0xFF and one sync request" "$(od -An -c "$d/reset.bytes" 2>/dev/null | head -3)"
  +has   "after the reset the agent gives the address"    "$out" "VMIP=[10.99.0.2]"
  +has   "the address carries no ARP note"                "$out" "VMNOTE=[]"
  +has   "after the reset the bring-up line reads true"   "$out" "BRINGUP=[]"
  +has   "the reset leaves its record, by name"           "$(cat "$d/run/qga-reset" 2>/dev/null)" " panel"
  +[ ! -e "$d/sleep.calls" ] && ok "the panel asks again at once, without the ten-second wait" \
  +                           || bad "the panel asks again at once" "slept: $(tr '\n' ' ' < "$d/sleep.calls")"
  +[ "$(nonascii "$d")" = 0 ] && ok "deaf: no request holds a byte above 0x7F" || bad "deaf: ASCII" ""
  +
  +echo "== an agent that stays silent"
  +d=$(scene silent inactive ok active); : > "$d/deaf"; out=$(panel "$d")
  +has   "the address comes from ARP"                     "$out" "VMIP=[10.99.0.2]"
  +has   "the address says where it came from"            "$out" "address via ARP"
  +has   "the panel says the agent does not answer"       "$out" "guest agent does not answer"
  +lacks "silence is not read as a build"                 "$out" "STILL BUILDING"
  +lacks "silence raises no ka9q-web alarm"               "$out" "ka9q-web IS NOT RUNNING"
  +[ ! -e "$d/run/qga-reset" ] && ok "a reset that brought no answer leaves no record" \
  +                             || bad "a reset that brought no answer leaves no record" "$(cat "$d/run/qga-reset")"
  +[ "$(wc -l < "$d/sleep.calls")" = 6 ] && ok "the six tries keep their waits" || bad "the six tries keep their waits" "$(wc -l < "$d/sleep.calls")"
  +
  +echo "== the RX888 notice"
  +d=$(scene rx inactive ok active); : > "$d/rxmark"; out=$(panel "$d")
  +has   "no SDR in the VM: the notice stands"            "$out" "RX888 NOT VISIBLE"
  +[ -e "$d/rxmark" ] && ok "the marker stays" || bad "the marker stays" "removed"
  +: > "$d/sdr"; out=$(panel "$d")
  +lacks "SDR in the VM: the notice clears"               "$out" "RX888 NOT VISIBLE"
  +[ ! -e "$d/rxmark" ] && ok "the marker goes" || bad "the marker goes" "still there"
  +grep -qx 25 "$d/timeout.calls" && ok "the lsusb request runs under a timeout" || bad "the lsusb request runs under a timeout" "$(sort -u "$d/timeout.calls" | tr '\n' ' ')"
  +
  +echo "== every request firstboot-v3.sh sends, from any of its scripts"
  +# A request is a `qm guest exec` that stands as a command.  The scan follows
  +# each one to the end of its shell statement, through quotes and continued
  +# lines, and looks for a byte above 0x7F in it.  The two counts are pins: the
  +# requests, and the mentions in all (the rest stand in prose and comments).  A
  +# new request, or a mention this scan does not take for one, moves a count
  +# and stops here until someone has looked at it.
  +WANT_CALLS=6; WANT_MENTIONS=10
  +python3 - "$SRC" > "$T/guard.out" <<'GUARDPY'
  +import re, sys
  +text = open(sys.argv[1], encoding="utf-8").read()
  +CALL = re.compile(r"(?:^[ \t]*|\$\(|\bif\s+(?:!\s+)?|&&\s*|\|\|\s*|timeout\s+\d+\s+)qm guest exec\b", re.M)
  +
  +def statement(text, start):
  +    stack, i = [], start
  +    while i < len(text):
  +        ch = text[i]
  +        top = stack[-1] if stack else None
  +        if top == "'":
  +            if ch == "'":
  +                stack.pop()
  +        elif ch == "\\":
  +            i += 1
  +        elif text.startswith("$(", i):
  +            stack.append("$(")
  +            i += 1
  +        elif top == '"':
  +            if ch == '"':
  +                stack.pop()
  +        elif ch in "'\"":
  +            stack.append(ch)
  +        elif ch == ")" and top == "$(":
  +            stack.pop()
  +        elif ch in "\n;|" and not stack:
  +            break
  +        elif ch == "&" and not stack and text[i - 1] != ">" and text[i + 1:i + 2] != ">":
  +            break
  +        i += 1
  +    return text[start:i]
  +
  +calls = [statement(text, m.start()) for m in CALL.finditer(text)]
  +bad = [s for s in calls if not s.isascii()]
  +print("CALLS=%d" % len(calls))
  +print("NONASCII=%d" % len(bad))
  +print("MENTIONS=%d" % text.count("qm guest exec"))
  +for s in bad:
  +    print("BAD=" + s.splitlines()[0].strip()[:90])
  +GUARDPY
  +g(){ sed -n "s/^$1=//p" "$T/guard.out" | head -1; }
  +[ "$(g NONASCII)" = 0 ] && ok "no request holds a byte above 0x7F" || bad "no request holds a byte above 0x7F" "$(g BAD)"
  +[ "$(g CALLS)" = "$WANT_CALLS" ] && ok "$WANT_CALLS requests, as counted when this test was written" \
  +    || bad "$WANT_CALLS requests" "found $(g CALLS): look at the new one, then move this count"
  +[ "$(g MENTIONS)" = "$WANT_MENTIONS" ] && ok "$WANT_MENTIONS mentions of qm guest exec in all" \
  +    || bad "$WANT_MENTIONS mentions of qm guest exec" "found $(g MENTIONS): is the new one a request this scan missed?"
  +
  +echo
  +echo "passed $pass, failed $fail"
  +[ "$fail" = 0 ]
  ````

- [ ] **Step 2: Run it and watch it fail.**

  Run: `bash test-panel-agent.sh; echo "exit $?"`
  Expected: `FAIL: no agent_block between its markers in /home/mjh/hamsci/repos/sigmond-appliance/firstboot-v3.sh`
  and `exit 1`. The block does not exist yet.

- [ ] **Step 3: Change the panel.**

  ```bash
  git apply --check $P/task-1-code.patch && git apply $P/task-1-code.patch
  ```

  The change:

  ````diff
  --- a/firstboot-v3.sh
  +++ b/firstboot-v3.sh
  @@ -2589,6 +2589,40 @@
       | awk '{print $4}' | cut -d/ -f1 | head -1)
   [ -n "$HOSTIP" ] || HOSTIP=$(hostname -I 2>/dev/null | tr ' ' '\n' \
       | grep -vE '^(10\.99\.0\.|127\.|192\.0\.0\.|fe80:)' | head -1)
  +# >>> agent_block (test-panel-agent.sh loads exactly this block)
  +# -- the guest agent: ASCII only, and one reset before any verdict ----------
  +# NO BYTE ABOVE 0x7F MAY CROSS TO THE GUEST AGENT.  Proxmox (PVE 9.1.1,
  +# qemu-server 9.0.30) writes a request that holds non-ASCII characters SHORT:
  +# as many bytes as the request has characters, so the closing bytes never
  +# arrive.  About one time in six the agent's parser then waits for them for
  +# good: it keeps its PID, reads every later request and answers none.  Up to
  +# v3.70 this panel sent such a request every five minutes (a grep for two
  +# box-drawing patterns), and that request never once ran.  Build such bytes
  +# INSIDE the guest (printf with octal escapes), as the status request below
  +# does.  test-panel-agent.sh fails on any byte above 0x7F in a request.
  +#
  +# qga_reset <vmid> : one 0xFF byte and a sync request down the agent's socket.
  +# The byte resets the agent's parser; the QGA protocol defines it and Proxmox
  +# never sends it.  Returns 0 when the agent answered the sync.  The same bytes
  +# stand in sigmond-wizard.sh, pm-align.py and pm-heartbeat.py.
  +QGA_DIR="${SIGMOND_QGA_DIR:-/var/run/qemu-server}"
  +QGA_NOTE="${SIGMOND_QGA_NOTE:-/run/sigmond/qga-reset}"
  +qga_reset(){
  +    timeout 10 python3 -c 'import socket,sys
  +s=socket.socket(socket.AF_UNIX); s.settimeout(4)
  +try:
  +    s.connect(sys.argv[2]+"/"+sys.argv[1]+".qga")
  +    s.sendall(b"\xff"+b"{\"execute\":\"guest-sync-delimited\",\"arguments\":{\"id\":4242}}\n")
  +    sys.exit(0 if b"4242" in s.recv(4096) else 1)
  +except Exception:
  +    sys.exit(1)' "$1" "$QGA_DIR" >/dev/null 2>&1 </dev/null
  +}
  +# One line, replaced each time: when the last reset brought the agent back,
  +# and who sent it.  pm-heartbeat.py reports from it.
  +qga_note_reset(){
  +    mkdir -p "$(dirname "$QGA_NOTE")" 2>/dev/null
  +    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" > "$QGA_NOTE" 2>/dev/null
  +}
   VMIP=""; VMNOTE=""
   for i in 1 2 3 4 5 6; do
     # Print the VM address THIS HOST can actually reach.  Taking whichever
  @@ -2643,6 +2677,15 @@
       cands.sort(key=lambda ip: 0 if is_local(ip) else 1)
       print(cands[0])' 2>/dev/null)
     [ -n "$VMIP" ] && break
  +  # ONE RESET BEFORE ANY VERDICT.  A first try that brought nothing and a ping
  +  # that fails: the agent may stand deaf.  Reset its parser once; when it
  +  # answers, record that and ask again at once.
  +  if [ "$i" = 1 ] && ! timeout 10 qm agent "$VMID" ping >/dev/null 2>&1 </dev/null; then
  +      if qga_reset "$VMID"; then
  +          qga_note_reset panel
  +          continue
  +      fi
  +  fi
     sleep 10
   done
   # Agent-free fallback: a wedged qemu-guest-agent (seen after any guest-exec
  @@ -2661,28 +2704,42 @@
     # v3.62, 2026-09-30).  Carry it as a separate label.
     [ -n "$VMIP" ] && VMNOTE=" (address via ARP — guest agent not answering)"
   fi
  -# ── is the station still BUILDING? ─────────────────────────────────────────
  -# ⛔ "still installing" and "broken" looked identical from outside, and that
  -# cost real time three times on 2026-09-20/21: the panel printed a ka9q-web
  -# URL that could not answer yet, the dashboard showed the channel down, and
  -# the only place the truth existed was firstrun-bringup.log inside the VM --
  -# reachable solely by someone with shell on this host AND a responsive guest
  -# agent.  rob concluded twice that a healthy station was broken; so did I.
  +# -- where does bring-up stand? ---------------------------------------------
  +# ONE request, ASCII only, answers four questions: the bring-up unit's state,
  +# the result its marker records (sigmond-firstrun-bringup writes ok, retry,
  +# awaiting-sdr or gave-up; a marker from before results reads as ok), the
  +# state of ka9q-web, and the last step in the log.  The unit reads `inactive`
  +# whenever it does not run, finished or not: only the marker tells those
  +# apart.  The step line comes back stripped to printable ASCII, because the
  +# agent's output reaches this host double-encoded.
   #
  -# So say it here, where the operator is already looking.  A short agent
  -# timeout on purpose: during bring-up the VM is saturated building venvs and
  -# the agent often will not answer, and "VM busy" is itself the answer.
  +# A silent agent says NOTHING about bring-up.  This comment used to explain
  +# the silence as "the VM is saturated building venvs"; from 2026-09-27 to
  +# 2026-10-08 that explanation hid a deaf agent on three stations.
   BRINGUP=""
   _bu=$(timeout 12 qm guest exec "$VMID" --timeout 8 -- /bin/bash -c \
  -        'systemctl is-active sigmond-firstrun-bringup 2>/dev/null; \
  -         sed "s/\x1b\[[0-9;]*m//g" /var/log/sigmond/firstrun-bringup.log 2>/dev/null \
  -           | grep -E "^───|» " | tail -1 | cut -c1-58' 2>/dev/null \
  +        'u=$(systemctl is-active sigmond-firstrun-bringup 2>/dev/null); r=none; \
  +         m=/var/lib/sigmond/.firstrun-bringup-done; \
  +         if [ -e "$m" ]; then r=$(sed -n "s/^result=//p" "$m" 2>/dev/null | head -1); r=${r:-ok}; fi; \
  +         w=$(systemctl is-active ka9q-web 2>/dev/null); \
  +         s=$(sed "s/\x1b\[[0-9;]*m//g" /var/log/sigmond/firstrun-bringup.log 2>/dev/null \
  +               | LC_ALL=C grep -E "$(printf "^\342\224\200\342\224\200\342\224\200|\302\273 ")" | tail -1 \
  +               | LC_ALL=C tr -cd "\40-\176" | sed "s/^ *//; s/ *$//" | cut -c1-58); \
  +         printf "unit=%s\nresult=%s\nweb=%s\nstep=%s\n" "${u:-unknown}" "$r" "${w:-unknown}" "$s"' \
  +        2>/dev/null </dev/null \
         | python3 -c 'import json,sys
   try: print((json.load(sys.stdin).get("out-data") or "").strip())
   except Exception: pass' 2>/dev/null)
  -case "$_bu" in
  -    activating*)
  -        _stage=$(printf '%s' "$_bu" | tail -1)
  +_buf(){ printf '%s\n' "$_bu" | sed -n "s/^$1=//p" | head -1; }
  +_unit=$(_buf unit); _res=$(_buf result); _web=$(_buf web); _stage=$(_buf step)
  +_bulog=" !!   check:  sigmond-vm   then:  tail -30 /var/log/sigmond/firstrun-bringup.log"
  +case "$_unit" in
  +    "")
  +        BRINGUP=" .. the decoder VM's guest agent does not answer, so this panel cannot
  + ..   read where bring-up stands.  That silence says nothing about a build.
  +"
  +        ;;
  +    activating)
           BRINGUP=" >> STATION IS STILL BUILDING -- this is normal, not a fault.
    >>   First-run bring-up is RUNNING (clones, builds ~25 components; tens of
    >>   minutes on a cold box).  ka9q-web and station-web start near the END,
  @@ -2691,21 +2748,37 @@
    >>   watch it:  qm guest exec $VMID -- tail -5 /var/log/sigmond/firstrun-bringup.log
   "
           ;;
  -    failed*|inactive*)
  -        # inactive is the normal finished state too, so only shout when the
  -        # marker says it ran and the services it should have started are not up.
  -        if ! qm guest exec "$VMID" --timeout 8 -- /bin/bash -c \
  -               'systemctl is-active ka9q-web >/dev/null' >/dev/null 2>&1; then
  -            BRINGUP=" !! BRING-UP FINISHED BUT ka9q-web IS NOT RUNNING
  - !!   check:  qm guest exec $VMID -- tail -30 /var/log/sigmond/firstrun-bringup.log
  - !!   re-run: qm guest exec $VMID -- smd bringup dasi2
  +    failed)
  +        BRINGUP=" !! BRING-UP WAS STOPPED BEFORE IT FINISHED (its 90-minute limit, or a kill)
  +$_bulog
   "
  -        fi
           ;;
  -    "")
  -        BRINGUP=" .. decoder VM guest agent did not answer (it is often saturated
  - ..   while the station builds) -- bring-up state unknown from here.
  +    *)
  +        case "$_res" in
  +            none) : ;;      # bring-up has not run yet: nothing to say
  +            retry)
  +                BRINGUP=" !! BRING-UP FAILED -- it tries again when the decoder VM next boots
  +$_bulog
  + !!   (the last lines of that log name the command that runs it by hand)
  +"
  +                ;;
  +            awaiting-sdr)
  +                BRINGUP=" .. bring-up ran WITHOUT the radio: no SDR stood on the USB bus.
  + ..   It finishes by itself when the SDR appears.
  +"
  +                ;;
  +            gave-up)
  +                BRINGUP=" !! BRING-UP FAILED THREE TIMES and has stopped trying
  +$_bulog
  + !!   (the last lines of that log name the commands that finish it by hand)
  +"
  +                ;;
  +            *)
  +                [ "$_web" = active ] || BRINGUP=" !! BRING-UP FINISHED BUT ka9q-web IS NOT RUNNING
  +$_bulog
   "
  +                ;;
  +        esac
           ;;
   esac
 
  @@ -2719,10 +2792,11 @@
   # install transcript.  Re-checked on every refresh and removed once the SDR
   # actually turns up, so it cannot linger as a stale scare.
   RXWARN=""
  -if [ -f /etc/sigmond-appliance/.rx888-needs-powercycle ]; then
  -    if qm guest exec "$VMID" -- /usr/bin/lsusb 2>/dev/null \
  +RXMARK="${SIGMOND_RX888_MARK:-/etc/sigmond-appliance/.rx888-needs-powercycle}"
  +if [ -f "$RXMARK" ]; then
  +    if timeout 25 qm guest exec "$VMID" --timeout 15 -- /usr/bin/lsusb 2>/dev/null </dev/null \
            | grep -qiE '04b4:00(f[013]|bc)|f4b3:0100'; then
  -        rm -f /etc/sigmond-appliance/.rx888-needs-powercycle
  +        rm -f "$RXMARK"
       else
           RXWARN=" !! RX888 NOT VISIBLE TO THE DECODER VM
    !!   The SDR was handed to the VM part-way through its USB start-up and
  @@ -2735,6 +2809,7 @@
   "
       fi
   fi
  +# <<< agent_block
 
   # libc crypt via perl, NOT openssl passwd: Debian roots are yescrypt ($y$),
   # which openssl cannot compute — v3.25 said "the password you set at
  ````

- [ ] **Step 4: Run the tests and watch them pass.**

  Run: `bash test-panel-agent.sh | tail -1` — expected: `passed 58, failed 0`.
  Run: `bash test-panel-remote.sh | tail -1` — expected: `test-panel-remote.sh: 129 passed, 0 failed`.
  Run: `bash -n firstboot-v3.sh && echo parses` — expected: `parses`.
  Run, to check the panel script by itself and its opener:

  ```bash
  sed -n "/^cat > \/usr\/local\/sbin\/sigmond-issue <<'ISSEOF'/,/^ISSEOF/p" firstboot-v3.sh | sed '1d;$d' > /tmp/issue-body.sh
  bash -n /tmp/issue-body.sh && echo "panel parses"
  grep -c "^cat > /usr/local/sbin/sigmond-issue <<'ISSEOF'$" firstboot-v3.sh     # expected: 1
  LC_ALL=C grep -c 'grep -E "\^[^ -~]' /tmp/issue-body.sh                        # expected: 0 (the rig's defect pattern)
  ```

  Then every other hermetic test that reads `firstboot-v3.sh`: `test-wifi-boot.sh` (108 passed),
  `test-bless-instructions.sh` (195 passed), `test-vm-console.sh` (15 passed), `test-unit-quoting.sh`,
  `test-console-storm.sh` (11 passed), `test-offline-debs.sh`, `test-instructions-remote.sh`.

- [ ] **Step 5: Repeat the mutations.** Each on a copy: `cp firstboot-v3.sh /tmp/m.sh`, make the one change,
  `bash test-panel-agent.sh /tmp/m.sh | grep -m1 FAIL`. Every row must print a FAIL line.

  | Mutation of the copy | First check that must fail |
  |---|---|
  | The status request greps with the old literal pattern `grep -E "^───|» "` | `building: no request holds a byte above 0x7F` |
  | Delete the line `none) : ;;` | `no marker: prints nothing about bring-up` |
  | `if qga_reset "$VMID"; then` becomes `if false; then` | `the reset sends 0xFF and one sync request` |
  | `s.sendall(b"\xff"+` becomes `s.sendall(b"\xfe"+` | `the reset sends 0xFF and one sync request` |
  | Delete the line `qga_note_reset panel` | `the reset leaves its record, by name` |
  | Remove `timeout 25 ` from the `lsusb` request | `the lsusb request runs under a timeout` |
  | Remove `r=${r:-ok}; ` from the status request | `a marker with no result line reads as ok` |
  | The silent-agent text starts ` >> STATION IS STILL BUILDING` | `silence is not read as a build` |
  | `_bulog` reads ` !!   re-run: qm guest exec $VMID -- smd bringup dasi2` | `no line sends smd through the guest agent` |
  | Add `# café` inside the import's `ip -4 -br addr; ip route; ss -ltn` request | `no request holds a byte above 0x7F` |
  | Add a seventh request, `qm guest exec "$VMID" --timeout 5 -- /usr/bin/true`, before `RXWARN=""` | `6 requests` |

- [ ] **Step 6: Commit.**

  ```bash
  cd /home/mjh/hamsci/repos/sigmond-appliance
  git add firstboot-v3.sh test-panel-agent.sh
  git commit -m "panel: every request to the guest agent stays ASCII, a silent agent gets one reset, and the bring-up line reads the marker" \
             -m "Up to v3.70 the panel asked the decoder VM for the last bring-up step with a grep for two box-drawing patterns.  Proxmox writes a request that holds non-ASCII characters short, and about one time in six the guest agent then stands deaf for good.  That request never once ran.  The panel now builds those bytes inside the VM, asks one question where it asked two, and reads the result the bring-up marker records, so that a station whose bring-up has not run, has failed or waits for its SDR no longer reads as 'finished but ka9q-web is not running'.  A first query that brings nothing and a ping that fails get one parser reset (the byte 0xFF); a reset that the agent answers leaves one line in /run/sigmond/qga-reset for the host's heartbeat.  test-panel-agent.sh runs the block as shipped and scans every request in firstboot-v3.sh." \
             -m "Design: sigmond tasks/plan-guest-agent-requests.md."
  ```

  End the message with the attribution lines your dispatch names.

---

### Task 2: the wizard's senders encode what they send, and its waits reset a silent agent once

**Files:**
- Create: `/home/mjh/hamsci/repos/sigmond/tests/test_wizard_guest_senders.py`
- Modify: `/home/mjh/hamsci/repos/sigmond/tests/test_wizard_pair_vm.py` (the `pair()` harness; three new tests)
- Modify: `/home/mjh/hamsci/repos/sigmond/scripts/proxmox/sigmond-wizard.sh` (new helpers ahead of `gexec`;
  `gexec`; `guest_out`; the wait inside `rac_pair_locked`; the wizard's own wait for the agent)

**Interfaces:**
- Consumes: nothing from Task 1 at run time. Task 3's test compares this `qga_reset` with the panel's.
- Produces: `qga_b64 <command>` (prints one base64 word), the variable `QGA_RUN` (the guest's side:
  `eval "$(printf %s "$1" | base64 -d)"`), `qga_reset <vmid>`, `qga_note_reset <who>`, the variables `QGA_DIR`
  and `QGA_NOTE`, and the markers `# >>> agent_wait` / `# <<< agent_wait`. The signatures of `gexec <timeout>
  <command...>` and `guest_out <timeout> <command...>` do not change; their 48 callers stay as they stand.

**What you need to know:**
- `gexec` runs a command in the VM and returns 0 or 1; `guest_out` returns the command's standard output. Both
  sent `bash -lc "<command>"`. Some callers put an operator's typed text into the command.
- The new request reads `bash -lc "$QGA_RUN" bash <base64 word>`. The guest's login shell decodes the word and
  runs it with `eval`, so `$0` reads `bash` and the command sees the same shell it saw before. The command
  arrives as an ARGUMENT. Do not change that to a pipe into `bash`: a command that reads its input would then
  swallow whatever followed it (the remote-access work met exactly this).
- The helpers must stand ahead of `gexec`, in the early part of the script: `sigmond-setup --pair-vm` runs before
  the wizard asks anything, and uses them.
- `gexec` matches agent trouble by the text of qm's answer, and one of its patterns reads `*"QGA"*`. A base64
  word can hold those three letters. That matters only if `qm` ever echoes its own arguments in an error, which
  nothing here has seen. Leave the pattern as it stands, and do not widen it.
- The wizard already restarts the agent inside the VM over ssh after 30 seconds of silence. That stays, as the
  second resort. Its comment records a trigger seen on AI6VN (an interrupted wizard); do not call that wrong.
- `tests/test_wizard_pair_vm.py` stubs `gexec` and `guest_out` itself, so its 98 existing tests do not notice the
  new senders. Its harness needs two more stubs, because the pairing's wait now calls `qga_reset`. The patch also
  lets that file read the wizard from `SIGMOND_TEST_WIZARD`, so that a mutation of the pairing runs on a copy.

- [ ] **Step 1: Add the tests.**

  ```bash
  cd /home/mjh/hamsci/repos/sigmond
  P=/home/mjh/hamsci/repos/sigmond/.superpowers/sdd/plan-guest-agent-requests-v371/proto
  git apply --check $P/task-2-tests.patch && git apply $P/task-2-tests.patch
  ```

  ````diff
  diff --git a/tests/test_wizard_guest_senders.py b/tests/test_wizard_guest_senders.py
  new file mode 100644
  --- /dev/null
  +++ b/tests/test_wizard_guest_senders.py
  @@ -0,0 +1,380 @@
  +"""What the wizard sends to the guest agent (tasks/plan-guest-agent-requests.md).
  +
  +Proxmox writes a guest-agent request that holds non-ASCII characters short,
  +and about one time in six the agent then stands deaf for good.  So `gexec`
  +and `guest_out` send every command base64-encoded, and a wizard that finds
  +the agent silent resets its parser once before it waits.
  +
  +These tests run the wizard's own functions, cut out of the shipped script by
  +name.  A stub `qm` plays Proxmox and the guest: it records every argument and
  +runs the request in a local shell, so a command's effect is real.
  +"""
  +import os
  +import re
  +import socket
  +import subprocess
  +import threading
  +from pathlib import Path
  +
  +import pytest
  +
  +REPO = Path(__file__).resolve().parent.parent
  +WIZARD = Path(os.environ.get("SIGMOND_TEST_WIZARD") or REPO / "scripts" / "proxmox" / "sigmond-wizard.sh")
  +TEXT = WIZARD.read_text()
  +
  +#: The bytes every reset sends.  tests/test_qga_reset.py holds the four
  +#: programs' copies equal to this literal.
  +RESET = b"\xff" + b'{"execute":"guest-sync-delimited","arguments":{"id":4242}}\n'
  +SENDERS = ("qga_b64", "qga_reset", "qga_note_reset", "gexec", "guest_out")
  +
  +
  +def shell_func(name: str) -> str:
  +    out, keep = [], False
  +    for ln in TEXT.splitlines(True):
  +        if ln.startswith(f"{name}(){{") or ln.startswith(f"{name}() {{"):
  +            keep = True
  +        if keep:
  +            out.append(ln)
  +            if ln.rstrip() == "}" or (len(out) == 1 and ln.rstrip().endswith("}")):
  +                break
  +    assert out, f"{name} not found in the wizard"
  +    return "".join(out)
  +
  +
  +def shell_var(name: str) -> str:
  +    m = re.search(rf"^{name}=.*$", TEXT, re.M)
  +    assert m, f"the wizard sets no {name}"
  +    return m.group(0) + "\n"
  +
  +
  +def harness(tmp_path: Path) -> str:
  +    """The senders, with a stub qm that runs each request in a local shell.
  +
  +    Scene files under tmp_path: `deaf` (the agent answers nothing), `busy`
  +    (a request times out while the agent still answers a ping), `calls/` (one
  +    file per qm call, arguments NUL-separated)."""
  +    (tmp_path / "calls").mkdir(exist_ok=True)
  +    (tmp_path / "home").mkdir(exist_ok=True)
  +    return f"""
  +set -u
  +VMID=100
  +LOG={tmp_path}/wizard.log
  +export SIGMOND_QGA_DIR={tmp_path}/qga SIGMOND_QGA_NOTE={tmp_path}/run/qga-reset
  +say(){{ echo "say: $*"; }}
  +sleep(){{ echo "$1" >> {tmp_path}/sleeps; }}
  +timeout(){{ shift; "$@"; }}
  +qm(){{
  +    printf '%s\\0' "$@" > "{tmp_path}/calls/$(date +%s%N)"
  +    case "$1" in
  +        agent) [ -e {tmp_path}/deaf ] && return 255; return 0 ;;
  +        guest)
  +            [ -e {tmp_path}/deaf ] && {{ echo "QEMU guest agent is not running" >&2; return 255; }}
  +            [ -e {tmp_path}/busy ] && {{ echo "VM 100 qmp command 'guest-exec' failed - got timeout" >&2; return 255; }}
  +            shift 3; [ "$1" = --timeout ] && shift 2; [ "$1" = -- ] && shift
  +            local o rc
  +            o=$(HOME={tmp_path}/home "$@" 2>/dev/null </dev/null); rc=$?
  +            python3 -c 'import json,sys; print(json.dumps({{"exitcode":int(sys.argv[1]),"exited":1,"out-data":sys.argv[2]}}))' "$rc" "$o"
  +            ;;
  +    esac
  +}}
  +{shell_var("QGA_RUN")}{shell_var("QGA_DIR")}{shell_var("QGA_NOTE")}
  +{"".join(shell_func(n) for n in SENDERS)}
  +"""
  +
  +
  +def bash(script, env=None):
  +    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
  +                          env=dict(os.environ, **(env or {})))
  +
  +
  +def calls(tmp_path: Path):
  +    return [p.read_bytes() for p in sorted((tmp_path / "calls").iterdir())]
  +
  +
  +def guest_execs(tmp_path: Path):
  +    return [c for c in calls(tmp_path) if c.startswith(b"guest\0exec\0")]
  +
  +
  +# -- the command arrives whole, and the request stays ASCII ------------------------------
  +
  +COMMANDS = [
  +    "echo hello",
  +    "exit 3",
  +    "echo \"it's quoted\" && echo $HOME",
  +    "printf '%s\\n' \"na\u00efve \u2014 \u2713 \u00bb \u2500\u2500\u2500\"",
  +    "for l in /sys/class/net/lo*; do echo $(basename $l); done",
  +    "false",
  +    "nosuchcommand_xyz",
  +    "cat <<EOT\nheredoc $((1+2))\nEOT",
  +    "f(){ return 7; }; f; echo rc=$?",
  +    'echo "$0"',
  +    "set -e; true; echo after",
  +    "read x; echo \"[$x]\"",
  +    "mkdir -p /tmp && { echo '[Match]'; echo 'Name=en*'; } | wc -l && for l in a b; do echo \\$(basename $l); done",
  +]
  +
  +
  +@pytest.mark.parametrize("cmd", COMMANDS, ids=range(len(COMMANDS)))
  +def test_guest_out_gives_what_the_direct_form_gave(tmp_path, cmd):
  +    """The encoded request runs the same command in the same kind of shell."""
  +    direct = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=30,
  +                            stdin=subprocess.DEVNULL, env=dict(os.environ, HOME=str(tmp_path / "home")))
  +    r = bash(harness(tmp_path) + 'guest_out 15 "$CMD"\n', env={"CMD": cmd})
  +    assert r.stdout == (direct.stdout.rstrip("\n") + "\n")
  +
  +
  +@pytest.mark.parametrize("cmd", COMMANDS, ids=range(len(COMMANDS)))
  +def test_gexec_answers_with_the_commands_own_exit(tmp_path, cmd):
  +    direct = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=30,
  +                            stdin=subprocess.DEVNULL, env=dict(os.environ, HOME=str(tmp_path / "home")))
  +    r = bash(harness(tmp_path) + 'gexec 15 "$CMD"; echo "rc=$?"\n', env={"CMD": cmd})
  +    assert r.stdout.splitlines()[-1] == ("rc=0" if direct.returncode == 0 else "rc=1")
  +
  +
  +@pytest.mark.parametrize("sender", ["gexec", "guest_out"])
  +@pytest.mark.parametrize("cmd", COMMANDS, ids=range(len(COMMANDS)))
  +def test_no_request_holds_a_byte_above_0x7f(tmp_path, sender, cmd):
  +    bash(harness(tmp_path) + f'{sender} 15 "$CMD"\n', env={"CMD": cmd})
  +    sent = guest_execs(tmp_path)
  +    assert sent, "the sender made no request"
  +    for c in sent:
  +        assert all(b < 0x80 for b in c), c
  +
  +
  +def test_an_operators_answer_with_an_accent_reaches_the_guest_whole(tmp_path):
  +    """The case the encoding exists for: typed text inside a command."""
  +    name = "Jos\u00e9 \u00c5ngstr\u00f6m \u2014 KD\u00d8XYZ"
  +    out = tmp_path / "answer"
  +    r = bash(harness(tmp_path) + f'gexec 15 "printf %s \\"$NAME\\" > {out}"; echo "rc=$?"\n', env={"NAME": name})
  +    assert r.stdout.splitlines()[-1] == "rc=0"
  +    assert out.read_text() == name
  +    assert all(b < 0x80 for c in guest_execs(tmp_path) for b in c)
  +
  +
  +def test_the_command_arrives_as_an_argument_not_on_stdin(tmp_path):
  +    (req,) = (bash(harness(tmp_path) + 'gexec 15 "echo one"\n'), )
  +    (c,) = guest_execs(tmp_path)
  +    args = c.rstrip(b"\0").split(b"\0")
  +    assert args[:6] == [b"guest", b"exec", b"100", b"--timeout", b"15", b"--"]
  +    assert args[6:9] == [b"bash", b"-lc", b'eval "$(printf %s "$1" | base64 -d)"']
  +    assert args[9] == b"bash" and len(args) == 11
  +    import base64
  +    assert base64.b64decode(args[10]) == b"echo one"
  +
  +
  +def test_a_command_that_exits_nonzero_is_not_sent_twice(tmp_path):
  +    r = bash(harness(tmp_path) + 'gexec 15 "exit 4"; echo "rc=$?"\n')
  +    assert r.stdout.splitlines()[-1] == "rc=1"
  +    assert len(guest_execs(tmp_path)) == 1
  +
  +
  +# -- a silent agent: one reset, then the wait ---------------------------------------------
  +
  +class Agent:
  +    """The agent's side of the socket: records what arrives, answers the sync,
  +    and from then on the scene's agent hears again."""
  +
  +    def __init__(self, tmp_path: Path):
  +        self.tmp, self.got = tmp_path, b""
  +        (tmp_path / "qga").mkdir()
  +        self.srv = socket.socket(socket.AF_UNIX)
  +        self.srv.bind(str(tmp_path / "qga" / "100.qga"))
  +        self.srv.listen(1)
  +        self.srv.settimeout(10)
  +        self.t = threading.Thread(target=self.run, daemon=True)
  +        self.t.start()
  +
  +    def run(self):
  +        try:
  +            c, _ = self.srv.accept()
  +        except OSError:
  +            return
  +        c.settimeout(5)
  +        try:
  +            while not self.got.endswith(b"\n"):
  +                d = c.recv(4096)
  +                if not d:
  +                    break
  +                self.got += d
  +        except OSError:
  +            pass
  +        (self.tmp / "deaf").unlink(missing_ok=True)
  +        c.sendall(b"\xff" + b'{"return": 4242}\n')
  +        c.close()
  +
  +
  +def test_a_deaf_agent_gets_one_reset_and_the_step_then_runs(tmp_path):
  +    (tmp_path / "deaf").touch()
  +    agent = Agent(tmp_path)
  +    out = tmp_path / "ran"
  +    r = bash(harness(tmp_path) + f'gexec 15 "echo done > {out}"; echo "rc=$?"\n')
  +    agent.t.join(5)
  +    assert agent.got == RESET
  +    assert r.stdout.splitlines()[-1] == "rc=0"
  +    assert out.read_text() == "done\n"
  +    assert "guest agent stopped answering" in r.stdout
  +    note = (tmp_path / "run" / "qga-reset").read_text()
  +    assert re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ wizard\n", note)
  +    assert not (tmp_path / "sleeps").exists(), "an agent that hears again costs no wait"
  +
  +
  +def test_an_agent_that_stays_silent_fails_the_step_and_leaves_no_record(tmp_path):
  +    (tmp_path / "deaf").touch()
  +    r = bash(harness(tmp_path) + 'gexec 15 "echo never"; echo "rc=$?"\n')
  +    assert r.stdout.splitlines()[-1] == "rc=1"
  +    assert not (tmp_path / "run" / "qga-reset").exists()
  +    assert (tmp_path / "sleeps").read_text().count("\n") == 24
  +    assert len(guest_execs(tmp_path)) == 2, "the step is tried once more after the wait, and no further"
  +
  +
  +def test_a_request_that_times_out_under_a_hearing_agent_brings_no_reset(tmp_path):
  +    """The record must mean: silent, then answered after a reset.  An agent
  +    that answers its ping was never silent."""
  +    (tmp_path / "busy").touch()
  +    agent = Agent(tmp_path)
  +    r = bash(harness(tmp_path) + 'gexec 15 "echo x"; echo "rc=$?"\n')
  +    agent.srv.close()
  +    assert r.stdout.splitlines()[-1] == "rc=1"
  +    assert agent.got == b""
  +    assert not (tmp_path / "run" / "qga-reset").exists()
  +
  +
  +def test_a_hearing_agent_gets_no_reset(tmp_path):
  +    agent = Agent(tmp_path)
  +    bash(harness(tmp_path) + 'gexec 15 "true"; guest_out 15 "echo x"\n')
  +    agent.srv.close()
  +    assert agent.got == b""
  +    assert not (tmp_path / "run" / "qga-reset").exists()
  +
  +
  +# -- the wizard's own wait for the agent: the reset first, the ssh restart second ---------
  +
  +def agent_wait(tmp_path: Path) -> str:
  +    """The wizard's wait for the agent, cut out by its markers, in a subshell
  +    (it exits on failure).  Scene files: `deaf`, `ssh-heals` (a restart over
  +    ssh brings the agent back)."""
  +    m = re.search(r"^# >>> agent_wait[^\n]*\n(.*?)^# <<< agent_wait", TEXT, re.S | re.M)
  +    assert m, "no agent_wait block between its markers in the wizard"
  +    return f"""
  +set -u
  +VMID=100
  +export SIGMOND_QGA_DIR={tmp_path}/qga SIGMOND_QGA_NOTE={tmp_path}/run/qga-reset
  +C={tmp_path}/calls.txt
  +say(){{ echo "say: $*"; }}
  +sleep(){{ echo "sleep $1" >> $C; }}
  +timeout(){{ shift; "$@"; }}
  +mgmt_vm_ip(){{ echo 10.99.0.2; }}
  +ssh(){{ echo "ssh" >> $C; [ -e {tmp_path}/ssh-heals ] && rm -f {tmp_path}/deaf; return 0; }}
  +qm(){{
  +    echo "qm $1 ${{3:-}}" >> $C
  +    case "$1" in agent) [ -e {tmp_path}/deaf ] && return 255;; esac
  +    return 0
  +}}
  +{shell_var("QGA_DIR")}{shell_var("QGA_NOTE")}{shell_func("qga_reset")}{shell_func("qga_note_reset")}
  +( {m.group(1)}
  +  echo "went on" )
  +echo "rc=$?"
  +"""
  +
  +
  +def wait_calls(tmp_path):
  +    return (tmp_path / "calls.txt").read_text().splitlines()
  +
  +
  +def test_an_agent_that_answers_costs_the_wizard_no_wait(tmp_path):
  +    r = bash(agent_wait(tmp_path))
  +    assert r.stdout.splitlines()[-2:] == ["went on", "rc=0"]
  +    assert wait_calls(tmp_path) == ["qm agent ping", "qm agent ping"]
  +
  +
  +def test_the_wizard_resets_a_deaf_agent_before_it_restarts_anything(tmp_path):
  +    (tmp_path / "deaf").touch()
  +    agent = Agent(tmp_path)
  +    r = bash(agent_wait(tmp_path))
  +    agent.t.join(5)
  +    assert agent.got == RESET
  +    assert "one parser reset brought it back" in r.stdout
  +    assert r.stdout.splitlines()[-2:] == ["went on", "rc=0"]
  +    calls = wait_calls(tmp_path)
  +    assert "ssh" not in calls
  +    assert calls.count("sleep 5") == 1, "one wait before the reset, none after it"
  +    assert re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ wizard\n",
  +                        (tmp_path / "run" / "qga-reset").read_text())
  +
  +
  +def test_the_restart_over_ssh_stays_as_the_second_resort(tmp_path):
  +    (tmp_path / "deaf").touch()
  +    (tmp_path / "ssh-heals").touch()
  +    r = bash(agent_wait(tmp_path))
  +    assert r.stdout.splitlines()[-2:] == ["went on", "rc=0"]
  +    calls = wait_calls(tmp_path)
  +    assert calls.count("ssh") == 1
  +    assert calls.index("ssh") > 6, "the restart comes after 30 s of silence, as before"
  +    assert not (tmp_path / "run" / "qga-reset").exists()
  +
  +
  +def test_an_agent_that_never_answers_ends_the_wizard(tmp_path):
  +    (tmp_path / "deaf").touch()
  +    r = bash(agent_wait(tmp_path))
  +    assert "not answering after 300s" in r.stdout
  +    assert r.stdout.splitlines()[-1] == "rc=1"
  +    assert wait_calls(tmp_path).count("sleep 5") == 60
  +
  +
  +# -- no other request in the wizard may carry a variable's text ---------------------------
  +
  +CALL = re.compile(r"(?:^[ \t]*|\$\(|\bif\s+(?:!\s+)?|&&\s*|\|\|\s*|timeout\s+\d+\s+)qm guest exec\b", re.M)
  +
  +
  +def statement(text, start):
  +    """From `start` to the end of that one command: the first newline, `;`,
  +    `|` or `&` that stands outside every quote and every $( ), and after no
  +    backslash.  What a `&&` joins on is the host's own next command."""
  +    stack, i = [], start
  +    while i < len(text):
  +        ch = text[i]
  +        top = stack[-1] if stack else None
  +        if top == "'":
  +            if ch == "'":
  +                stack.pop()
  +        elif ch == "\\":
  +            i += 1
  +        elif text.startswith("$(", i):
  +            stack.append("$(")
  +            i += 1
  +        elif top == '"':
  +            if ch == '"':
  +                stack.pop()
  +        elif ch in "'\"":
  +            stack.append(ch)
  +        elif ch == ")" and top == "$(":
  +            stack.pop()
  +        elif ch in "\n;|" and not stack:
  +            break
  +        elif ch == "&" and not stack and text[i - 1] != ">" and text[i + 1:i + 2] != ">":
  +            break
  +        i += 1
  +    return text[start:i]
  +
  +
  +def direct_requests():
  +    """Every `qm guest exec` that stands as a command outside the two senders."""
  +    spans = []
  +    for name in ("gexec", "guest_out"):
  +        at = TEXT.index(shell_func(name))
  +        spans.append((at, at + len(shell_func(name))))
  +    return [statement(TEXT, m.start()) for m in CALL.finditer(TEXT)
  +            if not any(a <= m.start() < b for a, b in spans)]
  +
  +
  +def test_every_direct_request_in_the_wizard_is_ascii_and_counted():
  +    """Four requests stand outside the two senders, each with fixed text: the
  +    USB list, the VM's host key, its address, and sshd's settings after a
  +    refused login.  A fifth must go through a sender, or be added here with
  +    its reason.  The total counts prose too (two lines of advice), so that a
  +    request written in a form this test does not know still stops a reader."""
  +    direct = direct_requests()
  +    for s in direct:
  +        assert s.isascii(), s
  +    assert len(direct) == 4, "\n---\n".join(direct)
  +    assert TEXT.count("qm guest exec") == 8, "a new mention of `qm guest exec`: is it a request?"
  --- a/tests/test_wizard_pair_vm.py
  +++ b/tests/test_wizard_pair_vm.py
  @@ -22,7 +22,8 @@
   import pytest
 
   REPO = Path(__file__).resolve().parent.parent
  -WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"
  +# A mutation gets tried on a copy of the wizard, never in the checkout: name the copy here.
  +WIZARD = Path(os.environ.get("SIGMOND_TEST_WIZARD") or REPO / "scripts" / "proxmox" / "sigmond-wizard.sh")
   TEXT = WIZARD.read_text()
 
   HOSTKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostHostHostHostHostHostHostHostHostHostHost"
  @@ -286,19 +287,21 @@
 
   def pair(tmp_path, *, slash30="10.99.0.1/30", other30="", agent=0, sudo="yes", apt_installs=False,
            vmkey=VMKEY, gexec_rcs="0 0", install_rc=0, hostpub=HOSTKEY + " root@pm\n", user_exists=False,
  -         chown_rc=0, enable_rc=0, lock_held=False, tail=None, real_mgmt_vm_ip=False, marks="dir"):
  +         chown_rc=0, enable_rc=0, lock_held=False, tail=None, real_mgmt_vm_ip=False, marks="dir",
  +         reset_heals=False):
       """`tail` is the last thing the script runs; by default one pairing, then its result.
       `lock_held` holds the pairing lock from this process, as another pairing would.
       `slash30` is the /30 address vmbr1 carries; `other30` is one on another interface, which
       `ip` lists first.  `real_mgmt_vm_ip` runs the wizard's own mgmt_vm_ip against the same `ip`.
       `marks="file"` puts a regular file where the mark directory belongs, so nothing can be
  -    written under it (root included)."""
  +    written under it (root included).  `reset_heals`: one parser reset brings a silent
  +    agent back (`agent` then gives only the answer before that reset)."""
       (tmp_path / "home").mkdir(exist_ok=True)
       if marks == "dir":
           (tmp_path / "marks").mkdir(exist_ok=True)
       elif not (tmp_path / "marks").exists():
           (tmp_path / "marks").write_text("not a directory\n")
  -    for stale in ("calls", "gexec.args", "timeouts", "body.fds", "body.lock"):
  +    for stale in ("calls", "gexec.args", "timeouts", "body.fds", "body.lock", "resets"):
           (tmp_path / stale).unlink(missing_ok=True)
       if hostpub is not None:
           (tmp_path / "host.pub").write_text(hostpub)
  @@ -324,7 +327,10 @@
               [ -n "{slash30}" ] && echo "7: vmbr1    inet {slash30} brd 10.99.0.3 scope global vmbr1"
               return 0
           }}
  -        qm(){{ echo "qm $*" >> $C; return {agent}; }}
  +        AGENT_RC={agent}
  +        qm(){{ echo "qm $*" >> $C; return $AGENT_RC; }}
  +        qga_reset(){{ echo "reset $1" >> {tmp_path}/resets; [ {int(reset_heals)} = 1 ] || return 1; AGENT_RC=0; }}
  +        qga_note_reset(){{ echo "note $1" >> {tmp_path}/resets; }}
           rac_have_sudo(){{ [ -e {tmp_path}/sudo-present ]; }}
           apt-get(){{ echo "apt-get $*" >> $C; case "$*" in *install*) [ {int(apt_installs)} = 1 ] && touch {tmp_path}/sudo-present;; esac; }}
           rac_install_host_files(){{
  @@ -369,6 +375,27 @@
       return r, calls
 
 
  +def test_a_deaf_agent_gets_one_reset_and_the_pairing_goes_on(tmp_path):
  +    """tasks/plan-guest-agent-requests.md section 4: one reset before any verdict."""
  +    r, calls = pair(tmp_path, agent=1, reset_heals=True)
  +    assert "rc=0 fail=" in r.stdout, r.stdout + r.stderr
  +    assert (tmp_path / "resets").read_text() == "reset 100\nnote pairing\n"
  +    assert [c for c in calls if c.startswith("qm agent")] == ["qm agent 100 ping"] * 2
  +    assert (tmp_path / "marks" / "rac-pairing").read_text().startswith("paired ")
  +
  +
  +def test_an_agent_that_stays_silent_gets_one_reset_and_no_more(tmp_path):
  +    r, calls = pair(tmp_path, agent=1)
  +    assert "fail=the guest agent in VM 100 does not answer" in r.stdout
  +    assert (tmp_path / "resets").read_text() == "reset 100\n"
  +    assert len([c for c in calls if c.startswith("qm agent")]) == 12
  +
  +
  +def test_an_agent_that_answers_gets_no_reset_from_the_pairing(tmp_path):
  +    pair(tmp_path)
  +    assert not (tmp_path / "resets").exists()
  +
  +
   def test_pairing_writes_both_halves_and_then_proves_them(tmp_path):
       r, calls = pair(tmp_path)
       assert "rc=0 fail=" in r.stdout, r.stdout + r.stderr
  ````

- [ ] **Step 2: Run them and watch them fail.**

  Run: `.venv/bin/pytest tests/test_wizard_guest_senders.py tests/test_wizard_pair_vm.py -q 2>&1 | tail -1`
  Expected: `65 failed, 100 passed`. The 63 failures in the new file read `qga_b64 not found in the wizard`,
  `the wizard sets no QGA_RUN` or `no agent_wait block between its markers in the wizard`. The two in the
  pairing's file miss the file `resets`. The scan of the direct requests and the test that a hearing agent gets
  no reset already pass: they describe the wizard as it stands.

- [ ] **Step 3: Change the wizard.**

  ```bash
  git apply --check $P/task-2-code.patch && git apply $P/task-2-code.patch
  ```

  ````diff
  --- a/scripts/proxmox/sigmond-wizard.sh
  +++ b/scripts/proxmox/sigmond-wizard.sh
  @@ -614,6 +614,49 @@
       printf '10.99.0.2\n'
   }
 
  +# ── what crosses to the guest agent ─────────────────────────────────────────
  +# ⛔ NO BYTE ABOVE 0x7F MAY CROSS TO THE GUEST AGENT.  Proxmox (PVE 9.1.1,
  +# qemu-server 9.0.30) writes a request that holds non-ASCII characters SHORT:
  +# as many bytes as the request has characters, so the closing bytes never
  +# arrive.  About one time in six the agent's parser then waits for them for
  +# good: it keeps its PID, reads every later request and answers none
  +# (tasks/plan-guest-agent-requests.md).  An operator's own answer can hold
  +# such a character -- a name with an accent, a password -- so gexec and
  +# guest_out send every command base64-encoded: the request holds ASCII
  +# whatever the command holds.  tests/test_wizard_guest_senders.py fails on
  +# any byte above 0x7F in a request.
  +#
  +# The guest decodes the word and runs it in the login shell it stands in, as
  +# `bash -lc "<command>"` did.  The command arrives as an ARGUMENT, never on
  +# the shell's standard input: a command that reads its input would swallow
  +# whatever followed it there.
  +qga_b64(){ printf '%s' "$1" | base64 -w0; }
  +QGA_RUN='eval "$(printf %s "$1" | base64 -d)"'
  +
  +# qga_reset <vmid> : one 0xFF byte and a sync request down the agent's socket.
  +# The byte resets the agent's parser; the QGA protocol defines it and Proxmox
  +# never sends it.  Returns 0 when the agent answered the sync.  The same bytes
  +# stand in the console panel (sigmond-appliance firstboot-v3.sh), pm-align.py
  +# and pm-heartbeat.py; tests/test_qga_reset.py holds them equal.
  +QGA_DIR="${SIGMOND_QGA_DIR:-/var/run/qemu-server}"
  +QGA_NOTE="${SIGMOND_QGA_NOTE:-/run/sigmond/qga-reset}"
  +qga_reset(){
  +    timeout 10 python3 -c 'import socket,sys
  +s=socket.socket(socket.AF_UNIX); s.settimeout(4)
  +try:
  +    s.connect(sys.argv[2]+"/"+sys.argv[1]+".qga")
  +    s.sendall(b"\xff"+b"{\"execute\":\"guest-sync-delimited\",\"arguments\":{\"id\":4242}}\n")
  +    sys.exit(0 if b"4242" in s.recv(4096) else 1)
  +except Exception:
  +    sys.exit(1)' "$1" "$QGA_DIR" >/dev/null 2>&1 </dev/null
  +}
  +# One line, replaced each time: when the last reset brought the agent back,
  +# and who sent it.  pm-heartbeat.py reports from it.
  +qga_note_reset(){
  +    mkdir -p "$(dirname "$QGA_NOTE")" 2>/dev/null
  +    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" > "$QGA_NOTE" 2>/dev/null
  +}
  +
   gexec(){ # gexec <timeout-s> <command...>  → runs in guest, echoes exitcode
       # A guest agent that dies mid-wizard used to poison every LATER step:
       # each one returned "QEMU guest agent is not running", each printed its
  @@ -625,9 +668,10 @@
       # A command that merely exits non-zero is NOT retried — that is a real
       # answer from a live agent.
       local t="$1"; shift
  -    local out rc try i
  +    local out rc try i b
  +    b=$(qga_b64 "$*")
       for try in 1 2; do
  -        out=$(qm guest exec "$VMID" --timeout "$t" -- bash -lc "$*" 2>&1 </dev/null)
  +        out=$(qm guest exec "$VMID" --timeout "$t" -- bash -lc "$QGA_RUN" bash "$b" 2>&1 </dev/null)
           rc=$(echo "$out" | grep -o '"exitcode" *: *[0-9-]*' | grep -o '[0-9-]*$' | head -1)
           echo "$out" >> "$LOG"
           [ "${rc:-1}" = "0" ] && return 0
  @@ -635,8 +679,13 @@
               *"guest agent is not running"*|*"got timeout"*|*"QGA"*)
                   [ "$try" = 2 ] && return 1
                   say "guest agent stopped answering — waiting for it before retrying"
  +                # One reset before the wait: an agent that stands deaf hears
  +                # again at once, and nothing inside the VM restarts.
  +                if ! timeout 10 qm agent "$VMID" ping >/dev/null 2>&1 </dev/null; then
  +                    qga_reset "$VMID" && qga_note_reset wizard
  +                fi
                   for i in $(seq 1 24); do
  -                    qm agent "$VMID" ping >/dev/null 2>&1 </dev/null && break
  +                    timeout 10 qm agent "$VMID" ping >/dev/null 2>&1 </dev/null && break
                       sleep 5
                   done
                   ;;
  @@ -650,7 +699,7 @@
   # exit code; preflight_devices already needed the output and open-coded this.
   guest_out() {
       local t="$1"; shift
  -    qm guest exec "$VMID" --timeout "$t" -- bash -lc "$*" 2>/dev/null </dev/null \
  +    qm guest exec "$VMID" --timeout "$t" -- bash -lc "$QGA_RUN" bash "$(qga_b64 "$*")" 2>/dev/null </dev/null \
         | python3 -c 'import json,sys
   try:  print(json.load(sys.stdin).get("out-data",""))
   except Exception: pass' 2>/dev/null || true
  @@ -1411,6 +1460,12 @@
       # hang, and this run would then sit on the pairing's lock.
       for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
           timeout 10 qm agent "$VMID" ping >/dev/null 2>&1 </dev/null && break
  +        # One reset after the first silent ping: an agent that stands deaf
  +        # hears again at once, and this run need not fail over it.
  +        if [ "$i" = 1 ] && qga_reset "$VMID"; then
  +            qga_note_reset pairing
  +            continue
  +        fi
           if [ "$i" = 12 ]; then
               RAC_PAIR_FAIL="the guest agent in VM $VMID does not answer"
               return 1
  @@ -1599,12 +1654,22 @@
       say "      SIGMOND_VMID=<id> sigmond-setup --reconfigure"
       exit 1
   fi
  +# >>> agent_wait (tests/test_wizard_guest_senders.py loads exactly this block)
   _vmip=$(mgmt_vm_ip)
   _healed=0
   for i in $(seq 1 60); do
  -    qm agent "$VMID" ping >/dev/null 2>&1 </dev/null && break
  +    timeout 10 qm agent "$VMID" ping >/dev/null 2>&1 </dev/null && break
       [ "$i" = 1 ] && qm start "$VMID" >/dev/null 2>&1 </dev/null
  -    # ⚡ SELF-HEAL A STALE CHANNEL.  Observed repeatedly on AI6VN 2026-09-29:
  +    # ONE RESET FIRST.  At the second silent ping the VM has had its start, so
  +    # the agent may stand deaf: it reads every request and answers none
  +    # (tasks/plan-guest-agent-requests.md).  One 0xFF byte resets its parser.
  +    # Nothing inside the VM restarts, and the private link plays no part.
  +    if [ "$i" = 2 ] && qga_reset "$VMID"; then
  +        qga_note_reset wizard
  +        say "  the guest agent stood deaf; one parser reset brought it back"
  +        continue
  +    fi
  +    # ⚡ SELF-HEAL A STALE CHANNEL, the second resort.  Observed repeatedly on AI6VN 2026-09-29:
       # the agent is `active` inside the guest with NRestarts=0, having served
       # requests moments earlier, while the host insists "QEMU guest agent is
       # not running".  It is the host<->guest channel that has desynced, not the
  @@ -1632,7 +1697,7 @@
       [ $((i % 3)) = 0 ] && say "  still waiting for the guest agent ($((i * 5))s of 300s)..."
       sleep 5
   done
  -if ! qm agent "$VMID" ping >/dev/null 2>&1 </dev/null; then
  +if ! timeout 10 qm agent "$VMID" ping >/dev/null 2>&1 </dev/null; then
       say "ERROR: guest agent in VM $VMID not answering after 300s."
       say "  The VM can be up and reachable over ssh while the agent channel is"
       say "  stale — the service inside the guest shows active, but the host still"
  @@ -1642,6 +1707,7 @@
       say "  If the VM was never imported, plug in the Sigmond USB to trigger it."
       exit 1
   fi
  +# <<< agent_wait
 
   # rd — read that ABORTS on EOF instead of letting a validation loop spin
   # forever when piped stdin runs dry (nested-test hang, 2026-08-11).  On a
  ````

- [ ] **Step 4: Run the tests and watch them pass.**

  Run: `.venv/bin/pytest tests/test_wizard_guest_senders.py tests/test_wizard_pair_vm.py -q 2>&1 | tail -1`
  Expected: `165 passed`.
  Run: `bash -n scripts/proxmox/sigmond-wizard.sh && echo parses` — expected: `parses`.
  Run: `.venv/bin/pytest tests/test_pm_align.py tests/test_wizard_remote_default.py tests/test_wizard_rac_register.py tests/test_rac_switch.py tests/test_wizard_uploads.py -q 2>&1 | tail -1`
  Expected: all pass, none failing. `tests/test_pm_align.py` runs pm-align's frozen reader over the wizard; it
  proves that the new lines hid no heredoc from it.
  Run: `.venv/bin/pytest -q 2>&1 | tail -1` — expected: `4042 passed, 1 warning, 65 subtests passed`.

- [ ] **Step 5: Repeat the mutations.** Each on a copy: `cp scripts/proxmox/sigmond-wizard.sh /tmp/w.sh`, make the
  one change, then
  `SIGMOND_TEST_WIZARD=/tmp/w.sh .venv/bin/pytest tests/test_wizard_guest_senders.py tests/test_wizard_pair_vm.py -q -rf 2>&1 | grep -E '^FAILED|passed|failed'`.
  Both test files read the copy's path from that variable. Each row must turn red the tests it names, and no
  other.

  | Mutation of the copy | Tests that must fail |
  |---|---|
  | `gexec` sends `bash -lc "$*"` again | `test_no_request_holds_a_byte_above_0x7f[3-gexec]`, `test_an_operators_answer_with_an_accent_reaches_the_guest_whole`, `test_the_command_arrives_as_an_argument_not_on_stdin` |
  | `guest_out` sends `bash -lc "$*"` again | `test_no_request_holds_a_byte_above_0x7f[3-guest_out]` |
  | `QGA_RUN='printf %s "$1" \| base64 -d \| bash'` (the command on stdin) | `test_the_command_arrives_as_an_argument_not_on_stdin` |
  | `s.sendall(b"\xff"+` becomes `s.sendall(b"\xfe"+` | `test_a_deaf_agent_gets_one_reset_and_the_step_then_runs`, `test_the_wizard_resets_a_deaf_agent_before_it_restarts_anything` |
  | In `gexec`'s wait, `qga_reset "$VMID" && qga_note_reset wizard` becomes `:` | `test_a_deaf_agent_gets_one_reset_and_the_step_then_runs` |
  | In `gexec`'s wait, drop `&& qga_note_reset wizard` | `test_a_deaf_agent_gets_one_reset_and_the_step_then_runs` |
  | In `gexec`'s wait, reset without the ping before it | `test_a_request_that_times_out_under_a_hearing_agent_brings_no_reset` |
  | In the wizard's own wait, `if [ "$i" = 2 ] && qga_reset "$VMID"; then` becomes `if false; then` | `test_the_wizard_resets_a_deaf_agent_before_it_restarts_anything` |
  | In the wizard's own wait, `"$i" = 2` becomes `"$i" = 9` | `test_the_wizard_resets_a_deaf_agent_before_it_restarts_anything` |
  | Add `x=$(qm guest exec "$VMID" --timeout 5 -- hostname)` above `preflight_devices() {` | `test_every_direct_request_in_the_wizard_is_ascii_and_counted` |
  | Add `# café` inside `"ip -4 -br addr show scope global"` | `test_every_direct_request_in_the_wizard_is_ascii_and_counted` |
  | In `rac_pair_locked`'s wait, delete the four lines of the reset | `test_a_deaf_agent_gets_one_reset_and_the_pairing_goes_on`, `test_an_agent_that_stays_silent_gets_one_reset_and_no_more` |
  | In `rac_pair_locked`'s wait, delete `qga_note_reset pairing` | `test_a_deaf_agent_gets_one_reset_and_the_pairing_goes_on` |
  | In `rac_pair_locked`'s wait, reset at every silent ping (`if qga_reset "$VMID"; then`) | `test_an_agent_that_stays_silent_gets_one_reset_and_no_more` |

- [ ] **Step 6: Commit.**

  ```bash
  cd /home/mjh/hamsci/repos/sigmond
  git add scripts/proxmox/sigmond-wizard.sh tests/test_wizard_guest_senders.py tests/test_wizard_pair_vm.py
  git commit -m "wizard: gexec and guest_out send every command base64-encoded, and a silent agent gets one reset first" \
             -m "Proxmox writes a guest-agent request that holds non-ASCII characters short, and about one time in six the agent then stands deaf for good.  An operator's own answer can hold such a character.  The two senders now encode the command; the guest decodes the word and runs it in the login shell it stands in, as an argument and never on stdin.  gexec's wait, the pairing's wait and the wizard's own wait each send one parser reset (the byte 0xFF) after a failed ping, ahead of the restart over ssh, and leave one line in /run/sigmond/qga-reset when the agent answers." \
             -m "Design: tasks/plan-guest-agent-requests.md."
  ```

---

### Task 3: the heartbeat and pm-align reset a silent agent once, and the heartbeat reports every reset

**Files:**
- Create: `/home/mjh/hamsci/repos/sigmond/tests/test_qga_reset.py`
- Modify: `/home/mjh/hamsci/repos/sigmond/scripts/proxmox/pm-heartbeat.py`
- Modify: `/home/mjh/hamsci/repos/sigmond/scripts/proxmox/pm-align.py`

**Interfaces:**
- Consumes: the wizard's `qga_reset` (Task 2) and the panel's (Task 1). One test of this task requires both to
  stand committed: `test_the_panels_reset_is_the_wizards_line_for_line` reads
  `../sigmond-appliance/firstboot-v3.sh` and skips when no such checkout exists.
- Produces, in `pm-heartbeat.py`: `QGA_DIR`, `QGA_RESET`, `QGA_NOTE`, `SOFT_FINDING_KINDS`,
  `_qga_reset(vmid) -> bool`, `_note_reset(who) -> None`, `_take_reset_note() -> str | None`; three optional
  arguments on `_check_guest_exec` and on `build_doctor_raw` (`reset_agent`, `note_reset`, `take_reset_note`),
  each `None` by default, so that every existing caller and test keeps its behaviour. In `pm-align.py`:
  `QGA_DIR`, `QGA_RESET`, `QGA_NOTE`, `_qga_reset`, `_note_reset`. `push_record`'s signature does not change.

**What you need to know:**
- `pm-heartbeat.py` runs on the Proxmox host every five minutes and cannot import sigmond. Its doctor block runs
  three checks; the first asks the guest agent to run `/usr/bin/true`. Today a silent agent in a running VM reads
  `guest-agent-wedged`, and any finding reads INVALID (red on the fleet board).
- The panel runs every five minutes too, and will usually reset a deaf agent before the heartbeat looks. So the
  heartbeat reports from the record, whoever wrote it: `_take_reset_note` reads `/run/sigmond/qga-reset` and
  moves it to `qga-reset.seen`, so that one reset costs one beat.
- A finding of kind `guest-agent-reset`, alone, reads INCONCLUSIVE: the station works again, and someone should
  know what it took. Beside any other finding the block still reads INVALID.
- A failure of another kind (`guest-exec-error`) brings no reset, and leaves the record for a later beat.
- `pm-align.py` copies one record into the VM through the guest agent (`push_record`), already base64-encoded.
  It gets one reset before it gives up, and only when qm's answer says the agent does not run.
- Both files travel to stations by `pm-align` itself, alone. Each carries its own copy of the reset. Do not move
  it into a shared module.
- Two details of loading these scripts in a test: each has a dash in its name, so the test loads it by path; and
  `pm-align.py`'s dataclasses need `sys.modules[name]` set before the module runs.

- [ ] **Step 1: Add the test.**

  ```bash
  cd /home/mjh/hamsci/repos/sigmond
  P=/home/mjh/hamsci/repos/sigmond/.superpowers/sdd/plan-guest-agent-requests-v371/proto
  git apply --check $P/task-3-tests.patch && git apply $P/task-3-tests.patch
  ```

  ````diff
  diff --git a/tests/test_qga_reset.py b/tests/test_qga_reset.py
  new file mode 100644
  --- /dev/null
  +++ b/tests/test_qga_reset.py
  @@ -0,0 +1,379 @@
  +"""One parser reset for the guest agent, in four programs
  +(tasks/plan-guest-agent-requests.md section 4).
  +
  +A guest agent can stand deaf: it keeps its process, reads every request and
  +answers none.  One 0xFF byte from the host resets its parser.  The console
  +panel, the wizard, pm-align and pm-heartbeat each carry the few lines that
  +send it, because each must run with nothing beside it.  These tests hold the
  +four copies to the same bytes, and they pin what the heartbeat and pm-align
  +do around a reset.
  +"""
  +import importlib.util
  +import os
  +import re
  +import socket
  +import sys
  +import threading
  +from datetime import datetime, timezone
  +from pathlib import Path
  +
  +import pytest
  +
  +REPO = Path(__file__).resolve().parent.parent
  +PROXMOX = Path(os.environ.get("SIGMOND_TEST_PROXMOX") or REPO / "scripts" / "proxmox")
  +WIZARD = Path(os.environ.get("SIGMOND_TEST_WIZARD") or REPO / "scripts" / "proxmox" / "sigmond-wizard.sh")
  +FIRSTBOOT = Path(os.environ.get("SIGMOND_TEST_FIRSTBOOT")
  +                 or REPO.parent / "sigmond-appliance" / "firstboot-v3.sh")
  +
  +if str(REPO / "lib") not in sys.path:
  +    sys.path.insert(0, str(REPO / "lib"))
  +
  +from sigmond import heartbeat_schema  # noqa: E402
  +
  +
  +def load(name, filename):
  +    spec = importlib.util.spec_from_file_location(name, PROXMOX / filename)
  +    mod = importlib.util.module_from_spec(spec)
  +    sys.modules[name] = mod          # pm-align's dataclasses look their module up by name
  +    spec.loader.exec_module(mod)
  +    return mod
  +
  +
  +hb = load("pm_heartbeat_qga", "pm-heartbeat.py")
  +pa = load("pm_align_qga", "pm-align.py")
  +
  +#: The bytes every reset sends: the byte that resets the agent's parser, then
  +#: one sync request that the agent must answer.
  +RESET = b"\xff" + b'{"execute":"guest-sync-delimited","arguments":{"id":4242}}\n'
  +STAMP = r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ"
  +
  +
  +def rr(rc, stdout="", stderr=""):
  +    return hb.RunResult(rc, stdout, stderr)
  +
  +
  +DEAF = rr(255, "", "QEMU guest agent is not running")
  +RUNNING = rr(0, "status: running")
  +
  +
  +# -- the four copies send the same bytes ---------------------------------------------------
  +
  +def test_the_heartbeat_and_pm_align_hold_the_reset_bytes():
  +    assert hb.QGA_RESET == RESET
  +    assert pa.QGA_RESET == RESET
  +    assert hb.QGA_NOTE == pa.QGA_NOTE == "/run/sigmond/qga-reset"
  +
  +
  +def bash_reset_snippet(text: str) -> str:
  +    """The python3 one-liner inside a bash `qga_reset`, as the script holds it."""
  +    m = re.search(r"^qga_reset\(\)\{\n(.*?)^\}\n", text, re.S | re.M)
  +    assert m, "no qga_reset function"
  +    return m.group(1)
  +
  +
  +def test_the_wizards_reset_holds_the_same_bytes():
  +    body = bash_reset_snippet(WIZARD.read_text())
  +    assert r's.sendall(b"\xff"+b"{\"execute\":\"guest-sync-delimited\",\"arguments\":{\"id\":4242}}\n")' in body
  +
  +
  +def test_the_panels_reset_is_the_wizards_line_for_line():
  +    """The panel lives in another repository.  With that checkout beside this
  +    one, its function must equal the wizard's; without it, the panel's own
  +    test (test-panel-agent.sh) still holds its bytes."""
  +    if not FIRSTBOOT.exists():
  +        pytest.skip(f"no sigmond-appliance checkout at {FIRSTBOOT.parent}")
  +    assert bash_reset_snippet(FIRSTBOOT.read_text()) == bash_reset_snippet(WIZARD.read_text())
  +
  +
  +# -- the Python form, against a stand-in socket ---------------------------------------------
  +
  +class Agent:
  +    """The agent's side of the socket.  mode: answer | wrong | hangup | silent
  +    (it reads the request and holds the connection open without a word, as a
  +    socket that another request occupies would)."""
  +
  +    def __init__(self, tmp_path: Path, mode="answer"):
  +        self.got, self.mode, self.release = b"", mode, threading.Event()
  +        self.srv = socket.socket(socket.AF_UNIX)
  +        self.srv.bind(str(tmp_path / "100.qga"))
  +        self.srv.listen(1)
  +        self.srv.settimeout(10)
  +        self.t = threading.Thread(target=self.run, daemon=True)
  +        self.t.start()
  +
  +    def run(self):
  +        try:
  +            c, _ = self.srv.accept()
  +        except OSError:
  +            return
  +        c.settimeout(5)
  +        try:
  +            while not self.got.endswith(b"\n"):
  +                d = c.recv(4096)
  +                if not d:
  +                    break
  +                self.got += d
  +            if self.mode == "answer":
  +                c.sendall(b"\xff" + b'{"return": 4242}\n')
  +            elif self.mode == "wrong":
  +                c.sendall(b"\xff" + b'{"return": 17}\n')
  +            elif self.mode == "silent":
  +                self.release.wait(20)
  +        except OSError:
  +            pass
  +        c.close()
  +
  +
  +@pytest.mark.parametrize("mod", [hb, pa], ids=["pm-heartbeat", "pm-align"])
  +def test_a_reset_that_the_agent_answers_reads_true(tmp_path, monkeypatch, mod):
  +    monkeypatch.setattr(mod, "QGA_DIR", str(tmp_path))
  +    agent = Agent(tmp_path)
  +    assert mod._qga_reset(100) is True
  +    agent.t.join(5)
  +    assert agent.got == RESET
  +
  +
  +@pytest.mark.parametrize("mod", [hb, pa], ids=["pm-heartbeat", "pm-align"])
  +@pytest.mark.parametrize("mode", ["wrong", "hangup"])
  +def test_a_reset_without_its_own_answer_reads_false(tmp_path, monkeypatch, mod, mode):
  +    monkeypatch.setattr(mod, "QGA_DIR", str(tmp_path))
  +    Agent(tmp_path, mode)
  +    assert mod._qga_reset(100) is False
  +
  +
  +def test_a_reset_that_gets_no_word_gives_up_within_seconds(tmp_path, monkeypatch):
  +    """A caller must never hang on the socket: the panel runs under a lock
  +    that the next refresh waits behind."""
  +    import time
  +    monkeypatch.setattr(hb, "QGA_DIR", str(tmp_path))
  +    agent = Agent(tmp_path, "silent")
  +    t0 = time.monotonic()
  +    assert hb._qga_reset(100) is False
  +    took = time.monotonic() - t0
  +    agent.release.set()
  +    assert 3.0 < took < 8.0, took
  +
  +
  +@pytest.mark.parametrize("mod", [hb, pa], ids=["pm-heartbeat", "pm-align"])
  +def test_a_reset_with_no_socket_reads_false(tmp_path, monkeypatch, mod):
  +    monkeypatch.setattr(mod, "QGA_DIR", str(tmp_path / "absent"))
  +    assert mod._qga_reset(100) is False
  +
  +
  +# -- the record one reset leaves ----------------------------------------------------------
  +
  +@pytest.mark.parametrize("mod,who", [(hb, "heartbeat"), (pa, "pm-align")])
  +def test_a_reset_that_worked_leaves_one_line(tmp_path, monkeypatch, mod, who):
  +    note = tmp_path / "run" / "qga-reset"
  +    monkeypatch.setattr(mod, "QGA_NOTE", str(note))
  +    mod._note_reset("first")
  +    mod._note_reset(who)
  +    assert re.fullmatch(rf"{STAMP} {re.escape(who)}\n", note.read_text())
  +
  +
  +@pytest.mark.parametrize("mod", [hb, pa], ids=["pm-heartbeat", "pm-align"])
  +def test_a_record_that_cannot_be_written_raises_nothing(tmp_path, monkeypatch, mod):
  +    blocker = tmp_path / "file"
  +    blocker.write_text("not a directory")
  +    monkeypatch.setattr(mod, "QGA_NOTE", str(blocker / "qga-reset"))
  +    mod._note_reset("heartbeat")
  +
  +
  +def test_the_heartbeat_takes_the_record_once(tmp_path, monkeypatch):
  +    note = tmp_path / "qga-reset"
  +    monkeypatch.setattr(hb, "QGA_NOTE", str(note))
  +    note.write_text("2026-10-10T02:03:04Z panel\n")
  +    assert hb._take_reset_note() == "2026-10-10T02:03:04Z panel"
  +    assert not note.exists()
  +    assert (tmp_path / "qga-reset.seen").read_text() == "2026-10-10T02:03:04Z panel\n"
  +    assert hb._take_reset_note() is None
  +
  +
  +@pytest.mark.parametrize("raw,want", [
  +    ("2026-10-10T02:03:04Z panel\x1b[31m\x00\n", "2026-10-10T02:03:04Z panel[31m"),
  +    ("x" * 300 + "\n", "x" * 80),
  +    ("\n", None),
  +    ("", None),
  +])
  +def test_the_record_travels_as_printable_text_of_bounded_length(tmp_path, monkeypatch, raw, want):
  +    note = tmp_path / "qga-reset"
  +    monkeypatch.setattr(hb, "QGA_NOTE", str(note))
  +    note.write_text(raw)
  +    assert hb._take_reset_note() == want
  +
  +
  +# -- the heartbeat: one reset before it calls the agent wedged ----------------------------
  +
  +def check(answers, status=RUNNING, reset=True, note=None):
  +    """Run _check_guest_exec with a scripted agent.  Returns (finding, log)."""
  +    log = []
  +    answers = list(answers)
  +
  +    def run_guest_exec(vmid):
  +        log.append("exec")
  +        return answers.pop(0)
  +
  +    def reset_agent(vmid):
  +        log.append("reset")
  +        return reset
  +
  +    def take():
  +        log.append("take")
  +        return note
  +
  +    f = hb._check_guest_exec(100, run_guest_exec, lambda vmid: status, reset_agent,
  +                             lambda who: log.append(f"note {who}"), take)
  +    return f, log
  +
  +
  +def test_a_deaf_agent_that_a_reset_brings_back_reads_reset_not_wedged():
  +    f, log = check([DEAF, rr(0)], note="2026-10-10T02:03:04Z heartbeat")
  +    assert f["kind"] == "guest-agent-reset"
  +    assert "2026-10-10T02:03:04Z heartbeat" in f["detail"]
  +    assert log == ["exec", "reset", "note heartbeat", "exec", "take"]
  +
  +
  +def test_a_deaf_agent_that_no_reset_brings_back_reads_wedged():
  +    f, log = check([DEAF], reset=False)
  +    assert f["kind"] == "guest-agent-wedged"
  +    assert "one parser reset brought no answer" in f["detail"]
  +    assert log == ["exec", "reset"]
  +
  +
  +def test_an_agent_that_answers_the_reset_and_fails_again_reads_wedged():
  +    f, log = check([DEAF, DEAF])
  +    assert f["kind"] == "guest-agent-wedged"
  +    assert "answered a parser reset and failed the next request" in f["detail"]
  +    assert log == ["exec", "reset", "note heartbeat", "exec"]
  +
  +
  +def test_a_stopped_vm_gets_no_reset():
  +    f, log = check([DEAF], status=rr(0, "status: stopped"))
  +    assert f["kind"] == "vm-stopped"
  +    assert log == ["exec"]
  +
  +
  +def test_another_programs_reset_since_the_last_beat_is_reported():
  +    f, log = check([rr(0)], note="2026-10-10T02:03:04Z panel")
  +    assert f["kind"] == "guest-agent-reset"
  +    assert "2026-10-10T02:03:04Z panel" in f["detail"]
  +    assert log == ["exec", "take"]
  +
  +
  +def test_an_agent_that_answers_with_no_record_brings_no_finding():
  +    f, log = check([rr(0)])
  +    assert f is None
  +    assert log == ["exec", "take"]
  +
  +
  +def test_another_failure_gets_no_reset_and_leaves_the_record_for_a_later_beat():
  +    f, log = check([rr(1, "", "connection refused")], note="2026-10-10T02:03:04Z panel")
  +    assert f["kind"] == "guest-exec-error"
  +    assert log == ["exec"]
  +
  +
  +def test_the_old_call_shape_still_reads_wedged_at_once():
  +    f = hb._check_guest_exec(100, lambda vmid: DEAF, lambda vmid: RUNNING)
  +    assert f["kind"] == "guest-agent-wedged"
  +    assert "reset" not in f["detail"]
  +
  +
  +def doctor(findings, checks_run=3):
  +    return hb._map_doctor({"findings": findings, "checks_run": checks_run, "checks_total": 3, "cat": None})
  +
  +
  +def test_a_reset_alone_reads_inconclusive():
  +    block = doctor([hb._finding("guest-exec", "guest-agent-reset", "x")])
  +    assert block["verdict"] == "INCONCLUSIVE"
  +    assert "guest-agent-reset" in block["reason"]
  +
  +
  +def test_a_reset_beside_a_real_fault_reads_invalid():
  +    block = doctor([hb._finding("guest-exec", "guest-agent-reset", "x"),
  +                    hb._finding("hookscript", "hookscript-missing", "y")])
  +    assert block["verdict"] == "INVALID"
  +
  +
  +def test_a_wedged_agent_still_reads_invalid():
  +    assert doctor([hb._finding("guest-exec", "guest-agent-wedged", "x")])["verdict"] == "INVALID"
  +
  +
  +def test_the_production_wiring_hands_the_doctor_all_three_helpers(monkeypatch):
  +    seen = {}
  +
  +    def fake(vmid, expect_cat, *runners, **kw):
  +        seen.update(kw)
  +        return {"checks_run": 3, "checks_total": 3, "findings": [], "cat": None}
  +
  +    monkeypatch.setattr(hb, "build_doctor_raw", fake)
  +    hb.build_production_checks({"vmid": 100, "expect_cat": False})["doctor"]()
  +    assert seen == {"reset_agent": hb._qga_reset, "note_reset": hb._note_reset,
  +                    "take_reset_note": hb._take_reset_note}
  +
  +
  +def test_an_envelope_with_a_reset_finding_validates_against_the_real_schema():
  +    now = datetime(2026, 10, 10, 2, 5, 0, tzinfo=timezone.utc)
  +    checks = {
  +        "now": lambda: now,
  +        "uptime_s": lambda: 1000.0,
  +        "versions": lambda: {"pveversion": "pve-manager/9.1.1", "kernel": "6.14.0"},
  +        "doctor": lambda: hb.build_doctor_raw(
  +            100, False, lambda vmid: rr(0), lambda vmid: RUNNING,
  +            lambda vmid: rr(0, "hookscript: local:snippets/cpu-pin-100.sh\n"),
  +            lambda: {"present": False, "cpus_list": None, "schemata": None},
  +            take_reset_note=lambda: "2026-10-10T02:03:04Z panel"),
  +        "resources": lambda: hb.build_resources_raw(
  +            lambda: (0.1, 0.1, 0.1), lambda path: 10.0, lambda: 1000.0, lambda: None),
  +    }
  +    env = hb.assemble({"station": "b4-pm", "interval_sec": 300}, checks)
  +    assert heartbeat_schema.validate(env) == []
  +    assert env["blocks"]["doctor"]["verdict"] == "INCONCLUSIVE"
  +
  +
  +# -- pm-align: one reset before it gives up on the VM's copy ------------------------------
  +
  +class Completed:
  +    def __init__(self, rc, stdout="", stderr=""):
  +        self.returncode, self.stdout, self.stderr = rc, stdout, stderr
  +
  +
  +def push(tmp_path, monkeypatch, answers, reset):
  +    rec = tmp_path / "host-aligned.json"
  +    rec.write_text("{}")
  +    log, answers = [], list(answers)
  +    monkeypatch.setattr(pa, "_qga_reset", lambda vmid: log.append("reset") or reset)
  +    monkeypatch.setattr(pa, "_note_reset", lambda who: log.append(f"note {who}"))
  +
  +    def run(argv, **kw):
  +        log.append("exec")
  +        return answers.pop(0)
  +
  +    return pa.push_record(rec, 100, run=run), log
  +
  +
  +AGENT_DOWN = Completed(255, "", "QEMU guest agent is not running\n")
  +PUSHED = Completed(0, '{"exitcode": 0}')
  +
  +
  +def test_pm_align_resets_a_deaf_agent_and_copies_the_record(tmp_path, monkeypatch):
  +    ok, log = push(tmp_path, monkeypatch, [AGENT_DOWN, PUSHED], reset=True)
  +    assert ok is True
  +    assert log == ["exec", "reset", "note pm-align", "exec"]
  +
  +
  +def test_pm_align_gives_up_when_the_reset_brings_no_answer(tmp_path, monkeypatch):
  +    ok, log = push(tmp_path, monkeypatch, [AGENT_DOWN], reset=False)
  +    assert ok is False
  +    assert log == ["exec", "reset"]
  +
  +
  +def test_pm_align_sends_no_reset_for_another_failure(tmp_path, monkeypatch):
  +    ok, log = push(tmp_path, monkeypatch, [Completed(0, '{"exitcode": 1}')], reset=True)
  +    assert ok is False
  +    assert log == ["exec"]
  +
  +
  +def test_pm_align_sends_no_reset_when_the_copy_works(tmp_path, monkeypatch):
  +    ok, log = push(tmp_path, monkeypatch, [PUSHED], reset=True)
  +    assert ok is True
  +    assert log == ["exec"]
  ````

- [ ] **Step 2: Run it and watch it fail.**

  Run: `.venv/bin/pytest tests/test_qga_reset.py -q 2>&1 | tail -1`
  Expected: `33 failed, 5 passed`. The failures read `AttributeError: module 'pm_heartbeat_qga' has no attribute
  'QGA_RESET'` and its like, or a `TypeError` for an argument `_check_guest_exec` does not take yet. Five tests
  already pass, because they describe what Tasks 1 and 2 built or what the heartbeat does today:
  `test_the_wizards_reset_holds_the_same_bytes`, `test_the_panels_reset_is_the_wizards_line_for_line`,
  `test_the_old_call_shape_still_reads_wedged_at_once`, `test_a_reset_beside_a_real_fault_reads_invalid`,
  `test_a_wedged_agent_still_reads_invalid`. If the second of them SKIPS, the appliance checkout does not stand
  beside this one: stop and say so.

- [ ] **Step 3: Change the two programs.**

  ```bash
  git apply --check $P/task-3-code.patch && git apply $P/task-3-code.patch
  ```

  ````diff
  --- a/scripts/proxmox/pm-heartbeat.py
  +++ b/scripts/proxmox/pm-heartbeat.py
  @@ -12,12 +12,18 @@
   ----------------
   The fleetboard watches VMs; the PM underneath them — the deployment
   fulcrum and the project's named critical dependency — was invisible.
  -Worse, its ``qm guest exec`` channel to the VM can wedge SILENTLY: the
  -host-side QEMU guest agent connection resets while the guest agent
  -inside the VM looks perfectly healthy from the VM's own seat.  Only the
  -PM can see that from the outside, which is the entire reason this is a
  -separate emitter rather than one more block on the station's own
  -heartbeat.
  +Worse, its ``qm guest exec`` channel to the VM can go deaf SILENTLY: the
  +guest agent keeps its process, reads every request and answers none, and
  +looks perfectly healthy from the VM's own seat.  Only the PM can see that
  +from the outside, which is the entire reason this is a separate emitter
  +rather than one more block on the station's own heartbeat.
  +
  +One cause is known (sigmond tasks/plan-guest-agent-requests.md): Proxmox
  +writes a request that holds non-ASCII characters short, and the agent's
  +parser then waits for the missing bytes for good.  One 0xFF byte from the
  +host resets that parser.  So this emitter sends one reset before it calls
  +the agent wedged, and it reports every reset that brought the agent back,
  +its own or another program's, as a finding of its own.
 
   Wire-contract compatibility (READ BEFORE EDITING)
   --------------------------------------------------
  @@ -73,6 +79,7 @@
   import glob
   import json
   import os
  +import socket
   import subprocess
   import sys
   import time
  @@ -114,6 +121,20 @@
 
   DEFAULT_INTERVAL_SEC = 300
 
  +# One parser reset for the guest agent: the byte 0xFF, then one sync request.
  +# The QGA protocol defines the byte; Proxmox never sends it.  The console
  +# panel (sigmond-appliance firstboot-v3.sh), sigmond-wizard.sh and pm-align.py
  +# send the same bytes; tests/test_qga_reset.py holds the copies equal.
  +QGA_DIR = "/var/run/qemu-server"
  +QGA_RESET = b"\xff" + b'{"execute":"guest-sync-delimited","arguments":{"id":4242}}\n'
  +# Whoever resets the agent and gets its answer leaves one line here,
  +# "<UTC> <who>", replacing the line before.  This emitter reports the line
  +# once and moves the file aside, so that one reset costs one beat.
  +QGA_NOTE = "/run/sigmond/qga-reset"
  +# A finding of these kinds alone reads INCONCLUSIVE, not INVALID: the station
  +# works again, and someone should know what it took.
  +SOFT_FINDING_KINDS = frozenset({"guest-agent-reset"})
  +
   CONFIG_PATH = "/etc/pm-heartbeat/config.toml"
   SPOOL_DIR = "/var/lib/pm-heartbeat/spool"
   KNOWN_HOSTS_PATH = "/var/lib/pm-heartbeat/known_hosts"
  @@ -328,7 +349,8 @@
   # ---------------------------------------------------------------------------
 
   def build_doctor_raw(vmid, expect_cat, run_guest_exec, run_qm_status,
  -                     run_qm_config, probe_cat) -> dict:
  +                     run_qm_config, probe_cat, reset_agent=None,
  +                     note_reset=None, take_reset_note=None) -> dict:
       """Run the three doctor sub-checks, each independently wrapped.
 
       A sub-check that raises contributes a ``"<name>-unassessed"`` finding
  @@ -342,7 +364,8 @@
       checks_run = 0
 
       try:
  -        f = _check_guest_exec(vmid, run_guest_exec, run_qm_status)
  +        f = _check_guest_exec(vmid, run_guest_exec, run_qm_status,
  +                              reset_agent, note_reset, take_reset_note)
           checks_run += 1
           if f is not None:
               findings.append(f)
  @@ -379,31 +402,59 @@
       }
 
 
  -def _check_guest_exec(vmid, run_guest_exec, run_qm_status):
  +def _check_guest_exec(vmid, run_guest_exec, run_qm_status,
  +                      reset_agent=None, note_reset=None, take_reset_note=None):
       """``qm guest exec <vmid> --timeout 15 -- /usr/bin/true``.
 
  -    rc 0 -> no finding.  A failure distinguishes THE WEDGE DETECTOR
  -    ("QEMU guest agent is not running" while the VM IS running ->
  -    ``guest-agent-wedged``) from an unremarkable ``vm-stopped`` (checked
  -    via ``qm status``) from anything else (``guest-exec-error``, verbatim
  -    stderr).
  +    A failure distinguishes THE WEDGE DETECTOR ("QEMU guest agent is not
  +    running" while the VM IS running -> ``guest-agent-wedged``) from an
  +    unremarkable ``vm-stopped`` (checked via ``qm status``) from anything
  +    else (``guest-exec-error``, verbatim stderr).
  +
  +    One reset before the verdict: with ``reset_agent`` given, a silent agent
  +    in a running VM gets one parser reset and one more request.  Only a
  +    second failure reads ``guest-agent-wedged``.
  +
  +    An agent that answers may still have needed a reset since the last
  +    beat, from this emitter or from the panel, the wizard or pm-align.
  +    ``take_reset_note`` hands over the line such a reset left, and that
  +    reads ``guest-agent-reset``.
       """
       res = run_guest_exec(vmid)
  -    if res.rc == 0:
  -        return None
       stderr = (res.stderr or "").strip()
  -    if "QEMU guest agent is not running" in stderr:
  +    if res.rc != 0 and "QEMU guest agent is not running" in stderr:
           status = run_qm_status(vmid)
           status_text = (status.stdout or "").strip().lower()
           if "stopped" in status_text:
               return _finding("guest-exec", "vm-stopped",
                               f"VM {vmid} is stopped")
  +        if reset_agent is None or not reset_agent(vmid):
  +            tried = "" if reset_agent is None else \
  +                "; one parser reset brought no answer"
  +            return _finding(
  +                "guest-exec", "guest-agent-wedged",
  +                f"VM {vmid} is running but the QEMU guest agent is not: "
  +                f"{stderr}{tried}")
  +        if note_reset is not None:
  +            note_reset("heartbeat")
  +        res = run_guest_exec(vmid)
  +        stderr = (res.stderr or "").strip()
  +        if res.rc != 0:
  +            return _finding(
  +                "guest-exec", "guest-agent-wedged",
  +                f"VM {vmid}: the guest agent answered a parser reset and "
  +                f"failed the next request: "
  +                f"{stderr or f'qm guest exec exited {res.rc}'}")
  +    elif res.rc != 0:
  +        return _finding("guest-exec", "guest-exec-error",
  +                        stderr or f"qm guest exec exited {res.rc}")
  +    note = take_reset_note() if take_reset_note is not None else None
  +    if note:
           return _finding(
  -            "guest-exec", "guest-agent-wedged",
  -            f"VM {vmid} is running but the QEMU guest agent is not: "
  -            f"{stderr}")
  -    return _finding("guest-exec", "guest-exec-error",
  -                    stderr or f"qm guest exec exited {res.rc}")
  +            "guest-exec", "guest-agent-reset",
  +            f"VM {vmid}: the guest agent stood silent until a parser reset "
  +            f"brought it back ({note})")
  +    return None
 
 
   def _check_hookscript(vmid, run_qm_config):
  @@ -460,8 +511,11 @@
               data)
       if findings:
           kinds = _unique(f.get("kind") for f in findings)
  +        verdict = "INVALID"
  +        if all(k in SOFT_FINDING_KINDS for k in kinds):
  +            verdict = "INCONCLUSIVE"
           return _block(
  -            "INVALID",
  +            verdict,
               f"{len(findings)} doctor finding(s): {', '.join(kinds)}",
               data)
       return _block("VALID", f"{checks_run} checks passed", data)
  @@ -668,6 +722,44 @@
       return RunResult(proc.returncode, proc.stdout, proc.stderr)
 
 
  +def _qga_reset(vmid) -> bool:
  +    """One parser reset for the VM's guest agent; True when it answered."""
  +    try:
  +        with socket.socket(socket.AF_UNIX) as s:
  +            s.settimeout(4)
  +            s.connect(f"{QGA_DIR}/{vmid}.qga")
  +            s.sendall(QGA_RESET)
  +            return b"4242" in s.recv(4096)
  +    except OSError:
  +        return False
  +
  +
  +def _note_reset(who: str) -> None:
  +    """Leave the one line a reset that worked leaves.  Never raises."""
  +    try:
  +        os.makedirs(os.path.dirname(QGA_NOTE), exist_ok=True)
  +        stamp = datetime.now(timezone.utc).strftime(_EMITTED_AT_FORMAT)
  +        with open(QGA_NOTE, "w", encoding="ascii") as fh:
  +            fh.write(f"{stamp} {who}\n")
  +    except OSError:
  +        pass
  +
  +
  +def _take_reset_note():
  +    """The line the last reset left, or None; the file moves aside.
  +
  +    Printable ASCII only and 80 characters at most: the line travels in a
  +    heartbeat, and another program wrote it."""
  +    try:
  +        with open(QGA_NOTE, "r", encoding="ascii", errors="replace") as fh:
  +            line = fh.readline()
  +        os.replace(QGA_NOTE, QGA_NOTE + ".seen")
  +    except OSError:
  +        return None
  +    line = "".join(c for c in line if " " <= c <= "~").strip()[:80]
  +    return line or None
  +
  +
   def _run_qm_status(vmid) -> RunResult:
       proc = subprocess.run(["qm", "status", str(vmid)], capture_output=True,
                             text=True, timeout=15, check=False)
  @@ -763,7 +855,8 @@
           "versions": lambda: build_versions_raw(_run_pveversion, _run_uname),
           "doctor": lambda: build_doctor_raw(
               vmid, expect_cat, _run_qm_guest_exec, _run_qm_status,
  -            _run_qm_config, _probe_cat),
  +            _run_qm_config, _probe_cat, reset_agent=_qga_reset,
  +            note_reset=_note_reset, take_reset_note=_take_reset_note),
           "resources": lambda: build_resources_raw(
               _read_loadavg, _read_disk_used_pct, read_uptime,
               _read_temperature),
  --- a/scripts/proxmox/pm-align.py
  +++ b/scripts/proxmox/pm-align.py
  @@ -29,6 +29,7 @@
   import os
   import re
   import shutil
  +import socket
   import subprocess
   import sys
   import tempfile
  @@ -46,6 +47,18 @@
   PROXMOX_FILES = ("pm-heartbeat.py", "pm-heartbeat.service", "pm-heartbeat.timer",
                    "pm-heartbeat-setup.sh", "pm-align.py")
   RECORD = "/etc/sigmond-appliance/host-aligned.json"
  +
  +# One parser reset for the guest agent: the byte 0xFF, then one sync request
  +# (sigmond tasks/plan-guest-agent-requests.md).  Proxmox writes a request
  +# that holds non-ASCII characters short, and the agent's parser can then wait
  +# for the missing bytes for good; this byte resets it.  The console panel,
  +# sigmond-wizard.sh and pm-heartbeat.py send the same bytes;
  +# tests/test_qga_reset.py holds the copies equal.
  +QGA_DIR = "/var/run/qemu-server"
  +QGA_RESET = b"\xff" + b'{"execute":"guest-sync-delimited","arguments":{"id":4242}}\n'
  +# Whoever resets the agent and gets its answer leaves one line here,
  +# "<UTC> <who>"; pm-heartbeat.py reports it.
  +QGA_NOTE = "/run/sigmond/qga-reset"
   BACKUP_BASE = "/var/lib/pm-align/backup"
   _FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
   _TIMEOUT = 30
  @@ -464,11 +477,40 @@
       b64 = base64.b64encode(record_path.read_bytes()).decode()
       cmd = (f"mkdir -p /etc/sigmond-appliance && echo {b64} | base64 -d "
              f"> {RECORD}.tmp && mv {RECORD}.tmp {RECORD}")
  -    r = run(["qm", "guest", "exec", str(vmid), "--timeout", "30", "--", "bash", "-lc", cmd],
  -            capture_output=True, text=True, timeout=60)
  +    argv = ["qm", "guest", "exec", str(vmid), "--timeout", "30", "--", "bash", "-lc", cmd]
  +    r = run(argv, capture_output=True, text=True, timeout=60)
  +    # One reset before the verdict: a deaf agent hears again at once, and
  +    # nothing inside the VM restarts.
  +    if r.returncode != 0 and "guest agent is not running" in (r.stderr or "") \
  +            and _qga_reset(vmid):
  +        _note_reset("pm-align")
  +        r = run(argv, capture_output=True, text=True, timeout=60)
       return r.returncode == 0 and re.search(r'"exitcode"\s*:\s*0\b', r.stdout or "") is not None
 
 
  +def _qga_reset(vmid) -> bool:
  +    """One parser reset for the VM's guest agent; True when it answered."""
  +    try:
  +        with socket.socket(socket.AF_UNIX) as s:
  +            s.settimeout(4)
  +            s.connect(f"{QGA_DIR}/{vmid}.qga")
  +            s.sendall(QGA_RESET)
  +            return b"4242" in s.recv(4096)
  +    except OSError:
  +        return False
  +
  +
  +def _note_reset(who: str) -> None:
  +    """Leave the one line a reset that worked leaves.  Never raises."""
  +    try:
  +        os.makedirs(os.path.dirname(QGA_NOTE), exist_ok=True)
  +        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
  +        with open(QGA_NOTE, "w", encoding="ascii") as fh:
  +            fh.write(f"{stamp} {who}\n")
  +    except OSError:
  +        pass
  +
  +
   def _apply_record_push(record: Path, vmid: int, *, run) -> str:
       """"yes" or "no — <why>" for --apply's informational VM-copy line.
 
  ````

- [ ] **Step 4: Run the tests and watch them pass.**

  Run: `.venv/bin/pytest tests/test_qga_reset.py -q 2>&1 | tail -1` — expected: `38 passed` (about four seconds:
  one test waits out the reset's own timeout).
  Run: `.venv/bin/pytest tests/test_pm_heartbeat.py tests/test_pm_align.py tests/test_fleetboard.py -q 2>&1 | tail -1`
  Expected: all pass. `test_pm_heartbeat.py` holds the drift guard for the wire contract.
  Run: `python3 -m py_compile scripts/proxmox/pm-heartbeat.py scripts/proxmox/pm-align.py && echo compiles`.
  Run: `.venv/bin/pytest -q 2>&1 | tail -1` — expected: `4080 passed, 1 warning, 65 subtests passed`.

- [ ] **Step 5: Repeat the mutations.** Each on copies:
  `mkdir -p /tmp/m && cp scripts/proxmox/pm-heartbeat.py scripts/proxmox/pm-align.py /tmp/m/`, make the one
  change there, then `SIGMOND_TEST_PROXMOX=/tmp/m .venv/bin/pytest tests/test_qga_reset.py -q 2>&1 | tail -1`.

  Add `-rf` and read the names. Each row must turn red the tests it names, and no other.

  | Mutation of the copy | Tests that must fail |
  |---|---|
  | heartbeat: `if reset_agent is None or not reset_agent(vmid):` becomes `if True:` | `test_a_deaf_agent_that_a_reset_brings_back_reads_reset_not_wedged`, `test_a_deaf_agent_that_no_reset_brings_back_reads_wedged`, `test_an_agent_that_answers_the_reset_and_fails_again_reads_wedged` |
  | heartbeat: drop the two lines that call `note_reset("heartbeat")` | the first and the third of those three |
  | heartbeat: never run the second request after a reset | the first and the third of those three |
  | heartbeat: `if all(k in SOFT_FINDING_KINDS for k in kinds):` becomes `if False:` | `test_a_reset_alone_reads_inconclusive`, `test_an_envelope_with_a_reset_finding_validates_against_the_real_schema` |
  | heartbeat: `all(` becomes `any(` in that line | `test_a_reset_beside_a_real_fault_reads_invalid` |
  | heartbeat: delete the line `os.replace(QGA_NOTE, QGA_NOTE + ".seen")` | `test_the_heartbeat_takes_the_record_once` |
  | heartbeat: `line = line.strip()` in place of the printable filter | `test_the_record_travels_as_printable_text_of_bounded_length` (two of its four cases) |
  | heartbeat: `QGA_RESET = b"\xfe"` and the rest of the line | `test_the_heartbeat_and_pm_align_hold_the_reset_bytes`, `test_a_reset_that_the_agent_answers_reads_true[pm-heartbeat]` |
  | heartbeat: `return bool(s.recv(4096))` | `test_a_reset_without_its_own_answer_reads_false` (the `wrong` case for pm-heartbeat) |
  | heartbeat: the production wiring passes no helper | `test_the_production_wiring_hands_the_doctor_all_three_helpers` |
  | heartbeat: take the record even after a `guest-exec-error` | `test_another_failure_gets_no_reset_and_leaves_the_record_for_a_later_beat` |
  | pm-align: `QGA_RESET = b"\xfe"` and the rest of the line | `test_the_heartbeat_and_pm_align_hold_the_reset_bytes`, `test_a_reset_that_the_agent_answers_reads_true[pm-align]` |
  | pm-align: `and _qga_reset(vmid):` becomes `and False:` | `test_pm_align_resets_a_deaf_agent_and_copies_the_record`, `test_pm_align_gives_up_when_the_reset_brings_no_answer` |
  | pm-align: reset on any failure (`if True \`) | `test_pm_align_sends_no_reset_for_another_failure`, `test_pm_align_sends_no_reset_when_the_copy_works` |
  | pm-align: delete `_note_reset("pm-align")` | `test_pm_align_resets_a_deaf_agent_and_copies_the_record` |

- [ ] **Step 6: Commit.**

  ```bash
  cd /home/mjh/hamsci/repos/sigmond
  git add scripts/proxmox/pm-heartbeat.py scripts/proxmox/pm-align.py tests/test_qga_reset.py
  git commit -m "pm-heartbeat, pm-align: one parser reset before a silent guest agent counts as wedged, and every reset gets reported" \
             -m "A guest agent can stand deaf: it keeps its process, reads every request and answers none.  One 0xFF byte from the host resets its parser.  The heartbeat now sends that byte before it calls the agent wedged, and pm-align before it gives up on the VM's copy of its record.  Whoever resets the agent and gets its answer leaves one line in /run/sigmond/qga-reset; the heartbeat reports that line once as the finding guest-agent-reset, which alone reads INCONCLUSIVE, because the panel will usually have reset the agent before the heartbeat looks.  tests/test_qga_reset.py holds the four copies of the reset to the same bytes." \
             -m "Design: tasks/plan-guest-agent-requests.md."
  graphify update /home/mjh/hamsci/repos
  ```

---

### Task 4: the nested rig proves it on a real Proxmox host

**Files:**
- Create: `/home/mjh/hamsci/repos/sigmond-appliance/test-qga-rig-lines.sh` (mode 755)
- Modify: `/home/mjh/hamsci/repos/sigmond-appliance/test-nested-v3.sh` (a marked block after `# <<< remote_rig`;
  three places in Phase D)

**Interfaces:**
- Consumes: from Task 1, the word `qga_reset` in the panel script and the seven sentences listed there; the
  record `/run/sigmond/qga-reset` with ` panel` at the end of its line. From the rig as it stands: `say`,
  `rr_tok <KEY> <text>`, `nh_ok <answer>` and `$SSHN` (ssh to the nested Proxmox host).
- Produces: `qga_probe_panel`, `qga_probe_refresh`, `qga_probe_deafen`, `qga_probe_heal`, `qga_probe_bringup`
  (each prints a script for the nested host), `qga_fatal <message> <answer>`, and the variables `QGA_PANELFIX`,
  `QGA_BUILD`, `QGA_UNCHECKED`.

**What you need to know:**
- `test-nested-v3.sh` runs on the image rig, never here. It installs the image into a nested Proxmox host and a
  decoder VM, and its Phase D ends with the line `PHASE D PASS — NESTED TEST COMPLETE`, which
  `test-update-v3.sh` and `bless-release.sh` match. Keep that line.
- The update rig runs this same script against a v3.70 image for its base install. That image's panel carries no
  reset. So every check here first asks `qga_probe_panel`, and on an older panel says
  `GUEST-AGENT CHECKS NOT EVALUATED` and judges nothing. `test-update-v3.sh` relays every `NOT EVALUATED` line of
  its base install.
- The nest has no RX888. Bring-up there ends with `result=awaiting-sdr`, so the panel must say that it ran
  without the radio. On v3.70 the same state would have raised the ka9q-web alarm, had the request ever run.
- The deafening sends real box-drawing bytes inside a request, built with `printf` on the nested host so that
  the rig's own file stays ASCII. About one such request in six deafens the agent; the others cost the next
  request alone. The probe tries thirty times; it calls the agent deaf after three unanswered pings in a row.
  When thirty tries leave the agent hearing, the rig says that the heal went unproven. It does not fail: a
  Proxmox that has fixed its truncation would deafen nothing.
- **None of the rig's own lines has run.** The probes ran against stand-ins (Step 4). The lines that call them
  passed `bash -n`. Say so in your report.

- [ ] **Step 1: Add the test.**

  ```bash
  cd /home/mjh/hamsci/repos/sigmond-appliance
  P=/home/mjh/hamsci/repos/sigmond/.superpowers/sdd/plan-guest-agent-requests-v371/proto
  git apply --check $P/task-4-tests.patch && git apply $P/task-4-tests.patch
  chmod 755 test-qga-rig-lines.sh
  ```

  ````diff
  diff --git a/test-qga-rig-lines.sh b/test-qga-rig-lines.sh
  new file mode 100755
  --- /dev/null
  +++ b/test-qga-rig-lines.sh
  @@ -0,0 +1,156 @@
  +#!/bin/bash
  +# test-qga-rig-lines.sh — the scripts the nested rig runs on its Proxmox host
  +# for the guest-agent checks.  No VM, no root; about a second.
  +#
  +# Why: a rig check that cannot fail proves nothing, and a rig run costs forty
  +# minutes.  Each script here runs against stand-ins for qm, systemctl and the
  +# panel, once in the state it must report as good and once in the state it
  +# must report as bad.  The block under test is cut out of test-nested-v3.sh
  +# from between its two markers, so this runs what the rig sends.
  +#
  +#   usage: ./test-qga-rig-lines.sh [path/to/test-nested-v3.sh]
  +set -u
  +HERE="$(cd "$(dirname "$0")" && pwd)"
  +SRC="${1:-$HERE/test-nested-v3.sh}"
  +T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  +pass=0; fail=0
  +ok(){  printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
  +bad(){ printf '  FAIL  %s\n        %s\n' "$1" "${2:-}"; fail=$((fail+1)); }
  +has(){   case "$2" in *"$3"*) ok "$1" ;; *) bad "$1" "missing: $3 in: $(printf '%s' "$2" | tr '\n' ' ')" ;; esac; }
  +lacks(){ case "$2" in *"$3"*) bad "$1" "found: $3" ;; *) ok "$1" ;; esac; }
  +
  +sed -n '/^# >>> qga_rig/,/^# <<< qga_rig/p' "$SRC" > "$T/block.sh"
  +[ -s "$T/block.sh" ] || { echo "FAIL: no qga_rig block between its markers in $SRC"; exit 1; }
  +bash -n "$T/block.sh" || { echo "FAIL: the qga_rig block does not parse"; exit 1; }
  +. "$T/block.sh"
  +
  +# The stand-ins.  A scene directory $D holds: deaf (no ping answers), lose-next
  +# (the next ping alone gets no answer), deaf-at (the poison request with this
  +# number deafens the agent), unit (what systemctl is-active prints in the VM),
  +# unit-after (what it prints once the panel has run), panel-heals (a refresh
  +# brings the agent back and leaves its line), issue.txt (what a refresh paints).
  +mkdir -p "$T/bin"
  +cat > "$T/bin/qm" <<'EOF'
  +#!/bin/bash
  +case "$1" in
  +    agent)
  +        [ -e "$D/deaf" ] && exit 255
  +        [ -e "$D/lose-next" ] && { rm -f "$D/lose-next"; exit 255; }
  +        exit 0 ;;
  +    guest)
  +        case "$*" in
  +            *is-active*)
  +                [ -e "$D/deaf" ] && exit 255
  +                printf '{\n   "exitcode" : 3,\n   "exited" : 1,\n   "out-data" : "%s\\n"\n}\n' "$(cat "$D/unit" 2>/dev/null)" ;;
  +            *)
  +                n=$(( $(cat "$D/count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$D/count"
  +                printf '%s\0' "$@" > "$D/poison.$n"
  +                if [ "$n" = "$(cat "$D/deaf-at" 2>/dev/null)" ]; then : > "$D/deaf"; else : > "$D/lose-next"; fi ;;
  +        esac ;;
  +esac
  +EOF
  +cat > "$T/bin/systemctl" <<'EOF'
  +#!/bin/bash
  +echo "$*" >> "$D/systemctl.calls"
  +EOF
  +printf '#!/bin/bash\nshift; exec "$@"\n' > "$T/bin/timeout"
  +printf '#!/bin/bash\nexit 0\n' > "$T/bin/sleep"
  +cat > "$T/panel" <<'EOF'
  +#!/bin/bash
  +# qga_reset: the word the rig looks for in a panel that carries the fix
  +echo "vmid=$SIGMOND_VMID" >> "$D/panel.calls"
  +[ -e "$D/issue.txt" ] && cp "$D/issue.txt" "$QGA_ISSUE"
  +[ -e "$D/unit-after" ] && cp "$D/unit-after" "$D/unit"
  +if [ -e "$D/panel-heals" ]; then rm -f "$D/deaf"; echo "2026-10-10T03:00:00Z panel" > "$QGA_NOTE"; fi
  +exit 0
  +EOF
  +chmod 755 "$T/bin/"* "$T/panel"
  +
  +# run <scene> <probe function> [args...] : the probe's answer in the scene.
  +run(){
  +    local d="$T/$1"; shift; local probe="$1"; shift
  +    mkdir -p "$d"
  +    "$probe" | D="$d" QGA_PANEL="${QGA_PANEL_OVERRIDE:-$T/panel}" QGA_ISSUE="$d/issue" QGA_NOTE="$d/qga-reset" \
  +        PATH="$T/bin:$PATH" bash -s 100 "$@" 2>&1
  +}
  +
  +echo "== is the fix in this image's panel?"
  +has   "a panel with the reset: PANELFIX:1"            "$(run p1 qga_probe_panel)" "PANELFIX:1"
  +printf '#!/bin/bash\n# a panel from v3.70\n' > "$T/oldpanel"
  +has   "a panel from before v3.71: PANELFIX:0"         "$(QGA_PANEL_OVERRIDE=$T/oldpanel run p2 qga_probe_panel)" "PANELFIX:0"
  +has   "no panel at all: PANELFIX:0"                   "$(QGA_PANEL_OVERRIDE=$T/absent run p3 qga_probe_panel)" "PANELFIX:0"
  +
  +echo "== twelve refreshes, a ping after each"
  +out=$(run r1 qga_probe_refresh)
  +has   "a hearing agent: SILENT:0"                     "$out" "SILENT:0"
  +has   "no reset happened: NOTE:none"                  "$out" "NOTE:none"
  +has   "the answer ends END:1"                         "$out" "END:1"
  +[ "$(wc -l < "$T/r1/panel.calls")" = 12 ] && ok "the panel ran twelve times" || bad "the panel ran twelve times" "$(wc -l < "$T/r1/panel.calls")"
  +grep -qx 'vmid=100' "$T/r1/panel.calls" && ok "the panel got the VM's number" || bad "the panel got the VM's number" "$(head -1 "$T/r1/panel.calls")"
  +[ "$(tr '\n' '|' < "$T/r1/systemctl.calls")" = "stop sigmond-issue.timer|start sigmond-issue.timer|" ] \
  +    && ok "the panel's timer stops, and starts again at the end" \
  +    || bad "the panel's timer stops, and starts again at the end" "$(tr '\n' '|' < "$T/r1/systemctl.calls")"
  +mkdir -p "$T/r2"; : > "$T/r2/deaf"
  +has   "a deaf agent: SILENT:12"                       "$(run r2 qga_probe_refresh)" "SILENT:12"
  +mkdir -p "$T/r3"; echo "2026-10-10T02:00:00Z panel" > "$T/r3/qga-reset"
  +has   "a reset's line comes back whole"               "$(run r3 qga_probe_refresh)" "NOTE:2026-10-10T02:00:00Z panel"
  +
  +echo "== deafening the agent"
  +mkdir -p "$T/d1"; echo 3 > "$T/d1/deaf-at"; : > "$T/d1/qga-reset"; : > "$T/d1/qga-reset.seen"
  +out=$(run d1 qga_probe_deafen 40)
  +has   "the third request deafens: DEAF:3"             "$out" "DEAF:3"
  +[ "$(cat "$T/d1/count")" = 3 ] && ok "it stops at the request that did it" || bad "it stops at the request that did it" "$(cat "$T/d1/count")"
  +LC_ALL=C grep -q -P '[^\x00-\x7F]' "$T/d1/poison.1" && ok "the request holds bytes above 0x7F (else it deafens nothing)" \
  +    || bad "the request holds bytes above 0x7F" "$(tr '\0' ' ' < "$T/d1/poison.1")"
  +[ "$(tr '\n' '|' < "$T/d1/systemctl.calls")" = "stop sigmond-issue.timer pm-heartbeat.timer|" ] \
  +    && ok "both timers stop, and stay stopped for the heal" \
  +    || bad "both timers stop, and stay stopped" "$(tr '\n' '|' < "$T/d1/systemctl.calls")"
  +[ ! -e "$T/d1/qga-reset" ] && [ ! -e "$T/d1/qga-reset.seen" ] && ok "an earlier reset's line is cleared first" || bad "an earlier reset's line is cleared first" "still there"
  +mkdir -p "$T/d2"; echo 99 > "$T/d2/deaf-at"
  +out=$(run d2 qga_probe_deafen 5)
  +has   "one lost ping per try is not deafness: DEAF:0" "$out" "DEAF:0"
  +[ "$(cat "$T/d2/count")" = 5 ] && ok "it gives up after the tries it was given" || bad "it gives up after the tries it was given" "$(cat "$T/d2/count")"
  +
  +echo "== the heal"
  +mkdir -p "$T/h1"; : > "$T/h1/deaf"; : > "$T/h1/panel-heals"; echo "all quiet" > "$T/h1/issue.txt"
  +out=$(run h1 qga_probe_heal)
  +has   "a refresh that heals: HEARS:1"                 "$out" "HEARS:1"
  +has   "the reset's line names the panel"              "$out" "NOTE:2026-10-10T03:00:00Z panel"
  +has   "the panel no longer says the agent is silent"  "$out" "PANELSILENT:0"
  +[ "$(tr '\n' '|' < "$T/h1/systemctl.calls")" = "start sigmond-issue.timer pm-heartbeat.timer|" ] \
  +    && ok "both timers start again" || bad "both timers start again" "$(tr '\n' '|' < "$T/h1/systemctl.calls")"
  +mkdir -p "$T/h2"; : > "$T/h2/deaf"; echo " .. the decoder VM's guest agent does not answer, so" > "$T/h2/issue.txt"
  +out=$(run h2 qga_probe_heal)
  +has   "a refresh that does not heal: HEARS:0"         "$out" "HEARS:0"
  +has   "no line of a reset: NOTE:none"                 "$out" "NOTE:none"
  +has   "the panel says the agent is silent"            "$out" "PANELSILENT:1"
  +grep -q 'start sigmond-issue.timer pm-heartbeat.timer' "$T/h2/systemctl.calls" && ok "the timers start again all the same" || bad "the timers start again all the same" "not started"
  +
  +echo "== the bring-up line"
  +mkdir -p "$T/b1"; echo activating > "$T/b1/unit"; echo " >> STATION IS STILL BUILDING -- this is normal, not a fault." > "$T/b1/issue.txt"
  +out=$(run b1 qga_probe_bringup)
  +has   "a running unit reads on both sides"            "$out" "UNIT:activating/activating"
  +has   "the panel says it builds: BUILDING:1"          "$out" "BUILDING:1"
  +has   "and raises no ka9q-web alarm: NOWEB:0"         "$out" "NOWEB:0"
  +mkdir -p "$T/b2"; echo inactive > "$T/b2/unit"; echo " .. bring-up ran WITHOUT the radio: no SDR stood on the USB bus." > "$T/b2/issue.txt"
  +out=$(run b2 qga_probe_bringup)
  +has   "a finished unit reads inactive"                "$out" "UNIT:inactive/inactive"
  +has   "no radio: NOSDR:1"                             "$out" "NOSDR:1"
  +has   "no radio: BUILDING:0"                          "$out" "BUILDING:0"
  +mkdir -p "$T/b3"; echo inactive > "$T/b3/unit"; echo " !! BRING-UP FINISHED BUT ka9q-web IS NOT RUNNING" > "$T/b3/issue.txt"
  +has   "the false alarm of v3.70 would show: NOWEB:1"  "$(run b3 qga_probe_bringup)" "NOWEB:1"
  +mkdir -p "$T/b4"; : > "$T/b4/deaf"; echo " .. the decoder VM's guest agent does not answer, so" > "$T/b4/issue.txt"
  +out=$(run b4 qga_probe_bringup)
  +has   "a deaf agent: no unit state"                   "$out" "UNIT:none/none"
  +has   "a deaf agent: SILENT:1"                        "$out" "SILENT:1"
  +mkdir -p "$T/b5"; echo activating > "$T/b5/unit"; echo inactive > "$T/b5/unit-after"
  +has   "a unit that ends during the refresh shows both" "$(run b5 qga_probe_bringup)" "UNIT:activating/inactive"
  +for k in RETRY GAVEUP STOPPED; do has "the answer carries a $k token" "$out" "$k:0"; done
  +
  +echo "== the probes themselves stay ASCII"
  +[ "$(LC_ALL=C grep -c -P '[^\x00-\x7F]' "$T/block.sh")" = 0 ] && ok "the block holds no byte above 0x7F" \
  +    || bad "the block holds no byte above 0x7F" "$(LC_ALL=C grep -n -P '[^\x00-\x7F]' "$T/block.sh" | head -2)"
  +
  +echo
  +echo "passed $pass, failed $fail"
  +[ "$fail" = 0 ]
  ````

- [ ] **Step 2: Run it and watch it fail.**

  Run: `bash test-qga-rig-lines.sh; echo "exit $?"`
  Expected: `FAIL: no qga_rig block between its markers in /home/mjh/hamsci/repos/sigmond-appliance/test-nested-v3.sh`
  and `exit 1`.

- [ ] **Step 3: Change the rig.**

  ```bash
  git apply --check $P/task-4-code.patch && git apply $P/task-4-code.patch
  ```

  ````diff
  --- a/test-nested-v3.sh
  +++ b/test-nested-v3.sh
  @@ -127,6 +127,125 @@
       LC_ALL=C awk '$2 == "ssh-ed25519" { print $3; exit }' "$1" 2>/dev/null
   }
   # <<< remote_rig
  +
  +# >>> qga_rig (test-qga-rig-lines.sh loads exactly this block)
  +# The guest-agent checks of Phase D (sigmond tasks/plan-guest-agent-requests.md).
  +#
  +# Each function below prints a script for the NESTED HOST; the rig pipes it to
  +# `$SSHN "bash -s <vmid>"`.  A script prints its facts as KEY:value lines and
  +# ends with END:1, so that rr_tok and nh_ok (the remote_rig block) can read
  +# them.  The scripts read three names from the environment, for the hermetic
  +# test alone: QGA_PANEL (the panel script), QGA_ISSUE (the file it paints) and
  +# QGA_NOTE (the line a reset leaves).
  +#
  +# qga_probe_panel: has this image's panel the fix?  PANELFIX:1 when the panel
  +# script carries the reset; 0 for a panel from before v3.71.
  +qga_probe_panel() {
  +    cat <<'QGAEOF'
  +P="${QGA_PANEL:-/usr/local/sbin/sigmond-issue}"
  +grep -q 'qga_reset' "$P" 2>/dev/null && echo PANELFIX:1 || echo PANELFIX:0
  +echo END:1
  +QGAEOF
  +}
  +# qga_probe_refresh: twelve panel refreshes, one ping after each.  The panel's
  +# own timer stands still meanwhile, because a refresh that finds another one
  +# running does nothing.  SILENT counts the pings that got no answer; NOTE
  +# holds the line a reset left, or "none".
  +qga_probe_refresh() {
  +    cat <<'QGAEOF'
  +VMID="$1"
  +P="${QGA_PANEL:-/usr/local/sbin/sigmond-issue}"
  +N="${QGA_NOTE:-/run/sigmond/qga-reset}"
  +systemctl stop sigmond-issue.timer >/dev/null 2>&1
  +trap 'systemctl start sigmond-issue.timer >/dev/null 2>&1' EXIT
  +n=0
  +for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  +    SIGMOND_VMID="$VMID" "$P" >/dev/null 2>&1
  +    timeout 10 qm agent "$VMID" ping >/dev/null 2>&1 || n=$((n+1))
  +done
  +echo "SILENT:$n"
  +if [ -e "$N" ]; then echo "NOTE:$(head -1 "$N")"; else echo "NOTE:none"; fi
  +echo END:1
  +QGAEOF
  +}
  +# qga_probe_deafen: send the request the panel of v3.70 sent, a command that
  +# holds box-drawing bytes, until the agent stands deaf.  Proxmox writes such a
  +# request short; about one time in six the agent's parser then waits for the
  +# rest for good.  The other times the agent drops the NEXT request and
  +# recovers, so one lost ping proves nothing: deaf means three pings in a row
  +# without an answer.  DEAF holds the try that did it, or 0 when none of the
  +# tries did.  The panel's and the heartbeat's timers stand still from here
  +# until qga_probe_heal has run, so that the rig's own refresh does the healing.
  +qga_probe_deafen() {
  +    cat <<'QGAEOF'
  +VMID="$1"; TRIES="${2:-40}"
  +N="${QGA_NOTE:-/run/sigmond/qga-reset}"
  +systemctl stop sigmond-issue.timer pm-heartbeat.timer >/dev/null 2>&1
  +rm -f "$N" "$N.seen"
  +poison=$(printf 'true # \342\224\200\342\224\200\342\224\200 \302\273')
  +deaf=0
  +for i in $(seq 1 "$TRIES"); do
  +    timeout 15 qm guest exec "$VMID" --timeout 5 -- /bin/bash -c "$poison" >/dev/null 2>&1
  +    s=0
  +    for j in 1 2 3; do
  +        sleep 2
  +        timeout 10 qm agent "$VMID" ping >/dev/null 2>&1 || s=$((s+1))
  +    done
  +    if [ "$s" = 3 ]; then deaf=$i; break; fi
  +done
  +echo "DEAF:$deaf"
  +echo END:1
  +QGAEOF
  +}
  +# qga_probe_heal: one panel refresh, then one ping.  HEARS:1 when the agent
  +# answers; NOTE holds the line the reset left; PANELSILENT:1 when the panel
  +# still says that the agent does not answer.  Both timers start again here,
  +# whatever the refresh did.
  +qga_probe_heal() {
  +    cat <<'QGAEOF'
  +VMID="$1"
  +P="${QGA_PANEL:-/usr/local/sbin/sigmond-issue}"
  +I="${QGA_ISSUE:-/etc/issue}"
  +N="${QGA_NOTE:-/run/sigmond/qga-reset}"
  +trap 'systemctl start sigmond-issue.timer pm-heartbeat.timer >/dev/null 2>&1' EXIT
  +SIGMOND_VMID="$VMID" "$P" >/dev/null 2>&1
  +if timeout 10 qm agent "$VMID" ping >/dev/null 2>&1; then echo HEARS:1; else echo HEARS:0; fi
  +if [ -e "$N" ]; then echo "NOTE:$(head -1 "$N")"; else echo "NOTE:none"; fi
  +grep -q 'guest agent does not answer' "$I" 2>/dev/null && echo PANELSILENT:1 || echo PANELSILENT:0
  +echo END:1
  +QGAEOF
  +}
  +# qga_probe_bringup: one panel refresh, then what its bring-up line says, one
  +# token per sentence the panel can print.  UNIT holds the state of the
  +# bring-up unit before and after the refresh, joined by a slash, so that the
  +# rig judges the BUILDING token only when the unit ran through the whole
  +# refresh.
  +qga_probe_bringup() {
  +    cat <<'QGAEOF'
  +VMID="$1"
  +P="${QGA_PANEL:-/usr/local/sbin/sigmond-issue}"
  +I="${QGA_ISSUE:-/etc/issue}"
  +unit(){ timeout 20 qm guest exec "$VMID" --timeout 10 -- /bin/systemctl is-active sigmond-firstrun-bringup 2>/dev/null \
  +          | grep -oE '"out-data" *: *"[a-z]+' | grep -oE '[a-z]+$' | head -1; }
  +u1=$(unit)
  +SIGMOND_VMID="$VMID" "$P" >/dev/null 2>&1
  +u2=$(unit)
  +echo "UNIT:${u1:-none}/${u2:-none}"
  +t(){ grep -q "$2" "$I" 2>/dev/null && echo "$1:1" || echo "$1:0"; }
  +t BUILDING 'STATION IS STILL BUILDING'
  +t NOWEB    'BRING-UP FINISHED BUT ka9q-web IS NOT RUNNING'
  +t NOSDR    'bring-up ran WITHOUT the radio'
  +t RETRY    'BRING-UP FAILED -- it tries again'
  +t GAVEUP   'BRING-UP FAILED THREE TIMES'
  +t STOPPED  'BRING-UP WAS STOPPED BEFORE IT FINISHED'
  +t SILENT   'guest agent does not answer'
  +echo END:1
  +QGAEOF
  +}
  +# qga_fatal <message> <answer>: a guest-agent check failed; shows the answer
  +# of the probe that the verdict rests on.
  +qga_fatal(){ say "FATAL: $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/      /' | tail -14; exit 1; }
  +# <<< qga_rig
   KEY="$HOME/appliance/build/applkey"
   # --release builds strip root-ssh-keys from answer-v3.toml on purpose
   # (build-usb-v3.sh RELEASE branch) so the test key never lands on a
  @@ -955,15 +1074,48 @@
   # lifecycle lock ✓" is trivially true of a bring-up that ran for five seconds
   # and got nowhere -- a pass that means nothing.  So establish COMPLETION
   # first, and make every claim conditional on it.
  +# ── the guest agent, part 1: does this image's panel carry the fix? ─────────
  +# Asked once, before bring-up, because the first check must run WHILE the
  +# station builds.  An image from before v3.71 (the update rig's base) carries
  +# no parser reset; every guest-agent check then says so and judges nothing.
  +QGA_PF=$(qga_probe_panel | $SSHN "bash -s" 2>&1)
  +QGA_PANELFIX=$(rr_tok PANELFIX "$QGA_PF")
  +QGA_BUILD=""; QGA_UNCHECKED=""
  +if [ "${QGA_PANELFIX:-0}" != 1 ]; then
  +    say "⚠ GUEST-AGENT CHECKS NOT EVALUATED — this image's panel predates v3.71"
  +    QGA_UNCHECKED="all of them (this image's panel carries no parser reset)"
  +fi
  +
   say "waiting for first-run bring-up to COMPLETE (its marker), up to ${BRINGUP_WAIT_MIN:-25} min"
   _done=0
   for _i in $(seq 1 $(( ${BRINGUP_WAIT_MIN:-25} * 4 )) ); do
       $SSHN "qm guest exec $VMID --timeout 30 -- /bin/bash -c 'test -e /var/lib/sigmond/.firstrun-bringup-done'" 2>/dev/null \
           | grep -q '"exitcode" : 0' && { _done=1; break; }
  +    # Thirty seconds into the wait, once: one panel refresh while bring-up
  +    # runs, and what the panel then says (judged below the loop).
  +    if [ "$_i" = 2 ] && [ "${QGA_PANELFIX:-0}" = 1 ]; then
  +        QGA_BUILD=$(qga_probe_bringup | $SSHN "bash -s $VMID" 2>&1)
  +    fi
       [ $(( _i % 8 )) -eq 0 ] && say "  … still bringing up ($(( _i / 4 )) min)"
       sleep 15
   done
 
  +# ── the guest agent, part 2: what the panel said while the station built ───
  +# Judged only when the bring-up unit ran through the whole refresh: a unit
  +# that ended meanwhile leaves the panel free to say either thing.
  +if [ "${QGA_PANELFIX:-0}" = 1 ]; then
  +    if nh_ok "$QGA_BUILD" && [ "$(rr_tok UNIT "$QGA_BUILD")" = activating/activating ]; then
  +        [ "$(rr_tok BUILDING "$QGA_BUILD")" = 1 ] \
  +            || qga_fatal "bring-up ran through a whole panel refresh, and the panel did not say STATION IS STILL BUILDING: its status request does not run, or its answer is not read" "$QGA_BUILD"
  +        [ "$(rr_tok NOWEB "$QGA_BUILD")" = 0 ] \
  +            || qga_fatal "the panel raised BRING-UP FINISHED BUT ka9q-web IS NOT RUNNING while bring-up still ran" "$QGA_BUILD"
  +        say "while the station built, the panel said so and raised no alarm ✓"
  +    else
  +        say "⚠ the panel's line DURING bring-up was not evaluated (the unit read '$(rr_tok UNIT "$QGA_BUILD")' around the refresh, not activating/activating)"
  +        QGA_UNCHECKED="${QGA_UNCHECKED:+$QGA_UNCHECKED; }the panel's line during bring-up"
  +    fi
  +fi
  +
   if [ "$_done" != 1 ]; then
       # NOT a FATAL: the nested guest has no RX888 and is slower than iron, so a
       # long bring-up is plausible.  But say plainly that nothing below was
  @@ -1874,6 +2026,79 @@
       $SSHN "systemctl stop rac-standin 2>/dev/null; true"
   fi
 
  +# ── the guest agent, part 3: no request deafens it, one reset heals it ─────
  +# sigmond tasks/plan-guest-agent-requests.md section 5.  Up to v3.70 the
  +# console panel sent the decoder VM a request that Proxmox wrote short, and
  +# about one time in six the guest agent then stood deaf for good.  Three
  +# things must hold on an image that carries the fix:
  +#   1. twelve panel refreshes leave the agent answering every ping;
  +#   2. an agent the rig deafens on purpose hears again after ONE refresh, and
  +#      the panel leaves the line the heartbeat reports from;
  +#   3. after bring-up the panel's line follows the marker's result, and the
  +#      false alarm of v3.70 does not show.
  +if [ "${QGA_PANELFIX:-0}" = 1 ]; then
  +    Q1=$(qga_probe_refresh | $SSHN "bash -s $VMID" 2>&1)
  +    nh_ok "$Q1" || qga_fatal "guest agent, twelve panel refreshes: the nested host gave no answer (not a verdict)" "$Q1"
  +    [ "$(rr_tok SILENT "$Q1")" = 0 ] \
  +        || qga_fatal "after twelve panel refreshes, $(rr_tok SILENT "$Q1") of twelve pings got no answer from the guest agent: a request of the panel still deafens it" "$Q1"
  +    say "twelve panel refreshes, twelve pings answered ✓"
  +    [ "$(rr_tok NOTE "$Q1")" = none ] \
  +        || say "NOTE: a parser reset stands on record ($(rr_tok NOTE "$Q1")). One at boot proves little; one at any other time names a cause"
  +
  +    Q2=$(qga_probe_deafen | $SSHN "bash -s $VMID 30" 2>&1)
  +    QDEAF=$(rr_tok DEAF "$Q2")
  +    # The heal runs whatever the deafening answered: it also starts the two
  +    # timers that the deafening stopped.
  +    Q3=$(qga_probe_heal | $SSHN "bash -s $VMID" 2>&1)
  +    if ! nh_ok "$Q2" || ! nh_ok "$Q3"; then
  +        $SSHN "systemctl start sigmond-issue.timer pm-heartbeat.timer" >/dev/null 2>&1
  +        qga_fatal "guest agent, deafen and heal: the nested host gave no answer (not a verdict)" "$Q2
  +$Q3"
  +    fi
  +    if [ "${QDEAF:-0}" = 0 ]; then
  +        say "⚠ thirty non-ASCII requests left the guest agent hearing: the heal was NOT proven in this run"
  +        QGA_UNCHECKED="${QGA_UNCHECKED:+$QGA_UNCHECKED; }the heal (the rig could not deafen the agent)"
  +    else
  +        [ "$(rr_tok HEARS "$Q3")" = 1 ] \
  +            || qga_fatal "the guest agent stood deaf (after request $QDEAF), and one panel refresh did not bring it back" "$Q3"
  +        case "$(rr_tok NOTE "$Q3")" in
  +            *" panel") ;;
  +            *) qga_fatal "the panel healed the agent and left no line of its own in /run/sigmond/qga-reset (read: '$(rr_tok NOTE "$Q3")'): the host's heartbeat can then report no reset" "$Q3" ;;
  +        esac
  +        [ "$(rr_tok PANELSILENT "$Q3")" = 0 ] \
  +            || qga_fatal "the panel healed the agent and still says that it does not answer" "$Q3"
  +        say "a guest agent deafened on purpose (request $QDEAF) heard again after one panel refresh ✓"
  +    fi
  +
  +    QRES=$($SSHN "qm guest exec $VMID --timeout 30 -- /bin/bash -c 'sed -n s/^result=//p /var/lib/sigmond/.firstrun-bringup-done 2>/dev/null | head -1'" 2>/dev/null \
  +            | grep -o '"out-data" *: *"[a-z-]*' | grep -o '[a-z-]*$' | head -1)
  +    Q4=$(qga_probe_bringup | $SSHN "bash -s $VMID" 2>&1)
  +    nh_ok "$Q4" || qga_fatal "guest agent, the panel's bring-up line: the nested host gave no answer (not a verdict)" "$Q4"
  +    [ "$(rr_tok SILENT "$Q4")" = 0 ] \
  +        || qga_fatal "after the heal, the panel says once more that the guest agent does not answer" "$Q4"
  +    case "${QRES:-none}" in
  +        awaiting-sdr)
  +            [ "$(rr_tok NOSDR "$Q4")" = 1 ] && [ "$(rr_tok NOWEB "$Q4")" = 0 ] && [ "$(rr_tok BUILDING "$Q4")" = 0 ] \
  +                || qga_fatal "bring-up ended awaiting-sdr; the panel must say that it ran without the radio, and must not say BUILDING or raise the ka9q-web alarm" "$Q4"
  +            say "bring-up ended without the radio, and the panel says exactly that ✓" ;;
  +        ok)
  +            [ "$(rr_tok BUILDING "$Q4")" = 0 ] && [ "$(rr_tok NOSDR "$Q4")" = 0 ] \
  +                || qga_fatal "bring-up ended ok; the panel must say neither BUILDING nor that it ran without the radio" "$Q4"
  +            say "bring-up ended ok; the panel's ka9q-web alarm reads $(rr_tok NOWEB "$Q4") ✓" ;;
  +        retry)
  +            [ "$(rr_tok RETRY "$Q4")" = 1 ] && [ "$(rr_tok NOWEB "$Q4")" = 0 ] \
  +                || qga_fatal "bring-up ended retry; the panel must say that it tries again, and must not say that bring-up finished" "$Q4"
  +            say "bring-up ended retry, and the panel says that it tries again ✓" ;;
  +        gave-up)
  +            [ "$(rr_tok GAVEUP "$Q4")" = 1 ] \
  +                || qga_fatal "bring-up ended gave-up; the panel must say that it stopped trying" "$Q4"
  +            say "bring-up gave up, and the panel says so ✓" ;;
  +        *)
  +            say "⚠ the panel's line AFTER bring-up was not evaluated (the marker's result read '${QRES:-none}')"
  +            QGA_UNCHECKED="${QGA_UNCHECKED:+$QGA_UNCHECKED; }the panel's line after bring-up" ;;
  +    esac
  +fi
  +
   # What the site sink checks left unchecked, said once more beside the verdict.
   # The PHASE D PASS line below stays exactly as it was: test-update-v3.sh and
   # bless-release.sh match it.
  @@ -1883,6 +2108,9 @@
   if [ -n "${RAC_UNCHECKED:-}" ]; then
       say "⚠ REMOTE ACCESS CHECKS NOT EVALUATED: $RAC_UNCHECKED"
   fi
  +if [ -n "${QGA_UNCHECKED:-}" ]; then
  +    say "⚠ GUEST-AGENT CHECKS NOT EVALUATED: $QGA_UNCHECKED"
  +fi
 
   say "PHASE D PASS — NESTED TEST COMPLETE"
   ;;
  ````

- [ ] **Step 4: Run the tests and watch them pass.**

  Run: `bash test-qga-rig-lines.sh | tail -1` — expected: `passed 40, failed 0`.
  Run: `bash -n test-nested-v3.sh && echo parses` — expected: `parses`.
  Run: `bash test-remote-rig-lines.sh | tail -1` — expected: `PASS: wizard_answers, rr_state, rr_tok, nh_ok, rr_khkey`.
  Run: `bash test-phase-d-verdict.sh | tail -1` — expected: `PASS: phase_d_verdict`.
  Run: `grep -c 'PHASE D PASS — NESTED TEST COMPLETE' test-nested-v3.sh` — expected: the count it printed before
  your change (check with `git stash; grep -c …; git stash pop`).

- [ ] **Step 5: Repeat the mutations.** Each on a copy: `cp test-nested-v3.sh /tmp/r.sh`, make the one change
  inside the `qga_rig` block, `bash test-qga-rig-lines.sh /tmp/r.sh | grep -m1 FAIL`.

  | Mutation | Must fail |
  |---|---|
  | `poison='true # --- >>'` (ASCII) | `the request holds bytes above 0x7F` |
  | deaf at one unanswered ping (`[ "$s" -ge 1 ]`) | `the third request deafens: DEAF:3` |
  | `qga_probe_refresh` without its `trap` | `the panel's timer stops, and starts again at the end` |
  | `qga_probe_heal` without its `trap` | `both timers start again` |
  | `t NOWEB` greps for `ka9q-web is down` | `the false alarm of v3.70 would show: NOWEB:1` |
  | `qga_probe_refresh` without its ping | `a deaf agent: SILENT:12` |
  | `qga_probe_deafen` without `rm -f "$N" "$N.seen"` | `an earlier reset's line is cleared first` |
  | `echo "UNIT:${u1:-none}/${u1:-none}"` | `a unit that ends during the refresh shows both` |

- [ ] **Step 6: Read the three call sites once more against the rig around them**, since nothing ran them. Check
  for each: that `rr_tok`, `nh_ok` and `say` stand defined at that point; that `$VMID` and `$SSHN` hold their
  values there; that a FATAL after `qga_probe_deafen` cannot leave the two timers stopped (the heal starts them,
  and the line after it starts them again when the nested host gave no answer). Write what you checked into your
  report.

- [ ] **Step 7: Commit.**

  ```bash
  cd /home/mjh/hamsci/repos/sigmond-appliance
  git add test-nested-v3.sh test-qga-rig-lines.sh
  git commit -m "rigs: the nested rig deafens the guest agent on purpose and requires one panel refresh to heal it" \
             -m "Phase D gains three guest-agent checks for an image whose panel carries the parser reset: twelve panel refreshes leave every ping answered; an agent the rig deafens with the request v3.70's panel sent hears again after one refresh, and the panel leaves the line the heartbeat reports from; and the panel's bring-up line follows the marker's result, during bring-up and after it.  An image from before v3.71 gets a NOT EVALUATED line and no verdict.  test-qga-rig-lines.sh runs the five probes against stand-ins.  The rig's own lines that call them have not run yet." \
             -m "Design: sigmond tasks/plan-guest-agent-requests.md section 5."
  ```

---

### Task 5: the words

**Files (sigmond):**
- Modify: `docs/operator/troubleshooting.md` (one new section; the `Verified against` line)
- Modify: `CLAUDE.md` (one rule under "Key constraints")
- Modify: `tasks/plan-sink-control-v370.md` (the carry list's guest-agent item: a pointer)
- Modify: `tasks/plan-guest-agent-requests.md` (its Status line)
- Create: `tasks/upstream-proxmox-qga-truncation.md` (the draft report)

**Files (sigmond-appliance):**
- Modify: `INSTALL.md`, `QUICKSTART.txt`, `docs/install-page.html` (three sentences; two new rows)
- Modify: `test-instructions-remote.sh` (one guard tightened; the new rows pinned to the panel's text)

**Interfaces:**
- Consumes: the panel's sentences as Task 1 committed them. Read them out of `firstboot-v3.sh`; never retype them
  from this plan.
- Produces: nothing a later task reads.

**The rule for every sentence here: the code wins.** Before you print a line that the software prints, find it in
the code and compare character for character. Before you state what the software does, find the code that does
it. The remote-access documents needed three rounds because sentences promised more than the code did.

- [ ] **Step 1: The operator's page.** In `docs/operator/troubleshooting.md`, add a section after
  "### The VM did not start after a reboot", with the page's four beats (likely causes, what to check, what to
  do, when to ask). Title: `### The console panel shows a line about bring-up or the guest agent`. It must cover
  these six lines of the panel, each quoted exactly as `firstboot-v3.sh` prints its first line:
  1. `STATION IS STILL BUILDING` — normal on a first boot; tens of minutes; the web links answer at the end.
  2. `bring-up ran WITHOUT the radio` — no SDR stood on the USB bus; plug it in; bring-up finishes by itself.
  3. `BRING-UP FAILED -- it tries again` — it tries again when the decoder VM next boots; the log names the
     command that runs it sooner.
  4. `BRING-UP FAILED THREE TIMES` — it has stopped trying; log in to the VM (`sigmond-vm` on the host, or
     `ssh -p 2222 sigmond@<host address>`), read the last 30 lines of `/var/log/sigmond/firstrun-bringup.log`,
     and send them to your fleet admin. The log names two commands; say that the second one matters (CHECK the
     script: `sigmond/bin/sigmond-firstrun-bringup`, the `gave-up` branch).
  5. `BRING-UP FINISHED BUT ka9q-web IS NOT RUNNING` — bring-up ended well and the web page's service does not
     run.
  6. `guest agent does not answer` — the host cannot ask the VM anything. The radio and the uploads do not depend
     on it. The panel tries one reset at each refresh, every five minutes. If the line stays for more than ten
     minutes while the VM runs, tell your fleet admin. Never tell the reader to run `smd` through
     `qm guest exec`.
  Move the page's `Verified against` line to the commit you write from, in the form the line already uses.

- [ ] **Step 2: The three parked sentences** (the remote-access plan's final re-review found them; its ledger
  parked them here). In sigmond-appliance:
  - `INSTALL.md` (the row that begins `| The panel prints`), `QUICKSTART.txt` (the entry that begins
    `The panel says  ssh root@<host-ip>`) and `docs/install-page.html` (the row that begins
    `<tr><td>The screen says`): each says the two "have not paired". The panel also prints that line when the
    host's last check of an existing pairing failed (CHECK: `sigmond-wizard.sh`, `rac_pair_vm` calls
    `rac_pair_record` with `not-paired` for every run that fails, including a re-check). Make each say: the two
    have not paired, or the host's last check of the pairing failed.
  - `INSTALL.md` §10 says "The host tries the pairing again after every boot." CHECK `rac_pair_locked`: the
    re-assertion units get armed only after the host files, the /30 check and the sshd key check have passed.
    Make the sentence say when the host tries again, and no more than the code does.
  - `test-instructions-remote.sh`, the guard on `sigmond-setup --rac-o`: it accepts a bare command anywhere
    within 400 characters after the words "login on the host". Cut the context at the last sentence end before
    the command, so that the sentence that names the command must itself give the way to type it. Show one
    mutation that turns it red: a bare `Run sigmond-setup --rac-off on the Proxmox machine.` placed right after
    §8's keyboard paragraph of `INSTALL.md` (on a copy).

- [ ] **Step 3: Two rows for the installer.** Each of the three appliance documents holds a list of things that
  look wrong and are not. Add two entries to each, quoting the panel's first line exactly:
  `bring-up ran WITHOUT the radio` (plug the SDR in; nothing else to do) and `guest agent does not answer` (the
  radio and the uploads go on; the panel tries a reset every five minutes; tell your fleet admin if it stays).
  Then pin them: in `test-instructions-remote.sh`, read both sentences out of `firstboot-v3.sh` (cut the text
  between `BRINGUP="` and the line's end) and require each document to hold them. Show that a changed word in
  the panel turns the test red (on a copy).

- [ ] **Step 4: The rule, where the next session reads it.** In sigmond's `CLAUDE.md`, under "## Key constraints",
  add one bullet:

  ```markdown
  - **No byte above 0x7F in a guest-agent request.** Proxmox writes a `qm guest exec` request that holds
    non-ASCII characters short, and about one time in six the guest agent then stands deaf: it keeps its
    process, reads every request and answers none. In `sigmond-wizard.sh`, send through `gexec` or `guest_out`
    (both base64-encode). Anywhere else, keep the request's text fixed and ASCII, or encode it. A host that
    finds the agent silent sends one 0xFF byte down `/var/run/qemu-server/<vmid>.qga` before it concludes
    anything. Design: `tasks/plan-guest-agent-requests.md`.
  ```

- [ ] **Step 5: The carry list and the design's status.** In `tasks/plan-sink-control-v370.md`, at the item that
  begins `- **No guest-agent request may carry a non-ASCII character**`, add one line at its end: where the four
  pieces of work landed (this plan's file name and the commits of Tasks 1 to 4). In
  `tasks/plan-guest-agent-requests.md`, set the Status line to what stands committed and what still waits (the
  rig run; the hardware; the report to Proxmox).

- [ ] **Step 6: The draft report for Proxmox.** Create `tasks/upstream-proxmox-qga-truncation.md` with this
  content. It stays a draft: Michael decides whether to send it, and where.

  ````markdown
  # Draft report for Proxmox: `qm guest exec` writes a non-ASCII request short

  **Status:** draft, not sent. Michael Hauan (AC0G) decides whether and where to file it.
  **Observed on:** Proxmox VE 9.1.1, qemu-server 9.0.30, perl 5.40.1; guest Debian 13 with qemu-guest-agent.
  **Measured:** 2026-10-08, on a nested install; seen in the field on three stations from 2026-09-27.

  ## What happens

  A `qm guest exec` whose arguments hold non-ASCII characters reaches the guest agent cut short. The request
  that arrives holds as many BYTES as the request has CHARACTERS, so its last bytes never arrive: one byte for
  each extra byte that UTF-8 needs.

  Read back from the guest's memory, one such request ended at `"capture-output":`. The seven bytes `true}}`
  and the newline never arrived, and the command's argument held seven extra UTF-8 bytes.

  ## What follows

  - Most of the time the cut lands inside a JSON string. The agent then fails the NEXT request, from any caller,
    and recovers.
  - About one time in six the cut lands between two tokens. The agent's JSON parser then waits for the rest of
    the object. Every later request arrives whole and balanced, so the parser queues it and dispatches nothing.
    The agent keeps its process and its PID, uses no CPU, and answers nothing. `qm agent <vmid> ping` reports
    "QEMU guest agent is not running" while `systemctl is-active qemu-guest-agent` inside the guest reads
    `active`. Only a restart of the agent, a reboot of the guest, or a parser reset from the host ends it.

  The order of JSON keys varies from one `qm` process to the next, which decides where the cut lands.

  ## How to reproduce

  ```
  qm guest exec <vmid> -- /bin/echo ok                # answers
  qm guest exec <vmid> -- /bin/echo '───'             # repeat up to about ten times
  qm agent <vmid> ping                                # "QEMU guest agent is not running"
  ```

  In one trial, fourteen such requests in a row ran zero times: twelve cost the next request, and two left the
  agent deaf. Six requests with ASCII arguments, sent the same way, all ran.

  ## What ends it without touching the guest

  The QGA protocol defines the byte 0xFF as a reset of the agent's parser. Proxmox does not send it. One such
  byte, followed by a `guest-sync-delimited` request, written to `/var/run/qemu-server/<vmid>.qga`, brought a
  deaf agent back at once, with the same PID:

  ```python
  import socket
  s = socket.socket(socket.AF_UNIX); s.settimeout(4)
  s.connect("/var/run/qemu-server/100.qga")
  s.sendall(b"\xff" + b'{"execute":"guest-sync-delimited","arguments":{"id":4242}}\n')
  print(s.recv(4096))
  ```

  ## What we ask

  1. Write the request's full length in bytes.
  2. Send 0xFF ahead of `guest-sync-delimited`, as the protocol's documentation suggests, so that one malformed
     request can never leave an agent deaf.

  ## What we did not establish

  We did not read the code that writes the request, so the sentence "counts characters, writes bytes" describes
  what we measured, not a line we can point to. We did not test other versions.
  ````

  CHECK each number in it against `tasks/plan-guest-agent-requests.md` and the v3.70 plan's carry list before you
  commit; where they differ, the plans win, and say so in your report.

- [ ] **Step 7: Run the documents' tests.**

  sigmond: `.venv/bin/pytest tests/test_docs_remote_access.py -q 2>&1 | tail -1`;
  `python3 scripts/docs-linkcheck.py --workspace /home/mjh/hamsci/repos docs` (0 broken links);
  `scripts/docs-conformance.py .` (0 findings).
  sigmond-appliance: `bash test-instructions-remote.sh | tail -1` and `bash test-bless-instructions.sh | tail -1`
  (the second pins `INSTALL.md`'s shape for the bless).

- [ ] **Step 8: Commit**, one commit in each repository, by path, each message naming what the documents now say
  and what they said before.

---

## Release: what Task 12 of the remote-access plan gains

This plan ships inside v3.71, with remote access. Its release steps therefore live in
`tasks/plan-remote-access-v371.md`, Task 12. Three of them gain a line when this plan has landed:

- **Step 6, the nested test**, must also print three lines that end in `✓`:
  `twelve panel refreshes, twelve pings answered`; `a guest agent deafened on purpose (request N) heard again
  after one panel refresh`; `bring-up ended without the radio, and the panel says exactly that`. It may print
  `while the station built, the panel said so and raised no alarm ✓`, or say that the line during bring-up went
  unevaluated. It must not print `GUEST-AGENT CHECKS NOT EVALUATED — this image's panel predates v3.71`.
- **Step 8, the B4 reflash**: within ten minutes of the first boot, `qm agent 100 ping` on the host answers, and
  goes on answering for an hour (a v3.69 panel deafened the agent within about half an hour). The fleet board's
  row for B4's host shows no `guest-agent-reset` finding after the first beats.
- **Step 10, AC0G-ND in place**: ND's agent probably stands deaf when the alignment starts. After
  `pm-align --apply`, nobody touches the agent. Within five minutes `qm agent 100 ping` must answer, and
  `/run/sigmond/qga-reset` must hold a line that ends ` panel`. The next heartbeat then carries one
  `guest-agent-reset` finding and reads INCONCLUSIVE in its doctor block, and the beats after it carry none.
  That is this plan's proof in the field.

## What this plan could not verify

1. **Whether one 0xFF byte heals an agent under this panel on a real host.** It did so by hand on the rig
   (2026-10-08) and on AC0G-ND (2026-10-09 13:13Z). The panel's own reset has only met a stand-in socket. Task 4's
   second check settles it.
2. **What a reset does while another request holds the agent's socket.** Both proven resets ran with the socket
   free. A reset that waits behind another client gives up after four seconds (tested); whether it can harm that
   client's request, nothing here shows.
3. **A reset during a boot.** A ping that fails because the agent has not started yet can meet an agent that
   starts within the reset's four seconds. The record then names a reset that healed nothing, and the fleet board
   shows one INCONCLUSIVE beat after that boot. The rig prints the record's line as a NOTE for this reason.
4. **The rig's own lines** (Task 4).
5. **What the agent's output looks like for the status request on a real host.** The panel reads `out-data` and
   the VM strips its answer to printable ASCII, so double encoding should not matter; the stub cannot prove it.
6. **That `sigmond-vm` exists on every host the panel reaches.** The wizard writes it; a host that never ran the
   v3.4x wizard may lack it. The panel's other lines already name it.
