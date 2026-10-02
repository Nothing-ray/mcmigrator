"""workdir 模块测试:绿色模式/兼容模式/实例态锚定(game_root/.mcmig)/不可写报错。

批次I-T1 路径契约 v3:实例态(快照/计划/规则/jobs/locks)统一锚定
``<game_root>/.mcmig``(spec §3.1),绿色/源码两模式同址;旧绿色 slug 布局
(``exe/data/<slug>/``)降级为 legacy_snapshots 只读回退。
"""

from pathlib import Path  # noqa: F401 — brief 原文测试保留(未直接引用)

import pytest

from migration.workdir import WorkdirError, resolve_workdir


def test_compat_mode_uses_cwd_mcmig(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("migration.workdir._is_frozen", lambda: False)
    wd = resolve_workdir(game_root=tmp_path / "game")
    assert wd.root == tmp_path / ".mcmig"
    assert wd.green is False
    assert wd.rules.name == "rules.yaml"


def test_green_mode_layout_and_slug(tmp_path, monkeypatch):
    """批次I-T1 路径契约 v3:绿色模式实例态锚定 game_root/.mcmig,全局态仍在 exe/data。"""
    monkeypatch.setattr("migration.workdir._is_frozen", lambda: True)
    monkeypatch.setattr("migration.workdir._exe_dir", lambda: tmp_path)
    game = tmp_path / "game"
    wd = resolve_workdir(game_root=game)
    assert wd.green is True
    assert wd.root == tmp_path / "data"
    assert wd.snapshots == game / ".mcmig" / "snapshots"
    assert wd.rules == game / ".mcmig" / "rules.yaml"
    assert wd.config == tmp_path / "data" / "config.toml"


def test_green_mode_slug_isolates_roots(tmp_path, monkeypatch):
    """批次I-T1 路径契约 v3:不同 game_root 的实例态目录天然隔离(各自 .mcmig 下)。"""
    monkeypatch.setattr("migration.workdir._is_frozen", lambda: True)
    monkeypatch.setattr("migration.workdir._exe_dir", lambda: tmp_path)
    a = resolve_workdir(game_root=tmp_path / "alpha")
    b = resolve_workdir(game_root=tmp_path / "beta")
    assert a.snapshots != b.snapshots


def test_green_mode_game_root_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("migration.workdir._is_frozen", lambda: True)
    monkeypatch.setattr("migration.workdir._exe_dir", lambda: tmp_path)
    wd = resolve_workdir(game_root=tmp_path / "game")
    wd.save_game_root(tmp_path / "game")
    wd2 = resolve_workdir()
    assert wd2.game_root == tmp_path / "game"


def test_instance_paths_anchor_to_game_root(tmp_path, monkeypatch):
    """实例态(快照/计划/规则/jobs/locks)统一锚定 <game_root>/.mcmig,绿/源码两模式同址(spec §3.1)。"""
    import migration.workdir as wd

    monkeypatch.setattr(wd, "_is_frozen", lambda: True)
    monkeypatch.setattr(wd, "_exe_dir", lambda: tmp_path / "exe")
    (tmp_path / "exe").mkdir()
    game = tmp_path / "game"
    game.mkdir()
    w = wd.resolve_workdir(game_root=game)
    assert w.game_root == game
    assert w.snapshots == game / ".mcmig" / "snapshots"
    assert w.plans == game / ".mcmig" / "plans"
    assert w.rules == game / ".mcmig" / "rules.yaml"
    assert w.jobs == game / ".mcmig" / "jobs"
    assert w.locks == game / ".mcmig" / "locks"
    assert w.config == tmp_path / "exe" / "data" / "config.toml"  # 全局态仍在软件侧
    # 源码模式同址
    monkeypatch.setattr(wd, "_is_frozen", lambda: False)
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    w2 = wd.resolve_workdir(game_root=game)
    assert w2.snapshots == game / ".mcmig" / "snapshots" and w2.rules == w.rules


def test_green_unconfigured_returns_welcome_state(tmp_path, monkeypatch):
    """绿色未配置:不再抛 WorkdirError,返回 game_root=None 的欢迎态布局(spec §3.1 首跑)。"""
    import migration.workdir as wd

    monkeypatch.setattr(wd, "_is_frozen", lambda: True)
    monkeypatch.setattr(wd, "_exe_dir", lambda: tmp_path / "exe")
    (tmp_path / "exe").mkdir()
    w = wd.resolve_workdir()  # 不抛
    assert w.game_root is None and w.snapshots is None and w.plans is None


def test_green_legacy_slug_fallback_recorded(tmp_path, monkeypatch):
    """旧绿色 exe/data/<slug>/snapshots 存在时记入 legacy_snapshots(只读回退,spec §3.1)。"""
    import migration.workdir as wd

    monkeypatch.setattr(wd, "_is_frozen", lambda: True)
    monkeypatch.setattr(wd, "_exe_dir", lambda: tmp_path / "exe")
    game = tmp_path / "game"
    game.mkdir()
    old = tmp_path / "exe" / "data" / game.name / "snapshots"
    old.mkdir(parents=True)
    w = wd.resolve_workdir(game_root=game)
    assert w.legacy_snapshots == old


def test_unwritable_exe_dir_raises(tmp_path, monkeypatch):
    import sys  # noqa: F401 — brief 原文测试保留(未直接引用)
    monkeypatch.setattr("migration.workdir._is_frozen", lambda: True)
    monkeypatch.setattr("migration.workdir._exe_dir", lambda: tmp_path)
    monkeypatch.setattr("migration.workdir._ensure_writable",
                        lambda p: (_ for _ in ()).throw(OSError(13, "denied")))
    with pytest.raises(WorkdirError) as ei:
        resolve_workdir(game_root=tmp_path / "game")
    assert "不可写" in ei.value.why
