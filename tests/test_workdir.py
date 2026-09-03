"""workdir 模块测试:绿色模式/兼容模式/slug 隔离/不可写报错。"""

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
    monkeypatch.setattr("migration.workdir._is_frozen", lambda: True)
    monkeypatch.setattr("migration.workdir._exe_dir", lambda: tmp_path)
    game = tmp_path / "game"
    wd = resolve_workdir(game_root=game)
    assert wd.green is True
    assert wd.root == tmp_path / "data"
    assert wd.snapshots == tmp_path / "data" / "game" / "snapshots"
    assert wd.rules == tmp_path / "data" / "game" / "rules.yaml"
    assert wd.config == tmp_path / "data" / "config.toml"


def test_green_mode_slug_isolates_roots(tmp_path, monkeypatch):
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
    assert wd2.game_root() == tmp_path / "game"


def test_green_mode_unconfigured_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("migration.workdir._is_frozen", lambda: True)
    monkeypatch.setattr("migration.workdir._exe_dir", lambda: tmp_path)
    with pytest.raises(WorkdirError) as ei:
        resolve_workdir()
    assert "游戏目录" in ei.value.what
    # 终审 I-1:未配置指引须指向真实入口——界面设置或手动 data/config.toml
    assert "图形界面" in ei.value.why
    assert "data/config.toml" in ei.value.why
    assert "game_root" in ei.value.why


def test_unwritable_exe_dir_raises(tmp_path, monkeypatch):
    import sys  # noqa: F401 — brief 原文测试保留(未直接引用)
    monkeypatch.setattr("migration.workdir._is_frozen", lambda: True)
    monkeypatch.setattr("migration.workdir._exe_dir", lambda: tmp_path)
    monkeypatch.setattr("migration.workdir._ensure_writable",
                        lambda p: (_ for _ in ()).throw(OSError(13, "denied")))
    with pytest.raises(WorkdirError) as ei:
        resolve_workdir(game_root=tmp_path / "game")
    assert "不可写" in ei.value.why
