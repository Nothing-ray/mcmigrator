# v1 Phase 1 Planner(`plan` 子命令)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **注意:** 本文件因 Plan Mode 权限限制暂存于 `.opencode/plans/`。批准后应移至 `Reference/plans/2026-07-07-planner-v1-phase1.md`(项目惯例,与 v0 计划同目录)。

**Goal:** 实现 `mcmig plan <src> <dst>` 子命令,把 v0 的 6 桶 `DiffReport` 细化为 9 个可执行 action,实现 `.bak` 判定 + 白名单,plan 文件持久化到 `.mcmig/plans/`——仍纯只读,对游戏目录零写入。

**Architecture:** 自底向上:先数据模型(`plan.py`),再规则扩展(`rules.py` 加白名单加载),再核心逻辑(`planner.py` 消费 DiffReport + src_index 做 .bak 判定),最后渲染(`reporter.py` 的 `PlanReporter`)与 CLI 接线。关键解耦:白名单在规则层注入(`build_ruleset(with_whitelist=True)`),经 Classifier/Differ 后白名单命中的文件已在 `to_migrate` 桶,不进 candidate——保证 diff 与 plan 对同一文件分类一致;`.bak` 判定在 Planner 内(需 src_index 查兄弟,超出纯路径规则能力),仅对 `config/` 前缀的 candidate。v0 的 Scanner/Classifier/Differ/Snapshot **一行不改**。

**Tech Stack:** Python 3.11+ / `rich`(终端)/ `PyYAML`(规则)/ `pathspec`(glob)/ stdlib `json`·`dataclasses`·`enum`·`fnmatch`·`pathlib`·`datetime`。测试 pytest,lint ruff。无新运行依赖。

**配套设计:** `Reference/specs/2026-07-07-planner-v1-phase1-design.md`、`Reference/design/planner-rules.md`

**用户决策(已拍板):**
- `plan` 命令**不加 `--game-root`**(plan 纯读快照+规则,不碰游戏目录,YAGNI)
- `skip_default_config` 的 ACTION_META title 用 `⚙️ 默认配置(skip_default_config)`(spec §7 原文 emoji 编码损坏,此处修正)

---

## 全局约定

- **项目根**:仓库根 `mcmigrator/`,包目录 `migration/`(扁平布局,非 `migration/migration/`),测试 `tests/`。所有命令在仓库根执行。
- **TDD**:每个任务先写失败测试→验证失败→最小实现→验证通过→提交。
- **提交信息**:中文 conventional commits(如 `feat(planner): 实现 6 桶→action 映射`)。
- **编码**:所有文件 UTF-8 无 BOM;路径一律 `pathlib.Path`,不拼字符串。
- **注释**:中文;公有函数中文 docstring;无多余注释。
- **不回归**:v0 的 `scan`/`diff` 行为零回归(白名单层仅 `plan` 命令启用,`with_whitelist` 默认 False)。

## 文件结构

```
mcmigrator/                              ← 仓库根
├── pyproject.toml                       ← [改] version 0.1.0 → 0.2.0
├── migration/                           ← 包(扁平)
│   ├── __init__.py                      ← [改] __version__ 0.1.0 → 0.2.0
│   ├── snapshot.py                      ← [改] TOOL_VERSION 0.1.0 → 0.2.0
│   ├── plan.py                          ← [新增] Action/ActionRecord/MigrationPlan + 持久化
│   ├── planner.py                       ← [新增] Planner: DiffReport + src_index → MigrationPlan
│   ├── rules.py                         ← [扩展] load_whitelist_rules
│   ├── cli.py                           ← [扩展] build_ruleset 加 with_whitelist; plan 子命令
│   ├── reporter.py                      ← [扩展] PlanReporter + PlanOptions + ACTION_META
│   ├── data/
│   │   ├── default_rules.yaml           ← 不变
│   │   └── whitelist.yaml               ← [新增] 5 条白名单
│   └── ...(hashing/classifier/scanner/differ 不变)
└── tests/
    ├── conftest.py                      ← [扩展] build_mini_version 加 bak_files/whitelist_files
    ├── test_plan.py                     ← [新增]
    ├── test_planner.py                  ← [新增]
    └── ...(test_rules/test_reporter/test_cli/test_e2e 扩展)
```

**职责边界**:
- `plan.py`:数据模型 + JSON 持久化(对齐 `snapshot.py` 风格)。`TOOL_VERSION` 复用 `snapshot.TOOL_VERSION`(工具整体版本单一来源)。
- `planner.py`:决策逻辑,消费 `DiffReport` + `src_index`。
- `.bak` 判定放 Planner(需 src_index);白名单放 rules.py(纯路径,规则层注入)。
- `only_in_dst` 桶**不产生 action**:迁移只关心「源→目标」要做什么;目标独有文件与迁移无关,不进 actions 列表也不进 summary。

---

## Task 0: 版本号同步 + whitelist.yaml 数据文件

**Files:**
- Modify: `pyproject.toml:7`、`migration/__init__.py:3`、`migration/snapshot.py:12`
- Create: `migration/data/whitelist.yaml`

- [ ] **Step 1: 升版本号 0.1.0 → 0.2.0**

`pyproject.toml` 第 7 行:
```toml
version = "0.2.0"
```

`migration/__init__.py`:
```python
"""Minecraft 整合包版本迁移工具。"""

__version__ = "0.2.0"
```

`migration/snapshot.py` 第 12 行:
```python
TOOL_VERSION = "0.2.0"
```

> 三处同步:pyproject(打包元数据)、`__init__.__version__`(CLI `--version`)、`snapshot.TOOL_VERSION`(快照/plan 文件的 `tool_version` 字段)。plan.py 将 import 复用 `TOOL_VERSION`,不重复定义。

- [ ] **Step 2: 创建 whitelist.yaml**

`migration/data/whitelist.yaml`:
```yaml
# 无 .bak 但属玩家偏好的文件白名单。
# 这些文件由 mod 直接写入玩家设置,不经过 NeoForge 的 .bak 机制,
# 故 .bak 判定法无法识别,需显式列入。
# 来源:AGENTS.md「config 玩家改动判定法」+ 实测 227 vs 228
# 优先级:user rules > whitelist > default rules(用户可用 rules.yaml 覆盖)
# 语义:每条强制 decide=must_migrate(yaml 不写 decide)

version: 1
rules:
  - match: "iris.properties"
    reason: "Iris 光影客户端设置(无 .bak 机制)"

  - match: "config/jade/**/*.json"
    reason: "Jade 显示偏好(无 .bak)"

  - match: "config/jei/*sort-order*"
    reason: "JEI 配方排序(无 .bak)"

  - match: "config/jei/bookmarks.ini"
    reason: "JEI 书签(无 .bak)"

  - match: "local/ftbchunks/**/ftbchunks-client.snbt"
    reason: "FTB Chunks 客户端偏好(无 .bak)"
```

- [ ] **Step 3: 验证 package-data 覆盖**

Run: `python -c "from importlib import resources; print(resources.files('migration').joinpath('data/whitelist.yaml').is_file())"`
Expected: `True`

> `pyproject.toml` 已有 `migration = ["data/*.yaml"]`,whitelist.yaml 自动被打包,无需改 pyproject 的 package-data。

- [ ] **Step 4: 验证 v0 测试不回归**

Run: `pytest -q`
Expected: 全绿(版本号变更不影响逻辑)

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml migration/__init__.py migration/snapshot.py migration/data/whitelist.yaml
git commit -m "chore: 升版本 0.2.0 + 新增 whitelist.yaml 数据文件"
```

---

## Task 1: plan.py — Action/ActionRecord/MigrationPlan 数据模型

**Files:**
- Create: `migration/plan.py`
- Test: `tests/test_plan.py`

- [ ] **Step 1: 写失败测试**

`tests/test_plan.py`:
```python
import json
from pathlib import Path

import pytest

from migration.plan import (
    PLAN_FORMAT,
    Action,
    ActionRecord,
    MigrationPlan,
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
                action=Action.COPY_NEW,
                src_size=1234,
                dst_size=None,
                md5_match=None,
                confidence="high",
                reason="must_migrate + dst missing",
                backup_target=None,
            ),
            ActionRecord(
                path="config/create.toml",
                action=Action.OVERWRITE,
                src_size=100,
                dst_size=98,
                md5_match=False,
                confidence="high",
                reason=".bak sibling exists",
                backup_target="_conflict_backup/config/create.toml",
            ),
        ],
    )


def test_action_values():
    assert Action.COPY_NEW.value == "copy_new"
    assert Action.OVERWRITE.value == "overwrite"
    assert Action.SKIP_DEFAULT_CONFIG.value == "skip_default_config"
    assert Action.ASK.value == "ask"
    assert Action.ADD_MOD.value == "add_mod"


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


def test_summary_counts_by_action(tmp_path: Path):
    sp = tmp_path / "p.json"
    _sample().save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    assert doc["summary"]["copy_new"] == 1
    assert doc["summary"]["overwrite"] == 1
    assert doc["summary"]["skip_default_config"] == 0


def test_load_rejects_unsupported_format(tmp_path: Path):
    sp = tmp_path / "bad.json"
    sp.write_text(
        json.dumps({"plan_format": 999, "src": "a", "dst": "b", "generated_at": "", "actions": []}),
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
            ActionRecord("x1", Action.COPY_NEW, 1, None, None, "high", "r", None),
            ActionRecord("x2", Action.OVERWRITE, 1, 1, True, "high", "r", None),
            ActionRecord("x3", Action.OVERWRITE, 1, 1, False, "high", "r", None),
        ],
    )
    plan.save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    assert doc["actions"][0]["md5_match"] is None
    assert doc["actions"][1]["md5_match"] is True
    assert doc["actions"][2]["md5_match"] is False
```

- [ ] **Step 2: 运行验证失败**

Run: `pytest tests/test_plan.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'migration.plan'`)

- [ ] **Step 3: 实现 plan.py**

`migration/plan.py`:
```python
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
            reason=d.get("reason", ""),
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

- [ ] **Step 4: 运行验证通过**

Run: `pytest tests/test_plan.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add migration/plan.py tests/test_plan.py
git commit -m "feat(plan): 实现 Action/MigrationPlan 数据模型与 JSON 持久化"
```

---

## Task 2: rules.py 扩展 — load_whitelist_rules

**Files:**
- Modify: `migration/rules.py`(追加函数)
- Test: `tests/test_rules.py`(追加用例)

- [ ] **Step 1: 写失败测试(追加到 tests/test_rules.py 末尾)**

```python
def test_load_whitelist_rules_returns_must_migrate(tmp_path: Path):
    from migration import rules
    from migration.rules import Category

    f = tmp_path / "wl.yaml"
    f.write_text(
        "version: 1\nrules:\n"
        "  - match: 'iris.properties'\n    reason: 'Iris 设置'\n"
        "  - match: 'config/jade/**/*.json'\n    reason: 'Jade 偏好'\n",
        encoding="utf-8",
    )
    layer, errs = rules.load_whitelist_rules(f)
    assert errs == []
    assert len(layer) == 2
    assert all(r.decide == Category.MUST_MIGRATE for r in layer)
    assert layer[0].match == "iris.properties"
    assert layer[0].reason == "Iris 设置"
    assert layer[0].source == "whitelist"


def test_load_whitelist_rules_missing_file_returns_empty(tmp_path: Path):
    from migration import rules

    layer, errs = rules.load_whitelist_rules(tmp_path / "nope.yaml")
    assert layer == [] and errs == []


def test_load_whitelist_rules_bad_match_skipped(tmp_path: Path):
    from migration import rules

    f = tmp_path / "bad.yaml"
    f.write_text(
        "rules:\n  - match: ''\n    reason: '空 match'\n  - match: 'ok.txt'\n",
        encoding="utf-8",
    )
    layer, errs = rules.load_whitelist_rules(f)
    assert len(layer) == 1
    assert layer[0].match == "ok.txt"
    assert len(errs) == 1 and "match" in errs[0]


def test_whitelist_priority_above_default_below_user():
    """白名单层 > default,< user(rules.yaml)。"""
    from migration import rules
    from migration.rules import Category, Rule, RuleSet

    user = [Rule(match="iris.properties", decide=Category.NEVER, source="user")]
    whitelist = [Rule(match="iris.properties", decide=Category.MUST_MIGRATE, source="whitelist")]
    default = [Rule(match="iris.properties", decide=Category.UNKNOWN, source="default")]
    rs = RuleSet.from_layers(user, whitelist, default)
    assert rs.classify("iris.properties") == Category.NEVER  # user 优先

    rs2 = RuleSet.from_layers(whitelist, default)
    assert rs2.classify("iris.properties") == Category.MUST_MIGRATE  # whitelist 升级
```

- [ ] **Step 2: 运行验证失败**

Run: `pytest tests/test_rules.py -v -k whitelist`
Expected: FAIL(`AttributeError: module 'migration.rules' has no attribute 'load_whitelist_rules'`)

- [ ] **Step 3: 实现 load_whitelist_rules(追加到 rules.py 末尾)**

```python
def load_whitelist_rules(path: Path) -> tuple[list[Rule], list[str]]:
    """加载白名单规则文件(详写格式,但 decide 强制为 MUST_MIGRATE,不要求 yaml 写)。

    白名单语义=「该迁的文件清单」,每条 match/reason,decide 固定 MUST_MIGRATE。
    文件不存在时返回空(对齐 user rules)。优先级:user rules > whitelist > default。
    """
    if not path.exists():
        return [], []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return [], [f"{path}: YAML 解析失败: {e}"]
    rules_list: list[Rule] = []
    errors: list[str] = []
    if not isinstance(doc, dict):
        return [], [f"{path}: 文档非映射结构"]
    for i, raw in enumerate(doc.get("rules") or []):
        if not isinstance(raw, dict):
            errors.append(f"{path.name} 白名单 #{i}: 非映射")
            continue
        match = raw.get("match")
        if not match or not isinstance(match, str):
            errors.append(f"{path.name} 白名单 #{i}: 缺少 match")
            continue
        rules_list.append(
            Rule(
                match=match,
                decide=Category.MUST_MIGRATE,
                reason=str(raw.get("reason", "")),
                source="whitelist",
            )
        )
    return rules_list, errors
```

- [ ] **Step 4: 运行验证通过**

Run: `pytest tests/test_rules.py -v`
Expected: 原有用例 + 4 新用例全绿

- [ ] **Step 5: 提交**

```bash
git add migration/rules.py tests/test_rules.py
git commit -m "feat(rules): 加 load_whitelist_rules(白名单层,强制 must_migrate)"
```

---

## Task 3: planner.py 基础 — 6 桶→action 映射

**Files:**
- Create: `migration/planner.py`
- Test: `tests/test_planner.py`

> 本 Task 实现 6 桶→action 的基础映射(不含 `.bak` 判定,候选 config 一律先标 `ask`)。Task 4 再加 `.bak` 判定。

- [ ] **Step 1: 写失败测试**

`tests/test_planner.py`:
```python
from migration.differ import DiffItem, DiffReport
from migration.planner import Planner
from migration.plan import Action
from migration.snapshot import FileEntry


def _e(path, size=1, md5="x"):
    return FileEntry(path=path, size=size, md5=md5)


def _plan(report: DiffReport, src_entries: list[FileEntry] | None = None) -> list:
    src_index = {e.path: e for e in (src_entries or [])}
    return Planner(report, src_index).plan().actions


def test_to_migrate_new_goes_copy_new():
    report = DiffReport(to_migrate=[DiffItem("options.txt", _e("options.txt"), None, "new")])
    actions = _plan(report, [_e("options.txt")])
    a = next(a for a in actions if a.path == "options.txt")
    assert a.action == Action.COPY_NEW
    assert a.backup_target is None
    assert a.confidence == "high"


def test_to_migrate_modified_goes_overwrite_with_backup():
    report = DiffReport(
        to_migrate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="a"),
                             _e("config/foo.toml", md5="b"), "modified")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.action == Action.OVERWRITE
    assert a.backup_target == "_conflict_backup/config/foo.toml"
    assert a.md5_match is False


def test_identical_verified_goes_skip_identical_high():
    report = DiffReport(
        identical=[DiffItem("options.txt", _e("options.txt", md5="a"),
                            _e("options.txt", md5="a"), "verified")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "options.txt")
    assert a.action == Action.SKIP_IDENTICAL
    assert a.md5_match is True
    assert a.confidence == "high"


def test_identical_size_based_goes_skip_identical_medium():
    report = DiffReport(
        identical=[DiffItem("dh/lod.sqlite", _e("dh/lod.sqlite", size=16, md5=None),
                            _e("dh/lod.sqlite", size=16, md5=None), "size-based")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "dh/lod.sqlite")
    assert a.action == Action.SKIP_IDENTICAL
    assert a.md5_match is None
    assert a.confidence == "medium"


def test_never_goes_skip_never():
    report = DiffReport(never=[DiffItem("logs/latest.log", _e("logs/latest.log"), None, "never")])
    actions = _plan(report)
    a = next(a for a in actions if a.path == "logs/latest.log")
    assert a.action == Action.SKIP_NEVER


def test_mods_to_add_goes_add_mod():
    report = DiffReport(mods=[DiffItem("mods/extra.jar", _e("mods/extra.jar"), None, "to_add")])
    actions = _plan(report)
    a = next(a for a in actions if a.path == "mods/extra.jar")
    assert a.action == Action.ADD_MOD


def test_mods_shared_goes_keep_mod():
    report = DiffReport(
        mods=[DiffItem("mods/create.jar", _e("mods/create.jar"), _e("mods/create.jar"), "shared")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "mods/create.jar")
    assert a.action == Action.KEEP_MOD


def test_mods_target_only_goes_ignore_target_mod():
    report = DiffReport(
        mods=[DiffItem("mods/x.jar", None, _e("mods/x.jar"), "target_only")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "mods/x.jar")
    assert a.action == Action.IGNORE_TARGET_MOD


def test_only_in_dst_not_in_actions():
    """only_in_dst(目标独有)不产生 action——迁移只关心源→目标。"""
    report = DiffReport(
        only_in_dst=[DiffItem("config/target_only.toml", None, _e("config/target_only.toml"), "target_only")]
    )
    actions = _plan(report)
    assert all(a.path != "config/target_only.toml" for a in actions)


def test_candidate_non_config_goes_ask():
    """非 config/ 前缀的 candidate 一律 ask(Task 4 前,config candidate 也暂 ask)。"""
    report = DiffReport(
        candidate=[DiffItem("kubejs/my.js", _e("kubejs/my.js"), None, "new")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "kubejs/my.js")
    assert a.action == Action.ASK
    assert a.confidence == "low"
```

- [ ] **Step 2: 运行验证失败**

Run: `pytest tests/test_planner.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'migration.planner'`)

- [ ] **Step 3: 实现 planner.py(基础版,无 .bak 判定)**

`migration/planner.py`:
```python
"""Planner:消费 v0 的 DiffReport + src_index → 可执行 MigrationPlan。

决策树要点(完整见 design/planner-rules.md):
- mods 桶 → add_mod/keep_mod/ignore_target_mod(按 note)
- never → skip_never;identical → skip_identical(分 verified/size-based)
- to_migrate → copy_new(new)/overwrite(modified)
- candidate → config/ 前缀做 .bak 判定(Task 4),非 config → ask
- only_in_dst 不产生 action(目标独有,与迁移无关)
"""

from __future__ import annotations

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
        """candidate 决策:config/ 前缀做 .bak 判定(Task 4),非 config → ask。

        本 Task 基础版:config candidate 也暂 ask,Task 4 替换为 .bak 判定。
        """
        return self._ask(item, reason="candidate (TODO .bak in Task 4)")

    def _ask(self, item: DiffItem, *, reason: str) -> ActionRecord:
        return ActionRecord(
            path=item.path, action=Action.ASK,
            src_size=item.src.size if item.src else None,
            dst_size=item.dst.size if item.dst else None,
            md5_match=_md5_match(item.src, item.dst), confidence="low",
            reason=reason, backup_target=None,
        )
```

- [ ] **Step 4: 运行验证通过**

Run: `pytest tests/test_planner.py -v`
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
git add migration/planner.py tests/test_planner.py
git commit -m "feat(planner): 实现 6 桶→action 基础映射(候选暂 ask)"
```

---

## Task 4: planner.py — `.bak` 判定

**Files:**
- Modify: `migration/planner.py`(替换 `_for_candidate` + 加 helper)
- Test: `tests/test_planner.py`(追加用例)

- [ ] **Step 1: 写失败测试(追加到 tests/test_planner.py 末尾)**

```python
def test_config_candidate_with_plain_bak_goes_copy_new():
    """config/ 下 candidate + src 有 plain .bak → copy_new(dst 缺)。"""
    report = DiffReport(
        candidate=[DiffItem("config/create.toml", _e("config/create.toml", md5="a"), None, "new")]
    )
    src = [_e("config/create.toml"), _e("config/create.toml.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/create.toml")
    assert a.action == Action.COPY_NEW
    assert a.reason == ".bak sibling exists"
    assert a.confidence == "high"


def test_config_candidate_with_versioned_bak_goes_overwrite():
    """versioned .bak(foo-N.toml.bak)也算命中。"""
    report = DiffReport(
        candidate=[DiffItem("config/create.toml", _e("config/create.toml", md5="a"),
                            _e("config/create.toml", md5="b"), "modified")]
    )
    src = [_e("config/create.toml"), _e("config/create-1.toml.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/create.toml")
    assert a.action == Action.OVERWRITE
    assert a.backup_target == "_conflict_backup/config/create.toml"


def test_config_candidate_no_bak_goes_skip_default_config():
    """config/ 下 candidate + 无 .bak → skip_default_config。"""
    report = DiffReport(
        candidate=[DiffItem("config/default.toml", _e("config/default.toml", md5="a"),
                            _e("config/default.toml", md5="b"), "modified")]
    )
    src = [_e("config/default.toml")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/default.toml")
    assert a.action == Action.SKIP_DEFAULT_CONFIG
    assert a.confidence == "high"
    assert "no .bak" in a.reason


def test_bak_judgment_only_applies_to_config_prefix():
    """kubejs/ 下 candidate 即使有 .bak 兄弟也 → ask。"""
    report = DiffReport(
        candidate=[DiffItem("kubejs/my.js", _e("kubejs/my.js"), None, "new")]
    )
    src = [_e("kubejs/my.js"), _e("kubejs/my.js.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "kubejs/my.js")
    assert a.action == Action.ASK
    assert a.confidence == "low"


def test_bak_in_dst_only_does_not_count():
    """.bak 只查 src(dst 有 .bak 不算:判定玩家是否改过以源为准)。"""
    report = DiffReport(
        candidate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="a"),
                            _e("config/foo.toml", md5="b"), "modified")]
    )
    src = [_e("config/foo.toml")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.action == Action.SKIP_DEFAULT_CONFIG


def test_multiple_bak_versions_also_match():
    """foo-1.bak + foo-2.bak 都算改过。"""
    report = DiffReport(
        candidate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="a"), None, "new")]
    )
    src = [_e("config/foo.toml"), _e("config/foo-1.toml.bak"), _e("config/foo-2.toml.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.action == Action.COPY_NEW
```

- [ ] **Step 2: 运行验证失败**

Run: `pytest tests/test_planner.py -v -k bak`
Expected: FAIL(config candidate 应走 .bak 判定,不再 ask)

- [ ] **Step 3: 实现 .bak 判定**

在 `planner.py` 顶部 import 补充:
```python
import fnmatch
```

在 `Planner` 类上方加模块级函数:
```python
def has_bak_sibling(path: str, src_paths: set[str]) -> bool:
    """判断 src 清单中是否存在该文件的 .bak 兄弟(plain 或 versioned)。

    - plain: path + ".bak"(如 config/foo.toml.bak)
    - versioned: stem + "-*" + suffix + ".bak"(如 config/foo-1.toml.bak)

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
    pattern = f"{stem}-*{suffix}.bak"
    return any(fnmatch.fnmatch(p, pattern) for p in src_paths)
```

替换 `Planner._for_candidate`:
```python
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
```

- [ ] **Step 4: 运行验证通过**

Run: `pytest tests/test_planner.py -v`
Expected: 16 passed(原 10 + 新 6)

- [ ] **Step 5: 提交**

```bash
git add migration/planner.py tests/test_planner.py
git commit -m "feat(planner): 实现 .bak 判定(config candidate → copy/skip_default)"
```

---

## Task 5: reporter.py 扩展 — PlanReporter

**Files:**
- Modify: `migration/reporter.py`(追加 `PlanReporter`/`PlanOptions`/`ACTION_META`)
- Test: `tests/test_reporter.py`(追加用例)

- [ ] **Step 1: 写失败测试(追加到 tests/test_reporter.py 末尾)**

```python
def test_plan_reporter_to_json_parseable():
    import json
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
    assert doc["summary"]["copy_new"] == 1
    assert any(a["path"] == "options.txt" and a["action"] == "copy_new" for a in doc["actions"])


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


def test_plan_reporter_show_skip_renders_skip_actions(capsys):
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
    assert "skip_default" in out
```

- [ ] **Step 2: 运行验证失败**

Run: `pytest tests/test_reporter.py -v -k plan`
Expected: FAIL(`ImportError: cannot import name 'PlanReporter'`)

- [ ] **Step 3: 实现 PlanReporter(追加到 reporter.py 末尾)**

顶部 import 补充:
```python
from .plan import Action, MigrationPlan
```

追加:
```python
ACTION_META: dict[str, tuple[str, bool, bool]] = {
    # action: (title, default_visible, show_backup_column)
    "copy_new":            ("✅ 新增(copy_new)",          True,  False),
    "overwrite":           ("🔄 覆盖(overwrite)",         True,  True),
    "add_mod":             ("📦 补 Mod(add_mod)",         True,  False),
    "ask":                 ("❓ 待确认(ask)",             True,  False),
    "skip_identical":      ("⏭ 一致(skip_identical)",    False, False),
    "skip_never":          ("⛔ 不迁(skip_never)",        False, False),
    "skip_default_config": ("⚙️ 默认配置(skip_default)",  False, False),
    "keep_mod":            ("📦 共有 Mod(keep_mod)",      False, False),
    "ignore_target_mod":   ("📦 目标独有 Mod(ignore)",    False, False),
}

_DEFAULT_VISIBLE = [a for a, (_, vis, _) in ACTION_META.items() if vis]


@dataclass
class PlanOptions:
    """Plan 报告可见性控制。"""

    show_skip: bool = False
    category: str | None = None
    visible_actions: set[str] | None = None  # 预留


class PlanReporter:
    """把 MigrationPlan 渲染成 rich 终端表格或 JSON(= plan 文件内容)。"""

    def __init__(self, plan: MigrationPlan, *, src_version: str, dst_version: str) -> None:
        self.plan = plan
        self.src_version = src_version
        self.dst_version = dst_version

    def to_json(self) -> str:
        """JSON 输出 = plan 文件内容。"""
        import json

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

    def _visible_actions(self, opts: PlanOptions) -> list[str]:
        if opts.category:
            return [opts.category] if opts.category in ACTION_META else []
        if opts.visible_actions is not None:
            return [a for a in ACTION_META if a in opts.visible_actions]
        if opts.show_skip:
            return list(ACTION_META.keys())
        return _DEFAULT_VISIBLE

    def render(self, opts: PlanOptions, console: Console | None = None) -> None:
        """渲染 rich 终端报告(按 action 分组)。"""
        console = console or Console()
        console.print(
            f"[bold]plan:[/] [cyan]{self.src_version}[/] → [cyan]{self.dst_version}[/]"
        )
        summary = self.plan.summary()
        summary_str = ", ".join(
            f"{ACTION_META[a][0].split('(')[0]}{summary.get(a, 0)}"
            for a in ACTION_META
            if summary.get(a, 0) > 0
        )
        console.print(f"[dim]汇总: {summary_str}[/]")
        for action_key in self._visible_actions(opts):
            items = [r for r in self.plan.actions if r.action.value == action_key]
            if not items:
                continue
            title, _, show_backup = ACTION_META[action_key]
            tbl = Table(title=f"{title} ({len(items)})", title_style="bold")
            tbl.add_column("路径")
            tbl.add_column("置信度", style="dim")
            tbl.add_column("原因", style="dim")
            if show_backup:
                tbl.add_column("备份目标")
            for r in items:
                row = [r.path, r.confidence, r.reason]
                if show_backup:
                    row.append(r.backup_target or "")
                tbl.add_row(*row)
            console.print(tbl)
        if not opts.show_skip and not opts.category:
            console.print("[dim][默认隐藏 skip_*,用 --show-skip 查看][/]")
```

- [ ] **Step 4: 运行验证通过**

Run: `pytest tests/test_reporter.py -v`
Expected: 原有 + 4 新用例全绿

- [ ] **Step 5: 提交**

```bash
git add migration/reporter.py tests/test_reporter.py
git commit -m "feat(reporter): 实现 PlanReporter + ACTION_META 元数据表"
```

---

## Task 6: cli.py 扩展 — `plan` 子命令

**Files:**
- Modify: `migration/cli.py`
- Test: `tests/test_cli.py`(追加用例)

- [ ] **Step 1: 写失败测试(追加到 tests/test_cli.py 末尾)**

```python
def test_plan_writes_plan_file(mini_version: Path, tmp_path: Path, monkeypatch):
    import shutil
    from migration import cli
    from migration.plan import plan_path

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version), str(versions / "mini"))
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])

    code = cli.main(["plan", "mini", "target"])
    assert code == 0
    assert plan_path(tmp_path, "mini", "target").exists()


def test_plan_missing_snapshot_friendly_error(tmp_path: Path, monkeypatch, capsys):
    from migration import cli

    monkeypatch.chdir(tmp_path)
    code = cli.main(["plan", "a", "b"])
    out = capsys.readouterr().out
    assert code != 0
    assert "scan" in out


def test_plan_json_output(tmp_path: Path, mini_version: Path, monkeypatch, capsys):
    import json
    import shutil
    from migration import cli

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version), str(versions / "mini"))
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])

    code = cli.main(["plan", "mini", "target", "--json"])
    out = capsys.readouterr().out
    assert code == 0
    doc = json.loads(out)
    assert doc["src"] == "mini" and doc["dst"] == "target"
    assert "summary" in doc and "actions" in doc


def test_plan_no_save_skips_file(tmp_path: Path, mini_version: Path, monkeypatch):
    import shutil
    from migration import cli
    from migration.plan import plan_path

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version), str(versions / "mini"))
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])

    cli.main(["plan", "mini", "target", "--no-save"])
    assert not plan_path(tmp_path, "mini", "target").exists()


def test_plan_show_skip_includes_skip_actions(tmp_path, mini_version, monkeypatch, capsys):
    import shutil
    from migration import cli

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version), str(versions / "mini"))
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])

    cli.main(["plan", "mini", "target", "--show-skip"])
    out = capsys.readouterr().out
    assert "skip_default" in out or "skip_never" in out or "skip_identical" in out
```

- [ ] **Step 2: 运行验证失败**

Run: `pytest tests/test_cli.py -v -k plan`
Expected: FAIL(`plan` 子命令未注册)

- [ ] **Step 3: 实现 plan 子命令(修改 cli.py)**

3a. 修改 `build_ruleset` 签名,加 `with_whitelist` 开关:
```python
def build_ruleset(
    versions: str | list[str],
    args: argparse.Namespace,
    mcmig_dir: Path,
    *,
    with_whitelist: bool = False,
) -> tuple[rules.RuleSet, list[str]]:
    """按优先级(CLI > extra > user > whitelist > default)组装 RuleSet。

    with_whitelist=True 时插入白名单层(仅 plan 命令启用),scan/diff 零回归。
    """
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
    whitelist: list[rules.Rule] = []
    if with_whitelist:
        from importlib import resources

        wl_path = Path(str(resources.files("migration").joinpath("data/whitelist.yaml")))
        whitelist, we = rules.load_whitelist_rules(wl_path)
        errors.extend(we)
    default, de = rules.load_default_rules(versions)
    errors.extend(de)
    rs = rules.RuleSet.from_layers(cli_rules, extra, user, whitelist, default)
    return rs, errors
```

3b. 在 `build_parser` 内 `p_diff` 之后加 `plan` 子命令:
```python
    p_plan = sub.add_parser("plan", help="生成迁移计划(只读,产出 action 列表)")
    p_plan.add_argument("src", help="源版本名")
    p_plan.add_argument("dst", help="目标版本名")
    p_plan.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    p_plan.add_argument("--include", action="append", default=[], metavar="GLOB")
    p_plan.add_argument("--rule", action="append", default=[], metavar="FILE")
    p_plan.add_argument("--show-skip", action="store_true", help="显示 skip_*/keep_mod/ignore")
    p_plan.add_argument("--category", default=None, help="仅显示某 action")
    p_plan.add_argument("--json", action="store_true")
    p_plan.add_argument("--no-save", action="store_true", help="不持久化 plan 文件")
    p_plan.add_argument("-q", "--quiet", action="store_true")
```

3c. 加 `_cmd_plan` 函数(在 `_cmd_diff` 之后):
```python
def _cmd_plan(args: argparse.Namespace) -> int:
    """plan 子命令:load snapshots → diff → plan → 渲染 + 持久化。"""
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
    rs, errs = build_ruleset([args.src, args.dst], args, mcmig_dir, with_whitelist=True)
    for e in errs:
        _print(f"[规则警告] {e}")
    clf = Classifier(rs)
    report = Differ(src.files, dst.files, clf).diff()
    src_index = {e.path: e for e in src.files}
    plan = Planner(report, src_index).plan()
    plan.src, plan.dst = args.src, args.dst
    reporter = PlanReporter(plan, src_version=args.src, dst_version=args.dst)
    if args.json:
        _print(reporter.to_json())
    else:
        reporter.render(PlanOptions(show_skip=args.show_skip, category=args.category))
    if not args.no_save:
        try:
            plan.save(plan_path(cwd, args.src, args.dst))
        except OSError as e:
            _print(f"[警告] plan 文件写入失败(已忽略,stdout 仍有效): {e}")
    return 0
```

3d. 在 `main` 派发:
```python
    if args.command == "plan":
        return _cmd_plan(args)
```

3e. 补 import(cli.py 顶部):
```python
from .plan import plan_path
from .planner import Planner
from .reporter import PlanReporter, PlanOptions
```

- [ ] **Step 4: 运行验证通过**

Run: `pytest tests/test_cli.py -v`
Expected: 原有 + 5 新用例全绿

- [ ] **Step 5: 提交**

```bash
git add migration/cli.py tests/test_cli.py
git commit -m "feat(cli): 实现 plan 子命令 + build_ruleset 加 with_whitelist"
```

---

## Task 7: e2e + conftest 扩展

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_e2e.py`(追加 plan e2e 用例)

- [ ] **Step 1: 扩展 conftest.py**

修改 `build_mini_version` 签名与尾部:
```python
def build_mini_version(
    root: Path,
    *,
    variant_b: bool = False,
    bak_files: list[str] | None = None,
    whitelist_files: list[str] | None = None,
) -> Path:
    """构建一个迷你版本文件夹,返回其路径。

    Args:
        variant_b: 做改动用于 diff。
        bak_files: 要创建的 .bak 文件相对路径列表(模拟玩家改过的 config)。
        whitelist_files: 要创建的白名单文件相对路径列表(无 .bak 的玩家偏好)。
    """
    root.mkdir(parents=True, exist_ok=True)
    # ...(原有内容不变)...
    # 末尾追加:
    for bak_rel in bak_files or []:
        p = root / bak_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00")
    for wl_rel in whitelist_files or []:
        p = root / wl_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
    return root
```

新增 fixture:
```python
@pytest.fixture
def mini_version_with_bak(tmp_path: Path) -> Path:
    """带 .bak 的 mini(模拟玩家改过 config/create.toml)。"""
    return build_mini_version(
        tmp_path / "mini_bak",
        bak_files=["config/create-1.toml.bak"],
    )


@pytest.fixture
def mini_version_with_whitelist(tmp_path: Path) -> Path:
    """带白名单文件的 mini(iris.properties + jade preset)。"""
    return build_mini_version(
        tmp_path / "mini_wl",
        whitelist_files=["iris.properties", "config/jade/preset.json"],
    )
```

- [ ] **Step 2: 写 e2e 测试(追加到 tests/test_e2e.py 末尾)**

```python
def test_e2e_plan_bak_judgment(mini_version_with_bak: Path, tmp_path: Path, monkeypatch):
    """.bak 命中 → config candidate 升级为 copy_new。"""
    import json
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version_with_bak), str(versions / "mini"))
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    _run(["scan", "mini", "--game-root", str(game_root)])
    _run(["scan", "target", "--game-root", str(game_root)])

    buf = io.StringIO()
    _run(["plan", "mini", "target", "--json"], buf)
    doc = json.loads(buf.getvalue())
    actions = {a["path"]: a["action"] for a in doc["actions"]}
    assert actions.get("config/create.toml") == "copy_new"


def test_e2e_plan_whitelist_upgrades_to_migrate(mini_version_with_whitelist: Path, tmp_path: Path, monkeypatch):
    """白名单命中的文件在规则层归 must_migrate → copy_new(不进 candidate)。"""
    import json
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version_with_whitelist), str(versions / "mini"))
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    _run(["scan", "mini", "--game-root", str(game_root)])
    _run(["scan", "target", "--game-root", str(game_root)])

    buf = io.StringIO()
    _run(["plan", "mini", "target", "--json"], buf)
    doc = json.loads(buf.getvalue())
    actions = {a["path"]: a["action"] for a in doc["actions"]}
    assert actions.get("iris.properties") == "copy_new"
    assert actions.get("config/jade/preset.json") == "copy_new"


def test_e2e_plan_no_write_to_game_dir(mini_version: Path, tmp_path: Path, monkeypatch):
    """plan 命令对游戏目录零写入(验收标准 3)。"""
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version), str(versions / "mini"))
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    _run(["scan", "mini", "--game-root", str(game_root)])
    _run(["scan", "target", "--game-root", str(game_root)])

    before = {p: p.stat().st_mtime_ns for p in game_root.rglob("*") if p.is_file()}
    _run(["plan", "mini", "target"])
    after = {p: p.stat().st_mtime_ns for p in game_root.rglob("*") if p.is_file()}
    assert before == after


def test_e2e_plan_default_config_skipped(tmp_path: Path, monkeypatch):
    """config 下无 .bak 且不在白名单 → skip_default_config。"""
    import json
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    mini = versions / "mini"
    mini.mkdir(parents=True)
    (mini / "config").mkdir()
    (mini / "config" / "default.toml").write_text("a=1\n", encoding="utf-8")
    (mini / "options.txt").write_text("v\n", encoding="utf-8")
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    _run(["scan", "mini", "--game-root", str(game_root)])
    _run(["scan", "target", "--game-root", str(game_root)])

    buf = io.StringIO()
    _run(["plan", "mini", "target", "--json"], buf)
    doc = json.loads(buf.getvalue())
    actions = {a["path"]: a["action"] for a in doc["actions"]}
    assert actions.get("config/default.toml") == "skip_default_config"
```

- [ ] **Step 3: 运行验证通过**

Run: `pytest tests/test_e2e.py -v`
Expected: 原有 + 4 新 e2e 全绿

- [ ] **Step 4: 全量回归 + lint**

Run: `pytest -q && ruff check .`
Expected: 全绿 + lint 干净

- [ ] **Step 5: 提交**

```bash
git add tests/conftest.py tests/test_e2e.py
git commit -m "test(e2e): plan 完整链路验收(.bak/白名单/零写入/skip_default)"
```

---

## 验收清单(对应 spec §验收标准)

- [ ] `mcmig plan 227 229` 产出 action 列表(rich),`.bak`/白名单命中正确
- [ ] plan 文件持久化到 `.mcmig/plans/227__229.plan.json`,可 `json.loads`,含 `summary` + `actions`
- [ ] 对游戏目录**零写入**(e2e `test_e2e_plan_no_write_to_game_dir` 验证)
- [ ] `--show-skip` / `--category` / `--json` / `--no-save` 全工作
- [ ] v0 的 `scan`/`diff` **零回归**(`with_whitelist` 默认 False,白名单层仅 plan 启用)
- [ ] 全部单元测试 + e2e 通过;`ruff check .` 干净

## Self-Review

**1. Spec 覆盖**:§1-13 全覆盖(§1 模块边界 / §2 Planner 接口 / §3 9 action / §4 判定规则 / §5 规则栈 / §6 plan schema / §7 PlanReporter / §8 whitelist.yaml / §9 CLI / §10 置信度 / §11 错误处理 / §12 测试 / §13 结构)。§14 不在范围。

**2. 占位符扫描**:无最终占位符(Task 3 的 `TODO .bak` 是临时标记,Task 4 替换)。所有步骤含完整代码。

**3. 类型一致性**:
- `Action` enum 值与 `ACTION_META` key 一致(9 个)
- `ActionRecord` 字段在 Task 1 定义,Task 3-4 构造参数一致
- `Planner.__init__(report, src_index)` 与 Task 6 调用一致
- `build_ruleset(..., *, with_whitelist=False)` 与 Task 6 调用 `with_whitelist=True` 一致

**4. 兼容性**:
- `build_ruleset` 加 keyword-only 参数默认 False → scan/diff 零回归
- `TOOL_VERSION` 升 0.2.0,`SNAPSHOT_FORMAT` 不变 → 旧快照仍可读
- v0 的 Scanner/Classifier/Differ/Snapshot/hashing 一行不改

## 待落地后补(不在本计划)

- 跑真实 227 vs 229 diff → 补 whitelist.yaml 条目
- 遇新 `.bak` 命名形态 → 扩展 `has_bak_sibling` + 加测试
