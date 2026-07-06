# AGENTS.md - MC 客户端版本迁移工具 项目规范

## 项目概述
本项目为 **龙窝社区「冒险活动客户端 v1.4」整合包**开发**版本迁移工具**。
该整合包基于 **Minecraft 1.21.1 + NeoForge**，通过 **PCL2 启动器**以**版本隔离**方式运行：
每个 `versions\<版本名>\` 文件夹是一个**完整独立的游戏实例**（自带 mods / config / saves 等）。

当服务端推送新 NeoForge 版本（如 `21.1.227` → `21.1.228`）后，新版本文件夹内只有整合包默认状态，
玩家需把**旧版本文件夹中的个性化状态**（存档、按键绑定、路径点、自定义脚本与 mod、改动过的 config）迁移到新版本文件夹，避免从零重来。

- 迁移范围: **同一整合包内** `versions/` 下不同版本文件夹之间互转（如 `1.21.1-NeoForge_21.1.227` ↔ `1.21.1-NeoForge_21.1.228`）
- 迁移对象: **用户内容**（玩家进度与自定义），不是整合包本身或 NeoForge 二进制
- 当前状态: **无任何代码**，技术栈已定（Python）；工具代码将放在本工作目录下新建的 `migration/` 子目录中
- 工作目录: `机械动力活动客户端v1.8\冒险活动客户端v1.4\`（本文件所在）
- 真实游戏根目录: 同级的 `冒险活动客户端\`（不是工作目录本身）

## 语言与注释规范
- **所有代码注释、docstring 必须使用中文**
- **所有设计决策说明使用中文**
- 变量名、函数名、类名使用英文（遵循所选语言的命名规范）
- 公有函数必须有中文 docstring
- 技术栈定下后，在本节补充类型提示/风格等细则

## 技术栈
- **语言**: Python 3.11+（venv 虚拟环境）
- **v0 运行依赖**:
  - `rich` — 终端报告渲染（表格/分组/折叠）
  - `PyYAML` — 规则文件 `.mcmig/rules.yaml` 与 `default_rules.yaml` 解析
  - `pathspec` — 规则 glob 匹配（gitignore 语义）
  - 标准库：`hashlib`（MD5）/ `pathlib`（路径）/ `argparse`（CLI）/ `tomllib`（config 判定）
- **演进阶段依赖**:`nbtlib`(NBT 解析:saves/dragon-survival 预设)
- **打包**: PyInstaller → 单文件 exe（面向无 Python 环境的玩家，见「分发策略」）
- **项目配置**: `pyproject.toml`
- **测试/lint**: pytest / ruff（详见「构建与运行命令」）
- **选型理由**: 语义模型需 TOML/JSON/NBT 解析 + 规则引擎 + mod 元数据读取，Python 生态完备且跨平台；PowerShell 仅够傻瓜复制（迟早漏，见 `Reference/discussions/chat.md`）。v0 设计详见 `Reference/specs/`、子系统设计见 `Reference/design/`

## 客户端环境定义
以下均为**实测**事实（来自启动器配置与版本 json），是迁移工具的边界条件，勿臆测：

| 项 | 值 | 来源 |
|----|----|----|
| 启动器 | PCL2（Plain Craft Launcher 2） | `冒险活动客户端\Plain Craft Launcher 2.exe` |
| Minecraft 版本 | 1.21.1 | 版本 json `--fml.mcVersion` |
| Mod 加载器 | NeoForge 21.1.228（当前活跃）/ 21.1.227（旧） | `PCL.ini` `Version:` 字段 |
| FML 版本 | 4.0.42 | 版本 json `--fml.fmlVersion` |
| NeoForm | 20240808.144430 | 版本 json `--fml.neoFormVersion` |
| Java | JDK 21（`C:\Program Files\Java\jdk-21.0.10`） | `PCL\LatestLaunch.bat` |
| 资源索引 | 17（assetIndex id） | 版本 json `assetIndex` |
| 版本隔离 | **开启**（`LaunchArgumentIndieV2:4`） | `冒险活动客户端\PCL\Setup.ini` |
| 活跃版本追踪 | `PCL.ini` 的 `Version:` + `PCL\Setup.ini` 的 `LaunchVersionSelect:` | 两处需同步 |
| 现有版本文件夹 | `1.21.1-NeoForge_21.1.227`、`1.21.1-NeoForge_21.1.228` | `冒险活动客户端\versions\` |

## 客户端目录结构
```
冒险活动客户端v1.4/                     # ← 工作目录（工具代码放这里的新子目录）
├── AGENTS.md                           # 本文件
└── Reference/                          # ★ 参考资料（按类型分目录，见 Reference/README.md）
    ├── README.md                       # 分类规则说明
    ├── discussions/                    # 工具设计原始讨论（chat.md）
    ├── design/                         # 工具子系统设计备忘（hashing-strategy.md / classifier-rules.md）
    ├── specs/                          # 工具版本设计规格（YYYY-MM-DD-*-design.md）
    ├── guide.md                        # 整合包新手手册（老版本·127mod·龙之生存主题）
    ├── linkage.md                      # mod 联动关系汇总
    └── research/                       # 131 个 mod 用途/联动调研（老版本，供来源反查）
└── 冒险活动客户端/                      # ★ 真实游戏根目录（.minecraft 等价物）
    ├── Plain Craft Launcher 2.exe      # PCL2 启动器
    ├── launcher_profiles.json          # 启动器档案（含账户/选中档案）
    ├── PCL.ini                         # ★ 活跃版本字段: Version:1.21.1-NeoForge_21.1.228
    ├── assets/                         # ★ 共享：资源（indexes/objects/skins），勿迁移
    ├── libraries/                      # ★ 共享：Java 库，勿迁移
    └── versions/                       # ★ 版本隔离：每个子文件夹=一个完整实例
        ├── 1.21.1-NeoForge_21.1.227/   # 玩家原始状态（迁移「源」）
        ├── 1.21.1-NeoForge_21.1.228/   # 迁移中（含较多玩家运行时数据）
        ├── 1.21.1-NeoForge_21.1.229/   # ★ 全新空壳（迁移「目标」形态参考：仅 mods/PCL/resourcepacks）
        └── DistantHorizons-*.jar       # 版本目录外散落的 jar（勿归入迁移）
```

### 版本文件夹内部结构（迁移的真正目标）
每个 `versions\<版本名>\` 是自包含实例，实测包含：
```
<版本名>/
├── <版本名>.jar                  # ❌ 不可迁：NeoForge 版本专属二进制
├── <版本名>.json                 # ❌ 不可迁：版本清单（含库/下载/启动参数）
├── <版本名>-natives/             # ❌ 不可迁：本地库（lwjgl 等）
├── mods/                         # ◐ 按需迁：玩家额外添加的 mod（整合包自带的不必重复）
├── config/                       # ◐ 按需迁：仅迁玩家改过的（用 .bak 判定，见「迁移机制说明」）
├── defaultconfigs/               # ⛔ 不迁：mod 首次运行自动生成
├── kubejs/                       # ◐ 按需迁：玩家 KubeJS 脚本（模板可跳过）
├── saves/                        # ✅ 必迁：单机存档（多人服常为空）
├── options.txt                   # ✅ 必迁：按键绑定/视频/语言等设置
├── servers.dat / servers.dat_old # ✅ 必迁：服务器列表
├── schematics/                   # ✅ 必迁：玩家 schematic 文件
├── xaero/                        # ✅ 必迁：Xaero 小地图+世界地图缓存（按服务器地址分目录）
├── XaeroWaypoints_BACKUP240807/  # ✅ 必迁：Xaero 路径点备份
├── local/ftbchunks/              # ✅ 必迁：FTB Chunks 区块地图/占领/死亡点（按玩家 UUID 分目录）
├── Distant_Horizons_server_data/ # ✅ 必迁：Distant Horizons 远景 LOD（按服务器+世界分目录）
├── dragon-survival/              # ✅ 必迁：Dragon Survival 龙角色自定义预设（.nbt/.json）
├── resourcepacks/                # ◐ 按需迁：玩家自定义资源包
├── shaderpacks/                  # ◐ 按需迁：光影包（Iris 加载）
├── downloads/                    # ⛔ 不迁：临时下载缓存
├── logs/                         # ⛔ 不迁：日志
├── crash-reports/                # ⛔ 不迁：崩溃报告（仅旧版本可能存在）
├── patchouli_books/              # ⛔ 不迁：mod 生成
├── patchouli_data.json           # ⛔ 不迁：mod 生成
├── usercache.json                # ⛔ 不迁：缓存
├── observable_announce           # ⛔ 不迁：mod 运行产物
├── PCL/                          # ⛔ 不迁：版本内启动器缓存
└── command_history.txt           # ⛔ 不迁：命令历史
```
> 实测（227 vs 228 深度哈希对比）：两版 `mods/` 完全相同（各 119 个），`options.txt`/`servers.dat` 哈希一致；差异全是玩家运行时产物——`local/ftbchunks/`（含 `Death #1` 死亡点）、`Distant_Horizons_server_data/`、`dragon-survival/`、若干带 `.bak` 的 config。即玩家进度集中在多人服相关数据，`saves/` 为空。

## 迁移机制说明
迁移 = 从**源版本文件夹**（玩家原始状态，如 227）拷贝**用户内容**到**目标版本文件夹**（全新空壳，如 229 形态），目标文件夹的整合包默认内容保留或按策略合并。

### 场景特征（实测）
- **多人服客户端为主**：`saves/` 常为空，玩家进度主要沉淀在 `xaero/`、`local/ftbchunks/`、`Distant_Horizons_server_data/`、`dragon-survival/`，而非单机存档
- **目标形态 = 全新空壳**：参考 `1.21.1-NeoForge_21.1.229/`，仅含 `mods/`/`PCL/`/`resourcepacks/` + 版本二进制。目标**首次启动游戏后**，mod 会自动生成 `config/` 默认值——故 mod 默认 config 不需迁，启动重建即可

### 迁移内容分类
| 类别 | 含义 | 处理策略 |
|----|----|----|
| ✅ 必迁 | 玩家进度，丢失不可逆 | 直接覆盖到目标（目标一般无此内容或可安全覆盖） |
| ◐ 按需迁 | 玩家自定义，需与整合包默认内容区分 | 默认迁；对 `mods/`、`config/` 需做差集/合并（见下） |
| ◑ 版本/硬件敏感 | 由 Loader/驱动派生，跨版本迁移高危 | **默认不迁（让目标重建）**；仅同版本可考虑迁移。见下「高危文件」 |
| ⛔ 不可迁 | 版本专属二进制 / 临时产物 / mod 生成 | 绝不拷贝 |
| ★ 共享勿动 | 游戏根目录下共享资源 | 不在版本文件夹内，迁移不涉及 |

#### ⚠️ 高危文件（◑ 版本/硬件敏感，实测存在）
直接覆盖会 crash 或出错，**默认让目标重建**：
- `config/fml.toml`、`config/neoforge-client.toml`、`config/neoforge-common.toml` — Loader 版本绑定，NeoForge 升级后字段会变
- `config/sodium-fingerprint.json` — 含设备指纹
- `config/sodium-options.json`、`config/iris-excluded.json` — 可能记录 GPU/驱动信息

### mods 与 config 合并策略
- **`mods/`**：目标已有的整合包 mod 不动；仅补齐**源有、目标无**的玩家额外 mod（取并集，去重按文件名）
- **`config/`**：整合包版本升级可能调整默认配置；**玩家改过的**应迁，**未改动的整合包默认**应让目标的新默认生效。判定规则如下：

#### 🔑 config 玩家改动判定法（实测可靠）
NeoForge 配置在玩家游戏内改动时，会自动生成 `.bak` 备份（命名 `xxx-N.toml.bak`，`N` 为备份版本号）。因此：
- **存在同名 `.bak`** → 该 config 被玩家改过 → **应迁**（源覆盖目标默认）
- **无 `.bak`** → mod 首次运行生成的默认值 → **不迁**（让目标新默认生效）

> 实测 227 有 **18 个 `.bak`**：`create`×2、`farmersdelight`×2、`kaleidoscope_compat`×2、`dragonsurvival`、`cataclysm`、`flywheel`、`acceleratedrendering`、`createaddition`、`createfood`、`create_connected`、`create_food_filling`、`enigmaticlegacyplus`、`royalvariations`、`ali/ali_common.json`、`logistics-network/client.toml。另有部分客户端偏好文件**无 .bak 但属玩家设置**（如 `iris.properties`、`config/jade/*.json`、`config/jei/*sort-order*`、`ftbchunks-client.snbt`），需用**白名单**补充。

### 冲突隔离机制
- **必迁类（✅）**：目标一般无 → 直接迁，零冲突
- **config 类（◑）**：目标首启后有 mod 默认值，与源（玩家改过）不同 = 冲突 → **源覆盖目标，目标默认先移入 `_conflict_backup/`**
- **资源包/光影（◑）**：目标同名 → 不覆盖，源文件重命名后放入或跳过
- **孤儿文件**：源里有、但目标已移除该 mod（版本演进删 mod，见「版本差异」）→ 识别后跳过或隔离，避免污染新版

### 版本差异提醒
`guide.md`/`research/` 针对的是**老版本（127 mod）**，当前实测 **119 mod**。迁移工具应内置「mod→数据目录」映射（见下），识别并跳过已移除 mod 的孤儿数据。

## 构建与运行命令
- 创建虚拟环境: `python -m venv .venv`
- 激活环境(Windows): `.venv\Scripts\Activate.ps1`
- 安装依赖: `pip install -r requirements.txt`（或 `pip install -e .` 配合 `pyproject.toml`）
- 扫描版本(生成/比对快照): `mcmig scan <版本名>`
- 执行迁移(源→目标): `mcmig migrate <源版本> <目标版本>`
- 干跑预览(不写盘): `mcmig migrate <源> <目标> --dry-run`
- 运行测试: `pytest tests/`
- 代码检查: `ruff check .`
- 类型检查(可选): `mypy migration/`
- 打包 exe: `pyinstaller --onefile migration/__main__.py`
- 发版(GitHub Actions): `git tag v0.1.0 && git push --tags` → 自动构建 Release（见「分发策略」）

## 编码规范
- **文件一律 UTF-8 无 BOM**（版本 json/options.txt 等均为 UTF-8）
- 文件路径一律用 `pathlib.Path`，**不拼接字符串**
- **绝不硬编码版本号**（如 `21.1.228`）；版本名从 `versions/` 目录枚举或 `PCL.ini` 读取
- 操作前**先备份**目标（或提供 `--dry-run` 预览），存档与 config 误覆盖不可逆
- 路径常含中文、空格、多层嵌套——所有文件 API 必须显式支持 Unicode
- **类型提示**必须标注在所有函数签名上；公有函数用 Google 风格中文 docstring
- 日志用 `logging` 模块，不用 `print`
- **迁移策略与规则放 `rules/`、`profiles/`（YAML），不硬编码进源码**；每条规则带 `reason` 字段（记录「为什么这样判」，便于半年后回溯）
- 测试文件与源码结构对应，放在 `tests/` 下；导入用绝对路径 `from migration.scanner import ...`

## Windows 路径与编码注意事项
- **路径含中文 + 空格 + 多层嵌套**（`H:\MC\龙窝客户端\机械动力活动客户端v1.8\冒险活动客户端v1.4\冒险活动客户端\versions\...`），全程统一 UTF-8
- PowerShell 处理含中文路径必须用 `-LiteralPath`，避免通配符误解析
- 关注 **Windows 长路径**（>260 字符）限制；必要时启用长路径支持或用 `\\?\` 前缀
- 读写 JSON/文本统一显式指定 UTF-8 编码，防止默认 ANSI/GBK 乱码
- `saves/` 单个存档可能很大且文件众多，拷贝注意耗时与磁盘空间

### 常见坑
| 坑 | 现象 | 原因 | 解决 |
|----|------|------|------|
| 启动器仍开旧版本 | 迁移后游戏里看不到新内容 | 未更新活跃版本指向 | 同步改 `PCL.ini` 的 `Version:` 与 `PCL\Setup.ini` 的 `LaunchVersionSelect:` |
| 误迁 NeoForge 二进制 | 新版本启动异常/库冲突 | 把 `<ver>.jar`/`-natives/` 也拷过去了 | 仅迁「用户内容」，二进制由整合包提供 |
| 配置全量覆盖 | 丢失新版本对整合包默认配置的修复 | `config/` 无脑覆盖 | 区分「玩家改动」与「整合包默认」，按策略合并 |
| 中文路径乱码 | 脚本找不到文件或路径异常 | 编码非 UTF-8 / 用了字符串拼接 | UTF-8 + Path 对象 / `-LiteralPath` |
| 存档损坏 | 进不去存档 | 迁移时游戏仍在运行、文件占用 | 迁移前确保游戏已退出 |

## 迁移对象来源映射（mod → 数据目录）
> 注：本表引用的 `research/`、`guide.md`、`linkage.md` 为**本地参考**（modpack 作者侧资料），不入本仓库；仅作来源反查依据。仓库内设计文档见 `Reference/`。

基于 `research/`（mod 用途调研）反推「数据目录 → 产生它的 mod」，用于**孤儿文件识别**（目标已删该 mod 时跳过）与分类校验：

| 数据目录 / 文件 | 来源 mod | 性质 | 来源依据 |
|----|----|----|----|
| `local/ftbchunks/` | FTB Chunks | ✅ 玩家区块地图/占领/死亡点 | `research/ftb_chunks.md` |
| `dragon-survival/` | Dragon Survival | ✅ 龙角色自定义预设 | `research/dragon_survival.md` |
| `Distant_Horizons_server_data/` | Distant Horizons | ✅ 服务器远景 LOD | mod 名直推 |
| `xaero/` + `XaeroWaypoints_*` | Xaero's Minimap/Worldmap | ✅ 小地图/路径点 | `guide.md` 工具表 |
| `shaderpacks/` + `iris.properties` | Iris | ◐ 光影包+设置 | `research/iris.md` |
| `config/jade/*` | Jade | ◐ 显示偏好 | `research/jade.md` |
| `config/jei/*sort-order*` | JEI | ◐ 配方排序/书签 | `research/jei.md` |
| `config/*` 带 `.bak` | 对应各 mod | ◐ 玩家改过的配置 | `.bak` 判定法 |
| `kubejs/` | KubeJS | ◐ 玩家脚本（模板可跳过） | `linkage.md` |

> `guide.md`/`linkage.md`/`research/` 为**老版本（127 mod）**资料，仅作 mod 用途反查；当前 119 mod，工具落地时以**实测 `versions/` 目录为准**。

## 设计思路与架构（源自 `Reference/discussions/chat.md`）
核心哲学：**建立「实例语义模型」**——每个文件都有身份/生命周期，而非傻瓜复制。但采用**渐进式 MVP 优先**，避免一上来过度设计。

### MVP 优先（v1 范围）
> 先解决自己的问题（"先 scratch your own itch"），不为社区做平台。

- **黑名单默认原则**：维护一份「**绝不迁移**」清单（`logs/`、`crash-reports/`、`*/cache/`、版本二进制、`libraries/`、`assets/` 等，见上文分类），**其余默认迁移**，首次遇到未知项时**逐项确认** → 决策沉淀进 `Manifest.json`
- **v1 只要 4 个模块**：
  | 模块 | 职责 |
  |----|----|
  | `Scanner` | 扫描版本目录，生成文件清单/快照 |
  | `Manifest` | 持久化迁移决策（path → action），逐渐长成「适合本整合包的知识库」 |
  | `Planner` | 旧版本 + 新版本 + Manifest → 迁移计划（new/modified/deleted/unknown） |
  | `Executor` | 执行 copy/覆盖/跳过/备份，支持 `--dry-run` |
- **v1 不上 GUI**：CLI 即可（`mcmig scan` / `mcmig migrate`），精力放规则正确性而非界面
- **未知文件 = 提示用户**（不自动迁、不自动删）；`.bak` 判定法 + research 映射可作为 Manifest 首次确认的**预填建议**

### 指导原则（贯穿各阶段）
- **保守默认（Conservative by Default）**：宁可漏迁一个配置，也不要因误迁导致实例无法启动
- **置信度机制**：每个文件给置信度（如 `servers.dat` 100%、玩家 config 95%、`neoforge-*.toml` 重建 90%、未知 20%），未知 → 人工确认
- **自动回滚**：迁移前打包备份目标；可选检测启动失败（ExitCode≠0）→ 自动恢复

### 五级文件生命周期分类（用于语义建模）
| 级别 | 含义 | 例 |
|----|----|----|
| Persistent 永久 | 几乎总应迁 | `options.txt`、`servers.dat`、`resourcepacks/`、`xaero/` |
| Regenerable 可重建 | 启动自动生成，不迁 | `logs/`、`*/cache/` |
| Derived 派生（高危） | Loader/版本绑定，默认重建 | `fml.toml`、`neoforge-*.toml` |
| World-bound 世界绑定 | 只能整世界迁 | `saves/region`、`poi`、`entities` |
| Volatile 一次性 | 直接忽略 | `latest.log`、`hs_err` |

### 演进路线（后续阶段，核心代码几乎不改）
1. **观察层**：扫描快照 + 升级时 diff（new/modified/deleted）+ 逐项确认 → Manifest 沉淀
2. **自用规则库**：把 Manifest 决策抽象为 `rules/*.yaml`（带 `reason` 字段）
3. **Mod 感知**：读 `META-INF/neoforge.mods.toml` 取 modid/version → `profiles/*.yaml`，按版本变化给策略（如 Create 0.5→6.0 配置格式变 → 提示）
4. **内容检测**：TOML/JSON/NBT Inspector 识别危险字段（`device_uuid`/GPU）
5. **开放社区**：把 Manifest/Rules 抽象成可共享、可 PR 的规则文件；可选 GUI

> 四层识别（80/20）：通用规则(80%) / 内容特征(15%) / Mod Profile(4%) / 用户学习(1%)。

## 分发策略
- **面向无 Python 环境的玩家**：用 **PyInstaller** 打成单文件 `mcmig.exe`
- **CI/发布**：**GitHub Actions** 在 `git tag v*` 时自动起 Windows runner → pip install → PyInstaller → 上传 Release（含 exe + SHA256）
- **项目布局**（工作目录下新建 `migration/`）：
```
migration/
├── pyproject.toml
├── requirements.txt
├── migration/        # 核心代码（scanner/manifest/planner/executor...）
├── rules/            # 通用规则库（YAML，演进阶段）
├── profiles/         # Mod Profile（YAML，演进阶段）
├── tests/
└── .github/workflows/{build.yml, release.yml}
```

## 启动器与版本隔离关键注意事项
- **版本隔离是核心模型**：`LaunchArgumentIndieV2:4`（`冒险活动客户端\PCL\Setup.ini`）使每个 `versions\<ver>\` 成为完整独立实例；**迁移以版本文件夹为单位**，不是整个游戏根目录
- **活跃版本有两处记录须同步**：
  - `冒险活动客户端\PCL.ini` → `Version:1.21.1-NeoForge_21.1.228`
  - `冒险活动客户端\PCL\Setup.ini` → `LaunchVersionSelect:1.21.1-NeoForge_21.1.219`（历史遗留值，迁移后应指向新版本）
  迁移完成后工具应提醒/自动更新这两处，否则启动器仍启动旧版本
- **共享资源在游戏根目录**（`assets/`、`libraries/`、`launcher_profiles.json`），不属于任何版本文件夹，迁移不触碰
- **`PCL.ini` 的 `CardValue1`** 记录可选版本列表，新增版本文件夹后可能需在此登记才出现在启动器版本选择中
- 版本 json（`<ver>.json`）是**版本清单的唯一事实来源**（NeoForge/FML/MC 版本、库、启动参数），需要版本元信息时读它而非猜测

## 迁移工具脚本参考
> **待定**（尚无脚本）。工具落地后在此用表格列出各脚本用途，类比：
>
> | 脚本 | 用途 | 关键特性 |
> |------|------|---------|
> | （待补充） | | |

## Skills 参考
> **待定**（目前无 skills 目录）。后续如沉淀出「迁移流程」「config 合并规则」等可复用 skill，在此登记。

## 待确认事项
- **Manifest 存放位置**：放在工作目录、迁移工具子目录、还是目标版本文件夹内
- **黑名单初版清单确认**：当前「⛔ 不迁」集合（logs/crash-reports/二进制/PCL/downloads/patchouli_*/usercache/observable_announce/command_history）是否完整
- **config 白名单补全**：`.bak` 判定法已确立，但 `iris.properties`/`jade`/`jei sort-order`/`ftbchunks-client.snbt` 等无 .bak 的玩家偏好文件需维护白名单
- **回滚机制**：是否提供迁移前自动打包备份目标版本文件夹 + 检测启动失败自动恢复
- **是否做 GUI**：v1 先 CLI；后续是否做「旧实例/新实例/浏览」式简单窗口（见 `Reference/discussions/chat.md`）
