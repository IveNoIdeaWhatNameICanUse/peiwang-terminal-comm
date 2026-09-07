# [AGENT_CHANGE_BEGIN] 2026-09-07 104-MVP核心模型
"""点表与工程配置。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PointDef:
    ioa: int
    type_id: int
    name: str = ""
    category: str = ""  # 遥信/遥测/遥控/遥调
    value: Any = None
    quality: int = 0


@dataclass
class ProjectConfig:
    remote_ip: str = "127.0.0.1"
    remote_port: int = 2404
    local_ip: str = ""
    local_port: int = 0
    common_address: int = 1
    originator: int = 0
    points: List[PointDef] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "remote_ip": self.remote_ip,
            "remote_port": self.remote_port,
            "local_ip": self.local_ip,
            "local_port": self.local_port,
            "common_address": self.common_address,
            "originator": self.originator,
            "points": [asdict(p) for p in self.points],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectConfig":
        pts = [PointDef(**p) for p in data.get("points", [])]
        return cls(
            remote_ip=data.get("remote_ip", "127.0.0.1"),
            remote_port=int(data.get("remote_port", 2404)),
            local_ip=data.get("local_ip", ""),
            local_port=int(data.get("local_port", 0) or 0),
            common_address=int(data.get("common_address", 1)),
            originator=int(data.get("originator", 0)),
            points=pts,
        )


class ProjectStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.config = ProjectConfig()

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

    def upsert_point(self, point: PointDef) -> None:
        for i, p in enumerate(self.config.points):
            if p.ioa == point.ioa:
                self.config.points[i] = point
                return
        self.config.points.append(point)

    def remove_point(self, ioa: int) -> bool:
        before = len(self.config.points)
        self.config.points = [p for p in self.config.points if p.ioa != ioa]
        return len(self.config.points) < before

    def update_values(self, objects: List[dict]) -> List[dict]:
        changed = []
        by_ioa = {p.ioa: p for p in self.config.points}
        for obj in objects:
            ioa = int(obj["ioa"])
            if ioa in by_ioa:
                by_ioa[ioa].value = obj.get("value")
                by_ioa[ioa].quality = int(obj.get("quality") or 0)
                changed.append(asdict(by_ioa[ioa]))
            else:
                p = PointDef(
                    ioa=ioa,
                    type_id=0,
                    name=f"IOA-{ioa}",
                    value=obj.get("value"),
                    quality=int(obj.get("quality") or 0),
                )
                self.config.points.append(p)
                changed.append(asdict(p))
        return changed


# [AGENT_CHANGE_END] 2026-09-07 104-MVP核心模型
