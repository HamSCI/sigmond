# v3.73 — what this release carries

**Status:** open, 2026-10-16.  Michael's decisions so far: sink control step 2a-ii (the send-record
key split) ships here (`plan-sink-control.md` D16), and so does Rob's e2488f2, held out of v3.72
when Michael chose to ship v3.72 as built (10-15).  Each item gets a short design in chat, or a
spec and a plan when it grows past one flow, before code lands.  Update the Status column as items
land.

| # | Item | Repos | Status |
|---|---|---|---|
| 1 | Sink control step 2a-ii: each sender keeps its own send record, with a one-time migration of the records (and queued retries) it shares today, the daemon skipping a producer whose own sender still runs (D13), and forward-only sending enforced (`plan-sink-control.md` §10.4, D16).  Map of the code: `tasks/research-key-split-v373.md`; its six questions need answers before code lands | hs-uploader, sigmond | open; no code yet |
| 2 | Rob's e2488f2: netfix de-fossilizes only an IPv4 address on a vmbr0 that carries the default route.  Without it an IPv6-only or Wi-Fi-uplink station (DASI sites run cable-free) can stall boot about 5 minutes, as v3.71 did | appliance | on main, not in v3.72 |
| 3 | No install turns on the magnetometer's daily packaging.  `mag-recorder/deploy.toml` links `mag-recorder-upload.timer` but leaves it out of `[systemd].units` on purpose, so that an operator enables it by hand.  That predates the sink switch, which now decides what ships.  Since nothing ever builds a daily zip, hs-uploader's `mag-psws` pipeline finds nothing to send on any station that did not enable the timer by hand.  B4 has sent no magnetometer data since at least 10-10 (v3.70 night check, before and after) and does not on v3.72.  Fix: list the timer in `[systemd].units` so every install turns it on, and drop the comment that tells the operator to.  The site sink switch then governs shipping, and `sink_doors` already sets aside zips built while it read `off`.  Before it lands, decide whether to package a day whose samples read all zeros (B4's RM3100 NACKs since 10-15); turning the timer on would otherwise build that day, and the switch would let it ship.  Also folds in release-v372 #4, which this item supersedes for the timer half | mag-recorder, sigmond | open |
