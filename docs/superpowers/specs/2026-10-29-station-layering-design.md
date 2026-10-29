# Station layering: a stable host, an independent sigmond, autonomous clients

Design, 2026-10-29.  Status: draft for Michael's approval.  Rob's three
questions (§10) remain open; they block steps 6 and 8 only.

Related:
- `2026-09-24-smd-align-design.md` (in-place alignment, pm-align)
- `sigmond-appliance/docs/superpowers/specs/2026-09-24-station-identity-design.md`
- `docs/CLIENT-CONTRACT.md` (to be revised under §6)
- Surveys behind this design, 2026-10-29: the host/VM boundary and client
  independence.  Their findings appear here where they matter.

## 1. Why

Once a Proxmox host stands installed at a site, it should stay there for years.
It should anchor everything else: from the host we manage the VM, update it,
and when necessary replace it, without anyone carrying a USB stick to the site.
A reflash should answer only new hardware, a dead disk, or a host broken beyond
repair.

We stand some way off.  On 2026-10-28 AC0G-ND's update stopped partway because
sigmond held knowledge that belonged to a client: how the recorders' decoders
get built.  The host refreshes about twenty of its own files and leaves many
more, along with all of its boot, network and VM settings, to a reflash.  No
tool replaces a VM.  The recorders cannot store or upload a spot without
sigmond, and the VM assumes a host it never checks for.

Each of these problems has the same shape.  A layer reaches up into the layer
above it, or holds knowledge that belongs below it.  This design fixes the
direction of every dependency and then defines what each layer offers the next.

## 2. Goals, non-goals, anti-goals

Goals:
- The host acts as a stable anchor.  Its own code and settings update in place,
  at most with a host reboot.  It can replace its VM without a download from
  the site's side beyond the release itself.
- sigmond runs without our Proxmox host: on bare metal, or in a VM under
  another hypervisor, wherever the hardware supports it.
- Every client runs without sigmond: it records, stores, uploads to its
  configured destinations, and serves its own page.
- Each layer holds only its own knowledge.  Host knowledge stays in the host,
  sigmond knowledge in sigmond, client knowledge in each client.
- An update of one client changes no other client.

Non-goals:
- We do not promise or support stand-alone client installs as a product until
  someone needs them.  The rule holds anyway, because it makes updates reliable.
- We do not redesign the RAC gateways, frps or the fleet board here.
- We do not move Proxmox major upgrades out of the reflash class.

Anti-goals, failures even if everything else works:
- Client code that branches on whether sigmond exists.  Two behaviours means
  one goes untested and rots.
- A sigmond that decides what to do by asking "am I in a VM?"
- A host that knows anything about clients.
- A station that becomes unreachable because a layer assumed another layer
  would provide remote access.

## 3. The layering rule

Dependencies point down only.

| Layer | Members | May depend on |
|---|---|---|
| Host (appliance) | the Proxmox host, its wizard, pm-align, host tunnel, relays, console relay, heartbeat | nothing above it |
| sigmond | smd, station-web, sigmond-rac | clients' published declarations, data products, status and docs; the host's offers when present |
| Clients | wspr-recorder, psk-recorder, meteor-scatter, hf-timestd, mag-recorder, codar-sounder, superdarn-sounder, hf-tec, hfdl-recorder, hamsci-physics (as post-processor) | libraries, radiod, hs-uploader's service |
| Libraries | ka9q-python, hamsci-dsp, hs-uploader, callhash, hamsci-physics (as library) | each other only |

radiod (ka9q-radio) sits beside the clients as their radio source.  A client
depends on a radiod; it does not care who installed it.

Each layer detects a capability, never an identity.  A client reads optional
configuration and uses it when present.  sigmond asks "does a host offer
remote access?", never "am I in a VM?"

### 3.1 hamsci-physics

It plays two roles, and the code already uses it both ways.

- A library: solar geometry and ionospheric data fetching.  hf-timestd and
  station-web import it, as they import hamsci-dsp.
- A post-processor: a client whose input comes from other clients' data
  products rather than from radiod.  It turns hf-timestd's measurements into
  the science products and uploads GRAPE through hs-uploader.

As a client it needs its own install, which it lacks today, and a written list
of the data products it reads.  The producing client and the shared schemas in
hamsci-dsp define those products, not the paths where files happen to sit.

### 3.2 station-web

It belongs to sigmond's layer: a read-only page that shows a station what goes
on in it, and the documentation.  It may know sigmond.  It keeps its own
repository but stays outside the client contract.

It reads clients only through what they publish: data products, status, and
the documentation each client ships.  Its science pages display client data
products and compute nothing a client should own.

## 4. The host–VM contract

The host offers services to the VM.  The VM uses each one when the host
announces it, and otherwise does without it or provides it itself.  Every
offer needs a reason the host, and not the VM, should provide it.

| Offer | Why the host | Without it, the VM (or bare metal) |
|---|---|---|
| Recovery: snapshots, VM replacement, a tunnel to the host itself | only something outside the VM survives the VM's death | has none; a dead machine needs a site visit, as for any bare-metal station |
| Hardware isolation: USB controller passthrough, CPU pinning, cache partition, vCPU topology | radiod needs cores and cache no other process touches; only the hypervisor can fence the VM's neighbours | uses its own devices directly and applies its own CPU isolation (guest isolcpus, affinity) |
| Network: the /30 link, NAT, DNS at 10.99.0.1 | keeps the VM's address stable whatever the site's LAN does, and puts the host's firewall in front | takes an address the ordinary way (DHCP or static) and uses the site's resolver |
| Port exposure: relays to the LAN, IPv6 proxy, forwarding for remote access | the VM sits behind NAT, so only the host can reach it from outside | listens on the LAN directly; remote access per §5 |
| Remote access tunnel (frpc to the gateways) | the host must stay reachable when the VM fails | runs its own tunnel through sigmond-rac (§5) |
| Split console | the host owns the screen and has no keyboard of its own | uses its own console |
| Guest agent channel | lets the host act inside the VM without the network | not needed |
| Site facts the host learned: LAN routes (`site-lan`), the wizard's answers | the host saw the site first, at install | discovers them itself, or the operator configures them |
| Identity escrow: a copy of the VM's keys and site settings | a replacement VM must inherit them | keeps them on its own disk, as any machine does |

### 4.1 Rules

- The host announces its offers in one file it pushes into the VM, with a
  contract version number.  The VM reads it at boot and at every align, and
  says plainly when the host offers less than the VM's code expects.  How the
  host announces itself remains an open question for Rob (§10).
- The VM never takes provenance from the host.  Today the wizard copies the
  host's install-time `version` and `manifest.txt` into the VM, so a newer VM
  would claim the host's old release.  The VM records its own.
- The VM holds its own identity.  The host keeps an escrow copy only so a
  replacement can inherit it.  Today the VM expects a `station.toml` "written
  by the PM" for its PSWS ids, and nothing writes it; that file moves into the
  escrow.
- Nothing the VM needs to run its clients comes only from the host.  The VM
  image must also boot on bare metal and find its network and devices.

## 5. Remote access and port exposure

Remote access remains a definite need on every station, appliance or bare
metal, as does reaching the web pages of the clients that run.

- Each client declares the ports it serves, in its own declaration file:
  "HTTP on 8000, named station-web".  The client knows nothing about tunnels.
- sigmond-rac collects the declarations of the clients that run, and keeps
  the list current as clients start and stop.
- When a host offers remote access, sigmond-rac hands the host that list.  The
  host forwards those ports through its own tunnel, as today's relays do.
- When no host offers it, sigmond-rac runs its own tunnel from the machine,
  with the same list.
- The host keeps its own tunnel in every case, for its own console and for
  recovery.

Whether the VM's own tunnel, when it needs one, shares the host's gateway
registration or registers separately remains an open question for Rob (§10).

## 6. Clients: autonomous all the time

A client defines everything it needs to run.  sigmond configures a client; it
never supplies code to it.

### 6.1 What a client owns

- Its environment.  Its lock file pins exact library commits, and its venv
  holds its own copy of every library.  A library update reaches a client only
  when that client moves its lock.  This reverses the "one source tree, many
  editable consumers" pattern in sigmond's CLAUDE.md, deliberately: shared
  editable libraries mean one checkout move changes every client at once.
  The cost: a library fix reaches each client through a small lock commit per
  client.  Tooling should automate those commits.
- Its native helpers.  wspr-recorder and psk-recorder build their own decoders
  (wsprd, jt9, decode_ft8) into their own install trees.  mag-recorder builds
  mag-usb.  sigmond builds none of them.
- Its data, its uploads and its destinations.  Clients write through
  `hs_uploader.sink` directly, store where hs-uploader's configuration says,
  and upload through hs-uploader's service to the destinations the client's own
  configuration names.  Today clients import the writer as
  `sigmond.hamsci_sink`, a compatibility name left from v3.70, and the writer
  stores to `/var/lib/sigmond/sink.db` and does nothing when that directory
  does not exist.  Both go.  The upload-wake socket the recorders signal,
  `/var/lib/sigmond/upload-wake.sock`, moves to hs-uploader's own directory
  for the same reason.  codar-sounder, hf-tec and superdarn-sounder
  declare hs-uploader rather than borrowing sigmond's bundled copy.
- Its units.  A unit names only the client's own directories as required.
  Anything sigmond may add carries systemd's optional `-` marker.
- Its declarations: ports (§5), data products, status, docs, install and build
  steps, and what an update of each kind requires.

### 6.2 What sigmond adds

Configuration only: an environment file, systemd drop-ins (CPU affinity, for
one), the station's identity and location, and ordering among services (start
after radiod; restart consumers after radiod restarts).  When none of it
exists, the client runs on its own defaults.

### 6.3 The contract document

`CLIENT-CONTRACT.md` disagrees with the code in several places, and leaves
out several things clients actually declare.  It gets revised from the code
and this design, and then each client gets checked against it.  The revision
names every special case `bin/smd` holds by client name and moves each into
the client's declarations.

### 6.4 Uploads without sigmond (calls made in drafting)

These settle questions the builder check raised.  Each marks my call, for
Michael to check.

- My call: the client owns its upload definition.  hs-uploader already loads
  one file per client from `pipelines.d/<client>.toml`.  A client's install
  writes its own file there, naming its destinations; today sigmond renders
  those entries into one manifest from each client's `[[hs_uploader.pipeline]]`
  blocks.  hs-uploader's service starts with an empty legacy manifest, so a
  station without sigmond still uploads.
- My call: the station's identity (call, grid, PSWS ids) lives in each
  client's own configuration, where the client reads it.  sigmond, when
  present, writes those values into every client's configuration and keeps
  them current; the 10-27 finding that five client configs on ND held B4's
  grid shows that job already falls to sigmond and needs doing reliably.
- My call: hs-uploader owns the sink schema as a versioned interface.  Writers
  only add columns; the daemon reads every older schema.  A client moving its
  lock to a newer `hs_uploader.sink` therefore breaks neither other clients
  nor the daemon.  hs-uploader's service follows its own lock.  The plan adds
  mixed-version tests.
- My call: no live station moves its sink.  The upload cursors key on the sink
  path, so moving `/var/lib/sigmond/sink.db` would re-key them and could send
  rows twice or strand them.  Existing stations keep that path, now named
  explicitly in hs-uploader's configuration rather than assumed; the socket
  likewise.  Only a fresh install takes hs-uploader's own default.  Clients
  keep accepting the old import name for a release while they move.
- My call: booting the VM image on bare metal stays a property we test, not a
  second image we ship.  Michael asked that such a boot have a good chance of
  running where the hardware allows; a test on one rig machine measures that.

## 7. The host as anchor

### 7.1 Updating the host in place

- pm-align refreshes every file firstboot and the wizard write on the host,
  derived from those scripts rather than from a hand list.  It restarts what it
  changes.
- pm-align also refreshes the install-time sigmond tree on the host.  Today
  every full wizard run, including `sigmond-setup --reconfigure`, copies
  pm-align out of that never-refreshed tree and downgrades an aligned host.
- For boot, tuning, network and VM settings, pm-align compares the host with
  what the release would write and reports each difference.  An explicit flag
  applies them; a host reboot follows.  Without the flag nothing changes, but
  pm-align never again stays silent about a difference.
- The host keeps the wizard's answers, so a reconfigure no longer asks every
  question again.

### 7.2 Replacing the VM

- At install the host keeps the golden VM disk, compressed, or it can fetch a
  release's VM disk alone.
- The host records the VM's settings it applied (passthrough, cores, affinity,
  hookscript, arguments), so a new VM receives them.  Rolling back to today's
  `pristine` snapshot would drop them.
- Identity escrow (§4.1) and the restore half of the station-identity design
  carry the keys, PSWS ids and site settings across.
- Learned state the station paid for, such as T6's chain delay and the timing
  history, carries across when the hardware stays the same, which a
  replacement on the same host guarantees.
- Upload queues drain or copy before the old VM goes.

This part deserves its own design once this one stands approved.

## 8. What then needs a reflash

| Change | Under this design |
|---|---|
| Client and library code | in place; the client's own update |
| Native builds | in place; the owning client rebuilds |
| sigmond, station-web, sigmond-rac | in place |
| VM base OS or layout | VM replacement from the host |
| Host files and units | in place; pm-align |
| Host boot, tuning, network, VM settings | in place with an explicit flag, then a reboot |
| Proxmox minor updates | apt, then a reboot |
| Proxmox major upgrade, new hardware, a dead disk | reflash |

## 9. How we measure progress

- A client test: install each client into a clean Debian container with no
  sigmond and no host, start it, and feed it a recorded stream.  It must store
  and queue uploads.  Each failure names an arrow pointing the wrong way.
- A bare-metal test: boot the VM's image on a machine with no Proxmox, and
  check that sigmond, its clients and its own tunnel come up.
- A host contract test: on the rig, remove each host offer in turn and check
  that the VM reports the loss plainly.
- An update rehearsal from v3.69, the oldest release that must still update
  in place (§10), as well as from the last release.  Beyond v3.69, VM
  replacement takes over.

## 10. Open questions

For Rob:
1. How the host announces its offers to the VM: a pushed file, an endpoint on
   10.99.0.1, or the guest agent.
2. Whether a VM's own tunnel shares the host's gateway registration or
   registers separately.
3. Whether anyone, among wsprdaemon users for instance, wants stand-alone
   clients now.  The answer changes priority, not the design.

Settled by Michael, 2026-10-29:
4. Per-client library pins (§6.1) come after the first moves, with the
   contract revision (step 5).  Until then clients keep sharing editable
   libraries, and step 1 changes only imports and paths.
5. A station as old as v3.69 must still update in place.  The rig rehearses
   updates from v3.69 as well as from the last release.  An older station
   goes to VM replacement.
6. hamsci-physics uploads GRAPE under the instrument's PSWS id.  PSWS ids
   belong to the station's instruments, held in station identity; whichever
   program uploads an instrument's data uses its id.

## 11. Order of work

Small, each useful alone:
1. Clients import `hs_uploader.sink` directly; the storage location moves to
   hs-uploader's configuration; units mark sigmond's directories optional.
2. Fix the wizard's pm-align downgrade, and stop the wizard stamping host
   provenance into the VM.
3. The client container test, run against every client, to measure the gaps.

Larger, each with its own plan:
4. The decoder and mag-usb builds move into their clients.
5. The revised client contract, and per-client library pins.
6. The host–VM contract file and the VM's check of it.
7. pm-align's full coverage, and tuning changes under a flag.
8. Port declarations and sigmond-rac's own tunnel.
9. VM replacement on a standing host.

## 12. Left to the builder

File formats and names for the declarations and the host's offer file; the
exact mechanism sigmond-rac uses to follow client starts and stops; how the
container test feeds a recorded stream; the compression and storage of the
retained golden disk.
