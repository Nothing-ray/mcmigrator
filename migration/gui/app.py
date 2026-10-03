"""pywebview 独立窗口壳(spec §10/批次I-W3 T9):uvicorn 线程+webview 窗口+
渲染器预检+关闭边界+停机接线+降级。

职责边界:
- 本模块只做「壳」:把 ``create_app()`` 的 FastAPI 应用包进本地线程、以
  pywebview 窗口打开界面,并处理全部失败路径(pywebview 未安装/WebView2
  运行时缺失/webview.start 抛错)——每条路径都有出路:降级浏览器模式
  (lazy 取 ``cli._cmd_gui``)或中文报错退 2,finally 恒回收服务线程。
- pywebview 为**可选依赖**:``import webview`` 一律函数内 lazy;核心模块
  与测试均不要求其安装(测试经 sys.modules 注入假 webview 模块)。
- JobStore 单源(评审 v3 P2-5):``store = app.state.jobs``(T7 暴露)——
  关闭守卫(should_block_close)/停机轮询/HTTP 服务共用同一仓库实例。
"""

from __future__ import annotations

import http.client
import json
import logging
import socket
import sys
import threading
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

from fastapi import FastAPI

from ..doctor import verify_data_manifest
from ..workdir import WorkdirError
from .server import JobStore, create_app

if TYPE_CHECKING:
    # 仅为类型标注服务;运行时 uvicorn 在 _start_server_thread 内 lazy import
    import uvicorn

log = logging.getLogger(__name__)

# 服务线程 join 超时(秒):uvicorn 收到 should_exit 后的正常收尾时限
_JOIN_TIMEOUT_SECONDS = 5.0

# 渲染器不可用(缺 WebView2 运行时/预检异常)时的统一降级提示
_WEBVIEW2_HINT = (
    "[提示] 当前环境缺少可用的现代窗口渲染器(WebView2/EdgeChromium)。"
    "可安装 WebView2 运行时: https://developer.microsoft.com/microsoft-edge/webview2/"
    " 或改用 mcmig gui(浏览器模式)"
)


def _print(text: str) -> None:
    """用户可见提示行走 stdout(与 cli 同约定;技术细节走 logging)。"""
    print(text)


def pick_free_port() -> int:
    """让 OS 在 127.0.0.1 上分配一个空闲 TCP 端口(bind 0 后读回实际端口)。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def build_url(port: int) -> str:
    """构造向导页地址(窗口加载与降级提示共用)。"""
    return f"http://127.0.0.1:{port}/"


def _manifest_errors() -> list[str]:
    """数据清单自检(doctor 同源:manifest.sha256 逐项核对)的薄封装。

    校验逻辑单点在 ``doctor.verify_data_manifest``(cli ``_cmd_gui`` 的启动
    自检与 ``mcmig doctor`` 均消费同一实现);此处再包一层命名函数,供
    ``verify_data_integrity`` 与测试 monkeypatch 共用同一注入点。
    """
    return verify_data_manifest()


def verify_data_integrity() -> list[str]:
    """窗口入口的数据清单自检(评审 P2-7):复用既有 manifest 校验路径。

    Returns:
        中文错误行列表(空列表=完好);非空即弹窗+退出码 2。
    """
    return _manifest_errors()


def selected_renderer(gui: str) -> str:
    """渲染器预检(评审 v3 P2-4+v4 P1):返回 pywebview 实际选中的渲染器名。

    pywebview 在 Windows 上无论 ``gui=`` 传什么都 import winforms 平台模块,
    edgechromium/mshtml 的选择发生在模块内——缺 WebView2 运行时时静默回落
    MSHTML 且只发 log.warning 不抛异常,故 ``webview.start(gui="edgechromium")``
    不足以保证现代渲染器,必须预检。

    导入形态必须是 ``from webview.guilib import initialize``——不能用
    ``from webview import guilib``:webview/__init__.py 模块级 ``guilib = None``
    (仅 start() 内赋值),包属性首启必为 None → AttributeError;子模块函数
    才是真实接口(__init__.py 自身同款用法)。``initialize(gui)`` 返回平台
    模块,取模块级 ``renderer`` 属性('edgechromium'/'mshtml'/'cef';非
    Windows 平台无该属性,回落 gui 名)。initialize 可重复调用(模块导入
    缓存+幂等守卫),start() 内部再调一次无害。内部 API 风险由
    ``pywebview>=5.0`` 钉版承担。

    Args:
        gui: 期望的渲染器名(本壳恒传 "edgechromium")。

    Returns:
        实际选中的渲染器名(取不到 renderer 属性时回落 gui 名)。

    Raises:
        Exception: 预检自身失败(如缺 pythonnet → WebViewException)——
            由调用方(main ③)以 try/except 包住按「渲染器不可用」降级,
            绝不裸抛。
    """
    from webview.guilib import initialize

    mod = initialize(gui)
    return str(getattr(mod, "renderer", gui))


def wait_server_ready(port: int, timeout: float = 10.0) -> bool:
    """轮询 GET /api/config 至 200(开窗前就绪探测,评审 P2-7)。

    避免窗口先于服务打开时的加载错误页;网络/协议异常一律按未就绪处理,
    超时返回 False(调用方按启动失败处理)。

    Args:
        port: 本地服务端口。
        timeout: 总超时秒数。

    Returns:
        服务应答 200 → True;超时 → False。
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            # host/port/timeout 三参显式(评审 v3 P2-5:HTTPConnection 首位
            # 形参是 host,传 port 会错位)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
            try:
                conn.request("GET", "/api/config")
                resp = conn.getresponse()
                ok = resp.status == 200
                resp.read()
            finally:
                conn.close()
            if ok:
                return True
        except (OSError, http.client.HTTPException):
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def should_block_close(store: JobStore) -> tuple[bool, str | None]:
    """窗口关闭边界(评审 v3 P2-5 原子化):直调 ``store.begin_shutdown()``。

    锁内一次完成「检查空闲 + 置排空」:busy → (True, 当前 job 描述)且状态
    不变(窗口停留);空闲 → 置 draining 并 (False, None)——「检查空闲」
    与「拒绝新任务」同一临界区,封死「检查为空闲 → 新迁移启动 → 窗口退出」
    竞争窗(此后 start() 在同临界区抛 StoreDraining → 端点 503)。

    Args:
        store: create_app 暴露的 JobStore(app.state.jobs 单源)。

    Returns:
        (是否阻止关闭, 阻止时的当前任务描述或 None)。
    """
    return store.begin_shutdown()


def _start_server_thread(app: FastAPI, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    """在后台线程启动 uvicorn 服务(模块级可注入原语,daemon=False)。

    uvicorn.Config 直接吃 app 实例——关闭守卫取的 store(app.state.jobs)
    与 HTTP 服务同一 FastAPI 应用(评审 v3 P2-5);测试 monkeypatch 本函数
    注入假 server/thread 断言回收。

    Args:
        app: create_app() 构建的 FastAPI 应用。
        port: 本地监听端口(127.0.0.1)。

    Returns:
        (uvicorn.Server, 已启动的服务线程)。
    """
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="mcmig-gui-server", daemon=False)
    thread.start()
    return server, thread


def _stop_server(server: uvicorn.Server) -> None:
    """请求 uvicorn 服务线程退出(置 should_exit;run() 收尾后线程自然结束)。"""
    server.should_exit = True


def _notify_box(title: str, text: str) -> None:
    """Windows 原生消息框(阻塞至用户确认);其他平台/失败时静默(控制台兜底)。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # 0x10 = MB_ICONERROR
    except (AttributeError, OSError):
        pass


def _fallback_browser(argv: Sequence[str] | None) -> int:
    """降级浏览器模式:转发 ``cli._cmd_gui``(uvicorn 起停由其自理)。

    import 必须在**函数体内** lazy 取——monkeypatch ``migration.cli._cmd_gui``
    才能生效(模块顶层 import 会绑死早期引用);argv 经 cli 解析器规整为
    Namespace 后透传,未识别参数沿用 argparse 语义退出。

    致命错误可见性(spec §4.1.2 v3 + 评审④ P2-4):降级路径自身的失败(抛错或
    非零退出)必须弹中文消息框;且**优先呈现真实失败原因**——cli._cmd_gui 的
    已知致命分支经 ``on_fatal`` 回调结构化 what/why(冻结无控制台形态下 print
    进 devnull,仅凭退出码生成的通用「doctor/重新下载」文案会丢失「软件目录
    不可写→移动指引」等可行动原因);回调未触发而非零退出时才用通用兜底。
    """
    from ..cli import _cmd_gui, build_parser

    ns = build_parser().parse_args(["gui", *(argv if argv is not None else ())])
    fatal: list[tuple[str, str]] = []

    def _on_fatal(what: str, why: str) -> None:
        fatal.append((what, why))
        _notify_box("mcmig 启动失败", f"{what}:{why}")

    try:
        rc = _cmd_gui(ns, on_fatal=_on_fatal)
    except Exception:  # noqa: BLE001 — 降级路径致命失败兜底(消息框可见,非静默)
        log.exception("浏览器降级模式启动失败")
        _notify_box(
            "mcmig 启动失败",
            "浏览器模式启动失败。\n请运行 mcmig doctor 自检,或到项目发布页重新下载完整发行包。",
        )
        return 2
    if rc != 0 and not fatal:
        _notify_box(
            "mcmig 启动失败",
            "浏览器模式未能启动。\n请运行 mcmig doctor 自检,或到项目发布页重新下载完整发行包。",
        )
    return rc


def main(argv: Sequence[str] | None = None) -> int:
    """独立窗口入口(spec §10):自检 → 预检 → 起服务 → 开窗 → 收尾回收。

    序列:
    ① ``verify_data_integrity`` 非空 → 打印+消息框+return 2(缺 Python 包
       以外的第一道失败防护,评审 P2-7);
    ② ``import webview`` 失败 → 打印安装指引并降级浏览器模式;
    ③ 渲染器预检 ``selected_renderer("edgechromium")``(评审 v3 P2-4+v4 P1,
       **先于服务线程**免「起即拆」):函数自身可能抛异常(缺 pythonnet 等)
       或返回值 ≠ edgechromium(WebView2 缺失时 pywebview 的静默 MSHTML
       回退形态)——同走降级分支,不起服务线程、不进 webview.start;
    ④ ``create_app()`` → ``store = app.state.jobs``(单源)→ 起服务线程 →
       ``wait_server_ready`` 超时 → 停线程+return 2;
    ⑤ 就绪后 create_window 并接线 closing → ``should_block_close``(忙 →
       返回 False 阻止关闭并提示;闲 → 放行且仓库进入排空态);
    ⑥ 停机轮询线程 0.5s 检查 ``app.state.shutdown_requested``(页面「退出
       服务」)→ ``window.destroy()``;
    ⑦ ``webview.start(gui="edgechromium")`` 抛错为**二道防线**(EdgeChrome
       初始化失败仍可能在 start 期抛错)→ 停线程+提示+降级浏览器模式;
    ⑧ 退出码:正常 0;自检失败/服务启动失败/就绪超时 2(中文报错**+消息框**,
       spec §4.1.2 v3 契约:冻结无控制台形态下 print 不可见);finally
       恒置 ``should_exit`` 并 join 服务线程(所有异常路径回收,不留僵尸);
    ⑨ 兜底(v3 契约):以上四分支之外的**任何未预期异常**(如 create_window
       抛错/端口分配失败/自检函数自身抛错)统一消息框+退 2——冻结 noconsole
       下裸异常=静默退出,可见性兜底是本契约存在的意义。

    Args:
        argv: 命令行参数(缺省 None);窗口模式解析 ``--port``/``--no-browser``
            (T13.5-5),降级浏览器模式时透传给 cli 解析器。None 时解析进程
            实参 ``sys.argv[1:]``(评审④ P2-3:console script 与 PyInstaller
            薄入口均无参调用 main——修复前 None 被当空列表,真实入口的
            --port/--no-browser/--help 全部被静默忽略)。

    Returns:
        进程退出码(0/2;降级路径返回浏览器模式自身的退出码)。
    """
    if argv is None:
        argv = list(sys.argv[1:])
    try:
        return _main_impl(argv)
    except Exception:  # noqa: BLE001 — 页面可用前未预期致命错误的可见性兜底(v3 契约)
        log.exception("窗口启动未预期错误")
        _notify_box(
            "mcmig 启动失败",
            "启动过程中发生未预期错误。\n请运行 mcmig doctor 自检,或到项目发布页重新下载完整发行包。",
        )
        return 2


def _main_impl(argv: Sequence[str] | None) -> int:
    """``main`` 的实际序列(已知失败路径各自处理;未预期异常由 ``main`` 兜底)。"""
    from ..cli import build_parser

    # argv 解析(T13.5-5):窗口模式 honor --port/--no-browser;未知参数沿
    # argparse 语义退出(打印用法)。降级浏览器模式仍透传原 argv 重解析
    ns = build_parser().parse_args(["gui", *(argv if argv is not None else ())])
    port = int(ns.port) if getattr(ns, "port", None) is not None else pick_free_port()
    # ① 数据清单自检(评审 P2-7)
    errors = verify_data_integrity()
    if errors:
        _print("[错误] 工具数据清单校验未通过:")
        for line in errors:
            _print(f"  - {line}")
        _print("请重新下载完整发行包覆盖安装;或运行 mcmig doctor 查看详情。")
        _notify_box(
            "mcmig 数据自检失败",
            "\n".join(errors) + "\n请重新下载完整发行包覆盖安装;或运行 mcmig doctor 查看详情。",
        )
        return 2
    # ② pywebview 可导入性(函数内 lazy import:可选依赖不进核心)
    try:
        import webview
    except ImportError:
        _print(
            "[提示] 未安装 pywebview,已回退浏览器模式: "
            "pip install pywebview(https://pywebview.flowrl.com)或使用 mcmig gui"
        )
        return _fallback_browser(argv)
    # ③ 渲染器预检(先于服务线程,评审 v3 P2-4+v4 P1)
    try:
        renderer = selected_renderer("edgechromium")
    except Exception as e:  # noqa: BLE001 — 预检可抛(缺 pythonnet 等),一律降级
        log.warning("渲染器预检抛异常,降级浏览器模式: %r", e)
        _print(_WEBVIEW2_HINT)
        return _fallback_browser(argv)
    if renderer != "edgechromium":
        log.warning("实际渲染器为 %s(非 edgechromium,WebView2 运行时缺失?),降级浏览器模式", renderer)
        _print(_WEBVIEW2_HINT)
        return _fallback_browser(argv)
    # ④ 起服务线程(store 单源:app.state.jobs,评审 v3 P2-5)
    try:
        app = create_app()
    except WorkdirError as e:
        _print(f"[错误] {e.what}:{e.why}")
        _notify_box("mcmig 启动失败", f"{e.what}:{e.why}")
        return 2
    store: JobStore = app.state.jobs
    _print(f"[提示] mcmig 窗口模式服务已启动: {build_url(port)}")
    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None
    stop_polling = threading.Event()
    start_failure: Exception | None = None
    try:
        try:
            server, thread = _start_server_thread(app, port)
        except OSError as e:
            _print(f"[错误] 本地服务启动失败(端口 {port}):{e}")
            _notify_box(
                "mcmig 启动失败",
                f"本地服务启动失败(端口 {port}):{e}\n请重试 mcmig-gui,或改用 mcmig gui(浏览器模式)。",
            )
            return 2
        # 就绪探测(评审 P2-7):服务应答 200 才开窗,避免先于服务打开的加载错误页
        if not wait_server_ready(port):
            _print("[错误] 本地服务就绪超时,无法打开窗口。请重试 mcmig-gui,或改用 mcmig gui(浏览器模式)。")
            _notify_box(
                "mcmig 启动失败",
                "本地服务就绪超时,无法打开窗口。\n请重试 mcmig-gui,或改用 mcmig gui(浏览器模式)。",
            )
            return 2
        # ⑤ 开窗 + 关闭边界接线
        window = webview.create_window("mcmig 迁移向导", build_url(port))

        def _on_closing() -> bool:
            """closing 事件:忙 → 返回 False 阻止关闭并提示;闲 → 放行(置排空)。

            回调本体只做同步判断并立即返回(0.12.0 复审#2):pywebview 的
            closing 是阻塞型事件——JS 线程正等待本回调返回,此时同步
            ``evaluate_js`` 又在等 JS 求值,互等永不返回(上游 issue #1699,
            异常捕获救不了「不返回」)。窗口提示改由后台线程延迟触发:回调
            返回后 JS 线程空闲,线程外调用 evaluate_js 是 pywebview 的文档
            化用法;页面未就绪时仅放弃提示,控制台输出已有兜底。
            """
            blocked, desc = should_block_close(store)
            if blocked and desc:
                _print(f"[提示] {desc},窗口暂不关闭;请等待任务完成(或取消)后再退出。")
                code = f"alert({json.dumps(desc, ensure_ascii=False)});"

                def _notify() -> None:
                    try:
                        window.evaluate_js(code)
                    except Exception:  # noqa: BLE001 — 窗口已关/页面未就绪:尽力而为
                        pass

                timer = threading.Timer(0.2, _notify)
                timer.daemon = True    # 不阻断进程退出
                timer.start()
            return not blocked

        window.events.closing += _on_closing

        # ⑥ 停机轮询:页面「退出服务」置 shutdown_requested → 关窗结束 start()
        def _poll_shutdown() -> None:
            """0.5s 间隔轮询停机标志;置位即 destroy 窗口并退出轮询。"""
            while not stop_polling.wait(0.5):
                if getattr(app.state, "shutdown_requested", False):
                    window.destroy()
                    return

        threading.Thread(
            target=_poll_shutdown, name="mcmig-gui-shutdown-poll", daemon=True
        ).start()
        # ⑦ webview.start(渲染器预检已过,此为显式声明+EdgeChrome 初始化
        # 失败仍可能抛错的二道防线)
        try:
            webview.start(gui="edgechromium")
        except Exception as e:  # noqa: BLE001 — 启动期任何异常都降级,不留僵尸线程
            start_failure = e
    finally:
        stop_polling.set()
        if server is not None:
            _stop_server(server)
        if thread is not None and thread.is_alive():
            thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
    if start_failure is not None:
        log.warning("webview.start 失败,降级浏览器模式: %r", start_failure)
        _print("[提示] 窗口启动失败,后台服务已停止,已回退浏览器模式。")
        _print(_WEBVIEW2_HINT)
        return _fallback_browser(argv)
    # ⑧ 正常退出:窗口已关(closing 已置排空),服务线程由 finally 回收
    return 0
