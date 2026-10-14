"""Fakes for the `smd check` tests (tasks/spec-smd-check-v372.md §8).

make_env() builds an Env in which every client counts as installed, every
service has run for an hour, files come from the real Reader (so the tests
build them in a temporary directory) and the journal is empty.  A test
passes keyword arguments to change any field.
"""
from sigmond.commands import check

NOW = 1_760_443_200.0          # 2025-10-14 12:00:00 UTC
HOUR = 3600.0


def svc(state='active', age_s=HOUR, enabled=True):
    return check.Service(state, age_s, enabled)


def make_env(**kw):
    base = dict(now=NOW, installed=lambda name: True, service=lambda pattern: svc(),
                reader=check.Reader(euid=lambda: 0), journal=lambda units, minutes: [])
    base.update(kw)
    return check.Env(**base)
