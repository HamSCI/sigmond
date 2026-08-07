"""Who git may run as inside a sigmond component checkout.

Running git as root in `/opt/git/sigmond/<name>` leaves root-owned .git
internals — objects, refs, FETCH_HEAD, config, HEAD, index — and every later
non-root operation trips over them.  The failure is silent and cumulative:
nothing errors when the damage is done, and it only surfaces when a non-root
user (or the `sigmond` system user, which has no shell or SSH key) next
touches the repo.  Observed on the v3.19 B4 install: 12 of 20 checkouts
affected.  See sigmond#43 and #44.

The harm is specifically git running as root in a checkout owned by *someone
else*.  A consistently root-owned tree is fine — and the clone path depends on
it, since install clones as root and only chowns at the end.  `resolve()`
returns one of DROP / ROOT_OK / REFUSE accordingly; a skipped update is
recoverable, a half-owned .git is not, so ambiguity resolves to REFUSE.

Deliberately stdlib-only, and imported by both `bin/smd` and
`sigmond.installer` — this policy has to be identical everywhere git is
invoked, and it previously was not.
"""
from __future__ import annotations

import os
import pwd
import subprocess
from pathlib import Path
from typing import Optional, Sequence

REFUSED_RC = 77          # EX_NOPERM — distinct from git's own exit codes

# Subcommands that only read.  Anything not listed here is treated as
# mutating, because the cost of being wrong in that direction is a skipped
# operation, while being wrong the other way corrupts the checkout.  Keeping
# reads runnable as root matters: inspection (`smd list`, status enrichment)
# must keep working on precisely the hosts whose checkouts are already
# root-owned, which is where a blanket refusal would be most disruptive.
_READ_ONLY = frozenset({
    'rev-parse', 'ls-remote', 'for-each-ref', 'cat-file',
    'rev-list', 'describe', 'show-ref', 'log', 'show', 'diff',
})


def is_read_only(git_args: Sequence[str]) -> bool:
    """True when `git_args` cannot write to the repository.

    `status` is NOT read-only — it refreshes the index stat cache and so
    writes .git/index.  `symbolic-ref` reads only in its one-argument form;
    given a value (or -d/-m) it rewrites a ref.  `remote` and `config` are
    split by subcommand/flag rather than by name.
    """
    if not git_args:
        return False
    sub = git_args[0]
    if sub in _READ_ONLY:
        return True
    if sub == 'symbolic-ref':
        rest = [a for a in git_args[1:] if not a.startswith('-')]
        flags = [a for a in git_args[1:] if a.startswith('-')]
        if any(f in ('-d', '--delete', '-m') for f in flags):
            return False
        return len(rest) <= 1
    if sub == 'remote':
        return len(git_args) > 1 and git_args[1] in ('get-url', 'show', 'v')
    if sub == 'config':
        return any(a.startswith('--get') or a in ('--list', '-l')
                   for a in git_args[1:])
    return False


DROP = 'drop'            # run as an unprivileged owner
ROOT_OK = 'root-ok'      # running as root introduces no inconsistency
REFUSE = 'refuse'        # no safe way to proceed — skip the operation


def resolve(repo_dir: Path):
    """Decide how git may run against `repo_dir`.

    Returns (DROP, user) | (ROOT_OK, None) | (REFUSE, None).

    The harm is not "git ran as root" per se — it is git running as root in a
    checkout owned by *someone else*, which sprays root-owned files into a
    tree the real owner must keep writing to.  So:

      * $SUDO_USER, when set AND when it owns the checkout, is the best
        identity available (a human with a key registered at the remote).
        When it does not own the checkout it is ignored: writing as an
        identity that cannot write the tree fails outright, which is worse
        than dropping to the owner.
      * A consistently root-owned tree is ROOT_OK.  Running as root there adds
        no inconsistency, and the clone path *requires* it: install clones as
        root and the tree stays root-owned until `_apply_canonical_perms()`
        chowns it at the very end.  Refusing here would break fresh installs.
      * A tree owned by a non-root user means we must drop to that user.
      * Split worktree/.git ownership (sigmond#44) is ambiguous — running as
        the worktree owner would write into someone else's .git — so REFUSE
        rather than guess.
      * Anything unresolvable (stat error, uid with no passwd entry) REFUSEs,
        because a skipped update is recoverable and a corrupted .git is not.
    """
    target = os.environ.get('SUDO_USER') or ''
    if target and target != 'root':
        # ...but only if that identity can actually write the checkout.
        # $SUDO_USER is whoever typed the command, which is not necessarily
        # who owns the tree: the golden-image build runs `smd install` as
        # `build`, smd re-execs itself under sudo (so SUDO_USER=build), and
        # the component checkouts are owned by `sigmond`.  Dropping to the
        # invoker there made every fetch fail with
        # ".git/FETCH_HEAD: Permission denied" and took the whole image build
        # down with it (2026-08-07, all 8 components).  Ownership is the
        # authority; the invoker is only a hint, so fall through when it
        # disagrees rather than write as someone who cannot.
        try:
            if os.stat(repo_dir).st_uid == pwd.getpwnam(target).pw_uid:
                return DROP, target
        except (OSError, KeyError):
            pass
    try:
        work_uid = os.stat(repo_dir).st_uid
        git_path = Path(repo_dir) / '.git'
        # A .git *file* (linked worktree / submodule) carries no separate
        # ownership worth comparing — fall back to the worktree's own uid.
        git_uid = os.stat(git_path).st_uid if git_path.is_dir() else work_uid
    except OSError:
        return REFUSE, None
    if work_uid != git_uid:
        return REFUSE, None
    if work_uid == 0:
        return ROOT_OK, None
    try:
        name = pwd.getpwuid(work_uid).pw_name
    except KeyError:
        return REFUSE, None
    return (ROOT_OK, None) if name == 'root' else (DROP, name)


def target_user(repo_dir: Path) -> Optional[str]:
    """The unprivileged user to drop to, or None when there is none.

    None covers both "root is fine here" and "refuse" — callers that need to
    tell those apart must use `resolve()`.
    """
    action, user = resolve(repo_dir)
    return user if action == DROP else None


def refused(repo_dir: Path, what: str) -> subprocess.CompletedProcess:
    """Stand-in result for a git command declined rather than run as root."""
    return subprocess.CompletedProcess(
        args=['git', '-C', str(repo_dir), what],
        returncode=REFUSED_RC,
        stdout='',
        stderr=(f'refusing to run `git {what}` as root in {repo_dir}: no '
                f'unprivileged owner could be resolved (root-owned, or split '
                f'worktree/.git ownership).  Skipped — running it as root '
                f'would leave root-owned .git internals behind.  Run '
                f'`smd admin diag` to see the ownership, fix it, then '
                f'retry.\n'))


def run_git(repo_dir: Path, *git_args: str,
            capture: bool = True) -> subprocess.CompletedProcess:
    """Run `git -C repo_dir <args>`, never as root for a mutating command.

    Reads run as-is.  Mutating commands follow `resolve()`: drop to the
    checkout's owner, proceed as root when the tree is consistently
    root-owned, or decline.  safe.directory is set per call so this works
    regardless of who ends up running it.
    """
    cmd = ['git', '-c', f'safe.directory={repo_dir}',
           '-C', str(repo_dir), *git_args]
    if os.geteuid() == 0 and not is_read_only(git_args):
        action, user = resolve(repo_dir)
        if action == REFUSE:
            return refused(repo_dir, ' '.join(git_args[:2]))
        if action == DROP:
            cmd = ['sudo', '-u', user, '-H', '--'] + cmd
    kw = {'capture_output': True, 'text': True} if capture else {}
    return subprocess.run(cmd, **kw)
