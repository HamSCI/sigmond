"""Tests for the `smd doctor` glue in bin/smd that isn't already covered
by tests/test_doctor.py (which tests sigmond.doctor directly): the
ExecStart parsing in `_unit_exec_expected`, the `_manifest_usable`
branch wiring, and the widened venv-sibling probing
(`_editable_siblings` / `_sibling_probe` / `_venv_sibling_skew`) added
to close the "only ka9q-python is checked" gap.

Loads bin/smd directly via SourceFileLoader, following the pattern
established in tests/test_start_autoenable.py.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import tempfile
import types
import unittest
from pathlib import Path

from sigmond.doctor import MIN_COMPONENT_ROWS

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    # bin/smd re-execs into the production venv unless told not to; suppress
    # that so importing the script just defines its functions.
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_doctor_glue", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_doctor_glue", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def _fake_run(stdout_by_unit: dict):
    """Stand-in for `_run`/`smd._run`: returns an object with `.stdout`
    keyed off the unit name (the last element of the command list), so
    tests never touch a real systemd."""
    def run(cmd, **kwargs):
        return types.SimpleNamespace(stdout=stdout_by_unit.get(cmd[-1], ''))
    return run


class UnitExecExpectedTests(unittest.TestCase):
    """`_unit_exec_expected`'s ExecStart= parsing, in isolation from
    systemd via the injected `run` callable."""

    def setUp(self):
        self.tdir = Path(tempfile.mkdtemp())

    def _unit_file(self, name: str, text: str) -> Path:
        p = self.tdir / name
        p.write_text(text)
        return p

    def test_plain_exec_start(self):
        f = self._unit_file('a.service', 'ExecStart=/usr/local/sbin/radiod\n')
        run = _fake_run({'a.service': str(f)})
        self.assertEqual(
            smd._unit_exec_expected('a.service', run=run),
            '/usr/local/sbin/radiod')

    def test_exec_start_with_arguments(self):
        f = self._unit_file(
            'b.service',
            'ExecStart=/usr/local/sbin/radiod --config /etc/radiod.conf --foo\n')
        run = _fake_run({'b.service': str(f)})
        self.assertEqual(
            smd._unit_exec_expected('b.service', run=run),
            '/usr/local/sbin/radiod')

    def test_prefix_modifier_dash(self):
        f = self._unit_file('c.service', 'ExecStart=-/usr/bin/foo\n')
        run = _fake_run({'c.service': str(f)})
        self.assertEqual(smd._unit_exec_expected('c.service', run=run), '/usr/bin/foo')

    def test_prefix_modifier_at(self):
        f = self._unit_file('d.service', 'ExecStart=@/usr/bin/foo\n')
        run = _fake_run({'d.service': str(f)})
        self.assertEqual(smd._unit_exec_expected('d.service', run=run), '/usr/bin/foo')

    def test_prefix_modifier_plus(self):
        f = self._unit_file('e.service', 'ExecStart=+/usr/bin/foo\n')
        run = _fake_run({'e.service': str(f)})
        self.assertEqual(smd._unit_exec_expected('e.service', run=run), '/usr/bin/foo')

    def test_prefix_modifier_bang(self):
        f = self._unit_file('f.service', 'ExecStart=!/usr/bin/foo\n')
        run = _fake_run({'f.service': str(f)})
        self.assertEqual(smd._unit_exec_expected('f.service', run=run), '/usr/bin/foo')

    def test_no_exec_start_line_at_all(self):
        f = self._unit_file('g.service', '[Unit]\nDescription=x\n[Service]\nType=notify\n')
        run = _fake_run({'g.service': str(f)})
        self.assertIsNone(smd._unit_exec_expected('g.service', run=run))

    def test_multiple_exec_start_lines_last_one_wins(self):
        # Documented as incidental (a side effect of overwriting on every
        # match), not deliberately chosen multi-command semantics — see
        # the comment above the parsing loop. Still must behave
        # predictably: last line wins.
        f = self._unit_file(
            'h.service', 'ExecStart=/bin/first\nExecStart=/bin/second\n')
        run = _fake_run({'h.service': str(f)})
        self.assertEqual(smd._unit_exec_expected('h.service', run=run), '/bin/second')

    def test_unreadable_unit_file(self):
        # FragmentPath pointing at a directory makes Path.read_text()
        # raise IsADirectoryError (an OSError subclass) unconditionally
        # — reliable regardless of the test runner's uid/permissions.
        run = _fake_run({'i.service': str(self.tdir)})
        self.assertIsNone(smd._unit_exec_expected('i.service', run=run))

    def test_empty_fragment_path(self):
        # systemctl returning nothing for FragmentPath (unit unknown to
        # systemd, or the query itself came back empty).
        run = _fake_run({})
        self.assertIsNone(smd._unit_exec_expected('missing.service', run=run))


class ManifestUsableTests(unittest.TestCase):
    """`_manifest_usable` — the extracted branch condition cmd_doctor
    uses to decide "unassessed" vs. "run manifest_drift()"."""

    def test_none_text_is_not_usable(self):
        self.assertFalse(smd._manifest_usable(None))

    def test_missing_header_is_not_usable(self):
        self.assertFalse(smd._manifest_usable('not a manifest at all\n'))

    def test_below_row_floor_is_not_usable(self):
        text = 'components (live):\n' + ''.join(
            f'    comp{i} abc{i:04d}\n' for i in range(MIN_COMPONENT_ROWS - 1))
        self.assertFalse(smd._manifest_usable(text))

    def test_at_row_floor_is_usable(self):
        text = 'components (live):\n' + ''.join(
            f'    comp{i} abc{i:04d}\n' for i in range(MIN_COMPONENT_ROWS))
        self.assertTrue(smd._manifest_usable(text))


class ManifestUsableWiringTests(unittest.TestCase):
    """The manifest_usable branch as actually wired into cmd_doctor:
    an unusable manifest must produce a distinct 'unassessed' finding
    (and a non-zero exit), a usable one must not."""

    def setUp(self):
        self.tdir = Path(tempfile.mkdtemp())
        self.base = self.tdir / 'base'
        self.base.mkdir()
        self._orig_manifest_path = smd.MANIFEST_PATH
        self._orig_topology_path = smd.TOPOLOGY_PATH
        # Point at a nonexistent topology file so _load_topology takes
        # its "no topology file" default path deterministically, rather
        # than picking up whatever the host running the tests has.
        smd.TOPOLOGY_PATH = self.tdir / 'no-such-topology.toml'

    def tearDown(self):
        smd.MANIFEST_PATH = self._orig_manifest_path
        smd.TOPOLOGY_PATH = self._orig_topology_path

    def _run_doctor(self):
        args = types.SimpleNamespace(base=str(self.base), fix=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = smd.cmd_doctor(args)
        return rc, buf.getvalue()

    def test_missing_manifest_reports_unassessed(self):
        smd.MANIFEST_PATH = self.tdir / 'no-such-manifest.txt'
        rc, out = self._run_doctor()
        self.assertEqual(rc, 1)
        self.assertIn('unassessed', out)
        self.assertIn('NOT the same as', out)

    def test_usable_manifest_does_not_report_unassessed(self):
        manifest = self.tdir / 'manifest.txt'
        manifest.write_text('components (live):\n' + ''.join(
            f'    comp{i} abc{i:04d}\n' for i in range(MIN_COMPONENT_ROWS)))
        smd.MANIFEST_PATH = manifest
        rc, out = self._run_doctor()
        self.assertNotIn('unassessed', out)


class EditableSiblingsTests(unittest.TestCase):
    """`_editable_siblings` reads the declared [tool.uv.sources] editable
    path dependencies from a component's OWN pyproject.toml."""

    def setUp(self):
        self.tdir = Path(tempfile.mkdtemp())

    def test_reads_declared_editable_path_sources(self):
        comp = self.tdir / 'wspr-recorder'
        comp.mkdir()
        (self.tdir / 'ka9q-python').mkdir()
        (self.tdir / 'hs-uploader').mkdir()
        (comp / 'pyproject.toml').write_text(
            '[tool.uv.sources]\n'
            'ka9q-python = { path = "../ka9q-python", editable = true }\n'
            'hs-uploader = { path = "../hs-uploader", editable = true }\n')
        siblings = smd._editable_siblings(comp)
        self.assertEqual(set(siblings), {'ka9q-python', 'hs-uploader'})
        self.assertEqual(siblings['ka9q-python'], self.tdir / 'ka9q-python')

    def test_ignores_non_editable_and_pathless_sources(self):
        comp = self.tdir / 'consumer'
        comp.mkdir()
        (comp / 'pyproject.toml').write_text(
            '[tool.uv.sources]\n'
            'wheel-only = { index = "https://example" }\n'
            'not-editable = { path = "../not-editable", editable = false }\n')
        self.assertEqual(smd._editable_siblings(comp), {})

    def test_missing_pyproject_returns_empty(self):
        comp = self.tdir / 'no-pyproject'
        comp.mkdir()
        self.assertEqual(smd._editable_siblings(comp), {})

    def test_malformed_toml_returns_empty(self):
        comp = self.tdir / 'broken'
        comp.mkdir()
        (comp / 'pyproject.toml').write_text('this is not [ valid toml\n')
        self.assertEqual(smd._editable_siblings(comp), {})


class _FakeCompletedProcess:
    def __init__(self, returncode, stdout='', stderr=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeSubprocessModule:
    """Stand-in for the `subprocess` module as seen by `_sibling_probe`'s
    closure: `responses` maps a package name (parsed out of the probe
    script it's handed) to the CompletedProcess it should get back."""

    def __init__(self, responses: dict):
        self.responses = responses

    def run(self, cmd, **kwargs):
        code = cmd[2]
        # The probe script always contains `name = 'pkg'` — see
        # _sibling_probe. Pull it back out rather than hardcoding.
        marker = "name = '"
        start = code.index(marker) + len(marker)
        pkg = code[start:code.index("'", start)]
        return self.responses.get(pkg, _FakeCompletedProcess(3))


class SiblingProbeTests(unittest.TestCase):
    """`_sibling_probe` must distinguish "package absent from this venv"
    (silently skipped, matching venv_skew's existing contract) from
    "package present per metadata but import failed" (recorded into
    `unassessable` instead of silently reading as clean) — closing the
    audit's gap 2."""

    def setUp(self):
        self.tdir = Path(tempfile.mkdtemp())
        self.venv = self.tdir / 'venv'
        (self.venv / 'bin').mkdir(parents=True)
        (self.venv / 'bin' / 'python').touch()
        self._orig_subprocess = smd.subprocess

    def tearDown(self):
        smd.subprocess = self._orig_subprocess

    def test_absent_package_is_silently_skipped(self):
        smd.subprocess = _FakeSubprocessModule(
            {'hamsci-dsp': _FakeCompletedProcess(3)})
        unassessable = []
        probe = smd._sibling_probe('hamsci-dsp', 'compA', unassessable)
        self.assertIsNone(probe(str(self.venv)))
        self.assertEqual(unassessable, [])

    def test_successful_probe_returns_location_and_version(self):
        smd.subprocess = _FakeSubprocessModule({
            'hamsci-dsp': _FakeCompletedProcess(
                0, stdout='/opt/git/sigmond/hamsci-dsp/src/hamsci_dsp\n0.3.0\n')})
        unassessable = []
        probe = smd._sibling_probe('hamsci-dsp', 'compA', unassessable)
        info = probe(str(self.venv))
        self.assertEqual(info, {
            'location': '/opt/git/sigmond/hamsci-dsp/src/hamsci_dsp',
            'version': '0.3.0'})
        self.assertEqual(unassessable, [])

    def test_import_failure_is_recorded_as_unassessable_not_skipped(self):
        # Distribution metadata resolves (exit 3 was NOT taken) but the
        # actual import raised — e.g. a stale .pth pointing at a moved
        # checkout. Must NOT be silently treated the same as "absent".
        smd.subprocess = _FakeSubprocessModule({
            'hamsci-dsp': _FakeCompletedProcess(
                4, stderr='ModuleNotFoundError: no module named hamsci_dsp')})
        unassessable = []
        probe = smd._sibling_probe('hamsci-dsp', 'compA', unassessable)
        self.assertIsNone(probe(str(self.venv)))
        self.assertEqual(len(unassessable), 1)
        self.assertEqual(unassessable[0]['component'], 'compA')
        self.assertEqual(unassessable[0]['package'], 'hamsci-dsp')
        self.assertIn('ModuleNotFoundError', unassessable[0]['detail'])

    def test_no_python_in_venv_is_silently_skipped(self):
        empty_venv = self.tdir / 'no-python-venv'
        empty_venv.mkdir()
        smd.subprocess = _FakeSubprocessModule({})  # never called
        unassessable = []
        probe = smd._sibling_probe('hamsci-dsp', 'compA', unassessable)
        self.assertIsNone(probe(str(empty_venv)))
        self.assertEqual(unassessable, [])


class VenvSiblingSkewTests(unittest.TestCase):
    """`_venv_sibling_skew` end-to-end: real `_editable_siblings` +
    real `venv_skew()` (unmodified), with only the subprocess boundary
    faked — proving the widened probing actually wires together."""

    def setUp(self):
        self.tdir = Path(tempfile.mkdtemp())
        self.comp = self.tdir / 'consumer'
        (self.comp / 'venv' / 'bin').mkdir(parents=True)
        (self.comp / 'venv' / 'bin' / 'python').touch()
        (self.tdir / 'hamsci-dsp').mkdir()
        (self.comp / 'pyproject.toml').write_text(
            '[tool.uv.sources]\n'
            'hamsci-dsp = { path = "../hamsci-dsp", editable = true }\n')
        self._orig_subprocess = smd.subprocess

    def tearDown(self):
        smd.subprocess = self._orig_subprocess

    def test_private_copy_is_reported_as_skew(self):
        # Resolves OUTSIDE the shared checkout -> skew.
        smd.subprocess = _FakeSubprocessModule({
            'hamsci-dsp': _FakeCompletedProcess(
                0, stdout='/some/private/venv/site-packages/hamsci_dsp\n0.1.0\n')})
        skew, unassessable = smd._venv_sibling_skew([self.comp])
        self.assertEqual(len(skew), 1)
        self.assertEqual(skew[0]['component'], 'consumer')
        self.assertEqual(skew[0]['package'], 'hamsci-dsp')
        self.assertEqual(unassessable, [])

    def test_shared_checkout_location_is_not_skew(self):
        shared_loc = str(self.tdir / 'hamsci-dsp' / 'src' / 'hamsci_dsp')
        smd.subprocess = _FakeSubprocessModule({
            'hamsci-dsp': _FakeCompletedProcess(0, stdout=f'{shared_loc}\n0.3.0\n')})
        skew, unassessable = smd._venv_sibling_skew([self.comp])
        self.assertEqual(skew, [])
        self.assertEqual(unassessable, [])

    def test_import_failure_surfaces_as_unassessable_not_skew(self):
        smd.subprocess = _FakeSubprocessModule({
            'hamsci-dsp': _FakeCompletedProcess(4, stderr='boom')})
        skew, unassessable = smd._venv_sibling_skew([self.comp])
        self.assertEqual(skew, [])
        self.assertEqual(len(unassessable), 1)
        self.assertEqual(unassessable[0]['component'], 'consumer')


if __name__ == '__main__':
    unittest.main()
