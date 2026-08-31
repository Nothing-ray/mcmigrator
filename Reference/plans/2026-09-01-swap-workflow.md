# 换包工作流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `mcmig migrate`(plan 执行器)与 `mcmig swap`(换包编排)两个子命令,把 modswap-830 观察的手工流程产品化。

**Architecture:** 新 `migration/executor.py` 承载纯执行逻辑(复制/冲突备份/MD5 校验/ASK 回调),CLI 只做参数与交互包装。swap 复用现有 plan 管线(scan_mods/read_neoforge_version/Differ modpack_swap)。plan 文件增 `executed_at` 防重复执行。

**Tech Stack:** Python 3.11+ / pathlib / hashlib / rich(Confirm) / pytest

**Spec:** `Reference/specs/2026-08-31-modpack-swap-workflow-design.md`(含已确认决策 §5,实现时必须遵守)

## Global Constraints

- 注释/docstring 全中文;函数签名全类型标注;公有函数中文 docstring
- 路径一律 `pathlib.Path`;文件 I/O 显式 `encoding="utf-8"`
- `PLAN_FORMAT` 保持 2(新增字段可选,向后兼容);`TOOL_VERSION` 0.4.0 → 0.5.0
- 绝不删除目标文件;覆盖必先备份到 `<dst>/_conflict_backup/`
- migrate 可重入:源/目标 MD5 相同 → 跳过且不重复备份
- 现有 229 测试零回归;`ruff check .` 干净

---

### Task 1: plan.py 增执行状态(executed_at + mark_executed)

**Files:**
- Modify: `migration/plan.py:161-191`(MigrationPlan)
- Modify: `migration/snapshot.py:12`(TOOL_VERSION)
- Modify: `migration/__init__.py:3`、`pyproject.toml:7`
- Test: `tests/test_plan.py`

**Interfaces:**
- Produces: `MigrationPlan.executed_at: str | None = None`、`MigrationPlan.execution_summary: dict[str, int] | None = None`(copied/identical/backed_up/failed 计数)
- Produces: `MigrationPlan.mark_executed(summary: dict[str, int]) -> None`(设 executed_at=当前本地时间 isoformat + execution_summary)
- Produces: `load_plan(path: Path) -> MigrationPlan` —— 就是现有 `MigrationPlan.load`,不动;save/load 增两个可选字段的序列化(缺省 None 兼容旧 plan)

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_plan.py`:

```python
def test_plan_mark_executed_roundtrip(tmp_path):
    """mark_executed 写入执行状态,save/load 往返保留。"""
    from datetime import datetime

    plan = MigrationPlan(src="a", dst="b", generated_at="2026-09-01T00:00:00", actions=[])
    assert plan.executed_at is None
    plan.mark_executed({"copied": 3, "failed": 0})
    assert plan.executed_at is not None
    assert plan.execution_summary == {"copied": 3, "failed": 0}
    p = tmp_path / "x.plan.json"
    plan.save(p)
    loaded = MigrationPlan.load(p)
    assert loaded.executed_at == plan.executed_at
    assert loaded.execution_summary == {"copied": 3, "failed": 0}


def test_plan_load_old_format_without_executed(tmp_path):
    """旧 plan(无 executed_at 字段)仍可加载,executed_at 为 None。"""
    import json as _json

    payload = {
        "tool_version": "0.4.0", "plan_format": 2, "src": "a", "dst": "b",
        "generated_at": "t", "summary": {}, "actions": [],
    }
    p = tmp_path / "old.plan.json"
    p.write_text(_json.dumps(payload), encoding="utf-8")
    loaded = MigrationPlan.load(p)
    assert loaded.executed_at is None
    assert loaded.execution_summary is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_plan.py -k executed -v`
Expected: FAIL(`executed_at` 属性不存在 / load 后丢失)

- [ ] **Step 3: 最小实现**

`MigrationPlan` 增字段与 `payload` 两键;`load` 用 `payload.get(...)` 读;`mark_executed`:

```python
    def mark_executed(self, summary: dict[str, int]) -> None:
        """记录执行状态(防重复执行;summary 为结果计数)。"""
        self.executed_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        self.execution_summary = summary
```

- [ ] **Step 4: 跑测试确认通过** `pytest tests/test_plan.py -v`

- [ ] **Step 5: 版本 bump** TOOL_VERSION/__version__/pyproject → `0.5.0`

- [ ] **Step 6: 全量回归** `pytest -q && ruff check .`

- [ ] **Step 7: Commit** `feat(plan): 执行状态 executed_at + mark_executed;版本 0.5.0`

---

### Task 2: executor.py — 执行器核心

**Files:**
- Create: `migration/executor.py`
- Create: `tests/test_executor.py`

**Interfaces:**
- Consumes: `MigrationPlan`/`ActionRecord`/`Behavior`(Task 1 及现有)
- Produces: `FileResult` frozen dataclass:`path: str`、`status: str`(`copied`/`identical`/`backed_up` 仅并入 copied 的附加标记见下)、`failed: bool`、`error: str | None`
  - **status 集合**: `copied`(本次复制,若发生覆盖则同对象另有 `backed_up: bool = True`)/`identical`(MD5 同,跳过)/`asked_no`(用户答否)/`skipped`(非 COPY 的 action 原样记录)
- Produces: `Executor(plan, src_root: Path, dst_root: Path, ask_handler: Callable[[ActionRecord], bool]) -> None`;`execute(dry_run: bool = False) -> list[FileResult]`
- 语义(spec §2.1/§3.1): 仅 `Behavior.COPY` 动手;`Behavior.ASK` 走 `ask_handler`,True 则按 COPY 执行;目标已存在且与源 MD5 相同 → `identical`(可重入,不备份);目标存在且不同 → 先备份到 `dst_root/_conflict_backup/<rel>` 再覆盖;复制后 MD5 校验,不符 → `failed=True`;`dry_run=True` 全程零写盘,结果按「将要发生」推演

- [ ] **Step 1: 写失败测试**

创建 `tests/test_executor.py`:

```python
"""executor 模块测试:复制/冲突备份/MD5 校验/ASK/可重入/dry-run。"""

import hashlib
from pathlib import Path

from migration.executor import Executor, FileResult
from migration.plan import ActionRecord, Behavior, Origin, MigrationPlan
from migration.snapshot import FileEntry


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
    src.mkdir(); dst.mkdir()
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_executor.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'migration.executor'`

- [ ] **Step 3: 实现 executor.py**

```python
"""计划执行器:按 MigrationPlan 执行复制,含冲突备份/MD5 校验/ASK 回调。

语义见 Reference/specs/2026-08-31-modpack-swap-workflow-design.md §2.1/§3.1:
- 仅 COPY 动手;ASK 走 ask_handler
- 可重入:目标与源 MD5 相同 → identical(不备份)
- 覆盖已存在文件前先镜像备份到 <dst>/_conflict_backup/<rel>
- 复制后 MD5 校验;绝不删除任何文件
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .plan import ActionRecord, Behavior, MigrationPlan

log = logging.getLogger(__name__)

BACKUP_DIR = "_conflict_backup"


@dataclass(frozen=True)
class FileResult:
    """单文件执行结果。

    Attributes:
        path: 相对路径。
        status: copied / identical / asked_no / skipped。
        backed_up: 本次复制前是否发生了冲突备份。
        failed: 复制或校验失败。
        error: 失败原因。
    """

    path: str
    status: str
    backed_up: bool = False
    failed: bool = False
    error: str | None = None


def _md5_of(p: Path) -> str | None:
    """计算文件 MD5;文件不存在/不可读返回 None。"""
    h = hashlib.md5()
    try:
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


class Executor:
    """执行 MigrationPlan 的 COPY/ASK 动作(纯逻辑,交互由 ask_handler 注入)。"""

    def __init__(
        self,
        plan: MigrationPlan,
        src_root: Path,
        dst_root: Path,
        ask_handler: Callable[[ActionRecord], bool],
    ) -> None:
        """初始化执行器。

        Args:
            plan: 已审阅的迁移计划。
            src_root: 源根目录(按 plan.path 相对定位;保持泛化,未来可指向包内目录)。
            dst_root: 目标版本根目录。
            ask_handler: ASK 动作的回调,返回 True=迁移。
        """
        self.plan = plan
        self.src_root = src_root
        self.dst_root = dst_root
        self.ask_handler = ask_handler

    def _backup(self, rel: str, dst_file: Path) -> bool:
        """覆盖前把目标现有文件镜像备份;返回是否发生备份。"""
        bak = self.dst_root / BACKUP_DIR / rel
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dst_file, bak)
        return True

    def _copy_one(self, rel: str, dry_run: bool) -> FileResult:
        """执行单个 COPY:identical 短路 → 备份 → 复制 → 校验。"""
        src_file = self.src_root / rel
        dst_file = self.dst_root / rel
        if not src_file.is_file():
            return FileResult(rel, "copied", failed=True, error="源文件不存在")
        src_md5 = _md5_of(src_file)
        if dst_file.is_file() and _md5_of(dst_file) == src_md5:
            return FileResult(rel, "identical")
        backed_up = False
        if not dry_run:
            if dst_file.is_file():
                backed_up = self._backup(rel, dst_file)
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            if _md5_of(dst_file) != src_md5:
                return FileResult(rel, "copied", backed_up=backed_up, failed=True,
                                  error="复制后 MD5 校验不一致")
        return FileResult(rel, "copied", backed_up=backed_up)

    def execute(self, dry_run: bool = False) -> list[FileResult]:
        """执行计划,返回逐文件结果(按 plan.actions 顺序)。

        Args:
            dry_run: True 时零写盘,结果为推演。
        """
        results: list[FileResult] = []
        for action in self.plan.actions:
            if action.behavior == Behavior.COPY:
                results.append(self._copy_one(action.path, dry_run))
            elif action.behavior == Behavior.ASK:
                if self.ask_handler(action):
                    results.append(self._copy_one(action.path, dry_run))
                else:
                    results.append(FileResult(action.path, "asked_no"))
            else:
                results.append(FileResult(action.path, "skipped"))
        return results
```

- [ ] **Step 4: 跑测试确认通过** `pytest tests/test_executor.py -v`
- [ ] **Step 5: 全量回归** `pytest -q && ruff check .`
- [ ] **Step 6: Commit** `feat(executor): 计划执行器 — 复制/冲突备份/MD5 校验/ASK 回调/可重入/dry-run`

---

### Task 3: CLI — migrate 子命令

**Files:**
- Modify: `migration/cli.py`(build_parser 增子命令 + `_cmd_migrate`)
- Test: `tests/test_e2e.py`

**Interfaces:**
- Consumes: `Executor`/`FileResult`(Task 2)、`MigrationPlan.load/mark_executed`(Task 1)、`_resolve_game_root`/`_version_dir`/`_print`(现有)
- Produces: `mcmig migrate <src> <dst> [--dry-run] [--skip-ask] [--yes-ask] [-y] [--force]`;退出码 0/1/2(spec §2.1)
- Produces: `_game_running(dst_root: Path) -> bool`(尝试以 'r+b' 打开 dst 的 `usercache.json`/`options.txt`,OSError 即视为可能运行)

行为要点(spec §2.1/§3.3):
- plan 缺失 → 码 2;`executed_at` 已设 → 提示已执行,`--force` 继续;快照文件 mtime > plan 文件 mtime → 过期警告,`--force` 继续
- 执行前摘要确认(复制 N/备份预估 M),`-y` 跳过;ASK 用 rich `Confirm.ask`(显示 path+reason);`--skip-ask`/`--yes-ask` 覆盖
- 结束:成功/失败清单、失败码 1;成功后 `mark_executed` 回写 plan;打印 PCL 提醒(spec §3.3 原文模板,路径用真实 game_root)

- [ ] **Step 1: 写失败 e2e**

追加到 `tests/test_e2e.py`:

```python
def test_migrate_full_chain(tmp_path, monkeypatch, capsys):
    """scan→plan→migrate 全链路:复制/冲突备份/重入全 identical/执行状态回写。"""
    import json
    from migration.cli import main

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    for d in (src_dir, dst_dir):
        d.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (dst_dir / "options.txt").write_text("fps:60\n", encoding="utf-8")
    (src_dir / "saves").mkdir()
    (src_dir / "saves" / "w.dat").write_bytes(b"world")

    monkeypatch.chdir(tmp_path)
    assert main(["scan", "src", "--game-root", str(game_root)]) == 0
    assert main(["plan", "src", "dst", "--game-root", str(game_root)]) == 0
    capsys.readouterr()

    # 执行(-y 跳过确认;确认输入用 stdin 隔离)
    assert main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"]) == 0
    out = capsys.readouterr().out
    assert (dst_dir / "options.txt").read_text(encoding="utf-8") == "fps:120\n"
    assert (dst_dir / "_conflict_backup" / "options.txt").read_text(encoding="utf-8") == "fps:60\n"
    assert (dst_dir / "saves" / "w.dat").exists()
    assert "PCL.ini" in out and "LaunchVersionSelect" in out  # PCL 提醒文案

    # plan 已回写执行状态
    plan_file = tmp_path / ".mcmig" / "plans" / "src__dst.plan.json"
    doc = json.loads(plan_file.read_text(encoding="utf-8"))
    assert doc.get("executed_at")

    # 重跑:不加 --force 应拒绝(码 2);提示已执行
    rc = main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"])
    assert rc == 2
    assert "已执行" in capsys.readouterr().out

    # --force 重跑:全部 identical,无新增备份
    assert main(["migrate", "src", "dst", "--game-root", str(game_root), "-y", "--force"]) == 0
    out = capsys.readouterr().out
    assert "identical" in out
    backups = list((dst_dir / "_conflict_backup").rglob("*"))
    assert len([b for b in backups if b.is_file()]) == 1


def test_migrate_missing_plan_exit_2(tmp_path, monkeypatch, capsys):
    from migration.cli import main

    game_root = tmp_path / "game"
    (game_root / "versions" / "src").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    rc = main(["migrate", "src", "dst", "--game-root", str(game_root)])
    assert rc == 2
    assert "plan" in capsys.readouterr().out
```

- [ ] **Step 2: 跑测试确认失败** `pytest tests/test_e2e.py -k migrate -v`(FAIL: 无 migrate 子命令,argparse 2)

- [ ] **Step 3: 实现 `_cmd_migrate` + 子命令注册**

`build_parser` 增:

```python
    p_mig = sub.add_parser("migrate", help="执行已保存的迁移计划(先 plan 后 migrate)")
    p_mig.add_argument("src", help="源版本名")
    p_mig.add_argument("dst", help="目标版本名")
    p_mig.add_argument("--game-root", default=None, help="游戏根目录(含 versions/)")
    p_mig.add_argument("--dry-run", action="store_true", help="彩排:零写盘")
    p_mig.add_argument("--skip-ask", action="store_true", help="needs_review 全部跳过")
    p_mig.add_argument("--yes-ask", action="store_true", help="needs_review 全部迁移")
    p_mig.add_argument("-y", action="store_true", help="跳过执行前确认")
    p_mig.add_argument("--force", action="store_true", help="忽略已执行/过期防护")
```

`_cmd_migrate`(要点,imports 就近):

```python
def _cmd_migrate(args: argparse.Namespace) -> int:
    """migrate 子命令:加载 plan → 防护校验 → 确认 → Executor 执行 → 回写状态 → PCL 提醒。"""
    cwd = Path.cwd()
    game_root = _resolve_game_root(args)
    p_path = plan_path(cwd, args.src, args.dst)
    if not p_path.exists():
        _print(f"[错误] 缺少计划文件 {p_path}")
        _print("请先运行: mcmig plan <源> <目标>")
        return 2
    plan = MigrationPlan.load(p_path)
    if plan.executed_at and not args.force:
        _print(f"[错误] 该计划已于 {plan.executed_at} 执行过。重跑请加 --force"
               "(可重入:已完成文件会自动跳过)。")
        return 2
    src_snap = snapshot_path(cwd, args.src)
    dst_snap = snapshot_path(cwd, args.dst)
    stale = any(p.exists() and p.stat().st_mtime > p_path.stat().st_mtime
                for p in (src_snap, dst_snap))
    if stale and not args.force:
        _print("[错误] 快照比计划新,计划可能过期。请重跑 plan,或 --force 强制执行。")
        return 2
    src_root = _version_dir(game_root, args.src)
    dst_root = _version_dir(game_root, args.dst)
    if not src_root.is_dir() or not dst_root.is_dir():
        _print("[错误] 源/目标版本文件夹不存在")
        return 2
    if _game_running(dst_root):
        _print("[警告] 目标版本文件被占用,游戏可能仍在运行;继续可能损坏存档。")
        if not args.force:
            return 2
    # ASK 处理器
    if args.skip_ask:
        ask = lambda _a: False  # noqa: E731
    elif args.yes_ask:
        ask = lambda _a: True  # noqa: E731
    else:
        from rich.console import Console
        from rich.prompt import Confirm
        _console = Console()
        def ask(a) -> bool:  # noqa: E731
            _console.print(f"  ❓ {a.path} — {a.reason}")
            return Confirm.ask("  迁移此文件?", default=False)
    copy_n = sum(1 for a in plan.actions if a.behavior == Behavior.COPY)
    ask_n = sum(1 for a in plan.actions if a.behavior == Behavior.ASK)
    _print(f"将执行: 复制 {copy_n} / 待确认 {ask_n} / 其余跳过"
           f"{' (dry-run)' if args.dry_run else ''}")
    if not args.y and not args.dry_run:
        if not Confirm.ask("确认执行?", default=False):
            _print("已取消。")
            return 0
    results = Executor(plan, src_root, dst_root, ask).execute(dry_run=args.dry_run)
    from collections import Counter
    stat = Counter(r.status for r in results)
    failed = [r for r in results if r.failed]
    _print(f"结果: {dict(stat)};失败 {len(failed)}")
    for r in failed:
        _print(f"  [失败] {r.path}: {r.error}")
    if not args.dry_run and not failed:
        plan.mark_executed({"copied": stat.get("copied", 0),
                            "identical": stat.get("identical", 0),
                            "asked_no": stat.get("asked_no", 0), "failed": 0})
        plan.save(p_path)
        _print(f"[提醒] 迁移完成。若要让启动器默认打开新版本,需同步两处配置:")
        _print(f"  1. {game_root / 'PCL.ini'} 的 Version: 行 → 改为 {args.dst}")
        _print(f"  2. {game_root / 'PCL' / 'Setup.ini'} 的 LaunchVersionSelect: 行 → 改为 {args.dst}")
        _print("工具不代改启动器配置(改错会导致无法启动任何版本),请手动确认后修改。")
    return 1 if failed else 0
```

(顶部 imports 补: `from .executor import Executor`;`MigrationPlan`/`Confirm` 按现有 import 风格就近。)

`_game_running`:

```python
def _game_running(dst_root: Path) -> bool:
    """探测目标版本是否被运行中的游戏占用(Windows 文件锁)。"""
    for name in ("usercache.json", "options.txt"):
        p = dst_root / name
        if p.exists():
            try:
                with p.open("r+b"):
                    pass
            except OSError:
                return True
    return False
```

- [ ] **Step 4: 跑测试确认通过** `pytest tests/test_e2e.py -k migrate -v`
- [ ] **Step 5: 全量回归** `pytest -q && ruff check .`
- [ ] **Step 6: Commit** `feat(cli): migrate 子命令 — 防护校验/确认/执行/PCL 提醒`

---

### Task 4: CLI — swap 预检与装包

**Files:**
- Modify: `migration/cli.py`(`_cmd_swap` 第一/二步 + 子命令骨架)
- Test: `tests/test_e2e.py`

**Interfaces:**
- Consumes: `scan_mods`/`read_neoforge_version`/`check_version_range`(moddb 现有)
- Produces: `mcmig swap <src> <dst> <new_mods_dir> [--dry-run] [--force]`
- Produces: `_swap_preflight(dst_dir: Path, new_mods_dir: Path) -> list[str]`:返回不满足的 mod 描述行(`f"{modid} {jar} 要求 {range},目标为 {nf}"`);dst 无 `<ver>.json` 抛 `SwapError`(或返回哨兵,由调用方报码 2)
- Produces: `_swap_install(dst_mods: Path, new_mods_dir: Path, resolver: Callable[[str], bool], dry_run: bool) -> tuple[int, int, int]`:`(copied, skipped_identical, conflicted)`;conflicted 项调用 `resolver(jar_name)`(True=覆盖,False=保留目标)

- [ ] **Step 1: 写失败 e2e**

```python
def _mk_jar(path: Path, modid: str, nf_range: str | None = None) -> None:
    """合成含 neoforge.mods.toml 的 jar。"""
    import zipfile
    deps = ""
    if nf_range:
        deps = (f'[[dependencies.{modid}]]\nmodId="neoforge"\ntype="required"\n'
                f'versionRange="{nf_range}"\n')
    toml = (f'modLoader="javafml"\nloaderVersion="[1,)"\n[[mods]]\n'
            f'modId="{modid}"\nversion="1.0"\n{deps}')
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", toml)


def test_swap_preflight_blocks_incompatible(tmp_path, monkeypatch, capsys):
    """新包 mod 要求 NF≥25x、目标 233 → 默认中止列出 mod;--force 后装包。"""
    import zipfile
    from migration.cli import main

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    src_dir.mkdir(parents=True)
    dst_dir.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    # 目标版本 json:声明 NeoForge 21.1.233
    import json
    (dst_dir / "dst.json").write_text(json.dumps(
        {"arguments": {"game": ["--fml.neoforgeVersion", "21.1.233"]}}), encoding="utf-8")

    new_mods = tmp_path / "newpack" / "mods"
    _mk_jar(new_mods / "create.jar", "create", "[21.1.0,)")
    _mk_jar(new_mods / "cp_lib.jar", "cp_lib", "[21.1.248,)")

    monkeypatch.chdir(tmp_path)
    assert main(["scan", "src", "--game-root", str(game_root)]) == 0
    capsys.readouterr()

    # 默认中止(码 2),列出 cp_lib
    rc = main(["swap", "src", "dst", str(new_mods.parent), "--game-root", str(game_root)])
    assert rc == 2
    out = capsys.readouterr().out
    assert "cp_lib" in out and "21.1.248" in out
    assert not (dst_dir / "mods").exists()  # 中止时未装包
```

- [ ] **Step 2: 跑测试确认失败** `pytest tests/test_e2e.py::test_swap_preflight_blocks_incompatible -v`

- [ ] **Step 3: 实现子命令骨架 + preflight + install**

`build_parser` 增:

```python
    p_swap = sub.add_parser("swap", help="整合包替换:预检→装包→生成迁移计划")
    p_swap.add_argument("src", help="源版本名(玩家数据来源)")
    p_swap.add_argument("dst", help="目标版本名(须先用 PCL2 建好)")
    p_swap.add_argument("new_pack", help="新整合包目录(含 mods/ 子目录)")
    p_swap.add_argument("--game-root", default=None, help="游戏根目录(含 versions/)")
    p_swap.add_argument("--dry-run", action="store_true", help="彩排:装包步骤零写盘")
    p_swap.add_argument("--force", action="store_true", help="忽略兼容不满足与目标非空")
```

`_swap_preflight` / `_swap_install` / `_cmd_swap`(第一/二步;第三/四步留 Task 5,先打印「规划步骤见 plan 命令」占位会导致测试假绿——**占位必须打印明确错误并返回 3**,Task 5 替换):

```python
def _swap_preflight(dst_dir: Path, new_pack: Path) -> tuple[str | None, list[str]]:
    """预检:返回 (dst 的 NeoForge 版本或 None 错误描述, 不满足清单)。"""
    nf = read_neoforge_version(dst_dir)
    if nf is None:
        return ("目标版本缺少 <版本名>.json(无法读取 NeoForge 版本)。"
                "请先用 PCL2 安装目标 NeoForge 版本。"), []
    reg = scan_mods(new_pack)
    bad = []
    for modid in sorted(reg.modids):
        mi = reg.get(modid)
        if mi is None or mi.neoforge_range is None:
            continue
        if not check_version_range(nf, mi.neoforge_range):
            bad.append(f"  {modid}({mi.jar_filename}) 要求 {mi.neoforge_range},目标为 {nf}")
    return None, bad
```

`_swap_install`: 遍历 `new_pack/mods/*.jar`;dst 同名同 MD5 → skipped_identical;同名不同 → `resolver(name)` True 覆盖 False 保留,计入 conflicted;否则复制。dry_run 只计数。dst/mods 存在**不在新包中**的 jar → 返回前检测并在 `_cmd_swap` 中警告确认(`--force` 跳过)。

`_cmd_swap` 流程: preflight 错误 → 码 2;bad 非空 → 红字列出,`--force` 才继续;装包(resolver 默认 rich Confirm「保留目标为默认」);完成后进入 Task 5 的规划步骤。

- [ ] **Step 4: 跑测试确认通过** `pytest tests/test_e2e.py -k swap -v`
- [ ] **Step 5: 全量回归** `pytest -q && ruff check .`
- [ ] **Step 6: Commit** `feat(cli): swap 子命令 — 兼容预检与装包(同名冲突询问)`

---

### Task 5: CLI — swap 规划编排与摘要

**Files:**
- Modify: `migration/cli.py`(替换 Task 4 的占位第三步)
- Test: `tests/test_e2e.py`

**Interfaces:**
- Consumes: `_cmd_plan` 的管线(scan/diff/plan;若直接调用子命令函数则用 argparse.Namespace 构造参数,**优先抽公共函数 `_run_plan_pipeline(src, dst, game_root, mcmig_dir, modpack_swap=True) -> tuple[MigrationPlan, list]`** 供 `_cmd_plan` 与 `_cmd_swap` 共用,避免复制粘贴)
- Produces: swap 结束打印分类计数 + 下一步提示行:`审阅 .mcmig/plans/<src>__<dst>.plan.json 后运行: mcmig migrate <src> <dst>`

- [ ] **Step 1: 写失败 e2e**

```python
def test_swap_full_flow_generates_plan(tmp_path, monkeypatch, capsys):
    """--force 全流程: 装包 → 自动生成含 mod_swapped_out 的 plan → 提示 migrate。"""
    import json
    from migration.cli import main

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    src_dir.mkdir(parents=True); dst_dir.mkdir(parents=True)
    (src_dir / "mods").mkdir()
    (src_dir / "mods" / "old-pack.jar").write_bytes(b"old")
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (dst_dir / "dst.json").write_text(json.dumps(
        {"arguments": {"game": ["--fml.neoforgeVersion", "21.1.248"]}}), encoding="utf-8")

    new_mods = tmp_path / "newpack" / "mods"
    _mk_jar(new_mods / "cp_lib.jar", "cp_lib", "[21.1.248,)")

    monkeypatch.chdir(tmp_path)
    assert main(["scan", "src", "--game-root", str(game_root)]) == 0
    capsys.readouterr()

    rc = main(["swap", "src", "dst", str(new_mods.parent), "--game-root", str(game_root), "--force"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mcmig migrate" in out          # 下一步提示
    assert (dst_dir / "mods" / "cp_lib.jar").exists()  # 装包完成
    plan_file = tmp_path / ".mcmig" / "plans" / "src__dst.plan.json"
    doc = json.loads(plan_file.read_text(encoding="utf-8"))
    acts = {a["path"]: a for a in doc["actions"]}
    assert acts["mods/old-pack.jar"]["origin"] == "mod_swapped_out"  # 换包排除生效
    assert acts["options.txt"]["behavior"] == "copy"
```

- [ ] **Step 2: 跑测试确认失败** `pytest tests/test_e2e.py::test_swap_full_flow_generates_plan -v`

- [ ] **Step 3: 实现**

1. 从 `_cmd_plan` 抽出 `_run_plan_pipeline(cwd, src, dst, game_root, args, modpack_swap) -> tuple[MigrationPlan, list]`(返回 plan + compat_warnings;内部完成 scan dst 快照、orphan 规则、ruleset、diff、planner、plan.save)——**重构后 `_cmd_plan` 行为零变化,现有测试必须全绿**
2. `_cmd_swap` 第三步: 调用 `_version_dir` scan dst → `_run_plan_pipeline(modpack_swap=True)`;第四步: 打印 `plan.summary()` 计数 + migrate 提示

- [ ] **Step 4: 跑测试确认通过** `pytest tests/test_e2e.py -k swap -v`
- [ ] **Step 5: 全量回归** `pytest -q && ruff check .`
- [ ] **Step 6: Commit** `feat(cli): swap 规划编排 — 共用 plan 管线,modpack_swap 内置`

---

### Task 6: README 文档 + 验收收尾

**Files:**
- Modify: `README.zh-CN.md` / `README.en.md`(「快速开始」或命令表增 `migrate`/`swap` 两行说明 + 换包一节)
- Test: 无新测试;跑全量

- [ ] **Step 1: README 增补**(zh 为主,en 同构翻译)

命令表追加:

```markdown
| `mcmig migrate <src> <dst>` | 执行已保存的迁移计划(先 plan 后 migrate;覆盖自动备份到 `_conflict_backup/`) |
| `mcmig swap <src> <dst> <新包目录>` | 整合包替换:兼容预检→装包→生成换包迁移计划 |
```

「整合包替换」小节末尾追加:

```markdown
完整换包流程:
```
mcmig swap <旧版本> <新版本> <新包目录>   # 预检+装包+出计划(会因 NeoForge 不满足而中止,按提示处理)
mcmig migrate <旧版本> <新版本>           # 审阅计划后执行复制
```
新版本文件夹请先用 PCL2 安装好对应 NeoForge。migrate 可重入(中断后重跑自动续传)。
```

- [ ] **Step 2: 全量回归** `pytest -q && ruff check .`
- [ ] **Step 3: Commit** `docs: README 增 migrate/swap 用法`

---

## Self-Review Checklist(实现完成后核对)

1. spec §2.1 全部语义有测试:可重入(identical)/冲突备份镜像/ASK 三态/MD5 校验失败/退出码 0-1-2/过期与已执行防护
2. spec §2.2: NF 不满足默认中止+列出/`--force`/同名不同内容询问(默认保留)/目标非空警告/dry-run
3. spec §3.3 PCL 提醒含两个真实路径
4. 决策 §5 四条全部体现(可重入/只提醒/询问保留/执行器接口泛化——src_root 无 versions/ 语义)
5. 现有 229 测试零回归;`_cmd_plan` 重构后行为不变
