# smd command examples

These examples come from AC0G-B4, the project's test station, on 2026-10-29 between about 22:35 and 22:36 UTC. Every command here only reads. Run `smd` as your own user. It refuses sudo. The examples drop the colour codes that the terminal shows.

## smd check

Use this to ask whether the station does its jobs. It tests the outputs that matter: spots, data files, uploads and the local web pages.

```
smd check
```

```
 ok    PSK            6087 FT8 and 1072 FT4 spots in the last 15 min
 ok    WSPR           362 spots in the last 10 min, on 11 bands
 ok    magnetometer   samples arriving, 32.6 C
 ok    GRAPE          all 6 channels wrote a file in the last 11 min
 ok    storage        /var/lib has 25 % free
 ok    uploads        on; last delivery 1 min ago, no failure in 24 h
 ok    meteor scatter decoding: 21 MSK144 decodes and 0 spots in the last 5 min
 ok    receiver page  http://127.0.0.1:8081 answers
 ok    station pages  http://127.0.0.1:8000 answers
 ok    magnetometer page  http://127.0.0.1:8082 answers
PASS
```

Look at the last line. `PASS` means every line reads `ok`. Any other mark on a line names the part that fails, and the final verdict changes. Read the text on that line first. It says what the check saw.

## smd status

Use this for the full picture: every service, every component version, the CPU plan and the timing judge.

```
smd status
```

The real output runs to about 100 lines. This example keeps one service group and the later sections, and marks each cut with `…` (trimmed).

```
━━━ status ━━━

  gmag-webui:
    ✓  gmag-webui.service: active

  …

  hf-timestd:
    ✓  timestd-core-recorder.service: active
    ✓  timestd-fusion.service: active
    …

  ka9q-radio:
    ✓  radiod@AC0G-B4.service: active

  …

  ✓  hf-timestd  v7.0.0  (0cb5fca)  contract=0.8
     default: 6 ch, 6 freqs

  …

  CPU affinity:
     radiod cores: [12, 13] (other pool: 12 CPUs)
  ✓  plan applied, no contention, no foreign drop-ins

  timing judge:
  ✓  judge T4  σ=200.2 µs  age 0s  gpsdo=locked
  ✗  619e5d2: OFFSET VIOLATION — offset +1.702 ms, rate -0.002 ppm, T4, seg 1
  ✗  4ad803f2: OFFSET VIOLATION — offset +17.346 ms, rate -0.002 ppm, T4, seg 1
  …

  network:
  ✓  lan-capable (checked 3212m ago) [stale]
     querier: v2 10.99.0.2 on ens18

adoptable:
  dasi2                        recognised kit, not adopted (3 source(s))
  run `smd adopt <name>` to record its sources, then enable and start what it brings
```

Look at the marks. A check mark means healthy, a warning mark means look soon, and a cross means a fault. This run returned exit code 1 because of the crosses under the timing judge. B4 is a test bench, so those offset violations stay under study there. On a production station, a cross needs follow-up. The line `[stale]` means the check last ran long ago.

## smd timing

Use this to see which timing tier governs the station and how far the system clock is off.

```
smd timing
```

```
Timing authority (hf-timestd)
  tier       T6   (available: T6,T5,T4 | witnesses: T5,T4,T2)
  offset     -16.07 ms    sigma 3.93 us
  governor   AC0G-B4-status.local
  published  4.8s
System clock (chrony)
  reference  192.168.1.80   stratum 2   offset -1.4 us   rms 3.0 us   leap Normal
```

Look at two things. The tier shows how good the timing source is, and T6 is the best. The `published` age should stay in seconds. Then look at the chrony line. A small offset and a small rms mean the system clock follows its reference well. A large offset, a high stratum or a leap warning needs a closer look.

## smd component list

Use this to see every component on the station, what state it runs in, and whether newer code waits upstream.

```
smd component list
```

The real list holds 26 rows. This example keeps seven and marks the cut with `…` (trimmed). The real output also carries a colour legend.

```
  NAME                LIFECYCLE        BRANCH                    VERDICT        INDEX  BEHIND HEAD DATE  POLICY
  sigmond             self (smd)       detached@1882651          behind          1683      10 2026-10-29 latest
  callhash            library          main                      up to date         6       - 2026-10-20 latest
  hs-uploader         library          detached@d138889          behind           168       6 2026-10-28 latest
  hf-timestd          enabled, running detached@0cb5fca          on branch       2254       - 2026-10-28 latest
  ka9q-radio          enabled, running detached@401992cd         behind          4348     163 2026-09-19 latest
  station-web         enabled, running main                      up to date       123       - 2026-10-28 latest
  codar-sounder       binary, missing  main                      behind            59       1 2026-10-23 latest
  …

  upload readiness  ✓ all upload paths have credentials  (wsprnet.org, wsprdaemon.org, PSKReporter, PSWS (hf-timestd), PSWS (mag-recorder))

     9 component(s) have updates available: sigmond, codar-sounder, hf-tec, hs-uploader, ka9q-radio, meteor-scatter, psk-recorder, superdarn-sounder, wspr-recorder
     run: smd component update  (preview: smd component update --dry-run)
```

Look at LIFECYCLE and VERDICT. A component that should run shows `enabled, running`. `behind` means newer commits wait upstream, and BEHIND counts them. Pinned stations stay behind on purpose; `smd align` decides what they should run. `dirty` means uncommitted changes in a checkout, which deserves attention.

## smd align

Use this before an update to see what would change. Without flags the command makes a dry run and changes nothing.

```
smd align
```

```
smd align — AC0G-B4 against blessed v3.83 (dry run: nothing changes)
  install-time release: v3.82   (the image this VM was first installed from)
  components:
    sigmond          1882651               current
    ka9q-python      f265661               current
    hamsci-dsp       9a16464               current
    …
    wspr-recorder    99f3d47               current
  image-carried files:
    /usr/local/sbin/sigmond-site-timing      current
    /usr/local/sbin/sigmond-location-check   current
  restarts:
    none
  summary: 0 to move, 0 left ahead, 0 refused, 0 missing, 0 stray, 0 files to refresh, 0 services to restart
  host: v3.83 (pm-align 2026-10-29T15:00:34Z)
  apply: re-run with --apply to align this station and restart what is stale (--no-restart leaves services alone)
```

The real list names 26 components; this example trims the middle with `…`.

Look at the summary line. All zeros mean the station already matches the blessed release. Any non-zero count tells you what `--apply` would move, refuse or restart. Read the `restarts` block before you apply: applying can restart radiod.

### smd align --verify

The flag `--verify` only reads, too. Use it after an align, or any time you want proof that the station still records and uploads cleanly.

```
smd align --verify
```

```
smd align --verify — aligned v3.83 at 2026-10-29T13:03:05Z (live record)
  gaps:    VALID — 0 gap events over 2.00 channel-hours (row 2026-10-29T22:05Z)
  uploads: VALID — no backlog; 37 old dead letters in grape-psws, newest 2026-10-27; 45 old dead letters in mag-psws, newest 2026-10-27
```

Look for `VALID` on both lines. The gaps line counts lost-sample events. Zero is the goal. The uploads line reports any backlog. Old dead letters are failed uploads that the queue gave up on. Their dates show whether new ones still arrive. Here the newest is two days old, so none arrived since.

## smd admin viewers

Use this to find the local web pages of the station and see whether each one runs.

```
smd admin viewers
```

```
━━━ viewers ━━━
  ka9q-web        8081  active    ka9q-web.service  Spectrum and waterfall (AC0G-B4-status.local)
  gmag-webui      8082  active    gmag-webui.service  Magnetometer
```

Each row gives the page name, its port, the state of its service and a description. Open the port on the station's address to see the page. `active` is good. Any other state means the page does not answer.

## smd admin cli-reference show

Use this to see which programs have a generated command reference, how many commands each has, and whether the reference is current.

```
smd admin cli-reference show
```

This example comes from a run on the development machine, not from B4, because B4 does not have the command yet. The reference there held only smd itself.

```
  smd                       206 commands  0.2.0-dev    current
```

Look at the last column. `current` means the reference matches the installed program. A stale mark means the program changed since the last build, so run `smd admin cli-reference build`. Add `--json` for the full command tree.
