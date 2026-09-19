# mcmigrator

[English](README.en.md) | [🏠 落地页](README.md)

> Minecraft 整合包版本迁移工具(只读 scan/diff)— 在同一整合包的版本隔离文件夹之间,比对玩家状态差异。

同一整合包从一个 NeoForge 版本文件夹迁到另一个时,你想知道:**玩家在新版本里要保留/改动哪些文件?** `mcmigrator` 用 `scan` 扫描版本文件夹、用 `diff` 对比两份快照,产出迁移导向的 6 桶报告。**v0 纯只读**——绝不写入游戏目录,所有产物落在工作目录的 `.mcmig/`,可无限次试。

## 特性

- **分层哈希**:文本全量 MD5、mods 按文件名集合、bulk(`.sqlite`/`.zip`/`.mca`)按 size——快且精确(玩家会改的文本字节级,不会改的二进制走 size 代理)。
- **数据驱动分类**:规则引擎(`pathspec`,gitignore 语义),分层 first-match-wins(CLI 覆盖 > 用户规则 > 内置默认 > unknown),改规则不重扫。
- **迁移导向 6 桶 diff**:`to_migrate`(必迁)/ `candidate`(待确认)/ `mods`(按文件名集合)/ `only_in_dst`(目标自带)/ `identical`(一致)/ `never`(不迁)。
- **零写入**:对游戏目录只读;回退/重复试验天然满足(游戏状态不可变)。

## 安装

需要 Python 3.11+。

```bash
git clone https://github.com/Nothing-ray/mcmigrator.git
cd mcmigrator
python -m venv .venv
.venv\Scripts\Activate.ps1   # Windows PowerShell
pip install -e .
```

## 配置游戏根目录

`mcmig` 需要知道你的游戏根目录(含 `versions/` 的那个)。优先级从高到低,三选一:

1. **命令标志**:`mcmig scan <ver> --game-root <绝对路径>`
2. **环境变量**:设 `MCMIG_GAME_ROOT`
3. **配置文件**:`cp config.example.yaml .mcmig/config.yaml`,改其中的 `game_root`

三者都没给时,工具报错退出并给出上述引导。

## 快速上手

```bash
mcmig scan 1.21.1-NeoForge_21.1.227                              # 扫描 → 快照 + 分类汇总
mcmig scan 1.21.1-NeoForge_21.1.229
mcmig diff 1.21.1-NeoForge_21.1.227 1.21.1-NeoForge_21.1.229     # 6 桶报告(rich)
mcmig diff <src> <dst> --json                                     # JSON 输出
mcmig diff <src> <dst> --exclude "logs/**"                        # 临时按 never
mcmig diff <src> <dst> --show-identical --show-never              # 显示隐藏桶
```

### 命令总览

| 命令 | 用途 |
|------|------|
| `mcmig scan <ver>` | 扫描版本文件夹,生成快照 + 分类汇总 |
| `mcmig diff <src> <dst>` | 对比两份快照,产出 6 桶报告 |
| `mcmig plan <src> <dst>` | 生成迁移计划(只读,产出 action 列表) |
| `mcmig migrate <src> <dst>` | 执行已保存的迁移计划(先 plan 后 migrate;覆盖自动备份到 `_conflict_backup/`) |
| `mcmig swap <src> <dst> <新包目录>` | 整合包替换:兼容预检→装包→生成换包迁移计划 |
| `mcmig doctor` | 环境体检:数据完整性 / 游戏目录配置 / 权限 / 磁盘空间 |
| `mcmig gui [--port N] [--no-browser]` | 启动本地 Web 迁移向导(自动开浏览器;默认随机空闲端口) |

## 工作方式

1. `scan` 遍历版本文件夹,按分层策略哈希,生成**原始清单快照**(`.mcmig/snapshots/<ver>.snapshot.json`,**不含分类**)。
2. `diff` 读两份快照,**按当前规则现算分类**,再把每个文件归入 6 桶。
3. 改规则(用户 `.mcmig/rules.yaml` 或 CLI `--exclude`/`--include`)后**直接重 diff,无需重扫**——分类在读快照时现算。

### 哈希分层

| 文件类型 | 依据 | 理由 |
|---|---|---|
| 文本(`config/`、`options.txt`、`*.dat`、脚本) | 全量 MD5 | 玩家会改,要字节精确 |
| `mods/**/*.jar` | 文件名集合 | 玩家不改 jar 内部,版本变 = 换文件名 |
| `*.sqlite` / `*.zip` / `*.mca` | size | 整体替换型,size 是好代理 |

`--strict` 强制全量哈希作为逃生口。

## 分类系统

`mcmig plan` 把每个文件归入一个 **origin**(语义来源),决定迁移行为:

| Origin | 行为 | 含义 | 举例 |
|--------|------|------|------|
| ✅ 必迁 | 复制 | 玩家核心数据,丢失不可逆 | `options.txt`、`saves/`、`local/ftbchunks/` |
| ✏️ 改过的 config | 复制 | 有 `.bak` 且内容不同=玩家游戏内改过 | `config/create-client.toml` |
| 📋 备份文件 | 复制 | `.bak` 文件,跟随父 config 迁移 | `config/create-1.toml.bak` |
| 📦 补 Mod | 复制 | 源独有 mod(玩家额外添加的) | `mods/extra.jar` |
| 📦 换包排除 | 跳过 | `--modpack-swap` 下源独有 mod 视为旧包自带,不回迁;rules.yaml 显式 must_migrate 仍放行 | 旧整合包的 `mods/old-pack.jar` |
| ❓ 待确认 | 询问 | 无可靠自动判定,需人工确认 | `kubejs/**`、`resourcepacks/*.zip` |
| 👻 孤儿数据 | 跳过 | 对应的 mod 未安装在目标版本,迁移无意义 | `config/jade/**`(Jade 已移除) |
| 🔒 版本敏感 | 跳过 | 版本/硬件派生,跨版本迁移高危,让目标重建 | `config/fml.toml` |
| ⚙️ 默认配置 | 跳过 | 无 `.bak` 的 mod 默认值,或 `.bak` 内容与 config 相同(自动生成) | `config/patchouli-client.toml` |
| ⛔ 不迁 | 跳过 | 临时产物/版本二进制/缓存 | `logs/`、`<ver>.jar` |
| ⏭ 一致 | 跳过 | 两边内容一致 | MD5 相同的文件 |
| 📦 共有 Mod | 跳过 | 两边都有的 mod | — |
| 📦 目标独有 Mod | 跳过 | 目标比源多出的 mod | — |

### 优先级

规则按优先级从高到低匹配(first-match-wins):

```
CLI(--include/--exclude) > 额外规则文件 > 用户 rules.yaml > 孤儿检测 > 版本敏感 > 白名单 > 内置默认
```

- **用户显式规则 > 孤儿检测**:在 `.mcmig/rules.yaml` 中写 `config/jade/** → must_migrate` 可强制迁移孤儿 config
- **孤儿检测 > 白名单**:白名单中对应 mod 已删除的条目自动失效
- **孤儿检测 = 事实判断**:mod 物理上不在目标 `mods/` 目录 → config 无人认领 → 迁移无意义

### 整合包替换

更换整个整合包(而非同包升级版本)时加 `--modpack-swap`:源独有 mod 不再回迁(旧包自带,非玩家私货),但用户 `rules.yaml` 中显式 `must_migrate` 的 jar 仍会放行。不加 flag 时,若检测到源独有 mod ≥ 20 个,工具会在 stderr 提示。

完整换包流程:

```bash
mcmig swap <旧版本> <新版本> <新包目录>   # 预检+装包+出计划(会因 NeoForge 不满足而中止,按提示处理;dry-run 不生成迁移计划)
mcmig migrate <旧版本> <新版本>           # 审阅计划后执行复制
```

新版本文件夹请先用 PCL2 安装好对应 NeoForge。migrate 可重入(中断后重跑自动续传)。

### .bak 判定法

NeoForge 在玩家游戏内修改 config 时自动生成 `.bak` 备份。工具通过比较 config 与 `.bak` 的 MD5 判断:

- **MD5 不同** → `.bak` 存的是改前的旧版本 → 玩家确实改过 → 迁移
- **MD5 相同** → `.bak` 备份的与当前一致 → mod 自动生成(非玩家修改) → 跳过

### diff 的 mods 桶语义与配对

diff 以**迁移源视角**报告:src=迁移源(旧实例),dst=目标(新实例)。
mods 桶标记:`shared`=两侧同名 jar;`to_add`=**源有目标无**(迁移时会补齐);
`target_only`=目标自带。升级/改名由 modid 配对识别(rich 表 `⇄upgrade`/`⇄renamed` 标记 +
表尾配对脚注;`--json` 输出顶层 `mod_pairs` 数组),不再表现为无关的"删旧+增新"。

- `rebuilt`:两侧同名同版本号但内容不同(上游重新打包)——diff 标记,plan 默认保留目标侧并警告,不自动覆盖
- `mod_pairs` 条目含 `source` 字段:`registry`(读取 jar 内 mods.toml,需两侧版本目录真实独立)或 `filename`(快照文件名家族归一,复放/junction 场景可用)
- 两侧版本目录指向同一路径(NTFS junction)时,注册表配对自动失效并提示,文件名配对兜底
- 源侧 mod 已被目标移除时,其 config 会被标注为孤儿(`never/orphan`)——独立 `diff` 与 `plan` 语义一致
- `*.properties`(如服务端 `server.properties`,vanilla 重写导致的转义/时间戳/编码噪声)与
  `*.json`/`*.toml`(mod 启动重写导致的键序/表序噪声)在字节不同但键值语义相同时
  报告为 `identical/semantics` 而非 modified
- JVM 崩溃残留(`hs_err_pid*.log`/`replay_pid*.log`)归入 never 桶,不迁移
- 孤儿标注与语义复核依赖快照的 `game_root` 可达;不可达时(跨机复放)自动降级为纯字节对比,stderr 提示一行

## 数据与卸载

### 工具数据放在哪

- **绿色 exe(推荐,免 Python)**:所有工具状态(配置/快照/计划/规则)都在 `mcmig.exe` 同级的 `data/` 文件夹内,绝不写入 AppData 或用户目录——整个客户端文件夹拷走即带走全部工具状态。`data/` 内再按游戏根目录名建子文件夹隔离(`data/<游戏目录名>/snapshots|plans|rules.yaml`),多个整合包互不串数据;`data/config.toml` 记录游戏根目录。
- **源码运行(Python)**:沿用当前目录的 `.mcmig/` 布局,语义与上述一致。

### 游戏侧会写什么

工具绝不在游戏根目录创建任何工具目录;迁移期间唯一写入游戏侧的是**冲突备份**:同名但内容不同的文件在覆盖前会先备份到 `<目标版本>/_conflict_backup/`(镜像相对路径结构;首份备份为覆盖前的原件,重跑不会覆盖)。迁移完成并确认无误后,该文件夹可安全删除。

### 如何卸载

1. 删除 mcmig 程序文件夹(绿色 exe 下 `data/` 在其中,一并删除即清空全部工具状态);
2. 可选:删除各 `<游戏根>/versions/<版本>/_conflict_backup/`(留着也无害);
3. 游戏目录本身(mods/config/saves…)不会被工具改动,无需清理。

### 校验下载完整性(SHA256)

每次 GitHub Release 附带 exe 与其 SHA256 校验值。下载后请校验(Windows 自带命令):

```bat
certutil -hashfile mcmig.exe SHA256
```

将输出与 Release 页面的 SHA256 比对,一致即下载完好;不一致请重新下载。

## 项目结构

```
mcmigrator/
├── migration/          # 工具源码(hashing/rules/classifier/snapshot/scanner/differ/reporter/cli)
├── tests/              # 单元 + 端到端测试(pytest)
├── Reference/          # 设计文档(specs / design / plans)
├── data/default_rules.yaml  (在包内)  # 内置默认分类规则
├── config.example.yaml # 配置模板
├── AGENTS.md           # 项目规范(给 AI 协作者)
└── README.md
```

## 设计与文档

详细设计见 `Reference/`:`specs/`(版本设计规格)、`design/`(子系统设计备忘)、`plans/`(实现计划)。

## 贡献

欢迎提交以下内容(中文/英文均可):

- **分类规则经验** — 你整合包里遇到的怪文件怎么归类,例如 `.mcmig/rules.yaml`:
  ```yaml
  rules:
    - match: "screenshots/**"
      decide: never
      reason: "玩家截图,不迁"
  ```
- **白名单条目** — 你发现的「无 `.bak` 但属玩家偏好」的文件(见 `migration/data/whitelist.yaml`)
- **Bug report & 功能建议**

→ [GitHub Issues](https://github.com/Nothing-ray/mcmigrator/issues) | PR 欢迎(贡献按 MIT 许可)

## 已知限制

- **旧版中文 Windows 控制台(cmd / GBK 代码页)下,报告里的 emoji 会显示为 `?`**。这是 Windows 控制台编码(GBK/cp936)无法渲染 emoji 的限制——`mcmigrator` 会自动降级以避免崩溃,中文与所有路径/原因始终正常显示,仅 ✅📦🔄 等装饰性符号变为 `?`。现代终端(Windows Terminal / PowerShell 7)不受影响。

### 编码行为说明(重要)

`mcmigrator` 按输出目的地自动选择编码:

- **直接显示在控制台**(tty):沿用控制台原生编码(GBK 控制台中文正常)
- **重定向到文件或管道**(`> out.json` / 供其他程序读取):**恒为 UTF-8**(无 BOM),与项目"文件一律 UTF-8"规范一致——`--json` 输出可放心跨机消费

两个 Windows 环境提示(源自 2026-09 服务端实测):

1. **推荐用 PowerShell 7(pwsh)或 Windows Terminal**;老旧 PowerShell 5.1 会把无 BOM 的 UTF-8 脚本按 GBK 解析,且 GBK 控制台无法显示 emoji
2. **GBK 区域机器上,版本名建议用 ASCII**(如 `pre-9.8` 而非 `9.11前`)——个别终端环境经 bash 传中文参数可能出现编码错位(用 PowerShell 传参正常);遇到"缺少快照"报错时优先排查此项

### 服务端(dedicated server)场景

专用服务器没有 `versions/` 结构——**每个服务器目录就是一个"版本"**。内置默认规则已覆盖服务端核心资产:`world/**`、`server.properties`、`whitelist.json`、`ops.json`、`banned-*.json` → 必迁;`mods_*/**`(运维回滚备份目录)→ 不迁。

零拷贝接入技巧:用 NTFS Junction(`mklink /J`)把服务器目录映射为 `versions\<名称>`,即可直接 `scan`/`diff`,无需复制 2GB+ 的服务端目录。换装前后各 scan 一次即可得到完整对比。

## 路线图

- ✅ v0:`scan`/`diff` 只读对比(已完成)
- ✅ v1 Phase 1:`plan` 子命令 + config 玩家改动判定(`.bak` 法 + 白名单)(已实现)
- ✅ v1 Phase 2:`migrate` 实际写盘 + `swap` 换包编排(已实现;回滚见未来)
- ✅ v0.6:事务式文件操作(fsops)+ 绿色 exe 数据布局 + `doctor` 体检 + 本地 Web 向导 `mcmig gui`(已实现)
- 📋 v1 Phase 3:Manifest 决策沉淀(自动记忆迁移决策)
- 📋 未来:Mod Profile(META-INF 解析)+ 内容检测

详见 [`Reference/specs/`](Reference/specs/)。

## 许可证

MIT — 见 [LICENSE](LICENSE)。
