"""sigmond.hamsci_sink, the compatibility import (plan-sink-control D14, §10.4 item 1).

Since v3.70 the sink writer lives in hs-uploader as hs_uploader.sink.  Clients
still import sigmond.hamsci_sink.  It hands them hs-uploader's module when their
venv can import it, and otherwise a bundled copy that sigmond ships, because
codar-sounder, hf-tec and superdarn-sounder carry sigmond but no hs-uploader.
These tests pin that choice, the names importers use today, and the bundled
copy's sameness with hs-uploader's file.

Each choice runs in a child interpreter: this process's sys.modules already
holds whichever copy its first import picked.
"""

import ast
import difflib
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_LIB = _REPO / "lib"
_BUNDLED = _LIB / "sigmond" / "hamsci_sink" / "_bundled.py"
_HS_SRC = _REPO.parent / "hs-uploader" / "src"
_UPSTREAM = _HS_SRC / "hs_uploader" / "sink" / "writer.py"

_BEGIN = b"# ---- BEGIN sigmond bundled-copy header"
_END = b"# ---- END sigmond bundled-copy header"
_REFRESH = (
    "{ sed '/^# ---- END sigmond bundled-copy header/q' "
    "lib/sigmond/hamsci_sink/_bundled.py; "
    "cat ../hs-uploader/src/hs_uploader/sink/writer.py; } "
    "> lib/sigmond/hamsci_sink/_bundled.py.new && "
    "mv lib/sigmond/hamsci_sink/_bundled.py.new lib/sigmond/hamsci_sink/_bundled.py"
)


def _split_header(data: bytes) -> tuple:
    """Split the bundled copy into (its header lines, the bytes below them)."""
    lines = data.splitlines(keepends=True)
    if not lines or not lines[0].startswith(_BEGIN):
        raise AssertionError(f"{_BUNDLED} must start with a line {_BEGIN.decode()!r}")
    for i, line in enumerate(lines):
        if line.startswith(_END):
            return lines[: i + 1], b"".join(lines[i + 1:])
    raise AssertionError(f"{_BUNDLED} has no line starting {_END.decode()!r}")


def _child(script: str, extra_path=()) -> dict:
    """Run `script` in a fresh interpreter with lib/ (then extra_path) first on
    sys.path, and return the JSON object it prints last."""
    paths = [str(_LIB), *map(str, extra_path)]
    prelude = f"import json, sys\nsys.path[:0] = {paths!r}\n"
    proc = subprocess.run(
        [sys.executable, "-I", "-c", prelude + textwrap.dedent(script)],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"child interpreter failed (rc={proc.returncode}):\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _fake_hs_uploader(root: str, sink_init: str = "", writer_py: str = "") -> None:
    """Write a stand-in hs_uploader package under `root`, with a sink package
    whose __init__ and writer hold the given source."""
    sink = Path(root) / "hs_uploader" / "sink"
    sink.mkdir(parents=True)
    (sink.parent / "__init__.py").write_text('__version__ = "0.1.0"\n')
    (sink / "__init__.py").write_text(sink_init)
    (sink / "writer.py").write_text(writer_py)


class BundledCopyTests(unittest.TestCase):
    """The bundled copy holds a marked header, then hs-uploader's file unchanged."""

    def test_header_sits_at_the_top_once_and_holds_only_comments(self):
        data = _BUNDLED.read_bytes()
        header, _ = _split_header(data)
        self.assertTrue(all(line.startswith(b"#") for line in header), header)
        lines = data.splitlines()
        self.assertEqual(sum(line.startswith(_BEGIN) for line in lines), 1)
        self.assertEqual(sum(line.startswith(_END) for line in lines), 1)

    def test_imports_only_the_standard_library(self):
        tree = ast.parse(_BUNDLED.read_bytes())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, f"relative import at line {node.lineno}")
                found.add(node.module.split(".")[0])
        self.assertEqual(sorted(found - set(sys.stdlib_module_names)), [])

    def test_matches_hs_uploader_writer(self):
        if not _HS_SRC.is_dir():
            self.skipTest(f"no hs-uploader checkout beside sigmond at {_HS_SRC}")
        self.assertTrue(
            _UPSTREAM.is_file(),
            f"{_UPSTREAM} is missing: pull hs-uploader, which holds the sink writer since v3.70",
        )
        _, body = _split_header(_BUNDLED.read_bytes())
        upstream = _UPSTREAM.read_bytes()
        if body != upstream:
            diff = "".join(difflib.unified_diff(
                upstream.decode("utf-8", "replace").splitlines(keepends=True),
                body.decode("utf-8", "replace").splitlines(keepends=True),
                "hs-uploader/src/hs_uploader/sink/writer.py",
                "sigmond/lib/sigmond/hamsci_sink/_bundled.py, below its header",
                n=1,
            ))
            self.fail(
                "the bundled sink writer differs from hs-uploader's.  Refresh it "
                f"from the sigmond checkout:\n  {_REFRESH}\n\n{diff[:4000]}"
            )


class SelectionTests(unittest.TestCase):
    """Which copy sigmond.hamsci_sink hands a client, and SINK_IMPL saying so."""

    def test_bundled_writer_used_without_hs_uploader(self):
        """codar-sounder, hf-tec and superdarn-sounder: no hs-uploader in the venv."""
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "sink.db")
            got = _child(f"""
                sys.modules["hs_uploader"] = None   # import hs_uploader now fails
                import sqlite3
                import sigmond.hamsci_sink as hs
                from sigmond.hamsci_sink import _bundled, writer
                w = hs.Writer.from_env(table="spots", mode="psk",
                                       env={{"SIGMOND_SQLITE_PATH": {db!r}}})
                w.insert([{{"mode": "ft8"}}, {{"mode": "msk144"}}])
                w.close()
                rows = sqlite3.connect({db!r}).execute(
                    "SELECT target_db, target_table, producer, local "
                    "FROM pending_uploads ORDER BY id").fetchall()
                print(json.dumps({{
                    "impl": hs.SINK_IMPL,
                    "bundled": writer is _bundled and hs.Writer is _bundled.Writer,
                    "rows": rows,
                }}))
            """)
        self.assertEqual(got["impl"], "bundled")
        self.assertTrue(got["bundled"])
        self.assertEqual(got["rows"], [["psk", "spots", "psk-recorder", 0],
                                       ["psk", "spots", "meteor-scatter", 0]])

    def test_bundled_writer_used_when_hs_uploader_predates_the_sink(self):
        """smd update pulls sigmond before hs-uploader.  A recorder restarted
        between the two pulls meets an hs-uploader with no sink package."""
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "hs_uploader"
            old.mkdir()
            (old / "__init__.py").write_text('__version__ = "0.1.0"\n')
            got = _child("""
                import hs_uploader
                import sigmond.hamsci_sink as hs
                print(json.dumps({"impl": hs.SINK_IMPL, "found": hs_uploader.__file__}))
            """, extra_path=[tmp])
            self.assertEqual(Path(got["found"]).parent, old)
        self.assertEqual(got["impl"], "bundled")

    def test_other_errors_from_the_sink_package_propagate(self):
        """The fallback catches ImportError only.  A sink package that fails
        some other way surfaces as that failure, and the half-built
        sigmond.hamsci_sink leaves nothing in sys.modules to import later."""
        with tempfile.TemporaryDirectory() as tmp:
            _fake_hs_uploader(tmp, sink_init='raise RuntimeError("sink broke")\n')
            got = _child("""
                try:
                    import sigmond.hamsci_sink
                except RuntimeError as exc:
                    raised = str(exc)
                else:
                    raised = None
                print(json.dumps({
                    "raised": raised,
                    "left": sorted(k for k in sys.modules
                                   if k.startswith("sigmond.hamsci_sink")),
                }))
            """, extra_path=[tmp])
        self.assertEqual(got["raised"], "sink broke")
        self.assertEqual(got["left"], [])

    def test_import_error_inside_the_sink_writer_falls_back(self):
        """An ImportError raised from inside hs_uploader.sink.writer falls
        back too, whether it names a missing module (a ModuleNotFoundError
        about some other package) or a missing name."""
        for label, body in (
            ("missing module", "import no_such_module_xyz\n"),
            ("missing name", "from os import no_such_name_xyz\n"),
        ):
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                _fake_hs_uploader(tmp, writer_py=body)
                got = _child("""
                    import sigmond.hamsci_sink as hs
                    from sigmond.hamsci_sink import _bundled
                    chosen = sys.modules["sigmond.hamsci_sink.writer"]
                    print(json.dumps({
                        "impl": hs.SINK_IMPL,
                        "is_bundled": chosen is _bundled and hs.Writer is _bundled.Writer,
                        "module": hs.Writer.__module__,
                        "file": chosen.__file__,
                    }))
                """, extra_path=[tmp])
                self.assertEqual(got["impl"], "bundled")
                self.assertTrue(got["is_bundled"])
                self.assertEqual(got["module"], "sigmond.hamsci_sink._bundled")
                self.assertEqual(Path(got["file"]).resolve(), _BUNDLED.resolve())

    def test_hs_uploader_writer_preferred_when_importable(self):
        if not _UPSTREAM.is_file():
            self.skipTest(f"no hs-uploader sink writer at {_UPSTREAM}")
        got = _child("""
            import sigmond.hamsci_sink as hs
            from sigmond.hamsci_sink import writer
            import hs_uploader.sink.writer as up
            print(json.dumps({
                "impl": hs.SINK_IMPL,
                "module": writer is up and sys.modules["sigmond.hamsci_sink.writer"] is up,
                "names": [hs.Writer is up.Writer, hs.BufferFull is up.BufferFull,
                          hs.SqliteConfig is up.SqliteConfig],
                "file": up.__file__,
            }))
        """, extra_path=[_HS_SRC])
        self.assertEqual(got["impl"], "hs_uploader")
        self.assertTrue(got["module"])
        self.assertEqual(got["names"], [True, True, True])
        self.assertEqual(Path(got["file"]).resolve(), _UPSTREAM.resolve())


class SurfaceTests(unittest.TestCase):
    """Every name sigmond and the clients import from sigmond.hamsci_sink today."""

    def test_every_name_importers_use_today(self):
        import sigmond.hamsci_sink as hs
        from sigmond.hamsci_sink import SINK_IMPL, BufferFull, SqliteConfig, Writer  # noqa: F401
        from sigmond.hamsci_sink import writer
        from sigmond.hamsci_sink.writer import (  # noqa: F401
            DEFAULT_SQLITE_AUTO_FLUSH_SECONDS, DEFAULT_SQLITE_BATCH_ROWS,
            HEALTH_DEGRADED, HEALTH_NOOP, HEALTH_OK, HEALTH_UNREACHABLE,
            _DEFAULT_SQLITE_PATH, _default_sqlite_writable, _resolve_db_alias,
        )
        self.assertIn(SINK_IMPL, ("hs_uploader", "bundled"))
        self.assertEqual(sorted(hs.__all__),
                         ["BufferFull", "SINK_IMPL", "SqliteConfig", "Writer"])
        # sigmond.hamsci_sink.writer names the chosen module itself, so a
        # global set through it (tests/test_hamsci_sink.py sets two) reaches
        # Writer.from_env.
        self.assertIs(sys.modules["sigmond.hamsci_sink.writer"], writer)
        self.assertIs(Writer, writer.Writer)
        self.assertIs(Writer.from_env.__func__.__globals__, vars(writer))
        for name in ("from_env", "insert", "flush", "close", "__enter__", "__exit__"):
            self.assertTrue(callable(getattr(Writer, name)), name)
        w = Writer("psk", "spots", schema_version=2)   # no config: a no-op writer
        self.assertEqual((w.database, w.table, w.schema_version), ("psk", "spots", 2))
        self.assertEqual((w.health, w.is_noop, w.buffered), (HEALTH_NOOP, True, 0))
        self.assertEqual(writer.logger.name, "sigmond.hamsci_sink")


if __name__ == "__main__":
    unittest.main()
