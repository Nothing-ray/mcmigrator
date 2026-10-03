# -*- mode: python ; coding: utf-8 -*-
# tools/packaging/app-onedir.spec —— onedir 全家桶(spec §4.1.1):CLI+GUI 双 EXE
# 双 EXE 各自一条 Analysis(每个 EXE 只携带自己的入口,评审 P1-1)
from PyInstaller.utils.hooks import collect_data_files

_datas = collect_data_files("migration") + [("build-form.txt", ".")]  # 末项=构建形态标记(后验用)

a_cli = Analysis(
    ["entry_cli.py"],
    pathex=["."],
    binaries=[],
    datas=_datas,
    hiddenimports=["migration._form"],  # 形态标记:真产物内后验通道
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
a_gui = Analysis(
    ["entry_gui.py"],
    pathex=["."],
    binaries=[],
    datas=_datas,
    hiddenimports=["webview.platforms.winforms", "migration._form"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz_cli = PYZ(a_cli.pure)
pyz_gui = PYZ(a_gui.pure)

exe_cli = EXE(pyz_cli, a_cli.scripts, [], exclude_binaries=True, name="mcmig",
              debug=False, strip=False, upx=False, console=True)
exe_gui = EXE(pyz_gui, a_gui.scripts, [], exclude_binaries=True, name="mcmig-gui",
              debug=False, strip=False, upx=False, console=False)

coll = COLLECT(exe_cli, exe_gui, a_cli.binaries, a_cli.datas, a_gui.binaries, a_gui.datas,
               strip=False, upx=False, name="mcmig")
