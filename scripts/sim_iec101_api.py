# -*- coding: utf-8 -*-
"""API-level integration test for IEC 101 sessions (no hardware needed).

Drives ApiBridge exactly like the UI does: create/update session with
protocol=101, connect over a fake serial port, general interrogation,
then checks the event log and the four-telemetry statistics.

Usage:  python scripts/sim_iec101_api.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import sim_iec101 as sim                                  # noqa: E402
from core.api import ApiBridge                            # noqa: E402

FAILS = []


def check(cond, label, extra=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def install_fake_serial(slave):
    fake = types.ModuleType("serial")
    fake.Serial = lambda **kw: sim.LoopSerial(slave, **kw)
    tools = types.ModuleType("serial.tools")
    lp = types.ModuleType("serial.tools.list_ports")

    class _Port:
        device = "COM_TEST"
        description = "Fake loop port"

    lp.comports = lambda: [_Port()]
    tools.list_ports = lp
    fake.tools = tools
    sys.modules["serial"] = fake
    sys.modules["serial.tools"] = tools
    sys.modules["serial.tools.list_ports"] = lp


LINK_START = "\u94fe\u8def\u542f\u52a8"   # lian-lu qi-dong
LINK_STOP = "\u94fe\u8def\u505c\u6b62"    # lian-lu ting-zhi


def main():
    slave = sim.SlaveSim(balanced=False, acd=True)
    install_fake_serial(slave)

    tmp = tempfile.mkdtemp(prefix="pw101_")
    api = ApiBridge(Path(tmp))
    sid = api.store.config.active_session().id
    print("session:", sid)

    ports = api.list_serial_ports()
    check(any(p["port"] == "COM_TEST" for p in ports), "serial port enumeration", str(ports))

    r = api.update_session(sid, {
        "protocol": "101", "serial_port": "COM_TEST", "baudrate": 9600,
        "serial_parity": "N", "stopbits": 1, "link_addr": 1, "balanced": False,
        "poll_period": 0.2, "ioa_size_101": 2, "common_address": 1,
    })
    check(r.get("ok", True), "update_session -> protocol 101")

    r = api.connect()
    check(r.get("ok") is True, "connect() over 101", str(r))
    check(api.is_connected(sid), "is_connected() true")

    check(wait_for(lambda: sim.link.FC_RESET_LINK in slave.fc_rx, 2.0), "slave saw reset link (FC=0)")
    # 非平衡：启动拉初始化结束/总召后延时获得期间暂停 2 级；完成后才周期召 2 级
    check(wait_for(lambda: sim.link.FC_REQ_LEVEL2 in slave.fc_rx, 12.0), "slave saw level-2 poll after startup")

    def points():
        return (api.get_project().get("points_by_session") or {}).get(sid, [])

    # [AGENT_CHANGE_BEGIN] 2026-09-12 禁止报文自动加点表
    # 点须预先进表；报文只刷新已有点，不再自动创建 IOA
    api.upsert_point({"ioa": 1001, "type_id": 13, "name": "YC-1001", "category": "遥测"}, sid=sid)
    api.upsert_point({"ioa": 11, "type_id": 1, "name": "YX-11", "category": "遥信"}, sid=sid)
    api.upsert_point({"ioa": 5, "type_id": 30, "name": "YX-SOE-5", "category": "遥信"}, sid=sid)

    slave.pending.append(sim.SPONT_ME)
    slave.pending.append(sim.mk_asdu(1, 3, 1, sim.sp_objects([(11, True)]), count=1))

    check(wait_for(lambda: any(p.get("ioa") == 1001 and p.get("value") is not None for p in points()), 4.0),
          "telemetry IOA 1001 value updated (existing point)")
    check(wait_for(lambda: any(p.get("ioa") == 11 and p.get("value") is not None for p in points()), 4.0),
          "single point IOA 11 value updated (existing point)")
    # 未建点的 IOA 不应被报文偷偷加入
    before_n = len(points())
    slave.pending.append(sim.mk_asdu(13, 3, 1, sim.me_nc_objects([(9999, 1.0)])))
    time.sleep(1.0)
    check(len(points()) == before_n and not any(p.get("ioa") == 9999 for p in points()),
          "unknown IOA 9999 not auto-added to point table")
    # [AGENT_CHANGE_END] 2026-09-12 禁止报文自动加点表

    ev = api.get_events(sid).get("events", [])
    check(any(e.get("kind") == "sys" and LINK_START in str(e.get("content", "")) for e in ev),
          "link start recorded as system event", str(ev[:3])[:160])

    # general interrogation closes the init window at activation termination
    api.general_interrogation()
    check(wait_for(lambda: slave.interrogated, 3.0), "slave received general interrogation")

    # spontaneous changes after the init window must be recorded
    slave.pending.append(sim.mk_asdu(13, 3, 1, sim.me_nc_objects([(1001, 45.6)])))
    slave.pending.append(sim.mk_asdu(1, 3, 1, sim.sp_objects([(11, False)]), count=1))
    slave.pending.append(sim.SOE_SP)

    check(wait_for(lambda: any(e.get("kind") == "cos" for e in api.get_events(sid).get("events", [])), 5.0),
          "COS event recorded (spontaneous, after init window)")
    check(wait_for(lambda: any(e.get("kind") == "soe" for e in api.get_events(sid).get("events", [])), 5.0),
          "SOE event recorded (M_SP_TB_1)")

    st = api.get_stats(sid)
    rows = st.get("rows") or []
    check(st.get("ok") is True and len(rows) >= 2, "get_stats() returned rows", f"rows={len(rows)}")
    check(any(int(r.get("change") or 0) > 0 or int(r.get("soe") or 0) > 0
              or int(r.get("dead") or 0) > 0 for r in rows),
          "statistics counted for changed points", str(rows)[:200])

    # direct execute (no select)
    r = api.single_command(11, True, False)
    check(r.get("ok") is True, "single command executed", str(r))
    check(wait_for(lambda: any(a.type_id == 45 for a in slave.rx_asdus), 3.0), "slave received C_SC_NA_1")

    # clock sync
    r = api.clock_sync()
    check(r.get("ok") is True, "clock sync ok", str(r))
    check(wait_for(lambda: any(a.type_id == 103 for a in slave.rx_asdus), 3.0), "slave received C_CS_NA_1")

    # read setting values (batch read, 101 uses the same ASDUs)
    r = api.read_points([1001], area=1)
    print("  read_points ->", str(r)[:120])

    r = api.disconnect()
    check(r.get("ok") is True and not api.is_connected(sid), "disconnect ok")
    ev = api.get_events(sid).get("events", [])
    check(any(e.get("kind") == "sys" and LINK_STOP in str(e.get("content", "")) for e in ev),
          "link stop recorded", str([e.get("content") for e in ev])[:160])


if __name__ == "__main__":
    main()
    print("\n%s (%d failure%s)" % ("ALL PASSED" if not FAILS else "FAILURES: " + ", ".join(FAILS),
                                   len(FAILS), "" if len(FAILS) == 1 else "s"))
    sys.exit(1 if FAILS else 0)
