# 批次 D 设计:变体尾缀配对 / diff 换装通透 / .mcmig 锚定 game-root

- 日期: 2026-09-20
- 语料: 七轮服务端测试交付包(`Reference/observations/mcmigrator_服务端测试_20260920/`)
- 前置: v0.6.3(3dbf6c6,批次C:rebuilt 检出/文件名配对/语义比较扩展/never 规则)
- 版本目标: **0.7.0**(含行为变更:`.mcmig` 生成物锚定,见 T5)

## 0. 背景与核实(七轮语料,工具 0.6.3 实测)

| 发现 | 核实结论 | 证据 |
|----|----|----|
| 批次C 三特性实证 ✓ | mod_pairs 0→3 全配对(source=filename)、junction 降级提示正确、339 孤儿正常 | `diff_r7pre_r7post.json` mod_pairs ×3;演化段 never/orphan=339 |
| **F20-3 同版本尾缀变体未配对** ★ | `kaleidoscope_compat-2.9.7…jar` ↔ `…2.9.7…-Patch.jar`(同 modid/同版本/size 差 250B)分立 to_add/target_only,无 ⇄。根因:`normalize_jar_family`(moddb.py:504)把纯字母词 `patch` 归入家族键,两侧键差一截 | mods 桶两条目;`旧件指纹.md` md5/size 对拍 |
| **F19 复现** | 裸 `mcmig diff` 验收换装时 4 个旧 jar 标 to_add——differ 早有 `modpack_swap` 逻辑(differ.py:132-139)但 **diff 子命令无此开关**,仅 plan/migrate 流程生效 | 报告第 5 点;r7-post diff to_add ×4 |
| F8 复现 | 快照仍随 CWD 散落(`tools\mcmig_lab\.mcmig\` vs `MC_Server\.mcmig\`),同一 game-root 不同 CWD 即分裂 | scan_r7pre.txt 输出路径;二轮 F8 原始记录 |
| 观察项(不动代码) | 演化段唯一 candidate `config/infernalmobs.cfg`(+114B)疑似 mod 运行时回写,时间线无管理员改动 → candidate 归类正确 | 时间线.md;diff_r6post_r7pre.json |

七轮原始快照**未随包**(仅 diff JSON + 指纹表),回归夹具以指纹表合成 mods-only 快照对(见 §4)。

## 1. 目标 / 非目标

**目标:**
1. **D1(F20-3)**: 文件名归一识别「变体尾缀」;同家族+同版本签名+异尾缀 → 配对 `kind="rebuilt"`
2. **D2(F19)**: `mcmig diff` 增加 `--modpack-swap`,换装验收流旧 jar 归「换包排除」而非 to_add
3. **D3(F8)**: `.mcmig` 生成物(snapshots/plans)锚定 game-root,静态配置(config.yaml/rules.yaml)留工作区
4. **D5(文档)**: 0920 观察README、0919 F12 junction 伪影附注、README 中英双语更新

**非目标:**
- 快照 schema v2 内嵌 registry(彻底解 junction 复放孤儿/语义复核)→ 批次 E
- `--out-dir` 显式参数(拍板②选自动锚定,不加第二套机制)
- mod_pairs 驱动 planner 行为(批次B 显式妥协不变:配对纯标注,升级行为不变)
- CI symlink 分支(CI 未建)
- `Wing Kirin - V.3.2.0` 空格文件名归一(registry 已覆盖,文件名兜底不支持,记录不动)

## 2. 决策记录(2026-09-20 拍板)

| # | 拍板项 | 决定 | 理由 |
|---|----|----|----|
| ① | 同版异尾缀配对的 kind 命名 | **复用 `rebuilt`** | 与批次C「同名同版本异构建」概念统一,用户只记一个词;报告/理由文案现成 |
| ② | `.mcmig` 散落对策 | **自动锚定 game-root**(非 --out-dir) | 同一 game-root 不再随 CWD 分裂;兼容回退见 T5;配静态配置留 CWD 的两分法 |
| ③ | 批次范围 | D1+D2+D3+D5 | 收掉全部「每轮都在疼」的点;D4 大项独立批次 E |

## 3. 任务设计

### T1 归一化三元组(moddb.py)

`normalize_jar_family` 返回值从 `(family, sig)` 扩为 **`(family, sig, tail)`**:

- **尾缀定义**: 最后一个含数字词**之后**的连续纯字母词(如 `-Patch`/`-feature`/`-Patch-final`);
  无含数字词或其后再无字母词 → `tail=""`
- `family`/`sig` 计算规则**不变**(家族键仍含尾缀词——这是零回归的关键,见 T2 两级匹配)
- 附带修账本遗留:pair_mods_by_filename 内对同一文件三次调用归一化 → 每文件一次存结构

### T2 文件名配对两级匹配(pair_mods_by_filename)

- **第一级(现有逻辑不变)**: `family` 全等配对 → sig 异 → `upgrade`,sig 同 → `renamed`
- **第二级(新增,仅对第一级未配上的残余)**: 一侧 `family` 去掉尾缀词后(减尾键)与另一侧 `family` 全等
  且双方 sig 相同 且 尾缀不同(含一侧空) → `kind="rebuilt"`
- 歧义守卫沿用:同键任一侧多候选 → 整族放弃
- **零回归论证**: 第一级键完全不变;第二级只消费第一级剩不下的条目
  (现状它们根本不配对,任何新配对都是净增益;`x-1.21.1-neoforge` vs `x-neoforge-1.21.1`
  这类 token 顺序差由第一级继续接管)

### T3 registry 配对同判对齐(pair_mods)

现状: 同 modid + version 同 + jar 文件名异 → `renamed`。改为**同一套尾缀启发式**:

- 对两侧 jar 文件名算尾缀:尾缀不同(含一侧空) → `rebuilt`;相同 → `renamed`
- version 异 → `upgrade`(不变)
- **已知有意变更**: 客户端 9.19 语料 `-feature` 尾缀案例将由 renamed 翻转为 rebuilt
  (同版本不同构建,rebuilt 更准;且消除 junction 复放/活体两源对同一对 jar 判定不一致)
- ModInfo 无 size 字段,registry 侧不比内容——尾缀启发式即判据,与 T2 规则统一

### T4 reporter 展示

- `⇄rebuilt` 自动随 kind 显示;pair kind 为 rebuilt 时标记前同样加 `⚠ `
  (与同名桶 note=rebuilt 的警示语义一致)
- JSON `mod_pairs[].kind` 新值 `"rebuilt"` 为 additive,消费方(服务端脚本)兼容
- 汇总行 `配对: ⇄rebuilt ×N · ⇄upgrade ×N` 自动生效

### T5a diff --modpack-swap(cli.py + differ 接线)

- `p_diff` 增加 `--modpack-swap`(store_true),传 `Differ(..., modpack_swap=args.modpack_swap)`
- 语义与 plan/migrate 一脉:src 独有 mod 且非用户显式 must_migrate → never 桶 note=`modpack_swap`
- 开启时打印一行提示(仿 junction 提示风格):
  「换包模式: N 个源独有 mod 按旧包自带排除(never/换包排除,--show-never 可见)」

### T5b `.mcmig` 锚定 game-root(cli.py)

**两分法**: 静态配置跟工作区走,生成物跟实例走。

| 内容 | 归属 | 说明 |
|----|----|----|
| `config.yaml`、用户 `rules.yaml` | `CWD/.mcmig` | 引导配置,`_resolve_game_root` 的第 3 级回退依赖它(改了就鸡生蛋) |
| `snapshots/`、`plans/` | `game_root/.mcmig` | 数据产物,同 game-root 不同 CWD 不再分裂 |

- 写入: 一律 game-root 侧(scan/plan/migrate 的 mcmig_dir/plans_dir)
- 读取: game-root 侧优先,miss 时回退 `CWD/.mcmig`(旧布局兼容),回退命中打印一次性提示
  「检测到旧布局快照(CWD/.mcmig),建议整体迁移至 <game_root>/.mcmig」
- `_resolve_game_root` 解析链不变(--game-root > MCMIG_GAME_ROOT > CWD/.mcmig/config.yaml > 报错)
- 特例天然兼容: 服务端 lab `--game-root .` 时锚定目录 == CWD/.mcmig,零扰动

### T6 版本与文档

- pyproject 0.6.3 → **0.7.0**;TOOL_VERSION 同步
- README.zh-CN / README.en: mods 桶补 ⇄rebuilt 语义;diff --modpack-swap;.mcmig 目录布局两分法
- `Reference/observations/mcmigrator_服务端测试_20260920/README.md`(语料文档,§场景/构成/F20/演化谱系第 5 点)
- 0919 README 附注: 五/六轮「F12 活体 semantics」判定按 junction 伪影重新解读(两侧同体时
  语义复核被 0.6.3 短路降级为字节比较,旧判定不宜作 ground truth)

### T7 语料回归夹具

七轮快照未随包 → 以 `旧件指纹.md` 合成 **mods-only 快照对夹具**(`tests/fixtures/server_corpus/20260920/`):

- src: 4 旧 jar(kaleidoscopecookery-1.4.1 / beachparty-2.1.4 / wildernature-1.1.5 /
  `[森罗物语：兼容] kaleidoscope_compat-2.9.7…jar`,size 取指纹表真值,md5=null 符合分层快照)
- dst: 4 新 jar(含 `-Patch` 变体)+ 数个 shared 桩
- 断言: mod_pairs 恰 4 对 = 3×upgrade + 1×**rebuilt**(compat);compat 两条目桶内仍
  to_add/target_only(配对纯标注不变)

## 4. 测试策略

- T1: 归一化三元组单测(尾缀出现/不出现/多词尾缀/无数字词退化/嵌套标签前缀既有用例不回归)
- T2: 两级匹配单测(升级对/改名对/新 rebuilt 对/减尾键歧义放弃/第一级已配不再进第二级)
- T3: registry 同判单测(version 同+尾缀异→rebuilt;尾缀同→renamed;version 异→upgrade)
- T5a: CLI 端到端(换装夹具 + flag:旧 jar 落 never/modpack_swap、mods 桶无 to_add、提示行出现;
  不加 flag 行为与 0.6.3 完全一致)
- T5b: CLI 端到端(--game-root 异于 CWD 时快照落 game_root/.mcmig;旧布局回退读+提示;
  config.yaml 仍从 CWD 引导)
- T7: 合成夹具黄金断言
- 全量 pytest + ruff;data/*.yaml 无改动 → manifest.sha256 无需重跑

## 5. 既有测试预期影响(有意更新清单)

- CLI 测试中凡 game_root ≠ CWD 且断言快照/plan 落 `CWD/.mcmig` 的用例 → 路径期望改为
  game_root/.mcmig(逐条在实现时列出,类似批次C §5 例外清单)
- 客户端 9.19 若有断言 `-feature` 对 kind=renamed 的用例 → 翻转为 rebuilt(拍板③含 D1,
  T3 有意变更)
- 其余(387 项)零改动预期;实现以测试运行为证,偏差即回查设计

## 6. 验收标准

1. T7 合成夹具: mod_pairs 恰 4 对,compat 为 rebuilt,既有 3 对 upgrade 不变
2. 批次C 四组语料回归(20260914/14b/19 夹具锚定)全绿——证明两级匹配零回归
3. `mcmig diff A B --modpack-swap`: 换装夹具旧 jar 全部落 never/modpack_swap,无 to_add,
   提示行出现;不带 flag 与 0.6.3 输出逐字段一致
4. game-root 锚定: 异 CWD 运行快照落 game_root/.mcmig;旧布局可回退读并提示;
   `--game-root .` 场景输出路径与旧版一致
5. `pytest` / `ruff` 全绿;README 中英 + 0920/0919 观察文档就位;版本 0.7.0
6. doctor 完整性检查通过(manifest 未动)

## 7. 显式妥协(原型期)

- **尾缀启发式朴素**: `-Patch`/`-feature` 等纯字母尾缀规则对「真改名恰好落在尾缀位」的极端
  构造可能误判 rebuilt——影响仅限标注文案,不驱动行为(plan 不因配对改变),歧义守卫兜底
- **registry 不比内容**: ModInfo 无 size/md5,同版异名靠文件名启发;真内容级判定要等快照
  schema v2(批次E)
- **modpack_swap 的 diff 通透不产生 plan**: diff 是只读验收,不落 plan 文件;
  plan/migrate 流程的 swap 语义早已存在,本批不动
- **`.mcmig` 不做自动迁移**: 旧布局只回退读+提示,搬目录交用户(避免工具自作主张移动文件)
