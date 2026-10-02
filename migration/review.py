"""审阅有效性:计划身份(plan_fingerprint)与执行前置条件(审阅守卫)分离(spec §3.3)。

两套机制各答一问,互不越界:
- ``plan_fingerprint`` 回答「这份计划文件的内容是什么」——对规范化的动作定义哈希,
  剔除 executed_at/execution_summary/tool_version(运行记录≠动作定义);
- 审阅守卫(``issue_review``/``validate_review``)与审阅状态校验(``check_action_states``)
  回答「审阅时看到的世界是否仍是执行时的世界」——实例身份/双侧快照指纹/规则指纹
  逐项比对,动作路径的源/目标当前状态 vs 快照记录状态逐条比对,失配即阻断并要求
  重新审阅。复制完整性校验(executor 的 identical 短路/MD5)不能替代本层
  (它只回答「当前两侧是否相同」,不回答「是否仍与审阅时相同」)。

重跑豁免不在此层:由调用方(pipeline/GUI/CLI)在 rerun_executed 明确决策下
跳过 check_action_states,依赖 identical 短路与 job journal(spec §3.3 v4 补注)。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from .fsops import md5_of
from .plan import ActionRecord, Behavior, MigrationPlan
from .preflight import PreflightBlocker
from .snapshot import FileEntry, Snapshot

# 文案常量([错误]/[提示] 前缀由调用方补;对齐 preflight.py 的 MSG_* 约定)
MSG_REVIEW_MISSING = "计划缺少审阅守卫(旧版工具生成),请重跑 plan 启用审阅保护。"
MSG_INSTANCE_MISMATCH = (
    "游戏根目录与计划签发时不一致(签发: {expected} / 当前: {actual}),"
    "请在计划所属实例上执行,或重跑 plan。"
)
MSG_SNAPSHOT_CHANGED = (
    "快照 {version} 在计划签发后已变化(重扫或被改写),计划可能过期,请重跑 plan 重新审阅。"
)
MSG_RULES_CHANGED = "规则文件在计划签发后已变化,请重跑 plan 重新审阅。"
MSG_SOURCE_STATE_CHANGED = (
    "源文件状态与审阅时不一致: {detail} 请重跑 plan 重新审阅后再执行。"
)
MSG_TARGET_STATE_CHANGED = (
    "目标文件状态与审阅时不一致: {detail} 请重跑 plan 重新审阅后再执行。"
)


class ReviewStateError(OSError):
    """审阅状态校验阻断(execute_migration 内部校验失配时抛出,携带失配项)。

    Attributes:
        blockers: 失配项列表(code 为 target_state_changed/source_state_changed)。
    """

    def __init__(self, blockers: list[PreflightBlocker]) -> None:
        """以失配项列表初始化(消息为各项 code+文案的级联)。

        Args:
            blockers: check_action_states 产出的失配项(非空)。
        """
        self.blockers = blockers
        super().__init__("; ".join(f"{b.code}: {b.message}" for b in blockers))


def file_sha256(path: Path) -> str:
    """计算文件内容的 SHA256 十六进制摘要(流式读取,字节精确,不做换行归一化)。

    Args:
        path: 目标文件(快照/规则文件指纹的基元;须存在,不存在即 FileNotFoundError)。

    Returns:
        64 位 16 进制摘要字符串。
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def plan_fingerprint(plan: MigrationPlan) -> str:
    """计划身份指纹:save payload 剔除运行结果字段后规范化序列化的 SHA256。

    规范化 = ``MigrationPlan.normalized_payload``(剔除 executed_at/execution_summary/
    tool_version)→ ``json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))``
    → sha256。含义是「这份计划文件的内容」,不承诺「审阅时看到的世界仍是这份计划的
    世界」(后者由 validate_review/check_action_states 保证)。

    Args:
        plan: 迁移计划(内存对象;mark_executed 改写运行字段不影响指纹)。

    Returns:
        64 位 16 进制摘要字符串(GUI plan_id 即此值)。
    """
    canonical = json.dumps(
        plan.normalized_payload(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def rules_fingerprint(paths: Sequence[Path]) -> str:
    """规则来源指纹:各存在路径的「路径+内容」级联哈希。

    Args:
        paths: 规则来源路径列表(通常为 [chosen_rules_dir / "rules.yaml"]);
            不存在的来源跳过(缺失→出现的变化由内容贡献自然体现)。

    Returns:
        64 位 16 进制摘要字符串;路径字符串参与哈希,调用方两次签名须传同构路径。
    """
    h = hashlib.sha256()
    for p in paths:
        if not p.is_file():
            continue
        h.update(str(p).encode("utf-8"))
        h.update(b"\0")
        h.update(file_sha256(p).encode("ascii"))
        h.update(b"\0")
    return h.hexdigest()


def issue_review(
    *,
    game_root: Path,
    snapshot_paths: dict[str, Path],
    rule_sources: Sequence[Path],
    modpack_swap: bool,
) -> dict:
    """签发审阅守卫:计划生成时随计划一并定格执行前置条件(spec §3.3)。

    Args:
        game_root: 游戏根目录(实例身份,resolve 消 junction/别名后记录)。
        snapshot_paths: 双侧快照文件路径(键=版本名;须存在,内容指纹随签发定格)。
        rule_sources: 规则来源路径列表(与 build_plan 选定的规则目录一致)。
        modpack_swap: 迁移模式(换包开关;作为守卫之一记录,本次不参与重验)。

    Returns:
        守卫字典:{"instance": str, "snapshots": {版本名: 文件SHA256},
        "rules": str, "mode": bool};写入 plan.review 随计划持久化。
    """
    return {
        "instance": str(game_root.resolve()),
        "snapshots": {ver: file_sha256(p) for ver, p in snapshot_paths.items()},
        "rules": rules_fingerprint(rule_sources),
        "mode": bool(modpack_swap),
    }


def validate_review(
    plan: MigrationPlan,
    *,
    game_root: Path,
    snapshot_paths: dict[str, Path],
    rule_sources: Sequence[Path],
) -> list[PreflightBlocker]:
    """执行前逐项比对审阅守卫(实例身份/双侧快照指纹/规则指纹)。

    Args:
        plan: 已加载的迁移计划(review 取内存对象)。
        game_root: 当前游戏根目录。
        snapshot_paths: 双侧快照文件路径(键=版本名,与 issue_review 同构)。
        rule_sources: 当前规则来源路径列表(与 issue_review 同构)。

    Returns:
        阻断项列表(非空即不得执行);code 取值:
        review_missing | instance_mismatch | snapshot_changed | rules_changed。
        快照文件缺失视同 snapshot_changed(无法验证即失配)。
    """
    review = plan.review
    if review is None:
        return [PreflightBlocker("review_missing", MSG_REVIEW_MISSING)]
    blockers: list[PreflightBlocker] = []
    actual_instance = str(game_root.resolve())
    if review.get("instance") != actual_instance:
        blockers.append(
            PreflightBlocker(
                "instance_mismatch",
                MSG_INSTANCE_MISMATCH.format(
                    expected=review.get("instance"), actual=actual_instance
                ),
            )
        )
    recorded = review.get("snapshots")
    for ver, path in snapshot_paths.items():
        expected = recorded.get(ver) if isinstance(recorded, dict) else None
        if expected is None or not path.is_file() or file_sha256(path) != expected:
            blockers.append(
                PreflightBlocker("snapshot_changed", MSG_SNAPSHOT_CHANGED.format(version=ver))
            )
    if rules_fingerprint(rule_sources) != review.get("rules"):
        blockers.append(PreflightBlocker("rules_changed", MSG_RULES_CHANGED))
    return blockers


def _side_changed(entry: FileEntry | None, current: Path, rel: str) -> str | None:
    """单侧状态比对:快照记录(条目或「不存在」)vs 当前盘上状态。

    检测范围分级(spec §3.3 v4):已哈希条目(md5 非 None)比 md5(全检);
    size 代理条目比 size+mtime(弱检范围,同 size 同 mtime 的内容替换不承诺发现);
    「不存在」本身是被记录的状态。返回失配描述(路径+期望/实际);一致返回 None。

    Args:
        entry: 快照中的记录条目;None 表示审阅时该路径不存在。
        current: 当前盘上路径。
        rel: 动作相对路径(message 展示用,与 plan.actions 同构)。

    Returns:
        失配中文描述;状态一致返回 None。
    """
    if entry is None:
        if current.is_file():
            return f"{rel}: 审阅时不存在,现已出现"
        return None
    if not current.is_file():
        return f"{entry.path}: 审阅时存在(size={entry.size}),现已缺失"
    try:
        st = current.stat()
    except OSError as e:
        return f"{entry.path}: 当前状态不可读({e})"
    if entry.md5 is not None:
        cur_md5 = md5_of(current)
        if cur_md5 is None or cur_md5 != entry.md5:
            return f"{entry.path}: 内容已变化(审阅时 md5={entry.md5}, 现不同)"
        return None
    cur_mtime = int(st.st_mtime)
    if st.st_size != entry.size or (
        entry.mtime is not None and cur_mtime != entry.mtime
    ):
        return (
            f"{entry.path}: 状态已变化"
            f"(审阅时 size={entry.size} mtime={entry.mtime}, "
            f"现 size={st.st_size} mtime={cur_mtime})"
        )
    return None


def check_action_states(
    actions: Sequence[ActionRecord],
    src_snap: Snapshot,
    dst_snap: Snapshot,
    src_root: Path,
    dst_root: Path,
) -> list[PreflightBlocker]:
    """执行前审阅状态校验:待执行动作的源/目标当前状态 vs 快照记录状态。

    范围:behavior ∈ {COPY, ASK} 的动作(SKIP 不动盘面,不校验);
    「快照一致 ≠ 磁盘未变」——本函数比对的是当前盘面与快照记录,
    防的是审阅后、执行前的静默漂移(未重扫的磁盘改动)。

    Args:
        actions: 计划动作列表(plan.actions)。
        src_snap: 源侧快照(审阅时的记录状态)。
        dst_snap: 目标侧快照(审阅时的记录状态)。
        src_root: 源版本根目录。
        dst_root: 目标版本根目录。

    Returns:
        阻断项列表;code 取值:target_state_changed | source_state_changed,
        message 含路径与期望/实际明细。
    """
    src_index = {e.path: e for e in src_snap.files}
    dst_index = {e.path: e for e in dst_snap.files}
    blockers: list[PreflightBlocker] = []
    for a in actions:
        if a.behavior not in (Behavior.COPY, Behavior.ASK):
            continue
        detail = _side_changed(src_index.get(a.path), src_root / a.path, a.path)
        if detail is not None:
            blockers.append(
                PreflightBlocker(
                    "source_state_changed", MSG_SOURCE_STATE_CHANGED.format(detail=detail)
                )
            )
        detail = _side_changed(dst_index.get(a.path), dst_root / a.path, a.path)
        if detail is not None:
            blockers.append(
                PreflightBlocker(
                    "target_state_changed", MSG_TARGET_STATE_CHANGED.format(detail=detail)
                )
            )
    return blockers
