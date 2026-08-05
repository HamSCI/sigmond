"""Tests for sigmond.timing_judge — the hf-timestd offset-judge consumers.

Two surfaces: the opt-in radiod restart-request honor path (used by
bin/sigmond-radiod-watchdog) and the `smd status` "timing judge"
renderer.  All filesystem and systemctl I/O is injected/tmp-dir'd.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond import timing_judge as tj

NOW = 1_800_000_000.0  # fixed 'now' for deterministic freshness math


def _iso(epoch: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        epoch, tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _valid_request(requested: float = NOW - 60.0, radiod_id: str = 'B4-100') -> dict:
    return {
        'schema': 'radiod-restart-request-v1',
        'requested_utc': _iso(requested),
        'source_key': 'hf-status.local/0000a4b2',
        'radiod_id': radiod_id,
        'offset_ms': 1203000.0,
        'sustained_s': 3600.0,
        'evidence': {
            'tier': 'T5',
            'sigma_ns': 850.0,
            'rate_ppm': 0.002,
            'classification': 'radiod-epoch-fault',
        },
        'cooldown_until': _iso(requested + 21600.0),
    }


class _Rig:
    """tmp-dir + spy harness for process_restart_request."""

    def __init__(self, tmp: Path, unit_states=None):
        self.request_path = tmp / 'radiod-restart-request.json'
        self.stamp_path = tmp / 'radiod-restart-honored.json'
        self.unit_states = unit_states or {}
        self.state_queries = []
        self.restarts = []

    def write_request(self, doc) -> None:
        self.request_path.write_text(
            doc if isinstance(doc, str) else json.dumps(doc))

    def unit_state(self, unit: str) -> str:
        self.state_queries.append(unit)
        return self.unit_states.get(unit, 'unknown')

    def restart_unit(self, unit: str) -> None:
        self.restarts.append(unit)

    def process(self, enabled: bool, now: float = NOW, **kw):
        return tj.process_restart_request(
            enabled,
            request_path=self.request_path,
            stamp_path=self.stamp_path,
            now=now,
            unit_state=self.unit_state,
            restart_unit=self.restart_unit,
            **kw)


class HonorGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.rig = _Rig(Path(self._tmp.name),
                        unit_states={'radiod@B4-100.service': 'active'})

    # -- artifact absent ----------------------------------------------------

    def test_no_artifact_is_silent_noop(self):
        d = self.rig.process(enabled=True)
        self.assertEqual(d.action, 'none')
        self.assertFalse(d.loud)
        self.assertEqual(self.rig.restarts, [])

    # -- site policy off ⇒ never, even for a perfect request ----------------

    def test_disabled_never_restarts(self):
        self.rig.write_request(_valid_request())
        d = self.rig.process(enabled=False)
        self.assertEqual(d.action, 'ignore')
        self.assertIn('site policy', d.reason)
        self.assertEqual(self.rig.restarts, [])
        self.assertEqual(self.rig.state_queries, [])   # not even probed
        self.assertFalse(self.rig.stamp_path.exists())
        # hf-timestd's artifact is never touched
        self.assertTrue(self.rig.request_path.exists())

    # -- happy path ---------------------------------------------------------

    def test_enabled_valid_fresh_restarts_exactly_once(self):
        self.rig.write_request(_valid_request())
        d = self.rig.process(enabled=True)
        self.assertEqual(d.action, 'restart')
        self.assertTrue(d.loud)
        self.assertEqual(d.unit, 'radiod@B4-100.service')
        self.assertEqual(self.rig.restarts, ['radiod@B4-100.service'])
        # acted stamp records the request's requested_utc
        stamp = json.loads(self.rig.stamp_path.read_text())
        self.assertEqual(stamp['schema'], 'radiod-restart-honored-v1')
        self.assertEqual(stamp['requested_utc'],
                         _valid_request()['requested_utc'])
        self.assertEqual(stamp['unit'], 'radiod@B4-100.service')
        # artifact left in place for hf-timestd to withdraw
        self.assertTrue(self.rig.request_path.exists())

        # A second watchdog pass over the SAME request must not act again.
        d2 = self.rig.process(enabled=True)
        self.assertEqual(d2.action, 'ignore')
        self.assertIn('never honoring twice', d2.reason)
        self.assertEqual(self.rig.restarts, ['radiod@B4-100.service'])

    def test_new_requested_utc_after_honored_acts_again(self):
        self.rig.write_request(_valid_request(requested=NOW - 300.0))
        self.rig.process(enabled=True)
        self.rig.write_request(_valid_request(requested=NOW - 30.0))
        d = self.rig.process(enabled=True)
        self.assertEqual(d.action, 'restart')
        self.assertEqual(len(self.rig.restarts), 2)

    # -- staleness ----------------------------------------------------------

    def test_stale_request_ignored(self):
        self.rig.write_request(
            _valid_request(requested=NOW - tj.REQUEST_FRESH_S - 1.0))
        d = self.rig.process(enabled=True)
        self.assertEqual(d.action, 'ignore')
        self.assertIn('stale', d.reason)
        self.assertEqual(self.rig.restarts, [])
        self.assertFalse(self.rig.stamp_path.exists())

    def test_future_dated_request_ignored(self):
        self.rig.write_request(_valid_request(requested=NOW + 3600.0))
        d = self.rig.process(enabled=True)
        self.assertEqual(d.action, 'ignore')
        self.assertEqual(self.rig.restarts, [])

    # -- schema validity ----------------------------------------------------

    def test_unparseable_json_ignored_loudly(self):
        self.rig.write_request('{not json')
        d = self.rig.process(enabled=True)
        self.assertEqual(d.action, 'ignore')
        self.assertTrue(d.loud)
        self.assertEqual(self.rig.restarts, [])

    def test_wrong_schema_ignored_loudly(self):
        doc = _valid_request()
        doc['schema'] = 'radiod-restart-request-v99'
        self.rig.write_request(doc)
        d = self.rig.process(enabled=True)
        self.assertEqual(d.action, 'ignore')
        self.assertTrue(d.loud)
        self.assertIn('schema', d.reason)
        self.assertEqual(self.rig.restarts, [])

    def test_missing_fields_ignored_loudly(self):
        for field in ('requested_utc', 'radiod_id', 'source_key',
                      'offset_ms', 'sustained_s', 'evidence'):
            doc = _valid_request()
            del doc[field]
            self.rig.write_request(doc)
            d = self.rig.process(enabled=True)
            self.assertEqual(d.action, 'ignore', field)
            self.assertTrue(d.loud, field)
        self.assertEqual(self.rig.restarts, [])

    def test_radiod_id_with_path_chars_rejected(self):
        doc = _valid_request()
        doc['radiod_id'] = '../../evil'
        self.rig.write_request(doc)
        d = self.rig.process(enabled=True)
        self.assertEqual(d.action, 'ignore')
        self.assertTrue(d.loud)
        self.assertEqual(self.rig.state_queries, [])
        self.assertEqual(self.rig.restarts, [])

    # -- unit mapping -------------------------------------------------------

    def test_unknown_unit_no_action_loud(self):
        self.rig.write_request(_valid_request(radiod_id='ac0g-bee1-rx888'))
        d = self.rig.process(enabled=True)
        self.assertEqual(d.action, 'ignore')
        self.assertTrue(d.loud)
        self.assertIn('refusing to act', d.reason)
        self.assertEqual(d.unit, 'radiod@ac0g-bee1-rx888.service')
        self.assertEqual(self.rig.restarts, [])
        self.assertFalse(self.rig.stamp_path.exists())

    def test_inactive_local_unit_no_action_loud(self):
        self.rig.unit_states['radiod@B4-100.service'] = 'inactive'
        self.rig.write_request(_valid_request())
        d = self.rig.process(enabled=True)
        self.assertEqual(d.action, 'ignore')
        self.assertTrue(d.loud)
        self.assertEqual(self.rig.restarts, [])


class ValidateRequestTests(unittest.TestCase):
    def test_valid_request_no_problems(self):
        self.assertEqual(tj.validate_restart_request(_valid_request()), [])

    def test_non_dict(self):
        self.assertTrue(tj.validate_restart_request(['x']))

    def test_bad_requested_utc(self):
        doc = _valid_request()
        doc['requested_utc'] = 'yesterday-ish'
        self.assertTrue(tj.validate_restart_request(doc))


# ---------------------------------------------------------------------------
# offset_judge.json loading + status rendering
# ---------------------------------------------------------------------------

def _judge_doc() -> dict:
    return {
        'schema': 'offset-judge-v1',
        'utc_published': _iso(NOW - 5.0),
        'k': 5.0,
        'judge': {'tier': 'T5', 'sigma_ns': 850.0, 'age_s': 4.2},
        'gpsdo_discipline': 'locked',
        'sources': {
            'hf-status.local/0000a4b2': {
                'offset_ns': 1234567.0,
                'sigma_ns': 850.0,
                'tier': 'T5',
                'segment_id': 3,
                'rate_ppm': 0.012,
                'rate_sigma_ppm': 0.004,
                'rate_source': 't6-residual',
                'rate_alarm': False,
                'in_violation': False,
            },
            'hf-status.local/00c0ffee': {
                'offset_ns': 1203000000000.0,
                'sigma_ns': 850.0,
                'tier': 'T5',
                'segment_id': 7,
                'rate_ppm': None,
                'rate_alarm': False,
                'in_violation': True,
            },
        },
    }


class LoadOffsetJudgeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'offset_judge.json'

    def _write(self, doc, age_s: float) -> None:
        self.path.write_text(
            doc if isinstance(doc, str) else json.dumps(doc))
        os.utime(self.path, (NOW - age_s, NOW - age_s))

    def test_absent_returns_none(self):
        self.assertIsNone(tj.load_offset_judge(self.path, now=NOW))

    def test_fresh_returns_doc(self):
        self._write(_judge_doc(), age_s=10.0)
        doc = tj.load_offset_judge(self.path, now=NOW)
        self.assertEqual(doc['judge']['tier'], 'T5')

    def test_stale_returns_none(self):
        self._write(_judge_doc(), age_s=tj.JUDGE_FRESH_S + 1.0)
        self.assertIsNone(tj.load_offset_judge(self.path, now=NOW))

    def test_garbage_returns_none(self):
        self._write('{nope', age_s=1.0)
        self.assertIsNone(tj.load_offset_judge(self.path, now=NOW))


class RenderStatusTests(unittest.TestCase):
    def test_healthy_doc_renders_judge_and_sources(self):
        doc = _judge_doc()
        doc['sources'].pop('hf-status.local/00c0ffee')
        lines = tj.render_status_lines(doc)
        levels = [lv for lv, _ in lines]
        texts = [tx for _, tx in lines]
        self.assertEqual(levels, ['ok', 'ok'])
        self.assertIn('judge T5', texts[0])
        self.assertIn('σ=0.8 µs', texts[0])          # 850 ns → µs, 1 dp
        self.assertIn('gpsdo=locked', texts[0])
        self.assertIn('a4b2:', texts[1])             # short ssrc
        self.assertIn('offset +1.235 ms', texts[1])
        self.assertIn('rate +0.012 ppm', texts[1])

    def test_violation_renders_err(self):
        lines = tj.render_status_lines(_judge_doc())
        errs = [tx for lv, tx in lines if lv == 'err']
        self.assertEqual(len(errs), 1)
        self.assertIn('c0ffee:', errs[0])
        self.assertIn('OFFSET VIOLATION', errs[0])

    def test_rate_alarm_renders_err(self):
        doc = _judge_doc()
        doc['sources']['hf-status.local/0000a4b2']['rate_alarm'] = True
        lines = tj.render_status_lines(doc)
        errs = [tx for lv, tx in lines if lv == 'err']
        self.assertTrue(any('RATE ALARM' in t and 'a4b2:' in t
                            for t in errs))

    def test_gpsdo_holdover_warns(self):
        doc = _judge_doc()
        doc['gpsdo_discipline'] = 'holdover'
        lines = tj.render_status_lines(doc)
        self.assertEqual(lines[0][0], 'warn')
        self.assertIn('gpsdo=holdover', lines[0][1])

    def test_no_verdict_warns(self):
        doc = _judge_doc()
        doc['judge'] = {'tier': None, 'sigma_ns': None, 'age_s': None}
        doc['sources'] = {}
        lines = tj.render_status_lines(doc)
        self.assertEqual(lines, [('warn',
                                  'judge has no verdict yet  gpsdo=locked')])

    def test_restart_request_rendered(self):
        lines = tj.render_status_lines(_judge_doc(),
                                       request=_valid_request())
        req_lines = [tx for lv, tx in lines
                     if lv == 'err' and 'restart requested' in tx]
        self.assertEqual(len(req_lines), 1)
        self.assertIn('radiod B4-100', req_lines[0])
        self.assertIn('awaiting site policy', req_lines[0])

    def test_restart_request_shows_honored(self):
        req = _valid_request()
        honored = {'schema': 'radiod-restart-honored-v1',
                   'requested_utc': req['requested_utc']}
        lines = tj.render_status_lines(_judge_doc(), request=req,
                                       honored=honored)
        req_lines = [tx for _, tx in lines if 'restart requested' in tx]
        self.assertIn('honored (radiod restarted)', req_lines[0])


if __name__ == '__main__':
    unittest.main()
