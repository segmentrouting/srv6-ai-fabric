"""Tests for mrc.lib.health — K-of-N down threshold + recovery + HealthAware integration."""
import threading
import time
import unittest

from mrc.lib.health import HealthMonitor
from mrc.lib.policy import HealthAware, RoundRobin
from mrc.lib.topo import FlowKey


def _make_probe(results: dict[int, list[bool]]):
    """Build a deterministic probe that returns results[plane].pop(0) on
    each call. If a plane's list is exhausted, defaults to True (healthy)
    so partially-specified tests don't accidentally drive plane down.
    """
    def probe(plane: int, timeout_s: float) -> bool:
        seq = results.get(plane, [])
        return seq.pop(0) if seq else True
    return probe


class TestHealthMonitorThreshold(unittest.TestCase):

    def test_one_failure_does_not_mark_down_with_threshold_3(self):
        down: set[int] = set()
        probe = _make_probe({1: [False]})
        hm = HealthMonitor(down, probe, threshold=3, recovery=1)
        hm.tick()
        self.assertEqual(down, set())

    def test_three_consecutive_failures_mark_down(self):
        down: set[int] = set()
        probe = _make_probe({1: [False, False, False]})
        hm = HealthMonitor(down, probe, threshold=3, recovery=1)
        hm.tick(); hm.tick(); hm.tick()
        self.assertEqual(down, {1})

    def test_failure_streak_resets_on_success(self):
        # F F P F — should NOT mark down (streak broken).
        down: set[int] = set()
        probe = _make_probe({2: [False, False, True, False]})
        hm = HealthMonitor(down, probe, threshold=3, recovery=1)
        for _ in range(4):
            hm.tick()
        self.assertEqual(down, set())

    def test_threshold_one_marks_down_on_first_failure(self):
        down: set[int] = set()
        probe = _make_probe({3: [False]})
        hm = HealthMonitor(down, probe, threshold=1, recovery=1)
        hm.tick()
        self.assertEqual(down, {3})

    def test_threshold_must_be_positive(self):
        with self.assertRaises(ValueError):
            HealthMonitor(set(), lambda p, t: True, threshold=0)


class TestHealthMonitorRecovery(unittest.TestCase):

    def test_immediate_recovery_default(self):
        # Mark plane 0 down, then one success brings it back (recovery=1).
        down: set[int] = {0}
        probe = _make_probe({0: [True]})
        hm = HealthMonitor(down, probe, threshold=3, recovery=1)
        hm.tick()
        self.assertEqual(down, set())

    def test_recovery_threshold_two(self):
        # With recovery=2, a single success is not enough.
        down: set[int] = {0}
        probe = _make_probe({0: [True]})
        hm = HealthMonitor(down, probe, threshold=3, recovery=2)
        hm.tick()
        self.assertEqual(down, {0})
        # Second success recovers.
        probe2 = _make_probe({0: [True, True]})
        hm2 = HealthMonitor(down, probe2, threshold=3, recovery=2)
        hm2.tick(); hm2.tick()
        self.assertEqual(down, set())

    def test_pass_streak_resets_on_failure(self):
        down: set[int] = {0}
        # recovery=2: P F P — should NOT recover.
        probe = _make_probe({0: [True, False, True]})
        hm = HealthMonitor(down, probe, threshold=3, recovery=2)
        for _ in range(3):
            hm.tick()
        self.assertEqual(down, {0})

    def test_recovery_must_be_positive(self):
        with self.assertRaises(ValueError):
            HealthMonitor(set(), lambda p, t: True, recovery=0)


class TestHealthMonitorMultiPlane(unittest.TestCase):

    def test_independent_per_plane(self):
        # plane 0: 3 fails → down. plane 1: stays up.
        down: set[int] = set()
        probe = _make_probe({0: [False, False, False]})
        hm = HealthMonitor(down, probe, threshold=3, recovery=1)
        hm.tick(); hm.tick(); hm.tick()
        self.assertEqual(down, {0})

    def test_recovery_does_not_affect_other_planes(self):
        down: set[int] = {0, 2}
        # plane 0 succeeds → recovers. plane 2 fails → stays down.
        probe = _make_probe({0: [True], 2: [False]})
        hm = HealthMonitor(down, probe, threshold=3, recovery=1)
        hm.tick()
        self.assertEqual(down, {2})  # plane 0 recovered, plane 2 stays down


class TestHealthAwareIntegration(unittest.TestCase):

    def test_health_monitor_drives_health_aware_policy(self):
        # End-to-end: HealthMonitor mutates the shared down set; HealthAware
        # picks an alternate plane. This is the only contract that matters.
        wrapped = HealthAware(inner=RoundRobin())
        down = wrapped.down
        flow = FlowKey("a", "b", 1, 2)

        # Initially all healthy → round-robin.
        picks = [wrapped.pick(s, flow) for s in range(4)]
        self.assertEqual(picks, [0, 1, 2, 3])

        # Drive plane 1 down via the monitor.
        probe = _make_probe({1: [False, False, False]})
        hm = HealthMonitor(down, probe, threshold=3, recovery=1)
        hm.tick(); hm.tick(); hm.tick()
        self.assertIn(1, down)

        # round-robin would pick plane 1 at seq=1; HealthAware skips to 2.
        self.assertEqual(wrapped.pick(1, flow), 2)


class TestHealthMonitorLifecycle(unittest.TestCase):

    def test_start_and_stop(self):
        down: set[int] = set()
        # Fast interval so multiple ticks happen during the sleep.
        calls = {"n": 0}
        lock = threading.Lock()
        def probe(plane: int, timeout_s: float) -> bool:
            with lock:
                calls["n"] += 1
            return True

        hm = HealthMonitor(down, probe, threshold=3, recovery=1,
                           interval_s=0.02)
        hm.start()
        time.sleep(0.15)   # ~7 intervals worth
        hm.stop(join_timeout_s=1.0)
        self.assertGreater(calls["n"], 4)   # at least a couple of ticks happened

    def test_double_start_rejected(self):
        hm = HealthMonitor(set(), lambda p, t: True, interval_s=10)
        hm.start()
        try:
            with self.assertRaises(RuntimeError):
                hm.start()
        finally:
            hm.stop()

    def test_stop_without_start_is_noop(self):
        hm = HealthMonitor(set(), lambda p, t: True)
        hm.stop()  # should not raise


class TestHealthMonitorStatus(unittest.TestCase):

    def test_last_status_reports_up_down(self):
        down: set[int] = {1, 3}
        hm = HealthMonitor(down, lambda p, t: True)
        self.assertEqual(
            hm.last_status(),
            {0: "up", 1: "down", 2: "up", 3: "down"},
        )


if __name__ == "__main__":
    unittest.main()
