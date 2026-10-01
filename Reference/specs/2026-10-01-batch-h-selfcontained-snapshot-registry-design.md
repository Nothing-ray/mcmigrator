# 批次H 设计规格：快照自包含（schema v2 内嵌 mod 名册）+ 批次G 遗留收口

- **日期**: 2026-10-01
- **版本**: 0.10.0 → 0.11.0
- **主题**: 让快照「拍全」——mod 名册随快照落盘，diff 消费快照优先；顺带清完批次G 终审遗留与两个整洁项
- **前置**: 批次G（`5300b94`, v0.10.0, F32-F35）已合并推送；476 测试 + ruff 全绿

---

## §0 背景与代码验证

### 痛点三层（快照缺 mod 名册）

快照目前只记录「文件长相」（path/size/md5/mtime/world_dirs），mod 名册（`scan_mods`
从 jar 的 mods.toml 提取的 modid/version 等）**不落盘**，diff/plan 时现场重扫：

| # | 痛点 | 证据 |
|---|------|------|
| 1 | **跨机/离线复放失真**：目录不可达 → `resolve_diff_context` 返回 None → 孤儿标注、registry 配对、client_only modid 通道全哑，只剩文件名配对兜底 | `pipeline.py:374-404`（不可达即 None）、`pipeline.py:649-651`（降级提示行）；复放对账结论（2026-09-19）实锤现场数字 ≠ 复放数字，差值即活体层效应 |
| 2 | **junction 影子根配对恒失效**（F14 真根因）：服务端标准用法=同一物理目录不同刻双快照，diff 时活体现扫两次恒等（都是最新状态）→ registry 配对恒空，filename 兜底 22/25 对 | `pipeline.py:351`（same_dir 注释）、`pipeline.py:512`（same_dir 时跳过 registry 配对）；`DiffContext.read_file` 同因短路 |
| 3 | **时刻不配套**：files 清单是 scan 时刻的，mods 名册是 diff 时刻现扫的；scan 后 mods 又变化则两者错位 | `scanner.py:92-111`（build_snapshot 不调 scan_mods）vs `pipeline.py:241-242`（build_plan 现扫） |

### 批次G 终审遗留（informational，本轮收口）

| # | 项 | 现状证据 |
|---|----|---------|
| 4 | world_rename 最小分母可被微小世界打穿（1 文件世界 100% 命中 → 一条无害误报提示） | `pipeline.py:475-478`（`denom = min(...)` 无下限） |
| 5 | world_dirs glob 元字符未加固：世界名 `world[1]` 注入 `Rule(match="world[1]/**")` 后 `[1]` 被当字符类，连带匹配 `world1/` | `cli.py:227-235`（守卫仅 `/ \ . ..`） |
| 6 | match_client_only_paths 内嵌宿主反查 + `& paths` 交集守卫缺直测（F32 夹具只间接覆盖） | `pipeline.py:547-555` |
| 7 | zh/en README 优先级图缺世界层；快照 v2 说明缺失 | `README.zh-CN.md:104` |

### 随手项与整洁项

| # | 项 | 现状证据 |
|---|----|---------|
| 8 | client_only 警示行无截断（全量 join，清单长则刷屏） | `pipeline.py:708-711` |
| 9 | `build_ruleset` 留在 cli，pipeline 延迟导入借调（循环依赖规避注释自述别扭） | `cli.py:170`、`pipeline.py:235/637` |
| 10 | `_LATTICE` 为 list，宜 tuple 化（防意外修改的常量习惯） | `moddb.py:696` |

---

## §1 目标与非目标

### 目标

1. 快照 schema v2：scan 时把 mod 名册序列化进快照（约 200 条 × 150B ≈ 30KB/份）；
2. 消费策略 **B（快照优先）**：双侧快照均有嵌入名册时，registry 消费（孤儿/配对/client_only/compat）
   一律用嵌入名册，活体现扫仅作无嵌入（v1 快照）时的回退；
3. 修复痛点 1/2/3：复放保真、junction 配对恢复（modid 级）、名册与 files 同刻同源；
4. 收口终审遗留 4/5/6/7 与随手项 8、整洁项 9/10。

### 非目标

- **不**在快照内嵌 jar 内容或文件内容——语义复核（read_file）仍需活体目录，不可达退回字节比较；
- **不**改 mods 桶哈希分层策略（jar 仍 md5=null）；
- **不**做 GUI 系列（锚定/碰撞/措辞）、pywebview、.mcmigpack、CI 项（批次I+）；
- **不**改 mod 配对键格（`_LATTICE` 仅类型注解变化，五级语义零变化）；
- **不**动 plan.json schema（mod_pairs 仍不持久化进 plan 文件）。

---

## §2 用户决策（2026-10-01）

| 决策点 | 结论 |
|--------|------|
| 内嵌名册消费策略 | **B 快照优先**（彻底修三痛点；变化窗口窄：仅 v2 快照的 junction 场景配对从无到有，从错到对） |
| 批次范围 | 主菜 + 全部四配菜（4/5/7/8） |
| 整洁项 9/10 | **带上**（build_ruleset 搬迁 + `_LATTICE` tuple 化） |
| 版本号 | **0.11.0**（快照格式升 v2 + 行为增强，minor 递进） |

---

## §3 设计

### 3.1 快照 schema v2：mods 名册序列化

**Snapshot 字段**（`snapshot.py`）：

```python
@dataclass
class Snapshot:
    ...
    mods: list[dict] = field(default_factory=list)  # v2:mod 名册(嵌入 registry)
```

- 元素为原始 dict（snapshot 层不认识 ModInfo，保持 `moddb → snapshot` 单向依赖），
  键：`modid` / `version` / `jar_filename` / `neoforge_range` / `embedded_in`
  （后两者可为 None；与 `ModInfo` 五字段一一对应）；
- `save`：payload 增加 `"mods"` 键；`snapshot_format` 写 `SNAPSHOT_FORMAT = 2`（常量 1→2）；
- `load`：接受 fmt ∈ {1, 2}（`fmt not in (1, SNAPSHOT_FORMAT)` 才抛 `SnapshotFormatError`，
  错误文案保留「请重新 scan」提示）；v1 无 mods 键 → `[]`；
- **mods 容错哲学与 world_dirs 一致**：`mods` 非 list → 降级 `[]`（增益层损坏不阻断 diff）；
  元素非 dict 或缺 `modid`/`jar_filename` → 跳过该条（数据级宽松）。

**moddb 转换函数**（`moddb.py`，与 ModRegistry 同文件）：

```python
def registry_to_dicts(registry: ModRegistry) -> list[dict]:
    """注册表 → v2 快照可序列化的 dict 列表(按 modid 升序)。"""

def registry_from_dicts(items: list[dict]) -> ModRegistry:
    """dict 列表 → 注册表;缺键条目跳过(与 snapshot.load 元素级容错同哲学)。"""
```

`ModRegistry` 补迭代入口 `entries() -> list[ModInfo]`（按 modid 升序），供 to_dicts 与测试用。

**scanner 接线**（`scanner.py`）：

- `Scanner.build_snapshot` 内调用 `moddb.scan_mods(self.version_dir)`，结果经
  `registry_to_dicts` 存入 `snap.mods`（scanner → moddb 顶层依赖，无循环：
  moddb → snapshot、scanner → {hashing, snapshot, moddb}）；
- **scan 耗时变化（用户可见）**：现状 scan 不开 jar（`scan_mods` 只在 diff/plan 调用），
  v2 起 scan 顺带扫名册——119 jar 开 zip 读 META-INF 单条目，预期 +1~3 秒；
  README 说明（见 §3.8）；
- scan_mods 对 mods/ 缺失、jar 损坏已有容错（warning + 跳过），沿用。

### 3.2 消费策略 B：registry 消费快照优先

**DiffContext 扩展**（`pipeline.py`）：

```python
@dataclass(frozen=True)
class DiffContext:
    src_mods: "ModRegistry"
    dst_mods: "ModRegistry"
    src_dir: Path
    dst_dir: Path
    same_dir: bool = False        # 物理同目录 → read_file 短路(语义不变)
    mods_frozen: bool = False     # 双侧 mods 来自快照嵌入(v2)→ registry 配对可信
```

- `read_file` 的 `same_dir` 短路**语义不变**（物理同目录读数恒等，嵌入不救内容读取）；
- registry 配对闸门从 `same_dir` 改为 `same_dir and not mods_frozen`
  （嵌入名册来自各自 scan 时刻，即使物理同目录、不同刻也不恒等 → 配对有意义；
  自比对/同刻场景两侧嵌入天然恒等 → 配对自然为空，无需闸门）。

**resolve_diff_context 改造**：

```
src_has = bool(src_snap.mods); dst_has = bool(dst_snap.mods)
若 src_has and dst_has（双侧嵌入，时刻对称才走冻结通道）:
    src_mods/dst_mods = registry_from_dicts(...)
    mods_frozen = True
    src_dir/dst_dir = 按快照 game_root 解析（可达与否不影响 mods 来源）
    same_dir = 活体双侧可达 → resolve() 比较；否则快照 resolved_root
              （双侧均有且相等 → True；缺 None → False）
否则（至少一侧 v1 无嵌入）:
    完全走 0.10.0 路径：活体双侧可达 → 现扫建 ctx（mods_frozen=False）；
    任一不可达 → 返回 None（逐字节现状，混合 v1/v2 不做单侧嵌入——保时刻对称）
```

**compute_mod_pairs 零改动**：`same_dir` 形参语义收敛为「registry 恒等不可信」，
调用方（run_diff）传 `ctx.same_dir and not ctx.mods_frozen`。

**run_diff 接线**：

- `ctx = resolve_diff_context(...)` 之后逻辑不变（orphan 用 `ctx.dst_mods`、
  配对用双侧、client_only 用双侧——嵌入名册自动流入）；
- 降级提示行调整：
  - ctx=None（现状行，保留原文）：仅 v1 快照不可达时出现；
  - 新增一行（嵌入+目录不可达）：`[提示] 版本目录不可达,已使用快照内嵌 mod 名册
    (语义复核退回字节比较)`——孤儿/配对照常，只降语义复核，向用户说明 semantics
    note 缺席的原因；
  - 嵌入+目录可达：不加提示（静默走冻结通道，输出无扰动）。

**build_plan 接线**：

- `src_mods = registry_from_dicts(src_snap.mods) if src_snap.mods else scan_mods(src_dir)`；
  dst 同理（`rescan_dst=True` 时 dst 快照刚重扫即 v2 含嵌入，自然一致）；
- 配对闸门 `same_dir = 活体 resolve 相等 and not 双侧嵌入`；
- orphan 规则、compat 检查（`neoforge_range` 已在嵌入五字段内）自动吃到嵌入名册；
- Differ 的 `mtime_evidence` 活体 resolve 比较保持不变（与 mods 来源无关）。

**行为矩阵**（本设计的核心承诺）：

| 快照组合 | 目录 | mods 来源 | registry 配对 | 对 0.10.0 |
|----------|------|----------|--------------|-----------|
| v1 + v1 | 双侧可达 | 活体现扫 | 现状闸门 | 逐字节一致 |
| v1 + v1 | 不可达 | 无（ctx=None） | 仅 filename | 逐字节一致 |
| v2 + v2 | 双侧可达 | **嵌入** | 开（同刻恒等自然空） | junction 场景配对从无到有（修 F14） |
| v2 + v2 | 不可达 | **嵌入** | 开（复放保真） | 孤儿/配对/client_only 从哑到活（修痛点 1） |
| v1 + v2 | 任意 | 活体双侧可达→现扫；否则 None | 现状 | 逐字节一致（保时刻对称，不单用一侧嵌入） |

### 3.3 F32 守卫直测（终审托付）

`match_client_only_paths`（`pipeline.py:531-555`）补直接单测：

- **负例**：嵌入件 modid 命中 client 清单（`embedded_in="host.jar"`），但宿主 jar 不在
  `report.mods`（双侧共有不进 mods 桶）→ `hit & paths` 交集守卫挡住 → 不误报；
- **正例**（对照）：宿主在 mods 桶 → 命中 `mods/host.jar`。

### 3.4 world_rename 最小分母（配菜 1）

`world_rename_notices`（`pipeline.py:449-483`）：

- 模块级常数 `_MIN_WORLD_FILES = 5`；`denom = min(len(a_files), len(b_files))` 后
  `if denom < _MIN_WORLD_FILES: continue`（微小世界不评估，消灭 1 文件世界 100% 命中误报）；
- docstring 同步注明阈值来源（拍脑袋下限 + r14 语料可再标定，见 §7 妥协 6）；
- r13 夹具锚定回归：660 文件世界不受影响。

### 3.5 world_dirs glob 元字符转义（配菜 2）

`build_ruleset` 的 world 层注入（`cli.py:227-235`）：

- 新增 `escape_world_glob(name: str) -> str`：对 `\ * ? [ ] # !` 逐字符反斜杠转义
  （gitignore 语义：`[]` 字符类 / `*?` 通配 / 行首 `#` 注释、`!` 否定——wd 拼在
  match 行首，`#!` 必须处理；非特殊位置的转义 pathspec 容忍，全位置转义最简）；
- `Rule(match=f"{escape_world_glob(wd)}/**", ...)`；
- 现有非法名守卫（`/ \ . ..` 空名）保持；转义后不再产生新的非法路径
  （`world\[1\]/**` 合法 gitignore 模式，仅精确匹配 `world[1]/` 下文件）；
- `world_rename_notices` 提示行用原名（用户可读），不受转义影响。

### 3.6 client_only 警示行截断（配菜 3）

`run_diff` 警示行（`pipeline.py:708-711`）：

```python
shown = sorted(client)
parts = ", ".join(shown[:5]) + (f" …等 {len(shown)} 件" if len(shown) > 5 else "")
```

- 模块级常数 `_CO_MAX_SHOWN = 5`；仅影响 stderr 提示行，JSON 输出与
  `client_only_paths` 集合不变（GUI 消费不受影响）。

### 3.7 build_ruleset 搬迁 + _LATTICE tuple 化（整洁项）

**搬迁**：`cli.build_ruleset` 整体（函数+docstring+转义函数）移至 `pipeline.py`：

- 消除 `pipeline → cli` 延迟导入（`pipeline.py:235/637` 两处 `from .cli import build_ruleset`
  删除，改本模块直调）；依赖方向收敛为 `cli → pipeline` 单向；
- `cli.py` 留一行 re-export（`from .pipeline import build_ruleset`，注释「批次H 搬迁,
  历史位置保兼容」）——cli 顶层已 import pipeline，无循环；
- 测试引用（`tests/test_rules.py`、`tests/test_corpus_regression.py` 共 2 文件）改从
  `migration.pipeline` 导入；
- 纯搬迁零行为变化（diff 审查以「函数体逐字节等价」为准）。

**tuple 化**：`_LATTICE: list[_Level]` → `tuple[_Level, ...]`（`moddb.py:696`），
一行类型注解变化，零行为。

### 3.8 README 双语 + 版本号（收尾）

- **README.zh-CN.md**：
  - 「快照」说明段（现 64 行附近）补：v2 起快照内嵌 mod 名册（scan 时顺带扫 jar，
    耗时约 +1~3 秒；旧 v1 快照完全兼容，重扫即可升级）；
  - 「优先级」图（104 行）末尾补 `> 世界目录(动态探测)`，并注明该层垫底
    （default never 仍可压过，世界内 `.bak` 不迁）；
- **README.en.md** 同步两点 + `Last synced: v0.11.0`；
- **版本号**：`pyproject.toml` + `migration/__init__.py` 双文件 0.11.0（`mcmig -V` 同步）。

---

## §4 夹具与测试资产

新夹具 `tests/fixtures/server_corpus/synth_v2/`（全部合成脱敏，game_root=
`C:\fixture\sanitized`，目录不可达——复放语义；不引用任何 observations 路径）：

| 夹具对 | 内容 | 锚定 |
|--------|------|------|
| `junction_pre/post` | 双侧 v2（含 mods 名册）；同 resolved_root；不同 scanned_at；src.mods={foo 1.0}，dst.mods={foo 2.0}；files 侧 foo jar 名不同 | **junction 修复黄金锚**：mod_pairs 含 foo upgrade（source=registry）；0.10.0 对照=0 对 |
| `replay_orphan` | 双侧 v2 不可达；dst.mods 缺 `bar`；src files 含 `config/bar.toml` | **复放保真锚**：config/bar.toml 落 never/orphan（0.10.0 落 candidate） |
| `frozen_semantics` | 双侧 v2 不可达 + server.properties 双侧 md5 异 | 降级提示行出现（「已使用快照内嵌 mod 名册」）；无 semantics note（字节比较） |
| `mixed_v1v2` | src=v1（无 mods 键）、dst=v2 | 行为与 v1+v1 逐字节一致（ctx=None 降级行） |

夹具生成：内联脚本写入 JSON（与批次G r13 夹具同法），v1 夹具由 v2 夹具删 `mods` 键
（并置 `snapshot_format: 1`）派生。十轮既有语料夹具（全 v1）天然充当「v1 路径零回归」
哨兵——本批**不改动任何既有夹具**。

---

## §5 验收标准

1. `SNAPSHOT_FORMAT == 2`；新 scan 产 v2 快照（含 mods 键）；v1 快照 load 正常
   （mods=[]），v2 快照 mods 损坏（非 list）降级 [] 不抛；
2. scan（真目录，tmp_path 夹具）产出的快照含正确名册（modid/version/jar_filename/
   embedded_in 五字段齐）；
3. 行为矩阵（§3.2）五行的输出与 0.10.0 对照全部符合：v1 组合逐字节一致、
   v2+junction 配对出现、v2+不可达孤儿/配对/client_only 存活；
4. `compute_mod_pairs` 函数签名与内部逻辑零改动（闸门在调用方）；
5. world_rename：1-2 文件同路径同尺寸世界无提示；≥5 文件 90% 命中有提示
   （r13 夹具 660 文件回归不变）；
6. world 名 `world[1]` 注入规则后仅匹配 `world[1]/` 下文件，`world1/` 不受影响；
7. client_only 警示行 ≤5 件全列、>5 件截断 + 「…等 N 件」；JSON 输出不变；
8. `build_ruleset` 从 `migration.pipeline` 可导入且行为等价；`migration.cli.build_ruleset`
   re-export 仍可用；ruff 无循环依赖告警；
9. `_LATTICE` 类型为 tuple；五级配对既有测试全绿（零语义变化）；
10. 全量测试绿（预期 476 → ~514），ruff 0 违例；`mcmig -V` = 0.11.0；
    README 双语含世界层图与 v2 说明。

---

## §6 任务分解（SDD 草案）

| 任务 | 内容 | 测试增量 |
|------|------|---------|
| T1 | 夹具：synth_v2 四对（junction/replay_orphan/frozen_semantics/mixed_v1v2）内联脚本 + 落盘 | 0（资产） |
| T2 | snapshot v2：mods 字段/save/load/v1 兼容/容错 + moddb 转换函数 + scanner 接线 | ~+10 |
| T3 | 消费策略 B：DiffContext.mods_frozen/resolve_diff_context/run_diff 提示行/build_plan 接线 + 三个黄金锚（junction 修复/复放保真/v1 零回归） | ~+12 |
| T4 | 守卫直测（§3.3）+ world_rename 门槛（§3.4）+ 警示行截断（§3.6） | ~+8 |
| T5 | glob 转义：escape_world_glob + 注入改造（§3.5） | ~+6 |
| T6 | build_ruleset 搬迁 pipeline + _LATTICE tuple 化（§3.7，纯重构） | ~+2 |
| T7 | README 双语 + 版本 0.11.0 双文件 + 收尾全量回归 | 0 |

依赖：T2 → T3；T1 → T3；T4/T5/T6 相互独立（T5 转义函数随 build_ruleset 在 T6 一起搬迁）。

## §7 妥协与边界

1. **scan 耗时 +1~3 秒**（119 jar 开 zip 读名册）：一次性成本换复放保真；strict 模式同；
2. **mods 元素级损坏静默跳过**（与 world_dirs 容错同哲学）：名册是增益层，
   不以它阻断 diff；结构级损坏（非 list）降级 []；
3. **混合 v1/v2 不走嵌入通道**（保时刻对称）：单侧嵌入不单用；升级路径=重扫旧侧；
4. **语义复核仍需活体**：嵌入只有名册无文件内容，不可达退字节比较（frozen_semantics
   夹具锚定该边界）；
5. **read_file 的 same_dir 短路保持**：物理同目录读数恒等是内容层事实，与名册来源无关；
6. **_MIN_WORLD_FILES=5 为拍脑袋下限**：r14（赛后回切）语料可再标定，留校准点；
7. **scan→diff 间 mods 变化**：v2 用 scan 时刻名册（与 files 清单一致，语义更对），
   极端场景（scan 后立刻换 mod 再 diff）以快照为准——与「快照即事实」的既有哲学一致；
8. **cli.build_ruleset re-export 保留**：外部/GUI 若有引用不断裂，主位置归 pipeline。
