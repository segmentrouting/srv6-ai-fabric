"""Topology constants + address/SID helpers for the MRC layer.

Single source of truth inside `mrc/` for everything that comes from the
fabric. Mirrors the patterns in `generate_fabric.py`, `routes.py`, and
`tools/spray.py`. The values here are deliberately re-stated rather than
imported, because:

  1. `tools/spray.py` runs inside Alpine host containers (no path to the
     parent dir at runtime).
  2. `generate_fabric.py` is a generator, not a library — importing it
     would pull in JSON-writing side effects.
  3. Drift is cheap to catch with the unit test that locks the few
     overlapping constants.

If you change a fabric constant here, change it in `generate_fabric.py`
and `routes.py` too, and re-run the lab.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- topology shape ---------------------------------------------------------

NUM_PLANES = 4
NUM_SPINES = 8
NUM_LEAVES = 16            # also = number of hosts per tenant

TENANTS = ("green", "yellow")

# eth0 is mgmt; eth1..eth(NUM_PLANES) are the per-plane uplinks.
PLANE_NIC = lambda plane: f"eth{plane + 1}"
PLANE_NICS = tuple(PLANE_NIC(p) for p in range(NUM_PLANES))

SPRAY_PORT = 9999

# Reference (lo, hi) host-pair -> chosen transit spine. Mirrors
# routes.py:REFERENCE_PAIRS_SPINES. Kept for reproducibility against the
# existing validate.sh / spray.py flow.
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
