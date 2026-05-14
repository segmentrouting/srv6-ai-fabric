import unittest

from srv6_fabric.mrc import scenario


# --- minimal valid scenario fixture ----------------------------------------

MINIMAL = {
    "name": "smoke",
    "flows": [
        {
            "pairs": "green-pairs-8",
            "policy": "round_robin",
            "rate": "1000pps",
            "duration": "5s",
        }
    ],
}


class TestMinimalValidation(unittest.TestCase):
    def test_smoke_validates(self):
        s = scenario.validate(MINIMAL)
        self.assertEqual(s.name, "smoke")
        self.assertEqual(s.description, "")
        self.assertEqual(len(s.flows), 1)
        self.assertEqual(len(s.flows[0].pairs), 8)
        self.assertEqual(s.flows[0].rate_pps, 1000)
        self.assertEqual(s.flows[0].duration_s, 5.0)
        self.assertEqual(s.flows[0].policy_label, "round_robin")
        self.assertEqual(s.faults, ())
        self.assertIsNone(s.report.out)


class TestRequiredKeys(unittest.TestCase):
    def test_missing_name(self):
        doc = {**MINIMAL}
        del doc["name"]
        with self.assertRaises(scenario.ScenarioError) as cm:
            scenario.validate(doc)
        self.assertIn("$", cm.exception.path)

    def test_missing_flows(self):
        doc = {"name": "x"}
        with self.assertRaises(scenario.ScenarioError):
            scenario.validate(doc)

    def test_unknown_top_level_key(self):
        doc = {**MINIMAL, "paris": []}  # the classic typo
        with self.assertRaises(scenario.ScenarioError) as cm:
            scenario.validate(doc)
        self.assertIn("paris", str(cm.exception))

    def test_top_level_must_be_mapping(self):
        with self.assertRaises(scenario.ScenarioError):
            scenario.validate([])
        with self.assertRaises(scenario.ScenarioError):
            scenario.validate("hello")


# --- pairs -----------------------------------------------------------------

class TestPairs(unittest.TestCase):
    def test_named_set_resolves(self):
        s = scenario.validate(MINIMAL)
        flow = s.flows[0]
        # First pair should be (green, 0, 15) — known reference table
        self.assertEqual(flow.pairs[0].tenant, "green")
        self.assertEqual(flow.pairs[0].src, 0)
        self.assertEqual(flow.pairs[0].dst, 15)

    def test_unknown_named_set(self):
        doc = {**MINIMAL,
               "flows": [{**MINIMAL["flows"][0], "pairs": "purple-pairs-99"}]}
        with self.assertRaises(scenario.ScenarioError) as cm:
            scenario.validate(doc)
        self.assertIn("pairs", cm.exception.path)

    def test_explicit_pair_list(self):
        doc = {
            **MINIMAL,
            "flows": [{
                **MINIMAL["flows"][0],
                "pairs": [{"tenant": "green", "src": 0, "dst": 15}],
            }],
        }
        s = scenario.validate(doc)
        self.assertEqual(len(s.flows[0].pairs), 1)
        self.assertEqual(s.flows[0].pairs[0].dst_host(), "green-host15")

    def test_empty_pair_list(self):
        doc = {**MINIMAL,
               "flows": [{**MINIMAL["flows"][0], "pairs": []}]}
        with self.assertRaises(scenario.ScenarioError):
            scenario.validate(doc)

    def test_src_equals_dst_rejected(self):
        doc = {**MINIMAL,
               "flows": [{**MINIMAL["flows"][0],
                          "pairs": [{"tenant": "green", "src": 5, "dst": 5}]}]}
        with self.assertRaises(scenario.ScenarioError):
            scenario.validate(doc)

    def test_bad_tenant(self):
        doc = {**MINIMAL,
               "flows": [{**MINIMAL["flows"][0],
                          "pairs": [{"tenant": "blue", "src": 0, "dst": 1}]}]}
        with self.assertRaises(scenario.ScenarioError):
            scenario.validate(doc)

    def test_bad_host_id(self):
        doc = {**MINIMAL,
               "flows": [{**MINIMAL["flows"][0],
                          "pairs": [{"tenant": "green", "src": 0, "dst": 16}]}]}
        with self.assertRaises(scenario.ScenarioError):
            scenario.validate(doc)


# --- policy ----------------------------------------------------------------

class TestPolicy(unittest.TestCase):
    def test_hash5tuple(self):
        doc = {**MINIMAL,
               "flows": [{**MINIMAL["flows"][0], "policy": "hash5tuple"}]}
        s = scenario.validate(doc)
        self.assertEqual(s.flows[0].policy_label, "hash5tuple")

    def test_weighted(self):
        doc = {**MINIMAL,
               "flows": [{**MINIMAL["flows"][0],
                          "policy": {"weighted": [1, 1, 1, 1]}}]}
        s = scenario.validate(doc)
        self.assertTrue(s.flows[0].policy_label.startswith("weighted"))

    def test_health_aware(self):
        doc = {**MINIMAL,
               "flows": [{**MINIMAL["flows"][0],
                          "policy": {"health_aware": "round_robin"}}]}
        s = scenario.validate(doc)
        self.assertIn("health_aware", s.flows[0].policy_label)

    def test_unknown_policy(self):
        doc = {**MINIMAL,
               "flows": [{**MINIMAL["flows"][0], "policy": "magic"}]}
        with self.assertRaises(scenario.ScenarioError):
            scenario.validate(doc)


# --- rate / duration -------------------------------------------------------

class TestRateDuration(unittest.TestCase):
    def test_rate_forms(self):
        for v in (1000, "1000", "1000pps", "1000 pps"):
            with self.subTest(rate=v):
                doc = {**MINIMAL,
                       "flows": [{**MINIMAL["flows"][0], "rate": v}]}
                s = scenario.validate(doc)
                self.assertEqual(s.flows[0].rate_pps, 1000)

    def test_bad_rate(self):
        for v in (0, -5, "fast", "1000pppppps", None):
            with self.subTest(rate=v):
                doc = {**MINIMAL,
                       "flows": [{**MINIMAL["flows"][0], "rate": v}]}
                with self.assertRaises(scenario.ScenarioError):
                    scenario.validate(doc)

    def test_duration_forms(self):
        cases = {"5s": 5.0, "500ms": 0.5, "1.5s": 1.5, 10: 10.0, 0.25: 0.25}
        for raw, expected in cases.items():
            with self.subTest(duration=raw):
                doc = {**MINIMAL,
                       "flows": [{**MINIMAL["flows"][0], "duration": raw}]}
                s = scenario.validate(doc)
                self.assertEqual(s.flows[0].duration_s, expected)

    def test_bad_duration(self):
        for v in (0, -1, "5x", "forever", None):
            with self.subTest(duration=v):
                doc = {**MINIMAL,
                       "flows": [{**MINIMAL["flows"][0], "duration": v}]}
                with self.assertRaises(scenario.ScenarioError):
                    scenario.validate(doc)


# --- faults ----------------------------------------------------------------

class TestFaults(unittest.TestCase):
    def test_valid_fault(self):
        doc = {
            **MINIMAL,
            "faults": [
                {"kind": "netem", "target": "plane 2", "spec": "loss 5%"},
            ],
        }
        s = scenario.validate(doc)
        self.assertEqual(len(s.faults), 1)
        self.assertEqual(s.faults[0].target, "plane 2")
        self.assertEqual(s.faults[0].spec, "loss 5%")

    def test_blackhole_spec(self):
        doc = {**MINIMAL,
               "faults": [{"kind": "netem", "target": "plane 0",
                           "spec": "blackhole"}]}
        s = scenario.validate(doc)
        self.assertEqual(s.faults[0].spec, "blackhole")

    def test_bad_kind(self):
        doc = {**MINIMAL,
               "faults": [{"kind": "bgp-shutdown", "target": "plane 0",
                           "spec": "loss 5%"}]}
        with self.assertRaises(scenario.ScenarioError):
            scenario.validate(doc)

    def test_bad_target_caught_early(self):
        doc = {**MINIMAL,
               "faults": [{"kind": "netem", "target": "plane 99",
                           "spec": "loss 5%"}]}
        with self.assertRaises(scenario.ScenarioError):
            scenario.validate(doc)

    def test_bad_spec_caught_early(self):
        doc = {**MINIMAL,
               "faults": [{"kind": "netem", "target": "plane 0",
                           "spec": "rm -rf /"}]}
        with self.assertRaises(scenario.ScenarioError):
            scenario.validate(doc)

    def test_unknown_key_in_fault(self):
        doc = {**MINIMAL,
               "faults": [{"kind": "netem", "target": "plane 0",
                           "spec": "loss 1%", "duration": "30s"}]}
        with self.assertRaises(scenario.ScenarioError):
            scenario.validate(doc)


# --- report ----------------------------------------------------------------

class TestReport(unittest.TestCase):
    def test_default_when_omitted(self):
        s = scenario.validate(MINIMAL)
        self.assertIsNone(s.report.out)

    def test_explicit_out(self):
        doc = {**MINIMAL, "report": {"out": "results/x.json"}}
        s = scenario.validate(doc)
        self.assertEqual(s.report.out, "results/x.json")

    def test_unknown_key(self):
        doc = {**MINIMAL, "report": {"format": "json"}}
        with self.assertRaises(scenario.ScenarioError):
            scenario.validate(doc)


# --- yaml roundtrip --------------------------------------------------------

class TestYamlLoading(unittest.TestCase):
    def test_from_yaml_string(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not available")
        text = """
name: smoke
description: basic
flows:
  - pairs: green-pairs-8
    policy: round_robin
    rate: 1000pps
    duration: 5s
faults:
  - kind: netem
    target: plane 2
    spec: loss 5%
report:
  out: results/x.json
"""
        s = scenario.from_yaml_string(text)
        self.assertEqual(s.name, "smoke")
        self.assertEqual(s.description, "basic")
        self.assertEqual(len(s.faults), 1)


if __name__ == "__main__":
    unittest.main()
