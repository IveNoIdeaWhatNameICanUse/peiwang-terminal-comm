# [AGENT_CHANGE_BEGIN] 2026-09-07 104-MVP入口
"""应用入口：默认 PySide6(Qt) 界面；PEIWANG_USE_TK=1 回退 Tk；PEIWANG_USE_WEBVIEW=1 走 WebView。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# [AGENT_CHANGE_BEGIN] 2026-09-07 exe打包路径适配
def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _data_root() -> Path:
    # [AGENT_CHANGE_BEGIN] 2026-09-11 Qt版：打包后持久数据走 %APPDATA%
    """可写持久数据目录。

    打包后走 %APPDATA%\\配网终端通讯：装到 C:\\Program Files 时 exe 目录无写权限，
    在 UAC 下会把工程配置写丢。源码运行时仍用项目根（configs/default.json 就在那里）。
    """
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or str(Path.home())
        target = Path(base) / "配网终端通讯"
        try:
            (target / "configs").mkdir(parents=True, exist_ok=True)
            return target
        except OSError:
            return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
    # [AGENT_CHANGE_END] 2026-09-11 Qt版：打包后持久数据走 %APPDATA%


ROOT = _app_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# [AGENT_CHANGE_END] 2026-09-07 exe打包路径适配

from core.api import ApiBridge


def _want_webview() -> bool:
    # [AGENT_CHANGE_BEGIN] 2026-09-07 默认Tk规避加密拦截
    return os.environ.get("PEIWANG_USE_WEBVIEW", "").strip() == "1"
    # [AGENT_CHANGE_END] 2026-09-07 默认Tk规避加密拦截


def _want_tk() -> bool:
    # [AGENT_CHANGE_BEGIN] 2026-09-11 Qt界面：Tk 保留为显式回退
    return os.environ.get("PEIWANG_USE_TK", "").strip() == "1"
    # [AGENT_CHANGE_END] 2026-09-11 Qt界面


def run_pywebview(api: ApiBridge) -> bool:
    """可选 WebView：从磁盘 ui/ 经本地 HTTP 加载（已去掉内嵌资源）。"""
    if not _want_webview():
        return False
    try:
        import json
        import threading
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        import webview  # type: ignore
    except Exception:
        return False

    ui_dir = ROOT / "ui"
    if not (ui_dir / "index.html").is_file():
        return False

    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ui_dir), **kwargs)

        def log_message(self, format, *args):  # noqa: A003
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, name="ui-http", daemon=True).start()
    ui_url = f"http://127.0.0.1:{port}/index.html"

    window = webview.create_window(
        "配网终端通讯 · 104/101 模拟主站",
        ui_url,
        js_api=api,
        width=1180,
        height=820,
        min_size=(1100, 720),
    )

    def push(event: dict) -> None:
        try:
            payload = json.dumps(event, ensure_ascii=False)
            window.evaluate_js(f"window.__onNativeEvent && window.__onNativeEvent({payload})")
        except Exception:
            pass

    api.set_ui_push(push)
    webview.start(debug=False)
    return True


def run_tk(api: ApiBridge) -> None:
    from app.tk_shell import run_tk_shell

    run_tk_shell(api, _data_root())


def run_qt(api: ApiBridge) -> None:
    from app.qt import run_qt_shell

    run_qt_shell(api, _data_root())


def main() -> None:
    # [AGENT_CHANGE_BEGIN] 2026-09-11 Qt界面：默认 PySide6 + QSS（规避加密软件拦 HTML/WebView）
    api = ApiBridge(_data_root())
    if _want_tk():
        run_tk(api)
        return
    if _want_webview() and run_pywebview(api):
        return
    run_qt(api)
    # [AGENT_CHANGE_END] 2026-09-11 Qt界面


if __name__ == "__main__":
    main()
# [AGENT_CHANGE_END] 2026-09-07 104-MVP入口
