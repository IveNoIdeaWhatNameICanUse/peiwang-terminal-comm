# [AGENT_CHANGE_BEGIN] 2026-09-07 104-MVP-Tk回退壳
"""当 pywebview 不可用时的 tkinter 桌面回退壳。"""
from __future__ import annotations

import queue
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.api import ApiBridge


def run_tk_shell(api: "ApiBridge", root: Path) -> None:
    win = tk.Tk()
    win.title("配网终端通讯 · 104模拟主站")
    win.geometry("1100x780")

    event_q: queue.Queue = queue.Queue()
    api.set_ui_push(lambda ev: event_q.put(ev))

    frm = ttk.Frame(win, padding=10)
    frm.pack(fill=tk.BOTH, expand=True)

    conn = ttk.LabelFrame(frm, text="连接配置", padding=8)
    conn.pack(fill=tk.X)

    remote_ip = tk.StringVar(value="127.0.0.1")
    remote_port = tk.StringVar(value="2404")
    local_ip = tk.StringVar(value="")
    local_port = tk.StringVar(value="")
    ca = tk.StringVar(value="1")
    oa = tk.StringVar(value="0")
    status = tk.StringVar(value="未连接")

    ttk.Label(conn, text="从站 IP").grid(row=0, column=0, sticky="w")
    ttk.Entry(conn, textvariable=remote_ip, width=16).grid(row=0, column=1, padx=4)
    ttk.Label(conn, text="端口").grid(row=0, column=2)
    ttk.Entry(conn, textvariable=remote_port, width=8).grid(row=0, column=3, padx=4)

    ttk.Label(conn, text="本地网卡/IP").grid(row=1, column=0, sticky="w")
    local_combo = ttk.Combobox(conn, textvariable=local_ip, width=28)
    local_combo.grid(row=1, column=1, columnspan=2, sticky="we", padx=4)
    ttk.Label(conn, text="本地端口").grid(row=1, column=3)
    ttk.Entry(conn, textvariable=local_port, width=8).grid(row=1, column=4, padx=4)

    ttk.Label(conn, text="CA").grid(row=2, column=0, sticky="w")
    ttk.Entry(conn, textvariable=ca, width=8).grid(row=2, column=1, sticky="w", padx=4)
    ttk.Label(conn, text="OA").grid(row=2, column=2)
    ttk.Entry(conn, textvariable=oa, width=8).grid(row=2, column=3, sticky="w", padx=4)
    ttk.Label(conn, textvariable=status).grid(row=2, column=4, sticky="e")

    def refresh_nics():
        nics = api.get_nics()
        values = [""] + [n["ip"] for n in nics]
        local_combo["values"] = values
        if local_ip.get() not in values:
            local_ip.set("")

    def do_connect():
        lp = local_port.get().strip()
        r = api.connect(
            {
                "remote_ip": remote_ip.get().strip(),
                "remote_port": int(remote_port.get() or 2404),
                "local_ip": local_ip.get().strip(),
                "local_port": int(lp) if lp else 0,
                "common_address": int(ca.get() or 1),
                "originator": int(oa.get() or 0),
            }
        )
        if not r.get("ok"):
            messagebox.showerror("连接失败", f"[{r.get('code', '')}] {r.get('error')}")
            status.set("连接失败")
        else:
            status.set("已连接")

    def do_disconnect():
        api.disconnect()
        status.set("未连接")

    def do_save():
        r = api.save_project(
            {
                **api.get_project(),
                "remote_ip": remote_ip.get().strip(),
                "remote_port": int(remote_port.get() or 2404),
                "local_ip": local_ip.get().strip(),
                "local_port": int(local_port.get() or 0) if local_port.get().strip() else 0,
                "common_address": int(ca.get() or 1),
                "originator": int(oa.get() or 0),
            },
            "",
        )
        if r.get("ok"):
            messagebox.showinfo("保存", r.get("path", ""))
        else:
            messagebox.showerror("保存失败", r.get("error", ""))

    btns = ttk.Frame(conn)
    btns.grid(row=3, column=0, columnspan=5, sticky="w", pady=6)
    ttk.Button(btns, text="连接", command=do_connect).pack(side=tk.LEFT, padx=3)
    ttk.Button(btns, text="断开", command=do_disconnect).pack(side=tk.LEFT, padx=3)
    ttk.Button(btns, text="刷新网卡", command=refresh_nics).pack(side=tk.LEFT, padx=3)
    ttk.Button(btns, text="保存工程", command=do_save).pack(side=tk.LEFT, padx=3)

    ops = ttk.LabelFrame(frm, text="四遥操作", padding=8)
    ops.pack(fill=tk.X, pady=8)

    def _ok(r):
        if not r.get("ok"):
            messagebox.showerror("失败", f"[{r.get('code', '')}] {r.get('error')}")

    ttk.Button(ops, text="总召唤", command=lambda: _ok(api.general_interrogation())).pack(side=tk.LEFT, padx=3)
    ttk.Button(ops, text="时钟同步", command=lambda: _ok(api.clock_sync())).pack(side=tk.LEFT, padx=3)

    cmd_ioa = tk.StringVar(value="1")
    cmd_kind = tk.StringVar(value="sc")
    cmd_val = tk.StringVar(value="1")
    cmd_sel = tk.BooleanVar(value=True)
    ttk.Label(ops, text="IOA").pack(side=tk.LEFT, padx=(12, 2))
    ttk.Entry(ops, textvariable=cmd_ioa, width=8).pack(side=tk.LEFT)
    ttk.Combobox(
        ops, textvariable=cmd_kind, values=["sc", "dc", "se_nc", "se_na"], width=8, state="readonly"
    ).pack(side=tk.LEFT, padx=4)
    ttk.Entry(ops, textvariable=cmd_val, width=8).pack(side=tk.LEFT, padx=4)
    ttk.Checkbutton(ops, text="预置SBO", variable=cmd_sel).pack(side=tk.LEFT)

    def send_cmd(select_force=None):
        select = cmd_sel.get() if select_force is None else select_force
        ioa = int(cmd_ioa.get())
        kind = cmd_kind.get()
        raw = cmd_val.get()
        if kind == "sc":
            r = api.single_command(ioa, raw == "1", select)
        elif kind == "dc":
            r = api.double_command(ioa, int(raw), select)
        elif kind == "se_nc":
            r = api.setpoint_float(ioa, float(raw), select)
        else:
            r = api.setpoint_normalized(ioa, float(raw), select)
        _ok(r)

    ttk.Button(ops, text="下发命令", command=send_cmd).pack(side=tk.LEFT, padx=6)
    ttk.Button(ops, text="仅执行", command=lambda: send_cmd(False)).pack(side=tk.LEFT)

    pts = ttk.LabelFrame(frm, text="点表 / 实时数据", padding=8)
    pts.pack(fill=tk.BOTH, expand=True)
    tree = ttk.Treeview(pts, columns=("ioa", "name", "type", "value", "q"), show="headings", height=10)
    for c, t, w in [
        ("ioa", "IOA", 70),
        ("name", "名称", 140),
        ("type", "类型", 70),
        ("value", "值", 120),
        ("q", "品质", 60),
    ]:
        tree.heading(c, text=t)
        tree.column(c, width=w, anchor=tk.W)
    tree.pack(fill=tk.BOTH, expand=True)

    def reload_points():
        tree.delete(*tree.get_children())
        for p in api.get_project().get("points", []):
            tree.insert(
                "",
                tk.END,
                values=(p["ioa"], p.get("name"), p.get("type_id"), p.get("value"), p.get("quality")),
            )

    addf = ttk.Frame(pts)
    addf.pack(fill=tk.X, pady=4)
    n_ioa = tk.StringVar(value="1")
    n_name = tk.StringVar(value="")
    n_type = tk.StringVar(value="1")
    ttk.Entry(addf, textvariable=n_ioa, width=8).pack(side=tk.LEFT, padx=2)
    ttk.Entry(addf, textvariable=n_name, width=16).pack(side=tk.LEFT, padx=2)
    ttk.Combobox(addf, textvariable=n_type, values=["1", "13", "45", "50"], width=6, state="readonly").pack(
        side=tk.LEFT
    )

    def add_point():
        tid = int(n_type.get())
        cat = {1: "遥信", 13: "遥测", 45: "遥控", 50: "遥调"}.get(tid, "")
        api.upsert_point({"ioa": int(n_ioa.get()), "type_id": tid, "name": n_name.get(), "category": cat})
        reload_points()

    ttk.Button(addf, text="添加点", command=add_point).pack(side=tk.LEFT, padx=4)

    logf = ttk.LabelFrame(frm, text="报文监视", padding=8)
    logf.pack(fill=tk.BOTH, expand=True, pady=8)
    log = tk.Text(logf, height=12, bg="#0b1220", fg="#dbeafe", font=("Consolas", 10))
    log.pack(fill=tk.BOTH, expand=True)

    def poll_events():
        try:
            while True:
                ev = event_q.get_nowait()
                if ev.get("type") == "connection":
                    status.set(ev.get("message") or ev.get("state"))
                elif ev.get("type") == "frame":
                    log.insert(tk.END, f"{ev.get('direction')} {ev.get('note')}\n{ev.get('hex')}\n\n")
                    log.see(tk.END)
                elif ev.get("type") == "points":
                    reload_points()
        except queue.Empty:
            pass
        win.after(200, poll_events)

    proj = api.get_project()
    remote_ip.set(proj.get("remote_ip") or "127.0.0.1")
    remote_port.set(str(proj.get("remote_port") or 2404))
    local_ip.set(proj.get("local_ip") or "")
    local_port.set(str(proj.get("local_port") or "") if proj.get("local_port") else "")
    ca.set(str(proj.get("common_address") or 1))
    oa.set(str(proj.get("originator") or 0))
    refresh_nics()
    reload_points()
    poll_events()
    win.mainloop()


# [AGENT_CHANGE_END] 2026-09-07 104-MVP-Tk回退壳
