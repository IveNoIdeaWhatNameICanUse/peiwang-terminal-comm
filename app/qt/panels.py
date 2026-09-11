# [AGENT_CHANGE_BEGIN] 2026-09-11 Qt功能面板
"""四遥状态（点表）/ 报文监视 / 事件记录 面板。

对照 app/tk_shell.py 的 build_points_panel / build_monitor_panel / build_event_panel。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# [AGENT_CHANGE_BEGIN] 2026-09-11 Fluent Widgets：点表/事件表/输入控件改用 Fluent
# 注意 TableWidget.__init__(parent=None) 不收行列数，列数要用 setColumnCount 设。
from qfluentwidgets import CheckBox, ComboBox, LineEdit, TableWidget
# [AGENT_CHANGE_END] 2026-09-11

from app.qt import theme
from app.qt.common import (
    CATEGORIES,
    TYPE_KINDS,
    error,
    field_label,
    fmt_ioa,
    fmt_val,
    hint_label,
    info,
    make_button,
    muted_label,
    point_category,
    type_label,
)


class PointsPanel(QWidget):
    """四遥分类点表 + 地址显示 + 添加/导入/删除。"""

    COLUMNS = [
        ("ioa", "信息体地址(IOA)", 110),
        ("name", "名称", 160),
        ("type", "类型", 120),
        ("value", "值", 120),
        ("q", "品质", 70),
        ("modval", "修改值", 100),
    ]
    MODVAL_COL = 5

    def __init__(self, win, sid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.win = win
        self.api = win.api
        self.sid = sid
        self.modvals: dict = {}
        self.points: dict = {}
        self._loading = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.cat_tabs = QTabWidget()
        self.tables: dict = {}
        for cat in CATEGORIES:
            table = TableWidget()  # [AGENT_CHANGE_2026-09-11] Fluent 表格
            table.setColumnCount(len(self.COLUMNS))
            table.setHorizontalHeaderLabels([c[1] for c in self.COLUMNS])
            for i, (_key, _title, width) in enumerate(self.COLUMNS):
                table.setColumnWidth(i, width)
            header = table.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.Interactive)
            header.setStretchLastSection(True)
            table.verticalHeader().setVisible(False)
            table.setAlternatingRowColors(True)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setWordWrap(False)
            table.cellDoubleClicked.connect(lambda r, c, t=table: self._on_double(t, r, c))
            table.itemChanged.connect(lambda item, t=table: self._on_item_changed(t, item))
            self.tables[cat] = table
            self.cat_tabs.addTab(table, cat)
        lay.addWidget(self.cat_tabs, 1)

        addr_bar = QHBoxLayout()
        self.hex_check = CheckBox("十六进制地址显示(H)")
        self.hex_check.toggled.connect(lambda _=False: self.reload())
        addr_bar.addWidget(self.hex_check)
        addr_bar.addWidget(
            muted_label("（遥信自 1H 起、遥测自 4001H 起、遥控自 6001H 起、遥调自 5001H 起）")
        )
        addr_bar.addStretch(1)
        lay.addLayout(addr_bar)

        add_bar = QHBoxLayout()
        self.n_ioa = LineEdit()
        self.n_ioa.setText("1")
        self.n_ioa.setFixedWidth(80)
        self.n_name = LineEdit()
        self.n_name.setFixedWidth(140)
        self.n_type = ComboBox()
        self.n_type.addItems(list(TYPE_KINDS))
        self.n_type.setFixedWidth(150)
        add_bar.addWidget(field_label("IOA"))
        add_bar.addWidget(self.n_ioa)
        add_bar.addWidget(field_label("名称"))
        add_bar.addWidget(self.n_name)
        add_bar.addWidget(self.n_type)
        add_bar.addWidget(make_button("添加点", self.add_point))
        add_bar.addWidget(make_button("导入CSV发码表", self.import_csv))
        add_bar.addWidget(make_button("删除选中点", self.del_points))
        add_bar.addStretch(1)
        lay.addLayout(add_bar)

    # ---- 数据 ----

    def reload(self) -> None:
        data = self.api.get_project()
        pts_raw = (data.get("points_by_session") or {}).get(self.sid, [])
        pts = sorted(pts_raw, key=lambda p: int(p.get("ioa") or 0))
        hex_mode = self.hex_check.isChecked()
        self._loading = True
        try:
            for table in self.tables.values():
                table.setRowCount(0)
            self.points = {}
            for p in pts:
                cat = point_category(p)
                table = self.tables.get(cat) or self.tables["遥信"]
                ioa = int(p.get("ioa") or 0)
                self.points[ioa] = p
                quality = p.get("quality")
                values = [
                    fmt_ioa(ioa, hex_mode),
                    p.get("name") or "",
                    type_label(p),
                    fmt_val(p),
                    "" if quality is None else str(quality),
                    str(self.modvals.get(ioa, "")),
                ]
                row = table.rowCount()
                table.insertRow(row)
                for col, text in enumerate(values):
                    item = QTableWidgetItem(str(text))
                    item.setData(Qt.UserRole, ioa)
                    if col != self.MODVAL_COL:
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    table.setItem(row, col, item)
        finally:
            self._loading = False

    def selected_ioas(self, cat: str = "") -> list:
        out = []
        for name, table in self.tables.items():
            if cat and name != cat:
                continue
            for index in table.selectionModel().selectedRows():
                item = table.item(index.row(), 0)
                if item is not None:
                    out.append(int(item.data(Qt.UserRole) or 0))
        return out

    def modval_items(self) -> list:
        """返回 [(ioa, 值或 None)]：遥调点表选中行 + 修改值列。"""
        out = []
        for ioa in self.selected_ioas("遥调"):
            raw = (self.modvals or {}).get(ioa, "")
            try:
                val = float(raw)
            except (TypeError, ValueError):
                val = None
            out.append((ioa, val))
        return out

    def clear_modvals(self) -> None:
        self.modvals = {}
        self._loading = True
        try:
            for table in self.tables.values():
                for row in range(table.rowCount()):
                    item = table.item(row, self.MODVAL_COL)
                    if item is not None:
                        item.setText("")
        finally:
            self._loading = False

    def focus_ioa(self, ioa: int) -> None:
        """切到该点所在分类页并选中（事件记录双击定位用）。"""
        p = self.points.get(int(ioa))
        if not p:
            return
        cat = point_category(p)
        table = self.tables.get(cat)
        if table is None:
            return
        self.cat_tabs.setCurrentWidget(table)
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and int(item.data(Qt.UserRole) or 0) == int(ioa):
                table.clearSelection()
                table.selectRow(row)
                table.scrollToItem(item, QAbstractItemView.PositionAtCenter)
                return

    # ---- 表格交互 ----

    def _on_double(self, table: QTableWidget, row: int, col: int) -> None:
        """双击：遥控点弹遥控窗口；“修改值”列就地编辑。"""
        item = table.item(row, self.MODVAL_COL)
        if col == self.MODVAL_COL:
            if item is not None:
                table.editItem(item)
            return
        first = table.item(row, 0)
        if first is None:
            return
        ioa = int(first.data(Qt.UserRole) or 0)
        p = self.points.get(ioa) or {}
        if point_category(p) != "遥控":
            return
        self.win.open_remote_dialog(ioa, p.get("name") or "", int(p.get("type_id") or 0))

    def _on_item_changed(self, table: QTableWidget, item: QTableWidgetItem) -> None:
        if self._loading or item is None or item.column() != self.MODVAL_COL:
            return
        ioa = item.data(Qt.UserRole)
        if ioa is None:
            return
        self.modvals[int(ioa)] = item.text().strip()

    # ---- 点表增删改 ----

    def add_point(self) -> None:
        try:
            ioa = int(self.n_ioa.text() or "0")
        except ValueError:
            error(self.win, "参数错误", "IOA 必须是整数")
            return
        from core.project import category_for_type

        tid = TYPE_KINDS.get(self.n_type.currentText(), 1)
        self.api.upsert_point(
            {
                "ioa": ioa,
                "type_id": tid,
                "name": self.n_name.text(),
                "category": category_for_type(tid),
            },
            sid=self.sid,
        )
        self.win.reload_points()

    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self.win, "选择发码表 CSV 文件", "", "CSV 文件 (*.csv);;所有文件 (*.*)"
        )
        if not path:
            return
        r = self.api.import_points_csv(path, sid=self.sid)
        if r.get("ok"):
            msg = f"已导入 {r.get('count', 0)} 个点"
            if r.get("skipped"):
                msg += f"，跳过 {r.get('skipped')} 行"
            info(self.win, "导入成功", msg)
            self.win.reload_points()
        else:
            error(self.win, "导入失败", r.get("error", ""))

    def del_points(self) -> None:
        ids = self.selected_ioas()
        if not ids:
            from app.qt.common import warn

            warn(self.win, "提示", "请先在点表中选中要删除的点")
            return
        for ioa in ids:
            self.api.remove_point(ioa, sid=self.sid)
        self.win.reload_points()


class MonitorPanel(QWidget):
    """报文监视：深色终端文本 + 冻结/复制/清空/导出。"""

    def __init__(self, win, sid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.win = win
        self.api = win.api
        self.sid = sid
        self.frozen = False
        self.pending: list = []
        self.segments: list = []   # (起始块号, 结束块号(不含))
        self.auto_scroll = True

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        bar = QHBoxLayout()
        bar.addWidget(field_label(f"会话：{win.session_name(sid)}"))
        self.status = hint_label("")
        bar.addWidget(self.status)
        bar.addWidget(make_button("清空", self.clear))
        self.scroll_check = CheckBox("自动滚动")
        self.scroll_check.setChecked(True)
        self.scroll_check.toggled.connect(self._on_auto_scroll)
        bar.addWidget(self.scroll_check)
        bar.addWidget(make_button("导出报文", self.export))
        bar.addStretch(1)
        lay.addLayout(bar)

        self.text = QTextEdit()
        self.text.setObjectName("Monitor")
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QTextEdit.NoWrap)
        self.text.setContextMenuPolicy(Qt.CustomContextMenu)
        self.text.customContextMenuRequested.connect(self._on_context_menu)
        self.text.viewport().installEventFilter(self)
        lay.addWidget(self.text, 1)

    # ---- 事件过滤器：左键点击选中整条报文 ----

    def eventFilter(self, obj, event):
        if obj is self.text.viewport() and event.type() == event.Type.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                pos = event.pos()
                QTimer.singleShot(0, lambda p=pos: self._select_block_at(p))
        return super().eventFilter(obj, event)

    def _select_block_at(self, pos) -> None:
        block_no = self.text.cursorForPosition(pos).blockNumber()
        seg = None
        for candidate in self.segments:
            if candidate[0] <= block_no < candidate[1]:
                seg = candidate
                break
        if not seg:
            return
        doc = self.text.document()
        start_block = doc.findBlockByNumber(seg[0])
        end_block = doc.findBlockByNumber(max(seg[1] - 1, seg[0]))
        if not start_block.isValid() or not end_block.isValid():
            return
        cursor = self.text.textCursor()
        cursor.setPosition(start_block.position())
        cursor.setPosition(
            end_block.position() + max(0, end_block.length() - 1), QTextCursor.KeepAnchor
        )
        self.text.setTextCursor(cursor)
        self.status.setText("已选中报文")

    # ---- 追加内容 ----

    def _append(self, block: str, out_format: Optional[QTextCharFormat] = None) -> None:
        doc = self.text.document()
        before = doc.blockCount()
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.End)
        if out_format is not None:
            cursor.insertText(block, out_format)
        else:
            cursor.insertText(block)
        after = doc.blockCount()
        start = max(0, before - 1)
        end = max(after - 1, start + 1)
        self.segments.append((start, end))
        if self.auto_scroll:
            self.text.moveCursor(QTextCursor.End)
            self.text.ensureCursorVisible()

    def _pending_or_append(self, block: str, is_log: bool = False) -> None:
        if self.frozen:
            self.pending.append((block, is_log))
            return
        self._do_append(block, is_log)

    def _do_append(self, block: str, is_log: bool = False) -> None:
        if is_log:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(theme.MONITOR_LOG))
            self._append(block, fmt)
        else:
            self._append(block)

    def append_frame(self, ev: dict) -> None:
        ts = time.strftime("%H:%M:%S", time.localtime(ev.get("ts") or time.time()))
        block = f"[{ts}] {ev.get('direction', '')} {ev.get('note', '')}\n{ev.get('hex', '')}\n\n"
        self._pending_or_append(block, is_log=False)

    def append_log(self, ev: dict) -> None:
        ts = time.strftime("%H:%M:%S", time.localtime(ev.get("ts") or time.time()))
        block = f"[{ts}] ◆ {ev.get('text', '')}\n\n"
        self._pending_or_append(block, is_log=True)

    # ---- 工具栏 ----

    def _on_auto_scroll(self, checked: bool) -> None:
        self.auto_scroll = bool(checked)
        if checked:
            self.flush_pending()
            self.text.moveCursor(QTextCursor.End)
            self.text.ensureCursorVisible()
            self.status.setText("")
        else:
            self.status.setText("已暂停自动滚动")

    def flush_pending(self) -> None:
        if self.pending:
            for block, is_log in self.pending:
                self._do_append(block, is_log)
            self.pending.clear()

    def set_frozen(self, frozen: bool) -> None:
        self.frozen = bool(frozen)
        if not frozen:
            self.flush_pending()
        self.status.setText("已冻结，新报文缓存中" if frozen else "")

    def clear(self) -> None:
        self.text.clear()
        self.pending.clear()
        self.segments.clear()
        self.status.setText("")

    def export(self) -> None:
        base = Path.home() / "Desktop"
        if not base.exists():
            base = Path.home()
        default = base / f"配网终端通讯_报文_{self.win.session_name(self.sid)}_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self.win, "导出当前主站报文", str(default), "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            Path(path).write_text(self.text.toPlainText(), encoding="utf-8")
            self.status.setText("已导出")
            info(self.win, "已导出", f"报文已保存到：\n{path}")
        except OSError as e:
            error(self.win, "导出失败", str(e))

    # ---- 右键菜单 ----

    def _on_context_menu(self, pos) -> None:
        view_pos = self.text.viewport().mapFrom(self.text, pos)
        block_no = self.text.cursorForPosition(view_pos).blockNumber()
        seg = None
        for candidate in self.segments:
            if candidate[0] <= block_no < candidate[1]:
                seg = candidate
                break
        menu = QMenu(self)
        if self.frozen:
            menu.addAction("解冻报文", lambda: self.set_frozen(False))
        else:
            menu.addAction("冻结报文", lambda: self.set_frozen(True))
        menu.addAction("复制报文", lambda: self._copy_msg(seg))
        menu.addSeparator()
        menu.addAction("清空报文", self.clear)
        menu.exec(self.text.mapToGlobal(pos))

    def _copy_msg(self, seg) -> None:
        text = self.text.textCursor().selectedText().strip()
        if not text and seg:
            doc = self.text.document()
            start_block = doc.findBlockByNumber(seg[0])
            end_block = doc.findBlockByNumber(max(seg[1] - 1, seg[0]))
            if start_block.isValid() and end_block.isValid():
                text = doc.toPlainText()[
                    start_block.position() : end_block.position() + max(0, end_block.length() - 1)
                ].strip()
        if not text:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(text)
        self.status.setText("已复制到剪贴板")


class EventsPanel(QWidget):
    """事件记录：SOE/COS/遥控/遥调，按类别着色，双击定位点表。"""

    COLUMNS = [
        ("ts", "接收时间", 150),
        ("info", "信息体", 180),
        ("content", "事件内容", 460),
        ("kind", "事件类别", 110),
    ]

    def __init__(self, win, sid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.win = win
        self.api = win.api
        self.sid = sid
        self._last_n = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        bar = QHBoxLayout()
        bar.addWidget(field_label(f"事件记录：{win.session_name(sid)}"))
        self.status = hint_label("")
        bar.addWidget(self.status)
        bar.addWidget(make_button("清空事件", self.clear))
        bar.addWidget(make_button("刷新", lambda: self.reload(silent=False)))
        bar.addWidget(muted_label("双击定位点表"))
        bar.addStretch(1)
        lay.addLayout(bar)

        self.table = TableWidget()  # [AGENT_CHANGE_2026-09-11] Fluent 表格
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in self.COLUMNS])
        for i, (_k, _t, width) in enumerate(self.COLUMNS):
            self.table.setColumnWidth(i, width)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.cellDoubleClicked.connect(self._on_double)
        lay.addWidget(self.table, 1)

        # 定时刷新（面板可见时），保证事件实时更新
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(lambda: self.reload(silent=True))
        self._timer.start()

    @staticmethod
    def tag_of(kind: str) -> str:
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

    def reload(self, silent: bool = True) -> None:
        if silent and not self.isVisible():
            return
        try:
            events = (self.api.get_events(self.sid, limit=2000) or {}).get("events") or []
        except Exception:
            return
        if len(events) == self._last_n:
            if not silent:
                self.status.setText(f"共 {len(events)} 条")
            return
        self.table.setRowCount(0)
        for ev in events:
            kind = ev.get("kind", "")
            color = QColor(theme.EVENT_COLORS.get(self.tag_of(kind), theme.TEXT))
            values = [
                str(ev.get("ts", "")),
                f"[{ev.get('ioa', '')}] {ev.get('name', '')}",
                str(ev.get("content", "")),
                str(kind),
            ]
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setForeground(color)
                item.setData(Qt.UserRole, int(ev.get("ioa") or 0))
                self.table.setItem(row, col, item)
        self._last_n = len(events)
        if not silent:
            self.status.setText(f"共 {len(events)} 条")

    def clear(self) -> None:
        self.api.clear_events(self.sid)
        self.reload(silent=False)

    def _on_double(self, row: int, _col: int) -> None:
        item = self.table.item(row, 0)
        if item is None:
            return
        self.win.locate_point(self.sid, int(item.data(Qt.UserRole) or 0))


__all__ = ["PointsPanel", "MonitorPanel", "EventsPanel"]
# [AGENT_CHANGE_END] 2026-09-11 Qt功能面板
