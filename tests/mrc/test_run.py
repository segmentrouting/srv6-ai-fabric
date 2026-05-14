"""Tests for mrc/run.py — the docker-host-side orchestrator.

Subprocess calls (`docker_exec`, `docker_exec_async`) are mocked so these
tests run anywhere. The live-lab integration is exercised by actually
running scenarios.
"""
import json
import subprocess
import unittest
from unittest import mock

from srv6_fabric.mrc.scenario import (
    FaultSpec, FlowPair, FlowSpec, ReportSpec, Scenario,
)
from srv6_fabric.mrc.run import (
    _recv_argv, _send_argv, expand_flows, faults_for_netem,
    policy_to_cli, run_flows, FlowRun,
)


def _make_scenario(*, faults=(), policy="round_robin"):
    flow = FlowSpec(
        pairs=(FlowPair("green", 0, 15),),
        policy_spec=policy,
        policy_label=str(policy),
        rate_pps=1000,
        duration_s=2.0,
    )
    return Scenario(
        name="t", description="", flows=(flow,),
        faults=tuple(faults), report=ReportSpec(out=None),
    )


# --- policy_to_cli ----------------------------------------------------------

class TestPolicyToCli(unittest.TestCase):

    def test_string_passthrough(self):
        self.assertEqual(policy_to_cli("round_robin"), "round_robin")
        self.assertEqual(policy_to_cli("hash5tuple"), "hash5tuple")

    def test_weighted_dict(self):
        self.assertEqual(
            policy_to_cli({"weighted": [0.4, 0.3, 0.2, 0.1]}),
            "weighted:0.4,0.3,0.2,0.1",
        )

    def test_health_aware_unsupported(self):
        # Documented limitation: orchestrator can't yet do health-aware.
        with self.assertRaises(NotImplementedError):
            policy_to_cli({"health_aware": "round_robin"})

    def test_unknown_shape_rejected(self):
        with self.assertRaises(ValueError):
            policy_to_cli({"random": [1, 2]})
        with self.assertRaises(ValueError):
            policy_to_cli(42)


# --- expand_flows -----------------------------------------------------------

class TestExpandFlows(unittest.TestCase):

    def test_single_pair(self):
        runs = expand_flows(_make_scenario())
        self.assertEqual(len(runs), 1)
        r = runs[0]
        self.assertEqual(r.src_host, "green-host00")
        self.assertEqual(r.dst_host, "green-host15")
        self.assertEqual(r.tenant, "green")
        self.assertEqual(r.src_id, 0)
        self.assertEqual(r.dst_id, 15)
        self.assertEqual(r.policy_cli, "round_robin")
        self.assertEqual(r.rate_pps, 1000)
        self.assertEqual(r.duration_s, 2.0)

    def test_multiple_pairs_per_flowspec(self):
        flow = FlowSpec(
            pairs=(FlowPair("green", 0, 15), FlowPair("green", 1, 14)),
            policy_spec="round_robin",
            policy_label="round_robin",
            rate_pps=500, duration_s=1.0,
        )
        sc = Scenario(name="t", description="", flows=(flow,),
                      faults=(), report=ReportSpec())
        runs = expand_flows(sc)
        self.assertEqual([(r.src_id, r.dst_id) for r in runs],
                         [(0, 15), (1, 14)])

    def test_weighted_policy_propagates_to_cli(self):
        sc = _make_scenario(policy={"weighted": [0.4, 0.3, 0.2, 0.1]})
        runs = expand_flows(sc)
        self.assertEqual(runs[0].policy_cli, "weighted:0.4,0.3,0.2,0.1")

    def test_health_aware_aborts(self):
        sc = _make_scenario(policy={"health_aware": "round_robin"})
        with self.assertRaises(SystemExit):
            expand_flows(sc)


# --- argv builders ----------------------------------------------------------

class TestArgvBuilders(unittest.TestCase):

    def test_send_argv_shape(self):
        fr = FlowRun(src_host="green-host00", dst_host="green-host15",
                     tenant="green", src_id=0, dst_id=15,
                     policy_cli="round_robin",
                     rate_pps=1000, duration_s=4.0)
        argv = _send_argv(fr)
        self.assertEqual(argv[:3], ["spray", "--role", "send"])
        self.assertIn("--dst-id", argv); self.assertIn("15", argv)
        self.assertIn("--rate", argv);   self.assertIn("1000pps", argv)
        self.assertIn("--duration", argv); self.assertIn("4.0s", argv)
        self.assertIn("--policy", argv); self.assertIn("round_robin", argv)
        self.assertIn("--json", argv)

    def test_recv_argv_shape(self):
        argv = _recv_argv(6.0)
        self.assertEqual(argv[:3], ["spray", "--role", "recv"])
        self.assertIn("--idle-timeout", argv)
        self.assertIn("6.0s", argv)
        self.assertIn("--json", argv)


# --- faults_for_netem -------------------------------------------------------

class TestFaultsForNetem(unittest.TestCase):

    def test_translation_preserves_target_and_spec(self):
        sc = _make_scenario(faults=[
            FaultSpec(kind="netem", target="plane 2", spec="loss 5%"),
            FaultSpec(kind="netem", target="host green-host00", spec="blackhole"),
        ])
        faults = faults_for_netem(sc)
        self.assertEqual(len(faults), 2)
        self.assertEqual(faults[0].target, "plane 2")
        self.assertEqual(faults[0].spec, "loss 5%")
        self.assertEqual(faults[1].target, "host green-host00")
        self.assertEqual(faults[1].spec, "blackhole")


# --- run_flows --------------------------------------------------------------

class _FakePopen:
    """Minimal Popen stand-in for receiver subprocesses."""
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    def communicate(self, timeout=None):
        return self._stdout, self._stderr

    def kill(self):
        pass


def _ok_sender_json(src, dst, sent=2000):
    return json.dumps({
        "src": src, "dst": dst, "tenant": "green",
        "policy": "round_robin", "rate_pps": 1000, "duration_s": 2.0,
        "spine": 0, "sent": sent, "elapsed_s": 2.0,
        "per_plane_sent": {"0": 500, "1": 500, "2": 500, "3": 500},
        "errors": 0,
    })


def _ok_receiver_json(host="green-host15", sent=2000):
    """Receiver JSON shape — matches FlowStats.to_dict() in lib/reorder.py."""
    return json.dumps({
        "host": host, "self_id": 15, "tenant": "green",
        "per_nic": {f"eth{i+1}": sent // 4 for i in range(4)},
        "per_plane": {str(i): sent // 4 for i in range(4)},
        "flows": [{
            "src": "2001:db8:bbbb:00::2",
            "dst": "2001:db8:bbbb:0f::2",
            "sport": 9999, "dport": 9999,
            "received": sent, "loss": 0, "duplicates": 0,
            "first_seq": 0, "last_seq": sent - 1, "expected": sent,
            "reorder_hist": {"0": sent},
            "reorder_max": 0,
            "reorder_mean": 0.0,
            "reorder_p99": 0,
            "per_plane_recv": {str(i): sent // 4 for i in range(4)},
        }],
    })


class TestRunFlows(unittest.TestCase):

    def _flows(self):
        return [FlowRun(src_host="green-host00", dst_host="green-host15",
                        tenant="green", src_id=0, dst_id=15,
                        policy_cli="round_robin",
                        rate_pps=1000, duration_s=1.0)]

    def test_happy_path(self):
        flows = self._flows()
        recv_proc = _FakePopen(stdout=_ok_receiver_json())
        send_res = mock.Mock(rc=0, stdout=_ok_sender_json(
            "green-host00", "green-host15"), stderr="",
            cmd=[], elapsed_s=1.0)

        with mock.patch("srv6_fabric.mrc.run.docker_exec_async", return_value=recv_proc), \
             mock.patch("srv6_fabric.mrc.run.docker_exec", return_value=send_res):
            senders, receivers = run_flows(flows, settle_s=0,
                                            idle_timeout_s=1.0)
        self.assertEqual(len(senders), 1)
        self.assertEqual(len(receivers), 1)
        self.assertEqual(senders[0]["sent"], 2000)
        self.assertEqual(receivers[0]["flows"][0]["received"], 2000)

    def test_sender_nonzero_rc_recorded_as_failure(self):
        flows = self._flows()
        recv_proc = _FakePopen(stdout=_ok_receiver_json())
        send_res = mock.Mock(rc=2, stdout="", stderr="boom",
                             cmd=[], elapsed_s=0.1)
        with mock.patch("srv6_fabric.mrc.run.docker_exec_async", return_value=recv_proc), \
             mock.patch("srv6_fabric.mrc.run.docker_exec", return_value=send_res):
            senders, receivers = run_flows(flows, settle_s=0,
                                            idle_timeout_s=1.0)
        self.assertEqual(senders, [])
        # Receiver still collected (partial info is better than none).
        self.assertEqual(len(receivers), 1)

    def test_receiver_bad_json_reported(self):
        flows = self._flows()
        recv_proc = _FakePopen(stdout="not-json")
        send_res = mock.Mock(rc=0, stdout=_ok_sender_json(
            "green-host00", "green-host15"), stderr="",
            cmd=[], elapsed_s=1.0)
        with mock.patch("srv6_fabric.mrc.run.docker_exec_async", return_value=recv_proc), \
             mock.patch("srv6_fabric.mrc.run.docker_exec", return_value=send_res):
            senders, receivers = run_flows(flows, settle_s=0,
                                            idle_timeout_s=1.0)
        self.assertEqual(len(senders), 1)
        self.assertEqual(receivers, [])

    def test_one_receiver_per_unique_dst(self):
        # Two flows targeting the same dst → one receiver only.
        flows = [
            FlowRun("green-host00", "green-host15", "green", 0, 15,
                    "round_robin", 500, 1.0),
            FlowRun("green-host01", "green-host15", "green", 1, 15,
                    "round_robin", 500, 1.0),
        ]
        recv_calls = []
        def fake_async(container, argv):
            recv_calls.append(container)
            return _FakePopen(stdout=_ok_receiver_json())
        send_res = mock.Mock(rc=0, stdout=_ok_sender_json(
            "green-host00", "green-host15"), stderr="",
            cmd=[], elapsed_s=1.0)
        with mock.patch("srv6_fabric.mrc.run.docker_exec_async", side_effect=fake_async), \
             mock.patch("srv6_fabric.mrc.run.docker_exec", return_value=send_res):
            run_flows(flows, settle_s=0, idle_timeout_s=1.0)
        self.assertEqual(recv_calls, ["green-host15"])


if __name__ == "__main__":
    unittest.main()
