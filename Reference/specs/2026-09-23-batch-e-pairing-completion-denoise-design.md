# 批次 E 设计:配对完整性(三级/四级) / swap 视角标记 / 降噪三件

- 日期: 2026-09-23
- 语料: 八轮(`Reference/observations/mcmigrator_服务端测试_20260921/`,F21-F24)
  + 九轮(`Reference/observations/mcmigrator_服务端测试_20260923/`,F25-F28)
- 前置: v0.7.0(1d79465,批次D:尾缀两级配对/diff 换装通透/.mcmig 锚定)
- 版本目标: **0.8.0**(新增配对能力 + swap 视角行为变更,minor)

## 0. 背景与核实(八/九轮语料,工具 0.7.0 实测)

| 发现 | 核实结论 | 证据 |
|----|----|----|
| 批次D 四特性实证 ✓ | rebuilt 检出(灾变 3.33 同名重建)、纯升级配对 5/5(版本中置/尾缀复合/CJK 前缀对称+非对称)、`--modpack-swap` to_add 通透、35h 演化段干净 | r8/r9 报告「正面实证」;`diff_r9pre_r9post.txt` |
| **F21 版本号+尾缀同变未配对** ★ | `kaleidoscope_world_liquor-1.1.8-…-feature` → `1.1.9-…-fix` 分立 to_add/target_only。根因:一级要求全家族键相等,但**家族键含尾缀词**(批次D 零回归设计),`…-feature` ≠ `…-fix` 失配;二级(减尾键)又要求 sig 相等,`1.1.8-1.21.1` ≠ `1.1.9-1.21.1` 也失配——两级中间漏了一格 | `diff_r8pre_r8post.txt` 46-49 行;`normalize_jar_family`(moddb.py:504) |
| **F22 命名风格全变未配对** ★ | `field-emitters-neoforge-1.21.1-1.1.0` → `field-emitters-1.2.1` 分立。根因:`neoforge` 平台装饰词混入家族键(旧=`field-emitters-neoforge`,新=`field-emitters`),上游 modid 未变只是改了命名风格 | 同上 102-103 行 |
| **F23/F26 swap 视角配对标记丢失** ★ | 普通视角 `⇄upgrade` + 汇总行齐备;`--modpack-swap` 后新件退化为裸 `target_only`、汇总行消失(r8 1 对、r9 5/5 干净复现)。根因:源独有 mod 被 Differ 移入 never 桶,**配对输入取自 `report.mods` 的 to_add 列表**(cli.py:363-366)随之扑空 | `diff_r9pre_r9post_swap.txt`(厨房/资源复制机/alltheleaks/irons/mekltgt 全裸 target_only) |
| **F24 autocrlf 行尾伪差** | `core.autocrlf=true` 贡献机检出 CRLF 后 `doctor verify_data_manifest` 报 4 件 yaml「损坏」;manifest 哈希 == git blob(LF) ≠ 工作区(CRLF)。数据本体完好 | r8 报告 F24 字节级三对照;本仓库 autocrlf=false 未复现 |
| **F27 junction 降级提示恒触发** | 九轮三组 diff 全弹「两侧版本目录指向同一路径…」。精化:提示按**实时路径**判定,而 diff 消费的是**时间点冻结的快照**——九轮标准的「同目录前后两刻双快照」用法下,提示恒属噪声(行为本身——注册表跳过/字节比较——是对的) | `diff_r9pre_r9post_swap.txt` 第 1 行;cli.py:362 |
| **F28 .bak 世代累积** | lootr 每次升级首启把旧配置存为 `-N.toml.bak`,N 单调递增永不清理;`.125→.126` 已产生第 2 世代,代代进 diff 噪声(modified/only_in_dst)。当前规则体系完全没有 `.bak` 的处理 | r9 报告 F28;`diff_r8post_r9pre.json` |
| F25 NTFS 隧道 CreationTime 失真 | 轮转日志同名重建继承旧元数据,启动验证脚本判新会话的坑——**纯运维侧**,不动工具,记录于观察 README | r9 报告 F25 |

八/九轮原始快照未随包(仅 diff JSON + 报告表格),回归夹具按报告明细合成 mods-only 快照对(§4)。

## 1. 目标 / 非目标

**目标:**
1. **E1(F21)**: 文件名配对第三级——减尾键相等 + 版本签名不同 → `kind="upgrade"`
2. **E2(F22)**: 文件名配对第四级——平台装饰词剥离后相等 → sig 异 `upgrade` / sig 同 `renamed`
3. **E3(F23/F26)**: 配对输入改从快照集合直接推导,`--modpack-swap` 下 `⇄upgrade` 标记与配对汇总行保留
4. **E4(F24)**: autocrlf 三重修——`.gitattributes` 锁 LF + gen_manifest 归一化哈希 + doctor 归一化校验
5. **E5(F27)**: junction 降级提示 `scanned_at` 门控——双快照不同刻静默(降 debug),同刻保留提示
6. **E6(F28)**: `**/*.bak` 归入默认 never 规则 + 重跑 gen_manifest
7. **文档**: 八/九轮观察 README(本地)、README 中英双语、版本 0.8.0

**非目标:**
- 快照 schema v2 内嵌 registry(重放保真彻底解)→ 批次 F
- registry 配对分支调整:F21/F22 在**真实双目录**(非 junction)下 registry 配对按 modid 本就能配上;
  三/四级是文件名兜底路径的完整性补全,junction 复放/缺 jar 场景才依赖
- modid 碰撞收口(基座+变体同目录共存边角)、GUI 生成物锚定、hint helper 抽取、uninstall 措辞 → 挂 backlog
- `--loose-pair` 逃生门(r8 报告备选):拍板走结构化三/四级,不加开关
- planner 行为:配对仍纯标注,不驱动迁移计划(批次B 妥协不变)

## 2. 决策记录(2026-09-23 拍板;AskUserQuestion 未应答,按惯例取保守默认=推荐项)

| # | 拍板项 | 决定 | 理由 |
|---|----|----|----|
| ① | 批次范围 | E1-E6 全做 | 两轮新发现一次闭合;规模与批次D 相当 |
| ② | F21 配对 kind | **复用 `upgrade`** | 版本号确实升了,语义准确;尾缀差异看文件名即知,不新增报告词汇 |
| ③ | F27 提示策略 | **`scanned_at` 门控** | 不同刻=标准影子根双快照用法,静默为 debug;同刻=疑似自比对错误,保留提示 |
| ④ | F28 规则范围 | **`**/*.bak` 全域** | 报告原建议;`.bak` 是「玩家改过」的标记物,本体无迁移价值,全域归 never 不影响判定法(判定法看存在性,白名单管无 .bak 的玩家偏好) |

## 3. 任务设计

### T1 三级配对:版本+尾缀同变(moddb.py)

`pair_mods_by_filename` 在二级之后新增第三级,**仅消费一/二级未配上的残余**:

- 键: `_reduced_family(family, tail)`(与二级同键)
- 条件: 减尾键相等 + `s_sig != d_sig` → `kind="upgrade"`(modid=减尾键)
- 论证: 残余中若尾缀相同且 sig 异,全家族键早已相等、轮不到三级;故三级隐含「尾缀亦变」,
  升级语义按拍板②覆盖之(尾缀差看文件名即知)
- 歧义守卫沿用:同键任一侧多候选 → 整族放弃

### T2 四级配对:平台装饰词剥离(moddb.py)

新增模块级常量 + 辅助函数,再挂第四级(仅消费一/二/三级残余):

- `_PLATFORM_WORDS = frozenset({"neoforge", "forge", "fabric", "quilt", "mc", "minecraft"})`
- `_platform_stripped(family, tail)`:先取减尾键,再剥键内平台词;剥后为空 → 返回空串(调用方跳过)
- 条件: 剥离键相等 → sig 异 `upgrade` / sig 同 `renamed`(modid=剥离键)
  - F22 例: 旧减尾键 `field-emitters-neoforge` → 剥离 `field-emitters`;新减尾键 `field-emitters` → 相等,sig `1.21.1-1.1.0`≠`1.2.1` → upgrade
- 危险面评估: 剥离只作用于残余条目,且歧义守卫照旧;两件真不同的 mod 仅在「剥平台词后同名」且
  各自独占两侧时才会误配——实测语料无此形态,风险可控
- 三/四级 modid 均不含平台词时与一/二级共存于同一 mod_pairs 列表,`merge_mod_pairs` 不变

### T3 swap 视角配对通透(cli.py)

- `_cmd_diff` 的文件名配对输入从 `report.mods` 的 to_add/target_only 改为**快照集合直接推导**:
  `src_only = mods 路径 ∈ src.files 且 ∉ dst.files`(对偶 dst_only),判定与 `Differ._is_mod`
  同源(differ.py 公开 `is_mod_jar`,`_is_mod` 委托之,消除双处判定漂移风险)
- 效果: `--modpack-swap` 只改 Differ 分桶,不再影响配对输入 → 新件 target_only 保留 `⇄upgrade`,
  配对汇总行回归;源件在 never 桶(note=modpack_swap)若渲染亦带 ⇄ 标记(reporter 既有 path→kind 映射)
- registry 配对分支不动(本就从活体注册表取,与 swap 无关)

### T4 junction 提示 scanned_at 门控(cli.py)

cli.py:362 的 same_dir 提示改为:

```python
elif ctx is not None and ctx.same_dir:
    if src.scanned_at == dst.scanned_at:
        _print_err("[提示] 两侧快照同刻且版本目录同路径(junction 同体):…")
    else:
        log.debug("junction 同体双快照(不同刻,标准影子根用法),已用文件名配对/字节比较")
```

- 同刻:两份快照同一秒拍同一目录,疑似「拿自己比自己」→ 保留 stderr 提示
- 不同刻:标准「同目录前后两刻」双快照用法 → 降 debug,九轮起不再刷屏
- 同目录 read_file 短路(语义复核禁用)行为**不变**——那是正确行为,只治提示噪声

### T5 autocrlf 三重修(.gitattributes + tools/gen_manifest.py + doctor.py)

1. 新增 `.gitattributes`:
   ```
   migration/data/*.yaml text eol=lf
   migration/data/manifest.sha256 text eol=lf
   ```
   (清单按 LF 字节哈希,锁检出行尾;现存 blob 本 LF,新克隆即正确,存量工作区由 2/3 兜底)
2. `gen_manifest.sha256_file`: 哈希前 `data.replace(b"\r\n", b"\n")` 归一化
3. `doctor._sha256_of`: 同样归一化后比对
- 收敛性质: LF 文件归一化为 no-op → **现有 manifest 数值不变,无需重生成**(E6 改 yaml 时才重跑);
  CRLF 检出件与 LF 提交字节在两层同时等价,F24 闭环

### T6 .bak never 规则(migration/data/default_rules.yaml + 重跑 gen_manifest)

never 列表新增:

```yaml
  - "**/*.bak"                       # NeoForge/mod 配置备份(-N 后缀世代累积不清理;F28)
                                     # .bak 是「玩家改过」标记物,本体无迁移价值;判定法看存在性不受影响
```

随后 `.venv/Scripts/python tools/gen_manifest.py` 重生成 manifest(仅 default_rules.yaml 行变化)。

### T7 语料回归:八/九轮黄金对(tests/fixtures/server_corpus/ + test_corpus_regression.py)

- `20260921/`(r8 换装段): src={酒馆 1.1.8-feature、field-emitters-neoforge-1.21.1-1.1.0、
  lootr .125、灾变 3.33 旧 md5} dst={酒馆 1.1.9-fix、field-emitters-1.2.1、lootr .126、灾变 3.33 新 md5}
  + 少量 shared 件 → 断言 4 对:lootr upgrade(T1)/酒馆 upgrade(三级)/field upgrade(四级)/灾变 rebuilt;
  buckets 断言 to_add/target_only 语义不变
- `20260923/`(r9 换装段): 5 对纯升级全 T1 配对;**swap 视角黄金**:同快照对 `--modpack-swap` 下
  mod_pairs 仍 5 对、target_only 条目带 ⇄upgrade(快照 JSON 断言 mod_pairs 非空 + 渲染断言汇总行)
- 合成规则沿批次D:md5=null(仅 rebuilt 件给 size 差)、game_root=夹具哨兵路径

### T8 文档 + 版本

- README.zh-CN.md / README.en.md:配对四级说明、swap 视角保留 ⇄upgrade、.bak never、
  doctor 行尾归一化(贡献机 autocrlf 不再误报)、junction 提示行为、Last synced v0.8.0
- pyproject.toml + `migration/__init__.py` `__version__` → 0.8.0(两处同步,`mcmig -V` 读后者——批次D 教训)
- `TOOL_VERSION`(snapshot.py)不动:快照格式无变化,仍是 0.6.0 语义戳
- 本地(不入库):`Reference/observations/mcmigrator_服务端测试_202609{21,23}/README.md` 索引 + F25 运维附注

## 4. 测试策略

| 层 | 用例 |
|----|----|
| 单元(moddb) | 三级:酒馆形态(sig+tail 双变)→ upgrade;尾缀同变版本(应被一级吃掉,三级不触发);歧义(减尾键同、一侧 2 候选)放弃;四级:field 形态 → upgrade;剥平台词后 sig 同 → renamed;家族键纯平台词 → 跳过;`_platform_stripped` 空串守卫 |
| 单元(differ/moddb 交界) | `is_mod_jar` 公开后 `_is_mod` 行为等价(既有测试零改即证) |
| CLI(e2e, tmp game_root) | swap 视角:`--json` mod_pairs 非空且 target_only 路径全覆盖;渲染输出含「配对: ⇄upgrade」汇总行;never 桶 modpack_swap 条目;门控:scanned_at 同刻 → stderr 提示在,不同刻 → 无(both junction same_dir 场景,monkeypatch resolve_diff_context 或快照 game_root 指向同目录) |
| doctor/gen_manifest | 临时 data 目录:CRLF 检出 + LF manifest → verify 全绿;LF 件哈希与旧 manifest 数值一致(归一化 no-op) |
| 语料回归 | §3 T7 两个夹具黄金对 + 现有 r1-r7 全部保持 |
| 有意变更断言 | `.bak` 件从 candidate/to_migrate 消失进 never(新增 default 规则测试);junction 提示断言更新 |

## 5. 验收标准

1. 全量 pytest 通过(406 既有 + 新增,有意变更断言随改);ruff check clean
2. r8 黄金:4 对全配(3 upgrade 含三级/四级各 1 + 1 rebuilt);r9 黄金:5 对 upgrade;
   swap 视角 mod_pairs 不丢、⇄upgrade 标记与汇总行保留(F23/F26 闭环)
3. 模拟 autocrlf:CRLF 工作区下 `doctor verify_data_manifest` 无「损坏」
4. junction 双快照不同刻 diff 输出零降级提示;同刻保留提示
5. `.bak` 在 diff 六桶中归 never(--show-never 可见,note=never)
6. `mcmig -V` → 0.8.0;`migration/data/manifest.sha256` 与 data 目录一致

## 6. 妥协与遗留

- **三级 upgrade 不辨版本方向**(降级也标 upgrade):与一级语义一致(一级同样不辨方向),不引入版本比较器
- **平台词表为枚举闭集**:六词覆盖实测语料;新平台词(如 future loader)出现时再扩
- **F25 NTFS 隧道**:运维侧知识,记入观察 README 与九轮报告,不进工具代码/文档主线
- **schema v2 / GUI 锚定 / modid 碰撞 / hint helper / uninstall 措辞**:批次 F backlog 沿挂
- 八/九轮原始快照未入库:黄金对为报告明细合成的 mods-only 夹具(与批次D 同法);完整复放等 schema v2
