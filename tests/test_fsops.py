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


def test_copy_atomic_rollback_failure_degrades_to_warning(tmp_path, monkeypatch, caplog):
    """携带项 B:回滚内部恢复 copy 抛 OSError → 降级 log.warning,不顶替类型化主异常。

    场景构造:verify 阶段校验失败(类型化 FsOpsError)触发回滚;
    回滚比对时诱导「目标已被改动」的假象,使恢复分支执行,
    其内部 copy2(备份→rtmp)被 monkeypatch 为抛 OSError。
    修复前:裸 OSError 从 except 块内上抛,顶替主异常且击穿 executor 逐文件容错。
    """
    import logging

    import migration.fsops as fs

    src = tmp_path / "new.txt"
    src.write_text("NEW", encoding="utf-8")
    dst_dir = tmp_path / "d"
    dst_dir.mkdir()
    target = dst_dir / "f.txt"
    target.write_text("OLD", encoding="utf-8")
    backup_dir = dst_dir / "_conflict_backup"

    # 队列驱动 md5_of:①src 原值 ②dst 原值 ③tmp 校验返回错误值(触发 verify 失败)
    # ④回滚比对 dst 返回异于 ② 的值(诱导「目标已被改动」进入恢复分支)
    real_md5_of = fs.md5_of
    returns = [real_md5_of(src), real_md5_of(target), "deadbeef", "f00dfeed"]
    monkeypatch.setattr(fs, "md5_of", lambda p: returns.pop(0) if returns else real_md5_of(p))

    # 回滚内部恢复 copy(备份→rtmp)抛 OSError(模拟备份盘只读/被占用);
    # 注意 rollback 以 long_path 传入(Windows 加 \\?\ 前缀),比对须同样归一化
    real_copy2 = fs.shutil.copy2
    bak_prefix = str(fs.long_path(backup_dir))

    def broken_copy2(s, d, **kw):
        if str(s).startswith(bak_prefix):
            raise OSError("回滚恢复时备份不可读")
        return real_copy2(s, d, **kw)

    monkeypatch.setattr(fs.shutil, "copy2", broken_copy2)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(fs.FsOpsError, match="MD5 校验不一致"):
            fs.copy_atomic(src, target, rel="f.txt", backup_dir=backup_dir)

    assert "回滚失败" in caplog.text  # 恢复失败降级为 warning
    assert target.read_text(encoding="utf-8") == "OLD"  # 目标保持原内容
    assert not list(dst_dir.glob("*.mcmig-tmp"))  # tmp 已由 finally 清除


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
