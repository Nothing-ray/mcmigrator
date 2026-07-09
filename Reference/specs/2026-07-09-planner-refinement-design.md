# v1 Phase 1.5 设计规格：Planner 精修（2D 行为/语义模型 + .bak 跟随 + rebuild 层）

> 状态：设计定稿（Q1–Q8 全部锁定），待写实现计划。
> 前置：v0（scan/diff 只读）、v1 Phase 1（plan 只读）已完成。
> 动机：真实 `1.21.1-NeoForge_21.1.227 → 1.21.1-NeoForge_21.1.229` diff 暴露三个计划层缺陷，本 spec 修复之。纯计划层改动，**零写盘**，scan/diff 上游零回归。

## 0. 目标与边界

### 0.1 真实数据暴露的三个问题

| # | 问题 | 现状（坏） | 后果 |
|---|---|---|---|
| 1 | `.bak` 文件自身被当成 candidate | `config/create-client-1.toml.bak` 查自己的 .bak 兄弟（没有）→ `skip_default_config` | 父 config 迁了但 .bak 没迁 → 下次迁移失去"玩家改过"信号 → **慢性数据丢失** |
| 2 | 高危版本敏感文件靠"无 .bak"巧合 | `config/fml.toml` 等进 candidate，仅因没 .bak 才被跳过 | 一旦带 .bak 就会被迁 → 版本绑定文件跨版本复制 → **崩溃** |
| 3 | 白名单需扩充 | 真实数据有一批"无 .bak 的玩家偏好"未被覆盖（sodium-options/jei/MouseTweaks…） | 玩家画质/UI 偏好被当默认值丢弃 |

### 0.2 范围

- **改**：计划层 `plan.py` / `planner.py` / `reporter.py` / `rules.py` / `differ.py` / `cli.py` + 数据文件（新增 `rebuild.yaml`、扩 `whitelist.yaml`、补 `default_rules.yaml`）。
- **不动**：扫描层（scanner/hashing/snapshot）、对比层上游逻辑（classifier/differ 的桶路由仅加 REBUILD 一支）、`FileEntry`、`SNAPSHOT_FORMAT`。
- **不在本 spec**（明确边界，见 §10）：Executor 写盘、Manifest、Mod 感知、NBT、自动回滚。

## 1. 核心重构：2D 模型（Behavior + Origin）

### 1.1 动机

现状 `Action` 枚举把"做什么（操作）"和"为什么（语义来源）"揉在一个维度，且单一维度同时伺候两个口味相反的消费者：**reporter**（显示，要语义细）和 **Executor**（写盘，要操作少）。拆成两个独立维度（组合，非继承）：
- **behavior** = 操作（Executor 关心，3 值极稳）
- **origin** = 语义来源（reporter 关心，随路线图增长）

二者当前 1:1（每个 origin 恰好一个 behavior），但**显式分离 behavior** 的价值在：①Executor 永远只 switch 3 个 behavior，不被 origin 词表增长拖累（若只有 origin，Executor 需维护一张随 origin 膨胀的映射表）；②未来 origin 可能需要更细行为（如"先提示再复制"），显式 behavior 可承载无需回填。`.bak` 案例推动我们审视这种揉合——它的 new/modified 区别其实由 `backup_target` 字段（非 behavior）携带，但暴露了"单枚举伺候两消费者"的结构性问题。

### 1.2 Behavior（操作维度，闭合枚举，3 值，极稳）

```python
class Behavior(str, Enum):
    COPY = "copy"    # 复制 src→dst（若 backup_target 非空，先备份 dst）
    SKIP = "skip"    # 不动
    ASK = "ask"      # 需人工确认（非交互→SKIP，交互→提示）
```

**为什么只有 3 个**：`copy_new`/`overwrite`/`add_mod` 操作上都是"复制"，唯一差别由**现有数据字段**携带——
- `overwrite` 的"先备份"由 `backup_target` 字段（已存在，planner 仅 overwrite 时填）驱动：Executor 的 COPY 分支 `if backup_target: 先备份; 复制`。
- `add_mod` 的"放 mods/"由 path 自带（`mods/` 前缀）。
- 对称性：path 能干活不开 behavior，backup_target 同理。

Executor（未来）的 switch 永远 3 分支；所有语义区分甩给 origin。

### 1.3 Origin（语义维度，闭合 str-Enum + 注册表）

```python
class Origin(str, Enum): ...  # 见 §2 词表
```

- **表示形式 = `Origin(str, Enum)`（11 个已知成员，§2）+ `ORIGIN_REGISTRY` 注册表**。采用「闭合核心 + 开放注册」：既非纯闭合枚举（不够扩展）、也非纯开放字符串（不安全）。
  - `Origin(str, Enum)` 是**已知词表的类型安全容器**：planner 发射时用 `Origin.MUST_MIGRATE` 这类成员，typo 在 import 期即报。
  - `ORIGIN_REGISTRY: dict[str, OriginSpec]` + `register_origin(key, *, title, visible, show_backup, behavior)` 是**元数据与扩展接缝**：启动时从 `Origin` 全部成员播种（替代原静态 `ACTION_META`）。比静态表更贴切（origin 本质会长），且为未来 `profiles/*.yaml`/插件 `register_origin` 注入**新** origin 预留——零改核心代码。emoji 不单列字段,并进 `title`(如 "✅ 必迁");`behavior` 为结构契约(2D 模型 1:1 不变量),非显示皮。
  - **v1.5 校验**：plan.json 加载时 `Origin(value)` 解析，未知即 `PlanFormatError`（v1.5 无数据注入路径，registry ≡ Enum，校验等价于闭合枚举）。**未来**开放数据自定义 origin 时，校验放宽为 registry 查表（届时 origin 字段类型从 `Origin` 宽为 `str`，向后兼容）。
  - **一句话**：安全来自"必须注册才合法"（v1.5 = 必须是 Enum 成员；未来 = 必须在 registry），不是"写死"。Enum 是已知词表容器，registry 是校验+扩展接缝，Enum ⊆ registry keys。
- **与 `reason` 的分工**：`origin` = 结构化分类（reporter 分组用）；`reason` = 自由文本逐条备注（显示在"原因"列）。两者并存。

### 1.4 ActionRecord 字段变更

```python
@dataclass(frozen=True)
class ActionRecord:
    path: str
    behavior: Behavior       # ← 替代旧 action
    origin: Origin           # ← 新增
    src_size: int | None
    dst_size: int | None
    md5_match: bool | None
    confidence: str          # "high"/"medium"/"low"
    reason: str
    backup_target: str | None  # overwrite 时 "_conflict_backup/<path>"；驱动 COPY 内备份步骤
```

- 移除 `action` 字段；`to_dict`/`from_dict` 改为序列化 `behavior`+`origin`（str-Enum 原生序列化为字符串值）。
- **删除 `Action` 枚举**（无遗留消费者：reporter 改按 origin、Executor 尚未存在）。

## 2. Origin 词表与注册表初版

11 个已知 origin，按 behavior 分组（每个文件的来源身份）：

| origin | behavior | 大白话 | 举例 |
|---|---|---|---|
| `must_migrate` | COPY | 规则明文要迁的玩家核心数据 | `options.txt`、`saves/`、`xaero/`、`local/ftbchunks/` |
| `config_modified` | COPY | config 有 .bak 兄弟 = 玩家游戏内改过 | `config/create-client.toml` |
| `bak_file` | COPY | `.bak` 文件本身，跟随父 config 迁移 | `config/create-client-1.toml.bak` |
| `mod_added` | COPY | 源独有 mod（玩家额外加的） | `mods/[机械动力] create-*.jar` |
| `identical` | SKIP | 两边内容一致 | 两版 md5 相同的文件 |
| `never` | SKIP | 垃圾/版本二进制/缓存 | `logs/`、`<ver>.jar`、`**/cache/**` |
| `default_config` | SKIP | config 无 .bak 且不在白名单 = mod 默认值 | `config/MouseTweaks.cfg`（未改） |
| `rebuild` | SKIP | 版本/硬件绑定，必须让目标重建（高危） | `config/fml.toml`、`sodium-fingerprint.json` |
| `mod_shared` | SKIP | 两边都有的 mod | 迁 227→228 时两边都有的 jar |
| `mod_target_only` | SKIP | 目标独有 mod | 目标比源多出的 jar |
| `needs_review` | ASK | 非 config candidate / 孤儿 .bak，无可靠自动判定 | `kubejs/**`、`resourcepacks/*.zip` |

> `bak_file` origin **仅含迁移中的备份（COPY）**。.bak 的完整去向见 §3：父 config 迁 → `bak_file`(COPY)；父是 rebuild(跳) → 完整继承父 `(SKIP, rebuild)`；孤儿 → `needs_review`(ASK)。故每个 origin 恰好一种 behavior。
> 白名单命中的文件经规则层提升进 `to_migrate` → origin 归 `must_migrate`（暂不为白名单单立 origin；如需细化未来 `register_origin("player_pref")` 零成本补）。

### 2.1 new/modified 不拆进 origin

`copy_new`(新增) 与 `overwrite`(覆盖) 的区别**不**拆成两个 origin（否则 origin 数 ×2~3 爆炸）。由 `backup_target` 字段在 origin 分组内以**子计数/列**呈现，如"必迁 (82: 新增 77 · 覆盖 5 已备份)"。origin 只管"为什么"，new/modified 由数据字段表达。

### 2.2 ORIGIN_META（reporter 显示元数据，注册表播种）

```python
ORIGIN_META = {
    "must_migrate":    ("✅ 必迁",           True,  False),
    "config_modified": ("✏️ 改过的 config",  True,  False),
    "bak_file":        ("📋 备份文件",       True,  False),
    "mod_added":       ("📦 补 Mod",         True,  False),
    "needs_review":    ("❓ 待确认",         True,  False),
    "rebuild":         ("🔒 版本敏感",       False, False),
    "default_config":  ("⚙️ 默认配置",       False, False),
    "never":           ("⛔ 不迁",           False, False),
    "identical":       ("⏭ 一致",           False, False),
    "mod_shared":      ("📦 共有 Mod",       False, False),
    "mod_target_only": ("📦 目标独有 Mod",   False, False),
}
# (title, default_visible, show_backup_column)
```

## 3. `.bak` 跟随父 config

### 3.1 父解析算法（`resolve_bak_parent`，与 `has_bak_sibling` 对偶）

给定一个 `.bak` 文件路径，3 步找回父 config：

```
1. 必须以 .bak 结尾；否则不处理（返回 None）
2. 剥掉 ".bak" 后缀 → base
3. 拆 base 最后一个 "." → (stem, suffix)
4. 若 stem 末尾匹配 -[0-9]+（versioned）：剥掉 → versioned_parent = stem' + suffix
   - 优先查 versioned_parent ∈ src_paths
5. 否则（plain）：parent = base 本身
   - 查 base ∈ src_paths
6. 都不在 src → 孤儿（返回 None）
```

**与 `has_bak_sibling` 严格对偶**：`has_bak_sibling("create-client.toml")` 找 `create-client-[0-9]*.toml.bak`；本函数从 `create-client-1.toml.bak` 反推回 `create-client.toml`。一来一回无矛盾。

**真实数据全验证（18 个 .bak）**：`create-client-1.toml.bak`→`create-client.toml`、`ali_common.json.bak`→`ali_common.json`(plain)、`royalvariations-1.toml.bak`→`royalvariations.toml`、`logistics-network/client-1.toml.bak`→`client.toml` … 全部正确落到已存在的父。

正则：`re.match(r"^(.*)-[0-9]+$", stem)`（贪婪 .* 剥最后一个 -数字，单段，与 has_bak_sibling 的 `-[0-9]*` 一致）。

### 3.2 继承父命运（两趟处理）

`.bak` 不自己决策，**继承父 config 的最终命运**。关键反例：若 `fml.toml`（rebuild）带 .bak，父被 rebuild 强制跳过，.bak 是"不能迁的文件的备份"，迁去毫无意义 → 跟父跳。

实现（两趟，保证看到父的**最终**决策，含 rebuild 覆盖后）：
1. **Pass 1**：处理所有**非 .bak** candidate → 建 `{path: ActionRecord}` 决策表。
2. **Pass 2**：对 `config/` 下以 `.bak` 结尾的 candidate：
   - `parent = resolve_bak_parent(path, src_paths)`
   - 父在决策表且 `parent.behavior == COPY` → `ActionRecord(behavior=COPY, origin=bak_file, backup_target=按 .bak 自身 note：new→None / modified→_backup_target(path))`
   - 父在决策表且 `parent.behavior == SKIP`（父是 rebuild）→ **完整继承父**：`ActionRecord(behavior=SKIP, origin=rebuild, reason="follows rebuild parent")`
   - 父不在 src（孤儿）→ `ActionRecord(behavior=ASK, origin=needs_review, reason="orphan .bak, parent not in src")`

### 3.3 作用域与边角

| 情况 | 处理 |
|---|---|
| `config/` 下 .bak，父存在且迁 | COPY / origin=bak_file |
| `config/` 下 .bak，父是 rebuild（跳） | SKIP / origin=rebuild（完整继承父） |
| `config/` 下 .bak，父不在 src（孤儿） | ASK / origin=needs_review |
| `config/` 外的 .bak（kubejs/ 等） | **不走本逻辑**——当普通 candidate → ASK / needs_review（.bak 机制是 NeoForge config 专属，§3.4） |
| 父 config 本身被白名单提升 | .bak 仍跟随（父 behavior=COPY）→ bak_file |

### 3.4 为什么只对 `config/` 前缀

`.bak` 自动备份是 **NeoForge config 持久化系统**的特征（`planner-rules.md §3.2`）。kubejs/resourcepacks/shaderpacks 无此机制，那里的 .bak（罕见）不是本机制产生。作用域与现有 `has_bak_sibling` 完全一致，不扩大。

## 4. rebuild 规则层（高危版本敏感文件）

### 4.1 数据初版（`migration/data/rebuild.yaml`，6 条，证据见 §4.2）

```yaml
# 版本/硬件派生的高危文件：跨版本/跨机器迁移会崩溃或指纹错乱。
# 默认让目标版本自行重建。用户可用 .mcmig/rules.yaml 强制覆盖（P2：用户主权）。
# 优先级：cli > extra > user > REBUILD > whitelist > default
version: 1
rules:
  - match: "config/fml.toml"
    reason: "FML 加载器核心配置(maxThreads/earlyWindowSkipGLVersions，版本+硬件派生)"
  - match: "config/neoforge-client.toml"
    reason: "NeoForge 客户端渲染管线开关(加载器派生)"
  - match: "config/neoforge-common.toml"
    reason: "NeoForge 通用开发/日志开关(加载器派生)"
  - match: "config/sodium-fingerprint.json"
    reason: "Sodium 设备指纹(s/u/p 哈希+时间戳，跨机器/跨版本必失效)"
  - match: "config/iris-excluded.json"
    reason: "Iris 按 GPU 拉黑的光影清单(同机可迁跨机错；保守跳过，用户可覆盖)"
  - match: "config/sodium-mixins.properties"
    reason: "Sodium Mixin 激活集(版本绑定；保守跳过，用户可覆盖)"
```

### 4.2 证据（读真实内容判定，非按名猜）

| 文件 | 内容证实 | 判定 |
|---|---|---|
| `fml.toml` | `maxThreads=-1`(按 CPU)、`earlyWindowSkipGLVersions`(驱动)、`dependencyOverrides` | 版本+硬件 ✓ |
| `neoforge-client.toml` | `useCombinedDepthStencilAttachment`(渲染管线)、`experimentalForgeLightPipelineEnabled` | 加载器 ✓ |
| `neoforge-common.toml` | `logLegacyTagWarnings`、`attributeAdvancedTooltipDebugInfo` | 加载器 ✓ |
| `sodium-fingerprint.json` | `{"s":"<64hex>","u":"<128hex>","p":"<128hex>","t":<ts>}` | **设备指纹 ✓✓** |
| `iris-excluded.json` | 本例占位符空；语义=玩家按 GPU 拉黑的光影 | 边界，保守纳入 |
| `sodium-mixins.properties` | 本例仅注释空；语义=版本绑定的 Mixin 集 | 边界，保守纳入 |

**关键纠正（证据推翻 AGENTS.md 猜测）**：`config/sodium-options.json` 内容全是玩家画质/性能偏好（无设备数据）→ **不进 rebuild，进白名单**（§5）。`config/DistantHorizons.toml` 是玩家视觉偏好（含 `serverId`/`numberOfThreads`）→ **不进 rebuild**，进白名单（§5）。

### 4.3 表示：新增 `Category.REBUILD`

- `rules.py`：`Category` 枚举 +`REBUILD = "rebuild"`；`_DECIDE_MAP` 自动收录；rebuild 规则 `decide: rebuild` 合法。
- `differ.py`：桶路由加一支——
  ```python
  if cat == Category.NEVER:
      report.never.append(DiffItem(path, s, d, note="never"))
  elif cat == Category.REBUILD:
      report.never.append(DiffItem(path, s, d, note="rebuild"))  # 同桶，note 区分
  ```
- `planner.py` `_for_never`：按 `item.note` 定 origin——`"rebuild"`→origin=rebuild；`"never"`→origin=never；behavior 均 SKIP。
- 分类信息一路自然流到 origin，planner 无需回头重查规则。

### 4.4 优先级栈（P2：用户主权）

```
cli(--include/--exclude) > extra(--rule 文件) > user(.mcmig/rules.yaml) > REBUILD > whitelist > default
```

- **rebuild 压过 .bak**：架构降维——rebuild→Category.REBUILD→never 桶，文件**到不了 candidate**，.bak 逻辑碰不到。无需优先级之争。
- **rebuild 压过白名单**：rebuild 层在 whitelist 之上，first-match-wins 命中前者。
- **rebuild 让位于 user/cli**：玩家**显式**写 `rules.yaml: config/fml.toml → must_migrate` 或 `--include`，是主动担责（"我知道我在干嘛"），工具服从。契合 AGENTS.md「用户可用 rules.yaml 覆盖」+「保守默认 + 用户可控」。

### 4.5 加载器与作用域

- 新增 `rules.load_rebuild_rules_from_text(text, source_name)`（镜像 `load_whitelist_rules_from_text`，强制 `decide=REBUILD`、`source="rebuild"`）。PyInstaller 安全（importlib.resources 读文本）。
- **作用域：scan/diff/plan 常开**（rebuild 是基础分类，与命令无关；diff 也该正确显示版本敏感文件）。白名单维持 **plan-only**。
- `cli.build_ruleset` 改造：rebuild 层恒加载并插入正确位置；`with_whitelist` 维持 plan-only。

| 命令 | 层栈（高→低） |
|---|---|
| scan / diff | cli > extra > user > **REBUILD** > default |
| plan | cli > extra > user > **REBUILD** > whitelist > default |

## 5. 白名单扩充（`migration/data/whitelist.yaml`）

入选标准（缺一不可）：①玩家**客户端**偏好 ②直接写、**无 .bak** ③**非**服务器/admin 配置 ④**非**运行时可重建数据。

证据基线：227 的 18 个 .bak 中 **17 个 .toml + 1 个 .json(ali)** → .toml 基本有 .bak（.bak 判定法覆盖）；.json/.cfg/.ini/.snbt/.properties 基本无 .bak（白名单目标）。

### 5.1 增量条目

```yaml
# 高置信度（已读内容证实为玩家客户端偏好，无 .bak）
- match: "config/sodium-options.json"
  reason: "Sodium 画质/性能偏好(直接写 JSON 无 .bak；Q6 证据：无设备数据)"
- match: "config/jei/*.ini"
  reason: "JEI 客户端 UI/排序/书签/搜索偏好(.ini 直接写无 .bak；含原 sort-order/bookmarks)"
- match: "config/jei/blacklist.json"
  reason: "JEI 玩家隐藏物品清单(无 .bak)"
- match: "config/MouseTweaks.cfg"
  reason: "鼠标 inventory 行为偏好(.cfg 直接写无 .bak)"
# 中等置信度（命名/类推，实现时读内容复核）
- match: "config/ftb*-client.snbt"
  reason: "FTB 客户端偏好(library/ultimine，类同 ftbchunks-client；-client 后缀)"
- match: "config/xaero/**"
  reason: "Xaero 小地图显示偏好(非路径点数据；cfg/json/txt 直接写)"
- match: "config/DistantHorizons.toml"
  reason: "Distant Horizons 远景视觉偏好(DH 自带配置系统无 .bak；含 serverId 与 LOD 数据配套迁)"
# 保留
- iris.properties / config/jade/**/*.json / local/ftbchunks/**/ftbchunks-client.snbt
```

> 原 `config/jei/*sort-order*` + `bookmarks.ini` 被 `config/jei/*.ini` 收编，删旧条目简化。

### 5.2 关键纠正（证据避免错误入库）

**`config/ftbessentials.snbt` 不收**——读内容发现是**服务器/admin 配置**（开关 `/fly`、`/god`、`/rtp`、`/home`、`/tpa` 等管理命令），文件自述"modpack 作者改 defaultconfigs/ftbessentials-server.snbt"。迁它会覆盖新版本服务器设置。

### 5.3 附带 never 清理（`default_rules.yaml`）

真实 candidate 混入的**运行时可重建**产物（非玩家偏好、非版本绑定，属垃圾）：
```yaml
never:
  # ...原有...
  - config/jei/world/**           # JEI 查询历史（运行时）
  - config/ars_nouveau/search_index/**  # Lucene 索引（运行时）
```

## 6. PLAN_FORMAT v2（plan.json schema）

### 6.1 schema 变更

```
v1: actions:[{path, action:"copy_new", ...}]   summary 按 action 计数
v2: actions:[{path, behavior:"copy", origin:"must_migrate", ...}]   summary 按 origin 计数
```

### 6.2 迁移策略：拒绝 + 提示重跑（选项 a）

- `PLAN_FORMAT = 1 → 2`；`TOOL_VERSION = "0.2.0" → "0.3.0"`。
- 复用 `plan.py` 现成拒绝逻辑（`if fmt != PLAN_FORMAT: raise PlanFormatError("…请重新 plan")`）。v1 plan 被拒，提示重跑。
- **不写自动升级**：全仓无任何已存 `.plan.json`（用户只跑过 scan/diff），零迁移负担。pre-release 红利。
- `SNAPSHOT_FORMAT` **不动**（snapshot 在上游，存 FileEntry 不涉 action/origin）→ **scan/diff 零回归**，已 scan 的快熙照常可读。

## 7. reporter 改造

| 改动点 | 旧 | 新 |
|---|---|---|
| 元数据表 | `ACTION_META`（key=action） | `ORIGIN_META`（key=origin，见 §2.2） |
| 可见性 | `_visible_actions` | `_visible_origins`（同逻辑，key 平移） |
| 分组 | `r.action.value == key` | `r.origin.value == key` |
| summary 计数 | `plan.summary()` 按 action | 按 origin |
| `--category` 过滤 | 按 action 名 | **按 origin 名**（如 `--category rebuild`） |
| new/modified 区分 | 拆成 copy_new/overwrite 两行 | 同 origin 组内以 `backup_target` 推导子计数列"新增 X · 覆盖 Y 已备份" |

汇总行示例：`汇总: ✅必迁82, ✏️改过18, 📋备份18, 🔒版本敏感6, ⚙️默认配置150, ⛔不迁42, ❓待确认26`

## 8. 测试策略（TDD，自底向上）

2D 模型让断言**更清晰**：`a.behavior == Behavior.COPY` + `a.origin == Origin.MUST_MIGRATE`（两件独立事）替代旧 `a.action == Action.COPY_NEW`（揉死的一串）。

### 8.1 波及文件

| 文件 | 改动 | 量 |
|---|---|---|
| `test_plan.py` | 枚举值断言、`ActionRecord` 构造、summary 计数 | ~6 处机械改 |
| `test_planner.py` | 18 个 `a.action==X` → `behavior+origin` 双字段 | 18 处改 + 新增 |
| `test_reporter.py` | ACTION_META/分组 → ORIGIN_META | 重写断言 |
| `test_e2e.py` | plan JSON 的 action 字段 → behavior+origin | 改 JSON 断言 |
| `test_rules.py` | +Category.REBUILD、rebuild 加载器 | 新增 |
| `conftest.py` | 造文件树，不碰 action | **不动** |
| classifier/differ/scanner/hashing/snapshot 测试 | 不涉 action/origin（differ 仅加 REBUILD 路由用例） | ~0 |

### 8.2 新增测试

- **§3 .bak 跟随**：`.bak`→`origin=bak_file`+`behavior=COPY`；孤儿→`needs_review`；config 外→`needs_review`；高危父带 .bak→跟父 `SKIP`；父解析 versioned/plain 两形态。
- **§4 rebuild**：`fml.toml`→`origin=rebuild`+`SKIP`；压过白名单（同文件既命中白名单又命中 rebuild→rebuild 赢）；让位于 user rules（user 写 must_migrate→user 赢）；Differ 把 REBUILD 路由进 never 桶 note=rebuild。
- **§5 白名单/never**：新 globs 命中→`origin=must_migrate`（规则层提升）；`ftbessentials.snbt` 不被白名单收（仍 candidate）；never 清理项→`origin=never`。

### 8.3 TDD 顺序

1. `plan.py`：Behavior/Origin 枚举 + 注册表 + ActionRecord 字段 → 枚举/注册表/roundtrip 测试
2. `rules.py`：Category.REBUILD + `load_rebuild_rules_from_text` → test_rules 补
3. `differ.py`：REBUILD 路由 → test_differ 补
4. `planner.py`：产出 (behavior, origin) + .bak 两趟处理 → 改 test_planner + 新增
5. `reporter.py`：ORIGIN_META + 子计数 → 改 test_reporter
6. `cli.py` + 数据文件（rebuild.yaml 新增、whitelist.yaml 增量、default_rules never 清理）→ test_cli/e2e
7. 全量 `pytest -q`（预期 >111 通过）+ `ruff check .`

## 9. 项目结构 + 依赖

```
migration/
├── plan.py            # Behavior/Origin/注册表；ActionRecord(behavior+origin)；PLAN_FORMAT=2
├── planner.py         # resolve_bak_parent；两趟处理；按 note 定 origin
├── reporter.py        # ORIGIN_META；按 origin 分组/计数/子计数
├── rules.py           # +Category.REBUILD；+load_rebuild_rules_from_text
├── differ.py          # +REBUILD→never 桶(note=rebuild)
├── cli.py             # build_ruleset 插 rebuild 层（常开）
├── snapshot.py        # TOOL_VERSION="0.3.0"（SNAPSHOT_FORMAT 不动）
└── data/
    ├── rebuild.yaml        # 新增（6 条）
    ├── whitelist.yaml      # 扩充（+7 条，删 2 旧条目）
    └── default_rules.yaml  # never +2 类运行时清理
```

- **依赖不变**（rich/PyYAML/pathspec 标准库）。
- **版本**：`pyproject.toml` 0.2.0 → 0.3.0；`TOOL_VERSION` 同步。

## 10. 不在本 spec 范围（明确边界）

| 项 | 后续 |
|---|---|
| Executor 写盘（copy/覆盖/备份） | v1 Phase 2（消费本 spec 的 Behavior） |
| Manifest 决策沉淀 | v1 Phase 3 |
| Mod 感知（META-INF/neoforge.mods.toml → profiles/） | 后续（产生新 origin，靠注册表扩展） |
| 孤儿 mod 数据识别 | 需 Mod 感知 |
| 启动器活跃版本同步（PCL.ini） | v1 Phase 2/3 |
| 启动失败检测 / 自动回滚 | 需 Executor |
| NBT 解析（saves/dragon-survival） | 后续 |
| hash 缓存 | v0.1+ 增强 |

## 验收标准（本 spec 完成判定）

1. 真实 `mcmig plan 1.21.1-NeoForge_21.1.227 1.21.1-NeoForge_21.1.229` 产出：`.bak` 文件落在 `bak_file`（非 `default_config`）；6 个高危文件落在 `rebuild`；白名单新项落在 `must_migrate`；`sodium-options.json`/`DistantHorizons.toml` 等**不**在 rebuild。
2. plan.json 为 `plan_format: 2`，actions 含 `behavior`+`origin`，summary 按 origin 计数；旧 v1 plan 被 `PlanFormatError` 拒绝并提示重跑。
3. `mcmig scan`/`diff` **零回归**：`SNAPSHOT_FORMAT` 不变，已 scan 快熙可读；rebuild 层在 scan/diff 也生效（fml.toml 显示为版本敏感）。
4. reporter 按origin 分组，`--category rebuild`/`--category needs_review` 等按 origin 过滤生效；new/modified 以子计数呈现。
5. 对游戏目录**零写入**（plan 纯只读；e2e `test_e2e_plan_no_write_to_game_dir` 仍过）。
6. 全量单元 + e2e 通过；`ruff check .` 干净。
7. 优先级 P2 验证：user `rules.yaml` 写 `config/fml.toml → must_migrate` 时，fml.toml 落 `must_migrate`（用户压过 rebuild）；不写时落 `rebuild`。

## 决策摘要（给快速回顾）

> 行为/显示解耦成 2D：**Behavior**（COPY/SKIP/ASK，3 值，闭合，Executor 吃）× **Origin**（11 语义来源，闭合 str-Enum + 注册表，reporter 吃，随路线图靠 register_origin 扩展）。`.bak` 文件靠 `resolve_bak_parent` 找父、**继承父命运**（两趟处理）。高危文件新增 `Category.REBUILD` + `rebuild.yaml`（6 条，证据判定），优先级 **P2**（压白名单/.bak，让位 user/cli）。白名单扩 7 条（含 sodium-options/DistantHorizons 等证据纠正）。`PLAN_FORMAT` 1→2 拒绝重跑（SNAPSHOT 不动，scan/diff 零回归）。reporter `ORIGIN_META` 按 origin 分组 + new/modified 子计数。
