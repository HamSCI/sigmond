# Reporter ids: one form for people, one for storage, one for systemd (v3.72 #2) — design

**Status:** spec, 2026-10-14, awaiting Michael's review.  Written against sigmond `2943431`.  The
site-by-site inventory sits beside the research maps:
`tasks/spec-reporter-id-v372-inventory.md`.  Rows there carry ids (S5, M2, A1 …), and
this spec cites them.

## 1. Why

A station's reporter id names the station to the outside world.  WSPRnet and PSKReporter credit
AC0G-B4's spots to `AC0G/B4`.  The same id names files, directories and systemd units on the
station, and none of those places accepts a slash.  sigmond therefore stores `AC0G=B4`.  systemd
in turn refuses `=` in a unit name, so it writes `AC0G\x3dB4`.

Three spellings of one name now travel through about eighty places in five repositories.  Most
handle the spelling they meet.  Seventeen in sigmond, two in meteor-scatter, one in psk-recorder
and one in the appliance do not, and they fail in ways an operator cannot see coming:

- meteor-scatter's `config init` looks for `AC0G\x3dB4.toml`, finds nothing, decides the live
  instance went stale, and offers to disable it, with yes as the default (M2).  AC0G-B4 carries
  the link names that trigger this today.
- `smd admin instance migrate --yes` takes the live unit for a legacy one and stops and disables
  it (S6).
- The watch commands take `--instance AC0G/B4`, and systemctl quietly turns the slash into a
  hyphen, so they look at `AC0G-B4`, a unit that does not exist (S39, S41, S42, S46).
- Without read access to `/etc/<client>/`, the TUI shows no instance at all (S5).
- Messages show `AC0G\x3dB4` to people (S14, S17, S23, S53, S54).

Michael decided on 2026-10-13 how this should end: a person only ever types and sees `AC0G/B4`.

## 2. The rule

| Who handles it | Form | Example |
|---|---|---|
| a person, typing or reading | display | `AC0G/B4` |
| sigmond and the clients: files, directories, config keys | storage | `AC0G=B4` |
| systemd: unit names, `*.wants` links, `%i` | systemd | `AC0G\x3dB4` |

Hyphens pass through all three unchanged, because operators use them on purpose: `W1ABC-5`
stays `W1ABC-5` everywhere.

Conversion happens at exactly two boundaries, and only through named helpers:

1. **Toward a person.**  Text that a person typed enters through `parse_user_reporter_id`, which
   already accepts all three spellings.  Text that a person will read leaves through
   `display_reporter_id`.
2. **Toward systemd.**  A unit name leaves through `systemd_instance` / `unit_name`.  Anything read
   back from systemd (list-units, list-unit-files, a `*.wants` link, `%i`, an `--instance` argument
   a unit passed) enters through `unescape_systemd_instance` before code compares, validates,
   stores or shows it.

Code between the boundaries holds the storage form and nothing else.

## 3. The helpers, in `sigmond/lib/sigmond/instance.py`

The module keeps the three it has and gains two:

- `parse_user_reporter_id(text) -> str`: unchanged.
- `unescape_systemd_instance(name) -> str`: unchanged.  Repeating it stays safe.
- `display_reporter_id(rid) -> str` now unescapes first, then turns `=` into `/`.  It therefore
  renders any of the three spellings correctly, and a stray `\x3d` can never reach a screen.
- **New** `systemd_instance(rid) -> str` takes a storage-form id, validates it, and escapes only
  the characters a unit name cannot hold.  For a valid reporter id that means `=` → `\x3d`; letters,
  digits and `-` stay as they are.  It works in plain Python and never calls `systemd-escape`.
  That tool turns `-` into `\x2d` (probed: `W1ABC-5` → `W1ABC\x2d5`), which names a different unit
  from the one systemctl created when the instance got enabled.  The new helper matches what
  systemctl itself does to an unescaped name, so it reproduces the names already on the stations.
- **New** `unit_name(client, rid, suffix=".service") -> str` returns
  `f"{client}@{systemd_instance(rid)}{suffix}"`.  `InstancePaths.unit_name` and every unit sigmond
  builds go through it.

No unit, file or directory on a station changes its name.  The helpers describe the names the
stations already carry.

## 4. sigmond changes

Route every site in the inventory's sigmond table through the helpers, and delete the private
copies: `_systemd_escape_reporter` and `_display_reporter` in `bin/smd` (S37, S38), the inline regex
at smd:17687, `storage_instance` in `upload.py` (S18), the `unicode_escape` decoders in
`harmonize.py` (S13, S15), and the TUI's ImportError fallbacks, one of which turns `/` into `-` (S32).

The bugs, grouped by what the fix does:

- **Unescape before validating or comparing:** S5, S6, S16, S43, S44, S45.
- **Parse operator input, then escape for systemd:** S39, S41, S42, S46, S48; S37 goes with them,
  since replacing it removes the hyphen trap.
- **Unescape before building a path:** S19 (no more shadow env files).
- **Display form in every message, label and printed hint:** S9, S14, S17, S20-S24, S26-S28, S30,
  S34, S50, S52, S53, S54.  A printed command that the operator will run uses the form that command
  accepts; the plan checks each one, and where the command parses its argument with
  `parse_user_reporter_id` the hint shows `AC0G/B4`.
- **Stop offering reporter ids as ssh hosts:** S54, S55.

`test_harmonize.py:929` asserts the escaped form in a message.  It changes to assert the display form.

## 5. Client changes

Michael chose on 2026-10-14 that each client keeps a small copy of the conversion code rather than
importing sigmond.  A client must run on a host or in a venv that carries no sigmond, the same
reasoning as D14 for the sink writer.

- **The copy.**  Each of psk-recorder, meteor-scatter and wspr-recorder gains one module,
  `_reporter_id.py`, of about fifteen lines: `unescape_systemd_instance`, `to_storage`,
  `systemd_instance`, `display`.  Its docstring names sigmond's `instance.py` as the original.
- **The guard against drift.**  sigmond holds one table of cases,
  `tests/data/reporter_id_vectors.json`: input, storage form, systemd form, display form, including
  `AC0G/B4`, `AC0G=B4`, `AC0G\x3dB4`, `AC0G\x5cx3dB4`, `W1ABC-5`, `KP4MD-RPI-4`, `AI6VN/P` and lower
  case.  Each client carries a byte-identical copy and a test that runs its module against it.
  sigmond's own test runs `instance.py` against the table and, when a sibling client checkout sits
  beside it, fails if that client's copy of the table differs.
- **meteor-scatter** (Michael's): `_stale_instances` unescapes the link name before comparing (M2).
  Its prompts show the display form and its `systemctl` calls use `systemd_instance` (M3).
  `_env_file_path` normalises to storage form (M4).  The test fixture uses the link name systemd
  really writes, `meteor-scatter@AC0G\x3dB4.service`.
- **psk-recorder** (Michael's): `cli.py` uses the module (P1); `_env_file_path` normalises (P5).
- **wspr-recorder** (rob's): `cli.py` uses the module (W1).  Nothing else changes.  The bus carries
  the plan to Rob before anyone touches the repository.
- **Unit templates, psk-recorder and meteor-scatter.**  `ExecStartPre=` builds
  `/var/lib/<client>/%i/…` and `chown`s it (M6, P3).  `%i` keeps the `\x3d`, which leaves a stray
  directory.  `%I` would unescape it but would also turn the `-` in `W1ABC-5` into `/` and build
  `/var/lib/psk-recorder/W1ABC/5`.  So the plan first confirms that nothing reads the per-instance
  directory (the spool keys on the radiod id), and then removes those lines.  If something does
  read it, the template uses a `StateDirectory=` that sigmond names in storage form.
- **The stray directories** already on stations go away during deploy, but only when they hold
  nothing except empty mode subdirectories.  Anything with content stays and gets reported.
- `--radiod-id %i` (M7) reaches the program escaped.  The program unescapes it on entry.

## 6. Appliance change

`sigmond-site-timing` computes the storage form with `tr '/' '='` and escapes with `systemd-escape`
(A1, A3).  Both get replaced by asking smd.  A new read-only `smd admin instance canonical <text>`
prints the storage form, or with `--systemd` the systemd form, and exits non-zero on an invalid id.
Messages use `$REPORTER` (A4), and the generated config's comment header uses the display form (A2).

## 7. Testing

- Every test that involves systemd uses the spelling systemd writes: `AC0G\x3dB4`.  A fixture
  holding `AC0G=B4` where systemd would write `\x3d` let M2 pass its test while real hosts broke.
- One test per bug in the inventory, each watched failing before its fix.
- Hyphen cases (`W1ABC-5`, `KP4MD-RPI-4`) in every helper test.
- Mutation: break each helper and each boundary call, and confirm a test fails.
- On AC0G-B4, after deploy: meteor-scatter's `_stale_instances`, called directly, returns no
  instance; `smd admin instance migrate` lists no candidate; the watch commands accept
  `--instance AC0G/B4`; `smd tui` shows `AC0G/B4`; the stray directories have gone.

## 8. Rollout

v3.72 carries it.  No unit, file or directory changes its name, so a station takes the update in
place, and rolling back restores today's behaviour without leaving anything behind.  The deploy's
one change on disk removes the empty stray directories.

## 9. Out of scope

- `SyslogIdentifier=<client>@%I` shows `AC0G=B4` in the journal.  Making it show `AC0G/B4` would
  need `%I`'s `-` → `/` rule, which breaks hyphenated ids.  It stays.
- hs-uploader's wire formats (`call.replace("/", "=")` for the wsprdaemon tar path, `"_"` for the
  sftp name) belong to the destinations, not to systemd (H1, H2).
- Instances keyed by radiod id (`AC0G-B4-status.local`) contain no `=` and need nothing.
