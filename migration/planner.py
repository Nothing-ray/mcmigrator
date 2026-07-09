"""Planner:消费 v0 的 DiffReport + src_index → 可执行 MigrationPlan。

决策树要点(完整见 Reference/design/planner-rules.md 与 spec):
- mods 桶 → mod_added/mod_shared/mod_target_only(按 note)
- never → skip;note="rebuild" → origin=rebuild,否则 origin=never
- identical → skip_identical(分 verified/size-based)
- to_migrate → copy(backup_target 区分 new/modified)
- candidate → config/ 前缀做 .bak 判定,非 config → ask
- only_in_dst 不产生 action
"""

from __future__ import annotations

import fnmatch
import re
from datetime import datetime, timezone

from .differ import DiffItem, DiffReport
from .plan import ActionRecord, Behavior, MigrationPlan, Origin
from .snapshot import FileEntry

CONFIG_PREFIX = "config/"
_CONFLICT_BACKUP_DIR = "_conflict_backup"


def _backup_target(path: str) -> str:
    """计算 overwrite 时的目标备份路径。"""
    return f"{_CONFLICT_BACKUP_DIR}/{path}"


def _md5_match(s: FileEntry | None, d: FileEntry | None) -> bool | None:
    """三态:两边都有 md5 → 比对结果;否则 None(未比对)。"""
    if s and d and s.md5 is not None and d.md5 is not None:
        return s.md5 == d.md5
    return None


def has_bak_sibling(path: str, src_paths: set[str]) -> bool:
    """判断 src 清单中是否存在该文件的 .bak 兄弟(plain 或 versioned)。

    - plain: path + ".bak"(如 config/foo.toml.bak)
    - versioned: stem + "-[0-9]*" + suffix + ".bak"(如 config/foo-1.toml.bak,数字开头)

    仅作路径模式匹配,不读文件内容。
    """
    plain = path + ".bak"
    if plain in src_paths:
        return True
    dot = path.rfind(".")
    if dot == -1:
        stem, suffix = path, ""
    else:
        stem, suffix = path[:dot], path[dot:]
    pattern = f"{stem}-[0-9]*{suffix}.bak"
    return any(fnmatch.fnmatch(p, pattern) for p in src_paths)


def resolve_bak_parent(path: str, src_paths: set[str]) -> str | None:
    """从一个 .bak 文件路径反推父 config(与 has_bak_sibling 对偶)。

    仅做路径模式匹配,不读文件。返回父 path(在 src_paths 中)或 None(孤儿)。
    仅适用于 config/ 前缀(NeoForge .bak 机制专属)。

    算法:
        1. 必须以 .bak 结尾,否则返回 None。
        2. 剥 .bak → base。
        3. 拆 base 最后一个 "." → (stem, suffix)。
        4. 若 stem 末尾匹配 -[0-9]+(versioned):剥掉得 versioned_parent,优先查它在 src_paths。
        5. 否则(plain):查 base 本身在 src_paths。
        6. 都不在 → 返回 None(孤儿)。

    Args:
        path: .bak 文件相对路径(如 "config/create-1.toml.bak")。
        src_paths: 源版本所有文件路径集合。

    Returns:
        父 config 路径;孤儿返回 None。
    """
    if not path.endswith(".bak"):
        return None
    base = path[: -len(".bak")]
    dot = base.rfind(".")
    stem, suffix = (base[:dot], base[dot:]) if dot != -1 else (base, "")
    # 取舍:versioned 优先(spec §3.1)。理论上"名字含 -数字 的合法文件"(如 config/foo-360.toml
    # 的 .bak)会优先匹配 config/foo.toml;NeoForge 版本号均为 -1/-2 小整数,现实无命中。
    m = re.match(r"^(.*)-[0-9]+$", stem)
    if m:
        versioned_parent = m.group(1) + suffix
        if versioned_parent in src_paths:
            return versioned_parent
    if base in src_paths:
        return base
    return None


class Planner:
    """消费 DiffReport + src_index,产出 MigrationPlan。"""

    def __init__(
        self,
        report: DiffReport,
        src_index: dict[str, FileEntry],
    ) -> None:
        self.report = report
        self.src_index = src_index

    def plan(self) -> MigrationPlan:
        """生成迁移计划(.bak candidate 两趟处理:pass 2 继承父命运)。"""
        actions: list[ActionRecord] = []
        bak_candidates: list[DiffItem] = []
        for item in self.report.to_migrate:
            actions.append(self._for_to_migrate(item))
        for item in self.report.candidate:
            # config/ 下的 .bak 延迟到 pass 2(需看父的最终决策)
            if item.path.startswith(CONFIG_PREFIX) and item.path.endswith(".bak"):
                bak_candidates.append(item)
            else:
                actions.append(self._for_candidate(item))
        for item in self.report.identical:
            actions.append(self._for_identical(item))
        for item in self.report.never:
            actions.append(self._for_never(item))
        for item in self.report.mods:
            actions.append(self._for_mod(item))
        # Pass 2:.bak candidate 继承父 config 的最终命运
        decision: dict[str, ActionRecord] = {a.path: a for a in actions}
        src_paths = set(self.src_index.keys())
        for item in bak_candidates:
            actions.append(self._for_bak(item, decision, src_paths))
        return MigrationPlan(
            src="",  # 由 CLI 填(Planner 不知版本名)
            dst="",
            generated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            actions=actions,
        )

    def _for_to_migrate(self, item: DiffItem) -> ActionRecord:
        s, d = item.src, item.dst
        if item.note == "new":
            return ActionRecord(
                path=item.path, behavior=Behavior.COPY, origin=Origin.MUST_MIGRATE,
                src_size=s.size if s else None, dst_size=None,
                md5_match=None, confidence="high",
                reason="must_migrate + dst missing", backup_target=None,
            )
        return ActionRecord(
            path=item.path, behavior=Behavior.COPY, origin=Origin.MUST_MIGRATE,
            src_size=s.size if s else None, dst_size=d.size if d else None,
            md5_match=_md5_match(s, d), confidence="high",
            reason="must_migrate + content differs",
            backup_target=_backup_target(item.path),
        )

    def _for_identical(self, item: DiffItem) -> ActionRecord:
        s, d = item.src, item.dst
        confidence = "high" if item.note == "verified" else "medium"
        return ActionRecord(
            path=item.path, behavior=Behavior.SKIP, origin=Origin.IDENTICAL,
            src_size=s.size if s else None, dst_size=d.size if d else None,
            md5_match=_md5_match(s, d), confidence=confidence,
            reason=f"identical ({item.note})", backup_target=None,
        )

    def _for_never(self, item: DiffItem) -> ActionRecord:
        origin = Origin.REBUILD if item.note == "rebuild" else Origin.NEVER
        return ActionRecord(
            path=item.path, behavior=Behavior.SKIP, origin=origin,
            src_size=item.src.size if item.src else None,
            dst_size=item.dst.size if item.dst else None,
            md5_match=None, confidence="high",
            reason=f"classified {origin.value}", backup_target=None,
        )

    def _for_mod(self, item: DiffItem) -> ActionRecord:
        behavior, origin = {
            "to_add": (Behavior.COPY, Origin.MOD_ADDED),
            "shared": (Behavior.SKIP, Origin.MOD_SHARED),
            "target_only": (Behavior.SKIP, Origin.MOD_TARGET_ONLY),
        }.get(item.note, (Behavior.SKIP, Origin.MOD_SHARED))
        return ActionRecord(
            path=item.path, behavior=behavior, origin=origin,
            src_size=item.src.size if item.src else None,
            dst_size=item.dst.size if item.dst else None,
            md5_match=None, confidence="high", reason=f"mods ({item.note})",
            backup_target=None,
        )

    def _for_candidate(self, item: DiffItem) -> ActionRecord:
        """candidate 决策树(config/ 前缀走 .bak 判定,非 config → ask)。

        白名单命中的文件已在规则层归 must_migrate(不进 candidate),故此处
        candidate 已是「白名单未命中的残余」——config 下只做 .bak 判定。
        """
        if not item.path.startswith(CONFIG_PREFIX):
            return self._ask(item, reason="candidate (non-config, needs user confirm)")
        src_paths = set(self.src_index.keys())
        if has_bak_sibling(item.path, src_paths):
            if item.note == "new":
                return ActionRecord(
                    path=item.path, behavior=Behavior.COPY, origin=Origin.CONFIG_MODIFIED,
                    src_size=item.src.size if item.src else None, dst_size=None,
                    md5_match=None, confidence="high",
                    reason=".bak sibling exists", backup_target=None,
                )
            return ActionRecord(
                path=item.path, behavior=Behavior.COPY, origin=Origin.CONFIG_MODIFIED,
                src_size=item.src.size if item.src else None,
                dst_size=item.dst.size if item.dst else None,
                md5_match=_md5_match(item.src, item.dst), confidence="high",
                reason=".bak sibling exists",
                backup_target=_backup_target(item.path),
            )
        return ActionRecord(
            path=item.path, behavior=Behavior.SKIP, origin=Origin.DEFAULT_CONFIG,
            src_size=item.src.size if item.src else None,
            dst_size=item.dst.size if item.dst else None,
            md5_match=_md5_match(item.src, item.dst), confidence="high",
            reason="no .bak, not in whitelist", backup_target=None,
        )

    def _ask(self, item: DiffItem, *, reason: str) -> ActionRecord:
        return ActionRecord(
            path=item.path, behavior=Behavior.ASK, origin=Origin.NEEDS_REVIEW,
            src_size=item.src.size if item.src else None,
            dst_size=item.dst.size if item.dst else None,
            md5_match=_md5_match(item.src, item.dst), confidence="low",
            reason=reason, backup_target=None,
        )

    def _for_bak(
        self, item: DiffItem, decision: dict[str, ActionRecord], src_paths: set[str]
    ) -> ActionRecord:
        """.bak candidate 继承父 config 命运(spec §3.2)。

        - 父在决策表且 behavior=SKIP(如 rebuild/identical)→ 完整继承父。最常见例子:
          fml.toml(rebuild)带 .bak → .bak 跟父跳。
        - 父在决策表且 behavior=COPY(迁)→ bak_file COPY(backup_target 按 new/modified)。
        - 父不在 src(孤儿)→ ASK / needs_review。
        - 父在 src 但不在决策表→ ASK(防御性保护,不应发生)。
        """
        parent_path = resolve_bak_parent(item.path, src_paths)
        if parent_path is None:
            return ActionRecord(
                path=item.path, behavior=Behavior.ASK, origin=Origin.NEEDS_REVIEW,
                src_size=item.src.size if item.src else None,
                dst_size=item.dst.size if item.dst else None,
                md5_match=_md5_match(item.src, item.dst), confidence="low",
                reason="orphan .bak, parent not in src", backup_target=None,
            )
        parent = decision.get(parent_path)
        if parent is None:
            # 防御:父在 src 但不在决策表(不应发生,保护边界)
            return ActionRecord(
                path=item.path, behavior=Behavior.ASK, origin=Origin.NEEDS_REVIEW,
                src_size=item.src.size if item.src else None,
                dst_size=item.dst.size if item.dst else None,
                md5_match=_md5_match(item.src, item.dst), confidence="low",
                reason="parent config not found in decision table",
                backup_target=None,
            )
        if parent.behavior == Behavior.SKIP:
            # 父被跳过 → 完整继承父(rebuild/identical/default_config 等)
            return ActionRecord(
                path=item.path, behavior=parent.behavior, origin=parent.origin,
                src_size=item.src.size if item.src else None,
                dst_size=item.dst.size if item.dst else None,
                md5_match=_md5_match(item.src, item.dst), confidence="low",
                reason=f"follows {parent.origin.value} parent", backup_target=None,
            )
        # 父迁 → bak_file COPY
        backup_target = None if item.note == "new" else _backup_target(item.path)
        return ActionRecord(
            path=item.path, behavior=Behavior.COPY, origin=Origin.BAK_FILE,
            src_size=item.src.size if item.src else None,
            dst_size=item.dst.size if item.dst else None,
            md5_match=_md5_match(item.src, item.dst), confidence="high",
            reason="follows migrated config parent", backup_target=backup_target,
        )
