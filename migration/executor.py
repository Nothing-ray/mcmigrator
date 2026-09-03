"""计划执行器:按 MigrationPlan 执行复制,含冲突备份/MD5 校验/ASK 回调。

语义见 Reference/specs/2026-08-31-modpack-swap-workflow-design.md §2.1/§3.1:
- 仅 COPY 动手;ASK 走 ask_handler
- 可重入:目标与源 MD5 相同 → identical(不备份)
- 覆盖已存在文件前先镜像备份到 <dst>/_conflict_backup/<rel>(首份不可逆)
- 复制/备份/校验/换名下沉 fsops.copy_atomic(事务式,任一步失败回滚且不留 tmp)
- 逐文件容错:捕获 FsOpsError,单文件失败不中断整个计划;绝不删除任何文件
"""

from __future__ import annotations

import logging
import shutil  # noqa: F401 — 测试 monkeypatch 锚点:改全局 shutil.copy2 属性,
# fsops 内的 copy2 调用同样被拦截,逐文件容错路径得以在测试中验证

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .fsops import FsOpsError, copy_atomic, md5_of as _md5_of
from .plan import ActionRecord, Behavior, MigrationPlan

log = logging.getLogger(__name__)

BACKUP_DIR = "_conflict_backup"


@dataclass(frozen=True)
class FileResult:
    """单文件执行结果。

    Attributes:
        path: 相对路径。
        status: copied / identical / asked_no / skipped。
        backed_up: 本次复制前是否发生了冲突备份。
        failed: 复制或校验失败。
        error: 失败原因。
    """

    path: str
    status: str
    backed_up: bool = False
    failed: bool = False
    error: str | None = None


class Executor:
    """执行 MigrationPlan 的 COPY/ASK 动作(纯逻辑,交互由 ask_handler 注入)。"""

    def __init__(
        self,
        plan: MigrationPlan,
        src_root: Path,
        dst_root: Path,
        ask_handler: Callable[[ActionRecord], bool],
    ) -> None:
        """初始化执行器。

        Args:
            plan: 已审阅的迁移计划。
            src_root: 源根目录(按 plan.path 相对定位;保持泛化,未来可指向包内目录)。
            dst_root: 目标版本根目录。
            ask_handler: ASK 动作的回调,返回 True=迁移。
        """
        self.plan = plan
        self.src_root = src_root
        self.dst_root = dst_root
        self.ask_handler = ask_handler

    def _copy_one(self, rel: str, dry_run: bool) -> FileResult:
        """执行单个 COPY:identical 短路 → fsops 事务复制(逐文件容错)。

        事务细节(冲突备份镜像/首份不可逆/tmp+MD5 校验+原子换名/失败回滚)
        全部下沉 fsops.copy_atomic,本方法只做前置判定与结果归并。
        """
        src_file = self.src_root / rel
        dst_file = self.dst_root / rel
        try:
            if not src_file.is_file():
                return FileResult(rel, "copied", failed=True, error="源文件不存在")
            src_md5 = _md5_of(src_file)
            if src_md5 is None:
                # 源不可读:复制无意义且校验必然失真,直接判失败
                return FileResult(rel, "copied", failed=True, error="源文件不可读(无法计算 MD5)")
            if dst_file.is_file() and _md5_of(dst_file) == src_md5:
                return FileResult(rel, "identical")
            backed_up = False
            if not dry_run:
                backed_up = copy_atomic(
                    src_file, dst_file, rel=rel, backup_dir=self.dst_root / BACKUP_DIR
                )
            return FileResult(rel, "copied", backed_up=backed_up)
        except FsOpsError as e:
            # 逐文件容错:单个文件复制/备份失败不中断整个计划(spec §2.1)
            log.warning("复制失败 %s: %s", rel, e)
            return FileResult(rel, "copied", failed=True, error=f"复制失败: {e}")

    def execute(
        self,
        dry_run: bool = False,
        progress_cb: Callable[[FileResult], None] | None = None,
    ) -> list[FileResult]:
        """执行计划,返回逐文件结果(按 plan.actions 顺序)。

        Args:
            dry_run: True 时零写盘,结果为推演。
            progress_cb: 逐文件实时进度回调——每个 FileResult 产出后立即同步调用
                (GUI 进度条数据源);None 时无回调,行为与旧版完全一致。
        """
        cb: Callable[[FileResult], None] = (
            progress_cb if progress_cb is not None else (lambda _r: None)
        )
        results: list[FileResult] = []
        for action in self.plan.actions:
            result: FileResult
            if action.behavior == Behavior.COPY:
                result = self._copy_one(action.path, dry_run)
            elif action.behavior == Behavior.ASK:
                if self.ask_handler(action):
                    result = self._copy_one(action.path, dry_run)
                else:
                    result = FileResult(action.path, "asked_no")
            else:
                result = FileResult(action.path, "skipped")
            results.append(result)
            cb(result)  # 实时回调:单文件完成即上报,而非执行完批量回放
        return results
