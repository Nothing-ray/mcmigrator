# mcmigrator

[English](README.en.md) | [🏠 落地页](README.md)

> Minecraft 整合包版本迁移工具:扫描 / 比对 / 计划 / 执行 / 换包 / 本地向导——在同一整合包的版本隔离文件夹之间迁移玩家状态。

同一整合包从一个 NeoForge 版本文件夹迁到另一个时,你想知道:**玩家在新版本里要保留/改动哪些文件?** `mcmigrator` 用 `scan` 扫描版本文件夹、`diff` 对比快照(迁移导向 6 桶报告),`plan`/`migrate` 全链路迁移玩家状态,`swap` 更换整个整合包;所有写盘动作先备份、可 `--dry-run` 预览,工具自有数据落在 `.mcmig/`(布局见「数据与卸载」)。

## 两种包怎么选

| 发行物 | 形态 | 建议 |
|---|---|---|
| `mcmig-<版本>-win-x64.zip` | **onedir 绿色目录** | **推荐**:解压即用,含 `mcmig.exe`(CLI)与 `mcmig-gui.exe`(窗口);启动快、磁盘占用省 |
| `mcmig-gui-<版本>-win-x64.exe` | onefile 单文件 | 尝鲜:双击即用的窗口版;每次启动需解压到临时目录(较慢),杀软误报率相对较高 |

## 下载

在 [Releases](https://github.com/Nothing-ray/mcmigrator/releases) 页下载任一资产;每份发行附 `SHA256SUMS.txt`(两资产的 SHA256 清单)。下载后校验(Windows 自带命令):

```bat
certutil -hashfile mcmig-<版本>-win-x64.zip SHA256
```

输出与 `SHA256SUMS.txt` 中该文件名的值一致即下载完好;不一致请重新下载。

## 更新方法(基础档)

- **CLI**:`mcmig update [--check]` —— 检查是否有新版(`--check` 仅检查),有则下载 → SHA256 强制校验 → 暂存,并按安装形态打印**替换三步**:
  - **onedir 绿色目录**:①退出所有 mcmig 进程(窗口与命令行);②把下载的 zip 解压,用解压出的 `mcmig` 文件夹内容覆盖现有 `mcmig` 程序目录(同名文件覆盖;你自己的 `data/config.toml` 保留);③重新启动。应用后暂存目录可整目录删除。
  - **onefile 单文件**:①退出当前 mcmig;②用本次下载的 exe 替换你现在使用的 `mcmig-gui*.exe`(覆盖或改名均可);③重新启动。应用后暂存目录可整目录删除。
- **GUI**:步①「关于 / 更新」面板 —— 检查更新 / 下载并校验(可取消,刷新页面进度可恢复),完成态显示「**已下载,尚未应用**」+ 打开暂存位置按钮 + 与上面同源的形态化替换指引。
- **行为约定**:工具**不会自动运行下载的文件**,也不提供「立即运行」;源码运行模式不提供下载(请 `git pull`)。校验失败/清单异常一律拒绝本次下载并清理临时文件。
- 自动应用更新(免手动替换)随后续版本提供,当前为**下载-校验-手动替换**。

## 特性

- **分层哈希**:文本全量 MD5、mods 按文件名集合、bulk(`.sqlite`/`.zip`/`.mca`)按 size——快且精确(玩家会改的文本字节级,不会改的二进制走 size 代理)。
- **数据驱动分类**:规则引擎(`pathspec`,gitignore 语义),分层 first-match-wins(CLI 覆盖 > 用户规则 > 内置默认 > unknown),改规则不重扫。
- **迁移导向 6 桶 diff**:`to_migrate`(必迁)/ `candidate`(待确认)/ `mods`(按文件名集合)/ `only_in_dst`(目标自带)/ `identical`(一致)/ `never`(不迁)。
- **游戏内容零改写**:只写工具自有 `.mcmig/`(快照/计划/任务日志),mods/config/saves 等游戏文件绝不改动。

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
3. **配置文件**:`cp config.example.yaml .mcmig/config.yaml`,改其中的 `game_root`(源码运行;绿色 exe 则在向导步①「游戏根目录」输入框保存,落盘到 `data/config.toml`)

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
| `mcmig diff <src> <dst> --modpack-swap` | 换包验收视角:源独有 mod 归「换包排除」而非 to_add;配对标记保留(新 mod 标 `⇄upgrade` 表示是升级而非全新增) |
| `mcmig plan <src> <dst>` | 生成迁移计划(只读,产出 action 列表) |
| `mcmig migrate <src> <dst>` | 执行已保存的迁移计划(先 plan 后 migrate;覆盖自动备份到 `_conflict_backup/`) |
| `mcmig swap <src> <dst> <新包目录>` | 整合包替换:兼容预检→装包→生成换包迁移计划 |
| `mcmig doctor` | 环境体检:数据完整性 / 游戏目录配置 / 权限 / 磁盘空间 |
| `mcmig-gui` | 以独立窗口启动迁移向导(WebView2 渲染;不可用时自动回退浏览器模式) |
| `mcmig gui [--port N] [--no-browser] [--window]` | 启动本地 Web 迁移向导(自动开浏览器;默认随机空闲端口;`--window` 同独立窗口) |

## 图形界面

三步迁移向导:①选版本 → ②审阅计划 → ③执行迁移。两种启动方式:

- **独立窗口**:`mcmig-gui`(或 `mcmig gui --window`)——pywebview + WebView2 渲染。启动前做数据清单自检与渲染器预检;pywebview 未安装、缺 WebView2 运行时或窗口启动失败时**自动回退浏览器模式**(打印提示;预检挡在开窗之前,不会闪开过时的 MSHTML 窗口)。任务运行中关窗会被阻止并提示(等任务完成或取消后再退);空闲关窗直接退出。
- **浏览器模式**:`mcmig gui`(默认随机空闲端口,自动开浏览器;`--no-browser` 不自动开)。

v0.12 起的向导能力:

- **审阅页摘要**:计划页顶部显示五要素——待迁移 N 项 / 约多少 MB / 待确认 M / 将覆盖(有备份)O / 兼容警告 K,并附「检测范围说明」(说明摘要承诺了什么、没承诺什么);mod 配对术语中文化(升级/改名/重打包),diff 摘要与警示随计划一并呈现,不再只落服务端日志。
- **换包两阶段**:换包面板先发起**只读预检**(`/api/swap/preflight`),呈现三类决策清单(新包中与目标 NeoForge 不兼容的 mod / 目标 mods/ 中新包没有的残留 jar / 同名但内容不同的冲突);确认后**应用装包**(`/api/swap/apply`,服务端持锁三重重验:输入指纹重算 + 版本对身份 + 兼容重跑),装包完成即在同一任务内链式重扫并生成换包迁移计划,直接进入审阅页(旧包独有 jar 不回迁)。被覆盖 jar 先备份到 `<游戏根>/.mcmig/backups/swap/<UTC 时间戳>/`;装包完成进入重规划阶段后取消入口隐藏(该段不可取消,取消请求得 409)。
- **刷新恢复与断线重连**:任务运行中刷新页面,按事件游标续显进度(不重扫不重放);网络瞬断时页面显示「连接中断,正在自动重连…」,恢复后事件按序号去重、不重复渲染,浏览器重连以 `Last-Event-ID` 请求头续订(优先于订阅 URL 中的旧游标参数)。
- **中断恢复**:迁移与换包装包的 write-ahead journal 落在 `<游戏根>/.mcmig/jobs/`;服务异常退出后重启,页面顶部横幅列出中断任务的待核对清单,按指引核对后可逐条 dismiss(该任务仍在运行时清除请求被 409 拒绝);已正常收尾的 journal 档案在下次读取中断清单时自动清扫,不累积。

## 工作方式

1. `scan` 遍历版本文件夹,按分层策略哈希,生成**原始清单快照**(`<game_root>/.mcmig/snapshots/<ver>.snapshot.json`,**不含分类**)。快照含可选身份字段 `resolved_root`(scan 时对版本目录 `resolve()` 的结果,NTFS junction 展开后的真实路径);旧快照缺省 `None`,完全兼容。v2 起快照还会内嵌一份 mod 清单——scan 时顺带读取 `mods/` 里各 jar 的 mod 信息(耗时约 +1~3 秒);旧 v1 快照完全兼容,重扫一次即可升级。
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
| 📋 备份文件 | 复制 | `.bak` 文件(默认归 never,本体不迁;仅用户规则显式提升时跟随父 config 迁移) | `config/create-1.toml.bak` |
| 📦 补 Mod | 复制 | 源独有 mod(玩家额外添加的) | `mods/extra.jar` |
| 📦 换包排除 | 跳过 | `--modpack-swap` 下源独有 mod 视为旧包自带,不回迁;rules.yaml 显式 must_migrate 仍放行;**配对标记保留**(新 mod 标 `⇄upgrade` = 升级而非全新增) | 旧整合包的 `mods/old-pack.jar` |
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
> 世界目录(动态探测)
```

- **用户显式规则 > 孤儿检测**:在 `.mcmig/rules.yaml` 中写 `config/jade/** → must_migrate` 可强制迁移孤儿 config
- **孤儿检测 > 白名单**:白名单中对应 mod 已删除的条目自动失效
- **孤儿检测 = 事实判断**:mod 物理上不在目标 `mods/` 目录 → config 无人认领 → 迁移无意义
- **世界目录层垫底**:服务端动态探测出的世界目录虽按「必迁」注入,但排在所有层之后——内置默认的 never 规则(如世界内的 `.bak` 备份)仍压得过它,不会被误迁

### 整合包替换

更换整个整合包(而非同包升级版本)时加 `--modpack-swap`:源独有 mod 不再回迁(旧包自带,非玩家私货),但用户 `rules.yaml` 中显式 `must_migrate` 的 jar 仍会放行。不加 flag 时,若检测到源独有 mod ≥ 20 个,工具会在 stderr 提示。

完整换包流程:

```bash
mcmig swap <旧版本> <新版本> <新包目录>   # 预检+装包+出计划(会因 NeoForge 不满足而中止,按提示处理;dry-run 不生成迁移计划)
mcmig migrate <旧版本> <新版本>           # 审阅计划后执行复制
```

新版本文件夹请先用 PCL2 安装好对应 NeoForge。migrate 可重入(中断后重跑自动续传)。图形界面中的换包为两阶段交互(只读预检 → 确认装包 + 链式生成换包计划),见「图形界面」。

### .bak 判定法

NeoForge 在玩家游戏内修改 config 时自动生成 `.bak` 备份。工具通过比较 config 与 `.bak` 的 MD5 判断:

- **MD5 不同** → `.bak` 存的是改前的旧版本 → 玩家确实改过 → 迁移
- **MD5 相同** → `.bak` 备份的与当前一致 → mod 自动生成(非玩家修改) → 跳过

### diff 的 mods 桶语义与配对

diff 以**迁移源视角**报告:src=迁移源(旧实例),dst=目标(新实例)。
mods 桶标记:`shared`=两侧同名 jar;`to_add`=**源有目标无**(迁移时会补齐);
`target_only`=目标自带。升级/改名由 modid 配对识别(rich 表 `⇄upgrade`/`⇄renamed` 标记 +
表尾配对脚注;`--json` 输出顶层 `mod_pairs` 数组),不再表现为无关的"删旧+增新"。
配对种类:升级 `⇄upgrade` / 改名 `⇄renamed` 不变;`⇄rebuilt` = 同版本号、文件名带
-Patch/-feature 类尾缀的重新打包(警示前缀 ⚠,与同名桶 rebuilt 语义统一)。
文件名配对兜底共**五级键格**(表驱动:每级=「配对键+前置条件+种类判定」,通用循环逐级
消费上级剩余候选,新增配对形态只需加一行格条目):①全名同族 ②剥变体尾缀(同版本→
rebuilt)③剥尾缀后版本升级(如 `1.1.8-feature`→`1.1.9-fix`)④再剥 `neoforge/forge/
fabric/mc` 等平台装饰词(作者改命名风格)⑤最后剥**装饰词闭集**(`all/patch/fix/
feature/release/up/port/api/lib/compat`,家族键任意位置出现即剥,闭集外之词不剥防伪配);
上级配不上的才进下一级,registry(modid)配对始终优先。

- `rebuilt`:两侧同名同版本号但内容不同(上游重新打包)——diff 标记,plan 默认保留目标侧并警告,不自动覆盖
- `⇄renamed(rebuilt)` ⚠:同版本号的改名对但**两侧 size 异**(上游重建版证据)——kind 仍是 renamed,
  仅追加 `content_differs` 注记(`--json` 的 `mod_pairs` 条目仅在为真时输出该键,消费方零噪声;plan COPY
  行同样镜像 `⇄renamed(rebuilt)`),避免据 renamed 误判「字节等同、可跳过部署」
- `mod_pairs` 条目含 `source` 字段:`registry`(读取 jar 内 mods.toml,需两侧版本目录真实独立)或 `filename`(快照文件名家族归一,复放/junction 场景可用)
- `plan` 报告的 COPY 行同样对配对 jar 的路径追加 `⇄<kind>` 注记(`rebuilt` 镜像 diff 语义加 `⚠` 前缀);注记仅在渲染层,`plan.json` 持久化不含配对(schema 不变)
- 两侧版本目录指向同一路径(NTFS junction)时,注册表配对自动失效并提示,文件名配对兜底
  (双快照不同时刻=标准影子根用法,不再提示,仅降 debug 日志;同刻自比对仍提醒)。
  自比对检测已单点化:同一快照文件直接主判;junction 场景回退「同目录+同刻」;
  复放(无活体目录)时以快照 `resolved_root` 相等且同刻输出「疑似自比对」佐证提示
- mtime 演化通道:未哈希文件(bulk/mods 件,md5=null)在快照中记录 `mtime`;仅当两侧快照
  `resolved_root` 为**同一物理根**(同一实例逐段快照/junction 形态)时启用——「同尺寸但 mtime 异」的
  重写报 modified(note=`mtime`,终端显示为 `mtime(同尺寸重写)`);跨实例迁移(复制必变 mtime)与
  旧快照(无该字段)恒关
- 源侧 mod 已被目标移除时,其 config 会被标注为孤儿(`never/orphan`)——独立 `diff` 与 `plan` 语义一致
- `*.properties`(如服务端 `server.properties`,vanilla 重写导致的转义/时间戳/编码噪声)与
  `*.json`/`*.toml`(mod 启动重写导致的键序/表序噪声)在字节不同但键值语义相同时
  报告为 `identical/semantics` 而非 modified
- JVM 崩溃残留(`hs_err_pid*.log`/`replay_pid*.log`)归入 never 桶,不迁移
- `*.bak` 备份文件(任何目录,不限 config;世代累积的标记物)同样归入 never 桶,本体不迁;
  `.bak` 判定法不受影响,用户规则可覆盖回迁
- 孤儿标注与语义复核依赖快照的 `game_root` 可达;不可达时(跨机复放)自动降级为纯字节对比,stderr 提示一行

### 已知客户端 mod 清单

`migration/data/client_mods.yaml` 维护一份「已知客户端 mod」清单(专服部署时的构造期崩溃风险件,首条 `glacier_dragon`/`frost-dragon` 来自 r11 专服实证)。每条目可给 `modid`(活体注册表通道)与 `family`(文件名家族键,复放通道)双键之任一,并附 `reason` 记录依据。`diff` 时命中清单的 mods 桶行会追加 `client_only` 注记,并在 stderr 输出一行警示。**仅标注、不拦截**——工具是对比器不是部署器,是否排除由你决定。内嵌(JarInJar)客户端件同样可见:modid 命中内嵌件(如 damage-engine 内嵌的 anima 渲染件)时,标注的是**宿主 jar 路径**——专服真正要隔离的物理部署件;清单已收编 `damageengine`/`anima` 两条(r12 专服构造期崩溃实证)。扩充清单:编辑该 yaml 增加条目,再重跑 `tools/gen_manifest.py` 刷新数据完整性清单。

## 数据与卸载

### 工具数据放在哪(路径契约 v3)

工具状态分两层:**软件侧全局态**(跟工具走)与**实例态**(跟游戏实例走,CLI 与 GUI 读写同一位置):

```
mcmig/(exe 所在文件夹)              <游戏根>/.mcmig/(实例态,CLI/GUI 互通)
├── mcmig-gui.exe / mcmig.exe        ├── snapshots/ plans/ rules.yaml
└── data/                            ├── jobs/(任务 journal,中断待核对)
    └── config.toml(仅全局配置)      ├── locks/(预留)/ backups/swap/(换包覆盖备份)
```

- **软件侧全局态**:绿色 exe 下为 `data/config.toml`,**仅存全局配置**(游戏根目录指向),绝不写入 AppData 或用户目录——整个客户端文件夹拷走即带走配置;源码运行下为工作目录 `.mcmig/config.yaml`(兼容现状)。首跑未配置时不报错(欢迎态),由向导步①输入框引导填写并落盘。
- **实例态(`<游戏根>/.mcmig/`)**:快照(`snapshots/`)、迁移计划(`plans/`)、用户规则(`rules.yaml`)、任务日志(`jobs/`,迁移与换包装包的 write-ahead journal——异常退出后据此在页面横幅提示「待核对」,已收尾档案自动清扫)统一锚定游戏根目录,绿色与源码两模式**同址**,多个整合包根互不串数据;`locks/` 为跨进程实例锁登记位(预留),`backups/swap/<UTC 时间戳>/` 为换包装包的覆盖备份(批次I W3 起启用)。
- **旧布局迁移说明**:旧绿色布局 `exe/data/<游戏名>/snapshots|plans|rules.yaml` 与旧源码布局 `cwd/.mcmig/` 实例态为**只读回退**——工具绝不自动写入旧位置;旧快照命中时照常读取并提示建议整体迁移;`rules.yaml` 两处并存时**以新位置为准**(旧文件忽略并提示)。「使用旧布局 plan」的提示为 **CLI**(`mcmig migrate`)侧行为——GUI 生成计划恒先重扫两侧,快照与计划文件始终落在锚定位置(旧布局快照仅发只读提示,不参与定位)。建议把旧 `snapshots/`、`rules.yaml` 整体搬至 `<游戏根>/.mcmig/` 后删除旧文件。

### 游戏侧会写什么

工具在游戏根目录创建的唯一目录是 `.mcmig/`(快照、迁移计划、用户规则与任务日志,纯工具产物——快照重扫即重建;任务日志仅保留未收尾档案,已正常收尾的在下次读取中断清单时自动清扫,删除未收尾档案只会让「中断待核对」横幅消失);除此之外不创建任何其他工具目录。迁移期间唯一写入游戏内容的是**冲突备份**:同名但内容不同的文件在覆盖前会先备份到 `<目标版本>/_conflict_backup/`(镜像相对路径结构;首份备份为覆盖前的原件,重跑不会覆盖)。换包(swap)则按用户确认把新包 `mods/` 装入目标版本,被覆盖 jar 先备份到 `<游戏根>/.mcmig/backups/swap/<UTC 时间戳>/`。迁移完成并确认无误后,这些备份文件夹均可安全删除。

### 如何卸载

1. 删除 mcmig 程序文件夹(绿色 exe 下 `data/config.toml`(仅全局配置)在其中,一并删除即清空软件侧状态;实例态见第 2 步);
2. 可选:删除 `<游戏根>/.mcmig/`(快照/计划/规则/任务日志,纯工具产物;快照重扫即重建,删除任务日志会一并清掉「中断待核对」提示);
3. 可选:删除各 `<游戏根>/versions/<版本>/_conflict_backup/`(留着也无害);
4. 游戏**内容**目录(mods/config/saves…)本身不会被工具改动,无需清理;游戏根内除第 2 步的 `.mcmig/` 外不残留其他工具文件。

### 校验下载完整性(SHA256)

每次 GitHub Release 附带两种资产与一份 `SHA256SUMS.txt` 校验清单。下载后校验(Windows 自带命令):

```bat
certutil -hashfile <下载的文件> SHA256
```

将输出与 `SHA256SUMS.txt` 中该文件名的值比对,一致即下载完好;不一致请重新下载。`mcmig update` 的下载路径会自动做同一校验,失败即拒绝并清理。

## 项目结构

```
mcmigrator/
├── migration/          # 工具源码(hashing/rules/classifier/snapshot/scanner/differ/pipeline/updater/gui)
├── tests/              # 单元 + 端到端测试(pytest)
├── tools/packaging/    # 发行构建:build.py(双形态)/两个 PyInstaller spec/锁定文件/发布守卫
├── docs/               # 递延账本(backlog.md)与 SDD 计划留档
├── Reference/          # 设计文档(specs / design / plans)
├── data/default_rules.yaml  (在包内)  # 内置默认分类规则
├── config.example.yaml # 配置模板
├── AGENTS.md           # 项目规范(给 AI 协作者)
└── README.md
```

> 数据文件哈希按 LF 归一化生成/校验,`core.autocrlf=true` 的检出不会误报数据损坏(F24);
> `.gitattributes` 已锁 `migration/data/` 行尾,贡献者无需手工转行尾。

## 设计与文档

详细设计见 `Reference/`:`specs/`(版本设计规格)、`design/`(子系统设计备忘)、`plans/`(实现计划)。

## 贡献

本地开发/跑测试:安装开发依赖组 `pip install -e ".[dev]"`(含 pytest 与 ruff)。**uv 用户注意**:`uv sync` 精确模式只装运行时依赖、会剪掉 pytest,测试环境请改用 `uv pip install -e ".[dev]"`。

**构建发行物**(需 Python 3.13;在独立构建 venv 内,依赖按 `tools/packaging/requirements-win-build.txt` 锁定文件安装):

```bat
python -m venv .venv-build
.venv-build\Scripts\python.exe -m pip install -r tools\packaging\requirements-win-build.txt
.venv-build\Scripts\python.exe tools\packaging\build.py --form onefile
.venv-build\Scripts\python.exe tools\packaging\build.py --form onedir
.venv-build\Scripts\python.exe tools\packaging\build.py --sums dist-release
```

tag `v*` 推送后由 GitHub Actions 自动执行同一流程(发布守卫先校验 tag==pyproject==`__version__` 三处一致)。

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

世界目录不必叫 `world`:scan 会动态探测(`server.properties` 的 `level-name` 指向目录 + 顶层直接含 `level.dat` 的目录,覆盖任意命名/改名留存/多世界形态),记入快照可选字段 `world_dirs`;scan 汇总/diff/plan 均按探测结果在**规则最低层**(内置默认之后)注入 must_migrate——内置 never 规则(如世界内 `.bak`)仍优先,客户端 `saves/<名>/level.dat` 二层结构天然不误触,空清单时输出与旧版逐字节一致。

世界目录随改名留存(world → world_backup_日期)原本会报成「源侧必迁 + 目标侧独有」的千级假警报;当旧路径从源侧 `world_dirs` 消失、新路径在目标侧出现,且同相对子路径同尺寸占比 ≥90% 时,diff 在 stderr 输出一条 `疑似世界目录改名` 提示(内容未消失,已随目录改名迁移)——仅解释、不重分类。

零拷贝接入技巧:用 NTFS Junction(`mklink /J`)把服务器目录映射为 `versions\<名称>`,即可直接 `scan`/`diff`,无需复制 2GB+ 的服务端目录。换装前后各 scan 一次即可得到完整对比。

## 路线图

- ✅ v0:`scan`/`diff` 只读对比(已完成)
- ✅ v1 Phase 1:`plan` 子命令 + config 玩家改动判定(`.bak` 法 + 白名单)(已实现)
- ✅ v1 Phase 2:`migrate` 实际写盘 + `swap` 换包编排(已实现;回滚见未来)
- ✅ v0.6:事务式文件操作(fsops)+ 绿色 exe 数据布局 + `doctor` 体检 + 本地 Web 向导 `mcmig gui`(已实现)
- ✅ v0.12:GUI 换包两阶段(预检→装包)+ 独立窗口壳 `mcmig-gui`(WebView2,缺失降级浏览器)+ 页面刷新恢复/断线自动重连 + journal 中断横幅 dismiss 与自动清扫(已实现)
- ✅ v0.13:分发闭环——PyInstaller 双形态入库 + GitHub Actions 自动构建发布(双平台全检 + SHA256SUMS)+ 更新基础档(`mcmig update` 与向导「关于/更新」面板:检查→下载→校验→暂存→手动替换)(已实现)
- 📋 v1 Phase 3:Manifest 决策沉淀(自动记忆迁移决策)
- 📋 未来:Mod Profile(META-INF 解析)+ 内容检测

详见 [`Reference/specs/`](Reference/specs/)。

## 附录:占用实测(0.13.0 试发后补录)

onefile / onedir 的体积、冷启动时间与磁盘峰值实测数据随 0.13.0 试发补录(非验收承诺,仅供选包参考)。

## 许可证

MIT — 见 [LICENSE](LICENSE)。
