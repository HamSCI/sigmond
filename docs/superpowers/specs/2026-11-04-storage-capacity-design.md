# Storage capacity: one ledger for every client's data

**Status:** design, 2026-11-04. Michael Hauan set the policy in conversation; calls the
designer made appear marked *(designer's call)*.

## Why

On 2026-11-03 four station VMs stopped. WB6CXC-7, K3LR, UACNJ and W2NAF each paused on a
disk I/O error when the Proxmox host's thin pool reached 100 %, and three of them sat dark for
seven to ten hours before anyone noticed. Nothing in the software knew the pool existed.

The chain ran like this. The v3.85 align took a `pre-v385` snapshot of each VM disk on 11-02,
with the disk already about 95 % allocated. hf-timestd's raw IQ ring then did what it always
does: it wrote some 3 GB an hour of new IQ and deleted the oldest. With the snapshot pinning
the old blocks, every rewrite took fresh pool space, and each pool ran out about 29 hours after
the align. hf-timestd's resource guardian saw none of it. It budgets against the guest's own
disk, which stood at 74 %. No VM disk had `discard` on, so space the guest freed never returned
to the pool, and no alarm watched the pool from the host.

The deeper fault: every client on a station writes to local disk on its own account, sizes its
holdings against `df`, and assumes the space belongs to it. A second client, a snapshot, a log
flood, or an engineer's test captures all draw from the same store without anyone keeping the
books. The station halts when the store runs dry.

## What this builds

A capacity ledger that every sigmond client works through, from one client alone on a
station to many sharing it. Each client registers the local sinks it writes to. sigmond
measures the capacity really available, divides it into allotments, and tells each client its
share. Each client keeps itself inside its share by trimming its own data, in the order its own
data deserves. When space runs short, sigmond raises the alarm loudly and early, tightens the
allotments in stages, and clears well-known clutter itself. If the shortage persists with no
operator directive, each client's declared default policy decides what it gives up.

## Goals

1. **No station halts on a full disk or a full pool.** Consumption never reaches 100 % of any
   store a station depends on, guest disk or host thin pool.
2. **Shares follow the station's real capacity.** That capacity grows when `smd archive add`
   attaches a drive and shrinks when anything outside the registered clients takes space.
3. **Each client manages its own data.** It trims inside its allotment, and nobody deletes a
   client's files on its behalf.
4. **The most irreplaceable data survives longest** (Michael's order, below).
5. **Pressure gets loud, early and specific.** Every alarm names who holds the space and what
   each client did about it.
6. **Prevention, not just cure.** Raised log levels expire. Snapshots don't linger. Known
   clutter gets cleared.

## Non-goals

- **Choosing what science to keep.** Which flare windows or events deserve archiving stays with
  the archiver's own rules (`tasks/plan-external-storage.md`). The ledger only makes sure the
  space exists.
- **Upstream delivery.** Uploads stay with each client and hs-uploader. The ledger only learns
  which data has been confirmed sent.
- **Network or cloud storage.** It covers local sinks and attached drives only.

## Anti-goals: it fails, even if it runs, if

- it deletes another client's files, or reaches into a client's directory to make room;
- it lets a station reach 100 % of any store, guest or host, because something it doesn't
  watch filled it;
- it alarms so often that operators learn to ignore it (the persistent-condition log-spam
  rule);
- a client that hasn't adopted it, or has crashed, can take the whole station down.

## The model

### Stores and capacity

A **store** means any place data lands: the VM's root filesystem, an archive drive mounted by
`smd archive add`, and, beneath the guest, the host's thin pool. sigmond measures each store's
true free space, including the host pool and any space snapshots pin there.

For each store sigmond computes the **allottable space**:

    allottable = true capacity
               − reserve for state
               − headroom (the slop that is never handed out)
               − space held by unregistered consumers

- The **reserve for state** covers configs, registrations, learned calibrations such as the T6
  chain delay, `authority_history.db`, upload cursors and keys: small, critical, and never
  offered for sharing.
- The **headroom** follows ZFS's slop space and ext4's reserved blocks. A copy-on-write store,
  thin pools included, needs free space even to delete, so the ledger never allots the last of
  it. *(Designer's call: the size, as a percentage with a floor in GB, set per store type.)*
- **Unregistered consumers** means everything no client claims: the journal, the apt cache,
  snapshots, an operator's files. The ledger measures them; it doesn't assume them away.

`smd archive add` raises a station's capacity by adding a store. `smd archive remove` lowers it.
The ledger re-divides the allotments when either happens.

### Registration and allotments

Each client declares, per instance, each sink it writes to: the store, the paths, its expected
rate, and a breakdown of its data into classes (below). This extends the `data_sinks`
declaration that the client contract already carries in §17.

sigmond divides each store's allottable space into **allotments**, one per registered client
on that store. Michael's default: **split evenly** among the clients until an operator sets
other shares. An operator may set shares per client. *(Designer's call: the client is the unit
of division, not the sink or the instance, so a client with six sinks gets one share, and it
divides that share among its own sinks and instances.)*

Each client keeps a **floor** it can always hold, enough to keep functioning: for hf-timestd,
perhaps an hour of raw IQ, so T6 and T3 keep running. Floors come first; the even split divides
what lies above them. *(Designer's call: each client declares its floor. sigmond checks the sum of
floors against the allottable space continuously, not just at registration, because
snapshots or an unregistered consumer can shrink that space at any time. When the floors no
longer fit, sigmond raises a critical alarm and moves straight to the final stage. The ledger
never demands a trim below a floor; a client's own final-stage policy may still go below it.)*

### Classes of data, and the order of sacrifice

Michael's order, most valuable first:

1. **Raw data (L0)** not yet confirmed upstream.
2. **Derived data** that analysis can recreate from raw data still on hand.
3. **Data already confirmed upstream**, which goes first of all.

Additions the conversation accepted:

- **Data an operator pins** outranks everything. Event windows, case studies and fault
  evidence can't be found by fixed rules, because their worth shows only afterwards.
- **A class can change.** A derived product whose raw source has already been deleted becomes
  the only record and rises to the rank of raw. Raw data confirmed upstream, or copied to the
  archive drive, falls to the bottom. Rank follows how replaceable the data remains, not its
  type alone.

Each client sorts its own holdings into these classes and reports the bytes in each. Only the
client can know which of its files have been uploaded, archived or derived. *(Designer's call:
the figures may be estimates, accurate to a few percent; the ledger uses them to choose whom to
ask, while each client's own trim decides what goes. Where hs-uploader holds the upload state,
it answers "confirmed sent" for the data it carried, and owns `sink.db` and its allotment.)*

### Who trims

**Each client trims inside its own allotment.** sigmond tells a client its new allotment;
the client deletes in its own sacrifice order until it fits, and reports back. sigmond never
deletes a client's files. (Android's split between expendable and protected data, and its
"ask for space" call, served as the model.)

A client may ask sigmond for room beyond its allotment, for a capture window an operator
requested, say. sigmond grants it from unused allotment, or refuses and says why.

### The ladder

Pressure gets met in stages, after Elasticsearch's disk watermarks and Ceph's full ratios. Each
store climbs separately. *(Designer's call: the levels below, as defaults; an operator may
change them. Every percentage counts used space against the store's true capacity, snapshot
space included, not against the allottable space. The headroom equals the space above the
final stage, 5 % by default, so the final stage fires before anything reaches the headroom.)*

| Stage | Trigger (default) | What happens |
|---|---|---|
| **Watch** | any store at 80 %, or a client over 90 % of its allotment | warn, naming the biggest holders |
| **Tighten** | 85 % | lower allotments toward floors; clients trim expendable classes; sigmond clears known clutter |
| **Demand** | 90 % | clients must report back within a deadline that they fit; a client that doesn't gets named in a loud alarm |
| **Final** | 95 % | no operator directive in force → each client applies its own declared default policy |

**The final stage belongs to the clients** (Michael). Each client declares in advance what it
does when space stays short after trimming everything expendable, because only it knows its
kind of data. hf-timestd might give up its oldest unsent raw IQ to keep the live timing stream.
A CODAR capture loop might simply stop capturing. An upload spool might refuse new rows. An
operator's directive, when one exists, overrides the default.

**The one lever sigmond holds** (Michael). A client that has crashed, hung, or predates the
contract can't trim. At the final stage only, when such a client fails to answer a trim request
by its deadline, sigmond stops that client's service. Stopping halts its writing and deletes
nothing; a loud alarm names the client and says why. sigmond never does this on a station an
operator has marked hands-off (a live experiment, say); there it alarms only.

Alarms run loud at every stage: the journal, sigmond's status and the heartbeat to the
fleetboard. They follow the persistent-condition rule: once per episode and once on clearing,
plus a periodic reminder while it lasts, never a line per check.

### Clean-up sigmond does itself

Michael: "Reasonable clean-up, clear-out should occur." sigmond clears well-known outside
consumers under fixed rules, starting at the Tighten stage:

- the systemd journal, down to a set size (`journalctl --vacuum-size`);
- the apt package cache;
- expired snapshots on the host that sigmond's own tools took (see below);
- stale temporary mounts and files that sigmond itself left behind, such as an aborted image
  build's loop mount.

It never touches a registered client's paths or an operator's home directory. Anything else
large and unclaimed gets reported, by path and size, for an operator to decide.

### Prevention

- **Raised log levels expire.** §11 of the client contract lets sigmond set a client's log
  level at runtime. A raised level now carries an expiry, and the client falls back to its
  routine level when it passes, as network gear does with timed debugging. The ledger counts
  log volume like any other consumer.
- **Snapshots carry a lifetime.** An align or upgrade snapshot gets removed once the change
  checks out, or after a set age *(designer's call: 48 h)*, and sigmond refuses to take one
  without enough pool headroom to carry it. The `pristine` first-boot snapshot costs little,
  since it pins only the small original image, and may stay.
  **Who deletes** (Michael): a host-side component deletes, by itself, only the snapshots
  sigmond's own tools took and labelled as theirs, once past their lifetime. A snapshot an
  operator took never gets deleted automatically; sigmond alarms about its age and its cost
  in the pool, and an operator decides.
- **Space freed in the guest returns to the pool.** VM disks get `discard=on`, and the guest
  runs a scheduled `fstrim`. On thin pools fstrim can stall I/O (Rob saw a radiod lose 17 s on
  KFS-SE), so it runs with the radiods stopped, or not at all on a busy station.
  *(Designer's call: weekly, in a short window with radiods stopped; skipped when a
  capture or an operator pin is active.)*
- **The host pool gets watched from the host.** The Proxmox host reports its pool percentage,
  the snapshots in it, and each VM's allocation, through the heartbeat.
  **How far the ledger reaches** (Michael): the ledger runs in the guest. A small reporter on
  the host only reports, and raises its own alarm when the pool climbs. The guest counts its
  usable space as the lesser of its own free disk and its share of the pool's headroom, so a
  filling pool shrinks every guest's allottable space. No host-side ladder divides a shared
  pool among VMs (KFS-OMNI's host carries two); an operator sets those shares. A host report
  that goes stale or missing counts as a fault and raises an alarm, never as "unknown".

## The client contract revision

This goes into `docs/CLIENT-CONTRACT.md` as the next version. In outline:

- **§17, extended.** Each `data_sinks` entry of kind `file` adds: the store it lives on, its
  data classes with their current sizes, its floor in bytes, and its final-stage default
  policy, chosen from a small set (give up the oldest of a named class; stop writing; refuse
  new rows; or a client-specific named policy it describes).
- **A trim call.** Each client answers "fit in N bytes" for a sink: it trims in its own order
  and reports the bytes it now holds, by class. It may refuse below its floor, and says so.
  *(Designer's call: CLI verbs beside `inventory`, because no client serves a §13 socket yet.)*
- **A holdings report.** Each client reports its bytes per class per sink on request, and
  sigmond also reads them from the self-describe inventory.
- **§11, extended.** A runtime log level carries an expiry time. Sigmond restores the
  routine level and sends SIGHUP.
- **Backward compatibility.** A client that hasn't adopted the revision still appears in
  the ledger: sigmond measures its paths from its existing `disk_writes` or `data_sinks`
  declaration and counts them against its allotment. It can't be asked to trim, so it gets
  reported loudly as unmanaged whenever it runs over. At the final stage it gets stopped like
  any client that fails to answer a trim request. That keeps the anti-goal: an old client
  can't take the station down unnoticed, and nobody deletes its files for it.

## Existing pieces to fold in

*(Designer's call on the hand-over: hf-timestd's guardian keeps its own 95 %-of-`df` eviction
during migration, as a last-resort net only. Once hf-timestd takes allotments, that net moves to
98 %, above the ledger's final stage, so the two never act on the same numbers.)*

| Piece | Today | Under the ledger |
|---|---|---|
| hf-timestd `resource_guardian.py` / `quota_manager.py` | budgets the raw ring against guest `df`; evicts oldest whole days at 95 % | becomes hf-timestd's side of the contract: reports its classes, trims to the allotment sigmond gives it |
| sigmond `storage_trim.py` | TTL delete on the shared `sink.db` queue | `sink.db` gets an owner and an allotment; rows confirmed sent count as the lowest class |
| `smd archive add` + the planned archiver | archive drive at `/var/lib/sigmond/archive`, its own 90 % high-water mark | the drive becomes a store; its high-water mark becomes that store's headroom |
| `timestd-prune.timer` | separate retention pruning | folded into hf-timestd's trim, driven by its allotment |
| §17 `data_sinks` with `retention_days`, `mb_per_day` | declared, not enforced | the registration the ledger works from |

## Decided by Michael

- Raw data first, then recoverable derived data, then data confirmed upstream.
- Shares split evenly until an operator intervenes.
- Each client trims inside its own allotment.
- Reasonable clean-up and clear-out by sigmond itself; verbose logging returns to routine
  after an investigation.
- Loud alarms when allotted capacity runs past a set level; absent an operator directive, a
  default policy per client, decided by the client for its own kind of data.
- `smd archive add` raises the allottable space; consumers outside the clients lower it.
- The work probably needs a revision of the client contract.
- The ledger runs in the guest; the host only reports its pool. A stale or missing host
  report raises an alarm.
- Host snapshots that sigmond's tools took get deleted automatically past their lifetime;
  an operator's snapshots get only an alarm.
- At the final stage, sigmond stops the service of a client that fails to answer a trim
  request, except on a station marked hands-off.

## Operator surface and rollout *(designer's calls)*

- Shares, directives and stage levels live in `/etc/sigmond/storage.toml`; `smd storage
  status|share|directive` reads and sets them, and the TUI shows the same.
- The hands-off mark lives there too (`smd storage directive hands-off`), with an expiry like a
  raised log level, so a finished experiment can't leave a station unprotected for good.
- Pins come from `smd archive keep` (already planned): a pin names a time window and,
  optionally, clients. Each client learns the pins from the ledger and protects whatever of
  its own data falls inside the window.
- The contract revision lands first. The trim call and the holdings report start as SHOULD,
  so existing clients keep conforming, and become MUST one release later. hf-timestd migrates
  first, since its raw ring makes up most of every station's data.
- The scheduled `fstrim` waits for a second phase, after the ledger, alarms and snapshot
  lifetimes have run in the fleet.

## Left to the builder

- Where the ledger runs (a sigmond service, a timer, or both) and how it stores its state.
- The exact message format between sigmond and clients, within the §13 control surface.
- How the host reports its pool to the guest (heartbeat field, a small agent, or a periodic
  `smd` call from the host side).
- How each existing client gets migrated, and in what order. hf-timestd matters most: its raw
  ring makes up most of every station's data.
- The tests. The 2026-11-03 sequence (snapshot, steady rewrite, pool to 100 %) makes a natural
  end-to-end case.
