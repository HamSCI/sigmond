# Check: bring-up

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
> **Canonical for:** what the bring-up check in `smd check` means

Check id: `platform.bringup`

## What it looks at

After a fresh install, the station finishes its setup on its own. This is the
first-run bring-up. It starts the services, one after another. The check reads
the state of the bring-up service and the marker file it leaves behind.

On an older image with no bring-up service, this line does not appear.

## What healthy looks like

Healthy shows no line at all. Once bring-up has finished, `smd check` prints
nothing for it.

## What each message means

**WAIT: `still running (started N min ago)`**
Bring-up works through its steps. Wait. Other lines may show WAIT for the
same reason, with the text "bring-up has not started it yet".

**FAIL: `failed; its log is /var/log/sigmond/firstrun-bringup.log.  Tell the fleet admin.`**
The bring-up service stopped with an error. Send the fleet admin that log.

**FAIL: `failed (attempt N of 3; it tries again at the next boot); its log is ...  Tell the fleet admin.`**
One attempt failed. Bring-up tries again at the next boot, up to three
attempts in all. Tell the fleet admin if the next boot fails too.

**FAIL: `failed N times and stopped trying; its log is ...  Tell the fleet admin.`**
Bring-up gave up. It will not try again by itself. The fleet admin must step in.

**FAIL: `ran without the radio: no SDR on the USB bus.  Plug the SDR in; bring-up finishes by itself.`**
The radio receiver (the SDR) was missing when bring-up ran. Plug it in. Bring-up
then finishes without further help. Until then the decoders wait, and their
lines show WAIT with "it starts once the SDR is plugged in".

## What to do

Wait for a WAIT. For a FAIL about the SDR, plug the SDR in. For any other FAIL, tell the
fleet admin, as the line says.

## Where to look closer

Read `/var/log/sigmond/firstrun-bringup.log` in the VM. Run `smd check` again
after a few minutes.

## Two further FAIL lines

Any check can print one of two generic FAIL lines. See "When a check cannot
run" in the checks README.
