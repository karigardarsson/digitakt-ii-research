# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH)

a = Analysis(
    [str(root / "builder_gui.py")],
    pathex=[str(root)],
    binaries=[(str(root / "native/elektron-firmware-tool.exe"), "native")],
    datas=[
        (str(root / "resources/dv-v51-overlay.bin"), "resources"),
        (str(root / "resources/dv-v51-overlay-manifest.json"), "resources"),
    ],
    # PyInstaller's inspect runtime hook reaches urllib.parse dynamically.
    # Keep both modules explicit so the windowed one-file build cannot omit them.
    hiddenimports=["urllib", "urllib.parse"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DT2-DejaVu-Builder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
