# 配网终端通讯工具（104 模拟主站 MVP）

Windows 桌面工具：IEC 60870-5-104 模拟主站，支持四遥测试与本地固定 IP/端口绑定。

## 启动

```bash
cd 配网终端通讯
python -m app.main
```

或：

```bash
python app/main.py
```

## 功能（MVP）

- 104 主站：连接、STARTDT、总召唤、时钟同步
- 四遥：遥信/遥测监视，单/双点遥控（SBO），浮点/归一化设点
- 本地绑定：多网卡列表、指定本地 IP、可选本地端口、失败分类提示
- 点表 JSON 工程保存；报文监视

## 依赖说明

- 优先 pywebview（见 
equirements.txt）。若未安装，自动回退 	kinter。
- 协议层当前为自研 Python 实现（满足本地 bind）。后续可桥接 c104 / lib60870（GPL）。

## 离线安装（pip 直连 PyPI 被拒时）

`ash
python scripts/install_offline_wheels.py
`

脚本用 curl 从 PyPI 下载 wheel 到 offline_wheels/，再 --no-index 安装。也可手动：

`ash
python -m pip install --no-index --find-links=offline_wheels --no-build-isolation pywebview
`

## 打包 exe

`ash
python scripts/build_exe.py
`

产物：dist/配网终端通讯/配网终端通讯.exe（需连同目录内 dll/资源一起发布，不要只拷 exe）。

## 目录

- `app/` 入口与桌面壳
- `ui/` HTML 界面（pywebview）
- `core/` 工程/点表/API 桥
- `protocol/iec104/` 104 主站
- `net/` 网卡枚举
- `configs/` 默认工程
