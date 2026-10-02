"""job journal:write-ahead 三段序与崩溃恢复判定(spec §4.3,批次I-T6)。

三段序锚点(executor 挂点):
1. ``record_intent``   —— 动作执行**前**持久化意图(write-ahead);
2. 文件操作(fsops 落盘,executor 内部);
3. ``record_completion`` —— 动作完成后持久化完成记录。

崩溃窗口语义:②已落盘而 ③ 未写即崩溃,重启后该条目经 ``unfinished`` 读作
「待核对」,而非「未执行」——迁移状态只能核对,不可臆断(Review Focus 6)。

写入策略:内态全量 ``write_json_atomic`` 原子重写(tmp+replace,绝无半截
JSON);写失败(OSError 族,含 FsOpsError)在 journal 层统一包装为
``JournalError`` 上抛,执行侧(Executor.before_action 挂点)据此停止分发
后续写入动作。

启动期恢复判定:``scan_interrupted`` 扫描 jobs 目录,列出「未收尾(finish
未标记)且有 unfinished 条目」的 job 供页面横幅提示(待核对 ≠ cancelled)。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .fsops import write_json_atomic

log = logging.getLogger(__name__)


class JournalError(Exception):
    """journal 读/写失败(执行侧须停止分发后续写入动作)。"""


class JobJournal:
    """单个 job 的 write-ahead journal,存储 ``<journal_dir>/<job_id>.json``。

    内态 schema::

        {"kind": "migrate", "started_at": "<ISO8601>", "finished": False,
         "entries": {"<rel>": {"detail": {...}, "completed": False}}}

    重开语义:构造时目标文件已存在则从盘上恢复内态(重启后原实例继续判定
    ``unfinished``);文件损坏抛 ``JournalError``(由调用方决定跳过/提示)。

    写失败留痕:任一次重写失败置 ``write_failed = True``——Executor 把
    before_action 的 JournalError 吞进 ``journal_failed`` 标志、只返回部分
    结果,执行编排层(GUI job)无法从返回值区分「写失败停发」与「正常完成」,
    须读本标志呈现停发原因并阻止「已执行」回写。
    """

    def __init__(self, journal_dir: Path, job_id: str, kind: str) -> None:
        """初始化 journal(文件已存在时载入恢复)。

        Args:
            journal_dir: journal 目录(<game_root>/.mcmig/jobs)。
            job_id: job 唯一标识(文件名 ``<job_id>.json``)。
            kind: job 类型("migrate"/"plan")。

        Raises:
            JournalError: 盘上已有 journal 但读取/解析失败。
        """
        self.path = journal_dir / f"{job_id}.json"
        self.kind = kind
        self.finished = False
        self.write_failed = False
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._entries: dict[str, dict] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        """从盘上恢复内态(损坏文件抛 JournalError,绝不静默清零)。"""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise JournalError(f"journal 读取失败: {self.path}({e})") from e
        if not isinstance(data, dict):
            raise JournalError(f"journal 顶层非对象: {self.path}")
        self.kind = str(data.get("kind") or self.kind)
        self.finished = bool(data.get("finished", False))
        raw = data.get("entries")
        if isinstance(raw, dict):
            for rel, entry in raw.items():
                detail = entry.get("detail") if isinstance(entry, dict) else None
                completed = entry.get("completed", False) if isinstance(entry, dict) else False
                self._entries[str(rel)] = {
                    "detail": detail if isinstance(detail, dict) else {},
                    "completed": bool(completed),
                }

    def _rewrite(self) -> None:
        """内态全量原子重写;OSError 族统一包装为 JournalError(并留痕 write_failed)。

        Raises:
            JournalError: 写入/换名失败(磁盘满/无权限等);执行侧应停止分发。
        """
        state = {
            "kind": self.kind,
            "started_at": self.started_at,
            "finished": self.finished,
            "entries": self._entries,
        }
        try:
            write_json_atomic(self.path, state)
        except OSError as e:
            # 留痕:Executor 吞掉 before_action 的 JournalError 只返回部分结果,
            # 编排层凭本标志才能区分「写失败停发」与「正常完成」
            self.write_failed = True
            raise JournalError(f"journal 写入失败: {self.path}({e})") from e

    def record_intent(self, rel: str, detail: dict) -> None:
        """①持久化动作意图(write-ahead;动作动手前调用)。

        Args:
            rel: 版本内相对路径。
            detail: 动作描述(如 {"op": "copy", "backup": ...},供恢复期核对)。

        Raises:
            JournalError: 写入失败(执行侧应停止分发)。
        """
        self._entries[rel] = {"detail": detail, "completed": False}
        self._rewrite()

    def record_completion(self, rel: str) -> None:
        """③持久化动作完成(动作落盘后调用;失败动作同样收口=结局已知)。

        Args:
            rel: 版本内相对路径。

        Raises:
            JournalError: 写入失败。
        """
        entry = self._entries.get(rel)
        if entry is not None:
            entry["completed"] = True
        self._rewrite()

    def unfinished(self) -> list[dict]:
        """列出有意图无完成的条目(待核对,≠未执行)。

        Returns:
            ``[{"rel": ..., "detail": {...}}]``,按记录(分发)顺序。
        """
        return [
            {"rel": rel, "detail": entry["detail"]}
            for rel, entry in self._entries.items()
            if not entry["completed"]
        ]

    def finish(self) -> None:
        """收尾标记(正常终态:完成/取消都在安全边界内结束,退出中断清单)。

        Raises:
            JournalError: 写入失败。
        """
        self.finished = True
        self._rewrite()


def scan_interrupted(jobs_dir: Path) -> list[dict[str, object]]:
    """启动期扫描 jobs 目录:未收尾且有 unfinished 条目的 job → 待核对清单。

    单个 journal 损坏只跳过并记 warning(不因一个坏文件阻断启动扫描);
    目录不存在/为空返回空清单。

    Args:
        jobs_dir: journal 目录(<game_root>/.mcmig/jobs)。

    Returns:
        ``[{"job_id": ..., "kind": ..., "entries": [{"rel": ..., "detail": {...}}]}]``,
        按文件名升序(稳定)。
    """
    if not jobs_dir.is_dir():
        return []
    items: list[dict[str, object]] = []
    for p in sorted(jobs_dir.glob("*.json")):
        try:
            journal = JobJournal(jobs_dir, p.stem, "")
        except JournalError as e:
            log.warning("journal 损坏/不可读,已跳过 %s: %s", p, e)
            continue
        if journal.finished:
            continue
        entries = journal.unfinished()
        if entries:
            items.append({"job_id": p.stem, "kind": journal.kind, "entries": entries})
    return items
