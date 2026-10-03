"""workdir:mcmig 工作目录布局解析(绿色软件模式 + 兼容模式)。

路径契约 v3(spec §3.1,批次I-T1):状态分两层——

- **全局态**(软件侧,跟工具走):绿色模式为 exe 同级 ``data/``
  (config.toml;不写 APPDATA,玩家整个客户端文件夹拷走即带走),
  源码运行为 ``cwd/.mcmig``(config.yaml,兼容现状)。
- **实例态**(游戏侧,跟实例走):统一锚定 ``<game_root>/.mcmig/``
  ——snapshots/plans/rules.yaml/jobs/locks,绿色与源码两模式**同址**,
  支持多个整合包根共存互不串数据;CLI/GUI 生成物不再随 CWD/exe 散落。

首跑欢迎态:绿色模式未配置 game_root 时**不再抛错**,返回 game_root=None、
实例态字段全 None 的布局,由 GUI/CLI 引导配置(spec §3.1)。

旧布局只读回退:绿色模式旧 slug 布局(``exe/data/<slug>/snapshots``)与源码
模式旧 CWD 布局(``cwd/.mcmig/snapshots``)存在时记入 ``legacy_snapshots``,
供调用方提示迁移,绝不自动写入。

注意:``_is_frozen``/``_exe_dir``/``_ensure_writable``
为模块级小函数,兼作测试注入点。
"""

from __future__ import annotations

import logging
import sys
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# 可写性探测临时文件名(_ensure_writable 用,探测后即删)
_PROBE_NAME = ".mcmig-write-probe"


class WorkdirError(Exception):
    """工作目录解析/写入失败。

    Attributes:
        what: 失败对象(中文短描述,GUI 弹窗标题级)。
        why: 中文失败原因与用户可执行的建议。
    """

    def __init__(self, what: str, why: str) -> None:
        """初始化工作目录异常。

        Args:
            what: 失败对象(中文短描述)。
            why: 中文失败原因与建议。
        """
        # str(e) 输出「对象:原因」完整中文,便于日志与终端直接展示
        super().__init__(f"{what}:{why}")
        self.what = what
        self.why = why


@dataclass(frozen=True)
class WorkDir:
    """mcmig 工作目录布局(不可变值对象,路径契约 v3)。

    Attributes:
        root: 全局态根(绿色=exe/data,兼容=cwd/.mcmig)。
        config: 全局配置文件路径(绿色=config.toml,兼容=config.yaml)。
        green: 是否绿色软件模式。
        game_root: 游戏根目录;None=未配置(首跑欢迎态,实例态字段随之全 None)。
        snapshots: 实例态快照目录(<game_root>/.mcmig/snapshots)。
        plans: 实例态迁移计划目录(<game_root>/.mcmig/plans)。
        rules: 实例态用户规则文件路径(<game_root>/.mcmig/rules.yaml)。
        jobs: 实例态 job journal 目录(T6 预留)。
        locks: 实例态锁登记目录(T5 预留,POSIX 回退用)。
        legacy_snapshots: 旧布局快照目录(只读回退:绿色=exe/data/<slug>/snapshots,
            兼容=cwd/.mcmig/snapshots);不存在或与新位置同体时为 None。
    """

    root: Path
    config: Path
    green: bool
    game_root: Path | None = None
    snapshots: Path | None = None
    plans: Path | None = None
    rules: Path | None = None
    jobs: Path | None = None
    locks: Path | None = None
    legacy_snapshots: Path | None = None

    def save_game_root(self, path: Path) -> None:
        """把游戏根目录持久化写入 config 文件(按模式分流 TOML/YAML)。

        Args:
            path: 游戏根目录(整合包客户端根)。
        """
        self.config.parent.mkdir(parents=True, exist_ok=True)
        if self.green:
            # 手写 TOML 行,不引 toml 写库:反斜杠转义后 tomllib 可无损读回
            text = f'game_root = "{_toml_escape(str(path))}"\n'
            self.config.write_text(text, encoding="utf-8")
        else:
            # 兼容模式沿用 .mcmig/config.yaml 的 YAML 格式(game_root: 键)
            self.config.write_text(
                yaml.safe_dump({"game_root": str(path)}, allow_unicode=True),
                encoding="utf-8",
            )
        log.debug("已保存游戏根目录 %s → %s", path, self.config)


def resolve_workdir(game_root: Path | None = None) -> WorkDir:
    """解析 mcmig 工作目录布局(路径契约 v3)。

    frozen(exe)→ 绿色模式(全局态=exe/data);源码运行 → 兼容模式
    (全局态=cwd/.mcmig)。实例态统一锚定 ``<game_root>/.mcmig``(两模式同址);
    未配置 game_root 时返回欢迎态布局(game_root=None,实例态字段全 None),
    **不再抛错**。

    Args:
        game_root: 游戏根目录;None 时按模式读 config
            (绿色=config.toml,兼容=config.yaml)。

    Returns:
        工作目录布局(未配置时实例态字段为 None)。

    Raises:
        WorkdirError: 绿色模式下 exe 目录不可写。
    """
    if _is_frozen():
        return _resolve_green(game_root)
    return _resolve_compat(game_root)


def _is_frozen() -> bool:
    """检测是否运行在 PyInstaller frozen 模式(单文件 exe)。"""
    return bool(getattr(sys, "frozen", False))


def _exe_dir() -> Path:
    """返回 exe(mcmig.exe)所在目录;测试可注入替换。"""
    return Path(sys.executable).resolve().parent


def _ensure_writable(p: Path) -> None:
    """确保目录 p 存在且可写:不存在则递归创建,再以临时探测文件验证写权限。

    Args:
        p: 待探测的目录(绿色模式下即 exe/data)。

    Raises:
        OSError: 目录无法创建或不可写(只读盘/Program Files ACL 拒绝等)。
    """
    p.mkdir(parents=True, exist_ok=True)
    probe = p / _PROBE_NAME
    try:
        probe.write_text("", encoding="utf-8")
    finally:
        probe.unlink(missing_ok=True)


def _instance_layout(game_root: Path) -> dict[str, Path]:
    """实例态目录布局(spec §3.1:统一 <game_root>/.mcmig,绿/源码同址)。"""
    base = game_root / ".mcmig"
    return {
        "snapshots": base / "snapshots",
        "plans": base / "plans",
        "rules": base / "rules.yaml",
        "jobs": base / "jobs",
        "locks": base / "locks",
    }


def _resolve_green(game_root: Path | None) -> WorkDir:
    """绿色模式布局:全局态=exe/data,实例态锚定 game_root/.mcmig(路径契约 v3)。

    Args:
        game_root: 游戏根目录;None 时读 data/config.toml,仍未配置则返回欢迎态。

    Returns:
        绿色模式工作目录布局(未配置时 game_root 与实例态字段均为 None)。

    Raises:
        WorkdirError: exe 目录不可写。
    """
    root = _exe_dir() / "data"
    # 前置可写性检查:data/ 不存在则尝试创建,不可写即整体失败
    try:
        _ensure_writable(root)
    except OSError as e:
        raise WorkdirError(
            what="软件目录不可写",
            # why 只写可行动指引:展示端按「what:why」组合(消息框/终端/页面),
            # 复读 what 会得到「软件目录不可写:软件目录不可写,…」(K2 真机手测收口)
            why="请把 mcmig 移动到可写的文件夹后重试",
        ) from e
    if game_root is None:
        game_root = _load_game_root_toml(root / "config.toml")
    if game_root is None:
        # 首跑欢迎态:不抛错,实例态字段为 None,由 GUI/CLI 引导配置(spec §3.1)。
        # 指引仍指向真实入口:图形界面步①有「游戏根目录」输入框(终审 I-1)
        return WorkDir(root=root, config=root / "config.toml", green=True)
    inst = _instance_layout(game_root)
    # 旧绿色 slug 布局(exe/data/<slug>/snapshots)存在时记入 legacy 只读回退
    legacy = root / game_root.name / "snapshots"
    return WorkDir(
        root=root,
        config=root / "config.toml",
        green=True,
        game_root=game_root,
        legacy_snapshots=legacy if legacy.is_dir() else None,
        **inst,
    )


def _resolve_compat(game_root: Path | None) -> WorkDir:
    """兼容模式布局:全局态=cwd/.mcmig(config.yaml),实例态锚定 game_root/.mcmig。

    Args:
        game_root: 游戏根目录;None 时读 cwd/.mcmig/config.yaml,仍未配置则返回欢迎态。

    Returns:
        兼容模式工作目录布局(未配置时 game_root 与实例态字段均为 None)。
    """
    root = Path.cwd() / ".mcmig"
    if game_root is None:
        game_root = _load_game_root_yaml(root / "config.yaml")
    base = WorkDir(root=root, config=root / "config.yaml", green=False)
    if game_root is None:
        return base
    inst = _instance_layout(game_root)
    # 旧 CWD 布局(cwd/.mcmig/snapshots)存在且与新位置不同体时记入 legacy
    legacy = root / "snapshots"
    return replace(
        base,
        game_root=game_root,
        legacy_snapshots=(
            legacy if legacy.is_dir() and legacy != inst["snapshots"] else None
        ),
        **inst,
    )


def _load_game_root_toml(config: Path) -> Path | None:
    """从 TOML config 读 game_root(绿色模式)。

    Args:
        config: config.toml 路径。

    Returns:
        游戏根目录;文件不存在/损坏/无 game_root 时返回 None。
    """
    if not config.is_file():
        return None
    try:
        doc = tomllib.loads(config.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as e:
        log.warning("config.toml 解析失败,按未配置处理(%s):%s", config, e)
        return None
    value = doc.get("game_root")
    if isinstance(value, str) and value:
        return Path(value)
    return None


def _load_game_root_yaml(config: Path) -> Path | None:
    """从 YAML config 读 game_root(兼容模式,沿用 .mcmig/config.yaml 格式)。

    Args:
        config: config.yaml 路径。

    Returns:
        游戏根目录;文件不存在/损坏/无 game_root 时返回 None。
    """
    if not config.is_file():
        return None
    try:
        # utf-8-sig 宽容读取:用户手写的 config.yaml 可能带 BOM(PowerShell 重定向生成),
        # 无 BOM 文件同样兼容;写入侧仍统一 UTF-8 无 BOM
        doc = yaml.safe_load(config.read_text(encoding="utf-8-sig")) or {}
    except (yaml.YAMLError, OSError) as e:
        log.warning("config.yaml 解析失败,按未配置处理(%s):%s", config, e)
        return None
    value = doc.get("game_root") if isinstance(doc, dict) else None
    if isinstance(value, str) and value:
        return Path(value)
    return None


def _toml_escape(text: str) -> str:
    """转义为 TOML 基本字符串字面量内容(反斜杠与双引号,Windows 路径关键)。"""
    return text.replace("\\", "\\\\").replace('"', '\\"')
