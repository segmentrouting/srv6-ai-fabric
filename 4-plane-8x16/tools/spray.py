#!/usr/bin/env python3
"""
spray.py — userspace SRv6/uSID packet sprayer + receiver.

  - Sender takes a single logical flow, splits packets across all 4 planes
    of the fabric by varying the OUTER IPv6 destination (the uSID list)
    while keeping the INNER destination constant (an anycast tenant addr).
  - Receiver listens on all 4 NICs, counts arrivals per NIC and per plane,
    and (next iteration) tracks reorder.

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

The outer IPv6 destination is the entire SR policy — uSID compresses the
SID list into the address itself, so no SR Extension Header is required.
Each hop's ASIC consumes one uSID by shifting the address left:

    fc00:000<P>:f00<S>:e00<L>:d000::      sender emits this
    fc00:000<P>:e00<L>:d000::             after ingress leaf
    fc00:000<P>:d000::                    after spine
    (inner packet)                        after egress leaf uDT6 decap

Green-only in this first cut; yellow + reorder + hash modes come later.

Update: yellow now also supported. Send side just builds the longer SID list
(`...e009:d001::` instead of `d000::`); recv side widens its BPF to also catch
proto-41 frames (yellow's egress leaf leaves the final `d001` uSID on the wire,
decapped by the host kernel's seg6local — we sniff before that decap so the
per-NIC counts still reflect the fabric path the packet actually took).

Run:
    # Receiver (in one terminal/exec)
    docker exec -it green-host15 python3 /tools/spray.py --role recv

    # Sender (in another)
    docker exec -it green-host00 python3 /tools/spray.py --role send \
        --dst-id 15 --rate 1000pps --duration 5s
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import struct
import sys
import time
from collections import Counter

# Scapy: only used by recv (parse) and send (compose). Suppress the
# "no IPv6 destination" verbose warnings on import.
import logging
logging.getLogger("scapy.runtime").setLevel(logging.ERROR)

from scapy.all import IPv6, UDP, AsyncSniffer  # noqa: E402


# --- constants --------------------------------------------------------------

NUM_PLANES = 4
SPRAY_PORT = 9999
NIC_PREFIX = "eth"        # NICs are eth1..eth4 (eth0 is mgmt)
PLANE_NICS = [f"{NIC_PREFIX}{p + 1}" for p in range(NUM_PLANES)]
PAYLOAD_PAD = b"X" * 32   # filler so each frame is comfortably > 64 bytes


# --- host identity ----------------------------------------------------------

def detect_self_id() -> tuple[str, int]:
    """Return (tenant, host_id) inferred from container hostname.

    Hostname pattern: '<tenant>-host<NN>', e.g. 'green-host07'.
    """
    h = socket.gethostname()
    m = re.match(r"(green|yellow)-host(\d{2})$", h)
    if not m:
        sys.exit(
            f"spray.py: cannot infer self identity from hostname '{h}'. "
            "Expected 'green-host<NN>' or 'yellow-host<NN>'."
        )
    return m.group(1), int(m.group(2))


# --- address helpers (mirror generate_fabric.py / routes.py) ---------------

def host_underlay_addr(tenant: str, plane: int, host_id: int) -> str:
    """Per-(host,plane) NIC underlay address — the outer IPv6 src."""
    base = "bbbb" if tenant == "green" else "cccc"
    return f"2001:db8:{base}:{plane:x}{host_id:02x}::2"


def green_anycast_addr(host_id: int) -> str:
    """Plane-independent green tenant address (inner dst / src)."""
    return f"2001:db8:bbbb:{host_id:02x}::2"


def yellow_loopback_addr(host_id: int) -> str:
    """Plane-independent yellow tenant address (inner dst, post-decap)."""
    return f"2001:db8:cccd:{host_id:02x}::1"


def inner_addr(tenant: str, host_id: int) -> str:
    return green_anycast_addr(host_id) if tenant == "green" else yellow_loopback_addr(host_id)


def usid_outer_dst(tenant: str, plane: int, spine: int, dst_leaf: int) -> str:
    """Build the outer IPv6 destination (the uSID list, compressed).

    Green:  fc00:000<P>:f00<S>:e00<L>:d000::
    Yellow: fc00:000<P>:f00<S>:e00<L>:e009:d001::
    """
    base = f"fc00:000{plane:x}:f00{spine:x}:e00{dst_leaf:x}"
    return f"{base}:d000::" if tenant == "green" else f"{base}:e009:d001::"


# Pair-to-spine map (must match routes.py REFERENCE_PAIRS_SPINES).
PAIRS = {
    (0, 15): 0, (1, 14): 2, (2, 13): 4, (3, 12): 6,
    (4, 11): 1, (5, 10): 3, (6, 9): 5,  (7, 8):  7,
}

def spine_for(src_id: int, dst_id: int) -> int:
    """Pick the transit spine. PAIRS lookup, else dst % 8 (matches
    trace-flow.sh's --install-route fallback)."""
    lo, hi = (src_id, dst_id) if src_id < dst_id else (dst_id, src_id)
    return PAIRS.get((lo, hi), dst_id % 8)


# --- send -------------------------------------------------------------------

def open_send_socket(iface: str) -> socket.socket:
    """Raw IPv6 socket bound to a single NIC.

    SOCK_RAW + IPPROTO_RAW gives us full control of the IPv6 header (we hand
    in pre-built scapy bytes). SO_BINDTODEVICE pins egress to the chosen NIC,
    which is how the sender selects a plane.
    """
    s = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_RAW)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, iface.encode())
    except PermissionError:
        sys.exit(
            "spray.py: SO_BINDTODEVICE needs CAP_NET_RAW. Run inside the lab "
            "host containers (clab gives them the capability) or as root."
        )
    return s


def build_packet(src_underlay: str, dst_outer: str,
                 src_inner: str, dst_inner: str,
                 seq: int, plane: int) -> bytes:
    """Build outer-IPv6(uSID dst, nh=41) / inner-IPv6 / UDP / payload."""
    payload = struct.pack("!QB", seq, plane) + PAYLOAD_PAD
    inner = (
        IPv6(src=src_inner, dst=dst_inner)
        / UDP(sport=SPRAY_PORT, dport=SPRAY_PORT)
        / payload
    )
    outer = IPv6(src=src_underlay, dst=dst_outer, nh=41) / inner
    return bytes(outer)


def cmd_send(args, tenant: str, my_id: int) -> None:
    if my_id == args.dst_id:
        sys.exit("spray.py: --dst-id must differ from this host's id")

    spine = spine_for(my_id, args.dst_id)
    src_inner = inner_addr(tenant, my_id)
    dst_inner = inner_addr(tenant, args.dst_id)

    # Pre-open one socket per plane so per-packet send cost stays low.
    sockets = {p: open_send_socket(PLANE_NICS[p]) for p in range(NUM_PLANES)}

    # Pre-compute per-plane (src_underlay, outer_dst, sockaddr).
    plane_meta = {}
    for p in range(NUM_PLANES):
        src_u = host_underlay_addr(tenant, p, my_id)
        outer_d = usid_outer_dst(tenant, p, spine, args.dst_id)
        plane_meta[p] = (src_u, outer_d, (outer_d, 0, 0, 0))

    interval = 1.0 / args.rate if args.rate > 0 else 0.0
    deadline = time.monotonic() + args.duration if args.duration > 0 else float("inf")

    print(f"spray.py SEND  tenant={tenant}  src=host{my_id:02d}  dst=host{args.dst_id:02d}")
    print(f"               spine=p<P>-spine{spine:02d}  rate={args.rate}pps  duration={args.duration}s")
    print(f"               inner: {src_inner} -> {dst_inner}")
    for p in range(NUM_PLANES):
        print(f"                 plane {p}: {plane_meta[p][0]} -> {plane_meta[p][1]}  via {PLANE_NICS[p]}")

    seq = 0
    sent_per_plane = Counter()
    t_start = time.monotonic()
    next_tx = t_start

    try:
        while time.monotonic() < deadline:
            plane = seq % NUM_PLANES
            src_u, outer_d, sa = plane_meta[plane]
            pkt = build_packet(src_u, outer_d, src_inner, dst_inner, seq, plane)
            try:
                sockets[plane].sendto(pkt, sa)
                sent_per_plane[plane] += 1
            except OSError as e:
                print(f"  send err plane={plane} seq={seq}: {e}", file=sys.stderr)
            seq += 1

            if interval > 0:
                next_tx += interval
                slack = next_tx - time.monotonic()
                if slack > 0:
                    time.sleep(slack)
                else:
                    # Falling behind; let it free-run rather than spin.
                    next_tx = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        for s in sockets.values():
            s.close()

    elapsed = time.monotonic() - t_start
    total = sum(sent_per_plane.values())
    print()
    print(f"  sent {total} packets in {elapsed:.2f}s ({total / elapsed:.0f} pps)")
    for p in range(NUM_PLANES):
        print(f"    plane {p}  ({PLANE_NICS[p]}) : {sent_per_plane[p]}")


# --- recv -------------------------------------------------------------------

class RecvState:
    def __init__(self) -> None:
        self.per_nic = Counter()
        self.per_plane = Counter()
        self.total = 0
        self.first_seq: int | None = None
        self.last_seq: int = -1
        # Wall-clock of the most recent packet; used by --idle-timeout to
        # decide when the burst is over and we can self-exit.
        self.last_rx_time: float = 0.0

    def consume(self, nic: str, raw_payload: bytes) -> None:
        if len(raw_payload) < 9:
            return
        seq, plane = struct.unpack("!QB", raw_payload[:9])
        self.per_nic[nic] += 1
        self.per_plane[plane] += 1
        self.total += 1
        self.last_rx_time = time.monotonic()
        if self.first_seq is None:
            self.first_seq = seq
        if seq > self.last_seq:
            self.last_seq = seq


def cmd_recv(args, tenant: str, my_id: int) -> None:
    inner_dst = inner_addr(tenant, my_id)
    idle_msg = (
        f"auto-exit after {args.idle_timeout:g}s of silence (after first packet)"
        if args.idle_timeout > 0 else "Ctrl-C to stop"
    )
    print(f"spray.py RECV  tenant={tenant}  self=host{my_id:02d}  inner={inner_dst}")
    print(f"               listening on {', '.join(PLANE_NICS)}  port={SPRAY_PORT}")
    print(f"               ({idle_msg})")
    if tenant == "yellow":
        print(f"               yellow: outer SR still on wire at NIC; sniffer peels it.")
        print(f"               (precondition: ./routes.py apply -f routes/reference-pairs.yaml — installs seg6local DT6)")
    print()

    state = RecvState()
    # BPF that catches both tenants:
    #   - green: leaf has already done End.DT6, so the host NIC sees plain
    #     inner IPv6/UDP (matched by `udp port N`).
    #   - yellow: leaf only consumed `e009`, leaving `d001` on the wire;
    #     the encapped frame is IPv6-in-IPv6 (proto 41), decapped later by
    #     the host kernel's seg6local. We sniff BEFORE that decap so per-NIC
    #     counters still reflect the fabric path the packet took.
    bpf = f"ip6 proto 41 or udp port {SPRAY_PORT}"

    # One-shot diagnostic flag for yellow (print outer src/dst on first
    # encapped packet so wire-format surprises are obvious).
    diag = {"printed": False}

    def handle(pkt) -> None:
        if IPv6 not in pkt:
            return
        nic = pkt.sniffed_on or "?"
        outer = pkt[IPv6]

        if outer.nh == 41:
            # Yellow path: peel outer IPv6, expect inner IPv6/UDP underneath.
            if not diag["printed"]:
                print(f"  [first encapped pkt on {nic}] "
                      f"outer src={outer.src}  dst={outer.dst}")
                # Sanity: the final uSID should still be d001 for yellow.
                if tenant == "yellow" and "d001" not in outer.dst:
                    print(f"  WARN: outer dst lacks 'd001' uSID — egress decap surprise?")
                diag["printed"] = True
            inner = outer.payload
            if not isinstance(inner, IPv6) or UDP not in inner:
                return
            udp = inner[UDP]
        else:
            # Green path: kernel-decapped already; outer IS the inner.
            if UDP not in pkt:
                return
            udp = pkt[UDP]

        if udp.dport != SPRAY_PORT:
            return
        state.consume(nic, bytes(udp.payload))

    sniffers = []
    for nic in PLANE_NICS:
        sn = AsyncSniffer(iface=nic, filter=bpf, prn=handle, store=False)
        sn.start()
        sniffers.append(sn)

    stop = {"flag": False}
    def _sig(*_):
        stop["flag"] = True
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    last_print = time.monotonic()
    last_total = 0
    try:
        while not stop["flag"]:
            time.sleep(0.5)
            now = time.monotonic()
            if now - last_print >= 1.0:
                last_print = now
                if state.total > 0 and state.total != last_total:
                    summary = " ".join(
                        f"{nic}={state.per_nic[nic]}" for nic in PLANE_NICS
                    )
                    print(f"  rx total={state.total}  per-nic: {summary}")
                    last_total = state.total
            # Idle-timeout self-exit. Only arms after the first packet so
            # `recv` started before `send` doesn't quit immediately.
            if (args.idle_timeout > 0
                    and state.total > 0
                    and (now - state.last_rx_time) >= args.idle_timeout):
                print(f"  idle for {args.idle_timeout:g}s; exiting.")
                break
    finally:
        for sn in sniffers:
            sn.stop()

    print()
    print(f"  received {state.total} packets")
    print(f"  per NIC:")
    for nic in PLANE_NICS:
        print(f"    {nic}: {state.per_nic[nic]}")
    print(f"  per plane (from payload):")
    for p in range(NUM_PLANES):
        print(f"    plane {p}: {state.per_plane[p]}")
    if state.first_seq is not None:
        seen_range = state.last_seq - state.first_seq + 1
        loss = seen_range - state.total
        print(f"  seq range: {state.first_seq}..{state.last_seq}  "
              f"({seen_range} expected, missing={loss})")


# --- arg parsing ------------------------------------------------------------

def parse_rate(s: str) -> int:
    """Accept '1000pps' or '1000' and return packets/sec."""
    m = re.match(r"^(\d+)\s*pps?$", s, re.I) or re.match(r"^(\d+)$", s)
    if not m:
        raise argparse.ArgumentTypeError(f"bad --rate: {s!r}")
    return int(m.group(1))


def parse_duration(s: str) -> float:
    """Accept '5s', '500ms', '0' (run forever), or a bare number = seconds."""
    s = s.strip().lower()
    if s in ("", "0", "0s"):
        return 0.0
    m = re.match(r"^(\d+(?:\.\d+)?)(ms|s)?$", s)
    if not m:
        raise argparse.ArgumentTypeError(f"bad --duration: {s!r}")
    val = float(m.group(1))
    return val / 1000.0 if m.group(2) == "ms" else val


def main() -> None:
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
    p.add_argument("--duration", type=parse_duration, default=parse_duration("5s"),
                   help="(send) e.g. 5s, 500ms, or 0 to run until ^C")
    p.add_argument("--idle-timeout", type=parse_duration, default=parse_duration("6s"),
                   help="(recv) auto-exit after this much silence following the "
                        "first packet; 0 disables (run until ^C). Default 6s.")
    args = p.parse_args()

    tenant, my_id = detect_self_id()

    if args.role == "send":
        if args.dst_id is None:
            p.error("--dst-id is required for --role send")
        cmd_send(args, tenant, my_id)
    else:
        cmd_recv(args, tenant, my_id)


if __name__ == "__main__":
    main()
