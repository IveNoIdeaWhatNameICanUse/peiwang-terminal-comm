# [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
"""海南加密模块多通道：串口上对 101 FT1.2 业务帧做 AA 封装，按中心编号分流。

帧格式（需求文档）：
  AA | L(2B 小端=业务长度 N) | 中心编号(1B) | 业务数据(N) | CS | 55
CS = 从 L 起至业务数据末的累加和(模 256)。
"""
from __future__ import annotations

import threading
from typing import Callable, Dict, Iterator, Optional, Tuple

HEAD = 0xAA
TAIL = 0x55


def wrap(center_id: int, payload: bytes) -> bytes:
    """封装业务帧。center_id: 1~255。"""
    cid = max(1, min(255, int(center_id)))
    n = len(payload)
    mid = bytes([n & 0xFF, (n >> 8) & 0xFF, cid & 0xFF]) + payload
    cs = sum(mid) & 0xFF
    return bytes([HEAD]) + mid + bytes([cs, TAIL])


def feed(buf: bytearray, data: bytes) -> Iterator[Tuple[int, bytes]]:
    """追加字节并产出完整 AA 帧：(center_id, payload)。"""
    buf.extend(data)
    while True:
        if not buf:
            return
        if buf[0] != HEAD:
            del buf[0]
            continue
        if len(buf) < 5:  # AA L L CID … CS 55 至少还需长度字段
            return
        n = buf[1] | (buf[2] << 8)
        total = 1 + 2 + 1 + n + 1 + 1  # AA+L+CID+data+CS+55
        if len(buf) < total:
            return
        frame = bytes(buf[:total])
        del buf[:total]
        if frame[-1] != TAIL:
            continue
        mid = frame[1:-2]  # L..data
        if (sum(mid) & 0xFF) != frame[-2]:
            continue
        center_id = frame[3]
        payload = frame[4:-2]
        if len(payload) != n:
            continue
        yield center_id, payload


class HainanSerialHub:
    """同 COM 多主站共享：按中心编号注册回调，RX 解封装后分发。"""

    _registry: Dict[str, "HainanSerialHub"] = {}
    _reg_lock = threading.Lock()

    def __init__(self, port: str, baudrate: int, bytesize: int, parity: str, stopbits: int):
        self.port = port
        self.baudrate = int(baudrate)
        self.bytesize = int(bytesize)
        self.parity = str(parity or "N")
        self.stopbits = int(stopbits)
        self._ser = None
        self._stop = threading.Event()
        self._rx_thread: Optional[threading.Thread] = None
        self._buf = bytearray()
        self._wlock = threading.Lock()
        self._clients: Dict[int, Callable[[bytes], None]] = {}
        self._owners: Dict[str, int] = {}  # owner_key -> center_id

    @classmethod
    def register(
        cls,
        owner_key: str,
        center_id: int,
        port: str,
        baudrate: int,
        bytesize: int,
        parity: str,
        stopbits: int,
        on_payload: Callable[[bytes], None],
    ) -> "HainanSerialHub":
        cid = max(1, min(255, int(center_id)))
        key = str(port).upper()
        with cls._reg_lock:
            hub = cls._registry.get(key)
            if hub is None:
                hub = HainanSerialHub(port, baudrate, bytesize, parity, stopbits)
                hub._open()
                cls._registry[key] = hub
            else:
                if (
                    hub.baudrate != int(baudrate)
                    or hub.bytesize != int(bytesize)
                    or hub.parity != str(parity or "N")
                    or hub.stopbits != int(stopbits)
                ):
                    raise RuntimeError(
                        f"串口 {port} 已被海南双主站占用，且波特率/校验参数不一致"
                    )
                old_cid = hub._owners.get(owner_key)
                if old_cid is not None and old_cid in hub._clients:
                    del hub._clients[old_cid]
                if cid in hub._clients and hub._owners.get(owner_key) != cid:
                    # 其它会话占用该中心编号
                    if any(o != owner_key and c == cid for o, c in hub._owners.items()):
                        raise RuntimeError(
                            f"串口 {port} 上中心编号 {cid} 已被其它主站占用"
                        )
            hub._clients[cid] = on_payload
            hub._owners[owner_key] = cid
            return hub

    @classmethod
    def unregister(cls, owner_key: str, port: str) -> None:
        key = str(port).upper()
        with cls._reg_lock:
            hub = cls._registry.get(key)
            if not hub:
                return
            cid = hub._owners.pop(owner_key, None)
            if cid is not None:
                hub._clients.pop(cid, None)
            if not hub._owners:
                hub._close()
                cls._registry.pop(key, None)

    def _open(self) -> None:
        import serial

        self._ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=self.bytesize,
            parity=self.parity,
            stopbits=self.stopbits,
            timeout=0.2,
        )
        self._stop.clear()
        self._rx_thread = threading.Thread(
            target=self._rx_loop, name=f"hainan-rx-{self.port}", daemon=True
        )
        self._rx_thread.start()

    def _close(self) -> None:
        self._stop.set()
        ser = self._ser
        self._ser = None
        if ser:
            try:
                ser.close()
            except Exception:
                pass
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1.5)
        self._rx_thread = None
        self._buf.clear()

    def write(self, center_id: int, payload: bytes) -> None:
        if not self._ser:
            raise RuntimeError("海南串口未打开")
        wire = wrap(center_id, payload)
        with self._wlock:
            self._ser.write(wire)
            self._ser.flush()

    def _rx_loop(self) -> None:
        while not self._stop.is_set() and self._ser:
            try:
                data = self._ser.read(256)
            except Exception:
                break
            if not data:
                continue
            for cid, payload in feed(self._buf, data):
                cb = self._clients.get(cid)
                if cb:
                    try:
                        cb(payload)
                    except Exception:
                        pass
# [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装
