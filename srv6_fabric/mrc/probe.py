"""MRC probe / loss-report packet encode + decode.

Three new packet types, all carried as UDP/IPv6 payloads. The IPv6 outer
+ optional SRH is built by the runner exactly the same way as for spray
data — this module only knows about the bytes after the UDP header.

| Packet         | UDP dport            | Encoder           | Decoder           |
|----------------|----------------------|-------------------|-------------------|
| PROBE          | SPRAY_PROBE_PORT     | encode_probe      | decode_probe      |
| PROBE_REPLY    | SPRAY_PROBE_PORT     | encode_probe_reply| decode_probe_reply|
| LOSS_REPORT    | SPRAY_REPORT_PORT    | encode_loss_report| decode_loss_report|

Wire format (all big-endian / network byte order):

  PROBE / PROBE_REPLY:
      magic       u8   = 0xA5 (PROBE) | 0xA6 (PROBE_REPLY)
      version     u8   = 1
      req_id      u16
      plane_id    u8
      _reserved   u8   = 0
      tx_ns       u64       (sender's monotonic_ns at TX time, echoed in reply)
      svc_time_ns u64       (PROBE: 0; PROBE_REPLY: responder service time)

  LOSS_REPORT:
      magic       u8   = 0xA7
      version     u8   = 1
      window_id   u16       (monotonically increasing per (sender, receiver))
      num_planes  u16
      _reserved   u16  = 0
      then num_planes × per-plane records:
          plane_id    u8
          _reserved   u8 = 0
          _reserved   u16 = 0
          seen        u32
          expected    u32
          max_gap     u32

We deliberately use a per-message magic byte so that an RX socket that
also sees spray data (in the unlikely event of port collision) can
demux defensively. Version byte is reserved for future protocol bumps.

The codecs are pure functions — no scapy, no sockets, no clocks. Same
test discipline as srv6_fabric/policy.py.

See `docs/design-mrc.md` "Probe wire format" for the design rationale.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


# --- magic / versions ------------------------------------------------------

PROBE_VERSION = 1

_MAGIC_PROBE = 0xA5
_MAGIC_PROBE_REPLY = 0xA6
_MAGIC_LOSS_REPORT = 0xA7


# struct format strings (network byte order)
# PROBE / PROBE_REPLY: magic, version, req_id, plane_id, _rsv, tx_ns, svc_time_ns
_PROBE_FMT = "!BBHBBQQ"
_PROBE_SIZE = struct.calcsize(_PROBE_FMT)   # 22 bytes

# LOSS_REPORT header: magic, version, window_id, num_planes, _rsv
_LOSS_HDR_FMT = "!BBHHH"
_LOSS_HDR_SIZE = struct.calcsize(_LOSS_HDR_FMT)  # 8 bytes
# per-plane record: plane_id, _rsv, _rsv16, seen, expected, max_gap
_LOSS_REC_FMT = "!BBHIII"
_LOSS_REC_SIZE = struct.calcsize(_LOSS_REC_FMT)  # 16 bytes


# --- exceptions ------------------------------------------------------------

class ProbeDecodeError(ValueError):
    """Raised when a packet doesn't conform to the expected layout."""


# --- dataclasses for decoded payloads --------------------------------------

@dataclass(frozen=True)
class Probe:
    """A decoded PROBE packet."""
    req_id: int
    plane_id: int
    tx_ns: int

    def __post_init__(self) -> None:
        _check_u16(self.req_id, "req_id")
        _check_u8(self.plane_id, "plane_id")
        _check_u64(self.tx_ns, "tx_ns")


@dataclass(frozen=True)
class ProbeReply:
    """A decoded PROBE_REPLY packet.

    `tx_ns` is echoed from the matching PROBE; sender computes
    `rtt_ns = now_ns - tx_ns` (optionally minus `svc_time_ns` if it
    chooses to factor out responder time, matching OCP's adj_svc_time
    bit).
    """
    req_id: int
    plane_id: int
    tx_ns: int
    svc_time_ns: int

    def __post_init__(self) -> None:
        _check_u16(self.req_id, "req_id")
        _check_u8(self.plane_id, "plane_id")
        _check_u64(self.tx_ns, "tx_ns")
        _check_u64(self.svc_time_ns, "svc_time_ns")


@dataclass(frozen=True)
class PlaneLossRecord:
    """One per-plane entry inside a LOSS_REPORT."""
    plane_id: int
    seen: int
    expected: int
    max_gap: int

    def __post_init__(self) -> None:
        _check_u8(self.plane_id, "plane_id")
        _check_u32(self.seen, "seen")
        _check_u32(self.expected, "expected")
        _check_u32(self.max_gap, "max_gap")


@dataclass(frozen=True)
class LossReport:
    """A decoded LOSS_REPORT packet.

    `planes` is in wire order; receivers should treat duplicate plane_id
    entries as the latest-wins (we don't enforce uniqueness on decode).
    """
    window_id: int
    planes: tuple[PlaneLossRecord, ...]

    def __post_init__(self) -> None:
        _check_u16(self.window_id, "window_id")
        if not isinstance(self.planes, tuple):
            raise TypeError("planes must be a tuple")


# --- encoders --------------------------------------------------------------

def encode_probe(req_id: int, plane_id: int, tx_ns: int) -> bytes:
    """Build the UDP-payload bytes for a PROBE packet.

    `tx_ns` is whatever the caller's monotonic clock returned. The
    receiver echoes it verbatim in the matching PROBE_REPLY; the sender
    subtracts it from a later clock read to get RTT.
    """
    _check_u16(req_id, "req_id")
    _check_u8(plane_id, "plane_id")
    _check_u64(tx_ns, "tx_ns")
    return struct.pack(
        _PROBE_FMT,
        _MAGIC_PROBE, PROBE_VERSION,
        req_id, plane_id, 0,
        tx_ns, 0,
    )


def encode_probe_reply(
    req_id: int, plane_id: int, tx_ns: int, svc_time_ns: int,
) -> bytes:
    """Build the UDP-payload bytes for a PROBE_REPLY packet.

    `tx_ns` MUST be the value from the matching PROBE so the sender can
    pair them up; `svc_time_ns` is the responder's measured
    "request-arrival → reply-emit" duration (may be 0 if not measured).
    """
    _check_u16(req_id, "req_id")
    _check_u8(plane_id, "plane_id")
    _check_u64(tx_ns, "tx_ns")
    _check_u64(svc_time_ns, "svc_time_ns")
    return struct.pack(
        _PROBE_FMT,
        _MAGIC_PROBE_REPLY, PROBE_VERSION,
        req_id, plane_id, 0,
        tx_ns, svc_time_ns,
    )


def encode_loss_report(
    window_id: int,
    planes: list[PlaneLossRecord] | tuple[PlaneLossRecord, ...],
) -> bytes:
    """Build the UDP-payload bytes for a LOSS_REPORT packet.

    Empty `planes` is allowed (an empty report still carries a
    window_id, so the sender can confirm the receiver is alive).
    """
    _check_u16(window_id, "window_id")
    if len(planes) > 0xFFFF:
        raise ValueError(f"too many plane records: {len(planes)} > 65535")
    out = bytearray(
        struct.pack(
            _LOSS_HDR_FMT,
            _MAGIC_LOSS_REPORT, PROBE_VERSION,
            window_id, len(planes), 0,
        )
    )
    for rec in planes:
        if not isinstance(rec, PlaneLossRecord):
            raise TypeError(
                f"loss report entries must be PlaneLossRecord, got {type(rec)}"
            )
        out += struct.pack(
            _LOSS_REC_FMT,
            rec.plane_id, 0, 0,
            rec.seen, rec.expected, rec.max_gap,
        )
    return bytes(out)


# --- decoders --------------------------------------------------------------

def decode_probe(payload: bytes) -> Probe:
    p, r = _decode_probe_or_reply(payload, expect_magic=_MAGIC_PROBE)
    assert r == 0  # PROBE has svc_time_ns == 0
    return p


def decode_probe_reply(payload: bytes) -> ProbeReply:
    _, _ = _decode_probe_or_reply.__annotations__, None  # silence linters
    if len(payload) < _PROBE_SIZE:
        raise ProbeDecodeError(
            f"probe_reply payload too short: {len(payload)} < {_PROBE_SIZE}"
        )
    magic, version, req_id, plane_id, _rsv, tx_ns, svc_time_ns = \
        struct.unpack(_PROBE_FMT, payload[:_PROBE_SIZE])
    if magic != _MAGIC_PROBE_REPLY:
        raise ProbeDecodeError(
            f"expected PROBE_REPLY magic 0x{_MAGIC_PROBE_REPLY:02x}, "
            f"got 0x{magic:02x}"
        )
    if version != PROBE_VERSION:
        raise ProbeDecodeError(
            f"unsupported probe protocol version {version}"
        )
    return ProbeReply(
        req_id=req_id,
        plane_id=plane_id,
        tx_ns=tx_ns,
        svc_time_ns=svc_time_ns,
    )


def decode_loss_report(payload: bytes) -> LossReport:
    if len(payload) < _LOSS_HDR_SIZE:
        raise ProbeDecodeError(
            f"loss_report payload too short: {len(payload)} < {_LOSS_HDR_SIZE}"
        )
    magic, version, window_id, num_planes, _rsv = struct.unpack(
        _LOSS_HDR_FMT, payload[:_LOSS_HDR_SIZE],
    )
    if magic != _MAGIC_LOSS_REPORT:
        raise ProbeDecodeError(
            f"expected LOSS_REPORT magic 0x{_MAGIC_LOSS_REPORT:02x}, "
            f"got 0x{magic:02x}"
        )
    if version != PROBE_VERSION:
        raise ProbeDecodeError(
            f"unsupported loss-report protocol version {version}"
        )
    expected_len = _LOSS_HDR_SIZE + num_planes * _LOSS_REC_SIZE
    if len(payload) < expected_len:
        raise ProbeDecodeError(
            f"loss_report truncated: got {len(payload)}B, "
            f"expected {expected_len}B for {num_planes} plane records"
        )
    planes: list[PlaneLossRecord] = []
    off = _LOSS_HDR_SIZE
    for _ in range(num_planes):
        plane_id, _r1, _r2, seen, expected, max_gap = struct.unpack(
            _LOSS_REC_FMT, payload[off:off + _LOSS_REC_SIZE],
        )
        planes.append(PlaneLossRecord(
            plane_id=plane_id, seen=seen,
            expected=expected, max_gap=max_gap,
        ))
        off += _LOSS_REC_SIZE
    return LossReport(window_id=window_id, planes=tuple(planes))


def _decode_probe_or_reply(
    payload: bytes, *, expect_magic: int,
) -> tuple[Probe, int]:
    """Shared decode for PROBE; returns (Probe, svc_time_ns)."""
    if len(payload) < _PROBE_SIZE:
        raise ProbeDecodeError(
            f"probe payload too short: {len(payload)} < {_PROBE_SIZE}"
        )
    magic, version, req_id, plane_id, _rsv, tx_ns, svc_time_ns = \
        struct.unpack(_PROBE_FMT, payload[:_PROBE_SIZE])
    if magic != expect_magic:
        raise ProbeDecodeError(
            f"expected magic 0x{expect_magic:02x}, got 0x{magic:02x}"
        )
    if version != PROBE_VERSION:
        raise ProbeDecodeError(
            f"unsupported probe protocol version {version}"
        )
    return (
        Probe(req_id=req_id, plane_id=plane_id, tx_ns=tx_ns),
        svc_time_ns,
    )


# --- range checks ----------------------------------------------------------

def _check_u8(v: int, name: str) -> None:
    if not isinstance(v, int) or v < 0 or v > 0xFF:
        raise ValueError(f"{name} must be uint8, got {v!r}")


def _check_u16(v: int, name: str) -> None:
    if not isinstance(v, int) or v < 0 or v > 0xFFFF:
        raise ValueError(f"{name} must be uint16, got {v!r}")


def _check_u32(v: int, name: str) -> None:
    if not isinstance(v, int) or v < 0 or v > 0xFFFFFFFF:
        raise ValueError(f"{name} must be uint32, got {v!r}")


def _check_u64(v: int, name: str) -> None:
    if not isinstance(v, int) or v < 0 or v > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"{name} must be uint64, got {v!r}")


# --- module-level constants for consumers ----------------------------------

__all__ = [
    "PROBE_VERSION",
    "Probe", "ProbeReply", "PlaneLossRecord", "LossReport",
    "encode_probe", "encode_probe_reply", "encode_loss_report",
    "decode_probe", "decode_probe_reply", "decode_loss_report",
    "ProbeDecodeError",
]
