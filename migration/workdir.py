"""workdir:mcmig 工作目录布局解析(绿色软件模式 + 兼容模式)。

PyInstaller 单文件 exe(frozen)下采用「绿色软件」布局——所有 mcmig 状态
(配置/快照/计划/规则)存放于 exe 同级的 ``data/`` 目录内,绝不写入
APPDATA/用户目录,玩家整个客户端文件夹拷走即带走全部工具状态;
快照/计划/规则再按「游戏根目录名(slug)」隔离子目录,支持多个整合包根共存
互不串数据。源码运行(非 frozen)下,``cwd/.mcmig`` 兼容布局现由 **GUI 表面**
使用(GUI 生成物仍走 workdir);CLI 自 0.7.0 起生成物(快照/计划)锚定
``game_root/.mcmig``(由 ``cli.py`` 接线),不再落在本布局内。

两种模式一览:
- 绿色模式(frozen):root=exe_dir/data;config=root/config.toml(TOML);
  snapshots=root/<slug>/snapshots、plans=root/<slug>/plans、rules=root/<slug>/rules.yaml
- 兼容模式(源码):root=cwd/.mcmig;snapshots=root/snapshots、plans=root/plans、
  rules=root/rules.yaml、config=root/config.yaml(YAML ``game_root:`` 键,兼容现状)

注意:``_is_frozen``/``_exe_dir``/``_ensure_writable``
为模块级小函数,兼作测试注入点。
"""

from __future__ import annotations

import logging
import sys
import tomllib
from dataclasses import dataclass
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
    """mcmig 工作目录布局(不可变值对象)。

    Attributes:
        root: 工作目录根(绿色=exe/data,兼容=cwd/.mcmig)。
        snapshots: 快照目录。
        plans: 迁移计划目录。
        rules: 用户规则文件路径(rules.yaml)。
        config: 配置文件路径(绿色=config.toml,兼容=config.yaml)。
        green: 是否绿色软件模式。
    """

    root: Path
    snapshots: Path
    plans: Path
    rules: Path
    config: Path
    green: bool

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

    def game_root(self) -> Path | None:
        """从 config 文件读取游戏根目录(按模式分流 TOML/YAML)。

        Returns:
            游戏根目录;未配置或 config 不存在/损坏时返回 None。
        """
        if self.green:
            return _load_game_root_toml(self.config)
        return _load_game_root_yaml(self.config)


def resolve_workdir(game_root: Path | None = None) -> WorkDir:
    """解析 mcmig 工作目录布局。

    frozen(exe)→ 绿色模式(exe/data,按游戏根目录名隔离);
    源码运行 → 兼容模式(cwd/.mcmig,与 v0.x 一致)。

    Args:
        game_root: 游戏根目录;None 时绿色模式先读 config.toml,
            读不到抛 WorkdirError(兼容模式不参与 game_root 解析)。

    Returns:
        工作目录布局。

    Raises:
        WorkdirError: 绿色模式下 exe 目录不可写,或未配置游戏目录。
    """
    if _is_frozen():
        return _resolve_green(game_root)
    return _resolve_compat()


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


def _resolve_green(game_root: Path | None) -> WorkDir:
    """绿色模式布局:root=exe/data,按游戏根目录名(slug)隔离子目录。

    Args:
        game_root: 游戏根目录;None 时读 data/config.toml。

    Returns:
        绿色模式工作目录布局。

    Raises:
        WorkdirError: exe 目录不可写,或未配置游戏目录。
    """
    root = _exe_dir() / "data"
    # 前置可写性检查:data/ 不存在则尝试创建,不可写即整体失败
    try:
        _ensure_writable(root)
    except OSError as e:
        raise WorkdirError(
            what="软件目录不可写",
            why="软件目录不可写,请把 mcmig 移动到可写的文件夹后重试",
        ) from e
    if game_root is None:
        game_root = _load_game_root_toml(root / "config.toml")
        if game_root is None:
            # 指引须指向真实入口:图形界面步①有「游戏根目录」输入框(终审 I-1),
            # 绿色 exe 首跑界面起不来时则手动建 data/config.toml
            raise WorkdirError(
                what="未配置游戏目录",
                why='启动图形界面后设置,或手动创建 data/config.toml 写入 game_root = "路径"',
            )
    # slug=游戏根目录名,不做 sanitize:目录名本身即合法文件夹名
    slug = game_root.name
    return WorkDir(
        root=root,
        snapshots=root / slug / "snapshots",
        plans=root / slug / "plans",
        rules=root / slug / "rules.yaml",
        config=root / "config.toml",
        green=True,
    )


def _resolve_compat() -> WorkDir:
    """兼容模式布局:root=cwd/.mcmig,与 v0.x 现状完全一致(不按 slug 隔离)。

    Returns:
        兼容模式工作目录布局。
    """
    root = Path.cwd() / ".mcmig"
    return WorkDir(
        root=root,
        snapshots=root / "snapshots",
        plans=root / "plans",
        rules=root / "rules.yaml",
        config=root / "config.yaml",
        green=False,
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
