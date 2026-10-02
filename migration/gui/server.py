"""FastAPI 薄翻译层:本地 Web GUI 服务(job 模型 + SSE + 单任务锁 + Host 校验)。

职责边界(spec §2/§4):
- 「薄翻译」:各 API(versions/config/plan/migrate/SSE)只做参数校验与 job 编排,
  管线一律调 ``migration.pipeline``,绝不 import cli 的私有函数;核心模块不感知 HTTP。
- 安全:绑定 127.0.0.1 由 uvicorn 启动方负责,本层加 Host 头校验中间件
  (防 DNS rebinding),仅放行本机回环主机名;无任何外部请求。
- 单任务锁:同一时刻至多一个 job(防双标签页并发写盘),第二个请求 409。
- 错误三段式:API 错误统一 ``{what, why, details}``(what=失败对象,why=原因与建议)。

事件流 schema(spec §4,两级进度,MAA 模型裁剪;批次I-T1 增补 notice 型):
- 每个事件统一携带 ``"seq"``(job 内单调 +1 的序号;批次I-T4),SSE 帧附
  ``id: <seq>`` 行——客户端断线重连时以 Last-Event-ID 续订,漏接封堵靠历史重放
- ``{"type":"phase","name":"scan_src|scan_dst|plan|migrate"}`` — 阶段切换
- ``{"type":"notice","text":..}`` — 过程提示(如旧布局快照迁移引导;页面忽略未知型)
- ``{"type":"warning","code":..,"message":..}`` — 执行预检降级警告(批次I-T2;
   决策放行后的提示;页面忽略未知型,不阻断流程)
- ``{"type":"file","path":..,"status":..,"index":..,"total":..,"backed_up":..,
   "failed":..,"error":..}`` — 文件级进度(failed/error 为失败文件补充字段)
- ``{"type":"reset"}`` — 历史截断标志(批次I-T4,衔接协议):续订早于保留的
   历史尾时首发,客户端须重读 GET /api/jobs/{id} 重建状态;非真实事件,无 seq
- ``{"type":"done",...}`` — plan job 带 ``plan``(按 origin 分组的 action 摘要)与
   ``plan_id``/``persisted``(计划身份指纹与持久化结果,批次I-T3;保存失败时
   persisted:false 且同事件携带三段式错误字段,页面禁执行);migrate job 带 ``summary``
   (各 status 计数)与 ``reminder``(PCL 两处配置提醒)
- ``{"type":"error","what":..,"why":..,"details":{..}}`` — job 内失败,任务终止

job 状态机(批次I-T4/T6,GET /api/jobs/{id} 的 ``status`` 字段):
running → succeeded | partial_failed | failed | cancelled;migrate 运行中可经
POST /api/jobs/{id}/cancel 进入中间态 cancelling(202),执行侧取消检查点
命中后以 cancelled 收尾(首个动作恒执行,安全边界内无半途文件)——
succeeded=done 且无失败文件;partial_failed=migrate 执行完但 results 含
失败文件;failed=发出过 error 事件;cancelled=用户取消的部分完成。
plan job 不可取消(cancel → 405),终态 job 取消 → 409。

恢复入口(批次I-T6,spec §4.3):migrate job 持 write-ahead journal
(<game_root>/.mcmig/jobs/<job_id>.json,意图→操作→完成三段序);启动期/
请求期经 GET /api/jobs/interrupted 列出未收尾且有待核对条目的中断 job。
退出入口 POST /api/shutdown:空闲置停机标志(uvicorn 接线归 T10),
job 运行 → 409。

本模块无任何 print:过程信息走 logging,结果走返回值/事件。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..executor import FileResult
from ..fsops import FsOpsError
from ..instlock import InstanceLockError, instance_locks
from ..journal import JobJournal, JournalError, scan_interrupted
from ..plan import (
    ORIGIN_REGISTRY,
    ActionRecord,
    Behavior,
    MigrationPlan,
    Origin,
    PlanFormatError,
    PlanPersistError,
)
from ..pipeline import (
    _rules_dir,  # 同包私有助手:规则目录「新位置优先+旧布局只读回退」单点(终审修复 I4;cli 亦有同款消费)
    build_plan,
    execute_migration,
    find_snapshot,
    list_versions,
    read_active_version,
    scan_version,
)
from ..preflight import PreflightBlocker, preflight_execute
from ..review import check_action_states, plan_fingerprint, validate_review
from ..snapshot import Snapshot, SnapshotFormatError
from ..workdir import WorkDir, WorkdirError, resolve_workdir
from .STRINGS import STRINGS

log = logging.getLogger(__name__)

# Host 校验白名单:仅本机回环主机名(端口不校验,启动侧绑定 127.0.0.1);
# 测试侧用 TestClient(app, base_url="http://127.0.0.1") 直连,无需白名单额外项。
_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

# job 线程启动后的最小存活时长:保证单任务锁语义对「连续两次请求」确定
# (第二个请求到来时第一个 job 必然未完成,从而稳定 409;见 task-6 brief 实现者注意)。
# 测试通过 monkeypatch 本常量(如 0.5s)放大窗口换取完全确定性。
_JOB_MIN_ALIVE_SECONDS = 0.005

# 事件历史保留上限(重放源,批次I-T4):超过即从头端截断,只保留最近 N 条;
# 测试 monkeypatch 锚点(如截断用例置 2)。
_EVENT_HISTORY_LIMIT = 500

# 单订阅者广播队列上限(批次I-T4):慢订阅者队列满即被移除(其 SSE 流自然
# 断流,客户端凭 Last-Event-ID 重连或重读状态端点),绝不反压 job 线程;
# 测试 monkeypatch 锚点。
_SUBSCRIBER_QUEUE_LIMIT = 256

# index.html 缺失时的占位片段(Task 7 创建真页面前 GET / 的兜底)
_INDEX_FALLBACK = "<h1>mcmig</h1>"


# ---------------------------------------------------------------------------
# job 模型:状态机 + 单调事件序号 + 历史重放 + 每订阅者广播队列 + 单任务锁仓库
# ---------------------------------------------------------------------------


class Job:
    """单个后台任务:状态机 + 事件广播底座(SSE 衔接协议,批次I-T4)。

    - 每个事件带 ``"seq"``(= revision,单调 +1);历史保留最近
      ``_EVENT_HISTORY_LIMIT`` 条,``_floor_seq`` 记已截断的最大序号。
    - 每个订阅者独享一条有界队列(``_SUBSCRIBER_QUEUE_LIMIT``):满则丢弃
      该订阅者,emit 永不阻塞(复制线程不被慢客户端反压)。
    - 状态机词表:running/cancelling(取消中间态,批次I-T6 由 cancel 端点
      写入)/succeeded/partial_failed/failed/cancelled(终态,由 _finish 收口)。
    """

    def __init__(self, job_id: str, kind: str) -> None:
        """初始化 job。

        Args:
            job_id: 任务唯一标识(uuid4 hex,由 JobStore 分配)。
            kind: 任务类型("plan" / "migrate")。
        """
        self.id = job_id
        self.kind = kind
        # 状态机对外契约(cancel 端点写 cancelling;终态由 _finish 收口)
        self.status = "running"
        self.revision = 0
        self.can_cancel = kind == "migrate"  # kind 决定可否取消(plan → 405)
        self.done = False
        # 终态取消标志已单次判定(终审修复 I2):settle() 置位后 cancel 端点一律 409,
        # 「done 事件字段/executed_at 回写/journal finish/GET status」由同一判定导出
        self._settled = False
        # migrate 执行结果(job 线程一次性赋值,状态端点序列化输出;plan job 恒 None)
        self.results: list[FileResult] | None = None
        # 事件派生状态(emit 内同步维护;状态端点据此派生 phase/progress/summary/error)
        self._last_phase: str | None = None
        self._last_file: dict | None = None
        self._summary: dict | None = None
        self._error: dict | None = None
        # 广播底座(锁保护历史/订阅队列的一致性)
        self._lock = threading.Lock()
        self._history: list[dict] = []
        self._floor_seq = 0  # 已截断的最大 seq(续订早于此 → 不可完整重放 → reset)
        self._subs: list[queue.Queue[dict]] = []

    def emit(self, event: dict) -> None:
        """派发一个事件:分配 seq → 记入历史(截断)→ 广播到全部订阅者。

        慢订阅者队列满时就地移除该订阅者(不反压本线程);事件派生状态
        (最后 phase/file、summary、error)同步维护供状态端点查询。
        """
        with self._lock:
            self.revision += 1
            ev = {**event, "seq": self.revision}
            self._history.append(ev)
            self._truncate_history_locked()
            etype = ev.get("type")
            if etype == "phase":
                self._last_phase = ev.get("name")
            elif etype == "file":
                self._last_file = ev
            elif etype == "error":
                self._error = ev
            elif etype == "done" and "summary" in ev:
                self._summary = ev["summary"]
            for q in list(self._subs):
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    # 慢订阅者:队列已满 → 丢弃该订阅者(流自然断开),绝不阻塞
                    self._subs.remove(q)

    def _truncate_history_locked(self) -> None:
        """把历史截断到 ``_EVENT_HISTORY_LIMIT`` 条并前移 floor(调用方须持锁)。"""
        limit = _EVENT_HISTORY_LIMIT
        overflow = len(self._history) - limit
        if overflow <= 0:
            return
        if limit <= 0:  # 病态配置(≤0):全量丢弃,只留 floor 标记
            self._floor_seq = self._history[-1]["seq"]
            self._history.clear()
            return
        del self._history[:overflow]
        self._floor_seq = self._history[0]["seq"] - 1

    def subscribe(self, last_seq: int = 0) -> tuple[bool, list[dict], queue.Queue[dict]]:
        """注册一个订阅者,并原子快照 ``seq > last_seq`` 的历史重放段。

        历史快照与队列注册在同一临界区完成,重放段与实时段无缝衔接
        (不重不漏);历史已截断、无法完整重放时置 reset 标记。

        Args:
            last_seq: 客户端已收到的最大 seq(Last-Event-ID);0 表示从头订阅。

        Returns:
            (是否需要 reset, 历史重放事件列表, 本订阅者的实时队列)。
        """
        with self._lock:
            reset = last_seq < self._floor_seq
            replay = [e for e in self._history if e["seq"] > last_seq]
            q: queue.Queue[dict] = queue.Queue(maxsize=_SUBSCRIBER_QUEUE_LIMIT)
            self._subs.append(q)
            return reset, replay, q

    def unsubscribe(self, q: queue.Queue[dict]) -> None:
        """注销订阅者(SSE 流收尾/中断时调用;已被满丢弃的队列静默忽略)。"""
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def settle(self) -> bool:
        """终态取消标志的锁内单次判定(终审修复 I2)。

        migrate job 体在构造 done 事件**之前**调用:锁内读取 cancelling 中间态并
        置 ``_settled``——此后 cancel 端点必 409(状态不再可能被翻转为 cancelling),
        done 事件的 cancelled 字段、executed_at 是否回写、journal 是否 finish 与
        GET status 的终态全部由这一次判定导出,四者永不自相矛盾。取消落在
        settle 之前的窗口照常生效(返回 True);落在之后被 409 拒绝(任务即将收尾)。

        Returns:
            True 表示用户取消(cancelling 中间态命中),job 体按取消终态收尾。
        """
        with self._lock:
            self._settled = True
            return self.status == "cancelling"

    def snapshot(self) -> dict[str, object]:
        """派生 GET /api/jobs/{id} 的状态快照(持锁读,与 emit 视图一致)。

        Returns:
            ``{status, kind, phase, revision, progress:{index,total}|None,
            summary|None, results|None, error:{what,why,details}|None}``。
        """
        with self._lock:
            progress: dict[str, int] | None = None
            if self._last_file is not None:
                progress = {
                    "index": self._last_file["index"],
                    "total": self._last_file["total"],
                }
            error: dict[str, object] | None = None
            if self._error is not None:
                error = {k: self._error[k] for k in ("what", "why", "details")}
            results: list[dict[str, object]] | None = None
            if self.results is not None:
                results = [
                    {
                        "path": r.path,
                        "status": r.status,
                        "backed_up": r.backed_up,
                        "failed": r.failed,
                        "error": r.error,
                    }
                    for r in self.results
                ]
            return {
                "status": self.status,
                "kind": self.kind,
                "phase": self._last_phase,
                "revision": self.revision,
                "progress": progress,
                "summary": self._summary,
                "results": results,
                "error": error,
            }


def _finish(job: Job, results: list[FileResult] | None = None) -> None:
    """终态判定收口(持 job 锁置位,保证与订阅/快照视图一致)。

    判定规则:取消中间态(cancelling)→ cancelled(批次I-T6:用户取消的
    部分完成,优先于其余判定);发过 error 事件 → failed;migrate 的 results
    含失败文件 → partial_failed;否则 succeeded。plan job 持久化失败
    (persisted:false 的 done 事件)不属 error 事件,状态仍为 succeeded
    (页面据事件内三段式字段禁执行)。

    终审修复 I2:migrate 的取消标志已由 job 体在 done 事件前经 ``settle()``
    锁内单次判定并封死翻转窗口,此处对 cancelling 的重判因此与 done 事件
    字段恒一致(plan job 不可取消,无此竞态)。
    """
    with job._lock:
        if job.status == "cancelling":
            job.status = "cancelled"
        elif job._error is not None:
            job.status = "failed"
        elif results is not None and any(r.failed for r in results):
            job.status = "partial_failed"
        else:
            job.status = "succeeded"
        # done 标志必须在最后一个事件入队之后置位(SSE 以「done 且队列空」收尾)
        job.done = True


class JobStore:
    """单任务锁的 job 仓库:同一时刻至多一个 job 在跑,第二个 start 返回 None。

    每个 app 实例独享一个 store(create_app 内创建),避免跨实例任务串扰。
    """

    def __init__(self) -> None:
        """初始化空仓库(内部锁保护 current 槽位)。"""
        self._lock = threading.Lock()
        self._current: Job | None = None
        self._jobs: dict[str, Job] = {}

    def start(self, kind: str, runner: Callable[[Job], None]) -> Job | None:
        """注册并启动后台 job 线程;已有未完成 job 时返回 None(单任务锁)。

        Args:
            kind: 任务类型("plan" / "migrate")。
            runner: job 线程目标函数(接收 Job;负责推送事件并在收尾置 done)。

        Returns:
            新建的 Job;已有任务在跑则返回 None(调用方应答 409)。
        """
        with self._lock:
            if self._current is not None and not self._current.done:
                return None
            job = Job(uuid.uuid4().hex, kind)
            self._current = job
            self._jobs[job.id] = job
        threading.Thread(target=runner, args=(job,), daemon=True, name=f"mcmig-{kind}").start()
        return job

    def get(self, job_id: str) -> Job | None:
        """按 id 查找 job(含已完成的,供 SSE 追溯消费)。"""
        return self._jobs.get(job_id)

    def is_busy(self) -> bool:
        """是否有未收尾的 job 在跑(退出端点守卫:空闲才允许停机,批次I-T6)。"""
        with self._lock:
            return self._current is not None and not self._current.done


# ---------------------------------------------------------------------------
# 错误三段式:{what, why, details} + HTTP 状态码
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """API 层可预期错误:统一三段式 what/why/details + HTTP 状态码。

    Attributes:
        status_code: HTTP 状态码(403/404/409/422 等)。
        what: 失败对象(中文短描述,GUI 弹窗标题级,取自 STRINGS)。
        why: 中文失败原因与用户可执行的建议(取自 STRINGS)。
        details: 结构化补充(可用版本列表/校验错误明细等)。
    """

    def __init__(
        self, status_code: int, what_key: str, why_key: str, details: dict | None = None
    ) -> None:
        """初始化 API 错误(文案键查 STRINGS 字典)。

        Args:
            status_code: HTTP 状态码。
            what_key: STRINGS 中的 what 键(如 "err_version_missing.what")。
            why_key: STRINGS 中的 why 键。
            details: 结构化补充,缺省为空字典。
        """
        super().__init__(f"{STRINGS[what_key]}:{STRINGS[why_key]}")
        self.status_code = status_code
        self.what = STRINGS[what_key]
        self.why = STRINGS[why_key]
        self.details = details or {}


def _three_part_response(status_code: int, what: str, why: str, details: dict) -> JSONResponse:
    """构造三段式错误响应体 ``{what, why, details}``(spec §4 错误三段式)。"""
    return JSONResponse(
        status_code=status_code, content={"what": what, "why": why, "details": details}
    )


def _error_event(what: str, why: str, details: dict) -> dict:
    """构造三段式 SSE error 事件(job 内失败,任务终止)。"""
    return {"type": "error", "what": what, "why": why, "details": details}


# ---------------------------------------------------------------------------
# 版本校验(M3 收口:枚举与 PCL.ini 活跃版本读取已提取为 pipeline 公共函数,
# 与 CLI 共用同一实现——分层纪律:消费 pipeline,不 import cli 私有函数)
# ---------------------------------------------------------------------------


def _ensure_version_dirs(game_root: Path, *versions: str) -> None:
    """校验版本文件夹存在,不存在抛 422 三段式(details 附可用版本列表)。"""
    vdir = game_root / "versions"
    missing = [v for v in versions if not (vdir / v).is_dir()]
    if missing:
        raise ApiError(
            422,
            "err_version_missing.what",
            "err_version_missing.why",
            {"missing": missing, "available": list_versions(game_root)},
        )


@dataclass(frozen=True)
class InstanceCtx:
    """job 的实例态上下文(批次I-T1:job 启动时一次性定格,闭包持有)。

    Attributes:
        game_root: 游戏根目录。
        snapshots: 实例态快照目录(<game_root>/.mcmig/snapshots)。
        plans: 实例态迁移计划目录(<game_root>/.mcmig/plans)。
        rules: 实例态用户规则文件(<game_root>/.mcmig/rules.yaml)。
        jobs: 实例态 job journal 目录(<game_root>/.mcmig/jobs,批次I-T6)。
        legacy_snapshots: 旧布局快照目录(只读回退);无旧布局时为 None。
    """

    game_root: Path
    snapshots: Path
    plans: Path
    rules: Path
    jobs: Path
    legacy_snapshots: Path | None


def _instance_ctx(wdir: WorkDir) -> InstanceCtx:
    """从 workdir 提取 job 的实例态上下文。

    Args:
        wdir: 当前工作目录布局(通常取 app.state.wdir)。

    Returns:
        定格的实例上下文(job 启动时调用一次,运行中不再随配置变更)。

    Raises:
        ApiError: 422(未配置 game_root 的首跑欢迎态,err_no_game_root)。
    """
    if wdir.game_root is None or wdir.snapshots is None:
        raise ApiError(422, "err_no_game_root.what", "err_no_game_root.why")
    return InstanceCtx(
        game_root=wdir.game_root,
        snapshots=wdir.snapshots,
        plans=wdir.plans or wdir.snapshots.parent / "plans",
        rules=wdir.rules or wdir.snapshots.parent / "rules.yaml",
        jobs=wdir.jobs or wdir.snapshots.parent / "jobs",
        legacy_snapshots=wdir.legacy_snapshots,
    )


# ---------------------------------------------------------------------------
# job 线程体:消费 pipeline,推送事件
# ---------------------------------------------------------------------------


def _group_actions(plan: MigrationPlan) -> dict[str, dict]:
    """把 plan.actions 按 origin 分组为可渲染摘要(done 事件 ``plan`` 字段)。

    组顺序遵循 Origin 枚举声明序(稳定,页面渲染顺序有依据);组元数据
    (title/footnote)取自 ORIGIN_REGISTRY,页面无需硬编码 origin 词表。

    Args:
        plan: 迁移计划。

    Returns:
        ``{origin: {"title": 组标题, "behavior": 组操作, "count": 计数,
        "footnote": 注脚或 None, "actions": [{path,reason,behavior,origin}]}}``,
        仅含非空分组。
    """
    grouped: dict[str, dict] = {}
    for origin in Origin:
        actions = [a for a in plan.actions if a.origin == origin]
        if not actions:
            continue
        spec = ORIGIN_REGISTRY.get(origin.value)
        grouped[origin.value] = {
            "title": spec.title if spec else origin.value,
            "behavior": actions[0].behavior.value,
            "count": len(actions),
            "footnote": spec.footnote if spec else None,
            "actions": [
                {
                    "path": a.path,
                    "reason": a.reason,
                    "behavior": a.behavior.value,
                    "origin": a.origin.value,
                }
                for a in actions
            ],
        }
    return grouped


def _build_reminder(game_root: Path, dst: str) -> list[str]:
    """构建 PCL 两处配置的中文提醒文案(与 CLI `_cmd_migrate` 逐行一致)。"""
    return [
        STRINGS["reminder.line1"],
        STRINGS["reminder.line_pcl_ini"].format(pcl_ini=game_root / "PCL.ini", dst=dst),
        STRINGS["reminder.line_setup_ini"].format(
            setup_ini=game_root / "PCL" / "Setup.ini", dst=dst
        ),
        STRINGS["reminder.line_tail"],
    ]


def _notify_abandoned_lock(job: Job, ctx: InstanceCtx, lockinfo: dict[str, list[str]]) -> None:
    """abandoned 实例锁 → notice 事件:前持有者异常退出,须核对 journal 中断记录。

    jobs 目录即 journal 目录(批次I-T6 落地,InstanceCtx.jobs);
    文案与 CLI `_warn_abandoned_lock` 同源(STRINGS 单点维护)。
    """
    if lockinfo["abandoned"]:
        job.emit(
            {
                "type": "notice",
                "text": STRINGS["notice.instlock_abandoned"].format(jobs_dir=ctx.jobs),
            }
        )


def _run_plan_job(job: Job, ctx: InstanceCtx, src: str, dst: str) -> None:
    """plan job 线程体:scan src → scan dst → build_plan,推送 phase/notice/done/error 事件。

    实例态路径全部来自 job 启动时定格的 ctx(闭包持有,批次I-T1:运行中改
    game_root 只影响后续 job);快照写 ctx.snapshots,plan 持久化到 ctx.plans
    (供 migrate job 加载);兼容警告走 logging(不污染 SSE 契约)。
    批次I-T5(spec §4.2):预检/执行序列最外层持源/目标实例锁——同进程第二个
    job 已由 JobStore 单任务锁先行 409,不会自锁;跨进程由命名互斥体互斥。
    """
    try:
        # 保证 job 至少存活数毫秒(单锁语义对连续请求确定,见 _JOB_MIN_ALIVE_SECONDS)
        time.sleep(_JOB_MIN_ALIVE_SECONDS)
        with instance_locks(ctx.game_root, src, dst) as lockinfo:
            _notify_abandoned_lock(job, ctx, lockinfo)
            # 旧布局快照迁移引导(spec §3.1):仅 legacy 命中(新位置无)时发 notice 事件;
            # 随后的 scan 会把新快照写入锚定位置,旧文件只读不写
            data_dir = ctx.game_root / ".mcmig"
            legacy_dir = ctx.legacy_snapshots.parent if ctx.legacy_snapshots is not None else None
            if legacy_dir is not None:
                for ver in (src, dst):
                    legacy_path, legacy_hit = find_snapshot(data_dir, legacy_dir, ver)
                    if legacy_hit:
                        job.emit(
                            {"type": "notice",
                             "text": STRINGS["notice.legacy_snapshot"].format(path=legacy_path)}
                        )
                # 旧布局规则只读回退提示(终审修复 I4,spec §3.1):_rules_dir 以同参数
                # 复算(与 build_plan 内部选择一致——同体归 None 的规范化也保持同构),
                # 提示经既有 notice 事件通道流出——不再让旧 rules.yaml 被无声忽略
                _rules_legacy = legacy_dir if legacy_dir != data_dir else None
                _chosen, rule_notices = _rules_dir(data_dir, _rules_legacy)
                for n in rule_notices:
                    job.emit({"type": "notice", "text": n})
            job.emit({"type": "phase", "name": "scan_src"})
            scan_version(ctx.game_root, src, ctx.snapshots)
            job.emit({"type": "phase", "name": "scan_dst"})
            scan_version(ctx.game_root, dst, ctx.snapshots)
            job.emit({"type": "phase", "name": "plan"})
            # 批次F:build_plan 三元组返回;GUI 暂不渲染 ⇄ 注记,_pairs 忽略(行为不变)
            plan, compat_warnings, _pairs = build_plan(
                Path.cwd(),
                ctx.game_root,
                src,
                dst,
                # 批次I-T1+终审修复 I4(spec §3.1):data_dir=实例态 .mcmig 目录
                # (快照读/写锚定位——scan 后两侧快照已在此,定位语义不受影响);
                # mcmig_dir=旧布局回退位,build_plan 内经 _rules_dir 据此做规则
                # 「新位置优先+旧布局只读回退」选择,绿色老用户的旧 rules.yaml
                # 不再被无声忽略;无旧布局时与 data_dir 同值,行为不变
                mcmig_dir=legacy_dir if legacy_dir is not None else data_dir,
                data_dir=data_dir,
                plans_dir=ctx.plans,
            )
            for w in compat_warnings:
                log.warning("[兼容警告] %s", w)
            job.emit(
                {
                    "type": "done",
                    "job_kind": "plan",
                    "src": src,
                    "dst": dst,
                    "plan": _group_actions(plan),
                    # 批次I-T3(spec §3.3):done 携带计划身份指纹(plan_id)与持久化
                    # 结果;migrate 请求须回传 plan_id,页面据 persisted 决定是否放行执行
                    "plan_id": plan_fingerprint(plan),
                    "persisted": True,
                }
            )
    except InstanceLockError as e:
        # 实例锁获取失败(批次I-T5):另一 mcmig 进程正在操作该实例
        job.emit(
            _error_event(
                STRINGS["job_err_instlock.what"],
                STRINGS["job_err_instlock.why"],
                {"holders": list(e.details.get("holders", []))},
            )
        )
    except PlanPersistError as e:
        # 白名单②的 GUI 侧(spec §3.3):保存失败不再吞——done 事件携带 persisted:false
        # + 三段式错误字段(一次事件,页面据此禁执行;计划未落盘,封死「审新执旧」)
        job.emit(
            {
                "type": "done",
                "job_kind": "plan",
                "src": src,
                "dst": dst,
                "plan": None,
                "plan_id": None,
                "persisted": False,
                "what": STRINGS["job_err_plan_persist.what"],
                "why": STRINGS["job_err_plan_persist.why"],
                "details": {"error": str(e)},
            }
        )
    except FsOpsError as e:
        job.emit(_error_event(e.what, e.why, {"error": str(e)}))
    except WorkdirError as e:
        job.emit(_error_event(e.what, e.why, {"error": str(e)}))
    except Exception as e:  # noqa: BLE001 — job 边界统一兜底,转 error 事件给前端
        log.exception("plan job 失败(%s → %s)", src, dst)
        job.emit(
            _error_event(
                STRINGS["job_err_failed.what"], str(e) or repr(e), {"error": repr(e)}
            )
        )
    finally:
        # 终态收口:done 标志 + status 归一(plan 无文件级 results;error 事件 → failed)
        _finish(job)


def _run_migrate_job(
    job: Job,
    ctx: InstanceCtx,
    src: str,
    dst: str,
    ask_yes: set[str],
    dry_run: bool,
    plan_id: str | None,
) -> None:
    """migrate job 线程体:加载已保存 plan → 审阅守卫校验 → 执行预检 → 执行 → 回写执行状态。

    实例态路径来自 job 启动时定格的 ctx(闭包持有,批次I-T1);plan 文件缺失/损坏
    → error 事件;审阅守卫校验(批次I-T3,spec §3.3,全部不可强制):
    ① plan_id 缺失(旧客户端)→ error 提示重新生成;② plan_fingerprint ≠ plan_id
    → guards_plan_mismatch;③ validate_review(实例/快照/规则)阻断 → error;
    随后 T2 preflight(已执行/快照过期/目录缺失/疑似占用,GUI 严格默认无决策放行
    通道)——它同时是「首跑闸门」:已执行计划以 plan_executed 明确阻断(GUI 无
    重跑通道),通过后才是真首跑;④ 首跑 check_action_states(源/目标状态漂移)
    阻断 → error;执行 consume **同一已加载 plan 对象**(不再按名二次读取);
    执行成功(非 dry-run 且零失败且未取消)时 mark_executed + save(重跑保留
    首次 executed_at;取消属部分完成,不回写 executed_at,依赖 identical 短路)。
    批次I-T5(spec §4.2):上述①-⑤全部检查与执行都持源/目标实例锁进行——
    **持锁重验**封「检查-取锁-执行」竞争窗(检查与执行之间不再可能插入他方
    写盘);同进程双 job 已由 JobStore 单任务锁先行 409,不会自锁。
    批次I-T6(spec §4.3):非 dry-run 执行持 write-ahead journal(ctx.jobs,
    ①意图→②操作→③完成三段序);取消端点写 job.status="cancelling",执行侧
    should_cancel 检查点命中后以终态 cancelled + 部分完成清单(done 事件
    cancelled:true)收尾;journal.finish 收尾=正常终态(取消/完成都在安全
    边界内结束),异常路径不 finish(留作中断记录,启动期 interrupted 可见)。
    journal 写失败停发(修复 finding 1):Executor 吞错只返回部分结果,job 凭
    journal.write_failed 识别,以 error 事件呈现停发原因且**不回写 executed_at**
    (与取消同等对待——部分执行不是成功,计划不锁,重跑/重审通道保持打开)。
    """
    try:
        time.sleep(_JOB_MIN_ALIVE_SECONDS)  # 同 _run_plan_job:单锁确定性
        job.emit({"type": "phase", "name": "migrate"})
        with instance_locks(ctx.game_root, src, dst) as lockinfo:
            _notify_abandoned_lock(job, ctx, lockinfo)
            p_path = ctx.plans / f"{src}__{dst}.plan.json"
            if not p_path.exists():
                job.emit(
                    _error_event(
                        STRINGS["job_err_plan_missing.what"],
                        STRINGS["job_err_plan_missing.why"],
                        {"plan_path": str(p_path)},
                    )
                )
                return
            try:
                plan = MigrationPlan.load(p_path)
            except (PlanFormatError, OSError) as e:
                job.emit(
                    _error_event(
                        STRINGS["job_err_plan_corrupt.what"],
                        STRINGS["job_err_plan_corrupt.why"],
                        {"error": str(e), "plan_path": str(p_path)},
                    )
                )
                return

            def _guards_error(code: str, blockers: list[PreflightBlocker]) -> None:
                """审阅守卫阻断 → 三段式 error 事件(details 带 code 与 blockers 列表)。"""
                job.emit(
                    _error_event(
                        STRINGS["job_err_guards.what"],
                        STRINGS["job_err_guards.why"],
                        {
                            "code": code,
                            "blockers": [
                                {"code": b.code, "message": b.message} for b in blockers
                            ],
                        },
                    )
                )

            # ① plan_id 必填(旧客户端兼容:None → error 提示重新生成,不可强制)
            if plan_id is None:
                job.emit(
                    _error_event(
                        STRINGS["job_err_plan_id.what"],
                        STRINGS["job_err_plan_id.why"],
                        {"code": "plan_id_missing", "plan_path": str(p_path)},
                    )
                )
                return
            # ② 计划身份:指纹失配 = 盘上计划与页面审阅的计划不是同一份 → 不可强制
            actual_fp = plan_fingerprint(plan)
            if actual_fp != plan_id:
                _guards_error("guards_plan_mismatch", [])
                return
            # ③ 审阅守卫:实例/双侧快照/规则 与签发时逐项比对(快照定位同 plan job 契约)。
            # 批次I-T5:持锁重验——validate_review 在实例锁内执行(spec §4.2 时序),
            # 检查通过到他方写盘的窗口被锁封死
            guard_snapshots = {
                src: ctx.snapshots / f"{src}.snapshot.json",
                dst: ctx.snapshots / f"{dst}.snapshot.json",
            }
            # 规则来源与签发侧同构(终审修复 I4):plan job 的 build_plan 经 _rules_dir
            # 选定规则目录(新位置优先+旧布局只读回退),此处同参复算同一目录——
            # 旧布局规则用户在 migrate 侧不因路径失配误报 rules_changed
            mig_data_dir = ctx.game_root / ".mcmig"
            mig_legacy = (
                ctx.legacy_snapshots.parent
                if ctx.legacy_snapshots is not None and ctx.legacy_snapshots.parent != mig_data_dir
                else None
            )
            chosen_rules_dir, _rule_notices = _rules_dir(mig_data_dir, mig_legacy)
            review_blockers = validate_review(
                plan, game_root=ctx.game_root,
                snapshot_paths=guard_snapshots, rule_sources=[chosen_rules_dir / "rules.yaml"],
            )
            if review_blockers:
                _guards_error(review_blockers[0].code, review_blockers)
                return
            # ④ 共享执行预检(批次I-T2):与 CLI 同一防护出口;GUI 走严格默认
            # (decisions=None,三类可决策项均阻断);快照/plan 定位同 plan job
            # 的实例态契约(锚定 ctx.game_root/.mcmig,旧布局回退取 legacy 父目录)。
            # 顺带充当「首跑闸门」:已执行计划在此以 plan_executed 阻断(GUI 无重跑通道),
            # 通过 ④ 才真正首跑 → ⑤ 才做审阅状态校验
            blockers, warnings = preflight_execute(
                plan, ctx.game_root, src, dst,
                data_dir=ctx.game_root / ".mcmig",
                legacy_dir=ctx.legacy_snapshots.parent if ctx.legacy_snapshots else None,
            )
            if blockers:
                job.emit(
                    _error_event(
                        STRINGS["job_err_preflight.what"],
                        STRINGS["job_err_preflight.why"],
                        {
                            "blockers": [
                                {"code": b.code, "message": b.message} for b in blockers
                            ]
                        },
                    )
                )
                return
            for w in warnings:
                # 事件契约增补 warning 型
                job.emit({"type": "warning", "code": w.code, "message": w.message})
            # ⑤ 首跑审阅状态校验(spec §3.3):源/目标当前状态 vs 快照记录——防审阅后、
            # 执行前的静默漂移;④ 已保证非重跑(未执行计划),状态漂移只能来自外部改动
            try:
                src_snap = Snapshot.load(guard_snapshots[src])
                dst_snap = Snapshot.load(guard_snapshots[dst])
            except (OSError, ValueError, SnapshotFormatError) as e:
                job.emit(
                    _error_event(
                        STRINGS["job_err_guards.what"], STRINGS["job_err_guards.why"],
                        {"code": "snapshot_changed", "error": str(e)},
                    )
                )
                return
            state_blockers = check_action_states(
                plan.actions, src_snap, dst_snap,
                ctx.game_root / "versions" / src, ctx.game_root / "versions" / dst,
            )
            if state_blockers:
                _guards_error(state_blockers[0].code, state_blockers)
                return
            total = len(plan.actions)
            index = 0

            # write-ahead journal(批次I-T6,spec §4.3):仅真实写盘路径启用——
            # dry-run 零写盘,journal 意图会让「待核对」清单失真,故不建
            journal: JobJournal | None = None
            if not dry_run:
                journal = JobJournal(ctx.jobs, job.id, "migrate")

            def _should_cancel() -> bool:
                """取消检查点数据源:cancel 端点已置 cancelling 中间态。"""
                return job.status == "cancelling"

            def _before_action(action: ActionRecord) -> None:
                """①意图(write-ahead):动作动手前持久化(恢复期核对依据)。"""
                assert journal is not None  # 仅 journal 存在时才作为回调注入
                journal.record_intent(
                    action.path, {"op": action.behavior.value, "backup": action.backup_target}
                )

            def _after_action(action: ActionRecord, _result: FileResult) -> None:
                """③完成:动作落盘后持久化(含失败动作——结局已知即收口)。"""
                assert journal is not None
                journal.record_completion(action.path)

            def on_file(result: FileResult) -> None:
                """progress_cb 包装:逐文件结果 → file 事件(failed/error 为失败补充字段)。"""
                nonlocal index
                index += 1
                job.emit(
                    {
                        "type": "file",
                        "path": result.path,
                        "status": result.status,
                        "index": index,
                        "total": total,
                        "backed_up": result.backed_up,
                        "failed": result.failed,
                        "error": result.error,
                    }
                )

            # 状态校验已在本 job ⑤ 完成,validate_states=False 显式跳过内部复检
            # (同一 plan 对象单次加载,无按名二次读取;避免双侧快照重复全量哈希)
            results = execute_migration(
                plan,
                ctx.game_root / "versions" / src,
                ctx.game_root / "versions" / dst,
                ask_yes,
                dry_run=dry_run,
                progress_cb=on_file,
                validate_states=False,
                should_cancel=_should_cancel,
                before_action=_before_action if journal is not None else None,
                after_action=_after_action if journal is not None else None,
            )
            # 执行结果定格到 job(批次I-T4:状态端点 results 字段的数据源,
            # 含备份位置与失败明细,非仅计数;job 线程一次性赋值后不再改动)
            job.results = results
            # 终态单次判定(终审修复 I2):持锁判定 cancelled 并封死取消窗口——
            # 取消落在判定前照常生效(下方按取消终态收尾);落在判定后被 cancel
            # 端点 409 拒绝,「done 事件/executed_at 回写/journal finish/GET status」
            # 四者由这一次判定导出,不再出现「done 说成功、状态报 cancelled」的矛盾
            cancelled = job.settle()
            # journal 写失败停发(修复 finding 1):Executor 吞掉 before_action 的
            # JournalError 只返回部分结果——此处的部分完成**不是**用户请求的取消,
            # 更不是成功:以 error 事件呈现停发原因,journal 不收尾(写已失败),
            # 不回写 executed_at(与取消同等对待,计划不锁,修复后可重跑/重审)
            if journal is not None and journal.write_failed:
                job.emit(
                    _error_event(
                        STRINGS["job_err_journal.what"],
                        STRINGS["job_err_journal.why"],
                        {
                            "journal": str(journal.path),
                            "dispatched": len(results),
                            "total": total,
                        },
                    )
                )
                return
            if journal is not None:
                # 收尾标记:取消/完成都在安全边界内结束(正常终态,退出中断清单)
                journal.finish()
            failed = [r for r in results if r.failed]
            # 五键恒存在(缺省 0,页面按固定键渲染不会取到 undefined,I1);
            # copied 计数含失败文件,failed 另列——与 CLI 展示口径一致
            summary: dict[str, int] = {
                "copied": 0,
                "identical": 0,
                "asked_no": 0,
                "skipped": 0,
                "failed": 0,
            }
            for status, count in Counter(r.status for r in results).items():
                summary[status] = count
            summary["failed"] = len(failed)
            for r in failed:
                log.warning("[失败] %s: %s", r.path, r.error)
            reminder: list[str] = []
            # 取消属部分完成:不回写 executed_at(重跑依赖 identical 短路,批次I-T6)
            if not dry_run and not failed and not cancelled:
                first_executed_at = plan.executed_at
                plan.mark_executed(
                    {
                        "copied": summary.get("copied", 0),
                        "identical": summary.get("identical", 0),
                        "asked_no": summary.get("asked_no", 0),
                        "failed": 0,
                    }
                )
                if first_executed_at is not None:
                    plan.executed_at = first_executed_at
                plan.save(p_path)
                reminder = _build_reminder(ctx.game_root, dst)
            done_event: dict[str, object] = {
                "type": "done",
                "job_kind": "migrate",
                "src": src,
                "dst": dst,
                "summary": summary,
                "reminder": reminder,
            }
            if cancelled:
                # 取消终态(批次I-T6,spec §4.3):部分完成清单——已完成=成功落盘
                # (copied/identical 且非 failed;失败拷贝 status 亦为 copied,修复
                # finding 2:从未写盘的文件不得计为「已完成」),未执行=计划中
                # 未分发的动手动作(SKIP 本就不迁;已分发但失败者由失败清单呈现)
                done_event["cancelled"] = True
                done_event["completed"] = [
                    r.path
                    for r in results
                    if not r.failed and r.status in ("copied", "identical")
                ]
                dispatched = {r.path for r in results}
                done_event["pending"] = [
                    a.path
                    for a in plan.actions
                    if a.behavior != Behavior.SKIP and a.path not in dispatched
                ]
            job.emit(done_event)
    except InstanceLockError as e:
        # 实例锁获取失败(批次I-T5):另一 mcmig 进程正在操作该实例
        job.emit(
            _error_event(
                STRINGS["job_err_instlock.what"],
                STRINGS["job_err_instlock.why"],
                {"holders": list(e.details.get("holders", []))},
            )
        )
    except FsOpsError as e:
        job.emit(_error_event(e.what, e.why, {"error": str(e)}))
    except WorkdirError as e:
        job.emit(_error_event(e.what, e.why, {"error": str(e)}))
    except JournalError as e:
        # journal 完成记录/收尾写入失败(after_action 路径异常上抛):迁移结果
        # 不确定,按失败呈现;盘上 journal 未收尾,待核对条目由 interrupted 呈现
        job.emit(
            _error_event(
                STRINGS["job_err_journal.what"],
                STRINGS["job_err_journal.why"],
                {"error": str(e)},
            )
        )
    except Exception as e:  # noqa: BLE001 — job 边界统一兜底,转 error 事件给前端
        log.exception("migrate job 失败(%s → %s)", src, dst)
        job.emit(
            _error_event(
                STRINGS["job_err_failed.what"], str(e) or repr(e), {"error": repr(e)}
            )
        )
    finally:
        # 终态收口:cancelling → cancelled(批次I-T6);error 事件 → failed;
        # results 含失败文件 → partial_failed;早退路径(守卫/预检阻断)
        # job.results 尚为 None,由 error 事件判 failed
        _finish(job, results=job.results)


# ---------------------------------------------------------------------------
# SSE 生成器与页面
# ---------------------------------------------------------------------------


def _sse_frame(ev: dict) -> str:
    """构造单帧 SSE:``id:`` 行携带 seq(客户端断线重连凭据),``data:`` 为事件 JSON。"""
    return f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"


def _sse_gen(job: Job, last_seq: int = 0) -> Iterator[str]:
    """把 job 事件翻译为 SSE 帧;job 结束即收尾断流(衔接协议,批次I-T4)。

    顺序:①历史已截断(last_seq 早于 floor)→ 先发 ``{"type":"reset"}``(无 id,
    客户端须重读 GET /api/jobs/{id} 重建状态);②重放历史尾(``seq > last_seq``,
    重放段已含终态则直接收尾);③活消费本订阅者队列,done/error 后断流;
    ④job.done 且队列排空(晚订阅兜底)亦收尾。流收尾/中断时注销队列。
    """
    reset, replay, q = job.subscribe(last_seq)
    try:
        if reset:
            yield f"data: {json.dumps({'type': 'reset'}, ensure_ascii=False)}\n\n"
        for ev in replay:
            yield _sse_frame(ev)
            if ev.get("type") in ("done", "error"):
                return
        while True:
            try:
                ev = q.get(timeout=0.5)
            except queue.Empty:
                if job.done:
                    break
                continue
            yield _sse_frame(ev)
            if ev.get("type") in ("done", "error"):
                break
    finally:
        job.unsubscribe(q)


def _read_index_html() -> str:
    """读取包内 index.html(缺失时记 error 日志后退化为占位片段,不外联任何资源)。

    缺失属打包事故(html 未入 package-data / 文件被杀软误删),静默降级会让
    玩家看到无功能占位页却无任何线索——故必须留 log.error 可观测性(I-2)。
    """
    try:
        return (
            resources.files("migration.gui")
            .joinpath("index.html")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError) as e:
        log.error("页面资源缺失:migration/gui/index.html 读取失败(%s),GET / 已降级为占位页", e)
        return _INDEX_FALLBACK


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class PlanRequest(BaseModel):
    """POST /api/plan 请求体。"""

    src: str
    dst: str


class ConfigRequest(BaseModel):
    """POST /api/config 请求体(步①「游戏根目录」输入框保存)。

    ``min_length=1`` 拒空串:空输入走 422 三段式,与非法路径同一呈现口径。
    """

    game_root: str = Field(min_length=1)


class MigrateRequest(BaseModel):
    """POST /api/migrate 请求体(ask_yes 即②步勾选的路径列表)。

    plan_id 为计划校验标识(plan job done 事件的 plan_id,批次I-T3):None →
    error 事件提示重新生成(旧客户端兼容);与盘上计划指纹失配 → guards_plan_mismatch。
    """

    src: str
    dst: str
    ask_yes: list[str] = Field(default_factory=list)
    dry_run: bool = False
    plan_id: str | None = None


# ---------------------------------------------------------------------------
# app 工厂
# ---------------------------------------------------------------------------


def create_app(workdir: WorkDir | None = None) -> FastAPI:
    """构建 FastAPI 应用(工厂;测试注入临时 workdir)。

    Args:
        workdir: mcmig 工作目录;None 时按 frozen/兼容模式自动解析
            (未配置 game_root 时为欢迎态布局,起服务不抛错,由步①引导配置)。

    Returns:
        FastAPI 应用(每 app 独立 JobStore,单任务锁以 app 为界)。
    """
    wdir = workdir if workdir is not None else resolve_workdir()
    store = JobStore()
    # 本地向导不需要 OpenAPI/文档端点,全部关闭减小暴露面
    app = FastAPI(title="mcmig", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.wdir = wdir
    app.state.jobs = store
    # 停机标志(批次I-T6):POST /api/shutdown 空闲时置 True;uvicorn 侧
    # 消费接线归 T10,本层只维护状态与守卫(job 运行 → 409)
    app.state.shutdown_requested = False
    # 启动期中断扫描(批次I-T6,spec §4.3):未收尾且有待核对条目的 job journal;
    # 欢迎态(未配置 game_root)为空清单。GET /api/jobs/interrupted 请求期现扫,
    # 覆盖「服务启动后才落下的中断记录」(测试即此形态)
    app.state.interrupted = scan_interrupted(wdir.jobs) if wdir.jobs is not None else []

    def _game_root() -> Path:
        """读当前 workdir 的游戏根目录;未配置(欢迎态)时抛 422 三段式。

        读 app.state.wdir 而非工厂闭包:POST /api/config 保存后会重建
        app.state.wdir(批次I-T1),后续请求立即反映新根。
        """
        root = app.state.wdir.game_root
        if root is None:
            raise ApiError(422, "err_no_game_root.what", "err_no_game_root.why")
        return root

    @app.middleware("http")
    async def host_guard(
        request: Request, call_next: Callable[[Request], object]
    ) -> object:
        """Host 头校验中间件(防 DNS rebinding):仅放行本机回环主机名,其余 403。

        端口不参与校验(启动侧绑定 127.0.0.1,端口由启动方控制)。
        """
        raw_host = request.headers.get("host", "")
        # 去掉端口(兼容 IPv6 字面量的方括号写法)
        host = raw_host.rsplit(":", 1)[0].strip("[]").lower()
        if host not in _ALLOWED_HOSTS:
            return _three_part_response(
                403,
                STRINGS["err_host.what"],
                STRINGS["err_host.why"],
                {"host": raw_host},
            )
        return await call_next(request)  # type: ignore[no-any-return]

    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        """可预期错误 → 三段式 JSON。"""
        return _three_part_response(exc.status_code, exc.what, exc.why, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """请求体校验失败 → 422 三段式(details 带字段级错误明细)。"""
        return _three_part_response(
            422,
            STRINGS["err_bad_request.what"],
            STRINGS["err_bad_request.why"],
            {"errors": jsonable_encoder(exc.errors())},
        )

    @app.exception_handler(Exception)
    async def _internal_handler(request: Request, exc: Exception) -> JSONResponse:
        """未预期错误 → 500 三段式(原始异常进日志,不外泄堆栈)。"""
        log.error("API 未预期错误: %s", exc, exc_info=True)
        return _three_part_response(
            500,
            STRINGS["err_internal.what"],
            STRINGS["err_internal.why"],
            {"error": str(exc)},
        )

    @app.get("/")
    def index() -> HTMLResponse:
        """返回向导页面(Task 7 的 index.html;当前为占位)。"""
        return HTMLResponse(_read_index_html())

    @app.get("/api/config")
    def api_config_get() -> dict[str, object]:
        """读当前配置:游戏根目录未配置时 game_root 为 null(步①输入框预填用)。"""
        root = app.state.wdir.game_root
        return {"game_root": str(root) if root is not None else None}

    @app.post("/api/config")
    def api_config_set(req: ConfigRequest) -> dict[str, bool]:
        """设置游戏根目录:校验(存在 + 含 versions/)→ save_game_root 落盘 → 重建 workdir。

        批次I-T1:保存后 ``app.state.wdir = resolve_workdir(new_root)`` 重建,
        实例态字段(snapshots/plans/rules)随之指向新根的 ``.mcmig``;
        运行中 job 已持有启动时定格的 InstanceCtx,不受本次重建影响。
        """
        path = Path(req.game_root.strip())
        if not path.is_dir() or not (path / "versions").is_dir():
            raise ApiError(
                422,
                "err_bad_game_root.what",
                "err_bad_game_root.why",
                {"game_root": req.game_root},
            )
        wdir.save_game_root(path)
        app.state.wdir = resolve_workdir(path)
        return {"ok": True}

    @app.get("/api/versions")
    def api_versions() -> dict[str, object]:
        """列可选版本 + 活跃版本(读 PCL.ini 的 ``Version:`` 行)。"""
        root = _game_root()
        return {"versions": list_versions(root), "active": read_active_version(root)}

    @app.post("/api/plan")
    def api_plan(req: PlanRequest) -> dict[str, str]:
        """启动 plan job(scan×2 → plan),返回 job_id;单锁占用时 409。

        实例上下文在 job 启动时一次性定格(批次I-T1):运行中改配置
        只影响后续 job,本 job 全程使用启动时的实例态路径。
        """
        ctx = _instance_ctx(app.state.wdir)
        _ensure_version_dirs(ctx.game_root, req.src, req.dst)
        job = store.start("plan", lambda j: _run_plan_job(j, ctx, req.src, req.dst))
        if job is None:
            raise ApiError(409, "err_job_busy.what", "err_job_busy.why")
        return {"job_id": job.id}

    @app.post("/api/migrate")
    def api_migrate(req: MigrateRequest) -> dict[str, str]:
        """启动 migrate job(加载已保存 plan → 执行),返回 job_id。

        实例上下文同 plan:job 启动时定格(批次I-T1)。
        """
        ctx = _instance_ctx(app.state.wdir)
        _ensure_version_dirs(ctx.game_root, req.src, req.dst)
        ask_yes = set(req.ask_yes)  # ②步勾选路径列表 → Executor ask 集合
        job = store.start(
            "migrate",
            lambda j: _run_migrate_job(
                j, ctx, req.src, req.dst, ask_yes, req.dry_run, req.plan_id
            ),
        )
        if job is None:
            raise ApiError(409, "err_job_busy.what", "err_job_busy.why")
        return {"job_id": job.id}

    # 声明顺序契约:/api/jobs/interrupted 必须先于 /api/jobs/{job_id} 声明,
    # 否则静态段会被动态路由吞掉(FastAPI 按声明序匹配,"interrupted" 会被
    # 当作 job_id 解析 → 404)
    @app.get("/api/jobs/interrupted")
    def api_jobs_interrupted() -> dict[str, object]:
        """启动期/请求期中断清单:未收尾 journal 的待核对条目(spec §4.3)。

        请求期现扫(而非只读 create_app 时的快照):服务长驻,启动后新落下
        的中断记录同样可见;未配置 game_root(欢迎态)或目录不存在 → 空清单。
        待核对 ≠ cancelled——只陈述「这条意图没有完成记录」,结局须人工核对。
        """
        jobs_dir = app.state.wdir.jobs
        items = scan_interrupted(jobs_dir) if jobs_dir is not None else []
        return {"items": items}

    @app.get("/api/jobs/{job_id}")
    def api_job_status(job_id: str) -> dict[str, object]:
        """查询 job 状态快照(spec §4.1 终端展示/断线重连后重建状态的唯一事实源)。

        返回 ``{status, kind, phase, revision, progress, summary, results, error}``;
        results 含逐文件明细(path/status/backed_up/failed/error),非仅计数。
        """
        job = store.get(job_id)
        if job is None:
            raise ApiError(404, "err_job_not_found.what", "err_job_not_found.why")
        return job.snapshot()

    @app.get("/api/jobs/{job_id}/events")
    def api_job_events(
        job_id: str, request: Request, last_event_id: int | None = None
    ) -> StreamingResponse:
        """SSE 进度流:phase/notice/warning/file/done/error 事件,job 结束即收尾断流。

        衔接协议(批次I-T4):``?last_event_id=N`` 或 ``Last-Event-ID`` 请求头
        续订(优先 query;缺省 0 = 从头重放,非法值按 0 处理);每帧附
        ``id: <seq>`` 行;历史截断时首帧 ``{"type":"reset"}``(客户端重读状态端点)。
        """
        job = store.get(job_id)
        if job is None:
            raise ApiError(404, "err_job_not_found.what", "err_job_not_found.why")
        raw = (
            str(last_event_id)
            if last_event_id is not None
            else request.headers.get("last-event-id")
        )
        try:
            last_seq = int(raw) if raw is not None else 0
        except ValueError:
            last_seq = 0
        return StreamingResponse(
            _sse_gen(job, last_seq),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.post("/api/jobs/{job_id}/cancel", status_code=202)
    def api_job_cancel(job_id: str) -> dict[str, str]:
        """取消迁移 job:置 cancelling 中间态,执行侧取消检查点命中后停发(202)。

        契约(批次I-T6,spec §4.3):plan job 不可取消 → 405 三段式(无假按钮
        语义);终态 job → 409(无可取消对象);未知 job → 404。取消只在动作间
        检查点生效——当前文件单元完成后才停,响应 202 即「已受理」而非「已停」,
        终态经 GET /api/jobs/{id} 轮询/SSE done 事件(cancelled:true)确认。
        """
        job = store.get(job_id)
        if job is None:
            raise ApiError(404, "err_job_not_found.what", "err_job_not_found.why")
        if not job.can_cancel:
            raise ApiError(
                405, "err_cancel_unsupported.what", "err_cancel_unsupported.why"
            )
        with job._lock:
            # 终审修复 I2:done 已收尾**或终态取消标志已单次判定(settle)**都拒绝——
            # 后者是「job 体刚判定成功、done 事件尚未发出」的窗口,此时受理取消会让
            # done 事件/executed_at/journal 与 GET status 自相矛盾
            if job.done or job._settled:
                raise ApiError(409, "err_cancel_terminal.what", "err_cancel_terminal.why")
            if job.status != "cancelling":
                job.status = "cancelling"  # 幂等:重复取消仍 202
        return {"status": "cancelling"}

    @app.post("/api/shutdown")
    def api_shutdown() -> dict[str, bool]:
        """退出服务(批次I-T6):空闲置停机标志(uvicorn 消费接线归 T10)。

        job 运行 → 409 三段式(先完成或取消任务再退出);置位后 HTTP 层无
        副作用——真正的进程退出由启动方(T10)轮询 shutdown_requested 执行。
        """
        if store.is_busy():
            raise ApiError(409, "err_shutdown_busy.what", "err_shutdown_busy.why")
        app.state.shutdown_requested = True
        return {"ok": True}

    return app
