# [AGENT_CHANGE_BEGIN] 2026-09-07 104-MVP主站
"""IEC 104 模拟主站（支持本地 IP/端口绑定）。"""
from __future__ import annotations

import errno
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional

from . import codec
from .const import DEFAULT_PORT, START_BYTE


EventCallback = Callable[[dict], None]


class MasterError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ConnectParams:
    remote_ip: str
    remote_port: int = DEFAULT_PORT
    local_ip: str = ""
    local_port: int = 0  # 0 = OS 分配
    common_address: int = 1
    originator: int = 0
    connect_timeout_s: float = 5.0
    t1: float = 15.0
    t2: float = 10.0
    t3: float = 20.0
    k: int = 12
    w: int = 8


class Iec104Master:
    def __init__(self, on_event: Optional[EventCallback] = None):
        self.on_event = on_event or (lambda _e: None)
        self._sock: Optional[socket.socket] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._ns = 0
        self._nr = 0
        self._ack_pending = 0
        self._connected = False
        self._params: Optional[ConnectParams] = None
        self._buf = bytearray()
        self._last_rx = 0.0
        self._last_tx = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self, params: ConnectParams) -> None:
        if self._connected:
            self.disconnect()

        local_ip = (params.local_ip or "").strip()
        local_port = int(params.local_port or 0)
        if local_port < 0 or local_port > 65535:
            raise MasterError("INVALID_LOCAL_PORT", f"本地端口无效: {local_port}")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(params.connect_timeout_s)

        try:
            if local_ip:
                try:
                    sock.bind((local_ip, local_port))
                except OSError as e:
                    sock.close()
                    raise self._classify_bind_error(local_ip, local_port, e) from e

            try:
                sock.connect((params.remote_ip, int(params.remote_port)))
            except socket.timeout as e:
                sock.close()
                raise MasterError(
                    "CONNECT_TIMEOUT",
                    f"连接超时：无法在 {params.connect_timeout_s:.0f}s 内连通 "
                    f"{params.remote_ip}:{params.remote_port}"
                    + (f"（源 {local_ip}:{local_port or '自动'}）" if local_ip else ""),
                ) from e
            except ConnectionRefusedError as e:
                sock.close()
                raise MasterError(
                    "CONNECT_REFUSED",
                    f"对端拒绝连接：{params.remote_ip}:{params.remote_port}（请确认从站已监听）",
                ) from e
            except OSError as e:
                sock.close()
                raise MasterError(
                    "CONNECT_FAILED",
                    f"连接失败：{params.remote_ip}:{params.remote_port} — {e}",
                ) from e
        except MasterError:
            raise
        except Exception as e:
            try:
                sock.close()
            except OSError:
                pass
            raise MasterError("CONNECT_FAILED", f"连接异常：{e}") from e

        sock.settimeout(1.0)
        self._sock = sock
        self._params = params
        self._ns = 0
        self._nr = 0
        self._ack_pending = 0
        self._buf.clear()
        self._stop.clear()
        self._connected = True
        self._last_rx = time.time()
        self._last_tx = time.time()

        bound = sock.getsockname()
        self._emit(
            {
                "type": "connection",
                "state": "connected",
                "local": f"{bound[0]}:{bound[1]}",
                "remote": f"{params.remote_ip}:{params.remote_port}",
                "message": f"已连接（本地 {bound[0]}:{bound[1]} → {params.remote_ip}:{params.remote_port}）",
            }
        )

        self._rx_thread = threading.Thread(target=self._rx_loop, name="iec104-rx", daemon=True)
        self._rx_thread.start()

        # STARTDT
        self._send_raw(codec.build_u_frame(start_dt=True), direction="TX", note="STARTDT act")

    def disconnect(self) -> None:
        self._stop.set()
        self._connected = False
        sock = self._sock
        self._sock = None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=2.0)
        self._rx_thread = None
        self._emit({"type": "connection", "state": "disconnected", "message": "已断开"})

    def general_interrogation(self) -> None:
        self._ensure()
        assert self._params
        asdu = codec.build_interrogation(self._params.common_address, oa=self._params.originator)
        self._send_i(asdu, note="总召唤")

    def clock_sync(self) -> None:
        self._ensure()
        assert self._params
        asdu = codec.build_clock_sync(self._params.common_address, datetime.now(), oa=self._params.originator)
        self._send_i(asdu, note="时钟同步")

    def single_command(self, ioa: int, on: bool, select: bool) -> None:
        self._ensure()
        assert self._params
        asdu = codec.build_single_command(
            self._params.common_address, ioa, on, select, oa=self._params.originator
        )
        self._send_i(asdu, note=f"单点遥控 IOA={ioa} {'预置' if select else '执行'} {'合' if on else '分'}")

    def double_command(self, ioa: int, state: int, select: bool) -> None:
        self._ensure()
        assert self._params
        asdu = codec.build_double_command(
            self._params.common_address, ioa, state, select, oa=self._params.originator
        )
        self._send_i(asdu, note=f"双点遥控 IOA={ioa} {'预置' if select else '执行'} state={state}")

    def setpoint_float(self, ioa: int, value: float, select: bool) -> None:
        self._ensure()
        assert self._params
        asdu = codec.build_setpoint_float(
            self._params.common_address, ioa, value, select, oa=self._params.originator
        )
        self._send_i(asdu, note=f"浮点设点 IOA={ioa} {'预置' if select else '执行'} value={value}")

    def setpoint_normalized(self, ioa: int, value: float, select: bool) -> None:
        self._ensure()
        assert self._params
        asdu = codec.build_setpoint_normalized(
            self._params.common_address, ioa, value, select, oa=self._params.originator
        )
        self._send_i(asdu, note=f"归一化设点 IOA={ioa} {'预置' if select else '执行'} value={value}")

    # --- internal ---

    def _classify_bind_error(self, local_ip: str, local_port: int, e: OSError) -> MasterError:
        en = getattr(e, "winerror", None) or e.errno
        msg = str(e)
        if en in (errno.EADDRNOTAVAIL, 10049):  # WSAEADDRNOTAVAIL
            return MasterError(
                "LOCAL_IP_NOT_FOUND",
                f"本地 IP 不存在或不可用：{local_ip}（请从网卡列表重新选择）",
            )
        if en in (errno.EADDRINUSE, 10048):  # WSAEADDRINUSE
            return MasterError(
                "LOCAL_PORT_IN_USE",
                f"本地端口已被占用：{local_ip}:{local_port}",
            )
        if en in (errno.EACCES, 10013):
            return MasterError(
                "LOCAL_BIND_DENIED",
                f"本地绑定被拒绝（权限/防火墙）：{local_ip}:{local_port or 0} — {msg}",
            )
        return MasterError("LOCAL_BIND_FAILED", f"本地绑定失败：{local_ip}:{local_port or 0} — {msg}")

    def _ensure(self) -> None:
        if not self._connected or not self._sock:
            raise MasterError("NOT_CONNECTED", "尚未连接从站")

    def _emit(self, event: dict) -> None:
        try:
            self.on_event(event)
        except Exception:
            pass

    def _send_raw(self, data: bytes, direction: str = "TX", note: str = "") -> None:
        with self._lock:
            if not self._sock:
                raise MasterError("NOT_CONNECTED", "尚未连接从站")
            self._sock.sendall(data)
            self._last_tx = time.time()
        self._emit(
            {
                "type": "frame",
                "direction": direction,
                "hex": codec.hex_dump(data),
                "note": note,
                "ts": time.time(),
            }
        )

    def _send_i(self, asdu: bytes, note: str = "") -> None:
        with self._lock:
            frame = codec.build_i_frame(self._ns, self._nr, asdu)
            self._ns = (self._ns + 1) & 0x7FFF
            if not self._sock:
                raise MasterError("NOT_CONNECTED", "尚未连接从站")
            self._sock.sendall(frame)
            self._last_tx = time.time()
        self._emit(
            {
                "type": "frame",
                "direction": "TX",
                "hex": codec.hex_dump(frame),
                "note": note,
                "ts": time.time(),
            }
        )

    def _rx_loop(self) -> None:
        sock = self._sock
        while not self._stop.is_set() and sock:
            try:
                data = sock.recv(4096)
                if not data:
                    self._emit({"type": "connection", "state": "disconnected", "message": "对端关闭连接"})
                    self._connected = False
                    break
                self._last_rx = time.time()
                self._buf.extend(data)
                self._consume_buffer()
            except socket.timeout:
                # TESTFR keepalive
                if self._params and time.time() - self._last_rx > self._params.t3:
                    try:
                        self._send_raw(codec.build_u_frame(testfr=True), note="TESTFR act")
                        self._last_rx = time.time()  # avoid flood; wait peer
                    except MasterError:
                        break
                continue
            except OSError:
                if not self._stop.is_set():
                    self._emit({"type": "connection", "state": "disconnected", "message": "接收中断，连接已断开"})
                self._connected = False
                break
        self._connected = False

    def _consume_buffer(self) -> None:
        while True:
            if len(self._buf) < 2:
                return
            if self._buf[0] != START_BYTE:
                # resync
                try:
                    idx = self._buf.index(START_BYTE)
                    del self._buf[:idx]
                except ValueError:
                    self._buf.clear()
                    return
                if len(self._buf) < 2:
                    return
            length = self._buf[1]
            total = 2 + length
            if len(self._buf) < total:
                return
            frame = bytes(self._buf[:total])
            del self._buf[:total]
            self._handle_frame(frame)

    def _handle_frame(self, frame: bytes) -> None:
        try:
            kind, detail = codec.parse_apci(frame)
        except ValueError:
            self._emit({"type": "frame", "direction": "RX", "hex": codec.hex_dump(frame), "note": "解析失败"})
            return

        note = kind
        if kind == "U":
            if detail.get("start_dt_con"):
                note = "STARTDT con"
            elif detail.get("testfr_act"):
                note = "TESTFR act"
                try:
                    self._send_raw(codec.build_u_frame(testfr=True, con=True), note="TESTFR con")
                except MasterError:
                    pass
            elif detail.get("testfr_con"):
                note = "TESTFR con"
            else:
                note = f"U {detail}"
            self._emit({"type": "frame", "direction": "RX", "hex": codec.hex_dump(frame), "note": note, "ts": time.time()})
            return

        if kind == "S":
            self._emit({"type": "frame", "direction": "RX", "hex": codec.hex_dump(frame), "note": f"S nr={detail.get('nr')}", "ts": time.time()})
            return

        # I frame
        self._nr = (detail["ns"] + 1) & 0x7FFF
        self._ack_pending += 1
        asdu_raw = detail.get("asdu") or b""
        parsed = None
        try:
            parsed = codec.decode_asdu(asdu_raw)
            note = f"I {parsed.type_name} COT={parsed.cot} CA={parsed.ca}"
        except Exception:
            note = "I (ASDU 解析失败)"

        self._emit(
            {
                "type": "frame",
                "direction": "RX",
                "hex": codec.hex_dump(frame),
                "note": note,
                "ts": time.time(),
                "asdu": None
                if not parsed
                else {
                    "type_id": parsed.type_id,
                    "type_name": parsed.type_name,
                    "cot": parsed.cot,
                    "ca": parsed.ca,
                    "objects": [
                        {"ioa": o.ioa, "value": o.value, "quality": o.quality} for o in parsed.objects
                    ],
                },
            }
        )

        if parsed and parsed.objects:
            self._emit(
                {
                    "type": "points",
                    "ca": parsed.ca,
                    "type_id": parsed.type_id,
                    "cot": parsed.cot,
                    "objects": [
                        {"ioa": o.ioa, "value": o.value, "quality": o.quality} for o in parsed.objects
                    ],
                }
            )

        # S 确认
        w = self._params.w if self._params else 8
        if self._ack_pending >= max(1, w // 2):
            try:
                self._send_raw(codec.build_s_frame(self._nr), note=f"S nr={self._nr}")
                self._ack_pending = 0
            except MasterError:
                pass


# [AGENT_CHANGE_END] 2026-09-07 104-MVP主站
