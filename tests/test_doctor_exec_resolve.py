"""`smd doctor`'s exec check must see something when it isn't root.

`/proc/<pid>/exe` is readable only by a process that could ptrace the
target, so a `smd doctor` run as the `sigmond` operator cannot read the
link for a service running as `timestd` or `wsprrec`. Every such service
collapsed to `status=unknown`:

    exec-mismatch: timestd-fusion.service: running=(unreadable)
                   expected=/opt/git/sigmond/hf-timestd/venv/bin/python
                   status=unknown

which is the whole check going blind for exactly the services it exists
to watch — observed across all 13 timestd units on DASI002.

Running the check under sudo is NOT the answer: smd refuses to run under
sudo at all ("it elevates itself when a verb needs root"), and wrapping
it that way produced an empty report.

The unprivileged fallback is `/proc/<pid>/cmdline`, which IS world
readable. Its argv[0] is what systemd exec'd, so for these services it
is the same absolute path — but argv[0] is process-CONTROLLED, so it is
evidence, not proof, and every finding derived from it says so.
"""

import importlib.machinery
import importlib.util
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

EXE = '/opt/git/sigmond/hf-timestd/venv/bin/python'


def _denied(pid):
    raise PermissionError(13, 'Permission denied')


def _gone(pid):
    raise FileNotFoundError(2, 'No such file or directory')


class ReadableExeTest(unittest.TestCase):
    """The authoritative path is still preferred whenever it works."""

    def test_a_readable_exe_is_returned_as_is(self):
        resolve, unverified = smd._make_exe_resolver(
            readlink=lambda pid: EXE, read_cmdline=lambda pid: ['/nope'])
        self.assertEqual(resolve(101), EXE)
        self.assertEqual(unverified, set())

    def test_the_deleted_suffix_survives(self):
        """exec_mismatch keys on it to report 'deleted' rather than
        'mismatch' — stripping it would erase that distinction."""
        resolve, _ = smd._make_exe_resolver(
            readlink=lambda pid: EXE + ' (deleted)',
            read_cmdline=lambda pid: [EXE])
        self.assertTrue(resolve(101).endswith(' (deleted)'))


class PermissionDeniedFallbackTest(unittest.TestCase):

    def test_argv0_answers_when_the_exe_link_is_denied(self):
        resolve, unverified = smd._make_exe_resolver(
            readlink=_denied, read_cmdline=lambda pid: [EXE, '-m', 'fusion'])
        self.assertEqual(resolve(101), EXE)
        self.assertIn('101', unverified)

    def test_a_relative_argv0_is_refused_rather_than_guessed(self):
        """A bare 'python' says nothing about WHICH python ran, and
        resolving it against our cwd would invent an answer."""
        resolve, unverified = smd._make_exe_resolver(
            readlink=_denied, read_cmdline=lambda pid: ['python3', '-m', 'x'])
        self.assertIsNone(resolve(101))
        self.assertEqual(unverified, set())

    def test_an_empty_cmdline_is_refused(self):
        resolve, unverified = smd._make_exe_resolver(
            readlink=_denied, read_cmdline=lambda pid: [])
        self.assertIsNone(resolve(101))
        self.assertEqual(unverified, set())

    def test_an_unreadable_cmdline_is_refused_not_raised(self):
        """A kernel thread or a process that exits mid-check must not
        crash the pass — doctor has to survive the permission problems
        it exists to report."""
        def boom(pid):
            raise OSError(5, 'I/O error')
        resolve, _ = smd._make_exe_resolver(readlink=_denied, read_cmdline=boom)
        self.assertIsNone(resolve(101))


class ProcessGoneTest(unittest.TestCase):

    def test_a_vanished_process_still_raises(self):
        """Only PERMISSION is worth falling back on. A process that
        exited has no argv[0] to read either, and exec_mismatch's
        'unknown' is the correct answer."""
        resolve, unverified = smd._make_exe_resolver(
            readlink=_gone, read_cmdline=lambda pid: [EXE])
        with self.assertRaises(OSError):
            resolve(101)
        self.assertEqual(unverified, set())


class ProvenanceIsDisclosedTest(unittest.TestCase):
    """A weaker source must never read like the authoritative one."""

    def test_a_fallback_finding_is_marked_unverified(self):
        text = smd._exec_finding_text(
            {'name': 'timestd-fusion.service', 'status': 'mismatch',
             'expected': EXE, 'running': '/usr/bin/python3'},
            pid='101', unverified={'101'})
        self.assertIn('unverified', text.lower())
        self.assertIn('argv[0]', text)

    def test_an_authoritative_finding_carries_no_caveat(self):
        text = smd._exec_finding_text(
            {'name': 'timestd-fusion.service', 'status': 'mismatch',
             'expected': EXE, 'running': '/usr/bin/python3'},
            pid='101', unverified=set())
        self.assertNotIn('unverified', text.lower())
        self.assertIn('status=mismatch', text)

    def test_an_unreadable_running_path_still_reads_unreadable(self):
        text = smd._exec_finding_text(
            {'name': 'x.service', 'status': 'unknown',
             'expected': EXE, 'running': None},
            pid='101', unverified=set())
        self.assertIn('(unreadable)', text)


class CmdlineParsingTest(unittest.TestCase):

    def test_nul_separated_argv_is_split(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'cmdline'
            p.write_bytes(EXE.encode() + b'\x00-m\x00fusion\x00')
            self.assertEqual(smd._read_cmdline_at(str(p))[0], EXE)

    def test_a_trailing_nul_does_not_produce_an_empty_argv0(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'cmdline'
            p.write_bytes(EXE.encode() + b'\x00')
            argv = smd._read_cmdline_at(str(p))
            self.assertEqual(argv, [EXE])


if __name__ == '__main__':
    unittest.main()
