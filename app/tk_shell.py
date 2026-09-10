# [AGENT_CHANGE_BEGIN] 2026-09-07 多主站Tk界面
"""tkinter 桌面壳：多主站会话 + 四遥。"""
from __future__ import annotations

import queue
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from core.project import category_for_type

if TYPE_CHECKING:
    from core.api import ApiBridge


def run_tk_shell(api: "ApiBridge", root: Path) -> None:
    win = tk.Tk()
    win.title("配网终端通讯 · 104模拟主站（多主站）")
    win.geometry("1180x820")

    event_q: queue.Queue = queue.Queue()
    api.set_ui_push(lambda ev: event_q.put(ev))

    frm = ttk.Frame(win, padding=10)
    frm.pack(fill=tk.BOTH, expand=True)

    # ---- 会话列表 ----
    sess_box = ttk.LabelFrame(frm, text="主站会话（可多开）", padding=8)
    sess_box.pack(fill=tk.X)

    session_list = tk.Listbox(sess_box, height=4, exportselection=False)
    session_list.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

    name_var = tk.StringVar(value="主站1")
    remote_ip = tk.StringVar(value="127.0.0.1")
    remote_port = tk.StringVar(value="2404")
    local_ip = tk.StringVar(value="")
    local_port = tk.StringVar(value="")
    ca = tk.StringVar(value="1")
    oa = tk.StringVar(value="0")
    status = tk.StringVar(value="未连接")
    _session_ids: list[str] = []
    _loading = {"flag": False}

    right = ttk.Frame(sess_box)
    right.pack(side=tk.LEFT, fill=tk.Y)

    def current_sid() -> str:
        sel = session_list.curselection()
        if not sel:
            return ""
        idx = int(sel[0])
        return _session_ids[idx] if 0 <= idx < len(_session_ids) else ""

    def refresh_session_list(select_id: str = ""):
        _loading["flag"] = True
        data = api.list_sessions()
        sessions = data.get("sessions") or []
        connected = data.get("session_connected") or {}
        active = data.get("active_session_id") or ""
        session_list.delete(0, tk.END)
        _session_ids.clear()
        select_idx = 0
        for i, s in enumerate(sessions):
            sid = s["id"]
            _session_ids.append(sid)
            mark = "●" if connected.get(sid) else "○"
            act = " [当前]" if sid == active else ""
            session_list.insert(tk.END, f"{mark} {s.get('name', sid)}  {s.get('remote_ip')}:{s.get('remote_port')}{act}")
            if select_id and sid == select_id:
                select_idx = i
            elif not select_id and sid == active:
                select_idx = i
        if _session_ids:
            session_list.selection_clear(0, tk.END)
            session_list.selection_set(select_idx)
            session_list.activate(select_idx)
            load_selected_into_form()
        _loading["flag"] = False
        ensure_station_tabs()

    def load_selected_into_form():
        sid = current_sid()
        if not sid:
            return
        for s in api.list_sessions().get("sessions") or []:
            if s["id"] != sid:
                continue
            name_var.set(s.get("name") or "")
            remote_ip.set(s.get("remote_ip") or "")
            remote_port.set(str(s.get("remote_port") or 2404))
            local_ip.set(s.get("local_ip") or "")
            lp = s.get("local_port") or 0
            local_port.set(str(lp) if lp else "")
            ca.set(str(s.get("common_address") or 1))
            oa.set(str(s.get("originator") or 0))
            connected = (api.list_sessions().get("session_connected") or {}).get(sid)
            status.set("已连接" if connected else "未连接")
            break

    def apply_form_to_session():
        sid = current_sid()
        if not sid:
            return
        lp = local_port.get().strip()
        api.update_session(
            sid,
            {
                "name": name_var.get().strip() or "主站",
                "remote_ip": remote_ip.get().strip(),
                "remote_port": int(remote_port.get() or 2404),
                "local_ip": local_ip.get().strip(),
                "local_port": int(lp) if lp else 0,
                "common_address": int(ca.get() or 1),
                "originator": int(oa.get() or 0),
            },
        )
        api.set_active_session(sid)

    def on_select(_evt=None):
        if _loading["flag"]:
            return
        sid = current_sid()
        if sid:
            api.set_active_session(sid)
            load_selected_into_form()

    session_list.bind("<<ListboxSelect>>", on_select)

    def add_session():
        r = api.create_session({"name": f"主站{len(_session_ids)+1}", "auto_local_port": True})
        if not r.get("ok"):
            messagebox.showerror("失败", r.get("error", ""))
            return
        refresh_session_list(r["session"]["id"])

    def del_session():
        sid = current_sid()
        if not sid:
            return
        r = api.delete_session(sid)
        if not r.get("ok"):
            messagebox.showerror("失败", r.get("error", ""))
            return
        remove_station_tab(sid)
        refresh_session_list()

    def import_config():
        """导入工程配置文件（主站会话/点表/参数/规约版本整体替换）。"""
        path = filedialog.askopenfilename(
            title="导入工程配置",
            filetypes=[("工程配置", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        r = api.load_project(path)
        if not r.get("ok"):
            messagebox.showerror("导入失败", r.get("error", ""))
            return
        # 规约版本：导入后同步 UI 下拉、提示与国网专属控件
        v = str((api.get_project() or {}).get("protocol_variant") or "广西")
        variant_var.set(v)
        try:
            on_variant_change()
        except Exception:
            pass
        for sid in list(station_views.keys()):
            remove_station_tab(sid)
        refresh_session_list()
        messagebox.showinfo("导入成功", f"已导入配置：{path}")

    ttk.Button(right, text="新建主站", command=add_session).pack(fill=tk.X, pady=2)
    ttk.Button(right, text="删除主站", command=del_session).pack(fill=tk.X, pady=2)
    ttk.Button(right, text="导入配置", command=import_config).pack(fill=tk.X, pady=2)

    # ---- 连接参数 ----
    conn = ttk.LabelFrame(frm, text="当前会话连接参数", padding=8)
    conn.pack(fill=tk.X, pady=6)

    ttk.Label(conn, text="名称").grid(row=0, column=0, sticky="w")
    ttk.Entry(conn, textvariable=name_var, width=12).grid(row=0, column=1, padx=4)
    ttk.Label(conn, text="从站 IP").grid(row=0, column=2, sticky="w")
    ttk.Entry(conn, textvariable=remote_ip, width=16).grid(row=0, column=3, padx=4)
    ttk.Label(conn, text="端口").grid(row=0, column=4)
    ttk.Entry(conn, textvariable=remote_port, width=8).grid(row=0, column=5, padx=4)

    ttk.Label(conn, text="本地网卡/IP").grid(row=1, column=0, sticky="w")
    local_combo = ttk.Combobox(conn, textvariable=local_ip, width=28)
    local_combo.grid(row=1, column=1, columnspan=2, sticky="we", padx=4)
    ttk.Label(conn, text="本地端口").grid(row=1, column=3)
    ttk.Entry(conn, textvariable=local_port, width=8).grid(row=1, column=4, padx=4)
    ttk.Label(conn, text="(多主站勿共用同一本地端口)", foreground="#666").grid(row=1, column=5, sticky="w")

    ttk.Label(conn, text="公共地址(CA)").grid(row=2, column=0, sticky="w")
    ttk.Entry(conn, textvariable=ca, width=8).grid(row=2, column=1, sticky="w", padx=4)
    ttk.Label(conn, text="起源地址(OA)").grid(row=2, column=2)
    ttk.Entry(conn, textvariable=oa, width=8).grid(row=2, column=3, sticky="w", padx=4)
    ttk.Label(conn, textvariable=status).grid(row=2, column=4, columnspan=2, sticky="e")

    def refresh_nics():
        nics = api.get_nics()
        values = [""] + [n["ip"] for n in nics]
        local_combo["values"] = values

    def do_connect():
        apply_form_to_session()
        r = api.connect()
        refresh_session_list(current_sid())
        if not r.get("ok"):
            messagebox.showerror("连接失败", f"[{r.get('code', '')}] {r.get('error')}")
            status.set("连接失败")
        else:
            status.set("已连接")

    def do_disconnect():
        sid = current_sid()
        api.disconnect(sid)
        refresh_session_list(sid)
        status.set("未连接")

    def do_save():
        apply_form_to_session()
        r = api.save_project(api.get_project(), "")
        if r.get("ok"):
            messagebox.showinfo("保存", r.get("path", ""))
        else:
            messagebox.showerror("保存失败", r.get("error", ""))

    def open_params_dialog():
        # 104 参数设置（KW-2200 风格），保存到当前主站，重新连接后生效
        sid = current_sid()
        if not sid:
            messagebox.showwarning("提示", "请先选择主站")
            return
        sess = None
        for x in api.list_sessions().get("sessions") or []:
            if x["id"] == sid:
                sess = x
                break
        if not sess:
            return

        dlg = tk.Toplevel(win)
        dlg.title(f"104 参数设置 - {sess.get('name')}")
        dlg.transient(win)
        dlg.grab_set()
        # 居中于主窗口（避免出现在其他屏幕默认位置）
        win.update_idletasks()
        _x = win.winfo_rootx() + max(0, (win.winfo_width() - 360) // 2)
        _y = win.winfo_rooty() + max(0, (win.winfo_height() - 520) // 2)
        dlg.geometry(f"360x520+{_x}+{_y}")

        def gv(key, default):
            return tk.StringVar(value=str(sess.get(key, default)))

        rows = [
            ("T0 连接超时(秒)", "t0", 30.0, float),
            ("T1 发送/测试超时(秒)", "t1", 15.0, float),
            ("T2 确认超时(秒)", "t2", 10.0, float),
            ("T3 空闲测试超时(秒)", "t3", 20.0, float),
            ("K 未确认I帧上限", "k", 12, int),
            ("W 触发S确认帧数", "w", 8, int),
            ("总召唤周期(秒,0=禁用)", "gi_period", 600, int),
            ("校时周期(分,0=禁用)", "clock_period", 30, int),
            ("召唤度周期(秒,0=禁用)", "call_period", 0, int),
            ("链路应答超时(秒)", "link_ack_timeout", 10.0, float),
            ("远控命令超时(秒)", "cmd_timeout", 30.0, float),
            ("发送延时(毫秒)", "tx_delay_ms", 0.0, float),
        ]
        vars_map = {}
        ttk.Label(dlg, text="104控制 / 周期 / 超时", font=("", 10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(6, 2)
        )
        r = 1
        for label, key, default, conv in rows:
            ttk.Label(dlg, text=label).grid(row=r, column=0, sticky="w", padx=6, pady=1)
            v = gv(key, default)
            vars_map[key] = (v, conv)
            ttk.Entry(dlg, textvariable=v, width=10).grid(row=r, column=1, padx=6, pady=1)
            r += 1

        ttk.Label(dlg, text="地址长度", font=("", 10, "bold")).grid(
            row=r, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 2)
        )
        r += 1
        cot_var = gv("cot_size", 2)
        ca_var = gv("ca_size", 2)
        ioa_var = gv("ioa_size", 3)
        ttk.Label(dlg, text="传送原因长度(字节)").grid(row=r, column=0, sticky="w", padx=6, pady=1)
        ttk.Combobox(dlg, textvariable=cot_var, values=["1", "2"], width=6, state="readonly").grid(
            row=r, column=1, padx=6, pady=1
        )
        r += 1
        ttk.Label(dlg, text="公共地址长度(字节)").grid(row=r, column=0, sticky="w", padx=6, pady=1)
        ttk.Combobox(dlg, textvariable=ca_var, values=["1", "2"], width=6, state="readonly").grid(
            row=r, column=1, padx=6, pady=1
        )
        r += 1
        ttk.Label(dlg, text="信息体地址长度(字节)").grid(row=r, column=0, sticky="w", padx=6, pady=1)
        ttk.Combobox(dlg, textvariable=ioa_var, values=["2", "3"], width=6, state="readonly").grid(
            row=r, column=1, padx=6, pady=1
        )
        r += 1
        ttk.Label(dlg, text="读命令(定值召唤)COT").grid(row=r, column=0, sticky="w", padx=6, pady=1)
        read_cot_var = gv("read_cot", 6)
        ttk.Combobox(dlg, textvariable=read_cot_var, values=["5", "6"], width=6, state="readonly").grid(
            row=r, column=1, padx=6, pady=1
        )
        r += 1
        recon_var = tk.BooleanVar(value=bool(sess.get("auto_reconnect", True)))
        ttk.Checkbutton(dlg, text="超时断线自动重连", variable=recon_var).grid(
            row=r, column=0, columnspan=2, sticky="w", padx=6, pady=6
        )
        r += 1

        def save_params():
            try:
                data = {}
                for key, (v, conv) in vars_map.items():
                    data[key] = conv(v.get() or 0)
                data["cot_size"] = int(cot_var.get())
                data["ca_size"] = int(ca_var.get())
                data["ioa_size"] = int(ioa_var.get())
                data["read_cot"] = int(read_cot_var.get())
                data["auto_reconnect"] = bool(recon_var.get())
                api.update_session(sid, data)
            except ValueError as e:
                messagebox.showerror("参数错误", str(e), parent=dlg)
                return
            dlg.destroy()
            messagebox.showinfo("已保存", "参数已保存到当前主站，重新连接后生效", parent=win)

        ttk.Frame(dlg).grid(row=r, column=0, columnspan=2, pady=2)
        bt = ttk.Frame(dlg)
        bt.grid(row=r, column=0, columnspan=2, pady=4)
        ttk.Button(bt, text="保存", command=save_params).pack(side=tk.LEFT, padx=6)
        ttk.Button(bt, text="取消", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    btns = ttk.Frame(conn)
    btns.grid(row=3, column=0, columnspan=6, sticky="w", pady=6)
    ttk.Button(btns, text="连接", command=do_connect).pack(side=tk.LEFT, padx=3)
    ttk.Button(btns, text="断开", command=do_disconnect).pack(side=tk.LEFT, padx=3)
    ttk.Button(btns, text="全部断开", command=lambda: (api.disconnect_all(), refresh_session_list(current_sid()), status.set("未连接"))).pack(
        side=tk.LEFT, padx=3
    )
    ttk.Button(btns, text="刷新网卡", command=refresh_nics).pack(side=tk.LEFT, padx=3)
    ttk.Button(btns, text="保存工程", command=do_save).pack(side=tk.LEFT, padx=3)
    ttk.Button(btns, text="104参数设置", command=open_params_dialog).pack(side=tk.LEFT, padx=3)

    # ---- 四遥 ----
    ops = ttk.LabelFrame(frm, text="四遥操作（作用于当前会话）", padding=8)
    ops.pack(fill=tk.X, pady=4)

    def _ok(r):
        if not r.get("ok"):
            messagebox.showerror("失败", f"[{r.get('code', '')}] {r.get('error')}")

    VARIANT_HINTS = {
        "广西": "点表分段：遥信1H/遥测4001H/遥调5001H/遥控6001H（以现场发码表为准）",
        "南网": "参数整定：召唤→预置→固化/撤销（SBO 两段式）",
        "国网": "支持参数全召唤/分组召唤；总召唤应答 SQ=1",
    }
    variant_var = tk.StringVar(value=str(api.get_project().get("protocol_variant") or "广西"))

    op_r1 = ttk.Frame(ops)
    op_r1.pack(fill=tk.X)
    ttk.Label(op_r1, text="规约版本").pack(side=tk.LEFT, padx=(0, 3))
    variant_combo = ttk.Combobox(
        op_r1, textvariable=variant_var, values=["广西", "南网", "国网"], width=6, state="readonly"
    )
    variant_combo.pack(side=tk.LEFT, padx=(0, 10))
    variant_hint = ttk.Label(op_r1, text=VARIANT_HINTS.get(variant_var.get(), ""), foreground="#666")
    variant_hint.pack(side=tk.LEFT, padx=(0, 10))
    ttk.Button(op_r1, text="总召唤", command=lambda: (apply_form_to_session(), _ok(api.general_interrogation()))).pack(
        side=tk.LEFT, padx=3
    )
    ttk.Button(op_r1, text="时钟同步", command=lambda: (apply_form_to_session(), _ok(api.clock_sync()))).pack(
        side=tk.LEFT, padx=3
    )

    # 遥控操作：不再在此处手输 IOA，改为在点表左键选中、双击弹出操作窗口（选择/执行/撤销 + 分/合）

    # ---- 定值整定（召唤/预置/激活/撤销）：IOA 与修改值从“遥调”点表取 ----
    op_r3 = ttk.Frame(ops)
    op_r3.pack(fill=tk.X, pady=2)
    ttk.Label(op_r3, text="定值整定").pack(
        side=tk.LEFT, padx=(0, 6)
    )
    batch_var = tk.StringVar(value="10")
    ttk.Label(op_r3, text="单帧定值个数").pack(side=tk.LEFT, padx=(0, 3))
    ttk.Entry(op_r3, textvariable=batch_var, width=5).pack(side=tk.LEFT, padx=(0, 6))

    def _current_batch() -> int:
        try:
            return max(1, min(int(batch_var.get()), 127))
        except (TypeError, ValueError):
            return 10

    def _sp_items() -> list:
        """返回 [(ioa, 值或None)]：遥调点表选中行 + 修改值列。"""
        sid = current_station_sid()
        view = station_views.get(sid)
        if not view:
            return []
        tree = view["trees"].get("遥调")
        if not tree:
            return []
        out = []
        for iid in tree.selection():
            try:
                ioa = int(str(iid)[1:])
            except ValueError:
                continue
            raw = (view.get("modvals") or {}).get(ioa, "")
            try:
                val = float(raw)
            except (TypeError, ValueError):
                val = None
            out.append((ioa, val))
        return out

    def read_setpoint_selected():
        apply_form_to_session()
        ioas = selected_ioas("遥调")
        if not ioas:
            messagebox.showwarning("提示", "请先在“遥调”点表中选中定值点")
            return
        _ok(api.read_points(ioas, area=_current_area(), batch=_current_batch()))

    def preset_setpoint():
        apply_form_to_session()
        items = _sp_items()
        if not items:
            messagebox.showwarning("提示", "请先在“遥调”点表中选中定值点")
            return
        missing = [ioa for ioa, v in items if v is None]
        if missing:
            messagebox.showwarning("提示", f"请双击“修改值”列填写数值（IOA {missing}）")
            return
        for ioa, v in items:
            _ok(api.preset_setpoint(ioa, v, True, area=_current_area()))

    def exec_setpoint():
        apply_form_to_session()
        # 国网：固化 = 203 VSQ=0 + 区号 + PI(S/E=0)，无需选中点/修改值
        if variant_var.get() == "国网":
            _ok(api.fix_setpoint(area=_current_area()))
            return
        items = _sp_items()
        if not items:
            messagebox.showwarning("提示", "请先在“遥调”点表中选中定值点")
            return
        missing = [ioa for ioa, v in items if v is None]
        if missing:
            messagebox.showwarning("提示", f"请双击“修改值”列填写数值（IOA {missing}）")
            return
        for ioa, v in items:
            _ok(api.preset_setpoint(ioa, v, False, area=_current_area()))

    def undo_setpoint():
        apply_form_to_session()
        # 国网：撤销 = 203 VSQ=0 + 区号 + PI(CR=1)，无信息体地址，无需选中点
        if variant_var.get() == "国网":
            _ok(api.cancel_setpoint(0, area=_current_area()))
            return
        items = _sp_items()
        if not items:
            messagebox.showwarning("提示", "请先在“遥调”点表中选中定值点")
            return
        for ioa, _v in items:
            _ok(api.cancel_setpoint(ioa, area=_current_area()))

    ttk.Button(op_r3, text="召唤选中", command=read_setpoint_selected).pack(side=tk.LEFT, padx=2)
    ttk.Button(op_r3, text="预置", command=preset_setpoint).pack(side=tk.LEFT, padx=2)
    ttk.Button(op_r3, text="激活", command=exec_setpoint).pack(side=tk.LEFT, padx=2)
    ttk.Button(op_r3, text="撤销", command=undo_setpoint).pack(side=tk.LEFT, padx=2)

    def full_read_setpoints():
        apply_form_to_session()
        sid = current_station_sid()
        batch = _current_batch()
        try:
            api.update_session(sid, {"setpoint_batch": batch})
        except Exception:
            pass
        _ok(api.read_all_setpoints(sid, area=_current_area(), batch=batch))
        reload_points()

    btn_full_read = ttk.Button(op_r3, text="参数全召唤", command=full_read_setpoints)

    # 国网细则：定值区（200 切换 / 201 读区号）
    area_var = tk.StringVar(value="1")
    area_lbl = ttk.Label(op_r3, text="区号")
    area_entry = ttk.Entry(op_r3, textvariable=area_var, width=4)

    def _current_area() -> int:
        try:
            raw = area_var.get().strip()
            return int(raw) if raw.isdigit() else 1
        except Exception:
            return 1

    def read_setting_area():
        apply_form_to_session()
        _ok(api.read_setting_area())

    def switch_setting_area():
        apply_form_to_session()
        raw = area_var.get().strip()
        if not raw.isdigit():
            messagebox.showwarning("提示", "定值区号无效")
            return
        _ok(api.switch_setting_area(int(raw)))

    btn_read_area = ttk.Button(op_r3, text="读定值区(201)", command=read_setting_area)
    btn_switch_area = ttk.Button(op_r3, text="切换定值区(200)", command=switch_setting_area)

    def _show_area_controls(show: bool):
        widgets = (area_lbl, area_entry, btn_read_area, btn_switch_area)
        if show:
            for w in widgets:
                w.pack(side=tk.LEFT, padx=2)
        else:
            for w in widgets:
                try:
                    w.pack_forget()
                except tk.TclError:
                    pass

    _show_area_controls(variant_var.get() == "国网")
    if variant_var.get() == "国网":
        btn_full_read.pack(side=tk.LEFT, padx=2)

    def on_variant_change(_evt=None):
        v = variant_var.get()
        api.set_protocol_variant(v)
        variant_hint.config(text=VARIANT_HINTS.get(v, ""))
        if v == "国网":
            btn_full_read.pack(side=tk.LEFT, padx=2)
        else:
            try:
                btn_full_read.pack_forget()
            except tk.TclError:
                pass
        _show_area_controls(v == "国网")

    variant_combo.bind("<<ComboboxSelected>>", on_variant_change)

    # ---- 主选项卡：每个主站一个（左侧菜单：四遥状态 / 报文监视）----
    main_nb = ttk.Notebook(frm)
    main_nb.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

    CATEGORIES = ["遥信", "遥测", "遥控", "遥调"]
    TYPE_LABELS = {
        1: "单点遥信", 3: "双点遥信", 5: "步位置", 7: "位串",
        9: "归一化遥测", 11: "标度化遥测", 13: "浮点遥测", 15: "累计量",
        30: "单点遥信(带时标)", 31: "双点遥信(带时标)", 36: "浮点遥测(带时标)",
        45: "单点遥控", 46: "双点遥控", 47: "步调节",
        48: "归一化设点", 49: "标度化设点", 50: "浮点设点",
        100: "总召唤", 101: "计数量召唤", 103: "时钟同步",
    }
    TYPE_KINDS = {
        "单点遥信(1)": 1,
        "双点遥信(3)": 3,
        "单点遥信带时标(30)": 30,
        "双点遥信带时标(31)": 31,
        "遥测(13)": 13,
        "遥控(45)": 45,
        "双点遥控(46)": 46,
        "遥调(50)": 50,
    }
    # 国网 202 定值对象的数据类型编码（附录D TLV：布尔=1、Uint=0x23、Float=0x26 等）
    DTYPE_NAMES = {
        0x01: "布尔",
        0x02: "整形",
        0x04: "八位位串/字符串",
        0x20: "无符号小整形",
        0x21: "短整形",
        0x23: "无符号整形",
        0x24: "长整形",
        0x25: "无符号长整形",
        0x26: "单精度浮点数",
        0x27: "双精度浮点数",
        0x2B: "小整形",
        0x2D: "无符号短整形",
    }

    station_views: dict = {}

    def session_name(sid: str) -> str:
        for s in api.list_sessions().get("sessions") or []:
            if s.get("id") == sid:
                return s.get("name") or sid
        return sid

    def current_station_sid() -> str:
        try:
            idx = main_nb.index(main_nb.select())
            text = main_nb.tab(idx, "text")
        except tk.TclError:
            return ""
        for sid, v in station_views.items():
            if v["tab_text"] == text:
                return sid
        return ""

    def selected_ioas(cat: str = "") -> list:
        """返回当前主站选项卡中选中的 IOA 列表。"""
        sid = current_station_sid()
        view = station_views.get(sid)
        if not view:
            return []
        out = []
        for c, tree in view["trees"].items():
            if cat and c != cat:
                continue
            for iid in tree.selection():
                try:
                    out.append(int(str(iid)[1:]))  # iid 形如 p<ioa>
                except ValueError:
                    continue
        return out

    def build_points_panel(parent, view) -> ttk.Frame:
        """四遥状态面板：四遥分类点表 + 地址显示 + 添加/导入/删除。"""
        panel = ttk.Frame(parent, padding=4)
        cat_nb = ttk.Notebook(panel)
        cat_nb.pack(fill=tk.BOTH, expand=True)
        trees = view["trees"]
        for cat in CATEGORIES:
            cat_frame = ttk.Frame(cat_nb, padding=4)
            cat_nb.add(cat_frame, text=cat)
            tree = ttk.Treeview(
                cat_frame,
                columns=("ioa", "name", "type", "value", "q", "modval"),
                show="headings",
                height=10,
                selectmode="extended",
            )
            for c, t, w in [
                ("ioa", "信息体地址(IOA)", 110),
                ("name", "名称", 160),
                ("type", "类型", 110),
                ("value", "值", 120),
                ("q", "品质", 60),
                ("modval", "修改值", 100),
            ]:
                tree.heading(c, text=t)
                tree.column(c, width=w, anchor=tk.W)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            sb = ttk.Scrollbar(cat_frame, orient=tk.VERTICAL, command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            sb.pack(side=tk.RIGHT, fill=tk.Y)

            def _select_all(event, t=tree):
                # Ctrl+A 全选当前分类表（配合“删除选中点”批量删除）
                t.selection_set(t.get_children())
                return "break"

            tree.bind("<Control-a>", _select_all)
            tree.bind("<Control-A>", _select_all)
            tree.bind("<Double-1>", lambda e, t=tree: on_tree_double(e, t))
            trees[cat] = tree

        hex_var = view["hex_var"]

        def fmt_ioa(v) -> str:
            v = int(v or 0)
            return f"{v:X}H" if hex_var.get() else str(v)

        def fmt_val(p: dict):
            """值列：遥信按单/双点显示；无符号整型等整数值不显示小数点。"""
            v = p.get("value")
            if v is None or str(v).lower() in ("none", ""):
                return ""
            tid = p.get("type_id") or 0
            if tid in (1, 30):  # 单点遥信(含带时标)
                return "合" if int(v or 0) == 1 else "分"
            if tid in (3, 31):  # 双点遥信(含带时标)
                return {0: "不确定", 1: "分", 2: "合", 3: "不确定"}.get(int(v), str(v))
            dt = p.get("data_type") or 0
            # 整型类（无符号/有符号/布尔等）或整数值浮点：按整数显示
            if dt in (0x01, 0x02, 0x04, 0x20, 0x21, 0x23, 0x24, 0x25, 0x2B, 0x2D):
                if isinstance(v, float) and v.is_integer():
                    return str(int(v))
                if isinstance(v, (int,)) and not isinstance(v, bool):
                    return str(v)
            if isinstance(v, float) and v.is_integer():
                return str(int(v))
            return v

        def point_category(p: dict) -> str:
            cat = p.get("category") or ""
            if cat in CATEGORIES:
                return cat
            return category_for_type(p.get("type_id") or 0)

        def open_remote_dialog(ioa: int, name: str, tid: int):
            """遥控操作窗口：遥控行为（选择/执行/撤销）+ 遥控动作（分/合）。"""
            kind = "dc" if tid == 46 else "sc"
            dlg = tk.Toplevel(win)
            dlg.title(f"遥控 - {ioa} ({name or '遥控分合闸'})")
            dlg.transient(win)
            dlg.grab_set()
            dlg.resizable(False, False)
            # 定位到主窗口中央，避免弹出到其他显示器
            win.update_idletasks()
            dlg.update_idletasks()
            x = win.winfo_rootx() + max((win.winfo_width() - dlg.winfo_reqwidth()) // 2, 0)
            y = win.winfo_rooty() + max((win.winfo_height() - dlg.winfo_reqheight()) // 3, 0)
            dlg.geometry(f"+{x}+{y}")
            behavior = tk.StringVar(value="选择")
            action = tk.StringVar(value="合")
            frm = ttk.Frame(dlg, padding=10)
            frm.pack(fill=tk.BOTH, expand=True)
            left = ttk.LabelFrame(frm, text="遥控行为")
            left.pack(side=tk.LEFT, padx=6, pady=4)
            for b in ("选择", "执行", "撤销"):
                ttk.Radiobutton(left, text=b, variable=behavior, value=b).pack(
                    anchor=tk.W, padx=8, pady=3
                )
            mid = ttk.LabelFrame(frm, text="遥控动作")
            mid.pack(side=tk.LEFT, padx=6, pady=4)
            for a in ("分", "合"):
                ttk.Radiobutton(mid, text=a, variable=action, value=a).pack(
                    anchor=tk.W, padx=8, pady=3
                )
            btns = ttk.Frame(frm)
            btns.pack(side=tk.LEFT, padx=10, anchor=tk.N)
            hint = ttk.Label(dlg, text="", foreground="#2563eb")
            hint.pack(side=tk.BOTTOM, pady=(0, 6))

            def do_ok():
                apply_form_to_session()
                val = 1 if action.get() == "合" else 0
                b = behavior.get()
                if b == "撤销":
                    r = api.cancel_command(ioa, kind)
                elif b == "选择":
                    r = (
                        api.double_command(ioa, val, True)
                        if kind == "dc"
                        else api.single_command(ioa, bool(val), True)
                    )
                else:
                    r = (
                        api.double_command(ioa, val, False)
                        if kind == "dc"
                        else api.single_command(ioa, bool(val), False)
                    )
                _ok(r)
                # 发送后不自动关闭弹窗，提示用户等待/手动关闭
                if r.get("ok"):
                    hint.config(text=f"已发送：{b} {action.get()}（等待从站回执，可手动关闭）")

            ttk.Button(btns, text="确定", command=do_ok).pack(fill=tk.X, pady=3)
            ttk.Button(btns, text="取消", command=dlg.destroy).pack(fill=tk.X, pady=3)
            dlg.wait_window()

        def _edit_modval(event, t):
            """双击“修改值”列：就地输入定值整定数值。"""
            iid = t.identify_row(event.y)
            if not iid:
                return
            try:
                ioa = int(str(iid)[1:])
            except ValueError:
                return
            bbox = t.bbox(iid, "modval")
            if not bbox or not bbox[2]:
                return
            x, y, w, h = bbox
            cur = (view.get("modvals") or {}).get(ioa, "")
            ed = ttk.Entry(t)
            ed.place(x=x, y=y, width=w, height=h)
            ed.insert(0, str(cur))
            ed.focus_set()
            ed.select_range(0, tk.END)
            done = {"v": False}

            def commit(_e=None):
                if done["v"]:
                    return
                done["v"] = True
                try:
                    val = ed.get().strip()
                except tk.TclError:
                    val = ""
                view.setdefault("modvals", {})[ioa] = val
                t.set(iid, "modval", val)
                ed.destroy()
                t.focus_set()

            def cancel(_e=None):
                if done["v"]:
                    return
                done["v"] = True
                ed.destroy()
                t.focus_set()

            ed.bind("<Return>", commit)
            ed.bind("<Escape>", cancel)
            ed.bind("<FocusOut>", commit)
            return "break"

        def on_tree_double(event, t):
            """点表双击：遥控点弹遥控窗口；“修改值”列就地编辑。"""
            if t.identify_column(event.x).startswith("#6"):
                return _edit_modval(event, t)
            iid = t.identify_row(event.y)
            if not iid:
                return
            p = (view.get("point_map") or {}).get(iid) or {}
            if point_category(p) != "遥控":
                return
            open_remote_dialog(
                int(p.get("ioa") or 0), p.get("name") or "", int(p.get("type_id") or 0)
            )

        def reload_panel():
            for tree in trees.values():
                tree.delete(*tree.get_children())
            pts_raw = (api.get_project().get("points_by_session") or {}).get(view["sid"], [])
            pts = sorted(pts_raw, key=lambda p: int(p.get("ioa") or 0))
            for p in pts:
                cat = point_category(p)
                tree = trees.get(cat) or trees["遥信"]
                tid = p.get("type_id") or 0
                dt = p.get("data_type") or 0
                if dt:
                    tlabel = DTYPE_NAMES.get(dt, f"数据类型0x{dt:02X}")
                else:
                    tlabel = TYPE_LABELS.get(tid, str(tid))
                tree.insert(
                    "",
                    tk.END,
                    iid=f"p{p.get('ioa')}",
                    values=(
                        fmt_ioa(p.get("ioa")),
                        p.get("name") or "",
                        tlabel,
                        fmt_val(p),
                        p.get("quality"),
                        (view.get("modvals") or {}).get(p.get("ioa"), ""),
                    ),
                )
                view.setdefault("point_map", {})[f"p{p.get('ioa')}"] = p

        view["reload"] = reload_panel

        addr_bar = ttk.Frame(panel)
        addr_bar.pack(fill=tk.X, pady=(2, 0))
        ttk.Checkbutton(
            addr_bar, text="十六进制地址显示(H)", variable=hex_var, command=reload_panel
        ).pack(side=tk.LEFT, padx=2)
        ttk.Label(
            addr_bar,
            text="（遥信自 1H 起、遥测自 4001H 起、遥控自 6001H 起、遥调自 5001H 起）",
            foreground="#666",
        ).pack(side=tk.LEFT, padx=6)

        addf = ttk.Frame(panel)
        addf.pack(fill=tk.X, pady=4)
        n_ioa = tk.StringVar(value="1")
        n_name = tk.StringVar(value="")
        n_type = tk.StringVar(value="单点遥信(1)")
        ttk.Label(addf, text="IOA").pack(side=tk.LEFT, padx=2)
        ttk.Entry(addf, textvariable=n_ioa, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Label(addf, text="名称").pack(side=tk.LEFT, padx=2)
        ttk.Entry(addf, textvariable=n_name, width=16).pack(side=tk.LEFT, padx=2)
        ttk.Combobox(addf, textvariable=n_type, values=list(TYPE_KINDS), width=10, state="readonly").pack(
            side=tk.LEFT, padx=2
        )

        def add_point():
            tid = TYPE_KINDS.get(n_type.get(), 1)
            api.upsert_point(
                {"ioa": int(n_ioa.get()), "type_id": tid, "name": n_name.get(), "category": category_for_type(tid)},
                sid=view["sid"],
            )
            reload_points()

        def import_csv():
            path = filedialog.askopenfilename(
                title="选择发码表 CSV 文件",
                filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            )
            if not path:
                return
            r = api.import_points_csv(path, sid=view["sid"])
            if r.get("ok"):
                msg = f"已导入 {r.get('count', 0)} 个点"
                if r.get("skipped"):
                    msg += f"，跳过 {r.get('skipped')} 行"
                messagebox.showinfo("导入成功", msg)
                reload_points()
            else:
                messagebox.showerror("导入失败", r.get("error", ""))

        def del_points():
            ids = selected_ioas()
            if not ids:
                messagebox.showwarning("提示", "请先在点表中选中要删除的点")
                return
            for ioa in ids:
                api.remove_point(ioa, sid=view["sid"])
            reload_points()

        ttk.Button(addf, text="添加点", command=add_point).pack(side=tk.LEFT, padx=4)
        ttk.Button(addf, text="导入CSV发码表", command=import_csv).pack(side=tk.LEFT, padx=4)
        ttk.Button(addf, text="删除选中点", command=del_points).pack(side=tk.LEFT, padx=4)
        return panel

    def reload_points():
        for v in station_views.values():
            rel = v.get("reload")
            if rel:
                rel()

    def make_station_tab(sid: str) -> None:
        """为主站会话创建选项卡（含左侧菜单：四遥状态 / 报文监视）。"""
        if sid in station_views:
            return
        frame = ttk.Frame(main_nb, padding=6)
        side = ttk.Frame(frame, width=110)
        side.pack(side=tk.LEFT, fill=tk.Y)
        side.pack_propagate(False)
        content = ttk.Frame(frame)
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        view = {
            "sid": sid,
            "frame": frame,
            "side": side,
            "content": content,
            "trees": {},
            "hex_var": tk.BooleanVar(value=False),
            "view_var": tk.StringVar(value="四遥状态"),
            "tab_text": f"主站·{session_name(sid)}",
            "pts_panel": None,
            "mon_panel": None,
            "evt_panel": None,
            "reload": None,
        }
        pts = build_points_panel(content, view)
        mon_frame, mon = build_monitor_panel(content, sid)
        evt_frame, evt = build_event_panel(content, sid)
        pts.grid(row=0, column=0, sticky="nsew")
        mon_frame.grid(row=0, column=0, sticky="nsew")
        evt_frame.grid(row=0, column=0, sticky="nsew")
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        view["pts_panel"] = pts
        view["mon_panel"] = mon_frame
        view["mon"] = mon
        view["evt_panel"] = evt_frame
        view["evt"] = evt

        ttk.Label(side, text="功能菜单", font=("", 10, "bold")).pack(anchor="w", pady=(0, 4), padx=4)
        ttk.Radiobutton(
            side, text="四遥状态", variable=view["view_var"], value="四遥状态",
            command=lambda v=view: _switch_view(v),
        ).pack(anchor="w", pady=2, padx=4)
        ttk.Radiobutton(
            side, text="报文监视", variable=view["view_var"], value="报文监视",
            command=lambda v=view: _switch_view(v),
        ).pack(anchor="w", pady=2, padx=4)
        ttk.Radiobutton(
            side, text="事件记录", variable=view["view_var"], value="事件记录",
            command=lambda v=view: _switch_view(v),
        ).pack(anchor="w", pady=2, padx=4)
        ttk.Button(
            side, text="四遥统计",
            command=lambda s=sid: open_stats_dialog(s),
        ).pack(anchor="w", fill=tk.X, pady=(8, 2), padx=4)
        evt_frame.grid_remove()  # 默认显示四遥状态
        mon_frame.grid_remove()  # 默认显示四遥状态

        main_nb.add(frame, text=view["tab_text"])
        station_views[sid] = view

    def _switch_view(view) -> None:
        v = view["view_var"].get()
        for name, panel in (
            ("四遥状态", view["pts_panel"]),
            ("报文监视", view["mon_panel"]),
            ("事件记录", view["evt_panel"]),
        ):
            if panel is None:
                continue
            if name == v:
                panel.grid()
                if name == "事件记录" and view.get("evt"):
                    _reload_events(view["sid"])
            else:
                panel.grid_remove()

    def remove_station_tab(sid: str) -> None:
        view = station_views.pop(sid, None)
        if not view:
            return
        try:
            main_nb.forget(view["frame"])
            view["frame"].destroy()
        except tk.TclError:
            pass
        monitors.pop(sid, None)
        event_panels.pop(sid, None)

    def ensure_station_tabs():
        for s in api.list_sessions().get("sessions") or []:
            make_station_tab(s["id"])
        reload_points()

    def on_tab_change(_evt=None):
        # 切换到哪个主站选项卡，活动会话就跟随哪个主站
        sid = current_station_sid()
        if sid:
            api.set_active_session(sid)
            refresh_session_list(sid)

    main_nb.bind("<<NotebookTabChanged>>", on_tab_change)

    # ---- 报文监视面板（每个主站选项卡内，左侧菜单切换）----
    monitors: dict = {}

    # ---- 事件记录 / 四遥统计 ----
    event_panels: dict = {}

    def build_event_panel(parent, sid: str):
        """构建该主站的事件记录面板（SOE/COS/遥控/遥调）；返回 (frame, ctx)。"""
        frame = ttk.Frame(parent, padding=4)
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text=f"事件记录：{session_name(sid)}").pack(side=tk.LEFT, padx=2)
        status_lbl = ttk.Label(toolbar, text="", foreground="#2563eb")
        status_lbl.pack(side=tk.LEFT, padx=8)

        def _clear():
            api.clear_events(sid)
            _reload_events(sid)

        ttk.Button(toolbar, text="清空事件", command=_clear).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="刷新", command=lambda: _reload_events(sid)).pack(side=tk.LEFT, padx=2)
        ttk.Label(toolbar, text="双击定位点表", foreground="#666").pack(side=tk.LEFT, padx=8)

        tree = ttk.Treeview(frame, columns=("ts", "info", "content", "kind"), show="headings", height=12)
        for c, t, w in [
            ("ts", "接收时间", 150),
            ("info", "信息体", 180),
            ("content", "事件内容", 460),
            ("kind", "事件类别", 110),
        ]:
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor=tk.W)
        # 事件类别配色
        tree.tag_configure("soe", foreground="#15803d")      # SOE 绿
        tree.tag_configure("cos", foreground="#b45309")      # 遥信变位 黄
        tree.tag_configure("ctrl", foreground="#dc2626")     # 遥控 红
        tree.tag_configure("adj", foreground="#1d4ed8")      # 遥调 蓝
        tree.tag_configure("mea", foreground="#7c3aed")      # 遥测 紫
        tree.tag_configure("sys", foreground="#475569")      # 链路启动/停止 灰
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        def _tag_of(kind: str):
            k = kind or ""
            if k == "sys":
                return "sys"
            if k.startswith("ctrl"):
                return "ctrl"
            if k.startswith("adj"):
                return "adj"
            if k.startswith("mea"):
                return "mea"
            if k == "soe":
                return "soe"
            return "cos"

        ctx = {
            "frame": frame,
            "tree": tree,
            "sid": sid,
            "status": status_lbl,
            "last_n": 0,
            "_tag_of": _tag_of,
        }
        event_panels[sid] = ctx
        # 定时刷新（面板可见时），保证事件实时更新
        def _tick():
            ctx2 = event_panels.get(sid)
            if ctx2 and ctx2 is ctx:
                view = station_views.get(sid)
                if view and view["view_var"].get() == "事件记录":
                    _reload_events(sid, silent=True)
            win.after(2000, _tick)

        win.after(2000, _tick)
        return frame, ctx

    def _reload_events(sid: str, silent: bool = False) -> None:
        ctx = event_panels.get(sid)
        if not ctx:
            return
        try:
            r = api.get_events(sid, limit=2000)
            evs = r.get("events") or []
        except Exception:
            return
        if len(evs) == ctx["last_n"]:
            return
        tree = ctx["tree"]
        tree.delete(*tree.get_children())
        for e in evs:
            tree.insert(
                "", tk.END,
                values=(e.get("ts", ""), f"[{e.get('ioa', '')}] {e.get('name', '')}",
                        e.get("content", ""), e.get("kind", "")),
                tags=(ctx["_tag_of"](e.get("kind", "")),),
            )
        ctx["last_n"] = len(evs)
        if not silent:
            ctx["status"].config(text=f"共 {len(evs)} 条")

    def open_stats_dialog(sid: str) -> None:
        """四遥统计对话框（遥信/遥测/遥控/遥调 四个页签）。"""
        dlg = tk.Toplevel(win)
        dlg.title(f"四遥统计 - {session_name(sid)}")
        dlg.transient(win)
        dlg.grab_set()
        win.update_idletasks()
        _x = win.winfo_rootx() + max(0, (win.winfo_width() - 660) // 2)
        _y = win.winfo_rooty() + max(0, (win.winfo_height() - 560) // 2)
        dlg.geometry(f"660x560+{_x}+{_y}")

        nb = ttk.Notebook(dlg)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        COL_DEFS = {
            "遥信": [("ioa", "点号", 70), ("name", "名称", 200), ("change", "变位次数", 90), ("soe", "SOE数量", 90)],
            "遥测": [("ioa", "点号", 70), ("name", "名称", 200), ("up", "越上限", 80), ("down", "越下限", 80), ("dead", "突变死区", 90), ("still", "不变告警", 90)],
            "遥控": [("ioa", "点号", 70), ("name", "名称", 200), ("seloff", "预选分", 80), ("exeoff", "执行分", 80), ("selon", "预选合", 80), ("exeon", "执行合", 80)],
            "遥调": [("ioa", "点号", 70), ("name", "名称", 200), ("preset", "预置", 80), ("exec", "执行(固化)", 100), ("cancel", "撤销", 80)],
        }
        SUM_DEFS = {
            "遥信": [("change", "总变位次数"), ("soe", "SOE数量")],
            "遥测": [("up", "总越上限次数"), ("down", "总越下限次数"), ("dead", "突变死区次数"), ("still", "长期不变告警次数")],
            "遥控": [("selon", "总预选合次数"), ("seloff", "总预选分次数"), ("exeon", "总执行合次数"), ("exeoff", "总执行分次数")],
            "遥调": [("preset", "总预置次数"), ("exec", "总执行次数"), ("cancel", "总撤销次数")],
        }
        tabs = {}
        for cat, cols in COL_DEFS.items():
            f = ttk.Frame(nb, padding=6)
            nb.add(f, text=cat)
            sf = ttk.Frame(f)
            sf.pack(fill=tk.X, pady=(0, 4))
            labels = {}
            for key, title in SUM_DEFS[cat]:
                lab = ttk.Label(sf, text=f"{title}：0次")
                lab.pack(side=tk.LEFT, padx=(0, 16))
                labels[key] = lab
            tree = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", height=12)
            for cid, title, w in cols:
                tree.heading(cid, text=title)
                tree.column(cid, width=w, anchor=tk.W if cid == "name" else tk.CENTER)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            sbb = ttk.Scrollbar(f, orient=tk.VERTICAL, command=tree.yview)
            tree.configure(yscrollcommand=sbb.set)
            sbb.pack(side=tk.RIGHT, fill=tk.Y)
            tabs[cat] = {"tree": tree, "labels": labels}

        def _fill():
            r = api.get_stats(sid)
            rows = r.get("rows") or []
            for cat, t in tabs.items():
                tree = t["tree"]
                tree.delete(*tree.get_children())
                tot = {}
                for row in rows:
                    if row.get("cat") != cat:
                        continue
                    col_keys = [c[0] for c in COL_DEFS[cat]]
                    tree.insert("", tk.END, values=[row.get(k, 0) for k in col_keys])
                    for key, _ in SUM_DEFS[cat]:
                        tot[key] = tot.get(key, 0) + int(row.get(key) or 0)
                for key, lab in t["labels"].items():
                    lab.config(text=f"{dict(SUM_DEFS[cat])[key]}：{tot.get(key, 0)}次")

        ttk.Button(dlg, text="刷新", command=_fill).pack(side=tk.LEFT, padx=10, pady=6)
        ttk.Button(dlg, text="确定", command=dlg.destroy).pack(side=tk.RIGHT, padx=(4, 10), pady=6)
        ttk.Button(dlg, text="取消", command=dlg.destroy).pack(side=tk.RIGHT, padx=4, pady=6)
        _fill()

    def build_monitor_panel(parent, sid: str):
        """构建该主站的报文监视面板；返回 (frame, mon)。"""
        mon = monitors.get(sid)
        if mon:
            mon["frame"].grid_forget()
            return mon["frame"], mon
        frame = ttk.Frame(parent, padding=4)
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text=f"会话：{session_name(sid)}").pack(side=tk.LEFT, padx=2)
        status_lbl = ttk.Label(toolbar, text="", foreground="#2563eb")
        status_lbl.pack(side=tk.LEFT, padx=8)
        ttk.Button(toolbar, text="清空", command=lambda s=sid: _clear_monitor_sid(s)).pack(side=tk.LEFT, padx=2)
        auto_scroll = tk.BooleanVar(value=True)

        def _on_auto_scroll():
            m = monitors.get(sid)
            if not m:
                return
            if auto_scroll.get():
                if not m["frozen"] and m["pending"]:
                    for b in m["pending"]:
                        _append_text(m, b)
                    m["pending"].clear()
                m["text"].see(tk.END)
                m["status"].config(text="")
            else:
                m["status"].config(text="已暂停自动滚动")

        ttk.Checkbutton(toolbar, text="自动滚动", variable=auto_scroll, command=_on_auto_scroll).pack(
            side=tk.LEFT, padx=6
        )

        def export_monitor():
            """导出当前主站报文监视内容到文本文件。"""
            m = monitors.get(sid)
            if not m:
                return
            content = m["text"].get("1.0", tk.END)
            path = filedialog.asksaveasfilename(
                title="导出当前主站报文",
                defaultextension=".txt",
                initialfile=f"报文-{session_name(sid)}.txt",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            )
            if not path:
                return
            try:
                Path(path).write_text(content, encoding="utf-8")
                m["status"].config(text="已导出")
            except OSError as e:
                messagebox.showerror("导出失败", str(e), parent=win)

        ttk.Button(toolbar, text="导出报文", command=export_monitor).pack(side=tk.LEFT, padx=6)

        text = tk.Text(frame, height=12, bg="#0b1220", fg="#dbeafe", font=("Consolas", 10), wrap="char")
        text.tag_config("log", foreground="#facc15")
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=sb.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        mon = {
            "frame": frame,
            "text": text,
            "status": status_lbl,
            "frozen": False,
            "pending": [],
            "tags": [],
            "n": 0,
            "auto_scroll": auto_scroll,
        }
        text.bind("<Button-1>", lambda e, m=mon: _on_left_click(e, m))
        text.bind("<Button-3>", lambda e, m=mon: _on_right_click(e, m))
        monitors[sid] = mon
        return frame, mon

    def remove_monitor(sid: str) -> None:
        mon = monitors.pop(sid, None)
        if not mon:
            return
        try:
            mon["frame"].destroy()
        except tk.TclError:
            pass

    def on_tab_right_click(event):
        # 主站选项卡：右键关闭
        try:
            idx = main_nb.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        if idx is None:
            return
        try:
            text = main_nb.tab(idx, "text")
        except tk.TclError:
            return
        sid = ""
        for s, v in station_views.items():
            if v["tab_text"] == text:
                sid = s
                break
        if not sid:
            return
        menu = tk.Menu(main_nb, tearoff=0)
        menu.add_command(label="关闭此主站选项卡", command=lambda s=sid: remove_station_tab(s))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    main_nb.bind("<Button-3>", on_tab_right_click)

    def _append_text(mon: dict, block: str, extra: tuple = ()) -> None:
        text = mon["text"]
        tag = f"m{mon['n']}"
        mon["n"] += 1
        mon["tags"].append(tag)
        text.insert(tk.END, block, (tag,) + extra)
        # 勾选“自动滚动”时滚到最新报文
        if mon.get("auto_scroll") and mon["auto_scroll"].get():
            text.see(tk.END)

    def _append_frame(mon: dict, ev: dict) -> None:
        ts = time.strftime("%H:%M:%S", time.localtime(ev.get("ts") or time.time()))
        block = f"[{ts}] {ev.get('direction', '')} {ev.get('note', '')}\n{ev.get('hex', '')}\n\n"
        if mon["frozen"]:
            mon["pending"].append(block)
            return
        _append_text(mon, block)

    def _append_log(mon: dict, ev: dict) -> None:
        ts = time.strftime("%H:%M:%S", time.localtime(ev.get("ts") or time.time()))
        block = f"[{ts}] ◆ {ev.get('text', '')}\n\n"
        if mon["frozen"]:
            mon["pending"].append(block)
            return
        _append_text(mon, block, extra=("log",))

    def _msg_text(mon: dict, tag: str) -> str:
        r = mon["text"].tag_ranges(tag)
        if not r:
            return ""
        return mon["text"].get(r[0], r[1]).rstrip("\n")

    def _msg_at(mon: dict, index: str) -> str:
        text = mon["text"]
        try:
            idx = text.index(index)
        except tk.TclError:
            return ""
        for tag in reversed(mon["tags"]):
            r = text.tag_ranges(tag)
            if r:
                try:
                    if text.compare(r[0], "<=", idx) and text.compare(idx, "<=", r[1]):
                        return tag
                except tk.TclError:
                    continue
        return ""

    def _on_left_click(event, mon: dict) -> None:
        # 左键：点击选中整条报文（延迟到默认按键处理之后执行）
        mon["text"].after_idle(lambda: _select_block(mon, event))

    def _select_block(mon: dict, event) -> None:
        text = mon["text"]
        tag = _msg_at(mon, f"@{event.x},{event.y}")
        if not tag:
            return
        r = text.tag_ranges(tag)
        if not r:
            return
        text.tag_remove("sel", "1.0", tk.END)
        text.tag_add("sel", r[0], r[1])
        mon["status"].config(text="已选中报文")

    def _set_frozen(mon: dict, frozen: bool) -> None:
        mon["frozen"] = frozen
        if not frozen and mon["pending"]:
            for block in mon["pending"]:
                _append_text(mon, block)
            mon["pending"].clear()
        mon["status"].config(text="已冻结，新报文缓存中" if frozen else "")

    def _copy_msg(mon: dict, tag: str) -> None:
        text = mon["text"]
        try:
            sel = text.get("sel.first", "sel.last").strip()
        except tk.TclError:
            sel = ""
        if not sel and tag:
            sel = _msg_text(mon, tag)
        if sel:
            text.clipboard_clear()
            text.clipboard_append(sel)
            mon["status"].config(text="已复制到剪贴板")

    def _clear_monitor(mon: dict) -> None:
        mon["text"].delete("1.0", tk.END)
        mon["pending"].clear()
        mon["tags"].clear()
        mon["n"] = 0
        mon["status"].config(text="")

    def _clear_monitor_sid(sid: str) -> None:
        mon = monitors.get(sid)
        if mon:
            _clear_monitor(mon)

    def _on_right_click(event, mon: dict) -> None:
        menu = tk.Menu(mon["text"], tearoff=0)
        tag = _msg_at(mon, f"@{event.x},{event.y}")
        if mon["frozen"]:
            menu.add_command(label="解冻报文", command=lambda: _set_frozen(mon, False))
        else:
            menu.add_command(label="冻结报文", command=lambda: _set_frozen(mon, True))
        menu.add_command(label="复制报文", command=lambda: _copy_msg(mon, tag))
        menu.add_separator()
        menu.add_command(label="清空报文", command=lambda: _clear_monitor(mon))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _commit_modvals():
        """固化成功后：把当前主站点表中有“修改值”的遥调点，值更新为修改值并保存。"""
        sid = current_station_sid()
        view = station_views.get(sid)
        if not view:
            return
        mods = view.get("modvals") or {}
        if not mods:
            return
        by_ioa = {}
        for p in (api.get_project().get("points_by_session") or {}).get(sid, []):
            by_ioa[int(p.get("ioa") or 0)] = p
        for ioa, raw in list(mods.items()):
            if raw == "":
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            p = by_ioa.get(ioa)
            if p is None:
                continue
            api.upsert_point({**p, "value": val}, sid=sid)
        reload_points()

    def poll_events():
        try:
            while True:
                ev = event_q.get_nowait()
                etype = ev.get("type")
                if etype == "connection":
                    sid = ev.get("session_id") or ""
                    if sid and ev.get("state") == "connected":
                        make_station_tab(sid)
                        # 不再自动切换到报文监视视图（由用户手动切换）
                    if not sid or sid == current_station_sid():
                        status.set(ev.get("message") or ev.get("state") or "")
                    refresh_session_list(current_sid() or sid)
                elif etype == "frame":
                    sid = ev.get("session_id") or ""
                    mon = monitors.get(sid)
                    if mon:
                        _append_frame(mon, ev)
                    # 定值区响应（201 读区号 / 200 切换确认）：刷新“区号”输入框
                    if sid and sid == current_station_sid():
                        asdu = ev.get("asdu") or {}
                        if asdu.get("type_id") in (200, 201):
                            objs = asdu.get("objects") or []
                            if objs and objs[0].get("extra", {}).get("area") is not None:
                                area_var.set(str(objs[0]["extra"]["area"]))
                elif etype == "log":
                    sid = ev.get("session_id") or ""
                    mon = monitors.get(sid)
                    if mon:
                        _append_log(mon, ev)
                elif etype == "points":
                    reload_points()
                elif etype == "cmd_result":
                    text = ev.get("text") or ""
                    if ev.get("ok"):
                        messagebox.showinfo("命令结果", text, parent=win)
                        if ev.get("commit_modvals"):
                            _commit_modvals()
                    else:
                        messagebox.showerror("命令失败", text, parent=win)
        except queue.Empty:
            pass
        win.after(200, poll_events)

    def on_close():
        # 退出前自动保存工程（防止重启丢失已保存主站）
        try:
            api.save_project(api.get_project(), "")
        except Exception:
            pass
        win.destroy()

    refresh_nics()
    refresh_session_list()
    ensure_station_tabs()
    reload_points()
    poll_events()
    win.protocol("WM_DELETE_WINDOW", on_close)
    win.mainloop()


# [AGENT_CHANGE_END] 2026-09-07 多主站Tk界面
