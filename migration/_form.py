"""构建形态标记(批次I-W4 spec §4.1.4/决策 D7)。

``FORM`` 是更新资产选择的**唯一事实来源**:source/onefile/onedir 三值。
入库恒为 ``"source"``;打包脚本在**独立工作副本**上按产物改写为
onefile/onedir 后构建(spec v3 改写纪律),产物内即固化——运行时不得
从 exe 文件名、入口形态、显示模式或 ``sys._MEIPASS`` 有无推断形态。
"""

FORM: str = "source"
