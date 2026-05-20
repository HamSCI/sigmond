# Operator templates — non-schema reference configs

Files in this directory are NOT consumed automatically by sigmond.
They are **reference snippets** that an operator can copy verbatim
(or adapt) to address a specific known wedge.  Each file's header
comment names the symptom it cures.

Current contents (2026-05-20):

| File | Cures |
|---|---|
| `wspr-recorder-single-source.config.toml` | Operator wants to disable one of two `[[source]]` blocks (e.g., a wedged radiod) without losing the rest of the config |
| `journald-wspr-recorder-rate.conf` | wspr-recorder under 17-band load floods journald > 10000 msgs/30s; cycle commit lines get dropped → `smd watch wspr` shows only some rx |
| `wspr-recorder-memoryhigh-3g.conf` | wspr-recorder 17-band load OOM-thrashes the default 2G MemoryHigh cap (per-host drop-in via `systemctl set-property`) |

If a snippet here turns out to be a one-size-fits-all default, promote
it to a permanent path (e.g., into `etc/clients/`, into the canonical
`memory-cap.conf`, etc.) and delete the template here.
