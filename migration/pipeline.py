"""编排管线下沉:scan / diff / plan / execute 四段编排,CLI 与未来 GUI 平级消费。

自 cli.py 原样搬移 `_run_plan_pipeline`、`_cmd_scan` 的扫描构建逻辑与
`_cmd_diff` 的 diff 编排,逻辑不改,仅参数化 mcmig_dir / plans_dir / 快照目录:
- 快照路径 = <mcmig_dir>/snapshots/<版本名>.snapshot.json(与 snapshot_path(cwd,·) 同构,
  mcmig_dir=cwd/.mcmig 时两者完全一致)
- plan 路径 = <plans_dir>/<src>__<dst>.plan.json(与 plan_path(cwd,·) 同构)
规则组装 build_ruleset 仍留在 cli(本模块延迟导入,避免 cli↔pipeline 模块级循环)。

输出约定:供 GUI 复用的数据一律走返回值;过程中的警告走 logging(stderr)。
例外两处,均为下沉前直写、被 CLI 测试断言的输出,保持原样:
- plan 管线的换包提示(build_plan 内 _print_err 直写);
- run_diff 的 stderr 提示行已结构化为 DiffOutcome.notices 返回(调用方逐行转发,
  与下沉前 CLI 输出逐字节一致);规则警告沿用下沉前 stdout print。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .classifier import Classifier
from .differ import DiffReport, Differ, is_mod_jar
from .executor import Executor, FileResult
from .fsops import check_disk_space, clean_stale_tmp
from .plan import ActionRecord, Behavior, MigrationPlan, Origin
from .planner import Planner
from .scanner import Scanner
from .snapshot import Snapshot

if TYPE_CHECKING:
    from . import rules
    from .moddb import CompatWarning, ModPair, ModRegistry

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
) -> tuple[MigrationPlan, list["CompatWarning"], list["ModPair"]]:
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
        (plan, compat_warnings, mod_pairs):plan 与兼容警告同前;mod_pairs 为
        双源配对结果(批次F,渲染级 ⇄ 注记用;plan 文件持久化不含它,schema 零变化)。

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
    # 两侧注册表各扫一次(批次F 上移 src 扫描:orphan 规则用 dst_mods,
    # 配对用双侧,compat 检查用 src_mods——不再后段重复扫描)
    src_mods = scan_mods(src_dir)
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
        world_dirs=sorted(set(src_snap.world_dirs) | set(dst_snap.world_dirs)),  # F34① 双侧并集
    )
    for e in errs:
        log.warning("[规则警告] %s", e)
    clf = Classifier(rs)
    # 换包提示:src 独有 mod jar 数量大(≥20)时提醒用户(正常版本升级只有个位数)
    # P6/P8 收口:jar 判定同源 Differ.is_mod_jar(与配对/分桶同一把尺),
    # 目标存在性改 O(n) 集合差(原为逐条 any 线性扫描)
    dst_paths = {d.path for d in dst_snap.files}
    src_only_mods = sum(
        1 for p in src_snap.files
        if is_mod_jar(p.path) and p.path not in dst_paths
    )
    if not modpack_swap and src_only_mods >= 20:
        _print_err(
            f"[提示] 检测到 {src_only_mods} 个源独有 mod。若这是一次整合包替换,"
            "请加 --modpack-swap 避免旧包 mod 被搬入新包。"
        )
    report = Differ(src_snap.files, dst_snap.files, clf, modpack_swap=modpack_swap,
                    mtime_evidence=src_dir.resolve() == dst_dir.resolve()).diff()
    # 批次F:双源配对(与 run_diff 同一单点;plan 侧渲染 ⇄ 注记用,不进 plan.json)
    mod_pairs = compute_mod_pairs(
        src_snap, dst_snap, src_mods, dst_mods,
        same_dir=src_dir.resolve() == dst_dir.resolve(),
    )
    src_index = {e.path: e for e in src_snap.files}
    plan = Planner(report, src_index).plan()
    plan.src, plan.dst = src, dst
    # 版本兼容检查:对 mod_added 的 jar 检查 NeoForge 版本范围
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
    return plan, compat_warnings, mod_pairs


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


def diff_identity_notices(
    src_path: Path, dst_path: Path,
    src: Snapshot, dst: Snapshot, ctx: DiffContext | None,
) -> str | None:
    """自比对/junction 同体检测(单点,live 与复放同源)。

    主判:两侧为同一快照文件(自比对错误用法);
    回退:live 模式 ctx.same_dir + 同刻(F27 现状,旧快照无 resolved_root 也走此臂,
      文案逐字节保留——活体证据优先于快照落盘字段,故先于佐证臂判定);
    佐证:ctx 不可达(复放)且 resolved_root 相等且同刻 → 疑似自比对
      (live 边缘窗——ctx 可达但 same_dir=False 而 resolved_root 同+同刻——保持静默:
      该窗内注册表配对实际可用,发「注册表配对不可用」文案失实)。
    同物理目录不同刻(影子根标准用法)返回 None,调用方按需 log.debug。

    Args:
        src_path: 源侧快照文件路径。
        dst_path: 目标侧快照文件路径。
        src: 源侧快照。
        dst: 目标侧快照。
        ctx: 活体扫描上下文;None 表示复放模式(目录不可达)。

    Returns:
        stderr 提示行;静默分支返回 None(由调用方 log.debug)。
    """
    if src_path.resolve() == dst_path.resolve():
        return ("[提示] 两侧为同一份快照文件(自比对):注册表配对与语义复核不可用,"
                "已使用文件名配对/字节比较")
    same_time = src.scanned_at == dst.scanned_at
    # 回退臂(live):junction 同体 + 同刻 → F27 现行文案
    if ctx is not None and ctx.same_dir and same_time:
        return ("[提示] 两侧快照同刻且版本目录指向同一路径(junction 同体):"
                "注册表配对与语义复核不可用,已使用文件名配对/字节比较")
    # 佐证臂(复放):ctx 不可达(复放)且快照自带的 resolved_root 相等 + 同刻 → 疑似自比对;
    # ctx 存在(live)时此臂静默——活体边缘窗(ctx 可达但 same_dir=False,resolved_root
    # 却同+同刻)里注册表配对实际可用,发降级文案会失实
    if (ctx is None and same_time
            and src.resolved_root is not None and src.resolved_root == dst.resolved_root):
        return ("[提示] 两侧快照同刻且版本目录指向同一路径(疑似自比对):"
                "注册表配对与语义复核不可用,已使用文件名配对/字节比较")
    return None


def world_rename_notices(src: Snapshot, dst: Snapshot) -> list[str]:
    """世界目录改名探测(F34②):src 独有世界 A → dst 独有世界 B,同路径同尺寸
    占比 ≥0.9 时发提示(仅提示不重分类——假警报降级为可见解释)。

    候选:A ∈ src.world_dirs 且 ∉ dst.world_dirs(旧路径消失),B 反之(新路径出现);
    匹配 = 相对子路径双侧存在且 size 相等;占比分母 min(双侧文件数),≥1 件才评估。
    阈值 0.9 为 r13 语料标定(660/660;容忍 session.lock 类零星重写)。

    Args:
        src: 源侧快照(需 world_dirs,旧快照缺省为空表自然不发)。
        dst: 目标侧快照。

    Returns:
        提示行列表(每命中改名对一条,按 "A → B" 排序)。
    """
    notices: list[str] = []
    for a in sorted(set(src.world_dirs) - set(dst.world_dirs)):
        a_files = {e.path[len(a) + 1:]: e.size for e in src.files
                   if e.path.startswith(a + "/")}
        if not a_files:
            continue
        for b in sorted(set(dst.world_dirs) - set(src.world_dirs)):
            b_files = {e.path[len(b) + 1:]: e.size for e in dst.files
                       if e.path.startswith(b + "/")}
            if not b_files:
                continue
            denom = min(len(a_files), len(b_files))
            matched = sum(1 for sub, sz in a_files.items()
                          if b_files.get(sub) == sz)
            if matched and matched / denom >= 0.9:
                notices.append(
                    f"[提示] 疑似世界目录改名: {a} → {b}"
                    f"({matched} 件同路径同尺寸,内容未消失,已随目录改名迁移)"
                )
    return sorted(notices)


def compute_mod_pairs(
    src: Snapshot,
    dst: Snapshot,
    src_mods: "ModRegistry | None",
    dst_mods: "ModRegistry | None",
    *,
    same_dir: bool = False,
) -> list["ModPair"]:
    """双源配对单点:filename(快照差集+is_mod_jar 过滤)恒算;registry 仅当双侧注册表
    都给出且 same_dir=False 时叠加(merge_mod_pairs registry 优先)。
    run_diff 传 ctx.same_dir;build_plan 传 <src_dir>.resolve()==<dst_dir>.resolve()。

    Args:
        src: 源侧快照。
        dst: 目标侧快照。
        src_mods: 源侧 mod 注册表;None 表示不可用(复放/降级)。
        dst_mods: 目标侧 mod 注册表;None 表示不可用(复放/降级)。
        same_dir: 两侧版本目录同体(junction)时注册表配对不可信,只算 filename。

    Returns:
        合并后的 ModPair 列表(registry 优先,filename 兜底)。
    """
    from .moddb import annotate_content_differs, merge_mod_pairs, pair_mods, pair_mods_by_filename

    # F4 双源配对:registry(读 jar,目录真实独立时)优先
    registry_pairs: list["ModPair"] = []
    if src_mods is not None and dst_mods is not None and not same_dir:
        registry_pairs = pair_mods(src_mods, dst_mods)
    # F23/F26:配对输入从快照集合直接推导(与 Differ.is_mod_jar 同源判定)——
    # modpack_swap 只改分桶,不再饿死配对(swap 下 target_only 保留 ⇄upgrade)
    src_paths = {e.path for e in src.files}
    dst_paths = {e.path for e in dst.files}
    filename_pairs = pair_mods_by_filename(
        sorted(p for p in src_paths - dst_paths if is_mod_jar(p)),
        sorted(p for p in dst_paths - src_paths if is_mod_jar(p)),
    )
    merged = merge_mod_pairs(registry_pairs, filename_pairs)
    # F33:同版本改名对补 size 证据注记(通道无关,快照为唯一 size 源)
    return annotate_content_differs(
        merged,
        {e.path: e.size for e in src.files},
        {e.path: e.size for e in dst.files},
    )


def match_client_only_paths(
    report: DiffReport, ctx: DiffContext | None,
    client_modids: set[str], client_families: set[str],
) -> set[str]:
    """mods 桶内命中客户端清单的路径集合(registry modid 反查 + 家族键,双通道)。"""
    from .moddb import normalize_jar_family

    if not client_modids and not client_families:
        return set()
    paths = {i.path for i in report.mods}
    hit: set[str] = set()
    if client_families:
        for p in paths:
            fam = normalize_jar_family(p)[0]
            if fam and fam in client_families:
                hit.add(p)
    if client_modids and ctx is not None:
        for mods in (ctx.src_mods, ctx.dst_mods):
            for mid in mods.modids:
                if mid in client_modids:
                    info = mods.get(mid)
                    # F32:内嵌(jar-in-jar)件反查宿主物理 jar——专服要隔离的是宿主实体
                    physical = info.embedded_in or info.jar_filename
                    hit.add(f"mods/{physical}")
    return hit & paths


@dataclass
class DiffOutcome:
    """diff 管线产物:六桶报告 + 配对 + 双侧快照 + stderr 提示行。"""

    report: DiffReport
    mod_pairs: list["ModPair"]
    src: Snapshot
    dst: Snapshot
    notices: list[str]
    # F30① 客户端件标注:命中已知客户端清单的 mods 桶路径(供 reporter 标注)
    client_only_paths: set[str] = field(default_factory=set)


def run_diff(
    cwd: Path,
    *,
    src: str,
    dst: str,
    modpack_swap: bool = False,
    exclude: Sequence[str] = (),
    include: Sequence[str] = (),
    rule_files: Sequence[Path] = (),
    mcmig_dir: Path | None = None,  # None → cwd/.mcmig
    game_root: Path | None = None,  # None → 快照走 cwd/.mcmig 直取(仅定位,不管 ctx)
) -> DiffOutcome:
    """diff 公共管线(自 cli._cmd_diff 整体搬移,编排逻辑不改;CLI 与 GUI 平级消费)。

    流程:快照定位(game_root 给出 → find_snapshot 锚定优先+旧 CWD 布局回退,
    legacy 命中提示入 notices;game_root=None → <mcmig_dir or cwd/.mcmig>/snapshots
    直取,无回退)→ load → resolve_diff_context(无条件,按快照内记录的 game_root
    判定活体可达;不可达提示入 notices)→ build_ruleset → Differ(modpack_swap)
    → compute_mod_pairs → diff_identity_notices(非 None 入 notices;
    ctx.same_dir 且 None 时 log.debug)→ swap 排除计数提示入 notices
    → client_only 清单匹配(非空入 client_only_paths + 警示行,仅标注)。

    Args:
        cwd: 调用方工作目录(mcmig_dir=None 时的 .mcmig 基准)。
        src: 源版本名。
        dst: 目标版本名。
        modpack_swap: 换包模式(源独有 mod 视为旧包自带,归换包排除)。
        exclude: CLI 级临时规则 glob(本次按 never)。
        include: CLI 级临时规则 glob(本次按 must_migrate)。
        rule_files: 额外规则文件路径列表。
        mcmig_dir: .mcmig 目录(rules.yaml 所在);None → cwd/.mcmig。
        game_root: 游戏根目录;仅决定快照文件定位(锚定+旧布局回退),
            None → <mcmig_dir or cwd/.mcmig> 直取。活体 ctx 与孤儿/配对是否
            降级由快照内记录的 game_root 是否可达决定(与下沉前 CLI 逐字节一致)。

    Returns:
        DiffOutcome(六桶报告 + 配对 + 双侧快照 + stderr 提示行,调用方逐行转发)。

    Raises:
        FileNotFoundError: 缺快照(消息「缺少 <名[, 名...]> 快照」)。
        ValueError: 快照存在但读取失败。
    """
    rules_base = mcmig_dir if mcmig_dir is not None else cwd / ".mcmig"
    notices: list[str] = []
    # F8 锚定:game-root 可解析 → 锚定优先+旧 CWD 布局回退(命中提示整体迁移);
    # 不可解析(夹具复放/纯快照对比)→ rules_base 直取,0.6.x 行为,零提示零扰动
    if game_root is not None:
        data_dir = game_root / ".mcmig"
        src_path, src_leg = find_snapshot(data_dir, rules_base, src)
        dst_path, dst_leg = find_snapshot(data_dir, rules_base, dst)
        if src_leg or dst_leg:
            notices.append(f"[提示] 使用旧布局快照({rules_base}),建议整体迁移至 {data_dir}")
    else:
        src_path = _snapshot_file(rules_base, src)
        dst_path = _snapshot_file(rules_base, dst)
    missing = [n for n, p in ((src, src_path), (dst, dst_path)) if not p.exists()]
    if missing:
        raise FileNotFoundError(f"缺少 {', '.join(missing)} 快照")
    try:
        src_snap = Snapshot.load(src_path)
        dst_snap = Snapshot.load(dst_path)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"快照读取失败: {e}") from e
    from .moddb import generate_orphan_rules, load_mod_config_map

    # 规则组装留在 cli(见模块 docstring),延迟导入避免模块级循环
    from .cli import build_ruleset

    # 扫描上下文(F2/F4/F12 基座):无条件按快照内记录的 game_root 解析活体目录
    # (I-1:与下沉前 CLI 逐字节一致——game_root 参数只管快照定位/旧布局回退,
    # 不管 ctx;CLI 无 game_root 但快照指向活体根(legacy 布局用户)时配对/孤儿
    # 照常生效);任一侧不可达(跨机复放/夹具)→ ctx=None,降级为纯快照对比
    ctx = resolve_diff_context(src_snap, dst_snap)
    orphan_rules: list[rules.Rule] = []
    if ctx is not None:
        # F2 孤儿规则:与 pipeline.build_plan 完全同源(src config × dst 注册表 × 覆盖表)
        orphan_rules = generate_orphan_rules(src_snap.files, ctx.dst_mods, load_mod_config_map())
    else:
        notices.append(
            "[提示] mods 扫描不可用(game_root 不可达):孤儿标注与注册表配对已跳过,文件名配对仍可用"
        )
    rs, errs = build_ruleset(
        [src, dst],
        exclude=list(exclude),
        include=list(include),
        rule_files=list(rule_files),
        mcmig_dir=rules_base,
        orphan_rules=orphan_rules,
        world_dirs=sorted(set(src_snap.world_dirs) | set(dst_snap.world_dirs)),  # F34① 双侧并集
    )
    for e in errs:
        print(f"[规则警告] {e}")  # stdout,与下沉前 CLI 逐字节一致
    clf = Classifier(rs)
    # F12/F16: content_reader 注入语义复核(.properties/.json/.toml)
    # F19: modpack_swap 透传 differ(与 plan/swap 流程同源,换装验收旧 jar 归换包排除)
    # F35:mtime 证据闸门——两侧快照同物理根(同实例随时间演化)才开;
    # 跨实例(复制必变 mtime)恒关,杜绝假阳性
    mtime_ev = (src_snap.resolved_root is not None
                and src_snap.resolved_root == dst_snap.resolved_root)
    report = Differ(
        src_snap.files, dst_snap.files, clf,
        content_reader=ctx.read_file if ctx is not None else None,
        modpack_swap=modpack_swap,
        mtime_evidence=mtime_ev,
    ).diff()
    # F27→批次F:自比对/junction 同体检测单点(同文件主判,同根同刻佐证,旧快照回退)
    hint = diff_identity_notices(src_path, dst_path, src_snap, dst_snap, ctx)
    if hint is not None:
        notices.append(hint)
    elif ctx is not None and ctx.same_dir:
        log.debug(
            "junction 同体双快照(不同刻,标准影子根用法):"
            "注册表配对与语义复核不可用,已使用文件名配对/字节比较"
        )
    # F34②:世界目录改名探测(仅提示;pre→mid 切换段的千级 to_migrate 假警报降级)
    notices.extend(world_rename_notices(src_snap, dst_snap))
    mod_pairs = compute_mod_pairs(
        src_snap, dst_snap,
        ctx.src_mods if ctx is not None else None,
        ctx.dst_mods if ctx is not None else None,
        same_dir=ctx.same_dir if ctx is not None else False,
    )
    # F19 换包模式提示:有排除项时入 notices(调用方逐行 stderr,不污染 --json 的 stdout)
    if modpack_swap:
        n_swap = sum(1 for i in report.never if i.note == "modpack_swap")
        if n_swap:
            notices.append(
                f"[提示] 换包模式: {n_swap} 个源独有 mod 按旧包自带排除"
                "(never/换包排除,--show-never 可见)"
            )
    # F30① 客户端件标注:已知清单(data/client_mods.yaml)命中 mods 桶时入
    # client_only_paths(reporter 标注)+ stderr 警示行(仅标注不拦截,复放走家族键通道)
    from .moddb import load_client_mods

    client_modids, client_families = load_client_mods()
    client = match_client_only_paths(report, ctx, client_modids, client_families)
    if client:
        notices.append(
            f"[警示] {len(client)} 件已知客户端 mod(专服启动部署风险,F30): "
            + ", ".join(sorted(client))
        )
    return DiffOutcome(
        report=report, mod_pairs=mod_pairs, src=src_snap, dst=dst_snap,
        notices=notices, client_only_paths=client,
    )
