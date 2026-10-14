# v3.72 — what this release carries

**Status:** open, 2026-10-14.  Michael decided on 2026-10-14 that v3.72 carries the small field fixes
gathered on 10-13 and 10-14, and that the send-record key split (sink control step 2a-ii) moves to
v3.73 (`plan-sink-control.md` D16).  Each item below gets a short design in chat, or a spec and a plan
when it grows past one flow, before code lands.  Update the Status column as items land.

| # | Item | Repos | Status |
|---|---|---|---|
| 1 | Old dead letters kept a station INVALID for good; only those from the last 24 h now count, and `hs-uploader purge-dead-letters` saves and clears them | sigmond, hs-uploader | done: sigmond 2943431, hs-uploader 3090b91; deployed on AC0G-B4 10-14 |
| 2 | Reporter ids: people see `AC0G/B4`, storage holds `AC0G=B4`, systemd `AC0G\x3dB4`; about 80 sites, 21 bugs (meteor-scatter's `config init` offers to disable the live instance) | sigmond, meteor-scatter, psk-recorder, wspr-recorder, appliance | done: plan `plan-reporter-id-v372.md`, 14 code tasks + final review fixes, pushed (sigmond 66ee16e, meteor-scatter ab16a34, psk-recorder 0bb62df, wspr-recorder d171d24, appliance 1759fc1); deployed and checked on AC0G-B4 10-14 17:40Z |
| 3 | sdr-recover: B4's RX888 also latched after both cold starts of the host on 10-14 (01:38Z test, 15:37Z power loss); see the firmware race in d504c56. A hub's `ppps` claim is not proof that it cuts VBUS (B4's VIA hub measured data-only, 4.98 V through every cut, 10-14); report the port as unverified until a watched cut; say "needs a hand replug" when a cut fails; `04b4:00f3` on return proves nothing; handle a present-but-wedged card; never cut VBUS on a non-RX888 port | sigmond | open |
| 4 | mag-recorder reopens its device after USB re-enumeration; `mag-recorder-upload.timer` joins deploy.toml's units | mag-recorder | open |
| 5 | A package built while the site sink is off gets discarded (the catch-up run fired before sink-on) | sigmond, hamsci-physics | open |
| 6 | A reflash leaves the site sink OFF, and site-restore does not carry it | sigmond, ops | open |
| 7 | `sigmond-radiod-consumers@` gives up at boot one second before radiod starts | sigmond | open |
| 8 | netfix strips vmbr0's address on carrier loss and waits for netwatch to restore it: a 4-minute dead window | sigmond | open |
| 9 | frpc logs every retry during an outage (114 lines in 18 min) | sigmond | open |
| 10 | deploy-wd30.sh's misleading "no sudo"; hamsci-hb-ingest chokes on leftover 0600 files | sigmond | open |
| 11 | `smd align` reports "moved 1" when sigmond and one more moved | sigmond | open |
| 12 | The heartbeat rollup names only the first failing block (uploads outranks doctor by list order), so one INVALID hides another; name every failing block | sigmond | open |
| 13 | After a hand replug nothing revives decoders that systemd gave up on (start limit hit during the outage); after radiod restarts, two hooks restart the hf-timestd stack twice | sigmond | open |
| 14 | New read-only `smd check`: one line per item (receiver page, magnetometer page, station pages over IPv4 and IPv6; PSK, WSPR, magnetometer, GRAPE recording, local store, upload state), "wait N min" where a first file has not arrived, one PASS / WAIT / FAIL verdict and exit code, no timing-judge noise.  INSTALL.md gains an on-site section that teaches this one command, for a console with no window manager | sigmond, appliance | open; research 10-14 (B4 survey) |
| 15 | IPv6 reaches the VM's web pages only on IPv6-only sites: the host's v6 relay (`sigmond-vm6proxy`) installs only from the v6-gateway path, so a dual-stack site has none.  Install it wherever the host has a global IPv6 address.  Inside the VM, gmag-webui (8082) and station-web (8000) listen on IPv4 only, which the host relay makes harmless | appliance | open |
| 16 | `smd watch uploads` printed nothing for 40 s on B4 while WSPR POSTs had just run; `smd status` exits 1 on a healthy station because the timing judge prints six red OFFSET VIOLATION lines | sigmond | open |

Already on main, ships with no further work: Rob's uplink gate (sigmond de38f63).

Moved out: the per-client sink switch (step 2a-ii, the key split) to v3.73; step 2b to v3.74.
