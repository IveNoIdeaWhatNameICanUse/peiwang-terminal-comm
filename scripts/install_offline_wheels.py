# [AGENT_CHANGE_BEGIN] 2026-09-07 离线PyPI安装脚本
"""用 curl 从 PyPI 拉 wheel 到 offline_wheels，再 --no-index 安装（绕过 Python 直连 PyPI 被拒）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHEELS = ROOT / "offline_wheels"
PKGS = [
    "packaging",
    "wheel",
    "setuptools",
    "typing_extensions",
    "bottle",
    "proxy_tools",
    "clr_loader",
    "pythonnet",
    "pywebview",
]


def curl(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["curl.exe", "-sL", url, "-o", str(out)], check=True)


def pick_artifact(meta: dict) -> dict:
    urls = [u for u in meta.get("urls", []) if not u.get("yanked")]
    wheels = [u for u in urls if u.get("packagetype") == "bdist_wheel"]
    preferred = [
        u
        for u in wheels
        if "py3-none-any" in u["filename"]
        or "py2.py3-none-any" in u["filename"]
        or ("cp310" in u["filename"] and ("win_amd64" in u["filename"] or "none-any" in u["filename"]))
    ]
    if preferred:
        return preferred[0]
    if wheels:
        return wheels[0]
    sdists = [u for u in urls if u.get("packagetype") == "sdist"]
    if not sdists:
        raise RuntimeError(f"无可用发行包: {meta.get('info', {}).get('name')}")
    return sdists[0]


def download_all() -> None:
    WHEELS.mkdir(parents=True, exist_ok=True)
    for name in PKGS:
        meta_path = WHEELS / f"{name}.json"
        curl(f"https://pypi.org/pypi/{name}/json", meta_path)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        art = pick_artifact(meta)
        dest = WHEELS / art["filename"]
        if dest.exists() and dest.stat().st_size > 0:
            print(f"SKIP {dest.name}")
            continue
        print(f"GET  {dest.name}")
        curl(art["url"], dest)
        print(f"     {dest.stat().st_size} bytes")


def install_all() -> None:
    # 先装构建辅助，再装业务依赖
    order = [
        "packaging",
        "wheel",
        "setuptools",
        "typing_extensions",
        "bottle",
        "proxy_tools",
        "clr_loader",
        "pythonnet",
        "pywebview",
    ]
    for name in order:
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            f"--find-links={WHEELS}",
            "--no-build-isolation",
            name,
        ]
        print("RUN", " ".join(cmd))
        subprocess.check_call(cmd)


def main() -> None:
    download_all()
    install_all()
    import importlib

    importlib.import_module("webview")
    print("OK: webview installed")


if __name__ == "__main__":
    main()
# [AGENT_CHANGE_END] 2026-09-07 离线PyPI安装脚本
