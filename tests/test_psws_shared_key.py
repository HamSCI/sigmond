"""One station id, one PSWS key, every instrument.

Michael, 2026-09-03, reading `smd psws verify` on AC0G-ND:

    ✓ SFTP login OK as S000111@pswsnetwork.eng.ua.edu
    ⚠ hf-timestd: SSH key missing: /home/timestd/.ssh/id_rsa_psws
    ⚠ mag-recorder: SSH key missing: /etc/hs-uploader/keys/id_ed25519

  "The single station ID and psws keys should cover two (when they exist)
   instruments: the rx888 and magnetometer."

He is right, and this module's own docstring already said so: both recorders
ship "to the SAME PSWS SFTP server with the SAME key-based mechanism".  The
portal authenticates the STATION account; the instrument id rides in the
upload path.  So one registered public key is the whole credential.

Two key files existed for one credential because two SERVICE USERS had to
read them — `magrec` cannot read `/home/timestd/.ssh/`, and on live B4 that
key is mode 0600 timestd:timestd while /etc/hs-uploader/keys/id_ed25519 is
0600 hsupload:sigmond.  `magrec` belongs to `dialout` alone.  So the fix is
a path outside every user home, with a group the PSWS service users share.

⚠ Existing stations are untouched BY CONSTRUCTION: read_state prefers the
config's own field over the default, and live B4 sets both explicitly
(`ssh_key = "/home/timestd/.ssh/id_rsa_psws"`,
 `ssh_key_file = "/etc/mag-recorder/keys/id_ed25519"`).  The last test here
pins that precedence, because a default that could override a set field
would silently invalidate a key already registered with the portal.
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import psws                                       # noqa: E402


class SharedPswsKeyTest(unittest.TestCase):

    def test_every_psws_recorder_defaults_to_the_same_key(self):
        defaults = {rec: spec["default_key"]
                    for rec, spec in psws.RECORDERS.items()}
        self.assertEqual(
            len(set(defaults.values())), 1,
            f"one station credential, one default key path — got {defaults}")

    def test_the_shared_default_lives_outside_every_user_home(self):
        # A key under /home/<user>/ can only be read by that user: the 0700
        # .ssh directory is the point of it.  That is what forced two files.
        shared = next(iter({s["default_key"] for s in psws.RECORDERS.values()}))
        self.assertFalse(shared.startswith("/home/"),
                         f"{shared} sits in a user home — no other service "
                         f"user can read it")
        self.assertTrue(shared.startswith("/etc/"),
                        f"{shared} should be station configuration")

    def test_the_shared_key_names_a_group_the_service_users_can_share(self):
        # Ownership cannot be a single service user either, or the other one
        # is locked out again.  The spec has to say which group carries it.
        self.assertTrue(getattr(psws, "SHARED_KEY_GROUP", ""),
                        "no group named for the shared key")

    def test_the_filename_does_not_lie_about_the_algorithm(self):
        # The old default was `id_rsa_psws` while key_type said ed25519 and
        # ssh-keygen was invoked with -t ed25519.  An operator reading the
        # path learned the wrong thing about their own credential.
        shared = next(iter({s["default_key"] for s in psws.RECORDERS.values()}))
        self.assertNotIn("rsa", Path(shared).name.lower())
        for rec, spec in psws.RECORDERS.items():
            self.assertEqual(spec["key_type"], "ed25519", rec)

    def test_a_config_set_key_path_still_beats_the_default(self):
        # ⛔ The guard that keeps B4 and DASI002 working.
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "timestd-config.toml"
            cfg.write_text(
                '[station]\n'
                'id = "S000123"\n'
                'instrument_id = "131"\n'
                '[uploader.sftp]\n'
                'ssh_key = "/home/timestd/.ssh/id_rsa_psws"\n'
            )
            spec = psws.RECORDERS["hf-timestd"]
            real = spec["config"]
            spec["config"] = cfg
            try:
                st = psws.read_state("hf-timestd")
            finally:
                spec["config"] = real
        self.assertEqual(st.key_path, "/home/timestd/.ssh/id_rsa_psws",
                         "a station that already registered a key with the "
                         "portal must keep using it")
        self.assertEqual(st.station, "S000123")


if __name__ == "__main__":
    unittest.main()
