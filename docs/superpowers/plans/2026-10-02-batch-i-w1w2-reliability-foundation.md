# 批次I-W1W2 实现计划:可靠性基座(T1-T7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地批次I spec(v4)的 W1(上下文与防护)+W2(任务模型)——实例态路径契约、共享执行预检、审阅有效性、job 状态衔接协议、跨进程实例锁、恢复与取消(journal write-ahead)。

**Architecture:** 核心全部下沉 `migration/` 纯库层(新增 preflight.py/review.py/instlock.py/journal.py 四个模块,pipeline 重导出保持 spec 命名);gui/server.py 只做翻译(job 模型重构为 状态机+广播);CLI 改薄壳消费同一管线,输出文案保持逐字对拍。零新运行依赖(instlock 用 ctypes)。

**Tech Stack:** Python 3.11+ / pytest / fastapi TestClient / ctypes(Win32 named mutex)。

**Spec:** `Reference/specs/2026-10-01-batch-i-gui-evolution-design.md`(v4,commit 9ef1daa)——执行者两个文件都读;本计划从 spec §3.1/§3.2/§3.3/§4.1/§4.2/§4.3 立论。

**执行前置:** 批次H 已实现并在 main(0.11.0);测试基线数以执行时 `pytest --collect-only` 实际值为准(spec 撰写时为 481)。

## Global Constraints

- 注释/docstring 全中文;公有函数中文 docstring;类型提示全覆盖;路径一律 pathlib;文件 UTF-8 无 BOM。
- 每任务退出条件: **全量测试绿 + `ruff check .` 零告警**(ruff 钉 0.15.20)。
- 每任务一个中文 conventional commit;提交前 `pytest` 全量通过。
- `migration/data/*.yaml` 本波**不改**;若万一改动,必须重跑 `python tools/gen_manifest.py` 并提交 manifest。
- 版本号**不动**(保持批次H 后的 0.11.0;1.0.0 在批次I W4 T15 收口)。
- 行为变更白名单(超出须先回 spec 改文档再改代码):
  1. workdir 实例态位置变更为 `<game_root>/.mcmig/`(T1,spec §3.1);
  2. `build_plan` 计划保存失败上抛 `PlanPersistError`、不再吞(T3,spec §3.3);
  3. CLI `--force` 语义=「已执行重跑+快照过期+疑似占用」三项显式决策,目录缺失永不可强制(T2,spec §3.2);
  4. 疑似占用文案改为「疑似被占用」措辞(T2,spec §3.2;涉及 test_cli 既有断言同步更新);
  5. 重跑(`rerun_executed`)跳过目标状态校验,依赖 identical 短路与 journal(T3;T3 内含 spec 一行补注)。
- 新增 GUI 文案一律进 `migration/gui/STRINGS.py` 集中字典。
- 依赖**零新增**。
- 涉及 workdir 布局的既有测试(test_workdir/test_gui_server 中绿色 slug 断言)属 T1 预期破坏面,更新时在测试 docstring 标注「批次I-T1 路径契约 v3」。

## Review Focus

1. **绿色模式路径错位(C1 回归点)**: `mcmig_dir` 误传 workdir.root 会让绿色模式错位到 `data/snapshots/`——T1 保持绿色 smoke 测试并新增「实例态=game_root/.mcmig」断言(test_workdir.py::test_green_instance_paths_anchor_to_game_root)。
2. **job 上下文漂移**: 运行中 job 在改 game_root 后仍用启动时路径、新 job 用新路径——T1 上下文定格测试(test_gui_server.py::test_running_job_context_frozen_across_config_change)。
3. **审新执旧/重入误伤**: plan 保存失败后 migrate 必须拒绝;identical 重跑不得被目标状态校验拦截——T3 双测试(test_gui_server.py::test_migrate_rejected_when_plan_not_persisted / test_pipeline.py::test_rerun_skips_target_state_check)。
4. **终态漏接**: 「查状态→任务完成→订阅 SSE」竞态与慢订阅者反压——T4(test_gui_server.py::test_subscribe_after_completion_replays_history / test_slow_subscriber_dropped_without_backpressure)。
5. **锁交叉/死锁/别名**: A→B 与 B→C 并发、junction 同键、第二把锁失败释放第一把——T5(test_instlock.py 三用例)。
6. **journal 崩溃窗口**: 「文件替换成功→完成登记前崩溃」重启呈现「待核对」而非「未执行」——T6(test_journal.py::test_crash_between_copy_and_completion_marks_unverified)。

---

### Task 1: 实例态路径契约 v3 + 首跑欢迎态 + job 上下文定格

**Files:**
- Modify: `migration/workdir.py`(WorkDir 字段重构 + resolve 语义)
- Modify: `migration/gui/server.py`(create_app 未配置态 / POST /api/config 重建 / InstanceCtx 定格)
- Modify: `migration/pipeline.py`(build_plan 增 rules_dir 参数与规则目录选择 helper)
- Modify: `migration/cli.py:392-408`(plan 命令 mcmig_dir/rules 接线)
- Test: `tests/test_workdir.py`、`tests/test_gui_server.py`、`tests/test_cli.py`

**Interfaces:**
- Consumes: `resolve_workdir(game_root)`(现状)、`find_snapshot(data_dir, legacy_dir, version)`(pipeline.py:94)、`build_ruleset(versions, *, exclude, include, rule_files, mcmig_dir, ...)`(cli.py:170,规则目录=`mcmig_dir/rules.yaml`)。
- Produces(T2-T6 与 W3 依赖):
```python
# workdir.py
@dataclass(frozen=True)
class WorkDir:
    root: Path                              # 全局根(绿=exe/data;兼容=cwd/.mcmig)
    config: Path                            # 全局配置(绿=config.toml;兼容=config.yaml)
    green: bool
    game_root: Path | None = None           # None=未配置(首跑欢迎态,不再抛错)
    snapshots: Path | None = None           # <game_root>/.mcmig/snapshots
    plans: Path | None = None
    rules: Path | None = None               # <game_root>/.mcmig/rules.yaml
    jobs: Path | None = None                # T6 journal 目录
    locks: Path | None = None               # T5 锁登记目录(POSIX 回退用)
    legacy_snapshots: Path | None = None    # 旧绿色 exe/data/<slug>/snapshots(只读回退)
def resolve_workdir(game_root: Path | None = None) -> WorkDir  # 绿色未配置→返回 game_root=None 的布局,不抛
# pipeline.py
def build_plan(..., rules_dir: Path | None = None, ...) -> tuple[MigrationPlan, list[CompatWarning], list[ModPair]]
    # rules_dir=None → 取 data_dir;规则目录选择: 新位置存在→用之(旧位置并存则 warning 忽略);
    # 仅旧位置存在→用旧位置+warning 建议迁移;均无→新位置
# gui/server.py
@dataclass(frozen=True)
class InstanceCtx:
    game_root: Path; snapshots: Path; plans: Path; rules: Path; legacy_snapshots: Path | None
def _instance_ctx(wdir: WorkDir) -> InstanceCtx   # 未配置时抛 ApiError(422, err_no_game_root)
```

- [ ] **Step 1: 写失败测试(workdir 契约)**

```python
# tests/test_workdir.py 追加(文件头部注释标注:批次I-T1 路径契约 v3)
def test_instance_paths_anchor_to_game_root(tmp_path, monkeypatch):
    """实例态(快照/计划/规则/jobs/locks)统一锚定 <game_root>/.mcmig,绿/源码两模式同址(spec §3.1)。"""
    import migration.workdir as wd
    monkeypatch.setattr(wd, "_is_frozen", lambda: True)
    monkeypatch.setattr(wd, "_exe_dir", lambda: tmp_path / "exe")
    (tmp_path / "exe").mkdir()
    game = tmp_path / "game"; game.mkdir()
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
    monkeypatch.chdir(tmp_path / "cwd"); (tmp_path / "cwd").mkdir()
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
    game = tmp_path / "game"; game.mkdir()
    old = tmp_path / "exe" / "data" / game.name / "snapshots"; old.mkdir(parents=True)
    w = wd.resolve_workdir(game_root=game)
    assert w.legacy_snapshots == old
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_workdir.py -k "instance_paths or unconfigured or legacy_slug" -v`
Expected: FAIL(现有 WorkDir 无 game_root/jobs/locks 字段、未配置抛 WorkdirError)。

- [ ] **Step 3: 最小实现 workdir.py**

重构 `WorkDir`(字段见 Interfaces)、`_resolve_green`/`_resolve_compat`:

```python
def _instance_layout(game_root: Path) -> dict[str, Path]:
    """实例态目录布局(spec §3.1:统一 <game_root>/.mcmig,绿/源码同址)。"""
    base = game_root / ".mcmig"
    return {"snapshots": base / "snapshots", "plans": base / "plans",
            "rules": base / "rules.yaml", "jobs": base / "jobs", "locks": base / "locks"}

def _resolve_green(game_root: Path | None) -> WorkDir:
    root = _exe_dir() / "data"
    try:
        _ensure_writable(root)
    except OSError as e:
        raise WorkdirError(what="软件目录不可写",
                           why="软件目录不可写,请把 mcmig 移动到可写的文件夹后重试") from e
    if game_root is None:
        game_root = _load_game_root_toml(root / "config.toml")
    if game_root is None:
        # 首跑欢迎态:不抛错,实例态字段为 None,由 GUI/CLI 引导配置(spec §3.1)
        return WorkDir(root=root, config=root / "config.toml", green=True)
    inst = _instance_layout(game_root)
    legacy = root / game_root.name / "snapshots"
    return WorkDir(root=root, config=root / "config.toml", green=True,
                   game_root=game_root, legacy_snapshots=legacy if legacy.is_dir() else None, **inst)

def _resolve_compat(game_root: Path | None) -> WorkDir:
    root = Path.cwd() / ".mcmig"
    if game_root is None:  # 兼容模式:config.yaml 现读
        game_root = _load_game_root_yaml(root / "config.yaml")
    base = WorkDir(root=root, config=root / "config.yaml", green=False)
    if game_root is None:
        return base
    inst = _instance_layout(game_root)
    legacy = root / "snapshots"
    return replace(base, game_root=game_root,
                   legacy_snapshots=legacy if legacy.is_dir() and legacy != inst["snapshots"] else None, **inst)
```

同时更新模块 docstring 的布局说明。**随后全量跑 `pytest tests/test_workdir.py`**:既有 slug 断言测试按白名单更新(改断言为 game_root 锚定,docstring 标注批次I-T1)。

- [ ] **Step 4: server 首跑欢迎态与上下文定格(失败测试先行)**

```python
# tests/test_gui_server.py 追加
def test_welcome_state_no_game_root(tmp_path, monkeypatch):
    """未配置 game_root:GET / 200、GET /api/config 返回 null、/api/plan 422 三段式(spec §3.1)。"""
    import migration.workdir as wd
    monkeypatch.setattr(wd, "_is_frozen", lambda: False)
    monkeypatch.chdir(tmp_path)  # 无 config.yaml → 未配置
    client = TestClient(create_app(), base_url="http://127.0.0.1")
    assert client.get("/").status_code == 200
    assert client.get("/api/config").json() == {"game_root": None}
    resp = client.post("/api/plan", json={"src": "a", "dst": "b"})
    assert resp.status_code == 422 and resp.json()["what"]

def test_config_post_rebuilds_context(tmp_path, monkeypatch):
    """POST /api/config 后:实例态落新根(game_root/.mcmig),plan job 写新位置(spec §3.1)。"""
    game, client = _make_client_unconfigured(tmp_path, monkeypatch)
    game = tmp_path / "game"
    for name, text in (("src", "fps:120\n"), ("dst", "fps:60\n")):
        d = game / "versions" / name; d.mkdir(parents=True)
        (d / "options.txt").write_text(text, encoding="utf-8")
    assert client.post("/api/config", json={"game_root": str(game)}).json()["ok"] is True
    job = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    events = _wait_job_done(client, job)
    assert events[-1]["type"] == "done"
    assert (game / ".mcmig" / "snapshots" / "src.snapshot.json").is_file()
    assert (game / ".mcmig" / "plans" / "src__dst.plan.json").is_file()

def test_running_job_context_frozen_across_config_change(tmp_path, monkeypatch):
    """运行中 job 用启动时捕获的实例上下文;改 game_root 只影响后续 job(spec §3.1)。"""
    game, client = _make_client(tmp_path, monkeypatch)   # 既有 helper
    game2 = tmp_path / "game2"
    (game2 / "versions").mkdir(parents=True)
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 1.5)  # 放大窗口
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    client.post("/api/config", json={"game_root": str(game2)})          # 运行中切换
    events = _wait_job_done(client, job_id)
    assert events[-1]["type"] == "done"
    # 旧 job 的快照落在旧根,新根未被动过
    assert (game / ".mcmig" / "snapshots" / "src.snapshot.json").is_file()
    assert not (game2 / ".mcmig").exists()
```

(`_make_client_unconfigured`:新 helper,monkeypatch 非冻结+chdir 空目录+`create_app()`。)

- [ ] **Step 5: server 实现要点**

- `create_app(workdir=None)`: `app.state.wdir = workdir if workdir is not None else resolve_workdir()`;所有 `_game_root()` 改读 `app.state.wdir.game_root`(None→ApiError 422 err_no_game_root,复用既有 STRINGS 键);实例路径消费点(`_run_plan_job`/`_run_migrate_job` 的 `workdir.snapshots/plans`)改走 `_instance_ctx(wdir)` 捕获的 `InstanceCtx`(job 启动时一次性定格,闭包持有)。
- `POST /api/config`: save_game_root 后 `app.state.wdir = resolve_workdir(new_root)`(重建;运行中 job 已持有旧 ctx 不受影响)。
- `_run_plan_job` 的 `mcmig_dir=workdir.snapshots.parent` 改为 `mcmig_dir=ctx.game_root / ".mcmig"`,并传 `legacy_dir`?——build_plan 现签名无 legacy_dir;GUI 侧快照回退:plan job 在 scan 前先 `find_snapshot(data, legacy, ver)` 检查,若仅 legacy 命中则 scan 前打印迁移提示事件(实现:`job.emit({"type":"notice","text":...})`,新增 notice 事件型,进 STRINGS)。本步可只接新路径,legacy 提示为 notice 事件(§4 事件契约增补一型,页面忽略未知型不炸——原生 JS 分发按 type switch,默认忽略)。
- 同步更新 test_gui_server 既有用例中绿色 slug 路径断言(白名单)。

- [ ] **Step 6: CLI 规则接线(失败测试先行)**

```python
# tests/test_cli.py 追加
def test_plan_rules_anchored_to_game_root(tmp_path, monkeypatch, caplog):
    """用户规则读取 <game_root>/.mcmig/rules.yaml(而非 cwd/.mcmig)(spec §3.1,cli.py:402 接线)。"""
    game = _mini_game(tmp_path)             # 既有 helper:versions/src+dst 各带 options.txt
    rr = game / ".mcmig"; rr.mkdir(parents=True)
    (rr / "rules.yaml").write_text(
        "- match: options.txt\ndecide: never\nreason: 测试锚定\nsource: user\n",
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)             # cwd/.mcmig 不存在 → 只能来自 game_root 侧
    rc, out = _run_cli(tmp_path, ["plan", "src", "dst", "--game-root", str(game)])
    assert rc == 0
    assert "to_migrate=0" in out or "must_migrate=0" in out   # never 生效=规则被读取
```

(按 test_cli 既有 `_run_cli`/`_mini_game` helper 实际名调整;断言「options.txt 落 never」以 PlanReporter 输出桶计数表达。)

- [ ] **Step 7: CLI/pipeline 实现**

- `pipeline.build_plan` 增 `rules_dir: Path | None = None`;内部规则目录选择:

```python
def _rules_dir(data_dir: Path, legacy_dir: Path | None) -> tuple[Path, list[str]]:
    """规则目录选择:新位置优先,旧位置只读回退,并存时明确提示不暗混(spec §3.1)。"""
    new, old = data_dir / "rules.yaml", (legacy_dir / "rules.yaml" if legacy_dir else None)
    notices: list[str] = []
    if new.exists() and old and old.exists():
        notices.append(f"[提示] 检测到旧规则 {old},已忽略(并存时以 {new} 为准),建议删除旧文件")
        return data_dir, notices
    if not new.exists() and old and old.exists():
        notices.append(f"[提示] 使用旧布局规则 {old},建议迁移至 {new}")
        return legacy_dir, notices   # type: ignore[return-value]
    return data_dir, notices
```

build_plan 内 `build_ruleset(..., mcmig_dir=chosen)`(chosen 来自 `_rules_dir(data, legacy)`);CLI 调用点 cli.py:402: `mcmig_dir=cwd / ".mcmig"` 保持(作 legacy),新增 `data_dir=game_root / ".mcmig"` 已有——rules 走 `rules_dir` 缺省=data_dir ✓ 零参数改动即可生效;`_cmd_swap` 同型核对。
- [ ] **Step 8: 全量回归**

Run: `pytest -q && ruff check .`
Expected: 全绿零告警(既有 slug 断言用例已按白名单更新)。

- [ ] **Step 9: Commit**

```bash
git add migration/workdir.py migration/gui/server.py migration/pipeline.py migration/cli.py tests/
git commit -m "feat(w1): 实例态路径契约统一 game_root/.mcmig+首跑欢迎态+job 上下文定格 (batchI-T1)"
```

---

### Task 2: 共享执行预检 preflight_execute + 磁盘预检增强

**Files:**
- Create: `migration/preflight.py`
- Modify: `migration/pipeline.py`(re-export + execute_migration 磁盘口径)
- Modify: `migration/cli.py`(_cmd_migrate 薄壳化;_game_running 迁移)
- Modify: `migration/gui/server.py`(_run_migrate_job 前置预检)
- Test: `tests/test_preflight.py`(新)、`tests/test_pipeline.py`、`tests/test_cli.py`

**Interfaces:**
- Consumes: T1 的 WorkDir/InstanceCtx;`find_snapshot`(pipeline.py:94)。
- Produces(T3/T5/GUI 依赖):
```python
# migration/preflight.py(pipeline 重导出,spec 命名 pipeline.preflight_execute 成立)
@dataclass(frozen=True)
class ExecutionDecisions:
    rerun_executed: bool = False        # 已执行计划的明确重跑
    accept_stale: bool = False          # 快照过期仍执行
    accept_maybe_running: bool = False  # 疑似占用独立决策
@dataclass(frozen=True)
class PreflightBlocker:
    code: str   # plan_executed|snapshot_stale|version_dir_missing|game_maybe_running
    message: str
@dataclass(frozen=True)
class PreflightWarning:
    code: str; message: str
def probe_maybe_running(dst_root: Path) -> bool
    # 自 cli._game_running 迁移:r+b 试开 usercache.json/options.txt;局限见 docstring
def preflight_execute(plan: MigrationPlan, game_root: Path, src: str, dst: str, *,
                      decisions: ExecutionDecisions | None = None,
                      data_dir: Path | None = None, legacy_dir: Path | None = None
                      ) -> tuple[list[PreflightBlocker], list[PreflightWarning]]
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_preflight.py(新)
"""preflight 共享执行预检:四道防护×决策矩阵(spec §3.2 阻断分类)。"""
from migration.plan import MigrationPlan, Behavior, Origin
from migration.preflight import ExecutionDecisions, PreflightBlocker, preflight_execute, probe_maybe_running

def _plan(executed: bool = False) -> MigrationPlan:
    from datetime import datetime, timezone
    p = MigrationPlan(src="src", dst="dst",
                      generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      actions=[])
    if executed:
        p.mark_executed({})
    return p

def test_executed_plan_blocks_without_decision(tmp_path):
    (tmp_path / "versions" / "src").mkdir(parents=True); (tmp_path / "versions" / "dst").mkdir(parents=True)
    blockers, _ = preflight_execute(_plan(executed=True), tmp_path, "src", "dst")
    assert [b.code for b in blockers] == ["plan_executed"]

def test_executed_plan_rerun_decision_downgrades_to_warning(tmp_path):
    for n in ("src", "dst"): (tmp_path / "versions" / n).mkdir(parents=True)
    blockers, warnings = preflight_execute(
        _plan(executed=True), tmp_path, "src", "dst",
        decisions=ExecutionDecisions(rerun_executed=True))
    assert not blockers and [w.code for w in warnings] == ["plan_executed"]

def test_version_dir_missing_never_forceable(tmp_path):
    (tmp_path / "versions" / "src").mkdir(parents=True)   # dst 缺失
    for dec in (None, ExecutionDecisions(rerun_executed=True, accept_stale=True, accept_maybe_running=True)):
        blockers, _ = preflight_execute(_plan(), tmp_path, "src", "dst", decisions=dec)
        assert "version_dir_missing" in [b.code for b in blockers]   # 无降级通道(spec §3.2)
```

(另补 snapshot_stale 与 game_maybe_running 两族同型用例:快照 mtime>plan mtime 构造、dst 写 usercache.json 并以独占句柄模拟占用。)

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_preflight.py -v` → FAIL: `No module named 'migration.preflight'`。

- [ ] **Step 3: 实现 preflight.py**

四道检查按 spec §3.2 表;文案常量(与 CLI 现输出逐字一致,game_maybe_running 用新「疑似」措辞):

```python
MSG_PLAN_EXECUTED = "该计划已执行(时间 {executed_at})。重跑请加 --force(可重入:已完成文件会自动跳过)。"
MSG_SNAPSHOT_STALE = "快照比计划新,计划可能过期。请重跑 plan,或 --force 强制执行。"
MSG_VERSION_DIR_MISSING = "源/目标版本文件夹不存在"
MSG_MAYBE_RUNNING = "目标版本文件疑似被占用,游戏可能仍在运行;继续可能损坏存档。请先退出源与目标实例。"

def probe_maybe_running(dst_root: Path) -> bool:
    """疑似占用探测:尝试读写打开 usercache.json/options.txt。

    局限(刻意明示,不称「检测运行中游戏」): 权限错误可能误报;文件未锁不证明
    游戏未运行。调用方文案一律「疑似占用」(spec §3.2)。
    """
```

`preflight_execute` 逻辑: dirs 缺失→blocker(永不可强制);executed→blocker/warning 按 rerun_executed;stale→按 accept_stale(快照定位 `find_snapshot(data_dir or game_root/".mcmig", legacy_dir, ver)`,存在才比 mtime,与 cli.py:620-627 语义一致);maybe_running→按 accept_maybe_running。pipeline.py 头部 `from .preflight import ExecutionDecisions, PreflightBlocker, PreflightWarning, preflight_execute, probe_maybe_running  # noqa: F401`(重导出)。

- [ ] **Step 4: CLI 接线对拍(先改测试预期再改实现)**

`_cmd_migrate`(cli.py:608-636)防护段替换为:

```python
    blockers, warnings = preflight_execute(
        plan, game_root, args.src, args.dst,
        decisions=ExecutionDecisions(rerun_executed=args.force, accept_stale=args.force,
                                     accept_maybe_running=args.force),
        data_dir=data_dir, legacy_dir=cwd / ".mcmig")
    for b in blockers:                       # 不可强制项与可强制项统一经此出口
        _print(f"[错误] {b.message}"); return 2
    for w in warnings:
        _print_err(f"[警告] {w.message}")
```

test_cli 既有 migrate 防护用例:文案断言除「被占用→疑似被占用」外逐字不变(白名单 4);新增用例 `test_migrate_force_maps_three_decisions`(--force 后 executed/stale/占用均放行、目录缺失仍拒)。删除 cli 本地 `_game_running`,引用处改 `probe_maybe_running`。

- [ ] **Step 5: GUI 接线**

`_run_migrate_job` 在加载 plan 后、执行前:

```python
        blockers, warnings = preflight_execute(plan, ctx.game_root, src, dst,
                                               data_dir=ctx.game_root / ".mcmig",
                                               legacy_dir=ctx.legacy_snapshots.parent if ctx.legacy_snapshots else None)
        if blockers:
            job.emit(_error_event(STRINGS["job_err_preflight.what"],
                                  STRINGS["job_err_preflight.why"],
                                  {"blockers": [{"code": b.code, "message": b.message} for b in blockers]}))
            return
        for w in warnings:
            job.emit({"type": "warning", "code": w.code, "message": w.message})  # 事件契约增补 warning 型
```

STRINGS 增 `job_err_preflight.what/why`;test_gui_server 增:已执行计划→error 事件含 blockers[0].code=="plan_executed";warnings 转事件。

- [ ] **Step 6: 磁盘预检含 ASK(pipeline.py:331)**

```python
# tests/test_pipeline.py 追加
def test_disk_check_counts_confirmed_ask(tmp_path, monkeypatch):
    """磁盘预检计入 ask_yes 命中的 ASK 动作(spec §3.2,修 pipeline.py:331 只计 COPY)。"""
```
(构造 COPY+ASK(yes) 各一大伪文件,monkeypatch `shutil.disk_usage` 返回小余量→DiskSpaceError;对照只计 COPY 时通过、计入 ASK 后拒绝。)实现: `needed = sum(a.src_size or 0 for a in plan.actions if a.behavior == Behavior.COPY or (a.behavior == Behavior.ASK and a.path in ask_yes))`。

- [ ] **Step 7: 全量回归 + Commit**

Run: `pytest -q && ruff check .` → 全绿。

```bash
git add migration/preflight.py migration/pipeline.py migration/cli.py migration/gui/server.py migration/gui/STRINGS.py tests/
git commit -m "feat(w1): 共享执行预检 preflight_execute+阻断分类决策+磁盘预检含 ASK (batchI-T2)"
```

---

### Task 3: 审阅有效性——plan_fingerprint / 审阅守卫 / 审阅状态校验 / persisted 阻断

**Files:**
- Create: `migration/review.py`
- Modify: `migration/plan.py`(MigrationPlan 增 review 字段 + PlanPersistError)
- Modify: `migration/pipeline.py`(build_plan 签发守卫+保存失败上抛)
- Modify: `migration/gui/server.py`(done 带 plan_id/persisted;migrate 校验)
- Modify: `Reference/specs/2026-10-01-batch-i-gui-evolution-design.md`(§3.3 一行补注:重跑跳过状态校验)
- Test: `tests/test_review.py`(新)、`tests/test_pipeline.py`、`tests/test_gui_server.py`

**Interfaces:**
- Consumes: T2 `PreflightBlocker`;`Snapshot`/`FileEntry`(snapshot.py)。
- Produces(T5 持锁重验/GUI/W3 依赖):
```python
# migration/review.py
def file_sha256(path: Path) -> str
def plan_fingerprint(plan: MigrationPlan) -> str
    # 规范化 payload(=save payload 剔除 executed_at/execution_summary/tool_version)→ sha256
def rules_fingerprint(paths: Sequence[Path]) -> str      # 各来源路径(存在者)路径+内容 → sha256
def issue_review(*, game_root: Path, snapshot_paths: dict[str, Path],
                 rule_sources: Sequence[Path], modpack_swap: bool) -> dict
    # {"instance": str(game_root.resolve()), "snapshots": {ver: file_sha256},
    #  "rules": rules_fingerprint(...), "mode": bool}
def validate_review(plan: MigrationPlan, *, game_root: Path, snapshot_paths: dict[str, Path],
                    rule_sources: Sequence[Path]) -> list[PreflightBlocker]
    # codes: review_missing|instance_mismatch|snapshot_changed|rules_changed
def check_action_states(actions: Sequence[ActionRecord], src_snap: Snapshot, dst_snap: Snapshot,
                        src_root: Path, dst_root: Path) -> list[PreflightBlocker]
    # codes: target_state_changed|source_state_changed;md5 有则比 md5,否则 size+mtime;
    # 「目标不存在」是记录状态;快照一致≠磁盘未变(spec §3.3 审阅状态校验契约)
# migration/plan.py
class PlanPersistError(OSError): ...        # 计划持久化失败(不再吞,spec §3.3)
@dataclass MigrationPlan: ... review: dict | None = None   # save payload 含 "review";load 兼容缺省
# migration/gui/server.py
#   plan job done 事件: {"type":"done", ..., "plan_id": "<hex>", "persisted": true|false}
#   MigrateRequest 增 plan_id: str | None = None;校验失败→error 事件 code=guards_plan_mismatch|review_*
```

- [ ] **Step 1: 写失败测试(指纹与守卫)**

```python
# tests/test_review.py(新)
"""审阅有效性:计划身份 vs 执行前置条件分离;审阅状态校验(spec §3.3)。"""
def test_fingerprint_excludes_runtime_fields(built_plan):
    """mark_executed 改写运行结果字段后指纹不变(运行记录≠动作定义)。"""
    fp1 = plan_fingerprint(built_plan)
    built_plan.mark_executed({"copied": 1})
    assert plan_fingerprint(built_plan) == fp1

def test_rescan_invalidates_review(built_layout, tmp_path):
    """计划 JSON 不变+重扫(快照文件内容变)→ review 守卫阻断(spec 验收用例)。"""
    plan = built_layout.plan                    # 已含 review
    blockers = validate_review(plan, game_root=built_layout.game,
                               snapshot_paths=built_layout.snapshot_paths,
                               rule_sources=built_layout.rule_sources)
    assert blockers == []
    (built_layout.dst_dir / "options.txt").write_text("fps:30\n", encoding="utf-8")
    scan_version(built_layout.game, "dst", built_layout.data / "snapshots")  # 重扫落盘
    codes = [b.code for b in validate_review(plan, game_root=built_layout.game,
              snapshot_paths=built_layout.snapshot_paths, rule_sources=built_layout.rule_sources)]
    assert "snapshot_changed" in codes

def test_target_state_changed_blocks(built_layout):
    """审阅后目标被改(未重扫)→ check_action_states 阻断;完整性校验不能替代审阅状态校验。"""
    (built_layout.dst_dir / "options.txt").write_text("tampered\n", encoding="utf-8")
    codes = [b.code for b in check_action_states(
        built_layout.plan.actions, built_layout.src_snap, built_layout.dst_snap,
        built_layout.src_dir, built_layout.dst_dir)]
    assert "target_state_changed" in codes

def test_target_created_after_review_blocks(built_layout):
    """审阅时目标不存在、执行前出现→阻断(「不存在」是被记录状态)。"""
    rel = "saves/new/world_level.dat"           # 视 built_layout 夹具而定,取一个 dst 缺失的动作路径
    (built_layout.dst_dir / rel).parent.mkdir(parents=True, exist_ok=True)
    (built_layout.dst_dir / rel).write_bytes(b"x" * 16)
    codes = [b.code for b in check_action_states(...同上...)]
    assert "target_state_changed" in codes

def test_weak_entry_size_only_change_blocks(built_layout):
    """size 代理条目(size+mtime 弱检)在其范围内变化→阻断;同 size 同 mtime 的内容替换不承诺发现。"""
```

(built_layout 夹具:conftest 增 `built_plan_layout(tmp_path)`——build_mini_version 两版本+build_plan 产出 plan/src_snap/dst_snap/路径组,复用既有 conftest helper。)

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_review.py -v` → FAIL: `No module named 'migration.review'`。

- [ ] **Step 3: 实现 review.py + plan.py 字段**

- `plan_fingerprint`: 以 `plan.save` 的 payload 字典为规范化基础,剔除 `executed_at`/`execution_summary`/`tool_version`,`json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":"))` 后 sha256。
- `MigrationPlan.review: dict | None = None`;save payload 增 `"review": self.review`;`from_dict` 读 `d.get("review")`(PLAN_FORMAT 不动,加法式)。
- `check_action_states`: 对 behavior∈{COPY, ASK} 的动作,取 `src_snap_index[rel]`/`dst_snap_index[rel]`(缺索引=状态变化);当前态 `stat`+`md5_of`(FileEntry.md5 非 None 时才算,否则 size+mtime);两侧各自比对,产出 target/source_state_changed,`details` 进 blocker message(路径+期望/实际)。
- **重跑豁免不在此层**:由调用方(pipeline/GUI/CLI)在 `rerun_executed` 决策下跳过 `check_action_states`(见 Step 4)。

- [ ] **Step 4: build_plan 签发与上抛(pipeline.py)**

```python
    # 规划完成后、save 前:签发审阅守卫(spec §3.3)
    review = issue_review(game_root=game_root, snapshot_paths={
        src: data / "snapshots" / f"{src}.snapshot.json",
        dst: data / "snapshots" / f"{dst}.snapshot.json"},
        rule_sources=[chosen_rules_dir / "rules.yaml"], modpack_swap=modpack_swap)
    plan.review = review
    if save:
        try:
            plan.save(plans_dir / f"{src}__{dst}.plan.json")
        except OSError as e:
            raise PlanPersistError(f"plan 文件写入失败: {e}") from e   # 不再吞(spec §3.3)
```

- `pipeline.execute_migration` 增可选参 `validate_states: bool = True`——内部在预检后调用 `check_action_states`(行为集中一处,CLI/GUI 平级);`rerun_executed` 场景调用方传 False 并依赖 identical 短路。tests/test_pipeline 增 `test_rerun_skips_target_state_check`(执行后 force 重跑不被拦截,Review Focus 3)。
- CLI `_cmd_plan`: 捕获 `PlanPersistError` → `[错误] ...` + return 2(白名单 2);`_cmd_migrate`: loaded plan 无 review → `_print_err("[提示] 计划缺少审阅守卫(旧版生成),建议重跑 plan 启用保护")` 继续执行(渐进采用,不破坏既有 CLI 测试)。
- spec 补注(docs 小修,与代码同任务提交):

```markdown
- 执行状态校验的适用边界: 重跑(`rerun_executed` 明确决策)跳过目标状态校验——此时依赖
  identical 短路与 job journal;状态校验保护的是「首次执行前的静默漂移」(spec §3.3 v4 补注)
```

- [ ] **Step 5: GUI 流(失败测试先行)**

```python
# tests/test_gui_server.py 追加
def test_plan_done_carries_plan_id_and_persisted(tmp_path, monkeypatch):
    ...  # plan job done 事件含 plan_id(64 hex)且 persisted=True

def test_migrate_rejected_when_plan_not_persisted(tmp_path, monkeypatch):
    """保存失败(注入)→ persisted=False → migrate 拒绝,封死「审新执旧」(spec §3.3)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(server_module, "write_json_atomic", _boom_write)   # 或经 plan.save 注入
    job = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    events = _wait_job_done(client, job)
    assert events[-1]["type"] == "error" and "plan" in events[-1].get("details", {}).get("what", "") or \
           events[-1]["type"] == "done" and events[-1]["persisted"] is False   # 按 Step 5 实现口径二选一定死
    monkeypatch.undo()  # 恢复写盘
    ...  # 后续 migrate 该 src/dst → error 事件(guards_plan_mismatch / persisted_false)

def test_migrate_plan_id_mismatch_blocks(tmp_path, monkeypatch):
    ...  # 提交旧 plan_id(先 plan 一次取 id → 再 plan 一次取新 id → 用旧 id migrate)→ error

def test_migrate_target_drift_blocks(tmp_path, monkeypatch):
    ...  # plan 成功后手改 dst/options.txt → migrate(带正确 plan_id)→ error 事件含 target_state_changed
```

实现: `_run_plan_job` 捕 `PlanPersistError` → done 事件 `persisted:false` + error 三段式(一次事件,页面禁执行);成功路径 done 增 `plan_id`。`MigrateRequest.plan_id` 必填(旧客户端兼容: None→error 提示重新生成);`_run_migrate_job`: load → `plan_fingerprint(plan) != req.plan_id` → guards_plan_mismatch;`validate_review` blockers → error;首跑(`not decisions.rerun_executed`)→ `check_action_states` blockers → error;随后 T2 preflight → execute **同一 plan 对象**(现状已单次加载,确认无二次按名读取)。

- [ ] **Step 6: 全量回归 + Commit**

Run: `pytest -q && ruff check .` → 全绿。

```bash
git add migration/review.py migration/plan.py migration/pipeline.py migration/cli.py migration/gui/server.py Reference/specs/2026-10-01-batch-i-gui-evolution-design.md tests/
git commit -m "feat(w1): 审阅有效性——plan_fingerprint/审阅守卫/审阅状态校验/persisted 阻断 (batchI-T3)"
```

---

### Task 4: job 状态机 + GET /api/jobs/{id} + SSE 广播衔接协议

**Files:**
- Modify: `migration/gui/server.py`(Job/JobStore/_sse_gen 重构 + 状态端点)
- Test: `tests/test_gui_server.py`

**Interfaces:**
- Consumes: 现状 Job/JobStore/job 线程体。
- Produces(T6/W3 依赖):
```python
# Job 增补属性(对外契约)
#   job.status: "running"|"cancelling"|"succeeded"|"partial_failed"|"failed"|"cancelled"
#   job.revision: int(单调事件序号;每事件+1,事件带 "seq")
#   job.can_cancel: bool(kind 决定:T4 先行 migrate=True, plan=False)
# GET /api/jobs/{id} → {"status","kind","phase","revision","progress":{"index","total"},
#                        "summary": {...}|None, "results": [...]|None, "error": {...}|None}
# GET /api/jobs/{id}/events?last_event_id=N(或 Last-Event-ID 头)
#   → SSE: id: <seq> 行 + data: {...};历史截断时先发 {"type":"reset"}(客户端须重读状态)
# 事件契约增补: 每事件带 "seq";新增 "warning" 型(T2)与 "reset" 型
# 常量(测试 monkeypatch 锚点): _EVENT_HISTORY_LIMIT=500, _SUBSCRIBER_QUEUE_LIMIT=256
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_gui_server.py 追加
def test_status_endpoint_returns_terminal_state(tmp_path, monkeypatch):
    """GET /api/jobs/{id}: 终态+summary+results(含备份位置与失败明细,非仅计数)(spec §4.1)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    job_id = client.post("/api/migrate", json={"src": "src", "dst": "dst"}).json()["job_id"]
    _wait_job_done(client, job_id)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "succeeded"
    assert body["results"][0]["path"] == "options.txt"
    assert "backup" in json.dumps(body) or body["results"][0].get("backed_up") is not None

def test_subscribe_after_completion_replays_history(tmp_path, monkeypatch):
    """终态漏接封堵: 任务完成后带 last_event_id=0 订阅 → 全量重放+终态(spec §4.1 衔接协议)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    job_id = client.post("/api/migrate", json={"src": "src", "dst": "dst"}).json()["job_id"]
    _wait_job_done(client, job_id)          # 先完成(不订阅)
    with client.stream("GET", f"/api/jobs/{job_id}/events?last_event_id=0") as resp:
        events = [json.loads(l[6:]) for l in resp.iter_lines() if l.startswith("data: ")]
    assert [e["type"] for e in events][-1] == "done"
    assert all(e.get("seq", 0) == i + 1 for i, e in enumerate(events))   # 序号连续

def test_two_subscribers_each_receive_all_events(tmp_path, monkeypatch):
    """广播: 两个并发订阅者各自收全量(修单队列分摊)(spec §4.1)。"""
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 1.0)
    game, client = _make_client(tmp_path, monkeypatch)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    collected: list[list[dict]] = [[] for _ in range(2)]
    with client.stream("GET", f"/api/jobs/{job_id}/events") as r1, \
         client.stream("GET", f"/api/jobs/{job_id}/events") as r2:
        for src, out in ((r1, collected[0]), (r2, collected[1])):
            for line in src.iter_lines():
                if line.startswith("data: "): out.append(json.loads(line[6:]))
                if out and out[-1].get("type") in ("done", "error"): break
    assert [e["type"] for e in collected[0]] == [e["type"] for e in collected[1]]

def test_slow_subscriber_dropped_without_backpressure(tmp_path, monkeypatch):
    """慢订阅者队列有界: 满则丢弃该订阅者并要求重读状态,不影响 job(不反压复制线程)。"""
    monkeypatch.setattr(server_module, "_SUBSCRIBER_QUEUE_LIMIT", 2)
    ...  # 不消费第二个订阅者的流,job 正常 done;状态端点可查询终态

def test_history_truncated_emits_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "_EVENT_HISTORY_LIMIT", 2)
    ...  # last_event_id=0 订阅 → 首事件 {"type":"reset"},其后为保留的历史尾
```

(TestClient 双流并发受线程模型限制,`test_two_subscribers` 若不稳,改为先后两次订阅+历史重放等价断言并注明。)

- [ ] **Step 2: 跑测试确认失败** → FAIL(无状态端点/单队列消费)。

- [ ] **Step 3: 实现**

- `Job`: `status/can_cancel/revision` 属性;`_history: list[dict]`(截断至 `_EVENT_HISTORY_LIMIT`,记 `_floor_seq`);`_subs: list[queue.Queue]`(maxsize=`_SUBSCRIBER_QUEUE_LIMIT`);`emit` 统一:`self.revision += 1; ev = {**event, "seq": self.revision}; self._history.append(ev)(截断); for q in list(self._subs): q.put_nowait(ev) except Full: self._subs.remove(q)`。
- 终态判定 helper `_finish(job, results=None, failed=False)`:`done=True`;status: error 事件→failed;results 有 failed→partial_failed;否则 succeeded(cancelling 中断→cancelled 归 T6);`_run_plan_job/_run_migrate_job` 的 finally 调用替换 `job.done = True`。
- `_sse_gen(job, last_seq: int)`:`last_seq < job._floor_seq` → 先 yield reset;重放 `_history` 中 `seq > last_seq` 的条目(SSE `id:` 行=seq);再活消费本订阅者队列;done/error 后收尾。端点读 `request.headers.get("last-event-id")` 或 query 参数。
- 状态端点: 从 job 派生 phase(最后一个 phase 事件)/progress(最后一个 file 事件)/summary(migrate done 载荷)/results(migrate job 存 `job.results = results`)/error。
- 404 既有;`can_cancel` 按 kind 映射。

- [ ] **Step 4: 全量回归(重点: 既有 SSE 序列用例兼容——事件增 "seq" 字段不破坏既有断言)+ Commit**

```bash
pytest -q && ruff check .
git add migration/gui/server.py tests/
git commit -m "feat(w2): job 状态机+GET /api/jobs/{id}+SSE 广播衔接协议(revision/Last-Event-ID/截断 reset) (batchI-T4)"
```

---

### Task 5: 跨进程实例锁(双锁排序/持锁重验/junction 归一/abandoned 标记)

**Files:**
- Create: `migration/instlock.py`
- Modify: `migration/cli.py`(scan/plan/migrate/swap 命令包裹)
- Modify: `migration/gui/server.py`(job 体包裹+持锁后重验)
- Test: `tests/test_instlock.py`(新)

**Interfaces:**
- Consumes: T3 `validate_review`;T1 `InstanceCtx`。
- Produces(T6/W3 swap 依赖):
```python
# migration/instlock.py
class InstanceLockError(Exception):
    # what/why/details(含 holders: 争用实例键列表;Windows named mutex 拿不到对端 pid,
    # details 说明「另一 mcmig 进程正在操作该实例」)
@contextmanager
def instance_locks(game_root: Path, *versions: str, timeout: float = 5.0
                   ) -> Iterator[dict[str, list[str]]]:
    """对涉及的源/目标实例按规范化身份排序获取排他锁(spec §4.2)。

    yields: {"abandoned": [实例键...]}(abandoned mutex=前持有者异常退出,须查 journal,
             不当干净任务);第二把获取失败→释放第一把并抛 InstanceLockError。
    实现: Windows=named mutex(ctypes CreateMutexW,名=Local\\mcmig-inst-<sha1(键)[:16]>);
          POSIX 回退=锁文件 O_EXCL(开发/测试用)。
    """
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_instlock.py(新)
"""实例锁:双锁排序/junction 归一/第二锁失败回滚/跨进程互斥/abandoned 标记(spec §4.2)。"""
import subprocess, sys, textwrap

def _holder_script(lock_game, versions, hold_seconds):
    return textwrap.dedent(f"""
        import time
        from migration.instlock import instance_locks
        with instance_locks({str(lock_game)!r}, {", ".join(repr(v) for v in versions)}):
            print("HELD", flush=True)
            time.sleep({hold_seconds})
    """)

def test_cross_process_second_acquire_fails(tmp_path):
    """子进程持 A→B 锁期间,主进程 A→B 获取失败(跨进程互斥,A→B 与 B→C 见下一条)。"""
    p = subprocess.Popen([sys.executable, "-c", _holder_script(tmp_path, ("A", "B"), 3.0)])
    assert p.stdout.readline().strip() == b"HELD"          # 同步:子进程已持锁
    with pytest.raises(InstanceLockError):
        with instance_locks(tmp_path, "A", "B", timeout=0.5):
            pass
    p.kill(); p.wait()

def test_ab_cross_bc_second_lock_conflict(tmp_path):
    """甲 A→B 写 B、乙 B→C 读 B:乙第二把锁(B)被甲持有→失败(spec §4.2 交叉场景)。"""
    p = subprocess.Popen([sys.executable, "-c", _holder_script(tmp_path, ("A", "B"), 3.0)])
    assert p.stdout.readline().strip() == b"HELD"
    with pytest.raises(InstanceLockError):
        with instance_locks(tmp_path, "B", "C", timeout=0.5):   # B 与甲冲突
            pass
    p.kill(); p.wait()

def test_second_lock_failure_releases_first(tmp_path):
    """第二把锁失败→第一把已释放(之后可单独重新获取第一把)(spec §4.2/评审 P2-3)。"""
    p = subprocess.Popen([sys.executable, "-c", _holder_script(tmp_path, ("B",), 3.0)])
    assert p.stdout.readline().strip() == b"HELD"
    with pytest.raises(InstanceLockError):
        with instance_locks(tmp_path, "A", "B", timeout=0.5):
            pass
    with instance_locks(tmp_path, "A", timeout=1.0):           # A 未被遗留占用
        pass
    p.kill(); p.wait()

def test_same_instance_dedups_single_key(tmp_path):
    """src==dst(同实例)→键去重为单锁(评审 P2-3)。"""
    with instance_locks(tmp_path, "A", "A") as info:
        assert info["abandoned"] == []

def test_junction_alias_same_key(tmp_path):
    """junction 指向同实例→锁键相同,不能绕过(Windows mklink /J)。"""
    real = tmp_path / "versions" / "A"; real.mkdir(parents=True)
    link = tmp_path / "versions" / "Alias"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)], check=True,
                   capture_output=True)
    p = subprocess.Popen([sys.executable, "-c",
                          _holder_script_vers(tmp_path / "versions" / "Alias", 3.0)])
    assert p.stdout.readline().strip() == b"HELD"
    with pytest.raises(InstanceLockError):
        with instance_locks(tmp_path / "versions" / "Alias", "A", timeout=0.5):  # 别名↔真名同键
            pass
    p.kill(); p.wait()

def test_abandoned_mutex_flagged(tmp_path):
    """持有者异常退出→主进程 WAIT_ABANDONED 获取成功且 abandoned 标记(不当干净任务)。"""
    p = subprocess.Popen([sys.executable, "-c", _holder_script(tmp_path, ("A",), 30.0)])
    assert p.stdout.readline().strip() == b"HELD"
    p.kill(); p.wait()                                       # 不释放退出
    with instance_locks(tmp_path, "A", timeout=2.0) as info:
        assert "A" in info["abandoned"] or info["abandoned"]  # 键为 resolve 后路径
```

(`_holder_script_vers`:以版本目录全路径为键的变体 helper;instance_locks 键=`(game_root/"versions"/v).resolve()` 字符串,junction resolve 后与真名同键。)

- [ ] **Step 2: 跑测试确认失败** → FAIL: `No module named 'migration.instlock'`。

- [ ] **Step 3: 实现 instlock.py**

ctypes 骨架(全中文注释;`WaitForSingleObject` 返回 `WAIT_ABANDONED(0x80)` 归入获取成功+abandoned 标记;`WAIT_TIMEOUT/WAIT_FAILED`→InstanceLockError):

```python
import ctypes, hashlib
from contextlib import contextmanager
from pathlib import Path

def _lock_keys(game_root: Path, versions: Sequence[str]) -> list[str]:
    """规范化锁键:resolve 消 junction/别名,排序去重(spec §4.2;同实例去重)。"""
    return sorted({str((Path(game_root) / "versions" / v).resolve()) for v in versions})

@contextmanager
def instance_locks(game_root, *versions, timeout=5.0):
    keys = _lock_keys(game_root, versions)
    handles, abandoned = [], []
    try:
        for key in keys:
            h = _acquire(key, timeout)          # (handle, abandoned_flag);失败抛 InstanceLockError
            handles.append(h); ...
    except InstanceLockError:
        raise                                    # finally 释放已获取的第一把
    finally:
        for h in handles: _release(h)
    yield {"abandoned": abandoned_keys}
```

(Windows 走 `ctypes.windll.kernel32.CreateMutexW/WaitForSingleObject/ReleaseMutex/CloseHandle`;非 Windows 用 `_locks` 目录 O_CREAT|O_EXCL 锁文件+内容 pid,陈旧判定 pid 不存活。锁名 `Local\mcmig-inst-<sha1[:16]}`——用户会话内隔离即可。)

- [ ] **Step 4: CLI/GUI 接线(持锁重验)**

- CLI: `_cmd_scan`(单版本)、`_cmd_plan`、`_cmd_migrate`、`_cmd_swap` 主体包裹 `with instance_locks(game_root, src, dst) as lockinfo:`;lockinfo abandoned 非空→`_print_err("[警告] 检测到上次异常退出的实例锁,请核对 <game_root>/.mcmig/jobs/ 下的中断记录")`(T6 前 journal 未建,提示路径即占位,T6 落地后为真实指引)。
- GUI: `_run_plan_job/_run_migrate_job` 在预检/执行序列**最外层**包裹;获取失败→error 事件(STRINGS 增 `job_err_instlock.what/why`);**持锁后重验**:`_run_migrate_job` 在锁内重跑 T3 `validate_review`(封「检查-取锁-执行」竞争窗,spec §4.2 时序)。
- test_gui_server 增:同 app 第二个 migrate(单任务锁 409 已有)+ instlock 层由单进程复入自锁?——注意: **同一进程内 CLI 与 GUI 各自 instance_locks 互斥**,但同一进程两个 job 因 JobStore 单锁先行 409,不会自锁;测试补 `test_migrate_job_acquires_instance_lock`(monkeypatch instlock 记录调用参数)。

- [ ] **Step 5: 全量回归 + Commit**

Run: `pytest -q && ruff check .` → 全绿(锁超时 5s 不拖慢常规用例——无争用时获取即时)。

```bash
git add migration/instlock.py migration/cli.py migration/gui/server.py migration/gui/STRINGS.py tests/
git commit -m "feat(w2): 跨进程实例锁——双键排序/junction 归一/失败回滚释放/abandoned 标记+持锁重验 (batchI-T5)"
```

---

### Task 6: 恢复与取消——journal write-ahead + can_cancel + interrupted + 退出入口

**Files:**
- Create: `migration/journal.py`
- Modify: `migration/executor.py`(取消检查点+动作回调)
- Modify: `migration/pipeline.py`(execute_migration 透传)
- Modify: `migration/gui/server.py`(cancel 端点/cancelling 态/interrupted 列表/shutdown)
- Modify: `migration/gui/index.html`(取消按钮/中断横幅/退出入口,最小组)
- Test: `tests/test_journal.py`(新)、`tests/test_executor.py`、`tests/test_gui_server.py`

**Interfaces:**
- Consumes: T4 状态机;T1 `InstanceCtx.jobs`;T5 abandoned 提示。
- Produces(W3 swap journal 复用):
```python
# migration/journal.py
class JournalError(Exception): ...            # journal 写失败(执行侧须停发)
class JobJournal:
    def __init__(self, journal_dir: Path, job_id: str, kind: str) -> None
        # 存储 <journal_dir>/<job_id>.json,write_json_atomic 原子重写(spec §4.3)
    def record_intent(self, rel: str, detail: dict) -> None       # ①持久化意图(write-ahead)
    def record_completion(self, rel: str) -> None                 # ③持久化完成
    def unfinished(self) -> list[dict]                            # 有意图无完成(待核对)
    def finish(self) -> None                                      # 收尾标记(正常终态)
def scan_interrupted(jobs_dir: Path) -> list[dict]
    # 启动期: 未收尾且有 unfinished 的 job → [{"job_id","kind","entries":[...]}](待核对)
# executor.py
def execute(self, dry_run=False, progress_cb=None, *,
            should_cancel: Callable[[], bool] | None = None,
            before_action: Callable[[ActionRecord], None] | None = None,
            after_action: Callable[[ActionRecord, FileResult], None] | None = None
            ) -> list[FileResult]
    # before/after 包住每个动手动作(意图→操作→完成 的①③锚点);should_cancel 在动作间检查,
    # 命中→停止分发,self.cancelled=True;before_action 抛 JournalError→停止分发(写失败停发)
# pipeline.execute_migration 透传三参
# gui/server.py
#   POST /api/jobs/{id}/cancel → 202 {"status":"cancelling"}(can_cancel=False→405 三段式;终态→409)
#   GET /api/jobs/interrupted → {"items":[...]}(启动期扫描 workdir.jobs)
#   POST /api/shutdown → 空闲 200 {"ok":true}(置 app.state.shutdown_requested);job 运行→409
```

- [ ] **Step 1: 写失败测试(journal 崩溃窗口)**

```python
# tests/test_journal.py(新)
"""journal write-ahead 三段序与崩溃窗口(spec §4.3)。"""
def test_crash_between_copy_and_completion_marks_unverified(tmp_path):
    """替换成功→登记前崩溃:重启后「状态待核对」而非「未执行」(Review Focus 6)。"""
    j = JobJournal(tmp_path, "job1", "migrate")
    j.record_intent("options.txt", {"op": "copy", "backup": "_conflict_backup/options.txt"})
    # 模拟: 文件已落盘(②),完成记录(③)未写即崩溃——不做 record_completion
    j2 = JobJournal(tmp_path, "job1", "migrate")            # 重启重开
    assert [e["rel"] for e in j2.unfinished()] == ["options.txt"]
    assert scan_interrupted(tmp_path)[0]["entries"][0]["rel"] == "options.txt"

def test_journal_write_failure_stops_dispatch(tmp_path, monkeypatch, mini_plan):
    """意图写失败→停止分发后续写入动作(spec §4.3)。"""
    import migration.journal as jm
    calls = {"n": 0}
    def _flaky(path, payload):
        calls["n"] += 1
        if calls["n"] > 1:              # 第一条意图成功,之后全部失败
            raise OSError("disk")
        jm.write_json_atomic.__wrapped__(path, payload)   # 或直接绕行真实现
    monkeypatch.setattr(jm, "write_json_atomic", _flaky)
    j = JobJournal(tmp_path, "job2", "migrate")
    ...  # executor 带 before_action=j.record_intent 跑 mini_plan(3 文件)
         # 断言: 仅第一个文件被复制,后续未分发,结果 partial
```

- [ ] **Step 2: 跑测试确认失败** → FAIL: `No module named 'migration.journal'`。

- [ ] **Step 3: 实现 journal.py + executor 挂点**

- `JobJournal`: 内态 `{"kind", "started_at", "finished": False, "entries": {rel: {"detail", "completed": bool}}}`;`record_intent/record_completion/finish` 各自 `write_json_atomic` 原子重写;`unfinished` 过滤 `completed=False`。
- `Executor.execute` 循环改造:

```python
        for action in self.plan.actions:
            if should_cancel is not None and results and should_cancel():
                self.cancelled = True
                break                                   # 安全边界:当前单元已完成(spec §4.3)
            if action.behavior in (Behavior.COPY, Behavior.ASK):
                if before_action is not None:
                    before_action(action)               # ①意图(write-ahead;抛错→停发)
            ...  # 原判定/复制(②操作)
            if after_action is not None and result.status != "asked_no":
                after_action(action, result)            # ③完成
            results.append(result); cb(result)
```

`before_action` 抛 `JournalError`→ 捕获、记 `self.journal_failed=True`、break(与 cancel 同型停发);`__init__` 增 `self.cancelled = False; self.journal_failed = False`。`pipeline.execute_migration` 增同名三参透传。

- [ ] **Step 4: GUI cancel/interrupted/shutdown(失败测试先行)**

```python
# tests/test_gui_server.py 追加
def test_cancel_migrate_flows_to_cancelled(tmp_path, monkeypatch):
    """取消:202+cancelling 中间态 → 「正在停止」 → 终态 cancelled+部分完成清单(spec §4.3)。"""
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 5.0)  # 拉长窗口
    game, client = _make_client(tmp_path, monkeypatch, files=20)        # helper 扩:多文件版本
    job_id = client.post("/api/migrate", json={"src": "src", "dst": "dst"}).json()["job_id"]
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 202
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelling"
    events = _wait_job_done(client, job_id)
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"
    assert events[-1]["type"] == "done" and events[-1].get("cancelled") is True

def test_cancel_plan_job_unsupported(tmp_path, monkeypatch):
    """扫描/plan 阶段不可取消:405 三段式,无假按钮语义(spec §4.3)。"""
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 2.0)
    game, client = _make_client(tmp_path, monkeypatch)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 405 and resp.json()["what"]

def test_interrupted_listing_from_journal(tmp_path, monkeypatch):
    """启动期扫描 journal: 未收尾 job 列为 interrupted(待核对),≠cancelled(spec §4.3)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    (game / ".mcmig" / "jobs").mkdir(parents=True)
    JobJournal(game / ".mcmig" / "jobs", "deadbeef", "migrate").record_intent(
        "options.txt", {"op": "copy"})
    items = client.get("/api/jobs/interrupted").json()["items"]
    assert items[0]["job_id"] == "deadbeef" and items[0]["entries"][0]["rel"] == "options.txt"

def test_shutdown_endpoint_guards_on_busy(tmp_path, monkeypatch):
    """退出入口: 空闲 200 置停机标志;job 运行 409(spec §4.3,uvicorn 侧接线归 T10)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    assert client.post("/api/shutdown").json()["ok"] is True
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 2.0)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    assert client.post("/api/shutdown").status_code == 409
    _wait_job_done(client, job_id)
```

实现: `_run_migrate_job` 建 journal、接 `before_action/after_action/should_cancel`(should_cancel=查 job.status=="cancelling");cancelling→终态 cancelled(done 事件 `cancelled:true`+已完成/未完成清单);STRINGS 增 cancel/interrupted/shutdown 文案键;`create_app` 启动期(有 game_root 时)扫描 `scan_interrupted` 存 `app.state.interrupted`。index.html 最小组:③执行页加「取消」按钮(POST cancel→显示「正在停止…」→轮询状态)、顶部 interrupted 横幅(fetch on load)、页脚「退出服务」按钮(POST /api/shutdown)——样式内联最简,W3 T7 重构 IA 时统一。

- [ ] **Step 5: executor 取消单测**

```python
# tests/test_executor.py 追加
def test_cancel_checkpoint_stops_between_files(mini_plan_dirs):
    """should_cancel 在第 1 个文件后命中→停发,返回部分结果且 executor.cancelled。"""
    ex = Executor(mini_plan_dirs.plan, mini_plan_dirs.src, mini_plan_dirs.dst, ask_yes=set())
    gate = {"stop": False}
    results = ex.execute(should_cancel=lambda: gate["stop"],
                         progress_cb=lambda r: gate.__setitem__("stop", True))
    assert ex.cancelled and len(results) == 1

def test_rerun_identical_after_partial(mini_plan_dirs):
    """取消后重跑: 已完成文件 identical 短路(与 T3 重跑豁免协同的回归锚)。"""
```

- [ ] **Step 6: 全量回归 + Commit**

Run: `pytest -q && ruff check .` → 全绿。

```bash
git add migration/journal.py migration/executor.py migration/pipeline.py migration/gui/server.py migration/gui/STRINGS.py migration/gui/index.html tests/
git commit -m "feat(w2): 恢复与取消——journal write-ahead+取消检查点+interrupted 判定+退出入口 (batchI-T6)"
```

---

### Task 7: W1W2 收口(README 路径契约 + 手测清单 + 回归确认)

**Files:**
- Modify: `README.zh-CN.md`、`README.en.md`(绿色软件目录布局/数据与卸载小节→路径契约 v3)
- Modify: `tests/gui-manual-checklist.md`(增补 W1W2 手测项)
- Test: 无新增(全量回归即验收)

**Interfaces:**
- Consumes: T1-T6 全部。
- Produces: 文档与实际行为一致;W3/W4 计划的前置。

- [ ] **Step 1: README 双语更新**

「绿色软件目录布局」小节替换为:

```markdown
mcmig/(exe 所在文件夹)              <游戏根>/.mcmig/(实例态,CLI/GUI 互通)
├── mcmig-gui.exe / mcmig.exe        ├── snapshots/ plans/ rules.yaml
└── data/                            ├── jobs/(任务 journal,中断待核对)
    └── config.toml(仅全局配置)      ├── locks/ 与 backups/(批次I W3 起)
```

附迁移说明: 旧 `exe/data/<游戏名>/` 与 `cwd/.mcmig/` 实例态为只读回退,建议整体迁移;两处规则并存以新位置为准。英文版同义。

- [ ] **Step 2: 手测清单增补(tests/gui-manual-checklist.md)**

新增 6 条: 首跑欢迎态(删 data/config.toml 后启动)/改根目录后运行中任务不漂移/取消 migrate 全流程/cancel 对 plan 无入口/中断 journal 横幅/退出入口忙碌 409。每条含操作步骤与期望。

- [ ] **Step 3: 全量回归与基线记录**

Run: `pytest -q && ruff check .`;记录测试总数(预期较 481 增约 +55~75,以实际为准,写入 commit body)。

- [ ] **Step 4: Commit**

```bash
git add README.zh-CN.md README.en.md tests/gui-manual-checklist.md
git commit -m "docs(w1w2): README 双语路径契约 v3+手测清单增补+批次I W1W2 收口 (batchI-T7)"
```

---

## 计划自审记录

1. **Spec 覆盖**: §3.1(T1)/§3.2(T2)/§3.3(T3,含一行 spec 补注)/§4.1(T4)/§4.2(T5)/§4.3(T6)→ 全部有任务;spec §3.3「执行对象同一性」在 T3 Step 5 落实(单次加载+校验);spec §4.2「锁覆盖 tmp 清理/写盘/结果登记」由 CLI/GUI 在锁内跑完整命令体覆盖。
2. **占位符扫描**: 无 TBD;两处「按实现口径二选一定死」(T3 Step 5 persisted 事件形态、TestClient 双流稳定性备注)已给出可执行的两案与判据,非悬空。
3. **类型一致性**: `ExecutionDecisions/PreflightBlocker/PreflightWarning`(T2)在 T3/T5 复用同名;`InstanceCtx`(T1)字段与 T2/T3/T5/T6 消费点一致;`instance_locks` yields dict 形态在 T5/T6 一致;executor 三新参在 T6 定义、pipeline 透传同名。
4. **Review Focus ↔ 测试归属**: 六条分别锚定 T1×2/T3×2/T4/T5/T6 各失败测试,无空挂。
