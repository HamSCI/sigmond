# Station adoption — install lays the groundwork, the operator adopts

Design, 2026-09-02. Michael and mjh's Claude instance.

Scope: the **VM side**. PM provisioning — RAC registrar selection, ssh access to
both PM and VM, pushing the station manifest — is a separate spec. §5 defines
the file the two agree on.

## 1 · Why

Michael, 2026-09-02:

> An installation will, in effect, lay the groundwork for any installation,
> including DASI2, without activating anything at the beginning but
> incrementally adding hardware and clients as the user acquires or desires
> them.

Two situations force it, and today neither works:

**Fargo.** The Beelink installs with NOTHING attached — no GPSDO, no RX888, no
magnetometer. Then it reboots, and a uhubctl-capable hub, an RX888 and a Bodnar
miniGPS get plugged in. There will be no magnetometer and no TS-1 for weeks.
⛔ `bringup` treats a missing rx888 as a HARD ABORT, so that install cannot
finish. And `base` is *detection-gated*: absent hardware means the component is
not installed at all, so hardware arriving later is never adopted.

**No local hardware.** A site runs no SDR; radiod lives on another machine on
the LAN. sigmond should find it and offer the clients that can consume it.

## 2 · What already exists

Most of the machinery is built. This design adds one thin layer, and the rest
is wiring.

| layer | state | what it does |
|---|---|---|
| **Discovery** | ✅ built | `sigmond.discovery.*` — `mdns`, `usb_sdr`, `http_ka9q`, `gpsdo`, `magnetometer`, `multicast`, `ntp`, `snmp`, `http_kiwisdr`, `reconciler` |
| **Selection** | ✅ built | `sigmond.sources` — each client consumes ZERO-OR-MORE sources; stable keys `radiod:<host>`, `usb:<vid>:<pid>:<serial>`, `kiwisdr:<host>:<port>`; persisted per client |
| **Adoption** | ❌ **new** | recognise a kit · offer what is adoptable · apply it |

Verified while designing:

* mDNS browses `_ka9q-ctl._udp`, which is what radiod publishes — **remote
  radiod discovery already works.** The module's own comment records a 2026-05-19
  fix for exactly this, made because "the LAN being full of radiods" produced
  zero observations.
* The **Bodnar miniGPS is fully supported**: PID `0x2211` in `hid_xport.PIDS`,
  a complete port of its PLL solver in `mini_pll.py`, its own UBX handling, and
  health logic for its quirks (no antenna indicator). It reports POSITION over
  the UBX HID stream, so a Fargo grid needs no operator input.
* Discovery is consumed today only by `preflight.py` and `heartbeat.py`.
  `bringup.py` never calls it. `smd admin sources` exists but sits under
  `admin`, framed as an expert tool rather than the adoption path.

⇒ The gap is not mechanism. It is that **install and adoption never consult
the mechanism**.

## 3 · The rule

**Install activates nothing.** Every component installs present-and-dormant on
every station, whatever is or is not attached. The rx888 hard abort is lifted.
Install RECORDS what discovery saw and acts on none of it.

**Adoption is explicit.** Hardware appearing is not an instruction. The station
reports a device as detected-but-unadopted and starts nothing until asked.

⛔ That second rule is not fastidiousness. On 2026-09-01 a unit that nobody
asked to run was started by `smd apply` and took B4's timing chain down twice
in one day. A station must never start a service the operator did not choose.

## 4 · Identity comes from the hostname

DASI2 machines are named `DASI001`–`DASI020` (later `DASI` + three digits
generally). Michael pre-defines each one's PSWS Station and Instrument IDs.

So membership is not a free-form claim. It is read from the hostname against a
roster shipped IN THIS REPO — twenty entries, versioned, and readable with no
network, which a field install needs.

    hostname matches DASI\d{3}  AND in roster  ->  DASI2 site
    hostname does not match the pattern        ->  ordinary sigmond station
    matches the pattern, NOT in the roster     ->  ⛔ REFUSE

The refusal matters. A DASI-named host absent from the roster is a typo, a
machine numbered ahead of the roster, or someone imitating the fleet. Guessing
either way is silently wrong: called DASI2 it gets the wrong registrar and no
PSWS IDs; called ordinary it runs forever under a fleet name it does not own.
Fix the hostname or add the roster entry.

This extends a decision the install redesign already locked: *"radiod status
name is auto-composed from the hostname — zero operator input."* The hostname
now carries the station's identity as well as radiod's.

### Membership has exactly two consequences

| consequence | side |
|---|---|
| PSWS station + instrument IDs | VM |
| **RAC registrar** — `vpn.hamsci.org` (DASI2) vs wsprdaemon gw2 (everyone else) | **PM** |

`rac_server` is a fleet constant today (`rac_config.py` calls
`serverAddr gw2:35736` one; `commands/config.py` defaults to
`remote.wsprdaemon.org`). It must become DERIVED.

⚠ **The DASI2 registrar is the one with the failure history.** The wizard once
defaulted to a dead `vpn.hamsci.org:35737` while `gw2.wsprdaemon.org` worked. A
TOFU frps was stood up and verified on vpn.hamsci.org 2026-08-09, so the
endpoint exists — but that path is about to become the default for every grant
kit and should be proven end-to-end before a fleet build depends on it. **Rob
owns that fix; this design only has to be ready for it.** Fargo is non-DASI2 and
therefore takes the registrar that already works.

### The kit pattern is not the membership

DASI2 = rx888 + LBE GPSDO + RM3100. Anyone who assembles that kit gets the
identical configuration offered, with the same prefills. They are simply not a
funded site: no roster entry, no grant identity, wsprdaemon gw2. A DASI2-alike
is a first-class station that may go on to exceed the set.

## 5 · The station manifest — the contract with the PM spec

The PM can install without the VM, and can **completely replace** it. So
adoption decisions made inside the VM are disposable, and re-answering identity
after every replacement would make replacement expensive exactly when it needs
to be cheap.

    /etc/sigmond/station.toml        (PM — the machine that survives)
      dasi2_site       = true|false
      psws_station     = "..."
      psws_instrument  = "..."
      adopted_sources  = ["usb:...", "radiod:hostname", ...]
      clients          = ["wspr-recorder", "psk-recorder", ...]

**No new channel.** The PM already holds an ssh key into the VM — that is how
every operation reached B4. So:

* **Established VM** — the VM is where adoption happens; the PM PULLS and
  mirrors.
* **Fresh VM** — the PM PUSHES on first contact; the VM re-adopts and asks
  nothing.

Conflict resolves by asymmetry rather than by a rule anyone must remember: a
fresh VM has nothing, so the PM's copy seeds it; an established VM is the
source, and the PM mirrors it. A stale mirror costs one re-answer. It cannot
produce a wrong station identity.

## 6 · Components

One new module, `lib/sigmond/adoption.py`, three PURE functions over an
inventory dict — so the entire decision surface tests with no USB bus and no
LAN:

    recognise(inventory) -> KitPattern | None
        DASI2 = rx888 + LBE GPSDO + RM3100.

    offers(inventory, selection) -> list[Offer]
        What could be adopted and is not: a whole kit when one matches,
        otherwise individual sources.  An adopted source stops being offered.

    plan(offer, inventory, identity) -> AdoptionPlan
        Components to enable, prefills derivable, fields that must be asked.

Prefills split as the identity does:

    derivable   component set · radiod status name (hostname)
                channel plan · grid and position (from the GPSDO)
    from roster PSWS station + instrument (DASI2 only)
    asked       nothing, on a rostered DASI2 kit

Surfaces:

* `smd status` gains **detected · adopted · adoptable**. Not under `admin` —
  adoption is the normal path, not an expert one.
* `smd adopt <kit-or-source>` composes the existing machinery: `sources.add`,
  then install → configure → enable → start.

Adoption COMPOSES; it adds no engine. That is the principle the install
redesign already states for `base` and `client`, applied to the layer above.

`bringup.py` gains one call: record the inventory at install time. It acts on
none of it.

## 7 · Testing

The decision surface is pure, so Fargo is dry-runnable before anything is
plugged in:

| case | expect |
|---|---|
| DASI2 inventory, rostered host | one kit offer, every field prefilled, nothing asked |
| DASI2 inventory, DASI-named host NOT in roster | refusal naming both remedies |
| Fargo (rx888 + miniGPS, no magnetometer) | NOT a kit match; three individual offers |
| empty inventory, `radiod:<host>` on the LAN | client offers against that source |
| already-adopted source | no longer offered |
| bare inventory | install plan completes, everything dormant |

⚡ Build Fargo's expected inventory and assert its offers BEFORE the trip. A
dry run that predicts what the box will say is worth more than a checklist.

## 8 · Staging

1. `adoption.py` and its tests — pure, no station contact.
2. Lift the rx888 abort; install-dormant everywhere; `bringup` records inventory.
3. `smd status` adoptable section — read-only, safe to ship early.
4. `smd adopt`.
5. Roster + hostname identity, including the refusal.
6. Manifest READ in the VM. Manifest push/pull is the PM spec.

Steps 1–3 change no running station. Step 4 is the first that starts anything,
and only when asked.

## 9 · What this does not cover

PM provisioning: RAC registrar derived from `dasi2_site`, ssh access to PM and
VM, manifest push/pull, and proving the vpn.hamsci.org path. Separate spec,
same manifest.

Nor does it address B4's own purpose, which Michael restated on 2026-09-02:
B4 exists to demonstrate that what we intend to deploy works — in an update to
an existing station or in a new USB image. **Data preservation on B4 is not a
goal**; data demonstrating intended function and performance is.
