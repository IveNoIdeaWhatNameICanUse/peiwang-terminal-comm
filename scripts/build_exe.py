# [AGENT_CHANGE_BEGIN] 2026-09-07 打包脚本
"""打包桌面 exe（PyInstaller）。默认 onefile，产物是单个 dist/<name>.exe；
也兼容 onedir 的 dist/<name>/<name>.exe（见下方 [AGENT_CHANGE 2026-09-12] 注释）。"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    dist = ROOT / "dist"
    build = ROOT / "build"
    for p in (dist, build):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    spec = ROOT / "peiwang.spec"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(spec),
    ]
    print("RUN:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))
    # [AGENT_CHANGE 2026-09-12] onefile 产物直接在 dist/ 下；同时兼容 onedir 的 dist/<name>/<name>.exe
    out = dist / "配网终端通讯.exe"
    if not out.exists():
        out = dist / "配网终端通讯" / "配网终端通讯.exe"
    if not out.exists():
        # 兼容部分环境下中文目录编码差异
        cands = list(dist.rglob("*.exe"))
        print("exe candidates:", cands)
        if not cands:
            raise SystemExit("打包失败：未找到 exe")
        out = cands[0]
    print("OK:", out)


if __name__ == "__main__":
    main()
# [AGENT_CHANGE_END] 2026-09-07 打包脚本
