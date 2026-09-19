# mods 桶内容感知与配对落地(批次 C:F17 rebuilt / 文件名配对 / F16 语义比较扩展 / F18 never 规则 / 新语料资产化)

- 日期: 2026-09-19
- 状态: 待评审
- 前置: 2026-09-13-diff-mods-awareness-design(批次 B,已交付 v0.6.2)
- 语料来源: Reference/observations/mcmigrator_服务端测试_20260914 / 20260914b / 20260919(F13-F19 报告,
  本次探索已核验:六组 diff 全量复放对账,报告桶数字与纯默认规则复放的差异全部归因于两个活体层效应)

## 0. 背景与核验结论(设计依据)

| 发现 | 核验裁定 | 依据 |
|---|---|---|
| F17 同名重建 jar 零检出 | 实锤 | 快照对 mods/*.jar 不存 md5(hashing 分层)+differ 同名只看存在性;enigmaticlegacyplus 同名重建(size 7233263→7233336)未进任何变化桶。**二次实例**:二轮语料(0912 夹具)ScorchedGuns-1.5.jar 同名异 size(18972536→19006006),当年同样漏检 |
| F4 配对现场从未生效 | 实锤,根因修正 | 四/六轮存档 JSON 的 mod_pairs 均为空;338 个 orphan 标注证明现场 ctx 活着且注册表非空 ⇒ 只能是 junction 同体致双侧注册表恒等。F14 报告"五对全配对"为采集 agent 人工配对 |
| 复放保真缺口 | 实锤(新发现) | orphan 层(338/333 个 config→never)与 F12 semantics(server.properties→identical)均需活体目录,复放时全部降级 ⇒ **报告桶数字不可从快照复现** |
| F18/F19议题3 崩溃残留 | 实锤 | 六轮 R2 存档 candidate 含 hs_err×2+replay×1;default_rules 无对应 glob |
| F16 json/toml 键序噪声 | 机制可信 | alltheleaks.json 398B→401B 自述语义未变(包内无副本,未逐字节验证);与 F12 同族 |

## 1. 目标与非目标

### 目标
1. **F17 rebuilt 检出**: mods 桶同名 jar 内容不同(比 size,md5 可得时也比)→ 新 note `rebuilt`,
   消灭"同名同版本号重新打包"的检出盲区。plan 层不自动迁移,出显式警告(用户拍板:方案 A)。
2. **F4 配对补 fallback(文件名家族配对)**: mods 桶内 to_add×target_only 按归一化文件名自动配对,
   纯快照数据可得——同时覆盖服务端 junction 场景(注册表恒等)与跨机复放场景(无活体目录)。
   语义决策:结果合并进现有 `mod_pairs`,条目加 `source` 字段标注来源(用户拍板:方案 A)。
3. **F16 语义比较扩展**: textcompare 增加 json/toml 键序无关比较,Differ 接线同 F12
   (md5 异 + 双侧可读 → identical/semantics)。
4. **F18 never 规则**: default_rules 补 `hs_err_pid*.log`、`replay_pid*.log`。
5. **F19议题1 显示提示**: mods 桶存在 to_add 时报表脚注说明裁剪语义。
6. **新语料资产化**: 三包语料入 Reference/observations(本地);六份快照脱敏入回归夹具,
   按**复放语义**锚定(含 F17/F14/F19/F15 四组黄金对)。

### 非目标(显式排除,防蔓延)
- **不改任何 mods 迁移行为**: planner 对 to_add 的 COPY、对 shared/target_only 的 SKIP 全部不变;
  rebuilt 映射为 SKIP(保守,不自动覆盖)。
- **不动快照 schema**: registry 内嵌(snapshot_format v2)记 backlog,等真实需求。
- 孤儿规则与 F12 properties 语义比较的复放降级**保持现状**(活体目录限定,文档化);
  本批只让"配对"摆脱活体依赖。
- snbt 语义比较不做(无解析器、无语料实锤);`--shrink` 意图参数不做(脚注先行);
  level.dat mod 集三方对齐、演化量谱系文档不做。
- 不动 GUI、不动 swap/migrate 管线。

## 2. 总体架构: 三处接线,零 schema 变更

```
_cmd_diff
  ├─ Snapshot.load ×2                                  # 不变
  ├─ pipeline.resolve_diff_context(src,dst)            # ★微调: 新增 same_dir 检测(resolve 后同路径)
  │     same_dir=True → 孤儿规则照常(dst=现役=判定基准),仅注册表配对作废
  ├─ build_ruleset(... orphan_rules=照旧)               # 不变(F18 只改 default_rules 内容)
  ├─ Differ(...).diff()                                # ★T1: mods 同名比 size/md5 → rebuilt
  │                                                    # ★T5: 语义比较扩展 .json/.toml
  ├─ moddb.pair_mods_by_filename(src_paths, dst_paths) # ★T2: 文件名家族配对(纯快照)
  ├─ moddb.pair_mods(reg…)                             # 仅 ctx 存在且 !same_dir 时
  ├─ merge_pairs(filename_pairs, registry_pairs)       # ★T2: 合并,registry 优先,source 标注
  └─ DiffReporter(... mod_pairs=merged)                # ★T6: to_add 脚注;rebuilt 显示
```

## 3. 分任务方案

### T1 F17:rebuilt note(differ.py + planner.py + reporter.py)

判定逻辑(`_mod_item`,同名双侧存在时):
```python
if s.size != d.size or (s.md5 and d.md5 and s.md5 != d.md5):
    note = "rebuilt"      # 同名同版本号、内容不同(重新打包)
else:
    note = "shared"
```
- size 是 tiered 快照的必存量,覆盖绝大多数重建(实测 ±73B);strict 快照双侧有 md5 时加验。
- planner `_for_mod` 映射表加显式条目 `"rebuilt": (SKIP, MOD_SHARED)`,reason 写明
  「同名同版本异构建(rebuilt),默认保留目标侧,如需采用源侧构建请手工处理」——reason 即警告位,
  不新增 warning 机制。
- reporter:`rebuilt` 原样进 JSON note(additive 新值);rich 显示加 ⚠ 前缀。

### T2 文件名家族配对(moddb.py 新增 + pipeline/cli 接线)

**归一化算法**(jar 文件名 → 家族键):
1. 取 basename,剥扩展名,剥 `[...]` 中文标签前缀(如 `[传送石碑／指路石]`);
2. 小写,按 `-` `_` `+` 切词;
3. **家族键** = 仅含字母的词按序拼接(含数字的词全丢弃);
   **版本签名** = 含数字的词按序拼接。
4. 配对域 = mods 桶 to_add × target_only 的同家族键交叉;**同侧同家族多个候选时不配**
   (歧义放弃,不猜);多对一同样放弃。
5. kind 判定: 版本签名不同 → `upgrade`;相同(仅前缀/格式差异)→ `renamed`。

语料验证(黄金样例全过):
| 侧 | 文件 | 家族键 | 结果 |
|---|---|---|---|
| src/dst | waystones-…-21.1.44 / .45 | waystones-neoforge | upgrade |
| src/dst | cobblestone_generator-1.2.0-mc1.21.1-neoforge / `[圆石生成器]`…-1.2.1-… | cobblestone-generator-neoforge | upgrade(前缀差异被剥) |
| src/dst | infernalmobs 同版本异名(0912 改名对) | infernalmobs | renamed |
| src | raritycore-1211.14.7(无对侧) | raritycore | 不配 ✓ |
| src | ftb-chunks / tinydragons(被删 mod) | — | 不配 ✓ |
| dst | field-emitters / mekmm(新增 mod) | — | 不配 ✓ |

**与注册表配对合并**:
- `ModPair` 增加 `source: str`("registry"|"filename"),`to_dict()` additive 输出;
  filename 配对的 `modid` 字段填家族键(消费者凭 source 可知其非真实 modid)。
- 合并规则: registry 对(仅 ctx 存在且 `not same_dir` 时计算)优先,其覆盖的文件不再参与
  filename 配对;两源结果按文件覆盖去重(registry 对覆盖的文件不再保留 filename 对)
  后并入同一 `mod_pairs` 列表。
- `DiffContext` 增加 `same_dir: bool`(两侧版本目录 `resolve()` 相同 → True);
  same_dir 时注册表配对与语义复核跳过并打印一行提示(「两侧版本目录指向同一路径(junction),
  注册表配对与语义复核不可用,已使用文件名配对/字节比较」);**孤儿规则不受 same_dir 影响**
  (dst=现役状态恰是孤儿判定基准,五/六轮 338 例全对实证)。

### T3 新语料资产化(夹具 + observations)

- 三包 zip 解包入 `Reference/observations/`(GBK 文件名解包;gitignore 已覆盖,不入库)。
- 六份快照脱敏(game_root → `C:\fixture\sanitized`,同既有夹具;文件路径本为版本内相对路径,
  无其它泄漏字段)入 `tests/fixtures/server_corpus/{20260914,20260914b,20260919}/`,
  命名 snake_case(r4_pre.json 等)。
- **锚定值按复放语义(ctx=None)实测**,测试 docstring 注明与报告数字的差异缘由
  (orphan 层与 semantics 为活体目录限定行为)。已实测的复放基线(实现时以最终行为复核):
  - 四轮R1(r3_post→r4_pre): 16/1/112/0/733/40;T2 后另锚 mod_pairs=0(空转段无 mod 变化)
  - 四轮R2(r4_pre→r4_post): 12/1/117/0/737/41;**mod_pairs=5×upgrade**(F14 黄金对)
  - 五轮R2(r5_pre→r5_post): 0/0/112/3/750/44;**rebuilt=1**(enigmaticlegacyplus,F17 黄金对)
  - 六轮R2(r6_pre→r6_post): 11/19→(T4 后 16)/120/8/1126/52→(T4 后 55);
    **mod_pairs=6×upgrade**(含 cobblestone 前缀对)+ hs_err/replay 3 件入 never
- F15 空转对(四轮R1)锚定 to_migrate 全为 world 心跳数据(语义正确性),不锚具体清单。

### T4 F18:default_rules never 桶补两条

`hs_err_pid*.log`、`replay_pid*.log`(JVM 崩溃产物,根目录散落,无迁移价值)。语料实测落
candidate(删除侧)/only_in_dst(新增侧),补规则后两侧均入 never。

### T5 F16:textcompare 扩展 + Differ 接线

- `json_semantic_equal(a,b)`: 双侧 `json.loads`(UTF-8,BOM 容忍);任一解析失败 → 不等
  (退回 modified);解析后深度相等(Python `==`:dict 键序无关,list 序敏感——正确)。
- `toml_semantic_equal(a,b)`: `tomllib.loads` 同构比较;表序/注释/空白差异消解。
- Differ 语义复核 dispatch 由 `*.properties` 扩为 `.properties/.json/.toml` 三格式,
  触发条件不变(md5 异 + content_reader 可读双侧)。identical(note=semantics)语义复用。
- plan 管线不传 content_reader,字节比较不变(批次 B 既定)。

### T6 F19议题1:reporter 脚注

rich 渲染:mods 桶可见且含 to_add 时输出一行
「to_add=源独有(迁移语义:目标缺→补);若为有意删除/裁剪的 mod 请忽略对应行」。
JSON 不加键(纯显示)。

## 4. 关键决策记录

| 决策 | 理由 |
|---|---|
| rebuilt → SKIP+警告,不自动覆盖(用户拍板 A) | 同名异构建可能是有意锁定(整合包回退);工具负责"看见并说清楚",覆盖决策留给人 |
| 文件名配对并入 mod_pairs + source 字段(用户拍板 A) | 单一出口;结构对现有消费者 additive;registry 与 filename 是同一信息的两个来源 |
| 同家族歧义(同侧多候选)不配 | 配对是提示性信息,猜错比不配有害;语料中未出现 |
| same_dir 只废配对不废孤儿 | 五/六轮 338 例 orphan 全对——dst=现役恰为孤儿判定基准;配对需要的是双侧**历史**状态,恰是 junction 给不出的 |
| 锚定值用复放语义而非报告数字 | 报告数字含活体层效应(orphan/semantics),快照复现不了;夹具在复放环境跑,锚定必须自洽 |
| T1 用 size 而非全量 md5 mods jar | 快照已存 size,零扫描成本;重建几乎必变 size(实测 ±73B);同尺寸异构建属极端,tiered 下明确为盲区(strict 快照 md5 补) |
| json 语义比较容 BOM、toml 用 tomllib | 与 F12 properties 的容错口径一致;tomllib 是 3.11+ 标准库,零依赖 |

## 5. 测试策略

1. **单测(moddb)**: 归一化矩阵(上表全样例+大小写/下划线/`+` 后缀);歧义不配;版本签名
   upgrade/renamed 判定;merge_pairs 的 registry 优先去重;ModPair.source 输出。
2. **单测(differ/planner)**: rebuilt 判定三分支(size 异/md5 异/全同);planner rebuilt →
   SKIP 且 reason 含 rebuilt;现有 `_MOD_NOTE_MAP` 测试零改动。
3. **单测(pipeline)**: same_dir 判定(同目录双路径/不同目录);same_dir 时孤儿规则仍生成。
4. **单测(textcompare)**: json 键序重排/空白差异 → 等;值差异 → 不等;畸形 json → 不等;
   toml 表序+注释差异 → 等;畸形 toml → 不等;Differ 集成(假 content_reader)三格式。
5. **夹具回归(新增 4 测试)**: 上节锚定表;现有 7 项语料回归**零改动全过**
   (0908/0913 无同名异 size jar、无 hs_err——实现时以测试运行为证)。**两处已知例外**(自审已核实):
   - 0912 夹具 ScorchedGuns-1.5.jar 在 T1 后 note shared→rebuilt——无测试断言其 note,
     mods 桶计数不变,预期零改动通过;新夹具测试中加一条二轮 rebuilt 断言(第二次实例)。
   - `test_diff_degraded_when_game_root_unreachable`(test_cli.py)断言 `mod_pairs==[]`
     与 stderr「mods 扫描不可用」——T2 后 ctx=None 仍产文件名配对,**该测试需一次有意更新**
     (断言 mod_pairs 含 filename 来源配对;提示文案改为「孤儿标注与注册表配对不可用,
     文件名配对仍可用」)。
   - `test_non_properties_never_triggers_reader`(test_differ.py,F12 引入)夹具用了
     `config/x.toml` 且两份字节恰好语义相等(仅注释差异)——T5 落地后正确落 identical/semantics,
     旧断言 candidate/modified 必败。**一次有意更新**:路径改 `config/x.txt`(dispatch 表外后缀,
     保留"表外后缀永不触发 reader"的原意图),重命名 test_undispatched_suffix_never_triggers_reader。
     (2026-09-20 实现期裁定:spec T5 三格式为准,旧测试夹具选择不当)
6. **e2e**: mini 版本目录 ctx 活体路径:registry+filename 双源合并;same_dir(同目录挂双名)
   → 仅 filename 配对 + 提示行。
7. **reporter**: rebuilt ⚠ 显示;to_add 脚注出现/不出现两态;JSON note=rebuilt additive。

## 6. 验收标准

1. 既有全部测试通过,改动仅限自审列出的两处已知例外(0912 rebuilt 断言为新增,
   降级测试为一次有意更新,其余零改动)。
2. 四个新夹具锚定测试通过(复放语义值,含 mod_pairs/rebuilt/never 断言)。
3. F17 黄金对(两例): r5-pre→r5-post 复放 enigmaticlegacyplus note=rebuilt 且 plan 该条
   SKIP+警告;0912 复放 ScorchedGuns note=rebuilt。
4. F14 黄金对: r4-pre→r4-post 复放,mod_pairs 恰 5×upgrade、source=filename。
5. F19 对: r6-pre→r6-post 复放,mod_pairs 恰 6×upgrade(cobblestone 前缀对在内),
   hs_err×2+replay×1 落 never。
6. 构造"仅键序差异"的 json/toml 双文件 → identical/semantics;真实值差异 → modified。
7. `pytest`/`ruff` 全绿;README 补 rebuilt 语义与配对来源说明一小节。

## 7. 显式妥协(原型期)

- **同尺寸异构建盲区**: tiered 快照下同名同 size 不同 md5 的重建检不出(md5=null 无从比);
  strict 快照可检出。语料未出现,记录不处理。
- **版本签名判定朴素**: 仅日期 token 变化的重建(DragonSurvival 类文件名)会判成 renamed;
  家族键碰撞(两个不同 mod 归一后同键)依赖"歧义不配"兜底。语料均未出现。
- **F16 断言未逐字节验证**: alltheleaks"语义未变"以自述为准;测试用自构造样例,不依赖该件。
- **孤儿/semantics 复放降级保持现状**: 夹具锚定已绕开;快照内嵌 registry(彻底解法)在 backlog。
- **配对不驱动行为**: mod_pairs 仍是纯标注,planner 不因配对改变 COPY/SKIP(升级对旧件
  不回迁的既有语义不变,那是显式妥协过的批次 B 决策)。
