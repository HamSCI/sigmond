"""`smd check` VM page lines and the full list of lines
(tasks/spec-smd-check-v372.md §2, §4)."""
import io
import unittest
import urllib.error

from sigmond.commands import check
from sigmond.commands.check import FAIL, OK

from tests.check_fakes import make_env


def pages(env):
    return [ln for ln in check.run_checks(env, [c for c in check.CHECKS if 'page' in c[0]])]


class PageTests(unittest.TestCase):
    def test_answering_pages_pass_on_127_0_0_1(self):
        asked = []
        env = make_env(http=lambda url: asked.append(url) or (200, ''))
        self.assertEqual(pages(env), [
            check.Line(OK, 'receiver page', 'http://127.0.0.1:8081 answers'),
            check.Line(OK, 'station pages', 'http://127.0.0.1:8000 answers'),
            check.Line(OK, 'magnetometer page', 'http://127.0.0.1:8082 answers')])
        self.assertEqual(asked, ['http://127.0.0.1:8081/', 'http://127.0.0.1:8000/',
                                 'http://127.0.0.1:8082/'])

    def test_no_magnetometer_page_line_without_mag_recorder(self):
        env = make_env(installed=lambda n: n != 'mag-recorder', http=lambda url: (200, ''))
        self.assertEqual([ln.label for ln in pages(env)], ['receiver page', 'station pages'])

    def test_a_refused_or_wrong_status_fails(self):
        env = make_env(http=lambda url: (None, '[Errno 111] Connection refused') if '8081' in url
                       else (503, ''))
        lines = pages(env)
        self.assertEqual(lines[0], check.Line(FAIL, 'receiver page',
                                              'http://127.0.0.1:8081 does not answer: '
                                              '[Errno 111] Connection refused'))
        self.assertEqual(lines[1].text, 'http://127.0.0.1:8000 does not answer: status 503')


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class HttpStatusTests(unittest.TestCase):
    def test_status_codes_and_reasons(self):
        self.assertEqual(check.http_status('http://x/', urlopen=lambda u, timeout: _Resp()), (200, ''))

        def http_error(u, timeout):
            raise urllib.error.HTTPError(u, 404, 'nf', {}, None)
        self.assertEqual(check.http_status('http://x/', urlopen=http_error), (404, ''))

        def refused(u, timeout):
            raise urllib.error.URLError(ConnectionRefusedError(111, 'Connection refused'))
        status, why = check.http_status('http://x/', urlopen=refused)
        self.assertIsNone(status)
        self.assertIn('Connection refused', why)


class AllLinesTests(unittest.TestCase):
    def test_the_lines_come_in_the_order_the_installer_reads(self):
        self.assertEqual([label for label, _ in check.CHECKS],
                         ['PSK', 'WSPR', 'magnetometer', 'GRAPE', 'storage', 'uploads',
                          'meteor scatter', 'receiver page', 'station pages', 'magnetometer page'])


if __name__ == '__main__':
    unittest.main()
