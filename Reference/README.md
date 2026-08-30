# Reference — 迁移工具设计档案馆

本目录是 **mcmig 迁移工具的设计档案馆**，保存历史决策、设计推理与真实迁移观测数据。

> **与 AGENTS.md 的分工**
>
> | | 定位 | 内容 |
> |---|---|---|
> | `../AGENTS.md` | 项目规范总纲 | 当前事实、规则、目录结构、编码规范（**该怎样做**） |
> | `Reference/`（本目录） | 设计档案馆 | 决策**为什么**这么定的来龙去脉 + 版本演化的设计快照 + 真实观测（**怎么推导出来的**） |
>
> 找「当前规则」去 AGENTS.md；找「设计推理 / 历史版本 / 真实数据」留在本目录。

---

## 目录结构

```
Reference/
├── README.md              ← 本文件（导航索引）
├── discussions/           ← 工具设计原始讨论（整个项目的源头）
│   └── chat.md
├── specs/                 ← 版本设计规格（日期前缀，定稿待实现）
│   ├── 2026-07-02-migration-v0-design.md
│   ├── 2026-07-07-planner-v1-phase1-design.md
│   └── 2026-07-09-planner-refinement-design.md
├── plans/                 ← 实现计划（与 specs 同名配对，TDD 任务清单）
│   ├── 2026-07-02-migration-v0.md
│   └── 2026-07-07-planner-v1-phase1.md
├── design/                ← 子系统设计备忘（跨版本复用，单主题深挖）
│   ├── hashing-strategy.md
│   ├── classifier-rules.md
│   └── planner-rules.md
└── observations/          ← 真实迁移观测数据（验证策略、反哺迭代）
    ├── README.md          ← 本子目录规范（已合并到本文档下方）
    └── 228-to-233/        ← 一次真实迁移的完整观测时间线
```

---

## 文档关系图

```text
discussions/chat.md ──(演化出)──▶ specs/*  ──(同名配对)──▶ plans/*
     (源头)              (版本规格)                    (实现计划)
                          ▲
                          │(引用子系统备忘)
                  design/*┘
               (跨版本复用)

observations/* ──(真实数据反哺)──▶ specs / design / AGENTS.md / rules
   (验证)              （闭环：实测 → 修正设计 → 沉淀规则）
```

**核心关系**：
- `discussions/chat.md` 是源头，演化出所有 specs
- `specs/` 与 `plans/` **同名配对**（日期 + 版本名一致），spec 定「做什么」、plan 定「怎么一步步做」
- `design/` 是跨版本复用的子系统备忘，被多个 specs 引用（specs 里常见「详见 `design/xxx.md`」）
- `observations/` 提供真实数据，反哺闭环——修正 specs/design，并沉淀进 AGENTS.md 与 `migration/data/*.yaml`

---

## 按场景查找

| 我想… | 去看 |
|---|---|
| 了解工具整体设计哲学 / 五级分类 / 演进路线 | `discussions/chat.md` |
| 理解某版本的目标与边界 | `specs/YYYY-MM-DD-*-design.md` |
| 动手实现某版本（TDD 任务清单） | `plans/YYYY-MM-DD-*.md`（与 spec 同名配对） |
| 深挖某子系统的设计推理 | `design/*.md` |
| 看真实迁移数据验证策略 | `observations/<src>-to-<dst>/` |
| 查项目当前规则 / 事实（非历史决策） | 上一级 `../AGENTS.md` |

---

## 各目录详述

### discussions/ — 设计原始讨论

**`chat.md`**（约 2255 行，工具设计的起点）
- 起因：玩家 PCL 版本隔离升级后手动迁移繁琐的求助帖，逐步演化出整套工具设计
- 现有方案对比：NTFS Junction / Shared Resources Mod / 自动迁移脚本 / Prism 实例复制模式
- 五级文件生命周期分类：Persistent（永久）/ Regenerable（可重建）/ Derived（派生高危）/ World-bound（世界绑定）/ Volatile（一次性）
- 五层识别架构（80/20）：通用规则 80% → 行为识别 15% → Mod Profile 4% → 用户学习 1%
- 置信度机制（非追求 100% 自动化）、保守默认原则
- 分发策略（PyInstaller 单文件 exe + GitHub Actions 发版）、MVP 四模块（Scanner/Manifest/Planner/Executor）

### specs/ — 版本设计规格

> 每个文件是一个**版本的设计快照**，定稿后待实现。按日期前缀排序即版本演进顺序。

**`2026-07-02-migration-v0-design.md`** — v0 只读 scan/diff 工具
- 范围：纯只读 `scan` + `diff`，**绝不写入游戏目录**（唯一写入 `.mcmig/`）
- 6 模块架构（Scanner/Classifier/Snapshot/Differ/Reporter/CLI），Executor/Manifest 留待后续
- 核心解耦：扫描存原始清单（无分类），分类在「读快照→出报告」时按当前规则现算 → 改规则不重扫
- 分层哈希、规则引擎分类器、迁移导向 6 桶 Diff 语义、快照 schema、验收标准

**`2026-07-07-planner-v1-phase1-design.md`** — v1 Phase 1 Planner（`plan` 子命令）
- 仍纯只读：把 v0 的 6 桶 `DiffReport` 细化为 9 个可执行 action
- 实现 `.bak` 判定法（玩家改过的 config）+ 白名单层（无 .bak 的玩家偏好）
- plan 文件持久化（`.mcmig/plans/*.plan.json`）、规则优先级栈、置信度三档、TDD 任务分解

**`2026-07-09-planner-refinement-design.md`** — v1 Phase 1.5 Planner 精修
- 动机：真实 227→229 diff 暴露三个计划层缺陷（.bak 自身误判 / 高危文件靠巧合 / 白名单不全）
- 核心重构：2D 模型拆分 **Behavior**（操作维度，3 值极稳）+ **Origin**（语义维度，随路线图增长）
- `.bak` 跟随父 config（父迁子随，防慢性数据丢失）、新增 rebuild 高危层（版本敏感文件让目标重建）
- PLAN_FORMAT v2、白名单扩充（带证据分级）、决策摘要快览

### plans/ — 实现计划

> 与 `specs/` **同名配对**（日期 + 版本名一致）。每个文件是 TDD 任务清单，供 agent 按 `superpowers:executing-plans` / `subagent-driven-development` 逐步执行。

**`2026-07-02-migration-v0.md`**（约 2053 行）
- v0 自底向上实现：hashing → rules → classifier → snapshot → scanner → differ → reporter → cli 接线
- 每个任务：先写失败测试 → 验证失败 → 最小实现 → 验证通过 → 提交（中文 conventional commits）

**`2026-07-07-planner-v1-phase1.md`**（约 1580 行）
- Planner 自底向上：plan 数据模型 → rules 白名单扩展 → planner 核心逻辑（.bak 判定）→ PlanReporter → CLI
- 关键解耦：白名单在规则层注入，保证 diff 与 plan 对同一文件分类一致；v0 零回归

### design/ — 子系统设计备忘

> 跨版本复用的单主题深挖，每条决策都带「为什么」。被多个 specs 引用（specs 常写「详见 `design/xxx.md`」）。

**`hashing-strategy.md`** — 哈希策略
- 核心矛盾：全量哈希最精确但 93% 算力浪费在 mods 上换 0 信号；只看 size 又漏检文本
- 分层策略：文本（config/options/dat/脚本）全量 MD5 / mods 按文件名集合 / bulk（sqlite/zip/mca）按 size
- 关键判断：精确性需求不均匀；**mtime 是陷阱**（拷贝改 mtime → 误判）
- `--strict` 逃生口、Diff 规则与置信度标注（verified vs size-based）、性能预估、hash 缓存演进

**`classifier-rules.md`** — 分类规则
- 核心原则：**数据驱动规则引擎**（非代码 if-else），加规则不改代码
- 关键架构：扫描与分类解耦（快照存原始清单，分类时按当前规则现算）
- 规则优先级栈（分层 first-match-wins，类 gitignore）、pathspec glob 语义
- 规则格式（用户 `.mcmig/rules.yaml` + CLI 临时 + 内置 `default_rules.yaml`）、应对三种混乱（mod 缓存混入 config / 玩家丢个人文件 / 孤儿数据）

**`planner-rules.md`** — Planner 判定规则
- `.bak` 判定法：机制原理（NeoForge 玩家改动自动生成 .bak）、作用域（仅 `config/` 前缀）、命名模式、边界情况
- 白名单机制：与 `.bak` 判定的关系、优先级栈第 3 层
- mods 特殊处理（文件名集合，非字节 diff）、非 config candidate 的 ask、置信度三档、已知限制

### observations/ — 真实迁移观测

> 保存**真实迁移过程**的观测数据，用于验证和迭代工具的分类规则与策略准确性。每次真实迁移一个子目录，形成「实测 → 修正设计 → 沉淀规则」的闭环。

**命名规范**
- 子目录：`<源版本>-to-<目标版本>/`（如 `228-to-233/`）
- 文件：`00-05` 编号前缀 = 时间线顺序，便于按流程阅读
- JSON = 工具原始输出（只读存档）；MD = 人工分析

**沉淀流程**：每次观测完成后，检查是否有新发现回溯沉淀到 `AGENTS.md` 分类表 / `migration/data/*.yaml` / `Reference/design/`。

**目录结构规范**
```
observations/
└── <源>-to-<目标>/
    ├── 00-baseline.md       # 迁移前源/目标版本基线分析
    ├── 01-tool-diff.json    # 工具生成的 6 桶 diff（只读存档）
    ├── 02-tool-plan.json    # 工具生成的迁移 plan（只读存档）
    ├── 03-runtime-data.md   # 运行后实测数据（连服/游玩后）
    ├── 03b-orphan-data.md   # （可选）孤儿数据专项排查
    ├── 04-first-launch.md   # 目标版本首次启动结果 + mod 自动生成的文件
    └── 05-summary.md        # 观测总结：工具 vs 实际差异 + 新发现
```

**`228-to-233/`** — NeoForge patch 升级观测（21.1.228 → 21.1.233，唯一差异是 patch 版本）
- `00-baseline.md` — 迁移前源/目标基线；含「mods 整包覆盖致 FML crash」根因（mod 声明更高 NeoForge 要求）
- `01-tool-diff.json` / `02-tool-plan.json` — 工具原始输出存档
- `03-runtime-data.md` — 连服 10 分钟后运行时数据：Distant Horizons LOD / FTB Chunks 区块图 / servers.dat 生成
- `03b-orphan-data.md` — **重大发现**：`xaero/` 是旧版 modpack 残留孤儿数据，本 modpack 三版本 mods 中均无 Xaero jar（玩家按 M 打开的是 FTB Chunks 地图，非 Xaero）
- `04-first-launch.md` — 233 首启前后对比：mod 自动生成 defaultconfigs（验证「默认 config 不需迁」）
- `05-summary.md` — 工具 vs 实际差异总结 + 对工具的启示（如：迁移前应读 `META-INF/neoforge.mods.toml` 检查 mod 版本要求）

---

## 命名规范汇总

| 目录 | 命名模式 | 说明 |
|---|---|---|
| `specs/` | `YYYY-MM-DD-<版本名>-design.md` | 日期前缀 + 版本名 + `-design` 后缀 |
| `plans/` | `YYYY-MM-DD-<版本名>.md` | 与对应 spec 同日期同版本名（配对） |
| `design/` | `<主题-kebab-case>.md` | 跨版本长效，按主题命名 |
| `observations/` | `<源>-to-<目标>/` + `00-05` 编号 | 子目录按迁移路径，文件按时间线编号 |

---

## 维护提示

- **新增版本设计** → 同步产出 `specs/` + `plans/` 一对（同名配对），并在本 README 对应章节补 bullet
- **新增子系统深挖** → 放 `design/`，文件头部写「用途」+「配套 specs」引用
- **真实迁移观测** → 按 `observations/` 规范建 `<src>-to-<dst>/` 子目录，完成后回溯沉淀到 AGENTS.md / rules / design
- **文档间引用**用相对路径（如 `design/hashing-strategy.md`），便于跨文件跳转
