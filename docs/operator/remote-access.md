# Remote access (RAC) — letting the fleet admin reach your station

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond ddaefd6 on 2026-10-10 — the switch script, registration and pairing in `scripts/proxmox/sigmond-wizard.sh`, `pm-align.py`, `lib/sigmond/commands/remote.py`, the console panel and the split console in sigmond-appliance `firstboot-v3.sh`, and their tests. A hardware install has not yet confirmed it.
> **Canonical for:** remote access (RAC) — what it is, what it exposes, how the operator switches it, how the admin connects

Every Sigmond station registers for remote access when it installs, and its
tunnel comes up once a gateway answers. The setup wizard asks nothing about it.
A tunnel gives the only effective means of supporting a station, so every
install carries one.

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

A station installed before v3.71 needs its host brought to v3.71 before
`smd remote` works there, and a station with the old layout cannot use it. The
old layout puts the VM on your own network, with no private link to its host.
§8 says which applies.

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
one connection open to the gateway (source: `/etc/sigmond/frpc-host.toml` on the
`[host]`). Your station tries gateways in order, best first. Each stop is a
tier, and `smd remote status` names the one you got. The console panel and the
install summary show it as well while the tunnel is up. On the wsprdaemon gateway's secure tier,
`wsprdaemon (secure)`, TLS protects the connection, and the host checks the
gateway against a certificate authority pinned into the image at
`/etc/sigmond/frps-ca.crt`. A DASI station's `HamSCI secure (TOFU)` tier
encrypts without that check. The unsecured tiers (`HamSCI unsecured (direct)`
and `wsprdaemon (UNSECURED)`) carry no TLS.

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

The tunnel carries **six channels** and nothing else on your station. This is
the complete list.

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

Not the public internet. The gateway keeps these ports private by default. The
admin reaches them over the gateway's private VPN. The one port open to the
internet is the one your station dials *out* to. An administrator of the gateway
may publish a VM channel on purpose. The two host channels stay private.

What a visitor meets at each channel differs:

- **The command lines still need a key.** Getting through the gateway only puts
  someone at an ssh login prompt. Logging in needs an ssh key already installed
  on your station, or the password you set. The Proxmox page asks for that
  password too.
- **The three web pages ask for no login.** Anyone the gateway admits can open
  the live receiver, the station pages and the magnetometer dashboard.

So "who can reach my station" has two answers. For the command lines and the
Proxmox page: **the people who get through the gateway and hold a key or the
password.** For the three web pages: **anyone who gets through the gateway.** In
practice today, the people the gateway admits are the person who gave you the
image, plus the volunteers who operate the fleet gateway. There is no
self-service portal and no public listing.

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

**What registering sends.** Two things: the station's name (your reporter ID in
upper case, with `/` written as `_`) and the host's public ssh key. A DASI
station also sends its DASI unit name, for example `DASI-003`. The gateway
answers with an account, a token, a RAC number and ports, and the host writes
them to `/etc/sigmond/frpc-host.toml`. Where a registrar hands out the account,
it still answers over plain HTTP today, so the token crosses the internet
unencrypted once. The tunnel itself uses TLS wherever the gateway offers it (§1).

**An install with no internet still finishes.** So does one that finds the
gateway down. The station then wants remote access on and has not registered.
The host tries again every 10 minutes, and brings the tunnel up once a gateway
answers. If you turn the switch off in the meantime, the host still registers
when it can, and stops there.

The wizard's closing summary carries a `RAC:` line with the state, and a
`Switch:` line that says whether the VM can work the switch. The summary stays
at `/root/sigmond-setup-summary.txt` on the `[host]`.

---

## 4. The switch: off, on, status

Everything in this section runs in the decoder `[VM]`. From another computer on
your network, log in with `ssh -p 2222 sigmond@<host address>` (on a station with
the old layout: `ssh sigmond@<VM address>`). The console panel prints that command
with your station's address, and lists the logins below it.

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

Only the third describes a fault. §7 covers it. The second line of the answer
changes with the case. A station that never registered says so on the second
line of `off`. If the tunnel service still runs after `off`, the answer adds a
NOTE line to say so, and `smd remote off` again stops it.

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

Off closes the tunnel even on a host that can write nothing, such as one whose
disk has filled. The host still stops and disables the tunnel and its relays.
Its answer then ends with lines that begin `sigmond-rac-switch:`, and one of
them says that it `could not record the wish 'off'`. Send those lines to your
fleet admin. Until the host can record the wish, a rerun of the setup wizard
could turn remote access on again.

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

- The **console panel** on the station's monitor starts its remote-access block
  with one of these lines:

  ```text
  Remote access   ON — RAC 507 on gw2.wsprdaemon.org  (wsprdaemon (secure))
  Remote access   OFF, by the operator's choice (since 2026-10-12T03:10:44Z) — not a fault
  Remote access   off — this station has not registered for it
  Remote access   WANTED ON, NOT REGISTERED YET — no gateway has answered
  Remote access   WANTED ON, BUT NOT UP — RAC 507 on gw2.wsprdaemon.org: 0/6 channels accepted
  Remote access   WANTED ON, BUT NOT UP — the tunnel service does not run (RAC 507)
  Remote access   STATE NOT READ — the switch gave no fresh answer (last: none)
  ```

  - `ON` lists the channels below it. The two host channels stand under the
    heading `ADMINISTRATORS ONLY`. The VM channels stand under `HamSCI users`.
  - `OFF` says since when, and says "not a fault". Below it, the panel gives
    the whole way to turn it on. On a station whose two machines have paired
    (§8), that line names the VM's command:

    ```text
    ==> to turn it on, log in to the decoder VM (ssh -p 2222 sigmond@<host address>) and type:  smd remote on
    ```

  - On a station that has not paired, the panel names the host's own switch
    in place of `smd remote`, as a whole command for another computer:

    ```text
    ==> to turn it on, type on another computer:  ssh root@<host address> 'sigmond-setup --rac-on'
    to turn it off, type on another computer:  ssh root@<host address> 'sigmond-setup --rac-off'
    ```

    The host records the outcome of each pairing, and the panel reads that
    record at every refresh. Only a recorded success earns the VM's command.
  - If the tunnel service still runs while the switch says off, the first line
    drops "not a fault". A further line reads "NOTE: the tunnel service still
    runs, so this station can still be reached." Below it, the panel names the
    way to close it: `smd remote off` once more.
  - `off — this station has not registered for it` means that its owner
    declined remote access before v3.71, or that the station never reached a
    gateway. §8 explains. `smd remote on` tries to register the station; if no
    gateway answers, the host tries again every 10 minutes (§3).
  - Each of the three `WANTED ON` lines gives its next step below it. Under
    `NOT REGISTERED YET` the panel says that the host tries again every 10
    minutes. Its last line begins "To keep the tunnel down once the station
    registers," and gives the command that turns remote access off.
  - `STATE NOT READ` means the switch gave no fresh answer. The line below it
    gives the command that asks the switch directly. In the VM,
    `smd remote status` asks the same question. If the panel still says
    `STATE NOT READ` five minutes later, send that answer to your fleet admin.
  - A host not yet brought to v3.71 (§8) prints the older panel text. That text
    knows two states and calls a deliberate off `OFFLINE`.
- The **login banner** of the VM prints the state as the host last reported it,
  with the time of that report in UTC. It never asks the host, so a login stays
  fast. When the report is more than 7 hours old, the banner says how old it is
  and tells you to ask again with `smd remote status`. Until the VM holds any
  report, it says `state not read yet`. If the host reported off while its
  tunnel service still ran, the banner reads `OFF, but the tunnel service still
  runs` and names `smd remote off` as the way to close it.
- The **fleet board** tags a station whose host reports remote access off with
  "remote access off by the operator". A station that never registered carries
  "remote access off: never registered" instead. When a station has gone silent,
  its tag reads "remote access was off by the operator at its last heartbeat".
  A silent station that never registered reads "remote access was off (never
  registered) at its last heartbeat".
  Your admin sees a decision, not a station to chase. The board hears from your
  station only when it sends status heartbeats; the wizard asks about them.

**How the VM asks.** The host has no keyboard of its own once the install
ends: the USB ports belong to the decoder VM, so a keyboard plugged into the
station types into the decoder VM. You know the VM as well. So the VM asks and
the host alone acts. At install the wizard
*pairs* the two machines: the VM gets one ssh key, kept in a file only `root`
can read, and the host gets an account for it. On the host that key can run
exactly one program, with one of three words: `on`, `off` or `status`. It opens
no shell, forwards no port and runs nothing else. The VM cannot create this
pairing; only the host can. Someone who breaks into the VM as `root` can
therefore work the switch, and gains nothing more by it: turning the tunnel on
opens the station only to the people the gateway admits (§2 says what each
channel then asks of them). §8 says how the host keeps the pairing in step with
its own ssh key.

`smd remote` raises its own privileges, because the pairing key belongs to
`root`. Type it as shown, with no `sudo` in front.

**From the host.** `sigmond-setup --rac-off` and `sigmond-setup --rac-on` on the
`[host]` run the same switch and do the same thing. Two ways lead to a `root`
login on the `[host]`:

- **From another computer:** `ssh root@<host address>`. The console panel
  prints that command. Beside the login `root` it says which password to use:
  `the password you set at install`, or
  `hamsci-sigmond   <-- image default, CHANGE IT`.
- **At the station, with a keyboard and the monitor.** On a station with the
  private link, installed at v3.66 or later, the panel's first line reads
  `KEYBOARD WORKS HERE — press Enter for a login to THIS host`. Press Enter.
  The screen asks for the host's `root` password; type it and press Enter.
  Nothing shows while you type the password. The decoder VM carries your typing
  to the host over the private link, and the host shows the login on its
  monitor. There you can type `sigmond-setup --rac-off`,
  `sigmond-setup --rac-on` and `sigmond-setup --pair-vm`.

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
- **Nothing about it is public unless a gateway administrator publishes it.**
  The gateway keeps the channel ports private. An administrator can publish a VM
  channel on purpose; a host channel stays private (§2). The station is not
  listed anywhere a stranger can browse.
- **It is not needed to produce or upload data.** Every upload path works with
  it off.
- **It is yours to end.** `smd remote off` stops it now, and it stays off until
  you type `smd remote on`.

Some owners will not grant standing access, yet will open the door when trouble
comes. The switch exists for exactly that: keep it off, and turn it on for the
hour your admin needs.

If you keep it off and your station sends status heartbeats, your admin sees
that on the fleet board. You then need not tell anyone. Do not delete files under
`/etc/sigmond/` by hand to achieve it (→ [do-not-touch.md](do-not-touch.md)).

---

## 7. If remote access does not come up

`smd remote status` says `wanted on, not up`, and the console panel shows one of
its three `WANTED ON` lines (§4). **This costs you nothing but support access.**
The radio, the recorders and every upload path are unaffected. Every `smd remote`
command below runs in the `[VM]`.

Read the second line of the answer; it begins one of three ways.

- **"The station has not registered yet: no gateway has answered."** The
  install found no internet, or the gateway was down. Do nothing: the host tries
  again every 10 minutes and brings the tunnel up when a gateway answers. To
  try now, type `smd remote on`.
- **"The tunnel service runs, and the gateway has accepted none of its 6
  channels"** — the line goes on with the RAC number and the gateway in
  parentheses. The station cannot reach its gateway at the moment. The service
  keeps trying every 30 seconds. After a broadband outage or a DNS hiccup, the
  channels return once the gateway answers again.
- **"The tunnel service does not run"** — the line goes on the same way.
  Something stopped it outside the switch, or it failed to start. Type
  `smd remote on`. If the state stays the same, send your fleet admin the output
  of this, run on the `[host]`:

  ```bash
  journalctl -u sigmond-rac-host.service -n 50
  ```

`smd remote` itself can fail to reach the host. It then says why. Four messages
cover most cases:

- *"this VM holds no pairing with its Proxmox host"* — the two machines have not
  paired; §8 gives the steps. Until then the host's own switch turns an existing
  registration off and on, whatever the host's version: type
  `sigmond-setup --rac-off` or `sigmond-setup --rac-on` on the `[host]`. The
  message also names `sigmond-setup --pair-vm`, and says that command needs the
  host at v3.71 or later.
- *"shows an sshd key this VM was not paired with"* — the host's ssh key
  changed. A host at v3.71 pairs this VM again when its sshd key file changes.
  If the message stays, run `sigmond-setup --pair-vm` on the `[host]`. If it
  stays after that, restart the host's ssh service and pair again: on the
  `[host]`, run `systemctl restart ssh`, then `sigmond-setup --pair-vm`. The
  pairing reads the host's key file, and a running ssh service serves the key it
  loaded at its start.
- *"did not accept this VM's pairing key"* — the host refuses the key this VM
  offers. Run `sigmond-setup --pair-vm` on the `[host]`.
- *"could not reach the Proxmox host"* — the host does not answer on the
  private link between the two machines. Your internet connection plays no part
  in that. Check that the Proxmox host runs, then ask again with
  `smd remote status`. If the message stays, tell your fleet admin.

Two more cases:

- *"gave no answer within"* some number of seconds — the host took the request,
  and its switch did not finish in time. Ask again with `smd remote status`.
- Any other message: send it to your fleet admin, whole.

---

## 8. A station installed before v3.71

Such a station keeps the answer its owner gave the old wizard. If the owner said
Yes and the station registered, remote access stays on. If the owner said No, or
the registration failed, it stays off, and the station registers only when its
owner types `smd remote on` (or, on a `[host]` that runs v3.71,
`sigmond-setup --rac-on`).

First find out which layout your station has. Look at the `Decoder VM` line of
the console panel. A station with the private link shows `10.99.0.2` and
`(private link to this host — not on your LAN)`. A station with the old layout
shows an address on your own network; for it, read the last paragraph of this
section.

On a station with the private link, the two machines must pair once before
`smd remote` works. Three steps bring that about, in this order:

1. The decoder VM takes its update first: `smd update --apply` in the `[VM]`,
   once your fleet admin says a release is out
   ([day-2.md](day-2.md#updates--who-decides-and-what-you-run)).
2. Your fleet admin updates the host to v3.71 with the admin's own tool,
   `pm-align`, which updates a Proxmox host to the newest released image without
   a reinstall. Its first run brings the host the new `pm-align` and the
   switch. A second `pm-align --apply`, or `sigmond-setup --pair-vm` on the
   host, then pairs the two machines.
3. `smd remote status` in the `[VM]` now answers.

Between steps 1 and 2, `smd remote` in the VM answers with the not-paired
message. That is expected:

```text
  ✗  this VM holds no pairing with its Proxmox host, so it cannot ask the host to switch remote access
     The Proxmox host works the switch itself, whatever its version.  There, run:
         sigmond-setup --rac-off
         sigmond-setup --rac-on
     To pair this VM, run on the Proxmox host:  sigmond-setup --pair-vm
     That command needs the host at v3.71 or later.
     An older host answers "already configured" and pairs nothing.
     Such a host needs its alignment to v3.71 first.
```

The message says the host works the switch itself, whatever its version. That
holds for turning an existing registration off and on. On an older host,
`sigmond-setup --rac-on` does nothing for a station that never registered.

Once the host runs v3.71, this command pairs the two machines by hand. You need
it when `pm-align` could not pair them, or when a message in §7 asks for it:

```bash
# [host] — on the Proxmox host, not the VM
sigmond-setup --pair-vm
```

It answers:

```text
[wizard] paired: the decoder VM can run smd remote on, smd remote off and smd remote status
```

A failed pairing answers `[wizard] NOT paired:` and names the reason. Send that
line to your fleet admin. On a station with the old layout, the reason reads
`this host has no private link to its decoder VM (no /30 address)`. A /30 is the
size of the small private network that joins the host and its VM. The answer
`[wizard] another pairing is running; nothing done` means the host already runs
a pairing. Wait a few minutes, then run the command again. If the same answer
returns after ten minutes, tell your fleet admin.

⚠ **On a host that has not reached v3.71, this command answers "already
configured" and pairs nothing.** Do not take that answer for success. The host
needs `pm-align` first.

A host at v3.71 checks the pairing again a few minutes after every boot, every 6
hours, and when its sshd key file (the file that holds its ssh host key)
changes. Those checks begin with the host's first attempt to pair.

Until the station pairs, the host's own `sigmond-setup --rac-off` and
`sigmond-setup --rac-on`, typed on the `[host]`, turn an existing registration
off and on. On a host at v3.71, `sigmond-setup --rac-on` also registers a
station that never registered. The console panel of such a host names those two
commands in place of `smd remote` (§4). Its line for turning remote access on
reads:

```text
==> to turn it on, type on another computer:  ssh root@<host address> 'sigmond-setup --rac-on'
```

The host records the outcome of each pairing, and the panel names the VM's
command once that record says the two machines have paired. Your fleet admin
can read the same record on the `[host]` with `pm-align --status`.

**A station with the old layout cannot pair.** The pairing needs the private
link between the host and its decoder VM. A station installed with the old
layout has its VM on the LAN, and no private link exists. There `smd remote`
does not work. The host's own `sigmond-setup --rac-off` and
`sigmond-setup --rac-on` remain its switch for an existing registration; type
them on the `[host]`, which you reach from another computer with
`ssh root@<host address>`. A reinstall brings the private link. For such a host
`pm-align` says "skipped: this host has no private link to its decoder VM", and
adds that `smd remote` needs that link.

---

## When to mention it

Tell your fleet admin if remote access stays `wanted on, not up` for more than a
day, or if they report being able to reach the port but not log in (§5).
Otherwise there is nothing here to check weekly — the weekly routine is
[day-2.md](day-2.md).
