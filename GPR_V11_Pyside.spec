# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path.cwd()

datas = [
    (str(project_root / "README.md"), "."),
    (str(project_root / "qt.conf"), "."),
    (str(project_root / "release_launcher.bat"), "."),
    (str(project_root / "RELEASE_INSTRUCTIONS.txt"), "."),
    (str(project_root / "gpr_lab_pro" / "processing" / "pipeline.py"), "gpr_lab_pro/processing"),
    (str(project_root / "gpr_lab_pro" / "resources" / "overview"), "gpr_lab_pro/resources/overview"),
]

online_map_config = project_root / "online_map.local.json"
if online_map_config.exists():
    datas.append((str(online_map_config), "."))

offline_tiles_root = project_root.parent / "offline_tiles"
if offline_tiles_root.exists():
    datas.append((str(offline_tiles_root), "offline_tiles"))

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "matplotlib.backends.backend_qtagg",
    "gpr_lab_pro.algorithms",
    "gpr_lab_pro.algorithms.core",
    "gpr_lab_pro.algorithms.external",
    "gpr_lab_pro.models",
]

a = Analysis(
    ["gpr_lab_pro/app/main.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="GPR_Lab_Pro_V3",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="GPR_Lab_Pro_V3",
)
