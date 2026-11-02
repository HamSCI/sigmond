"""build_plan for every profile WITH clients is unchanged by the radiod-only work.

The radiod-only profile changed lib/sigmond/bringup.py (clientless plans get
their own Stage 1 and Stage 4).  A full station's plan must not move by one
step.  tests/data/bringup_plan_snapshot_9d77c22.json holds, for every
`[profile.*]` in etc/catalog.toml that has clients, across local/remote radiod,
sdr_absent True/False and with_optional True/False, each step's
[label, argv, check].

How the snapshot was made -- from the BASE, not from this code: the tree of
9d77c22 (the origin/main the radiod-only work was rebased onto; first made
at d1a11cc, where that work began) was exported to a temp dir,
`git archive 9d77c22 lib etc | tar -x -C $TMP`, and a short script put
$TMP/lib first on sys.path, read the profiles straight from
$TMP/etc/catalog.toml (no /etc/sigmond overlay) and wrote
`{"<profile>|local=..|sdr_absent=..|with_optional=..": [[label, argv,
check], ...]}` with json.dumps(indent=1, ensure_ascii=False,
sort_keys=True), stdin from /dev/null (so `configure radiod` takes
--non-interactive, as under pytest).  `snapshot_of()` below is that script's
loop; regenerate only with the base's code, never with this tree's.
"""
import itertools
import json
import sys
import tomllib
from pathlib import Path

import pytest

from sigmond.bringup import build_plan
from sigmond.catalog import Profile

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO / "tests" / "data" / "bringup_plan_snapshot_9d77c22.json"


def profiles_with_clients(catalog: Path) -> list:
    blocks = tomllib.loads(catalog.read_text()).get("profile") or {}
    out = []
    for name in sorted(blocks):
        b = blocks[name]
        prof = Profile(name=name, description=b.get("description", ""),
                       clients=tuple(b.get("clients", ())),
                       local_radiod_infra=tuple(b.get("local_radiod_infra", ())),
                       optional=tuple(b.get("optional", ())))
        if prof.clients:
            out.append(prof)
    return out


def snapshot_of(profiles) -> dict:
    out = {}
    for prof in profiles:
        for local, absent, opt in itertools.product((True, False), repeat=3):
            plan = build_plan(prof, local_radiod=local, sdr_absent=absent,
                              with_optional=opt)
            key = f"{prof.name}|local={local}|sdr_absent={absent}|with_optional={opt}"
            out[key] = [[s.label, list(s.argv), s.check] for s in plan.steps]
    return out


@pytest.fixture(autouse=True)
def _no_tty(monkeypatch):
    # build_plan reads sys.stdin.isatty() for `configure radiod`.
    class _In:
        def isatty(self):
            return False
    monkeypatch.setattr(sys, "stdin", _In())


def test_every_profile_with_clients_plans_exactly_as_at_the_base():
    want = json.loads(SNAPSHOT.read_text())
    got = json.loads(json.dumps(snapshot_of(
        profiles_with_clients(REPO / "etc" / "catalog.toml"))))
    assert sorted(got) == sorted(want)
    for key in sorted(want):
        assert got[key] == want[key], key


def test_the_snapshot_covers_every_profile_with_clients():
    want = json.loads(SNAPSHOT.read_text())
    names = {k.split("|")[0] for k in want}
    assert names == {p.name for p in profiles_with_clients(REPO / "etc" / "catalog.toml")}
    assert len(want) == 8 * len(names)
    assert "radiod-only" not in names
