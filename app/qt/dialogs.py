# [AGENT_CHANGE_BEGIN] 2026-09-11 Qt对话框
"""101 / 104 / 设备参数、遥控、四遥统计对话框。

对照 app/tk_shell.py 的 open_101_params_dialog / open_params_dialog /
open_device_params_dialog / open_remote_dialog / open_stats_dialog。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSplitter,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    EditableComboBox,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    RadioButton,
    TableWidget,
)

from app.qt.common import (
    error,
    field_label,
    fit_dialog,
    hint_label,
    info,
    make_button,
    muted_label,
    warn,
)

MODE_LABELS = {"unbalanced": "非平衡", "balanced": "平衡", "hainan": "海南双主站"}
LABEL_MODES = {v: k for k, v in MODE_LABELS.items()}


# [AGENT_CHANGE 2026-09-12] 表单控件统一尺寸：原来各处 width 混用 70/80/90/100/120/140，
# 且 Fluent ComboBox 比 LineEdit 矮（25/27 vs 33），同一列相邻控件宽窄高低不一。
_FORM_W, _FORM_H = 120, 33


def _line(text: str, width: int = _FORM_W) -> QLineEdit:
    # [AGENT_CHANGE_2026-09-11] Fluent 输入框
    ed = LineEdit()
    ed.setText(str(text))
    ed.setFixedSize(_FORM_W, _FORM_H)
    return ed


def _combo(items, current: str, width: int = _FORM_W, editable: bool = False):
    # [AGENT_CHANGE_2026-09-11] Fluent 下拉：可编辑场景用 EditableComboBox（本身即输入框），
    # 注意 Fluent ComboBox 不是 QComboBox，没有 setEditable/setInsertPolicy。
    cb = EditableComboBox() if editable else ComboBox()
    cb.addItems([str(i) for i in items])
    cb.setFixedSize(_FORM_W, _FORM_H)
    cb.setCurrentText(str(current))
    return cb


class Params101Dialog(QDialog):
    """101 串口 / 链路参数。"""

    def __init__(self, win, sid: str, sess: dict) -> None:
        super().__init__(win)
        self.win = win
        self.api = win.api
        self.sid = sid
        self.setWindowTitle(f"101 参数设置 - {sess.get('name')}")
        self.setModal(True)
        self._addr_prog = False
        self._addr_user = False

        mode0 = str(sess.get("link_mode") or ("balanced" if sess.get("balanced") else "unbalanced"))
        addr0 = int(sess.get("addr_size") or 2)
        # 海南现场链路地址为 1 字节；工程里遗留的 2（旧默认）在海南模式下按未设置处理
        if mode0 == "hainan" and addr0 == 2:
            addr0 = 1

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        root.addWidget(muted_label("101 串口 / 链路（保存后重新连接生效）"))

        # [AGENT_CHANGE 2026-09-12] 改为两列网格（同 104）：12 个字段单列是 646×569（偏高、
        # 右侧还有留白）。「刷新串口」按钮放在右列起点（不放跨列的 port_box，否则会把左值列
        # 撑到 250+、中间留一大片空）；「中心编号」的说明文字也移到右列，避免撑宽该行。
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)

        # [AGENT_CHANGE_BEGIN] 2026-09-12 101串口保存生效
        # EditableComboBox 空列表时 setCurrentText 无效；须先有 items 再选中，
        # 且刷新时保留会话/当前口（不在枚举里则插入），禁止静默改成 ports[0]。
        self.port = _combo([], "", editable=True)
        grid.addWidget(field_label("串口"), 0, 0, Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(self.port, 0, 1)
        grid.addWidget(
            make_button("刷新串口", self.refresh_ports), 0, 2, Qt.AlignLeft | Qt.AlignVCenter
        )
        self.refresh_ports(preferred=str(sess.get("serial_port") or "COM1"))
        # [AGENT_CHANGE_END] 2026-09-12 101串口保存生效

        self.baud = _combo(
            ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"],
            sess.get("baudrate", 9600),
            editable=True,
        )
        self.parity = _combo(["N", "E", "O"], sess.get("serial_parity", "N"))
        self.stopbits = _combo(["1", "2"], sess.get("stopbits", 1))
        self.link_addr = _line(sess.get("link_addr", 1))
        self.addr_size = _combo(["1", "2"], addr0)
        self.addr_size.currentTextChanged.connect(self._on_addr_changed)
        self.poll = _line(sess.get("poll_period", 1.0))
        self.ack = _line(sess.get("link_ack_timeout", 10.0))
        self.ioa_size = _combo(["2", "3"], sess.get("ioa_size_101", 2))
        self.tx_delay = _line(sess.get("tx_delay_ms", 200.0))
        self.mode = _combo(
            [MODE_LABELS["unbalanced"], MODE_LABELS["balanced"], MODE_LABELS["hainan"]],
            MODE_LABELS.get(mode0, MODE_LABELS["unbalanced"]),
        )
        self.mode.currentTextChanged.connect(self._on_mode_changed)
        self.center = _line(sess.get("center_id", 1))

        pairs = [
            ("波特率", self.baud),
            ("校验", self.parity),
            ("停止位", self.stopbits),
            ("链路地址", self.link_addr),
            ("链路地址长度(字节)", self.addr_size),
            ("轮询周期(秒)", self.poll),
            ("链路应答超时(秒)", self.ack),
            ("信息体地址长度(字节)", self.ioa_size),
            ("发送间隔(毫秒)", self.tx_delay),
            ("链路模式", self.mode),
            ("中心编号(1~255)", self.center),
        ]
        for i, (label, w) in enumerate(pairs):
            row, col = divmod(i, 2)
            grid.addWidget(field_label(label), row + 1, col * 2, Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(w, row + 1, col * 2 + 1)
        self.center_hint = muted_label("同 COM 多主站按编号分流（默认 1/2）")
        # 放在整张网格下方独占一行（跨列）；若塞进某一列里会把这列撑宽
        grid.addWidget(self.center_hint, 7, 0, 1, 4, Qt.AlignLeft | Qt.AlignVCenter)
        root.addLayout(grid)

        self.ignore_fcb = CheckBox(
            "忽略 FCB 位错误（发送用户数据时 FCV=0，兼容从站 FCB 翻转异常）"
        )
        self.ignore_fcb.setChecked(bool(sess.get("ignore_fcb_error", False)))
        root.addWidget(self.ignore_fcb)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(make_button("保存", self.save_params, variant="primary"))
        btns.addWidget(make_button("取消", self.reject))
        root.addLayout(btns)

        self._on_mode_changed()
        # [AGENT_CHANGE_2026-09-11] 收紧最小尺寸，避免大片留白（实际尺寸仍由 sizeHint 决定）
        fit_dialog(self, win, min_w=480, min_h=220)

    def refresh_ports(self, preferred: str | None = None) -> None:
        # [AGENT_CHANGE_BEGIN] 2026-09-12 101串口保存生效
        want = (preferred if preferred is not None else self.port.currentText()).strip()
        ports = [p.get("port") for p in (self.api.list_serial_ports() or []) if p.get("port")]
        if want and want not in ports:
            ports = [want] + ports
        if not ports:
            ports = [want or "COM1"]
        self.port.blockSignals(True)
        try:
            self.port.clear()
            self.port.addItems(ports)
            self.port.setCurrentText(want if want in ports else ports[0])
        finally:
            self.port.blockSignals(False)
        # [AGENT_CHANGE_END] 2026-09-12 101串口保存生效

    def _on_addr_changed(self, _text: str) -> None:
        if not self._addr_prog:
            self._addr_user = True

    def _on_mode_changed(self, _text: str = "") -> None:
        is_hainan = self.mode.currentText() == MODE_LABELS["hainan"]
        self.center.setEnabled(is_hainan)
        self.center_hint.setEnabled(is_hainan)
        if not self._addr_user:
            target = "1" if is_hainan else "2"
            if self.addr_size.currentText() != target:
                self._addr_prog = True
                self.addr_size.setCurrentText(target)
                self._addr_prog = False

    def save_params(self) -> None:
        link_mode = LABEL_MODES.get(self.mode.currentText(), "unbalanced")
        try:
            data = {
                "serial_port": self.port.currentText().strip() or "COM1",
                "baudrate": int(self.baud.currentText() or 9600),
                "serial_parity": self.parity.currentText() or "N",
                "stopbits": int(self.stopbits.currentText() or 1),
                "link_addr": int(self.link_addr.text() or 1),
                "addr_size": int(
                    self.addr_size.currentText() or (1 if link_mode == "hainan" else 2)
                ),
                "poll_period": float(self.poll.text() or 1.0),
                "link_ack_timeout": float(self.ack.text() or 10.0),
                "ioa_size_101": int(self.ioa_size.currentText() or 2),
                "link_mode": link_mode,
                "balanced": link_mode == "balanced",
                "center_id": int(self.center.text() or 1),
                "ignore_fcb_error": bool(self.ignore_fcb.isChecked()),
                "tx_delay_ms": float(self.tx_delay.text() or 0),
                "protocol": "101",  # 保存 101 参数即确认使用 101
            }
            self.api.update_session(self.sid, data)
        except ValueError as e:
            error(self, "参数错误", str(e))
            return
        self.win.load_selected_into_form()
        self.win.set_status(f"101 参数已保存（{data['serial_port']} {data['baudrate']}）")
        self.accept()


class Params104Dialog(QDialog):
    """104 控制 / 超时 / 地址长度参数（KW-2200 风格）。"""

    ROWS = [
        ("T0 连接超时(秒)", "t0", 30.0, float),
        ("T1 发送/测试超时(秒)", "t1", 15.0, float),
        ("T2 确认超时(秒)", "t2", 10.0, float),
        ("T3 空闲测试超时(秒)", "t3", 20.0, float),
        ("K 未确认I帧上限", "k", 12, int),
        ("W 触发S确认帧数", "w", 8, int),
        ("链路应答超时(秒)", "link_ack_timeout", 10.0, float),
        ("远控命令超时(秒)", "cmd_timeout", 30.0, float),
        ("发送延时(毫秒)", "tx_delay_ms", 0.0, float),
    ]

    def __init__(self, win, sid: str, sess: dict) -> None:
        super().__init__(win)
        self.win = win
        self.api = win.api
        self.sid = sid
        self.setWindowTitle(f"104 参数设置 - {sess.get('name')}")
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        root.addWidget(muted_label("104 控制 / 超时（保存后重新连接生效）"))

        # [AGENT_CHANGE 2026-09-12] 改两列网格：13 个字段单列排下来窗口又高又窄（400x596）、
        # 右侧还留一片空白。两列后每列 = 标签 + 120 宽控件，控件宽度不变、不会挤。
        pairs: list = []
        self.entries: dict = {}
        for label, key, default, _conv in self.ROWS:
            ed = _line(sess.get(key, default))
            self.entries[key] = ed
            pairs.append((label, ed))

        self.cot_size = _combo(["1", "2"], sess.get("cot_size", 2))
        self.ca_size = _combo(["1", "2"], sess.get("ca_size", 2))
        self.ioa_size = _combo(["2", "3"], sess.get("ioa_size", 3))
        self.read_cot = _combo(["5", "6"], sess.get("read_cot", 6))
        pairs += [
            ("传送原因长度(字节)", self.cot_size),
            ("公共地址长度(字节)", self.ca_size),
            ("信息体地址长度(字节)", self.ioa_size),
            ("读命令(定值召唤)COT", self.read_cot),
        ]

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)
        for i, (label, w) in enumerate(pairs):
            row, col = divmod(i, 2)
            grid.addWidget(field_label(label), row, col * 2, Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(w, row, col * 2 + 1)
        root.addLayout(grid)

        self.auto_reconnect = CheckBox("超时断线自动重连")
        self.auto_reconnect.setChecked(bool(sess.get("auto_reconnect", True)))
        root.addWidget(self.auto_reconnect)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(make_button("保存", self.save_params, variant="primary"))
        btns.addWidget(make_button("取消", self.reject))
        root.addLayout(btns)
        fit_dialog(self, win, min_w=460, min_h=240)

    def save_params(self) -> None:
        try:
            data = {}
            for label, key, _default, conv in self.ROWS:
                del label
                data[key] = conv(self.entries[key].text() or 0)
            data["cot_size"] = int(self.cot_size.currentText())
            data["ca_size"] = int(self.ca_size.currentText())
            data["ioa_size"] = int(self.ioa_size.currentText())
            data["read_cot"] = int(self.read_cot.currentText())
            data["auto_reconnect"] = bool(self.auto_reconnect.isChecked())
            self.api.update_session(self.sid, data)
        except ValueError as e:
            error(self, "参数错误", str(e))
            return
        self.accept()
        info(self.win, "已保存", "参数已保存到当前主站，重新连接后生效")


class DeviceParamsDialog(QDialog):
    """设备参数：总召 / 校时 / 心跳周期（心跳仅 101 显示）。"""

    def __init__(self, win, sid: str, sess: dict, is101: bool) -> None:
        super().__init__(win)
        self.win = win
        self.api = win.api
        self.sid = sid
        self.is101 = is101
        self.setWindowTitle(f"设备参数 - {sess.get('name')}")
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        root.addWidget(muted_label("周期任务（保存后重新连接生效；0=禁用）"))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        self.clock = _line(sess.get("clock_period", 10), width=100)
        form.addRow(field_label("时钟同步周期（分钟）"), self.clock)
        self.gi = _line(sess.get("gi_period_min", 15), width=100)
        form.addRow(field_label("总召唤周期（分钟）"), self.gi)
        self.heartbeat = _line(sess.get("heartbeat_period", 30), width=100)
        if is101:
            form.addRow(field_label("心跳测试周期（秒）"), self.heartbeat)
        root.addLayout(form)
        if is101:
            root.addWidget(muted_label("心跳：测试链路 FC=2（平衡帧 10 D2/F2 …）"))

        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(make_button("保存", self.save_params, variant="primary"))
        btns.addWidget(make_button("取消", self.reject))
        root.addLayout(btns)
        fit_dialog(self, win, min_w=360, min_h=170 if is101 else 150)

    def save_params(self) -> None:
        try:
            data = {
                "clock_period": int(self.clock.text() or 0),
                "gi_period_min": int(self.gi.text() or 0),
            }
            if self.is101:
                data["heartbeat_period"] = int(self.heartbeat.text() or 0)
            self.api.update_session(self.sid, data)
        except ValueError as e:
            error(self, "参数错误", str(e))
            return
        self.accept()
        self.win.set_status("设备参数已保存（重新连接后生效）")


# [AGENT_CHANGE_BEGIN] 2026-09-11 Fluent 试水：分组标题 + 若干 RadioButton 成一列
def choice_column(title: str, options, group: QButtonGroup, checked: int = 0) -> QVBoxLayout:
    col = QVBoxLayout()
    col.setSpacing(8)
    title_label = BodyLabel(title)
    # 顶部对齐：不加 Fixed 时 QHBoxLayout 会把各列拉到等高，QLabel 吸收多余高度后文字
    # 垂直居中，导致各列标题不在同一水平线上（实测高度 76 vs 108、错位 16px）。
    title_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    col.addWidget(title_label)
    for i, text in enumerate(options):
        rb = RadioButton(text)
        rb.setChecked(i == checked)
        group.addButton(rb, i)
        col.addWidget(rb)
    col.addStretch(1)
    return col
# [AGENT_CHANGE_END] 2026-09-11 Fluent 试水


class RemoteDialog(QDialog):
    """遥控操作窗口：遥控行为（选择/执行/撤销）+ 遥控动作（分/合）。

    [AGENT_CHANGE_BEGIN] 2026-09-11 Fluent Widgets 试水
    控件换用 qfluentwidgets（RadioButton / PushButton / PrimaryPushButton / BodyLabel，
    失败提示改用 Fluent MessageBox）；容器仍是 QDialog，保持 setModal / fit_dialog /
    accept-reject 以及 _behavior()/_action() 取值逻辑不变，便于与 Tk 版逐条复核。
    [AGENT_CHANGE_END]
    """

    BEHAVIORS = ("选择", "执行", "撤销")

    def __init__(self, win, ioa: int, name: str, tid: int) -> None:
        super().__init__(win)
        self.win = win
        self.api = win.api
        self.ioa = int(ioa)
        self.name = name or "遥控分合闸"
        self.kind = "dc" if int(tid) == 46 else "sc"
        self.setWindowTitle(f"遥控 - {ioa} ({self.name})")
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        body = QHBoxLayout()
        body.setSpacing(28)

        self.behavior_group = QButtonGroup(self)
        body.addLayout(choice_column("遥控行为", self.BEHAVIORS, self.behavior_group, checked=0))

        self.action_group = QButtonGroup(self)
        body.addLayout(choice_column("遥控动作", ("分", "合"), self.action_group, checked=1))

        btns = QVBoxLayout()
        btns.setSpacing(8)
        ok = PrimaryPushButton("确定")
        ok.clicked.connect(lambda _=False: self.do_ok())
        cancel = PushButton("取消")
        cancel.clicked.connect(lambda _=False: self.reject())
        btns.addWidget(ok)
        btns.addWidget(cancel)
        btns.addStretch(1)
        body.addLayout(btns)
        body.addStretch(1)
        root.addLayout(body)

        self.hint = hint_label("")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        # [AGENT_CHANGE 2026-09-12] min_w 收到 300：内容约 290 宽，原来 420 多出的宽度全堆在右侧
        fit_dialog(self, win, min_w=300, min_h=150)

    def _behavior(self) -> str:
        btn = self.behavior_group.checkedButton()
        return btn.text() if btn is not None else "选择"

    def _action(self) -> str:
        btn = self.action_group.checkedButton()
        return btn.text() if btn is not None else "合"

    def do_ok(self) -> None:
        self.win.apply_form_to_session()
        action = self._action()
        behavior = self._behavior()
        val = 1 if action == "合" else 0
        if behavior == "撤销":
            r = self.api.cancel_command(self.ioa, self.kind)
        elif behavior == "选择":
            r = (
                self.api.double_command(self.ioa, val, True)
                if self.kind == "dc"
                else self.api.single_command(self.ioa, bool(val), True)
            )
        else:
            r = (
                self.api.double_command(self.ioa, val, False)
                if self.kind == "dc"
                else self.api.single_command(self.ioa, bool(val), False)
            )
        if not r.get("ok"):
            code = r.get("code", "")
            text = f"[{code}] {r.get('error')}" if code else str(r.get("error", ""))
            MessageBox("失败", text, self).exec()
            return
        # 发送后不自动关闭弹窗，提示用户等待/手动关闭
        self.hint.setText(f"已发送：{behavior} {action}（等待从站回执，可手动关闭）")


class StatsDialog(QDialog):
    """四遥统计（遥信/遥测/遥控/遥调 四个页签）。"""

    COL_DEFS = {
        "遥信": [("ioa", "点号", 70), ("name", "名称", 200), ("change", "变位次数", 90), ("soe", "SOE数量", 90)],
        "遥测": [("ioa", "点号", 70), ("name", "名称", 200), ("up", "越上限", 80), ("down", "越下限", 80),
                 ("dead", "突变死区", 90), ("still", "不变告警", 90)],
        "遥控": [("ioa", "点号", 70), ("name", "名称", 200), ("seloff", "预选分", 80), ("exeoff", "执行分", 80),
                 ("selon", "预选合", 80), ("exeon", "执行合", 80)],
        "遥调": [("ioa", "点号", 70), ("name", "名称", 200), ("preset", "预置", 80), ("exec", "执行(固化)", 100),
                 ("cancel", "撤销", 80)],
    }
    SUM_DEFS = {
        "遥信": [("change", "总变位次数"), ("soe", "SOE数量")],
        "遥测": [("up", "总越上限次数"), ("down", "总越下限次数"), ("dead", "突变死区次数"),
                 ("still", "长期不变告警次数")],
        "遥控": [("selon", "总预选合次数"), ("seloff", "总预选分次数"), ("exeon", "总执行合次数"),
                 ("exeoff", "总执行分次数")],
        "遥调": [("preset", "总预置次数"), ("exec", "总执行次数"), ("cancel", "总撤销次数")],
    }

    def __init__(self, win, sid: str) -> None:
        super().__init__(win)
        self.win = win
        self.api = win.api
        self.sid = sid
        self.setWindowTitle(f"四遥统计 - {win.session_name(sid)}")
        self.setModal(True)
        # [AGENT_CHANGE_2026-09-11] 收紧尺寸 + Fluent 表格/标签
        self.resize(660, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(8)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.pages: dict = {}
        for cat, cols in self.COL_DEFS.items():
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(8, 8, 8, 8)
            lay.setSpacing(6)
            sum_bar = QHBoxLayout()
            labels = {}
            for key, title in self.SUM_DEFS[cat]:
                lab = BodyLabel(f"{title}：0次")
                sum_bar.addWidget(lab)
                labels[key] = lab
            sum_bar.addStretch(1)
            lay.addLayout(sum_bar)
            table = TableWidget()
            table.setColumnCount(len(cols))
            table.setHorizontalHeaderLabels([c[1] for c in cols])
            for i, (_k, _t, width) in enumerate(cols):
                table.setColumnWidth(i, width)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
            table.horizontalHeader().setStretchLastSection(True)
            table.verticalHeader().setVisible(False)
            table.setAlternatingRowColors(True)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            lay.addWidget(table, 1)
            self.tabs.addTab(page, cat)
            self.pages[cat] = {"table": table, "labels": labels}

        btns = QHBoxLayout()
        btns.addWidget(make_button("刷新", self.fill))
        btns.addStretch(1)
        btns.addWidget(make_button("确定", self.accept, variant="primary"))
        btns.addWidget(make_button("取消", self.reject))
        root.addLayout(btns)
        self.fill()

    def fill(self) -> None:
        rows = (self.api.get_stats(self.sid) or {}).get("rows") or []
        for cat, page in self.pages.items():
            table = page["table"]
            table.setRowCount(0)
            totals: dict = {}
            col_keys = [c[0] for c in self.COL_DEFS[cat]]
            for row in rows:
                if row.get("cat") != cat:
                    continue
                index = table.rowCount()
                table.insertRow(index)
                for col, key in enumerate(col_keys):
                    table.setItem(index, col, QTableWidgetItem(str(row.get(key, 0))))
                for key, _title in self.SUM_DEFS[cat]:
                    totals[key] = totals.get(key, 0) + int(row.get(key) or 0)
            titles = dict(self.SUM_DEFS[cat])
            for key, lab in page["labels"].items():
                lab.setText(f"{titles[key]}：{totals.get(key, 0)}次")


# ---- 打开入口 ----


def _require_session(win, sid: str, sess: Optional[dict] = None) -> Optional[dict]:
    """参数对话框都需要一个当前主站，未选中时按 Tk 版口径提示。"""
    if not sid:
        warn(win, "提示", "请先选择主站")
        return None
    data = sess if sess is not None else (win.session(sid) or {})
    if not data:
        warn(win, "提示", "请先选择主站")
        return None
    return data


def open_101_params(win, sid: str, sess: Optional[dict] = None) -> None:
    data = _require_session(win, sid, sess)
    if data is None:
        return
    Params101Dialog(win, sid, data).exec()


def open_104_params(win, sid: str, sess: Optional[dict] = None) -> None:
    data = _require_session(win, sid, sess)
    if data is None:
        return
    Params104Dialog(win, sid, data).exec()


def open_device_params(win, sid: str) -> None:
    data = _require_session(win, sid)
    if data is None:
        return
    is101 = (data.get("protocol") or "104") == "101" or win.is_101_selected()
    DeviceParamsDialog(win, sid, data, is101).exec()


def open_remote_dialog(win, ioa: int, name: str, tid: int) -> None:
    dlg = RemoteDialog(win, ioa, name, tid)
    dlg.exec()


def open_stats_dialog(win, sid: str) -> None:
    dlg = StatsDialog(win, sid)
    dlg.exec()


# [AGENT_CHANGE_BEGIN] 2026-09-12 报文解析对话框
class FrameParseDialog(QDialog):
    """报文帧数据：左侧完整解析（至信息体），右侧 HEX；点选字段高亮对应字节。
    监视窗口摘要仍只显示到公共地址；本对话框做全量解析。
    """

    def __init__(self, win, raw: bytes, sess: dict, title_ts: str = "") -> None:
        super().__init__(win)
        self.win = win
        self.raw = bytes(raw or b"")
        self.setWindowTitle(f"报文帧数据: {title_ts}" if title_ts else "报文帧数据")
        self.setModal(False)
        self.resize(780, 420)

        from protocol.frame_inspect import inspect_frame, session_sizes

        sizes = session_sizes(sess)
        root = inspect_frame(self.raw, **sizes)

        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名称", "值", "描述"])
        self.tree.setColumnWidth(0, 120)
        self.tree.setColumnWidth(1, 100)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)

        self.hex_view = QTextEdit()
        self.hex_view.setReadOnly(True)
        self.hex_view.setLineWrapMode(QTextEdit.WidgetWidth)
        self.hex_view.setFontFamily("Consolas")
        self.hex_text = " ".join(f"{b:02X}" for b in self.raw)
        self.hex_view.setPlainText(self.hex_text)

        self._fill_tree(None, root)
        self.tree.expandAll()
        self.tree.itemSelectionChanged.connect(self._on_select)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

        split.addWidget(self.tree)
        split.addWidget(self.hex_view)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        hint = muted_label(
            f"规约={sizes['protocol']}  COT={sizes['cot_size']}B  "
            f"CA={sizes['ca_size']}B  IOA={sizes['ioa_size']}B"
            + (f"  链路地址={sizes['addr_size']}B" if sizes["protocol"] == "101" else "")
            + "  （完整解析）"
        )
        lay.addWidget(hint)
        lay.addWidget(split, 1)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(make_button("关闭", self.accept))
        lay.addLayout(btns)

    def _fill_tree(self, parent: Optional[QTreeWidgetItem], node) -> None:
        item = QTreeWidgetItem([node.name, node.value, node.desc])
        item.setData(0, Qt.UserRole, (int(node.offset), int(node.length)))
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        for ch in node.children or []:
            self._fill_tree(item, ch)

    def _on_select(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        off, length = items[0].data(0, Qt.UserRole) or (0, 0)
        self._highlight(int(off or 0), int(length or 0))

    def _highlight(self, offset: int, length: int) -> None:
        """按字节偏移高亮右侧 HEX（每字节 'XX '）。"""
        self.hex_view.setExtraSelections([])
        if length <= 0 or not self.raw:
            return
        start = max(0, offset) * 3
        end = min(len(self.raw), offset + length) * 3
        if end <= start:
            return
        end = min(end, len(self.hex_text))
        while end > start and self.hex_text[end - 1] == " ":
            end -= 1
        cursor = self.hex_view.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        sel = QTextEdit.ExtraSelection()
        sel.cursor = cursor
        sel.format.setBackground(QColor("#1d4ed8"))
        sel.format.setForeground(QColor("#ffffff"))
        self.hex_view.setExtraSelections([sel])
        self.hex_view.setTextCursor(cursor)
        self.hex_view.ensureCursorVisible()


def open_frame_parse(win, raw: bytes, sess: dict, title_ts: str = "") -> None:
    if not raw:
        warn(win, "提示", "无有效报文可解析")
        return
    FrameParseDialog(win, raw, sess or {}, title_ts).exec()


# [AGENT_CHANGE_END] 2026-09-12 报文解析对话框


__all__ = [
    "open_101_params",
    "open_104_params",
    "open_device_params",
    "open_remote_dialog",
    "open_stats_dialog",
    "open_frame_parse",
]
# [AGENT_CHANGE_END] 2026-09-11 Qt对话框
