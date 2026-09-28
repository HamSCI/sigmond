# plan: Wi-Fi bring-up during the wizard (v3.55)

**Status — 2026-09-28: REQUESTED, not built.** rob: during wizard installation,
probe for a Wi-Fi interface on the host, and if one exists let the operator scan
for and join an access point — so **a device with no Ethernet can still be
brought up**.

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
