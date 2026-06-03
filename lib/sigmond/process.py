"""Subprocess and root-check helpers."""

import os
import shutil
import subprocess
import sys


def run(cmd: list, *, cwd=None, capture: bool = True, sudo: bool = False) -> subprocess.CompletedProcess:
    """Run a command, optionally prefixing with sudo when not already root."""
    if sudo and os.geteuid() != 0:
        cmd = ['sudo'] + cmd
    return subprocess.run(cmd, cwd=cwd, capture_output=capture, text=True)


def need_root(cmd_name: str) -> bool:
    """Auto-elevate to root via sudo when invoked as a normal user.

    Mirrors ``bin/smd:_need_root``: rather than telling the operator to
    re-type the command under sudo, re-exec ourselves under sudo with the
    same argv — the operator never has to prefix ``sudo`` themselves.

    Returns True only if elevation isn't possible (no sudo on PATH);
    callers should still check the return value and exit on True.
    """
    if os.geteuid() == 0:
        return False
    sudo = shutil.which('sudo')
    if sudo:
        # Replaces the current process; sudo prompts for a password if
        # needed, then runs the same script with the same args as root.
        os.execvp(sudo, [sudo, '--', sys.argv[0], *sys.argv[1:]])
        # execvp doesn't return on success; if it does, fall through.
    print(f'smd {cmd_name}: must run as root (sudo not found on PATH)',
          file=sys.stderr)
    return True


# Back-compat aliases.
_run       = run
_need_root = need_root
