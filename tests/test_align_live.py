import inspect
import unittest

from sigmond import align_live as L


class StaleTests(unittest.TestCase):
    def test_own_checkout_moved_after_start_is_stale(self):
        out = L.stale_components({"codar-sounder"}, {"codar-sounder": 200.0},
                                 {"codar-sounder": 100.0}, {})
        self.assertEqual(out, {"codar-sounder": "its checkout moved after it started"})

    def test_moved_before_start_is_not_stale(self):
        self.assertEqual(L.stale_components({"a"}, {"a": 100.0}, {"a": 200.0}, {}), {})

    def test_moved_at_same_second_is_not_stale(self):
        self.assertEqual(L.stale_components({"a"}, {"a": 100.0}, {"a": 100.0}, {}), {})

    def test_consumed_library_moved_after_start_is_stale(self):
        out = L.stale_components({"psk-recorder"}, {"ka9q-python": 300.0, "psk-recorder": 50.0},
                                 {"psk-recorder": 100.0}, {"psk-recorder": {"ka9q-python"}})
        self.assertEqual(out, {"psk-recorder": "ka9q-python moved after it started"})

    def test_both_reasons_are_named(self):
        out = L.stale_components({"x"}, {"x": 300.0, "lib": 300.0}, {"x": 100.0}, {"x": {"lib"}})
        self.assertEqual(out, {"x": "its checkout moved; lib moved after it started"})

    def test_not_running_is_never_stale(self):
        self.assertEqual(L.stale_components({"a"}, {"a": 300.0}, {"a": None}, {}), {})

    def test_unknown_move_time_is_not_stale(self):
        self.assertEqual(L.stale_components({"a"}, {}, {"a": 100.0}, {}), {})

    def test_only_services_are_considered(self):
        self.assertEqual(L.stale_components(set(), {"ka9q-python": 300.0},
                                            {"ka9q-python": 100.0}, {}), {})


class PredictedTests(unittest.TestCase):
    def test_moving_component_and_consumers_of_moving_library(self):
        got = L.predicted_restarts(
            moving={"codar-sounder", "ka9q-python"},
            running={"codar-sounder", "psk-recorder", "hf-timestd", "radiod"},
            consumes={"psk-recorder": {"ka9q-python"}, "hf-timestd": {"hamsci-dsp"}},
            radiod_consumers={"psk-recorder", "hf-timestd", "codar-sounder"})
        self.assertEqual(got, (False, ["codar-sounder", "psk-recorder"]))

    def test_ka9q_radio_moving_restarts_radiod_and_every_running_consumer(self):
        got = L.predicted_restarts(
            moving={L.RADIOD}, running={L.RADIOD, "psk-recorder", "hf-timestd"},
            consumes={}, radiod_consumers={"psk-recorder", "hf-timestd", "wspr-recorder"})
        self.assertEqual(got, (True, ["hf-timestd", "psk-recorder"]))

    def test_predicted_uses_the_topology_key(self):
        got = L.predicted_restarts(
            moving={"ka9q-radio"}, running={"ka9q-radio", "psk-recorder"},
            consumes={}, radiod_consumers={"psk-recorder"})
        self.assertEqual(got, (True, ["psk-recorder"]))


class GuardTests(unittest.TestCase):
    def test_align_live_never_imports_subprocess(self):
        self.assertNotIn("subprocess", inspect.getsource(L))
