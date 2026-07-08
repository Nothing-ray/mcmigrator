"""命令行入口:scan / diff 两个子命令。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__, rules
from .classifier import Classifier
from .differ import Differ
from .plan import plan_path
from .planner import Planner
from .reporter import DiffReporter, PlanOptions, PlanReporter, ReportOptions
from .scanner import Scanner
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
    p_plan.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    p_plan.add_argument("--include", action="append", default=[], metavar="GLOB")
    p_plan.add_argument("--rule", action="append", default=[], metavar="FILE")
    p_plan.add_argument("--show-skip", action="store_true", help="显示 skip_*/keep_mod/ignore")
    p_plan.add_argument("--category", default=None, help="仅显示某 action")
    p_plan.add_argument("--json", action="store_true")
    p_plan.add_argument("--no-save", action="store_true", help="不持久化 plan 文件")
    p_plan.add_argument("-q", "--quiet", action="store_true")
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
    args: argparse.Namespace,
    mcmig_dir: Path,
    *,
    with_whitelist: bool = False,
) -> tuple[rules.RuleSet, list[str]]:
    """按优先级(CLI > extra > user > whitelist > default)组装 RuleSet。

    with_whitelist=True 时插入白名单层(仅 plan 命令启用),scan/diff 零回归。
    """
    cli_rules = rules.load_cli_rules(args.exclude, args.include)
    extra: list[rules.Rule] = []
    errors: list[str] = []
    for f in args.rule:
        r, e = rules.load_user_rules(Path(f))
        extra.extend(r)
        errors.extend(e)
    user_path = mcmig_dir / "rules.yaml"
    user, ue = rules.load_user_rules(user_path)
    errors.extend(ue)
    whitelist: list[rules.Rule] = []
    if with_whitelist:
        from importlib import resources

        wl_text = resources.files("migration").joinpath("data/whitelist.yaml").read_text(encoding="utf-8")
        whitelist, we = rules.load_whitelist_rules_from_text(wl_text, "whitelist.yaml")
        errors.extend(we)
    default, de = rules.load_default_rules(versions)
    errors.extend(de)
    rs = rules.RuleSet.from_layers(cli_rules, extra, user, whitelist, default)
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
    rs, errs = build_ruleset(args.version, args, mcmig_dir)
    for e in errs:
        _print(f"[规则警告] {e}")
    snap, scan_errors = Scanner(ver_dir, args.version, strict=args.strict).build_snapshot(
        str(game_root)
    )
    spath = snapshot_path(cwd, args.version)
    snap.save(spath)
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
                    "unreadable": len(scan_errors),
                    "snapshot": str(spath),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print(f"[完成] 扫描 {args.version}: {snap.file_count} 个文件 → {spath}")
        _print("分类汇总: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        if scan_errors:
            _print(f"[警告] {len(scan_errors)} 个文件无法读取(已跳过)")
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
    rs, errs = build_ruleset([args.src, args.dst], args, mcmig_dir)
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
    """plan 子命令:load snapshots → diff → plan → 渲染 + 持久化。"""
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
    rs, errs = build_ruleset([args.src, args.dst], args, mcmig_dir, with_whitelist=True)
    for e in errs:
        _print(f"[规则警告] {e}")
    clf = Classifier(rs)
    report = Differ(src.files, dst.files, clf).diff()
    src_index = {e.path: e for e in src.files}
    plan = Planner(report, src_index).plan()
    plan.src, plan.dst = args.src, args.dst
    reporter = PlanReporter(plan, src_version=args.src, dst_version=args.dst)
    if args.json:
        _print(reporter.to_json())
    else:
        reporter.render(PlanOptions(show_skip=args.show_skip, category=args.category))
    if not args.no_save:
        try:
            plan.save(plan_path(cwd, args.src, args.dst))
        except OSError as e:
            _print(f"[警告] plan 文件写入失败(已忽略,stdout 仍有效): {e}")
    return 0


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
    build_parser().print_help()
    return 1
