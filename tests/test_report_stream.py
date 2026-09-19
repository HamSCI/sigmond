"""A report is one stream.

⛔ `smd status` used to write 106 lines to stdout and exactly one — the red ✗
— to stderr.  Piped to a pager that is not cosmetic: `smd status | less` sends
the 106 to the pager and prints the ✗ straight to the terminal, out of order
and usually scrolled away.  On W3USR-019 (2026-09-19) the line that went
missing was the one naming the station's blocker:

    ✗  station.psws_station_id is unset (need PSWS-issued S0xxxxx)

rob: "everything should go to standard out."

So all five helpers share `_report_stream()`.  The exception is a
machine-readable invocation: with `--json` on the command line stdout belongs
to the JSON document and the human lines step aside — which is stricter than
the old behaviour, where ✓/⚠/plain lines could interleave into the document.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_report_stream", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_report_stream", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

EMITTERS = ("_ok", "_warn", "_err", "_info")


class OneStreamPerReportTests(unittest.TestCase):

    def _capture(self, argv):
        """Run every emitter under a given argv; return (stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
             mock.patch.object(sys, "stdout", out), \
             mock.patch.object(sys, "stderr", err):
            for name in EMITTERS:
                getattr(smd, name)(f"{name} line")
            smd._heading("a heading")
        return out.getvalue(), err.getvalue()

    def test_every_emitter_lands_on_stdout_for_a_human(self):
        out, err = self._capture(["smd", "status"])
        for name in EMITTERS:
            with self.subTest(emitter=name):
                self.assertIn(
                    f"{name} line", out,
                    f"{name} did not reach stdout, so a piped report loses it")
        self.assertEqual(
            err, "",
            f"part of the report went to stderr and will not reach a pager: {err!r}")

    def test_err_specifically_is_not_alone_on_stderr(self):
        """The exact regression: ✗ split away from the rest of the report."""
        out, err = self._capture(["smd", "status"])
        self.assertIn("_err line", out)
        self.assertNotIn("_err line", err)

    def test_json_invocations_keep_stdout_clean(self):
        """--json means stdout is the document; humans step aside."""
        out, err = self._capture(["smd", "admin", "validate", "--json"])
        self.assertEqual(
            out, "",
            f"human output leaked into a JSON document on stdout: {out!r}")
        for name in EMITTERS:
            with self.subTest(emitter=name):
                self.assertIn(f"{name} line", err)

    def test_the_helpers_do_not_hardcode_a_stream(self):
        """A new helper that writes file=sys.stderr would resplit the report."""
        src = (REPO / "bin" / "smd").read_text().splitlines()
        offenders = []
        for n, line in enumerate(src, 1):
            stripped = line.strip()
            if not stripped.startswith("def _ok(") and \
               not stripped.startswith("def _warn(") and \
               not stripped.startswith("def _err(") and \
               not stripped.startswith("def _info(") and \
               not stripped.startswith("def _heading("):
                continue
            body = line + (src[n] if n < len(src) else "")
            if "file=sys.stderr" in body or "file=sys.stdout" in body:
                offenders.append(f"{n}: {stripped}")
        self.assertEqual(
            offenders, [],
            "these pin a stream instead of using _report_stream(), which is "
            "how the report got split in the first place:\n  "
            + "\n  ".join(offenders))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
