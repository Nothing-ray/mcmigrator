"""pywebview 窗口壳:关闭边界/停机接线/降级路径(pywebview 未安装不炸)。"""
import sys

import migration.gui.app as gui_app


def test_should_block_close_idle_vs_busy():
    """spec §5.3/T6:空闲放行(begin_shutdown 置 draining);job 运行阻止并
    给出当前任务描述(状态不变)。"""
    from migration.gui.server import Job, JobStore

    store = JobStore()
    assert gui_app.should_block_close(store) == (False, None)
    assert store.draining is True  # 空闲判定即原子进入排空态
    job = Job("j1", "migrate")
    store._current = job
    store._jobs["j1"] = job
    store2 = JobStore()
    store2._current = job
    store2._jobs["j1"] = job
    blocked, desc = gui_app.should_block_close(store2)
    assert blocked and desc and store2.draining is False


def test_begin_shutdown_drains_when_idle():
    """评审 v3 P2-5:begin_shutdown 锁内原子判定——忙则阻止且状态不变(可
    再判定);闲则置 draining(503 拒新 job 的端点级用例在 test_gui_server.py)。"""
    from migration.gui.server import Job, JobStore

    store = JobStore()
    assert store.begin_shutdown() == (False, None)
    assert store.draining is True  # 空闲判定即原子进入排空态
    job = Job("j1", "migrate")
    store2 = JobStore()
    store2._current = job
    store2._jobs["j1"] = job
    blocked, desc = store2.begin_shutdown()
    assert blocked and desc and store2.draining is False


def test_main_falls_back_to_browser_without_pywebview(monkeypatch, capsys):
    """降级:webview 不可导入 → 打印安装指引并转浏览器模式(不抛 ImportError)。"""
    import builtins

    real_import = builtins.__import__

    def _no_webview(name, *a, **k):
        if name == "webview":
            raise ImportError("no webview")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_webview)
    called = {"rc": None}

    def _fake_gui(args, on_fatal=None):
        called["rc"] = 0
        return 0

    monkeypatch.setattr("migration.cli._cmd_gui", _fake_gui)
    rc = gui_app.main([])
    assert rc == 0 and called["rc"] == 0  # 已走浏览器模式回退
    assert "pywebview" in capsys.readouterr().out  # 安装指引可见


def test_pick_free_port_bindable():
    import socket

    port = gui_app.pick_free_port()
    assert isinstance(port, int) and 0 < port < 65536  # 可用端口须有值(评审 v3 P2-6)
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.close()


def test_wait_server_ready_true_on_200():
    """评审 P2-7:就绪探测——服务应答 200 即 True(临时 uvicorn 起停)。"""
    import threading

    import uvicorn

    from migration.gui.server import create_app

    port = gui_app.pick_free_port()
    srv = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    assert gui_app.wait_server_ready(port, timeout=10.0) is True
    srv.should_exit = True
    t.join(timeout=5.0)


def test_wait_server_ready_false_on_timeout(monkeypatch):
    """就绪超时 False(无服务监听的端口)——按启动失败路径处理。"""
    import socket

    port = gui_app.pick_free_port()
    s = socket.socket()  # 占住端口但不答 HTTP
    s.bind(("127.0.0.1", port))
    s.listen(1)
    try:
        assert gui_app.wait_server_ready(port, timeout=0.3) is False
    finally:
        s.close()


def test_mshtml_renderer_rejected_before_start(monkeypatch):
    """评审 v3 P2-4:预检发现实际 renderer=mshtml(WebView2 缺失时 pywebview 的
    静默回退形态)→ **不起服务线程**、不进 webview.create_window/start,降级
    浏览器模式(缺运行时的主防线;start 抛错是二道防线,见下两用例)。

    假件注入点= `sys.modules["webview.guilib"]`(评审 v4 P1:真实接口是
    `from webview.guilib import initialize`,经子模块路径解析;**不设**
    `fake.guilib` 包属性——包属性初始为 None 正是被修的坑,设了反而掩盖)。
    """
    import types

    fake_guilib = types.ModuleType("webview.guilib")
    fake_winforms = types.ModuleType("webview.platforms.winforms")
    fake_winforms.renderer = "mshtml"  # 预检信号:实际选中 MSHTML
    fake_guilib.initialize = lambda gui: fake_winforms
    fake = types.ModuleType("webview")
    started = {"n": 0}

    def _must_not_start(*a, **k):
        started["n"] += 1
        raise AssertionError("mshtml 形态不得进入 webview.start")

    fake.start = _must_not_start
    fake.create_window = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setitem(sys.modules, "webview.guilib", fake_guilib)
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: [])
    served = {"n": 0}

    def _track_server(app, port):
        served["n"] += 1
        return None, None

    monkeypatch.setattr(gui_app, "_start_server_thread", _track_server)
    fell_back = {"rc": None}

    def _fake_gui(args, on_fatal=None):
        fell_back["rc"] = 0
        return 0

    monkeypatch.setattr("migration.cli._cmd_gui", _fake_gui)
    rc = gui_app.main([])
    assert rc in (0, 2) and started["n"] == 0
    assert served["n"] == 0 and fell_back["rc"] == 0  # 未起线程+已降级


def test_renderer_precheck_exception_falls_back(monkeypatch):
    """评审 v4 P1:预检自身抛异常(如缺 pythonnet → WebViewException)→ 与
    「渲染器不可用」同路径降级浏览器模式,不起服务线程、不裸抛。"""
    import types

    fake_guilib = types.ModuleType("webview.guilib")

    def _boom(gui):
        raise RuntimeError("You must have pythonnet installed")

    fake_guilib.initialize = _boom
    fake = types.ModuleType("webview")

    def _must_not_start(*a, **k):
        raise AssertionError("预检失败不得进入 webview.start")

    fake.start = _must_not_start
    fake.create_window = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setitem(sys.modules, "webview.guilib", fake_guilib)
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: [])
    served = {"n": 0}

    def _track_server(app, port):
        served["n"] += 1
        return None, None

    monkeypatch.setattr(gui_app, "_start_server_thread", _track_server)
    fell_back = {"rc": None}

    def _fake_gui(args, on_fatal=None):
        fell_back["rc"] = 0
        return 0

    monkeypatch.setattr("migration.cli._cmd_gui", _fake_gui)
    rc = gui_app.main([])
    assert rc in (0, 2) and served["n"] == 0 and fell_back["rc"] == 0


def test_webview_start_failure_falls_back_and_stops_server(monkeypatch):
    """评审 P2-7+v3 P2-4+v4 P2-4:渲染器预检通过(edgechromium)但 webview.start
    期抛错(EdgeChrome 初始化失败的二道防线)→ 停 uvicorn 线程+降级浏览器
    模式+退出码有定义,不留僵尸线程;start 确实被调用、降级确实发生。"""
    import types

    fake_guilib = types.ModuleType("webview.guilib")
    fake_winforms = types.ModuleType("webview.platforms.winforms")
    fake_winforms.renderer = "edgechromium"  # 预检通过,异常在 start 期
    fake_guilib.initialize = lambda gui: fake_winforms
    fake = types.ModuleType("webview")
    started = {"n": 0}

    def _boom(*a, **k):
        started["n"] += 1
        raise RuntimeError("WebView2 runtime not found")

    fake.start = _boom

    class _FakeClosing:
        def __iadd__(self, handler):
            return self

    class _FakeWindow:
        # 完整假窗口(评审 v4 P2-4:返回 None 会在 window.events.closing 接线
        # 即 AttributeError,触不到 start())
        events = types.SimpleNamespace(closing=_FakeClosing())

        def destroy(self):
            pass

    fake.create_window = lambda *a, **k: _FakeWindow()
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setitem(sys.modules, "webview.guilib", fake_guilib)
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: [])
    monkeypatch.setattr(gui_app, "wait_server_ready", lambda port, timeout=10.0: True)
    stopped = {"called": False}

    class _FakeServer:
        should_exit = False

    monkeypatch.setattr(
        gui_app, "_start_server_thread", lambda app, port: (_FakeServer(), None)
    )

    def _fake_stop(srv):
        stopped["called"] = True
        srv.should_exit = True

    monkeypatch.setattr(gui_app, "_stop_server", _fake_stop)
    fell_back = {"rc": None}

    def _fake_gui(args, on_fatal=None):
        fell_back["rc"] = 0
        return 0

    monkeypatch.setattr("migration.cli._cmd_gui", _fake_gui)
    rc = gui_app.main([])
    assert rc in (0, 2)
    assert started["n"] == 1  # start 被调用且抛错
    assert stopped["called"] and fell_back["rc"] == 0  # 线程已回收+降级已走


def test_verify_data_integrity_delegates(monkeypatch):
    """评审 P2-7:窗口入口复用数据清单自检——校验函数失败行直传(非空即拒)。"""
    monkeypatch.setattr(gui_app, "_manifest_errors", lambda: ["data/rules.yaml 校验失败"])
    assert gui_app.verify_data_integrity() == ["data/rules.yaml 校验失败"]
    monkeypatch.setattr(gui_app, "_manifest_errors", lambda: [])
    assert gui_app.verify_data_integrity() == []


def test_closing_handler_does_not_evaluate_js_synchronously(monkeypatch):
    """评审(0.12.0 复审#2):pywebview 阻塞型 closing 事件内同步 evaluate_js
    会死锁(上游 #1699:JS 线程正等待回调返回,evaluate_js 又在等 JS 求值,
    互等永不返回,异常捕获救不了)——回调本体必须只做同步判断并立即返回,
    窗口提示经后台线程异步触发(回调返回后 JS 线程空闲,线程外调用安全)。"""
    import types

    fake_guilib = types.ModuleType("webview.guilib")
    fake_winforms = types.ModuleType("webview.platforms.winforms")
    fake_winforms.renderer = "edgechromium"       # 预检通过,进入开窗流程
    fake_guilib.initialize = lambda gui: fake_winforms
    fake = types.ModuleType("webview")
    captured: dict[str, object] = {"handler": None}
    eval_log: list[tuple[str, str]] = []          # (调用时机, 代码)
    phase = {"cur": "before-start"}

    class _FakeClosing:
        def __iadd__(self, handler):
            captured["handler"] = handler
            return self

    class _FakeWindow:
        events = types.SimpleNamespace(closing=_FakeClosing())

        def destroy(self):
            pass

        def evaluate_js(self, code):
            eval_log.append((phase["cur"], code))
            return None

    fake.create_window = lambda *a, **k: _FakeWindow()

    def _fake_start(*a, **k):
        # 模拟 pywebview 阻塞型 closing:在「JS 线程等待回调返回」期间同步派发
        phase["cur"] = "in-closing"
        handler = captured["handler"]
        assert handler is not None, "closing 事件必须已接线"
        blocked = handler()
        phase["cur"] = "after-closing"
        assert blocked is False                   # 忙 → False = 阻止关窗
        return                                    # 正常返回 → main 走正常退出

    fake.start = _fake_start
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setitem(sys.modules, "webview.guilib", fake_guilib)
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: [])
    monkeypatch.setattr(gui_app, "wait_server_ready", lambda port, timeout=10.0: True)

    class _FakeServer:
        should_exit = False

    monkeypatch.setattr(
        gui_app, "_start_server_thread", lambda app, port: (_FakeServer(), None)
    )
    monkeypatch.setattr(gui_app, "should_block_close", lambda store: (True, "有任务正在执行"))

    rc = gui_app.main([])
    assert rc == 0
    # 死锁形态(修复前):evaluate_js 在 closing 回调内被同步调用
    assert all(p != "in-closing" for p, _ in eval_log), eval_log
    # 异步提示最终到达(回调返回后由后台线程触发)
    import time as _t
    for _ in range(60):                            # 至多等 3s
        if any(p == "after-closing" for p, _ in eval_log):
            break
        _t.sleep(0.05)
    assert any(p == "after-closing" and "alert" in code
               for p, code in eval_log), eval_log


# ---- W4 T1:致命错误可见性契约(spec §4.1.2 v3)+ 构建形态标记 ----
import types  # noqa: E402


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


def _fake_app():
    """最小 app 假件:仅需 app.state.jobs(store 单源)。"""
    from migration.gui.server import JobStore

    store = JobStore()
    return types.SimpleNamespace(state=types.SimpleNamespace(jobs=store))


def _record_boxes(monkeypatch) -> list[tuple[str, str]]:
    """接管数据自检与消息框:返回 (标题, 正文) 记录表(致命路径断言用)。"""
    boxes: list[tuple[str, str]] = []
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: [])
    monkeypatch.setattr(gui_app, "_notify_box", lambda t, x: boxes.append((t, x)))
    return boxes


def test_fatal_workdir_error_shows_message_box(monkeypatch):
    """软件目录不可写(WorkdirError)→ 消息框+退出码 2,不得只打印。"""
    from migration.workdir import WorkdirError

    boxes = _record_boxes(monkeypatch)
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
    boxes = _record_boxes(monkeypatch)
    _fake_webview_ok(monkeypatch)
    monkeypatch.setattr(gui_app, "create_app", _fake_app)

    def _boom(app, port):
        raise OSError("10048")

    monkeypatch.setattr(gui_app, "_start_server_thread", _boom)
    assert gui_app.main([]) == 2
    assert boxes and "重试" in boxes[0][1]


def test_fatal_ready_timeout_shows_message_box(monkeypatch):
    """服务就绪超时 → 消息框(含改用浏览器模式指引)+退出码 2。"""
    boxes = _record_boxes(monkeypatch)
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
    """降级浏览器模式自身失败(非零退出)→ 消息框兜底(不静默退出)。"""
    import builtins

    real_import = builtins.__import__

    def _no_webview(name, *a, **k):
        if name == "webview":
            raise ImportError("no webview")
        return real_import(name, *a, **k)

    boxes = _record_boxes(monkeypatch)
    monkeypatch.setattr(builtins, "__import__", _no_webview)
    monkeypatch.setattr("migration.cli._cmd_gui", lambda ns, on_fatal=None: 2)
    assert gui_app.main([]) == 2
    assert boxes and "doctor" in boxes[0][1]


def test_fallback_browser_boxes_real_fatal_reason(monkeypatch):
    """评审④ P2-4:降级浏览器路径的致命错误必须带真实原因(只读目录→移动
    指引),不得只弹 doctor/重新下载通用文案——cli._cmd_gui 经 on_fatal
    回调结构化 what/why,降级壳据此弹窗。"""
    from migration.workdir import WorkdirError

    boxes = _record_boxes(monkeypatch)

    def _boom():
        raise WorkdirError("软件目录不可写", "软件目录不可写,请把 mcmig 移动到可写的文件夹后重试")

    monkeypatch.setattr("migration.cli.resolve_workdir", _boom)
    rc = gui_app._fallback_browser(["--no-browser"])
    assert rc == 2
    assert len(boxes) == 1, "on_fatal 已弹真实原因,不再重复通用兜底"
    assert "移动到可写的文件夹" in boxes[0][1]


def test_form_module_defaults_to_source():
    """_form.FORM 入库默认 source(构建期才改写,spec D7)。"""
    from migration import _form

    assert _form.FORM == "source"


def test_unexpected_fatal_error_shows_message_box(monkeypatch):
    """v3 契约兜底:四分支之外的未预期致命错误(如 create_app 抛非 WorkdirError)
    同样必须消息框+退 2——冻结 noconsole 下异常直冒=静默退出。"""
    boxes = _record_boxes(monkeypatch)
    _fake_webview_ok(monkeypatch)

    def _boom():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(gui_app, "create_app", _boom)
    assert gui_app.main([]) == 2
    assert boxes and "doctor" in boxes[0][1]


def test_data_integrity_failure_box_carries_guidance(monkeypatch):
    """数据自检失败的消息框须含恢复指引(v3:消息框=失败原因+恢复指引)。"""
    boxes: list[tuple[str, str]] = []
    monkeypatch.setattr(gui_app, "_notify_box", lambda t, x: boxes.append((t, x)))
    monkeypatch.setattr(gui_app, "verify_data_integrity", lambda: ["规则数据缺失"])
    assert gui_app.main([]) == 2
    assert boxes and "重新下载" in boxes[0][1]


def test_verify_data_integrity_raising_shows_message_box(monkeypatch):
    """自检函数自身抛错(①区域)也须兜底为消息框,不得静默退出。"""
    boxes = _record_boxes(monkeypatch)

    def _boom():
        raise OSError("hash read failed")

    monkeypatch.setattr(gui_app, "verify_data_integrity", _boom)
    assert gui_app.main([]) == 2
    assert boxes


def test_window_main_honors_port(monkeypatch):
    """T13.5-5:窗口模式 honor --port(此前恒随机空闲端口)。"""
    import sys as _sys

    boxes = _record_boxes(monkeypatch)
    _fake_webview_ok(monkeypatch)

    class _Closing:
        def __iadd__(self, fn):
            return self

    class _Window:
        def __init__(self):
            self.events = type("_E", (), {})()
            self.events.closing = _Closing()

        def destroy(self):
            pass

        def evaluate_js(self, code):
            pass

    _sys.modules["webview"].create_window = lambda *a, **k: _Window()
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
    assert rc == 0 and ports == [8123] and not boxes


def test_main_none_resolves_sys_argv(monkeypatch):
    """评审④ P2-3:公有入口 argv=None 时解析进程实参 sys.argv[1:]——console
    script 与 PyInstaller 薄入口都无参调用 main,修复前 None 被当空列表,
    --port/--no-browser/--help 全部被静默忽略。"""
    seen: list[object] = []
    monkeypatch.setattr(gui_app, "_main_impl", lambda argv: seen.append(argv) or 0)
    monkeypatch.setattr(gui_app.sys, "argv", ["mcmig-gui", "--port", "8123", "--no-browser"])
    rc = gui_app.main()
    assert rc == 0 and seen == [["--port", "8123", "--no-browser"]]


def test_thin_entry_consumes_argv_help_exits_zero():
    """评审④ P2-3 + 评审⑤ P3:真实薄入口(tools/packaging/entry_gui.py)必须消费
    命令行——--help 走 argparse 退出 0(修复前参数被忽略,会尝试真实启动而挂住)。
    子进程输出编码随测试声明:未声明时 Windows 中文环境按 GBK 输出中文帮助文本,
    父进程按 UTF-8 解码必 UnicodeDecodeError(本机全局 PYTHONUTF8 会掩盖该
    环境依赖,评审⑤ 在默认编码机上实测失败)。"""
    import os
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, str(root / "tools" / "packaging" / "entry_gui.py"), "--help"],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0
    assert "usage" in (out.stdout + out.stderr).lower()
