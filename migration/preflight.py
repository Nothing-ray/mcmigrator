"""共享执行预检:四道防护×显式决策矩阵(spec §3.2 阻断分类)。

自 cli._cmd_migrate 防护段与 cli._game_running 下沉(批次I-T2):
- executed / stale / 疑似占用 三道检查各对应一项 ``ExecutionDecisions`` 显式决策
  (决策放行 → 降级为 PreflightWarning;未决策 → PreflightBlocker);
- 目录缺失一道**永不可强制**(无降级通道,spec §3.2);
- CLI 与 GUI 平级消费:CLI 的 ``--force`` 映射为三项决策全 True,GUI 走严格
  默认(decisions=None);pipeline 头部重导出本模块全部符号
  (``pipeline.preflight_execute`` 命名成立,供 T3/T5/GUI 消费)。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .plan import MigrationPlan

# 文案常量:前三条与下沉前 CLI 输出逐字一致([错误]/[警告] 前缀由调用方补);
# game_maybe_running 按白名单④改用「疑似」措辞——探测无法证明占用,只称疑似
MSG_PLAN_EXECUTED = "该计划已执行(时间 {executed_at})。重跑请加 --force(可重入:已完成文件会自动跳过)。"
MSG_SNAPSHOT_STALE = "快照比计划新,计划可能过期。请重跑 plan,或 --force 强制执行。"
MSG_VERSION_DIR_MISSING = "源/目标版本文件夹不存在"
MSG_MAYBE_RUNNING = "目标版本文件疑似被占用,游戏可能仍在运行;继续可能损坏存档。请先退出源与目标实例。"


@dataclass(frozen=True)
class ExecutionDecisions:
    """执行预检的三项显式决策(调用方逐项表态,不捆绑)。

    Attributes:
        rerun_executed: 已执行计划的明确重跑。
        accept_stale: 快照过期仍执行。
        accept_maybe_running: 疑似占用仍执行(独立决策)。
    """

    rerun_executed: bool = False
    accept_stale: bool = False
    accept_maybe_running: bool = False


@dataclass(frozen=True)
class PreflightBlocker:
    """阻断项:列表非空时调用方不得执行迁移。

    Attributes:
        code: 机器码(plan_executed|snapshot_stale|version_dir_missing|game_maybe_running)。
        message: 中文文案(MSG_* 常量,可直接展示)。
    """

    code: str
    message: str


@dataclass(frozen=True)
class PreflightWarning:
    """降级提示:决策放行后的非阻断提醒(与被放行的阻断项同 code)。"""

    code: str
    message: str


def probe_maybe_running(dst_root: Path) -> bool:
    """疑似占用探测:尝试读写打开 usercache.json/options.txt。

    局限(刻意明示,不称「检测运行中游戏」): 权限错误可能误报;文件未锁不证明
    游戏未运行。调用方文案一律「疑似占用」(spec §3.2)。
    """
    for name in ("usercache.json", "options.txt"):
        p = dst_root / name
        if p.exists():
            try:
                with p.open("r+b"):
                    pass
            except OSError:
                return True
    return False


def _find_plan_file(data_dir: Path, legacy_dir: Path | None, src: str, dst: str) -> Path:
    """定位 plan 文件:锚定目录优先+旧布局回退(同 find_snapshot 语义;仅取 mtime,不加载)。

    Args:
        data_dir: 生成物锚定 .mcmig 目录(plan 在 <data_dir>/plans/ 下)。
        legacy_dir: 旧布局 .mcmig 目录;None 或与 data_dir 相同表示无回退。
        src: 源版本名。
        dst: 目标版本名。

    Returns:
        plan 文件路径(两侧均不存在时返回锚定路径,调用方按不存在处理)。
    """
    anchored = data_dir / "plans" / f"{src}__{dst}.plan.json"
    if anchored.exists() or legacy_dir is None or legacy_dir == data_dir:
        return anchored
    legacy = legacy_dir / "plans" / f"{src}__{dst}.plan.json"
    return legacy if legacy.exists() else anchored


def preflight_execute(
    plan: MigrationPlan,
    game_root: Path,
    src: str,
    dst: str,
    *,
    decisions: ExecutionDecisions | None = None,
    data_dir: Path | None = None,
    legacy_dir: Path | None = None,
) -> tuple[list[PreflightBlocker], list[PreflightWarning]]:
    """执行前四道防护(spec §3.2 阻断分类),返回 (阻断列表, 警告列表)。

    检查序列(阻断按此固定序回报,CLI 取首条即退出;与下沉前 CLI 触发顺序
    一致,仅目录缺失从第三位提前到首位——它永不可强制,理应最先报):
    1. ``version_dir_missing`` —— 源/目标版本文件夹缺失,永不可强制(无降级通道);
    2. ``plan_executed`` —— 计划已执行(executed_at 取内存对象),按 rerun_executed 降级;
    3. ``snapshot_stale`` —— 快照文件 mtime > plan 文件 mtime(快照定位
       find_snapshot 锚定优先+旧布局回退,存在才比,语义与下沉前 CLI 一致),
       按 accept_stale 降级;
    4. ``game_maybe_running`` —— probe_maybe_running 疑似占用,按 accept_maybe_running 降级。

    Args:
        plan: 已加载的迁移计划(executed_at 以内存对象为准)。
        game_root: 游戏根目录(含 versions/)。
        src: 源版本名。
        dst: 目标版本名。
        decisions: 三项显式决策;None 等于全 False(严格防护,GUI 默认)。
        data_dir: 生成物锚定 .mcmig 目录(快照/plan 定位基准);None → game_root/.mcmig。
        legacy_dir: 旧布局 .mcmig 目录(快照/plan 回退位);None 表示无回退。

    Returns:
        (阻断列表, 警告列表):阻断非空时不得执行;警告为决策放行后的提示
        (与被放行的检查项同 code,便于调用方按 code 呈现)。
    """
    dec = decisions or ExecutionDecisions()
    base = data_dir if data_dir is not None else game_root / ".mcmig"
    blockers: list[PreflightBlocker] = []
    warnings: list[PreflightWarning] = []
    # ① 目录缺失:永不可强制(即使三项决策全 True)
    src_root = game_root / "versions" / src
    dst_root = game_root / "versions" / dst
    if not src_root.is_dir() or not dst_root.is_dir():
        blockers.append(PreflightBlocker("version_dir_missing", MSG_VERSION_DIR_MISSING))
    # ② 已执行:按 rerun_executed 决策
    if plan.executed_at:
        msg = MSG_PLAN_EXECUTED.format(executed_at=plan.executed_at)
        if dec.rerun_executed:
            warnings.append(PreflightWarning("plan_executed", msg))
        else:
            blockers.append(PreflightBlocker("plan_executed", msg))
    # ③ 快照过期:plan 文件在盘才可比 mtime(内存对象无时间锚);快照存在才比
    from .pipeline import find_snapshot  # 延迟导入避免循环(pipeline 头部重导出本模块)

    p_file = _find_plan_file(base, legacy_dir, src, dst)
    if p_file.exists():
        plan_mtime = p_file.stat().st_mtime
        stale = any(
            p.exists() and p.stat().st_mtime > plan_mtime
            for p in (find_snapshot(base, legacy_dir, src)[0],
                      find_snapshot(base, legacy_dir, dst)[0])
        )
        if stale:
            if dec.accept_stale:
                warnings.append(PreflightWarning("snapshot_stale", MSG_SNAPSHOT_STALE))
            else:
                blockers.append(PreflightBlocker("snapshot_stale", MSG_SNAPSHOT_STALE))
    # ④ 疑似占用:按 accept_maybe_running 决策(dst 目录缺失时探测自然为 False)
    if probe_maybe_running(dst_root):
        if dec.accept_maybe_running:
            warnings.append(PreflightWarning("game_maybe_running", MSG_MAYBE_RUNNING))
        else:
            blockers.append(PreflightBlocker("game_maybe_running", MSG_MAYBE_RUNNING))
    return blockers, warnings
