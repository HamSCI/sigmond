"""`smd admin completion bash` must keep up with the CLI.

⛔ WHY THIS IS A TEST AND NOT A CHECKLIST ITEM

The completion script is a hand-written string constant (`_BASH_COMPLETION`
in bin/smd).  Nothing links it to argparse, so every new verb is a silent
chance for it to fall behind, and a stale completion is worse than none: it
tells an operator a subcommand does not exist.  Found on 2026-10-24 while
adding `smd watch usb` — `watch gpsdo` had been a verb for some time and the
completion offered it no flags at all.

rob asked that the completions be refreshed with each build.  A build-time
step is a reminder; this is the thing the reminder was trying to buy.  It
compares the word lists in the completion script against what argparse
actually publishes, so the two cannot diverge without a red test.

The check is deliberately ONE-WAY on flags (every advertised flag must be
real) and TWO-WAY on verbs (the sets must match exactly).  Offering a flag
that does not exist is a lie; not yet offering every flag of every verb is
an incompleteness the author may be mid-way through, and failing on it would
make the test a nuisance rather than a guard.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run(*args):
    env = dict(os.environ, PYTHONPATH=str(REPO / "lib"),
               SIGMOND_NO_VENV_REEXEC="1")
    return subprocess.run([sys.executable, str(REPO / "bin" / "smd"), *args],
                          capture_output=True, text=True, env=env,
                          timeout=60).stdout


def _completion_script():
    return _run("admin", "completion", "bash")


def _help_verbs(*args):
    """The `{a,b,c}` subcommand group argparse prints for `smd <args> --help`."""
    out = _run(*args, "--help")
    m = re.search(r"\{([a-z0-9,\-]+)\}", out)
    assert m, f"no {{verb}} group in `smd {' '.join(args)} --help`:\n{out[:400]}"
    return set(m.group(1).split(","))


def _help_flags(*args):
    out = _run(*args, "--help")
    return set(re.findall(r"(--[a-z0-9][a-z0-9\-]*)", out))


def _compgen_words(script, verb):
    """The `-W "…"` word list the script offers after `smd <verb>`.

    Matches the first `compgen -W` inside that verb's case arm.
    """
    m = re.search(r'^\s*' + re.escape(verb) + r'\)\s*\n'
                  r'\s*COMPREPLY=\(\$\(compgen -W "([^"]*)"',
                  script, flags=re.M)
    assert m, f"no compgen word list for `{verb}` in the completion script"
    return set(m.group(1).split())


def test_the_script_is_valid_bash():
    # A completion script with a syntax error breaks the user's shell
    # startup, not just completion.
    r = subprocess.run(["bash", "-n"], input=_completion_script(),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


def test_top_level_verbs_match_argparse():
    script = _completion_script()
    m = re.search(r'local top_cmds="([^"]*)"', script)
    assert m, "no top_cmds list in the completion script"
    offered = set(m.group(1).split())
    real = _help_verbs()
    # `help` is a courtesy word several arms offer; argparse does not list it.
    missing = real - offered - {"help"}
    bogus = offered - real - {"help"}
    assert not missing, f"completion is missing real verbs: {sorted(missing)}"
    assert not bogus, f"completion offers verbs that do not exist: {sorted(bogus)}"


def test_watch_subverbs_match_argparse():
    # The one that went stale: `watch` grows a subverb per client.
    offered = _compgen_words(_completion_script(), "watch")
    real = _help_verbs("watch")
    assert offered == real, (
        f"missing: {sorted(real - offered)}; "
        f"not a subverb: {sorted(offered - real)}")


def test_component_subverbs_match_argparse():
    # `help` is a courtesy word, offered and sometimes declared; compare
    # without it rather than let it decide the result either way.
    offered = _compgen_words(_completion_script(), "component") - {"help"}
    real = _help_verbs("component") - {"help"}
    assert offered == real, (
        f"missing: {sorted(real - offered)}; "
        f"not a subverb: {sorted(offered - real)}")


def _watch_flag_block(script):
    """The body of the `watch)` arm that dispatches on `${words[2]}`.

    ⚠ Scoping matters.  Matching per-subverb arms by indentation alone also
    catches `show)` from the `config)` block, and the test then reports that
    `smd watch show` rejects `--json` — a confident, wrong finding about a
    verb that does not exist.
    """
    outer = script.index('case "${words[1]}" in')
    start = script.index("\n                watch)\n", outer)
    # The arm ends at the next sibling arm, which is one `;;` plus a label
    # at the same depth.
    end = script.index("\n                install)", start)
    return script[start:end]


def test_every_watch_flag_offered_is_a_real_flag():
    """A flag the completion invents is a lie an operator will type."""
    script = _completion_script()
    arms = re.findall(r'^\s+([a-z0-9|\-]+)\)\s*\n'
                      r'\s*COMPREPLY=\(\$\(compgen -W "([^"]*)"',
                      _watch_flag_block(script), flags=re.M)
    assert arms, "no per-subverb watch completion arms found"
    checked = 0
    for names, words in arms:
        offered = {w for w in words.split() if w.startswith("--")}
        if not offered:
            continue
        for name in names.split("|"):
            real = _help_flags("watch", name)
            bogus = offered - real
            assert not bogus, (
                f"`smd watch {name}` does not accept {sorted(bogus)}")
            checked += 1
    assert checked, "no watch flag arms were actually checked"


def test_watch_usb_is_offered_with_its_flags():
    # The verb this test file was written alongside.  Named explicitly so a
    # regression says which one went missing rather than only that a set
    # comparison failed.
    script = _completion_script()
    assert "usb" in _compgen_words(script, "watch")
    m = re.search(r'^\s*usb\)\s*\n\s*COMPREPLY=\(\$\(compgen -W "([^"]*)"',
                  script, flags=re.M)
    assert m, "no flag completion for `smd watch usb`"
    assert {"--once", "--seconds"} <= set(m.group(1).split())
