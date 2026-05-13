"""Tests for mrc.lib.report — merge logic between sender/receiver records."""
import unittest

from mrc.lib.report import FlowRow, ScenarioReport


def _sender(src="green-host00", dst="green-host15", tenant="green",
            policy="round_robin", spine=0, sent=4000, **kw):
    base = {
        "src": src, "dst": dst, "tenant": tenant, "policy": policy,
        "spine": spine, "rate_pps": 1000, "duration_s": 4.0,
        "sent": sent, "elapsed_s": 4.001,
        "per_plane_sent": {0: 1000, 1: 1000, 2: 1000, 3: 1000},
        "errors": 0,
    }
    base.update(kw)
    return base


def _recv_flow(src_addr, dst_addr, rx=1000, loss=0, **kw):
    base = {
        "src_addr": src_addr, "dst_addr": dst_addr,
        "src_port": 9999, "dst_port": 9999,
        "rx": rx, "loss": loss, "dup": 0,
        "reordered": 0,
        "max_reorder_distance": 0,
        "mean_reorder_distance": 0.0,
        "p99_reorder_distance": 0,
        "per_plane_rx": {0: rx // 4, 1: rx // 4, 2: rx // 4, 3: rx // 4},
    }
    base.update(kw)
    return base


def _receiver(host="green-host15", tenant="green",
              flows=None, per_nic=None, per_plane=None):
    return {
        "host": host, "self_id": int(host[-2:]),
        "tenant": tenant,
        "per_nic": per_nic or {f"eth{i+1}": 1000 for i in range(4)},
        "per_plane": per_plane or {0: 1000, 1: 1000, 2: 1000, 3: 1000},
        "flows": flows or [],
    }


class TestMatchingHappyPath(unittest.TestCase):

    def test_single_flow_matched(self):
        s = _sender()
        r = _receiver(flows=[_recv_flow(
            "2001:db8:bbbb:00::2", "2001:db8:bbbb:0f::2",
            rx=4000,
        )])
        rep = ScenarioReport.from_records("baseline", [s], [r])
        self.assertEqual(len(rep.flows), 1)
        f = rep.flows[0]
        self.assertEqual(f.sent, 4000)
        self.assertEqual(f.rx, 4000)
        self.assertEqual(f.loss, 0)
        self.assertEqual(f.loss_pct(), 0.0)
        self.assertEqual(rep.warnings, [])

    def test_yellow_tenant_addresses_resolve(self):
        s = _sender(src="yellow-host03", dst="yellow-host12", tenant="yellow")
        r = _receiver(host="yellow-host12", tenant="yellow",
                      flows=[_recv_flow(
                          "2001:db8:cccd:03::1", "2001:db8:cccd:0c::1",
                          rx=4000,
                      )])
        rep = ScenarioReport.from_records("baseline", [s], [r])
        self.assertEqual(len(rep.flows), 1)
        self.assertEqual(rep.flows[0].rx, 4000)
        self.assertEqual(rep.warnings, [])


class TestMissingReceiver(unittest.TestCase):

    def test_sender_with_no_matching_receiver_record(self):
        s = _sender()
        rep = ScenarioReport.from_records("x", [s], [])
        self.assertEqual(len(rep.flows), 1)
        self.assertIsNone(rep.flows[0].rx)
        self.assertTrue(any("no receiver record" in n
                            for n in rep.flows[0].notes))

    def test_receiver_present_but_no_matching_flow(self):
        s = _sender(dst="green-host15")
        r = _receiver(host="green-host15", flows=[])
        rep = ScenarioReport.from_records("x", [s], [r])
        self.assertIsNone(rep.flows[0].rx)
        self.assertTrue(any("saw no flow" in n
                            for n in rep.flows[0].notes))


class TestOrphanFlows(unittest.TestCase):

    def test_orphan_receiver_flow_becomes_warning(self):
        s = _sender()
        r = _receiver(flows=[
            _recv_flow("2001:db8:bbbb:00::2", "2001:db8:bbbb:0f::2",
                       rx=4000),
            # Stray flow nobody sent: warning.
            _recv_flow("2001:db8:bbbb:07::2", "2001:db8:bbbb:0f::2",
                       rx=42),
        ])
        rep = ScenarioReport.from_records("x", [s], [r])
        self.assertEqual(len(rep.flows), 1)
        self.assertEqual(rep.flows[0].rx, 4000)
        self.assertTrue(any("orphan flow" in w for w in rep.warnings))


class TestDuplicateReceiverHost(unittest.TestCase):

    def test_duplicate_receiver_host_warns(self):
        r1 = _receiver(host="green-host15", flows=[
            _recv_flow("2001:db8:bbbb:00::2", "2001:db8:bbbb:0f::2", rx=4000)])
        r2 = _receiver(host="green-host15", flows=[])
        rep = ScenarioReport.from_records("x", [_sender()], [r1, r2])
        self.assertTrue(any("duplicate receiver record" in w
                            for w in rep.warnings))
        # First record should still drive the merge.
        self.assertEqual(rep.flows[0].rx, 4000)


class TestLossAccounting(unittest.TestCase):

    def test_loss_pct_computed(self):
        s = _sender(sent=4000)
        r = _receiver(flows=[_recv_flow(
            "2001:db8:bbbb:00::2", "2001:db8:bbbb:0f::2",
            rx=3000, loss=1000,
        )])
        rep = ScenarioReport.from_records("x", [s], [r])
        self.assertEqual(rep.flows[0].loss_pct(), 25.0)


class TestSerialization(unittest.TestCase):

    def test_to_dict_round_trip(self):
        s = _sender()
        r = _receiver(flows=[_recv_flow(
            "2001:db8:bbbb:00::2", "2001:db8:bbbb:0f::2", rx=4000)])
        rep = ScenarioReport.from_records("baseline", [s], [r])
        d = rep.to_dict()
        self.assertEqual(d["scenario"], "baseline")
        self.assertEqual(len(d["flows"]), 1)
        self.assertEqual(d["flows"][0]["loss_pct"], 0.0)
        # JSON-serializable
        import json
        json.dumps(d, default=str)

    def test_render_ascii_contains_key_fields(self):
        s = _sender(sent=5000)
        r = _receiver(flows=[_recv_flow(
            "2001:db8:bbbb:00::2", "2001:db8:bbbb:0f::2",
            rx=4750, loss=250, reordered=30,
            max_reorder_distance=12, p99_reorder_distance=8)])
        rep = ScenarioReport.from_records("hash5tuple", [s], [r])
        out = rep.render_ascii()
        self.assertIn("scenario: hash5tuple", out)
        self.assertIn("green-host00 -> green-host15", out)
        self.assertIn("5000", out)
        self.assertIn("4750", out)
        self.assertIn("5.00%", out)  # loss pct
        self.assertIn("per-plane (sent / rx)", out)

    def test_render_ascii_renders_warnings(self):
        rep = ScenarioReport(scenario="x")
        rep.warnings.append("something happened")
        self.assertIn("something happened", rep.render_ascii())

    def test_render_ascii_renders_notes(self):
        rep = ScenarioReport.from_records("x", [_sender()], [])
        self.assertIn("no receiver record", rep.render_ascii())


if __name__ == "__main__":
    unittest.main()
