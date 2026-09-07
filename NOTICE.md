# 第三方许可说明（NOTICE）

本工具为内部使用的配网终端 IEC 60870-5-104 模拟主站。

## 当前 MVP 实现

- 104 协议主站为项目内自研 Python 实现（支持本地 IP/端口绑定），便于满足多网卡现场需求。
- 后续可替换/桥接至 lib60870 / c104（GPLv3）。若引入这些依赖，整个分发需遵守 GPLv3 义务。

## 计划/可选依赖

- lib60870 (MZ Automation) — GPLv3 / 商业双许可
- c104 (Fraunhofer iec104-python) — GPLv3
- pywebview — BSD-3-Clause

当前环境若无法从 PyPI 安装 pywebview，应用会自动回退到 tkinter 桌面壳；HTML UI（`ui/`）在 pywebview 可用时优先加载。
