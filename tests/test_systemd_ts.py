"""systemd 249 (Ubuntu 22.04) refuses ``systemctl show --timestamp=unix``
("Invalid value: unix.", exit 1).  Every reader of unit start times then
failed: on VK5ARG (2026-11-14) each upload-lever step of bring-up, so the
uploads stayed held and hs-uploader had no pipelines.  The readers keep the
unix call and fall back to --timestamp=utc only when systemd refuses it."""
import calendar
import importlib.machinery
import importlib.util
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from sigmond import systemd_ts
from sigmond import sink_levers
from sigmond.commands import uploader as up_cmd

REPO = Path(__file__).resolve().parent.parent
T = calendar.timegm((2026, 11, 14, 19, 5, 22))      # Sat 2026-11-14 19:05:22 UTC


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_systemd_ts", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_systemd_ts", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Systemd249:
    """systemctl as systemd 249 answers it: no ``--timestamp=unix``."""

    def __init__(self, stdout_utc):
        self.stdout_utc = stdout_utc
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if "--timestamp=unix" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "Invalid value: unix.\n")
        assert "--timestamp=utc" in argv, argv
        return subprocess.CompletedProcess(argv, 0, self.stdout_utc, "")


class Helpers(unittest.TestCase):
    def test_utc_value(self):
        self.assertEqual(systemd_ts.utc_value_to_unix("Sat 2026-11-14 19:05:22 UTC"), f"@{T}")

    def test_utc_value_with_microseconds(self):
        self.assertEqual(systemd_ts.utc_value_to_unix("Sat 2026-11-14 19:05:22.398306 UTC"),
                         f"@{T}.398306")

    def test_other_values_untouched(self):
        for v in ("active", "", "n/a", "0", "@123"):
            self.assertEqual(systemd_ts.utc_value_to_unix(v), v)

    def test_key_value_output(self):
        out = systemd_ts.utc_to_unix_output(
            "Id=a.service\nActiveState=active\nActiveEnterTimestamp=Sat 2026-11-14 19:05:22 UTC\n"
            "ActiveEnterTimestampMonotonic=5000000\n\nId=b.service\nActiveEnterTimestamp=\n")
        self.assertEqual(out, f"Id=a.service\nActiveState=active\nActiveEnterTimestamp=@{T}\n"
                              "ActiveEnterTimestampMonotonic=5000000\n\nId=b.service\n"
                              "ActiveEnterTimestamp=\n")

    def test_bare_value_output(self):
        self.assertEqual(systemd_ts.utc_to_unix_output("Sat 2026-11-14 19:05:22 UTC\n"), f"@{T}\n")

    def test_refusal_is_recognised(self):
        self.assertTrue(systemd_ts.unix_unsupported(
            subprocess.CompletedProcess([], 1, "", "Invalid value: unix.\n")))
        self.assertFalse(systemd_ts.unix_unsupported(
            subprocess.CompletedProcess([], 1, "", "Unit x.service not loaded.\n")))
        self.assertFalse(systemd_ts.unix_unsupported(subprocess.CompletedProcess([], 0, "", "")))

    def test_with_utc_swaps_only_that_flag(self):
        self.assertEqual(systemd_ts.with_utc(["systemctl", "show", "--timestamp=unix", "-p", "X"]),
                         ["systemctl", "show", "--timestamp=utc", "-p", "X"])


class SinkLevers(unittest.TestCase):
    def test_show_props_falls_back_on_systemd_249(self):
        fake = Systemd249("Id=psk-recorder@X.service\nActiveState=active\n"
                          "ActiveEnterTimestamp=Sat 2026-11-14 19:05:22 UTC\n")
        blocks = sink_levers._show_props(fake, ["psk-recorder@X.service"],
                                         ("Id", "ActiveState", "ActiveEnterTimestamp"))
        self.assertEqual(blocks, [{"Id": "psk-recorder@X.service", "ActiveState": "active",
                                   "ActiveEnterTimestamp": f"@{T}"}])
        self.assertEqual(len(fake.calls), 2)

    def test_show_props_one_call_where_unix_works(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "ActiveEnterTimestamp=@7\n", "")
        self.assertEqual(sink_levers._show_props(run, ["a.service"], ("ActiveEnterTimestamp",)),
                         [{"ActiveEnterTimestamp": "@7"}])
        self.assertEqual(len(calls), 1)
        self.assertIn("--timestamp=unix", calls[0])

    def test_other_failures_still_raise(self):
        def run(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", "Failed to connect to bus\n")
        with self.assertRaises(OSError):
            sink_levers._show_props(run, ["a.service"], ("Id",))


class UploaderCmd(unittest.TestCase):
    def test_service_started_at_falls_back_on_systemd_249(self):
        fake = Systemd249("Sat 2026-11-14 19:05:22 UTC\n")
        with mock.patch.object(up_cmd.subprocess, "run", side_effect=fake):
            self.assertEqual(up_cmd._service_started_at(), float(T))
        self.assertEqual(len(fake.calls), 2)


class AlignStartedAt(unittest.TestCase):
    smd = _load_smd()

    def test_falls_back_on_systemd_249(self):
        fake = Systemd249("ActiveState=active\nActiveEnterTimestamp=Sat 2026-11-14 19:05:22 UTC\n")
        self.assertEqual(self.smd._align_units_started_at(["a.service"], run=fake), float(T))


if __name__ == "__main__":
    unittest.main()
