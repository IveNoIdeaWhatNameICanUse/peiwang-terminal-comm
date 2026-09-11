# [AGENT_CHANGE_BEGIN] 2026-09-11 Qt界面公共组件
"""Qt 界面公共组件：事件桥、卡片/按钮/标签工厂、点表格式化与提示框包装。"""
from __future__ import annotations

import queue
from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# [AGENT_CHANGE_BEGIN] 2026-09-11 Fluent Widgets 全面接入：控件工厂改用 qfluentwidgets
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)
# [AGENT_CHANGE_END] 2026-09-11 Fluent Widgets 全面接入

from core.project import category_for_type

CATEGORIES = ["遥信", "遥测", "遥控", "遥调"]

# 与 app/tk_shell.py 保持一致的显示表
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
# 国网 202 定值对象的数据类型编码（附录D TLV）
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
VARIANT_HINTS = {
    "广西": "点表分段：遥信1H/遥测4001H/遥调5001H/遥控6001H（以现场发码表为准）",
    "南网": "参数整定：召唤→预置→固化/撤销（SBO 两段式）",
    "国网": "支持参数全召唤/分组召唤；总召唤应答 SQ=1",
}


class EventBridge(QObject):
    """把 api.set_ui_push（后台线程）推来的事件搬到主线程处理。

    与 Tk 版 `poll_events()`（队列 + 200ms 轮询）等效，只是定时器换成了 QTimer。
    """

    event = Signal(dict)

    def __init__(self, interval_ms: int = 200, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._drain)

    def attach(self, api) -> None:
        api.set_ui_push(self.push)
        self._timer.start()

    def push(self, ev: dict) -> None:
        """线程安全：仅入队，由主线程定时取走。"""
        try:
            self._q.put(ev)
        except Exception:
            pass

    def _drain(self) -> None:
        while True:
            try:
                ev = self._q.get_nowait()
            except queue.Empty:
                return
            try:
                self.event.emit(ev)
            except Exception:
                pass


# ---- 控件工厂 ----


def set_variant(w: QWidget, name: str, value) -> None:
    """设置动态属性并刷新样式（配合 QSS 的 [variant="primary"] 选择器）。"""
    w.setProperty(name, value)
    st = w.style()
    st.unpolish(w)
    st.polish(w)


def make_button(
    text: str,
    on_click: Optional[Callable[[], None]] = None,
    variant: str = "",
    tooltip: str = "",
    parent: Optional[QWidget] = None,
) -> QPushButton:
    # [AGENT_CHANGE_BEGIN] 2026-09-11 Fluent：primary -> PrimaryPushButton，其余 PushButton
    b = PrimaryPushButton(text, parent) if variant == "primary" else PushButton(text, parent)
    # [AGENT_CHANGE_END] 2026-09-11
    if on_click is not None:
        b.clicked.connect(lambda _=False: on_click())
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def menu_button(
    text: str, on_click: Optional[Callable[[], None]] = None, parent: Optional[QWidget] = None
) -> QPushButton:
    """左侧功能菜单按钮（可选中高亮）。"""
    b = PushButton(text, parent)
    b.setObjectName("MenuButton")
    b.setCheckable(True)
    b.setCursor(Qt.PointingHandCursor)
    if on_click is not None:
        b.clicked.connect(lambda _=False: on_click())
    return b


def card(title: str = "", parent: Optional[QWidget] = None) -> tuple:
    """返回 (Fluent 卡片 CardWidget, 内容 QVBoxLayout)。"""
    frame = CardWidget(parent)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 16)
    lay.setSpacing(10)
    if title:
        lay.addWidget(StrongBodyLabel(title))
    return frame, lay


def field_label(text: str) -> QLabel:
    return BodyLabel(text)


def muted_label(text: str) -> QLabel:
    return CaptionLabel(text)


def hint_label(text: str = "") -> QLabel:
    return CaptionLabel(text)


def make_badge(text: str = "未连接", parent: Optional[QWidget] = None) -> QLabel:
    lab = QLabel(text, parent)
    lab.setObjectName("Badge")
    set_badge(lab, None, text)
    return lab


def set_badge(lab: QLabel, on: Optional[bool], text: str) -> None:
    lab.setText(text)
    set_variant(lab, "on", "true" if on else "false")
    st = lab.style()
    st.unpolish(lab)
    st.polish(lab)


def row(*widgets: QWidget, spacing: int = 6, margins: tuple = (0, 0, 0, 0)) -> QWidget:
    """把若干控件排成一行。"""
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for w in widgets:
        lay.addWidget(w)
    lay.addStretch(1)
    return holder


# ---- 点表格式化（与 Tk 版一致的显示口径）----


def point_category(p: dict) -> str:
    cat = p.get("category") or ""
    if cat in CATEGORIES:
        return cat
    return category_for_type(p.get("type_id") or 0)


def fmt_ioa(value, hex_mode: bool) -> str:
    v = int(value or 0)
    return f"{v:X}H" if hex_mode else str(v)


def type_label(p: dict) -> str:
    dt = p.get("data_type") or 0
    tid = p.get("type_id") or 0
    if dt:
        return DTYPE_NAMES.get(dt, f"数据类型0x{dt:02X}")
    return TYPE_LABELS.get(tid, str(tid))


def fmt_val(p: dict) -> str:
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
    if dt in (0x01, 0x02, 0x04, 0x20, 0x21, 0x23, 0x24, 0x25, 0x2B, 0x2D):
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        if isinstance(v, int) and not isinstance(v, bool):
            return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# ---- 提示框包装（统一父窗口与文案）----


def info(parent: Optional[QWidget], title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def warn(parent: Optional[QWidget], title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def error(parent: Optional[QWidget], title: str, text: str) -> None:
    QMessageBox.critical(parent, title, text)


def ask(parent: Optional[QWidget], title: str, text: str) -> bool:
    return (
        QMessageBox.question(
            parent, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        == QMessageBox.Yes
    )


def show_result(parent: Optional[QWidget], r: dict, title: str = "失败") -> bool:
    """统一处理 api 返回；失败时弹窗。返回是否成功。"""
    if r.get("ok"):
        return True
    code = r.get("code", "")
    error(parent, title, f"[{code}] {r.get('error')}" if code else str(r.get("error", "")))
    return False


def fit_dialog(dlg: QDialog, parent: Optional[QWidget] = None, min_w: int = 420, min_h: int = 300) -> None:
    """对话框按内容自适应尺寸并居中于父窗口。"""
    try:
        dlg.adjustSize()
        hint = dlg.sizeHint()
        w = max(int(min_w), hint.width())
        h = max(int(min_h), hint.height())
        dlg.resize(w, h)
        dlg.setMinimumSize(w, h)
        anchor = parent.window() if parent is not None else None
        if anchor is not None:
            geo = anchor.geometry()
            x = geo.x() + max(0, (geo.width() - w) // 2)
            y = geo.y() + max(0, (geo.height() - h) // 3)
            dlg.move(x, y)
    except Exception:
        pass
# [AGENT_CHANGE_END] 2026-09-11 Qt界面公共组件
