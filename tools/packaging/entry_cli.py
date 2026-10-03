"""CLI 发行入口(薄启动脚本,spec §4.1.2 评审 P1-1):绝对导入转发。

控制台形态(console=True)无 stdio 兜底需求——真实控制台必然有流。
"""

from migration.cli import main

raise SystemExit(main())
