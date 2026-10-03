# 批次I-W4 分发闭环设计(打包双形态/CI 自动发布/更新基础档)1.0.0

- 日期: 2026-10-03
- 状态: 开工设计通过(v3,评审条件已收编)
- 上游: `Reference/specs/2026-10-01-batch-i-gui-evolution-design.md`(批次I 总 spec,§6 分发闭环为本批伞形依据)
- 基线: 本地 main `0535fef`(0.12.0,743 passed + 2 skipped);W1-W3 已收官
- 里程碑: 批次I 收口 = **1.0.0**(社区分发就绪)

## 1. 目标 / 非目标

### 目标

1. **打包入库**: PyInstaller spec 进仓(`tools/packaging/mcmig.spec`),在锁定构建环境下可复现产出双形态发行物(口径见 §4.1.2)
2. **CI 自动发布**: tag `v*` → GitHub Actions 自动构建并发布 Release(双资产 + SHA256SUMS.txt)
3. **更新基础档(1.0 必达)**: `mcmig update [--check]` + GUI「关于/更新」面板——检查→流式下载→SHA256 强制校验→暂存→「打开位置+替换说明」两态展示
4. **滑移收编**: W3 终审判定的页面打磨批与杂项一次清账
5. **收口**: README 双语重写 / AGENTS.md 打包节刷新 / 递延账本落库(`docs/backlog.md`)/ 版本 1.0.0
6. **顺带退役 J7**: CI 附加 Linux job 真跑 POSIX flock 的 2 个 skip 用例

### 非目标(本批不做)

- 更新事务档(自动替换,滑移批次J,见 §6)
- OTA 差分 / 多更新镜像源 / 更新签名(spec §6.3 已裁剪,1.0 记录同源 SHA256 局限)
- 启动时自动检查更新(纯手动,见决策 D3)
- 0.12.0 复审第④阶段(服务线程故障监测/窗口就绪确认/Ctrl+C 协作停机措辞/JobStore 公共任务服务重构)——可靠性打磨非分发闭环,落 `docs/backlog.md` 留 1.x
- J6 真实 WebView2 手测(依赖用户允许安装 pywebview,与 W4 无耦合,独立 gating 不动)

## 2. 与批次I 总 spec §6 的关系

| 总 spec 条目 | 本批处置 |
|---|---|
| §6.1 CI 形态 | 照办 + 精化(Linux 附加 job,决策 D2) |
| §6.2 双形态与受管清单 | 照办 + 边界澄清(§4.1.3) |
| §6.3 更新生命周期-基础档 | 照办(行为红线逐条落验收) |
| §6.3 更新生命周期-事务档 | 滑移批次J(§6 继承前提清单) |
| §6.3 Velopack 评估门 | 归 T14(批次J),预调研结论留档 §6 |
| §6.4 更新入口 | 照办 |
| §9 任务分解 T11-T15 | 重排为 W4a(5 任务)+W4b(T14),新增 T13.5 滑移收编(决策 D1) |

## 3. 现状基线(2026-10-03 实测)

| 项 | 现状 | 差距 |
|---|---|---|
| PyInstaller | 无 spec 文件、无 tools/packaging/ | T11 全新建 |
| CI | `.github/` 不存在(AGENTS.md 分发策略节却宣称 tag 自动发 Release) | T12 全新建 |
| 依赖 | httpx>=0.27 已在运行依赖;pywebview>=5.0 为**下限约束非钉版**(窗口壳消费其内部 API,发行构建须锁定) | 锁定文件新建 |
| package-data | `migration = ["data/*.yaml","data/*.sha256","data/*.txt","gui/*.html"]` 已配(终审 I-2) | 直接可用 |
| 数据定位 | doctor/manifest 走 `importlib.resources`(包资源);exe 旁 `data/` 仅承载用户全局配置 `config.toml`(`workdir._resolve_green`,首跑 `_ensure_writable`) | 双轨事实,§4.1.3 写明 |
| 版本/文案 | 0.12.0;pyproject description 仍写「只读 scan/diff」(过时) | T13.5/T15 刷新 |
| tools/ | `gen_manifest.py` 已有(改 data/*.yaml 须重跑,记忆锚) | 受管清单思路可参考 |
| 携带项 | W3 页面打磨 5 项、`--window` 丢 `--port/--no-browser`、swap 行为 node 测试入库、账本未落库 | T13.5/T15 |

## 4. W4a 设计(分发基座 → 0.13.0 试发 → 1.0.0)

### 4.1 T11 打包入库

#### 4.1.1 双形态产物(资产命名=更新契约,一处锁定)

- `mcmig-gui-<ver>-win-x64.exe` — **onefile,只出 GUI 单文件**(总 spec 决策#4):入口 `migration/gui/app.py:main`,窗口模式缺 WebView2 自动降级浏览器模式(已有);CLI 用户用 onedir
- `mcmig-<ver>-win-x64.zip` — **onedir 全家桶**: `mcmig.exe`(CLI)+ `mcmig-gui.exe`(窗口)+ `_internal/` + `filelist.txt`(受管清单)
- `SHA256SUMS.txt` — 两资产的 SHA256(release.yml 生成)

#### 4.1.2 spec 文件、启动脚本与依赖锁定

- **薄启动脚本(评审 P1-1)**: PyInstaller 消费脚本入口,不能直接用 `app.py:main` 函数入口——包内模块的相对导入在入口脚本语境下必然 `attempted relative import with no known parent package`(实测)。新增两个**绝对导入**薄启动脚本(建议 `tools/packaging/entry_gui.py`、`tools/packaging/entry_cli.py`,内容仅 `from migration... import main; raise SystemExit(main())`);onedir 双 EXE 各自一条 Analysis(共享 datas/纯 Python 模块分析),**每个 EXE 只携带自己的入口**,不把同一份 scripts 交给两个 EXE
- **无控制台兼容(评审 P1-2)**: GUI exe 按 `console=False` 打包(小白双击不弹黑窗);冻结无控制台形态下 `sys.stdout/stderr` 为 `None`——当前 `cli.py` 的编码 reconfigure(`stream.isatty()`)与 uvicorn 默认日志配置都会在此炸(实测/已知坑)。契约:**薄入口在被 import 的 main 之前先把 `sys.stdout/stderr` 兜底为 `os.devnull` 打开的句柄**(标准 Windows 无控制台处置);冒烟必须覆盖**原生窗口启动、缺 WebView2 的浏览器降级、页面退出与进程回收**三路径,不以 HTTP 就绪替代 GUI 验收
- **致命错误可见性契约(v3 评审 P2)**: 冻结 GUI(console=False)在**页面可用前**发生的一切致命错误,都必须以中文消息框呈现(失败原因+恢复指引)后方可退出——**原生窗口与浏览器降级两条路径同约束**。现状缺口(代码事实):数据自检分支已有消息框(`app.py:258`),而 WorkdirError/软件目录不可写(`app.py:283`)、本地服务启动失败(`app.py:297`)、就绪超时(`app.py:301`)均**只 print**;降级浏览器模式自身致命失败(转发包装层)同病——stdout 已被 devnull 兜底,玩家双击后即无声退出(内存探针实测:模拟不可写目录,退出码 2、消息框调用 0 次)。落点:上述分支补 `_notify_box` 单点复用;验收覆盖**软件目录不可写**与**服务就绪失败**两条注入路径。保留「GUI 不回退 TEMP」决定(workdir 前置校验语义不变)
- `tools/packaging/mcmig.spec`:datas 复用 package-data 既有清单(`migration/data/*`、`migration/gui/index.html`)
- **hiddenimports 修正(评审 P2-8)**: 包名是 `webview` 非 `pywebview`——需要时写 `webview.platforms.winforms`;并依赖 `pyinstaller-hooks-contrib`(其 `hook-webview` 官方钩子通常已覆盖渲染后端收集,冒烟验证后按需显式补)
- pyproject 拆可选依赖组 `gui = ["pywebview>=5.0"]`(决策 D5):默认安装=CLI+浏览器模式;打包与想要窗口的源码用户 `pip install -e ".[gui]"`;**打包产物不变**(spec 显式引 webview)
- **Windows 构建锁定文件**: 源码安装维持宽松约束(`pyproject` 下限语义不变,窗口壳消费内部 API 的风险由锁定文件兜住);新增 `tools/packaging/requirements-win-build.txt` 钉死已验证组合(Python 3.13 + 全部运行依赖精确版本 + `pyinstaller==6.x.y` + `pyinstaller-hooks-contrib==x.y.z`),CI 与本地发行构建一律 `pip install -r` 此文件
- **复现口径修正**: 「任何人任何机器可复现」收敛为「**Windows x64 + 锁定文件**的构建环境可复现产物形态与行为」;不承诺字节级一致(PyInstaller 默认含时间戳,不做 SOURCE_DATE_EPOCH 工程)
- 本地冒烟脚本(实现细节归计划):打出的包真跑 `mcmig doctor`(manifest 校验过)+ `mcmig-gui` 起服务与窗口(就绪探测过)+ `mcmig --help` + **无控制台三路径**(见上)

#### 4.1.3 数据布局双轨与受管清单边界(澄清,替代总 spec §6.2 的歧义表述)

- **程序数据 = 包资源**:`migration/data/*.yaml`、`manifest.sha256`、`gui/index.html` 经 PyInstaller 落入 `_internal/migration/`(onedir)或 `_MEIPASS`(onefile),doctor 用 `importlib.resources` 定位——**两形态天然可用,无释放机制需求**(实测代码事实,`doctor.py:37`)
- **用户态文件 = exe 旁 `data/`**: `config.toml`(全局配置,绿色理念:数据在软件自己目录、可写校验锚点)+ 运行产物(如 W4 起的 `update-staging/` 暂存目录——程序生成的临时产物,同属软件自己目录,不残留用户别处);用户规则覆盖在 `game_root/.mcmig/rules.yaml`(实例态)
- **受管清单 `filelist.txt` = 且仅 = 发行 zip 展开后的程序文件树**(含 `_internal/migration/data/*.yaml` 程序副本);`data/config.toml` 及一切清单外文件(用户自放)永不被更新触碰;删除范围=旧受管清单−新受管清单(事务档消费,基础档只生成)

#### 4.1.4 构建形态标记(评审 P2-4,决策 D7)

- 新增 `migration/_form.py`,内容仅 `FORM: str = "source"`(入库默认值);T11 打包脚本与 CI 在构建**前**按产物改写为 `"onefile"` / `"onedir"`(改写后构建,产物内固化)
- **改写纪律(v3 评审)**: 优先在**独立构建工作副本**上改写(源码树零污染,推荐实现);若实现选择直接改写源码树,则成功与失败路径都**必须恢复 `"source"`**(try/finally 包住整个构建,构建崩溃不留脏树);两形态分别使用**独立构建缓存**(workpath/distpath 分开),防前次改写或半成品残留串入下一形态
- `pick_asset(kind)` 的 kind **唯一事实来源** = `_form.FORM`,CLI/GUI 两前端共读;**不得**从 exe 文件名、入口形态、显示模式或 WebView 可用性推断——两形态都 frozen、onedir 也有 GUI、onefile 缺 WebView2 仍可进浏览器模式,这些信号都不可靠
- 验收锚点: onedir GUI 选 zip 资产;onefile 降级浏览器模式后仍选 exe 资产
- 不依赖运行时探测的理由补充: 现代 PyInstaller onedir 同样设置 `sys._MEIPASS`(指向 `_internal/`),`_MEIPASS` 有无不构成 onefile/onedir 判据

### 4.2 T12 CI

#### 4.2.1 `build.yml`(push/PR/workflow_dispatch)

- windows-latest + `actions/setup-python@v5` Python 3.13(与主仓一致)
- 分段安装(v3 统一口径):**测试段** `pip install -e ".[dev,gui]"` → `ruff check migration/ tests/`(0.15.20)→ `pytest tests/`(runner 预装 node,页面行为测试真跑);**构建段** `pip install -r tools/packaging/requirements-win-build.txt` 后执行双形态构建 → `actions/upload-artifact`——发行产物不得在宽松解析环境产生(§4.1.2 锁定文件已含 PyInstaller 与 hooks-contrib,不再游离钉版)
- **Linux 附加 job**(决策 D2):ubuntu-latest + Python 3.13 → ruff + pytest——POSIX flock 的 2 个 skip 在 Linux 真跑,**J7 手测项退役**;`tests/test_instlock.py:202` 的 skipif 平台门天然放行

#### 4.2.2 `release.yml`(tag `v*`)

- 同 build 全检 → 双形态构建 → python 生成 `SHA256SUMS.txt`(UTF-8 无 BOM,`<sha256hex>  <文件名>` 两空格分隔,certutil 可比对)→ `gh release create`(softprops/action-gh-release 或 gh CLI,附三资产)→ Release notes 默认生成自 tag 注解
- 无 secrets 需求(`GITHUB_TOKEN` 权限 `contents: write` 即可);工作流与步骤注释中文

### 4.3 T13 更新基础档

#### 4.3.1 `migration/updater.py` 纯库模块(CLI/GUI 平级消费)

```
常量: _REPO = "Nothing-ray/mcmigrator"(仓标识,非版本号,不违「不硬编码版本」)
      _RELEASES_LATEST / _TIMEOUT 等

fetch_latest_release() -> ReleaseInfo | None
    httpx GET api.github.com/repos/<repo>/releases/latest(Accept: vnd.github+json,
    timeout 10s, follow_redirects=True);404(无 Release)→ None;网络/5xx/403 限额
    → UpdateError(中文 why)
pick_asset(assets) -> AssetInfo | None
    kind 取 _form.FORM(§4.1.4,构建时固化)按资产命名契约匹配;
    缺失 → None(调用方按「该形态未发布」呈现)
is_newer(remote_tag: str, local_ver: str) -> bool
    去 v 前缀,按 '.' 切段数值元组比较;后缀(如 rc)忽略但记录在决策
fetch_sums(browser_download_url) -> dict[str, str]
    下载 SHA256SUMS.txt(小文件,同样跟随重定向),解析 {资产名: sha256hex}
download_and_verify(asset, sums_entry, staging_dir, progress_cb,
                    should_cancel=None) -> Path
    每次下载独占子目录 staging_dir/<uuid4>/<资产名>.part(评审 P2-5:
    JobStore 单实例约束只在单 app;CLI+GUI 或双 GUI 跨进程并发同名 .part
    会互相截断/误删);流式下载→SHA256 校验→通过则**在本 uuid 子目录内**
    os.replace 原子落位为 <uuid4>/<资产名>,返回该独占路径——**成功产物
    也留在独占路径,不落共享路径**(v3 评审:GitHub 资产不可变须显式启用,
    同版本资产重传时共享路径会被后任务改写、破坏先任务已校验内容);
    失败/取消/校验不过→清理**本任务的 uuid 子目录**(只清自己拥有的文件);
    成功产物保留至用户应用,不跨任务清理(保守:不删已校验文件;替换指引
    说明暂存目录可在应用后整目录删除)
plan_update(staging_root) -> UpdatePlan | None
    编排上述各步;UpdatePlan{version, asset_name, path, size, sha256}
staging 根(两端分治,评审 P2-6):
    CLI `mcmig update` 独立解析暂存位置,不依赖 workdir(检查/下载不需要
    game_root 与全局配置):冻结模式=<exe目录>/data/update-staging/,
    不可写回退 %TEMP%\mcmig-update\,再失败→UpdateError「请手动下载」;
    GUI 不承诺 %TEMP% 回退——绿色模式 workdir 前置校验 exe/data 可写,
    不可写时页面根本不会启动(该致命路径按 §4.1.2 v3 契约经消息框给出
    「移动到可写文件夹」指引),
    到达更新面板的 GUI 必然 exe/data 可写
```

- **源码模式不提供下载**:`_form.FORM == "source"` 时 `--check` 可用,下载路径给出「源码运行请 git pull」指引——防测试/开发态误替换
- **SHA256 失败即拒绝成功态(评审顺手项)**: SUMS 清单缺失、目标资产条目缺失、非法十六进制、同名重复条目——四种形态一律 UpdateError 拒绝,不进入下载/落位;**重定向必须显式跟随**(httpx 默认不跟随;`browser_download_url` 实为 302 至 objects.githubusercontent.com)
- **错误分类三段式**(what/why 中文,CLI 直印/GUI 转 error 事件): 网络不可达/超时、限额(403 含 X-RateLimit-Remaining 头时提示「GitHub 匿名限额,稍后再试」)、无新版(非错误,信息呈现)、资产缺失、校验失败、目录不可写
- 更新检查**纯手动**(决策 D3):无任何启动期/定时网络请求

#### 4.3.2 行为红线(总 spec §6.3 基础档,逐条落验收)

- **不自动运行暂存文件**、**无「立即运行」按钮**
- 界面明确「退出旧版→替换到原目录→再启动」三步指引(绿色模式全局配置按 exe 目录定位,暂存目录内运行会读到空配置并产生嵌套暂存)
- 不做通用启动位置限制(暂存 exe 本身是合法便携形态;验收只约束工具自身行为)

#### 4.3.3 CLI 入口

`mcmig update [--check]`:检查→(有新版且非 --check)下载+校验+暂存→打印暂存路径+替换指引(两段式);退出码 0=无新版或成功,2=失败。**暂存位置独立解析,不依赖 workdir**(§4.3.1,可回退 `%TEMP%`)。源码模式 `--check` 可用,下载路径给出 git pull 指引。

#### 4.3.4 GUI 入口(「关于/更新」面板)

- 步①向导下方折叠区(轻量):当前版本号 + 「检查更新」按钮
- 检查后三态:已是最新 / 发现新版(版本号+体积+更新日志链接) / 失败(三段式)
- 「下载并校验」→ job 化;文案全进 STRINGS/PAGE_STRINGS;页面零新框架

**update job 契约(评审 P2-3,补齐四件)**:

1. `kind="update"` 且 **可取消**(`can_cancel=True`):下载循环按块轮询 `should_cancel`(同 swap 装包取消检查点形态);取消→停读→清理本次 uuid 子目录(§4.3.1)→cancelled 终态
2. **字节进度进事件且进 GET 快照**:`progress` 事件携带 `{received, total}`(按兆级节流);`Job` 快照的 progress 字段同样携带——刷新恢复经 `restoreSavedJob` 重读 GET 不丢进度
3. **完整终态载荷**:done 事件=UpdatePlan 全字段(version/asset_name/path/size/sha256),与 GET done 载荷同源
4. **专用渲染分派**:`handlersForKind("update")` 独立实现(进度条+两态展示),不落入迁移渲染;`restoreSavedJob`/SSE reset/overflow 后重读 GET 的恢复路径按 kind 分派到同一渲染(复用 W3 游标四规则,验收含刷新恢复与 reset/overflow 行为)

**打开位置的安全口径(评审顺手项)**:按钮调用 `explorer /select,<资产路径>`(或 `os.startfile(父目录)`)——**绝不 `os.startfile` 资产本体**(那等于执行下载物);验收核「实际打开对象正确」与「未执行资产」(点击后无新 mcmig 进程),仅检查按钮存在不够

- 完成态两态展示:「已下载,尚未应用」+ 打开暂存位置按钮 + 替换三步指引
- 下载期间其他任务 409 属预期(单任务锁),文案说明

### 4.4 T13.5 滑移收编(一个任务清账,W3 终审+0.12.0 遗留)

1. swap 装包逐 jar 进度:`swap_install` 加 `progress_cb(jar_name, i, total)` 参数(CLI 传 rich 进度行,GUI 传 job emit)——对齐 T8 页面文案承诺
2. cancel-hint 在 `swap_replan_failed` 终态隐藏(一行 else 分支)
3. 恢复 running swap 的取消按钮文案(「取消迁移」→按 kind 分化)
4. 恢复 terminal `swap_preflight` 面板死按钮复燃(用 jobModel.src/dst)
5. `mcmig gui --window` 转发补 `--port`/`--no-browser`(传剥窗 Namespace)
6. pyproject description 刷新(「Minecraft 整合包版本迁移工具:scan/diff/plan/migrate/换包/向导界面」)
7. swap 行为 node 测试入库 + `no_plain_replan` 锚点移到 swap 函数区
8. resync 退避耗尽后横幅文案改为诚实版(「连接已断开且多次重试失败,请刷新页面或检查服务」——0.12.0 复审#8 已做重试,此为耗尽分支文案)

> 注: 3/4 的「swap 恢复态」与 0.12.0 复审#10(路线派生+回填)同区域,实现时共用夹具防回归。

### 4.5 T15 收口

- README 双语重写:是什么/两种包怎么选/下载/更新方法/构建自源码;删除过时表述
- AGENTS.md:构建与运行命令节的打包命令改为 spec 入库口径;「分发策略/项目布局」节与现实对齐(含本仓 AGENTS.md 中已过时的客户端环境事实不动——属 modpack 侧资料,仅注「已过时,以实测为准」)
- **递延账本落库 `docs/backlog.md`**:W3 44 项中可名表者(~11)+M5/M7/M8+0.12.0 复审第④阶段+事务档滑移声明;每项一句「是什么+为何递延+何时捡起」
- 版本:W4a 合入后先 **0.13.0 试发**(决策 D1),验收通过重打 **1.0.0**
- 占用实测记录(README 附录,非验收承诺):onefile/onedir 体积、冷启动时间、磁盘峰值
- 签名局限记录:同源 SHA256SUMS 只证「资产与清单一致」,不证发布者身份;事务档阶段再评来源签名

## 5. W4a 排期与试发协议(决策 D1/D8)

```text
T11 打包 → T12 CI(build 先行,release 待 T11 就绪)
        → T13 更新基础档 → T13.5 滑移收编 → T15 收口(版本 0.13.0)
        → 用户放行推送后:
          ① push main → tag v0.13.0(正式 Release,非 prerelease/draft
             —— /releases/latest 不返回 prerelease 与 draft,试验链路依赖 latest)
          ② 修改版本至 0.13.1 → tag v0.13.1 → 在 0.13.0 内真实走完
             「检查→发现新版→下载→校验→暂存→替换→配置保留」全链路(评审 P2-7:
             只发 0.13.0 无法验收「发现更高版本」,须两连发)
          ③ 验收(§8)通过 → 修改 pyproject 与 migration.__version__ 至 1.0.0
             → tag v1.0.0(批次I 收口)——**换版本必改源码两处,不许只换 tag**
W4b T14 事务档:独立验收,可滑移批次J(§6)
```

**发布守卫(release.yml 内建,评审 P2-7)**: 构建前强制校验 `tag 名(去 v)== pyproject.version == migration.__version__`,任一不一致即 fail(防内置版本与资产名/更新比较基准漂移);资产文件名从同一版本生成。

> **推送仍冻结**(用户 2026-10-02 令):试发依赖用户放行 `git push` + `git push --tags`;spec 不改变推送纪律。

## 6. W4b T14 更新事务档(滑移批次J,设计继承不改)

- 生命周期契约照总 spec §6.3 事务档原文:助手自复制(副本自包含)→安装级更新锁(覆盖共享 `_internal` 的 GUI/CLI 进程;「主程序退出」不充分)→受管清单差集替换(旧→`.old` 事务登记;zip 解压路径规范化限安装目录内,防 zip-slip)→启动新版→成功条件(manifest 自检+版本匹配+GUI 就绪信号;超时保留 `.old` 与登记)→清理且仅清理本事务登记;中途失败保留备份+登记,给手动恢复指引,不承诺自动回滚
- **适用范围仅 onedir**(onefile 重下载替换成本≈0,保持基础档)
- **Velopack 评估门(T14 动工前,预调研留档 2026-10-03,评审后更新证据)**: Velopack 官方支持 Python(`pip install velopack` + `vpk` 打包 PyInstaller 产物,~1.2.0;velopack.io / github.com/velopack/velopack),能力面(独立 Update.exe/等待退出/更新互斥/稳定入口与程序目录分离)与本契约高度重合。**证据修正(v2)**: 此前援引的 Python 更新崩溃 issue(#911)**已关闭**;官方另提供 **Portable.zip** 打包形态(平铺布局,非安装器)——「布局冲突」论据弱化,平铺双 exe 布局存在适配路径。评估维度据此定为三条: ①Python 集成路径当前成熟度(以当期 issue 面为准,不引用已关闭个案) ②Portable.zip 布局与绿色 zip 双 exe 形态的适配成本 ③自研助手成本(仅作参考量级,**不作选型决定依据**)。倾向性结论留待 T14 正式评估留档,本 spec 不预判
- T14 实施前提清单(总 spec §6.3 原文继承):运行期登记/占用协议、加入阻断检查、锁释放时序(启动新版前释放,防循环等待)、助手临时副本自包含
- 0.12.0 复审#11 的写守卫(Origin+Content-Type)已收口;事务档助手如走本机 HTTP 交互须沿用同一守卫口径

## 7. 决策记录

| # | 决策 | 理由 |
|---|---|---|
| D1 | 两波分批:W4a(0.13.0 试发)→W4b(T14 批次J)→1.0.0 | tag→Release 全链路是最贵的集成测试,先用非里程碑号排雷;1.0.0 只干净重打;事务档复杂度(安装级锁+真实 exe 冒烟)不绑架「分发可用」 |
| D2 | CI 加 Linux 附加 job(仅 ruff+pytest,不构建) | 顺带真跑 POSIX flock 2 个 skip,J7 退役;成本≈1 分钟/push |
| D3 | 更新检查纯手动(GUI 按钮+CLI 命令) | 匿名限额 **60 次/小时·每来源 IP**(非按人;共享代理/NAT 用户共用额度,玩家群体自动检查风险更高);静默后台网络请求与绿色理念相悖;总 spec「手动+低频足够」同向 |
| D4 | 受管清单边界=发行 zip 程序文件树(含 `_internal/.../data/*.yaml`);exe 旁 `data/`(用户配置)永不在清单 | 澄清总 spec §6.2「data/ 永不被触碰」的字面歧义;代码事实:程序数据走包资源(doctor importlib.resources),用户配置走 exe 旁,双轨本就解耦 |
| D5 | pyproject 拆 `gui` 可选依赖组(源码宽松)+ Windows 构建锁定文件(发行精确) | 0.12.0 复审第④阶段遗留项中唯一便宜的;源码党裸装跑 CLI+浏览器;「可复现」收敛为锁定环境,不承诺字节级一致(评审 P2-8) |
| D6 | onefile 数据布局=包资源直读,无释放机制 | 实测 doctor/manifest 走 importlib.resources,onefile `_MEIPASS` 天然可用;曾设想的「首跑释放到 exe 旁」不需要(避免引入释放-校验复杂度) |
| D7 | 构建形态标记 `migration/_form.py`(source/onefile/onedir),构建前改写固化,两前端共读 | 两形态都 frozen、onedir 也有 GUI、onefile 可降级浏览器——入口/显示/文件名皆不可靠信号;`_MEIPASS` 有无在现代 PyInstaller 下也不构成判据(评审 P2-4) |
| D8 | 试发协议=0.13.0→0.13.1 两连真实更新试验;发布守卫=tag/pyproject/__version__ 三处一致强校验;试发 Release 为正式类型 | 只发 0.13.0 无法验收「发现更高版本」(顺序循环);`/releases/latest` 不返回 prerelease/draft;换版本必须改源码不许只换 tag(评审 P2-7) |

## 8. 验收标准

1. **打包可复现**: 干净 clone + venv + Windows 构建锁定文件 → 按 README 命令产出双形态;本地冒烟过(doctor/起服务与窗口/--help/**无控制台三路径**:原生窗口启动、缺 WebView2 浏览器降级、页面退出与进程回收——不以 HTTP 就绪替代 GUI 验收);**致命错误可见性**(v3):注入软件目录不可写与服务就绪失败 → 中文消息框含恢复指引弹出,非静默退出(两条注入路径均覆盖)
2. **CI 绿**: push/PR 上 build.yml 双平台全绿(Windows 含 node 页面测试;Linux flock 用例真跑非 skip)
3. **tag→Release**: `v0.13.0` 试发,Release 页自动出现 exe+zip+SHA256SUMS.txt;`certutil -hashfile` 与 SUMS 一致(总 spec 验收#8 前半);发布守卫生效(故意推 tag 与版本不一致的组合在 CI 被拒——干跑验证)
4. **更新基础档可用且合规**: 按 §5 两连发协议,0.13.0 内 `mcmig update --check` 报 0.13.1;`mcmig update` 下载+校验+暂存;GUI 面板走「检查→下载(可取消,字节进度刷新恢复不丢)→已下载,尚未应用」全链;**不自动运行暂存文件、无「立即运行」按钮、含替换三步指引**;打开位置核**实际打开对象**(Explorer 选中资产)且**未执行资产**(点击后无新 mcmig 进程);篡改下载字节→校验失败且暂存被清理;SUMS 缺条目/非法哈希/重复条目→拒绝;断网/限额→中文引导非 traceback;`data/config.toml` 全程不被触碰;**形态选择正确**(onedir GUI 选 zip;onefile 降级浏览器模式后仍选 exe)
5. **滑移清账**: T13.5 八项逐条核(装包进度可见/replan 终态无取消提示/恢复态按钮文案正确/--window 带参转发/description 刷新/node 测试入库/横幅诚实文案)
6. **文档与账本**: README 双语/AGENTS.md 打包节/`docs/backlog.md` 落库;版本 1.0.0(pyproject 与 `__version__` 同步修改)
7. **事务档未交付时**: Release 页与 README 标注「自动应用更新随后续版本提供,当前为下载-校验-手动替换」(总 spec 验收#8 后半口径)

## 9. 测试策略

- **updater 单元(mock httpx)**: latest 响应解析/404→None/网络与限额错误分类;资产名契约匹配(含 `_form.FORM` 三值选择);is_newer 数值比较边界(0.9<0.10<1.0,后缀忽略);SUMS 解析含中文名/空行容错+**四种失败形态**(清单缺失/条目缺失/非法 hex/重复条目→拒);流式下载进度回调;**重定向跟随**(mock 302→200 链,断言不跟随即失败的回归);SHA256 失败→删 .part+UpdateError;**uuid 子目录隔离**(v3:两并发路径互不可见,**终路径亦独占**——同版本资产重传不改写先任务已返回路径);CLI staging 回退 %TEMP% 再失败→「请手动下载」;源码模式拒绝下载(仅 --check)
- **致命错误可见性(v3)**: `app.py` 各致命分支(WorkdirError/服务启动失败/就绪超时/降级浏览器包装层)monkeypatch `_notify_box` 断言被调用且文案含恢复指引;数据自检分支既有消息框回归不破
- **构建标记恢复(v3)**: 打包脚本走源码树改写路线时,注入构建失败断言 `_form.py` 复原 `"source"`;走独立工作副本路线时断言源码树全程未被改写
- **无控制台兼容(P1-2)**: 薄入口的 stdio 兜底单测(注入 `sys.stdout/stderr=None` 后 import 入口不炸);cli 编码 reconfigure 在 None 流下安全
- **update job 契约(P2-3)**: kind=update 可取消(取消回调触发→停读+清理本次子目录+cancelled 终态);字节进度进事件**且进 GET 快照**(发送进度后快照非 None);done 载荷=UpdatePlan 全字段;页面 update 渲染分派(不落迁移渲染);刷新恢复/SSE reset/overflow 后经 GET 重建并续订
- **打开位置安全**: 契约断言调用的是 explorer /select 或父目录 startfile,不含对资产本体的 startfile 调用形态
- **发布守卫**: 单测三处版本一致校验逻辑(tag/pyproject/__version__ 任一不一致→fail)
- **行为红线(源码断言+页面契约)**: updater 模块与页面无「立即运行」字符串;替换三步指引文案存在;两态「已下载,尚未应用」存在
- **CLI**: update/--check 退出码与输出对拍(无新版 0/成功 0/失败 2);源码模式 git pull 指引
- **GUI**: 面板三态渲染;下载 job 事件流;下载期间其他任务 409 文案
- **T13.5 各项**: 对应既有测试风格逐项补(装包 progress_cb 单测+CLI/GUI 对拍;恢复态按钮用 #10 夹具)
- **CI 自证**: build.yml 每 push 跑全套;release.yml 首跑即 0.13.0 排雷
- 手测清单增补 K 组: 真实 Release 下载→替换→配置保留;只读目录(CLI 回退 TEMP,GUI 拒启文案);断网;代理环境;无控制台三路径
- 测试增量估计 +55~80(v1 估 45~65,P2-3/P1-2 增补后上调)

## 10. 风险与已知边界

- onefile 启动解压临时目录(慢/杀软误报率较高):双形态并供缓解;1.0 实测记录进 README 附录,不作验收承诺
- 匿名限额 60/h:手动触发够用;未来玩家量级上来再评 token/镜像(落 backlog)
- `gh release` 首跑失败模式(权限/资产命名漂移):0.13.0 试发即排雷点;SUMS 生成用 python 而非 shell 拼接,防 CRLF/编码坑
- 同源 SHA256 不证身份:记录为已知限制(§4.5)
- PyInstaller 与 pywebview/winforms 版本组合的 hiddenimports 漂移:钉版+冒烟脚本双保险
- 本 spec 不改变推送冻结纪律;试发节点待用户放行

---

修订记录:
- v1(2026-10-03): 初稿。继承批次I 总 spec §6 并精化:W4a/W4b 分批(D1)、Linux job(D2)、纯手动检查(D3)、受管清单边界澄清(D4)、gui 可选组(D5)、onefile 无释放机制结论(D6);新增 T13.5 滑移收编八项与账本落库。
- v2(2026-10-03,收编评审 2P1+6P2+顺手项): ①薄启动脚本+双入口 Analysis+console=False 的 stdio 兜底与无控制台三路径冒烟(P1-1/P1-2) ②update job 契约四件:可取消/字节进度入 GET 快照/完整终态载荷/专用渲染与恢复分派(P2-3) ③构建形态标记 `_form.py` 固化 kind 事实来源(D7,P2-4) ④uuid 下载子目录+只清自己+原子落位,封跨进程 .part 竞争(P2-5) ⑤staging 两端分治:CLI 独立解析可回退 TEMP,GUI 不承诺 TEMP 回退(workdir 前置事实,P2-6) ⑥试发协议 0.13.0→0.13.1 两连发+发布守卫三处版本一致+正式 Release 口径(D8,P2-7) ⑦Windows 构建锁定文件+复现口径收敛+hiddenimports 模块名修正(`webview.platforms.winforms`)+依赖 hooks-contrib(P2-8) ⑧顺手项:打开位置=Explorer /select 且绝不 startfile 资产本体+验收核未执行资产;SHA256 四种失败形态即拒+重定向显式跟随;Velopack 证据更新(#911 已关闭/Portable.zip 存在,自研行数降为参考不作选型依据);限额口径改每来源 IP。测试增量估计上调至 +55~80。
- v3(2026-10-03,收编 v2 评审条件「有条件通过」): ①**致命错误可见性契约**(P2):冻结 GUI 页面可用前的一切致命错误(WorkdirError/服务启动失败/就绪超时/降级浏览器包装层)必须中文消息框+恢复指引,原生窗口与浏览器降级同约束;验收覆盖不可写目录与服务就绪失败两条注入;保留 GUI 不回退 TEMP ②**构建标记改写纪律**:`_form.py` 优先独立构建工作副本改写,改源码树则 try/finally 恢复 `"source"`,两形态独立构建缓存 ③**CI 安装口径统一**:build.yml 分测试段(`-e ".[dev,gui]"`)与构建段(`-r requirements-win-build.txt`),发行产物不在宽松环境产生 ④**下载终路径独占**:成功产物也留在 `<uuid4>/<资产名>` 不落共享路径(GitHub 资产不可变须显式启用,不作前提);成功产物不跨任务清理,指引说明可整目录删除;测试策略同步(uuid 隔离措辞/致命错误消息框/构建标记恢复三组用例)。
