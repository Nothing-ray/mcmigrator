# 换包工作流设计（mcmig migrate + mcmig swap）

> 状态: 已评审待实现 | 日期: 2026-08-31 | 前置: mod-awareness(0.4.0) / --modpack-swap(377a9ed)
> 观察依据: `Reference/observations/modswap-830-20260830/`(整包替换全流程手工演练)

## 1. 目标与非目标

**目标**: 把 modswap-830 观察中手工执行的完整流程产品化:
1. **执行器 `mcmig migrate`**: 按已保存的 plan 执行复制(含冲突备份/校验/交互确认)——通用能力,正常版本迁移同样受益
2. **换包编排 `mcmig swap`**: 预检(兼容)→装包→生成 plan,一条命令完成换包准备

**非目标**:
- 不安装/管理 NeoForge 或版本二进制(PCL2 的职责;目标版本须由用户预先建好)
- 不修改 PCL 配置(只提醒,见 §5)
- 不实现封包(.mcmigpack)格式(见 §8 未来扩展,仅留接口钩子)
- 不做删除:plan 语义中没有 delete,migrate 不会删目标任何文件

## 2. 命令契约

### 2.1 `mcmig migrate <src> <dst> [--dry-run] [--skip-ask] [--yes-ask] [-y]`

- **输入**: `.mcmig/plans/<src>__<dst>.plan.json`(plan 命令已保存)。**不重新规划**——审过的计划才是执行的保证
- **前置校验**:
  - plan 文件缺失 → 报错退出(提示先 `mcmig plan`)
  - **过期检测**: src/dst 快照的 mtime 晚于 plan 生成时间 → 警告「快照已更新,计划可能过期,建议重跑 plan」;`--force` 才继续
  - plan 已执行过(见 §3.5)→ 提示已执行,`--force` 才重跑
- **执行语义**:
  - 仅执行 `behavior=COPY` 的 action;SKIP/ASK 之外的 action 不触碰
  - `behavior=ASK`(needs_review): 默认逐项交互确认(rich Confirm);`--skip-ask` 全部跳过;`--yes-ask` 全部迁
  - `-y`: 跳过执行前的整体确认摘要(非交互场景)
- **可重入**(已确认决策): 源/目标 MD5 相同的条目视为已完成——跳过且**不重复备份**;中断后重跑 = 续传
- **冲突备份**: 覆盖任何已存在的 dst 文件前,先复制到 `<dst>/_conflict_backup/<原相对路径>`(镜像目录结构)。`_conflict_backup/` 本身在 scan 的 never 规则中(不迁不删)
- **校验**: 复制后逐文件 MD5 比对源;不一致计入失败清单,结束时报出(不静默、不回滚单个文件——备份已保底)
- **退出码**: 0=全部成功;1=部分失败(详见报告);2=前置校验失败

### 2.2 `mcmig swap <src> <dst> <new_mods_dir> [--dry-run] [--force]`

按序四步,任一步失败即停(已完成的步骤如实报告):

1. **预检**:
   - dst 版本文件夹存在且有 `<ver>.json` → 否则报错「请先用 PCL2 安装目标 NeoForge 版本」
   - 解析 new_mods_dir 注册表(scan_mods,含 jarjar 内嵌) + dst 的 NeoForge 版本(read_neoforge_version)
   - 逐 mod 检查 neoforge_range:不满足者列红字警告(参照实测: jei[21.1.238,)/cp_*[21.1.248,) vs 233);**存在即默认中止**,`--force` 才继续
   - src 快照必须已存在(scan 过)
2. **装包**: 复制 new_mods_dir 的 jar → dst/mods/
   - 同名同内容(MD5)→ 跳过
   - **同名不同内容 → 提醒并交互询问**(已确认决策): 保留目标的(默认)/覆盖为新包的/中止
   - dst/mods 已含**其他** jar(非本次新包内容)→ 警告「目标 mods 非空,换包流程面向空壳版本」,确认后继续
3. **规划**: 自动 `scan dst` → 内部走 plan 管线(modpack_swap=True + orphan 规则) → 保存 plan 文件
4. **摘要**: 打印 plan 分类计数 + 下一步提示(`mcmig migrate <src> <dst>`;提醒先审阅 `.mcmig/plans/` 下计划)

`--dry-run`: 预检+装包按彩排报告(不写盘),规划步照常生成 plan(只读源)。
决策(2026-09-01): 实现中 dry-run 跳过规划步——装包未写盘时重扫 dst 会产出误导性 plan;去掉 --dry-run 后 swap 自动生成。

## 3. 模块设计

### 3.1 新模块 `migration/executor.py`

```python
class ExecutionResult:  # 每文件: path/copied/skipped_identical/backed_up/failed/asked
class Executor:
    def __init__(self, plan: MigrationPlan, src_root: Path, dst_root: Path,
                 ask_handler: Callable[[ActionRecord], bool]): ...
    def execute(self, dry_run: bool = False) -> list[ExecutionResult]: ...
```

- 纯逻辑,无 I/O 之外的 CLI 依赖;交互通过 `ask_handler` 注入(CLI 传 rich Confirm,测试传固定函数)
- plan 文件读写校验在 `plan.py` 增补(`load_plan` / `mark_executed`,写入 `executed_at` + 结果摘要)

### 3.2 CLI(`migration/cli.py`)

- 新子命令 `migrate` / `swap`,复用 `_resolve_game_root` / `_version_dir`
- swap 内部直接调用现有 `scan_mods`/`read_neoforge_version`/`check_version_range`/plan 管线函数(与 `_cmd_plan` 共用,抽公共函数避免复制粘贴)

### 3.3 PCL 指向提醒(migrate 完成后打印,文案要求完整可照做)

```
[提醒] 迁移完成。若要让启动器默认打开新版本,需同步两处配置:
  1. <游戏根>\PCL.ini 的 Version: 行 → 改为 <dst>
  2. <游戏根>\PCL\Setup.ini 的 LaunchVersionSelect: 行 → 改为 <dst>
工具不代改启动器配置(改错会导致无法启动任何版本),请手动确认后修改。
```

## 4. 安全清单

- 执行前整体确认摘要(N 复制/M 备份/K 跳过);`--dry-run` 全程可用
- 永不删除;覆盖必先备份;备份目录自带 never 规则
- 快照过期/已执行防护(§2.1)
- 游戏运行检测: 尝试独占打开 dst 的 `options.txt`/`usercache.json`,被占用则警告「游戏可能仍在运行」(Windows 文件锁特性,非强制)

## 5. 已确认决策记录

| # | 决策 |
|---|------|
| 1 | migrate 可重入: MD5 相同跳过、不重复备份 |
| 2 | PCL 指向只提醒不动手,提醒文案含两处路径与改法 |
| 3 | 装包同名不同内容 jar: 提醒+交互询问(默认保留目标) |
| 4 | 封包格式本轮不实现,仅保证执行器接口不绑定「源=版本文件夹」 |
| 5 | (2026-09-01) swap --dry-run 跳过规划步: 装包未写盘时重扫 dst 会产出误导性 plan;去掉 --dry-run 后 swap 自动生成 |

## 6. 测试计划

- executor 单测: 复制/相同跳过/冲突备份(含镜像结构)/MD5 校验失败/ASK 三态(ask_handler)/可重入(跑两遍第二遍全 skipped)/dry-run 零写盘
- plan 增补单测: load_plan round-trip / mark_executed 防重跑
- swap e2e(tmp_path 合成目录): NF 不满足默认中止+--force 放行 / 装包(同名同内容跳过、同名不同内容走询问 handler)/目标非空警告/自动生成含 mod_swapped_out 的 plan
- migrate e2e: scan→plan→migrate 全链路,断言 dst 文件内容+备份存在+plan executed_at 写入;过期 plan 拒绝
- 零回归: 现有 229 测试不动

## 7. 验收标准

1. `mcmig migrate` 对 modswap-830 场景重放(合成): 83 文件复制、11 冲突备份、重跑全 skip、MD5 全过
2. `mcmig swap` 在「新包要求 NF≥25x、目标 233」的合成场景: 默认中止并列出 mod;`--force` 后完成装包+plan(mod_swapped_out 生效)
3. PCL 提醒文案出现且含两个具体路径
4. 全量 pytest + ruff 通过

## 8. 未来扩展: 封包格式(.mcmigpack)——仅记录,不实现

- zip 容器: `manifest.json`(规则版本/来源 mcmig 版本/文件清单+MD5/mod 依赖声明) + 玩家数据 + 可选 `rules.yaml`
- 解包 = swap 的变体: 源从「版本文件夹」换成「包」;预检/孤儿/兼容管线全复用
- 关键钩子: Executor 的 `src_root: Path` 保持泛化(任何能按相对路径读文件的结构),不硬编码 versions/ 语义
- 规则分享: rules.yaml/覆盖表作为包的可选段,解包时提示「是否采纳对方规则」(用户主权)
