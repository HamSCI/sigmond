"""Tests for sigmond.identity — the station identity file list.

Every test builds a fake station under a temporary root; nothing touches /etc.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from sigmond import identity


def _keygen(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
                   check=True)


def _vm_root(tmp: Path) -> Path:
    root = tmp / "vm"
    _keygen(root / "etc/ssh/ssh_host_ed25519_key")
    _keygen(root / "etc/hs-uploader/keys/id_ed25519_host")
    (root / "home/timestd/.ssh").mkdir(parents=True)
    # Neither of these is identity; both must stay out.
    (root / "home/timestd/.ssh/known_hosts").write_text("x\n")
    (root / "etc/ssh/sshd_config").write_text("Port 22\n")
    return root


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_vm_manifest_lists_host_and_uploader_keys_only(self):
        root = _vm_root(self.tmp)
        paths = {e["path"] for e in identity.manifest("vm", str(root))["files"]}
        self.assertEqual(paths, {
            "/etc/ssh/ssh_host_ed25519_key",
            "/etc/ssh/ssh_host_ed25519_key.pub",
            "/etc/hs-uploader/keys/id_ed25519_host",
            "/etc/hs-uploader/keys/id_ed25519_host.pub",
        })

    def test_pub_fingerprint_matches_ssh_keygen(self):
        root = _vm_root(self.tmp)
        pub = root / "etc/ssh/ssh_host_ed25519_key.pub"
        want = subprocess.run(["ssh-keygen", "-lf", str(pub)], check=True,
                              capture_output=True, text=True).stdout.split()[1]
        entry = next(e for e in identity.manifest("vm", str(root))["files"]
                     if e["path"] == "/etc/ssh/ssh_host_ed25519_key.pub")
        self.assertEqual(entry["ssh_fingerprint"], want)

    def test_manifest_records_mode(self):
        root = _vm_root(self.tmp)
        os.chmod(root / "etc/ssh/ssh_host_ed25519_key", 0o600)
        entry = next(e for e in identity.manifest("vm", str(root))["files"]
                     if e["path"] == "/etc/ssh/ssh_host_ed25519_key")
        self.assertEqual(entry["mode"], "0600")

    def test_manifest_holds_no_private_key_bytes(self):
        root = _vm_root(self.tmp)
        secret = (root / "etc/ssh/ssh_host_ed25519_key").read_text()
        body = [l for l in secret.splitlines() if l and not l.startswith("-----")]
        text = json.dumps(identity.manifest("vm", str(root)))
        for line in body:
            self.assertNotIn(line, text)

    def test_missing_optional_files_are_simply_absent(self):
        root = self.tmp / "bare"
        root.mkdir()
        self.assertEqual(identity.manifest("vm", str(root))["files"], [])

    def test_unknown_plane_refused(self):
        with self.assertRaises(ValueError):
            identity.manifest("dom0", str(self.tmp))


if __name__ == "__main__":
    unittest.main()
