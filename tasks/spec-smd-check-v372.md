# One command that says whether a station works (v3.72 #14) — design

**Status:** approved by Michael 2026-10-14; marks amended to ASCII words the same day (§2).  Written against sigmond `3d8a57e`.
Michael chose on 2026-10-14 that the installer types the check on the host, at the console
(`sigmond-check`), and that the decoder VM gains `smd check` underneath it.

## 1. Why

An installer stands at a Beelink with a keyboard and a monitor and no window manager.  The
install guide's §9 asks them to open three web pages from another computer, run two watchers
that never stop by themselves, and grep a config file.  A survey of AC0G-B4 on 2026-10-14 found
no single command that says "this station works", and two that mislead:

- `smd status` shows each service as running, but says nothing about spots or files.  On a
  healthy station it prints six red timing-judge lines and exits 1.
- `smd doctor` reports checkout bookkeeping, not science.

Checking each item by hand takes five or six commands, two Ctrl-Cs and the reading of file
times.  Michael asked for something simple enough that an installer reads it to the end.

## 2. What the installer sees

At the console the keyboard reaches a login on the host (install guide §8).  The installer logs
in and types one command:

```
sigmond-check
```

It prints one line per item and a verdict, and finishes within about fifteen seconds:

```
 ok    PSK            214 FT8 and 31 FT4 spots in the last 15 min
 ok    WSPR           48 spots in the last 10 min, on 6 bands
 FAIL  magnetometer   frozen: 0.00 C, no change in 60 s.
                      Fix: sigmond-vm sudo systemctl restart mag-recorder
 wait  GRAPE          first files due in about 7 min (hf-timestd started 4 min ago)
 ok    storage        /var/lib has 61 % free
 wait  uploads        off, new station: check the lines above, then run  sigmond-vm smd sink upload
 --    meteor scatter not on this station
 ok    receiver page  IPv4 http://192.168.1.50:8081   IPv6 http://[2601:db8::50]:8081
 ok    station pages  IPv4 http://192.168.1.50:8000   IPv6 http://[2601:db8::50]:8000
 ok    magnetometer page  IPv4 http://192.168.1.50:8082   IPv6 http://[2601:db8::50]:8082
FAIL  (1 failing, 2 waiting)
```

The marks:

| Mark | Meaning |
|---|---|
| `ok` | works |
| `wait` | not yet, and normal for a new station; the line says how long to wait |
| `FAIL` | broken; the line says what to do, or whom to tell |
| `--` | that part does not run on this station, and nothing counts it |

Michael chose plain words on 2026-10-14, after a measurement: the host's console font (Lat15) has
no glyph for a check mark or a dotted circle.  Both programs print ASCII only, everywhere, so the
installer reads every character on that console.

The verdict takes the worst line: any `FAIL` line gives `FAIL`, else any `wait` line gives `WAIT`, else `PASS`.
The exit code follows it: 0 for PASS, 2 for WAIT, 1 for FAIL.  The page lines print the very
addresses a person on the station's network types into a browser, so the check doubles as the
list of links.

## 3. Two programs

**`smd check`** runs in the decoder VM.  It needs no root and no network beyond the VM itself,
and works alone from any VM login: `smd check`, or from the host `sigmond-vm smd check`.  It
checks the science items (§4) and the VM's own web pages at `127.0.0.1`.

**`sigmond-check`** runs on the host.  It calls `sigmond-vm smd check --no-verdict`, prints those
lines, adds the page lines through the host's addresses (§5), and prints the one verdict from
both sets.  If the VM does not answer, it prints `FAIL decoder VM  does not answer: <reason>` and
gives `FAIL`.

Neither program changes anything.  Neither calls the timing judge, `smd doctor`, or a watcher.

## 4. The VM's lines

Each line reads data that already exists.  A client appears only when `compute_state(name)`
(`lib/sigmond/component_state.py`) reports it installed; otherwise the line shows `--`.

| Line | Reads | ok when | wait when | FAIL when |
|---|---|---|---|---|
| PSK | `/var/log/psk-recorder/*-ft8.log`, `*-ft4.log` (the files `smd watch psk` reads) | at least one spot in the last 15 min | the service started under 15 min ago | the service has stopped or failed, or no spot in 15 min after that |
| WSPR | the wspr-recorder journal, last 10 min (the source `smd watch wspr` replays) | at least one spot | the service started under 12 min ago | the service has stopped or failed, or no spot after 12 min |
| magnetometer | today's `/var/lib/mag-recorder/samples-<date>.jsonl`, its last 60 lines | the newest sample under 10 s old, and the readings change | the service started under 1 min ago | no sample for 10 s; or frozen (§4.1); or the service enabled but its device `/dev/ttyMAG0` absent (v3.72 #17) |
| meteor scatter | the meteor-scatter journal, its `stats MSK144` lines, last 5 min | a stats line in the last 5 min | the service started under 5 min ago | stopped or failed, or no stats line |
| GRAPE | `/var/lib/timestd/raw_buffer/<channel>/<UTC date>/`, the channel list from the hf-timestd config | every configured channel holds a file under 11 min old | hf-timestd started under 12 min ago | a channel has no fresh file after that; the line names it |
| storage | free space on the filesystem holding `/var/lib` | 10 % free or more | — | under 10 % free |
| uploads | the site sink switch (`coordination.toml`), and `hs_uploader.backlog.summarize` on `watermarks.db` | switch `upload`, a delivery acknowledged in the last 30 min, and no dead letter in the last 24 h | switch `off` with the reason `new station: …`: the line tells the installer to run `smd sink upload` once the lines above pass | switch `off` or `hold` for any other reason (ask the fleet admin); dead letters in the last 24 h; or the backlog unreadable |
| receiver, station, magnetometer pages (VM view) | HTTP GET on `127.0.0.1:8081`, `:8000`, `:8082`, 3 s each | status 200 | — | anything else |

A WSPR or meteor-scatter quiet spell on a dead band can look like a fault.  The windows above
match the ones `smd watch` already treats as normal, and a `FAIL` line for "no spots" says
"check the antenna" before it says anything else.

### 4.1 The magnetometer freeze

A USB re-enumeration leaves mag-recorder writing a sample every second with `rt` 0.00 and
unchanged axes (memory: `reference_mag_usb_reenumeration_freeze`).  The samples look fresh, so
no current check sees it.  `smd check` calls the magnetometer frozen when every one of the last
60 samples carries `rt == 0.0`, or when x, y and z each hold one value across all 60.  The line
gives the fix: `sudo systemctl restart mag-recorder`.

### 4.2 Reading without root

The `sigmond` login reads these files today, as the watchers do.  Where a file or the journal
refuses it, `smd check` tries `sudo -n` once, the way `_file_size` in `bin/smd` already does.  If
that fails too, the line shows `FAIL ... cannot read <path> as <user>`, never a pass.

## 5. The host's page lines

The host tests the addresses people use.  It finds its own global IPv4 and IPv6 addresses on
`vmbr0` and checks ports 8081, 8000 and 8082.

**IPv4 cannot use a plain HTTP GET.**  The host relays these ports with DNAT rules in
`PREROUTING`, and traffic the host sends to itself never passes through `PREROUTING`.  Measured
on B4's host 2026-10-14: `curl http://192.168.1.244:8081/` returned nothing, while the VM's own
page answered 200 at `10.99.0.2:8081` and the LAN reached it.  So the IPv4 line passes when the
DNAT rule for the port exists, `net.ipv4.ip_forward` reads 1, and a GET on the VM's address
returns 200.  Those three things together carry a LAN browser's request.

**IPv6 can use a plain GET.**  `sigmond-vm6proxy` relays IPv6 in userspace, so a GET on the host's
own global IPv6 address goes through the relay a browser would use.

| Host has | Line shows |
|---|---|
| IPv4 only | the IPv4 link; IPv6 reads `no IPv6 here` and counts as nothing |
| IPv6 and the relay | both links, each checked |
| IPv6 and no relay | `FAIL IPv6 http://[...]:8081 does not answer: the IPv6 relay is not installed` (v3.72 #15 cures this on dual-stack sites) |

The magnetometer page line appears only when the VM reports mag-recorder installed.

## 6. Where the code lives

- `lib/sigmond/commands/check.py`: the checks of §4, one small function per line, each
  returning a mark and a sentence.  `bin/smd` registers `check` with `--no-verdict` and `--json`,
  and never calls `_need_root`.  `--json` serves the tests and later tools; `sigmond-check`
  does not need it.
- `scripts/proxmox/sigmond-wizard.sh`: a new heredoc writes `/usr/local/bin/sigmond-check` beside
  `sigmond-vm`.  `scripts/proxmox/pm-align.py` gains it in a `WIZARD_HEREDOCS_SINCE_V372` set,
  so stations installed earlier receive it from `pm-align`, as they received `sigmond-vm`.
- `sigmond-appliance/INSTALL.md` §9: rewritten (§7).
- The console panel's help line (`From the host:  sigmond-vm …`) names `sigmond-check` too.

## 7. The install guide's §9

§9 becomes short.  Its title stays "Fifteen minutes later — check it's alive", and its body says:

1. At the station's keyboard press Enter and log in to the host (§8 shows how).  Type
   `sigmond-check`.
2. A table of the four marks, as in §2.
3. `WAIT`: run it again after the time the line names.  `FAIL`: do what the `FAIL` line says; if it
   names no fix, send the whole output to the fleet admin.
4. When every line except uploads shows `ok`, and the uploads line says `new station`, run
   `sigmond-vm smd sink upload`.  The existing paragraphs about the packing window and about
   wsprnet and pskreporter stay, shortened.
5. Optional, from another computer on the same network: open the page links that
   `sigmond-check` printed.  Two examples, one per address family:
   `http://192.168.1.50:8081` and `http://[2601:db8::50]:8081`.  An IPv6 address takes square
   brackets in a browser.

From another computer, `ssh -p 2222 sigmond@<host address> smd check` gives the VM's lines
without the host's.  §11's row "No spots after 30 minutes" points to `sigmond-check` in place of
`smd status`.

## 8. Testing

- Each line's function gets tests for `ok`, `wait`, `FAIL` and `--`, with the files built in a temporary
  directory and the clock injected.  Paths come in as parameters with the real paths as
  defaults.
- The freeze test: 60 samples with `rt` 0.0 give FAIL; 60 with real `rt` and varying axes give ok;
  59 frozen then one live gives ok.
- The verdict and exit code over mixed lines; a line that raises an exception becomes `FAIL`
  with the exception's text, never a crash and never a pass.
- `smd check` never calls the timing judge: a test patches `timing_judge` to raise and expects
  the command to finish.
- `sigmond-check`: a shell test with stub `sigmond-vm`, `ip`, `iptables`, `sysctl` and `curl`,
  covering IPv4 only, dual stack with the relay, dual stack without it, and the VM silent.
- Mutation: break each rule's threshold and watch a test fail.
- On AC0G-B4 after deploy: `sigmond-check` on the host and `smd check` in the VM, each within
  15 s; then stop mag-recorder and watch its line turn `FAIL`, and start it again.

## 9. Out of scope

- The timing judge's place in `smd status` and the silent `smd watch uploads` (v3.72 #16).
- The IPv6 relay on dual-stack sites (v3.72 #15).  This check reports its absence and no more.
- GRAPE's daily product and its PSWS upload, which arrive only the next UTC day.  The uploads
  line covers what an installer can see on the day.
