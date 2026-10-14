"""Fakes for the `smd check` tests (tasks/spec-smd-check-v372.md §8).

make_env() builds an Env in which every client counts as installed.  A test
passes keyword arguments to change any field.  Task 2 adds services, files
and the journal.
"""
from sigmond.commands import check

NOW = 1_760_443_200.0          # 2025-10-14 12:00:00 UTC


def make_env(**kw):
    base = dict(now=NOW, installed=lambda name: True)
    base.update(kw)
    return check.Env(**base)
