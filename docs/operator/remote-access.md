# Remote access (RAC) — letting the fleet admin reach your station

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 4aec0c2 on 2026-08-23 — walk-through fixes (live dasi002 + b4)
> **Canonical for:** remote access (RAC) — what it is, what it exposes, on/off, how the admin connects

The setup wizard asked you for one decision about this — it ends
*"Enable remote access?"* — and most operators press Enter without knowing what
they just agreed to. This
page is the honest answer: what it is, exactly what it lets someone see, who
that someone is, and how to switch it off. Words you don't recognise are in the
[glossary](glossary.md).

**RAC** stands for *Remote Access Channel*. It is optional, it is off if you
said No, and turning it off later is one command.

⚠ **Two things to know before you read on.** First: **from inside the decoder
`[VM]` there is no way to tell whether remote access is enabled on your
station** — every check on this page runs on the `[host]`, and the one VM-side
verb answers about a different tunnel entirely (§4). Second, and consequently:
**you need the host address**, which prints on the station's monitor at install
time and is very hard to recover afterwards, because the keyboard stops working
by design once the install is done. Write it down then
([install.md](install.md)); if you did not, your fleet admin or your router's
DHCP list can find it for you.

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
- **You can hang up.** One command on the `[host]` stops it (§4), and the
  station keeps doing science exactly as before.

Concretely: a small program called `frpc` runs on the **Proxmox host** and keeps
one TLS connection open to `gw2.wsprdaemon.org` on port 35736, verified against a
certificate authority pinned into the image at `/etc/sigmond/frps-ca.crt`
(source: `/etc/sigmond/frpc-host.toml`, read live on both fleet stations
2026-08-23).

### It runs on the host, not on the VM

This is the part that confuses people, so it gets its own paragraph. The
[station is two machines in one box](README.md#two-machines-in-one-box) — the
Proxmox `[host]` and the decoder `[VM]` — and **the tunnel lives on the
`[host]`**. It is installed there during the first boot, before the wizard even
runs (source: `sigmond-appliance/firstboot-v3.sh`, which unpacks the
`sigmond-rac` payload and runs its `install-host.sh` — "host RAC installed
(inert until configured)"), and it is the wizard on the host console that turns
it on.

Putting it on the host buys two things worth having: the admin can still reach
the station **when the decoder VM is down** — which is precisely when you most
need help — and one tunnel carries the VM's channels too, so nothing has to
track the VM's changing address.

Live check, 2026-08-23: `sigmond-rac-host.service` is *active (running)* on
**both** fleet stations' hosts — AC0G-B4-PM since 2026-08-11, DASI002-PM since
2026-08-07.

---

## 2. What it exposes, and to whom

**Four channels**, and this is the complete list. Nothing else on your station
is reachable through the tunnel.

| Channel | What it reaches | Gateway port |
|---|---|---|
| **VM ssh** | a command line on the decoder `[VM]`, as `hamsci` or `sigmond` | 35800 + your RAC number |
| **VM web** | the [ka9q-web](glossary.md) live-receiver page (port 8081 on the VM) | 45800 + your RAC number |
| **host ssh** | a command line on the Proxmox `[host]`, as `root` | 50800 + your RAC number |
| **host UI** | the Proxmox web GUI (port 8006 on the host) | 55800 + your RAC number |

(Source: `sigmond/scripts/proxmox/sigmond-wizard.sh`, `RAC_BASE_VMSSH=35800`,
`RAC_BASE_VMWEB=45800`, `RAC_BASE_HSSH=50800`, `RAC_BASE_HUI=55800`; confirmed
live against both stations' `/etc/sigmond/frpc-host.toml` — on each station the
four ports are exactly those bases plus that station's RAC number.)

The **RAC number** is bookkeeping, not identity — the gateway assigns a free one
during the install (except on the HamSCI-direct rungs, where it is derived from
your DASI unit number instead: 220 + N, so DASI-001 is RAC #221), it is written
to `/etc/sigmond-appliance/rac-number` **on the `[host]`** — that file does not
exist in the `[VM]`, where `/etc/sigmond-appliance/` holds only `manifest.txt`
and `version` (checked live on both stations, 2026-08-23) — and it **sticks**
across a later
`sigmond-setup --reconfigure` so your ports never move (source:
`sigmond-wizard.sh` header and `ask_rac()`, "the RAC number is sticky"; live on
b4).

The two VM channels do not need to know the VM's address. They point at two
little relays on the host itself (`sigmond-vm-ssh-relay.socket` and
`sigmond-vm-web-relay.socket`, listening on `127.0.0.1:12222` and `:12223`),
and each relay asks the Proxmox guest agent for the VM's *current* address every
time a connection arrives — so a DHCP lease change breaks nothing (source: the
two socket units, read live on b4's host; `sigmond-wizard.sh` header).

### Who can actually get in

Not the public internet. Two locks, and you can check the outer one yourself:

- **The gateway does not publish these ports.** From an ordinary internet
  connection on 2026-08-23, b4's VM-ssh channel port (`:3XXXX`) and host-ssh
  channel port (`:5XXXX`) on `gw2.wsprdaemon.org` both refused to connect, while
  the tunnel control port 35736 — the one your station dials *out* to —
  answered. So the channel ports are not reachable from the open internet; the
  admin reaches them over the gateway's private VPN.
- **Then they still need a key.** Getting through the gateway only puts someone
  at your station's ssh login prompt. Logging in needs an ssh key already
  installed on your station, or the password you set.

So "who can reach my station" is: **the fleet admins who hold both.** In
practice today that is the person who gave you the image, plus the volunteers
who operate the fleet gateway (`gw2.wsprdaemon.org`, run for the HamSCI and
wsprdaemon projects). There is no self-service portal and no public listing.

What it is **not** for: it carries no science data. Your spots and PSWS products
travel their own upload paths ([registration.md](registration.md)) and would
keep flowing with the tunnel switched off.

---

## 3. What the wizard asked you

During the install ([INSTALL.md §7](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#7-answer-the-setup-questions))
the console said, near the end:

```text
Remote access (RAC) is a reverse tunnel to the fleet gateway so the
admin can reach this station for support.  Keys, credentials and
channel numbers are all handled automatically, and you can turn it
off any time with:  sigmond-setup --rac-off

...  (the gateways it will try, most secure first — and, if any hardware
     was missing, a note that remote access is how somebody helps you
     finish the build)

Enable remote access? [Y/n]
```

(Source: `sigmond-wizard.sh`, `ask_rac()`.) If your station was missing a piece
of hardware at install time, the wizard also pressed the point — remote access
is how somebody helps you finish the build once the missing part arrives.

If it worked, the wizard's closing summary said so, and that summary is still at
`/root/sigmond-setup-summary.txt` — **on the `[host]`, and readable only by
`root`** (that is what `/root/` means), so it is `ssh root@<host address>` or
nothing. Real line from b4, with this station's number and ports masked — yours
prints the actual digits:

```text
 RAC:       #<your RAC number> live on gw2.wsprdaemon.org via wsprdaemon (secure) — VM ssh :3XXXX · VM web :4XXXX · host ssh :5XXXX · Proxmox UI :5XXXX (off: sigmond-setup --rac-off)
```

---

## 4. Turning it on, off, and checking it — all on the `[host]`

Everything in this section runs on the Proxmox **host**, after
`ssh root@<host address>`.

**Is it running?** — `[host]`:

```bash
systemctl is-active sigmond-rac-host
```

`active` is the answer you want. For the fuller picture, including how long it
has been up and the last few log lines — `[host]`:

```bash
systemctl status sigmond-rac-host
```

**Are all four channels up?** The tunnel program keeps a small status page on
the host's own loopback address. Counting the running channels should give
**4** — `[host]`:

```bash
curl -s http://127.0.0.1:7500/api/status | grep -o '"status":"running"' | wc -l
```

b4 returned `4` on 2026-08-23. This is exactly the check the wizard itself makes
before it declares success (`sigmond-wizard.sh`; it waits up to a minute for
4/4 and reports `FAILED` otherwise).

**Turn it off** — the tunnel stops, the configuration is kept, so turning it
back on later needs no re-registration. `[host]`:

```bash
sigmond-setup --rac-off
```

**Turn it back on** — `[host]`:

```bash
sigmond-setup --rac-on
```

**Enable it if you said No at install time**, or repair it after a failure —
`[host]`:

```bash
sigmond-setup --reconfigure
```

Press Enter through every question you want to keep; your reporter ID, grid
square, RAC number and PSWS registration all stick
([INSTALL.md §12](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#12-moving-a-station-staged-in-one-place-deployed-in-another)).

(Sources: `sigmond-wizard.sh` subcommand handlers — `--rac-off` disables and
stops `sigmond-rac-host.service` and prints "tunnel is down, config kept";
`--rac-on` re-enables it; both were read from the shipped script, **not** run on
a live station.)

**Nothing on this list can be run from the `[VM]`,** and that is the honest
answer to "is my remote access working?" asked from a decoder-VM ssh session:
you cannot tell from there. Not by design so much as by nobody having built it —
there is no read-only host verb either, which is why the channel check above is a
`curl` against frpc's own API
([docs-gap ledger row 14](../contributor/docs-gap-ledger.md)).

### Ignore `smd admin rac` on an appliance station

Inside the decoder `[VM]` there is a family of verbs — `smd admin rac status`,
`… install`, `… start`, `… stop`, `… configure` — and on your station they will
tell you remote access is **not configured**. That is correct output, not a
fault. Both fleet stations print exactly this in the VM, 2026-08-23:

```text
━━━ wd-rac (frpc) remote-access tunnel ━━━
  ✓  frpc binary : /usr/local/sbin/frpc (0.64.0)
  ⚠  not configured on this host (/etc/sigmond/frpc.toml / wd-rac.service missing)
     provision with:  smd admin rac install   (or  smd tui  → RAC screen)
```

Those verbs manage a **VM-side** tunnel (`/etc/sigmond/frpc.toml`,
`wd-rac.service`) that exists for stations installed *without* the appliance
image — a plain Linux box with no Proxmox host under it. Your appliance station's
tunnel is the host-side one from §1, and it is healthy while the VM says this.
**Do not run `smd admin rac install` to "fix" it** — that would stand up a
second, redundant tunnel. If the VM's message worries you, check the host
instead, with the commands above.

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
VM: both fleet stations set `PermitRootLogin no` in
`/etc/ssh/sshd_config.d/10-sigmond-operator.conf` (verified live on b4 and
dasi002, 2026-08-23). The port answers, the host key is exchanged, and then
authentication fails every time — which looks exactly like a broken tunnel and
is not one.

**The truthful form is `hamsci@` or `sigmond@`** for the decoder VM. `root@` is
correct only for the **host ssh** channel, where root login is permitted.

This is a known defect in the dashboard's generated instruction, not something
you can fix from your station — it is tracked as row 2 of the
[docs-gap ledger](../contributor/docs-gap-ledger.md). If an admin tells you they
"can reach the port but can't log in", this is the first thing to suspect.

---

## 6. Privacy — and switching it off for good

Plainly:

- **It gives an admin a shell on your machine.** Not a limited view, not a
  read-only dashboard: a login, as `hamsci`/`sigmond` on the VM or `root` on the
  host. Someone using it can read and change anything on the station. That is
  the trade — it is what makes "just ask, they'll fix it"
  ([INSTALL.md §11](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#11-if-something-goes-wrong))
  possible.
- **The tunnel itself forwards nothing but those four ports.** It is not a
  route onto your home network: no other device of yours becomes reachable
  *through the tunnel*. (An admin who logs in is then on your LAN like any
  other machine in the house — that follows from the shell in the bullet above,
  not from the tunnel.)
- **Nothing about it is public.** The channel ports are not open to the internet
  (§2), and the station is not listed anywhere a stranger can browse.
- **It is not needed to produce or upload data.** Every upload path works with
  it off.
- **It is yours to end.** `sigmond-setup --rac-off` stops it now and it stays
  off across reboots; nothing on the station turns it back on by itself.

If you want it gone permanently rather than merely stopped, run
`sigmond-setup --rac-off` and **tell your fleet admin** — so that "cannot reach
the station" is recorded as your decision rather than chased as a fault. Do not
delete files under `/etc/sigmond/` by hand to achieve it
(→ [do-not-touch.md](do-not-touch.md)).

---

## 7. If it says FAILED

The install prints `RAC: FAILED — …` in its closing summary, and
[INSTALL.md §7](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#7-answer-the-setup-questions)
tells you not to worry about it at the time. That is right: **a failed tunnel
costs you nothing but support access.** The radio, the recorders and every
upload path are unaffected.

**The fix is one command**, from the `[host]`, whenever you like
([INSTALL.md §11](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#11-if-something-goes-wrong)):

```bash
# [host] — on the Proxmox host, not the VM
sigmond-setup --reconfigure
```

Press Enter through everything; only the remote-access step needs to re-run.

**Where to look for the reason.** A tunnel that was never configured leaves *no
journal entries at all* — the service is written to stay inert until its config
file exists (`ConditionPathExists=/etc/sigmond/frpc-host.toml` in
`/etc/systemd/system/sigmond-rac-host.service`, read live on b4). So an empty
`journalctl -u sigmond-rac-host` means "never set up", not "broken". The wizard's
own summary is the place that says what happened — `[host]`, as `root` (the file
lives on the host and is root-only):

```bash
cat /root/sigmond-setup-summary.txt
```

Its `RAC:` line either names your number and four ports, or says why not.

**On an old image, ask for a current one rather than retrying.** This step
used to fail for everybody, twice over, and both traps were fixed in the
wizard rather than in anything you can reach:

- Every greenfield install from **2026-07-30 until the gateway ladder landed on
  2026-08-09** finished with remote access dead, silently, because the wizard
  pointed at a registration service that never answered. Nobody noticed until
  somebody needed remote support and went looking, on 2026-08-06 (source:
  `sigmond-wizard.sh`, the RAC-ladder comment block).
- The **v3.25 and v3.26** images then shipped a "secure" setting that no
  gateway actually served, so installs quietly fell through to a less secure
  rung (source: `sigmond-wizard.sh`, the RAC-ladder comment block, corrected
  2026-08-09).

Today the wizard tries a ladder of gateways, announces on the console which one
answered, and falls back to `gw2.wsprdaemon.org` — b4's live
`/etc/sigmond-appliance/rac-registrar` (`[host]`, like `rac-number` above) reads
`http://gw2.wsprdaemon.org:35737/register`, its recorded tier is
`wsprdaemon (secure)`, and its four channels have been up since 2026-08-11. If
`sigmond-setup --reconfigure` still fails, the answer is a newer image, not more
retries — ask your fleet admin.

**A tunnel that drops and comes back is normal.** The service restarts itself
every 30 seconds until the gateway answers (`Restart=always`, `RestartSec=30s`),
so a broadband outage or a DNS hiccup heals on its own — b4's journal shows
exactly that on 2026-08-19: several failed connection attempts, then
`login to server success` and all four channels re-added, with no intervention.

---

## When to mention it

Tell your fleet admin if remote access says FAILED and you *want* it, if you
turn it off deliberately, or if they report being able to reach the port but not
log in (§5). Otherwise there is nothing here to check weekly — the weekly
routine is [day-2.md](day-2.md).
