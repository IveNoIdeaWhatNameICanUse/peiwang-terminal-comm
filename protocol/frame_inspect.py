# [AGENT_CHANGE_BEGIN] 2026-09-12 报文完整解析
"""报文帧结构化解析，供监视窗口右键「报文解析」使用。

- 监视摘要（主站 note）：只到公共地址（由 master 截断）
- 本模块：完整解析链路/APCI + ASDU 头 + 信息体，字段带 offset/length 便于高亮 HEX
- COT/CA/IOA/链路地址长度由当前会话规约传入
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from protocol.iec101 import link
from protocol.iec104.codec import decode_asdu, parse_apci
from protocol.iec104.const import TYPE_NAMES, TypeId, cot_name


@dataclass
class ParseField:
    name: str
    value: str
    desc: str = ""
    offset: int = 0
    length: int = 0
    children: List["ParseField"] = field(default_factory=list)


def hex_to_bytes(text: str) -> bytes:
    """从监视区 HEX 文本提取字节（忽略非十六进制字符）。"""
    parts = []
    for tok in (text or "").replace("\n", " ").split():
        t = "".join(c for c in tok if c in "0123456789abcdefABCDEF")
        if len(t) == 2:
            parts.append(int(t, 16))
        elif len(t) > 2 and len(t) % 2 == 0:
            for i in range(0, len(t), 2):
                parts.append(int(t[i : i + 2], 16))
    return bytes(parts)


def _u8(data: bytes, off: int) -> int:
    return int(data[off])


def _le(data: bytes, off: int, size: int) -> int:
    return int.from_bytes(data[off : off + size], "little")


def _fmt_val(v) -> str:
    if isinstance(v, bool):
        return "合" if v else "分"
    if isinstance(v, float):
        return "%g" % v
    if v is None:
        return ""
    return str(v)


def _elem_size(type_id: int) -> Optional[int]:
    """信息元素字节数（不含 IOA）；无法确定时返回 None。"""
    if type_id in (TypeId.M_SP_NA_1, TypeId.M_DP_NA_1):
        return 1
    if type_id in (TypeId.M_SP_TB_1, TypeId.M_DP_TB_1):
        return 1 + 7
    if type_id in (TypeId.M_ME_NA_1, TypeId.M_ME_NB_1):
        return 3
    if type_id == TypeId.M_ME_NC_1:
        return 5
    if type_id == TypeId.M_ME_TF_1:
        return 5 + 7
    if type_id == 55:
        return 5
    if type_id in (TypeId.C_RP_NA_1, TypeId.C_IC_NA_1, TypeId.C_CS_NA_1):
        # 105:QRP; 100:QOI; 103:CP56Time — C_CS is 7 bytes not 1
        if type_id == TypeId.C_CS_NA_1:
            return 7
        return 1
    return None


def _group_name(type_id: int) -> str:
    if type_id in (1, 3, 30, 31):
        return "遥信"
    if type_id in (9, 11, 13, 36):
        return "遥测"
    if type_id in (45, 46):
        return "遥控"
    if type_id in (48, 49, 50, 55, 108, 200, 201, 202, 203):
        return "遥调"
    return "信息体"


def _asdu_fields(
    asdu: bytes,
    base: int,
    cot_size: int,
    ca_size: int,
    ioa_size: int,
) -> List[ParseField]:
    """ASDU 完整字段：TID/VSQ/COT/DCA + 信息体（可高亮）。"""
    need = 2 + cot_size + ca_size
    if len(asdu) < need:
        return [
            ParseField("ASDU", "", f"长度不足({len(asdu)}<{need})", base, len(asdu))
        ]
    tid = _u8(asdu, 0)
    vsq = _u8(asdu, 1)
    sq = bool(vsq & 0x80)
    n = vsq & 0x7F
    cot_raw = _le(asdu, 2, cot_size)
    cot = cot_raw & 0x3F
    pos = (cot_raw & 0x40) == 0
    test = bool(cot_raw & 0x80)
    oa = (cot_raw >> 8) & 0xFF if cot_size >= 2 else 0
    ca = _le(asdu, 2 + cot_size, ca_size)

    cot_desc = cot_name(cot)
    if cot in (1, 7, 9, 10, 11):
        cot_desc += "(肯定)" if pos else "(否定)"
    if test:
        cot_desc += "(测试)"
    if cot_size >= 2:
        cot_desc += f" OA={oa}"

    fields: List[ParseField] = [
        ParseField("TID", str(tid), TYPE_NAMES.get(tid, f"类型{tid}"), base + 0, 1),
        ParseField(
            "VSQ",
            str(vsq),
            f"{'连续' if sq else '非连续'} 数量:{n}",
            base + 1,
            1,
        ),
        ParseField("COT", str(cot), cot_desc, base + 2, cot_size),
        ParseField("DCA", str(ca), "公共地址", base + 2 + cot_size, ca_size),
    ]

    hdr = need
    payload = asdu[hdr:]
    payload_base = base + hdr
    if not payload or n <= 0:
        return fields

    try:
        parsed = decode_asdu(asdu, cot_size=cot_size, ca_size=ca_size, ioa_size=ioa_size)
        objects = parsed.objects or []
    except Exception:
        objects = []

    if not objects:
        fields.append(
            ParseField(
                "DATA",
                payload.hex(" ").upper(),
                "未识别信息体",
                payload_base,
                len(payload),
            )
        )
        return fields

    group = ParseField(
        _group_name(tid),
        "",
        f"共{len(objects)}个",
        payload_base,
        len(payload),
        [],
    )
    elem = _elem_size(tid)
    off = 0
    if elem is not None and sq:
        # 连续：起始 IOA + n × 元素
        ioa0 = _le(payload, 0, ioa_size) if len(payload) >= ioa_size else 0
        group.children.append(
            ParseField(
                "起始IOA",
                str(ioa0),
                "连续地址起点",
                payload_base,
                ioa_size,
            )
        )
        off = ioa_size
        for i, obj in enumerate(objects):
            if off + elem > len(payload):
                break
            vs = _fmt_val(obj.value)
            group.children.append(
                ParseField(
                    f"数值[{i}]",
                    vs,
                    f"IOA={obj.ioa}",
                    payload_base + off,
                    elem,
                )
            )
            off += elem
    elif elem is not None and not sq:
        for i, obj in enumerate(objects):
            if off + ioa_size + elem > len(payload):
                break
            group.children.append(
                ParseField(
                    f"IOA[{i}]",
                    str(obj.ioa),
                    "信息体地址",
                    payload_base + off,
                    ioa_size,
                )
            )
            off += ioa_size
            group.children.append(
                ParseField(
                    f"数值[{i}]",
                    _fmt_val(obj.value),
                    f"IOA={obj.ioa}",
                    payload_base + off,
                    elem,
                )
            )
            off += elem
    else:
        # 控制/定值等：整块高亮 + 逐对象展示（无精确子偏移时用整段）
        for i, obj in enumerate(objects):
            extra = obj.extra or {}
            desc = f"IOA={obj.ioa}"
            if extra.get("area") is not None:
                desc += f" 区号={extra['area']}"
            group.children.append(
                ParseField(
                    f"对象[{i}]",
                    _fmt_val(obj.value),
                    desc,
                    payload_base,
                    len(payload),
                )
            )
    fields.append(group)
    return fields


def _parse_104(
    data: bytes, cot_size: int, ca_size: int, ioa_size: int
) -> Tuple[str, List[ParseField]]:
    kind, detail = parse_apci(data)
    root_name = {"I": "IFrame", "S": "SFrame", "U": "UFrame"}.get(kind, "APCI")
    children: List[ParseField] = [
        ParseField("START", f"0x{data[0]:02X}", "起始", 0, 1),
        ParseField("LEN", str(data[1]), "APDU长度", 1, 1),
    ]
    if kind == "I":
        children.append(
            ParseField(
                "CTRL",
                f"N(S)={detail.get('ns')} N(R)={detail.get('nr')}",
                "I 帧控制域",
                2,
                4,
            )
        )
        asdu = detail.get("asdu") or b""
        children.extend(_asdu_fields(asdu, 6, cot_size, ca_size, ioa_size))
    elif kind == "S":
        children.append(
            ParseField("CTRL", f"N(R)={detail.get('nr')}", "S 帧", 2, 4)
        )
    else:
        children.append(
            ParseField("CTRL", f"0x{data[2]:02X}", str(detail), 2, 4)
        )
    return root_name, children


def _ctrl_desc_101(c: int) -> str:
    dir_b = bool(c & 0x80)
    prm = bool(c & 0x40)
    fcb = bool(c & 0x20)
    fcv = bool(c & 0x10)
    fc = c & 0x0F
    return f"DIR={int(dir_b)} PRM={int(prm)} FCB={int(fcb)} FCV={int(fcv)} FC={fc}"


def _parse_101(
    data: bytes,
    cot_size: int,
    ca_size: int,
    addr_size: int,
    ioa_size: int,
) -> Tuple[str, List[ParseField]]:
    if not data:
        return "Empty", []
    if data[0] == link.SINGLE_CHAR:
        return "E5", [ParseField("E5", "0xE5", "单字符确认", 0, 1)]
    if data[0] == link.START_FIXED:
        total = 4 + addr_size
        children = [
            ParseField("START", "0x10", "固定帧", 0, 1),
            ParseField("CTRL", f"0x{data[1]:02X}", _ctrl_desc_101(data[1]), 1, 1),
            ParseField(
                "ADDR",
                str(_le(data, 2, addr_size)),
                "链路地址",
                2,
                addr_size,
            ),
        ]
        if len(data) >= total:
            children.append(
                ParseField("CS", f"0x{data[total - 2]:02X}", "校验", total - 2, 1)
            )
            children.append(
                ParseField("END", f"0x{data[total - 1]:02X}", "结束", total - 1, 1)
            )
        return "FixedFrame", children
    if data[0] == link.START_VAR and len(data) >= 4:
        length = data[1]
        children = [
            ParseField("START", "0x68", "可变帧", 0, 1),
            ParseField("LEN", str(length), "长度", 1, 1),
            ParseField("LEN2", str(data[2]), "长度重复", 2, 1),
            ParseField("START2", "0x68", "起始重复", 3, 1),
        ]
        if len(data) < 5 + addr_size:
            return "VariableFrame", children
        c = data[4]
        children.append(
            ParseField("CTRL", f"0x{c:02X}", _ctrl_desc_101(c), 4, 1)
        )
        children.append(
            ParseField(
                "ADDR",
                str(_le(data, 5, addr_size)),
                "链路地址",
                5,
                addr_size,
            )
        )
        asdu_off = 5 + addr_size
        user_end = 4 + length
        asdu = data[asdu_off:user_end] if user_end > asdu_off else data[asdu_off:]
        children.extend(_asdu_fields(asdu, asdu_off, cot_size, ca_size, ioa_size))
        total = 4 + length + 2
        if len(data) >= total:
            children.append(
                ParseField("CS", f"0x{data[total - 2]:02X}", "校验", total - 2, 1)
            )
            children.append(
                ParseField("END", f"0x{data[total - 1]:02X}", "结束", total - 1, 1)
            )
        return "VariableFrame", children
    return "Raw", [ParseField("DATA", data.hex(" ").upper(), "无法识别帧头", 0, len(data))]


def inspect_frame(
    data: bytes,
    *,
    protocol: str = "104",
    cot_size: int = 2,
    ca_size: int = 2,
    ioa_size: int = 3,
    addr_size: int = 2,
) -> ParseField:
    """完整解析一帧（含信息体）；监视摘要截断不在此处理。"""
    proto = (protocol or "104").strip()
    ioa_size = max(1, int(ioa_size or 1))
    try:
        use_101 = proto == "101" or (
            data
            and (
                data[0] in (link.START_FIXED, link.SINGLE_CHAR)
                or (len(data) >= 4 and data[0] == 0x68 and data[3] == 0x68)
            )
        )
        if use_101:
            name, children = _parse_101(
                data, cot_size, ca_size, max(1, int(addr_size or 1)), ioa_size
            )
        else:
            name, children = _parse_104(data, cot_size, ca_size, ioa_size)
    except Exception as e:
        name, children = "Error", [
            ParseField("ERR", str(e), "解析失败", 0, len(data))
        ]
    return ParseField(name, "", "报文帧", 0, len(data), children)


def session_sizes(sess: Optional[dict]) -> dict:
    """从会话字典取 COT/CA/IOA/链路地址长度。"""
    sess = sess or {}
    proto = str(sess.get("protocol") or "104")
    if proto == "101":
        return {
            "protocol": "101",
            "cot_size": int(sess.get("cot_size") or 2),
            "ca_size": int(sess.get("ca_size") or 2),
            "ioa_size": int(sess.get("ioa_size_101") or sess.get("ioa_size") or 2),
            "addr_size": int(sess.get("addr_size") or 2),
        }
    return {
        "protocol": "104",
        "cot_size": int(sess.get("cot_size") or 2),
        "ca_size": int(sess.get("ca_size") or 2),
        "ioa_size": int(sess.get("ioa_size") or 3),
        "addr_size": 0,
    }


# [AGENT_CHANGE_END] 2026-09-12 报文完整解析
