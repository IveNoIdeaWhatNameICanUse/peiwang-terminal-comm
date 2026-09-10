# 配网终端通讯 · 进度交接（handoff）

更新日期：2026-09-07  
仓库：https://github.com/IveNoIdeaWhatNameICanUse/peiwang-terminal-comm.git  
本地路径：`D:\project\规约工具及文档\配网终端通讯`  
分支：`main`（远端已有初始提交；后续功能多为**未提交工作区改动**）

---

## 1. 产品目标（已确认）

- Windows 桌面工具：IEC 60870-5-104 **模拟主站**
- 支持四遥测试、绑定本地固定 IP（含指定本地端口、多网卡选择、连接失败分类提示）
- 产品形态：桌面；接受 GPL；101 后续优先平衡式；**当前先交付 104**
- 协议层：自研 Python（满足本地 bind）；后续可桥接 c104 / lib60870

---

## 2. 当前进度摘要

### 已完成

| 项 | 状态 | 说明 |
|---|---|---|
| 开源调研与技术方案 | 完成 | 见 `2026-09-07-配网终端通讯开源调研与技术方案.html` |
| 104 MVP 骨架 | 完成 | 已推远端 `c66a3f2 feat: 初始化 104 模拟主站 MVP` |
| Tk 桌面壳 | 完成 | 默认入口为 tkinter（规避本机加密软件拦截 HTML/WebView） |
| 可选 WebView | 保留但默认关闭 | `PEIWANG_USE_WEBVIEW=1` 时从磁盘 `ui/` 走本地 HTTP；**已去掉内嵌 UI 资源方案** |
| 固定本地端口重连 | 已修（待验证） | 原 WinError 10048；断开时 `SO_LINGER` + 强制关 socket |
| 多主站模式 | 已实现（待充分验证） | 多 session 同时在线；UI 可新建/删除/切换；遥控等操作作用在当前选中会话 |
| PyInstaller 打包 | 脚本就绪 | `python scripts/build_exe.py` → `dist\配网终端通讯\配网终端通讯.exe` |
| 离线装依赖 | 就绪 | `scripts/install_offline_wheels.py` |
| IEC 101（平衡/非平衡） | 完成 | `protocol/iec101/`（FT1.2 链路 + 主站）；UI 可选协议与串口参数；模拟从站自测脚本 `scripts/sim_iec101.py`、`scripts/sim_iec101_api.py` |
| 事件记录 / 四遥统计 | 完成 | 见 `core/eventlog.py`；修正自动建点未写四遥类别导致首帧不计统计的问题 |

### 未做 / 后续

- 101 实机联调（真实终端串口、厂家参数确认：IOA 长度/链路地址/平衡方式）
- 101 的非平衡「一级数据主动上送 + ACD 轮询」实机时序调优
- 更完整点表/工程管理体验
- 与 c104 / lib60870 桥接（可选）
- 清理全仓库 `AGENT_CHANGE` 标记（用户未确认清理前保留）
- 将当前未提交改动 commit / push 到远端
- README 中部分反引号被破坏（显示异常），需整理

---

## 3. Git / 工作区状态（交接时）

**已推远端：**

- `c66a3f2` 初始化 104 MVP

**工作区未提交（相对 HEAD 有实质改动）：**

- 已修改：`README.md`、`app/main.py`、`app/tk_shell.py`、`core/api.py`、`core/project.py`、`protocol/iec104/master.py`
- 未跟踪：`peiwang.spec`、`scripts/build_exe.py`、若干截图（`重连bug.png`、`自动步骤.png`、`ScreenShot_*.png`）

> 交接时请勿把截图当核心代码一并误提交；打包产物 `dist/`、`build/` 一般不入库。

---

## 4. 架构速览

```
app/main.py          # 入口：默认 Tk；可选 WebView
app/tk_shell.py      # Tk UI（多主站列表、连接参数、四遥、报文）
ui/index.html        # WebView 备用界面（磁盘文件，非内嵌）
core/api.py          # ApiBridge：会话/连接/四遥/工程 API
core/project.py      # 工程模型 + SessionDef 多会话
core/events.py       # 事件总线
protocol/iec104/     # 104 编解码 + Master
net/                 # 网卡枚举
configs/             # 默认工程
scripts/build_exe.py # 打包
peiwang.spec         # PyInstaller 配置
```

### 多主站要点

- `ProjectDef.sessions`：每路独立远端/本地 IP·端口、CA、OA
- `ApiBridge` 按 `session_id` 维护多个 `Iec104Master`
- 可同时连接多路；总召/对时/遥控等走**当前激活会话**
- 新建会话时自动避开已占用本地端口

### 重连 10048 修复要点（`protocol/iec104/master.py`）

- 连接前确保旧 socket 已关
- 断开使用 `SO_LINGER(1,0)` + `shutdown` + `close`，尽快释放本地端口
- 对端断开路径同样关闭本地 socket

---

## 5. 如何运行

### 源码

```bash
cd D:\project\规约工具及文档\配网终端通讯
python -m app.main
# 或
python app/main.py
```

可选 WebView（本机加密环境通常仍会拦 HTML，默认勿开）：

```bash
set PEIWANG_USE_WEBVIEW=1
python -m app.main
```

### 离线装 pywebview（若需要）

```bash
python scripts/install_offline_wheels.py
```

### 打包 exe

```bash
python scripts/build_exe.py
```

产物：`dist\配网终端通讯\配网终端通讯.exe`（需整目录发布，勿只拷单个 exe）。

**注意：** 若旧 exe 仍在运行，打包会因 `dist` 目录占用失败（WinError 5）。先结束进程再打包。

交接当日已成功重打一版（去掉内嵌 UI 后）：

- `D:\project\规约工具及文档\配网终端通讯\dist\配网终端通讯\配网终端通讯.exe`

---

## 6. 建议验证清单

1. **重连**：绑定固定本地端口 → 连接 → 断开 → 立即再连（应不再 10048；无需多次点断开）
2. **多主站**：新建第二路不同本地端口 → 两路同时连接 → 切换会话做总召/遥控
3. **同端口冲突**：两路设相同本地端口时，新建/连接应有明确提示或自动改端口
4. **exe**：关闭旧进程后运行新打包 exe，确认默认进 Tk，多主站与重连行为正常

---

## 7. 环境与已知坑

| 项 | 说明 |
|---|---|
| Python | 本机约 3.10.10；PyInstaller 5.9.0 |
| 加密软件 | 拦截本地 HTML/WebView；故默认 Tk |
| 内嵌 UI | 曾做过 `embedded_ui` / `embed_ui`；**已按要求去掉**，勿再恢复除非明确需求 |
| `AGENT_CHANGE` | 大量源文件仍有 `AGENT_CHANGE_BEGIN/END` 标记；清理需用户明确同意 |
| README | 部分 markdown 反引号损坏，阅读时以本 handoff / 源码为准 |

---

## 8. 下一任接手建议顺序

1. 按第 6 节验证重连 + 多主站；有问题再改 `master.py` / `api.py` / `tk_shell.py`
2. 整理 README，提交当前功能 diff（含 `peiwang.spec`、`scripts/build_exe.py`），push
3. 确认后清理 `AGENT_CHANGE` 标记
4. 再规划 101 / 点表增强等下一阶段

---

## 9. 101（串口）使用要点

- 协议层：`protocol/iec101/link.py`（FT1.2 帧编解码，链路地址 1/2 字节）、`protocol/iec101/master.py`
- 非平衡：复位链路(FC=0) → 请求链路状态(FC=9) → 周期召唤 2 级(FC=11)；收到 ACD=1 时下次召唤 1 级(FC=10)；用户数据 FC=3 等 ACK
- 平衡：控制域 DIR/FCB，用户数据等 FC=4 确认；链路初始化同复用 FC=0/FC=9
- 连接不阻塞界面：链路初始化在后台线程执行（链路确认等待 ≤1.5s）；单字符 E5 视为复位确认；未收到确认时提示“未收到从站确认”而不谎报完成；非平衡轮询与初始化并行，连接后立即可见报文
- 会话参数：`protocol=101`、`serial_port`、`baudrate`、`serial_parity`、`stopbits`、`link_addr`（>255 自动 2 字节）、`addr_size`（链路地址长度，默认 **2 字节**）、`balanced`、`poll_period`、`ioa_size_101`（默认 2 字节）
- 报文格式（与现场抓包一致，帧例均为 2 字节链路地址=1）：
  - 非平衡主站：复位链路 `10 40 01 00 41 16`、请求链路状态 `10 49 01 00 4A 16`、召唤 2 级 `10 4B 01 00 4C 16`、用户数据 `68 0C 0C 68 73 01 00 …`（FCB 翻转后 `53`）
  - 平衡主站：复位链路 `10 C0 01 00 C1 16`、请求链路状态 `10 C9 01 00 CA 16`、用户数据 `68 0C 0C 68 F3 01 00 …`（FCB 翻转后 `D3`）
  - 从站帧：`10 00 01 00 01 16`（确认）、`10 0B …`（链路忙）；部分终端从站帧 bit7 会置位（如 `8B`），解析时按 PRM=bit6 判定方向即可
- 现场交互（KW-2200 参数：**平衡模式**、2 字节链路地址、无校验、9600、IOA 2 字节）：
  - 主站复位链路 `C0` → 从站回 `00`（或 E5）；从站主动 `49`（请求链路状态）→ 主站回 `8B`（链路忙）；从站主动 `40`（复位链路）→ 主站回 `80`（确认）
  - 对“请求链路状态”收到 `0B`（链路忙）视为**链路正常**，不再误判未确认
  - 链路建立（收到响应）后自动发一次总召唤（平衡式从站不会自发全量数据）
  - 确认等待标志在**发送前**清零（避免从站快速响应被丢失，导致误报未确认）
- UI：连接参数区「协议」下拉切换，选 101 后显示串口参数（Tk 与 WebView 均已同步）
- 自测：`python scripts/sim_iec101.py`（内存回环模拟从站）、`python scripts/sim_iec101_api.py`（API 层集成）

---

## 10. 关键对话结论备忘

- 默认 UI：**Tk**（方案 B），不是 WebView
- 用户明确要求：**去掉内嵌 UI 相关修改**（已删 `app/embedded_ui.py`、`scripts/embed_ui.py`）
- 重连 bug 截图：`重连bug.png`（本地未跟踪）
- 不默认 git commit；用户说提交再提交
