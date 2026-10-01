# 批次I 设计规格:GUI 演进与分发闭环(v0.6 → 1.0.0)

> 状态: 评审修订 v2(收编 2026-10-01 外部静态评审 8 项意见,见 §0) | 日期: 2026-10-01 | 前置: 批次H spec(466d2de,待实现,目标 0.11.0);GUI v0.6 已交付(`2026-09-04-gui-design.md`)
> 调研依据: MAA(MaaAssistantArknights)架构调研(2026-10-01,证据锚定与措辞边界见 §8);评审的全部代码级断言已对照本仓核实(file:line 见 §0)
> 决策确认: 2026-10-01 与维护者对齐(排期先H后I / 维持 Web+pywebview / 首批五项 / CI 双形态打包);v2 增补:**可靠性闭环取代功能数量成为 1.0 验收主轴**

## 0. v2 评审对照表(意见 → 核实 → 处置)

| # | 意见 | 核实结果 | 处置 |
|---|---|---|---|
| 1 | GUI 缺 CLI 执行防护 | ✅ CLI 四道: executed_at(cli.py:613)/快照过期(:620-627)/版本目录存在(:628-632)/游戏运行中(:633-636);GUI migrate job(server.py:304-368)零防护;pipeline.execute_migration 仅共享 tmp 清理+磁盘预检两道(pipeline.py:326-332) | 新增共享执行预检 §3.2;「GUI 防护≥CLI 等价」进验收 |
| 2 | 「快照存在即复用」有旧快照风险;计划保存失败被吞 | ✅ pipeline.py:287-291 save 失败仅 log.warning;GUI migrate 从固定文件名重载(server.py:321)——「审新执旧」裂缝真实 | 复用改审阅上下文绑定;save 失败=不可执行(§3.3) |
| 3 | swap 缺两阶段协议;覆盖无备份 | ✅ cli.py:487 `backup_dir=None`(注释自认「无需再备份」);v1 设计单端点且 force 混装两义 | 两阶段 API+指纹重校验+覆盖备份+force 拆分(§5.2) |
| 4 | 关窗杀 daemon 线程,写盘中途丢失 | ✅ server.py:122 `daemon=True` | closing 事件边界+取消语义(§4.3) |
| 5 | SSE 共享队列分摊事件;无任务状态接口;锁仅进程内 | ✅ server.py:430-444 单队列 get 消费;API 面无 GET /api/jobs/{id};JobStore(server.py:100-127)进程内 | 状态机+状态端点+广播+跨进程目标锁(§4.1/4.2) |
| 6 | 信息架构应贴近玩家决策;client_only 非风险;术语需人话 | 设计判断,采纳 | 三步向导保留,diff 摘要并入审阅页(§5.1);呈现规范 |
| 7 | 首跑抛错;根目录切换不重建上下文;规则文件双轨 | ✅ workdir.py:177-185 未配置即抛(app 创建期,页面未起,注释自认妥协 I-1);server.py:599 `save_game_root` 后 WorkDir 的 snapshots/plans/rules 路径仍绑定旧 slug(构造期固定);GUI 规则在 `exe/data/<slug>/rules.yaml` vs CLI 侧 game_root | 欢迎页+实例上下文重建+路径契约 v2(§3.1),升为全批地基 |
| 8 | 更新缺事务闭环;绝对化措辞 | 技术判断,采纳(Windows 替换语义/受管清单差集/.old 时序/zip-slip/真实 exe 测试) | 更新拆基础档+事务档(§6.3);措辞改待测指标(§7/§8) |

> 表内 file:line 为 v2 核实时快照,后续批次可能漂移,以函数名为准。

## 1. 目标与非目标

**目标**(v2 重排,可靠性先于功能):
0. **可靠性基座**: 执行防护 CLI/GUI 等价、审阅-执行一致性、任务状态可恢复、并发互斥、窗口关闭边界——1.0 验收主轴
1. diff 能力进 GUI(审阅页摘要+按需明细形态,非独立步骤)
2. swap 换包 GUI 化(两阶段: 只读预检 → 确认应用)
3. pywebview 独立窗口(含关闭边界与降级)
4. CI 自动构建发布(GitHub Actions,双形态+SHA256)
5. 自动更新(基础档 1.0 必达;事务档独立验收,可滑移)

**非目标**: .mcmigpack 封包、规则编辑器、页面多语言切换、OTA 差分增量包、多更新镜像源、PySide6/Tauri 重评、C++ 重写(裁决见 §7.1)、macOS/Linux 发行(PCL2 生态为 Windows)、**自动回滚**(中途中止保留备份与事务登记,给手动恢复指引,不承诺自动回滚——诚实边界)。

## 2. 现状基线与总体架构

### 2.1 已有地基(沿用,不重构)

```
页面(单文件 HTML+原生 JS)──SSE/fetch──▶ gui/server.py(薄翻译+job+SSE+单任务锁+Host 校验)
                                              │
cli.py ──────────────────────────────┐        ▼
                                     └──▶ pipeline.py(编排库,CLI/GUI 平级消费)
                                              ▼
                                core(scanner/rules/planner/executor + fsops)
```

- v0.6 三步向导已交付,14 个 server 测试;报告对象全结构化 dataclass,rich 仅 CLI 渲染层
- `Executor.execute(progress_cb=...)` 逐文件回调(GUI 进度数据源);`execute_migration` 已共享 tmp 清理+磁盘预检两道预检(pipeline.py:326-332,零写盘契约绑定,保持原位)
- 绿色布局与 data 完整性校验(manifest.sha256 + doctor)沿用
- 分层纪律: gui 不 import cli 私有函数;核心不感知 HTTP——与 MAA「核心是库、GUI 是薄壳、窄事件协议」同构,本批只加可靠性基座、壳与闭环

### 2.2 已核实的缺口(v2 新增,本批消化)

| 缺口 | 证据 | 后果 | 归属 |
|---|---|---|---|
| GUI migrate 无执行防护 | server.py:304-368 vs cli.py:613-636 | 重复执行已执行计划/过期计划/游戏运行中写盘 | T2 |
| plan 保存失败被吞→审新执旧 | pipeline.py:287-291 + server.py:321 | 页面审阅新计划,执行加载旧文件 | T3 |
| 绿色首跑抛错(页面未起) | workdir.py:177-185 | exe 首跑界面起不来 | T1 |
| 根目录切换不重建上下文 | server.py:599 | 快照/计划/规则仍写旧 slug 目录 | T1 |
| 规则文件双轨(CLI/GUI 分叉) | `exe/data/<slug>/rules.yaml` vs CLI 侧 | 同一游戏两处规则→不同计划 | T1 |
| SSE 单队列消费+无状态接口 | server.py:430-444 | 双标签页分摊事件;刷新丢终态 | T4 |
| 单任务锁仅进程内 | JobStore | 双开 GUI/GUI+CLI 并发写同一目标 | T5 |
| 写盘中关窗杀 daemon 线程 | server.py:122 | 部分完成无提示、tmp 残留 | T6 |
| swap 覆盖无备份 | cli.py:487 | 装包覆盖不可恢复 | T8 |
| diff/mod_pairs/client_only 未进 GUI;compat 警告只进 stderr | 批次F 沿挂 | 信息不可见 | T7 |
| swap 编排滞留 cli.py(`_swap_preflight`:441/`_swap_install`:460/`_cmd_swap`:492) | swap spec | GUI 无法复用 | T8 |
| CI 缺失 | README 宣称 vs `.github/` 不存在 | 发布靠本地手动 | T11-T12 |
| modid 碰撞收口、uninstall 措辞 | 批次E 沿挂 | — | T15 随行(可滑移) |

### 2.3 目标架构(增量)

```
页面(index.html)──SSE(广播)/fetch──▶ gui/server.py ──▶ pipeline.py ──▶ core
                                        │    ▲    └─ preflight_execute / run_swap(新,自 cli 下沉)
mcmig-gui 入口 ──▶ gui/app.py(pywebview 壳,closing 边界) ──┘    │
cli.py(mcmig update / 薄壳化 swap·migrate) ────────────────────┴─▶ updater.py(新)
                                        jobs: 状态机 + GET /api/jobs/{id} + 跨进程目标锁
GitHub Actions(build/release) ──▶ Release 资产(受管清单 filelist) ──▶ updater 消费
```

## 3. W1:上下文与防护基座(先行,全批地基)

### 3.1 实例上下文与路径契约(v2,替代 v1 §3.4)

- **路径契约**:
  - **实例态**(随游戏走,CLI/GUI 互通): 快照、计划、**规则 rules.yaml**、swap 备份、目标锁 → 统一 `<game_root>/.mcmig/`
  - **全局态**(随软件走): game_root 记忆等 → `exe/data/config.toml`
  - 旧 `exe/data/<slug>/` 布局: 读侧回退+命中提示整体迁移(复用 find_snapshot 锚定优先先例);兼容模式(源码运行)不变
- **首跑欢迎页**: `create_app` 允许「未配置游戏根目录」状态启动(不再在 app 创建期抛 WorkdirError);页面=欢迎/选择根目录;选定后 `save_game_root` + **重建实例上下文**——WorkDir 的实例态路径改为由 game_root 现值**惰性派生**(或 app 持可重建工厂,实现计划二选一),根目录再切换同样生效
- 规则双轨问题随契约消解: CLI 与 GUI 读同一份 `<game_root>/.mcmig/rules.yaml`

### 3.2 共享执行预检 `pipeline.preflight_execute`(v2 新增)

```python
@dataclass(frozen=True)
class PreflightBlocker:  # code: plan_executed|snapshot_stale|version_dir_missing|game_running
    code: str; message: str
@dataclass(frozen=True)
class PreflightWarning:
    code: str; message: str
def preflight_execute(plan: MigrationPlan, game_root: Path, src: str, dst: str, *,
                      force: bool = False) -> tuple[list[PreflightBlocker], list[PreflightWarning]]
```

- 收编 CLI 四道防护(cli.py:613-636): 计划已执行 / 快照比计划新(find_snapshot 锚定+回退)/ 源目标目录存在 / 游戏运行中(`_game_running` 复用);`force=True` 时阻断降级为警告(CLI `--force` 语义等价)
- **CLI** `_cmd_migrate` 改薄壳调用,输出文案保持(对拍);**GUI** migrate job 前置调用: blocks→error 事件(三段式),warnings→页面警示条;GUI「强制继续」入口=显式勾选+确认弹层
- 既有 tmp 清理+磁盘预检留在 `execute_migration`(副作用与 dry-run 零写盘契约绑定,不挪)
- **验收级要求: GUI 执行防护 ≥ CLI 等价**

### 3.3 审阅-执行一致性(v2 新增)

- plan job 的 done 事件携带 `plan_id`(内容指纹: src/dst/generated_at/actions 摘要哈希)与 `persisted` 标志
- **`persisted=false`(保存失败)→ 页面禁用执行+提示重试**;`build_plan` 不再吞 OSError(pipeline.py:287-291)——返回失败标志或上抛;CLI plan 同步把该 warning 升级为**非零退出+明确错误**(行为变更,记入 CLI 变更清单,对拍基准随之更新)
- `POST /api/migrate` 携带 `plan_id`;server 校验磁盘计划指纹匹配,失配=阻断「计划已变化,请重新生成」——封死「审新执旧」
- 切换 game_root / 重新 plan / swap 装包后,旧 plan_id 自然失配失效(v1 的「快照存在即复用」设计撤销,复用只在同一审阅上下文内成立)

## 4. W2:任务模型可靠性

### 4.1 job 状态机与状态接口

- Job 增 `status: running|succeeded|partial_failed|failed|cancelled`(partial_failed=流程走完但有 failed 文件;cancelled=用户在安全边界停止)
- 新端点 `GET /api/jobs/{id}` → `{status, phase, progress:{index,total}, summary, error}`;**SSE 只做实时通知**: 单队列消费改**广播**(每订阅者独立队列;job 保留事件历史,超限截断,终态必留);断线/刷新 → 先状态接口校准再续流;页面刷新后恢复当前任务视图(v0.6 §9「job 内存态」妥协就此收口)
### 4.2 跨进程目标锁

- 锁粒度=目标版本目录;载体二选一(实现计划定): `<game_root>/.mcmig/locks/<dst>.lock`(pid+心跳时间戳,陈旧锁启动期检测清理)或 Win32 named mutex(ctypes,零新依赖)
- CLI migrate/swap 与 GUI 共用;获取失败=结构化报错并提示持有方(双开 GUI、GUI+CLI 并发场景)
### 4.3 窗口关闭边界与取消

- pywebview `closing` 事件: 空闲=放行退出;job 运行=**阻止关闭+显示当前任务**;页面提供「取消」→ `POST /api/jobs/{id}/cancel` → Executor 增协作式取消检查点(当前文件完成后停止分发后续动作),终态=cancelled+已完成/未完成清单,**明示「部分完成,非回滚」**
- 浏览器降级模式补「退出服务」页面入口(仅空闲可用,优雅停机);console Ctrl+C 保留

## 5. W3:能力补全

### 5.1 审阅页信息架构重构(diff 摘要并入,三步向导保留)

- 向导维持**三步**(①选版本 → ②审阅 → ③执行);v1 的独立「②总览」步与 `POST /api/diff` 独立端点**撤销**(YAGNI: 生成计划只读且廉价,无需前置闸门)——diff 摘要由 plan job 一并产出(复用 build_plan 内部 diff 管线,补 run_diff 的 notices/client_only 组装),随 done 载荷返回
- **②审阅页结构**(一页回答「有什么差异 + 会怎么处理」):

| 层级 | 内容 |
|---|---|
| 顶部摘要(决策视角) | 待迁移数量/体积、待确认(ASK)数、将覆盖(有备份)数、阻断问题(若有) |
| 默认展开 | 需要用户决定的内容(ASK 组)、兼容风险(compat 警示,替换现 log.warning 丢弃) |
| 按需查看(折叠) | mod 配对(⇄ 注记)、相同文件、目标独有文件、不迁移原因、client_only、世界提示、六桶计数(分析视角降级至此) |

- **呈现规范**: client_only=中性信息色(属性提示,非风险);`renamed(rebuilt)`/`⇄` 等实现术语→中文人话(映射进 STRINGS,如「同物异名(疑似重打包)」)
- **③结束页**: 成功/失败数、备份位置(`_conflict_backup/` 与 swap 备份)、恢复指引、PCL 下一步(既有提醒保留)

### 5.2 swap 下沉+两阶段(v2 修订)

- 下沉照 v1(`swap_preflight`/`swap_install`/`run_swap` 自 cli.py:441-585 移入 pipeline;交互参数化沿用 ask_yes 先例);对拍基准=**决策语义与退出码**,允许新增: 备份行为+一行备份位置输出
- **两阶段 API**:
  - `POST /api/swap/preflight {src,dst,new_pack}` → 只读: NeoForge 兼容清单 / src 快照存在 / extras 残留清单 / 同名冲突清单(jar 名+两侧 MD5 指纹)→ 返回 `preflight_id`(内存态,绑定输入与指纹,随单任务锁 TTL)
  - `POST /api/swap/apply {preflight_id, accept_incompat, accept_extras, overwrite_jars[]}` → **重校验指纹**(期间目录被改=拒绝,要求重预检)→ 装包 → 重扫+规划
  - v1 的 `force` 混装语义**拆分**为 `accept_incompat` / `accept_extras` 两个显式决策
- **覆盖备份**(修 cli.py:487 的 `backup_dir=None`): 同名不同内容覆盖前,目标 jar 备份至 `<game_root>/.mcmig/backups/swap/<时间戳>/`;CLI/GUI 同享
- 页面边界文案: 「确认装包后目标 mods/ 已被修改;此后取消迁移**不会自动撤销装包**(备份可在 … 找回)」

### 5.3 pywebview 窗口壳(v1 §3.5 保留,关闭边界并入 §4.3)

- 新模块 `migration/gui/app.py`: uvicorn daemon 线程 → `webview.create_window` → `webview.start()`;新入口 `mcmig-gui = migration.gui.app:main`(pyproject scripts);`mcmig gui` 浏览器模式行为不变,增 `--window` 旗标
- WebView2 缺失降级: 提示手动打开 URL + 运行时安装链接;`--noconsole` 形态下降级走日志文件+消息框;pywebview 新增运行依赖约 +2~3MB

### 5.4 随行收口(可滑移,不阻塞): modid 碰撞收口、uninstall 措辞

## 6. W4:分发闭环

### 6.1 CI(GitHub Actions)

- **`build.yml`**(push/PR/workflow_dispatch): windows-latest + Python 3.13 → `pip install -e ".[dev]"` → `ruff check .` → `pytest` → PyInstaller 双形态 → upload-artifact
- **`release.yml`**(push tags `v*`): 同上 + `SHA256SUMS.txt`(Get-FileHash) → `gh release create` 上传资产;yaml 注释中文

### 6.2 打包与受管清单(v2 修订 filelist 语义)

| 形态 | 资产名 | 内容 |
|---|---|---|
| onefile 便携 | `mcmig-gui-<ver>-win-x64.exe` | 单文件 GUI(--noconsole) |
| onedir 绿色 | `mcmig-<ver>-win-x64.zip` | `mcmig/`(mcmig.exe + mcmig-gui.exe + `_internal/` + filelist.txt) |

- **受管清单 filelist.txt = 且仅 = 程序自身文件**(CI 打包时生成);`data/`(用户数据)与安装目录内其他非清单文件**永不在更新触碰范围**
- **删除范围 = 旧受管清单 − 新受管清单**(不是「安装目录中不在新清单的一切」)
- PyInstaller spec 入库 `tools/packaging/mcmig.spec`(两 target×两形态;datas 显式收集 data yaml 与 gui html);AGENTS.md 打包命令节同步更新

### 6.3 自动更新两档(v2 重构)

- **基础档(1.0 必达)**: 检查(`fetch_latest_release` / `is_newer` 数值三元组比较,规避 "0.10"<"0.9" 字典序坑)→ 流式下载+SHA256 强制校验(失败删下载留旧版)→ 暂存 `update-staging/` → 引导「打开暂存位置」+ 手动替换说明(或直接运行新 onefile exe)。零替换风险。
- **事务档(独立验收,可滑移批次J)**: 独立更新助手小 exe(CI 一并构建):

```text
主程序: 下载+校验+暂存 → 启动助手(携带事务参数) → 自行退出
助手:   等待主程序退出 → 按受管清单差集替换(旧文件→.old 并事务登记;
        新文件就位;zip 解压路径规范化限制在安装目录内,防 zip-slip)→ 启动新版
新版:   首启确认成功 → 清理本事务登记的 .old(且仅本事务登记的)
中途失败: 保留 .old+事务登记,报错并给手动恢复指引;不承诺自动回滚
```

- **运行中文件替换不作「必然可改名」假设**(Windows 受打开句柄与共享权限影响);助手模式主程序已退出,从根上规避该问题;助手自身由下次更新自然覆盖
- 失败路径与裁剪声明同 v1: 断网/超时优雅失败、只读目录→「请手动下载」、更新不触碰 data/、无 OTA 差分、无镜像源(GitHub API 未认证限额 60 次/时,手动+低频检查足够);依赖 httpx(已有),零新增

### 6.4 更新入口

- CLI: `mcmig update [--check]`;GUI: 「关于/更新」面板(job 化复用状态机;基础档=下载进度+打开位置;事务档=分阶段事件);文案进 STRINGS

## 7. 关键决策记录(v2)

1. **C++ 裁决: 不做**(论证保留): MAA 选 C++ 的动机是 OpenCV/OCR/DL 计算密集+长驻+Android 嵌入+多语言绑定(MaaFramework AGENTS.md 自述「C++20 编写,提供 C 语言 API 供各语言绑定调用」);其 win-x64 发行包 270MB 为查证事实(GitHub Releases v6.18.0)。mcmigrator 是分钟级、I/O 密集、依赖 YAML/NBT/TOML 生态的 Windows 单平台工具;重写代价=全部核心+481 测试基线(0.10.1)+规则生态。降占用的杠杆是打包形态——**体积/启动时间/磁盘峰值为待测指标,发布前实测记录(onefile vs onedir 对比表);杀软误报为经验性风险提示,非承诺**(PyInstaller 文档支撑「onefile 需解压、启动较慢」判断)。
2. **技术栈维持 Web+pywebview——首要理由是 v0.6 资产复用**(922 行页面+14 个 server 测试+分层纪律);MAA 作旁证且措辞中性: 其 GUI 层(WPF)绑定 Windows,跨平台由 MaaMacGui/MAAUnified(Avalonia)分担——原生选型绑定平台是真实成本,但不据此论断「WPF 是失败选择」。
3. **实例态/全局态路径契约**(§3.1): 实例态随游戏走,CLI/GUI 互通;`exe/data` 只留全局配置,绿色卸载语义不变。
4. **onefile 只出 GUI 单 exe、onedir 出全家桶**(保留): 避免 onefile 双 exe 体积翻倍。
5. **更新=全量+SHA256 强制校验+两档制**: 30MB 级 OTA 差分是过度设计;MAA 发行无 checksum 是反面教材。基础档先立(零风险),事务档独立验收。
6. **swap 交互参数化+两阶段+覆盖备份**: 决策显式化(拆 force)、应用前重校验、覆盖可恢复。
7. **`mcmig gui` 默认浏览器模式不变**(保留)。
8. **(v2) 三步向导保留,diff 摘要并入审阅页**: 独立 diff 页与 `/api/diff` 撤销(YAGNI);避免六桶与 origin 两套分类重复展示。
9. **(v2) 取消语义=安全边界停止+「部分完成」明示**,不伪装回滚。
10. **(v2) 1.0 验收主轴=关键流程可靠闭环**,功能数量退居其次。

## 8. MAA 映射与裁剪清单(v2 含措辞边界)

| MAA 机制 | 我们的对应物 | 采纳状态 |
|---|---|---|
| C ABI: 句柄+JSON(`include/AsstCaller.h`) | pipeline 库 API + SSE JSON 事件流 | ✅ 已同构(v0.6) |
| 多前端薄壳(WPF / MAAUnified / maa-cli 加载同一 MaaCore;MAAUnified 自述「前端桥接 MaaCore、保持核心协议一致、参与完整发布与回归」) | CLI/GUI 平级消费 pipeline | ✅ 已有;pywebview 再加一壳(T10) |
| 数据与程序分离+数据自版本 | data/ + manifest.sha256 + doctor | ✅ 已有 |
| CI 矩阵构建 | build.yml / release.yml | ✅ 本批建 |
| 客户端更新: staging + removelist + `.old` + 失败保留旧版 | 基础档+事务档(受管清单差集=removelist 正向版) | ✅ 裁剪采纳 |
| OTA 差分包/多 CDN/MirrorChyan/Sparkle | — | ❌ 过度设计 |
| 无 checksum 的发行 | SHA256SUMS.txt 强制校验 | 🔄 反其道(必须做) |
| interface.json 声明式界面契约 | rules/profiles YAML(只读) | ⏸ 远期与 .mcmigpack 合流 |

**证据锚定**: MAA 证据取自其仓库 `archive/master-v1` 分支(2026-10-01 调研时默认分支已是 dev-v2,v1 稳定线归档于该分支),引用以**文件路径+分支**锚定,未逐一固定 commit SHA——后续引用者按路径复核;本仓相关断言(§0/§2.2)均以 file:line 落到本仓代码。

## 9. 测试策略(v2)

- **preflight(T2)**: 四道阻断×force 降级×CLI 输出对拍(文案保持)
- **一致性(T3)**: save 失败注入(mock OSError)→ persisted=false 不可执行;plan_id 失配阻断;CLI plan 保存失败→非零退出
- **状态机(T4)**: 五态转移;广播(双 TestClient 订阅各自收全量);刷新恢复;cancel 检查点(当前文件完成后停)
- **跨进程锁(T5)**: 双进程夹具冒烟+陈旧锁清理
- **swap(T8/T9)**: 两阶段指纹重校验(预检后篡改目录→apply 拒绝);覆盖备份断言(备份目录内容);CLI 对拍(允许新增备份输出行)
- **workdir(T1)**: 首跑欢迎态(未配置不抛错);根目录切换后路径重建;旧布局回退
- **updater(T13/T14)**: 基础档 mock httpx+SHA256 失败路径;事务档=受管清单差集/zip-slip 攻击用例/.old 仅清本事务登记/**真实打包 exe 集成冒烟(tmp_path 普通文件不能模拟运行时占用,以测试 repo 或 draft Release 演练)**
- **页面手测清单增补**: 关窗边界/取消/刷新恢复/两阶段 swap/WebView2 降级
- 全批测试增量估计 **+60~90**

## 10. 验收标准(v2,可靠性主轴)

1. **首跑**: 绿色 exe 无配置启动 → 欢迎页 → 选根目录 → 进入向导(不再启动失败)
2. **防护等价**: GUI migrate 前置四道防护,与 CLI 同源同语义(结构化 blocks/warnings)
3. **一致性**: plan 持久化失败→不可执行;migrate 携带 plan_id 且失配即阻断;切换根目录/重扫/装包后旧审阅失效
4. **任务恢复**: 刷新页面恢复当前任务;断线重连以状态接口校准;终态五态可查询
5. **并发**: 双开 GUI 或 GUI+CLI 同目标 → 后来者被跨进程锁拒绝并提示持有方
6. **窗口边界**: 写盘中关窗被阻止;取消在安全边界停止并明示「部分完成,非回滚」;浏览器模式有退出入口
7. **swap**: 两阶段+指纹重校验+覆盖有备份+「装包不可自动撤销」明示;CLI 行为语义与下沉前等价
8. **分发**: tag → Release 自动产出三资产+SHA256SUMS(certutil 一致);更新基础档可用(检查/下载/校验/打开位置);事务档独立验收通过——若滑移批次J,本条标注「事务档未交付,基础档可用」
9. 能力项: 审阅页摘要/按需明细/compat 警示可见;client_only 中性色;术语中文化;独立窗口双击即用且关闭边界正确
10. 全量测试绿 + ruff 干净;版本线 0.10.1 →(批次H)0.11.0 →(批次I)**1.0.0**;发布附 onefile/onedir 体积与启动时间实测记录

## 11. 任务分解 v2(四波,可按波切 plan)

**W1 上下文与防护(地基,先行)**:
- T1 路径契约+首跑欢迎页+根目录切换重建(实例态进 game_root/.mcmig,规则双轨消解)
- T2 共享执行预检 preflight_execute(CLI/GUI 接线+对拍)
- T3 plan 持久化失败阻断+plan_id 审阅上下文

**W2 任务模型可靠性**:
- T4 job 状态机+GET /api/jobs/{id}+SSE 广播+刷新恢复
- T5 跨进程目标锁
- T6 窗口关闭边界+取消语义+浏览器退出入口

**W3 能力补全**:
- T7 审阅页 IA 重构(diff 摘要并入/决策视角分组/compat 警示/术语中文化/client_only 中性色/结束页恢复指引)
- T8 swap 下沉+两阶段 API+覆盖备份(CLI 对拍)
- T9 swap 页面(两阶段交互+边界文案)
- T10 pywebview 窗口壳(closing 边界在 T6 之上)

**W4 分发闭环(相对独立,可并行/倒置)**:
- T11 打包入库(mcmig.spec 双形态+受管清单 filelist 生成+本地构建验证)
- T12 CI build.yml + release.yml
- T13 更新基础档(updater.py: check/download/verify/open)
- T14 更新事务档(助手进程+受管清单差集+.old 事务登记+zip-slip 防护+真实 exe 集成冒烟)——独立验收,可滑移批次J
- T15 收口: README 双语/AGENTS.md(打包节+「是否做 GUI」过时条目)/updater REPO 与 remote 校对/STRINGS/版本 1.0.0/占用实测记录

依赖: **W1→W2→W3 顺序实施**(W1 是路径与防护地基,W1 内 T1 先行——T2 的快照定位与 T3 的计划落点都依赖路径契约);W4 不碰 GUI 可随时并行(仅 T14 依赖 T11)。

## 12. 排期与版本

- **先实现批次H**(0.11.0,spec 466d2de 已定稿)再启动批次I(维护者 2026-10-01 确认);批次H 的 build_ruleset 搬迁保留 re-export,GUI 消费不断裂
- 批次I 完成版本 **1.0.0**(可靠性闭环+分发闭环=社区就绪)
- plan 切分建议: W1+W2 合一份「可靠性基座」plan,W3 一份「能力补全」plan,W4 一份「分发闭环」plan(共三份,按波推进)
