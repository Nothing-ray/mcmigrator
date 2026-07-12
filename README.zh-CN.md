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

### .bak 判定法

NeoForge 在玩家游戏内修改 config 时自动生成 `.bak` 备份。工具通过比较 config 与 `.bak` 的 MD5 判断:

- **MD5 不同** → `.bak` 存的是改前的旧版本 → 玩家确实改过 → 迁移
- **MD5 相同** → `.bak` 备份的与当前一致 → mod 自动生成(非玩家修改) → 跳过

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

## 路线图

- ✅ v0:`scan`/`diff` 只读对比(已完成)
- 🚧 v1 Phase 1:`plan` 子命令 + config 玩家改动判定(`.bak` 法 + 白名单)(设计中)
- 📋 v1 Phase 2:`migrate` 实际写盘 + 回滚
- 📋 v1 Phase 3:Manifest 决策沉淀(自动记忆迁移决策)
- 📋 未来:Mod Profile(META-INF 解析)+ 内容检测 + GUI

详见 [`Reference/specs/`](Reference/specs/)。

## 许可证

MIT — 见 [LICENSE](LICENSE)。
