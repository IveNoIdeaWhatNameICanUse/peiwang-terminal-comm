# [AGENT_CHANGE_BEGIN] 2026-09-11 Qt界面主题
"""Qt 界面主题：调色板 + QSS + 中文本地化。

配色沿用 `ui/styles.css`，但界面全部由 Qt 原生绘制：
不读取任何 .html/.js/.css，不起本地 HTTP，不依赖 WebView2。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QLibraryInfo, QLocale, QObject, QRect, QSize, Qt, QTimer, QTranslator
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

# ---- 颜色（对应 ui/styles.css 的 CSS 变量）----
BG = "#f3f6f8"
PANEL = "#ffffff"
LINE = "#d7e0e6"
TEXT = "#1f2a32"
MUTED = "#6b7c88"
ACCENT = "#0f766e"
ACCENT2 = "#115e59"
DANGER = "#b91c1c"
OK = "#047857"
HINT = "#2563eb"
MONITOR_BG = "#0b1220"
MONITOR_FG = "#dbeafe"
MONITOR_LOG = "#facc15"

# 事件类别配色（对应 app/tk_shell.py 的 tree.tag_configure）
EVENT_COLORS = {
    "soe": "#15803d",   # SOE 绿
    "cos": "#b45309",   # 遥信变位 黄
    "ctrl": "#dc2626",  # 遥控 红
    "adj": "#1d4ed8",   # 遥调 蓝
    "mea": "#7c3aed",   # 遥测 紫
    "sys": "#475569",   # 链路启动/停止 灰
}

QSS = """
QWidget { color: #1f2a32; font-size: 12px; }
QMainWindow, QDialog { background: #eef3f6; }
QToolTip { background: #115e59; color: #ffffff; border: none; padding: 4px 6px; }

/* ---- 卡片 / 顶栏 ---- */
QFrame#Card { background: #ffffff; border: 1px solid #d7e0e6; border-radius: 12px; }
QFrame#TopBar { background: #ffffff; border: none; border-bottom: 1px solid #d7e0e6; }
QFrame#SideBar { background: #ffffff; border: 1px solid #d7e0e6; border-radius: 12px; }

/* ---- 文本 ---- */
QLabel { background: transparent; }
QLabel#CardTitle { color: #115e59; font-size: 14px; font-weight: 700; }
QLabel#Brand { color: #115e59; font-size: 16px; font-weight: 700; }
QLabel#Muted { color: #6b7c88; }
QLabel#FieldLabel { color: #6b7c88; font-size: 12px; }
QLabel#Hint { color: #2563eb; }
QLabel#Badge {
  border: 1px solid #d7e0e6; border-radius: 11px; padding: 4px 12px;
  background: #eef2f5; color: #6b7c88;
}
QLabel#Badge[on="true"] { background: #d1fae5; color: #047857; border-color: #a7f3d0; }
QLabel#Badge[on="false"] { background: #fee2e2; color: #b91c1c; border-color: #fecaca; }

/* ---- 按钮 / 输入控件 ----
   [AGENT_CHANGE 2026-09-12] 原来针对 QPushButton / QLineEdit / QSpinBox / QComboBox 的
   background / border / padding / min-height 规则已删除：Fluent 化后
   PushButton / LineEdit / EditableComboBox / ComboBox 分别继承 QPushButton / QLineEdit，
   会被这些规则打到 —— 实测 min-height+padding 把可编辑下拉压到 18px 高（其 dropButton
   固定 25px 高，于是溢出、箭头看起来“歪”），且按钮(24px)/输入框(18px)/下拉(25px) 高度
   互不一致。删除后各控件回到 Fluent 自身尺寸，外观由组件自绘。 ---- */

/* ---- 输入控件：仅原生 QComboBox 的下拉列表样式（Fluent ComboBox 不继承 QComboBox，
   不受影响；保留以备将来有原生下拉时使用） ---- */
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 18px; border: none; }
QComboBox QAbstractItemView {
  background: #ffffff; border: 1px solid #d7e0e6; border-radius: 8px;
  selection-background-color: #d1fae5; selection-color: #1f2a32;
  outline: none; padding: 4px 0px;   /* 左右不留内边距，选中行才能通栏到边 */
}
/* 用列表自身作为下拉（不用 QComboBoxPrivateContainer 那套弹出窗口）：
   这样才能拿到正常的细灰圆角滚动条，而不是 Fusion 在边缘画的小三角。
   选中态已由 _ComboItemDelegate 自绘，所以不再受 Fusion 高亮影响。 */
QComboBox { combobox-popup: 0; }
QComboBox QAbstractItemView QScrollBar:vertical {
  background: #ffffff; width: 10px; margin: 4px 2px 4px 0; border: none;
}
QComboBox QAbstractItemView QScrollBar::handle:vertical {
  background: #c9d5dd; border-radius: 5px; min-height: 26px;
}
QComboBox QAbstractItemView QScrollBar::handle:vertical:hover { background: #9fb3bf; }
QComboBox QAbstractItemView QScrollBar::add-line:vertical,
QComboBox QAbstractItemView QScrollBar::sub-line:vertical { height: 0; }
QComboBox QAbstractItemView QScrollBar::add-page:vertical,
QComboBox QAbstractItemView QScrollBar::sub-page:vertical { background: transparent; }

/* ---- 列表 ---- */
QListWidget { background: #ffffff; border: 1px solid #d7e0e6; border-radius: 8px; padding: 4px; outline: none; }
QListWidget::item { padding: 6px 8px; border-radius: 6px; color: #1f2a32; }
QListWidget::item:hover { background: #f1f7f6; }
QListWidget::item:selected { background: #d1fae5; color: #115e59; }

/* ---- 表格 ----
   [AGENT_CHANGE 2026-09-12] 原 QTableWidget / QTableView / QHeaderView 规则已删除：
   Fluent 的 TableWidget 继承 QTableWidget，表头与选中态由组件自绘，QSS 只会干扰。 */

/* ---- 页签 ---- */
QTabWidget::pane {
  border: 1px solid #d7e0e6; border-radius: 10px; background: #ffffff; top: -1px;
}
QTabBar::tab {
  background: #e9eff3; color: #5b6b77; border: 1px solid #d7e0e6; border-bottom: none;
  border-top-left-radius: 8px; border-top-right-radius: 8px; padding: 6px 14px; margin-right: 2px;
}
QTabBar::tab:hover { background: #f1f7f6; }
QTabBar::tab:selected { background: #ffffff; color: #0f766e; font-weight: 600; }

/* ---- 分组框 ---- */
QGroupBox {
  border: 1px solid #d7e0e6; border-radius: 10px; margin-top: 10px;
  background: #ffffff; padding: 10px;
}
QGroupBox::title {
  subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #115e59; font-weight: 600;
}

/* ---- 报文监视（深色终端）---- */
QTextEdit#Monitor {
  background: #0b1220; color: #dbeafe; border: 1px solid #d7e0e6; border-radius: 8px;
  font-family: Consolas, "Courier New", monospace; font-size: 12px; padding: 8px;
  selection-background-color: #1d4ed8; selection-color: #ffffff;
}
QTextEdit#Monitor QScrollBar:vertical { background: #0b1220; width: 10px; margin: 0; }
QTextEdit#Monitor QScrollBar::handle:vertical { background: #334155; border-radius: 5px; min-height: 24px; }
QTextEdit#Monitor QScrollBar::add-line:vertical, QTextEdit#Monitor QScrollBar::sub-line:vertical { height: 0; }

/* ---- 菜单 ---- */
QMenu { background: #ffffff; border: 1px solid #d7e0e6; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 6px; }
QMenu::item:selected { background: #e2efed; color: #115e59; }
QMenu::separator { height: 1px; background: #e3e9ee; margin: 4px 8px; }

/* ---- 滚动条 ---- */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #c9d5dd; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #9fb3bf; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #c9d5dd; border-radius: 5px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: #9fb3bf; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }

/* ---- 状态栏 / 对话框 ---- */
QStatusBar { background: #ffffff; border-top: 1px solid #d7e0e6; color: #6b7c88; }
QStatusBar::item { border: none; }
QMessageBox { background: #ffffff; }
QMessageBox QLabel { color: #1f2a32; }
"""


def apply_theme(app: QApplication) -> None:
    """应用 Fusion 风格 + 调色板 + QSS。"""
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    pal = app.palette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor(PANEL))
    pal.setColor(QPalette.AlternateBase, QColor("#fafcfc"))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(PANEL))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor(ACCENT2))
    pal.setColor(QPalette.ToolTipText, QColor("#ffffff"))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#a5b1ba"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#a5b1ba"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
    _install_combo_popup_fix(app)


def install_translations(app: QApplication):
    """装 Qt 自带中文翻译（QMessageBox / QFileDialog 按钮中文化）。"""
    try:
        tr = QTranslator(app)
        path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
        if tr.load(QLocale(QLocale.Chinese, QLocale.China), "qtbase", "_", path):
            app.installTranslator(tr)
            return tr
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# QComboBox 下拉：去掉弹出容器自带的那圈方框
#
# QComboBox 默认把列表放进独立弹出窗口 QComboBoxPrivateContainer，这个窗口由 QStyle
# 直接画一圈 palette.mid()（#9f9f9f）的深灰边框。app 级 QSS 覆盖不掉它
# （QComboBox QFrame / QComboBoxPrivateContainer 选择器实测都无效，setFrameStyle
# 也无效），在浅色卡片界面上看就是「下拉外面多出一个方框」。
# 有效且不改列表观感的办法：容器首次显示时直接给它设 widget 级样式表 —— 只去边框、
# 背景填白，列表自身的圆角边框与选中态（Fusion 的高亮色）完全保持原样。
# ---------------------------------------------------------------------------
_POPUP_CONTAINER_QSS = "border: none; background: #ffffff;"


class _ComboArrow(QWidget):
    """下拉框右侧的展开小三角（提示「这是可选框」）。

    QSS 的 `::down-arrow` 只能给 `image:`（要磁盘图片资源）；QProxyStyle 的
    drawPrimitive 在 QStyleSheetStyle 接管 combo 之后根本不会被调用（实测命中 0）。
    于是在 combo 里放一个鼠标穿透的小控件自己画三角：零外部资源，位置随父框同步。
    """

    ARROW_W = 9.0
    ARROW_H = 5.0
    BOX = 18            # 与 QSS 里 QComboBox::drop-down 的宽度一致
    COLOR = ACCENT

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ComboArrow")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)   # 点击穿透到 combo

    def sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None or parent.width() <= self.BOX:
            return
        want = QRect(parent.width() - self.BOX - 1, 1, self.BOX, max(1, parent.height() - 2))
        if self.geometry() != want:
            self.setGeometry(want)
        if not self.isVisible():
            self.show()          # 子控件新建时默认隐藏（本对象是在父框 Show 事件里建的）
        self.raise_()

    def paintEvent(self, event) -> None:
        self.sync_geometry()          # 万一父框尺寸变了没收到事件，重绘时自纠
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.COLOR))
        cx, cy = self.width() / 2.0, self.height() / 2.0
        path = QPainterPath()
        path.moveTo(cx - self.ARROW_W / 2, cy - self.ARROW_H / 2)
        path.lineTo(cx + self.ARROW_W / 2, cy - self.ARROW_H / 2)
        path.lineTo(cx, cy + self.ARROW_H / 2)
        path.closeSubpath()
        painter.drawPath(path)


class _ComboItemDelegate(QStyledItemDelegate):
    """自绘下拉选项。

    Fusion 风格会无视 QSS 自己画下拉列表的高亮（实测 ::item:selected、
    selection-background-color、调色板 Highlight、widget 级样式表全部无效），
    只能用 delegate 接管。样式：选中行淡绿通栏 + 深色字，悬停行极浅绿，行高 29px。
    """

    PAD_H = 11
    PAD_V = 7
    SELECT_BG = "#d1fae5"   # 选中行：淡绿通栏（对应参考图的淡紫高亮）
    SELECT_FG = TEXT        # 深色字
    HOVER_BG = "#f1f7f6"
    FG = TEXT

    def sizeHint(self, option, index) -> QSize:
        base = super().sizeHint(option, index)
        height = max(base.height(), option.fontMetrics.height() + self.PAD_V * 2)
        return QSize(max(base.width() + self.PAD_H * 2 + 8, 80), height)

    def paint(self, painter, option, index) -> None:
        painter.save()
        # 通栏高亮：左右到边、不带圆角（参考图那种整行铺底）
        rect = option.rect
        text_rect = rect.adjusted(self.PAD_H, 0, -self.PAD_H, 0)
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, QColor(self.SELECT_BG))
            painter.setPen(QColor(self.SELECT_FG))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(rect, QColor(self.HOVER_BG))
            painter.setPen(QColor(self.FG))
        else:
            painter.setPen(QColor(self.FG))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, str(index.data()))
        painter.restore()


class _ComboPopupPolisher(QObject):
    """统一处理所有 QComboBox 的下拉：去容器外框 + 自绘选项。

    delegate 必须在 QComboBox 自己首次显示时就装上：等到弹出容器 Show 才装的话，
    行高会变但列表高度已经按旧行高算好，最后一行会被裁掉。
    """

    def eventFilter(self, obj, event):
        try:
            etype = event.type()
            if isinstance(obj, QComboBox):
                if etype in (QEvent.Show, QEvent.Polish):
                    # Polish 早于首次 Show：这样连当前不可见页签里的下拉也能提前装好箭头
                    self._install_delegate(obj.view())
                    self._install_arrow(obj)
                elif etype == QEvent.Resize:
                    self._place_arrow(obj)
            elif etype == QEvent.Show and isinstance(obj, QWidget)                     and obj.metaObject().className() == "QComboBoxPrivateContainer":
                self._polish_container(obj)
        except Exception:
            pass
        return False

    @classmethod
    def _install_arrow(cls, combo: QComboBox) -> None:
        arrow = combo.findChild(_ComboArrow, "ComboArrow")
        if arrow is None:
            arrow = _ComboArrow(combo)
        arrow.show()
        arrow.sync_geometry()
        # Show 时父布局可能还没定稿（宽度还是默认值），延后一拍再摆一次
        QTimer.singleShot(0, lambda c=combo: cls._place_arrow(c))

    @staticmethod
    def _place_arrow(combo: QComboBox) -> None:
        arrow = combo.findChild(_ComboArrow, "ComboArrow")
        if arrow is not None:
            arrow.sync_geometry()

    @classmethod
    def _install_delegate(cls, view) -> None:
        if view is None:
            return
        if not isinstance(view.itemDelegate(), _ComboItemDelegate):
            view.setMouseTracking(True)
            view.setItemDelegate(_ComboItemDelegate(view))

    @classmethod
    def _polish_container(cls, container: QWidget) -> None:
        if not container.property("pw_no_popup_frame"):
            container.setProperty("pw_no_popup_frame", True)
            container.setStyleSheet(_POPUP_CONTAINER_QSS)
        cls._install_delegate(container.findChild(QAbstractItemView))


def _install_combo_popup_fix(app: QApplication) -> None:
    if getattr(app, "_pw_combo_popup_polisher", None) is not None:
        return
    polisher = _ComboPopupPolisher(app)
    app.installEventFilter(polisher)
    app._pw_combo_popup_polisher = polisher


def mono_font(size: int = 10, bold: bool = False) -> QFont:
    f = QFont("Consolas", size)
    f.setStyleHint(QFont.Monospace)
    f.setBold(bold)
    return f
# [AGENT_CHANGE_END] 2026-09-11 Qt界面主题
