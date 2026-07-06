"""分类器:把 FileEntry 按规则集归类。"""

from __future__ import annotations

from dataclasses import dataclass

from .rules import Category, RuleSet
from .snapshot import FileEntry


@dataclass(frozen=True)
class ClassifiedEntry:
    """带分类标签的文件条目。"""

    entry: FileEntry
    category: Category


class Classifier:
    """按 RuleSet 对文件做分类。"""

    def __init__(self, ruleset: RuleSet) -> None:
        self.ruleset = ruleset

    def classify_path(self, rel_path: str) -> Category:
        """按相对路径字符串分类。"""
        return self.ruleset.classify(rel_path)

    def classify(self, entry: FileEntry) -> Category:
        """按 FileEntry 的 path 分类。"""
        return self.classify_path(entry.path)

    def classify_all(self, entries: list[FileEntry]) -> list[ClassifiedEntry]:
        """批量分类,保持输入顺序。"""
        return [ClassifiedEntry(entry=e, category=self.classify(e)) for e in entries]
