# Release notes draft: sink control step 2b, part 2

**Status:** draft, 2026-10-23, for the release that carries branch `sink-2b-part2` (hs-uploader
77db5c8, sigmond ad53b95, sigmond-appliance 36da796).  The release number comes once the image is
blessed.  The spec: `tasks/spec-sink-2b-part2.md`.

## What changes

The sink switches now decide what leaves a station.

- `smd sink off`, `smd sink fill` and `smd sink upload` set the site sink switch.  sigmond writes
  `/etc/hs-uploader/sinks/site.toml` and keeps `[uploads]` in site-profile.toml and coordination.toml
  as a mirror of it.  `smd upload on|hold|off|discard` and `smd config uploads enable|disable` reach
  the same handler.
- `upload` stores and sends.  `fill` stores and sends no data; `smd sink upload` later ships what it
  stored, oldest first.  `off` sends no data and stores nothing new for upload; data stored before
  `off` waits and ships when the switch rises.  The heartbeat goes out in every state.
- **What `fill` keeps until v3.79.**  sigmond's trim still runs.  It keeps FT8 spots for one hour
  and WSPR spots for 24 hours, sent or not.  A `fill` longer than that loses the older rows.  v3.79
  moves cleanup into hs-uploader and spares unsent rows.
- When the site switch reads below `upload`, sigmond turns off the senders that recorders run of
  their own: psk-recorder and meteor-scatter direct delivery, wspr-recorder's in-process uploader,
  `wspr-uploader.service` and the magnetometer's fallback uploader.  It restarts each touched
  recorder instance alone, one at a time, and never radiod.  Raising the switch turns none of them
  back on.

## The first manifest step after the update

`smd update` pulls the code and changes nothing else.  It prints "the sink switches take effect at
the next `smd align`".  The next manifest step (`smd align`, `smd apply`, `smd sink …`) carries the
station's `[uploads]` setting into switch files once, then writes the carry-over marker
`/var/lib/hs-uploader/carry-over-v1.done`.  Only then do the switches take effect.

- A station at `upload` sees no change in what it sends.  hs-uploader restarts once, because the
  heartbeat moves to `/etc/hs-uploader/pipelines.d/sigmond.toml`.
- A station whose site switch reads `fill` or `off` gets every armed in-process sender lowered, and
  each touched recorder instance restarts once.  Before any switch first reads `off`, sigmond also
  restarts each psk-recorder, meteor-scatter and wspr-recorder instance still running the old sink
  writer, once.
- A client that a recorder's own setting held back (for example `PSK_USE_HS_UPLOADER=0`, as on K3LR)
  carries over at `fill`.  `smd sink` sets the site switch only; no smd verb raises a single client in
  this release.  To raise one, run as root:
  `hs-uploader sink set <client> upload --reason "<why>" --by "<who>"`.

## Rollback

**Roll back only with `smd admin manifest restore`.**  A restore to a sigmond older than this release
moves the carry-over marker aside and removes `pipelines.d/sigmond.toml`, so the old code and the
switch files never disagree.  A hand rollback (`git checkout` in `/opt/git/sigmond/…`) is not
supported: a new hs-uploader keeps enforcing a `site.toml` that the old `smd upload` no longer writes.

**Known gap.**  A restore leaves each checkout it moved detached at the restored commit.  `smd update`
pulls nothing into a detached checkout, so after a restore `smd update` alone never brings the
station forward again.  Run `smd align --apply`: it moves each checkout to the latest blessed
release.  A fix belongs to the next build.

## For the next build (v3.79)

- hs-uploader's installer, when it learns to take the install lock, must use
  `/run/hs-uploader-install/lock`, the path sigmond's manifest step takes now, in a directory only root
  can write.  The older design text named `/run/lock/hs-uploader-install.lock`.
- The carry-over marker sits in `/var/lib/hs-uploader`, which recorder accounts can write.  A
  compromised recorder account could delete it and lift enforcement.  The plan (Ruling 5) records a
  move under `/etc/hs-uploader/sinks/` for v3.79.
