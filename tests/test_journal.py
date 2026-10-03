"""journal write-ahead 三段序与崩溃窗口(spec §4.3,批次I-T6)。

三段序:①record_intent(意图持久化,write-ahead)→ ②文件操作(fsops 落盘)
→ ③record_completion(完成持久化)。崩溃窗口语义:②已落盘而 ③ 未写即崩溃,
重启后该条目读作「待核对」(unfinished),而非「未执行」——迁移状态不可臆断。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from migration.executor import Executor
from migration.journal import JobJournal, JournalError, scan_interrupted

# 仓库根(持锁子进程 ``python -c`` 须显式注入 sys.path 才能 import migration,
# 同 tests/test_instlock.py 的既有约定;测试可能被 chdir 影响,不依赖 cwd)
_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_crash_between_copy_and_completion_marks_unverified(tmp_path: Path):
    """替换成功→登记前崩溃:重启后「状态待核对」而非「未执行」(Review Focus 6)。"""
    j = JobJournal(tmp_path, "job1", "migrate")
    j.record_intent("options.txt", {"op": "copy", "backup": "_conflict_backup/options.txt"})
    # 模拟: 文件已落盘(②),完成记录(③)未写即崩溃——不做 record_completion
    j2 = JobJournal(tmp_path, "job1", "migrate")  # 重启重开(从盘上恢复内态)
    assert [e["rel"] for e in j2.unfinished()] == ["options.txt"]
    assert scan_interrupted(tmp_path)[0]["entries"][0]["rel"] == "options.txt"


def test_completion_clears_unfinished_and_finish_hides_from_scan(tmp_path: Path):
    """③完成登记后不再待核对;finish 收尾后整体退出中断清单(正常终态)。"""
    j = JobJournal(tmp_path, "job1", "migrate")
    j.record_intent("a.txt", {"op": "copy"})
    j.record_completion("a.txt")
    assert j.unfinished() == []
    assert scan_interrupted(tmp_path) == []
    # 再录一条未完成 → 出现在扫描;finish 后退出(收尾=正常终态,非中断)
    j.record_intent("b.txt", {"op": "copy"})
    assert scan_interrupted(tmp_path)[0]["job_id"] == "job1"
    j.finish()
    assert scan_interrupted(tmp_path) == []


def test_scan_interrupted_empty_dir_and_missing(tmp_path: Path):
    """目录不存在/为空 → 空清单(启动期扫描零噪声)。"""
    assert scan_interrupted(tmp_path / "nope") == []
    (tmp_path / "jobs").mkdir()
    assert scan_interrupted(tmp_path / "jobs") == []


def test_journal_write_failure_stops_dispatch(
    tmp_path: Path, monkeypatch, mini_plan
):
    """意图写失败→停止分发后续写入动作(spec §4.3,仅首个文件被复制)。

    注入点(批次I-W3 T3 机械更新):``JobJournal._append``——JSONL 追加原语,
    替代旧版全量重写锚点 write_json_atomic;start 行不计次(构造期写入),
    保持与旧注入等价的「首条意图成功、之后全部失败」语义。
    """
    calls = {"n": 0}
    real_append = JobJournal._append

    def _flaky(self, line: dict) -> None:
        if line.get("op") != "start":  # start 行不计次(构造期追加,非记录)
            calls["n"] += 1
        if calls["n"] > 1:  # 第一条意图成功,之后全部失败
            self.write_failed = True  # 模拟真实 _append 的 OSError 留痕语义
            raise JournalError("journal 写入失败: disk(注入)")
        real_append(self, line)

    monkeypatch.setattr(JobJournal, "_append", _flaky)
    j = JobJournal(tmp_path, "job2", "migrate")
    ex = Executor(mini_plan.plan, mini_plan.src, mini_plan.dst, lambda _a: True)
    results = ex.execute(
        before_action=lambda a: j.record_intent(a.path, {"op": "copy"})
    )
    # 首个动作完整走完①②(③未接);第二个动作的①写失败 → journal_failed 停发
    assert ex.journal_failed is True
    assert ex.cancelled is False
    assert len(results) == 1 and results[0].status == "copied"
    assert (mini_plan.dst / "f0.txt").exists()
    assert not (mini_plan.dst / "f1.txt").exists()
    assert not (mini_plan.dst / "f2.txt").exists()


def test_write_failed_flag_marks_journal(tmp_path: Path, monkeypatch):
    """写失败在 journal 对象上留痕(write_failed=True),供执行编排侧停发呈现判定。

    执行侧(Executor)把 before_action 的 JournalError 吞进 journal_failed 标志、
    只返回部分结果——编排层(GUI job)无法从返回值区分「写失败停发」与「正常
    完成」,故 journal 自身须留痕(修复 finding 1 的判定数据源)。

    注入点(批次I-W3 T3 机械更新):``JobJournal._append``——start 行正常落盘
    (构造不受影响),其余追加置 write_failed 并抛 JournalError(模拟真实
    _append 的 OSError 包装语义)。
    """
    real_append = JobJournal._append

    def _boom(self, line: dict) -> None:
        if line.get("op") == "start":
            real_append(self, line)
            return
        self.write_failed = True
        raise JournalError("journal 写入失败: disk(注入)")

    monkeypatch.setattr(JobJournal, "_append", _boom)
    j = JobJournal(tmp_path, "jw", "migrate")
    assert j.write_failed is False
    with pytest.raises(JournalError):
        j.record_intent("a.txt", {"op": "copy"})
    assert j.write_failed is True


# ---- 批次I-W3 T3:JSONL 增量存储 + 中断判定(锁探针活性)+ finished 清扫 ----


def test_jsonl_append_not_full_rewrite(tmp_path: Path):
    """#13:意图/完成各追加一行,既有行不被重写(文件头 start 行原样保留)。"""
    j = JobJournal(tmp_path, "job1", "migrate", src="s", dst="d", game_root="g")
    head = (tmp_path / "job1.jsonl").read_text(encoding="utf-8")
    j.record_intent("a.txt", {"op": "copy"})
    j.record_intent("b.txt", {"op": "copy"})
    body = (tmp_path / "job1.jsonl").read_text(encoding="utf-8")
    assert body.startswith(head) and body.count("\n") == 3     # start+2 intent,无重写


def test_legacy_json_journal_still_readable(tmp_path: Path):
    """旧 .json 存储(write_json_atomic 全量)只读恢复,重开照常判 unfinished。"""
    (tmp_path / "old.json").write_text(json.dumps({
        "kind": "migrate", "started_at": "t", "finished": False,
        "entries": {"options.txt": {"detail": {"op": "copy"}, "completed": False}},
    }, ensure_ascii=False), encoding="utf-8")
    j = JobJournal(tmp_path, "old", "migrate")
    assert [e["rel"] for e in j.unfinished()] == ["options.txt"]


def test_interrupted_all_completed_zero_unfinished_reported(tmp_path: Path):
    """P2-8:A 完成+B 意图未登记即崩溃 → journal 全完成未收尾 → 仍报整体中断
    (unknown_progress=True,entries 为空)。"""
    j = JobJournal(tmp_path, "job2", "migrate", src="s", dst="d", game_root=str(tmp_path))
    j.record_intent("a.txt", {"op": "copy"})
    j.record_completion("a.txt")
    items = scan_interrupted(tmp_path)
    assert items and items[0]["job_id"] == "job2" and items[0]["unknown_progress"] is True


def test_scan_interrupted_reports_dead_but_skips_lock_holder(tmp_path: Path):
    """跨进程活性(二轮跨进程项):子进程持锁的未收尾 journal=活任务不报;
    无锁占用的未收尾 journal=前任已死,报。两 journal 用不同实例对,探针互不串扰。"""
    import subprocess
    import sys
    import textwrap

    j = JobJournal(tmp_path, "dead", "migrate", src="A", dst="B",
                   game_root=str(tmp_path))
    j.record_intent("a.txt", {"op": "copy"})
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(_REPO_ROOT)!r})
            import time
            from migration.instlock import instance_locks
            with instance_locks({str(tmp_path)!r}, "C", "D"):
                print("HELD", flush=True); time.sleep(30)
        """)],
        stdout=subprocess.PIPE)                          # 评审建议 B:无 PIPE 则 readline 失败
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == b"HELD"
        j2 = JobJournal(tmp_path, "alive", "migrate", src="C", dst="D",
                        game_root=str(tmp_path))
        j2.record_intent("b.txt", {"op": "copy"})
        ids = [i["job_id"] for i in scan_interrupted(tmp_path)]
        assert "dead" in ids and "alive" not in ids
    finally:
        holder.kill()
        holder.wait()


def test_finished_journals_pruned_on_scan(tmp_path: Path):
    """#9:finished journal 扫描时清理(已尽其用,不再累积)。"""
    jf = JobJournal(tmp_path, "jdone", "migrate")
    jf.record_intent("a", {})
    jf.finish()
    scan_interrupted(tmp_path)
    assert not (tmp_path / "jdone.jsonl").exists()          # 已清扫


def test_torn_multibyte_last_line_preserves_valid_prefix(tmp_path: Path, caplog):
    """评审(0.12.0 复审#3):末行被断电/杀软截断在多字节字符内部(中文
    路径/detail 是常态,ensure_ascii=False)——write-ahead 的意义恰在崩溃后
    呈现恢复证据,不得因末行损坏丢弃**整份档案**;按字节切行逐行解码,
    完整行(start 与已落盘意图)全部保留,损坏行跳过+warning。"""
    start = json.dumps({"op": "start", "kind": "migrate", "src": "s", "dst": "d",
                        "game_root": str(tmp_path), "started_at": "t"},
                       ensure_ascii=False)
    ok_intent = json.dumps({"op": "intent", "rel": "已完成的意图.txt",
                            "detail": {"op": "copy"}}, ensure_ascii=False)
    line = json.dumps({"op": "intent", "rel": "配置/龙.txt", "detail": {"op": "copy"}},
                      ensure_ascii=False)
    cut = len(line[: line.index("龙")].encode("utf-8")) + 2   # 切在「龙」第 2/3 字节
    (tmp_path / "torn.jsonl").write_bytes(
        (start + "\n" + ok_intent + "\n").encode("utf-8")
        + line.encode("utf-8")[:cut])
    with caplog.at_level("WARNING", logger="migration.journal"):
        items = scan_interrupted(tmp_path)    # 修复前:整档解码失败→跳过→[]
    # 前缀保留:start 身份可读 + 完整意图进待核对清单(修复前:items == [])
    assert len(items) == 1 and items[0]["job_id"] == "torn"
    assert items[0]["src"] == "s" and items[0]["dst"] == "d"
    assert [e["rel"] for e in items[0]["entries"]] == ["已完成的意图.txt"]
    assert items[0]["unknown_progress"] is False
    assert "torn" in caplog.text              # 损坏行留 warning 可观测


def test_sweep_unlink_failure_keeps_file_and_scan_alive(tmp_path: Path, caplog):
    """评审(0.12.0 复审#4):finished journal 清扫失败(Windows 只读/句柄占用)
    只警告并保留文件,不得阻断扫描——应用工厂(create_app)同步调用本扫描,
    修复前 PermissionError 会把 GUI 启动直接打穿。"""
    import os

    j = JobJournal(tmp_path, "done1", "migrate", src="s", dst="d",
                   game_root=str(tmp_path))
    j.record_intent("a.txt", {"op": "copy"})
    j.finish()
    f = tmp_path / "done1.jsonl"
    assert f.exists()
    os.chmod(f, 0o444)          # Windows:只读位 → unlink PermissionError
    try:
        with caplog.at_level("WARNING", logger="migration.journal"):
            items = scan_interrupted(tmp_path)   # 修复前:PermissionError 逃逸
        assert items == []                        # 无中断:清单照常为空
        assert f.exists()                         # 文件保留,不因失败半途而废
        assert "done1" in caplog.text             # 留 warning 可观测
    finally:
        os.chmod(f, 0o666)
