"""Plan 数据模型与 JSON 持久化。

Plan = 可执行 action 列表(由 Planner 从 DiffReport 细化而来),供 Executor 后续消费。
对齐 snapshot.py 风格:格式版本检查 + 友好错误。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from .snapshot import TOOL_VERSION

PLAN_FORMAT = 1


class PlanFormatError(Exception):
    """plan 文件格式版本不支持或内容损坏。"""


class Action(Enum):
    """单个文件在迁移计划中的动作。"""

    COPY_NEW = "copy_new"                 # 源有目标无,直接复制
    OVERWRITE = "overwrite"               # 两边都有但不同,源覆盖(目标备份)
    SKIP_IDENTICAL = "skip_identical"     # 两边一致
    SKIP_NEVER = "skip_never"             # 分类 never
    SKIP_DEFAULT_CONFIG = "skip_default_config"  # config 无 .bak 且不在白名单
    ADD_MOD = "add_mod"                   # 源独有 mod
    KEEP_MOD = "keep_mod"                 # 共有 mod
    IGNORE_TARGET_MOD = "ignore_target_mod"  # 目标独有 mod
    ASK = "ask"                           # 无法判定,需人工确认


@dataclass(frozen=True)
class ActionRecord:
    """单个文件的迁移动作记录。"""

    path: str
    action: Action
    src_size: int | None
    dst_size: int | None
    md5_match: bool | None  # true/false/null;null = 未比对(size-based 或一边缺)
    confidence: str         # "high" / "medium" / "low"
    reason: str
    backup_target: str | None  # overwrite 时为 "_conflict_backup/<path>";其余 None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["action"] = self.action.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ActionRecord":
        return cls(
            path=d["path"],
            action=Action(d["action"]),
            src_size=d.get("src_size"),
            dst_size=d.get("dst_size"),
            md5_match=d.get("md5_match"),
            confidence=d["confidence"],
            reason=d["reason"],
            backup_target=d.get("backup_target"),
        )


@dataclass
class MigrationPlan:
    """一个 src→dst 迁移计划。"""

    src: str
    dst: str
    generated_at: str
    actions: list[ActionRecord]
    tool_version: str = TOOL_VERSION
    plan_format: int = PLAN_FORMAT

    def summary(self) -> dict[str, int]:
        """按 action 统计计数。"""
        counts: dict[str, int] = {a.value: 0 for a in Action}
        for r in self.actions:
            counts[r.action.value] += 1
        return counts

    def save(self, path: Path) -> None:
        """写入 JSON(自动创建父目录)。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tool_version": self.tool_version,
            "plan_format": self.plan_format,
            "src": self.src,
            "dst": self.dst,
            "generated_at": self.generated_at,
            "summary": self.summary(),
            "actions": [r.to_dict() for r in self.actions],
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Path) -> "MigrationPlan":
        """从 JSON 读 plan;格式版本不支持时抛 PlanFormatError。"""
        with path.open("r", encoding="utf-8") as f:
            try:
                payload = json.load(f)
            except json.JSONDecodeError as e:
                raise PlanFormatError(f"plan JSON 解析失败: {e}") from e
        if not isinstance(payload, dict):
            raise PlanFormatError(f"plan 顶层非对象: {type(payload).__name__}")
        fmt = payload.get("plan_format")
        if fmt != PLAN_FORMAT:
            raise PlanFormatError(
                f"plan 格式版本 {fmt} 不支持(当前 {PLAN_FORMAT}),请重新 plan"
            )
        try:
            actions = [ActionRecord.from_dict(d) for d in payload["actions"]]
            return cls(
                src=payload["src"],
                dst=payload["dst"],
                generated_at=payload["generated_at"],
                actions=actions,
            )
        except (KeyError, TypeError, ValueError) as e:
            raise PlanFormatError(f"plan 内容字段缺失或类型错误: {e}") from e


def plan_path(workdir: Path, src: str, dst: str) -> Path:
    """返回 plan 文件标准路径:<workdir>/.mcmig/plans/<src>__<dst>.plan.json。"""
    return workdir / ".mcmig" / "plans" / f"{src}__{dst}.plan.json"
