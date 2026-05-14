"""Topology constants + address/SID helpers.

Single source of truth for everything that comes from the fabric shape:
plane/spine/leaf counts, tenant names, NIC ordinals, address blocks,
reference-pair spine assignments, and SID-list construction.

The constants are loaded at import time from a topo.yaml file. By
default that's `topologies/4p-8x16/topo.yaml` relative to the repo
root; override via the `SRV6_TOPO` environment variable to drive a
different topology. CLI tools running inside lab host containers have
SRV6_TOPO pre-set by the host-image entrypoint to the bind-mounted
topo.yaml.

If you change a fabric constant here, change it in `topo.yaml` (not
this file). For documentation of the YAML schema see
`topologies/4p-8x16/topo.yaml` itself; for the design rationale of
the address scheme see `docs/topologies/<name>.md`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# --- topology loader --------------------------------------------------------

def _find_default_topo_yaml() -> Path:
    """Locate topologies/4p-8x16/topo.yaml relative to this file.

    srv6_fabric/topo.py is at <root>/srv6_fabric/topo.py, so the default
    topology is two levels up + topologies/4p-8x16/topo.yaml.
    """
    here = Path(__file__).resolve()
    return here.parent.parent / "topologies" / "4p-8x16" / "topo.yaml"


def _load_topo() -> dict:
    """Read the topo.yaml driving this process.

    Order of precedence:
      1. $SRV6_TOPO (must point at a topo.yaml file)
      2. <repo>/topologies/4p-8x16/topo.yaml (development default)

    Falls back to a hardcoded 4p-8x16 dict if neither file is reachable
    AND yaml is missing — keeps `import srv6_fabric.topo` working in
    truly minimal environments (e.g., schema-only tooling).
    """
    path_str = os.environ.get("SRV6_TOPO")
    path = Path(path_str) if path_str else _find_default_topo_yaml()

    try:
        import yaml  # type: ignore[import-not-found]
        with open(path) as f:
            return yaml.safe_load(f)
    except (FileNotFoundError, ImportError):
        # Hardcoded fallback so tests can import without yaml installed
        # and without the file present (e.g., CI bare-clone scenarios).
        return {
            "name": "4p-8x16",
            "planes": 4,
            "spines_per_plane": 8,
            "leaves_per_plane": 16,
            "tenants": ["green", "yellow"],
            "images": {
                "sonic": "docker-sonic-vs:latest",
                "host": "alpine-srv6-scapy:1.0",
            },
            "clab": {"topology_name": "sonic-docker-4p-8x16"},
        }


_TOPO = _load_topo()


# --- topology shape ---------------------------------------------------------

NUM_PLANES: int = _TOPO["planes"]
NUM_SPINES: int = _TOPO["spines_per_plane"]
NUM_LEAVES: int = _TOPO["leaves_per_plane"]            # also = hosts per tenant

# Containerlab topology name (matches `name:` at the top of the
# generated topology.clab.yaml). Used to construct the clab-<topo>-<node>
# container-name fallback in routes.py and netem.py when the user is
# running with the (recommended) `prefix: ""` setting and the short name
# isn't resolvable.
CLAB_TOPOLOGY_NAME: str = _TOPO.get("clab", {}).get(
    "topology_name", "sonic-docker-4p-8x16"
)

TENANTS: tuple[str, ...] = tuple(_TOPO["tenants"])

# eth0 is mgmt; eth1..eth(NUM_PLANES) are the per-plane uplinks.
PLANE_NIC = lambda plane: f"eth{plane + 1}"
PLANE_NICS = tuple(PLANE_NIC(p) for p in range(NUM_PLANES))

SPRAY_PORT = 9999

# Reference (lo, hi) host-pair -> chosen transit spine. Used by the
# spray.py demo and routes.py to pick a deterministic transit spine for
# well-known test pairs. Other pairs fall back to a hash; see
# spine_for() below.
#
# This table is hardcoded for the 4p-8x16 reference design. Topologies
# of other shapes need their own table or will use the hash fallback.
REFERENCE_PAIRS_SPINES: dict[tuple[int, int], int] = {
    (0, 15): 0, (1, 14): 2, (2, 13): 4, (3, 12): 6,
    (4, 11): 1, (5, 10): 3, (6, 9):  5, (7, 8):  7,
}


# --- identity ---------------------------------------------------------------

def host_name(tenant: str, host_id: int) -> str:
    return f"{tenant}-host{host_id:02d}"


def spine_for(src_id: int, dst_id: int) -> int:
    """Transit spine for a given pair. Reference table first, deterministic
    hash fallback."""
    a, b = (src_id, dst_id) if src_id < dst_id else (dst_id, src_id)
    s = REFERENCE_PAIRS_SPINES.get((a, b))
    if s is not None:
        return s
    return (a * NUM_LEAVES + b) % NUM_SPINES


# --- addresses --------------------------------------------------------------

def host_underlay_addr(tenant: str, plane: int, host_id: int) -> str:
    """Per-(host, plane) NIC underlay address — the outer IPv6 source.

    bbbb for green, cccc for yellow.
    """
    _check_tenant(tenant)
    _check_plane(plane)
    _check_host(host_id)
    base = "bbbb" if tenant == "green" else "cccc"
    return f"2001:db8:{base}:{plane:x}{host_id:02x}::2"


def green_anycast_addr(host_id: int) -> str:
    """Plane-independent green tenant address (inner src/dst)."""
    _check_host(host_id)
    return f"2001:db8:bbbb:{host_id:02x}::2"


def yellow_loopback_addr(host_id: int) -> str:
    """Plane-independent yellow tenant address (inner dst after host decap)."""
    _check_host(host_id)
    return f"2001:db8:cccd:{host_id:02x}::1"


def inner_addr(tenant: str, host_id: int) -> str:
    _check_tenant(tenant)
    return (green_anycast_addr(host_id) if tenant == "green"
            else yellow_loopback_addr(host_id))


def leaf_gateway_addr(tenant: str, plane: int, host_id: int) -> str:
    """Address to ping for plane-health probes. Green: anycast leaf gw on
    Vrf-green Ethernet32 (same on every plane). Yellow: per-plane leaf gw on
    Ethernet36 underlay /64."""
    _check_tenant(tenant)
    _check_plane(plane)
    _check_host(host_id)
    if tenant == "green":
        # The green leaf-side gateway is identical across planes (anycast),
        # so the plane argument is informational only.
        return f"2001:db8:bbbb:{host_id:02x}::1"
    return f"2001:db8:cccc:{plane:x}{host_id:02x}::1"


# --- uSID outer destination -------------------------------------------------

def usid_outer_dst(tenant: str, plane: int, spine: int, dst_leaf: int) -> str:
    """Outer IPv6 destination = compressed uSID list.

    Green : fc00:000<P>:f00<S>:e00<L>:d000::
    Yellow: fc00:000<P>:f00<S>:e00<L>:e009:d001::
    """
    _check_tenant(tenant)
    _check_plane(plane)
    _check_spine(spine)
    _check_host(dst_leaf)
    head = f"fc00:000{plane:x}:f00{spine:x}:e00{dst_leaf:x}"
    return f"{head}:d000::" if tenant == "green" else f"{head}:e009:d001::"


# --- flow identity ----------------------------------------------------------

@dataclass(frozen=True)
class FlowKey:
    """Identity tuple used by hash-based policies and the reorder bookkeeper.

    Matches a 5-tuple closely enough for our purposes — protocol is always
    UDP here, src/dst are the inner (plane-independent) tenant addresses.
    """
    src_addr: str
    dst_addr: str
    src_port: int
    dst_port: int

    def hash5(self) -> int:
        # Stable across processes (Python's hash() is salted). FNV-1a 64-bit
        # over the canonical tuple string.
        s = f"{self.src_addr}|{self.dst_addr}|{self.src_port}|{self.dst_port}|17"
        h = 0xcbf29ce484222325
        for b in s.encode():
            h ^= b
            h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
        return h


# --- validation -------------------------------------------------------------

def _check_tenant(v: str) -> None:
    if v not in TENANTS:
        raise ValueError(f"tenant must be one of {TENANTS}, got {v!r}")


def _check_plane(v: int) -> None:
    if not isinstance(v, int) or not (0 <= v < NUM_PLANES):
        raise ValueError(f"plane must be 0..{NUM_PLANES - 1}, got {v!r}")


def _check_spine(v: int) -> None:
    if not isinstance(v, int) or not (0 <= v < NUM_SPINES):
        raise ValueError(f"spine must be 0..{NUM_SPINES - 1}, got {v!r}")


def _check_host(v: int) -> None:
    if not isinstance(v, int) or not (0 <= v < NUM_LEAVES):
        raise ValueError(f"host id must be 0..{NUM_LEAVES - 1}, got {v!r}")
