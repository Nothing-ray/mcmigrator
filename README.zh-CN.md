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

## 路线图

- ✅ v0:`scan`/`diff` 只读对比(已完成)
- 🚧 v1 Phase 1:`plan` 子命令 + config 玩家改动判定(`.bak` 法 + 白名单)(设计中)
- 📋 v1 Phase 2:`migrate` 实际写盘 + 回滚
- 📋 v1 Phase 3:Manifest 决策沉淀(自动记忆迁移决策)
- 📋 未来:Mod Profile(META-INF 解析)+ 内容检测 + GUI

详见 [`Reference/specs/`](Reference/specs/)。

## 许可证

MIT — 见 [LICENSE](LICENSE)。
