import unittest
from collections import Counter

from srv6_fabric import policy
from srv6_fabric.topo import FlowKey, NUM_PLANES


F = FlowKey("2001:db8:bbbb:00::2", "2001:db8:bbbb:0f::2", 9999, 9999)


class TestRoundRobin(unittest.TestCase):
    def test_strictly_cycles(self):
        p = policy.RoundRobin()
        out = [p.pick(i, F) for i in range(12)]
        self.assertEqual(out, [0, 1, 2, 3] * 3)


class TestHash5Tuple(unittest.TestCase):
    def test_same_flow_same_plane(self):
        p = policy.Hash5Tuple()
        choices = {p.pick(i, F) for i in range(1000)}
        self.assertEqual(len(choices), 1)

    def test_distribution_over_many_flows(self):
        # Use a realistic mix of varying tuple fields; if you only vary one
        # field with strong correlation (e.g. seq i, mod 4) you'll find
        # FNV-1a's low bits track that correlation. Real workloads vary all
        # of src/dst/sport with independent entropy.
        p = policy.Hash5Tuple()
        counts = Counter()
        for i in range(2048):
            flow = FlowKey(
                f"src-{i}", f"dst-{(i * 7) % 97}",
                9000 + (i * 13) % 65535, 9999,
            )
            counts[p.pick(0, flow)] += 1
        for plane in range(NUM_PLANES):
            self.assertGreater(counts[plane], 0)
        # Not strict uniformity — just a sanity floor.
        for plane in range(NUM_PLANES):
            self.assertGreater(counts[plane], 2048 // (NUM_PLANES * 4))


class TestWeighted(unittest.TestCase):
    def test_distribution_tracks_weights(self):
        p = policy.Weighted(weights=(0.4, 0.3, 0.2, 0.1))
        counts = Counter()
        n = 10_000
        for i in range(n):
            counts[p.pick(i, F)] += 1
        # Low-discrepancy sequence — tolerance is tight but not zero.
        self.assertAlmostEqual(counts[0] / n, 0.4, delta=0.02)
        self.assertAlmostEqual(counts[1] / n, 0.3, delta=0.02)
        self.assertAlmostEqual(counts[2] / n, 0.2, delta=0.02)
        self.assertAlmostEqual(counts[3] / n, 0.1, delta=0.02)

    def test_uniform_weights_match_round_robin_roughly(self):
        p = policy.Weighted(weights=(1, 1, 1, 1))
        counts = Counter(p.pick(i, F) for i in range(8000))
        for plane in range(NUM_PLANES):
            self.assertAlmostEqual(counts[plane] / 8000, 0.25, delta=0.02)

    def test_deterministic(self):
        p1 = policy.Weighted(weights=(1, 2, 3, 4))
        p2 = policy.Weighted(weights=(1, 2, 3, 4))
        a = [p1.pick(i, F) for i in range(200)]
        b = [p2.pick(i, F) for i in range(200)]
        self.assertEqual(a, b)

    def test_validation(self):
        with self.assertRaises(ValueError):
            policy.Weighted(weights=(1, 2, 3))           # wrong count
        with self.assertRaises(ValueError):
            policy.Weighted(weights=(1, -1, 1, 1))       # negative
        with self.assertRaises(ValueError):
            policy.Weighted(weights=(0, 0, 0, 0))        # zero sum


class TestHealthAware(unittest.TestCase):
    def test_passthrough_when_no_downs(self):
        inner = policy.RoundRobin()
        wrapped = policy.HealthAware(inner=inner)
        for i in range(16):
            self.assertEqual(wrapped.pick(i, F), inner.pick(i, F))

    def test_skips_down_plane(self):
        wrapped = policy.HealthAware(inner=policy.RoundRobin(), down={2})
        # seq 2 would normally hit plane 2 -> should walk forward to 3.
        self.assertEqual(wrapped.pick(2, F), 3)
        # seq 0..3, plane 2 banned:
        seen = [wrapped.pick(i, F) for i in range(4)]
        self.assertNotIn(2, seen)

    def test_all_down_returns_inner_choice(self):
        wrapped = policy.HealthAware(
            inner=policy.RoundRobin(),
            down=set(range(NUM_PLANES)),
        )
        # Everything down: degrade to inner; don't infinite-loop.
        for i in range(NUM_PLANES):
            self.assertEqual(wrapped.pick(i, F), i % NUM_PLANES)

    def test_three_down_distributes_to_one(self):
        wrapped = policy.HealthAware(
            inner=policy.RoundRobin(),
            down={0, 1, 3},
        )
        seen = {wrapped.pick(i, F) for i in range(16)}
        self.assertEqual(seen, {2})

    def test_mutable_down_updates_live(self):
        wrapped = policy.HealthAware(inner=policy.RoundRobin())
        self.assertEqual(wrapped.pick(2, F), 2)
        wrapped.down.add(2)
        self.assertNotEqual(wrapped.pick(2, F), 2)

    def test_name(self):
        w = policy.HealthAware(inner=policy.RoundRobin())
        self.assertEqual(w.name, "health_aware(round_robin)")


class TestPolicyFromSpec(unittest.TestCase):
    def test_string_forms(self):
        self.assertIsInstance(policy.policy_from_spec("round_robin"),
                              policy.RoundRobin)
        self.assertIsInstance(policy.policy_from_spec("hash5tuple"),
                              policy.Hash5Tuple)

    def test_weighted(self):
        p = policy.policy_from_spec({"weighted": [1, 1, 1, 1]})
        self.assertIsInstance(p, policy.Weighted)

    def test_health_aware_wraps_inner(self):
        p = policy.policy_from_spec({"health_aware": "round_robin"})
        self.assertIsInstance(p, policy.HealthAware)
        self.assertIsInstance(p.inner, policy.RoundRobin)

    def test_health_aware_around_weighted(self):
        p = policy.policy_from_spec(
            {"health_aware": {"weighted": [1, 2, 3, 4]}}
        )
        self.assertIsInstance(p, policy.HealthAware)
        self.assertIsInstance(p.inner, policy.Weighted)

    def test_bad_specs(self):
        with self.assertRaises(ValueError):
            policy.policy_from_spec("nonesuch")
        with self.assertRaises(ValueError):
            policy.policy_from_spec({"weighted": [1]})        # wrong shape


if __name__ == "__main__":
    unittest.main()
