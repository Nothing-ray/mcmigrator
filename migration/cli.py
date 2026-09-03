"""命令行入口:scan / diff / plan / swap / migrate / doctor 子命令(编排逻辑消费 pipeline)。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from . import __version__, rules
from .classifier import Classifier
from .differ import Differ
from .doctor import run_doctor
from .fsops import copy_atomic
from .plan import Behavior, MigrationPlan, PlanFormatError, plan_path
from .pipeline import build_plan, execute_migration, scan_version
from .reporter import DiffReporter, PlanOptions, PlanReporter, ReportOptions
from rich.prompt import Confirm

from .snapshot import Snapshot, snapshot_path


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
    p_mig.add_argument("--force", action="store_true", help="忽略已执行/过期防护")

    # doctor 无参数:工作目录按 frozen/兼容模式自动解析
    sub.add_parser("doctor", help="环境体检:数据完整性/配置/权限/磁盘")
    return parser


def _safe_reconfigure_streams() -> None:
    """将 stdout/stderr 错误处理改为 replace,避免 GBK 控制台 emoji 崩溃。

    保留控制台原生编码(gbk/utf-8 自适应):中文始终正常,emoji 降级为 '?'。
    rich 无论走 legacy_windows_render 还是 file.write 路径,最终都经 file.write,
    故在编码层 reconfigure 即可全覆盖。PyInstaller exe 同样适用(sys.stdout 仍为 TextIOWrapper)。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass  # 非 TextIOWrapper 或不支持 reconfigure(如已关闭/重定向到非文本流)


def _setup_logging(quiet: bool) -> None:
    logging.basicConfig(level=logging.WARNING if quiet else logging.INFO, format="%(message)s")


def _resolve_game_root(args: argparse.Namespace) -> Path:
    """解析游戏根目录:--game-root > MCMIG_GAME_ROOT > .mcmig/config.yaml > 报错退出 2。"""
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
    _print(
        "[错误] 未配置游戏根目录。请用 --game-root、设置环境变量 MCMIG_GAME_ROOT、"
        "或在 .mcmig/config.yaml 写 game_root"
    )
    raise SystemExit(2)


def build_ruleset(
    versions: str | list[str],
    *,
    exclude: list[str],
    include: list[str],
    rule_files: list[Path],
    mcmig_dir: Path,
    with_whitelist: bool = False,
    orphan_rules: list[rules.Rule] | None = None,
) -> tuple[rules.RuleSet, list[str]]:
    """按优先级(CLI > extra > user > ORPHAN > REBUILD > whitelist > default)组装 RuleSet。

    纯参数签名(不依赖 argparse.Namespace):CLI 从 args 展开传参,
    pipeline.build_plan 直调亦可(GUI 复用)。

    Args:
        versions: 参与判定的版本名(展开 default 规则中的版本占位)。
        exclude: CLI 级临时规则 glob(本次按 never,对应 --exclude)。
        include: CLI 级临时规则 glob(本次按 must_migrate,对应 --include)。
        rule_files: 额外规则文件路径列表(对应 --rule)。
        mcmig_dir: .mcmig 目录(user rules.yaml 所在)。
        with_whitelist: 是否启用 whitelist 层(仅 plan 命令)。
        orphan_rules: orphan 规则(仅 plan 命令,plan-only)。

    Returns:
        (规则集, 规则加载警告列表)。

    rebuild 层对所有命令(scan/diff/plan)常开;whitelist 仅 plan 命令启用;
    orphan 规则仅 plan 命令启用(plan-only)。
    """
    from importlib import resources

    cli_rules = rules.load_cli_rules(exclude, include)
    extra: list[rules.Rule] = []
    errors: list[str] = []
    for f in rule_files:
        r, e = rules.load_user_rules(f)
        extra.extend(r)
        errors.extend(e)
    user_path = mcmig_dir / "rules.yaml"
    user, ue = rules.load_user_rules(user_path)
    errors.extend(ue)
    orphan = orphan_rules or []
    # rebuild 层:常开(scan/diff/plan 都需正确识别版本敏感文件)
    rb_text = resources.files("migration").joinpath("data/rebuild.yaml").read_text(encoding="utf-8")
    rebuild, rbe = rules.load_rebuild_rules_from_text(rb_text, "rebuild.yaml")
    errors.extend(rbe)
    whitelist: list[rules.Rule] = []
    if with_whitelist:
        wl_text = resources.files("migration").joinpath("data/whitelist.yaml").read_text(encoding="utf-8")
        whitelist, we = rules.load_whitelist_rules_from_text(wl_text, "whitelist.yaml")
        errors.extend(we)
    default, de = rules.load_default_rules(versions)
    errors.extend(de)
    rs = rules.RuleSet.from_layers(cli_rules, extra, user, orphan, rebuild, whitelist, default)
    return rs, errors


def _version_dir(game_root: Path, version: str) -> Path:
    return game_root / "versions" / version


def _list_versions(game_root: Path) -> list[str]:
    vdir = game_root / "versions"
    if not vdir.is_dir():
        return []
    return sorted(p.name for p in vdir.iterdir() if p.is_dir())


def _print(text: str) -> None:
    print(text)


def _cmd_scan(args: argparse.Namespace) -> int:
    game_root = _resolve_game_root(args)
    ver_dir = _version_dir(game_root, args.version)
    if not ver_dir.is_dir():
        avail = _list_versions(game_root)
        _print(f"[错误] 版本 '{args.version}' 不存在于 {game_root / 'versions'}")
        if avail:
            _print("可用版本: " + ", ".join(avail))
        return 2
    cwd = Path.cwd()
    mcmig_dir = cwd / ".mcmig"
    rs, errs = build_ruleset(
        args.version,
        exclude=args.exclude,
        include=args.include,
        rule_files=[Path(f) for f in args.rule],
        mcmig_dir=mcmig_dir,
    )
    for e in errs:
        _print(f"[规则警告] {e}")
    # 扫描构建逻辑已下沉 pipeline(快照仍写 .mcmig/snapshots/,与 snapshot_path 同构);
    # 不可读文件经 on_error 收集,恢复 unreadable 计数(v0 spec §7 报告契约)
    unreadable: list[str] = []
    snap = scan_version(
        game_root,
        args.version,
        mcmig_dir / "snapshots",
        strict=args.strict,
        on_error=unreadable.append,
    )
    spath = snapshot_path(cwd, args.version)
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
    cwd = Path.cwd()
    src_path = snapshot_path(cwd, args.src)
    dst_path = snapshot_path(cwd, args.dst)
    missing = [n for n, p in ((args.src, src_path), (args.dst, dst_path)) if not p.exists()]
    if missing:
        _print("[错误] 缺少快照: " + ", ".join(missing))
        _print("请先运行: mcmig scan <版本名>")
        return 2
    try:
        src = Snapshot.load(src_path)
        dst = Snapshot.load(dst_path)
    except Exception as e:  # noqa: BLE001
        _print(f"[错误] 快照读取失败: {e}")
        return 2
    mcmig_dir = cwd / ".mcmig"
    rs, errs = build_ruleset(
        [args.src, args.dst],
        exclude=args.exclude,
        include=args.include,
        rule_files=[Path(f) for f in args.rule],
        mcmig_dir=mcmig_dir,
    )
    for e in errs:
        _print(f"[规则警告] {e}")
    clf = Classifier(rs)
    report = Differ(src.files, dst.files, clf).diff()
    reporter = DiffReporter(report, src_version=args.src, dst_version=args.dst)
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
    src_path = snapshot_path(cwd, args.src)
    dst_path = snapshot_path(cwd, args.dst)
    missing = [n for n, p in ((args.src, src_path), (args.dst, dst_path)) if not p.exists()]
    if missing:
        _print("[错误] 缺少快照: " + ", ".join(missing))
        _print("请先运行: mcmig scan <版本名>")
        return 2
    game_root = _resolve_game_root(args)
    try:
        plan, compat_warnings = build_plan(
            cwd,
            game_root,
            args.src,
            args.dst,
            modpack_swap=args.modpack_swap,
            rescan_dst=False,
            save=not args.no_save,
            mcmig_dir=cwd / ".mcmig",
            plans_dir=cwd / ".mcmig" / "plans",
            exclude=args.exclude,
            include=args.include,
            rule_files=[Path(f) for f in args.rule],
        )
    except (FileNotFoundError, ValueError) as e:
        _print(f"[错误] {e}")
        return 2
    reporter = PlanReporter(plan, src_version=args.src, dst_version=args.dst)
    if args.json:
        _print(reporter.to_json(compat_warnings))
    else:
        reporter.render(PlanOptions(show_skip=args.show_skip, category=args.category))
        reporter.render_compat_warnings(compat_warnings)
    return 0


def _game_running(dst_root: Path) -> bool:
    """探测目标版本是否被运行中的游戏占用(Windows 文件锁)。"""
    for name in ("usercache.json", "options.txt"):
        p = dst_root / name
        if p.exists():
            try:
                with p.open("r+b"):
                    pass
            except OSError:
                return True
    return False


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

    # 第一步:预检(NeoForge 兼容)
    err, bad = _swap_preflight(dst_dir, new_pack)
    if err is not None:
        _print(f"[错误] {err}")
        return 2
    # 预检:src 快照必须已存在(规划步依赖;装包前检查,dry-run 同样生效)
    src_snap = snapshot_path(Path.cwd(), args.src)
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
        plan, compat_warnings = build_plan(
            Path.cwd(),
            game_root,
            args.src,
            args.dst,
            modpack_swap=True,
            rescan_dst=True,
            mcmig_dir=Path.cwd() / ".mcmig",
            plans_dir=Path.cwd() / ".mcmig" / "plans",
        )
    except (FileNotFoundError, ValueError) as e:
        _print(f"[错误] 规划失败: {e}")
        return 2
    for w in compat_warnings:
        _print(f"[兼容警告] {w}")
    # 第四步:摘要 + 下一步提示
    _print("[规划] 迁移计划已生成,按来源分类计数:")
    _print(
        "  " + ", ".join(f"{k}={v}" for k, v in sorted(plan.summary().items()) if v > 0)
    )
    p_path = plan_path(Path.cwd(), args.src, args.dst)
    _print(f"审阅 {p_path} 后运行: mcmig migrate {args.src} {args.dst}")
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    """migrate 子命令:加载 plan → 防护校验 → ASK 预收集 → 确认 → pipeline 执行 → 回写状态 → PCL 提醒。"""
    cwd = Path.cwd()
    game_root = _resolve_game_root(args)
    p_path = plan_path(cwd, args.src, args.dst)
    if not p_path.exists():
        _print(f"[错误] 缺少计划文件 {p_path}")
        _print("请先运行: mcmig plan <源> <目标>")
        return 2
    try:
        plan = MigrationPlan.load(p_path)
    except (PlanFormatError, OSError) as e:
        _print(f"[错误] 计划文件读取失败: {e}")
        return 2
    if plan.executed_at and not args.force:
        _print(
            f"[错误] 该计划已执行(时间 {plan.executed_at})。重跑请加 --force"
            "(可重入:已完成文件会自动跳过)。"
        )
        return 2
    src_snap = snapshot_path(cwd, args.src)
    dst_snap = snapshot_path(cwd, args.dst)
    stale = any(
        p.exists() and p.stat().st_mtime > p_path.stat().st_mtime for p in (src_snap, dst_snap)
    )
    if stale and not args.force:
        _print("[错误] 快照比计划新,计划可能过期。请重跑 plan,或 --force 强制执行。")
        return 2
    src_root = _version_dir(game_root, args.src)
    dst_root = _version_dir(game_root, args.dst)
    if not src_root.is_dir() or not dst_root.is_dir():
        _print("[错误] 源/目标版本文件夹不存在")
        return 2
    if _game_running(dst_root):
        _print("[警告] 目标版本文件被占用,游戏可能仍在运行;继续可能损坏存档。")
        if not args.force:
            return 2
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
    results = execute_migration(plan, src_root, dst_root, ask_yes, dry_run=args.dry_run)
    from collections import Counter

    stat = Counter(r.status for r in results)
    failed = [r for r in results if r.failed]
    _print(f"结果: {dict(stat)};失败 {len(failed)}")
    for r in failed:
        _print(f"  [失败] {r.path}: {r.error}")
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
    ok, lines = run_doctor()
    for line in lines:
        _print(line)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""
    _safe_reconfigure_streams()
    args = build_parser().parse_args(argv)
    _setup_logging(getattr(args, "quiet", False))
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
    build_parser().print_help()
    return 1
