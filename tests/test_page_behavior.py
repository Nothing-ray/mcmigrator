"""页面状态核行为测试:node harness 实际执行 index.html 的脚本段。

评审建议 A 的落地范围裁决:正则契约只能证明「函数存在」,证不了「迟到响应/
事件去重/reset 恢复」的行为——但为整页引 jsdom 级基建不成比例。折中:T5 的
状态核(applyEvent/applyStatus/newJob/ownsCurrent/cancel 迟到守卫)设计为
**零 DOM 依赖的纯函数**(render 才碰 DOM),harness 以最小 DOM/fetch/
EventSource 桩加载脚本(DOMContentLoaded 不触发→init 不接线,函数定义可用),
对状态核做确定性断言;render/DOM 仍归契约测+浏览器手测。

无 node 环境(git bash/CI 未装)自动跳过;node 存在时全平台可跑。

执行方式说明:整段程序(harness+页面脚本+测试)写入临时 .js 文件后以
``node <file>`` 执行——Windows CreateProcess 命令行上限约 32K 字符,页面
脚本段已近 30K,走 ``node -e <program>`` 会撞限;临时文件全平台等价。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
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
globalThis.scrollTo = function () {};           // showStep 的窗口滚动(行为桩)
globalThis.sessionStorage = { _s: {}, getItem(k){ return this._s[k] ?? null; },
  setItem(k, v){ this._s[k] = String(v); }, removeItem(k){ delete this._s[k]; } };
globalThis.console.log = function () {};        // 页面脚本日志静默(不污染 stdout)
globalThis.emit = function (o) {                // 测试结果输出通道(评审 v3 P2-6:
    process.stdout.write(JSON.stringify(o) + "\n");  // console.log 已被桩空,测试不得依赖它)
};
globalThis.EventSource = function () { return { close(){}, addEventListener(){} }; };
"""


def _run_js(program: str) -> dict:
    """在 node harness 中执行页面脚本+测试程序,回传 JSON 结果(emit 单通道输出)。

    程序落临时文件再执行(见模块 docstring 的 Windows 命令行长度说明);
    Promise 微任务在 node 事件循环清空时自然排干,resolve 后的 emit 先于进程退出。
    """
    full = _HARNESS + "\n" + _SCRIPT + "\n" + program
    fd, path = tempfile.mkstemp(suffix=".js", text=False)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(full)
        out = subprocess.run([NODE, path], capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
    finally:
        os.unlink(path)
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


def test_cancel_late_202_does_not_pollute_new_generation():
    """复审修复 #1:取消 202 回调按**入口捕获的 jobId** 做归属校验——j1 的
    取消 POST 在途时用户离开执行页并开启新迁移(代际切到 j2),j1 迟到的
    202 不得把 j2 的模型写回「正在停止」中间态(不得回读 live 变量
    currentMigrateJobId——它已指向 j2,守卫会误放行)。"""
    r = _run_js(r"""
      newJob("j1", "migrate");
      currentMigrateJobId = "j1";
      var resolved = null;
      globalThis.fetch = function () {
        return new Promise(function (resolve) { resolved = resolve; });  // 挂起 j1 的取消 POST
      };
      cancelMigrate();                        // j1 取消:POST 在途
      newJob("j2", "migrate");                // 离开执行页→重新执行迁移:代际切换
      currentMigrateJobId = "j2";
      resolved({ ok: true, json: function () { return Promise.resolve({ status: "cancelling" }); } });
      setTimeout(function () {                // 宏任务:等全部微任务(202 回调链)排干
        emit({ jobId: jobModel.jobId, status: jobModel.status });
        if (cancelPollTimer) { clearInterval(cancelPollTimer); cancelPollTimer = null; }
      }, 0);
    """)
    assert r == {"jobId": "j2", "status": "running"}, \
        "迟到的 j1 取消 202 不得污染 j2 的运行态"


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


def test_notice_lines_derive_from_model():
    """修复 A7b:notice/warning 事件此前只入模型、全页无渲染路径(旧布局快照/
    规则回退/中断锁提示用户不可见)——文本派生 noticeLines 自 jobModel.notices
    生成行(warning 带 ⚠ 前缀),新代际清空。"""
    r = _run_js(r"""
      newJob("j1", "plan");
      applyEvent({type:"notice", text:"检测到旧布局快照", seq:1});
      applyEvent({type:"warning", code:"snapshot_stale", message:"快照比计划新", seq:2});
      var lines = noticeLines();
      newJob("j2", "plan");                  // 代际重置:notices 清空
      emit({lines: lines, after: noticeLines()});
    """)
    assert r["lines"] == ["检测到旧布局快照", "⚠ 快照比计划新"]
    assert r["after"] == []


def test_dismiss_failure_shows_server_why():
    """复核 Minor:dismiss 失败时按钮呈现服务端三段式的 why(句柄占用/保留名
    等具体中文指引),无 why 时回退通用文案——修复前一律通用文案,服务端
    指引不可达,用户只会盲目重试。"""
    r = _run_js(r"""
      var b1 = { textContent: "" }, b2 = { textContent: "" };
      globalThis.fetch = function (url) {
        var withWhy = url.indexOf("first") >= 0;
        return Promise.resolve({ ok: false, status: 409, json: function () {
          return Promise.resolve(withWhy
            ? { what: "清除失败:无法删除", why: "文件被占用或为系统保留名;请手动删除" }
            : {});
        } });
      };
      dismissInterrupted("first", b1);
      dismissInterrupted("second", b2);
      setTimeout(function () {
        emit({ specific: b1.textContent, fallback: b2.textContent,
               generic: PAGE_STRINGS.INTERRUPTED_DISMISS_FAILED });
      }, 10);
    """)
    assert r["specific"] == "文件被占用或为系统保留名;请手动删除"
    assert r["fallback"] == r["generic"]


def test_migrate_retry_falls_back_to_review_plan_id():
    """评审(0.12.0 复审#9):返回审阅页重试迁移时,jobModel 已切到 migrate
    代际(newJob 清空 planId),请求的 plan_id 应回退全局 planId(与审阅页
    同生命周期)——修复前发 null 被服务端 422 拒成死路,页面自家承诺的
    「修正后可直接重试」失效。"""
    r = _run_js(r"""
      newJob("m1", "migrate");            // 执行后的代际:jobModel.planId 已清
      planId = "pf-9";                    // 审阅页所示计划(全局,随页面生命周期)
      var sent = null;
      globalThis.fetch = function (url, opts) {
        sent = { url: url, body: JSON.parse(opts.body) };
        return Promise.resolve({ ok: true, json: function () {
          return Promise.resolve({ job_id: "m2" });
        } });
      };
      startMigrate();
      setTimeout(function () { emit({ plan_id: sent && sent.body.plan_id }); }, 20);
    """)
    assert r["plan_id"] == "pf-9"


def test_resync_retries_with_backoff_until_success():
    """评审(0.12.0 复审#8):控制帧重同步的 GET 失败(网络瞬断)须带退避
    重试直至成功重订——修复前只挂「正在自动重连」横幅,无任何重试,连接数
    归零、页面静默停更。退避表压到 10/20ms 加速。"""
    r = _run_js(r"""
      RESYNC_BACKOFF_MS = [10, 20];
      newJob("j1", "migrate");
      applyEvent({type:"file", path:"a", index:1, total:1, status:"copied", seq:1});
      var attempts = { n: 0 };
      globalThis.fetch = function () {
        attempts.n += 1;
        if (attempts.n < 3) { return Promise.reject(new TypeError("net down")); }
        return Promise.resolve({ ok: true, status: 200, json: function () {
          return Promise.resolve({ status: "running", kind: "migrate", revision: 1 });
        } });
      };
      resyncAfterControl();
      setTimeout(function () {
        emit({ attempts: attempts.n, reconnected: currentEs !== null,
               pendingTimer: resyncTimer !== null });
      }, 150);
    """)
    assert r["attempts"] == 3                     # 两次失败 + 第三次成功
    assert r["reconnected"] is True               # 成功后重开订阅
    assert r["pendingTimer"] is False             # 成功即清重试句柄


def test_resync_retry_cleared_on_generation_switch():
    """评审(0.12.0 复审#8)代际清理:重试等待期间切换任务(newJob)——未决
    重试必须清理,不得让旧代际的重试在后台触达新任务。"""
    r = _run_js(r"""
      RESYNC_BACKOFF_MS = [50];
      newJob("j1", "migrate");
      var attempts = { n: 0 };
      globalThis.fetch = function () {
        attempts.n += 1;
        return Promise.reject(new TypeError("net down"));
      };
      resyncAfterControl();               // 第一次失败 → 50ms 后重试已排程
      newJob("j2", "plan");               // 代际切换:清未决重试
      setTimeout(function () { emit({ attempts: attempts.n, timer: resyncTimer !== null }); }, 150);
    """)
    assert r["attempts"] == 1                     # 重试未再触发
    assert r["timer"] is False


def test_restore_derives_swap_preflight_route():
    """评审(0.12.0 复审#10):swap 预检终态刷新恢复——swapSrc/swapDst 须从
    恢复模型派生(修复前全局为空串,apply 的路线比对必失败成死路)。"""
    r = _run_js(r"""
      globalThis.fetch = function () {
        return Promise.resolve({ ok: true, status: 200, json: function () {
          return Promise.resolve({
            kind: "swap_preflight", status: "succeeded", revision: 3,
            done: { type: "done", job_kind: "swap_preflight", src: "v227",
                    dst: "v228", preflight_id: "pf-1", incompat: [], extras: [],
                    conflicts: [], seq: 3 }
          });
        } });
      };
      restoreSavedJob({ job_id: "p1", seq: 3 }).then(function () {
        emit({ swapSrc: swapSrc, swapDst: swapDst, modelSrc: jobModel.src,
               planError: jobModel.error === null });
      });
    """)
    assert r["swapSrc"] == "v227" and r["swapDst"] == "v228"
    assert r["modelSrc"] == "v227"


def test_update_progress_and_two_state_done():
    """Review Focus:progress 进状态核;done 进入 downloaded 两态(含 plan 字段)。"""
    r = _run_js(r"""
      newJob("j1", "update");
      applyEvent({type:"progress", received: 1048576, total: 31457280, seq: 1});
      applyEvent({type:"done", job_kind:"update", version:"9.9.9",
                  asset_name:"mcmig-gui-9.9.9-win-x64.exe",
                  path:"D:/mcmig/data/update-staging/u/a.exe",
                  size:31457280, sha256:"ab", seq: 2});
      emit({bytes: jobModel.bytes ? [jobModel.bytes.received, jobModel.bytes.total] : null,
            status: updateModel.status,
            ver: updateModel.plan ? updateModel.plan.version : null});
    """)
    assert r["bytes"] == [1048576, 31457280]
    assert r["status"] == "downloaded"
    assert r["ver"] == "9.9.9"


def test_update_error_and_cancel_states():
    r = _run_js(r"""
      newJob("j1", "update");
      applyUpdateEvent({type:"error", what:"校验失败", why:"SHA256 不一致"});
      var e1 = {status: updateModel.status, what: updateModel.error ? updateModel.error.what : null};
      newJob("j2", "update");
      applyUpdateEvent({type:"done", job_kind:"update", cancelled: true});
      emit({err: e1, cancelled: updateModel.status});
    """)
    assert r["err"] == {"status": "error", "what": "校验失败"}
    assert r["cancelled"] == "cancelled"


def test_update_check_states_via_model():
    r = _run_js(r"""
      updateModel.status = "idle";
      applyCheckResult({newer: false, latest: null});
      var a = updateModel.status;
      applyCheckResult({newer: true, latest: "9.9.9", asset_name: "a.exe", size: 1024});
      var b = [updateModel.status, updateModel.latest];
      applyCheckResult({error: {what: "x", why: "y"}});
      emit({a: a, b: b, c: updateModel.status});
    """)
    assert r["a"] == "latest" and r["b"] == ["available", "9.9.9"] and r["c"] == "error"


def test_newjob_resets_update_model():
    """代际纪律:新任务(非 update)清空 updateModel,旧回调不污染新代际。"""
    r = _run_js(r"""
      newJob("j1", "update");
      applyUpdateEvent({type:"done", job_kind:"update", version:"9.9.9",
                        asset_name:"a.exe", path:"p", size:1, sha256:"ab"});
      newJob("j2", "migrate");
      emit({status: updateModel.status, plan: updateModel.plan});
    """)
    assert r["status"] == "idle" and r["plan"] is None


def test_apply_status_rebuilds_update_model():
    """评审② I4:GET 状态(reset/overflow 重同步与刷新恢复共用)必须重建更新面板
    ——运行中回填字节进度,终态按 done 载荷收口两态。"""
    r = _run_js(r"""
      newJob("j1", "update");
      applyStatus({status: "running", revision: 2,
                   progress_bytes: {received: 10, total: 100}, done: null});
      var mid = [updateModel.status, updateModel.received];
      applyStatus({status: "succeeded", revision: 4,
                   progress_bytes: {received: 100, total: 100},
                   done: {type: "done", job_kind: "update", version: "9.9.9",
                          asset_name: "a.exe", path: "p", size: 100, sha256: "ab"}});
      emit({mid: mid, end: [updateModel.status, updateModel.plan ? updateModel.plan.version : null]});
    """)
    assert r["mid"] == ["downloading", 10]
    assert r["end"] == ["downloaded", "9.9.9"]


def test_restore_saved_update_job_rebuilds_progress():
    """刷新恢复:restoreSavedJob 经 GET 快照重建字节进度与终态(评审② I4 测试缺口)。"""
    r = _run_js(r"""
      globalThis.fetch = function () {
        return Promise.resolve({ ok: true, json: function () {
          return Promise.resolve({ kind: "update", status: "running", revision: 5,
                                   progress_bytes: {received: 2097152, total: 31457280},
                                   done: null });
        } });
      };
      restoreSavedJob({job_id: "j1", kind: "update", seq: 3}).then(function () {
        emit({status: updateModel.status, recv: updateModel.received, seq: jobModel.lastSeq});
      });
    """)
    assert r["status"] == "downloading" and r["recv"] == 2097152


def test_replan_failed_no_cancel_hint_and_src_dst_captured():
    """T13.5-2/4:重规划段显示不可取消提示;replan_failed 终态不可见;
    事件携带的 src/dst 入模型(终态 preflight 面板复燃输入)。"""
    r = _run_js(r"""
      newJob("j1", "swap");
      applyEvent({type:"phase", name:"replan", seq:1});
      var during = cancelHintVisible(jobModel);
      applyEvent({type:"error", code:"swap_replan_failed", job_kind:"swap",
                  src:"a", dst:"b", seq:2});
      emit({during: during, after: cancelHintVisible(jobModel),
            src: jobModel.src, dst: jobModel.dst});
    """)
    assert r["during"] is True and r["after"] is False
    assert (r["src"], r["dst"]) == ("a", "b")


def test_cancel_button_text_by_kind():
    """T13.5-3:取消按钮文案按 kind 分化(恢复运行中 swap 不得显示「取消迁移」)。"""
    r = _run_js(r"""
      emit({s: cancelButtonText({kind:"swap"}), m: cancelButtonText({kind:"migrate"})});
    """)
    assert r["s"] == "取消装包" and r["m"] == "取消迁移"


def test_resync_exhausted_banner_honest_wording():
    """T13.5-8:退避耗尽横幅=诚实版(含「请刷新页面或检查服务」)。"""
    r = _run_js(r"""
      emit({giveup: resyncFailureText()});
    """)
    assert "刷新页面" in r["giveup"]


def test_swap_apply_decision_state_renders():
    """T13.5-7:swap 行为 node 测试入库——done 载荷(链式计划+装包统计)入模型。"""
    r = _run_js(r"""
      newJob("j1", "swap");
      applyEvent({type:"done", job_kind:"swap", src:"a", dst:"b",
                  plan:{files:[]}, install:{copied:3, skipped:0, conflicts:[]},
                  backup_dir:null, seq:1});
      emit({kind: jobModel.kind, hasPlan: !!jobModel.plan,
            copied: jobModel.swap && jobModel.swap.install ? jobModel.swap.install.copied : null});
    """)
    assert r == {"kind": "swap", "hasPlan": True, "copied": 3}


# ---- 评审④(试发前):请求归属/回执接管/恢复态/形态指引 ----

def test_update_check_does_not_invalidate_download_receipt():
    """评审④ P1:下载 POST 在飞时点「检查更新」——检查不得废止下载回执
    (202 已在服务端生效;回执被弃=页面失去任务控制权:无 jobId/无刷新游标/
    无 SSE/无法取消,而单锁仍被后台占用)。修复后:下载期间检查被禁用且检查
    走独立代际;即便回执迟到于任何后续请求,adoptJobReceipt 仍完成接管。"""
    r = _run_js(r"""
      var pending = [];
      postJson = function (url) {
        return new Promise(function (res) { pending.push({ url: url, res: res }); });
      };
      startUpdateDownload();          // 下载 POST 在飞(回执未到,状态=downloading)
      checkUpdate();                  // 冲突操作:修复后禁用/独立代际,不动共享代际
      pending[0].res({ job_id: "u1" });   // 下载回执到达
      setTimeout(function () {
        var cursor = null;
        try { cursor = JSON.parse(sessionStorage.getItem("mcmig.job") || "null"); } catch (e) {}
        emit({ jobId: jobModel.jobId, kind: jobModel.kind,
               cursor: cursor && cursor.job_id, subscribed: currentEs !== null,
               status: updateModel.status, extraChecks: pending.length - 1 });
      }, 20);
    """)
    assert r["jobId"] == "u1" and r["kind"] == "update"
    assert r["cursor"] == "u1", "回执被弃则刷新游标缺失(修复前即此形态)"
    assert r["subscribed"] is True
    assert r["status"] == "downloading"
    assert r["extraChecks"] == 0, "下载期间检查被禁用,不应再发起检查请求"


def test_adopt_job_receipt_survives_newer_request():
    """评审④ P1 + 评审⑤ P1:任务创建回执必须完成接管——更晚请求已开始(旧
    requestAlive 必失效)也不得丢弃 202 回执;接管即 newJob+订阅(游标/SSE
    一并落位),创建请求代际随回执记录(排序凭据)。"""
    r = _run_js(r"""
      var gen = beginRequest();
      beginRequest();                         // 更晚请求:gen 已失效
      var adopted = adoptJobReceipt({ job_id: "u2" }, "update", handlersForKind("update"), gen);
      emit({ adopted: adopted, alive: requestAlive(gen),
             jobId: jobModel.jobId, kind: jobModel.kind, subscribed: currentEs !== null,
             createGen: jobModel.createGen });
    """)
    assert r == {"adopted": True, "alive": False, "jobId": "u2",
                 "kind": "update", "subscribed": True, "createGen": 1}


def test_adopt_job_receipt_rejects_stale_receipt():
    """评审⑤ P2①(前提修正):拒收的唯一形态=**迟到回执**——页面已持创建更晚
    的任务(createGen 更大),旧回执不得重新展示(旧结果覆盖新计划);原「页面
    持另一运行中任务即拒收」的前提已被排序规则取代(更晚回执必须接管,见下
    两用例——客户端 terminal 不是服务端单锁的事实)。"""
    r = _run_js(r"""
      newJob("m1", "migrate", 5);             // 页面当前任务:创建代际 5
      var adopted = adoptJobReceipt({ job_id: "u3" }, "update", handlersForKind("update"), 4);
      emit({ adopted: adopted, jobId: jobModel.jobId, kind: jobModel.kind,
             createGen: jobModel.createGen });
    """)
    assert r == {"adopted": False, "jobId": "m1", "kind": "migrate", "createGen": 5}


def test_back_reselect_then_new_receipt_adopts():
    """评审⑤ P1:计划运行中点「返回重选版本」(closeEvents,模型保持非终态)——
    旧任务后台收尾后重新生成,服务端单锁已释放、合法返回新 202;新回执的创建
    代际更晚,必须接管(修复前被「页面已持非终态任务」拒收:页面停准备中、
    新任务无订阅无游标,刷新前不可恢复)。"""
    r = _run_js(r"""
      var gen1 = beginRequest();
      newJob("old-plan", "plan", gen1);       // 旧计划运行中
      closeEvents();                          // btn-back1 真实动作:关订阅,模型不动
      var gen2 = beginRequest();              // 重新生成:更晚的创建请求
      var adopted = adoptJobReceipt({ job_id: "new-plan" }, "plan", planHandlers(), gen2);
      var cursor = null;
      try { cursor = JSON.parse(sessionStorage.getItem("mcmig.job") || "null"); } catch (e) {}
      emit({ adopted: adopted, jobId: jobModel.jobId, terminal: jobModel.terminal,
             subscribed: currentEs !== null, cursor: cursor && cursor.job_id });
    """)
    assert r == {"adopted": True, "jobId": "new-plan", "terminal": False,
                 "subscribed": True, "cursor": "new-plan"}


def test_late_old_receipt_does_not_replace_newer_plan():
    """评审⑤ P2①:旧成功回执迟到于「新计划完成」之后——不得夺回当前模型
    (修复前:newJob 覆盖新计划,planId 清空、状态退回 running、刷新游标被改写;
    排序规则下 gen <= createGen 的迟到回执静默拒收)。"""
    r = _run_js(r"""
      var gen1 = beginRequest();              // 旧计划请求(回执延迟)
      var gen2 = beginRequest();              // 新计划请求
      var adoptedNew = adoptJobReceipt({ job_id: "new-plan" }, "plan", planHandlers(), gen2);
      applyEvent({ type: "done", job_kind: "plan", plan: { groups: [] },
                   plan_id: "p-new", persisted: true, seq: 1 });   // 新计划完成
      var adoptedOld = adoptJobReceipt({ job_id: "old-plan" }, "plan", planHandlers(), gen1);
      var cursor = null;
      try { cursor = JSON.parse(sessionStorage.getItem("mcmig.job") || "null"); } catch (e) {}
      emit({ adoptedNew: adoptedNew, adoptedOld: adoptedOld,
             jobId: jobModel.jobId, planId: jobModel.planId, status: jobModel.status,
             cursor: cursor && cursor.job_id });
    """)
    assert r == {"adoptedNew": True, "adoptedOld": False, "jobId": "new-plan",
                 "planId": "p-new", "status": "succeeded", "cursor": "new-plan"}


def test_update_check_disabled_matrix():
    """评审④ P1:检查按钮禁用矩阵——检查中/下载中/已下载(有产物)禁用;
    空闲/已取消/错误态可再检。"""
    r = _run_js(r"""
      emit({
        idle: updateCheckDisabled({status: "idle", plan: null}),
        checking: updateCheckDisabled({status: "checking", plan: null}),
        downloading: updateCheckDisabled({status: "downloading", plan: null}),
        downloaded: updateCheckDisabled({status: "downloaded", plan: {path: "p"}}),
        cancelled: updateCheckDisabled({status: "cancelled", plan: null}),
        error: updateCheckDisabled({status: "error", plan: null})
      });
    """)
    assert r == {"idle": False, "checking": True, "downloading": True,
                 "downloaded": True, "cancelled": False, "error": False}


def test_update_panel_job_owned_matrix():
    """评审⑤ P2②:检查结果应用门——下载任务已接管面板(运行中/已下载有产物)
    时在飞检查结果(含错误)不得改写归属;空闲/检查中/已取消不拦(downloaded
    无产物非本状态机形态,防御性不拦)。"""
    r = _run_js(r"""
      emit({
        idle: updatePanelJobOwned({status: "idle", plan: null}),
        checking: updatePanelJobOwned({status: "checking", plan: null}),
        downloading: updatePanelJobOwned({status: "downloading", plan: null}),
        downloaded: updatePanelJobOwned({status: "downloaded", plan: {path: "p"}}),
        downloadedNoPlan: updatePanelJobOwned({status: "downloaded", plan: null}),
        cancelled: updatePanelJobOwned({status: "cancelled", plan: null}),
        error: updatePanelJobOwned({status: "error", plan: null})
      });
    """)
    assert r == {"idle": False, "checking": False, "downloading": True,
                 "downloaded": True, "downloadedNoPlan": False,
                 "cancelled": False, "error": False}


def test_restore_update_running_without_bytes_shows_preparing():
    """评审④ P2-5:刷新恢复落在「尚未出字节进度」窗口(检查 Release/取 SUMS/
    首块未达,progress_bytes=null)——运行态由任务 status 决定,面板须显示
    下载中(准备下载文案),取消入口随 downloading 可见。"""
    r = _run_js(r"""
      globalThis.fetch = function () {
        return Promise.resolve({ ok: true, json: function () {
          return Promise.resolve({ kind: "update", status: "running", revision: 2,
                                   progress_bytes: null, done: null });
        } });
      };
      restoreSavedJob({job_id: "j2", kind: "update", seq: 0}).then(function () {
        emit({status: updateModel.status, terminal: jobModel.terminal,
              preparing: updateProgressText(updateModel) === PAGE_STRINGS.UPDATE_PREPARING});
      });
    """)
    assert r == {"status": "downloading", "terminal": False, "preparing": True}


def test_restore_update_late_check_does_not_override_downloading():
    """评审⑤ P2②:刷新恢复 GET 未返回时用户点「检查更新」(在飞),恢复先落地
    downloading,检查结果后到——不得把面板盖回 available(取消入口消失而任务
    仍在跑)。接管/恢复更新任务即作废在飞检查,回调再验面板归属双保险。"""
    r = _run_js(r"""
      globalThis.fetch = function () {
        return Promise.resolve({ ok: true, json: function () {
          return Promise.resolve({ kind: "update", status: "running", revision: 2,
                                   progress_bytes: null, done: null });
        } });
      };
      var restoreP = restoreSavedJob({ job_id: "u1", kind: "update", seq: 0 });
      var resolveCheck = null;
      postJson = function () { return new Promise(function (res) { resolveCheck = res; }); };
      checkUpdate();                        // 恢复 GET 未返回:检查在飞
      restoreP.then(function () {
        var mid = updateModel.status;
        resolveCheck({ newer: true, current: "0.12.0", latest: "0.13.1",
                       asset_name: "a.exe", size: 1, form: "onefile" });
        setTimeout(function () {
          emit({ mid: mid, after: updateModel.status,
                 cancelVisible: updateModel.status === "downloading",
                 jobRunning: jobModel.jobId === "u1" && !jobModel.terminal });
        }, 20);
      });
    """)
    assert r == {"mid": "downloading", "after": "downloading",
                 "cancelVisible": True, "jobRunning": True}


def test_update_progress_text_with_bytes():
    """评审④ P2-5 反向守卫:有字节进度时仍是「下载中 x / y MB」口径。"""
    r = _run_js(r"""
      emit({text: updateProgressText({received: 2097152, total: 31457280})});
    """)
    assert "2 / 30" in r["text"]


def test_update_steps_text_by_form():
    """评审④ P2-2:替换指引按安装形态分派——onedir=解压覆盖(保留
    data/config.toml),onefile=替换现有 mcmig-gui*.exe;未知形态回落通用三步。"""
    r = _run_js(r"""
      emit({onefile: updateStepsText({form: "onefile"}),
            onedir: updateStepsText({form: "onedir"}),
            unknown: updateStepsText({form: null})});
    """)
    assert "mcmig-gui" in r["onefile"] and ".exe" in r["onefile"]
    assert "解压" in r["onedir"] and "config.toml" in r["onedir"]
    assert "替换" in r["unknown"] and "重新启动" in r["unknown"]


def test_apply_check_result_carries_form():
    """评审④ P2-2:检查结果携带 form → 更新面板按形态选指引。"""
    r = _run_js(r"""
      applyCheckResult({current: "0.13.0", mode: "frozen", newer: true, latest: "0.13.1",
                        asset_name: "mcmig-0.13.1-win-x64.zip", size: 1, error: null,
                        form: "onedir"});
      emit({status: updateModel.status, form: updateModel.form});
    """)
    assert r == {"status": "available", "form": "onedir"}


def test_restore_update_done_keeps_form_for_steps():
    """评审④ P2-2:刷新恢复路经 done 载荷同源携带 form(终态两态展示的
    替换指引仍按形态给出)。"""
    r = _run_js(r"""
      globalThis.fetch = function () {
        return Promise.resolve({ ok: true, json: function () {
          return Promise.resolve({ kind: "update", status: "succeeded", revision: 4,
            progress_bytes: {received: 10, total: 10},
            done: {type: "done", job_kind: "update", version: "9.9.9",
                   asset_name: "mcmig-0.13.1-win-x64.zip", path: "p", size: 10,
                   sha256: "ab", form: "onedir"} });
        } });
      };
      restoreSavedJob({job_id: "j3", kind: "update", seq: 4}).then(function () {
        emit({status: updateModel.status, form: updateModel.form,
              unzip: updateStepsText(updateModel).indexOf("解压") >= 0});
      });
    """)
    assert r == {"status": "downloaded", "form": "onedir", "unzip": True}
