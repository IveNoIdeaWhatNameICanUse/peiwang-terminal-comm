# [AGENT_CHANGE_BEGIN] 2026-09-07 多主站API桥
"""供 UI 调用的 Python API 桥（多主站会话）。"""
from __future__ import annotations

import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.events import EventBus
from core.eventlog import EventLog
from core.project import PointDef, ProjectConfig, ProjectStore, SessionDef, category_for_type
from net import nics_as_dicts
from protocol.iec101 import Iec101Master, SerialParams
from protocol.iec104 import ConnectParams, Iec104Master, MasterError, TYPE_NAMES


_CSV_COLUMN_ALIASES = {
    "ioa": ["ioa", "信息体地址", "信息对象地址", "点号", "地址", "序号", "信息地址"],
    "name": ["name", "名称", "点名", "描述", "说明", "备注"],
    "type_id": ["type_id", "typeid", "类型标识", "类型", "tid", "type"],
    "category": ["category", "类别", "分类", "四遥", "所属类别", "类别名称"],
    "value": ["value", "值", "数值", "当前值"],
    "quality": ["quality", "品质", "质量", "q"],
    "upper_limit": ["upper_limit", "上限", "越上限", "遥测上限"],
    "lower_limit": ["lower_limit", "下限", "越下限", "遥测下限"],
    "dead_band": ["dead_band", "死区", "突变死区"],
    "no_change_time": ["no_change_time", "长期不变", "不变告警", "不复位时间"],
}

_CSV_TYPE_TEXT = {
    "单点遥信": 1, "双点遥信": 3, "步位置": 5, "位串": 7,
    "归一化遥测": 9, "标度化遥测": 11, "浮点遥测": 13, "短浮点遥测": 13, "累计量": 15,
    "带时标单点": 30, "带时标双点": 31, "带时标浮点": 36,
    "单点遥控": 45, "双点遥控": 46, "步调节": 47,
    "归一化设点": 48, "标度化设点": 49, "浮点设点": 50, "短浮点设点": 50,
    "遥信": 1, "遥测": 13, "遥控": 45, "遥调": 50,
}

# 厂家发码表分组（WLD2660 等常见格式：首列 1=遥信 / 2=遥测 / 5=遥控 / 7=遥调）
_VENDOR_GROUPS = {
    "1": ("遥信", 1),
    "2": ("遥测", 13),
    "5": ("遥控", 45),
    "7": ("遥调", 50),
}


def _first_cjk(cells) -> str:
    """返回首个包含汉字的单元格（用于厂家格式中定位名称列）。"""
    for c in cells:
        s = (c or "").strip()
        if s and any("\u4e00" <= ch <= "\u9fff" for ch in s):
            return s
    return ""


def _csv_norm(text) -> str:
    return str(text or "").strip().lower().replace(" ", "").replace("_", "")


def _csv_parse_type(text) -> Optional[int]:
    s = _csv_norm(text)
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in _CSV_TYPE_TEXT:
        return _CSV_TYPE_TEXT[s]
    for key, tid in _CSV_TYPE_TEXT.items():
        if key in s or s in key:
            return tid
    return None


class ApiBridge:
    def __init__(self, root: Path, bus: Optional[EventBus] = None):
        self.root = root
        self.bus = bus or EventBus()
        self.store = ProjectStore()
        self._masters: Dict[str, Iec104Master] = {}
        self._event_logs: Dict[str, EventLog] = {}
        self._init_pending: Dict[str, bool] = {}   # 连接后初始化未完成(期间事件不计)
        self._init_since: Dict[str, float] = {}
        self._ui_push: Optional[Callable[[dict], None]] = None
        self._frames: List[dict] = []
        self._lock = threading.Lock()
        default_cfg = root / "configs" / "default.json"
        if not default_cfg.exists() and getattr(sys, "frozen", False):
            # 打包运行：exe 目录无配置时回退到包内默认（_MEIPASS），保存仍写 exe 目录
            alt = Path(getattr(sys, "_MEIPASS", root)) / "configs" / "default.json"
            if alt.exists():
                default_cfg = alt
        if default_cfg.exists():
            try:
                self.store.load(default_cfg)
            except Exception:
                pass
        self.store.config.ensure_sessions()
        for s in self.store.config.sessions:
            self._ensure_master(s.id)

    def set_ui_push(self, push: Callable[[dict], None]) -> None:
        self._ui_push = push

    def _push(self, event: dict) -> None:
        self.bus.emit(event.get("type", "event"), event)
        if self._ui_push:
            try:
                self._ui_push(event)
            except Exception:
                pass

    def _event_log(self, sid: str) -> EventLog:
        if sid not in self._event_logs:
            self._event_logs[sid] = EventLog()
        return self._event_logs[sid]

    # [AGENT_CHANGE_BEGIN] 2026-09-10 删除101自动保存报文到桌面
    def _on_master_event(self, event: dict) -> None:
        if event.get("type") == "connection":
            sid = str(event.get("session_id") or self.store.config.active_session_id)
            st = event.get("state")
            if st == "connected":
                self._init_pending[sid] = True
                self._init_since[sid] = time.time()
                self._event_log(sid).on_link("start")
            elif st == "disconnected":
                self._init_pending.pop(sid, None)
                self._event_log(sid).on_link("stop")
        # [AGENT_CHANGE_END] 2026-09-10 删除101自动保存报文到桌面
        if event.get("type") == "frame":
            with self._lock:
                self._frames.append(event)
                if len(self._frames) > 800:
                    self._frames = self._frames[-800:]
            # 总召唤激活终止(100/10)视为初始化完成
            asdu = event.get("asdu") or {}
            if asdu.get("type_id") == 100 and asdu.get("cot") == 10:
                self._init_pending.pop(
                    str(event.get("session_id") or self.store.config.active_session_id), None
                )
        if event.get("type") == "points":
            sid = str(event.get("session_id") or self.store.config.active_session_id)
            changed = self.store.update_values(sid, event.get("objects") or [])
            # 初始化窗口：连接后总召唤激活终止前的首次上送不计事件/统计
            if self._init_pending.get(sid):
                if time.time() - self._init_since.get(sid, 0.0) > 60.0:
                    self._init_pending.pop(sid, None)  # 兜底:60s 后视为完成
                else:
                    # SOE（带时标遥信 30/31）是关键事件，初始化窗口内也记录
                    soe_objs = [
                        o for o in (event.get("objects") or [])
                        if int(o.get("type_id") or 0) in (30, 31)
                    ]
                    if soe_objs:
                        slog = self._event_log(sid)
                        smeta = {p.ioa: p for p in self.store.config.session_points(sid)}
                        for obj in soe_objs:
                            ioa = int(obj.get("ioa") or 0)
                            sp = smeta.get(ioa)
                            snm = (sp.name if sp and sp.name else f"IOA-{ioa}")
                            stid = int(obj.get("type_id") or 0)
                            slog.on_yx(ioa, snm, obj.get("value"), tid=stid, is_soe=True)
                    event = {**event, "points": changed}
                    self._push(event)
                    return
            # 事件记录：SOE/COS/遥测统计（排除总召唤/组召唤响应等首次全量上送）
            # COT 在事件顶层(帧级)，不在各对象内；5=响应组召唤 20=响应站召唤
            if int(event.get("cot") or 0) in (5, 20):
                event = {**event, "points": changed}
                self._push(event)
                return
            log = self._event_log(sid)
            pts = self.store.config.session_points(sid)
            meta = {}
            for p in pts:
                meta[p.ioa] = p
            for obj in event.get("objects") or []:
                ioa = int(obj.get("ioa") or 0)
                tid = int(obj.get("type_id") or 0)
                p = meta.get(ioa)
                nm = (p.name if p and p.name else f"IOA-{ioa}")
                val = obj.get("value")
                if p and p.category == "遥信":
                    log.on_yx(ioa, nm, val, tid=tid, is_soe=tid in (30, 31))
                elif p and p.category == "遥测":
                    log.on_yc(
                        ioa, nm, val,
                        upper=p.upper_limit, lower=p.lower_limit,
                        dead_band=p.dead_band, no_change_time=p.no_change_time,
                    )
            event = {**event, "points": changed}
        self._push(event)

    def _record_control(self, ioa: int, action: str, on: bool) -> None:
        sid = self.store.config.active_session_id
        nm = ""
        for p in self.store.config.session_points(sid):
            if p.ioa == int(ioa):
                nm = p.name or f"IOA-{ioa}"
                break
        self._event_log(sid).on_control(int(ioa), nm or f"IOA-{ioa}", action, bool(on))

    def _record_adjust(self, ioa: int, action: str, value=None) -> None:
        sid = self.store.config.active_session_id
        nm = ""
        for p in self.store.config.session_points(sid):
            if p.ioa == int(ioa):
                nm = p.name or f"IOA-{ioa}"
                break
        if not nm and int(ioa) == 0:
            nm = "固化(整区)" if action == "exec" else "撤销(整区)"
        self._event_log(sid).on_adjust(int(ioa), nm or f"IOA-{ioa}", action, value)

    def _ensure_master(self, sid: str):
        """按会话协议创建/复用主站实例(104 TCP / 101 串口)。"""
        if sid not in self._masters:
            sess = next((s for s in self.store.config.sessions if s.id == sid), None)
            proto = getattr(sess, "protocol", "104") if sess else "104"
            if proto == "101":
                m = Iec101Master(on_event=self._on_master_event)
            else:
                m = Iec104Master(on_event=self._on_master_event)
            m.session_id = sid
            self._masters[sid] = m
        return self._masters[sid]

    def _active_master(self):
        sid = self.store.config.active_session().id
        return self._ensure_master(sid)

    def _recreate_master(self, sid: str) -> None:
        """协议/端口变更后重建主站实例(先断开旧连接)。"""
        old = self._masters.pop(sid, None)
        if old:
            try:
                old.disconnect()
            except Exception:
                pass
        self._ensure_master(sid)

    # ---- exposed ----

    def get_nics(self) -> list:
        return nics_as_dicts()

    def list_serial_ports(self) -> list:
        """枚举本机串口（101 用）。"""
        try:
            from serial.tools import list_ports
            return [
                {"port": p.device, "desc": p.description or p.device}
                for p in list_ports.comports()
            ]
        except Exception:
            return []

    def get_type_names(self) -> dict:
        return {str(k): v for k, v in TYPE_NAMES.items()}

    def get_project(self) -> dict:
        self.store.config.ensure_sessions()
        data = self.store.config.to_dict()
        # 附带连接状态
        statuses = {}
        for s in self.store.config.sessions:
            m = self._masters.get(s.id)
            statuses[s.id] = bool(m and m.connected)
        data["session_connected"] = statuses
        # [AGENT_CHANGE_BEGIN] 2026-09-11 工程保存：暴露当前工程文件路径（新建未保存时为空串）
        data["project_path"] = str(self.store.path) if self.store.path else ""
        # [AGENT_CHANGE_END] 2026-09-11 工程保存
        return data

    def list_sessions(self) -> dict:
        proj = self.get_project()
        return {
            "ok": True,
            "sessions": proj["sessions"],
            "active_session_id": proj["active_session_id"],
            "session_connected": proj.get("session_connected", {}),
        }

    def create_session(self, data: Optional[dict] = None) -> dict:
        data = data or {}
        n = len(self.store.config.sessions) + 1
        # 默认递增本地端口，降低多主站冲突
        used_ports = {s.local_port for s in self.store.config.sessions if s.local_port}
        local_port = int(data.get("local_port") or 0)
        if not local_port:
            candidate = 2404
            while candidate in used_ports:
                candidate += 1
            local_port = candidate if data.get("auto_local_port", True) else 0
        s = SessionDef(
            id=uuid.uuid4().hex[:8],
            name=str(data.get("name") or f"主站{n}"),
            remote_ip=str(data.get("remote_ip") or "127.0.0.1"),
            remote_port=int(data.get("remote_port") or 2404),
            local_ip=str(data.get("local_ip") or ""),
            local_port=local_port,
            common_address=int(data.get("common_address") or 1),
            originator=int(data.get("originator") or 0),
            protocol=str(data.get("protocol") or "104"),
        )
        # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
        used_cid = {
            int(getattr(x, "center_id", 1) or 1)
            for x in self.store.config.sessions
            if getattr(x, "protocol", "104") == "101"
        }
        cid = 1
        while cid in used_cid:
            cid += 1
        s.center_id = cid
        # [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装
        # [AGENT_CHANGE_BEGIN] 2026-09-11 海南双主站默认 1 字节链路地址
        _lm = str(data.get("link_mode") or "").strip().lower()
        if _lm in ("unbalanced", "balanced", "hainan"):
            s.link_mode = _lm
            s.balanced = _lm in ("balanced", "hainan")
            if _lm == "hainan":
                s.addr_size = int(data.get("addr_size") or 1)
        # [AGENT_CHANGE_END] 2026-09-11 海南双主站默认 1 字节链路地址
        self.store.config.sessions.append(s)
        # 新主站复制当前主站的点表作为初始模板（此后各自独立）
        template = list(self.store.config.session_points(self.store.config.active_session_id))
        self.store.config.set_session_points(s.id, template)
        self.store.config.active_session_id = s.id
        self.store.config._sync_legacy_from_active()
        self._ensure_master(s.id)
        return {"ok": True, "session": s.to_dict(), "project": self.get_project()}

    def update_session(self, sid: str, data: dict) -> dict:
        for s in self.store.config.sessions:
            if s.id != sid:
                continue
            if "name" in data:
                s.name = str(data["name"])
            if "remote_ip" in data:
                s.remote_ip = str(data["remote_ip"])
            if "remote_port" in data:
                s.remote_port = int(data["remote_port"])
            if "local_ip" in data:
                s.local_ip = str(data["local_ip"])
            if "local_port" in data:
                s.local_port = int(data["local_port"] or 0)
            if "common_address" in data:
                s.common_address = int(data["common_address"])
            if "originator" in data:
                s.originator = int(data["originator"])
            # 104 参数
            for key, conv in {
                "t0": float, "t1": float, "t2": float, "t3": float,
                "link_ack_timeout": float, "cmd_timeout": float, "tx_delay_ms": float,
                "gi_period": int, "gi_period_min": int, "clock_period": int,
                "heartbeat_period": int, "call_period": int,
                "cot_size": int, "ca_size": int, "ioa_size": int, "read_cot": int,
            }.items():
                if key in data:
                    setattr(s, key, conv(data[key]))
            # [AGENT_CHANGE_BEGIN] 2026-09-10 设备参数周期
            if "gi_period_min" in data:
                s.gi_period = int(s.gi_period_min or 0) * 60
            # [AGENT_CHANGE_END] 2026-09-10 设备参数周期
            if "k" in data:
                s.k = int(data["k"])
            if "w" in data:
                s.w = int(data["w"])
            if "auto_reconnect" in data:
                s.auto_reconnect = bool(data["auto_reconnect"])
            if "setpoint_batch" in data:
                s.setpoint_batch = max(1, min(int(data["setpoint_batch"] or 10), 127))
            # 101 串口参数 / 协议类型
            proto_changed = False
            if "protocol" in data:
                new_proto = str(data["protocol"] or "104")
                if new_proto != getattr(s, "protocol", "104"):
                    proto_changed = True
                s.protocol = new_proto
            if "serial_port" in data:
                s.serial_port = str(data["serial_port"] or "COM1")
            if "baudrate" in data:
                s.baudrate = int(data["baudrate"] or 9600)
            if "serial_parity" in data:
                # 101 默认无校验（N）
                s.serial_parity = str(data["serial_parity"] or "N")
            if "stopbits" in data:
                s.stopbits = int(data["stopbits"] or 1)
            if "link_addr" in data:
                s.link_addr = int(data["link_addr"] or 1)
            if "addr_size" in data:
                s.addr_size = int(data["addr_size"] or 1)
            if "ioa_size_101" in data:
                s.ioa_size_101 = int(data["ioa_size_101"] or 2)
            if "data_frame_dir" in data:
                s.data_frame_dir = bool(data["data_frame_dir"])
            if "cs_compat" in data:
                s.cs_compat = bool(data["cs_compat"])
            if "balanced" in data:
                s.balanced = bool(data["balanced"])
            # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
            if "link_mode" in data:
                mode = str(data["link_mode"] or "unbalanced").strip().lower()
                if mode not in ("unbalanced", "balanced", "hainan"):
                    mode = "balanced" if data.get("balanced") else "unbalanced"
                s.link_mode = mode
                # 海南双主站底层按平衡链路
                s.balanced = mode in ("balanced", "hainan")
                # [AGENT_CHANGE_BEGIN] 2026-09-11 海南双主站默认 1 字节链路地址
                # 未显式指定链路地址长度时：海南默认 1 字节，其它模式回到 2 字节
                if "addr_size" not in data:
                    s.addr_size = 1 if mode == "hainan" else 2
                # [AGENT_CHANGE_END] 2026-09-11 海南双主站默认 1 字节链路地址
            elif "balanced" in data:
                s.link_mode = "balanced" if s.balanced else "unbalanced"
            if "center_id" in data:
                s.center_id = max(1, min(255, int(data["center_id"] or 1)))
            # [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装
            # [AGENT_CHANGE_BEGIN] 2026-09-10 忽略FCB位错误
            if "ignore_fcb_error" in data:
                s.ignore_fcb_error = bool(data["ignore_fcb_error"])
            # [AGENT_CHANGE_END] 2026-09-10 忽略FCB位错误
            if "poll_period" in data:
                s.poll_period = float(data["poll_period"] or 1.0)
            if proto_changed:
                self._recreate_master(sid)
            if self.store.config.active_session_id == sid:
                self.store.config._sync_legacy_from_active()
            return {"ok": True, "session": s.to_dict(), "project": self.get_project()}
        return {"ok": False, "error": f"会话不存在: {sid}"}

    def delete_session(self, sid: str) -> dict:
        if len(self.store.config.sessions) <= 1:
            return {"ok": False, "error": "至少保留一个主站会话"}
        m = self._masters.pop(sid, None)
        if m:
            try:
                m.disconnect()
            except Exception:
                pass
        self.store.config.sessions = [s for s in self.store.config.sessions if s.id != sid]
        self.store.config.ensure_sessions()
        return {"ok": True, "project": self.get_project()}

    def set_active_session(self, sid: str) -> dict:
        if not any(s.id == sid for s in self.store.config.sessions):
            return {"ok": False, "error": f"会话不存在: {sid}"}
        self.store.config.active_session_id = sid
        self.store.config._sync_legacy_from_active()
        return {"ok": True, "project": self.get_project()}

    def save_project(self, data: dict, path: str = "") -> dict:
        try:
            # [AGENT_CHANGE_BEGIN] 2026-09-11 工程保存
            # 未指定路径时沿用当前工程文件（已打开/已保存过的工程直接覆盖）；
            # 新建且从未保存过则拒绝落盘，由 UI 先询问文件名与保存目录
            if path:
                target = Path(path)
            elif self.store.path:
                target = Path(self.store.path)
            else:
                return {"ok": False, "error": "工程尚未保存过，请先选择保存位置与文件名"}
            # [AGENT_CHANGE_END] 2026-09-11 工程保存
            # 允许带 sessions 的完整工程，也兼容旧扁平字段写回 active
            if data.get("sessions"):
                self.store.config = self.store.config.from_dict({**self.store.config.to_dict(), **data})
            else:
                sid = self.store.config.active_session_id
                self.update_session(
                    sid,
                    {
                        "remote_ip": data.get("remote_ip"),
                        "remote_port": data.get("remote_port"),
                        "local_ip": data.get("local_ip"),
                        "local_port": data.get("local_port"),
                        "common_address": data.get("common_address"),
                        "originator": data.get("originator"),
                    },
                )
                if "points" in data:
                    self.store.config.points = [PointDef(**p) for p in data["points"]]
            self.store.save(target)
            return {"ok": True, "path": str(target)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def load_project(self, path: str) -> dict:
        try:
            for m in list(self._masters.values()):
                try:
                    m.disconnect()
                except Exception:
                    pass
            self._masters.clear()
            cfg = self.store.load(Path(path))
            for s in cfg.sessions:
                self._ensure_master(s.id)
            return {"ok": True, "project": self.get_project()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # [AGENT_CHANGE_BEGIN] 2026-09-11 新建工程按钮
    def new_project(self) -> dict:
        """断开全部连接，重置为仅含默认主站1、空点表的新工程（内存态；保存前不写盘）。"""
        try:
            for m in list(self._masters.values()):
                try:
                    m.disconnect()
                except Exception:
                    pass
            self._masters.clear()
            self._event_logs.clear()
            self._init_pending.clear()
            self._init_since.clear()
            with self._lock:
                self._frames.clear()
            self.store.config = ProjectConfig()
            self.store.config.ensure_sessions()
            self.store.path = None
            for s in self.store.config.sessions:
                self._ensure_master(s.id)
            return {"ok": True, "project": self.get_project()}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    # [AGENT_CHANGE_END] 2026-09-11 新建工程按钮

    def upsert_point(self, point: dict, sid: Optional[str] = None) -> dict:
        sid = sid or self.store.config.active_session_id
        tid = int(point.get("type_id") or 0)

        def _optf(v):
            if v in (None, "", 0, 0.0):
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        p = PointDef(
            ioa=int(point["ioa"]),
            type_id=tid,
            name=str(point.get("name") or ""),
            category=str(point.get("category") or "") or category_for_type(tid),
            value=point.get("value"),
            quality=int(point.get("quality") or 0),
            upper_limit=_optf(point.get("upper_limit")),
            lower_limit=_optf(point.get("lower_limit")),
            dead_band=_optf(point.get("dead_band")),
            no_change_time=_optf(point.get("no_change_time")),
        )
        self.store.upsert_point(sid, p)
        return {"ok": True, "project": self.get_project()}

    def remove_point(self, ioa: int, sid: Optional[str] = None) -> dict:
        sid = sid or self.store.config.active_session_id
        ok = self.store.remove_point(sid, int(ioa))
        return {"ok": ok, "project": self.get_project()}

    # ---- CSV 发码表导入 ----

    def import_points_csv(self, path: str, sid: Optional[str] = None) -> dict:
        """从 CSV 发码表导入点（默认导入到指定/当前主站，按 IOA 去重）。

        支持:
        - 中文/英文表头标准格式: 信息体地址(IOA)/点号, 名称, 类型, 类别...
        - 无表头默认列序 [IOA, 名称, 类型]
        - 厂家格式(WLD2660 等): 首行文件头 0,型号,IEC104,版本; 首列分组 1=遥信/2=遥测/5=遥控/7=遥调
        编码自动兼容 UTF-8 / GBK / GB18030 等。
        """
        import csv
        import io

        try:
            p = Path(path)
            if not p.exists():
                return {"ok": False, "error": f"文件不存在：{path}"}
            sid = sid or self.store.config.active_session_id
            text = None
            last_err = None
            for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "gb2312", "big5"):
                try:
                    text = p.read_bytes().decode(enc)
                    break
                except (UnicodeDecodeError, LookupError) as e:
                    last_err = e
            if text is None:
                return {"ok": False, "error": f"无法识别文件编码：{last_err}"}

            rows = [r for r in csv.reader(io.StringIO(text)) if any((c or "").strip() for c in r)]
            if not rows:
                return {"ok": False, "error": "CSV 文件为空"}

            alias_rev = {}
            for key, names in _CSV_COLUMN_ALIASES.items():
                for nm in names:
                    alias_rev[_csv_norm(nm)] = key
            header = [_csv_norm(c) for c in rows[0]]
            has_header = any(h in alias_rev for h in header)
            col_map: dict = {}
            if has_header:
                for i, h in enumerate(header):
                    key = alias_rev.get(h)
                    if key and key not in col_map:
                        col_map[key] = i
                if "ioa" not in col_map:
                    return {"ok": False, "error": "CSV 缺少“信息体地址(IOA)”列"}
                data_rows = rows[1:]
                start_line = 2
                mode = "header"
            else:
                # 厂家发码表: 首行首列 0(文件头), 或首列是分组 1/2/5/7 且第 2 列为数字
                first = rows[0] if rows else []
                vendor = bool(
                    rows and first
                    and (
                        first[0].strip() == "0"
                        or (
                            first[0].strip() in _VENDOR_GROUPS
                            and len(first) > 1
                            and first[1].strip().isdigit()
                        )
                    )
                )
                if vendor:
                    skip_head = first[0].strip() == "0"
                    data_rows = rows[1:] if skip_head else rows
                    start_line = 2 if skip_head else 1
                    mode = "vendor"
                else:
                    data_rows = rows
                    start_line = 1
                    mode = "plain"

            count = 0
            skipped = []
            for i, row in enumerate(data_rows, start=start_line):
                if mode == "header":

                    def cell(key: str) -> str:
                        idx = col_map.get(key)
                        return (row[idx] if idx is not None and idx < len(row) else "").strip()

                    ioa_s = cell("ioa")
                    name_s = cell("name")
                    type_s = cell("type_id")
                    cat_s = cell("category")
                    val_s = cell("value")
                    q_s = cell("quality")
                    up_s = cell("upper_limit")
                    low_s = cell("lower_limit")
                    db_s = cell("dead_band")
                    nc_s = cell("no_change_time")
                elif mode == "vendor":
                    grp = (row[0] if row else "").strip()
                    info = _VENDOR_GROUPS.get(grp)
                    if not info:
                        skipped.append(f"第{i}行：未知分组“{grp}”，已跳过")
                        continue
                    ioa_s = (row[1] if len(row) > 1 else "").strip()
                    name_s = _first_cjk(row[2:]) or (row[-1].strip() if row and row[-1].strip() else "")
                    type_s = str(info[1])
                    cat_s = info[0]
                    val_s = ""
                    q_s = ""
                    up_s = ""
                    low_s = ""
                    db_s = ""
                    nc_s = ""
                else:  # plain: 无表头默认 [IOA, 名称, 类型]
                    ioa_s = (row[0] if len(row) > 0 else "").strip()
                    name_s = (row[1] if len(row) > 1 else "").strip()
                    type_s = (row[2] if len(row) > 2 else "").strip()
                    cat_s = ""
                    val_s = ""
                    q_s = ""
                    up_s = ""
                    low_s = ""
                    db_s = ""
                    nc_s = ""

                def _optf(s: str):
                    return float(s) if s and s.strip().lstrip("-").replace(".", "", 1).isdigit() else None

                if not ioa_s.isdigit():
                    skipped.append(f"第{i}行：IOA 无效“{ioa_s}”")
                    continue
                tid = _csv_parse_type(type_s) or 0
                cat = cat_s or category_for_type(tid)
                if cat not in ("遥信", "遥测", "遥控", "遥调"):
                    cat = category_for_type(tid)
                self.store.upsert_point(
                    sid,
                    PointDef(
                        ioa=int(ioa_s),
                        type_id=tid,
                        name=name_s or f"IOA-{int(ioa_s)}",
                        category=cat,
                        value=val_s if val_s != "" else None,
                        quality=int(q_s) if q_s.isdigit() else 0,
                        upper_limit=_optf(up_s),
                        lower_limit=_optf(low_s),
                        dead_band=_optf(db_s),
                        no_change_time=_optf(nc_s),
                    ),
                )
                count += 1
            # 源点表(发码表)无类型列时，按 IOA 分段补全类型/类别
            self.fill_missing_point_types(sid)
            return {
                "ok": True,
                "count": count,
                "skipped": len(skipped),
                "errors": skipped,
                "project": self.get_project(),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def connect(self, params: Optional[dict] = None) -> dict:
        """连接当前活动会话；params 若给出则先写回该会话。
        会话 protocol=104 走 TCP；protocol=101 走串口(平衡/非平衡)。"""
        try:
            s = self.store.config.active_session()
            if params:
                self.update_session(s.id, params)
                s = self.store.config.active_session()
            if getattr(s, "protocol", "104") == "101":
                _la = int(s.link_addr or 1)
                _as = int(getattr(s, "addr_size", 2) or 2)
                if _la > 0xFFFF:
                    return {"ok": False, "code": "BAD_LINK_ADDR", "error": "101 链路地址超范围(0~65535)"}
                if _la > 0xFF and _as == 1:
                    _as = 2  # 链路地址>255 自动用 2 字节
                sp = SerialParams(
                    port=s.serial_port,
                    baudrate=int(s.baudrate or 9600),
                    parity=str(s.serial_parity or "N"),
                    stopbits=int(s.stopbits or 1),
                    link_addr=_la,
                    addr_size=_as,
                    balanced=bool(getattr(s, "balanced", False)),
                    # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
                    link_mode=str(getattr(s, "link_mode", "") or (
                        "balanced" if getattr(s, "balanced", False) else "unbalanced"
                    )),
                    center_id=int(getattr(s, "center_id", 1) or 1),
                    # [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装
                    data_frame_dir=bool(getattr(s, "data_frame_dir", True)),
                    # [AGENT_CHANGE_BEGIN] 2026-09-10 101可变帧校验和按用户数据求和
                    cs_compat=bool(getattr(s, "cs_compat", False)),
                    # [AGENT_CHANGE_END] 2026-09-10 101可变帧校验和按用户数据求和
                    poll_period=float(getattr(s, "poll_period", 1.0) or 1.0),
                    resp_timeout=float(getattr(s, "link_ack_timeout", 10.0) or 10.0),
                    common_address=s.common_address,
                    originator=s.originator,
                    cot_size=int(getattr(s, "cot_size", 2) or 2),
                    ca_size=int(getattr(s, "ca_size", 2) or 2),
                    ioa_size=int(getattr(s, "ioa_size_101", 2) or 2),
                    # 现场（KW-2200）发送间隔 200ms；未配置时取 200
                    tx_delay_ms=float(getattr(s, "tx_delay_ms", 0.0) or 0.0) or 200.0,
                    # [AGENT_CHANGE_BEGIN] 2026-09-10 忽略FCB位错误
                    ignore_fcb_error=bool(getattr(s, "ignore_fcb_error", False)),
                    # [AGENT_CHANGE_END] 2026-09-10 忽略FCB位错误
                    # [AGENT_CHANGE_BEGIN] 2026-09-10 设备参数周期
                    gi_period_min=int(getattr(s, "gi_period_min", 15) or 0),
                    clock_period_min=int(getattr(s, "clock_period", 10) or 0),
                    heartbeat_period=int(getattr(s, "heartbeat_period", 30) or 0),
                    # [AGENT_CHANGE_END] 2026-09-10 设备参数周期
                )
                self._ensure_master(s.id).connect(sp)
                return {"ok": True, "message": "串口连接成功", "session_id": s.id}
            # 多会话本地端口冲突检查
            if s.local_port:
                for other in self.store.config.sessions:
                    if other.id == s.id or not other.local_port:
                        continue
                    om = self._masters.get(other.id)
                    if om and om.connected and other.local_ip == s.local_ip and other.local_port == s.local_port:
                        return {
                            "ok": False,
                            "code": "LOCAL_PORT_CONFLICT",
                            "error": f"本地 {s.local_ip or '*'}:{s.local_port} 已被会话「{other.name}」占用",
                        }
            cp = ConnectParams(
                remote_ip=s.remote_ip,
                remote_port=s.remote_port,
                local_ip=s.local_ip,
                local_port=s.local_port,
                common_address=s.common_address,
                originator=s.originator,
                t0=float(getattr(s, "t0", 30.0) or 30.0),
                t1=float(getattr(s, "t1", 15.0) or 15.0),
                t2=float(getattr(s, "t2", 10.0) or 10.0),
                t3=float(getattr(s, "t3", 20.0) or 20.0),
                k=int(getattr(s, "k", 12) or 12),
                w=int(getattr(s, "w", 8) or 8),
                # [AGENT_CHANGE_BEGIN] 2026-09-10 设备参数周期
                gi_period=int(getattr(s, "gi_period_min", 15) or 0) * 60,
                clock_period=int(getattr(s, "clock_period", 10) or 0),
                call_period=int(getattr(s, "call_period", 0) or 0),
                # [AGENT_CHANGE_END] 2026-09-10 设备参数周期
                link_ack_timeout=float(getattr(s, "link_ack_timeout", 10.0) or 10.0),
                cmd_timeout=float(getattr(s, "cmd_timeout", 30.0) or 30.0),
                cot_size=int(getattr(s, "cot_size", 2) or 2),
                ca_size=int(getattr(s, "ca_size", 2) or 2),
                ioa_size=int(getattr(s, "ioa_size", 3) or 3),
                read_cot=int(getattr(s, "read_cot", 5) or 5),
                auto_reconnect=bool(getattr(s, "auto_reconnect", True)),
                tx_delay_ms=float(getattr(s, "tx_delay_ms", 0.0) or 0.0),
            )
            self._ensure_master(s.id).connect(cp)
            return {"ok": True, "message": "连接成功", "session_id": s.id}
        except MasterError as e:
            return {"ok": False, "code": e.code, "error": e.message}
        except Exception as e:
            return {"ok": False, "code": "UNEXPECTED", "error": str(e), "detail": traceback.format_exc()}

    def disconnect(self, sid: str = "") -> dict:
        try:
            sid = sid or self.store.config.active_session().id
            m = self._masters.get(sid)
            if m:
                m.disconnect()
            return {"ok": True, "session_id": sid}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def disconnect_all(self) -> dict:
        for m in self._masters.values():
            try:
                m.disconnect()
            except Exception:
                pass
        return {"ok": True}

    def is_connected(self, sid: str = "") -> bool:
        sid = sid or self.store.config.active_session().id
        m = self._masters.get(sid)
        return bool(m and m.connected)

    def general_interrogation(self) -> dict:
        return self._cmd(lambda: self._active_master().general_interrogation())

    def clock_sync(self) -> dict:
        return self._cmd(lambda: self._active_master().clock_sync())

    # [AGENT_CHANGE_BEGIN] 2026-09-12 复位进程命令
    def reset_process(self, qrp: int = 1) -> dict:
        """发送复位进程命令 C_RP_NA_1(105)，默认 QRP=1。"""
        return self._cmd(lambda: self._active_master().reset_process(int(qrp)))
    # [AGENT_CHANGE_END] 2026-09-12 复位进程命令

    def single_command(self, ioa: int, on: bool, select: bool) -> dict:
        self._record_control(ioa, "sel" if select else "exec", on)
        return self._cmd_confirm(
            lambda m: m.single_command(int(ioa), bool(on), bool(select)),
            type_ids=(45,), cots=(7, 10),
            ok_text="遥控选择成功" if select else "遥控执行成功",
        )

    def double_command(self, ioa: int, state: int, select: bool) -> dict:
        self._record_control(ioa, "sel" if select else "exec", state == 1)
        return self._cmd_confirm(
            lambda m: m.double_command(int(ioa), int(state), bool(select)),
            type_ids=(46,), cots=(7, 10),
            ok_text="遥控选择成功" if select else "遥控执行成功",
        )

    def setpoint_float(self, ioa: int, value: float, select: bool) -> dict:
        return self._cmd(lambda: self._active_master().setpoint_float(int(ioa), float(value), bool(select)))

    def setpoint_normalized(self, ioa: int, value: float, select: bool) -> dict:
        return self._cmd(lambda: self._active_master().setpoint_normalized(int(ioa), float(value), bool(select)))

    def read_points(self, ioas: list, area: int = 1, batch: Optional[int] = None) -> dict:
        """定值召唤（支持单选/多选 IOA）：广西/南网用 108(C_RS_NA_1)，国网用 202(区号(2B)+IOA)。
        batch=单帧定值个数，多选时按批发送，每批等从站回执后再发下一批（后台线程）。"""
        try:
            ids = [int(i) for i in ioas]
            if not ids:
                return {"ok": False, "error": "未选择定值点"}
            tid = 202 if self.store.config.protocol_variant == "国网" else 108
            batch = max(1, min(int(batch or 10), 127))
            m = self._active_master()
            timeout = float(getattr(getattr(m, "_params", None), "cmd_timeout", 30.0) or 30.0)

            def _run():
                try:
                    before = m.i_frame_count()
                    for k in range(0, len(ids), batch):
                        m.read_setpoints_batch(ids[k:k + batch], tid=tid, area=area)
                        m.wait_i_frame(before, timeout)
                        before = m.i_frame_count()
                except Exception:
                    pass

            threading.Thread(target=_run, daemon=True).start()
            return {"ok": True, "count": len(ids), "batch": batch}
        except MasterError as e:
            return {"ok": False, "code": e.code, "error": e.message}
        except Exception as e:
            return {"ok": False, "code": "UNEXPECTED", "error": str(e)}

    @staticmethod
    def _guess_type_for_ioa(ioa: int) -> int:
        """按 IOA 分段推断缺失类型：≥6001H 遥控(45)、≥5001H 遥调(50)、≥4001H 遥测(13)、其他 遥信(1)。"""
        if ioa >= 0x6001:
            return 45
        if ioa >= 0x5001:
            return 50
        if ioa >= 0x4001:
            return 13
        return 1

    def fill_missing_point_types(self, sid: Optional[str] = None) -> dict:
        """点表缺类型的点按 IOA 分段填入类型/类别（国网发码表常无类型列），返回补充数量。"""
        try:
            sid = sid or self.store.config.active_session_id
            filled = 0
            for p in self.store.config.session_points(sid):
                if p.type_id or p.category:
                    continue
                p.type_id = self._guess_type_for_ioa(p.ioa)
                p.category = category_for_type(p.type_id)
                self.store.upsert_point(sid, p)
                filled += 1
            return {"ok": True, "filled": filled}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def read_all_setpoints(self, sid: Optional[str] = None, area: int = 1, batch: Optional[int] = None) -> dict:
        """参数全召唤：点表中全部遥调(定值)点按单帧个数分批发送；每批等从站回执后再发下一批（后台线程）。"""
        try:
            sid = sid or self.store.config.active_session_id
            self.fill_missing_point_types(sid)
            ids = [p.ioa for p in self.store.config.session_points(sid) if p.category == "遥调"]
            if not ids:
                return {"ok": False, "error": "点表中没有遥调(定值)点"}
            tid = 202 if self.store.config.protocol_variant == "国网" else 108
            sess = next((s for s in self.store.config.sessions if s.id == sid), None)
            batch = max(1, min(int(batch if batch else (getattr(sess, "setpoint_batch", 10) or 10)), 127))
            m = self._active_master()
            timeout = float(getattr(getattr(m, "_params", None), "cmd_timeout", 30.0) or 30.0)

            def _run():
                try:
                    before = m.i_frame_count()
                    for k in range(0, len(ids), batch):
                        m.read_setpoints_batch(ids[k:k + batch], tid=tid, area=area)
                        m.wait_i_frame(before, timeout)
                        before = m.i_frame_count()
                except Exception:
                    pass

            threading.Thread(target=_run, daemon=True).start()
            return {"ok": True, "count": len(ids), "batch": batch}
        except MasterError as e:
            return {"ok": False, "code": e.code, "error": e.message}
        except Exception as e:
            return {"ok": False, "code": "UNEXPECTED", "error": str(e)}

    def set_protocol_variant(self, variant: str) -> dict:
        """设置规约/细则版本（广西/南网/国网），影响四遥功能适配。"""
        if variant not in ("广西", "南网", "国网"):
            return {"ok": False, "error": f"不支持的规约版本: {variant}"}
        self.store.config.protocol_variant = variant
        return {"ok": True, "project": self.get_project()}

    def cancel_command(self, ioa: int, kind: str) -> dict:
        """遥控撤销（COT=8 去激活：sc/dc），等停止激活确认后弹窗结果。"""
        tid = 46 if kind == "dc" else 45
        self._record_control(ioa, "cancel", False)

        def fn(m):
            if kind == "dc":
                m.double_command(int(ioa), 1, False, cancel=True)
            else:
                m.single_command(int(ioa), True, False, cancel=True)

        return self._cmd_confirm(fn, type_ids=(tid,), cots=(9, 7), ok_text="遥控撤销成功")

    def preset_setpoint(self, ioa: int, value: float, select: bool, area: int = 1) -> dict:
        """定值整定：预置(S/E=1) / 固化(S/E=0)；等从站确认后弹窗结果。
        广西/南网用 55(C_SP_NA_1)，国网用 203(C_WS_NA_1，细则7.9.4)。"""
        tid = 203 if self.store.config.protocol_variant == "国网" else 55
        if select:
            cots = (7,)  # 预置：激活确认
            ok_text = "预置成功"
        else:
            cots = (7,)  # 固化：激活(6) → 激活确认(7)
            ok_text = "固化(激活)成功"
        self._record_adjust(ioa, "preset" if select else "exec", value)
        # [AGENT_CHANGE_BEGIN] 2026-09-10 固化撤销清修改值
        return self._cmd_confirm(
            lambda m: m.preset_param(int(ioa), float(value), bool(select), tid=tid, area=int(area)),
            type_ids=(tid,), cots=cots,
            ok_text=ok_text,
            clear_modvals=not select,  # 固化成功后清空「修改值」
        )
        # [AGENT_CHANGE_END] 2026-09-10 固化撤销清修改值

    def fix_setpoint(self, area: int = 1) -> dict:
        """国网定值固化：无需选中点/值——203 VSQ=0 + 区号(2B) + PI=00(S/E=0)，COT=6。
        固化成功后由 UI 将“修改值”提交为点表“值”。"""
        self._record_adjust(0, "exec", None)
        return self._cmd_confirm(
            lambda m: m.preset_param(0, 0.0, select=False, tid=203, area=int(area)),
            type_ids=(203,), cots=(7,),
            ok_text="固化(激活)成功（已更新点表值）",
            commit_modvals=True,
        )

    def cancel_setpoint(self, ioa: int, kind: str = "", area: int = 1) -> dict:
        """定值整定撤销(COT=8)：等停止激活确认后弹窗结果。
        国网 203 撤销报文无信息体地址(整区撤销)，ioa 传 0 即可。"""
        tid = 203 if self.store.config.protocol_variant == "国网" else 55
        self._record_adjust(ioa, "cancel", None)
        # [AGENT_CHANGE_BEGIN] 2026-09-10 固化撤销清修改值
        return self._cmd_confirm(
            lambda m: m.preset_param(int(ioa), 0.0, select=False, cancel=True, tid=tid, area=int(area)),
            type_ids=(tid,), cots=(9, 7),
            ok_text="撤销成功",
            clear_modvals=True,
        )
        # [AGENT_CHANGE_END] 2026-09-10 固化撤销清修改值

    def read_setting_area(self) -> dict:
        """读定值区号(C_RR_NA_1=201，国网)。"""
        return self._cmd(lambda: self._active_master().read_setting_area())

    def switch_setting_area(self, area: int) -> dict:
        """切换定值区(C_SR_NA_1=200，国网)。"""
        return self._cmd(lambda: self._active_master().switch_setting_area(int(area)))

    def get_frames(self, limit: int = 100) -> list:
        with self._lock:
            return list(self._frames[-int(limit) :])

    def clear_frames(self) -> dict:
        with self._lock:
            self._frames.clear()
        return {"ok": True}

    # ---- 事件记录 / 四遥统计 ----

    def get_events(self, sid: str = "", limit: int = 500) -> dict:
        sid = sid or self.store.config.active_session_id
        log = self._event_log(sid)
        return {"ok": True, "events": log.snapshot(limit)}

    def clear_events(self, sid: str = "") -> dict:
        sid = sid or self.store.config.active_session_id
        self._event_log(sid).clear()
        return {"ok": True}

    def get_stats(self, sid: str = "") -> dict:
        sid = sid or self.store.config.active_session_id
        names, cats = {}, {}
        for p in self.store.config.session_points(sid):
            names[p.ioa] = p.name or f"IOA-{p.ioa}"
            cats[p.ioa] = p.category
        rows = self._event_log(sid).stats_rows(names, cats)
        return {"ok": True, "rows": rows}

    def _cmd(self, fn) -> dict:
        try:
            fn()
            return {"ok": True}
        except MasterError as e:
            return {"ok": False, "code": e.code, "error": e.message}
        except Exception as e:
            return {"ok": False, "code": "UNEXPECTED", "error": str(e)}

    def _cmd_confirm(self, send_fn, type_ids: tuple, cots: tuple, ok_text: str,
                     commit_modvals: bool = False, clear_modvals: bool = False) -> dict:
        """发送命令并在后台等待从站确认帧，完成后推送 cmd_result 事件（UI 弹窗）。"""
        try:
            m = self._active_master()
            before = m.i_frame_count()
        except MasterError as e:
            return {"ok": False, "code": e.code, "error": e.message}
        except Exception as e:
            return {"ok": False, "code": "UNEXPECTED", "error": str(e)}

        def _run():
            try:
                send_fn(m)
            except MasterError as e:
                self._push({"type": "cmd_result", "ok": False, "text": f"发送失败：{e.message}"})
                return
            except Exception as e:
                self._push({"type": "cmd_result", "ok": False, "text": f"发送失败：{e}"})
                return
            timeout = float(getattr(getattr(m, "_params", None), "cmd_timeout", 30.0) or 30.0)
            r = m.wait_cmd_result(set(type_ids), set(cots), before, timeout)
            if r is None:
                self._push({
                    "type": "cmd_result", "ok": False,
                    "text": "等待从站确认超时（未收到对应回复）",
                })
            elif not r.get("pos", True):
                self._push({
                    "type": "cmd_result", "ok": False,
                    "text": "从站返回否定确认：命令被拒绝（传送原因 %s）" % r.get("cot"),
                })
            else:
                # [AGENT_CHANGE_BEGIN] 2026-09-10 固化撤销清修改值
                self._push({
                    "type": "cmd_result", "ok": True, "text": ok_text,
                    "commit_modvals": commit_modvals,
                    "clear_modvals": clear_modvals or commit_modvals,
                })
                # [AGENT_CHANGE_END] 2026-09-10 固化撤销清修改值

        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True}


# [AGENT_CHANGE_END] 2026-09-07 多主站API桥
