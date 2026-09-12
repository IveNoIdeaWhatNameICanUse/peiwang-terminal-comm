# [AGENT_CHANGE_BEGIN] 2026-09-11 Qt主窗口
"""Qt 主窗口：多主站会话 + 连接参数 + 四遥操作 + 每个主站的功能选项卡。

对照 app/tk_shell.py 的 run_tk_shell（Tk 版仍保留，PEIWANG_USE_TK=1 可回退）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# [AGENT_CHANGE_BEGIN] 2026-09-11 Fluent Widgets：输入控件/顶栏标题改用 Fluent
from qfluentwidgets import ComboBox, EditableComboBox, LineEdit, StrongBodyLabel
# [AGENT_CHANGE_END] 2026-09-11

from app.qt import theme
from app.qt.common import (
    VARIANT_HINTS,
    EventBridge,
    ask,
    card,
    error,
    field_label,
    hint_label,
    info,
    make_badge,
    make_button,
    menu_button,
    muted_label,
    set_badge,
    show_result,
    warn,
)
from app.qt.dialogs import (
    open_101_params,
    open_104_params,
    open_device_params,
    open_remote_dialog,
    open_stats_dialog,
)
from app.qt.panels import EventsPanel, MonitorPanel, PointsPanel

VIEWS = ("四遥状态", "报文监视", "事件记录")


class MainWindow(QMainWindow):
    def __init__(self, api, data_root: Path) -> None:
        super().__init__()
        self.api = api
        self.data_root = data_root
        self.station_views: dict = {}
        self._loading = False
        self.bridge = EventBridge(200, self)
        self.bridge.event.connect(self.on_event)

        self.setWindowTitle("配网终端通讯 · 104/101 模拟主站（多主站）")
        self.resize(1240, 880)
        self.setMinimumSize(1180, 780)

        self._build_ui()
        self._boot()

    # ================= 界面搭建 =================

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- 顶栏 ----
        top = QFrame()
        top.setObjectName("TopBar")
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(14, 10, 14, 10)
        top_lay.setSpacing(10)
        brand = StrongBodyLabel("配网终端通讯 · 104/101 模拟主站")
        top_lay.addWidget(brand)
        self.project_label = muted_label("未命名工程")
        top_lay.addWidget(self.project_label)
        top_lay.addStretch(1)
        self.badge = make_badge("未连接")
        top_lay.addWidget(self.badge)
        outer.addWidget(top)

        body = QWidget()
        outer.addWidget(body, 1)
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(12, 12, 12, 10)
        body_lay.setSpacing(12)

        # ---- 左侧：主站会话 ----
        side_card, side_lay = card("主站会话（可多开）")
        side_card.setFixedWidth(250)
        self.session_list = QListWidget()
        self.session_list.setMinimumHeight(140)
        side_lay.addWidget(self.session_list, 1)
        side_lay.addWidget(make_button("新建主站", self.add_session))
        side_lay.addWidget(make_button("删除主站", self.del_session))
        side_lay.addWidget(muted_label("每个主站一个页签，功能菜单在页签左侧"))
        body_lay.addWidget(side_card)

        # ---- 右侧：连接参数 / 四遥 / 主选项卡 ----
        right = QVBoxLayout()
        right.setSpacing(10)
        body_lay.addLayout(right, 1)

        right.addWidget(self._build_conn_card())
        right.addWidget(self._build_ops_card())

        self.main_tabs = QTabWidget()
        self.main_tabs.setDocumentMode(False)
        # 页签右键：关闭此主站选项卡（与 Tk 版一致）
        self.main_tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.main_tabs.tabBar().customContextMenuRequested.connect(self._on_tab_context_menu)
        right.addWidget(self.main_tabs, 1)

        self.setStatusBar(self.statusBar())
        self.statusBar().showMessage("就绪")

    def _build_conn_card(self) -> QFrame:
        frame, lay = card("当前会话连接参数")
        self.conn_card = frame

        # [AGENT_CHANGE_2026-09-11] Fluent 输入控件
        self.name_edit = LineEdit()
        self.name_edit.setText("主站1")
        self.name_edit.setFixedWidth(110)
        self.remote_ip = LineEdit()
        self.remote_ip.setText("127.0.0.1")
        self.remote_ip.setFixedWidth(130)
        self.remote_port = LineEdit()
        self.remote_port.setText("2404")
        self.remote_port.setFixedWidth(80)
        self.proto_combo = ComboBox()
        self.proto_combo.addItems(["104", "101"])
        self.proto_combo.setFixedWidth(80)
        self.local_ip = EditableComboBox()
        self.local_ip.setMinimumWidth(220)
        self.local_port = LineEdit()
        self.local_port.setFixedWidth(80)
        self.ca = LineEdit()
        self.ca.setText("1")
        self.ca.setFixedWidth(70)
        self.oa = LineEdit()
        self.oa.setText("0")
        self.oa.setFixedWidth(70)
        self.status_label = hint_label("")

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.addWidget(field_label("名称"), 0, 0)
        grid.addWidget(self.name_edit, 0, 1)
        grid.addWidget(field_label("从站 IP"), 0, 2)
        grid.addWidget(self.remote_ip, 0, 3)
        grid.addWidget(field_label("端口"), 0, 4)
        grid.addWidget(self.remote_port, 0, 5)
        grid.addWidget(field_label("协议"), 0, 6)
        grid.addWidget(self.proto_combo, 0, 7)

        grid.addWidget(field_label("本地网卡/IP"), 1, 0)
        grid.addWidget(self.local_ip, 1, 1, 1, 2)
        grid.addWidget(field_label("本地端口"), 1, 3)
        grid.addWidget(self.local_port, 1, 4)
        grid.addWidget(muted_label("(多主站勿共用同一本地端口)"), 1, 5, 1, 3)

        grid.addWidget(field_label("公共地址(CA)"), 2, 0)
        grid.addWidget(self.ca, 2, 1)
        grid.addWidget(field_label("起源地址(OA)"), 2, 2)
        grid.addWidget(self.oa, 2, 3)
        grid.addWidget(self.status_label, 2, 4, 1, 4)
        grid.setColumnStretch(8, 1)

        # 工程三钮：固定在最右侧
        proj_box = QWidget()
        proj_lay = QVBoxLayout(proj_box)
        proj_lay.setContentsMargins(0, 0, 0, 0)
        proj_lay.setSpacing(6)
        proj_lay.addWidget(make_button("新建工程", self.new_project))
        proj_lay.addWidget(make_button("导入工程", self.import_config))
        proj_lay.addWidget(make_button("保存工程", self.do_save))
        proj_lay.addStretch(1)
        grid.addWidget(proj_box, 0, 9, 3, 1)
        lay.addLayout(grid)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addWidget(make_button("连接", self.do_connect, variant="primary"))
        btn_row.addWidget(make_button("断开", self.do_disconnect))
        btn_row.addWidget(make_button("全部断开", self.do_disconnect_all))
        btn_row.addWidget(make_button("刷新网卡", self.refresh_nics))
        self.btn_104_params = make_button("104 参数设置", lambda: open_104_params(self, self.current_sid()))
        self.btn_101_params = make_button("101 参数设置", lambda: open_101_params(self, self.current_sid()))
        btn_row.addWidget(self.btn_104_params)
        btn_row.addWidget(self.btn_101_params)
        btn_row.addWidget(make_button("设备参数", lambda: open_device_params(self, self.current_sid())))
        btn_row.addStretch(1)
        lay.addLayout(btn_row)
        return frame

    def _build_ops_card(self) -> QFrame:
        frame, lay = card("四遥操作（作用于当前会话）")

        project = self.api.get_project() or {}
        self.variant_combo = ComboBox()
        self.variant_combo.addItems(["广西", "南网", "国网"])
        self.variant_combo.setCurrentText(str(project.get("protocol_variant") or "广西"))
        self.variant_hint = muted_label(
            VARIANT_HINTS.get(str(project.get("protocol_variant") or "广西"), "")
        )
        self.variant_combo.currentTextChanged.connect(lambda _txt: self.on_variant_change())

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row1.addWidget(field_label("规约版本"))
        row1.addWidget(self.variant_combo)
        row1.addWidget(self.variant_hint)
        # [AGENT_CHANGE_BEGIN] 2026-09-12 复位进程按钮
        # 总召/对时靠左（紧跟规约版本），其后增加「复位进程」；右侧 stretch 留白
        row1.addWidget(make_button("总召唤", lambda: self._do(self.api.general_interrogation)))
        row1.addWidget(make_button("时钟同步", lambda: self._do(self.api.clock_sync)))
        row1.addWidget(make_button("复位进程", lambda: self._do(lambda: self.api.reset_process(1))))
        row1.addStretch(1)
        # [AGENT_CHANGE_END] 2026-09-12 复位进程按钮
        lay.addLayout(row1)

        self.batch_edit = LineEdit()
        self.batch_edit.setText("10")
        self.batch_edit.setFixedWidth(60)
        self.area_edit = LineEdit()
        self.area_edit.setText("1")
        self.area_edit.setFixedWidth(60)
        self.area_label = field_label("区号")

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addWidget(field_label("定值整定"))
        row2.addWidget(field_label("单帧定值个数"))
        row2.addWidget(self.batch_edit)
        row2.addWidget(make_button("召唤选中", self.read_setpoint_selected))
        row2.addWidget(make_button("预置", self.preset_setpoint))
        row2.addWidget(make_button("激活", self.activate_setpoint))
        row2.addWidget(make_button("撤销", self.undo_setpoint))
        self.btn_read_all = make_button("参数全召唤", self.full_read_setpoints)
        row2.addWidget(self.btn_read_all)
        row2.addWidget(self.area_label)
        row2.addWidget(self.area_edit)
        self.btn_read_area = make_button("读定值区(201)", self.read_setting_area)
        self.btn_switch_area = make_button("切换定值区(200)", self.switch_setting_area)
        row2.addWidget(self.btn_read_area)
        row2.addWidget(self.btn_switch_area)
        row2.addStretch(1)
        lay.addLayout(row2)

        self.on_variant_change()
        return frame

    # ================= 启动 =================

    def _boot(self) -> None:
        self.refresh_nics()
        # 协议切换立即写回会话（避免后续刷新把界面改回旧协议）
        self.proto_combo.currentTextChanged.connect(lambda _t: self.on_proto_change(save=True))
        self.session_list.currentItemChanged.connect(self.on_session_changed)
        self.main_tabs.currentChanged.connect(self.on_tab_changed)
        self.refresh_sessions()
        self.update_project_title(str(getattr(self.api.store, "path", "") or ""))
        self.ensure_station_tabs()
        self.reload_points()
        self.bridge.attach(self.api)

    # ================= 会话 =================

    def current_sid(self) -> str:
        item = self.session_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.UserRole) or "")

    def current_station_sid(self) -> str:
        widget = self.main_tabs.currentWidget()
        for sid, view in self.station_views.items():
            if view["page"] is widget:
                return sid
        return ""

    def session(self, sid: str) -> Optional[dict]:
        if not sid:
            return None
        for s in self.api.list_sessions().get("sessions") or []:
            if s.get("id") == sid:
                return s
        return None

    def session_name(self, sid: str) -> str:
        sess = self.session(sid)
        return str(sess.get("name") or sid) if sess else sid

    def is_101_selected(self) -> bool:
        return self.proto_combo.currentText() == "101"

    def refresh_sessions(self, select_id: str = "") -> None:
        self._loading = True
        try:
            data = self.api.list_sessions()
            sessions = data.get("sessions") or []
            connected = data.get("session_connected") or {}
            active = data.get("active_session_id") or ""
            self.session_list.clear()
            select_row = 0
            for i, s in enumerate(sessions):
                sid = s["id"]
                mark = "●" if connected.get(sid) else "○"
                act = " [当前]" if sid == active else ""
                if str(s.get("protocol") or "104") == "101":
                    addr = f"{s.get('serial_port')}@{s.get('baudrate')}"
                else:
                    addr = f"{s.get('remote_ip')}:{s.get('remote_port')}"
                item = QListWidgetItem(f"{mark} {s.get('name', sid)}  {addr}{act}")
                item.setData(Qt.UserRole, sid)
                self.session_list.addItem(item)
                if select_id and sid == select_id:
                    select_row = i
                elif not select_id and sid == active:
                    select_row = i
            if self.session_list.count():
                self.session_list.setCurrentRow(select_row)
                self.load_selected_into_form()
        finally:
            self._loading = False
        self.ensure_station_tabs()

    def on_session_changed(self, item: Optional[QListWidgetItem], _prev=None) -> None:
        if self._loading or item is None:
            return
        sid = str(item.data(Qt.UserRole) or "")
        if not sid:
            return
        self.api.set_active_session(sid)
        self.load_selected_into_form()

    def load_selected_into_form(self) -> None:
        sid = self.current_sid()
        sess = self.session(sid)
        if not sess:
            return
        guard = self._loading
        self._loading = True
        try:
            self.name_edit.setText(str(sess.get("name") or ""))
            self.remote_ip.setText(str(sess.get("remote_ip") or ""))
            self.remote_port.setText(str(sess.get("remote_port") or 2404))
            self.local_ip.setCurrentText(str(sess.get("local_ip") or ""))
            lp = sess.get("local_port") or 0
            self.local_port.setText(str(lp) if lp else "")
            self.ca.setText(str(sess.get("common_address") or 1))
            self.oa.setText(str(sess.get("originator") or 0))
            self.proto_combo.setCurrentText(str(sess.get("protocol") or "104"))
        finally:
            self._loading = guard
        self.on_proto_change()
        self.update_badge(sid)

    def apply_form_to_session(self) -> None:
        sid = self.current_sid()
        if not sid:
            return

        def as_int(text: str, default: int) -> int:
            try:
                return int(str(text).strip() or default)
            except (TypeError, ValueError):
                return default

        lp = self.local_port.text().strip()
        self.api.update_session(
            sid,
            {
                "name": self.name_edit.text().strip() or "主站",
                "remote_ip": self.remote_ip.text().strip(),
                "remote_port": as_int(self.remote_port.text(), 2404),
                "local_ip": self.local_ip.currentText().strip(),
                "local_port": as_int(lp, 0) if lp else 0,
                "common_address": as_int(self.ca.text(), 1),
                "originator": as_int(self.oa.text(), 0),
                "protocol": self.proto_combo.currentText(),
            },
        )
        self.api.set_active_session(sid)

    def on_proto_change(self, save: bool = False) -> None:
        is101 = self.is_101_selected()
        # 101 走串口：从站 IP/端口与本地端口/网卡在 101 下均不可编辑
        for widget in (self.remote_ip, self.remote_port, self.local_port, self.local_ip):
            widget.setEnabled(not is101)
        self.btn_104_params.setEnabled(not is101)
        self.btn_101_params.setEnabled(is101)
        if save and not self._loading:
            # 协议一旦切换就写回会话，避免后续刷新（如保存 101 参数）把界面改回旧协议
            sid = self.current_sid()
            if sid:
                try:
                    self.api.update_session(sid, {"protocol": self.proto_combo.currentText()})
                    self.refresh_sessions(sid)
                except Exception:
                    pass

    def update_badge(self, sid: str = "") -> None:
        sid = sid or self.current_sid()
        connected = bool((self.api.list_sessions().get("session_connected") or {}).get(sid))
        set_badge(self.badge, connected, "已连接" if connected else "未连接")
        self.status_label.setText("已连接" if connected else "未连接")

    def add_session(self) -> None:
        r = self.api.create_session(
            {"name": f"主站{self.session_list.count() + 1}", "auto_local_port": True}
        )
        if not show_result(self, r):
            return
        self.refresh_sessions(r["session"]["id"])

    def del_session(self) -> None:
        sid = self.current_sid()
        if not sid:
            return
        r = self.api.delete_session(sid)
        if not show_result(self, r):
            return
        self.remove_station_tab(sid)
        self.refresh_sessions()

    # ================= 主站选项卡 =================

    def ensure_station_tabs(self) -> None:
        for s in self.api.list_sessions().get("sessions") or []:
            self.make_station_tab(s["id"], str(s.get("name") or ""))
        self.reload_points()

    def make_station_tab(self, sid: str, name: str = "") -> None:
        view = self.station_views.get(sid)
        title = f"主站·{name or self.session_name(sid)}"
        if view:
            index = self.main_tabs.indexOf(view["page"])
            if index >= 0 and self.main_tabs.tabText(index) != title:
                self.main_tabs.setTabText(index, title)
            return

        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)

        side = QFrame()
        side.setObjectName("SideBar")
        side.setFixedWidth(132)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(8, 8, 8, 8)
        side_lay.setSpacing(4)
        side_lay.addWidget(field_label("功能菜单"))

        points = PointsPanel(self, sid)
        monitor = MonitorPanel(self, sid)
        events = EventsPanel(self, sid)
        stack = QStackedWidget()
        for panel in (points, monitor, events):
            stack.addWidget(panel)

        buttons = {}
        for i, view_name in enumerate(VIEWS):
            btn = menu_button(
                view_name, lambda _=False, s=sid, n=view_name: self.show_view(s, n)
            )
            btn.setChecked(i == 0)
            buttons[view_name] = btn
            side_lay.addWidget(btn)
        side_lay.addWidget(make_button("四遥统计", lambda s=sid: open_stats_dialog(self, s)))
        side_lay.addStretch(1)

        lay.addWidget(side)
        lay.addWidget(stack, 1)
        index = self.main_tabs.addTab(page, title)
        self.station_views[sid] = {
            "sid": sid,
            "page": page,
            "stack": stack,
            "buttons": buttons,
            "points": points,
            "monitor": monitor,
            "events": events,
            "tab_index": index,
        }

    def remove_station_tab(self, sid: str) -> None:
        view = self.station_views.pop(sid, None)
        if not view:
            return
        index = self.main_tabs.indexOf(view["page"])
        if index >= 0:
            self.main_tabs.removeTab(index)
        view["page"].deleteLater()

    def show_view(self, sid: str, name: str) -> None:
        view = self.station_views.get(sid)
        if not view:
            return
        view["stack"].setCurrentIndex(VIEWS.index(name))
        for view_name, btn in view["buttons"].items():
            btn.setChecked(view_name == name)
        if name == "事件记录":
            view["events"].reload(silent=True)

    def _on_tab_context_menu(self, pos) -> None:
        bar = self.main_tabs.tabBar()
        index = bar.tabAt(pos)
        if index < 0:
            return
        page = self.main_tabs.widget(index)
        sid = ""
        for candidate, view in self.station_views.items():
            if view["page"] is page:
                sid = candidate
                break
        if not sid:
            return
        menu = QMenu(self)
        menu.addAction("关闭此主站选项卡", lambda s=sid: self.remove_station_tab(s))
        menu.exec(bar.mapToGlobal(pos))

    def on_tab_changed(self, _index: int) -> None:
        # 切换到哪个主站选项卡，活动会话就跟随哪个主站
        sid = self.current_station_sid()
        if not sid:
            return
        self.api.set_active_session(sid)
        if self.current_sid() != sid:
            self.refresh_sessions(sid)

    def locate_point(self, sid: str, ioa: int) -> None:
        """事件记录双击：切到该主站的四遥状态并选中该点。"""
        view = self.station_views.get(sid)
        if not view:
            return
        index = self.main_tabs.indexOf(view["page"])
        if index >= 0:
            self.main_tabs.setCurrentIndex(index)
        self.show_view(sid, "四遥状态")
        view["points"].focus_ioa(ioa)

    def reload_points(self) -> None:
        for view in self.station_views.values():
            view["points"].reload()

    # ================= 连接 =================

    def refresh_nics(self) -> None:
        current = self.local_ip.currentText()
        values = [""] + [str(n.get("ip") or "") for n in (self.api.get_nics() or [])]
        self.local_ip.clear()
        self.local_ip.addItems(values)
        self.local_ip.setCurrentText(current)

    def do_connect(self) -> None:
        self.apply_form_to_session()
        sess = self.session(self.current_sid()) or {}
        if str(sess.get("protocol") or "104") == "101" and not str(
            sess.get("serial_port") or ""
        ).strip():
            warn(self, "提示", "请先点「101 参数设置」选择串口，再连接")
            return
        r = self.api.connect()
        self.refresh_sessions(self.current_sid())
        if not r.get("ok"):
            error(self, "连接失败", f"[{r.get('code', '')}] {r.get('error')}")
            self.set_status("连接失败")
            self.status_label.setText("连接失败")
        else:
            self.set_status("已连接")
            self.status_label.setText("已连接")

    def do_disconnect(self) -> None:
        sid = self.current_sid()
        self.api.disconnect(sid)
        self.refresh_sessions(sid)
        self.set_status("未连接")
        self.status_label.setText("未连接")

    def do_disconnect_all(self) -> None:
        sid = self.current_sid()
        self.api.disconnect_all()
        self.refresh_sessions(sid)
        self.set_status("未连接")
        self.status_label.setText("未连接")

    def set_status(self, text: str) -> None:
        try:
            self.statusBar().showMessage(text or "就绪")
        except Exception:
            pass

    # ================= 工程 =================

    def update_project_title(self, path: str = "") -> None:
        name = "未命名工程"
        if path:
            try:
                name = Path(path).stem or name
            except Exception:
                pass
        self.project_label.setText(name)
        self.setWindowTitle(f"配网终端通讯 · {name} · 104/101 模拟主站（多主站）")

    def new_project(self) -> None:
        if not ask(
            self,
            "新建工程",
            "将断开全部连接并清空当前会话/点表，未保存的修改会丢失。是否继续？",
        ):
            return
        r = self.api.new_project()
        if not show_result(self, r, "新建失败"):
            return
        self._sync_variant()
        for sid in list(self.station_views.keys()):
            self.remove_station_tab(sid)
        self.refresh_sessions()
        self.status_label.setText("未连接")
        # 新建工程在内存中，落盘前不写任何本地文件
        self.update_project_title("")
        info(self, "新建工程", "已创建空白工程；点「保存工程」时可命名工程并选择保存目录")

    def import_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入工程", "", "工程文件 (*.json);;所有文件 (*.*)"
        )
        if not path:
            return
        r = self.api.load_project(path)
        if not show_result(self, r, "导入失败"):
            return
        self._sync_variant()
        for sid in list(self.station_views.keys()):
            self.remove_station_tab(sid)
        self.refresh_sessions()
        # 导入即「打开已有工程」：标题跟随工程名，之后保存直接覆盖该文件
        self.update_project_title(path)
        info(self, "导入成功", f"已导入工程：{path}")

    def do_save(self) -> None:
        self.apply_form_to_session()
        data = self.api.get_project()
        # 已有工程文件（打开过的或保存过的）→ 直接覆盖，不再询问名称与目录
        path = str(data.get("project_path") or "")
        if not path:
            initial = self.data_root / "configs"
            path, _ = QFileDialog.getSaveFileName(
                self,
                "保存工程（命名并选择保存目录）",
                str(initial / "未命名工程.json"),
                "工程文件 (*.json);;所有文件 (*.*)",
            )
            if not path:
                return
        r = self.api.save_project(data, path)
        if r.get("ok"):
            saved = str(r.get("path", ""))
            self.update_project_title(saved)
            self.set_status(f"已保存：{saved}")
        else:
            error(self, "保存失败", str(r.get("error", "")))

    def _sync_variant(self) -> None:
        variant = str((self.api.get_project() or {}).get("protocol_variant") or "广西")
        self.variant_combo.setCurrentText(variant)
        self.on_variant_change()

    def on_variant_change(self) -> None:
        variant = self.variant_combo.currentText()
        self.api.set_protocol_variant(variant)
        self.variant_hint.setText(VARIANT_HINTS.get(variant, ""))
        is_gw = variant == "国网"
        self.btn_read_all.setVisible(is_gw)
        for widget in (self.area_label, self.area_edit, self.btn_read_area, self.btn_switch_area):
            widget.setVisible(is_gw)

    # ================= 四遥操作 =================

    def _do(self, fn) -> bool:
        self.apply_form_to_session()
        return show_result(self, fn())

    def _current_batch(self) -> int:
        try:
            return max(1, min(int(self.batch_edit.text()), 127))
        except (TypeError, ValueError):
            return 10

    def _current_area(self) -> int:
        raw = self.area_edit.text().strip()
        return int(raw) if raw.isdigit() else 1

    def _current_points(self, sid: str = "") -> Optional[PointsPanel]:
        view = self.station_views.get(sid or self.current_station_sid())
        return view["points"] if view else None

    def read_setpoint_selected(self) -> None:
        self.apply_form_to_session()
        points = self._current_points()
        ioas = points.selected_ioas("遥调") if points else []
        if not ioas:
            warn(self, "提示", "请先在“遥调”点表中选中定值点")
            return
        self._do(lambda: self.api.read_points(ioas, area=self._current_area(), batch=self._current_batch()))

    def _selected_setpoints(self):
        points = self._current_points()
        items = points.modval_items() if points else []
        if not items:
            warn(self, "提示", "请先在“遥调”点表中选中定值点")
            return None
        missing = [ioa for ioa, val in items if val is None]
        if missing:
            warn(self, "提示", f"请双击“修改值”列填写数值（IOA {missing}）")
            return None
        return items

    def preset_setpoint(self) -> None:
        self.apply_form_to_session()
        items = self._selected_setpoints()
        if items is None:
            return
        for ioa, value in items:
            show_result(self, self.api.preset_setpoint(ioa, value, True, area=self._current_area()))

    def activate_setpoint(self) -> None:
        self.apply_form_to_session()
        # 国网：固化 = 203 VSQ=0 + 区号 + PI(S/E=0)，无需选中点/修改值
        if self.variant_combo.currentText() == "国网":
            self._do(lambda: self.api.fix_setpoint(area=self._current_area()))
            return
        items = self._selected_setpoints()
        if items is None:
            return
        for ioa, value in items:
            show_result(self, self.api.preset_setpoint(ioa, value, False, area=self._current_area()))

    def undo_setpoint(self) -> None:
        self.apply_form_to_session()
        # 国网：撤销 = 203 VSQ=0 + 区号 + PI(CR=1)，无信息体地址，无需选中点
        if self.variant_combo.currentText() == "国网":
            self._do(lambda: self.api.cancel_setpoint(0, area=self._current_area()))
            return
        points = self._current_points()
        items = points.modval_items() if points else []
        if not items:
            warn(self, "提示", "请先在“遥调”点表中选中定值点")
            return
        for ioa, _value in items:
            show_result(self, self.api.cancel_setpoint(ioa, area=self._current_area()))

    def full_read_setpoints(self) -> None:
        self.apply_form_to_session()
        sid = self.current_station_sid()
        batch = self._current_batch()
        try:
            self.api.update_session(sid, {"setpoint_batch": batch})
        except Exception:
            pass
        self._do(lambda: self.api.read_all_setpoints(sid, area=self._current_area(), batch=batch))
        self.reload_points()

    def read_setting_area(self) -> None:
        self._do(self.api.read_setting_area)

    def switch_setting_area(self) -> None:
        self.apply_form_to_session()
        raw = self.area_edit.text().strip()
        if not raw.isdigit():
            warn(self, "提示", "定值区号无效")
            return
        self._do(lambda: self.api.switch_setting_area(int(raw)))

    # ================= 遥控 / 修改值 =================

    def open_remote_dialog(self, ioa: int, name: str, tid: int) -> None:
        open_remote_dialog(self, ioa, name, tid)

    def _commit_modvals(self) -> None:
        """固化成功后：把当前主站点表中有「修改值」的遥调点，值更新为修改值并保存。"""
        sid = self.current_station_sid()
        points = self._current_points(sid)
        if not points:
            return
        mods = points.modvals or {}
        if not mods:
            return
        by_ioa = {}
        for p in (self.api.get_project().get("points_by_session") or {}).get(sid, []):
            by_ioa[int(p.get("ioa") or 0)] = p
        for ioa, raw in list(mods.items()):
            if raw == "":
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            p = by_ioa.get(int(ioa))
            if p is None:
                continue
            self.api.upsert_point({**p, "value": value}, sid=sid)
        self.reload_points()

    def _clear_modvals(self) -> None:
        """固化成功或撤销成功后：清空遥调「修改值」列。"""
        points = self._current_points(self.current_station_sid())
        if points:
            points.clear_modvals()

    # ================= 后端事件 =================

    def on_event(self, ev: dict) -> None:
        etype = ev.get("type")
        if etype == "connection":
            sid = str(ev.get("session_id") or "")
            if sid and ev.get("state") == "connected":
                self.make_station_tab(sid)
            if not sid or sid == self.current_station_sid():
                self.set_status(str(ev.get("message") or ev.get("state") or ""))
            self.refresh_sessions(self.current_sid() or sid)
        elif etype == "frame":
            sid = str(ev.get("session_id") or "")
            view = self.station_views.get(sid)
            if view:
                view["monitor"].append_frame(ev)
            # 定值区响应（201 读区号 / 200 切换确认）：刷新「区号」输入框
            if sid and sid == self.current_station_sid():
                asdu = ev.get("asdu") or {}
                if asdu.get("type_id") in (200, 201):
                    objs = asdu.get("objects") or []
                    extra = (objs[0].get("extra") or {}) if objs else {}
                    if extra.get("area") is not None:
                        self.area_edit.setText(str(extra["area"]))
        elif etype == "log":
            sid = str(ev.get("session_id") or "")
            view = self.station_views.get(sid)
            if view:
                view["monitor"].append_log(ev)
        elif etype == "points":
            self.reload_points()
        elif etype == "cmd_result":
            text = str(ev.get("text") or "")
            if ev.get("ok"):
                info(self, "命令结果", text)
                if ev.get("commit_modvals"):
                    self._commit_modvals()
                if ev.get("clear_modvals") or ev.get("commit_modvals"):
                    self._clear_modvals()
            else:
                error(self, "命令失败", text)

    # ================= 关闭 =================

    def closeEvent(self, event) -> None:
        # 仅已有工程文件时才自动落盘；新建且从未保存过的工程不写任何本地文件
        try:
            if getattr(self.api.store, "path", None):
                self.api.save_project(self.api.get_project(), "")
        except Exception:
            pass
        super().closeEvent(event)


__all__ = ["MainWindow"]
# [AGENT_CHANGE_END] 2026-09-11 Qt主窗口
