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


def build_interrogation(ca: int, qoi: int = 20, oa: int = 0, cot_size: int = 2, ca_size: int = 2, ioa_size: int = 3) -> bytes:
    header = encode_asdu_header(TypeId.C_IC_NA_1, 1, 6, ca, oa, cot_size=cot_size, ca_size=ca_size)
    return header + _ioa_bytes(0, ioa_size) + bytes([qoi & 0xFF])


def build_clock_sync(ca: int, dt: Optional[datetime] = None, oa: int = 0, cot_size: int = 2, ca_size: int = 2, ioa_size: int = 3) -> bytes:
    dt = dt or datetime.now()
    header = encode_asdu_header(TypeId.C_CS_NA_1, 1, 6, ca, oa, cot_size=cot_size, ca_size=ca_size)
    # CP56Time2a（7字节）：毫秒(2B little) + 分 + 时 + 日(bit0-4)/星期(bit5-7) + 月 + 年(0-99)
    msec = dt.second * 1000 + dt.microsecond // 1000
    time_bytes = struct.pack(
        "<HBBBBB",
        msec,
        dt.minute & 0x3F,
        dt.hour & 0x1F,
        (dt.day & 0x1F) | ((dt.isoweekday() & 0x07) << 5),
        dt.month & 0x0F,
        (dt.year % 100) & 0x7F,
    )
    return header + _ioa_bytes(0, ioa_size) + time_bytes


# [AGENT_CHANGE_BEGIN] 2026-09-12 复位进程命令
def build_reset_process(
    ca: int,
    qrp: int = 1,
    oa: int = 0,
    cot_size: int = 2,
    ca_size: int = 2,
    ioa_size: int = 3,
) -> bytes:
    """C_RP_NA_1(105) 复位进程：IOA=0 + QRP。
    QRP：1=总复位进程（常用），2=复位事件缓冲区带时标的未处理信息（F30/标准）。
    """
    header = encode_asdu_header(TypeId.C_RP_NA_1, 1, 6, ca, oa, cot_size=cot_size, ca_size=ca_size)
    return header + _ioa_bytes(0, ioa_size) + bytes([int(qrp) & 0xFF])


# [AGENT_CHANGE_END] 2026-09-12 复位进程命令


# [AGENT_CHANGE_BEGIN] 2026-09-12 平衡101对齐KW-2200延时获得
def build_delay_acquisition(
    ca: int,
    delay_ms: int,
    oa: int = 0,
    cot: int = 6,
    cot_size: int = 2,
    ca_size: int = 2,
    ioa_size: int = 3,
) -> bytes:
    """C_CD_NA_1(106) 延时获得：IOA + CP16Time2a(毫秒, 2B LE)。"""
    header = encode_asdu_header(TypeId.C_CD_NA_1, 1, cot, ca, oa, cot_size=cot_size, ca_size=ca_size)
    return header + _ioa_bytes(0, ioa_size) + struct.pack("<H", int(delay_ms) & 0xFFFF)
# [AGENT_CHANGE_END] 2026-09-12 平衡101对齐KW-2200延时获得


def build_single_command(ca: int, ioa: int, on: bool, select: bool, oa: int = 0, cot: int = 6, cot_size: int = 2, ca_size: int = 2, ioa_size: int = 3) -> bytes:
    header = encode_asdu_header(TypeId.C_SC_NA_1, 1, cot, ca, oa, cot_size=cot_size, ca_size=ca_size)
    sco = (1 if on else 0) | (0x80 if select else 0x00)
    return header + _ioa_bytes(ioa, ioa_size) + bytes([sco])


def build_double_command(ca: int, ioa: int, state: int, select: bool, oa: int = 0, cot: int = 6, cot_size: int = 2, ca_size: int = 2, ioa_size: int = 3) -> bytes:
    """state: 1=OFF, 2=ON; cot=8 表示撤销(去激活)"""
    header = encode_asdu_header(TypeId.C_DC_NA_1, 1, cot, ca, oa, cot_size=cot_size, ca_size=ca_size)
    dco = (state & 0x03) | (0x80 if select else 0x00)
    return header + _ioa_bytes(ioa, ioa_size) + bytes([dco])


def build_setpoint_float(ca: int, ioa: int, value: float, select: bool, oa: int = 0, cot: int = 6, cot_size: int = 2, ca_size: int = 2, ioa_size: int = 3) -> bytes:
    header = encode_asdu_header(TypeId.C_SE_NC_1, 1, cot, ca, oa, cot_size=cot_size, ca_size=ca_size)
    qos = 0x80 if select else 0x00
    return header + _ioa_bytes(ioa, ioa_size) + struct.pack("<f", float(value)) + bytes([qos])


def build_setpoint_normalized(ca: int, ioa: int, value: float, select: bool, oa: int = 0, cot: int = 6, cot_size: int = 2, ca_size: int = 2, ioa_size: int = 3) -> bytes:
    """value: -1.0 .. 1.0 -> int16; cot=8 表示撤销(去激活)"""
    header = encode_asdu_header(TypeId.C_SE_NA_1, 1, cot, ca, oa, cot_size=cot_size, ca_size=ca_size)
    nva = int(max(-1.0, min(1.0, value)) * 32767)
    qos = 0x80 if select else 0x00
    return header + _ioa_bytes(ioa, ioa_size) + struct.pack("<h", nva) + bytes([qos])


def build_read_command(ca: int, ioa: int, oa: int = 0, cot: int = 5, cot_size: int = 2, ca_size: int = 2, ioa_size: int = 3) -> bytes:
    """读命令 C_RD_NA_1(102)；标准 COT=5(请求)，可选 6(激活)。"""
    header = encode_asdu_header(TypeId.C_RD_NA_1, 1, cot, ca, oa, cot_size=cot_size, ca_size=ca_size)
    return header + _ioa_bytes(ioa, ioa_size)


def build_read_param(ca: int, ioa: int, value: float = 0.0, oa: int = 0, cot: int = 6,
                     tid: int = 108, area: int = 1, cot_size: int = 2, ca_size: int = 2,
                     ioa_size: int = 3) -> bytes:
    """参数/定值读取命令：COT=6(激活)。
    - 108 C_RS_NA_1(南网/广西)：头部 + IOA + 短浮点(读填 0.0)
    - 202 C_RS_NA_1(国网)：头部 + 定值区号(2B) + IOA(3B)
    """
    header = encode_asdu_header(tid, 1, cot, ca, oa, cot_size=cot_size, ca_size=ca_size)
    if tid == 202:
        return header + struct.pack("<H", int(area) & 0xFFFF) + _ioa_bytes(ioa, ioa_size)
    return header + _ioa_bytes(ioa, ioa_size) + struct.pack("<f", float(value))


def build_read_param_batch(ca: int, ioas: list, tid: int = 108, area: int = 1, oa: int = 0, cot: int = 6,
                           cot_size: int = 2, ca_size: int = 2, ioa_size: int = 3) -> bytes:
    """多对象定值读取命令（SQ=0 非连续，一帧 n 个）：COT=6(激活)。
    - 108 C_RS_NA_1(南网/广西)：头部 + n×(IOA + 短浮点0.0)
    - 202 C_RS_NA_1(国网)：头部 + 定值区号(2B) 一次 + n×IOA(3B)
    """
    ioas = [int(i) for i in ioas][:127]
    header = encode_asdu_header(tid, len(ioas), cot, ca, oa, cot_size=cot_size, ca_size=ca_size)
    body = b""
    if tid == 202:
        body += struct.pack("<H", int(area) & 0xFFFF)
        for ioa in ioas:
            body += _ioa_bytes(ioa, ioa_size)
    else:
        for ioa in ioas:
            body += _ioa_bytes(ioa, ioa_size) + struct.pack("<f", 0.0)
    return header + body


def build_preset_param(ca: int, ioa: int, value: float, select: bool = True, oa: int = 0, cot: int = 6,
                       tid: int = 55, cot_size: int = 2, ca_size: int = 2, ioa_size: int = 3) -> bytes:
    """参数预置/激活命令：配网扩展类型 55(C_SP_NA_1，南网/广西)；
    结构 = 头部 + IOA + 短浮点 + 1B(S/E：预置=0x80，激活=0x00)；cot=8 撤销。"""
    header = encode_asdu_header(tid, 1, cot, ca, oa, cot_size=cot_size, ca_size=ca_size)
    qos = 0x80 if select else 0x00
    return header + _ioa_bytes(ioa, ioa_size) + struct.pack("<f", float(value)) + bytes([qos])


def build_write_param(ca: int, items: list, area: int = 1, pi: int = 0x01, oa: int = 0, cot: int = 6,
                      tid: int = 203, cot_size: int = 2, ca_size: int = 2, ioa_size: int = 3) -> bytes:
    """国网 203 C_WS_NA_1 写参数和定值（细则 7.9.4）：
    - 预置：VSQ=n，体 = 定值区号SN(2B) + 参数特征标识(1B) + n×[IOA(3B)+Tag(1B)+长度(1B)+值]
    - 固化/撤销：VSQ=0x00，体 = 定值区号SN(2B) + 特征标识(1B)
    特征标识 PI：bit0 S/E(1=预置,0=固化)，bit1 CR(1=取消预置)。
    items: [(ioa, value), ...]；预置时至少 1 项。"""
    header = encode_asdu_header(tid, len(items), cot, ca, oa, cot_size=cot_size, ca_size=ca_size)
    body = struct.pack("<H", int(area) & 0xFFFF) + bytes([int(pi) & 0xFF])
    for ioa, val in items:
        try:
            f = float(val)
            if f.is_integer():
                tag, raw = 0x23, struct.pack("<I", int(f) & 0xFFFFFFFF)
            else:
                tag, raw = 0x26, struct.pack("<f", f)
        except (TypeError, ValueError):
            tag, raw = 0x23, struct.pack("<I", int(val) & 0xFFFFFFFF)
        body += _ioa_bytes(ioa, ioa_size) + bytes([tag, len(raw)]) + raw
    return header + body


def build_area_command(ca: int, value: int = 0, tid: int = 200, oa: int = 0, cot: int = 6,
                       cot_size: int = 2, ca_size: int = 2, ioa_size: int = 3) -> bytes:
    """定值区命令（细则 7.9.1/7.9.2）：
    - 200 切换定值区：信息体地址(0)(3B) + 定值区号SN(2B)
    - 201 读当前定值区号：信息体地址(0)(3B)
    """
    header = encode_asdu_header(tid, 1, cot, ca, oa, cot_size=cot_size, ca_size=ca_size)
    body = _ioa_bytes(0, ioa_size)  # 信息体地址(0)
    if tid == 200:
        body += struct.pack("<H", int(value) & 0xFFFF)
    return header + body


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
    pos: bool = True   # P/N：1=肯定确认(0x40)，0=否定
    test: bool = False  # T：1=测试帧(0x80)

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
        cot_byte = data[idx]
        cot = cot_byte & 0x3F
        pos = not bool(cot_byte & 0x40)  # P/N：0=肯定确认，1=否定确认（国网2009 6.3.3.4）
        test = bool(cot_byte & 0x80)
        oa = data[idx + 1]
        idx += 2
    else:
        cot_byte = data[idx]
        cot = cot_byte & 0x3F
        pos = not bool(cot_byte & 0x40)
        test = bool(cot_byte & 0x80)
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
                         TypeId.C_RD_NA_1, TypeId.C_SE_NA_1, TypeId.C_SE_NB_1, TypeId.C_SE_NC_1,
                         TypeId.C_CD_NA_1, TypeId.C_RP_NA_1):
            # 控制方向确认，尽量解析 IOA（105 复位进程：IOA + QRP）
            if len(payload) >= ioa_size:
                ioa = int.from_bytes(payload[:ioa_size], "little")
                rest = payload[ioa_size:]
                val: object = rest.hex()
                extra = {}
                if type_id == TypeId.C_RP_NA_1 and rest:
                    val = int(rest[0])
                    extra = {"qrp": int(rest[0])}
                objects = [InformationObject(ioa=ioa, value=val, extra=extra)]
        elif type_id in (200, 201):
            # 定值区命令响应：对象地址(3B) + 当前定值区号(2B) [+ 最小区号(2B) + 最大区号(2B)，仅 201]
            if type_id == 201 and len(payload) >= ioa_size + 6:
                ioa = int.from_bytes(payload[:ioa_size], "little")
                area = int.from_bytes(payload[ioa_size:ioa_size + 2], "little")
                min_area = int.from_bytes(payload[ioa_size + 2:ioa_size + 4], "little")
                max_area = int.from_bytes(payload[ioa_size + 4:ioa_size + 6], "little")
                objects = [InformationObject(
                    ioa=ioa, value=area,
                    extra={"area": area, "min_area": min_area, "max_area": max_area},
                )]
            elif len(payload) >= ioa_size + 2:
                ioa = int.from_bytes(payload[:ioa_size], "little")
                area = int.from_bytes(payload[ioa_size:ioa_size + 2], "little")
                objects = [InformationObject(ioa=ioa, value=area, extra={"area": area})]
            elif len(payload) >= 2:
                area = int.from_bytes(payload[:2], "little")
                objects = [InformationObject(ioa=0, value=area, extra={"area": area})]
        elif type_id in (55, 108, 202, 203):
            # 配网扩展：参数/定值
            if type_id in (202, 203):
                # 202 读/203 写参数和定值（响应同为 区号(2B)+参数特征标识(1B)+[IOA+Tag+长度+值]×n）
                objs = _parse_202_objs(payload, sq, n, ioa_size)
                if objs:
                    objects = objs
                elif type_id == 203 and n == 0 and len(payload) >= 3:
                    # 203 固化/撤销确认：区号(2B)+特征标识(1B)，无对象、无值
                    area = int.from_bytes(payload[:2], "little")
                    pi = payload[2]
                    objects = [InformationObject(
                        ioa=0, value=None, extra={"area": area, "feat": pi, "pi": pi, "noobj": True},
                    )]
                elif type_id == 202 and len(payload) == 9:
                    # 旧格式：区号(2B) + IOA + 短浮点(4B)
                    area = int.from_bytes(payload[:2], "little")
                    ioa = int.from_bytes(payload[2:2 + ioa_size], "little")
                    val = struct.unpack_from("<f", payload, 2 + ioa_size)[0]
                    objects = [InformationObject(ioa=ioa, value=val, extra={"area": area})]
                elif type_id == 202 and len(payload) == 5:
                    area = int.from_bytes(payload[:2], "little")
                    ioa = int.from_bytes(payload[2:2 + ioa_size], "little")
                    objects = [InformationObject(ioa=ioa, value=0.0, extra={"area": area})]
                else:
                    elem = 4
                    objects = _decode_seq(payload, n, sq, ioa_size, elem, _parse_me_nc)
            elif type_id in (55,):
                # 55：IOA + 短浮点 + 1B S/E 限定词
                objects = _decode_seq(payload, n, sq, ioa_size, 5, _parse_c_sp)
            else:
                elem = 4
                objects = _decode_seq(payload, n, sq, ioa_size, elem, _parse_me_nc)
        else:
            if n and len(payload) >= ioa_size:
                ioa = int.from_bytes(payload[:ioa_size], "little")
                objects = [InformationObject(ioa=ioa, value=payload[ioa_size:].hex())]
    except Exception:
        objects = []

    return AsduMessage(
        type_id=type_id, vsq=vsq, cot=cot, oa=oa, ca=ca, objects=objects, raw=data,
        pos=pos, test=test,
    )


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
    return val, (b[4] if len(b) > 4 else 0), {}


def _parse_c_sp(b: bytes):
    """55/203 预置/激活参数对象：短浮点 + 1B S/E（0x80=预置，0x00=激活）。"""
    val = struct.unpack_from("<f", b, 0)[0]
    return val, 0, {"se": b[4] if len(b) > 4 else 0}


def _dt_value(dtype: int, raw: bytes):
    """国网2009附录D TLV 数据类型取值。"""
    if dtype == 0x26 and len(raw) >= 4:      # 单精度浮点 Float(38)
        return struct.unpack_from("<f", raw, 0)[0]
    if dtype == 0x27 and len(raw) >= 8:      # 双精度浮点 Double(39)
        return struct.unpack_from("<d", raw, 0)[0]
    if dtype == 0x01:                        # 布尔 Boolean(1)
        return int(raw[0] & 0x01) if raw else 0
    if dtype == 0x04:                        # 八位位串/字符串(4)
        s = raw.split(b"\x00", 1)[0]
        try:
            return s.decode("ascii")
        except (UnicodeDecodeError, AttributeError):
            return raw.hex()
    if dtype in (0x02, 0x24, 0x21, 0x2B):    # Int/Long/Short/Tiny 有符号
        return int.from_bytes(raw, "little", signed=True)
    return int.from_bytes(raw, "little")     # Uint/Ulong/UShort/UTiny/其他 无符号


def _parse_202_objs(payload: bytes, sq: bool, n: int, ioa_size: int = 3) -> List[InformationObject]:
    """国网 202 读取定值(响应)对象序列（支持批量 n>=1）：
    定值区号(2B)+参数特征标识(1B) 仅一次，随后：
    非连续：n × [IOA(3B)+类型(1B)+长度(1B)+值(变长)]
    连续SQ=1：起始IOA 后接 n × [类型+长度+值]，IOA 递增。"""
    objects: List[InformationObject] = []
    if len(payload) < 2 + 1:
        return objects
    area = int.from_bytes(payload[:2], "little")
    feat = payload[2]
    off = 3
    if sq:
        if off + ioa_size > len(payload):
            return objects
        ioa_base = int.from_bytes(payload[off:off + ioa_size], "little")
        off += ioa_size
        idx = 0
        while idx < n and off + 2 <= len(payload):
            dtype = payload[off]
            dlen = payload[off + 1]
            off += 2
            raw = payload[off:off + dlen]
            off += dlen
            objects.append(InformationObject(
                ioa=ioa_base + idx, value=_dt_value(dtype, raw),
                extra={"area": area, "feat": feat, "dtype": dtype, "dlen": dlen},
            ))
            idx += 1
    else:
        idx = 0
        while idx < n and off + ioa_size + 2 <= len(payload):
            ioa = int.from_bytes(payload[off:off + ioa_size], "little")
            off += ioa_size
            dtype = payload[off]
            dlen = payload[off + 1]
            off += 2
            raw = payload[off:off + dlen]
            off += dlen
            objects.append(InformationObject(
                ioa=ioa, value=_dt_value(dtype, raw),
                extra={"area": area, "feat": feat, "dtype": dtype, "dlen": dlen},
            ))
            idx += 1
    return objects


def hex_dump(data: bytes) -> str:
    return " ".join(f"{x:02X}" for x in data)


# [AGENT_CHANGE_END] 2026-09-07 104-MVP协议编解码
