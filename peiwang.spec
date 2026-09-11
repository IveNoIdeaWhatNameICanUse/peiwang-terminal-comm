# -*- mode: python ; coding: utf-8 -*-
# [AGENT_CHANGE_BEGIN] 2026-09-07 PyInstaller打包配置
"""配网终端通讯 — PyInstaller 打包（默认 PySide6/Qt，不含 WebView2 内核）。

打包要点：
- **打包模式：onefile**（2026-09-12 按用户要求由 onedir 改过来，便于「只给单个 exe」分发）。
  代价：每次启动解压到 %TEMP% —— 那正是 DLP 重点监控目录，且启动慢数秒。若现场被 DLP 拦，
  改回 onedir：恢复下方 `EXE(..., [], exclude_binaries=True)` + `COLLECT(...)` 即可。
- 排除 numpy：本机 numpy 2.2.6 与旧编译模块冲突，带进去会打出坏包。
- 只用 QtCore/QtGui/QtWidgets，其余 Qt 模块（WebEngine/Quick/Qml/Charts/...）全部排除。
- tkinter 保留打包：PEIWANG_USE_TK=1 的回退界面仍要能启动（确认稳定前不删 Tk 界面）。
- PySide6-Fluent-Widgets：资源内嵌于 _rc/resource.py（无外部 datas）；包内全是静态导入，
  hiddenimports 只给顶层包名即可，非 Windows 子包在 excludes 里排除（见下方注释）。
"""

# [AGENT_CHANGE_BEGIN] 2026-09-11 PySide6-Fluent-Widgets 打包要点
# - qfluentwidgets / qframelesswindow 的图片、字体、qss 资源全部内嵌在 _rc/resource.py，
#   没有磁盘资源文件，无需额外 datas。
# - 两个包内部全是静态 import，hiddenimports 只给顶层包名即可，PyInstaller 会自行把
#   common/components/window/_rc 等子模块全部收进来。
#   （不要用 collect_submodules：它靠“导入”枚举，本机导入 qfluentwidgets 会因
#     shiboken/numpy 环境问题失败，导致 common/components 子模块漏收。）
# - 下列子包 Windows 用不到，且会牵出已排除的 Qt 模块或非 Windows 依赖，故在 excludes 排除：
#     qfluentwidgets.multimedia      -> QtMultimedia / QtMultimediaWidgets
#     qframelesswindow.webengine     -> QtWebEngineWidgets
#     qframelesswindow.linux / .mac  -> 非 Windows 平台实现（.mac 依赖 Cocoa/objc）
#     qframelesswindow.utils.linux_utils / .mac_utils -> 同上
# - qframelesswindow 在 Windows 经 pywin32 访问窗口特效（utils/win32_utils.py），显式声明。
# [AGENT_CHANGE_END] 2026-09-11 PySide6-Fluent-Widgets 打包要点

block_cipher = None

a = Analysis(
    ['app/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('configs', 'configs'),
        ('NOTICE.md', '.'),
    ],
    hiddenimports=[
        'core',
        'core.api',
        'core.events',
        'core.eventlog',
        'core.project',
        'net',
        'protocol',
        'protocol.iec104',
        'protocol.iec104.master',
        'protocol.iec104.codec',
        'protocol.iec104.const',
        'protocol.iec101',
        'protocol.iec101.master',
        'protocol.iec101.link',
        'protocol.iec101.hainan',
        'app.tk_shell',
        'app.qt',
        'app.qt.theme',
        'app.qt.common',
        'app.qt.panels',
        'app.qt.dialogs',
        'app.qt.main_window',
        # PySide6-Fluent-Widgets：只给顶层包名，子模块由 PyInstaller 静态追踪
        'qfluentwidgets',
        'qframelesswindow',
        'darkdetect',
        'win32api',
        'win32con',
        'win32gui',
        'win32print',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'numpy',
        'webview',
        'pythonnet',
        'clr_loader',
        'bottle',
        'proxy_tools',
        'cefpython3',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtQml',
        'PySide6.QtQmlModels',
        'PySide6.QtQuick',
        'PySide6.QtQuickWidgets',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtSql',
        'PySide6.QtTest',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtDesigner',
        'PySide6.QtHelp',
        'PySide6.QtBluetooth',
        'PySide6.QtNfc',
        'PySide6.QtPositioning',
        'PySide6.QtLocation',
        'PySide6.QtRemoteObjects',
        'PySide6.QtScxml',
        'PySide6.QtSensors',
        'PySide6.QtSerialBus',
        'PySide6.QtSerialPort',
        'PySide6.QtSpatialAudio',
        'PySide6.QtStateMachine',
        'PySide6.QtTextToSpeech',
        'PySide6.QtUiTools',
        'PySide6.QtWebChannel',
        'PySide6.QtWebSockets',
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        # PySide6-Fluent-Widgets 中 Windows 用不到的子包（见文件头打包要点）
        'qfluentwidgets.multimedia',
        'qframelesswindow.webengine',
        'qframelesswindow.linux',
        'qframelesswindow.mac',
        'qframelesswindow.utils.linux_utils',
        'qframelesswindow.utils.mac_utils',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# [AGENT_CHANGE 2026-09-12] 改为 onefile：按用户要求「只给单个 exe」分发。
# 已知代价（用户确认接受）：每次启动会把资源解压到 %TEMP% —— 启动慢数秒，且 %TEMP% 正是
# DLP 重点监控目录，与路线 1「躲 DLP」的初衷相冲突。若日后要回到 onedir（现场推荐），
# 把本段恢复为：EXE(..., [], exclude_binaries=True) + COLLECT(exe, a.binaries, a.zipfiles, a.datas)。
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    exclude_binaries=False,
    name='配网终端通讯',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
# [AGENT_CHANGE_END] 2026-09-07 PyInstaller打包配置
