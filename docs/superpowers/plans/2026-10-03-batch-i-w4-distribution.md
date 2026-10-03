# 批次I-W4 实现计划:分发闭环(T11-T15,v1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 W4 spec(v3)分发闭环:打包双形态入库(`_form` 标记+致命错误消息框契约)、CI 自动构建发布(发布守卫+SUMS)、更新基础档(`mcmig update`+GUI 面板,uuid 独占暂存)、T13.5 八项滑移收编、T15 收口至 0.13.0。

**Architecture:** 打包侧以「独立工作副本改写 `_form.py` → PyInstaller 两 spec(共享资产命名契约)→ build.py 后处理(filelist/zip/SUMS)」实现源码树零污染;updater 为纯库模块(CLI/GUI 平级消费,`_open_client` 单点可注入 mock,`_form.FORM` 为资产选择唯一事实来源);GUI 更新走既有 job+SSE 底座(kind="update" 四件契约),页面复用 W3 Presentation Model(applyEvent 扩 bytes 通道+applyUpdateEvent 专态)。

**Tech Stack:** Python 3.11+ / pytest / httpx(MockTransport)/ PyInstaller 6(锁定文件钉版)/ GitHub Actions。

**Spec:** `Reference/specs/2026-10-03-batch-i-w4-distribution-design.md`(v3,`346cfc8`)——执行者两个文件都读;本计划从 spec §4.1(T11)/§4.2(T12)/§4.3(T13)/§4.4(T13.5)/§4.5+§5(T15)立论。

**执行前置:** main=`346cfc8`(0.12.0),测试基线 **743 passed + 2 skipped**(执行时以 `pytest --collect-only` 实际值为准)。

## Global Constraints

- 注释/docstring 全中文;公有函数中文 docstring;类型提示全覆盖;路径一律 pathlib;文件 UTF-8 无 BOM。
- 每任务退出条件: **全量测试绿 + `python -m ruff check migration/ tests/` 零告警**(ruff 钉 0.15.20;主仓有用户未跟踪草稿目录,勿跑全仓 ruff)。
- 每任务一个中文 conventional commit;提交前 `pytest` 全量通过(venv: `.venv/Scripts/python.exe -m pytest`)。
- **推送冻结**(用户 2026-10-02 令):全程不 `git push`;tag/试发(T15 之后)待用户放行。
- **不触碰用户未跟踪文件**(racing-event/、goal.md、.zcode/、.zcodeignore、两个 zip、F1 zip);新产物目录(`.venv-build/`、`.build-work/`、`dist-release/`)必须先进 .gitignore 再创建。
- **主仓 `.venv` 不装 pywebview**(J6 纪律):一切打包操作在独立 `.venv-build/`(gitignore)进行;自动化测试零依赖 pywebview(既有 sys.modules 假件模式)。
- `migration/data/*.yaml` 本波不改;若万一改动,必须重跑 `python tools/gen_manifest.py` 并提交 manifest。
- 版本号:Task 1-13 期间不动;Task 14 统一升 **0.13.0**(pyproject 与 `migration.__version__` 两处同改)。
- 新增 GUI 文案一律进 `migration/gui/STRINGS.py`(server 侧)或页面 `PAGE_STRINGS`(页面侧);行为红线:updater/页面**不得出现「立即运行」**,完成态固定「已下载,尚未应用」。
- 行为变更白名单(超出须先回 spec 改文档再改代码;W1-W3 白名单继续有效):
  1. `app.py` 致命分支(WorkdirError/服务启动失败/就绪超时/降级包装层)新增 `_notify_box` 消息框——仅新增可见性,退出码与打印不变(spec §4.1.2 v3 契约);
  2. `Job.can_cancel` 白名单加 `"update"`;`Job.emit` 新增 `type=="progress"` 分支(记 `_last_progress`);`snapshot()` 新增 `progress_bytes` 键(向后兼容新增字段);
  3. 页面 `applyEvent` 新增 `type=="progress"` 分支(维护 `jobModel.bytes`);`handlersForKind` 增加 update 分派;
  4. pyproject 把 `pywebview` 从核心依赖移入可选组 `gui`(源码安装面收窄;测试不依赖);
  5. `pipeline.swap_install` 新增可选参数 `progress_cb`(默认 None,行为不变);
  6. `gui/app.main` 开始解析 argv 的 `--port`/`--no-browser`(此前忽略;窗口模式 honor --port)。
- 涉及 index.html 的任务必须同步更新 `tests/test_page_contract.py`(源码契约钉,W1W2 教训①)。

## Review Focus

1. **noconsole 静默致命退出**:冻结 GUI 页面可用前的致命错误(不可写目录/服务启动失败/就绪超时/降级自身失败)只 print——devnull 兜底下玩家双击无声退出码 2——Task 1 红测四条(WorkdirError/OSError/就绪超时/降级 rc≠0),monkeypatch `_notify_box` 断言调用且文案含恢复指引。
2. **下载物隔离与完整性**:同版本资产重传时共享路径被后任务改写、跨进程并发 .part 互踩、SHA256/302 处理不严——Task 7 红测 `test_same_version_reupload_does_not_touch_earlier_path`+`test_download_two_runs_use_distinct_uuid_dirs`+SHA256 失败清理;Task 6 红测 302 跟随回归与 SUMS 四失败形态。
3. **更新进度/终态刷新恢复丢失**:下载中刷新页面丢字节进度或终态(两态页变空白)——Task 9 `progress_bytes` 入 GET 快照+done 载荷同源;Task 10 node 行为测 `test_update_progress_and_done_survive_restore`。
4. **构建形态误判**:kind 从 exe 文件名/入口形态/`_MEIPASS` 推断(两形态都 frozen、onedir 也有 GUI)——Task 6 `pick_asset` 只读 `_form.FORM` 三值测试;Task 3 `_verify_form_in_dist` onedir 产物内后验+工作副本改写纪律测试。
5. **误执行/越界打开下载资产**:「打开位置」直接 startfile 资产本体(=执行下载物)或接受暂存根外路径——Task 10 契约测(页面无「立即运行」、server 端 explorer /select)+功能测(根外 400)。

---

## 任务↔spec 映射

| 计划任务 | spec 任务 | 交付 |
|---|---|---|
| Task 1 | T11(§4.1.2 v3 契约+§4.1.4 标记) | `_form.py`+app.py 致命错误消息框 |
| Task 2 | T11(§4.1.2 依赖锁定/D5) | gui 可选组+requirements-win-build.txt+gitignore |
| Task 3 | T11(§4.1.1/§4.1.2/§4.1.3/§4.1.4) | 薄入口/双 spec/build.py |
| Task 4 | T12(§4.2.1) | build.yml(测试 win/linux+锁定构建) |
| Task 5 | T12(§4.2.2+§5 守卫) | release.yml+check_release.py+SUMS |
| Task 6 | T13(§4.3.1 纯函数层) | updater 检查/选资产/比版/SUMS |
| Task 7 | T13(§4.3.1 下载暂存) | download_and_verify/staging/plan_update |
| Task 8 | T13(§4.3.3) | CLI `mcmig update [--check]` |
| Task 9 | T13(§4.3.4 job 契约) | GUI 端点+update job 四件 |
| Task 10 | T13(§4.3.4 面板+§4.3.2 红线) | 页面更新面板+打开位置安全 |
| Task 11 | T13.5 第 1 项 | swap 装包逐 jar 进度 |
| Task 12 | T13.5 第 2/3/4/8 项 | 页面收口四项 |
| Task 13 | T13.5 第 5/6/7 项 | --window 转发+description+node 测试入库 |
| Task 14 | T15(§4.5)+版本 | README/AGENTS/backlog/0.13.0/手测清单 K 组 |

T14(事务档)不在本计划——批次J 独立,spec §6 评估门先行。

---

### Task 1: 构建形态标记 `_form.py` + 致命错误消息框契约

**Files:**
- Create: `migration/_form.py`
- Modify: `migration/gui/app.py`(`_fallback_browser` 约 210-220、`main` 致命分支约 281-302)
- Test: `tests/test_gui_app.py`(追加)

**Interfaces:**
- Produces: `migration/_form.py` 的模块级常量 `FORM: str`("source" 入库默认;onefile/onedir 由 Task 3 构建期改写固化)——Task 6/9/10 消费。
- Produces: `app.py` 各致命路径保证「消息框(失败原因+恢复指引)→ 退出码 2」顺序;`_notify_box(title, text)` 签名不变(测试注入点)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_gui_app.py 追加(spec §4.1.2 v3 契约:冻结 GUI 页面可用前致命错误必须可见)
import types

from migration.gui.server import JobStore


def _fake_webview_ok(monkeypatch):
    """注入「可用」假 webview(渲染器=edgechromium),让 main 走到起服务之后。"""
    fake_guilib = types.ModuleType("webview.guilib")
    fake_winforms = types.ModuleType("webview.platforms.winforms")
    fake_winforms.renderer = "edgechromium"
    fake_guilib.initialize = lambda gui: fake_winforms
    fake = types.ModuleType("webview")
    fake.guilib = fake_guilib
    fake.create_window = lambda *a, **k: None
    fake.start = lambda **k: None
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setitem(sys.modules, "webview.guilib", fake_guilib)
    monkeypatch.setitem(sys.modules, "webview.platforms.winforms", fake_winforms)


def _fake_app() -> types.SimpleNamespace:
    store = JobStore()
    return types.SimpleNamespace(state=types.SimpleNamespace(jobs=store))


def test_fatal_workdir_error_shows_message_box(monkeypatch, capsys):
    """软件目录不可写(WorkdirError)→ 消息框+退出码 2,不得只打印。"""
    from migration.workdir import WorkdirError
    import migration.gui.app as gui_app

    boxes: list[tuple[str, str]] = []
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: [])
    monkeypatch.setattr(gui_app, "_notify_box", lambda t, x: boxes.append((t, x)))
    _fake_webview_ok(monkeypatch)

    def _boom():
        raise WorkdirError("软件目录不可写", "软件目录不可写,请把 mcmig 移动到可写的文件夹后重试")

    monkeypatch.setattr(gui_app, "create_app", _boom)
    rc = gui_app.main([])
    assert rc == 2
    assert boxes, "致命路径必须弹消息框(noconsole 形态 print 不可见)"
    assert "可写" in boxes[0][1]


def test_fatal_service_start_failure_shows_message_box(monkeypatch):
    """本地服务启动失败(OSError)→ 消息框+退出码 2。"""
    import migration.gui.app as gui_app

    boxes: list[tuple[str, str]] = []
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: [])
    monkeypatch.setattr(gui_app, "_notify_box", lambda t, x: boxes.append((t, x)))
    _fake_webview_ok(monkeypatch)
    monkeypatch.setattr(gui_app, "create_app", _fake_app)

    def _boom(app, port):
        raise OSError("10048")

    monkeypatch.setattr(gui_app, "_start_server_thread", _boom)
    assert gui_app.main([]) == 2
    assert boxes and "重试" in boxes[0][1]


def test_fatal_ready_timeout_shows_message_box(monkeypatch):
    """服务就绪超时 → 消息框(含改用浏览器模式指引)+退出码 2。"""
    import migration.gui.app as gui_app

    boxes: list[tuple[str, str]] = []
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: [])
    monkeypatch.setattr(gui_app, "_notify_box", lambda t, x: boxes.append((t, x)))
    _fake_webview_ok(monkeypatch)
    monkeypatch.setattr(gui_app, "create_app", _fake_app)

    class _Srv:  # 最小 server/thread 假件:finally 回收路径可用
        should_exit = False

    class _Thr:
        @staticmethod
        def is_alive():
            return False

    monkeypatch.setattr(gui_app, "_start_server_thread", lambda app, port: (_Srv(), _Thr()))
    monkeypatch.setattr(gui_app, "wait_server_ready", lambda port, timeout=10.0: False)
    assert gui_app.main([]) == 2
    assert boxes and "浏览器模式" in boxes[0][1]


def test_fallback_browser_failure_shows_message_box(monkeypatch):
    """降级浏览器模式自身失败(非零退出)→ 消息框兜底(不依赖真 webview 未装路径)。"""
    import migration.gui.app as gui_app

    boxes: list[tuple[str, str]] = []
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: [])
    monkeypatch.setattr(gui_app, "_notify_box", lambda t, x: boxes.append((t, x)))
    monkeypatch.setattr(builtins, "__import__", _no_webview)  # 复用本文件既有拦截器
    monkeypatch.setattr("migration.cli._cmd_gui", lambda ns: 2)
    assert gui_app.main([]) == 2
    assert boxes and "doctor" in boxes[0][1]
```

> 注:`_no_webview` 是 `tests/test_gui_app.py` 既有的 import 拦截器(见 `test_main_falls_back_to_browser_without_pywebview`);`builtins` 已在该文件 import。若拦截器名不同,以文件内实际为准复用。

```python
def test_form_module_defaults_to_source():
    """_form.FORM 入库默认 source(构建期才改写,spec D7)。"""
    from migration import _form

    assert _form.FORM == "source"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gui_app.py -k "message_box or form_module" -v`
Expected: 5 条 FAIL(`_form` 不存在 ImportError;致命分支无消息框断言失败)

- [ ] **Step 3: 最小实现**

```python
# migration/_form.py
"""构建形态标记(批次I-W4 spec §4.1.4/决策 D7)。

``FORM`` 是更新资产选择的**唯一事实来源**:source/onefile/onedir 三值。
入库恒为 ``"source"``;打包脚本在**独立工作副本**上按产物改写为
onefile/onedir 后构建(spec v3 改写纪律),产物内即固化——运行时不得
从 exe 文件名、入口形态、显示模式或 ``sys._MEIPASS`` 有无推断形态。
"""

FORM: str = "source"
```

`app.py` 四处修改(只加消息框,不动打印与退出码):

```python
# ① WorkdirError 分支(app.py:283-285)
    except WorkdirError as e:
        _print(f"[错误] {e.what}:{e.why}")
        _notify_box("mcmig 启动失败", f"{e.what}:{e.why}")
        return 2
# ② 服务启动失败分支(app.py:296-298)
        except OSError as e:
            _print(f"[错误] 本地服务启动失败(端口 {port}):{e}")
            _notify_box(
                "mcmig 启动失败",
                f"本地服务启动失败(端口 {port}):{e}\n请重试 mcmig-gui,或改用 mcmig gui(浏览器模式)。",
            )
            return 2
# ③ 就绪超时分支(app.py:300-302)
        if not wait_server_ready(port):
            _print("[错误] 本地服务就绪超时,无法打开窗口。请重试 mcmig-gui,或改用 mcmig gui(浏览器模式)。")
            _notify_box(
                "mcmig 启动失败",
                "本地服务就绪超时,无法打开窗口。\n请重试 mcmig-gui,或改用 mcmig gui(浏览器模式)。",
            )
            return 2
```

```python
# ④ _fallback_browser 整体替换(致命错误可见性 v3:降级路径自身失败兜底)
def _fallback_browser(argv: Sequence[str] | None) -> int:
    """降级浏览器模式:转发 ``cli._cmd_gui``(uvicorn 起停由其自理)。

    import 必须在**函数体内** lazy 取——monkeypatch ``migration.cli._cmd_gui``
    才能生效;argv 经 cli 解析器规整为 Namespace 后透传。

    致命错误可见性(spec §4.1.2 v3):降级路径自身的失败(抛错或非零退出)
    必须弹中文消息框——冻结无控制台形态下 print 不可见,不得静默退出。
    """
    from ..cli import _cmd_gui, build_parser

    ns = build_parser().parse_args(["gui", *(argv if argv is not None else ())])
    try:
        rc = _cmd_gui(ns)
    except Exception:  # noqa: BLE001 — 降级路径致命失败兜底(消息框可见,非静默)
        log.exception("浏览器降级模式启动失败")
        _notify_box(
            "mcmig 启动失败",
            "浏览器模式启动失败。\n请运行 mcmig doctor 自检,或到项目发布页重新下载完整发行包。",
        )
        return 2
    if rc != 0:
        _notify_box(
            "mcmig 启动失败",
            "浏览器模式未能启动。\n请运行 mcmig doctor 自检,或到项目发布页重新下载完整发行包。",
        )
    return rc
```

同时更新 `main` docstring ⑧ 行:「自检失败/服务启动失败/就绪超时 2(中文报错**+消息框**)」。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gui_app.py -v && .venv/Scripts/python.exe -m pytest`
Expected: 全绿(743+5 新增)

- [ ] **Step 5: 提交**

```bash
git add migration/_form.py migration/gui/app.py tests/test_gui_app.py
git commit -m "feat(batch-i-w4): 致命错误消息框契约+构建形态标记 _form(T11 基座)"
```

---

### Task 2: gui 可选依赖组 + Windows 构建锁定文件

**Files:**
- Modify: `pyproject.toml`(dependencies 移除 pywebview;新增 `[project.optional-dependencies].gui`)
- Modify: `.gitignore`(追加构建产物目录)
- Create: `tools/packaging/requirements-win-build.txt`
- Test: `tests/test_packaging_layout.py`(新建)

**Interfaces:**
- Produces: 可选组 `gui = ["pywebview>=5.0"]`(源码宽松)+ 锁定文件(发行精确,含 `pyinstaller`/`pyinstaller-hooks-contrib` 钉版)——Task 3/4/5 的构建环境一律 `pip install -r tools/packaging/requirements-win-build.txt`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_packaging_layout.py(新建)
"""打包基建结构测试:依赖分组/锁定文件/入口与 spec 契约(spec §4.1)。"""

from pathlib import Path

import tomllib

_ROOT = Path(__file__).resolve().parents[1]
_PKG = _ROOT / "tools" / "packaging"


def _pyproject() -> dict:
    return tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_moves_pywebview_to_gui_group():
    """D5:核心依赖不含 pywebview;可选组 gui 收容(源码默认=CLI+浏览器模式)。"""
    data = _pyproject()
    names = [d.split(">=")[0].split("<")[0].strip().lower() for d in data["project"]["dependencies"]]
    assert "pywebview" not in names
    gui = data["project"]["optional-dependencies"]["gui"]
    assert any(d.lower().startswith("pywebview") for d in gui)


def test_win_build_lockfile_pins_runtime_and_tools():
    """锁定文件(spec §4.1.2):运行依赖+PyInstaller 工具链全部精确钉版(==)。"""
    lines = (_PKG / "requirements-win-build.txt").read_text(encoding="utf-8").splitlines()
    pins = {ln.split("==")[0].strip().lower() for ln in lines if "==" in ln and not ln.startswith("#")}
    for name in (
        "rich", "pyyaml", "pathspec", "fastapi", "uvicorn", "httpx",
        "pywebview", "pyinstaller", "pyinstaller-hooks-contrib",
    ):
        assert name in pins, f"锁定文件缺 {name} 钉版"


def test_gitignore_covers_build_workspaces():
    """构建工作区不入库:独立 venv/工作副本/产物目录(spec §4.1.4 改写纪律)。"""
    text = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in (".venv-build/", ".build-work/", "dist-release/"):
        assert entry in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_packaging_layout.py -v`
Expected: 3 FAIL(pywebview 仍在核心依赖;锁定文件/条目不存在)

- [ ] **Step 3: 最小实现**

`pyproject.toml`——dependencies 删除 `"pywebview>=5.0"` 行及其注释,新增:

```toml
[project.optional-dependencies]
# 窗口壳可选组(spec §4.1.2/D5):默认安装=CLI+浏览器模式;
# 打包与想要独立窗口的源码用户 `pip install -e ".[gui]"`
gui = ["pywebview>=5.0"]
```

(dev 组保持原样,两键并存。)

`.gitignore` 追加:

```gitignore
# W4 打包工作区(独立构建 venv/工作副本/发行产物,均不入库)
.venv-build/
.build-work/
dist-release/
```

创建构建 venv 并生成锁定文件(Git Bash;**不动主仓 .venv**):

```bash
python -m venv .venv-build
.venv-build/Scripts/python.exe -m pip install -U pip
.venv-build/Scripts/python.exe -m pip install -e ".[gui]" pyinstaller pyinstaller-hooks-contrib
{ printf '# mcmigrator Windows 发行构建锁定文件(spec 4.1.2/D5)\n# 再生成: .venv-build 内 pip install -e ".[gui]" pyinstaller pyinstaller-hooks-contrib 后\n#           python -m pip freeze 覆盖正文(保留本头注释);升级后必须重跑本地冒烟\n'; .venv-build/Scripts/python.exe -m pip freeze; } > tools/packaging/requirements-win-build.txt
```

检查生成文件:正文应含 `fastapi==`/`pyinstaller==`/`pywebview==` 等全量钉版行(含传递依赖);`-e .` 行不得出现(pip freeze 对 editable 不输出,若有则手工剔除)。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_packaging_layout.py -v && .venv/Scripts/python.exe -m pytest`
Expected: 全绿(主仓 venv 未装 pywebview,pyproject 变更不影响既有测试)

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml .gitignore tools/packaging/requirements-win-build.txt tests/test_packaging_layout.py
git commit -m "build(batch-i-w4): gui 可选依赖组拆分+Windows 构建锁定文件+gitignore(T11)"
```

---

### Task 3: 打包入库——薄入口/双 spec/构建编排

**Files:**
- Create: `tools/packaging/entry_gui.py`、`tools/packaging/entry_cli.py`
- Create: `tools/packaging/gui-onefile.spec`、`tools/packaging/app-onedir.spec`
- Create: `tools/packaging/build.py`
- Test: `tests/test_packaging_layout.py`(追加)

**Interfaces:**
- Consumes: Task 2 的锁定文件(构建 venv 环境)。
- Produces: `build.py` 的 `asset_names(form: str, version: str) -> tuple[str, str]`(返回 `(mcmig-gui-<ver>-win-x64.exe, mcmig-<ver>-win-x64.zip)`)、`make_filelist(dist_app: Path) -> None`、`make_sums(out_dir: Path) -> Path`——Task 5 的 release.yml 与测试消费。
- Produces: CLI 用法 `python tools/packaging/build.py --form onefile|onedir [--out dist-release]` 与 `--sums <dir>`(Task 4/5 消费)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_packaging_layout.py 追加
import importlib.util


def _load_build_py():
    spec = importlib.util.spec_from_file_location("mcmig_build", _PKG / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_asset_names_contract():
    """资产命名=更新契约(spec §4.1.1,一处锁定)。"""
    mod = _load_build_py()
    assert mod.asset_names("onefile", "0.13.0") == ("mcmig-gui-0.13.0-win-x64.exe", "mcmig-0.13.0-win-x64.zip")


def test_make_filelist_posix_sorted_and_excludes_self(tmp_path):
    """受管清单(spec §4.1.3):POSIX 相对路径、排序、不含自身;UTF-8 无 BOM。"""
    mod = _load_build_py()
    (tmp_path / "_internal" / "migration" / "data").mkdir(parents=True)
    (tmp_path / "mcmig.exe").write_bytes(b"1")
    (tmp_path / "mcmig-gui.exe").write_bytes(b"2")
    (tmp_path / "_internal" / "migration" / "data" / "rules.yaml").write_bytes(b"3")
    mod.make_filelist(tmp_path)
    text = (tmp_path / "filelist.txt").read_text(encoding="utf-8")
    assert not text.startswith("\ufeff")
    assert text.splitlines() == [
        "_internal/migration/data/rules.yaml",
        "mcmig-gui.exe",
        "mcmig.exe",
    ]


def test_make_sums_two_space_format(tmp_path):
    """SUMS 格式(spec §4.2.2):sha256+两空格+文件名,UTF-8 无 BOM。"""
    import hashlib

    mod = _load_build_py()
    (tmp_path / "a.exe").write_bytes(b"hello")
    (tmp_path / "b.zip").write_bytes(b"world")
    sums = mod.make_sums(tmp_path)
    lines = sums.read_text(encoding="utf-8").splitlines()
    assert lines == [
        f"{hashlib.sha256(b'hello').hexdigest()}  a.exe",
        f"{hashlib.sha256(b'world').hexdigest()}  b.zip",
    ]


def test_entry_scripts_absolute_import_and_stdio_guard():
    """薄入口(评审 P1-1/P1-2):绝对导入;GUI 入口先兜底 stdio 再 import 主程序。"""
    gui = (_PKG / "entry_gui.py").read_text(encoding="utf-8")
    assert "from migration.gui.app import main" in gui
    assert "devnull" in gui
    cli = (_PKG / "entry_cli.py").read_text(encoding="utf-8")
    assert "from migration.cli import main" in cli


def test_spec_files_split_by_form_with_console_flags():
    """两形态各自 spec(一张 spec 无法同时固化两种 FORM 标记):gui=console=False。"""
    one = (_PKG / "gui-onefile.spec").read_text(encoding="utf-8")
    two = (_PKG / "app-onedir.spec").read_text(encoding="utf-8")
    assert "entry_gui.py" in one and "console=False" in one
    assert "webview.platforms.winforms" in one
    assert "entry_cli.py" in two and "entry_gui.py" in two
    assert 'name="mcmig"' in two and 'name="mcmig-gui"' in two


def test_build_script_workcopy_discipline():
    """改写纪律(spec §4.1.4 v3):独立工作副本(.build-work/<form>),两形态分离;onedir 后验 FORM。"""
    text = (_PKG / "build.py").read_text(encoding="utf-8")
    assert ".build-work" in text
    assert "_verify_form_in_dist" in text
    assert "--form" in text and "onefile" in text and "onedir" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_packaging_layout.py -v`
Expected: 6 FAIL(文件不存在)

- [ ] **Step 3: 实现**

```python
# tools/packaging/entry_gui.py
"""GUI 发行入口(薄启动脚本,spec §4.1.2 评审 P1-1/P1-2)。

PyInstaller 只消费脚本入口——包内模块的函数入口在入口脚本语境下相对导入
必然失败,故以绝对导入转发;console=False 形态下 ``sys.stdout/stderr`` 为
``None``,先兜底为 devnull 句柄再 import 主程序(cli 的 isatty 探测与
uvicorn 日志配置都假定流非 None)。
"""

import os
import sys

for _name in ("stdout", "stderr"):
    if getattr(sys, _name, None) is None:
        # 打包形态生命周期与进程同寿,不经 with 关闭(生命周期例外)
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115

from migration.gui.app import main  # noqa: E402

raise SystemExit(main())
```

```python
# tools/packaging/entry_cli.py
"""CLI 发行入口(薄启动脚本,spec §4.1.2 评审 P1-1):绝对导入转发。

控制台形态(console=True)无 stdio 兜底需求——真实控制台必然有流。
"""

from migration.cli import main

raise SystemExit(main())
```

```python
# -*- mode: python ; coding: utf-8 -*-
# tools/packaging/gui-onefile.spec —— GUI onefile 单文件形态(spec §4.1.1)
# 运行语境:build.py 已把本 spec 与入口拷入 .build-work/onefile/ 根,cwd=该目录
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ["entry_gui.py"],
    pathex=["."],
    binaries=[],
    datas=collect_data_files("migration"),  # data/*.yaml|sha256|txt + gui/*.html
    hiddenimports=["webview.platforms.winforms"],  # 包名是 webview(评审 P2-8)
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="mcmig-gui",           # 版本后缀由 build.py 落产物时补
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,              # 小白双击不弹黑窗(spec §4.1.2)
)
```

```python
# -*- mode: python ; coding: utf-8 -*-
# tools/packaging/app-onedir.spec —— onedir 全家桶(spec §4.1.1):CLI+GUI 双 EXE
# 双 EXE 各自一条 Analysis(每个 EXE 只携带自己的入口,评审 P1-1)
from PyInstaller.utils.hooks import collect_data_files

_datas = collect_data_files("migration")

a_cli = Analysis(
    ["entry_cli.py"],
    pathex=["."],
    binaries=[],
    datas=_datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
a_gui = Analysis(
    ["entry_gui.py"],
    pathex=["."],
    binaries=[],
    datas=_datas,
    hiddenimports=["webview.platforms.winforms"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz_cli = PYZ(a_cli.pure)
pyz_gui = PYZ(a_gui.pure)

exe_cli = EXE(pyz_cli, a_cli.scripts, [], exclude_binaries=True, name="mcmig",
              debug=False, strip=False, upx=False, console=True)
exe_gui = EXE(pyz_gui, a_gui.scripts, [], exclude_binaries=True, name="mcmig-gui",
              debug=False, strip=False, upx=False, console=False)

coll = COLLECT(exe_cli, exe_gui, a_cli.binaries, a_cli.datas, a_gui.binaries, a_gui.datas,
               strip=False, upx=False, name="mcmig")
```

```python
# tools/packaging/build.py
"""构建编排(spec §4.1.2/§4.1.4):独立工作副本改写 _form → PyInstaller → 后处理。

用法(须在 .venv-build 内运行——该 venv 只按锁定文件装依赖,刻意**不做**
``pip install -e .``:可编辑安装的查找器会抢在工作副本之前解析 ``migration``,
使产物固化错误的 FORM 标记):

    python tools/packaging/build.py --form onefile [--out dist-release]
    python tools/packaging/build.py --form onedir  [--out dist-release]
    python tools/packaging/build.py --sums dist-release

改写纪律(spec §4.1.4 v3):构建在 ``.build-work/<form>/`` 工作副本上进行
(源码树零污染,两形态独立缓存互不残留);产物按资产命名契约落 ``--out``。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
_ROOT = _TOOLS.parents[1]
_WORK_ROOT = _ROOT / ".build-work"


def asset_names(form: str, version: str) -> tuple[str, str]:
    """资产命名契约(spec §4.1.1,更新契约一处锁定):返回 (onefile 名, onedir 名)。"""
    return (f"mcmig-gui-{version}-win-x64.exe", f"mcmig-{version}-win-x64.zip")


def _read_version(work: Path) -> str:
    """从工作副本解析 ``__version__``(产物文件名的版本来源)。"""
    text = (work / "migration" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if m is None:
        raise SystemExit("未能在 migration/__init__.py 解析 __version__")
    return m.group(1)


def _rewrite_form(work: Path, form: str) -> None:
    """把工作副本内 ``_form.FORM`` 改写为目标形态(构建前调用;产物内固化)。"""
    p = work / "migration" / "_form.py"
    new = re.sub(r'FORM: str = "[^"]*"', f'FORM: str = "{form}"',
                 p.read_text(encoding="utf-8"))
    if f'FORM: str = "{form}"' not in new:
        raise SystemExit("_form.py 改写失败:源文件内容与预期结构不符")
    p.write_text(new, encoding="utf-8", newline="\n")


def _verify_form_in_dist(app_dir: Path, form: str) -> None:
    """onedir 后验:产物内 ``_form.py`` 必须等于本形态(防解析到源码树副本)。"""
    text = (app_dir / "_internal" / "migration" / "_form.py").read_text(encoding="utf-8")
    if f'FORM: str = "{form}"' not in text:
        raise SystemExit(f"产物内 FORM 标记与形态不符(期望 {form})——疑似解析到了源码树/可编辑副本")


def make_filelist(dist_app: Path) -> None:
    """生成受管清单 filelist.txt(spec §4.1.3):zip 展开后程序文件树(相对 mcmig/ 根)。"""
    lines = sorted(
        p.relative_to(dist_app).as_posix()
        for p in dist_app.rglob("*")
        if p.is_file() and p.name != "filelist.txt"
    )
    (dist_app / "filelist.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def make_sums(out_dir: Path) -> Path:
    """生成 SHA256SUMS.txt(UTF-8 无 BOM,``<sha256hex>  <文件名>`` 两空格,certutil 可比对)。"""
    lines = []
    for p in sorted(out_dir.iterdir()):
        if p.is_file() and p.name != "SHA256SUMS.txt":
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}")
    sums = out_dir / "SHA256SUMS.txt"
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return sums


def build(form: str, out: Path) -> list[Path]:
    """单形态构建:工作副本 → 改写 FORM → PyInstaller → 命名/打包;返回产物路径。"""
    spec_name = "gui-onefile.spec" if form == "onefile" else "app-onedir.spec"
    work = _WORK_ROOT / form
    if work.exists():
        shutil.rmtree(work)  # 两形态独立缓存,且杜绝上次残留(改写纪律)
    work.mkdir(parents=True)
    shutil.copytree(_ROOT / "migration", work / "migration")
    for name in ("entry_gui.py", "entry_cli.py", spec_name):
        shutil.copy2(_TOOLS / name, work / name)
    _rewrite_form(work, form)
    version = _read_version(work)
    dist, build_dir = work / "dist", work / "build"
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(dist), "--workpath", str(build_dir), spec_name],
        cwd=work, check=True,
    )
    out.mkdir(parents=True, exist_ok=True)
    if form == "onefile":
        one, _ = asset_names("onefile", version)
        target = out / one
        shutil.copy2(dist / "mcmig-gui.exe", target)
        return [target]
    app_dir = dist / "mcmig"
    _verify_form_in_dist(app_dir, form)
    make_filelist(app_dir)
    _, zipped = asset_names("onedir", version)
    target = out / zipped
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(app_dir.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(app_dir.parent).as_posix())  # zip 内含 mcmig/ 顶层
    return [target]


def main() -> int:
    """CLI 入口:--form 构建单形态;--sums 对目录生成 SHA256SUMS.txt。"""
    parser = argparse.ArgumentParser(description="mcmigrator 发行构建编排(spec W4 T11)")
    parser.add_argument("--form", choices=("onefile", "onedir"), help="构建形态")
    parser.add_argument("--out", type=Path, default=_ROOT / "dist-release", help="产物输出目录")
    parser.add_argument("--sums", type=Path, metavar="DIR", help="对目录生成 SHA256SUMS.txt 后退出")
    args = parser.parse_args()
    if args.sums is not None:
        print(make_sums(args.sums))
        return 0
    if args.form is None:
        parser.error("须提供 --form 或 --sums")
    for artifact in build(args.form, args.out):
        print(f"[产物] {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过(含本地真构建冒烟)**

```bash
.venv/Scripts/python.exe -m pytest tests/test_packaging_layout.py -v
.venv/Scripts/python.exe -m pytest
.venv-build/Scripts/python.exe tools/packaging/build.py --form onefile
.venv-build/Scripts/python.exe tools/packaging/build.py --form onedir
.venv-build/Scripts/python.exe tools/packaging/build.py --sums dist-release
```

Expected: 测试全绿;三产物落 `dist-release/`;`git status` 无源码树污染(`migration/_form.py` 仍为 `source`)。
冒烟(手测,记入 `tests/gui-manual-checklist.md` K 组):

```bash
dist-release/mcmig-<ver>-win-x64.zip 解压后: mcmig/mcmig.exe doctor   # 数据清单校验通过
                                                            # mcmig.exe --help
onefile: mcmig-gui-<ver>-win-x64.exe 双击(窗口/降级/关窗三路径,见 K1-K3)
```

> 注:exe 真跑属 J6 同源的真实 WebView2 路径——**用户放行后**再执行窗口冒烟;`doctor`/`--help` 两条自动化不涉窗口,可直接跑。

- [ ] **Step 5: 提交**

```bash
git add tools/packaging/entry_gui.py tools/packaging/entry_cli.py tools/packaging/gui-onefile.spec tools/packaging/app-onedir.spec tools/packaging/build.py tests/test_packaging_layout.py tests/gui-manual-checklist.md
git commit -m "build(batch-i-w4): 打包入库——薄入口/双 spec/构建编排(资产命名契约+filelist+FORM 后验)(T11)"
```

---

### Task 4: build.yml——CI 三 job

**Files:**
- Create: `.github/workflows/build.yml`
- Test: `tests/test_ci_workflows.py`(新建)

**Interfaces:**
- Consumes: Task 2 锁定文件、Task 3 build.py。
- Produces: push/PR/dispatch 触发的 CI(测试 win+linux;构建 win 上传 artifact)——Task 5 release.yml 复用同一安装口径。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ci_workflows.py(新建)
"""CI workflow 结构测试:安装口径分段/三 job/J7 退役锚(spec §4.2)。"""

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]


def test_build_yaml_three_jobs_and_lockfile_build():
    """build.yml:win 测试/linux 测试(flock 真跑)/win 锁定构建;构建段只用锁定文件。"""
    doc = yaml.safe_load((_ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8"))
    jobs = doc["jobs"]
    assert set(jobs) == {"test-windows", "test-linux", "build-windows"}
    # 触发面:push/PR/手动(spec §4.2.1)
    on = doc.get("on") or doc.get(True)  # yaml 把 on 解析为 True 的兼容取法
    assert {"push", "pull_request", "workflow_dispatch"} <= set(on)
    # 测试 job:可编辑安装(dev,gui)——ruff+pytest
    test_steps = "\n".join(jobs["test-windows"]["steps"][i].get("run", "") for i in range(len(jobs["test-windows"]["steps"])))
    assert 'pip install -e ".[dev,gui]"' in test_steps
    assert "ruff check migration/ tests/" in test_steps
    assert "pytest tests/" in test_steps
    # 构建_job:只装锁定文件(发行产物不在宽松解析环境产生,v3 统一口径),且须 needs 测试
    build_steps = "\n".join(jobs["build-windows"]["steps"][i].get("run", "") for i in range(len(jobs["build-windows"]["steps"])))
    assert "pip install -r tools/packaging/requirements-win-build.txt" in build_steps
    assert "-e " not in build_steps
    assert jobs["build-windows"]["needs"] == ["test-windows"]
    assert "--form onefile" in build_steps and "--form onedir" in build_steps
    # linux job 也跑 ruff+pytest(J7:POSIX flock 用例真跑)
    linux_steps = "\n".join(jobs["test-linux"]["steps"][i].get("run", "") for i in range(len(jobs["test-linux"]["steps"])))
    assert "ruff check migration/ tests/" in linux_steps and "pytest tests/" in linux_steps
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ci_workflows.py -v`
Expected: FAIL(文件不存在)

- [ ] **Step 3: 实现**

```yaml
# .github/workflows/build.yml —— push/PR/手动:测试(双平台)+锁定构建(spec §4.2.1)
name: build

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

jobs:
  # 测试 job:可编辑安装(源码宽松口径);ruff+pytest 全检
  test-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"   # 与主仓一致
      - run: python -m pip install -e ".[dev,gui]"
      - run: python -m ruff check migration/ tests/
      - run: python -m pytest tests/   # runner 预装 node,页面行为测试真跑

  # Linux 附加 job(决策 D2):顺带真跑 POSIX flock 的 2 个 skip 用例,J7 退役
  test-linux:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: python -m pip install -e ".[dev]"
      - run: python -m ruff check migration/ tests/
      - run: python -m pytest tests/

  # 构建 job:只装锁定文件(发行产物不在宽松解析环境产生;且不做可编辑安装,
  # 防其查找器遮蔽 .build-work 工作副本的 _form 标记,见 build.py docstring)
  build-windows:
    runs-on: windows-latest
    needs: [test-windows]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: python -m pip install -r tools/packaging/requirements-win-build.txt
      - run: python tools/packaging/build.py --form onefile
      - run: python tools/packaging/build.py --form onedir
      - uses: actions/upload-artifact@v4
        with:
          name: mcmig-win-x64
          path: dist-release/
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ci_workflows.py -v && .venv/Scripts/python.exe -m pytest`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/build.yml tests/test_ci_workflows.py
git commit -m "ci(batch-i-w4): build.yml 三 job(测试 win/linux+锁定构建)(T12)"
```

---

### Task 5: release.yml + 发布守卫 + SUMS

**Files:**
- Create: `.github/workflows/release.yml`、`tools/packaging/check_release.py`
- Test: `tests/test_ci_workflows.py`(追加)、`tests/test_packaging_layout.py`(追加)

**Interfaces:**
- Consumes: Task 3 `build.py --sums`、Task 4 安装口径。
- Produces: `check_release.check(tag: str, root: Path) -> list[str]`(空=通过;Task 14 的干跑验收复用);tag `v*` → Release(三资产)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_packaging_layout.py 追加
def test_release_guard_three_way_consistency(tmp_path):
    """发布守卫(spec §5/D8):tag==pyproject==__version__,任一不一致即拒。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("mcmig_check_release", _PKG / "check_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.13.0"\n', encoding="utf-8")
    (tmp_path / "migration").mkdir()
    (tmp_path / "migration" / "__init__.py").write_text('__version__ = "0.13.0"\n', encoding="utf-8")
    assert mod.check("v0.13.0", tmp_path) == []
    assert mod.check("v0.13.1", tmp_path) == [
        "tag(0.13.1) 与 pyproject.version(0.13.0) 不一致",
        "tag(0.13.1) 与 migration.__version__(0.13.0) 不一致",
    ]
    (tmp_path / "migration" / "__init__.py").write_text('__version__ = "0.13.1"\n', encoding="utf-8")
    # pyproject 与 tag 同版、__version__ 不同:仅一条错误
    assert mod.check("v0.13.0", tmp_path) == ["tag(0.13.0) 与 migration.__version__(0.13.1) 不一致"]
```

```python
# tests/test_ci_workflows.py 追加
def test_release_yaml_guard_and_sums():
    """release.yml:守卫先行+锁定构建+SUMS+gh release(三资产)。"""
    doc = yaml.safe_load((_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    jobs = doc["jobs"]
    assert {"check", "release"} <= set(jobs)
    assert jobs["release"]["needs"] == ["check"]
    assert jobs["release"]["permissions"] == {"contents": "write"}
    steps = "\n".join(s.get("run", "") for s in jobs["release"]["steps"])
    assert "check_release.py" in steps
    assert "pip install -r tools/packaging/requirements-win-build.txt" in steps
    assert "--sums" in steps
    check_steps = "\n".join(s.get("run", "") for s in jobs["check"]["steps"])
    assert "ruff check migration/ tests/" in check_steps and "pytest tests/" in check_steps
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ci_workflows.py tests/test_packaging_layout.py -v`
Expected: 新增 2 FAIL

- [ ] **Step 3: 实现**

```python
# tools/packaging/check_release.py
"""发布守卫(spec §5/决策 D8):tag==pyproject.version==migration.__version__ 三处一致才放行。

防「内置版本与资产名/更新比较基准漂移」——换版本必须改源码两处,不许只换 tag。
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _pyproject_version(root: Path) -> str:
    """读 pyproject 的 project.version。"""
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _dunder_version(root: Path) -> str:
    """读 migration/__init__.py 的 __version__(正则解析,不 import 以免依赖环境)。"""
    m = re.search(r'__version__\s*=\s*"([^"]+)"',
                  (root / "migration" / "__init__.py").read_text(encoding="utf-8"))
    if m is None:
        raise SystemExit("migration/__init__.py 未找到 __version__")
    return m.group(1)


def check(tag: str, root: Path = _ROOT) -> list[str]:
    """校验三处版本一致;返回中文错误列表(空列表=通过)。"""
    t = tag[1:] if tag.startswith("v") else tag
    pv, mv = _pyproject_version(root), _dunder_version(root)
    errors: list[str] = []
    if t != pv:
        errors.append(f"tag({t}) 与 pyproject.version({pv}) 不一致")
    if t != mv:
        errors.append(f"tag({t}) 与 migration.__version__({mv}) 不一致")
    return errors


def main() -> int:
    """CLI:``check_release.py <tag>``;非零退出=守卫拒绝。"""
    if len(sys.argv) != 2:
        print("用法: check_release.py <tag>", file=sys.stderr)
        return 2
    errors = check(sys.argv[1])
    for e in errors:
        print(f"[发布守卫] {e}——换版本必须同步改源码两处后重打 tag", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

```yaml
# .github/workflows/release.yml —— tag v*:全检+锁定构建+SUMS+自动 Release(spec §4.2.2/§5)
name: release

on:
  push:
    tags: ["v*"]

jobs:
  # 全检 job(可编辑安装;与 build.yml 同口径)
  check:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: python -m pip install -e ".[dev,gui]"
      - run: python -m ruff check migration/ tests/
      - run: python -m pytest tests/

  # 发布 job:守卫先行;只装锁定文件(不做可编辑安装,防 _form 遮蔽,见 build.py)
  release:
    runs-on: windows-latest
    needs: [check]
    permissions:
      contents: write   # gh release 建页所需
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: python tools/packaging/check_release.py ${{ github.ref_name }}
      - run: python -m pip install -r tools/packaging/requirements-win-build.txt
      - run: python tools/packaging/build.py --form onefile
      - run: python tools/packaging/build.py --form onedir
      - run: python tools/packaging/build.py --sums dist-release
      - uses: softprops/action-gh-release@v2
        with:
          files: dist-release/*   # exe+zip+SHA256SUMS.txt 三资产
          generate_release_notes: true
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ci_workflows.py tests/test_packaging_layout.py -v && .venv/Scripts/python.exe -m pytest`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/release.yml tools/packaging/check_release.py tests/test_ci_workflows.py tests/test_packaging_layout.py
git commit -m "ci(batch-i-w4): release.yml+发布守卫三处版本一致+SHA256SUMS(T12)"
```

---

### Task 6: updater 纯函数层——检查/选资产/比版/SUMS

**Files:**
- Create: `migration/updater.py`
- Test: `tests/test_updater.py`(新建)

**Interfaces:**
- Consumes: Task 1 `migration/_form.py` 的 `FORM`。
- Produces(Task 7/8/9/10 消费,签名固定):

```python
class UpdateError(Exception):  # __init__(what: str, why: str);属性 .what/.why(中文三段式)
class AssetInfo:               # dataclass(frozen=True): name: str; size: int; url: str
class ReleaseInfo:             # dataclass(frozen=True): tag: str; assets: tuple[AssetInfo, ...]
def _open_client() -> httpx.Client          # 测试注入点:monkeypatch 为 MockTransport 客户端
def local_version() -> str                  # 动态读 migration.__version__(函数内 import,monkeypatch 可生效)
def fetch_latest_release() -> ReleaseInfo | None
def pick_asset(assets: Sequence[AssetInfo] -> AssetInfo | None)   # 仅按 _form.FORM 匹配命名契约
def is_newer(remote_tag: str, local_ver: str) -> bool
def fetch_sums(url: str) -> dict[str, str]   # {资产名: sha256hex};四失败形态一律 UpdateError
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_updater.py(新建)
"""updater 纯函数层测试(spec §4.3.1):mock httpx 经 _open_client 注入。"""

from __future__ import annotations

import httpx
import pytest

from migration import _form, updater


def _install_transport(monkeypatch, handler) -> None:
    """把 _open_client 替换为 MockTransport 客户端(跟随重定向与生产一致)。"""
    monkeypatch.setattr(
        updater, "_open_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                             timeout=updater._TIMEOUT),
    )


def _release_json(tag: str = "v0.13.1") -> dict:
    return {
        "tag_name": tag,
        "assets": [
            {"name": "mcmig-gui-0.13.1-win-x64.exe", "size": 31, "browser_download_url": "https://dl/e.exe"},
            {"name": "mcmig-0.13.1-win-x64.zip", "size": 32, "browser_download_url": "https://dl/m.zip"},
            {"name": "SHA256SUMS.txt", "size": 3, "browser_download_url": "https://dl/sums.txt"},
        ],
    }


def test_fetch_latest_parses_assets(monkeypatch):
    _install_transport(monkeypatch, lambda req: httpx.Response(200, json=_release_json()))
    rel = updater.fetch_latest_release()
    assert rel is not None and rel.tag == "v0.13.1"
    assert [a.name for a in rel.assets] == [
        "mcmig-gui-0.13.1-win-x64.exe", "mcmig-0.13.1-win-x64.zip", "SHA256SUMS.txt",
    ]


def test_fetch_latest_404_returns_none(monkeypatch):
    _install_transport(monkeypatch, lambda req: httpx.Response(404))
    assert updater.fetch_latest_release() is None


def test_fetch_latest_403_rate_limit_message(monkeypatch):
    _install_transport(monkeypatch, lambda req: httpx.Response(403))
    with pytest.raises(updater.UpdateError) as ei:
        updater.fetch_latest_release()
    assert "限额" in ei.value.why


def test_fetch_latest_network_error_is_updateerror(monkeypatch):
    def _handler(req):
        raise httpx.ConnectError("断网")
    _install_transport(monkeypatch, _handler)
    with pytest.raises(updater.UpdateError):
        updater.fetch_latest_release()


def test_fetch_latest_follows_redirect(monkeypatch):
    """302→200 链必须跟随(httpx 默认不跟随;browser_download_url 实为 302)。"""
    def handler(req):
        if "latest" in str(req.url):
            return httpx.Response(302, headers={"Location": "https://api/real"})
        return httpx.Response(200, json=_release_json())
    _install_transport(monkeypatch, handler)
    assert updater.fetch_latest_release() is not None


def test_pick_asset_reads_form_only(monkeypatch):
    """资产形态唯一事实来源=_form.FORM(D7);source 不选任何资产。"""
    assets = [
        updater.AssetInfo("mcmig-gui-0.13.1-win-x64.exe", 31, "u1"),
        updater.AssetInfo("mcmig-0.13.1-win-x64.zip", 32, "u2"),
    ]
    monkeypatch.setattr(_form, "FORM", "onefile")
    assert updater.pick_asset(assets).name == "mcmig-gui-0.13.1-win-x64.exe"
    monkeypatch.setattr(_form, "FORM", "onedir")
    assert updater.pick_asset(assets).name == "mcmig-0.13.1-win-x64.zip"
    monkeypatch.setattr(_form, "FORM", "source")
    assert updater.pick_asset(assets) is None


@pytest.mark.parametrize(
    ("remote", "local", "expect"),
    [("v0.13.1", "0.13.0", True), ("0.13.0", "0.13.0", False), ("0.9", "0.10", False),
     ("1.0.0", "0.13.1", True), ("0.14.0rc1", "0.13.9", True)],
)
def test_is_newer_boundaries(remote, local, expect):
    assert updater.is_newer(remote, local) is expect


def test_fetch_sums_parses_with_tolerance(monkeypatch):
    body = (
        "abc123  mcmig-0.13.1-win-x64.zip\n"
        "\n"
        "def456  mcmig-gui-0.13.1-win-x64.exe\n"
        "# 注释行忽略\n"
        "012abc  中文名资产.zip\n"
    )
    _install_transport(monkeypatch, lambda req: httpx.Response(200, text=body))
    sums = updater.fetch_sums("https://dl/sums.txt")
    assert sums["中文名资产.zip"] == "012abc"


@pytest.mark.parametrize(
    ("body", "why_fragment"),
    [("", "清单"), ("zz nothex  a.zip\n", "十六进制"), ("aa… bb.zip\naa… bb.zip\n".replace("…", "11"), "重复")],
)
def test_fetch_sums_rejects_bad_forms(monkeypatch, body, why_fragment):
    _install_transport(monkeypatch, lambda req: httpx.Response(200, text=body))
    with pytest.raises(updater.UpdateError) as ei:
        updater.fetch_sums("https://dl/sums.txt")
    assert why_fragment in ei.value.why


def test_fetch_sums_404_rejected(monkeypatch):
    _install_transport(monkeypatch, lambda req: httpx.Response(404))
    with pytest.raises(updater.UpdateError):
        updater.fetch_sums("https://dl/sums.txt")
```

> 注:`test_pick_asset_reads_form_only` 直接以 AssetInfo 列表断言,无需网络。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_updater.py -v`
Expected: 全部 FAIL/ERROR(`migration.updater` 不存在)

- [ ] **Step 3: 实现**

```python
# migration/updater.py
"""更新基础档(spec §4.3):检查/选资产/比版/SUMS 校验/下载暂存——纯库,CLI/GUI 平级消费。

行为红线(spec §4.3.2):本模块**不自动运行暂存文件**、不提供任何「运行」
入口;产物只到「暂存+返回路径」,由前端给「打开位置+替换说明」。
形态选择唯一事实来源=``migration._form.FORM``(构建时固化,决策 D7)。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

from . import _form

_REPO = "Nothing-ray/mcmigrator"  # 仓标识(非版本号,不违「不硬编码版本」)
_RELEASES_LATEST = f"https://api.github.com/repos/{_REPO}/releases/latest"
_ACCEPT = "application/vnd.github+json"
_TIMEOUT = 10.0
_DOWNLOAD_CHUNK = 256 * 1024


class UpdateError(Exception):
    """更新失败(中文三段式载体:``what`` 短标题/``why`` 原因+建议)。"""

    def __init__(self, what: str, why: str) -> None:
        super().__init__(f"{what}:{why}")
        self.what = what
        self.why = why


@dataclass(frozen=True)
class AssetInfo:
    """Release 资产(名称/字节数/浏览器下载地址)。"""

    name: str
    size: int
    url: str


@dataclass(frozen=True)
class ReleaseInfo:
    """latest Release 摘要(tag+资产列表)。"""

    tag: str
    assets: tuple[AssetInfo, ...]


def _open_client() -> httpx.Client:
    """构造生产 httpx 客户端(跟随重定向——GitHub 资产实为 302;测试注入点)。"""
    return httpx.Client(follow_redirects=True, timeout=_TIMEOUT,
                        headers={"Accept": _ACCEPT})


def local_version() -> str:
    """动态读当前版本(函数内 import,每次解析模块属性——测试可 monkeypatch)。"""
    from . import __version__

    return __version__


def fetch_latest_release() -> ReleaseInfo | None:
    """取 latest Release;404(尚无发布)→ None;网络/限额/5xx → UpdateError。"""
    try:
        with _open_client() as client:
            resp = client.get(_RELEASES_LATEST)
    except httpx.HTTPError as e:
        raise UpdateError("更新检查失败", f"无法连接 GitHub({e.__class__.__name__})。"
                                         "请检查网络后重试,或到项目发布页手动查看。") from e
    if resp.status_code == 404:
        return None
    if resp.status_code == 403:
        raise UpdateError("更新检查被限流",
                          "GitHub 匿名访问限额(60 次/小时·每来源 IP)已用尽,请稍后再试。")
    if resp.status_code != 200:
        raise UpdateError("更新检查失败", f"GitHub 返回状态码 {resp.status_code},请稍后重试。")
    data = resp.json()
    assets = tuple(
        AssetInfo(str(a["name"]), int(a.get("size", 0)), str(a["browser_download_url"]))
        for a in data.get("assets", [])
    )
    return ReleaseInfo(tag=str(data["tag_name"]), assets=assets)


def pick_asset(assets: Sequence[AssetInfo]) -> AssetInfo | None:
    """按资产命名契约匹配本形态资产;source 或缺失 → None(调用方按「该形态未发布」呈现)。"""
    if _form.FORM == "onefile":
        matches = [a for a in assets if a.name.startswith("mcmig-gui-") and a.name.endswith("-win-x64.exe")]
    elif _form.FORM == "onedir":
        matches = [a for a in assets
                   if a.name.startswith("mcmig-") and a.name.endswith("-win-x64.zip")
                   and not a.name.startswith("mcmig-gui-")]
    else:
        return None
    return matches[0] if matches else None


def _version_tuple(ver: str) -> tuple[int, ...]:
    """版本串→数值段元组(去 v 前缀;每段取前导数字,rc 等后缀忽略但有序)。"""
    v = ver.lstrip("vV")
    segs = []
    for seg in v.split("."):
        m = re.match(r"(\d+)", seg)
        segs.append(int(m.group(1)) if m else 0)
    return tuple(segs)


def is_newer(remote_tag: str, local_ver: str) -> bool:
    """数值比较远端 tag 是否高于本地版本(0.9 < 0.10 < 1.0;后缀忽略记录于决策)。"""
    return _version_tuple(remote_tag) > _version_tuple(local_ver)


_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


def fetch_sums(url: str) -> dict[str, str]:
    """下载并解析 SHA256SUMS.txt → {资产名: sha256hex小写}。

    四种失败形态一律 UpdateError 拒绝(spec §4.3.1):请求失败/清单空、
    非法十六进制、同名重复条目(条目缺失由调用方查 dict 时判定)。
    """
    try:
        with _open_client() as client:
            resp = client.get(url)
    except httpx.HTTPError as e:
        raise UpdateError("校验清单下载失败", f"SHA256SUMS.txt 下载失败({e.__class__.__name__}),请重试。") from e
    if resp.status_code != 200:
        raise UpdateError("校验清单下载失败", f"SHA256SUMS.txt 返回 {resp.status_code};发布不完整,请稍后再试。")
    sums: dict[str, str] = {}
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise UpdateError("校验清单格式异常", "SHA256SUMS.txt 存在无法解析的行,已拒绝本次更新。")
        digest, name = parts[0].lower(), parts[1].strip()
        if not _HEX.match(digest):
            raise UpdateError("校验清单格式异常", f"资产 {name} 的 SHA256 非合法十六进制,已拒绝本次更新。")
        if name in sums:
            raise UpdateError("校验清单格式异常", f"资产 {name} 在清单中重复出现,已拒绝本次更新。")
        sums[name] = digest
    if not sums:
        raise UpdateError("校验清单为空", "SHA256SUMS.txt 不含任何条目,已拒绝本次更新。")
    return sums
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_updater.py -v && .venv/Scripts/python.exe -m pytest`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add migration/updater.py tests/test_updater.py
git commit -m "feat(batch-i-w4): updater 纯函数层——检查/选资产(_form)/比版/SUMS 校验(T13)"
```

---

### Task 7: updater 下载暂存——uuid 独占终路径/取消/staging 解析/编排

**Files:**
- Modify: `migration/updater.py`(追加)
- Test: `tests/test_updater.py`(追加)

**Interfaces:**
- Consumes: Task 6 全部符号。
- Produces(Task 8/9 消费):

```python
class UpdateCancelled(Exception)                       # 取消专用(已清理本次子目录后抛出)
def download_and_verify(asset: AssetInfo, sha256: str, staging_dir: Path,
                        progress_cb: Callable[[int, int], None] | None = None,
                        should_cancel: Callable[[], bool] | None = None) -> Path
    # 终路径=staging_dir/<uuid4>/<资产名>(独占,永不落共享路径);失败/取消清理本 uuid 子目录
def staging_root_at(base: Path) -> Path                # <base>/data/update-staging(不回退)
def resolve_staging() -> Path                          # CLI:冻结 exe 目录→%TEMP% 回退→UpdateError
def plan_update(staging_root: Path, *, progress_cb=None, should_cancel=None) -> UpdatePlan | None
class UpdatePlan:  # dataclass(frozen=True): version: str; asset_name: str; path: Path; size: int; sha256: str
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_updater.py 追加
import hashlib


def _stream_handler(content: bytes):
    def handler(req):
        return httpx.Response(200, content=content,
                              headers={"Content-Length": str(len(content))})
    return handler


def test_download_and_verify_success_exclusive_path(monkeypatch, tmp_path):
    """成功产物落 <uuid>/<资产名>(独占终路径,v3),.part 已消失。"""
    payload = b"x" * 1000
    _install_transport(monkeypatch, _stream_handler(payload))
    asset = updater.AssetInfo("mcmig-0.13.1-win-x64.zip", len(payload), "https://dl/m.zip")
    path = updater.download_and_verify(asset, hashlib.sha256(payload).hexdigest(), tmp_path)
    assert path.parent.parent == tmp_path and path.name == asset.name
    assert not list(path.parent.glob("*.part"))
    assert path.read_bytes() == payload


def test_download_bad_sha_cleans_own_subdir(monkeypatch, tmp_path):
    _install_transport(monkeypatch, _stream_handler(b"payload"))
    asset = updater.AssetInfo("a.zip", 7, "https://dl/a.zip")
    with pytest.raises(updater.UpdateError) as ei:
        updater.download_and_verify(asset, "0" * 64, tmp_path)
    assert "校验" in ei.value.what
    assert list(tmp_path.iterdir()) == []  # 本任务子目录已清理


def test_download_cancel_cleans_and_raises_cancelled(monkeypatch, tmp_path):
    _install_transport(monkeypatch, _stream_handler(b"payload"))
    asset = updater.AssetInfo("a.zip", 7, "https://dl/a.zip")
    with pytest.raises(updater.UpdateCancelled):
        updater.download_and_verify(asset, "0" * 64, tmp_path, should_cancel=lambda: True)
    assert list(tmp_path.iterdir()) == []


def test_download_progress_callback_reports_bytes(monkeypatch, tmp_path):
    payload = b"y" * 5000
    _install_transport(monkeypatch, _stream_handler(payload))
    seen: list[tuple[int, int]] = []
    asset = updater.AssetInfo("a.zip", len(payload), "https://dl/a.zip")
    updater.download_and_verify(asset, hashlib.sha256(payload).hexdigest(), tmp_path,
                                progress_cb=lambda r, t: seen.append((r, t)))
    assert seen and seen[-1] == (len(payload), len(payload))


def test_same_version_reupload_does_not_touch_earlier_path(monkeypatch, tmp_path):
    """Review Focus:v3——同版本资产重传时,后任务不得改写先任务已返回的独占路径。"""
    v1, v2 = b"version-one", b"version-two-reuploaded"
    state = {"content": v1}
    sums = {"content": hashlib.sha256(v1).hexdigest()}
    def handler(req):
        if "sums" in str(req.url):
            line = f"{sums['content']}  a.zip\n"
            return httpx.Response(200, text=line)
        return httpx.Response(200, content=state["content"])
    _install_transport(monkeypatch, handler)
    asset = updater.AssetInfo("a.zip", 0, "https://dl/a.zip")
    first = updater.download_and_verify(asset, sums["content"], tmp_path)
    # 资产被重传(不可变性未启用),SUMS 同步换内容
    state["content"] = v2
    sums["content"] = hashlib.sha256(v2).hexdigest()
    second = updater.download_and_verify(asset, sums["content"], tmp_path)
    assert second != first and second.parent != first.parent
    assert first.read_bytes() == v1  # 先任务路径内容不被后任务改写


def test_download_two_runs_use_distinct_uuid_dirs(monkeypatch, tmp_path):
    """跨进程/双任务并发:两次下载各占独立 uuid 子目录,互不可见。"""
    payload = b"z" * 100
    _install_transport(monkeypatch, _stream_handler(payload))
    asset = updater.AssetInfo("a.zip", len(payload), "https://dl/a.zip")
    digest = hashlib.sha256(payload).hexdigest()
    p1 = updater.download_and_verify(asset, digest, tmp_path)
    p2 = updater.download_and_verify(asset, digest, tmp_path)
    assert p1.parent != p2.parent


def test_resolve_staging_frozen_then_temp_fallback(monkeypatch, tmp_path):
    """冻结:exe 旁 data/update-staging;不可写→回退 %TEMP%\\mcmig-update。

    失败腿注入用 monkeypatch(Windows 对目录 chmod 不生效,只对文件切只读位)。
    """
    exe_dir = tmp_path / "app"; exe_dir.mkdir()
    (tmp_path / "app" / "mcmig.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "mcmig.exe"))
    root = updater.resolve_staging()
    assert root == exe_dir / "data" / "update-staging" and root.is_dir()
    # exe 目录不可写(staging_root_at 注入 OSError)→ 回退 %TEMP%\mcmig-update
    with monkeypatch.context() as m:
        m.setattr(updater, "staging_root_at",
                  lambda base: (_ for _ in ()).throw(OSError("read-only")))
        m.setenv("TEMP", str(tmp_path / "tmp"))
        root2 = updater.resolve_staging()
        assert root2 == tmp_path / "tmp" / "mcmig-update" and root2.is_dir()


def test_resolve_staging_both_fail_rejects(monkeypatch, tmp_path):
    """两腿皆失败 → UpdateError「请手动下载」(TEMP 父路径被同名文件占位→mkdir 必败)。"""
    exe_dir = tmp_path / "app"; exe_dir.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "mcmig.exe"))
    (tmp_path / "nope").write_bytes(b"")  # 同名文件占位:TEMP 子路径 mkdir 必失败
    monkeypatch.setattr(updater, "staging_root_at",
                        lambda base: (_ for _ in ()).throw(OSError("read-only")))
    monkeypatch.setenv("TEMP", str(tmp_path / "nope" / "deep"))
    with pytest.raises(updater.UpdateError) as ei:
        updater.resolve_staging()
    assert "手动下载" in ei.value.why


def test_plan_update_orchestrates(monkeypatch, tmp_path):
    """编排:latest→SUMS→下载→UpdatePlan 全字段;无新版→None。"""
    payload = b"firmware"
    digest = hashlib.sha256(payload).hexdigest()
    def handler(req):
        url = str(req.url)
        if url.endswith("/latest"):
            if handler.mode == "none":
                return httpx.Response(200, json=_release_json("v0.12.0"))
            rel = _release_json()
            rel["assets"] = [
                {"name": "mcmig-gui-0.13.1-win-x64.exe", "size": len(payload),
                 "browser_download_url": "https://dl/e.exe"},
                {"name": "SHA256SUMS.txt", "size": 1, "browser_download_url": "https://dl/sums.txt"},
            ]
            return httpx.Response(200, json=rel)
        if "sums" in url:
            return httpx.Response(200, text=f"{digest}  mcmig-gui-0.13.1-win-x64.exe\n")
        return httpx.Response(200, content=payload)
    handler.mode = "new"
    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(_form, "FORM", "onefile")
    import migration
    monkeypatch.setattr(migration, "__version__", "0.12.0")
    plan = updater.plan_update(tmp_path)
    assert plan is not None
    assert (plan.version, plan.asset_name, plan.sha256, plan.size) == (
        "0.13.1", "mcmig-gui-0.13.1-win-x64.exe", digest, len(payload))
    assert plan.path.read_bytes() == payload
    handler.mode = "none"
    assert updater.plan_update(tmp_path) is None  # 无新版
```

> 注:`import sys` 需在文件头补齐(frozen 探测用);比版经 `local_version()` 动态读,`monkeypatch.setattr(migration, "__version__", ...)` 即可生效。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_updater.py -v`
Expected: 新增 9 FAIL/ERROR(符号不存在)

- [ ] **Step 3: 实现(updater.py 追加)**

```python
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path


class UpdateCancelled(Exception):
    """用户取消(本次 uuid 子目录已清理后抛出;调用方按 cancelled 终态呈现)。"""


@dataclass(frozen=True)
class UpdatePlan:
    """一次成功下载的完整结果(done 载荷与 GET 同源,spec §4.3.4)。"""

    version: str
    asset_name: str
    path: Path
    size: int
    sha256: str


def staging_root_at(base: Path) -> Path:
    """暂存根(无回退版):``<base>/data/update-staging``——GUI 消费(不承诺 TEMP)。"""
    root = base / "data" / "update-staging"
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_staging() -> Path:
    """CLI 暂存根(spec §4.3.1 两端分治):冻结=<exe目录>/data/update-staging,
    不可写回退 %TEMP%\\mcmig-update\\,再失败 → UpdateError「请手动下载」。"""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    try:
        return staging_root_at(base)
    except OSError:
        pass
    tmp = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "mcmig-update"
    try:
        tmp.mkdir(parents=True, exist_ok=True)
        probe = tmp / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return tmp
    except OSError as e:
        raise UpdateError("暂存目录不可写",
                          "软件目录与系统临时目录都不可写,无法暂存更新;"
                          "请到项目发布页手动下载替换。") from e


def download_and_verify(asset: AssetInfo, sha256: str, staging_dir: Path,
                        progress_cb: Callable[[int, int], None] | None = None,
                        should_cancel: Callable[[], bool] | None = None) -> Path:
    """流式下载+SHA256 校验,终路径=``<staging_dir>/<uuid4>/<资产名>``(独占,v3)。

    成功产物也留在 uuid 子目录——GitHub 资产不可变须显式启用,同版本重传时
    共享路径会被后任务改写、破坏先任务已校验内容(spec §4.3.1 v3)。
    失败/取消只清理**本任务的 uuid 子目录**;成功产物保留至用户应用,不跨任务清理。
    """
    sub = staging_dir / str(uuid.uuid4())
    sub.mkdir(parents=True)
    part = sub / (asset.name + ".part")
    digest = hashlib.sha256()
    received = 0
    try:
        with _open_client() as client, client.stream("GET", asset.url) as resp:
            if resp.status_code != 200:
                raise UpdateError("下载失败", f"资产下载返回 {resp.status_code},请稍后重试。")
            total = int(resp.headers.get("content-length", 0))
            with part.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=_DOWNLOAD_CHUNK):
                    if should_cancel is not None and should_cancel():
                        raise UpdateCancelled()
                    f.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if progress_cb is not None:
                        progress_cb(received, total)
        if digest.hexdigest().lower() != sha256.lower():
            raise UpdateError("校验失败",
                              "下载内容 SHA256 与发布清单不一致,本次下载已丢弃;请重试,"
                              "持续失败请到项目发布页手动下载。")
        final = sub / asset.name
        part.replace(final)
        return final
    except UpdateCancelled:
        shutil.rmtree(sub, ignore_errors=True)
        raise
    except UpdateError:
        shutil.rmtree(sub, ignore_errors=True)
        raise
    except OSError as e:
        shutil.rmtree(sub, ignore_errors=True)
        raise UpdateError("下载失败", f"写盘失败({e.__class__.__name__}),请检查磁盘空间与目录权限。") from e


def plan_update(staging_root: Path, *,
                progress_cb: Callable[[int, int], None] | None = None,
                should_cancel: Callable[[], bool] | None = None) -> UpdatePlan | None:
    """编排:检查→比版→选资产→SUMS→下载校验;无新版/无发布 → None。

    有新版但本形态资产缺失 → UpdateError(「该形态未发布」);源码模式由调用方前置拦截。
    """
    rel = fetch_latest_release()
    if rel is None or not is_newer(rel.tag, local_version()):
        return None
    asset = pick_asset(rel.assets)
    if asset is None:
        raise UpdateError("该形态未发布",
                          "本次发布未包含当前安装形态的资产;请到项目发布页手动查看。")
    sums_url = next((a.url for a in rel.assets if a.name == "SHA256SUMS.txt"), None)
    if sums_url is None:
        raise UpdateError("校验清单缺失", "本次发布未附 SHA256SUMS.txt,已拒绝自动下载。")
    entry = fetch_sums(sums_url).get(asset.name)
    if entry is None:
        raise UpdateError("校验清单缺条目",
                          f"SHA256SUMS.txt 未包含 {asset.name},已拒绝自动下载。")
    path = download_and_verify(asset, entry, staging_root, progress_cb, should_cancel)
    return UpdatePlan(version=rel.tag.lstrip("vV"), asset_name=asset.name,
                      path=path, size=asset.size, sha256=entry)
```

(`import os/sys/tempfile/shutil/uuid` 并入文件头;`hashlib` 若 Task 6 未引入则此处补;比版一律经 `local_version()` 动态读,勿在模块顶绑死 `__version__` 值——monkeypatch 与版本升级都会绕开绑定。)

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_updater.py -v && .venv/Scripts/python.exe -m pytest`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add migration/updater.py tests/test_updater.py
git commit -m "feat(batch-i-w4): updater 下载暂存——uuid 独占终路径/流式校验/取消清理/staging 解析(T13)"
```

---

### Task 8: CLI `mcmig update [--check]`

**Files:**
- Modify: `migration/cli.py`(parser 约 117-128 追加子命令;dispatch 约 910 追加分支;新增 `_cmd_update`)
- Test: `tests/test_cli.py`(追加)

**Interfaces:**
- Consumes: Task 7 `plan_update/resolve_staging/fetch_latest_release/is_newer/UpdateError`、Task 1 `_form.FORM`。
- Produces: 子命令 `mcmig update [--check]`;退出码 0=无新版或成功,2=失败(spec §4.3.3)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cli.py 追加(沿用本文件既有的 monkeypatch/fake 模式与 cli main 调用约定)
def test_update_check_reports_newer(capsys, monkeypatch, tmp_path):
    """--check:发现新版打印版本并退 0(不下载)。"""
    ...  # monkeypatch updater.fetch_latest_release → ReleaseInfo(tag="v0.13.1", assets=())
    ...  # monkeypatch migration.__version__ → "0.12.0"
    rc = _run_cli(["update", "--check"])   # 复用本文件既有 CLI 调用 helper(名以文件实际为准)
    assert rc == 0 and "0.13.1" in capsys.readouterr().out


def test_update_check_latest(capsys, monkeypatch):
    ...  # fetch → tag 与本地同版
    rc = _run_cli(["update", "--check"])
    assert rc == 0 and "已是最新" in capsys.readouterr().out


def test_update_source_mode_download_gives_git_pull(capsys, monkeypatch):
    """源码模式:--check 可用;不带 --check 时给 git pull 指引而非下载(退 0)。"""
    from migration import _form
    monkeypatch.setattr(_form, "FORM", "source")
    ...  # fetch → v9.9.9
    rc = _run_cli(["update"])
    assert rc == 0 and "git pull" in capsys.readouterr().out


def test_update_frozen_download_flow(capsys, monkeypatch, tmp_path):
    """冻结 onefile 全流程:暂存落 exe/data/update-staging/<uuid>/,输出三步替换指引。"""
    from migration import _form, updater
    monkeypatch.setattr(_form, "FORM", "onefile")
    exe = tmp_path / "app" / "mcmig.exe"; exe.parent.mkdir(parents=True); exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    payload = b"new-binary"
    plan = updater.UpdatePlan("0.13.1", "mcmig-gui-0.13.1-win-x64.exe",
                              path=None, size=len(payload), sha256="ab" * 32)
    def _fake_plan(root, *, progress_cb=None, should_cancel=None):
        sub = root / "uuid-1"; sub.mkdir(parents=True)
        p = sub / plan.asset_name; p.write_bytes(payload)
        return updater.UpdatePlan("0.13.1", plan.asset_name, p, len(payload), "ab" * 32)
    monkeypatch.setattr(updater, "plan_update", _fake_plan)
    rc = _run_cli(["update"])
    out = capsys.readouterr().out
    assert rc == 0
    assert (exe.parent / "data" / "update-staging" / "uuid-1" / plan.asset_name).read_bytes() == payload
    assert "替换" in out and "重新启动" in out   # 三步指引要素


def test_update_failure_exit_2(capsys, monkeypatch):
    ...  # monkeypatch updater.fetch_latest_release → raise UpdateError("更新检查失败", "网络不可达")
    rc = _run_cli(["update", "--check"])
    assert rc == 2
```

> 注:`_run_cli` 与既有 fake ReleaseInfo 构造以 `tests/test_cli.py` 文件内既有 helper 名为准(若无统一 helper,直接 `from migration.cli import main; main(["update", "--check"])`);`import sys` 按需补。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py -k update -v`
Expected: 5 FAIL(未知子命令)

- [ ] **Step 3: 实现**

parser 追加(doctor 之前):

```python
    p_upd = sub.add_parser(
        "update", help="检查并下载新版本(下载校验后暂存,给手动替换指引)")
    p_upd.add_argument("--check", action="store_true",
                       help="仅检查是否有新版,不下载")
```

dispatch 追加:

```python
        if args.command == "update":
            return _cmd_update(args)
```

新函数:

```python
def _cmd_update(args: argparse.Namespace) -> int:
    """更新基础档入口(spec §4.3.3):--check 只查;下载→校验→暂存→两段式指引。

    源码模式:--check 可用,下载路径给 git pull 指引(防开发态误替换)。
    退出码:0=无新版或成功;2=失败。
    """
    from . import _form
    from . import updater

    local = updater.local_version()  # 动态读(勿模块顶绑死,测试 monkeypatch 依赖)
    if _form.FORM == "source":
        try:
            rel = updater.fetch_latest_release()
        except updater.UpdateError as e:
            _print(f"[错误] {e.what}:{e.why}")
            return 2
        if rel is None or not updater.is_newer(rel.tag, local):
            _print("[提示] 已是最新版本。")
            return 0
        _print(f"[提示] 发现新版 {rel.tag.lstrip('vV')}(当前 {local})。")
        if args.check:
            return 0
        _print("[提示] 源码运行模式不提供自动下载;请 git pull 更新到该版本后再试。")
        return 0
    if args.check:
        try:
            rel = updater.fetch_latest_release()
        except updater.UpdateError as e:
            _print(f"[错误] {e.what}:{e.why}")
            return 2
        if rel is None or not updater.is_newer(rel.tag, local):
            _print(f"[提示] 已是最新版本({local})。")
        else:
            _print(f"[提示] 发现新版 {rel.tag.lstrip('vV')}(当前 {local});运行 mcmig update 下载。")
        return 0
    try:
        staging = updater.resolve_staging()
        plan = updater.plan_update(staging)
    except updater.UpdateError as e:
        _print(f"[错误] {e.what}:{e.why}")
        return 2
    if plan is None:
        _print(f"[提示] 已是最新版本({local})。")
        return 0
    _print(f"[提示] 新版 {plan.version} 已下载并校验通过:")
    _print(f"  文件: {plan.path}")
    _print(f"  SHA256: {plan.sha256}")
    _print("[提示] 应用更新三步:①退出当前 mcmig;②把上述文件替换到 mcmig 所在目录(覆盖旧文件);"
           "③重新启动 mcmig。应用后暂存目录可整目录删除。")
    return 0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py -k update -v && .venv/Scripts/python.exe -m pytest`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add migration/cli.py tests/test_cli.py
git commit -m "feat(batch-i-w4): CLI mcmig update [--check]——源码模式指引+两段式替换说明(T13)"
```

---

### Task 9: GUI 更新端点与 update job 四件契约

**Files:**
- Modify: `migration/gui/server.py`(`Job.can_cancel` 约 266-274;`emit` 约 294-301;`snapshot` 约 386-417;端点区追加;`STRINGS` 引用)
- Modify: `migration/gui/STRINGS.py`(追加两组键)
- Test: `tests/test_gui_server.py`(追加)

**Interfaces:**
- Consumes: Task 7 `plan_update/staging_root_at/UpdateError/UpdateCancelled/UpdatePlan`、`_form.FORM`。
- Produces(Task 10 页面消费):

```python
POST /api/update/check  → 200 {"current": str, "mode": "source"|"frozen",
                                "newer": bool|None, "latest": str|None,
                                "asset_name": str|None, "size": int|None,
                                "error": {"what","why"} | None}
POST /api/update/download → 202 {"job_id": str}(kind="update",单锁 409;源码模式 400)
POST /api/update/open-location {"path": str} → 200 {"ok": True}(暂存根外 400)
# Job 契约:can_cancel 白名单含 "update";progress 事件 {"type":"progress","received","total"}
#           → GET snapshot 新键 progress_bytes={"received","total"}|None;done 载荷=UpdatePlan 全字段
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_gui_server.py 追加(既有 client/game_root 夹具模式)
def test_update_job_is_cancellable_and_progress_in_snapshot():
    """update job 四件之二:可取消;progress 事件进 GET 快照(progress_bytes)。"""
    from migration.gui.server import Job

    job = Job("t1", "update")
    assert job.can_cancel is True
    assert Job("t2", "plan").can_cancel is False
    job.emit({"type": "progress", "received": 10, "total": 100})
    snap = job.snapshot()
    assert snap["progress_bytes"] == {"received": 10, "total": 100}


def test_update_check_source_mode(client):  # 夹具名以文件实际为准
    from migration import _form
    ...
    r = client.post("/api/update/check", json={})
    assert r.status_code == 200 and r.json()["mode"] == "source"


def test_update_check_three_states(client, monkeypatch):
    """三态:发现新版(tag/asset/size)/已是最新/失败(error 三段式)。"""
    from migration import _form, updater
    monkeypatch.setattr(_form, "FORM", "onefile")
    rel = updater.ReleaseInfo("v0.13.1", (
        updater.AssetInfo("mcmig-gui-0.13.1-win-x64.exe", 31, "u"),
        updater.AssetInfo("SHA256SUMS.txt", 1, "s"),
    ))
    monkeypatch.setattr(updater, "fetch_latest_release", lambda: rel)
    import migration
    monkeypatch.setattr(migration, "__version__", "0.12.0")
    r = client.post("/api/update/check", json={})
    body = r.json()
    assert body["newer"] is True and body["asset_name"] == "mcmig-gui-0.13.1-win-x64.exe"
    monkeypatch.setattr(updater, "fetch_latest_release", lambda: None)
    assert client.post("/api/update/check", json={}).json()["newer"] is None
    def _boom():
        raise updater.UpdateError("更新检查失败", "网络不可达")
    monkeypatch.setattr(updater, "fetch_latest_release", _boom)
    body = client.post("/api/update/check", json={}).json()
    assert body["error"]["what"] == "更新检查失败"


def test_update_download_job_done_payload(client, monkeypatch, game_root):
    """update job 四件之三:done 载荷=UpdatePlan 全字段(与 GET 同源)。"""
    from migration import _form, updater
    import migration.gui.server as srv
    monkeypatch.setattr(_form, "FORM", "onefile")
    monkeypatch.setattr(srv, "_update_staging_root", lambda: game_root / "up-staging")
    plan = updater.UpdatePlan("0.13.1", "a.exe", game_root / "up-staging" / "u" / "a.exe", 7, "ab")
    monkeypatch.setattr(updater, "plan_update",
                        lambda root, *, progress_cb=None, should_cancel=None: plan)
    r = client.post("/api/update/download", json={})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    snap = _wait_terminal(client, job_id)   # 复用既有「轮询 GET 到终态」helper;无则内联 time.sleep 轮询
    done = snap["done"]
    assert done["job_kind"] == "update" and done["version"] == "0.13.1" and done["sha256"] == "ab"


def test_update_download_cancelled_terminal(client, monkeypatch, game_root):
    """取消:should_cancel 命中 → 清理+cancelled 终态(done.cancelled=True)。"""
    from migration import _form, updater
    import migration.gui.server as srv
    monkeypatch.setattr(_form, "FORM", "onefile")
    monkeypatch.setattr(srv, "_update_staging_root", lambda: game_root / "up-staging")
    cleaned: list[Path] = []

    def _fake_plan(root, *, progress_cb=None, should_cancel=None):
        while should_cancel is not None and not should_cancel():
            time.sleep(0.01)
            if progress_cb:
                progress_cb(1, 10)
        cleaned.append(root)
        raise updater.UpdateCancelled()

    monkeypatch.setattr(updater, "plan_update", _fake_plan)
    r = client.post("/api/update/download", json={})
    job_id = r.json()["job_id"]
    _wait_running(client, job_id)
    client.post(f"/api/jobs/{job_id}/cancel", json={})
    snap = _wait_terminal(client, job_id)
    assert snap["status"] == "cancelled" and snap["done"]["cancelled"] is True


def test_update_open_location_rejects_outside_staging(client, monkeypatch, tmp_path):
    import migration.gui.server as srv
    monkeypatch.setattr(srv, "_update_staging_root", lambda: tmp_path)
    r = client.post("/api/update/open-location", json={"path": str(tmp_path.parent / "evil.exe")})
    assert r.status_code == 400


def test_update_open_location_uses_explorer_select(client, monkeypatch, tmp_path):
    calls: list[list[str]] = []
    monkeypatch.setattr("migration.gui.server.subprocess.run",
                        lambda cmd, check=None: calls.append(cmd))
    import migration.gui.server as srv
    monkeypatch.setattr(srv, "_update_staging_root", lambda: tmp_path)
    target = tmp_path / "u" / "a.exe"; target.parent.mkdir(); target.write_bytes(b"")
    r = client.post("/api/update/open-location", json={"path": str(target)})
    assert r.status_code == 200
    assert calls and calls[0][0] == "explorer" and calls[0][1] == "/select,"
```

> 注:`client`/`game_root`/`_wait_terminal`/`_wait_running` 以 `tests/test_gui_server.py` 既有夹具与 helper 名为准(终态轮询若无名 helper,写内联 `for _ in range(200): snap=client.get(...).json(); if snap["status"] in (...)...`);`server.py` 须 `import subprocess`(若尚未引入)。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gui_server.py -k update -v`
Expected: 7 FAIL(端点/白名单/progress_bytes 不存在)

- [ ] **Step 3: 实现**

`server.py` 修改点:

```python
# ① Job.__init__ 追加(约 258 行 _done_payload 旁)
        self._last_progress: dict | None = None
# ② can_cancel 白名单(266-274)
        return self.kind in ("migrate", "swap", "update") and not self.cancel_closed
# ③ emit 分支(294-296 phase/file 之间)
            elif etype == "progress":
                # update job 字节进度(spec §4.3.4;与 file 事件通道并行)
                self._last_progress = ev
# ④ snapshot()(386-417):progress 推导后追加
            progress_bytes: dict[str, int] | None = None
            if self._last_progress is not None:
                progress_bytes = {
                    "received": self._last_progress["received"],
                    "total": self._last_progress["total"],
                }
            # 返回 dict 增键 "progress_bytes": progress_bytes,
```

模块级函数(create_app 外):

```python
def _update_staging_root() -> Path:
    """GUI 暂存根(spec §4.3.1 两端分治):冻结 exe 旁 data/update-staging,不回退 TEMP。

    GUI 绿色模式 workdir 前置校验 exe/data 可写,到达更新面板必然可写;
    不可写属致命路径(app.py 消息框契约),不走 TEMP 兜底。
    """
    from .. import updater

    return updater.staging_root_at(Path(sys.executable).parent)
```

端点区(create_app 内,api_job_cancel 附近;`import sys/subprocess` 入文件头;当前版本一律经 `updater.local_version()` 动态读):

```python
    class UpdateOpenRequest(BaseModel):
        """打开暂存位置请求(资产路径来自 done 载荷)。"""
        path: str

    @app.post("/api/update/check")
    def api_update_check() -> dict[str, object]:
        """检查更新(同步,量级=HTTP 超时 10s):三态 newer/latest/error + 源码模式。"""
        from .. import _form, updater

        current = updater.local_version()
        if _form.FORM == "source":
            return {"current": current, "mode": "source", "newer": None,
                    "latest": None, "asset_name": None, "size": None, "error": None}
        try:
            rel = updater.fetch_latest_release()
        except updater.UpdateError as e:
            return {"current": current, "mode": "frozen", "newer": None, "latest": None,
                    "asset_name": None, "size": None, "error": {"what": e.what, "why": e.why}}
        if rel is None or not updater.is_newer(rel.tag, current):
            latest = None if rel is None else rel.tag.lstrip("vV")
            return {"current": current, "mode": "frozen", "newer": False, "latest": latest,
                    "asset_name": None, "size": None, "error": None}
        asset = updater.pick_asset(rel.assets)
        return {"current": current, "mode": "frozen", "newer": True, "latest": rel.tag.lstrip("vV"),
                "asset_name": asset.name if asset else None,
                "size": asset.size if asset else None, "error": None}

    @app.post("/api/update/download")
    def api_update_download() -> dict[str, str]:
        """启动 update job(可取消):下载+校验+暂存;单锁 409;源码模式 400。"""
        from .. import _form, updater

        if _form.FORM == "source":
            raise ApiError(400, "err_update_source.what", "err_update_source.why")

        def _run(job: Job) -> None:
            last = 0

            def _progress(received: int, total: int) -> None:
                nonlocal last
                if received - last >= 1024 * 1024 or received == total:  # 兆级节流
                    last = received
                    job.emit({"type": "progress", "received": received, "total": total})

            try:
                staging = _update_staging_root()
                plan = updater.plan_update(staging, progress_cb=_progress,
                                           should_cancel=lambda: job.status == "cancelling")
            except updater.UpdateCancelled:
                job.emit({"type": "done", "job_kind": "update", "cancelled": True})
                return
            except updater.UpdateError as e:
                job.emit({"type": "error", "job_kind": "update", "code": "update_failed",
                          "what": e.what, "why": e.why,
                          "details": {"code": "update_failed"}})
                return
            if plan is None:
                job.emit({"type": "done", "job_kind": "update", "no_update": True})
                return
            job.emit({"type": "done", "job_kind": "update", "version": plan.version,
                      "asset_name": plan.asset_name, "path": str(plan.path),
                      "size": plan.size, "sha256": plan.sha256})

        job = _start_job("update", _run)
        if job is None:
            raise ApiError(409, "err_job_busy.what", "err_job_busy.why")
        return {"job_id": job.id}

    @app.post("/api/update/open-location")
    def api_update_open_location(req: UpdateOpenRequest) -> dict[str, str]:
        """打开暂存位置(Explorer 选中;spec §4.3.4:绝不 startfile 资产本体)。"""
        target = Path(req.path).resolve()
        root = _update_staging_root().resolve()
        if not target.is_relative_to(root):
            raise ApiError(400, "err_update_open_outside.what", "err_update_open_outside.why")
        # explorer /select 只接受反斜杠
        subprocess.run(["explorer", "/select,", str(target)], check=False)
        return {"ok": True}
```

`STRINGS.py` 追加:

```python
    # --- 更新基础档(批次I-W4 T13;spec §4.3.4) ---
    "err_update_source.what": "源码运行模式不支持下载更新",
    "err_update_source.why": (
        "当前为源码运行形态,请 git pull 更新到目标版本;"
        "自动下载仅面向打包发行形态"
    ),
    "err_update_open_outside.what": "拒绝打开暂存目录之外的路径",
    "err_update_open_outside.why": "打开位置仅限本次下载的暂存资产;请从更新面板重新发起",
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gui_server.py -k update -v && .venv/Scripts/python.exe -m pytest`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add migration/gui/server.py migration/gui/STRINGS.py tests/test_gui_server.py
git commit -m "feat(batch-i-w4): GUI 更新端点——update job 四件契约(可取消/字节进度入快照/终态载荷/409)(T13)"
```

---

### Task 10: 页面更新面板——两态展示/恢复分派/打开位置安全

**Files:**
- Modify: `migration/gui/index.html`(step1 区后追加 `<details id="update-panel">`;PAGE_STRINGS 追加;`applyEvent` progress 分支;`updateModel`/`applyUpdateEvent`/`renderUpdatePanel`/`checkUpdate`/`startUpdateDownload`/`openUpdateLocation`/`updateHandlers`;`handlersForKind` 分派;`restoreSavedJob` 恢复)
- Test: `tests/test_page_contract.py`(追加)、`tests/test_page_behavior.py`(追加)

**Interfaces:**
- Consumes: Task 9 三端点与 job 事件契约。
- Produces: 页面状态核纯函数 `updateModel`(status: "idle"|"checking"|"available"|"latest"|"downloading"|"downloaded"|"error";latest/received/total/plan/error 字段)与 `applyUpdateEvent(ev)`、`renderUpdatePanel()`(node harness 可驱动);`jobModel.bytes`(applyEvent 对 `type=="progress"` 维护 `{received,total}`)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_page_contract.py 追加
def test_update_panel_elements_and_strings():
    """面板骨架+文案全进 PAGE_STRINGS;行为红线:无「立即运行」。"""
    page = _page_text()   # 本文件既有取 index.html 源的 helper(名以实际为准)
    for needle in ('id="update-panel"', 'id="btn-update-check"', 'id="btn-update-download"',
                   'id="btn-update-open"', 'id="update-status"'):
        assert needle in page
    assert "已下载,尚未应用" in page          # 两态展示(spec §5.1 呈现规范)
    assert "立即运行" not in page              # 行为红线(spec §4.3.2)
    assert "退出" in page and "替换" in page   # 三步指引要素


def test_handlers_dispatch_includes_update():
    page = _page_text()
    assert 'kind === "update"' in page and "updateHandlers()" in page


def test_apply_event_progress_branch_present():
    page = _page_text()
    assert '"progress"' in page and "jobModel.bytes" in page
```

```python
# tests/test_page_behavior.py 追加
def test_update_progress_and_two_state_done():
    """Review Focus:progress 进状态核;done 进入 downloaded 两态(含 plan 字段)。"""
    r = _run_js(r"""
      newJob("j1", "update");
      applyEvent({type:"progress", received: 1048576, total: 31457280, seq: 1});
      applyEvent({type:"done", job_kind:"update", version:"9.9.9",
                  asset_name:"mcmig-gui-9.9.9-win-x64.exe",
                  path:"D:\\mcmig\\data\\update-staging\\u\\a.exe",
                  size:31457280, sha256:"ab", seq: 2});
      emit({bytes: jobModel.bytes ? [jobModel.bytes.received, jobModel.bytes.total] : null,
            status: updateModel.status,
            ver: updateModel.plan ? updateModel.plan.version : null});
    """)
    assert r["bytes"] == [1048576, 31457280]
    assert r["status"] == "downloaded"
    assert r["ver"] == "9.9.9"


def test_update_error_and_cancel_states():
    r = _run_js(r"""
      newJob("j1", "update");
      applyUpdateEvent({type:"error", what:"校验失败", why:"SHA256 不一致"});
      var e1 = {status: updateModel.status, what: updateModel.error ? updateModel.error.what : null};
      newJob("j2", "update");
      applyUpdateEvent({type:"done", job_kind:"update", cancelled: true});
      emit({err: e1, cancelled: updateModel.status});
    """)
    assert r["err"] == {"status": "error", "what": "校验失败"}
    assert r["cancelled"] == "cancelled"


def test_update_check_states_via_model():
    r = _run_js(r"""
      updateModel.status = "idle";
      applyCheckResult({newer: false});
      var a = updateModel.status;
      applyCheckResult({newer: true, latest: "9.9.9", asset_name: "a.exe", size: 1024});
      var b = [updateModel.status, updateModel.latest];
      applyCheckResult({error: {what: "x", why: "y"}});
      emit({a: a, b: b, c: updateModel.status});
    """)
    assert r["a"] == "latest" and r["b"] == ["available", "9.9.9"] and r["c"] == "error"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_page_contract.py tests/test_page_behavior.py -k update -v`
Expected: 6 FAIL

- [ ] **Step 3: 实现(index.html)**

骨架(插在 `</section>`(step1 结束)之后、step2 之前):

```html
<!-- ============ 关于/更新(折叠,W4 T13;spec §4.3.4) ============ -->
<details id="update-panel" class="update-panel">
  <summary id="update-summary">关于 / 更新</summary>
  <div id="update-current"></div>
  <button id="btn-update-check" class="secondary">检查更新</button>
  <div id="update-status" hidden></div>
  <button id="btn-update-download" hidden>下载并校验</button>
  <button id="btn-update-cancel" class="secondary" hidden>取消下载</button>
  <div id="update-progress" hidden></div>
  <div id="update-done" hidden></div>
  <button id="btn-update-open" class="secondary" hidden>打开暂存位置</button>
</details>
```

PAGE_STRINGS 追加(键名与中文文案):

```javascript
  UPDATE_TITLE: "关于 / 更新",
  UPDATE_CHECK_BTN: "检查更新",
  UPDATE_CHECKING: "正在检查…",
  UPDATE_LATEST: function (v) { return "已是最新版本(v" + v + ")"; },
  UPDATE_NO_RELEASE: "尚无任何发布版本",
  UPDATE_AVAILABLE: function (v, mb) { return "发现新版 v" + v + "(" + mb + " MB)"; },
  UPDATE_SOURCE: "源码运行模式:请 git pull 更新,自动下载仅面向打包发行版",
  UPDATE_DOWNLOAD_BTN: "下载并校验",
  UPDATE_CANCEL_BTN: "取消下载",
  UPDATE_DOWNLOADING: function (recvMb, totalMb) { return "下载中 " + recvMb + " / " + totalMb + " MB"; },
  UPDATE_DOWNLOADED: "已下载,尚未应用",
  UPDATE_STEPS: "应用三步:①退出当前 mcmig;②把下载的文件替换到 mcmig 所在目录(覆盖旧文件);③重新启动 mcmig。应用后暂存目录可整目录删除。",
  UPDATE_OPEN_BTN: "打开暂存位置",
  UPDATE_CANCELLED: "下载已取消(本次临时文件已清理)",
  UPDATE_BUSY: "有任务正在运行,请等它结束后再下载",
```

状态核(script 段,jobModel 附近):

```javascript
  // ---- 更新面板状态核(W4 T13;零 DOM 纯函数,行为测试驱动) ----
  var updateModel = {
    status: "idle",      // idle/checking/available/latest/downloading/downloaded/cancelled/error
    current: null, latest: null, assetName: null, size: null,
    received: 0, total: 0, plan: null, error: null,
  };
  function applyCheckResult(res) {
    if (res.error) { updateModel.status = "error"; updateModel.error = res.error; return; }
    updateModel.current = res.current; updateModel.latest = res.latest;
    updateModel.assetName = res.asset_name; updateModel.size = res.size;
    updateModel.error = null;
    if (res.mode === "source") { updateModel.status = "latest"; updateModel.latest = "source"; return; }
    updateModel.status = res.newer ? "available" : "latest";
  }
  function applyUpdateEvent(ev) {
    if (ev.type === "progress") {
      updateModel.status = "downloading";
      updateModel.received = ev.received; updateModel.total = ev.total;
    } else if (ev.type === "error") {
      updateModel.status = "error"; updateModel.error = { what: ev.what, why: ev.why };
    } else if (ev.type === "done") {
      if (ev.cancelled) { updateModel.status = "cancelled"; }
      else if (ev.no_update) { updateModel.status = "latest"; }
      else {
        updateModel.status = "downloaded";
        updateModel.plan = { version: ev.version, asset_name: ev.asset_name,
                             path: ev.path, size: ev.size, sha256: ev.sha256 };
      }
    }
  }
```

`applyEvent` 追加分支(file 分支旁):

```javascript
    } else if (ev.type === "progress") {
      jobModel.bytes = { received: ev.received, total: ev.total };
```

`newJob` 代际重置处同步归位(W3 代际纪律,防旧任务/旧恢复污染新任务):

```javascript
  updateModel.status = "idle"; updateModel.plan = null; updateModel.error = null;
  updateModel.received = 0; updateModel.total = 0;
```

渲染与接线(DOM 区):

```javascript
  function renderUpdatePanel() { /* 按 updateModel.status 切换各元素 hidden/文本,
                                     downloaded 态展示两态文案+三步指引+打开按钮;
                                     页面实现细节,契约测+手测覆盖 */ }
  function checkUpdate() {
    updateModel.status = "checking"; renderUpdatePanel();
    fetch("/api/update/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then(function (r) { return r.json(); })
      .then(function (body) { applyCheckResult(body); renderUpdatePanel(); })
      .catch(function () { applyCheckResult({ error: { what: "检查失败", why: "无法连接本地服务" } }); renderUpdatePanel(); });
  }
  function startUpdateDownload() {
    updateModel.status = "downloading"; renderUpdatePanel();
    fetch("/api/update/download", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then(function (r) {
        if (r.status === 409) { updateModel.status = "error";
          updateModel.error = { what: PAGE_STRINGS.UPDATE_BUSY, why: "" }; renderUpdatePanel(); return null; }
        return r.json();
      })
      .then(function (body) {
        if (!body) return;
        newJob(body.job_id, "update");
        openEvents(body.job_id, updateHandlers());
      })
      .catch(function () { applyUpdateEvent({ type: "error", what: "下载失败", why: "无法连接本地服务" }); renderUpdatePanel(); });
  }
  function openUpdateLocation() {
    if (!updateModel.plan) return;
    fetch("/api/update/open-location",
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: updateModel.plan.path }) });
  }
  function updateHandlers() {
    return {
      onmessage: function (ev) {
        applyEvent(ev);
        applyUpdateEvent(ev);
        renderUpdatePanel();
      },
      onclose: function () { renderUpdatePanel(); },
    };
  }
```

`handlersForKind`(1294 行)改为三分派:

```javascript
function handlersForKind(kind) {
  if (kind === "plan") { return planHandlers(); }
  if (kind === "update") { return updateHandlers(); }
  return migrateHandlers();
}
```

`restoreSavedJob`:kind==="update" 时从 GET snapshot 重建——`applyEvent` 消费 `done` 载荷、`progress_bytes` 回填 `jobModel.bytes`,再 `applyUpdateEvent(done)`/`applyUpdateEvent({type:"progress",...})`,续订 `openEvents(jobId, updateHandlers(), { resumeSeq: jobModel.lastSeq })`;init 时填充 `#update-current`(GET /api/config 或 check 惰性)。取消按钮接 `POST /api/jobs/{id}/cancel`(复用既有 cancelMigrate 通道,终态守卫同 W3 规则)。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_page_contract.py tests/test_page_behavior.py -k update -v && .venv/Scripts/python.exe -m pytest`
Expected: 全绿

- [ ] **Step 5: 手测清单增补 + 提交**

`tests/gui-manual-checklist.md` 追加 K 组条目(浏览器源码模式即可验证面板三态/取消/409;真下载走 0.13.0 试发,见 Task 14):

```bash
git add migration/gui/index.html tests/test_page_contract.py tests/test_page_behavior.py tests/gui-manual-checklist.md
git commit -m "feat(batch-i-w4): 页面更新面板——两态展示/恢复分派/打开位置安全(T13)"
```

---

### Task 11: swap 装包逐 jar 进度(progress_cb 三端接线)

**Files:**
- Modify: `migration/pipeline.py`(`swap_install` 约 1388-1443)
- Modify: `migration/cli.py`(`_cmd_swap` 装包调用处)
- Modify: `migration/gui/server.py`(swap apply runner 约 1447-1454)
- Test: `tests/test_pipeline.py`(追加)、`tests/test_gui_server.py`(追加)

**Interfaces:**
- Produces: `swap_install(..., progress_cb: Callable[[str, int, int], None] | None = None)`——参数 `(jar 文件名, i, total)`,i 从 1 起;每 jar 物理拷贝成功后调用;dry_run 不调用;None 时行为与现状完全一致。
- GUI 端以既有 `file` 事件承载(`{"type":"file","path":<jar 名>,"index":i,"total":total}`)→ snapshot `progress` 与页面「最近:」渲染零新增通道。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline.py 追加(沿用本文件 swap 夹具:game_root/src 版本目录/new_pack 形态)
def test_swap_install_reports_progress_per_jar(swap_env):   # 夹具名以文件实际为准
    """progress_cb 逐 jar 回调(1-based,含 total);dry_run 不回调。"""
    calls: list[tuple[str, int, int]] = []
    outcome = pipeline.swap_install(..., progress_cb=lambda n, i, t: calls.append((n, i, t)))
    assert [c[1] for c in calls] == list(range(1, len(calls) + 1))
    assert all(c[2] == len(calls) for c in calls)
    calls.clear()
    pipeline.swap_install(..., dry_run=True, progress_cb=lambda n, i, t: calls.append((n, i, t)))
    assert calls == []
```

```python
# tests/test_gui_server.py 追加
def test_swap_apply_emits_file_events_per_jar(client, monkeypatch, game_root, swap_env_gui):
    """GUI 装包阶段:每个 jar 产生 file 事件(index/total)——复用既有进度通道。"""
    ...  # 触发 /api/swap/apply(既有两阶段夹具),订阅或轮询收集事件
    files = [ev for ev in events if ev["type"] == "file"]
    assert files and [f["index"] for f in files] == list(range(1, len(files) + 1))
```

> 注:两处夹具/触发方式以既有 swap 测试(`test_swap_apply_*`)为准展开,断言形态如上;`swap_env`/`swap_env_gui` 若不存在,直接复用既有 swap 用例的搭建代码。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline.py tests/test_gui_server.py -k progress -v`
Expected: FAIL(参数不存在)

- [ ] **Step 3: 实现**

`pipeline.swap_install` 签名追加 `progress_cb`;装包循环内 `copy_atomic` 成功、`copied += 1` 之后:

```python
                if progress_cb is not None:
                    progress_cb(jar.name, copied, total_jars)
```

(`total_jars=len(actions)` 于预扫描处已有统计基础;dry_run 分支不调用。)

CLI `_cmd_swap` 装包调用处传( rich 进度行,与既有输出风格一致):

```python
    from rich.progress import Progress, BarColumn, TextColumn

    with Progress(TextColumn("[进度] {task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}")) as progress:
        task = progress.add_task("装包", total=<total_jars>)
        outcome = swap_install(..., progress_cb=lambda name, i, total: progress.advance(task))
```

(total 的取得:`swap_install` 预检返回值已有 actions 统计——若不在返回面,则 progress_cb 首调时 `progress.update(task, total=total)` 补齐;实现取简,不扩返回面。)

GUI swap apply runner 装包调用处:

```python
                            journal=journal, should_cancel=_should_cancel,
                            progress_cb=lambda name, i, total: job.emit(
                                {"type": "file", "path": name, "index": i, "total": total}))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline.py tests/test_gui_server.py -k "progress or swap" -v && .venv/Scripts/python.exe -m pytest`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add migration/pipeline.py migration/cli.py migration/gui/server.py tests/test_pipeline.py tests/test_gui_server.py
git commit -m "feat(batch-i-w4): swap 装包逐 jar 进度(progress_cb 三端接线)(T13.5)"
```

---

### Task 12: 页面收口四项(replan 终态取消提示/恢复按钮文案/preflight 复燃/resync 诚实文案)

**Files:**
- Modify: `migration/gui/index.html`
- Test: `tests/test_page_contract.py`(追加)、`tests/test_page_behavior.py`(追加)

**Interfaces:** 无新接口;四项均为渲染分支修正(spec §4.4 第 2/3/4/8 项)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_page_contract.py 追加
def test_replan_failed_hides_cancel_hint():
    """swap_replan_failed 终态不再展示 cancelNote(else 分支,spec T13.5-2)。"""
    page = _page_text()
    assert "swap_replan_failed" in page  # 分支存在
    # 行为级:node 测试断言渲染结果


def test_restore_cancel_button_text_by_kind():
    """恢复 running swap 的取消按钮文案按 kind 分化(T13.5-3)。"""
    page = _page_text()
    assert "取消换包" in page and "取消迁移" in page


def test_terminal_swap_preflight_buttons_revive():
    """terminal swap_preflight 面板死按钮复燃:用 jobModel.src/dst(T13.5-4)。"""
    page = _page_text()
    assert "btn-swap-back" in page or "swapBackToStart" in page  # 以实际按钮/函数名为准
```

```python
# tests/test_page_behavior.py 追加
def test_replan_failed_no_cancel_note_and_preflight_revive():
    r = _run_js(r"""
      newJob("j1", "swap");
      applyEvent({type:"error", code:"swap_replan_failed", job_kind:"swap", src:"a", dst:"b", seq:1});
      var hiddenNote = !cancelHintVisible(jobModel);
      var src = jobModel.src, dst = jobModel.dst;
      newJob("j2", "swap_preflight");
      applyEvent({type:"done", job_kind:"swap_preflight", src:"a", dst:"b", seq:1});
      emit({hiddenNote: hiddenNote, src: src, dst: dst, src2: jobModel.src, dst2: jobModel.dst});
    """)
    assert r["hiddenNote"] is True
    assert (r["src"], r["dst"]) == ("a", "b")
    assert (r["src2"], r["dst2"]) == ("a", "b")


def test_resync_exhausted_banner_honest_wording():
    """退避耗尽横幅=诚实版(T13.5-8):文案含「请刷新页面或检查服务」。"""
    r = _run_js(r"""
      emit({giveup: resyncFailureText()});
    """)
    assert "刷新页面" in r["giveup"]
```

> 注:`cancelHintVisible(jobModel)`(布尔)与 `resyncFailureText()`(文案)为本次**新增**的状态核纯函数——实现落在 index.html 状态核区(零 DOM,W3 纪律),渲染层消费;`jobModel.src/dst` 由 `applyEvent` 对携带 src/dst 的事件(swap/swap_preflight)顺手记录(恢复态复燃的输入,spec T13.5-4)。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_page_contract.py tests/test_page_behavior.py -k "replan or revive or resync or cancel_button" -v`
Expected: 5 FAIL

- [ ] **Step 3: 实现(index.html 四处)**

1. 终态渲染的 cancel-hint 区:`jobModel.error && jobModel.error.code === "swap_replan_failed"`(或既有判定位)不加 cancelNote——在该分支补 else;恢复判定抽 `cancelHintVisible(jobModel)` 纯函数。
2. 恢复态/运行态取消按钮文案:`kind === "swap" || kind === "swap_preflight"` → `PAGE_STRINGS.CANCEL_SWAP("取消换包")`,否则 `CANCEL_BUTTON("取消迁移")`;终态判断先行(W3 规则不变)。
3. terminal swap_preflight 面板:返回/重跑按钮以 `jobModel.src`/`jobModel.dst` 复燃(回步①选中,或直接重发 preflight)。
4. resync 退避耗尽(4 次后):横幅文案改 `PAGE_STRINGS.RESYNC_GIVE_UP = "连接已断开且多次重试失败,请刷新页面或检查服务。"`(替换现「重连中」类措辞;重试期内文案不变)——文案取自新纯函数 `resyncFailureText()`。
5. `applyEvent` 对携带 `src`/`dst` 的事件(swap/swap_preflight 的 done/error)顺手记 `jobModel.src/dst`(第 3 项与行为测试的输入)。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_page_contract.py tests/test_page_behavior.py -v && .venv/Scripts/python.exe -m pytest`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add migration/gui/index.html tests/test_page_contract.py tests/test_page_behavior.py
git commit -m "fix(batch-i-w4): 页面收口四项——replan 终态取消提示/恢复按钮文案/preflight 复燃/resync 诚实文案(T13.5)"
```

---

### Task 13: --window 参数转发 + description 刷新 + swap node 测试入库

**Files:**
- Modify: `migration/cli.py`(`_cmd_gui` --window 分支约 808-811)
- Modify: `migration/gui/app.py`(`main` 解析 argv 的 --port/--no-browser)
- Modify: `pyproject.toml`(description)
- Test: `tests/test_cli.py`、`tests/test_gui_app.py`、`tests/test_page_behavior.py`(各追加)

**Interfaces:**
- Produces: `mcmig gui --window --port N --no-browser` 参数不再丢失;`app.main(argv)` 解析 `--port`/`--no-browser`(窗口模式 honor --port,降级浏览器模式透传)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cli.py 追加
def test_gui_window_forwards_port_and_no_browser(monkeypatch):
    seen: dict = {}
    import migration.gui.app as gui_app
    monkeypatch.setattr(gui_app, "main", lambda argv: seen.update(argv=argv) or 0)
    rc = _run_cli(["gui", "--window", "--port", "8123", "--no-browser"])
    assert rc == 0
    assert "--port" in seen["argv"] and "8123" in seen["argv"] and "--no-browser" in seen["argv"]
```

```python
# tests/test_gui_app.py 追加
def test_window_main_honors_port(monkeypatch):
    """窗口模式 honor --port(此前恒随机空闲端口,T13.5-5)。"""
    import migration.gui.app as gui_app
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: [])
    _fake_webview_ok(monkeypatch)   # Task 1 建立的 helper
    monkeypatch.setattr(gui_app, "create_app", _fake_app)
    ports: list[int] = []
    monkeypatch.setattr(gui_app, "pick_free_port", lambda: 9999)

    class _Srv:
        should_exit = False

    class _Thr:
        @staticmethod
        def is_alive():
            return False

    def _srv(app, port):
        ports.append(port)
        return _Srv(), _Thr()

    monkeypatch.setattr(gui_app, "_start_server_thread", _srv)
    monkeypatch.setattr(gui_app, "wait_server_ready", lambda port, timeout=10.0: True)
    rc = gui_app.main(["--port", "8123"])
    assert rc == 0 and ports == [8123]
```

```python
# tests/test_page_behavior.py 追加(swap 行为 node 测试入库,T13.5-7)
def test_swap_apply_decision_state_renders():
    r = _run_js(r"""
      newJob("j1", "swap");
      applyEvent({type:"done", job_kind:"swap", src:"a", dst:"b",
                  plan:{files:[]}, swap:{install:{copied:3, skipped:0, conflicts:[]}}, seq:1});
      emit({kind: jobModel.kind, hasPlan: !!jobModel.plan,
            copied: jobModel.swap && jobModel.swap.install ? jobModel.swap.install.copied : null});
    """)
    assert r == {"kind": "swap", "hasPlan": True, "copied": 3}
```

pyproject description 断言并入 `tests/test_packaging_layout.py`:

```python
def test_pyproject_description_refreshed():
    """T13.5-6:description 不再是「只读 scan/diff」。"""
    data = _pyproject()
    assert "scan/diff" not in data["project"]["description"]
    assert "迁移" in data["project"]["description"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py tests/test_gui_app.py tests/test_page_behavior.py tests/test_packaging_layout.py -k "window_forwards or honors_port or swap_apply_decision or description" -v`
Expected: 4 FAIL

- [ ] **Step 3: 实现**

`cli.py` --window 分支:

```python
    if getattr(args, "window", False):
        from .gui.app import main as window_main

        fwd: list[str] = []
        if getattr(args, "port", None) is not None:
            fwd += ["--port", str(args.port)]
        if getattr(args, "no_browser", False):
            fwd.append("--no-browser")
        return window_main(fwd)
```

`app.main` 头部解析(参数不致致命——未知参数沿 argparse 语义退出):

```python
    from ..cli import build_parser

    ns = build_parser().parse_args(["gui", *(argv if argv is not None else ())])
    port = int(ns.port) if getattr(ns, "port", None) is not None else pick_free_port()
```

(`main` 内两处 `pick_free_port()` 调用点统一改用该 `port`;docstring 同步。)

`pyproject.toml`:`description = "Minecraft 整合包版本迁移工具:scan/diff/plan/migrate/换包/向导界面"`。

`no_plain_replan` 契约测试(在 `tests/test_page_contract.py`)移到 swap 函数区:仅移动测试函数位置+分区注释,断言不变。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py tests/test_gui_app.py tests/test_page_behavior.py tests/test_packaging_layout.py -v && .venv/Scripts/python.exe -m pytest`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add migration/cli.py migration/gui/app.py pyproject.toml tests/test_cli.py tests/test_gui_app.py tests/test_page_behavior.py tests/test_page_contract.py tests/test_packaging_layout.py
git commit -m "fix(batch-i-w4): --window 参数转发+description 刷新+swap 行为 node 测试入库(T13.5)"
```

---

### Task 14: T15 收口——README/AGENTS/backlog/版本 0.13.0/手测清单 K 组

**Files:**
- Modify: `README.md`、`README.zh-CN.md`、`README.en.md`
- Modify: `AGENTS.md`(构建与运行命令节/迁移工具脚本参考表/分发策略节)
- Create: `docs/backlog.md`
- Modify: `pyproject.toml`(`version = "0.13.0"`)、`migration/__init__.py`(`__version__ = "0.13.0"`)
- Modify: `tests/gui-manual-checklist.md`(K 组)
- Test: `tests/test_packaging_layout.py`(追加版本一致性守卫)

**Interfaces:**
- Consumes: 前 13 个任务的全部交付。
- Produces: 0.13.0 试发就绪态(推送与 tag 待用户放行);`docs/backlog.md` 递延账本。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_packaging_layout.py 追加
def test_version_two_places_consistent():
    """版本双源一致(发布守卫的本地常驻形态,D8)。"""
    data = _pyproject()
    mv = re.search(r'__version__\s*=\s*"([^"]+)"',
                   (_ROOT / "migration" / "__init__.py").read_text(encoding="utf-8")).group(1)
    assert data["project"]["version"] == mv == "0.13.0"


def test_backlog_doc_exists_with_required_sections():
    """递延账本落库(spec §4.5):含事务档滑移与 0.12.0 复审④两组锚。"""
    text = (_ROOT / "docs" / "backlog.md").read_text(encoding="utf-8")
    assert "事务档" in text and "服务线程" in text
```

(`import re` 补文件头。)

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_packaging_layout.py -k "version_two or backlog" -v`
Expected: 2 FAIL

- [ ] **Step 3: 实现**

**版本**:`pyproject.toml` → `version = "0.13.0"`;`migration/__init__.py` → `__version__ = "0.13.0"`。

**README 三件**:
- `README.md`(hub):摘要改为「Minecraft 整合包版本迁移工具:扫描/比对/计划/执行/换包/向导界面」;新增「下载与更新」节指向 Release 双资产与 `mcmig update`;过时的「只读 scan/diff」「零写入」表述删除。
- `README.zh-CN.md` / `README.en.md` 同步重写,固定节序:是什么→两种包怎么选(onedir 绿色目录=推荐/onefile 单文件=尝鲜)→下载(Release 资产名+SHA256SUMS 校验命令)→更新方法(基础档三步+「已下载,尚未应用」语义+**「自动应用更新随后续版本提供,当前为下载-校验-手动替换」标注**,spec 验收7)→构建自源码(`.venv-build`+锁定文件+build.py+冒烟)→许可证;末尾「附录:占用实测(0.13.0 试发后补录:onefile/onedir 体积、冷启动、磁盘峰值)」。

**AGENTS.md**:
- 「构建与运行命令」节的打包行改为:`构建发行(在 .venv-build): python tools/packaging/build.py --form onefile|onedir`;发布守卫/发版命令(守卫→tag→CI)入「分发策略」;
- 「迁移工具脚本参考」表落四行:`tools/packaging/build.py`(双形态构建+filelist+SUMS)/`check_release.py`(三处版本一致守卫)/`requirements-win-build.txt`(锁定文件,升级重生成)/`gen_manifest.py`(既有);
- 「分发策略」节与现实对齐(workflow 已入库;试发协议一句话引用 spec §5);客户端环境事实表**不动**(modpack 侧资料,仅注「已过时,以实测为准」——spec §4.5)。

**docs/backlog.md**(每项一句「是什么+为何递延+何时捡起」;来源逐条核对 W3 计划处置表与 0.12.0 复审记录后落账,以下为骨架必含项):

```markdown
# 递延账本(1.x 待办)

> 来源:W3 终审 44 项可名表者 / 0.12.0 复审第④阶段 / W4 spec 滑移决策。
> 落账纪律:每项一句「是什么+为何递延+何时捡起」;捡起时移入当批 spec。

## 更新事务档(批次J,spec §6)
- 自动应用更新(安装级锁+受管清单差集+Velopack 评估门)——基础档已交付手动替换闭环,事务档独立验收。
- 更新来源签名——同源 SHA256 只证资产一致不证身份;事务档阶段评估(借鉴 Tauri updater)。

## 可靠性打磨(0.12.0 复审第④阶段)
- 服务线程故障监测(异常退出目前只覆盖启动期)。
- 窗口就绪确认(closing 前的窗口级 ready 信号)。
- Ctrl+C 协作停机措辞与两阶段语义澄清。
- JobStore 公共任务服务重构(四类 job 创建入口去重)。

## 观察项(1.0 实测后定级)
- onefile 杀软误报率与冷启动(双形态并供缓解;README 附录补录数据)。
- GitHub 匿名限额 60/h·每 IP:玩家量级上来再评 token/镜像。
- (W3 终审可名表项,逐条自查 docs/superpowers/plans/2026-10-02-batch-i-w3-capability.md 处置表后补齐此节)
```

**手测清单 K 组**(`tests/gui-manual-checklist.md` 追加):

```markdown
- [ ] K1 真实 Release 下载→替换→配置保留(0.13.0→0.13.1 两连发后执行;spec §5 试发协议)
- [ ] K2 只读目录:CLI 回退 %TEMP% 提示;GUI 拒启消息框(app.py 契约)
- [ ] K3 断网/代理环境:检查更新三段式中文引导,非 traceback
- [ ] K4 无控制台三路径:原生窗口启动/缺 WebView2 降级/页面退出进程回收(onefile exe)
- [ ] K5 打开位置:Explorer 选中资产且无新 mcmig 进程(不执行下载物)
```

- [ ] **Step 4: 跑测试确认通过 + 全量收口**

```bash
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check migration/ tests/
```

Expected: 全绿零告警

- [ ] **Step 5: 提交**

```bash
git add README.md README.zh-CN.md README.en.md AGENTS.md docs/backlog.md pyproject.toml migration/__init__.py tests/test_packaging_layout.py tests/gui-manual-checklist.md
git commit -m "docs(batch-i-w4): 收口——README 双语重写/AGENTS 打包节/backlog 落库/版本 0.13.0(T15)"
```

---

## 试发 runbook(Task 14 之后,用户放行推送时执行;非本计划任务)

1. 用户放行 → `git push origin main` → `git tag v0.13.0 && git push origin v0.13.0` → release.yml 自动出三资产(发布守卫已核三处一致)。
2. 本机装 0.13.0(onedir 形态),改版本两处至 0.13.1 → push → tag v0.13.1 → 在 0.13.0 内走完「检查→发现新版→下载(可取消/刷新恢复)→校验→暂存→手动替换→配置保留」全链(spec §5 两连发)。
3. §8 验收逐条核 → 通过后版本两处改 1.0.0 → tag v1.0.0(批次I 收口)。

## 自审记录

- **Spec 覆盖**:§4.1.1→Task 3(asset_names/make_filelist);§4.1.2→Task 1/2/3;§4.1.3→Task 3;§4.1.4→Task 1/3(改写纪律+后验);§4.2.1→Task 4;§4.2.2→Task 5;§4.3.1→Task 6/7;§4.3.2→Task 7/10(红线文案+无立即运行);§4.3.3→Task 8;§4.3.4→Task 9/10;§4.4 八项→Task 11(1)/12(2,3,4,8)/13(5,6,7);§4.5→Task 14;§5→Task 5+runbook;§6 T14→显式不在范围。§8/§9 验收与测试点已逐任务内联。
- **类型一致性**:`UpdatePlan(version, asset_name, path, size, sha256)` 五字段在 Task 7 定义、Task 8/9/10 同名消费;两处 `progress_cb` 各自钉死(pipeline=`(jar名, i, total)`,updater=`(received, total)`,docstring 显式);`_update_staging_root` 在 Task 9 定义并被 Task 9/10 测试引用;`_form.FORM` 三值在 Task 1/3/6/8/9/10 用法一致;**版本读取统一走 `updater.local_version()` 动态解析**(Task 6 定义,Task 7/8/9 消费——模块顶 `from . import __version__` 绑死值会让 monkeypatch 失效、Task 14 升版后测试假绿)。
- **占位符扫描**:Task 8/9/11 中「以文件既有 helper/夹具名为准」的注记是对既有代码的引用方式说明(执行者读文件后展开),非缺内容;所有新函数均有完整实现代码。窗口关闭的同步化由 fake `webview.start`(no-op)天然覆盖,无需额外桩。
- **Review Focus 对账**:①Task 1 四测;②Task 7 重传/并发/清理三测+Task 6 SUMS/302;③Task 9 progress_bytes/done+Task 10 node 恢复测;④Task 6 pick_asset 三值+Task 3 后验;⑤Task 10 契约(explorer /select/无立即运行/根外 400)。
