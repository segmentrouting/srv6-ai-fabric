"""Unit tests for srv6_fabric.mrc.ev_state.

Tests are deterministic — no clock, no threads, no sockets. The state
machine is driven entirely by `record_probe_result()` and
`record_loss_window()` calls.
"""

import threading
import unittest

from srv6_fabric.mrc.ev_state import (
    EVState,
    EVStateConfig,
    EVStateTable,
)


def _table(
    num_planes: int = 4,
    tenants=("green", "yellow"),
    cfg: EVStateConfig | None = None,
    on_transition=None,
    lock=None,                # single-threaded tests
) -> EVStateTable:
    return EVStateTable(
        tenants=tenants,
        num_planes=num_planes,
        cfg=cfg or EVStateConfig(),
        on_transition=on_transition,
        lock=lock,
    )


class TestInitialState(unittest.TestCase):
    def test_all_planes_start_unknown(self):
        t = _table()
        for tenant in ("green", "yellow"):
            for p in range(4):
                self.assertIs(t.state(tenant, p), EVState.UNKNOWN)

    def test_min_active_default(self):
        t = _table(num_planes=4)
        self.assertEqual(t.min_active, 2)
        t2 = _table(num_planes=2)
        self.assertEqual(t2.min_active, 1)

    def test_min_active_explicit(self):
        cfg = EVStateConfig(min_active_planes=3)
        t = _table(num_planes=4, cfg=cfg)
        self.assertEqual(t.min_active, 3)

    def test_min_active_clamps_to_num_planes(self):
        cfg = EVStateConfig(min_active_planes=99)
        t = _table(num_planes=4, cfg=cfg)
        self.assertEqual(t.min_active, 4)

    def test_empty_tenants_rejected(self):
        with self.assertRaises(ValueError):
            EVStateTable(tenants=(), num_planes=4, lock=None)

    def test_zero_planes_rejected(self):
        with self.assertRaises(ValueError):
            EVStateTable(tenants=("green",), num_planes=0, lock=None)


class TestProbePath(unittest.TestCase):
    def test_demote_after_threshold_consecutive_timeouts(self):
        t = _table()
        # Bring planes 0,1,2 to GOOD first so we have headroom under the
        # min_active=2 floor.
        for p in (0, 1, 2):
            for _ in range(5):
                t.record_probe_result("green", p, success=True, rtt_ns=1_000_000)
        self.assertIs(t.state("green", 3), EVState.UNKNOWN)
        # 2 timeouts -> still UNKNOWN
        t.record_probe_result("green", 3, success=False)
        t.record_probe_result("green", 3, success=False)
        self.assertIs(t.state("green", 3), EVState.UNKNOWN)
        # 3rd timeout -> ASSUMED_BAD
        t.record_probe_result("green", 3, success=False)
        self.assertIs(t.state("green", 3), EVState.ASSUMED_BAD)

    def test_one_success_resets_timeout_counter(self):
        t = _table()
        # headroom
        for p in (0, 1, 2):
            for _ in range(5):
                t.record_probe_result("green", p, success=True, rtt_ns=1_000_000)
        t.record_probe_result("green", 3, success=False)
        t.record_probe_result("green", 3, success=False)
        t.record_probe_result("green", 3, success=True, rtt_ns=1_000_000)
        # Two more timeouts shouldn't demote (counter was reset).
        t.record_probe_result("green", 3, success=False)
        t.record_probe_result("green", 3, success=False)
        self.assertIs(t.state("green", 3), EVState.UNKNOWN)

    def test_recovery_requires_consecutive_successes(self):
        t = _table()
        for p in (0, 1, 2):
            for _ in range(5):
                t.record_probe_result("green", p, success=True, rtt_ns=1_000_000)
        for _ in range(3):
            t.record_probe_result("green", 3, success=False)
        self.assertIs(t.state("green", 3), EVState.ASSUMED_BAD)
        # 4 successes — not enough (threshold=5).
        for _ in range(4):
            t.record_probe_result("green", 3, success=True, rtt_ns=1_000_000)
        self.assertIs(t.state("green", 3), EVState.ASSUMED_BAD)
        # 5th tips it.
        t.record_probe_result("green", 3, success=True, rtt_ns=1_000_000)
        self.assertIs(t.state("green", 3), EVState.GOOD)

    def test_recovery_blocked_by_recent_loss_demote(self):
        t = _table()
        for p in (0, 1, 2):
            for _ in range(5):
                t.record_probe_result("green", p, success=True, rtt_ns=1_000_000)
        # Loss-feedback path demotes plane 3.
        t.record_loss_window("green", 3, seen=900, expected=1000)
        t.record_loss_window("green", 3, seen=900, expected=1000)
        self.assertIs(t.state("green", 3), EVState.ASSUMED_BAD)
        # Probes pass cleanly but loss-demote-counter is still non-zero,
        # so recovery must NOT fire.
        for _ in range(10):
            t.record_probe_result("green", 3, success=True, rtt_ns=1_000_000)
        self.assertIs(t.state("green", 3), EVState.ASSUMED_BAD)
        # Now a clean loss window: zeros the loss counter.
        t.record_loss_window("green", 3, seen=1000, expected=1000)
        # One more probe success crosses the recover threshold (we already
        # had 10 in a row; next success won't add since counter was reset
        # on demote... actually it wasn't, only timeouts were. So the next
        # probe success should immediately tip).
        # Sanity: after the loss-quiet window, the very next probe pass
        # checks the gate and since successes >= threshold, GOOD.
        t.record_probe_result("green", 3, success=True, rtt_ns=1_000_000)
        self.assertIs(t.state("green", 3), EVState.GOOD)

    def test_success_requires_rtt(self):
        t = _table()
        with self.assertRaises(ValueError):
            t.record_probe_result("green", 0, success=True, rtt_ns=None)

    def test_negative_rtt_rejected(self):
        t = _table()
        with self.assertRaises(ValueError):
            t.record_probe_result("green", 0, success=True, rtt_ns=-1)


class TestLossPath(unittest.TestCase):
    def test_demote_on_two_consecutive_bad_windows(self):
        t = _table()
        for p in (0, 1, 2):
            for _ in range(5):
                t.record_probe_result("green", p, success=True, rtt_ns=1_000_000)
        # 10% loss > 5% threshold; one window not enough.
        t.record_loss_window("green", 3, seen=900, expected=1000)
        self.assertIs(t.state("green", 3), EVState.UNKNOWN)
        # Second consecutive bad window -> demote.
        t.record_loss_window("green", 3, seen=900, expected=1000)
        self.assertIs(t.state("green", 3), EVState.ASSUMED_BAD)

    def test_loss_below_threshold_does_not_demote(self):
        t = _table()
        for p in (0, 1, 2):
            for _ in range(5):
                t.record_probe_result("green", p, success=True, rtt_ns=1_000_000)
        # 1% loss < 5% threshold AND < threshold/2 = 2.5% (so this counts
        # as a quiet window).
        for _ in range(10):
            t.record_loss_window("green", 3, seen=990, expected=1000)
        self.assertIs(t.state("green", 3), EVState.UNKNOWN)

    def test_mild_loss_neither_demotes_nor_clears(self):
        # ratio in (loss_threshold/2, loss_threshold] = (2.5%, 5%]:
        # ambiguous — neither demote evidence nor recovery evidence.
        t = _table()
        for p in (0, 1, 2):
            for _ in range(5):
                t.record_probe_result("green", p, success=True, rtt_ns=1_000_000)
        # Prime one bad window:
        t.record_loss_window("green", 3, seen=900, expected=1000)
        # Mild window: counter neither increments nor resets.
        t.record_loss_window("green", 3, seen=970, expected=1000)
        self.assertIs(t.state("green", 3), EVState.UNKNOWN)
        # Bad window again: total bad windows = 2 -> demote.
        t.record_loss_window("green", 3, seen=900, expected=1000)
        self.assertIs(t.state("green", 3), EVState.ASSUMED_BAD)

    def test_expected_zero_is_noop(self):
        t = _table()
        t.record_loss_window("green", 3, seen=0, expected=0)
        self.assertIs(t.state("green", 3), EVState.UNKNOWN)

    def test_seen_exceeds_expected_clamped(self):
        # Late reorders can produce seen > expected. We clamp to expected
        # so ratio is 0 (quiet) rather than raising.
        t = _table()
        t.record_loss_window("green", 3, seen=1100, expected=1000)
        # No exception; state still UNKNOWN; treated as fully-clean window.
        self.assertIs(t.state("green", 3), EVState.UNKNOWN)

    def test_negative_inputs_rejected(self):
        t = _table()
        with self.assertRaises(ValueError):
            t.record_loss_window("green", 3, seen=-1, expected=10)
        with self.assertRaises(ValueError):
            t.record_loss_window("green", 3, seen=10, expected=-1)


class TestMinActiveFloor(unittest.TestCase):
    def test_floor_suppresses_demote(self):
        # min_active=2: with 2 already ASSUMED_BAD, the third demote is
        # suppressed.
        t = _table(num_planes=4)
        # Demote planes 0 and 1 via probe timeouts.
        for p in (0, 1):
            for _ in range(3):
                t.record_probe_result("green", p, success=False)
        self.assertIs(t.state("green", 0), EVState.ASSUMED_BAD)
        self.assertIs(t.state("green", 1), EVState.ASSUMED_BAD)
        # Try to demote plane 2 — only planes 3 would remain "usable"
        # (UNKNOWN counts toward usable). That's 1 < min_active=2, so
        # suppress.
        for _ in range(3):
            t.record_probe_result("green", 2, success=False)
        self.assertIs(t.state("green", 2), EVState.UNKNOWN)
        snap = t.snapshot()
        plane2 = next(
            x for x in snap["tenants"]["green"] if x["plane"] == 2
        )
        self.assertGreaterEqual(plane2["demotes_suppressed_by_floor"], 1)

    def test_floor_suppresses_loss_path_too(self):
        t = _table(num_planes=4)
        for p in (0, 1):
            for _ in range(3):
                t.record_probe_result("green", p, success=False)
        for _ in range(2):
            t.record_loss_window("green", 2, seen=900, expected=1000)
        self.assertIs(t.state("green", 2), EVState.UNKNOWN)

    def test_floor_with_higher_min_active(self):
        cfg = EVStateConfig(min_active_planes=3)
        t = _table(num_planes=4, cfg=cfg)
        # Demote plane 0. Three usable (1, 2, 3) remain = floor met.
        for _ in range(3):
            t.record_probe_result("green", 0, success=False)
        self.assertIs(t.state("green", 0), EVState.ASSUMED_BAD)
        # Demote plane 1. After demote, usable=2 < floor=3 -> suppress.
        for _ in range(3):
            t.record_probe_result("green", 1, success=False)
        self.assertIs(t.state("green", 1), EVState.UNKNOWN)


class TestWeights(unittest.TestCase):
    def test_initial_weights_sum_to_one(self):
        t = _table(num_planes=4)
        w = t.weights("green")
        self.assertEqual(len(w), 4)
        self.assertAlmostEqual(sum(w), 1.0)
        # All UNKNOWN -> uniform.
        for x in w:
            self.assertAlmostEqual(x, 0.25)

    def test_good_planes_dominate(self):
        t = _table(num_planes=4)
        for _ in range(5):
            t.record_probe_result("green", 0, success=True, rtt_ns=1_000_000)
            t.record_probe_result("green", 1, success=True, rtt_ns=1_000_000)
        # Planes 0,1 GOOD (weight 1.0 each); planes 2,3 UNKNOWN (0.5 each).
        # Total = 3.0 -> 0.333... / 0.333... / 0.166... / 0.166...
        w = t.weights("green")
        self.assertAlmostEqual(w[0], 1.0 / 3.0, places=6)
        self.assertAlmostEqual(w[1], 1.0 / 3.0, places=6)
        self.assertAlmostEqual(w[2], 1.0 / 6.0, places=6)
        self.assertAlmostEqual(w[3], 1.0 / 6.0, places=6)

    def test_bad_planes_get_zero_weight(self):
        t = _table(num_planes=4)
        # Make 0,1 GOOD so there's headroom to demote plane 2 under the
        # min_active=2 floor.
        for _ in range(5):
            t.record_probe_result("green", 0, success=True, rtt_ns=1_000_000)
            t.record_probe_result("green", 1, success=True, rtt_ns=1_000_000)
        for _ in range(3):
            t.record_probe_result("green", 2, success=False)
        self.assertIs(t.state("green", 2), EVState.ASSUMED_BAD)
        w = t.weights("green")
        self.assertEqual(w[2], 0.0)
        self.assertAlmostEqual(sum(w), 1.0)

    def test_all_bad_degrades_to_uniform(self):
        # Force every plane bad by disabling the floor. min_active=1
        # would let 3 of 4 fall; min_active=0 isn't legal, so we use a
        # single-plane table and demote it.
        t = _table(num_planes=1)
        # min_active = max(1, 1//2) = 1, so we can't actually demote here
        # via the normal path. Use a 2-plane table with min_active=1
        # instead; demote one of two -> still 1 usable -> floor=1 ok.
        # Then demote the other; floor=1 means: usable_after = 0 < 1 ->
        # suppress. So the "all bad" case is unreachable via the floor
        # logic — which is exactly the point of the floor. We instead
        # cover the safety-net branch directly by constructing a table
        # with min_active=0 (illegal per resolve_min_active clamp) ...
        # actually resolve_min_active clamps to >= 1. So the all-bad
        # path is truly unreachable through normal API.
        # Cover the weights-cache fallback by direct internal mutation:
        # set all states to ASSUMED_BAD and rebuild.
        t2 = _table(num_planes=4)
        for tenant_planes in t2._planes.values():  # noqa: SLF001
            for rec in tenant_planes:
                rec.state = EVState.ASSUMED_BAD
        # Rebuild via a no-op transition wouldn't fire; call directly.
        t2._rebuild_weights_locked("green")        # noqa: SLF001
        w = t2.weights("green")
        for x in w:
            self.assertAlmostEqual(x, 0.25)


class TestGoodPlanes(unittest.TestCase):
    def test_good_set(self):
        t = _table(num_planes=4)
        for _ in range(5):
            t.record_probe_result("green", 0, success=True, rtt_ns=1_000_000)
            t.record_probe_result("green", 2, success=True, rtt_ns=1_000_000)
        self.assertEqual(t.good_planes("green"), frozenset({0, 2}))


class TestCallbacks(unittest.TestCase):
    def test_on_transition_fires_with_old_and_new(self):
        events = []
        t = _table(
            num_planes=4,
            on_transition=lambda tenant, plane, old, new:
                events.append((tenant, plane, old, new)),
        )
        for _ in range(5):
            t.record_probe_result("green", 0, success=True, rtt_ns=1_000_000)
        self.assertEqual(events, [("green", 0, EVState.UNKNOWN, EVState.GOOD)])

    def test_no_transition_no_event(self):
        events = []
        t = _table(on_transition=lambda *a: events.append(a))
        # Below threshold -> no transition.
        t.record_probe_result("green", 0, success=True, rtt_ns=1_000_000)
        t.record_probe_result("green", 0, success=True, rtt_ns=1_000_000)
        self.assertEqual(events, [])


class TestRttRing(unittest.TestCase):
    def test_p50_and_p99(self):
        t = _table()
        for rtt in [1_000_000, 2_000_000, 3_000_000, 4_000_000, 5_000_000]:
            t.record_probe_result("green", 0, success=True, rtt_ns=rtt)
        self.assertEqual(t.rtt_p50_ns("green", 0), 3_000_000)
        self.assertEqual(t.rtt_p99_ns("green", 0), 5_000_000)

    def test_none_when_empty(self):
        t = _table()
        self.assertIsNone(t.rtt_p50_ns("green", 0))
        self.assertIsNone(t.rtt_p99_ns("green", 0))

    def test_ring_bounded(self):
        cfg = EVStateConfig(rtt_ring_size=4)
        t = _table(cfg=cfg)
        for i in range(100):
            t.record_probe_result("green", 0, success=True, rtt_ns=i)
        # Only the last 4 samples remain.
        ring = t._planes["green"][0].rtt_ring_ns         # noqa: SLF001
        self.assertEqual(list(ring), [96, 97, 98, 99])


class TestSnapshot(unittest.TestCase):
    def test_snapshot_shape(self):
        t = _table()
        for _ in range(5):
            t.record_probe_result("green", 0, success=True, rtt_ns=1_000_000)
        snap = t.snapshot()
        self.assertIn("config", snap)
        self.assertIn("tenants", snap)
        self.assertIn("green", snap["tenants"])
        self.assertEqual(len(snap["tenants"]["green"]), 4)
        plane0 = snap["tenants"]["green"][0]
        self.assertEqual(plane0["plane"], 0)
        self.assertEqual(plane0["state"], "good")
        self.assertEqual(plane0["transitions"], 1)
        self.assertEqual(plane0["weight"], 1.0 / 2.5)  # 1.0 / (1.0+0.5+0.5+0.5)


class TestInputValidation(unittest.TestCase):
    def test_unknown_tenant(self):
        t = _table()
        with self.assertRaises(ValueError):
            t.state("purple", 0)

    def test_plane_out_of_range(self):
        t = _table(num_planes=4)
        with self.assertRaises(ValueError):
            t.state("green", 4)
        with self.assertRaises(ValueError):
            t.state("green", -1)


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_writes_dont_lose_transitions(self):
        # Smoke test: many threads pounding the same plane shouldn't
        # corrupt the state machine. We can't assert ordering, only that
        # the final state is consistent and total transitions are bounded.
        t = EVStateTable(
            tenants=("green",), num_planes=4,
            cfg=EVStateConfig(),
        )

        def loop_demote(plane):
            for _ in range(100):
                t.record_probe_result("green", plane, success=False)

        threads = [
            threading.Thread(target=loop_demote, args=(0,)) for _ in range(8)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        # Plane 0 must have ended up ASSUMED_BAD (we sent 800 timeouts
        # under min_active=2 with planes 1,2,3 still UNKNOWN-usable).
        self.assertIs(t.state("green", 0), EVState.ASSUMED_BAD)


if __name__ == "__main__":
    unittest.main()
