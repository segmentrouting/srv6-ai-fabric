"""MRC probe + loss-report I/O layer (sender and receiver agents).

This module wraps the pure-logic pieces from probe_clock.py,
loss_window.py and loss_compute.py with the actual sockets and threads,
and exposes two coordinator classes:

  SenderMrcAgent
      Started inside spray.py --role send when policy=health_aware_mrc.
      Drives EV probes per plane, listens for replies + loss reports,
      and feeds an EVStateTable that the HealthAwareMrc policy reads
      from once per pick.

  ReceiverMrcAgent
      Started inside spray.py --role recv when MRC is enabled.
      Listens for probes on every plane and unicasts replies back on
      the same plane; tracks per-(flow, plane) data-packet loss in
      rolling windows and unicasts LOSS_REPORTs back to the senders
      identified by the reply_addr cached from received probes.

Threading model
---------------
SenderMrcAgent runs FOUR daemon threads:
  - emit thread: every probe_interval_ms, send one PROBE per plane
  - reply-RX thread per plane: blocks on per-plane probe socket,
    decodes replies, calls ProbeClock.match_reply, pushes RTT into
    EVStateTable
  - report-RX thread: blocks on the report socket, decodes
    LOSS_REPORTs, calls loss_compute.apply_loss_report
  - timeout-sweep thread: every probe_interval_ms, calls
    ProbeClock.sweep_timeouts, pushes each timeout into EVStateTable
    as a failed probe

Plus a small piece of state on the sender hot path: a SentWindowRing
that the runner's progress_cb feeds via `agent.record_sent(plane)`.
The agent's window-rotate thread closes a SentWindow every
loss_window_ms and pushes it into the ring for the report-RX thread
to find.

ReceiverMrcAgent runs THREE daemon threads per agent:
  - probe-RX thread per plane: blocks on per-plane probe socket,
    decodes PROBE, builds + sends PROBE_REPLY on the same socket
    (so the reply goes back via the same plane NIC); also caches
    sender's reply_addr for the loss-report emitter
  - loss-emit thread: every loss_window_ms, snapshot each known flow
    in LossWindowTable, encode LOSS_REPORT, sendto cached reply_addr

Plus: `agent.record_data(flow_key, plane, seq)` hooked into the
existing data-receive path so the LossWindowTable sees data packets.

All threads are daemons; they exit when the main thread does. Each
thread checks `self._stop.is_set()` on its select/sleep wakeups so a
caller-driven stop is responsive too (used by tests).

Lab vs test
-----------
In a real container deployment, sockets are AF_INET6 + SO_BINDTODEVICE
on e<plane-NIC>. In tests, the BIND_TO_DEVICE flag is suppressed and
sockets bind to ::1 with a per-plane port offset (each "plane" gets a
distinct port so test traffic doesn't conflict). The construction
helpers below accept a `use_loopback: bool` flag controlling this.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..topo import (
    NUM_PLANES,
    PLANE_NICS,
    SPRAY_PROBE_PORT,
    SPRAY_REPORT_PORT,
    host_underlay_addr,
    tenant_id as topo_tenant_id,
    tenant_name as topo_tenant_name,
)
from .ev_state import EVStateTable
from .loss_compute import (
    LossFusionStats,
    SentWindow,
    SentWindowRing,
    apply_loss_report,
)
from .loss_window import LossWindowTable
from .probe import (
    LossReport,
    Probe,
    ProbeDecodeError,
    decode_loss_report,
    decode_probe,
    decode_probe_reply,
    encode_loss_report,
    encode_probe,
    encode_probe_reply,
)
from .probe_clock import ProbeClock


log = logging.getLogger(__name__)


# --- defaults (conservative; agent is off by default in scenarios) ---------

DEFAULT_PROBE_INTERVAL_MS = 200
DEFAULT_PROBE_TIMEOUT_MS = 100
DEFAULT_LOSS_WINDOW_MS = 200
DEFAULT_MAX_WINDOW_SKEW_MS = 500
DEFAULT_RECV_BUFSIZE = 4096
DEFAULT_SOCKET_TIMEOUT_S = 0.25  # how often blocking RX threads wake to check stop


@dataclass
class AgentConfig:
    """Wall-clock cadence + sockets config. Times are milliseconds at
    this layer (converted to ns for the ProbeClock)."""
    probe_interval_ms: int = DEFAULT_PROBE_INTERVAL_MS
    probe_timeout_ms: int = DEFAULT_PROBE_TIMEOUT_MS
    loss_window_ms: int = DEFAULT_LOSS_WINDOW_MS
    max_window_skew_ms: int = DEFAULT_MAX_WINDOW_SKEW_MS
    use_loopback: bool = False  # tests set True; lab leaves False

    def __post_init__(self) -> None:
        for f in ("probe_interval_ms", "probe_timeout_ms",
                  "loss_window_ms", "max_window_skew_ms"):
            v = getattr(self, f)
            if v <= 0:
                raise ValueError(f"{f} must be positive, got {v}")


# --- socket helpers --------------------------------------------------------

def _open_udp_socket(
    *,
    iface: Optional[str],
    bind_addr: str,
    bind_port: int,
    use_loopback: bool,
) -> socket.socket:
    """Open an AF_INET6 UDP socket, optionally bound to a NIC.

    `iface` is the linux interface name for SO_BINDTODEVICE; ignored
    when `use_loopback=True` (loopback doesn't accept BINDTODEVICE).

    We always set SO_REUSEADDR so test runs don't trip over each other.
    """
    s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM, 0)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if not use_loopback and iface is not None:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                         iface.encode())
        except PermissionError as e:
            raise PermissionError(
                f"SO_BINDTODEVICE on {iface} needs CAP_NET_RAW. "
                "Run inside the host containers or as root."
            ) from e
    s.bind((bind_addr, bind_port))
    s.settimeout(DEFAULT_SOCKET_TIMEOUT_S)
    return s


# --- sender agent ----------------------------------------------------------

@dataclass
class _PeerInfo:
    """A single sender's per-plane peer addresses for probes."""
    # Per-plane (peer_underlay_addr, probe_port). The peer's underlay
    # address differs per plane because each host has a per-plane
    # underlay v6. We store the address as a string and let socket
    # resolve it at sendto time.
    peer_addrs: Tuple[str, ...]
    probe_port: int
    report_port: int


class SenderMrcAgent:
    """Per-flow sender-side MRC agent.

    One instance per --role send process. Owns:
      - the EVStateTable read by HealthAwareMrc.pick()
      - a ProbeClock + per-plane probe sockets
      - a SentWindowRing + a window-rotate timer
      - a LossFusionStats counter

    Lifecycle: construct -> start() -> run for the duration of the
    spray flow -> stop(). stop() is best-effort: threads are daemons.
    """

    def __init__(
        self,
        *,
        tenant: str,
        src_id: int,
        dst_id: int,
        table: EVStateTable,
        config: AgentConfig,
        # Optional injection points for tests:
        sockets_factory: Optional[Callable[[int], socket.socket]] = None,
        report_socket_factory: Optional[Callable[[], socket.socket]] = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if table.num_planes != NUM_PLANES:
            raise ValueError(
                f"EVStateTable.num_planes={table.num_planes} but "
                f"topo NUM_PLANES={NUM_PLANES}"
            )
        if tenant not in table.tenants:
            raise ValueError(
                f"tenant {tenant!r} not in table {table.tenants}"
            )

        self.tenant = tenant
        self.tenant_id = topo_tenant_id(tenant)
        self.src_id = src_id
        self.dst_id = dst_id
        self.table = table
        self.cfg = config
        self.clock_ns = clock_ns

        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()

        self.stats = LossFusionStats()
        self.probe_clock = ProbeClock(
            num_planes=NUM_PLANES,
            probe_timeout_ns=config.probe_timeout_ms * 1_000_000,
        )
        self.sent_ring = SentWindowRing(num_planes=NUM_PLANES)

        # Build per-plane peer addresses. Probes go from my underlay
        # on plane P to the dst's underlay on plane P, never via SR.
        self._peer = _PeerInfo(
            peer_addrs=tuple(
                host_underlay_addr(tenant, p, dst_id)
                for p in range(NUM_PLANES)
            ),
            probe_port=SPRAY_PROBE_PORT,
            report_port=SPRAY_REPORT_PORT,
        )

        # Socket factories: tests inject their own to skip BINDTODEVICE
        # and use ::1 with distinct ports per plane.
        if sockets_factory is None:
            sockets_factory = self._default_probe_socket
        if report_socket_factory is None:
            report_socket_factory = self._default_report_socket

        self._probe_sockets: Dict[int, socket.socket] = {
            p: sockets_factory(p) for p in range(NUM_PLANES)
        }
        self._report_socket: socket.socket = report_socket_factory()

        # Sender-side per-plane TX counter for the current emit-window.
        # Updated by record_sent() on the hot path; snapshotted by the
        # window-rotate thread.
        self._current_window_sent: List[int] = [0] * NUM_PLANES
        self._current_window_start_ns: int = self.clock_ns()
        self._current_window_id: int = 0

    # --- public API ----------------------------------------------------

    def start(self) -> None:
        """Start all daemon threads."""
        self._stop.clear()
        self._spawn(self._emit_loop, name="mrc-emit")
        self._spawn(self._sweep_loop, name="mrc-sweep")
        self._spawn(self._window_rotate_loop, name="mrc-window")
        for p in range(NUM_PLANES):
            self._spawn(self._reply_rx_loop, name=f"mrc-reply-p{p}", args=(p,))
        self._spawn(self._report_rx_loop, name="mrc-report")

    def stop(self, *, timeout_s: float = 1.0) -> None:
        """Signal threads to exit; close sockets. Threads are daemons so
        we don't require them to actually join in time."""
        self._stop.set()
        # Closing the sockets unblocks any in-flight recvfrom.
        for s in list(self._probe_sockets.values()):
            try:
                s.close()
            except OSError:
                pass
        try:
            self._report_socket.close()
        except OSError:
            pass
        deadline = time.monotonic() + timeout_s
        for t in self._threads:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                t.join(timeout=remaining)

    def record_sent(self, plane: int) -> None:
        """Hook for the runner's progress_cb. O(1), lock-free per call;
        the window-rotate thread snapshots the counters under the lock.

        Out-of-range planes are silently dropped — defensive: a policy
        returning garbage shouldn't crash the agent.
        """
        if 0 <= plane < NUM_PLANES:
            # We trade strict atomicity for speed here: Python's list
            # element += is not atomic, but a single thread (the spray
            # hot loop) calls record_sent, and the window-rotate thread
            # takes the lock when snapshotting. Worst case is one
            # off-by-one in a snapshot taken concurrently with an
            # increment, which is negligible for loss math.
            self._current_window_sent[plane] += 1

    # --- thread bodies -------------------------------------------------

    def _emit_loop(self) -> None:
        """Send one PROBE per plane every probe_interval_ms."""
        interval_s = self.cfg.probe_interval_ms / 1000.0
        next_tick = time.monotonic()
        while not self._stop.is_set():
            now_ns = self.clock_ns()
            for plane in range(NUM_PLANES):
                req_id, tx_ns = self.probe_clock.emit(plane, now_ns=now_ns)
                try:
                    payload = encode_probe(
                        req_id=req_id,
                        plane_id=plane,
                        tx_ns=tx_ns,
                        tenant_id=self.tenant_id,
                        src_id=self.src_id,
                        reply_port=self._peer.report_port,
                    )
                except ValueError:
                    # tx_ns occasionally exceeds u64 on systems with
                    # unusual clocks — treat as a soft error.
                    log.warning("mrc.probe: encode_probe failed for plane %d",
                                plane)
                    continue
                try:
                    self._probe_sockets[plane].sendto(
                        payload,
                        (self._peer.peer_addrs[plane], self._peer.probe_port),
                    )
                except OSError as e:
                    log.debug("mrc.probe: sendto p%d failed: %s", plane, e)
                    # The probe is still considered "outstanding"; it
                    # will time out naturally and trigger a probe-fail
                    # signal. That's the right semantic for "I tried to
                    # probe but the kernel refused."
            next_tick += interval_s
            sleep_s = next_tick - time.monotonic()
            if sleep_s < 0:
                # Falling behind; reset cadence rather than spin.
                next_tick = time.monotonic()
            else:
                self._stop.wait(sleep_s)

    def _sweep_loop(self) -> None:
        """Check for outstanding probes past the timeout."""
        interval_s = self.cfg.probe_interval_ms / 1000.0
        while not self._stop.is_set():
            timeouts = self.probe_clock.sweep_timeouts(self.clock_ns())
            for plane, _req_id in timeouts:
                self.table.record_probe_result(
                    self.tenant, plane, success=False,
                )
            self._stop.wait(interval_s)

    def _reply_rx_loop(self, plane: int) -> None:
        """Per-plane PROBE_REPLY listener; sets RTT on the EV table."""
        sock = self._probe_sockets[plane]
        while not self._stop.is_set():
            try:
                payload, _from = sock.recvfrom(DEFAULT_RECV_BUFSIZE)
            except socket.timeout:
                continue
            except OSError:
                return  # socket closed during stop()
            try:
                reply = decode_probe_reply(payload)
            except ProbeDecodeError as e:
                log.debug("mrc.probe: bad reply on plane %d: %s", plane, e)
                continue
            # The reply could be for any plane; trust the payload
            # plane_id, not the socket. (If the reply lands on the
            # wrong socket due to a network mishap, ProbeClock will
            # treat it as stale via the plane mismatch.)
            now_ns = self.clock_ns()
            rtt_ns = self.probe_clock.match_reply(
                req_id=reply.req_id,
                plane=reply.plane_id,
                reply_tx_ns=reply.tx_ns,
                now_ns=now_ns,
            )
            if rtt_ns is None:
                continue
            self.table.record_probe_result(
                self.tenant, reply.plane_id,
                success=True, rtt_ns=rtt_ns,
            )

    def _report_rx_loop(self) -> None:
        """LOSS_REPORT listener; pushes into EVStateTable via fusion."""
        sock = self._report_socket
        while not self._stop.is_set():
            try:
                payload, _from = sock.recvfrom(DEFAULT_RECV_BUFSIZE)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                report = decode_loss_report(payload)
            except ProbeDecodeError as e:
                log.debug("mrc.probe: bad loss report: %s", e)
                continue
            apply_loss_report(
                table=self.table,
                tenant=self.tenant,
                report=report,
                sent_ring=self.sent_ring,
                received_at_ns=self.clock_ns(),
                max_window_skew_ns=self.cfg.max_window_skew_ms * 1_000_000,
                stats=self.stats,
            )

    def _window_rotate_loop(self) -> None:
        """Close + ring-push a SentWindow every loss_window_ms."""
        interval_s = self.cfg.loss_window_ms / 1000.0
        while not self._stop.is_set():
            self._stop.wait(interval_s)
            if self._stop.is_set():
                return
            self._rotate_window()

    def _rotate_window(self) -> None:
        """Snapshot the current sent counters into a closed SentWindow.

        Resets per-plane counters under the lock so concurrent
        record_sent calls don't lose increments straddling the rotate.
        """
        now_ns = self.clock_ns()
        with self._lock:
            sent = tuple(self._current_window_sent)
            start = self._current_window_start_ns
            wid = self._current_window_id
            self._current_window_sent = [0] * NUM_PLANES
            self._current_window_start_ns = now_ns
            self._current_window_id = (wid + 1) & 0xFFFF
        if any(s > 0 for s in sent):
            self.sent_ring.push(SentWindow(
                start_ns=start, end_ns=now_ns,
                sent=sent, window_id=wid,
            ))

    # --- default socket factories --------------------------------------

    def _default_probe_socket(self, plane: int) -> socket.socket:
        """Open a UDP socket on the per-plane underlay address."""
        bind_addr = (
            "::1" if self.cfg.use_loopback
            else host_underlay_addr(self.tenant, plane, self.src_id)
        )
        bind_port = (
            self._peer.probe_port + plane if self.cfg.use_loopback
            else 0  # sender side doesn't need a fixed src port
        )
        iface = None if self.cfg.use_loopback else PLANE_NICS[plane]
        return _open_udp_socket(
            iface=iface, bind_addr=bind_addr, bind_port=bind_port,
            use_loopback=self.cfg.use_loopback,
        )

    def _default_report_socket(self) -> socket.socket:
        """Open the UDP socket the receivers send LOSS_REPORTs to."""
        bind_addr = (
            "::1" if self.cfg.use_loopback
            else "::"  # any interface — kernel routes back to sender
        )
        return _open_udp_socket(
            iface=None, bind_addr=bind_addr, bind_port=self._peer.report_port,
            use_loopback=self.cfg.use_loopback,
        )

    # --- internal helpers ----------------------------------------------

    def _spawn(self, fn, *, name: str, args: tuple = ()) -> None:
        t = threading.Thread(target=fn, name=name, args=args, daemon=True)
        t.start()
        self._threads.append(t)


# --- receiver agent --------------------------------------------------------

@dataclass
class _SenderAddr:
    """Cached reply address for a sender we've seen probes from."""
    underlay_addr: str   # source addr of the probe (per recvfrom)
    report_port: int     # reply_port the sender asked us to use


class ReceiverMrcAgent:
    """Per-host receiver-side MRC agent.

    Started by spray.py --role recv when MRC is enabled. Owns:
      - the LossWindowTable into which the data-RX path feeds packets
      - per-plane probe sockets that listen for PROBEs and emit
        PROBE_REPLYs on the same socket
      - a loss-emit timer that periodically encodes + unicasts
        LOSS_REPORTs back to the cached sender addresses

    The data RX path stays in spray.py / runner.py; this agent exposes
    `record_data(flow_key, plane, seq)` for that path to call.
    """

    def __init__(
        self,
        *,
        tenant: str,
        my_id: int,
        config: AgentConfig,
        sockets_factory: Optional[Callable[[int], socket.socket]] = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.tenant = tenant
        self.my_id = my_id
        self.cfg = config
        self.clock_ns = clock_ns
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []

        self.loss_table = LossWindowTable(num_planes=NUM_PLANES)

        # Cache of (tenant_id, src_id) -> sender reply info, learned
        # from received PROBEs. Keyed by tenant_id + src_id (not the
        # full FlowKey, because the receiver doesn't yet know which
        # FlowKey a sender's data packets will use; senders identify
        # themselves at the probe level by tenant/src_id pair).
        self._senders: Dict[Tuple[int, int], _SenderAddr] = {}
        self._senders_lock = threading.Lock()

        if sockets_factory is None:
            sockets_factory = self._default_probe_socket
        self._probe_sockets: Dict[int, socket.socket] = {
            p: sockets_factory(p) for p in range(NUM_PLANES)
        }

    # --- public API ----------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        for p in range(NUM_PLANES):
            self._spawn(self._probe_rx_loop, name=f"mrc-probe-p{p}",
                        args=(p,))
        self._spawn(self._report_emit_loop, name="mrc-report-emit")

    def stop(self, *, timeout_s: float = 1.0) -> None:
        self._stop.set()
        for s in list(self._probe_sockets.values()):
            try:
                s.close()
            except OSError:
                pass
        deadline = time.monotonic() + timeout_s
        for t in self._threads:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                t.join(timeout=remaining)

    def record_data(self, flow_key, plane: int, seq: int) -> None:
        """Hook for the data-RX path."""
        self.loss_table.record(flow_key, plane=plane, seq=seq)

    def known_senders(self) -> Tuple[Tuple[int, int], ...]:
        """Test/diagnostic accessor for the sender cache."""
        with self._senders_lock:
            return tuple(self._senders.keys())

    # --- thread bodies -------------------------------------------------

    def _probe_rx_loop(self, plane: int) -> None:
        """Listen on plane P; on PROBE, send PROBE_REPLY on the same socket."""
        sock = self._probe_sockets[plane]
        while not self._stop.is_set():
            try:
                payload, peer = sock.recvfrom(DEFAULT_RECV_BUFSIZE)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                probe: Probe = decode_probe(payload)
            except ProbeDecodeError as e:
                log.debug("mrc.recv: bad probe on plane %d: %s", plane, e)
                continue
            # peer[0] is the source IPv6; peer[1] is the source port.
            # We use peer[0] for the report destination but the
            # sender-specified probe_port for replies (which goes to
            # SPRAY_PROBE_PORT on the sender, NOT peer[1]).
            self._learn_sender(
                tenant_id=probe.tenant_id, src_id=probe.src_id,
                underlay_addr=peer[0], report_port=probe.reply_port,
            )
            # Build reply with the probe's identity echoed back.
            try:
                reply_payload = encode_probe_reply(
                    req_id=probe.req_id,
                    plane_id=probe.plane_id,
                    tx_ns=probe.tx_ns,
                    svc_time_ns=0,  # we don't measure service time today
                    tenant_id=probe.tenant_id,
                    src_id=probe.src_id,
                    reply_port=probe.reply_port,
                )
            except ValueError:
                continue
            # Reply to the source of the probe on its probe_port. Since
            # this socket is bound to plane P's NIC, the reply
            # naturally traverses plane P.
            try:
                sock.sendto(reply_payload, (peer[0], peer[1]))
            except OSError as e:
                log.debug("mrc.recv: probe reply sendto failed: %s", e)

    def _report_emit_loop(self) -> None:
        """Every loss_window_ms, emit a LOSS_REPORT per known flow."""
        interval_s = self.cfg.loss_window_ms / 1000.0
        # One emit socket; UDP, kernel picks source IPv6 for routing.
        emit_sock = self._open_emit_socket()
        try:
            while not self._stop.is_set():
                self._stop.wait(interval_s)
                if self._stop.is_set():
                    return
                self._emit_one_round(emit_sock)
        finally:
            try:
                emit_sock.close()
            except OSError:
                pass

    def _emit_one_round(self, sock: socket.socket) -> None:
        """For each known flow, snapshot + send a LOSS_REPORT."""
        for flow_key in self.loss_table.known_flows():
            report = self.loss_table.snapshot_and_reset(flow_key)
            if not report.planes:
                continue
            # Pick the destination from our sender cache. flow_key
            # convention in this agent: (tenant_id, src_id, dst_id).
            # Receiver code in spray.py is responsible for choosing
            # this convention when calling record_data.
            if not (isinstance(flow_key, tuple) and len(flow_key) >= 2):
                log.debug("mrc.recv: flow_key %r not in (tid, sid, ...) "
                          "shape; cannot route report", flow_key)
                continue
            key = (flow_key[0], flow_key[1])
            with self._senders_lock:
                sender = self._senders.get(key)
            if sender is None:
                # Never received a probe from this sender; we have no
                # reply address. Skip (the report will retry next
                # window once a probe arrives).
                continue
            try:
                payload = encode_loss_report(
                    window_id=report.window_id, planes=list(report.planes),
                )
            except ValueError:
                continue
            try:
                sock.sendto(payload, (sender.underlay_addr,
                                      sender.report_port))
            except OSError as e:
                log.debug("mrc.recv: loss report sendto failed: %s", e)

    # --- helpers -------------------------------------------------------

    def _learn_sender(
        self, *, tenant_id: int, src_id: int,
        underlay_addr: str, report_port: int,
    ) -> None:
        with self._senders_lock:
            self._senders[(tenant_id, src_id)] = _SenderAddr(
                underlay_addr=underlay_addr,
                report_port=report_port,
            )

    def _default_probe_socket(self, plane: int) -> socket.socket:
        # Receiver listens on the canonical probe port per plane.
        if self.cfg.use_loopback:
            bind_addr = "::1"
            # Per-plane port offset so tests can spin up sender + receiver
            # in the same process without socket conflicts.
            bind_port = SPRAY_PROBE_PORT + 100 + plane
            iface = None
        else:
            bind_addr = host_underlay_addr(self.tenant, plane, self.my_id)
            bind_port = SPRAY_PROBE_PORT
            iface = PLANE_NICS[plane]
        return _open_udp_socket(
            iface=iface, bind_addr=bind_addr, bind_port=bind_port,
            use_loopback=self.cfg.use_loopback,
        )

    def _open_emit_socket(self) -> socket.socket:
        bind_addr = "::1" if self.cfg.use_loopback else "::"
        # Use ephemeral port; kernel routing picks the egress NIC.
        return _open_udp_socket(
            iface=None, bind_addr=bind_addr, bind_port=0,
            use_loopback=self.cfg.use_loopback,
        )

    def _spawn(self, fn, *, name: str, args: tuple = ()) -> None:
        t = threading.Thread(target=fn, name=name, args=args, daemon=True)
        t.start()
        self._threads.append(t)


__all__ = [
    "AgentConfig",
    "SenderMrcAgent",
    "ReceiverMrcAgent",
]
