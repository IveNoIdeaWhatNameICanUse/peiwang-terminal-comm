# IEC 60870-5-101 FT1.2 link layer: fixed / variable / single-char frames.
# Control field supports unbalanced (PRM/FCB/FCV) and balanced (DIR/FCB/FCV) modes.
from __future__ import annotations

START_FIXED = 0x10
START_VAR = 0x68
END_BYTE = 0x16
SINGLE_CHAR = 0xE5

# ---- primary (PRM=1) function codes, unbalanced ----
FC_RESET_LINK = 0
FC_RESET_USER = 1
FC_TEST_LINK = 2
FC_USER_DATA = 3
FC_USER_DATA_CONF = 4
FC_REQ_LINK_STATUS = 9
FC_REQ_LEVEL1 = 10
FC_REQ_LEVEL2 = 11

# ---- secondary (PRM=0) function codes ----
FC_ACK = 0
FC_NACK = 1
FC_DATA = 8
FC_NO_DATA = 9
FC_LINK_BUSY = 11


def ctrl_primary(fc: int, fcb: bool = False, fcv: bool = False) -> int:
    """Unbalanced primary direction control byte (PRM=1)."""
    return 0x40 | (0x20 if fcb else 0) | (0x10 if fcv else 0) | (fc & 0x0F)


def ctrl_secondary(fc: int, acd: bool = False, dfc: bool = False) -> int:
    """Unbalanced secondary direction control byte (PRM=0)."""
    return (0x20 if acd else 0) | (0x10 if dfc else 0) | (fc & 0x0F)


def ctrl_balanced(fc: int, dir_master: bool = True, fcb: bool = False, fcv: bool = False) -> int:
    """Balanced mode control byte: DIR + PRM + FCB + FCV + FC.

    Master-originated frames set DIR=1 and PRM=1 -> 0xC0 base (matches field
    captures: reset link 0xC0, request link status 0xC9, user data 0xF3).
    Slave-originated frames keep both bits clear.
    """
    base = 0xC0 if dir_master else 0x00
    return base | (0x20 if fcb else 0) | (0x10 if fcv else 0) | (fc & 0x0F)


def parse_ctrl(c: int) -> dict:
    return {
        "raw": c,
        "prm": bool(c & 0x40),     # unbalanced: PRM ; balanced: reserved 0
        "dir": bool(c & 0x80),     # balanced: DIR (1=master->slave)
        "fcb": bool(c & 0x20),     # or ACD for secondary
        "fcv": bool(c & 0x10),     # or DFC for secondary
        "acd": bool(c & 0x20),     # secondary only
        "dfc": bool(c & 0x10),     # secondary only
        "fc": c & 0x0F,
    }


def _cs(data: bytes) -> int:
    return sum(data) & 0xFF


def build_fixed(ctrl: int, addr: int, addr_size: int = 1) -> bytes:
    """Fixed frame: 10H | C | A | CS | 16H (A = 1 or 2 bytes little-endian)."""
    a = int(addr).to_bytes(addr_size, "little")
    body = bytes([ctrl]) + a
    return bytes([START_FIXED]) + body + bytes([_cs(body), END_BYTE])


def build_single() -> bytes:
    return bytes([SINGLE_CHAR])


def build_variable(ctrl: int, addr: int, asdu: bytes, addr_size: int = 1) -> bytes:
    """Variable frame: 68H | L | L | 68H | C | A | ASDU | CS | 16H."""
    a = int(addr).to_bytes(addr_size, "little")
    payload = bytes([ctrl]) + a + asdu
    length = len(payload)
    body = bytes([START_VAR, length, length, START_VAR]) + payload
    return body + bytes([_cs(body), END_BYTE])


def feed(buf: bytearray, data: bytes, addr_size: int = 1):
    """Append bytes and yield complete frames (bytes)."""
    buf.extend(data)
    while True:
        if not buf:
            return
        start = buf[0]
        if start == SINGLE_CHAR:
            del buf[0]
            yield bytes([SINGLE_CHAR])
            continue
        if start == START_FIXED:
            # 10H C A.. CS 16H
            total = 4 + addr_size
            if len(buf) < total:
                return
            frame = bytes(buf[:total])
            if frame[total - 1] != END_BYTE:
                del buf[0]
                continue
            del buf[:total]
            yield frame
            continue
        if start == START_VAR:
            if len(buf) < 4:
                return
            length = buf[1]
            total = 4 + length + 2
            if len(buf) < total:
                return
            frame = bytes(buf[:total])
            if frame[total - 1] != END_BYTE:
                del buf[0]
                continue
            del buf[:total]
            yield frame
            continue
        del buf[0]  # resync


def parse_frame(frame: bytes, addr_size: int = 1) -> dict:
    """Parse one FT1.2 frame -> {kind, ctrl, fc, addr, asdu}."""
    if frame == bytes([SINGLE_CHAR]):
        return {"kind": "single", "ctrl": 0, "fc": -1, "addr": 0, "asdu": b""}
    if frame[0] == START_FIXED:
        c = frame[1]
        addr = int.from_bytes(frame[2:2 + addr_size], "little")
        return {"kind": "fixed", "ctrl": c, "addr": addr, "asdu": b"", **parse_ctrl(c)}
    # variable
    c = frame[4]
    addr = int.from_bytes(frame[5:5 + addr_size], "little")
    asdu = frame[5 + addr_size:-2]
    return {"kind": "variable", "ctrl": c, "addr": addr, "asdu": asdu, **parse_ctrl(c)}
