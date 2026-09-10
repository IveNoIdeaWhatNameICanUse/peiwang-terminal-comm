# [AGENT_CHANGE_BEGIN] 2026-09-07 多主站工程模型
"""点表与工程配置（支持多主站会话）。"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, List, Optional


@dataclass
class PointDef:
    ioa: int
    type_id: int
    name: str = ""
    category: str = ""
    value: Any = None
    quality: int = 0
    data_type: int = 0  # 实际定值/参数数据类型（202 响应 dtype），0=未知
    upper_limit: Optional[float] = None   # 遥测越上限阈值（None=不统计）
    lower_limit: Optional[float] = None   # 遥测越下限阈值（None=不统计）
    dead_band: Optional[float] = None     # 遥测突变死区（None=不统计）
    no_change_time: Optional[float] = None  # 遥测长期不变告警时间(秒)（None=不统计）


# 四遥类别（按 IEC 104 类型标识归类）
CATEGORY_BY_TYPE = {
    1: "遥信", 3: "遥信", 30: "遥信", 31: "遥信",
    5: "遥测", 7: "遥测", 9: "遥测", 11: "遥测", 13: "遥测", 15: "遥测", 36: "遥测",
    45: "遥控", 46: "遥控", 47: "遥控",
    48: "遥调", 49: "遥调", 50: "遥调",
}


def category_for_type(type_id: int) -> str:
    """按类型标识归类到四遥；未知类型返回空串（由调用方按 IOA 分段推断）。"""
    return CATEGORY_BY_TYPE.get(int(type_id or 0), "")


@dataclass
class SessionDef:
    id: str
    name: str = "主站1"
    remote_ip: str = "127.0.0.1"
    remote_port: int = 2404
    local_ip: str = ""
    local_port: int = 0
    common_address: int = 1
    originator: int = 0
    # ---- 104 可调参数（KW-2200 风格）----
    t0: float = 30.0          # 连接建立超时(秒)
    t1: float = 15.0          # 发送/测试APDU超时(秒)
    t2: float = 10.0          # 无数据确认超时(秒)
    t3: float = 20.0          # 空闲测试超时(秒)
    k: int = 12               # 未确认I帧上限
    w: int = 8                # 触发S确认的I帧数
    gi_period: int = 600      # 总召唤周期(秒)，0=禁用
    clock_period: int = 30    # 校时周期(分)，0=禁用
    call_period: int = 0      # 召唤度周期(秒)，0=禁用
    link_ack_timeout: float = 10.0   # 链路应答超时(秒)
    cmd_timeout: float = 30.0        # 远控命令超时(秒)
    cot_size: int = 2         # 传送原因长度(字节)
    ca_size: int = 2          # 公共地址长度(字节)
    ioa_size: int = 3         # 信息体地址长度(字节)
    read_cot: int = 6         # 读命令(定值召唤)传送原因：5=请求(标准)，6=激活
    auto_reconnect: bool = True      # 超时断线重连
    tx_delay_ms: float = 0.0         # 发送延时(毫秒)
    setpoint_batch: int = 10         # 参数全召唤：单帧定值个数(1~127)
    # ---- 101 串口参数 ----
    protocol: str = "104"            # 104(TCP) / 101(串口,平衡/非平衡)
    serial_port: str = "COM1"        # 串口号
    baudrate: int = 9600             # 波特率
    serial_parity: str = "E"         # 校验: N无 / E偶 / O奇
    stopbits: int = 1                # 停止位
    link_addr: int = 1               # 101 链路地址
    addr_size: int = 1               # 链路地址长度(1/2 字节)
    balanced: bool = False           # True=平衡方式, False=非平衡方式
    poll_period: float = 1.0         # 非平衡轮询周期(秒)
    ioa_size_101: int = 2            # 101 信息体地址长度(字节，国内常见 2)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SessionDef":
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:8]),
            name=str(data.get("name") or "主站"),
            remote_ip=str(data.get("remote_ip") or "127.0.0.1"),
            remote_port=int(data.get("remote_port") or 2404),
            local_ip=str(data.get("local_ip") or ""),
            local_port=int(data.get("local_port") or 0),
            common_address=int(data.get("common_address") or 1),
            originator=int(data.get("originator") or 0),
            t0=float(data.get("t0") or 30.0),
            t1=float(data.get("t1") or 15.0),
            t2=float(data.get("t2") or 10.0),
            t3=float(data.get("t3") or 20.0),
            k=int(data.get("k") or 12),
            w=int(data.get("w") or 8),
            gi_period=int(data.get("gi_period") or 600),
            clock_period=int(data.get("clock_period") or 30),
            call_period=int(data.get("call_period") or 0),
            link_ack_timeout=float(data.get("link_ack_timeout") or 10.0),
            cmd_timeout=float(data.get("cmd_timeout") or 30.0),
            cot_size=int(data.get("cot_size") or 2),
            ca_size=int(data.get("ca_size") or 2),
            ioa_size=int(data.get("ioa_size") or 3),
            read_cot=int(data.get("read_cot") or 6),
            auto_reconnect=bool(data.get("auto_reconnect", True)),
            tx_delay_ms=float(data.get("tx_delay_ms") or 0.0),
            setpoint_batch=int(data.get("setpoint_batch") or 10),
            protocol=str(data.get("protocol") or "104"),
            serial_port=str(data.get("serial_port") or "COM1"),
            baudrate=int(data.get("baudrate") or 9600),
            serial_parity=str(data.get("serial_parity") or "E"),
            stopbits=int(data.get("stopbits") or 1),
            link_addr=int(data.get("link_addr") or 1),
            addr_size=int(data.get("addr_size") or 1),
            balanced=bool(data.get("balanced", False)),
            poll_period=float(data.get("poll_period") or 1.0),
            ioa_size_101=int(data.get("ioa_size_101") or 2),
        )


@dataclass
class ProjectConfig:
    sessions: List[SessionDef] = field(default_factory=list)
    active_session_id: str = ""
    points: List[PointDef] = field(default_factory=list)
    # 每个主站(会话)独立的点表：session_id -> points
    points_by_session: Dict[str, List[PointDef]] = field(default_factory=dict)
    # 规约/细则版本（广西、南网、国网）
    protocol_variant: str = "广西"
    # 兼容旧单连接字段（读写时同步到 active session）
    remote_ip: str = "127.0.0.1"
    remote_port: int = 2404
    local_ip: str = ""
    local_port: int = 0
    common_address: int = 1
    originator: int = 0

    def ensure_sessions(self) -> None:
        if not self.sessions:
            sid = uuid.uuid4().hex[:8]
            self.sessions = [
                SessionDef(
                    id=sid,
                    name="主站1",
                    remote_ip=self.remote_ip,
                    remote_port=self.remote_port,
                    local_ip=self.local_ip,
                    local_port=self.local_port,
                    common_address=self.common_address,
                    originator=self.originator,
                )
            ]
            self.active_session_id = sid
        if not self.active_session_id or not any(s.id == self.active_session_id for s in self.sessions):
            self.active_session_id = self.sessions[0].id
        # 确保每个会话都有独立点表；旧数据(仅全局 points)迁移到各会话
        for s in self.sessions:
            if s.id not in self.points_by_session:
                self.points_by_session[s.id] = list(self.points)
        self._sync_legacy_from_active()

    def session_points(self, sid: str) -> List[PointDef]:
        return self.points_by_session.get(sid, [])

    def set_session_points(self, sid: str, pts: List[PointDef]) -> None:
        self.points_by_session[sid] = pts

    def active_session(self) -> SessionDef:
        if not self.sessions:
            self.ensure_sessions()
        for s in self.sessions:
            if s.id == self.active_session_id:
                return s
        return self.sessions[0]

    def _sync_legacy_from_active(self) -> None:
        s = None
        for x in self.sessions:
            if x.id == self.active_session_id:
                s = x
                break
        if s is None:
            s = self.sessions[0]
            self.active_session_id = s.id
        self.remote_ip = s.remote_ip
        self.remote_port = s.remote_port
        self.local_ip = s.local_ip
        self.local_port = s.local_port
        self.common_address = s.common_address
        self.originator = s.originator

    def to_dict(self) -> dict:
        self.ensure_sessions()
        self.points = list(self.points_by_session.get(self.active_session_id, []))
        return {
            "sessions": [s.to_dict() for s in self.sessions],
            "active_session_id": self.active_session_id,
            "points": [asdict(p) for p in self.points],
            "points_by_session": {
                sid: [asdict(p) for p in pts] for sid, pts in self.points_by_session.items()
            },
            "protocol_variant": self.protocol_variant,
            # legacy mirrors
            "remote_ip": self.remote_ip,
            "remote_port": self.remote_port,
            "local_ip": self.local_ip,
            "local_port": self.local_port,
            "common_address": self.common_address,
            "originator": self.originator,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectConfig":
        def _mk_point(p: dict) -> PointDef:
            def _opt(key):
                v = p.get(key)
                if v in (None, "", 0):
                    return None
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            return PointDef(
                ioa=int(p.get("ioa") or 0),
                type_id=int(p.get("type_id") or 0),
                name=str(p.get("name") or ""),
                category=str(p.get("category") or ""),
                value=p.get("value"),
                quality=int(p.get("quality") or 0),
                data_type=int(p.get("data_type") or 0),
                upper_limit=_opt("upper_limit"),
                lower_limit=_opt("lower_limit"),
                dead_band=_opt("dead_band"),
                no_change_time=_opt("no_change_time"),
            )

        pts = [_mk_point(p) for p in data.get("points", [])]
        sessions_raw = data.get("sessions") or []
        sessions = [SessionDef.from_dict(s) for s in sessions_raw]
        pbs_raw = data.get("points_by_session") or {}
        points_by_session = {}
        for sid, raw in pbs_raw.items():
            if isinstance(raw, list):
                points_by_session[str(sid)] = [_mk_point(p) for p in raw]
        cfg = cls(
            sessions=sessions,
            active_session_id=str(data.get("active_session_id") or ""),
            points=pts,
            points_by_session=points_by_session,
            protocol_variant=str(data.get("protocol_variant") or "广西"),
            remote_ip=data.get("remote_ip", "127.0.0.1"),
            remote_port=int(data.get("remote_port", 2404)),
            local_ip=data.get("local_ip", ""),
            local_port=int(data.get("local_port", 0) or 0),
            common_address=int(data.get("common_address", 1)),
            originator=int(data.get("originator", 0)),
        )
        cfg.ensure_sessions()
        # 旧文件迁移：无 points_by_session 时，全局 points 复制到各会话
        if not pbs_raw:
            for s in cfg.sessions:
                cfg.set_session_points(s.id, list(pts))
        return cfg


class ProjectStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.config = ProjectConfig()
        self.config.ensure_sessions()

    def load(self, path: Path) -> ProjectConfig:
        text = path.read_text(encoding="utf-8")
        self.config = ProjectConfig.from_dict(json.loads(text))
        self.path = path
        return self.config

    def save(self, path: Optional[Path] = None) -> Path:
        path = path or self.path
        if path is None:
            raise ValueError("未指定保存路径")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.config.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.path = path
        return path

    def upsert_point(self, sid: str, point: PointDef) -> None:
        pts = self.config.session_points(sid)
        for i, p in enumerate(pts):
            if p.ioa == point.ioa:
                pts[i] = point
                self.config.set_session_points(sid, pts)
                return
        pts.append(point)
        self.config.set_session_points(sid, pts)

    def remove_point(self, sid: str, ioa: int) -> bool:
        pts = self.config.session_points(sid)
        new_pts = [p for p in pts if p.ioa != ioa]
        changed = len(new_pts) < len(pts)
        if changed:
            self.config.set_session_points(sid, new_pts)
        return changed

    def update_values(self, sid: str, objects: List[dict]) -> List[dict]:
        pts = self.config.session_points(sid)
        changed = []
        by_ioa = {p.ioa: p for p in pts}
        # 仅遥信/遥测数据类自动创建新点；控制/参数类确认(55/108/202/203 等)
        # 不在点表时不自动添加(如 203 固化/撤销确认的 IOA=0 整区对象)
        AUTO_CREATE = {1, 3, 5, 7, 9, 11, 13, 15, 30, 31, 36}
        for obj in objects:
            ioa = int(obj["ioa"])
            extra = obj.get("extra") or {}
            rt = obj.get("type_id")
            se = extra.get("se")
            pos = obj.get("pos", True)
            # 定值整定：预置阶段(55 S/E=0x80 / 203 PI bit7=1)或否定确认时不更新“值”，待固化(激活)成功后才更新
            update_value = True
            if rt in (55, 203):
                if rt == 55:
                    preset_flag = (se == 0x80)
                else:
                    preset_flag = ((extra.get("feat") or 0) & 0x80) == 0x80
                if preset_flag or not pos:
                    update_value = False
            if ioa in by_ioa:
                if update_value and obj.get("value") is not None:
                    by_ioa[ioa].value = obj.get("value")
                    by_ioa[ioa].quality = int(obj.get("quality") or 0)
                # 遥信类：按实际收到的报文类型（单点1/30、双点3/31）回写点表类型，
                # 避免导入时误标为单点导致 1 被显示成“合”（双点 1=分）
                if rt in (1, 3, 30, 31) and by_ioa[ioa].category in ("遥信", ""):
                    if by_ioa[ioa].type_id != rt:
                        by_ioa[ioa].type_id = rt
                    by_ioa[ioa].category = "遥信"
                if extra.get("dtype") is not None:
                    by_ioa[ioa].data_type = int(extra["dtype"])
                changed.append(asdict(by_ioa[ioa]))
            else:
                if rt not in AUTO_CREATE or ioa == 0:
                    continue  # 控制/参数确认或整区对象：不自动建点
                p = PointDef(
                    ioa=ioa,
                    type_id=int(rt),
                    name=f"IOA-{ioa}",
                    category=category_for_type(rt),
                    value=obj.get("value") if update_value else None,
                    quality=int(obj.get("quality") or 0),
                    data_type=int(extra["dtype"]) if extra.get("dtype") is not None else 0,
                )
                pts.append(p)
                changed.append(asdict(p))
        if changed:
            self.config.set_session_points(sid, pts)
        return changed


# [AGENT_CHANGE_END] 2026-09-07 多主站工程模型
