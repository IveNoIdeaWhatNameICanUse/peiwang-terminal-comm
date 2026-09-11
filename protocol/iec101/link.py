# IEC 60870-5-101 FT1.2 link layer: fixed / variable / single-char frames.
# Control field supports unbalanced (PRM/FCB/FCV) and balanced (DIR/FCB/FCV) modes.
from __future__ import annotations

from typing import Optional

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


def ctrl_balanced(fc: int, dir_master: bool = True, fcb: bool = False, fcv: bool = False,
                  prm: Optional[bool] = None) -> int:
    """Balanced mode control byte: DIR + PRM + FCB + FCV + FC.

    Master-originated frames: DIR=1, PRM=1 -> 0xC0 base (reset 0xC0, link status 0xC9,
    user data 0xF3). Master responses: DIR=1, PRM=0 -> 0x80 base (ACK 0x80, link busy 0x8B).
    Slave frames keep DIR=0; a slave-originated frame sets PRM=1 (e.g. 0x49).
    """
    if prm is None:
        prm = dir_master
    return (0x80 if dir_master else 0x00) | (0x40 if prm else 0x00) \
        | (0x20 if fcb else 0x00) | (0x10 if fcv else 0x00) | (fc & 0x0F)


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


def build_variable(ctrl: int, addr: int, asdu: bytes, addr_size: int = 1,
                   cs_compat: bool = False) -> bytes:
    """Variable frame: 68H | L | L | 68H | C | A | ASDU | CS | 16H.

    # [AGENT_CHANGE_BEGIN] 2026-09-10 101可变帧校验和按用户数据求和
    CS = 字节和(模 256)，仅对 **L 字节用户数据**(控制域+链路地址+ASDU)求和，
    不含 68/L/L/68 与末尾 CS/16（DL/T 634.5101 / IEC 60870-5-2）。
    此前误把 L/L/68 计入，L=12(总召)时与标准巧合一致，L=18(对时)会错。

    cs_compat=True 时再把结果 bit7 取反（个别非标终端）；现场 KW-2200/F30
    抓包为标准算法，默认应 False。
    # [AGENT_CHANGE_END] 2026-09-10 101可变帧校验和按用户数据求和
    """
    a = int(addr).to_bytes(addr_size, "little")
    payload = bytes([ctrl]) + a + asdu
    length = len(payload)
    body = bytes([START_VAR, length, length, START_VAR]) + payload
    # [AGENT_CHANGE_BEGIN] 2026-09-10 101可变帧校验和按用户数据求和
    cs = _cs(payload)
    if cs_compat:
        cs ^= 0x80
    # [AGENT_CHANGE_END] 2026-09-10 101可变帧校验和按用户数据求和
    return body + bytes([cs, END_BYTE])


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
    """Parse one FT1.2 frame -> {kind, ctrl, fc, addr, asdu, cs_ok}."""
    if frame == bytes([SINGLE_CHAR]):
        return {"kind": "single", "ctrl": 0, "fc": -1, "addr": 0, "asdu": b"", "cs_ok": True}
    if frame[0] == START_FIXED:
        c = frame[1]
        addr = int.from_bytes(frame[2:2 + addr_size], "little")
        # [AGENT_CHANGE_BEGIN] 2026-09-10 101可变帧校验和按用户数据求和
        cs_ok = len(frame) >= 2 and frame[-2] == _cs(frame[1:-2])
        # [AGENT_CHANGE_END] 2026-09-10 101可变帧校验和按用户数据求和
        return {"kind": "fixed", "ctrl": c, "addr": addr, "asdu": b"", "cs_ok": cs_ok,
                **parse_ctrl(c)}
    # variable
    c = frame[4]
    addr = int.from_bytes(frame[5:5 + addr_size], "little")
    asdu = frame[5 + addr_size:-2]
    # [AGENT_CHANGE_BEGIN] 2026-09-10 101可变帧校验和按用户数据求和
    user = frame[4:-2]  # 控制域+地址+ASDU
    cs_ok = len(frame) >= 2 and frame[-2] in (_cs(user), _cs(user) ^ 0x80)
    # [AGENT_CHANGE_END] 2026-09-10 101可变帧校验和按用户数据求和
    return {"kind": "variable", "ctrl": c, "addr": addr, "asdu": asdu, "cs_ok": cs_ok,
            **parse_ctrl(c)}
