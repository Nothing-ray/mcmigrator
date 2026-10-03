"""计划执行器:按 MigrationPlan 执行复制,含冲突备份/MD5 校验/ASK 回调。

语义见 Reference/specs/2026-08-31-modpack-swap-workflow-design.md §2.1/§3.1:
- 仅 COPY 动手;ASK 走 ask_handler
- 可重入:目标与源 MD5 相同 → identical(不备份)
- 覆盖已存在文件前先镜像备份到 <dst>/_conflict_backup/<rel>(首份不可逆)
- 复制/备份/校验/换名下沉 fsops.copy_atomic(事务式,任一步失败回滚且不留 tmp)
- 逐文件容错:捕获 FsOpsError,单文件失败不中断整个计划;绝不删除任何文件
- 取消检查点+journal 三段序挂点(批次I-T6,spec §4.3):should_cancel 在动作间
  检查(当前动作单元完成后才停,安全边界内绝无半途文件);before/after_action
  包住每个动手动作(①意图 write-ahead → ②操作 → ③完成)。批次I-W3 T3 收口
  (#4b/#5):结果先入列/上报再写完成记录(after_action 异常不丢结果,分发
  计数不失真);after_action 仅对有意向的动作(COPY/ASK)回调且不再排除
  asked_no(结局已知即收口),SKIP 无意图不触发(不再产生无效 journal 重写);
  其 JournalError → journal_failed=True 停发(与取消同型安全停止)
"""

from __future__ import annotations

import logging
import shutil  # noqa: F401 — 测试 monkeypatch 锚点:改全局 shutil.copy2 属性,
# fsops 内的 copy2 调用同样被拦截,逐文件容错路径得以在测试中验证

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .fsops import FsOpsError, copy_atomic, md5_of as _md5_of
from .journal import JournalError
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
        # 停发标志(批次I-T6,spec §4.3):取消/journal 写失败都以「当前动作
        # 单元已完成」为安全边界停止分发后续动作,由调用方读标志区分呈现
        self.cancelled = False
        self.journal_failed = False

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
        *,
        should_cancel: Callable[[], bool] | None = None,
        before_action: Callable[[ActionRecord], None] | None = None,
        after_action: Callable[[ActionRecord, FileResult], None] | None = None,
    ) -> list[FileResult]:
        """执行计划,返回逐文件结果(按 plan.actions 顺序)。

        Args:
            dry_run: True 时零写盘,结果为推演。
            progress_cb: 逐文件实时进度回调——每个 FileResult 产出后立即同步调用
                (GUI 进度条数据源);None 时无回调,行为与旧版完全一致。
            should_cancel: 取消检查点(动作间轮询;首批动作恒执行,此后命中即停
                发)→ ``self.cancelled = True`` 并返回部分结果(批次I-T6)。
            before_action: 动手动作(COPY/ASK)执行前的回调——①意图持久化锚点
                (write-ahead);抛 ``JournalError`` → ``self.journal_failed = True``
                并停止分发(写失败停发,与取消同型安全停止)。
            after_action: 有意向动作(COPY/ASK)完成后的回调——③完成持久化
                锚点;asked_no(用户未确认,零写盘)同样触发(结局已知即收口,
                批次I-W3 T3);SKIP 无意图不触发;失败动作同样触发。结果先入列
                (``results``/progress_cb)再调本回调——其抛 ``JournalError`` →
                ``self.journal_failed = True`` 并停止分发,该文件结果已入列,
                分发计数不失真(#4b)。

        Returns:
            逐文件执行结果;取消/journal 停发时为部分结果(按已分发顺序)。
        """
        cb: Callable[[FileResult], None] = (
            progress_cb if progress_cb is not None else (lambda _r: None)
        )
        results: list[FileResult] = []
        for action in self.plan.actions:
            # 取消检查点(动作间):首个动作恒执行(results 为空不判停),此后
            # 命中即停——当前动作单元已完成,安全边界内绝无半途文件(spec §4.3)
            if should_cancel is not None and results and should_cancel():
                self.cancelled = True
                break
            if action.behavior in (Behavior.COPY, Behavior.ASK) and before_action is not None:
                try:
                    before_action(action)  # ①意图(write-ahead)
                except JournalError as e:
                    # journal 写失败:后续动作不再分发(停发),与取消同型安全停止
                    self.journal_failed = True
                    log.error("journal 写入失败,停止分发后续动作: %s", e)
                    break
            result: FileResult
            if action.behavior == Behavior.COPY:
                result = self._copy_one(action.path, dry_run)  # ②操作
            elif action.behavior == Behavior.ASK:
                result = (self._copy_one(action.path, dry_run)  # ②操作(确认后动手)
                          if self.ask_handler(action)
                          else FileResult(action.path, "asked_no"))
            else:
                result = FileResult(action.path, "skipped")
            results.append(result)
            cb(result)  # 结果先入列/上报,完成记录随后(写失败不失计数,#4b)
            if (action.behavior in (Behavior.COPY, Behavior.ASK)
                    and after_action is not None):
                try:
                    after_action(action, result)  # ③完成(asked_no 结局已知同样收口)
                except JournalError as e:
                    # 完成记录写失败:后续动作不再分发;本文件结果已入列,
                    # 分发计数不失真(批次I-W3 T3,#4b)
                    self.journal_failed = True
                    log.error("journal 完成记录写入失败,停止分发后续动作: %s", e)
                    break
        return results
