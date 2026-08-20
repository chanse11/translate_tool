# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。用法: pyinstaller build.spec"""

from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH)

# 使用脱敏配置，避免把开发机上的 api_key 打进分发包
bundled_config = project_root / "packaging" / "config.yaml"
app_icon = project_root / "assets" / "app_icon.ico"

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(bundled_config), "."),
        (str(app_icon), "assets"),
    ],
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
        "websockets",
        "httpx",
        "httpx._transports",
        "httpcore",
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "bailian",
        "bailian.livetranslate",
        "bailian.chat",
        "pipeline.factory",
        "pipeline.resources",
        "pipeline.en_to_zh",
        "pipeline.zh_to_en",
        "pipeline.bailian_en_to_zh",
        "pipeline.bailian_zh_to_en",
        "translate.argos_translator",
        "translate.lang_detect",
        "storage.history_db",
        "ui.main_window",
        "ui.styles",
        "ui.ptt_key_filter",
        "config_loader",
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
    name="TranslateTool",
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
    icon=str(app_icon),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TranslateTool",
)
