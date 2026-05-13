"""Per-scenario result merging + summary rendering.

The orchestrator (run.py) runs one or more senders and one or more receivers
per scenario, each producing a JSON record (the dicts returned by
`run_sender(...).to_dict()` and `run_receiver(...)`). This module merges
those records into a single scenario-level result and renders an ASCII
summary suitable for stdout / a logfile.

Public API:
    ScenarioReport.from_records(scenario, sender_records, receiver_records)
        — class method, builds the merged report
    ScenarioReport.to_dict()  — JSON-serializable
    ScenarioReport.render_ascii() -> str  — human summary

The matching rule between senders and receivers is by (tenant, src_host,
dst_host): each sender's `dst` must appear as some receiver's `host`, and
that receiver's flow list must contain a flow with matching src/dst inner
addresses. We surface mismatches as warnings in the report rather than
exceptions, because partial visibility is genuinely useful when debugging
a blackholed plane.
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

    # Receiver-side (None means no matching receiver record found)
    rx: int | None = None
    loss: int | None = None
    dup: int | None = None
    reordered: int | None = None
    max_reorder_distance: int | None = None
    mean_reorder_distance: float | None = None
    p99_reorder_distance: int | None = None
    per_plane_rx: dict[int, int] = field(default_factory=dict)
    per_nic_rx: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def loss_pct(self) -> float | None:
        if self.sent <= 0 or self.rx is None:
            return None
        return 100.0 * max(self.sent - self.rx, 0) / self.sent

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

        Matching strategy (see module docstring): by (tenant, src_host,
        dst_host). Unmatched senders get rx=None and a note; unmatched
        receiver flows become scenario-level warnings (they shouldn't
        happen unless there's stray traffic on the wire).
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
            # Sender doesn't expose src_addr/dst_addr explicitly in the
            # to_dict shape — we have to reconstruct from (tenant, src/dst
            # host_id). Do it cheaply via inner_addr.
            from .runner import host_for  # local: avoids import cycle risk
            from .topo import inner_addr  # safe — pure helper
            # Re-derive src/dst inner addresses from host names.
            # host name = "<tenant>-host<NN>". Pull NN out.
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
                if f["src_addr"] == src_addr and f["dst_addr"] == dst_addr:
                    flow_match = f
                    matched_receiver_flows.add(
                        (row.dst_host,
                         (f["src_addr"], f["dst_addr"],
                          f["src_port"], f["dst_port"])),
                    )
                    break

            if flow_match is None:
                row.notes.append(
                    f"receiver {row.dst_host} saw no flow "
                    f"{src_addr} -> {dst_addr}"
                )
                report.flows.append(row)
                continue

            row.rx = flow_match["rx"]
            row.loss = flow_match["loss"]
            row.dup = flow_match["dup"]
            row.reordered = flow_match["reordered"]
            row.max_reorder_distance = flow_match["max_reorder_distance"]
            row.mean_reorder_distance = flow_match["mean_reorder_distance"]
            row.p99_reorder_distance = flow_match["p99_reorder_distance"]
            row.per_plane_rx = {int(k): v
                                for k, v in flow_match.get("per_plane_rx",
                                                           {}).items()}
            # per-NIC rx is aggregate-across-flows on the receiver side, so
            # only attach it once per (host) to the first matched flow.
            # We use the receiver's top-level per_nic counters — they cover
            # all flows landing on that host, which is what users want when
            # exactly one sender targets that host. For multi-sender-to-one
            # we'd need to track per-NIC at the FlowStats level (not
            # currently exposed); leave per_nic_rx empty in that case.
            if recv.get("_per_nic_attached") is not True:
                row.per_nic_rx = {str(k): v
                                  for k, v in recv.get("per_nic", {}).items()}
                recv["_per_nic_attached"] = True

            report.flows.append(row)

        # Flag receiver flows nobody claimed.
        for host, rec in recv_by_host.items():
            for f in rec.get("flows", []):
                key = (host, (f["src_addr"], f["dst_addr"],
                              f["src_port"], f["dst_port"]))
                if key not in matched_receiver_flows:
                    report.warnings.append(
                        f"orphan flow at {host}: {f['src_addr']} -> "
                        f"{f['dst_addr']} ({f['rx']} pkts)"
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
            --------------------------------------------------------------
              src -> dst      policy       sent   rx    loss%  reord  max
              green-host00 -> green-host15 rr     5000  5000   0.00%  47   12
              ...
            --------------------------------------------------------------
              per-plane (sent / rx):
                plane 0:  1250 / 1248
                plane 1:  1250 / 1247
                ...

            warnings:
              - ...
        """
        lines: list[str] = []
        lines.append(f"scenario: {self.scenario}")
        lines.append("=" * 78)

        # Header row.
        hdr = (f"  {'flow':<30}  {'policy':<14} {'sent':>6} "
               f"{'rx':>6} {'loss%':>7} {'reord':>6} {'max':>4}")
        lines.append(hdr)
        lines.append("  " + "-" * (len(hdr) - 2))

        for f in self.flows:
            flow_label = f"{f.src_host} -> {f.dst_host}"
            rx_str = "-" if f.rx is None else str(f.rx)
            reord_str = "-" if f.reordered is None else str(f.reordered)
            max_str = "-" if f.max_reorder_distance is None else str(f.max_reorder_distance)
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
            for p, n in f.per_plane_rx.items():
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
