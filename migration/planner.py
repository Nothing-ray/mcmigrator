"""Planner:消费 v0 的 DiffReport + src_index → 可执行 MigrationPlan。

决策树要点(完整见 design/planner-rules.md):
- mods 桶 → add_mod/keep_mod/ignore_target_mod(按 note)
- never → skip_never;identical → skip_identical(分 verified/size-based)
- to_migrate → copy_new(new)/overwrite(modified)
- candidate → config/ 前缀做 .bak 判定(Task 4),非 config → ask
- only_in_dst 不产生 action(目标独有,与迁移无关)
"""

from __future__ import annotations

import fnmatch
from datetime import datetime, timezone

from .differ import DiffItem, DiffReport
from .plan import Action, ActionRecord, MigrationPlan
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
        """生成迁移计划。"""
        actions: list[ActionRecord] = []
        for item in self.report.to_migrate:
            actions.append(self._for_to_migrate(item))
        for item in self.report.candidate:
            actions.append(self._for_candidate(item))
        for item in self.report.identical:
            actions.append(self._for_identical(item))
        for item in self.report.never:
            actions.append(self._for_never(item))
        for item in self.report.mods:
            actions.append(self._for_mod(item))
        # only_in_dst 不产生 action
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
                path=item.path, action=Action.COPY_NEW,
                src_size=s.size if s else None, dst_size=None,
                md5_match=None, confidence="high",
                reason="must_migrate + dst missing", backup_target=None,
            )
        return ActionRecord(
            path=item.path, action=Action.OVERWRITE,
            src_size=s.size if s else None, dst_size=d.size if d else None,
            md5_match=_md5_match(s, d), confidence="high",
            reason="must_migrate + content differs",
            backup_target=_backup_target(item.path),
        )

    def _for_identical(self, item: DiffItem) -> ActionRecord:
        s, d = item.src, item.dst
        confidence = "high" if item.note == "verified" else "medium"
        return ActionRecord(
            path=item.path, action=Action.SKIP_IDENTICAL,
            src_size=s.size if s else None, dst_size=d.size if d else None,
            md5_match=_md5_match(s, d), confidence=confidence,
            reason=f"identical ({item.note})", backup_target=None,
        )

    def _for_never(self, item: DiffItem) -> ActionRecord:
        return ActionRecord(
            path=item.path, action=Action.SKIP_NEVER,
            src_size=item.src.size if item.src else None,
            dst_size=item.dst.size if item.dst else None,
            md5_match=None, confidence="high", reason="classified never",
            backup_target=None,
        )

    def _for_mod(self, item: DiffItem) -> ActionRecord:
        action = {
            "to_add": Action.ADD_MOD,
            "shared": Action.KEEP_MOD,
            "target_only": Action.IGNORE_TARGET_MOD,
        }.get(item.note, Action.KEEP_MOD)
        return ActionRecord(
            path=item.path, action=action,
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
                    path=item.path, action=Action.COPY_NEW,
                    src_size=item.src.size if item.src else None, dst_size=None,
                    md5_match=None, confidence="high",
                    reason=".bak sibling exists", backup_target=None,
                )
            return ActionRecord(
                path=item.path, action=Action.OVERWRITE,
                src_size=item.src.size if item.src else None,
                dst_size=item.dst.size if item.dst else None,
                md5_match=_md5_match(item.src, item.dst), confidence="high",
                reason=".bak sibling exists",
                backup_target=_backup_target(item.path),
            )
        return ActionRecord(
            path=item.path, action=Action.SKIP_DEFAULT_CONFIG,
            src_size=item.src.size if item.src else None,
            dst_size=item.dst.size if item.dst else None,
            md5_match=_md5_match(item.src, item.dst), confidence="high",
            reason="no .bak, not in whitelist", backup_target=None,
        )

    def _ask(self, item: DiffItem, *, reason: str) -> ActionRecord:
        return ActionRecord(
            path=item.path, action=Action.ASK,
            src_size=item.src.size if item.src else None,
            dst_size=item.dst.size if item.dst else None,
            md5_match=_md5_match(item.src, item.dst), confidence="low",
            reason=reason, backup_target=None,
        )
