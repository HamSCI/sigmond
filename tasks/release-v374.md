# v3.74 — what this release carries

> ⛔ **v3.74 IS SUPERSEDED BY v3.75 AND MUST NOT BE INSTALLED AT AN IPv6-ONLY
> SITE.**  Its row-3 netfix guard short-circuits on a FIRST boot, before the
> branch that removes the installer fossil 192.168.100.2, strips the dead
> static stanza and writes the persistent `iface vmbr0 inet6 auto`.  The
> fossil left on the bridge makes every later do-we-have-IPv4 test lie, and
> `sigmond-v6-gateway` keys off exactly that question.  Fixed in appliance
> `a800d13`, shipped as v3.75.  Found by rob on a wired IPv6-only box,
> 2026-10-21 — the exact case row 3 was written to fix.  v3.74 was never
> blessed; it sits in `pending/` on all three targets.

**Status:** collecting, 2026-10-20; rows 1 and 3 landed 2026-10-21 (rob).  v3.73 was blessed 2026-10-20 at 23:43Z, and B4 adopted it
in place at 23:45Z.  Nothing here has been ruled yet.  Rows 1 to 5 come from Chris's report on
the Jenny Jump install (v3.70, IPv6-only site, 2026-10-20).  Rows 6 onward come from v3.73's
acceptance and its review.  v3.73 moved its rows 10 and 11, and the rest of row 14, to this
release; they keep their text in `release-v373.md`.

| # | Item | Repos | Status |
|---|---|---|---|
| 1 | The USB stick's boot menu counts down a few seconds, then installs without asking, and the install erases the internal disk.  Chris asks for a longer countdown and a plain warning.  We ship the Proxmox auto-installer's menu unchanged.  Fix: lengthen the timeout (30 to 60 s) and name the entry so that it reads "ERASES THE INTERNAL DISK".  The menu lives in the ISO's grub.cfg, so the build has to repack the ISO, much as it already rewrites the ESP | appliance | landed 10-21: appliance f8ac8b3 |
| 2 | Step 4 of the install has the operator put the stick back into a running host to import the VM.  If the power drops while the stick sits in a machine that boots USB first, the machine reinstalls and erases the station without a word.  Explore: can the installer, or the boot menu, notice an existing Sigmond station and stop?  Failing that, should the menu default to the internal disk once a station exists? | appliance | ruled 10-21: no block; a flash is a new install, and a reinstall costs only a restart from the beginning (Michael).  Nothing to build |
| 3 | On an IPv6-only site, first boot prints "vmbr0 is on the PVE installer fallback 192.168.100.2 — that address is not routable here", then hunts for IPv4 and retries DHCP.  The station comes up fine over IPv6, but the screen reads as a fault: Chris concluded the installer gets no IPv6 address.  Fix in netfix (`firstboot-v3.sh`): check for an IPv6 default route first, and when one exists, say once that the site runs on IPv6 and the IPv4 fallback does not matter.  The status panel already says so ("no IPv4 gateway: this site is IPv6 — normal here") | appliance | landed 10-21: appliance 1be48c4 |
| 4 | Colour on the console status panel, for addresses, logins and the default password (Chris's mockup).  The console prints escape codes from the panel file as is, and they take no columns, so the fit-on-screen limit holds.  Colour adds to the text and never carries the meaning alone.  The panel tests compare text and must strip the codes first | appliance | open |
| 5 | A console menu after login instead of a bare shell (Chris).  The root password stays in front of it, because anyone at the machine can reach the console and every item acts as root.  Offer `sigmond-check`, `sigmond-setup --reconfigure`, remote access off and on, a gateway and DNS test over IPv4 and IPv6, and a shell.  Console only; SSH keeps the plain shell | appliance, sigmond | open |
| 6 | `smd restart <client>` restarts radiod too.  On B4, 10-20, `smd restart hf-timestd` and `smd restart hamsci-physics` each restarted `radiod@AC0G-B4` (13:25:41Z, 13:30:02Z).  A client restart should leave the radio alone | sigmond | open |
| 7 | callhash version floor.  meteor-scatter needs callhash adc9d9d (RR73 no longer reads as a grid), but no dependency says so.  Raise the floor and regenerate the locks together | callhash, meteor-scatter, psk-recorder | open |
| 8 | MSK144 short messages and SWL mode.  jt9's file mode hard-codes `bshmsg` and `bswl` off, so we never decode the short messages WSJT-X decodes live.  Needs a patch to `decode_msk144.f90` | wsjtx build, meteor-scatter | open |
| 9 | Locate each front-end AGC step to the sample, not to radiod's status packets; and measure the step response on B1 with a steady tone (Suggs, v3.73 row 21) | meteor-scatter, ka9q-radio (read only) | open |
| 10 | I/Q recordings in Digital RF, with the sidecars as DRF metadata (Frissell, 10-20).  Waits on answers from Frissell and Suggs | meteor-scatter | waiting |
| 11 | timestd-fusion still writes about 11,000 lines an hour on B4 after v3.73 row 18 | hf-timestd | fixed 10-21, NOT pushed or deployed: hf-timestd f294887 (+ test follow-up) — fusion validated every L1 row in its 30-min lookback each 8.2 s cycle, so each detection trained the arrival window ~450x (B4 obs=12098) and flooded the journal; now validated once per row; per-cycle status lines at INFO once a minute; moderate discontinuities through ConditionLog. ~147k -> ~10-12k lines/day. Reviewed: /home/mjh/hamsci/proofs/review-hf-timestd-f294887.md. Waits on Michael (it changes window training) |
| 12 | The meteor-scatter PSKReporter pipeline reports `decoding_software` as "psk-recorder/0.1" | meteor-scatter, hs-uploader | open |
| 13 | B4's RX888 dropped out 12 times in 24 h (`smd doctor` sdr-flapping).  Waits on a bench test of the cable, hub and card | hardware | waiting — evidence 10-21 (/home/mjh/hamsci/proofs/b4-rx888-dropouts-20261021.md): 11 stalls/day, no kernel event, card stays at 5000 Mb/s; rate rose ~10x when the card went behind a StarTech 14b0:0131 hub at the 10-15 reflash. First bench step: swap only the hub. Side finding: B4 starts with RX888 AGC on |
| 14 | `/etc/sigmond/CLAUDE-COORDINATION.md` is missing on B4 | ops | open |
| 15 | `bless-release.sh` stops at line 43 under `systemd-run`, which sets no HOME.  Default HOME to root's, or say so at the top | appliance | landed 10-21: sigmond-appliance b618a65 |
| 16 | `ops/bin/fleet-ssh` drops stdin across its nested hop, so `fleet-ssh b4 'cat > f' < f` writes an empty file and says nothing.  Pass stdin through, or refuse it | ops | landed 10-21: ops 80245ca (stdin now passes through the nested hop; tests/test_fleet_ssh_stdin.py) |
| 17 | **timestd-fusion replays every L1 row in its lookback into the per-broadcast Kalman bank each cycle** (`multi_broadcast_fusion.py:1312`; found reviewing row 11).  A row entered each filter once per cycle (probe: n_updates=90 after 3 cycles of 30 rows), ~220x over its 30 min — so each filter reports too small a sigma, and those sigmas set the fusion weights (M-H12).  The replay alone triggers "Mode transition detected".  `_update_long_term_stats` (:2590) stamps replayed rows with t=now; `_update_calibration` (:3496) likely the same.  Timing correctness: Michael's ruling before any change | hf-timestd | open, needs ruling |

## Landed 2026-10-21 (rob)

**Row 1 — the installer countdown.**  appliance `f8ac8b3`.  The live entry was
`set timeout=10` under `'Install Proxmox VE (Automated)'` (the list above said
30 s; the ISO says 10).  It is now 60 s under
`'Install Sigmond: ERASES THE INTERNAL DISK'`, still first, so an unattended
install proceeds as before.  `patch-installer-menu.sh` rewrites
`/boot/grub/grub.cfg` inside the iso9660 **without changing the image's
length** — build-usb-v3.sh locates the ESP by sector and takes the payload
offset from the volume size, and both move if the length does.  Verified on
the real ISO: size, ESP sectors and volsize all unchanged.  INSTALL.md §5
updated.  Row 2 (refusing to erase an existing station) is untouched and
waits on Michael's ruling.

**Row 3 — the IPv6-only site.**  appliance `1be48c4`.  netfix now asks whether
vmbr0 is itself a live IPv6 uplink before hunting for IPv4, and if it is, says
so once and stops.  The hunt was not only misleading on Chris's screen: it
costs up to 25 s of dhclient per port and flushes addresses on a machine
already carrying the station, on every boot, because an IPv6-only site never
acquires the IPv4 that would quiet it.  The check is scoped to the device
(`v6_uplink_on()` in the shared net lib) — a bare `ip -6 route show default`
is true because of the RADIO on a dual-homed host and would excuse a dead
bridge.  It also clears `.network-unreachable`, extending `6360654`'s rule to
v6.

**Also in v3.74, not listed above** — five fixes rob pushed 2026-10-21 00:28Z
from AC1SN (DASI-017) field experience, after this list was opened:

| Repo | Commit | What |
|---|---|---|
| sigmond | `eb6236a` | never pass the Rembrandt controllers that hang the host (7735U: three of five xHCIs hard-locked the host) |
| sigmond | `2ee0ed6` | `sigmond-vm-guard` — autostart only after the VM proves 10 min of uptime, ending a crash-reboot loop |
| appliance | `6360654` | a real address clears the "no network" marker; a self-healed host printed NO WORKING NETWORK ADDRESS for three days |
| appliance | `89cf515` | dnsmasq follows the host's resolv.conf instead of freezing the install site's resolver |
| appliance | `67071d5` | NUL bytes in /etc/motd hid the panel from every ssh login; the install banner is dropped |

## v3.75 — 2026-10-21

v3.74 + `a800d13` only.  The row-3 guard now additionally requires that
`iface vmbr0 inet6` is already in `/etc/network/interfaces` and that no
192.168.100.x remains on vmbr0, so a first boot falls through to the branch
that establishes both and every later boot takes the quiet path.

Why v3.74's guard was reached at all on a first boot: IPv6 forwarding is
enabled only by the post-up *inside* that stanza, so on a fresh install
forwarding is 0, the default `accept_ra=1` is honoured, and vmbr0 acquires a
SLAAC address and a default route unaided — making `v6_uplink_on()` true
before any of the persistence work had run.

    sigmond-appliance-v3.75-20261021-release.img
    5,562,695,680 bytes  sha256 c87ab45f28711a9f84c6abb9b4112f7e064adb8f6541d5acf39d4343b743ff7c

Everything else is as v3.74: row 1 (60 s countdown, renamed entry) and the
five AC1SN fixes.  Row 2 and row 6 remain open.
