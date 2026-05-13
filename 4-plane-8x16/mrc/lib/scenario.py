"""Scenario YAML validator.

Validates the full scenario shape laid out in mrc/README.md:

    name: <string>
    description: <string>            # optional
    flows:
      - pairs: <named-set | list of {tenant,src,dst}>
        policy: <policy-spec>        # passed to policy.policy_from_spec
        rate: <int|str>              # e.g. 1000 or "1000pps"
        duration: <str>              # e.g. "30s", "500ms"
    faults:                          # optional
      - kind: netem
        target: <target-string>
        spec: <netem-spec-string>
    report:                          # optional
      out: <path>

The validator is intentionally strict — unknown keys raise. This catches
typos like `paris:` vs `pairs:` before a long lab run.

Output is a `Scenario` dataclass tree. Importing this module does NOT need
PyYAML; only `from_yaml_file()` / `from_yaml_string()` do, and they raise
a clean error if it's missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .netem import normalize_spec, parse_target
from .policy import policy_from_spec
from .topo import NUM_LEAVES, TENANTS, host_name


# --- public dataclasses -----------------------------------------------------

@dataclass(frozen=True)
class FlowPair:
    """One src/dst pair to be run as a flow."""
    tenant: str
    src: int
    dst: int

    def src_host(self) -> str:
        return host_name(self.tenant, self.src)

    def dst_host(self) -> str:
        return host_name(self.tenant, self.dst)


@dataclass(frozen=True)
class FlowSpec:
    """One `flows:` entry, expanded to its concrete pair list."""
    pairs: tuple[FlowPair, ...]
    policy_spec: Any                  # raw spec; runner calls policy_from_spec
    rate_pps: int
    duration_s: float
    # The original `policy:` value, kept verbatim so reports can show it
    # without re-encoding.
    policy_label: str = field(default="")


@dataclass(frozen=True)
class FaultSpec:
    """One `faults:` entry. Resolved-but-not-applied."""
    kind: str                          # currently only "netem"
    target: str
    spec: str


@dataclass(frozen=True)
class ReportSpec:
    out: str | None = None


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    flows: tuple[FlowSpec, ...]
    faults: tuple[FaultSpec, ...]
    report: ReportSpec


# --- named pair sets --------------------------------------------------------

# Mirrors the reference pairs in topo.REFERENCE_PAIRS_SPINES — same 8 pairs,
# laid out as 16 hosts. Useful as a one-liner `pairs: green-pairs-8` in YAMLs.
_REFERENCE_PAIRS = [
    (0, 15), (1, 14), (2, 13), (3, 12),
    (4, 11), (5, 10), (6, 9),  (7, 8),
]

NAMED_PAIR_SETS: dict[str, list[FlowPair]] = {
    "green-pairs-8": [FlowPair("green", a, b) for a, b in _REFERENCE_PAIRS],
    "yellow-pairs-8": [FlowPair("yellow", a, b) for a, b in _REFERENCE_PAIRS],
    # One pair only — handy for smoke tests.
    "green-00-15": [FlowPair("green", 0, 15)],
    "yellow-00-15": [FlowPair("yellow", 0, 15)],
}


# --- error type -------------------------------------------------------------

class ScenarioError(ValueError):
    """Raised for any malformed scenario. Always includes the dotted path."""

    def __init__(self, path: str, msg: str) -> None:
        super().__init__(f"{path}: {msg}")
        self.path = path


# --- top-level entry --------------------------------------------------------

def validate(doc: Any) -> Scenario:
    """Validate a parsed YAML document and return a Scenario tree.

    Raises ScenarioError on the first problem.
    """
    if not isinstance(doc, dict):
        raise ScenarioError("$", "scenario must be a mapping at top level")

    _require_keys(doc, "$", required={"name", "flows"},
                  optional={"description", "faults", "report"})

    name = _require_str(doc, "$.name")
    description = _opt_str(doc, "$.description", default="")

    flows_raw = doc["flows"]
    if not isinstance(flows_raw, list) or not flows_raw:
        raise ScenarioError("$.flows", "must be a non-empty list")
    flows = tuple(_validate_flow(item, f"$.flows[{i}]")
                  for i, item in enumerate(flows_raw))

    faults_raw = doc.get("faults") or []
    if not isinstance(faults_raw, list):
        raise ScenarioError("$.faults", "must be a list if present")
    faults = tuple(_validate_fault(item, f"$.faults[{i}]")
                   for i, item in enumerate(faults_raw))

    report = _validate_report(doc.get("report"), "$.report")

    return Scenario(
        name=name,
        description=description,
        flows=flows,
        faults=faults,
        report=report,
    )


def from_yaml_string(s: str) -> Scenario:
    yaml = _load_pyyaml()
    return validate(yaml.safe_load(s))


def from_yaml_file(path: str | Path) -> Scenario:
    yaml = _load_pyyaml()
    with open(path, "r") as f:
        return validate(yaml.safe_load(f))


# --- flow ------------------------------------------------------------------

def _validate_flow(item: Any, path: str) -> FlowSpec:
    if not isinstance(item, dict):
        raise ScenarioError(path, "must be a mapping")
    _require_keys(item, path,
                  required={"pairs", "policy", "rate", "duration"})

    pairs = _resolve_pairs(item["pairs"], f"{path}.pairs")
    policy_raw = item["policy"]
    # Validate policy spec by attempting to build it — but don't keep
    # the instance (FlowSpec stores the raw spec for runner-side rebuild).
    try:
        policy_from_spec(policy_raw)
    except ValueError as e:
        raise ScenarioError(f"{path}.policy", str(e)) from None

    rate_pps = _parse_rate(item["rate"], f"{path}.rate")
    duration_s = _parse_duration(item["duration"], f"{path}.duration")

    return FlowSpec(
        pairs=pairs,
        policy_spec=policy_raw,
        rate_pps=rate_pps,
        duration_s=duration_s,
        policy_label=_policy_label(policy_raw),
    )


def _resolve_pairs(value: Any, path: str) -> tuple[FlowPair, ...]:
    if isinstance(value, str):
        named = NAMED_PAIR_SETS.get(value)
        if named is None:
            raise ScenarioError(
                path,
                f"unknown named pair set {value!r}; known: "
                f"{sorted(NAMED_PAIR_SETS)}",
            )
        return tuple(named)
    if isinstance(value, list):
        if not value:
            raise ScenarioError(path, "pair list is empty")
        out: list[FlowPair] = []
        for i, entry in enumerate(value):
            ep = f"{path}[{i}]"
            if not isinstance(entry, dict):
                raise ScenarioError(ep, "pair entry must be a mapping")
            _require_keys(entry, ep, required={"tenant", "src", "dst"})
            tenant = _require_choice(entry, f"{ep}.tenant", TENANTS)
            src = _require_host_id(entry, f"{ep}.src")
            dst = _require_host_id(entry, f"{ep}.dst")
            if src == dst:
                raise ScenarioError(ep, "src and dst must differ")
            out.append(FlowPair(tenant=tenant, src=src, dst=dst))
        return tuple(out)
    raise ScenarioError(path, "must be a named set string or list of {tenant,src,dst}")


def _policy_label(spec: Any) -> str:
    if isinstance(spec, str):
        return spec
    if isinstance(spec, dict) and len(spec) == 1:
        (k, v), = spec.items()
        return f"{k}({_policy_label(v) if not isinstance(v, list) else 'weights'})"
    return repr(spec)


# --- fault -----------------------------------------------------------------

def _validate_fault(item: Any, path: str) -> FaultSpec:
    if not isinstance(item, dict):
        raise ScenarioError(path, "must be a mapping")
    _require_keys(item, path, required={"kind", "target", "spec"})
    kind = _require_str(item, f"{path}.kind")
    if kind != "netem":
        raise ScenarioError(f"{path}.kind",
                            f"unsupported fault kind {kind!r}; only 'netem' is implemented")
    target = _require_str(item, f"{path}.target")
    spec = _require_str(item, f"{path}.spec")
    # Validate by parsing — surfaces typos at scenario-load time.
    try:
        parse_target(target)
    except ValueError as e:
        raise ScenarioError(f"{path}.target", str(e)) from None
    try:
        normalize_spec(spec)
    except ValueError as e:
        raise ScenarioError(f"{path}.spec", str(e)) from None
    return FaultSpec(kind=kind, target=target, spec=spec)


# --- report ----------------------------------------------------------------

def _validate_report(value: Any, path: str) -> ReportSpec:
    if value is None:
        return ReportSpec()
    if not isinstance(value, dict):
        raise ScenarioError(path, "must be a mapping if present")
    _require_keys(value, path, required=set(), optional={"out"})
    out = value.get("out")
    if out is not None and not isinstance(out, str):
        raise ScenarioError(f"{path}.out", "must be a string path")
    return ReportSpec(out=out)


# --- primitive helpers ------------------------------------------------------

def _require_keys(d: dict, path: str, *,
                  required: set[str],
                  optional: set[str] | None = None) -> None:
    keys = set(d.keys())
    missing = required - keys
    if missing:
        raise ScenarioError(path, f"missing required key(s): {sorted(missing)}")
    allowed = required | (optional or set())
    extra = keys - allowed
    if extra:
        raise ScenarioError(path, f"unknown key(s): {sorted(extra)}")


def _require_str(d: dict, path: str) -> str:
    leaf = path.rsplit(".", 1)[-1]
    v = d[leaf]
    if not isinstance(v, str) or not v:
        raise ScenarioError(path, "must be a non-empty string")
    return v


def _opt_str(d: dict, path: str, *, default: str) -> str:
    leaf = path.rsplit(".", 1)[-1]
    v = d.get(leaf, default)
    if not isinstance(v, str):
        raise ScenarioError(path, "must be a string if present")
    return v


def _require_choice(d: dict, path: str, choices) -> str:
    leaf = path.rsplit(".", 1)[-1]
    v = d[leaf]
    if v not in choices:
        raise ScenarioError(path, f"must be one of {tuple(choices)}, got {v!r}")
    return v


def _require_host_id(d: dict, path: str) -> int:
    leaf = path.rsplit(".", 1)[-1]
    v = d[leaf]
    if not isinstance(v, int) or not 0 <= v < NUM_LEAVES:
        raise ScenarioError(
            path, f"must be int 0..{NUM_LEAVES - 1}, got {v!r}"
        )
    return v


_RATE_RE = re.compile(r"^\s*(\d+)\s*(?:pps?)?\s*$", re.I)


def _parse_rate(v: Any, path: str) -> int:
    if isinstance(v, int) and v > 0:
        return v
    if isinstance(v, str):
        m = _RATE_RE.match(v)
        if m:
            return int(m.group(1))
    raise ScenarioError(path, f"must be a positive int or '<N>pps', got {v!r}")


_DUR_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s)?\s*$", re.I)


def _parse_duration(v: Any, path: str) -> float:
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    if isinstance(v, str):
        m = _DUR_RE.match(v)
        if m:
            val = float(m.group(1))
            return val / 1000.0 if (m.group(2) or "").lower() == "ms" else val
    raise ScenarioError(path, f"must be a duration like '30s' or '500ms', got {v!r}")


def _load_pyyaml():
    try:
        import yaml  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "PyYAML not installed; needed by scenario.from_yaml_*. "
            "Install with: pip3 install pyyaml"
        ) from e
    return yaml
