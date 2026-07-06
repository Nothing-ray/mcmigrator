"""Diff:两份分类快照 → 迁移导向 6 桶报告。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .classifier import Classifier
from .rules import Category
from .snapshot import FileEntry

MODS_PREFIX = "mods/"


@dataclass(frozen=True)
class DiffItem:
    """单个文件的 diff 条目。"""

    path: str
    src: FileEntry | None
    dst: FileEntry | None
    note: str = ""  # verified / size-based / modified / new / to_add / shared / target_only ...


@dataclass
class DiffReport:
    """迁移导向的 6 桶报告。"""

    to_migrate: list[DiffItem] = field(default_factory=list)
    candidate: list[DiffItem] = field(default_factory=list)
    mods: list[DiffItem] = field(default_factory=list)
    only_in_dst: list[DiffItem] = field(default_factory=list)
    identical: list[DiffItem] = field(default_factory=list)
    never: list[DiffItem] = field(default_factory=list)


def _is_mod(path: str) -> bool:
    """是否为 mods 目录下的 jar(按文件名集合处理)。"""
    return path.startswith(MODS_PREFIX) and path.endswith(".jar")


class Differ:
    """对比 src/dst 两份文件清单,按分类与存在性分桶。"""

    def __init__(
        self,
        src_entries: list[FileEntry],
        dst_entries: list[FileEntry],
        classifier: Classifier,
    ) -> None:
        self.src = {e.path: e for e in src_entries}
        self.dst = {e.path: e for e in dst_entries}
        self.classifier = classifier

    @staticmethod
    def _same_content(s: FileEntry, d: FileEntry) -> tuple[bool, str]:
        """比较内容。两边有 md5 比字节(verified);否则比 size(size-based)。"""
        if s.md5 is not None and d.md5 is not None:
            return s.md5 == d.md5, "verified"
        return s.size == d.size, "size-based"

    def _mod_item(self, path: str, s: FileEntry | None, d: FileEntry | None) -> DiffItem:
        """mods 目录条目按文件名集合三态分桶:shared / to_add / target_only。"""
        if s and d:
            note = "shared"
        elif s:
            note = "to_add"
        else:
            note = "target_only"
        return DiffItem(path=path, src=s, dst=d, note=note)

    def diff(self) -> DiffReport:
        """生成 6 桶 DiffReport。

        - mods/*.jar 一律进 mods 桶(按文件名集合,不进 candidate,避免 119 jar 噪声)
        - 分类 NEVER → never 桶
        - 分类 MUST_MIGRATE:dst 缺失/内容不同 → to_migrate;相同 → identical;
          src 缺失(仅 dst 有)→ only_in_dst
        - 分类 UNKNOWN/ASK:dst 缺失/内容不同 → candidate;相同 → identical;
          src 缺失 → only_in_dst
        """
        report = DiffReport()
        for path in sorted(set(self.src) | set(self.dst)):
            s = self.src.get(path)
            d = self.dst.get(path)
            if _is_mod(path):
                report.mods.append(self._mod_item(path, s, d))
                continue
            cat = self.classifier.classify_path(path)
            if cat == Category.NEVER:
                report.never.append(DiffItem(path, s, d, note="never"))
                continue
            if cat == Category.MUST_MIGRATE:
                if d is None:
                    report.to_migrate.append(DiffItem(path, s, d, note="new"))
                elif s is None:
                    report.only_in_dst.append(DiffItem(path, s, d, note="target_only"))
                else:
                    same, how = self._same_content(s, d)
                    if same:
                        report.identical.append(DiffItem(path, s, d, note=how))
                    else:
                        report.to_migrate.append(DiffItem(path, s, d, note="modified"))
                continue
            # UNKNOWN / ASK:v0 视同待用户决策
            if d is None:
                report.candidate.append(DiffItem(path, s, d, note="new"))
            elif s is None:
                report.only_in_dst.append(DiffItem(path, s, d, note="target_only"))
            else:
                same, how = self._same_content(s, d)
                if same:
                    report.identical.append(DiffItem(path, s, d, note=how))
                else:
                    report.candidate.append(DiffItem(path, s, d, note="modified"))
        return report
