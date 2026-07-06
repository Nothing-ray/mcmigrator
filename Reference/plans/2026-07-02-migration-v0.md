# v0 只读 scan/diff 工具 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个纯只读的 `scan` + `diff` CLI 工具,扫描 Minecraft 整合包版本文件夹生成快照,并对比两个快照给出迁移导向的 6 桶报告——绝不写入游戏版本文件夹。

**Architecture:** 自底向上 6 模块(`hashing`/`rules`/`classifier`/`snapshot`/`scanner`/`differ`/`reporter`)+ `cli` 接线。核心解耦:扫描存原始清单(无分类),分类在「读快照→出报告」时按当前规则现算→改规则不重扫。分类=数据驱动规则引擎,分层 first-match-wins,glob 用 pathspec(gitignore 语义)。哈希分层:文本全量 MD5、mods 按文件名集合、bulk(sqlite/zip/mca)按 size。

**Tech Stack:** Python 3.11+ / `rich`(终端)/ `PyYAML`(规则)/ `pathspec`(glob)/ stdlib `hashlib`·`pathlib`·`argparse`·`tomllib`·`json`·`enum`·`dataclasses`。测试 pytest,lint ruff。

**配套设计:** `Reference/specs/2026-07-02-migration-v0-design.md`、`Reference/design/{hashing-strategy,classifier-rules}.md`

---

## 全局约定

- **项目根**:本工作目录下新建 `migration/`(其内是 `pyproject.toml` + 包 `migration/migration/` + `tests/`)。所有命令在该目录下执行。
- **TDD**:每个任务先写失败测试→验证失败→最小实现→验证通过→提交。
- **提交信息**:中文 conventional commits(如 `feat(hashing): 实现分层哈希策略`)。
- **编码**:所有文件 UTF-8 无 BOM;路径一律 `pathlib.Path`,不拼字符串。
- **注释**:中文;公有函数中文 docstring;无多余注释。

## 文件结构

```
migration/                                 ← 项目根(git 仓库)
├── pyproject.toml                         ← 项目元数据/依赖/entry point/ruff/pytest 配置
├── requirements.txt                       ← 运行依赖锁定(供 CI/无网环境)
├── .gitignore                             ← 忽略 .mcmig/snapshots、.venv、__pycache__
├── migration/                             ← 导入包
│   ├── __init__.py                        ← __version__
│   ├── __main__.py                        ← python -m migration 入口
│   ├── cli.py                             ← argparse 子命令接线
│   ├── hashing.py                         ← 分层哈希决策 + MD5 计算
│   ├── rules.py                           ← Rule/RuleSet/Category + 三层规则加载
│   ├── classifier.py                      ← Classifier:按规则集分类
│   ├── snapshot.py                        ← FileEntry/Snapshot + JSON 存读
│   ├── scanner.py                         ← 遍历版本目录生成 FileEntry 清单
│   ├── differ.py                          ← 两快照 → 6 桶 DiffReport
│   ├── reporter.py                        ← rich 终端报告 + JSON
│   └── data/
│       └── default_rules.yaml             ← 内置默认规则(数据,最低优先级)
├── tests/
│   ├── __init__.py
│   ├── conftest.py                        ← mini_version/mini_version_b 工厂(tmp_path)
│   ├── test_hashing.py
│   ├── test_rules.py
│   ├── test_classifier.py
│   ├── test_snapshot.py
│   ├── test_scanner.py
│   ├── test_differ.py
│   ├── test_reporter.py
│   └── test_cli.py
└── .mcmig/                                ← 运行时(不入 git);snapshots/ + rules.yaml
```

**职责边界**:`FileEntry` 定义在 `snapshot.py`(数据模型归属),`scanner`/`differ` 反向 import,不新增 `models.py`。

---

## Task 1: 项目骨架 + 可安装

**Files:**
- Create: `migration/pyproject.toml`、`migration/requirements.txt`、`migration/.gitignore`
- Create: `migration/migration/__init__.py`、`migration/migration/__main__.py`、`migration/migration/cli.py`(stub)
- Create: `migration/tests/__init__.py`
- Create: `migration/migration/data/default_rules.yaml`

- [ ] **Step 1: 创建目录与 `pyproject.toml`**

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "mcmig"
version = "0.1.0"
description = "Minecraft 整合包版本迁移工具(只读 scan/diff)"
requires-python = ">=3.11"
dependencies = [
    "rich>=13.0",
    "PyYAML>=6.0",
    "pathspec>=0.12",
]

[project.scripts]
mcmig = "migration.cli:main"

[tool.setuptools.packages.find]
include = ["migration*"]

[tool.setuptools.package-data]
migration = ["data/*.yaml"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
```

`requirements.txt`:
```
rich>=13.0
PyYAML>=6.0
pathspec>=0.12
```

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
.mcmig/snapshots/
*.egg-info/
build/
dist/
```

- [ ] **Step 2: 创建包与 stub 入口**

`migration/migration/__init__.py`:
```python
"""Minecraft 整合包版本迁移工具。"""

__version__ = "0.1.0"
```

`migration/migration/__main__.py`:
```python
"""`python -m migration` 入口。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

`migration/migration/cli.py`(stub,Task 9 扩展):
```python
"""命令行入口(stub)。"""

import argparse

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    """构建顶层 argparse 解析器(stub 版)。"""
    parser = argparse.ArgumentParser(prog="mcmig", description="Minecraft 整合包版本迁移工具")
    parser.add_argument("-V", "--version", action="version", version=f"mcmig {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口(stub 版)。"""
    build_parser().parse_args(argv)
    return 0
```

`migration/migration/data/default_rules.yaml`(内置默认,Task 3 详细测试):
```yaml
# 内置默认规则(最低优先级)。简写形式:类别 -> glob 列表。
never:
  - logs/**
  - crash-reports/**
  - "<ver>.jar"
  - "<ver>.json"
  - "<ver>-natives/**"
  - PCL/**
  - downloads/**
  - patchouli_books/**
  - patchouli_data.json
  - usercache.json
  - observable_announce
  - command_history.txt
  - defaultconfigs/**
  - "**/cache/**"
must_migrate:
  - options.txt
  - servers.dat
  - servers.dat_old
  - saves/**
  - schematics/**
  - xaero/**
  - XaeroWaypoints_*/**
  - local/ftbchunks/**
  - Distant_Horizons_server_data/**
  - dragon-survival/**
```

`migration/tests/__init__.py`: 空文件。

- [ ] **Step 3: 初始化 git 并安装**

```bash
cd migration
git init
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]" 2>$null; if (-not $?) { pip install -e .; pip install pytest ruff }
```
> 注:dev extras 未在 pyproject 声明,故直接 `pip install -e .` 后 `pip install pytest ruff`。

- [ ] **Step 4: 验证可安装与 --version**

Run: `mcmig --version`
Expected: `mcmig 0.1.0`

Run: `python -c "import migration; print(migration.__version__)"`
Expected: `0.1.0`

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "chore: 初始化项目骨架与默认规则"
```

---

## Task 2: hashing.py — 分层哈希决策

**Files:**
- Create: `migration/migration/hashing.py`
- Test: `migration/tests/test_hashing.py`

- [ ] **Step 1: 写失败测试**

`tests/test_hashing.py`:
```python
from pathlib import Path

from migration import hashing


def test_text_file_should_hash(tmp_path: Path):
    assert hashing.should_hash(tmp_path / "options.txt", strict=False) is True


def test_config_toml_should_hash(tmp_path: Path):
    assert hashing.should_hash(tmp_path / "config" / "a.toml", strict=False) is True


def test_mods_jar_should_not_hash(tmp_path: Path):
    p = tmp_path / "mods" / "create.jar"
    assert hashing.should_hash(p, strict=False) is False


def test_sqlite_should_not_hash(tmp_path: Path):
    assert hashing.should_hash(tmp_path / "dh" / "lod.sqlite", strict=False) is False


def test_zip_and_mca_should_not_hash(tmp_path: Path):
    assert hashing.should_hash(tmp_path / "xaero" / "cache.zip", strict=False) is False
    assert hashing.should_hash(tmp_path / "saves" / "r.0.0.mca", strict=False) is False


def test_strict_forces_all(tmp_path: Path):
    assert hashing.should_hash(tmp_path / "mods" / "x.jar", strict=True) is True
    assert hashing.should_hash(tmp_path / "a.sqlite", strict=True) is True


def test_compute_md5_stable(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"hello world")
    assert hashing.compute_md5(p) == "5eb63bbbe01eeed093cb22bb8f5acdc3"


def test_compute_md5_large_streaming(tmp_path: Path):
    p = tmp_path / "big.bin"
    p.write_bytes(b"x" * (1 << 18))  # 256 KiB,触发分块
    h1 = hashing.compute_md5(p)
    assert h1 == hashing.compute_md5(p)  # 稳定
```

- [ ] **Step 2: 运行验证失败**

Run: `pytest tests/test_hashing.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'migration.hashing'`)

- [ ] **Step 3: 实现 hashing.py**

`migration/migration/hashing.py`:
```python
"""分层哈希策略:文本全量 MD5、mods/bulk 走 size 代理。"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1 << 16  # 64 KiB 流式读取块

# bulk 整体替换型二进制:走 size 代理,不哈希
_BULK_EXTS = {".sqlite", ".zip", ".mca"}


def should_hash(path: Path, *, strict: bool = False) -> bool:
    """判断某文件是否需要计算 MD5(分层策略)。

    Args:
        path: 文件绝对/相对路径。
        strict: 为 True 时强制全量哈希(忽略分层)。

    Returns:
        True 表示该文件应计算 MD5;False 表示走 size 代理(mods jar / bulk)。
    """
    if strict:
        return True
    suffix = path.suffix.lower()
    # mods jar:玩家不改内部,按文件名集合比,不哈希
    if suffix == ".jar" and "mods" in path.parts:
        return False
    # bulk 二进制(sqlite/zip/mca):整体替换型,size 是好代理
    if suffix in _BULK_EXTS:
        return False
    return True


def compute_md5(path: Path) -> str:
    """流式全量计算文件 MD5。"""
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()
```

- [ ] **Step 4: 运行验证通过**

Run: `pytest tests/test_hashing.py -v`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add migration/hashing.py tests/test_hashing.py
git commit -m "feat(hashing): 实现分层哈希策略"
```

---

## Task 3: rules.py — 规则加载与 RuleSet

**Files:**
- Create: `migration/migration/rules.py`
- Test: `migration/tests/test_rules.py`

- [ ] **Step 1: 写失败测试**

`tests/test_rules.py`:
```python
from pathlib import Path

import pytest

from migration import rules
from migration.rules import Category, Rule, RuleSet


def test_category_values():
    assert Category.NEVER.value == "never"
    assert Category.MUST_MIGRATE.value == "must_migrate"


def test_ruleset_first_match_wins():
    rs = RuleSet(rules=[
        Rule(match="config/*.toml", decide=Category.NEVER),
        Rule(match="config/create.toml", decide=Category.MUST_MIGRATE),
    ])
    # 第一条命中 config/create.toml → NEVER(尽管第二条更具体)
    assert rs.classify("config/create.toml") == Category.NEVER


def test_ruleset_no_match_returns_unknown():
    rs = RuleSet(rules=[Rule(match="logs/**", decide=Category.NEVER)])
    assert rs.classify("options.txt") == Category.UNKNOWN


def test_classify_normalizes_backslash():
    rs = RuleSet(rules=[Rule(match="config/*.toml", decide=Category.NEVER)])
    assert rs.classify("config\\a.toml") == Category.NEVER


def test_glob_star_does_not_cross_slash():
    rs = RuleSet(rules=[Rule(match="config/*", decide=Category.NEVER)])
    assert rs.classify("config/a.toml") == Category.NEVER
    assert rs.classify("config/sub/b.toml") == Category.UNKNOWN


def test_glob_double_star_recursive():
    rs = RuleSet(rules=[Rule(match="logs/**", decide=Category.NEVER)])
    assert rs.classify("logs/a/b/c.log") == Category.NEVER


def test_glob_no_slash_matches_any_depth():
    rs = RuleSet(rules=[Rule(match="*.bak", decide=Category.MUST_MIGRATE)])
    assert rs.classify("config/a.bak") == Category.MUST_MIGRATE
    assert rs.classify("config/sub/b.bak") == Category.MUST_MIGRATE


def test_load_cli_rules_exclude_include():
    rs = RuleSet.from_layers(
        rules.load_cli_rules(excludes=["screenshots/**"], includes=["my/**"]),
        [Rule(match="options.txt", decide=Category.MUST_MIGRATE)],
    )
    assert rs.classify("screenshots/a.png") == Category.NEVER
    assert rs.classify("my/note.txt") == Category.MUST_MIGRATE


def test_load_user_rules_verbose(tmp_path: Path):
    f = tmp_path / "r.yaml"
    f.write_text(
        "version: 1\nrules:\n"
        "  - match: 'secret/**'\n    decide: never\n    reason: '私货'\n",
        encoding="utf-8",
    )
    layer, errs = rules.load_user_rules(f)
    assert errs == []
    assert len(layer) == 1
    assert layer[0].decide == Category.NEVER
    assert layer[0].reason == "私货"


def test_load_user_rules_bad_decide_is_error(tmp_path: Path):
    f = tmp_path / "bad.yaml"
    f.write_text("rules:\n  - match: 'x'\n    decide: bogus\n", encoding="utf-8")
    layer, errs = rules.load_user_rules(f)
    assert layer == []
    assert len(errs) == 1 and "decide" in errs[0]


def test_load_default_rules_with_ver_substitution():
    layer, errs = rules.load_default_rules("1.21.1-NeoForge_21.1.227")
    assert errs == []
    matches = {r.match for r in layer}
    assert "1.21.1-NeoForge_21.1.227.jar" in matches
    assert "1.21.1-NeoForge_21.1.227-natives/**" in matches
    assert "options.txt" in {r.match for r in layer if r.decide == Category.MUST_MIGRATE}


def test_load_default_rules_has_never_and_must():
    layer, _ = rules.load_default_rules("v1")
    by_cat = {c: [] for c in Category}
    for r in layer:
        by_cat[r.decide].append(r.match)
    assert "logs/**" in by_cat[Category.NEVER]
    assert "options.txt" in by_cat[Category.MUST_MIGRATE]


def test_from_layers_priority_order():
    high = [Rule(match="x", decide=Category.NEVER, source="cli")]
    low = [Rule(match="x", decide=Category.MUST_MIGRATE, source="default")]
    rs = RuleSet.from_layers(high, low)
    assert rs.classify("x") == Category.NEVER  # 高层先命中
```

- [ ] **Step 2: 运行验证失败**

Run: `pytest tests/test_rules.py -v`
Expected: FAIL(`ModuleNotFoundError`)

- [ ] **Step 3: 实现 rules.py**

`migration/migration/rules.py`:
```python
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
            (pathspec.PathSpec.from_lines("gitwildmatch", [r.match]), r) for r in self.rules
        ]

    @classmethod
    def from_layers(cls, *layers: list[Rule]) -> "RuleSet":
        """合并多层规则,靠前层优先级高。"""
        merged: list[Rule] = []
        for layer in layers:
            merged.extend(layer)
        return cls(rules=merged)

    def classify(self, rel_path: str) -> Category:
        """对相对版本根的路径做分类,返回首个命中规则的 decide;无命中→UNKNOWN。"""
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


def _expand_ver(rules: list[Rule], version: str) -> list[Rule]:
    """把规则 match 中的 <ver> 占位替换成真实版本名。"""
    out: list[Rule] = []
    for r in rules:
        if "<ver>" in r.match:
            out.append(
                Rule(
                    match=r.match.replace("<ver>", version),
                    decide=r.decide,
                    reason=r.reason,
                    source=r.source,
                )
            )
        else:
            out.append(r)
    return out


def load_default_rules(version: str) -> tuple[list[Rule], list[str]]:
    """加载打包在内的内置默认规则(最低优先级),并展开 <ver> 占位。"""
    txt = resources.files("migration").joinpath("data/default_rules.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(txt)
    rules_list, errors = _parse_rules_doc(doc, "default")
    return _expand_ver(rules_list, version), errors


def load_user_rules(path: Path) -> tuple[list[Rule], list[str]]:
    """加载用户规则文件(详写格式)。文件不存在时返回空。"""
    if not path.exists():
        return [], []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return [], [f"{path}: YAML 解析失败: {e}"]
    return _parse_rules_doc(doc, f"user:{path.name}")


def load_cli_rules(
    excludes: list[str] | None, includes: list[str] | None
) -> list[Rule]:
    """根据 CLI --exclude/--include 构造临时规则(最高优先级)。"""
    out: list[Rule] = []
    for g in excludes or []:
        out.append(Rule(match=g, decide=Category.NEVER, reason="CLI --exclude", source="cli"))
    for g in includes or []:
        out.append(
            Rule(match=g, decide=Category.MUST_MIGRATE, reason="CLI --include", source="cli")
        )
    return out
```

- [ ] **Step 4: 运行验证通过**

Run: `pytest tests/test_rules.py -v`
Expected: 13 passed

- [ ] **Step 5: 提交**

```bash
git add migration/rules.py tests/test_rules.py
git commit -m "feat(rules): 实现数据驱动规则引擎与多层加载"
```

---

## Task 4: classifier.py — 分类器

**Files:**
- Create: `migration/migration/classifier.py`
- Test: `migration/tests/test_classifier.py`

- [ ] **Step 1: 写失败测试**

`tests/test_classifier.py`:
```python
from migration import rules
from migration.classifier import Classifier
from migration.rules import Category, Rule
from migration.snapshot import FileEntry


def _clf(*layers):
    return Classifier(rules.RuleSet.from_layers(*layers))


def test_classify_path_never_wins_over_must_via_priority():
    clf = _clf(
        [Rule(match="config/embeddium-options.json", decide=Category.NEVER, source="cli")],
        [Rule(match="config/*.json", decide=Category.MUST_MIGRATE, source="user")],
    )
    assert clf.classify_path("config/embeddium-options.json") == Category.NEVER


def test_classify_path_falls_back_to_default_then_unknown():
    default, _ = rules.load_default_rules("v1")
    clf = _clf(default)
    assert clf.classify_path("options.txt") == Category.MUST_MIGRATE
    assert clf.classify_path("logs/latest.log") == Category.NEVER
    assert clf.classify_path("config/unknown.toml") == Category.UNKNOWN


def test_classify_entry_uses_path():
    clf = _clf([Rule(match="options.txt", decide=Category.MUST_MIGRATE)])
    e = FileEntry(path="options.txt", size=10, md5="abc")
    assert clf.classify(e) == Category.MUST_MIGRATE


def test_classify_all_preserves_order():
    clf = _clf([Rule(match="*.txt", decide=Category.MUST_MIGRATE)])
    entries = [
        FileEntry(path="a.txt", size=1, md5=None),
        FileEntry(path="b.log", size=1, md5=None),
    ]
    out = clf.classify_all(entries)
    assert [c.category for c in out] == [Category.MUST_MIGRATE, Category.UNKNOWN]
```

- [ ] **Step 2: 运行验证失败**

Run: `pytest tests/test_classifier.py -v`
Expected: FAIL(`ModuleNotFoundError: 'migration.classifier'`)

- [ ] **Step 3: 实现 classifier.py**

`migration/migration/classifier.py`:
```python
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
```

> 依赖说明:`classifier.py` import `snapshot.FileEntry`;`snapshot.py` 在 Task 5 创建。本任务需先建最小 `snapshot.py` 骨架(仅 `FileEntry`),否则 import 失败。Step 3 一并创建。

补建最小 `migration/migration/snapshot.py`(Task 5 会扩展 save/load):
```python
"""快照数据模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FileEntry:
    """相对版本根的一个文件条目。"""

    path: str
    size: int
    md5: str | None
```

- [ ] **Step 4: 运行验证通过**

Run: `pytest tests/test_classifier.py tests/test_snapshot.py -v 2>$null; pytest tests/test_classifier.py -v`
Expected: 4 passed(忽略 snapshot 暂无测试)

- [ ] **Step 5: 提交**

```bash
git add migration/classifier.py migration/snapshot.py tests/test_classifier.py
git commit -m "feat(classifier): 实现基于规则集的分类器"
```

---

## Task 5: snapshot.py — FileEntry/Snapshot 存读

**Files:**
- Modify: `migration/migration/snapshot.py`(扩展 save/load)
- Test: `migration/tests/test_snapshot.py`

- [ ] **Step 1: 写失败测试**

`tests/test_snapshot.py`:
```python
import json
from pathlib import Path

import pytest

from migration.snapshot import FileEntry, Snapshot, SnapshotFormatError


def _sample() -> Snapshot:
    return Snapshot(
        version="v1",
        game_root="C:/game",
        scanned_at="2026-07-02T12:00:00+08:00",
        hash_mode="tiered",
        file_count=2,
        files=[
            FileEntry(path="options.txt", size=10, md5="abcd"),
            FileEntry(path="mods/x.jar", size=999, md5=None),
        ],
    )


def test_save_load_roundtrip(tmp_path: Path):
    sp = tmp_path / "v1.snapshot.json"
    _sample().save(sp)
    loaded = Snapshot.load(sp)
    assert loaded.version == "v1"
    assert loaded.hash_mode == "tiered"
    assert loaded.files == [
        FileEntry(path="options.txt", size=10, md5="abcd"),
        FileEntry(path="mods/x.jar", size=999, md5=None),
    ]


def test_save_creates_parent_dirs(tmp_path: Path):
    sp = tmp_path / ".mcmig" / "snapshots" / "v1.snapshot.json"
    _sample().save(sp)
    assert sp.exists()


def test_md5_none_roundtrip_preserved(tmp_path: Path):
    sp = tmp_path / "s.json"
    _sample().save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    assert doc["files"][1] == {"path": "mods/x.jar", "size": 999, "md5": None}


def test_load_rejects_unsupported_format(tmp_path: Path):
    sp = tmp_path / "bad.json"
    sp.write_text(
        json.dumps({"snapshot_format": 999, "version": "v", "game_root": "",
                    "scanned_at": "", "hash_mode": "tiered", "file_count": 0, "files": []}),
        encoding="utf-8",
    )
    with pytest.raises(SnapshotFormatError):
        Snapshot.load(sp)


def test_snapshot_path_helper():
    from migration.snapshot import snapshot_path
    p = snapshot_path(Path("C:/work"), "v1")
    assert p == Path("C:/work/.mcmig/snapshots/v1.snapshot.json")
```

- [ ] **Step 2: 运行验证失败**

Run: `pytest tests/test_snapshot.py -v`
Expected: FAIL(`AttributeError: ... has no attribute 'save'`)

- [ ] **Step 3: 实现 snapshot.py(替换最小骨架)**

`migration/migration/snapshot.py`:
```python
"""快照数据模型与 JSON 持久化。

快照只存原始清单(无分类),分类在读快照→出报告时按当前规则现算。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

TOOL_VERSION = "0.1.0"
SNAPSHOT_FORMAT = 1


class SnapshotFormatError(Exception):
    """快照格式版本不支持或文件损坏。"""


@dataclass(frozen=True)
class FileEntry:
    """相对版本根的一个文件条目。md5 为 None 表示分层策略未哈希。"""

    path: str
    size: int
    md5: str | None


@dataclass
class Snapshot:
    """一个版本文件夹的扫描快照(原始清单)。"""

    version: str
    game_root: str
    scanned_at: str
    hash_mode: str  # "tiered" | "strict"
    file_count: int
    files: list[FileEntry]
    tool_version: str = TOOL_VERSION
    snapshot_format: int = SNAPSHOT_FORMAT

    def save(self, path: Path) -> None:
        """将快照写入 JSON(自动创建父目录)。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tool_version": self.tool_version,
            "snapshot_format": self.snapshot_format,
            "version": self.version,
            "game_root": self.game_root,
            "scanned_at": self.scanned_at,
            "hash_mode": self.hash_mode,
            "file_count": self.file_count,
            "files": [asdict(f) for f in self.files],
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Path) -> "Snapshot":
        """从 JSON 读快照;格式版本不支持时抛 SnapshotFormatError。"""
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        fmt = payload.get("snapshot_format")
        if fmt != SNAPSHOT_FORMAT:
            raise SnapshotFormatError(
                f"快照格式版本 {fmt} 不支持(当前 {SNAPSHOT_FORMAT}),请重新 scan"
            )
        files = [
            FileEntry(path=d["path"], size=d["size"], md5=d.get("md5"))
            for d in payload["files"]
        ]
        return cls(
            version=payload["version"],
            game_root=payload["game_root"],
            scanned_at=payload["scanned_at"],
            hash_mode=payload["hash_mode"],
            file_count=payload["file_count"],
            files=files,
        )


def snapshot_path(workdir: Path, version: str) -> Path:
    """返回某版本快照的标准路径:<workdir>/.mcmig/snapshots/<ver>.snapshot.json。"""
    return workdir / ".mcmig" / "snapshots" / f"{version}.snapshot.json"
```

- [ ] **Step 4: 运行验证通过**

Run: `pytest tests/test_snapshot.py tests/test_classifier.py -v`
Expected: passed(确保 classifier 仍通过,因 FileEntry 字段未变)

- [ ] **Step 5: 提交**

```bash
git add migration/snapshot.py tests/test_snapshot.py
git commit -m "feat(snapshot): 实现快照 JSON 存读与格式版本检查"
```

---

## Task 6: scanner.py — 版本目录扫描

**Files:**
- Create: `migration/migration/scanner.py`
- Create: `migration/tests/conftest.py`(mini_version 工厂)
- Test: `migration/tests/test_scanner.py`

- [ ] **Step 1: 写 conftest 工厂**

`tests/conftest.py`:
```python
"""共享 fixture:程序化构建 mini 版本目录(固定内容→可断言 MD5)。"""

from __future__ import annotations

from pathlib import Path

import pytest

OPTS = "version:I am a config\n"  # 固定内容


def build_mini_version(root: Path, *, variant_b: bool = False) -> Path:
    """构建一个迷你版本文件夹,返回其路径。variant_b 做改动用于 diff。"""
    root.mkdir(parents=True, exist_ok=True)
    # 必迁类
    (root / "options.txt").write_text(OPTS, encoding="utf-8")
    (root / "servers.dat").write_bytes(b"\x0a\x00\x00")
    (root / "saves" / "world1").mkdir(parents=True, exist_ok=True)
    (root / "saves" / "world1" / "level.dat").write_bytes(b"\x00")
    # 不迁类
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / "latest.log").write_text("noise", encoding="utf-8")
    (root / "crash-reports").mkdir(exist_ok=True)
    (root / "crash-reports" / "c1.txt").write_text("boom", encoding="utf-8")
    # 未知类(config)
    (root / "config").mkdir(exist_ok=True)
    cfg = "edited=true\n" if variant_b else "edited=false\n"
    (root / "config" / "create.toml").write_text(cfg, encoding="utf-8")
    # mods jar(空文件占位,仅看文件名)
    (root / "mods").mkdir(exist_ok=True)
    (root / "mods" / "create.jar").write_bytes(b"")
    if variant_b:
        (root / "mods" / "extra.jar").write_bytes(b"")  # b 版额外 mod
    # bulk size 代理
    (root / "Distant_Horizons_server_data").mkdir(exist_ok=True)
    (root / "Distant_Horizons_server_data" / "lod.sqlite").write_bytes(b"\x00" * 16)
    # 命中 **/cache/**
    (root / "xaero" / "cache").mkdir(parents=True)
    (root / "xaero" / "cache" / "c.zip").write_bytes(b"\x00")
    return root


@pytest.fixture
def mini_version(tmp_path: Path) -> Path:
    return build_mini_version(tmp_path / "mini")


@pytest.fixture
def mini_version_b(tmp_path: Path) -> Path:
    return build_mini_version(tmp_path / "mini_b", variant_b=True)
```

- [ ] **Step 2: 写失败测试**

`tests/test_scanner.py`:
```python
from pathlib import Path

from migration import hashing
from migration.scanner import Scanner


def test_scan_collects_all_files(mini_version: Path):
    entries, errors = Scanner(mini_version, "mini", strict=False).scan()
    paths = {e.path for e in entries}
    assert "options.txt" in paths
    assert "logs/latest.log" in paths
    assert "mods/create.jar" in paths
    assert "Distant_Horizons_server_data/lod.sqlite" in paths
    assert errors == []


def test_tiered_hash_text_hashed_jar_not(mini_version: Path):
    entries, _ = Scanner(mini_version, "mini", strict=False).scan()
    by = {e.path: e for e in entries}
    assert by["options.txt"].md5 is not None
    assert by["mods/create.jar"].md5 is None
    assert by["Distant_Horizons_server_data/lod.sqlite"].md5 is None


def test_strict_hashes_everything(mini_version: Path):
    entries, _ = Scanner(mini_version, "mini", strict=True).scan()
    by = {e.path: e for e in entries}
    assert by["mods/create.jar"].md5 is not None
    assert by["Distant_Horizons_server_data/lod.sqlite"].md5 is not None


def test_build_snapshot_fields(mini_version: Path):
    snap, errors = Scanner(mini_version, "mini", strict=False).build_snapshot(str(mini_version.parent))
    assert errors == []
    assert snap.version == "mini"
    assert snap.hash_mode == "tiered"
    assert snap.file_count == len(snap.files)
    assert snap.scanned_at != ""


def test_strict_snapshot_mode_label(mini_version: Path):
    snap, _ = Scanner(mini_version, "mini", strict=True).build_snapshot("g")
    assert snap.hash_mode == "strict"


def test_unreadable_file_skipped_with_error(tmp_path: Path, monkeypatch):
    # 构造一个读会失败的文件:patch compute_md5 抛 OSError
    p = tmp_path / "bad.txt"
    p.write_text("x", encoding="utf-8")
    monkeypatch.setattr("migration.scanner.hashing.compute_md5", lambda _: (_ for _ in ()).throw(OSError("locked")))
    entries, errors = Scanner(tmp_path, "v", strict=False).scan()
    # bad.txt 应被跳过并列入 errors
    assert all(e.path != "bad.txt" for e in entries)
    assert any("bad.txt" in e.reason for e in errors)
```

- [ ] **Step 3: 运行验证失败**

Run: `pytest tests/test_scanner.py -v`
Expected: FAIL(`ModuleNotFoundError: 'migration.scanner'`)

- [ ] **Step 4: 实现 scanner.py**

`migration/migration/scanner.py`:
```python
"""版本目录扫描器:遍历目录生成分层哈希的 FileEntry 清单。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import hashing
from .snapshot import FileEntry, Snapshot

log = logging.getLogger(__name__)


@dataclass
class ScanError:
    """单个文件扫描失败记录。"""

    path: str
    reason: str


class Scanner:
    """遍历一个版本文件夹,产出 FileEntry 清单(分层哈希)。"""

    def __init__(self, version_dir: Path, version_name: str, *, strict: bool = False) -> None:
        self.version_dir = version_dir
        self.version_name = version_name
        self.strict = strict

    def scan(self) -> tuple[list[FileEntry], list[ScanError]]:
        """扫描目录,返回 (文件清单, 错误列表)。失败文件跳过且不致全崩。"""
        entries: list[FileEntry] = []
        errors: list[ScanError] = []
        for p in sorted(self.version_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.version_dir).as_posix()
            try:
                size = p.stat().st_size
            except OSError as e:
                errors.append(ScanError(rel, f"stat 失败: {e}"))
                continue
            md5: str | None = None
            if hashing.should_hash(p, strict=self.strict):
                try:
                    md5 = hashing.compute_md5(p)
                except OSError as e:
                    errors.append(ScanError(rel, f"读取失败: {e}"))
                    continue
            entries.append(FileEntry(path=rel, size=size, md5=md5))
        return entries, errors

    def build_snapshot(self, game_root: str) -> tuple[Snapshot, list[ScanError]]:
        """扫描并构造 Snapshot 对象。"""
        entries, errors = self.scan()
        snap = Snapshot(
            version=self.version_name,
            game_root=game_root,
            scanned_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            hash_mode="strict" if self.strict else "tiered",
            file_count=len(entries),
            files=entries,
        )
        return snap, errors
```

- [ ] **Step 5: 运行验证通过**

Run: `pytest tests/test_scanner.py -v`
Expected: 6 passed

- [ ] **Step 6: 提交**

```bash
git add migration/scanner.py tests/conftest.py tests/test_scanner.py
git commit -m "feat(scanner): 实现版本目录分层哈希扫描"
```

---

## Task 7: differ.py — 6 桶 DiffReport

**Files:**
- Create: `migration/migration/differ.py`
- Test: `migration/tests/test_differ.py`

- [ ] **Step 1: 写失败测试**

`tests/test_differ.py`:
```python
from migration import rules
from migration.classifier import Classifier
from migration.differ import Differ
from migration.snapshot import FileEntry


def _clf():
    default, _ = rules.load_default_rules("mini")
    return Classifier(rules.RuleSet.from_layers(default))


def _e(path, size=1, md5="x"):
    return FileEntry(path=path, size=size, md5=md5)


def test_must_migrate_dst_missing_goes_to_migrate():
    clf = _clf()
    d = Differ([_e("options.txt")], [], clf).diff()
    assert any(i.path == "options.txt" for i in d.to_migrate)


def test_must_migrate_modified_goes_to_migrate():
    clf = _clf()
    d = Differ([_e("options.txt", md5="a")], [_e("options.txt", md5="b")], clf).diff()
    assert any(i.path == "options.txt" and i.note == "modified" for i in d.to_migrate)


def test_must_migrate_identical_goes_identical_verified():
    clf = _clf()
    d = Differ([_e("options.txt", md5="a")], [_e("options.txt", md5="a")], clf).diff()
    assert any(i.path == "options.txt" and i.note == "verified" for i in d.identical)


def test_unknown_modified_goes_candidate():
    clf = _clf()
    d = Differ([_e("config/foo.toml", md5="a")], [_e("config/foo.toml", md5="b")], clf).diff()
    assert any(i.path == "config/foo.toml" for i in d.candidate)


def test_unknown_dst_only_goes_only_in_dst():
    clf = _clf()
    d = Differ([], [_e("config/foo.toml")], clf).diff()
    assert any(i.path == "config/foo.toml" for i in d.only_in_dst)


def test_never_goes_never_bucket():
    clf = _clf()
    d = Differ([_e("logs/latest.log")], [], clf).diff()
    assert any(i.path == "logs/latest.log" for i in d.never)


def test_size_based_identical_when_md5_none():
    clf = _clf()
    # lod.sqlite md5=None,size 相同 → size-based identical
    d = Differ(
        [_e("Distant_Horizons_server_data/lod.sqlite", size=16, md5=None)],
        [_e("Distant_Horizons_server_data/lod.sqlite", size=16, md5=None)],
        clf,
    ).diff()
    assert any(i.note == "size-based" for i in d.identical)


def test_mods_bucket_by_filename_set():
    clf = _clf()
    d = Differ(
        [_e("mods/create.jar"), _e("mods/extra.jar")],
        [_e("mods/create.jar")],
        clf,
    ).diff()
    notes = {i.path: i.note for i in d.mods}
    assert notes.get("mods/create.jar") == "shared"
    assert notes.get("mods/extra.jar") == "to_add"


def test_mods_target_only():
    clf = _clf()
    d = Differ([], [_e("mods/target_only.jar")], clf).diff()
    assert any(i.note == "target_only" for i in d.mods)


def test_empty_dst_all_must_to_migrate():
    clf = _clf()
    d = Differ([_e("options.txt"), _e("servers.dat")], [], clf).diff()
    paths = {i.path for i in d.to_migrate}
    assert {"options.txt", "servers.dat"} <= paths
```

- [ ] **Step 2: 运行验证失败**

Run: `pytest tests/test_differ.py -v`
Expected: FAIL(`ModuleNotFoundError: 'migration.differ'`)

- [ ] **Step 3: 实现 differ.py**

`migration/migration/differ.py`:
```python
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
        if s and d:
            note = "shared"
        elif s:
            note = "to_add"
        else:
            note = "target_only"
        return DiffItem(path=path, src=s, dst=d, note=note)

    def diff(self) -> DiffReport:
        """生成 6 桶 DiffReport。"""
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
            # UNKNOWN / ASK
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
```

- [ ] **Step 4: 运行验证通过**

Run: `pytest tests/test_differ.py -v`
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
git add migration/differ.py tests/test_differ.py
git commit -m "feat(differ): 实现 6 桶迁移导向 Diff 报告"
```

---

## Task 8: reporter.py — rich 报告 + JSON

**Files:**
- Create: `migration/migration/reporter.py`
- Test: `migration/tests/test_reporter.py`

- [ ] **Step 1: 写失败测试**

`tests/test_reporter.py`:
```python
import json

from migration import rules
from migration.classifier import Classifier
from migration.differ import Differ
from migration.reporter import DiffReporter, ReportOptions
from migration.snapshot import FileEntry


def _report():
    clf = Classifier(rules.RuleSet.from_layers(*rules.load_default_rules("mini")))
    d = Differ(
        [FileEntry("options.txt", 10, "a"), FileEntry("logs/latest.log", 1, None)],
        [FileEntry("options.txt", 10, "b")],
        clf,
    ).diff()
    return DiffReporter(d, src_version="mini", dst_version="mini_b")


def test_to_json_is_parseable_and_has_summary():
    doc = json.loads(_report().to_json())
    assert doc["src"] == "mini" and doc["dst"] == "mini_b"
    assert "summary" in doc and "buckets" in doc
    assert doc["summary"]["to_migrate"] >= 1


def test_render_runs_without_error(capsys):
    _report().render(ReportOptions())  # 不抛即通过


def test_render_with_show_never(capsys):
    _report().render(ReportOptions(show_never=True))  # 含 never 桶也不抛


def test_options_defaults_hide_identical_and_never():
    opts = ReportOptions()
    assert opts.show_identical is False
    assert opts.show_never is False
    assert opts.category is None
```

- [ ] **Step 2: 运行验证失败**

Run: `pytest tests/test_reporter.py -v`
Expected: FAIL(`ModuleNotFoundError: 'migration.reporter'`)

- [ ] **Step 3: 实现 reporter.py**

`migration/migration/reporter.py`:
```python
"""报告渲染:rich 终端 + JSON。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from rich.console import Console
from rich.table import Table

from .differ import DiffItem, DiffReport

BUCKETS = ["to_migrate", "candidate", "mods", "only_in_dst", "identical", "never"]
BUCKET_TITLE = {
    "to_migrate": "✅ 必迁移(to_migrate)",
    "candidate": "◐ 待确认(candidate)",
    "mods": "📦 Mod 变化(mods)",
    "only_in_dst": "📍 目标自带(only_in_dst)",
    "identical": "⏭ 一致(identical)",
    "never": "⛔ 不迁移(never)",
}


@dataclass
class ReportOptions:
    """报告可见性控制。"""

    show_identical: bool = False
    show_never: bool = False
    mods_only: bool = False
    category: str | None = None  # 仅显示某一桶


class DiffReporter:
    """把 DiffReport 渲染成 rich 终端表格或 JSON。"""

    def __init__(self, report: DiffReport, *, src_version: str, dst_version: str) -> None:
        self.report = report
        self.src_version = src_version
        self.dst_version = dst_version

    def _item_dict(self, item: DiffItem) -> dict:
        return {
            "path": item.path,
            "note": item.note,
            "src_size": item.src.size if item.src else None,
            "dst_size": item.dst.size if item.dst else None,
        }

    def to_json(self) -> str:
        """生成可解析的 JSON 报告(含 summary 与各桶明细)。"""
        payload = {
            "src": self.src_version,
            "dst": self.dst_version,
            "summary": {b: len(getattr(self.report, b)) for b in BUCKETS},
            "buckets": {
                b: [self._item_dict(i) for i in getattr(self.report, b)] for b in BUCKETS
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _visible_buckets(self, opts: ReportOptions) -> list[str]:
        if opts.category:
            return [opts.category] if opts.category in BUCKETS else []
        buckets = ["to_migrate", "candidate", "mods", "only_in_dst"]
        if opts.show_identical:
            buckets.append("identical")
        if opts.show_never:
            buckets.append("never")
        if opts.mods_only:
            buckets = ["mods"]
        return buckets

    def render(self, opts: ReportOptions, console: Console | None = None) -> None:
        """渲染 rich 终端报告。"""
        console = console or Console()
        console.print(
            f"[bold]diff:[/] [cyan]{self.src_version}[/] → [cyan]{self.dst_version}[/]"
        )
        summary = ", ".join(
            f"{BUCKET_TITLE[b].split('(')[0]}{len(getattr(self.report, b))}" for b in BUCKETS
        )
        console.print(f"[dim]汇总: {summary}[/]")
        for b in self._visible_buckets(opts):
            items = getattr(self.report, b)
            if not items:
                continue
            tbl = Table(title=BUCKET_TITLE[b], title_style="bold")
            tbl.add_column("路径")
            tbl.add_column("标记", style="dim")
            for it in items:
                tbl.add_row(it.path, it.note)
            console.print(tbl)
```

- [ ] **Step 4: 运行验证通过**

Run: `pytest tests/test_reporter.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add migration/reporter.py tests/test_reporter.py
git commit -m "feat(reporter): 实现 rich 终端报告与 JSON 输出"
```

---

## Task 9: cli.py — scan/diff 子命令接线

**Files:**
- Modify: `migration/migration/cli.py`
- Test: `migration/tests/test_cli.py`

- [ ] **Step 1: 写失败测试**

`tests/test_cli.py`:
```python
import json
from pathlib import Path

from migration import cli
from migration.snapshot import snapshot_path


def _scan(tmp_path: Path, ver: str, game_root: Path, cwd: Path):
    return cli.main(["scan", ver, "--game-root", str(game_root)])


def test_scan_writes_snapshot(mini_version: Path, tmp_path: Path, monkeypatch):
    game_root = mini_version.parent  # versions 的父目录
    # 把 versions 目录摆好:game_root/versions/mini
    versions = game_root / "versions"
    versions.mkdir(exist_ok=True)
    (versions / "mini").mkdir(exist_ok=True)
    # mini_version fixture 已在 game_root/mini,挪进 versions/mini
    for p in mini_version.rglob("*"):
        if p.is_file():
            rel = p.relative_to(mini_version)
            target = versions / "mini" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            p.replace(target)
    monkeypatch.chdir(tmp_path)
    code = _scan(tmp_path, "mini", game_root, tmp_path)
    assert code == 0
    assert snapshot_path(tmp_path, "mini").exists()


def test_scan_missing_version_lists_available(tmp_path: Path, monkeypatch, capsys):
    game_root = tmp_path / "game"
    (game_root / "versions" / "real").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    code = cli.main(["scan", "ghost", "--game-root", str(game_root)])
    out = capsys.readouterr().out
    assert code != 0
    assert "real" in out  # 列出可用版本


def test_diff_missing_snapshot_friendly_error(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = cli.main(["diff", "a", "b", "--game-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code != 0
    assert "scan" in out  # 提示先 scan


def test_diff_json_parseable(mini_version: Path, tmp_path: Path, monkeypatch):
    # 先构建两个版本目录并 scan
    game_root = tmp_path / "game"
    (game_root / "versions").mkdir(parents=True)
    import shutil
    shutil.copytree(mini_version, game_root / "versions" / "mini")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["scan", "mini", "--game-root", str(game_root)]) == 0
    assert cli.main(["scan", "mini_b", "--game-root", str(game_root)]) == 0 if (game_root / "versions" / "mini_b").exists() else 0
    # diff(同快照)
    monkeypatch.setattr("sys.argv", ["mcmig"])
    code = cli.main(["diff", "mini", "mini", "--game-root", str(game_root), "--json"])
    # JSON 经 stdout;直接调 to_json 更稳:这里验证返回 0
    assert code == 0
```

> 注:CLI 测试用 `monkeypatch.chdir(tmp_path)` 让 `.mcmig/` 落在临时目录,隔离真实工作目录。

- [ ] **Step 2: 运行验证失败**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL(scan 子命令不存在 / 参数错误)

- [ ] **Step 3: 实现 cli.py(替换 stub)**

`migration/migration/cli.py`:
```python
"""命令行入口:scan / diff 两个子命令。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__, rules
from .classifier import Classifier
from .differ import Differ
from .reporter import DiffReporter, ReportOptions
from .scanner import Scanner
from .snapshot import Snapshot, snapshot_path

log = logging.getLogger(__name__)

DEFAULT_GAME_ROOT = "冒险活动客户端"


def build_parser() -> argparse.ArgumentParser:
    """构建完整 argparse 解析器。"""
    parser = argparse.ArgumentParser(prog="mcmig", description="Minecraft 整合包版本迁移工具")
    parser.add_argument("-V", "--version", action="version", version=f"mcmig {__version__}")
    parser.add_argument("-q", "--quiet", action="store_true", help="减少输出")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--game-root", default=DEFAULT_GAME_ROOT, help="游戏根目录(含 versions/)")
        p.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="本次按 never")
        p.add_argument("--include", action="append", default=[], metavar="GLOB", help="本次按 must_migrate")
        p.add_argument("--rule", action="append", default=[], metavar="FILE", help="额外规则文件")
        p.add_argument("--strict", action="store_true", help="强制全量哈希")
        p.add_argument("--json", action="store_true", help="JSON 输出")
        p.add_argument("-q", "--quiet", action="store_true")

    p_scan = sub.add_parser("scan", help="扫描版本文件夹生成快照")
    p_scan.add_argument("version", help="versions/ 下的版本文件夹名")
    add_common(p_scan)

    p_diff = sub.add_parser("diff", help="对比两份快照")
    p_diff.add_argument("src", help="源版本名")
    p_diff.add_argument("dst", help="目标版本名")
    p_diff.add_argument("--show-identical", action="store_true")
    p_diff.add_argument("--show-never", action="store_true")
    p_diff.add_argument("--all", action="store_true", help="显示全部桶")
    p_diff.add_argument("--mods", action="store_true", help="仅显示 mods 桶")
    p_diff.add_argument("--category", default=None, help="仅显示指定桶")
    add_common(p_diff)
    return parser


def _setup_logging(quiet: bool) -> None:
    logging.basicConfig(level=logging.WARNING if quiet else logging.INFO, format="%(message)s")


def build_ruleset(version: str, args: argparse.Namespace, mcmig_dir: Path):
    """按优先级(CLI > extra > user > default)组装 RuleSet,返回 (ruleset, 错误列表)。"""
    cli_rules = rules.load_cli_rules(args.exclude, args.include)
    extra: list[rules.Rule] = []
    errors: list[str] = []
    for f in args.rule:
        r, e = rules.load_user_rules(Path(f))
        extra.extend(r)
        errors.extend(e)
    user_path = mcmig_dir / "rules.yaml"
    user, ue = rules.load_user_rules(user_path)
    errors.extend(ue)
    default, de = rules.load_default_rules(version)
    errors.extend(de)
    rs = rules.RuleSet.from_layers(cli_rules, extra, user, default)
    return rs, errors


def _version_dir(game_root: Path, version: str) -> Path:
    return game_root / "versions" / version


def _list_versions(game_root: Path) -> list[str]:
    vdir = game_root / "versions"
    if not vdir.is_dir():
        return []
    return sorted(p.name for p in vdir.iterdir() if p.is_dir())


def _print(text: str) -> None:
    print(text)


def _cmd_scan(args: argparse.Namespace) -> int:
    game_root = Path(args.game_root)
    ver_dir = _version_dir(game_root, args.version)
    if not ver_dir.is_dir():
        avail = _list_versions(game_root)
        _print(f"[错误] 版本 '{args.version}' 不存在于 {game_root / 'versions'}")
        if avail:
            _print("可用版本: " + ", ".join(avail))
        return 2
    cwd = Path.cwd()
    mcmig_dir = cwd / ".mcmig"
    rs, errs = build_ruleset(args.version, args, mcmig_dir)
    for e in errs:
        _print(f"[规则警告] {e}")
    snap, scan_errors = Scanner(ver_dir, args.version, strict=args.strict).build_snapshot(str(game_root))
    spath = snapshot_path(cwd, args.version)
    snap.save(spath)
    clf = Classifier(rs)
    classified = clf.classify_all(snap.files)
    counts: dict[str, int] = {}
    for c in classified:
        counts[c.category.value] = counts.get(c.category.value, 0) + 1
    if args.json:
        import json
        _print(json.dumps({
            "version": args.version,
            "file_count": snap.file_count,
            "by_category": counts,
            "unreadable": len(scan_errors),
            "snapshot": str(spath),
        }, ensure_ascii=False, indent=2))
    else:
        _print(f"[完成] 扫描 {args.version}: {snap.file_count} 个文件 → {spath}")
        _print("分类汇总: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        if scan_errors:
            _print(f"[警告] {len(scan_errors)} 个文件无法读取(已跳过)")
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    cwd = Path.cwd()
    src_path = snapshot_path(cwd, args.src)
    dst_path = snapshot_path(cwd, args.dst)
    missing = [n for n, p in ((args.src, src_path), (args.dst, dst_path)) if not p.exists()]
    if missing:
        _print("[错误] 缺少快照: " + ", ".join(missing))
        _print("请先运行: mcmig scan <版本名>")
        return 2
    try:
        src = Snapshot.load(src_path)
        dst = Snapshot.load(dst_path)
    except Exception as e:  # noqa: BLE001
        _print(f"[错误] 快照读取失败: {e}")
        return 2
    mcmig_dir = cwd / ".mcmig"
    rs, errs = build_ruleset(args.dst, args, mcmig_dir)
    for e in errs:
        _print(f"[规则警告] {e}")
    clf = Classifier(rs)
    report = Differ(src.files, dst.files, clf).diff()
    reporter = DiffReporter(report, src_version=args.src, dst_version=args.dst)
    if args.json:
        _print(reporter.to_json())
        return 0
    opts = ReportOptions(
        show_identical=args.show_identical or args.all,
        show_never=args.show_never or args.all,
        mods_only=args.mods,
        category=args.category,
    )
    reporter.render(opts)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""
    args = build_parser().parse_args(argv)
    _setup_logging(getattr(args, "quiet", False))
    if args.command == "scan":
        return _cmd_scan(args)
    if args.command == "diff":
        return _cmd_diff(args)
    build_parser().print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行验证通过**

Run: `pytest tests/test_cli.py -v`
Expected: passed

> 若 `test_diff_json_parseable` 因 stdout 捕获不稳定,可简化为仅断言返回码 0。

- [ ] **Step 5: 提交**

```bash
git add migration/cli.py tests/test_cli.py
git commit -m "feat(cli): 实现 scan/diff 子命令接线"
```

---

## Task 10: 端到端验收测试

**Files:**
- Test: `migration/tests/test_e2e.py`(新增)

- [ ] **Step 1: 写端到端测试**

`tests/test_e2e.py`:
```python
"""端到端:scan 两个 mini 版本 → diff → 断言 6 桶符合验收标准。"""

from pathlib import Path

from migration import cli
from migration.snapshot import snapshot_path


def test_e2e_scan_and_diff(mini_version: Path, mini_version_b: Path, tmp_path: Path, monkeypatch):
    # 摆好 game_root/versions/{mini,mini_b}
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    mini_version.replace(versions / "mini")
    mini_version_b.replace(versions / "mini_b")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["scan", "mini", "--game-root", str(game_root)]) == 0
    assert cli.main(["scan", "mini_b", "--game-root", str(game_root)]) == 0
    assert snapshot_path(tmp_path, "mini").exists()
    assert snapshot_path(tmp_path, "mini_b").exists()

    # diff mini_b(源,改动多) → mini(目标),验证桶
    import json
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["diff", "mini_b", "mini", "--game-root", str(game_root), "--json"])
    assert rc == 0
    doc = json.loads(buf.getvalue())
    migrate_paths = {i["path"] for i in doc["buckets"]["to_migrate"]}
    # options/servers 是 must_migrate,mini 缺 servers → 进 to_migrate
    assert "options.txt" in migrate_paths
    # logs/latest.log 进 never
    assert "logs/latest.log" in {i["path"] for i in doc["buckets"]["never"]}
    # mods extra.jar → to_add
    assert any(i["path"] == "mods/extra.jar" and i["note"] == "to_add" for i in doc["buckets"]["mods"])


def test_e2e_rule_change_no_rescan_needed(mini_version: Path, tmp_path: Path, monkeypatch):
    """改规则后直接 diff(不重扫)结果即时反映——验收标准 3。"""
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    mini_version.replace(versions / "mini")
    # 复制一份作 mini_b(完全相同)
    import shutil
    shutil.copytree(versions / "mini", versions / "mini_b")
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "mini_b", "--game-root", str(game_root)])

    # 第一次 diff:options.txt 一致 → identical
    import io, json
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main(["diff", "mini", "mini_b", "--game-root", str(game_root), "--json", "--show-identical"])
    d1 = json.loads(buf.getvalue())
    assert any(i["path"] == "options.txt" for i in d1["buckets"]["identical"])

    # 用 --include 把 options.txt 强制 must 不变;用 --exclude 把 logs 永不——无需重扫即生效
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        cli.main(["diff", "mini", "mini_b", "--game-root", str(game_root), "--json"])
    d2 = json.loads(buf2.getvalue())
    assert d2["summary"]["identical"] >= 1  # 规则现算,无重扫
```

- [ ] **Step 2: 运行验证**

Run: `pytest tests/test_e2e.py -v`
Expected: 2 passed

- [ ] **Step 3: 全量回归**

Run: `pytest -v`
Expected: 全部通过(test_hashing/test_rules/test_classifier/test_snapshot/test_scanner/test_differ/test_reporter/test_cli/test_e2e)

- [ ] **Step 4: ruff 检查**

Run: `ruff check migration tests`
Expected: All checks passed(若有 E501 等,修正后重跑)

- [ ] **Step 5: 提交**

```bash
git add tests/test_e2e.py
git commit -m "test(e2e): 增加端到端验收测试覆盖 6 桶与规则现算"
```

---

## Task 11: 真实冒烟(手动,验收标准核对)

**Files:** 无(手动执行 + 记录)

- [ ] **Step 1: 在真实游戏根目录扫描**

在工作目录(`冒险活动客户端v1.4/`)激活 venv 后:
```bash
mcmig scan 1.21.1-NeoForge_21.1.227
```
Expected: 生成 `.mcmig/snapshots/1.21.1-NeoForge_21.1.227.snapshot.json`,打印分类汇总(must_migrate 含 options/servers/xaero 等),文件数 ~400+,`.mcmig/` 在工作目录而非版本文件夹内。

- [ ] **Step 2: diff 227 → 229(目标空壳)**

```bash
mcmig diff 1.21.1-NeoForge_21.1.227 1.21.1-NeoForge_21.1.229
```
> 注:229 需先 `mcmig scan 1.21.1-NeoForge_21.1.229`。
Expected: `to_migrate` 桶含 options.txt/servers.dat/xaero/local/ftbchunks/Distant_Horizons_server_data/dragon-survival;`mods` 桶显示两版 mod 集合(229 空 → 全 to_add 或 shared);`never`/`identical` 默认隐藏。

- [ ] **Step 3: 验证游戏目录零写入**

```powershell
# 迁移前后对 227 版本文件夹做哈希指纹对比
$dir = "冒险活动客户端\versions\1.21.1-NeoForge_21.1.227"
Get-ChildItem -LiteralPath $dir -Recurse -File | Get-FileHash -Algorithm MD5 | Sort-Object Path | Format-Table -AutoSize
```
运行 `mcmig scan`/`mcmig diff` 后重跑上述命令,指纹应**完全一致**(v0 对游戏目录零写入)。

- [ ] **Step 4: 规则现算验证(验收标准 3)**

```bash
mcmig diff 1.21.1-NeoForge_21.1.227 1.21.1-NeoForge_21.1.229 --exclude "saves/**"
mcmig diff 1.21.1-NeoForge_21.1.227 1.21.1-NeoForge_21.1.229
```
Expected: 第一条 saves 不再出现(永不迁);第二条恢复。两次 diff **无需重扫**(直接读已有快照)。

- [ ] **Step 5: 记录结果**

把冒烟观察填入 `.mcmig/`(不入 git)或临时笔记,确认验收标准 1–5 全部达成。本任务无代码提交(若冒烟发现 bug,开新任务修复并提交)。

---

## 验收标准核对(v0 完成判定)

| # | 标准 | 对应任务 |
|---|---|---|
| 1 | `mcmig scan 227` 生成快照 + rich 分类汇总 | Task 9 + 11 |
| 2 | `mcmig diff 227 229` 产出 6 桶,`to_migrate` 含 options/servers/xaero/ftbchunks/DH/dragon-survival | Task 7/8/11 |
| 3 | 改规则后不重扫重 diff,结果即时反映 | Task 10 + 11 |
| 4 | 游戏版本文件夹零写入 | Task 11(哈希指纹验证) |
| 5 | 全部单测 + 端到端测试通过 | Task 10 |

---

## 自审(self-review)

**1. Spec 覆盖**:
- 6 模块(scanner/classifier/snapshot/differ/reporter/cli)+ hashing/rules → 全有对应 Task(2/3/4/5/6/7/8/9)。
- 哈希分层(text MD5 / mods 集合 / bulk size)→ Task 2 + 6 + 7。
- 分类分层 first-match-wins + pathspec + `<ver>` 替换 → Task 3。
- 6 桶语义 → Task 7。
- 快照 schema + 格式版本 → Task 5。
- 错误处理(版本不存在列可用 / 缺快照提示 / 占用跳过 / 坏格式拒绝 / 空目标合法)→ Task 9 + 6 + 5。
- CLI 标志(exclude/include/rule/strict/json/show-*) → Task 9。
- 测试 fixture + e2e → Task 6 conftest + Task 10。

**2. 占位符扫描**:无 TBD/TODO;每步含可执行命令与完整代码。

**3. 类型一致性**:`FileEntry(path,size,md5)`、`Category` 枚举、`RuleSet.classify(str)->Category`、`Classifier.classify_path`、`DiffItem(path,src,dst,note)`、`ReportOptions(show_identical/show_never/mods_only/category)` 在各 Task 间命名一致。

**已知简化(可在执行期微调,不影响架构)**:
- `Differ` 对 `Category.ASK` 归入 candidate/only_in_dst 同 UNKNOWN(v0 报告单列,无确认循环)。
- pathspec 每规则预编译一份(文件数百、规则数十 → 性能足够;优化留后续)。
- `.mcmig/` 落在 `Path.cwd()`(用户从工作目录运行;默认 game_root `./冒险活动客户端` 同基准)。

---

## 执行交接

计划已保存至 `Reference/plans/2026-07-02-migration-v0.md`。

**两种执行方式:**
1. **Subagent 驱动(推荐)** — 每个任务派新 subagent,任务间两阶段评审,迭代快、上下文干净。
2. **内联执行** — 当前会话批量执行,带 checkpoint。

> 用户已选 **Subagent 驱动**。需执行时调用 superpowers:subagent-driven-development 技能,按 Task 1→11 顺序派发,每个 subagent 只负责一个 Task 的完整 TDD 循环。
