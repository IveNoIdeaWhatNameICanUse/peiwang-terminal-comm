# [AGENT_CHANGE_BEGIN] 2026-09-07 104-MVP协议编解码
"""IEC 104 APCI / ASDU 编解码（MVP 常用类型）。"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional, Tuple

from .const import START_BYTE, TYPE_NAMES, TypeId


def build_u_frame(start_dt: bool = False, stop_dt: bool = False, testfr: bool = False, con: bool = False) -> bytes:
    ctrl = 0x03
    if start_dt and not con:
        ctrl = 0x07
    elif start_dt and con:
        ctrl = 0x0B
    elif stop_dt and not con:
        ctrl = 0x13
    elif stop_dt and con:
        ctrl = 0x23
    elif testfr and not con:
        ctrl = 0x43
    elif testfr and con:
        ctrl = 0x83
    return bytes([START_BYTE, 0x04, ctrl, 0x00, 0x00, 0x00])


def build_s_frame(ack_seq: int) -> bytes:
    v = (ack_seq & 0x7FFF) << 1
    return bytes([START_BYTE, 0x04, 0x01, 0x00, v & 0xFF, (v >> 8) & 0xFF])


def build_i_frame(send_seq: int, recv_seq: int, asdu: bytes) -> bytes:
    length = 4 + len(asdu)
    ns = (send_seq & 0x7FFF) << 1
    nr = (recv_seq & 0x7FFF) << 1
    apci = bytes(
        [
            START_BYTE,
            length & 0xFF,
            ns & 0xFF,
            (ns >> 8) & 0xFF,
            nr & 0xFF,
            (nr >> 8) & 0xFF,
        ]
    )
    return apci + asdu


def parse_apci(frame: bytes) -> Tuple[str, dict]:
    if len(frame) < 6 or frame[0] != START_BYTE:
        raise ValueError("非法 APCI")
    b1 = frame[2]
    if (b1 & 0x01) == 0:
        ns = ((frame[3] << 8) | frame[2]) >> 1
        nr = ((frame[5] << 8) | frame[4]) >> 1
        return "I", {"ns": ns, "nr": nr, "asdu": frame[6:]}
    if (b1 & 0x03) == 0x01:
        nr = ((frame[5] << 8) | frame[4]) >> 1
        return "S", {"nr": nr}
    # U
    kind = "U"
    detail = {}
    if b1 & 0x04:
        detail["start_dt"] = bool(b1 & 0x08 == 0)  # STARTDT act: 0x07
    if b1 == 0x07:
        detail = {"start_dt_act": True}
    elif b1 == 0x0B:
        detail = {"start_dt_con": True}
    elif b1 == 0x13:
        detail = {"stop_dt_act": True}
    elif b1 == 0x23:
        detail = {"stop_dt_con": True}
    elif b1 == 0x43:
        detail = {"testfr_act": True}
    elif b1 == 0x83:
        detail = {"testfr_con": True}
    else:
        detail = {"ctrl": b1}
    return kind, detail


def _ioa_bytes(ioa: int, size: int = 3) -> bytes:
    return ioa.to_bytes(size, "little")


def encode_asdu_header(
    type_id: int,
    vsq: int,
    cot: int,
    ca: int,
    oa: int = 0,
    cot_size: int = 2,
    ca_size: int = 2,
) -> bytes:
    cot_val = cot & 0x3F
    if cot_size == 2:
        cot_bytes = bytes([cot_val, oa & 0xFF])
    else:
        cot_bytes = bytes([cot_val])
    ca_bytes = ca.to_bytes(ca_size, "little")
    return bytes([type_id & 0xFF, vsq & 0xFF]) + cot_bytes + ca_bytes


def build_interrogation(ca: int, qoi: int = 20, oa: int = 0) -> bytes:
    header = encode_asdu_header(TypeId.C_IC_NA_1, 1, 6, ca, oa)
    return header + _ioa_bytes(0) + bytes([qoi & 0xFF])


def build_clock_sync(ca: int, dt: Optional[datetime] = None, oa: int = 0) -> bytes:
    dt = dt or datetime.now()
    header = encode_asdu_header(TypeId.C_CS_NA_1, 1, 6, ca, oa)
    # CP56Time2a
    ms = dt.hour * 3600000 + dt.minute * 60000 + dt.second * 1000 + dt.microsecond // 1000
    # standard: milliseconds (0..59999) in 16bit, then minutes, hours, day/dow, month, year
    msec = dt.second * 1000 + dt.microsecond // 1000
    time_bytes = struct.pack(
        "<HBBBBB",
        msec,
        dt.minute & 0x3F,
        dt.hour & 0x1F,
        dt.day & 0x1F,
        dt.month & 0x0F,
        (dt.year % 100) & 0x7F,
    )
    return header + _ioa_bytes(0) + time_bytes


def build_single_command(ca: int, ioa: int, on: bool, select: bool, oa: int = 0) -> bytes:
    header = encode_asdu_header(TypeId.C_SC_NA_1, 1, 6, ca, oa)
    sco = (1 if on else 0) | (0x80 if select else 0x00)
    return header + _ioa_bytes(ioa) + bytes([sco])


def build_double_command(ca: int, ioa: int, state: int, select: bool, oa: int = 0) -> bytes:
    """state: 1=OFF, 2=ON"""
    header = encode_asdu_header(TypeId.C_DC_NA_1, 1, 6, ca, oa)
    dco = (state & 0x03) | (0x80 if select else 0x00)
    return header + _ioa_bytes(ioa) + bytes([dco])


def build_setpoint_float(ca: int, ioa: int, value: float, select: bool, oa: int = 0) -> bytes:
    header = encode_asdu_header(TypeId.C_SE_NC_1, 1, 6, ca, oa)
    qos = 0x80 if select else 0x00
    return header + _ioa_bytes(ioa) + struct.pack("<f", float(value)) + bytes([qos])


def build_setpoint_normalized(ca: int, ioa: int, value: float, select: bool, oa: int = 0) -> bytes:
    """value: -1.0 .. 1.0 -> int16"""
    header = encode_asdu_header(TypeId.C_SE_NA_1, 1, 6, ca, oa)
    nva = int(max(-1.0, min(1.0, value)) * 32767)
    qos = 0x80 if select else 0x00
    return header + _ioa_bytes(ioa) + struct.pack("<h", nva) + bytes([qos])


@dataclass
class InformationObject:
    ioa: int
    value: Any
    quality: int = 0
    extra: dict = field(default_factory=dict)


@dataclass
class AsduMessage:
    type_id: int
    vsq: int
    cot: int
    oa: int
    ca: int
    objects: List[InformationObject] = field(default_factory=list)
    raw: bytes = b""

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type_id, f"TYPE_{self.type_id}")


def decode_asdu(data: bytes, cot_size: int = 2, ca_size: int = 2, ioa_size: int = 3) -> AsduMessage:
    if len(data) < 2 + cot_size + ca_size:
        raise ValueError("ASDU 过短")
    type_id = data[0]
    vsq = data[1]
    idx = 2
    if cot_size == 2:
        cot = data[idx] & 0x3F
        oa = data[idx + 1]
        idx += 2
    else:
        cot = data[idx] & 0x3F
        oa = 0
        idx += 1
    ca = int.from_bytes(data[idx : idx + ca_size], "little")
    idx += ca_size
    sq = (vsq & 0x80) != 0
    n = vsq & 0x7F
    objects: List[InformationObject] = []
    payload = data[idx:]

    def read_ioa(buf: bytes, off: int) -> Tuple[int, int]:
        return int.from_bytes(buf[off : off + ioa_size], "little"), off + ioa_size

    try:
        if type_id in (TypeId.M_SP_NA_1, TypeId.M_SP_TB_1):
            elem = 1 + (7 if type_id == TypeId.M_SP_TB_1 else 0)
            objects = _decode_seq(payload, n, sq, ioa_size, elem, _parse_sp)
        elif type_id in (TypeId.M_DP_NA_1, TypeId.M_DP_TB_1):
            elem = 1 + (7 if type_id == TypeId.M_DP_TB_1 else 0)
            objects = _decode_seq(payload, n, sq, ioa_size, elem, _parse_dp)
        elif type_id == TypeId.M_ME_NA_1:
            objects = _decode_seq(payload, n, sq, ioa_size, 3, _parse_me_na)
        elif type_id == TypeId.M_ME_NB_1:
            objects = _decode_seq(payload, n, sq, ioa_size, 3, _parse_me_nb)
        elif type_id in (TypeId.M_ME_NC_1, TypeId.M_ME_TF_1):
            elem = 5 + (7 if type_id == TypeId.M_ME_TF_1 else 0)
            objects = _decode_seq(payload, n, sq, ioa_size, elem, _parse_me_nc)
        elif type_id in (TypeId.C_SC_NA_1, TypeId.C_DC_NA_1, TypeId.C_IC_NA_1, TypeId.C_CS_NA_1,
                         TypeId.C_SE_NA_1, TypeId.C_SE_NB_1, TypeId.C_SE_NC_1):
            # 控制方向确认，尽量解析 IOA
            if len(payload) >= ioa_size:
                ioa = int.from_bytes(payload[:ioa_size], "little")
                objects = [InformationObject(ioa=ioa, value=payload[ioa_size:].hex())]
        else:
            if n and len(payload) >= ioa_size:
                ioa = int.from_bytes(payload[:ioa_size], "little")
                objects = [InformationObject(ioa=ioa, value=payload[ioa_size:].hex())]
    except Exception:
        objects = []

    return AsduMessage(type_id=type_id, vsq=vsq, cot=cot, oa=oa, ca=ca, objects=objects, raw=data)


def _decode_seq(buf, n, sq, ioa_size, elem_size, parser):
    objects = []
    off = 0
    if sq:
        ioa, off = int.from_bytes(buf[off:off + ioa_size], "little"), off + ioa_size
        for i in range(n):
            val, q, extra = parser(buf[off:off + elem_size])
            objects.append(InformationObject(ioa=ioa + i, value=val, quality=q, extra=extra))
            off += elem_size
    else:
        for _ in range(n):
            ioa = int.from_bytes(buf[off:off + ioa_size], "little")
            off += ioa_size
            val, q, extra = parser(buf[off:off + elem_size])
            objects.append(InformationObject(ioa=ioa, value=val, quality=q, extra=extra))
            off += elem_size
    return objects


def _parse_sp(b: bytes):
    return bool(b[0] & 0x01), b[0] & 0xF0, {}


def _parse_dp(b: bytes):
    return b[0] & 0x03, b[0] & 0xF0, {}


def _parse_me_na(b: bytes):
    nva = struct.unpack_from("<h", b, 0)[0]
    return nva / 32767.0, b[2], {"nva": nva}


def _parse_me_nb(b: bytes):
    sva = struct.unpack_from("<h", b, 0)[0]
    return sva, b[2], {}


def _parse_me_nc(b: bytes):
    val = struct.unpack_from("<f", b, 0)[0]
    return val, b[4], {}


def hex_dump(data: bytes) -> str:
    return " ".join(f"{x:02X}" for x in data)


# [AGENT_CHANGE_END] 2026-09-07 104-MVP协议编解码
