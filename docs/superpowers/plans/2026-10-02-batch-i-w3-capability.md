# 批次I-W3 实现计划:能力补全+可靠性收口(T1-T10,v4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **v2(2026-10-02):** 收编计划评审 2 P1+6 P2+3 建议(见「计划评审修订记录」);十任务结构不变。
>
> **v3(2026-10-02):** 收编计划评审第二轮 1 P1+5 P2+2 结果契约建议(见「计划评审修订记录(v3)」);十任务结构不变。
>
> **v4(2026-10-02):** 收编计划评审第三轮 1 P1+3 P2+2 T5 接线测试建议(见「计划评审修订记录(v4)」);十任务结构不变。

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
  8. 页面 SSE onerror 不再主动 close EventSource(交浏览器自动重连),断线文案改「正在重连」;收到任意新事件即清除断线横幅(T5);
  9. SSE 续订游标优先级从「query 参数 > Last-Event-ID 头」翻转为「**有效头 > query > 0**」(WHATWG 重连语义:自动重连以头携带最新游标,URL 残留旧 query 不得压回重放;query 仅服务显式恢复的首次请求)(T2,评审 v3 P2-2);
  10. swap job 装包阶段结束后**取消窗关闭**——此后 cancel 请求 409「装包已完成,正在重新规划,不可取消」,重扫+规划段不可取消(避免「done 带成功计划 vs GET 收口 cancelled」分歧)(T7,评审 v3 P2-3);
  11. 窗口关闭判定与拒绝新任务**原子化**:`JobStore.begin_shutdown()` 在锁内判定(忙→阻止;闲→置 draining);**draining 拒绝发生在 `JobStore.start()` 的同一临界区内**(StoreDraining 异常,端点映射 503)——封「检查为空闲→新迁移启动→窗口退出」竞争窗(T9,评审 v3 P2-5+v4 P2-2)。
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

## 计划评审修订记录(v2,2026-10-02 收编计划评审 2 P1+6 P2+3 建议)

评审者以 621 测试可收集为基线,对令牌类型/指纹/GET 载荷做了小型验证;全部发现经本仓源码逐项复核属实。处置:

| # | 评审发现 | 复核 | 处置 |
|---|---|---|---|
| P1-1 | T4 锁释放按 `isinstance(token, int)` 分派会破坏 Windows(句柄同为 int) | ✅ instlock.py:158-160 返回 `int(handle)` | **T4**:`_release` 改按 `_IS_WINDOWS` 平台分派(与 `_acquire` 同构);补 `test_windows_release_allows_cross_process_reacquire` 回归锚 |
| P1-2 | GUI 装包后回向导重新 plan(`modpack_swap=False`)会把旧包 jar 重新列为可迁移 | ✅ /api/plan 无换包参数;spec §5.2 原文即「装包→重扫+规划」 | **T7/T8**:apply job 装包后**同 job 链式** `build_plan(modpack_swap=True, rescan_dst=True)`,done 与 plan job 同形直入②审阅页;验收 `test_swap_apply_chains_modpack_swap_replan`(装包→审阅→迁移,断言旧包 jar 不回迁);页面契约禁 swap 流内调用普通 /api/plan |
| P2-3 | swap 指纹未绑定 game_root 与目标版本 json(NeoForge 兼容判定输入) | ✅ 原函数 game_root 形参未入哈希 | **T7**:指纹并入 `game_root.resolve()`+`file_sha256(<dst>.json)`;apply 持锁后**三重重验**(指纹/仓内身份 vs 当前 ctx/兼容检查重跑);补切根/改 json 两拒绝用例+指纹单测 |
| P2-4 | GET 状态不含 plan/plan_id/persisted/diff,刷新恢复缺料;swap 预检同理 | ✅ snapshot() 仅 8 键;spec §4.1「results 含计划摘要…非仅计数」本就要求 | **T2**:emit 终态事件把完整载荷存 `_done_payload`(与状态收口同临界区),snapshot() 增 `"done"` 键——GET 与 SSE 消费同一份结果;**T5** 恢复路径按 kind 用 done 载荷重建(审阅页/终态/swap 决策页);测试三形态 |
| P2-5 | URL 游标与 Last-Event-ID header 冲突(query 优先,自动重连从旧位置重放);控制帧无 seq 会污染游标 | ✅ server query 优先属实;WHATWG 重连机制按 header | **T5** 游标四规则:正常订阅 URL 干净(重连走 header)/仅显式重订带游标/控制帧不推进+数值 seq 去重/GET 重同步后按 revision 续订;契约测+node 行为测双钉 |
| P2-6 | 取消迟到 202 仍可覆盖已收尾界面;旧任务回调污染新任务 | ✅ 计划原文仅在入口设防 | **T5**:`newJob` 代际重置+`ownsCurrent`;取消入口**与 202 回调**双守卫;全部异步回调提交 UI 前归属校验(≥5 处,契约计数);node 行为测 3 用例 |
| P2-7 | T9 只覆盖缺 Python 包;WebView2 缺失/MSHTML 静默回退/启动失败/线程回收未覆盖 | ✅ 页面依赖 EventSource,MSHTML 不可用 | **T9**:`webview.start(gui="edgechromium")` 显式现代渲染器(缺运行时=异常而非静默降级)→停线程+提示+浏览器模式;`wait_server_ready` 就绪后开窗;`verify_data_integrity` 清单自检(复用 doctor 校验段);`_start_server_thread/_stop_server` 可注入原语+失败路径回收测试 |
| P2-8 | dismiss 端点只查文件存在,可删活任务 journal(删档后重建残缺文件) | ✅ 端点无归属/活性校验 | **T3**:dismiss 删前重读+`_journal_owner_alive` 探针(与 scan 同一单点);活任务 409;跨进程持锁用例 |
| 建议 A | 页面正则契约证不了行为(迟到响应/去重/reset 恢复) | ✅ 但整页 jsdom 基建不成比例 | **采纳(收窄)**:T5 状态核设计为零 DOM 纯函数,`tests/test_page_behavior.py` 以 node harness 实际执行(去重/控制帧游标/迟到 202/代际/done 载荷 5 用例,skipif 无 node);DOM 渲染仍契约测+手测(④ 顺带验证搜索/ASK 勾选保持) |
| 建议 B | 现成测试代码三处缺陷:Popen 无 PIPE/T1 rename 无快照/T4 空断言;T6 extras 引用 plan 须后置 | ✅ 全部复现 | **已修**:T3/T4 Popen 补 `stdout=subprocess.PIPE`(T4 统一走 `_start_holder`);T1 夹具先 `scan_version` 建 anchored 再 rename;T4 删空断言(保留 fcntl/无 _stale_unlink/_pid_alive 三断言);T6 extras 移至 `plan = Planner(...)` 之后 |
| 建议 C | T6 摘要缺「将覆盖数/阻断问题」;端点名与 spec 不一致(/api/swap/apply);T10 杀服务应验收 journal 恢复 | ✅ spec §5.1 五要素/§5.2 端点名核对属实 | **已修**:extras 增 `overwrite_count`(backup_target 计数),摘要条五要素,阻断=compat 警示计数呈现;端点统一 `/api/swap/apply`;T10 手测 ② 拆为网络瞬断(自动重连)与服务重启(journal 中断恢复,不期待恢复内存 job) |

v2 结论:十任务结构保留;T2/T5 因终态载荷与代际守卫的依赖关系仍为前后序(T2→T5),其余不变。

---

## 计划评审修订记录(v3,2026-10-02 收编计划评审第二轮 1 P1+5 P2+2 契约建议)

评审者对 v2 复审,以 621 测试可收集为基线,做了源码对照、pywebview 官方源码核查(winforms.py 的
`is_chromium = not is_cef and _is_chromium() and forced_gui_ != 'mshtml'`)与 node harness 实测;全部
发现经本仓逐项复核属实,零误报。处置:

| # | 评审发现 | 复核 | 处置 |
|---|---|---|---|
| P1 | 新任务 POST 回调被 `ownsCurrent`(含 `!terminal`)拒绝:计划完成→执行迁移时当前 plan 已终态,迁移 POST 成功回调被丢弃而后台已开跑 | ✅ v2 计划 1160 行自相矛盾(「POST 成功即 newJob」与「回调先 ownsCurrent」并存) | **T5**:**请求归属与任务归属分离**——`requestSeq` 模块级计数器,`beginRequest()/requestAlive(gen)` 守 POST then/catch(发请求前取 gen);`ownsCurrent` 只守运行中任务的回调(SSE/取消/轮询);init() 恢复 fetch 同走 generation;+2 node 行为测试 |
| P2-2 | 显式恢复的 EventSource 仍带固定 `?last_event_id=N`,其自动重连时浏览器发新 header 但服务端 query 优先→反复重放/reset | ✅ server.py:1289-1297 query 优先属实;WHATWG 重连同原 URL | **T2**:服务端解析优先级改「**有效 Last-Event-ID 头 > query > 0**」(query 仅作首次显式恢复后备);+「恢复后再次断线」测试(同 URL 旧 query+新 header→按 header 续);既有 query-only/header-only 用例无一同时发两者,翻转不破坏 |
| P2-3 | swap 链式规划阶段取消边界未闭合:重扫/规划段取消仍被受理却无停止逻辑,可致 done 带成功计划 vs GET 收口 cancelled 的分歧 | ✅ v2 计划 1507-1508 只写「取消时跳过链式规划」,未关受理窗 | **T7**:装包循环结束处**原子判定**——命中取消→cancelled 收尾(跳过重规划);未命中→`job.cancel_closed=True` 关窗,此后 cancel 409,重扫+规划段不可取消;**T8** 页面 phase=replan 隐藏取消入口;+服务端测试+页面契约 |
| P2-4 | `gui="edgechromium"` 不保证缺 WebView2 抛异常:winforms 后端以 `_is_chromium()` 判可用性,不满足进 MSHTML 分支(仅 log.warning) | ✅ pywebview master 源码确证(guilib.py Windows 恒 import winforms;选择在 winforms 模块内) | **T9**:`selected_renderer(gui)` 经 `webview.guilib.initialize()` 预检**实际选中 renderer**,非 edgechromium(即 MSHTML)→提示+停线程+浏览器降级(**不进 webview.start**);start 抛错路径保留为二道防线;+测试 |
| P2-5 | 窗口壳生命周期接口不完整:`_start_server_thread(port)` 不收 app(关闭守卫/轮询/HTTP 服务可能异 JobStore);空闲检查与拒新任务非原子;`HTTPConnection(port)` 首参是 host(错位) | ✅ v2 计划 1741/1743 行属实 | **T9**:`_start_server_thread(app, port)`,`store = app.state.jobs` 单源;`JobStore.begin_shutdown()` 锁内原子判定+draining 后新 job 503;`HTTPConnection("127.0.0.1", port, timeout=…)`;+测试 |
| P2-6 | 新增验收测试可执行性缺陷:node harness 把 console.log 桩成空函数而测试靠它输出(实测 exit 0 空 stdout);T4 用例重复读 HELD(`_start_holder` 已消费;且该 helper 在计划中**根本未定义**);9 个 test 函数只有 docstring/省略号无断言(评审计 8,含旧 jar 不回迁核心验收) | ✅ 全部复现 | **已修**:harness 增 `emit(o)` 输出通道(测试改用 emit);T4 显式定义 `_holder_script/_start_holder`(HELD 由 helper 消费,用例不再复读);9 个空壳测试(3 executor+6 swap)全部补可执行体;`test_pick_free_port_bindable` 补断言 |
| 契约A | `run_swap` 只返回 (rc, 装包统计) 不足以维持 CLI 既有输出(预检/兼容警告/规划摘要/计划路径);装包成功后规划失败时用户无从知道目标已被修改 | ✅ 现 `_cmd_swap` 输出面实测含上述四类 | **T7**:返回形改 `SwapRunOutcome`(rc/preflight/install/compat_warnings/plan_summary/plan_file/plan_error),CLI 打印全自 outcome;规划失败分支输出装包统计+备份位置;GUI replan 失败的 error 载荷带 install+backup_dir;+2 测试 |
| 契约B | (并入契约A 的「规划失败须报装包状态」半) | — | 同上 |

v3 结论:十任务结构保留;修订集中在跨任务接口(T2↔T5 游标、T5 请求代际、T7↔T8 取消窗、T9 生命周期)与测试可执行性,核心编排不动。

---

## 计划评审修订记录(v4,2026-10-02 收编计划评审第三轮 1 P1+3 P2+2 T5 建议)

评审者对 v3 复审(独立交叉核查+内存探针复现交错),71 个可解析测试函数确认无空壳、621 测试可收集;全部发现经本仓源码与 pywebview 官方源码逐项复核属实。处置:

| # | 评审发现 | 复核 | 处置 |
|---|---|---|---|
| P1 | `selected_renderer` 用 `from webview import guilib` 取子模块——`webview/__init__.py` 模块级 `guilib = None`(start() 才赋值),首启 `.initialize()` 必抛 AttributeError;测试以 `fake.guilib` 掩盖了真实接口 | ✅ `webview/__init__.py` 源码确证:模块级 `guilib = None`+`start()` 内 `global guilib` 赋值(虽然 __init__ 顶部 `from webview.guilib import initialize` 已加载子模块,但同名属性被 None 覆盖) | **T9**:改 `from webview.guilib import initialize`(子模块函数导入,__init__.py 自身同款用法,不经过包属性);**预检自身抛异常**(如缺 pythonnet 的 WebViewException)同样走浏览器降级(try/except 包住,不崩);测试假件改为经 `sys.modules["webview.guilib"]` 注入(匹配真实接口),另补预检异常降级用例 |
| P2-2 | draining 判定在端点「提交前检查」仍是 TOCTOU:请求过检→关窗置 draining→原请求进 `JobStore.start()` 照常注册启动 | ✅ `JobStore.start()` 锁内仅查 `_current`,draining 判定若放端点必在锁外 | **T9**:`start()` 锁内先判 draining → 抛 `StoreDraining`(新异常;端点捕获映射 503 三段式;返回 None=占用 409 语义不变);`begin_shutdown` 与之同一把 `self._lock`——判定与注册同一临界区,窗封死;测试以「drain-then-start 交错注入」红绿判别(仅端点检查时该交错会 202) |
| P2-3 | swap 取消窗仍可被迟到请求穿透:取消端点在 job 锁**外**读 `can_cancel`(server.py:1316),读到允许→装包关窗→取消进锁内置 cancelling→重现「规划成功 vs cancelled」 | ✅ 现取消端点锁外 `if not job.can_cancel: 405`,锁内仅查 `done/_settled`(后者正是同类竞态的既有先例——修法对齐) | **T7**:取消端点在 `job._lock` 内**重检** `cancel_closed`(关→409 `err_cancel_closed` 三段式)与 `can_cancel`(不支持→405)——锁外只做 404;`Job.can_cancel` 降级为锁内复核的辅助,端点不得依赖锁外读;白盒用例(cancel_closed=True 的 swap job→409 且不置 cancelling)与既有 phase=replan 屏障用例双钉 |
| P2-4 | 四处新增验收测试验证不到预期分支:①replan 失败用例预检后才加 victim.jar(先触发指纹失配)且新包无同名 jar(无冲突可覆盖)②`done.plan.actions` 假设扁平结构(实为 `_group_actions` 按 origin 分组)③切根夹具只有 dst 无 src(端点版本校验先 422)④假 `create_window` 返回 None,`window.events.closing` 接线即 AttributeError,触不到 start() | ✅ 全部复核:`_group_actions` 返回 `{origin: {title,count,actions:[{path,origin,...}]}}`;apply 端点先做版本对目录校验 | **已修**:①victim.jar 两侧预检前创建(同名不同内容=冲突)+断言 `pf["conflicts"]` 含之;②`moved` 改遍历各组 `group["actions"]`;③切根夹具建完整版本对(src 带文件+dst 带 json);④假件改返回完整假窗口(`events.closing` 支持 `+=`、`destroy()`),并断言 start 确实被调用+降级确实发生 |
| T5 建议 | 原语测试不足以验证实际消费路径:计划终态后发起迁移的成功响应须确实订阅新 job;刷新恢复须「保存游标=终态 seq,GET done 完整恢复审阅页」 | ✅ 合理——beginRequest/requestAlive 目前只有原语级用例 | **T5**:页面侧把刷新恢复逻辑提取为零 DOM 的 `restoreSavedJob(saved)`(返回 Promise,init() 薄壳调用),node 行为测试以 fetch 桩走真实恢复路径(GET→applyStatus 消费 done 载荷,断言 planId/persisted/terminal 复原);新增 startMigrate 接线契约测试(beginRequest 先于 /api/migrate 请求、回调内 requestAlive→newJob→openEvents 链条齐备) |

v4 结论:十任务结构保留;本轮集中在「并发交错的原子性收尾」(draining/取消窗的锁内判定)与「测试对真实接口的贴合」(guilib 导入形态/分组载荷/假窗口),编排不变。

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
    from migration.pipeline import scan_version
    game, client = _make_client(tmp_path, monkeypatch)
    # _make_client 只建版本目录不产快照——先锚定扫描再 rename 到旧布局
    # (rename 保 mtime,不触发 snapshot_stale;评审建议 B:直接 rename 会 FileNotFoundError)
    anchored = game / ".mcmig" / "snapshots"
    scan_version(game, "src", anchored)
    scan_version(game, "dst", anchored)
    (tmp_path / ".mcmig" / "snapshots").mkdir(parents=True)
    for ver in ("src", "dst"):
        (anchored / f"{ver}.snapshot.json").rename(
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
# GET /api/jobs/{id}/events 续订游标解析(吸收 #3+评审 v3 P2-2):
#   query 参数声明改 str 手工解析;优先级 = **有效 Last-Event-ID 请求头 >
#   query 参数 > 0**(int 失败按缺省处理,消除 pydantic int 校验的 422 路径)。
#   header 优先的理由(WHATWG SSE 重连语义):浏览器自动重连回发**最新**游标
#   的 header,而 URL(含残留旧 query)原样重用——query 优先会让带旧游标的
#   显式恢复连接在每次自动重连时反复从旧位置重放/触发 reset;query 仅服务
#   「显式恢复的首次请求」(新建 EventSource 首次请求无 header)
# 终态载荷入 GET(评审 P2-4 服务端半):emit 终态事件时把**完整终态事件载荷**
#   存为 job._done_payload(与状态收口同一临界区);snapshot() 增 "done" 键
#   (终态事件原样,含 plan/plan_id/persisted/diff/summary/reminder/cancelled/
#   swap 预检载荷等)——GET 与 SSE 消费同一份结果,刷新恢复/迟到重读不再缺料
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

def test_sse_header_overrides_stale_query_on_reconnect(tmp_path, monkeypatch):
    """评审 v3 P2-2:「显式恢复连接(带旧 query)再次断线后自动重连」——URL
    仍带旧游标,浏览器回发新 header:须按 header 续订,不得被旧 query 压回
    重放(否则反复重放/反复 reset)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    events = _wait_job_done(client, job_id)
    seqs = [e["seq"] for e in events]
    assert len(seqs) >= 2                                  # phase+done 至少两帧
    with client.stream(
        "GET", f"/api/jobs/{job_id}/events?last_event_id={seqs[0] - 1}",
        headers={"Last-Event-ID": str(seqs[-2])},
    ) as resp:
        got = [json.loads(l[6:]) for l in resp.iter_lines() if l.startswith("data: ")]
    assert [e["seq"] for e in got] == seqs[-1:]            # 从 header 游标续,仅末帧

def test_status_endpoint_carries_full_done_payload(tmp_path, monkeypatch):
    """评审 P2-4:GET 终态含完整 done 载荷(plan/plan_id/persisted)——
    刷新恢复按 kind 重建页面的数据源,不再只有 status+计数(diff 键由 T6
    增补并在 T6 用例中断言)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    _wait_job_done(client, job_id)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "succeeded"
    done = body["done"]
    assert done["type"] == "done" and done["plan_id"] and done["persisted"] is True
    assert done["plan"]                                   # 审阅页可从 GET 重建
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gui_server.py -k "atomically or without_finish or overflowed or invalid_query or stale_query or done_payload" -v`
Expected: 6 FAIL(emit 不收口状态/无 dropped 属性/无 overflow 帧/abc 走 422/query 优先压回重放/GET 无 done 键)。

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
                # 终态原子提交(P2-4):状态收口+done 标志+终态载荷 与
                # revision/history/广播同一临界区——GET 的 snapshot() 与订阅
                # 重放永不再见到「running + revision 已含终态事件」的撕裂态;
                # _done_payload 让 GET 与 SSE 消费同一份终态结果(评审 P2-4)
                self._done_payload = ev
                self._settle_status_locked()
                self.done = True
```

`snapshot()` 返回 dict 增 `"done": self._done_payload`(终态事件原样;运行中为 None);`__init__` 增 `self._done_payload: dict | None = None`。

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
- 事件端点:`last_event_id: str | None = None`(query 声明改 str);解析优先级 =
  **有效 Last-Event-ID 请求头 > query 参数 > 0**(评审 v3 P2-2,白名单⑨):
  先读 header,`int()` 可解析即用;header 缺失/非法再看 query(同为 str 手工解析,
  非法按 0,吸收 #3);两者皆无 → 0。既有用例均为 query-only 或 header-only
  单独发送(test_gui_server.py:607/613/701),无一同时携带两者——优先级翻转
  不破坏既有测试;docstring 衔接协议段同步改写为「头优先,query 为显式恢复
  首次请求的后备」。
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
#   POST /api/jobs/interrupted/{job_id}/dismiss → 200 {"ok": true}(删除 journal 文件)
#     — 删除前重读该 journal 并做活性探针(复用 journal._journal_owner_alive):
#       探针失败(实例锁被持,本 app 或跨进程 CLI)=活任务 → 409(err_dismiss_live);
#       无身份(旧格式)/探针可获取=前任已死 → 删除;文件不存在 → 404(评审 P2-8)
# journal.py 探针提取为可复用单点:
#   def _journal_owner_alive(journal: JobJournal) -> bool
#     — 有身份(src/dst/game_root 齐备)→ instance_locks(Path(game_root), src, dst,
#       timeout=0.2) 获取失败即 True(有活进程持锁);无身份 → False(scan 与
#       dismiss 共用;scan_interrupted 内联逻辑抽此函数,判定语义不变)
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
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import time
            from migration.instlock import instance_locks
            with instance_locks({str(tmp_path)!r}, "C", "D"):
                print("HELD", flush=True); time.sleep(30)
        """)],
        stdout=subprocess.PIPE)                          # 评审建议 B:无 PIPE 则 readline 失败
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

def test_interrupted_dismiss_rejects_live_owner(tmp_path, monkeypatch):
    """评审 P2-8:活任务的 journal 不可 dismiss——跨进程持有实例锁(如正在
    运行的 CLI 迁移)时端点以 409 拒绝,防止删档后活任务重建出缺 start/意图
    的残缺 journal(恢复证据被削弱)。"""
    import subprocess, sys, textwrap
    game, client = _make_client(tmp_path, monkeypatch)
    jobs_dir = game / ".mcmig" / "jobs"
    jobs_dir.mkdir(parents=True)
    JobJournal(jobs_dir, "livejob", "migrate", src="src", dst="dst",
               game_root=str(game)).record_intent("a.txt", {"op": "copy"})
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import time
            from migration.instlock import instance_locks
            with instance_locks({str(game)!r}, "src", "dst"):
                print("HELD", flush=True); time.sleep(30)
        """)],
        stdout=subprocess.PIPE)
    try:
        assert holder.stdout.readline().strip() == b"HELD"
        resp = client.post("/api/jobs/interrupted/livejob/dismiss")
        assert resp.status_code == 409
        assert (jobs_dir / "livejob.jsonl").exists()    # 档案未被删除
    finally:
        holder.kill(); holder.wait()
```

```python
# tests/test_executor.py 追加(评审 v3 P2-6:三用例补齐可执行体,基于本文件
# 既有 _setup/_action/_plan/yes/no 辅助,不依赖 mini 夹具的具体文件清单)
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
        if _journal_owner_alive(journal):
            continue                    # 有活进程持锁(GUI 或跨进程 CLI)→ 非中断
        if not entries and not (journal.src and journal.dst and journal.game_root):
            continue                    # 旧格式无身份且无待核对:维持旧判据,不扩大
        items.append({"job_id": p.stem, "kind": journal.kind,
                      "src": journal.src, "dst": journal.dst,
                      "game_root": journal.game_root, "entries": entries,
                      "unknown_progress": not entries})
    return items
```

(探针本体提取为模块级 `_journal_owner_alive(journal) -> bool`——有身份才探针,`instance_locks(Path(game_root), src, dst, timeout=0.2)` 抛 InstanceLockError 即 True;scan 与 dismiss 端点共用同一判定,评审 P2-8。)

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
        """清除一条中断记录:确认前任已死后删除 journal 文件(#9 清除通道)。

        评审 P2-8:只查文件存在就删,活任务(CLI/GUI 正在写)的 journal 会被
        删档——其后续追加会重建出缺 start 与先前意图的残缺文件,恢复证据被
        削弱。删除前重读该 journal 并探针判活(与 scan_interrupted 同一单点
        journal._journal_owner_alive):锁被持=活 → 409,不删。
        """
        jobs_dir = app.state.wdir.jobs
        if jobs_dir is None:
            raise ApiError(422, "err_no_game_root.what", "err_no_game_root.why")
        for suffix in (".jsonl", ".json"):
            p = jobs_dir / f"{job_id}{suffix}"
            if p.exists():
                try:
                    journal = JobJournal(jobs_dir, job_id, "")
                except JournalError:
                    continue          # 损坏档案视同可清除(读不出即无恢复价值)
                if _journal_owner_alive(journal):
                    raise ApiError(409, "err_dismiss_live.what", "err_dismiss_live.why")
                p.unlink()
                return {"ok": True}
        raise ApiError(404, "err_job_not_found.what", "err_job_not_found.why")
```

(`_journal_owner_alive` 自 journal 模块 import;STRINGS 增 `err_dismiss_live.what/why`——「任务仍在执行」「该记录对应的迁移仍在运行(可能是其他窗口或命令行),完成后再清除」。)

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
# tests/test_instlock.py 追加(评审 v3 P2-6:_start_holder 此前未定义;现显式
# 定义且 HELD 信号由 helper 内部消费——用例不得复读,否则阻塞至 EOF 后失败)
import subprocess
import sys
import textwrap

def _holder_script(game_root, versions: tuple[str, ...], hold: float) -> str:
    """子进程脚本文本:持锁后打印 HELD 再睡 hold 秒(对齐「锁已到手」时机)。"""
    return textwrap.dedent(f"""
        import time
        from migration.instlock import instance_locks
        with instance_locks({str(game_root)!r}, {", ".join(map(repr, versions))}):
            print("HELD", flush=True); time.sleep({hold})
    """)

def _start_holder(script: str) -> subprocess.Popen:
    """启动持锁子进程,等到 HELD(锁已到手)才返回;HELD 由本函数消费。"""
    p = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE)
    assert p.stdout is not None and p.stdout.readline().strip() == b"HELD"
    return p

def test_posix_backend_uses_flock_no_stale_unlink():
    """#19/B1 双确认:POSIX 后端必须以 fcntl.flock 为互斥真源,_stale_unlink 的
    「读 pid→unlink」TOCTOU 模式不得存在(窗口内可互删他方活锁,两轮复审独立指出)。"""
    import inspect
    import migration.instlock as il
    src = inspect.getsource(il)
    assert "fcntl.flock" in src
    assert not hasattr(il, "_stale_unlink")   # 陈旧摘除逻辑整体消失(评审:删除空断言)
    assert not hasattr(il, "_pid_alive")      # pid 判定随陈旧接管一并消失

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 回退后端专属")
def test_posix_flock_two_process_exclusive(tmp_path):
    """flock 互斥:子进程持锁期间主进程获取失败(超时报 InstanceLockError)。"""
    p = _start_holder(_holder_script(tmp_path, ("A",), 3.0))   # HELD 已由 helper 消费
    with pytest.raises(InstanceLockError):
        with instance_locks(tmp_path, "A", timeout=0.5):
            pass
    p.kill(); p.wait()

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 回退后端专属")
def test_posix_flock_released_on_process_death_no_abandoned(tmp_path):
    """持有者死亡→内核释放 flock→等待方干净获取且 abandoned 恒空(语义变更:
    POSIX 侧中断证据只来自 journal,不再有陈旧接管标记)。"""
    p = _start_holder(_holder_script(tmp_path, ("A",), 30.0))  # HELD 已由 helper 消费
    p.kill(); p.wait()
    with instance_locks(tmp_path, "A", timeout=2.0) as info:
        assert info["abandoned"] == []
```

同型更新 test_abandoned_mutex_flagged 的 docstring:注明 Windows named mutex 路径的 abandoned 语义不变;0.5s 同步窗补注释(吸收 #4a:「等待方进入 WaitForSingleObject 前持有者必须仍持有,0.5s 为实测下界;此为并发等待形态的固有同步窗」)。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_instlock.py -k "flock_no_stale_unlink or windows_release" -v`
Expected: 前者 FAIL(`_stale_unlink` 仍存在,模块无 fcntl.flock);`test_windows_release_allows_cross_process_reacquire` 在改分派前即通过(现状正确路径的锚定用例,P1-1 修复过程中若破坏会变红)。POSIX 行为用例在 win32 跳过(实现须在 CI/Linux 复核,见 T10 手测清单注记)。

- [ ] **Step 3: 实现**

替换 POSIX 段(`_stale_unlink`/`_acquire_lockfile`/`_release_lockfile`/`_pid_alive` 整体重写,**`_pid_alive` 与 `_stale_unlink` 一并删除**——pid 判定不再参与互斥;`_lock_file_path` 保留):

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

`_release` 分派**按平台标志**(与 `_acquire` 的 `if _IS_WINDOWS:` 同构):`if _IS_WINDOWS: _release_mutex(token) else: _release_lockfile(token)`。**评审 P1-1:不得用 `isinstance(token, int)` 分派**——`_acquire_mutex` 返回的 Windows 句柄同样是 `int`(instlock.py:158-160 `int(handle)`),类型判别会把 Windows 误入 fcntl 释放路径;两后端令牌统一为 `int`(handle/fd),归属由创建它的平台分支决定,分派只认 `_IS_WINDOWS`。`instance_locks` docstring 与模块头 POSIX 段同步改写。`import fcntl` 置函数内(Windows 无该模块,模块级 import 会炸)。

补 Windows 释放回归测(评审 P1-1 要求):

```python
def test_windows_release_allows_cross_process_reacquire(tmp_path):
    """P1-1 回归:Windows 获取→正常释放→另一进程可再次获取(abandoned 空)——
    分派若误入 fcntl 路径,此用例在 win32 上即失败。"""
    p = _start_holder(_holder_script(tmp_path, ("A",), 0.3))   # 短持有后正常退出(释放)
    p.wait(timeout=5.0)
    with instance_locks(tmp_path, "A", timeout=2.0) as info:
        assert info["abandoned"] == []            # 正常释放≠abandoned
    # 主进程自身获取-释放-再获取(同进程路径)
    with instance_locks(tmp_path, "A", timeout=1.0):
        pass
    with instance_locks(tmp_path, "A", timeout=1.0):
        pass
```

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
- Test: `tests/test_page_contract.py`、`tests/test_page_behavior.py`(新,node 状态核 harness)

**Interfaces:**
- Consumes: T2 的 overflow/reset 事件契约、GET `/api/jobs/{id}` 状态快照(含 done 终态载荷)、`?last_event_id=` 续订。
- Produces(T6/T8 页面重构的地基):
```javascript
// 页面状态模型(Presentation Model,设计输入①):SSE 事件与 GET 状态两路
// 更新同一 jobModel,渲染一律自模型派生(请求乱序可直测——对模型断言)
var jobModel = { jobId: null, kind: null, status: "idle", phase: null,
                 progress: { index: 0, total: 0 }, summary: null, results: null,
                 error: null, cancelled: false, lastSeq: 0,
                 get terminal() { return ["succeeded", "partial_failed", "failed",
                                          "cancelled"].indexOf(this.status) >= 0; } };
function newJob(id, kind)   // 代际重置:jobId/kind/lastSeq/progress/summary 全清零
                            // (startPlan/startMigrate/swap 启动与恢复路径共用)
function applyEvent(ev)     // SSE 事件 → 模型;**先按 seq 去重**(typeof ev.seq ===
                            // "number" 且 ev.seq <= lastSeq → 丢弃;否则推进 lastSeq)
                            // reset/overflow 是无 seq 控制帧,分派层处理,不入此函数
function applyStatus(body)  // GET 状态快照 → 模型(revision 一致性:仅当
                            // body.revision >= lastSeq 采用;终态时消费 body.done
                            // 完整载荷——plan 恢复审阅页/migrate 恢复终态,按 kind)
function ownsCurrent(id)    // 任务归属校验:id === jobModel.jobId && !jobModel.terminal
                            // (只守**运行中任务**的回调:SSE 终态/取消/轮询)
var requestSeq = 0          // 请求代际(评审 v3 P1):POST 回调的归属凭据,与
                            // 任务归属分离——「计划完成→执行迁移」时当前任务已
                            // 终态,ownsCurrent 必拒,而后台可能已开始执行
function beginRequest()     // 发请求前调用:++requestSeq 并返回本次 gen
function requestAlive(gen)  // gen === requestSeq(发起新请求即废止旧请求回调)
function render()           // 模型 → DOM(全部展示逻辑唯一出口)
function restoreSavedJob(saved)  // 刷新恢复零 DOM 主体(评审 v4 T5 建议):
                                // GET /api/jobs/<id> → applyStatus 消费 done 载荷
                                // → 未终态按规则②带游标续订;返回 Promise,
                                // node 行为测试以 fetch 桩直测真实恢复路径,
                                // init() 仅薄壳调用(读 sessionStorage 后转发)
// 游标规则(评审 P2-5,四条钉死,与 WHATWG SSE 重连机制一致):
//   ① 正常订阅的 EventSource URL **不携带** last_event_id——断线后浏览器自动
//      重连按标准回发 Last-Event-ID 请求头,服务端以 header 续订;
//      (服务端解析已改**头优先**(T2,评审 v3 P2-2)——URL 若带旧游标也不再
//       压掉重连时更新的 header,但正常订阅仍保持干净 URL,语义最简)
//   ② 仅**显式重建订阅**(reset/overflow 后 GET 重同步、页面刷新恢复)才在
//      新 EventSource 的 URL 上带 ?last_event_id=<seq>(新建连接无 header,游标
//      由页面主动给出);该 query **仅作用于首次请求**,其后的自动重连由
//      header 主导(T2 头优先),不会反复触发重放(评审 v3 P2-2)
//   ③ 控制帧(reset/overflow,无 seq)不推进 lastSeq;普通事件按 seq 单调去重
//   ④ GET 重同步后以 body.revision 为准续订(applyStatus 已校 revision >= lastSeq)
// 回调归属双轨(评审 P2-6+v3 P1):
//   - **请求归属**:startPlan/startMigrate/swap 的 POST(及 init() 恢复 fetch)
//     在发请求前 beginRequest() 取 gen,then/catch 首行 `if (!requestAlive(gen))
//     return;`——**不得**用 ownsCurrent 守 POST 回调(终态任务后的新请求会被
//     误拒);成功回调内 newJob(body.job_id, kind) 切换任务模型
//   - **任务归属**:cancelMigrate 入口与 202 回调、pollCancelStatus 轮询、
//     SSE onDone/onError 提交 UI 前一律 ownsCurrent——旧任务的回调不得污染
//     新代际
// 恢复协议(设计输入②):onerror 不 close(浏览器自动重连);断线横幅改
//   「连接中断,正在自动重连…」,任意新事件到达即清除;
//   sessionStorage["mcmig.job"] = {job_id, seq} 仅在 lastSeq 推进时更新,页面
//   加载时恢复:GET 状态 → 终态则以 body.done 按 kind 重建页面(审阅页/执行
//   终态/swap 决策页),未终态则按规则②带游标续订
// PAGE_STRINGS:页面侧文案集中(设计输入/吸收 #6 页面半)
```

- [ ] **Step 1a: 写失败测试(源码契约)**

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
    """#7/设计输入②+评审 P2-5:游标写 sessionStorage;恢复走 GET→按 kind 重建;
    **正常订阅 URL 不带 last_event_id**(自动重连靠 Last-Event-ID 请求头),
    仅显式重订(reset/overflow/刷新恢复)带游标。"""
    assert re.search(r"sessionStorage\.setItem\([\"']mcmig\.job[\"']", _PAGE)
    assert re.search(r"sessionStorage\.getItem\([\"']mcmig\.job[\"']", _PAGE)
    resub = re.search(r'last_event_id=', _PAGE)
    assert resub, "显式重订路径须带游标"
    # 正常订阅不得把游标烧进 URL(否则自动重连时 query 压掉更新的 header)
    normal = re.search(r'new EventSource\(\s*"/api/jobs/"\s*\+\s*[^,)]+\s*\+\s*"/events"\s*\)', _PAGE)
    assert normal, "正常订阅应为无 query 的干净 URL"

def test_control_frames_do_not_advance_cursor() -> None:
    """评审 P2-5:reset/overflow 无 seq,不得推进 lastSeq;普通事件按 seq 去重
    (applyEvent 内 typeof ev.seq === "number" 门)。"""
    gate = re.search(r'typeof\s+ev\.seq\s*===\s*["\']number["\']', _PAGE)
    assert gate, "applyEvent 须以数值 seq 门控去重与游标推进"

def test_presentation_model_single_writer() -> None:
    """设计输入①:SSE 与 GET 两路都写 jobModel,DOM 更新走 render()。"""
    assert re.search(r"function\s+applyEvent\(", _PAGE)
    assert re.search(r"function\s+applyStatus\(", _PAGE)
    assert re.search(r"var\s+jobModel", _PAGE)
    assert re.search(r"function\s+render\(\)", _PAGE)
    assert re.search(r"function\s+newJob\(", _PAGE)          # 代际重置入口

def test_cancel_late_response_guarded() -> None:
    """#22+评审 P2-6:取消入口**与 202 响应回调内**都先 ownsCurrent 才写中间态;
    轮询见终态走 applyStatus+render 共用终态处理。"""
    cancel = re.search(r"function\s+cancelMigrate\(\)\s*\{(.*?)\n\}", _PAGE, re.S)
    assert cancel
    body = cancel.group(1)
    assert "ownsCurrent" in body and body.count("ownsCurrent") >= 2, \
        "取消入口与异步回调两处都须归属校验"
    poll = re.search(r"function\s+pollCancelStatus\(\)\s*\{(.*?)\n\}", _PAGE, re.S)
    assert poll and re.search(r"applyStatus", poll.group(1))

def test_async_callbacks_check_ownership() -> None:
    """评审 P2-6:全部异步回调提交 UI 前验证 ownsCurrent(旧任务回调不污染新代际)。"""
    assert _PAGE.count("ownsCurrent") >= 5, \
        "POST then/catch、轮询、SSE 终态等回调均须归属校验(≥5 处)"

def test_start_migrate_wires_request_generation_and_subscribe() -> None:
    """评审 v4 T5 建议:startMigrate 的实际接线——发请求前取 generation,成功
    回调内 requestAlive→newJob→openEvents 订阅新 job(计划终态后发起迁移的
    真实消费路径,原语级测试覆盖不到)。"""
    m = re.search(r"function\s+startMigrate\(\)\s*\{[\s\S]*?\n\}", _PAGE)
    assert m, "须有 startMigrate"
    body = m.group(0)
    assert "beginRequest()" in body and "/api/migrate" in body
    assert body.index("beginRequest()") < body.index("/api/migrate"), \
        "generation 须在发请求前捕获"
    for needle in ("requestAlive(", "newJob(", "openEvents("):
        assert needle in body, f"startMigrate 回调缺 {needle}"

def test_restore_saved_job_is_extracted_function() -> None:
    """评审 v4 T5 建议:刷新恢复主体为零 DOM 的 restoreSavedJob(可被 node
    行为测试直测),init() 薄壳调用。"""
    assert re.search(r"function\s+restoreSavedJob\(", _PAGE)
    init = re.search(r"function\s+init\(\)\s*\{[\s\S]{0,600}", _PAGE)
    assert init and "restoreSavedJob(" in init.group(0)

def test_reset_and_overflow_handlers_resync() -> None:
    """#11/T2 契约:reset/overflow 事件 → 重读 GET 状态并重订(不静默丢弃)。"""
    for t in ("reset", "overflow"):
        assert re.search(rf'ev\.type\s*===\s*["\']{t}["\']', _PAGE), f"页面须处理 {t} 事件"

def test_disconnect_banner_cleared_on_message() -> None:
    """#12:收到任意新事件即清除断线横幅。"""
    assert re.search(r'banner-disconnect["\']\)\.hidden\s*=\s*true', _PAGE)
```

- [ ] **Step 1b: 写失败测试(node 行为测试——状态核实际执行,评审建议 A 的落地范围)**

```python
# tests/test_page_behavior.py(新)
"""页面状态核行为测试:node harness 实际执行 index.html 的脚本段。

评审建议 A 的落地范围裁决:正则契约只能证明「函数存在」,证不了「迟到响应/
事件去重/reset 恢复」的行为——但为整页引 jsdom 级基建不成比例。折中:T5 的
状态核(applyEvent/applyStatus/newJob/ownsCurrent/cancel 迟到守卫)设计为
**零 DOM 依赖的纯函数**(render 才碰 DOM),harness 以最小 DOM/fetch/
EventSource 桩加载脚本(DOMContentLoaded 不触发→init 不接线,函数定义可用),
对状态核做确定性断言;render/DOM 仍归契约测+浏览器手测。

无 node 环境(git bash/CI 未装)自动跳过;node 存在时全平台可跑。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from importlib import resources

import pytest

_PAGE = resources.files("migration.gui").joinpath("index.html").read_text(encoding="utf-8")
_SCRIPT = _PAGE.split("<script>")[1].split("</script>")[0]

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node 不在 PATH,页面行为测试跳过")

# 最小桩:document/window/sessionStorage/console/fetch/EventSource
_HARNESS = r"""
globalThis.document = {
  getElementById: function () { return { style: {}, classList: { add(){}, toggle(){}, remove(){} },
    addEventListener(){}, appendChild(){}, removeChild(){}, querySelectorAll(){ return []; },
    dataset: {} }; },
  addEventListener: function () {},          // DOMContentLoaded 不触发:init 不接线
  createElement: function () { return { style: {}, classList: { add(){}, toggle(){}, remove(){} },
    appendChild(){}, addEventListener(){}, dataset: {} }; },
};
globalThis.window = globalThis;
globalThis.sessionStorage = { _s: {}, getItem(k){ return this._s[k] ?? null; },
  setItem(k, v){ this._s[k] = String(v); }, removeItem(k){ delete this._s[k]; } };
globalThis.console.log = function () {};        // 页面脚本日志静默(不污染 stdout)
globalThis.emit = function (o) {                // 测试结果输出通道(评审 v3 P2-6:
    process.stdout.write(JSON.stringify(o) + "\n");  // console.log 已被桩空,测试不得依赖它)
};
globalThis.EventSource = function () { return { close(){}, addEventListener(){} }; };
"""

def _run_js(program: str) -> dict:
    """在 node harness 中执行页面脚本+测试程序,回传 JSON 结果(emit 单通道输出)。"""
    full = _HARNESS + "\n" + _SCRIPT + "\n" + program
    out = subprocess.run([NODE, "-e", full], capture_output=True, text=True,
                         encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    assert lines, "harness 无输出:测试须以 emit() 回传结果"
    return json.loads(lines[-1])

def test_apply_event_dedups_by_seq():
    """P2-5:重复/乱序 seq 丢弃,游标只前进。"""
    r = _run_js(r"""
      newJob("j1", "migrate");
      applyEvent({type:"file", path:"a", index:1, total:9, status:"copied", seq:1});
      applyEvent({type:"file", path:"a", index:1, total:9, status:"copied", seq:1});  // 重复
      applyEvent({type:"file", path:"b", index:2, total:9, status:"copied", seq:0});  // 旧
      emit({seq: jobModel.lastSeq, idx: jobModel.progress.index});
    """)
    assert r == {"seq": 1, "idx": 1}

def test_control_frames_do_not_advance_cursor():
    """P2-5:无 seq 的事件(控制帧形态)应用但不推进游标——typeof 门控。"""
    r = _run_js(r"""
      newJob("j1", "migrate");
      applyEvent({type:"file", path:"a", index:1, total:9, status:"copied", seq:3});
      applyEvent({type:"notice", text:"控制帧形态,无 seq"});   // 无 seq:不推进
      emit({seq: jobModel.lastSeq});
    """)
    assert r["seq"] == 3

def test_cancel_late_202_does_not_overwrite_terminal():
    """P2-6:done 已到,迟到的取消回调不得把状态写回「正在停止」中间态。"""
    r = _run_js(r"""
      newJob("j1", "migrate");
      applyEvent({type:"file", path:"a", index:1, total:1, status:"copied", seq:1});
      applyEvent({type:"done", job_kind:"migrate", summary:{copied:1,failed:0}, seq:2});
      var terminalBefore = jobModel.terminal;
      var late = ownsCurrent("j1");          // 迟到回调的归属校验:终态即拒
      emit({t: terminalBefore, late: late});
    """)
    assert r["t"] is True and r["late"] is False

def test_stale_callback_cannot_pollute_new_generation():
    """P2-6:旧任务(id=j1)的回调在新代际(j2)下 ownsCurrent 失败。"""
    r = _run_js(r"""
      newJob("j1", "migrate");
      newJob("j2", "migrate");
      emit({stale: ownsCurrent("j1"), cur: ownsCurrent("j2")});
    """)
    assert r == {"stale": False, "cur": True}

def test_apply_status_consumes_done_payload_by_kind():
    """P2-4:GET 终态含 done 完整载荷——plan job 恢复审阅数据。"""
    r = _run_js(r"""
      newJob("j1", "plan");
      applyStatus({status:"succeeded", kind:"plan", revision:5,
                   done:{type:"done", job_kind:"plan", plan_id:"abc",
                         persisted:true, plan:{must_migrate:{count:1}}, seq:5}});
      emit({planId: jobModel.planId, persisted: jobModel.persisted});
    """)
    assert r == {"planId": "abc", "persisted": True}

def test_request_generation_not_blocked_by_terminal():
    """评审 v3 P1:终态任务后的新请求回调不被 ownsCurrent 拦截——请求归属
    (generation)与任务归属(ownsCurrent)分离,「计划完成→执行迁移」的
    POST 成功回调才能继续 newJob 并订阅新任务。"""
    r = _run_js(r"""
      newJob("j1", "plan");
      applyEvent({type:"done", job_kind:"plan", plan_id:"p", persisted:true, seq:2});
      var gen = beginRequest();
      emit({alive: requestAlive(gen), owns: ownsCurrent("j1")});
    """)
    assert r == {"alive": True, "owns": False}

def test_newer_request_invalidates_older_callback():
    """评审 v3 P1:发起新请求即废止旧请求的迟到回调(双击/快速切换任务)。"""
    r = _run_js(r"""
      var g1 = beginRequest();
      var g2 = beginRequest();
      emit({old: requestAlive(g1), cur: requestAlive(g2)});
    """)
    assert r == {"old": False, "cur": True}

def test_restore_saved_job_consumes_done_payload():
    """评审 v4 T5 建议:刷新恢复的真实消费路径——保存游标=终态 seq,GET 的
    done 载荷完整恢复审阅页数据(fetch 桩返回终态 plan job 快照)。"""
    r = _run_js(r"""
      globalThis.fetch = function () {
        return Promise.resolve({ ok: true, json: function () {
          return Promise.resolve({
            status: "succeeded", kind: "plan", revision: 7,
            done: {type: "done", job_kind: "plan", plan_id: "pf-1",
                   persisted: true, plan: {must_migrate: {count: 2}}, seq: 7}
          });
        } });
      };
      restoreSavedJob({job_id: "j9", seq: 7}).then(function () {
        emit({id: jobModel.jobId, planId: jobModel.planId,
              persisted: jobModel.persisted, terminal: jobModel.terminal,
              seq: jobModel.lastSeq});
      });
    """)
    assert r == {"id": "j9", "planId": "pf-1", "persisted": True,
                 "terminal": True, "seq": 7}
```

(实现侧配合:jobModel 增 `planId/persisted/diff` 字段,applyEvent 的 done 分支与 applyStatus 的 done 载荷分支同源填充——migrate 请求的 planId 取自模型而非闭包变量。)

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_page_contract.py tests/test_page_behavior.py -v`
Expected: 新增契约 10 用例+行为 8 用例 FAIL(现状 onerror 无条件 close/无游标/无模型/reset 溢出无处理/无 node 可执行的状态核函数与请求代际原语/无 restoreSavedJob 提取)。

- [ ] **Step 3: 实现(index.html 脚本段重构)**

要点(与既有函数的映射):
- 状态核零 DOM 依赖:`applyEvent/applyStatus/newJob/ownsCurrent` 只读写 jobModel 与 sessionStorage,不碰 document(node harness 可执行,建议 A 的落地前提);`render()` 是唯一 DOM 出口。
- `openEvents(jobId, handlers, opts)`:**正常订阅 URL 干净无 query**(游标规则①——自动重连由浏览器回发 Last-Event-ID 请求头,服务端以 header 续订;服务端解析已改**头优先**(T2,评审 v3 P2-2),URL 残留旧游标不再压回重放,但正常订阅保持干净 URL 语义最简);仅显式重订传 `opts.resumeSeq` 时拼 `?last_event_id=<seq>`(规则②;该 query 仅作用于首次请求)。onmessage:清断线横幅 → 控制帧(reset/overflow)分流到 `resyncAfterControl()`(关流→GET 状态→applyStatus→render→未终态按规则②带 `jobModel.lastSeq` 重订)→ 其余 `applyEvent(ev)` → appendLog。onerror: `if (jobModel.terminal) return;` 仅置横幅「连接中断,正在自动重连…」**不 close**。
- `applyEvent(ev)`:`if (typeof ev.seq === "number") { if (ev.seq <= jobModel.lastSeq) return; jobModel.lastSeq = ev.seq; sessionStorage.setItem("mcmig.job", ...) }`(规则③:无 seq 不推进;数值 seq 单调去重)后按 type 归入模型:phase→model.phase;file→model.progress(+failedFiles);done→model.planId/persisted/summary/reminder/cancelled/status 终态归一;error→model.error/status="failed";notice/warning→model.notices。
- `applyStatus(body)`:`if (body.revision < jobModel.lastSeq) return;`(迟到状态不回退);运行态字段直映射;**终态且 body.done 存在 → 按 done 载荷走 applyEvent 同源填充**(plan 恢复 planId/plan/diff,migrate 恢复 summary/reminder——GET 与 SSE 消费同一份终态结果,评审 P2-4)。
- `newJob(id, kind)`:jobId/kind/lastSeq/progress/summary/results/error/notices/planId/persisted/diff 全清零(代际重置);`ownsCurrent(id)` = `id === jobModel.jobId && !jobModel.terminal`。
- **回调归属双轨(评审 P2-6+v3 P1,不得混用)**:
  - 请求归属——startPlan/startMigrate/swap 的 POST 与 init() 恢复 fetch,**发请求前** `var gen = beginRequest();`,then/catch 首行 `if (!requestAlive(gen)) return;`(校验失败静默丢弃);成功回调内 `newJob(body.job_id, kind)` 切换任务模型。**禁止**对 POST 回调用 ownsCurrent——「计划完成→执行迁移」时当前任务已终态,ownsCurrent 必拒而后台已开跑(v3 P1 正是此坑);
  - 任务归属——cancelMigrate 的 202 then、pollCancelStatus 的 fetch then、SSE onDone/onError 提交 UI 前 `ownsCurrent(capturedJobId)`(id+非终态),校验失败即静默丢弃(旧代际回调)。
- `render()`:现有 onFileEvent/onMigrateDone/plan-phase 的 DOM 写入全部改经模型派生;终态渲染自 model.status 分支(succeeded/partial_failed/failed/cancelled)。
- handlers 装配提取为共享函数 `migrateHandlers()`/`planHandlers()`(自现 startMigrate/startPlan 的内联对象字面量提出,恢复路径与正常路径共用同一份分派);刷新恢复主体提取为**零 DOM 的 `restoreSavedJob(saved)`**(评审 v4 T5 建议:返回 Promise,node 行为测试可经 fetch 桩直测消费路径),`init()` 首行为薄壳(读 sessionStorage→转发):

```javascript
function restoreSavedJob(saved) {
  var gen = beginRequest();               // 恢复期间用户可能已发起新任务
  return fetch("/api/jobs/" + encodeURIComponent(saved.job_id))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (body) {
        if (!requestAlive(gen)) return;     // 旧恢复回调不覆盖新任务(v3 P1)
        if (!body) { sessionStorage.removeItem("mcmig.job"); return; }
        newJob(saved.job_id, body.kind);
        jobModel.lastSeq = saved.seq || 0;  // GET 前先立游标(applyStatus 的 revision 门用)
        applyStatus(body); render();        // 终态:done 载荷按 kind 重建页面
        if (!jobModel.terminal) {           // 未终态:规则②带游标续订
          openEvents(saved.job_id, migrateHandlers(), { resumeSeq: jobModel.lastSeq });
        }
      });
}
// init() 首行薄壳:
//   var saved = null;
//   try { saved = JSON.parse(sessionStorage.getItem("mcmig.job") || "null"); } catch (e) {}
//   if (saved && saved.job_id) { restoreSavedJob(saved); }
```

- `cancelMigrate()`:入口与 202 then 两处 `if (!ownsCurrent(currentMigrateJobId)) return;`;`pollCancelStatus()` 终态分支改 `applyStatus(body); render(); $("btn-cancel").hidden = true;`。
- 页面文案集中为 `var PAGE_STRINGS = {...}`(PHASE_LABELS/BEHAVIOR_LABELS/STATUS_LABELS 及新增横幅/提示文案并入;引用处逐个替换)。
- 既有 test_page_contract 用例(plan_id/persisted/identical 前提/hidden 优先/shutdown 文案)必须保持通过——重构不得删这些语义特征。

- [ ] **Step 4: 全量回归 + Commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`

```bash
git add migration/gui/index.html tests/test_page_contract.py tests/test_page_behavior.py
git commit -m "feat(w3): 页面 Presentation Model+游标四规则+代际守卫+node 状态核行为测试 (batchI-W3-T5)"
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
    for key in ("buckets", "total_bytes", "ask_count", "overwrite_count", "mod_pairs",
                "compat_warnings", "client_only", "world_notices", "guard_scope"):
        assert key in diff, f"done.diff 缺 {key}"
    assert diff["buckets"]["must_migrate"] >= 1 and diff["guard_scope"]

# tests/test_pipeline.py 追加
def test_build_plan_returns_review_extras(tmp_path):
    """白名单⑦:4 元组第 4 位=extras(buckets/total_bytes/ask_count/
    overwrite_count/client_only/world_notices)。"""
    plan, compat, pairs, extras = build_plan(...)          # 既有夹具
    assert set(extras) >= {"buckets", "total_bytes", "ask_count", "overwrite_count",
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

- pipeline.build_plan:在 **`plan = Planner(report, src_index).plan()` 与 `plan.src/plan.dst` 赋值之后**(评审建议 B:extras 引用 `plan.actions`,必须后置——原稿置于 mod_pairs 之后会 NameError)、issue_review 之前增:

```python
    # 审阅摘要数据(批次I-W3 T6,spec §5.1):client_only 匹配与 run_diff 同一
    # 单点——ctx 经 resolve_diff_context 按快照记录的 game_root 解析(语义一致)
    from .moddb import load_client_mods

    client_modids, client_families = load_client_mods()
    client_only: set[str] = match_client_only_paths(
        report, resolve_diff_context(src_snap, dst_snap),
        client_modids, client_families)
    extras: dict[str, object] = {
        "buckets": plan.summary(),                # origin 计数(决策视角)
        "total_bytes": sum(
            a.src_size or 0 for a in plan.actions
            if a.behavior in (Behavior.COPY, Behavior.ASK)),
        "ask_count": sum(1 for a in plan.actions if a.behavior == Behavior.ASK),
        # spec §5.1 顶部摘要「将覆盖(有备份)数」(评审建议 C 补):带备份目标
        # 的动作=执行时目标同位文件会被移入备份
        "overwrite_count": sum(
            1 for a in plan.actions
            if a.behavior in (Behavior.COPY, Behavior.ASK) and a.backup_target),
        "client_only": sorted(client_only),
        "world_notices": world_rename_notices(src_snap, dst_snap),
    }
```

返回 4 元组。docstring Returns 段同步。cli.py 两处解包(`plan, compat_warnings, _pairs =` → `plan, compat_warnings, _pairs, _extras =`)与测试夹具机械更新(conftest/test_e2e/test_pipeline 共约 10 处;T1 的 `_legacy_layout` 亦在其中)。
- server `_run_plan_job` done 事件增 `"diff": {**_extras, "mod_pairs": [p.to_dict() for p in _pairs], "compat_warnings": [str(w) for w in compat_warnings], "guard_scope": STRINGS["review.scope_note"]}`;STRINGS 增 `review.scope_note`(固定文案:已哈希条目全量校验源/目标状态;bulk/mods 代理条目在其 size+mtime 范围内承诺;「目标不存在」亦为被记录状态——不承诺发现范围外的任何改动)。
- **③结束页备份位置(spec §5.1)**:migrate done 事件增 `"backup_dir": str(dst_root / "_conflict_backup")`(自 executor.BACKUP_DIR 派生,仅当 results 含 backed_up 时非空提示);页面终态渲染备份位置行+恢复指引(「被覆盖文件的原件在 <backup_dir>,需要找回请到该目录」)。
- 页面②重构:顶部 `#review-summary` 摘要条(**待迁移 N 项/约 X MB/待确认 M/将覆盖(有备份)O/兼容警告 K**——spec §5.1 五要素齐,评审建议 C;guard_scope 提示行);默认展开=ASK 组+compat 警示;折叠区:mod 配对表(⇄→中文映射:upgrade=升级/renamed=改名/rebuilt=重打包,`content_differs` →「⚠ 上游重打包」)、client_only(中性 info 色)、世界提示、目标独有/不迁移原因/相同文件;`buildGroupBody(g)` 独立函数,组头展开事件首次调用并缓存(懒建行);file 事件更新收拢进 rAF 回调(`scheduleProgressRender()` 去重合批);`btn-back1` 移出 `#plan-body`(置于 step2 section 直下,plan-progress/错误态均可见)。
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
- Modify: `migration/gui/server.py`(POST /api/swap/preflight、POST /api/swap/apply job;preflight_id 指纹仓)
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
@dataclass(frozen=True)
class SwapRunOutcome:
    """run_swap 全流程结构化结果(评审 v3 契约A:CLI 既有输出所需信息全量回传;
    装包成功而规划失败时,install 保留——用户须知目标 mods/ 已被修改)。"""
    rc: int                                       # 0=成功/用户拒绝继续;2=预检失败/规划失败
    preflight: SwapPreflightOutcome | None        # 预检结构化结果(error/incompat/extras/conflicts)
    install: SwapInstallOutcome | None            # 装包结果;规划失败时同样保留
    compat_warnings: tuple[str, ...] = ()         # 规划期兼容警告
    plan_summary: dict[str, int] | None = None    # plan.summary()(规划成功时)
    plan_file: Path | None = None                 # 计划文件路径(「审阅后 migrate」提示行)
    plan_error: str | None = None                 # 规划失败原因(install 已完成的情形)
def swap_preflight(game_root: Path, dst: str, new_pack: Path, *,
                   src: str, legacy_dir: Path | None = None) -> SwapPreflightOutcome
def swap_install(dst_mods: Path, new_mods_dir: Path, *, overwrite: set[str],
                 dry_run: bool, backup_root: Path,
                 journal: JobJournal | None = None,
                 should_cancel: Callable[[], bool] | None = None) -> SwapInstallOutcome
    # 逐 jar:取消检查点命中即停(安全边界=当前 jar 已完成),cancelled=True
def run_swap(cwd, game_root, src, dst, new_pack, *, confirm_extras: Callable[[list[str]], bool],
             resolve_conflict: Callable[[str], bool], force: bool = False,
             dry_run: bool = False) -> SwapRunOutcome
    # CLI 全流程编排(预检→装包→重扫规划 modpack_swap=True);交互(提示行+确认)
    # 在回调内由调用方打印,事中/事后信息全量入 SwapRunOutcome——CLI 对拍基准=
    # 决策语义与退出码,新增备份行为允许一行备份位置输出(spec §5.2);
    # 规划失败分支:rc=2 且 install 保留+plan_error,CLI 须打印装包统计与备份
    # 位置(目标已被修改,不可静默)
# gui/server.py
#   POST /api/swap/preflight {src,dst,new_pack} → 202 {job_id}(只读 job;首步
#     scan src(恒重扫,GUI 用户免「先跑 scan」断崖);done 载荷=
#     SwapPreflightOutcome 序列化+备份位置预告)
#   POST /api/swap/apply {preflight_id, src, dst, accept_incompat, accept_extras,
#                   overwrite_jars:[...]} → 202 {job_id}(spec §5.2 原文端点名;装包
#     job,kind="swap" 可取消;**持锁后三重重验**(评审 P2-3):① 重算指纹失配 →
#     error 事件 code=swap_inputs_changed;② 请求版本对/游戏根与指纹仓记录核对;
#    ③ swap_preflight 的兼容检查重跑——三项全过才装包;装包持 journal;)
#     **装包段结束即原子判定并关闭取消窗**(评审 v3 P2-3):命中取消→cancelled
#     收尾(跳过链式重规划);未命中→job.cancel_closed=True(锁内置位);取消
#     端点在 job._lock 内**重检**(评审 v4 P2-3):cancel_closed → 409「装包已
#     完成,正在重新规划,不可取消」(err_cancel_closed 三段式)/ can_cancel
#     不支持 → 405——锁外只做 404,**不得在锁外读 can_cancel/cancel_closed**
#     (迟到取消读到允许→关窗→进锁置 cancelling 的交错已复核可复现);重扫+
#     规划段不可取消,封死「done 带成功计划 vs GET 收口 cancelled」的分歧
#   apply 装包完成后**链式重规划**(评审 P1-2,spec §5.2「装包 → 重扫+规划」):
#     同一 job 内 scan src+dst(与 plan job 同型恒重扫)→
#     build_plan(modpack_swap=True, rescan_dst=True) →
#     done 载荷与 plan job 同形(plan/plan_id/persisted/diff)——页面直接进入②
#     审阅页,不存在「回向导用普通 plan 把旧包 jar 重新列为可迁移」的路径;
#     链式段失败 → error code=swap_replan_failed 且载荷带 install+backup_dir
#     (评审 v3 契约B:装包已成功,目标 mods/ 已被修改,不可静默)
#   preflight 指纹 = sha256(规范化 game_root|src|dst|new_pack 解析路径|目标
#     <dst>.json 内容|两侧 mods jar 名+MD5 清单)——实例身份与 NeoForge 版本
#     元信息入指纹(评审 P2-3:跨游戏根同名版本/篡改版本 json 必失配);
#     服务端进程内 preflight_id → 指纹仓(仅保留最近 10 条,防无界增长)
#   Job.can_cancel 判定改 kind ∈ {"migrate", "swap"} 且 not cancel_closed
#   (**仅作取消端点锁内复核的辅助**——锁外读是 TOCTOU,评审 v4 P2-3;
#   既有 migrate 用例不受影响;migrate 无 cancel_closed 置位路径,恒可取消)
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline.py 追加(新节;块首补导入:
#   from migration.journal import JobJournal, scan_interrupted
#   from migration.pipeline import run_swap, swap_install  — build_plan/scan_version 已有)
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

def test_swap_journal_records_intent_per_jar(tmp_path, monkeypatch):
    """spec §5.2/§4.3:装包过程 write-ahead——每 jar 意图(含备份位)→完成;
    崩溃窗口重启经 scan_interrupted 可核(spec §9 swap 断言)。"""
    import migration.pipeline as pl
    dst_mods = tmp_path / "dst" / "mods"; dst_mods.mkdir(parents=True)
    (dst_mods / "a-1.0.jar").write_bytes(b"OLD")
    pack = _pack_with_jars(tmp_path, {"a-1.0.jar": b"NEW", "b-2.0.jar": b"B"})
    journal = JobJournal(tmp_path / "jobs", "swapjob", "swap",
                         src="s", dst="d", game_root=str(tmp_path))
    real_copy = pl.copy_atomic

    def _crash_on_b(src, dst, *, rel, backup_dir=None):
        if rel == "b-2.0.jar":
            raise OSError("模拟崩溃:装包中途中断")
        return real_copy(src, dst, rel=rel, backup_dir=backup_dir)

    monkeypatch.setattr(pl, "copy_atomic", _crash_on_b)
    with pytest.raises(OSError):
        swap_install(dst_mods, pack / "mods", overwrite={"a-1.0.jar"},
                     dry_run=False, backup_root=tmp_path / "backups",
                     journal=journal)
    items = scan_interrupted(tmp_path / "jobs")
    assert items and items[0]["kind"] == "swap"
    assert [e["rel"] for e in items[0]["entries"]] == ["b-2.0.jar"]
    # a.jar 已意图+完成双记录,b.jar 停在意图态(待核对,≠未执行)
    assert (dst_mods / "a-1.0.jar").read_bytes() == b"NEW"

def test_swap_run_cli_equivalent(tmp_path):
    """CLI 对拍基准:run_swap 决策语义与退出码等价(确认拒绝=0/成功=0);
    SwapRunOutcome 携带 CLI 既有输出所需全量信息(评审 v3 契约A:prefetch/
    install/compat_warnings/plan_summary/plan_file 皆可自返回值打印)。"""
    game = tmp_path / "game"
    src_dir = game / "versions" / "src"; src_dir.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    dst_dir = game / "versions" / "dst"; dst_dir.mkdir()
    (dst_dir / "dst.json").write_text(
        '{"arguments": {"game": ["--fml.neoforgeVersion", "21.1.228"]}}',
        encoding="utf-8")
    (dst_dir / "mods").mkdir()
    (dst_dir / "mods" / "leftover.jar").write_bytes(b"L")   # 新包外残留→触发确认
    (game / ".mcmig" / "plans").mkdir(parents=True)
    scan_version(game, "src", game / ".mcmig" / "snapshots")
    scan_version(game, "dst", game / ".mcmig" / "snapshots")
    pack = _pack_with_jars(tmp_path, {"new-1.0.jar": b"NEW"})

    def run(confirm_extras):
        return run_swap(tmp_path, game, "src", "dst", pack,
                        confirm_extras=confirm_extras,
                        resolve_conflict=lambda n: True)

    # 分支①:extras 确认被拒 → rc 0,零装包,preflight 仍回传(extras 清单在)
    out = run(confirm_extras=lambda extras: False)
    assert out.rc == 0 and out.install is None
    assert out.preflight is not None and "leftover.jar" in out.preflight.extras
    assert not (dst_dir / "mods" / "new-1.0.jar").exists()
    # 分支②:确认放行 → rc 0,装包计数正确,规划摘要/计划文件齐备
    out = run(confirm_extras=lambda extras: True)
    assert out.rc == 0 and out.install is not None and out.install.copied == 1
    assert (dst_dir / "mods" / "new-1.0.jar").read_bytes() == b"NEW"
    assert out.plan_summary and out.plan_summary.get("must_migrate", 0) >= 1
    assert out.plan_file and out.plan_file.is_file()
    assert out.plan_error is None

def test_swap_fingerprint_binds_instance_and_version_json(tmp_path):
    """评审 P2-3:指纹绑定规范化游戏根与目标 <dst>.json——同名版本对在不同
    游戏根指纹不同;版本 json 内容变化指纹不同。"""
    a, b = tmp_path / "ga", tmp_path / "gb"
    for root in (a, b):
        (root / "versions" / "dst" / "mods").mkdir(parents=True)
        (root / "versions" / "dst" / "dst.json").write_text("{}", encoding="utf-8")
    (a / "versions" / "dst" / "dst.json").write_text('{"x": 1}', encoding="utf-8")
    fa = _swap_fingerprint(a, "src", "dst", a / "pack",
                           a / "versions" / "dst" / "mods", a / "pack" / "mods")
    fb = _swap_fingerprint(b, "src", "dst", b / "pack",
                           b / "versions" / "dst" / "mods", b / "pack" / "mods")
    assert fa != fb                                     # 不同游戏根(含 json 差异)
    (b / "versions" / "dst" / "dst.json").write_text('{"x": 2}', encoding="utf-8")
    fb2 = _swap_fingerprint(b, "src", "dst", b / "pack",
                            b / "versions" / "dst" / "mods", b / "pack" / "mods")
    assert fb != fb2                                    # json 内容变化即失配
```

```python
# tests/test_gui_server.py 追加(评审 v3 P2-6:四用例+两新增全部可执行;
# _swap_layout 为共用布局,apply 请求体经 _apply_body 辅助统一构造)
def _swap_layout(tmp_path, monkeypatch) -> tuple[Path, TestClient]:
    """swap 两阶段公共布局:src(旧包独有 jar+options.txt)/dst(版本 json,
    mods 空)/new_pack(含 newpack.jar);经 _make_client 注入 workdir。"""
    game = tmp_path / "game"
    src = game / "versions" / "src"; src.mkdir(parents=True)
    (src / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (src / "mods").mkdir()
    (src / "mods" / "oldpack-only.jar").write_bytes(b"OLD")
    dst = game / "versions" / "dst"; dst.mkdir()
    (dst / "dst.json").write_text(
        '{"arguments": {"game": ["--fml.neoforgeVersion", "21.1.228"]}}',
        encoding="utf-8")
    (dst / "mods").mkdir()
    pack = tmp_path / "newpack"; (pack / "mods").mkdir(parents=True)
    (pack / "mods" / "newpack.jar").write_bytes(b"NEW")
    return _make_client(tmp_path, monkeypatch, game=game)

def _swap_preflight_done(client: TestClient, game: Path) -> dict:
    """跑完 preflight job,返回 done 载荷(含 preflight_id 与三类决策清单)。

    new_pack 用 _swap_layout 的固定位:<tmp>/newpack(与 game 同级)。
    """
    job = client.post("/api/swap/preflight", json={
        "src": "src", "dst": "dst",
        "new_pack": str(game.parent / "newpack"),
    }).json()["job_id"]
    events = _wait_job_done(client, job)
    done = events[-1]
    assert done["type"] == "done", done
    return done

def _apply_body(pf: dict) -> dict:
    """apply 请求体:以 preflight_id 携带全部决策(默认全拒/零覆盖)。"""
    return {"preflight_id": pf["preflight_id"], "src": "src", "dst": "dst",
            "accept_incompat": False, "accept_extras": False, "overwrite_jars": []}

def test_swap_apply_rejects_changed_inputs(tmp_path, monkeypatch):
    """两阶段指纹重校验:preflight 后篡改目标 mods/(新增 jar)→ apply 拒
    (swap_inputs_changed),零写盘。"""
    game, client = _swap_layout(tmp_path, monkeypatch)
    pf = _swap_preflight_done(client, game)
    (game / "versions" / "dst" / "mods" / "intruder.jar").write_bytes(b"X")
    job = client.post("/api/swap/apply", json=_apply_body(pf)).json()["job_id"]
    err = _wait_job_done(client, job)[-1]
    assert err["type"] == "error" and err["code"] == "swap_inputs_changed"
    assert not (game / "versions" / "dst" / "mods" / "newpack.jar").exists()

def test_swap_apply_rejects_switched_game_root(tmp_path, monkeypatch):
    """评审 P2-3:preflight 后切换游戏根(POST /api/config)再 apply → 拒
    (swap_inputs_changed:指纹含规范化 game_root,跨根必失配)。"""
    game, client = _swap_layout(tmp_path, monkeypatch)
    pf = _swap_preflight_done(client, game)
    other = tmp_path / "other_root"
    # 完整版本对(评审 v4 P2-4:只有 dst 会先被 apply 的版本校验 422 挡下,
    # 触不到指纹失配分支)
    for name in ("src", "dst"):
        (other / "versions" / name).mkdir(parents=True)
    (other / "versions" / "src" / "options.txt").write_text("x\n", encoding="utf-8")
    (other / "versions" / "dst" / "dst.json").write_text(
        '{"arguments": {"game": ["--fml.neoforgeVersion", "21.1.228"]}}',
        encoding="utf-8")
    assert client.post("/api/config", json={"game_root": str(other)}).status_code == 200
    job = client.post("/api/swap/apply", json=_apply_body(pf)).json()["job_id"]
    err = _wait_job_done(client, job)[-1]
    assert err["type"] == "error" and err["code"] == "swap_inputs_changed"

def test_swap_apply_rejects_changed_version_json(tmp_path, monkeypatch):
    """评审 P2-3:preflight 后修改目标 <dst>.json → apply 拒(NeoForge 兼容
    判定的输入已变,兼容检查须重跑,不能沿用预检结论)。"""
    game, client = _swap_layout(tmp_path, monkeypatch)
    pf = _swap_preflight_done(client, game)
    (game / "versions" / "dst" / "dst.json").write_text(
        '{"arguments": {"game": ["--fml.neoforgeVersion", "21.1.999"]}}',
        encoding="utf-8")
    job = client.post("/api/swap/apply", json=_apply_body(pf)).json()["job_id"]
    err = _wait_job_done(client, job)[-1]
    assert err["type"] == "error" and err["code"] == "swap_inputs_changed"
    assert not (game / "versions" / "dst" / "mods" / "newpack.jar").exists()

def test_swap_apply_chains_modpack_swap_replan(tmp_path, monkeypatch):
    """评审 P1-2:apply 装包后同 job 链式重扫+规划(modpack_swap=True)——done
    载荷与 plan job 同形(plan_id/persisted/diff);随后从该计划 migrate,
    旧包独有 jar 不回迁(源独有 mod 归换包排除,非 must_migrate)。"""
    game, client = _swap_layout(tmp_path, monkeypatch)
    pf = _swap_preflight_done(client, game)
    job = client.post("/api/swap/apply", json=_apply_body(pf)).json()["job_id"]
    done = _wait_job_done(client, job)[-1]
    assert done["type"] == "done" and done["job_kind"] == "swap"
    assert done["plan_id"] and done["persisted"] is True
    assert done["install"]["copied"] == 1
    # done.plan 是 _group_actions 分组 {origin: {actions: [...]}}(评审 v4 P2-4:
    # 无扁平 actions 键)——遍历各组的 actions
    moved = {a["path"]
             for group in done["plan"].values()
             for a in group["actions"]
             if a["origin"] in ("must_migrate", "mod_added")}
    assert "mods/oldpack-only.jar" not in moved        # 换包排除,非可迁移
    mig = client.post("/api/migrate", json={
        "plan_id": done["plan_id"], "src": "src", "dst": "dst",
        "ask_yes": []}).json()["job_id"]
    _wait_job_done(client, mig)
    assert (game / "versions" / "dst" / "mods" / "newpack.jar").exists()
    assert not (game / "versions" / "dst" / "mods" / "oldpack-only.jar").exists()

def test_swap_cancel_window_closes_before_replan(tmp_path, monkeypatch):
    """评审 v3 P2-3:装包完成后取消窗原子关闭——重扫/规划阶段的 cancel 请求
    409,job 以携带计划的 done 正常收口(封死「done 带成功计划 vs GET 收口
    cancelled」的分歧)。"""
    import threading
    import time
    game, client = _swap_layout(tmp_path, monkeypatch)
    pf = _swap_preflight_done(client, game)
    replan_started = threading.Event()
    real_build = server_module.build_plan

    def slow_build(*a, **k):
        replan_started.set()
        time.sleep(0.5)                                # 留出取消请求的观察窗
        return real_build(*a, **k)

    monkeypatch.setattr(server_module, "build_plan", slow_build)
    job = client.post("/api/swap/apply", json=_apply_body(pf)).json()["job_id"]
    assert replan_started.wait(timeout=10.0), "未进入链式重规划阶段"
    resp = client.post(f"/api/jobs/{job}/cancel")
    assert resp.status_code == 409                     # 取消窗已关(白名单⑩)
    done = _wait_job_done(client, job)[-1]
    assert done["type"] == "done" and done.get("plan_id")

def test_cancel_endpoint_rechecks_closed_window_in_lock(tmp_path, monkeypatch):
    """评审 v4 P2-3:取消端点不得依赖锁外 can_cancel 读——白盒直接构造
    cancel_closed=True 的 swap job(关窗瞬间的形态),取消 409 且不置
    cancelling(锁内重检;若只在锁外判定,此形态会 202 并污染终态)。"""
    game, client = _swap_layout(tmp_path, monkeypatch)
    from migration.gui.server import Job
    job = Job("swapclosed", "swap")
    job.cancel_closed = True                           # 白盒:直接构造关窗形态
    store = client.app.state.jobs                      # T7 暴露(app.state.jobs)
    store._jobs["swapclosed"] = job
    resp = client.post("/api/jobs/swapclosed/cancel")
    assert resp.status_code == 409
    assert job.status != "cancelling"                  # 未被置为取消中

def test_swap_apply_replan_failure_reports_install_state(tmp_path, monkeypatch):
    """评审 v3 契约A/B:装包成功而链式规划失败——error 载荷带 install 统计与
    backup_dir(用户须知目标 mods/ 已被修改,不可静默只报规划失败)。"""
    game, client = _swap_layout(tmp_path, monkeypatch)
    # 冲突 jar 必须在 preflight **前**于两侧创建(评审 v4 P2-4:指纹覆盖两侧
    # jar 清单,预检后再加 victim.jar 会先触发 swap_inputs_changed;且新包
    # 无同名 jar 则根本没有可覆盖冲突)
    (game / "versions" / "dst" / "mods" / "victim.jar").write_bytes(b"OLD")
    (game.parent / "newpack" / "mods" / "victim.jar").write_bytes(b"NEW")
    pf = _swap_preflight_done(client, game)
    assert "victim.jar" in pf["conflicts"]             # 冲突已被预检识别

    def _boom(*a, **k):
        raise ValueError("规划失败(模拟)")

    monkeypatch.setattr(server_module, "build_plan", _boom)
    job = client.post("/api/swap/apply", json={
        **_apply_body(pf), "overwrite_jars": ["victim.jar"]}).json()["job_id"]
    err = _wait_job_done(client, job)[-1]
    assert err["type"] == "error" and err.get("code") == "swap_replan_failed"
    assert err["install"]["copied"] >= 1 and err["backup_dir"]
    assert (game / "versions" / "dst" / "mods" / "newpack.jar").exists()
    backup = Path(err["backup_dir"])
    assert (backup / "victim.jar").read_bytes() == b"OLD"
```

test_cli.py 既有 swap 用例保持通过(对拍基准;装包统计/退出码不变);新增 `test_cli_swap_prints_backup_location`(覆盖发生时输出含 `backups/swap/` 路径行)。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline.py tests/test_gui_server.py -k "swap" -v`
Expected: FAIL(`swap_install`/`swap_preflight`/`run_swap`/`_swap_fingerprint` 不在 pipeline;/api/swap/* 端点不存在)。

- [ ] **Step 3: 实现 pipeline 侧**

- `_swap_preflight`/`_md5` 自 cli.py 搬入(逻辑不改);`swap_preflight` 包装:加 src 快照存在性检查(cli.py:481-485 同语义)+ 指纹计算:

```python
def _swap_fingerprint(game_root: Path, src: str, dst: str, new_pack: Path,
                      dst_mods: Path, new_mods: Path) -> str:
    """两阶段输入指纹:参与决策的全部输入 → sha256(评审 P2-3 收口)。

    绑定面:规范化实例身份(game_root.resolve,消 junction/别名——跨游戏根的
    同名版本对必失配)+ 版本对 + 新包路径 + 目标 <dst>.json 内容(NeoForge
    兼容判定的输入)+ 两侧 mods jar 名与 MD5 清单。
    """
    h = hashlib.sha256()
    h.update(f"R|{game_root.resolve()}|{src}|{dst}|{new_pack.resolve()}".encode("utf-8"))
    ver_json = game_root / "versions" / dst / f"{dst}.json"
    if ver_json.is_file():
        h.update(f"V|{file_sha256(ver_json)}".encode("utf-8"))
    for jar in sorted(dst_mods.glob("*.jar")):
        h.update(f"D|{jar.name}|{_md5(jar)}".encode("utf-8"))
    for jar in sorted(new_mods.glob("*.jar")):
        h.update(f"N|{jar.name}|{_md5(jar)}".encode("utf-8"))
    return h.hexdigest()
```

- `swap_install`:resolver 参数改 `overwrite: set[str]`(决策预收集——CLI 由 Confirm 回调收集,GUI 由勾选收集);覆盖路径 `copy_atomic(jar, target, rel=jar.name, backup_dir=backup_root)`;backup_root=`game_root/.mcmig/backups/swap/<UTC时间戳>`(调用方传入);journal 挂点:`record_intent(jar.name, {"op": "install", "backup": str(备份相对位) or None})` → copy → `record_completion`;identical 跳过不记意图(零写盘);`should_cancel` 逐 jar 检查(命中即停,`cancelled=True`,与 executor 取消同型安全边界)。
- `run_swap`:承接 _cmd_swap 编排(锁内调用约定写入 docstring:调用方须持 instance_locks(game_root, src, dst));交互经 confirm_extras/resolve_conflict 回调(extras/冲突的**提示行**也由回调方打印——CLI 在回调内打印清单后 Confirm);装包 job_id=`cli-<UTC>-<pid>` 建 journal;重扫规划段 build_plan(modpack_swap=True, rescan_dst=True)同参搬移;**返回 `SwapRunOutcome`**(评审 v3 契约A)。`_cmd_swap` 改薄壳:解析参数→instance_locks→run_swap(回调=rich 打印+Confirm)→按 outcome 打印(预检错误/不兼容清单/装包统计/兼容警告/规划摘要/计划路径提示,文案与现输出逐字对拍;备份位置行新增);**规划失败分支**(outcome.plan_error 非空):`[错误] 规划失败: …` + `[提示] 装包已完成(复制 N),目标 mods/ 已被修改;备份位于 <backup_dir>,可从中找回被覆盖 jar`(评审 v3 契约B——目标已变更不可静默)。
- server 两端点:`POST /api/swap/preflight` job(只读;**首步 `scan_version(ctx.game_root, src, ctx.snapshots)`**(与 plan job 同型恒重扫——GUI 用户不必先跑 scan/plan;快照内容不入指纹,不影响 apply 重验);done=SwapPreflightOutcome 序列化+`backup_preview=str(<game_root>/.mcmig/backups/swap)`);指纹仓 `app.state.swap_preflights: dict[str, dict]`(preflight_id→{fingerprint, src, dst, new_pack, game_root};插入后仅保留最近 10 条);`POST /api/swap/apply` job(kind="swap",`Job.can_cancel` 判定扩为 `kind in ("migrate", "swap") and not cancel_closed`),**持实例锁后先决+三重重验再装包**(评审 P2-3):
  - 先决:指纹仓无此 preflight_id → 422(preflight 过期/未知,不建 job);
  - 重验①:仓内记录的 game_root/src/dst 与**当前 job 定格的 ctx** 核对(切换游戏根后旧 preflight 作废);
  - 重验②:锁内重算 `_swap_fingerprint` ≠ 仓内指纹 → error 事件 `swap_inputs_changed`(STRINGS);失配覆盖「目标 mods/ 被改」「目标 <dst>.json 被改」两形态;
  - 重验③:`swap_preflight` 的兼容检查重跑——`accept_incompat=False` 且仍有 incompat → error;`accept_extras` 同理;`overwrite_jars` 必须 ⊆ conflicts(越界→422)。
- **链式重规划(评审 P1-2,spec §5.2「装包 → 重扫+规划」原文)**:apply job 在装包完成后**同一 job 内**继续:`job.emit({"type":"phase","name":"replan"})` → `scan_version(ctx.game_root, src, ctx.snapshots)`+`scan_version(ctx.game_root, dst, ctx.snapshots)`(与 plan job 同型恒重扫,src+dst 双侧;server 侧经模块级名引用 scan_version/build_plan——与既有 `monkeypatch.setattr(server_module, "scan_version", …)` 注入惯例一致)→ `build_plan(..., modpack_swap=True, rescan_dst=True, ...)`(4 元组)→ done 事件与 plan job 同形:`{"type":"done","job_kind":"swap","install":SwapInstallOutcome序列化,"backup_dir":...,"plan":...,"plan_id":...,"persisted":...,"diff":...}`——页面直接以 plan_id 进②审阅页执行迁移;**不存在**「装包后回步① 用普通 /api/plan(modpack_swap=False)把旧包独有 jar 重新列为可迁移」的路径(T8 页面按此接线,验收用例 test_swap_apply_chains_modpack_swap_replan 断言旧包 jar 不回迁)。**链式段失败**(重扫/规划抛错):error 事件 `code=swap_replan_failed`,**载荷带 install 统计与 backup_dir**(评审 v3 契约B——装包已成功,目标 mods/ 已被修改,用户必须知道);journal finish 照常收尾(装包部分已双记录,重规划不写 journal)。
- 装包+journal+取消窗:`swap_install` 挂 `JobJournal(ctx.jobs, job.id, "swap", src=src, dst=dst, game_root=str(ctx.game_root))`+should_cancel(查 job.status=="cancelling");**装包段结束的原子判定与关窗(评审 v3 P2-3,白名单⑩)**:`swap_install` 返回后、进入 replan 前,在 job 锁内一次完成——`status=="cancelling"` → 复用 migrate 的 cancelled 收尾口径(done 事件 cancelled:true+已完成/未完成 jar 清单,**跳过链式规划**——部分装包后的计划没有审阅意义,重走 swap);否则 `job.cancel_closed = True`,再发 phase=replan 进入不可取消的重扫+规划段。**取消端点配套改造(评审 v4 P2-3)**:`api_job_cancel` 的 `can_cancel`/终态判定整体挪进 `with job._lock:`——锁内序 `job.done or job._settled → 409`、`job.cancel_closed → 409`(STRINGS 新增 `err_cancel_closed.what/why`:「装包已完成,正在重新规划」「此阶段不可取消;若不需要该计划,可在审阅页放弃」)、`not job.can_cancel → 405`、否则置 cancelling(幂等 202);锁外仅做 404——与既有 `_settled` 锁内守卫(终审 I2)同构,封死「取消在锁外读到允许→关窗→进锁置 cancelling→规划成功却收口 cancelled」的交错。T8 页面在 phase=replan 时同步隐藏取消入口。**另:`create_app` 暴露 `app.state.jobs = store`**(单行;T9 窗口壳单源取用的前提,同时供本任务取消窗白盒用例注入)。

- [ ] **Step 4: 全量回归 + Commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`

```bash
git add migration/pipeline.py migration/cli.py migration/gui/server.py migration/gui/STRINGS.py tests/
git commit -m "feat(w3): swap 编排下沉 pipeline+两阶段 API+覆盖备份+装包 journal (batchI-W3-T7)"
```

---

### Task 8: swap 页面(两阶段交互+链式规划进审阅页+边界文案)(spec T9)

**Files:**
- Modify: `migration/gui/index.html`(步① 增「整合包换包」次级入口+swap 面板;复用 T5 jobModel)
- Modify: `migration/gui/STRINGS.py`(边界文案)
- Test: `tests/test_page_contract.py`

**Interfaces:**
- Consumes: T7 两端点(POST /api/swap/preflight → job → done 载荷;POST /api/swap/apply → job → done 载荷含链式规划的 plan/plan_id/diff);T5 applyEvent/render/ownsCurrent/beginRequest/requestAlive。
- Produces: 页面 swap 流(入口→预检结果呈现(不兼容/新包外/冲突勾选)→应用→进度→**装包统计+边界文案+以链式计划直接进②审阅页**)。

- [ ] **Step 1: 写失败测试(源码契约)**

```python
# tests/test_page_contract.py 追加
def test_swap_panel_two_phase_flow() -> None:
    """spec §5.2/T9:swap 面板两阶段——先 /api/swap/preflight 只读预检,
    呈现 incompat/extras/conflicts 三类决策,再 POST /api/swap/apply 提交决策面。"""
    assert re.search(r'postJson\("/api/swap/preflight"', _PAGE)
    m = re.search(r'postJson\("/api/swap/apply",\s*\{([^}]*)\}', _PAGE)
    assert m
    for key in ("preflight_id", "src", "dst", "accept_incompat", "accept_extras",
                "overwrite_jars"):
        assert re.search(rf"\b{key}\s*:", m.group(1)), f"swap 请求体缺 {key}"

def test_swap_boundary_copy_present() -> None:
    """spec §5.2:边界文案——确认装包后取消迁移不会自动撤销装包+备份位置指引。"""
    assert "不会自动撤销" in _PAGE and "备份" in _PAGE

def test_swap_replan_phase_hides_cancel_entry() -> None:
    """评审 v3 P2-3:装包完成进入 replan 阶段——页面同步隐藏/禁用取消入口
    (服务端该阶段 cancel 已 409,按钮不得留在界面上误导用户)。"""
    m = re.search(r'["\']replan["\'][\s\S]{0,500}', _PAGE)
    assert m, "页面须识别 replan 阶段(phase 事件分派)"
    seg = m.group(0)
    assert re.search(r'btn-cancel', seg) or "hideCancel" in seg, \
        "replan 分支须触达取消入口"
    assert re.search(r'hidden|disable', seg), "取消入口须隐藏或禁用"
    assert "不可取消" in _PAGE                     # 边界文案(PAGE_STRINGS)

def test_swap_done_enters_review_with_chained_plan() -> None:
    """评审 P1-2(页面半):apply 的 done 载荷带链式换包计划——页面以 plan_id
    直接进入②审阅页,不回步① 用普通 plan 重新规划(那会把旧包 jar 重新列为
    可迁移)。"""
    swap_done = re.search(r'function\s+onSwapApplyDone\((.*?)\n\}', _PAGE, re.S) \
        or re.search(r'job_kind\s*===?\s*["\']swap["\'][\s\S]{0,600}', _PAGE)
    assert swap_done, "页面须有 swap apply done 的专门处理"
    seg = swap_done.group(0)
    assert re.search(r"plan_id|planId", seg), "swap done 须捕获链式计划的 plan_id"
    assert re.search(r"showStep\(2\)|renderPlan\(", seg), "须直接进入②审阅页渲染"

def test_no_plain_replan_after_swap() -> None:
    """评审 P1-2:swap 完成路径不得调用普通 /api/plan(其 modpack_swap=False)。"""
    swap_flow = _PAGE.split("btn-swap-apply")[1].split("</section>")[0] \
        if "btn-swap-apply" in _PAGE else ""
    assert 'postJson("/api/plan"' not in swap_flow, \
        "swap 流程内不得回退到普通 plan(丢失换包模式)"
```

- [ ] **Step 2: 跑测试确认失败** → FAIL(无 swap 面板)。

Run: `.venv/Scripts/python.exe -m pytest tests/test_page_contract.py -k swap -v`

- [ ] **Step 3: 实现**

步① section 增次级按钮 `#btn-swap-entry`(「整合包换包(进阶)…」)→ 切换 `#swap-panel`(hidden):新包目录输入(须含 mods/ 子目录,预检由服务端校验)、src/dst 沿用向导已选、`#btn-swap-preflight` → POST /api/swap/preflight → jobModel 订阅 → done 载荷渲染三类决策面(不兼容清单+`accept_incompat` 勾选、新包外残留清单+`accept_extras` 勾选、冲突 jar 多选=overwrite_jars)+预检指纹随载荷留存;`#btn-swap-apply` → POST /api/swap/apply → 进度(file 事件复用 migrate 渲染)→ done:**装包统计+边界文案条**(STRINGS:确认装包后目标 mods/ 已被修改;此后取消迁移不会自动撤销装包;备份位于 `<backup_dir>`,可从中找回被覆盖 jar)+ **以 done 载荷的 plan/plan_id/diff 直接渲染②审阅页**(planId 入 jobModel,「执行迁移」即从审阅页走既有 migrate 流——旧包 jar 已按换包排除,不存在回迁路径;评审 P1-2)+「放弃此计划返回」出口(回步①,明确提示该计划已作废须重走 swap)。返回按钮/新包输入校验失败态均有出口,不困死面板;swap 全流程的**POST 回调走 requestAlive、SSE/取消回调走 ownsCurrent**(T5 双轨守卫);**phase=replan 时隐藏/禁用取消入口并提示「装包完成,正在生成换包计划(此阶段不可取消)」**(评审 v3 P2-3,与服务端 409 同步切换);apply 的 error 事件若带 `install/backup_dir`(swap_replan_failed)→ 按边界文案呈现「装包已完成但规划失败」而非常规错误态。

- [ ] **Step 4: 全量回归 + Commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`

```bash
git add migration/gui/index.html migration/gui/STRINGS.py tests/test_page_contract.py
git commit -m "feat(w3): swap 页面两阶段交互+链式换包计划直入审阅页+边界文案 (batchI-W3-T8)"
```

---

### Task 9: pywebview 窗口壳(spec T10)

**Files:**
- Create: `migration/gui/app.py`(窗口壳:uvicorn 线程+webview 窗口+渲染器预检+关闭边界+停机接线+降级)
- Modify: `migration/gui/server.py`(JobStore.begin_shutdown 原子排空+draining 拒新 job;store 挂 `app.state.jobs` 供窗口壳单源取用——评审 v3 P2-5)
- Modify: `pyproject.toml`(dependencies 增 `pywebview>=5.0`;`[project.scripts]` 增 `mcmig-gui = "migration.gui.app:main"`)
- Modify: `migration/cli.py`(_cmd_gui 增 `--window` 旗标转发 app.main)
- Test: `tests/test_gui_app.py`(新;不依赖 pywebview 安装)、`tests/test_gui_server.py`(begin_shutdown/503 两用例)

**Interfaces:**
- Consumes: `create_app()`;`JobStore.is_busy()`/`active_id()`;`app.state.shutdown_requested`(T6 既有);`_cmd_gui` 现浏览器模式。
- Produces:
```python
# migration/gui/app.py
def pick_free_port() -> int                        # 127.0.0.1 随机端口(启动日志报出)
def build_url(port: int) -> str                    # http://127.0.0.1:<port>/
def verify_data_integrity() -> list[str]
    # 窗口入口的数据清单自检(评审 P2-7):复用既有 manifest 校验路径
    # (doctor 同源逻辑抽出的校验函数);失败返回中文错误行列表(非空即弹窗+退出码 2)
def selected_renderer(gui: str) -> str
    # 实际渲染器预检(评审 v3 P2-4+v4 P1):pywebview 在 Windows 上无论 gui=
    # 传什么(除 qt)都 import winforms 平台模块,edgechromium/mshtml 的选择
    # 发生在模块内 —— is_chromium = not is_cef and _is_chromium() and
    # forced_gui != 'mshtml';缺 WebView2 运行时 _is_chromium()=False → 走
    # MSHTML 分支且**只发 log.warning,不抛异常**(guilib.py/winforms.py,
    # r0x0r/pywebview master)。故 `webview.start(gui="edgechromium")` 不足以
    # 保证现代渲染器。
    # 导入形态(评审 v4 P1):**`from webview.guilib import initialize`**——
    # 不能用 `from webview import guilib`:webview/__init__.py 模块级
    # `guilib = None`(start() 内才 `global guilib` 赋值,且顶部
    # `from webview.guilib import initialize` 加载子模块后同名属性仍被 None
    # 覆盖),包属性首启必为 None → AttributeError;子模块函数导入才是真实
    # 接口(__init__.py 自身同款用法)。调用 `initialize(gui)` 返回平台模块,
    # 取 `getattr(mod, "renderer", gui)`(winforms 模块级 renderer 属性,
    # 'edgechromium'/'mshtml'/'cef';非 Windows 平台无该属性,回落 gui 名);
    # initialize 可重复调用(模块导入缓存+setup_app 幂等守卫),start() 内部
    # 再调一次无害。内部 API 风险由 `pywebview>=5.0` 钉版承担(注释说明)。
    # 本函数**可能抛异常**(如缺 pythonnet → WebViewException):调用方(main ③)
    # 以 try/except 包住,按「渲染器不可用」同路径降级,绝不裸抛
def wait_server_ready(port: int, timeout: float = 10.0) -> bool
    # 服务就绪后开窗(评审 P2-7):轮询 GET /api/config 至 200 才 create_window,
    # 避免窗口先于服务打开时的加载错误页;超时 False → 按启动失败处理;
    # 实现:http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)——
    # host/port/timeout 三参显式(评审 v3 P2-5:HTTPConnection 首位形参是 host,
    # 传 port 会错位)
def should_block_close(store) -> tuple[bool, str | None]
    # 关闭边界(评审 v3 P2-5 原子化):调 store.begin_shutdown()——锁内判定,
    # busy → (True, 当前 job 描述)且状态不变;空闲 → 置 draining 并 (False, None)。
    # 「检查空闲」与「拒绝新任务」同一临界区完成,封死「检查为空闲→新迁移启动→
    # 窗口退出」竞争窗
def main(argv: Sequence[str] | None = None) -> int
    # ①verify_data_integrity 非空 → 弹窗(消息框)+return 2(缺 Python 包以外的
    #   第一道失败防护,评审 P2-7)
    # ②import webview 失败 → 打印指引(安装链接)并**降级**调 _cmd_gui 浏览器模式
    # ③**渲染器预检(评审 v3 P2-4+v4 P1,先于服务线程——免「起即拆」)**:
    #   webview 可导入后,在 try/except 中调用 selected_renderer("edgechromium")
    #   ——函数自身可能抛异常(缺 pythonnet 的 WebViewException 等),异常与
    #   返回值 ≠"edgechromium"(即 MSHTML——页面依赖 EventSource/ES5+,IE11
    #   形态不可用)同走降级分支:记日志、提示「安装 WebView2 运行时(链接)
    #   或改用 mcmig gui」、**降级浏览器模式**——不起 uvicorn 线程、不进
    #   webview.create_window/start,窗口根本不打开
    # ④app = create_app() → store = app.state.jobs(**单源**:关闭守卫/停机轮询/
    #   HTTP 服务同一 JobStore,评审 v3 P2-5)→ pick_free_port →
    #   _start_server_thread(app, port)(uvicorn.Config(app) 直接吃 app 实例)
    #   → wait_server_ready 超时 → 停线程+return 2
    # ⑤就绪后 webview.create_window(closing=lambda: not should_block_close(
    #   store)[0];阻止时窗口停留并提示);停机轮询线程 0.5s 检查
    #   app.state.shutdown_requested(页面「退出服务」)→ window.destroy;
    #   webview.start(gui="edgechromium") 抛错为**二道防线**(EdgeChrome 初始化
    #   失败仍可能 start 期抛错):同样停线程+提示+降级(评审 P2-7:缺包/缺
    #   运行时/启动异常三段失败路径全部有出路,不留僵尸线程)
    # ⑥退出码:正常 0;自检失败/端口占用/uvicorn 启动失败/就绪超时 2(中文报错);
    #   finally 恒置 server.should_exit 并 join 线程(所有异常路径回收)
# migration/gui/server.py(评审 v3 P2-5+v4 P2-2)
#   JobStore.begin_shutdown() -> tuple[bool, str | None]
#     锁内原子判定:busy → (True, f"任务 {active_id()} 正在执行")(状态不变);
#     空闲 → self._draining = True 并 (False, None)。
#   **draining 拒绝进 start() 的临界区(评审 v4 P2-2,白名单⑪)**:
#   JobStore.start() 在 self._lock 内**首先**判 _draining → 抛 StoreDraining
#   (新异常类,server 内定义);随后才查 _current 占用(None→调用方 409)。
#   端点不再做锁外预检——捕获 StoreDraining → ApiError(503, shutting_down
#   三段式)。判定与注册同锁,「端点过检→关窗→start 照常注册」的交错不存在。
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_gui_app.py(新)
"""pywebview 窗口壳:关闭边界/停机接线/降级路径(pywebview 未安装不炸)。"""
import sys

import migration.gui.app as gui_app

def test_should_block_close_idle_vs_busy():
    """spec §5.3/T6:空闲放行(begin_shutdown 置 draining);job 运行阻止并
    给出当前任务描述(状态不变)。"""
    from migration.gui.server import Job, JobStore
    store = JobStore()
    assert gui_app.should_block_close(store) == (False, None)
    assert store.draining is True                 # 空闲判定即原子进入排空态
    job = Job("j1", "migrate"); store._current = job; store._jobs["j1"] = job
    store2 = JobStore()
    store2._current = job; store2._jobs["j1"] = job
    blocked, desc = gui_app.should_block_close(store2)
    assert blocked and desc and store2.draining is False

def test_begin_shutdown_drains_when_idle():
    """评审 v3 P2-5:begin_shutdown 锁内原子判定——忙则阻止且状态不变(可
    再判定);闲则置 draining(503 拒新 job 的端点级用例在 test_gui_server.py)。"""
    from migration.gui.server import Job, JobStore
    store = JobStore()
    assert store.begin_shutdown() == (False, None)
    assert store.draining is True                  # 空闲判定即原子进入排空态
    job = Job("j1", "migrate")
    store2 = JobStore()
    store2._current = job; store2._jobs["j1"] = job
    blocked, desc = store2.begin_shutdown()
    assert blocked and desc and store2.draining is False

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
    assert isinstance(port, int) and 0 < port < 65536   # 可用端口须有值(评审 v3 P2-6)
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.close()

def test_wait_server_ready_true_on_200():
    """评审 P2-7:就绪探测——服务应答 200 即 True(临时 uvicorn 起停)。"""
    import threading
    import uvicorn
    from migration.gui.server import create_app
    port = gui_app.pick_free_port()
    srv = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1",
                                        port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    assert gui_app.wait_server_ready(port, timeout=10.0) is True
    srv.should_exit = True; t.join(timeout=5.0)

def test_wait_server_ready_false_on_timeout(monkeypatch):
    """就绪超时 False(无服务监听的端口)——按启动失败路径处理。"""
    import socket
    port = gui_app.pick_free_port()
    s = socket.socket()                      # 占住端口但不答 HTTP
    s.bind(("127.0.0.1", port)); s.listen(1)
    try:
        assert gui_app.wait_server_ready(port, timeout=0.3) is False
    finally:
        s.close()

def test_mshtml_renderer_rejected_before_start(monkeypatch):
    """评审 v3 P2-4:预检发现实际 renderer=mshtml(WebView2 缺失时 pywebview 的
    静默回退形态)→ **不起服务线程**、不进 webview.create_window/start,降级
    浏览器模式(缺运行时的主防线;start 抛错是二道防线,见下两用例)。

    假件注入点= `sys.modules["webview.guilib"]`(评审 v4 P1:真实接口是
    `from webview.guilib import initialize`,经子模块路径解析;**不设**
    `fake.guilib` 包属性——包属性初始为 None 正是被修的坑,设了反而掩盖)。
    """
    import types
    fake_guilib = types.ModuleType("webview.guilib")
    fake_winforms = types.ModuleType("webview.platforms.winforms")
    fake_winforms.renderer = "mshtml"              # 预检信号:实际选中 MSHTML
    fake_guilib.initialize = lambda gui: fake_winforms
    fake = types.ModuleType("webview")
    started = {"n": 0}

    def _must_not_start(*a, **k):
        started["n"] += 1
        raise AssertionError("mshtml 形态不得进入 webview.start")

    fake.start = _must_not_start
    fake.create_window = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setitem(sys.modules, "webview.guilib", fake_guilib)
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: [])
    served = {"n": 0}

    def _track_server(app, port):
        served["n"] += 1
        return None, None

    monkeypatch.setattr(gui_app, "_start_server_thread", _track_server)
    fell_back = {"rc": None}

    def _fake_gui(args):
        fell_back["rc"] = 0
        return 0

    monkeypatch.setattr("migration.cli._cmd_gui", _fake_gui)
    rc = gui_app.main([])
    assert rc in (0, 2) and started["n"] == 0
    assert served["n"] == 0 and fell_back["rc"] == 0   # 未起线程+已降级

def test_renderer_precheck_exception_falls_back(monkeypatch):
    """评审 v4 P1:预检自身抛异常(如缺 pythonnet → WebViewException)→ 与
    「渲染器不可用」同路径降级浏览器模式,不起服务线程、不裸抛。"""
    import types
    fake_guilib = types.ModuleType("webview.guilib")

    def _boom(gui):
        raise RuntimeError("You must have pythonnet installed")

    fake_guilib.initialize = _boom
    fake = types.ModuleType("webview")

    def _must_not_start(*a, **k):
        raise AssertionError("预检失败不得进入 webview.start")

    fake.start = _must_not_start
    fake.create_window = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setitem(sys.modules, "webview.guilib", fake_guilib)
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: [])
    served = {"n": 0}

    def _track_server(app, port):
        served["n"] += 1
        return None, None

    monkeypatch.setattr(gui_app, "_start_server_thread", _track_server)
    fell_back = {"rc": None}

    def _fake_gui(args):
        fell_back["rc"] = 0
        return 0

    monkeypatch.setattr("migration.cli._cmd_gui", _fake_gui)
    rc = gui_app.main([])
    assert rc in (0, 2) and served["n"] == 0 and fell_back["rc"] == 0

def test_webview_start_failure_falls_back_and_stops_server(monkeypatch):
    """评审 P2-7+v3 P2-4+v4 P2-4:渲染器预检通过(edgechromium)但 webview.start
    期抛错(EdgeChrome 初始化失败的二道防线)→ 停 uvicorn 线程+降级浏览器
    模式+退出码有定义,不留僵尸线程;start 确实被调用、降级确实发生。"""
    import types
    fake_guilib = types.ModuleType("webview.guilib")
    fake_winforms = types.ModuleType("webview.platforms.winforms")
    fake_winforms.renderer = "edgechromium"        # 预检通过,异常在 start 期
    fake_guilib.initialize = lambda gui: fake_winforms
    fake = types.ModuleType("webview")
    started = {"n": 0}

    def _boom(*a, **k):
        started["n"] += 1
        raise RuntimeError("WebView2 runtime not found")

    fake.start = _boom

    class _FakeClosing:
        def __iadd__(self, handler):
            return self

    class _FakeWindow:
        # 完整假窗口(评审 v4 P2-4:返回 None 会在 window.events.closing 接线
        # 即 AttributeError,触不到 start())
        events = types.SimpleNamespace(closing=_FakeClosing())

        def destroy(self):
            pass

    fake.create_window = lambda *a, **k: _FakeWindow()
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setitem(sys.modules, "webview.guilib", fake_guilib)
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: [])
    monkeypatch.setattr(gui_app, "wait_server_ready", lambda port, timeout=10.0: True)
    stopped = {"called": False}

    class _FakeServer:
        should_exit = False

    monkeypatch.setattr(gui_app, "_start_server_thread",
                        lambda app, port: (_FakeServer(), None))

    def _fake_stop(srv):
        stopped["called"] = True; srv.should_exit = True

    monkeypatch.setattr(gui_app, "_stop_server", _fake_stop)
    fell_back = {"rc": None}

    def _fake_gui(args):
        fell_back["rc"] = 0
        return 0

    monkeypatch.setattr("migration.cli._cmd_gui", _fake_gui)
    rc = gui_app.main([])
    assert rc in (0, 2)
    assert started["n"] == 1                            # start 被调用且抛错
    assert stopped["called"] and fell_back["rc"] == 0   # 线程已回收+降级已走

def test_verify_data_integrity_delegates(monkeypatch):
    """评审 P2-7:窗口入口复用数据清单自检——校验函数失败行直传(非空即拒)。"""
    monkeypatch.setattr(gui_app, "_manifest_errors", lambda: ["data/rules.yaml 校验失败"])
    assert gui_app.verify_data_integrity() == ["data/rules.yaml 校验失败"]
    monkeypatch.setattr(gui_app, "_manifest_errors", lambda: [])
    assert gui_app.verify_data_integrity() == []
```

```python
# tests/test_gui_server.py 追加(T9:draining 拒新 job 的端点级用例)
def test_job_submission_rejected_while_draining(tmp_path, monkeypatch):
    """评审 v3 P2-5:begin_shutdown 置 draining 后,新 job 提交 503(plan/
    migrate/swap 三入口同一守卫,此处以 plan 代表)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    client.app.state.jobs.begin_shutdown()
    resp = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    assert resp.status_code == 503

def test_job_submission_rejected_when_drain_lands_during_start(tmp_path, monkeypatch):
    """评审 v4 P2-2:draining 判定必须在 JobStore.start() 的临界区内——以
    「drain-then-start」交错注入模拟「端点过检→关窗→注册」的真实交错:
    判定仅在端点锁外预检时,本交错会照常建 job(红);锁内首判 → 503(绿)。"""
    from migration.gui.server import JobStore
    game, client = _make_client(tmp_path, monkeypatch)
    real_start = JobStore.start

    def _drain_then_start(self, *a, **k):
        self.begin_shutdown()                 # 注册前一瞬关窗(交错点)
        return real_start(self, *a, **k)

    monkeypatch.setattr(JobStore, "start", _drain_then_start)
    resp = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    assert resp.status_code == 503
```

(实现侧配合:`_manifest_errors` 为 doctor 同源校验逻辑的薄封装(manifest.sha256 逐项核对,抽自 cli `_cmd_doctor` 的校验段供两处消费);`_start_server_thread(app, port) -> (server, thread)`(评审 v3 P2-5:接收 app 实例,uvicorn.Config(app) 消费同一 FastAPI 应用——关闭守卫取的 store 即 `app.state.jobs`,与服务端同源)与 `_stop_server(server)` 提为模块级可注入函数,webview.start 抛错/就绪超时/正常退出与 finally 恒调用——测试以此注入假 server/thread 断言回收。)

(降级路径可测的前提:app.main 的回退分支**函数内** lazy import `from ..cli import _cmd_gui`——monkeypatch `migration.cli._cmd_gui` 才能生效,模块顶层 import 会绑死早期引用;实现Step 3 明确此约束。)

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gui_app.py -v`
Expected: FAIL(`No module named 'migration.gui.app'`)。

- [ ] **Step 3: 实现 app.py + 接线**

- `should_block_close(store)`: 直调 `store.begin_shutdown()`(评审 v3 P2-5:原子);实现消费 server 的公开面,不 import cli 私有函数。server 侧配套(评审 v4 P2-2):`JobStore.begin_shutdown()`(锁内:busy → (True, 描述) 状态不变;空闲 → `_draining=True` → (False, None));**`JobStore.start()` 锁内首判 `_draining` → 抛 `StoreDraining`**(新异常类;其后 `_current` 占用返回 None 的 409 语义不变);job 创建端点(plan/migrate/swap preflight+apply)以 `try: store.start(...) except StoreDraining: raise ApiError(503, "err_shutting_down.what", "err_shutting_down.why")` 接线(STRINGS 新增;白名单⑪)——端点**无锁外预检**。
- `selected_renderer(gui)`: `from webview.guilib import initialize`(**不得** `from webview import guilib`——包属性初始为 None,评审 v4 P1);`mod = initialize(gui); return str(getattr(mod, "renderer", gui))`——评审 v3 P2-4 主防线;注释说明内部 API 风险由 `pywebview>=5.0` 钉版承担、非 Windows 平台无 renderer 属性回落 gui 名、函数可抛异常由调用方降级。
- `_start_server_thread(app, port) -> tuple[uvicorn.Server, threading.Thread]` / `_stop_server(server)`:模块级可注入原语(daemon=False);`main` 的所有出口(webview.start 抛错/就绪超时/正常退出)与 finally 恒 `_stop_server`(评审 P2-7:不留僵尸线程)。
- `verify_data_integrity()`:`_manifest_errors()` 直传——doctor 校验段(manifest.sha256 逐项核对)自 `_cmd_doctor` 抽出为共享函数(cli 与窗口壳两处消费);非空 → 消息框(`ctypes.windll.user32.MessageBoxW` 或 print+exit 兜底)+return 2。
- `wait_server_ready(port, timeout)`:0.1s 间隔轮询 `http.client.HTTPConnection("127.0.0.1", port, timeout=0.5).request("GET", "/api/config")` 至 200;超时 False(评审 v3 P2-5:host/port/timeout 三参显式——HTTPConnection 首位形参是 host)。
- `main`:①`verify_data_integrity()` 非空 → return 2;②`import webview`(函数内,未安装→ImportError 捕获→打印「未安装 pywebview,已回退浏览器模式: pip install pywebview / 或使用 mcmig gui」→ **函数内 lazy** `from ..cli import _cmd_gui` 转发浏览器启动,返回其退出码);③`try: selected_renderer("edgechromium") except Exception:` 或返回值 ≠ "edgechromium" → 记日志+提示「安装 WebView2 运行时: https://developer.microsoft.com/microsoft-edge/webview2/ 或改用 mcmig gui」→ lazy `_cmd_gui` 降级(**不起服务线程**,评审 v3 P2-4+v4 P1:预检自身抛错同降级);④`app = create_app()` → `store = app.state.jobs` → `pick_free_port()` → `_start_server_thread(app, port)` → `wait_server_ready(port)` 超时 → 停线程+return 2;⑤就绪后 `window = webview.create_window("mcmig 迁移向导", build_url(port))`;`window.events.closing += lambda: not should_block_close(store)[0]`(返回 False 阻止关闭;阻止时以 JS 求值提示 `window.evaluate_js(...)` 或标题提示,最简实现即可);⑥停机轮询线程 0.5s 检查 `app.state.shutdown_requested` → `window.destroy()`;⑦`webview.start(gui="edgechromium")`(渲染器预检已过,此为显式声明+EdgeChrome 初始化失败的**二道防线**)抛错 → 记日志+提示(同③链接)→ `_stop_server` → lazy `_cmd_gui` 降级浏览器模式;⑧正常路径 `webview.start()` 返回后停 uvicorn+join,return 0。
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

新增条目(每条含步骤与期望):①迁移中刷新页面→游标恢复(进度续显,不重扫)②a **网络瞬断**(代理切换/网卡禁用再启用,服务进程存活)→页面「正在自动重连」→恢复后清横幅、事件按 seq 去重不重复渲染;恢复后**再次断线**(重订连接 URL 残留旧游标)→重连按 Last-Event-ID 请求头续订,不从旧 query 位置反复重放(评审 v3 P2-2/白名单⑨) ②b **服务进程重启**(迁移写盘中杀服务进程再启动)→自动重连必失败;刷新页面后**内存 job 已丢失,不期待恢复任务本体**——验收点是**中断横幅出现且列出待核对清单**(journal write-ahead 的恢复证据),按指引核对后可 dismiss(评审建议 C:杀服务验收的是 journal 中断恢复)③取消迁移→202 后任务恰好完成→界面呈现结果而非「正在停止」④大目录(≥5000 文件)计划渲染与迁移进度流畅(懒建行+rAF;顺带验证搜索命中折叠组时行正确显隐、ASK 勾选在搜索/折叠操作后保持)⑤swap 全流程 GUI 两阶段(预检→篡改目标 mods/ 或切换游戏根→apply 被拒;正常装包→链式计划直入审阅页→迁移后旧包 jar 未回迁;备份就位;**装包完成进入重规划阶段→取消入口隐藏且 cancel 请求 409**——评审 v3 P2-3/白名单⑩)⑥独立窗口:job 运行中关窗被阻止并提示;空闲关窗直接退出(**关窗判定后再提交的新任务被 503 拒**——评审 v3 P2-5/白名单⑪,可借第二浏览器页验证);页面「退出服务」关窗;WebView2 缺失机器上启动→提示+浏览器模式降级(**不闪开 MSHTML 窗口**——评审 v3 P2-4) ⑦POSIX 后端 flock 互斥(CI/Linux 或 WSL 复核,win32 跳过注记)⑧旧布局快照用户 GUI 全链路(plan notice 提示→migrate 不误报)。

- [ ] **Step 3: 版本与全量回归**

`migration/__init__.py` 与 `pyproject.toml` 版本 → `0.12.0`;Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check migration/ tests/`;记录测试总数(预期 621 + 约 55~75,以实际为准,写入 commit body)。

- [ ] **Step 4: Commit**

```bash
git add README.zh-CN.md README.en.md tests/gui-manual-checklist.md migration/__init__.py pyproject.toml
git commit -m "docs(w3): README 双语 W3 能力+手测清单增补+版本 0.12.0 收口 (batchI-W3-T10)"
```

---

## 计划自审记录(v4)

1. **Spec 覆盖**: §5.1(T6,含③结束页备份位置与恢复指引、顶部摘要五要素含将覆盖数)/§5.2(T7+T8,含逐 jar 取消——spec §4.3「取消检查点覆盖 swap 装包」、链式重扫+规划、`/api/swap/apply` 端点名与 spec 逐字一致)/§5.3(T9,含 WebView2/启动失败/就绪开窗/清单自检)/§5.4(滑移 W4 T15,处置表 #28)→ W3 四项全落;§4.1 沿挂(衔接协议页面侧=T5,GET 完整结果载荷=T2)/§4.3 沿挂(中断模型=T3)随行收口;吸收清单 28 项全部显式处置(26 修+1 接受强化+1 撤销过时);**计划评审三轮(2P1+6P2+3建议 / 1P1+5P2+2契约 / 1P1+3P2+2建议)全部收编(见修订记录 v2/v3/v4)**;spec §3.3「检测范围向用户说明」在 T6 STRINGS 固定文案兑现;「两态展示」为 W4 更新面板条目,不入本波。
2. **占位符扫描**: 无 TBD;v3 复扫 0 空壳测试;**v4 复修 4 处测试贴合真实接口**(评审指出:victim.jar 须预检前双侧创建否则先触发指纹失配/done.plan 为 _group_actions 分组无扁平 actions 键/切根夹具须完整版本对否则 422 先挡/假 create_window 返回 None 在 closing 接线即炸——全部改为可验证预期分支);**v4 新增 5 测试**(restoreSavedJob 行为测、startMigrate 接线契约测、预检异常降级测、drain-then-start 交错测、取消窗白盒测);所有步骤含可运行命令与预期输出。
3. **类型一致性**: `PrecheckOutcome`(T1)字段与 CLI/GUI 消费一致;`_Sub.dropped`/`_pump_sub`(T2)三处一致;`_done_payload`→snapshot `"done"` 键在 T2 产出、T5 applyStatus/restoreSavedJob 消费、T8 swap done 复用同形;`JobJournal(..., *, src, dst, game_root)`(T3)六处一致;`_journal_owner_alive`(T3)为 scan 与 dismiss 共用单点;`SwapPreflightOutcome/SwapInstallOutcome/SwapRunOutcome` 在 run_swap 与 CLI 薄壳/GUI 两端点一致;`_swap_fingerprint` 六参与预检/重验两调用点一致;`job.cancel_closed` 在 T7 关窗(锁内置位)/取消端点(锁内重检→409)/T8 页面(隐藏入口)三处同源;`newJob/ownsCurrent/beginRequest/requestAlive/applyEvent/applyStatus/restoreSavedJob`(T5)在契约测/行为测/实现三处同名同语义;`JobStore.begin_shutdown()/StoreDraining`(T9)在窗口壳/`start()` 临界区/端点 503 三处一致(白名单⑪);`selected_renderer` 的导入形态(`from webview.guilib import initialize`)在 Produces/Step3/测试假件(`sys.modules["webview.guilib"]` 注入,不设包属性)三处一致;`_start_server_thread(app, port)`/`app.state.jobs`(T9 单源,T7 先行暴露)与 main 序列/失败路径测试对得上;T1 `_legacy_layout` 解包 3 元组、T6 升 4 元组时在机械更新清单内。
4. **Review Focus ↔ 测试归属**: 六条分别锚定 T2×2/T1/T3/T7/T6 各失败测试,无空挂;T5 的页面接缝由 10 条源码契约测+8 条 node 行为测双钉(v4 增接线契约与恢复行为各 1)。
5. **顺序依赖**: T1→(T2,T3 可并行)→T5→T6→T7→T8→T9→T10;T4 独立(仅 instlock),可穿插;T2 的终态载荷/overflow/reset 契约是 T5 页面处理的前提(done 载荷也是 T5 恢复与 T8 swap done 的地基),T3 的 dismiss/unknown_progress 是 T5 横幅与 T10 手测的前提——已按此排列任务序。
6. **跨任务一致性复核(v2+v3+v4)**: P1-2 链式重规划在 T7(服务端)/T8(页面消费)/T10(手测⑤)三处口径一致(旧包 jar 不回迁;链式段 scan src+dst 与 plan job 同型);游标优先级=「有效头>query>0」在 T2 端点与 T5 规则①②互补不矛盾;回调双轨在 T5(定义/测试/restoreSavedJob)/T8(swap 流接线)一致;P2-3 取消窗在 T7(锁内关窗+端点锁内重检 409/白盒+屏障双测)/T8(隐藏入口)/T10(手测⑤)闭合——**迟到取消的锁外读路径已消除**(v4 P2-3);SwapRunOutcome 在 T7 产出、CLI 薄壳与 replan 失败分支消费,GUI error 载荷带 install+backup_dir 同源;窗口壳:`selected_renderer` 预检先于服务线程且自身异常同降级(v4 P1)、`_start_server_thread(app, port)` 单源、begin_shutdown 与 start() 同锁排空(v4 P2-2),三条失败路径(缺包/缺运行时/启动异常)各有独立测试且断言降级与线程回收确实发生;P2-8 的 409 语义与 T3 探针单点复用无循环依赖(journal→instlock 单向)。
