"""fsops 模块测试:原子复制/回滚/原子写/长路径/磁盘检查/类型化异常。"""

import pytest

from migration.fsops import (
    DiskSpaceError, SourceMissingError, clean_stale_tmp, copy_atomic,
    md5_of, write_json_atomic,
)


def test_copy_atomic_basic_and_backup(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("NEW", encoding="utf-8")
    dst = tmp_path / "b.txt"
    dst.write_text("OLD", encoding="utf-8")
    bak = tmp_path / "bak"
    backed = copy_atomic(src, dst, backup_dir=bak, rel="b.txt")
    assert dst.read_text(encoding="utf-8") == "NEW"
    assert backed is True
    assert (bak / "b.txt").read_text(encoding="utf-8") == "OLD"


def test_copy_atomic_identical_short_circuit(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("SAME", encoding="utf-8")
    dst = tmp_path / "a.txt"
    dst.write_text("SAME", encoding="utf-8")
    assert copy_atomic(src, dst, backup_dir=tmp_path / "bak", rel="a.txt") is False


def test_copy_atomic_missing_source_raises(tmp_path):
    with pytest.raises(SourceMissingError):
        copy_atomic(tmp_path / "ghost", tmp_path / "x", backup_dir=None, rel="x")


def test_copy_atomic_locked_target_rolls_back(tmp_path, monkeypatch):
    """换名失败(模拟锁定)→ 目标保持原内容,备份在场,tmp 清除。"""
    src = tmp_path / "a.txt"
    src.write_text("NEW", encoding="utf-8")
    dst = tmp_path / "b.txt"
    dst.write_text("OLD", encoding="utf-8")
    bak = tmp_path / "bak"
    import migration.fsops as fs

    def boom(a, b):
        raise PermissionError(13, "另一个程序正在使用此文件")

    monkeypatch.setattr(fs.os, "replace", boom)
    with pytest.raises(fs.TargetLockedError):
        fs.copy_atomic(src, dst, backup_dir=bak, rel="b.txt")
    assert dst.read_text(encoding="utf-8") == "OLD"          # 回滚
    assert (bak / "b.txt").read_text(encoding="utf-8") == "OLD"
    assert not list(tmp_path.glob("*.mcmig-tmp"))            # tmp 清干净


def test_write_json_atomic_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    write_json_atomic(p, {"k": "中文"})
    import json
    assert json.loads(p.read_text(encoding="utf-8"))["k"] == "中文"
    assert not list(tmp_path.glob("*.mcmig-tmp"))


def test_clean_stale_tmp(tmp_path):
    (tmp_path / "x.mcmig-tmp").write_text("half", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "y.mcmig-tmp").write_text("h", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("k", encoding="utf-8")
    assert clean_stale_tmp(tmp_path) == 2
    assert (tmp_path / "keep.txt").exists()
    assert not list(tmp_path.rglob("*.mcmig-tmp"))


def test_check_disk_space_raises_with_gap(tmp_path):
    with pytest.raises(DiskSpaceError) as ei:
        from migration.fsops import check_disk_space
        check_disk_space(tmp_path, needed_bytes=10**15)
    assert "MB" in ei.value.why


def test_md5_of_missing_returns_none(tmp_path):
    assert md5_of(tmp_path / "ghost") is None
