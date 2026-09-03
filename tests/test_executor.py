"""executor 模块测试:复制/冲突备份/MD5 校验/ASK/可重入/dry-run。"""

import hashlib
from pathlib import Path

from migration.executor import Executor
from migration.plan import ActionRecord, Behavior, Origin, MigrationPlan


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _action(path: str, behavior=Behavior.COPY, origin=Origin.MUST_MIGRATE) -> ActionRecord:
    return ActionRecord(path=path, behavior=behavior, origin=origin, src_size=1,
                        dst_size=None, md5_match=None, confidence="high",
                        reason="t", backup_target=None)


def _plan(*actions: ActionRecord) -> MigrationPlan:
    return MigrationPlan(src="s", dst="d", generated_at="t", actions=list(actions))


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (src / "config").mkdir(parents=True)
    (src / "config" / "a.toml").write_text("x=1\n", encoding="utf-8")
    return src, dst


def yes(_a: ActionRecord) -> bool:
    return True


def no(_a: ActionRecord) -> bool:
    return False


def test_execute_copies_files(tmp_path):
    src, dst = _setup(tmp_path)
    plan = _plan(_action("options.txt"))
    results = Executor(plan, src, dst, yes).execute()
    assert (dst / "options.txt").read_text(encoding="utf-8") == "fps:120\n"
    assert results[0].status == "copied" and not results[0].failed


def test_execute_creates_parent_dirs(tmp_path):
    src, dst = _setup(tmp_path)
    plan = _plan(_action("config/a.toml"))
    Executor(plan, src, dst, yes).execute()
    assert (dst / "config" / "a.toml").exists()


def test_execute_conflict_backup_mirrors_path(tmp_path):
    src, dst = _setup(tmp_path)
    (dst / "config").mkdir()
    (dst / "config" / "a.toml").write_text("OLD", encoding="utf-8")
    plan = _plan(_action("config/a.toml"))
    Executor(plan, src, dst, yes).execute()
    assert (dst / "_conflict_backup" / "config" / "a.toml").read_text(encoding="utf-8") == "OLD"
    assert (dst / "config" / "a.toml").read_text(encoding="utf-8") == "x=1\n"


def test_execute_identical_skip_no_backup(tmp_path):
    """可重入:目标与源 MD5 相同 → identical,不产生备份。"""
    src, dst = _setup(tmp_path)
    (dst / "options.txt").write_text("fps:120\n", encoding="utf-8")
    plan = _plan(_action("options.txt"))
    results = Executor(plan, src, dst, yes).execute()
    assert results[0].status == "identical"
    assert not (dst / "_conflict_backup").exists()


def test_execute_twice_second_run_all_identical(tmp_path):
    src, dst = _setup(tmp_path)
    plan = _plan(_action("options.txt"), _action("config/a.toml"))
    ex = Executor(plan, src, dst, yes)
    ex.execute()
    results = ex.execute()
    assert all(r.status == "identical" for r in results)


def test_execute_ask_handler_no_leaves_file(tmp_path):
    src, dst = _setup(tmp_path)
    (dst / "options.txt").write_text("OLD", encoding="utf-8")
    plan = _plan(_action("options.txt", behavior=Behavior.ASK, origin=Origin.NEEDS_REVIEW))
    results = Executor(plan, src, dst, no).execute()
    assert results[0].status == "asked_no"
    assert (dst / "options.txt").read_text(encoding="utf-8") == "OLD"


def test_execute_ask_handler_yes_copies(tmp_path):
    src, dst = _setup(tmp_path)
    plan = _plan(_action("options.txt", behavior=Behavior.ASK, origin=Origin.NEEDS_REVIEW))
    Executor(plan, src, dst, yes).execute()
    assert (dst / "options.txt").read_text(encoding="utf-8") == "fps:120\n"


def test_execute_skip_behavior_untouched(tmp_path):
    src, dst = _setup(tmp_path)
    plan = _plan(_action("logs/latest.log", behavior=Behavior.SKIP, origin=Origin.NEVER))
    results = Executor(plan, src, dst, yes).execute()
    assert results[0].status == "skipped"


def test_execute_missing_src_file_fails(tmp_path):
    src, dst = _setup(tmp_path)
    plan = _plan(_action("ghost.txt"))
    results = Executor(plan, src, dst, yes).execute()
    assert results[0].failed is True
    assert results[0].error is not None


def test_execute_dry_run_writes_nothing(tmp_path):
    src, dst = _setup(tmp_path)
    (dst / "options.txt").write_text("OLD", encoding="utf-8")
    plan = _plan(_action("options.txt"))
    results = Executor(plan, src, dst, yes).execute(dry_run=True)
    assert (dst / "options.txt").read_text(encoding="utf-8") == "OLD"
    assert not (dst / "_conflict_backup").exists()
    assert results[0].status == "copied"  # 预演:将会复制


def test_execute_oserror_on_one_file_continues(tmp_path, monkeypatch):
    """逐文件容错:某文件复制抛 OSError → 该条 failed,后续文件仍被复制。"""
    import migration.executor as executor_mod

    src, dst = _setup(tmp_path)
    real_copy2 = executor_mod.shutil.copy2

    def flaky_copy2(s, d, **kw):
        if Path(s).name == "options.txt":
            raise OSError("文件被游戏进程占用")
        return real_copy2(s, d, **kw)

    monkeypatch.setattr(executor_mod.shutil, "copy2", flaky_copy2)
    plan = _plan(_action("options.txt"), _action("config/a.toml"))
    results = Executor(plan, src, dst, yes).execute()
    assert results[0].failed is True
    assert results[0].error is not None and "复制失败" in results[0].error
    assert not results[1].failed
    assert (dst / "config" / "a.toml").read_text(encoding="utf-8") == "x=1\n"
    assert not (dst / "options.txt").exists()


def test_backup_keeps_first_copy_on_remigrate(tmp_path):
    """重复迁移(--force 场景)不得覆盖首份冲突备份(目标原始值不可逆)。"""
    src, dst = _setup(tmp_path)
    (dst / "config").mkdir()
    (dst / "config" / "a.toml").write_text("原始默认", encoding="utf-8")
    plan = _plan(_action("config/a.toml"))
    Executor(plan, src, dst, yes).execute()
    # 用户又改了源,重跑(--force):此时 dst 内容是上次迁入值
    (src / "config" / "a.toml").write_text("第二次改动", encoding="utf-8")
    Executor(plan, src, dst, yes).execute()
    bak = dst / "_conflict_backup" / "config" / "a.toml"
    assert bak.read_text(encoding="utf-8") == "原始默认"


def test_unreadable_src_md5_fails_without_copy(tmp_path, monkeypatch):
    """源 MD5 不可读时直接判失败,不进入复制(避免假『校验不一致』)。"""
    import migration.executor as ex

    src, dst = _setup(tmp_path)
    monkeypatch.setattr(ex, "_md5_of", lambda p: None)
    plan = _plan(_action("options.txt"))
    results = Executor(plan, src, dst, yes).execute()
    assert results[0].failed
    assert "不可读" in (results[0].error or "")
    assert not (dst / "options.txt").exists()
