# 批次F 设计规格：三机制收口（配对键格 / diff 管线下沉 / 快照身份）+ client_only 标注

- 日期：2026-09-29
- 输入：第十轮服务端语料（`mcmigrator_服务端测试_20260929.zip`，F30/F31）+ 用户「补丁统一化」诉求
- 前置：批次E（0.8.0，main=61e1941）已 live 大满贯（F31）
- 版本目标：**0.9.0**

## §0 验证表（探查结论 → 处置）

| 发现 | 定性 | 处置 |
|---|---|---|
| F31 批次E live 大满贯（12/12 配对 + renamed torchmaster 首秀；swap 稳定；junction 静默） | 正面验证 | 语料资产化为 r11 黄金对夹具（T1，命名沿 zip 内部会话标签 r11pre/r11post，同 20260921→r8_pre 先例），无代码修复。**更正**：报告把 DragonSurvival 归因「三级配对 live 首证」不实——两侧 `-all` 尾缀词同在，家族键全等，实为**一级**（mod_pairs.modid=`dragonsurvival-all` 佐证；三级 modid 应为减尾键 `dragonsurvival`）；三级唯一锚仍是 20260921 夹具（酒馆） |
| F30① frost_dragon（客户端件）混入服务端整包 → 专服构造期崩；diff 里裸 target_only 无提示 | 工具缺口 | client_only 标注 + stderr 警示（T5，仅标注不拦截） |
| F30② 崩溃残留 config（glacier_dragon-common.toml） | 已覆盖 | orphan 检测天然命中（mod 隔离→config 成孤儿）；r11 换装后 create_food_filling×2 + tarotcards 全部 live 命中实证，无需代码 |
| gc.log.0-4 残留（运维漏网） | 已覆盖 | `logs/**` 规则已收（diff JSON 实证 never 桶）；运维教训入观察 README |
| 「尾缀↔中段平台词互换」backlog 缺口 | **误判** | 实证（见 §3.1）四级已覆盖（家族键位置无关 + 四级平台剥词）；backlog 撤销，等价测试钉死 |
| 非平台装饰词增删形态（`foo-bar-1.2.3`→`foo-1.2.4`） | 真缺口（无真实语料） | 五级配对=装饰词闭集剥离键（T2，用户批预防性加） |
| `uv sync` 精确模式剪掉 pytest（r11 实踩） | 工程坑 | pyproject 增 `[project.optional-dependencies] dev`（T7） |
| froststalker_spawns.json「预期自愈」存疑（alexsmobs 已不在包内不会重建，但本就是 orphan，删除无害） | 运维备注 | 观察 README 记录，不动工具 |

## §1 目标 / 非目标

**目标**：把历轮累积的特判补丁统一为三个正常机制，并承接 F30①：

1. **A 配对声明化**：四级阶梯 → 数据驱动的键格（key lattice）；注册表配对与文件名配对共用身份归一/尾缀语义
2. **B diff 管线下沉**：`_cmd_diff` 编排下沉 `pipeline.run_diff`；配对/注记信息一等化，reporter 纯渲染；plan 命令顺带获得 ⇄ 注记
3. **C 快照身份事实**：schema 增可选字段 `resolved_root`；自比对检测从 scanned_at 代理升级为「快照文件身份主判 + 时间戳佐证」，且复放模式（无活体目录）同样可用
4. **F30①**：`data/client_mods.yaml` 已知客户端 mod 清单 + mods 桶 client_only 标注 + stderr 警示（仅标注，不自动排除）
5. **五级配对**：装饰词闭集剥离键（预防性，合成测试验证）
6. r11 黄金对夹具资产化；dev 依赖组；文档/版本收口

**非目标**：

- registry 内嵌快照（完整 schema v2 replay 保真）仍挂 backlog——无真实语料需求
- GUI 生成物锚定、`.mcmig` 根 hint helper 抽取、uninstall 措辞、modid 碰撞收口——批次F 沿挂
- 版本比较器（降级也标 upgrade 的语义维持批次E 决定）
- client_only 不做任何拦截/自动排除（工具是对比器不是部署器）

## §2 决策记录（用户四答，2026-09-29）

| # | 决策点 | 决定 |
|---|---|---|
| ① | 批次F 范围 | **甲：三机制收口**（A+B+C+F30①+语料+dev 组） |
| ② | plan 命令 ⇄ 配对注记 | **顺带加上**（plan 渲染有意变更；plan.json schema 不变，配对不持久化） |
| ③ | 五级配对 | **预防性加**（实证修正定义：非「尾中互换」——那已被四级覆盖——而是装饰词闭集剥离） |
| ④ | client_only 强度 | **仅标注+警示**（data 清单 + mods 桶标记 + stderr 一行） |

## §3 机制设计

### 3.1 A：配对键格（declarative key lattice）

**现状（补丁形态）**：`pair_mods_by_filename` 四段同构 30 行块（分组→求交→歧义守卫→定 kind→标记已配），136 行；每轮语料加一段。

**覆盖面实证（2026-09-29，当前实现直测）**：

| 形态 | 输入 | 现状结果 |
|---|---|---|
| 平台词尾缀↔中段（版本变） | `foo-1.2.3-neoforge` vs `foo-neoforge-1.2.4` | ✅ upgrade（一级：家族键位置无关） |
| 平台词尾缀↔中段（同版本） | `foo-1.2.3-neoforge` vs `foo-neoforge-1.2.3` | ✅ renamed |
| 平台词消失/新增（F22 形） | `foo-neoforge-1.2.3` vs `foo-1.2.4` | ✅ upgrade（四级） |
| 变体词换位（版本变） | `foo-1.2.3-patch` vs `foo-patch-1.2.4` | ✅ upgrade（一级） |
| **非平台装饰词增删** | `foo-bar-1.2.3` vs `foo-1.2.4`（及同版） | ❌ 未配对 → **五级收口** |

**键格设计**：阶梯改为表驱动。每级 = `(键函数, 前置条件, kind 规则)`，通用循环逐级消费**上级残余**（歧义守卫：任一侧多候选整族放弃）：

| 级 | 键函数 | 前置条件 | kind 规则 | 语义来源 |
|---|---|---|---|---|
| 1 | family（含尾缀词） | — | sig≠→upgrade，=→renamed | 0.6.3 |
| 2 | reduced（剥尾缀） | sig= 且 tail≠ | rebuilt | 批次D F20-3 |
| 3 | reduced | sig≠ | upgrade | 批次E F21 |
| 4 | platform 剥词 reduced | — | sig≠→upgrade，=→renamed | 批次E F22 |
| 5 | **decoration 剥词**（reduced 先剥平台词，再删闭集装饰词，任意位置） | — | sig≠→upgrade，=→renamed | 批次F（本批） |

**装饰词闭集**（枚举，语料出现再扩，与 `_PLATFORM_WORDS` 同哲学）：
`_DECORATION_WORDS = frozenset({"all", "patch", "fix", "feature", "release", "up", "port", "api", "lib", "compat"})`
——全部来自本整合包语料实见尾缀/装饰形态（DragonSurvival-`all`、compat-`Patch`、酒馆-`fix`/`-feature`、Re-Avaritia-`release`、alexscaves-`up` 等）。键剥后为空 → 跳过。

**等价与安全约束**：

- 1-4 级**行为零变化**：r1-r10 全部黄金对夹具断言原样全绿（键格等价性哨兵）
- 五级只消费上级残余 + 歧义守卫；配对是**纯标注**（不改分桶/plan 行为），误配上限=显示层一条错误信息
- 误配面分析：`create-goggles` 与 `create` 收敛需要 "goggles"∈闭集——闭集不含它，不收敛；闭集词（如 up）理论上可造伪对（`foo-up` 移除 + 全新 `foo` 出现），概率与危害（仅标注）可接受

**注册表配对（P2）**：`pair_mods` 的 rebuilt/renamed 判定与文件名路径**共用** `normalize_jar_family` 尾缀语义，kind 判定单点实现（registry 用真实 version，不硬套 sig 规则——共用的是「同版异名→尾缀异→rebuilt/同→renamed」这一条判定函数）。

### 3.2 B：diff 管线下沉 `pipeline.run_diff`

**现状（补丁形态）**：`cli._cmd_diff` 平铺编排（快照定位→ctx→孤儿规则→规则集→Differ→junction 门控→双源配对→swap 提示），与 `pipeline.build_plan` 平行重复；配对只在 diff 命令存在；F23/F26 的配对输入是 CLI 层手动集合推导；reporter 靠字符串拼接缝标记。

**设计**：新增 `pipeline.run_diff(...) -> DiffOutcome`（与 `build_plan` 平级，GUI 亦可直调）：

```
DiffOutcome:
  report: DiffReport          # 六桶
  mod_pairs: list[ModPair]    # 双源合并后
  src / dst: Snapshot
  notices: list[str]          # 预格式化 stderr 行（junction 门控提示、swap 排除提示、client_only 警示）
```

- 编排内聚：快照定位（find_snapshot 锚定+旧布局回退）→ Snapshot.load → resolve_diff_context → 孤儿规则 → build_ruleset → Differ（modpack_swap 透传）→ 配对（registry + filename 集合推导 + merge）→ client_only 匹配 → notices 装配
- 快照缺失/读取失败：照 build_plan 先例 `raise FileNotFoundError/ValueError`，CLI 捕获渲染退出码 2
- CLI `_cmd_diff` 瘦身为：参数展开 → run_diff → 异常处理 → reporter/render 或 JSON（stdout 纯净性不变：notices 全走 stderr）
- **P6/P8 顺带收口**：`src_only_mods` 统计改 `is_mod_jar` + 集合差（O(n²)→O(n)）；swap 计数单点在管线产出
- **reporter 一等化（P4）**：`DiffReporter` 以 `_annotations: dict[path, list[str]]`（有序：如 `["⇄upgrade"]`、`["client_only"]`）取代 `_display_note` 里 mods/never 双分支字符串拼接；`⚠ rebuilt` 前缀逻辑保留但走同一注记通道。**渲染输出字节级不变**（除 client_only 新标记）

### 3.3 C：快照身份事实 `resolved_root`

**现状（P5）**：F27 用 `scanned_at` 相等代理「疑似自比对」；本质想判定的是「同一物理目录的同一时刻」。

**设计**：

- `Snapshot` 增可选字段 `resolved_root: str | None`（scan 时 `ver_dir.resolve()` 落盘；旧快照缺字段 → `None`，加载不炸——向后兼容）
- **自比对检测统一**（单点函数，live 与复放两模式同源）：
  - 主判：两侧快照**文件路径相同**（同一文件自己比自己）→ stderr 提示（错误用法）
  - 佐证：`resolved_root` 均存在且相等（同物理目录，junction 已解析）+ `scanned_at` 相等（同刻）→ stderr 提示；`scanned_at` 不同 → log.debug（影子根标准用法，恒噪声）
  - 回退：`resolved_root` 缺失（旧快照）→ 维持现状（`ctx.same_dir` + scanned_at 比较），行为不变
- schema v2 只落这一个字段（最小步）；registry 内嵌仍挂 backlog

### 3.4 F30①：client_only 标注

- 新数据文件 `migration/data/client_mods.yaml`（纳入 manifest 与 package-data）：

```yaml
# 已知客户端 mod 清单(F30):专服部署构造期崩溃风险件,仅标注警示不拦截
client_only:
  - modid: glacier_dragon      # 活体 registry 命中键(mods.toml 的 modId)
    family: frost-dragon       # 快照文件名家族键(无活体目录时的复放匹配键)
    reason: r11 专服构造期加载 LocalPlayer 于 DEDICATED_SERVER 崩溃(F30 实证)
```

- 匹配：registry 可达时按 modid；复放模式按 `normalize_jar_family` 家族键。命中面=mods 桶任意条目（to_add/target_only/shared 均可标——shared 也提示双端风险）
- 呈现：note 装饰 ` client_only`（走 §3.2 注记通道）+ notices 一行汇总（`[警示] N 件已知客户端 mod(专服启动风险): <文件名列表截断>`）
- 无命中时输出零变化；清单可被用户 `rules.yaml` 无关——独立于规则层（它是风险注记不是迁移决策）

### 3.5 plan 命令 ⇄ 注记（决策②）

- `build_plan` 以与 `run_diff` 同源的配对逻辑产出 `mod_pairs`（ctx=活体目录两侧注册表可达时 registry+filename 双源，否则 filename 兜底）——管线内共用一个配对函数，不再两处编排
- `PlanReporter` 增 `mod_pairs` 参数：COPY/MOD_ADDED 行的路径命中 `src_files` → 显示 `⇄<kind>`；`⚠` 镜像 rebuilt 同 diff 语义
- **plan.json 不持久化配对**（派生显示数据，可重算）：文件 schema 零变化，executor/GUI 消费方无感
- 行为变更限于渲染注记；既有 plan 渲染测试锚定随改

## §4 语料资产化（T1）

`tests/fixtures/server_corpus/20260929/`：`r11_pre.json` / `r11_post.json`（mods-only 合成，同 20260920 前例）：

- 源侧 15 旧件 size/md5 取自《旧件指纹.md》真值；其余 shared 件与新侧 16 件 size 取自 `diff_r11pre_r11post.json`；md5 一律 null（配对不依赖）
- 断言（现状锚定，先于重构落地）：
  - 12 对全配：11 upgrade + 1 renamed（torchmaster，家族键同、sig 同）；DragonSurvival 为一级配对（见 §0 更正，`-all` 两侧同在）——三级唯一锚仍是 20260921 夹具（酒馆）
  - 3 件真移除裸 to_add（Create-Food-Filling / fzzy_config / tarotcards）+ 4 件净增 target_only（Azotosaurus / FTB chunks / FTB teams / frost_dragon）
  - swap 视角：15 旧件归换包排除（never+modpack_swap），`mod_pairs` 与 ⇄ 标记不丢（F23/F26 哨兵）
  - `配对: ⇄renamed ×1 · ⇄upgrade ×11` 汇总行
- 夹具 README 补 20260929 行 + 回填 backlog 记录

## §5 兼容性与验收标准

1. 全量 pytest 通过（426 基线 + 新增；T6 plan 渲染有意变更断言随改）；ruff check clean
2. **键格等价零回归**：六套既有黄金对夹具（0914/0919/0920/0921/0923/0929）配对结果与重构前完全一致；§3.1 覆盖面实证表入测试（形态 1/5/7 钉死「已覆盖」防未来退化）
3. r1-r10 十轮语料六桶数字全部不变（`.bak`/hs_err 等规则不动）
4. diff 普通视角渲染**字节级不变**（除 client_only 命中时新增标记与 stderr 警示行）；`--json` 结构不变（client_only 走 notices 不进 buckets）
5. 五级：装饰词形态合成配对（upgrade/renamed 两向）+ 歧义守卫 + 闭集外词不收敛（`create-goggles` 反例）
6. `resolved_root`：新快照含字段；旧快照加载不炸且回退行为与现状一致；自比对主判（同文件）在 live 与复放两模式均触发
7. client_only：r11 夹具 frost_dragon target_only 行标 `client_only` + stderr 一行；无清单命中时输出零变化
8. plan ⇄：r11 夹具 plan 的 waystones COPY 行标 `⇄upgrade`；plan.json 文件内容不含 mod_pairs
9. `mcmig -V` → 0.9.0（pyproject + `__init__` 两处同步）；`manifest.sha256` 与 data 一致（含 client_mods.yaml）；`uv pip install -e ".[dev]"` 可装出 pytest+ruff

## §6 任务切分（SDD 草案）

| 任务 | 内容 | 依赖 |
|---|---|---|
| T1 | r11 黄金对夹具 + 现状锚定断言（12 对/3删4增/swap/汇总行）+ 夹具 README | — |
| T2 | moddb 键格重构（表驱动五级 + kind 单点 + registry 语义共用）+ 等价/覆盖面/五级合成测试 | T1 |
| T3 | snapshot `resolved_root` 字段（往返/兼容）+ 自比对检测统一（文件身份主判）+ 门控迁移 | — |
| T4 | `pipeline.run_diff` 下沉 + reporter 注记一等化 + P6/P8 收口；CLI 瘦身 | T2, T3 |
| T5 | client_only：data 清单 + 匹配 + 注记通道 + notices + 夹具断言 | T4 |
| T6 | plan ⇄：build_plan 共用配对 + PlanRenderer 注记 + plan.json 不持久化验证 | T4 |
| T7 | pyproject dev 组 + README 双语（机制/清单/五级/坑）+ 0.9.0 两处 + manifest 重生成 + 观察 README | T2-T6 |

## §7 妥协与遗留

- 键格级序=严格强弱序贪心（先强后弱、逐级消费残余），不做全局最优匹配
- 装饰词/平台词均为枚举闭集，新词出现时扩表（语料驱动）
- plan.json 不含配对数据（显示层派生）；GUI 如需自行调管线取
- registry 内嵌快照、GUI 生成物锚定、hint helper、uninstall 措辞、modid 碰撞 → 批次G backlog 沿挂
- 五级无真实语料背书（用户批准预防性加）；若未来语料证伪，删除格条目即可（单行回退）
- client_only 清单非权威（社区可 PR 扩充，但工具不承诺完备）
- `--json` 不携带 client_only/notices（外部 JSON 消费方零影响，标注仅文本渲染与 stderr 可见）；后续有消费需求再加顶层字段
