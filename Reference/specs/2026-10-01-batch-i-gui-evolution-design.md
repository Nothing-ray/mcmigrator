# 批次I 设计规格:GUI 演进与分发闭环(v0.6 → 1.0.0)

> 状态: 评审修订 v3(第二轮评审收编: 补四份契约——审阅有效性/锁范围与时序/任务恢复与取消/更新生命周期) | 日期: 2026-10-02 | 前置: 批次H spec(466d2de,待实现,目标 0.11.0);GUI v0.6 已交付(`2026-09-04-gui-design.md`)
> 调研依据: MAA 架构调研(2026-10-01,证据锚定见 §8);Electron/Velopack/Tauri/SSE 标准的借鉴边界见 §8.1;两轮评审全部代码级断言已对照本仓核实(§0)
> 决策确认: 2026-10-01 与维护者对齐(排期先H后I / 维持 Web+pywebview / 首批五项 / CI 双形态打包);v2 确立可靠性主轴;v3 补齐使验收承诺可被机制保证的契约层

## 0. 评审对照表(意见 → 核实 → 处置)

### 第一轮(v2 收编,8 项,措辞随 v3 修正)

| # | 意见 | 核实结果 | 处置 |
|---|---|---|---|
| 1 | GUI 缺 CLI 执行防护 | ✅(v3 修正表述)CLI 四道: executed_at(cli.py:613)/快照过期(:620-627)/版本目录存在(:628-632)/疑似游戏占用(:633-636);GUI **并非零防护**——API 层已有目录存在检查(server.py:624 `_ensure_version_dirs`),缺的是其余三道与共享化 | §3.2 共享预检;「GUI 防护≥CLI 等价」进验收 |
| 2 | 计划保存失败被吞→审新执旧 | ✅ pipeline.py:287-291 + server.py:321 | §3.3 |
| 3 | swap 缺两阶段;覆盖无备份 | ✅ cli.py:487 `backup_dir=None` | §5.2 |
| 4 | 关窗杀 daemon 线程 | ✅ server.py:122 | §4.3 |
| 5 | SSE 单队列分摊;无状态接口;锁仅进程内 | ✅ server.py:430-444;无 GET /api/jobs/{id};JobStore 进程内 | §4.1/4.2 |
| 6 | IA 贴近玩家决策 | 设计判断,采纳 | §5.1 |
| 7 | 首跑抛错;根目录切换不重建;规则双轨 | ✅ workdir.py:177-185;server.py:599;规则双轨 | §3.1 |
| 8 | 更新缺事务闭环;绝对化措辞 | 技术判断,采纳 | §6.3/§7/§8 |

### 第二轮(v3 收编,7 项+杂项)

| # | 意见 | 核实结果 | 处置 |
|---|---|---|---|
| 1 | plan_id 推导不成立(重扫不改计划文件/根目录复制/磁盘改动/规则变化均不失效) | ✅ v2 §3.3 承诺超出机制保证: scan 只写快照不写计划,指纹不变;逻辑缺陷属实 | §3.3 重写为「审阅有效性契约」(计划身份与执行前置条件分离) |
| 2 | force 扩权: 目录缺失在 CLI 不可强制(cli.py:628-632 无 force 通道),v2 笼统降级会削弱防护 | ✅ 属实 | §3.2 阻断分类表(可强制/不可强制分道);`_game_running`(cli.py:421-431,r+b 试开两文件,OSError 即真)改「疑似占用」语义 |
| 3 | 锁只锁目标漏 A→B/B→C 交叉;检查-取锁-执行有竞争窗 | ✅ 设计缺口属实(乙读甲写中的 B) | §4.2 重写为「锁范围与时序契约」 |
| 4 | 状态查询+SSE 衔接有终态漏接窗口;历史截断/慢订阅者未定义 | ✅ 设计缺口属实 | §4.1 增补 revision/Last-Event-ID 衔接协议 |
| 5 | 路径契约自相矛盾(实例态统一 vs 兼容模式不变);CLI 规则入口传 cwd/.mcmig | ✅ cli.py:402 `mcmig_dir=cwd/".mcmig"` 属实;v3 契约统一两模式实例态 | §3.1 重写;T1 覆盖 CLI 接线 |
| 6 | 取消未覆盖扫描/装包/下载;崩溃恢复与断线恢复未区分;swap 新增文件无备份可查 | ✅ 设计缺口属实 | §4.3 重写为「任务恢复与取消契约」(can_cancel/中间态/落盘 journal/中断≠取消) |
| 7 | 基础档允许暂存目录直跑=配置丢失+嵌套暂存;事务档助手生命周期缺口 | ✅ workdir 绿色模式按 exe 目录定位 data(workdir.py:168),暂存运行必错位 | §6.3 重写为「更新生命周期契约」 |
| 杂 | 磁盘预检未计 ASK 命中;swap 备份跨卷;W4 非独立;资产计数歧义;体积为估算 | ✅ pipeline.py:331 仅计 COPY 属实;其余属实 | §3.2/§6.2/§11 相应修正 |

> file:line 为核实时快照,以函数名为准。

## 1. 目标与非目标

**目标**(可靠性先于功能):
0. **可靠性基座**: 执行防护 CLI/GUI 等价、**审阅有效性**、任务状态可恢复、**锁范围与时序**、窗口关闭边界——1.0 验收主轴,由 §3.3/§4.1/§4.2/§4.3 四份契约保证
1. diff 能力进 GUI(审阅页摘要+按需明细形态)
2. swap 换包 GUI 化(两阶段: 只读预检 → 确认应用)
3. pywebview 独立窗口(含关闭边界与降级)
4. CI 自动构建发布(双形态+SHA256)
5. 自动更新(基础档 1.0 必达;事务档独立验收,可滑移)

**非目标**: .mcmigpack 封包、规则编辑器、页面多语言、OTA 差分、多镜像源、PySide6/Tauri 重评、C++ 重写(§7.1)、macOS/Linux 发行、自动回滚(中途中止保留备份/事务登记/journal,给手动恢复指引,不承诺自动回滚)。

## 2. 现状基线与总体架构

### 2.1 已有地基(沿用,不重构)

```
页面(单文件 HTML+原生 JS)──SSE(广播)/fetch──▶ gui/server.py ──▶ pipeline.py ──▶ core
cli.py ─────────────────────────────────────────────┘
```

- v0.6 三步向导已交付,14 个 server 测试;报告对象全结构化 dataclass,rich 仅 CLI 渲染层
- `Executor.execute(progress_cb=...)` 逐文件回调;`execute_migration` 已共享 tmp 清理+磁盘预检(pipeline.py:326-332)
- 绿色布局与 data 完整性校验(manifest.sha256 + doctor)沿用;分层纪律(gui 不碰 cli 私有函数、核心不感知 HTTP)沿用——与 MAA「核心是库、GUI 是薄壳、窄事件协议」同构

### 2.2 已核实的缺口(本批消化)

| 缺口 | 证据 | 归属 |
|---|---|---|
| GUI 缺共享执行防护(API 层已有目录检查,缺其余三道与共享化) | server.py:624 vs cli.py:613-636 | T2 |
| 磁盘预检未计 ask_yes 命中的 ASK 动作;swap 备份与目标可能异卷 | pipeline.py:331 | T2 |
| plan 保存失败被吞→审新执旧 | pipeline.py:287-291 + server.py:321 | T3 |
| 绿色首跑抛错(页面未起) | workdir.py:177-185 | T1 |
| 根目录切换不重建上下文 | server.py:599 | T1 |
| 规则文件双轨(CLI 入口也传 cwd/.mcmig) | cli.py:402;GUI 侧 exe/data/<slug>/rules.yaml | T1 |
| SSE 单队列消费+无状态接口 | server.py:430-444 | T4 |
| 单任务锁仅进程内 | JobStore | T5 |
| 写盘中关窗杀 daemon 线程 | server.py:122 | T6 |
| swap 覆盖无备份 | cli.py:487 | T8 |
| diff/mod_pairs/client_only 未进 GUI;compat 警告只进 stderr | 批次F 沿挂 | T7 |
| swap 编排滞留 cli.py | cli.py:441-585 | T8 |
| CI 缺失 | README 宣称 vs `.github/` 不存在 | T11-T12 |
| modid 碰撞、uninstall 措辞 | 批次E 沿挂 | T15 随行(可滑移) |

### 2.3 目标架构(增量)

同 v2: 页面/SSE 广播/状态接口 → server → pipeline(新增 preflight_execute/run_swap/审阅有效性守卫) → core;mcmig-gui 入口+pywebview 壳;cLI 薄壳化;jobs 状态机+跨进程实例锁;GitHub Actions → Release 资产(受管清单) → updater 消费。

## 3. W1:上下文与防护基座(先行,全批地基)

### 3.1 实例上下文与路径契约(v3 重写,消除 v2 自相矛盾)

- **实例态**(快照、计划、**规则 rules.yaml**、swap 备份、job journal、实例锁)统一 `<game_root>/.mcmig/`——**frozen 与源码模式同址**;差异仅**全局配置**位置(frozen=`exe/data/config.toml`;源码= `cwd/.mcmig/config.yaml` 仅存全局项)
- 旧位置(`exe/data/<slug>/`、源码 `cwd/.mcmig/` 实例态)**只读回退**+命中提示整体迁移;**两处规则并存时明确选择并提示,不暗中混合**
- **T1 覆盖 CLI 接线**: `cli.py:402` 的 `mcmig_dir=cwd/.mcmig`(用户规则入口)改为 game_root 锚定,与 GUI 同一契约
- **上下文不可在运行中漂移**: 每个 job 启动时**捕获不可变实例上下文**(game_root/快照/计划/规则路径定格为启动时值);修改全局根目录只影响后续 job,不改变运行中任务的路径——比「按最新 game_root 惰性派生」更不易串实例
- 首跑欢迎页: `create_app` 允许未配置 game_root 状态启动(不再 app 创建期抛 WorkdirError);选定根目录后重建实例上下文

### 3.2 共享执行预检 `pipeline.preflight_execute`(v3 修订 force 语义)

```python
@dataclass(frozen=True)
class PreflightBlocker:   # code: plan_executed|snapshot_stale|version_dir_missing|game_maybe_running|...
    code: str; message: str
@dataclass(frozen=True)
class PreflightWarning:
    code: str; message: str
def preflight_execute(plan, game_root, src, dst, *, decisions: ExecutionDecisions | None = None
                      ) -> tuple[list[PreflightBlocker], list[PreflightWarning]]
```

- 收编 CLI 四道(cli.py:613-636);**阻断分类,不再笼统 force 降级**:

| 阻断类型 | 处理 |
|---|---|
| 版本目录缺失 / 计划指纹不匹配(§3.3) / 路径越界 / 锁冲突(§4.2) | **不可强制**(无降级通道,与 CLI 现状一致: cli.py:628-632 无 force) |
| 已执行计划的明确重跑 | 可按**明确决策**继续(CLI `--force` 先例语义,GUI=显式勾选+确认) |
| 快照过期 | GUI 优先引导**重新生成计划**;显式强制需标注风险 |
| 疑似游戏占用 | **独立警告与独立决策**,不与其他强制项合并,不解释为「已确认安全」 |

- **疑似占用语义**(修正 `_game_running` cli.py:421-431 的定位): 检测手段=尝试读写打开 `usercache.json`/`options.txt`,存在两类局限——权限错误可能误报为占用、文件未锁不证明游戏未运行;命名与文案一律「疑似占用」,提示**退出源与目标实例**
- **磁盘预检增强**(修 pipeline.py:331 现状): 需求量计入 ask_yes 命中的 ASK 动作;swap 备份目标与 dst 异卷时分别校验
- CLI `_cmd_migrate` 改薄壳调用(输出文案保持,对拍);GUI migrate job 前置调用,blocks→error 事件,warnings→警示条;**验收级: GUI 执行防护 ≥ CLI 等价**

### 3.3 审阅有效性契约(v3 重写,替代 v2「plan_id 自然失效」)

v2 的推导不成立(重扫只写快照不写计划、根目录复制、磁盘改动、规则变化均不改变计划指纹)。v3 将两个概念分离:

- **计划身份 `plan_fingerprint`**: 对**完整、规范化的可执行计划**(`MigrationPlan` 规范化序列化后哈希)计算——含义=「这份计划文件的内容」,不承诺「审阅时看到的世界仍是这份计划的世界」
- **执行前置条件 `ExecutionGuards`**(审阅时随计划一并签发,执行前逐项比对,失配→阻断并要求重新审阅):
  - 实例身份: 规范化 game_root 路径(resolve 消 junction/别名)
  - 双侧快照指纹: src/dst 快照**文件内容哈希**(重扫即变)
  - 规则指纹: 规则各层来源与内容(规则变化即失效)
  - 迁移模式: modpack_swap 等开关
- **磁盘边界(明示限制)**: 快照一致 ≠ 磁盘未变。可行的复核: 执行器逐文件 identical 短路+MD5 校验(既有);size 代理的 bulk/mods 条目**不可完整复核**,在执行摘要中说明此限制
- **执行对象同一性**: 前置条件校验通过后,执行**同一个已加载的计划对象**(GUI 持内存对象,或单次加载+指纹校验),不再按文件名二次读取(封死 server.py:321 的「按名重载」路径)
- plan 持久化失败 → `persisted=false` → 不可进入可执行状态(pipeline 不再吞 OSError;CLI plan 同步升级为非零退出,行为变更记入 CLI 变更清单)
- **验收用例(v3 新增)**: 计划 JSON 不变,但发生重扫/规则变化/目标文件变化 → 执行前置条件失配,旧审阅仍被阻断

## 4. W2:任务模型可靠性

### 4.1 状态查询与广播的衔接协议(v3 增补)

- Job 状态机: `running|succeeded|partial_failed|failed|cancelled|interrupted`(interrupted=进程崩溃后由 journal 判定,见 §4.3)
- `GET /api/jobs/{id}` → `{status, phase, revision, progress, summary, results, error}`——**results 含计划摘要、备份位置、失败明细**,非仅计数;`revision` 单调递增
- **衔接协议(封死终态漏接窗口)**: 每条 SSE 事件携带序号(SSE 标准 `id:` 字段);订阅可指定「从 revision 之后继续」(沿用标准 `Last-Event-ID` 重连语义,整页刷新由应用保存 job_id 游标恢复);「查状态→完成→订阅」竞态由序号衔接消除
- **历史截断**: 事件历史上限,超限订阅返回截断标志→客户端重读完整状态接口
- **慢订阅者**: 每订阅者独立有界队列,满则断开并提示重读状态,**不反压文件复制线程**
- 页面刷新经保存的 job_id 或「当前任务」接口定位
- 浏览器模式补「退出服务」入口(仅空闲);console Ctrl+C 保留

### 4.2 锁范围与时序契约(v3 重写)

- **锁对象=操作涉及的源与目标实例(双锁,排他)**,按规范化身份(resolve 后路径)**排序获取**防死锁——覆盖「甲 A→B 写 B、乙 B→C 读 B」交叉场景(v2 只锁目标漏此)
- **时序**: 获取全部锁 → **重验执行前置条件/预检指纹**(封死「检查-取锁-执行」竞争窗)→ 执行(覆盖 tmp 清理/写盘/结果登记)→ 释放
- scan/plan 写共享快照与计划文件时,遵循同一实例锁纪律(读他人快照=共享读,写=排他;实现计划细化)
- 目录别名/junction 规范化后落同一锁键,不可绕过同一实例的锁
- **载体**: Windows 发行版优先系统 mutex(ctypes,免自维护心跳租约);**abandoned mutex 只表示前持有者异常退出**——仍须检查遗留 job journal(§4.3),标记「中断,待核对」,不当干净任务
- CLI migrate/swap 与 GUI 共用;获取失败=结构化报错提示持有方
- **验收用例**: A→B 与 B→C 并发,乙获取 B 锁失败被拒

### 4.3 任务恢复与取消契约(v3 重写)

- **三类情形分治**:
  - **页面断线/刷新**: 内存态恢复足够(§4.1 衔接协议)
  - **用户取消**: 协作式——job 声明 `can_cancel`;取消请求后先显示「正在停止,等待当前单元完成」**中间态**,真正停止后才进入终态 `cancelled`;不可取消的阶段(如扫描)不显示取消按钮,不提供实际无效的入口
  - **进程崩溃**: 依赖**落盘 job journal**(写入点: `<game_root>/.mcmig/jobs/`): swap 记录本次**新增/覆盖文件清单、备份位置、完成进度**;迁移记录已完成动作;崩溃后重启标记 `interrupted`(≠cancelled),提示「中断,待核对」与核对指引(如 swap 新增 jar 清单——新增文件无旧备份,仅靠备份目录无法反推应删哪些)
- 取消检查点覆盖全部长任务类型: migrate(逐文件,既有)、swap 装包(逐 jar)、更新下载(分块)——扫描阶段不支持取消(明示)
- pywebview `closing` 事件: 空闲放行;job 运行阻止+显示当前任务+取消入口(§4.3 语义)
- 不做自动回滚(边界重申)

## 5. W3:能力补全

### 5.1 审阅页信息架构(同 v2)

- 三步向导保留(①选版本→②审阅→③执行);独立 diff 页与 `/api/diff` 撤销(YAGNI)——diff 摘要由 plan job 一并产出随 done 载荷返回
- ②审阅页: 顶部摘要(待迁移数量/体积、待确认数、将覆盖(有备份)数、阻断问题)/默认展开(ASK 组、compat 警示)/按需折叠(mod 配对、相同文件、目标独有、不迁移原因、client_only、世界提示、六桶计数)
- 呈现规范: client_only=中性信息色;`renamed(rebuilt)`/`⇄` 等术语→中文人话(STRINGS 映射);**「已下载,尚未应用」类两态展示**(借鉴 Electron autoUpdater,用于更新面板)
- ③结束页: 成功/失败数、备份位置、恢复指引、PCL 下一步

### 5.2 swap 下沉+两阶段(同 v2,备份位置随 §3.1 契约)

- 下沉(`swap_preflight`/`swap_install`/`run_swap` 自 cli.py:441-585 移入 pipeline;交互参数化);对拍基准=决策语义与退出码,允许新增备份行为+一行备份位置输出
- 两阶段: `POST /api/swap/preflight`(只读: 兼容清单/快照存在/extras/同名冲突+两侧 MD5 指纹→`preflight_id` 绑定输入与指纹)→ `POST /api/swap/apply`(preflight_id+`accept_incompat`+`accept_extras`+`overwrite_jars[]`,**重校验指纹**,期间目录被改=拒绝)→ 装包 → 重扫+规划;force 混装语义拆分
- **覆盖备份**: 同名不同内容覆盖前备份目标 jar 至 `<game_root>/.mcmig/backups/swap/<时间戳>/`;**装包过程写 job journal**(新增/覆盖/备份位置,§4.3)
- 页面边界文案: 「确认装包后目标 mods/ 已被修改;此后取消迁移不会自动撤销装包(备份可在 … 找回)」

### 5.3 pywebview 窗口壳(同 v2)

新模块 `migration/gui/app.py` + 入口 `mcmig-gui = migration.gui.app:main`;`mcmig gui` 浏览器模式不变+`--window`;WebView2 缺失降级(提示+安装链接;--noconsole 形态走日志+消息框);pywebview 新增依赖(估算 +2~3MB,非验收前提)。

### 5.4 随行收口(可滑移): modid 碰撞、uninstall 措辞

## 6. W4:分发闭环

### 6.1 CI(同 v2)

`build.yml`(push/PR/dispatch: windows-latest+Python 3.13→install→ruff→pytest→双形态构建→artifact);`release.yml`(tag `v*`: 同上+SHA256SUMS→`gh release create`);注释中文。

### 6.2 打包与受管清单(同 v2,表述修正)

- 发行物=**两种发行包+一份校验清单**: `mcmig-gui-<ver>-win-x64.exe`(onefile GUI 单文件)、`mcmig-<ver>-win-x64.zip`(onedir 全家桶: mcmig.exe+mcmig-gui.exe+`_internal/`+filelist.txt,事务档助手入此包受管清单)、`SHA256SUMS.txt`
- 受管清单=且仅=程序自身文件;删除范围=旧受管清单−新受管清单;`data/` 与清单外文件永不被更新触碰
- PyInstaller spec 入库 `tools/packaging/mcmig.spec`;AGENTS.md 打包命令节同步更新

### 6.3 更新生命周期契约(v3 重写)

- **基础档(1.0 必达)**: 检查(`fetch_latest_release`/`is_newer` 数值比较)→ 下载流式+SHA256 强制校验 → 暂存 → **只提供「打开位置+替换说明」**。**禁止从暂存目录直接运行新版**(绿色模式全局配置按 exe 目录定位,暂存运行=读到空配置+产生嵌套暂存);替换回原目录后再启动。GUI 两态展示「已下载,尚未应用」。
- **事务档(独立验收,可滑移批次J)**,生命周期——**适用范围: 先覆盖 onedir 形态**(助手已在其受管清单内);onefile 为便携形态,事务档不适用,保持基础档(单文件重下载替换成本≈0,与手动替换等效):

```text
主程序: 下载+校验+暂存 → 启动助手(先自复制到临时目录运行,独立于待替换文件)→ 自行退出
助手:   获取安装目录级更新锁(覆盖 GUI/CLI 等所有共享 _internal 的进程;
        「主程序退出」不充分,等锁释放)→ 按受管清单差集替换(旧→.old 事务登记;
        zip 解压路径规范化限制在安装目录内,防 zip-slip)→ 启动新版
新版:   确认成功(条件: manifest.sha256 自检通过+版本号匹配+GUI 就绪信号;
        超时未确认=保留 .old 与事务登记)→ 清理且仅清理本事务登记的 .old
中途失败: 保留备份+事务登记,报错并给手动恢复指引;不承诺自动回滚
```

- **T14 动工前设评估门**: 先评估 **Velopack 复用成本**(独立 Update.exe/等待退出/更新互斥/稳定入口布局——其 Windows 布局「稳定入口与实际程序目录分离」正是暂存运行问题的系统解),评估结论留档后再决定自研助手;本 spec 的生命周期契约无论自研或复用均适用
- **签名边界(已知限制)**: 同源 SHA256SUMS 只证明「资产与清单一致」,不证明发布者身份;1.0 将此记录为已知限制,自动应用阶段(事务档)评估更新签名(借鉴 Tauri updater 的来源验证)
- 失败路径与裁剪: 断网/超时优雅失败;只读目录→「请手动下载」;更新不触碰 data/;无 OTA 差分、无镜像源(未认证限额 60 次/时,手动+低频足够);依赖 httpx(已有);体积「约 30MB」为估算,非验收前提

### 6.4 更新入口

CLI `mcmig update [--check]`;GUI「关于/更新」面板(job 化;基础档=下载进度+打开位置+两态展示;事务档=分阶段事件);文案进 STRINGS。

## 7. 关键决策记录

1. **C++ 裁决: 不做**(同 v2 论证与措辞边界: 270MB 为查证事实;体积/启动/磁盘峰值为待测指标,发布前实测;杀软误报为经验性风险提示非承诺)
2. **技术栈维持 Web+pywebview——首要理由=v0.6 资产复用**;MAA 旁证措辞中性(不作「WPF 失败」论断)
3. **实例态/全局态路径契约**(§3.1 v3: 两模式同址,差异仅全局配置;job 捕获不可变上下文)
4. **onefile 只出 GUI 单 exe、onedir 出全家桶**
5. **更新=全量+SHA256+两档制**(基础档禁暂存直跑)
6. **swap 交互参数化+两阶段+覆盖备份**
7. **`mcmig gui` 默认浏览器模式不变**
8. 三步向导保留,diff 摘要并入审阅页(独立 diff 页与 /api/diff 撤销)
9. 取消=安全边界停止+「部分完成」明示,不伪装回滚;崩溃=interrupted+journal,≠cancelled
10. **1.0 验收主轴=关键流程可靠闭环**
11. **(v3) 计划身份与执行前置条件分离**: 指纹只证内容,有效性由 guards(实例/快照/规则/模式)保证
12. **(v3) 阻断分类取代笼统 force**: 不可强制项(目录缺失/指纹失配/越界/锁冲突)无降级通道
13. **(v3) 实例锁=源+目标双锁+排序获取+持锁重验**;系统 mutex 为载体,abandoned≠干净
14. **(v3) 更新生命周期=安装级锁+助手自复制+明确成功条件+超时保留备份**;Velopack 评估门先行

## 8. MAA 映射与裁剪清单(同 v2)+ 借鉴边界

### 8.1 其他成熟方案借鉴(v3,只借边界不换栈)

| 参考 | 借鉴点 | 落点 |
|---|---|---|
| Electron autoUpdater | 「下载完成」与「退出安装」分离 | 更新面板两态「已下载,尚未应用」(§5.1/§6.3) |
| Velopack | 独立 Update.exe、等待退出、更新互斥、稳定入口与程序目录分离 | T14 评估门(§6.3);稳定入口思想 |
| Tauri updater | 更新包签名验证来源 | 事务档评估签名;1.0 记录同源 SHA256 的局限 |

### 8.2 MAA 映射(同 v2)

C ABI 窄契约↔pipeline+SSE(已同构)/多前端薄壳(pywebview 再加一壳)/数据程序分离(已有)/CI 矩阵(本批建)/客户端更新 staging+removelist+.old(裁剪采纳为受管清单差集)/OTA 差分与多 CDN(不采纳)/无 checksum(反其道)/interface.json(远期)。证据锚定: MAA 引用以 `archive/master-v1` 分支+文件路径(2026-10-01 调研,默认分支已 dev-v2),未逐一固定 commit SHA;本仓断言以 file:line 锚定。

## 9. 测试策略(v3)

- **preflight(T2)**: 阻断分类×决策矩阵;CLI 输出对拍;磁盘预检含 ASK 命中+异卷备份
- **审阅有效性(T3)**: 计划 JSON 不变+重扫/规则变化/目标文件变化→仍阻断(guards 失配三类);快照指纹变化检测;save 失败注入→persisted=false 不可执行;执行对象同一性(不再按名重载)
- **状态衔接(T4)**: 「查状态→完成→订阅」竞态用例(revision/Last-Event-ID 衔接);历史截断→重读状态;慢订阅者有界队列不阻塞复制;崩溃→interrupted 标记+journal 内容(swap 新增/覆盖/备份清单)
- **锁(T5)**: A→B 与 B→C 并发拒;排序获取无死锁(双锁交叉用例);持锁后指纹重验;junction 归一化同锁键;abandoned→interrupted
- **取消(T6)**: can_cancel 矩阵;取消中间态→终态;扫描阶段无取消入口
- **swap(T8/T9)**: 两阶段指纹重校验(预检后篡改→apply 拒);覆盖备份断言;journal 断言;CLI 对拍(允许新增备份输出)
- **workdir(T1)**: 首跑欢迎态;根目录切换后新 job 用新上下文、运行中 job 不漂移;旧位置只读回退;双规则并存提示;CLI 接线等价
- **updater(T13/T14)**: 基础档(含**暂存直跑被阻止**的断言);事务档=受管清单差集/zip-slip/助手自复制/安装级锁等待/成功条件与超时保留/真实打包 exe 集成冒烟
- 页面手测清单增补: 关窗边界/取消/刷新恢复/两阶段 swap/降级/更新两态
- 全批测试增量估计 **+70~100**

## 10. 验收标准(v3)

1. **首跑**: 绿色 exe 无配置启动→欢迎页→选根目录→进入向导
2. **防护等价**: GUI migrate 前置共享预检,阻断分类与 CLI 同源同语义;磁盘预检含 ASK 命中
3. **审阅有效性**: 计划 JSON 不变但重扫/规则变化/目标文件变化→旧审阅被阻断;plan 持久化失败→不可执行;执行同一计划对象
4. **任务衔接**: 查状态与订阅 SSE 无终态漏接(revision 序号衔接);刷新恢复;状态接口含备份位置与失败明细
5. **并发**: A→B 与 B→C 并发被拒(双实例锁);双开 GUI/GUI+CLI 同目标被拒并提示持有方
6. **窗口与取消**: 写盘中关窗被阻止;可取消任务经「正在停止」中间态进入 cancelled;崩溃后标记 interrupted(≠cancelled)并给出 journal 核对指引
7. **swap**: 两阶段+指纹重校验+覆盖有备份+journal+「装包不可自动撤销」明示;CLI 行为语义等价
8. **分发**: tag→两种发行包+一份校验清单自动产出(certutil 一致);基础档可用且**暂存直跑被阻止**;事务档独立验收(助手自复制/安装级锁/成功条件/超时保留)——滑移批次J 时本条标注「事务档未交付,基础档可用」
9. 能力项: 审阅页摘要/按需明细/compat 警示;client_only 中性色;术语中文化;独立窗口+关闭边界
10. 全量测试绿+ruff 干净;版本线 0.10.1→(批次H)0.11.0→(批次I)**1.0.0**;发布附 onefile/onedir 体积与启动时间实测记录

## 11. 任务分解(四波,可按波切 plan)

**W1 上下文与防护(地基,先行)**:
- T1 路径契约 v3(两模式实例态同址 game_root/.mcmig+CLI 规则接线 cli.py:402+首跑欢迎页+job 不可变上下文)
- T2 共享执行预检(阻断分类表+疑似占用语义+磁盘预检增强;CLI/GUI 接线+对拍)
- T3 审阅有效性契约(plan_fingerprint+ExecutionGuards+persisted 阻断+执行对象同一性)

**W2 任务模型可靠性**:
- T4 状态机+衔接协议(revision/Last-Event-ID/截断/有界队列/完整结果载荷)
- T5 实例锁契约(双锁排序+持锁重验+mutex 载体+abandoned→interrupted)
- T6 恢复与取消契约(can_cancel/中间态/journal/interrupted 标记+窗口关闭边界)

**W3 能力补全**:
- T7 审阅页 IA 重构(diff 摘要并入/决策视角/术语中文化/client_only 中性色/两态展示/结束页)
- T8 swap 下沉+两阶段+覆盖备份+journal(CLI 对拍)
- T9 swap 页面(两阶段交互+边界文案)
- T10 pywebview 窗口壳(closing 边界基于 T6)

**W4 分发闭环(依赖修正: 更新面板依赖 T4,更新退出依赖 T6,T14 依赖 T13+资产契约;T11-T13 可先行)**:
- T11 打包入库(mcmig.spec 双形态+受管清单+本地验证)
- T12 CI build.yml+release.yml
- T13 更新基础档(check/download/verify/打开位置;**阻止暂存直跑**)
- T14 更新事务档(**Velopack 评估门先行**+助手自复制+安装级锁+成功条件+zip-slip+真实 exe 冒烟)——独立验收,可滑移批次J
- T15 收口: README 双语/AGENTS.md(打包节+过时条目)/REPO 校对/STRINGS/版本 1.0.0/占用实测/签名局限记录

依赖: **W1→W2→W3**(W1 内 T1 先行);W4 的 T11-T13 可与 W1-W3 并行,T13 的 GUI 面板部分需 T4 后接入,T14 需 T13+资产契约。

## 12. 排期与版本

- **先实现批次H**(0.11.0,spec 466d2de 已定稿)再启动批次I(维护者确认);批次H 的 build_ruleset 搬迁保留 re-export
- 批次I 完成版本 **1.0.0**(可靠性闭环+分发闭环=社区就绪)
- plan 切分: 三份——「可靠性基座」(W1+W2)/「能力补全」(W3)/「分发闭环」(W4);按波推进
