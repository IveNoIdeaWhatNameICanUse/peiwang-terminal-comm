# [AGENT_CHANGE_BEGIN] 2026-09-07 104-MVP入口
"""应用入口：默认 tkinter；可选 PEIWANG_USE_WEBVIEW=1 时用磁盘 ui/ + 本地 HTTP。"""
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
    # 可写持久数据目录：exe 所在目录（打包后），源码用项目根
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = _app_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# [AGENT_CHANGE_END] 2026-09-07 exe打包路径适配

from core.api import ApiBridge


def _want_webview() -> bool:
    # [AGENT_CHANGE_BEGIN] 2026-09-07 默认Tk规避加密拦截
    return os.environ.get("PEIWANG_USE_WEBVIEW", "").strip() == "1"
    # [AGENT_CHANGE_END] 2026-09-07 默认Tk规避加密拦截


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


def main() -> None:
    # [AGENT_CHANGE_BEGIN] 2026-09-07 默认Tk规避加密拦截
    api = ApiBridge(_data_root())
    if _want_webview() and run_pywebview(api):
        return
    run_tk(api)
    # [AGENT_CHANGE_END] 2026-09-07 默认Tk规避加密拦截


if __name__ == "__main__":
    main()
# [AGENT_CHANGE_END] 2026-09-07 104-MVP入口
