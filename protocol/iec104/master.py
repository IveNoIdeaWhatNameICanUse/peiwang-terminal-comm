# [AGENT_CHANGE_BEGIN] 2026-09-07 104-MVP主站
"""IEC 104 模拟主站（支持本地 IP/端口绑定、快速重连、多会话）。"""
from __future__ import annotations

import errno
import socket
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from . import codec
from .const import DEFAULT_PORT, START_BYTE, cot_name


EventCallback = Callable[[dict], None]

# 监视方向（数据类）类型：只有这些类型的报文才更新实时数据点表
# （55/108/202/203 为配网扩展参数/定值，从站确认或回读时更新点值）
MONITOR_TYPES = {1, 3, 5, 7, 9, 11, 13, 15, 30, 31, 36, 55, 108, 202, 203}


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
    connect_timeout_s: float = 5.0  # 兼容旧字段
    t0: float = 30.0                # 连接建立超时(秒)
    t1: float = 15.0
    t2: float = 10.0
    t3: float = 20.0
    k: int = 12
    w: int = 8
    clock_period: int = 10          # 校时周期(分)，0=禁用
    gi_period: int = 900            # 总召唤周期(秒)=设备参数分钟*60，0=禁用
    call_period: int = 0            # 召唤度周期(秒)，0=禁用
    link_ack_timeout: float = 10.0  # 链路应答超时(秒)
    cmd_timeout: float = 30.0       # 远控命令超时(秒)
    cot_size: int = 2               # 传送原因长度
    ca_size: int = 2                # 公共地址长度
    ioa_size: int = 3               # 信息体地址长度
    read_cot: int = 6               # 读命令(定值召唤)传送原因：5=请求(标准) 6=激活
    auto_reconnect: bool = True     # 超时断线重连
    tx_delay_ms: float = 0.0        # 发送延时(毫秒)


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
        self._i_frames = 0
        self._i_msgs: list = []   # [(序号, type_id, cot)] 最近收到的信息帧摘要
        self.session_id = ""
        # 连接后自动初始化：STARTDT→总召唤→(数据完毕)→时钟同步
        self.auto_init = True
        self._gi_sent_at = 0.0
        self._clock_sent = False
        # 104 参数运行状态
        self._timer_thread: Optional[threading.Thread] = None
        self._last_cmd_sent = 0.0
        self._cmd_timeout_notified = False
        self._startdt_sent_at = 0.0
        self._startdt_ok = False
        self._startdt_timeout_notified = False
        self._last_gi = 0.0
        self._last_clock = 0.0
        self._last_i_rx = 0.0           # 最近收到 I 帧时间（T2 定时确认用）
        self._testfr_waiting = False       # 已发 TESTFR，等待从站确认
        self._testfr_sent_at = 0.0
        self._last_call = 0.0
        # [AGENT_CHANGE_BEGIN] 2026-09-11 104静默无限重连
        self._reconnect_thread: Optional[threading.Thread] = None
        # [AGENT_CHANGE_END] 2026-09-11 104静默无限重连

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self, params: ConnectParams) -> None:
        # [AGENT_CHANGE_BEGIN] 2026-09-07 修复固定本地端口重连10048
        self.disconnect()

        local_ip = (params.local_ip or "").strip()
        local_port = int(params.local_port or 0)
        if local_port < 0 or local_port > 65535:
            raise MasterError("INVALID_LOCAL_PORT", f"本地端口无效: {local_port}")

        last_err: Optional[MasterError] = None
        for attempt in range(6):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.settimeout(params.t0 or params.connect_timeout_s)

                if local_ip or local_port:
                    bind_ip = local_ip or "0.0.0.0"
                    try:
                        sock.bind((bind_ip, local_port))
                    except OSError as e:
                        sock.close()
                        err = self._classify_bind_error(bind_ip, local_port, e)
                        if err.code == "LOCAL_PORT_IN_USE" and attempt < 5:
                            last_err = err
                            time.sleep(0.15 * (attempt + 1))
                            continue
                        raise err from e

                try:
                    sock.connect((params.remote_ip, int(params.remote_port)))
                except socket.timeout as e:
                    sock.close()
                    raise MasterError(
                        "CONNECT_TIMEOUT",
                        f"连接超时：无法在 {params.connect_timeout_s:.0f}s 内连通 "
                        f"{params.remote_ip}:{params.remote_port}"
                        + (
                            f"（源 {local_ip or '任意'}:{local_port or '自动'}）"
                            if (local_ip or local_port)
                            else ""
                        ),
                    ) from e
                except ConnectionRefusedError as e:
                    sock.close()
                    raise MasterError(
                        "CONNECT_REFUSED",
                        f"对端拒绝连接：{params.remote_ip}:{params.remote_port}（请确认从站已监听）",
                    ) from e
                except OSError as e:
                    sock.close()
                    en = getattr(e, "winerror", None) or e.errno
                    if en in (errno.EADDRINUSE, 10048):
                        err = MasterError(
                            "LOCAL_PORT_IN_USE",
                            f"本地端口仍被占用（多为上次连接 TIME_WAIT）："
                            f"{local_ip or '0.0.0.0'}:{local_port or '自动'}。"
                            f"正在重试({attempt + 1}/6)…",
                        )
                        if attempt < 5:
                            last_err = err
                            time.sleep(0.2 * (attempt + 1))
                            continue
                        raise err from e
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
            self._gi_sent_at = 0.0
            self._clock_sent = False
            # 104 参数定时/超时状态复位
            now = time.time()
            self._last_cmd_sent = now
            self._cmd_timeout_notified = False
            self._startdt_sent_at = 0.0
            self._startdt_ok = False
            self._startdt_timeout_notified = False
            self._last_gi = now
            self._last_clock = now
            self._last_call = now
            self._last_i_rx = now
            self._testfr_waiting = False
            self._testfr_sent_at = 0.0

            bound = sock.getsockname()
            self._emit(
                {
                    "type": "connection",
                    "state": "connected",
                    "session_id": self.session_id,
                    "local": f"{bound[0]}:{bound[1]}",
                    "remote": f"{params.remote_ip}:{params.remote_port}",
                    "message": f"已连接（本地 {bound[0]}:{bound[1]} → {params.remote_ip}:{params.remote_port}）",
                }
            )

            self._rx_thread = threading.Thread(target=self._rx_loop, name="iec104-rx", daemon=True)
            self._rx_thread.start()
            self._log("链路初始化开始：TCP 已连接，发送「启动数据传输」激活(act)")
            self._send_raw(codec.build_u_frame(start_dt=True), direction="TX", note="启动数据传输(act)")
            self._startdt_sent_at = time.time()
            if not self._timer_thread or not self._timer_thread.is_alive():
                self._timer_thread = threading.Thread(target=self._timer_loop, name="iec104-timer", daemon=True)
                self._timer_thread.start()
            return

        if last_err:
            raise last_err
        raise MasterError("LOCAL_PORT_IN_USE", "本地端口占用，重试仍失败，请更换本地端口或稍后再连")
        # [AGENT_CHANGE_END] 2026-09-07 修复固定本地端口重连10048

    def disconnect(self) -> None:
        # [AGENT_CHANGE_BEGIN] 2026-09-07 修复固定本地端口重连10048
        was_connected = self._connected
        self._stop.set()
        self._connected = False
        sock = self._sock
        self._sock = None
        if sock:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            except OSError:
                pass
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
        if was_connected:
            self._emit(
                {
                    "type": "connection",
                    "state": "disconnected",
                    "session_id": self.session_id,
                    "message": "已断开",
                }
            )
        # [AGENT_CHANGE_END] 2026-09-07 修复固定本地端口重连10048

    def general_interrogation(self) -> None:
        self._ensure()
        assert self._params
        asdu = codec.build_interrogation(
            self._params.common_address,
            oa=self._params.originator,
            cot_size=self._params.cot_size,
            ca_size=self._params.ca_size,
            ioa_size=self._params.ioa_size,
        )
        self._send_i(asdu, note="总召唤")

    def clock_sync(self) -> None:
        self._ensure()
        assert self._params
        asdu = codec.build_clock_sync(
            self._params.common_address,
            datetime.now(),
            oa=self._params.originator,
            cot_size=self._params.cot_size,
            ca_size=self._params.ca_size,
            ioa_size=self._params.ioa_size,
        )
        self._send_i(asdu, note="时钟同步")

    def single_command(self, ioa: int, on: bool, select: bool, cancel: bool = False) -> None:
        self._ensure()
        assert self._params
        cot = 8 if cancel else 6
        asdu = codec.build_single_command(
            self._params.common_address, ioa, on, select, oa=self._params.originator,
            cot=cot, cot_size=self._params.cot_size, ca_size=self._params.ca_size, ioa_size=self._params.ioa_size,
        )
        action = "撤销" if cancel else ("预置" if select else "执行")
        self._send_i(asdu, note=f"单点遥控 IOA={ioa} {action} {'合' if on else '分'}")

    def double_command(self, ioa: int, state: int, select: bool, cancel: bool = False) -> None:
        self._ensure()
        assert self._params
        cot = 8 if cancel else 6
        asdu = codec.build_double_command(
            self._params.common_address, ioa, state, select, oa=self._params.originator,
            cot=cot, cot_size=self._params.cot_size, ca_size=self._params.ca_size, ioa_size=self._params.ioa_size,
        )
        action = "撤销" if cancel else ("预置" if select else "执行")
        self._send_i(asdu, note=f"双点遥控 IOA={ioa} {action} state={state}")

    def setpoint_float(self, ioa: int, value: float, select: bool, cancel: bool = False) -> None:
        self._ensure()
        assert self._params
        cot = 8 if cancel else 6
        asdu = codec.build_setpoint_float(
            self._params.common_address, ioa, value, select, oa=self._params.originator,
            cot=cot, cot_size=self._params.cot_size, ca_size=self._params.ca_size, ioa_size=self._params.ioa_size,
        )
        action = "撤销" if cancel else ("预置" if select else "执行")
        self._send_i(asdu, note=f"浮点设点 IOA={ioa} {action} value={value}")

    def setpoint_normalized(self, ioa: int, value: float, select: bool, cancel: bool = False) -> None:
        self._ensure()
        assert self._params
        cot = 8 if cancel else 6
        asdu = codec.build_setpoint_normalized(
            self._params.common_address, ioa, value, select, oa=self._params.originator,
            cot=cot, cot_size=self._params.cot_size, ca_size=self._params.ca_size, ioa_size=self._params.ioa_size,
        )
        action = "撤销" if cancel else ("预置" if select else "执行")
        self._send_i(asdu, note=f"归一化设点 IOA={ioa} {action} value={value}")

    def read_point(self, ioa: int, cot: Optional[int] = None) -> None:
        """标准读命令 C_RD_NA_1(102)。"""
        self._ensure()
        assert self._params
        if cot is None:
            cot = getattr(self._params, "read_cot", 6) or 6
        asdu = codec.build_read_command(
            self._params.common_address, ioa, oa=self._params.originator, cot=cot,
            cot_size=self._params.cot_size, ca_size=self._params.ca_size, ioa_size=self._params.ioa_size,
        )
        self._send_i(asdu, note=f"读命令(102) IOA={ioa}")

    def read_setpoint(self, ioa: int, tid: int = 108, area: int = 1) -> None:
        """参数/定值召唤：108(南网/广西，IOA+短浮点) 或 202(国网，区号(2B)+IOA(3B))，COT=6(激活)。"""
        self._ensure()
        assert self._params
        asdu = codec.build_read_param(
            self._params.common_address, ioa,
            oa=self._params.originator, cot=6, tid=tid, area=area,
            cot_size=self._params.cot_size, ca_size=self._params.ca_size, ioa_size=self._params.ioa_size,
        )
        suffix = f" 区号={area}" if tid == 202 else ""
        self._send_i(asdu, note=f"定值召唤(读参数 {tid}) IOA={ioa}{suffix}")

    def read_setpoints_batch(self, ioas: list, tid: int = 108, area: int = 1) -> None:
        """批量定值召唤：一帧携带多个对象（SQ=0），108/202 结构同单点。"""
        self._ensure()
        assert self._params
        ioas = [int(i) for i in ioas][:127]
        if not ioas:
            return
        asdu = codec.build_read_param_batch(
            self._params.common_address, ioas, tid=tid, area=area,
            oa=self._params.originator, cot=6,
            cot_size=self._params.cot_size, ca_size=self._params.ca_size, ioa_size=self._params.ioa_size,
        )
        suffix = f" 区号={area}" if tid == 202 else ""
        first, last = ioas[0], ioas[-1]
        self._send_i(asdu, note=f"定值召唤(读参数 {tid}) IOA={first}..{last} 共{len(ioas)}个{suffix}")

    def preset_param(self, ioa: int, value: float, select: bool = True, cancel: bool = False,
                     tid: int = 55, area: int = 1) -> None:
        """参数预置/激活/撤销：
        - 55(南网/广西)：IOA+短浮点+S/E(0x80预置/0x00激活)，预置/激活 COT=6，撤销 COT=8
        - 203(国网 7.9.4/7.9.5)：预置 COT=6+区域(2B)+PI(TLV)；固化/撤销 COT=8+区域(2B)+PI(VSQ=0)
          PI 位序(BS1[1]=bit0)：bit7=S/E(1=预置,0=固化)、bit6=CR(1=取消预置)。
          预置 PI=0x80、固化 PI=0x00、撤销 PI=0x40。"""
        self._ensure()
        assert self._params
        if tid == 203:
            if cancel:
                cot, pi = 8, 0x40   # 撤销：停止激活(8) + CR=1(bit6)
            elif select:
                cot, pi = 6, 0x80   # 预置：激活(6) + S/E=1(bit7)，带 TLV 对象
            else:
                cot, pi = 6, 0x00   # 固化：激活(6) + S/E=0，无对象
            items = [(ioa, value)] if select and not cancel else []
            asdu = codec.build_write_param(
                self._params.common_address, items, area=area, pi=pi, oa=self._params.originator,
                cot=cot, tid=tid,
                cot_size=self._params.cot_size, ca_size=self._params.ca_size, ioa_size=self._params.ioa_size,
            )
        else:
            cot = 8 if cancel else 6
            asdu = codec.build_preset_param(
                self._params.common_address, ioa, value, select=select,
                oa=self._params.originator, cot=cot, tid=tid,
                cot_size=self._params.cot_size, ca_size=self._params.ca_size, ioa_size=self._params.ioa_size,
            )
        action = "撤销" if cancel else ("预置" if select else "固化")
        self._send_i(asdu, note=f"定值整定({tid}) IOA={ioa} {action} value={value} 区号={area}")

    def read_setting_area(self) -> None:
        """读定值区号：C_RR_NA_1(201)，国网。"""
        self._ensure()
        assert self._params
        asdu = codec.build_area_command(
            self._params.common_address, 0, tid=201, oa=self._params.originator,
            cot_size=self._params.cot_size, ca_size=self._params.ca_size, ioa_size=self._params.ioa_size,
        )
        self._send_i(asdu, note="读定值区号(201)")

    def switch_setting_area(self, area: int) -> None:
        """切换定值区：C_SR_NA_1(200)，国网。"""
        self._ensure()
        assert self._params
        asdu = codec.build_area_command(
            self._params.common_address, int(area), tid=200, oa=self._params.originator,
            cot_size=self._params.cot_size, ca_size=self._params.ca_size, ioa_size=self._params.ioa_size,
        )
        self._send_i(asdu, note=f"切换定值区(200) 区号={area}")

    def wait_i_frame(self, before: int, timeout: float) -> bool:
        """等待收到新的信息帧（计数超过 before）或超时；用于"召唤一批→等回执→下一批"。"""
        end = time.time() + max(0.0, float(timeout))
        while time.time() < end:
            if self._i_frames > before:
                return True
            time.sleep(0.05)
        return False

    def wait_cmd_result(self, type_ids: set, cots: set, before: int, timeout: float):
        """等待从站对命令的确认帧（指定类型/原因，序号晚于 before）；返回 {type_id,cot,pos} 或 None(超时)。
        pos=True 肯定确认；pos=False 否定确认（P/N=1）。"""
        end = time.time() + max(0.0, float(timeout))
        while time.time() < end:
            with self._lock:
                msgs = list(self._i_msgs)
            for cnt, tid, cot, pos in msgs:
                if cnt > before and tid in type_ids and cot in cots:
                    return {"type_id": tid, "cot": cot, "pos": pos}
            time.sleep(0.05)
        return None

    def i_frame_count(self) -> int:
        return self._i_frames

    def _classify_bind_error(self, local_ip: str, local_port: int, e: OSError) -> MasterError:
        en = getattr(e, "winerror", None) or e.errno
        msg = str(e)
        if en in (errno.EADDRNOTAVAIL, 10049):
            return MasterError(
                "LOCAL_IP_NOT_FOUND",
                f"本地 IP 不存在或不可用：{local_ip}（请从网卡列表重新选择）",
            )
        if en in (errno.EADDRINUSE, 10048):
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
        if "session_id" not in event and self.session_id:
            event = {**event, "session_id": self.session_id}
        try:
            self.on_event(event)
        except Exception:
            pass

    def _log(self, text: str) -> None:
        """链路初始化/运行提示，打印到报文监视框。"""
        self._emit({"type": "log", "text": text, "ts": time.time()})

    def _send_raw(self, data: bytes, direction: str = "TX", note: str = "") -> None:
        if self._params and self._params.tx_delay_ms:
            time.sleep(self._params.tx_delay_ms / 1000.0)
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
        if self._params and self._params.tx_delay_ms:
            time.sleep(self._params.tx_delay_ms / 1000.0)
        with self._lock:
            frame = codec.build_i_frame(self._ns, self._nr, asdu)
            self._ns = (self._ns + 1) & 0x7FFF
            if not self._sock:
                raise MasterError("NOT_CONNECTED", "尚未连接从站")
            self._sock.sendall(frame)
            self._last_tx = time.time()
        self._last_cmd_sent = time.time()
        self._cmd_timeout_notified = False
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
                    self._log("链路断开：对端(从站)关闭了连接")
                    self._emit(
                        {
                            "type": "connection",
                            "state": "disconnected",
                            "message": "对端关闭连接",
                        }
                    )
                    self._connected = False
                    try:
                        if self._sock:
                            self._sock.close()
                    except OSError:
                        pass
                    self._sock = None
                    break
                self._last_rx = time.time()
                self._cmd_timeout_notified = False
                self._testfr_waiting = False  # 收到任何数据视为链路存活
                self._buf.extend(data)
                self._consume_buffer()
            except socket.timeout:
                p = self._params
                now = time.time()
                if p:
                    # T3 空闲超时：发送链路测试(act)，并等待从站确认
                    # 空闲 = 最后收发较晚者起算（与从站对等，避免从站先到期断开）
                    idle = now - max(self._last_rx, self._last_tx)
                    if not self._testfr_waiting and idle > p.t3:
                        try:
                            self._send_raw(codec.build_u_frame(testfr=True), note="链路测试(act)")
                            self._testfr_waiting = True
                            self._testfr_sent_at = now
                            self._log(f"链路测试：空闲 {p.t3:.0f}s，发送 TESTFR(act)，等待确认(超时 T1={p.t1:.0f}s)")
                        except MasterError:
                            break
                    # 已发 TESTFR，但 T1 内未收到确认：链路失效，断开重连
                    if self._testfr_waiting and now - self._testfr_sent_at > p.t1:
                        self._log(f"链路测试确认超时：{p.t1:.0f}s 内未收到 TESTFR 确认，断开链路")
                        self._emit(
                            {
                                "type": "connection",
                                "state": "disconnected",
                                "message": f"链路测试确认超时：{p.t1:.0f}s 内未收到 TESTFR 确认，断开链路",
                            }
                        )
                        self._connected = False
                        try:
                            if self._sock:
                                self._sock.close()
                        except OSError:
                            pass
                        self._sock = None
                        break
                # 自动初始化兜底：总召唤发出后 8s 内未收到激活终止，仍发起时钟同步
                if self.auto_init and self._gi_sent_at and not self._clock_sent:
                    if now - self._gi_sent_at > 8.0:
                        try:
                            self.clock_sync()
                            self._clock_sent = True
                        except MasterError:
                            pass
                continue
            except OSError as e:
                if not self._stop.is_set():
                    self._log(f"链路断开：接收中断（{e}）")
                    self._emit(
                        {
                            "type": "connection",
                            "state": "disconnected",
                            "message": "接收中断，连接已断开",
                        }
                    )
                self._connected = False
                try:
                    if self._sock:
                        self._sock.close()
                except OSError:
                    pass
                self._sock = None
                break
        self._connected = False
        # [AGENT_CHANGE_BEGIN] 2026-09-11 104静默无限重连
        if self._params and self._params.auto_reconnect and not self._stop.is_set():
            self._start_reconnect(self._params)
        # [AGENT_CHANGE_END] 2026-09-11 104静默无限重连

    def _consume_buffer(self) -> None:
        while True:
            if len(self._buf) < 2:
                return
            if self._buf[0] != START_BYTE:
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
            self._emit(
                {"type": "frame", "direction": "RX", "hex": codec.hex_dump(frame), "note": "解析失败"}
            )
            return

        note = kind
        if kind == "U":
            if detail.get("start_dt_con"):
                note = "启动数据传输确认(con)"
                self._startdt_ok = True
                self._startdt_timeout_notified = False
                # 自动初始化：STARTDT 确认后自动总召唤
                if self.auto_init and not self._gi_sent_at:
                    try:
                        self._log("链路初始化：收到启动传输确认，自动总召唤")
                        self.general_interrogation()
                        self._gi_sent_at = time.time()
                    except MasterError:
                        pass
            elif detail.get("testfr_act"):
                note = "链路测试(act)"
                try:
                    self._send_raw(codec.build_u_frame(testfr=True, con=True), note="链路测试确认(con)")
                except MasterError:
                    pass
            elif detail.get("testfr_con"):
                note = "链路测试确认(con)"
            else:
                note = f"控制帧 {detail}"
            self._emit(
                {
                    "type": "frame",
                    "direction": "RX",
                    "hex": codec.hex_dump(frame),
                    "note": note,
                    "ts": time.time(),
                }
            )
            return

        if kind == "S":
            self._emit(
                {
                    "type": "frame",
                    "direction": "RX",
                    "hex": codec.hex_dump(frame),
                    "note": f"监视帧(确认) N(R)={detail.get('nr')}",
                    "ts": time.time(),
                }
            )
            return

        self._nr = (detail["ns"] + 1) & 0x7FFF
        self._ack_pending += 1
        self._i_frames += 1
        self._last_i_rx = time.time()
        asdu_raw = detail.get("asdu") or b""
        parsed = None
        try:
            sz = self._params if self._params else None
            parsed = codec.decode_asdu(
                asdu_raw,
                cot_size=(sz.cot_size if sz else 2),
                ca_size=(sz.ca_size if sz else 2),
                ioa_size=(sz.ioa_size if sz else 3),
            )
            note = f"信息帧 {parsed.type_name} 原因={cot_name(parsed.cot)}"
            # 确认类原因附带肯定/否定位（P/N：1=否定，0=肯定）
            if parsed.cot in (1, 7, 9, 10, 11):
                note += "(肯定)" if parsed.pos else "(否定)"
            if parsed.test:
                note += "(测试)"
            note += f" 公共地址={parsed.ca}"
            # 对象摘要：IOA + 值
            objs = parsed.objects
            if objs:
                parts = []
                for o in objs[:8]:
                    v = o.value
                    if isinstance(v, bool):
                        vs = "合" if v else "分"
                    elif isinstance(v, float):
                        vs = "%g" % v
                    else:
                        vs = str(v)
                    desc = f"IOA=0x{o.ioa:X}({o.ioa}) 值={vs}"
                    if o.extra.get("area") is not None:
                        desc += f" 区号={o.extra['area']}"
                        if o.extra.get("feat") is not None:
                            desc += f" 特征={o.extra['feat']}"
                    parts.append(desc)
                if len(objs) > 8:
                    parts.append(f"…共{len(objs)}个")
                note += " | " + " ; ".join(parts)
        except Exception:
            note = "信息帧(ASDU 解析失败)"

        if parsed is not None:
            self._i_msgs.append((self._i_frames, parsed.type_id, parsed.cot, parsed.pos))
            if len(self._i_msgs) > 256:
                del self._i_msgs[:128]

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
                        {"ioa": o.ioa, "value": o.value, "quality": o.quality, "extra": o.extra}
                        for o in parsed.objects
                    ],
                },
            }
        )

        if parsed and parsed.type_id == 100 and parsed.cot == 10 and self.auto_init and not self._clock_sent:
            # 总召唤激活终止：自动时钟同步
            try:
                self._log("链路初始化：总召唤完成，自动时钟同步")
                self.clock_sync()
                self._clock_sent = True
            except MasterError:
                pass
        if parsed and parsed.type_id == 103 and parsed.cot == 7:
            if not self._clock_sent:
                self._log("链路初始化：时钟同步完成，初始化结束")
            self._clock_sent = True

        # 仅监视方向(数据类)报文更新实时数据点表；控制类确认（如总召唤/对时/遥控回执）不产生数据点
        if parsed and parsed.objects and parsed.type_id in MONITOR_TYPES:
            self._emit(
                {
                    "type": "points",
                    "ca": parsed.ca,
                    "type_id": parsed.type_id,
                    "cot": parsed.cot,
                    "objects": [
                        {"ioa": o.ioa, "value": o.value, "quality": o.quality, "extra": o.extra,
                         "type_id": parsed.type_id, "pos": parsed.pos}
                        for o in parsed.objects
                    ],
                }
            )

        # S 帧确认策略：
        # ① 遥控(45/46/47)/遥调(48/49/50/55/108/202/203)/SOE(30/31)/否定确认后立即回 S 帧
        #    —— 刷新终端链路活动时间，同时若终端已断链，发送将立刻暴露异常
        # ② 其余按 W/2 阈值批量确认（T2 定时兜底见 _timer_loop）
        w = self._params.w if self._params else 8
        urgent = bool(
            parsed is not None
            and (
                parsed.type_id in (30, 31, 45, 46, 47, 48, 49, 50, 55, 108, 202, 203)
                or not parsed.pos  # 否定确认(P/N=1)
            )
        )
        if self._ack_pending and (urgent or self._ack_pending >= max(1, w // 2)):
            try:
                self._send_raw(codec.build_s_frame(self._nr), note=f"监视帧(确认) N(R)={self._nr}")
                self._ack_pending = 0
            except MasterError as e:
                self._log(f"发送监视帧(确认)失败：{e}（链路可能已断开）")

    def _timer_loop(self) -> None:
        """周期任务（总召唤/校时/召唤度）与超时检测（启动应答/远控命令）。"""
        while not self._stop.is_set():
            time.sleep(0.5)
            if not self._connected or not self._params:
                continue
            p = self._params
            now = time.time()
            try:
                if p.gi_period and now - self._last_gi >= p.gi_period:
                    self._last_gi = now
                    self.general_interrogation()
                if p.call_period and now - self._last_call >= p.call_period:
                    self._last_call = now
                    self.general_interrogation()
                # [AGENT_CHANGE_BEGIN] 2026-09-10 设备参数周期
                if p.clock_period and now - self._last_clock >= float(p.clock_period) * 60.0:
                    self._last_clock = now
                    self.clock_sync()
                # [AGENT_CHANGE_END] 2026-09-10 设备参数周期
                # T2 确认超时：收到 I 帧后未达到 W/2 触发时，定时回 S 帧确认
                if self._ack_pending and now - self._last_i_rx > p.t2:
                    try:
                        self._send_raw(
                            codec.build_s_frame(self._nr), note=f"监视帧(确认) N(R)={self._nr}"
                        )
                        self._ack_pending = 0
                    except MasterError:
                        pass
                # 链路应答超时：STARTDT 发出后未收到确认
                if (
                    self._startdt_sent_at
                    and not self._startdt_ok
                    and not self._startdt_timeout_notified
                    and now - self._startdt_sent_at > p.link_ack_timeout
                ):
                    self._startdt_timeout_notified = True
                    self._log(f"链路初始化超时：{p.link_ack_timeout:.0f}s 内未收到「启动传输确认」")
                    self._emit(
                        {
                            "type": "connection",
                            "state": "notice",
                            "message": f"链路应答超时：{p.link_ack_timeout:.0f}s 内未收到启动传输确认",
                        }
                    )
                # 远控命令超时
                if (
                    self._last_cmd_sent
                    and not self._cmd_timeout_notified
                    and now - self._last_cmd_sent > p.cmd_timeout
                    and self._last_cmd_sent > self._last_rx
                ):
                    self._cmd_timeout_notified = True
                    self._emit(
                        {
                            "type": "connection",
                            "state": "notice",
                            "message": f"远控命令响应超时：{p.cmd_timeout:.0f}s 无应答",
                        }
                    )
            except MasterError:
                pass

    def _start_reconnect(self, params: ConnectParams) -> None:
        # [AGENT_CHANGE_BEGIN] 2026-09-11 104静默无限重连
        t = self._reconnect_thread
        if t is not None and t.is_alive():
            return
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, args=(params,), name="iec104-reconnect", daemon=True
        )
        self._reconnect_thread.start()
        # [AGENT_CHANGE_END] 2026-09-11 104静默无限重连

    def _reconnect_loop(self, params: ConnectParams) -> None:
        # [AGENT_CHANGE_BEGIN] 2026-09-11 104静默无限重连
        # 不限次数；每 5s：未连接则静默 TCP 重连（connect 内发启动帧），
        # 已连接未收到启动确认则补发启动帧；直到从站 STARTDT con 或用户断开。
        # 不打印「自动重连」类提示。connect()→disconnect() 会置位 _stop，失败后须清除。
        interval = 5.0
        while not self._stop.is_set():
            time.sleep(interval)
            if self._stop.is_set():
                return
            if self._connected and self._startdt_ok:
                return
            if self._connected and not self._startdt_ok:
                try:
                    self._send_raw(
                        codec.build_u_frame(start_dt=True),
                        note="启动数据传输(act)",
                    )
                    self._startdt_sent_at = time.time()
                    self._startdt_timeout_notified = False
                except MasterError:
                    if not self._connected:
                        self._stop.clear()
                continue
            try:
                self.connect(params)
            except MasterError:
                if not self._connected:
                    self._stop.clear()
                continue
        # [AGENT_CHANGE_END] 2026-09-11 104静默无限重连


# [AGENT_CHANGE_END] 2026-09-07 104-MVP主站
