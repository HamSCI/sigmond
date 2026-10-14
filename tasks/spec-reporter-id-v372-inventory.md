# v3.72 #2 reporter-id forms: inventory of every site (2026-10-14)

Read-only sweep of sigmond, psk-recorder, meteor-scatter, wspr-recorder, sigmond-appliance and
hs-uploader, at sigmond `2943431`.  It rebuilds the 10-13 inventory, which lived only in that
session.  The spec that uses it: `spec-reporter-id-v372.md`, beside this file.

Categories: ESC = escape toward systemd; UNESC = unescape after reading from systemd; DISP = shown
to a person; CMP = comparison; FILE = file or directory name; **BUG** = wrong behaviour today on a
host whose instance reads `AC0G=B4` in storage and `AC0G\x3dB4` in systemd.

## How systemd behaves (systemd 257, probed read-only)

- `systemd-escape 'AC0G=B4'` gives `AC0G\x3dB4`; `systemd-escape 'AC0G/B4'` gives `AC0G-B4`, a different
  instance; `systemd-escape 'AC0G\x3dB4'` gives `AC0G\x5cx3dB4`; `systemd-escape` also turns `-` into
  `\x2d`, so `W1ABC-5` would become `W1ABC\x2d5`.
- `systemctl … psk-recorder@AC0G=B4.service` mangles the name to `\x3d` itself and warns on stderr
  ("Invalid unit name … escaped as …").  `systemctl … psk-recorder@AC0G/B4.service` silently targets
  `psk-recorder@AC0G-B4.service`, the wrong unit.
- Everything systemd hands back (list-units, list-unit-files, `*.wants/` link names, `%i`) carries `\x3d`.
- In a unit file, `%I` unescapes `\x3d` to `=` and ALSO turns `-` into `/`.
- Measured on AC0G-B4 10-14: `/etc/systemd/system/*.wants/{meteor-scatter,psk-recorder,wspr-recorder}@AC0G\x3dB4.service`,
  and stray `/var/lib/psk-recorder/AC0G\x3dB4/`, `/var/lib/meteor-scatter/AC0G\x3dB4/`.

## Existing helpers

| Helper | Where | Does | Callers |
|---|---|---|---|
| `unescape_systemd_instance` | instance.py:77-83 | `\xHH` → char; safe to repeat | instance.py:131, lifecycle.py:279,308,324 only |
| `validate_reporter_id` | instance.py:90-110 | `^[A-Z0-9][A-Z0-9=-]*[A-Z0-9]$`; rejects `\x3d` | many; smd:16019 |
| `parse_user_reporter_id` | instance.py:113-134 | unescape → strip → upper → `/`→`=` → validate; the canonical way in | smd:16163,21275,21733; tui configuration.py:51,518 |
| `display_reporter_id` / `to_wsprnet_form` | instance.py:137-152 | `=`→`/`; does NOT unescape | instance.py, uploader_manifest.py:101, tui, smd `_display_reporter` |
| `_systemd_escape_reporter` | bin/smd:21282-21304 | private; shells to `systemd-escape` (so `-`→`\x2d`, `/`→`-`) | smd:17689 only |
| `_display_reporter` | bin/smd:21307-21326 | private regex unescape + display | smd, 11 sites |
| `storage_instance` | upload.py:41-48 | private `/`→`=`; no upper, unescape or validation | upload.py:55,60 |
| `_decode_on`, `_resolve_instance_env` | harmonize.py:908-914, 1180-1192 | private `unicode_escape` | harmonize only |
| TUI fallbacks | activity.py:24-28, logs.py:23-27, verifier.py:27-31, psws.py:69-73, configuration.py:53-58 | ImportError fallbacks; configuration.py's `_parse_rid` fallback does `/`→`-` (wrong) | — |

Tests today: test_instance.py:68-71 (parse/display with `AC0G/S`); test_upload.py:21-22;
test_harmonize.py:919-929 ASSERTS the escaped form appears in a message (locks a display leak in);
meteor-scatter test_configurator.py:440-448 uses a link named `AC0G=B4` where systemd writes
`AC0G\x3dB4`, so it passes while real hosts break.  No tests cover `unescape_systemd_instance`,
`_list_units_for_client`, `detect_migration_candidates` with an escaped name, or the client cli unescapes.

## sigmond

| # | file:line | Cat | Now → should |
|---|---|---|---|
| S1 | instance.py:77-83 | UNESC helper | keep: the one unescape |
| S2 | instance.py:113-134 | ingress helper | keep: the one parser |
| S3 | instance.py:137-152 | DISP helper | unescape first |
| S4 | instance.py:194 | ESC | `unit_name=f"{client}@{reporter_id}.service"` → build with the escape helper |
| S5 | instance.py:261-275 `_list_units_for_client` | **BUG** | `\x3d` fails validate and gets dropped → unprivileged TUI shows no instance; unescape first |
| S6 | instance.py:716-736 `detect_migration_candidates` | **BUG** | live `\x3d` unit flagged legacy; `smd admin instance migrate --yes` stops+disables it (863-866); TUI dropdowns add a `(legacy)` duplicate; unescape first |
| S7 | instance.py:688-697 | FILE | env stem fine; unescape for safety |
| S8 | instance.py:832-839 `migrate_one_instance` | ESC/FILE | take an unescaped id; escape only for the unit |
| S9 | instance.py:391,409,418,493-494,515-516 | DISP | comments/suggested commands use `=`; slash form in prose (EnvironmentFile path stays `=`) |
| S10 | instance.py:406,436,477,791 | DISP | OK |
| S11 | lifecycle.py:270-326 | UNESC | OK; the model |
| S12 | lifecycle.py:333 | ESC | `template.replace('@.', f"@{instance}.")` → escape helper |
| S13 | harmonize.py:908-914 | UNESC copy | → `unescape_systemd_instance` |
| S14 | harmonize.py:926-933 | **BUG** DISP | warning lists raw `AC0G\x3dB4` |
| S15 | harmonize.py:1180-1192 | UNESC/FILE copy | unescape once; `{rid}.env` |
| S16 | harmonize.py:1196-1213 `_active_instances` | UNESC missing | return unescaped |
| S17 | harmonize.py:1245 | **BUG** DISP | `wspr-recorder@AC0G\x3dS` in validate output (test_harmonize.py:929 locks it) |
| S18 | upload.py:41-48 | copy | → `parse_user_reporter_id` (radiod ids need a lenient path) |
| S19 | upload.py:51-63 `env_path_for` | **BUG** FILE | escaped input → non-existent `AC0G\x3dB4.env`; `apply_enable` creates a shadow env file |
| S20 | commands/config.py:453-462 | DISP | `restarting: …@AC0G\x3dB4.service` |
| S21 | commands/config.py:1354 | DISP | `e.stem` shows `AC0G=B4` |
| S22 | commands/config.py:1371 | DISP | echoes as typed; normalise then display |
| S23 | tui/format.py:876-879, 987 | **BUG** DISP | timing-consumer label shows `AC0G\x3dB4` |
| S24 | tui/screens/lifecycle.py:223 | DISP | `psk-recorder@AC0G=B4` → `@AC0G/B4` |
| S25 | tui/screens/lifecycle.py:112-143 | CMP | OK |
| S26 | tui/screens/activity.py:162-163 | **BUG** DISP | label = storage form; imported display helper unused |
| S27 | activity.py:167-170 | BUG via S6 | `(legacy)` entry |
| S28 | logs.py:95-101 | **BUG** DISP | as S26/S27 |
| S29 | logs.py:305 | CMP | OK; use escape helper consistently |
| S30 | verifier.py:214-220 | **BUG** DISP | as S26/S27 |
| S31 | verifier.py:244 | DISP | goes with S6 |
| S32 | configuration.py:53-58 | wrong copy | fallback `/`→`-`; delete, import mandatory |
| S33 | configuration.py:292,299 | ESC | `systemctl show` arg → escape helper |
| S34 | configuration.py:487,493,522,531 | DISP | confirmation text uses `=` → slash |
| S35 | configuration.py:302,412,462,491,529; psws.py:370,446; receiver_channels.py:107; uploader_manifest.py:101 | DISP | OK |
| S36 | psws.py:226 | FILE | OK after S19 |
| S37 | bin/smd:21282-21304 `_systemd_escape_reporter` | ESC copy, **BUG** | `systemd-escape`: `/`→`-` and `-`→`\x2d` (W1ABC-5 → wrong unit); replace with the instance.py helper |
| S38 | bin/smd:21307-21326 `_display_reporter` | copy | → `display_reporter_id` |
| S39 | bin/smd:17685-17689 meteor-watch | **BUG** ESC | `--instance AC0G/B4` → unit `AC0G-B4`, config `/etc/meteor-scatter/AC0G/B4.toml` |
| S40 | bin/smd:17747-17753 | DISP | OK |
| S41 | bin/smd:17793 verifier-watch | **BUG** ESC | raw `wspr-recorder@{instance}`; slash → `AC0G-B4` |
| S42 | bin/smd:18264 wspr-watch | **BUG** ESC | same |
| S43 | bin/smd:18282-18290 | UNESC missing | `resolved_reporter` raw (display hides it) |
| S44 | bin/smd:19318/19329 hfdl-watch, 19623/19635 codar-watch | **BUG** UNESC | `/etc/hfdl-recorder/AC0G\x3dB4.toml` misses → "no data directory" |
| S45 | bin/smd:20273/20278 hf-tec-watch | UNESC | raw; may match by accident; verify |
| S46 | bin/smd:20412 + 20445 psk-watch | **BUG** ESC/FILE | slash → wrong unit and path; escaped → config miss, "no per-mode log" |
| S47 | bin/smd:20509-20516, 20836, 19135 | DISP | OK |
| S48 | bin/smd:16012-16019 | CMP | `validate_reporter_id` rejects `\x3d` and `/`; parse first |
| S49 | bin/smd:16160-16163 | ingress | OK |
| S50 | bin/smd:16441-16470 | FILE/DISP | hint `wspr-recorder@AC0G=B4.service` → slash |
| S51 | bin/smd:21275, 21733 | ingress | OK |
| S52 | bin/smd:21379,21385,21390,21485,21597-21640 | ESC/DISP | `=` to systemctl (warning can leak into error text at 21611/21638); escape for argv, slash for display |
| S53 | bin/smd:21705-21762 | DISP | migrate output raw `\x3d` and storage form (21762) |
| S54 | bin/smd:14938 bash completion | DISP | offers raw `AC0G\x3dB4` as `--host` |
| S55 | bin/smd:22472-22489 `_radiod_watch_candidate_hosts` | **BUG** | reporter ids offered as ssh aliases (`ssh AC0G\x3dB4`) |

Passed straight back to systemctl, no conversion needed: smd 7035, 10113, 10129, 11222, 11870, 12939,
13663, 15736, 17030, 18004; cpu.py 490/511/1111/1133/1377; component_state.py:188; purge.py:54;
storage_migrate.py:597; tui/screens/overview.py:71.

## meteor-scatter

| # | file:line | Cat | Now → should |
|---|---|---|---|
| M1 | src/meteor_scatter/cli.py:341-346 | UNESC copy | handles `\x3d` only → full unescape |
| M2 | src/meteor_scatter/configurator.py:465-475 `_stale_instances` | **BUG** | `*.wants` link `AC0G\x3dB4` vs `AC0G\x3dB4.toml` → live instance called stale; `config init` offers `disable --now`, DEFAULT YES (484-501); `inst == keep_id` mismatches too.  Fixture test_configurator.py:443 uses `AC0G=B4` |
| M3 | configurator.py:484-501 | DISP/ESC | escaped name in prompts → slash for display, helper for argv |
| M4 | configurator.py:1179-1180 `_env_file_path` | FILE | raw instance; normalise to storage |
| M5 | systemd/meteor-scatter@.service:22-23 | FILE | `env/%i.env` + `env/%I.env`; OK |
| M6 | meteor-scatter@.service:27,29 | **BUG** FILE | `mkdir`/`chown /var/lib/meteor-scatter/%i/msk144` → stray `AC0G\x3dB4/` (spool keys on radiod_id) |
| M7 | meteor-scatter@.service:56-57 | ESC | `--instance %i --radiod-id %i`; `--radiod-id` never unescaped |
| M8 | meteor-scatter@.service:77 | DISP | `SyslogIdentifier=…@%I` (out of scope) |
| M9 | configurator.py:147,155,199 | N/A | radiod_id units |
| M10 | scripts/deploy.sh:88-92 | — | OK |

## psk-recorder

| # | file:line | Cat | Now → should |
|---|---|---|---|
| P1 | src/psk_recorder/cli.py:340-345 | UNESC copy | `\x3d` only → full |
| P2 | systemd/psk-recorder@.service:21-22 | FILE | `%i.env` + `%I.env`; OK |
| P3 | psk-recorder@.service:26-29 | **BUG** FILE | stray `/var/lib/psk-recorder/AC0G\x3dB4/{ft8,ft4}` |
| P4 | psk-recorder@.service:56-57, 76 | ESC/DISP | as M7/M8 |
| P5 | src/psk_recorder/configurator.py:1080-1081 | FILE | as M4 |
| P6 | configurator.py:125,173 | N/A | radiod_id units |
| P7 | scripts/deploy.sh:88-92; install.sh:302-325 | — | OK |

## wspr-recorder (rob's)

| # | file:line | Cat | Now → should |
|---|---|---|---|
| W1 | wspr_recorder/cli.py:315-321 | UNESC copy | `\x3d` only → full |
| W2 | systemd/wspr-recorder@.service:28-29 | FILE | `%i.env` + `%I.env`; OK |
| W3 | wspr-recorder@.service:55,70,88 | ESC/DISP | `StateDirectory=wspr-recorder` (good); `--instance %i`; SyslogIdentifier out of scope |
| W4 | install.sh:323-325,376; scripts/deploy.sh:89-93 | — | OK |
| W5 | hs_uploader_shim.py:130 | other | `call.replace("/", "_")` for an uploader name; not systemd |

## sigmond-appliance

| # | file:line | Cat | Now → should |
|---|---|---|---|
| A1 | sigmond-site-timing:427-428 | copy, latent **BUG** | `tr '/' '='`, no upper/validate; a lowercase reporter_id splits the toml name from the unit name |
| A2 | sigmond-site-timing:460 | DISP | comment header `=` → slash |
| A3 | sigmond-site-timing:463 | ESC | `systemd-escape "$INST"`: right idea, wrong tool (turns `-` into `\x2d`) |
| A4 | sigmond-site-timing:464-465 | DISP | `$INST` → `$REPORTER` |

## hs-uploader (no systemd boundary)

H1 transports/wsprdaemon.py:144-146 `call.replace("/", "=")` (wire format, the origin of `=`); H2
wsprdaemon.py:959,970,1170,1225 `call.replace("/", "_")`.  Leave both.

## Counts

sigmond 55 rows, 17 bugs (S5, S6, S14, S17, S19, S23, S26-S28, S30, S37, S39, S41, S42, S44, S46, S55);
meteor-scatter 10 / 2 (M2, M6); psk-recorder 7 / 1 (P3); wspr-recorder 5 / 0; appliance 4 / 1 latent
(A1, plus A3's hyphen trap); hs-uploader 2 / 0.
