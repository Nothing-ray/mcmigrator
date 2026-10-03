"""index.html 源码契约测试(终审修复 C1/I3)。

无 JS 测试基建下的接缝钉:页面行为无法在 pytest 中执行,改为对页面源码断言
**语义特征**(正则特征而非整段字符串比对)——锁定三件事:
1. migrate 请求体必须携带 plan_id(批次I-T3:缺失必被 plan_id_missing 拒绝);
2. plan job 的 onDone 必须捕获 ev.plan_id,且 persisted===false 分支渲染三段式
   错误并禁用「执行迁移」(未落盘的计划不可执行);
3. 部分执行后的三处重试引导均以「重新生成计划」为前提(I3:直接重跑会被目标
   状态校验阻断,文案不得再暗示可直接重执行)。

读取路径与 server._read_index_html 同源(importlib.resources),打包形态同测。
"""

from __future__ import annotations

import re
from importlib import resources

_PAGE = resources.files("migration.gui").joinpath("index.html").read_text(encoding="utf-8")


def test_migrate_request_body_carries_plan_id() -> None:
    """C1:POST /api/migrate 请求体构造必须含 plan_id 键(语义特征,非整段比对)。"""
    m = re.search(r'postJson\("/api/migrate",\s*\{([^}]*)\}', _PAGE)
    assert m, "页面必须以 postJson 构造 /api/migrate 请求"
    body = m.group(1)
    for key in ("src", "dst", "ask_yes", "dry_run", "plan_id"):
        assert re.search(rf"\b{key}\s*:", body), f"migrate 请求体缺少 {key} 键"


def test_plan_done_captures_plan_id() -> None:
    """C1:onDone 必须把 done 事件的 plan_id 存入页面状态(migrate 请求的数据源)。"""
    assert re.search(r"planId\s*=\s*ev\.plan_id", _PAGE), "onDone 应捕获 ev.plan_id"
    # 状态声明存在且初始为 null(页面刷新丢失后依赖 plan_id_missing 错误引导重新生成)
    assert re.search(r"var\s+planId\s*=\s*null", _PAGE)
    # 重新生成计划时旧 planId 作废(resetStep2 内)
    reset = re.search(r"function\s+resetStep2\(\)\s*\{(.*?)\n\}", _PAGE, re.S)
    assert reset and re.search(r"planId\s*=\s*null", reset.group(1))


def test_plan_done_persisted_false_blocks_execution() -> None:
    """C1:persisted===false 分支存在——渲染三段式错误、禁用执行按钮并提前返回。"""
    m = re.search(r"if\s*\(ev\.persisted\s*===\s*false\)\s*\{(.*?)return;", _PAGE, re.S)
    assert m, "onDone 必须有 persisted===false 分支且提前 return(不渲染空分组)"
    branch = m.group(1)
    assert re.search(r'renderError\(\$\("step2-error"\),\s*ev\.what,\s*ev\.why,\s*ev\.details\)',
                     branch), "persisted:false 须以 done 事件自带的三段式字段渲染错误"
    assert re.search(r'\$\("btn-migrate"\)\.disabled\s*=\s*true', branch), \
        "persisted:false 须禁用「执行迁移」按钮"
    assert re.search(r"planId\s*=\s*null", branch), "persisted:false 须作废计划标识"


def test_retry_guidance_requires_replan() -> None:
    """I3:三处部分执行后的重试引导(失败清单/取消注记/中断横幅)均以重新生成计划为前提。"""
    assert "重新生成计划再执行" in _PAGE        # 失败清单注记(步③)
    assert "重新生成计划后再执行" in _PAGE      # 取消终态注记(步③)
    assert "重新生成计划再继续迁移" in _PAGE    # 中断横幅(页面加载)
    # 旧措辞「可返回重新执行」(无前提的直接重执行暗示)必须清除
    assert "可返回重新执行" not in _PAGE


def test_retry_guidance_keeps_identical_skip_explanation() -> None:
    """I3:保留 identical 跳过说明,但前提明确挂在「重新生成的计划」上。"""
    assert _PAGE.count("按「内容一致」跳过") >= 3  # 失败清单/取消注记/中断横幅三处
    for m in re.finditer(r"[^\n]*按「内容一致」跳过[^\n]*", _PAGE):
        assert "重新生成" in m.group(0), f"identical 跳过说明须以重新 plan 为前提: {m.group(0)}"


def test_shutdown_copy_does_not_claim_exit() -> None:
    """W2.5 复审 B6:退出按钮成功文案不得声称「服务已退出」。

    停机标志已置但进程退出由启动方轮询执行(T10 接线前仍在运行)——假承诺
    会让玩家关页后留下存活的控制台进程。
    """
    branch = re.search(r"function\s+shutdownServer[\s\S]*?\}\)\.catch", _PAGE)
    assert branch, "页面必须有 shutdownServer 函数"
    body = branch.group(0)
    assert "已请求退出" in body            # 陈述请求而非完成
    assert "服务已退出" not in body        # 假承诺清除


def test_hidden_attribute_beats_display_rules() -> None:
    """W2.6 复审附带(预存缺陷):`[hidden]{display:none!important}` 必须存在。

    .warn{display:inline-block} 等类样式会压制 UA 对 hidden 属性的默认
    display:none(MDN hidden 属性说明)——源与目标不同时「不能是同一个版本」
    警告仍显示(reviewer 实测)。全局 [hidden] 提升 + !important 一处收口。
    """
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important;?\s*\}", _PAGE), (
        "CSS 须含 [hidden]{display:none!important} 压制类样式对 hidden 属性的覆盖"
    )


# ---------------------------------------------------------------------------
# 批次I-W3 T5:页面 Presentation Model + 事件恢复协议(源码契约接缝钉)
# ---------------------------------------------------------------------------


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
    """评审 v4 T5 建议 + 评审④ P1 修订:startMigrate 发请求前取 generation(迟到
    错误丢弃),成功回执经 adoptJobReceipt 完成接管(接管函数内 newJob+openEvents
    ——回执不得因更晚请求的请求代际被静默丢弃)。"""
    m = re.search(r"function\s+startMigrate\(\)\s*\{[\s\S]*?\n\}", _PAGE)
    assert m, "须有 startMigrate"
    body = m.group(0)
    assert "beginRequest()" in body and "/api/migrate" in body
    assert body.index("beginRequest()") < body.index("/api/migrate"), \
        "generation 须在发请求前捕获"
    for needle in ("requestAlive(", "adoptJobReceipt("):
        assert needle in body, f"startMigrate 回调缺 {needle}"


def test_all_job_receipts_adopt() -> None:
    """评审④ P1 + 评审⑤ P1/P2①:五处任务创建回执统一经 adoptJobReceipt 完成
    接管,且必须携带创建请求代际(接管排序的凭据)——回执被弃=后台任务失控
    (无 jobId/游标/SSE/取消),而单锁仍被占用;缺代际则迟到回执无从判定。"""
    for fn in ("startPlan", "startMigrate", "startSwapPreflight", "startSwapApply",
               "startUpdateDownload"):
        m = re.search(rf"function\s+{fn}\(\)\s*\{{[\s\S]*?\n\}}", _PAGE)
        assert m, f"须有 {fn}"
        body = m.group(0)
        assert "adoptJobReceipt(" in body, f"{fn} 的回执未经 adoptJobReceipt 接管"
        assert re.search(r"adoptJobReceipt\([^\n]*,\s*gen\)", body), \
            f"{fn} 的回执未携带创建请求代际(排序接管,评审⑤)"


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


# ---------------------------------------------------------------------------
# 批次I-W3 T6:审阅页 IA 重构(spec §5.1/T7)——摘要条/折叠组/懒建行/rAF 合批
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 批次I-W3 T8:swap 页面两阶段交互+链式计划直入审阅页+边界文案(spec §5.2/T9)
# ---------------------------------------------------------------------------


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
    (服务端该阶段 cancel 已 409,按钮不得留在界面上误导用户)。

    T13.5 起判定收敛进 cancelHintVisible 状态核(零 DOM,行为测试直测),
    契约锚改为 render 分支对状态核的消费点。
    """
    m = re.search(r'var swapReplanning = cancelHintVisible\(jobModel\);[\s\S]{0,300}', _PAGE)
    assert m, "render 分支须经 cancelHintVisible 状态核识别 replan 阶段"
    seg = m.group(0)
    assert re.search(r'btn-cancel', seg), "replan 分支须触达取消入口"
    assert re.search(r'hidden|disable', seg), "取消入口须隐藏或禁用"
    assert "不可取消" in _PAGE                     # 边界文案(PAGE_STRINGS)

def test_no_plain_replan_after_swap() -> None:
    """评审 P1-2:swap 完成路径不得调用普通 /api/plan(其 modpack_swap=False)。

    T13.5-7:锚点从标记区(btn-swap-apply 起、section 尾止)移到 **swap 函数区**
    ——标记区不含函数体,函数改名/移动即漏判;函数区覆盖装包/预检/放弃全部路径。
    """
    m = re.search(r"function startSwapApply\(\)[\s\S]*?(?=function restoreSavedJob)", _PAGE)
    assert m, "页面须有 startSwapApply 起的 swap 函数区"
    assert 'postJson("/api/plan"' not in m.group(0),         "swap 流程内不得回退到普通 plan(丢失换包模式)"


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


# ---- 修复 A7b:过程提示与中断清单可见化 ----


def test_notice_events_have_dom_outlet() -> None:
    """修复 A7b:notice/warning 事件此前只入模型、全页无渲染路径——顶部提示
    横幅 #banner-notices 必须存在,render() 经 renderNotices 铺入(唯一 DOM 出口)。"""
    assert re.search(r'id="banner-notices"', _PAGE), "页面须有过程提示横幅容器"
    render = re.search(r"function\s+render\(\)\s*\{(.*?)\n\}", _PAGE, re.S)
    assert render and "renderNotices()" in render.group(1), "render() 须调用 renderNotices"
    lines = re.search(r"function\s+noticeLines\(\)\s*\{(.*?)\n\}", _PAGE, re.S)
    assert lines and "notices" in lines.group(1), "文本派生须读 jobModel.notices"
    view = re.search(r"function\s+renderNotices\(\)\s*\{(.*?)\n\}", _PAGE, re.S)
    assert view and "banner-notices" in view.group(1), "渲染须写入提示横幅容器"


def test_interrupted_banner_lists_entries() -> None:
    """修复 A7b:中断横幅此前只计数、不列清单(计划自设验收 J2b 未兑现)——
    loadInterrupted 必须逐条渲染 entries(文件/计划动作/备份位置)与整体进度
    未知文案,而非只做计数求和。"""
    body = re.search(r"function\s+loadInterrupted\(\)\s*\{(.*?)\n\}", _PAGE, re.S)
    assert body, "loadInterrupted 须存在"
    seg = body.group(1)
    assert re.search(r"it\.entries", seg), "须遍历 entries(不只计数)"
    assert "interruptedEntry" in seg, "条目文本须经 PAGE_STRINGS.interruptedEntry 派生"
    assert "INTERRUPTED_UNKNOWN" in seg, "整体进度未知文案须渲染"
    assert "details" in seg, "明细以折叠容器承载(懒展开)"
    for key in ("interruptedItem", "interruptedEntry", "INTERRUPTED_UNKNOWN"):
        assert re.search(rf"\b{key}\s*:", _PAGE), f"PAGE_STRINGS 缺少 {key}"


def test_terminal_title_branches_on_pure_failure() -> None:
    """评审(0.12.0 复审#9):纯失败终态(守卫/预检/journal 失败,无逐文件
    summary)不得显示「迁移完成」——renderMigrateTerminal 必须在 failed>0 的
    部分失败分支之外,按 status=failed/error 显式分支到 DONE_FAILED。"""
    assert re.search(r"\bDONE_FAILED\s*:", _PAGE), "PAGE_STRINGS 缺少 DONE_FAILED"
    seg = re.search(r"function\s+renderMigrateTerminal\(\)\s*\{(.*?)\n\}", _PAGE, re.S)
    assert seg, "renderMigrateTerminal 须存在"
    body = seg.group(1)
    assert re.search(r'jobModel\.status === "failed" \|\| jobModel\.error', body), (
        "须有纯失败分支(status=failed 或存在 error)"
    )
    assert "DONE_FAILED" in body, "失败分支落到 DONE_FAILED 标题"


def test_restore_backfills_swap_route_selections() -> None:
    """评审(0.12.0 复审#10):swap 预检刷新恢复后,apply 的路线比对需要
    ①swapSrc/swapDst 从恢复模型派生(restoreSavedJob)②向导下拉回填
    (loadVersions 完成后 restoreRouteSelections)——否则有效预检被误判
    「向导已改动」挡成死路。"""
    restore = re.search(r"function\s+restoreSavedJob\(\w+\)\s*\{(.*?)\n\}", _PAGE, re.S)
    assert restore, "restoreSavedJob 须存在"
    assert re.search(r'swap_preflight', restore.group(1)) and "swapSrc = jobModel.src" in restore.group(1), (
        "恢复路径须从模型派生 swapSrc"
    )
    assert re.search(r"function\s+restoreRouteSelections\(\)\s*\{", _PAGE), (
        "restoreRouteSelections 须存在"
    )
    lv = re.search(r"function\s+loadVersions\(\)\s*\{(.*?)\n  \}\n", _PAGE, re.S)
    assert lv and "restoreRouteSelections()" in lv.group(1), (
        "loadVersions 拉到版本列表后须回填恢复路线"
    )


def test_update_panel_elements_and_strings() -> None:
    """面板骨架+文案;行为红线:无「立即运行」(spec §4.3.2/§5.1 两态展示)。"""
    for needle in ('id="update-panel"', 'id="btn-update-check"', 'id="btn-update-download"',
                   'id="btn-update-open"', 'id="update-status"', 'id="btn-update-cancel"'):
        assert needle in _PAGE, f"缺更新面板元素 {needle}"
    assert "已下载,尚未应用" in _PAGE          # 两态展示(spec §5.1 呈现规范)
    assert "立即运行" not in _PAGE              # 行为红线(spec §4.3.2)
    assert "替换" in _PAGE and "重新启动" in _PAGE   # 三步指引要素


def test_handlers_dispatch_includes_update() -> None:
    assert 'kind === "update"' in _PAGE and "updateHandlers()" in _PAGE


def test_apply_event_progress_branch_present() -> None:
    assert "jobModel.bytes" in _PAGE and '"progress"' in _PAGE


def test_update_open_location_never_startfiles_asset() -> None:
    """打开位置安全:页面经服务端 explorer /select,绝无 startfile 资产调用。"""
    assert "openUpdateLocation" in _PAGE and "/api/update/open-location" in _PAGE
    assert "startfile" not in _PAGE


def test_t135_page_closeout_anchors() -> None:
    """T13.5 页面收口:纯函数/文案锚(行为细节在 node 行为测试)。"""
    assert "cancelHintVisible" in _PAGE and "cancelButtonText" in _PAGE
    assert "resyncFailureText" in _PAGE and "RESYNC_GIVE_UP" in _PAGE
    assert "SWAP_CANCEL_BUTTON" in _PAGE and "CANCEL_BUTTON" in _PAGE
    # src/dst 入模型(T13.5-4 终态 preflight 复燃的输入)
    assert "jobModel.src = ev.src" in _PAGE and "jobModel.dst = ev.dst" in _PAGE
