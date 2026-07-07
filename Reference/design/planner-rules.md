# Planner 判定规则设计（Planner Rules）

> 用途：Planner 模块判定逻辑设计备忘，便于后续迭代时反查。
> 每条决策都带「为什么」。配套：见 `specs/` 下版本设计规格、`design/classifier-rules.md`、`design/hashing-strategy.md`。
> 背景动机：v0 的 Classifier 基于**纯路径 glob** 分类，覆盖绝大多数文件；但 `config/` 同时含「整合包默认值」与「玩家改动」，纯路径规则无法区分——Planner 的核心增值就是补上这层判定。

---

## 1. 背景与问题

v0 Classifier 把每个文件归 4 类（`never` / `must_migrate` / `unknown` / `ask`），基于纯路径 glob。对 `config/` 是个例外：

- `config/` 下既有**整合包默认值**（mod 首次运行生成），也有**玩家改过的配置**
- 整合包升级可能调整默认值 → 应让目标的新默认生效（**不迁**）
- 玩家改过的 → **应迁**（否则丢设置）
- **纯路径规则无法区分这两者**（都是 `config/*.toml`）

Planner 把 v0 的 candidate（unknown）桶细化成可执行 action。

---

## 2. 判定流程总览

Planner 消费 Differ 产出的 6 桶 `DiffReport`，对每个 item 决定 action：

```
对每个 DiffItem：

  ① 在 mods/ 下且 .jar？
     ├─ 是 → 走 mods 分支（文件名集合，见 §6）
     └─ 否 → ②

  ② 分类（category）是什么？
     ├─ never             → skip_never
     ├─ must_migrate      → 看 note：
     │                       new      → copy_new
     │                       modified → overwrite
     │                       verified → skip_identical
     ├─ ask               → ask（强制人工）
     └─ unknown(candidate)→ ③

  ③ candidate 决策树：
     path 在 config/ 下？
     ├─ 是 → config 判定（§3 + §4）：
     │       ① 命中白名单？        → copy_new / overwrite
     │       ② src 有 .bak 兄弟？  → copy_new / overwrite
     │       ③ 否则               → skip_default_config
     └─ 否（kubejs/resourcepacks/shaderpacks/个人文件…）
        → ask（人工确认，见 §5）
```

> copy_new vs overwrite 看 `note`：`new`（dst 无）→ `copy_new`；`modified`（两边都有但不同）→ `overwrite`。

---

## 3. `.bak` 判定法

### 3.1 机制原理（AGENTS.md 实测）

NeoForge 配置系统在玩家**游戏内修改 config 时**，自动把改动前的版本备份成 `.bak`：

```
玩家游戏内改了 config/create.toml 某项
    ↓
NeoForge 自动生成 config/create-1.toml.bak（改动前快照，N=备份版本号）
```

因此等价关系成立（仅对 `config/` 下文件）：

> `.bak` 存在 ⇔ 玩家改过该 config → **应迁**
> `.bak` 不存在 ⇔ mod 默认值 → **不迁**（让目标新默认生效）

### 3.2 作用域：仅 `config/` 前缀

**关键约束：`.bak` 判定只作用于 `path.startswith("config/")` 的 candidate。**

理由：`.bak` 是 NeoForge **config 持久化机制**的特征，不是文件系统通用特征。其他来源没有这套自动备份机制：

| candidate 来源 | 有 .bak 机制？ | 误用 .bak 判定的后果 |
|---|---|---|
| `config/*.toml` / `*.json` | ✅ NeoForge 自动 | —（正确） |
| `kubejs/**/*.js` | ❌ 脚本无自动备份 | **玩家脚本被当「默认值」丢弃**（灾难） |
| `resourcepacks/*.zip` | ❌ zip 无备份 | 误判 |
| `shaderpacks/*.zip` | ❌ 同上 | 误判 |
| 个人文件（截图/笔记） | ❌ | 误判 |

**反例**：玩家写的 `kubejs/my_script.js` 没有 `.bak`（kubejs 不生成），若用 `.bak` 判定会把它当成「mod 默认值」标 `skip_default_config`——这是灾难（玩家脚本被丢弃）。

### 3.3 命名模式（初版，待实测补充）

`.bak` 文件命名形态：

| 形态 | 例子 | 来源 |
|---|---|---|
| plain | `config/foo.toml.bak` | 常见 |
| versioned | `config/foo-N.toml.bak`（N=数字） | NeoForge 实测（`create-1.toml.bak` 等） |

判定函数：`has_bak_sibling(path: str, src_paths: set[str]) -> bool`

- plain：`path + ".bak" in src_paths`
- versioned：拆 `path` 为 `stem + suffix`，在 `src_paths` 中查 `stem + "-*" + suffix + ".bak"`（用 pathspec 或 fnmatch）

> **已知限制**：当前只覆盖 plain + versioned 两种形态。落地后若遇其他形态（如 `foo_N.toml.bak`、`foo.bak.toml`），需扩展 `has_bak_sibling` 的模式匹配并在测试加用例。见 §8。

### 3.4 查找范围：仅 src 清单

只查 **src**（源版本，玩家状态）的清单，不查 dst。

理由：判定「玩家是否改过」——以源为准。源没 `.bak` = 玩家在源版本里没改过，即使 dst 有 `.bak` 也不影响判定。

### 3.5 边界情况

| 情况 | 判定 |
|---|---|
| src 有 .bak，dst 无 | 玩家改过 → `copy_new`（若 dst 无该 config）或 `overwrite`（若 dst 有且不同） |
| src 无 .bak，dst 有 | 源没改过 → `skip_default_config`（dst 的 .bak 不算数） |
| src 有 config/foo.toml，无任何 .bak | mod 默认值 → `skip_default_config` |
| src 有多个 .bak（foo-1.bak + foo-2.bak） | 也算「改过」 → `copy`/`overwrite`（取 src 文件本身） |
| config 子目录（`config/jade/preset.json`） | 同样适用 `config/` 前缀判定；通常被白名单先命中 |

---

## 4. 白名单机制

### 4.1 用途

部分玩家偏好文件**无 `.bak` 机制**（mod 直接写，不经 NeoForge config 系统），`.bak` 判定识别不到，需显式列入：

| 文件 | 来源 mod | 为什么无 .bak |
|---|---|---|
| `iris.properties` | Iris | mod 直接写设置 |
| `config/jade/**/*.json` | Jade | 显示偏好，直接 JSON |
| `config/jei/*sort-order*` | JEI | 排序，直接写 |
| `config/jei/bookmarks.ini` | JEI | 书签 |
| `local/ftbchunks/**/ftbchunks-client.snbt` | FTB Chunks | 客户端偏好，SNBT 格式 |

### 4.2 与 `.bak` 判定的关系

白名单是 `.bak` 判定的**补充**（覆盖「无 .bak 的玩家偏好」），不冲突：

- 命中白名单 → 迁（无论有无 .bak）
- 未命中白名单但有 .bak → 迁
- 都没有 → `skip_default_config`

**实现位置（重要）**：白名单在**规则层**（`build_ruleset(with_whitelist=True)`）注入，经 Classifier/Differ 分类后，命中的文件归 `must_migrate`（在 `to_migrate` 桶），**不会进入 candidate 分支**。因此 §2 决策树里「① 命中白名单？」是逻辑等价表述——实际 Planner 看到的 candidate 已是「白名单未命中」的残余。`.bak` 判定才是 Planner 内部做的事。

### 4.3 优先级（规则栈第 3 层）

```
1. CLI (--exclude/--include/--rule)   [最高]
2. user rules (.mcmig/rules.yaml)
3. whitelist (whitelist.yaml)          [本次新增]
4. default (default_rules.yaml + <ver> 展开)
5. unknown                             [最低]
```

- **高于 default**：把 default 归 unknown 的玩家偏好升级为 `must_migrate`
- **低于 user rules**：用户可用 `rules.yaml` 覆盖（如某玩家就是不想要某 mod 偏好迁移）

### 4.4 数据文件

`migration/data/whitelist.yaml`（打包为数据文件，初版见 specs `2026-07-07-planner-v1-phase1-design.md` §7）。每条带 `reason` 字段。落地后跑真实 diff 看哪些文件误归 `skip_default_config`，再补条目。

---

## 5. 非 config candidate 的处理（ask）

对 `kubejs/`、`resourcepacks/`、`shaderpacks/`、个人文件等非 config candidate：

- **默认 `ask`**（人工确认）
- 理由：无可靠自动判定法；`ask` 是 v0「未知不自动迁」哲学的延续，**不会丢数据**
- 用户后续可用 `.mcmig/rules.yaml` 把特定路径（如 `resourcepacks/my_pack.zip`、`kubejs/**`）显式归类

> 后续 Mod Profile（META-INF 解析）可识别孤儿数据（目标已删 mod 留下的脚本等），本 spec 不实现。

---

## 6. mods 特殊处理（文件名集合）

`mods/**/*.jar` 不走 candidate 流程，在 Differ 阶段已单独按文件名集合分桶：

| 情况 | note | action |
|---|---|---|
| src 有、dst 无 | to_add | `add_mod` |
| 两边都有 | shared | `keep_mod`（不动） |
| src 无、dst 有 | target_only | `ignore_target_mod`（目标自带，不动） |

理由（见 `hashing-strategy.md`）：玩家不改 jar 内部，版本变 = 换文件名 → 按文件名集合比，不哈希、不判 .bak。

---

## 7. 置信度映射（三档）

| action | 来源 | confidence |
|---|---|---|
| `copy_new` / `overwrite` | must_migrate | high |
| `copy_new` / `overwrite` | candidate + `.bak` 判定 | high |
| `copy_new` / `overwrite` | candidate + 白名单 | medium |
| `skip_identical` | md5 verified | high |
| `skip_identical` | size-based | medium |
| `skip_never` / `skip_default_config` / `add_mod` / `keep_mod` / `ignore_target_mod` | — | high |
| `ask` | candidate 残余 | low |

Executor 后续可基于 confidence 决定是否需额外确认（medium/low 触发提示）。

---

## 8. 已知限制与待办

| 项 | 当前状态 | 待办 |
|---|---|---|
| `.bak` 命名模式 | 覆盖 plain + versioned | 落地后遇其他形态扩展 `has_bak_sibling` + 加测试用例 |
| 白名单条目 | 初版 5 条（AGENTS.md 列举） | 跑真实 227 vs 228 diff 补条目 |
| 非 config candidate | 一律 `ask` | 后续 Mod Profile 可识别孤儿数据 |
| 孤儿 mod 数据识别 | 未实现 | 需 META-INF 解析（后续 spec） |
| kubejs 模板 vs 玩家脚本区分 | 未实现 | 整合包作者侧需提供模板清单 |

---

## 决策摘要（给快速回顾）

> **一句话**：Planner 消费 Differ 的 6 桶；`config/` 下 candidate 用「白名单 > `.bak` 兄弟 > `skip_default_config`」三级判定（`.bak` 是 NeoForge config 机制的特征，**只对 `config/` 成立**）；非 config candidate 默认 `ask`（保守，不丢数据）；mods 按文件名集合独立处理；置信度三档（high/medium/low）。
