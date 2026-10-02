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
    plan, warns, _pairs = build_plan(
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


def test_find_snapshot_anchored_first_then_legacy(tmp_path: Path) -> None:
    """锚定目录优先;锚定缺失时回退旧布局;均无返回锚定路径(报错指向新位置)。"""
    from migration.pipeline import find_snapshot

    data = tmp_path / "game" / ".mcmig"
    legacy = tmp_path / "cwd" / ".mcmig"
    data_snap = data / "snapshots" / "v.snapshot.json"
    legacy_snap = legacy / "snapshots" / "v.snapshot.json"
    data_snap.parent.mkdir(parents=True)
    legacy_snap.parent.mkdir(parents=True)

    # 两侧都有 → 锚定优先
    data_snap.write_text("{}", encoding="utf-8")
    legacy_snap.write_text("{}", encoding="utf-8")
    p, is_legacy = find_snapshot(data, legacy, "v")
    assert (p, is_legacy) == (data_snap, False)
    # 仅旧布局 → 回退命中
    data_snap.unlink()
    p, is_legacy = find_snapshot(data, legacy, "v")
    assert (p, is_legacy) == (legacy_snap, True)
    # 均无 → 返回锚定路径(调用方报缺少快照)
    legacy_snap.unlink()
    p, is_legacy = find_snapshot(data, legacy, "v")
    assert (p, is_legacy) == (data_snap, False)
    # legacy_dir=None(同目录语义/GUI)→ 永不回退
    assert find_snapshot(data, None, "v") == (data_snap, False)


def test_build_plan_data_dir_reads_anchored_and_falls_back(tmp_path, caplog) -> None:
    """data_dir 与 mcmig_dir 分离:快照优先读 data_dir,缺失回退 mcmig_dir(旧布局)。"""
    import logging

    game = tmp_path / "game"
    _mk_version(game, "src", "fps:120\n")
    _mk_version(game, "dst")
    data = tmp_path / "data"  # 生成物锚定 .mcmig(game_root 侧,快照所在)
    mcmig = tmp_path / "mcmig"  # 规则层 .mcmig(CWD 侧,旧布局快照回退位)
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir()

    # 1) 快照放 data_dir/snapshots → build_plan(data_dir=data) 成功
    scan_version(game, "src", data / "snapshots")
    scan_version(game, "dst", data / "snapshots")
    plan, _, _pairs = build_plan(
        tmp_path, game, "src", "dst",
        mcmig_dir=mcmig, plans_dir=plans_dir, data_dir=data,
    )
    assert plan.summary()["must_migrate"] >= 1
    # 锚定命中时不得触碰 mcmig_dir(旧布局位保持为空)
    assert not (mcmig / "snapshots" / "src.snapshot.json").exists()

    # 2) 快照只放 mcmig_dir/snapshots(旧布局) → 仍成功,并 warning 提示旧布局
    for p in (data / "snapshots").iterdir():
        p.unlink()
    scan_version(game, "src", mcmig / "snapshots")
    scan_version(game, "dst", mcmig / "snapshots")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="migration.pipeline"):
        plan, _, _pairs = build_plan(
            tmp_path, game, "src", "dst",
            mcmig_dir=mcmig, plans_dir=plans_dir, data_dir=data,
        )
    assert plan.summary()["must_migrate"] >= 1
    assert any("旧布局" in m for m in caplog.messages)

    # 3) rescan_dst=True → 新快照写 data_dir/snapshots(而非 mcmig_dir)
    (mcmig / "snapshots" / "dst.snapshot.json").unlink()
    build_plan(
        tmp_path, game, "src", "dst", rescan_dst=True,
        mcmig_dir=mcmig, plans_dir=plans_dir, data_dir=data,
    )
    assert (data / "snapshots" / "dst.snapshot.json").exists()
    assert not (mcmig / "snapshots" / "dst.snapshot.json").exists()


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


def test_disk_check_counts_confirmed_ask(tmp_path, monkeypatch):
    """磁盘预检计入 ask_yes 命中的 ASK 动作(spec §3.2,修只计 COPY 的口径)。

    构造 COPY+ASK 各一条 src_size=100MB 的动作(真实文件很小,size 取计划记录),
    余量注入 150MB:ask_yes 空(只计 COPY=100MB)通过并真实执行;同一计划
    ask_yes 命中 ASK(共 200MB)→ DiskSpaceError,零写盘。
    """
    import shutil
    from dataclasses import replace
    from types import SimpleNamespace

    import pytest

    from migration.fsops import DiskSpaceError

    src_root = tmp_path / "s"
    dst_root = tmp_path / "d"
    src_root.mkdir()
    dst_root.mkdir()
    (src_root / "big.bin").write_bytes(b"c")
    (src_root / "q.bin").write_bytes(b"q")
    big = replace(_mk_action("big.bin", Behavior.COPY), src_size=100 << 20)
    ask = replace(_mk_action("q.bin", Behavior.ASK), src_size=100 << 20)
    plan = _mk_plan([big, ask])
    monkeypatch.setattr(shutil, "disk_usage", lambda p: SimpleNamespace(free=150 << 20))

    # 对照:只计 COPY(100MB ≤ 150MB)→ 通过(ASK 未确认不计入)
    results = execute_migration(plan, src_root, dst_root, ask_yes=set())
    assert {r.status for r in results} == {"copied", "asked_no"}
    # 计入已确认 ASK(100+100MB > 150MB)→ 拒绝,预检失败零写盘
    (dst_root / "big.bin").unlink()  # 移除上一轮产物,验证未再写盘
    with pytest.raises(DiskSpaceError):
        execute_migration(plan, src_root, dst_root, ask_yes={"q.bin"})
    assert not (dst_root / "big.bin").exists()
    assert not (dst_root / "q.bin").exists()


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


def test_diff_client_only_registry_modid_channel(tmp_path: Path) -> None:
    """F30① 活体补锚(T5 递延):家族键通道不命中(文件名家族 ≠ frost-dragon),
    modId=glacier_dragon 经注册表反查通道命中 client_only —— 警示行 +
    client_only_paths 恰为该 jar(仅标注不拦截)。"""
    from migration.pipeline import run_diff

    root, _ = _make_game_root(tmp_path, "a", "glacier_dragon", "1.0.0")
    _make_game_root(tmp_path, "b", "othermod", "2.0")  # dst 无该 jar → src 侧落 mods 桶
    snaps = root / ".mcmig" / "snapshots"
    scan_version(root, "a", snaps)
    scan_version(root, "b", snaps)
    out = run_diff(tmp_path, src="a", dst="b", mcmig_dir=root / ".mcmig")
    # 家族键 "glacier-dragon" 不在清单(仅 frost-dragon)→ 命中只能来自 registry modid
    assert out.client_only_paths == {"mods/glacier_dragon-1.0.0.jar"}
    assert any("客户端" in n and "glacier_dragon-1.0.0.jar" in n for n in out.notices)


def test_run_diff_rules_dir_select_single_point(tmp_path: Path) -> None:
    """W2.5 复审 B3:run_diff 规则目录经 select_rules_dir 单点,与 build_plan 同口径。

    基线无规则 → options.txt 入 to_migrate;仅旧布局(cwd/.mcmig)规则 →
    回退读+提示;新位置出现(并存)→ 新位置优先+提示忽略旧——diff 预演与
    plan 正片不再口径分裂(spec §3.1 T1 全入口)。
    """
    from migration.pipeline import run_diff, scan_version

    root = tmp_path / "root"
    for n in ("a", "b"):
        vdir = root / "versions" / n
        vdir.mkdir(parents=True)
        (vdir / "options.txt").write_text("v=1\n" if n == "a" else "v=2\n", encoding="utf-8")
        (vdir / "servers.dat").write_bytes(b"\x0a\x00\x00")
    scan_version(root, "a", root / ".mcmig" / "snapshots")
    scan_version(root, "b", root / ".mcmig" / "snapshots")

    def _to_migrate(out) -> set[str]:
        return {i.path for i in out.report.to_migrate}

    assert "options.txt" in _to_migrate(run_diff(tmp_path, src="a", dst="b", game_root=root))

    # 仅旧布局规则 → 回退读(never 生效)+ 提示
    legacy = tmp_path / ".mcmig"
    legacy.mkdir()
    (legacy / "rules.yaml").write_text(
        "version: 1\nrules:\n  - match: options.txt\n    decide: never\n    reason: t\n",
        encoding="utf-8")
    out = run_diff(tmp_path, src="a", dst="b", game_root=root)
    assert "options.txt" not in _to_migrate(out)
    assert any("旧布局规则" in n for n in out.notices)

    # 新旧并存 → 新位置优先(旧 never 失效,新 never 转投 servers.dat)+ 提示忽略旧
    (root / ".mcmig" / "rules.yaml").write_text(
        "version: 1\nrules:\n  - match: servers.dat\n    decide: never\n    reason: t\n",
        encoding="utf-8")
    out2 = run_diff(tmp_path, src="a", dst="b", game_root=root)
    assert "options.txt" in _to_migrate(out2)      # 旧规则被忽略,恢复默认判定
    assert "servers.dat" not in _to_migrate(out2)  # 新规则生效
    assert any("已忽略" in n for n in out2.notices)


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


def test_resolve_diff_context_same_dir_detected(tmp_path):
    """junction 同体:两侧版本目录 resolve 后同路径 → same_dir=True(ctx 仍可用,孤儿照常)。"""
    root = tmp_path / "root"
    (root / "versions" / "a" / "mods").mkdir(parents=True)
    (root / "versions" / "b").symlink_to(root / "versions" / "a", target_is_directory=True)
    from migration.pipeline import resolve_diff_context

    ctx = resolve_diff_context(_snap("a", str(root)), _snap("b", str(root)))
    assert ctx is not None and ctx.same_dir is True


def test_resolve_diff_context_distinct_dirs_same_dir_false(tmp_path):
    root = tmp_path / "root"
    (root / "versions" / "a" / "mods").mkdir(parents=True)
    (root / "versions" / "b" / "mods").mkdir(parents=True)
    from migration.pipeline import resolve_diff_context

    ctx = resolve_diff_context(_snap("a", str(root)), _snap("b", str(root)))
    assert ctx is not None and ctx.same_dir is False


def test_resolve_context_dirs_live_single_point(tmp_path):
    """终审递延 Minor:目录活性单点化——resolve_diff_context 判一次记入
    DiffContext.dirs_live,run_diff 嵌入名册提示行消费该字段,不再二次探活。

    冻结通道(双侧嵌入)目录不可达 → ctx 存活且 dirs_live=False;
    冻结通道活体双侧可达 → dirs_live=True;v1 活体 → True。
    """
    from migration.pipeline import resolve_diff_context

    ra, _ = _make_game_root(tmp_path, "a", "x", "1.0")
    rb, _ = _make_game_root(tmp_path, "b", "y", "1.0")
    embed = [{"modid": "x", "version": "1.0", "jar_filename": "x-1.0.jar"}]

    def _frozen(version: str, game_root: str) -> Snapshot:
        s = _snap(version, game_root)
        s.mods = list(embed)
        return s

    # 冻结 + dst 不可达(跨机复放):ctx 存活,活性记录为 False
    ctx = resolve_diff_context(_frozen("a", str(ra)), _frozen("ghost", str(ra)))
    assert ctx is not None and ctx.mods_frozen is True and ctx.dirs_live is False
    # 冻结 + 活体双侧可达:dirs_live=True(嵌入名册提示行不触发)
    ctx2 = resolve_diff_context(_frozen("a", str(ra)), _frozen("b", str(rb)))
    assert ctx2 is not None and ctx2.dirs_live is True
    # v1 活体(无嵌入):ctx 存活即双侧可达,dirs_live=True
    ctx3 = resolve_diff_context(_snap("a", str(ra)), _snap("b", str(rb)))
    assert ctx3 is not None and ctx3.dirs_live is True


def test_diff_context_pairing_trusted() -> None:
    """终审建议收敛:配对可信性单点——same_dir 且非冻结→不可信;冻结或异体→可信。"""
    from pathlib import Path as _P

    from migration.moddb import ModRegistry
    from migration.pipeline import DiffContext

    def _ctx(same_dir: bool, frozen: bool) -> DiffContext:
        reg = ModRegistry()
        return DiffContext(src_mods=reg, dst_mods=reg, src_dir=_P("a"), dst_dir=_P("b"),
                           same_dir=same_dir, mods_frozen=frozen)

    assert _ctx(True, False).pairing_trusted() is False   # junction 同体+现扫:不可信
    assert _ctx(True, True).pairing_trusted() is True     # 冻结通道:嵌入名册各自时刻
    assert _ctx(False, False).pairing_trusted() is True   # 异体:可信
    assert _ctx(False, True).pairing_trusted() is True


# ---- 批次F Task 4:diff 编排下沉 run_diff ----


def test_run_diff_outcome_and_notices(tmp_path: Path) -> None:
    """run_diff 下沉:六桶/配对/快照齐备;swap 提示与「mods 扫描不可用」进 notices。"""
    import json

    from migration.pipeline import run_diff

    game_root = tmp_path / "game"
    va = game_root / "versions" / "a"
    vb = game_root / "versions" / "b"
    (va / "mods").mkdir(parents=True)
    (vb / "mods").mkdir(parents=True)
    (va / "options.txt").write_text("fps:60\n", encoding="utf-8")
    (va / "mods" / "x-1.0.0.jar").write_bytes(b"")  # 占位 jar(空文件,注册表不可解析)
    (vb / "mods" / "x-1.1.0.jar").write_bytes(b"")
    data = game_root / ".mcmig" / "snapshots"
    scan_version(game_root, "a", data)
    scan_version(game_root, "b", data)

    out = run_diff(tmp_path, src="a", dst="b", game_root=game_root)
    assert out.src.version == "a" and out.dst.version == "b"
    assert any(i.path.startswith("mods/") for i in out.report.mods)
    assert isinstance(out.mod_pairs, list)
    # 空占位 jar 注册表配对为空,文件名配对兜底:x 1.0.0 → 1.1.0 upgrade
    assert len(out.mod_pairs) == 1
    assert (out.mod_pairs[0].kind, out.mod_pairs[0].source) == ("upgrade", "filename")
    assert out.notices == []  # live 双目录健康路径零提示

    # swap 模式:排除提示进 notices(有排除时)
    out_sw = run_diff(tmp_path, src="a", dst="b", modpack_swap=True, game_root=game_root)
    assert any("换包模式" in n for n in out_sw.notices)

    # 复放(game_root=None):快照直取 cwd/.mcmig;ctx 按快照内 game_root 判定(I-1:
    # 与 game_root 参数无关),故把复放份的 game_root 改写为不存在路径模拟真复放
    # (tmp 夹具根可达,不改写则 ctx 激活、降级提示不会出现)
    legacy = tmp_path / ".mcmig" / "snapshots"
    legacy.mkdir(parents=True)
    for n in ("a", "b"):
        doc = json.loads((data / f"{n}.snapshot.json").read_text(encoding="utf-8"))
        doc["game_root"] = r"C:\\definitely\\missing"
        (legacy / f"{n}.snapshot.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    out_replay = run_diff(tmp_path, src="a", dst="b")
    assert any("mods 扫描不可用" in n for n in out_replay.notices)
    assert out_replay.mod_pairs  # 文件名配对兜底仍可用


def test_run_diff_missing_snapshot_raises(tmp_path: Path) -> None:
    """缺快照 → FileNotFoundError(消息含版本名),CLI 层保持既有退出码 2 文案。"""
    import pytest

    from migration.pipeline import run_diff

    with pytest.raises(FileNotFoundError, match="b"):
        run_diff(tmp_path, src="a", dst="b", game_root=tmp_path)


def test_match_client_only_embedded_modid_hits_host_jar() -> None:
    """F32:内嵌(jar-in-jar) modid 反查宿主物理 jar——anima 命中 damage-engine 路径。"""
    from pathlib import Path

    from migration.differ import DiffItem, DiffReport
    from migration.moddb import ModInfo, ModRegistry
    from migration.pipeline import DiffContext, match_client_only_paths

    reg = ModRegistry()
    reg.add(ModInfo(modid="damageengine", version="2.1.1",
                    jar_filename="damage-engine-2.1.1-NeoForge-1.21.1.jar",
                    neoforge_range=None))
    reg.add(ModInfo(modid="anima", version="1.0.5", jar_filename="anima-1.0.5.jar",
                    neoforge_range=None,
                    embedded_in="damage-engine-2.1.1-NeoForge-1.21.1.jar"))
    ctx = DiffContext(src_mods=reg, dst_mods=reg,
                      src_dir=Path("x"), dst_dir=Path("y"))
    report = DiffReport()
    report.mods = [DiffItem("mods/damage-engine-2.1.1-NeoForge-1.21.1.jar",
                            None, None, note="to_add")]
    hit = match_client_only_paths(report, ctx, {"anima"}, set())
    assert hit == {"mods/damage-engine-2.1.1-NeoForge-1.21.1.jar"}
    # 顶层 modid 反查行为不变
    hit2 = match_client_only_paths(report, ctx, {"damageengine"}, set())
    assert hit2 == {"mods/damage-engine-2.1.1-NeoForge-1.21.1.jar"}


def _wsnap(version: str, world_dirs: list[str], files: dict[str, int]) -> "Snapshot":
    """构造只含 path→size 的迷你快射(F34② 测试用)。"""
    from migration.snapshot import FileEntry, Snapshot

    return Snapshot(version=version, game_root="C:\\fixture", scanned_at="t",
                    hash_mode="tiered", file_count=len(files),
                    files=[FileEntry(p, s, None) for p, s in files.items()],
                    world_dirs=world_dirs)


def test_world_rename_notices_full_match() -> None:
    """F34②:world → world_backup 全量同路径同尺寸 → 一条改名提示。

    批次H-T4 起分母须 ≥_MIN_WORLD_FILES=5 才评估,夹具由 2 件扩至 5 件
    (微小世界抑制由 test_world_rename_tiny_world_suppressed 专测)。
    """
    from migration.pipeline import world_rename_notices

    src = _wsnap("a", ["world"], {f"world/{i}.mca": i for i in range(5)})
    dst = _wsnap("b", ["world_backup"], {f"world_backup/{i}.mca": i for i in range(5)})
    notices = world_rename_notices(src, dst)
    assert len(notices) == 1
    assert "疑似世界目录改名" in notices[0]
    assert "world → world_backup" in notices[0] and "5" in notices[0]


def test_world_rename_notices_threshold_and_mismatch() -> None:
    """F34②:占比 <0.9 不发(跨世界重合极低);同路径但 size 异不计入匹配。"""
    from migration.pipeline import world_rename_notices

    # 10 件中 8 件匹配(80%) → 不发
    src = _wsnap("a", ["world"], {f"world/{i}.mca": i for i in range(10)})
    dst = _wsnap("b", ["nb"], {f"nb/{i}.mca": (i if i < 8 else 999) for i in range(10)})
    assert world_rename_notices(src, dst) == []
    # 10/10 匹配 → 发
    dst2 = _wsnap("b", ["nb"], {f"nb/{i}.mca": i for i in range(10)})
    assert len(world_rename_notices(src, dst2)) == 1
    # 旧目录双侧都在(未消失) → 不参与
    both = _wsnap("b", ["world"], {"world/level.dat": 1})
    assert world_rename_notices(src, both) == []


def test_world_rename_notices_empty_and_no_dirs() -> None:
    """F34②:无 world_dirs(旧快照)或无候选对 → 空。"""
    from migration.pipeline import world_rename_notices

    assert world_rename_notices(_wsnap("a", [], {}), _wsnap("b", [], {})) == []


# ---- 批次H Task 3:消费策略 B——冻结通道(resolve_diff_context 双侧嵌入) ----


def test_resolve_diff_context_frozen_branch() -> None:
    """双侧嵌入 → mods_frozen=True 且 mods 来自嵌入(不触盘);单侧 v1 → 活体/None 老路径。"""
    from migration.pipeline import resolve_diff_context

    # FIX 同 synth_v2 锚定测试(synth_v2 夹具:game_root 不可达,复放语义)
    fix = Path(__file__).parent / "fixtures" / "server_corpus" / "synth_v2"
    src = Snapshot.load(fix / "junction_pre.snapshot.json")
    dst = Snapshot.load(fix / "junction_post.snapshot.json")
    ctx = resolve_diff_context(src, dst)
    assert ctx is not None and ctx.mods_frozen is True
    assert set(ctx.src_mods.modids) == {"foo"} and ctx.src_mods.get("foo").version == "1.0"
    assert ctx.same_dir is True          # resolved_root 双侧相等


# ---- 批次H Task 4:F32 守卫直测 + world_rename 最小分母门槛 + client_only 截断 ----


def test_client_only_embedded_host_guard_negative() -> None:
    """F32 守卫直测·负例:嵌入件命中清单但宿主 jar 不在 mods 桶 → 交集守卫挡住,不误报。"""
    from migration.differ import DiffReport, DiffItem
    from migration.moddb import ModInfo, ModRegistry
    from migration.pipeline import DiffContext, match_client_only_paths
    reg = ModRegistry()
    reg.add(ModInfo("damage-engine-neoforge", "1.0", "phys-embedded.jar",
                    None, embedded_in="host.jar"))
    report = DiffReport()  # mods 桶仅含无关 jar(宿主 host.jar 不在——双侧共有不进桶)
    report.mods.append(DiffItem(path="mods/other.jar", src=None, dst=None))
    ctx = DiffContext(src_mods=reg, dst_mods=ModRegistry(),
                      src_dir=Path("x"), dst_dir=Path("y"))
    assert match_client_only_paths(report, ctx, {"damage-engine-neoforge"}, set()) == set()


def test_client_only_embedded_host_guard_positive() -> None:
    """F32 守卫直测·正例(对照):宿主在 mods 桶 → 命中 mods/host.jar。"""
    from migration.differ import DiffReport, DiffItem
    from migration.moddb import ModInfo, ModRegistry
    from migration.pipeline import DiffContext, match_client_only_paths
    reg = ModRegistry()
    reg.add(ModInfo("damage-engine-neoforge", "1.0", "phys-embedded.jar",
                    None, embedded_in="host.jar"))
    report = DiffReport()  # mods 桶含宿主 jar(源独有/目标独有 → 进桶)
    report.mods.append(DiffItem(path="mods/host.jar", src=None, dst=None))
    ctx = DiffContext(src_mods=reg, dst_mods=ModRegistry(),
                      src_dir=Path("x"), dst_dir=Path("y"))
    assert match_client_only_paths(report, ctx, {"damage-engine-neoforge"}, set()) == {"mods/host.jar"}


def test_world_rename_tiny_world_suppressed() -> None:
    """1 文件世界 100% 命中 → 无提示(_MIN_WORLD_FILES=5,spec §3.4)。"""
    from migration.pipeline import world_rename_notices

    src = _wsnap("a", ["a"], {"a/level.dat": 1})
    dst = _wsnap("b", ["b"], {"b/level.dat": 1})
    assert world_rename_notices(src, dst) == []


def test_world_rename_five_files_still_detects() -> None:
    """≥5 文件 90% 命中 → 照常提示(r13 夹具 660 文件回归由既有测试守护)。"""
    from migration.pipeline import world_rename_notices

    src = _wsnap("a", ["a"], {f"a/{i}.mca": i for i in range(5)})
    dst = _wsnap("b", ["b"], {f"b/{i}.mca": i for i in range(5)})
    notices = world_rename_notices(src, dst)
    assert len(notices) == 1
    assert "a → b" in notices[0]


def test_client_warning_truncated_over_five() -> None:
    """警示行 >5 件截断 +「…等 N 件」;≤5 全列(spec §3.6)。"""
    from migration.pipeline import _format_client_warning
    five = {f"mods/m{i}.jar" for i in range(5)}
    assert _format_client_warning(five).endswith("mods/m4.jar") or "等" not in _format_client_warning(five)
    seven = {f"mods/m{i}.jar" for i in range(7)}
    line = _format_client_warning(seven)
    assert "…等 7 件" in line and "m6" not in line.split("…")[0]


def test_build_ruleset_importable_from_pipeline() -> None:
    """主位置迁至 pipeline;cli re-export 不断裂(spec §5.8)。"""
    from migration import cli, pipeline

    assert pipeline.build_ruleset is cli.build_ruleset      # 同一函数对象
    assert pipeline.escape_world_glob("a[1]") == "a\\[1\\]"


def test_escape_world_glob_backslash_and_all_specials() -> None:
    """终审递延 Minor:`\\` 直测+七特殊字符全位置转义(pathspec gitwildmatch 语义)。"""
    from migration import pipeline

    assert pipeline.escape_world_glob("a\\b") == "a\\\\b"  # 转义字符本身也要转义
    for ch in "\\*?[]#!":
        assert pipeline.escape_world_glob(f"a{ch}b") == f"a\\{ch}b"


def test_escape_world_glob_identity_without_specials() -> None:
    """终审递延 Minor:无特殊字符的常规 mod 名/路径恒等返回(零扰动)。"""
    from migration import pipeline

    assert pipeline.escape_world_glob("mods/create-1.0.jar") == "mods/create-1.0.jar"


# ---- 批次I-T3:审阅有效性(build_plan 签发守卫 + persisted 阻断 + 重跑豁免) ----


def test_build_plan_issues_review(built_plan_layout) -> None:
    """build_plan 签发审阅守卫并随 plan 持久化(spec §3.3:计划生成时定格执行前置条件)。"""
    import json

    from migration.review import file_sha256

    lay = built_plan_layout
    review = lay.plan.review
    assert review is not None
    assert review["instance"] == str(lay.game.resolve())
    assert set(review["snapshots"]) == {"src", "dst"}
    assert review["mode"] is False
    assert review["snapshots"]["src"] == file_sha256(lay.snapshot_paths["src"])
    assert review["snapshots"]["dst"] == file_sha256(lay.snapshot_paths["dst"])
    # 落盘的 plan JSON 同样含 review(load 兼容)
    doc = json.loads(
        (lay.data / "plans" / "src__dst.plan.json").read_text(encoding="utf-8")
    )
    assert doc["review"] == review


def test_build_plan_save_failure_raises(built_plan_layout, monkeypatch) -> None:
    """② 白名单:保存失败上抛 PlanPersistError 不再吞(封死「审新执旧」,spec §3.3)。"""
    import pytest

    from migration import pipeline
    from migration.plan import MigrationPlan, PlanPersistError

    def _boom(self, path: Path) -> None:  # noqa: ARG001 — 类属性替换,签名须与 save 一致
        raise OSError("disk full (注入)")

    monkeypatch.setattr(MigrationPlan, "save", _boom)
    with pytest.raises(PlanPersistError):
        pipeline.build_plan(
            built_plan_layout.cwd, built_plan_layout.game, "src", "dst",
            mcmig_dir=built_plan_layout.data,
            plans_dir=built_plan_layout.data / "plans",
            data_dir=built_plan_layout.data,
        )


def test_rerun_skips_target_state_check(built_plan_layout) -> None:
    """⑤ 白名单:重跑(validate_states=False 明确决策)跳过目标状态校验→identical 短路。

    Review Focus 3:首跑改写目标后状态必然失配——不加豁免必被 ReviewStateError
    拦截;豁免是重跑决策的特权,依赖 identical 短路与 job journal(spec §3.3 v4 补注)。
    """
    import pytest

    from migration.review import ReviewStateError

    lay = built_plan_layout
    # 首跑:状态与快照一致 → 内部校验通过,真实执行
    results = execute_migration(lay.plan, lay.src_dir, lay.dst_dir, ask_yes=set())
    assert not any(r.failed for r in results)
    # 重跑不加豁免:目标已被首跑改写 → 审阅状态校验拦截
    with pytest.raises(ReviewStateError):
        execute_migration(lay.plan, lay.src_dir, lay.dst_dir, ask_yes=set())
    # 重跑加豁免(调用方 rerun_executed 决策):identical 短路,照常完成
    results2 = execute_migration(
        lay.plan, lay.src_dir, lay.dst_dir, ask_yes=set(), validate_states=False
    )
    assert not any(r.failed for r in results2)


def test_execute_migration_without_snapshots_skips_state_check(tmp_path) -> None:
    """合成计划/非锚定布局(无 .mcmig/snapshots)→ 状态校验降级跳过,执行照常。

    兼容既有直调用法(execute_migration 不因快照缺失而拒绝执行;
    真实 plan 管线用法恒有锚定快照)。
    """
    src_root = tmp_path / "s"
    dst_root = tmp_path / "d"
    src_root.mkdir()
    dst_root.mkdir()
    (src_root / "a.txt").write_text("A", encoding="utf-8")
    plan = _mk_plan([_mk_action("a.txt", Behavior.COPY)])
    results = execute_migration(plan, src_root, dst_root, ask_yes=set())
    assert {r.status for r in results} == {"copied"}
    assert (dst_root / "a.txt").read_text(encoding="utf-8") == "A"


# ---- W2.6 复审 P1-1:守卫快照取位与签发/重验同构 + 有守卫缺材料须阻断 ----


def test_execute_guard_snapshots_fallback_to_legacy_dir(built_plan_layout) -> None:
    """W2.6 复审 P1-1:快照仅存旧布局时,状态校验经 legacy_dir 回退照常进行。

    修复前 _load_guard_snapshots 只认锚定布局(<game_root>/.mcmig/snapshots),
    缺失即返回 None 静默跳过审阅状态校验——而签发(build_plan)与哈希重验
    (validate_review)都允许旧布局回退,守卫材料三处取位不同构:reviewer 复现
    「计划签发后改目标 options.txt,无 --force 仍被静默覆盖」。修复后执行侧
    与 find_snapshot 同一命中位(锚定优先+旧布局回退),漂移照常拦截。
    """
    import pytest

    from migration.review import ReviewStateError

    lay = built_plan_layout
    legacy = lay.cwd / ".mcmig"
    legacy_snaps = legacy / "snapshots"
    legacy_snaps.mkdir(parents=True)
    anchored = lay.data / "snapshots"
    for ver in ("src", "dst"):
        (anchored / f"{ver}.snapshot.json").rename(legacy_snaps / f"{ver}.snapshot.json")
    anchored.rmdir()
    # 签发后外部改动目标(options.txt 为已哈希条目 → md5 全检):必须在旧布局
    # 快照上拦截,而非因锚定位缺失而放行
    dst_opts = lay.dst_dir / "options.txt"
    orig = dst_opts.read_text(encoding="utf-8")
    dst_opts.write_text(orig + "extra_drift:1\n", encoding="utf-8")
    with pytest.raises(ReviewStateError):
        execute_migration(
            lay.plan, lay.src_dir, lay.dst_dir, ask_yes=set(), legacy_dir=legacy
        )
    # 复原后同一调用放行——证明回退是真加载了旧布局快照(不是换一种跳过)
    dst_opts.write_text(orig, encoding="utf-8")
    results = execute_migration(
        lay.plan, lay.src_dir, lay.dst_dir, ask_yes=set(), legacy_dir=legacy
    )
    assert not any(r.failed for r in results)


def test_execute_guard_snapshots_missing_fails_closed(built_plan_layout) -> None:
    """W2.6 复审 P1-1:有审阅守卫却缺校验材料 → 阻断(fail-closed),不再静默跳过。

    锚定与旧布局回退都不命中时,带 review 的计划不得在无状态校验下执行
    (不可校验≠可执行);review=None 的合成计划保留降级跳过语义
    (test_execute_migration_without_snapshots_skips_state_check 继续覆盖)。
    """
    import pytest

    from migration.review import ReviewStateError

    lay = built_plan_layout
    for p in (lay.data / "snapshots").glob("*.snapshot.json"):
        p.unlink()
    with pytest.raises(ReviewStateError) as ei:
        execute_migration(lay.plan, lay.src_dir, lay.dst_dir, ask_yes=set())
    assert any(b.code == "review_snapshot_missing" for b in ei.value.blockers)
    # 零写盘:目标未被触碰
    assert (lay.dst_dir / "options.txt").exists()


def test_execute_guard_snapshot_corrupt_fails_closed(built_plan_layout) -> None:
    """W2.6 复审 P1-1:守卫快照损坏(不可读)与缺失同型——有守卫即阻断,不降级跳过。"""
    import pytest

    from migration.review import ReviewStateError

    lay = built_plan_layout
    (lay.data / "snapshots" / "src.snapshot.json").write_bytes(b"not-json{")
    with pytest.raises(ReviewStateError) as ei:
        execute_migration(lay.plan, lay.src_dir, lay.dst_dir, ask_yes=set())
    assert any(b.code == "review_snapshot_missing" for b in ei.value.blockers)


def test_build_plan_review_includes_extra_rule_files(tmp_path) -> None:
    """W2.6 复审 P2-3:--rule 额外规则文件进守卫指纹与 rule_sources 记录。

    修复前 issue_review 只记录 chosen_rules_dir/rules.yaml,额外规则文件
    (--rule)的内容漂移对守卫不可见(reviewer 复现:复制计划在额外规则改成
    禁止后仍照常执行)。修复后额外文件随签发记录,改动 → rules_changed。
    """
    from migration.review import validate_review

    game = tmp_path / "game"
    _mk_version(game, "src", "fps:120\n")
    _mk_version(game, "dst", "fps:60\n")
    data = game / ".mcmig"
    scan_version(game, "src", data / "snapshots")
    scan_version(game, "dst", data / "snapshots")
    extra = tmp_path / "extra.yaml"
    extra.write_text("version: 1\nrules: []\n", encoding="utf-8")
    plan, _compat, _pairs = build_plan(
        tmp_path, game, "src", "dst",
        mcmig_dir=data, plans_dir=data / "plans", data_dir=data,
        rule_files=[extra],
    )
    assert plan.review is not None
    assert str(extra) in plan.review["rule_sources"]
    # 调用方按 CLI/GUI 现状只传现选 rules.yaml:重验以记录清单为准
    kw = {
        "game_root": game,
        "snapshot_paths": {
            "src": data / "snapshots" / "src.snapshot.json",
            "dst": data / "snapshots" / "dst.snapshot.json",
        },
        "rule_sources": [data / "rules.yaml"],
    }
    assert validate_review(plan, **kw) == []
    extra.write_text(
        "version: 1\nrules:\n  - match: options.txt\n    decide: never\n    reason: 改主意\n",
        encoding="utf-8",
    )
    blockers = validate_review(plan, **kw)
    assert [b.code for b in blockers] == ["rules_changed"]
