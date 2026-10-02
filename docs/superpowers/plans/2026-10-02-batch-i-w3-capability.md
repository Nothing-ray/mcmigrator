# 批次I-W3 实现计划:能力补全+可靠性收口(T1-T10)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地批次I spec(v4)W3 能力四项(审阅页 IA 重构/swap 下沉+两阶段/swap 页面/pywebview 窗口壳),同时收口 W1W2 两轮复审沿挂的全部可靠性条目(终态原子提交/慢订阅者溢出/中断恢复模型/POSIX flock/页面事件恢复协议/执行上下文传到底)。

**Architecture:** 可靠性先行:核心侧把「守卫+预检+状态校验」合为 `pipeline.precheck_execution` 单点(CLI/GUI 平级消费,封死取位不同构再犯);Job 广播底座做终态原子提交与订阅者溢出信号;journal 存储转 JSONL 增量追加,中断判定改「未收尾+实例锁探针活性」;页面重构为 Presentation Model(SSE/GET 统一消费+Last-Event-ID 自动重连+刷新游标)。能力侧:build_plan 附带审阅摘要数据,swap 编排自 cli.py 下沉 pipeline(覆盖备份+journal),GUI 两阶段 swap 端点+页面,pywebview 壳经可选依赖包独立窗口。

**Tech Stack:** Python 3.11+ / pytest / fastapi TestClient / fcntl(POSIX)/ pywebview(新可选依赖,仅窗口壳)。

**Spec:** `Reference/specs/2026-10-01-batch-i-gui-evolution-design.md`(v4)——执行者两个文件都读;本计划从 spec §5.1/§5.2/§5.3/§5.4(W3 能力)与 §4.1/§4.3(沿挂收口)立论。

**执行前置:** main=`3698356`(0.11.2),测试基线 **621**(执行时以 `pytest --collect-only` 实际值为准)。

## Global Constraints

- 注释/docstring 全中文;公有函数中文 docstring;类型提示全覆盖;路径一律 pathlib;文件 UTF-8 无 BOM。
- 每任务退出条件: **全量测试绿 + `python -m ruff check migration/ tests/` 零告警**(ruff 钉 0.15.20;主仓有用户未跟踪草稿目录,勿跑全仓 ruff)。
- 每任务一个中文 conventional commit;提交前 `pytest` 全量通过(venv: `.venv/Scripts/python.exe -m pytest`)。
- `migration/data/*.yaml` 本波**不改**;若万一改动,必须重跑 `python tools/gen_manifest.py` 并提交 manifest。
- 版本号:T1-T9 期间不动;T10 收口统一升 **0.12.0**(能力批次,延续每批次 minor 递增先例;1.0.0 仍在 W4 T15)。
- 新增 GUI 文案一律进 `migration/gui/STRINGS.py`(server 侧)或页面 `PAGE_STRINGS` 对象(T5 建立后)。
- 依赖新增**仅一项**: `pywebview>=5.0`(T9,spec §5.3 已批准;必须可选导入——未安装时降级浏览器模式,核心与测试零依赖它)。
- 行为变更白名单(超出须先回 spec 改文档再改代码;W1W2 白名单 1-5 见 W1W2 计划,继续有效):
  1. GUI/CLI 执行前置检查合并为 `precheck_execution` 单点后,**多阻断并存时的首错优先序统一为「守卫→预检→状态校验」**(GUI 原为守卫先、CLI 原为预检先;单阻断用例的输出不变)(T1);
  2. `GET /api/jobs/{id}/events?last_event_id=<非数字>` 按 0 处理(原先经 pydantic int 校验返回 422,与 docstring「非法值按 0」不一致)(T2);
  3. journal 存储格式改 JSONL 增量追加(旧 `.json` 只读兼容;**已收尾的 journal 文件在 scan 时清理**)(T3);
  4. `scan_interrupted` 判据从「未收尾且有 unfinished 条目」改为「未收尾即报+实例锁探针判活」(新 journal 带 src/dst/game_root 身份;旧 journal 无身份,维持旧判据)(T3);
  5. executor 的 after_action 改为「有意向的动作(COPY/ASK)一律回调(含 asked_no)」,且结果先入列再写完成记录;SKIP 不再触发无效 journal 重写(T3);
  6. swap 覆盖同名 jar 前备份到 `<game_root>/.mcmig/backups/swap/<时间戳>/`(决策语义与退出码不变,新增备份行为+一行备份位置输出)(T7);
  7. `build_plan` 返回值 3 元组→4 元组(追加 review extras dict;cli 两处/测试机械更新)(T6);
  8. 页面 SSE onerror 不再主动 close EventSource(交浏览器自动重连),断线文案改「正在重连」;收到任意新事件即清除断线横幅(T5)。
- 涉及 index.html 的任务必须同步更新 `tests/test_page_contract.py`(源码契约钉,W1W2 教训①:UI 接缝是评审盲区)。

## Review Focus

1. **终态撕裂读**: emit(done) 与 _finish 分置两个临界区,GET 可读「running+revision 含 done」撕裂态→按该 revision 续订得空流——T2 红测 `test_emit_terminal_settles_status_atomically`(Job 单元层,emit 终态事件后 snapshot 即终态,无需 _finish)。
2. **旧布局用户 GUI 全链路不同构再犯**: GUI 校验侧快照取位仅锚定(server.py:747-750),CLI 已 W2.6 同构——T1 红测 `test_migrate_gui_legacy_snapshots_guard_no_false_positive`(旧布局快照+GUI plan→migrate 不误报 snapshot_changed)。
3. **中断误报与漏报**: 活着的 CLI job 被 GUI 报「上次未完成」(跨进程失明),或「全完成+未收尾」的死任务漏报(P2-8)——T3 红测 `test_scan_interrupted_reports_dead_but_skips_lock_holder`(子进程持锁=活,跳过;伪造死任务 journal=报)与 `test_interrupted_all_completed_zero_unfinished_reported`。
4. **慢消费者结果丢失**: 队列满被摘后生成器空转到收尾,done 无法送达——T2 红测 `test_overflowed_subscriber_gets_explicit_signal_and_prompt_close`(溢出帧+即时断流促重同步)。
5. **swap 覆盖不可恢复**: 同名不同内容覆盖无备份、装包中途崩溃无账可查——T7 红测 `test_swap_install_backs_up_overwritten_jars` + `test_swap_journal_records_intent_per_jar`。
6. **大列表渲染卡死**: 万级行全量建 DOM/逐事件强制回流——T6 契约测 `test_group_rows_lazy_built`+`test_file_events_batched_by_animation_frame`(源码特征)+手测清单大目录项。

---

## W1W2/两轮复审吸收清单处置表(本计划立论前提,逐项验证于 2026-10-02,基线 3698356)

| # | 条目(来源) | 现状核实 | 处置 |
|---|---|---|---|
| 1 | legacy **快照** notice 事件无测试(终审) | `notice.legacy_snapshot`(server.py:567)确无 GUI 测试;规则 notice 已有 test_plan_job_uses_legacy_rules_with_notice | **T1**(随 GUI 旧布局红测补断言) |
| 2 | guards_plan_mismatch 空 blockers 数组(终审) | server.py:742 `_guards_error("guards_plan_mismatch", [])`,details.blockers 空列表,消费方取 [0] 会崩 | **T1**(合成 blocker 入列) |
| 3 | last_event_id 非法 query 422 与 docstring 不一致(终审) | 参数声明 `last_event_id: int \| None`(server.py:1278)→ 非数字 query 走 RequestValidationError 422,docstring 却说「非法值按 0」 | **T2**(参数改 str 手工解析) |
| 4a | abandoned 用例 0.5s 时序假设(终审) | test_instlock.py:148 `time.sleep(0.5)` 等「等待方已进入 WaitForSingleObject」 | **接受并强化注释**(T4:并发等待已是正确形态,同步窗固有;注释说明为何 0.5s 下界可靠) |
| 4b | record_completion 异常先于 results.append 逃逸(终审) | executor.py:161-162:after_action 异常时该 result 未入列,分发计数失真 | **T3**(先 append+cb 再 after_action,异常捕获→journal_failed+break) |
| 5 | after_action 对 SKIP 无效 journal 重写(终审) | executor.py:161 条件 `status != "asked_no"` 含 skipped → record_completion 查无条目仍全量重写 | **T3**(行为门控:COPY/ASK 才回调;存储改 JSONL 后重写问题一并消解) |
| 6 | 页面文案 STRINGS 化+README plan 旧回退精确化(终审) | 页面 PHASE_LABELS 等散硬编码;README 未区分「plan 旧回退仅 CLI」 | **T5**(页面 PAGE_STRINGS)/**T10**(README) |
| 7 | 页面刷新恢复游标(W2.5 复审 B5②;spec §4.1 明文) | 页面无 sessionStorage/游标,刷新即丢任务 | **T5** |
| 8 | guard 快照缺失静默无日志→W2.6 已 fail-closed,余「日志措辞」 | pipeline.py `_load_guard_snapshots` 已 log.warning,前缀「[提示]」与阻断结局不符 | **T1**(改「[警告]」) |
| 9 | interrupted 清除通道+finish journal 清扫(W2.5 B4 余项) | 无 dismiss 端点;finished journal 永不清理 | **T3** |
| 10 | GUI 校验侧快照取位仅锚定(W2.5/二轮 P1-1 余项) | server.py:747-750/797-798 锚定直拼,CLI 已 find_snapshot 同构 | **T1**(precheck_execution 下沉,取位单点化) |
| 11 | 页面忽略 reset 事件(W2.5) | index.html onmessage 仅 phase/file/done/error 分支 | **T5**(reset→GET 状态重建) |
| 12 | 断线横幅收到新事件不清除(W2.5) | onerror 置横幅后无清除路径 | **T5**(onmessage 清除) |
| 13 | journal O(N²) 全量重写(W2.5) | journal.py `_rewrite` 每条目全量 JSON 重写 | **T3**(JSONL 追加) |
| 14 | btn-back1 在 plan-body 内失败态无法返回(W2.5) | index.html:228 按钮在 #plan-body 内,plan 失败时 body 隐藏→无返回 | **T6**(移出/错误分支补入口) |
| 15 | §3.3 检测范围摘要说明(W2.5) | 审阅页无「检测范围分级」说明行 | **T6**(STRINGS 固定文案入摘要区) |
| 16 | differ note 透传后 mtime 闸门口径统一(W2.5) | build_plan:407 用活目录 resolve 比较,run_diff:972 用快照记录 resolved_root,两形不同 | **T1**(抽 `_same_physical_root` helper 两处共用) |
| 17 | Snapshot.load 往返 format 升格(W2.5) | `Snapshot.save` 唯一调用方=scan_version:157(恒新扫 v2),**无 load→save 往返路径** | **撤销**(条目过时,证据如左;若未来出现往返再立) |
| 18 | 被丢弃慢订阅者流到收尾才断(W2.5) | emit 摘队列后生成器照常 q.get 空转 | **T2**(溢出信号+即时断流) |
| 19 | POSIX 锁 _stale_unlink TOCTOU(一轮 B1=二轮 P1 双确认) | instlock.py:244-250 读 pid→unlink 窗口可互删他方活锁 | **T4**(fcntl.flock 化) |
| 20 | SSE 终态原子提交(二轮 P2-4) | emit(done) 与 _finish 分置临界区 | **T2** |
| 21 | 慢订阅者溢出显式信号(二轮 P2-5) | queue.Full 仅摘队列 | **T2**(与 #18 同修) |
| 22 | 页面 cancel 迟到响应(二轮 P2-7) | cancelMigrate 202 后无条件「正在停止…」;轮询见终态只藏按钮 | **T5**(终态判定先行+共用终态渲染) |
| 23 | 中断恢复模型重设计(二轮 P2-8+跨进程) | scan_interrupted 只报「有 unfinished」的 job;GUI active_id 不识 CLI 活任务 | **T3**(锁探针活性+未收尾即报+身份入 journal) |
| 24 | 大整合包渲染性能(W2.5) | 全量建行+逐事件直写 DOM | **T6**(懒建行+rAF 合批+手测) |
| 25 | 设计输入① SSE/GET 统一状态消费(reviewer) | 页面两路更新各写各的 DOM | **T5**(Presentation Model) |
| 26 | 设计输入② 保留 Last-Event-ID 自动重连(reviewer) | onerror 主动 close 断掉浏览器重连 | **T5**(与 #12 同修) |
| 27 | 设计输入③ 执行上下文传到底(reviewer) | GUI ③④⑤校验序列在 server.py 内联,与 CLI 各写一套 | **T1**(precheck_execution) |
| 28 | modid 碰撞收口、uninstall 措辞(spec §5.4 随行) | 未做 | **滑移 W4 T15**(spec 已标注可滑移) |

---

### Task 1: 执行前置检查下沉 `pipeline.precheck_execution`(执行上下文传到底+GUI 取位同构)

**Files:**
- Modify: `migration/pipeline.py`(新函数 precheck_execution;_load_guard_snapshots 日志措辞;_same_physical_root helper)
- Modify: `migration/cli.py:593-635`(_cmd_migrate 守卫段改薄壳调用)
- Modify: `migration/gui/server.py:714-813`(_run_migrate_job ③④⑤段改薄壳调用;guards_plan_mismatch 空 blockers 修复)
- Test: `tests/test_pipeline.py`、`tests/test_gui_server.py`、`tests/test_cli.py`

**Interfaces:**
- Consumes: `validate_review(plan, *, game_root, snapshot_paths, rule_sources)`(review.py,重验以 review.rule_sources 为权威)、`preflight_execute(plan, game_root, src, dst, *, decisions, data_dir, legacy_dir)`、`check_action_states(actions, src_snap, dst_snap, src_root, dst_root)`、`_load_guard_snapshots(plan, src_root, legacy_dir)`、`find_snapshot(data_dir, legacy_dir, version)`、`select_rules_dir(data_dir, legacy_dir)`、`ExecutionDecisions`(preflight.py)。
- Produces(T2-T9 与 W4 依赖):
```python
# pipeline.py(re-export 保持 spec 命名 pipeline.precheck_execution 成立)
@dataclass(frozen=True)
class PrecheckOutcome:
    """执行前置检查结论:守卫+预检+状态校验三段合一的统一出口。"""
    blockers: list[PreflightBlocker]   # 按检查序聚合(守卫→预检→状态)
    warnings: list[PreflightWarning]   # 预检降级警告(决策放行后的提示)
    review_missing: bool               # plan.review is None(调用方发「旧版计划」提示)

def precheck_execution(
    plan: MigrationPlan, game_root: Path, src: str, dst: str, *,
    legacy_dir: Path | None = None,
    decisions: ExecutionDecisions | None = None,
) -> PrecheckOutcome:
    """执行前置检查单点(CLI/GUI 平级消费,spec §4.2 持锁后调用)。

    检查序(行为变更白名单①):① 审阅守卫 validate_review(快照取 find_snapshot
    实际命中位,规则经 select_rules_dir 同参复算,--force/accept_stale 放行
    snapshot_changed);② 共享预检 preflight_execute;③ 首跑审阅状态校验
    (rerun_executed 决策跳过;有守卫缺材料 fail-closed=review_snapshot_missing)。
    """
```

- [ ] **Step 1: 写失败测试(pipeline 单点)**

```python
# tests/test_pipeline.py 追加
def _legacy_layout(tmp_path):
    """旧布局夹具:双侧快照仅存 cwd/.mcmig/snapshots(rename 保留 mtime,不触发
    snapshot_stale),锚定目录为空;plan 经 build_plan(legacy 载入)签发——与
    tests/test_cli.py::test_migrate_legacy_snapshot_state_change_blocked 同构。

    Returns: (game_root, plan, legacy_dir)。
    """
    game = tmp_path / "game"
    for name, text in (("src", "fps:120\n"), ("dst", "fps:60\n")):
        d = game / "versions" / name
        d.mkdir(parents=True)
        (d / "options.txt").write_text(text, encoding="utf-8")
    anchored = game / ".mcmig" / "snapshots"
    anchored.mkdir(parents=True)
    scan_version(game, "src", anchored)
    scan_version(game, "dst", anchored)
    legacy = tmp_path / ".mcmig" / "snapshots"
    legacy.mkdir(parents=True)
    for ver in ("src", "dst"):
        (anchored / f"{ver}.snapshot.json").rename(legacy / f"{ver}.snapshot.json")
    plan, _compat, _pairs = build_plan(
        tmp_path, game, "src", "dst",
        mcmig_dir=tmp_path / ".mcmig", plans_dir=game / ".mcmig" / "plans",
        data_dir=game / ".mcmig")
    return game, plan, tmp_path / ".mcmig"

def test_precheck_execution_guards_then_preflight_then_state(tmp_path):
    """precheck_execution 三段合一:未漂移布局零阻断;目标漂移产状态阻断。"""
    game, plan, legacy = _legacy_layout(tmp_path)
    outcome = precheck_execution(plan, game, "src", "dst", legacy_dir=legacy)
    assert outcome.blockers == [] and not outcome.review_missing
    (game / "versions" / "dst" / "options.txt").write_text("篡改\n", encoding="utf-8")
    outcome2 = precheck_execution(plan, game, "src", "dst", legacy_dir=legacy)
    assert "target_state_changed" in [b.code for b in outcome2.blockers]

def test_precheck_execution_review_none_flagged(tmp_path):
    """review=None 的旧版计划:review_missing=True 且不产 snapshot 守卫阻断。"""
    game, plan, legacy = _legacy_layout(tmp_path)
    plan.review = None
    outcome = precheck_execution(plan, game, "src", "dst", legacy_dir=legacy)
    assert outcome.review_missing and not [b for b in outcome.blockers
                                            if b.code == "snapshot_changed"]

def test_precheck_execution_legacy_layout_no_false_positive(tmp_path):
    """W2.6 P1-1 的管线侧:旧布局快照下 precheck 不误报 snapshot_changed
    (取位与签发同构——GUI 消费路径 T1 Step4 同判)。"""
    game, plan, legacy = _legacy_layout(tmp_path)
    outcome = precheck_execution(plan, game, "src", "dst", legacy_dir=legacy)
    assert not [b for b in outcome.blockers if b.code == "snapshot_changed"]
    assert not [b for b in outcome.blockers if b.code == "review_snapshot_missing"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline.py -k "precheck_execution" -v`
Expected: FAIL(`AttributeError: module 'migration.pipeline' has no attribute 'precheck_execution'`)。

- [ ] **Step 3: 实现 precheck_execution**

pipeline.py 追加(放 execute_migration 之前;头部 import 增 `PrecheckOutcome` 的消费符号无需,re-export 在 `from .preflight import` 行追加无需——本类型定义在本模块):

```python
@dataclass(frozen=True)
class PrecheckOutcome:
    blockers: list[PreflightBlocker]
    warnings: list[PreflightWarning]
    review_missing: bool


def precheck_execution(
    plan: MigrationPlan, game_root: Path, src: str, dst: str, *,
    legacy_dir: Path | None = None,
    decisions: ExecutionDecisions | None = None,
) -> PrecheckOutcome:
    """执行前置检查单点(CLI/GUI 平级消费;调用方须已持实例锁,spec §4.2)。"""
    blockers: list[PreflightBlocker] = []
    warnings: list[PreflightWarning] = []
    data_dir = game_root / ".mcmig"
    # ① 审阅守卫:快照取 find_snapshot 实际命中位(锚定优先+旧布局回退,
    # 与签发侧同构);规则目录同参复算(validate_review 内以 review.rule_sources
    # 为权威,此处列表仅作无记录键旧守卫的回退)
    if plan.review is not None:
        chosen_rules_dir, _notices = select_rules_dir(data_dir, legacy_dir)
        guard_blockers = validate_review(
            plan, game_root=game_root,
            snapshot_paths={
                src: find_snapshot(data_dir, legacy_dir, src)[0],
                dst: find_snapshot(data_dir, legacy_dir, dst)[0],
            },
            rule_sources=[chosen_rules_dir / "rules.yaml"],
        )
        if decisions is not None and decisions.accept_stale:
            guard_blockers = [b for b in guard_blockers if b.code != "snapshot_changed"]
        blockers.extend(guard_blockers)
    # ② 共享执行预检(已执行/快照过期/目录缺失/疑似占用)
    pre_blockers, warnings = preflight_execute(
        plan, game_root, src, dst, decisions=decisions,
        data_dir=data_dir, legacy_dir=legacy_dir)
    blockers.extend(pre_blockers)
    # ③ 首跑审阅状态校验:重跑决策跳过;有守卫缺材料 fail-closed
    rerun = decisions is not None and decisions.rerun_executed
    if plan.review is not None and not rerun:
        src_root = game_root / "versions" / src
        snaps = _load_guard_snapshots(plan, src_root, legacy_dir=legacy_dir)
        if snaps is not None:
            blockers.extend(check_action_states(
                plan.actions, snaps[0], snaps[1],
                src_root, game_root / "versions" / dst))
        else:
            blockers.append(PreflightBlocker(
                "review_snapshot_missing", MSG_REVIEW_SNAPSHOT_MISSING))
    return PrecheckOutcome(blockers, warnings, plan.review is None)
```

同任务小修:`_load_guard_snapshots` 内 `log.warning("[提示] ...")` 改 `log.warning("[警告] ...")`(吸收 #8);`MSG_REVIEW_SNAPSHOT_MISSING` 自 `.review` import(已有)。
mtime 闸门统一(吸收 #16):pipeline.py 新增

```python
def _same_physical_root(
    src_snap: Snapshot, dst_snap: Snapshot, src_dir: Path, dst_dir: Path
) -> bool:
    """mtime 证据闸门统一口径:双侧快照均记录 resolved_root 则比记录值,
    任一缺失(旧 v1 快照)回退活目录 resolve 比较——build_plan 与 run_diff
    两处调用点同形(spec F35,W3 复审 #16)。"""
    if src_snap.resolved_root is not None and dst_snap.resolved_root is not None:
        return src_snap.resolved_root == dst_snap.resolved_root
    return src_dir.resolve() == dst_dir.resolve()
```

build_plan:407 与 run_diff:972 改调本 helper;补一条用例:`test_mtime_gate_same_form_plan_and_diff`(构造 v1 快照对 resolved_root=None+同活目录 → 两处均 True;v2 快照对异根 → 两处均 False)。

- [ ] **Step 4: GUI 接线(失败测试先行)**

```python
# tests/test_gui_server.py 追加
def test_migrate_gui_legacy_snapshots_guard_no_false_positive(tmp_path, monkeypatch):
    """吸收 #1/#10:旧布局快照用户 GUI 全链路——plan 用旧快照载入+notice 提示,
    migrate 不误报 snapshot_changed;守卫错误 details.blockers 恒非空(#2)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    # 把锚定快照挪到 cwd/.mcmig/snapshots(rename 保 mtime,不触发 stale)
    (tmp_path / ".mcmig" / "snapshots").mkdir(parents=True)
    for ver in ("src", "dst"):
        (game / ".mcmig" / "snapshots" / f"{ver}.snapshot.json").rename(
            tmp_path / ".mcmig" / "snapshots" / f"{ver}.snapshot.json")
    job = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    events = _wait_job_done(client, job)
    assert events[-1]["type"] == "done"
    assert any(e["type"] == "notice" and "旧布局快照" in e["text"] for e in events)  # 吸收 #1
    r = client.post("/api/migrate", json={"src": "src", "dst": "dst",
                                          "ask_yes": [], "plan_id": events[-1]["plan_id"]})
    events2 = _wait_job_done(client, r.json()["job_id"])
    assert events2[-1]["type"] == "done"                              # 不误报

def test_migrate_guards_error_details_never_empty_blockers(tmp_path, monkeypatch):
    """吸收 #2:guards_plan_mismatch 时 details.blockers 恒含合成条目(消费方取 [0] 安全)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    j1 = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    old_id = _wait_job_done(client, j1)[-1]["plan_id"]
    j2 = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    _wait_job_done(client, j2)                                        # 盘上已是新计划
    r = client.post("/api/migrate", json={"src": "src", "dst": "dst",
                                          "ask_yes": [], "plan_id": old_id})
    ev = _wait_job_done(client, r.json()["job_id"])[-1]
    assert ev["type"] == "error" and ev["details"]["code"] == "guards_plan_mismatch"
    assert ev["details"]["blockers"] and ev["details"]["blockers"][0]["code"] == "guards_plan_mismatch"
```

实现(server.py `_run_migrate_job`):③④⑤三段(现 server.py:744-813)替换为:

```python
            outcome = precheck_execution(
                plan, ctx.game_root, src, dst, legacy_dir=mig_legacy)
            if outcome.review_missing:
                job.emit({"type": "notice",
                          "text": STRINGS["notice.review_missing"]})
            if outcome.blockers:
                _guards_error(outcome.blockers[0].code, outcome.blockers)
                return
            for w in outcome.warnings:
                job.emit({"type": "warning", "code": w.code, "message": w.message})
```

其中 `mig_legacy` 沿用现有归一化(server.py:754-759);`_guards_error` 的 guards_plan_mismatch 调用点(server.py:742)改 `_guards_error("guards_plan_mismatch", [PreflightBlocker("guards_plan_mismatch", STRINGS["job_err_guards.why"])])`(空列表消除);execute_migration 调用维持 `validate_states=False`(precheck 已做,同 plan 对象单次加载)。STRINGS 增 `notice.review_missing`(与 CLI 旧版计划提示同文案)。

- [ ] **Step 5: CLI 接线对拍**

`_cmd_migrate`(cli.py:593-635)守卫段替换为 precheck_execution 调用(`legacy_dir` 沿用现有变量 cli.py:617;`decisions=ExecutionDecisions(...--force 三映射)`;review_missing 时保留既有「缺少审阅守卫」提示行);输出文案逐字不变(既有 test_cli 防护用例为单阻断,首错序变化不可见)。若存在多阻断并存断言的用例,按白名单①更新断言顺序并在 docstring 标注「批次I-W3 T1」。

- [ ] **Step 6: 全量回归 + Commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`

```bash
git add migration/pipeline.py migration/cli.py migration/gui/server.py migration/gui/STRINGS.py tests/
git commit -m "feat(w3): 执行前置检查下沉 precheck_execution 单点——CLI/GUI 取位同构+守卫细节收口 (batchI-W3-T1)"
```

---

### Task 2: SSE 终态原子提交+慢订阅者溢出信号+衔接协议收口

**Files:**
- Modify: `migration/gui/server.py`(Job.emit 终态原子化;_Sub 订阅者对象+溢出信号;_sse_gen 促断流;last_event_id 参数改 str)
- Test: `tests/test_gui_server.py`

**Interfaces:**
- Consumes: 现状 Job/`_finish`/`_sse_gen`/`subscribe`。
- Produces(T5/T7/T8 页面与 W4 依赖):
```python
# Job 变更(对外契约):
#   job.emit(ev) — ev.type ∈ {done, error} 时在**同一临界区**内一并完成:
#     状态收口(cancelling→cancelled/error→failed/results 含失败→partial_failed/
#     否则 succeeded)+ done=True(revision/history/广播与状态同一锁内,封死
#     「GET 读 running+revision 含终态事件」撕裂态,二轮复审 P2-4)
#   _finish 删除;job 线程 finally 改为「未收尾时的防御性收口」(emit 本身失败等)
# 订阅者对象 _Sub(queue, dropped);生成器拆两层(可测性):
#   _sse_gen(job, last_seq) = subscribe → 委托 _pump_sub → unsubscribe(端点不变)
#   _pump_sub(job, sub, reset, replay) — 重放段→活段(q.get 轮询 0.5s);
#     醒来发现 job.done 或 sub.dropped 即收尾;sub.dropped 时补发一帧
#     data: {"type":"overflow"}(无 id)再断流——客户端据此重读 GET 状态并以
#     Last-Event-ID 重订(二轮复审 P2-5/#18:被摘订阅者不再空转到 job 收尾)
# GET /api/jobs/{id}/events 的 last_event_id query 参数改 str 手工解析
#   (int 失败按 0;消除 pydantic int 校验的 422 路径,对齐 docstring,吸收 #3)
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_gui_server.py 追加
def test_emit_terminal_settles_status_atomically():
    """P2-4:终态事件入历史与状态收口同一临界区——emit(done) 返回即终态,无 _finish。"""
    from migration.gui.server import Job
    from migration.executor import FileResult
    job = Job("t1", "migrate")
    job.emit({"type": "phase", "name": "migrate"})
    assert job.snapshot()["status"] == "running"
    job.settle()                                        # 非取消场景仍先封取消窗
    job.results = [FileResult("options.txt", "copied", failed=True, error="x")]
    job.emit({"type": "done", "job_kind": "migrate", "summary": {}})
    snap = job.snapshot()                               # emit 后立即快照,不经 _finish
    assert snap["status"] == "partial_failed" and snap["revision"] == 2

def test_emit_error_settles_failed_without_finish():
    """P2-4 同型:error 事件即收口 failed(守卫阻断的早退路径)。"""
    from migration.gui.server import Job
    job = Job("t2", "migrate")
    job.emit({"type": "error", "what": "w", "why": "y", "details": {}})
    assert job.snapshot()["status"] == "failed" and job.done

def test_overflowed_subscriber_gets_explicit_signal_and_prompt_close(monkeypatch):
    """P2-5/#18:慢订阅者队列满被摘后,其流尽快以 overflow 帧收尾(而非空转到 job 结束)。

    确定性构造:手工占满订阅者队列(maxsize=1)再 emit → put_nowait 必然 Full
    → sub.dropped;随后直接泵送该 sub(_pump_sub),0.5s 轮询醒来即收尾。
    """
    monkeypatch.setattr(server_module, "_SUBSCRIBER_QUEUE_LIMIT", 1)
    job = server_module.Job("t3", "migrate")
    reset, replay, sub = job.subscribe(0)
    sub.queue.put_nowait({"type": "file", "seq": -1})   # 预占满队列(不被消费)
    job.emit({"type": "file", "path": "f0", "index": 1, "total": 9})
    assert sub.dropped                                  # 溢出即摘除+标记
    frames = list(server_module._pump_sub(job, sub, reset=False, replay=[]))
    assert any('"overflow"' in f for f in frames)       # 显式溢出帧
    assert not job.done                                 # job 未收尾,流已先行断开

def test_last_event_id_invalid_query_treated_as_zero(tmp_path, monkeypatch):
    """吸收 #3:?last_event_id=abc 按 0 处理(200 从头重放),不再 422。"""
    game, client = _make_client(tmp_path, monkeypatch)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    _wait_job_done(client, job_id)
    with client.stream("GET", f"/api/jobs/{job_id}/events?last_event_id=abc") as resp:
        assert resp.status_code == 200
        lines = [l for l in resp.iter_lines() if l.startswith("data: ")]
    assert json.loads(lines[-1][6:])["type"] == "done"  # 从头重放含终态
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gui_server.py -k "atomically or without_finish or overflowed or invalid_query" -v`
Expected: 4 FAIL(emit 不收口状态/无 dropped 属性/无 overflow 帧/abc 走 422)。

- [ ] **Step 3: 实现**

- 新增订阅者小对象(模块内私有):

```python
class _Sub:
    """单个订阅者:独立有界队列 + 溢出丢弃标记(emit 摘除时置位,生成器据此断流)。"""
    __slots__ = ("queue", "dropped")

    def __init__(self) -> None:
        self.queue: queue.Queue[dict] = queue.Queue(maxsize=_SUBSCRIBER_QUEUE_LIMIT)
        self.dropped = False
```

- `Job._subs: list[_Sub]`;`subscribe(last_seq)` 返回 `(reset, replay, sub)`;`unsubscribe(sub)` 容忍已摘除。
- `emit` 尾段改:

```python
            for sub in list(self._subs):
                try:
                    sub.queue.put_nowait(ev)
                except queue.Full:
                    # 慢订阅者:摘除并标记——其 SSE 流将尽快以 overflow 帧收尾,
                    # 客户端凭 Last-Event-ID 重订或重读状态端点;绝不反压本线程
                    sub.dropped = True
                    self._subs.remove(sub)
```

- `emit` 增终态原子段(锁内,事件派生状态维护之后):

```python
            if etype in ("done", "error"):
                # 终态原子提交(P2-4):状态收口+done 标志与 revision/history/广播
                # 同一临界区——GET 的 snapshot() 与订阅重放永不再见到
                # 「running + revision 已含终态事件」的撕裂态
                self._settle_status_locked()
                self.done = True
```

`_settle_status_locked()`(调用方持锁)承接现 `_finish` 的判定体(cancelling→cancelled;error→failed;results 含失败→partial_failed;否则 succeeded)。`_finish` 删除;两个 job 线程体的 `finally: _finish(...)` 改为防御性收口:

```python
    finally:
        # emit 终态事件已原子收口;此处仅兜底「终态事件未能发出」的异常路径
        with job._lock:
            if not job.done:
                job._settle_status_locked()
                job.done = True
```

- `_pump_sub(job, sub, *, reset, replay)`:承接收尾 `sub.queue` 的泵送逻辑(自现 `_sse_gen` 主体搬移):重放段遇 done/error 即断;活段 `q.get(timeout=0.5)` 的 `except queue.Empty:` 改 `if job.done or sub.dropped: break`;每帧 yield 后若 `sub.dropped` 同样跳出;循环收尾时 `sub.dropped` 为真且未发过终态帧 → 补发 `data: {"type":"overflow"}\n\n` 再 return。`_sse_gen(job, last_seq)` 改为 `subscribe → 委托 _pump_sub → finally unsubscribe` 的薄壳(端点签名不变)。
- 事件端点:`last_event_id: str | None = None`(query 声明改 str),解析 `int(raw) except ValueError → 0`(头部路径不变);docstring 的「非法值按 0」自此字面成立。
- 模块 docstring 事件 schema 段补 `overflow` 型一行。

- [ ] **Step 4: 全量回归(重点:既有 SSE 序列/终态用例兼容——状态判定逻辑等价迁移)+ Commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`

```bash
git add migration/gui/server.py tests/
git commit -m "feat(w3): SSE 终态原子提交+慢订阅者溢出信号+last_event_id 契约对齐 (batchI-W3-T2)"
```

---

### Task 3: journal JSONL 增量化+中断判定重构(锁探针活性)+清除通道

**Files:**
- Modify: `migration/journal.py`(存储 JSONL;身份字段;scan_interrupted 重构+finished 清扫)
- Modify: `migration/executor.py`(after_action 行为门控+先入列后写完成)
- Modify: `migration/gui/server.py`(interrupted 端点透传新字段+dismiss 端点)
- Modify: `migration/gui/STRINGS.py`(dismiss/unknown_progress 文案)
- Test: `tests/test_journal.py`、`tests/test_executor.py`、`tests/test_gui_server.py`

**Interfaces:**
- Consumes: `instance_locks(game_root, *versions, timeout)`(instlock.py,探针用);`JobJournal` 现有 API。
- Produces(T5 横幅/T7 swap journal/W4 依赖):
```python
# journal.py(公共 API 不变:record_intent/record_completion/unfinished/finish/
#   finished/write_failed/path;内部存储改 JSONL 追加,首行 start 携身份)
#   JobJournal.__init__(journal_dir, job_id, kind, *, src="", dst="", game_root="")
#     — 新文件写 <job_id>.jsonl;遇旧 <job_id>.json 按旧格式只读恢复(兼容)
# scan_interrupted(jobs_dir) -> list[dict]:
#   [{"job_id", "kind", "src", "dst", "game_root",
#     "entries": [{"rel","detail"}...],          # 待核对(可为空)
#     "unknown_progress": bool}]                  # 未收尾且无待核对=整体进度未知(P2-8)
#   判定:未收尾(finish 未标记)即报;带身份(src/dst/game_root)的 journal 以
#   实例锁探针判活——instance_locks 可获取=无进程在操作该实例对=前任已死→报;
#   获取失败(InstanceLockError)=有活进程持锁(本 app 或 CLI)→跳过(跨进程
#   活性判定,替代仅识本 app 的 active_id 过滤);旧格式 journal 无身份→维持
#   旧判据(有 unfinished 条目才报,保守不扩大)。finished=True 的 journal 文件
#   在扫描时删除(清扫,吸收 #9)。
# gui/server.py:
#   POST /api/jobs/interrupted/{job_id}/dismiss → 200 {"ok": true}(删除 journal 文件;不存在 404)
# executor.py execute 循环:
#   results.append(result); cb(result) 先于 after_action;after_action 仅对
#   COPY/ASK(有意向者)回调且不再排除 asked_no(结局已知即收口);其 JournalError
#   → journal_failed=True + break(result 已入列,分发计数不失真,#4b/#5)
```

- [ ] **Step 1: 写失败测试(journal 与 executor)**

```python
# tests/test_journal.py 追加
def test_jsonl_append_not_full_rewrite(tmp_path):
    """#13:意图/完成各追加一行,既有行不被重写(文件头 start 行原样保留)。"""
    j = JobJournal(tmp_path, "job1", "migrate", src="s", dst="d", game_root="g")
    head = (tmp_path / "job1.jsonl").read_text(encoding="utf-8")
    j.record_intent("a.txt", {"op": "copy"})
    j.record_intent("b.txt", {"op": "copy"})
    body = (tmp_path / "job1.jsonl").read_text(encoding="utf-8")
    assert body.startswith(head) and body.count("\n") == 3     # start+2 intent,无重写

def test_legacy_json_journal_still_readable(tmp_path):
    """旧 .json 存储(write_json_atomic 全量)只读恢复,重开照常判 unfinished。"""
    (tmp_path / "old.json").write_text(json.dumps({
        "kind": "migrate", "started_at": "t", "finished": False,
        "entries": {"options.txt": {"detail": {"op": "copy"}, "completed": False}},
    }, ensure_ascii=False), encoding="utf-8")
    j = JobJournal(tmp_path, "old", "migrate")
    assert [e["rel"] for e in j.unfinished()] == ["options.txt"]

def test_interrupted_all_completed_zero_unfinished_reported(tmp_path):
    """P2-8:A 完成+B 意图未登记即崩溃 → journal 全完成未收尾 → 仍报整体中断
    (unknown_progress=True,entries 为空)。"""
    j = JobJournal(tmp_path, "job2", "migrate", src="s", dst="d", game_root=str(tmp_path))
    j.record_intent("a.txt", {"op": "copy"}); j.record_completion("a.txt")
    items = scan_interrupted(tmp_path)
    assert items and items[0]["job_id"] == "job2" and items[0]["unknown_progress"] is True

def test_scan_interrupted_reports_dead_but_skips_lock_holder(tmp_path):
    """跨进程活性(二轮跨进程项):子进程持锁的未收尾 journal=活任务不报;
    无锁占用的未收尾 journal=前任已死,报。两 journal 用不同实例对,探针互不串扰。"""
    import subprocess, sys, textwrap
    j = JobJournal(tmp_path, "dead", "migrate", src="A", dst="B",
                   game_root=str(tmp_path))
    j.record_intent("a.txt", {"op": "copy"})
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import time
        from migration.instlock import instance_locks
        with instance_locks({str(tmp_path)!r}, "C", "D"):
            print("HELD", flush=True); time.sleep(30)
    """)])
    try:
        assert holder.stdout.readline().strip() == b"HELD"
        j2 = JobJournal(tmp_path, "alive", "migrate", src="C", dst="D",
                        game_root=str(tmp_path))
        j2.record_intent("b.txt", {"op": "copy"})
        ids = [i["job_id"] for i in scan_interrupted(tmp_path)]
        assert "dead" in ids and "alive" not in ids
    finally:
        holder.kill(); holder.wait()

def test_finished_journals_pruned_on_scan(tmp_path):
    """#9:finished journal 扫描时清理(已尽其用,不再累积)。"""
    jf = JobJournal(tmp_path, "jdone", "migrate")
    jf.record_intent("a", {})
    jf.finish()
    scan_interrupted(tmp_path)
    assert not (tmp_path / "jdone.jsonl").exists()          # 已清扫
```

```python
# tests/test_gui_server.py 追加
def test_interrupted_dismiss_endpoint(tmp_path, monkeypatch):
    """#9:dismiss 端点删除指定未收尾 journal;再 dismiss → 404。"""
    game, client = _make_client(tmp_path, monkeypatch)
    jobs_dir = game / ".mcmig" / "jobs"
    jobs_dir.mkdir(parents=True)
    JobJournal(jobs_dir, "stale1", "migrate", src="s", dst="d",
               game_root=str(game)).record_intent("a.txt", {"op": "copy"})
    assert client.post("/api/jobs/interrupted/stale1/dismiss").json()["ok"] is True
    assert not (jobs_dir / "stale1.jsonl").exists()
    assert client.post("/api/jobs/interrupted/stale1/dismiss").status_code == 404
```

```python
# tests/test_executor.py 追加
def test_after_action_journal_error_keeps_result_counted(mini_plan_dirs, monkeypatch):
    """#4b:完成记录写失败时该文件结果已入列(分发计数不失真),后续停发。"""
    ...  # mini_plan_dirs 夹具 3 文件;after_action 第 2 次起抛 JournalError
         # 断言 len(results)==2 且 executor.journal_failed
def test_after_action_not_called_for_skip(mini_plan_dirs):
    """#5:SKIP 动作不触发 after_action(无意图,无 journal 写)。"""
    ...  # 记录调用路径列表,断言仅 COPY/ASK 路径出现
def test_after_action_called_for_asked_no(mini_plan_dirs):
    """asked_no 结局已知,完成记录照写(意图不留悬账)。"""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_journal.py tests/test_executor.py tests/test_gui_server.py -k "jsonl or legacy_json or zero_unfinished or lock_holder or pruned or dismiss or keeps_result_counted or not_called_for_skip or asked_no" -v`
Expected: FAIL(构造器无 src/dst/game_root 参数/scan_interrupted 判据不同/无 dismiss/executor 顺序与门控不同)。

- [ ] **Step 3: 实现 journal.py**

- `JobJournal.__init__` 增 keyword-only `src/dst/game_root`(默认空串=无身份,旧调用兼容);`self.path = journal_dir / f"{job_id}.jsonl"`;存在旧 `<job_id>.json` 且无 .jsonl → 旧格式只读恢复(`self._legacy = True`,重开不迁移写)。
- 存储原语:`_append(line: dict) -> None`(`open(self.path, "a", encoding="utf-8")` 写 `json.dumps(line, ensure_ascii=False) + "\n"` + flush;OSError → `write_failed=True` + raise JournalError)。首行 start:`{"op":"start","kind","src","dst","game_root","started_at"}`。
- `record_intent` → append `{"op":"intent","rel","detail"}` 并更新内态 map;`record_completion` → append `{"op":"completion","rel"}`(内态无条目也 append,折叠时忽略孤儿 completion);`finish` → append `{"op":"finish"}`。
- `_load` 重写:新格式逐行折叠(start/intent/completion/finish → 内态);损坏行跳过并 warning(单行损坏不毁整个 journal);旧格式走现逻辑。
- `scan_interrupted` 重构:

```python
def scan_interrupted(jobs_dir: Path) -> list[dict[str, object]]:
    """扫描 jobs 目录:未收尾 job → 待核对清单;锁探针判活;finished 清扫。"""
    if not jobs_dir.is_dir():
        return []
    items: list[dict[str, object]] = []
    for p in sorted([*jobs_dir.glob("*.jsonl"), *jobs_dir.glob("*.json")]):
        try:
            journal = JobJournal(jobs_dir, p.stem, "")
        except JournalError as e:
            log.warning("journal 损坏/不可读,已跳过 %s: %s", p, e)
            continue
        if journal.finished:
            p.unlink(missing_ok=True)          # 清扫:收尾文件已尽其用(#9)
            continue
        entries = journal.unfinished()
        has_identity = bool(journal.src and journal.dst and journal.game_root)
        if has_identity:
            from .instlock import InstanceLockError, instance_locks
            try:                                # 探针:可获取=无活进程操作该实例对
                with instance_locks(Path(journal.game_root), journal.src, journal.dst,
                                    timeout=0.2):
                    pass
            except InstanceLockError:
                continue                        # 有活进程持锁(GUI 或 CLI)→ 非中断
        elif not entries:
            continue                            # 旧格式无身份:维持旧判据,不扩大
        items.append({"job_id": p.stem, "kind": journal.kind,
                      "src": journal.src, "dst": journal.dst,
                      "game_root": journal.game_root, "entries": entries,
                      "unknown_progress": not entries})
    return items
```

- 注意:server 侧 `/api/jobs/interrupted` 的 active_id 过滤保留(同进程快路径;探针是跨进程真相源)。

- [ ] **Step 4: 实现 executor 循环改造 + GUI dismiss 端点**

executor.py:143-163 改:

```python
            result: FileResult
            if action.behavior == Behavior.COPY:
                result = self._copy_one(action.path, dry_run)
            elif action.behavior == Behavior.ASK:
                result = (self._copy_one(action.path, dry_run)
                          if self.ask_handler(action) else FileResult(action.path, "asked_no"))
            else:
                result = FileResult(action.path, "skipped")
            results.append(result)
            cb(result)  # 结果先入列/上报,完成记录随后(写失败不失计数,#4b)
            if (action.behavior in (Behavior.COPY, Behavior.ASK)
                    and after_action is not None):
                try:
                    after_action(action, result)   # ③完成(asked_no 结局已知同样收口)
                except JournalError as e:
                    self.journal_failed = True
                    log.error("journal 完成记录写入失败,停止分发后续动作: %s", e)
                    break
```

server.py 增端点(声明序置于 `/api/jobs/interrupted` 之后、动态段之前不冲突——静态前缀 `interrupted` 已在先):

```python
    @app.post("/api/jobs/interrupted/{job_id}/dismiss")
    def api_jobs_interrupted_dismiss(job_id: str) -> dict[str, bool]:
        """清除一条中断记录:删除对应 journal 文件(#9 清除通道)。"""
        jobs_dir = app.state.wdir.jobs
        if jobs_dir is None:
            raise ApiError(422, "err_no_game_root.what", "err_no_game_root.why")
        for suffix in (".jsonl", ".json"):
            p = jobs_dir / f"{job_id}{suffix}"
            if p.exists():
                p.unlink()
                return {"ok": True}
        raise ApiError(404, "err_job_not_found.what", "err_job_not_found.why")
```

既有 test_journal/test_gui_server 中断用例按新返回形(含 src/dst/game_root/unknown_progress)机械更新;两个 journal 构造点补身份:CLI `_cmd_migrate`(cli.py:671)加 `src=args.src, dst=args.dst, game_root=str(game_root)`;GUI `_run_migrate_job`(server.py:821)加 `src=src, dst=dst, game_root=str(ctx.game_root)`(swap 的构造点在 T7 新增时直接带身份)。

- [ ] **Step 5: 全量回归 + Commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`

```bash
git add migration/journal.py migration/executor.py migration/gui/server.py migration/gui/STRINGS.py migration/cli.py tests/
git commit -m "feat(w3): journal JSONL 增量+中断判定锁探针活性+清除通道+executor 完成记录收口 (batchI-W3-T3)"
```

---

### Task 4: POSIX 实例锁 fcntl.flock 化

**Files:**
- Modify: `migration/instlock.py`(POSIX 后端重写;模块 docstring)
- Test: `tests/test_instlock.py`

**Interfaces:**
- Consumes: 现状 `_acquire_lockfile/_stale_unlink/_release_lockfile`(将被替换)。
- Produces(对外不变): `instance_locks(game_root, *versions, timeout=5.0)`;POSIX 路径 returns `(fd, False)`——**flock 无 abandoned 语义**(持有者死亡由内核即刻释放锁,等待方干净获取;中断证据归 journal,docstring 明示);Windows named mutex 路径零改动。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_instlock.py 追加
def test_posix_backend_uses_flock_no_stale_unlink():
    """#19/B1 双确认:POSIX 后端必须以 fcntl.flock 为互斥真源,_stale_unlink 的
    「读 pid→unlink」TOCTOU 模式不得存在(窗口内可互删他方活锁,两轮复审独立指出)。"""
    import inspect
    import migration.instlock as il
    src = inspect.getsource(il)
    assert "fcntl.flock" in src
    assert "_stale_unlink" not in src and "def _release_lockfile" not in src or True
    # 精确断言:锁文件路径函数保留(互斥真源换 flock,文件仍在),陈旧摘除逻辑整体消失
    assert not hasattr(il, "_stale_unlink")

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 回退后端专属")
def test_posix_flock_two_process_exclusive(tmp_path):
    """flock 互斥:子进程持锁期间主进程获取失败(超时报 InstanceLockError)。"""
    p = subprocess.Popen([sys.executable, "-c", _holder_script(tmp_path, ("A",), 3.0)])
    assert p.stdout.readline().strip() == b"HELD"
    with pytest.raises(InstanceLockError):
        with instance_locks(tmp_path, "A", timeout=0.5):
            pass
    p.kill(); p.wait()

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 回退后端专属")
def test_posix_flock_released_on_process_death_no_abandoned(tmp_path):
    """持有者死亡→内核释放 flock→等待方干净获取且 abandoned 恒空(语义变更:
    POSIX 侧中断证据只来自 journal,不再有陈旧接管标记)。"""
    p = subprocess.Popen([sys.executable, "-c", _holder_script(tmp_path, ("A",), 30.0)])
    assert p.stdout.readline().strip() == b"HELD"
    p.kill(); p.wait()
    with instance_locks(tmp_path, "A", timeout=2.0) as info:
        assert info["abandoned"] == []
```

同型更新 test_abandoned_mutex_flagged 的 docstring:注明 Windows named mutex 路径的 abandoned 语义不变;0.5s 同步窗补注释(吸收 #4a:「等待方进入 WaitForSingleObject 前持有者必须仍持有,0.5s 为实测下界;此为并发等待形态的固有同步窗」)。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_instlock.py -k "flock_no_stale_unlink" -v`
Expected: FAIL(`_stale_unlink` 仍存在,模块无 fcntl.flock)。POSIX 行为用例在 win32 跳过(实现须在 CI/Linux 复核,见 T10 手测清单注记)。

- [ ] **Step 3: 实现**

替换 POSIX 段(`_stale_unlink`/`_acquire_lockfile`/`_release_lockfile` 整体重写;`_pid_alive`/`_lock_file_path` 保留或按需精简):

```python
def _acquire_lockfile(key: str, timeout: float) -> tuple[int, bool]:
    """flock 独占锁定(POSIX 回退;持有者死亡由内核即刻释放,无陈旧接管)。

    互斥真源 = 打开状态上的 flock(LOCK_EX|LOCK_NB 轮询到超时);锁文件仅作
    锁载体不再承载 pid 判定——「读 pid→unlink」的 TOCTOU 窗口(可互删他方
    活锁)随 pid 判定整体消失(两轮复审 B1/P1 双确认)。abandoned 恒 False:
    flock 无前持有者异常退出语义,中断证据归 journal(spec §4.3)。
    """
    import fcntl

    path = _lock_file_path(key)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, _POSIX_LOCK_MODE)
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("ascii"))   # 诊断信息,非判定依据
            return fd, False
        except OSError:                                        # BlockingIOError=EWOULDBLOCK
            if time.monotonic() >= deadline:
                os.close(fd)
                raise InstanceLockError(
                    f"实例锁({key})",
                    "获取实例排他锁超时:另一 mcmig 进程正在操作该实例,"
                    "请等待其完成(或关闭其他 mcmig 窗口/命令)后重试",
                    {"holders": [key], "timeout": timeout},
                ) from None
            time.sleep(_POSIX_POLL_SECONDS)


def _release_lockfile(fd: int) -> None:
    """解锁并关闭(POSIX;尽力而为,不抛出)。"""
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except OSError:
        log.warning("flock 释放异常(fd=%s)", fd, exc_info=True)
```

`_release` 分派(`isinstance(token, Path)` → 改 `isinstance(token, int)` 为 POSIX、其余 Windows——令牌类型统一为 `int`(Windows handle / POSIX fd));`instance_locks` docstring 与模块头 POSIX 段同步改写。`import fcntl` 置函数内(Windows 无该模块,模块级 import 会炸)。

- [ ] **Step 4: 全量回归 + Commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`(Windows 路径全部用 named mutex,既有 6 用例不受影响)

```bash
git add migration/instlock.py tests/test_instlock.py
git commit -m "fix(w3): POSIX 实例锁 fcntl.flock 化——消 _stale_unlink TOCTOU(双确认 B1/P1) (batchI-W3-T4)"
```

---

### Task 5: 页面 Presentation Model+事件恢复协议(统一消费/自动重连/刷新游标/取消迟到/reset+overflow)

**Files:**
- Modify: `migration/gui/index.html`(jobModel 重构;onerror 不关流;sessionStorage 游标;cancel/轮询/横幅;PAGE_STRINGS)
- Test: `tests/test_page_contract.py`

**Interfaces:**
- Consumes: T2 的 overflow/reset 事件契约与 GET `/api/jobs/{id}` 状态快照;`?last_event_id=` 续订。
- Produces(T6/T8 页面重构的地基):
```javascript
// 页面状态模型(Presentation Model,设计输入①):SSE 事件与 GET 状态两路
// 更新同一 jobModel,渲染一律自模型派生(请求乱序可直测——对模型断言)
var jobModel = { jobId: null, kind: null, status: "idle", phase: null,
                 progress: { index: 0, total: 0 }, summary: null, results: null,
                 error: null, cancelled: false, lastSeq: 0,
                 get terminal() { return ["succeeded", "partial_failed", "failed",
                                          "cancelled"].indexOf(this.status) >= 0; } };
function applyEvent(ev)   // SSE 事件 → 模型(reset/overflow 不在此处理,见 openEvents)
function applyStatus(body)// GET 状态快照 → 模型(revision 一致性:仅当 body.revision >= lastSeq 采用)
function render()         // 模型 → DOM(全部展示逻辑唯一出口)
// 恢复协议(设计输入②):onerror 不 close(浏览器按标准自动重连并回发
//   Last-Event-ID);断线横幅改「连接中断,正在自动重连…」,任意新事件到达即清除;
//   sessionStorage["mcmig.job"] = {job_id, seq} 逐事件更新,页面加载时恢复
//   (GET 状态→terminal 则直接渲染终态,否则带 last_event_id 续订)
// 事件分派增补:reset/overflow → fetchStatus 重同步+重订;notice/warning → 提示区渲染
// 取消迟到响应(#22):cancelMigrate 先查 jobModel.terminal;cancelPollTimer 见终态
//   → applyStatus(body)+render()(与 SSE 共用终态处理),不再只藏按钮
// PAGE_STRINGS:页面侧文案集中(设计输入/吸收 #6 页面半)
```

- [ ] **Step 1: 写失败测试(源码契约)**

```python
# tests/test_page_contract.py 追加
def test_onerror_keeps_eventsource_for_autoreconnect() -> None:
    """设计输入②:onerror 在任务未终态时不得 close EventSource(浏览器标准
    自动重连会回发 Last-Event-ID);断线文案为「正在自动重连」。"""
    branch = re.search(r"es\.onerror\s*=\s*function\s*\(\)\s*\{(.*?)\n\s*\};", _PAGE, re.S)
    assert branch, "openEvents 必须有 onerror 处理"
    body = branch.group(1)
    assert "terminal" in body, "onerror 须先判任务终态"
    if "es.close()" in body:  # 若有 close,必须在终态判定之后(仅终态主动断流)
        assert body.index("terminal") < body.index("es.close()")
    assert "正在自动重连" in _PAGE
    assert "刷新页面重新开始" not in _PAGE    # 旧断线文案清除(改为重连语义)

def test_job_cursor_saved_and_restored() -> None:
    """#7/设计输入②:逐事件写 sessionStorage 游标;init 读游标→GET 状态→
    terminal 直接渲染,否则带 last_event_id 续订。"""
    assert re.search(r"sessionStorage\.setItem\([\"']mcmig\.job[\"']", _PAGE)
    assert re.search(r"sessionStorage\.getItem\([\"']mcmig\.job[\"']", _PAGE)
    assert re.search(r"last_event_id=", _PAGE)          # 续订 URL 携带游标

def test_presentation_model_single_writer() -> None:
    """设计输入①:SSE 与 GET 两路都写 jobModel,DOM 更新走 render()。"""
    assert re.search(r"function\s+applyEvent\(", _PAGE)
    assert re.search(r"function\s+applyStatus\(", _PAGE)
    assert re.search(r"var\s+jobModel", _PAGE)
    assert re.search(r"function\s+render\(\)", _PAGE)

def test_cancel_late_response_guarded() -> None:
    """#22:cancelMigrate 202 后写「正在停止…」前先判终态;轮询见终态走
    applyStatus+render 共用终态处理。"""
    cancel = re.search(r"function\s+cancelMigrate\(\)\s*\{(.*?)\n\}", _PAGE, re.S)
    assert cancel and "terminal" in cancel.group(1)
    poll = re.search(r"function\s+pollCancelStatus\(\)\s*\{(.*?)\n\}", _PAGE, re.S)
    assert poll and re.search(r"applyStatus", poll.group(1))

def test_reset_and_overflow_handlers_resync() -> None:
    """#11/T2 契约:reset/overflow 事件 → 重读 GET 状态并重订(不静默丢弃)。"""
    for t in ("reset", "overflow"):
        assert re.search(rf'ev\.type\s*===\s*["\']{t}["\']', _PAGE), f"页面须处理 {t} 事件"

def test_disconnect_banner_cleared_on_message() -> None:
    """#12:收到任意新事件即清除断线横幅。"""
    assert re.search(r'banner-disconnect["\']\)\.hidden\s*=\s*true', _PAGE)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_page_contract.py -v`
Expected: 新增 6 用例 FAIL(现状 onerror 无条件 close/无游标/无模型/reset 溢出无处理)。

- [ ] **Step 3: 实现(index.html 脚本段重构)**

要点(与既有函数的映射):
- `openEvents(jobId, handlers)` 重构:URL 增 `?last_event_id=<jobModel.lastSeq>`;onmessage 首行清除断线横幅+`jobModel.lastSeq = ev.seq`+`sessionStorage.setItem("mcmig.job", JSON.stringify({ job_id: jobId, seq: ev.lastSeq... }))`;分派 switch 增 `notice`(提示区追加一行)/`warning`(warn 条)/`reset`/`overflow`(两者:关闭当前流→`fetchStatus()` 重同步→若未终态重订);onerror: `if (jobModel.terminal) return;` 仅置横幅「连接中断,正在自动重连…」**不 close**。
- `applyEvent(ev)`:phase→model.phase;file→model.progress(+failedFiles 采集);done→model.summary/reminder/cancelled/status(按 summary.failed/cancelled 归一);error→model.error/status="failed";notice/warning→model.notices 追加。
- `applyStatus(body)`:`if (body.revision < jobModel.lastSeq) return;`(迟到状态不回退模型);字段直映射(status/progress/summary/results/error)。
- `render()`:现有 onFileEvent/onMigrateDone/plan-phase 的 DOM 写入全部改经模型派生;终态渲染自 model.status 分支(succeeded/partial_failed/failed/cancelled)。
- `startMigrate/startPlan`:POST 成功即 `jobModel.jobId=body.job_id; jobModel.status="running"`;handlers 装配提取为共享函数 `migrateHandlers()`/`planHandlers()`(自现 startMigrate/startPlan 的内联对象字面量提出,恢复路径与正常路径共用同一份分派);`init()` 首行增游标恢复:

```javascript
  var saved = null;
  try { saved = JSON.parse(sessionStorage.getItem("mcmig.job") || "null"); } catch (e) {}
  if (saved && saved.job_id) {
    fetch("/api/jobs/" + encodeURIComponent(saved.job_id))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (body) {
        if (!body) { sessionStorage.removeItem("mcmig.job"); return; }
        jobModel.jobId = saved.job_id;
        jobModel.kind = body.kind; applyStatus(body); render();
        if (!jobModel.terminal) { openEvents(saved.job_id, migrateHandlers()); }
      });
  }
```

- `cancelMigrate()`:入口 `if (jobModel.terminal) { return; }`;`pollCancelStatus()` 终态分支改 `applyStatus(body); render(); $("btn-cancel").hidden = true;`。
- 页面文案集中为 `var PAGE_STRINGS = {...}`(PHASE_LABELS/BEHAVIOR_LABELS/STATUS_LABELS 及新增横幅/提示文案并入;引用处逐个替换)。
- 既有 test_page_contract 用例(plan_id/persisted/identical 前提/hidden 优先/shutdown 文案)必须保持通过——重构不得删这些语义特征。

- [ ] **Step 4: 全量回归 + Commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`

```bash
git add migration/gui/index.html tests/test_page_contract.py
git commit -m "feat(w3): 页面 Presentation Model+SSE/GET 统一消费+自动重连+刷新游标+取消迟到收口 (batchI-W3-T5)"
```

---

### Task 6: 审阅页 IA 重构——diff 摘要并入+决策视角+渲染性能(spec T7)

**Files:**
- Modify: `migration/pipeline.py`(build_plan 4 元组+review extras 计算)
- Modify: `migration/cli.py`(_cmd_plan/_cmd_swap 解包 4 元组)
- Modify: `migration/gui/server.py`(plan job done 载荷增 diff 摘要+guard_scope)
- Modify: `migration/gui/STRINGS.py`(review.scope_note/摘要区文案)
- Modify: `migration/gui/index.html`(②审阅页重构;btn-back1 移位;懒建行;rAF 合批)
- Test: `tests/test_pipeline.py`、`tests/test_gui_server.py`、`tests/test_page_contract.py`

**Interfaces:**
- Consumes: T5 的 jobModel/render;`ModPair.to_dict()`(moddb);`match_client_only_paths(report, ctx, modids, families)`;`resolve_diff_context(src_snap, dst_snap)`;`world_rename_notices(src, dst)`;`plan.summary()`。
- Produces:
```python
# pipeline.build_plan 返回 4 元组(白名单⑦):
#   (plan, compat_warnings, mod_pairs, extras: dict)
# extras = {"buckets": plan.summary(),                    # origin 计数(决策视角)
#           "total_bytes": int, "ask_count": int,         # 体积/待确认数
#           "client_only": sorted[str],                    # 中性信息色呈现
#           "world_notices": [str]}                        # 世界目录提示
# GUI plan job done 事件增键:
#   "diff": extras + {"mod_pairs": [...ModPair.to_dict()],
#                     "compat_warnings": [str...],
#                     "guard_scope": STRINGS["review.scope_note"]}   # §3.3 检测范围说明(#15)
```

- [ ] **Step 1: 写失败测试(服务端载荷)**

```python
# tests/test_gui_server.py 追加
def test_plan_done_carries_diff_summary(tmp_path, monkeypatch):
    """spec §5.1/T7:done 载荷带 diff 摘要——桶计数/体积/待确认/mod 配对/
    compat 警示(不再只进 stderr 日志)/client_only/世界提示/检测范围说明。"""
    game, client = _make_client(tmp_path, monkeypatch)
    (game / "versions" / "src" / "mods").mkdir()
    (game / "versions" / "src" / "mods" / "clientish-1.0.jar").write_bytes(b"jar")
    job = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    done = _wait_job_done(client, job)[-1]
    diff = done["diff"]
    for key in ("buckets", "total_bytes", "ask_count", "mod_pairs",
                "compat_warnings", "client_only", "world_notices", "guard_scope"):
        assert key in diff, f"done.diff 缺 {key}"
    assert diff["buckets"]["must_migrate"] >= 1 and diff["guard_scope"]

# tests/test_pipeline.py 追加
def test_build_plan_returns_review_extras(tmp_path):
    """白名单⑦:4 元组第 4 位=extras(buckets/total_bytes/ask_count/client_only/world_notices)。"""
    plan, compat, pairs, extras = build_plan(...)          # 既有夹具
    assert set(extras) >= {"buckets", "total_bytes", "ask_count",
                           "client_only", "world_notices"}
```

```python
# tests/test_page_contract.py 追加
def test_review_page_summary_and_collapsed_sections() -> None:
    """spec §5.1:②审阅页顶部摘要区(数量/体积/待确认/将覆盖/阻断)+按需折叠
    (mod 配对/相同文件/目标独有/不迁移原因/client_only/世界提示);client_only
    用中性信息色(不得用警示色类)。"""
    assert re.search(r'id="review-summary"', _PAGE)
    for sec in ("mod_pairs", "client_only", "world_notices"):
        assert sec in _PAGE
    m = re.search(r'client_only[\s\S]{0,400}?class="[^"]*info[^"]*"', _PAGE)
    assert m and "warn" not in m.group(0)

def test_group_rows_lazy_built() -> None:
    """#24:折叠组行 DOM 懒创建——行构建收拢为独立函数且仅在展开时调用。"""
    assert re.search(r'function\s+buildGroupBody\(', _PAGE)
    head = re.search(r'function\s+renderPlan\(\)[\s\S]*?\n\}', _PAGE)
    assert head and "appendChild(buildGroupBody" not in head.group(0)

def test_file_events_batched_by_animation_frame() -> None:
    """#24:file 事件 DOM 更新经 requestAnimationFrame 合批(不逐事件强制回流)。"""
    assert "requestAnimationFrame" in _PAGE

def test_back_button_reachable_on_plan_failure() -> None:
    """#14:btn-back1 不再困在 #plan-body 内——plan 失败(错误条)时仍可返回步①。"""
    assert not re.search(r'id="plan-body"[\s\S]{0,600}?id="btn-back1"', _PAGE), \
        "btn-back1 须在 plan-body 之外(或错误分支另有返回入口)"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gui_server.py tests/test_pipeline.py tests/test_page_contract.py -k "diff_summary or review_extras or summary_and_collapsed or lazy_built or animation_frame or back_button" -v`
Expected: FAIL(done 无 diff 键/3 元组/页面无摘要区与懒建行)。

- [ ] **Step 3: 实现(pipeline 4 元组 → server 载荷 → 页面)**

- pipeline.build_plan:在 `report` 计算后(mod_pairs 之后、issue_review 之前)增:

```python
    # 审阅摘要数据(批次I-W3 T6,spec §5.1):client_only 匹配与 run_diff 同一
    # 单点——ctx 经 resolve_diff_context 按快照记录的 game_root 解析(语义一致)
    from .moddb import load_client_mods

    client_modids, client_families = load_client_mods()
    client_only: set[str] = match_client_only_paths(
        report, resolve_diff_context(src_snap, dst_snap),
        client_modids, client_families)
    extras: dict[str, object] = {
        "buckets": None,          # 占位:plan 生成后回填 plan.summary()
        "total_bytes": sum(
            a.src_size or 0 for a in plan.actions
            if a.behavior in (Behavior.COPY, Behavior.ASK)),
        "ask_count": sum(1 for a in plan.actions if a.behavior == Behavior.ASK),
        "client_only": sorted(client_only),
        "world_notices": world_rename_notices(src_snap, dst_snap),
    }
```

`plan = Planner(...)` 之后回填 `extras["buckets"] = plan.summary()`;返回 4 元组。docstring Returns 段同步。cli.py 两处解包(`plan, compat_warnings, _pairs =` → `plan, compat_warnings, _pairs, _extras =`)与测试夹具机械更新(conftest/test_e2e/test_pipeline 共约 10 处;T1 的 `_legacy_layout` 亦在其中)。
- server `_run_plan_job` done 事件增 `"diff": {**_extras, "mod_pairs": [p.to_dict() for p in _pairs], "compat_warnings": [str(w) for w in compat_warnings], "guard_scope": STRINGS["review.scope_note"]}`;STRINGS 增 `review.scope_note`(固定文案:已哈希条目全量校验源/目标状态;bulk/mods 代理条目在其 size+mtime 范围内承诺;「目标不存在」亦为被记录状态——不承诺发现范围外的任何改动)。
- **③结束页备份位置(spec §5.1)**:migrate done 事件增 `"backup_dir": str(dst_root / "_conflict_backup")`(自 executor.BACKUP_DIR 派生,仅当 results 含 backed_up 时非空提示);页面终态渲染备份位置行+恢复指引(「被覆盖文件的原件在 <backup_dir>,需要找回请到该目录」)。
- 页面②重构:顶部 `#review-summary` 摘要条(待迁移 N 项/约 X MB/待确认 M/兼容警告 K;guard_scope 提示行);默认展开=ASK 组+compat 警示;折叠区:mod 配对表(⇄→中文映射:upgrade=升级/renamed=改名/rebuilt=重打包,`content_differs` →「⚠ 上游重打包」)、client_only(中性 info 色)、世界提示、目标独有/不迁移原因/相同文件;`buildGroupBody(g)` 独立函数,组头展开事件首次调用并缓存(懒建行);file 事件更新收拢进 rAF 回调(`scheduleProgressRender()` 去重合批);`btn-back1` 移出 `#plan-body`(置于 step2 section 直下,plan-progress/错误态均可见)。
- 既有用例回归:`DEFAULT_EXPANDED`/搜索过滤/ask 勾选语义不变(test_page_contract 既有断言全部保持)。

- [ ] **Step 4: 全量回归 + Commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`

```bash
git add migration/pipeline.py migration/cli.py migration/gui/server.py migration/gui/STRINGS.py migration/gui/index.html tests/
git commit -m "feat(w3): 审阅页 IA 重构——diff 摘要并入/术语中文化/client_only 中性色/懒建行+rAF 合批 (batchI-W3-T6)"
```

---

### Task 7: swap 编排下沉+两阶段 API+覆盖备份+journal(spec T8)

**Files:**
- Modify: `migration/pipeline.py`(swap_preflight/swap_install/run_swap 自 cli.py 搬入+备份+journal)
- Modify: `migration/cli.py:403-553`(_cmd_swap 薄壳化;journal job_id)
- Modify: `migration/gui/server.py`(POST /api/swap/preflight、POST /api/swap job;preflight_id 指纹仓)
- Modify: `migration/gui/STRINGS.py`(swap 文案)
- Test: `tests/test_pipeline.py`、`tests/test_cli.py`、`tests/test_gui_server.py`

**Interfaces:**
- Consumes: `read_neoforge_version/scan_mods/check_version_range`(moddb);`copy_atomic(src, dst, *, rel, backup_dir)`(fsops);`JobJournal`(T3 新签名);`instance_locks`(调用方持锁约定)。
- Produces(T8 页面/W4 依赖):
```python
# pipeline.py
@dataclass(frozen=True)
class SwapPreflightOutcome:
    error: str | None                 # 致命错(缺版本 json/缺快照)→ CLI rc=2 文案
    incompat: list[str]               # NeoForge 不兼容清单
    extras: list[str]                 # 目标有而新包无的 jar
    conflicts: list[str]              # 同名不同内容(待覆盖决策)
    preflight_id: str | None          # 输入指纹(sha256,仅只读预检产出)
@dataclass(frozen=True)
class SwapInstallOutcome:
    copied: int; skipped: int; conflicted: int
    backed_up: list[str]              # 被覆盖前备份的 jar 名单
    backup_dir: Path | None
    cancelled: bool = False           # 逐 jar 取消检查点命中(spec §4.3 覆盖 swap 装包)
def swap_preflight(game_root: Path, dst: str, new_pack: Path, *,
                   src: str, legacy_dir: Path | None = None) -> SwapPreflightOutcome
def swap_install(dst_mods: Path, new_mods_dir: Path, *, overwrite: set[str],
                 dry_run: bool, backup_root: Path,
                 journal: JobJournal | None = None,
                 should_cancel: Callable[[], bool] | None = None) -> SwapInstallOutcome
    # 逐 jar:取消检查点命中即停(安全边界=当前 jar 已完成),cancelled=True
def run_swap(cwd, game_root, src, dst, new_pack, *, confirm_extras: Callable[[list[str]], bool],
             resolve_conflict: Callable[[str], bool], force: bool = False,
             dry_run: bool = False) -> tuple[int, SwapInstallOutcome | None]
    # CLI 全流程编排(预检→装包→重扫规划);返回 (退出码等价, 装包结果|None)
    # 输出契约:调用方(CLI)打印过程行;本函数只回结构化结果——CLI 对拍基准=
    # 决策语义与退出码,新增备份行为允许一行备份位置输出(spec §5.2)
# gui/server.py
#   POST /api/swap/preflight {src,dst,new_pack} → 202 {job_id}(只读 job;done 载荷=
#     SwapPreflightOutcome 序列化+备份位置预告)
#   POST /api/swap {preflight_id, src, dst, accept_incompat, accept_extras,
#                   overwrite_jars:[...]} → 202 {job_id}(装包 job,kind="swap" 可取消;
#     apply 前重算指纹失配 → error 事件 code=swap_inputs_changed;装包持 journal;
#     done 后引导重新 plan)
#   preflight 指纹 = sha256(src|dst|new_pack 解析路径 | 两侧 mods jar 名+MD5 清单 |
#     决策参数面)——服务端进程内 preflight_id → 指纹仓(仅保留最近 10 条,防无界增长)
#   Job.can_cancel 判定改 kind ∈ {"migrate", "swap"}(既有 migrate 用例不受影响)
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline.py 追加(新节)
def _pack_with_jars(tmp: Path, names: dict[str, bytes]) -> Path:
    pack = tmp / "newpack"; (pack / "mods").mkdir(parents=True)
    for n, b in names.items():
        (pack / "mods" / n).write_bytes(b)
    return pack

def test_swap_install_backs_up_overwritten_jars(tmp_path):
    """spec §5.2:同名不同内容覆盖前,目标 jar 备份至 backups/swap/<时间戳>/。"""
    dst_mods = tmp_path / "dst" / "mods"; dst_mods.mkdir(parents=True)
    (dst_mods / "a-1.0.jar").write_bytes(b"OLD")
    pack = _pack_with_jars(tmp_path, {"a-1.0.jar": b"NEW"})
    out = swap_install(dst_mods, pack / "mods", overwrite={"a-1.0.jar"},
                       dry_run=False, backup_root=tmp_path / "backups")
    assert (dst_mods / "a-1.0.jar").read_bytes() == b"NEW"
    assert out.backed_up == ["a-1.0.jar"]
    assert (out.backup_dir / "a-1.0.jar").read_bytes() == b"OLD"

def test_swap_journal_records_intent_per_jar(tmp_path):
    """spec §5.2/§4.3:装包过程 write-ahead——每 jar 意图(含备份位)→完成;
    崩溃窗口重启经 scan_interrupted 可核(spec §9 swap 断言)。"""
    journal = JobJournal(tmp_path / "jobs", "swapjob", "swap",
                         src="s", dst="d", game_root=str(tmp_path))
    ...  # swap_install 带中途中断注入(monkeypatch copy_atomic 第二次抛模拟崩溃)
         # 断言 journal 文件含未完成 intent;scan_interrupted 报 kind=swap

def test_swap_run_cli_equivalent(tmp_path):
    """CLI 对拍基准:run_swap 决策语义与退出码等价(预检失败=2/确认拒绝=0/
    成功=0);新增备份行为允许(输出差异仅备份位置行)。"""
    ...  # 三分支:不兼容且未 force→rc 2;confirm_extras 返回 False→rc 0 零装包;
         # 正常→rc 0 且 copied 计数正确

def test_swap_apply_rejects_changed_inputs(tmp_path, monkeypatch):
    """两阶段指纹重校验:preflight 后篡改目标 mods/(新增 jar)→ apply 拒
    (swap_inputs_changed),零写盘。"""
    ...  # GUI 两端点形态:preflight job done 取 preflight_id → 篡改 →
         # POST /api/swap → error 事件 code=swap_inputs_changed
```

test_cli.py 既有 swap 用例保持通过(对拍基准;装包统计/退出码不变);新增 `test_cli_swap_prints_backup_location`(覆盖发生时输出含 `backups/swap/` 路径行)。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline.py -k "swap" -v`
Expected: FAIL(`swap_install`/`swap_preflight`/`run_swap` 不在 pipeline)。

- [ ] **Step 3: 实现 pipeline 侧**

- `_swap_preflight`/`_md5` 自 cli.py 搬入(逻辑不改);`swap_preflight` 包装:加 src 快照存在性检查(cli.py:481-485 同语义)+ 指纹计算:

```python
def _swap_fingerprint(game_root: Path, src: str, dst: str, new_pack: Path,
                      dst_mods: Path, new_mods: Path) -> str:
    """两阶段输入指纹:参与决策的全部输入(版本对+路径+两侧 jar 名与 MD5)→ sha256。"""
    h = hashlib.sha256()
    h.update(f"{src}|{dst}|{new_pack.resolve()}".encode("utf-8"))
    for jar in sorted(dst_mods.glob("*.jar")):
        h.update(f"D|{jar.name}|{_md5(jar)}".encode("utf-8"))
    for jar in sorted(new_mods.glob("*.jar")):
        h.update(f"N|{jar.name}|{_md5(jar)}".encode("utf-8"))
    return h.hexdigest()
```

- `swap_install`:resolver 参数改 `overwrite: set[str]`(决策预收集——CLI 由 Confirm 回调收集,GUI 由勾选收集);覆盖路径 `copy_atomic(jar, target, rel=jar.name, backup_dir=backup_root)`;backup_root=`game_root/.mcmig/backups/swap/<UTC时间戳>`(调用方传入);journal 挂点:`record_intent(jar.name, {"op": "install", "backup": str(备份相对位) or None})` → copy → `record_completion`;identical 跳过不记意图(零写盘);`should_cancel` 逐 jar 检查(命中即停,`cancelled=True`,与 executor 取消同型安全边界)。
- `run_swap`:承接 _cmd_swap 编排(锁内调用约定写入 docstring:调用方须持 instance_locks(game_root, src, dst));交互经 confirm_extras/resolve_conflict 回调;装包 job_id=`cli-<UTC>-<pid>` 建 journal;重扫规划段 build_plan(modpack_swap=True, rescan_dst=True)同参搬移;返回 (rc, outcome)。`_cmd_swap` 改薄壳:解析参数→instance_locks→run_swap(回调=rich Confirm)→按返回值打印过程行(文案与现输出逐字对拍,备份位置行新增)。
- server 两端点:`POST /api/swap/preflight` job(只读;done=SwapPreflightOutcome+`backup_preview=str(<game_root>/.mcmig/backups/swap)`);指纹仓 `app.state.swap_preflights: dict[str, dict]`(preflight_id→{fingerprint, src, dst, new_pack};插入后仅保留最近 10 条);`POST /api/swap` job(kind="swap",`Job.can_cancel` 判定扩为 `kind in ("migrate", "swap")`):apply 前重算指纹失配→error(`swap_inputs_changed`,STRINGS);accept_incompat=False 且有 incompat→error;accept_extras 同理;overwrite_jars 必须 ⊆ conflicts(越界→422);装包 swap_install+journal(job.id,src/dst/game_root 全身份)+should_cancel(查 job.status=="cancelling");取消终态复用 migrate 的 cancelled 收尾口径(done 事件 cancelled:true+已完成/未完成 jar 清单);done 引导「重新生成迁移计划」。

- [ ] **Step 4: 全量回归 + Commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`

```bash
git add migration/pipeline.py migration/cli.py migration/gui/server.py migration/gui/STRINGS.py tests/
git commit -m "feat(w3): swap 编排下沉 pipeline+两阶段 API+覆盖备份+装包 journal (batchI-W3-T7)"
```

---

### Task 8: swap 页面(两阶段交互+边界文案)(spec T9)

**Files:**
- Modify: `migration/gui/index.html`(步① 增「整合包换包」次级入口+swap 面板;复用 T5 jobModel)
- Modify: `migration/gui/STRINGS.py`(边界文案)
- Test: `tests/test_page_contract.py`

**Interfaces:**
- Consumes: T7 两端点(POST /api/swap/preflight → job → done 载荷;POST /api/swap);T5 applyEvent/render。
- Produces: 页面 swap 流(入口→预检结果呈现(不兼容/新包外/冲突勾选)→应用→进度→完成(备份位置+边界文案+引导重新 plan))。

- [ ] **Step 1: 写失败测试(源码契约)**

```python
# tests/test_page_contract.py 追加
def test_swap_panel_two_phase_flow() -> None:
    """spec §5.2/T9:swap 面板两阶段——先 /api/swap/preflight 只读预检,
    呈现 incompat/extras/conflicts 三类决策,再 POST /api/swap 提交决策面。"""
    assert re.search(r'postJson\("/api/swap/preflight"', _PAGE)
    m = re.search(r'postJson\("/api/swap",\s*\{([^}]*)\}', _PAGE)
    assert m
    for key in ("preflight_id", "src", "dst", "accept_incompat", "accept_extras",
                "overwrite_jars"):
        assert re.search(rf"\b{key}\s*:", m.group(1)), f"swap 请求体缺 {key}"

def test_swap_boundary_copy_present() -> None:
    """spec §5.2:边界文案——确认装包后取消迁移不会自动撤销装包+备份位置指引。"""
    assert "不会自动撤销" in _PAGE and "备份" in _PAGE

def test_swap_done_guides_replan() -> None:
    """装包完成引导重新生成计划(rescan 后的 plan 才含新包状态)。"""
    assert re.search(r'重新生成(迁移)?计划', _PAGE)
```

- [ ] **Step 2: 跑测试确认失败** → FAIL(无 swap 面板)。

Run: `.venv/Scripts/python.exe -m pytest tests/test_page_contract.py -k swap -v`

- [ ] **Step 3: 实现**

步① section 增次级按钮 `#btn-swap-entry`(「整合包换包(进阶)…」)→ 切换 `#swap-panel`(hidden):新包目录输入(须含 mods/ 子目录,预检由服务端校验)、src/dst 沿用向导已选、`#btn-swap-preflight` → POST /api/swap/preflight → jobModel 订阅 → done 载荷渲染三类决策面(不兼容清单+`accept_incompat` 勾选、新包外残留清单+`accept_extras` 勾选、冲突 jar 多选=overwrite_jars)+预检指纹随载荷留存;`#btn-swap-apply` → POST /api/swap → 进度(file 事件复用 migrate 渲染)→ done:统计+**边界文案条**(STRINGS:确认装包后目标 mods/ 已被修改;此后取消迁移不会自动撤销装包;备份位于 `<backup_dir>`,可从中找回被覆盖 jar)+「返回向导重新生成迁移计划」按钮(回步①,提示先重扫)。返回按钮/新包输入校验失败态均有出口,不困死面板。

- [ ] **Step 4: 全量回归 + Commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`

```bash
git add migration/gui/index.html migration/gui/STRINGS.py tests/test_page_contract.py
git commit -m "feat(w3): swap 页面两阶段交互+边界文案+装包后引导重新规划 (batchI-W3-T8)"
```

---

### Task 9: pywebview 窗口壳(spec T10)

**Files:**
- Create: `migration/gui/app.py`(窗口壳:uvicorn 线程+webview 窗口+关闭边界+停机接线+降级)
- Modify: `pyproject.toml`(dependencies 增 `pywebview>=5.0`;`[project.scripts]` 增 `mcmig-gui = "migration.gui.app:main"`)
- Modify: `migration/cli.py`(_cmd_gui 增 `--window` 旗标转发 app.main)
- Test: `tests/test_gui_app.py`(新;不依赖 pywebview 安装)

**Interfaces:**
- Consumes: `create_app()`;`JobStore.is_busy()`/`active_id()`;`app.state.shutdown_requested`(T6 既有);`_cmd_gui` 现浏览器模式。
- Produces:
```python
# migration/gui/app.py
def pick_free_port() -> int                        # 127.0.0.1 随机端口(启动日志报出)
def build_url(port: int) -> str                    # http://127.0.0.1:<port>/
def should_block_close(store) -> tuple[bool, str | None]
    # 关闭边界:busy → (True, 当前 job 描述);空闲 → (False, None)(spec §5.3/T6)
def main(argv: Sequence[str] | None = None) -> int
    # ①import webview 失败 → 打印指引(安装链接)并**降级**调 _cmd_gui 浏览器模式
    # ②uvicorn 起后台线程(daemon=False,join 语义明确)→ webview.create_window
    #   (closing 事件=should_block_close 判定;阻止时窗口停留并提示当前任务)
    # ③轮询 app.state.shutdown_requested(页面「退出服务」)→ window.close → 进程收尾
    # ④退出码:正常 0;端口占用/uvicorn 启动失败 2(中文报错)
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_gui_app.py(新)
"""pywebview 窗口壳:关闭边界/停机接线/降级路径(pywebview 未安装不炸)。"""
import migration.gui.app as gui_app

def test_should_block_close_idle_vs_busy():
    """spec §5.3/T6:空闲放行;job 运行阻止并给出当前任务描述。"""
    from migration.gui.server import Job, JobStore
    store = JobStore()
    assert gui_app.should_block_close(store) == (False, None)
    job = Job("j1", "migrate"); store._current = job; store._jobs["j1"] = job
    blocked, desc = gui_app.should_block_close(store)
    assert blocked and desc

def test_main_falls_back_to_browser_without_pywebview(monkeypatch, capsys):
    """降级:webview 不可导入 → 打印安装指引并转浏览器模式(不抛 ImportError)。"""
    import builtins
    real_import = builtins.__import__

    def _no_webview(name, *a, **k):
        if name == "webview":
            raise ImportError("no webview")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_webview)
    called = {"rc": None}

    def _fake_gui(args):
        called["rc"] = 0
        return 0

    monkeypatch.setattr("migration.cli._cmd_gui", _fake_gui)
    rc = gui_app.main([])
    assert rc == 0 and called["rc"] == 0          # 已走浏览器模式回退
    assert "pywebview" in capsys.readouterr().out  # 安装指引可见

def test_pick_free_port_bindable():
    import socket
    port = gui_app.pick_free_port()
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.close()
```

(降级路径可测的前提:app.main 的回退分支**函数内** lazy import `from ..cli import _cmd_gui`——monkeypatch `migration.cli._cmd_gui` 才能生效,模块顶层 import 会绑死早期引用;实现Step 3 明确此约束。)

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gui_app.py -v`
Expected: FAIL(`No module named 'migration.gui.app'`)。

- [ ] **Step 3: 实现 app.py + 接线**

- `should_block_close(store)`: `store.is_busy()` → (True, f"任务 {store.active_id()} 正在执行");实现消费 server 的公开面,不 import cli 私有函数。
- `main`: `import webview`(函数内,未安装→ImportError 捕获→打印「未安装 pywebview,已回退浏览器模式: pip install pywebview / 或使用 mcmig gui」→ **函数内 lazy** `from ..cli import _cmd_gui` 转发浏览器启动,返回其退出码);成功路径:`app = create_app()` → `pick_free_port()` → `uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))` 后台线程 → `window = webview.create_window("mcmig 迁移向导", build_url(port))`;`window.events.closing += lambda: not should_block_close(store)[0]`(返回 False 阻止关闭;阻止时以 JS 求值提示 `window.evaluate_js(...)` 或标题提示,最简实现即可);停机轮询线程 0.5s 检查 `app.state.shutdown_requested` → `window.destroy()`;`webview.start()` 返回后停 uvicorn(server.should_exit=True)+join。
- pyproject:`dependencies` 追加 `"pywebview>=5.0"`;scripts 追加 mcmig-gui;**注意** `pip install -e ".[dev]"` 后本地补装 `pip install pywebview`(dev 组不加——测试零依赖它)。
- `_cmd_gui` 增 `--window` 参数→ `from .gui.app import main as window_main; return window_main()`。

- [ ] **Step 4: 全量回归 + Commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`

```bash
git add migration/gui/app.py pyproject.toml migration/cli.py tests/test_gui_app.py
git commit -m "feat(w3): pywebview 独立窗口壳——关闭边界/停机接线/WebView 缺失降级 (batchI-W3-T9)"
```

---

### Task 10: W3 收口(README/手测清单/版本 0.12.0/全量回归)

**Files:**
- Modify: `README.zh-CN.md`、`README.en.md`(swap GUI 化+独立窗口+两阶段;「plan 旧布局回退」精确为 CLI 侧行为(吸收 #6);journal 清理与 dismiss 说明)
- Modify: `tests/gui-manual-checklist.md`(增补 W3 手测项)
- Modify: `migration/__init__.py`、`pyproject.toml`(版本 0.12.0)
- Test: 无新增(全量回归即验收)

**Interfaces:**
- Consumes: T1-T9 全部。
- Produces: W4(分发闭环)计划的前置;文档与实际行为一致。

- [ ] **Step 1: README 双语更新**

- 「图形界面」小节:独立窗口 `mcmig-gui`(WebView2,缺失自动回退浏览器模式)与 `mcmig gui [--window]`;swap 两阶段(只读预检→确认应用)+覆盖备份位置说明;journal 中断记录位于 `<game_root>/.mcmig/jobs/`,页面可逐条 dismiss,已收尾记录自动清理。
- plan 旧布局回退表述精确化:该提示为 **CLI** 侧行为(GUI plan 恒重扫落锚定位)。英文版同义。

- [ ] **Step 2: 手测清单增补(tests/gui-manual-checklist.md)**

新增条目(每条含步骤与期望):①迁移中刷新页面→游标恢复(进度续显,不重扫)②断开 SSE(杀服务进程重启)→页面「正在自动重连」→恢复后清横幅 ③取消迁移→202 后任务恰好完成→界面呈现结果而非「正在停止」④大目录(≥5000 文件)计划渲染与迁移进度流畅(懒建行+rAF)⑤swap 全流程 GUI 两阶段(预检→篡改目标 mods/→apply 被拒;正常装包→备份就位→引导重新 plan)⑥独立窗口:job 运行中关窗被阻止并提示;空闲关窗直接退出;页面「退出服务」关窗 ⑦POSIX 后端 flock 互斥(CI/Linux 或 WSL 复核,win32 跳过注记)⑧旧布局快照用户 GUI 全链路(plan notice 提示→migrate 不误报)。

- [ ] **Step 3: 版本与全量回归**

`migration/__init__.py` 与 `pyproject.toml` 版本 → `0.12.0`;Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`;记录测试总数(预期 621 + 约 55~75,以实际为准,写入 commit body)。

- [ ] **Step 4: Commit**

```bash
git add README.zh-CN.md README.en.md tests/gui-manual-checklist.md migration/__init__.py pyproject.toml
git commit -m "docs(w3): README 双语 W3 能力+手测清单增补+版本 0.12.0 收口 (batchI-W3-T10)"
```

---

## 计划自审记录

1. **Spec 覆盖**: §5.1(T6,含③结束页备份位置与恢复指引)/§5.2(T7+T8,含逐 jar 取消——spec §4.3「取消检查点覆盖 swap 装包」)/§5.3(T9)/§5.4(滑移 W4 T15,处置表 #28)→ W3 四项全落;§4.1 沿挂(衔接协议页面侧=T5)/§4.3 沿挂(中断模型=T3)随行收口;吸收清单 28 项全部显式处置(26 修+1 接受强化+1 撤销过时);spec §3.3「检测范围向用户说明」在 T6 STRINGS 固定文案兑现;「两态展示」为 W4 更新面板条目(spec §5.1 明注「用于更新面板」),不入本波。
2. **占位符扫描**: 无 TBD;T3 Step1 executor 三用例以夹具+注入点文字说明(指名 mini_plan_dirs 与注入次数),与 W1W2 计划同粒度;所有步骤含可运行命令与预期输出。自审修正三处可执行性缺陷:T2 溢出用例原设计会挂起(生成器空订阅)→ 改 `_pump_sub` 拆层+确定性预占满队列;T3 探针用例两 journal 原同实例对会互相干扰 → 改不同实例对;T9 第三用例原截断 → 补全 socket bind 验证。
3. **类型一致性**: `PrecheckOutcome`(T1)字段与 CLI/GUI 消费一致;`_Sub.dropped`/`_pump_sub(job, sub, *, reset, replay)`(T2)在 emit/泵送/退订三处一致;`JobJournal(journal_dir, job_id, kind, *, src, dst, game_root)`(T3)在 executor 挂点/CLI migrate/server migrate/swap_install/scan_interrupted 五处一致;`SwapPreflightOutcome/SwapInstallOutcome`(含 cancelled/should_cancel)在 run_swap 与两端点一致;`should_block_close(store)`(T9)与 main 的 closing 接线一致;T1 `_legacy_layout` 解包 3 元组、T6 升 4 元组时该 helper 在机械更新清单内。
4. **Review Focus ↔ 测试归属**: 六条分别锚定 T2×2/T1/T3/T7/T6 各失败测试,无空挂;T5 的页面接缝由 6 条源码契约测钉死(W1W2 教训①:UI 接缝是评审盲区)。
5. **顺序依赖**: T1→(T2,T3 可并行)→T5→T6→T7→T8→T9→T10;T4 独立(仅 instlock),可穿插;T2 的 overflow/reset 契约是 T5 页面处理的前提,T3 的 dismiss/unknown_progress 是 T5 横幅与 T10 手测的前提——已按此排列任务序。
