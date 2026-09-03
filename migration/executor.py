"""计划执行器:按 MigrationPlan 执行复制,含冲突备份/MD5 校验/ASK 回调。

语义见 Reference/specs/2026-08-31-modpack-swap-workflow-design.md §2.1/§3.1:
- 仅 COPY 动手;ASK 走 ask_handler
- 可重入:目标与源 MD5 相同 → identical(不备份)
- 覆盖已存在文件前先镜像备份到 <dst>/_conflict_backup/<rel>
- 复制后 MD5 校验;绝不删除任何文件
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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


def _md5_of(p: Path) -> str | None:
    """计算文件 MD5;文件不存在/不可读返回 None。"""
    h = hashlib.md5()
    try:
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


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

    def _backup(self, rel: str, dst_file: Path) -> bool:
        """覆盖前把目标现有文件镜像备份;返回是否发生备份。

        首份备份=目标原始值,不可逆,绝不覆盖(重复迁移 --force 时保留最早的
        原始内容;已存在即跳过)。
        """
        bak = self.dst_root / BACKUP_DIR / rel
        if bak.exists():
            return False
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dst_file, bak)
        return True

    def _copy_one(self, rel: str, dry_run: bool) -> FileResult:
        """执行单个 COPY:identical 短路 → 备份 → 复制 → 校验(逐文件容错)。"""
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
                if dst_file.is_file():
                    backed_up = self._backup(rel, dst_file)
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                if _md5_of(dst_file) != src_md5:
                    return FileResult(rel, "copied", backed_up=backed_up, failed=True,
                                      error="复制后 MD5 校验不一致")
            return FileResult(rel, "copied", backed_up=backed_up)
        except OSError as e:
            # 逐文件容错:单个文件复制/备份失败不中断整个计划(spec §2.1)
            log.warning("复制失败 %s: %s", rel, e)
            return FileResult(rel, "copied", failed=True, error=f"复制失败: {e}")

    def execute(self, dry_run: bool = False) -> list[FileResult]:
        """执行计划,返回逐文件结果(按 plan.actions 顺序)。

        Args:
            dry_run: True 时零写盘,结果为推演。
        """
        results: list[FileResult] = []
        for action in self.plan.actions:
            if action.behavior == Behavior.COPY:
                results.append(self._copy_one(action.path, dry_run))
            elif action.behavior == Behavior.ASK:
                if self.ask_handler(action):
                    results.append(self._copy_one(action.path, dry_run))
                else:
                    results.append(FileResult(action.path, "asked_no"))
            else:
                results.append(FileResult(action.path, "skipped"))
        return results
