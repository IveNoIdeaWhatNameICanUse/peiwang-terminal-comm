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
# [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
from . import hainan as hainan_mux
# [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装


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
    balanced: bool = False     # True=平衡方式, False=非平衡方式（兼容旧字段）
    # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
    link_mode: str = ""                # unbalanced | balanced | hainan；空则由 balanced 推导
    center_id: int = 1                 # 海南双主站中心编号 1~255
    # [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装
    data_frame_dir: bool = True   # 用户数据帧带 DIR 位(现场 KW-2200: 带 -> 0xF3/0xD3)
    # [AGENT_CHANGE_BEGIN] 2026-09-10 101可变帧校验和按用户数据求和
    cs_compat: bool = False       # True=校验和再 ^0x80；KW-2200/F30 现场为标准求和，默认 False
    # [AGENT_CHANGE_END] 2026-09-10 101可变帧校验和按用户数据求和
    poll_period: float = 1.0   # 非平衡轮询周期(秒)
    resp_timeout: float = 5.0  # 等待应答超时(秒)
    common_address: int = 1
    originator: int = 0
    cot_size: int = 2
    ca_size: int = 2
    ioa_size: int = 2
    poll_level2: bool = True   # 轮询时召唤 2 级数据
    tx_delay_ms: float = 0.0
    # [AGENT_CHANGE_BEGIN] 2026-09-10 忽略FCB位错误
    ignore_fcb_error: bool = False  # True=用户数据 FCV=0/FCB=0，兼容从站 FCB 翻转异常
    # [AGENT_CHANGE_END] 2026-09-10 忽略FCB位错误
    # [AGENT_CHANGE_BEGIN] 2026-09-10 设备参数周期
    gi_period_min: int = 15         # 总召唤周期(分钟)，0=禁用
    clock_period_min: int = 10      # 校时周期(分钟)，0=禁用
    heartbeat_period: int = 30      # 心跳测试周期(秒)，0=禁用；发 FC=2(D2/F2)
    # [AGENT_CHANGE_END] 2026-09-10 设备参数周期

    def __post_init__(self) -> None:
        # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
        mode = str(self.link_mode or "").strip().lower()
        if mode not in ("unbalanced", "balanced", "hainan"):
            mode = "balanced" if self.balanced else "unbalanced"
        self.link_mode = mode
        # 海南双主站：业务链路按平衡 101，外层再做 AA 封装
        self.balanced = mode in ("balanced", "hainan")
        self.center_id = max(1, min(255, int(self.center_id or 1)))
        # [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装

    @property
    def is_hainan(self) -> bool:
        return self.link_mode == "hainan"


class Iec101Master:
    """101 主站：非平衡(主动轮询) / 平衡(双方可发起)。事件回调与 104 对齐。"""

    # [AGENT_CHANGE_BEGIN] 2026-09-10 101定值回读写入四遥
    # 与 104 对齐：55/108/202/203 定值回读也发 points，写入遥调点表
    MONITOR_TYPES = {1, 3, 5, 7, 9, 11, 13, 15, 30, 31, 36, 55, 108, 202, 203}
    # [AGENT_CHANGE_END] 2026-09-10 101定值回读写入四遥

    def __init__(self, on_event: Optional[Callable[[dict], None]] = None):
        self.on_event = on_event or (lambda _e: None)
        self.session_id = ""
        self._ser = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._params: Optional[SerialParams] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._init_thread: Optional[threading.Thread] = None
        # [AGENT_CHANGE_BEGIN] 2026-09-10 设备参数周期
        self._timer_thread: Optional[threading.Thread] = None
        self._last_gi = 0.0
        self._last_clock = 0.0
        self._last_heartbeat = 0.0
        # [AGENT_CHANGE_END] 2026-09-10 设备参数周期
        self._buf = bytearray()
        self._fcb = False              # 主站发送帧计数位(翻转)
        self._i_frames = 0
        self._i_msgs: List[tuple] = []
        self._last_rx = 0.0
        self._last_tx = 0.0
        self._connected = False
        self._ack_event = threading.Event()
        self._ack_ok = False
        self._ack_note = ""            # 确认类型说明(如 链路忙)
        # [AGENT_CHANGE_BEGIN] 2026-09-10 101可变帧校验和按用户数据求和
        self._cs_compat = False        # 连接后按探测/参数更新
        # [AGENT_CHANGE_END] 2026-09-10 101可变帧校验和按用户数据求和
        self._dir_override: Optional[bool] = None   # 探测期间临时覆盖“数据帧带DIR”
        self._saw_init_end = False     # 是否已收到从站“初始化结束”(M_EI_NA_1)
        self._peer_active_frames = 0   # 从站主动发起帧计数(49/40 等)
        self._acd = False              # 从站请求访问位
        # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
        self._hub: Optional[hainan_mux.HainanSerialHub] = None
        self._hub_port: str = ""
        # [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装

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
        self._params = p
        self._cs_compat = bool(getattr(p, "cs_compat", False))
        self._dir_override = None
        self._saw_init_end = False
        self._peer_active_frames = 0
        self._buf.clear()
        self._stop.clear()
        self._fcb = False
        self._i_frames = 0
        self._i_msgs.clear()
        self._last_rx = time.time()
        self._last_tx = time.time()

        # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
        if p.is_hainan:
            try:
                self._hub_port = p.port
                self._hub = hainan_mux.HainanSerialHub.register(
                    owner_key=self.session_id or id(self),
                    center_id=p.center_id,
                    port=p.port,
                    baudrate=int(p.baudrate),
                    bytesize=int(p.bytesize),
                    parity=str(p.parity or "E"),
                    stopbits=int(p.stopbits),
                    on_payload=self._on_hainan_payload,
                )
            except Exception as e:
                self._hub = None
                raise Iec101Error("SERIAL_OPEN_FAILED", f"海南双主站串口失败：{p.port} — {e}") from e
            self._ser = None
            mode_txt = f"海南双主站(中心{p.center_id})"
        else:
            try:
                self._ser = serial.Serial(
                    port=p.port,
                    baudrate=int(p.baudrate),
                    bytesize=int(p.bytesize),
                    parity=str(p.parity or "E"),
                    stopbits=int(p.stopbits),
                    timeout=0.2,
                )
            except Exception as e:
                raise Iec101Error("SERIAL_OPEN_FAILED", f"串口打开失败：{p.port} — {e}") from e
            self._hub = None
            mode_txt = "平衡" if p.balanced else "非平衡"
        # [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装

        self._connected = True
        self._emit({
            "type": "connection",
            "state": "connected",
            "session_id": self.session_id,
            "local": p.port,
            "remote": f"101/{mode_txt}",
            "message": f"串口已打开 {p.port} {p.baudrate} {p.bytesize}{p.parity}{p.stopbits}"
                       f"（{mode_txt}, 链路地址={p.link_addr}）",
        })
        # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
        if not p.is_hainan:
            self._rx_thread = threading.Thread(target=self._rx_loop, name="iec101-rx", daemon=True)
            self._rx_thread.start()
        # [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装
        # [AGENT_CHANGE_BEGIN] 2026-09-10 设备参数周期
        now = time.time()
        self._last_gi = now
        self._last_clock = now
        self._last_heartbeat = now
        if not self._timer_thread or not self._timer_thread.is_alive():
            self._timer_thread = threading.Thread(target=self._timer_loop, name="iec101-timer", daemon=True)
            self._timer_thread.start()
        # [AGENT_CHANGE_END] 2026-09-10 设备参数周期
        # 链路初始化（复位/请求链路状态）放后台线程，避免阻塞界面
        self._init_thread = threading.Thread(target=self._init_link, name="iec101-init", daemon=True)
        self._init_thread.start()
        if not p.balanced:
            # 轮询不等待链路初始化：连接后立即按周期召唤，便于现场尽快看到报文
            self._poll_thread = threading.Thread(target=self._poll_loop, name="iec101-poll", daemon=True)
            self._poll_thread.start()
            self._log(f"101 非平衡方式：开始周期轮询召唤 2 级数据（周期 {p.poll_period:g}s）")
        elif p.is_hainan:
            # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站改平衡链路
            self._log(f"101 海南双主站：平衡链路 + AA 封装，中心编号={p.center_id}（同 COM 按编号分流）")
            # [AGENT_CHANGE_END] 2026-09-10 海南双主站改平衡链路
        else:
            self._log("101 平衡方式：双方可发起传输（日志上送取决于从站）")

    def disconnect(self) -> None:
        was = self._connected
        self._stop.set()
        self._connected = False
        # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
        if self._hub is not None:
            try:
                hainan_mux.HainanSerialHub.unregister(
                    self.session_id or str(id(self)), self._hub_port or (self._params.port if self._params else "")
                )
            except Exception:
                pass
            self._hub = None
            self._hub_port = ""
        # [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装
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
        self._init_thread = None
        # [AGENT_CHANGE_BEGIN] 2026-09-10 设备参数周期
        self._timer_thread = None
        # [AGENT_CHANGE_END] 2026-09-10 设备参数周期
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
        # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
        if not self._connected or not self._params:
            raise Iec101Error("NOT_CONNECTED", "尚未连接从站")
        if self._params.is_hainan:
            if self._hub is None:
                raise Iec101Error("NOT_CONNECTED", "尚未连接从站")
        elif not self._ser:
            raise Iec101Error("NOT_CONNECTED", "尚未连接从站")
        # [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装

    def _on_hainan_payload(self, payload: bytes) -> None:
        """海南 Hub 分发的业务 FT1.2 字节 → 组帧处理。"""
        # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
        if not self._connected or not self._params:
            return
        self._last_rx = time.time()
        _as = self._params.addr_size
        frames = list(link.feed(self._buf, payload, _as))
        for frame in frames:
            try:
                self._handle_frame(frame)
            except Exception:
                pass
        # [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装

    def _send(self, frame: bytes, note: str = "") -> None:
        self._ensure()
        assert self._params
        if self._params.tx_delay_ms:
            time.sleep(self._params.tx_delay_ms / 1000.0)
        # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
        wire = frame
        show_note = note
        if self._params.is_hainan and self._hub is not None:
            wire = hainan_mux.wrap(self._params.center_id, frame)
            show_note = f"{note}[海南中心{self._params.center_id}]" if note else f"[海南中心{self._params.center_id}]"
        with self._lock:
            try:
                if self._params.is_hainan and self._hub is not None:
                    self._hub.write(self._params.center_id, frame)
                else:
                    self._ser.write(frame)
                    self._ser.flush()
            except Exception as e:
                self._connected = False
                raise Iec101Error("SEND_FAILED", f"串口发送失败：{e}") from e
            self._last_tx = time.time()
        self._emit({
            "type": "frame", "direction": "TX",
            "hex": codec104.hex_dump(wire), "note": show_note, "ts": time.time(),
        })
        # [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装

    def _next_fcb(self) -> bool:
        self._fcb = not self._fcb
        return self._fcb

    def _send_fixed(self, ctrl: int, note: str) -> None:
        assert self._params
        self._send(link.build_fixed(ctrl, self._params.link_addr, self._params.addr_size), note)

    def _arm_ack_wait(self) -> None:
        """发送前调用：清确认标志。

        必须在发送之前清：从站响应可能极快（模拟器/高速链路），
        若在发送后才清，会把已经到达的确认丢掉，误判为“未确认”。
        """
        self._ack_ok = False
        self._ack_note = ""
        self._ack_event.clear()

    def _wait_ack(self, timeout: Optional[float] = None) -> bool:
        assert self._params
        ok = self._ack_event.wait(timeout or self._params.resp_timeout)
        return ok and self._ack_ok

    def _reply_fixed(self, fc: int, note: str, balanced_frame: bool = False) -> None:
        """主站对“从站主动发起”的固定帧的响应。

        balanced_frame=True 时按平衡格式回(DIR=1,PRM=0，如链路忙 0x8B)；
        否则按非平衡从站响应格式(0x0B)。从站帧带 DIR 位时按平衡格式回，
        使“未勾平衡但现场从站是平衡式”的情况也能通信。
        """
        assert self._params
        p = self._params
        ctrl = link.ctrl_balanced(fc, True, prm=False) if (p.balanced or balanced_frame) \
            else link.ctrl_secondary(fc)
        try:
            self._send_fixed(ctrl, note)
        except Iec101Error:
            pass

    def _reset_link(self, ack_timeout: Optional[float] = None) -> bool:
        """按现场(KW-2200)时序：请求链路状态(FC=9) → 复位远方链路(FC=0)。返回是否收到确认。"""
        assert self._params
        p = self._params
        t = float(ack_timeout or min(float(p.resp_timeout or 1.5), 1.5))
        # 1) 请求链路状态（现场第一帧就是 C9）
        ctrl = link.ctrl_balanced(link.FC_REQ_LINK_STATUS, True) if p.balanced \
            else link.ctrl_primary(link.FC_REQ_LINK_STATUS, False, False)
        self._arm_ack_wait()
        self._send_fixed(ctrl, "请求链路状态(FC=9)")
        ok1 = self._wait_ack(t)
        if not self._connected:
            return False
        # 2) 复位远方链路
        if p.balanced:
            ctrl = link.ctrl_balanced(link.FC_RESET_LINK, True)   # 0xC0
        else:
            ctrl = link.ctrl_primary(link.FC_RESET_LINK, False, False)   # 0x40
        self._arm_ack_wait()
        self._send_fixed(ctrl, "复位链路(FC=0)")
        ok2 = self._wait_ack(t)
        return bool(ok1 and ok2)

    def _init_link(self) -> None:
        """后台链路初始化：复位链路 → 请求链路状态（不阻塞界面与轮询）。"""
        assert self._params
        p = self._params
        # 链路层确认等待时间不宜过长：帧交互通常在百毫秒内完成
        ack_timeout = max(0.6, min(float(p.resp_timeout or 1.5), 1.5))
        try:
            ok = self._reset_link(ack_timeout)
        except Iec101Error:
            return
        if not self._connected:
            return
        if ok:
            self._log(f"101 链路初始化完成（从站已响应：{self._ack_note or '确认'}）")
            # 与 KW-2200 现场时序一致：链路建立后约 0.5s 即发总召唤
            time.sleep(0.5)
            # [AGENT_CHANGE_BEGIN] 2026-09-10 101可变帧校验和按用户数据求和
            # 优先标准校验和(与 KW-2200/规约分析工具一致)；兼容 ^0x80 作兜底
            combos = [(True, False), (True, False), (True, False), (True, True), (False, False)]
            # [AGENT_CHANGE_END] 2026-09-10 101可变帧校验和按用户数据求和
            for attempt, (use_dir, use_cs) in enumerate(combos, 1):
                if not self._connected:
                    return
                self._dir_override = use_dir
                self._cs_compat = use_cs
                before = self._last_rx
                try:
                    self.general_interrogation()
                except Iec101Error:
                    return
                tag = f"DIR={'带' if use_dir else '不带'}，校验和={'兼容' if use_cs else '标准'}"
                self._log(f"已发送总召唤（第 {attempt}/{len(combos)} 次，{tag}）；"
                          f"握手：从站主动帧 {self._peer_active_frames} 次，"
                          f"初始化结束={'已收到' if self._saw_init_end else '未收到'}")
                end = time.time() + 2.5
                got = False
                while self._connected and time.time() < end:
                    if self._last_rx > before:
                        got = True
                        break
                    time.sleep(0.05)
                if got:
                    self._log(f"从站已响应，已采用：{tag}")
                    return
            # 所有组合都无响应：恢复用户设置
            self._dir_override = None
            self._cs_compat = bool(getattr(p, "cs_compat", False))
            self._log("总召唤多次未得到从站响应：请核对从站方式(平衡/非平衡)、链路地址、波特率/校验")
        else:
            self._log("101 链路初始化：未收到从站确认（复位/链路状态已发出，继续监听）")

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
                before = self._last_rx
                if self._acd:
                    self._acd = False
                    self._request_level(1)
                elif p.poll_level2:
                    self._request_level(2)
                else:
                    continue
                # 非平衡：一次只发一帧，等从站应答（最多 min(轮询周期, 1s)）再发下一帧
                end = time.time() + max(0.2, min(1.0, float(p.poll_period)))
                while not self._stop.is_set() and time.time() < end:
                    if self._last_rx > before:
                        break
                    time.sleep(0.01)
            except Iec101Error:
                pass

    # [AGENT_CHANGE_BEGIN] 2026-09-10 设备参数周期
    def _timer_loop(self) -> None:
        """设备参数周期：总召唤 / 校时 / 心跳(测试链路 FC=2)。"""
        while not self._stop.is_set():
            time.sleep(0.5)
            if not self._connected or not self._params:
                continue
            p = self._params
            now = time.time()
            try:
                gi_sec = int(p.gi_period_min or 0) * 60
                if gi_sec and now - self._last_gi >= gi_sec:
                    self._last_gi = now
                    self.general_interrogation()
                clk_sec = int(p.clock_period_min or 0) * 60
                if clk_sec and now - self._last_clock >= clk_sec:
                    self._last_clock = now
                    self.clock_sync()
                hb = int(p.heartbeat_period or 0)
                if hb and now - self._last_heartbeat >= hb:
                    self._last_heartbeat = now
                    self.test_link()
            except Iec101Error:
                pass
            except Exception:
                pass

    def test_link(self) -> None:
        """心跳：固定帧测试链路 FC=2（平衡带 DIR：D2/F2 翻转）。"""
        self._ensure()
        assert self._params
        p = self._params
        use_dir = bool(p.data_frame_dir) if self._dir_override is None else bool(self._dir_override)
        fcb = self._next_fcb()
        if p.balanced and use_dir:
            ctrl = link.ctrl_balanced(link.FC_TEST_LINK, True, fcb, True)
        else:
            ctrl = link.ctrl_primary(link.FC_TEST_LINK, fcb, True)
        self._arm_ack_wait()
        self._send_fixed(ctrl, "测试链路(心跳 FC=2)")
        self._await_link_ack("测试链路(心跳)")
    # [AGENT_CHANGE_END] 2026-09-10 设备参数周期

    def _send_asdu(self, asdu: bytes, note: str = "", level: int = 1) -> None:
        """发送 ASDU：非平衡=用户数据(FC=3)等 ACK；平衡=用户数据(DIR=1)等确认。"""
        assert self._params
        p = self._params
        use_dir = bool(p.data_frame_dir) if self._dir_override is None else bool(self._dir_override)
        # [AGENT_CHANGE_BEGIN] 2026-09-10 忽略FCB位错误
        if p.ignore_fcb_error:
            fcb, fcv = False, False
        else:
            fcb, fcv = self._next_fcb(), True
        if p.balanced and use_dir:
            ctrl = link.ctrl_balanced(link.FC_USER_DATA, True, fcb, fcv)
            note = note or "用户数据(平衡)"
        else:
            # 现场抓包(KW-2200)：控制 0xF3/0xD3（PRM|FCB|FCV|FC=3）或 0x73/0x53(不带 DIR)
            ctrl = link.ctrl_primary(link.FC_USER_DATA, fcb, fcv)
            note = note or "用户数据(FC=3)"
        if p.ignore_fcb_error:
            note = f"{note}[忽略FCB]"
        # [AGENT_CHANGE_END] 2026-09-10 忽略FCB位错误
        frame = link.build_variable(ctrl, p.link_addr, asdu, p.addr_size, cs_compat=self._cs_compat)
        self._arm_ack_wait()
        self._send(frame, note)
        self._await_link_ack(note)

    def _await_link_ack(self, note: str) -> None:
        """后台等待链路层确认，超时只记日志（不阻塞界面线程）。"""
        assert self._params
        t = max(0.6, min(float(self._params.resp_timeout or 1.5), 1.5))

        def _wait() -> None:
            if self._connected and not self._wait_ack(t):
                self._log(f"{note}：未收到从站链路确认（继续等待数据帧）")

        threading.Thread(target=_wait, name="iec101-ack", daemon=True).start()

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
            frames = list(link.feed(self._buf, data, _as))
            if not frames and len(data) >= 5:
                # 终端回了字节但组不成有效帧（长度/结束字/校验异常）——提示出来便于定位
                self._log(f"收到 {len(data)} 字节但未组成有效帧：{codec104.hex_dump(data)}")
            for frame in frames:
                try:
                    self._handle_frame(frame)
                except Exception:
                    pass
        self._connected = False

    def _handle_frame(self, frame: bytes) -> None:
        info = link.parse_frame(frame, self._params.addr_size if self._params else 1)
        kind = info["kind"]
        cs_ok = bool(info.get("cs_ok", True))
        cs_note = "" if cs_ok else " ［校验和错误］"
        if kind == "single":
            # 单字符 E5：从站对“复位链路”的确认
            self._ack_ok = True
            self._ack_event.set()
            self._emit({"type": "frame", "direction": "RX", "hex": "E5",
                        "note": "单字符确认(E5)", "ts": time.time()})
            return
        if kind == "fixed":
            fc = info["fc"]
            acd = info.get("acd")
            if acd:
                self._acd = True
            if info["prm"]:
                # 从站主动发起：主站需要响应（现场：请求链路状态 -> 链路忙 0x8B；复位链路 -> 确认 0x80）
                self._peer_active_frames += 1
                names_p = {0: "复位链路", 1: "复位用户进程", 2: "测试链路", 9: "请求链路状态",
                           10: "召唤1级数据", 11: "召唤2级数据"}
                pname = names_p.get(fc, f"FC={fc}")
                is_bal_frame = bool(info.get("dir")) or bool(self._params and self._params.balanced)
                if fc == link.FC_REQ_LINK_STATUS:
                    self._reply_fixed(link.FC_LINK_BUSY, "链路状态(链路忙)", is_bal_frame)
                elif fc in (link.FC_RESET_LINK, link.FC_RESET_USER, link.FC_TEST_LINK):
                    self._fcb = False
                    self._reply_fixed(link.FC_ACK, "确认(ACK)", is_bal_frame)
                self._emit({"type": "frame", "direction": "RX", "hex": codec104.hex_dump(frame),
                            "note": f"从站发起:{pname}" + ("（ACD=1）" if acd else "") + cs_note,
                            "ts": time.time()})
                return
            if fc in (link.FC_ACK, link.FC_LINK_BUSY):
                # 链路忙也是有效响应（现场从站用 0x0B 回应请求链路状态）
                self._ack_ok = True
                self._ack_note = "链路忙" if fc == link.FC_LINK_BUSY else "确认"
                self._ack_event.set()
            elif fc in (link.FC_NACK, link.FC_NO_DATA):
                self._ack_ok = False
                self._ack_note = "否认" if fc == link.FC_NACK else "无数据"
                self._ack_event.set()
            names = {0: "确认(ACK)", 1: "否认(NACK)", 8: "用户数据", 9: "无所召唤数据", 11: "链路忙"}
            note = names.get(fc, f"固定帧 FC={fc}")
            self._emit({"type": "frame", "direction": "RX", "hex": codec104.hex_dump(frame),
                        "note": note + ("（ACD=1 请求访问）" if acd else "") + cs_note, "ts": time.time()})
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
            "hex": codec104.hex_dump(frame), "note": note + cs_note, "ts": time.time(),
            "asdu": None if not parsed else {
                "type_id": parsed.type_id, "type_name": parsed.type_name,
                "cot": parsed.cot, "ca": parsed.ca,
                "objects": [
                    {"ioa": o.ioa, "value": o.value, "quality": o.quality, "extra": o.extra}
                    for o in parsed.objects
                ],
            },
        })
        # 链路确认：非平衡收到数据帧回 ACK；平衡回确认(现场为 0x80=DIR|FC=0)
        try:
            if p and p.balanced:
                self._send_fixed(link.ctrl_balanced(link.FC_ACK, True, prm=False), "确认(ACK)")
            elif p:
                self._send_fixed(link.ctrl_secondary(link.FC_ACK), "确认(ACK)")
        except Iec101Error:
            pass
        if parsed is None:
            return
        if int(parsed.type_id) == 70:      # M_EI_NA_1 初始化结束
            self._saw_init_end = True
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
