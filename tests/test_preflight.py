"""preflight 共享执行预检:四道防护×决策矩阵(spec §3.2 阻断分类)。

同型用例补全(任务简报 Step 1 末注):
- snapshot_stale 族:快照文件 mtime > plan 文件 mtime 构造(os.utime 显式设定,
  不依赖真实时间流逝);
- game_maybe_running 族:dst 写 usercache.json 并以真独占句柄持有
  (Windows CreateFileW share=0,见 conftest.hold_exclusive)模拟占用。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from tests.conftest import hold_exclusive

from migration.plan import ActionRecord, Behavior, MigrationPlan, Origin
from migration.preflight import (
    ExecutionDecisions,
    PreflightBlocker,
    preflight_execute,
    probe_maybe_running,
)


def _plan(executed: bool = False) -> MigrationPlan:
    from datetime import datetime, timezone
    p = MigrationPlan(src="src", dst="dst",
                      generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      actions=[])
    if executed:
        p.mark_executed({})
    return p


def _save_plan_file(game_root: Path, *, mtime: float) -> Path:
    """落盘一份带单条 COPY 动作的 plan 文件并显式设定 mtime(stale 比对基准)。

    生产 plan 恒有动作;带真实 ActionRecord 亦覆盖「plan 文件只是 mtime 载体、
    预检不重新加载它」的语义。
    """
    plan = MigrationPlan(
        src="src", dst="dst",
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        actions=[ActionRecord(path="options.txt", behavior=Behavior.COPY,
                              origin=Origin.MUST_MIGRATE, src_size=1, dst_size=None,
                              md5_match=None, confidence="high", reason="test",
                              backup_target=None)],
    )
    p_path = game_root / ".mcmig" / "plans" / "src__dst.plan.json"
    plan.save(p_path)
    os.utime(p_path, (mtime, mtime))
    return p_path


def _touch_snapshot(game_root: Path, version: str, *, mtime: float) -> Path:
    """落一个快照文件并显式设定 mtime(stale 检查只比 mtime,不加载内容)。"""
    snap = game_root / ".mcmig" / "snapshots" / f"{version}.snapshot.json"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text("{}", encoding="utf-8")
    os.utime(snap, (mtime, mtime))
    return snap


def test_executed_plan_blocks_without_decision(tmp_path):
    (tmp_path / "versions" / "src").mkdir(parents=True)
    (tmp_path / "versions" / "dst").mkdir(parents=True)
    blockers, _ = preflight_execute(_plan(executed=True), tmp_path, "src", "dst")
    assert [b.code for b in blockers] == ["plan_executed"]


def test_executed_plan_rerun_decision_downgrades_to_warning(tmp_path):
    for n in ("src", "dst"):
        (tmp_path / "versions" / n).mkdir(parents=True)
    blockers, warnings = preflight_execute(
        _plan(executed=True), tmp_path, "src", "dst",
        decisions=ExecutionDecisions(rerun_executed=True))
    assert not blockers and [w.code for w in warnings] == ["plan_executed"]


def test_version_dir_missing_never_forceable(tmp_path):
    (tmp_path / "versions" / "src").mkdir(parents=True)   # dst 缺失
    for dec in (None, ExecutionDecisions(rerun_executed=True, accept_stale=True, accept_maybe_running=True)):
        blockers, _ = preflight_execute(_plan(), tmp_path, "src", "dst", decisions=dec)
        assert "version_dir_missing" in [b.code for b in blockers]   # 无降级通道(spec §3.2)


# --- snapshot_stale 族(快照 mtime > plan 文件 mtime 构造) ---


def test_snapshot_newer_than_plan_blocks_without_decision(tmp_path):
    """快照 mtime > plan 文件 mtime → snapshot_stale 阻断(下沉前 cli 语义不变)。"""
    for n in ("src", "dst"):
        (tmp_path / "versions" / n).mkdir(parents=True)
    _save_plan_file(tmp_path, mtime=1_000_000)
    _touch_snapshot(tmp_path, "src", mtime=1_000_010)
    blockers, warnings = preflight_execute(_plan(), tmp_path, "src", "dst")
    assert [b.code for b in blockers] == ["snapshot_stale"]
    assert not warnings
    assert all(isinstance(b, PreflightBlocker) for b in blockers)  # 契约:阻断项类型


def test_snapshot_stale_accept_decision_downgrades_to_warning(tmp_path):
    """accept_stale=True → 快照过期降级为警告,不再阻断。"""
    for n in ("src", "dst"):
        (tmp_path / "versions" / n).mkdir(parents=True)
    _save_plan_file(tmp_path, mtime=1_000_000)
    _touch_snapshot(tmp_path, "dst", mtime=1_000_010)
    blockers, warnings = preflight_execute(
        _plan(), tmp_path, "src", "dst",
        decisions=ExecutionDecisions(accept_stale=True))
    assert not blockers and [w.code for w in warnings] == ["snapshot_stale"]


def test_snapshot_not_newer_than_plan_passes(tmp_path):
    """快照不比计划新(旧于 plan)→ 不发 stale(存在才比、更新才断)。"""
    for n in ("src", "dst"):
        (tmp_path / "versions" / n).mkdir(parents=True)
    _save_plan_file(tmp_path, mtime=1_000_010)
    _touch_snapshot(tmp_path, "src", mtime=1_000_000)
    _touch_snapshot(tmp_path, "dst", mtime=1_000_000)
    blockers, warnings = preflight_execute(_plan(), tmp_path, "src", "dst")
    assert not blockers and not warnings


def test_stale_skipped_when_plan_file_not_on_disk(tmp_path):
    """plan 文件不在盘上(GUI/夹具传入内存对象)→ 无从比对,不发 stale。"""
    for n in ("src", "dst"):
        (tmp_path / "versions" / n).mkdir(parents=True)
    _touch_snapshot(tmp_path, "src", mtime=1_000_010)  # 有快照但无 plan 文件
    blockers, warnings = preflight_execute(_plan(), tmp_path, "src", "dst")
    assert not blockers and not warnings


# --- game_maybe_running 族(dst 写 usercache.json + 真独占句柄模拟占用) ---


def test_maybe_running_blocks_without_decision(tmp_path):
    """dst 的 usercache.json 被独占句柄持有 → game_maybe_running 阻断。"""
    for n in ("src", "dst"):
        (tmp_path / "versions" / n).mkdir(parents=True)
    dst_root = tmp_path / "versions" / "dst"
    (dst_root / "usercache.json").write_text("[]", encoding="utf-8")
    with hold_exclusive(dst_root / "usercache.json"):
        assert probe_maybe_running(dst_root) is True  # 探测本体:独占下 open(r+b) 报错
        blockers, _ = preflight_execute(_plan(), tmp_path, "src", "dst")
    assert [b.code for b in blockers] == ["game_maybe_running"]


def test_maybe_running_accept_decision_downgrades_to_warning(tmp_path):
    """accept_maybe_running=True → 疑似占用降级为警告(独立决策,不与其他项捆绑)。"""
    for n in ("src", "dst"):
        (tmp_path / "versions" / n).mkdir(parents=True)
    dst_root = tmp_path / "versions" / "dst"
    (dst_root / "usercache.json").write_text("[]", encoding="utf-8")
    with hold_exclusive(dst_root / "usercache.json"):
        blockers, warnings = preflight_execute(
            _plan(), tmp_path, "src", "dst",
            decisions=ExecutionDecisions(accept_maybe_running=True))
    assert not blockers and [w.code for w in warnings] == ["game_maybe_running"]


def test_probe_maybe_running_false_when_files_free_or_absent(tmp_path):
    """文件不存在或可自由打开 → 不疑似占用(探测假阴性边界,docstring 已明示)。"""
    dst_root = tmp_path / "versions" / "dst"
    dst_root.mkdir(parents=True)
    assert probe_maybe_running(dst_root) is False  # 两文件均不存在
    (dst_root / "usercache.json").write_text("[]", encoding="utf-8")
    (dst_root / "options.txt").write_text("fps:1\n", encoding="utf-8")
    assert probe_maybe_running(dst_root) is False  # 存在但未被锁


def test_multiple_blockers_reported_in_fixed_order(tmp_path):
    """多项同时命中:按 目录缺失→已执行→过期→疑似占用 固定序回报(CLI 取首条)。"""
    for n in ("src", "dst"):
        (tmp_path / "versions" / n).mkdir(parents=True)
    dst_root = tmp_path / "versions" / "dst"
    (dst_root / "usercache.json").write_text("[]", encoding="utf-8")
    _save_plan_file(tmp_path, mtime=1_000_000)
    _touch_snapshot(tmp_path, "src", mtime=1_000_010)
    with hold_exclusive(dst_root / "usercache.json"):
        blockers, _ = preflight_execute(_plan(executed=True), tmp_path, "src", "dst")
    assert [b.code for b in blockers] == [
        "plan_executed", "snapshot_stale", "game_maybe_running",
    ]


def test_blocker_message_wording_matches_cli_contract():
    """文案契约:前三条与 CLI 既有输出逐字一致;占用为「疑似」新措辞(白名单④)。"""
    from migration.preflight import (
        MSG_MAYBE_RUNNING,
        MSG_PLAN_EXECUTED,
        MSG_SNAPSHOT_STALE,
        MSG_VERSION_DIR_MISSING,
    )

    assert MSG_PLAN_EXECUTED == ("该计划已执行(时间 {executed_at})。"
                                 "重跑请加 --force(可重入:已完成文件会自动跳过)。")
    assert MSG_SNAPSHOT_STALE == "快照比计划新,计划可能过期。请重跑 plan,或 --force 强制执行。"
    assert MSG_VERSION_DIR_MISSING == "源/目标版本文件夹不存在"
    assert MSG_MAYBE_RUNNING == ("目标版本文件疑似被占用,游戏可能仍在运行;"
                                 "继续可能损坏存档。请先退出源与目标实例。")


def test_pipeline_reexports_preflight_symbols():
    """spec 命名 pipeline.preflight_execute 成立(pipeline 头部重导出契约,T3/T5 消费)。"""
    from migration import pipeline

    assert pipeline.preflight_execute is preflight_execute
    assert pipeline.probe_maybe_running is probe_maybe_running
    assert pipeline.ExecutionDecisions is ExecutionDecisions
    assert pipeline.PreflightBlocker is PreflightBlocker
