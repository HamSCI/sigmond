# plan: Wi-Fi bring-up during the wizard (v3.55)

**Status — 2026-09-28: REQUESTED, not built.** rob, in three passes, each of
which widened it:

1. During wizard installation, probe for a Wi-Fi interface and let the operator
   scan for and join an AP — so a device with no Ethernet can still be brought up.
2. Then: *"Wi-Fi ought to be a standard part brought up by the PM at every
   installation site. They all have Wi-Fi."*  So it is not a fallback — every
   DASI box gets a second, independent management path as a matter of course.
3. Then, the one that inverts the design: **⚡ PREFER Wi-Fi OVER WIRED.**

## ⚡ Why Wi-Fi is preferred, not merely allowed

rob, 2026-09-28: *"the wired interface introduces potential for radio
interference into the SDR, so in general we should prefer Wi-Fi if possible."*

This is a physical-layer argument, not a convenience one, and it outranks every
networking preference in this document. 1000BASE-T signals at a 125 MHz symbol
rate; its harmonics and the switching noise of the PHY land squarely in HF, and
unshielded twisted pair entering the shack carries that in as common-mode
current on a conductor that is, electrically, an antenna. Wi-Fi at 2.4/5 GHz is
nowhere near the receiver's passband. **Removing the Ethernet cable removes a
conducted noise path into the instrument.**

A station is a measuring instrument first and a computer second. A management
convenience that raises the noise floor has cost us the thing we are there to
collect.

### The signal gate

Preferring Wi-Fi is only right while the link is good, so the wizard must
measure and say so rather than silently choose:

| RSSI | verdict |
|---|---|
| better than −60 dBm | prefer Wi-Fi, no comment |
| −60 to −70 dBm | usable; say so |
| worse than −70 dBm | ⚠ **warn** — offer wired, and say the trade is RFI vs reliability |

rob set the warn threshold at roughly −70. For reference, AI6VN-PM measured
**−25 dBm on 5745 MHz** — the easy case, and not what a remote site will look
like.

### What this changes

- **The default route belongs on Wi-Fi** in the normal case, with wired as the
  exception — the inverse of the bench configuration described below.
- `sigmond-netfix` must not treat "wired carrier present" as "use wired".
- A site with weak Wi-Fi and a required wired link should be **recorded**, so a
  later noise-floor investigation has somewhere to start.

## Why it is not just "run wpa_supplicant"

Measured directly on AI6VN-PM (pve-manager/9.1.1, kernel 6.17.2-1-pve) while
setting up an unrelated rescue path, 2026-09-28. Four findings, each of which
turns a working feature into one that silently does nothing:

1. ⛔ **Proxmox ships neither `wpasupplicant` nor `iw`.** Both had to be
   installed over the working IPv4 link. On the box this feature exists to
   rescue — one with no Ethernet — there is no way to install them. **They must
   be in the golden image**, which makes this a `build-golden-vm.sh` /
   `provision-components.sh` dependency, not only a wizard change.

2. ⚠ **The regulatory domain defaults to `country 00` (DFS-UNSET)**, which caps
   the radio to 2402-2472 MHz. Every 5 GHz AP is invisible. A site with a
   5 GHz-only AP would see an empty scan list and read it as "no Wi-Fi here"
   when the radio is fine. The wizard must set a country (`iw reg set <CC>`)
   or at minimum say that the scan is 2.4 GHz-only.

3. The radio comes up `DOWN` with `NO-CARRIER`; `ip link set <dev> up` is
   required before `iw dev <dev> scan` returns anything. Scanning a down
   interface fails in a way that looks like "no networks".

4. `rfkill` returned nothing on this host, so a soft-blocked radio cannot be
   assumed visible through it. Check `/sys/class/net/<dev>/flags` and the scan
   result, not rfkill alone.

5. ⛔ **A wrong passphrase is almost invisible.** Three attempts were needed on
   AI6VN-PM, and the ONLY honest diagnostic was `wpa_cli list_networks` showing
   `[TEMP-DISABLED]`. `wpa_state` sat at `SCANNING` — indistinguishable from
   "AP not found"; `journalctl -t wpa_supplicant` logged one line and nothing
   about the failure; and `wpa_supplicant -B` daemonises before logging, so a
   redirect to a file captures only the startup banner. A wizard that shows
   "connecting…" and times out is unusable. It MUST read `list_networks` and
   distinguish **"the access point rejected the password"** from **"that
   network was not found"** — they need different responses from the operator.

6. `iw reg set US` did not take — `iw reg get` still reported `country 00`
   afterwards. 5 GHz APs were visible in the scan regardless, so it did not
   matter here, but a wizard must not assume the call succeeded.

## Shape

- **Probe:** any interface with `/sys/class/net/*/wireless`, or `iw dev`.
  Nothing to do when absent — this must not add a prompt on the 99 % of hosts
  with Ethernet.
- **Offer:** only when the operator has no usable wired link, or on request.
  The existing `sigmond-netfix` already decides "is there a usable link"; this
  is a new branch of that decision, not a new decision.
- **Scan → pick → PSK → join:** `wpa_supplicant` config written to
  `/etc/wpa_supplicant/`, interface handed to the normal address path so
  `sigmond-netfix` treats it like any other candidate.
- **Persist**, and make it survive reboot: a station joined by Wi-Fi during
  install must come back on Wi-Fi.

## Open

- Which subsystem owns the association — `ifupdown` (Proxmox's native
  `/etc/network/interfaces`) or NetworkManager (not installed on PVE). Almost
  certainly `ifupdown` + a `wpa-conf` stanza, which keeps it consistent with
  how `sigmond-setnet` and `sigmond-netfix` already write that file.
- WPA3/SAE vs WPA2 — `wpa_supplicant` 2:2.10 supports SAE, but the config
  differs; a wizard that only writes WPA2 PSK silently fails on a WPA3-only AP.
- Hidden SSIDs need `scan_ssid=1`; the scan list cannot discover them, so the
  wizard needs a "type it in" path as well as a pick-from-list path.
- Enterprise (802.1X) is out of scope unless a site demands it.

## Relationship to the IPv6 work

Independent features, but they meet in `sigmond-netfix`: both add a way for a
host to acquire a usable address when the obvious one fails. See
[plan-ipv6-support.md](plan-ipv6-support.md). The Wi-Fi path is also what makes
the IPv6 bench test safe — on AI6VN-PM it is the rescue route that stays IPv4
while the wired side goes IPv6-only.


## Bench configuration on AI6VN-PM (2026-09-28) — the INVERSE of production

For the IPv6 bench test the Wi-Fi is deliberately **management-only**: a static
address and **no default route**, so that when the wired side is moved to an
IPv6-only LAN the host genuinely has no IPv4 transit. With a default route here
the test would pass while proving nothing.

Production wants the opposite (see "Why Wi-Fi is preferred"). Do not copy the
bench setup to a station.

What was installed, and how it persists:

```
wpa_supplicant@wlp3s0.service     enabled   (Debian template; reads
                                   /etc/wpa_supplicant/wpa_supplicant-wlp3s0.conf)
sigmond-wifi-rescue.service       enabled   (address only; re-asserts "no
                                   default route" on every start)
/usr/local/sbin/sigmond-wifi-rescue
```

`/etc/network/interfaces.d/` was NOT used even though Proxmox sources it: the
main file already carries `iface wlp3s0 inet manual` and ifupdown rejects a
duplicate stanza. Editing the main file is worse — Proxmox rewrites it from its
own API whenever the network is touched in the GUI, and the edit would vanish
silently.

⚠ **Site credentials must never reach the golden image.** The SSID and PSK are
site data collected by the wizard, stored hashed, mode 600. Baking a working
config into an image would ship one operator's home PSK to every station.
