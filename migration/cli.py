"""命令行入口:scan / diff / plan / swap / migrate / doctor / gui 子命令(编排逻辑消费 pipeline)。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, doctor
from .classifier import Classifier
from .executor import FileResult
from .fsops import FsOpsError
from .instlock import InstanceLockError, instance_locks
from .journal import JournalError, JobJournal
from .plan import ActionRecord, Behavior, MigrationPlan, PlanFormatError, PlanPersistError, plan_path
from .pipeline import (
    build_plan,
    execute_migration,
    find_snapshot,
    list_versions,
    precheck_execution,  # 执行前置检查单点(批次I-W3 T1):守卫→预检→状态校验三段合一
    run_diff,
    run_swap,  # 换包全流程编排(批次I-W3 T7):预检→装包→重扫规划
    scan_version,
    select_rules_dir,
    version_name_error,  # 版本名单一路径分量校验(0.12.0 复审#1)
)
from .preflight import ExecutionDecisions
from .reporter import DiffReporter, PlanOptions, PlanReporter, ReportOptions
from .review import ReviewStateError
from .workdir import WorkdirError, resolve_workdir
from collections.abc import Callable

from rich.prompt import Confirm

from .snapshot import snapshot_path

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """构建完整 argparse 解析器。"""
    parser = argparse.ArgumentParser(prog="mcmig", description="Minecraft 整合包版本迁移工具")
    parser.add_argument("-V", "--version", action="version", version=f"mcmig {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--game-root", default=None, help="游戏根目录(含 versions/)")
        p.add_argument(
            "--exclude", action="append", default=[], metavar="GLOB", help="本次按 never"
        )
        p.add_argument(
            "--include", action="append", default=[], metavar="GLOB", help="本次按 must_migrate"
        )
        p.add_argument("--rule", action="append", default=[], metavar="FILE", help="额外规则文件")
        p.add_argument("--strict", action="store_true", help="强制全量哈希")
        p.add_argument("--json", action="store_true", help="JSON 输出")
        p.add_argument("-q", "--quiet", action="store_true")

    p_scan = sub.add_parser("scan", help="扫描版本文件夹生成快照")
    p_scan.add_argument("version", help="versions/ 下的版本文件夹名")
    add_common(p_scan)

    p_diff = sub.add_parser("diff", help="对比两份快照")
    p_diff.add_argument("src", help="源版本名")
    p_diff.add_argument("dst", help="目标版本名")
    p_diff.add_argument("--show-identical", action="store_true")
    p_diff.add_argument("--show-never", action="store_true")
    p_diff.add_argument("--all", action="store_true", help="显示全部桶")
    p_diff.add_argument("--mods", action="store_true", help="仅显示 mods 桶")
    p_diff.add_argument("--category", default=None, help="仅显示指定桶")
    p_diff.add_argument("--modpack-swap", action="store_true",
                        help="换包验收视角:源独有 mod 视为旧包自带,归「换包排除」而非 to_add")
    add_common(p_diff)

    p_plan = sub.add_parser("plan", help="生成迁移计划(只读,产出 action 列表)")
    p_plan.add_argument("src", help="源版本名")
    p_plan.add_argument("dst", help="目标版本名")
    p_plan.add_argument("--game-root", default=None, help="游戏根目录(含 versions/)")
    p_plan.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    p_plan.add_argument("--include", action="append", default=[], metavar="GLOB")
    p_plan.add_argument("--rule", action="append", default=[], metavar="FILE")
    p_plan.add_argument("--show-skip", action="store_true", help="显示 skip 类 origin(never/default_config/identical/mod_shared/mod_target_only/rebuild)")
    p_plan.add_argument("--category", default=None, help="仅显示某 origin")
    p_plan.add_argument("--json", action="store_true")
    p_plan.add_argument("--modpack-swap", action="store_true",
                        help="整合包替换模式:源独有 mod 视为旧包自带,不回迁(用户 rules.yaml 显式 must_migrate 仍放行)")
    p_plan.add_argument("--no-save", action="store_true", help="不持久化 plan 文件")
    p_plan.add_argument("-q", "--quiet", action="store_true")

    p_swap = sub.add_parser("swap", help="整合包替换:预检→装包→生成迁移计划")
    p_swap.add_argument("src", help="源版本名(玩家数据来源)")
    p_swap.add_argument("dst", help="目标版本名(须先用 PCL2 建好)")
    p_swap.add_argument("new_pack", help="新整合包目录(含 mods/ 子目录)")
    p_swap.add_argument("--game-root", default=None, help="游戏根目录(含 versions/)")
    p_swap.add_argument("--dry-run", action="store_true", help="彩排:装包步骤零写盘")
    p_swap.add_argument("--force", action="store_true", help="忽略兼容不满足与目标非空")

    p_mig = sub.add_parser("migrate", help="执行已保存的迁移计划(先 plan 后 migrate)")
    p_mig.add_argument("src", help="源版本名")
    p_mig.add_argument("dst", help="目标版本名")
    p_mig.add_argument("--game-root", default=None, help="游戏根目录(含 versions/)")
    p_mig.add_argument("--dry-run", action="store_true", help="彩排:零写盘")
    p_mig.add_argument("--skip-ask", action="store_true", help="needs_review 全部跳过")
    p_mig.add_argument("--yes-ask", action="store_true", help="needs_review 全部迁移")
    p_mig.add_argument("-y", action="store_true", help="跳过执行前确认")
    p_mig.add_argument(
        "--force", action="store_true",
        help="忽略已执行/快照过期/疑似占用防护(目录缺失不可强制)"
    )

    # doctor 无参数:工作目录按 frozen/兼容模式自动解析
    sub.add_parser("doctor", help="环境体检:数据完整性/配置/权限/磁盘")

    p_upd = sub.add_parser(
        "update", help="检查并下载新版本(下载校验后暂存,给手动替换指引)")
    p_upd.add_argument("--check", action="store_true",
                       help="仅检查是否有新版,不下载")

    p_gui = sub.add_parser("gui", help="启动本地 Web 迁移向导(自动打开浏览器)")
    p_gui.add_argument(
        "--port", type=int, default=None, metavar="N", help="监听端口(默认随机空闲端口)"
    )
    p_gui.add_argument(
        "--no-browser", action="store_true", help="不自动打开浏览器(手动访问打印的地址)"
    )
    p_gui.add_argument(
        "--window",
        action="store_true",
        help="以独立窗口打开(pywebview;未安装/缺 WebView2 运行时时自动回退浏览器模式)",
    )
    return parser


def _safe_reconfigure_streams() -> None:
    """按输出目的地设置编码:真实控制台保原生编码,重定向/管道强制 UTF-8。

    - 控制台(tty):保留原生编码(GBK 控制台中文正常),emoji 降级为 '?'(errors=replace)
    - 重定向/管道(非 tty):强制 UTF-8 —— 机器可读输出(--json 等)跨机消费恒为 UTF-8。
      回归来源:2026-09 服务端语料 diff JSON 在 GBK 控制台重定向后被 GBK 污染(F7)
    - rich 无论走 legacy_windows_render 还是 file.write 路径,最终都经 file.write,
      故在编码层 reconfigure 即可全覆盖。PyInstaller exe 同样适用(sys.stdout 仍为 TextIOWrapper)。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass  # 非 TextIOWrapper 或不支持 reconfigure(如已关闭/重定向到非文本流)


def _setup_logging(quiet: bool) -> None:
    """配置根日志级别,并静音第三方库的请求日志。

    httpx 在 INFO 级记录每次 HTTP 请求(「HTTP Request: GET … 403」英文行),
    对玩家是噪声;失败原因由 updater 的中文三段式文案呈现(K3 真机手测收口)。
    """
    logging.basicConfig(level=logging.WARNING if quiet else logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _try_resolve_game_root(args: argparse.Namespace) -> Path | None:
    """宽容解析游戏根目录(链同 _resolve_game_root,失败返回 None 不退出)。

    diff 用:夹具复放/无 game-root 配置场景退回 CWD-only 查找,保持 0.6.x 行为。
    """
    if args.game_root:
        return Path(args.game_root)
    env = os.environ.get("MCMIG_GAME_ROOT")
    if env:
        return Path(env)
    cfg = Path.cwd() / ".mcmig" / "config.yaml"
    if cfg.is_file():
        import yaml

        doc = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        gr = doc.get("game_root")
        if gr:
            return Path(gr)
    return None


def _resolve_game_root(args: argparse.Namespace) -> Path:
    """解析游戏根目录:--game-root > MCMIG_GAME_ROOT > .mcmig/config.yaml > 报错退出 2。"""
    gr = _try_resolve_game_root(args)
    if gr is not None:
        return gr
    _print(
        "[错误] 未配置游戏根目录。请用 --game-root、设置环境变量 MCMIG_GAME_ROOT、"
        "或在 .mcmig/config.yaml 写 game_root"
    )
    raise SystemExit(2)


from .pipeline import build_ruleset, escape_world_glob  # 批次H 搬迁,历史位置保兼容  # noqa: E402, F401


def _version_dir(game_root: Path, version: str) -> Path:
    return game_root / "versions" / version


def _print(text: str) -> None:
    print(text)


def _print_err(text: str) -> None:
    """stderr 输出(提示/警告类),不污染 --json 的 stdout。"""
    print(text, file=sys.stderr)


def _warn_abandoned_lock(game_root: Path, lockinfo: dict[str, list[str]]) -> None:
    """abandoned 实例锁提示:前持有者异常退出,须核对 journal 中断记录(批次I-T5)。

    W2.5 复审 B7:CLI migrate 与 GUI 均已接 write-ahead journal(批次I-T6+
    本批),jobs/ 中断记录对两条路径皆为真实指引,不再是前向引用占位。
    """
    if lockinfo["abandoned"]:
        _print_err(
            "[警告] 检测到上次异常退出的实例锁,请核对 "
            f"{game_root / '.mcmig' / 'jobs'} 下的中断记录"
        )


def _cmd_scan(args: argparse.Namespace) -> int:
    game_root = _resolve_game_root(args)
    ver_dir = _version_dir(game_root, args.version)
    if not ver_dir.is_dir():
        avail = list_versions(game_root)
        _print(f"[错误] 版本 '{args.version}' 不存在于 {game_root / 'versions'}")
        if avail:
            _print("可用版本: " + ", ".join(avail))
        return 2
    # 批次I-T5(spec §4.2):扫描写快照前获取实例排他锁,封「检查-取锁-写」竞争窗
    # (无争用即时通过,常规路径零延迟;abandoned → 前持有者异常退出提示)
    with instance_locks(game_root, args.version) as lockinfo:
        _warn_abandoned_lock(game_root, lockinfo)
        cwd = Path.cwd()
        data_dir = game_root / ".mcmig"  # 生成物跟实例(F8)
        # W2.5 复审 B3(spec §3.1 T1 全入口):规则目录与 plan/diff 同经
        # select_rules_dir 单点(新位置优先+旧布局只读回退),分类口径不分裂
        legacy = cwd / ".mcmig"
        rules_dir, rule_notices = select_rules_dir(
            data_dir, legacy if legacy != data_dir else None)
        for n in rule_notices:
            _print_err(n)
        # 扫描构建逻辑已下沉 pipeline(快照写锚定 <game_root>/.mcmig/snapshots/,F8);
        # 不可读文件经 on_error 收集,恢复 unreadable 计数(v0 spec §7 报告契约)
        # F34①:先扫描后组规则——build_ruleset 需要快照内探测到的 world_dirs
        # (动态世界层注入;[规则警告] 打印随之移到扫描输出后,stdout 顺序变化是有意为之)
        unreadable: list[str] = []
        snap = scan_version(
            game_root,
            args.version,
            data_dir / "snapshots",
            strict=args.strict,
            on_error=unreadable.append,
        )
        rs, errs = build_ruleset(
            args.version,
            exclude=args.exclude,
            include=args.include,
            rule_files=[Path(f) for f in args.rule],
            mcmig_dir=rules_dir,
            world_dirs=snap.world_dirs,
        )
        for e in errs:
            _print(f"[规则警告] {e}")
        spath = snapshot_path(game_root, args.version)
        clf = Classifier(rs)
        classified = clf.classify_all(snap.files)
        counts: dict[str, int] = {}
        for c in classified:
            counts[c.category.value] = counts.get(c.category.value, 0) + 1
        if args.json:
            import json

            _print(
                json.dumps(
                    {
                        "version": args.version,
                        "file_count": snap.file_count,
                        "by_category": counts,
                        "unreadable": len(unreadable),
                        "snapshot": str(spath),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            _print(f"[完成] 扫描 {args.version}: {snap.file_count} 个文件 → {spath}")
            _print("分类汇总: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
            if unreadable:
                _print(f"[警告] {len(unreadable)} 个文件无法读取(已跳过)")
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    """diff 子命令:参数展开 → pipeline.run_diff → notices 转发与渲染。

    编排逻辑(快照定位/孤儿规则/规则集/Differ/双源配对/身份检测/换包提示)
    已下沉 pipeline.run_diff,本函数只负责参数展开、既有错误文案与结果渲染;
    stderr 提示行(notices)逐行转发,与下沉前 CLI 输出逐字节一致。
    """
    cwd = Path.cwd()
    # F8 锚定:game-root 可解析 → 锚定优先+旧 CWD 布局回退;
    # 不可解析(夹具复放/纯快照对比)→ CWD-only,0.6.x 行为
    game_root = _try_resolve_game_root(args)
    try:
        outcome = run_diff(
            cwd,
            src=args.src,
            dst=args.dst,
            modpack_swap=args.modpack_swap,
            exclude=args.exclude,
            include=args.include,
            rule_files=[Path(f) for f in args.rule],
            game_root=game_root,
        )
    except FileNotFoundError as e:
        # 异常消息形如「缺少 <名[, 名...]> 快照」→ 剥壳还原既有多列友好文案
        names = str(e).removeprefix("缺少 ").removesuffix(" 快照")
        _print("[错误] 缺少快照: " + names)
        _print("请先运行: mcmig scan <版本名>")
        return 2
    except ValueError as e:
        _print(f"[错误] {e}")
        return 2
    for n in outcome.notices:
        _print_err(n)
    reporter = DiffReporter(
        outcome.report, src_version=args.src, dst_version=args.dst,
        mod_pairs=outcome.mod_pairs,
        client_only_paths=outcome.client_only_paths,  # F30① 客户端件标注(空集时输出不变)
    )
    if args.json:
        _print(reporter.to_json())
        return 0
    opts = ReportOptions(
        show_identical=args.show_identical or args.all,
        show_never=args.show_never or args.all,
        mods_only=args.mods,
        category=args.category,
    )
    reporter.render(opts)
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    """plan 子命令:load snapshots → scan mods → orphan rules → diff → plan → 兼容检查 → 渲染。

    编排逻辑已下沉 pipeline.build_plan,本函数只负责参数展开与结果渲染。
    """
    cwd = Path.cwd()
    # F8 锚定:快照预检同 diff 定位规则(锚定优先+旧布局回退);
    # game-root 不可解析时按 0.6.x CWD-only 预检,保持「缺快照先报 scan」的友好顺序
    gr = _try_resolve_game_root(args)
    if gr is not None:
        data_dir = gr / ".mcmig"
        src_path, _ = find_snapshot(data_dir, cwd / ".mcmig", args.src)
        dst_path, _ = find_snapshot(data_dir, cwd / ".mcmig", args.dst)
    else:
        src_path = snapshot_path(cwd, args.src)
        dst_path = snapshot_path(cwd, args.dst)
    missing = [n for n, p in ((args.src, src_path), (args.dst, dst_path)) if not p.exists()]
    if missing:
        _print("[错误] 缺少快照: " + ", ".join(missing))
        _print("请先运行: mcmig scan <版本名>")
        return 2
    game_root = gr if gr is not None else _resolve_game_root(args)
    data_dir = game_root / ".mcmig"
    # 批次I-T5(spec §4.2):生成计划(读双侧快照 → 写 plan 文件)全程持源/目标
    # 实例锁,封「检查-取锁-写」竞争窗(无争用即时通过)
    with instance_locks(game_root, args.src, args.dst) as lockinfo:
        _warn_abandoned_lock(game_root, lockinfo)
        try:
            # 批次I-T1:rules_dir 缺省=data_dir → 用户规则优先读 <game_root>/.mcmig/rules.yaml;
            # mcmig_dir=cwd/.mcmig 仅作旧布局回退(仅旧侧存在时回退读+提示,select_rules_dir)
            # 批次I-W3 T6(白名单⑦):4 元组,extras(审阅摘要)CLI 暂不消费
            plan, compat_warnings, pairs, _extras = build_plan(
                cwd,
                game_root,
                args.src,
                args.dst,
                modpack_swap=args.modpack_swap,
                rescan_dst=False,
                save=not args.no_save,
                mcmig_dir=cwd / ".mcmig",
                plans_dir=data_dir / "plans",
                data_dir=data_dir,
                exclude=args.exclude,
                include=args.include,
                rule_files=[Path(f) for f in args.rule],
            )
        except (FileNotFoundError, ValueError) as e:
            _print(f"[错误] {e}")
            return 2
        except PlanPersistError as e:
            # 白名单②:plan 保存失败不再吞,非零退出(未持久化的计划不可执行,spec §3.3)
            _print(f"[错误] {e}")
            _print("计划未保存,无法进入 migrate;请检查目标目录权限/磁盘空间后重试。")
            return 2
        reporter = PlanReporter(plan, src_version=args.src, dst_version=args.dst, mod_pairs=pairs)
        if args.json:
            _print(reporter.to_json(compat_warnings))
        else:
            reporter.render(PlanOptions(show_skip=args.show_skip, category=args.category))
            reporter.render_compat_warnings(compat_warnings)
    return 0


def _start_swap_progress() -> tuple[object, Callable[[str, int, int], None]]:
    """装包逐 jar 进度行(T13.5):rich Progress(非 tty 自动降级纯文本)。

    Returns:
        (progress 句柄, 回调 ``(jar 名, i, total)``);调用方在 run_swap 结束
        (含异常路径)后调用句柄的 ``stop()``。
    """
    from rich.progress import BarColumn, Progress, TextColumn

    progress = Progress(
        TextColumn("[进度] 装包 {task.completed}/{task.total}"),
        BarColumn(),
        TextColumn("{task.description}"),
        transient=True,
    )
    task = progress.add_task("", total=None)
    progress.start()

    def cb(name: str, i: int, total: int) -> None:
        progress.update(task, completed=i, total=total, description=name)

    return progress, cb


def _cmd_swap(args: argparse.Namespace) -> int:
    """swap 子命令薄壳:参数/输入校验 → 实例锁 → run_swap 编排 → 按 outcome 打印。

    编排逻辑(预检→交互决策→装包→重扫规划)已下沉 pipeline.run_swap(批次I-W3
    T7);本函数只做参数展开、回调内打印(rich 清单+Confirm)与结果渲染——
    事中提示行由回调打印,事后信息全量来自 SwapRunOutcome(评审 v3 契约A)。
    """
    from rich.console import Console

    console = Console()
    game_root = _resolve_game_root(args)
    bad = {v: version_name_error(v) for v in (args.src, args.dst)
           if version_name_error(v) is not None}
    if bad:
        # 版本名单一路径分量校验(0.12.0 复审#1):防绝对路径/穿越形态把
        # 读写面带出 game_root(_version_dir 拼接对绝对路径整体替换)
        for v, why in bad.items():
            _print(f"[错误] 非法版本名 {v!r}:{why}")
        return 2
    src_dir = _version_dir(game_root, args.src)
    dst_dir = _version_dir(game_root, args.dst)
    if not src_dir.is_dir() or not dst_dir.is_dir():
        _print("[错误] 源/目标版本文件夹不存在")
        return 2
    new_pack = Path(args.new_pack)
    if not (new_pack / "mods").is_dir():
        _print(f"[错误] 新整合包目录缺少 mods/ 子目录: {new_pack}")
        return 2

    # 批次I-T5(spec §4.2):预检→装包(写目标 mods/)→重扫规划全程持源/目标
    # 实例锁;装包确认等交互在锁内进行属有意语义(正在操作该实例)
    with instance_locks(game_root, args.src, args.dst) as lockinfo:
        _warn_abandoned_lock(game_root, lockinfo)

        def confirm_extras(extras: list[str]) -> bool:
            """extras 提示行+确认(提示行在回调内打印;拒绝时对话就地收口)。"""
            _print(f"[警告] 目标 mods/ 存在 {len(extras)} 个不在新包中的 jar,将被新包替换后残留:")
            for name in extras[:10]:
                _print(f"  {name}")
            if len(extras) > 10:
                _print(f"  ... 共 {len(extras)} 个")
            if not Confirm.ask("继续装包?(建议先清理目标 mods/)", default=False):
                _print("已取消。")
                return False
            return True

        def resolve_conflict(jar_name: str) -> bool:
            """同名冲突决策:默认保留目标(保守);提示行在回调内打印。"""
            return Confirm.ask(f"  {jar_name} 与目标同名但内容不同,覆盖目标?", default=False)

        progress = None
        progress_cb = None
        if not args.dry_run:
            progress, progress_cb = _start_swap_progress()
        try:
            outcome = run_swap(
                Path.cwd(), game_root, args.src, args.dst, new_pack,
                confirm_extras=confirm_extras, resolve_conflict=resolve_conflict,
                force=args.force, dry_run=args.dry_run,
                progress_cb=progress_cb,
            )
        except JournalError as e:
            # journal 构造失败(装包前,0.12.0 复审#5 后装包段内的 journal 失败
            # 已收敛进 SwapInstallOutcome.error 随返回值呈现,不再上抛到此):
            # 零装包、无部分结果可报,对齐 migrate 的两行式中文引导
            # (traceback 对打包玩家不可读);重跑安全
            _print_err(f"[错误] journal 写入失败,装包中止({e})")
            _print_err(
                "装包进度未知,请核对 journal 中的意图清单后,重新运行 mcmig swap"
                "(已装 jar 会按相同跳过,不会重复复制)"
            )
            return 2
        except FsOpsError as e:
            # 装包段文件操作失败(磁盘不足/目标被占用等,修复 A4):三段式短文案
            # 替代 traceback;装包可能停在任意 jar,重跑安全(相同跳过)
            _print_err(f"[错误] 装包文件操作失败({e.what}: {e.why})")
            _print_err(
                "装包可能停在任意 jar(若已发生同名覆盖,原件在 .mcmig/backups/swap/ 下);"
                "重新运行 mcmig swap 安全(已装 jar 按「相同跳过」,不会重复复制)"
            )
            return 2
        finally:
            if progress is not None:
                progress.stop()   # 进度条收尾(成功/异常路径同规,T13.5)
        # 按结果渲染(文案与下沉前输出逐字对拍;备份位置行为新增,白名单⑥)
        pre = outcome.preflight
        if pre is not None and pre.error is not None:
            _print(f"[错误] {pre.error}")
            return outcome.rc
        if pre is not None and pre.incompat:
            console.print("[red]以下 mod 与目标 NeoForge 版本不兼容:[/red]")
            for line in pre.incompat:
                _print(line)
            if not args.force:
                _print("中止。确认可忽略请加 --force 继续。")
                return outcome.rc
            _print("[警告] --force 已指定,忽略上述不兼容继续。")
        if pre is not None and pre.extras and args.force:
            _print(f"[警告] --force:目标 mods/ 有 {len(pre.extras)} 个新包外 jar,保留不动。")
        inst = outcome.install
        if inst is None:
            # 确认环节拒绝(对话已由回调收口)或预检中止——无装包与规划输出
            return outcome.rc
        _print(
            f"[装包] 复制 {inst.copied} / 相同跳过 {inst.skipped} / 冲突 {inst.conflicted}"
            f"{' (dry-run)' if args.dry_run else ''}"
        )
        if inst.backed_up and inst.backup_dir is not None:
            _print(f"[备份] 被覆盖 jar 已备份至: {inst.backup_dir}")
        if args.dry_run:
            # 彩排模式未真正写盘,规划会基于旧状态误导用户,故跳过规划
            _print("[提示] dry-run 未写盘,跳过规划步骤。去掉 --dry-run 将自动生成迁移计划。")
            return outcome.rc
        if inst.cancelled:
            # CLI 无取消通道,防御分支(部分装包不进规划)
            return outcome.rc
        if inst.error is not None:
            # 装包失败(0.12.0 复审#5/#6:空间预检/中途文件操作)——部分统计
            # 与备份位置必须可见,不可笼统报「规划失败」
            _print(f"[错误] 装包未完成: {inst.error}")
            backup_note = (f"被覆盖原件备份于 {inst.backup_dir}"
                           if inst.backed_up and inst.backup_dir is not None
                           else "未发生覆盖备份")
            _print(
                f"[提示] 已复制 {inst.copied} 个 jar,{backup_note};"
                "解决失败原因后重新运行 mcmig swap 安全"
                "(已装 jar 同名同 MD5 按「相同跳过」,不会重复复制)"
            )
            return outcome.rc
        if outcome.plan_error is not None:
            # 评审 v3 契约B:装包已成功而规划失败——目标 mods/ 已被修改,不可静默
            _print(f"[错误] 规划失败: {outcome.plan_error}")
            _print(
                f"[提示] 装包已完成(复制 {inst.copied}),目标 mods/ 已被修改;"
                f"备份位于 {inst.backup_dir},可从中找回被覆盖 jar"
            )
            return outcome.rc
        for w in outcome.compat_warnings:
            _print(f"[兼容警告] {w}")
        # 摘要 + 下一步提示
        _print("[规划] 迁移计划已生成,按来源分类计数:")
        _print(
            "  "
            + ", ".join(
                f"{k}={v}" for k, v in sorted((outcome.plan_summary or {}).items()) if v > 0
            )
        )
        _print(f"审阅 {outcome.plan_file} 后运行: mcmig migrate {args.src} {args.dst}")
    return outcome.rc


def _cmd_migrate(args: argparse.Namespace) -> int:
    """migrate 子命令:加载 plan → 防护校验 → ASK 预收集 → 确认 → pipeline 执行 → 回写状态 → PCL 提醒。"""
    cwd = Path.cwd()
    game_root = _resolve_game_root(args)
    data_dir = game_root / ".mcmig"
    # 批次I-T5(spec §4.2):加载计划→预检→确认→执行→回写状态全程持源/目标
    # 实例锁——预检在锁内进行,封「检查-取锁-执行」竞争窗;交互确认在锁内
    # 属有意语义(正在操作该实例,他方 mcmig 应等待而非并发写盘)
    with instance_locks(game_root, args.src, args.dst) as lockinfo:
        _warn_abandoned_lock(game_root, lockinfo)
        # F8:plan 文件锚定优先,旧 CWD 布局回退;两侧皆无时取锚定路径报「缺少计划」
        p_anchored = plan_path(game_root, args.src, args.dst)
        p_legacy = plan_path(cwd, args.src, args.dst)
        p_path = (
            p_anchored if p_anchored.exists() else (p_legacy if p_legacy.exists() else p_anchored)
        )
        if not p_path.exists():
            _print(f"[错误] 缺少计划文件 {p_path}")
            _print("请先运行: mcmig plan <源> <目标>")
            return 2
        if p_path == p_legacy and p_legacy != p_anchored:
            # 仅真·旧布局命中才提示(game_root==CWD 时两路径同体,不提示,spec 验收 4)
            _print_err(
                f"[提示] 使用旧布局 plan({p_legacy}),"
                f"建议整体迁移至 {game_root / '.mcmig' / 'plans'}"
            )
        try:
            plan = MigrationPlan.load(p_path)
        except (PlanFormatError, OSError) as e:
            _print(f"[错误] 计划文件读取失败: {e}")
            return 2
        if plan.review is None:
            # 渐进采用(批次I-T3):旧版 plan 无审阅守卫——提示后继续,不阻断既有流程
            _print_err("[提示] 计划缺少审阅守卫(旧版生成),建议重跑 plan 启用保护")
        # 执行前置检查单点(批次I-W3 T1):守卫 validate_review → 共享预检
        # preflight_execute → 首跑审阅状态校验,三段合一经 pipeline.precheck_execution
        # ——与 GUI 平级消费同一实现(行为变更白名单①:多阻断并存时首错优先序
        # 统一为「守卫→预检→状态校验」)。快照/规则定位与签发侧同构:快照取
        # find_snapshot 的**实际命中位**(锚定优先+旧布局回退——锚定快照缺失时
        # build_plan 记录的是实际载入的旧布局快照哈希,重验必须取同一命中位,
        # 否则旧布局快照用户假阳性 snapshot_changed 且「重跑 plan」仍读旧布局,
        # 死循环);规则经 select_rules_dir 同参复算(重验以 review.rule_sources
        # 为权威);--force 映射为三项显式决策(已执行重跑/接受过期/接受疑似
        # 占用)并放行 snapshot_changed(与预检「快照过期」同源);目录缺失永
        # 不可强制;实例漂移/规则变化无决策通道,恒阻断(保守默认)。
        # legacy 归一化单一变量(`cwd/.mcmig ≠ data_dir 才有回退`),与
        # build_plan 的 `legacy = mcmig_dir if data != mcmig_dir else None` 同构,
        # 不引入第三种;该变量同时下传 execute_migration(执行侧兜底校验的快照
        # 回退位)。旧计划(review=None)precheck 跳过守卫/状态段,沿用上方
        # 「缺少审阅守卫」提示路径(渐进采用)。
        legacy_dir = cwd / ".mcmig" if (cwd / ".mcmig") != data_dir else None
        outcome = precheck_execution(
            plan, game_root, args.src, args.dst, legacy_dir=legacy_dir,
            decisions=ExecutionDecisions(rerun_executed=args.force, accept_stale=args.force,
                                         accept_maybe_running=args.force))
        for b in outcome.blockers:               # 不可强制项与可强制项统一经此出口
            _print(f"[错误] {b.message}")
        if outcome.blockers:
            if any(b.code in ("source_state_changed", "target_state_changed",
                              "review_snapshot_missing") for b in outcome.blockers):
                # 状态校验类阻断的既有引导行(与下沉前 execute_migration 异常
                # 路径文案一致;--force 即 rerun 决策可跳过状态校验)
                _print("请重跑 mcmig plan 重新审阅后再执行;确认要按当前状态继续可加 --force。")
            return 2
        for w in outcome.warnings:
            _print_err(f"[警告] {w.message}")
        src_root = _version_dir(game_root, args.src)
        dst_root = _version_dir(game_root, args.dst)
        # ASK 预收集:执行期 ASK 决策 = 路径 ∈ ask_yes(pipeline.execute_migration 消费)
        if args.yes_ask:
            ask_yes: set[str] = {a.path for a in plan.actions if a.behavior == Behavior.ASK}
        else:
            ask_yes = set()
        copy_n = sum(1 for a in plan.actions if a.behavior == Behavior.COPY)
        ask_n = sum(1 for a in plan.actions if a.behavior == Behavior.ASK)
        _print(
            f"将执行: 复制 {copy_n} / 待确认 {ask_n} / 其余跳过"
            f"{' (dry-run)' if args.dry_run else ''}"
        )
        if not args.y and not args.dry_run:
            if not Confirm.ask("确认执行?", default=False):
                _print("已取消。")
                return 0
        if not args.skip_ask and not args.yes_ask:
            # 交互模式:逐文件确认(显示路径与判定原因),先收集决策集合再统一交执行器
            from rich.console import Console

            console = Console()
            for a in plan.actions:
                if a.behavior == Behavior.ASK:
                    console.print(f"  ❓ {a.path} — {a.reason}")
                    if Confirm.ask("  迁移此文件?", default=False):
                        ask_yes.add(a.path)
        # write-ahead journal(W2.5 复审 B7,spec §4.3):CLI 与 GUI 同构的崩溃恢复
        # 黑匣子——三段序 intent→操作→completion,崩溃后经 scan_interrupted 呈现
        # 「待核对」;dry-run 零写盘不建(意图会让待核对清单失真)。job_id 带
        # UTC 时间戳与 pid(文件名升序即时间序;实例锁保证同实例无并发迁移)。
        # 身份字段(批次I-W3 T3):src/dst/game_root 随 start 行落盘,供
        # scan_interrupted/dismiss 的实例锁探针判活(跨进程活性判定)。
        # 构造即追加 start 行(JSONL 增量存储),写失败在此出口(构造点不在
        # 下方 try 内,不拦会以 traceback 逃逸)——与既有 JournalError 呈现同型
        journal: JobJournal | None = None
        if not args.dry_run:
            job_id = (datetime.now(timezone.utc).strftime("cli-%Y%m%dT%H%M%SZ-")
                      + str(os.getpid()))
            try:
                journal = JobJournal(data_dir / "jobs", job_id, "migrate",
                                     src=args.src, dst=args.dst, game_root=str(game_root))
            except JournalError as e:
                _print_err(f"[错误] journal 写入失败,迁移中止({e})")
                _print_err("请核对 journal 中的意图清单后,重新运行 mcmig plan 再执行")
                return 2

        def _journal_before(action: ActionRecord) -> None:
            """①意图(write-ahead):动作动手前持久化(恢复期核对依据)。"""
            assert journal is not None  # 仅 journal 存在时才作为回调注入
            journal.record_intent(
                action.path, {"op": action.behavior.value, "backup": action.backup_target})

        def _journal_after(action: ActionRecord, _result: FileResult) -> None:
            """③完成:动作落盘后持久化(含失败动作——结局已知即收口)。"""
            assert journal is not None
            journal.record_completion(action.path)

        try:
            results = execute_migration(
                plan, src_root, dst_root, ask_yes, dry_run=args.dry_run,
                # 审阅状态校验的执行侧开关(批次I-W3 T1):带守卫的计划已在
                # precheck_execution ③ 段(同一持锁段、同 plan 对象)完成校验,
                # 不再重复双侧快照加载与逐动作比对(与 GUI 同口径);旧版计划
                # (review=None)precheck 跳过 ③ 段,由执行侧内部校验兜底——
                # 行为与下沉前一致。--force 重跑豁免语义不变(白名单⑤:rerun
                # 决策两侧都跳过,依赖 identical 短路,spec §3.3 v4 补注)
                validate_states=plan.review is None and not args.force,
                # 审阅状态校验的快照回退位(W2.6 复审 P1-1):与上方守卫的
                # find_snapshot 同一命中位,旧布局快照下状态校验照常进行
                legacy_dir=legacy_dir,
                before_action=_journal_before if journal is not None else None,
                after_action=_journal_after if journal is not None else None,
            )
        except ReviewStateError as e:
            # 审阅状态校验失配(源/目标与审阅时不一致,零写盘):逐条展示后退出
            for b in e.blockers:
                _print(f"[错误] {b.message}")
            _print("请重跑 mcmig plan 重新审阅后再执行;确认要按当前状态继续可加 --force。")
            return 2
        except FsOpsError as e:
            # 执行段可预期文件操作失败(磁盘不足预检/目标被占用等):
            # 三段式短文案替代 traceback(携带项 a;DiskSpaceError 等均为此族)。
            # 部分执行后的重试路径=重新生成计划(状态校验按新计划重算;直接重跑同
            # 一计划会被目标漂移阻断),identical 跳过仅在重新 plan 后生效(终审修复 I3)
            _print(f"[错误] {e.what}:{e.why}")
            _print(
                "修正问题(关闭占用文件的程序、释放磁盘空间)后,重新运行 mcmig plan 再执行;"
                "重新生成的计划中已完成文件会按「内容一致」跳过,不会重复复制。"
            )
            return 2
        except JournalError as e:
            # journal 收尾段写失败(W2.5 复审 B7;批次I-W3 T3 起 executor 已把
            # 意图/完成段挂点的 JournalError 吞进 journal_failed 停发,此出口
            # 只剩 finish 等收尾路径的异常):与写失败停发同型——中止呈现、
            # journal 不再补写(意图已落盘,留给 interrupted 通道「待核对」)、
            # 不回写 executed_at
            _print_err(f"[错误] journal 写入失败,迁移中止({e})")
            _print_err("请核对 journal 中的意图清单后,重新运行 mcmig plan 再执行")
            return 2
        from collections import Counter

        stat = Counter(r.status for r in results)
        failed = [r for r in results if r.failed]
        _print(f"结果: {dict(stat)};失败 {len(failed)}")
        for r in failed:
            _print(f"  [失败] {r.path}: {r.error}")
        if journal is not None and journal.write_failed:
            # journal 写失败停发(与 GUI 同型安全停止):部分完成不是成功——错误
            # 呈现、journal 不收尾(写已失败)、不回写 executed_at(计划不锁,
            # 修复后可重新 plan 再执行)
            _print_err(f"[错误] journal 写入失败({journal.path}),已停止分发后续动作")
            _print_err("已分发动作的结局以该文件为准;请核对后重新运行 mcmig plan 再执行")
            return 2
        if journal is not None:
            # 收尾标记:完成/部分失败都在安全边界内结束(已分发动作结局已知),
            # 退出中断清单;异常中断(FsOpsError 上抛)不收尾 → interrupted 待核对。
            # 修复 A4:收尾写失败(磁盘满/杀软拦截)不再裸穿 traceback——中文
            # 引导退 2;journal 未收尾,记录留在核对通道
            try:
                journal.finish()
            except JournalError as e:
                _print_err(f"[错误] journal 收尾写入失败,迁移记录未收尾({e})")
                _print_err(
                    "本次迁移的文件操作已执行完毕;该记录会以「待核对」呈现,"
                    "请在 GUI 中断横幅中核对目标实例后清除"
                )
                return 2
        if not args.dry_run and not failed:
            # --force 重跑统计修正:保留首次 executed_at(执行状态的时间锚点),
            # execution_summary 取最新一次(反映当前实例状态;重跑多为全 identical)
            first_executed_at = plan.executed_at
            plan.mark_executed(
                {
                    "copied": stat.get("copied", 0),
                    "identical": stat.get("identical", 0),
                    "asked_no": stat.get("asked_no", 0),
                    "failed": 0,
                }
            )
            if first_executed_at is not None:
                plan.executed_at = first_executed_at
            # 修复 A4:计划回写失败(磁盘满/权限收紧)给三段式短文案,不再
            # traceback——迁移文件已完成,回写失败只影响「已执行」标记
            try:
                plan.save(p_path)
            except (FsOpsError, PlanPersistError) as e:
                detail = f"{e.what}: {e.why}" if isinstance(e, FsOpsError) else str(e)
                _print_err(f"[错误] 计划文件回写失败({detail})")
                _print_err(
                    "迁移文件已完成,但「已执行」标记未写入计划;请检查磁盘空间/权限后重试,"
                    "并手动同步 PCL.ini 与 PCL\\Setup.ini 的版本指向"
                )
                return 2
            _print("[提醒] 迁移完成。若要让启动器默认打开新版本,需同步两处配置:")
            _print(f"  1. {game_root / 'PCL.ini'} 的 Version: 行 → 改为 {args.dst}")
            _print(f"  2. {game_root / 'PCL' / 'Setup.ini'} 的 LaunchVersionSelect: 行 → 改为 {args.dst}")
            _print("工具不代改启动器配置(改错会导致无法启动任何版本),请手动确认后修改。")
        return 1 if failed else 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """doctor 子命令:逐行打印体检结果;全绿退出 0,任一 ❌ 退出 1。"""
    ok, lines = doctor.run_doctor()
    for line in lines:
        _print(line)
    return 0 if ok else 1


# 应用更新三步指引(评审④ P2-2):按安装形态分派——onedir 下载物是含顶层
# mcmig/ 目录的 zip,「替换到所在目录」不足以完成更新;onefile 是替换现有 exe。
_UPDATE_STEPS_ONEFILE = (
    "应用更新三步:①退出当前 mcmig(窗口与命令行);②用本次下载的 exe 替换你现在使用的 "
    "mcmig-gui*.exe(覆盖或改名均可);③重新启动 mcmig。应用后暂存目录可整目录删除。"
)
_UPDATE_STEPS_ONEDIR = (
    "应用更新三步:①退出所有 mcmig 进程(窗口与命令行);②把下载的 zip 解压,用解压出的 "
    "mcmig 文件夹内容覆盖现有 mcmig 程序目录(同名文件覆盖;你自己的 data/config.toml "
    "保留);③重新启动 mcmig。应用后暂存目录可整目录删除。"
)


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
    _print(f"[提示] {_UPDATE_STEPS_ONEDIR if _form.FORM == 'onedir' else _UPDATE_STEPS_ONEFILE}")
    return 0


def _free_port() -> int:
    """让 OS 分配一个空闲 TCP 端口(bind 到端口 0 后读回实际端口)。"""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _cmd_gui(args: argparse.Namespace,
             on_fatal: Callable[[str, str], None] | None = None) -> int:
    """gui 子命令:启动自检 → 起本地服务(127.0.0.1)→ 延时自动开浏览器。

    ``--window`` 时转发独立窗口壳 ``migration.gui.app.main``(spec §10,
    批次I-W3 T9):pywebview 在其内部 lazy import(可选依赖),渲染器预检
    不可用/未安装/启动失败均由该入口自行降级回本函数的浏览器模式。

    启动自检(spec §11):①resolve_workdir(目录不可写在起服务前暴露;
    未配置 game_root 自批次I-T1 起为欢迎态,不再拦截,由界面步①引导配置)
    ②verify_data_manifest(杀软误删/传输损坏的规则数据拦截);
    任一失败打印 doctor 引导文案退 2,绝不带病起服务。

    服务线程异常退出(评审 I2:uvicorn 启动失败以 sys.exit 报告,线程内会被
    threading 静默吞掉)→ 打印中文错误并退 2(不谎报成功);正常运行期由
    ``_serve_until_shutdown`` 消费页面「退出服务」置位后优雅停机,退 0。

    Args:
        args: 已解析的命令行参数。
        on_fatal: 可选致命错误回调(评审④ P2-4)——三个致命分支在打印前回调
            ``(what, why)``;降级浏览器壳(gui.app._fallback_browser)经此把
            真实失败原因弹进消息框(冻结无控制台形态下 print 不可见,只凭
            退出码生成的通用文案会丢失「软件目录不可写→移动指引」等可行动
            原因)。默认 None=纯 CLI 行为不变。

    Returns:
        进程退出码(0=正常/停机;2=自检失败/服务异常退出)。
    """
    if getattr(args, "window", False):
        from .gui.app import main as window_main

        # 剥窗转发(T13.5-5):--port/--no-browser 不得丢——窗口模式 honor 端口,
        # 降级浏览器模式时由 app.main 透传同一 argv 重解析
        fwd: list[str] = []
        if getattr(args, "port", None) is not None:
            fwd += ["--port", str(args.port)]
        if getattr(args, "no_browser", False):
            fwd.append("--no-browser")
        return window_main(fwd)
    import webbrowser

    try:
        wdir = resolve_workdir()
    except WorkdirError as e:
        if on_fatal is not None:
            on_fatal(e.what, e.why)
        _print(f"[错误] {e.what}:{e.why}")
        _print("环境类问题可运行 mcmig doctor 逐项体检。")
        return 2
    findings = doctor.verify_data_manifest()
    if findings:
        if on_fatal is not None:
            on_fatal("工具数据清单校验未通过",
                     "请重新下载完整发行包覆盖安装;或运行 mcmig doctor 查看详情。")
        _print("[错误] 工具数据清单校验未通过:")
        for f in findings:
            _print(f"  - {f}")
        _print("请重新下载完整发行包覆盖安装;或运行 mcmig doctor 查看详情。")
        return 2
    port = args.port if args.port is not None else _free_port()
    url = f"http://127.0.0.1:{port}"
    timer: threading.Timer | None = None
    if not args.no_browser:
        # 延时 0.8s 再开浏览器:等服务端就绪,避免首刷打不开;daemon+失败时
        # cancel(评审二轮 Minor):服务启动失败路径不得再把浏览器指向死服务
        timer = threading.Timer(0.8, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    _print(f"[提示] mcmig 迁移向导已启动: {url}")
    _print("[提示] 使用完毕后可在页面点「退出服务」,或关闭本窗口(按 Ctrl+C)退出。")
    import uvicorn

    from .gui.server import create_app

    # workdir 复用自检结果(create_app 不再二次解析,两次解析可能不一致)
    application = create_app(workdir=wdir)
    # 修复 A5:此前 uvicorn.run 阻塞且全仓无人消费 shutdown_requested——页面
    # 「退出服务」在此模式下是死按钮。改为 Server 实例 + 工作线程,主循环
    # 轮询置位后置 should_exit 优雅停机(端点侧已改经 begin_shutdown 原子排空:
    # 停机后新任务在 JobStore.start 同临界区被拒 → 503)
    server = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=port))
    failure: list[BaseException] = []

    def _run_server() -> None:
        """服务线程体:捕获启动/运行期异常(评审 I2)。

        uvicorn 启动失败(端口占用等)以 ``sys.exit`` 报告,而线程内 SystemExit
        会被 threading **静默吞掉**——不捕获则主循环误判为「正常结束」,谎报
        退出码 0(修复前主线程 uvicorn.run 会以 3 退出)。记录后交主循环判失败。
        """
        try:
            server.run()
        except BaseException as e:  # noqa: BLE001 — 线程边界:记录后交主循环判失败
            failure.append(e)

    worker = threading.Thread(target=_run_server, name="mcmig-uvicorn", daemon=True)
    worker.start()
    _serve_until_shutdown(application, server, worker)
    if failure:
        # SystemExit(3)=uvicorn STARTUP_FAILURE(端口占用/绑定失败);其余为
        # 运行期崩溃(评审二轮 Minor:文案不再把一切归因于端口)
        if timer is not None:
            timer.cancel()  # 启动失败:不把浏览器指向死服务
        if on_fatal is not None:
            on_fatal("本地服务异常退出",
                     f"端口 {port} 上的本地服务异常退出;请检查端口是否被占用,"
                     "或查看日志确认服务内部错误。")
        _print_err(
            f"[错误] 本地服务异常退出(端口 {port});请检查端口是否被占用,"
            "或查看上方日志确认服务内部错误"
        )
        return 2
    return 0


def _serve_until_shutdown(
    application: object, server: object, worker: threading.Thread
) -> None:
    """主循环等待服务线程结束:页面「退出服务」置位后引导 uvicorn 优雅停机(修复 A5)。

    0.5s 轮询 ``application.state.shutdown_requested``(POST /api/shutdown
    经 JobStore.begin_shutdown 原子排空后置位)→ 置 ``server.should_exit``;
    Ctrl+C 同样收敛为优雅停机(uvicorn 的信号处理只在主线程安装,工作线程
    形态下 KeyboardInterrupt 落到本循环)。参数走鸭子类型便于测试注入替身。

    Args:
        application: FastAPI 应用(读 ``state.shutdown_requested``)。
        server: uvicorn.Server 实例(写 ``should_exit``)。
        worker: 运行 ``server.run`` 的服务线程。
    """
    try:
        while worker.is_alive():
            if getattr(getattr(application, "state", None), "shutdown_requested", False):
                server.should_exit = True
            worker.join(timeout=0.5)
    except KeyboardInterrupt:
        server.should_exit = True
        worker.join(timeout=5)


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""
    _safe_reconfigure_streams()
    args = build_parser().parse_args(argv)
    _setup_logging(getattr(args, "quiet", False))
    try:
        if args.command == "scan":
            return _cmd_scan(args)
        if args.command == "diff":
            return _cmd_diff(args)
        if args.command == "plan":
            return _cmd_plan(args)
        if args.command == "swap":
            return _cmd_swap(args)
        if args.command == "migrate":
            return _cmd_migrate(args)
        if args.command == "doctor":
            return _cmd_doctor(args)
        if args.command == "update":
            return _cmd_update(args)
        if args.command == "gui":
            return _cmd_gui(args)
    except InstanceLockError as e:
        # 批次I-T5:实例锁获取失败(scan/plan/swap/migrate)——另一 mcmig 进程
        # 正在操作源/目标实例;走 stderr 不污染 --json 的 stdout
        _print_err(f"[错误] {e.what}:{e.why}")
        return 2
    build_parser().print_help()
    return 1
