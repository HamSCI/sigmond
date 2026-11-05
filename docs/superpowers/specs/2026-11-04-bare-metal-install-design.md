# A station from the repos, on a machine someone already owns — design

**Status:** draft for Michael Hauan (AC0G), 2026-11-04.  Test beds: sigma and AC0G-B2, beside B4.

## 1. Why

Most hams who might run a sigmond station already have a Linux machine.  Many will not wipe it to
flash our USB image, and some cannot.  They should be able to clone sigmond, follow the README, and
end with a working station: radiod on the hardware they have, the clients they chose, uploads
going where they should.  No Proxmox host, no golden VM, no one from the project at their elbow.

Until now only the image path got that kind of testing.  On 2026-11-04 two of Michael's own
machines took the README path from a clean start.  Neither ended with a working station.  Each
failure came from sigmond, not from the machine, and each would meet a ham the same way.  This
note gathers what the two runs found, decides how sigmond should behave instead, and sets the
order of the work.

The two machines:

- **sigma** (192.168.1.174): bare-metal Debian 13 on an Intel i5-1340P.  That part mixes four
  performance cores, two threads each (CPUs 0-7, 4.6 GHz), with eight single-thread efficiency
  cores (CPUs 8-15, 3.4 GHz, sharing L2 by fours).  One RX888, one RM3100 magnetometer.  Profile
  `dasi2`: a whole station on one box.
- **AC0G-B2** (192.168.1.60): bare-metal Debian 13 on a Ryzen 5700U, no SDR.  It decodes for the
  two radiods on AC0G-B1 over the LAN.  Profile `client`.

Before each run the old install went into `/var/attic/sigmond-20261104*` on the machine, so
the README started from a machine with no sigmond on it.

## 2. Goals, non-goals, anti-goals

Goals:

1. A ham follows the README, without `sudo` and without editing a file, and gets a station that
   records.  Bring-up ends COMPLETE, or names the one thing that stopped it.
2. The same works for a client-only machine that decodes for radiods elsewhere on the LAN.
3. sigmond reads the machine it lands on (core types, cache layout, USB controllers, which radiods
   answer on the network) and acts on what it finds.  It never assumes the shape of B4.
4. The image path keeps working as it does today.

Non-goals:

- Distributions other than Debian 12/13 and Ubuntu 22.04+, which the README already names.
- Changing a ham's BIOS, or anything outside the operating system.

Anti-goals.  The work fails, even if bring-up says COMPLETE, if:

- radiod runs on a core that cannot keep up with it, and nothing says so;
- a client starts against a radiod name it was never given, or a template placeholder;
- a short outage (a radiod restarting, a router rebooting) leaves a client dead until a person
  notices;
- bring-up changes the machine more than the ham agreed to (section 6).

## 3. What the two runs found

Fixed and pushed already (sigmond 8293c65, sigmond-rac a01c464):

| # | Finding | Fix |
|---|---|---|
| F1 | `install.sh` cloned every repo one commit deep.  Bring-up installs with `--no-pull`, so a pinned commit behind upstream HEAD could not be built.  sigma got no radiod: ka9q-radio's pin 401992cd sits behind Phil's 11-02 commit. | Full clones. |
| F2 | Bring-up ran 20 minutes past the failed radiod install, starting clients against nothing. | A hard `radiod installed` checkpoint checks the radiod binary and rx888.so. |
| F3 | The README said `sudo smd …` (smd refuses sudo), gave a wrong path to the topology example, and never named `smd bringup`.  The install banner named two commands that do not exist. | README leads with `smd bringup`; commands corrected. |
| F4 | The RAC installer read `coordination.env`, which a fresh machine does not have yet, and `pipefail` ended it with no reason printed. | It returns empty when the file is missing. |

Open, and the subject of this note:

| # | Finding | Machine |
|---|---|---|
| F5 | Placement put radiod on efficiency core 15 alone.  It lost a full second of samples every second ("USB xfer failures +0") and quit after 5 s, 64 times over. | sigma |
| F6 | The FFT wisdom planner skips itself whenever `/etc/fftw/wisdomf` exists.  An old file from June, planned for other sizes, kept it from running; radiod then started with no wisdom for its 3,240,000-point FFT. | sigma |
| F7 | `smd apply` runs in stage 1 and sets up the uploader before bring-up has installed hs-uploader, so the step fails, and the receive-buffer tuning fails with it. | sigma |
| F8 | mag-recorder's install lays down a template config.  Bring-up took that file for a configured one, skipped configuring it, and the magnetometer never started. | sigma |
| F9 | radiod's RX888 driver trips a libusb assertion while shutting down, so every quit becomes a core dump.  Phil's code, not ours. | sigma |
| F10 | `--remote-radiod` reaches only a note in the plan.  Nothing records it, so hf-timestd keeps `<YOUR_RADIOD_STATUS>` and the recorders keep the template's `my-rx888-status.local`.  The `client` profile has likely never worked end to end. | B2 |
| F11 | Bring-up takes one remote radiod.  A second takes `instance add`, `sources add`, `sources apply` and a restart, none of which the README mentions. | B2 |
| F12 | A client-only machine gets no receive-buffer tuning (`rmem_max` stays at 208 KB against a 16 MB floor), because that step belongs to radiod hosts. | B2 |
| F13 | wspr-recorder and psk-recorder try their radiod's name once, for 5 s, then quit; systemd stops restarting them after a burst.  B2's recorders sat dead from 10-15 and again on 11-04. | B2 |
| F14 | A new instance's source file holds `selections = []`, while the reader looks for `selected`.  Harmless while empty. | B2 |
| F15 | The ka9q-python version check warns about a ka9q-radio checkout that a client host never builds. | B2 |
| F16 | Bring-up wrote `isolcpus=15` into `/etc/default/grub.d/` on sigma and asked for a reboot.  On a ham's own machine that changes how their kernel boots. | sigma |

Two more things worth knowing, though they need no work.  sigma's July hf-timestd had held its
clock 27.7 minutes fast, which the 10-22 change (chrony takes nothing from fusion) already ended.
And the old B2 had never uploaded anything: its uploads had been held on purpose since 09-30.

## 4. How sigmond should behave

### 4.1 Cores (F5, F16)

radiod runs on a performance core with both its threads.  On a hybrid Intel part the kernel lists
each core type's CPUs in `/sys/devices/cpu_core/cpus` and `/sys/devices/cpu_atom/cpus`; placement
reads them and never offers radiod an efficiency core.  Where those files do not exist (AMD, older
Intel, most VMs), nothing changes.  The xhci interrupts keep to a different performance core, the
lowest one radiod leaves free, as `sigmond-rx888-irq-affinity` already arranges.

Done in the working tree, not committed: `get_performance_cpus()` and a first filter in
`eligible_radiod_cores()` (lib/sigmond/cpu.py), with six tests.  On sigma it gives radiod {6,7}
and the interrupts {0,1}.

On a split-L3 part (the 5700U), each radiod keeps to one L3 half, with its controller's
interrupts beside it (sigmond ef2998d).

When no performance core remains free for a radiod, placement never falls back to an efficiency
core on its own.  Bring-up stops with the reason, as `smd apply` already refuses more radiods than
cores; `[cpu_affinity] radiod_cpus` in topology.toml lets an operator choose otherwise, knowingly.
*(Builder's call, 2026-11-04.)*

Placement must work well without kernel isolation.  On a machine installed from the repos,
bring-up prints the `isolcpus`/`nohz_full` change it would make and leaves GRUB alone unless the
operator asks for it; the image keeps writing its drop-in as now.  *(Builder's call; Michael may
overrule — section 6.)*

### 4.2 FFT wisdom (F6)

The planner runs when no wisdom covers the FFT this radiod will ask for, not merely when no file
exists.  It runs on the cores radiod will use, after placement, since FFTW times its plans on the
core it runs on.  Bring-up waits for it before starting radiod, as it already means to.

### 4.3 Order of steps (F7, F8, F12)

- Host tuning that every station needs, receive buffers among it, runs on every host, client or
  radiod, and runs before the uploader matters.
- The uploader manifest step runs after hs-uploader exists.  Before that it has nothing to set up,
  and saying so counts as success, not failure.
- "Configured" means the client says so.  A template counts as unconfigured for every client, not
  just those whose template carries the one sentinel the probe knows.  The clients' own
  `validate --json` gives the honest answer, and the probe should ask it.

### 4.4 Sources come from discovery (F10, F11, F14)

Michael's rule: a client instance presumes we know what sources exist, and sigmond must discover
them, never take them hard-coded.

On a client host, bring-up browses the LAN for radiods (`_ka9q-ctl._udp`, which `smd admin
sources list` already sees) and lists what answers.  Each radiod found becomes a source.  For each
source the operator keeps, bring-up:

1. records the radiod in coordination, keyed by its status name, with the host it lives on;
2. creates one instance of each radiod-bound client, named for that radiod;
3. writes the source into the instance and renders it into the client's config before the client
   first starts.

Which radiods bring-up keeps when nobody names them (Michael, 2026-11-05): those whose
advertisement carries this station's callsign.  B1's radiods advertise "AC0G @EM38ww B1-a
longwire", so B2 keeps both, while sigma's and B4's radiods, or a friend's on a ham's LAN, appear
in the list but stay unused.  sigmond never adopts, or uploads under, someone else's receiver by
accident.  `--remote-radiod <name>` names radiods outright, may repeat, and overrides the callsign
rule.  A name given that does not answer is an error before anything installs, not a client that
fails later.

Each kept radiod's instances take a reporter ID derived from the radiod (Michael, 2026-11-05):
the callsign plus the radiod's short name from its status name, so `ac0g-b1-a-status.local`
gives AC0G/B1A.  A non-interactive bring-up can then finish unaided, and `smd admin instance`
renames one later.  A host with a single radiod keeps using `--reporter` as now.

hf-timestd has no per-radiod instances.  On a host that keeps more than one radiod it binds the
one the operator names (`--timing-radiod <name>`); with none named it installs but stays idle,
and bring-up says so plainly (Michael, 2026-11-05).

A radiod host does the same for its own radiods: `configure radiod` already records them, and the
clients take their source from that record, not from the template.

The source file gets one key, `selected`, written and read alike.

One record holds each instance's binding: its source file under `/etc/sigmond/clients/`.
Coordination's `[[clients.X]] radiod_id` and the env file derive from it.  Each radiod-bound
client gets a renderer that writes its sources into that instance's own config.  Today only
wspr-recorder has one, and it writes the shared `config.toml`, not the instance's file; psk-recorder
and meteor-scatter need theirs.  `SIGMOND_RADIOD_STATUS` stays for hosts with exactly one radiod.
*(Builder's call, 2026-11-04.)*

### 4.5 Clients survive an outage (F13)

A radiod-bound client that cannot resolve or reach its radiod keeps trying, with a growing delay
up to a minute, and logs once per episode rather than once per try.  It reports itself not ready,
so `smd status` shows the wait, but it does not exit.  This changes wspr-recorder and psk-recorder
(and any other client that quits the same way); the contract should say it, so new clients do the
same.

### 4.6 Smaller items

- F15: the ka9q-python check looks at the ka9q-radio a host actually builds.  A client host builds
  none, so it says nothing.
- F9: reported to Phil with the backtrace from sigma.

## 5. Proving it

The test of this work is the README itself.  For each fix: a unit test that fails without it, and
then both machines rebuilt from a clean start, by the README, with no hand steps.

- sigma ends COMPLETE: radiod on a performance core, no sample-loss steps over an hour, wisdom
  planned, WSPR, FT8, meteor scatter, hf-timestd and the magnetometer all recording.
- B2 ends COMPLETE with both B1 radiods found by discovery (and sigma's and B4's listed but
  unused), four recorders decoding as AC0G/B1A and AC0G/B1B, hf-timestd bound to the radiod
  named, and uploads held.  Restarting a B1 radiod for five minutes leaves B2's recorders running and
  recording again afterwards, with nobody touching B2.
- B4 and one image-built station run the changed code for a day with no change in behaviour.

## 6. Decisions for Michael

1. **Kernel boot options on a ham's machine (F16).**  Settled for now as the builder's call in
   4.1: print the change, leave GRUB alone unless asked.  Michael may overrule.
2. ~~Instance names on a client host~~ — decided: derived from the radiod (4.4).
3. ~~Uploads after a fresh install~~ — decided (Michael, 2026-11-05): a first install from the
   repos stores but sends nothing until bring-up's final validate passes and the operator lets
   uploads go (`smd sink upload`, or a closing prompt from bring-up).  No bench spots and no
   spots under a wrong call go out by surprise.  The image keeps its own behaviour.
4. ~~Which radiods discovery keeps~~ — decided: the station's own callsign (4.4).
5. ~~hf-timestd with several radiods~~ — decided: the one named, else idle (4.4).

## 7. Order of work

1. Cores (4.1, in hand) and the order of steps (4.3), then rebuild sigma.
2. FFT wisdom (4.2), on sigma's settled placement.
3. Sources by discovery (4.4), then rebuild B2.
4. Clients surviving an outage (4.5), in the client repos, with the contract text.
5. The smaller items, the README's client-host section, and the final rebuild of both machines.

## 8. Left to the builder

- How discovery presents its list in an interactive bring-up.
- How the planner decides that existing wisdom covers radiod's FFT: read the wisdom, or try an
  import and time a plan.
- The retry schedule's exact numbers, within "up to a minute".
