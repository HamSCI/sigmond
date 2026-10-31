"""smd apply lifts usbcore.usbfs_memory_mb, the host-wide cap on in-flight
USB transfer memory.  AI6VN-PM 2026-10-31: with two RX888s and the 16 MiB
default, whichever radiod started second got no transfers and quit with
"No rx888 data for 5 seconds" while the first kept running."""
from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock


def _load_smd():
    spec = importlib.util.spec_from_loader("smd_mod_usbfs", loader=None)
    smd = importlib.util.module_from_spec(spec)
    smd.__dict__["__file__"] = "bin/smd"
    src = Path(__file__).resolve().parent.parent / "bin" / "smd"
    with mock.patch.dict(os.environ, {"SIGMOND_NO_VENV_REEXEC": "1"}):
        exec(compile(src.read_text(), str(src), "exec"), smd.__dict__)
    return smd


class UsbfsMemoryTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.smd = _load_smd()
        self.tmp = Path(tempfile.mkdtemp())
        self.param = self.tmp / "usbfs_memory_mb"
        self.conf = self.tmp / "tmpfiles.d" / "sigmond-usbfs.conf"
        self.smd.USBFS_MEMORY_PARAM = self.param
        self.smd.USBFS_MEMORY_TMPFILES = self.conf
        for name in ("_ok", "_info", "_warn"):
            setattr(self.smd, name, mock.Mock())

    def test_default_cap_is_lifted_and_persisted(self):
        self.param.write_text("16\n")
        self.assertTrue(self.smd._ensure_usbfs_memory())
        self.assertEqual(self.param.read_text().strip(), "0")
        text = self.conf.read_text()
        self.assertIn(f"w {self.param} - - - - 0", text)

    def test_second_run_is_a_no_op(self):
        self.param.write_text("16\n")
        self.smd._ensure_usbfs_memory()
        self.assertFalse(self.smd._ensure_usbfs_memory())

    def test_live_value_reset_by_reboot_is_reapplied(self):
        self.param.write_text("16\n")
        self.smd._ensure_usbfs_memory()
        self.param.write_text("16\n")          # a reboot without tmpfiles
        self.assertTrue(self.smd._ensure_usbfs_memory())
        self.assertEqual(self.param.read_text().strip(), "0")

    def test_dry_run_writes_nothing(self):
        self.param.write_text("16\n")
        self.assertFalse(self.smd._ensure_usbfs_memory(dry_run=True))
        self.assertEqual(self.param.read_text().strip(), "16")
        self.assertFalse(self.conf.exists())

    def test_no_usbcore_is_a_no_op(self):
        self.assertFalse(self.smd._ensure_usbfs_memory())
        self.assertFalse(self.conf.exists())


if __name__ == "__main__":
    unittest.main()
