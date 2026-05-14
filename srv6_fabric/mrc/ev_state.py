"""Per-(tenant, plane) EV state machine.

Models the OCP MRC `mrc_ctl_ev_state` enum (`GOOD`, `ASSUMED_BAD`, `UNKNOWN`;
`DENIED` is fabric-admin-only and not modeled here) and the demote/recover
logic that real NICs implement in firmware.

This module is pure logic — no sockets, no scapy, no threads. Two signal
sources feed it via separate methods:

- `record_probe_result(...)` for active EV Probes (OCP `MRC_CTL_EP_OP_EV_PROBE`).
- `record_loss_window(...)` for receiver-side passive loss feedback
  (our trim-NACK substitute).

A `health_aware` policy reads `weights()` / `state(...)` on the TX hot path;
both are lock-free and return slightly stale data, which is fine — we're
voting on plane health over hundreds of milliseconds, not nanoseconds.

State transitions are guarded by a `threading.Lock` so the RX thread that
calls `record_*` can't race the TX-thread reads. Callers that mutate state
from a single thread can pass `lock=None` to disable.

See `docs/design-mrc.md` "Detection & re-spray" for the design rationale,
including the OCP mapping and asymmetric demote-fast / recover-slow rule.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable


# --- enums -----------------------------------------------------------------

class EVState(Enum):
    """Matches OCP `enum mrc_ctl_ev_state`, minus DENIED."""
    UNKNOWN = "unknown"
    GOOD = "good"
    ASSUMED_BAD = "assumed_bad"


# Spray weight per state. Sums and zeros are handled in `weights()`.
_STATE_WEIGHT: dict[EVState, float] = {
    EVState.GOOD: 1.0,
    EVState.UNKNOWN: 0.5,
    EVState.ASSUMED_BAD: 0.0,
}


# --- config ----------------------------------------------------------------

@dataclass(frozen=True)
class EVStateConfig:
    """Tunables for the state machine. Defaults match `docs/design-mrc.md`.

    Cadence values are stored in milliseconds for human readability;
    callers that need ns multiply at the boundary.
    """
    probe_fail_threshold: int = 3
    probe_recover_threshold: int = 5
    loss_threshold: float = 0.05
    loss_demote_consecutive: int = 2
    # `mrc_min_active_planes`: floor below which the state machine refuses
    # to demote further. None = `max(1, num_planes // 2)`.
    min_active_planes: int | None = None
    # RTT ring length (probe samples kept per plane for p50/p99 reporting).
    rtt_ring_size: int = 64

    def resolve_min_active(self, num_planes: int) -> int:
        if self.min_active_planes is None:
            return max(1, num_planes // 2)
        return max(1, min(self.min_active_planes, num_planes))


# --- per-plane record ------------------------------------------------------

@dataclass
class _PlaneRecord:
    """All mutable per-(tenant, plane) bookkeeping in one place."""
    state: EVState = EVState.UNKNOWN
    # Probe signal
    consecutive_probe_timeouts: int = 0
    consecutive_probe_successes: int = 0
    rtt_ring_ns: deque[int] = field(default_factory=lambda: deque(maxlen=64))
    # Loss-feedback signal
    consecutive_loss_demote_windows: int = 0
    last_loss_ratio: float = 0.0
    # Counters surfaced in reports.
    transitions: int = 0
    demotes_suppressed_by_floor: int = 0


# --- table -----------------------------------------------------------------

# Type of the optional `on_transition(tenant, plane, old, new)` callback.
TransitionCb = Callable[[str, int, EVState, EVState], None]


class EVStateTable:
    """Mutable per-(tenant, plane) EV state, fed by probes + loss reports.

    Construction:
        t = EVStateTable(
            tenants=("green", "yellow"),
            num_planes=4,
            cfg=EVStateConfig(),
        )

    Signal in:
        t.record_probe_result("green", plane=2, success=True, rtt_ns=1_200_000)
        t.record_probe_result("green", plane=2, success=False)
        t.record_loss_window("green", plane=2, seen=950, expected=1000)

    Policy reads:
        t.state("green", plane=2)            -> EVState
        t.weights("green")                   -> tuple[float, ...]   (len num_planes)
        t.good_planes("green")               -> frozenset[int]

    Reporting:
        t.snapshot()                         -> dict suitable for JSON

    Threading:
        The internal lock protects state transitions. `state()` /
        `weights()` are intentionally lock-free reads of a single tuple
        attribute that is replaced atomically on every change.
    """

    def __init__(
        self,
        tenants: Iterable[str],
        num_planes: int,
        cfg: EVStateConfig | None = None,
        on_transition: TransitionCb | None = None,
        lock: threading.Lock | None = ...,  # type: ignore[assignment]
    ) -> None:
        self._tenants = tuple(tenants)
        if not self._tenants:
            raise ValueError("tenants must be non-empty")
        if num_planes < 1:
            raise ValueError(f"num_planes must be >= 1, got {num_planes}")
        self._num_planes = num_planes
        self._cfg = cfg or EVStateConfig()
        self._on_transition = on_transition
        self._min_active = self._cfg.resolve_min_active(num_planes)
        # `lock=...` (sentinel) -> default to a real lock. lock=None -> no
        # locking (single-threaded callers).
        self._lock = threading.Lock() if lock is ... else lock

        rec_cfg = self._cfg
        self._planes: dict[str, list[_PlaneRecord]] = {
            tenant: [
                _PlaneRecord(
                    rtt_ring_ns=deque(maxlen=rec_cfg.rtt_ring_size),
                )
                for _ in range(num_planes)
            ]
            for tenant in self._tenants
        }
        # Cache for lock-free reads. Rebuilt on every state transition.
        # Indexed by tenant -> tuple of weights per plane.
        self._weights_cache: dict[str, tuple[float, ...]] = {}
        for tenant in self._tenants:
            self._rebuild_weights_locked(tenant)

    # ------------------------------------------------------------------
    # Configuration / shape introspection
    # ------------------------------------------------------------------

    @property
    def tenants(self) -> tuple[str, ...]:
        return self._tenants

    @property
    def num_planes(self) -> int:
        return self._num_planes

    @property
    def min_active(self) -> int:
        return self._min_active

    @property
    def cfg(self) -> EVStateConfig:
        return self._cfg

    # ------------------------------------------------------------------
    # Signal ingress: probes
    # ------------------------------------------------------------------

    def record_probe_result(
        self,
        tenant: str,
        plane: int,
        success: bool,
        rtt_ns: int | None = None,
    ) -> None:
        """Record one probe outcome.

        `success=True` requires `rtt_ns` (the measured round-trip). On
        timeout, pass `success=False` and `rtt_ns=None`.

        Transitions:
            success: consecutive_successes++, consecutive_timeouts=0.
                When successes >= probe_recover_threshold AND the loss
                signal is also quiet (consecutive_loss_demote_windows==0)
                AND current state != GOOD, promote to GOOD.
            timeout: consecutive_timeouts++, consecutive_successes=0.
                When timeouts >= probe_fail_threshold AND current state
                != ASSUMED_BAD, demote (subject to ev_min_active floor).
        """
        self._check_tenant(tenant)
        self._check_plane(plane)
        with self._guard():
            rec = self._planes[tenant][plane]
            if success:
                if rtt_ns is None:
                    raise ValueError(
                        "record_probe_result(success=True) requires rtt_ns"
                    )
                if rtt_ns < 0:
                    raise ValueError(f"rtt_ns must be >= 0, got {rtt_ns}")
                rec.consecutive_probe_successes += 1
                rec.consecutive_probe_timeouts = 0
                rec.rtt_ring_ns.append(rtt_ns)
                if (
                    rec.state is not EVState.GOOD
                    and rec.consecutive_probe_successes
                        >= self._cfg.probe_recover_threshold
                    and rec.consecutive_loss_demote_windows == 0
                ):
                    self._transition_locked(tenant, plane, EVState.GOOD)
            else:
                rec.consecutive_probe_timeouts += 1
                rec.consecutive_probe_successes = 0
                if (
                    rec.state is not EVState.ASSUMED_BAD
                    and rec.consecutive_probe_timeouts
                        >= self._cfg.probe_fail_threshold
                ):
                    self._try_demote_locked(tenant, plane)

    # ------------------------------------------------------------------
    # Signal ingress: receiver loss feedback
    # ------------------------------------------------------------------

    def record_loss_window(
        self,
        tenant: str,
        plane: int,
        seen: int,
        expected: int,
    ) -> None:
        """Record one loss-report window for a plane.

        `expected` is the number of packets the receiver believed should
        have arrived in the window (from sender-side seq numbering);
        `seen` is what actually arrived. `expected==0` is a no-op (no
        traffic on that plane in the window — neither demote nor recover
        evidence).

        Transitions:
            loss_ratio > loss_threshold:
                consecutive_loss_demote_windows++. When >=
                loss_demote_consecutive, demote (subject to floor).
            loss_ratio <= loss_threshold / 2:
                consecutive_loss_demote_windows = 0 (counts as quiet,
                contributes toward eventual recovery via probe path).
            else (mildly elevated but below demote): leave counter
                unchanged.
        """
        self._check_tenant(tenant)
        self._check_plane(plane)
        if seen < 0 or expected < 0:
            raise ValueError(
                f"seen/expected must be >= 0, got seen={seen} expected={expected}"
            )
        if seen > expected:
            # Reordered late arrivals can push seen past expected in a
            # given window; clamp rather than reject.
            seen = expected
        if expected == 0:
            return
        ratio = (expected - seen) / expected
        with self._guard():
            rec = self._planes[tenant][plane]
            rec.last_loss_ratio = ratio
            if ratio > self._cfg.loss_threshold:
                rec.consecutive_loss_demote_windows += 1
                if (
                    rec.state is not EVState.ASSUMED_BAD
                    and rec.consecutive_loss_demote_windows
                        >= self._cfg.loss_demote_consecutive
                ):
                    self._try_demote_locked(tenant, plane)
            elif ratio <= self._cfg.loss_threshold / 2:
                rec.consecutive_loss_demote_windows = 0
            # mild-but-non-zero loss falls through without changing the
            # counter — neither demote evidence nor recovery evidence.

    # ------------------------------------------------------------------
    # Reads (lock-free)
    # ------------------------------------------------------------------

    def state(self, tenant: str, plane: int) -> EVState:
        self._check_tenant(tenant)
        self._check_plane(plane)
        # Reading a single attribute of a dataclass is atomic in CPython
        # under the GIL; no lock required for the staleness we accept.
        return self._planes[tenant][plane].state

    def weights(self, tenant: str) -> tuple[float, ...]:
        """Normalized spray weights per plane for `tenant`.

        Weights sum to 1.0 across planes that have any positive weight.
        If every plane is ASSUMED_BAD, returns uniform weights — the
        ev_min_active floor should normally prevent this, but if it
        somehow happens we degrade to spreading rather than collapsing.
        """
        self._check_tenant(tenant)
        return self._weights_cache[tenant]

    def good_planes(self, tenant: str) -> frozenset[int]:
        return frozenset(
            p for p in range(self._num_planes)
            if self._planes[tenant][p].state is EVState.GOOD
        )

    def rtt_p50_ns(self, tenant: str, plane: int) -> int | None:
        self._check_tenant(tenant)
        self._check_plane(plane)
        ring = self._planes[tenant][plane].rtt_ring_ns
        if not ring:
            return None
        s = sorted(ring)
        return s[len(s) // 2]

    def rtt_p99_ns(self, tenant: str, plane: int) -> int | None:
        self._check_tenant(tenant)
        self._check_plane(plane)
        ring = self._planes[tenant][plane].rtt_ring_ns
        if not ring:
            return None
        s = sorted(ring)
        idx = min(len(s) - 1, (len(s) * 99) // 100)
        return s[idx]

    def snapshot(self) -> dict:
        """JSON-friendly view of the table, suitable for report.py."""
        out: dict = {
            "config": {
                "probe_fail_threshold": self._cfg.probe_fail_threshold,
                "probe_recover_threshold": self._cfg.probe_recover_threshold,
                "loss_threshold": self._cfg.loss_threshold,
                "loss_demote_consecutive": self._cfg.loss_demote_consecutive,
                "min_active_planes": self._min_active,
            },
            "tenants": {},
        }
        for tenant in self._tenants:
            planes_out = []
            for p, rec in enumerate(self._planes[tenant]):
                planes_out.append({
                    "plane": p,
                    "state": rec.state.value,
                    "consecutive_probe_timeouts":
                        rec.consecutive_probe_timeouts,
                    "consecutive_probe_successes":
                        rec.consecutive_probe_successes,
                    "consecutive_loss_demote_windows":
                        rec.consecutive_loss_demote_windows,
                    "last_loss_ratio": round(rec.last_loss_ratio, 6),
                    "rtt_p50_ns": self.rtt_p50_ns(tenant, p),
                    "rtt_p99_ns": self.rtt_p99_ns(tenant, p),
                    "transitions": rec.transitions,
                    "demotes_suppressed_by_floor":
                        rec.demotes_suppressed_by_floor,
                    "weight": self._weights_cache[tenant][p],
                })
            out["tenants"][tenant] = planes_out
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _check_tenant(self, tenant: str) -> None:
        if tenant not in self._planes:
            raise ValueError(
                f"unknown tenant {tenant!r}; known: {self._tenants}"
            )

    def _check_plane(self, plane: int) -> None:
        if not 0 <= plane < self._num_planes:
            raise ValueError(
                f"plane {plane} out of range [0, {self._num_planes})"
            )

    def _guard(self):
        # Wraps the optional lock so call sites don't need to branch.
        if self._lock is None:
            return _NullCtx()
        return self._lock

    def _try_demote_locked(self, tenant: str, plane: int) -> None:
        """Demote to ASSUMED_BAD, honoring ev_min_active floor.

        Caller holds the lock (or lock is None).
        """
        # Count planes that would remain GOOD or UNKNOWN after this demote.
        # We treat UNKNOWN as "potentially usable" for floor purposes
        # because demoting all-UNKNOWN planes en masse would collapse spray.
        usable_after = sum(
            1
            for p, r in enumerate(self._planes[tenant])
            if p != plane and r.state is not EVState.ASSUMED_BAD
        )
        if usable_after < self._min_active:
            rec = self._planes[tenant][plane]
            rec.demotes_suppressed_by_floor += 1
            return
        self._transition_locked(tenant, plane, EVState.ASSUMED_BAD)

    def _transition_locked(
        self, tenant: str, plane: int, new_state: EVState,
    ) -> None:
        rec = self._planes[tenant][plane]
        old = rec.state
        if old is new_state:
            return
        rec.state = new_state
        rec.transitions += 1
        # On promote to GOOD, clear timeout counter so we don't immediately
        # re-demote from stale data; loss window counter is already 0 (a
        # precondition for entering this branch).
        if new_state is EVState.GOOD:
            rec.consecutive_probe_timeouts = 0
        # On demote, clear success counter for symmetry.
        if new_state is EVState.ASSUMED_BAD:
            rec.consecutive_probe_successes = 0
        self._rebuild_weights_locked(tenant)
        if self._on_transition is not None:
            # Callback runs under the lock — keep it cheap (usually
            # just a log line + a counter bump).
            self._on_transition(tenant, plane, old, new_state)

    def _rebuild_weights_locked(self, tenant: str) -> None:
        raw = [
            _STATE_WEIGHT[self._planes[tenant][p].state]
            for p in range(self._num_planes)
        ]
        total = sum(raw)
        if total <= 0:
            # All planes ASSUMED_BAD — fall back to uniform so we don't
            # divide by zero and don't collapse traffic onto plane 0.
            w = 1.0 / self._num_planes
            self._weights_cache[tenant] = (w,) * self._num_planes
            return
        self._weights_cache[tenant] = tuple(x / total for x in raw)


# --- tiny utility ----------------------------------------------------------

class _NullCtx:
    """Context manager that does nothing — used when lock=None."""
    def __enter__(self) -> None:
        return None

    def __exit__(self, *a) -> None:
        return None
