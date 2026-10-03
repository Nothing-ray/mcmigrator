"""GUI 发行入口(薄启动脚本,spec §4.1.2 评审 P1-1/P1-2)。

PyInstaller 只消费脚本入口——包内模块的函数入口在入口脚本语境下相对导入
必然失败,故以绝对导入转发;console=False 形态下 ``sys.stdout/stderr`` 为
``None``,先兜底为 devnull 句柄再 import 主程序(cli 的 isatty 探测与
uvicorn 日志配置都假定流非 None)。
"""

import os
import sys

for _name in ("stdout", "stderr"):
    if getattr(sys, _name, None) is None:
        # 打包形态生命周期与进程同寿,不经 with 关闭(生命周期例外)
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115

from migration.gui.app import main  # noqa: E402

raise SystemExit(main())
