# -*- mode: python ; coding: utf-8 -*-
# tools/packaging/gui-onefile.spec —— GUI onefile 单文件形态(spec §4.1.1)
# 运行语境:build.py 已把本 spec 与入口拷入 .build-work/onefile/ 根,cwd=该目录
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ["entry_gui.py"],
    pathex=["."],
    binaries=[],
    datas=collect_data_files("migration") + [("build-form.txt", ".")],  # 末项=构建形态标记(后验用)
    hiddenimports=["webview.platforms.winforms",  # 包名是 webview(评审 P2-8)
                   "migration._form"],         # 形态标记:真产物内后验通道
    hookspath=[],
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
    name="mcmig-gui",           # 版本后缀由 build.py 落产物时补
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,              # 小白双击不弹黑窗(spec §4.1.2)
)
