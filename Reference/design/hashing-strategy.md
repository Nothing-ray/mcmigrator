# 哈希策略设计（Hashing Strategy）

> 用途：v0 scan/diff 工具的哈希策略设计思路备忘，便于后续迭代时反查。
> 每条决策都带「为什么」，呼应 AGENTS.md 的 `reason` 习惯。
> 配套：见 AGENTS.md「设计思路与架构」节。

---

## 1. 背景与问题

v0 的 `scan`/`diff` 需要检测**两个版本文件夹之间**哪些文件被「修改」，从而识别玩家在新版本里产生/改动了什么。

数据集特征（实测 227/228）：
- 总量约 **380MB**：`mods/` 119 个 jar 共 **354MB**、`Distant_Horizons_server_data/*.sqlite` **26MB**、其余为小文本（`config/` 182 文件 ~0.5MB、`options.txt`、`servers.dat`、`xaero/`、`kubejs/` 等）
- **玩家改动集中在小文本**（config/options/脚本）；bulk 二进制（mods jar、sqlite、zip 缓存）是「整体替换型」，按字节 diff 毫无信号

**核心矛盾**：全量哈希最精确但把 93% 算力浪费在 mods 上换 0 信号；只看 size 又会在文本上漏检。需要按文件语义分层。

---

## 2. 两条核心判断

### 判断一：精确性需求不均匀
- 玩家会改的（config/options/dat/脚本）→ 需要**字节级精确**
- 玩家不会改的（mods jar / sqlite / zip 缓存）→ 只需知道「在不在」「换没换版本」→ **size 或文件名集合即可**

### 判断二：mtime 是陷阱（对本工具致命）
- 迁移的本质就是**复制文件**，而复制会重置/改变 `mtime`
- 若用 `size + mtime` 判修改，复制过去的文件会**全部看起来被改过** → diff 全乱
- **结论**：`mtime` 只能当**缓存失效键**（见第 11 节），**绝不能当 diff 信号**

---

## 3. 策略全谱对比（性能 vs 精确性）

| # | 策略 | 做法 | 性能(380MB 集) | 精确性 | 对本工具评价 |
|---|---|---|---|---|---|
| 1 | 全量 MD5/SHA | 每文件从头读到尾 | 慢 ~2-4s | 字节级 100% | 浪费：93% 算力花在 mods 换 0 信号 |
| 2 | 仅 size | 只 stat | 极快 | 差 | 文本改一字常同 size → 漏检 |
| 3 | size + mtime | stat | 极快 | 中 | ⚠️ **不可用**：复制改 mtime，全乱 |
| 4 | **分层** | bulk 记 size、文本全 hash | 快 <0.1s | 文本精确/bulk 近似 | ✅ 匹配信号分布 |
| 5 | size 预筛再 hash | size 同才 hash 确认 | 中 | 100% | 不省：227/228 mods 字节相同仍要全 hash |
| 6 | 采样 hash | 首+尾各 N KB | 快 | 非字节精确 | 有漏检风险，不宜做唯一依据 |
| 7 | 快算法 xxhash/blake3 | 全量但用快 hash | 快 ~0.1-0.5s | 100% | 好，但加依赖；非对抗 MD5 已够 |
| 8 | hash 缓存 | (path,size,mtime)→hash 跨 run 复用 | 二次 run 极快 | 100%（mtime 仅 cache key） | 适合 v0.1 增强 |

---

## 4. mods 的特殊视角：按「文件名集合」而非字节 diff

跨版本时，mods 的有意义问题是 **「哪些 jar 增/删了」**，而不是「create.jar 的字节变没变」。
- 玩家不会编辑 jar 内部；版本变化 = 换文件名（换 mod 版本）
- 因此 mods 应按 **文件名集合** 比较（presence），连 size 都不必用于 diff（size 仅作信息记录）
- 同理 DH sqlite / xaero zip 缓存是「整体替换」型，**size 变化** = 改过，已足够准

---

## 5. 推荐分层策略（v0 采纳）

按文件**语义**分三档处理，而非简单按大小：

| 文件类型 | diff 依据 | 扫描时记录 | 理由（reason） |
|---|---|---|---|
| `mods/**/*.jar` | **文件名集合**(增/删) | path + size（不 hash） | 玩家不改 jar 内部；版本变 = 换文件名 |
| `*.sqlite`、`*.zip`（xaero/ftbchunks 缓存）、`saves/**/*.mca` | **size 变化** | path + size（不 hash） | 整体替换型；size 是 99% 好的代理 |
| 其余文本（config/options/kubejs/dat/saves 小文件） | **MD5 全量** | path + size + md5 | 这正是玩家会改的，要字节精确 |

---

## 6. 算法选择

- **v0 用标准库 `hashlib`（MD5）**：零依赖、非对抗场景足够（我们不做完整性校验/对抗，只做「同或不同」判断）
- 哈希碰撞风险对本用途可忽略（非密码学场景，MD5 碰撞需刻意构造）
- **若以后嫌慢再换 `xxhash`**——但因为跳过了 bulk，根本不会慢，所以 v0 不必引入额外依赖

---

## 7. Diff 规则与置信度标注

Diff 时按记录类型分别处理：

- 两边都有 `md5` → **比 md5**（字节精确）→ 命中记 `identical(verified)` / `modified`
- 任一边 `md5=null`（size-only）→ **比 size** → size 同记 `identical(size-based)`，size 异记 `modified`
- mods 按文件名集合：`new`(src 有 dst 无) / `deleted`(dst 有 src 无)

**报告必须标注置信度**：`identical(verified)` vs `identical(size-based)`，让用户清楚哪些没做字节验证。
> reason：透明优于假装精确；用户据此决定是否对某文件用 `--strict` 复核。

---

## 8. 性能预估

- 全量 MD5：380MB / ~300MB/s ≈ 1.3s（加 Python 开销实际 2-4s）
- 分层（只 hash ~5MB 文本）：~5MB / 300MB/s ≈ 0.02s + 约 400 文件的 stat 开销 → **<0.1s**
- **分层比全量快约 20-40×**（对本数据集）

---

## 9. `--strict` 逃生口

- 默认走分层策略（快）
- 提供 `--strict` 开关：强制**全量 hash 所有文件**，用于「我就要 100% 字节确认」或排查疑点
- reason：默认高性能、特殊场景给精确选项，两全

---

## 10. 边界情况

| 情况 | 分层策略表现 | 说明 |
|---|---|---|
| 同 size 不同内容的**文本**（如 `true`→`false` 等长替换） | ✅ 全量 MD5 抓得到 | 文本档走全量 hash |
| 同 size 不同字节的 **mod jar** | ⚠️ size 代理判 identical（假阴性） | 但 mods 按文件名集合比，「文件名没变=同一 mod」已覆盖；要较真用 `--strict` |
| `saves/` 巨大 `.mca` region 文件 | size 代理 | region 文件随游玩增长，size 变 = 改过；属 World-bound 类，整世界迁移，size 足够 |
| 文件被占用/无权限 | 跳过 + warn | 不崩溃，报告里标 `unreadable` |

---

## 11. 演进（v0.1+）：hash 缓存

- 加一层缓存：`(path, size, mtime) → md5`，跨 run 复用
- 二次扫描时，若 (path,size,mtime) 未变 → 直接复用旧 md5，跳过读文件
- **安全性**：这里 mtime 只作**缓存失效键**（命中失效最坏情况 = 重新 hash，仍正确），不参与 diff 判定，规避了第 2 节的陷阱
- 收益：第二次起扫描近瞬时；适合「改分类规则反复 diff」的试验循环

---

## 决策摘要（给快速回顾）

> **一句话**：文本全量 MD5、mods 按文件名集合、bulk(sqlite/zip/mca) 按 size；mtime 永不作 diff 信号；`--strict` 兜底字节精确；v0.1 加 hash 缓存。
