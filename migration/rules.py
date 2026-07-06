"""规则引擎:Rule / RuleSet / Category 与多层规则加载。

规则是数据(YAML / CLI),分层 first-match-wins(类 gitignore),glob 用 pathspec。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from importlib import resources
from pathlib import Path

import pathspec
import yaml

log = logging.getLogger(__name__)


class Category(Enum):
    """文件迁移决策类别。"""

    NEVER = "never"
    MUST_MIGRATE = "must_migrate"
    UNKNOWN = "unknown"
    ASK = "ask"


_DECIDE_MAP = {c.value: c for c in Category}


@dataclass(frozen=True)
class Rule:
    """单条分类规则。"""

    match: str
    decide: Category
    reason: str = ""
    source: str = ""  # 规则来源:cli / user / default


@dataclass
class RuleSet:
    """按优先级展开的规则集;rules[0] 优先级最高,first-match-wins。"""

    rules: list[Rule]
    _compiled: list = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._compiled = [
            (pathspec.PathSpec.from_lines("gitignore", [r.match]), r) for r in self.rules
        ]

    @classmethod
    def from_layers(cls, *layers: list[Rule]) -> "RuleSet":
        """合并多层规则,靠前层优先级高。"""
        merged: list[Rule] = []
        for layer in layers:
            merged.extend(layer)
        return cls(rules=merged)

    def classify(self, rel_path: str) -> Category:
        """对相对版本根的路径做分类,返回首个命中规则的 decide;无命中→UNKNOWN。

        glob 语义遵循 gitignore(pathspec gitwildmatch):含 `/` 的模式锚定版本根,
        `**` 跨层递归;目录命中会级联到其下文件(标准 gitignore 行为)。
        """
        norm = rel_path.replace("\\", "/")
        for spec, rule in self._compiled:
            if spec.match_file(norm):
                return rule.decide
        return Category.UNKNOWN


def _parse_rules_doc(doc: object, source: str) -> tuple[list[Rule], list[str]]:
    """解析一个 YAML 规则文档,支持两种写法。

    - 详写:{version, rules:[{match, decide, reason}]}
    - 简写:{category: [glob, ...]}(用于内置默认)

    返回 (规则列表, 错误信息列表)。
    """
    rules: list[Rule] = []
    errors: list[str] = []
    if not isinstance(doc, dict):
        return rules, [f"{source}: 文档非映射结构"]
    if "rules" in doc:
        for i, raw in enumerate(doc["rules"] or []):
            if not isinstance(raw, dict):
                errors.append(f"{source} 规则 #{i}: 非映射")
                continue
            match = raw.get("match")
            decide_raw = raw.get("decide")
            if not match or not isinstance(match, str):
                errors.append(f"{source} 规则 #{i}: 缺少 match")
                continue
            if decide_raw not in _DECIDE_MAP:
                errors.append(f"{source} 规则 #{i} '{match}': decide 非法 '{decide_raw}'")
                continue
            rules.append(
                Rule(
                    match=match,
                    decide=_DECIDE_MAP[decide_raw],
                    reason=str(raw.get("reason", "")),
                    source=source,
                )
            )
        return rules, errors
    # 简写
    for key, globs in doc.items():
        if key in ("version",):
            continue
        if key not in _DECIDE_MAP:
            errors.append(f"{source}: 未知类别 '{key}'")
            continue
        cat = _DECIDE_MAP[key]
        for g in globs or []:
            if not isinstance(g, str):
                errors.append(f"{source} 类别 '{key}': 非字符串 glob {g!r}")
                continue
            rules.append(Rule(match=g, decide=cat, reason=f"内置默认({key})", source=source))
    return rules, errors


def _expand_ver(rules: list[Rule], versions: list[str]) -> list[Rule]:
    """把规则 match 中的 <ver> 占位替换成各真实版本名(每个 <ver> 规则按版本展开成多条)。

    diff 上下文需同时识别 src 与 dst 的版本二进制,故接受版本列表;
    scan 上下文传单元素列表即可。
    """
    out: list[Rule] = []
    for r in rules:
        if "<ver>" in r.match:
            for v in versions:
                out.append(
                    Rule(
                        match=r.match.replace("<ver>", v),
                        decide=r.decide,
                        reason=r.reason,
                        source=r.source,
                    )
                )
        else:
            out.append(r)
    return out


def load_default_rules(versions: str | list[str]) -> tuple[list[Rule], list[str]]:
    """加载打包在内的内置默认规则(最低优先级),并展开 <ver> 占位。

    versions 可为单版本名(scan 上下文)或列表(diff 上下文传 [src, dst],
    以便两侧的版本专属二进制都命中 never 规则)。
    """
    if isinstance(versions, str):
        versions = [versions]
    txt = (
        resources.files("migration").joinpath("data/default_rules.yaml").read_text(encoding="utf-8")
    )
    doc = yaml.safe_load(txt)
    rules_list, errors = _parse_rules_doc(doc, "default")
    return _expand_ver(rules_list, versions), errors


def load_user_rules(path: Path) -> tuple[list[Rule], list[str]]:
    """加载用户规则文件(详写格式)。文件不存在时返回空。"""
    if not path.exists():
        return [], []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return [], [f"{path}: YAML 解析失败: {e}"]
    return _parse_rules_doc(doc, f"user:{path.name}")


def load_cli_rules(excludes: list[str] | None, includes: list[str] | None) -> list[Rule]:
    """根据 CLI --exclude/--include 构造临时规则(最高优先级)。"""
    out: list[Rule] = []
    for g in excludes or []:
        out.append(Rule(match=g, decide=Category.NEVER, reason="CLI --exclude", source="cli"))
    for g in includes or []:
        out.append(
            Rule(match=g, decide=Category.MUST_MIGRATE, reason="CLI --include", source="cli")
        )
    return out
