# Timing-chain architecture — chrony / gpsd / hf-timestd

**Status:** Design (proposed 2026-06-06). Step 1 (quiet the storm) applied to the
AC0G/sigma host the same day; steps 2–4 not yet built.

The GPSDO → gpsd → chrony → hf-timestd timing stack on a HamSCI station kept
turning a single failure into a 2–3 component failure. This documents *why* and
the architecture that makes recovery idempotent and non-cascading.

## The failure we hit

On sigma, the local GPS reference (Leo Bodnar LG290P) kept dropping out and the
system fell back to internet NTP. Root cause was not any one component but a
destructive feedback loop:

1. gpsd writes GPS time to SysV SHM `NTP0` (+ PPS); chrony reads it (`refclock
   SHM 0`). Whichever of gpsd/chrony creates the segment first owns it — so a
   chrony restart re-created `NTP0` as root-0600 and **locked gpsd out** → GPS
   reach 0.
2. hf-timestd's `timestd-fusion.service` ran, on every start,
   `ExecStartPre=systemctl stop chrony`, `ipcrm -M NTP0/NTP1` (deleting gpsd's
   SHM outright), and `ExecStartPost=systemctl restart chrony`.
3. hf-timestd's watchdogs (`pipeline-watchdog`, `chrony-monitor` running
   `check-chrony-reach.sh`, `hpps/hfps-watchdog`) restarted fusion and/or chrony
   whenever the `FUSE`/`HPPS` chrony sources were missing — and those sources are
   missing because chrony has no `refclock SHM` wired to read hf-timestd's
   solution. So the checks could never pass → endless restarts (13 chrony
   restarts in 30 min), each nuking the GPS reference.

## The anti-pattern (three coupled mistakes)

1. **Recovery by restarting a *shared* dependency.** hf-timestd "recovered" by
   restarting chrony — which gpsd also depends on. A component power-cycled
   infrastructure it doesn't own, breaking the other producer.
2. **Non-deterministic resource ownership.** SHM-segment ownership depended on
   start order, so every restart was *destructive* instead of idempotent.
3. **Health checks wired to blind restarts.** "FUSE/HPPS reach 0" → restart
   chrony, when the real problem was hf-timestd's own feed not being wired in.
   Dueling watchdogs amplified it into a storm.

## The four rules

1. **Stable interfaces, not lifecycle coupling.** Producers (gpsd, hf-timestd)
   write to chrony's SHM; chrony consumes. With a fixed SHM contract (stable unit
   numbers + perms, pre-created) every process can restart independently and
   non-destructively. The coupling that hurts is one process restarting another.
2. **Own-only recovery.** A component may restart only itself or resources it
   *exclusively owns* — never a shared dependency. hf-timestd must never
   `systemctl restart chrony`; if its feed is missing it restarts its own writer
   or asks chrony to `reload`.
3. **One reconciler owns the chain.** Replace the competing watchdogs with a
   single idempotent reconciler that knows the whole graph and fixes the
   *specific* broken link. It is the only actor allowed to act on the chain.
4. **Deterministic ordering + per-unit self-heal.** Express the dependency in
   systemd (`gpsd → chrony → hf-timestd`), give each unit `Restart=on-failure`,
   and pre-create the SHM with fixed perms before any of them.

## Target architecture

```
GPSDO (LG290P) ──► gpsd ──► SHM[0]=GPS, SHM[1]=PPS ──┐
   (gpsdo-monitor observes lock/sats)                 ├─► chrony ─► system clock
hf-timestd fusion ──► SHM[2]=FUSE, SHM[3]=HPPS ───────┘   (restarted by NOBODY
                                                            except the reconciler)
```

- **SHM contract:** a oneshot pre-creates units 0–3 (mode 0666) ordered before
  gpsd/chrony/hf-timestd. gpsd writes 0/1, hf-timestd writes 2/3, chrony reads
  all four as refclocks. No ownership race. (Interim alternative: run gpsd as
  root so chrony restarts can't lock it out.)
- **chrony:** config carries all four refclocks (adds the missing
  `refclock SHM 2 refid FUSE` / `SHM 3 refid HPPS`). Config changes apply via
  `chronyc reload` / `systemctl reload` — **never restart**.
- **hf-timestd:** *publishes* to its SHM units and never touches chrony's
  lifecycle. Remove the chrony stop/restart + `ipcrm` from `timestd-fusion`;
  remove the `restart chrony` from `check-chrony-reach.sh`; watchdogs may restart
  fusion but may not touch chrony or gpsd's SHM.
- **Reconciler (`smd timing`):** a timer + on-demand verb (the natural home given
  `smd apply` is already sigmond's idempotent reconciler) that walks the chain
  top-down and fixes exactly one link — GPSDO unlocked → alert; gpsd not writing
  SHM → restart gpsd; chrony missing a refclock / not selecting GPS → rewrite
  config + `chronyc reload`, chrony dead → restart chrony once; FUSE/HPPS absent
  → restart fusion only.
- **`smd validate`:** reports the whole chain's health (observability), separate
  from remediation.

Result: any single failure → the reconciler restores the desired state of the
whole chain idempotently, and because the interfaces are stable, no restart
cascades into its neighbours.

## Implementation plan

- **Step 1 — quiet the storm (DONE 2026-06-06, host-level, reversible):** stop +
  disable the chrony-bouncing timers (`timestd-pipeline-watchdog`,
  `timestd-chrony-monitor`, `timestd-hpps-watchdog`, `timestd-hfps-watchdog`);
  drop-in on `timestd-fusion.service` neutralising its chrony stop/restart +
  `ipcrm` (keeping only the legitimate mkdir/chown ExecStartPre); re-establish
  the GPS reference. Stabilises the system so the GPS fix holds while steps 2–4
  are built.
- **Step 2 — stable contract:** SHM pre-create oneshot (or gpsd-as-root) + add
  the `FUSE`/`HPPS` refclocks to chrony + systemd ordering and `Restart=on-failure`.
- **Step 3 — the reconciler:** `smd timing` (validate + reconcile) replacing the
  per-component watchdogs as the single owner of chain recovery.
- **Step 4 — observability:** wire the timing chain into `smd validate`.

This spans sigmond + hf-timestd but uses the same reconcile philosophy sigmond
already applies (`apply` / `validate`), extended to own the timing chain.
