# IEC 60870-5-101 master over serial line (FT1.2), unbalanced & balanced modes.
from __future__ import annotations

import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional

from protocol.iec104 import codec as codec104
from protocol.iec104 import MasterError
from protocol.iec104.const import cot_name

from . import link


class Iec101Error(MasterError):
    """与 104 MasterError 同源，便于 API 层统一捕获。"""

    def __init__(self, code: str, message: str):
        super().__init__(code, message)


@dataclass
class SerialParams:
    port: str = "COM1"
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "E"          # N / E / O
    stopbits: int = 1
    link_addr: int = 1         # 链路地址
    addr_size: int = 1         # 链路地址长度(1/2 字节)
    balanced: bool = False     # True=平衡方式, False=非平衡方式
    poll_period: float = 1.0   # 非平衡轮询周期(秒)
    resp_timeout: float = 5.0  # 等待应答超时(秒)
    common_address: int = 1
    originator: int = 0
    cot_size: int = 2
    ca_size: int = 2
    ioa_size: int = 2
    poll_level2: bool = True   # 轮询时召唤 2 级数据
    tx_delay_ms: float = 0.0


class Iec101Master:
    """101 主站：非平衡(主动轮询) / 平衡(双方可发起)。事件回调与 104 对齐。"""

    MONITOR_TYPES = {1, 3, 5, 7, 9, 11, 13, 15, 30, 31, 36}

    def __init__(self, on_event: Optional[Callable[[dict], None]] = None):
        self.on_event = on_event or (lambda _e: None)
        self.session_id = ""
        self._ser = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._params: Optional[SerialParams] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._buf = bytearray()
        self._fcb = False              # 主站发送帧计数位(翻转)
        self._i_frames = 0
        self._i_msgs: List[tuple] = []
        self._last_rx = 0.0
        self._last_tx = 0.0
        self._connected = False
        self._ack_event = threading.Event()
        self._ack_ok = False
        self._acd = False              # 从站请求访问位

    # ---------- helpers ----------
    def _emit(self, event: dict) -> None:
        if "session_id" not in event and self.session_id:
            event = {**event, "session_id": self.session_id}
        try:
            self.on_event(event)
        except Exception:
            pass

    def _log(self, text: str) -> None:
        self._emit({"type": "log", "text": text, "ts": time.time()})

    def i_frame_count(self) -> int:
        return self._i_frames

    def _put_i_msg(self, type_id: int, cot: int, pos: bool) -> None:
        self._i_msgs.append((self._i_frames, type_id, cot, pos))
        if len(self._i_msgs) > 128:
            del self._i_msgs[:64]

    def wait_cmd_result(self, type_ids: set, cots: set, before: int, timeout: float):
        end = time.time() + max(0.1, float(timeout))
        while time.time() < end:
            for count, tid, cot, pos in list(self._i_msgs):
                if count > before and tid in type_ids and cot in cots:
                    return {"type_id": tid, "cot": cot, "pos": pos}
            time.sleep(0.05)
        return None

    def wait_i_frame(self, before: int, timeout: float) -> bool:
        end = time.time() + max(0.1, float(timeout))
        while time.time() < end:
            if self._i_frames > before:
                return True
            time.sleep(0.05)
        return False

    # ---------- link ----------
    def connect(self, p: SerialParams) -> None:
        import serial  # pyserial

        self.disconnect()
        try:
            self._ser = serial.Serial(
                port=p.port,
                baudrate=int(p.baudrate),
                bytesize=int(p.bytesize),
                parity=str(p.parity or "E"),
                stopbits=int(p.stopbits),
                timeout=0.2,
            )
        except Exception as e:  # serial.SerialException 等
            raise Iec101Error("SERIAL_OPEN_FAILED", f"串口打开失败：{p.port} — {e}") from e
        self._params = p
        self._buf.clear()
        self._stop.clear()
        self._fcb = False
        self._i_frames = 0
        self._i_msgs.clear()
        self._last_rx = time.time()
        self._last_tx = time.time()
        self._connected = True
        self._emit({
            "type": "connection",
            "state": "connected",
            "session_id": self.session_id,
            "local": p.port,
            "remote": f"101/{'平衡' if p.balanced else '非平衡'}",
            "message": f"串口已打开 {p.port} {p.baudrate} {p.bytesize}{p.parity}{p.stopbits}"
                       f"（{'平衡' if p.balanced else '非平衡'}方式, 链路地址={p.link_addr}）",
        })
        self._rx_thread = threading.Thread(target=self._rx_loop, name="iec101-rx", daemon=True)
        self._rx_thread.start()
        # 链路初始化：复位链路 → 请求链路状态
        try:
            self._reset_link()
        except Iec101Error:
            pass
        if not p.balanced:
            self._poll_thread = threading.Thread(target=self._poll_loop, name="iec101-poll", daemon=True)
            self._poll_thread.start()
            self._log("101 非平衡链路初始化完成，开始周期轮询（召唤 2 级数据）")
        else:
            self._log("101 平衡链路初始化完成（双方可发起传输）")

    def disconnect(self) -> None:
        was = self._connected
        self._stop.set()
        self._connected = False
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
        self._poll_thread = None
        if was:
            self._emit({
                "type": "connection",
                "state": "disconnected",
                "session_id": self.session_id,
                "message": "串口已关闭",
            })

    @property
    def connected(self) -> bool:
        return self._connected

    def _ensure(self) -> None:
        if not self._connected or not self._ser:
            raise Iec101Error("NOT_CONNECTED", "尚未连接从站")

    def _send(self, frame: bytes, note: str = "") -> None:
        self._ensure()
        assert self._params
        if self._params.tx_delay_ms:
            time.sleep(self._params.tx_delay_ms / 1000.0)
        with self._lock:
            try:
                self._ser.write(frame)
                self._ser.flush()
            except Exception as e:
                self._connected = False
                raise Iec101Error("SEND_FAILED", f"串口发送失败：{e}") from e
            self._last_tx = time.time()
        self._emit({
            "type": "frame", "direction": "TX",
            "hex": codec104.hex_dump(frame), "note": note, "ts": time.time(),
        })

    def _next_fcb(self) -> bool:
        self._fcb = not self._fcb
        return self._fcb

    def _send_fixed(self, ctrl: int, note: str) -> None:
        assert self._params
        self._send(link.build_fixed(ctrl, self._params.link_addr, self._params.addr_size), note)

    def _wait_ack(self, timeout: Optional[float] = None) -> bool:
        assert self._params
        self._ack_event.clear()
        ok = self._ack_event.wait(timeout or self._params.resp_timeout)
        return ok and self._ack_ok

    def _reset_link(self) -> None:
        """复位链路(FC=0) 并请求链路状态(FC=9)。"""
        assert self._params
        p = self._params
        if p.balanced:
            ctrl = link.ctrl_balanced(link.FC_RESET_LINK, True, self._next_fcb(), True)
        else:
            ctrl = link.ctrl_primary(link.FC_RESET_LINK, self._next_fcb(), True)
        self._send_fixed(ctrl, "复位链路(FC=0)")
        self._wait_ack()
        ctrl = link.ctrl_balanced(link.FC_REQ_LINK_STATUS, True, False, False) if p.balanced \
            else link.ctrl_primary(link.FC_REQ_LINK_STATUS, False, False)
        self._send_fixed(ctrl, "请求链路状态(FC=9)")
        self._wait_ack()

    def _request_level(self, level: int) -> None:
        """非平衡：召唤 1/2 级数据。"""
        assert self._params
        fc = link.FC_REQ_LEVEL1 if level == 1 else link.FC_REQ_LEVEL2
        ctrl = link.ctrl_primary(fc, False, False)
        self._send_fixed(ctrl, f"召唤{level}级数据(FC={fc})")

    def _poll_loop(self) -> None:
        assert self._params
        p = self._params
        while not self._stop.is_set():
            time.sleep(max(0.05, p.poll_period))
            if not self._connected:
                continue
            try:
                if self._acd:
                    self._acd = False
                    self._request_level(1)
                elif p.poll_level2:
                    self._request_level(2)
            except Iec101Error:
                pass

    def _send_asdu(self, asdu: bytes, note: str = "", level: int = 1) -> None:
        """发送 ASDU：非平衡=用户数据(FC=3)等 ACK；平衡=用户数据(DIR=1)等确认。"""
        assert self._params
        p = self._params
        if p.balanced:
            ctrl = link.ctrl_balanced(link.FC_USER_DATA, True, self._next_fcb(), True)
            note = note or "用户数据(平衡)"
        else:
            ctrl = link.ctrl_primary(link.FC_USER_DATA, self._next_fcb(), False)
            note = note or "用户数据(FC=3)"
        frame = link.build_variable(ctrl, p.link_addr, asdu, p.addr_size)
        self._send(frame, note)
        self._wait_ack()

    # ---------- RX ----------
    def _rx_loop(self) -> None:
        while not self._stop.is_set() and self._ser:
            try:
                data = self._ser.read(256)
            except Exception as e:
                if not self._stop.is_set():
                    self._log(f"链路断开：串口读取异常（{e}）")
                    self._emit({"type": "connection", "state": "disconnected",
                                "message": f"串口读取异常：{e}"})
                self._connected = False
                break
            if not data:
                continue
            self._last_rx = time.time()
            _as = self._params.addr_size if self._params else 1
            for frame in link.feed(self._buf, data, _as):
                try:
                    self._handle_frame(frame)
                except Exception:
                    pass
        self._connected = False

    def _handle_frame(self, frame: bytes) -> None:
        info = link.parse_frame(frame, self._params.addr_size if self._params else 1)
        kind = info["kind"]
        if kind == "single":
            self._emit({"type": "frame", "direction": "RX", "hex": "E5",
                        "note": "单字符确认(E5)", "ts": time.time()})
            return
        if kind == "fixed":
            fc = info["fc"]
            acd = info.get("acd")
            if acd:
                self._acd = True
            if not info["prm"] and fc in (link.FC_ACK, link.FC_NACK, link.FC_NO_DATA, link.FC_LINK_BUSY):
                self._ack_ok = (fc == link.FC_ACK)
                self._ack_event.set()
            names = {0: "确认(ACK)", 1: "否认(NACK)", 8: "用户数据", 9: "无所召唤数据", 11: "链路忙"}
            note = names.get(fc, f"固定帧 FC={fc}")
            if info["prm"]:
                note = f"从站固定帧(PRM=1) FC={fc}"
            self._emit({"type": "frame", "direction": "RX", "hex": codec104.hex_dump(frame),
                        "note": note + ("（ACD=1 请求访问）" if acd else ""), "ts": time.time()})
            return
        # variable
        asdu_raw = info["asdu"]
        p = self._params
        parsed = None
        note = "可变帧(ASDU 解析失败)"
        if p and asdu_raw:
            try:
                parsed = codec104.decode_asdu(
                    asdu_raw, cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size
                )
                note = f"数据 {parsed.type_name} 原因={cot_name(parsed.cot)}"
                if parsed.cot in (1, 7, 9, 10, 11):
                    note += "(肯定)" if parsed.pos else "(否定)"
                note += f" 公共地址={parsed.ca}"
                objs = parsed.objects
                if objs:
                    parts = []
                    for o in objs[:8]:
                        v = o.value
                        vs = "合" if v is True else ("分" if v is False else str(v))
                        parts.append(f"IOA={o.ioa} 值={vs}")
                    if len(objs) > 8:
                        parts.append(f"…共{len(objs)}个")
                    note += " | " + " ; ".join(parts)
            except Exception:
                parsed = None
        self._emit({
            "type": "frame", "direction": "RX",
            "hex": codec104.hex_dump(frame), "note": note, "ts": time.time(),
            "asdu": None if not parsed else {
                "type_id": parsed.type_id, "type_name": parsed.type_name,
                "cot": parsed.cot, "ca": parsed.ca,
                "objects": [
                    {"ioa": o.ioa, "value": o.value, "quality": o.quality, "extra": o.extra}
                    for o in parsed.objects
                ],
            },
        })
        # 链路确认：非平衡收到数据帧回 ACK；平衡回用户数据确认(FC=4)
        try:
            if p and p.balanced:
                self._send_fixed(link.ctrl_balanced(link.FC_USER_DATA_CONF, True), "用户数据确认(FC=4)")
            elif p:
                self._send_fixed(link.ctrl_secondary(link.FC_ACK), "确认(ACK)")
        except Iec101Error:
            pass
        if parsed is None:
            return
        self._i_frames += 1
        self._put_i_msg(parsed.type_id, parsed.cot, parsed.pos)
        if parsed.objects and parsed.type_id in self.MONITOR_TYPES:
            self._emit({
                "type": "points",
                "ca": parsed.ca,
                "type_id": parsed.type_id,
                "cot": parsed.cot,
                "objects": [
                    {"ioa": o.ioa, "value": o.value, "quality": o.quality, "extra": o.extra,
                     "type_id": parsed.type_id, "pos": parsed.pos}
                    for o in parsed.objects
                ],
            })

    # ---------- user commands (same names as Iec104Master) ----------
    def general_interrogation(self) -> None:
        self._ensure()
        assert self._params
        p = self._params
        asdu = codec104.build_interrogation(p.common_address, oa=p.originator,
                                            cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size)
        self._send_asdu(asdu, note="总召唤")

    def clock_sync(self) -> None:
        self._ensure()
        assert self._params
        p = self._params
        asdu = codec104.build_clock_sync(p.common_address, oa=p.originator,
                                         cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size)
        self._send_asdu(asdu, note="时钟同步")

    def single_command(self, ioa: int, on: bool, select: bool, cancel: bool = False) -> None:
        self._ensure()
        assert self._params
        p = self._params
        asdu = codec104.build_single_command(
            p.common_address, ioa, on, select, oa=p.originator,
            cot=8 if cancel else 6, cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size)
        self._send_asdu(asdu, note=f"单点遥控 IOA={ioa} {'选择' if select else '执行'} {'合' if on else '分'}")

    def double_command(self, ioa: int, state: int, select: bool, cancel: bool = False) -> None:
        self._ensure()
        assert self._params
        p = self._params
        asdu = codec104.build_double_command(
            p.common_address, ioa, state, select, oa=p.originator,
            cot=8 if cancel else 6, cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size)
        self._send_asdu(asdu, note=f"双点遥控 IOA={ioa} {'选择' if select else '执行'} 值={state}")

    def setpoint_float(self, ioa: int, value: float, select: bool, cancel: bool = False) -> None:
        self._ensure()
        assert self._params
        p = self._params
        asdu = codec104.build_setpoint_float(
            p.common_address, ioa, value, select, oa=p.originator,
            cot=8 if cancel else 6, cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size)
        self._send_asdu(asdu, note=f"浮点设点 IOA={ioa} 值={value}")

    def setpoint_normalized(self, ioa: int, value: float, select: bool, cancel: bool = False) -> None:
        self._ensure()
        assert self._params
        p = self._params
        asdu = codec104.build_setpoint_normalized(
            p.common_address, ioa, value, select, oa=p.originator,
            cot=8 if cancel else 6, cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size)
        self._send_asdu(asdu, note=f"归一化设点 IOA={ioa} 值={value}")

    def read_setpoints_batch(self, ioas: list, tid: int = 108, area: int = 1) -> None:
        self._ensure()
        assert self._params
        p = self._params
        asdu = codec104.build_read_param_batch(
            p.common_address, [int(i) for i in ioas], tid=tid, area=int(area), oa=p.originator,
            cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size)
        self._send_asdu(asdu, note=f"定值召唤({tid}) 共{len(ioas)}个 区号={area}")

    def preset_param(self, ioa: int, value: float, select: bool = True, cancel: bool = False,
                     tid: int = 55, area: int = 1) -> None:
        self._ensure()
        assert self._params
        p = self._params
        if cancel:
            cot, pi = 8, 0x40
        elif select:
            cot, pi = 6, 0x80
        else:
            cot, pi = 6, 0x00
        if tid == 203:
            items = [(ioa, value)] if select and not cancel else []
            asdu = codec104.build_write_param(
                p.common_address, items, area=area, pi=pi, oa=p.originator, cot=cot, tid=tid,
                cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size)
        else:
            asdu = codec104.build_preset_param(
                p.common_address, ioa, value, select=select, oa=p.originator,
                cot=8 if cancel else 6, tid=tid,
                cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size)
        action = "撤销" if cancel else ("预置" if select else "固化")
        self._send_asdu(asdu, note=f"定值整定({tid}) IOA={ioa} {action} value={value} 区号={area}")

    def read_setting_area(self) -> None:
        self._ensure()
        assert self._params
        p = self._params
        asdu = codec104.build_area_command(
            p.common_address, value=0, tid=201, oa=p.originator,
            cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size)
        self._send_asdu(asdu, note="读定值区号(201)")

    def switch_setting_area(self, area: int) -> None:
        self._ensure()
        assert self._params
        p = self._params
        asdu = codec104.build_area_command(
            p.common_address, value=int(area), tid=200, oa=p.originator,
            cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size)
        self._send_asdu(asdu, note=f"切换定值区(200) 区号={area}")
