# 分类规则设计（Classifier Rules）

> 用途：迁移工具分类器的设计思路备忘，便于后续迭代时反查。
> 每条决策都带「为什么」。配套：见 `specs/` 下版本设计规格。
> 背景动机：mod 生态混乱 + 版本文件夹会混入非游戏文件，分类器必须容错且可扩展。

---

## 1. 核心原则：数据驱动规则引擎（非代码 if-else）

分类器**不能**是一堆硬编码 if-else。否则每遇到一个怪文件（mod 把缓存写进 `config/`、玩家往文件夹丢个人文件、新版删 mod 留下孤儿数据）都要改代码。

**结论**：分类 = 「规则集 + 匹配器」。规则是**数据**（YAML / CLI 参数），加规则不改代码。

---

## 2. 关键架构：扫描与分类解耦

| 层 | 职责 | 输出 |
|---|---|---|
| `Scanner` | 只管「有哪些文件」 | 原始清单（path/size/md5）→ 存快照 |
| `Classifier` | 只管「该文件算什么」 | 清单 + 规则集 → 带分类的视图 |
| `RuleSet` | 外部数据 | 可改、不碰代码 |

**关键**：快照存**原始清单（不存分类）**，分类在「读快照 → 出报告」时按**当前规则**现算。
- 收益：scan 一次 → 改规则 → 重新出报告，**零重扫**
- 配合快照持久化，构成理想的试验循环
- 规则改了快照不会过期（快照规则无关）

---

## 3. 规则优先级栈（分层 first-match-wins，类 gitignore）

所有规则按优先级展平，**第一个命中的决定分类**：

| 优先级 | 来源 | 时效 | v0 |
|---|---|---|---|
| 1（最高） | CLI 覆盖（`--exclude` / `--include` / `--rule FILE`） | 临时（本次运行） | ✓ |
| 2 | 用户规则（`.mcmig/rules.yaml`） | 半永久 | ✓ |
| 3 | Manifest 沉淀（之前确认过的决策） | 学习 | 接口预留，不实现 |
| 4（最低） | 内置默认（`default_rules.yaml`，本身是规则数据） | 内置 | ✓ |
| — | 无命中 | — | → `❓ unknown` |

> 选 first-match-wins 而非「最具体路径优先」：前者行为可预测（类 gitignore），后者在规则冲突时难推断。选分层而非「类别总赢」：避免 never/must 冲突时无清晰裁决。

---

## 4. glob 语义：pathspec（gitignore 语义）

v0 用 [`pathspec`](https://pypi.org/project/pathspec/) 库，行为与 `.gitignore` 完全一致（用户零学习成本、写错风险最低）。

关键约定（文档化给规则作者）：
- `*` —— 匹配单层路径段内（**不跨 `/`**）
- `**` —— 跨层段递归匹配
- 无 `/` 的模式 —— 匹配**任意深度**的文件名（如 `*.bak` 命中 `config/a.bak`）
- 带 `/` 的模式 —— **锚定版本根**（如 `config/*.toml` 仅命中 `config/foo.toml`，不命中 `config/sub/bar.toml`）
- `logs/**` —— logs 目录下递归全部
- 前导 `/` —— 显式锚定根

> 放弃 `fnmatch`（`*` 连 `/` 也跨，`config/*` 会误配 `config/a/b.toml`）与 `pathlib.match`（`**` 非贪婪、行为诡异）。

---

## 5. 规则格式

### 用户规则文件 `.mcmig/rules.yaml`
```yaml
version: 1
rules:
  - match: "screenshots/**"
    decide: never
    reason: "玩家截图，不迁"
  - match: "config/embeddium-options.json"
    decide: never
    reason: "含 GPU 指纹，高危"
  - match: "my_stuff/**"
    decide: must_migrate
    reason: "我自己的资料"
```

字段：
- `match`（必填）：glob，相对版本根，正斜杠
- `decide`（必填）：`never` | `must_migrate` | `unknown` | `ask`
- `reason`（强烈建议）：记录为什么，便于半年后回溯

### CLI 临时规则
```bash
mcmig scan <ver> --exclude "screenshots/**" --include "my_stuff/**" --rule extra.yaml
```
- `--exclude GLOB` = 本次按 `never`
- `--include GLOB` = 本次按 `must_migrate`
- `--rule FILE` = 本次额外加载规则文件

---

## 6. 内置默认规则（`migration/data/default_rules.yaml`）

打包为数据文件（非代码常量），作为最低优先级层加载——与用户规则同格式，纯数据驱动，可查看可覆盖。

```
never:         logs/**, crash-reports/**, <ver>.jar, <ver>.json, <ver>-natives/**,
               PCL/**, downloads/**, patchouli_books/**, patchouli_data.json,
               usercache.json, observable_announce, command_history.txt,
               defaultconfigs/**, **/cache/**

must_migrate:  options.txt, servers.dat, servers.dat_old, saves/**, schematics/**,
               xaero/**, XaeroWaypoints_*/**, local/ftbchunks/**,
               Distant_Horizons_server_data/**, dragon-survival/**
```
其余（`mods/`、`config/`、`kubejs/`、`resourcepacks/`、`shaderpacks/`、个人文件…）→ 默认 `unknown`。

> `<ver>.jar/.json/-natives` 为版本专属二进制，命名随版本变，匹配时按版本名展开。

---

## 7. 决策类别

| 类别 | 含义 | 报告表现 |
|---|---|---|
| `never` | ⛔ 绝不迁 | 默认隐藏（`--show-never` 看） |
| `must_migrate` | ✅ 必迁 | to_migrate 桶 |
| `unknown` | ❓ 待确认 | candidate 桶（需用户裁决） |
| `ask` | 强制单列 | v0（只读）归入报告「需决策」段；后续写操作阶段触发确认循环 |

---

## 8. 应对三种混乱

| 混乱 | 应对 |
|---|---|
| 个人文件混入（截图/笔记/备份 zip） | 默认落 `unknown` → 用 CLI 临时或 `rules.yaml` 永久归类 → 改完重新出报告验证 |
| mod 违反惯例（缓存写进 config/、用户数据写进 cache/） | 按 path 精确匹配规则，不靠「目录名猜 mod」；遇怪文件加针对性规则 |
| 孤儿 mod 数据（新版删 mod 留下遗留） | v0 落 `unknown` 由用户裁决；后续阶段用 `profiles/`（mod→数据目录映射）识别 |

---

## 9. v0 范围与演进

**v0 落地**：
- 数据驱动规则引擎（规则加载器 + pathspec 匹配）
- 三层规则源：内置默认 + `.mcmig/rules.yaml` + CLI 覆盖
- first-match-wins 裁决
- Manifest 沉淀层预留接口（不实现）

**后续演进**（核心代码几乎不改）：
1. Manifest 沉淀：未知项首次确认后存决策，下次自动归类
2. Mod Profile（`profiles/*.yaml`）：读 `META-INF/neoforge.mods.toml` 取 modid/version，按 mod 给规则
3. 内容检测：TOML/JSON Inspector 识别危险字段（`device_uuid`/GPU）→ 自动建议 `never`

---

## 决策摘要（给快速回顾）

> **一句话**：分类 = 数据驱动规则引擎；扫描/分类解耦（快照不存分类）；分层 first-match-wins（CLI > user rules.yaml > [Manifest] > default_rules.yaml > unknown）；glob 用 pathspec（gitignore 语义）；每条规则带 reason；mod 违惯例/个人文件/孤儿数据全靠加规则应对，不改代码。
