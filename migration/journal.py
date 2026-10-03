"""job journal:write-ahead 三段序与崩溃恢复判定(spec §4.3,批次I-T6;W3-T3 JSONL 增量化)。

三段序锚点(executor 挂点):
1. ``record_intent``   —— 动作执行**前**持久化意图(write-ahead);
2. 文件操作(fsops 落盘,executor 内部);
3. ``record_completion`` —— 动作完成后持久化完成记录。

崩溃窗口语义:②已落盘而 ③ 未写即崩溃,重启后该条目经 ``unfinished`` 读作
「待核对」,而非「未执行」——迁移状态只能核对,不可臆断(Review Focus 6)。

写入策略(批次I-W3 T3,#13):存储改 **JSONL 追加**——首行 start(携带身份
kind/src/dst/game_root),此后意图/完成/收尾各追加一行,既有行绝不重写。
相比旧版全量 ``write_json_atomic`` 重写:增量写省 IO,且单行损坏只跳过该行
(折叠时 warning),不毁整个 journal。写失败(OSError 族)在 journal 层包装
为 ``JournalError`` 上抛并留痕 ``write_failed``,执行侧(Executor 挂点)据此
停止分发后续写入动作。

旧格式兼容:盘上存在旧版 ``<job_id>.json``(全量重写格式)且无 ``.jsonl``
时按旧格式**只读恢复**(不迁移写;仅 scan_interrupted/dismiss 只读消费)。

启动期恢复判定(批次I-W3 T3,行为变更白名单④):``scan_interrupted`` 对
**未收尾**(finish 未标记)的 job 即报;带身份的 journal 以实例锁探针
``_journal_owner_alive`` 判活——锁可获取=前任已死→报;锁被持(GUI 或跨进程
CLI)=活任务→跳过(跨进程活性判定,替代仅识本 app 的 active_id 过滤);
旧格式无身份→维持旧判据(有 unfinished 条目才报,保守不扩大)。``finished``
的 journal 文件在扫描时删除(清扫,吸收 #9:已尽其用,不再累积)。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .instlock import InstanceLockError, instance_locks

log = logging.getLogger(__name__)

# 实例锁探针超时秒数:短超时快速判活(锁被持时 0.2s 即失败,不拖慢扫描)
_PROBE_TIMEOUT_SECONDS = 0.2


class JournalError(Exception):
    """journal 读/写失败(执行侧须停止分发后续写入动作)。"""


class JobJournal:
    """单个 job 的 write-ahead journal,存储 ``<journal_dir>/<job_id>.jsonl``。

    JSONL 行 schema(追加式,首行 start 携身份)::

        {"op": "start", "kind": .., "src": .., "dst": .., "game_root": ..,
         "started_at": ..}
        {"op": "intent", "rel": .., "detail": {..}}
        {"op": "completion", "rel": ..}
        {"op": "finish"}

    身份字段(批次I-W3 T3):src/dst/game_root 齐备的 journal 可被实例锁探针
    (:func:`_journal_owner_alive`)判活(scan_interrupted 与 dismiss 端点共用);
    缺省空串=无身份(旧调用兼容,中断判定退回旧判据)。

    重开语义:构造时 ``.jsonl`` 已存在则逐行折叠恢复内态(单行损坏跳过并
    warning,不毁整个 journal);盘上仅有旧 ``.json`` 时只读恢复;文件整体
    不可读抛 ``JournalError``(由调用方决定跳过/提示)。

    写失败留痕:任一次追加失败置 ``write_failed = True``——Executor 把挂点的
    JournalError 吞进 ``journal_failed`` 标志、只返回部分结果,执行编排层
    (GUI job)无法从返回值区分「写失败停发」与「正常完成」,须读本标志呈现
    停发原因并阻止「已执行」回写。
    """

    def __init__(
        self,
        journal_dir: Path,
        job_id: str,
        kind: str,
        *,
        src: str = "",
        dst: str = "",
        game_root: str = "",
    ) -> None:
        """初始化 journal(新 job 追加首行 start;盘上已有则载入恢复)。

        Args:
            journal_dir: journal 目录(<game_root>/.mcmig/jobs)。
            job_id: job 唯一标识(文件名 ``<job_id>.jsonl``)。
            kind: job 类型("migrate"/"plan")。
            src: 源版本名(身份字段,供锁探针判活;空串=无身份)。
            dst: 目标版本名(身份字段)。
            game_root: 游戏根目录字符串(身份字段)。

        Raises:
            JournalError: 新 journal 的 start 行写入失败,或盘上已有 journal
                但整体读取失败。
        """
        self.path = journal_dir / f"{job_id}.jsonl"
        self._legacy_path = journal_dir / f"{job_id}.json"
        self.kind = kind
        self.src = src
        self.dst = dst
        self.game_root = game_root
        self.finished = False
        self.write_failed = False
        self.started_at = datetime.now(timezone.utc).isoformat()
        # 旧格式标记:盘上是旧版全量 .json(无 .jsonl)→ 只读恢复,不迁移写
        self._legacy = False
        self._entries: dict[str, dict] = {}
        if self.path.exists():
            self._load()
        elif self._legacy_path.exists():
            self._legacy = True
            self._load()
        else:
            # 新 journal:首行 start 即时落盘(身份随行携带,供后续锁探针判活)
            self._append(
                {
                    "op": "start",
                    "kind": self.kind,
                    "src": self.src,
                    "dst": self.dst,
                    "game_root": self.game_root,
                    "started_at": self.started_at,
                }
            )

    def _load(self) -> None:
        """从盘上恢复内态(新格式 JSONL 逐行折叠;旧格式走全量 JSON 逻辑)。

        Raises:
            JournalError: 文件整体不可读(OSError)/ 旧格式顶层非对象。
        """
        if self._legacy:
            self._load_legacy()
        else:
            self._load_jsonl()

    def _load_jsonl(self) -> None:
        """新格式恢复:逐行折叠 start/intent/completion/finish → 内态。

        按**字节**切行、逐行解码(0.12.0 复审#3):write-ahead 的意义恰在
        崩溃后呈现恢复证据,末行截断在多字节字符内部(中文路径/detail 是
        常态,ensure_ascii=False)时,整文件 ``read_text`` 会因末行解码失败
        丢弃**整份档案**——改为逐行解码,完整行全部保留,损坏行(解码失败/
        非法 JSON/非对象)只跳过并 warning;无意图先行的孤儿 completion 忽略。
        """
        try:
            data = self.path.read_bytes()
        except OSError as e:
            # 盘面不可读(权限/占用)才是整档不可恢复;解码失败已不在此层
            raise JournalError(f"journal 读取失败: {self.path}({e})") from e
        for lineno, raw in enumerate(data.split(b"\n"), start=1):
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                log.warning("journal 行解码失败已跳过 %s:%d", self.path, lineno)
                continue
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except ValueError:
                log.warning("journal 行损坏已跳过 %s:%d", self.path, lineno)
                continue
            if not isinstance(row, dict):
                log.warning("journal 行非对象已跳过 %s:%d", self.path, lineno)
                continue
            op = row.get("op")
            if op == "start":
                self.kind = str(row.get("kind") or self.kind)
                self.src = str(row.get("src") or self.src)
                self.dst = str(row.get("dst") or self.dst)
                self.game_root = str(row.get("game_root") or self.game_root)
            elif op == "intent":
                detail = row.get("detail")
                self._entries[str(row.get("rel", ""))] = {
                    "detail": detail if isinstance(detail, dict) else {},
                    "completed": False,
                }
            elif op == "completion":
                entry = self._entries.get(str(row.get("rel", "")))
                if entry is not None:  # 孤儿 completion(无先行意图)忽略
                    entry["completed"] = True
            elif op == "finish":
                self.finished = True

    def _load_legacy(self) -> None:
        """旧格式(全量重写 JSON)只读恢复;损坏抛 JournalError,绝不静默清零。"""
        try:
            data = json.loads(self._legacy_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise JournalError(f"journal 读取失败: {self._legacy_path}({e})") from e
        if not isinstance(data, dict):
            raise JournalError(f"journal 顶层非对象: {self._legacy_path}")
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

    def _append(self, line: dict) -> None:
        """追加一行 JSONL(``json.dumps`` + 换行,写后 flush 立即落盘语义)。

        Args:
            line: 行对象(op=intent/completion/finish/start)。

        Raises:
            JournalError: 追加失败(OSError 族:磁盘满/无权限等);执行侧应
                停止分发(留痕 ``write_failed`` 供编排层区分停发原因)。
        """
        try:
            # 父目录惰性创建(与旧版 write_json_atomic 同语义:首个 job 自建 jobs/)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
                f.flush()
        except OSError as e:
            # 留痕:Executor 吞掉挂点的 JournalError 只返回部分结果,
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
        self._append({"op": "intent", "rel": rel, "detail": detail})

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
        # 内态无条目也照常 append(如 SKIP 后补记的孤儿):折叠时按「无先行
        # 意图」忽略,不撤销落盘——追加式存储以盘上行为准
        self._append({"op": "completion", "rel": rel})

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
        self._append({"op": "finish"})


def _journal_owner_alive(journal: JobJournal) -> bool:
    """实例锁探针:journal 身份对应的实例对是否仍被活进程持有(单点判定)。

    scan_interrupted 与 dismiss 端点共用同一判定(评审 P2-8):有身份
    (src/dst/game_root 齐备)时以 ``instance_locks`` 短超时探测——获取失败
    (InstanceLockError)= 有活进程(本 app 或跨进程 CLI)持锁 → True;获取
    成功(瞬间拿到又释放,不影响他方)= 前任已死 → False。无身份(旧格式/
    构造未带)→ 恒 False(不探针,退回旧判据)。

    Args:
        journal: 待判定的 journal(身份字段取自 start 行/构造入参)。

    Returns:
        True=有活进程持有该实例对(非中断残留);False=无身份或前任已死。
    """
    if not (journal.src and journal.dst and journal.game_root):
        return False
    try:
        with instance_locks(
            Path(journal.game_root), journal.src, journal.dst,
            timeout=_PROBE_TIMEOUT_SECONDS,
        ):
            return False
    except InstanceLockError:
        return True


def scan_interrupted(jobs_dir: Path) -> list[dict[str, object]]:
    """扫描 jobs 目录:未收尾 job → 待核对清单;锁探针判活;finished 清扫。

    判定(批次I-W3 T3,行为变更白名单④):未收尾(finish 未标记)即报;
    带身份(src/dst/game_root)的 journal 以实例锁探针判活——锁可获取=前任
    已死→报;锁被持(本 app 或跨进程 CLI 的活任务)→跳过。旧格式无身份→
    维持旧判据(有 unfinished 条目才报,保守不扩大)。finished=True 的
    journal 文件在扫描时删除(清扫,#9)。

    单个 journal 损坏只跳过并记 warning(不因一个坏文件阻断启动扫描);
    目录不存在/为空返回空清单。

    Args:
        jobs_dir: journal 目录(<game_root>/.mcmig/jobs)。

    Returns:
        ``[{"job_id", "kind", "src", "dst", "game_root", "entries": [{"rel",
        "detail"}...], "unknown_progress": bool}]``——entries 为空且仍报时
        ``unknown_progress=True``(未收尾且无待核对=整体进度未知,P2-8),
        按文件名升序(稳定)。
    """
    if not jobs_dir.is_dir():
        return []
    items: list[dict[str, object]] = []
    for p in sorted([*jobs_dir.glob("*.jsonl"), *jobs_dir.glob("*.json")]):
        try:
            journal = JobJournal(jobs_dir, p.stem, "")
        except JournalError as e:
            log.warning("journal 损坏/不可读,已跳过 %s: %s", p, e)
            continue
        if journal.finished:
            # 清扫(0.12.0 复审#4):删除失败(只读位/句柄占用)只警告并保留
            # 文件——本函数被应用工厂同步调用,清扫失败不得阻断 GUI 启动;
            # 残留文件下次扫描再试,不影响中断清单语义(已收尾本就不报)
            try:
                p.unlink(missing_ok=True)      # 清扫:收尾文件已尽其用(#9)
            except OSError as e:
                log.warning("已收尾 journal 清扫失败,保留文件 %s: %s", p, e)
            continue
        entries = journal.unfinished()
        if _journal_owner_alive(journal):
            continue                    # 有活进程持锁(GUI 或跨进程 CLI)→ 非中断
        if not entries and not (journal.src and journal.dst and journal.game_root):
            continue                    # 旧格式无身份且无待核对:维持旧判据,不扩大
        items.append({"job_id": p.stem, "kind": journal.kind,
                      "src": journal.src, "dst": journal.dst,
                      "game_root": journal.game_root, "entries": entries,
                      "unknown_progress": not entries})
    return items
