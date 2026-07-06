# v0 设计规格：只读 scan/diff 工具

> 状态：定稿（待实现）
> 日期：2026-07-02
> 配套设计备忘：`design/hashing-strategy.md`、`design/classifier-rules.md`
> 原始讨论：`discussions/chat.md`

---

## 0. 目标与回退保证

- **范围**：纯只读的 `scan` + `diff` 两个子命令。**绝不写入游戏版本文件夹**。
- **唯一写入**：`.mcmig/`（工具自身 scratch 目录）。
- **回退 / 重复试验**：游戏状态对 v0 不可变 → 可无限次运行；重置工具状态 = 删 `.mcmig/`。
- **不在 v0 范围**：实际迁移（copy/覆盖）、Manifest 决策沉淀、写操作、GUI。

## 1. 架构（6 模块）

| 模块 | 职责 | v0 |
|---|---|---|
| `Scanner` | 遍历版本目录 → 原始 `FileEntry(path,size,md5\|None)` 清单 | ✓ |
| `Classifier` | 规则引擎：`RuleSet.classify(path)→Category`（数据驱动） | ✓ |
| `Snapshot` | 存/读 `.snapshot.json`（**原始清单，无分类**） | ✓ |
| `Differ` | 两份分类快照 → 6 桶 `DiffReport` | ✓ |
| `Reporter` | rich 终端报告 + `--json` | ✓ |
| `CLI` | argparse 子命令 | ✓ |
| `Executor` / `Manifest` | 写操作 / 决策持久化 | ✗ v0 不做 |

**核心解耦**：扫描存原始清单（无分类字段），分类在「读快照 → 出报告」时按**当前规则**现算 → 改规则不重扫。

## 2. 哈希策略（分层，详见 `design/hashing-strategy.md`）

- 文本（`config/` / `options.txt` / `*.dat` / 小存档）= **全量 MD5**
- `mods/**/*.jar` = **文件名集合**比（不哈希）
- `*.sqlite` / `*.zip` / `*.mca` = **size 代理**（不哈希）
- `--strict` 强制全量哈希；报告标 `verified` vs `size-based` 置信度
- 算法：stdlib `hashlib`（MD5，零依赖）

## 3. 分类器（规则引擎，详见 `design/classifier-rules.md`）

- **分层 first-match-wins**：CLI 覆盖 > `.mcmig/rules.yaml` > [Manifest 预留] > `default_rules.yaml`（内置） > `unknown`
- **glob**：`pathspec`（gitignore 语义）
- **规则**：`match`（glob） + `decide`（`never`/`must_migrate`/`unknown`/`ask`） + `reason`
- **内置默认**（`migration/data/default_rules.yaml`）：
  - `never`：`logs/**`、`crash-reports/**`、`<ver>.jar/.json`、`<ver>-natives/**`、`PCL/**`、`downloads/**`、`patchouli_books/**`、`patchouli_data.json`、`usercache.json`、`observable_announce`、`command_history.txt`、`defaultconfigs/**`、`**/cache/**`
  - `must_migrate`：`options.txt`、`servers.dat(_old)`、`saves/**`、`schematics/**`、`xaero/**`、`XaeroWaypoints_*/**`、`local/ftbchunks/**`、`Distant_Horizons_server_data/**`、`dragon-survival/**`
  - 其余 → `unknown`

## 4. Diff 语义（迁移导向 6 桶）

`diff <src> <dst>`，src=玩家状态、dst=目标。每文件按（分类, 在src?, 在dst?, 内容同?）落桶：

| 桶 | 条件 |
|---|---|
| ✅ `to_migrate` | must_migrate 且（dst 无 / 内容不同） |
| ◐ `candidate` | unknown（非 mod）且不同 |
| 📦 `mods` | mods 按文件名集合（源独有 / 目标独有 / 共有） |
| 📍 `only_in_dst` | dst 有 src 无（目标自带，不动） |
| ⏭ `identical` | 两边一致（**默认隐藏**） |
| ⛔ `never` | 分类=never（**默认隐藏**） |

- `ask` 决策文件在 v0（只读）归入报告「需决策」段单列（无确认循环）。
- `--show-identical` / `--show-never` / `--all` / `--mods` / `--category CAT` 控制可见性。
- **mods 单独按文件名集合**处理（不混进 candidate），避免满版本 vs 空壳时 119 jar 全成噪音。

## 5. CLI

```
mcmig scan <ver> [--game-root PATH] [--exclude GLOB] [--include GLOB]
                 [--rule FILE] [--strict] [--json] [-q]
mcmig diff <src> <dst> [--game-root PATH] [--exclude/--include/--rule]
                       [--show-identical] [--show-never] [--all] [--mods]
                       [--category CAT] [--json] [-q]
mcmig --version / --help
```
- `--exclude GLOB` = 本次按 `never`；`--include GLOB` = 本次按 `must_migrate`（可多次）
- `--rule FILE` = 本次额外加载规则文件
- `--strict` = 强制全量哈希
- `--game-root` 默认 `./冒险活动客户端`

## 6. 快照 schema + 布局

**`.mcmig/snapshots/<ver>.snapshot.json`**（原始清单，**无分类字段**）：
```json
{
  "tool_version": "0.1.0",
  "snapshot_format": 1,
  "version": "1.21.1-NeoForge_21.1.227",
  "game_root": "<abs path>",
  "scanned_at": "2026-07-02T12:00:00+08:00",
  "hash_mode": "tiered",
  "file_count": 412,
  "files": [
    {"path": "options.txt", "size": 1234, "md5": "abcd..."},
    {"path": "mods/create.jar", "size": 999999, "md5": null}
  ]
}
```
- `snapshot_format` 做向前兼容；`md5: null` = 分层未哈希（区别于「哈希失败」）。

**`.mcmig/` 位置**：工作目录（非用户 home——规则整合包专属，需随项目走）。
```
<workdir>/.mcmig/
├── snapshots/*.snapshot.json   ← gitignore（机器/时间相关）
└── rules.yaml                   ← 可提交（团队共享规则）
```

## 7. 错误处理

- 版本文件夹不存在 → 报错 + 列出 `versions/` 可用版本
- 文件占用 / 无权限（游戏运行中常见）→ **跳过 + warn 计数，不崩溃**；报告单列 `unreadable`
- 快照损坏 / 格式不兼容 → 提示重新 scan，不尝试修复
- diff 缺快照 → 提示「先 `mcmig scan <ver>`」
- 空目标（如 229）→ 合法状态，正常产出大量 src_only，**不是错误**
- glob 规则语法错 → 指出哪条规则 + 其余继续（不整套失效）
- **原则**：v0 无破坏性操作，**单文件永不致全崩**；收集错误，结尾汇总。

## 8. 测试

- **原则**：不碰真实 354MB；合成 fixture；快、自包含、可重复。
- **Fixture**（`tests/fixtures/`）：`mini_version/` + `mini_version_b/`，每类别代表文件（options/servers=必迁、logs/crash=不迁、config/foo.toml=未知、mods/fake.jar、DH/test.sqlite、某/cache/ 命中 `**/cache/**`），内容固定可断言 MD5；b 版做「改一 config + 加一文件 + 删一文件」用于 diff。
- **测什么**：Classifier（优先级 / first-match）、Snapshot 往返、Differ 各桶、Scanner（分层哈希 / 跳 unreadable）、CLI（`--json` 可 `json.loads`）。
- **不测**：真实游戏目录（手动）、性能基准、PyInstaller 打包。

## 9. 项目结构 + 依赖

```
migration/
├── pyproject.toml
├── migration/{__main__,scanner,classifier,rules,snapshot,differ,reporter,cli,hashing}.py
├── migration/data/default_rules.yaml
├── tests/{test_classifier,test_rules,test_snapshot,test_differ,test_scanner,test_cli}.py + fixtures/
└── .mcmig/   (运行时，gitignore snapshots/)
```
- **v0 运行依赖**：`rich` + `PyYAML` + `pathspec`（标准库：`hashlib` / `pathlib` / `argparse`）
- **打包**：PyInstaller → 单文件 exe（见 AGENTS.md「分发策略」）

---

## 验收标准（v0 完成判定）

1. `mcmig scan 1.21.1-NeoForge_21.1.227` 生成快照 + 打印分类汇总（rich）
2. `mcmig diff 1.21.1-NeoForge_21.1.227 1.21.1-NeoForge_21.1.229` 产出 6 桶报告，`to_migrate` 含 options/servers/xaero/ftbchunks/DH/dragon-survival
3. 改 `.mcmig/rules.yaml` 加一条 `--exclude` 后**不重扫**重新 diff，结果即时反映
4. 游戏版本文件夹**零写入**（可用文件哈希前后对比验证）
5. 全部单元测试通过；fixture 端到端测试通过
