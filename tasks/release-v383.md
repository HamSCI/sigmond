# v3.83 — what this release carries

**Status:** collecting, opened 2026-10-26 after v3.82 was blessed (01:46Z)
and B4 was reflashed to it.

| # | Item | Repos | Status |
|---|---|---|---|
| 1 | **Evaluate the T6 chain-delay residual** (carried from v3.74 row 18).  T6 measures each station's chain delay: the offset between the TS-1 pulse as received and the lower clock tiers.  On B4 after the v3.82 reflash, T6 sits 14 to 17 ms from T4, and ND's offset is about 17.7 ms.  The offset judge gates T6 against T4 at sub-millisecond tolerance, so it reads that offset as a conflict.  It logs a CRITICAL CROSS-BENCH CONFLICT about once a minute and hands GRAPE the T4 labels while authority publishes T6.  Fix as ruled: the judge learns each station's residual as its measured chain delay (stored, with sigma) and adopts T6 while the residual stays within a few sigma of it.  It alarms loudly on a change (re-measure and confirm, never absorb), and refuses T6 outright outside a coarse gross-error bound (chain-delay range plus the lower tier's uncertainty).  GRAPE then moves from T4 to T6 labels, a step of about 16 ms on B4 and ND: announce it and mark the moment in the data | hf-timestd | ruled 2026-10-23 (Michael); added to v3.83 2026-10-26.  Not built |
| 2 | **The first install on B4 missed the ethernet**, though the link LED was lit.  A second install, with nothing physical changed, found it.  The log went with the PM reinstall, so the next fresh install should keep one | appliance | open; needs a reproduction |
| 3 | **A reflash loses the magnetometer's I2C address.**  B4's RM3100 answers at 0x20 and its MCP9808 temperature sensor at 0x19 (`mag-usb -S`, `-T`).  The template writes 0x23 and leaves the temperature address commented out, so the recorder logged half an hour of all-zero samples until set by hand.  Fix: probe both addresses at install and write what answers; refuse to record zeros as data | mag-recorder, sigmond | open |
| 4 | **A reflash makes new hs-uploader keys**, which PSWS does not know.  On B4 the old keys came back by hand from the pre-flash backup.  The station-identity store should carry them through a reflash | sigmond, hs-uploader | open |
| 5 | **The RX888 came up wedged behind B4's new UGREEN hub** on a cold boot (descriptor read -110, address -62).  A replug cleared it.  Find out whether bring-up can power-cycle the hub port, as the switching hub allows | appliance | open; watch for a repeat |
| 6 | **Drop the v3.81 nest limit from the update rig.**  v3.82 passes `--yes` to each install.sh, so `test-update-v3.sh` no longer needs to excuse hf-timestd's setup wizard (around line 2979) | appliance | open |

Also open, not tied to a build: the E-bis and E-ter NOT EVALUATED rulings on
the update rig, how many blessed images to keep, and Task 29 on the B4 bench.
