"""journal write-ahead 三段序与崩溃窗口(spec §4.3,批次I-T6)。

三段序:①record_intent(意图持久化,write-ahead)→ ②文件操作(fsops 落盘)
→ ③record_completion(完成持久化)。崩溃窗口语义:②已落盘而 ③ 未写即崩溃,
重启后该条目读作「待核对」(unfinished),而非「未执行」——迁移状态不可臆断。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from migration.executor import Executor
from migration.journal import JobJournal, scan_interrupted


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
    """意图写失败→停止分发后续写入动作(spec §4.3,仅首个文件被复制)。"""
    import migration.journal as jm

    real_write = jm.write_json_atomic  # fsops 实现无 __wrapped__,先留真实现引用
    calls = {"n": 0}

    def _flaky(path, payload):
        calls["n"] += 1
        if calls["n"] > 1:  # 第一条意图成功,之后全部失败
            raise OSError("disk")
        real_write(path, payload)

    monkeypatch.setattr(jm, "write_json_atomic", _flaky)
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
    """
    import migration.journal as jm

    def _boom(path, payload):
        raise OSError("disk")

    monkeypatch.setattr(jm, "write_json_atomic", _boom)
    j = JobJournal(tmp_path, "jw", "migrate")
    assert j.write_failed is False
    with pytest.raises(jm.JournalError):
        j.record_intent("a.txt", {"op": "copy"})
    assert j.write_failed is True
