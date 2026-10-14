# Reporter ids: one form per audience (v3.72 #2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person types and reads `AC0G/B4`; files and directories hold `AC0G=B4`; systemd gets `AC0G\x3dB4`; and
each conversion happens only at the boundary, through one set of named helpers.

**Architecture:** `sigmond/lib/sigmond/instance.py` gains the helpers (`systemd_escape`, `systemd_instance`,
`unit_name`, `display_unit_name`, `parse_instance_arg`) and `display_reporter_id` learns to unescape.  A shared table,
`tests/data/reporter_id_vectors.json`, pins what every copy of the rules must do.  The sigmond call sites then move
onto the helpers, grouped by what the fix does: read systemd's names back in storage form (Task 2), build unit names
through the helpers (Tasks 3-4), parse what an operator types (Task 5), and show people the display form (Tasks 6-7).
A read-only `smd admin instance canonical` serves shell scripts (Task 8).  Each client (psk-recorder, meteor-scatter,
wspr-recorder) carries a small `_reporter_id.py` copy and a byte-identical copy of the table (Tasks 9-12); the
appliance asks smd instead of using `tr` and `systemd-escape` (Task 13); a sigmond test catches a drifted copy (Task
14); and a manual check on AC0G-B4 closes the work (Task 15).

**Tech Stack:** Python 3.11, standard library only in sigmond core; bash; systemd unit files; pytest; the appliance's
hermetic bash tests.

**Spec:** `/home/mjh/hamsci/repos/sigmond/tasks/spec-reporter-id-v372.md` (Michael approved it on 2026-10-14), with
its site inventory `/home/mjh/hamsci/repos/sigmond/tasks/spec-reporter-id-v372-inventory.md`.  This plan cites the
inventory's row ids (S5, M2, A1 …).  Where the plan departs from the spec, it says so in the task and in "Decisions
this plan made" below.

**Status:** written 2026-10-14 against sigmond `973838b`, meteor-scatter `83213d0`, psk-recorder `8dfb64f`,
wspr-recorder `65713b5` and sigmond-appliance `753a618`.  The planner re-read every cited line in those trees.
"Verified anchors" below lists the ones that moved and the rows the inventory got wrong.

## How this plan carries its code

The planner built scratch copies of sigmond, meteor-scatter, wspr-recorder and sigmond-appliance and ran there the
code of Tasks 1-9, 12 and 13, and the cleanup script of Task 11 with its `StrayDirTests`.  Every one of those tests
passed after its change.  The sigmond full suite passed on the scratch copy with Tasks 1-8 applied (4317 passed;
its 2 failures came from the scratch copy lacking `.gitignore`, and pass in the checkout).  The planner also ran the
new tests of Tasks 2, 3 and 13 against the unchanged code and saw the failures this plan predicts.  These did not
run: all of Task 10 (psk-recorder's venv carries no pytest), Task 11's unit-template edits, `UnitTemplateTests` and
Branch B, and Task 14.

So treat each red run as a real check.  If a test fails for a reason its step does not name, stop and read before you
change anything.  If an anchor quoted in a step no longer matches the tree, find where it went, make the same edit by
hand, and say so in your report.

## Verified anchors (2026-10-14)

Every row the tasks below touch was read at the commits above.  Rows not listed here sit where the inventory says.

- **Moved by a line or two:** `harmonize.py` `_active_instances` runs 1197-1214 (inventory: 1196-1213).
  `upload.py` `env_path_for` runs 51-64.  `instance.py` `_list_units_for_client` runs 238-276; its validate sits at
  271-274.  `bin/smd` hfdl-watch takes the instance at 19319 and resolves the config at 19329-19330; codar-watch at
  19625 and 19635-19636.  meteor-scatter `cli.py` unescape sits at 341-346 inside `_handle_daemon` (def at 320);
  psk-recorder's at 340-345 (def at 319).  wspr-recorder `cli.py` at 314-321.  meteor-scatter `_stale_instances` runs
  457-476, `_retire_stale_instances` 479-501.  psk-recorder `install.sh` installs the unit at 298-302.
- **The inventory missed these:**
  - `commands/config.py:1382` prints `sudo systemctl restart '{client}@{instance}'` with the instance as typed.
    Typed as `AC0G/S`, it names `AC0G-S`, the wrong unit.  Task 6 fixes it with S22.
  - `bin/smd:16191-16201` (`cmd_sources_remove`) never normalises a `<client>@AC0G/B4` key, so the slash lands in a
    file path.  Task 6 makes the stub files print that very command with the display form, so Task 5 fixes the verb.
  - Escaping `UnitRef.unit` (S12) reaches every line that prints it: `bin/smd` 7594, 10380-10401, 11087, 11366-11372,
    11442, 12324-12326, 12398-12436, 13240 and 13523.  They printed `psk-recorder@AC0G=B4.service` before and would
    print `\x3d` after.  Task 4 routes all of them through `display_unit_name`.
  - `tests/test_uninstall.py:232` names a `*.wants` link `wspr-recorder@AC0G=S.service`.  The test does not depend on
    the spelling, but spec §7 asks for systemd's spelling in every such fixture; Task 3 changes it.
- **Rows that turned out different from the inventory:**
  - S45 (hf-tec watch, `bin/smd:20273/20278`): hf-tec instances carry radiod ids (`hf-tec@.service` has
    `After=radiod@%i.service`), never `=`.  No change.
  - S44: the fallback data directories in hfdl-watch and codar-watch key on `%i` on purpose (codar's unit runs
    `mkdir -p /var/lib/codar-sounder/%i`).  Task 2 unescapes only inside `_radiod_status_from_per_instance_config`,
    which both watches call, and leaves the fallbacks alone.
  - S43 (`bin/smd:18282-18290`): only the display used `resolved_reporter`, and the display already unescaped.  Task 5
    unescapes it anyway, as hygiene, with no test of its own.
  - S33 belongs with the configuration screen, so Task 7 carries it.
- **Test runners, checked:** sigmond `.venv/bin/pytest` works (4262 passed at `973838b`, 2 min 50 s).
  meteor-scatter `.venv/bin/pytest` works (275 passed, 1 skipped).  wspr-recorder `.venv/bin/pytest` works, but the
  venv cannot import the `wspr_recorder` package (no `hamsci_dsp`), so its tests load modules by file path.
  psk-recorder's `.venv` has no pytest: use `uv run --frozen --extra dev pytest`, and run `git status --short uv.lock`
  afterwards (it must print nothing; if it does, `git checkout uv.lock`).

## Decisions this plan made (spec ambiguities)

1. **More helpers than the spec's two.**  Spec §3 adds `systemd_instance` and `unit_name`.  The plan adds three more,
   because the call sites need them:
   - `systemd_escape(name)` escapes without validating.  `lifecycle.py`, `migrate`, the logs screen and the watch
     commands also handle legacy radiod-keyed names (`my-rx888`, `bee1-status.local`) that fail
     `validate_reporter_id`.  `systemd_instance` = validate, then `systemd_escape`.
   - `display_unit_name(unit)` renders `psk-recorder@AC0G\x3dB4.service` as `psk-recorder@AC0G/B4.service`.  Without
     it, escaping `UnitRef.unit` (S12) would put `\x3d` on every lifecycle line smd prints.
   - `parse_instance_arg(text)` reads a watch command's `--instance` or an upload instance.  It takes a reporter id in
     any spelling and also lets a legacy name through.  Its rule: text with `/` or `=` names a reporter id; text in
     lower case without either names a legacy radiod id; anything else counts as a reporter id when it validates.
     So `smd watch psk --instance w1abc-5` looks for the legacy unit `w1abc-5`.  The `smd admin instance` verbs keep
     the strict `parse_user_reporter_id` and upper-case it.  Review Focus 1 covers this.
2. **`systemd_escape` escapes a backslash too** (`\x5c`).  That makes it the exact inverse of one
   `unescape_systemd_instance`, so a double-escaped orphan (`psk-recorder@AC0G\x5cx3dB4.service`) keeps its own name
   in `lifecycle.py` instead of resolving to the live unit.  Today it resolves to the live unit, and stopping "the
   orphan" stops the live recorder.  systemctl itself would leave a backslash alone; that difference never touches a
   valid reporter id.
3. **`display_reporter_id` unescapes until nothing changes**, so even `AC0G\x5cx3dB4` shows as `AC0G/B4`.  Spec §3
   says "unescapes first"; the plan reads its promise ("a stray `\x3d` can never reach a screen") as the rule.
4. **Printed commands.**  Spec §4: a printed command uses the form its command accepts.  For `smd` commands that
   means the display form (`smd admin instance enable psk-recorder AC0G/B4`).  For `systemctl` and `journalctl` it
   means the systemd form, single-quoted: `sudo systemctl restart 'wspr-recorder@AC0G\x3dB4.service'`.  smd has no
   per-instance restart verb to point at instead.  `smd admin instance show` therefore prints `unit:
   psk-recorder@AC0G/B4` and, on the next line, `journal:  journalctl -u 'psk-recorder@AC0G\x3dB4.service'`.
5. **The client copy's `systemd_instance` does not validate.**  Clients meet legacy names too (meteor-scatter's
   stale-instance sweep).  It matches sigmond's `systemd_escape`; on every reporter id in the table it agrees with
   sigmond's `systemd_instance`, and the table tests both.
6. **The appliance derives the display form with `tr '=' '/'` on smd's canonical output.**  Spec §6 gives
   `canonical` only the storage and `--systemd` forms.  Turning a canonical storage id into its display form takes
   exactly that one substitution, and `display_reporter_id` does nothing more to it.  Spec §6 also says messages use
   `$REPORTER`; the plan uses that display form, which equals `$REPORTER` for any id typed in upper case and fixes a
   lower-case one (A1).
7. **Stray directories go away in each client's `scripts/install.sh`**, after its `systemctl daemon-reload`.
   `smd update --apply` runs `scripts/install.sh` after every pull (`lib/sigmond/update.py:188-196`;
   `bin/smd:4018-4050`), and its own note says not to use `deploy.sh`.  A fresh v3.72 image never had the directories.
8. **The unit-template branch.**  Task 11 decides it by reading code.  The planner's reading predicts Branch A
   (remove the lines): every reader joins the spool root with the radiod id, and the daemon creates its spool
   directories on first write.
9. **`smd admin instance show` keeps the systemd spelling inside the journalctl command** (decision 4).  Michael may
   prefer to drop that line; it costs one line to remove.

## Global Constraints

- The three forms, verbatim from spec §2: a person handles `AC0G/B4`; sigmond and the clients store `AC0G=B4`;
  systemd carries `AC0G\x3dB4`.  "Hyphens pass through all three unchanged": `W1ABC-5` stays `W1ABC-5` everywhere.
- "Conversion happens at exactly two boundaries, and only through named helpers."  Code between the boundaries holds
  the storage form and nothing else.
- `systemd_instance(rid)` "escapes only the characters a unit name cannot hold … It works in plain Python and never
  calls `systemd-escape`."
- "No unit, file or directory on a station changes its name."  The deploy's one change on disk removes empty stray
  directories.
- Each client keeps its own `_reporter_id.py` and imports nothing from sigmond (D14 reasoning).
- Every test that involves systemd uses the spelling systemd writes: `AC0G\x3dB4`.
- Every bug fix gets a test that you watch fail before the fix.  Every task ends with a mutation check: break the line
  named, run the named test, see it fail, then restore with `git checkout -- <file>`.  The mutation step comes after
  the commit, so `git checkout` restores the committed fix.
- **Repositories and commits.**  Tasks 1-8 and 14 commit in `/home/mjh/hamsci/repos/sigmond`; Task 9 in
  meteor-scatter; Task 10 in psk-recorder; Task 11 in both of those (two commits); Task 12 in wspr-recorder; Task 13
  in sigmond-appliance.  Work on `main`.  Commit by path, never `git add -A`.  Never push.  End each commit message
  with the attribution lines your own session's instructions give.
- **Tests.**  sigmond: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest <path> -q --override-ini addopts=`
  (never a bare `pytest`, never `uv run`).  meteor-scatter and wspr-recorder: `.venv/bin/pytest <path> -q`.
  psk-recorder: `uv run --frozen --extra dev pytest <path> -q`, then `git status --short uv.lock` must print nothing.
  sigmond-appliance: `bash <test-script>`.
- **Reach nothing outside this machine** until Task 15, and in Task 15 only after the bus announcement.  Run no smd
  verb that changes the host you work on.
- wspr-recorder belongs to Rob.  Task 12 posts to the claude-bus before it touches that repository.
- After the code tasks, run `graphify update /home/mjh/hamsci/repos` once (never `graphify update .`).

## Review Focus

1. **A lower-case reporter id without a slash typed where a legacy name may appear** (`smd watch psk --instance
   w1abc-5`, `smd config upload psk-recorder w1abc-5 --on`).  A person expects it to mean `W1ABC-5`.  The lenient
   parser reads it as a legacy radiod name, as decision 1 says.  Task 1 pins the rule; Task 2 makes the upload path
   use the existing `W1ABC-5.env` when only that file exists, so nobody gets a shadow file.
2. **A double-escaped orphan unit** (`psk-recorder@AC0G\x5cx3dB4.service`) beside the live one.  A person expects
   smd to flag it as an orphan and never to touch the live unit through it.  Task 3 pins it.
3. **An smd too old to know `canonical`**, run by a newer appliance script.  A person expects a clear warning, not a
   silently missing meteor-scatter instance.  Task 13 pins it with the invalid-id case, which takes the same path.
4. **A stray directory that turns out to be a symlink.**  A person expects the cleanup never to follow it or remove
   what it points at.  Task 11 pins it.
5. **A reporter id with more than one slash** (`AC0G/B4/P`).  A person expects it to round-trip like any other.
   Task 1 puts it in the shared table, so every copy gets tested on it.

---

### Task 1: The helpers and the shared table (sigmond)

**Files:**
- Create: `tests/data/reporter_id_vectors.json`
- Create: `tests/test_reporter_id_forms.py`
- Modify: `lib/sigmond/instance.py:25-27` (imports), `lib/sigmond/instance.py:137-144` (`display_reporter_id`, and
  new functions after it)

**Interfaces:**
- Consumes: `unescape_systemd_instance`, `validate_reporter_id`, `parse_user_reporter_id`, `InvalidReporterId`
  (all in `instance.py` today).
- Produces, in `sigmond.instance`:
  - `display_reporter_id(reporter_id: str) -> str`, now accepting all three spellings.
  - `parse_instance_arg(text: str) -> str`; raises `InvalidReporterId`.
  - `systemd_escape(name: str) -> str`; no validation.
  - `systemd_instance(reporter_id: str) -> str`; validates, raises `InvalidReporterId`.
  - `unit_name(client: str, reporter_id: str, suffix: str = ".service") -> str`.
  - `display_unit_name(unit: str) -> str`.
  - The table `tests/data/reporter_id_vectors.json` with keys `reporter_ids` (input, storage, systemd, display),
    `legacy_names` (input, storage, systemd), `invalid` (list of str) and `display_only` (input, display).

- [ ] **Step 1: Write the shared table**

Create `tests/data/reporter_id_vectors.json` with exactly this content.  Tasks 9, 10 and 12 copy it byte for byte.

```json
{
  "about": "Reporter-id spellings: display AC0G/B4, storage AC0G=B4, systemd AC0G\\x3dB4. The original lives in sigmond tests/data/; psk-recorder, meteor-scatter and wspr-recorder keep byte-identical copies in their own tests/data/. Edit sigmond's copy, then cp it to each client. sigmond tests/test_reporter_id_drift.py fails while any copy differs.",
  "reporter_ids": [
    {"input": "AC0G/B4",     "storage": "AC0G=B4",     "systemd": "AC0G\\x3dB4",       "display": "AC0G/B4"},
    {"input": "AC0G=B4",     "storage": "AC0G=B4",     "systemd": "AC0G\\x3dB4",       "display": "AC0G/B4"},
    {"input": "AC0G\\x3dB4", "storage": "AC0G=B4",     "systemd": "AC0G\\x3dB4",       "display": "AC0G/B4"},
    {"input": " ac0g/b4 ",   "storage": "AC0G=B4",     "systemd": "AC0G\\x3dB4",       "display": "AC0G/B4"},
    {"input": "AC0G-B4",     "storage": "AC0G-B4",     "systemd": "AC0G-B4",           "display": "AC0G-B4"},
    {"input": "W1ABC-5",     "storage": "W1ABC-5",     "systemd": "W1ABC-5",           "display": "W1ABC-5"},
    {"input": "KP4MD-RPI-4", "storage": "KP4MD-RPI-4", "systemd": "KP4MD-RPI-4",       "display": "KP4MD-RPI-4"},
    {"input": "AI6VN/P",     "storage": "AI6VN=P",     "systemd": "AI6VN\\x3dP",       "display": "AI6VN/P"},
    {"input": "ai6vn=p",     "storage": "AI6VN=P",     "systemd": "AI6VN\\x3dP",       "display": "AI6VN/P"},
    {"input": "AC0G/B4/P",   "storage": "AC0G=B4=P",   "systemd": "AC0G\\x3dB4\\x3dP", "display": "AC0G/B4/P"}
  ],
  "legacy_names": [
    {"input": "my-rx888",             "storage": "my-rx888",             "systemd": "my-rx888"},
    {"input": "B4-100-rx888mk2",      "storage": "B4-100-rx888mk2",      "systemd": "B4-100-rx888mk2"},
    {"input": "AC0G-B4-status.local", "storage": "AC0G-B4-status.local", "systemd": "AC0G-B4-status.local"},
    {"input": "KA9Q_0-10",            "storage": "KA9Q_0-10",            "systemd": "KA9Q_0-10"}
  ],
  "invalid": ["", "   ", "/B4", "AC0G/", "AC0G/B4!", "AC0G\\x5cx3dB4"],
  "display_only": [
    {"input": "AC0G\\x5cx3dB4", "display": "AC0G/B4"}
  ]
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_reporter_id_forms.py`:

```python
"""The three spellings of a reporter id, and the helpers that convert them
(tasks/spec-reporter-id-v372.md §2-§3).

    display   AC0G/B4      what a person types and reads
    storage   AC0G=B4      file names, directory names, config keys
    systemd   AC0G\\x3dB4  unit names, *.wants links, %i

tests/data/reporter_id_vectors.json holds the cases every copy of these
rules must agree on.  psk-recorder, meteor-scatter and wspr-recorder each
carry a byte-identical copy; tests/test_reporter_id_drift.py compares them.
"""
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond.instance import (  # noqa: E402
    InvalidReporterId,
    display_reporter_id,
    display_unit_name,
    parse_instance_arg,
    parse_user_reporter_id,
    systemd_escape,
    systemd_instance,
    unescape_systemd_instance,
    unit_name,
)

VECTORS = json.loads(
    (REPO / "tests" / "data" / "reporter_id_vectors.json").read_text())


class VectorTableTests(unittest.TestCase):
    """instance.py against the shared table."""

    def test_reporter_ids_convert_to_every_form(self):
        for v in VECTORS["reporter_ids"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(parse_user_reporter_id(v["input"]), v["storage"])
                self.assertEqual(parse_instance_arg(v["input"]), v["storage"])
                self.assertEqual(systemd_instance(v["storage"]), v["systemd"])
                self.assertEqual(unit_name("psk-recorder", v["storage"]),
                                 f"psk-recorder@{v['systemd']}.service")
                self.assertEqual(unescape_systemd_instance(v["systemd"]), v["storage"])
                self.assertEqual(display_reporter_id(v["storage"]), v["display"])
                self.assertEqual(display_reporter_id(v["systemd"]), v["display"])

    def test_legacy_names_pass_through(self):
        for v in VECTORS["legacy_names"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(parse_instance_arg(v["input"]), v["storage"])
                self.assertEqual(systemd_escape(v["storage"]), v["systemd"])
                with self.assertRaises(InvalidReporterId):
                    systemd_instance(v["storage"])

    def test_invalid_text_is_refused(self):
        for text in VECTORS["invalid"]:
            with self.subTest(text=text):
                with self.assertRaises(InvalidReporterId):
                    parse_instance_arg(text)
                with self.assertRaises(InvalidReporterId):
                    parse_user_reporter_id(text)

    def test_display_never_shows_an_escape(self):
        for v in VECTORS["display_only"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(display_reporter_id(v["input"]), v["display"])


class HelperTests(unittest.TestCase):

    def test_hyphens_stay_hyphens_toward_systemd(self):
        # systemd-escape(1) would write W1ABC\x2d5: a unit nobody enabled.
        self.assertEqual(systemd_instance("W1ABC-5"), "W1ABC-5")
        self.assertEqual(systemd_instance("KP4MD-RPI-4"), "KP4MD-RPI-4")
        self.assertEqual(unit_name("wspr-recorder", "W1ABC-5"),
                         "wspr-recorder@W1ABC-5.service")

    def test_systemd_instance_takes_only_the_storage_form(self):
        for text in ("AC0G/B4", "AC0G\\x3dB4", "ac0g=b4", "my-rx888"):
            with self.subTest(text=text), self.assertRaises(InvalidReporterId):
                systemd_instance(text)

    def test_escape_undoes_exactly_one_unescape(self):
        self.assertEqual(systemd_escape("AC0G=B4"), "AC0G\\x3dB4")
        # A double-escaped orphan keeps its own name through a round trip,
        # so it can never be mistaken for the live unit.
        orphan = "AC0G\\x5cx3dB4"
        self.assertEqual(systemd_escape(unescape_systemd_instance(orphan)), orphan)

    def test_unit_name_takes_a_suffix(self):
        self.assertEqual(unit_name("wspr-recorder", "AC0G=B4", suffix=".timer"),
                         "wspr-recorder@AC0G\\x3dB4.timer")

    def test_display_unit_name(self):
        cases = {
            "psk-recorder@AC0G\\x3dB4.service": "psk-recorder@AC0G/B4.service",
            "psk-recorder@AC0G=B4.service": "psk-recorder@AC0G/B4.service",
            "psk-recorder@AC0G\\x3dB4": "psk-recorder@AC0G/B4",
            "wspr-recorder@W1ABC-5.service": "wspr-recorder@W1ABC-5.service",
            "radiod@ac0g-b4.service": "radiod@ac0g-b4.service",
            "codar-sounder@bee1-status.local.service":
                "codar-sounder@bee1-status.local.service",
            "wspr-recorder@*": "wspr-recorder@*",
            "hf-timestd.service": "hf-timestd.service",
        }
        for unit, want in cases.items():
            with self.subTest(unit=unit):
                self.assertEqual(display_unit_name(unit), want)

    def test_lower_case_without_a_slash_reads_as_a_legacy_name(self):
        # Review Focus 1: watch commands and `smd config upload` read w1abc-5
        # as a legacy radiod-keyed name; the instance verbs, which take only
        # reporter ids, upper-case it.
        self.assertEqual(parse_instance_arg("w1abc-5"), "w1abc-5")
        self.assertEqual(parse_user_reporter_id("w1abc-5"), "W1ABC-5")
        self.assertEqual(parse_instance_arg("ac0g/b4"), "AC0G=B4")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_reporter_id_forms.py -q --override-ini addopts=`
Expected: FAIL at collection with `ImportError: cannot import name 'display_unit_name' from 'sigmond.instance'`.

- [ ] **Step 4: Add the import**

In `lib/sigmond/instance.py`, replace

```python
import re
import shutil
import tomllib
```

with

```python
import re
import shutil
import string
import tomllib
```

- [ ] **Step 5: Replace `display_reporter_id` and add the new helpers**

In `lib/sigmond/instance.py`, replace the whole of this function (lines 137-144):

```python
def display_reporter_id(reporter_id: str) -> str:
    """Render a stored reporter ID for user-facing display.

    The `=` sentinels in the stored form mark where the user
    originally typed `/`; revert them.  Hyphens are user-intentional
    and stay.  Inverse of `parse_user_reporter_id`.
    """
    return reporter_id.replace("=", "/")
```

with this block (the `to_wsprnet_form` alias below it stays as it is):

```python
def display_reporter_id(reporter_id: str) -> str:
    """Render a reporter ID, in any of its three spellings, for a person.

    Takes the storage form (``AC0G=B4``), systemd's form
    (``AC0G\\x3dB4``) or the display form itself, and returns the
    display form (``AC0G/B4``).  It unescapes until nothing changes,
    so even a double-escaped name (``AC0G\\x5cx3dB4``) never puts a
    ``\\x3d`` on a screen.  Hyphens are user-intentional and stay.
    Inverse of `parse_user_reporter_id`.
    """
    prev = None
    name = reporter_id
    while name != prev:
        prev, name = name, unescape_systemd_instance(name)
    return name.replace("=", "/")


def parse_instance_arg(text: str) -> str:
    """Read an instance name a person typed, or one systemd passed on.

    Like `parse_user_reporter_id`, but a legacy radiod-keyed name
    (``my-rx888``, ``AC0G-B4-status.local``, ``KA9Q_0-10``) passes
    through unchanged instead of failing.  The rule that tells them
    apart: text with a ``/`` or ``=`` is a reporter id; text in lower
    case without either is a legacy name (that is the shape radiod ids
    have); anything else is a reporter id when it validates and a
    legacy name when it does not.  So ``w1abc-5`` reads as a legacy
    name; type ``W1ABC-5``.

    Raises InvalidReporterId on empty text, on text that still holds a
    backslash after one unescape (``AC0G\\x5cx3dB4`` names a different,
    double-escaped unit), and on a reporter id that fails validation.
    """
    if not isinstance(text, str):
        raise InvalidReporterId("instance name must be a string")
    plain = unescape_systemd_instance(text.strip())
    if not plain:
        raise InvalidReporterId("instance name must be a non-empty string")
    if "\\" in plain:
        raise InvalidReporterId(
            f"instance name {text!r} is escaped twice; it names a "
            f"different unit from the one systemd created")
    has_sep = "/" in plain or "=" in plain
    if not has_sep and plain != plain.upper():
        return plain
    try:
        return parse_user_reporter_id(plain)
    except InvalidReporterId:
        if has_sep:
            raise
        return plain


# Characters systemd keeps as they are in a unit's instance name: letters,
# digits, ':', '-', '_' and '.'.  systemctl escapes anything else as \xHH
# when it is handed an unescaped name, which is how every reporter-keyed
# unit on the stations got its name (psk-recorder@AC0G\x3dB4.service).
# A backslash is escaped too (\x5c), so systemd_escape() is the exact
# inverse of one unescape_systemd_instance() and a double-escaped orphan
# keeps its own name.  systemd-escape(1) is NOT this: it also turns '-'
# into \x2d (W1ABC-5 -> W1ABC\x2d5), a unit nobody enabled.
_UNIT_NAME_CHARS = frozenset(string.ascii_letters + string.digits + ":-_.")
_UNIT_SUFFIXES = (".service", ".timer", ".target", ".socket", ".path")


def systemd_escape(name: str) -> str:
    """Escape any instance name the way a unit name carries it.

    Takes a name already in storage form, or a legacy radiod-keyed
    name, and does not validate it.  ``AC0G=B4`` -> ``AC0G\\x3dB4``;
    ``W1ABC-5`` and ``bee1-status.local`` come back unchanged.  Pure
    Python; never calls systemd-escape(1).
    """
    out = []
    for ch in name:
        if ch in _UNIT_NAME_CHARS:
            out.append(ch)
        else:
            out.extend(f"\\x{b:02x}" for b in ch.encode("utf-8"))
    return "".join(out)


def systemd_instance(reporter_id: str) -> str:
    """The instance part of a unit name, for a storage-form reporter ID.

    Validates first, so the display form, systemd's own spelling or
    lower case raise InvalidReporterId instead of being escaped into
    a unit nobody enabled.  ``AC0G=B4`` -> ``AC0G\\x3dB4``.
    """
    validate_reporter_id(reporter_id)
    return systemd_escape(reporter_id)


def unit_name(client: str, reporter_id: str, suffix: str = ".service") -> str:
    """``unit_name('psk-recorder', 'AC0G=B4')`` -> ``psk-recorder@AC0G\\x3dB4.service``."""
    return f"{client}@{systemd_instance(reporter_id)}{suffix}"


def display_unit_name(unit: str) -> str:
    """A unit name as a person should read it.

    ``psk-recorder@AC0G\\x3dB4.service`` -> ``psk-recorder@AC0G/B4.service``.
    A unit without an instance, a radiod-keyed instance, and a glob
    such as ``wspr-recorder@*`` come back unchanged.  For screens and
    messages only: what it returns is not a unit name systemctl knows.
    """
    if "@" not in unit:
        return unit
    head, _, rest = unit.partition("@")
    for suffix in _UNIT_SUFFIXES:
        if rest.endswith(suffix):
            return f"{head}@{display_reporter_id(rest[:-len(suffix)])}{suffix}"
    return f"{head}@{display_reporter_id(rest)}"
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_reporter_id_forms.py tests/test_instance.py -q --override-ini addopts=`
Expected: PASS (10 in the new file, plus `test_instance.py`'s 7).

- [ ] **Step 7: Commit**

```bash
git add tests/data/reporter_id_vectors.json tests/test_reporter_id_forms.py lib/sigmond/instance.py
git commit -m "instance: one set of helpers for the three reporter-id spellings

display_reporter_id now accepts all three spellings.  New:
systemd_escape, systemd_instance, unit_name, display_unit_name,
parse_instance_arg.  tests/data/reporter_id_vectors.json pins what every
copy of these rules must do (tasks/spec-reporter-id-v372.md §3)."
```

- [ ] **Step 8: Mutation check**

Make each change below with the Edit tool, run the test named, see it FAIL, then `git checkout -- lib/sigmond/instance.py`.

| Change in `lib/sigmond/instance.py` | Run | Must fail |
|---|---|---|
| `if ch in _UNIT_NAME_CHARS:` → `if ch in _UNIT_NAME_CHARS and ch != "-":` | `tests/test_reporter_id_forms.py` | `test_hyphens_stay_hyphens_toward_systemd` |
| `prev, name = name, unescape_systemd_instance(name)` → `prev, name = name, name` | same | `test_reporter_ids_convert_to_every_form` |
| delete `validate_reporter_id(reporter_id)` inside `systemd_instance` | same | `test_systemd_instance_takes_only_the_storage_form` |
| delete the three lines starting `if "\\" in plain:` | same | `test_invalid_text_is_refused` |

Finish with `git status --short` printing nothing.

---

### Task 2: Read what systemd hands back in storage form (sigmond; S5-S7, S13, S15, S16, S18, S19, S44)

**Files:**
- Create: `tests/test_reporter_id_inbound.py`
- Modify: `lib/sigmond/instance.py:268-272` (S5), `:691-692` (S7), `:730-731` (S6)
- Modify: `lib/sigmond/harmonize.py:30` (import), `:908-924` (S13), `:1180-1194` (S15), `:1213` (S16)
- Modify: `lib/sigmond/upload.py:14` (import), `:41-64` (S18, S19)
- Modify: `tests/test_upload.py:20-22`
- Modify: `bin/smd:156-161` (import block), `bin/smd:17064-17066` (S44)

**Interfaces:**
- Consumes: `unescape_systemd_instance`, `parse_instance_arg`, `parse_user_reporter_id`, `display_reporter_id`,
  `display_unit_name`, `systemd_escape` from Task 1.
- Produces:
  - `bin/smd` module names `_display_rid`, `_display_unit`, `_parse_instance_arg`, `_systemd_escape`,
    `_unescape_instance` (aliases of the instance.py helpers).  Tasks 4 and 5 use them.
  - `upload.env_path_for(client, instance, base="/etc") -> Path`, accepting any spelling; raises
    `InvalidReporterId` (a `ValueError`) on junk.  `upload.storage_instance` no longer exists.
  - `harmonize._active_instances(client) -> list[str]` returns storage forms.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reporter_id_inbound.py`:

```python
"""What systemd hands back carries its own escapes (AC0G\\x3dB4).  Every
reader turns it into the storage form (AC0G=B4) before it validates,
compares or builds a path (tasks/spec-reporter-id-v372.md §2, boundary 2;
inventory rows S5-S7, S13, S15, S16, S44)."""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import harmonize as H  # noqa: E402
from sigmond import instance as I  # noqa: E402


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_reporter_inbound", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def _completed(stdout):
    """A subprocess.run stand-in that answers every call with `stdout`."""
    return lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout, "")


def _rooted(tmp):
    """A stand-in for pathlib.Path that moves /etc/... and /opt/... under tmp."""
    real = Path

    def P(*parts):
        p = real(*parts)
        s = str(p)
        if s in ("/etc", "/opt") or s.startswith(("/etc/", "/opt/")):
            return real(tmp + s)
        return p
    return P


class ListUnitsFallbackTests(unittest.TestCase):
    """S5: without read access to /etc/<client>/, the instance list comes
    from systemctl, which spells the instance AC0G\\x3dB4."""

    def test_an_escaped_unit_still_names_its_instance(self):
        out = "psk-recorder@AC0G\\x3dB4.service loaded active running PSK\n"
        with mock.patch("subprocess.run", _completed(out)):
            self.assertEqual(I._list_units_for_client("psk-recorder"), ["AC0G=B4"])


class MigrationCandidateTests(unittest.TestCase):
    """S6, S7: `smd admin instance migrate --yes` stops and disables every
    candidate, so the live reporter-keyed unit must never be one."""

    def _detect(self, units_out, env_names=()):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "etc" / "wspr-recorder" / "env"
            env.mkdir(parents=True)
            for name in env_names:
                (env / f"{name}.env").write_text("")
            with mock.patch("subprocess.run", _completed(units_out)), \
                 mock.patch.object(I, "Path", _rooted(tmp)):
                return [(c.client, c.old_instance)
                        for c in I.detect_migration_candidates()]

    def test_the_live_escaped_unit_is_not_legacy(self):
        out = "wspr-recorder@AC0G\\x3dB4.service loaded active running WSPR\n"
        self.assertEqual(self._detect(out), [])

    def test_a_real_legacy_unit_is_still_found(self):
        out = "wspr-recorder@my-rx888.service loaded inactive dead WSPR\n"
        self.assertEqual(self._detect(out), [("wspr-recorder", "my-rx888")])

    def test_an_env_file_in_systemd_spelling_is_not_legacy(self):
        self.assertEqual(self._detect("", env_names=["AC0G\\x3dB4"]), [])


class HarmonizeInboundTests(unittest.TestCase):
    """S13, S15, S16: harmonize reads unit names and finds env files."""

    def test_active_instances_come_back_in_storage_form(self):
        out = "wspr-recorder@AC0G\\x3dB4.service loaded active running WSPR\n"
        with mock.patch("subprocess.run", _completed(out)):
            self.assertEqual(H._active_instances("wspr-recorder"), ["AC0G=B4"])

    def test_resolve_instance_env_reads_the_storage_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "etc" / "wspr-recorder" / "env"
            env.mkdir(parents=True)
            (env / "AC0G=B4.env").write_text("WD_DECODE_VIA_DB=1\n")
            with mock.patch.object(H, "Path", _rooted(tmp)):
                for inst in ("AC0G\\x3dB4", "AC0G=B4"):
                    with self.subTest(inst=inst):
                        self.assertEqual(H._resolve_instance_env("wspr-recorder", inst),
                                         {"WD_DECODE_VIA_DB": "1"})

    def test_decode_rule_finds_the_env_through_the_escaped_unit(self):
        # Passed before this change too: it guards the rewrite of _decode_on.
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "etc" / "wspr-recorder" / "env"
            env.mkdir(parents=True)
            (env / "AC0G=B4.env").write_text("WD_DECODE_VIA_DB=1\n")
            out = "wspr-recorder@AC0G\\x3dB4.service loaded active running WSPR\n"
            with mock.patch.object(H, "Path", _rooted(tmp)), \
                 mock.patch("subprocess.run", _completed(out)):
                self.assertEqual(H.rule_wspr_decode_enabled(None).severity, "pass")

    def test_no_private_unescape_is_left(self):
        src = (REPO / "lib" / "sigmond" / "harmonize.py").read_text()
        self.assertFalse("unicode_escape" in src,
                         "harmonize.py still decodes unit names with unicode_escape")


class WatchConfigLookupTests(unittest.TestCase):
    """S44: hfdl-watch and codar-watch take the instance from a unit name."""

    def test_config_lookup_unescapes_the_unit_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "etc" / "hfdl-recorder" / "AC0G=B4.toml"
            cfg.parent.mkdir(parents=True)
            cfg.write_text('[[radiod]]\nstatus = "b4-status.local"\n')
            with mock.patch.object(smd, "Path", _rooted(tmp)):
                self.assertEqual(
                    smd._radiod_status_from_per_instance_config(
                        "hfdl-recorder", "AC0G\\x3dB4"),
                    "b4-status.local")


if __name__ == "__main__":
    unittest.main()
```

In `tests/test_upload.py`, replace

```python
    def test_storage_form_slash_to_equals(self):
        self.assertEqual(upload.storage_instance("AC0G/S"), "AC0G=S")
        self.assertEqual(upload.storage_instance("my-rx888"), "my-rx888")
```

with

```python
    def test_storage_form_slash_to_equals(self):
        base = str(self.base)
        self.assertEqual(upload.env_path_for("x", "AC0G/S", base=base).name, "AC0G=S.env")
        self.assertEqual(upload.env_path_for("x", "my-rx888", base=base).name, "my-rx888.env")

    def test_escaped_instance_finds_the_storage_env_file(self):
        # S19: systemd's spelling must reach AC0G=B4.env, not AC0G\x3dB4.env.
        env = self._env("psk-recorder", "AC0G=B4")
        env.parent.mkdir(parents=True)
        env.write_text("X=1\n")
        self.assertEqual(
            upload.env_path_for("psk-recorder", "AC0G\\x3dB4", base=str(self.base)), env)

    def test_escaped_instance_writes_no_shadow_file(self):
        path, *_ = upload.apply_enable(
            "wspr-recorder", "AC0G\\x3dB4", True, base=str(self.base))
        self.assertEqual(path.name, "AC0G=B4.env")
        self.assertEqual([p.name for p in path.parent.iterdir()], ["AC0G=B4.env"])

    def test_lower_case_finds_the_upper_case_file(self):
        # Review Focus 1: w1abc-5 reads as a legacy name, but the existing
        # W1ABC-5.env wins over a new shadow file.
        env = self._env("psk-recorder", "W1ABC-5")
        env.parent.mkdir(parents=True)
        env.write_text("")
        self.assertEqual(
            upload.env_path_for("psk-recorder", "w1abc-5", base=str(self.base)), env)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_reporter_id_inbound.py tests/test_upload.py -q --override-ini addopts=`
Expected: 8 FAIL — `test_an_escaped_unit_still_names_its_instance` (got `[]`),
`test_the_live_escaped_unit_is_not_legacy`, `test_an_env_file_in_systemd_spelling_is_not_legacy`,
`test_active_instances_come_back_in_storage_form`, `test_no_private_unescape_is_left`,
`test_config_lookup_unescapes_the_unit_instance` (got `None`), `test_escaped_instance_finds_the_storage_env_file`,
`test_escaped_instance_writes_no_shadow_file`.  The rest pass already.

- [ ] **Step 3: Fix `instance.py` (S5, S6, S7)**

In `_list_units_for_client`, replace

```python
            cli, _, rid = base.partition("@")
            if cli != client or not rid:
                continue
            try:
                validate_reporter_id(rid)
```

with

```python
            cli, _, rid = base.partition("@")
            if cli != client or not rid:
                continue
            # systemd lists the instance in its own spelling (AC0G\x3dB4);
            # validate and return the storage form (AC0G=B4).
            rid = unescape_systemd_instance(rid)
            try:
                validate_reporter_id(rid)
```

In `detect_migration_candidates`, replace

```python
        for env_file in sorted(env_dir.glob("*.env")):
            name = env_file.stem
```

with

```python
        for env_file in sorted(env_dir.glob("*.env")):
            # An env file named in systemd's spelling (a shadow some older
            # smd wrote: AC0G\x3dB4.env) is the same reporter-keyed instance.
            name = unescape_systemd_instance(env_file.stem)
```

and further down replace

```python
            if not name:
                continue
            try:
                validate_reporter_id(name)
                continue                # already reporter-keyed
```

with

```python
            if not name:
                continue
            # systemd lists the live reporter-keyed unit as AC0G\x3dB4;
            # unescaped it validates, so it is not a legacy candidate.
            name = unescape_systemd_instance(name)
            try:
                validate_reporter_id(name)
                continue                # already reporter-keyed
```

- [ ] **Step 4: Fix `harmonize.py` (S13, S15, S16)**

Replace `from .sysview import SystemView` (line 30) with

```python
from .instance import unescape_systemd_instance
from .sysview import SystemView
```

Replace the nested `_decode_on` (lines 908-924):

```python
    def _decode_on(unit: str) -> bool:
        inst = unit[len("wspr-recorder@"):-len(".service")]
        forms = {inst}
        if "\\x" in inst:
            try:
                forms.add(inst.encode().decode("unicode_escape"))
            except Exception:                          # noqa: BLE001
                pass
        for name in forms:
            envf = env_dir / f"{name}.env"
            if not envf.exists():
                continue
            for line in envf.read_text().splitlines():
                line = line.strip()
                if line.startswith("WD_DECODE_VIA_DB") and "=" in line:
                    return line.split("=", 1)[1].strip() not in ("", "0")
        return False
```

with

```python
    def _decode_on(unit: str) -> bool:
        env = _resolve_instance_env(
            "wspr-recorder", unit[len("wspr-recorder@"):-len(".service")])
        return env.get("WD_DECODE_VIA_DB", "").strip() not in ("", "0")
```

Replace `_resolve_instance_env` (lines 1180-1194):

```python
def _resolve_instance_env(client: str, inst: str) -> dict:
    """Per-instance env for `<client>@<inst>`.  Handles systemd's escaped
    instance form (e.g. AC0G\\x3dS) vs the env-file name (AC0G=S.env)."""
    env_dir = Path(f"/etc/{client}/env")
    forms = {inst}
    if "\\x" in inst:
        try:
            forms.add(inst.encode().decode("unicode_escape"))
        except Exception:                                  # noqa: BLE001
            pass
    for name in forms:
        f = env_dir / f"{name}.env"
        if f.exists():
            return _read_env_file(f)
    return {}
```

with

```python
def _resolve_instance_env(client: str, inst: str) -> dict:
    """Per-instance env for `<client>@<inst>`.  `inst` may come straight
    from a unit name (AC0G\\x3dS); the env file carries the storage form
    (AC0G=S.env)."""
    f = Path(f"/etc/{client}/env") / f"{unescape_systemd_instance(inst)}.env"
    return _read_env_file(f) if f.exists() else {}
```

In `_active_instances`, replace

```python
            names.append(u[len(f"{client}@"):-len(".service")])
```

with

```python
            names.append(unescape_systemd_instance(
                u[len(f"{client}@"):-len(".service")]))
```

- [ ] **Step 5: Fix `upload.py` (S18, S19)**

Replace `from typing import List, Tuple` (line 14) with

```python
from typing import List, Tuple

from .instance import InvalidReporterId, parse_instance_arg, parse_user_reporter_id
```

Replace `storage_instance` and `env_path_for` (lines 41-64), from `def storage_instance(instance: str) -> str:`
through `    return canonical`, with

```python
def env_path_for(client: str, instance: str, base: str = "/etc") -> Path:
    """The per-instance env file for an instance named in any spelling.

    The display form (AC0G/S), the storage form (AC0G=S) and systemd's
    spelling (AC0G\\x3dS) all name <base>/<client>/env/AC0G=S.env.  A
    legacy radiod-keyed name (my-rx888) keeps its case.  Lower case with
    no slash (w1abc-5) reads as a legacy name, but when only the
    upper-case file (W1ABC-5.env) exists, that file wins, so the flag
    lands in it instead of in a new shadow file.  Raises
    InvalidReporterId (a ValueError) on text that names nothing.
    """
    env_dir = Path(base) / client / "env"
    canonical = env_dir / f"{parse_instance_arg(instance)}.env"
    if canonical.exists():
        return canonical
    try:
        strict = env_dir / f"{parse_user_reporter_id(instance)}.env"
    except InvalidReporterId:
        return canonical
    return strict if strict.exists() else canonical
```

`cmd_config_upload` already catches `ValueError` around `apply_enable` (`commands/config.py:1363-1370`), so an
invalid instance prints an error instead of a traceback.

- [ ] **Step 6: Fix `bin/smd` (import block and S44)**

In the `try:` import block near the top, replace

```python
        has_failure as _diag_drop_in_has_failure,
    )
    _HAS_SIGMOND_LIB = True
```

with

```python
        has_failure as _diag_drop_in_has_failure,
    )
    # Reporter-id spellings (tasks/spec-reporter-id-v372.md): a person sees
    # AC0G/B4, files hold AC0G=B4, unit names carry AC0G\x3dB4.
    from sigmond.instance import (
        display_reporter_id       as _display_rid,
        display_unit_name         as _display_unit,
        parse_instance_arg        as _parse_instance_arg,
        systemd_escape            as _systemd_escape,
        unescape_systemd_instance as _unescape_instance,
    )
    _HAS_SIGMOND_LIB = True
```

In `_radiod_status_from_per_instance_config`, replace

```python
    cfg_path = Path(f'/etc/{client}/{instance}.toml')
    if not cfg_path.exists():
        return None
```

with

```python
    # A watch that found its instance in a unit name holds systemd's
    # spelling (AC0G\x3dB4); the config file carries AC0G=B4.toml.
    instance = _unescape_instance(instance)
    cfg_path = Path(f'/etc/{client}/{instance}.toml')
    if not cfg_path.exists():
        return None
```

Leave hfdl-watch's and codar-watch's fallback `data_dir` (`/var/lib/<client>/{instance}`) alone: codar's unit
creates that directory from `%i`, so the systemd spelling stays right there.  hf-tec-watch (S45) needs nothing:
hf-tec instances carry radiod ids.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_reporter_id_inbound.py tests/test_upload.py tests/test_harmonize.py tests/test_instance.py -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/pytest tests -q --override-ini addopts=`
Expected: PASS (about 4280; no failures).

- [ ] **Step 9: Commit**

```bash
git add tests/test_reporter_id_inbound.py tests/test_upload.py lib/sigmond/instance.py \
        lib/sigmond/harmonize.py lib/sigmond/upload.py bin/smd
git commit -m "reporter ids: read what systemd hands back in storage form

The live AC0G\\x3dB4 unit no longer counts as a migration candidate
(migrate --yes stopped it), the unprivileged instance list finds it,
harmonize and the upload flag find AC0G=B4.env, and hfdl/codar watch find
AC0G=B4.toml.  Inventory rows S5-S7, S13, S15, S16, S18, S19, S44."
```

- [ ] **Step 10: Mutation check**

| Change | Run | Must fail |
|---|---|---|
| `instance.py`: delete `rid = unescape_systemd_instance(rid)` | `tests/test_reporter_id_inbound.py` | `test_an_escaped_unit_still_names_its_instance` |
| `instance.py`: delete `name = unescape_systemd_instance(name)` (the one after `if not name:`) | same | `test_the_live_escaped_unit_is_not_legacy` |
| `harmonize.py`: in `_resolve_instance_env`, `unescape_systemd_instance(inst)` → `inst` | same | `test_resolve_instance_env_reads_the_storage_file` |
| `bin/smd`: delete `instance = _unescape_instance(instance)` | same | `test_config_lookup_unescapes_the_unit_instance` |
| `upload.py`: `f"{parse_instance_arg(instance)}.env"` → `f"{instance}.env"` | `tests/test_upload.py` | `test_escaped_instance_finds_the_storage_env_file` |

After each, `git checkout -- <file>`.  Finish with `git status --short` printing nothing.

---

### Task 3: Unit names leave through the helpers (sigmond library; S4, S8, S12, S24, S29)

**Files:**
- Create: `tests/test_reporter_id_unit_names.py`
- Modify: `lib/sigmond/instance.py:171` (comment), `:194` (S4), `:666-668` (`MigrationCandidate.unit_name`), `:832` (S8)
- Modify: `lib/sigmond/lifecycle.py:279` (import), `:333` (S12)
- Modify: `lib/sigmond/tui/screens/lifecycle.py:187` (import), `:223` (S24)
- Modify: `lib/sigmond/tui/screens/logs.py:107` (new helper before it), `:305` (S29), `:318` (status line)
- Modify: `tests/test_uninstall.py:232` (fixture spelling)

**Interfaces:**
- Consumes: `systemd_escape`, `unit_name`, `display_unit_name` from Task 1.
- Produces:
  - `InstancePaths.unit_name` now holds the systemd spelling (`psk-recorder@AC0G\x3dB4.service`).  Every smd line
    that prints it changes in Task 4.
  - `UnitRef.unit` (from `lifecycle._expand_template`) holds the systemd spelling; `UnitRef.instance` keeps the
    storage form.  Task 4 changes every smd line that prints `UnitRef.unit`.
  - `sigmond.tui.screens.logs._instance_unit(component: str, instance: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reporter_id_unit_names.py`:

```python
"""Unit names leave sigmond in systemd's spelling (AC0G\\x3dB4), built by
the helpers in instance.py; the screens show them in the display form
(tasks/spec-reporter-id-v372.md §2, boundary 2; inventory rows S4, S8, S12,
S24, S29)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import instance as I  # noqa: E402
from sigmond import lifecycle  # noqa: E402

try:
    import textual  # noqa: F401
    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False


def _rooted(tmp):
    real = Path

    def P(*parts):
        p = real(*parts)
        s = str(p)
        if s in ("/etc", "/opt") or s.startswith(("/etc/", "/opt/")):
            return real(tmp + s)
        return p
    return P


class InstancePathsTests(unittest.TestCase):
    """S4: the one unit name sigmond builds for an instance."""

    def test_unit_name_is_in_systemd_spelling_and_files_keep_storage(self):
        p = I.instance_paths("psk-recorder", "AC0G=B4")
        self.assertEqual(p.unit_name, "psk-recorder@AC0G\\x3dB4.service")
        self.assertEqual(p.config, Path("/etc/psk-recorder/AC0G=B4.toml"))
        self.assertEqual(p.env, Path("/etc/psk-recorder/env/AC0G=B4.env"))


class MigrateUnitNamesTests(unittest.TestCase):
    """S8: migrate addresses both units by the names systemd gave them."""

    def test_dry_run_names_both_units_correctly(self):
        steps = I.migrate_one_instance("wspr-recorder", "my-rx888", "AC0G=B4",
                                       dry_run=True)
        self.assertIn("would systemctl stop wspr-recorder@my-rx888.service", steps)
        self.assertIn("would systemctl enable --now wspr-recorder@AC0G\\x3dB4.service",
                      steps)


class ExpandTemplateTests(unittest.TestCase):
    """S12: lifecycle's UnitRef.unit is what smd start/stop/restart pass on."""

    def _expand(self, listed, env_names=()):
        units_json = json.dumps([{"unit": u} for u in listed])

        def run(argv, **kw):
            out = units_json if argv[1] == "list-units" else ""
            return subprocess.CompletedProcess(argv, 0, out, "")
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "etc" / "psk-recorder" / "env"
            env.mkdir(parents=True)
            for name in env_names:
                (env / f"{name}.env").write_text("")
            with mock.patch.object(lifecycle, "Path", _rooted(tmp)), \
                 mock.patch.object(lifecycle.subprocess, "run", run):
                return lifecycle._expand_template(
                    "psk-recorder", "psk-recorder@.service", "test", "service")

    def test_unit_carries_systemd_spelling_instance_carries_storage(self):
        refs = self._expand(["psk-recorder@AC0G\\x3dB4.service",
                             "psk-recorder@bee1-status.local.service"])
        self.assertEqual(
            sorted((r.unit, r.instance) for r in refs),
            [("psk-recorder@AC0G\\x3dB4.service", "AC0G=B4"),
             ("psk-recorder@bee1-status.local.service", "bee1-status.local")])

    def test_a_double_escaped_orphan_keeps_its_own_unit_name(self):
        # Review Focus 2: an orphan left by an older double-escape bug must
        # never resolve to the live unit, or stopping the orphan stops it.
        refs = self._expand(["psk-recorder@AC0G\\x3dB4.service",
                             "psk-recorder@AC0G\\x5cx3dB4.service"],
                            env_names=["AC0G=B4"])
        got = {r.unit: r.orphaned for r in refs}
        self.assertEqual(got, {"psk-recorder@AC0G\\x3dB4.service": False,
                               "psk-recorder@AC0G\\x5cx3dB4.service": True})


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class TuiUnitNameTests(unittest.TestCase):
    """S24, S29: the Lifecycle and Logs screens."""

    def test_lifecycle_row_shows_the_display_form(self):
        from sigmond.lifecycle import UnitRef
        from sigmond.tui.screens import lifecycle as screen
        ref = UnitRef(component="psk-recorder",
                      unit="psk-recorder@AC0G\\x3dB4.service",
                      template="psk-recorder@.service", instance="AC0G=B4",
                      kind="service", source="test")
        topo = SimpleNamespace(enabled_components=lambda: ["psk-recorder"])
        with mock.patch("sigmond.topology.load_topology", lambda *a, **k: topo), \
             mock.patch("sigmond.catalog.load_catalog", lambda *a, **k: {}), \
             mock.patch("sigmond.lifecycle.resolve_units", lambda *a, **k: [ref]), \
             mock.patch.object(screen, "_systemctl_show_batch",
                               lambda units, props: {u: {"ActiveState": "active"}
                                                     for u in units}):
            data = screen._gather()
        self.assertIsNone(data.error)
        self.assertEqual([r.display for r in data.rows], ["psk-recorder@AC0G/B4"])
        self.assertEqual([r.unit for r in data.rows], ["psk-recorder@AC0G\\x3dB4.service"])

    def test_logs_instance_unit_is_spelled_for_systemd(self):
        from sigmond.tui.screens import logs
        self.assertEqual(logs._instance_unit("psk-recorder", "AC0G=B4"),
                         "psk-recorder@AC0G\\x3dB4.service")
        self.assertEqual(logs._instance_unit("psk-recorder", "my-rx888"),
                         "psk-recorder@my-rx888.service")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_reporter_id_unit_names.py -q --override-ini addopts=`
Expected: 6 FAIL.  `p.unit_name` reads `psk-recorder@AC0G=B4.service`; the dry run says `enable --now
wspr-recorder@AC0G=B4.service`; the expand tests get `@AC0G=B4.service` and the orphan resolves to
`@AC0G\x3dB4.service`; the lifecycle row shows `psk-recorder@AC0G\x3dB4`; `logs._instance_unit` does not exist.

- [ ] **Step 3: Implement in `instance.py` (S4, S8)**

Replace the comment on line 171

```python
    unit_name: str      # <client>@<reporter_id>.service
```

with

```python
    unit_name: str      # <client>@<systemd form>.service, e.g. psk-recorder@AC0G\x3dB4.service
```

In `instance_paths`, replace

```python
        unit_name=f"{client}@{reporter_id}.service",
```

with

```python
        unit_name=unit_name(client, reporter_id),
```

In `MigrationCandidate`, replace

```python
    @property
    def unit_name(self) -> str:
        return f"{self.client}@{self.old_instance}.service"
```

with

```python
    @property
    def unit_name(self) -> str:
        return f"{self.client}@{systemd_escape(self.old_instance)}.service"
```

In `migrate_one_instance`, replace

```python
    old_unit = f"{client}@{old_instance}.service"
```

with

```python
    old_unit = f"{client}@{systemd_escape(old_instance)}.service"
```

`new_unit = new_paths.unit_name` now carries `\x3d`, so `new_dropin` names the drop-in directory systemd reads
(`wspr-recorder@AC0G\x3dB4.service.d`).

- [ ] **Step 4: Implement in `lifecycle.py` (S12)**

Replace line 279

```python
    from .instance import unescape_systemd_instance
```

with

```python
    from .instance import systemd_escape, unescape_systemd_instance
```

and replace line 333

```python
        unit_name = template.replace('@.', f"@{instance}.")
```

with

```python
        # `instance` holds the storage form (AC0G=B4); the unit carries
        # systemd's spelling (AC0G\x3dB4), so systemctl gets the name it
        # created and never warns "Invalid unit name".
        unit_name = template.replace('@.', f"@{systemd_escape(instance)}.")
```

- [ ] **Step 5: Implement in the two screens (S24, S29)**

In `lib/sigmond/tui/screens/lifecycle.py`, inside `_gather`, replace

```python
        from ...catalog import load_catalog
```

with

```python
        from ...catalog import load_catalog
        from ...instance import display_unit_name
```

and replace

```python
                        display=u.unit.removesuffix(".service"),
```

with

```python
                        display=display_unit_name(u.unit).removesuffix(".service"),
```

In `lib/sigmond/tui/screens/logs.py`, insert before `def _resolve_unit_names(component: str) -> list:`

```python
def _instance_unit(component: str, instance: str) -> str:
    """The unit for one dropdown choice: a storage-form reporter id
    (AC0G=B4) or a legacy radiod-keyed name, spelled as systemd spells it
    (psk-recorder@AC0G\\x3dB4.service)."""
    from ...instance import systemd_escape
    return f"{component}@{systemd_escape(instance)}.service"


```

replace

```python
                target_unit = f"{comp}@{instance}.service"
```

with

```python
                target_unit = _instance_unit(comp, instance)
```

and replace

```python
            status = f"following journal for: {', '.join(units)}"
```

with

```python
            from ...instance import display_unit_name
            status = ("following journal for: "
                      + ", ".join(display_unit_name(u) for u in units))
```

- [ ] **Step 6: Give the uninstall fixture systemd's spelling**

In `tests/test_uninstall.py`, replace

```python
            (wants / "wspr-recorder@AC0G=S.service").symlink_to(
```

with

```python
            (wants / "wspr-recorder@AC0G\\x3dS.service").symlink_to(
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_reporter_id_unit_names.py tests/test_lifecycle.py tests/test_tui_logs.py tests/test_uninstall.py -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/pytest tests -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add tests/test_reporter_id_unit_names.py tests/test_uninstall.py lib/sigmond/instance.py \
        lib/sigmond/lifecycle.py lib/sigmond/tui/screens/lifecycle.py lib/sigmond/tui/screens/logs.py
git commit -m "reporter ids: build unit names with the helpers

InstancePaths.unit_name and UnitRef.unit now carry systemd's spelling
(AC0G\\x3dB4), so systemctl gets the name it created and a double-escaped
orphan keeps its own name instead of aliasing the live unit.  The
Lifecycle and Logs screens show AC0G/B4.  Inventory rows S4, S8, S12,
S24, S29."
```

- [ ] **Step 10: Mutation check**

| Change | Run | Must fail |
|---|---|---|
| `lifecycle.py`: `f"@{systemd_escape(instance)}."` → `f"@{instance}."` | `tests/test_reporter_id_unit_names.py` | both `ExpandTemplateTests` |
| `instance.py`: `unit_name=unit_name(client, reporter_id),` → `unit_name=f"{client}@{reporter_id}.service",` | same | `test_unit_name_is_in_systemd_spelling_and_files_keep_storage` |
| `tui/screens/lifecycle.py`: `display_unit_name(u.unit)` → `u.unit` | same | `test_lifecycle_row_shows_the_display_form` |

After each, `git checkout -- <file>`.

---

### Task 4: smd's instance and lifecycle verbs show AC0G/B4 (sigmond; S38 in part, S52, S53)

After Task 3, every line that prints `InstancePaths.unit_name` or `UnitRef.unit` shows `\x3d`.  This task routes
them all through the display helpers, and moves the instance verbs off the private `_display_reporter`.

**Files:**
- Create: `tests/test_smd_instance_display.py`
- Modify: `bin/smd` — every `{u.unit}`, `{unit.unit}`, `{unit_ref.unit}` (lines 7594, 10380-10401, 11087, 11366-11372,
  12324-12326, 12398-12436, 13240); 11442; 13523; the instance verbs at 21350, 21366, 21379, 21411, 21458, 21485,
  21560-21641, 21738, 21762.

**Interfaces:**
- Consumes: `_display_unit` (Task 2's import block); `inst.display_reporter_id`; `InstancePaths.unit_name` in systemd
  spelling (Task 3).
- Produces: nothing new for later tasks.  After this task only the watch commands still call `_display_reporter`;
  Task 5 removes it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_smd_instance_display.py`:

```python
"""smd's instance and lifecycle verbs show a person AC0G/B4 and hand
systemctl AC0G\\x3dB4 (tasks/spec-reporter-id-v372.md §4; inventory rows
S52, S53, plus the lifecycle verbs, which print UnitRef.unit and so began
to carry systemd's spelling when Task 3 escaped it)."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_instance_display", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class InstanceVerbTests(unittest.TestCase):

    def test_disable_shows_the_display_form_and_sends_the_systemd_form(self):
        calls = []

        def run(argv, **kw):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "", "")
        out = io.StringIO()
        with mock.patch.object(smd, "_need_root", lambda *a, **k: False), \
             mock.patch("subprocess.run", run), contextlib.redirect_stdout(out):
            rc = smd.cmd_instance_disable(
                argparse.Namespace(client="psk-recorder", reporter_id="AC0G/B4"))
        self.assertEqual(rc, 0)
        self.assertIn(["systemctl", "stop", "psk-recorder@AC0G\\x3dB4.service"], calls)
        self.assertIn(["systemctl", "disable", "psk-recorder@AC0G\\x3dB4.service"], calls)
        self.assertIn("psk-recorder@AC0G/B4 stopped and disabled", out.getvalue())
        self.assertNotIn("\\x3d", out.getvalue())

    def test_show_names_the_instance_and_prints_a_working_journal_command(self):
        inst = smd._instance_module()
        found = inst.Instance(paths=inst.instance_paths("psk-recorder", "AC0G=B4"),
                              has_config=True, has_env=True, has_sources=True)
        out = io.StringIO()
        with mock.patch.object(inst, "get_instance", lambda c, r: found), \
             mock.patch("subprocess.run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0, "active\n", "")), \
             contextlib.redirect_stdout(out):
            rc = smd.cmd_instance_show(
                argparse.Namespace(client="psk-recorder", reporter_id="AC0G\\x3dB4"))
        text = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("unit:     psk-recorder@AC0G/B4", text)
        # A printed command uses the form that command accepts (spec §4).
        self.assertIn("journal:  journalctl -u 'psk-recorder@AC0G\\x3dB4.service'", text)


class LifecycleVerbOutputTests(unittest.TestCase):

    def test_no_lifecycle_message_prints_a_raw_unit_name(self):
        src = (REPO / "bin" / "smd").read_text()
        raw = re.findall(r"\{(?:unit|u|unit_ref)\.unit\}", src)
        self.assertEqual(raw, [], "smd prints UnitRef.unit without _display_unit()")
        self.assertNotIn("following journal for: {\", \".join(unit_names)}", src)

    def test_display_unit_is_the_instance_helper(self):
        from sigmond.instance import display_unit_name
        self.assertIs(smd._display_unit, display_unit_name)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_smd_instance_display.py -q --override-ini addopts=`
Expected: 3 FAIL — the disable output ends `psk-recorder@AC0G\x3dB4.service stopped and disabled`; show prints
`unit:     psk-recorder@AC0G\x3dB4.service`; the regex finds 22 raw sites.  `test_display_unit_is_the_instance_helper`
passes (Task 2 added the alias).

- [ ] **Step 3: Route the lifecycle verbs through `_display_unit`**

Use the Edit tool with `replace_all: true` three times on `bin/smd`:

- `{u.unit}` → `{_display_unit(u.unit)}`
- `{unit.unit}` → `{_display_unit(unit.unit)}`
- `{unit_ref.unit}` → `{_display_unit(unit_ref.unit)}`

Then replace (in the stop verb, line 11442)

```python
        _err(f'{unit_name}: {err}')
```

with

```python
        _err(f'{_display_unit(unit_name)}: {err}')
```

and (in `smd log`, line 13523)

```python
    _info(f'following journal for: {", ".join(unit_names)}')
```

with

```python
    _info('following journal for: '
          + ', '.join(_display_unit(u) for u in unit_names))
```

Radiod units (10380-10401, 11087) carry no reporter id; `_display_unit` returns them unchanged.

- [ ] **Step 4: Move the instance verbs off `_display_reporter`**

Replace, in `cmd_instance_list`,

```python
        print(f'  {i.client:<22} {_display_reporter(i.reporter_id, inst):<18} '
```

with

```python
        print(f'  {i.client:<22} {inst.display_reporter_id(i.reporter_id):<18} '
```

Replace both occurrences (show and remove) of

```python
    display_rid = _display_reporter(args.reporter_id, inst)
```

with

```python
    display_rid = inst.display_reporter_id(args.reporter_id)
```

In `cmd_instance_add`, replace `    display_rid = _display_reporter(reporter_id, inst)` with
`    display_rid = inst.display_reporter_id(reporter_id)`.  In `cmd_instance_migrate`, replace
`        display_rid = _display_reporter(reporter_id, inst)` with
`        display_rid = inst.display_reporter_id(reporter_id)`.

- [ ] **Step 5: `show` and `remove` (S52)**

In `cmd_instance_show`, replace

```python
    print(f'  unit:     {i.paths.unit_name}')
```

with

```python
    print(f'  unit:     {args.client}@{display_rid}')
    print(f"  journal:  journalctl -u '{i.paths.unit_name}'")
```

In `cmd_instance_remove`, replace

```python
        _info(f'unit {i.paths.unit_name} is NOT touched; '
```

with

```python
        _info(f'the {args.client}@{display_rid} unit is NOT touched; '
```

- [ ] **Step 6: `enable` and `disable` (S52)**

In `cmd_instance_enable`, replace

```python
    try:
        paths = inst.instance_paths(args.client, args.reporter_id)
    except inst.InvalidReporterId as exc:
        _err(str(exc))
        return 1
    if not _check_systemd_template(args.client):
```

with

```python
    try:
        paths = inst.instance_paths(args.client, args.reporter_id)
    except inst.InvalidReporterId as exc:
        _err(str(exc))
        return 1
    label = f'{args.client}@{inst.display_reporter_id(args.reporter_id)}'
    if not _check_systemd_template(args.client):
```

then replace

```python
        _err(f'{paths.config} declares no radiod source — not enabling '
             f'{paths.unit_name}, because it would fail on every start')
```

with

```python
        _err(f'{paths.config} declares no radiod source — not enabling '
             f'{label}, because it would fail on every start')
```

replace

```python
        _info(f'    smd admin instance edit {args.client} {args.reporter_id}')
```

with

```python
        _info(f'    smd admin instance edit {args.client} '
              f'{inst.display_reporter_id(args.reporter_id)}')
```

and replace

```python
            _err(f'systemctl {action} {paths.unit_name} failed '
                 f'(exit {result.returncode})')
            return result.returncode
    _ok(f'{paths.unit_name} enabled and started')
```

with

```python
            _err(f'systemctl {action} {label} failed '
                 f'(exit {result.returncode})')
            return result.returncode
    _ok(f'{label} enabled and started')
```

In `cmd_instance_disable`, replace

```python
    try:
        paths = inst.instance_paths(args.client, args.reporter_id)
    except inst.InvalidReporterId as exc:
        _err(str(exc))
        return 1
    import subprocess
    for action in ('stop', 'disable'):
```

with

```python
    try:
        paths = inst.instance_paths(args.client, args.reporter_id)
    except inst.InvalidReporterId as exc:
        _err(str(exc))
        return 1
    label = f'{args.client}@{inst.display_reporter_id(args.reporter_id)}'
    import subprocess
    for action in ('stop', 'disable'):
```

and replace

```python
            _warn(f'systemctl {action} {paths.unit_name} exited '
                  f'{result.returncode} (may be already stopped)')
    _ok(f'{paths.unit_name} stopped and disabled')
```

with

```python
            _warn(f'systemctl {action} {label} exited '
                  f'{result.returncode} (may be already stopped)')
    _ok(f'{label} stopped and disabled')
```

The `systemctl` argv keeps `paths.unit_name`, now in systemd's spelling, so systemctl no longer warns "Invalid unit
name" into the error text.

- [ ] **Step 7: `migrate` (S53)**

In `cmd_instance_migrate`, replace

```python
            _ok(f'migrated {c.client}@{c.old_instance} → '
                f'{c.client}@{reporter_id}')
```

with

```python
            _ok(f'migrated {c.client}@{c.old_instance} → '
                f'{c.client}@{display_rid}')
```

`c.old_instance` now holds a legacy radiod name in storage form (Task 2), so the other migrate lines need nothing.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_smd_instance_display.py tests/test_instance_guard.py -q --override-ini addopts=`
Expected: PASS.  Then `grep -n "_display_reporter(" bin/smd` must list only the definition and the six watch-command
calls (17747, 17752, 19135, 20510, 20515, 20836 at `973838b`).

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/pytest tests -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add tests/test_smd_instance_display.py bin/smd
git commit -m "smd: instance and lifecycle verbs show AC0G/B4, hand systemctl AC0G\\x3dB4

Every line that prints a unit name goes through display_unit_name; the
instance verbs use instance.display_reporter_id instead of smd's private
copy; show adds a journalctl command in the form journalctl accepts.
Inventory rows S52, S53."
```

- [ ] **Step 11: Mutation check**

| Change in `bin/smd` | Run | Must fail |
|---|---|---|
| in `cmd_instance_disable`, `label = f'{args.client}@{inst.display_reporter_id(args.reporter_id)}'` → `label = paths.unit_name` | `tests/test_smd_instance_display.py` | `test_disable_shows_the_display_form_and_sends_the_systemd_form` |
| one `{_display_unit(unit.unit)}` (in `cmd_restart`) → `{unit.unit}` | same | `test_no_lifecycle_message_prints_a_raw_unit_name` |

After each, `git checkout -- bin/smd`.

---

### Task 5: Operator input on the watch and sources verbs; no reporter ids as ssh hosts (sigmond; S37-S39, S41-S43, S46, S48, S50, S54, S55)

**Files:**
- Create: `tests/test_smd_watch_instance.py`
- Modify: `bin/smd` — `_watch_preflight` (17016-17044, and a new helper before it), meteor-watch (17679-17758),
  verifier-watch (17791-17844), wspr-watch (18260-18415, 19135), psk-watch (20411-20520), mag-watch line 20836,
  `_is_valid_per_instance_client` (16003-16022, and two new helpers after it), `cmd_sources_add` (16160-16163),
  `cmd_sources_remove` (16200-16201), `cmd_sources_apply` (16456-16470), `_BASH_COMPLETION` line 14938,
  `_radiod_watch_candidate_hosts` (22451-22491); delete `_systemd_escape_reporter` and `_display_reporter`
  (21282-21326).

**Interfaces:**
- Consumes: `_parse_instance_arg`, `_systemd_escape`, `_display_rid`, `_display_unit`, `_unescape_instance` (Task 2's
  import block).
- Produces, in `bin/smd`:
  - `_watch_instance(raw, client: str) -> tuple` — `(storage, unit)` or `(None, None)`; raises `ValueError`.
  - `_normalize_instance_client(client: str) -> str`; raises `ValueError`.
  - `_restart_hint(client: str, rid: str) -> str`.
  - `_systemd_escape_reporter` and `_display_reporter` no longer exist.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_smd_watch_instance.py`:

```python
"""Operator input enters through the parser, then leaves for systemd in
systemd's spelling (tasks/spec-reporter-id-v372.md §2; inventory rows S37,
S39, S41, S42, S46, S48, S50, S54, S55)."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_watch_instance", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class WatchInstanceArgTests(unittest.TestCase):

    def test_every_spelling_names_the_same_unit(self):
        for text in ("AC0G/B4", "AC0G=B4", "AC0G\\x3dB4", "ac0g/b4"):
            with self.subTest(text=text):
                self.assertEqual(smd._watch_instance(text, "psk-recorder"),
                                 ("AC0G=B4", "psk-recorder@AC0G\\x3dB4.service"))

    def test_hyphens_stay_hyphens(self):
        # S37: systemd-escape turned W1ABC-5 into W1ABC\x2d5, a unit nobody enabled.
        self.assertEqual(smd._watch_instance("W1ABC-5", "meteor-scatter"),
                         ("W1ABC-5", "meteor-scatter@W1ABC-5.service"))

    def test_a_legacy_name_passes_through(self):
        self.assertEqual(smd._watch_instance("my-rx888", "wspr-recorder"),
                         ("my-rx888", "wspr-recorder@my-rx888.service"))

    def test_no_instance(self):
        self.assertEqual(smd._watch_instance(None, "psk-recorder"), (None, None))

    def test_junk_is_refused(self):
        with self.assertRaises(ValueError):
            smd._watch_instance("AC0G/", "psk-recorder")

    def test_the_private_copies_are_gone(self):
        self.assertFalse(hasattr(smd, "_systemd_escape_reporter"))
        self.assertFalse(hasattr(smd, "_display_reporter"))


class WatchCommandUnitTests(unittest.TestCase):
    """S39, S41, S42, S46: each watch asks systemd about the right unit."""

    def _patterns(self, cmd, instance="AC0G/B4"):
        seen = []

        def preflight(label, pattern, hint=''):
            seen.append(pattern)
            return 1
        args = argparse.Namespace(instance=instance, recent=0, verbose=False,
                                  backlog=0)
        with mock.patch.object(smd, "_watch_preflight", preflight), \
             contextlib.redirect_stdout(io.StringIO()):
            rc = cmd(args)
        self.assertEqual(rc, 1)
        return seen

    def test_psk_watch(self):
        self.assertEqual(self._patterns(smd.cmd_psk_watch),
                         ["psk-recorder@AC0G\\x3dB4.service"])

    def test_wspr_watch(self):
        self.assertEqual(self._patterns(smd.cmd_wspr_watch),
                         ["wspr-recorder@AC0G\\x3dB4.service"])

    def test_verifier_watch(self):
        self.assertEqual(self._patterns(smd.cmd_verifier_watch),
                         ["wspr-recorder@AC0G\\x3dB4.service"])

    def test_meteor_watch(self):
        self.assertEqual(self._patterns(smd.cmd_meteor_watch),
                         ["meteor-scatter@AC0G\\x3dB4.service"])

    def test_a_bad_instance_never_reaches_systemd(self):
        for cmd in (smd.cmd_psk_watch, smd.cmd_wspr_watch,
                    smd.cmd_verifier_watch, smd.cmd_meteor_watch):
            with self.subTest(cmd=cmd.__name__):
                self.assertEqual(self._patterns(cmd, instance="AC0G/"), [])

    def test_preflight_names_the_unit_for_a_person(self):
        out = io.StringIO()
        with mock.patch.object(smd.subprocess, "run", lambda argv, **kw:
                               subprocess.CompletedProcess(argv, 0, "", "")), \
             contextlib.redirect_stdout(out):
            rc = smd._watch_preflight("psk-watch", "psk-recorder@AC0G\\x3dB4.service")
        self.assertEqual(rc, 1)
        self.assertIn("psk-recorder@AC0G/B4", out.getvalue())
        self.assertNotIn("\\x3d", out.getvalue())


class SourcesKeyTests(unittest.TestCase):
    """S48 and `smd admin sources remove`, which never normalised its key."""

    def test_every_spelling_is_a_valid_per_instance_key(self):
        for key in ("wspr-recorder@AC0G/B4", "wspr-recorder@AC0G=B4",
                    "wspr-recorder@AC0G\\x3dB4"):
            with self.subTest(key=key):
                self.assertTrue(smd._is_valid_per_instance_client(key, {"wspr-recorder"}))

    def test_normalize_instance_client(self):
        self.assertEqual(smd._normalize_instance_client("wspr-recorder@AC0G\\x3dB4"),
                         "wspr-recorder@AC0G=B4")
        self.assertEqual(smd._normalize_instance_client("wspr-recorder"), "wspr-recorder")

    def test_sources_remove_loads_the_storage_key(self):
        loaded = []

        class FakeSources:
            selected = []

            @classmethod
            def load(cls, key):
                loaded.append(key)
                return cls()
        with mock.patch.object(smd, "_need_root", lambda *a, **k: False), \
             mock.patch("sigmond.sources.ClientSources", FakeSources), \
             contextlib.redirect_stdout(io.StringIO()):
            rc = smd.cmd_sources_remove(argparse.Namespace(
                client="wspr-recorder@AC0G/B4", source="radiod:x"))
        self.assertEqual(rc, 0)
        self.assertEqual(loaded, ["wspr-recorder@AC0G=B4"])

    def test_restart_hint(self):
        # S50: the instance for a person, then a command systemctl accepts.
        self.assertEqual(
            smd._restart_hint("wspr-recorder", "AC0G=B4"),
            "wspr-recorder@AC0G/B4  "
            "(sudo systemctl restart 'wspr-recorder@AC0G\\x3dB4.service')")


class SshHostTests(unittest.TestCase):
    """S54, S55: reporter ids are not ssh aliases."""

    UNITS = ("wspr-recorder@AC0G\\x3dB4.service loaded active running x\n"
             "psk-recorder@W1ABC-5.service loaded active running x\n"
             "wspr-recorder@bee1.service loaded active running x\n")

    def test_radiod_watch_offers_only_peer_names(self):
        with mock.patch.object(smd, "_get_radiod_instances", lambda: []), \
             mock.patch.object(smd.subprocess, "run", lambda argv, **kw:
                               subprocess.CompletedProcess(argv, 0, self.UNITS, "")):
            self.assertEqual(smd._radiod_watch_candidate_hosts(), ["localhost", "bee1"])

    def test_bash_completion_offers_only_peer_names(self):
        line = next(l for l in smd._BASH_COMPLETION.splitlines() if "_rhosts=" in l)
        pipeline = line.split("2>/dev/null | ", 1)[1].rsplit(" | sort -u)", 1)[0]
        r = subprocess.run(["bash", "-c", pipeline], input=self.UNITS,
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.split(), ["bee1"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_smd_watch_instance.py -q --override-ini addopts=`
Expected: FAIL — `_watch_instance`, `_normalize_instance_client` and `_restart_hint` do not exist yet; psk-watch asks
for `psk-recorder@AC0G/B4.service`; meteor-watch asks for `meteor-scatter@AC0G-B4.service` (systemd-escape turned
the slash into a hyphen); the preflight message shows `\x3d`; `sources remove` loads `wspr-recorder@AC0G/B4`;
radiod-watch offers `AC0G\x3dB4` and `W1ABC-5`; the completion pipeline prints all three names.

- [ ] **Step 3: Add `_watch_instance` and fix the preflight message**

Insert before `def _watch_preflight(target_label: str, unit_pattern: str,`:

```python
def _watch_instance(raw, client: str):
    """A watch command's ``--instance`` -> (storage form, unit name).

    The operator may type the display form (AC0G/B4), the storage form
    (AC0G=B4), or systemd's spelling copied from a unit name (AC0G\\x3dB4);
    a legacy radiod-keyed name (my-rx888) passes through.  Returns
    (None, None) when --instance was not given.  Raises InvalidReporterId
    (a ValueError) on text that is neither, before anything asks systemd.
    """
    if raw is None:
        return None, None
    plain = _parse_instance_arg(raw)
    return plain, f'{client}@{_systemd_escape(plain)}.service'


```

In `_watch_preflight`, replace

```python
    msg = (f'{target_label}: no active service matching '
           f'{unit_pattern!r} on this host.')
```

with

```python
    msg = (f'{target_label}: no active service matching '
           f'{_display_unit(unit_pattern)!r} on this host.')
```

- [ ] **Step 4: meteor-watch (S37, S39)**

In `cmd_meteor_watch`, replace

```python
    instance = getattr(args, 'instance', None)
    # The operator's --instance is a plain reporter ID (e.g. AC0G=S), but
    # systemd names the template unit with the '=' escaped
    # (meteor-scatter@AC0G\x3dS.service) while the per-instance config path
    # keeps the literal '=' (/etc/meteor-scatter/AC0G=S.toml).  Canonicalize
    # to the unescaped reporter for paths/display; escape only for the unit.
    reporter = None
    if instance:
        reporter = re.sub(r'\\x([0-9a-fA-F]{2})',
                          lambda m: chr(int(m.group(1), 16)), instance)
    unit_pattern = (f'meteor-scatter@{_systemd_escape_reporter(reporter)}.service'
                    if reporter else 'meteor-scatter@*')
```

with

```python
    # --instance in any spelling: `reporter` holds the storage form for
    # paths (/etc/meteor-scatter/AC0G=B4.toml), `unit` systemd's spelling.
    try:
        reporter, unit = _watch_instance(getattr(args, 'instance', None),
                                         'meteor-scatter')
    except ValueError as exc:
        _err(f'meteor-watch: --instance: {exc}')
        return 1
    unit_pattern = unit or 'meteor-scatter@*'
```

Replace `        _err(f'no msk144 log for instance={reporter!r} '` with
`        _err(f'no msk144 log for instance={_display_rid(reporter)} '`, and
`        rep_label = _display_reporter(reporter)` with `        rep_label = _display_rid(reporter)`.  In the
`_ids = sorted({` block that follows, replace

```python
            _display_reporter(ln.split()[0].split('@', 1)[1]
                              .rsplit('.service', 1)[0])
```

with

```python
            _display_rid(ln.split()[0].split('@', 1)[1]
                         .rsplit('.service', 1)[0])
```

(the same three lines appear again in psk-watch; Step 7 handles that copy).

- [ ] **Step 5: verifier-watch (S41)**

In `cmd_verifier_watch`, replace

```python
    instance = getattr(args, 'instance', None)
    if instance is not None:
        unit_pattern = f'wspr-recorder@{instance}.service'
    else:
```

with

```python
    try:
        instance, unit = _watch_instance(getattr(args, 'instance', None),
                                         'wspr-recorder')
    except ValueError as exc:
        _err(f'verifier-watch: --instance: {exc}')
        return 1
    if instance is not None:
        unit_pattern = unit
    else:
```

and replace `    _heading(f'Upload-verifier activity ({unit_pattern})')` with
`    _heading(f'Upload-verifier activity ({_display_unit(unit_pattern)})')`.

- [ ] **Step 6: wspr-watch (S42, S43)**

In `cmd_wspr_watch`, replace

```python
    instance = getattr(args, 'instance', None)
    if instance is None:
        unit_pattern = 'wspr-recorder@*'
    else:
        unit_pattern = f'wspr-recorder@{instance}.service'
```

with

```python
    try:
        instance, unit = _watch_instance(getattr(args, 'instance', None),
                                         'wspr-recorder')
    except ValueError as exc:
        _err(f'wspr-watch: --instance: {exc}')
        return 1
    unit_pattern = unit or 'wspr-recorder@*'
```

Replace

```python
                resolved_reporter = unit.split('@', 1)[1].rsplit('.', 1)[0]
```

with

```python
                resolved_reporter = _unescape_instance(
                    unit.split('@', 1)[1].rsplit('.', 1)[0])
```

Replace `    _heading(f'WSPR cycle activity ({unit_pattern})')` with
`    _heading(f'WSPR cycle activity ({_display_unit(unit_pattern)})')`; replace
`        _info(f'   ⏳ no journal data for {unit_pattern} yet` with
`        _info(f'   ⏳ no journal data for {_display_unit(unit_pattern)} yet`; and replace
`            reporter_part = (f' reporter={_display_reporter(resolved_reporter)}'` with
`            reporter_part = (f' reporter={_display_rid(resolved_reporter)}'`.  The `-u unit_pattern` argument to
journalctl keeps systemd's spelling.

- [ ] **Step 7: psk-watch (S46) and mag-watch's label**

In `cmd_psk_watch`, replace

```python
    instance = getattr(args, 'instance', None)
    unit_pattern = (f'psk-recorder@{instance}.service' if instance
                    else 'psk-recorder@*')
```

with

```python
    try:
        instance, unit = _watch_instance(getattr(args, 'instance', None),
                                         'psk-recorder')
    except ValueError as exc:
        _err(f'psk-watch: --instance: {exc}')
        return 1
    unit_pattern = unit or 'psk-recorder@*'
```

Replace `            _err(f'no per-mode log for instance={instance!r} '` with
`            _err(f'no per-mode log for instance={_display_rid(instance)} '`; replace
`        rep_label = _display_reporter(instance)` with `        rep_label = _display_rid(instance)`; and replace the
second copy of

```python
            _display_reporter(ln.split()[0].split('@', 1)[1]
                              .rsplit('.service', 1)[0])
```

with

```python
            _display_rid(ln.split()[0].split('@', 1)[1]
                         .rsplit('.service', 1)[0])
```

In the mag watch (line 20836), replace `_display_reporter(rid)` with `_display_rid(rid)`.

- [ ] **Step 8: Delete the private copies (S37, S38)**

Delete from `def _systemd_escape_reporter(reporter_id: str) -> str:` down to the blank lines before
`def cmd_instance_list(args) -> int:`; that removes both `_systemd_escape_reporter` and `_display_reporter`.  Then
`grep -n "_display_reporter\|_systemd_escape_reporter" bin/smd` must print nothing.

- [ ] **Step 9: Sources (S48, S50, and `sources remove`)**

In `_is_valid_per_instance_client`, replace

```python
    try:
        from sigmond.instance import validate_reporter_id
        validate_reporter_id(instance)
        return True
    except Exception:
        return False
```

with

```python
    try:
        # Any spelling: AC0G/B4, AC0G=B4, or AC0G\x3dB4 copied from a unit.
        from sigmond.instance import parse_user_reporter_id
        parse_user_reporter_id(instance)
        return True
    except Exception:
        return False


def _normalize_instance_client(client: str) -> str:
    """`<client>@<reporter-id>` with the reporter id in storage form.

    The sources CLI keys per-instance selections by
    /etc/sigmond/clients/<client>@<storage form>.sources.toml, so
    `wspr-recorder@AC0G/B4`, `...@AC0G=B4` and `...@AC0G\\x3dB4` must all
    name one file.  A key without '@' comes back unchanged.  Raises
    InvalidReporterId (a ValueError) on a bad reporter id.
    """
    if '@' not in client:
        return client
    from sigmond import instance as _inst
    base, _, rid = client.partition('@')
    return f'{base}@{_inst.parse_user_reporter_id(rid)}'


def _restart_hint(client: str, rid: str) -> str:
    """The instance as a person reads it, then a restart command in the
    form systemctl accepts (a slash would name a different unit)."""
    return (f"{client}@{_display_rid(rid)}  "
            f"(sudo systemctl restart '{client}@{_systemd_escape(rid)}.service')")
```

In `cmd_sources_add`, replace

```python
    if '@' in client:
        from sigmond import instance as _inst
        _base, _, _rid = client.partition('@')
        client = f"{_base}@{_inst.parse_user_reporter_id(_rid)}"
```

with

```python
    try:
        client = _normalize_instance_client(client)
    except ValueError as exc:
        _err(str(exc))
        return 1
```

In `cmd_sources_remove`, replace

```python
    client = args.client
    cs = ClientSources.load(client)
```

with

```python
    try:
        client = _normalize_instance_client(args.client)
    except ValueError as exc:
        _err(str(exc))
        return 1
    cs = ClientSources.load(client)
```

In `cmd_sources_apply`, replace

```python
            if sel:
                _info(f'  ⊘ {stem}: not yet multi-source aware (deferred)')
```

with

```python
            if sel:
                _info(f'  ⊘ {base}@{_display_rid(rid)}: not yet multi-source '
                      f'aware (deferred)')
```

replace

```python
            _info(f'  ⚠ {stem}: no per-instance config at {inst_config} — '
                  f'run `smd admin instance add wspr-recorder {rid}` first')
```

with

```python
            _info(f'  ⚠ {base}@{_display_rid(rid)}: no per-instance config at '
                  f'{inst_config} — run `smd admin instance add wspr-recorder '
                  f'{_display_rid(rid)}` first')
```

and replace

```python
            restart_hints.append(f'wspr-recorder@{rid}.service')
```

with

```python
            restart_hints.append(_restart_hint('wspr-recorder', rid))
```

- [ ] **Step 10: ssh hosts (S54, S55)**

In `_BASH_COMPLETION`, replace

```
| sed -E 's/^(wspr|psk)-recorder@(.+)\.service.*/\2/' | sort -u)"
```

with

```
| sed -E 's/^(wspr|psk)-recorder@(.+)\.service.*/\2/' | grep -Ev '\\x[0-9a-fA-F]{2}|^[A-Z0-9][A-Z0-9=-]*[A-Z0-9]$' | sort -u)"
```

(`_BASH_COMPLETION` is a raw string, so `\\x` reaches grep as written and matches a literal backslash-x.)

In `_radiod_watch_candidate_hosts`, replace

```python
    import re as _re2
    local = set()
```

with

```python
    import re as _re2
    from sigmond.instance import InvalidReporterId, validate_reporter_id
    local = set()
```

and replace

```python
            inst = m.group(1)
            if inst in local or inst in seen:
                continue
            seen.add(inst)
            peers.append(inst)
```

with

```python
            inst = _unescape_instance(m.group(1))
            if inst in local or inst in seen:
                continue
            # A reporter-keyed instance (AC0G=B4, W1ABC-5) names a station,
            # not an ssh alias: `ssh AC0G\x3dB4` reaches nothing.  Peers are
            # named like `bee1`, which never validates as a reporter id.
            try:
                validate_reporter_id(inst)
                continue
            except InvalidReporterId:
                pass
            seen.add(inst)
            peers.append(inst)
```

- [ ] **Step 11: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_smd_watch_instance.py tests/test_smd_instance_display.py -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 12: Run the full suite**

Run: `.venv/bin/pytest tests -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 13: Commit**

```bash
git add tests/test_smd_watch_instance.py bin/smd
git commit -m "smd: watch and sources verbs read any spelling; no reporter ids as ssh hosts

--instance AC0G/B4 used to name AC0G-B4 (systemctl turns a slash into a
hyphen); systemd-escape turned W1ABC-5 into W1ABC\\x2d5.  Both go through
parse_instance_arg and systemd_escape now.  sources remove normalises
its key like sources add.  Inventory rows S37-S39, S41-S43, S46, S48,
S50, S54, S55."
```

- [ ] **Step 14: Mutation check**

| Change in `bin/smd` | Run | Must fail |
|---|---|---|
| in `_watch_instance`, `{_systemd_escape(plain)}` → `{plain}` | `tests/test_smd_watch_instance.py` | `test_every_spelling_names_the_same_unit` |
| in `_watch_instance`, `plain = _parse_instance_arg(raw)` → `plain = raw` | same | `test_every_spelling_names_the_same_unit`, `test_junk_is_refused` |
| in `_radiod_watch_candidate_hosts`, delete the `try: validate_reporter_id(inst) … pass` block | same | `test_radiod_watch_offers_only_peer_names` |
| in `_BASH_COMPLETION`, delete the ` \| grep -Ev '…'` stage | same | `test_bash_completion_offers_only_peer_names` |

After each, `git checkout -- bin/smd`.

---

### Task 6: People read AC0G/B4 in the library's messages (sigmond; S9, S14, S17, S20-S23, config.py:1382)

**Files:**
- Create: `tests/test_reporter_id_messages.py`
- Modify: `lib/sigmond/instance.py:391, 409, 418, 493, 515-516, 778` (S9)
- Modify: `lib/sigmond/harmonize.py` import (Task 2's line), `:926-927` (S14), `:1245` (S17)
- Modify: `lib/sigmond/commands/config.py:462` (S20), `:1323-1324`, `:1354` (S21), `:1371`, `:1382` (S22)
- Modify: `lib/sigmond/tui/format.py:25` (import), `:876-878`, `:987` (S23)
- Modify: `tests/test_harmonize.py:929`, `tests/test_tui_annotation_quality.py` (two new tests)

**Interfaces:**
- Consumes: `display_reporter_id`, `display_unit_name`, `parse_instance_arg`, `systemd_escape`,
  `unescape_systemd_instance` (Task 1); `harmonize._active_instances` returning storage forms (Task 2).
- Produces: `TimingConsumerUnit.instance` holds the storage form (was systemd's spelling).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reporter_id_messages.py`:

```python
"""Text a person reads names the instance AC0G/B4 (tasks/spec-reporter-id-
v372.md §4; inventory rows S9, S14, S17, S20-S22, and the restart hint at
commands/config.py that the inventory missed)."""
import argparse
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import harmonize as H  # noqa: E402
from sigmond import instance as I  # noqa: E402
from sigmond import upload  # noqa: E402
from sigmond.commands import config as C  # noqa: E402


def _rooted(tmp):
    real = Path

    def P(*parts):
        p = real(*parts)
        s = str(p)
        if s in ("/etc", "/opt") or s.startswith(("/etc/", "/opt/")):
            return real(tmp + s)
        return p
    return P


class StubFileTests(unittest.TestCase):
    """S9: the comments sigmond writes into new files."""

    def test_sources_stub(self):
        s = I._sources_stub("wspr-recorder", "AC0G=B4")
        self.assertIn("for wspr-recorder@AC0G/B4", s)
        self.assertIn("smd admin sources add wspr-recorder@AC0G/B4 <kind>:<id>", s)
        self.assertIn("smd admin sources remove wspr-recorder@AC0G/B4 <kind>:<id>", s)
        self.assertNotIn("AC0G=B4", s)

    def test_config_stub(self):
        s = I._config_stub("wspr-recorder", "AC0G=B4")
        self.assertIn("smd admin sources add wspr-recorder@AC0G/B4 <kind>:<id>", s)
        self.assertIn("# wspr-recorder AC0G/B4` to invoke", s)
        self.assertNotIn("AC0G=B4", s)

    def test_env_stub_keeps_the_storage_path(self):
        s = I._env_stub("wspr-recorder", "AC0G=B4")
        self.assertIn("# Loaded by the wspr-recorder unit for AC0G/B4 via", s)
        self.assertIn("EnvironmentFile=-/etc/wspr-recorder/env/AC0G=B4.env", s)

    def test_migration_header(self):
        s = I._migration_config_header("wspr-recorder", "my-rx888", "AC0G=B4")
        self.assertIn("# Per-instance config for wspr-recorder@AC0G/B4", s)


class HarmonizeMessageTests(unittest.TestCase):
    """S14: the recorder-only warning."""

    def test_recorder_only_warning_uses_the_display_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "etc" / "wspr-recorder" / "env").mkdir(parents=True)
            out = "wspr-recorder@AC0G\\x3dB4.service loaded active running WSPR\n"
            with mock.patch.object(H, "Path", _rooted(tmp)), \
                 mock.patch("subprocess.run", lambda argv, **kw:
                            subprocess.CompletedProcess(argv, 0, out, "")):
                r = H.rule_wspr_decode_enabled(None)
        self.assertEqual(r.severity, "warn")
        self.assertIn("AC0G/B4", r.message)
        self.assertNotIn("\\x3d", r.message)


class ConfigCommandTests(unittest.TestCase):
    """S20, S21, S22."""

    def test_restart_location_consumers(self):
        calls = []

        def run(argv, **kw):
            calls.append(list(argv))
            out = ("psk-recorder@AC0G\\x3dB4.service loaded active running PSK\n"
                   if argv[:2] == ["systemctl", "list-units"] and argv[-1] == "psk-recorder@*"
                   else "")
            return subprocess.CompletedProcess(argv, 0, out, "")
        err = io.StringIO()
        with mock.patch("subprocess.run", run), contextlib.redirect_stderr(err):
            C._restart_location_consumers()
        self.assertIn("restarting: psk-recorder@AC0G/B4.service", err.getvalue())
        self.assertIn(["systemctl", "restart", "psk-recorder@AC0G\\x3dB4.service"], calls)

    def test_upload_status_lists_instances_in_display_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "etc" / "wspr-recorder" / "env"
            env.mkdir(parents=True)
            (env / "AC0G=S.env").write_text("WSPR_USE_HS_UPLOADER=1\n")
            out = io.StringIO()
            with mock.patch.object(C, "Path", _rooted(tmp)), \
                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = C.cmd_config_upload(argparse.Namespace(
                    client="wspr-recorder", instance=None, on=False, off=False, via=None))
        self.assertEqual(rc, 0)
        self.assertIn("AC0G/S", out.getvalue())
        self.assertNotIn("AC0G=S", out.getvalue())

    def test_upload_change_names_the_instance_and_a_working_restart(self):
        fake = (lambda c, i, on, base="/etc", delivery=None:
                (Path("/x.env"), "WSPR_USE_HS_UPLOADER", ["wsprnet.org"], None))
        for typed in ("AC0G/S", "AC0G\\x3dS"):
            with self.subTest(typed=typed):
                err = io.StringIO()
                with mock.patch.object(upload, "apply_enable", fake), \
                     contextlib.redirect_stderr(err):
                    rc = C.cmd_config_upload(argparse.Namespace(
                        client="wspr-recorder", instance=typed, on=True, off=False, via=None))
                self.assertEqual(rc, 0)
                self.assertIn("upload for wspr-recorder@AC0G/S", err.getvalue())
                self.assertIn("sudo systemctl restart 'wspr-recorder@AC0G\\x3dS.service'",
                              err.getvalue())


if __name__ == "__main__":
    unittest.main()
```

In `tests/test_harmonize.py`, replace

```python
        self.assertIn("wspr-recorder@AC0G\\x3dS", r.message)
```

with

```python
        self.assertIn("wspr-recorder@AC0G/S", r.message)
        self.assertNotIn("\\x3d", r.message)
```

In `tests/test_tui_annotation_quality.py`, insert before `    def test_non_templated_unit_has_empty_instance(self):`

```python
    def test_reporter_keyed_instance_comes_back_in_storage_form(self):
        # S23: systemctl lists wspr-recorder@AC0G\x3dB4.service.
        runner = self._make_runner({
            "wspr-recorder@*.service": (
                0, "wspr-recorder@AC0G\\x3dB4.service loaded active running\n", ""),
        })
        units = enumerate_timing_consumer_units(runner=runner)
        self.assertEqual([(u.instance, u.unit) for u in units],
                         [("AC0G=B4", "wspr-recorder@AC0G\\x3dB4.service")])

```

and insert before `    def test_green_verdict_when_sigma_tight(self):`

```python
    def test_reporter_keyed_row_shows_the_display_form(self):
        # S23: the row label a person reads is AC0G/B4.
        auth, recorder, units = self._full_inputs()
        units.append(TimingConsumerUnit("wspr-recorder", "AC0G=B4",
                                        "wspr-recorder@AC0G\\x3dB4.service"))
        out = render_annotation_quality_body(auth, None, 5.0, recorder, None, units)
        self.assertIn("AC0G/B4", out)
        self.assertNotIn("AC0G=B4", out)
        self.assertNotIn("\\x3d", out)

```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_reporter_id_messages.py tests/test_harmonize.py tests/test_tui_annotation_quality.py -q --override-ini addopts=`
Expected: FAIL in all four stub tests, the harmonize message test and `test_harmonize.py`'s
`test_off_instance_warns_with_remediation`, all three config tests, and both new annotation tests.  The env stub
test fails on its first line; the second already holds.

- [ ] **Step 3: Stub files (S9)**

In `lib/sigmond/instance.py` make these replacements.  `_stub_header`:

```python
        f"# {kind} for {client}@{reporter_id}\n"
```

→

```python
        f"# {kind} for {client}@{display_reporter_id(reporter_id)}\n"
```

`_config_stub`:

```python
        '#   smd admin sources add ' + client + '@' + reporter_id + ' <kind>:<id>\n'
```

→

```python
        '#   smd admin sources add ' + client + '@'
        + display_reporter_id(reporter_id) + ' <kind>:<id>\n'
```

and

```python
        f"# {client} {reporter_id}` to invoke the client's config flow.\n"
```

→

```python
        f"# {client} {display_reporter_id(reporter_id)}` to invoke the client's config flow.\n"
```

`_env_stub` (the `EnvironmentFile=` line below it keeps the storage form, because it names a file):

```python
        f"# Loaded by {client}@{reporter_id}.service via\n"
```

→

```python
        f"# Loaded by the {client} unit for {display_reporter_id(reporter_id)} via\n"
```

`_sources_stub`:

```python
        f"#   smd admin sources add {client}@{reporter_id} <kind>:<id>\n"
        f"#   smd admin sources remove {client}@{reporter_id} <kind>:<id>\n"
```

→

```python
        f"#   smd admin sources add {client}@{display_reporter_id(reporter_id)} <kind>:<id>\n"
        f"#   smd admin sources remove {client}@{display_reporter_id(reporter_id)} <kind>:<id>\n"
```

`_migration_config_header`:

```python
        f"# Per-instance config for {client}@{reporter_id}\n"
```

→

```python
        f"# Per-instance config for {client}@{display_reporter_id(reporter_id)}\n"
```

Each printed `smd` command now carries `AC0G/B4`, which `smd admin instance` and `smd admin sources add|remove`
parse (Task 5 made `remove` parse).

- [ ] **Step 4: harmonize (S14, S17)**

Replace `from .instance import unescape_systemd_instance` (Task 2) with
`from .instance import display_reporter_id, unescape_systemd_instance`.  Replace

```python
    recorder_only = [u[len("wspr-recorder@"):-len(".service")]
                     for u in active if not _decode_on(u)]
```

with

```python
    recorder_only = [display_reporter_id(u[len("wspr-recorder@"):-len(".service")])
                     for u in active if not _decode_on(u)]
```

and replace

```python
                off.append(f"{client}@{inst} -> {' / '.join(dest)}")
```

with

```python
                off.append(f"{client}@{display_reporter_id(inst)} -> {' / '.join(dest)}")
```

- [ ] **Step 5: `commands/config.py` (S20, S21, S22, and line 1382)**

In `_restart_location_consumers`, replace

```python
    info('restarting: ' + ' '.join(units))
```

with

```python
    from ..instance import display_unit_name
    info('restarting: ' + ' '.join(display_unit_name(u) for u in units))
```

The `systemctl restart` argv keeps the names systemctl listed.  In `cmd_config_upload`, replace

```python
    from .. import upload
    client = args.client
```

with

```python
    from .. import upload
    from ..instance import display_reporter_id, parse_instance_arg, systemd_escape
    client = args.client
```

replace

```python
            print(f"    {state}  {e.stem}    "
```

with

```python
            print(f"    {state}  {display_reporter_id(e.stem)}    "
```

replace

```python
    ok(f"{'enabled' if on else 'disabled'} upload for {client}@{instance}  "
```

with

```python
    rid = parse_instance_arg(instance)      # apply_enable accepted it above
    ok(f"{'enabled' if on else 'disabled'} upload for {client}@{display_reporter_id(rid)}  "
```

and replace

```python
    info(f"restart to apply:  sudo systemctl restart '{client}@{instance}'")
```

with

```python
    # A printed command in the form systemctl accepts: the slash form would
    # name a different unit (AC0G/S -> AC0G-S).
    info(f"restart to apply:  sudo systemctl restart "
         f"'{client}@{systemd_escape(rid)}.service'")
```

- [ ] **Step 6: `tui/format.py` (S23)**

Replace

```python
from typing import List, Optional
```

(line 25) with

```python
from typing import List, Optional

from ..instance import display_reporter_id, unescape_systemd_instance
```

In `enumerate_timing_consumer_units`, replace

```python
            instance = ""
            if '@' in unit_name:
                instance = unit_name.split('@', 1)[1].rsplit('.', 1)[0]
```

with

```python
            instance = ""
            if '@' in unit_name:
                instance = unescape_systemd_instance(
                    unit_name.split('@', 1)[1].rsplit('.', 1)[0])
```

and in `render_annotation_quality_body`, replace

```python
            label = u.instance or "(default)"
```

with

```python
            label = display_reporter_id(u.instance) if u.instance else "(default)"
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_reporter_id_messages.py tests/test_harmonize.py tests/test_tui_annotation_quality.py tests/test_upload.py tests/test_instance.py -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/pytest tests -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add tests/test_reporter_id_messages.py tests/test_harmonize.py tests/test_tui_annotation_quality.py \
        lib/sigmond/instance.py lib/sigmond/harmonize.py lib/sigmond/commands/config.py lib/sigmond/tui/format.py
git commit -m "reporter ids: messages and stub files name the instance AC0G/B4

harmonize warnings, smd config upload, the location-consumer restart, the
annotation-quality labels and the comments in new instance files all show
the display form.  The upload restart hint now names the real unit; as
typed (AC0G/S) it named AC0G-S.  Inventory rows S9, S14, S17, S20-S23."
```

- [ ] **Step 10: Mutation check**

| Change | Run | Must fail |
|---|---|---|
| `harmonize.py`: `display_reporter_id(u[len("wspr-recorder@"):-len(".service")])` → `u[len("wspr-recorder@"):-len(".service")]` | `tests/test_reporter_id_messages.py` | `test_recorder_only_warning_uses_the_display_form` |
| `commands/config.py`: `{systemd_escape(rid)}.service` → `{rid}.service` | same | `test_upload_change_names_the_instance_and_a_working_restart` |
| `tui/format.py`: `display_reporter_id(u.instance) if u.instance else "(default)"` → `u.instance or "(default)"` | `tests/test_tui_annotation_quality.py` | `test_reporter_keyed_row_shows_the_display_form` |

After each, `git checkout -- <file>`.

---

### Task 7: The TUI shows AC0G/B4 (sigmond; S26-S28, S30-S34)

**Files:**
- Create: `tests/test_tui_reporter_display.py`
- Modify: `lib/sigmond/tui/screens/activity.py:24-28, 163`
- Modify: `lib/sigmond/tui/screens/logs.py:23-27, 96`
- Modify: `lib/sigmond/tui/screens/verifier.py:27-31, 215`
- Modify: `lib/sigmond/tui/screens/psws.py:69-73`
- Modify: `lib/sigmond/tui/screens/configuration.py:48-58, 291-292, 299-300, 486-494, 522-531`

**Interfaces:**
- Consumes: `display_reporter_id`, `parse_user_reporter_id` (Task 1); `InstancePaths.unit_name` in systemd spelling
  (Task 3); `detect_migration_candidates` that no longer lists the live unit (Task 2, which closes S27 and S31).
- Produces: nothing new.  The dropdowns' option values stay in storage form; Task 5 made `smd watch --instance`
  accept it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tui_reporter_display.py`:

```python
"""The TUI shows a person AC0G/B4 (tasks/spec-reporter-id-v372.md §4;
inventory rows S26-S28, S30, S32-S34)."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

try:
    import textual  # noqa: F401
    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False

SCREENS = REPO / "lib" / "sigmond" / "tui" / "screens"


def _instances(client):
    from sigmond.instance import Instance, instance_paths
    return lambda catalog_clients=None: [Instance(
        paths=instance_paths(client, "AC0G=B4"),
        has_config=True, has_env=True, has_sources=True)]


class FallbackCopiesTests(unittest.TestCase):
    """S32: no screen keeps its own copy of the conversion rules."""

    def test_no_import_fallback_left(self):
        for name in ("activity.py", "logs.py", "verifier.py", "psws.py",
                     "configuration.py"):
            with self.subTest(screen=name):
                self.assertNotIn("except ImportError", (SCREENS / name).read_text())


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class DropdownTests(unittest.TestCase):
    """S26-S28, S30: a dropdown shows AC0G/B4 and passes AC0G=B4 on."""

    def _patched(self, client):
        return (mock.patch("sigmond.instance.list_instances", _instances(client)),
                mock.patch("sigmond.instance.detect_migration_candidates", lambda: []))

    def test_activity(self):
        from sigmond.tui.screens import activity
        a, b = self._patched("psk-recorder")
        with a, b, mock.patch.dict(activity._TARGET_TO_CLIENT, {"psk": "psk-recorder"}):
            opts = activity.ActivityScreen._instance_options_for("psk")
        self.assertIn(("AC0G/B4", "AC0G=B4"), opts)

    def test_logs(self):
        from sigmond.tui.screens import logs
        a, b = self._patched("psk-recorder")
        with a, b:
            opts = logs._instance_options_for_component("psk-recorder")
        self.assertIn(("AC0G/B4", "AC0G=B4"), opts)

    def test_verifier(self):
        from sigmond.tui.screens import verifier
        a, b = self._patched("wspr-recorder")
        with a, b, mock.patch.dict(verifier._TARGET_TO_CLIENT, {"wspr": "wspr-recorder"}):
            opts = verifier.VerifierScreen._instance_options_for("wspr")
        self.assertIn(("AC0G/B4", "AC0G=B4"), opts)


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class ConfigurationScreenTests(unittest.TestCase):
    """S34: the confirmation text and the command it runs."""

    def _capture(self):
        seen = {}

        def confirm_and_run(app, *, title, body, cmd, sudo, on_complete):
            seen.update(title=title, body=body, cmd=cmd)
        return seen, mock.patch("sigmond.tui.mutation.confirm_and_run", confirm_and_run)

    def test_remove(self):
        from sigmond.tui.screens.configuration import ConfigurationScreen
        seen, patch = self._capture()
        fake = SimpleNamespace(_selected=lambda: ("psk-recorder", "AC0G=B4"),
                               app=None, _after_mutation=None,
                               _set_last=lambda msg: None)
        with patch:
            ConfigurationScreen._do_remove(fake)
        self.assertEqual(seen["title"], "Remove psk-recorder@AC0G/B4?")
        self.assertIn("smd admin instance remove psk-recorder AC0G/B4 --yes", seen["body"])
        self.assertEqual(seen["cmd"][-3:], ["psk-recorder", "AC0G/B4", "--yes"])

    def test_add(self):
        from sigmond.tui.screens.configuration import ConfigurationScreen
        seen, patch = self._capture()
        values = {"#cf-add-client": "psk-recorder", "#cf-add-reporter": "ac0g/b4"}
        fake = SimpleNamespace(
            query_one=lambda sel, cls=None: SimpleNamespace(value=values[sel]),
            app=None, _after_mutation=None, _set_last=lambda msg: None)
        with patch:
            ConfigurationScreen._do_add(fake, dry_run=False)
        self.assertEqual(seen["title"], "Add instance psk-recorder@AC0G/B4?")
        self.assertIn("smd admin instance add psk-recorder AC0G/B4", seen["body"])
        self.assertEqual(seen["cmd"][-2:], ["psk-recorder", "AC0G/B4"])

    def test_unit_state_is_asked_for_by_the_systemd_name(self):
        # S33
        src = (SCREENS / "configuration.py").read_text()
        self.assertNotIn('f"{i.client}@{i.reporter_id}.service"', src)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_tui_reporter_display.py -q --override-ini addopts=`
Expected: 7 FAIL — five fallbacks found, the three dropdowns offer `("AC0G=B4", "AC0G=B4")`, the configuration screen
shows `smd admin instance remove psk-recorder AC0G=B4 --yes`, and the source still builds
`f"{i.client}@{i.reporter_id}.service"`.  `test_add` fails on its body assertion.

- [ ] **Step 3: Delete the fallbacks (S32)**

In each of `activity.py`, `logs.py`, `verifier.py` and `psws.py`, replace

```python
try:
    from ...instance import display_reporter_id as _display_reporter_id
except ImportError:
    def _display_reporter_id(rid: str) -> str:
        return rid.replace("=", "/")
```

with

```python
from ...instance import display_reporter_id as _display_reporter_id
```

In `configuration.py`, replace

```python
try:
    from ...instance import (
        display_reporter_id as _display,
        parse_user_reporter_id as _parse_rid,
    )
except ImportError:
    def _display(rid: str) -> str:
        return rid.replace("=", "/")

    def _parse_rid(s: str) -> str:
        return s.strip().replace("/", "-").upper()
```

with

```python
from ...instance import (
    display_reporter_id as _display,
    parse_user_reporter_id as _parse_rid,
)
```

The deleted `_parse_rid` fallback turned `/` into `-`: `AC0G/B4` would have become `AC0G-B4`, another station.

- [ ] **Step 4: Dropdown labels (S26, S28, S30)**

In `activity.py` and `verifier.py`, replace

```python
            options.append((i.reporter_id, i.reporter_id))
```

with

```python
            options.append((_display_reporter_id(i.reporter_id), i.reporter_id))
```

In `logs.py`, replace

```python
        options.append((i.reporter_id, i.reporter_id))
```

with

```python
        options.append((_display_reporter_id(i.reporter_id), i.reporter_id))
```

- [ ] **Step 5: Configuration screen (S33, S34)**

Replace

```python
        active_states = _batch_unit_active(
            [f"{i.client}@{i.reporter_id}.service" for i in instances])
```

with

```python
        active_states = _batch_unit_active(
            [i.paths.unit_name for i in instances])
```

and

```python
            unit = f"{i.client}@{i.reporter_id}.service"
            state = active_states.get(unit, "unknown")
```

with

```python
            state = active_states.get(i.paths.unit_name, "unknown")
```

In `_do_remove`, replace

```python
        client, reporter = sel
        cmd = [_smd_binary(), 'admin', 'instance', 'remove', client, reporter,
               '--yes']
        confirm_and_run(
            self.app,
            title=f"Remove {client}@{_display(reporter)}?",
            body=(
                f"Run [bold]smd admin instance remove {client} {reporter} "
```

with

```python
        client, reporter = sel
        shown = _display(reporter)
        cmd = [_smd_binary(), 'admin', 'instance', 'remove', client, shown,
               '--yes']
        confirm_and_run(
            self.app,
            title=f"Remove {client}@{shown}?",
            body=(
                f"Run [bold]smd admin instance remove {client} {shown} "
```

In `_do_add`, replace

```python
        cmd = [_smd_binary(), 'admin', 'instance', 'add', client, reporter]
        if dry_run:
            cmd.append('--dry-run')
            self._exec_async(cmd)
            return
        confirm_and_run(
            self.app,
            title=f"Add instance {client}@{_display(reporter)}?",
            body=(
                f"Run [bold]smd admin instance add {client} {reporter}[/]\n\n"
```

with

```python
        shown = _display(reporter)
        cmd = [_smd_binary(), 'admin', 'instance', 'add', client, shown]
        if dry_run:
            cmd.append('--dry-run')
            self._exec_async(cmd)
            return
        confirm_and_run(
            self.app,
            title=f"Add instance {client}@{shown}?",
            body=(
                f"Run [bold]smd admin instance add {client} {shown}[/]\n\n"
```

The command shows and runs the display form, which `smd admin instance` parses (spec §4).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_tui_reporter_display.py tests/test_tui_logs.py tests/test_tui_psws.py -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest tests -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add tests/test_tui_reporter_display.py lib/sigmond/tui/screens/activity.py \
        lib/sigmond/tui/screens/logs.py lib/sigmond/tui/screens/verifier.py \
        lib/sigmond/tui/screens/psws.py lib/sigmond/tui/screens/configuration.py
git commit -m "tui: show AC0G/B4 in dropdowns and confirmations; drop private copies

The Activity, Logs and Verifier dropdowns label instances in display form;
the Configuration screen asks systemd by the real unit name and shows and
runs AC0G/B4.  The five ImportError fallbacks are gone; one of them turned
'/' into '-'.  Inventory rows S26-S28, S30, S32-S34."
```

- [ ] **Step 9: Mutation check**

| Change | Run | Must fail |
|---|---|---|
| `activity.py`: `(_display_reporter_id(i.reporter_id), i.reporter_id)` → `(i.reporter_id, i.reporter_id)` | `tests/test_tui_reporter_display.py` | `test_activity` |
| `configuration.py` `_do_remove`: `shown = _display(reporter)` → `shown = reporter` | same | `test_remove` |

After each, `git checkout -- <file>`.

---

### Task 8: `smd admin instance canonical` (sigmond; spec §6)

**Files:**
- Create: `tests/test_smd_instance_canonical.py`
- Modify: `bin/smd` — new `cmd_instance_canonical` before `def cmd_radiod_migrate(args) -> int:` (21775); parser at
  23863-23934; dispatch at 25559-25561
- Modify: `docs/MULTI-INSTANCE-ARCHITECTURE.md:193`

**Interfaces:**
- Consumes: `parse_user_reporter_id`, `systemd_instance`, `InvalidReporterId` (Task 1) through `_instance_module()`.
- Produces: the command `smd admin instance canonical <text> [--systemd]`.  Stdout: one line, the storage form, or
  with `--systemd` the systemd form.  Exit 0.  On an invalid id: nothing on stdout, a message on stderr, exit 1.
  Task 13 calls it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_smd_instance_canonical.py`:

```python
"""`smd admin instance canonical` (tasks/spec-reporter-id-v372.md §6): the
one way a shell script turns a reporter id into a file name or unit name."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _canonical(*argv):
    env = dict(os.environ, PYTHONPATH=str(REPO / "lib"), SIGMOND_NO_VENV_REEXEC="1")
    return subprocess.run(
        [sys.executable, str(REPO / "bin" / "smd"), "admin", "instance",
         "canonical", *argv],
        capture_output=True, text=True, env=env, timeout=60)


class CanonicalTests(unittest.TestCase):

    def test_storage_form_from_every_spelling(self):
        for text in ("AC0G/B4", "AC0G=B4", "AC0G\\x3dB4", "ac0g/b4"):
            with self.subTest(text=text):
                r = _canonical(text)
                self.assertEqual((r.returncode, r.stdout), (0, "AC0G=B4\n"), r.stderr)

    def test_systemd_form(self):
        r = _canonical("--systemd", "AC0G/B4")
        self.assertEqual((r.returncode, r.stdout), (0, "AC0G\\x3dB4\n"), r.stderr)

    def test_hyphen_stays_a_hyphen(self):
        # systemd-escape(1) would print W1ABC\x2d5.
        r = _canonical("--systemd", "W1ABC-5")
        self.assertEqual((r.returncode, r.stdout), (0, "W1ABC-5\n"), r.stderr)

    def test_invalid_id_exits_1_with_nothing_on_stdout(self):
        for text in ("AC0G/", "AC0G\\x5cx3dB4", ""):
            with self.subTest(text=text):
                r = _canonical(text)
                self.assertEqual((r.returncode, r.stdout), (1, ""))
                self.assertIn("reporter ID", r.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_smd_instance_canonical.py -q --override-ini addopts=`
Expected: FAIL — argparse rejects `canonical` (`invalid choice: 'canonical'`, exit 2).

- [ ] **Step 3: Add the handler**

Insert before `def cmd_radiod_migrate(args) -> int:`:

```python
def cmd_instance_canonical(args) -> int:
    """`smd admin instance canonical <text> [--systemd]`: read-only.

    Prints a reporter id, typed in any spelling (AC0G/B4, AC0G=B4,
    AC0G\\x3dB4, lower case), in its storage form (AC0G=B4), or with
    --systemd in the form a unit name carries (AC0G\\x3dB4; W1ABC-5 stays
    W1ABC-5).  Scripts call it instead of `tr '/' '='` and
    systemd-escape(1).  Prints nothing on stdout and exits 1 on an
    invalid id, so `$(smd admin instance canonical ...)` is empty then.
    """
    inst = _instance_module()
    if inst is None:
        return 1
    try:
        storage = inst.parse_user_reporter_id(args.text)
    except inst.InvalidReporterId as exc:
        print(f'smd: {exc}', file=sys.stderr)
        return 1
    print(inst.systemd_instance(storage) if args.systemd else storage)
    return 0


```

- [ ] **Step 4: Wire the parser and the dispatch**

In the `instance` parser, replace

```python
             '(list / show / add / remove / edit / enable / disable / migrate)')
```

with

```python
             '(list / show / add / remove / edit / enable / disable / migrate '
             '/ canonical)')
```

Insert before `    # smd admin radiod — RADIOD-IDENTIFICATION.md §6 Phase 5`:

```python
    p_i_canonical = instance_sub.add_parser(
        'canonical',
        help='print a reporter id in its storage form (AC0G/B4 -> AC0G=B4), '
             'or with --systemd as a unit name carries it; read-only, '
             'exits 1 on an invalid id')
    p_i_canonical.add_argument(
        'text', help='reporter id in any spelling: AC0G/B4, AC0G=B4, '
                     'or as copied from a unit name')
    p_i_canonical.add_argument(
        '--systemd', action='store_true',
        help='print the form a unit name carries (AC0G/B4 -> AC0G\\x3dB4)')

```

In the dispatch, replace

```python
        if ic == 'migrate':
            return cmd_instance_migrate(args)
        p_instance.print_help()
```

with

```python
        if ic == 'migrate':
            return cmd_instance_migrate(args)
        if ic == 'canonical':
            return cmd_instance_canonical(args)
        p_instance.print_help()
```

- [ ] **Step 5: Document it**

In `docs/MULTI-INSTANCE-ARCHITECTURE.md`, after the line
`smd admin instance migrate                                 # one-shot migration from radiod-keyed names`, add

```
smd admin instance canonical <text> [--systemd]            # print the storage (or unit-name) form; read-only
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_smd_instance_canonical.py tests/test_docs_cli_table.py -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/test_smd_instance_canonical.py bin/smd docs/MULTI-INSTANCE-ARCHITECTURE.md
git commit -m "smd: admin instance canonical prints a reporter id's storage or unit form

Read-only.  Shell scripts ask smd instead of tr '/' '=' and
systemd-escape, which turns W1ABC-5 into W1ABC\\x2d5
(tasks/spec-reporter-id-v372.md §6)."
```

- [ ] **Step 8: Mutation check**

In `cmd_instance_canonical`, change `print(inst.systemd_instance(storage) if args.systemd else storage)` to
`print(storage)`.  Run `.venv/bin/pytest tests/test_smd_instance_canonical.py -q --override-ini addopts=`; expect
`test_systemd_form` to FAIL.  Then `git checkout -- bin/smd`.

---

### Task 9: meteor-scatter carries its own copy and reads every spelling (M1-M4, M7)

Work in `/home/mjh/hamsci/repos/meteor-scatter` (Michael's).

**Files:**
- Create: `src/meteor_scatter/_reporter_id.py`
- Create: `tests/data/reporter_id_vectors.json` (copy of sigmond's)
- Create: `tests/test_reporter_id.py`, `tests/test_instance_args.py`
- Modify: `src/meteor_scatter/cli.py:320` (new helper before `_handle_daemon`), `:341-346` (M1, M7)
- Modify: `src/meteor_scatter/configurator.py:64` (import), `:464-472` (M2), `:482-501` (M3), `:1179-1180` (M4)
- Modify: `tests/test_configurator.py:443` (fixture), and four new tests in `StrayInstanceTests`

**Interfaces:**
- Consumes: sigmond's `tests/data/reporter_id_vectors.json` (Task 1), copied byte for byte.
- Produces:
  - `meteor_scatter._reporter_id` with `unescape_systemd_instance(name) -> str`, `to_storage(text) -> str`
    (raises `ValueError`), `systemd_instance(name) -> str` (no validation), `display(name) -> str`.  Tasks 10 and 12
    copy this exact file.
  - `meteor_scatter.cli._normalize_instance_args(args) -> None`.

- [ ] **Step 1: Copy the table and write the failing tests**

```bash
cd /home/mjh/hamsci/repos/meteor-scatter
mkdir -p tests/data
cp ../sigmond/tests/data/reporter_id_vectors.json tests/data/
```

Create `tests/test_reporter_id.py`:

```python
"""This client's copy of the reporter-id rules, run against the shared table.

tests/data/reporter_id_vectors.json must stay a byte-identical copy of
sigmond's tests/data/reporter_id_vectors.json; sigmond's
tests/test_reporter_id_drift.py fails when they differ.  Refresh it with:

    cp ../sigmond/tests/data/reporter_id_vectors.json tests/data/
"""
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from meteor_scatter._reporter_id import (  # noqa: E402
    display, systemd_instance, to_storage, unescape_systemd_instance,
)

VECTORS = json.loads(
    (REPO / "tests" / "data" / "reporter_id_vectors.json").read_text())


class VectorTableTests(unittest.TestCase):

    def test_reporter_ids(self):
        for v in VECTORS["reporter_ids"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(to_storage(v["input"]), v["storage"])
                self.assertEqual(systemd_instance(v["storage"]), v["systemd"])
                self.assertEqual(unescape_systemd_instance(v["systemd"]), v["storage"])
                self.assertEqual(display(v["storage"]), v["display"])
                self.assertEqual(display(v["systemd"]), v["display"])

    def test_legacy_names_pass_through(self):
        for v in VECTORS["legacy_names"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(to_storage(v["input"]), v["storage"])
                self.assertEqual(systemd_instance(v["storage"]), v["systemd"])

    def test_invalid_text_is_refused(self):
        for text in VECTORS["invalid"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                to_storage(text)

    def test_display_never_shows_an_escape(self):
        for v in VECTORS["display_only"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(display(v["input"]), v["display"])


if __name__ == "__main__":
    unittest.main()
```

Create `tests/test_instance_args.py`:

```python
"""The daemon receives --instance %i --radiod-id %i, and %i keeps systemd's
spelling (AC0G\\x3dB4).  cli._normalize_instance_args turns both back into
the storage form before anything reads them (sigmond
tasks/spec-reporter-id-v372.md §5, M1 and M7)."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from meteor_scatter.cli import _normalize_instance_args  # noqa: E402


class InstanceArgTests(unittest.TestCase):

    def test_systemd_spelling_becomes_storage(self):
        ns = SimpleNamespace(instance="AC0G\\x3dB4", radiod_id="AC0G\\x3dB4")
        _normalize_instance_args(ns)
        self.assertEqual((ns.instance, ns.radiod_id), ("AC0G=B4", "AC0G=B4"))

    def test_every_escape_is_decoded_not_only_x3d(self):
        ns = SimpleNamespace(instance="W1ABC\\x2d5", radiod_id=None)
        _normalize_instance_args(ns)
        self.assertEqual(ns.instance, "W1ABC-5")
        self.assertIsNone(ns.radiod_id)

    def test_plain_names_pass_through(self):
        ns = SimpleNamespace(instance="AC0G-B4",
                             radiod_id="B4-100-rx888mk2-status.local")
        _normalize_instance_args(ns)
        self.assertEqual((ns.instance, ns.radiod_id),
                         ("AC0G-B4", "B4-100-rx888mk2-status.local"))


if __name__ == "__main__":
    unittest.main()
```

In `tests/test_configurator.py`, class `StrayInstanceTests`, replace

```python
        self._enable("AC0G=B4")              # a sigmond per-instance config
```

with

```python
        self._enable("AC0G\\x3dB4")          # a sigmond per-instance config; the
                                             # link carries systemd's spelling
```

and insert before `    def test_interactive_init_disables_the_orphan_on_yes(self):`

```python
    def test_a_live_instance_is_never_stale_under_any_spelling(self):
        # M2: on AC0G-B4 the link reads meteor-scatter@AC0G\x3dB4.service and
        # the config AC0G=B4.toml; init offered to disable the live instance.
        self._enable("AC0G\\x3dB4")
        (self.etc / "env" / "AC0G=B4.env").write_text("")
        self.assertEqual(configurator._stale_instances("127.0.0.1", self.etc), [])

    def test_a_stale_reporter_instance_is_named_for_a_person(self):
        # M3: prompts show AC0G/B5; systemctl gets AC0G\x3dB5.
        self._enable("AC0G\\x3dB5")
        with mock.patch.object(configurator.shutil, "which", return_value="/bin/systemctl"), \
             mock.patch.object(configurator.subprocess, "run",
                               side_effect=AssertionError("must not touch systemd")), \
             mock.patch.object(configurator, "_info") as info:
            configurator._retire_stale_instances("127.0.0.1", self.etc, interactive=False)
        said = " ".join(str(c.args[0]) for c in info.call_args_list)
        self.assertIn("meteor-scatter@AC0G/B5 is enabled", said)
        self.assertIn("sudo systemctl disable --now 'meteor-scatter@AC0G\\x3dB5.service'", said)

    def test_interactive_disable_sends_the_unit_systemd_named(self):
        self._enable("AC0G\\x3dB5")
        calls = []

        class _R:
            returncode = 0
            stdout = stderr = ""

        with mock.patch.object(configurator.shutil, "which", return_value="/bin/systemctl"), \
             mock.patch.object(configurator.subprocess, "run",
                               side_effect=lambda argv, **k: calls.append(argv) or _R()), \
             mock.patch.object(configurator, "_prompt", lambda *a, **k: "y"), \
             mock.patch.object(configurator, "_ok"):
            configurator._retire_stale_instances("127.0.0.1", self.etc, interactive=True)
        self.assertIn(["/bin/systemctl", "disable", "--now",
                       "meteor-scatter@AC0G\\x3dB5.service"], calls)

    def test_env_file_path_takes_any_spelling(self):
        # M4
        for typed in ("AC0G\\x3dB4", "AC0G/B4", "AC0G=B4"):
            with self.subTest(typed=typed):
                self.assertEqual(configurator._env_file_path(typed),
                                 configurator._ENV_DIR / "AC0G=B4.env")
        self.assertEqual(configurator._env_file_path("bee1-status.local"),
                         configurator._ENV_DIR / "bee1-status.local.env")

```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_reporter_id.py tests/test_instance_args.py tests/test_configurator.py -q`
Expected: two collection errors (`No module named 'meteor_scatter._reporter_id'`; `cannot import name
'_normalize_instance_args'`), and in `test_configurator.py` FAIL for `test_only_the_orphan_is_stale` (it now also
lists `AC0G\x3dB4`), `test_a_live_instance_is_never_stale_under_any_spelling`,
`test_a_stale_reporter_instance_is_named_for_a_person` and `test_env_file_path_takes_any_spelling`.
`test_interactive_disable_sends_the_unit_systemd_named` passes by accident today; keep it as a guard.

- [ ] **Step 3: Write the module**

Create `src/meteor_scatter/_reporter_id.py`.  Tasks 10 and 12 copy this file unchanged, so it names no client:

```python
"""Reporter-id spellings: one name, three forms.

    display   AC0G/B4      what a person types and reads
    storage   AC0G=B4      file names, directory names, config keys
    systemd   AC0G\\x3dB4  unit names, *.wants links, %i

A copy of the rules in sigmond's lib/sigmond/instance.py, which stays the
original.  This client keeps its own copy so it runs on a host, or in a
venv, without sigmond.  tests/data/reporter_id_vectors.json is a
byte-identical copy of sigmond's table, and tests/test_reporter_id.py runs
this module against it; sigmond's tests/test_reporter_id_drift.py fails
when the two tables differ.  psk-recorder, meteor-scatter and
wspr-recorder carry this same file.
"""
import re

_XHH = re.compile(r"\\x([0-9a-fA-F]{2})")
_RID = re.compile(r"^[A-Z0-9][A-Z0-9=-]*[A-Z0-9]$")
_UNIT_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:-_.")


def unescape_systemd_instance(name: str) -> str:
    """AC0G\\x3dB4 -> AC0G=B4.  A plain name comes back unchanged."""
    return _XHH.sub(lambda m: chr(int(m.group(1), 16)), name)


def to_storage(text: str) -> str:
    """Any spelling -> storage form.  Lower case with no '/' or '=' reads as
    a legacy radiod-keyed name (my-rx888) and passes through unchanged.
    Raises ValueError on text that names nothing."""
    plain = unescape_systemd_instance(text.strip())
    if not plain or "\\" in plain:
        raise ValueError(f"not a reporter id: {text!r}")
    has_sep = "/" in plain or "=" in plain
    if not has_sep and plain != plain.upper():
        return plain
    rid = plain.upper().replace("/", "=")
    if _RID.match(rid):
        return rid
    if has_sep:
        raise ValueError(f"not a reporter id: {text!r}")
    return plain


def systemd_instance(name: str) -> str:
    """Storage form -> the instance as a unit name carries it.  '=' becomes
    \\x3d; letters, digits and '-' stay (systemd-escape would turn '-' into
    \\x2d and name a different unit)."""
    return "".join(
        c if c in _UNIT_CHARS else "".join(f"\\x{b:02x}" for b in c.encode())
        for c in name)


def display(name: str) -> str:
    """Any spelling -> what a person reads (AC0G/B4)."""
    prev = None
    while name != prev:
        prev, name = name, unescape_systemd_instance(name)
    return name.replace("=", "/")
```

- [ ] **Step 4: Fix `cli.py` (M1, M7)**

Insert before `def _handle_daemon(args):` (line 320):

```python
def _normalize_instance_args(args) -> None:
    """Turn --instance and --radiod-id back into the storage form.

    The unit template passes `--instance %i --radiod-id %i`, and %i keeps
    systemd's spelling (AC0G\\x3dB4).  Config lookup, block matching and
    log lines all want AC0G=B4, so decode every \\xHH here, once, before
    anything reads them.  Plain names (AC0G-B4, bee1-status.local) pass
    through unchanged.
    """
    from meteor_scatter._reporter_id import unescape_systemd_instance
    if getattr(args, "instance", None):
        args.instance = unescape_systemd_instance(args.instance)
    if getattr(args, "radiod_id", None):
        args.radiod_id = unescape_systemd_instance(args.radiod_id)


```

Inside `_handle_daemon`, replace

```python
    # systemd escapes '=' in unit instance names to '\x3d', so with the
    # %i unit templates a '='-style (reporter-id sentinel) instance
    # reaches the daemon in escaped form.  Unescape so config lookup
    # works for both naming styles ('-' names pass through untouched).
    if args.instance:
        args.instance = args.instance.replace("\\x3d", "=")
```

with

```python
    _normalize_instance_args(args)
```

- [ ] **Step 5: Fix `configurator.py` (M2, M3, M4)**

Replace

```python
from .config import DEFAULT_CONFIG_PATH, DEFAULTS, load_config
```

with

```python
from ._reporter_id import display, systemd_instance, to_storage, unescape_systemd_instance
from .config import DEFAULT_CONFIG_PATH, DEFAULTS, load_config
```

In `_stale_instances`, replace

```python
        if m:
            found.add(m.group(1))
    stale = []
    for inst in sorted(found):
        if inst == keep_id:
            continue
```

with

```python
        if m:
            # systemd names the link in its own spelling (AC0G\x3dB4); the
            # config and env files carry the storage form (AC0G=B4).
            found.add(unescape_systemd_instance(m.group(1)))
    keep = unescape_systemd_instance(keep_id)
    stale = []
    for inst in sorted(found):
        if inst == keep:
            continue
```

In `_retire_stale_instances`, replace the whole loop

```python
    for inst in stale:
        unit = f"meteor-scatter@{inst}.service"
        if not interactive or not sctl:
            _info(f"⚠ {unit} is enabled but no config names it any more; "
                  f"disable it with:  sudo systemctl disable --now {unit} && "
                  f"sudo systemctl reset-failed {unit}")
            continue
        ans = _prompt(f"{unit} is enabled but no config names it any more.  "
                      "Disable it? (Y/n)", "y")
        if not ans.strip().lower().startswith("y"):
            _info(f"left {unit} enabled")
            continue
        r = subprocess.run([sctl, "disable", "--now", unit], capture_output=True, text=True)
        subprocess.run([sctl, "reset-failed", unit], capture_output=True, text=True)
        if r.returncode == 0:
            _ok(f"disabled stale {unit}")
        else:
            _info(f"(could not disable {unit}: {r.stderr.strip()} -- run: "
                  f"sudo systemctl disable --now {unit})")
```

with

```python
    for inst in stale:
        unit = f"meteor-scatter@{systemd_instance(inst)}.service"   # for systemctl
        label = f"meteor-scatter@{display(inst)}"                   # for a person
        if not interactive or not sctl:
            _info(f"⚠ {label} is enabled but no config names it any more; "
                  f"disable it with:  sudo systemctl disable --now '{unit}' && "
                  f"sudo systemctl reset-failed '{unit}'")
            continue
        ans = _prompt(f"{label} is enabled but no config names it any more.  "
                      "Disable it? (Y/n)", "y")
        if not ans.strip().lower().startswith("y"):
            _info(f"left {label} enabled")
            continue
        r = subprocess.run([sctl, "disable", "--now", unit], capture_output=True, text=True)
        subprocess.run([sctl, "reset-failed", unit], capture_output=True, text=True)
        if r.returncode == 0:
            _ok(f"disabled stale {label}")
        else:
            _info(f"(could not disable {label}: {r.stderr.strip()} -- run: "
                  f"sudo systemctl disable --now '{unit}')")
```

Replace

```python
def _env_file_path(instance: str) -> Path:
    return _ENV_DIR / f"{instance}.env"
```

with

```python
def _env_file_path(instance: str) -> Path:
    """The env file for an instance named in any spelling (AC0G/B4,
    AC0G=B4, AC0G\\x3dB4 all -> AC0G=B4.env; bee1-status.local stays)."""
    try:
        stem = to_storage(instance)
    except ValueError:
        stem = instance
    return _ENV_DIR / f"{stem}.env"
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_reporter_id.py tests/test_instance_args.py tests/test_configurator.py -q`
Expected: PASS.  Then `.venv/bin/pytest tests -q` — expect 286 passed, 1 skipped (275 before, 11 new).

- [ ] **Step 7: Commit**

```bash
git add src/meteor_scatter/_reporter_id.py tests/data/reporter_id_vectors.json tests/test_reporter_id.py \
        tests/test_instance_args.py tests/test_configurator.py src/meteor_scatter/cli.py \
        src/meteor_scatter/configurator.py
git commit -m "reporter ids: read every spelling; never offer to disable the live instance

config init compared the link meteor-scatter@AC0G\\x3dB4.service with
AC0G=B4.toml, called the live instance stale and offered to disable it,
default yes.  _reporter_id.py carries sigmond's rules, checked against a
byte-identical copy of sigmond's table (sigmond
tasks/spec-reporter-id-v372.md §5; M1-M4, M7)."
```

- [ ] **Step 8: Mutation check**

| Change | Run | Must fail |
|---|---|---|
| `configurator.py` `_stale_instances`: `found.add(unescape_systemd_instance(m.group(1)))` → `found.add(m.group(1))` | `tests/test_configurator.py` | `test_a_live_instance_is_never_stale_under_any_spelling` |
| `cli.py`: `args.radiod_id = unescape_systemd_instance(args.radiod_id)` → `args.radiod_id = args.radiod_id` | `tests/test_instance_args.py` | `test_systemd_spelling_becomes_storage` |
| `_reporter_id.py`: in `display`, `prev, name = name, unescape_systemd_instance(name)` → `prev, name = name, name` | `tests/test_reporter_id.py` | `test_reporter_ids` |

After each, `git checkout -- <file>`.

---

### Task 10: psk-recorder carries its own copy and reads every spelling (P1, P4, P5)

Work in `/home/mjh/hamsci/repos/psk-recorder` (Michael's).  Its `.venv` has no pytest: every run below uses
`uv run --frozen --extra dev pytest`, and after each run `git status --short uv.lock` must print nothing (if it does,
`git checkout uv.lock`).

**Files:**
- Create: `src/psk_recorder/_reporter_id.py` (copy of meteor-scatter's, Task 9)
- Create: `tests/data/reporter_id_vectors.json` (copy of sigmond's)
- Create: `tests/test_reporter_id.py`, `tests/test_reporter_id_sites.py`
- Modify: `src/psk_recorder/cli.py:319` (new helper before `_handle_daemon`), `:340-345` (P1, P4)
- Modify: `src/psk_recorder/configurator.py:53` (import), `:1080-1081` (P5)

**Interfaces:**
- Consumes: meteor-scatter's `src/meteor_scatter/_reporter_id.py` (Task 9) and sigmond's table (Task 1).
- Produces: `psk_recorder._reporter_id` (same four functions); `psk_recorder.cli._normalize_instance_args(args)`.

- [ ] **Step 1: Copy the module and the table; write the failing tests**

```bash
cd /home/mjh/hamsci/repos/psk-recorder
mkdir -p tests/data
cp ../sigmond/tests/data/reporter_id_vectors.json tests/data/
```

Create `tests/test_reporter_id.py` (meteor-scatter's file from Task 9 with the import changed):

```python
"""This client's copy of the reporter-id rules, run against the shared table.

tests/data/reporter_id_vectors.json must stay a byte-identical copy of
sigmond's tests/data/reporter_id_vectors.json; sigmond's
tests/test_reporter_id_drift.py fails when they differ.  Refresh it with:

    cp ../sigmond/tests/data/reporter_id_vectors.json tests/data/
"""
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from psk_recorder._reporter_id import (  # noqa: E402
    display, systemd_instance, to_storage, unescape_systemd_instance,
)

VECTORS = json.loads(
    (REPO / "tests" / "data" / "reporter_id_vectors.json").read_text())


class VectorTableTests(unittest.TestCase):

    def test_reporter_ids(self):
        for v in VECTORS["reporter_ids"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(to_storage(v["input"]), v["storage"])
                self.assertEqual(systemd_instance(v["storage"]), v["systemd"])
                self.assertEqual(unescape_systemd_instance(v["systemd"]), v["storage"])
                self.assertEqual(display(v["storage"]), v["display"])
                self.assertEqual(display(v["systemd"]), v["display"])

    def test_legacy_names_pass_through(self):
        for v in VECTORS["legacy_names"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(to_storage(v["input"]), v["storage"])
                self.assertEqual(systemd_instance(v["storage"]), v["systemd"])

    def test_invalid_text_is_refused(self):
        for text in VECTORS["invalid"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                to_storage(text)

    def test_display_never_shows_an_escape(self):
        for v in VECTORS["display_only"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(display(v["input"]), v["display"])


if __name__ == "__main__":
    unittest.main()
```

Create `tests/test_reporter_id_sites.py`:

```python
"""psk-recorder's two reporter-id sites (sigmond tasks/spec-reporter-id-v372.md
§5): the daemon's --instance/--radiod-id, which arrive as %i in systemd's
spelling (P1, P4), and the env file a sigmond call names (P5)."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from psk_recorder import configurator  # noqa: E402
from psk_recorder.cli import _normalize_instance_args  # noqa: E402


class InstanceArgTests(unittest.TestCase):

    def test_systemd_spelling_becomes_storage(self):
        ns = SimpleNamespace(instance="AC0G\\x3dB4", radiod_id="AC0G\\x3dB4")
        _normalize_instance_args(ns)
        self.assertEqual((ns.instance, ns.radiod_id), ("AC0G=B4", "AC0G=B4"))

    def test_every_escape_is_decoded_not_only_x3d(self):
        ns = SimpleNamespace(instance="W1ABC\\x2d5", radiod_id=None)
        _normalize_instance_args(ns)
        self.assertEqual(ns.instance, "W1ABC-5")
        self.assertIsNone(ns.radiod_id)

    def test_plain_names_pass_through(self):
        ns = SimpleNamespace(instance="AC0G-B4", radiod_id="bee1-status.local")
        _normalize_instance_args(ns)
        self.assertEqual((ns.instance, ns.radiod_id), ("AC0G-B4", "bee1-status.local"))


class EnvFilePathTests(unittest.TestCase):

    def test_env_file_path_takes_any_spelling(self):
        for typed in ("AC0G\\x3dB4", "AC0G/B4", "AC0G=B4"):
            with self.subTest(typed=typed):
                self.assertEqual(configurator._env_file_path(typed),
                                 configurator._ENV_DIR / "AC0G=B4.env")
        self.assertEqual(configurator._env_file_path("bee1-status.local"),
                         configurator._ENV_DIR / "bee1-status.local.env")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen --extra dev pytest tests/test_reporter_id.py tests/test_reporter_id_sites.py -q`
Expected: two collection errors (`No module named 'psk_recorder._reporter_id'`; `cannot import name
'_normalize_instance_args' from 'psk_recorder.cli'`).

- [ ] **Step 3: Copy the module**

```bash
cp ../meteor-scatter/src/meteor_scatter/_reporter_id.py src/psk_recorder/_reporter_id.py
cmp ../meteor-scatter/src/meteor_scatter/_reporter_id.py src/psk_recorder/_reporter_id.py && echo identical
```

Expected: `identical`.

- [ ] **Step 4: Fix `cli.py` (P1, P4)**

Insert before `def _handle_daemon(args):` (line 319):

```python
def _normalize_instance_args(args) -> None:
    """Turn --instance and --radiod-id back into the storage form.

    The unit template passes `--instance %i --radiod-id %i`, and %i keeps
    systemd's spelling (AC0G\\x3dB4).  Config lookup, block matching and
    log lines all want AC0G=B4, so decode every \\xHH here, once, before
    anything reads them.  Plain names (AC0G-B4, bee1-status.local) pass
    through unchanged.
    """
    from psk_recorder._reporter_id import unescape_systemd_instance
    if getattr(args, "instance", None):
        args.instance = unescape_systemd_instance(args.instance)
    if getattr(args, "radiod_id", None):
        args.radiod_id = unescape_systemd_instance(args.radiod_id)


```

Inside `_handle_daemon`, replace

```python
    # systemd escapes '=' in unit instance names to '\x3d', so with the
    # %i unit templates a '='-style (reporter-id sentinel) instance
    # reaches the daemon in escaped form.  Unescape so config lookup
    # works for both naming styles ('-' names pass through untouched).
    if args.instance:
        args.instance = args.instance.replace("\\x3d", "=")
```

with

```python
    _normalize_instance_args(args)
```

- [ ] **Step 5: Fix `configurator.py` (P5)**

Replace

```python
from .config import DEFAULT_CONFIG_PATH, DEFAULTS, load_config
```

with

```python
from ._reporter_id import to_storage
from .config import DEFAULT_CONFIG_PATH, DEFAULTS, load_config
```

and replace

```python
def _env_file_path(instance: str) -> Path:
    return _ENV_DIR / f"{instance}.env"
```

with

```python
def _env_file_path(instance: str) -> Path:
    """The env file for an instance named in any spelling (AC0G/B4,
    AC0G=B4, AC0G\\x3dB4 all -> AC0G=B4.env; bee1-status.local stays)."""
    try:
        stem = to_storage(instance)
    except ValueError:
        stem = instance
    return _ENV_DIR / f"{stem}.env"
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --frozen --extra dev pytest tests/test_reporter_id.py tests/test_reporter_id_sites.py tests/test_configurator.py -q`
Expected: PASS.  Then `uv run --frozen --extra dev pytest tests -q` — expect no failures.  Then
`git status --short uv.lock` prints nothing.

- [ ] **Step 7: Commit**

```bash
git add src/psk_recorder/_reporter_id.py tests/data/reporter_id_vectors.json tests/test_reporter_id.py \
        tests/test_reporter_id_sites.py src/psk_recorder/cli.py src/psk_recorder/configurator.py
git commit -m "reporter ids: decode every escape in --instance/--radiod-id; env path takes any spelling

_reporter_id.py carries sigmond's rules, checked against a byte-identical
copy of sigmond's table (sigmond tasks/spec-reporter-id-v372.md §5; P1,
P4, P5)."
```

- [ ] **Step 8: Mutation check**

In `cli.py`, change `args.instance = unescape_systemd_instance(args.instance)` to
`args.instance = args.instance.replace("\\x3d", "=")`.  Run
`uv run --frozen --extra dev pytest tests/test_reporter_id_sites.py -q`; expect
`test_every_escape_is_decoded_not_only_x3d` to FAIL.  Then `git checkout -- src/psk_recorder/cli.py`.

---

### Task 11: The per-instance directory in the unit templates, and the stray directories (meteor-scatter M6, psk-recorder P3)

Both unit templates run `mkdir -p /var/lib/<client>/%i/<mode>` and `chown -R … /var/lib/<client>/%i`.  `%i` keeps
systemd's spelling, which left `/var/lib/psk-recorder/AC0G\x3dB4/` and `/var/lib/meteor-scatter/AC0G\x3dB4/` on
AC0G-B4.  `%I` would unescape `\x3d` but also turn the `-` in `W1ABC-5` into `/`.  So the lines either go (Branch A)
or give way to a `StateDirectory=` that sigmond names in storage form (Branch B).  Step 1 decides which.

This task makes two commits: meteor-scatter first, then psk-recorder.

**Files (Branch A, both repos):**
- Create: `scripts/remove-stray-instance-dirs.sh`, `tests/test_stray_instance_dirs.py`
- Modify: `systemd/meteor-scatter@.service:27-30`; `systemd/psk-recorder@.service:26-30`
- Modify: `scripts/install.sh` — meteor-scatter after line 269, psk-recorder after line 302 (each just after the unit
  template's `systemctl daemon-reload`)

**Files (Branch B adds):** `sigmond/lib/sigmond/instance.py` (new `state_dropin`), `sigmond/bin/smd`
(`cmd_instance_enable`), `sigmond/tests/test_reporter_id_unit_names.py`.

**Interfaces:**
- Consumes: nothing from earlier tasks in Branch A.  Branch B consumes `unit_name` (Task 1) and
  `smd admin instance canonical` (Task 8).
- Produces: `scripts/remove-stray-instance-dirs.sh <state-root>` in each client — always exits 0; prints
  `[INFO]  removed empty stray directory <path>` on stdout, or `[WARN]  left <path> in place: …` and the contents on
  stderr.  Task 15 looks for these lines.

- [ ] **Step 1: Decide the branch by reading the code**

Run:

```bash
cd /home/mjh/hamsci/repos/meteor-scatter
grep -rn "var/lib/meteor-scatter\|spool_dir\|STATE_DIRECTORY\|state_dir" src scripts deploy.toml systemd
grep -rn "args.instance" src | grep -iv "resolve_config_path\|logger\|warning\|log\." 
grep -rn "mkdir(parents=True" src/meteor_scatter/core
cd /home/mjh/hamsci/repos/psk-recorder
grep -rn "var/lib/psk-recorder\|spool_dir\|STATE_DIRECTORY\|state_dir" src scripts deploy.toml systemd
grep -rn "args.instance" src | grep -iv "resolve_config_path\|logger\|warning\|log\."
grep -rn "mkdir(parents=True" src/psk_recorder/core
```

Read every hit that builds a path under the state root.  The planner's reading at `83213d0` / `8dfb64f`:

- every reader joins the root with the radiod id — meteor-scatter `contract.py:67`, `core/recorder.py:223, 625-626,
  869-873`; psk-recorder `contract.py:72`, `core/recorder.py:332, 736-745, 978`;
- nothing joins it with `args.instance`, and nothing reads `$STATE_DIRECTORY`;
- the daemons create their spool directories on first write (`core/wav.py:57`, `core/slot.py:469` / `:574`,
  `core/receiver_manager.py:270` / `:271`, all `mkdir(parents=True, exist_ok=True)`), and `scripts/install.sh` and
  `deploy.toml` create the root itself, owned by the service user.

**If all three hold, take Branch A** (Steps 2A-5A).  **If any reader joins the root with the instance, or reads
`$STATE_DIRECTORY`, take Branch B** (Steps 2B-5B), and write in the commit message which file and line decided it.
Either way, Steps 8-11 (the stray-directory cleanup) follow.

- [ ] **Step 2A: Write the failing unit-template test (meteor-scatter)**

Create `tests/test_stray_instance_dirs.py` in meteor-scatter:

```python
"""The stray AC0G\\x3dB4/ directories an older unit template built under
/var/lib/meteor-scatter go away on install, but only while they hold
nothing but empty mode directories (sigmond tasks/spec-reporter-id-v372.md
§5)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "remove-stray-instance-dirs.sh"
UNIT = REPO / "systemd" / "meteor-scatter@.service"
STATE_ROOT = "/var/lib/meteor-scatter"
MODE = "msk144"


class UnitTemplateTests(unittest.TestCase):

    def test_the_unit_builds_no_per_instance_directory(self):
        # %i keeps systemd's spelling, so this used to make AC0G\x3dB4/.
        self.assertNotIn(f"{STATE_ROOT}/%i", UNIT.read_text())


class StrayDirTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.stray = self.root / "AC0G\\x3dB4"

    def _run(self, root=None):
        return subprocess.run(["bash", str(SCRIPT), str(root or self.root)],
                              capture_output=True, text=True)

    def test_empty_mode_dirs_are_removed(self):
        (self.stray / MODE).mkdir(parents=True)
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.stray.exists())
        self.assertIn("removed empty stray directory", r.stdout)

    def test_a_dir_holding_a_file_stays_and_is_named(self):
        (self.stray / MODE).mkdir(parents=True)
        (self.stray / MODE / "slot.wav").write_bytes(b"")
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertTrue((self.stray / MODE / "slot.wav").exists())
        self.assertIn("slot.wav", r.stderr)

    def test_a_dir_with_nested_dirs_stays(self):
        (self.stray / MODE / "2026").mkdir(parents=True)
        self._run()
        self.assertTrue((self.stray / MODE / "2026").is_dir())

    def test_real_spool_dirs_are_never_touched(self):
        spool = self.root / "B4-100-rx888mk2-status.local" / MODE
        spool.mkdir(parents=True)
        plain = self.root / "AC0G=B4"
        plain.mkdir()
        self._run()
        self.assertTrue(spool.is_dir())
        self.assertTrue(plain.is_dir())

    def test_a_symlink_is_never_followed_or_removed(self):
        # Review Focus 4.
        target = self.root / "elsewhere"
        target.mkdir()
        os.symlink(target, self.stray)
        self._run()
        self.assertTrue(self.stray.is_symlink())
        self.assertTrue(target.is_dir())

    def test_a_missing_root_is_fine(self):
        self.assertEqual(self._run(self.root / "absent").returncode, 0)

    def test_install_runs_it_after_the_unit_reload(self):
        text = (REPO / "scripts" / "install.sh").read_text()
        call = 'bash "$REPO_SOURCE/scripts/remove-stray-instance-dirs.sh" "$SPOOL_DIR"'
        self.assertIn(call, text)
        self.assertGreater(text.index(call), text.index("systemctl daemon-reload"))


if __name__ == "__main__":
    unittest.main()
```

Run: `cd /home/mjh/hamsci/repos/meteor-scatter && .venv/bin/pytest tests/test_stray_instance_dirs.py -q`
Expected: 5 FAIL — `test_the_unit_builds_no_per_instance_directory`, `test_install_runs_it_after_the_unit_reload`,
`test_empty_mode_dirs_are_removed`, `test_a_dir_holding_a_file_stays_and_is_named` and `test_a_missing_root_is_fine`
(bash cannot open the script and exits 127).  The other three pass, since a missing script removes nothing.

- [ ] **Step 3A: Remove the per-instance lines (meteor-scatter)**

In `systemd/meteor-scatter@.service`, replace

```
ExecStartPre=+/bin/mkdir -p /var/lib/meteor-scatter/%i/msk144
ExecStartPre=+/bin/mkdir -p /var/log/meteor-scatter
ExecStartPre=+/usr/bin/chown -R meteorscat:meteorscat /var/lib/meteor-scatter/%i
ExecStartPre=+/usr/bin/chown -R meteorscat:meteorscat /var/log/meteor-scatter
```

with

```
# No per-instance directory under /var/lib/meteor-scatter: the spool keys on
# the radiod id (<spool_dir>/<radiod_id>/msk144), and the daemon creates it
# on first write.  These lines used to build /var/lib/meteor-scatter/%i, and
# %i keeps systemd's spelling, which left a stray AC0G\x3dB4/ beside the real
# spool (sigmond tasks/spec-reporter-id-v372.md §5).  install.sh removes
# those leftovers while they are empty.
ExecStartPre=+/bin/mkdir -p /var/log/meteor-scatter
ExecStartPre=+/usr/bin/chown -R meteorscat:meteorscat /var/log/meteor-scatter
```

- [ ] **Step 4A: Write the failing unit-template test (psk-recorder)**

Create `tests/test_stray_instance_dirs.py` in psk-recorder (the meteor-scatter file with psk-recorder's unit, root and
mode):

```python
"""The stray AC0G\\x3dB4/ directories an older unit template built under
/var/lib/psk-recorder go away on install, but only while they hold
nothing but empty mode directories (sigmond tasks/spec-reporter-id-v372.md
§5)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "remove-stray-instance-dirs.sh"
UNIT = REPO / "systemd" / "psk-recorder@.service"
STATE_ROOT = "/var/lib/psk-recorder"
MODE = "ft8"


class UnitTemplateTests(unittest.TestCase):

    def test_the_unit_builds_no_per_instance_directory(self):
        # %i keeps systemd's spelling, so this used to make AC0G\x3dB4/.
        self.assertNotIn(f"{STATE_ROOT}/%i", UNIT.read_text())


class StrayDirTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.stray = self.root / "AC0G\\x3dB4"

    def _run(self, root=None):
        return subprocess.run(["bash", str(SCRIPT), str(root or self.root)],
                              capture_output=True, text=True)

    def test_empty_mode_dirs_are_removed(self):
        (self.stray / MODE).mkdir(parents=True)
        (self.stray / "ft4").mkdir()
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.stray.exists())
        self.assertIn("removed empty stray directory", r.stdout)

    def test_a_dir_holding_a_file_stays_and_is_named(self):
        (self.stray / MODE).mkdir(parents=True)
        (self.stray / MODE / "slot.wav").write_bytes(b"")
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertTrue((self.stray / MODE / "slot.wav").exists())
        self.assertIn("slot.wav", r.stderr)

    def test_a_dir_with_nested_dirs_stays(self):
        (self.stray / MODE / "2026").mkdir(parents=True)
        self._run()
        self.assertTrue((self.stray / MODE / "2026").is_dir())

    def test_real_spool_dirs_are_never_touched(self):
        spool = self.root / "B4-100-rx888mk2-status.local" / MODE
        spool.mkdir(parents=True)
        plain = self.root / "AC0G=B4"
        plain.mkdir()
        self._run()
        self.assertTrue(spool.is_dir())
        self.assertTrue(plain.is_dir())

    def test_a_symlink_is_never_followed_or_removed(self):
        # Review Focus 4.
        target = self.root / "elsewhere"
        target.mkdir()
        os.symlink(target, self.stray)
        self._run()
        self.assertTrue(self.stray.is_symlink())
        self.assertTrue(target.is_dir())

    def test_a_missing_root_is_fine(self):
        self.assertEqual(self._run(self.root / "absent").returncode, 0)

    def test_install_runs_it_after_the_unit_reload(self):
        text = (REPO / "scripts" / "install.sh").read_text()
        call = 'bash "$REPO_SOURCE/scripts/remove-stray-instance-dirs.sh" "$SPOOL_DIR"'
        self.assertIn(call, text)
        self.assertGreater(text.index(call), text.index("systemctl daemon-reload"))


if __name__ == "__main__":
    unittest.main()
```

Run: `cd /home/mjh/hamsci/repos/psk-recorder && uv run --frozen --extra dev pytest tests/test_stray_instance_dirs.py -q`
Expected: FAIL as in Step 2A.

- [ ] **Step 5A: Remove the per-instance lines (psk-recorder)**

In `systemd/psk-recorder@.service`, replace

```
ExecStartPre=+/bin/mkdir -p /var/lib/psk-recorder/%i/ft8
ExecStartPre=+/bin/mkdir -p /var/lib/psk-recorder/%i/ft4
ExecStartPre=+/bin/mkdir -p /var/log/psk-recorder
ExecStartPre=+/usr/bin/chown -R pskrec:pskrec /var/lib/psk-recorder/%i
ExecStartPre=+/usr/bin/chown -R pskrec:pskrec /var/log/psk-recorder
```

with

```
# No per-instance directory under /var/lib/psk-recorder: the spool keys on
# the radiod id (<spool_dir>/<radiod_id>/{ft8,ft4}), and the daemon creates
# it on first write.  These lines used to build /var/lib/psk-recorder/%i, and
# %i keeps systemd's spelling, which left a stray AC0G\x3dB4/ beside the real
# spool (sigmond tasks/spec-reporter-id-v372.md §5).  install.sh removes
# those leftovers while they are empty.
ExecStartPre=+/bin/mkdir -p /var/log/psk-recorder
ExecStartPre=+/usr/bin/chown -R pskrec:pskrec /var/log/psk-recorder
```

Branch A ends here; go to Step 8.

- [ ] **Step 2B (Branch B only): Write the failing sigmond test**

Append to `sigmond/tests/test_reporter_id_unit_names.py`, before `if __name__ == "__main__":`:

```python
class StateDropinTests(unittest.TestCase):
    """Branch B of plan Task 11: sigmond names the state directory in storage form."""

    def test_state_dropin_for_a_reporter_instance(self):
        path, body = I.state_dropin("psk-recorder", "AC0G=B4")
        self.assertEqual(path, Path("/etc/systemd/system/"
                                    "psk-recorder@AC0G\\x3dB4.service.d/"
                                    "10-state-directory.conf"))
        self.assertIn("StateDirectory=psk-recorder/AC0G=B4\n", body)

    def test_no_dropin_for_a_client_without_one(self):
        self.assertIsNone(I.state_dropin("wspr-recorder", "AC0G=B4"))
```

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_reporter_id_unit_names.py -q --override-ini addopts=`
Expected: FAIL with `AttributeError: module 'sigmond.instance' has no attribute 'state_dropin'`.

- [ ] **Step 3B (Branch B only): Add `state_dropin` and write it on enable**

In `sigmond/lib/sigmond/instance.py`, after `def instance_paths(...)` and its body, add:

```python
# Clients whose unit template reads a per-instance state directory that
# sigmond names (plan-reporter-id-v372 Task 11, Branch B).  %i would keep
# systemd's spelling and %I would turn W1ABC-5 into W1ABC/5, so sigmond
# writes the storage form into a drop-in instead.
_STATE_DIR_CLIENTS = ("psk-recorder", "meteor-scatter")


def state_dropin(client: str, reporter_id: str) -> "Optional[tuple[Path, str]]":
    """(path, body) of the drop-in that gives an instance its state
    directory, /var/lib/<client>/<storage form>; None for other clients."""
    if client not in _STATE_DIR_CLIENTS:
        return None
    path = (Path("/etc/systemd/system") / f"{unit_name(client, reporter_id)}.d"
            / "10-state-directory.conf")
    body = ("# Written by `smd admin instance enable`: this instance's state\n"
            "# directory, named in storage form (AC0G=B4, never AC0G\\x3dB4).\n"
            "[Service]\n"
            f"StateDirectory={client}/{reporter_id}\n")
    return path, body
```

In `sigmond/bin/smd`, `cmd_instance_enable`, replace

```python
    import subprocess
    for action in ('enable', 'start'):
```

with

```python
    import subprocess
    dropin = inst.state_dropin(args.client, args.reporter_id)
    if dropin is not None:
        dropin_path, dropin_body = dropin
        dropin_path.parent.mkdir(parents=True, exist_ok=True)
        dropin_path.write_text(dropin_body)
        subprocess.run(['systemctl', 'daemon-reload'], check=False)
    for action in ('enable', 'start'):
```

Run the Step 2B command again.  Expected: PASS.  Commit in sigmond:

```bash
git add lib/sigmond/instance.py bin/smd tests/test_reporter_id_unit_names.py
git commit -m "instance: name a recorder's state directory in storage form (plan Task 11, Branch B)

<file:line that decided Branch B> reads /var/lib/<client>/<instance>.
%i would keep AC0G\\x3dB4 and %I would turn W1ABC-5 into W1ABC/5, so
smd admin instance enable writes StateDirectory=<client>/AC0G=B4."
```

(Replace the angle-bracket line with the file and line Step 1 found; that one line comes from your reading, not
from this plan.)

- [ ] **Step 4B (Branch B only): Change both unit templates**

Make the same replacements as Steps 3A and 5A; they remove the `%i` lines.  The templates gain no `StateDirectory=`
line of their own: the drop-in carries it.  Then, in each client's `scripts/install.sh`, write the drop-in for
instances enabled before this change, just after the template's `systemctl daemon-reload`.  meteor-scatter:

```bash
# --- Phase 5.4: state directory for instances enabled before v3.72 ---
# sigmond writes this drop-in on `smd admin instance enable`; instances
# enabled earlier get it here, named in storage form (AC0G=B4).
for link in /etc/systemd/system/*.wants/meteor-scatter@*.service; do
    [ -e "$link" ] || continue
    unit=$(basename "$link")
    inst=${unit#meteor-scatter@}; inst=${inst%.service}
    rid=$(smd admin instance canonical "$inst" 2>/dev/null) || continue
    mkdir -p "/etc/systemd/system/$unit.d"
    printf '[Service]\nStateDirectory=meteor-scatter/%s\n' "$rid" \
        > "/etc/systemd/system/$unit.d/10-state-directory.conf"
done
systemctl daemon-reload
```

psk-recorder:

```bash
# --- Phase 5.4: state directory for instances enabled before v3.72 ---
# sigmond writes this drop-in on `smd admin instance enable`; instances
# enabled earlier get it here, named in storage form (AC0G=B4).
for link in /etc/systemd/system/*.wants/psk-recorder@*.service; do
    [ -e "$link" ] || continue
    unit=$(basename "$link")
    inst=${unit#psk-recorder@}; inst=${inst%.service}
    rid=$(smd admin instance canonical "$inst" 2>/dev/null) || continue
    mkdir -p "/etc/systemd/system/$unit.d"
    printf '[Service]\nStateDirectory=psk-recorder/%s\n' "$rid" \
        > "/etc/systemd/system/$unit.d/10-state-directory.conf"
done
systemctl daemon-reload
```

- [ ] **Step 5B (Branch B only): Extend each client's test**

In both `tests/test_stray_instance_dirs.py` files, replace `test_the_unit_builds_no_per_instance_directory` with:

```python
    def test_the_unit_builds_no_per_instance_directory(self):
        # %i keeps systemd's spelling; the state directory comes from the
        # drop-in sigmond writes in storage form.
        self.assertNotIn(f"{STATE_ROOT}/%i", UNIT.read_text())
        self.assertIn("10-state-directory.conf",
                      (REPO / "scripts" / "install.sh").read_text())
```

- [ ] **Step 6B (Branch B only):** continue with Step 8.

- [ ] **Step 8: Write the cleanup script (both repos)**

Create `scripts/remove-stray-instance-dirs.sh` in meteor-scatter with this content, then copy it to psk-recorder
(`cp ../meteor-scatter/scripts/remove-stray-instance-dirs.sh scripts/`).  Make both executable (`chmod 755`).

```bash
#!/bin/bash
# remove-stray-instance-dirs.sh — remove the per-instance directories an
# older unit template left under the state root.
#
# Before v3.72 the unit ran `mkdir -p <state-root>/%i/<mode>`, and %i keeps
# systemd's spelling, so a reporter-keyed instance (AC0G=B4) got a directory
# named AC0G\x3dB4 that nothing reads: the spool keys on the radiod id.
# This removes such a directory only when it holds nothing but empty
# directories one level down (the mode directories the template made).
# Anything else stays, and the script names what it found.
#
# Usage: remove-stray-instance-dirs.sh <state-root>
#   e.g. remove-stray-instance-dirs.sh /var/lib/meteor-scatter
# Always exits 0: a leftover directory must never fail an install.
set -u
root="${1:?usage: $0 <state-root>}"
[ -d "$root" ] || exit 0
shopt -s nullglob
for d in "$root"/*'\x'[0-9a-fA-F][0-9a-fA-F]*; do
    [ -d "$d" ] && [ ! -L "$d" ] || continue
    if [ -z "$(find "$d" -mindepth 1 ! -type d -print -quit)" ] \
       && [ -z "$(find "$d" -mindepth 2 -print -quit)" ]; then
        rm -r -- "$d" && echo "[INFO]  removed empty stray directory $d"
    else
        echo "[WARN]  left $d in place: it holds more than empty mode directories:" >&2
        find "$d" -mindepth 1 | head -20 >&2
    fi
done
exit 0
```

- [ ] **Step 9: Call it from each `install.sh`**

`smd update --apply` runs `scripts/install.sh` after it pulls (`sigmond/lib/sigmond/update.py:188-196`,
`sigmond/bin/smd:4018-4050`).  In meteor-scatter `scripts/install.sh`, replace

```bash
    /etc/systemd/system/meteor-scatter@.service
systemctl daemon-reload
```

with

```bash
    /etc/systemd/system/meteor-scatter@.service
systemctl daemon-reload

# --- Phase 5.5: stray per-instance directories ---
# The unit template before v3.72 built /var/lib/meteor-scatter/%i, and %i
# keeps systemd's spelling (AC0G\x3dB4).  Nothing reads those directories;
# the spool keys on the radiod id.  Removed only while empty.
bash "$REPO_SOURCE/scripts/remove-stray-instance-dirs.sh" "$SPOOL_DIR"
```

In psk-recorder `scripts/install.sh`, replace

```bash
    /etc/systemd/system/psk-recorder@.service
systemctl daemon-reload
```

with

```bash
    /etc/systemd/system/psk-recorder@.service
systemctl daemon-reload

# --- Phase 5.5: stray per-instance directories ---
# The unit template before v3.72 built /var/lib/psk-recorder/%i, and %i
# keeps systemd's spelling (AC0G\x3dB4).  Nothing reads those directories;
# the spool keys on the radiod id.  Removed only while empty.
bash "$REPO_SOURCE/scripts/remove-stray-instance-dirs.sh" "$SPOOL_DIR"
```

The call runs after `daemon-reload`, so a unit that restarts meanwhile already runs the new template and cannot make
the directory again.  Each `install.sh` runs `systemctl daemon-reload` only once (meteor-scatter line 269,
psk-recorder line 302), so the test's `text.index` finds that one.

- [ ] **Step 10: Run the tests to verify they pass**

```bash
cd /home/mjh/hamsci/repos/meteor-scatter && .venv/bin/pytest tests/test_stray_instance_dirs.py -q && .venv/bin/pytest tests -q
cd /home/mjh/hamsci/repos/psk-recorder && uv run --frozen --extra dev pytest tests/test_stray_instance_dirs.py -q \
    && uv run --frozen --extra dev pytest tests -q && git status --short uv.lock
```

Expected: PASS in both; the last command prints nothing.  Also `bash -n scripts/install.sh` in each repo prints
nothing.

- [ ] **Step 11: Commit (two commits)**

```bash
cd /home/mjh/hamsci/repos/meteor-scatter
git add systemd/meteor-scatter@.service scripts/remove-stray-instance-dirs.sh scripts/install.sh \
        tests/test_stray_instance_dirs.py
git commit -m "unit: stop building /var/lib/meteor-scatter/%i; install removes the empty leftovers

%i keeps systemd's spelling, so AC0G=B4 got a stray AC0G\\x3dB4/ that
nothing reads (the spool keys on the radiod id).  install.sh removes it
only while it holds nothing but empty mode directories (sigmond
tasks/spec-reporter-id-v372.md §5; M6)."
cd /home/mjh/hamsci/repos/psk-recorder
git add systemd/psk-recorder@.service scripts/remove-stray-instance-dirs.sh scripts/install.sh \
        tests/test_stray_instance_dirs.py
git commit -m "unit: stop building /var/lib/psk-recorder/%i; install removes the empty leftovers

%i keeps systemd's spelling, so AC0G=B4 got a stray AC0G\\x3dB4/ that
nothing reads (the spool keys on the radiod id).  install.sh removes it
only while it holds nothing but empty mode directories (sigmond
tasks/spec-reporter-id-v372.md §5; P3)."
```

In Branch B, name the deciding file and line in both messages too.

- [ ] **Step 12: Mutation check**

In meteor-scatter `scripts/remove-stray-instance-dirs.sh`, change `-mindepth 2 -print -quit` to
`-mindepth 3 -print -quit`.  Run `.venv/bin/pytest tests/test_stray_instance_dirs.py -q`; expect
`test_a_dir_with_nested_dirs_stays` to FAIL.  Then change `[ -d "$d" ] && [ ! -L "$d" ] || continue` to
`[ -d "$d" ] || continue`; expect `test_a_symlink_is_never_followed_or_removed` to FAIL.  After each,
`git checkout -- scripts/remove-stray-instance-dirs.sh`.

---

### Task 12: wspr-recorder decodes every escape in `--instance` (Rob's repository; W1)

wspr-recorder belongs to Rob.  Tell him on the bus before you touch it.

**Files:**
- Create: `wspr_recorder/_reporter_id.py` (copy of meteor-scatter's, Task 9)
- Create: `tests/data/reporter_id_vectors.json` (copy of sigmond's)
- Create: `tests/test_reporter_id.py`
- Modify: `wspr_recorder/cli.py:314-321` (W1)

**Interfaces:**
- Consumes: meteor-scatter's `_reporter_id.py` (Task 9); sigmond's table (Task 1).
- Produces: `wspr_recorder._reporter_id` (same four functions).

- [ ] **Step 1: Read the bus, then post the change**

Read it first, as `/home/mjh/hamsci/CLAUDE.md` requires:

```bash
id -nG | tr ' ' '\n' | grep -qx hamsci && echo in-group || echo use-sudo
sudo -n cat /srv/hamsci/claude-bus/README.md
sudo -n bash -c 'ls -1 /srv/hamsci/claude-bus/ | grep "^2026" | sort | tail -10'
```

Read the newest messages in that list (`sudo -n bash -c 'cat /srv/hamsci/claude-bus/<name>'`).  If Rob has asked
that nobody touch wspr-recorder, stop and report.

Write this draft with the Write tool to `/tmp/bus-draft-wspr-reporter-id.md`:

```markdown
# wspr-recorder: one small change for reporter ids (v3.72), before I touch your repo

From mjh's Claude.  Michael approved sigmond tasks/spec-reporter-id-v372.md
on 2026-10-14.  The plan is tasks/plan-reporter-id-v372.md (Task 12).

What changes in wspr-recorder, and nothing else:

- wspr_recorder/cli.py, _handle_daemon: --instance %i reaches the daemon in
  systemd's spelling (AC0G\x3dB4).  Today the code turns only \x3d back into
  '='.  It will call _reporter_id.unescape_systemd_instance(), which decodes
  every \xHH.  For AC0G\x3dB4 the result stays AC0G=B4, as today.
- New wspr_recorder/_reporter_id.py: about 50 lines, stdlib only, a copy of
  sigmond's instance.py rules.  Same file in psk-recorder and meteor-scatter.
- New tests/data/reporter_id_vectors.json: byte-identical copy of sigmond's
  table.  New tests/test_reporter_id.py runs the module against it.

No unit, file, directory, config key or upload path changes.  Committed
locally on main; nothing pushed.  Say so here if you would rather make the
change yourself, or not at all.
```

Then post it:

```bash
f=/srv/hamsci/claude-bus/$(date -u +%Y%m%dT%H%M%SZ)-mjh.md
sudo -n bash -c "cp /tmp/bus-draft-wspr-reporter-id.md $f; chown mjh:hamsci $f; chmod 660 $f"
sudo -n bash -c "ls -l $f"
```

Expected: one line, `-rw-rw---- 1 mjh hamsci … <timestamp>-mjh.md`.  Then delete the draft with
`rm /tmp/bus-draft-wspr-reporter-id.md`.

- [ ] **Step 2: Copy the table and write the failing test**

```bash
cd /home/mjh/hamsci/repos/wspr-recorder
mkdir -p tests/data
cp ../sigmond/tests/data/reporter_id_vectors.json tests/data/
```

Create `tests/test_reporter_id.py`:

```python
"""This client's copy of the reporter-id rules, run against the shared
table, and the one place the daemon uses it (sigmond
tasks/spec-reporter-id-v372.md §5, W1).

tests/data/reporter_id_vectors.json must stay a byte-identical copy of
sigmond's tests/data/reporter_id_vectors.json; sigmond's
tests/test_reporter_id_drift.py fails when they differ.  Refresh it with:

    cp ../sigmond/tests/data/reporter_id_vectors.json tests/data/

Both modules load by file path or under a stand-in package:
wspr_recorder/__init__.py imports ka9q-python and hamsci-dsp, which a
test venv may lack (the same reason tests/test_configurator.py does this).
"""
import importlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "wspr_recorder"

_spec = importlib.util.spec_from_file_location(
    "_wspr_reporter_id_under_test", PKG / "_reporter_id.py")
rid = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rid)

VECTORS = json.loads(
    (REPO / "tests" / "data" / "reporter_id_vectors.json").read_text())


class VectorTableTests(unittest.TestCase):

    def test_reporter_ids(self):
        for v in VECTORS["reporter_ids"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(rid.to_storage(v["input"]), v["storage"])
                self.assertEqual(rid.systemd_instance(v["storage"]), v["systemd"])
                self.assertEqual(rid.unescape_systemd_instance(v["systemd"]), v["storage"])
                self.assertEqual(rid.display(v["storage"]), v["display"])
                self.assertEqual(rid.display(v["systemd"]), v["display"])

    def test_legacy_names_pass_through(self):
        for v in VECTORS["legacy_names"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(rid.to_storage(v["input"]), v["storage"])
                self.assertEqual(rid.systemd_instance(v["storage"]), v["systemd"])

    def test_invalid_text_is_refused(self):
        for text in VECTORS["invalid"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                rid.to_storage(text)

    def test_display_never_shows_an_escape(self):
        for v in VECTORS["display_only"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(rid.display(v["input"]), v["display"])


class DaemonInstanceTests(unittest.TestCase):
    """W1: _handle_daemon hands the legacy entry point the storage form."""

    def _daemon_argv(self, instance):
        pkg = types.ModuleType("wspr_recorder")
        pkg.__path__ = [str(PKG)]
        seen = {}
        legacy = types.ModuleType("wspr_recorder.__main__")

        def main():
            seen["argv"] = list(sys.argv)
            return 0
        legacy.main = main
        pkg.__main__ = legacy
        with mock.patch.dict(sys.modules, {"wspr_recorder": pkg,
                                           "wspr_recorder.__main__": legacy}), \
             mock.patch.object(sys, "argv", ["wspr-recorder"]):
            sys.modules.pop("wspr_recorder.cli", None)
            sys.modules.pop("wspr_recorder._reporter_id", None)
            cli = importlib.import_module("wspr_recorder.cli")
            with mock.patch.object(cli, "_install_sighup_handler", lambda: None), \
                 self.assertRaises(SystemExit):
                cli._handle_daemon(SimpleNamespace(config=None, instance=instance,
                                                   memprofile=False))
        return seen["argv"]

    def test_systemd_spelling_becomes_storage(self):
        self.assertEqual(self._daemon_argv("AC0G\\x3dB4")[-2:], ["--instance", "AC0G=B4"])

    def test_every_escape_is_decoded_not_only_x3d(self):
        self.assertEqual(self._daemon_argv("W1ABC\\x2d5")[-2:], ["--instance", "W1ABC-5"])


if __name__ == "__main__":
    unittest.main()
```

Run: `.venv/bin/pytest tests/test_reporter_id.py -q`
Expected: FAIL at collection — `FileNotFoundError` for `wspr_recorder/_reporter_id.py`.

- [ ] **Step 3: Copy the module**

```bash
cp ../meteor-scatter/src/meteor_scatter/_reporter_id.py wspr_recorder/_reporter_id.py
```

Run: `.venv/bin/pytest tests/test_reporter_id.py -q`
Expected: 5 pass, `test_every_escape_is_decoded_not_only_x3d` FAIL (`W1ABC\x2d5` reaches the daemon unchanged).

- [ ] **Step 4: Change `cli.py` (W1)**

In `_handle_daemon`, replace

```python
    if getattr(args, "instance", None):
        # systemd escapes '=' in unit instance names to '\x3d', so with
        # the %i unit templates a '='-style (reporter-id sentinel)
        # instance reaches us in escaped form.  Unescape so config
        # lookup works for both naming styles ('-' names untouched).
        legacy_argv += [
            "--instance", str(args.instance).replace("\\x3d", "="),
        ]
```

with

```python
    if getattr(args, "instance", None):
        # The unit passes --instance %i, and %i keeps systemd's spelling
        # (AC0G\x3dB4).  Decode every \xHH so config lookup finds
        # /etc/wspr-recorder/AC0G=B4.toml ('-' names pass untouched).
        from ._reporter_id import unescape_systemd_instance
        legacy_argv += [
            "--instance", unescape_systemd_instance(str(args.instance)),
        ]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_reporter_id.py -q && .venv/bin/pytest tests -q`
Expected: PASS; the full suite shows no new failure.

- [ ] **Step 6: Commit**

```bash
git add wspr_recorder/_reporter_id.py tests/data/reporter_id_vectors.json tests/test_reporter_id.py \
        wspr_recorder/cli.py
git commit -m "cli: decode every systemd escape in --instance, through a copy of sigmond's rules

_reporter_id.py carries sigmond's reporter-id rules, checked against a
byte-identical copy of sigmond's table.  Announced on the claude-bus
first (sigmond tasks/spec-reporter-id-v372.md §5; W1)."
```

- [ ] **Step 7: Mutation check**

In `wspr_recorder/cli.py`, change `unescape_systemd_instance(str(args.instance))` to
`str(args.instance).replace("\\x3d", "=")`.  Run `.venv/bin/pytest tests/test_reporter_id.py -q`; expect
`test_every_escape_is_decoded_not_only_x3d` to FAIL.  Then `git checkout -- wspr_recorder/cli.py`.

---

### Task 13: The appliance asks smd for the forms (sigmond-appliance; A1-A4)

Work in `/home/mjh/hamsci/repos/sigmond-appliance`.

**Files:**
- Create: `test-site-timing-meteor.sh`
- Modify: `sigmond-site-timing:426-466` (step 4.5)

**Interfaces:**
- Consumes: `smd admin instance canonical <text> [--systemd]` (Task 8): one line on stdout and exit 0, or nothing on
  stdout and exit 1.
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the failing test**

Create `test-site-timing-meteor.sh` (and `chmod 755` it).  Every `rm` in it guards its variable with `${T:?}`:

```bash
#!/bin/bash
# test-site-timing-meteor.sh — sigmond-site-timing step 4.5 (meteor-scatter)
# names the config file in the storage form, the unit in the form systemd
# gives it, and people's text in the display form, by asking smd, never tr
# or systemd-escape (sigmond tasks/spec-reporter-id-v372.md §6).  Runs the
# step verbatim against stubbed smd and systemctl.  No root, no network.
#
# systemd-escape(1) turns '-' into \x2d: W1ABC-5 became W1ABC\x2d5, a unit
# nobody enabled.  `tr '/' '='` never upper-cased, so a lower-case reporter
# id split the config file's name from the unit's.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
T=$(mktemp -d)
trap 'rm -rf "${T:?}"' EXIT
fail=0

awk '/^# ── 4\.5 meteor-scatter/{f=1} /^# ── 4\.7 ka9q-web/{f=0} f' \
    "$HERE/sigmond-site-timing" \
    | sed -e "s#/etc/sigmond/site-profile.toml#$T/site-profile.toml#g" \
          -e "s#/etc/radio/#$T/radio/#g" \
          -e "s#/etc/meteor-scatter/#$T/meteor-scatter/#g" \
          -e "s#/opt/git/sigmond/meteor-scatter#$T/opt-meteor#g" > "$T/step.sh"
grep -q 'meteor-scatter' "$T/step.sh" || { echo "FAIL: step 4.5 not found in sigmond-site-timing"; exit 1; }
mkdir -p "$T/bin" "$T/radio" "$T/meteor-scatter" "$T/opt-meteor"
: > "$T/radio/radiod@b4.conf"

# The smd stub answers `admin instance canonical` as sigmond does for the ids
# these cases use, and exits 1 with nothing on stdout for anything else —
# which is also what an smd older than v3.72 does with the unknown verb.
cat > "$T/bin/smd" <<EOF
#!/bin/bash
echo "\$*" >> "$T/smd.log"
case "\$*" in
  "admin instance canonical AC0G/B4"|"admin instance canonical ac0g/b4") echo 'AC0G=B4' ;;
  "admin instance canonical --systemd AC0G/B4"|"admin instance canonical --systemd ac0g/b4") echo 'AC0G\x3dB4' ;;
  "admin instance canonical W1ABC-5"|"admin instance canonical --systemd W1ABC-5") echo 'W1ABC-5' ;;
  "admin instance canonical "*) echo "smd: invalid reporter ID" >&2; exit 1 ;;
esac
exit 0
EOF
printf '#!/bin/bash\necho "$*" >> %s/systemctl.log\n' "$T" > "$T/bin/systemctl"
printf '#!/bin/bash\necho "$*" >> %s/escape.log\necho WRONG\n' "$T" > "$T/bin/systemd-escape"
chmod +x "$T/bin/smd" "$T/bin/systemctl" "$T/bin/systemd-escape"

run_case() {  # run_case <name> <reporter_id> <want-conf|-> <want-unit|-> <want-display|->
    local name=$1 rid=$2 conf=$3 unit=$4 disp=$5 ok=1 why=""
    rm -f "${T:?}/smd.log" "${T:?}/systemctl.log" "${T:?}/escape.log" "${T:?}/say.log"
    find "${T:?}/meteor-scatter" -name '*.toml' -delete
    printf '[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n[reporters]\nreporter_id = "%s"\n' \
        "$rid" > "$T/site-profile.toml"
    PATH="$T/bin:$PATH" bash -c "say(){ echo \"\$*\" >> $T/say.log; }; source $T/step.sh"
    if [ -s "$T/escape.log" ]; then ok=0; why="$why; called systemd-escape"; fi
    if [ "$conf" = - ]; then
        if ls "$T"/meteor-scatter/*.toml >/dev/null 2>&1; then ok=0; why="$why; wrote a config"; fi
        if grep -q 'enable' "$T/systemctl.log" 2>/dev/null; then ok=0; why="$why; enabled a unit"; fi
        grep -q 'WARN' "$T/say.log" 2>/dev/null || { ok=0; why="$why; said no WARN"; }
    else
        [ -f "$T/meteor-scatter/$conf" ] || { ok=0; why="$why; no $conf"; }
        grep -qxF "enable --now meteor-scatter@$unit.service" "$T/systemctl.log" 2>/dev/null \
            || { ok=0; why="$why; systemctl got: $(cat "$T/systemctl.log" 2>/dev/null)"; }
        head -1 "$T/meteor-scatter/$conf" 2>/dev/null \
            | grep -qxF "# Per-instance config for meteor-scatter@$disp" \
            || { ok=0; why="$why; header: $(head -1 "$T/meteor-scatter/$conf" 2>/dev/null)"; }
        grep -qxF "reporter_id = \"$disp\"" "$T/meteor-scatter/$conf" 2>/dev/null \
            || { ok=0; why="$why; reporter_id line is not \"$disp\""; }
        grep -qF "meteor-scatter@$disp configured" "$T/say.log" 2>/dev/null \
            || { ok=0; why="$why; said: $(cat "$T/say.log" 2>/dev/null)"; }
    fi
    if [ "$ok" = 1 ]; then echo "  ok   $name"; else echo "  FAIL $name:$why"; fail=1; fi
}

run_case "slash id: AC0G=B4.toml, unit AC0G\\x3dB4, said AC0G/B4" 'AC0G/B4' 'AC0G=B4.toml' 'AC0G\x3dB4' 'AC0G/B4'
run_case "hyphen id keeps its hyphen in the unit name"              'W1ABC-5' 'W1ABC-5.toml' 'W1ABC-5'    'W1ABC-5'
run_case "lower-case id lands on the same file and unit"           'ac0g/b4' 'AC0G=B4.toml' 'AC0G\x3dB4' 'AC0G/B4'
run_case "invalid id (or an old smd): WARN, nothing written"        'AC0G/'   -              -            -

[ "$fail" = 0 ] && echo "PASS: site-timing step 4.5" || { echo "FAILED"; exit 1; }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/mjh/hamsci/repos/sigmond-appliance && bash test-site-timing-meteor.sh`
Expected: four `FAIL` lines and `FAILED`, exit 1.  Each case reports `called systemd-escape` and `systemctl got:
enable --now meteor-scatter@WRONG`; the slash case reports the header `meteor-scatter@AC0G=B4` (A2) and says
`meteor-scatter@AC0G=B4 configured` (A4); the lower-case case reports `no AC0G=B4.toml` (A1).

- [ ] **Step 3: Change step 4.5**

In `sigmond-site-timing`, replace

```bash
    if [ -n "$REPORTER" ] && [ -n "$RADID" ]; then
        INST=$(echo "$REPORTER" | tr '/' '=')
        MCONF="/etc/meteor-scatter/$INST.toml"
        if ! grep -q '\[\[radiod\]\]' "$MCONF" 2>/dev/null; then
            smd install --components meteor-scatter --yes >/dev/null 2>&1
            smd admin instance add --force meteor-scatter "$REPORTER" >/dev/null 2>&1
            python3 - "$MCONF" "$REPORTER" "$RADID" <<'PYEOF'
```

with

```bash
    if [ -n "$REPORTER" ] && [ -n "$RADID" ]; then
        # smd owns the three spellings of a reporter id: the storage form
        # names the file (AC0G=B4.toml), the systemd form names the unit
        # (meteor-scatter@AC0G\x3dB4).  Never systemd-escape: it turns '-'
        # into \x2d, a unit nobody enabled.  The display form (AC0G/B4) is
        # the canonical storage form with '=' turned back into '/'.
        if INST=$(smd admin instance canonical "$REPORTER" 2>/dev/null) \
           && UNIT_INST=$(smd admin instance canonical --systemd "$REPORTER" 2>/dev/null) \
           && [ -n "$INST" ] && [ -n "$UNIT_INST" ]; then
            SHOWN=$(printf '%s' "$INST" | tr '=' '/')
            MCONF="/etc/meteor-scatter/$INST.toml"
            if ! grep -q '\[\[radiod\]\]' "$MCONF" 2>/dev/null; then
                smd install --components meteor-scatter --yes >/dev/null 2>&1
                smd admin instance add --force meteor-scatter "$REPORTER" >/dev/null 2>&1
                python3 - "$MCONF" "$SHOWN" "$RADID" <<'PYEOF'
```

Leave the python body between `PYEOF` lines alone except its last argument tuple: replace

```python
""" % (reporter.replace('/','='), reporter, radid,
```

with

```python
""" % (reporter, reporter, radid,
```

(`reporter` now holds the display form, so the comment header reads `# Per-instance config for
meteor-scatter@AC0G/B4` (A2) and `reporter_id = "AC0G/B4"` holds even for a lower-case entry (A1).)  Then replace

```bash
            systemctl enable --now "meteor-scatter@$(systemd-escape "$INST")" 2>/dev/null \
                && say "meteor-scatter@$INST configured + started (MSK144 10m/6m)" \
                || say "WARN: meteor-scatter@$INST failed to start - check journal"
        fi
    fi
fi
```

with

```bash
                systemctl enable --now "meteor-scatter@$UNIT_INST.service" 2>/dev/null \
                    && say "meteor-scatter@$SHOWN configured + started (MSK144 10m/6m)" \
                    || say "WARN: meteor-scatter@$SHOWN failed to start - check journal"
            fi
        else
            say "WARN: smd could not read reporter id '$REPORTER' (an invalid id, or an smd older than v3.72) - meteor-scatter not configured"
        fi
    fi
fi
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash -n sigmond-site-timing && bash test-site-timing-meteor.sh && bash test-site-timing-t4.sh && bash test-site-timing-metrology.sh`
Expected: no syntax output, then `PASS: site-timing step 4.5`, and the two existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add sigmond-site-timing test-site-timing-meteor.sh
git commit -m "site-timing: ask smd for the reporter id's file and unit forms

tr '/' '=' never upper-cased, and systemd-escape turned W1ABC-5 into
W1ABC\\x2d5.  smd admin instance canonical gives both forms; messages and
the config header show AC0G/B4 (sigmond tasks/spec-reporter-id-v372.md
§6; A1-A4).  Needs sigmond v3.72 for the canonical verb; an older smd
gets a WARN line, not a half-made instance."
```

- [ ] **Step 6: Mutation check**

In `sigmond-site-timing`, change `"meteor-scatter@$UNIT_INST.service"` to `"meteor-scatter@$INST.service"`.  Run
`bash test-site-timing-meteor.sh`; expect the slash and lower-case cases to FAIL (`systemctl got: enable --now
meteor-scatter@AC0G=B4.service`).  Then `git checkout -- sigmond-site-timing`.

---

### Task 14: sigmond catches a client's drifted table (sigmond)

**Files:**
- Create: `tests/test_reporter_id_drift.py`

**Interfaces:**
- Consumes: `tests/data/reporter_id_vectors.json` (Task 1) and the client copies (Tasks 9, 10, 12).
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the test**

Create `tests/test_reporter_id_drift.py`:

```python
"""Each client's copy of the reporter-id table matches sigmond's, byte for
byte (tasks/spec-reporter-id-v372.md §5, "The guard against drift").

psk-recorder, meteor-scatter and wspr-recorder each keep a small
_reporter_id.py rather than importing sigmond, and run it against their
copy of tests/data/reporter_id_vectors.json.  That only guards anything
while the copies agree.  A client checkout absent from beside sigmond
skips its test; a checkout that is present but lacks the copy fails.
"""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TABLE = REPO / "tests" / "data" / "reporter_id_vectors.json"


class SiblingTableTests(unittest.TestCase):

    def _check(self, client: str) -> None:
        checkout = REPO.parent / client
        if not checkout.is_dir():
            self.skipTest(f"no {client} checkout beside sigmond at {checkout}")
        copy = checkout / "tests" / "data" / "reporter_id_vectors.json"
        refresh = f"cp {TABLE} {copy}"
        self.assertTrue(copy.is_file(), f"{client} has no copy of the table; {refresh}")
        self.assertEqual(copy.read_bytes(), TABLE.read_bytes(),
                         f"{client}'s copy differs from sigmond's; {refresh}")

    def test_psk_recorder(self):
        self._check("psk-recorder")

    def test_meteor_scatter(self):
        self._check("meteor-scatter")

    def test_wspr_recorder(self):
        self._check("wspr-recorder")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Watch it fail on a drifted copy**

Tasks 9, 10 and 12 already copied the table, so the test passes as written.  Prove it can fail first:

```bash
cd /home/mjh/hamsci/repos/meteor-scatter && printf ' ' >> tests/data/reporter_id_vectors.json
cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_reporter_id_drift.py -q --override-ini addopts=
```

Expected: `test_meteor_scatter` FAIL with "meteor-scatter's copy differs from sigmond's; cp …".  Restore:
`cd /home/mjh/hamsci/repos/meteor-scatter && git checkout -- tests/data/reporter_id_vectors.json`.

- [ ] **Step 3: Run it to verify it passes**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_reporter_id_drift.py tests/test_reporter_id_forms.py -q --override-ini addopts=`
Expected: PASS (3 drift tests, none skipped on this machine).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest tests -q --override-ini addopts=`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_reporter_id_drift.py
git commit -m "tests: fail when a client's reporter-id table drifts from sigmond's

psk-recorder, meteor-scatter and wspr-recorder test their own
_reporter_id.py against a copy of tests/data/reporter_id_vectors.json;
this keeps the copies byte-identical (tasks/spec-reporter-id-v372.md §5)."
```

- [ ] **Step 6: Mutation check**

Append a space to sigmond's own table (`printf ' ' >> tests/data/reporter_id_vectors.json`).  Run
`.venv/bin/pytest tests/test_reporter_id_drift.py -q --override-ini addopts=`; expect all three tests to FAIL.  Then
`git checkout -- tests/data/reporter_id_vectors.json`.

- [ ] **Step 7: Refresh the code graph**

Run: `graphify update /home/mjh/hamsci/repos` (from any directory; never `graphify update .`).

---

### Task 15: Check it on AC0G-B4 (manual; spec §7)

AC0G-B4 serves as the test bench, but Rob's Claude works on it too.  Nothing in this task runs until the bus carries
the announcement, and nothing runs until Michael has pushed the commits of Tasks 1-14 (Rob pushes, or approves,
Task 12's).  The station takes the change through the existing route: the v3.72 image, or in place with
`smd update --apply`, which pulls each repository and runs its `scripts/install.sh` (`lib/sigmond/update.py:188-196`).
This task invents no other mechanism.

Reach B4 only through `/home/mjh/hamsci/ops/bin/fleet-ssh b4 '<command>'`.  Never retype a reach.  If the
permission system refuses a command that needs root on B4, write the command into a plain script under
`/home/mjh/`, and hand Michael one line to run with the `!` prefix.

**Files:** none in any repository.  One bus message before, one after.

- [ ] **Step 1: Read the bus and announce**

Run the three bus-reading commands of Task 12, Step 1.  Read anything newer than this plan about AC0G-B4,
especially an A/B experiment in progress.  If one runs, stop and ask Michael.

Write `/tmp/bus-draft-b4-reporter-id.md` with the Write tool:

```markdown
# AC0G-B4: reporter-id fix (v3.72 #2) — deploy and checks, starting now

From mjh's Claude.  Plan: sigmond tasks/plan-reporter-id-v372.md, Task 15.

What changes on B4: `smd update --apply` pulls sigmond, psk-recorder,
meteor-scatter, wspr-recorder and sigmond-appliance, runs each install.sh,
and restarts their recorders.  The only change on disk: the empty stray
directories /var/lib/psk-recorder/AC0G\x3dB4/ and
/var/lib/meteor-scatter/AC0G\x3dB4/ go away.  No unit, file or directory
changes its name.  No CPU affinity, IRQ or cache change.

Then read-only checks: migrate candidates, the stale-instance sweep, the
watch commands with --instance AC0G/B4, smd tui.  I will post the result.
```

Post it with the commands of Task 12, Step 1 (draft path `/tmp/bus-draft-b4-reporter-id.md`).

- [ ] **Step 2: Record what B4 holds now (read-only)**

```bash
F=/home/mjh/hamsci/ops/bin/fleet-ssh
$F b4 'ls -la /etc/systemd/system/multi-user.target.wants/ | grep -E "(psk-recorder|meteor-scatter|wspr-recorder)@"'
$F b4 'sudo -n find /var/lib/psk-recorder /var/lib/meteor-scatter -maxdepth 2 -name "*x3d*"'
$F b4 'smd admin instance migrate'
```

Expected today: three links named `…@AC0G\x3dB4.service`; the two stray directories with their empty `ft8`, `ft4`,
`msk144`; and migrate listing the live `AC0G\x3dB4` units as candidates (the S6 defect).  Note the output in the
closing bus message.

- [ ] **Step 3: Deploy**

Michael pushes; then on B4, as the `hamsci` user (smd refuses to run under sudo):

```bash
$F b4 'smd update'
$F b4 'smd update --apply'
```

Expected: the plan lists a pull and an install for each changed repository; the apply output for psk-recorder and
meteor-scatter carries `[INFO]  removed empty stray directory /var/lib/<client>/AC0G\x3dB4`.  A `[WARN]  left …`
line means the directory held something: copy the listing into the bus message and leave the directory alone.
Restart the recorders as `smd update` advises once radiod has settled.

- [ ] **Step 4: Run the checks of spec §7**

```bash
$F b4 'smd admin instance migrate'
```

Expected: `no migration candidates found — all units / env files are reporter-ID-shaped already.`

```bash
$F b4 'sudo -n /opt/git/sigmond/meteor-scatter/venv/bin/python3 -c "from pathlib import Path; from meteor_scatter.configurator import _stale_instances; print(_stale_instances(chr(0), Path(\"/etc/meteor-scatter\")))"'
```

Expected: `[]` (`chr(0)` keeps no instance by id, so only a config or env file can save one).  Any name in the list
must be a real orphan, never `AC0G=B4`.

```bash
$F b4 'timeout 30 smd watch psk --instance AC0G/B4; echo rc=$?'
$F b4 'timeout 30 smd watch wspr --instance AC0G/B4; echo rc=$?'
$F b4 'timeout 30 smd watch meteor --instance AC0G/B4; echo rc=$?'
$F b4 'timeout 30 smd watch verifier --instance AC0G/B4; echo rc=$?'
```

Expected for each: no `no active service matching` line; a heading that names `@AC0G/B4`; `rc=124` (the watch ran
until `timeout` stopped it).  No `\x3d` anywhere in the output.

```bash
$F b4 'smd admin instance list'
$F b4 'smd admin instance show psk-recorder AC0G/B4'
$F b4 'smd admin instance canonical AC0G/B4; smd admin instance canonical --systemd AC0G/B4'
$F b4 'systemctl is-active "psk-recorder@AC0G\x3dB4.service" "meteor-scatter@AC0G\x3dB4.service" "wspr-recorder@AC0G\x3dB4.service"'
$F b4 'systemctl list-units --all --no-legend "*x5c*"'
$F b4 'sudo -n find /var/lib/psk-recorder /var/lib/meteor-scatter -maxdepth 2 -name "*x3d*"'
```

Expected: the list shows `AC0G/B4` for each client; `show` prints `unit:     psk-recorder@AC0G/B4` and a `journal:`
line; canonical prints `AC0G=B4` then `AC0G\x3dB4`; three lines of `active`; no `x5c` unit; and the `find` prints
nothing (or only directories a `[WARN]` line already reported).

- [ ] **Step 5: Check the TUI by eye**

Michael runs `smd tui` on B4 (interactive, so not through fleet-ssh).  Expected: the Configuration table shows
`AC0G/B4`; the Lifecycle rows read `psk-recorder@AC0G/B4`; the Activity and Logs instance dropdowns offer `AC0G/B4`
and no `(legacy)` entry for it.

- [ ] **Step 6: Report on the bus**

Write the result (pass or fail for each check of Steps 2-5, with any `[WARN]` listing) to
`/tmp/bus-draft-b4-reporter-id-result.md` and post it with the commands of Task 12, Step 1.  Then delete both drafts.

---

## Self-review (done while writing)

- **Spec coverage.**  §2-§3 helpers: Task 1.  §4 groups: unescape (S5, S6, S16, S43, S44, S45) Tasks 2 and 5; parse
  then escape (S37, S39, S41, S42, S46, S48) Task 5; path (S19) Task 2; display (S9, S14, S17, S20-S24, S26-S28, S30,
  S34, S50, S52-S54) Tasks 3-7; ssh (S54, S55) Task 5; private copies deleted (S13, S15, S18, S32, S37, S38, the regex
  at smd:17687) Tasks 2, 5, 7; `test_harmonize.py:929` Task 6.  §5 clients: Tasks 9-12; template lines Task 11; stray
  directories Task 11; `--radiod-id` Tasks 9-10; the meteor-scatter fixture Task 9.  §6 appliance: Tasks 8 and 13.
  §7 testing: every task's tests use `\x3d`, watch a failure first, carry hyphen cases, and end with mutations; the
  B4 checks form Task 15.  §8 rollout: Task 15 names `smd update --apply` or the image.  §9 stays out.
- **Placeholders.**  One on purpose: Task 11 Branch B's commit message asks for the file and line that Step 1 found,
  because only that reading can supply it.
- **Names.**  `systemd_escape`, `systemd_instance`, `unit_name`, `display_unit_name`, `parse_instance_arg`,
  `display_reporter_id` (sigmond); `_display_rid`, `_display_unit`, `_parse_instance_arg`, `_systemd_escape`,
  `_unescape_instance`, `_watch_instance`, `_normalize_instance_client`, `_restart_hint`, `cmd_instance_canonical`
  (smd); `unescape_systemd_instance`, `to_storage`, `systemd_instance`, `display`, `_normalize_instance_args`
  (clients).  Each later task uses them as Task 1, 2, 5, 8 and 9 define them.

