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


def test_execute_progress_cb_called_per_file(tmp_path):
    """progress_cb 逐文件回调,顺序与结果一致(不传=行为不变由其余测试覆盖)。"""
    src, dst = tmp_path / "s", tmp_path / "d"
    src.mkdir()
    dst.mkdir()
    (src / "a.txt").write_text("1", encoding="utf-8")
    (src / "b.txt").write_text("2", encoding="utf-8")
    plan = _plan(_action("a.txt"), _action("b.txt"))
    seen = []
    Executor(plan, src, dst, yes).execute(progress_cb=seen.append)
    assert [r.path for r in seen] == ["a.txt", "b.txt"]


def test_execute_progress_cb_realtime_before_next_file(tmp_path):
    """实时性:收到 a.txt 回调时 b.txt 尚未写盘(单文件完成即回调,非批量回放)。"""
    src, dst = _setup(tmp_path)
    plan = _plan(_action("options.txt"), _action("config/a.toml"))
    snapshot_at_first_cb: list[bool] = []

    def cb(r) -> None:
        """首个文件回调时,检查第二个文件是否仍未写盘。"""
        if r.path == "options.txt":
            snapshot_at_first_cb.append(not (dst / "config" / "a.toml").exists())

    Executor(plan, src, dst, yes).execute(progress_cb=cb)
    assert snapshot_at_first_cb == [True]


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


# ---- 批次I-T6:取消检查点(动作间停发,安全边界内无半途文件) ----


def test_cancel_checkpoint_stops_between_files(mini_plan_dirs):
    """should_cancel 在第 1 个文件后命中→停发,返回部分结果且 executor.cancelled。"""
    ex = Executor(mini_plan_dirs.plan, mini_plan_dirs.src, mini_plan_dirs.dst, yes)
    gate = {"stop": False}
    results = ex.execute(should_cancel=lambda: gate["stop"],
                         progress_cb=lambda r: gate.__setitem__("stop", True))
    assert ex.cancelled and len(results) == 1
    assert results[0].status == "copied"
    # 安全边界:已分发文件完整落盘,未分发文件零痕迹
    assert (mini_plan_dirs.dst / "f0.txt").exists()
    assert not (mini_plan_dirs.dst / "f1.txt").exists()
    assert ex.journal_failed is False


def test_first_action_always_runs_before_cancel_check(mini_plan_dirs):
    """取消检查点的守门:首个动作恒执行(results 为空不判停)——GUI 在 job 启动
    瞬间取消时,仍能保证至少一个动作单元完整走完,进度与 journal 不空转;
    停发判定在第二个检查点生效(cancelled 置位)。"""
    ex = Executor(mini_plan_dirs.plan, mini_plan_dirs.src, mini_plan_dirs.dst, yes)
    results = ex.execute(should_cancel=lambda: True)  # 一开始就喊停
    assert len(results) == 1 and results[0].status == "copied"
    assert ex.cancelled is True


def test_rerun_identical_after_partial(mini_plan_dirs):
    """取消后重跑: 已完成文件 identical 短路(与 T3 重跑豁免协同的回归锚)。"""
    ex = Executor(mini_plan_dirs.plan, mini_plan_dirs.src, mini_plan_dirs.dst, yes)
    gate = {"stop": False}
    partial = ex.execute(should_cancel=lambda: gate["stop"],
                         progress_cb=lambda r: gate.__setitem__("stop", True))
    assert ex.cancelled and len(partial) == 1
    results2 = ex.execute()  # 重跑(取消路径不标记 executed,依赖 identical 短路)
    assert len(results2) == 3
    assert results2[0].status == "identical"      # 已完成文件短路,不重复复制
    assert results2[1].status == "copied"          # 续迁剩余文件
    assert results2[2].status == "copied"
    results3 = ex.execute()  # 全量完成后再跑:整体 identical(T3 重跑锚)
    assert all(r.status == "identical" for r in results3)


def test_before_after_action_hooks_wrap_copy(mini_plan_dirs):
    """before/after 挂点包住每个动手动作:①意图→②操作→③完成序(SKIP 不触发)。"""
    ex = Executor(mini_plan_dirs.plan, mini_plan_dirs.src, mini_plan_dirs.dst, yes)
    calls: list[tuple[str, str]] = []
    ex.execute(
        before_action=lambda a: calls.append(("intent", a.path)),
        after_action=lambda a, r: calls.append(("done", a.path)),
    )
    assert calls == [
        ("intent", "f0.txt"), ("done", "f0.txt"),
        ("intent", "f1.txt"), ("done", "f1.txt"),
        ("intent", "f2.txt"), ("done", "f2.txt"),
    ]


# ---- 批次I-W3 T3:after_action 收口(有意向动作一律回调;结果先入列) ----


def test_after_action_journal_error_keeps_result_counted(tmp_path):
    """#4b:完成记录写失败时该文件结果已入列(分发计数不失真),后续停发。"""
    from migration.journal import JournalError

    src, dst = _setup(tmp_path)
    (src / "config" / "b.toml").write_text("y=2\n", encoding="utf-8")
    plan = _plan(_action("options.txt"), _action("config/a.toml"),
                 _action("config/b.toml"))
    calls = {"n": 0}

    def after(_a, _r):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise JournalError("journal 写入失败(模拟)")

    ex = Executor(plan, src, dst, yes)
    results = ex.execute(after_action=after)
    assert ex.journal_failed is True
    assert len(results) == 2                       # 第 2 文件结果已入列,第 3 文件停发
    assert (dst / "options.txt").exists() and (dst / "config" / "a.toml").exists()
    assert not (dst / "config" / "b.toml").exists()


def test_after_action_not_called_for_skip(tmp_path):
    """#5:SKIP 动作不触发 after_action(无意图,无 journal 写)。"""
    src, dst = _setup(tmp_path)
    plan = _plan(_action("options.txt", Behavior.SKIP), _action("config/a.toml"))
    seen: list[str] = []
    results = Executor(plan, src, dst, yes).execute(
        after_action=lambda a, _r: seen.append(a.path))
    assert seen == ["config/a.toml"]               # 仅 COPY 路径出现
    assert not (dst / "options.txt").exists()      # SKIP 不写盘
    assert not any(r.failed for r in results)


def test_after_action_called_for_asked_no(tmp_path):
    """asked_no 结局已知,完成记录照写(意图不留悬账)。"""
    src, dst = _setup(tmp_path)
    plan = _plan(_action("options.txt", Behavior.ASK))
    seen: list[str] = []
    results = Executor(plan, src, dst, no).execute(
        after_action=lambda _a, r: seen.append(r.status))
    assert results[0].status == "asked_no"
    assert seen == ["asked_no"]                    # asked_no 也回调(T3 后语义)
    assert not (dst / "options.txt").exists()      # 拒绝不写盘
