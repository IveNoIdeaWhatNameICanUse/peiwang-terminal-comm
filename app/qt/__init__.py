# [AGENT_CHANGE_BEGIN] 2026-09-11 Qt界面入口
"""Qt（PySide6 + QSS）界面包。

界面由 Qt 原生绘制，不加载磁盘上的 .html/.js/.css，也不起本地 HTTP 或浏览器内核，
用于规避加密/DLP 对本地 HTML / WebView 的拦截（见 handoff-ui.md 路线 1）。
"""
from __future__ import annotations

import sys
from pathlib import Path


def run_qt_shell(api, data_root: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from app.qt import theme
    from app.qt.main_window import MainWindow

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName("配网终端通讯")
    app.setApplicationDisplayName("配网终端通讯")
    theme.install_translations(app)
    theme.apply_theme(app)

    window = MainWindow(api, data_root)
    window.show()
    app.exec()


__all__ = ["run_qt_shell"]
# [AGENT_CHANGE_END] 2026-09-11 Qt界面入口
