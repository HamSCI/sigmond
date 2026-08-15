"""`smd update` — the DASI002 sequence, encoded so it can be re-run safely.

Updating DASI002 on 2026-08-15 took eight manual steps and hit six traps,
each found only when a command failed.  This plans the same work from
observed state, so re-running is a no-op and every trap is a check
rather than a surprise:

* repos are owned by their SERVICE users and the login user has no sudo,
  so git must run as the owner (2996 + 938 root-owned paths had to be
  repaired first);
* a dirty tree may be a REAL uncommitted fix — DASI002's was — so the
  plan REFUSES rather than discarding;
* `deploy.sh` does not re-resolve `[tool.uv.sources]`, so a venv holding
  a private copy of a sibling stays stale through any number of
  pull+deploy cycles.  `install.sh` (uv sync) is the repair;
* `config_paths.h` is not regenerated when HEAD moves, so a rebuilt
  radiod silently embeds a STALE commit hash;
* radiod's plan set must be covered by wisdom or it silently falls back
  to FFTW_ESTIMATE — `fft.log` is written only on a miss;
* services restart radiod-first, then the recorders.

The planner is pure: it takes observed state and returns ordered
actions, so the ordering and the refusals are testable without a host.
"""
import pytest

from sigmond.update import plan_update, Action, Refusal


CLEAN = {
    'repos': {},                 # name -> {behind, dirty, owner}
    'venv_skew': [],             # component names whose sibling is a copy
    'radiod': {'running': 'cd44bbdd', 'pin': 'cd44bbdd'},
    'wisdom_misses': [],
    'services': ['radiod@X', 'timestd-core-recorder'],
}


def _state(**over):
    s = {k: (v.copy() if hasattr(v, 'copy') else v) for k, v in CLEAN.items()}
    s.update(over)
    return s


def test_a_current_host_needs_nothing():
    """Idempotence is the whole point: re-running must be a no-op."""
    assert plan_update(CLEAN) == []


def test_a_repo_behind_upstream_is_pulled_as_its_owner():
    plan = plan_update(_state(repos={'hf-timestd': {'behind': 3, 'dirty': [],
                                                    'owner': 'timestd'}}))

    assert [a.kind for a in plan] == ['pull', 'install', 'restart']
    assert plan[0].target == 'hf-timestd'
    assert plan[0].run_as == 'timestd'


def test_a_dirty_tree_refuses_rather_than_discarding():
    """DASI002's local change was a real fix (StartLimit keys moved out of
    [Service], where systemd ignores them).  Discarding it would have
    destroyed work."""
    plan = plan_update(_state(repos={'hf-timestd': {
        'behind': 3, 'dirty': ['deploy/systemd/timestd-metrology@.service'],
        'owner': 'timestd'}}))

    assert isinstance(plan[0], Refusal)
    assert 'timestd-metrology' in plan[0].reason
    assert 'diff' in plan[0].reason.lower()


def test_a_dirty_tree_does_not_block_other_components():
    """One held component must not stall the whole fleet update."""
    plan = plan_update(_state(repos={
        'hf-timestd': {'behind': 1, 'dirty': ['x'], 'owner': 'timestd'},
        'psk-recorder': {'behind': 1, 'dirty': [], 'owner': 'sigmond'}}))

    assert any(isinstance(a, Refusal) for a in plan)
    assert any(a.kind == 'pull' and a.target == 'psk-recorder'
               for a in plan if isinstance(a, Action))


def test_venv_skew_is_repaired_with_install_not_deploy():
    """deploy.sh runs `pip install -e .` for the consumer ALONE and will
    never re-resolve the editable sibling."""
    plan = plan_update(_state(venv_skew=['hf-timestd']))

    install = [a for a in plan if a.kind == 'install']
    assert install and install[0].target == 'hf-timestd'
    assert 'install.sh' in install[0].detail
    # deploy.sh may be NAMED, but only as the thing not to use.
    assert 'NOT deploy.sh' in install[0].detail


def test_radiod_is_rebuilt_when_the_running_binary_is_not_the_pin():
    plan = plan_update(_state(radiod={'running': '14d780af',
                                      'pin': 'cd44bbdd'}))

    rebuild = [a for a in plan if a.kind == 'rebuild-radiod']
    assert rebuild
    # config_paths.h is NOT regenerated when HEAD moves; without removing
    # it the new binary reports the OLD commit.
    assert 'config_paths.h' in rebuild[0].detail


def test_radiod_at_the_pin_is_not_rebuilt():
    assert [a for a in plan_update(CLEAN) if a.kind == 'rebuild-radiod'] == []


def test_wisdom_is_regenerated_only_when_plans_are_missing():
    plan = plan_update(_state(wisdom_misses=['cif300', 'cif600']))

    w = [a for a in plan if a.kind == 'wisdom']
    assert w and 'cif300' in w[0].detail
    assert [a for a in plan_update(CLEAN) if a.kind == 'wisdom'] == []


def test_radiod_restarts_before_the_recorders():
    """A recorder started against a restarting radiod picks up a bad
    anchor; radiod must be stable first."""
    plan = plan_update(_state(radiod={'running': 'old', 'pin': 'new'},
                              repos={'hf-timestd': {'behind': 1, 'dirty': [],
                                                    'owner': 'timestd'}}))
    kinds = [a.kind for a in plan if isinstance(a, Action)]

    assert kinds.index('rebuild-radiod') < kinds.index('restart')


def test_the_whole_sequence_is_ordered_as_it_was_run_by_hand():
    plan = plan_update(_state(
        repos={'hf-timestd': {'behind': 2, 'dirty': [], 'owner': 'timestd'}},
        venv_skew=['hf-timestd'],
        radiod={'running': 'old', 'pin': 'new'},
        wisdom_misses=['cif300']))

    kinds = [a.kind for a in plan if isinstance(a, Action)]
    assert kinds == ['pull', 'install', 'rebuild-radiod', 'wisdom', 'restart']


def test_every_action_is_verifiable():
    """Each step carries how to CHECK it — today's failures were all
    'looked fine, wasn't done'."""
    plan = plan_update(_state(
        repos={'hf-timestd': {'behind': 1, 'dirty': [], 'owner': 'timestd'}},
        radiod={'running': 'old', 'pin': 'new'}))

    for a in plan:
        if isinstance(a, Action):
            assert a.verify, f'{a.kind} has no verification'


def test_a_host_running_a_superset_of_the_pin_is_not_rebuilt():
    """B4 runs our merge (7fca458a), which CONTAINS the pinned upstream
    release plus two fork commits.  Exact-equality would order a pointless
    rebuild that would also throw away the fork patches.

    `contains_pin` is supplied by the caller (a merge-base check against
    the local checkout), because the planner itself stays pure.
    """
    plan = plan_update(_state(radiod={'running': '7fca458a',
                                      'pin': 'cd44bbdd',
                                      'contains_pin': True}))

    assert [a for a in plan if a.kind == 'rebuild-radiod'] == []


def test_a_host_behind_the_pin_is_still_rebuilt():
    plan = plan_update(_state(radiod={'running': '14d780af',
                                      'pin': 'cd44bbdd',
                                      'contains_pin': False}))

    assert [a for a in plan if a.kind == 'rebuild-radiod']


def test_an_unknown_running_commit_is_refused_not_guessed():
    """If the running version cannot be read, saying "rebuild" is a guess.
    B4 reported `running None` because radiod prints its banner to
    stderr — a parse failure that masqueraded as a real finding."""
    plan = plan_update(_state(radiod={'running': None, 'pin': 'cd44bbdd'}))

    assert any(isinstance(a, Refusal) and a.target == 'ka9q-radio'
               for a in plan)
    assert [a for a in plan if getattr(a, 'kind', '') == 'rebuild-radiod'] == []
