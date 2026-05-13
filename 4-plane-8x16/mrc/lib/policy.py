"""Spray policies — given (seq, flow), pick a plane.

Four implementations, all behind a single `pick()` method so the runner is
oblivious to which policy it's using. Health-awareness is layered in by
composition: any policy can be wrapped in `HealthAware(...)` to skip planes
marked down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .topo import NUM_PLANES, FlowKey


class SprayPolicy(Protocol):
    """Pick a plane for the next packet of the given flow."""

    name: str

    def pick(self, seq: int, flow: FlowKey) -> int: ...


# --- concrete policies ------------------------------------------------------

@dataclass
class RoundRobin:
    name: str = "round_robin"

    def pick(self, seq: int, flow: FlowKey) -> int:
        return seq % NUM_PLANES


@dataclass
class Hash5Tuple:
    """Per-flow plane affinity. Same flow → same plane (mimics ECMP).

    For meaningful spread you need many flows; a single flow pins to one
    plane. This is included for comparison with `round_robin` under load,
    not because it's MRC-correct on its own.
    """
    name: str = "hash5tuple"

    def pick(self, seq: int, flow: FlowKey) -> int:
        return flow.hash5() % NUM_PLANES


@dataclass
class Weighted:
    """Plane choice from a discrete distribution. Deterministic per seq
    (no RNG state) so two runs with the same seed produce identical traces.

    Weights are normalized internally; they don't need to sum to 1.
    """
    weights: tuple[float, ...]
    name: str = "weighted"

    # Precomputed cumulative thresholds in [0, 1).
    _cdf: tuple[float, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.weights) != NUM_PLANES:
            raise ValueError(
                f"weights must have {NUM_PLANES} entries, got {len(self.weights)}"
            )
        if any(w < 0 for w in self.weights):
            raise ValueError(f"weights must be >= 0, got {self.weights}")
        total = sum(self.weights)
        if total <= 0:
            raise ValueError("weights sum to zero")
        cum = 0.0
        cdf: list[float] = []
        for w in self.weights:
            cum += w / total
            cdf.append(cum)
        # Guard against fp drift at the tail.
        cdf[-1] = 1.0
        object.__setattr__(self, "_cdf", tuple(cdf))

    def pick(self, seq: int, flow: FlowKey) -> int:
        # Deterministic, low-discrepancy: mix flow + seq into a 64-bit value,
        # map to [0, 1). Using the additive-recurrence golden-ratio sequence
        # keeps long-run frequencies close to weights without an RNG.
        GOLDEN = 0x9E3779B97F4A7C15
        x = (flow.hash5() + seq * GOLDEN) & 0xFFFFFFFFFFFFFFFF
        u = x / float(1 << 64)
        for p, threshold in enumerate(self._cdf):
            if u < threshold:
                return p
        return NUM_PLANES - 1  # unreachable, _cdf[-1] == 1.0


@dataclass
class HealthAware:
    """Wrap any policy with plane-health filtering.

    `down` is a mutable set of plane indices; the runner's health probe owns
    it. `pick` calls the inner policy, and if it returns a down plane, walks
    forward (mod NUM_PLANES) to the next healthy one. If *all* planes are
    down, returns the inner choice unchanged (degrades to "send and hope").
    """
    inner: SprayPolicy
    down: set[int] = field(default_factory=set)
    name: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", f"health_aware({self.inner.name})")

    def pick(self, seq: int, flow: FlowKey) -> int:
        choice = self.inner.pick(seq, flow)
        if not self.down or choice not in self.down:
            return choice
        if len(self.down) >= NUM_PLANES:
            return choice  # nothing healthy — emit anyway
        for step in range(1, NUM_PLANES):
            cand = (choice + step) % NUM_PLANES
            if cand not in self.down:
                return cand
        return choice


# --- construction from scenario YAML ---------------------------------------

def policy_from_spec(spec) -> SprayPolicy:
    """Build a policy from a scenario `policy:` value.

    Accepted forms:
      "round_robin"
      "hash5tuple"
      {"weighted": [0.4, 0.3, 0.2, 0.1]}
      {"health_aware": "round_robin"}
      {"health_aware": {"weighted": [...]}}
    """
    if isinstance(spec, str):
        if spec == "round_robin":
            return RoundRobin()
        if spec == "hash5tuple":
            return Hash5Tuple()
        raise ValueError(f"unknown policy: {spec!r}")
    if isinstance(spec, dict) and len(spec) == 1:
        (kind, value), = spec.items()
        if kind == "weighted":
            return Weighted(weights=tuple(float(x) for x in value))
        if kind == "health_aware":
            return HealthAware(inner=policy_from_spec(value))
        raise ValueError(f"unknown policy kind: {kind!r}")
    raise ValueError(f"bad policy spec: {spec!r}")
