# Planner 精修(2D 模型 + .bak 跟随 + rebuild 层)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把计划层从单一 `Action` 枚举重构为 `Behavior`(操作)× `Origin`(语义)2D 模型,修复 `.bak` 跟随父 config,新增 rebuild 规则层与白名单扩充,纯只读零写盘。

**Architecture:** 自底向上 TDD。先给规则/对比层加 `Category.REBUILD`(独立可绿),再做 plan/planner/reporter 三件套的 2D 原子重构(唯一不可拆分的耦合单元),随后 .bak 两趟处理、数据文件与 CLI 接线、reporter 子计数、终验。每个 task 结束整树测试绿 + 一次 commit。

**Tech Stack:** Python 3.11+、rich、PyYAML、pathspec、pytest、ruff。venv 在 `.venv\`(用 `.venv\Scripts\python.exe -m pytest`)。

## Global Constraints

- 文件一律 UTF-8 无 BOM;路径用 `pathlib.Path`,绝不字符串拼接;绝不硬编码版本号(`21.1.228` 等)。
- 所有代码注释/docstring 用中文;公有函数 Google 风格中文 docstring;所有函数签名标注类型提示。
- 测试与源码结构对应,放 `tests/`;导入用绝对路径 `from migration.xxx import ...`。
- 对游戏目录零写入(plan 纯只读);`e2e_plan_no_write_to_game_dir` 必须持续通过。
- 运行测试:`.venv\Scripts\python.exe -m pytest tests/ -q`;lint:`.venv\Scripts\python.exe -m ruff check .`。
- 版本号目标:`TOOL_VERSION` 0.2.0→0.3.0、`__version__` 0.2.0→0.3.0、`pyproject.toml` version 0.2.0→0.3.0、`PLAN_FORMAT` 1→2、`SNAPSHOT_FORMAT` **不动**(保持 1)。
- 规范来源:`Reference/specs/2026-07-09-planner-refinement-design.md`(本计划与其冲突时以 spec 为准)。

## File Structure

| 文件 | 责任 | 本计划改动 |
|---|---|---|
| `migration/rules.py` | 规则引擎:`Category`/`Rule`/`RuleSet`/多层加载 | +`Category.REBUILD`;+`load_rebuild_rules_from_text` |
| `migration/differ.py` | 两份分类快照→6 桶报告 | +`REBUILD`→never 桶(note="rebuild")路由 |
| `migration/plan.py` | Plan 数据模型与 JSON 持久化 | 重构:`Behavior`/`Origin`/注册表;`ActionRecord(behavior+origin)`;删 `Action`;`PLAN_FORMAT=2` |
| `migration/planner.py` | DiffReport+src_index→MigrationPlan | 全方法改产 (behavior, origin);+`resolve_bak_parent`+两趟 .bak |
| `migration/reporter.py` | rich 终端 + JSON 渲染 | `PlanReporter` 改按 origin 分组;`--category` 按 origin;new/modified 子计数 |
| `migration/cli.py` | scan/diff/plan 子命令 | `build_ruleset` 插 rebuild 层(常开) |
| `migration/snapshot.py` | 快照模型 | `TOOL_VERSION`→0.3.0(`SNAPSHOT_FORMAT` 不动) |
| `migration/__init__.py` | 包标识 | `__version__`→0.3.0 |
| `pyproject.toml` | 项目配置 | version→0.3.0 |
| `migration/data/rebuild.yaml` | rebuild 规则 | 新增(6 条) |
| `migration/data/whitelist.yaml` | 白名单 | +7 条,删 2 旧条目 |
| `migration/data/default_rules.yaml` | 内置默认规则 | never +2 类运行时清理 |
| `tests/test_plan.py` | plan 模型测试 | 重写(枚举/字段/summary/roundtrip) |
| `tests/test_planner.py` | planner 测试 | 18 处 action→behavior+origin;+.bak 两趟用例 |
| `tests/test_reporter.py` | reporter 测试 | ACTION_META→ORIGIN_REGISTRY;分组断言 |
| `tests/test_differ.py` | differ 测试 | +REBUILD 路由用例 |
| `tests/test_rules.py` | rules 测试 | +Category.REBUILD、rebuild 加载器 |
| `tests/test_e2e.py` | 端到端 | plan JSON action→behavior+origin;+acceptance 用例 |
| `tests/test_cli.py` | cli 测试 | plan JSON 断言更新 |

---

## Task 1: rules.py — Category.REBUILD + rebuild 加载器

**Files:**
- Modify: `migration/rules.py:17-26`(Category 枚举)、`migration/rules.py:241`(文件末尾追加加载器)
- Test: `tests/test_rules.py`(追加)

**Interfaces:**
- Produces: `Category.REBUILD`(自动进 `_DECIDE_MAP`,使 YAML 详写 `decide: rebuild` 合法);`rules.load_rebuild_rules_from_text(text: str, source_name: str) -> tuple[list[Rule], list[str]]`(每条强制 `decide=REBUILD`、`source="rebuild"`,镜像 `load_whitelist_rules_from_text`)。

- [ ] **Step 1: 写失败测试(追加到 `tests/test_rules.py` 末尾)**

```python
def test_category_has_rebuild():
    assert Category.REBUILD.value == "rebuild"


def test_rebuild_decide_accepted_in_verbose_rules(tmp_path: Path):
    f = tmp_path / "r.yaml"
    f.write_text(
        "version: 1\nrules:\n  - match: 'config/fml.toml'\n    decide: rebuild\n    reason: '版本绑定'\n",
        encoding="utf-8",
    )
    layer, errs = rules.load_user_rules(f)
    assert errs == []
    assert layer[0].decide == Category.REBUILD


def test_load_rebuild_rules_from_text_forces_rebuild():
    text = "version: 1\nrules:\n  - match: 'config/fml.toml'\n    reason: 'FML'\n"
    layer, errs = rules.load_rebuild_rules_from_text(text, "rebuild.yaml")
    assert errs == []
    assert len(layer) == 1
    assert layer[0].match == "config/fml.toml"
    assert layer[0].decide == Category.REBUILD
    assert layer[0].source == "rebuild"


def test_load_rebuild_rules_from_text_bad_match_skipped():
    text = "rules:\n  - match: ''\n    reason: '空'\n  - match: 'config/ok.toml'\n"
    layer, errs = rules.load_rebuild_rules_from_text(text, "rebuild.yaml")
    assert len(layer) == 1
    assert layer[0].match == "config/ok.toml"
    assert len(errs) == 1 and "match" in errs[0]


def test_load_rebuild_rules_from_text_bad_yaml():
    layer, errs = rules.load_rebuild_rules_from_text(": broken", "bad")
    assert layer == []
    assert len(errs) == 1 and "YAML" in errs[0]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rules.py -q`
Expected: FAIL(`Category.REBUILD` 不存在 / `load_rebuild_rules_from_text` 未定义)。

- [ ] **Step 3: 给 Category 加 REBUILD(`migration/rules.py`,替换 17-26 行的枚举)**

```python
class Category(Enum):
    """文件迁移决策类别。"""

    NEVER = "never"
    MUST_MIGRATE = "must_migrate"
    REBUILD = "rebuild"  # 版本/硬件派生的高危文件,默认让目标重建
    UNKNOWN = "unknown"
    ASK = "ask"
```

> `_DECIDE_MAP = {c.value: c for c in Category}`(26 行)无需改,自动收录 REBUILD——详写 YAML `decide: rebuild` 随即合法。

- [ ] **Step 4: 在 `migration/rules.py` 末尾追加 rebuild 加载器(镜像 184-241 行的白名单实现)**

```python
def _parse_rebuild_doc(doc: dict, source_name: str) -> tuple[list[Rule], list[str]]:
    """解析 rebuild YAML 文档(已 load 好的 dict),返回 (规则列表, 错误列表)。

    每条强制 decide=REBUILD + source="rebuild"。
    source_name 仅用于错误消息(如 "rebuild.yaml")。
    """
    rules_list: list[Rule] = []
    errors: list[str] = []
    for i, raw in enumerate(doc.get("rules") or []):
        if not isinstance(raw, dict):
            errors.append(f"{source_name} rebuild #{i}: 非映射")
            continue
        match = raw.get("match")
        if not match or not isinstance(match, str):
            errors.append(f"{source_name} rebuild #{i}: 缺少 match")
            continue
        rules_list.append(
            Rule(
                match=match,
                decide=Category.REBUILD,
                reason=str(raw.get("reason", "")),
                source="rebuild",
            )
        )
    return rules_list, errors


def load_rebuild_rules_from_text(text: str, source_name: str) -> tuple[list[Rule], list[str]]:
    """从 YAML 文本直接解析 rebuild 规则(PyInstaller 安全)。

    与白名单语义对偶:每条强制 decide=REBUILD(source="rebuild"),不要求 yaml 写 decide。
    供 importlib.resources 读取打包资源时使用。source_name 仅用于错误消息。
    """
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return [], [f"{source_name}: YAML 解析失败: {e}"]
    if not isinstance(doc, dict):
        return [], [f"{source_name}: 文档非映射结构"]
    return _parse_rebuild_doc(doc, source_name)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rules.py -q`
Expected: PASS(全绿)。

- [ ] **Step 6: 提交**

```bash
git add migration/rules.py tests/test_rules.py
git commit -m "feat(rules): 新增 Category.REBUILD 与 rebuild 规则加载器"
```

---

## Task 2: differ.py — REBUILD 路由进 never 桶(note="rebuild")

**Files:**
- Modify: `migration/differ.py:88-91`(在 NEVER 分支后插 REBUILD 分支)
- Test: `tests/test_differ.py`(追加)

**Interfaces:**
- Consumes: `Category.REBUILD`(Task 1)。
- Produces: `Differ.diff()` 把 `Category.REBUILD` 文件路由进 `report.never`,`DiffItem.note="rebuild"`(与 `note="never"` 同桶,note 区分)。下游 `planner._for_never` 据 note 定 origin(Task 3)。

- [ ] **Step 1: 写失败测试(追加到 `tests/test_differ.py` 末尾)**

```python
def test_rebuild_classified_goes_never_bucket_with_rebuild_note():
    from migration.rules import Category, Rule, RuleSet

    rs = RuleSet(rules=[Rule(match="config/fml.toml", decide=Category.REBUILD)])
    clf = Classifier(rs)
    d = Differ([_e("config/fml.toml", md5="a")], [_e("config/fml.toml", md5="b")], clf).diff()
    # 进 never 桶,note="rebuild"(与普通 never 区分,供 planner 定 origin)
    matches = [i for i in d.never if i.path == "config/fml.toml"]
    assert len(matches) == 1
    assert matches[0].note == "rebuild"
    # 不应进 candidate
    assert not any(i.path == "config/fml.toml" for i in d.candidate)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_differ.py::test_rebuild_classified_goes_never_bucket_with_rebuild_note -q`
Expected: FAIL(REBUILD 文件当前走 UNKNOWN 分支进 candidate)。

- [ ] **Step 3: 加 REBUILD 路由(`migration/differ.py`,在 88-91 行的 NEVER 分支 `continue` 之后、MUST_MIGRATE 分支之前插入)**

现有代码(88-91 行):
```python
            cat = self.classifier.classify_path(path)
            if cat == Category.NEVER:
                report.never.append(DiffItem(path, s, d, note="never"))
                continue
```
改为(插一个分支):
```python
            cat = self.classifier.classify_path(path)
            if cat == Category.NEVER:
                report.never.append(DiffItem(path, s, d, note="never"))
                continue
            if cat == Category.REBUILD:
                report.never.append(DiffItem(path, s, d, note="rebuild"))
                continue
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_differ.py -q`
Expected: PASS(全绿;differ 其余用例零回归)。

- [ ] **Step 5: 提交**

```bash
git add migration/differ.py tests/test_differ.py
git commit -m "feat(differ): REBUILD 分类路由进 never 桶(note=rebuild)"
```

---

## Task 3: 2D 模型重构(plan.py + planner.py + reporter.py + 版本号 + 测试)

> 这是本计划最大的 task,但**原子不可拆**:`ActionRecord` 改形状后,其生产者(planner)与消费者(reporter)必须同 commit 改完,否则整树编译失败。Task 内部 TDD 红→绿,commit 时全绿。`.bak` 文件本身的命运(bak_file origin)留到 Task 4;本 task 内 `.bak` 文件沿用现状(落 `default_config`),不引入新 bug,仅未修。

**Files:**
- Modify(重写模型段): `migration/plan.py:1-131`(整文件)
- Modify(重写决策方法): `migration/planner.py:1-178`(整文件,Task 3 版本保留单趟 .bak)
- Modify(重写 PlanReporter): `migration/reporter.py:93-180`
- Modify: `migration/snapshot.py:12`、`migration/__init__.py:3`、`pyproject.toml:8`
- Rewrite: `tests/test_plan.py`(整文件)、`tests/test_planner.py`(整文件)
- Modify: `tests/test_reporter.py`(42-109 行 plan 段 + 104-109 行 meta 测试)
- Modify: `tests/test_e2e.py`(plan JSON 断言)、`tests/test_cli.py`(plan 断言)

**Interfaces:**
- Produces: `plan.Behavior`(`COPY`/`SKIP`/`ASK`,str-Enum)、`plan.Origin`(11 成员,str-Enum)、`plan.OriginMeta`(dataclass)、`plan.ORIGIN_REGISTRY`(dict)+`plan.register_origin(...)`、`plan.ActionRecord(path, behavior, origin, src_size, dst_size, md5_match, confidence, reason, backup_target)`、`plan.PLAN_FORMAT=2`。删除 `plan.Action`。
- `planner.Planner.plan()` 产 `ActionRecord`(behavior+origin);`reporter.PlanReporter` 按 origin 分组。

- [ ] **Step 1: 重写 `tests/test_plan.py`(整文件覆盖)**

```python
import json
from pathlib import Path

import pytest

from migration.plan import (
    ActionRecord,
    Behavior,
    MigrationPlan,
    Origin,
    ORIGIN_REGISTRY,
    PlanFormatError,
    plan_path,
)


def _sample() -> MigrationPlan:
    return MigrationPlan(
        src="227",
        dst="229",
        generated_at="2026-07-07T12:00:00+08:00",
        actions=[
            ActionRecord(
                path="options.txt",
                behavior=Behavior.COPY,
                origin=Origin.MUST_MIGRATE,
                src_size=1234,
                dst_size=None,
                md5_match=None,
                confidence="high",
                reason="must_migrate + dst missing",
                backup_target=None,
            ),
            ActionRecord(
                path="config/create.toml",
                behavior=Behavior.COPY,
                origin=Origin.CONFIG_MODIFIED,
                src_size=100,
                dst_size=98,
                md5_match=False,
                confidence="high",
                reason=".bak sibling exists",
                backup_target="_conflict_backup/config/create.toml",
            ),
        ],
    )


def test_behavior_values():
    assert Behavior.COPY.value == "copy"
    assert Behavior.SKIP.value == "skip"
    assert Behavior.ASK.value == "ask"


def test_origin_values_count():
    assert {o.value for o in Origin} == {
        "must_migrate", "config_modified", "bak_file", "mod_added",
        "identical", "never", "default_config", "rebuild",
        "mod_shared", "mod_target_only", "needs_review",
    }


def test_origin_registry_covers_all_origins():
    assert set(ORIGIN_REGISTRY.keys()) == {o.value for o in Origin}


def test_register_origin_adds_entry():
    from migration.plan import register_origin
    register_origin("custom_x", title="自定义", visible=False, show_backup=False)
    assert "custom_x" in ORIGIN_REGISTRY


def test_save_load_roundtrip(tmp_path: Path):
    sp = tmp_path / "p.plan.json"
    _sample().save(sp)
    loaded = MigrationPlan.load(sp)
    assert loaded.src == "227" and loaded.dst == "229"
    assert loaded.actions == _sample().actions


def test_save_creates_parent_dirs(tmp_path: Path):
    sp = tmp_path / ".mcmig" / "plans" / "227__229.plan.json"
    _sample().save(sp)
    assert sp.exists()


def test_plan_format_is_2(tmp_path: Path):
    sp = tmp_path / "p.json"
    _sample().save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    assert doc["plan_format"] == 2


def test_summary_counts_by_origin(tmp_path: Path):
    sp = tmp_path / "p.json"
    _sample().save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    assert doc["summary"]["must_migrate"] == 1
    assert doc["summary"]["config_modified"] == 1
    assert doc["summary"]["rebuild"] == 0


def test_actions_serialize_behavior_and_origin(tmp_path: Path):
    sp = tmp_path / "p.json"
    _sample().save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    a = doc["actions"][0]
    assert a["behavior"] == "copy"
    assert a["origin"] == "must_migrate"
    assert "action" not in a


def test_load_rejects_unsupported_format(tmp_path: Path):
    sp = tmp_path / "bad.json"
    sp.write_text(
        json.dumps({"plan_format": 1, "src": "a", "dst": "b", "generated_at": "", "actions": []}),
        encoding="utf-8",
    )
    with pytest.raises(PlanFormatError):
        MigrationPlan.load(sp)


def test_plan_path_helper():
    p = plan_path(Path("C:/work"), "227", "229")
    assert p == Path("C:/work/.mcmig/plans/227__229.plan.json")


def test_md5_match_three_states_roundtrip(tmp_path: Path):
    sp = tmp_path / "p.json"
    plan = MigrationPlan(
        src="a", dst="b", generated_at="t",
        actions=[
            ActionRecord("x1", Behavior.COPY, Origin.MUST_MIGRATE, 1, None, None, "high", "r", None),
            ActionRecord("x2", Behavior.COPY, Origin.MUST_MIGRATE, 1, 1, True, "high", "r", None),
            ActionRecord("x3", Behavior.COPY, Origin.MUST_MIGRATE, 1, 1, False, "high", "r", None),
        ],
    )
    plan.save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    assert doc["actions"][0]["md5_match"] is None
    assert doc["actions"][1]["md5_match"] is True
    assert doc["actions"][2]["md5_match"] is False
```

- [ ] **Step 2: 跑 plan 测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_plan.py -q`
Expected: FAIL(`Behavior`/`Origin` 未定义)。

- [ ] **Step 3: 重写 `migration/plan.py`(整文件覆盖)**

```python
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


@dataclass(frozen=True)
class OriginMeta:
    """单个 origin 的 reporter 显示元数据。

    Attributes:
        title: 含 emoji 的分组标题(如 "✅ 必迁")。
        default_visible: 不带 --show-skip 时是否默认显示。
        show_backup: 是否在该 origin 分组表里显示 backup_target 列。
    """

    title: str
    default_visible: bool
    show_backup: bool


class Behavior(str, Enum):
    """单个文件的操作(Executor 关心,3 值闭合,极稳)。

    - COPY: 复制 src→dst(若 backup_target 非空,先备份 dst)。
    - SKIP: 不动。
    - ASK: 需人工确认(非交互→SKIP,交互→提示)。
    """

    COPY = "copy"
    SKIP = "skip"
    ASK = "ask"


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


# origin -> OriginMeta(初版词表,见 spec §2.2)
_ORIGIN_SEED: dict[str, OriginMeta] = {
    "must_migrate":    OriginMeta("✅ 必迁",            True,  False),
    "config_modified": OriginMeta("✏️ 改过的 config",   True,  False),
    "bak_file":        OriginMeta("📋 备份文件",        True,  False),
    "mod_added":       OriginMeta("📦 补 Mod",          True,  False),
    "needs_review":    OriginMeta("❓ 待确认",          True,  False),
    "rebuild":         OriginMeta("🔒 版本敏感",        False, False),
    "default_config":  OriginMeta("⚙️ 默认配置",        False, False),
    "never":           OriginMeta("⛔ 不迁",            False, False),
    "identical":       OriginMeta("⏭ 一致",            False, False),
    "mod_shared":      OriginMeta("📦 共有 Mod",        False, False),
    "mod_target_only": OriginMeta("📦 目标独有 Mod",    False, False),
}


ORIGIN_REGISTRY: dict[str, OriginMeta] = {}


def register_origin(key: str, *, title: str, visible: bool, show_backup: bool) -> None:
    """注册一个 origin 元数据(启动时播种已知 origin;未来 profiles/插件可扩展新 origin)。

    Args:
        key: origin 字符串值(如 "must_migrate")。
        title: 含 emoji 的分组标题。
        visible: 默认是否显示。
        show_backup: 是否显示 backup_target 列。
    """
    ORIGIN_REGISTRY[key] = OriginMeta(title=title, default_visible=visible, show_backup=show_backup)


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
```

- [ ] **Step 4: 跑 plan 测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_plan.py -q`
Expected: PASS。注:此刻 `planner.py`/`reporter.py` 仍 import 旧 `Action`,整树仍有红——属预期,后续步骤修。

- [ ] **Step 5: 重写 `tests/test_planner.py`(整文件覆盖,Task 3 版本:单趟 .bak,.bak 文件本身暂落 default_config)**

```python
from migration.differ import DiffItem, DiffReport
from migration.plan import Behavior, Origin
from migration.planner import Planner
from migration.snapshot import FileEntry


def _e(path, size=1, md5="x"):
    return FileEntry(path=path, size=size, md5=md5)


def _plan(report: DiffReport, src_entries: list[FileEntry] | None = None) -> list:
    src_index = {e.path: e for e in (src_entries or [])}
    return Planner(report, src_index).plan().actions


def test_to_migrate_new_goes_copy_must_migrate():
    report = DiffReport(to_migrate=[DiffItem("options.txt", _e("options.txt"), None, "new")])
    actions = _plan(report, [_e("options.txt")])
    a = next(a for a in actions if a.path == "options.txt")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.MUST_MIGRATE
    assert a.backup_target is None
    assert a.confidence == "high"


def test_to_migrate_modified_goes_copy_with_backup():
    report = DiffReport(
        to_migrate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="a"),
                             _e("config/foo.toml", md5="b"), "modified")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.MUST_MIGRATE
    assert a.backup_target == "_conflict_backup/config/foo.toml"
    assert a.md5_match is False


def test_identical_verified_goes_skip_identical_high():
    report = DiffReport(
        identical=[DiffItem("options.txt", _e("options.txt", md5="a"),
                            _e("options.txt", md5="a"), "verified")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "options.txt")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.IDENTICAL
    assert a.md5_match is True
    assert a.confidence == "high"


def test_identical_size_based_goes_skip_identical_medium():
    report = DiffReport(
        identical=[DiffItem("dh/lod.sqlite", _e("dh/lod.sqlite", size=16, md5=None),
                            _e("dh/lod.sqlite", size=16, md5=None), "size-based")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "dh/lod.sqlite")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.IDENTICAL
    assert a.md5_match is None
    assert a.confidence == "medium"


def test_never_note_never_goes_skip_never():
    report = DiffReport(never=[DiffItem("logs/latest.log", _e("logs/latest.log"), None, "never")])
    actions = _plan(report)
    a = next(a for a in actions if a.path == "logs/latest.log")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.NEVER


def test_never_note_rebuild_goes_skip_rebuild():
    report = DiffReport(never=[DiffItem("config/fml.toml", _e("config/fml.toml"),
                                        None, "rebuild")])
    actions = _plan(report)
    a = next(a for a in actions if a.path == "config/fml.toml")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.REBUILD


def test_mods_to_add_goes_copy_mod_added():
    report = DiffReport(mods=[DiffItem("mods/extra.jar", _e("mods/extra.jar"), None, "to_add")])
    actions = _plan(report)
    a = next(a for a in actions if a.path == "mods/extra.jar")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.MOD_ADDED


def test_mods_shared_goes_skip_mod_shared():
    report = DiffReport(
        mods=[DiffItem("mods/create.jar", _e("mods/create.jar"), _e("mods/create.jar"), "shared")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "mods/create.jar")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.MOD_SHARED


def test_mods_target_only_goes_skip_mod_target_only():
    report = DiffReport(
        mods=[DiffItem("mods/x.jar", None, _e("mods/x.jar"), "target_only")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "mods/x.jar")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.MOD_TARGET_ONLY


def test_only_in_dst_not_in_actions():
    report = DiffReport(
        only_in_dst=[DiffItem("config/target_only.toml", None, _e("config/target_only.toml"), "target_only")]
    )
    actions = _plan(report)
    assert all(a.path != "config/target_only.toml" for a in actions)


def test_candidate_non_config_goes_ask_needs_review():
    report = DiffReport(
        candidate=[DiffItem("kubejs/my.js", _e("kubejs/my.js"), None, "new")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "kubejs/my.js")
    assert a.behavior == Behavior.ASK
    assert a.origin == Origin.NEEDS_REVIEW
    assert a.confidence == "low"


def test_config_candidate_with_plain_bak_goes_copy_config_modified():
    report = DiffReport(
        candidate=[DiffItem("config/create.toml", _e("config/create.toml", md5="a"), None, "new")]
    )
    src = [_e("config/create.toml"), _e("config/create.toml.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/create.toml")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.CONFIG_MODIFIED
    assert a.reason == ".bak sibling exists"
    assert a.confidence == "high"


def test_config_candidate_with_versioned_bak_goes_copy_with_backup():
    report = DiffReport(
        candidate=[DiffItem("config/create.toml", _e("config/create.toml", md5="a"),
                            _e("config/create.toml", md5="b"), "modified")]
    )
    src = [_e("config/create.toml"), _e("config/create-1.toml.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/create.toml")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.CONFIG_MODIFIED
    assert a.backup_target == "_conflict_backup/config/create.toml"


def test_config_candidate_no_bak_goes_skip_default_config():
    report = DiffReport(
        candidate=[DiffItem("config/default.toml", _e("config/default.toml", md5="a"),
                            _e("config/default.toml", md5="b"), "modified")]
    )
    src = [_e("config/default.toml")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/default.toml")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.DEFAULT_CONFIG
    assert a.confidence == "high"
    assert "no .bak" in a.reason


def test_bak_judgment_only_applies_to_config_prefix():
    report = DiffReport(
        candidate=[DiffItem("kubejs/my.js", _e("kubejs/my.js"), None, "new")]
    )
    src = [_e("kubejs/my.js"), _e("kubejs/my.js.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "kubejs/my.js")
    assert a.behavior == Behavior.ASK
    assert a.origin == Origin.NEEDS_REVIEW
    assert a.confidence == "low"


def test_bak_in_dst_only_does_not_count():
    report = DiffReport(
        candidate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="a"),
                            _e("config/foo.toml", md5="b"), "modified")]
    )
    src = [_e("config/foo.toml")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.DEFAULT_CONFIG


def test_multiple_bak_versions_also_match():
    report = DiffReport(
        candidate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="a"), None, "new")]
    )
    src = [_e("config/foo.toml"), _e("config/foo-1.toml.bak"), _e("config/foo-2.toml.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.CONFIG_MODIFIED


def test_bak_does_not_false_match_stem_with_hyphens():
    from migration.planner import has_bak_sibling

    src = {
        "config/dragon-survival.toml",
        "config/dragon-survival-extra.toml.bak",
    }
    assert has_bak_sibling("config/dragon-survival.toml", src) is False
```

- [ ] **Step 6: 重写 `migration/planner.py`(整文件覆盖,Task 3 版本:保留单趟 has_bak_sibling)**

```python
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
```

- [ ] **Step 7: 重写 `migration/reporter.py` 的 plan 段(替换 93-180 行的 ACTION_META 起至文件末尾)**

先把 import 行(12 行)`from .plan import MigrationPlan` 改为:
```python
from .plan import MigrationPlan, ORIGIN_REGISTRY
```
然后替换 93 行到文件末尾(`ACTION_META` 起的全部内容)为:

```python
_DEFAULT_VISIBLE = [k for k, m in ORIGIN_REGISTRY.items() if m.default_visible]


@dataclass
class PlanOptions:
    """Plan 报告可见性控制。"""

    show_skip: bool = False
    category: str | None = None
    visible_actions: set[str] | None = None  # 预留(key 为 origin 字符串)


class PlanReporter:
    """把 MigrationPlan 渲染成 rich 终端表格或 JSON(= plan 文件内容)。"""

    def __init__(self, plan: MigrationPlan, *, src_version: str, dst_version: str) -> None:
        self.plan = plan
        self.src_version = src_version
        self.dst_version = dst_version

    def to_json(self) -> str:
        """JSON 输出 = plan 文件内容。"""
        payload = {
            "tool_version": self.plan.tool_version,
            "plan_format": self.plan.plan_format,
            "src": self.src_version,
            "dst": self.dst_version,
            "generated_at": self.plan.generated_at,
            "summary": self.plan.summary(),
            "actions": [r.to_dict() for r in self.plan.actions],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _visible_origins(self, opts: PlanOptions) -> list[str]:
        if opts.category:
            return [opts.category] if opts.category in ORIGIN_REGISTRY else []
        if opts.visible_actions is not None:
            return [o for o in ORIGIN_REGISTRY if o in opts.visible_actions]
        if opts.show_skip:
            return list(ORIGIN_REGISTRY.keys())
        return _DEFAULT_VISIBLE

    def render(self, opts: PlanOptions, console: Console | None = None) -> None:
        """渲染 rich 终端报告(按 origin 分组)。"""
        console = console or Console()
        console.print(
            f"[bold]plan:[/] [cyan]{self.src_version}[/] → [cyan]{self.dst_version}[/]"
        )
        summary = self.plan.summary()
        # 仅显示非零 origin(全显示会过长)
        summary_str = ", ".join(
            f"{ORIGIN_REGISTRY[o].title}{summary.get(o, 0)}"
            for o in ORIGIN_REGISTRY
            if summary.get(o, 0) > 0
        )
        console.print(f"[dim]汇总: {summary_str}[/]")
        for origin_key in self._visible_origins(opts):
            items = [r for r in self.plan.actions if r.origin.value == origin_key]
            if not items:
                continue
            meta = ORIGIN_REGISTRY[origin_key]
            tbl = Table(title=f"{meta.title} ({len(items)})", title_style="bold")
            tbl.add_column("路径")
            tbl.add_column("置信度", style="dim")
            tbl.add_column("原因", style="dim")
            if meta.show_backup:
                tbl.add_column("备份目标")
            for r in items:
                row = [r.path, r.confidence, r.reason]
                if meta.show_backup:
                    row.append(r.backup_target or "")
                tbl.add_row(*row)
            console.print(tbl)
        if not opts.show_skip and not opts.category:
            console.print("[dim]默认隐藏 skip 类 origin,用 --show-skip 查看[/]")
```

> 注意:文件顶部 `BUCKETS`/`BUCKET_TITLE`/`DiffReporter`/`ReportOptions`(1-90 行)**不动**——它们是 diff 命令的渲染器,与 plan 无关。

- [ ] **Step 8: 更新 `tests/test_reporter.py` 的 plan 段(替换 42-109 行)**

```python
def test_plan_reporter_to_json_parseable():
    from migration.differ import DiffItem, DiffReport
    from migration.planner import Planner
    from migration.reporter import PlanReporter
    from migration.snapshot import FileEntry

    report = DiffReport(
        to_migrate=[DiffItem("options.txt", FileEntry("options.txt", 10, "a"), None, "new")]
    )
    plan = Planner(report, {"options.txt": FileEntry("options.txt", 10, "a")}).plan()
    plan.src, plan.dst = "227", "229"
    doc = json.loads(PlanReporter(plan, src_version="227", dst_version="229").to_json())
    assert doc["src"] == "227" and doc["dst"] == "229"
    assert doc["summary"]["must_migrate"] == 1
    assert any(
        a["path"] == "options.txt" and a["behavior"] == "copy" and a["origin"] == "must_migrate"
        for a in doc["actions"]
    )


def test_plan_reporter_render_no_error(capsys):
    from migration.differ import DiffItem, DiffReport
    from migration.planner import Planner
    from migration.reporter import PlanOptions, PlanReporter
    from migration.snapshot import FileEntry

    report = DiffReport(
        to_migrate=[
            DiffItem("options.txt", FileEntry("options.txt", 10, "a"), None, "new"),
            DiffItem("config/foo.toml", FileEntry("config/foo.toml", 5, "a"),
                     FileEntry("config/foo.toml", 4, "b"), "modified"),
        ],
        candidate=[DiffItem("kubejs/x.js", FileEntry("kubejs/x.js", 1, "a"), None, "new")],
    )
    plan = Planner(report, {"options.txt": FileEntry("options.txt", 10, "a")}).plan()
    plan.src, plan.dst = "227", "229"
    PlanReporter(plan, src_version="227", dst_version="229").render(PlanOptions())


def test_plan_options_defaults_hide_skip():
    from migration.reporter import PlanOptions

    opts = PlanOptions()
    assert opts.show_skip is False
    assert opts.category is None


def test_plan_reporter_show_skip_renders_skip_origins(capsys):
    from migration.differ import DiffItem, DiffReport
    from migration.planner import Planner
    from migration.reporter import PlanOptions, PlanReporter
    from migration.snapshot import FileEntry

    report = DiffReport(
        candidate=[DiffItem("config/d.toml", FileEntry("config/d.toml", 1, "a"),
                            FileEntry("config/d.toml", 1, "b"), "modified")]
    )
    plan = Planner(report, {"config/d.toml": FileEntry("config/d.toml", 1, "a")}).plan()
    plan.src, plan.dst = "a", "b"
    PlanReporter(plan, src_version="a", dst_version="b").render(PlanOptions(show_skip=True))
    out = capsys.readouterr().out
    assert "默认配置" in out  # origin=default_config 的标题


def test_origin_registry_covers_all_origin_values():
    """ORIGIN_REGISTRY 必须覆盖所有 Origin enum 值,否则 render 会静默丢弃。"""
    from migration.plan import Origin, ORIGIN_REGISTRY

    assert set(ORIGIN_REGISTRY.keys()) == {o.value for o in Origin}
```

- [ ] **Step 9: 更新 `tests/test_e2e.py` 的 plan JSON 断言**

把 `test_e2e_plan_bak_judgment`(109-113 行)、`test_e2e_plan_whitelist_upgrades_to_migrate`(130-132 行)、`test_e2e_plan_default_config_skipped`(170 行)中的:
```python
    actions = {a["path"]: a["action"] for a in doc["actions"]}
    assert actions.get("config/create.toml") == "copy_new"
```
类断言改为按 behavior 判定:
```python
    actions = {a["path"]: a["behavior"] for a in doc["actions"]}
    assert actions.get("config/create.toml") == "copy"
```
具体三处替换:
- `test_e2e_plan_bak_judgment`:`actions.get("config/create.toml") == "copy_new"` → `== "copy"`(并改键名 `a["action"]`→`a["behavior"]`)。
- `test_e2e_plan_whitelist_upgrades_to_migrate`:`actions.get("iris.properties") == "copy_new"` 和 `actions.get("config/jade/preset.json") == "copy_new"` → 均改 `== "copy"`(`a["action"]`→`a["behavior"]`)。
- `test_e2e_plan_default_config_skipped`:`actions.get("config/default.toml") == "skip_default_config"` → 改为查 origin:`origins = {a["path"]: a["origin"] for a in doc["actions"]}; assert origins.get("config/default.toml") == "default_config"`。

- [ ] **Step 10: 更新 `tests/test_cli.py` 的 plan 断言**

`test_plan_show_skip_includes_skip_actions`(214 行):
```python
    assert "skip_default" in out or "skip_never" in out or "skip_identical" in out
```
改为(origin 标题中文):
```python
    assert "默认配置" in out or "不迁" in out or "一致" in out
```

- [ ] **Step 11: 版本号 bump**

- `migration/snapshot.py:12`:`TOOL_VERSION = "0.2.0"` → `TOOL_VERSION = "0.3.0"`。
- `migration/__init__.py:3`:`__version__ = "0.2.0"` → `__version__ = "0.3.0"`。
- `pyproject.toml:8`:`version = "0.2.0"` → `version = "0.3.0"`。

- [ ] **Step 12: 跑全量测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS(全绿,>111 项)。若有残留 `Action`/`action` 引用导致红,用 grep 定位:`Select-String -Pattern "\bAction\b|\.action\b|ACTION_META|\"action\"" -Path tests,migration` 逐个修(注释中的 `action` 字样如 "可执行 action 列表"可保留)。

- [ ] **Step 13: lint**

Run: `.venv\Scripts\python.exe -m ruff check .`
Expected: 无输出(干净)。

- [ ] **Step 14: 提交**

```bash
git add migration/plan.py migration/planner.py migration/reporter.py migration/snapshot.py migration/__init__.py pyproject.toml tests/test_plan.py tests/test_planner.py tests/test_reporter.py tests/test_e2e.py tests/test_cli.py
git commit -m "refactor(plan): 2D 模型(Behavior×Origin)+ 注册表,删 Action,PLAN_FORMAT=2"
```

---

## Task 4: planner.py — resolve_bak_parent + 两趟 .bak 跟随父

**Files:**
- Modify: `migration/planner.py`(加 `import re`、加 `resolve_bak_parent`、`plan()` 改两趟、加 `_for_bak`)
- Test: `tests/test_planner.py`(追加 .bak 两趟用例)

**Interfaces:**
- Produces: `planner.resolve_bak_parent(path: str, src_paths: set[str]) -> str | None`(与 `has_bak_sibling` 对偶,从 .bak 反推父 config);`Planner.plan()` 两趟处理(config/ 下 .bak candidate 在 pass 2 继承父命运)。

- [ ] **Step 1: 写失败测试(追加到 `tests/test_planner.py` 末尾)**

```python
def test_resolve_bak_parent_versioned():
    from migration.planner import resolve_bak_parent
    src = {"config/create.toml"}
    assert resolve_bak_parent("config/create-1.toml.bak", src) == "config/create.toml"


def test_resolve_bak_parent_plain():
    from migration.planner import resolve_bak_parent
    src = {"config/ali_common.json"}
    assert resolve_bak_parent("config/ali_common.json.bak", src) == "config/ali_common.json"


def test_resolve_bak_parent_orphan_returns_none():
    from migration.planner import resolve_bak_parent
    assert resolve_bak_parent("config/ghost-1.toml.bak", {"config/other.toml"}) is None


def test_resolve_bak_parent_non_bak_returns_none():
    from migration.planner import resolve_bak_parent
    assert resolve_bak_parent("config/foo.toml", {"config/foo.toml"}) is None


def test_bak_file_follows_migrated_parent_to_bak_file_copy_new():
    """父 config 迁(COPY)→ .bak 落 bak_file COPY。"""
    report = DiffReport(
        candidate=[
            DiffItem("config/create.toml", _e("config/create.toml", md5="a"), None, "new"),
            DiffItem("config/create-1.toml.bak", _e("config/create-1.toml.bak"), None, "new"),
        ]
    )
    src = [_e("config/create.toml"), _e("config/create-1.toml.bak")]
    actions = _plan(report, src)
    bak = next(a for a in actions if a.path == "config/create-1.toml.bak")
    assert bak.behavior == Behavior.COPY
    assert bak.origin == Origin.BAK_FILE
    assert bak.backup_target is None  # new → 无备份


def test_bak_file_follows_migrated_parent_to_bak_file_copy_modified():
    """父 config 迁(COPY, modified)→ .bak 落 bak_file COPY + backup_target。"""
    report = DiffReport(
        candidate=[
            DiffItem("config/create.toml", _e("config/create.toml", md5="a"),
                     _e("config/create.toml", md5="b"), "modified"),
            DiffItem("config/create-1.toml.bak", _e("config/create-1.toml.bak"),
                     _e("config/create-1.toml.bak", md5="b"), "modified"),
        ]
    )
    src = [_e("config/create.toml"), _e("config/create-1.toml.bak")]
    actions = _plan(report, src)
    bak = next(a for a in actions if a.path == "config/create-1.toml.bak")
    assert bak.behavior == Behavior.COPY
    assert bak.origin == Origin.BAK_FILE
    assert bak.backup_target == "_conflict_backup/config/create-1.toml.bak"


def test_bak_file_follows_rebuild_parent_to_skip_rebuild():
    """父是 rebuild(SKIP)→ .bak 完整继承父 (SKIP, rebuild)。"""
    report = DiffReport(
        never=[DiffItem("config/fml.toml", _e("config/fml.toml"), None, "rebuild")],
        candidate=[DiffItem("config/fml-1.toml.bak", _e("config/fml-1.toml.bak"), None, "new")],
    )
    src = [_e("config/fml.toml"), _e("config/fml-1.toml.bak")]
    actions = _plan(report, src)
    bak = next(a for a in actions if a.path == "config/fml-1.toml.bak")
    assert bak.behavior == Behavior.SKIP
    assert bak.origin == Origin.REBUILD


def test_bak_file_orphan_goes_ask_needs_review():
    """父不在 src(孤儿)→ ASK / needs_review。"""
    report = DiffReport(
        candidate=[DiffItem("config/ghost-1.toml.bak", _e("config/ghost-1.toml.bak"), None, "new")]
    )
    src = [_e("config/ghost-1.toml.bak")]
    actions = _plan(report, src)
    bak = next(a for a in actions if a.path == "config/ghost-1.toml.bak")
    assert bak.behavior == Behavior.ASK
    assert bak.origin == Origin.NEEDS_REVIEW


def test_bak_file_outside_config_goes_ask_not_bak_file():
    """config/ 外的 .bak 不走本逻辑 → 普通候选 → ASK / needs_review。"""
    report = DiffReport(
        candidate=[DiffItem("kubejs/data.js.bak", _e("kubejs/data.js.bak"), None, "new")]
    )
    src = [_e("kubejs/data.js.bak")]
    actions = _plan(report, src)
    bak = next(a for a in actions if a.path == "kubejs/data.js.bak")
    assert bak.behavior == Behavior.ASK
    assert bak.origin == Origin.NEEDS_REVIEW
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_planner.py -q`
Expected: FAIL(`resolve_bak_parent` 未定义;.bak 文件当前落 default_config)。

- [ ] **Step 3: 在 `migration/planner.py` 顶部加 `import re`(13 行 `import fnmatch` 后加一行)**

```python
import fnmatch
import re
```

- [ ] **Step 4: 在 `migration/planner.py` 的 `has_bak_sibling` 函数后(54 行后)加 `resolve_bak_parent`**

```python
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
    m = re.match(r"^(.*)-[0-9]+$", stem)
    if m:
        versioned_parent = m.group(1) + suffix
        if versioned_parent in src_paths:
            return versioned_parent
    if base in src_paths:
        return base
    return None
```

- [ ] **Step 5: 改 `migration/planner.py` 的 `plan()` 为两趟(替换 67-86 行的 plan 方法)**

```python
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
```

- [ ] **Step 6: 在 `migration/planner.py` 的 `_ask` 方法后(文件末尾)加 `_for_bak`**

```python
    def _for_bak(
        self, item: DiffItem, decision: dict[str, ActionRecord], src_paths: set[str]
    ) -> ActionRecord:
        """.bak candidate 继承父 config 命运(spec §3.2)。

        - 父在决策表且 behavior=SKIP(如 rebuild/identical)→ 完整继承父。
        - 父在决策表且 behavior=COPY(迁)→ bak_file COPY(backup_target 按 new/modified)。
        - 父不在 src(孤儿)→ ASK / needs_review。
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
        if parent is not None and parent.behavior == Behavior.SKIP:
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
```

- [ ] **Step 7: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_planner.py -q`
Expected: PASS(全绿)。

- [ ] **Step 8: 提交**

```bash
git add migration/planner.py tests/test_planner.py
git commit -m "feat(planner): .bak 跟随父 config(resolve_bak_parent 两趟处理)"
```

---

## Task 5: 数据文件 + cli.build_ruleset 接线(rebuild 常开、白名单扩充、never 清理)

**Files:**
- Create: `migration/data/rebuild.yaml`
- Modify: `migration/data/whitelist.yaml`、`migration/data/default_rules.yaml`
- Modify: `migration/cli.py:108-139`(build_ruleset)
- Test: `tests/test_rules.py`、`tests/test_cli.py`、`tests/test_e2e.py`(追加)

**Interfaces:**
- Consumes: `rules.load_rebuild_rules_from_text`(Task 1)。
- Produces: rebuild 层在 scan/diff/plan 三命令**常开**(插在 user 与 whitelist 之间);优先级 `cli > extra > user > REBUILD > whitelist > default`。

- [ ] **Step 1: 写失败测试(追加到 `tests/test_cli.py` 末尾)**

```python
def test_plan_rebuild_files_go_rebuild_origin(tmp_path: Path, monkeypatch, capsys):
    """fml.toml 等命中 rebuild.yaml → plan 中 origin=rebuild(不进 candidate)。"""
    import json
    import shutil
    from migration import cli

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    mini = versions / "mini"
    mini.mkdir()
    (mini / "config").mkdir()
    (mini / "config" / "fml.toml").write_text("x=1\n", encoding="utf-8")
    (mini / "options.txt").write_text("v\n", encoding="utf-8")
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])
    capsys.readouterr()
    cli.main(["plan", "mini", "target", "--json"])
    doc = json.loads(capsys.readouterr().out)
    origins = {a["path"]: a["origin"] for a in doc["actions"]}
    assert origins.get("config/fml.toml") == "rebuild"


def test_plan_whitelist_sodium_options_goes_must_migrate(tmp_path: Path, monkeypatch, capsys):
    """sodium-options.json 命中白名单 → must_migrate(不进 rebuild/default_config)。"""
    import json
    import shutil
    from migration import cli

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    mini = versions / "mini"
    mini.mkdir()
    (mini / "config").mkdir()
    (mini / "config" / "sodium-options.json").write_text("{}", encoding="utf-8")
    (mini / "options.txt").write_text("v\n", encoding="utf-8")
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])
    capsys.readouterr()
    cli.main(["plan", "mini", "target", "--json"])
    doc = json.loads(capsys.readouterr().out)
    origins = {a["path"]: a["origin"] for a in doc["actions"]}
    assert origins.get("config/sodium-options.json") == "must_migrate"


def test_plan_rebuild_yields_to_user_rules(tmp_path: Path, monkeypatch, capsys):
    """user rules.yaml 写 fml.toml→must_migrate 时压过 rebuild(P2 用户主权)。"""
    import json
    from migration import cli

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    mini = versions / "mini"
    mini.mkdir()
    (mini / "config").mkdir()
    (mini / "config" / "fml.toml").write_text("x=1\n", encoding="utf-8")
    (mini / "options.txt").write_text("v\n", encoding="utf-8")
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcmig").mkdir()
    (tmp_path / ".mcmig" / "rules.yaml").write_text(
        "version: 1\nrules:\n  - match: 'config/fml.toml'\n    decide: must_migrate\n    reason: 'user override'\n",
        encoding="utf-8",
    )
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])
    capsys.readouterr()
    cli.main(["plan", "mini", "target", "--json"])
    doc = json.loads(capsys.readouterr().out)
    origins = {a["path"]: a["origin"] for a in doc["actions"]}
    assert origins.get("config/fml.toml") == "must_migrate"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -q -k "rebuild or whitelist_sodium"`
Expected: FAIL(rebuild.yaml 不存在;sodium-options 未在白名单)。

- [ ] **Step 3: 创建 `migration/data/rebuild.yaml`(spec §4.1 的 6 条)**

```yaml
# 版本/硬件派生的高危文件:跨版本/跨机器迁移会崩溃或指纹错乱。
# 默认让目标版本自行重建。用户可用 .mcmig/rules.yaml 强制覆盖(P2:用户主权)。
# 优先级:cli > extra > user > REBUILD > whitelist > default
# 语义:每条强制 decide=rebuild(yaml 不写 decide),source="rebuild"

version: 1
rules:
  - match: "config/fml.toml"
    reason: "FML 加载器核心配置(maxThreads/earlyWindowSkipGLVersions,版本+硬件派生)"
  - match: "config/neoforge-client.toml"
    reason: "NeoForge 客户端渲染管线开关(加载器派生)"
  - match: "config/neoforge-common.toml"
    reason: "NeoForge 通用开发/日志开关(加载器派生)"
  - match: "config/sodium-fingerprint.json"
    reason: "Sodium 设备指纹(s/u/p 哈希+时间戳,跨机器/跨版本必失效)"
  - match: "config/iris-excluded.json"
    reason: "Iris 按 GPU 拉黑的光影清单(同机可迁跨机错;保守跳过,用户可覆盖)"
  - match: "config/sodium-mixins.properties"
    reason: "Sodium Mixin 激活集(版本绑定;保守跳过,用户可覆盖)"
```

- [ ] **Step 4: 改 `migration/data/whitelist.yaml`(删 2 旧条目,加 7 新条目;整文件覆盖)**

```yaml
# 无 .bak 但属玩家偏好的文件白名单。
# 这些文件由 mod 直接写入玩家设置,不经过 NeoForge 的 .bak 机制,
# 故 .bak 判定法无法识别,需显式列入。
# 来源:AGENTS.md「config 玩家改动判定法」+ 实测 227 vs 228 + spec §5
# 优先级:user rules > rebuild > whitelist > default rules
# 语义:每条强制 decide=must_migrate(yaml 不写 decide)

version: 1
rules:
  # 高置信度(已读内容证实为玩家客户端偏好,无 .bak)
  - match: "iris.properties"
    reason: "Iris 光影客户端设置(无 .bak 机制)"

  - match: "config/jade/**/*.json"
    reason: "Jade 显示偏好(无 .bak)"

  - match: "config/jei/*.ini"
    reason: "JEI 客户端 UI/排序/书签/搜索偏好(.ini 直接写无 .bak;收编原 sort-order+bookmarks)"

  - match: "config/jei/blacklist.json"
    reason: "JEI 玩家隐藏物品清单(无 .bak)"

  - match: "config/MouseTweaks.cfg"
    reason: "鼠标 inventory 行为偏好(.cfg 直接写无 .bak)"

  - match: "config/sodium-options.json"
    reason: "Sodium 画质/性能偏好(直接写 JSON 无 .bak;Q6 证据:无设备数据)"

  # 中等置信度(命名/类推)
  - match: "config/ftb*-client.snbt"
    reason: "FTB 客户端偏好(library/ultimine,类同 ftbchunks-client;-client 后缀)"

  - match: "config/xaero/**"
    reason: "Xaero 小地图显示偏好(非路径点数据;cfg/json/txt 直接写)"

  - match: "config/DistantHorizons.toml"
    reason: "Distant Horizons 远景视觉偏好(DH 自带配置系统无 .bak;含 serverId 与 LOD 数据配套迁)"

  - match: "local/ftbchunks/**/ftbchunks-client.snbt"
    reason: "FTB Chunks 客户端偏好(无 .bak)"
```

- [ ] **Step 5: 改 `migration/data/default_rules.yaml`(never 加 2 类运行时清理)**

在 `never:` 段(`command_history.txt` 后、`defaultconfigs/**` 前或后均可)追加:
```yaml
  - config/jei/world/**                    # JEI 查询历史(运行时可重建)
  - config/ars_nouveau/search_index/**     # Lucene 索引(运行时可重建)
```

完整 `never:` 段示例(插入位置在 `defaultconfigs/**` 之后):
```yaml
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
  - config/jei/world/**
  - config/ars_nouveau/search_index/**
```

- [ ] **Step 6: 改 `migration/cli.py` 的 `build_ruleset`(108-139 行)插 rebuild 层**

把现有函数体替换为(在 user 之后、whitelist 之前恒加载 rebuild):
```python
def build_ruleset(
    versions: str | list[str],
    args: argparse.Namespace,
    mcmig_dir: Path,
    *,
    with_whitelist: bool = False,
) -> tuple[rules.RuleSet, list[str]]:
    """按优先级(CLI > extra > user > REBUILD > whitelist > default)组装 RuleSet。

    rebuild 层对所有命令(scan/diff/plan)常开;whitelist 仅 plan 命令启用。
    """
    from importlib import resources

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
    # rebuild 层:常开(scan/diff/plan 都需正确识别版本敏感文件)
    rb_text = resources.files("migration").joinpath("data/rebuild.yaml").read_text(encoding="utf-8")
    rebuild, rbe = rules.load_rebuild_rules_from_text(rb_text, "rebuild.yaml")
    errors.extend(rbe)
    whitelist: list[rules.Rule] = []
    if with_whitelist:
        wl_text = resources.files("migration").joinpath("data/whitelist.yaml").read_text(encoding="utf-8")
        whitelist, we = rules.load_whitelist_rules_from_text(wl_text, "whitelist.yaml")
        errors.extend(we)
    default, de = rules.load_default_rules(versions)
    errors.extend(de)
    rs = rules.RuleSet.from_layers(cli_rules, extra, user, rebuild, whitelist, default)
    return rs, errors
```

- [ ] **Step 7: 跑 cli 测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -q`
Expected: PASS。

- [ ] **Step 8: 加 rules 数据层单元测试(追加到 `tests/test_rules.py` 末尾,验证打包资源能解析)**

```python
def test_rebuild_yaml_loads_six_entries():
    from importlib import resources
    text = resources.files("migration").joinpath("data/rebuild.yaml").read_text(encoding="utf-8")
    layer, errs = rules.load_rebuild_rules_from_text(text, "rebuild.yaml")
    assert errs == []
    matches = {r.match for r in layer}
    assert "config/fml.toml" in matches
    assert "config/sodium-fingerprint.json" in matches
    assert len(layer) == 6
    assert all(r.decide == Category.REBUILD for r in layer)


def test_whitelist_yaml_loads_sodium_options():
    from importlib import resources
    text = resources.files("migration").joinpath("data/whitelist.yaml").read_text(encoding="utf-8")
    layer, errs = rules.load_whitelist_rules_from_text(text, "whitelist.yaml")
    assert errs == []
    matches = {r.match for r in layer}
    assert "config/sodium-options.json" in matches
    assert "config/jei/*.ini" in matches
    # 旧条目已被 *.ini 收编删除
    assert "config/jei/*sort-order*" not in matches
```

- [ ] **Step 9: 跑全量测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS(全绿)。

- [ ] **Step 10: lint**

Run: `.venv\Scripts\python.exe -m ruff check .`
Expected: 无输出。

- [ ] **Step 11: 提交**

```bash
git add migration/data/rebuild.yaml migration/data/whitelist.yaml migration/data/default_rules.yaml migration/cli.py tests/test_cli.py tests/test_rules.py
git commit -m "feat(rules): rebuild 层常开 + 白名单扩充(7 条) + never 运行时清理"
```

---

## Task 6: reporter.py — new/modified 子计数

**Files:**
- Modify: `migration/reporter.py`(render 内 origin 分组标题加子计数)
- Test: `tests/test_reporter.py`(追加)

**Interfaces:**
- Consumes: `plan.Behavior`(COPY)、`ActionRecord.backup_target`(None=新增,set=覆盖)。
- Produces: COPY 类 origin 分组标题显示 `(N: 新增 X · 覆盖 Y 已备份)`;SKIP 类不变。

- [ ] **Step 1: 写失败测试(追加到 `tests/test_reporter.py` 末尾)**

```python
def test_plan_reporter_new_modified_subcount(capsys):
    from migration.differ import DiffItem, DiffReport
    from migration.plan import Behavior
    from migration.planner import Planner
    from migration.reporter import PlanOptions, PlanReporter
    from migration.snapshot import FileEntry

    report = DiffReport(
        to_migrate=[
            DiffItem("options.txt", FileEntry("options.txt", 10, "a"), None, "new"),
            DiffItem("servers.dat", FileEntry("servers.dat", 5, "a"),
                     FileEntry("servers.dat", 4, "b"), "modified"),
        ],
    )
    plan = Planner(report, {"options.txt": FileEntry("options.txt", 10, "a")}).plan()
    plan.src, plan.dst = "a", "b"
    PlanReporter(plan, src_version="a", dst_version="b").render(PlanOptions())
    out = capsys.readouterr().out
    # must_migrate 组标题含新增/覆盖子计数
    assert "新增" in out and "覆盖" in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_reporter.py::test_plan_reporter_new_modified_subcount -q`
Expected: FAIL(当前标题无子计数)。

- [ ] **Step 3: 改 `migration/reporter.py`**

先把 import 行加 `Behavior`:
```python
from .plan import Behavior, MigrationPlan, ORIGIN_REGISTRY
```
再把 `render` 内构造 Table 标题那段(原 `meta = ORIGIN_REGISTRY[origin_key]` 与 `tbl = Table(title=f"{meta.title} ({len(items)})"...`)替换为:

```python
            meta = ORIGIN_REGISTRY[origin_key]
            # COPY 类 origin 按 backup_target 推导 new/modified 子计数(SKIP 类不拆)
            copy_items = [r for r in items if r.behavior == Behavior.COPY]
            if copy_items and origin_key in (
                "must_migrate", "config_modified", "bak_file", "mod_added"
            ):
                new_count = sum(1 for r in copy_items if not r.backup_target)
                mod_count = sum(1 for r in copy_items if r.backup_target)
                title = f"{meta.title} ({len(items)}: 新增 {new_count} · 覆盖 {mod_count} 已备份)"
            else:
                title = f"{meta.title} ({len(items)})"
            tbl = Table(title=title, title_style="bold")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_reporter.py -q`
Expected: PASS(全绿)。

- [ ] **Step 5: 提交**

```bash
git add migration/reporter.py tests/test_reporter.py
git commit -m "feat(reporter): COPY 类 origin 分组显示 new/modified 子计数"
```

---

## Task 7: 终验(全量回归 + acceptance 校验 + scan/diff 零回归)

**Files:**
- Test: `tests/test_e2e.py`(追加 acceptance 整合用例)

**Interfaces:**
- 无新产品代码;本 task 跑全量 pytest + ruff,并补一个端到端 acceptance 用例覆盖 spec 验收标准的关键点。

- [ ] **Step 1: 写 acceptance 整合测试(追加到 `tests/test_e2e.py` 末尾)**

```python
def test_e2e_acceptance_plan_format_and_origins(tmp_path: Path, monkeypatch, capsys):
    """spec 验收标准整合:plan_format=2;.bak→bak_file;rebuild→rebuild;白名单→must_migrate;
    scan/diff 零回归(snapshot 可读);对游戏目录零写入。"""
    import json
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    mini = versions / "mini"
    mini.mkdir(parents=True)
    (mini / "config").mkdir(parents=True)
    # 玩家改过的 config + 其 versioned .bak
    (mini / "config" / "create.toml").write_text("a=1\n", encoding="utf-8")
    (mini / "config" / "create-1.toml.bak").write_bytes(b"\x00")
    # 高危 rebuild 文件
    (mini / "config" / "fml.toml").write_text("x=1\n", encoding="utf-8")
    # 白名单文件(无 .bak 玩家偏好)
    (mini / "config" / "sodium-options.json").write_text("{}", encoding="utf-8")
    # 必迁
    (mini / "options.txt").write_text("v\n", encoding="utf-8")
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)

    # scan/diff 零回归:先 scan 再 diff 不报错
    assert _run(["scan", "mini", "--game-root", str(game_root)]) == 0
    assert _run(["scan", "target", "--game-root", str(game_root)]) == 0
    buf = io.StringIO()
    assert _run(["diff", "mini", "target", "--game-root", str(game_root), "--json"], buf) == 0
    json.loads(buf.getvalue())  # 可解析

    # plan
    capsys.readouterr()
    assert _run(["plan", "mini", "target", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    # 验收 2:plan_format=2
    assert doc["plan_format"] == 2
    origins = {a["path"]: a["origin"] for a in doc["actions"]}
    behaviors = {a["path"]: a["behavior"] for a in doc["actions"]}
    # 验收 1:.bak → bak_file(非 default_config)
    assert origins.get("config/create-1.toml.bak") == "bak_file"
    assert behaviors.get("config/create-1.toml.bak") == "copy"
    # 验收 1:高危文件 → rebuild
    assert origins.get("config/fml.toml") == "rebuild"
    assert behaviors.get("config/fml.toml") == "skip"
    # 验收 1:白名单 → must_migrate
    assert origins.get("config/sodium-options.json") == "must_migrate"


def test_e2e_scan_zero_regression_snapshot_format_unchanged(tmp_path: Path, monkeypatch):
    """验收 3:SNAPSHOT_FORMAT 不动,scan 产物可读。"""
    import json
    from migration.snapshot import SNAPSHOT_FORMAT, Snapshot, snapshot_path

    assert SNAPSHOT_FORMAT == 1  # 未改动
    game_root = tmp_path / "game"
    (game_root / "versions" / "mini").mkdir(parents=True)
    (game_root / "versions" / "mini" / "options.txt").write_text("v\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _run(["scan", "mini", "--game-root", str(game_root)]) == 0
    snap = Snapshot.load(snapshot_path(tmp_path, "mini"))  # 旧 snapshot 仍可读
    assert snap.file_count >= 1
```

- [ ] **Step 2: 跑全量测试**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS(全绿,预期总数 >111)。

- [ ] **Step 3: lint**

Run: `.venv\Scripts\python.exe -m ruff check .`
Expected: 无输出。

- [ ] **Step 4: 提交**

```bash
git add tests/test_e2e.py
git commit -m "test(e2e): planner 精修 acceptance 整合(plan_format=2/.bak/rebuild/白名单)"
```

- [ ] **Step 5(可选冒烟):真实数据 227→229**

> 若 `game_data` junction(指向真实游戏目录)可用,可手动冒烟(非测试):
> ```
> .venv\Scripts\python.exe -m migration scan 1.21.1-NeoForge_21.1.227 --game-root <game_data 绝对路径>
> .venv\Scripts\python.exe -m migration scan 1.21.1-NeoForge_21.1.229 --game-root <game_data 绝对路径>
> .venv\Scripts\python.exe -m migration plan 1.21.1-NeoForge_21.1.227 1.21.1-NeoForge_21.1.229 --game-root <...> --show-skip
> ```
> 人工核对:.bak 文件落在「📋 备份文件」,fml.toml 等落在「🔒 版本敏感」,sodium-options/DistantHorizons 落在「✅ 必迁」。

---

## Self-Review

**1. Spec coverage(逐节对照):**
- §1 2D 模型(Behavior/Origin/注册表/ActionRecord)→ Task 3 ✓
- §2 Origin 词表(11)+ ORIGIN_META → Task 3(`_ORIGIN_SEED`+`ORIGIN_REGISTRY`)✓
- §3 .bak 跟随父(resolve_bak_parent + 两趟 + 边角)→ Task 4 ✓
- §4 rebuild 层(Category.REBUILD + differ 路由 + rebuild.yaml + P2 优先级)→ Task 1/2/5 ✓
- §5 白名单扩充(+7 删 2)+ never 清理 → Task 5 ✓
- §6 PLAN_FORMAT 1→2 + 拒绝重跑 + SNAPSHOT 不动 → Task 3(PLAN_FORMAT)+ Task 7(SNAPSHOT 校验)✓
- §7 reporter(ORIGIN_META/分组/--category by origin/子计数)→ Task 3(分组)+ Task 6(子计数)✓
- §8 TDD 自底向上顺序 → Task 1→7 与 spec §8.3 一致 ✓
- §9 项目结构 + 版本号 bump → Task 3/5 ✓
- 验收标准 1-7 → Task 3/4/5/6 实现 + Task 7 整合测试 ✓

**2. Placeholder scan:** 无 TBD/TODO/"implement later";每步含完整代码或确切命令。✓

**3. Type consistency:**
- `Behavior`/`Origin` 在 Task 3 定义,Task 4/6 引用一致。✓
- `resolve_bak_parent(path, src_paths) -> str | None` 定义(Task 4 Step 4)与调用(Task 4 Step 6 `_for_bak`)签名一致。✓
- `ORIGIN_REGISTRY` 命名在 plan.py 定义、reporter.py/测试引用一致(spec §2.2 的 `ORIGIN_META` 名在本实现中=注册表,已在 Task 3 说明)。✓
- `load_rebuild_rules_from_text(text, source_name)` 定义(Task 1)与 cli 调用(Task 5)一致。✓
- `ActionRecord` 字段(behavior/origin/backup_target)在 plan/planner/reporter/测试中一致。✓

**注(Task 3 中间态说明):** Task 3 commit 时 `.bak` 文件本身仍落 `default_config`(沿用现状,非新 bug),Task 4 改为 `bak_file`。两 task 各自 commit 时整树绿,语义递进。

---

## Follow-up(从 final whole-branch review 遗留,均 safe-to-defer,可独立 PR 补)

> 本计划 7 个 task + final-review doc fix 已全部并入 `main`(commit 范围 `3b8cc1f..f471b4f`,140/140 测试通过、ruff 干净)。下列项在 final review 中被判定为「非阻塞、可推迟」,这里登记成可认领的工单,便于排期。

### 覆盖缺口(补测试,不改生产行为)

1. **identical-父 `.bak` 继承未测**(来源 Task 4)
   - 现状:`planner.py` `_for_bak` 的「父 SKIP→完整继承父」分支只被 `never`/rebuild-父路径覆盖;`identical`-桶父(→ .bak 继承 `(SKIP, IDENTICAL)`)未直接断言。该分支 origin-agnostic(代码行 `planner.py` 内 `return ActionRecord(behavior=parent.behavior, origin=parent.origin, ...)`),rebuild 路径已走过,故风险低。
   - 补法:在 `tests/test_planner.py` 加一条用例——父 config 进 `identical` 桶(note=verified),其 `.bak` 进 candidate;断言 .bak 的 `behavior==SKIP`、`origin==IDENTICAL`。

2. **rebuild > whitelist 冲突未测**(来源 Task 5)
   - 现状:优先级契约 P2 规定同一文件 rebuild 压过 whitelist;但无测试故意把同一文件同时放进两层。实际上按设计无真实文件同时命中两者(rebuild 是版本/硬件绑定、whitelist 是玩家偏好),且 `from_layers(cli, extra, user, rebuild, whitelist, default)` 顺序正确,属理论缺口。
   - 补法:在 `tests/test_cli.py` 或 `tests/test_rules.py` 加一条——临时把 `config/fml.toml` 也加进白名单(用 `--rule` 传一个 whitelist 风格文件,或 monkeypatch),断言 plan 中 fml.toml 仍落 `origin=rebuild`(rebuild 赢)。

3. **非 must_migrate 的 COPY origin 子计数标题未直接断言**(来源 Task 6)
   - 现状:`reporter.py` 子计数格式对 4 个 COPY 类 origin(must_migrate/config_modified/bak_file/mod_added)统一应用,但 `test_plan_reporter_new_modified_subcount` 只用 must_migrate 项跑通。另 3 个 origin 走同一代码路径,风险低。
   - 补法:在 `tests/test_reporter.py` 加参数化或额外用例——构造含 new+modified 的 `config_modified` 组(父 config + .bak)和 `mod_added` 组(新增+覆盖 jar),断言各自标题含「新增 X · 覆盖 Y 已备份」。

### 注释/文案微调(纯文档)

4. **whitelist.yaml 优先级注释不全**:`migration/data/whitelist.yaml:5` 注释写 `user rules > rebuild > whitelist > default rules`,漏了 cli/extra。建议与 `rebuild.yaml:3`、`cli.py` 的 `build_ruleset` docstring 对齐为完整链 `cli > extra > user > REBUILD > whitelist > default`。
5. **子计数测试 fixture 路径名**:`tests/test_reporter.py` 子计数测试用了 `server.dat`(单数),真实 MC 文件是 `servers.dat`。纯字符串 fixture、不影响分类,但易误导;可对齐为 `servers.dat`。
6. **子计数断言粒度**:同测试只断言「新增」「覆盖」两词,未断「已备份」与计数数字。可加 `assert "已备份" in out` 与具体计数断言以锁死格式。
7. **default_rules.yaml 注释风格**:`migration/data/default_rules.yaml` 新增的两条 never(`config/jei/world/**`、`config/ars_nouveau/search_index/**`)带行内 `#` 注释,与同段既有裸条目风格不一致。可统一(全裸或全注释)。

### 未来观察项(非本轮 review 缺口,登记备忘)

- **`ORIGIN_REGISTRY` 模块级可变 + `_DEFAULT_VISIBLE` 导入期冻结**:若未来 profiles/插件在导入后 `register_origin(visible=True)`,默认渲染视图不会自动纳入新 origin(需同步刷新 `_DEFAULT_VISIBLE`)。当前无动态注册场景,潜在。
- **rebuild 解析器镜像 vs 参数化**:`_parse_rebuild_doc` 镜像 `_parse_whitelist_doc`(用户已选「保持镜像」)。若将来出现第 4 个同类解析器(如 never-list),应转为带 `decide`/`source` 参数的通用 helper。
