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
    parity: str = "N"          # N / E / O（默认无校验）
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
        # [AGENT_CHANGE_BEGIN] 2026-09-12 平衡101对齐KW-2200延时获得
        self._link_ready_at = 0.0
        self._startup_post_gi_done = False
        self._startup_active = False   # 启动阶段：非平衡只召1级，不召2级
        # [AGENT_CHANGE_END] 2026-09-12 平衡101对齐KW-2200延时获得
        # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡启动停轮询防抢发
        self._suppress_poll = False    # True=暂停周期轮询（拉初始化结束/总召后延时获得期间）
        # [AGENT_CHANGE_END] 2026-09-12 非平衡启动停轮询防抢发
        self._acd = False              # 从站请求访问位
        # [AGENT_CHANGE_BEGIN] 2026-09-10 海南双主站AA封装
        self._hub: Optional[hainan_mux.HainanSerialHub] = None
        self._hub_port: str = ""
        # [AGENT_CHANGE_END] 2026-09-10 海南双主站AA封装
        # [AGENT_CHANGE_BEGIN] 2026-09-12 101链路应答超时重启
        self._link_restarting = False
        self._link_restart_lock = threading.Lock()
        self._last_link_restart_at = 0.0
        # 复位进程发出后：等应用层确认，窗口内禁止因链路ACK超时重启
        self._defer_link_restart_until = 0.0
        # [AGENT_CHANGE_END] 2026-09-12 101链路应答超时重启
        # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡等链路ACK再轮询
        self._expect_link_ack = False  # True=已发需确认帧，禁止抢发召1/2级
        # [AGENT_CHANGE_END] 2026-09-12 非平衡等链路ACK再轮询
        # [AGENT_CHANGE_BEGIN] 2026-09-12 链路未就绪禁召二级
        self._link_recovering = False  # True=链路恢复中：禁召1/2级与平衡心跳/周期总召校时
        # [AGENT_CHANGE_END] 2026-09-12 链路未就绪禁召二级
        # [AGENT_CHANGE_BEGIN] 2026-09-12 延时获得序贯同步等ACK
        self._post_gi_busy = False  # True=总召后106→校时序贯中，禁止心跳抢发
        self._ack_wait_gen = 0  # 递增以作废被下一帧抢清的异步等ACK，防误重启
        self._startup_seq_until = 0.0  # 启动总召至 COT=10/序贯结束前抑制心跳
        # [AGENT_CHANGE_END] 2026-09-12 延时获得序贯同步等ACK

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
        # [AGENT_CHANGE_BEGIN] 2026-09-12 平衡101对齐KW-2200延时获得
        self._link_ready_at = 0.0
        self._startup_post_gi_done = False
        self._startup_active = False
        # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡启动停轮询防抢发
        self._suppress_poll = False
        # [AGENT_CHANGE_END] 2026-09-12 非平衡启动停轮询防抢发
        # [AGENT_CHANGE_END] 2026-09-12 平衡101对齐KW-2200延时获得
        # [AGENT_CHANGE_BEGIN] 2026-09-12 101链路应答超时重启
        self._link_restarting = False
        self._last_link_restart_at = 0.0
        self._defer_link_restart_until = 0.0
        # [AGENT_CHANGE_END] 2026-09-12 101链路应答超时重启
        # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡等链路ACK再轮询
        self._expect_link_ack = False
        # [AGENT_CHANGE_END] 2026-09-12 非平衡等链路ACK再轮询
        # [AGENT_CHANGE_BEGIN] 2026-09-12 链路未就绪禁召二级
        # 平衡/非平衡/海南：连上后先恢复链路；成功前禁止召1/2级、心跳、周期总召/校时
        self._link_recovering = True
        # [AGENT_CHANGE_END] 2026-09-12 链路未就绪禁召二级
        # [AGENT_CHANGE_BEGIN] 2026-09-12 延时获得序贯同步等ACK
        self._post_gi_busy = False
        self._ack_wait_gen = 0
        self._startup_seq_until = 0.0
        # [AGENT_CHANGE_END] 2026-09-12 延时获得序贯同步等ACK
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
                    parity=str(p.parity or "N"),
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
                    parity=str(p.parity or "N"),
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
            # 轮询线程可先起；启动关键段用 _suppress_poll 门禁，避免与拉初始化结束抢发
            self._poll_thread = threading.Thread(target=self._poll_loop, name="iec101-poll", daemon=True)
            self._poll_thread.start()
            self._log(f"101 非平衡方式：周期轮询就绪（周期 {p.poll_period:g}s；启动完成前按需召1级）")
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
        # [AGENT_CHANGE_BEGIN] 2026-09-12 延时获得序贯同步等ACK
        # 新发送作废上一笔异步等ACK，避免被 clear 后误判超时重启链路
        self._ack_wait_gen = int(self._ack_wait_gen) + 1
        # [AGENT_CHANGE_END] 2026-09-12 延时获得序贯同步等ACK
        self._ack_ok = False
        self._ack_note = ""
        self._ack_event.clear()
        # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡等链路ACK再轮询
        self._expect_link_ack = True
        # [AGENT_CHANGE_END] 2026-09-12 非平衡等链路ACK再轮询

    def _wait_ack(self, timeout: Optional[float] = None, gen: Optional[int] = None) -> bool:
        assert self._params
        # [AGENT_CHANGE_BEGIN] 2026-09-12 延时获得序贯同步等ACK
        my_gen = self._ack_wait_gen if gen is None else int(gen)
        ok = self._ack_event.wait(timeout or self._params.resp_timeout)
        if my_gen != self._ack_wait_gen:
            return False  # 已被后续发送取代，调用方勿当成本帧超时去重启
        # [AGENT_CHANGE_END] 2026-09-12 延时获得序贯同步等ACK
        # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡等链路ACK再轮询
        self._expect_link_ack = False
        # [AGENT_CHANGE_END] 2026-09-12 非平衡等链路ACK再轮询
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

    # [AGENT_CHANGE_BEGIN] 2026-09-12 101链路应答超时重启
    def _link_ack_timeout_sec(self) -> float:
        """严格使用界面「链路应答超时」(映射为 resp_timeout)。"""
        assert self._params
        return max(0.1, float(self._params.resp_timeout or 10.0))

    def _reset_link(self, ack_timeout: Optional[float] = None) -> bool:
        """请求链路状态(FC=9) → 复位远方链路(FC=0)。

        任一环节未获确认则继续请求（失败绝不改去召1/2级/心跳/周期总召）；直到成功或断开。
        """
        assert self._params
        p = self._params
        t = float(ack_timeout) if ack_timeout is not None else self._link_ack_timeout_sec()
        # [AGENT_CHANGE_BEGIN] 2026-09-12 链路未就绪禁召二级
        while self._connected and not self._stop.is_set():
            # 1) 请求链路状态：失败则接着请求
            while self._connected and not self._stop.is_set():
                ctrl = link.ctrl_balanced(link.FC_REQ_LINK_STATUS, True) if p.balanced \
                    else link.ctrl_primary(link.FC_REQ_LINK_STATUS, False, False)
                self._arm_ack_wait()
                try:
                    self._send_fixed(ctrl, "请求链路状态(FC=9)")
                except Iec101Error as e:
                    self._log(f"请求链路状态发送失败：{e}，稍后重试")
                    time.sleep(0.5)
                    continue
                if self._wait_ack(t):
                    break
                self._log(f"请求链路状态失败（{t:g}s 无确认），继续请求…")
            if not self._connected or self._stop.is_set():
                return False
            # 2) 复位远方链路
            if p.balanced:
                ctrl = link.ctrl_balanced(link.FC_RESET_LINK, True)   # 0xC0
            else:
                ctrl = link.ctrl_primary(link.FC_RESET_LINK, False, False)   # 0x40
            self._arm_ack_wait()
            try:
                self._send_fixed(ctrl, "复位链路(FC=0)")
            except Iec101Error as e:
                self._log(f"复位链路发送失败：{e}，重新请求链路状态")
                continue
            if self._wait_ack(t):
                return True
            self._log(f"复位链路失败（{t:g}s 无确认），重新请求链路状态…")
        return False
        # [AGENT_CHANGE_END] 2026-09-12 链路未就绪禁召二级

    def _startup_after_link_ok(self) -> None:
        """链路复位确认后：等初始化结束 → 总召（后续由总召终止触发延时获得/校时）。"""
        assert self._params
        p = self._params
        # 平衡：等从站「初始化结束」再总召
        # 非平衡(KW)：复位确认常带 ACD=1 → 发一次1级拉「初始化结束」并等应答，禁止连发翻 FCB
        if p.balanced:
            wait_end = time.time() + 8.0
            while self._connected and not self._saw_init_end and time.time() < wait_end:
                time.sleep(0.05)
            if self._saw_init_end:
                self._log("已收到从站「初始化结束」，准备总召唤")
                time.sleep(0.4)
            else:
                self._log("等待「初始化结束」超时，仍尝试总召唤")
            combos = [(True, False), (True, False), (True, False), (True, True), (False, False)]
        else:
            self._startup_active = True
            self._suppress_poll = True
            try:
                # KW：复位后只发一次 7A，等初始化结束；不应答则有限次补召（每次等 RX）
                deadline = time.time() + 8.0
                for _ in range(3):
                    if not self._connected or self._saw_init_end or time.time() >= deadline:
                        break
                    self._acd = False
                    before = self._last_rx
                    try:
                        self._request_level(1)
                    except Iec101Error:
                        break
                    end = time.time() + 1.5
                    while self._connected and time.time() < end:
                        if self._saw_init_end or self._last_rx > before:
                            break
                        time.sleep(0.05)
                    if self._saw_init_end:
                        break
                    if not self._acd:
                        break  # 无 ACD 且非初始化结束：再发也无益
                wait_end = deadline
                while self._connected and not self._saw_init_end and time.time() < wait_end:
                    time.sleep(0.05)
                if self._saw_init_end:
                    self._log("已收到从站「初始化结束」(1级召唤)，准备总召唤")
                    time.sleep(0.4)
                else:
                    self._log("等待「初始化结束」超时，仍尝试总召唤")
            finally:
                # 总召过程需周期召1级拉数据（与 KW 一致），解除门禁
                self._suppress_poll = False
            combos = [(False, False), (False, False), (False, True)]
        for attempt, (use_dir, use_cs) in enumerate(combos, 1):
            if not self._connected:
                return
            self._dir_override = use_dir if p.balanced else False
            self._cs_compat = use_cs
            before = self._last_rx
            try:
                # [AGENT_CHANGE_BEGIN] 2026-09-12 延时获得序贯同步等ACK
                # 启动总召至 COT=10/106序贯结束前禁止心跳，避免总召过程中误重启
                self._startup_seq_until = time.time() + 120.0
                # [AGENT_CHANGE_END] 2026-09-12 延时获得序贯同步等ACK
                self.general_interrogation()
            except Iec101Error:
                return
            tag = f"DIR={'带' if (p.balanced and use_dir) else '不带'}，校验和={'兼容' if use_cs else '标准'}"
            self._log(f"已发送总召唤（第 {attempt}/{len(combos)} 次，{tag}）；"
                      f"握手：从站主动帧 {self._peer_active_frames} 次，"
                      f"初始化结束={'已收到' if self._saw_init_end else '未收到'}")
            end = time.time() + 2.5
            got = False
            while self._connected and time.time() < end:
                if self._last_rx > before:
                    got = True
                    break
                # 非平衡：总召后 ACK 常带 ACD，由 _poll_loop 召1级；此处不再连发
                time.sleep(0.05)
            if got:
                self._log(f"从站已响应，已采用：{tag}")
                return
        # 所有组合都无响应：恢复用户设置
        self._dir_override = None
        self._cs_compat = bool(getattr(p, "cs_compat", False))
        self._log("总召唤多次未得到从站响应：请核对从站方式(平衡/非平衡)、链路地址、波特率/校验")

    def _restart_link_on_ack_timeout(self, cause: str) -> None:
        """链路层确认超时：复位链路；成功后自动再总召（总召终止后仍走延时获得/校时）。"""
        if not self._connected or not self._params:
            return
        # [AGENT_CHANGE_BEGIN] 2026-09-12 复位进程等确认后再重启
        if time.time() < float(self._defer_link_restart_until or 0.0):
            self._log(f"{cause}：复位进程等待从站确认中，暂不重启链路")
            return
        # [AGENT_CHANGE_END] 2026-09-12 复位进程等确认后再重启
        t = self._link_ack_timeout_sec()
        with self._link_restart_lock:
            if self._link_restarting or not self._connected:
                return
            now = time.time()
            cool = max(5.0, t)
            if now - self._last_link_restart_at < cool:
                self._log(f"{cause}：链路应答超时（{t:g}s），距上次重启未满 {cool:g}s，跳过")
                return
            self._link_restarting = True
            self._last_link_restart_at = now
        # [AGENT_CHANGE_BEGIN] 2026-09-12 链路未就绪禁召二级
        self._link_recovering = True
        try:
            self._log(
                f"{cause}：链路应答超时（{t:g}s），重启链路"
                f"（请求状态→复位；失败则继续请求，不发召1/2级/心跳）"
            )
            self._fcb = False
            self._saw_init_end = False
            self._startup_post_gi_done = False
            try:
                ok = self._reset_link(t)
            except Iec101Error as e:
                self._log(f"链路重启失败：{e}")
                return
            if not self._connected:
                return
            if ok:
                self._link_ready_at = time.time()
                self._link_recovering = False
                self._log(f"链路重启成功（{self._ack_note or '确认'}），重新总召/校时")
                self._startup_after_link_ok()
            else:
                self._log("链路重启结束：连接已断开（恢复中未发召1/2级/心跳）")
        finally:
            self._link_restarting = False
            if not self._connected:
                self._link_recovering = False
        # [AGENT_CHANGE_END] 2026-09-12 链路未就绪禁召二级

    def _init_link(self) -> None:
        """后台链路初始化：请求链路状态 → 复位远方链路（对齐 KW-2200）。"""
        assert self._params
        ack_timeout = self._link_ack_timeout_sec()
        # [AGENT_CHANGE_BEGIN] 2026-09-12 链路未就绪禁召二级
        self._link_recovering = True
        try:
            ok = self._reset_link(ack_timeout)
        except Iec101Error:
            return
        if not self._connected:
            return
        if ok:
            self._link_ready_at = time.time()
            self._link_recovering = False
            self._log(f"101 链路初始化完成（从站已响应：{self._ack_note or '确认'}）")
            self._startup_after_link_ok()
        else:
            self._log("101 链路初始化结束：连接已断开（未获确认期间未发召1/2级/心跳）")
        # [AGENT_CHANGE_END] 2026-09-12 链路未就绪禁召二级
    # [AGENT_CHANGE_END] 2026-09-12 101链路应答超时重启

    def _request_level(self, level: int) -> None:
        """非平衡：召唤 1/2 级数据（KW-2200：FCV=1 且 FCB 翻转 → 7A/5A、7B/5B）。"""
        assert self._params
        fc = link.FC_REQ_LEVEL1 if level == 1 else link.FC_REQ_LEVEL2
        # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡101对齐KW-2200启动
        fcb = self._next_fcb()
        ctrl = link.ctrl_primary(fc, fcb, True)
        # [AGENT_CHANGE_END] 2026-09-12 非平衡101对齐KW-2200启动
        self._send_fixed(ctrl, f"召唤{level}级数据(FC={fc})")

    def _poll_loop(self) -> None:
        assert self._params
        p = self._params
        while not self._stop.is_set():
            time.sleep(max(0.05, p.poll_period))
            if not self._connected:
                continue
            # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡启动停轮询防抢发
            if self._suppress_poll:
                continue
            # [AGENT_CHANGE_END] 2026-09-12 非平衡启动停轮询防抢发
            # [AGENT_CHANGE_BEGIN] 2026-09-12 链路未就绪禁召二级
            # 请求链路状态/复位未成功前，禁止召1/2级（失败只继续请求状态）
            if self._link_recovering or self._link_restarting:
                continue
            # [AGENT_CHANGE_END] 2026-09-12 链路未就绪禁召二级
            # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡等链路ACK再轮询
            # 总召等用户数据发出后须等固定帧 ACK，再召1级；否则半双工易撞出 14 C9 FC 类垃圾
            if self._expect_link_ack:
                continue
            # [AGENT_CHANGE_END] 2026-09-12 非平衡等链路ACK再轮询
            try:
                before = self._last_rx
                # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡101对齐KW-2200启动
                if self._acd or self._startup_active:
                    self._acd = False
                    self._request_level(1)
                elif p.poll_level2 and not self._startup_active:
                    self._request_level(2)
                else:
                    continue
                # [AGENT_CHANGE_END] 2026-09-12 非平衡101对齐KW-2200启动
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
            # [AGENT_CHANGE_BEGIN] 2026-09-12 链路未就绪禁召二级
            # 平衡：链路未就绪时禁止周期总召/校时/心跳（失败只继续请求链路状态）
            if self._link_recovering or self._link_restarting:
                continue
            # [AGENT_CHANGE_END] 2026-09-12 链路未就绪禁召二级
            # [AGENT_CHANGE_BEGIN] 2026-09-12 延时获得序贯同步等ACK
            if (
                self._post_gi_busy
                or self._expect_link_ack
                or time.time() < float(self._startup_seq_until or 0.0)
            ):
                continue
            # [AGENT_CHANGE_END] 2026-09-12 延时获得序贯同步等ACK
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
                # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡101不发心跳
                # 心跳(测试链路 FC=2)为平衡链路保活；非平衡靠召 1/2 级即可，对齐 KW-2200
                if p.balanced:
                    hb = int(p.heartbeat_period or 0)
                    if hb and now - self._last_heartbeat >= hb:
                        self._last_heartbeat = now
                        self.test_link()
                # [AGENT_CHANGE_END] 2026-09-12 非平衡101不发心跳
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

    def _send_asdu(
        self,
        asdu: bytes,
        note: str = "",
        level: int = 1,
        restart_on_ack_timeout: bool = True,
        wait_ack: bool = False,
    ) -> bool:
        """发送 ASDU：非平衡=用户数据(FC=3)等 ACK；平衡=用户数据(DIR=1)等确认。

        wait_ack=True：同步等链路确认（用于总召后 106→校时序贯，避免异步等 ACK 被下一帧抢清）。
        """
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
        # [AGENT_CHANGE_BEGIN] 2026-09-12 延时获得序贯同步等ACK
        if wait_ack:
            gen = self._ack_wait_gen
            ok = self._wait_ack(self._link_ack_timeout_sec(), gen=gen)
            if not ok and gen == self._ack_wait_gen:
                self._log(f"{note}：未收到从站链路确认")
            return bool(ok) and gen == self._ack_wait_gen
        gen = self._ack_wait_gen
        self._await_link_ack(note, restart_on_timeout=restart_on_ack_timeout, gen=gen)
        return True
        # [AGENT_CHANGE_END] 2026-09-12 延时获得序贯同步等ACK

    def _await_link_ack(
        self, note: str, restart_on_timeout: bool = True, gen: Optional[int] = None
    ) -> None:
        """后台等待链路层确认；超时按界面「链路应答超时」重启链路（不阻塞界面线程）。"""
        assert self._params
        # [AGENT_CHANGE_BEGIN] 2026-09-12 101链路应答超时重启
        t = self._link_ack_timeout_sec()
        my_gen = self._ack_wait_gen if gen is None else int(gen)

        def _wait() -> None:
            if not self._connected:
                return
            ok = self._wait_ack(t, gen=my_gen)
            # [AGENT_CHANGE_BEGIN] 2026-09-12 延时获得序贯同步等ACK
            if my_gen != self._ack_wait_gen:
                return  # 已被后续帧取代，不作废重启
            # [AGENT_CHANGE_END] 2026-09-12 延时获得序贯同步等ACK
            if ok:
                return
            if not restart_on_timeout:
                self._log(f"{note}：链路应答超时（{t:g}s，本命令不因链路ACK重启）")
                return
            # 复位/重启序贯进行中：只记日志，避免嵌套重启
            if self._link_restarting or self._post_gi_busy:
                self._log(f"{note}：链路应答超时（{t:g}s，序贯/重启进行中，不重复重启）")
                return
            self._restart_link_on_ack_timeout(note)

        # [AGENT_CHANGE_END] 2026-09-12 101链路应答超时重启
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
            _as = self._params.addr_size if self._params else 1
            frames = list(link.feed(self._buf, data, _as))
            # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡等链路ACK再轮询
            # 仅完整帧或半帧等待才刷新 last_rx；纯同步搜索丢弃的噪声不算“从站已响应”
            if frames or self._ft12_waiting_more(self._buf, _as):
                self._last_rx = time.time()
            # [AGENT_CHANGE_END] 2026-09-12 非平衡等链路ACK再轮询
            # [AGENT_CHANGE_BEGIN] 2026-09-12 修正半帧误报未组帧
            # 大 ASDU（如总召双点 L=0x6F≈117B、遥测 L=0xD3≈217B）常被串口拆成多包。
            # feed 缓冲半帧时返回空属正常，不得把“本批 chunk”误报成组帧失败。
            # 另：无 10/68/E5 的短噪声（如半双工冲突残片 14 C9 FC）只丢弃，不刷屏告警。
            if not frames and data and not self._ft12_waiting_more(self._buf, _as):
                has_start = any(b in (link.START_FIXED, link.START_VAR, link.SINGLE_CHAR) for b in data)
                if has_start or len(data) > 8:
                    self._log(
                        f"收到 {len(data)} 字节无法组帧（缓冲 {len(self._buf)}B，非半帧等待）："
                        f"{codec104.hex_dump(data[:64])}"
                        + (" …" if len(data) > 64 else "")
                    )
            # [AGENT_CHANGE_END] 2026-09-12 修正半帧误报未组帧
            for frame in frames:
                try:
                    self._handle_frame(frame)
                except Exception:
                    pass
        self._connected = False

    def _ft12_waiting_more(self, buf: bytearray, addr_size: int) -> bool:
        """缓冲里已是合法 FT1.2 帧头、只是长度未到齐 → 半帧等待中。"""
        if not buf:
            return False
        b0 = buf[0]
        if b0 == link.SINGLE_CHAR:
            return False
        if b0 == link.START_FIXED:
            return len(buf) < (4 + int(addr_size))
        if b0 == link.START_VAR:
            if len(buf) < 2:
                return True
            length = int(buf[1])
            return len(buf) < (4 + length + 2)
        return False

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
                # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡等链路ACK再轮询
                self._expect_link_ack = False
                # [AGENT_CHANGE_END] 2026-09-12 非平衡等链路ACK再轮询
                self._ack_event.set()
            elif fc in (link.FC_NACK, link.FC_NO_DATA):
                self._ack_ok = False
                self._ack_note = "否认" if fc == link.FC_NACK else "无数据"
                # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡等链路ACK再轮询
                self._expect_link_ack = False
                # [AGENT_CHANGE_END] 2026-09-12 非平衡等链路ACK再轮询
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
                # [AGENT_CHANGE_BEGIN] 2026-09-12 监视摘要只到公共地址
                # [AGENT_CHANGE_END] 2026-09-12 监视摘要只到公共地址
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
        # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡101对齐KW-2200启动
        # 可变帧控制域 ACD：继续拉1级；平衡才回固定 ACK，非平衡靠召1/2级拉数(KW 无次站ACK)
        if info.get("acd"):
            self._acd = True
        try:
            if p and p.balanced:
                self._send_fixed(link.ctrl_balanced(link.FC_ACK, True, prm=False), "确认(ACK)")
        except Iec101Error:
            pass
        # [AGENT_CHANGE_END] 2026-09-12 非平衡101对齐KW-2200启动
        if parsed is None:
            return
        if int(parsed.type_id) == 70:      # M_EI_NA_1 初始化结束
            self._saw_init_end = True
        # [AGENT_CHANGE_BEGIN] 2026-09-12 平衡101对齐KW-2200延时获得
        # KW-2200：总召激活终止后 → 延时获得(激活) → 延时获得(突发) → 时钟同步
        # 非平衡同样需要（抓包：总召终止后 6A 激活/突发再校时）
        if (
            p
            and int(parsed.type_id) == 100
            and int(parsed.cot) == 10
            and not self._startup_post_gi_done
        ):
            self._startup_post_gi_done = True
            # [AGENT_CHANGE_BEGIN] 2026-09-12 延时获得序贯同步等ACK
            self._startup_seq_until = 0.0  # 改由 _post_gi_busy 门禁
            # [AGENT_CHANGE_END] 2026-09-12 延时获得序贯同步等ACK
            threading.Thread(
                target=self._post_gi_startup, name="iec101-post-gi", daemon=True
            ).start()
        # [AGENT_CHANGE_END] 2026-09-12 平衡101对齐KW-2200延时获得
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

    # [AGENT_CHANGE_BEGIN] 2026-09-12 平衡101对齐KW-2200延时获得
    def delay_acquisition(self, delay_ms: int, cot: int = 6, wait_ack: bool = False) -> bool:
        """C_CD_NA_1(106) 延时获得（KW-2200 总召后、校时前）。"""
        self._ensure()
        assert self._params
        p = self._params
        asdu = codec104.build_delay_acquisition(
            p.common_address,
            int(delay_ms),
            oa=p.originator,
            cot=int(cot),
            cot_size=p.cot_size,
            ca_size=p.ca_size,
            ioa_size=p.ioa_size,
        )
        cot_name = {6: "激活", 3: "突发", 7: "激活确认"}.get(int(cot), str(cot))
        return self._send_asdu(
            asdu, note=f"延时获得({cot_name}) {int(delay_ms)}ms", wait_ack=wait_ack
        )

    def _post_gi_startup(self) -> None:
        """对齐 KW-2200：延时获得(COT=6) → 等确认并回 ACK → 延时获得(COT=3) → 时钟同步。

        每帧同步等链路 ACK，禁止异步等 ACK 被下一帧 _arm_ack_wait 清掉而误重启链路。
        """
        if not self._connected or not self._params:
            return
        # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡启动停轮询防抢发
        was_suppress = self._suppress_poll
        self._suppress_poll = True
        # [AGENT_CHANGE_END] 2026-09-12 非平衡启动停轮询防抢发
        # [AGENT_CHANGE_BEGIN] 2026-09-12 延时获得序贯同步等ACK
        self._post_gi_busy = True
        t_ack = self._link_ack_timeout_sec()
        try:
            ready = self._link_ready_at or time.time()
            delay_act = max(1, int((time.time() - ready) * 1000)) & 0xFFFF
            before = self.i_frame_count()
            if not self.delay_acquisition(delay_act, cot=6, wait_ack=True):
                self._log("延时获得(激活)无链路确认，中止后续突发/校时")
                return
            if not self._connected:
                return
            # 非平衡：延时获得确认在1级用户数据里，需再召1级（KW: 5A）
            if not self._params.balanced:
                self._acd = False
                before_rx = self._last_rx
                try:
                    self._request_level(1)
                except Iec101Error:
                    pass
                end = time.time() + 1.5
                while self._connected and time.time() < end:
                    if self._last_rx > before_rx:
                        break
                    time.sleep(0.05)
                if not self._connected:
                    return
            # 等应用层激活确认(COT=7)；平衡下 RX 线程会先回固定 ACK（对齐 KW）
            conf = self.wait_cmd_result({106}, {7}, before, t_ack)
            if conf:
                time.sleep(0.35)  # 给平衡 ACK(0x80) 发出的时间，再发突发
            else:
                self._log(f"延时获得：{t_ack:g}s 内未收到激活确认，仍尝试突发/校时")
            if not self._connected:
                return
            delay_spont = 355 if self._params.balanced else 0x0038  # KW 非平衡突发帧 38 00
            if not self.delay_acquisition(delay_spont, cot=3, wait_ack=True):
                self._log("延时获得(突发)无链路确认，中止校时")
                return
            if not self._connected:
                return
            time.sleep(0.35)
            # 校时也同步等 ACK，避免与序贯后续抢事件
            p = self._params
            asdu = codec104.build_clock_sync(
                p.common_address, oa=p.originator,
                cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size,
            )
            if not self._send_asdu(asdu, note="时钟同步", wait_ack=True):
                self._log("时钟同步无链路确认")
            self._startup_active = False
            self._log("链路初始化：总召唤后已发延时获得并时钟同步")
        except Iec101Error as e:
            self._startup_active = False
            self._log(f"总召后延时获得/校时失败：{e}")
        finally:
            self._post_gi_busy = False
            self._startup_seq_until = 0.0
            # [AGENT_CHANGE_BEGIN] 2026-09-12 非平衡启动停轮询防抢发
            self._suppress_poll = False if self._connected else was_suppress
            self._startup_active = False
            # [AGENT_CHANGE_END] 2026-09-12 非平衡启动停轮询防抢发
        # [AGENT_CHANGE_END] 2026-09-12 延时获得序贯同步等ACK
    # [AGENT_CHANGE_END] 2026-09-12 平衡101对齐KW-2200延时获得

    def clock_sync(self) -> None:
        self._ensure()
        assert self._params
        p = self._params
        asdu = codec104.build_clock_sync(p.common_address, oa=p.originator,
                                         cot_size=p.cot_size, ca_size=p.ca_size, ioa_size=p.ioa_size)
        self._send_asdu(asdu, note="时钟同步")

    # [AGENT_CHANGE_BEGIN] 2026-09-12 复位进程命令
    def reset_process(self, qrp: int = 1) -> None:
        """C_RP_NA_1(105) 复位进程，默认 QRP=1 总复位。

        发出后不因链路 ACK 超时立即重启；先等从站应用层确认(COT=7)。
        - 无确认：按「链路应答超时」重启链路
        - 有确认：从站进程复位后常会失联，再按同一超时监视静默，无接收则复位链路并重新总召
        """
        self._ensure()
        assert self._params
        p = self._params
        asdu = codec104.build_reset_process(
            p.common_address,
            qrp=int(qrp),
            oa=p.originator,
            cot_size=p.cot_size,
            ca_size=p.ca_size,
            ioa_size=p.ioa_size,
        )
        # [AGENT_CHANGE_BEGIN] 2026-09-12 复位进程等确认后再重启
        t = self._link_ack_timeout_sec()
        before = self.i_frame_count()
        self._defer_link_restart_until = time.time() + t
        self._send_asdu(
            asdu,
            note=f"复位进程(QRP={int(qrp)})",
            restart_on_ack_timeout=False,
        )

        def _watch_confirm() -> None:
            try:
                r = self.wait_cmd_result({105}, {7}, before, t)
                if not self._connected:
                    return
                if not r:
                    self._log(f"复位进程：{t:g}s 内未收到从站确认，重启链路")
                    self._defer_link_restart_until = 0.0
                    self._restart_link_on_ack_timeout("复位进程(无应用确认)")
                    return
                self._log(
                    f"复位进程：已收到从站确认(COT={r.get('cot')}"
                    f"{'' if r.get('pos', True) else ' 否定'})，"
                    f"继续监视；{t:g}s 无接收则复位链路"
                )
                # 确认后允许重启；按 last_rx 静默超时再复位（进程复位后失联）
                self._defer_link_restart_until = 0.0
                self._watch_silence_then_restart(t, "复位进程后无应答")
            finally:
                self._defer_link_restart_until = 0.0

        threading.Thread(target=_watch_confirm, name="iec101-rp-wait", daemon=True).start()
        # [AGENT_CHANGE_END] 2026-09-12 复位进程等确认后再重启

    def _watch_silence_then_restart(self, silence_sec: float, cause: str) -> None:
        """监视接收静默：超过 silence_sec 无任何已组帧/半帧接收则重启链路。"""
        silence_sec = max(0.5, float(silence_sec))
        # 以「当前时刻」与 last_rx 较晚者为锚，避免确认帧本身立刻触发
        while self._connected and not self._stop.is_set():
            idle = time.time() - float(self._last_rx or 0.0)
            if idle >= silence_sec:
                self._log(f"{cause}：已 {idle:.0f}s 无接收（门限 {silence_sec:g}s），重启链路")
                self._restart_link_on_ack_timeout(cause)
                return
            time.sleep(0.2)
    # [AGENT_CHANGE_END] 2026-09-12 复位进程命令

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
