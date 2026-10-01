# 批次G 设计规格：世界感知 + 内嵌客户端件收口 + renamed(rebuilt) 标注

- 日期：2026-10-01
- 语料来源：第十一轮 `mcmigrator_服务端测试_20260930.zip`（F32/F33）+ 第十二轮 `mcmigrator_服务端测试_20261001.zip`（F34/F35），已解压至 `Reference/observations/`（gitignored，不入库）
- 基线：main = cfccc8c（0.9.0，批次F），pytest 443 / ruff 0
- 目标版本：**0.10.0**

## §0 语料核对表

| 发现 | 语料证据（实测） | 0.9.0 代码现状 | 定性 |
|----|----|----|----|
| F32 JarInJar 内嵌客户端件 | damage-engine-2.1.1 内嵌 `META-INF/jarjar/anima-1.0.5.jar`，专服构造期 `MultiBufferSource` 崩溃；实验室本地补录 yaml 两条待收编 | `scan_mods` **已解析**内嵌 mods.toml（moddb.py:161-183，ModInfo.embedded_in 已有）；但 `match_client_only_paths` 反查用 `jar_filename`（内层名，物理不存在）→ 内嵌 modid 永不命中 | 一行修复 + 数据收编 |
| F33 renamed 吞没 rebuilt | `create_connected-1.3.3` 剥前缀 + **size 差 1 字节**（6,786,783→6,786,784，md5 异），判 `⇄renamed` | 配对层 `pair_mods_by_filename` 只见路径不见 size；快照 FileEntry 本就存 size | 配对后注记，零哈希成本 |
| F34① 任意命名世界不识别 | `f1-shanghai/`（level-name 指向）+ `world_backup_20261001/`（改名留存）全落 unknown；scan 汇总 must_migrate 665→5 塌缩 | 分类器仅静态 `world/**` glob（default_rules.yaml:43） | scan 动态探测 + 快照字段 + 规则层注入 |
| F34② 世界改名假警报 | pre→mid：world/ 660 件 to_migrate + world_backup 660 件 only_in_dst（内容未消失，仅随目录改名换路径） | 无世界目录级配对概念 | 改名配对提示（仅提示，不重分类） |
| F35 世界二进制演化零检出 | mid→post：3 个 region .mca 被 chunk 懒升级重写（mtime 12:29→13:30）但 size 不变（4,206,592B）→ identical | .mca 按设计不哈希（hashing._BULK_EXTS），FileEntry 无 mtime 字段 | 未哈希文件记 mtime + 同根闸门通道 |
| （无需动代码） | froststalker 闭环（alexsmobs 半年前已移除，config 系陈年孤儿）；0.9.0 首战 6/6 配对全胜；alexsmobs 89 件孤儿已大扫除；演化谱系第 10/11 点 | — | 观察记录，见 observations README |

## §1 目标

1. 专服部署前能从**常规 diff 报告**看见内嵌客户端雷（F32），而非靠人工 unzip 探测。
2. `⇄renamed` 不再吞没内容差异证据；操作者不会据 renamed 误判「字节等同可跳过部署」（F33）。
3. 任意命名的世界目录（level-name 指向 / 含 level.dat 的顶层目录）获得与 `world/**` 一致的必迁语义；scan 汇总不再塌缩（F34①）。
4. 世界目录改名（world → world_backup_日期）降为一条提示，消除千级假警报（F34②）。
5. 同一实例随时间演化的世界二进制重写（懒升级/世界编辑）可检出（F35）。
6. 全部变更对旧快照/旧消费者向后兼容（schema 加法式，SNAPSHOT_FORMAT 保持 1）。

## §2 用户决策记录（2026-10-01）

| 决策点 | 选择 |
|----|----|
| 批次范围 | **全部四项 + F34②**（两轮语料一次闭环） |
| F35 信号强度 | **离桶可见**：同尺寸+mtime 异 → 按 modified 入桶，note=mtime |
| 世界归类 | 探测到的世界目录（含备份/改名留存）**全部 must_migrate** |
| 版本号 | **0.10.0**（功能性变更，minor 递进） |

## §3 设计

### §3.1 F32：client_only 内嵌反查 + yaml 收编

**修复**（pipeline.py `match_client_only_paths` 注册表通道）：

```python
for mods in (ctx.src_mods, ctx.dst_mods):
    for mid in mods.modids:
        if mid in client_modids:
            info = mods.get(mid)
            physical = info.embedded_in or info.jar_filename  # 内嵌件反查宿主物理文件
            hit.add(f"mods/{physical}")
```

- 内嵌 modid（如 anima）命中时标注的是**宿主 jar 路径**（物理部署件）——这正是专服要隔离的实体。
- 尾部 `hit & paths` 交集已有守卫：宿主 jar 不在 mods 桶（如已隔离删除）则自然不标。
- 家族键通道（复放模式）不变：内嵌件无物理文件名，复放模式靠宿主的 family 条目兜底（damage-engine 条目即为此）。

**数据收编**（migration/data/client_mods.yaml 追加两条，实验室 r12 本地补录转正）：

```yaml
  - modid: damageengine
    family: damage-engine
    reason: r12 专服构造期 MultiBufferSource 崩溃(F32 实证,已隔离;内嵌 anima)
  - modid: anima
    family: anima
    reason: r12 damage-engine 内嵌 JarInJar 客户端渲染件(F32;modid 通道命中宿主,
      family 兜底独立散装 anima jar)
```

- data yaml 变更后**必须重跑 `tools/gen_manifest.py`**（manifest.sha256 增行）。
- scan_mods 不改（内嵌解析已存在且实测工作）。

### §3.2 F33：renamed 配对的内容差异注记

**原则**：配对语义（kind）不变——create_connected 确实是改名对；变的是**证据呈现**：同版本改名但 size 异 → 注记 `content_differs`，与 F17/F20「同名同版本异构建 ⚠」谱系对齐。

**ModPair 加法式扩展**（moddb.py）：

- 字段 `content_differs: bool = False`（frozen dataclass 默认值，全部现有构造点零改动）。
- `to_dict()` 仅在 True 时输出 `"content_differs": true`（JSON 消费方零噪声；schema 加法式）。

**纯函数注记**（moddb.py 新增）：

```python
def annotate_content_differs(
    pairs: list[ModPair], src_sizes: dict[str, int], dst_sizes: dict[str, int],
) -> list[ModPair]:
    """同版本改名对(size 异)注记 content_differs(F33)。

    仅 kind=="renamed" 参与:upgrade 本就隐含内容变化,rebuilt 已有 ⚠;
    任一侧 size 未知(旧快照/registry 通道无快照对应)则不注记(不猜)。
    """
```

- 实现：`dataclasses.replace(p, content_differs=True)`（frozen）。
- 接线：`pipeline.compute_mod_pairs` 在 `merge_mod_pairs` 之后追加：
  `pairs = annotate_content_differs(pairs, {e.path: e.size for e in src.files}, {e.path: e.size for e in dst.files})`——通道无关（registry/filename 合并后统一注记）。

**呈现**（reporter.py）：

- DiffReporter 增第二映射 `_pair_content_differs: set[str]`（构造时从 mod_pairs 收集）。
- mods 分支：`parts[0]` 在 content_differs 时为 `f"⇄{kind}(rebuilt)"`；⚠ 前缀条件扩展为 `kind == "rebuilt" or item.note == "rebuilt" or item.path in self._pair_content_differs`。
- never/modpack_swap 镜像分支（F23）同步：`⇄renamed(rebuilt)` + ⚠。
- PlanReporter COPY 行路径格镜像：`f"{r.path} ⇄{kind}(rebuilt)"` + ⚠（批次F ⚠ 条件同幅扩展）。
- 汇总行 `⇄renamed ×N` 计数**不变**（按 kind 计数，rebuilt 标记属证据注记非新类别）。
- 局限（文档化）：size 相等而字节异的改名对不可见——md5=null 的既定代价，语料实证差异伴随 size 变化。

### §3.3 F34①：动态世界目录探测

**快照字段**（snapshot.py Snapshot）：

- `world_dirs: list[str] = field(default_factory=list)`；save 写键；load `payload.get("world_dirs", [])`（非 list / 元素非 str → 空表，容错降级不抛）。
- SNAPSHOT_FORMAT 保持 **1**（加法式可选字段，旧工具读新快照忽略未知键，新工具读旧快照得空表——双向兼容；registry 内嵌的 schema v2 仍是后续批次事项）。

**探测函数**（scanner.py 新增，纯函数可测）：

```python
def detect_world_dirs(entries: list[FileEntry], properties_text: bytes | None) -> list[str]:
    """探测世界目录(F34①):server.properties level-name + 顶层含 level.dat 目录。

    ① level-name 指向目录(须为单段安全名且在文件清单中存在)——活跃世界;
    ② 顶层直接包含 level.dat 的目录——覆盖任意命名/改名留存/多世界(Bukkit 系);
    并集去重升序。properties_text=None(server.properties 缺失/不可读)仅走②。
    """
```

- properties 解析复用 textcompare：将 `_parse_properties` 提为公有 `parse_properties`（原私有名保留别名或直接改名，调用点同步）。
- 安全校验：level-name 值含 `/`、`\`、为 `.`/`..` 或空 → 跳过①（防路径注入规则层）。
- `Scanner.build_snapshot`：读 `version_dir/server.properties` 字节（OSError → None），`world_dirs=detect_world_dirs(entries, text)`。
- 客户端版本文件夹不误触：客户端世界在 `saves/<名>/level.dat`（二层），顶层 level.dat 探测天然不命中；客户端无 server.properties。

**规则层注入**（cli.py build_ruleset）：

- 新关键字参数 `world_dirs: Sequence[str] = ()`。
- 生成 `Rule(match=f"{d}/**", decide=MUST_MIGRATE, reason="世界目录探测(F34:level-name/level.dat)", source="world")` 列表，经 `from_layers(..., default, world_layer)` 追加在 **default 之后（最低优先级）**。
- 位置语义：default 的 never 规则（如 `**/*.bak`、`logs/**`）对世界内文件**仍优先**——与今天 `world/**` 在 default 段内位于 never 之后的同序语义；用户/CLI 规则可覆盖。
- 无效目录名（空/含斜杠/..）跳过并入 errors 警告（与规则校验同风格）。

**接线**（三处）：

- `cli._cmd_scan`：调序——先 `scan_version` 后 `build_ruleset(..., world_dirs=snap.world_dirs)`（ruleset 仅用于其后的分类汇总，调序无行为耦合）。
- `pipeline.run_diff`：`world_dirs=sorted(set(src.world_dirs) | set(dst.world_dirs))`。
- `pipeline.build_plan`：同式取双侧快照并集。
- 空 world_dirs（客户端场景/旧快照）→ 零规则注入 → 输出与 0.9.0 逐字节一致；服务端仅 "world" 时动态层被静态 `world/**` 先命中，分类同值零扰动。

**归类**：全部 must_migrate（用户决策③）——活跃/备份/改名留存皆玩家进度；仅 default 未命中的路径落此层。

### §3.4 F34②：世界改名配对提示

**纯函数**（pipeline.py 新增）：

```python
def world_rename_notices(src: Snapshot, dst: Snapshot) -> list[str]:
    """世界目录改名探测(F34②):src 独有世界 A → dst 独有世界 B 同路径同尺寸占比≥90%。

    候选:A ∈ src.world_dirs 且 ∉ dst.world_dirs(旧路径消失),B 反之(新路径出现);
    匹配:相对子路径双侧存在且 size 相等;占 min(|A文件|,|B文件|) ≥ 0.9 且 ≥1 件。
    仅提示不重分类(保守——假警报降级为可见解释,桶语义不动)。
    """
```

- 消息形如：`[提示] 疑似世界目录改名: world → world_backup_20261001(660 件同路径同尺寸,内容未消失,已随目录改名迁移)`。
- r13 实测锚：660/660 = 100%；world → f1-shanghai 跨世界子路径重合率极低，不触发。
- 阈值 0.9 语料标定（r13 全量同尺寸；容忍零星 session.lock 类重写）。
- 接线：run_diff 在 diff_identity_notices 之后追加进 notices（stderr 转发）。

### §3.5 F35：未哈希文件 mtime 记录 + 同根演化通道

**FileEntry 扩展**（snapshot.py）：

- `mtime: int | None = None`（epoch 秒，int）；save 经 asdict 自动带上；load `d.get("mtime")`（非 int → None）。

**记录范围**（scanner.py scan）：仅**未哈希**文件（`should_hash` False → md5 None 的 bulk/mods 件）记录 `mtime=int(st.st_mtime)`——stat 结果复用（现行 `p.stat().st_size` 改为单次 `st = p.stat()` 取双值）；哈希文件保持 None（快照精瘦、意图明确；strict 模式全哈希 → 全 None，通道自然失活）。

**diff 通道**（differ.py）：

- Differ 构造参数 `mtime_evidence: bool = False`（默认关——全部现有调用点行为不变）。
- `_same_content_semantic` 的 size-based 分支前插入：

```python
if s.md5 is None and d.md5 is None:
    if (self.mtime_evidence and s.mtime is not None and d.mtime is not None
            and s.mtime != d.mtime and s.size == d.size and not is_mod_jar(path)):
        return False, "mtime"   # 同尺寸重写(懒升级/世界编辑);note 区别于字节比对
    return s.size == d.size, "size-based"
```

- 通道四重闸门：① mtime_evidence 开（见下）；② 双侧 mtime 已知；③ size 相等（size 异本就走 modified，无需 mtime）；④ 非 mods jar（jar 同尺寸重打包是 F33 域，不混入）。
- **闸门计算**：`run_diff` 中 `mtime_ev = src.resolved_root is not None and src.resolved_root == dst.resolved_root`——**同一物理目录随时间演化**（服务端逐段快照/junction 实验室形态）才开；跨实例迁移（复制必变 mtime）恒关。`build_plan` 以 `src_dir.resolve() == dst_dir.resolve()` 同式。
- note="mtime" 落桶：must_migrate（世界）→ to_migrate；unknown → candidate。planner/executor 按 modified 常规处理（COPY），无特判。
- 呈现：reporter `_display_note` 对 note=="mtime" 显示 `mtime(同尺寸重写)`（F3「new ←仅源」同风格的显示映射；JSON 保持原始 "mtime"）。
- 旧快照（无 mtime / 无 resolved_root）→ 通道失活，行为与 0.9.0 一致；重扫即获得能力。

## §4 夹具与语料

新夹具（`tests/fixtures/server_corpus/`，延续 r11 合成式口径——以 diff JSON 真值为骨、规模裁剪、game_root 指向不可达的 sanitizer 路径）：

| 夹具 | 内容 | 锚定 |
|----|----|----|
| `20260930/r12_pre.json` + `r12_post.json` | mods-only 合成：11 旧件（src 独有）+ 11 新件（dst 独有）+ 少量 shared；**size 取自 `旧件指纹.md` 实测**（create_connected 6,786,783 vs 6,786,784；LazyTick/ftb-chunks 双侧同尺寸） | 6 配对（renamed×3 + upgrade×3）；create-connected `content_differs=true`；LazyTick/ftb-chunks 无此键 |
| `20261001/r13_pre.json` | world/ 缩样（level.dat、region/r.0.0.mca 带 mtime、session.lock、serverconfig 样例）+ server.properties + mods 骨架 | world_dirs=["world"] |
| `20261001/r13_mid.json` | world_backup_20261001/（与 pre 的 world/ 同子路径同尺寸缩样）+ f1-shanghai/（level.dat 66,492、region/r.-1.-1.mca 4,206,592 带 mtime）+ server.properties（level-name=f1-shanghai）+ automobility target_only | world_dirs=["f1-shanghai","world_backup_20261001"]；三夹具 resolved_root 同值（mtime 闸门复放开） |
| `20261001/r13_post.json` | f1-shanghai 演化：r.-1.-1.mca **size 同 mtime 异**；level.dat 5,519（size 异）；mod 世界数据 dst 新生缩样 | world_dirs=["f1-shanghai"] |

**注意**：夹具断言锚定**新行为**——mid→post 的 raids.dat/level.dat 等在 0.9.0 语料里落 candidate，F34① 后 f1-shanghai/** 为 must_migrate → 落 to_migrate。spec 明示这是有意分叉，语料原文桶值属 0.9.0 行为。

**活体单测**（tmp_path 小树，不走夹具）：detect_world_dirs 三形态（level-name/level.dat/非法名守卫）；scanner mtime 记录（os.utime 设值 + 未哈希限定）；Differ 闸门开关矩阵；build_ruleset world 层位序（世界内 .bak 仍 never）。

夹具 README 补 20260930 / 20261001 两行；observations README 补两轮收官记录。

## §5 验收标准

1. `pytest tests/` 全绿（基线 443 + 新增），`ruff check .` 0 违例。
2. F32：内嵌注册表单测——registry 含 anima(embedded_in=damage-engine jar) 时 client_only 命中宿主路径；`load_client_mods` 含 4 条目；manifest.sha256 重生成且仅增行。
3. F33：`annotate_content_differs` 单测（renamed+size异→True；upgrade/size同/size未知→不改）；r12 夹具复放——mod_pairs 含 `create-connected ... "content_differs": true`；终端渲染 `⇄renamed(rebuilt) ⚠`；plan COPY 行镜像。
4. F34①：探测单测三形态 + 非法名守卫；规则层位序单测（世界内 `.bak` never 优先）；scan/diff/plan 三接线；空 world_dirs 输出与 0.9.0 逐字节一致（既有夹具全量回归即证）。
5. F34②：r13 夹具复放发改名提示；无改名/低重合不发；阈值边界（<0.9 不发）单测。
6. F35：mtime 记录限定单测；r13 复放——r.-1.-1.mca 落 to_migrate note=mtime；跨根闸门关；旧快照（无 mtime）惰性；mods jar 不触发。
7. schema 兼容：SNAPSHOT_FORMAT 仍 1；旧快照加载全通过（既有快照回放测试即证）；diff JSON 仅加法（content_differs 条件键）。
8. 版本 0.10.0 双文件同步（pyproject.toml + migration/__init__.py）；README zh/en 五点同步（动态世界目录/内嵌反查/renamed(rebuilt)/mtime 通道/改名提示）；`Last synced: v0.10.0`。
9. gen_manifest 重跑；夹具 README 与 observations README 收官行就位。

## §6 任务划分（SDD，每任务 TDD + 评审 + fix loop ≤5）

| 任务 | 内容 | 主改文件 |
|----|----|----|
| T1 | 四套夹具 + 语料回归骨架红测（断言按本 spec 新行为） | tests/fixtures/server_corpus/、tests/test_corpus_regression.py、fixtures README |
| T2 | F33：ModPair.content_differs + annotate_content_differs + compute_mod_pairs 接线 + 双 reporter 呈现 | moddb.py、pipeline.py、reporter.py |
| T3 | F32：embedded_in 反查 + client_mods.yaml 两收编 + manifest | pipeline.py、data/client_mods.yaml、manifest.sha256 |
| T4 | F34①：Snapshot.world_dirs + detect_world_dirs + textcompare 公有化 + build_ruleset world 层 + 三接线 | snapshot.py、scanner.py、textcompare.py、cli.py、pipeline.py |
| T5 | F34②：world_rename_notices + run_diff 接线 | pipeline.py |
| T6 | F35：FileEntry.mtime + 记录 + Differ.mtime_evidence 闸门 + 双管线接线 + 显示映射 | snapshot.py、scanner.py、differ.py、pipeline.py、reporter.py |
| T7 | 语料回归转绿收口 + 版本 0.10.0 + README 双语 + manifest + 全量验证 | pyproject.toml、__init__.py、README×2 |

依赖：T4 → T5；T1 先行立红；T2/T3/T6 相互独立。合并：单干净 squash 提交（用户授权委托收尾）。

## §7 妥协与边界

1. **mtime 通道的先天局限**：仅未哈希文件记录、依赖 resolved_root（0.6.x 旧快照两样皆无 → 通道关闭，重扫即得）；闸门外的同尺寸重写（跨实例复制后演化）不可见。
2. **F33 的 size 代理局限**：size 相等而字节异的改名对不注记（md5=null 既定代价）；不为此给 mods 全量哈希（430MB 级扫描代价）。
3. **F34② 仅提示不重分类**：661 条 to_migrate 行仍在桶内，但 stderr 一条解释降恐慌；自动折叠桶行是后续 backlog。
4. **world 层位序**：default never 规则压过动态世界层（世界内 .bak 仍不迁）——与现状同序，防新层意外翻案。
5. **schema v1 保持**：registry 内嵌 schema v2（复放模式配对/内嵌识别的根治）仍是下一批次首位 backlog。
6. **client_only JSON 不加键**（延续批次F 决策）；mod_pairs 仅条件键 content_differs；快照新键 world_dirs/mtime 全可选。
7. 阈值与闭集：改名配对 0.9、探测校验规则为语料标定值，新语料出现再调（不提前泛化）。
