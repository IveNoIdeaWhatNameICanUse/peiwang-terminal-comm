# 配网终端通讯 · UI 重写专项交接（handoff-ui）

更新日期：2026-09-11
状态：**路线 1 已实施（Qt 界面已可用，待打包实测与现场验收）**
关联文档：`handoff.md`（总体交接）、`README.md`、`core/api.py`、`peiwang.spec`

> 本文档的用途：UI 方案是在前一轮会话里讨论确定的，新会话看不到那段对话。
> 读完本文件即可直接开始 UI 重写，不需要回溯历史。

---

## 1. 目标与已定决策

**目标**：把默认的 Tk 界面升级成"类似 HTML 那样精美"的界面，同时**规避本机加密/DLP 对本地 HTML / WebView 的拦截**。

**已确认选择：路线 1 —— PySide6 + QSS 重写 UI 层。**

为什么它能绕开加密软件：界面由 Qt 原生绘制，UI 描述是 QSS 字符串（可写进 `.py` 或编译进 `.qrc` 二进制资源），**全程不加载磁盘上的 `.html/.js/.css`，也没有浏览器渲染子进程和缓存目录**，DLP 的三类常见拦截面（按扩展名透明加密、拦 file:// 与本地 HTTP、拦浏览器内核子进程及缓存目录）都不碰。

备选方案（未采用，保留备查）：

- **路线 2**：保留现有 HTML 前端，把 `ui/` 打包成 zip/base64 内嵌到 `.py`，运行时内存注入（`webview.create_window(html=...)` 或 `QWebEngineView.setHtml()`），不读磁盘、不起本地 HTTP。改动小但**仍依赖 WebView2 / QtWebEngine 内核**，内核若被拦就无解。
- **路线 3**：现有 Tk 换皮（`customtkinter` / `sv-ttk` / `ttkbootstrap`）。零 OS 限制、改动最小，但外观上限低于 CSS。

另有一条根治途径（与代码无关）：让 IT 给发布目录加白名单或设为"不加密目录"。**不要试图破解或停用加密软件。**

---

## 2. 环境实测（2026-09-11，本机）

| 项 | 实测结果 |
|---|---|
| Python | 3.10.11 64-bit（`D:\Program Files\Python310`） |
| PySide6 | **6.6.0 已装**（含 PySide6-Addons/Essentials、shiboken6），Qt 6.6.0 |
| PyInstaller | 6.21.0 + pyinstaller-hooks-contrib 2026.6 |
| WebView2 Runtime | 已装 152.0.4191.66 |
| pywebview | **未安装**（这就是现在 `PEIWANG_USE_WEBVIEW=1` 无效的原因之一） |
| pyserial | **未安装**（只影响 101 串口相关代码运行；协议层是函数内 import，纯 UI 工作不受影响） |
| customtkinter / sv-ttk / ttkbootstrap | 均未安装 |
| PySide6-Fluent-Widgets | **1.11.3 已装**（连带 PySideSix-Frameless-Window 0.8.2、darkdetect 0.8.0、pywin32 312），装到 user site（全局 site-packages 不可写） |
| 打包实测 | 最小 PySide6 + QSS 探针，onedir **106 MB**（exe 1.1 MB + `_internal/` 105 MB），已排除 QtWebEngineCore/QtQuick/QtQml/Qt3D/Charts/Multimedia/Sql/tkinter/numpy |
| 打包实测（含 Fluent Widgets） | onedir **131 MB**（+25 MB，来自 Pillow / QtSvg / QtXml / QtSvgWidgets / pywin32）；`qfluentwidgets` 100 个模块入包，numpy 仍被排除 |
| 打包实测（onefile，2026-09-12） | 单文件 **54 MB**（压缩态，运行时解压到 `%TEMP%`）；启动冒烟 `exit=124` 正常 |

注意事项：

- **PyInstaller 打包必须排除 `numpy`**：本机 numpy 2.2.6 与旧编译模块冲突，带进去会打出坏包。
- **打包模式：onefile（2026-09-12 按用户要求由 onedir 改过来）**。原建议 onedir 整目录发布，因为
  onefile 每次启动会把资源解压到 `%TEMP%` —— 那正是 DLP 重点监控目录，与路线 1「躲 DLP」的初衷
  相冲突，启动也慢数秒。用户明确选择「只给单个 exe」便于分发，故接受该代价。
  **若现场发现被 DLP 拦，改回 onedir**：`peiwang.spec` 里恢复
  `EXE(..., [], exclude_binaries=True)` + `COLLECT(exe, a.binaries, a.zipfiles, a.datas)`；
  `scripts/build_exe.py` 的产物识别已兼容两种模式。

---

## 3. 交付物的运行环境要求

- **目标机：Windows 10 1809 x64 或更高 / Win11**。Qt 6 不支持 32 位 Windows，不支持 Win7/8/8.1（这是路线 1 最大的硬约束；若现场确有 Win7 工控机，需要改用 Qt5/PySide2 或回退路线 3）。
- 目标机**不需要**装 Python、PySide6、WebView2 Runtime。
- VC++ 运行库一般由 PyInstaller 自动收集进 `_internal/`；Win10 自带 UCRT。
- 磁盘占用约 110 MB（整目录）。
- **发布目录必须可写且不被加密/DLP 拦截**：否则 Qt 会直接报 `could not find or load the Qt platform plugin "windows"` 而起不来。
- 图形环境无特殊要求：Qt Widgets 走 raster 渲染，不需要 GPU/OpenGL，工控机与远程桌面可用（极端情况可设 `QT_OPENGL=software`）。
- 业务侧同现状：101 需 COM 口正常；104 绑本地固定端口需放行防火墙，绑 <1024 端口需管理员权限。

---

## 4. 实施步骤（建议顺序）

1. 新增 `app/qt_shell.py`（或 `app/qt/` 包）：主窗口骨架 + QSS 主题，先让静态界面跑起来。
2. 事件桥：`QObject` + `Signal`，接 `api.set_ui_push(...)`（`ApiBridge` 本来就是 UI 无关设计，**不需要改 `core/api.py`**）。
3. 按功能区逐个迁移（对照 `app/tk_shell.py`，共约 1900 行）：
   主站会话列表 → 连接参数 → 四遥面板（点表/值/操作）→ 报文监视 → 101 参数 / 104 参数 / 设备参数对话框 → 工程（新建/导入/保存）→ 事件记录与四遥统计。
4. 入口切换：`app/main.py` 默认走 Qt；保留 `PEIWANG_USE_TK=1` 回退 Tk（确认稳定前不要删 Tk 界面）。
5. 打包：更新 `peiwang.spec`（Qt 的 hiddenimports / excludes）。
6. 顺带修一个交付隐患：`_data_root()` 目前取 exe 所在目录，装到 `C:\Program Files` 会因 UAC 无写权限丢配置，建议改走 `%APPDATA%\配网终端通讯`。

---

## 4.1 实施记录（2026-09-11，本会话完成）

新增 `app/qt/` 包（全部 Qt 原生绘制，不读 `.html/.js/.css`，不起本地 HTTP）：

| 文件 | 作用 |
|---|---|
| `app/qt/__init__.py` | `run_qt_shell(api, data_root)`：QApplication + 主题 + 主窗口 |
| `app/qt/theme.py` | QSS 主题（配色照搬 `ui/styles.css`）+ Fusion 调色板 + Qt 中文翻译 |
| `app/qt/common.py` | `EventBridge`（队列 + 200ms QTimer，替代 Tk `poll_events`）、卡片/按钮/标签工厂、点表格式化、提示框包装 |
| `app/qt/panels.py` | `PointsPanel`（四遥点表）、`MonitorPanel`（报文监视）、`EventsPanel`（事件记录） |
| `app/qt/dialogs.py` | 101 / 104 / 设备参数、遥控、四遥统计对话框 |
| `app/qt/main_window.py` | 主窗口组装、会话/页签联动、四遥操作、工程新建/导入/保存、事件分发 |

入口：`app/main.py` 默认走 Qt，`PEIWANG_USE_TK=1` 回退 Tk，`PEIWANG_USE_WEBVIEW=1` 仍走 WebView。
`_data_root()` 打包后改为 `%APPDATA%\配网终端通讯`（源码运行仍用项目根）。
`peiwang.spec` 已补 Qt/101 的 hiddenimports 与 Qt 冗余模块 excludes；`requirements.txt` 加 `PySide6>=6.6.0`。

与 Tk 版的已知差异（有意为之，便于逐条复核）：

- 报文监视用 `QTextEdit`：左键点击选中整条报文、右键冻结/复制/清空/导出均保留；复制走剪贴板。
- 事件记录双击「定位点表」在 Tk 版只有提示文字没有实现，Qt 版**已实现**（切到该主站四遥状态并选中该点）。
- 遥控弹窗、保存后提示等交互与 Tk 版一致（发送后不自动关闭弹窗）。

下拉相关修复（均在 `app/qt/theme.py`，下次要调下拉样式先看这段）：

- **去掉下拉外框**：QComboBox 会把列表放进独立弹出窗口 `QComboBoxPrivateContainer`，该窗口由 QStyle 直接画一圈 `palette.mid()`（`#9f9f9f`）深灰边框。app 级 QSS（`QComboBoxPrivateContainer{}`、`QComboBox QFrame{}`——后者还会误伤列表自身边框，因为 `QAbstractItemView` 继承 `QFrame`）与 `setFrameStyle(NoFrame)` 实测**全部无效**；有效做法是在容器 Show 时给它设 widget 级样式表（只去边框、背景填白）。`combobox-popup: 0` 现在**已启用**：它让下拉改用列表自身承载，才能拿到正常的细灰圆角滚动条（否则 Fusion 只在弹窗边缘画一个小三角），也不会再有容器边框；选中态已由 delegate 自绘，因此不再有「改掉 Fusion 高亮」的副作用。
- **下拉箭头**：QSS `::down-arrow` 只能给 `image:`（要磁盘图片资源）；`QProxyStyle.drawPrimitive(PE_IndicatorArrowDown)` 在 QStyleSheetStyle 接管 combo 后**根本不会被调用**（实测命中 0 次）。最终用 `_ComboArrow`（combo 的子控件、鼠标穿透）自绘实心小三角：在 combo 的 Show 事件里创建，**必须显式 `show()`** —— 子控件在父框 Show 期间创建时默认隐藏，否则既不显示、也不会出现在 `grab()` 里；位置在 `sync_geometry()` 里按父框尺寸同步。
- **下拉选项自绘**：Fusion 会无视 QSS 自己画下拉高亮（`::item:selected`、`selection-background-color`、调色板 Highlight、widget 级样式表实测**全部无效**），只能用 `_ComboItemDelegate`（QStyledItemDelegate）接管绘制：选中行**淡绿通栏**（左右到边、无圆角）+ 深色字、悬停极浅绿、行高 29px。delegate 必须在 **QComboBox 自身 Show 时**装上，若等到弹出容器 Show 才装，列表高度已按旧行高算死，最后一行会被裁掉。

尚未完成（见第 7 节验收清单）：

- 打包实测（`python scripts/build_exe.py`）与在目标机（无 Python 的 Win10 x64）运行。
- 发布目录处于加密/DLP 监控环境下的现场启动验证（本方案的核心目的，需现场做）。
- Tk 界面在 Qt 版通过现场验收前保留，不要删。

## 4.2 Fluent Widgets 接入（2026-09-11）

已引入 **PySide6-Fluent-Widgets**（Fluent Design 组件库，GPLv3，商用需 Pro 授权），并从
「仅 `RemoteDialog` 试水」推进到「**对话框 + 主界面内部全面 Fluent 化**」：按钮 / 标签 /
输入框 / 下拉 / 复选框 / 卡片 / 表格已全部换用 qfluentwidgets；`theme.py` 的 QSS 以及
`QTabWidget` / `QListWidget` / `QTextEdit` / 顶栏·侧栏 `QFrame` / 状态栏仍保留。

依赖与打包（已落盘）：

- `requirements.txt`：加 `PySide6-Fluent-Widgets>=1.11.3`。
- `peiwang.spec`：`hiddenimports` 加 `qfluentwidgets` / `qframelesswindow` / `darkdetect` /
  `win32api` / `win32con` / `win32gui` / `win32print`；`excludes` 加 `qfluentwidgets.multimedia`、
  `qframelesswindow.webengine`、`qframelesswindow.linux`、`qframelesswindow.mac` 及
  `qframelesswindow.utils.linux_utils` / `.mac_utils`。
- **不要用 `collect_submodules` 收集 qfluentwidgets**：它靠「导入」枚举，本机导入
  `qfluentwidgets` 会因 shiboken/numpy 环境问题失败，导致 `common/components` 子模块漏收
  （实测只拿到 25 个）；只给顶层包名后 PyInstaller 静态追踪到 **100 个模块**。
- 资源（图片/字体/qss）全部内嵌在 `_rc/resource.py`，**没有磁盘资源文件**，`datas` 无需改动。
- 打包实测：onedir **131 MB**；按用户要求改 onefile 后为单文件 **54 MB**（见第 2 节注意事项）。

接入方式（改动集中在工厂，一处生效全局）：

- `app/qt/common.py` 工厂统一换 Fluent：`make_button`（`variant="primary"`→`PrimaryPushButton`，
  其余→`PushButton`）、`menu_button`、`card`（→`CardWidget` + `StrongBodyLabel` 标题）、
  `field_label`（→`BodyLabel`）、`muted_label`/`hint_label`（→`CaptionLabel`）。**凡是用这些
  工厂的文件（dialogs / panels / main_window）都自动 Fluent 化。**
- `app/qt/dialogs.py`：`_line`→`LineEdit`、`_combo`→`ComboBox`/`EditableComboBox`、
  `QCheckBox`→`CheckBox`、统计表 `QTableWidget`→`TableWidget`。
- `app/qt/panels.py` / `main_window.py`：表格→`TableWidget`，输入框/下拉→`LineEdit`/`ComboBox`/
  `EditableComboBox`，顶栏标题→`StrongBodyLabel`。
- **行为未变**：`QButtonGroup` 取值、`double_command`/`single_command`/`cancel_command` 调用参数、
  按钮 `clicked` 回调、发送后不自动关闭等均与 Tk 版一致（已用脚本逐条断言）。

观感结论（关键，2026-09-12 更正）：**QSS 与 Fluent 控件并非"互不干扰"**。Fluent 只自绘
background / border / 圆角这类**绘制**，但 `padding` / `min-height` / `border` 会经
QStyleSheetStyle 影响 **sizeHint** —— 实测 `QLineEdit{padding:5px 8px; min-height:16px}`
把 EditableComboBox 压到 **18px 高**，而它的 dropButton 固定 25px 高 → 溢出、箭头看起来"歪"；
同一表单里按钮(24px) / 输入框(18px) / 下拉(25px) 高度也互不一致。
**已删除 `theme.py` 里会打到 Fluent 控件的规则**（`QPushButton` / `QLineEdit` / `QSpinBox` /
`QDoubleSpinBox` / `QComboBox` / `QTableWidget` / `QTableView` / `QHeaderView` 各段），
只保留 Fluent 不涉及的部分（背景、`QFrame#Card/TopBar/SideBar`、`QLabel#Badge`、`QListWidget`、
`QTabWidget/QTabBar`、`QTextEdit#Monitor`、`QMenu`、`QScrollBar`、`QStatusBar`、`QMessageBox`）。
QDialog/QMainWindow 背景仍由 QSS 控制（`#eef3f6` 浅灰蓝），故仍是「Fluent 控件 + 原背景/页签」
的混搭；要纯 Fluent 观感需改用 `qfluentwidgets.Dialog` 并继续移除剩余 QSS。

坑（已实测）：

- **`AcrylicLabel`（亚克力/毛玻璃）在本项目不可用**：`qfluentwidgets.common.image_utils` 顶层
  硬导入 numpy/scipy/colorthief，而 spec **必须排除 numpy**、本机也没有 scipy/colorthief。
  好在 `acrylic_label.py` 用 `try/except ImportError` 兜底（`isAcrylicAvailable=False`），
  **不会崩**，只是毛玻璃退化为原图。要用它得装 `[full]` 额外依赖，但 scipy 依赖 numpy，与
  「排除 numpy」的硬约束冲突 —— 暂不要用 `AcrylicLabel`。
- 布局坑：把 Fluent 控件放进 `QHBoxLayout` 的多列 `QVBoxLayout` 时，各列会被拉到等高，
  `BodyLabel`（QLabel）会吸收多余高度、文字随之垂直居中，导致 **各列标题不在同一水平线上**
  （实测高度 76 vs 108、错位 16px）。修法：给标题 `setSizePolicy(Preferred, Fixed)`，并给每列
  末尾 `addStretch(1)`（见 `app/qt/dialogs.py` 的 `choice_column()`）。
- **Fluent `ComboBox` 不是 `QComboBox`**（它继承 `QPushButton`）：没有 `setEditable` /
  `setInsertPolicy` / `currentIndexChanged` 那一套。需要「可手输」的下拉要用
  **`EditableComboBox`**（继承 `LineEdit`）—— 串口 / 波特率 / 网卡 IP 已按此处理。
- 对话框留白：`fit_dialog(dlg, win, min_w, min_h)` 的 `min_w/min_h` 是**下限**，给得比内容所需
  还大时窗口贴不到内容、多出的宽度全堆在**右侧**（遥控弹窗 460→420 后右边仍空约 120px；
  现收到 **300×150**）。各对话框现值：遥控 300×150、101 参数 480×220、104 参数 460×240、
  设备参数 360×170、统计 660×480。
- 表单控件尺寸：`dialogs.py` 的 `_line` / `_combo` 原来各处 width 混用 70/80/90/100/120/140，
  且 Fluent ComboBox 比 LineEdit 矮（25/27 vs 33），同一列相邻控件宽窄高低不一。现统一
  `setFixedSize(120, 33)`（`_FORM_W` / `_FORM_H`）。
- **101 / 104 参数对话框改为两列**（2026-09-12）：单列排下来又高又窄（101 646×569、104
  400×596）且右侧留白，改用 `QGridLayout` 两列（`pairs` + `divmod(i, 2)` 按行左→右填、标签
  `AlignRight`）后为 **101: 592×406** / **104: 628×386**，控件仍是 120 宽、并不挤。
  两个坑（均实测）：① 会撑宽的额外控件（「刷新串口」按钮、`center_hint` 说明文字）**不能放进
  值/标签格**，否则会把该列撑宽（实测跨列放按钮使左值列涨到 250+、窗口一度到 724 宽、中间
  一大片空白）—— 按钮放下一列起点、说明文字放网格下方独占一行；
  ② 101 的「链路地址(>255 自动 2 字节)」标签按要求缩短为「链路地址」。
- `qfluentwidgets` 运行时会在 stderr 打印一行 Pro 版广告，属正常。
- 新引入的 Qt 模块 `QtXml` / `QtSvg` / `QtSvgWidgets` 是 Fluent 的必需依赖，**不要加进 excludes**。

---

## 5. ApiBridge 接口约定（`core/api.py`，不要改方法名）

UI 层唯一后端入口，构造：`ApiBridge(root: Path)`；推送注册：`set_ui_push(callable)`。

| 功能组 | 方法 |
|---|---|
| 工程 | `get_project()`, `new_project()`, `load_project(path)`, `save_project(data, path="")`, `set_protocol_variant(variant)` |
| 会话 | `list_sessions()`, `create_session(data?)`, `update_session(sid, data)`, `delete_session(sid)`, `set_active_session(sid)` |
| 点表 | `upsert_point(point, sid?)`, `remove_point(ioa, sid?)`, `import_points_csv(path, sid?)`, `fill_missing_point_types(sid?)` |
| 连接 | `connect(params?)`, `disconnect(sid?)`, `disconnect_all()`, `is_connected(sid?)` |
| 四遥 | `general_interrogation()`, `clock_sync()`, `single_command(ioa, on, select)`, `double_command(ioa, state, select)`, `setpoint_float(ioa, value, select)`, `setpoint_normalized(ioa, value, select)`, `read_points(ioas, area, batch?)`, `read_all_setpoints(sid?, area, batch?)`, `cancel_command(ioa, kind)` |
| 定值 | `preset_setpoint(ioa, value, select, area)`, `fix_setpoint(area)`, `cancel_setpoint(ioa, kind?, area)`, `read_setting_area()`, `switch_setting_area(area)` |
| 监视 | `get_frames(limit)`, `clear_frames()`, `get_events(sid?, limit)`, `clear_events(sid?)`, `get_stats(sid?)` |
| 辅助 | `get_nics()`, `list_serial_ports()`, `get_type_names()` |

### 必须知道的行为语义（新加/易踩）

- `get_project()` 在工程数据之上附加两个 UI 字段：`session_connected`（各会话连接状态）与 **`project_path`**（当前工程文件路径，空串 = 新建未保存）。这两个字段只存在于返回值，**不会写进工程文件**。
- `save_project(data, path="")`：`path` 为空时沿用当前工程文件（已打开/已保存过的工程直接覆盖）；**新建且从未保存过会返回 `{"ok": False, "error": "工程尚未保存过，请先选择保存位置与文件名"}`**，不再回落到 `configs/default.json`。
- `new_project()`：内存态空白工程，`store.path = None`，**落盘前不写任何本地文件**（Tk 版 `on_close` 也只在 `store.path` 非空时才自动保存）。
- 海南双主站（`link_mode="hainan"`）**默认链路地址 1 字节**（`addr_size=1`）；显式传 `addr_size` 时尊重传入值；切回非海南模式回到 2。已实测 1 字节复位帧 `10 49 01 4A 16` 与需求文档示例一致。
- **已知缺陷**：`ui/app.js` 里调用了 `api("import_config")`，但 `ApiBridge` 没有该方法，WebView 版"导入工程"按钮是坏的。Qt 版请直接用 `load_project`。

### 事件推送格式（`set_ui_push` 收到的 dict）

| `type` | 关键字段 |
|---|---|
| `connection` | `session_id`, `state`（`connected` / `disconnected`） |
| `frame` | `session_id`, `asdu{type_id, cot, ...}` 及报文信息 |
| `points` | `session_id`, `cot`, `objects[{ioa, type_id, value, extra{...}}]`, `points`（本次变更后的点） |
| `cmd_result` | `ok`, `text`, `commit_modvals`, `clear_modvals` |

参考实现：`ui/app.js`（`window.__onNativeEvent`）与 `app/tk_shell.py` 的 `poll_events()`（队列 + `after(200)`）。

---

## 6. QSS ↔ 现有 `ui/styles.css` 对照要点

- 可以照搬：颜色、字体、圆角 `border-radius`、`padding`、`qlineargradient(...)` 渐变、`:hover` / `:checked` / `:disabled` 伪状态、`QTableView::item:selected` 等。
- 需要替代：`box-shadow` → `QGraphicsDropShadowEffect`；CSS 过渡动画 → `QPropertyAnimation`；flex 布局 → `QHBoxLayout` / `QGridLayout`。
- 已引入 **PySide6-Fluent-Widgets** 作为第二套 UI 方案（见 4.2 节，先用于 `RemoteDialog`）；它的控件自绘、与上面的 QSS 不冲突，两条路线可并存。
- 现成主题可参考：qdarkstyle、qt-material、PyDracula（注意其 GPL/MIT 许可与项目"接受 GPL"的一致性）。

---

## 7. 验收清单（Qt 版）

1. 默认启动即 Qt 界面；**不读取任何 `.html/.js/.css`，不起本地 HTTP，不依赖 WebView2**。
2. 多主站会话、101/104 参数、设备参数、四遥（含遥控 SBO、定值 202/203 读写固化撤销）、报文监视、事件与统计、工程新建/导入/保存全部可用。
3. 打包产物在**未装 Python** 的 Win10 x64 机器上可运行。
4. 在**发布目录处于加密/DLP 监控**的环境下仍能启动（需现场验证，这是本方案的核心目的）。
5. `PEIWANG_USE_TK=1` 仍能回退 Tk 界面。

---

## 8. 当前工作区状态（避免新会话误判）

**未提交的功能改动（等你确认后才提交）**：

- `core/api.py`、`app/tk_shell.py`、`ui/app.js`：工程保存语义（新建未保存不落盘；已有工程点保存直接覆盖，不重命名不选目录）。
- `core/api.py`、`app/tk_shell.py`、`ui/app.js`：海南双主站默认链路地址 1 字节。
- `configs/default.json`：被改成完整 101 工程，**用户已确认保留原样**，提交时应排除。
- `requirements.txt`、`peiwang.spec`：加 PySide6-Fluent-Widgets 依赖与打包收集（见 4.2 节）。
- `app/qt/common.py`（控件工厂）、`app/qt/dialogs.py`、`app/qt/panels.py`、`app/qt/main_window.py`：
  全面 Fluent 化（按钮/标签/输入框/下拉/复选框/卡片/表格）并收紧对话框留白（见 4.2 节）。
- 未跟踪：`reasonix.toml`、`.reasonix/`（工具产物）。

**参考**：`scripts/sim_iec101.py`、`scripts/sim_iec101_api.py` 可无硬件自测 101 全流程。

---

## 9. 约定（沿用 `handoff.md`）

- 不主动 commit / push，用户说"提交"再提交。
- 源码中大量 `[AGENT_CHANGE_*]` 标记，清理需用户明确同意。
- 默认界面已切到 Qt；`PEIWANG_USE_TK=1` 可回退 Tk，Qt 版通过现场验收前不要删 Tk 界面。
- 改动前先看 `handoff.md` 的现场备忘（KW-2200 / 海南）与已知坑。
