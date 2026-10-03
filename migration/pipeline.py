"""编排管线上下沉:scan / diff / plan / execute / swap 五段编排,CLI 与 GUI 平级消费。

自 cli.py 原样搬移 `_run_plan_pipeline`、`_cmd_scan` 的扫描构建逻辑与
`_cmd_diff` 的 diff 编排,逻辑不改,仅参数化 mcmig_dir / plans_dir / 快照目录;
批次I-W3 T7 再下沉 swap 编排(预检/装包/全流程,见文末 swap 段):
- 快照路径 = <mcmig_dir>/snapshots/<版本名>.snapshot.json(与 snapshot_path(cwd,·) 同构,
  mcmig_dir=cwd/.mcmig 时两者完全一致)
- plan 路径 = <plans_dir>/<src>__<dst>.plan.json(与 plan_path(cwd,·) 同构)
规则组装 build_ruleset 已由 cli 搬入本模块(批次H,消模块级循环;cli 原位置 re-export 保兼容)。

输出约定:供 GUI 复用的数据一律走返回值;过程中的警告走 logging(stderr)。
例外两处,均为下沉前直写、被 CLI 测试断言的输出,保持原样:
- plan 管线的换包提示(build_plan 内 _print_err 直写);
- run_diff 的 stderr 提示行已结构化为 DiffOutcome.notices 返回(调用方逐行转发,
  与下沉前 CLI 输出逐字节一致);规则警告沿用下沉前 stdout print。
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from . import rules
from .classifier import Classifier
from .differ import DiffReport, Differ, is_mod_jar
from .executor import Executor, FileResult
from .fsops import FsOpsError, check_disk_space, clean_stale_tmp, copy_atomic, md5_of
from .journal import JobJournal, JournalError
from .plan import ActionRecord, Behavior, MigrationPlan, Origin, PlanPersistError
from .planner import Planner
from .preflight import (  # noqa: F401 — 重导出:spec 命名 pipeline.preflight_execute 成立
    ExecutionDecisions,
    PreflightBlocker,
    PreflightWarning,
    preflight_execute,
    probe_maybe_running,
)
from .review import (  # noqa: F401 — 重导出:审阅有效性符号经 pipeline.* 可达,供 T5/GUI/W3 消费
    MSG_REVIEW_SNAPSHOT_MISSING,
    ReviewStateError,
    check_action_states,
    file_sha256,
    issue_review,
    plan_fingerprint,
    rules_fingerprint,
    validate_review,
)
from .scanner import Scanner
from .snapshot import Snapshot

if TYPE_CHECKING:
    from .moddb import CompatWarning, ModPair, ModRegistry

log = logging.getLogger(__name__)


def _print_err(text: str) -> None:
    """打到 stderr(保持 stdout 纯净,如 plan --json 模式)。"""
    print(text, file=sys.stderr)


def _version_dir(game_root: Path, version: str) -> Path:
    """返回版本文件夹路径:game_root/versions/<version>。"""
    return game_root / "versions" / version


# 版本名的非法字符集:路径分隔符(/ \)、盘符与 ADS 冒号、通配符与重定向符
# (Win32 保留)、控制字符(0x00-0x1f,含换行)
_VERSION_NAME_BAD_CHARS = set('\\/:*?"<>|')


def version_name_error(name: str) -> str | None:
    """校验版本名是「单一路径分量」,非法返回中文原因,合法返回 None。

    版本名最终与 ``game_root / "versions" / <name>`` 拼接——pathlib 拼接
    **绝对路径时会整体替换**,盘符/分隔符形态的「版本名」会把读写面带出
    game_root;故 CLI/API/核心(run_swap)共用本校验先行拒绝。junction/
    符号链接别名(名字合法、目录指向他处)不受影响——只校验名字形态。

    Args:
        name: 待校验的版本名。

    Returns:
        非法原因(中文);``None`` 表示合法。
    """
    if not name:
        return "版本名不能为空"
    if name in (".", ".."):
        return "版本名不能是 . 或 .."
    if name != name.strip():
        return "版本名不能以空白开头或结尾(Windows 会归一化致名不符)"
    if name.endswith("."):
        return "版本名不能以点结尾(Windows 会归一化致名不符)"
    for ch in name:
        if ord(ch) < 0x20 or ch in _VERSION_NAME_BAD_CHARS:
            return f"版本名含非法字符 {ch!r}(路径分隔符/盘符/通配符/控制字符)"
    return None


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


_WORLD_GLOB_SPECIALS = frozenset("\\*?[]#!")


def escape_world_glob(name: str) -> str:
    """转义世界目录名中的 gitignore 元字符(spec §3.5;全位置转义最简,pathspec 容忍)。"""
    return "".join(f"\\{ch}" if ch in _WORLD_GLOB_SPECIALS else ch for ch in name)


def build_ruleset(
    versions: str | list[str],
    *,
    exclude: list[str],
    include: list[str],
    rule_files: list[Path],
    mcmig_dir: Path,
    with_whitelist: bool = False,
    orphan_rules: list[rules.Rule] | None = None,
    world_dirs: Sequence[str] = (),
) -> tuple[rules.RuleSet, list[str]]:
    """按优先级(CLI > extra > user > ORPHAN > REBUILD > whitelist > default > world)组装 RuleSet。

    纯参数签名(不依赖 argparse.Namespace):CLI 从 args 展开传参,
    pipeline.build_plan 直调亦可(GUI 复用)。

    Args:
        versions: 参与判定的版本名(展开 default 规则中的版本占位)。
        exclude: CLI 级临时规则 glob(本次按 never,对应 --exclude)。
        include: CLI 级临时规则 glob(本次按 must_migrate,对应 --include)。
        rule_files: 额外规则文件路径列表(对应 --rule)。
        mcmig_dir: .mcmig 目录(user rules.yaml 所在)。
        with_whitelist: 是否启用 whitelist 层(仅 plan 命令)。
        orphan_rules: orphan 规则(plan 与独立 diff 共用)。
        world_dirs: 动态探测的世界目录名(F34①,scan 时入快照);默认空=行为不变。

    Returns:
        (规则集, 规则加载警告列表)。

    rebuild 层对所有命令(scan/diff/plan)常开;whitelist 仅 plan 命令启用;
    orphan 规则在 plan 与独立 diff 命令启用(plan 与独立 diff 共用同一生成源);
    world 层垫底(仅 default 未命中的路径落此层,非法名跳过并计入警告)。
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
    # F34①:动态世界目录层(default 之下最低优先级——default never 规则可压过,
    # 用户/CLI 规则可覆盖;仅 default 未命中的路径落此层)
    world_layer: list[rules.Rule] = []
    for wd in world_dirs:
        if not wd or "/" in wd or "\\" in wd or wd in (".", ".."):
            errors.append(f"world_dirs: 非法目录名已跳过 {wd!r}")
            continue
        world_layer.append(
            rules.Rule(match=f"{escape_world_glob(wd)}/**", decide=rules.Category.MUST_MIGRATE,
                       reason="世界目录探测(F34:level-name/level.dat)", source="world"))
    rs = rules.RuleSet.from_layers(cli_rules, extra, user, orphan, rebuild, whitelist,
                                   default, world_layer)
    return rs, errors


def select_rules_dir(data_dir: Path, legacy_dir: Path | None) -> tuple[Path, list[str]]:
    """规则目录选择单点:新位置优先,旧位置只读回退,并存时明确提示不暗混(spec §3.1)。

    三类消费方共用(plan 的 build_plan、diff 的 run_diff、scan 与 migrate 守卫的
    CLI·GUI 调用方;W2.5 复审 B3 由私有 ``_rules_dir`` 转正):「diff 预演与
    plan 正片」必须同口径,规则目录选择不允许出现第二套逻辑。

    Args:
        data_dir: 生成物锚定 .mcmig 目录(game_root 侧,新规则位置)。
        legacy_dir: 旧布局 .mcmig 目录(通常 CWD 侧);None 或与 data_dir
            相同表示无回退。

    Returns:
        (选定的规则目录, 提示行列表):新位置存在→用之(旧位置并存则提示已忽略);
        仅旧位置存在→用旧位置+提示建议迁移;均无→新位置。
    """
    new, old = data_dir / "rules.yaml", (legacy_dir / "rules.yaml" if legacy_dir else None)
    notices: list[str] = []
    if new.exists() and old and old.exists():
        notices.append(f"[提示] 检测到旧规则 {old},已忽略(并存时以 {new} 为准),建议删除旧文件")
        return data_dir, notices
    if not new.exists() and old and old.exists():
        notices.append(f"[提示] 使用旧布局规则 {old},建议迁移至 {new}")
        return legacy_dir, notices  # type: ignore[return-value]
    return data_dir, notices


def _same_physical_root(
    src_snap: Snapshot, dst_snap: Snapshot, src_dir: Path, dst_dir: Path
) -> bool:
    """mtime 证据闸门统一口径:双侧快照均记录 resolved_root 则比记录值,
    任一缺失(旧 v1 快照)回退活目录 resolve 比较——build_plan 与 run_diff
    两处调用点同形(spec F35,W3 复审 #16)。

    Args:
        src_snap: 源侧快照(取 resolved_root 记录值)。
        dst_snap: 目标侧快照(取 resolved_root 记录值)。
        src_dir: 源侧活版本目录(fallback resolve 比较用)。
        dst_dir: 目标侧活版本目录(fallback resolve 比较用)。

    Returns:
        双侧是否同一物理根(同实例随时间演化 → mtime 证据可用)。
    """
    if src_snap.resolved_root is not None and dst_snap.resolved_root is not None:
        return src_snap.resolved_root == dst_snap.resolved_root
    return src_dir.resolve() == dst_dir.resolve()


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
    rules_dir: Path | None = None,  # 用户规则目录;None → 新旧位置自动选择(批次I-T1)
) -> tuple[MigrationPlan, list["CompatWarning"], list["ModPair"], dict[str, object]]:
    """plan 公共管线(plan 子命令与 swap 第三步共用,GUI 亦可直调)。

    流程:载入 src 快照 → dst 快照(rescan_dst=True 时现场重扫并落盘,否则载入已有)
    → orphan 规则 → ruleset → diff(modpack_swap) → planner → mod 兼容检查
    → 审阅摘要数据(extras) → 保存 plan。

    Args:
        cwd: 调用方工作目录(签名锚点;快照/rules/plans 路径已分别由
            mcmig_dir/plans_dir 参数化,本函数不直接使用 cwd)。
        game_root: 游戏根目录。
        src: 源版本名。
        dst: 目标版本名。
        modpack_swap: 换包模式(源独有 mod 视为旧包自带,不回迁)。
        rescan_dst: True 时重扫 dst 生成最新快照并落盘(swap 装包后必开)。
        save: 是否持久化 plan 文件。
        mcmig_dir: .mcmig 目录(旧布局快照/rules.yaml 回退位)。
        plans_dir: plan 目录(写为 plans_dir/<src>__<dst>.plan.json)。
        data_dir: 生成物锚定目录(F8 批次D):快照读/写均落 <data_dir>/snapshots/;
            None → 等于 mcmig_dir(GUI 兼容布局,行为不变)。mcmig_dir 侧快照仅作
            旧布局回退(命中时 warning 提示整体迁移)。
        exclude: CLI 级临时规则 glob(本次按 never)。
        include: CLI 级临时规则 glob(本次按 must_migrate)。
        rule_files: 额外规则文件路径列表。
        rules_dir: 用户规则目录(批次I-T1,spec §3.1);None → 按 select_rules_dir 在
            data_dir(新位置,如 <game_root>/.mcmig)与 mcmig_dir(旧布局回退)
            之间自动选择,选择结果非默认时发 warning 提示。

    Returns:
        (plan, compat_warnings, mod_pairs, extras):plan 与兼容警告同前;mod_pairs 为
        双源配对结果(批次F,渲染级 ⇄ 注记用;plan 文件持久化不含它,schema 零变化);
        extras 为审阅摘要数据(批次I-W3 T6,spec §5.1: buckets=origin 计数、
        total_bytes/ask_count=体积与待确认数、overwrite_count=带备份将覆盖数、
        client_only=已知客户端件命中、world_notices=世界目录改名提示)。

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
        registry_from_dicts,
        scan_mods,
    )

    src_dir = _version_dir(game_root, src)
    dst_dir = _version_dir(game_root, dst)
    # 两侧注册表各取一次(批次F 上移 src 扫描:orphan 规则用 dst_mods,配对用双侧,
    # compat 检查用 src_mods——不再后段重复扫描);批次H(消费策略 B):嵌入优先——
    # v2 快照自带名册(与 files 同刻同源)即取,v1 快照回退现扫;
    # rescan_dst=True 时 dst 快照刚经 scan_version 重扫为 v2 含嵌入,自然一致
    src_mods = registry_from_dicts(src_snap.mods) if src_snap.mods else scan_mods(src_dir)
    dst_mods = registry_from_dicts(dst_snap.mods) if dst_snap.mods else scan_mods(dst_dir)
    override = load_mod_config_map()
    orphan_rules = generate_orphan_rules(src_snap.files, dst_mods, override)
    # 批次I-T1:用户规则目录选择(新位置=生成物锚定位优先,旧布局回退,不暗混);
    # 显式 rules_dir 跳过选择;非默认选择/并存时逐行 warning 提示
    chosen_rules_dir, rule_notices = (
        (rules_dir, []) if rules_dir is not None else select_rules_dir(data, legacy)
    )
    for n in rule_notices:
        log.warning("%s", n)
    rs, errs = build_ruleset(
        [src, dst],
        exclude=list(exclude),
        include=list(include),
        rule_files=list(rule_files),
        mcmig_dir=chosen_rules_dir,
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
                    mtime_evidence=_same_physical_root(src_snap, dst_snap, src_dir, dst_dir)).diff()
    # 批次F:双源配对(与 run_diff 同一单点;plan 侧渲染 ⇄ 注记用,不进 plan.json);
    # 批次H:闸门收敛——双侧嵌入名册来自各自 scan 时刻,物理同目录不同刻也不恒等,配对有意义
    mod_pairs = compute_mod_pairs(
        src_snap, dst_snap, src_mods, dst_mods,
        same_dir=src_dir.resolve() == dst_dir.resolve()
        and not (bool(src_snap.mods) and bool(dst_snap.mods)),
    )
    src_index = {e.path: e for e in src_snap.files}
    plan = Planner(report, src_index).plan()
    plan.src, plan.dst = src, dst
    # 审阅摘要数据(批次I-W3 T6,spec §5.1):client_only 匹配与 run_diff 同一
    # 单点——ctx 经 resolve_diff_context 按快照记录的 game_root 解析(语义一致)
    from .moddb import load_client_mods

    client_modids, client_families = load_client_mods()
    client_only: set[str] = match_client_only_paths(
        report, resolve_diff_context(src_snap, dst_snap),
        client_modids, client_families)
    extras: dict[str, object] = {
        "buckets": plan.summary(),                # origin 计数(决策视角)
        "total_bytes": sum(
            a.src_size or 0 for a in plan.actions
            if a.behavior in (Behavior.COPY, Behavior.ASK)),
        "ask_count": sum(1 for a in plan.actions if a.behavior == Behavior.ASK),
        # spec §5.1 顶部摘要「将覆盖(有备份)数」(评审建议 C 补):带备份目标
        # 的动作=执行时目标同位文件会被移入备份
        "overwrite_count": sum(
            1 for a in plan.actions
            if a.behavior in (Behavior.COPY, Behavior.ASK) and a.backup_target),
        "client_only": sorted(client_only),
        "world_notices": world_rename_notices(src_snap, dst_snap),
    }
    # 版本兼容检查:对 mod_added 的 jar 检查 NeoForge 版本范围
    dst_nf_version = read_neoforge_version(dst_dir)
    mod_added_paths = [
        r.path for r in plan.actions if r.behavior == Behavior.COPY and r.origin == Origin.MOD_ADDED
    ]
    compat_warnings = check_mod_compat(mod_added_paths, src_mods, dst_nf_version)
    # 规划完成后、save 前:签发审阅守卫(spec §3.3)——计划生成时定格执行前置条件
    # (实例身份/双侧快照内容指纹/规则指纹/迁移模式),执行前由 validate_review 重验。
    # 快照取位:锚定位存在取锚定位(与执行侧校验同构);仅旧布局存在时取实际载入位
    # (守卫记录的是计划实际消费的快照);规则来源取**签发时实际消费的全部用户态
    # 来源**(本函数选定的规则目录 + --rule 额外文件,W2.6 复审 P2-3;内嵌数据层
    # 由 review._rule_guard_fingerprint 固定并入)
    guard_src = data / "snapshots" / f"{src}.snapshot.json"
    guard_dst = data / "snapshots" / f"{dst}.snapshot.json"
    plan.review = issue_review(
        game_root=game_root,
        snapshot_paths={
            src: guard_src if guard_src.is_file() else src_path,
            dst: guard_dst if guard_dst.is_file() else dst_path,
        },
        rule_sources=[chosen_rules_dir / "rules.yaml", *rule_files],
        modpack_swap=modpack_swap,
    )
    if save:
        try:
            plan.save(plans_dir / f"{src}__{dst}.plan.json")
        except OSError as e:
            # 不再吞:保存失败的计划不可进入可执行状态(封死「审新执旧」,spec §3.3)
            raise PlanPersistError(f"plan 文件写入失败: {e}") from e
    return plan, compat_warnings, mod_pairs, extras


def _load_guard_snapshots(
    plan: MigrationPlan, src_root: Path, legacy_dir: Path | None = None
) -> tuple[Snapshot, Snapshot] | None:
    """按「锚定优先+旧布局回退」加载双侧快照,供审阅状态校验(W2.6 复审 P1-1)。

    取位与签发(build_plan 的 find_snapshot)及哈希重验(CLI/GUI 的
    validate_review)同构:锚定位存在取锚定位,否则回退 legacy_dir(旧 CWD
    布局)——修复前只认锚定布局,缺失即静默跳过状态校验,守卫材料三处取位
    不同构(reviewer 复现:旧布局下签发后改目标文件,无 --force 仍被覆盖)。

    Args:
        plan: 迁移计划(src/dst 字段定位快照文件)。
        src_root: 源版本根目录(game_root/versions/<src>;上两级即 game_root)。
        legacy_dir: 旧布局 .mcmig 目录(通常 CWD 侧);None 表示无回退。

    Returns:
        (src_snap, dst_snap);任一快照缺失或损坏返回 None——调用方按
        ``plan.review`` 是否存在裁定:有守卫缺材料须阻断(fail-closed),
        合成/旧版计划(review=None)降级跳过。
    """
    anchored = src_root.parent.parent / ".mcmig"
    src_p = find_snapshot(anchored, legacy_dir, plan.src)[0]
    dst_p = find_snapshot(anchored, legacy_dir, plan.dst)[0]
    if not (src_p.is_file() and dst_p.is_file()):
        return None
    try:
        return Snapshot.load(src_p), Snapshot.load(dst_p)
    except Exception as e:  # noqa: BLE001 — 快照损坏同缺失:材料不可用,交调用方按 review 裁定
        log.warning("[警告] 审阅状态校验所需快照不可读(%s / %s): %s", src_p, dst_p, e)
        return None


@dataclass(frozen=True)
class PrecheckOutcome:
    """执行前置检查结论:守卫+预检+状态校验三段合一的统一出口。

    Attributes:
        blockers: 按检查序聚合的阻断项(守卫→预检→状态,行为变更白名单①:
            多阻断并存时首错优先序统一为「守卫→预检→状态校验」)。
        warnings: 预检降级警告(决策放行后的提示,与被放行的检查项同 code)。
        review_missing: plan.review is None(旧版计划,调用方发「旧版计划」提示)。
    """

    blockers: list[PreflightBlocker]
    warnings: list[PreflightWarning]
    review_missing: bool


def precheck_execution(
    plan: MigrationPlan, game_root: Path, src: str, dst: str, *,
    legacy_dir: Path | None = None,
    decisions: ExecutionDecisions | None = None,
) -> PrecheckOutcome:
    """执行前置检查单点(CLI/GUI 平级消费;调用方须已持实例锁,spec §4.2)。

    检查序(行为变更白名单①):① 审阅守卫 validate_review(快照取 find_snapshot
    实际命中位,规则经 select_rules_dir 同参复算,--force/accept_stale 放行
    snapshot_changed);② 共享预检 preflight_execute;③ 首跑审阅状态校验
    (rerun_executed 决策跳过;有守卫缺材料 fail-closed=review_snapshot_missing)。

    Args:
        plan: 已加载的迁移计划(review/executed_at 以内存对象为准)。
        game_root: 游戏根目录(含 versions/)。
        src: 源版本名。
        dst: 目标版本名。
        legacy_dir: 旧布局 .mcmig 目录(快照/规则回退位);None 表示无回退。
        decisions: 执行预检三项显式决策;None 等于全 False(GUI 严格默认),
            CLI 的 --force 映射为三项全 True。

    Returns:
        PrecheckOutcome:blockers 非空时不得执行;warnings 为决策放行后的
        降级提示;review_missing 提示旧版计划(渐进采用,不阻断)。
    """
    blockers: list[PreflightBlocker] = []
    warnings: list[PreflightWarning] = []
    data_dir = game_root / ".mcmig"
    # ① 审阅守卫:快照取 find_snapshot 实际命中位(锚定优先+旧布局回退,
    # 与签发侧同构);规则目录同参复算(validate_review 内以 review.rule_sources
    # 为权威,此处列表仅作无记录键旧守卫的回退)
    if plan.review is not None:
        chosen_rules_dir, _notices = select_rules_dir(data_dir, legacy_dir)
        guard_blockers = validate_review(
            plan, game_root=game_root,
            snapshot_paths={
                src: find_snapshot(data_dir, legacy_dir, src)[0],
                dst: find_snapshot(data_dir, legacy_dir, dst)[0],
            },
            rule_sources=[chosen_rules_dir / "rules.yaml"],
        )
        if decisions is not None and decisions.accept_stale:
            guard_blockers = [b for b in guard_blockers if b.code != "snapshot_changed"]
        blockers.extend(guard_blockers)
    # ② 共享执行预检(已执行/快照过期/目录缺失/疑似占用)
    pre_blockers, warnings = preflight_execute(
        plan, game_root, src, dst, decisions=decisions,
        data_dir=data_dir, legacy_dir=legacy_dir)
    blockers.extend(pre_blockers)
    # ③ 首跑审阅状态校验:重跑决策跳过;有守卫缺材料 fail-closed
    # (review=None 的旧版计划跳过——渐进采用语义,由调用方决定是否经
    # execute_migration 的内部校验兜底)
    rerun = decisions is not None and decisions.rerun_executed
    if plan.review is not None and not rerun:
        src_root = game_root / "versions" / src
        snaps = _load_guard_snapshots(plan, src_root, legacy_dir=legacy_dir)
        if snaps is not None:
            blockers.extend(check_action_states(
                plan.actions, snaps[0], snaps[1],
                src_root, game_root / "versions" / dst))
        else:
            blockers.append(PreflightBlocker(
                "review_snapshot_missing", MSG_REVIEW_SNAPSHOT_MISSING))
    return PrecheckOutcome(blockers, warnings, plan.review is None)


def execute_migration(
    plan: MigrationPlan,
    src_root: Path,
    dst_root: Path,
    ask_yes: set[str],
    dry_run: bool = False,
    progress_cb: Callable[[FileResult], None] | None = None,
    validate_states: bool = True,
    *,
    should_cancel: Callable[[], bool] | None = None,
    before_action: Callable[[ActionRecord], None] | None = None,
    after_action: Callable[[ActionRecord, FileResult], None] | None = None,
    legacy_dir: Path | None = None,
) -> list[FileResult]:
    """执行迁移计划(三道预检 + 审阅状态校验 + Executor 封装,CLI 与 GUI 平级消费)。

    预检序列(执行前):
    1. clean_stale_tmp(dst_root):清理上次崩溃残留的 *.mcmig-tmp(有写盘副作用,
       dry-run 跳过以守住零写盘契约)
    2. check_disk_space(dst_root, Σ COPY + 已确认 ASK 动作源文件大小):不足抛
       DiskSpaceError,此时零写盘(模块级函数引用,便于 GUI/测试注入替身);
       批次I-T2 起 ASK 命中 ask_yes 即将真实写盘,须计入口径
    3. check_action_states(批次I-T3,spec §3.3):待执行动作的源/目标当前状态 vs
       快照记录状态,失配抛 ReviewStateError(零写盘);重跑(rerun_executed 明确
       决策)由调用方传 validate_states=False 跳过,依赖 identical 短路与 job
       journal——行为集中一处,CLI/GUI 平级;快照取位锚定优先+旧布局回退
       (legacy_dir,W2.6 复审 P1-1),**有审阅守卫却取不到校验材料时阻断**
       (review_snapshot_missing;review=None 的合成/旧版计划仍降级跳过)

    Args:
        plan: 已审阅的迁移计划。
        src_root: 源版本根目录。
        dst_root: 目标版本根目录。
        ask_yes: 预收集的 ASK 确认路径集合(命中即迁移,未命中按 asked_no 跳过)。
        dry_run: True 时零写盘,结果为推演(tmp 清理随之跳过;磁盘预检只读仍执行)。
        progress_cb: 逐文件结果实时回调——注入 Executor.execute,单文件完成即同步
            调用(GUI 进度条数据源);None 时无回调,行为不变。
        validate_states: 是否执行审阅状态校验(默认 True);False 仅供重跑路径
            (调用方 rerun_executed 决策),首跑必须校验。
        should_cancel: 取消检查点,透传 Executor.execute(GUI 取消按钮接线,
            批次I-T6);None 时行为不变。
        before_action: 动手动作前置回调(①意图 write-ahead),透传 Executor.execute。
        after_action: 动作完成回调(③完成持久化),透传 Executor.execute。
        legacy_dir: 旧布局 .mcmig 目录(审阅状态校验的快照回退位;与 CLI
            守卫的 find_snapshot 同构,W2.6 复审 P1-1);None 表示仅查锚定布局。

    Returns:
        逐文件执行结果(按 plan.actions 顺序)。

    Raises:
        DiskSpaceError: 目标磁盘剩余空间不足(预检失败,零写盘)。
        ReviewStateError: 审阅状态校验失配(源/目标与审阅时不一致,零写盘)。
    """
    if not dry_run:
        removed = clean_stale_tmp(dst_root)
        if removed:
            log.info("[预检] 已清理 %d 个残留临时文件(*.mcmig-tmp)", removed)
    # 磁盘预检:按 COPY + ask_yes 命中的 ASK 动作源文件大小求和(批次I-T2 口径,
    # 已确认 ASK 与 COPY 同样将真实写盘;identical 会零写盘,偏保守无害)
    needed = sum(
        a.src_size or 0 for a in plan.actions
        if a.behavior == Behavior.COPY or (a.behavior == Behavior.ASK and a.path in ask_yes)
    )
    check_disk_space(dst_root, needed)
    # 审阅状态校验(只读,零写盘):快照可得才校验;GUI 侧已在 job 内先行校验过
    # 的场景也经 validate_states=False 显式跳过,避免双侧快照重复全量哈希。
    # W2.6 复审 P1-1:快照取位锚定优先+旧布局回退(legacy_dir,与签发/重验同构);
    # **有审阅守卫却取不到校验材料 → 阻断**(不可校验≠可执行),仅 review=None
    # 的合成/旧版计划保留降级跳过(直调兼容语义)。
    if validate_states:
        snaps = _load_guard_snapshots(plan, src_root, legacy_dir=legacy_dir)
        if snaps is not None:
            src_snap, dst_snap = snaps
            blockers = check_action_states(plan.actions, src_snap, dst_snap, src_root, dst_root)
            if blockers:
                raise ReviewStateError(blockers)
        elif plan.review is not None:
            raise ReviewStateError([
                PreflightBlocker("review_snapshot_missing", MSG_REVIEW_SNAPSHOT_MISSING)
            ])

    def ask(a: ActionRecord) -> bool:
        """ASK 动作决策:路径在预确认集合内即迁移。"""
        return a.path in ask_yes

    return Executor(plan, src_root, dst_root, ask).execute(
        dry_run=dry_run, progress_cb=progress_cb,
        should_cancel=should_cancel, before_action=before_action,
        after_action=after_action,
    )


@dataclass(frozen=True)
class DiffContext:
    """独立 diff 的扫描上下文:两侧 mod 注册表 + 真实版本目录(F2/F4/F12 共同基座)。"""

    src_mods: "ModRegistry"
    dst_mods: "ModRegistry"
    src_dir: Path
    dst_dir: Path
    same_dir: bool = False  # 两侧版本目录 resolve 后同路径(junction 同体)→ 注册表配对不可信
    # 批次H(消费策略 B):双侧名册来自快照嵌入(v2)→ 配对闸门收敛为
    # same_dir and not mods_frozen;read_file 的 same_dir 短路语义不变——
    # 物理同目录读数恒等是内容层事实,嵌入名册救不了内容读取
    mods_frozen: bool = False
    # 目录活性单点(终审递延 Minor):resolve_diff_context 判定时刻双侧版本目录
    # 是否可达——调用方(run_diff 嵌入名册提示行等)消费此字段,不再二次探活
    dirs_live: bool = True

    def pairing_trusted(self) -> bool:
        """注册表配对是否可信(批次H 终审建议:分散布尔闸门收敛为单点)。

        物理同目录(same_dir)且名册非冻结(两侧现扫)时配对不可信;
        冻结通道嵌入名册来自各自 scan 时刻,同体不同刻配对仍有意义。
        """
        return not (self.same_dir and not self.mods_frozen)

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
    """从两份快照解析 diff 上下文:双侧 v2 嵌入走冻结通道,其余走 v1 路径。

    冻结通道(双侧 mods 嵌入,时刻对称才走):名册取 registry_from_dicts(嵌入),
    目录可达与否不影响 mods 来源;可达性判定记入 ctx.dirs_live(单点,调用方
    勿重复探活);same_dir 在活体双侧可达时按 resolve() 比较,
    否则按快照 resolved_root(双侧均有且相等 → True)。
    v1 组合(任一侧无嵌入,含混合 v1/v2——保时刻对称不做单侧嵌入):
    活体双侧可达 → 现扫建 ctx;任一不可达 → None(降级为纯快照对比,
    与既有 v1 行为逐字节一致)。版本目录 = <game_root>/versions/<version>,
    服务端 NTFS Junction 影子根同样成立。

    Args:
        src_snap: 源侧快照。
        dst_snap: 目标侧快照。

    Returns:
        DiffContext,或 None(字段缺失且无嵌入/活体不可达)。
    """
    for snap in (src_snap, dst_snap):
        # version 空串同样守卫:"" 参与 Path 拼接会折叠成 versions/ 目录本身
        if not snap.game_root or not snap.version:
            return None
    src_vdir = Path(src_snap.game_root) / "versions" / src_snap.version
    dst_vdir = Path(dst_snap.game_root) / "versions" / dst_snap.version
    from .moddb import registry_from_dicts, scan_mods  # 延迟导入避免循环
    if src_snap.mods and dst_snap.mods:                      # 冻结通道(双侧 v2)
        live = src_vdir.is_dir() and dst_vdir.is_dir()
        same_dir = (src_vdir.resolve() == dst_vdir.resolve() if live else
                    src_snap.resolved_root is not None
                    and src_snap.resolved_root == dst_snap.resolved_root)
        return DiffContext(
            src_mods=registry_from_dicts(src_snap.mods),
            dst_mods=registry_from_dicts(dst_snap.mods),
            src_dir=src_vdir, dst_dir=dst_vdir,
            same_dir=same_dir, mods_frozen=True, dirs_live=live)
    if not (src_vdir.is_dir() and dst_vdir.is_dir()):        # v1 路径:逐字节现状
        return None
    return DiffContext(
        src_mods=scan_mods(src_vdir), dst_mods=scan_mods(dst_vdir),
        src_dir=src_vdir, dst_dir=dst_vdir,
        same_dir=src_vdir.resolve() == dst_vdir.resolve())


def diff_identity_notices(
    src_path: Path, dst_path: Path,
    src: Snapshot, dst: Snapshot, ctx: DiffContext | None,
) -> str | None:
    """自比对/junction 同体检测(单点,live 与复放同源)。

    主判:两侧为同一快照文件(自比对错误用法);
    回退:live 模式 ctx.same_dir + 同刻且非冻结通道(终审:mods_frozen 下嵌入名册
      仍可用,注册表配对实际已恢复,发降级文案自相矛盾;F27 现状,旧快照无
      resolved_root 也走此臂,文案逐字节保留——活体证据优先于快照落盘字段,
      故先于佐证臂判定);
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
    # 回退臂(live):junction 同体 + 同刻 → F27 现行文案;批次H 终审:冻结通道
    # (mods_frozen)下 run_diff 的配对闸门已收敛为 same_dir and not mods_frozen,
    # 注册表配对实际存活,此臂同步收敛(文案一字不改,仅触发条件随闸门)——
    # v1 路径 mods_frozen 恒 False,行为恒等
    if ctx is not None and not ctx.pairing_trusted() and same_time:
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


# 微小世界不评估改名:文件过少时 min(双侧文件数) 分母占比失真,易假警报
_MIN_WORLD_FILES = 5   # 拍脑袋下限,r14 语料可再标定(spec §7 妥协 6)


def world_rename_notices(src: Snapshot, dst: Snapshot) -> list[str]:
    """世界目录改名探测(F34②):src 独有世界 A → dst 独有世界 B,同路径同尺寸
    占比 ≥0.9 时发提示(仅提示不重分类——假警报降级为可见解释)。

    候选:A ∈ src.world_dirs 且 ∉ dst.world_dirs(旧路径消失),B 反之(新路径出现);
    匹配 = 相对子路径双侧存在且 size 相等;占比分母 min(双侧文件数),
    ≥_MIN_WORLD_FILES(5)件才评估——阈值来源=拍脑袋下限,r14 语料可再标定
    (spec §7 妥协 6),微小世界占比失真故不发。
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
            if denom < _MIN_WORLD_FILES:
                continue
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
    run_diff 传 not ctx.pairing_trusted()(批次H:冻结通道嵌入名册来自各自 scan
    时刻,物理同目录不同刻也不恒等,配对有意义);build_plan 传
    <src_dir>.resolve()==<dst_dir>.resolve()。

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


# client_only 警示行最多列示件数(超出截断为「…等 N 件」,仅影响 stderr 行)
_CO_MAX_SHOWN = 5


def _format_client_warning(client: set[str]) -> str:
    """client_only 警示行文案:≤5 全列,>5 截断+计数(仅 stderr,JSON 与集合不变)。"""
    shown = sorted(client)
    parts = ", ".join(shown[:_CO_MAX_SHOWN]) + (
        f" …等 {len(shown)} 件" if len(shown) > _CO_MAX_SHOWN else "")
    return (f"[警示] {len(client)} 件已知客户端 mod(专服启动部署风险,F30): " + parts)


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
    rules_dir: Path | None = None,  # None → select_rules_dir 自动选择(W2.5 复审 B3)
) -> DiffOutcome:
    """diff 公共管线(自 cli._cmd_diff 整体搬移,编排逻辑不改;CLI 与 GUI 平级消费)。

    流程:快照定位(game_root 给出 → find_snapshot 锚定优先+旧 CWD 布局回退,
    legacy 命中提示入 notices;game_root=None → <mcmig_dir or cwd/.mcmig>/snapshots
    直取,无回退)→ load → resolve_diff_context(无条件,按快照内记录的 game_root
    判定活体可达;v1 不可达提示入 notices,双侧 v2 嵌入走冻结通道——不可达时
    换发嵌入提示行)→ build_ruleset → Differ(modpack_swap)
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
        mcmig_dir: .mcmig 目录(快照旧布局回退位);None → cwd/.mcmig。
        game_root: 游戏根目录;仅决定快照文件定位(锚定+旧布局回退),
            None → <mcmig_dir or cwd/.mcmig> 直取。活体 ctx 与孤儿/配对是否
            降级由快照内记录的 game_root 是否可达决定(与下沉前 CLI 逐字节一致)。
        rules_dir: 用户规则目录(与 build_plan 同参;None → game_root 可解析时
            经 select_rules_dir 在 <game_root>/.mcmig(新位置)与 rules_base
            (旧布局回退)间自动选择,提示入 notices;game_root=None(夹具
            复放)时 rules_base 直取,0.6.x 行为)。

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
    # 规则目录(W2.5 复审 B3,spec §3.1 T1 全入口):与 build_plan 同经
    # select_rules_dir 单点(新位置优先+旧布局只读回退+并存提示入 notices),
    # diff 预演与 plan 正片口径一致;rules_base 仍专责快照的旧布局回退,
    # 两职责解耦。game_root=None(夹具复放)无新位置概念,rules_base 直取
    chosen_rules_dir = rules_dir
    if chosen_rules_dir is None:
        if game_root is not None:
            chosen_rules_dir, rule_notices = select_rules_dir(
                data_dir, rules_base if rules_base != data_dir else None)
            notices.extend(rule_notices)
        else:
            chosen_rules_dir = rules_base
    missing = [n for n, p in ((src, src_path), (dst, dst_path)) if not p.exists()]
    if missing:
        raise FileNotFoundError(f"缺少 {', '.join(missing)} 快照")
    try:
        src_snap = Snapshot.load(src_path)
        dst_snap = Snapshot.load(dst_path)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"快照读取失败: {e}") from e
    from .moddb import generate_orphan_rules, load_mod_config_map

    # 扫描上下文(F2/F4/F12 基座):无条件按快照内记录的 game_root 解析活体目录
    # (I-1:与下沉前 CLI 逐字节一致——game_root 参数只管快照定位/旧布局回退,
    # 不管 ctx;CLI 无 game_root 但快照指向活体根(legacy 布局用户)时配对/孤儿
    # 照常生效);任一侧不可达(跨机复放/夹具)且无双侧嵌入 → ctx=None,降级为
    # 纯快照对比;双侧 v2 嵌入 → 冻结通道(ctx 存活,名册即嵌入,见 resolve_diff_context)
    ctx = resolve_diff_context(src_snap, dst_snap)
    orphan_rules: list[rules.Rule] = []
    if ctx is not None:
        # F2 孤儿规则:与 pipeline.build_plan 完全同源(src config × dst 注册表 × 覆盖表)
        orphan_rules = generate_orphan_rules(src_snap.files, ctx.dst_mods, load_mod_config_map())
        # 批次H:嵌入+目录不可达 → 名册照常,仅语义复核退字节比较(向用户说明缺席原因);
        # 可达性取 resolve_diff_context 的单点判定 ctx.dirs_live,不二次探活
        if ctx.mods_frozen and not ctx.dirs_live:
            notices.append("[提示] 版本目录不可达,已使用快照内嵌 mod 名册(语义复核退回字节比较)")
    else:
        notices.append(
            "[提示] mods 扫描不可用(game_root 不可达):孤儿标注与注册表配对已跳过,文件名配对仍可用"
        )
    rs, errs = build_ruleset(
        [src, dst],
        exclude=list(exclude),
        include=list(include),
        rule_files=list(rule_files),
        mcmig_dir=chosen_rules_dir,
        orphan_rules=orphan_rules,
        world_dirs=sorted(set(src_snap.world_dirs) | set(dst_snap.world_dirs)),  # F34① 双侧并集
    )
    for e in errs:
        print(f"[规则警告] {e}")  # stdout,与下沉前 CLI 逐字节一致
    clf = Classifier(rs)
    # F12/F16: content_reader 注入语义复核(.properties/.json/.toml)
    # F19: modpack_swap 透传 differ(与 plan/swap 流程同源,换装验收旧 jar 归换包排除)
    # F35:mtime 证据闸门——两侧快照同物理根(同实例随时间演化)才开;
    # 跨实例(复制必变 mtime)恒关,杜绝假阳性;口径与 build_plan 同形
    # (双侧记录值优先,任一缺失回退活目录 resolve,W3 复审 #16)
    src_vdir = Path(src_snap.game_root) / "versions" / src_snap.version
    dst_vdir = Path(dst_snap.game_root) / "versions" / dst_snap.version
    mtime_ev = _same_physical_root(src_snap, dst_snap, src_vdir, dst_vdir)
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
    # 同步收敛:冻结通道 same_dir 不同刻也不 debug 降级语义(注册表配对实际存活,
    # debug 文案与真实行为同样矛盾;v1 路径 mods_frozen 恒 False,行为恒等)
    elif ctx is not None and not ctx.pairing_trusted():
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
        # 批次H:闸门收敛——嵌入名册来自各自 scan 时刻,物理同目录不同刻也不恒等,配对有意义
        same_dir=(not ctx.pairing_trusted()) if ctx is not None else False,
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
        notices.append(_format_client_warning(client))
    return DiffOutcome(
        report=report, mod_pairs=mod_pairs, src=src_snap, dst=dst_snap,
        notices=notices, client_only_paths=client,
    )


# ---------------------------------------------------------------------------
# 换包(swap)编排:预检 / 装包 / 全流程(自 cli.py 下沉,批次I-W3 T7,spec §8)
# ---------------------------------------------------------------------------


def _md5(path: Path) -> str | None:
    """计算文件 MD5(装包阶段同名冲突判定用);不可读返回 None。"""
    return md5_of(path)


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


def _swap_fingerprint(game_root: Path, src: str, dst: str, new_pack: Path,
                      dst_mods: Path, new_mods: Path) -> str:
    """两阶段输入指纹:参与决策的全部输入 → sha256(评审 P2-3 收口)。

    绑定面:规范化实例身份(game_root.resolve,消 junction/别名——跨游戏根的
    同名版本对必失配)+ 版本对 + 新包路径 + 目标 <dst>.json 内容(NeoForge
    兼容判定的输入)+ 两侧 mods jar 名与 MD5 清单。
    """
    h = hashlib.sha256()
    h.update(f"R|{game_root.resolve()}|{src}|{dst}|{new_pack.resolve()}".encode("utf-8"))
    ver_json = game_root / "versions" / dst / f"{dst}.json"
    if ver_json.is_file():
        h.update(f"V|{file_sha256(ver_json)}".encode("utf-8"))
    for jar in sorted(dst_mods.glob("*.jar")):
        h.update(f"D|{jar.name}|{_md5(jar)}".encode("utf-8"))
    for jar in sorted(new_mods.glob("*.jar")):
        h.update(f"N|{jar.name}|{_md5(jar)}".encode("utf-8"))
    return h.hexdigest()


@dataclass(frozen=True)
class SwapPreflightOutcome:
    """换包预检结论(只读;CLI 中止文案与 GUI 两阶段第一阶段的共同数据源)。

    Attributes:
        error: 致命错(缺版本 json/缺源快照)→ CLI rc=2 文案;None 表示可继续。
        incompat: NeoForge 不兼容清单。
        extras: 目标有而新包无的 jar(装包后将残留)。
        conflicts: 同名不同内容 jar(待覆盖决策)。
        preflight_id: 输入指纹(sha256,仅只读预检产出;GUI apply 持锁重验用)。
    """

    error: str | None
    incompat: list[str]
    extras: list[str]
    conflicts: list[str]
    preflight_id: str | None


@dataclass(frozen=True)
class SwapInstallOutcome:
    """换包装包结论(计数语义与下沉前 CLI 一致)。

    Attributes:
        copied: 复制数(含冲突覆盖件)。
        skipped: 同名同 MD5 跳过数。
        conflicted: 同名不同内容计数(无论覆盖决策)。
        backed_up: 被覆盖前备份的 jar 名单。
        backup_dir: 备份目录(调用方传入的 backup_root;dry-run 为 None)。
        cancelled: 逐 jar 取消检查点命中(spec §4.3 覆盖 swap 装包)。
        error: 非中断性失败原因(空间预检不过/中途文件操作或 journal 写失败,
            0.12.0 复审#5/#6)——**不再上抛**:已完成的计数与备份名单保留在
            本返回值,调用方据此呈现「已改动什么、原件在哪」;None=正常。
    """

    copied: int
    skipped: int
    conflicted: int
    backed_up: list[str]
    backup_dir: Path | None
    cancelled: bool = False
    error: str | None = None


@dataclass(frozen=True)
class SwapRunOutcome:
    """run_swap 全流程结构化结果(评审 v3 契约A:CLI 既有输出所需信息全量回传;
    装包成功而规划失败时,install 保留——用户须知目标 mods/ 已被修改)。

    Attributes:
        rc: 0=成功/用户拒绝继续;2=预检失败/规划失败。
        preflight: 预检结构化结果(error/incompat/extras/conflicts)。
        install: 装包结果;规划失败时同样保留。
        compat_warnings: 规划期兼容警告。
        plan_summary: plan.summary()(规划成功时)。
        plan_file: 计划文件路径(「审阅后 migrate」提示行)。
        plan_error: 规划失败原因(install 已完成的情形)。
    """

    rc: int
    preflight: SwapPreflightOutcome | None
    install: SwapInstallOutcome | None
    compat_warnings: tuple[str, ...] = ()
    plan_summary: dict[str, int] | None = None
    plan_file: Path | None = None
    plan_error: str | None = None


def swap_preflight(game_root: Path, dst: str, new_pack: Path, *,
                   src: str, legacy_dir: Path | None = None) -> SwapPreflightOutcome:
    """换包预检(只读):NeoForge 兼容 + 源快照存在性 + extras/conflicts + 输入指纹。

    Args:
        game_root: 游戏根目录。
        dst: 目标版本名(读 <dst>.json 判 NeoForge 兼容;缺失为致命错)。
        new_pack: 新整合包目录(含 mods/)。
        src: 源版本名(仅用于源快照存在性检查——规划步依赖,装包前检查)。
        legacy_dir: 旧布局 .mcmig 目录(源快照回退位);None 表示仅查锚定布局。

    Returns:
        SwapPreflightOutcome:error 非 None 为致命错(缺版本 json/缺源快照,调用方
        按文案中止);incompat 为不兼容清单;extras 为目标有而新包无的 jar;
        conflicts 为同名不同内容 jar(待覆盖决策);preflight_id 为输入指纹
        (仅无致命错时产出,GUI apply 持锁重验用)。
    """
    dst_dir = _version_dir(game_root, dst)
    err, bad = _swap_preflight(dst_dir, new_pack)
    if err is not None:
        return SwapPreflightOutcome(error=err, incompat=[], extras=[],
                                    conflicts=[], preflight_id=None)
    # 预检:src 快照必须已存在(规划步依赖;装包前检查,dry-run 同样生效)
    src_snap = find_snapshot(game_root / ".mcmig", legacy_dir, src)[0]
    if not src_snap.exists():
        return SwapPreflightOutcome(
            error=f"缺少源版本快照 {src_snap}\n请先运行: mcmig scan {src}",
            incompat=[], extras=[], conflicts=[], preflight_id=None)
    dst_mods = dst_dir / "mods"
    new_mods = new_pack / "mods"
    new_names = {p.name for p in new_mods.glob("*.jar")}
    existing = {p.name for p in dst_mods.glob("*.jar")} if dst_mods.is_dir() else set()
    extras = sorted(existing - new_names)
    conflicts = sorted(
        n for n in existing & new_names
        if _md5(dst_mods / n) != _md5(new_mods / n)
    )
    fingerprint = _swap_fingerprint(game_root, src, dst, new_pack, dst_mods, new_mods)
    return SwapPreflightOutcome(error=None, incompat=bad, extras=extras,
                                conflicts=conflicts, preflight_id=fingerprint)


def _probe_existing_dir(p: Path) -> Path:
    """沿祖先找到第一个存在的目录(空间探测基准;全不存在时返回 anchor)。"""
    probe = p
    while not probe.exists() and probe != probe.anchor:
        probe = probe.parent
    return probe


def _swap_space_error(dst_mods: Path, actions: list[tuple[Path, Path, bool]],
                      backup_root: Path) -> str | None:
    """装包前磁盘空间预检(0.12.0 复审#6),不足返回中文原因(None=通过)。

    写入需求=将复制 jar 大小之和;备份需求=将被覆盖目标大小之和。目标卷与
    备份卷**同卷合并**计算、**异卷分别**检查(fsops.check_disk_space 复用,
    探测基准沿祖先找现有目录)。首个装包动作前拦截,避免部分文件已改写后
    才发现空间不足。
    """
    need_write = sum(j.stat().st_size for j, _t, _o in actions)
    need_backup = sum(t.stat().st_size for _j, t, o in actions if o)
    if need_write == 0 and need_backup == 0:
        return None
    try:
        dst_probe = _probe_existing_dir(dst_mods)
        bak_probe = _probe_existing_dir(backup_root)
        same_volume = os.stat(dst_probe).st_dev == os.stat(bak_probe).st_dev
        if same_volume:
            check_disk_space(dst_mods, need_write + need_backup)
        else:
            check_disk_space(dst_mods, need_write)
            check_disk_space(backup_root, need_backup)
    except FsOpsError as e:  # DiskSpaceError 是其子类
        return e.why
    return None


def swap_install(dst_mods: Path, new_mods_dir: Path, *, overwrite: set[str],
                 dry_run: bool, backup_root: Path,
                 journal: JobJournal | None = None,
                 should_cancel: Callable[[], bool] | None = None,
                 progress_cb: Callable[[str, int, int], None] | None = None) -> SwapInstallOutcome:
    """将新包 mods/*.jar 装入目标 mods/(覆盖前备份 + write-ahead journal + 取消检查点)。

    逐 jar:取消检查点命中即停(安全边界=当前 jar 已完成为准,与 executor
    取消同型),cancelled=True;identical(同名同 MD5)零写盘且不记 journal
    意图;同名不同内容未选中覆盖者保留目标原件。

    失败不上抛(0.12.0 复审#5):空间预检不过(首个动作前,零写盘)或中途
    文件操作/journal 写失败,以 ``SwapInstallOutcome.error`` 返回**已知部分
    结果**(copied/backed_up 保留),调用方据此呈现已改动内容与备份位置。

    Args:
        dst_mods: 目标版本 mods/ 目录。
        new_mods_dir: 新包 mods/ 目录(不存在时零动作,返回全 0)。
        overwrite: 预收集的覆盖决策(选中覆盖的冲突 jar 名集合;CLI 由
            Confirm 回调收集,GUI 由勾选收集——决策与执行解耦)。
        dry_run: True 时零写盘,仅计数(journal 亦不挂——调用方不建)。
        backup_root: 覆盖备份目录(调用方传入,形如
            <game_root>/.mcmig/backups/swap/<UTC 时间戳>;被覆盖 jar 先备份到
            backup_root/<jar 名> 再写入,事务复制见 fsops.copy_atomic)。
        journal: write-ahead journal;None 表示不记录(①意图→复制→③完成,
            含备份相对位与备份根,崩溃后经 scan_interrupted 呈现「待核对」)。
        should_cancel: 逐 jar 取消检查点;None 时不可取消(CLI 通道)。

    Returns:
        SwapInstallOutcome(copied 含冲突覆盖件,conflicted 计全部同名冲突
        无论决策——计数语义与下沉前 CLI 逐字对拍;error 见类 docstring)。
    """
    copied = skipped = conflicted = 0
    backed_up: list[str] = []
    cancelled = False
    error: str | None = None
    if not new_mods_dir.is_dir():
        return SwapInstallOutcome(0, 0, 0, backed_up, None, False)
    # 预扫描(0.12.0 复审#5/#6):一次判定 identical/冲突/将写清单,供空间
    # 预检与执行段共用;取消检查点仍在每个写动作前
    actions: list[tuple[Path, Path, bool]] = []
    for jar in sorted(new_mods_dir.glob("*.jar")):
        target = dst_mods / jar.name
        will_overwrite = False
        if target.exists():
            if _md5(target) == _md5(jar):
                skipped += 1
                continue  # identical:零写盘,不记意图
            conflicted += 1
            if jar.name not in overwrite:
                continue  # 决策=保留目标
            will_overwrite = True
        actions.append((jar, target, will_overwrite))
    if not dry_run and actions:
        space_err = _swap_space_error(dst_mods, actions, backup_root)
        if space_err is not None:
            return SwapInstallOutcome(0, skipped, conflicted, backed_up, None,
                                      False, space_err)
    for jar, target, will_overwrite in actions:
        if should_cancel is not None and should_cancel():
            cancelled = True
            break  # 安全边界:当前 jar 之前的工作均已完成
        if not dry_run:
            if journal is not None:
                # 意图含备份根(0.12.0 复审#5):崩溃后中断清单能直接指出
                # 「原件在哪个备份目录」,不必让用户反查 jobs 目录
                try:
                    journal.record_intent(
                        jar.name,
                        {"op": "install",
                         "backup": jar.name if will_overwrite else None,
                         "backup_root": str(backup_root)})
                except JournalError as e:
                    error = f"装包日志写入失败,已停止装包:{e}"
                    break
            try:
                if copy_atomic(jar, target, rel=jar.name, backup_dir=backup_root):
                    backed_up.append(jar.name)
            except (FsOpsError, OSError) as e:
                why = getattr(e, "why", None) or str(e)
                error = f"装包文件操作失败({jar.name}):{why}"
                break
            # 物理复制已完成即计入——完成记录失败不抹除「文件已在盘上」事实
            copied += 1
            if progress_cb is not None:
                progress_cb(jar.name, copied, len(actions))  # 逐 jar 进度(T13.5)
            if journal is not None:
                try:
                    journal.record_completion(jar.name)
                except JournalError as e:
                    error = f"装包日志写入失败,已停止装包:{e}"
                    break
        else:
            copied += 1          # dry-run:只计数
    backup_dir = None if dry_run else backup_root
    return SwapInstallOutcome(copied, skipped, conflicted, backed_up, backup_dir,
                              cancelled, error)


def run_swap(cwd: Path, game_root: Path, src: str, dst: str, new_pack: Path, *,
             confirm_extras: Callable[[list[str]], bool],
             resolve_conflict: Callable[[str], bool],
             force: bool = False,
             dry_run: bool = False,
             progress_cb: Callable[[str, int, int], None] | None = None) -> SwapRunOutcome:
    """换包全流程编排:预检 → 交互决策(extras 确认/冲突覆盖)→ 装包 → 重扫规划。

    调用约定:**调用方须已持 instance_locks(game_root, src, dst)**(spec §4.2,
    预检→装包(写目标 mods/)→重扫规划全程在锁内;装包确认等交互在锁内进行
    属有意语义——正在操作该实例,他方 mcmig 应等待而非并发写盘)。

    Args:
        cwd: 调用方工作目录(旧布局快照/rules 回退基准)。
        game_root: 游戏根目录。
        src: 源版本名(玩家数据来源,规划步的源)。
        dst: 目标版本名(装包目标)。
        new_pack: 新整合包目录(含 mods/)。
        confirm_extras: extras 确认回调(目标有而新包无的 jar 将残留)——
            回调方自行打印清单与提示行后取得布尔决策,False=中止(零装包)。
        resolve_conflict: 同名冲突覆盖决策回调(逐 jar 调用)——回调方自行打印
            提示行;True=覆盖(旧件先备份),False=保留目标。
        force: 忽略兼容不满足与 extras 确认(冲突决策仍逐个询问,与既有 CLI 语义一致)。
        dry_run: 装包零写盘且跳过规划(规划会基于旧状态误导)。

    Returns:
        SwapRunOutcome(评审 v3 契约A:事中/事后信息全量入返回值——CLI 对拍
        基准=决策语义与退出码;规划失败分支 rc=2 且 install 保留+plan_error,
        装包统计与备份位置必须可见,目标 mods/ 已被修改,不可静默)。
    """
    dst_dir = _version_dir(game_root, dst)
    # 核心侧同闸(0.12.0 复审#1):CLI 已先行校验,此处兜底防其他调用方
    # 直接以绝对路径/穿越形态调 run_swap(拼接会越出 game_root)
    for name in (src, dst):
        why = version_name_error(name)
        if why is not None:
            raise ValueError(f"非法版本名 {name!r}:{why}")
    legacy_dir = cwd / ".mcmig"
    data_dir = game_root / ".mcmig"
    # 第一步:预检(NeoForge 兼容 + 源快照存在性 + extras/conflicts 清单 + 指纹)
    preflight = swap_preflight(game_root, dst, new_pack, src=src, legacy_dir=legacy_dir)
    if preflight.error is not None:
        return SwapRunOutcome(rc=2, preflight=preflight, install=None)
    if preflight.incompat and not force:
        return SwapRunOutcome(rc=2, preflight=preflight, install=None)
    if preflight.extras and not force and not confirm_extras(preflight.extras):
        return SwapRunOutcome(rc=0, preflight=preflight, install=None)
    # 冲突覆盖决策预收集(决策与执行解耦:与装包循环不交错)
    overwrite = {name for name in preflight.conflicts if resolve_conflict(name)}
    # 第二步:装包(覆盖备份目录=<game_root>/.mcmig/backups/swap/<UTC 时间戳>)
    backup_root = (data_dir / "backups" / "swap"
                   / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ%f"))
    journal: JobJournal | None = None
    if not dry_run:
        job_id = (datetime.now(timezone.utc).strftime("cli-%Y%m%dT%H%M%SZ-")
                  + str(os.getpid()))
        journal = JobJournal(data_dir / "jobs", job_id, "swap",
                             src=src, dst=dst, game_root=str(game_root))
    install = swap_install(dst_dir / "mods", new_pack / "mods", overwrite=overwrite,
                           dry_run=dry_run, backup_root=backup_root,
                           journal=journal, should_cancel=None,
                           progress_cb=progress_cb)
    if journal is not None:
        # 收尾标记:装包在安全边界内结束(取消/完成同型),退出中断清单;
        # 收尾写失败(0.12.0 复审#5)只警告——装包结果已知且必须返回,不得
        # 因 finish 失败丢统计(jobs/ 可能残留一条未收尾档案,可核对后清除)
        try:
            journal.finish()
        except JournalError as e:
            log.warning("swap journal 收尾失败(装包结果仍在返回值中): %s", e)
    if install.error is not None:
        # 装包失败(空间预检不过/中途文件操作失败,0.12.0 复审#5/#6):契约B
        # 同型——install 携带已知部分结果保留返回,plan_error 呈现原因,rc=2;
        # 调用方必须呈现已改动内容与备份位置(目标 mods/ 可能已被部分改写)
        return SwapRunOutcome(rc=2, preflight=preflight, install=install,
                              plan_error=install.error)
    if dry_run:
        # 彩排模式未真正写盘,规划会基于旧状态误导用户,故跳过规划
        return SwapRunOutcome(rc=0, preflight=preflight, install=install)
    if install.cancelled:
        # CLI 无取消通道(should_cancel=None),防御分支:部分装包不进规划
        return SwapRunOutcome(rc=0, preflight=preflight, install=install)
    # 第三步:重扫 dst(装包刚改写 mods/,rescan_dst=True)→ 规划(modpack_swap 内置)
    try:
        plan, compat_warnings, _pairs, _extras = build_plan(
            cwd,
            game_root,
            src,
            dst,
            modpack_swap=True,
            rescan_dst=True,
            mcmig_dir=legacy_dir,
            plans_dir=data_dir / "plans",
            data_dir=data_dir,
        )
    except (FileNotFoundError, ValueError, PlanPersistError) as e:
        # 评审 v3 契约B:装包已成功而规划失败——install 保留 + plan_error,
        # 调用方必须呈现装包统计与备份位置(目标 mods/ 已被修改,不可静默)
        return SwapRunOutcome(rc=2, preflight=preflight, install=install,
                              plan_error=str(e))
    return SwapRunOutcome(
        rc=0,
        preflight=preflight,
        install=install,
        compat_warnings=tuple(str(w) for w in compat_warnings),
        plan_summary=plan.summary(),
        plan_file=data_dir / "plans" / f"{src}__{dst}.plan.json",
    )
