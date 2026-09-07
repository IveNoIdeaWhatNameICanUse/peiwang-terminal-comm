# [AGENT_CHANGE_BEGIN] 2026-09-07 104-MVP服务桥
"""供 UI 调用的 Python API 桥。"""
from __future__ import annotations

import json
import threading
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, List, Optional

from core.events import EventBus
from core.project import PointDef, ProjectStore
from net import nics_as_dicts
from protocol.iec104 import ConnectParams, Iec104Master, MasterError, TYPE_NAMES


class ApiBridge:
    """暴露给前端（pywebview js_api 或 tk 回调）的统一接口。"""

    def __init__(self, root: Path, bus: Optional[EventBus] = None):
        self.root = root
        self.bus = bus or EventBus()
        self.store = ProjectStore()
        self.master = Iec104Master(on_event=self._on_master_event)
        self._ui_push: Optional[Callable[[dict], None]] = None
        self._frames: List[dict] = []
        self._lock = threading.Lock()
        default_cfg = root / "configs" / "default.json"
        if default_cfg.exists():
            try:
                self.store.load(default_cfg)
            except Exception:
                pass

    def set_ui_push(self, push: Callable[[dict], None]) -> None:
        self._ui_push = push

    def _push(self, event: dict) -> None:
        self.bus.emit(event.get("type", "event"), event)
        if self._ui_push:
            try:
                self._ui_push(event)
            except Exception:
                pass

    def _on_master_event(self, event: dict) -> None:
        if event.get("type") == "frame":
            with self._lock:
                self._frames.append(event)
                if len(self._frames) > 500:
                    self._frames = self._frames[-500:]
        if event.get("type") == "points":
            changed = self.store.update_values(event.get("objects") or [])
            event = {**event, "points": changed}
        self._push(event)

    # ---- exposed ----

    def get_nics(self) -> list:
        return nics_as_dicts()

    def get_type_names(self) -> dict:
        return {str(k): v for k, v in TYPE_NAMES.items()}

    def get_project(self) -> dict:
        return self.store.config.to_dict()

    def save_project(self, data: dict, path: str = "") -> dict:
        try:
            self.store.config = self.store.config.from_dict(data)
            target = Path(path) if path else (self.store.path or (self.root / "configs" / "default.json"))
            self.store.save(target)
            return {"ok": True, "path": str(target)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def load_project(self, path: str) -> dict:
        try:
            cfg = self.store.load(Path(path))
            return {"ok": True, "project": cfg.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def upsert_point(self, point: dict) -> dict:
        p = PointDef(
            ioa=int(point["ioa"]),
            type_id=int(point.get("type_id") or 0),
            name=str(point.get("name") or ""),
            category=str(point.get("category") or ""),
            value=point.get("value"),
            quality=int(point.get("quality") or 0),
        )
        self.store.upsert_point(p)
        return {"ok": True, "project": self.store.config.to_dict()}

    def remove_point(self, ioa: int) -> dict:
        ok = self.store.remove_point(int(ioa))
        return {"ok": ok, "project": self.store.config.to_dict()}

    def connect(self, params: dict) -> dict:
        try:
            # 同步到工程配置
            c = self.store.config
            c.remote_ip = str(params.get("remote_ip") or c.remote_ip)
            c.remote_port = int(params.get("remote_port") or c.remote_port)
            c.local_ip = str(params.get("local_ip") or "")
            c.local_port = int(params.get("local_port") or 0)
            c.common_address = int(params.get("common_address") or c.common_address)
            c.originator = int(params.get("originator") or 0)

            cp = ConnectParams(
                remote_ip=c.remote_ip,
                remote_port=c.remote_port,
                local_ip=c.local_ip,
                local_port=c.local_port,
                common_address=c.common_address,
                originator=c.originator,
            )
            self.master.connect(cp)
            return {"ok": True, "message": "连接成功"}
        except MasterError as e:
            return {"ok": False, "code": e.code, "error": e.message}
        except Exception as e:
            return {"ok": False, "code": "UNEXPECTED", "error": str(e), "detail": traceback.format_exc()}

    def disconnect(self) -> dict:
        try:
            self.master.disconnect()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def is_connected(self) -> bool:
        return bool(self.master.connected)

    def general_interrogation(self) -> dict:
        return self._cmd(lambda: self.master.general_interrogation())

    def clock_sync(self) -> dict:
        return self._cmd(lambda: self.master.clock_sync())

    def single_command(self, ioa: int, on: bool, select: bool) -> dict:
        return self._cmd(lambda: self.master.single_command(int(ioa), bool(on), bool(select)))

    def double_command(self, ioa: int, state: int, select: bool) -> dict:
        return self._cmd(lambda: self.master.double_command(int(ioa), int(state), bool(select)))

    def setpoint_float(self, ioa: int, value: float, select: bool) -> dict:
        return self._cmd(lambda: self.master.setpoint_float(int(ioa), float(value), bool(select)))

    def setpoint_normalized(self, ioa: int, value: float, select: bool) -> dict:
        return self._cmd(lambda: self.master.setpoint_normalized(int(ioa), float(value), bool(select)))

    def get_frames(self, limit: int = 100) -> list:
        with self._lock:
            return list(self._frames[-int(limit) :])

    def clear_frames(self) -> dict:
        with self._lock:
            self._frames.clear()
        return {"ok": True}

    def _cmd(self, fn) -> dict:
        try:
            fn()
            return {"ok": True}
        except MasterError as e:
            return {"ok": False, "code": e.code, "error": e.message}
        except Exception as e:
            return {"ok": False, "code": "UNEXPECTED", "error": str(e)}


# [AGENT_CHANGE_END] 2026-09-07 104-MVP服务桥
