# Station firmware (BIOS) settings: before you start

> **Canonical for:** the BIOS settings a station computer needs, by platform,
> and how to check each one from Linux afterwards.

A few settings live only in the computer's firmware.  Linux cannot change
them, and sigmond cannot either.  Someone has to sit at the station with a
monitor and keyboard and set them by hand.  The install puts a person at
the console anyway, so do it then: an hour at install saves a site visit
later.

**Your hardware may vary.**  Makers rename and move these settings from
one BIOS release to the next, and the same model can ship with different
defaults.  This page records what fleet stations have found, no more.  If
your menus differ, or a setting below does not appear at all, trust what
your screen shows, note it, and tell us (see "Report your platform").

Fleet survey, 2026-11-25: of twelve Beelink hosts, eleven still hid 3 or
4 GB of their 16 GB from the station.  On AC0G-B4 that shortfall let a
nightly job run the station out of memory, and the kernel killed the timing
recorder.  One BIOS setting would have prevented it.

## The settings

Set the **must** items at install.  The rest stay as the factory left them
unless this page says otherwise.

| # | Setting | Set to | Must? | Why | Linux can check? |
|---|---|---|---|---|---|
| 1 | UMA Frame Buffer Size (AMD graphics memory) | **64M** | must, on AMD hosts | The factory reserves 3-4 GB of RAM for the built-in graphics.  A station shows only a text console and needs 64 MB.  The rest goes back to the station. | yes: `pm-memory` |
| 2 | SVM Mode (AMD) or VT-x (Intel) | **Enabled** | must, on a Proxmox appliance | The station runs as a virtual machine. | yes: `pm-firmware` |
| 3 | IOMMU (AMD-Vi, VT-d) | **Enabled** | must, on a Proxmox appliance | Proxmox hands the USB controllers, and so the receiver, straight to the station. | yes: `pm-firmware` |
| 4 | Restore on AC Power Loss (also "State After G3", "AC Back", "Power On After Power Failure") | **Power On** | must | A station runs unattended.  After a power cut it has to start by itself. | no: test it (below) |
| 5 | Fast Boot | **Disabled** while you install | must, for the install | With Fast Boot on, many machines ignore the USB stick. | no |
| 6 | SMT (hyper-threading) | **Enabled** (factory default) | leave | The CPU plan pins radiod and the station to thread pairs. | yes: `pm-firmware` |
| 7 | HPET | **Enabled** if offered (factory default) | leave | The kernel's fallback clock if it ever stops trusting the CPU clock. | yes: `pm-firmware` |
| 8 | Secure Boot, memory speed (XMP/EXPO), boot order | as shipped | leave | Nothing here needs them changed. | no |

**Power-saving states (C-states, Cool'n'Quiet) no longer need a BIOS
visit.**  The April 2026 checklist turned them off by hand to keep the CPU
clock (the TSC) steady.  The Proxmox host now sets that from its own kernel
command line (`tsc=reliable processor.max_cstate=1`, from
`scripts/proxmox/host-apply.sh`).  All twelve Beelinks surveyed on
2026-11-25 run on a clean TSC.  If `pm-firmware` ever reports the clock
off the TSC, start from the "Fallback" section of
[proxmox/wsprdaemon-proxmox-cpu-clock-tuning.md](proxmox/wsprdaemon-proxmox-cpu-clock-tuning.md).

## By platform

Menu names vary with the maker and the BIOS release.  The paths below
come from stations that made the change, and yours may differ even on
the same model.  Where a cell says "not yet recorded", look for the
setting under **Advanced**, **Chipset**, **Power** or **AMD CBS**, and
please send us the path you found (see "Report your platform").  A
platform missing from this table needs your report most of all.

| Platform | CPU | BIOS seen in the fleet | Graphics reservation | Stations |
|---|---|---|---|---|
| Beelink SER (AZW) | Ryzen 7 5825U | AMI SERAL301 (2025-04-08) | 4 GB | B4, K3LR, W2NAF, UACNJ; N8UR done |
| Beelink SER (AZW) | Ryzen 7 7735U | AMI ASER5009 (2025-03-15) | 4 GB | AC1SN |
| Beelink SER (AZW) | Ryzen 5 5560U | AMI FP655U508, FP655U509 | 3 GB | AB4EJ, KPH2, KFS-OMNI, WB6CXC |
| Beelink SER (AZW) | Ryzen 7 5800H | AMI SER5H508, 5800H602 | 3 GB | KFS-MAIN, AC0G-ND |
| Lenovo ThinkCentre | Core i5-6500T | not yet recorded | none | AI6VN |
| LattePanda Sigma (bare metal) | Core i5-1340P | LP-BS 5.27 (2023-06-13) | none | SIGMA |

**Beelink SER (AMD, AMI BIOS).**  The setup key and the boot-menu key
vary by release; Del, F7, F11 and F12 cover the ones seen so far.
- UMA Frame Buffer Size: **Advanced > AMD CBS > NBIO > GFX Configuration >
  UMA Frame buffer Size**, the path `pm-memory` prints.  Set **64M**.
  (N8UR, 2026-11-17: the host then saw 15.5 of 16 GB.)
- SVM, IOMMU: on from the factory on every Beelink surveyed.
- Restore on AC Power Loss: not yet recorded.  Look under **Chipset** or
  **Advanced > Power**.

**Lenovo ThinkCentre (Intel).**  No graphics reservation to reclaim
(2026-11-17 survey).  Menu paths not yet recorded.

**LattePanda Sigma (Intel, bare metal).**  No virtual machine, so SVM, VT-x
and IOMMU do not matter.  Linux sees 31.1 of 32 GB, so no large graphics
reservation needs reclaiming.  This board offers no HPET; the clock runs on the TSC
without it.  Still set Restore on AC Power Loss and Fast Boot.

## Before you start: the checklist

Take this to the station with a monitor and a USB keyboard.

1. Power on and enter BIOS setup.
2. Write down the BIOS version shown on the first page.
3. Set items 1 to 5 from the table above.
4. Save and exit (usually F10).
5. Install as the install guide says.
6. Once the station runs, **pull the power cord for ten seconds and plug it
   back in.**  The station must start without anyone pressing the button.
   That proves item 4; Linux has no way to read it.

A station already installed needs only steps 1-4 and 6, then on the
Proxmox host:

    pm-memory            # what the host now sees, what the station holds
    pm-memory --apply    # gives the station the freed RAM at its next restart
    qm reboot 100        # restarts the station now (a minute or two without recording)

## Check it from Linux

On a Proxmox host, `pm-firmware` reads what Linux can see of these
settings and says which ones need a visit.  `pm-memory` covers the graphics
reservation.  `pm-align` runs both and prints their one-line findings, so
each alignment shows them.

    pm-firmware           # every check, one line each
    pm-firmware --brief   # one line, as pm-align prints it
    pm-firmware --report  # the platform's facts and the checks, for a report

A bare-metal station has no `pm-firmware` installed; run it from the
sigmond checkout instead: `python3 /opt/git/sigmond/sigmond/scripts/proxmox/pm-firmware.py --report`.

## Report your platform

Every station that installs teaches us something about its hardware.  We
want this page to cover every platform in the fleet, and only operators
can supply that.  After your install, or after any BIOS visit, please send
a short note:

1. The output of `pm-firmware --report` (on a bare-metal station, the
   command in "Check it from Linux").  It names the maker, model, BIOS
   release, CPU and memory, and holds no serial numbers.
2. For each setting you changed or looked for: the menu path you
   followed, the value you found, and the value you set.  "Not present"
   counts as an answer.
3. Whether the station came back by itself when you pulled the power
   cord.
4. A photo of any screen you were unsure about.

Open an issue at https://github.com/HamSCI/sigmond/issues, or send it to
your fleet admin.  We add
each platform to the table above, with credit to the station that found
it.
