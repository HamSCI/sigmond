# Do not touch — the guard rails

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 4aec0c2 on 2026-08-23 — walk-through pass 2 fixes (live dasi002 + b4)
> **Amended 2026-09-02** (not re-walked): the SDR sentinel was retired, so a
> replugged RX888 is brought back with `smd status` + `smd adopt`.
> **Canonical for:** what an operator must not do on a station, and why

Almost nothing on a station is fragile. What *is* fragile is the small set of
things that were configured once, deliberately, and are now load-bearing for
everything else: which commit the radio is built from, which CPU cores it owns,
which grid square goes on every upload. None of those announce themselves when
you break them. The station keeps saying `active`, the web pages keep loading,
and the damage shows up days later as missing science.

So this page is short and blunt. Everything on it is something that looks
harmless, is easy to type, and has cost this project real data at least once.

Words you don't recognise are in the [glossary](glossary.md). What *healthy*
looks like is [day-2.md](day-2.md); what to do when something is broken is
[troubleshooting.md](troubleshooting.md). This page is the third leg: what not
to do in either case.

**Nothing here is a trap you can fall into by reading.** Every read-only
command in the other operator pages — `smd status`, `smd doctor`,
`smd version`, `smd update` with no flag, `df -h /`, the four web pages — stays
safe, always. The list of what you may do freely is
[at the bottom](#what-you-may-do-freely).

---

## The table

Everything below runs on the **decoder VM** (`[VM]`) unless the row says
`[host]`. "Tell your fleet admin" always means: say what you typed and when,
before anything else — that one sentence is usually the difference between a
five-minute fix and a week of chasing ghosts.

| Don't | Why | If you already did |
|---|---|---|
| **`sudo smd …`** — anything | `smd` refuses a sudo-wrapped invocation outright and prints *"don't run smd under sudo — it elevates itself when a verb needs root"* (`bin/smd`, the guard at the top of `main`), because config files and station identity must land under you and not root | Nothing to undo — it refused. Re-run it as yourself |
| **`apt upgrade` / `apt install` / `apt remove`**, in the `[VM]` or on the `[host]` | The radio and several decoders are **native binaries built on this machine** against the libraries that are on it now (`_install_radiod_native` in `bin/smd`; [`docs/native-binaries.md`](../native-binaries.md)) — and nothing on the station holds those packages in place, so an upgrade moves the ground under a build nobody will re-run (`apt-mark showhold` is empty on both fleet stations, 2026-08-23). On the `[host]` the stakes are different and worse: the radio reaches the VM through a **PCIe USB controller handed over at boot**, and a host kernel or Proxmox upgrade can leave those controllers bound to the host instead — at which point the VM simply has no radio ([`docs/proxmox/wsprdaemon-proxmox-vm-setup.md`](../proxmox/wsprdaemon-proxmox-vm-setup.md) Steps 4 and 6) | Tell your fleet admin **before the next reboot** — that is when a host-side change bites — and send `smd doctor` + `smd version` |
| **`pip install` / `uv pip install`** anywhere | Every client runs from a virtual environment built from a committed `uv.lock`; a hand-installed package replaces an *editable* link to the shared source with a frozen copy, which then stops tracking every future update silently (`CLAUDE.md`, "The two layers to consider" — this exact skew hid a fix from hf-timestd for days on DASI002) | Tell your fleet admin; `smd doctor` detects this class as `venv-skew` |
| **`git pull` / `git checkout` / `git add -A`** inside `/opt/git/sigmond/*` | Deploy trees are not workspaces — they are a git working tree, an install target *and* the running source at once, owned by service users, and a bare pull leaves compiled binaries stale while the checkout claims to be current ([`CONTRIBUTING.md` §8](../../CONTRIBUTING.md#8-deploy-trees-are-not-workspaces), §9; a stray `git add -A` once broke `git pull` on a field unit) | Tell your fleet admin, send `smd doctor`; do **not** try to undo it yourself |
| **Run any script out of `/opt/git/sigmond/…` by hand** — `install.sh`, `deploy.sh`, or a `sudo bash …/scripts/<anything>.sh` that some command's output suggested (e.g. `setup-psws-keys.sh`) | Those are installers, not repair tools: they rewrite system files on a *running* station — sysctl drop-ins, chrony config, CPU-affinity setup, systemd units (`hf-timestd/scripts/install.sh`) — and hf-timestd's did exactly that damage once, silently killing a timing refclock for 9.5 hours (hf-timestd#16; the offending step was removed in `9b7e03b`, 2026-08-16). Your fleet admin may run one deliberately — [`CONTRIBUTING.md` §8](../../CONTRIBUTING.md#8-deploy-trees-are-not-workspaces) repairs a consumer with its `install.sh`, never `deploy.sh` — but that is their call, on a station they have decided to take out of service | Tell your fleet admin. If timing looks wrong afterwards, say so explicitly — it can fail hours later, not at the time |
| **`smd install ka9q-radio`** or **`smd component install ka9q-radio`** | That path re-pins the radio's checkout to ka9q-python's compat commit and **would revert a fork or merge checkout** — `bin/smd` says so in as many words (`_rebuild_component`: *"Deliberately NOT `_install_radiod_native` / `smd install` — those repin to the ka9q-python compat commit and would revert a fork/merge checkout"*). b4 is on exactly such a checkout today: branch `upgrade-2026.08.15` at `7fca458a`, tracking a fork remote (live, 2026-08-23) | Tell your fleet admin **immediately** — before any restart or reboot. The wrong radio build can look completely healthy |
| **`smd apply`** or **`smd component update`** | `smd apply` reconciles the whole station with config in one pass — it enables and starts units, rewrites radiod's CPU-affinity drop-ins and the cpufreq governor on its cores, rewrites the `radiod@*.conf.d/` fragments, reconciles the RX888 FX3 firmware variant **and restarts radiod if that firmware changed**, and may restart the uploaders — it does whenever anything changed or one is already down (`cmd_apply` in `bin/smd`). `smd component update` pulls every repo and then runs the same apply | Tell your fleet admin, then `smd status` and send it. Expect the radio to have bounced; give timing ten minutes before judging it |
| **`smd doctor --fix`** or **`smd update --apply`** unless you were told to | The plain forms are read-only; these two flags are precisely what turns them into a change to a working station. `--fix` repairs file ownership and nothing else (`smd doctor --help`: *"repair ownership (the only auto-repairable class)"*), and `--apply` performs the update plan for real ([day-2.md](day-2.md#updates--who-decides-and-what-you-run) — a release goes through the [canary](glossary.md) station first) | Send the output to your fleet admin and say which flag you used |
| **Edit `/etc/radio/radiod@*.conf`, or anything in `radiod@*.conf.d/`** | Nothing in `/etc/radio` was written by hand: the base file is generated from a template and says so in its own first three lines, and the `conf.d/` fragments are **rewritten by `smd apply`** whenever they differ from what the client declares — so your edit either vanishes at the next apply or, worse, survives. Two of its settings are load-bearing (`fft-threads`, `affinity`) — see below | Do **not** restart radiod. Tell your fleet admin what you changed; the file has to be repaired before the radio is bounced |
| **`smd config <client> edit` / `smd config edit <client>` (both orders parse, but they are two different flows — the first runs the guided PSWS wizard `psws.cmd_edit` for `hf-timestd`/`mag-recorder`, the second runs the client's `deploy.toml [contract.config]` edit entry point `cmd_config_edit_client`), `smd config init <client>`, or a client's own `<client> config init`** | These rewrite a client's configuration file — the same class of change as hand-editing `/etc/sigmond/`, just with a friendlier front door. Two of the station's own messages hand them to you unprompted: `smd status`'s `━━━ PSWS upload not finished ━━━` block ends each line with `finish:  smd config hf-timestd edit`, and `mag-recorder`'s log on a station with no sensor ends with *"run `mag-recorder config init` (or `smd config init mag-recorder`)"* (both live, 2026-08-23). Neither is addressed to you. The operator path for changing station identity or PSWS ids is `sigmond-setup --reconfigure` on the `[host]` ([registration.md §5a](registration.md#5a-get-the-ids-in-a-browser-once)); for a client that was never set up here, the answer is to tell your fleet admin so the unit gets disabled | Tell your fleet admin what the editor was opened on and whether you saved. Send `smd status` |
| **Edit or delete anything under `/etc/sigmond/`** | It is the station's identity and wiring — `topology.toml`, `coordination.toml`, and the `coordination.env` that sigmond renders from it and every client reads. It is written by the wizard and by `smd`, not by hand; deleting a file here does not turn a feature off, it makes the station disagree with itself | Tell your fleet admin. To turn something off there is always a supported verb — e.g. `sigmond-setup --rac-off` on the `[host]` for remote access ([remote-access.md §6](remote-access.md#6-privacy--and-switching-it-off-for-good)) |
| **`smd admin rac install`** inside the `[VM]` | Your remote-access tunnel runs on the Proxmox **host**, not in the VM; the VM's `smd admin rac` verbs manage a *different* tunnel and their "not configured" message is correct output, not a fault. Running the install would stand up a second, redundant tunnel ([remote-access.md §4](remote-access.md#ignore-smd-admin-rac-on-an-appliance-station); [docs-gap ledger row 13](../contributor/docs-gap-ledger.md)) | Tell your fleet admin so the extra tunnel gets removed from the gateway too |
| **Change CPU pinning, the VM's CPU count, or the `[host]`'s CPU settings in the Proxmox GUI** | `radiod`'s FFT and block threads have to share one physical core's L1/L2 cache, and the host enforces that with a boot **hookscript** doing strict 1:1 vCPU→pCPU pinning plus per-core frequency caps (`hookscript: local:snippets/cpu-pin-100.sh`, `-smp 14,sockets=1,cores=7,threads=2`, live on b4's host 2026-08-23; `CLAUDE.md`, "CPU pinning & the Proxmox host"). Change the core count and the computed layout is wrong; the symptom is USB sample loss, which appears as gaps in recordings, not as an error | Put it back exactly as it was in the Proxmox GUI and reboot the `[host]`. Tell your fleet admin the dates it was wrong — recordings from that window are suspect |
| **Move the station to a new location without `sigmond-setup --reconfigure`** | The grid square is baked into every upload, the timing station's coordinates and the metrology channels; moving the box without re-running the wizard puts the **wrong location on all of it**, and nothing complains ([INSTALL.md §12](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#12-moving-a-station-staged-in-one-place-deployed-in-another)) | Run it now, on the `[host]`: `sigmond-setup --reconfigure`, type the new grid square, press Enter through the rest. Then tell your fleet admin which days were reported from the old grid |
| **Plug the RX888 into a USB 2 port, or through a hub** | It streams more than 2 Gbit/s; USB 2 cannot carry that and a hub starves the transfer, and the symptom is **silent sample loss** rather than an error ([shopping-list.md](../hardware/shopping-list.md#things-that-look-right-but-arent)). Hubs are fine for the GPSDO and the magnetometer — never for the radio | Move it to a blue USB-3 port, straight into the machine. Then `smd status` to see it under **adoptable**, and `smd adopt <name>` to bring it back — nothing starts on its own ([troubleshooting.md](troubleshooting.md#rx888-not-found-or-the-waterfall-is-blank)) |
| **Reboot to fix an RX888 that has gone missing** | A warm reboot holds USB power, so a latched FX3 stays latched — *"Only removing power — or physically replugging the RX888 — resets it"* (`scripts/proxmox/sigmond-wizard.sh`). A reboot is the first thing everyone tries and it is the one thing that cannot work here | Power the whole box **off**, wait, power it on. Allow ten minutes ([troubleshooting.md](troubleshooting.md#replug-restart-reboot-reinstall--which-one-when)) |
| **Delete files to free disk space** | You cannot tell the station's regenerable scratch from the day's science by looking, and the timing client already manages its own eviction — above 95 % it stops writing and then deletes its own oldest recordings (after a ten-minute pause above 95 % — see [day-2.md §3](day-2.md#3-disk--df--h-)) | Tell your fleet admin what you deleted and from where. Do not delete anything else while waiting |

---

## The reasoning, row by row

If the one-line "why" above was enough, skip this. These are the ones worth a
few more words, because in each case the *harmless-looking* version is what
people actually type.

**`sudo smd` is the one prohibition that enforces itself.** `smd` is designed
to run as you. When a verb genuinely needs root — `start`, `stop`, `restart`,
`reload` — it re-runs itself under `sudo` on its own (`_need_root()` in
`bin/smd` re-execs `sudo -- env SIGMOND_ALLOW_SUDO=1 smd <same arguments>`), and
because the appliance image gives `hamsci` passwordless sudo, you are not even
asked for a password. So `smd restart mag-recorder` is the complete command;
there is never a `sudo` in front of it. If `smd` ever answers that your account
may not use sudo, that is a provisioning fault on the station — report it, don't
work around it ([troubleshooting.md](troubleshooting.md#one-thing-to-know-before-you-type-anything-that-changes-the-station)).

**The three package managers are one rule with three faces.** `apt`, `pip` and
`git pull` all look like "getting something newer," and all three break the same
invariant: the station's software is a *set* that was built and tested together,
and `smd update` is the only thing that moves the whole set at once, in the right
order, with the right ownership. The failure mode is never an error message. It
is a service that keeps running the old code, or a checkout that reports the
right commit while its virtual environment imports something else — the exact
class [`CONTRIBUTING.md` §9](../../CONTRIBUTING.md#9-verify-the-thing-that-runs-not-the-thing-you-installed)
opens with: *"Nearly every failure this project has had looked like success."*

**`install.sh` is not a repair tool, and neither is `smd apply`.** Both are
reconciliation passes: they take the machine from "unknown" to "as configured,"
which is exactly right on a fresh host and exactly wrong on a live one, where
"unknown" is in fact a carefully tuned running station. `smd apply` will happily
swap the RX888 firmware variant back to the configured default and restart the
radio to make it take, and a client's own `install.sh` will happily rewrite
chrony's configuration underneath a timing stack that is mid-measurement. Both
belong to whoever configured the station, not to whoever is looking at it today.

**The radio's build is a pin, and pins are decisions.** ka9q-radio is not pinned
in sigmond's catalog at all — `etc/catalog.toml` says so explicitly, and points
at ka9q-python's `ka9q_radio_compat` as the owner of that version, *"DERIVED from
a real check of the 118 status tags and the encoding enum."* A station may
legitimately run a **superset** of that pin — b4 does today, on a merge branch
carrying RX888 firmware and serial-handling work that upstream does not have.
`smd install ka9q-radio` would quietly put it back on the plain pin and rebuild.
The station would come up, `smd status` would be all ticks, and the reason the
radio had stopped behaving would be invisible for as long as it took somebody to
run `git log` in the right directory.

**The radio's config file is rendered, not authored.** `/etc/radio/radiod@<designator>.conf`
opens with *"Generated by `smd config init radiod` (CONTRACT-v0.5 §14)"* — that line is in
the real file on both fleet stations, 2026-08-23 — and the channel definitions live in
`radiod@<designator>.conf.d/<NN>-<client>.conf`, one fragment per client, each rendered from
a template in that client's repo. `smd apply` hashes what it would write against what is
there and rewrites any fragment that differs
(`lib/sigmond/commands/radiod_fragments.py`), so a hand edit inside `conf.d/` is reverted
the next time anyone applies. The two settings worth naming are `fft-threads = 1` — `0`
silently starves decoding rather than failing — and `affinity = false`, which is what hands
CPU confinement to systemd and the pinning described above. Both read exactly that on b4
and dasi002 today.

**CPU layout is physics, not preference.** The reason radiod gets its own
physical core is cache: its FFT and block threads share L1 and L2 when they sit
on the two hyperthreads of one core, and that sharing *is* the difference
between clean capture and dropped USB packets. The layout is computed from the
host's real sibling pairs and applied by a Proxmox hookscript at every VM start.
Adding a core in the GUI does not add capacity — it invalidates the map. Note
that the isolation is done entirely with pinning and systemd CPU affinity: there
is no `isolcpus` on either fleet station's kernel command line (`/proc/cmdline`,
live 2026-08-23), and guest-kernel isolation was tried and reverted for making
things worse.

**The grid square is the one wrong answer nobody catches.** Every other mistake
on this page eventually produces a symptom. A station reporting from the grid
square it was *built* in produces perfect, plausible, wrong data indefinitely —
the spots upload, the timing products upload, the portal accepts them, and the
science is quietly corrupted. Re-running the wizard takes two minutes and the
recorders restart themselves.

---

## What you may do freely

No permission needed, any time, as often as you like. All `[VM]` unless noted.

- **`smd status`** — the weekly check ([day-2.md](day-2.md#1-smd-status--is-everything-running)).
- **`smd doctor`** — read-only. Just never with `--fix` unless you were told to.
- **`smd version`** — what this station is actually running.
- **`smd update`** with no flag — a **dry run**: it prints the plan and changes
  nothing ([day-2.md](day-2.md#updates--who-decides-and-what-you-run)).
- **`smd watch <thing>`** — `gpsdo`, `uploads`, `mag` and friends: live views,
  read-only.
- **`smd psws status`**, **`smd config uploads status`** — status verbs, all read-only.
- **`smd component list`** — every installed component and whether it is
  [enabled](glossary.md) ([day-2.md](day-2.md#installed-enabled-shown)). By
  default it runs `git fetch` for each component first, so it needs internet and
  takes a while; `smd component list --no-fetch` skips that and answers from
  cached refs, which is all you need for the LIFECYCLE column.
- **`df -h /`**, **`lsusb`**, **`systemctl status <unit>`** — plain Linux looking-around.
- **`du -xh --max-depth=2 /var/lib /home /var/log 2>/dev/null | sort -h | tail -15`**
  — read-only: shows what is actually using the disk, so your message about a
  full disk can name a directory instead of a percentage. It skips what your
  account may not read, so the answer is incomplete; paste it to your fleet admin
  anyway ([troubleshooting.md → *Disk filling up*](troubleshooting.md#disk-filling-up)).
  ⚠ Not to be confused with `smd admin storage`, which is **not** a read-only
  report — its subcommands change the station.
- **The four web pages** — receiver, timing, magnetometer, Proxmox
  ([day-2.md](day-2.md#the-four-windows)).
- **`smd restart <cheap name>`** — `mag-recorder`, `gpsdo-monitor`,
  `igmp-querier`, or `gmag-webui` (which also pulls `mag-recorder`). These four
  declare no dependency on the radio, so restarting one moves nothing else.
- **`smd restart all`** — when [troubleshooting.md](troubleshooting.md#restarting-one-client-is-often-not-one-client)
  sends you there. Everything else — `wspr-recorder`, `psk-recorder`,
  `meteor-scatter`, `hf-timestd`, `ka9q-web`, `radiod` — **bounces the radio for
  the whole station**, so `smd restart all` is the honest way to do it: one
  bounce, every recorder re-anchored together. Look up the name in that table
  first, every time.
- **`passwd`** — on the `[VM]` and on the `[host]` separately
  ([day-2.md](day-2.md#passwords-and-logins)).
- **Reboot, or power off and on, the whole box** — with your fleet admin's
  agreement when you are chasing a fault, and freely when you need to move
  furniture. It comes back by itself; allow ten minutes.
- **Unplug the monitor and keyboard.** After the first reboot they do nothing
  anyway — the USB ports belong to the radio.

---

## The one rule behind all of them

Every item on this page is the same rule wearing a different hat:

> **Changing what the station is made of is a decision, and it is not yours
> alone. Looking at the station is always yours.**

Read-only is free. Anything that installs, pulls, edits, deletes, or repins goes
through your fleet admin — not because you are not trusted, but because a fleet
only stays diagnosable while every station is what its manifest says it is.

And the habit that saves more time than any command here: **write down what you
changed and when.** "It started Tuesday, and on Monday I moved the GPS antenna"
is worth more than any output you can paste.
