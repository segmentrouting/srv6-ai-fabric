#!/usr/bin/env python3
"""spray.py — userspace SRv6/uSID packet sprayer + receiver (CLI shim).

This is now a thin wrapper around `mrc.lib.runner`. The actual send/recv
loops, payload codec, address builders, and per-flow stats all live in
the `mrc/` package; this file is just the argparse surface that hosts
in the lab use via `docker exec`. Keeping it small (and identical CLI
to the previous version) means existing docs and muscle memory still
work — see `spray.md`.

Encap shape — uSID, NO SRH:

    +--------------------------------------------------------+
    | IPv6  src=<host underlay>  dst=<uSID per plane>  nh=41 |   <- outer
    |   +----------------------------------------------------+
    |   | IPv6  src=<inner>  dst=<inner anycast>  nh=17      |   <- inner
    |   |   +------------------------------------------------+
    |   |   | UDP  sport  dport=<SPRAY_PORT>                 |
    |   |   |   +-------------+--------------------------+   |
    |   |   |   | seq (8 B)   | plane (1 B) | pad …      |   |
    +---+---+---+-------------+--------------------------+---+

Run:
    # Receiver (one terminal)
    docker exec -it green-host15 python3 /tools/spray.py --role recv

    # Sender (another)
    docker exec -it green-host00 python3 /tools/spray.py --role send \\
        --dst-id 15 --rate 1000pps --duration 5s

New since the library refactor:
    --policy {round_robin,hash5tuple,weighted:0.4,0.3,0.2,0.1}
    --json              print machine-readable result instead of human text
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

# The mrc/ project root is bind-mounted read-only at /mrc inside lab host
# containers (see generate_fabric.py). Putting it on sys.path lets us
# `from lib.runner import ...` directly. Outside the lab (e.g. running
# from a dev checkout), fall back to the working tree.
if "/mrc" not in sys.path:
    sys.path.insert(0, "/mrc")
try:
    from lib.runner import (
        FlowEndpoint, run_receiver, run_sender, detect_self_id,
    )
    from lib.policy import policy_from_spec
    from lib.topo import (
        NUM_PLANES, PLANE_NICS, SPRAY_PORT,
        host_underlay_addr, inner_addr, usid_outer_dst, spine_for,
    )
except ImportError:
    # Dev-tree fallback: ../mrc relative to this file.
    import os as _os
    _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    sys.path.insert(0, _os.path.join(_root, "mrc"))
    from lib.runner import (   # type: ignore[no-redef]
        FlowEndpoint, run_receiver, run_sender, detect_self_id,
    )
    from lib.policy import policy_from_spec   # type: ignore[no-redef]
    from lib.topo import (   # type: ignore[no-redef]
        NUM_PLANES, PLANE_NICS, SPRAY_PORT,
        host_underlay_addr, inner_addr, usid_outer_dst, spine_for,
    )


# --- CLI parsing ------------------------------------------------------------

def parse_rate(s: str) -> int:
    """Accept '1000pps' or '1000'."""
    m = re.match(r"^(\d+)\s*pps?$", s, re.I) or re.match(r"^(\d+)$", s)
    if not m:
        raise argparse.ArgumentTypeError(f"bad --rate: {s!r}")
    return int(m.group(1))


def parse_duration(s: str) -> float:
    """Accept '5s', '500ms', '0' (forever), or bare seconds."""
    s = s.strip().lower()
    if s in ("", "0", "0s"):
        return 0.0
    m = re.match(r"^(\d+(?:\.\d+)?)(ms|s)?$", s)
    if not m:
        raise argparse.ArgumentTypeError(f"bad --duration: {s!r}")
    val = float(m.group(1))
    return val / 1000.0 if m.group(2) == "ms" else val


def parse_policy(s: str):
    """Convert CLI string into a SprayPolicy via policy_from_spec.

    Accepted forms:
        round_robin
        hash5tuple
        weighted:0.4,0.3,0.2,0.1
    """
    s = s.strip()
    if s.startswith("weighted:"):
        weights = [float(w) for w in s.split(":", 1)[1].split(",")]
        return policy_from_spec({"weighted": weights})
    return policy_from_spec(s)


# --- send -------------------------------------------------------------------

def cmd_send(args, tenant: str, my_id: int) -> int:
    if args.dst_id is None:
        print("spray.py: --dst-id is required for --role send", file=sys.stderr)
        return 2
    if my_id == args.dst_id:
        print("spray.py: --dst-id must differ from this host's id", file=sys.stderr)
        return 2

    flow = FlowEndpoint(tenant=tenant, src_id=my_id, dst_id=args.dst_id)
    policy = parse_policy(args.policy)

    if not args.json:
        spine = spine_for(my_id, args.dst_id)
        src_inner = inner_addr(tenant, my_id)
        dst_inner = inner_addr(tenant, args.dst_id)
        print(f"spray.py SEND  tenant={tenant}  "
              f"src=host{my_id:02d}  dst=host{args.dst_id:02d}")
        print(f"               spine=p<P>-spine{spine:02d}  "
              f"policy={policy.name}  "
              f"rate={args.rate}pps  duration={args.duration}s")
        print(f"               inner: {src_inner} -> {dst_inner}")
        for p in range(NUM_PLANES):
            src_outer = host_underlay_addr(tenant, p, my_id)
            dst_outer = usid_outer_dst(tenant, p, spine, args.dst_id)
            print(f"                 plane {p}: {src_outer} -> {dst_outer}"
                  f"  via {PLANE_NICS[p]}")

    result = run_sender(flow, policy, args.rate, args.duration)

    if args.json:
        json.dump(result.to_dict(), sys.stdout)
        sys.stdout.write("\n")
        return 0

    d = result.to_dict()
    print()
    print(f"  sent {d['sent']} packets in {d['elapsed_s']}s "
          f"({d['sent'] / max(d['elapsed_s'], 1e-9):.0f} pps)")
    for p in range(NUM_PLANES):
        n = d["per_plane_sent"].get(p, 0)
        print(f"    plane {p}  ({PLANE_NICS[p]}) : {n}")
    if d["errors"]:
        print(f"    errors: {d['errors']}")
    return 0


# --- recv -------------------------------------------------------------------

def cmd_recv(args, tenant: str, my_id: int) -> int:
    if not args.json:
        idle_msg = (
            f"auto-exit after {args.idle_timeout:g}s of silence (after first pkt)"
            if args.idle_timeout > 0 else "Ctrl-C to stop"
        )
        print(f"spray.py RECV  tenant={tenant}  self=host{my_id:02d}")
        print(f"               listening on {', '.join(PLANE_NICS)}  "
              f"port={SPRAY_PORT}")
        print(f"               ({idle_msg})")
        if tenant == "yellow":
            print(f"               yellow: outer SR still on wire at NIC; "
                  f"sniffer peels it.")
        print()

    report = run_receiver(
        self_host=f"{tenant}-host{my_id:02d}",
        self_id=my_id,
        tenant=tenant,
        idle_timeout_s=args.idle_timeout,
    )

    if args.json:
        json.dump(report, sys.stdout)
        sys.stdout.write("\n")
        return 0

    total = sum(report["per_nic"].values())
    print()
    print(f"  received {total} packets")
    print(f"  per NIC:")
    for nic in PLANE_NICS:
        print(f"    {nic}: {report['per_nic'].get(nic, 0)}")
    print(f"  per plane (from payload):")
    for p in range(NUM_PLANES):
        print(f"    plane {p}: {report['per_plane'].get(p, 0)}")
    print(f"  flows: {len(report['flows'])}")
    for f in report["flows"]:
        print(f"    {f['src']}:{f['sport']} -> "
              f"{f['dst']}:{f['dport']}: "
              f"rx={f['received']}  loss={f['loss']}  dup={f['duplicates']}  "
              f"reord_max={f['reorder_max']}  "
              f"p99={f['reorder_p99']}")
    return 0


# --- main -------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="userspace SRv6/uSID packet sprayer + receiver",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--role", required=True, choices=("send", "recv"))
    p.add_argument("--dst-id", type=int, default=None,
                   help="(send) destination host id 0..15")
    p.add_argument("--rate", type=parse_rate, default=parse_rate("1000pps"),
                   help="(send) packets/sec, e.g. 1000 or 1000pps")
    p.add_argument("--duration", type=parse_duration,
                   default=parse_duration("5s"),
                   help="(send) e.g. 5s, 500ms, or 0 to run until ^C")
    p.add_argument("--policy", type=str, default="round_robin",
                   help="(send) spray policy: round_robin (default), "
                        "hash5tuple, or 'weighted:0.4,0.3,0.2,0.1'")
    p.add_argument("--idle-timeout", type=parse_duration,
                   default=parse_duration("6s"),
                   help="(recv) auto-exit after this much silence "
                        "following the first packet; 0 disables. "
                        "Default 6s.")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON result instead of "
                        "human-readable output (used by mrc orchestrator)")
    args = p.parse_args()

    try:
        tenant, my_id = detect_self_id()
    except ValueError as e:
        print(f"spray.py: {e}", file=sys.stderr)
        return 2

    if args.role == "send":
        return cmd_send(args, tenant, my_id)
    return cmd_recv(args, tenant, my_id)


if __name__ == "__main__":
    sys.exit(main())
