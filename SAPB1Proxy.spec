# -*- mode: python ; coding: utf-8 -*-
import sys


if sys.platform == "win32":
    platform_hiddenimports = [
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "pystray._win32",
    ]
elif sys.platform == "darwin":
    platform_hiddenimports = [
        "webview.platforms.cocoa",
        "pystray._darwin",
    ]
else:
    platform_hiddenimports = [
        "webview.platforms.gtk",
        "pystray._gtk",
    ]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("web", "web")],
    hiddenimports=platform_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SAPB1Proxy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
