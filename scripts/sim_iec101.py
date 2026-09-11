# -*- coding: utf-8 -*-
"""Offline test for IEC 60870-5-101 master (unbalanced + balanced).

Runs the real Iec101Master against a minimal in-memory slave, wired through a
fake serial port, so no hardware / COM port is required.

Usage:  python scripts/sim_iec101.py
"""
from __future__ import annotations

import os
import struct
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from protocol.iec101 import link                       # noqa: E402
from protocol.iec101.master import Iec101Master, SerialParams  # noqa: E402
from protocol.iec104 import codec as codec104          # noqa: E402

ADDR = 1
ADDR_SIZE = 2      # field captures (KW-2200 / terminals) use a 2-byte link address


# ---------------------------------------------------------------- ASDU bytes
def mk_asdu(type_id, cot, ca, objs, count=None):
    """ASDU header + object bytes (SQ=0)."""
    n = count if count is not None else (1 if objs else 0)
    return bytes([type_id, n & 0x7F]) + struct.pack("<H", cot) + struct.pack("<H", ca) + objs


def sp_objects(pairs):
    """M_SP_NA_1 body: IOA(2) + SIQ(1) per point."""
    b = b""
    for ioa, on in pairs:
        b += struct.pack("<H", ioa) + bytes([0x01 if on else 0x00])
    return b


def me_nc_objects(pairs):
    """M_ME_NC_1 body: IOA(2) + IEEE754 float + QDS."""
    b = b""
    for ioa, val in pairs:
        b += struct.pack("<H", ioa) + struct.pack(">f", float(val)) + b"\x00"
    return b


def sp_tb_objects(pairs):
    """M_SP_TB_1 (SOE) body: IOA(2) + SIQ(1) + CP56Time2a(7)."""
    b = b""
    for ioa, on in pairs:
        cp56 = struct.pack("<H", 12345) + bytes([0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00])
        b += struct.pack("<H", ioa) + bytes([0x01 if on else 0x00]) + cp56
    return b


CA = 1
GI_CONF = mk_asdu(100, 7, CA, b"")                                  # 总召唤激活确认
GI_DATA_SP = mk_asdu(1, 20, CA, sp_objects([(1, True), (2, False)]), count=2)
GI_DATA_ME = mk_asdu(13, 20, CA, me_nc_objects([(1001, 220.5)]))
GI_END = mk_asdu(100, 10, CA, b"")                                  # 激活终止
SPONT_ME = mk_asdu(13, 3, CA, me_nc_objects([(1001, 12.34)]))
SOE_SP = mk_asdu(30, 3, CA, sp_tb_objects([(5, True)]))
MEI_NA_1 = mk_asdu(70, 4, CA, b"\x00\x00\x02", count=1)   # M_EI_NA_1 初始化结束(现场: 46 01 04 00 01 00 00 00 02)


# ---------------------------------------------------------------- fake serial
class SlaveSim:
    """Minimal unbalanced/balanced 101 slave."""

    def __init__(self, balanced=False, acd=False, single_char_ack=False):
        self.balanced = balanced
        self.single_char_ack = single_char_ack   # reset link confirmed with E5 (per DL/T 634.5101)
        self.buf = bytearray()
        self.out = bytearray()
        self.pending = []
        self.fc_rx = []          # primary frames received
        self.ack_rx = []         # secondary-direction frames received
        self.rx_frames = []      # raw frames as received
        self.rx_asdus = []
        self.acd = acd          # access demand flag reported in link-status response
        self.interrogated = False
        self.init_reported = False

    def emit(self, frame):
        self.out += frame

    def feed(self, data):
        for frame in link.feed(self.buf, data, ADDR_SIZE):
            self.rx_frames.append(frame)
            info = link.parse_frame(frame, ADDR_SIZE)
            try:
                self.handle(info)
            except Exception as e:  # pragma: no cover
                print("  slave error:", e)

    def handle(self, info):
        if info["kind"] == "fixed":
            if not info["prm"]:
                # 主站响应帧(确认/链路忙)或非平衡从站帧:不响应
                self.ack_rx.append(info["fc"])
                return
            fc = info["fc"]
            self.fc_rx.append(fc)
            if fc == link.FC_RESET_LINK:
                if self.single_char_ack:
                    self.emit(link.build_single())
                else:
                    self.emit(link.build_fixed(link.ctrl_secondary(link.FC_ACK), ADDR, ADDR_SIZE))
                if not self.init_reported:
                    # 现场行为：复位链路确认后，从站主动上报“初始化结束”(M_EI_NA_1)
                    self.init_reported = True
                    self.emit(link.build_variable(link.ctrl_secondary(link.FC_DATA), ADDR,
                                                  MEI_NA_1, ADDR_SIZE))
            elif fc == link.FC_REQ_LINK_STATUS:
                # 现场从站：用“链路忙”(FC=11)回应请求链路状态
                self.emit(link.build_fixed(link.ctrl_secondary(link.FC_LINK_BUSY), ADDR, ADDR_SIZE))
                if self.balanced:
                    # 并主动发起请求链路状态(PRM=1, DIR=0 -> 0x49)，期待主站回“链路忙”(0x8B)
                    self.emit(link.build_fixed(
                        link.ctrl_balanced(link.FC_REQ_LINK_STATUS, False, prm=True), ADDR, ADDR_SIZE))
            elif fc in (link.FC_REQ_LEVEL1, link.FC_REQ_LEVEL2):
                self.serve_poll()
            return
        if info["kind"] == "variable":
            if self.balanced:
                self.emit(link.build_fixed(link.ctrl_balanced(link.FC_USER_DATA_CONF, False), ADDR, ADDR_SIZE))
            else:
                self.emit(link.build_fixed(link.ctrl_secondary(link.FC_ACK), ADDR, ADDR_SIZE))
            try:
                parsed = codec104.decode_asdu(info["asdu"], cot_size=2, ca_size=2, ioa_size=2)
            except Exception:
                return
            self.rx_asdus.append(parsed)
            if parsed.type_id == 100 and parsed.cot == 6:
                self.interrogated = True
                self.pending.extend([GI_CONF, GI_DATA_SP, GI_DATA_ME, GI_END])
                if self.balanced:
                    while self.pending:
                        self.serve_poll()

    def serve_poll(self):
        if not self.pending:
            ctrl = link.ctrl_secondary(link.FC_NO_DATA, self.acd)
            self.acd = False
            self.emit(link.build_fixed(ctrl, ADDR, ADDR_SIZE))
            return
        asdu = self.pending.pop(0)
        ctrl = link.ctrl_balanced(link.FC_DATA, False) if self.balanced \
            else link.ctrl_secondary(link.FC_DATA)
        self.emit(link.build_variable(ctrl, ADDR, asdu, ADDR_SIZE))


class SilentSim:
    """Slave that never answers: verifies non-blocking connect + honest status."""

    def __init__(self):
        self.buf = bytearray()
        self.out = bytearray()
        self.rx_frames = []

    def feed(self, data):
        for frame in link.feed(self.buf, data, ADDR_SIZE):
            self.rx_frames.append(frame)

    def emit(self, _frame):
        pass

    def fc_list(self):
        out = []
        for f in self.rx_frames:
            try:
                info = link.parse_frame(f, ADDR_SIZE)
            except Exception:
                continue
            if info["kind"] == "fixed":
                out.append(info["fc"])
        return out


class LoopSerial:
    def __init__(self, sim, **kw):
        self.sim = sim
        self.closed = False

    def write(self, data):
        self.sim.feed(bytes(data))
        return len(data)

    def flush(self):
        pass

    def read(self, n=1):
        out = self.sim.out
        if out:
            chunk = bytes(out[:n])
            del out[:n]
            return chunk
        time.sleep(0.02)
        return b""

    @property
    def in_waiting(self):
        return len(self.sim.out)

    def close(self):
        self.closed = True


def install_fake_serial(sim):
    fake = types.ModuleType("serial")
    fake.Serial = lambda **kw: LoopSerial(sim, **kw)
    sys.modules["serial"] = fake


# ---------------------------------------------------------------- test helpers
FAILS = []


def check(cond, label, extra=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def wait_for(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def run(unbalanced: bool):
    print("\n=== %s mode ===" % ("unbalanced" if unbalanced else "balanced"))
    sim = SlaveSim(balanced=not unbalanced, acd=True, single_char_ack=not unbalanced)
    install_fake_serial(sim)
    events = []
    m = Iec101Master(on_event=events.append)
    m.session_id = "sim"
    p = SerialParams(port="FAKE", baudrate=9600, parity="N", link_addr=ADDR,
                     addr_size=ADDR_SIZE, balanced=not unbalanced, data_frame_dir=True,
                     poll_period=0.2, resp_timeout=1.0, common_address=CA)
    m.connect(p)

    check(m.connected, "connection established (fake serial)")
    check(wait_for(lambda: link.FC_RESET_LINK in sim.fc_rx, 1.5), "reset link (FC=0) sent")
    check(wait_for(lambda: link.FC_REQ_LINK_STATUS in sim.fc_rx, 1.5), "request link status (FC=9) sent")
    def logs():
        return [e.get("text", "") for e in events if e.get("type") == "log"]

    check(wait_for(lambda: any("初始化完成" in t for t in logs()), 3.0),
          "link init reported complete (slave confirmed)", str(logs()[:3])[:160])
    check(not any("未收到从站确认" in t for t in logs()), "no false 'no confirmation' warning")
    if not unbalanced:
        check(any(e.get("type") == "frame" and e.get("direction") == "RX"
                  and e.get("hex") == "E5" for e in events), "single-char E5 confirmation handled")

    if unbalanced:
        check(wait_for(lambda: (link.FC_REQ_LEVEL2 in sim.fc_rx), 2.0), "cyclic level-2 poll observed")
        check(wait_for(lambda: (link.FC_REQ_LEVEL1 in sim.fc_rx), 4.0),
              "level-1 poll after ACD=1", "slave set ACD=1")

    # spontaneous telemetry
    sim.pending.append(SPONT_ME)
    if not unbalanced:
        sim.serve_poll()
    got_me = wait_for(lambda: any(e.get("type") == "points" and e.get("type_id") == 13 for e in events), 3.0)
    check(got_me, "spontaneous M_ME_NC_1 delivered to points event")

    # general interrogation is auto-issued right after link init
    check(wait_for(lambda: sim.interrogated, 8.0), "slave received C_IC_NA_1 (COT=6) [auto GI]")
    check(wait_for(lambda: any(e.get("type") == "points" and e.get("type_id") == 1
                               for e in events), 5.0), "GI single points delivered")
    check(wait_for(lambda: any(e.get("type") == "points" and e.get("type_id") == 13
                               and e.get("cot") in (9, 10, 20) for e in events), 5.0),
          "GI measured values delivered")
    gi_end = wait_for(lambda: any(e.get("type") == "frame" and e.get("direction") == "RX"
                                  and (e.get("asdu") or {}).get("type_id") == 100
                                  and (e.get("asdu") or {}).get("cot") == 10 for e in events), 4.0)
    check(gi_end, "GI activation termination (COT=10) received")

    # commands
    m.single_command(5, True, select=False)
    check(wait_for(lambda: any(a.type_id == 45 and a.cot == 6 for a in sim.rx_asdus), 2.0),
          "C_SC_NA_1 command written to slave")
    m.clock_sync()
    check(wait_for(lambda: any(a.type_id == 103 for a in sim.rx_asdus), 2.0),
          "C_CS_NA_1 clock sync written to slave")

    if not unbalanced:
        # slave-initiated SOE in balanced mode
        sim.emit(link.build_variable(link.ctrl_balanced(link.FC_DATA, False), ADDR, SOE_SP, ADDR_SIZE))
        check(wait_for(lambda: any(e.get("type") == "points" and e.get("type_id") == 30 for e in events), 3.0),
              "SOE (M_SP_TB_1) pushed by slave")
        check(wait_for(lambda: link.FC_ACK in sim.ack_rx, 2.0),
              "master acknowledges slave data (FC=0 / 0x80)")

    tx = [e for e in events if e.get("type") == "frame" and e.get("direction") == "TX"]
    rx = [e for e in events if e.get("type") == "frame" and e.get("direction") == "RX"]
    raw = [f.hex(" ").upper() for f in sim.rx_frames]
    if unbalanced:
        check(any(h == "10 40 01 00 41 16" for h in raw),
              "frame format: reset link = 10 40 01 00 41 16", str(raw[:3]))
        check(any(h == "10 49 01 00 4A 16" for h in raw),
              "frame format: link status = 10 49 01 00 4A 16")
        check(any(h.startswith("10 4B 01 00") for h in raw), "frame format: level-2 poll = 10 4B 01 00 ...")
    else:
        check(any(h == "10 C0 01 00 C1 16" for h in raw),
              "frame format: reset link = 10 C0 01 00 C1 16", str(raw[:3]))
        check(any(h == "10 C9 01 00 CA 16" for h in raw),
              "frame format: link status = 10 C9 01 00 CA 16")
        check(any(h == "10 8B 01 00 8C 16" for h in raw),
              "master answers slave-originated link-status request with link busy (10 8B 01 00 8C 16)",
              str([h for h in raw if h.startswith("10 8")][:2]))
    want_c = ("F3", "D3") if not unbalanced else ("73", "53")   # 平衡带 DIR；非平衡用 PRM 帧
    check(any(len(h.split(" ")) > 6 and h.split(" ")[4] in want_c for h in raw),
          f"frame format: user-data control byte = 0x{want_c[0]}/0x{want_c[1]}",
          str([h[:23] for h in raw if h.startswith("68")][:2]))
    # 校验和：标准=sum(用户数据)；兼容=再^0x80
    var = [f for f in sim.rx_frames if f and f[0] == 0x68]
    # [AGENT_CHANGE_BEGIN] 2026-09-10 101可变帧校验和按用户数据求和
    bad_cs = [f.hex(" ").upper() for f in var
              if f[-2] not in ((sum(f[4:-2]) & 0xFF), (sum(f[4:-2]) & 0xFF) ^ 0x80)]
    # [AGENT_CHANGE_END] 2026-09-10 101可变帧校验和按用户数据求和
    check(bool(var) and not bad_cs, "variable frame checksum valid (standard or field algorithm)",
          str(bad_cs[:1]))
    fixed = [f for f in sim.rx_frames if f and f[0] == 0x10]
    bad_fcs = [f.hex(" ").upper() for f in fixed if f[-2] != (sum(f[1:-2]) & 0xFF)]
    check(not bad_fcs, "fixed frame checksum = sum(C,A) mod 256", str(bad_fcs[:1]))
    print("  frames: TX=%d RX=%d, slave got %d ASDUs" % (len(tx), len(rx), len(sim.rx_asdus)))
    for a in sim.rx_asdus:
        print("    slave RX: %s COT=%s" % (a.type_name, a.cot))
    m.disconnect()
    check(not m.connected, "disconnected cleanly")


def run_link_roundtrip():
    print("\n=== link layer round-trip ===")
    for addr_size in (1, 2):
        addr = 0x1234 if addr_size == 2 else 7
        for ctrl, note in ((link.ctrl_primary(link.FC_USER_DATA, fcb=True, fcv=True), "primary FC=3"),
                           (link.ctrl_secondary(link.FC_DATA, acd=True), "secondary FC=8"),
                           (link.ctrl_balanced(link.FC_USER_DATA, True, True, True), "balanced FC=3")):
            fixed = link.build_fixed(ctrl, addr, addr_size)
            f2 = list(link.feed(bytearray(), fixed, addr_size))[0]
            i2 = link.parse_frame(f2, addr_size)
            ok = i2["ctrl"] == ctrl and i2["addr"] == addr and i2["kind"] == "fixed"
            check(ok, f"{note} fixed frame addr_size={addr_size}",
                  f"ctrl=0x{ctrl:02X} addr={addr} fc={i2['fc']}")
        var = link.build_variable(link.ctrl_primary(link.FC_USER_DATA, True, True), addr, GI_CONF, addr_size)
        fv = list(link.feed(bytearray(), var, addr_size))[0]
        iv = link.parse_frame(fv, addr_size)
        check(iv["asdu"] == GI_CONF and iv["addr"] == addr, f"variable frame addr_size={addr_size}")
    # byte-by-byte reassembly
    frame = link.build_variable(link.ctrl_primary(link.FC_USER_DATA), 1, GI_DATA_ME, 1)
    buf = bytearray()
    out = []
    for b in frame:
        out.extend(link.feed(buf, bytes([b]), 1))
    check(len(out) == 1 and out[0] == frame, "byte-wise reassembly of variable frame")
    check(list(link.feed(bytearray(), link.build_single(), 1))[0] == bytes([0xE5]), "single char E5 frame")


def run_no_slave_response():
    print("\n=== no slave response (non-blocking connect) ===")
    sim = SilentSim()
    install_fake_serial(sim)
    events = []
    m = Iec101Master(on_event=events.append)
    m.session_id = "silent"
    p = SerialParams(port="FAKE", link_addr=ADDR, addr_size=ADDR_SIZE, balanced=False,
                     poll_period=0.3, resp_timeout=10.0, common_address=CA)
    t0 = time.time()
    m.connect(p)
    dt = time.time() - t0
    check(dt < 1.0, f"connect() returns immediately (took {dt:.2f}s)")
    check(wait_for(lambda: link.FC_RESET_LINK in sim.fc_list(), 2.0), "reset link sent")
    check(wait_for(lambda: link.FC_REQ_LINK_STATUS in sim.fc_list(), 2.0), "link status request sent")
    check(wait_for(lambda: link.FC_REQ_LEVEL2 in sim.fc_list(), 3.0), "polling starts without waiting for link init")
    def logs():
        return [e.get("text", "") for e in events if e.get("type") == "log"]

    check(wait_for(lambda: any("未收到从站确认" in t for t in logs()), 6.0),
          "reports 'no confirmation' honestly when silent", str(logs())[:200])
    check(not any("链路初始化完成" in t for t in logs()), "never claims init complete without confirmation")
    m.disconnect()


def run_dir_variant():
    """Optional style: user-data frames WITHOUT DIR (control 0x73/0x53)."""
    print("\n=== balanced without DIR in user-data frames ===")
    sim = SlaveSim(balanced=True, acd=False, single_char_ack=True)
    install_fake_serial(sim)
    m = Iec101Master(on_event=lambda _e: None)
    m.session_id = "nodir"
    p = SerialParams(port="FAKE", link_addr=ADDR, addr_size=ADDR_SIZE, balanced=True,
                     data_frame_dir=False, resp_timeout=1.0)
    m.connect(p)
    check(wait_for(lambda: any(link.parse_frame(f, ADDR_SIZE)["kind"] == "fixed"
                               and link.parse_frame(f, ADDR_SIZE)["fc"] == link.FC_REQ_LINK_STATUS
                               for f in sim.rx_frames), 3.0), "link init done")
    m.clock_sync()
    raw = [f.hex(" ").upper() for f in sim.rx_frames]
    check(wait_for(lambda: any(len(h.split(" ")) > 6 and h.split(" ")[4] in ("73", "53")
                               for h in raw if h.startswith("68")), 3.0),
          "user-data control byte = 0x73/0x53 when DIR disabled",
          str([h[:23] for h in raw if h.startswith("68")][:2]))
    m.disconnect()


def run_field_frame_check():
    """Byte-compare our frames against the real KW-2200 capture (F30G terminal)."""
    print("\n=== field capture compatibility (KW-2200 / F30G) ===")
    field_gi = bytes.fromhex("68 0C 0C 68 F3 01 00 64 01 06 00 01 00 00 00 14 74 16")
    info = link.parse_frame(field_gi, ADDR_SIZE)
    check(info["kind"] == "variable" and info["cs_ok"],
          "field capture (GI) parses, checksum accepted (field algorithm)")
    asdu = codec104.build_interrogation(CA, oa=0, cot_size=2, ca_size=2, ioa_size=2)
    ctrl = link.ctrl_balanced(link.FC_USER_DATA, True, True, True)
    # [AGENT_CHANGE_BEGIN] 2026-09-10 101可变帧校验和按用户数据求和
    ours = link.build_variable(ctrl, ADDR, asdu, ADDR_SIZE, cs_compat=False)
    check(ours == field_gi, "our GI frame is byte-identical to KW-2200 capture",
          f"ours={ours.hex(' ').upper()} field={field_gi.hex(' ').upper()}")
    compat = link.build_variable(ctrl, ADDR, asdu, ADDR_SIZE, cs_compat=True)
    check(compat[-2] == field_gi[-2] ^ 0x80, "compat checksum = standard ^ 0x80",
          f"compat={compat[-2]:02X} field={field_gi[-2]:02X}")
    # 对时 L=18：标准 CS 必须等于 sum(用户数据)，不能把 L/L/68 算进去
    clock_asdu = codec104.build_clock_sync(CA, oa=0, cot_size=2, ca_size=2, ioa_size=2)
    clock_fr = link.build_variable(ctrl, ADDR, clock_asdu, ADDR_SIZE, cs_compat=False)
    clock_user = clock_fr[4:-2]
    check(clock_fr[-2] == (sum(clock_user) & 0xFF),
          "clock-sync L=18 checksum = sum(user data) only",
          f"cs={clock_fr[-2]:02X} expect={(sum(clock_user)&0xFF):02X} frame={clock_fr.hex(' ').upper()}")
    # [AGENT_CHANGE_END] 2026-09-10 101可变帧校验和按用户数据求和
    field_mei = bytes.fromhex("68 0C 0C 68 73 01 00 46 01 04 00 01 00 00 00 02 C2 16")
    check(link.parse_frame(field_mei, ADDR_SIZE)["cs_ok"],
          "field capture (slave M_EI_NA_1) parses, checksum accepted")
    field_ack = bytes.fromhex("10 80 01 00 81 16")
    fi = link.parse_frame(field_ack, ADDR_SIZE)
    check(fi["kind"] == "fixed" and fi["fc"] == link.FC_ACK and fi["prm"] is False,
          "field capture (master ACK 0x80) parsed as confirmation")


if __name__ == "__main__":
    run_link_roundtrip()
    run_field_frame_check()
    run(unbalanced=True)
    run(unbalanced=False)
    run_dir_variant()
    run_no_slave_response()
    print("\n%s (%d failure%s)" % ("ALL PASSED" if not FAILS else "FAILURES: " + ", ".join(FAILS),
                                  len(FAILS), "" if len(FAILS) == 1 else "s"))
    sys.exit(1 if FAILS else 0)
