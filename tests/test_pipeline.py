"""pipeline 模块测试:编排函数直调(不经 CLI)。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from migration.pipeline import build_plan, execute_migration, scan_version
from migration.plan import ActionRecord, Behavior, MigrationPlan, Origin


def _mk_version(root: Path, name: str, options: str = "fps:60\n") -> Path:
    """在 root/versions/ 下建一个仅含 options.txt 的最小版本文件夹。"""
    d = root / "versions" / name
    d.mkdir(parents=True)
    (d / "options.txt").write_text(options, encoding="utf-8")
    return d


def _mk_action(path: str, behavior: Behavior) -> ActionRecord:
    """构造测试用 ActionRecord(COPY→must_migrate,ASK/SKIP→needs_review)。"""
    origin = Origin.MUST_MIGRATE if behavior == Behavior.COPY else Origin.NEEDS_REVIEW
    return ActionRecord(
        path=path,
        behavior=behavior,
        origin=origin,
        src_size=1,
        dst_size=None,
        md5_match=None,
        confidence="high",
        reason="test",
        backup_target=None,
    )


def _mk_plan(actions: list[ActionRecord]) -> MigrationPlan:
    """构造最小 MigrationPlan(execute_migration 直测用)。"""
    return MigrationPlan(
        src="s",
        dst="d",
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        actions=actions,
    )


def test_scan_version_writes_snapshot(tmp_path):
    game = tmp_path / "game"
    _mk_version(game, "src", "fps:120\n")
    snaps = tmp_path / "snapshots"
    snap = scan_version(game, "src", snaps)
    assert (snaps / "src.snapshot.json").exists()
    assert any(f.path == "options.txt" for f in snap.files)


def test_build_plan_returns_plan_and_saves(tmp_path):
    game = tmp_path / "game"
    _mk_version(game, "src", "fps:120\n")
    _mk_version(game, "dst")
    snaps = tmp_path / "snapshots"
    scan_version(game, "src", snaps)
    scan_version(game, "dst", snaps)
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir()
    plan, warns = build_plan(
        tmp_path, game, "src", "dst",
        mcmig_dir=tmp_path, plans_dir=plans_dir,
    )
    assert plan.summary()["must_migrate"] >= 1
    assert (plans_dir / "src__dst.plan.json").exists()
    # dst 无版本 json(NeoForge 版本未知)→ 不产生兼容警告
    assert warns == []


def test_build_plan_missing_src_snapshot_raises(tmp_path):
    """src 快照缺失 → FileNotFoundError(mcmig_dir/snapshots 下找不到)。"""
    game = tmp_path / "game"
    _mk_version(game, "src", "fps:120\n")
    _mk_version(game, "dst")
    import pytest

    with pytest.raises(FileNotFoundError):
        build_plan(
            tmp_path, game, "src", "dst",
            mcmig_dir=tmp_path, plans_dir=tmp_path / "plans",
        )


def test_execute_migration_ask_yes_set_decides_ask(tmp_path):
    """ask_yes 集合语义:命中的 ASK 迁移;未传 progress_cb 时 no-op 兜底不报错。"""
    src_root = tmp_path / "s"
    dst_root = tmp_path / "d"
    src_root.mkdir()
    dst_root.mkdir()
    (src_root / "a.txt").write_text("A", encoding="utf-8")
    (src_root / "q.txt").write_text("Q", encoding="utf-8")
    plan = _mk_plan([_mk_action("a.txt", Behavior.COPY), _mk_action("q.txt", Behavior.ASK)])
    results = execute_migration(plan, src_root, dst_root, ask_yes={"q.txt"})
    by_path = {r.path: r.status for r in results}
    assert by_path["a.txt"] == "copied"
    assert by_path["q.txt"] == "copied"  # ASK 命中 ask_yes → 迁移
    assert (dst_root / "a.txt").read_text(encoding="utf-8") == "A"
    assert (dst_root / "q.txt").read_text(encoding="utf-8") == "Q"


def test_execute_migration_dry_run_zero_write_and_progress_cb(tmp_path):
    """dry_run 零写盘(COPY/ASK 推演、未命中 ASK=asked_no、SKIP=skipped);progress_cb 逐结果回调。"""
    src_root = tmp_path / "s"
    dst_root = tmp_path / "d"
    src_root.mkdir()
    dst_root.mkdir()
    (src_root / "a.txt").write_text("A", encoding="utf-8")
    (src_root / "q.txt").write_text("Q", encoding="utf-8")
    plan = _mk_plan(
        [
            _mk_action("a.txt", Behavior.COPY),
            _mk_action("q.txt", Behavior.ASK),
            _mk_action("z.txt", Behavior.SKIP),
        ]
    )
    seen: list[str] = []
    results = execute_migration(
        plan, src_root, dst_root, ask_yes=set(),
        dry_run=True, progress_cb=lambda r: seen.append(r.path),
    )
    by_path = {r.path: r.status for r in results}
    assert by_path["a.txt"] == "copied"  # dry-run 推演
    assert by_path["q.txt"] == "asked_no"  # ASK 未命中 ask_yes
    assert by_path["z.txt"] == "skipped"
    assert not (dst_root / "a.txt").exists()  # 零写盘
    assert not (dst_root / "q.txt").exists()
    assert seen == ["a.txt", "q.txt", "z.txt"]  # 按 plan.actions 顺序逐结果回调
