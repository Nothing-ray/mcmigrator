# v1 Phase 1.7 设计规格：Mod 感知（孤儿 config 识别 + .bak 假阳性根治 + 版本兼容检查）

> 状态：设计定稿，待写实现计划。
> 前置：v0（scan/diff 只读）、v1 Phase 1（plan 只读）、v1 Phase 1.5（2D 模型 + .bak 跟随 + rebuild 层）已完成。
> 动机：228→233 迁移观测（`Reference/observations/228-to-233/`）暴露三个根因——41% config 是已删 mod 的孤儿数据、.bak 判定法 ~10% 假阳性、白名单系统性误判。本 spec 通过读取 `mods/*.jar` 的 `META-INF/neoforge.mods.toml` 建立 mod 注册表，从根源解决。

## 0. 目标与边界

### 0.1 观测暴露的三个问题

| # | 问题 | 现状（坏） | 后果 |
|---|---|---|---|
| 1 | **41% config 是孤儿** | 228 的 198 个 config 中 82 个来自已删 mod（Xaero/Jade/JEI/Sodium/Iris…），工具无感知 | 白名单/默认规则把它们当活 config 处理 → 近半误判 |
| 2 | **.bak 假阳性 ~10%** | royalvariations 首启自动生成 .bak（内容=默认值）；acceleratedrendering 是孤儿 .bak | 假阳性 .bak → 误标为"玩家改过" → 迁移无用 config |
| 3 | **白名单系统性误判** | 白名单中 jade/jei/iris 条目全是孤儿（mod 已删），仍标 must_migrate | 工具主动迁死数据 |
| 4 | **mods 整包替换危险** | 玩家手动把 233 mods 覆盖到 228 → cp_lib 要求 NF≥233，228 是 228 → crash | 工具不检查 mod 版本兼容性 |

### 0.2 范围

- **改**：新增 `moddb.py` 模块（jar 解析 + config→modid 映射 + orphan 规则生成 + 版本兼容检查）；`plan.py`（+Origin.ORPHAN）；`planner.py`（`has_bak_sibling`→`find_bak_siblings` + MD5 内容比对）；`rules.py`（+Category.ORPHAN）；`differ.py`（+ORPHAN 路由）；`reporter.py`（+orphan 分组 + 兼容警告）；`cli.py`（scan_mods + orphan 规则插入 + 兼容检查接线）；`snapshot.py`（TOOL_VERSION bump）；新增数据 `data/mod_config_map.yaml`；README/文档补充分类说明。
- **不动**：扫描层（scanner/hashing/snapshot schema）、对比层上游逻辑（classifier 不变，differ 仅加 ORPHAN 一支）、`FileEntry`、`SNAPSHOT_FORMAT`、`PLAN_FORMAT`（保持 2）。
- **不在本 spec**（明确边界，见 §10）：Executor 写盘、Manifest、orphan 在 diff/scan 生效、不兼容 mod 改为 ASK、NBT、自动回滚。

### 0.3 核心哲学

**orphan 是事实，不是策略**。mod 物理上不在 dst 的 mods/ 目录里 → config 无人认领 → 迁了也没用。这不同于 rebuild（"让目标重建"的政策决定）。事实压过内置默认，但不压过用户显式意图（P2：用户主权）。

## 1. 架构总览

### 1.1 新增模块 `moddb.py`

| 组件 | 职责 |
|------|------|
| `ModInfo` | dataclass：modid、version、jar_filename、neoforge_range |
| `ModRegistry` | `dict[modid, ModInfo]` + 大小写不敏感查找 |
| `scan_mods(version_dir)` | 遍历 `mods/*.jar`，读 `META-INF/neoforge.mods.toml`（fallback `mods.toml`），解析 modid/version/neoforge 依赖 |
| `extract_modid_candidate(path)` | 纯文件名约定提取 modid 候选（不查注册表） |
| `map_config_to_mod(path, dst_mods, override)` | config→modid 映射（约定+下划线回退+覆盖表），返回 `(modid, is_orphan)` |
| `generate_orphan_rules(src_entries, dst_mods, override)` | 对 src 的 config 文件生成 orphan 规则列表 |
| `read_neoforge_version(version_dir)` | 从版本 json 读 `--fml.neoforgeVersion` |
| `check_version_range(version, range_str)` | Maven 版本范围解析与检查 |
| `check_mod_compat(mod_added_paths, src_mods, dst_nf_version)` | 对 mod_added 检查 NeoForge 兼容性 |

### 1.2 管道变化（plan 命令）

```
1. load src/dst snapshots（不变）
2. scan_mods(src_dir) → src_mods     ← 新增
3. scan_mods(dst_dir) → dst_mods     ← 新增
4. generate_orphan_rules(src_files, dst_mods, override) → orphan_rules  ← 新增
5. build_ruleset(cli > extra > user > orphan > rebuild > whitelist > default)  ← orphan 层插入
6. classify + diff（differ +ORPHAN→never 桶）
7. plan（planner +.bak MD5 比对）
8. check_mod_compat(mod_added, src_mods, dst_nf_version) → warnings  ← 新增
9. render（reporter +orphan 分组 +兼容警告段）+ save
```

### 1.3 不变的部分

- **Scanner / Hashing / Snapshot**：文件清单和 MD5 完全不变；`.bak` 文件已有 MD5（`.toml.bak` 不在 bulk 排除列表）
- **Classifier**：仍是 `RuleSet.classify(path)→Category`，只是 RuleSet 多了动态 orphan 规则
- **Differ 核心**：仅加一支 ORPHAN 路由（同 REBUILD 模式）
- **PLAN_FORMAT**：保持 2（orphan 只是新 Origin 枚举成员 + 新 Category，schema 不变）

## 2. Config→Modid 映射

### 2.1 约定（自动，覆盖 95%+）

NeoForge mod 的 config 命名约定：`config/<modid>*`（顶层）或 `config/<modid>/*`（子目录）。

```python
def extract_modid_candidate(path: str) -> str | None:
    """从 config 路径提取 modid 候选(纯文件名约定,不查注册表)。

    - 子目录: config/jade/foo.json → "jade"
    - 顶层文件: config/create-client.toml → "create" (第一个 "-" 前)
    - 无 "-": config/DistantHorizons.toml → "distanthorizons"
    - 核心配置: config/fml.toml → None (rebuild 管辖)
    - 含空格: config/Vital Herbs Config.toml → None (非合法 modid)
    - .bak 文件: → None (planner Pass 2 管辖)
    """
```

**规则**：
1. 仅处理 `config/` 前缀；非 config → None
2. `.bak` 结尾 → None（.bak 由 planner Pass 2 继承父命运）
3. 子目录 → candidate = 目录名（小写）
4. 顶层文件 → 剥扩展名 → 取第一个 `-` 前的部分（小写）
5. 核心配置（`fml`/`neoforge`/`minecraft`）→ None（rebuild 规则管辖）
6. 合法性检查：`^[a-z][a-z0-9_]*$`，含空格/非字母数字开头 → None（无法确定，保守）

> **已知限制（hyphen）**：规则 6 的正则拒绝含 `-` 的候选。对子目录场景（如 `config/dragon-survival/`），目录名 `dragon-survival` 因含 `-` 被 regex 拒绝 → 返回 None（无法确定）→ orphan 规则不生成 → config 落到默认行为。该限制与 §2.2 的下划线回退平行。**当前缓解**：`mod_config_map.yaml` 显式覆盖表把 `config/dragon-survival/**` 映射到真实 modid `dragonsurvival`。未来若多个 hyphenated modid 出现,可考虑在 `map_config_to_mod` 加 `candidate.replace("-", "_")` 重试分支。

### 2.2 下划线回退

NeoForge 配置变体后缀有 `-client`/`_client` 两种写法。当候选含 `_` 且不在 dst_mods 时，尝试 rsplit `_` 取前缀：

| 候选 | dst_mods 有? | 回退尝试 | 结果 |
|------|-------------|---------|------|
| `tide_client` | 否 | `tide` → 在 dst | 非孤儿（属于 `tide` mod） |
| `ars_nouveau` | 否 | `ars` → 也不在 | 孤儿（两部分都不在 dst） |
| `create_bitterballen` | 在 | — | 非孤儿（精确匹配） |

**已知限制**：若 `tide_client` 本身是真实 modid（不同于 `tide`）且不在 dst，但 `tide` 在 dst → 漏判（应为孤儿但标为非孤儿）。此为保守方向（多迁一个无用 config，不漏迁有用 config）。NeoForge modid 极少以 `_client` 结尾（`_client` 是配置变体后缀而非 modid 组成），现实中此情况极罕见。

### 2.3 覆盖表（`migration/data/mod_config_map.yaml`）

仅处理目录名/文件名 ≠ modid 的例外。实测 16 个被删 mod 中仅 Xaero 需要（目录 `xaero` ≠ modid `xaerominimap`）：

```yaml
# 非约定式 config→modid 映射覆盖表
# 约定: config/<modid>* 或 config/<modid>/* → modid (自动匹配)
# 此表仅列不遵循约定的例外
version: 1
mappings:
  - match: "config/xaero/**"
    modid: "xaerominimap"
    reason: "目录名 xaero ≠ modid xaerominimap"
  - match: "config/xaerohud.txt"
    modid: "xaerominimap"
    reason: "文件名无 modid 前缀特征"
  - match: "config/xaeropatreon.txt"
    modid: "xaerominimap"
    reason: "同上"
```

### 2.4 映射完整算法

```python
def map_config_to_mod(
    path: str, dst_mods: ModRegistry, override: OverrideTable
) -> tuple[str | None, bool]:
    """返回 (modid, is_orphan)。

    modid = 识别到的 modid(或 None = 无法确定)
    is_orphan = True 表示 mod 未安装在 dst
    """
    # 1. 覆盖表优先(路径精确匹配)
    mapped = override.lookup(path)
    if mapped is not None:
        return mapped, mapped not in dst_mods

    # 2. 约定提取
    candidate = extract_modid_candidate(path)
    if candidate is None:
        return None, False  # 无法确定 → 保守,不标 orphan

    # 3. 精确匹配 dst_mods(大小写不敏感)
    if candidate in dst_mods:
        return candidate, False

    # 4. 下划线回退
    if "_" in candidate:
        prefix = candidate.rsplit("_", 1)[0]
        if prefix in dst_mods:
            return prefix, False

    # 5. 不在 dst → orphan
    return candidate, True
```

## 3. Orphan 规则层

### 3.1 生成流程

```
1. scan_mods(dst) → dst_mods: ModRegistry
2. 对 src 快照中每个文件:
   a. 非 config/ 前缀 → 跳过
   b. .bak 文件 → 跳过(planner Pass 2 继承父命运)
   c. map_config_to_mod(path, dst_mods, override) → (modid, is_orphan)
   d. modid 为 None → 跳过(保守,无法确定)
   e. is_orphan=True → 生成 Rule(match=path, decide=ORPHAN, source="orphan")
3. 返回 orphan_rules: list[Rule]
```

### 3.2 优先级栈

```
命令        层栈(高→低)
scan/diff   cli > extra > user > REBUILD > default                    (不变)
plan        cli > extra > user > ORPHAN > REBUILD > whitelist > default (orphan 同 whitelist 一样 plan-only)
```

- **orphan 压过白名单**：白名单中 jade/jei/iris 条目全是孤儿，orphan 规则精确路径先命中 → 跳过
- **orphan 压过 rebuild**：mod 都没了，config 是版本敏感还是默认值已无意义
- **orphan 让位于 user/cli**：玩家显式写 `rules.yaml: config/jade/** → must_migrate` 或 `--include` → 用户主权

### 3.3 .bak 文件不生成 orphan 规则

.bak 走 planner Pass 2 继承父命运：
- 父 config 被 orphan 规则 → never 桶(note=orphan) → SKIP(origin=orphan) → .bak 继承 SKIP
- 父 config 未被 orphan → candidate 桶 → 正常 .bak 逻辑（§4）

### 3.4 优先级验证（P2 用户主权）

| 场景 | 谁先命中 | 结果 |
|------|---------|------|
| `--include config/jade/**` | CLI 规则 | 玩家显式 → 迁移（orphan 让路） |
| `rules.yaml: config/jade/** → must_migrate` | user 规则 | 玩家显式 → 迁移 |
| `whitelist.yaml: config/jade/**`（内置） | ORPHAN 规则 | 孤儿 → 跳过 |
| 无任何规则 | default/UNKNOWN | 走 candidate → planner 判定 |

### 3.5 表示：新增 `Category.ORPHAN`

- `rules.py`：`Category` 枚举 +`ORPHAN = "orphan"`；`_DECIDE_MAP` 自动收录
- `differ.py`：桶路由加一支——
  ```python
  if cat == Category.ORPHAN:
      report.never.append(DiffItem(path, s, d, note="orphan"))  # 同桶,note 区分
  ```
- `planner.py` `_for_never`：按 `item.note` 定 origin——`"orphan"`→origin=orphan；`"rebuild"`→origin=rebuild；`"never"`→origin=never；behavior 均 SKIP

## 4. .bak 内容比对（假阳性根治）

### 4.1 原理

NeoForge .bak 机制：玩家游戏内改 config → 保存新内容 + 生成 .bak 存旧内容。
- **config MD5 ≠ .bak MD5** → .bak 存的是改前的旧版本 → 玩家确实改过 → `config_modified` (COPY)
- **config MD5 == .bak MD5** → .bak 备份的跟当前一模一样 → 没人改过（mod 自动生成的噪音）→ `default_config` (SKIP)

### 4.2 `has_bak_sibling` → `find_bak_siblings`

旧函数返回 bool；新函数返回所有 .bak 兄弟路径列表（可能多个，如 create 有 -1 和 -2）：

```python
def find_bak_siblings(path: str, src_paths: set[str]) -> list[str]:
    """找到所有 .bak 兄弟文件路径(plain + versioned),可能为空。

    与 resolve_bak_parent 对偶:后者从 .bak 反推父,本函数从父找 .bak。
    """
    results: list[str] = []
    # plain: path + ".bak"
    plain = path + ".bak"
    if plain in src_paths:
        results.append(plain)
    # versioned: stem-[0-9]*suffix.bak
    dot = path.rfind(".")
    stem, suffix = (path[:dot], path[dot:]) if dot != -1 else (path, "")
    pattern = f"{stem}-[0-9]*{suffix}.bak"
    for p in src_paths:
        if fnmatch.fnmatch(p, pattern) and p not in results:
            results.append(p)
    return results
```

### 4.3 决策树变化（`_for_candidate`）

```python
bak_paths = find_bak_siblings(item.path, src_paths)
if bak_paths:
    parent_md5 = src_entry.md5
    bak_md5s = [src_index[p].md5 for p in bak_paths if p in src_index]
    has_md5 = parent_md5 is not None and all(m is not None for m in bak_md5s)
    if has_md5 and all(m == parent_md5 for m in bak_md5s):
        # 全部 .bak 内容 == config 内容 → 自动生成 → 降级
        → default_config (SKIP, reason=".bak content identical (auto-generated)")
    else:
        # 任一 .bak 内容 ≠ config 内容 → 玩家改过
        → config_modified (COPY)
else:
    → default_config (SKIP, reason="no .bak, not in whitelist")
```

**多 .bak 处理**：`all(m == parent_md5)` — 全部 .bak 与 config 相同才降级；任一不同 → 玩家改过。保守倾向"判为改过"。

**无 MD5（size-based 文件）**：`has_md5=False` → 走 `else` 分支 → config_modified（保守，与旧逻辑一致）。

### 4.4 Pass 2 自动适配

.bak 继承父命运（现有逻辑不变）：
- 父被降级为 `default_config` (SKIP) → .bak 继承 SKIP ✓
- 父保持 `config_modified` (COPY) → .bak 是 `bak_file` (COPY) ✓

### 4.5 验证（228 真实数据）

| .bak 案例 | parent MD5 vs .bak MD5 | 判定 | 修复 |
|-----------|------------------------|------|------|
| royalvariations-1.toml.bak | **相等**（首启自动生成） | default_config (SKIP) | ✅ 假阳性修复 |
| create-client-1.toml.bak | **不等**（玩家改过） | config_modified (COPY) | ✅ 保持 |
| create-client-2.toml.bak | **不等**（第二次改） | config_modified (COPY) | ✅ 多 .bak 正确 |
| acceleratedrendering-1.toml.bak | N/A（父被 orphan 规则拦截） | 继承 orphan (SKIP) | ✅ 由 mod 感知修复 |

**零例外表**，纯 MD5 驱动。

## 5. Mod 版本兼容检查

### 5.1 范围

仅检查 `mod_added`（玩家额外加的 mod）。整合包自带的 mod 是作者的责任。

### 5.2 ModInfo 字段

```python
@dataclass(frozen=True)
class ModInfo:
    modid: str
    version: str
    jar_filename: str
    neoforge_range: str | None  # mods.toml 中 [[dependencies.X]] modId="neoforge" 的 versionRange
```

### 5.3 jar 解析

```python
def scan_mods(version_dir: Path) -> ModRegistry:
    """遍历 mods/*.jar,读 META-INF/neoforge.mods.toml,解析 modid/version/deps。

    - 优先 neoforge.mods.toml,fallback mods.toml(旧 Forge 格式)
    - 一个 jar 可含多个 [[mods]] 条目(捆绑 mod)→ 全部解析
    - jar 无 mods.toml / 格式损坏 → 跳过(不崩溃,记 warning)
    - 用 tomllib(Python 3.11+ 标准库)解析 TOML
    """
```

### 5.4 版本范围解析（Maven 语义，简化版）

| 格式 | 含义 | 例子 |
|------|------|------|
| `[x,)` | ≥x | `[21.1.219,)` → 21.1.219+ |
| `[x,y)` | ≥x, <y | `[1.0.0,2.0)` → 1.x |
| `[x,y]` | ≥x, ≤y | `[1.0.0,2.0.0]` |
| `[x]` | =x | `[21.1.228]` |
| `(x,)` | >x | `(21.1.0,)` |

版本比较：按 `.` 分割为 int 元组，逐段比较（`(21,1,233) > (21,1,228)`）。

**边界处理**：
- mod 无 neoforge 依赖声明 → `neoforge_range=None` → 跳过检查
- 版本范围格式异常 → 跳过（不崩溃）
- 目标版本 json 读不到 NeoForge 版本 → 跳过检查

### 5.5 不改变 plan 数据

兼容检查是**报告层诊断**，不修改 ActionRecord：
- mod_added 仍是 COPY（不阻断迁移）
- `check_mod_compat()` 返回 `list[CompatWarning]`
- 警告在 reporter 单独渲染一段
- `MigrationPlan` 数据结构不变 → PLAN_FORMAT 不变

```python
@dataclass(frozen=True)
class CompatWarning:
    modid: str
    jar_filename: str
    mod_version: str
    required_range: str
    dst_neoforge: str
```

## 6. Origin / Category / Reporter / 文档

### 6.1 新增 `Category.ORPHAN`

```python
class Category(Enum):
    NEVER = "never"
    MUST_MIGRATE = "must_migrate"
    REBUILD = "rebuild"
    ORPHAN = "orphan"       # ← 新增
    UNKNOWN = "unknown"
    ASK = "ask"
```

### 6.2 新增 `Origin.ORPHAN`

```python
class Origin(str, Enum):
    ...
    ORPHAN = "orphan"       # ← 新增

# 注册表播种
"orphan": OriginSpec("👻 孤儿数据", False, False, Behavior.SKIP),
# default_visible=False:41% 的 config 是孤儿,默认隐藏避免噪音
# summary 行仍显示计数
```

### 6.3 PLAN_FORMAT 不变（保持 2）

- orphan 只是 Origin 枚举新成员（str-Enum 原生序列化为 `"orphan"` 字符串）
- 旧 plan（无 orphan action）加载正常
- 新 plan（有 orphan action）需新代码解析（`Origin("orphan")`）
- `TOOL_VERSION` bump 0.3.0 → 0.4.0；`SNAPSHOT_FORMAT` 不动

### 6.4 Reporter 变化

| 改动 | 内容 |
|------|------|
| ORIGIN_META | +orphan 条目 |
| summary 行 | +`👻孤儿82` 计数 |
| `--category orphan` | 过滤显示孤儿组 |
| orphan 组首次出现 | 注脚：`对应的 mod 未安装在目标版本中,迁移无意义。如需强制迁移,请在 rules.yaml 中显式指定。` |
| 兼容警告段 | mod_added 中不兼容 NeoForge 的 mod 列表（如有） |

### 6.5 文档补充

1. **README.zh-CN.md / README.en.md 新增「分类系统」章节**：解释每个 origin 的含义、优先级栈、orphan 概念
2. **`mcmig plan --help`**：优先级说明简述
3. **README 路线图更新**：Mod 感知从"未来"提前到 v1 Phase 1.7（已完成观测驱动）

## 7. 测试策略

### 7.1 合成 jar fixture

用 `zipfile` 创建含 `META-INF/neoforge.mods.toml` 的 .jar，无需真实 mod：

```python
def make_fake_jar(tmp_path, filename, modid, version="1.0", nf_range="[21.1.0,)"):
    jar = tmp_path / "mods" / filename
    jar.parent.mkdir(parents=True, exist_ok=True)
    toml = f'''modLoader="javafml"
loaderVersion="[1,)"
[[mods]]
modId="{modid}"
version="{version}"
[[dependencies.{modid}]]
modId="neoforge"
type="required"
versionRange="{nf_range}"
'''
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", toml)
    return jar
```

### 7.2 测试矩阵

| 文件 | 新增内容 |
|------|---------|
| `test_moddb.py`（新） | jar 解析（单 mod/多 mod/无 toml/格式损坏）/ config→modid 映射（约定/下划线/覆盖表/核心排除/含空格）/ orphan 规则生成（mod 在 dst/不在 dst/无法确定）/ 版本范围解析（在范围内/外/边界/无 range/格式异常） |
| `test_planner.py` | `find_bak_siblings` 返回路径列表 / .bak MD5 相等→default_config / 不等→config_modified / 多 .bak 全等→降级 / 多 .bak 任一不等→保持 / 无 MD5→保守 / 孤儿父带 .bak→继承 SKIP |
| `test_rules.py` | Category.ORPHAN / orphan 规则优先级（user > orphan > rebuild > whitelist） |
| `test_differ.py` | ORPHAN→never 桶 note=orphan |
| `test_reporter.py` | orphan 分组 / summary 计数 / 注脚显示 / 兼容警告段 |
| `test_e2e.py` | 真实 228→233：82 孤儿 config 标记 / royalvariations .bak 降级 / acceleratedrendering .bak 继承 orphan / 版本兼容检查 |

### 7.3 边界条件清单

**MD5 比对**：
- 单字节差异 → MD5 完全不同 → 正确判为"改过"
- 多 .bak（create -1/-2）→ 全部与 config 比对
- 无 MD5（size-based）→ 保守走旧逻辑（有 .bak → config_modified）
- .bak 在 src_index 缺失 → 保守走旧逻辑

**config→modid 映射**：
- 名字相近（create vs createaddition）→ 第一个 `-` 切分，精确匹配
- 下划线歧义（tide_client）→ 先试完整再试 rsplit → 已知保守限制（§2.2）
- 核心配置（fml/neoforge/minecraft）→ 硬排除
- 含空格/非标准 → 返回 None（保守不判）
- 大小写差异（CSC vs csc）→ 小写化匹配

**jar 解析**：
- 无 neoforge.mods.toml → fallback mods.toml → 都没有 → 跳过
- 多 [[mods]] → 全部解析
- TOML 格式损坏 → 跳过（不崩溃）
- jar 文件损坏/无法读取 → 跳过（不崩溃）

**版本范围**：
- 无 neoforge 依赖 → 跳过检查
- 格式异常 → 跳过（不崩溃）
- dst 版本 json 缺失 → 跳过检查

## 8. 项目结构 + 依赖

```
migration/
├── moddb.py               # 新增:jar 解析 + config→modid + orphan 规则 + 版本兼容
├── plan.py                # +Origin.ORPHAN +OriginSpec 播种
├── planner.py             # has_bak_sibling→find_bak_siblings + .bak MD5 比对
├── rules.py               # +Category.ORPHAN
├── differ.py              # +ORPHAN→never 桶(note=orphan)
├── reporter.py            # +orphan 分组 + 注脚 + 兼容警告段
├── cli.py                 # scan_mods + orphan 规则插入 + 版本兼容检查接线
├── snapshot.py            # TOOL_VERSION 0.3.0→0.4.0
└── data/
    ├── mod_config_map.yaml  # 新增(覆盖表)
    ├── rebuild.yaml         # 不变
    ├── whitelist.yaml       # 不变(orphan 规则覆盖白名单中的孤儿条目)
    └── default_rules.yaml   # 不变
```

- **依赖不变**（rich/PyYAML/pathspec 标准库 + `tomllib` Python 3.11+ 内置 + `zipfile` 标准库）
- **版本**：`pyproject.toml` 0.3.0 → 0.4.0；`TOOL_VERSION` 同步

## 9. 加载器与作用域

- 新增 `moddb.scan_mods(version_dir)` 在 plan 命令中调用（读实际版本目录的 mods/）
- `generate_orphan_rules()` 返回 `list[Rule]`，在 `cli.build_ruleset()` 中插入（plan-only，同 whitelist）
- `load_mod_config_map()` 用 `importlib.resources` 读取打包的 `data/mod_config_map.yaml`（PyInstaller 安全）
- orphan 规则为**精确路径**规则（`match="<exact_path>"`），不使用通配符，避免误匹配

```python
# cli.build_ruleset 改造
def build_ruleset(versions, args, mcmig_dir, *, with_whitelist=False, orphan_rules=None):
    ...
    rs = rules.RuleSet.from_layers(
        cli_rules, extra, user,
        orphan_rules or [],      # ← 新增(plan-only)
        rebuild, whitelist, default,
    )
```

| 命令 | 层栈（高→低） |
|---|---|
| scan / diff | cli > extra > user > REBUILD > default |
| plan | cli > extra > user > **ORPHAN** > REBUILD > whitelist > default |

## 10. 不在本 spec 范围（明确边界）

| 项 | 后续 |
|---|---|
| Executor 写盘（copy/覆盖/备份） | v1 Phase 2（消费 Behavior） |
| Manifest 决策沉淀 | v1 Phase 3 |
| orphan 在 diff/scan 生效 | 后续（当前 plan-only） |
| 不兼容 mod 改为 ASK | 后续（当前仅警告，未来可加 `mod_added_incompatible` origin） |
| 启动器活跃版本同步（PCL.ini） | v1 Phase 2/3 |
| 启动失败检测 / 自动回滚 | 需 Executor |
| NBT 解析（saves/dragon-survival） | 后续 |
| orphan 规则通配符化 | 后续（当前精确路径，82 条对规则引擎无压力） |

## 验收标准（本 spec 完成判定）

1. 真实 `mcmig plan 1.21.1-NeoForge_21.1.228 1.21.1-NeoForge_21.1.233` 产出：
   - 82 个孤儿 config 落在 `orphan` origin（SKIP）
   - royalvariations .bak 降级为 `default_config`（MD5 相等）
   - acceleratedrendering .bak 继承父 `orphan`（SKIP）
   - 白名单中的 jade/jei/iris 条目被 orphan 覆盖（不再 must_migrate）
2. `rules.yaml: config/jade/** → must_migrate` 时，jade config 落 `must_migrate`（用户压过 orphan）
3. `mcmig scan`/`diff` **零回归**：`SNAPSHOT_FORMAT` 不变，已 scan 快照可读；orphan 不在 scan/diff 生效
4. reporter 按 origin 分组，`--category orphan` 过滤生效；summary 行含 `👻孤儿` 计数；orphan 组有注脚
5. mod_added 中不兼容 NeoForge 的 mod 出现在兼容警告段
6. 对游戏目录**零写入**（plan 纯只读）
7. 全量单元 + e2e 通过；`ruff check .` 干净
8. P2 验证：user `rules.yaml` 写 `config/jade/** → must_migrate` 时，jade 落 `must_migrate`；不写时落 `orphan`

## 决策摘要（给快速回顾）

> **moddb 新模块**读 `mods/*.jar` 的 `META-INF/neoforge.mods.toml` → 建 `ModRegistry`(modid/version/neoforge_range)。**config→modid 映射**用约定(目录名/文件名第一个 `-` 前)+下划线回退+覆盖表(xaero→xaerominimap)。**orphan 规则层**(动态生成,精确路径,plan-only)插在 `user > ORPHAN > REBUILD > whitelist` 优先级——事实压过内置默认,让位用户显式。**Category.ORPHAN + Origin.ORPHAN**(SKIP,👻,默认隐藏)。**.bak 假阳性根治**用 MD5 内容比对(`find_bak_siblings` 返回全部,全等→降级 default_config,任一不等→保持 config_modified)——零例外表。**版本兼容检查**对 mod_added 读 neoforge_range 比对 dst 版本,仅警告不改 plan 数据。`PLAN_FORMAT` 保持 2,`TOOL_VERSION` 0.3.0→0.4.0。README 补分类说明章节。
