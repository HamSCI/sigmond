# Installing the appliance

> **Audience:** operator
> **Status:** pointer
> **Verified against:** n/a
> **See instead:** [sigmond-appliance/INSTALL.md](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md)

The install walkthrough belongs to the repository that builds the USB image, so
it lives there instead of being duplicated here.

What to expect before you start: budget about **45 minutes** end to end — the
installer runs unattended for roughly 10 minutes and then **turns the machine
off on purpose** (that shutdown is your signal to pull the stick, not a
failure). Power it back on **with the stick out** — booting with it still in
can restart the installer — and only put the stick back in once that second
boot is up and the console asks for it; you then answer the setup questions and
remove the stick for good when told. Keep it until then.

⚠ **Write down both addresses when the install shows them.** The setup finishes
by printing the `[host]` (Proxmox) address and the `[VM]` (decoder) address on
the station's monitor. Copy both somewhere you will still have them in a year:
after the next reboot the station's keyboard stops working by design — the USB
ports belong to the radio — and the host address is needed for the Proxmox
window and for every remote-access check in
[remote-access.md](remote-access.md). The VM address alone is not enough.

When INSTALL.md §9 says "check it's alive", come back here and continue with
[day-2.md](day-2.md).
