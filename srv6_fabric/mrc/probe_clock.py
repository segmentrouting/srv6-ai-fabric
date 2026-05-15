"""Per-plane outstanding-probe tracking + timeout sweep (sender side).

The sender's probe loop emits one PROBE per plane every probe_interval_ms
and wants to know which planes' probes never came back. This module
holds the bookkeeping for that question, separated from the I/O so the
state-machine logic is unit-testable without sockets.

Lifecycle from the caller's view:

    clock = ProbeClock(num_planes=4, probe_timeout_ns=50_000_000)

    # Each emit:
    req_id, tx_ns = clock.emit(plane=2, now_ns=time.monotonic_ns())
    # ... encode + sendto

    # Each reply received:
    matched = clock.match_reply(req_id=req_id, plane=2,
                                 reply_tx_ns=reply.tx_ns,
                                 now_ns=time.monotonic_ns())
    # matched is None (unknown / late / wrong-plane) or an RTT in ns.

    # Periodic timeout sweep (e.g. every probe_interval_ms):
    timeouts = clock.sweep_timeouts(now_ns=time.monotonic_ns())
    for plane in timeouts:
        ev_table.record_probe_result(tenant, plane, success=False)

`req_id` is a u16 that wraps. The tracker keeps at most
`max_outstanding_per_plane` entries per plane (default 256, fits in
u16 wrap window for typical cadences). Older outstanding entries are
silently overwritten — at the cadences we run (10–100 Hz per plane)
this is not reachable in practice; the cap exists to bound memory in
adversarial / runaway-emit failure modes.

Thread-safety: the tracker uses a single threading.Lock around all
state. Expected callers: one emit thread + one reply RX thread + one
sweep thread. Hot path is short — match_reply is O(1), sweep is O(N)
in outstanding entries. Holding the lock during EV table updates is
NOT ok (those would re-enter the table's lock); the sweep returns a
plain list and the caller updates EVStateTable outside the lock.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class _OutstandingProbe:
    """Bookkeeping for one in-flight probe.

    `req_id` is also the dict key in `_outstanding[plane]`; we keep it
    in the value too so the sweep can build clean (plane, req_id) tuples
    without re-iterating dict items.
    """
    req_id: int
    plane: int
    tx_ns: int


class ProbeClock:
    """Per-plane req-id allocator + outstanding-probe registry."""

    def __init__(
        self,
        *,
        num_planes: int,
        probe_timeout_ns: int,
        max_outstanding_per_plane: int = 256,
    ) -> None:
        if num_planes <= 0:
            raise ValueError(f"num_planes must be positive, got {num_planes}")
        if probe_timeout_ns <= 0:
            raise ValueError(
                f"probe_timeout_ns must be positive, got {probe_timeout_ns}"
            )
        if max_outstanding_per_plane <= 0:
            raise ValueError(
                "max_outstanding_per_plane must be positive, got "
                f"{max_outstanding_per_plane}"
            )

        self._num_planes = num_planes
        self._probe_timeout_ns = probe_timeout_ns
        self._max_outstanding = max_outstanding_per_plane

        # Per-plane next req_id (u16, wraps). Per-plane keeps the
        # number-space dense and lets us reason about wrap independently
        # per plane; req_ids ARE NOT globally unique across planes.
        self._next_req_id: List[int] = [0] * num_planes

        # Per-plane outstanding probes: req_id -> _OutstandingProbe.
        self._outstanding: List[Dict[int, _OutstandingProbe]] = [
            {} for _ in range(num_planes)
        ]

        # Counters: how many probes have we ever emitted vs replied to
        # vs timed out per plane. Diagnostic only; useful for tests
        # asserting the I/O layer is calling us correctly.
        self._emit_count: List[int] = [0] * num_planes
        self._reply_count: List[int] = [0] * num_planes
        self._timeout_count: List[int] = [0] * num_planes
        # Replies that didn't match anything outstanding. Either too
        # late (already swept as timeout) or duplicate / wrong plane.
        self._stale_replies: int = 0

        self._lock = threading.Lock()

    @property
    def num_planes(self) -> int:
        return self._num_planes

    def emit(self, plane: int, now_ns: int) -> Tuple[int, int]:
        """Allocate a fresh req_id for `plane` and record the tx time.

        Returns (req_id, tx_ns) — caller passes both to encode_probe.
        tx_ns is just `now_ns` echoed back (we accept the clock as a
        parameter so tests don't depend on time.monotonic_ns).

        If the plane already has `max_outstanding_per_plane` outstanding
        probes, the OLDEST entry is dropped (LRU eviction). This shouldn't
        happen at sane cadences and is treated as a silent timeout — the
        evicted entry won't appear in any sweep.
        """
        self._check_plane(plane)
        with self._lock:
            req_id = self._next_req_id[plane]
            self._next_req_id[plane] = (req_id + 1) & 0xFFFF
            outstanding = self._outstanding[plane]
            if len(outstanding) >= self._max_outstanding:
                # Drop oldest. dicts preserve insertion order; popitem
                # without a key removes the LAST item, so we use iter
                # over keys to find the first one. Cheap at <= max size.
                oldest_key = next(iter(outstanding))
                del outstanding[oldest_key]
            outstanding[req_id] = _OutstandingProbe(
                req_id=req_id, plane=plane, tx_ns=now_ns,
            )
            self._emit_count[plane] += 1
            return req_id, now_ns

    def match_reply(
        self,
        *,
        req_id: int,
        plane: int,
        reply_tx_ns: int,
        now_ns: int,
    ) -> Optional[int]:
        """Match an incoming PROBE_REPLY against an outstanding probe.

        Returns the RTT in ns if matched (and removes the entry), or
        None if no match (stale / duplicate / wrong plane).

        We require the (plane, req_id) pair to match — a reply that
        arrives on a different plane than the probe was sent on is
        treated as stale. This catches the rare case where a reply
        traverses the wrong NIC due to a misconfigured route, which
        would otherwise be silently counted as the wrong plane's RTT.

        We also cross-check `reply_tx_ns` against the recorded tx_ns:
        if they don't match exactly the reply is also stale (a different
        probe with the same req_id, e.g. after wrap).
        """
        self._check_plane(plane)
        with self._lock:
            outstanding = self._outstanding[plane]
            entry = outstanding.get(req_id)
            if entry is None or entry.tx_ns != reply_tx_ns:
                self._stale_replies += 1
                return None
            del outstanding[req_id]
            self._reply_count[plane] += 1
            # RTT is wall-time-from-emit-to-now. We don't subtract
            # svc_time_ns here; that's a sender-side policy decision
            # left to the caller (e.g. for the OCP adj_svc_time bit).
            return now_ns - entry.tx_ns

    def sweep_timeouts(self, now_ns: int) -> List[Tuple[int, int]]:
        """Remove + return any outstanding probes older than the timeout.

        Returns a list of (plane, req_id) for each timed-out probe.
        Caller is responsible for translating each into a
        EVStateTable.record_probe_result(success=False) call (which we
        don't do directly to avoid coupling this module to the table).
        """
        deadline_ns = now_ns - self._probe_timeout_ns
        timed_out: List[Tuple[int, int]] = []
        with self._lock:
            for plane in range(self._num_planes):
                outstanding = self._outstanding[plane]
                # Iterate over a copy of items because we mutate the dict.
                # At sane cadences this is small (a handful of entries).
                for req_id, entry in list(outstanding.items()):
                    if entry.tx_ns <= deadline_ns:
                        del outstanding[req_id]
                        timed_out.append((plane, req_id))
                        self._timeout_count[plane] += 1
        return timed_out

    def stats(self) -> dict:
        """Snapshot of per-plane counters for tests / diagnostics."""
        with self._lock:
            return {
                "emit": list(self._emit_count),
                "reply": list(self._reply_count),
                "timeout": list(self._timeout_count),
                "stale_replies": self._stale_replies,
                "outstanding": [len(o) for o in self._outstanding],
            }

    def outstanding(self, plane: int) -> int:
        """Number of probes currently in-flight on `plane`."""
        self._check_plane(plane)
        with self._lock:
            return len(self._outstanding[plane])

    # --- internal -----------------------------------------------------

    def _check_plane(self, plane: int) -> None:
        if not 0 <= plane < self._num_planes:
            raise ValueError(
                f"plane {plane} out of range [0, {self._num_planes})"
            )


__all__ = ["ProbeClock"]
