# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。用法: pyinstaller build.spec"""

import sys
from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH)

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[(str(project_root / "config.yaml"), ".")],
    hiddenimports=[
        "argostranslate",
        "argostranslate.package",
        "argostranslate.translate",
        "faster_whisper",
        "ctranslate2",
        "miniaudio",
        "webrtcvad",
        "edge_tts",
        "pyaudiowpatch",
        "sounddevice",
        "yaml",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="ZoomTranslate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name="ZoomTranslate",
)
