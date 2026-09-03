"""gui 包:FastAPI 薄翻译层(server)+ 集中文案(STRINGS)。

分层纪律(spec §2):本包只做「薄翻译+安全+单任务锁」,管线一律消费
``migration.pipeline``,不 import cli 的任何私有函数;核心模块不感知 HTTP。
"""
