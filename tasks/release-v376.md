# v3.76 — what this release carries

**Status:** collecting, 2026-10-21.  v3.75 was built 06:56Z 2026-10-21 and is in
`pending/` on all three targets; neither it nor v3.74 is blessed.  Michael is on
vacation, so rows needing his ruling are marked.

| # | Item | Repos | Status |
|---|---|---|---|
| 1 | **The wizard must not ask for a grid square when a GPSDO is attached.**  rob, 2026-10-21 | sigmond | open, NO ruling needed |
| 2 | **First boot is slow when a wired cable is connected.**  rob, 2026-10-21 | appliance | open |
| 3 | Row 2 of the v3.74 list — the installer noticing an existing Sigmond station and refusing to erase it | appliance | ruled 10-21, NOT BUILT: no block.  A flash is a new install.  If a stick left in a USB-first machine reinstalls after a power cut, the operator loses a few minutes by starting again from the beginning, nothing more (Michael) |
| 4 | Row 6 of the v3.74 list — `smd restart <client>` restarts radiod too (Michael, B4, 10-20) | sigmond | landed 10-21: sigmond c478b40; dependencies are started, never restarted |

## Row 1 — no grid question when a GPSDO is present

rob: *"if it sees there's a GPSDO attached to the USB port it should skip asking
for the grid locator.  You can figure it out from the GPSDO later on."*

**This needs no ruling: it is already policy.**  Michael, 2026-10-08, rule 1 of
three: *"wherever a GPSDO exists (DASI or not), rely on it.  Elsewhere the
operator's grid square or lat/lon stands as the authority."*  A wizard that
demands a typed grid from a station that has a GPSDO contradicts that.

**The machinery is already there.**  `sigmond-wizard.sh` detects the GPSDO by
USB id (`HAVE_GPSDO`, `GPSDO_MODEL`, LBE-Mini 1dd2:2211 / LBE-1421 2444 /
LBE-1420 2443) and `gpsdo_lock_report()` already extracts a grid into
`GPSDO_GRID_UBX`.  The wizard's own comment states the rest:

> This value is only the PROMPT DEFAULT; the authoritative position is
> re-asserted from the GPSDO after bring-up by sigmond-location-check
> ("location authority first (GPSDO definitive)").

So on a GPSDO station the typed grid is **overwritten anyway**.  The question
costs the operator a step and buys nothing.

**Shape of the fix:** when `HAVE_GPSDO=1`, skip the grid prompt, record the
location source as the GPSDO, and let `sigmond-location-check` assert it after
bring-up.  Ask only where no GPSDO is present.

⚠ **The one real caveat.**  A GPSDO indoors with no antenna may never get a fix
(rob's own bench, 2026-10-18: *"there's no antenna here... I wouldn't expect us
to get any metrology"*).  Anything wanting a grid BEFORE the first fix then has
none, where today it has a typed value.  So the skip must not become a silent
hole: if no fix has arrived by the time bring-up finishes, the station should
say so and ask then, rather than carry an empty location quietly.  Decide
whether WSPR/PSK reporting can tolerate that gap before shipping.

## Row 2 — first boot is slow with a wired cable

rob, 2026-10-21, on a wired IPv6-only box: first boot sat long enough that he
read it as a hang at the root file check.  It was not: it completed and prompted
for the stick.  (He corrected this himself — *"I was wrong. I was impatient."*)
The AI6VN hang after the v3.71 patch, never explained, may be the same thing.

Why it LOOKS like a hang at the file check: Proxmox boots with `quiet`, so after
`/dev/mapper/pve-root: clean, ...` nothing further prints, and a stall anywhere
in systemd is indistinguishable from a hang there.

Candidate cause, not yet confirmed: `networking.service` has its own
`TimeoutStartSec=5min`.  We cap it at 45 s — but the drop-in is written BY
`firstboot-v3.sh`, and `answer.toml` sets `[first-boot] ordering = "fully-up"`,
so firstboot has not run during the first boot's network start.  **On the first
boot the cap does not exist.**  With no cable there is no carrier and it fails
fast; with a cable into a LAN that has no DHCPv4 server it can block for the
full five minutes.

Second candidate: `sigmond-prefer-wired.service` declares
`Wants=network-online.target`, pulling that target into the boot transaction.

**Shape of the fix:** ship the `networking.service` drop-in in the IMAGE rather
than writing it from firstboot, so the cap is in force on the very first boot.
Confirm first by booting once without `quiet` and reading where it actually
sits.
