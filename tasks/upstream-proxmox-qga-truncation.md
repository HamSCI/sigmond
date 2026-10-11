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
