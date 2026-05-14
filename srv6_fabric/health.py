"""Per-plane health probe + down-set maintenance.

The HealthAware policy wrapper (lib/policy.py) holds a mutable `down: set[int]`
that the spray loop consults on every packet. This module owns the rules for
mutating that set:

  - One ICMPv6 echo per plane every `probe_interval_s`.
  - K consecutive failures → mark down.
  - First success → mark up.
  - Probes run in a background thread so the sender's pace isn't perturbed.

The actual probe is parameterized — production wires it to a function that
sends an ICMPv6 echo via the plane's NIC and waits for the reply; tests pass
a deterministic mock so we can verify the K-of-N threshold + recovery rules
without root or live NICs.

Public API:
    HealthMonitor(down, probe, *, num_planes=NUM_PLANES,
                  interval_s=0.5, threshold=3,
                  recovery=1, timeout_s=0.3)
        .start()        # spawn thread
        .stop()         # join thread
        .tick()         # one round of probes, for tests
        .last_status()  # snapshot dict {plane: 'up'|'down'}

`probe(plane: int, timeout_s: float) -> bool` returns True on echo reply
within timeout_s, False otherwise. Implementations must be thread-safe (one
probe per plane per tick).
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Protocol

from .topo import NUM_PLANES


# --- probe abstraction ------------------------------------------------------

class ProbeFn(Protocol):
    def __call__(self, plane: int, timeout_s: float) -> bool: ...


# --- monitor ----------------------------------------------------------------

class HealthMonitor:
    """Threaded per-plane liveness tracker.

    Args:
        down: shared set[int] owned by a HealthAware policy. Mutated in place.
        probe: ProbeFn — returns True on success, False on timeout.
        num_planes: how many planes to monitor (default NUM_PLANES = 4).
        interval_s: time between probe rounds (each round = num_planes probes).
        threshold: consecutive failures to declare a plane down.
        recovery: consecutive successes to declare a plane up.
            Default 1 = immediate recovery (matches design doc).
        timeout_s: per-probe timeout passed to `probe()`.
        clock: monotonic clock fn, injectable for tests.
    """

    def __init__(self,
                 down: set[int],
                 probe: ProbeFn,
                 *,
                 num_planes: int = NUM_PLANES,
                 interval_s: float = 0.5,
                 threshold: int = 3,
                 recovery: int = 1,
                 timeout_s: float = 0.3,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        if recovery < 1:
            raise ValueError("recovery must be >= 1")
        if num_planes < 1:
            raise ValueError("num_planes must be >= 1")
        self._down = down
        self._probe = probe
        self._num_planes = num_planes
        self._interval_s = interval_s
        self._threshold = threshold
        self._recovery = recovery
        self._timeout_s = timeout_s
        self._clock = clock

        # Per-plane streak counters; positive = consecutive failures,
        # negative = consecutive successes. Reset to 0 on transition.
        self._fail_streak: list[int] = [0] * num_planes
        self._pass_streak: list[int] = [0] * num_planes

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("HealthMonitor already started")
        self._thread = threading.Thread(
            target=self._run, name="health-monitor", daemon=True,
        )
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 1.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=join_timeout_s)
        self._thread = None

    # -- core logic ---------------------------------------------------------

    def tick(self) -> None:
        """Run one round of probes (one per plane) and update `down`.

        Public for tests; the background thread calls this in a loop.
        """
        for plane in range(self._num_planes):
            ok = bool(self._probe(plane, self._timeout_s))
            self._record(plane, ok)

    def _record(self, plane: int, ok: bool) -> None:
        with self._lock:
            if ok:
                self._fail_streak[plane] = 0
                self._pass_streak[plane] += 1
                # Recover only if currently down and enough successes seen.
                if plane in self._down and self._pass_streak[plane] >= self._recovery:
                    self._down.discard(plane)
            else:
                self._pass_streak[plane] = 0
                self._fail_streak[plane] += 1
                if plane not in self._down and self._fail_streak[plane] >= self._threshold:
                    self._down.add(plane)

    def _run(self) -> None:
        # Stagger the first probe by interval_s so a freshly-started monitor
        # doesn't race a freshly-started sender.
        next_tick = self._clock() + self._interval_s
        while not self._stop.is_set():
            slack = next_tick - self._clock()
            if slack > 0:
                # Use Event.wait so .stop() can interrupt promptly.
                if self._stop.wait(slack):
                    return
            self.tick()
            next_tick += self._interval_s
            # If we've fallen behind (e.g. system was paused), reset cadence
            # rather than burst-firing.
            now = self._clock()
            if next_tick < now:
                next_tick = now + self._interval_s

    # -- introspection ------------------------------------------------------

    def last_status(self) -> dict[int, str]:
        """Snapshot {plane: 'up'|'down'} — safe to call concurrently."""
        with self._lock:
            return {
                p: ("down" if p in self._down else "up")
                for p in range(self._num_planes)
            }


# --- ICMPv6 probe implementation -------------------------------------------

def make_icmpv6_probe(target_addrs: dict[int, str],
                      nics: tuple[str, ...]) -> ProbeFn:
    """Build a ProbeFn that ICMPv6-echoes `target_addrs[plane]` via `nics[plane]`.

    Lazy-imports scapy so this module remains stdlib-only at import time.
    The probe is synchronous (sr1) and respects timeout_s; one probe at a
    time per plane.

    Args:
        target_addrs: plane → IPv6 address to ping. Typically the
            per-plane underlay address of the *remote* host (so a probe
            failure indicates the fabric plane is down, not the local NIC).
        nics: per-plane NIC list, indexed same as PLANE_NICS.

    Returns:
        ProbeFn(plane, timeout_s) → bool.
    """
    # Local import: scapy.sr1 needs CAP_NET_RAW; in unit tests we never call
    # this factory.
    import logging
    logging.getLogger("scapy.runtime").setLevel(logging.ERROR)
    from scapy.all import IPv6, ICMPv6EchoRequest, sr1, conf  # type: ignore

    # Cache one scapy socket per (NIC, plane). conf.L3socket binds to iface.
    # We rely on routing via dst addr instead — scapy doesn't expose
    # SO_BINDTODEVICE through sr1 portably across versions. The down-set
    # interpretation is per-plane "can I reach the remote underlay via this
    # plane's fabric?" — the route table on the lab hosts already pins the
    # per-plane underlay /64 to the matching NIC (generate_fabric.py /
    # routes.py), so the dst alone selects the egress NIC.

    def probe(plane: int, timeout_s: float) -> bool:
        if plane not in target_addrs:
            return False
        pkt = IPv6(dst=target_addrs[plane]) / ICMPv6EchoRequest(
            id=0xBEEF, seq=plane,
        )
        try:
            resp = sr1(pkt, timeout=timeout_s, verbose=False)
        except OSError:
            return False
        return resp is not None

    return probe
