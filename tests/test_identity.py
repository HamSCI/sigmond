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


FRPC = """\
serverAddr = "gw.example"
serverPort = 35736
user = "0123456789abcdef"

[metadatas]
pubkey = "ssh-ed25519 AAAA..."
site = "TEST_SITE"

[auth]
method = "token"
token = "s3cret-token-value"

[[proxies]]
name = "TEST_SITE-host-ssh"
"""


class RacCredentialTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "etc/sigmond").mkdir(parents=True)
        (self.root / "etc/sigmond/frpc-host.toml").write_text(FRPC)
        _keygen(self.root / "etc/ssh/ssh_host_ed25519_key")

    def tearDown(self):
        self._tmp.cleanup()

    def test_only_credential_fields_are_taken(self):
        self.assertEqual(identity.rac_credential(str(self.root)), {
            "user": "0123456789abcdef",
            "auth.method": "token",
            "auth.token": "s3cret-token-value",
        })

    def test_pm_manifest_names_fields_but_holds_no_values(self):
        m = identity.manifest("pm", str(self.root))
        self.assertEqual(m["rac_credential"]["fields"],
                         ["auth.method", "auth.token", "user"])
        text = json.dumps(m)
        self.assertNotIn("s3cret-token-value", text)
        self.assertNotIn("0123456789abcdef", text)

    def test_pm_without_rac_config_records_none(self):
        (self.root / "etc/sigmond/frpc-host.toml").unlink()
        self.assertIsNone(identity.manifest("pm", str(self.root))["rac_credential"])

    def test_token_change_changes_the_digest(self):
        before = identity.manifest("pm", str(self.root))["rac_credential"]["sha256"]
        (self.root / "etc/sigmond/frpc-host.toml").write_text(
            FRPC.replace("s3cret-token-value", "another-token"))
        after = identity.manifest("pm", str(self.root))["rac_credential"]["sha256"]
        self.assertNotEqual(before, after)


import builtins
import hashlib
import io
import sys
import tarfile
from unittest import mock

IDENTITY_SRC = Path(identity.__file__).read_bytes()


class _OpenCounter:
    """Counts real opens of one path.

    tarfile binds `bltn_open = builtins.open` (via `from builtins import open
    as bltn_open`) once at its own import time, so patching `builtins.open`
    alone is invisible to tar.add()'s internal read.  Patch both references.
    """

    def __init__(self, target_path: str):
        self.target = os.path.realpath(target_path)
        self.opens: list[str] = []

    def _wrap(self, real):
        def wrapped(path, *a, **kw):
            p = path.decode() if isinstance(path, bytes) else str(path)
            try:
                if os.path.realpath(p) == self.target:
                    self.opens.append(p)
            except OSError:
                pass
            return real(path, *a, **kw)
        return wrapped

    def __enter__(self):
        self._patches = [
            mock.patch("builtins.open", self._wrap(builtins.open)),
            mock.patch("tarfile.bltn_open", self._wrap(tarfile.bltn_open)),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


class ExportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_export_round_trip_keeps_content_and_mode(self):
        root = _vm_root(self.tmp)
        os.chmod(root / "etc/ssh/ssh_host_ed25519_key", 0o600)
        buf = io.BytesIO()
        m = identity.export("vm", buf, str(root))
        buf.seek(0)
        with tarfile.open(fileobj=buf) as tar:
            names = tar.getnames()
            key = tar.getmember("etc/ssh/ssh_host_ed25519_key")
            self.assertEqual(key.mode, 0o600)
            self.assertEqual(tar.extractfile(key).read(),
                             (root / "etc/ssh/ssh_host_ed25519_key").read_bytes())
            inner = json.loads(tar.extractfile(identity.MANIFEST_MEMBER).read())
        self.assertEqual(names[-1], identity.MANIFEST_MEMBER)
        self.assertEqual({n for n in names if not n.startswith("identity/")},
                         {e["path"].lstrip("/") for e in m["files"]})
        self.assertEqual(inner, m)

    def test_pm_export_carries_the_credential_member(self):
        root = self.tmp / "pm"
        (root / "etc/sigmond").mkdir(parents=True)
        (root / "etc/sigmond/frpc-host.toml").write_text(FRPC)
        buf = io.BytesIO()
        identity.export("pm", buf, str(root))
        buf.seek(0)
        with tarfile.open(fileobj=buf) as tar:
            member = tar.getmember(identity.RAC_MEMBER)
            cred = json.loads(tar.extractfile(member).read())
        self.assertEqual(member.mode, 0o600)
        self.assertEqual(cred["auth.token"], "s3cret-token-value")

    def test_fingerprints_runs_from_piped_source(self):
        # Exactly how site-identity runs it on a station with no sigmond.
        root = _vm_root(self.tmp)
        out = subprocess.run(
            [sys.executable, "-", "fingerprints", "--plane", "vm", "--root", str(root)],
            input=IDENTITY_SRC, capture_output=True, check=True)
        self.assertEqual(json.loads(out.stdout), identity.manifest("vm", str(root)))

    def test_export_runs_from_piped_source(self):
        root = _vm_root(self.tmp)
        out = subprocess.run(
            [sys.executable, "-", "export", "--plane", "vm", "--root", str(root)],
            input=IDENTITY_SRC, capture_output=True, check=True)
        with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tar:
            self.assertIn("etc/ssh/ssh_host_ed25519_key", tar.getnames())

    def test_export_reads_each_file_exactly_once(self):
        # A file read twice (once for the manifest digest, once again for the
        # tar member) is the bug this test catches: two reads of the SAME
        # bytes happen to agree today, but the second read is pure waste, and
        # a manifest built from one read and a tar member built from another
        # is exactly how the two could disagree.
        root = _vm_root(self.tmp)
        with _OpenCounter(str(root / "etc/ssh/ssh_host_ed25519_key")) as counter:
            buf = io.BytesIO()
            m = identity.export("vm", buf, str(root))
        self.assertEqual(len(counter.opens), 1,
                          f"file opened {len(counter.opens)} times, want 1")

        buf.seek(0)
        with tarfile.open(fileobj=buf) as tar:
            data = tar.extractfile("etc/ssh/ssh_host_ed25519_key").read()
        entry = next(e for e in m["files"]
                     if e["path"] == "/etc/ssh/ssh_host_ed25519_key")
        self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])

    def test_pm_export_reads_the_rac_config_exactly_once(self):
        root = self.tmp / "pm"
        (root / "etc/sigmond").mkdir(parents=True)
        (root / "etc/sigmond/frpc-host.toml").write_text(FRPC)
        with _OpenCounter(str(root / "etc/sigmond/frpc-host.toml")) as counter:
            buf = io.BytesIO()
            identity.export("pm", buf, str(root))
        self.assertEqual(len(counter.opens), 1,
                          f"frpc-host.toml opened {len(counter.opens)} times, want 1")


if __name__ == "__main__":
    unittest.main()
