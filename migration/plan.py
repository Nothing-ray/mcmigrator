"""Plan 数据模型与 JSON 持久化。

Plan = 可执行 action 列表(由 Planner 从 DiffReport 细化而来),供 Executor 后续消费。
2D 模型:behavior(操作,Executor 吃)× origin(语义来源,reporter 吃)。
对齐 snapshot.py 风格:格式版本检查 + 友好错误。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from .snapshot import TOOL_VERSION

PLAN_FORMAT = 2


class PlanFormatError(Exception):
    """plan 文件格式版本不支持或内容损坏。"""


class Behavior(str, Enum):
    """单个文件的操作(Executor 关心,3 值闭合,极稳)。

    - COPY: 复制 src→dst(若 backup_target 非空,先备份 dst)。
    - SKIP: 不动。
    - ASK: 需人工确认(非交互→SKIP,交互→提示)。
    """

    COPY = "copy"
    SKIP = "skip"
    ASK = "ask"


@dataclass(frozen=True)
class OriginSpec:
    """单个 origin 的元数据:结构契约(behavior)+ reporter 显示皮。

    Attributes:
        title: 含 emoji 的分组标题(如 "✅ 必迁")。
        default_visible: 不带 --show-skip 时是否默认显示。
        show_backup: 是否在该 origin 分组表里显示 backup_target 列。
        behavior: 该 origin 对应的操作(2D 模型 1:1 不变量,spec §2 表);
            reporter 据此判定是否显示 new/modified 子计数,Executor/Planner 不读。
    """

    title: str
    default_visible: bool
    show_backup: bool
    behavior: Behavior


class Origin(str, Enum):
    """单个文件的语义来源(reporter 关心,随路线图增长)。"""

    MUST_MIGRATE = "must_migrate"
    CONFIG_MODIFIED = "config_modified"
    BAK_FILE = "bak_file"
    MOD_ADDED = "mod_added"
    IDENTICAL = "identical"
    NEVER = "never"
    DEFAULT_CONFIG = "default_config"
    REBUILD = "rebuild"
    MOD_SHARED = "mod_shared"
    MOD_TARGET_ONLY = "mod_target_only"
    NEEDS_REVIEW = "needs_review"


# origin -> OriginSpec(初版词表,见 spec §2.2/§2;behavior 为结构契约,非显示皮)
_ORIGIN_SEED: dict[str, OriginSpec] = {
    "must_migrate":    OriginSpec("✅ 必迁",            True,  False, Behavior.COPY),
    "config_modified": OriginSpec("✏️ 改过的 config",   True,  False, Behavior.COPY),
    "bak_file":        OriginSpec("📋 备份文件",        True,  False, Behavior.COPY),
    "mod_added":       OriginSpec("📦 补 Mod",          True,  False, Behavior.COPY),
    "needs_review":    OriginSpec("❓ 待确认",          True,  False, Behavior.ASK),
    "rebuild":         OriginSpec("🔒 版本敏感",        False, False, Behavior.SKIP),
    "default_config":  OriginSpec("⚙️ 默认配置",        False, False, Behavior.SKIP),
    "never":           OriginSpec("⛔ 不迁",            False, False, Behavior.SKIP),
    "identical":       OriginSpec("⏭ 一致",            False, False, Behavior.SKIP),
    "mod_shared":      OriginSpec("📦 共有 Mod",        False, False, Behavior.SKIP),
    "mod_target_only": OriginSpec("📦 目标独有 Mod",    False, False, Behavior.SKIP),
}


# 先声明词典供模块级引用,再通过 _seed_registry() 填充(两步保证 import 安全)
ORIGIN_REGISTRY: dict[str, OriginSpec] = {}


def register_origin(
    key: str, *, title: str, visible: bool, show_backup: bool, behavior: Behavior
) -> None:
    """注册一个 origin 元数据(启动时播种已知 origin;未来 profiles/插件可扩展新 origin)。

    Args:
        key: origin 字符串值(如 "must_migrate")。
        title: 含 emoji 的分组标题。
        visible: 默认是否显示。
        show_backup: 是否显示 backup_target 列。
        behavior: 该 origin 对应的操作(COPY/SKIP/ASK,2D 模型 1:1 不变量)。
    """
    ORIGIN_REGISTRY[key] = OriginSpec(
        title=title, default_visible=visible, show_backup=show_backup, behavior=behavior
    )


def _seed_registry() -> None:
    """从 Origin 全部成员播种注册表(幂等)。"""
    for member in Origin:
        ORIGIN_REGISTRY[member.value] = _ORIGIN_SEED[member.value]


_seed_registry()


@dataclass(frozen=True)
class ActionRecord:
    """单个文件的迁移动作记录。"""

    path: str
    behavior: Behavior
    origin: Origin
    src_size: int | None
    dst_size: int | None
    md5_match: bool | None  # true/false/null;null = 未比对(size-based 或一边缺)
    confidence: str         # "high" / "medium" / "low"
    reason: str
    backup_target: str | None  # 覆盖时为 "_conflict_backup/<path>";驱动 COPY 内备份步骤;其余 None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["behavior"] = self.behavior.value
        d["origin"] = self.origin.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ActionRecord":
        return cls(
            path=d["path"],
            behavior=Behavior(d["behavior"]),
            origin=Origin(d["origin"]),
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
        """按 origin 统计计数。"""
        counts: dict[str, int] = {o.value: 0 for o in Origin}
        for r in self.actions:
            counts[r.origin.value] += 1
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
