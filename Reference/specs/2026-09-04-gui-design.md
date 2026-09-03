# GUI 设计(迁移向导)v0.6

> 状态: 设计定稿待评审 | 日期: 2026-09-04 | 前置: 0.5.0(migrate/swap 已落地,251 测试)
> 参考调研: MAA(MaaAssistantArknights)架构思想借鉴见 §8;modswap-830 观察为交互依据

## 1. 目标与非目标

**目标**: 本地 Web GUI 三步迁移向导(选版本→审阅计划→执行),自用骨架起步、打磨至社区分发。
**非目标**: swap 换包 GUI 化(下迭代)、页面多语言切换(仅集中文案)、JS 测试链、文件系统级快照/VSS(见 §7.6 决策)。

## 2. 总体架构

```
页面(单文件 HTML+原生 JS)──SSE/fetch──▶ gui/server.py(薄翻译+安全+单任务锁)
                                              │
cli.py ──────────────────────────────┐        │
                                     ▼        ▼
                                pipeline.py(scan/plan/migrate 编排,无 HTTP 概念)
                                              │
                                core(scanner/rules/planner/executor + fsops)
```

- **进程模型**: `mcmig gui` 启动 FastAPI 绑定 `127.0.0.1:随机端口` → 自动开浏览器;分发期换 pywebview 独立窗口(页面零改动,升级成本约一天)
- **分层纪律**: 管线从 cli.py 下沉为 `migration/pipeline.py`,cli 与 gui 平级消费;gui 不 import cli 的私有函数;核心不感知 HTTP
- **方案对比结论**(备查): tkinter 天花板低 / PySide6 +100MB / TUI 对小白死路 / Electron 过重;Web 方案做「几百行分类表格(折叠/勾选/筛选)」快一个数量级

## 3. 绿色软件目录布局

```
mcmig/(exe 所在文件夹)              冒险活动客户端/(游戏根)
├── mcmig-gui.exe                    └── versions/<版本>/_conflict_backup/  ← 冲突备份
└── data/                                                                  (游戏实例的回滚数据)
    ├── config.toml                  ← game_root 路径等
    └── <游戏根名>/                  ← 多游戏根隔离(版本名可能撞车)
        ├── snapshots/ plans/ rules.yaml
```

- **零 APPDATA/注册表/用户目录写入**(绿色软件理念,卸载=删文件夹)
- 游戏目录侧只产生 `_conflict_backup/`
- 查找顺序: exe 目录(data/) → `cwd/.mcmig`(开发期兼容,现状不迁移)
- exe 目录不可写(玩家丢进 Program Files 等)→ 启动友好报错「请移动到可写文件夹」
- `data/` 内置中文 README.txt 说明每个文件用途与删除后果;README 增「数据与卸载」一节

## 4. API 契约(全部薄翻译,4 个)

| 端点 | 语义 |
|------|------|
| `GET /api/versions` | 列可选版本 + 活跃版本(读 PCL.ini) |
| `POST /api/plan {src,dst}` | **启动 job**(scan×2→plan),返回 job_id |
| `POST /api/migrate {src,dst,ask_yes:[路径],dry_run}` | 启动 job,ask_yes 即②步勾选,转 Executor 的 ask_handler |
| `GET /api/jobs/{id}/events` | SSE 进度流 |

**一切长任务皆 job**(scan 大目录可达 10s+,plan/migrate 同模型;未来 swap GUI 化=多几个阶段事件)。
**单任务锁**: 同时刻仅一个 job,第二个请求拒绝(防双标签页并发写盘)。
**安全**: 仅 127.0.0.1+随机端口;Host 头校验中间件(防 DNS rebinding);无外部请求。

**事件流 schema**(两级,MAA 模型裁剪):

```json
{"type":"phase","name":"scan_src|scan_dst|plan|migrate|done"}
{"type":"file","path":"options.txt","status":"copied","index":42,"total":83,"backed_up":true}
{"type":"done","summary":{"copied":61,"identical":0,"failed":0}}
{"type":"error","what":"复制失败","why":"文件被占用","details":{...}}
```

**错误三段式**: API 错误统一 `{what,why,details}`。

## 5. 页面(三步向导)

- **① 选版本**: game_root(自动读 config,可改) + 源/目标下拉 + 开始
- **② 审阅计划**: origin 分组折叠表格(✅/✏️/❓默认展开,👻/⚙️/⛔/📦 折叠),组计数徽章,行内 path+reason,❓组勾选框(默认全不勾),👻组脚注同 CLI 文案,搜索框过滤
- **③ 执行**: 阶段进度 + 文件级计数 `42/83`(不逐行刷 DOM),失败清单可复制,日志面板(时间戳/级别过滤/出错自动展开),完成态含 PCL 提醒

## 6. 健壮性:文件操作统一门 `migration/fsops.py`

- **单文件事务复制**: 备份(若不同)→ 写 `<name>.mcmig-tmp` → MD5 校验 → `os.replace` 原子换名;任一步失败删 tmp,已备份则回滚原件。执行前清理残留 `*.mcmig-tmp`
- **状态文件原子写**: `write_json_atomic`(plan/snapshot 快照),tmp+replace
- **类型化异常**: SourceMissing/TargetLocked/DiskFull/PermissionDenied → server 统一映射 what/why/details
- **磁盘空间预检**: COPY 动作源体积求和 vs `shutil.disk_usage(dst)`,不足即拒并列出缺口
- **长路径**: `\\?\` 前缀规范化收进 fsops(AGENTS 列明的 >260 坑)
- **清单即 plan 文件**(不另建 journal): plan 列明全部动作,崩溃后重跑 identical-skip=续传;修正 `--force` 重跑覆盖 executed_at 统计的问题
- **完整性校验**: 构建时生成 `migration/data/manifest.sha256`;GUI 启动校验(YAML 静默损坏=迁移行为悄悄变错,必须变响亮报错);新增 `mcmig doctor` 子命令(数据完整性/game_root 可达/exe 目录可写/磁盘空间/版本目录可读);README 补 Release SHA256 校验用法

## 7. 关键决策记录

1. `.mcmig` 状态随软件目录(绿色软件),非游戏目录/APPDATA——卸载零残留;冲突备份留游戏侧(回滚数据归游戏)
2. 应用级 COW(tmp+原子换名+逐文件备份)已覆盖所需;**不上 VSS/文件系统快照**——权限/复杂度/无界回滚能力均超出有界不删除场景所需
3. plan 文件兼任进度清单 journal,不新建机制(YAGNI)
4. 浏览器起步,pywebview 为分发期独立窗口路径(页面零改动)
5. swap dry-run 跳过规划的先例沿用: 一切「基于未发生状态出计划」的场景默认中止并说明

## 8. MAA 借鉴清单(思想非代码)

核心/UI 显式消息契约(SSE 事件 schema)、两级进度(阶段/文件)、错误三段式(what/why/details)、运行日志面板、interface.json 式声明化(远期与 .mcmigpack 合流)。**不采纳**: 三线程/句柄生命周期/OTA/任务队列(我们是秒级一次性向导,非长驻代理)。

## 9. 原型期显式妥协(写明防遗忘)

| 妥协 | 原型期 | 分发期路径 |
|------|--------|-----------|
| job 内存态 | 刷新页面=重走向导 | SSE Last-Event-ID 重放 |
| 进程生命周期 | console 常驻,Ctrl+C 退 | pywebview 窗口+完成停留提示 |
| 页面文案 | 集中 STRINGS 字典不切换 | 加语言切换只动字典 |

## 10. 测试策略

- **pipeline 层**(主战场): 管线下沉后 TDD;251 现有测试全绿是验收线
- **server 层**: TestClient——API 契约/SSE 事件序列/单任务锁/Host 校验/ask_yes 传递/绿色目录解析(含只读目录报错)/manifest 校验
- **fsops 层**: 原子换名(中途失败不留半文件)/回滚/类型化异常/长路径
- **页面**: 手测清单 `tests/gui-manual-checklist.md`(三步/断线刷新/错误渲染各 3-5 条)

## 11. 验收标准

1. `mcmig gui` 三步向导走通一次真实迁移(含 ASK 勾选、冲突备份、PCL 提醒)
2. 迁移中强制关闭页面重开→重跑→identical 续传,无半截文件
3. 断电模拟(tmp 残留)→下次执行自动清理
4. exe 目录只读时启动报友好错误;数据 manifest 损坏时启动响亮报错
5. `mcmig doctor` 全绿;251+新增测试全过;ruff 干净
