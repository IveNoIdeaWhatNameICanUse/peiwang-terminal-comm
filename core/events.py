# [AGENT_CHANGE_BEGIN] 2026-09-07 104-MVP事件总线
"""简单线程安全事件总线。"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable, DefaultDict, List


class EventBus:
    def __init__(self) -> None:
        self._subs: DefaultDict[str, List[Callable]] = defaultdict(list)
        self._lock = threading.Lock()

    def on(self, topic: str, cb: Callable) -> None:
        with self._lock:
            self._subs[topic].append(cb)

    def off(self, topic: str, cb: Callable) -> None:
        with self._lock:
            if cb in self._subs[topic]:
                self._subs[topic].remove(cb)

    def emit(self, topic: str, payload=None) -> None:
        with self._lock:
            subs = list(self._subs.get(topic, []))
            subs += list(self._subs.get("*", []))
        for cb in subs:
            try:
                cb(payload)
            except Exception:
                pass


# [AGENT_CHANGE_END] 2026-09-07 104-MVP事件总线
