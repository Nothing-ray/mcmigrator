"""pipeline 模块测试:编排函数直调(不经 CLI)。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from migration.pipeline import build_plan, execute_migration, scan_version
from migration.plan import ActionRecord, Behavior, MigrationPlan, Origin
from migration.snapshot import Snapshot


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


def test_scan_version_on_error_collects_unreadable_paths(tmp_path, monkeypatch):
    """回归(v0 spec §7 unreadable 契约):不可读文件经 on_error 上报相对路径,快照照常返回落盘。

    用注入 ScanError 的桩 Scanner 稳定触发(真实「文件占用」在 CI/Windows 上不稳定)。
    """
    from migration import pipeline
    from migration.scanner import ScanError, Scanner

    game = tmp_path / "game"
    _mk_version(game, "src", "fps:120\n")

    class _StubScanner:
        """包装真 Scanner,额外注入 1 条模拟扫描错误(文件被占用不可读)。"""

        def __init__(self, version_dir: Path, version_name: str, *, strict: bool = False) -> None:
            self._inner = Scanner(version_dir, version_name, strict=strict)

        def build_snapshot(self, game_root: str) -> tuple[Snapshot, list[ScanError]]:
            snap, errors = self._inner.build_snapshot(game_root)
            errors.append(ScanError(path="saves/locked.dat", reason="模拟占用"))
            return snap, errors

    monkeypatch.setattr(pipeline, "Scanner", _StubScanner)
    seen: list[str] = []
    snap = pipeline.scan_version(game, "src", tmp_path / "snapshots", on_error=seen.append)
    assert seen == ["saves/locked.dat"]  # 每条扫描错误回调一次,传相对路径
    assert snap.file_count >= 1
    assert (tmp_path / "snapshots" / "src.snapshot.json").exists()
    # 不传 on_error(None 默认)不崩,由 test_scan_version_writes_snapshot 覆盖


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


def test_execute_migration_cleans_tmp_and_checks_disk(tmp_path, monkeypatch):
    """预检:清理残留 tmp;磁盘不足抛 DiskSpaceError 且零写盘。

    磁盘预检用注入法:monkeypatch 模块级 check_disk_space(实现须调用模块级名,
    不可 from-import 后改本地别名调用,否则 monkeypatch 拦不到)。
    """
    import pytest

    from migration import pipeline as pl
    from migration.fsops import DiskSpaceError

    game = tmp_path / "game"
    src = game / "versions" / "src"
    dst = game / "versions" / "dst"
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    (src / "options.txt").write_text("fps:1\n", encoding="utf-8")
    (dst / "stale.mcmig-tmp").write_text("half", encoding="utf-8")

    plan = _mk_plan([_mk_action("options.txt", Behavior.COPY)])
    execute_migration(plan, src, dst, ask_yes=set())
    assert not (dst / "stale.mcmig-tmp").exists()  # tmp 清理
    assert (dst / "options.txt").read_text(encoding="utf-8") == "fps:1\n"

    def no_disk(root, needed):
        raise DiskSpaceError(str(root), f"缺 {needed // (1024 * 1024)} MB")

    monkeypatch.setattr(pl, "check_disk_space", no_disk)
    (dst / "options.txt").unlink()  # 移除已迁移产物,验证预检失败零写盘
    plan2 = _mk_plan([_mk_action("options.txt", Behavior.COPY)])
    with pytest.raises(DiskSpaceError):
        execute_migration(plan2, src, dst, ask_yes=set())
    assert not (dst / "options.txt").exists()  # 执行未发生(预检失败零写盘)


def test_execute_migration_dry_run_keeps_stale_tmp(tmp_path):
    """携带项 b:dry-run 零写盘契约 → 既有残留 tmp 原样保留(clean_stale_tmp 随 dry-run 跳过)。

    与 test_execute_migration_cleans_tmp_and_checks_disk 互补:非 dry-run 清理,
    dry-run 不动(任何写盘副作用都违反零写盘承诺)。
    """
    src_root = tmp_path / "s"
    dst_root = tmp_path / "d"
    src_root.mkdir()
    dst_root.mkdir()
    (src_root / "a.txt").write_text("A", encoding="utf-8")
    stale = dst_root / "stale.mcmig-tmp"
    stale.write_text("half", encoding="utf-8")
    plan = _mk_plan([_mk_action("a.txt", Behavior.COPY)])
    execute_migration(plan, src_root, dst_root, ask_yes=set(), dry_run=True)
    assert stale.exists()  # dry-run 不清理
    assert not (dst_root / "a.txt").exists()  # 零写盘


def test_list_versions_and_active_version(tmp_path):
    """M3 收口:版本枚举与 PCL.ini 活跃版本读取成为 pipeline 公共函数(cli/gui 共用)。

    行为零变化(自 server._list_versions/_read_active_version 原样提取):
    versions/ 缺失 → 空列表;升序;PCL.ini 缺失 → None;带 BOM 亦可读。
    """
    from migration.pipeline import list_versions, read_active_version

    game = tmp_path / "game"
    assert list_versions(game) == []  # versions/ 不存在
    (game / "versions" / "b").mkdir(parents=True)
    (game / "versions" / "a").mkdir()
    assert list_versions(game) == ["a", "b"]

    assert read_active_version(game) is None  # 无 PCL.ini
    (game / "PCL.ini").write_text("[general]\nVersion:a\n", encoding="utf-8")
    assert read_active_version(game) == "a"
    # BOM(utf-8-sig)与 GBK 系编码按序尝试,均可读回
    (game / "PCL.ini").write_bytes("﻿Version:a\n".encode("utf-8"))
    assert read_active_version(game) == "a"
    (game / "PCL.ini").write_bytes("Version:a\n".encode("gb18030"))
    assert read_active_version(game) == "a"


def test_execute_migration_progress_cb_is_realtime(tmp_path):
    """携带项 A:progress_cb 注入 Executor 实时逐文件回调,而非执行后批量回放。

    判据:收到第一个文件的回调时,第二个文件应尚未写盘(批量回放则必已写盘)。
    """
    src_root = tmp_path / "s"
    dst_root = tmp_path / "d"
    src_root.mkdir()
    dst_root.mkdir()
    (src_root / "a.txt").write_text("A", encoding="utf-8")
    (src_root / "b.txt").write_text("B", encoding="utf-8")
    plan = _mk_plan([_mk_action("a.txt", Behavior.COPY), _mk_action("b.txt", Behavior.COPY)])
    realtime: list[bool] = []

    def cb(r) -> None:
        """收到 a.txt 回调时记录 b.txt 是否仍未写盘。"""
        if r.path == "a.txt":
            realtime.append(not (dst_root / "b.txt").exists())

    execute_migration(plan, src_root, dst_root, ask_yes=set(), progress_cb=cb)
    assert realtime == [True]
    assert (dst_root / "b.txt").read_text(encoding="utf-8") == "B"  # 全程正常完成


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


# resolve_diff_context 测试(批次 B F2/F4/F12 基座;Snapshot 已在文件头导入)


def _snap(version: str, game_root: str) -> Snapshot:
    """构造最小快照对象(scanned_at/hash_mode/file_count 为必填,填占位值)。"""
    return Snapshot(version=version, game_root=game_root,
                    scanned_at="2026-09-13T00:00:00+00:00", hash_mode="tiered",
                    file_count=0, files=[])


def _make_game_root(tmp_path, version: str, modid: str, mod_version: str):
    """建一个含 1 个 mod jar 的最小版本目录,返回 (game_root, 版本目录)。"""
    from tests.conftest import write_mod_jar

    root = tmp_path / "root"
    vdir = root / "versions" / version
    vdir.mkdir(parents=True)
    write_mod_jar(vdir / "mods" / f"{modid}-{mod_version}.jar", modid, mod_version)
    return root, vdir


def test_resolve_context_success(tmp_path):
    from migration.pipeline import resolve_diff_context

    ra, _ = _make_game_root(tmp_path, "a", "waystones", "21.1.42")
    rb, _ = _make_game_root(tmp_path, "b", "waystones", "21.1.44")
    ctx = resolve_diff_context(_snap("a", str(ra)), _snap("b", str(rb)))
    assert ctx is not None
    assert "waystones" in ctx.src_mods and "waystones" in ctx.dst_mods
    assert ctx.src_mods.get("waystones").version == "21.1.42"


def test_resolve_context_missing_dir_returns_none(tmp_path):
    from migration.pipeline import resolve_diff_context

    ra, _ = _make_game_root(tmp_path, "a", "x", "1.0")
    # dst 指向不存在的版本目录(跨机复放/夹具场景)
    assert resolve_diff_context(_snap("a", str(ra)), _snap("ghost", str(ra))) is None


def test_resolve_context_empty_game_root_returns_none():
    from migration.pipeline import resolve_diff_context

    assert resolve_diff_context(_snap("a", ""), _snap("b", "C:\\nonexistent")) is None


def test_context_read_file_roundtrip_and_missing(tmp_path):
    from migration.pipeline import resolve_diff_context

    ra, va = _make_game_root(tmp_path, "a", "x", "1.0")
    (va / "server.properties").write_bytes(b"motd=hi\n")
    rb, _ = _make_game_root(tmp_path, "b", "x", "1.0")
    ctx = resolve_diff_context(_snap("a", str(ra)), _snap("b", str(rb)))
    assert ctx.read_file("server.properties", "src") == b"motd=hi\n"
    assert ctx.read_file("server.properties", "dst") is None  # 读取失败 → None
    assert ctx.read_file("server.properties", "unknown-side") is None


def test_resolve_context_empty_version_returns_none(tmp_path):
    """终审 T2②:version="" 时路径折叠成 versions/ 目录本身,不得误当版本目录。

    game_root 有效但 version 为空串 → resolve_diff_context 必须返回 None
    (与 game_root 为空的守卫同级),否则会把 versions/ 当版本目录去扫 mods。
    """
    from migration.pipeline import resolve_diff_context

    ra, _ = _make_game_root(tmp_path, "a", "x", "1.0")
    # src 侧 version=""(dst 侧完全有效,证明守卫在空串一侧生效)
    assert resolve_diff_context(_snap("", str(ra)), _snap("a", str(ra))) is None
