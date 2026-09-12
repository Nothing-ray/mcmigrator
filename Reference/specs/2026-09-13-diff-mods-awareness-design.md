# diff 层 mod 感知设计(批次 B:F4 配对 / F2 孤儿打通 / F3 方向语义)

- 日期: 2026-09-13
- 状态: 待评审
- 前置: 2026-09-04-gui-design(v0.6 已交付)、2026-09-13 服务端语料回归夹具(feat/server-scenario)
- 语料来源: Reference/observations/mcmigrator_服务端测试_20260908/20260912(F1-F9 报告)

## 1. 目标与非目标

### 目标
1. **F4 配对**: 独立 `diff` 的 mods 桶把「同 mod 跨版本升级」与「同 jar 改名」配对呈现,
   不再以"删旧+增新"两条无关条目出现。7 对升级(waystones/sophisticatedbackpacks 等)+ 1 对改名
   ([稀有精英怪] infernalmobs)为黄金语料。
2. **F2 孤儿打通**: 独立 `diff` 命令获得与 plan 相同的孤儿 config 标注能力
   (mod 已在目标侧删除 → never 桶 note=orphan)。二轮语料删除态孤儿(create_pillagers_arise/
   modern_glass_doors)与一轮保留态孤儿(zstdnet/** 等 40+ 文件)均应可见。
3. **F3 方向语义**: 报告读者不再把 candidate 的 `new`(源独有)误读为"目标新增"。

### 非目标(显式排除,防蔓延)
- **不改任何迁移行为**: planner 的 `_MOD_NOTE_MAP`(to_add→COPY 等映射)与 executor 语义零变化。
  「plain migrate 时源侧旧版 jar 默认不迁(避免同 modid 双版本冲突)」记为未来候选,本轮不做。
- 不改 diff 六桶结构、不改现有 JSON 键名(`to_add`/`target_only`/`shared` 等全部保留)。
- 不动 GUI(server.py 消费 plan 管线,其孤儿/mod_added 语义本已正确)。
- 不处理 F8(CWD/workdir 统一,另行安排)。

## 2. 总体架构: 纯标注层,不动分桶

```
_cmd_diff
  ├─ Snapshot.load ×2                          # 现状不变
  ├─ pipeline.resolve_diff_context(src,dst)    # ★新增: 解析两侧版本目录 → scan_mods ×2
  │     失败(game_root 不可达/目录缺失) → None, 打印一行提示,行为退回今天
  ├─ build_ruleset(... orphan_rules=ctx 存在时 generate_orphan_rules(...))   # ★F2
  ├─ Differ(...).diff()                        # 分桶逻辑零改动
  ├─ moddb.pair_mods(src_reg, dst_reg)         # ★F4: 配对清单(纯新增信息)
  ├─ DiffReporter(report, pairs=..., ctx=...)  # ★渲染层: F3 箭头 + F4 配对标记
  └─ 输出(rich / --json)
```

关键决策:**Differ 与 planner 完全不动**。配对与孤儿是"扫描上下文带来的附加信息",
分桶/note/行为全部保持 —— 六桶回归夹具(脱敏 game_root → 上下文解析失败 → 降级路径)
恰好锚定"无扫描时与今天逐字节一致"。

## 3. 组件契约

### 3.1 `pipeline.resolve_diff_context(src_snap, dst_snap) -> DiffContext | None`

```python
@dataclass(frozen=True)
class DiffContext:
    src_mods: ModRegistry
    dst_mods: ModRegistry

def resolve_diff_context(src: Snapshot, dst: Snapshot) -> DiffContext | None:
    """从两份快照的 game_root+version 解析各自版本目录并扫描 mods。

    任一侧目录不可达(跨机复放/夹具/手动删除)→ 返回 None,调用方降级。
    版本目录 = <game_root>/versions/<version>(服务端 Junction 影子根同样成立)。
    """
```

- 复用 `moddb.scan_mods`(含 jar-in-jar);不写盘、只读 zip 中央目录,~114 jar 亚秒级。
- `Snapshot.game_root` 为空/目录不存在 → None。异常(scan_mods 内部已容忍损坏 jar)不外抛。

### 3.2 F2 孤儿: 复用 plan 的生成路径

`ctx` 存在时,`_cmd_diff` 以 `generate_orphan_rules(src_snap.files, ctx.dst_mods, override)`
产出 orphan 规则传入 `build_ruleset`(与 `pipeline.build_plan` 完全同源)。
效果: 孤儿 config 落 never 桶 note=orphan —— 一轮保留态(identical→never/orphan)、
二轮删除态(candidate→never/orphan)均获得标注。override 表加载与 plan 同源(mod_config_map)。

### 3.3 F4 配对: `moddb.pair_mods(src_mods, dst_mods) -> list[ModPair]`

```python
@dataclass(frozen=True)
class ModPair:
    modid: str
    kind: str            # "upgrade"(同 modid 异版本) | "renamed"(同 modid 同版本异文件名)
    src_files: list[str] # 源侧 jar 相对路径(通常 1 个)
    dst_files: list[str] # 目标侧 jar 相对路径
    src_version: str | None
    dst_version: str | None
```

配对规则(按 modid 分组,组内:
- 两侧均有该 modid:版本不同 → `upgrade`;版本相同但文件名集合不同 → `renamed`;
  版本与文件名均相同 → 不配对(已是 shared)。
- 仅一侧有 → 不配对(维持 to_add/target_only 原语义)。
- 多 jar 同 modid(罕见): 整组列入 src_files/dst_files,版本取最高;不做逐 jar 拆分。

### 3.4 渲染与 JSON

- **JSON**: 顶层新增 additive 键 `"mod_pairs": [ModPair...]`(无 ctx 时为 `[]`)。
  现有六桶键与 note 值零改动 —— 消费方兼容。
- **rich 表**: mods 桶中命中配对的行,note 列追加标记: 升级对两侧分别为
  `to_add ⇄upgrade`(旧版)/ `target_only ⇄upgrade`(新版);改名对两侧分别为
  `to_add ⇄renamed` / `target_only ⇄renamed`(文件名不同,分桶必为 to_add/target_only)。
  表尾注脚列出配对摘要(`⇄upgrade ×7 · ⇄renamed ×1`,与 orphan 分组脚注同风格)。
- **F3 方向提示**: candidate/only_in_dst 的 one-sided 行 note 显示为
  `new ←仅源` / `target_only →仅目标`;JSON 值不变,仅 rich 显示层。
- **语义文档**: README「分类系统」新增 mods 桶语义小节: src=迁移源视角,`to_add`=源有目标无。

### 3.5 CLI 输出

- ctx 解析失败: `_print_err` 一行 `[提示] mods 扫描不可用(game_root 不可达),配对与孤儿标注已跳过`;
  stderr,不污染 `--json` stdout。
- ctx 成功但产出 0 孤儿/0 配对: 静默(不打扰)。

## 4. 关键决策记录

| 决策 | 理由 |
|---|---|
| 配对放渲染/JSON 层,不动 Differ 分桶 | planner `_MOD_NOTE_MAP` 与六桶夹具零波动;行为变化(升级对跳过源侧旧版)是语义决策,单独立项 |
| 扫描失败 = 降级而非报错 | 跨机快照复放(语料夹具)必须保持可用;diff 是只读快操作,不该因 game_root 缺失而拒答 |
| 孤儿规则与 plan 同源复用 | 两处语义漂移是最危险的腐化路径;generate_orphan_rules 单一实现 |
| renamed 定义为"同 modid 同版本异文件名" | 语料中的改名(infernalmobs)是包作者加中文前缀;md5 相同是巧合强化证据而非必要条件 |
| F3 只改显示不改 JSON 键 | 报告的 JSON 消费方(测试夹具/未来工具)不应为可读性付兼容成本 |

## 5. 测试策略

1. **降级路径回归**: 现有 `test_corpus_regression.py` 六桶锚定不动(脱敏 game_root → ctx=None
   → 与今天一致)—— 夹具即"无扫描行为不变"的证明。
2. **构造路径**(conftest mini 版本目录):
   - ctx 解析: game_root+version 存在 → 双注册表;缺失 → None
   - F4: 升级对/改名对/无配对三种 ModPair 输出;JSON `mod_pairs` additive 断言(六桶键不变)
   - F2: dst 缺 mod → 其 config 落 never/orphan;dst 有 mod → 不标注
   - F3: rich 渲染含 `←仅源`/`⇄upgrade` 标记(Reporter 单测)
3. **e2e**: mini 游戏根上 `diff --json` 全链路(ctx 成功),断言 mod_pairs + orphan + 六桶。

## 6. 验收标准

1. 语料回归夹具 5 测试不改动且全过(降级路径锚定)。
2. 黄金语料对拍: 用 0912 原始 game_root 结构(本地 junction 或临时目录重建 7 对升级 + 1 改名)
   跑 diff,`mod_pairs` 恰含 7×upgrade + 1×renamed。
3. 一轮语料(保留态孤儿)与二轮语料(删除态孤儿)在 ctx 可用时,孤儿 config 均 `note=orphan`。
4. plan/swap/migrate/e2e 全量测试零改动全过(行为不变证明)。
5. README mods 桶语义小节落地;`mcmig diff --help` 无新必填参数(全部自动)。

## 7. 显式妥协(原型期)

- 升级对不展示版本号差异摘要(如 `21.1.42→21.1.44`): rich 行宽有限,v1 先靠文件名自证;
  JSON 的 src/dst_version 字段已机读。
- 多 jar 同 modid 的组内拆分不做(整组配对)—— 语料中未出现,等真实需求。
- ctx 扫描不做缓存: diff 本就低频,~100 jar 亚秒级扫描可接受。
