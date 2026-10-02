"""命令行入口:scan / diff / plan / swap / migrate / doctor / gui 子命令(编排逻辑消费 pipeline)。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, doctor
from .classifier import Classifier
from .executor import FileResult
from .fsops import FsOpsError, copy_atomic
from .instlock import InstanceLockError, instance_locks
from .journal import JournalError, JobJournal
from .plan import ActionRecord, Behavior, MigrationPlan, PlanFormatError, PlanPersistError, plan_path
from .pipeline import (
    build_plan,
    execute_migration,
    find_snapshot,
    list_versions,
    run_diff,
    scan_version,
    select_rules_dir,
)
from .preflight import ExecutionDecisions, preflight_execute
from .reporter import DiffReporter, PlanOptions, PlanReporter, ReportOptions
from .review import ReviewStateError, validate_review
from .workdir import WorkdirError, resolve_workdir
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

    p_gui = sub.add_parser("gui", help="启动本地 Web 迁移向导(自动打开浏览器)")
    p_gui.add_argument(
        "--port", type=int, default=None, metavar="N", help="监听端口(默认随机空闲端口)"
    )
    p_gui.add_argument(
        "--no-browser", action="store_true", help="不自动打开浏览器(手动访问打印的地址)"
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
    logging.basicConfig(level=logging.WARNING if quiet else logging.INFO, format="%(message)s")


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
            plan, compat_warnings, pairs = build_plan(
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


def _md5(path: Path) -> str | None:
    """计算文件 MD5(装包阶段同名冲突判定用);不可读返回 None。"""
    from .executor import _md5_of

    return _md5_of(path)


def _swap_preflight(dst_dir: Path, new_pack: Path) -> tuple[str | None, list[str]]:
    """预检:返回 (dst 的 NeoForge 版本或 None 错误描述, 不满足清单)。"""
    from .moddb import check_version_range, read_neoforge_version, scan_mods

    nf = read_neoforge_version(dst_dir)
    if nf is None:
        return ("目标版本缺少 <版本名>.json(无法读取 NeoForge 版本)。"
                "请先用 PCL2 安装目标 NeoForge 版本。"), []
    reg = scan_mods(new_pack)
    bad = []
    for modid in sorted(reg.modids):
        mi = reg.get(modid)
        if mi is None or mi.neoforge_range is None:
            continue
        if not check_version_range(nf, mi.neoforge_range):
            bad.append(f"  {modid}({mi.jar_filename}) 要求 {mi.neoforge_range},目标为 {nf}")
    return None, bad


def _swap_install(
    dst_mods: Path,
    new_mods_dir: Path,
    resolver: Callable[[str], bool],
    dry_run: bool,
) -> tuple[int, int, int]:
    """将新包 mods/*.jar 装入目标 mods/。

    Returns:
        (copied, skipped_identical, conflicted):复制数 / 同名同 MD5 跳过数 /
        同名不同内容经 resolver 决策数(resolver True=覆盖,False=保留目标)。
    """
    copied = skipped = conflicted = 0
    if not new_mods_dir.is_dir():
        return 0, 0, 0
    for jar in sorted(new_mods_dir.glob("*.jar")):
        target = dst_mods / jar.name
        if target.exists():
            if _md5(target) == _md5(jar):
                skipped += 1
                continue
            conflicted += 1
            if not resolver(jar.name):
                continue  # 保留目标
        if not dry_run:
            # 装包覆盖走 fsops 事务复制(tmp+MD5 校验+原子换名);
            # 冲突是否覆盖已由 resolver 决策,无需再备份(backup_dir=None)
            copy_atomic(jar, target, rel=jar.name, backup_dir=None)
        copied += 1
    return copied, skipped, conflicted


def _cmd_swap(args: argparse.Namespace) -> int:
    """swap 子命令(整合包替换):预检→装包→(Task 5 规划)。"""
    from rich.console import Console

    console = Console()
    game_root = _resolve_game_root(args)
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
        # 第一步:预检(NeoForge 兼容)
        err, bad = _swap_preflight(dst_dir, new_pack)
        if err is not None:
            _print(f"[错误] {err}")
            return 2
        # 预检:src 快照必须已存在(规划步依赖;装包前检查,dry-run 同样生效)
        # F8:快照锚定优先+旧 CWD 布局回退(与 diff/plan 同一定位规则)
        src_snap = find_snapshot(game_root / ".mcmig", Path.cwd() / ".mcmig", args.src)[0]
        if not src_snap.exists():
            _print(f"[错误] 缺少源版本快照 {src_snap}")
            _print(f"请先运行: mcmig scan {args.src}")
            return 2
        if bad:
            console.print("[red]以下 mod 与目标 NeoForge 版本不兼容:[/red]")
            for line in bad:
                _print(line)
            if not args.force:
                _print("中止。确认可忽略请加 --force 继续。")
                return 2
            _print("[警告] --force 已指定,忽略上述不兼容继续。")

        # 第二步:装包
        dst_mods = dst_dir / "mods"
        new_names = {p.name for p in (new_pack / "mods").glob("*.jar")}
        existing = {p.name for p in dst_mods.glob("*.jar")} if dst_mods.is_dir() else set()
        extras = sorted(existing - new_names)
        if extras and not args.force:
            _print(f"[警告] 目标 mods/ 存在 {len(extras)} 个不在新包中的 jar,将被新包替换后残留:")
            for name in extras[:10]:
                _print(f"  {name}")
            if len(extras) > 10:
                _print(f"  ... 共 {len(extras)} 个")
            if not Confirm.ask("继续装包?(建议先清理目标 mods/)", default=False):
                _print("已取消。")
                return 0
        elif extras:
            _print(f"[警告] --force:目标 mods/ 有 {len(extras)} 个新包外 jar,保留不动。")

        def resolver(jar_name: str) -> bool:
            """同名冲突决策:默认保留目标(保守)。"""
            return Confirm.ask(f"  {jar_name} 与目标同名但内容不同,覆盖目标?", default=False)

        copied, skipped, conflicted = _swap_install(
            dst_mods, new_pack / "mods", resolver, dry_run=args.dry_run
        )
        _print(
            f"[装包] 复制 {copied} / 相同跳过 {skipped} / 冲突 {conflicted}"
            f"{' (dry-run)' if args.dry_run else ''}"
        )

        # 第三步:重扫 dst(装包刚改写 mods/)→ 规划(modpack_swap 内置)
        if args.dry_run:
            # 彩排模式未真正写盘,规划会基于旧状态误导用户,故跳过规划
            _print("[提示] dry-run 未写盘,跳过规划步骤。去掉 --dry-run 将自动生成迁移计划。")
            return 0
        try:
            plan, compat_warnings, _pairs = build_plan(
                Path.cwd(),
                game_root,
                args.src,
                args.dst,
                modpack_swap=True,
                rescan_dst=True,
                mcmig_dir=Path.cwd() / ".mcmig",
                plans_dir=game_root / ".mcmig" / "plans",
                data_dir=game_root / ".mcmig",
            )
        except (FileNotFoundError, ValueError, PlanPersistError) as e:
            _print(f"[错误] 规划失败: {e}")
            return 2
        for w in compat_warnings:
            _print(f"[兼容警告] {w}")
        # 第四步:摘要 + 下一步提示
        _print("[规划] 迁移计划已生成,按来源分类计数:")
        _print(
            "  " + ", ".join(f"{k}={v}" for k, v in sorted(plan.summary().items()) if v > 0)
        )
        p_path = plan_path(game_root, args.src, args.dst)
        _print(f"审阅 {p_path} 后运行: mcmig migrate {args.src} {args.dst}")
    return 0


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
        # 共享执行预检(批次I-T2):四道防护统一经 preflight_execute 出口;
        # --force 映射为三项显式决策(已执行重跑/接受过期/接受疑似占用),
        # 目录缺失永不可强制(无降级通道);快照/plan 定位语义与下沉前一致
        blockers, warnings = preflight_execute(
            plan, game_root, args.src, args.dst,
            decisions=ExecutionDecisions(rerun_executed=args.force, accept_stale=args.force,
                                         accept_maybe_running=args.force),
            data_dir=data_dir, legacy_dir=cwd / ".mcmig")
        for b in blockers:                       # 不可强制项与可强制项统一经此出口
            _print(f"[错误] {b.message}")
            return 2
        for w in warnings:
            _print_err(f"[警告] {w.message}")
        # 审阅守卫(终审修复 I1,spec §3.3 白名单增补):与 GUI 同源的 validate_review
        # ——实例身份/双侧快照指纹/规则指纹失配即阻断,CLI/GUI 防护对称。路径定位与
        # 签发/preflight 两侧对称:快照取 find_snapshot 的**实际命中位**(锚定优先+
        # 旧布局回退)——build_plan 签发时锚定快照缺失会记录实际载入的旧布局快照哈希
        # (pipeline.issue_review),重验必须取同一命中位,否则旧布局快照用户假阳性
        # snapshot_changed 且「重跑 plan」仍读旧布局,死循环(复审修复);规则=
        # build_plan 当时经 select_rules_dir 选定的目录。legacy 归一化只此一处变量
        # (`cwd/.mcmig ≠ data_dir 才有回退`),与 build_plan 的
        # `legacy = mcmig_dir if data != mcmig_dir else None` 同构,不引入第三种。
        # 旧计划(review=None)跳过,沿用上方「缺少审阅守卫」提示路径(渐进采用)。
        # --force 对齐 accept_stale 决策:快照内容漂移(snapshot_changed)与预检
        # 「快照过期」同源,可随 --force 放行(既有 --force 重跑语义不变);
        # 实例漂移/规则变化无决策通道,恒阻断(重新 plan 即可,保守默认)
        if plan.review is not None:
            legacy_dir = cwd / ".mcmig" if (cwd / ".mcmig") != data_dir else None
            chosen_rules_dir, _rule_notices = select_rules_dir(data_dir, legacy_dir)
            review_blockers = [
                b for b in validate_review(
                    plan,
                    game_root=game_root,
                    snapshot_paths={
                        args.src: find_snapshot(data_dir, legacy_dir, args.src)[0],
                        args.dst: find_snapshot(data_dir, legacy_dir, args.dst)[0],
                    },
                    rule_sources=[chosen_rules_dir / "rules.yaml"],
                )
                if not (args.force and b.code == "snapshot_changed")
            ]
            for b in review_blockers:
                _print(f"[错误] {b.message}")
            if review_blockers:
                return 2
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
        # UTC 时间戳与 pid(文件名升序即时间序;实例锁保证同实例无并发迁移)
        journal: JobJournal | None = None
        if not args.dry_run:
            job_id = (datetime.now(timezone.utc).strftime("cli-%Y%m%dT%H%M%SZ-")
                      + str(os.getpid()))
            journal = JobJournal(data_dir / "jobs", job_id, "migrate")

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
                # 重跑豁免(白名单⑤):--force 即 rerun_executed 决策 → 跳过目标状态校验,
                # 依赖 identical 短路;首跑必须校验(spec §3.3 v4 补注)
                validate_states=not args.force,
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
            # journal ③完成段写失败(W2.5 复审 B7):executor 只吞 before_action 的
            # JournalError,completion 段失败从此处出口——与写失败停发同型:
            # 中止呈现、journal 不收尾(意图已落盘,留给 interrupted 通道「待核对」)、
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
            # 退出中断清单;异常中断(FsOpsError 上抛)不收尾 → interrupted 待核对
            journal.finish()
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
            plan.save(p_path)
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


def _free_port() -> int:
    """让 OS 分配一个空闲 TCP 端口(bind 到端口 0 后读回实际端口)。"""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _cmd_gui(args: argparse.Namespace) -> int:
    """gui 子命令:启动自检 → 起本地服务(127.0.0.1)→ 延时自动开浏览器。

    启动自检(spec §11):①resolve_workdir(目录不可写在起服务前暴露;
    未配置 game_root 自批次I-T1 起为欢迎态,不再拦截,由界面步①引导配置)
    ②verify_data_manifest(杀软误删/传输损坏的规则数据拦截);
    任一失败打印 doctor 引导文案退 2,绝不带病起服务。
    """
    import threading
    import webbrowser

    try:
        wdir = resolve_workdir()
    except WorkdirError as e:
        _print(f"[错误] {e.what}:{e.why}")
        _print("未配置游戏根目录时按上一行指引设置即可;目录不可写等其他环境问题可运行 mcmig doctor 逐项体检。")
        return 2
    findings = doctor.verify_data_manifest()
    if findings:
        _print("[错误] 工具数据清单校验未通过:")
        for f in findings:
            _print(f"  - {f}")
        _print("请重新下载完整发行包覆盖安装;或运行 mcmig doctor 查看详情。")
        return 2
    port = args.port if args.port is not None else _free_port()
    url = f"http://127.0.0.1:{port}"
    if not args.no_browser:
        # 延时 0.8s 再开浏览器:等服务端就绪,避免首刷打不开
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    _print(f"[提示] mcmig 迁移向导已启动: {url}")
    _print("[提示] 使用完毕后关闭本窗口(或按 Ctrl+C)退出。")
    import uvicorn

    from .gui.server import create_app

    # workdir 复用自检结果(create_app 不再二次解析,两次解析可能不一致)
    uvicorn.run(create_app(workdir=wdir), host="127.0.0.1", port=port)
    return 0


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
        if args.command == "gui":
            return _cmd_gui(args)
    except InstanceLockError as e:
        # 批次I-T5:实例锁获取失败(scan/plan/swap/migrate)——另一 mcmig 进程
        # 正在操作源/目标实例;走 stderr 不污染 --json 的 stdout
        _print_err(f"[错误] {e.what}:{e.why}")
        return 2
    build_parser().print_help()
    return 1
