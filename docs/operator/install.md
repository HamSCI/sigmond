# Installing the appliance

> **Audience:** operator
> **Status:** pointer
> **Verified against:** sigmond 007bf72 on 2026-10-10 — remote-access words, the addresses the host relays and the keyboard note checked against commands/remote.py, the wizard's switch and the appliance's console panel and split console; walk-through fixes (live dasi002 + b4) as of 4aec0c2
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

⚠ **Write down the host address when the install shows it.** The setup finishes
by printing the `[host]` (Proxmox) address and the `[VM]` (decoder) address on
the station's monitor. The VM address beside it (`10.99.0.2` on a station with
the private link) works only from the host; you need not keep it. Copy the host
address somewhere you will still have it in a year. You need it for the Proxmox
window and for a rerun of the setup wizard. The remote-access switch runs in the
VM, and you log in to the VM through the host's address too:
`ssh -p 2222 sigmond@<host address>`. On a station with the old layout, use
`ssh sigmond@<VM address>` instead. [remote-access.md](remote-access.md) says
more.

When INSTALL.md §9 says "check it's alive", come back here and continue with
[day-2.md](day-2.md).
