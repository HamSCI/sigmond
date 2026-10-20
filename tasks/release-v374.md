# v3.74 — what this release carries

**Status:** collecting, 2026-10-20.  v3.73 was blessed 2026-10-20 at 23:43Z, and B4 adopted it
in place at 23:45Z.  Nothing here has been ruled yet.  Rows 1 to 5 come from Chris's report on
the Jenny Jump install (v3.70, IPv6-only site, 2026-10-20).  Rows 6 onward come from v3.73's
acceptance and its review.  v3.73 moved its rows 10 and 11, and the rest of row 14, to this
release; they keep their text in `release-v373.md`.

| # | Item | Repos | Status |
|---|---|---|---|
| 1 | The USB stick's boot menu counts down a few seconds, then installs without asking, and the install erases the internal disk.  Chris asks for a longer countdown and a plain warning.  We ship the Proxmox auto-installer's menu unchanged.  Fix: lengthen the timeout (30 to 60 s) and name the entry so that it reads "ERASES THE INTERNAL DISK".  The menu lives in the ISO's grub.cfg, so the build has to repack the ISO, much as it already rewrites the ESP | appliance | open |
| 2 | Step 4 of the install has the operator put the stick back into a running host to import the VM.  If the power drops while the stick sits in a machine that boots USB first, the machine reinstalls and erases the station without a word.  Explore: can the installer, or the boot menu, notice an existing Sigmond station and stop?  Failing that, should the menu default to the internal disk once a station exists? | appliance | open |
| 3 | On an IPv6-only site, first boot prints "vmbr0 is on the PVE installer fallback 192.168.100.2 — that address is not routable here", then hunts for IPv4 and retries DHCP.  The station comes up fine over IPv6, but the screen reads as a fault: Chris concluded the installer gets no IPv6 address.  Fix in netfix (`firstboot-v3.sh`): check for an IPv6 default route first, and when one exists, say once that the site runs on IPv6 and the IPv4 fallback does not matter.  The status panel already says so ("no IPv4 gateway: this site is IPv6 — normal here") | appliance | open |
| 4 | Colour on the console status panel, for addresses, logins and the default password (Chris's mockup).  The console prints escape codes from the panel file as is, and they take no columns, so the fit-on-screen limit holds.  Colour adds to the text and never carries the meaning alone.  The panel tests compare text and must strip the codes first | appliance | open |
| 5 | A console menu after login instead of a bare shell (Chris).  The root password stays in front of it, because anyone at the machine can reach the console and every item acts as root.  Offer `sigmond-check`, `sigmond-setup --reconfigure`, remote access off and on, a gateway and DNS test over IPv4 and IPv6, and a shell.  Console only; SSH keeps the plain shell | appliance, sigmond | open |
| 6 | `smd restart <client>` restarts radiod too.  On B4, 10-20, `smd restart hf-timestd` and `smd restart hamsci-physics` each restarted `radiod@AC0G-B4` (13:25:41Z, 13:30:02Z).  A client restart should leave the radio alone | sigmond | open |
| 7 | callhash version floor.  meteor-scatter needs callhash adc9d9d (RR73 no longer reads as a grid), but no dependency says so.  Raise the floor and regenerate the locks together | callhash, meteor-scatter, psk-recorder | open |
| 8 | MSK144 short messages and SWL mode.  jt9's file mode hard-codes `bshmsg` and `bswl` off, so we never decode the short messages WSJT-X decodes live.  Needs a patch to `decode_msk144.f90` | wsjtx build, meteor-scatter | open |
| 9 | Locate each front-end AGC step to the sample, not to radiod's status packets; and measure the step response on B1 with a steady tone (Suggs, v3.73 row 21) | meteor-scatter, ka9q-radio (read only) | open |
| 10 | I/Q recordings in Digital RF, with the sidecars as DRF metadata (Frissell, 10-20).  Waits on answers from Frissell and Suggs | meteor-scatter | waiting |
| 11 | timestd-fusion still writes about 11,000 lines an hour on B4 after v3.73 row 18 | hf-timestd | open |
| 12 | The meteor-scatter PSKReporter pipeline reports `decoding_software` as "psk-recorder/0.1" | meteor-scatter, hs-uploader | open |
| 13 | B4's RX888 dropped out 12 times in 24 h (`smd doctor` sdr-flapping).  Waits on a bench test of the cable, hub and card | hardware | waiting |
| 14 | `/etc/sigmond/CLAUDE-COORDINATION.md` is missing on B4 | ops | open |
| 15 | `bless-release.sh` stops at line 43 under `systemd-run`, which sets no HOME.  Default HOME to root's, or say so at the top | appliance | open |
| 16 | `ops/bin/fleet-ssh` drops stdin across its nested hop, so `fleet-ssh b4 'cat > f' < f` writes an empty file and says nothing.  Pass stdin through, or refuse it | ops | open |
