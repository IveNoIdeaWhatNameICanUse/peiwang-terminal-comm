# Event log & four-telemetry statistics (SOE/COS/control/adjust), per session.
from __future__ import annotations

import threading
import time
from typing import Dict, List


class EventLog:
    """Per-station event record & statistics. Thread-safe."""

    def __init__(self, max_events: int = 2000) -> None:
        self.max_events = max_events
        self._lock = threading.RLock()  # 可重入：add 在 on_* 持锁内被调用
        self._events: List[dict] = []
        self._last_value: Dict[int, object] = {}
        self._last_change: Dict[int, float] = {}
        self._still_notified: Dict[int, bool] = {}
        self._stats: Dict[int, dict] = {}

    def add(self, ioa: int, name: str, content: str, kind: str) -> None:
        with self._lock:
            self._events.append(
                {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    "ioa": ioa,
                    "name": name,
                    "content": content,
                    "kind": kind,
                }
            )
            if len(self._events) > self.max_events:
                del self._events[: len(self._events) - self.max_events]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._stats.clear()
            self._last_value.clear()
            self._last_change.clear()
            self._still_notified.clear()

    def snapshot(self, limit: int = 500) -> List[dict]:
        with self._lock:
            return list(self._events[-limit:])

    def on_link(self, state: str) -> None:
        """Connection start/stop hint; not counted in stats."""
        txt = "链路启动" if state == "start" else "链路停止"
        self.add(0, "", txt, "sys")

    @staticmethod
    def _yx_text(tid: int, value) -> str:
        """遥信值显示：单点(1/30)：1=合、0=分；双点(3/31)：2=合、1=分、0/3=不确定。"""
        if tid in (3, 31):
            return {0: "不确定", 1: "分", 2: "合", 3: "不确定"}.get(int(value or 0), str(value))
        return "合" if value in (1, True, "1") else "分"

    def on_yx(self, ioa: int, name: str, value, tid: int = 1, is_soe: bool = False) -> None:
        """YX: change counts COS only; SOE counted separately (no double)."""
        with self._lock:
            st = self._stats.setdefault(ioa, {})
            st.setdefault("change", 0)
            st.setdefault("soe", 0)
            vs = self._yx_text(tid, value)
            if is_soe:
                # SOE：独立记录，只计 SOE 数量，不影响变位次数
                st["soe"] += 1
                self.add(ioa, name, "%s（SOE %s）" % (vs, name), "soe")
                return
            old = self._last_value.get(ioa)
            if old == value:
                return
            self._last_value[ioa] = value
            st["change"] += 1
            self.add(ioa, name, "%s %s" % (name, vs), "cos")

    def on_yc(self, ioa: int, name: str, value, upper=None, lower=None,
              dead_band=None, no_change_time=None) -> None:
        """YC: upper/lower limit, dead-band jump, still-change alarm."""
        try:
            val = float(value)
        except (TypeError, ValueError):
            return
        with self._lock:
            st = self._stats.setdefault(ioa, {})
            for k in ("up", "down", "dead", "still"):
                st.setdefault(k, 0)
            old = self._last_value.get(ioa)
            now = time.time()
            if dead_band and old is not None and abs(val - float(old)) > float(dead_band):
                st["dead"] += 1
                self.add(ioa, name, "突变 %s→%s（死区%s）" % (old, val, dead_band), "mea_dead")
            if upper is not None and val > float(upper):
                st["up"] += 1
                self.add(ioa, name, "越上限 %s>%s" % (val, upper), "mea_up")
            if lower is not None and val < float(lower):
                st["down"] += 1
                self.add(ioa, name, "越下限 %s<%s" % (val, lower), "mea_down")
            if no_change_time and float(no_change_time) > 0:
                if old is None or float(old) != val:
                    self._last_change[ioa] = now
                    self._still_notified[ioa] = False
                elif now - self._last_change.get(ioa, now) > float(no_change_time):
                    if not self._still_notified.get(ioa):
                        self._still_notified[ioa] = True
                        st["still"] += 1
                        self.add(ioa, name, "长期不变（%ss）" % (no_change_time,), "mea_still")
            self._last_value[ioa] = val

    def on_control(self, ioa: int, name: str, action: str, on: bool) -> None:
        """action: sel / exec / cancel. on=True 合, on=False 分."""
        if action == "sel":
            key = "selon" if on else "seloff"
            kind = "ctrl_sel"
            label = "预选"
        elif action == "exec":
            key = "exeon" if on else "exeoff"
            kind = "ctrl_exec"
            label = "执行"
        else:
            key = "cancel"
            kind = "ctrl_cancel"
            label = "撤销"
        with self._lock:
            st = self._stats.setdefault(ioa, {})
            st[key] = st.get(key, 0) + 1
            act = "合" if on else "分"
            self.add(ioa, name, "%s%s %s" % (label, act, name), kind)

    def on_adjust(self, ioa: int, name: str, action: str, value=None) -> None:
        """action: preset / exec / cancel."""
        with self._lock:
            st = self._stats.setdefault(ioa, {})
            st[action] = st.get(action, 0) + 1
            val_txt = "" if value is None else "=%s" % value
            labels = {"preset": "预置", "exec": "执行(固化)", "cancel": "撤销"}
            kinds = {"preset": "adj_preset", "exec": "adj_exec", "cancel": "adj_cancel"}
            self.add(ioa, name, "%s%s %s" % (labels.get(action, action), val_txt, name),
                     kinds.get(action, "adj_preset"))

    def stats_rows(self, names: Dict[int, str], cats: Dict[int, str]) -> List[dict]:
        """Rows sorted by ioa; includes zero-count points from point table."""
        with self._lock:
            out = []
            for ioa in sorted(self._stats):
                st = self._stats[ioa]
                cat = cats.get(ioa, "")
                nm = names.get(ioa, "IOA-%d" % ioa)
                if ioa == 0 and not cat:
                    # 整区固化/撤销(无具体点)归入遥调统计
                    cat = "遥调"
                    nm = "固化(整区)" if st.get("exec") else "撤销(整区)"
                out.append({"ioa": ioa, "name": nm, "cat": cat, **st})
            seen = {r["ioa"] for r in out}
            for ioa, name in names.items():
                if ioa in seen:
                    continue
                out.append({"ioa": ioa, "name": name, "cat": cats.get(ioa, "")})
            out.sort(key=lambda r: r["ioa"])
            return out
