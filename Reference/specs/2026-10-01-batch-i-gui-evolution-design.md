# 批次I 设计规格:GUI 演进与分发闭环(v0.6 → 1.0.0)

> 状态: 设计定稿待评审 | 日期: 2026-10-01 | 前置: 批次H spec(466d2de,待实现,目标 0.11.0);GUI v0.6 已交付(`2026-09-04-gui-design.md`)
> 调研依据: MAA(MaaAssistantArknights)架构调研(2026-10-01,证据索引见 §6);积欠清单源自批次B-H 各 spec 沿挂项
> 决策确认: 2026-10-01 与维护者对齐(排期先H后I / 维持 Web+pywebview / 首批五项 / CI 双形态打包)

## 1. 目标与非目标

**目标**: 把工具从「自用骨架」推到「社区分发就绪」(v0.6 GUI spec 的既定终点),五项:
1. **diff 能力进 GUI**: 总览页(六桶计数 / mod 配对表 / client_only / 提示区)
2. **swap 换包 GUI 化**(前置: swap 编排从 cli.py 下沉 pipeline)
3. **pywebview 独立窗口**: 摆脱浏览器标签页形态,页面零改动
4. **CI 自动构建发布**: GitHub Actions(push 冒烟 + tag 出 Release)
5. **自动更新**: GitHub Releases 单源,全量包 + SHA256 强制校验

**非目标**: .mcmigpack 封包、规则编辑器、页面多语言切换、OTA 差分增量包、多更新镜像源、PySide6/Tauri 重评、C++ 重写(裁决见 §5.1)、macOS/Linux 发行(PCL2 生态为 Windows)。

## 2. 现状基线与总体架构

### 2.1 已有地基(全部沿用,不重构)

```
页面(单文件 HTML+原生 JS)──SSE/fetch──▶ gui/server.py(薄翻译+job+SSE+单任务锁+Host 校验)
                                              │
cli.py ──────────────────────────────┐        ▼
                                     └──▶ pipeline.py(编排库,CLI/GUI 平级消费)
                                              ▼
                                core(scanner/rules/planner/executor + fsops)
```

- v0.6 三步向导已交付(选版本→审阅计划→执行),14 个 server 测试
- 报告对象全结构化 dataclass(`DiffReport`/`MigrationPlan`/`ModPair`/`FileResult`),rich 仅 CLI 渲染层——GUI 路径不碰 rich
- `Executor.execute(progress_cb=...)` 逐文件同步回调(executor.py:99-128,GUI 进度数据源);扫描无文件级回调(粒度缺口延续,不在本批修)
- 绿色布局: `exe/data/<slug>/{snapshots,plans,rules.yaml}` + `exe/data/config.toml`;`data/manifest.sha256` 完整性校验 + `mcmig doctor`
- 分层纪律: gui 不 import cli 私有函数;核心不感知 HTTP

**架构判断**: 这正是 MAA「核心是库、GUI 是可替换薄壳、窄 JSON 事件协议」模式的 Python 等价物(MAA 用 C ABI+回调传 JSON,我们用 pipeline API+SSE 传 JSON)。边界已画对,本批只加壳与闭环,不动核心。

### 2.2 积欠清单(本批消化)

| 积欠 | 来源 | 本批归属 |
|---|---|---|
| diff/run_diff 能力未进 GUI | 批次F(下沉时注明「GUI 亦可直调」) | T1 |
| swap 编排滞留 cli.py(`_swap_preflight`:441 / `_swap_install`:460 / `_cmd_swap`:492) | swap spec(2026-08-31) | T2 |
| swap GUI 化 | v0.6 GUI spec 非目标「下迭代」 | T3 |
| GUI 生成物锚定不一致(exe/data vs game_root/.mcmig) | 批次D/E/F 沿挂 | T4 |
| pywebview 独立窗口 | v0.6 GUI spec §9 分发期路径 | T5 |
| compat_warnings 只进 stderr 未页面化(server.py 现仅 log.warning) | v0.6 交付即知 | T3(随 swap/审阅页统一处理) |
| CI 缺失(README 宣称 Release+SHA256,`.github/` 不存在) | README vs 仓库现状 | T7-T9 |
| modid 碰撞收口、uninstall 措辞 | 批次E 沿挂 | T6(可滑移,不阻塞) |

### 2.3 目标架构(增量)

```
页面(index.html)──SSE/fetch──▶ gui/server.py ──▶ pipeline.py ──▶ core
                                   ▲                  ▲    └─ run_swap(新,自 cli 下沉)
mcmig-gui 入口 ──▶ gui/app.py(pywebview 壳,新) ──┘   │
cli.py(mcmig update 子命令,新) ─────────────────────┴──▶ updater.py(新,纯库)
GitHub Actions: build.yml / release.yml(新) ──▶ Release 资产 ←── updater 消费
```

## 3. I-A:GUI 能力设计

### 3.1 diff 总览页(三步向导 → 四步)

- **新端点** `POST /api/diff {src,dst}` → job(phases: `scan_src|scan_dst|diff`);done 载荷:

```json
{"type":"done","diff":{
  "buckets":{"to_migrate":12,"candidate":3,"mods":2,"only_in_dst":40,"identical":118,"never":5},
  "mod_pairs":[{"src":"...jar","dst":"...jar","note":"⇄ renamed(rebuilt)"}],
  "notices":["[提示] 世界目录 world1 疑似改名自 world ..."],
  "client_only":["fabric-api-like.jar"]
}}
```

- 数据源 `pipeline.run_diff(...) -> DiffOutcome`(pipeline.py:571,批次F 下沉): report(六桶)/mod_pairs/notices(含世界改名提示)/client_only_paths 直出;mod_pairs 序列化字段对齐 CLI reporter 的 ⇄ 渲染所需(具体字段映射在实现计划定)
- **向导步骤**: ①选版本 → **②总览(diff job)** → ③审阅计划 → ④执行。②→③ 不重复扫描: diff job 已把双侧快照落盘,plan job 的 scan 步骤改为「快照存在即复用」(`scan_version` 增复用语义或 server 侧判断;接口不变,实现注记)
- **页面**: 六桶计数卡 + 配对表(⇄/renamed(rebuilt) 等注记) + 提示区(notices 全文 + client_only 明细折叠,复用批次G 的 ≤5 件截断策略于前端展开)
- compat_warnings 页面化归 T3(③审阅页警示区统一处理)

### 3.2 swap 编写下沉(前置)

- `cli.py:441-585` 下沉为 pipeline 公共函数(签名骨架,实现计划细化):

```python
def swap_preflight(dst_version_dir: Path, new_pack: Path) -> tuple[str | None, list[str]]
def swap_install(dst_mods: Path, new_mods_dir: Path, resolver: Callable[[str], bool],
                 dry_run: bool) -> tuple[int, int, int]
def run_swap(...) -> SwapOutcome  # 校验→预检→装包→重扫+规划(modpack_swap=True, rescan_dst=True)
```

- **交互参数化**(沿用 `execute_migration` 的 ask_yes 先例): CLI 的两处 `Confirm.ask`(extras 继续确认 / jar 同名覆盖决策)改为参数——`force`(预确认)+ `overwrite_jars: set[str]`(或 resolver 回调)
- CLI `_cmd_swap` 改薄壳;**行为对拍以现有 CLI swap 测试为等价基准**(下沉前后输出与退出码一致)

### 3.3 swap GUI 化

- `POST /api/swap {src,dst,new_pack,force,overwrite_jars,dry_run}` → job(phases: `preflight|install|scan_dst|plan`)
- **页面流程**: 新包路径选择(目录,须含 mods/) → 预检结果(不兼容 mod 清单 / extras 残留清单,确认或中止) → 覆盖勾选(同名不同内容 jar,默认保留目标=保守) → 装包进度(文件级) → 计划摘要(跳转③审阅复用既有页,compat 警示在③展示)
- dry-run: 装包零写盘,跳过规划(CLI 先例沿用:「基于未发生状态出计划」默认中止)

### 3.4 GUI 生成物锚定统一

- **决策**: 快照/计划统一锚定 `<game_root>/.mcmig/`(与 CLI 一致——数据随游戏走,CLI/GUI 产物互通);`exe/data/<slug>/` 只留 `config.toml` 与 `rules.yaml`(工具自身配置,绿色卸载随删,语义不变)
- `workdir.py`: 布局解析改锚定模式 + 旧布局(`exe/data/<slug>/snapshots|plans`)回退——命中时提示整体迁移(复用 `find_snapshot` 的锚定优先+回退先例,pipeline.py:94-112)
- 影响面: server 的 workdir 消费点 + `doctor` 检查项文案

### 3.5 pywebview 独立窗口

- 新模块 `migration/gui/app.py`: uvicorn daemon 线程(127.0.0.1 随机端口) → `webview.create_window` → `webview.start()`;**窗口关闭=进程退出**(替代 console Ctrl+C,兑现 v0.6 spec §9「进程生命周期」妥协的分发期路径)
- 新入口 `mcmig-gui = migration.gui.app:main`(pyproject scripts);`mcmig gui` 浏览器模式**行为不变**,新增 `--window` 旗标进窗口模式(变更最小化)
- **WebView2 缺失降级**: import/运行失败 → 提示手动打开 URL + WebView2 运行时安装链接;onedir/onefile 均 `--noconsole`,降级路径写日志文件 + 消息框(实现计划定)
- pywebview 新增运行依赖(约 +2~3MB,走系统自带 WebView2);降级文案进 STRINGS

### 3.6 随行收口(可滑移,不阻塞批次)

- compat_warnings 页面化(③审阅页警示区,替换 server 现状只 log.warning)
- modid 碰撞收口(基座+变体同目录共存边角)与 uninstall 措辞——CLI 侧语义微调;排期紧则滑移至批次J,不影响本批验收

## 4. I-B:分发闭环设计

### 4.1 CI(GitHub Actions)

- **`build.yml`**(push / PR / workflow_dispatch): windows-latest + Python 3.13(与主仓一致) → `pip install -e ".[dev]"` → `ruff check .` → `pytest`(全绿基线) → PyInstaller 双形态构建 → upload-artifact
- **`release.yml`**(push tags `v*`): 复用构建步骤 + 生成 `SHA256SUMS.txt`(Get-FileHash) → `gh release create` 上传三资产
- workflow yaml 注释用中文(对齐仓库语言规范)

### 4.2 打包形态与资产契约(命名锁定,updater 依赖此契约)

| 形态 | 资产名 | 内容 | 场景 |
|---|---|---|---|
| onefile 便携 | `mcmig-gui-<ver>-win-x64.exe` | 单文件 GUI(--noconsole) | 小白下载双击即用 |
| onedir 绿色 | `mcmig-<ver>-win-x64.zip` | `mcmig/`(mcmig.exe + mcmig-gui.exe + `_internal/` + filelist.txt) | 全功能,启动快占用低 |

- 校验: `SHA256SUMS.txt`(两资产 SHA256+文件名);README 附 certutil 用法(已宣称,本批补齐)
- **filelist.txt**: CI 打包时生成的 onedir 全量文件清单——更新应用时按它对齐目录(多余文件移入 `.old`),这是 MAA removelist 的正向版,解决「新版删文件后旧文件残留」
- **PyInstaller spec 入库** `tools/packaging/mcmig.spec`(替代手动命令): 两 target(CLI console / GUI noconsole)×两形态;datas 显式收集 `migration/data/*.{yaml,sha256,txt}` 与 `gui/*.html`(package-data 已配,spec 不再依赖 setuptools 行为)
- AGENTS.md「打包 exe」命令节同步更新(§构建与运行命令)

### 4.3 自动更新 `migration/updater.py`(纯库,无 HTTP-server 概念)

```python
REPO: tuple[str, str]                                  # GitHub owner/repo 常量,收尾与 git remote 校对
@dataclass(frozen=True)
class ReleaseInfo:                                     # tag/version/assets{资产名→下载 URL}
def fetch_latest_release(timeout: float = 10.0) -> ReleaseInfo      # GET repos/<REPO>/releases/latest
def is_newer(latest: str, current: str) -> bool        # 数值三元组比较(规避 "0.10"<"0.9" 字典序坑)
def download_asset(url, dest, *, sha256_expected, progress_cb=None) -> Path  # 流式下载+SHA256 强制校验
def apply_update(asset: Path, install_dir: Path) -> None   # 形态自识别: exe→自替换 / zip→staging+filelist 对齐
def cleanup_old(install_dir: Path) -> None             # 启动清理 *.old 与 update-staging/ 残留
```

- **应用算法**(MAA 客户端思想裁剪,原则=校验通过才动旧版):
  - onefile: 运行中 exe 改名 `.old`(Windows 允许改名不允许覆写) → 新 exe 就位 → 提示重启
  - onedir: 解压 `update-staging/` → 按 filelist.txt 逐文件对齐(旧文件改名 `.old`,新版删除的文件同样入 `.old`) → 提示重启
  - 下次启动 `cleanup_old` 兜底清理
- **失败路径**: SHA256 不符 → 删下载、留旧版、三段式报错;断网/超时 → 优雅「检查更新失败」;exe 目录只读 → 「请手动下载」+ Release URL;更新**不触碰 `data/`**(绿色布局天然分离)
- **裁剪声明**: 无 OTA 差分(全量 ~30MB)、无多 CDN/MirrorChyan 式通道;GitHub API 未认证限额 60 次/时,手动+启动低频检查足够
- 依赖: httpx(已是运行依赖),零新增

### 4.4 更新入口(CLI/GUI 平级消费)

- CLI: `mcmig update [--check]`(check 只报有无新版与 Release URL,不下载)
- GUI: 「关于/更新」面板(当前版本号 + 检查按钮) → job 化复用 JobStore+SSE(phases: `check|download|apply`;download 进度=字节级事件) → done 提示重启;文案进 STRINGS

## 5. 关键决策记录

1. **C++ 裁决: 不做**。MAA 选 C++ 的动机是 OpenCV/OCR/DL **计算密集** + 长驻后台 + Android 嵌入 + 多语言绑定(MaaFramework AGENTS.md 自述「C++20 编写,提供 C 语言 API 供各语言绑定调用」);其 win-x64 发行包 **270MB**(C++ ≠ 低占用,大头是模型与原生依赖)。mcmigrator 是分钟级、I/O 密集、依赖 YAML/NBT/TOML 解析生态的 Windows 单平台工具;重写代价=全部核心+481 测试基线(0.10.1)+规则生态,收益不可感知。**降占用的正确杠杆是打包形态**: onedir 免 onefile 每次启动解压到临时目录(启动快、磁盘峰值减半、杀软误报率低),本批双形态都出,玩家自选。
2. **技术栈维持 Web+pywebview**(不重评原生): v0.6 已交付且测试覆盖;pywebview 走系统 WebView2 零额外体积;MAA 自身教训恰是原生 GUI(WPF)跨平台受限,正以 MAAUnified(Avalonia)重写——薄壳可替换比原生观感重要。
3. **生成物锚定统一 game_root**(行为变更): 数据随游戏走,CLI/GUI 产物互通;`exe/data` 只留工具配置,绿色卸载语义不变。
4. **onefile 只出 GUI 单 exe、onedir 出全家桶**: 避免 onefile 双 exe 体积翻倍;小白(双击即用)与高级(CLI)场景分形态满足。
5. **更新=全量+SHA256 强制校验**: 30MB 级做 OTA 差分是过度设计(MAA 的 OTAPacker 为 270MB 日更资源而生);MAA 发行无 checksum 是反面教材,我们必须做。
6. **swap 交互参数化沿用 ask_yes 先例**: 交互决策全部参数化后下沉,CLI 薄壳化,GUI 复用同一管线。
7. **`mcmig gui` 默认浏览器模式不变**: 窗口模式走 `--window` / 独立入口 `mcmig-gui`(变更最小化,老用户零感知)。

## 6. MAA 映射与裁剪清单(证据化)

| MAA 机制(证据) | 我们的对应物 | 采纳状态 |
|---|---|---|
| C ABI: 句柄+JSON(`include/AsstCaller.h`,`AsstApiCallback(msg, details_json, arg)`) | pipeline 库 API + SSE JSON 事件流 | ✅ 已同构(v0.6),无需重构 |
| 多前端薄壳(WPF / MAAUnified / maa-cli,加载同一 MaaCore) | CLI/GUI 平级消费 pipeline | ✅ 已有;pywebview 再加一壳(T5) |
| 数据与程序分离 + 数据自版本(resource/version.json) | `data/` + manifest.sha256 + doctor | ✅ 已有 |
| CI 矩阵构建(`.github/workflows/ci.yml`,cmake/dotnet preset) | build.yml / release.yml | ✅ 本批建(T7-T9) |
| 客户端更新: 解压 staging + removelist + `.old` 备份 + 失败保留旧版 | apply_update(staging + filelist 对齐 + `.old` + cleanup) | ✅ 裁剪采纳(T10) |
| Python ctypes 绑定(核心可从 Python 消费) | 我们核心本是 Python | ➖ 不适用(反向问题) |
| OTA 差分包(OTAPacker/ziplist,多 CDN/MirrorChyan/Sparkle) | — | ❌ 过度设计(30MB 全量足够) |
| 无 checksum 的发行 | SHA256SUMS.txt 强制校验 | 🔄 反其道(必须做) |
| interface.json 声明式界面契约 | rules/profiles YAML(GUI 只读消费) | ⏸ 远期与 .mcmigpack 合流(定位不变) |

## 7. 测试策略

- **pipeline(T2 重构对拍)**: swap 下沉前后行为等价——现有 CLI swap 测试为基准;`run_swap` 参数化交互分支(force / extras / overwrite_jars / dry-run)全覆盖
- **updater 单测(主战场)**: mock httpx(MockTransport)覆盖 `fetch_latest_release` / `is_newer`(含 0.10.0 > 0.9.0 数值比较) / 下载 SHA256 失败留旧版 / `apply_update` 自替换链(tmp_path 模拟运行中 exe 改名) / filelist 对齐(多余文件入 `.old`) / `cleanup_old` 残留清理 / 只读目录降级文案
- **server**: `/api/diff` 与 `/api/swap` 端点 + SSE 事件序列 / 单任务锁互斥 / 更新 job;TestClient 沿用
- **workdir(T4)**: 锚定解析 + 旧布局回退 + 迁移提示
- **页面手测清单**(`tests/gui-manual-checklist.md` 增补): 四步向导 / swap 全程 / 独立窗口 / WebView2 降级 / 真实更新演练(测试 repo 或 draft Release)
- **CI 冒烟**: workflow_dispatch 手动触发 build.yml 验证 Runner 全链;首个正式 Release 前用 pre-release 演练

## 8. 验收标准

1. tag 推送 → Release 自动产出三资产(两形态 + SHA256SUMS.txt),certutil 校验一致
2. 旧版(onefile/onedir 各一)触发更新 → 全量下载 + SHA256 校验 + 自替换/staging 应用 → 重启后为新版且 `data/` 完整保留;SHA256 篡改演练=中止且旧版可用
3. GUI 四步向导: ②总览页可见六桶计数 / ⇄ 配对表 / client_only 与世界提示;③审阅页 compat 警示可见
4. swap 全程 GUI: 选包→预检确认→覆盖勾选→装包→计划摘要,无 console 依赖
5. `mcmig-gui` 双击开独立窗口,关闭窗口=进程退出;WebView2 缺失环境降级提示可操作
6. CLI swap/migrate 行为与下沉前等价(对拍测试全绿);`mcmig update --check` 输出版本与 URL
7. 生成物: GUI 与 CLI 产物互通(同一 game_root 下互见快照/计划);旧 `exe/data` 布局回退不丢数据
8. 全量测试绿 + ruff 干净;版本线 0.10.1 →(批次H)0.11.0 →(批次I)**1.0.0**(0.10.1 = r14 语料入库后的当前基线)

## 9. 任务分解(两实施组,可切两份 plan)

**I-A(GUI 能力)**:
- T1 diff 总览: `/api/diff` job + 四步向导页面 + plan job 快照复用注记
- T2 swap 下沉: `swap_preflight`/`swap_install`/`run_swap` + CLI 薄壳化(对拍)
- T3 swap GUI: `/api/swap` job + 确认/覆盖页 + compat 警示页面化
- T4 锚定统一: workdir 锚定 + 回退 + doctor 文案
- T5 窗口壳: `gui/app.py` + `mcmig-gui` 入口 + WebView2 降级
- T6 随行收口(可滑移): modid 碰撞、uninstall 措辞

**I-B(分发闭环)**:
- T7 打包入库: `tools/packaging/mcmig.spec` 双形态双 target + filelist 生成 + 本地构建验证
- T8 build.yml(push/PR 冒烟)
- T9 release.yml + 资产契约落地
- T10 updater.py 核心(fetch/compare/download/apply/cleanup)
- T11 更新入口: `mcmig update` + GUI 更新面板 job
- T12 收口: README 双语 / AGENTS.md(打包节 + 「是否做 GUI」过时待确认条目) / updater 的 REPO 常量与 git remote 校对 / STRINGS / 版本 1.0.0

依赖: T2→T3;T7→T9→T10(资产契约)→T11;T1/T4/T5 相互独立可并行。测试增量估计 **+45~70**。

## 10. 排期与版本

- **先实现批次H**(0.11.0,spec 已定稿 466d2de)再启动批次I(维护者 2026-10-01 确认);批次H 的 `build_ruleset` 搬迁保留 re-export,GUI 消费不断裂
- 批次I 完成版本 **1.0.0**(分发闭环=社区就绪,v0.6 spec「自用骨架起步、打磨至社区分发」的终点)
- 实施切两份 plan: **I-A(T1-T6)→ I-B(T7-T12)**;两组无强耦合,若分发更紧迫可倒置(I-B 的 T7-T9 纯打包不碰 GUI)
