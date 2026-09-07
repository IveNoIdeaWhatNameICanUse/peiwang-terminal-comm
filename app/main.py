# [AGENT_CHANGE_BEGIN] 2026-09-07 104-MVP入口
"""应用入口：优先 pywebview，不可用时回退 tkinter+本地 HTML。"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.api import ApiBridge


def _push_to_webview(window, event: dict) -> None:
    try:
        payload = json.dumps(event, ensure_ascii=False)
        window.evaluate_js(f"window.__onNativeEvent && window.__onNativeEvent({payload})")
    except Exception:
        pass


def run_pywebview(api: ApiBridge) -> bool:
    try:
        import webview  # type: ignore
    except ImportError:
        # 尝试 vendor
        vendor = ROOT / "vendor" / "pywebview"
        if vendor.exists():
            sys.path.insert(0, str(vendor))
            try:
                import webview  # type: ignore
            except ImportError:
                return False
        else:
            return False

    ui = (ROOT / "ui" / "index.html").as_uri()
    window = webview.create_window(
        "配网终端通讯 · 104模拟主站",
        ui,
        js_api=api,
        width=1180,
        height=820,
    )

    def push(event: dict) -> None:
        if window:
            _push_to_webview(window, event)

    api.set_ui_push(push)
    webview.start(debug=False)
    return True


def run_tk(api: ApiBridge) -> None:
    from app.tk_shell import run_tk_shell

    run_tk_shell(api, ROOT)


def main() -> None:
    api = ApiBridge(ROOT)
    if not run_pywebview(api):
        run_tk(api)


if __name__ == "__main__":
    main()
# [AGENT_CHANGE_END] 2026-09-07 104-MVP入口
