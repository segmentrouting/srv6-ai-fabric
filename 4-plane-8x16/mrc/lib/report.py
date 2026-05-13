"""Per-scenario result merging + summary rendering.

The orchestrator (run.py) runs one or more senders and one or more receivers
per scenario, each producing a JSON record (the dicts returned by
`run_sender(...).to_dict()` and `run_receiver(...)`). This module merges
those records into a single scenario-level result and renders an ASCII
summary suitable for stdout / a logfile.

Schema reference: `mrc/lib/reorder.py` `FlowStats.to_dict()` and
`mrc/lib/runner.py` `SenderResult.to_dict()`. The receiver-side per-flow
record uses these keys: src, dst, sport, dport, received, duplicates,
loss, per_plane_recv, reorder_hist, reorder_max, reorder_mean, reorder_p99.

The matching rule between senders and receivers is by (tenant, src_host,
dst_host): each sender's `dst` (a host name) must appear as some
receiver's `host`, and that receiver's flow list must contain a flow
with matching src/dst inner addresses. We surface mismatches as warnings
in the report rather than exceptions, because partial visibility is
genuinely useful when debugging a blackholed plane.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


# --- merged per-flow row ----------------------------------------------------

@dataclass
class FlowRow:
    """Single sender↔receiver pairing — what the user actually wants to see."""
    src_host: str
    dst_host: str
    tenant: str
    policy: str
    spine: int
    rate_pps: int
    duration_s: float

    # Sender-side
    sent: int = 0
    elapsed_s: float = 0.0
    per_plane_sent: dict[int, int] = field(default_factory=dict)
    send_errors: int = 0

    # Receiver-side (None means no matching receiver record found).
    # Keys match FlowStats.to_dict() in lib/reorder.py.
    received: int | None = None
    loss: int | None = None
    duplicates: int | None = None
    reorder_max: int | None = None
    reorder_mean: float | None = None
    reorder_p99: int | None = None
    reorder_hist: dict[int, int] = field(default_factory=dict)
    # Number of packets that arrived out-of-order (any non-zero histogram
    # bin). Convenience, derived from reorder_hist.
    reordered: int | None = None
    per_plane_recv: dict[int, int] = field(default_factory=dict)
    per_nic_rx: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def loss_pct(self) -> float | None:
        if self.sent <= 0 or self.received is None:
            return None
        return 100.0 * max(self.sent - self.received, 0) / self.sent

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["loss_pct"] = self.loss_pct()
        return d


# --- top-level report -------------------------------------------------------

@dataclass
class ScenarioReport:
    scenario: str
    flows: list[FlowRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ----- construction ---------------------------------------------------

    @classmethod
    def from_records(cls,
                     scenario_name: str,
                     sender_records: list[dict],
                     receiver_records: list[dict]) -> "ScenarioReport":
        """Merge raw JSON records into a ScenarioReport.

        sender_records: list of dicts from `SenderResult.to_dict()`
        receiver_records: list of dicts from `run_receiver(...)`

        Matching strategy (see module docstring): by host names from the
        sender side, and by inner IPv6 addresses on the receiver side
        (FlowStats keys flows by `(src, dst, sport, dport)` where src/dst
        are IPv6 strings).
        """
        report = cls(scenario=scenario_name)

        # Build dst_host -> receiver record map for O(1) lookup.
        recv_by_host: dict[str, dict] = {}
        for r in receiver_records:
            host = r.get("host")
            if host is None:
                report.warnings.append(
                    f"receiver record without 'host' field: {r!r}"
                )
                continue
            if host in recv_by_host:
                report.warnings.append(
                    f"duplicate receiver record for host={host}; "
                    f"keeping first, ignoring second"
                )
                continue
            recv_by_host[host] = r

        matched_receiver_flows: set[tuple[str, tuple]] = set()

        for s in sender_records:
            row = FlowRow(
                src_host=s["src"],
                dst_host=s["dst"],
                tenant=s["tenant"],
                policy=s["policy"],
                spine=s["spine"],
                rate_pps=s["rate_pps"],
                duration_s=s["duration_s"],
                sent=s["sent"],
                elapsed_s=s["elapsed_s"],
                per_plane_sent={int(k): v
                                for k, v in s["per_plane_sent"].items()},
                send_errors=s.get("errors", 0),
            )

            recv = recv_by_host.get(row.dst_host)
            if recv is None:
                row.notes.append(
                    f"no receiver record for dst={row.dst_host}"
                )
                report.flows.append(row)
                continue

            # Find the flow in the receiver's flow list whose addrs match.
            # Sender doesn't expose src_addr/dst_addr in to_dict; reconstruct
            # from (tenant, host_id) via inner_addr.
            from .topo import inner_addr  # local: pure helper, no scapy
            try:
                src_id = int(row.src_host.rsplit("host", 1)[1])
                dst_id = int(row.dst_host.rsplit("host", 1)[1])
            except (ValueError, IndexError):
                row.notes.append(
                    f"cannot parse host_id from {row.src_host}/{row.dst_host}"
                )
                report.flows.append(row)
                continue

            src_addr = inner_addr(row.tenant, src_id)
            dst_addr = inner_addr(row.tenant, dst_id)

            flow_match = None
            for f in recv.get("flows", []):
                if f["src"] == src_addr and f["dst"] == dst_addr:
                    flow_match = f
                    matched_receiver_flows.add(
                        (row.dst_host,
                         (f["src"], f["dst"], f["sport"], f["dport"])),
                    )
                    break

            if flow_match is None:
                row.notes.append(
                    f"receiver {row.dst_host} saw no flow "
                    f"{src_addr} -> {dst_addr}"
                )
                report.flows.append(row)
                continue

            row.received = flow_match["received"]
            row.loss = flow_match["loss"]
            row.duplicates = flow_match["duplicates"]
            row.reorder_max = flow_match["reorder_max"]
            row.reorder_mean = flow_match["reorder_mean"]
            row.reorder_p99 = flow_match["reorder_p99"]
            row.reorder_hist = {int(k): v
                                for k, v in flow_match.get(
                                    "reorder_hist", {}).items()}
            # Derived: count of out-of-order arrivals = sum of bins with k>0.
            row.reordered = sum(v for k, v in row.reorder_hist.items()
                                if k > 0)
            row.per_plane_recv = {int(k): v
                                  for k, v in flow_match.get(
                                      "per_plane_recv", {}).items()}
            # per-NIC rx is aggregate-across-flows on the receiver side, so
            # only attach it once per (host) to the first matched flow.
            if recv.get("_per_nic_attached") is not True:
                row.per_nic_rx = {str(k): v
                                  for k, v in recv.get("per_nic", {}).items()}
                recv["_per_nic_attached"] = True

            report.flows.append(row)

        # Flag receiver flows nobody claimed.
        for host, rec in recv_by_host.items():
            for f in rec.get("flows", []):
                key = (host, (f["src"], f["dst"], f["sport"], f["dport"]))
                if key not in matched_receiver_flows:
                    report.warnings.append(
                        f"orphan flow at {host}: {f['src']} -> "
                        f"{f['dst']} ({f['received']} pkts)"
                    )

        return report

    # ----- serialization --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "flows": [f.to_dict() for f in self.flows],
            "warnings": list(self.warnings),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    # ----- ascii rendering ------------------------------------------------

    def render_ascii(self) -> str:
        """Render a fixed-width ASCII summary, two sections:

            scenario: <name>
            =================================================================
              flow                              policy           sent     rx
              green-host00 -> green-host15      round_robin      5000   5000
              ...
            -----------------------------------------------------------------
              per-plane (sent / rx):
                plane 0:  1250 / 1248
                ...

            warnings:
              - ...
        """
        lines: list[str] = []
        lines.append(f"scenario: {self.scenario}")
        lines.append("=" * 78)

        hdr = (f"  {'flow':<30}  {'policy':<14} "
               f"{'sent':>6} {'rx':>6} {'loss%':>7} "
               f"{'reord':>6} {'max':>4}")
        lines.append(hdr)
        lines.append("  " + "-" * (len(hdr) - 2))

        for f in self.flows:
            flow_label = f"{f.src_host} -> {f.dst_host}"
            rx_str = "-" if f.received is None else str(f.received)
            reord_str = "-" if f.reordered is None else str(f.reordered)
            max_str = "-" if f.reorder_max is None else str(f.reorder_max)
            lp = f.loss_pct()
            loss_str = "-" if lp is None else f"{lp:.2f}%"
            lines.append(
                f"  {flow_label:<30}  {f.policy:<14} "
                f"{f.sent:>6} {rx_str:>6} {loss_str:>7} "
                f"{reord_str:>6} {max_str:>4}"
            )
            for note in f.notes:
                lines.append(f"      ! {note}")

        # Per-plane aggregate (sum across flows).
        plane_sent: dict[int, int] = {}
        plane_rx: dict[int, int] = {}
        for f in self.flows:
            for p, n in f.per_plane_sent.items():
                plane_sent[p] = plane_sent.get(p, 0) + n
            for p, n in f.per_plane_recv.items():
                plane_rx[p] = plane_rx.get(p, 0) + n

        if plane_sent or plane_rx:
            lines.append("")
            lines.append("  per-plane (sent / rx):")
            planes = sorted(set(plane_sent) | set(plane_rx))
            for p in planes:
                lines.append(
                    f"    plane {p}:  {plane_sent.get(p, 0):>6}"
                    f" / {plane_rx.get(p, 0):>6}"
                )

        if self.warnings:
            lines.append("")
            lines.append("  warnings:")
            for w in self.warnings:
                lines.append(f"    - {w}")

        lines.append("=" * 78)
        return "\n".join(lines)
