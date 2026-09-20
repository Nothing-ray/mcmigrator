"""编排管线下沉:scan / plan / execute 三段编排,CLI 与未来 GUI 平级消费。

自 cli.py 原样搬移 `_run_plan_pipeline` 与 `_cmd_scan` 的扫描构建逻辑,
逻辑不改,仅参数化 mcmig_dir / plans_dir / 快照目录:
- 快照路径 = <mcmig_dir>/snapshots/<版本名>.snapshot.json(与 snapshot_path(cwd,·) 同构,
  mcmig_dir=cwd/.mcmig 时两者完全一致)
- plan 路径 = <plans_dir>/<src>__<dst>.plan.json(与 plan_path(cwd,·) 同构)
规则组装 build_ruleset 仍留在 cli(本模块延迟导入,避免 cli↔pipeline 模块级循环)。

输出约定:供 GUI 复用的数据一律走返回值;过程中的警告走 logging(stderr),
唯一例外是换包提示——原实现直写 stderr,且被 CLI 测试断言,保持 print 不变。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .classifier import Classifier
from .differ import Differ
from .executor import Executor, FileResult
from .fsops import check_disk_space, clean_stale_tmp
from .plan import ActionRecord, Behavior, MigrationPlan, Origin
from .planner import Planner
from .scanner import Scanner
from .snapshot import Snapshot

if TYPE_CHECKING:
    from .moddb import CompatWarning, ModRegistry

log = logging.getLogger(__name__)


def _print_err(text: str) -> None:
    """打到 stderr(保持 stdout 纯净,如 plan --json 模式)。"""
    print(text, file=sys.stderr)


def _version_dir(game_root: Path, version: str) -> Path:
    """返回版本文件夹路径:game_root/versions/<version>。"""
    return game_root / "versions" / version


def list_versions(game_root: Path) -> list[str]:
    """列出游戏根目录下全部版本文件夹名(升序);versions/ 不存在时返回空列表。

    M3 收口:CLI(错误提示列可用版本)与 GUI(/api/versions)共用的唯一实现。
    """
    vdir = game_root / "versions"
    if not vdir.is_dir():
        return []
    return sorted(p.name for p in vdir.iterdir() if p.is_dir())


def read_active_version(game_root: Path) -> str | None:
    """读取 PCL.ini 的活跃版本(``Version:`` 行);文件缺失/不可解析返回 None。

    M3 收口:自 gui/server 原样提取(CLI 与 GUI 共用)。PCL2 写出的 ini 可能为
    UTF-8(可带 BOM)或 ANSI(GBK 系),按序尝试解码。
    """
    ini = game_root / "PCL.ini"
    if not ini.is_file():
        return None
    text: str | None = None
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = ini.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, OSError):
            continue
    if text is None:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Version:"):
            value = stripped.split(":", 1)[1].strip()
            return value or None
    return None


def _snapshot_file(mcmig_dir: Path, version: str) -> Path:
    """返回快照文件路径:<mcmig_dir>/snapshots/<version>.snapshot.json(文件名规则不变)。"""
    return mcmig_dir / "snapshots" / f"{version}.snapshot.json"


def find_snapshot(data_dir: Path, legacy_dir: Path | None, version: str) -> tuple[Path, bool]:
    """查找快照文件(F8 批次D 锚定):data_dir(锚定)优先,legacy_dir(旧 CWD 布局)回退。

    Args:
        data_dir: 生成物锚定 .mcmig 目录(game_root 侧)。
        legacy_dir: 旧布局 .mcmig 目录(通常 CWD 侧);None 或与 data_dir 相同表示无回退。
        version: 版本名。

    Returns:
        (快照路径, 是否旧布局命中);两侧均不存在时返回 (锚定路径, False),
        调用方以此路径报「缺少快照」。
    """
    anchored = _snapshot_file(data_dir, version)
    if anchored.exists() or legacy_dir is None or legacy_dir == data_dir:
        return anchored, False
    legacy = _snapshot_file(legacy_dir, version)
    if legacy.exists():
        return legacy, True
    return anchored, False


def scan_version(
    game_root: Path,
    version: str,
    workdir_snapshots: Path,
    *,
    strict: bool = False,
    on_error: Callable[[str], None] | None = None,
) -> Snapshot:
    """扫描一个版本文件夹,构建快照并写入快照文件。

    Args:
        game_root: 游戏根目录(含 versions/)。
        version: 版本名(versions/ 下的文件夹名)。
        workdir_snapshots: 快照目录,快照写为 <workdir_snapshots>/<version>.snapshot.json。
        strict: True 时强制全量哈希(对应 scan --strict)。
        on_error: 单个不可读文件的回调(传相对路径,每条扫描错误调用一次);
            与 log.warning 并行触发,供调用方统计 unreadable(v0 spec §7 报告契约)。

    Returns:
        已构建并落盘的快照。无法读取的文件会被跳过并逐条记 warning 日志。
    """
    ver_dir = _version_dir(game_root, version)
    snap, scan_errors = Scanner(ver_dir, version, strict=strict).build_snapshot(
        str(game_root)
    )
    snap.save(workdir_snapshots / f"{version}.snapshot.json")
    for e in scan_errors:
        log.warning("[警告] 扫描 %s 时无法读取: %s", version, e)
        if on_error is not None:
            on_error(e.path)
    return snap


def build_plan(
    cwd: Path,
    game_root: Path,
    src: str,
    dst: str,
    *,
    modpack_swap: bool = False,
    rescan_dst: bool = False,
    save: bool = True,
    mcmig_dir: Path,
    plans_dir: Path,
    data_dir: Path | None = None,  # 生成物锚定目录(快照);None → mcmig_dir(GUI 兼容布局)
    exclude: Sequence[str] = (),
    include: Sequence[str] = (),
    rule_files: Sequence[Path] = (),
) -> tuple[MigrationPlan, list["CompatWarning"]]:
    """plan 公共管线(plan 子命令与 swap 第三步共用,GUI 亦可直调)。

    流程:载入 src 快照 → dst 快照(rescan_dst=True 时现场重扫并落盘,否则载入已有)
    → orphan 规则 → ruleset → diff(modpack_swap) → planner → mod 兼容检查 → 保存 plan。

    Args:
        cwd: 调用方工作目录(签名锚点;快照/rules/plans 路径已分别由
            mcmig_dir/plans_dir 参数化,本函数不直接使用 cwd)。
        game_root: 游戏根目录。
        src: 源版本名。
        dst: 目标版本名。
        modpack_swap: 换包模式(源独有 mod 视为旧包自带,不回迁)。
        rescan_dst: True 时重扫 dst 生成最新快照并落盘(swap 装包后必开)。
        save: 是否持久化 plan 文件。
        mcmig_dir: .mcmig 目录(rules.yaml 所在)。
        plans_dir: plan 目录(写为 plans_dir/<src>__<dst>.plan.json)。
        data_dir: 生成物锚定目录(F8 批次D):快照读/写均落 <data_dir>/snapshots/;
            None → 等于 mcmig_dir(GUI 兼容布局,行为不变)。mcmig_dir 侧快照仅作
            旧布局回退(命中时 warning 提示整体迁移)。
        exclude: CLI 级临时规则 glob(本次按 never)。
        include: CLI 级临时规则 glob(本次按 must_migrate)。
        rule_files: 额外规则文件路径列表。

    Returns:
        (plan, compat_warnings)。

    Raises:
        FileNotFoundError: src 快照不存在,或 rescan_dst=False 且 dst 快照不存在。
        ValueError: 快照存在但读取失败。
    """
    data = data_dir or mcmig_dir
    legacy = mcmig_dir if data != mcmig_dir else None
    src_path, src_legacy = find_snapshot(data, legacy, src)
    if not src_path.exists():
        raise FileNotFoundError(f"缺少 {src} 快照")
    if src_legacy:
        log.warning("[提示] %s 使用旧布局快照(%s),建议整体迁移至 %s",
                    src, src_path.parent.parent, data)
    try:
        src_snap = Snapshot.load(src_path)
    except FileNotFoundError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"{src} 快照读取失败: {e}") from e
    dst_path, dst_legacy = find_snapshot(data, legacy, dst)
    if rescan_dst:
        # swap 装包刚改写 dst/mods,必须现场重扫以保证 dst 快照反映最新状态
        dst_snap = scan_version(game_root, dst, data / "snapshots")
    elif not dst_path.exists():
        raise FileNotFoundError(f"缺少 {dst} 快照")
    else:
        if dst_legacy:
            log.warning("[提示] %s 使用旧布局快照(%s),建议整体迁移至 %s",
                        dst, dst_path.parent.parent, data)
        try:
            dst_snap = Snapshot.load(dst_path)
        except FileNotFoundError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"{dst} 快照读取失败: {e}") from e
    # 扫描 src/dst mods → 建 mod 注册表 → 生成 orphan 规则
    from .moddb import (
        check_mod_compat,
        generate_orphan_rules,
        load_mod_config_map,
        read_neoforge_version,
        scan_mods,
    )

    # 规则组装留在 cli(见模块 docstring),延迟导入避免模块级循环
    from .cli import build_ruleset

    src_dir = _version_dir(game_root, src)
    dst_dir = _version_dir(game_root, dst)
    dst_mods = scan_mods(dst_dir)
    override = load_mod_config_map()
    orphan_rules = generate_orphan_rules(src_snap.files, dst_mods, override)
    rs, errs = build_ruleset(
        [src, dst],
        exclude=list(exclude),
        include=list(include),
        rule_files=list(rule_files),
        mcmig_dir=mcmig_dir,
        with_whitelist=True,
        orphan_rules=orphan_rules,
    )
    for e in errs:
        log.warning("[规则警告] %s", e)
    clf = Classifier(rs)
    # 换包提示:src 独有 mod jar 数量大(≥20)时提醒用户(正常版本升级只有个位数)
    src_only_mods = sum(
        1 for p in src_snap.files
        if p.path.startswith("mods/") and p.path.endswith(".jar")
        and not any(d.path == p.path for d in dst_snap.files)
    )
    if not modpack_swap and src_only_mods >= 20:
        _print_err(
            f"[提示] 检测到 {src_only_mods} 个源独有 mod。若这是一次整合包替换,"
            "请加 --modpack-swap 避免旧包 mod 被搬入新包。"
        )
    report = Differ(src_snap.files, dst_snap.files, clf, modpack_swap=modpack_swap).diff()
    src_index = {e.path: e for e in src_snap.files}
    plan = Planner(report, src_index).plan()
    plan.src, plan.dst = src, dst
    # 版本兼容检查:对 mod_added 的 jar 检查 NeoForge 版本范围
    src_mods = scan_mods(src_dir)
    dst_nf_version = read_neoforge_version(dst_dir)
    mod_added_paths = [
        r.path for r in plan.actions if r.behavior == Behavior.COPY and r.origin == Origin.MOD_ADDED
    ]
    compat_warnings = check_mod_compat(mod_added_paths, src_mods, dst_nf_version)
    if save:
        try:
            plan.save(plans_dir / f"{src}__{dst}.plan.json")
        except OSError as e:
            log.warning("[警告] plan 文件写入失败(已忽略,stdout 仍有效): %s", e)
    return plan, compat_warnings


def execute_migration(
    plan: MigrationPlan,
    src_root: Path,
    dst_root: Path,
    ask_yes: set[str],
    dry_run: bool = False,
    progress_cb: Callable[[FileResult], None] | None = None,
) -> list[FileResult]:
    """执行迁移计划(三道预检 + Executor 封装,CLI 与 GUI 平级消费)。

    预检序列(执行前):
    1. clean_stale_tmp(dst_root):清理上次崩溃残留的 *.mcmig-tmp(有写盘副作用,
       dry-run 跳过以守住零写盘契约)
    2. check_disk_space(dst_root, Σ COPY 动作源文件大小):不足抛 DiskSpaceError,
       此时零写盘(模块级函数引用,便于 GUI/测试注入替身)

    Args:
        plan: 已审阅的迁移计划。
        src_root: 源版本根目录。
        dst_root: 目标版本根目录。
        ask_yes: 预收集的 ASK 确认路径集合(命中即迁移,未命中按 asked_no 跳过)。
        dry_run: True 时零写盘,结果为推演(tmp 清理随之跳过;磁盘预检只读仍执行)。
        progress_cb: 逐文件结果实时回调——注入 Executor.execute,单文件完成即同步
            调用(GUI 进度条数据源);None 时无回调,行为不变。

    Returns:
        逐文件执行结果(按 plan.actions 顺序)。

    Raises:
        DiskSpaceError: 目标磁盘剩余空间不足(预检失败,零写盘)。
    """
    if not dry_run:
        removed = clean_stale_tmp(dst_root)
        if removed:
            log.info("[预检] 已清理 %d 个残留临时文件(*.mcmig-tmp)", removed)
    # 磁盘预检:按 COPY 动作源文件大小求和(identical 会零写盘,偏保守无害)
    needed = sum(a.src_size or 0 for a in plan.actions if a.behavior == Behavior.COPY)
    check_disk_space(dst_root, needed)

    def ask(a: ActionRecord) -> bool:
        """ASK 动作决策:路径在预确认集合内即迁移。"""
        return a.path in ask_yes

    return Executor(plan, src_root, dst_root, ask).execute(
        dry_run=dry_run, progress_cb=progress_cb
    )


@dataclass(frozen=True)
class DiffContext:
    """独立 diff 的扫描上下文:两侧 mod 注册表 + 真实版本目录(F2/F4/F12 共同基座)。"""

    src_mods: "ModRegistry"
    dst_mods: "ModRegistry"
    src_dir: Path
    dst_dir: Path
    same_dir: bool = False  # 两侧版本目录 resolve 后同路径(junction 同体)→ 注册表配对不可信

    def read_file(self, rel_path: str, side: str) -> bytes | None:
        """按侧读取版本目录内文件字节内容;文件缺失/IO 失败返回 None。

        Args:
            rel_path: 版本内相对路径(正斜杠)。
            side: "src" 或 "dst";其他值视为不存在。
        """
        # junction 同体(same_dir)短路:两侧目录是同一物理路径,"src"/"dst" 读数恒等,
        # 语义复核会以"当前字节 vs 当前字节"伪等价掩盖两条快照间的真实变化
        # (终审 Issue 1)。返回 None 让 Differ 按读取失败退回快照字节比较。
        if self.same_dir:
            return None
        if side not in ("src", "dst"):
            return None
        root = self.src_dir if side == "src" else self.dst_dir
        try:
            return (root / rel_path).read_bytes()
        except OSError:
            return None


def resolve_diff_context(src_snap: Snapshot, dst_snap: Snapshot) -> DiffContext | None:
    """从两份快照的 game_root+version 解析各自版本目录并扫描 mods。

    任一侧目录不可达(跨机复放/夹具/手动删除)→ 返回 None,调用方降级为
    纯快照对比(与 0.6.1 行为逐字节一致)。版本目录 = <game_root>/versions/<version>,
    服务端 NTFS Junction 影子根同样成立。

    Args:
        src_snap: 源侧快照。
        dst_snap: 目标侧快照。

    Returns:
        DiffContext,或 None(无法解析/不可达)。
    """
    dirs: list[Path] = []
    for snap in (src_snap, dst_snap):
        # version 空串同样守卫:"" 参与 Path 拼接会折叠成 versions/ 目录本身
        if not snap.game_root or not snap.version:
            return None
        vdir = Path(snap.game_root) / "versions" / snap.version
        if not vdir.is_dir():
            return None
        dirs.append(vdir)
    from .moddb import scan_mods  # 延迟导入避免循环
    return DiffContext(
        src_mods=scan_mods(dirs[0]),
        dst_mods=scan_mods(dirs[1]),
        src_dir=dirs[0],
        dst_dir=dirs[1],
        same_dir=dirs[0].resolve() == dirs[1].resolve(),
    )
