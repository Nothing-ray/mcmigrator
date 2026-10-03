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
- ``{"type":"overflow"}`` — 订阅者广播队列满被摘的显式断流信号(批次I-W3 T2,
  衔接协议):本帧后流即收尾,无 id;客户端须重读 GET /api/jobs/{id} 并以
  Last-Event-ID 重订——被摘订阅者不再空转到 job 收尾
- ``{"type":"done",...}`` — plan job 带 ``plan``(按 origin 分组的 action 摘要)、
   ``plan_id``/``persisted``(计划身份指纹与持久化结果,批次I-T3;保存失败时
   persisted:false 且同事件携带三段式错误字段,页面禁执行)与 ``diff``(审阅摘要:
   桶计数/体积/待确认/将覆盖/mod 配对/兼容警示/client_only/世界提示/检测范围
   说明,批次I-W3 T6);migrate job 带 ``summary``(各 status 计数)、``reminder``
   (PCL 两处配置提醒)与 ``backup_dir``(冲突备份目录,T6)
- ``{"type":"error","what":..,"why":..,"details":{..}}`` — job 内失败,任务终止

job 状态机(批次I-T4/T6,GET /api/jobs/{id} 的 ``status`` 字段):
running → succeeded | partial_failed | failed | cancelled;migrate/swap 运行中
可经 POST /api/jobs/{id}/cancel 进入中间态 cancelling(202),执行侧取消检查点
命中后以 cancelled 收尾(首个动作恒执行,安全边界内无半途文件)——
succeeded=done 且无失败文件;partial_failed=migrate 执行完但 results 含
失败文件;failed=发出过 error 事件;cancelled=用户取消的部分完成。
plan/swap_preflight job 不可取消(cancel → 405),终态 job 取消 → 409;
swap job 装包段结束后取消窗原子关闭(cancel_closed,批次I-W3 T7 白名单⑩:
重扫+规划段不可取消 → 409,封死「done 带成功计划 vs GET 收口 cancelled」
的分歧——取消端点全部判定在 job._lock 内重检,锁外只做 404)。

swap 两阶段(批次I-W3 T7,spec §5.2/§8):
- ``POST /api/swap/preflight {src,dst,new_pack}`` → 202 {job_id}(只读 job,
  首步恒重扫 src);done 载荷 = SwapPreflightOutcome 序列化 + backup_preview;
  preflight_id(输入指纹)入 ``app.state.swap_preflights`` 指纹仓(仅保留
  最近 10 条,防无界增长)。
- ``POST /api/swap/apply {preflight_id,src,dst,accept_incompat,accept_extras,
  overwrite_jars}`` → 202 {job_id}(kind="swap" 可取消):持实例锁后**三重重验**
  (仓内身份 vs 当前 ctx / 指纹重算 / 兼容重跑;preflight_id 未知或
  overwrite_jars ⊄ conflicts → 422,不建 job)→ 装包(覆盖备份+装包 journal)
  → 取消窗原子关闭 → 同 job 内链式重扫+规划(modpack_swap=True),done 载荷
  与 plan job 同形 + install/backup_dir;链式段失败 → error code=
  swap_replan_failed 且载荷带 install+backup_dir(装包已成功,不可静默)。

恢复入口(批次I-T6,spec §4.3;批次I-W3 T3 增量化+清除通道):migrate job 持
write-ahead journal(<game_root>/.mcmig/jobs/<job_id>.jsonl,JSONL 追加,首行
start 携带 src/dst/game_root 身份;意图→操作→完成三段序);GET /api/jobs/
interrupted 列出未收尾的 job——锁探针(journal._journal_owner_alive)判活,
跨进程活任务(CLI 持锁)跳过,条目携带 src/dst/game_root/unknown_progress;
POST /api/jobs/interrupted/{job_id}/dismiss 删除已确认前任死亡的中断记录
(锁被持 → 409)。
退出入口 POST /api/shutdown(修复 A5):空闲经 ``JobStore.begin_shutdown``
原子排空(置 draining + 停机标志)后由启动方消费 ``shutdown_requested``
执行进程退出(窗口模式 gui/app.py 轮询;浏览器模式 cli._cmd_gui 主循环);
job 运行 → 409。关闭边界(批次I-W3 T9,评审 v3 P2-5/v4 P2-2):
``JobStore.begin_shutdown`` 锁内原子判定(忙 → 阻止;闲 → 置 draining),
此后 job 创建端点(plan/migrate/swap preflight+apply)在 ``JobStore.start``
同一临界区首判排空态抛 ``StoreDraining`` → 503 err_shutting_down(白名单⑪)。

本模块无任何 print:过程信息走 logging,结果走返回值/事件。
"""

from __future__ import annotations

import json
import logging
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit
from collections import Counter
import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..executor import BACKUP_DIR, FileResult
from ..fsops import FsOpsError, md5_of
from ..instlock import InstanceLockError, instance_locks
from ..journal import (
    JobJournal,
    JournalError,
    _journal_owner_alive,  # journal 活性探针单点(scan 与 dismiss 共用,W3 T3)
    scan_interrupted,
)
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
    build_plan,
    execute_migration,
    find_snapshot,
    list_versions,
    precheck_execution,  # 执行前置检查单点(批次I-W3 T1):守卫→预检→状态校验三段合一
    read_active_version,
    scan_version,
    select_rules_dir,  # 规则目录「新位置优先+旧布局只读回退」单点(W2.5 复审 B3 转正;cli/pipeline 内部同源消费)
    swap_install,  # 换包装包(批次I-W3 T7):覆盖备份+journal+取消检查点
    swap_preflight,  # 换包预检(批次I-W3 T7):兼容/清单/指纹,只读
    version_name_error,  # 版本名单一路径分量校验(0.12.0 复审#1):防越出 game_root
)
from ..preflight import PreflightBlocker
from ..review import ReviewStateError, plan_fingerprint
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

# 单订阅者广播队列上限(批次I-T4):慢订阅者队列满即被移除,绝不反压 job
# 线程;被摘订阅者的 SSE 流以一帧 {"type":"overflow"} 显式收尾(W3 T2),
# 客户端凭 Last-Event-ID 重订或重读状态端点;测试 monkeypatch 锚点。
_SUBSCRIBER_QUEUE_LIMIT = 256

# swap 预检指纹仓保留条数上限(批次I-W3 T7,评审 P2-3):preflight_id → 输入
# 指纹等记录,apply 持锁重验用;超出即淘汰最旧(进程内字典,防无界增长)
_SWAP_PREFLIGHT_CACHE_LIMIT = 10

# 终态 job 保留条数上限(修复 A7):_jobs 供迟到 GET/SSE 追溯消费,长驻服务下
# 若只增不减,每个终态 job 常驻整份计划/diff 终态载荷与 ≤500 条事件历史(大
# 计划可至 MB 级),内存随会话线性增长;超出即按插入序淘汰最旧**终态** job,
# 未收尾 job 永不淘汰(测试 monkeypatch 锚点,如 3)
_MAX_JOBS = 32

# index.html 缺失时的占位片段(Task 7 创建真页面前 GET / 的兜底)
_INDEX_FALLBACK = "<h1>mcmig</h1>"

# job_id 形态白名单(修复 A1,安全;M2 放宽为「单一路径分量」语义):禁止
# 路径分隔符、盘符/ADS 冒号、通配/保留字符与控制字符——修复前 dismiss 端点
# 直接拼接 ``jobs_dir / f"{job_id}…"``,Windows 下 URL 里的 ``%5C`` 解码出
# ``\`` 被 pathlib 当路径分隔符,构成可删除 ``jobs/`` 之外任意 ``.json``/
# ``.jsonl`` 的通道(含 ``versions/<ver>.json`` 版本清单与
# ``launcher_profiles.json``;测试 test_dismiss_rejects_path_traversal_job_id)。
# 修复 M2:不再限制为 ASCII 窄字符集——journal 文件名即 job_id(scan_interrupted
# 以 p.stem 列条目),用户手放的中文/空格名档案若恒被拒会「横幅可见但 dismiss
# 恒 404」不可清除;单分量+目录跳转/设备名排除已是等价的路径安全边界
_JOB_ID_RE = re.compile(r'\A[^\\/:*?"<>|\x00-\x1f]+\Z')

# Windows 保留设备名(评审 M1):``jobs/NUL.jsonl`` 等被 Win32 解析为设备,
# exists() 为真而 unlink() 抛 PermissionError → 500;设备名规则=首个 '.' 之前
# 的段匹配(大小写无关、忽略扩展名,**且尾随空格/点会被 Win32 归一**——实测
# ``NUL  .jsonl``/``CON .txt`` 同为设备),故段先 rstrip(" .") 再整段排除
_JOB_ID_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def _valid_job_id(job_id: str) -> bool:
    """job_id 形态白名单校验(A1/M1/M2):**单一路径分量**且非 ``.``/``..``/
    Windows 保留设备名(首个 ``.`` 前的段,rstrip 空白/点);非 ASCII(中文/空格)放行。"""
    return (
        bool(_JOB_ID_RE.match(job_id))
        and job_id not in (".", "..")
        and job_id.split(".")[0].rstrip(" .").lower() not in _JOB_ID_RESERVED
    )


# ---------------------------------------------------------------------------
# job 模型:状态机 + 单调事件序号 + 历史重放 + 每订阅者广播队列 + 单任务锁仓库
# ---------------------------------------------------------------------------


class _Sub:
    """单个订阅者:独立有界队列 + 溢出丢弃标记(emit 摘除时置位,生成器据此断流)。"""

    __slots__ = ("queue", "dropped")

    def __init__(self) -> None:
        """初始化订阅者(队列容量取模块常量 ``_SUBSCRIBER_QUEUE_LIMIT``)。"""
        self.queue: queue.Queue[dict] = queue.Queue(maxsize=_SUBSCRIBER_QUEUE_LIMIT)
        self.dropped = False


class Job:
    """单个后台任务:状态机 + 事件广播底座(SSE 衔接协议,批次I-T4)。

    - 每个事件带 ``"seq"``(= revision,单调 +1);历史保留最近
      ``_EVENT_HISTORY_LIMIT`` 条,``_floor_seq`` 记已截断的最大序号。
    - 每个订阅者独享一条有界队列(``_SUBSCRIBER_QUEUE_LIMIT``):满则摘除
      该订阅者并置 dropped 标记,emit 永不阻塞(复制线程不被慢客户端反压);
      其 SSE 流以一帧 ``{"type":"overflow"}`` 尽快收尾(W3 T2)。
    - 状态机词表:running/cancelling(取消中间态,批次I-T6 由 cancel 端点
      写入)/succeeded/partial_failed/failed/cancelled(终态,由 emit 终态
      事件在同一临界区内原子收口,W3 T2——封死「GET 读 running + revision
      已含终态事件」的撕裂态)。
    """

    def __init__(self, job_id: str, kind: str) -> None:
        """初始化 job。

        Args:
            job_id: 任务唯一标识(uuid4 hex,由 JobStore 分配)。
            kind: 任务类型("plan" / "migrate" / "swap" / "swap_preflight")。
        """
        self.id = job_id
        self.kind = kind
        # 状态机对外契约(cancel 端点写 cancelling;终态由 emit 终态事件原子收口)
        self.status = "running"
        self.revision = 0
        # 装包后取消窗关闭标志(批次I-W3 T7,评审 v3/v4 P2-3):swap job 装包段
        # 结束时在 _lock 内置位——此后 cancel 端点 409(重扫+规划段不可取消);
        # migrate job 无置位路径,取消窗恒开
        self.cancel_closed = False
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
        # 终态事件载荷(emit done/error 时与状态收口同一临界区定格;运行中为 None)
        self._done_payload: dict | None = None
        # update job 字节进度(progress 事件最新一条;快照 progress_bytes 数据源)
        self._last_progress: dict | None = None
        # 广播底座(锁保护历史/订阅队列的一致性)
        self._lock = threading.Lock()
        self._history: list[dict] = []
        self._floor_seq = 0  # 已截断的最大 seq(续订早于此 → 不可完整重放 → reset)
        self._subs: list[_Sub] = []

    @property
    def can_cancel(self) -> bool:
        """取消支持判定:kind ∈ {"migrate", "swap", "update"} 且取消窗未关(白名单⑩)。

        仅作取消端点**锁内**复核的辅助(锁外读是 TOCTOU,评审 v4 P2-3——
        迟到取消在锁外读到允许→关窗→进锁置 cancelling 的交错已复核可复现);
        cancel_closed 由 swap job 装包段结束时在 _lock 内置位;migrate 无
        置位路径,恒可取消(既有用例不受影响)。
        """
        return self.kind in ("migrate", "swap", "update") and not self.cancel_closed

    def emit(self, event: dict) -> None:
        """派发一个事件:分配 seq → 记入历史(截断)→ 广播到全部订阅者。

        慢订阅者队列满时就地摘除该订阅者并置 dropped 标记(不反压本线程,
        其 SSE 流将尽快以 overflow 帧收尾);事件派生状态(最后 phase/file、
        summary、error)同步维护供状态端点查询。

        终态原子提交(批次I-W3 T2,P2-4):``type`` 为 done/error 时,状态收口
        (cancelling→cancelled/error→failed/results 含失败→partial_failed/否则
        succeeded)+ ``done`` 标志 + ``_done_payload`` 定格 与 revision/history/
        广播在同一临界区完成——emit 返回即终态,GET 的 snapshot() 与订阅重放
        永不再见到「running + revision 已含终态事件」的撕裂态。
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
            elif etype == "progress":
                # update job 字节进度(spec §4.3.4;与 file 事件通道并行)
                self._last_progress = ev
            elif etype == "error":
                self._error = ev
            elif etype == "done" and "summary" in ev:
                self._summary = ev["summary"]
            for sub in list(self._subs):
                try:
                    sub.queue.put_nowait(ev)
                except queue.Full:
                    # 慢订阅者:摘除并标记——其 SSE 流将尽快以 overflow 帧收尾,
                    # 客户端凭 Last-Event-ID 重订或重读状态端点;绝不反压本线程
                    sub.dropped = True
                    self._subs.remove(sub)
            if etype in ("done", "error"):
                # 终态原子提交(P2-4):状态收口+done 标志+终态载荷 与
                # revision/history/广播同一临界区——GET 的 snapshot() 与订阅
                # 重放永不再见到「running + revision 已含终态事件」的撕裂态;
                # _done_payload 让 GET 与 SSE 消费同一份终态结果(评审 P2-4)
                self._done_payload = ev
                self._settle_status_locked()
                self.done = True

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

    def subscribe(self, last_seq: int = 0) -> tuple[bool, list[dict], _Sub]:
        """注册一个订阅者,并原子快照 ``seq > last_seq`` 的历史重放段。

        历史快照与队列注册在同一临界区完成,重放段与实时段无缝衔接
        (不重不漏);历史已截断、无法完整重放时置 reset 标记。

        Args:
            last_seq: 客户端已收到的最大 seq(Last-Event-ID);0 表示从头订阅。

        Returns:
            (是否需要 reset, 历史重放事件列表, 本订阅者对象)。
        """
        with self._lock:
            reset = last_seq < self._floor_seq
            replay = [e for e in self._history if e["seq"] > last_seq]
            sub = _Sub()
            self._subs.append(sub)
            return reset, replay, sub

    def unsubscribe(self, sub: _Sub) -> None:
        """注销订阅者(SSE 流收尾/中断时调用;已被满摘除的订阅者静默忽略)。"""
        with self._lock:
            try:
                self._subs.remove(sub)
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
            summary|None, results|None, error:{what,why,details}|None,
            done:终态事件原样|None}``。``done`` 为完整终态事件载荷(W3 T2,
            评审 P2-4:GET 与 SSE 消费同一份终态结果,刷新恢复/迟到重读
            可按 kind 重建页面);运行中为 None。
        """
        with self._lock:
            progress: dict[str, int] | None = None
            if self._last_file is not None:
                progress = {
                    "index": self._last_file["index"],
                    "total": self._last_file["total"],
                }
            progress_bytes: dict[str, int] | None = None
            if self._last_progress is not None:
                progress_bytes = {
                    "received": self._last_progress["received"],
                    "total": self._last_progress["total"],
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
                "progress_bytes": progress_bytes,
                "summary": self._summary,
                "results": results,
                "error": error,
                "done": self._done_payload,
            }

    def _settle_status_locked(self) -> None:
        """终态判定收口(调用方须持 ``_lock``;原 ``_finish`` 判定体等价迁移)。

        判定规则:发过 error 事件 → failed(修复 A3①,error 优先);取消中间态
        (cancelling)→ cancelled;migrate 的 results 含失败文件 → partial_failed;
        否则 succeeded。plan job 持久化失败(persisted:false 的 done 事件)不属
        error 事件,状态仍为 succeeded(页面据事件内三段式字段禁执行)。

        error 优先的理由(修复 A3①):取消落在守卫/预检/状态校验窗口(可数秒)
        而随后以 error 阻断的交错——修复前按 cancelling 收口成 cancelled,
        GET 报「已取消」而 SSE error 事件说「任务失败」,状态接口与事件流
        自相矛盾;纯取消路径不发 error,仍按 cancelled 收口,语义不变。

        终审修复 I2:migrate 的取消标志已由 job 体在 done 事件前经 ``settle()``
        锁内单次判定并封死翻转窗口,此处对 cancelling 的重判因此与 done 事件
        字段恒一致(plan job 不可取消,无此竞态)。``done`` 标志由调用方
        (emit 终态原子段/防御性收口)在同一临界区内紧随置位。
        """
        if self._error is not None:
            self.status = "failed"
        elif self.status == "cancelling":
            self.status = "cancelled"
        elif self.results is not None and any(r.failed for r in self.results):
            self.status = "partial_failed"
        else:
            self.status = "succeeded"


class StoreDraining(Exception):
    """仓库已进入排空态(窗口关闭边界,批次I-W3 T9):拒绝注册新 job。

    由 :meth:`JobStore.start` 在锁内首判抛出;端点捕获后转 503(白名单⑪,
    评审 v4 P2-2)——判定与注册同一临界区,「端点过检 → 关窗 → start 照常
    注册」的交错不存在。
    """


class JobStore:
    """单任务锁的 job 仓库:同一时刻至多一个 job 在跑,第二个 start 返回 None。

    每个 app 实例独享一个 store(create_app 内创建),避免跨实例任务串扰。
    关闭边界(批次I-W3 T9,评审 v3 P2-5):``begin_shutdown`` 锁内原子判定,
    空闲即置 ``_draining``;此后 ``start`` 在同一 ``self._lock`` 临界区首判
    排空态并抛 :class:`StoreDraining`(端点 → 503)。
    """

    def __init__(self) -> None:
        """初始化空仓库(内部锁保护 current 槽位)。"""
        self._lock = threading.Lock()
        self._current: Job | None = None
        self._jobs: dict[str, Job] = {}
        self._draining = False

    @property
    def draining(self) -> bool:
        """是否已进入排空态(begin_shutdown 空闲判定时置位;只读视图)。"""
        with self._lock:
            return self._draining

    def begin_shutdown(self) -> tuple[bool, str | None]:
        """关闭边界原子判定(评审 v3 P2-5):锁内一次完成「检查空闲 + 置排空」。

        - 有未收尾 job → (True, 中文任务描述),**状态不变**(不置 draining,
          窗口停留,任务收尾后可再次判定放行);
        - 空闲 → 置 ``_draining = True`` 并 (False, None)——此后 start() 在
          同一临界区拒绝新 job(StoreDraining → 端点 503),封死「检查为
          空闲 → 新迁移启动 → 窗口退出」的竞争窗。

        Returns:
            (是否阻止关闭, 阻止时的当前任务描述或 None)。
        """
        with self._lock:
            if self._current is not None and not self._current.done:
                # 描述内联取 _current.id(与 active_id() 同值;锁不可重入,
                # 不得在锁内再调取锁方法)
                return True, f"任务 {self._current.id} 正在执行"
            self._draining = True
            return False, None

    def start(self, kind: str, runner: Callable[[Job], None]) -> Job | None:
        """注册并启动后台 job 线程;已有未完成 job 时返回 None(单任务锁)。

        Args:
            kind: 任务类型("plan" / "migrate" / "swap" / "swap_preflight")。
            runner: job 线程目标函数(接收 Job;负责推送事件,终态事件在
                emit 内原子收口 done,W3 T2)。

        Returns:
            新建的 Job;已有任务在跑则返回 None(调用方应答 409)。

        Raises:
            StoreDraining: 已进入排空态(窗口关闭边界)——**锁内首判**,先于
                ``_current`` 占用检查(评审 v4 P2-2,白名单⑪:端点不做锁外
                预检,判定与注册同临界区)。
        """
        with self._lock:
            if self._draining:
                raise StoreDraining("服务正在退出,拒绝新任务")
            if self._current is not None and not self._current.done:
                return None
            job = Job(uuid.uuid4().hex, kind)
            self._current = job
            self._jobs[job.id] = job
            self._evict_done_locked()  # 终态 job 淘汰(修复 A7):仓库内存有界
        threading.Thread(target=runner, args=(job,), daemon=True, name=f"mcmig-{kind}").start()
        return job

    def _evict_done_locked(self) -> None:
        """淘汰最旧的已收尾 job(调用方须持 ``self._lock``;修复 A7)。

        只保留最近 ``_MAX_JOBS`` 条终态 job(迟到 GET/SSE 的追溯窗口足够),
        未收尾 job(在跑或刚注册)永不淘汰;被淘汰 job 的 GET/SSE 按未知
        404(与既有语义一致)。
        """
        if len(self._jobs) <= _MAX_JOBS:
            return
        for jid in list(self._jobs):
            if len(self._jobs) <= _MAX_JOBS:
                break
            candidate = self._jobs[jid]
            if not candidate.done:
                continue  # 未收尾:保留(在跑 job 或刚注册的新 job)
            del self._jobs[jid]

    def get(self, job_id: str) -> Job | None:
        """按 id 查找 job(含已完成的,供 SSE 追溯消费)。"""
        return self._jobs.get(job_id)

    def active_id(self) -> str | None:
        """当前在跑 job 的 id;无在跑任务返回 None。

        interrupted 清单活性过滤(W2.5 复审 B4)用:在跑 migrate job 的 journal
        同样「未收尾+有 unfinished」,但它是**当前任务**而非「上次未完成的迁移」。
        """
        with self._lock:
            if self._current is not None and not self._current.done:
                return self._current.id
            return None


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


def _defensive_settle(job: Job) -> None:
    """job 线程 finally 的防御性收口(修复 A3②):未产出终态事件即退出(异常
    路径/emit 自身失败)时补发兜底 error 事件。

    修复前此处只做状态收口——``_error`` 为空且 results 为空时会被判 succeeded,
    而 SSE 侧从未发出 done 帧,页面停在「运行中/重连」,GET 却报成功;现在
    补发 error(状态=failed,GET 的 done 载荷=该事件),页面与状态一致。
    正常路径(终态事件已由 emit 原子收口)为 no-op;兜底 emit 自身失败时
    仍强制收口状态,绝不让异常逃出 finally。
    """
    with job._lock:
        need = not job.done
    if not need:
        return
    try:
        job.emit(
            _error_event(
                STRINGS["job_err_aborted.what"],
                STRINGS["job_err_aborted.why"],
                {"code": "job_aborted"},
            )
        )
    except Exception:  # noqa: BLE001 — 兜底发送失败:仍须收口状态,不可再抛
        log.exception("兜底 error 事件发送失败(job=%s)", job.id)
        with job._lock:
            if not job.done:
                job._error = _error_event(
                    STRINGS["job_err_aborted.what"],
                    STRINGS["job_err_aborted.why"],
                    {"code": "job_aborted"},
                )
                # 状态与载荷同源(评审 M3):GET 的 done 键与 error 字段一致,
                # 不出现「status=failed 而 done=None」的不对称
                job._done_payload = job._error
                job._settle_status_locked()
                job.done = True


# ---------------------------------------------------------------------------
# 版本校验(M3 收口:枚举与 PCL.ini 活跃版本读取已提取为 pipeline 公共函数,
# 与 CLI 共用同一实现——分层纪律:消费 pipeline,不 import cli 私有函数)
# ---------------------------------------------------------------------------


def _ensure_version_dirs(game_root: Path, *versions: str) -> None:
    """校验版本名形态与目录存在,非法/缺失抛 422 三段式。

    形态校验(0.12.0 复审#1):版本名必须是「单一路径分量」——修复前只查
    ``(versions/<v>).is_dir()``,pathlib 拼绝对路径整体替换,外部已存在目录
    即受理(写盘可越出 game_root);现与 CLI/核心共用
    :func:`pipeline.version_name_error` 先行拒绝,再查存在性。
    """
    invalid = {
        v: version_name_error(v) for v in versions if version_name_error(v) is not None
    }
    if invalid:
        raise ApiError(
            422,
            "err_version_bad_name.what",
            "err_version_bad_name.why",
            {"invalid": invalid, "available": list_versions(game_root)},
        )
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
                # 旧布局规则只读回退提示(终审修复 I4,spec §3.1):select_rules_dir 以同参数
                # 复算(与 build_plan 内部选择一致——同体归 None 的规范化也保持同构),
                # 提示经既有 notice 事件通道流出——不再让旧 rules.yaml 被无声忽略
                _rules_legacy = legacy_dir if legacy_dir != data_dir else None
                _chosen, rule_notices = select_rules_dir(data_dir, _rules_legacy)
                for n in rule_notices:
                    job.emit({"type": "notice", "text": n})
            job.emit({"type": "phase", "name": "scan_src"})
            scan_version(ctx.game_root, src, ctx.snapshots)
            job.emit({"type": "phase", "name": "scan_dst"})
            scan_version(ctx.game_root, dst, ctx.snapshots)
            job.emit({"type": "phase", "name": "plan"})
            # 批次F:build_plan 三元组返回;批次I-W3 T6(白名单⑦)4 元组:
            # _pairs/_extras 进 done.diff 摘要(mod 配对/兼容警示不再只落 stderr 日志)
            plan, compat_warnings, _pairs, _extras = build_plan(
                Path.cwd(),
                ctx.game_root,
                src,
                dst,
                # 批次I-T1+终审修复 I4(spec §3.1):data_dir=实例态 .mcmig 目录
                # (快照读/写锚定位——scan 后两侧快照已在此,定位语义不受影响);
                # mcmig_dir=旧布局回退位,build_plan 内经 select_rules_dir 据此做规则
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
                    # 批次I-W3 T6(spec §5.1):diff 摘要——审阅页顶部摘要条与折叠组
                    # (mod 配对/兼容警示/client_only/世界提示/检测范围说明)的数据源
                    "diff": {
                        **_extras,
                        "mod_pairs": [p.to_dict() for p in _pairs],
                        "compat_warnings": [str(w) for w in compat_warnings],
                        "guard_scope": STRINGS["review.scope_note"],
                    },
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
        # 防御性收口(修复 A3②):未产出终态事件的异常路径补发兜底 error
        _defensive_settle(job)


def _run_migrate_job(
    job: Job,
    ctx: InstanceCtx,
    src: str,
    dst: str,
    ask_yes: set[str],
    dry_run: bool,
    plan_id: str | None,
) -> None:
    """migrate job 线程体:加载已保存 plan → 执行前置检查单点 → 执行 → 回写执行状态。

    实例态路径来自 job 启动时定格的 ctx(闭包持有,批次I-T1);plan 文件缺失/损坏
    → error 事件;前置检查(批次I-T3,spec §3.3,全部不可强制):
    ① plan_id 缺失(旧客户端)→ error 提示重新生成;② plan_fingerprint ≠ plan_id
    → guards_plan_mismatch(details.blockers 恒含合成条目,W3 复审 #2);
    ③④⑤ 经 pipeline.precheck_execution 单点(批次I-W3 T1,CLI/GUI 平级消费):
    守卫 validate_review(实例/快照/规则,快照取位 find_snapshot 锚定优先+旧布局
    回退,与签发侧同构)→ 共享预检(已执行/快照过期/目录缺失/疑似占用,GUI 严格
    默认无决策放行通道——已执行计划以 plan_executed 明确阻断,即「首跑闸门」,
    GUI 无重跑通道)→ 首跑 check_action_states(源/目标状态漂移;有守卫缺校验
    材料 fail-closed);阻断 → error(details 带首错 code 与全量 blockers),
    降级警告 → warning 事件;执行 consume **同一已加载 plan 对象**(不再按名
    二次读取);执行成功(非 dry-run 且零失败且未取消)时 mark_executed + save
    (重跑保留首次 executed_at;取消属部分完成,不回写 executed_at,依赖
    identical 短路)。
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
                """审阅守卫/前置检查阻断 → 三段式 error 事件(details 带 code 与 blockers)。

                修复 A6:why 取首条阻断的**具体中文指引**(blockers[0].message,
                与 CLI 逐条呈现同源)——此前恒用「请重新生成并审阅计划」的通用
                文案,「疑似被占用」(游戏没退)等场景用户按提示重跑照旧被拦、
                无法自救;多阻断时其余条目在 details.blockers 中完整保留。
                """
                job.emit(
                    _error_event(
                        STRINGS["job_err_guards.what"],
                        blockers[0].message if blockers else STRINGS["job_err_guards.why"],
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
            # ② 计划身份:指纹失配 = 盘上计划与页面审阅的计划不是同一份 → 不可强制;
            # W3 复审 #2:合成阻断项入 blockers(details.blockers 恒非空,消费方取 [0] 安全)
            actual_fp = plan_fingerprint(plan)
            if actual_fp != plan_id:
                _guards_error(
                    "guards_plan_mismatch",
                    [PreflightBlocker("guards_plan_mismatch", STRINGS["job_err_guards.why"])],
                )
                return
            # ③④⑤ 执行前置检查单点(批次I-W3 T1):守卫 validate_review → 共享预检
            # preflight_execute → 首跑审阅状态校验,三段合一经 pipeline.precheck_execution
            # ——CLI/GUI 平级消费同一实现(行为变更白名单①:首错优先序统一为
            # 「守卫→预检→状态校验」)。快照取位改经 find_snapshot(锚定优先+旧布局
            # 回退,与签发侧同构——修复前硬拼锚定路径,旧布局快照用户假阳性
            # snapshot_changed,W3 复审 #10);GUI 走严格默认(decisions=None,三类
            # 可决策项均阻断),已执行计划以 plan_executed 阻断(首跑闸门,GUI 无
            # 重跑通道);有守卫缺校验材料 fail-closed(review_snapshot_missing)。
            # 批次I-T5:持锁重验——全部检查在实例锁内执行(spec §4.2 时序),
            # 检查通过到他方写盘的窗口被锁封死
            mig_data_dir = ctx.game_root / ".mcmig"
            mig_legacy = (
                ctx.legacy_snapshots.parent
                if ctx.legacy_snapshots is not None and ctx.legacy_snapshots.parent != mig_data_dir
                else None
            )
            outcome = precheck_execution(
                plan, ctx.game_root, src, dst, legacy_dir=mig_legacy)
            if outcome.review_missing:
                # 旧版计划(review=None):提示后继续(渐进采用,与 CLI 同文案)
                job.emit({"type": "notice", "text": STRINGS["notice.review_missing"]})
            if outcome.blockers:
                _guards_error(outcome.blockers[0].code, outcome.blockers)
                return
            for w in outcome.warnings:
                # 事件契约增补 warning 型
                job.emit({"type": "warning", "code": w.code, "message": w.message})
            total = len(plan.actions)
            index = 0

            # write-ahead journal(批次I-T6,spec §4.3):仅真实写盘路径启用——
            # dry-run 零写盘,journal 意图会让「待核对」清单失真,故不建;
            # 身份字段(批次I-W3 T3):src/dst/game_root 随 start 行落盘,
            # scan_interrupted/dismiss 据此做实例锁探针判活
            journal: JobJournal | None = None
            if not dry_run:
                journal = JobJournal(ctx.jobs, job.id, "migrate",
                                     src=src, dst=dst, game_root=str(ctx.game_root))

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

            # 状态校验:review≠None 时已由本 job ⑤(precheck_execution)完成,显式
            # 跳过内部复检(同一 plan 对象单次加载,避免双侧快照重复全量哈希);
            # review=None 的旧版计划在 ⑤ 无校验材料,按 CLI 同构语义兜底执行内部
            # 状态校验(修复 A2:cli.py:_cmd_migrate 传 validate_states=plan.review
            # is None——此前 GUI 恒 False,旧版计划在 GUI 完全无状态校验,弱于 CLI);
            # legacy_dir 同步传给执行侧(评审 I1:旧布局快照用户在校验材料取位处
            # 与 CLI 同参,漏传会让 review=None 计划在旧布局下静默跳过)
            results = execute_migration(
                plan,
                ctx.game_root / "versions" / src,
                ctx.game_root / "versions" / dst,
                ask_yes,
                dry_run=dry_run,
                progress_cb=on_file,
                validate_states=plan.review is None,
                legacy_dir=mig_legacy,
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
                # 批次I-W3 T6(spec §5.1):备份位置(自 executor.BACKUP_DIR 派生)——
                # 页面仅在 results 含 backed_up 时呈现该行(未覆盖时目录可能不存在)
                "backup_dir": str(ctx.game_root / "versions" / dst / BACKUP_DIR),
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
    except ReviewStateError as e:
        # 内部状态校验兜底阻断(review=None 旧版计划,修复 A2):与 precheck 的
        # 状态校验阻断同构呈现——首条失配文案作 why,全部失配进 details.blockers
        job.emit(
            _error_event(
                STRINGS["job_err_guards.what"],
                e.blockers[0].message if e.blockers else STRINGS["job_err_guards.why"],
                {
                    "code": "review_state_changed",
                    "blockers": [
                        {"code": b.code, "message": b.message} for b in e.blockers
                    ],
                },
            )
        )
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
        # 防御性收口(修复 A3②):未产出终态事件的异常路径补发兜底 error——
        # 早退分支的 error 事件已在 emit 内原子收口,此处仅兜底 emit 本身失败等
        _defensive_settle(job)


# ---------------------------------------------------------------------------
# swap 两阶段 job(批次I-W3 T7,spec §5.2/§8)
# ---------------------------------------------------------------------------


def _swap_backup_root(ctx: InstanceCtx) -> Path:
    """换包覆盖备份目录:<game_root>/.mcmig/backups/swap/<UTC 时间戳>。

    时间戳含微秒(同秒多次换包不共目录,保证「首份备份=目标原始值」不因
    目录复用而被跳过);目录由装包事务按需创建,此处只算路径。
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ%f")
    return ctx.game_root / ".mcmig" / "backups" / "swap" / stamp


def _run_swap_preflight_job(
    job: Job, ctx: InstanceCtx, src: str, dst: str, new_pack: Path,
    pf_store: dict[str, dict],
) -> None:
    """swap 预检 job 线程体(只读):scan src(恒重扫)→ swap_preflight → done。

    done 载荷 = SwapPreflightOutcome 序列化 + ``backup_preview``(覆盖备份
    目录预告,页面在决策步展示「覆盖将备份到哪」);预检通过时 preflight_id
    连同决策清单入指纹仓(仅保留最近 ``_SWAP_PREFLIGHT_CACHE_LIMIT`` 条),
    供 apply 持锁重验(评审 P2-3:快照内容不入指纹,预检后重扫不影响重验)。
    预检致命错(缺版本 json/缺源快照)不以 error 事件收场——done 载荷的
    error 字段携带 CLI 同款文案,页面据此禁用 apply 并展示引导。
    """
    try:
        time.sleep(_JOB_MIN_ALIVE_SECONDS)  # 同 _run_plan_job:单锁确定性
        with instance_locks(ctx.game_root, src, dst) as lockinfo:
            _notify_abandoned_lock(job, ctx, lockinfo)
            data_dir = ctx.game_root / ".mcmig"
            legacy_dir = (
                ctx.legacy_snapshots.parent if ctx.legacy_snapshots is not None else None
            )
            # 首步恒重扫 src(与 plan job 同型):GUI 用户免「先跑 scan」断崖
            job.emit({"type": "phase", "name": "scan_src"})
            scan_version(ctx.game_root, src, ctx.snapshots)
            outcome = swap_preflight(ctx.game_root, dst, new_pack, src=src,
                                     legacy_dir=legacy_dir)
            if outcome.error is None and outcome.preflight_id is not None:
                pf_store[outcome.preflight_id] = {
                    "fingerprint": outcome.preflight_id,
                    "src": src,
                    "dst": dst,
                    "new_pack": str(new_pack),
                    "game_root": str(ctx.game_root),
                    "incompat": list(outcome.incompat),
                    "extras": list(outcome.extras),
                    "conflicts": list(outcome.conflicts),
                }
                while len(pf_store) > _SWAP_PREFLIGHT_CACHE_LIMIT:
                    pf_store.pop(next(iter(pf_store)))  # 淘汰最旧(插入序)
            job.emit(
                {
                    "type": "done",
                    "job_kind": "swap_preflight",
                    "src": src,
                    "dst": dst,
                    "error": outcome.error,
                    "incompat": outcome.incompat,
                    "extras": outcome.extras,
                    "conflicts": outcome.conflicts,
                    "preflight_id": outcome.preflight_id,
                    "backup_preview": str(data_dir / "backups" / "swap"),
                }
            )
    except InstanceLockError as e:
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
    except Exception as e:  # noqa: BLE001 — job 边界统一兜底,转 error 事件给前端
        log.exception("swap preflight job 失败(%s → %s)", src, dst)
        job.emit(
            _error_event(
                STRINGS["job_err_failed.what"], str(e) or repr(e), {"error": repr(e)}
            )
        )
    finally:
        _defensive_settle(job)  # 兜底:未产出终态事件的异常路径补发 error(修复 A3②)


def _run_swap_apply_job(
    job: Job,
    ctx: InstanceCtx,
    src: str,
    dst: str,
    accept_incompat: bool,
    accept_extras: bool,
    overwrite: set[str],
    record: dict,
    new_pack: Path,
) -> None:
    """swap 装包 job 线程体(spec §5.2):持锁三重重验 → 装包 → 原子关取消窗 → 链式重规划。

    时序(全程持源/目标实例锁,批次I-T5;同进程双 job 已由 JobStore 单任务锁
    先行 409):
    1. 三重重验(评审 P2-3):① 指纹仓记录的 game_root/src/dst 与当前 job
       定格的 ctx 核对(切换游戏根后旧预检作废);② 锁内重跑 swap_preflight
       重算指纹,失配(目标 mods/ 或 <dst>.json 被改)→ error
       swap_inputs_changed,零写盘;③ 兼容/extras 重跑——accept_* 未勾选
       而清单非空 → error。
    2. 装包:swap_install(覆盖备份至 backups/swap/<时间戳>;write-ahead
       journal;逐 jar 取消检查点=job.status=="cancelling")。
    3. 取消窗原子关闭(评审 v3/v4 P2-3,白名单⑩):swap_install 返回后在
       job._lock 内一次完成「命中取消判定 + cancel_closed 置位」——命中 →
       cancelled 收尾(跳过链式规划,部分装包的计划没有审阅意义);未命中 →
       关窗后进入不可取消的重扫+规划段,封死「done 带成功计划 vs GET 收口
       cancelled」的分歧。journal.finish 取消/完成都收尾(安全边界内结束)。
    4. 链式重规划(评审 P1-2):同一 job 内重扫双侧 + build_plan(
       modpack_swap=True, rescan_dst=True)——done 载荷与 plan job 同形
       (plan/plan_id/persisted/diff)+ install/backup_dir,页面直接以
       plan_id 进②审阅页执行迁移,不存在「回步①用普通 plan 把旧包独有
       jar 重新列为可迁移」的路径。链式段失败 → error code=swap_replan_failed
       且载荷带 install 统计与 backup_dir(评审 v3 契约B:装包已成功,目标
       mods/ 已被修改,不可静默);重规划不写 journal(装包部分已双记录)。
    """
    try:
        time.sleep(_JOB_MIN_ALIVE_SECONDS)  # 同 _run_plan_job:单锁确定性
        job.emit({"type": "phase", "name": "install"})
        with instance_locks(ctx.game_root, src, dst) as lockinfo:
            _notify_abandoned_lock(job, ctx, lockinfo)
            data_dir = ctx.game_root / ".mcmig"
            legacy_dir = (
                ctx.legacy_snapshots.parent if ctx.legacy_snapshots is not None else None
            )

            def _inputs_changed(reason: str) -> None:
                """重验失配 → 统一 error 事件(swap_inputs_changed,零写盘)。"""
                job.emit(
                    {
                        "type": "error",
                        "code": "swap_inputs_changed",
                        "what": STRINGS["job_err_swap_inputs_changed.what"],
                        "why": STRINGS["job_err_swap_inputs_changed.why"],
                        "details": {"code": "swap_inputs_changed", "reason": reason},
                    }
                )

            # 重验①:仓内记录的实例身份 vs 当前 job 定格的 ctx(评审 P2-3)
            if (record["game_root"] != str(ctx.game_root)
                    or record["src"] != src or record["dst"] != dst):
                _inputs_changed("instance_mismatch")
                return
            # 重验②③:锁内重跑预检——指纹重算 + 兼容/extras 重跑(不沿用预检结论)
            current = swap_preflight(ctx.game_root, dst, new_pack, src=src,
                                     legacy_dir=legacy_dir)
            if current.error is not None:
                job.emit(
                    {
                        "type": "error",
                        "code": "swap_preflight_failed",
                        "what": STRINGS["job_err_swap_preflight.what"],
                        "why": STRINGS["job_err_swap_preflight.why"],
                        "details": {"code": "swap_preflight_failed",
                                    "error": current.error},
                    }
                )
                return
            if current.preflight_id != record["fingerprint"]:
                _inputs_changed("fingerprint_mismatch")
                return
            if current.incompat and not accept_incompat:
                job.emit(
                    {
                        "type": "error",
                        "code": "swap_incompat_blocked",
                        "what": STRINGS["job_err_swap_incompat.what"],
                        "why": STRINGS["job_err_swap_incompat.why"],
                        "details": {"code": "swap_incompat_blocked",
                                    "incompat": current.incompat},
                    }
                )
                return
            if current.extras and not accept_extras:
                job.emit(
                    {
                        "type": "error",
                        "code": "swap_extras_blocked",
                        "what": STRINGS["job_err_swap_extras.what"],
                        "why": STRINGS["job_err_swap_extras.why"],
                        "details": {"code": "swap_extras_blocked",
                                    "extras": current.extras},
                    }
                )
                return
            # 装包(覆盖备份 + write-ahead journal + 逐 jar 取消检查点)
            backup_root = _swap_backup_root(ctx)
            journal = JobJournal(ctx.jobs, job.id, "swap",
                                 src=src, dst=dst, game_root=str(ctx.game_root))

            def _should_cancel() -> bool:
                """取消检查点数据源:cancel 端点已置 cancelling 中间态。"""
                return job.status == "cancelling"

            dst_mods = ctx.game_root / "versions" / dst / "mods"
            install = swap_install(dst_mods, new_pack / "mods", overwrite=overwrite,
                                   dry_run=False, backup_root=backup_root,
                                   journal=journal, should_cancel=_should_cancel,
                                   progress_cb=lambda name, i, total: job.emit(
                                       {"type": "file", "path": name, "status": "copied",
                                        "index": i, "total": total}))
            install_payload: dict[str, object] = {
                "copied": install.copied,
                "skipped": install.skipped,
                "conflicted": install.conflicted,
                "backed_up": list(install.backed_up),
                "cancelled": install.cancelled,
            }
            if install.error is not None:
                # 装包失败(0.12.0 复审#5/#6:空间预检不过/中途文件操作失败)——
                # 不再上抛吞掉已知结果:取消窗关闭 + 尽力收尾 journal,失败事件
                # 携带部分统计(copied/backed_up)与备份位置(契约B:用户须能
                # 判断「已改动什么、原件在哪」),引导重新预检
                with job._lock:
                    job.cancel_closed = True
                try:
                    journal.finish()
                except JournalError as e:
                    log.warning("swap journal 收尾失败(装包结果仍在事件中): %s", e)
                job.emit(
                    {
                        "type": "error",
                        "code": "swap_install_failed",
                        "what": STRINGS["job_err_swap_install.what"],
                        "why": STRINGS["job_err_swap_install.why"],
                        "install": {**install_payload, "error": install.error},
                        "backup_dir": str(backup_root),
                        "details": {"code": "swap_install_failed",
                                    "error": install.error},
                    }
                )
                return
            # 装包段结束:取消窗原子判定并关闭(评审 v3 P2-3,白名单⑩)——
            # 判定与置位同一临界区,迟到取消在 cancel 端点锁内重检时必见关窗
            with job._lock:
                cancelling = job.status == "cancelling"
                if not cancelling:
                    job.cancel_closed = True
            try:
                journal.finish()  # 取消/完成都在安全边界内结束,退出中断清单
            except JournalError as e:
                # 收尾写失败(0.12.0 复审#5)只警告:装包结果已知且必须随事件
                # 返回;jobs/ 可能残留一条未收尾档案,可核对后清除
                log.warning("swap journal 收尾失败(装包结果仍在事件中): %s", e)
            if cancelling:
                # cancelled 收尾(复用 migrate 口径:已完成/未执行清单,跳过链式
                # 规划——部分装包后的计划没有审阅意义,重走 swap)
                new_mods_dir = new_pack / "mods"
                jars = sorted(new_mods_dir.glob("*.jar")) if new_mods_dir.is_dir() else []
                completed = [
                    j.name for j in jars
                    if (dst_mods / j.name).is_file()
                    and md5_of(dst_mods / j.name) == md5_of(j)
                ]
                done_set = set(completed)
                job.emit(
                    {
                        "type": "done",
                        "job_kind": "swap",
                        "src": src,
                        "dst": dst,
                        "cancelled": True,
                        "install": install_payload,
                        "backup_dir": str(backup_root),
                        "completed": completed,
                        "pending": [j.name for j in jars if j.name not in done_set],
                    }
                )
                return
            # 链式重规划(评审 P1-2):同一 job 内重扫双侧 + build_plan
            # (modpack_swap=True——旧包独有 jar 归换包排除,不回迁)
            try:
                job.emit({"type": "phase", "name": "replan"})
                scan_version(ctx.game_root, src, ctx.snapshots)
                scan_version(ctx.game_root, dst, ctx.snapshots)
                plan, compat_warnings, _pairs, _extras = build_plan(
                    Path.cwd(),
                    ctx.game_root,
                    src,
                    dst,
                    modpack_swap=True,
                    rescan_dst=True,
                    mcmig_dir=legacy_dir if legacy_dir is not None else data_dir,
                    data_dir=data_dir,
                    plans_dir=ctx.plans,
                )
            except Exception as e:  # noqa: BLE001 — 装包已成功,失败必须带装包状态(契约B)
                log.exception("swap 链式重规划失败(%s → %s)", src, dst)
                job.emit(
                    {
                        "type": "error",
                        "code": "swap_replan_failed",
                        "what": STRINGS["job_err_swap_replan.what"],
                        "why": STRINGS["job_err_swap_replan.why"],
                        "install": install_payload,
                        "backup_dir": str(backup_root),
                        "details": {"code": "swap_replan_failed", "error": str(e)},
                    }
                )
                return
            for w in compat_warnings:
                log.warning("[兼容警告] %s", w)
            job.emit(
                {
                    "type": "done",
                    "job_kind": "swap",
                    "src": src,
                    "dst": dst,
                    "install": install_payload,
                    "backup_dir": str(backup_root),
                    "plan": _group_actions(plan),
                    "plan_id": plan_fingerprint(plan),
                    "persisted": True,
                    "diff": {
                        **_extras,
                        "mod_pairs": [p.to_dict() for p in _pairs],
                        "compat_warnings": [str(w) for w in compat_warnings],
                        "guard_scope": STRINGS["review.scope_note"],
                    },
                }
            )
    except InstanceLockError as e:
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
        # journal 写入/收尾失败:装包结果不确定,按失败呈现;盘上 journal 未
        # 收尾,待核对条目由 interrupted 呈现
        job.emit(
            _error_event(
                STRINGS["job_err_journal.what"],
                STRINGS["job_err_journal.why"],
                {"error": str(e)},
            )
        )
    except Exception as e:  # noqa: BLE001 — job 边界统一兜底,转 error 事件给前端
        log.exception("swap apply job 失败(%s → %s)", src, dst)
        job.emit(
            _error_event(
                STRINGS["job_err_failed.what"], str(e) or repr(e), {"error": repr(e)}
            )
        )
    finally:
        _defensive_settle(job)  # 兜底:未产出终态事件的异常路径补发 error(修复 A3②)


# ---------------------------------------------------------------------------
# SSE 生成器与页面
# ---------------------------------------------------------------------------


def _sse_frame(ev: dict) -> str:
    """构造单帧 SSE:``id:`` 行携带 seq(客户端断线重连凭据),``data:`` 为事件 JSON。"""
    return f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"


async def _pump_sub(job: Job, sub: _Sub, *, reset: bool, replay: list[dict]) -> AsyncIterator[str]:
    """泵送单个订阅者:重放段 → 活段轮询,终态/溢出即收尾(自 _sse_gen 主体搬移)。

    顺序:①``reset`` 为真(历史已截断,last_seq 早于 floor)→ 先发
    ``{"type":"reset"}``(无 id,客户端须重读 GET /api/jobs/{id} 重建状态);
    ②重放历史尾(``seq > last_seq``,重放段已含终态则直接收尾);③活段
    ``queue.get(0.5s)`` 轮询,done/error 帧后断流;醒来发现 ``job.done`` 或
    ``sub.dropped`` 即收尾(W3 T2 P2-5/#18:被摘订阅者不再空转到 job 收尾)。
    循环收尾时 ``sub.dropped`` 为真且未发过终态帧 → 补发一帧
    ``data: {"type":"overflow"}``(无 id)再 return——客户端据此重读 GET
    状态并以 Last-Event-ID 重订。

    async 化(0.12.0 复审#7):同步泵的 ``queue.get`` 阻塞线程池线程,客户端
    断开时任务取消传不进同步迭代器,``_sse_gen`` 的 finally 注销要等下一
    事件/终态;``asyncio.to_thread`` 拉取把等待点放回事件循环——取消在
    await 处即时生效(被弃线程 ≤0.5s 自然结束,结果丢弃)。
    """
    if reset:
        yield f"data: {json.dumps({'type': 'reset'}, ensure_ascii=False)}\n\n"
    for ev in replay:
        yield _sse_frame(ev)
        if ev.get("type") in ("done", "error"):
            return
    while True:
        try:
            ev = await asyncio.to_thread(sub.queue.get, True, 0.5)
        except queue.Empty:
            if job.done or sub.dropped:
                break
            continue
        yield _sse_frame(ev)
        if ev.get("type") in ("done", "error"):
            break
        if sub.dropped:
            break
    if sub.dropped:
        # 溢出信号帧(无 id):本帧后流即断开,客户端重读状态端点并重订
        yield f"data: {json.dumps({'type': 'overflow'}, ensure_ascii=False)}\n\n"


async def _sse_gen(job: Job, last_seq: int = 0) -> AsyncIterator[str]:
    """把 job 事件翻译为 SSE 帧;job 结束即收尾断流(衔接协议,批次I-T4)。

    薄壳(W3 T2,可测性拆层):subscribe → 委托 ``_pump_sub`` 泵送 →
    finally unsubscribe(流收尾/**客户端断开取消**时注销订阅者;端点签名与
    调用方不变)。
    """
    reset, replay, sub = job.subscribe(last_seq)
    try:
        async for frame in _pump_sub(job, sub, reset=reset, replay=replay):
            yield frame
    finally:
        job.unsubscribe(sub)


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


class SwapPreflightRequest(BaseModel):
    """POST /api/swap/preflight 请求体(换包两阶段第一步,只读)。"""

    src: str
    dst: str
    new_pack: str = Field(min_length=1)


class SwapApplyRequest(BaseModel):
    """POST /api/swap/apply 请求体(spec §5.2 端点;决策以 preflight_id 携带)。

    preflight_id 为预检指纹(只读预检 job 的 done 载荷);apply 时服务端持锁
    重算指纹核对,失配 → error 事件 swap_inputs_changed(零写盘)。
    accept_incompat/accept_extras 为用户对预检清单的显式接受;overwrite_jars
    为选中覆盖的冲突 jar 名集合(须 ⊆ 预检 conflicts,越界 → 422)。
    """

    preflight_id: str = Field(min_length=1)
    src: str
    dst: str
    accept_incompat: bool = False
    accept_extras: bool = False
    overwrite_jars: list[str] = Field(default_factory=list)

class UpdateOpenRequest(BaseModel):
    """打开暂存位置请求(资产路径来自 done 载荷;模块级定义——FastAPI 在
    ``from __future__ import annotations`` 下无法解析函数内局部类的注解)。"""

    path: str



# ---------------------------------------------------------------------------
# app 工厂
# ---------------------------------------------------------------------------



def _update_staging_root() -> Path:
    """GUI 暂存根(spec §4.3.1 两端分治):冻结 exe 旁 data/update-staging,不回退 TEMP。

    GUI 绿色模式 workdir 前置校验 exe/data 可写,到达更新面板必然可写;
    不可写属致命路径(app.py 消息框契约),不走 TEMP 兜底。
    """
    from .. import updater

    return updater.staging_root_at(Path(sys.executable).parent)


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
    # swap 预检指纹仓(批次I-W3 T7):preflight_id → 指纹与决策清单,apply 持锁
    # 重验用;仅保留最近 _SWAP_PREFLIGHT_CACHE_LIMIT 条(插入序淘汰最旧)
    app.state.swap_preflights: dict[str, dict] = {}
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

    def _job_or_404(job_id: str) -> Job:
        """按 id 取 job:形态白名单先行,未知/非法一律 404(修复 A1,统一输入面)。

        状态/事件/取消三个端点共用;形态非法(含路径分隔符/穿越形态)与
        「不存在」对外不可区分,均按未知 job 应答。
        """
        job = store.get(job_id) if _valid_job_id(job_id) else None
        if job is None:
            raise ApiError(404, "err_job_not_found.what", "err_job_not_found.why")
        return job

    @app.middleware("http")
    async def request_guard(
        request: Request, call_next: Callable[[Request], object]
    ) -> object:
        """请求来源守卫(防 DNS rebinding 与浏览器 CSRF),两级校验:

        ①Host 头(所有请求):仅放行本机回环主机名,其余 403。端口不参与
        校验(启动侧绑定 127.0.0.1,端口由启动方控制)。
        ②写请求(POST):0.12.0 复审#11——Host 由浏览器按目标生成、对 CSRF
        恒过,须再校验 **Origin 白名单** 与 **JSON Content-Type**:
        Origin 存在时其主机必须同为回环白名单(浏览器不可伪造该头;跨源
        fetch 的 ``application/json`` 会先被 CORS 预检拦下,而 no-cors 只能发
        text/plain 等简单类型);无 Origin(非浏览器工具如 curl)放行,只查
        Content-Type。GET(SSE/状态查询)只读,不设第二级。
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
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("origin", "")
            if origin:
                origin_host = (urlsplit(origin).hostname or "").strip("[]").lower()
                if origin_host not in _ALLOWED_HOSTS:
                    return _three_part_response(
                        403,
                        STRINGS["err_origin.what"],
                        STRINGS["err_origin.why"],
                        {"origin": origin},
                    )
            ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return _three_part_response(
                    415,
                    STRINGS["err_content_type.what"],
                    STRINGS["err_content_type.why"],
                    {"content_type": request.headers.get("content-type", "")},
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

    def _start_job(kind: str, runner: Callable[[Job], None]) -> Job:
        """注册并启动 job:排空态 → 503;单锁占用 → 返回 None(调用方 409)。

        draining 判定在 JobStore.start() 的临界区内首判(批次I-W3 T9,评审
        v4 P2-2,白名单⑪):端点不做锁外预检——「端点过检 → 关窗 → start
        照常注册」的交错不存在;plan/migrate/swap preflight/apply 四个 job
        创建入口统一经此接线。
        """
        try:
            return store.start(kind, runner)
        except StoreDraining:
            raise ApiError(503, "err_shutting_down.what", "err_shutting_down.why")

    @app.post("/api/plan")
    def api_plan(req: PlanRequest) -> dict[str, str]:
        """启动 plan job(scan×2 → plan),返回 job_id;单锁占用时 409。

        实例上下文在 job 启动时一次性定格(批次I-T1):运行中改配置
        只影响后续 job,本 job 全程使用启动时的实例态路径。
        """
        ctx = _instance_ctx(app.state.wdir)
        _ensure_version_dirs(ctx.game_root, req.src, req.dst)
        job = _start_job("plan", lambda j: _run_plan_job(j, ctx, req.src, req.dst))
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
        job = _start_job(
            "migrate",
            lambda j: _run_migrate_job(
                j, ctx, req.src, req.dst, ask_yes, req.dry_run, req.plan_id
            ),
        )
        if job is None:
            raise ApiError(409, "err_job_busy.what", "err_job_busy.why")
        return {"job_id": job.id}

    @app.post("/api/swap/preflight")
    def api_swap_preflight(req: SwapPreflightRequest) -> dict[str, str]:
        """启动 swap 预检 job(只读:scan src → 预检),返回 job_id;单锁占用 409。

        换包两阶段第一步(批次I-W3 T7,spec §5.2):done 载荷携带
        incompat/extras/conflicts 三类决策清单与 preflight_id(输入指纹);
        首步恒重扫 src——GUI 用户不必先跑 scan/plan。实例上下文同 plan:
        job 启动时定格(批次I-T1)。
        """
        ctx = _instance_ctx(app.state.wdir)
        _ensure_version_dirs(ctx.game_root, req.src, req.dst)
        new_pack = Path(req.new_pack.strip())
        if not (new_pack / "mods").is_dir():
            raise ApiError(
                422,
                "err_swap_bad_pack.what",
                "err_swap_bad_pack.why",
                {"new_pack": req.new_pack},
            )
        job = _start_job(
            "swap_preflight",
            lambda j: _run_swap_preflight_job(
                j, ctx, req.src, req.dst, new_pack, app.state.swap_preflights
            ),
        )
        if job is None:
            raise ApiError(409, "err_job_busy.what", "err_job_busy.why")
        return {"job_id": job.id}

    @app.post("/api/swap/apply")
    def api_swap_apply(req: SwapApplyRequest) -> dict[str, str]:
        """启动 swap 装包 job(三重重验 → 装包 → 链式重规划),返回 job_id。

        先决(不建 job,评审 P2-3):指纹仓无此 preflight_id → 422(过期/未知);
        请求版本对与仓内记录不符 → 422;overwrite_jars ⊄ 预检 conflicts → 422。
        重验①(实例身份)与②③(指纹/兼容重跑)在 job 内持实例锁后进行。
        实例上下文同 plan:job 启动时定格(批次I-T1)。
        """
        ctx = _instance_ctx(app.state.wdir)
        _ensure_version_dirs(ctx.game_root, req.src, req.dst)
        record = app.state.swap_preflights.get(req.preflight_id)
        if record is None:
            raise ApiError(
                422,
                "err_swap_preflight_unknown.what",
                "err_swap_preflight_unknown.why",
                {"preflight_id": req.preflight_id},
            )
        if record["src"] != req.src or record["dst"] != req.dst:
            raise ApiError(
                422,
                "err_swap_preflight_unknown.what",
                "err_swap_preflight_unknown.why",
                {"preflight_id": req.preflight_id,
                 "record": f'{record["src"]} → {record["dst"]}'},
            )
        out_of_range = sorted(set(req.overwrite_jars) - set(record["conflicts"]))
        if out_of_range:
            # overwrite_jars 必须 ⊆ 预检冲突清单(越界 422,评审 P2-3)
            raise ApiError(
                422,
                "err_swap_overwrite.what",
                "err_swap_overwrite.why",
                {"out_of_range": out_of_range},
            )
        overwrite = set(req.overwrite_jars)
        new_pack = Path(str(record["new_pack"]))
        job = _start_job(
            "swap",
            lambda j: _run_swap_apply_job(
                j, ctx, req.src, req.dst, req.accept_incompat, req.accept_extras,
                overwrite, record, new_pack,
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
        """中断清单:未收尾 journal 的待核对条目(spec §4.3;W3-T3 判定重构)。

        请求期现扫(而非只读 create_app 时的快照):服务长驻,启动后新落下
        的中断记录同样可见;未配置 game_root(欢迎态)或目录不存在 → 空清单。
        判定(批次I-W3 T3):未收尾(finish 未标记)即报;条目携带身份字段
        (src/dst/game_root)与 unknown_progress(无待核对条目=整体进度未知,
        P2-8),由页面横幅消费。活性双层:同进程在跑 migrate job 的 journal 按
        id 剔除(快路径,W2.5 复审 B4);scan_interrupted 内的实例锁探针对带
        身份的 journal 做跨进程判活(CLI 正在跑 → 不报)。待核对 ≠ cancelled
        ——只陈述「这条意图没有完成记录」,结局须人工核对。
        """
        jobs_dir = app.state.wdir.jobs
        items = scan_interrupted(jobs_dir) if jobs_dir is not None else []
        active = store.active_id()
        if active is not None:
            items = [i for i in items if i.get("job_id") != active]
        return {"items": items}

    @app.post("/api/jobs/interrupted/{job_id}/dismiss")
    def api_jobs_interrupted_dismiss(job_id: str) -> dict[str, bool]:
        """清除一条中断记录:确认前任已死后删除 journal 文件(#9 清除通道)。

        评审 P2-8:只查文件存在就删,活任务(CLI/GUI 正在写)的 journal 会被
        删档——其后续追加会重建出缺 start 与先前意图的残缺文件,恢复证据被
        削弱。删除前重读该 journal 并探针判活(与 scan_interrupted 同一单点
        journal._journal_owner_alive):锁被持=活 → 409,不删;文件不存在 → 404。
        """
        jobs_dir = app.state.wdir.jobs
        if jobs_dir is None:
            raise ApiError(422, "err_no_game_root.what", "err_no_game_root.why")
        if not _valid_job_id(job_id):
            # 形态白名单(修复 A1,安全):修复前直接拼接文件名,Windows 下
            # URL 的 %5C 解码出的 \ 被 pathlib 当分隔符 → 可删除 jobs/ 之外
            # 任意 .json/.jsonl(含 versions/<ver>.json);非法形态按未知记录 404
            raise ApiError(404, "err_job_not_found.what", "err_job_not_found.why")
        jobs_root = jobs_dir.resolve()
        for suffix in (".jsonl", ".json"):
            p = jobs_dir / f"{job_id}{suffix}"
            # 双保险(修复 A1):解析后仍须落在 jobs/ 内——即便形态校验被绕过
            # 也不得触达目录外文件;越界视同不存在
            if p.resolve().parent != jobs_root:
                continue
            if p.exists():
                try:
                    journal = JobJournal(jobs_dir, job_id, "")
                except JournalError:
                    continue          # 损坏档案视同可清除(读不出即无恢复价值)
                if _journal_owner_alive(journal):
                    raise ApiError(409, "err_dismiss_live.what", "err_dismiss_live.why")
                try:
                    p.unlink()
                except FileNotFoundError:
                    # 竞态兜底:exists() 与 unlink() 之间被并发 dismiss(另一
                    # 标签页/进程)抢先删除——清除目标已达成,按幂等成功收场,
                    # 不虚构「无法删除」冲突误导用户去删一个已不在的文件
                    return {"ok": True}
                except OSError as e:
                    # 兜底(评审 M1 补):设备保留名变体/句柄占用等仍可能令删除
                    # 失败——不得以 500 收场;按冲突呈现并提示手动删除
                    log.warning("中断记录删除失败 %s: %s", p, e)
                    raise ApiError(
                        409, "err_dismiss_failed.what", "err_dismiss_failed.why"
                    ) from e
                return {"ok": True}
        raise ApiError(404, "err_job_not_found.what", "err_job_not_found.why")

    @app.get("/api/jobs/{job_id}")
    def api_job_status(job_id: str) -> dict[str, object]:
        """查询 job 状态快照(spec §4.1 终端展示/断线重连后重建状态的唯一事实源)。

        返回 ``{status, kind, phase, revision, progress, summary, results, error}``;
        results 含逐文件明细(path/status/backed_up/failed/error),非仅计数。
        """
        job = _job_or_404(job_id)
        return job.snapshot()

    @app.get("/api/jobs/{job_id}/events")
    def api_job_events(
        job_id: str, request: Request, last_event_id: str | None = None
    ) -> StreamingResponse:
        """SSE 进度流:phase/notice/warning/file/done/error 事件,job 结束即收尾断流。

        衔接协议(批次I-T4;游标契约 W3 T2 对齐):``Last-Event-ID`` 请求头或
        ``?last_event_id=N`` query 续订,优先级 = **有效请求头 > query 参数 > 0**
        (非法整数按缺省处理,消除 422 路径)。头优先的理由(WHATWG SSE 重连
        语义):浏览器自动重连回发**最新**游标的 header,而 URL(含残留旧
        query)原样重用——query 优先会让带旧游标的显式恢复连接在每次自动
        重连时反复从旧位置重放/触发 reset;query 仅服务「显式恢复的首次
        请求」(新建 EventSource 首次请求无 header)。每帧附 ``id: <seq>`` 行;
        历史截断时首帧 ``{"type":"reset"}``;订阅者队列溢出被摘时末帧
        ``{"type":"overflow"}``(客户端重读状态端点并重订)。
        """
        job = _job_or_404(job_id)
        # 游标解析:候选按优先级排列(头 > query),首个可解析为整数的生效;
        # 全部缺失/非法 → 0(从头重放)
        last_seq = 0
        for raw in (request.headers.get("last-event-id"), last_event_id):
            if raw is None:
                continue
            try:
                last_seq = int(raw)
                break
            except ValueError:
                continue
        return StreamingResponse(
            _sse_gen(job, last_seq),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.post("/api/jobs/{job_id}/cancel", status_code=202)
    def api_job_cancel(job_id: str) -> dict[str, str]:
        """取消迁移/换包装包 job:置 cancelling 中间态,执行侧取消检查点命中后停发(202)。

        契约(批次I-T6,spec §4.3;W3-T7 扩 swap+取消窗):plan/preflight job
        不可取消 → 405 三段式(无假按钮语义);终态 job → 409(无可取消对象);
        swap job 装包段结束关窗后 → 409 err_cancel_closed(白名单⑩:重扫+规划
        段不可取消,封死「done 带成功计划 vs GET 收口 cancelled」分歧);未知
        job → 404。取消只在动作间检查点生效——当前文件单元完成后才停,响应
        202 即「已受理」而非「已停」,终态经 GET /api/jobs/{id} 轮询/SSE done
        事件(cancelled:true)确认。

        **全部判定在 job._lock 内**(W3-T7,评审 v4 P2-3):锁外读
        can_cancel/cancel_closed 是 TOCTOU——迟到取消会在锁外读到允许→装包
        段关窗→进锁置 cancelling,重现「规划成功却收口 cancelled」的交错;
        锁内序 done/_settled → 409,cancel_closed → 409,not can_cancel →
        405,否则置 cancelling(幂等 202)。与既有 _settled 锁内守卫(终审
        修复 I2)同构。
        """
        job = _job_or_404(job_id)
        with job._lock:
            # 终审修复 I2:done 已收尾**或终态取消标志已单次判定(settle)**都拒绝——
            # 后者是「job 体刚判定成功、done 事件尚未发出」的窗口,此时受理取消会让
            # done 事件/executed_at/journal 与 GET status 自相矛盾
            if job.done or job._settled:
                raise ApiError(409, "err_cancel_terminal.what", "err_cancel_terminal.why")
            if job.cancel_closed:
                raise ApiError(
                    409, "err_cancel_closed.what", "err_cancel_closed.why"
                )
            if not job.can_cancel:
                raise ApiError(
                    405, "err_cancel_unsupported.what", "err_cancel_unsupported.why"
                )
            if job.status != "cancelling":
                job.status = "cancelling"  # 幂等:重复取消仍 202
        return {"status": "cancelling"}

    @app.post("/api/update/check")
    def api_update_check() -> dict[str, object]:
        """检查更新(同步,量级=HTTP 超时 10s):三态 newer/latest/error + 源码模式。"""
        from .. import _form, updater

        current = updater.local_version()
        if _form.FORM == "source":
            return {"current": current, "mode": "source", "form": _form.FORM, "newer": None,
                    "latest": None, "asset_name": None, "size": None, "error": None}
        try:
            rel = updater.fetch_latest_release()
        except updater.UpdateError as e:
            return {"current": current, "mode": "frozen", "form": _form.FORM, "newer": None,
                    "latest": None, "asset_name": None, "size": None,
                    "error": {"what": e.what, "why": e.why}}
        if rel is None or not updater.is_newer(rel.tag, current):
            latest = None if rel is None else rel.tag.lstrip("vV")
            return {"current": current, "mode": "frozen", "form": _form.FORM,
                    "newer": False, "latest": latest,
                    "asset_name": None, "size": None, "error": None}
        asset = updater.pick_asset(rel.assets)
        return {"current": current, "mode": "frozen", "form": _form.FORM, "newer": True,
                "latest": rel.tag.lstrip("vV"),
                "asset_name": asset.name if asset else None,
                "size": asset.size if asset else None, "error": None}

    @app.post("/api/update/download", status_code=202)
    def api_update_download() -> dict[str, str]:
        """启动 update job(可取消):下载+校验+暂存;单锁 409;源码模式 400。"""
        from .. import _form, updater

        if _form.FORM == "source":
            raise ApiError(400, "err_update_source.what", "err_update_source.why")

        def _run(job: Job) -> None:
            last = 0

            def _progress(received: int, total: int) -> None:
                nonlocal last
                if received - last >= 1024 * 1024 or received == total:  # 兆级节流
                    last = received
                    job.emit({"type": "progress", "received": received, "total": total})

            try:
                try:
                    staging = _update_staging_root()
                    plan = updater.plan_update(staging, progress_cb=_progress,
                                               should_cancel=lambda: job.status == "cancelling")
                except updater.UpdateCancelled:
                    job.emit({"type": "done", "job_kind": "update", "cancelled": True})
                    return
                except updater.UpdateError as e:
                    job.emit({"type": "error", "job_kind": "update", "code": "update_failed",
                              "what": e.what, "why": e.why,
                              "details": {"code": "update_failed"}})
                    return
                except Exception as e:  # noqa: BLE001 — job 边界统一兜底(评审② C1:
                    # 未预期异常不得让 job 永挂 running、单锁永久占用)
                    log.exception("update job 失败")
                    job.emit({"type": "error", "job_kind": "update", "code": "update_failed",
                              "what": STRINGS["job_err_update.what"],
                              "why": f"{e.__class__.__name__}: {e}",
                              "details": {"code": "update_failed", "error": repr(e)}})
                    return
                # 终态取消标志单次判定(评审② I3,与 migrate I2 同规):取消落在
                # 「校验完成→收尾」窗口 → 清理本次暂存并收口 cancelled,绝不出现
                # done 带成功产物而 GET 报 cancelled 的矛盾
                if job.settle():
                    if plan is not None:
                        shutil.rmtree(plan.path.parent, ignore_errors=True)  # 仅本任务 uuid 子目录
                    job.emit({"type": "done", "job_kind": "update", "cancelled": True})
                    return
                if plan is None:
                    job.emit({"type": "done", "job_kind": "update", "no_update": True})
                    return
                job.emit({"type": "done", "job_kind": "update", "version": plan.version,
                          "asset_name": plan.asset_name, "path": str(plan.path),
                          "size": plan.size, "sha256": plan.sha256,
                          "form": _form.FORM})
            finally:
                # 防御性收口(修复 A3② 同规,评审② C1):未产出终态事件即退出时
                # 补发兜底 error,绝不让异常路径把 job 留在 running
                _defensive_settle(job)

        job = _start_job("update", _run)
        if job is None:
            raise ApiError(409, "err_job_busy.what", "err_job_busy.why")
        return {"job_id": job.id}

    @app.post("/api/update/open-location")
    def api_update_open_location(req: UpdateOpenRequest) -> dict[str, object]:
        """打开暂存位置(Explorer 选中;spec §4.3.4:绝不 startfile 资产本体)。"""
        target = Path(req.path).resolve()
        root = _update_staging_root().resolve()
        if not target.is_relative_to(root):
            raise ApiError(400, "err_update_open_outside.what", "err_update_open_outside.why")
        # explorer /select 只接受反斜杠
        subprocess.run(["explorer", "/select,", str(target)], check=False)
        return {"ok": True}

    @app.post("/api/shutdown")
    def api_shutdown() -> dict[str, bool]:
        """退出服务(批次I-T6;修复 A5:经 ``begin_shutdown`` 原子排空)。

        空闲 → 锁内一次完成「检查空闲 + 置排空(draining)」再置停机标志——
        此后新任务在 ``JobStore.start`` 同一临界区被拒(StoreDraining →
        503),封死「检查为空闲 → 新迁移启动 → 进程退出」竞争窗(与窗口
        关闭路径同一原语);忙 → 409 三段式(details 附任务描述)。

        进程退出由启动方消费 ``shutdown_requested`` 执行:窗口模式为
        gui/app.py 的停机轮询,浏览器模式为 cli._cmd_gui 的主循环轮询。
        """
        blocked, task = store.begin_shutdown()
        if blocked:
            raise ApiError(
                409,
                "err_shutdown_busy.what",
                "err_shutdown_busy.why",
                {"task": task},
            )
        app.state.shutdown_requested = True
        return {"ok": True}

    return app
