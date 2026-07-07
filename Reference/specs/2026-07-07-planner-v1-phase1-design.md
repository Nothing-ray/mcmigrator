# v1 Phase 1 设计规格：Planner（`plan` 子命令）

> 状态：定稿（待实现）
> 日期：2026-07-07
> 配套设计备忘：`design/planner-rules.md`（判定规则）、`design/hashing-strategy.md`、`design/classifier-rules.md`
> 前序规格：`specs/2026-07-02-migration-v0-design.md`（v0 scan/diff）
> 实现计划：`plans/2026-07-07-planner-v1-phase1.md`（待生成）

---

## 0. 目标与边界

- **范围**：新增 `mcmig plan <src> <dst>` 子命令 + Planner 模块。**仍纯只读，绝不写入游戏版本文件夹**（对齐 v0「零写入」哲学）。
- **唯一写入**：`.mcmig/plans/*.plan.json`（plan 持久化，工具自身 scratch 目录）。
- **核心增值**：
  1. 把 v0 的 6 桶 `DiffReport` 细化为可执行 action 列表（`copy_new`/`overwrite`/`skip_*`/`add_mod`/`ask`…）。
  2. 实现 AGENTS.md 的「config 玩家改动判定法」（`.bak` 法 + 白名单），把 `candidate` 桶自动分化为 `copy`/`skip_default_config`。
  3. plan 文件持久化，供后续 Executor 直接消费。
- **不在本 spec**：实际写盘（Executor）、Manifest 决策沉淀、Mod 感知、启动器活跃版本同步、回滚（见 §14）。

---

## 1. 模块边界与组件

| 文件 | 变更 | 职责 |
|---|---|---|
| `migration/plan.py` | **新增** | `Action` / `MigrationPlan` 数据模型 + JSON 持久化（对齐 `snapshot.py` 风格） |
| `migration/planner.py` | **新增** | `Planner`：消费 `DiffReport` + src_index → `MigrationPlan`（含 `.bak` 判定、白名单匹配） |
| `migration/rules.py` | **扩展** | 加 `load_whitelist_rules(path) -> tuple[list[Rule], list[str]]`（解析详写 yaml 的 `match`/`reason`，**每条强制 `decide=MUST_MIGRATE`**；白名单语义=「该迁的文件清单」，故不要求 yaml 写 `decide`） |
| `migration/data/whitelist.yaml` | **新增** | 无 `.bak` 但属玩家偏好的文件白名单（数据文件） |
| `migration/cli.py` | **扩展** | 新增 `plan` 子命令；`build_ruleset` 加 `with_whitelist` 开关 |
| `migration/reporter.py` | **扩展** | 加 `PlanReporter` + `PlanOptions` + `ACTION_META` 表（对齐 `DiffReporter`） |
| `tests/test_plan.py` | **新增** | Action/Plan 数据模型 + 持久化测试 |
| `tests/test_planner.py` | **新增** | 6 桶→action 映射 + `.bak` 判定 + 白名单 |
| `tests/conftest.py` | **扩展** | `build_mini_version` 加 `bak_files`/`whitelist_files` 参数 |
| `tests/test_rules.py` / `test_reporter.py` / `test_cli.py` / `test_e2e.py` | **扩展** | 追加白名单/PlanReporter/plan 子命令/e2e 测试 |

**关键职责分离**：
- `.bak` 判定放 **Planner**（需访问 src 清单查 `.bak` 是否存在，超出纯路径规则能力）。
- 白名单放 **rules.py**（纯路径，作为高优先级规则注入，复用现有 RuleSet 机制）。
- v0 的 `Scanner`/`Classifier`/`Differ`/`Snapshot` **一行不改**（Planner 只消费 Differ 产出）。

---

## 2. Planner 接口

```python
class Planner:
    def __init__(
        self,
        report: DiffReport,                  # 复用 v0 Differ 产出（6 桶；白名单已在规则层生效）
        src_index: dict[str, FileEntry],     # 用于 .bak 兄弟查找
    ) -> None: ...

    def plan(self) -> MigrationPlan: ...
```

> Planner 不重新分类——消费 Differ 已算好的 6 桶。只做两件事：(a) 桶→action 映射、(b) `.bak` 判定（需 src_index，仅对 `config/` 下 candidate）。
>
> **白名单不在 Planner 层**：白名单作为规则层（`build_ruleset(with_whitelist=True)`）注入，经 Classifier/Diver 分类后，命中的文件已在 `to_migrate` 桶，不会进入 candidate 分支。这与 v0 规则栈一致，且保证 diff 报告与 plan 报告对同一文件分类一致。

---

## 3. Action 词汇表（6 桶 → 9 action）

| action | 含义 | 来源桶 | 备份目标 |
|---|---|---|---|
| `copy_new` | 源有目标无，直接复制 | to_migrate/candidate (new) | 否 |
| `overwrite` | 两边都有但不同，源覆盖 | to_migrate/candidate (modified) | 是 → `_conflict_backup/<path>` |
| `skip_identical` | 两边一致 | identical | 否 |
| `skip_never` | 分类 `never` | never | 否 |
| `skip_default_config` | config 无 `.bak` 且不在白名单 | candidate（经 `.bak` 判定） | 否 |
| `add_mod` | 源独有 mod | mods (to_add) | 否 |
| `keep_mod` | 共有 mod | mods (shared) | 否 |
| `ignore_target_mod` | 目标独有 mod | mods (target_only) | 否 |
| `ask` | 仍无法判定（残余 candidate） | candidate 残余 | — |

> `skip_default_config` 是 `.bak` 判定法的产物——把「mod 默认值、玩家没改」的 config 识别出来不迁，让目标新默认生效（AGENTS.md「config 合并策略」核心要求）。

---

## 4. 判定规则

**完整判定逻辑见 `design/planner-rules.md`。** 本节仅述决策树要点：

```
对每个 DiffItem：
  ① mods/*.jar → mods 分支（add_mod / keep_mod / ignore_target_mod）
  ② 分类裁决：
     never        → skip_never
     must_migrate → copy_new / overwrite / skip_identical（看 note）
     ask          → ask
     candidate    → ③
  ③ candidate（path 在 config/ 下？）：
     是 → 白名单 > .bak 兄弟 > skip_default_config
     否 → ask
```

**关键约束**：`.bak` 判定**只作用于 `config/` 前缀**的 candidate。`.bak` 是 NeoForge config 持久化机制的特征，对 `kubejs/`/`resourcepacks/` 等无此机制，误用会导致玩家脚本/资源包被当「默认值」丢弃。

---

## 5. 规则优先级栈（新增白名单层）

```
1. CLI (--exclude/--include/--rule)   [最高]
2. user rules (.mcmig/rules.yaml)
3. whitelist (whitelist.yaml)          [本次新增]
4. default (default_rules.yaml + <ver> 展开)
5. unknown                             [最低]
```

- 白名单**高于 default**：把 default 归 unknown 的玩家偏好升级为 `must_migrate`。
- 白名单**低于 user rules**：用户可用 `rules.yaml` 覆盖（如不想要某 mod 偏好迁移）。

**`cli.py:build_ruleset` 扩展**——加 `with_whitelist` 开关：

```python
def build_ruleset(versions, args, mcmig_dir, *, with_whitelist: bool = False):
    ...
    whitelist, we = ([], [])
    if with_whitelist:
        whitelist, we = rules.load_whitelist_rules(...)
    rs = rules.RuleSet.from_layers(cli_rules, extra, user, whitelist, default)
    ...
```

> `with_whitelist` 默认 `False`——`scan`/`diff` 行为零回归。仅 `plan` 命令传 `True`。

---

## 6. Plan 文件 schema（`.mcmig/plans/<src>__<dst>.plan.json`）

```json
{
  "tool_version": "0.2.0",
  "plan_format": 1,
  "src": "1.21.1-NeoForge_21.1.227",
  "dst": "1.21.1-NeoForge_21.1.229",
  "generated_at": "2026-07-07T12:00:00+08:00",
  "summary": {
    "copy_new": 12,
    "overwrite": 3,
    "skip_identical": 200,
    "skip_never": 50,
    "skip_default_config": 150,
    "add_mod": 2,
    "keep_mod": 117,
    "ignore_target_mod": 0,
    "ask": 5
  },
  "actions": [
    {
      "path": "options.txt",
      "action": "copy_new",
      "src_size": 1234,
      "dst_size": null,
      "md5_match": null,
      "confidence": "high",
      "reason": "must_migrate + dst missing",
      "backup_target": null
    },
    {
      "path": "config/create.toml",
      "action": "overwrite",
      "src_size": 4812,
      "dst_size": 4800,
      "md5_match": false,
      "confidence": "high",
      "reason": ".bak sibling exists",
      "backup_target": "_conflict_backup/config/create.toml"
    },
    {
      "path": "config/some_default.toml",
      "action": "skip_default_config",
      "src_size": 1000,
      "dst_size": 998,
      "md5_match": false,
      "confidence": "high",
      "reason": "no .bak, not in whitelist",
      "backup_target": null
    }
  ]
}
```

- `tool_version` 升到 `0.2.0`（本 spec 产出）。
- `plan_format: 1`（向前兼容，对齐 `snapshot_format`）。
- `md5_match` 三态：`true` / `false` / `null`（两边都有 hash 比；`null` = size-based 或一边缺）。**不简化为布尔**——Executor 后续需区分「未比对」与「比对不一致」。
- `--json` 输出 = 此文件内容。
- **路径 helper**：`plan_path(workdir, src, dst) -> Path` 返回 `<workdir>/.mcmig/plans/<src>__<dst>.plan.json`。

---

## 7. PlanReporter 输出 + Action 元数据

### 7.1 `ACTION_META` 表（扩展性锚点）

新增 action 仅改此表，renderer 自动适配：

```python
ACTION_META = {
    # action:               title,                            default_visible, show_backup
    "copy_new":             ("✅ 新增(copy_new)",             True,            False),
    "overwrite":            ("🔄 覆盖(overwrite)",            True,            True),
    "add_mod":              ("📦 补 Mod(add_mod)",            True,            False),
    "ask":                  ("❓ 待确认(ask)",                True,            False),
    "skip_identical":       ("⏭ 一致(skip_identical)",       False,           False),
    "skip_never":           ("⛔ 不迁(skip_never)",           False,           False),
    "skip_default_config":  ("oze 默认配置(skip_default)",    False,           False),
    "keep_mod":             ("📦 共有 Mod(keep_mod)",         False,           False),
    "ignore_target_mod":    ("📦 目标独有 Mod(ignore)",       False,           False),
}
```

- `default_visible` 控制默认显示。
- `show_backup` 控制 `overwrite` 表多一列「备份目标」。
- `PlanOptions` 预留 `visible_actions: set[str] | None = None`（未来自定义可见集）。

### 7.2 rich 终端输出（按 action 分组）

```
plan: 1.21.1-NeoForge_21.1.227 → 1.21.1-NeoForge_21.1.229
汇总: copy_new=12, overwrite=3, add_mod=2, ask=5, skip_default_config=150, ...

✅ 新增(copy_new) (12)
┌──────────────────────────────┬──────────┬──────────────────┐
│ 路径                         │ 置信度   │ 原因             │
├──────────────────────────────┼──────────┼──────────────────┤
│ options.txt                  │ high     │ must_migrate     │
│ config/create.toml           │ high     │ .bak exists      │
│ config/jade/preset.json      │ medium   │ whitelist        │
└──────────────────────────────┴──────────┴──────────────────┘

🔄 覆盖(overwrite) (3)   [目标将备份到 _conflict_backup/]
┌──────────────────────────────┬──────────┬──────────────┬───────────────────────────────────┐
│ 路径                         │ 置信度   │ 原因         │ 备份目标                          │
├──────────────────────────────┼──────────┼──────────────┼───────────────────────────────────┤
│ config/foo.toml              │ high     │ .bak exists  │ _conflict_backup/config/foo.toml  │
└──────────────────────────────┴──────────┴──────────────┴───────────────────────────────────┘

📦 补 Mod(add_mod) (2)
┌────────────────────┐
│ mods/extra.jar     │
└────────────────────┘

❓ 待确认(ask) (5)
┌──────────────────────────────┐
│ kubejs/my_script.js          │
│ resourcepacks/my_pack.zip    │
└──────────────────────────────┘

[默认隐藏 skip_*，用 --show-skip 查看]
```

- 默认显示 4 个 action：`copy_new` / `overwrite` / `add_mod` / `ask`（用户需关注的动作）。
- 默认隐藏：`skip_*` / `keep_mod` / `ignore_target_mod`（不动作）。
- `overwrite` 表多一列「备份目标」，让用户清楚目标原文件会去哪。

---

## 8. `whitelist.yaml` 初版

```yaml
# 无 .bak 但属玩家偏好的文件白名单。
# 这些文件由 mod 直接写入玩家设置，不经过 NeoForge 的 .bak 机制，
# 故 .bak 判定法无法识别，需显式列入。
# 来源：AGENTS.md「config 玩家改动判定法」+ 实测 227 vs 228
# 优先级：user rules > whitelist > default rules（用户可用 rules.yaml 覆盖）

version: 1
rules:
  - match: "iris.properties"
    reason: "Iris 光影客户端设置（无 .bak 机制）"

  - match: "config/jade/**/*.json"
    reason: "Jade 显示偏好（无 .bak）"

  - match: "config/jei/*sort-order*"
    reason: "JEI 配方排序（无 .bak）"

  - match: "config/jei/bookmarks.ini"
    reason: "JEI 书签（无 .bak）"

  - match: "local/ftbchunks/**/ftbchunks-client.snbt"
    reason: "FTB Chunks 客户端偏好（无 .bak）"
```

> **初版**，落地后跑真实 227 vs 228 diff 看哪些文件误归 `skip_default_config`，再补条目。每条带 `reason` 便于回溯。

---

## 9. CLI（`plan` 子命令）

```
mcmig plan <src> <dst>
  [--game-root PATH]        # 游戏根目录（复用 _resolve_game_root）
  [--exclude GLOB]          # 本次按 never（可多次）
  [--include GLOB]          # 本次按 must_migrate（可多次）
  [--rule FILE]             # 额外规则文件（可多次）
  [--show-skip]             # 显示 skip_* 类（默认隐藏）
  [--category ACTION]       # 仅显示某 action
  [--json]                  # JSON 输出（= plan 文件内容）
  [--no-save]               # 不持久化 plan 文件（默认持久化）
  [-q/--quiet]              # 静默
```

**有意省略的 v0 参数（及原因）：**
- `--strict`：plan 读已有快照，不重扫（分层哈希已在快照里固化）。
- `--show-identical` / `--show-never`：统一用 `--show-skip` 覆盖所有 `skip_*`。
- `--mods`：用 `--category add_mod` 替代。

**`_cmd_plan` 流程**：load snapshots → `build_ruleset(with_whitelist=True)` → `Classifier(rs)` + `Differ(src, dst, clf).diff()` → `Planner(report, src_index).plan()` → `PlanReporter.render` + `save`（除非 `--no-save`）。

---

## 10. 置信度映射（三档）

| action | 来源 | confidence |
|---|---|---|
| `copy_new` / `overwrite` | must_migrate | high |
| `copy_new` / `overwrite` | candidate + `.bak` 判定 | high |
| `copy_new` / `overwrite` | candidate + 白名单 | medium |
| `skip_identical` | md5 verified | high |
| `skip_identical` | size-based | medium |
| `skip_never` / `skip_default_config` / `add_mod` / `keep_mod` / `ignore_target_mod` | — | high |
| `ask` | candidate 残余 | low |

> Executor 后续可基于 confidence 决定是否需额外确认（medium/low 触发提示）。

---

## 11. 错误处理（继承 v0 哲学）

| 情况 | 处理 |
|---|---|
| 缺快照（未 scan） | 报错 + 提示 `mcmig scan <ver>`，exit 2 |
| 规则语法错（whitelist/user/CLI） | 指出哪条 + 继续加载其余（v0 模式） |
| `whitelist.yaml` 缺失 | 返回空，不报错（对齐 user rules） |
| `.bak` 判定异常 | 不致错，降级为 `ask` |
| plan 文件写失败（权限/磁盘） | warn，但 stdout 仍打印 plan（命令算成功，exit 0） |
| 空目标（如全新 229） | 合法状态，大量 `copy_new`，非错误 |

---

## 12. 测试策略（TDD）

**原则**：先写失败测试 → 验证失败 → 最小实现 → 验证通过 → 提交（对齐 v0 plan 风格）。

### 12.1 Task 分解（自底向上）

| Task | 模块 | 测试要点 |
|---|---|---|
| 1 | `plan.py` | Action 字段、save/load 往返、`plan_path` helper、`plan_format` 不兼容抛 `PlanFormatError` |
| 2 | `rules.py` 扩展 | `load_whitelist_rules` 返回 must_migrate 规则；缺失文件返回空 |
| 3 | `planner.py` 基础 | 6 桶 → action 映射各一例；`overwrite` 算 `backup_target` |
| 4 | `planner.py` `.bak` 判定 | plain/versioned `.bak` 命中；无 `.bak` 且不在白名单 → `skip_default_config`；白名单覆盖；dst 有 `.bak` 不算；`.bak` 仅作用于 `config/` |
| 5 | `reporter.py` 扩展 | `PlanReporter.to_json` 可解析；`render` 不抛；`PlanOptions` 默认隐藏 skip |
| 6 | `cli.py` 扩展 | `plan` 写 plan 文件；缺快照友好报错；`--json`/`--show-skip`/`--no-save` |
| 7 | `test_e2e.py` | mini→mini_b 完整链路；`.bak`/白名单 fixture 命中正确 |

### 12.2 `conftest.py` 扩展（向后兼容）

```python
def build_mini_version(root, *, variant_b=False, bak_files=None, whitelist_files=None):
    """构建迷你版本文件夹。

    Args:
        bak_files: 要创建的 .bak 文件相对路径列表（模拟玩家改过的 config）。
        whitelist_files: 要创建的白名单文件相对路径列表（无 .bak 的玩家偏好）。
    """
    # ... 现有内容不变 ...
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

新增 fixture：`mini_version_with_bak`（带 `config/create-1.toml.bak`）、`mini_version_with_whitelist`（带 `config/jade/preset.json` + `iris.properties`）。

### 12.3 不测

- 真实游戏目录（手动验证）。
- 性能基准。
- PyInstaller 打包。

---

## 13. 项目结构 + 依赖

```
migration/
├── migration/
│   ├── plan.py             [新增]
│   ├── planner.py          [新增]
│   ├── rules.py            [扩展: load_whitelist_rules]
│   ├── cli.py              [扩展: plan 子命令]
│   ├── reporter.py         [扩展: PlanReporter]
│   ├── data/
│   │   ├── default_rules.yaml
│   │   └── whitelist.yaml  [新增]
│   └── ...（hashing/classifier/snapshot/scanner/differ 不变）
└── tests/
    ├── test_plan.py        [新增]
    ├── test_planner.py     [新增]
    └── ...（conftest/test_rules/test_reporter/test_cli/test_e2e 扩展）
```

- **本 spec 不引入新运行依赖**：仍 `rich` + `PyYAML` + `pathspec` + 标准库。
- `pyproject.toml`：`version` 升 `0.2.0`；`package-data` 已含 `data/*.yaml`（自动覆盖 whitelist.yaml）。

---

## 14. 不在本 spec 范围（明确边界）

| 项 | 后续 spec |
|---|---|
| 实际写盘（Executor：copy/overwrite/skip/backup） | v1 Phase 2 |
| Manifest 决策沉淀（首次确认后存决策，下次自动归类） | v1 Phase 3 |
| Mod 感知（读 `META-INF/neoforge.mods.toml`，profiles/） | 后续 |
| 孤儿 mod 数据识别（需 Mod 感知） | 后续 |
| 启动器活跃版本同步（`PCL.ini` + `PCL\Setup.ini`） | v1 Phase 2/3 |
| 启动失败检测 / 自动回滚 | 需 Executor，v1 Phase 2+ |
| NBT 解析（saves/dragon-survival 预设） | 后续 |
| hash 缓存（`(path,size,mtime)→md5` 跨 run 复用） | v0.1+ 增强 |

---

## 验收标准（本 spec 完成判定）

1. `mcmig plan 1.21.1-NeoForge_21.1.227 1.21.1-NeoForge_21.1.229` 产出 action 列表（rich），`.bak`/白名单命中正确。
2. plan 文件持久化到 `.mcmig/plans/227__229.plan.json`，可 `json.loads`，含 `summary` + `actions`。
3. 对游戏目录**零写入**（`plan` 命令纯只读，与 v0 一致；可用文件哈希前后对比验证）。
4. `--show-skip` / `--category ACTION` / `--json` / `--no-save` 全工作。
5. v0 的 `scan`/`diff` 行为**零回归**（白名单层仅 `plan` 命令启用）。
6. 全部单元测试 + e2e 通过；`ruff check .` 干净。
