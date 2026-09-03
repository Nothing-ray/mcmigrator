"""FastAPI 薄翻译层:本地 Web GUI 服务(job 模型 + SSE + 单任务锁 + Host 校验)。

职责边界(spec §2/§4):
- 「薄翻译」:4 个 API 只做参数校验与 job 编排,管线一律调 ``migration.pipeline``,
  绝不 import cli 的私有函数;核心模块不感知 HTTP。
- 安全:绑定 127.0.0.1 由 uvicorn 启动方负责,本层加 Host 头校验中间件
  (防 DNS rebinding),仅放行本机回环主机名;无任何外部请求。
- 单任务锁:同一时刻至多一个 job(防双标签页并发写盘),第二个请求 409。
- 错误三段式:API 错误统一 ``{what, why, details}``(what=失败对象,why=原因与建议)。

事件流 schema(spec §4,两级进度,MAA 模型裁剪):
- ``{"type":"phase","name":"scan_src|scan_dst|plan|migrate"}`` — 阶段切换
- ``{"type":"file","path":..,"status":..,"index":..,"total":..,"backed_up":..,
   "failed":..,"error":..}`` — 文件级进度(failed/error 为失败文件补充字段)
- ``{"type":"done",...}`` — plan job 带 ``plan``(按 origin 分组的 action 摘要);
  migrate job 带 ``summary``(各 status 计数)与 ``reminder``(PCL 两处配置提醒)
- ``{"type":"error","what":..,"why":..,"details":{..}}`` — job 内失败,任务终止

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
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..executor import FileResult
from ..fsops import FsOpsError
from ..plan import ORIGIN_REGISTRY, MigrationPlan, Origin, PlanFormatError
from ..pipeline import (
    build_plan,
    execute_migration,
    list_versions,
    read_active_version,
    scan_version,
)
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

# index.html 缺失时的占位片段(Task 7 创建真页面前 GET / 的兜底)
_INDEX_FALLBACK = "<h1>mcmig</h1>"


# ---------------------------------------------------------------------------
# job 模型:事件队列 + 完成标志 + 单任务锁仓库
# ---------------------------------------------------------------------------


class Job:
    """单个后台任务:事件队列 + 完成标志(SSE 生成器据此收尾断流)。"""

    def __init__(self, job_id: str, kind: str) -> None:
        """初始化 job。

        Args:
            job_id: 任务唯一标识(uuid4 hex,由 JobStore 分配)。
            kind: 任务类型("plan" / "migrate")。
        """
        self.id = job_id
        self.kind = kind
        self.events: queue.Queue[dict] = queue.Queue()
        self.done = False

    def emit(self, event: dict) -> None:
        """推送一个事件入队(SSE 生成器按序取出)。"""
        self.events.put(event)


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


def _run_plan_job(job: Job, workdir: WorkDir, game_root: Path, src: str, dst: str) -> None:
    """plan job 线程体:scan src → scan dst → build_plan,推送 phase/done/error 事件。

    快照写 workdir.snapshots,plan 持久化到 workdir.plans(供 migrate job 加载);
    兼容警告走 logging(不污染 SSE 契约)。
    """
    try:
        # 保证 job 至少存活数毫秒(单锁语义对连续请求确定,见 _JOB_MIN_ALIVE_SECONDS)
        time.sleep(_JOB_MIN_ALIVE_SECONDS)
        job.emit({"type": "phase", "name": "scan_src"})
        scan_version(game_root, src, workdir.snapshots)
        job.emit({"type": "phase", "name": "scan_dst"})
        scan_version(game_root, dst, workdir.snapshots)
        job.emit({"type": "phase", "name": "plan"})
        plan, compat_warnings = build_plan(
            Path.cwd(),
            game_root,
            src,
            dst,
            # mcmig_dir 必须取 snapshots 的「父目录」:build_plan 内部按
            # <mcmig_dir>/snapshots 读快照、<mcmig_dir>/rules.yaml 读规则——
            # 兼容布局(=.mcmig)与绿色布局(=data/<slug>)下 snapshots.parent
            # 都与 workdir.rules/plans 同层;若误传 workdir.root,绿色模式下
            # 会错位到 data/snapshots/(C1 回归点,有绿色 smoke 测试守护)
            mcmig_dir=workdir.snapshots.parent,
            plans_dir=workdir.plans,
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
        # done 标志必须在最后一个事件入队之后置位(SSE 以「done 且队列空」收尾)
        job.done = True


def _run_migrate_job(
    job: Job,
    workdir: WorkDir,
    game_root: Path,
    src: str,
    dst: str,
    ask_yes: set[str],
    dry_run: bool,
) -> None:
    """migrate job 线程体:加载已保存 plan → execute_migration → 回写执行状态。

    plan 文件缺失/损坏 → error 事件;执行成功(非 dry-run 且零失败)时
    mark_executed + save(重跑保留首次 executed_at,与 CLI 语义一致)。
    """
    try:
        time.sleep(_JOB_MIN_ALIVE_SECONDS)  # 同 _run_plan_job:单锁确定性
        job.emit({"type": "phase", "name": "migrate"})
        p_path = workdir.plans / f"{src}__{dst}.plan.json"
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
        total = len(plan.actions)
        index = 0

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

        results = execute_migration(
            plan,
            game_root / "versions" / src,
            game_root / "versions" / dst,
            ask_yes,
            dry_run=dry_run,
            progress_cb=on_file,
        )
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
        if not dry_run and not failed:
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
            reminder = _build_reminder(game_root, dst)
        job.emit(
            {
                "type": "done",
                "job_kind": "migrate",
                "src": src,
                "dst": dst,
                "summary": summary,
                "reminder": reminder,
            }
        )
    except FsOpsError as e:
        job.emit(_error_event(e.what, e.why, {"error": str(e)}))
    except WorkdirError as e:
        job.emit(_error_event(e.what, e.why, {"error": str(e)}))
    except Exception as e:  # noqa: BLE001 — job 边界统一兜底,转 error 事件给前端
        log.exception("migrate job 失败(%s → %s)", src, dst)
        job.emit(
            _error_event(
                STRINGS["job_err_failed.what"], str(e) or repr(e), {"error": repr(e)}
            )
        )
    finally:
        job.done = True


# ---------------------------------------------------------------------------
# SSE 生成器与页面
# ---------------------------------------------------------------------------


def _sse_gen(job: Job) -> Iterator[str]:
    """把 job.events 队列翻译为 SSE ``data:`` 行;job 结束即收尾断流。

    收尾条件:取到 done/error 事件,或 job.done 且队列已排空(客户端晚订阅时回放)。
    """
    while True:
        try:
            event = job.events.get(timeout=0.5)
        except queue.Empty:
            if job.done:
                break
            continue
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        if event.get("type") in ("done", "error"):
            break


def _read_index_html() -> str:
    """读取包内 index.html(Task 7 创建;缺失时退化为占位片段,不外联任何资源)。"""
    try:
        return (
            resources.files("migration.gui")
            .joinpath("index.html")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError):
        return _INDEX_FALLBACK


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class PlanRequest(BaseModel):
    """POST /api/plan 请求体。"""

    src: str
    dst: str


class MigrateRequest(BaseModel):
    """POST /api/migrate 请求体(ask_yes 即②步勾选的路径列表)。"""

    src: str
    dst: str
    ask_yes: list[str] = Field(default_factory=list)
    dry_run: bool = False


# ---------------------------------------------------------------------------
# app 工厂
# ---------------------------------------------------------------------------


def create_app(workdir: WorkDir | None = None) -> FastAPI:
    """构建 FastAPI 应用(工厂;测试注入临时 workdir)。

    Args:
        workdir: mcmig 工作目录;None 时按 frozen/兼容模式自动解析
            (绿色模式未配置游戏目录会抛 WorkdirError)。

    Returns:
        FastAPI 应用(每 app 独立 JobStore,单任务锁以 app 为界)。
    """
    wdir = workdir if workdir is not None else resolve_workdir()
    store = JobStore()
    # 本地向导不需要 OpenAPI/文档端点,全部关闭减小暴露面
    app = FastAPI(title="mcmig", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.workdir = wdir
    app.state.jobs = store

    def _game_root() -> Path:
        """从 workdir config 读游戏根目录;未配置时抛 422 三段式。"""
        root = wdir.game_root()
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

    @app.get("/api/versions")
    def api_versions() -> dict[str, object]:
        """列可选版本 + 活跃版本(读 PCL.ini 的 ``Version:`` 行)。"""
        root = _game_root()
        return {"versions": list_versions(root), "active": read_active_version(root)}

    @app.post("/api/plan")
    def api_plan(req: PlanRequest) -> dict[str, str]:
        """启动 plan job(scan×2 → plan),返回 job_id;单锁占用时 409。"""
        root = _game_root()
        _ensure_version_dirs(root, req.src, req.dst)
        job = store.start(
            "plan", lambda j: _run_plan_job(j, wdir, root, req.src, req.dst)
        )
        if job is None:
            raise ApiError(409, "err_job_busy.what", "err_job_busy.why")
        return {"job_id": job.id}

    @app.post("/api/migrate")
    def api_migrate(req: MigrateRequest) -> dict[str, str]:
        """启动 migrate job(加载已保存 plan → 执行),返回 job_id。"""
        root = _game_root()
        _ensure_version_dirs(root, req.src, req.dst)
        ask_yes = set(req.ask_yes)  # ②步勾选路径列表 → Executor ask 集合
        job = store.start(
            "migrate",
            lambda j: _run_migrate_job(j, wdir, root, req.src, req.dst, ask_yes, req.dry_run),
        )
        if job is None:
            raise ApiError(409, "err_job_busy.what", "err_job_busy.why")
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}/events")
    def api_job_events(job_id: str) -> StreamingResponse:
        """SSE 进度流:phase/file/done/error 四型事件,job 结束即收尾断流。"""
        job = store.get(job_id)
        if job is None:
            raise ApiError(404, "err_job_not_found.what", "err_job_not_found.why")
        return StreamingResponse(
            _sse_gen(job),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return app
