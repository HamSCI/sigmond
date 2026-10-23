#!/usr/bin/env bash
# Regenerate the `hs-uploader sink status --json` fixtures sigmond's
# sink_state tests read (global constraint 4: real output, never typed).
#
#   tests/fixtures/sink_status/capture.sh [OUT_DIR]
#
# Builds each fixture tree in a fresh temporary directory, runs the branch
# hs-uploader on it through its own venv exactly as sigmond's runner does
# (python -I -m hs_uploader.cli, from /, with a bare environment), and
# writes OUT_DIR/<case>.json and OUT_DIR/<case>.rc.  The tree's temporary
# path reads @TREE@ in the output, so a re-run leaves `git diff` empty.
# OUT_DIR defaults to this script's directory; the contract test in
# tests/test_sink_state.py passes its own and compares key for key.
#
# HS_UPLOADER_PY overrides the interpreter (default: the venv of the
# hs-uploader checkout beside sigmond).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="${1:-$here}"
py="${HS_UPLOADER_PY:-$here/../../../../hs-uploader/.venv/bin/python}"
[[ -x "$py" ]] || { echo "capture.sh: no interpreter at $py" >&2; exit 1; }
mkdir -p "$out"

# CAPTURE_WORK names an empty directory to build the trees in and keep
# (the contract test runs sigmond's reader on them); by default they go
# in a temporary directory removed on exit.
if [[ -n "${CAPTURE_WORK:-}" ]]; then
    work="$CAPTURE_WORK"
else
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
fi

AT="2026-10-01T00:00:00Z"

manifest() {    # manifest TREE: the legacy manifest and three pipelines.d files
    local t="$1"
    mkdir -p "$t/etc/pipelines.d" "$t/state"
    cat > "$t/etc/pipelines.toml" <<'TOML'
[identity]
call = "AC0G"
grid = "EM38ww"

[[pipeline]]
name = "sigmond-heartbeat"
client = "sigmond"
[pipeline.source]
type = "sqlite"
database = "sigmond"
table = "sigmond.heartbeat"
[pipeline.transport]
type = "heartbeat_sftp"
host = "heartbeat.example"
TOML
    cat > "$t/etc/pipelines.d/wspr-recorder.toml" <<'TOML'
[[pipeline]]
name = "wspr-recorder-wsprnet"
[pipeline.source]
type = "sqlite"
database = "sigmond"
table = "wspr.spots"
producer = "wspr-recorder"
[pipeline.transport]
type = "wsprnet"
TOML
    cat > "$t/etc/pipelines.d/psk-recorder.toml" <<'TOML'
[[pipeline]]
name = "psk-recorder-pskreporter"
[pipeline.source]
type = "sqlite"
database = "sigmond"
table = "psk.spots"
producer = "psk-recorder"
[pipeline.transport]
type = "pskreporter"
TOML
    cat > "$t/etc/pipelines.d/mag-recorder.toml" <<'TOML'
[[pipeline]]
name = "mag-recorder-psws"
[pipeline.source]
type = "filetree"
root = "/var/lib/mag-recorder/outbox"
patterns = ["*.zip"]
[pipeline.transport]
type = "psws_dataset"
instrument_id = "367"
TOML
}

switch() {      # switch TREE NAME STATE: one switch file, as `sink set` writes it
    local t="$1" name="$2" state="$3"
    mkdir -p "$t/etc/sinks"
    {
        [[ "$name" == site ]] || echo "client = \"$name\""
        echo "state = \"$state\""
        echo "reason = \"fixture\""
        echo "set_by = \"carry-over\""
        echo "set_at = \"$AT\""
    } > "$t/etc/sinks/$name.toml"
}

switches() {    # switches TREE SITE WSPR PSK MAG
    switch "$1" site "$2"
    switch "$1" wspr-recorder "$3"
    switch "$1" psk-recorder "$4"
    switch "$1" mag-recorder "$5"
}

marker() {
    printf 'carried over at %s\nfixture\n' "$AT" > "$1/state/carry-over-v1.done"
}

capture() {     # capture CASE: run status on $work/CASE, normalise, store
    local c="$1" t="$work/$1" rc=0
    (cd / && env -i PATH=/usr/bin:/bin "$py" -I -m hs_uploader.cli \
        --state "$t/state/watermarks.db" sink status --json \
        --manifest "$t/etc/pipelines.toml" \
        --marker "$t/state/carry-over-v1.done") \
        > "$work/$c.out" 2>/dev/null || rc=$?
    sed "s|$t|@TREE@|g" "$work/$c.out" > "$out/$c.json"
    echo "$rc" > "$out/$c.rc"
}

# 1. Every switch at upload, the marker standing.
t="$work/all-upload"; manifest "$t"; switches "$t" upload upload upload upload
marker "$t"; capture all-upload

# 2. Site off: every client reads off whatever its own file says.
t="$work/site-off"; manifest "$t"; switches "$t" off upload upload upload
marker "$t"; capture site-off

# 3. psk-recorder's file at fill, the site at upload.
t="$work/psk-fill"; manifest "$t"; switches "$t" upload upload fill upload
marker "$t"; capture psk-fill

# 4. psk-recorder's file at fill under site off: file and effective differ.
t="$work/psk-fill-site-off"; manifest "$t"; switches "$t" off upload fill upload
marker "$t"; capture psk-fill-site-off

# 5. A broken site.toml.
t="$work/broken-site"; manifest "$t"; switches "$t" upload upload upload upload
echo 'state = "upload' > "$t/etc/sinks/site.toml"
marker "$t"; capture broken-site

# 6. No sinks/ directory, the marker standing.
t="$work/no-sinks"; manifest "$t"; marker "$t"; capture no-sinks

# 7. The marker absent: effective states still read, nothing enforced.
t="$work/marker-absent"; manifest "$t"; switches "$t" off upload fill upload
capture marker-absent

# 8. The store unreadable: status exits 0 and lists the error.
t="$work/store-unreadable"; manifest "$t"; switches "$t" upload upload fill upload
marker "$t"; echo "not a database" > "$t/state/watermarks.db"
capture store-unreadable

# 9. Refusals: a pipelines.d file the loader refuses, and one the daemon
#    refused for the store's schema (recorded in the real store).
t="$work/refused"; manifest "$t"; switches "$t" upload upload upload upload
switch "$t" meteor-scatter upload
cat > "$t/etc/pipelines.d/meteor-scatter.toml" <<'TOML'
[[pipeline]]
name = "meteor-scatter-pskreporter"
[pipeline.source]
type = "sqlite"
database = "sigmond"
table = "psk.spots"
start_at = "now"
[pipeline.transport]
type = "pskreporter"
TOML
(cd / && env -i PATH=/usr/bin:/bin "$py" -I -c '
import sys
from hs_uploader.watermark.sqlite import SqliteWatermarkStore
s = SqliteWatermarkStore(sys.argv[1])
s.record_pipeline_states([("store-held", "wspr-recorder", "store schema too old")])
s.close()' "$t/state/watermarks.db")
marker "$t"; capture refused

# 10. The manifest unreadable: status exits 2 and still prints JSON.
t="$work/manifest-unreadable"; manifest "$t"; switches "$t" upload upload upload upload
echo '[[pipeline' > "$t/etc/pipelines.toml"
marker "$t"; capture manifest-unreadable
