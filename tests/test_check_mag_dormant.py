"""`smd check`: a dormant mag-recorder with no sensor is ok, not FAIL."""
import tempfile
import unittest
from pathlib import Path

from sigmond.commands import check
from sigmond.commands.check import OK, FAIL

from tests.check_fakes import make_env, svc


class MagDormant(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        self.device = self.dir / 'ttyMAG0'          # never created: no sensor

    def line(self, service):
        return check.mag_line(make_env(service=lambda p: service),
                              mag_dir=self.dir, device=str(self.device))

    def test_disabled_inactive_no_sensor_is_ok_dormant(self):
        line = self.line(svc('inactive', None, enabled=False))
        self.assertEqual(line, check.Line(OK, 'magnetometer', 'no magnetometer; dormant'))

    def test_disabled_unit_with_no_service_is_ok_dormant(self):
        line = self.line(svc('absent', None, enabled=False))
        self.assertEqual(line.mark, OK)
        self.assertEqual(line.text, 'no magnetometer; dormant')

    def test_enabled_with_no_sensor_still_fails(self):
        # an enabled unit with a once-working or declared sensor is a fault
        (self.dir / 'samples-2026-01-01.jsonl').write_text('{}\n')
        self.assertEqual(self.line(svc('active')).mark, FAIL)

    def test_sensor_present_but_unit_disabled_is_not_dormant(self):
        self.device.write_text('')
        self.assertEqual(self.line(svc('inactive', None, enabled=False)).mark, FAIL)
