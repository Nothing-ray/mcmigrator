"""Diff:两份分类快照 → 迁移导向 6 桶报告。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .classifier import Classifier
from .rules import Category
from .snapshot import FileEntry
from .textcompare import (
    json_semantic_equal,
    properties_semantic_equal,
    toml_semantic_equal,
)

MODS_PREFIX = "mods/"

ContentReader = Callable[[str, str], "bytes | None"]  # (rel_path, side) → 内容;None=读取失败


@dataclass(frozen=True)
class DiffItem:
    """单个文件的 diff 条目。

    note 为 Differ→Planner 的字符串契约(非枚举,各桶异质;typo 静默走默认):
    - to_migrate / candidate: new / modified
    - identical:              verified / size-based / semantics
    - never:                  never / rebuild / orphan
    - mods:                   to_add / shared / rebuilt / target_only
    - only_in_dst:            target_only
    """

    path: str
    src: FileEntry | None
    dst: FileEntry | None
    note: str = ""


@dataclass
class DiffReport:
    """迁移导向的 6 桶报告。"""

    to_migrate: list[DiffItem] = field(default_factory=list)
    candidate: list[DiffItem] = field(default_factory=list)
    mods: list[DiffItem] = field(default_factory=list)
    only_in_dst: list[DiffItem] = field(default_factory=list)
    identical: list[DiffItem] = field(default_factory=list)
    never: list[DiffItem] = field(default_factory=list)


def is_mod_jar(path: str) -> bool:
    """是否为 mods 目录下的 jar(按文件名集合处理;CLI 配对输入推导同源,F23)。"""
    return path.startswith(MODS_PREFIX) and path.endswith(".jar")


# F12/F16: 语义等价判定按后缀分发(仅 md5 异 + 双侧内容可读时触发)
SEMANTIC_EQUAL_BY_SUFFIX: dict[str, Callable[[bytes, bytes], bool]] = {
    ".properties": properties_semantic_equal,
    ".json": json_semantic_equal,
    ".toml": toml_semantic_equal,
}


class Differ:
    """对比 src/dst 两份文件清单,按分类与存在性分桶。"""

    def __init__(
        self,
        src_entries: list[FileEntry],
        dst_entries: list[FileEntry],
        classifier: Classifier,
        modpack_swap: bool = False,
        content_reader: ContentReader | None = None,
    ) -> None:
        self.src = {e.path: e for e in src_entries}
        self.dst = {e.path: e for e in dst_entries}
        self.classifier = classifier
        self.modpack_swap = modpack_swap
        self.content_reader = content_reader

    @staticmethod
    def _same_content(s: FileEntry, d: FileEntry) -> tuple[bool, str]:
        """比较内容。两边有 md5 比字节(verified);否则比 size(size-based)。"""
        if s.md5 is not None and d.md5 is not None:
            return s.md5 == d.md5, "verified"
        return s.size == d.size, "size-based"

    def _same_content_semantic(self, path: str, s: FileEntry, d: FileEntry) -> tuple[bool, str]:
        """内容比较:F12/F16 在 md5 异、后缀命中、读取成功时做语义复核。"""
        if s.md5 is not None and d.md5 is not None and s.md5 != d.md5:
            suffix = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
            check = SEMANTIC_EQUAL_BY_SUFFIX.get(suffix)
            if check is not None and self.content_reader is not None:
                a = self.content_reader(path, "src")
                b = self.content_reader(path, "dst")
                if a is not None and b is not None and check(a, b):
                    return True, "semantics"
            return False, "verified"
        return self._same_content(s, d)

    def _mod_item(self, path: str, s: FileEntry | None, d: FileEntry | None) -> DiffItem:
        """mods 目录条目按文件名集合分桶:shared / rebuilt / to_add / target_only。"""
        if s and d:
            if s.size != d.size or (s.md5 and d.md5 and s.md5 != d.md5):
                note = "rebuilt"  # F17: 同名同版本号、内容不同(上游重新打包)
            else:
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
            if is_mod_jar(path):
                # 换包模式:src 独有 mod 不回迁(旧 modpack 自带,非玩家私货),
                # 但用户显式 must_migrate 规则命中的 jar 仍放行(用户主权)
                if (
                    self.modpack_swap
                    and s is not None
                    and d is None
                    and self.classifier.classify_path(path) != Category.MUST_MIGRATE
                ):
                    report.never.append(DiffItem(path, s, d, note="modpack_swap"))
                    continue
                report.mods.append(self._mod_item(path, s, d))
                continue
            cat = self.classifier.classify_path(path)
            if cat == Category.NEVER:
                report.never.append(DiffItem(path, s, d, note="never"))
                continue
            if cat == Category.REBUILD:
                report.never.append(DiffItem(path, s, d, note="rebuild"))
                continue
            if cat == Category.ORPHAN:
                report.never.append(DiffItem(path, s, d, note="orphan"))
                continue
            if cat == Category.MUST_MIGRATE:
                if d is None:
                    report.to_migrate.append(DiffItem(path, s, d, note="new"))
                elif s is None:
                    report.only_in_dst.append(DiffItem(path, s, d, note="target_only"))
                else:
                    same, how = self._same_content_semantic(path, s, d)
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
                same, how = self._same_content_semantic(path, s, d)
                if same:
                    report.identical.append(DiffItem(path, s, d, note=how))
                else:
                    report.candidate.append(DiffItem(path, s, d, note="modified"))
        return report
