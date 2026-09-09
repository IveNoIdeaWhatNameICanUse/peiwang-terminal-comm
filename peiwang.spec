# -*- mode: python ; coding: utf-8 -*-
# [AGENT_CHANGE_BEGIN] 2026-09-07 PyInstaller打包配置
"""配网终端通讯 — PyInstaller onedir（默认 Tk，不含 WebView2）。"""

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
        'core.project',
        'net',
        'protocol',
        'protocol.iec104',
        'protocol.iec104.master',
        'protocol.iec104.codec',
        'protocol.iec104.const',
        'app.tk_shell',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'webview',
        'pythonnet',
        'clr_loader',
        'bottle',
        'proxy_tools',
        'cefpython3',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='配网终端通讯',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='配网终端通讯',
)
# [AGENT_CHANGE_END] 2026-09-07 PyInstaller打包配置
