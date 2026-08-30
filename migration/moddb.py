"""Mod 感知模块:jar 解析 + config→modid 映射 + orphan 规则生成 + 版本兼容检查。

读取 mods/*.jar 的 META-INF/neoforge.mods.toml 提取 modid/version/依赖,
用于识别孤儿 config、检查 mod 版本兼容性。
"""

from __future__ import annotations

import io
import json
import logging
import re
import tomllib
import zipfile
import zlib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import pathspec
import yaml

from .rules import Category, Rule
from .snapshot import FileEntry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModInfo:
    """单个 mod 的元数据。

    Attributes:
        modid: mod 标识符(如 "create")。
        version: mod 版本号(如 "6.0.10")。
        jar_filename: jar 文件名(如 "create-1.21.1-6.0.10.jar")。
        neoforge_range: NeoForge 版本范围要求(如 "[21.1.219,)"),无要求时 None。
        embedded_in: 内嵌(jar-in-jar)时宿主 jar 的文件名,顶层 jar 为 None。
    """

    modid: str
    version: str
    jar_filename: str
    neoforge_range: str | None
    embedded_in: str | None = None


class ModRegistry:
    """mod 注册表:modid → ModInfo,支持大小写不敏感查询。"""

    def __init__(self) -> None:
        self._mods: dict[str, ModInfo] = {}
        self._lower_ids: dict[str, str] = {}

    def add(self, info: ModInfo) -> None:
        """添加一个 mod 信息。"""
        self._mods[info.modid] = info
        self._lower_ids[info.modid.lower()] = info.modid

    def __contains__(self, modid: str) -> bool:
        return modid.lower() in self._lower_ids

    def get(self, modid: str) -> ModInfo | None:
        """按 modid 查询(大小写不敏感)。"""
        original = self._lower_ids.get(modid.lower())
        return self._mods.get(original) if original else None

    @property
    def modids(self) -> set[str]:
        """所有已注册的 modid 集合。"""
        return set(self._mods.keys())

    def __len__(self) -> int:
        return len(self._mods)


def _parse_mods_toml(content: str, jar_filename: str, embedded_in: str | None = None) -> list[ModInfo]:
    """解析 mods.toml 内容,返回 ModInfo 列表。

    一个 jar 可含多个 [[mods]] 条目(捆绑 mod)。
    """
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as e:
        log.warning("jar %s 的 mods.toml 解析失败: %s", jar_filename, e)
        return []

    mods_data = data.get("mods", [])
    if not isinstance(mods_data, list):
        return []

    deps_map: dict[str, list[dict]] = data.get("dependencies", {})
    if not isinstance(deps_map, dict):
        deps_map = {}

    results: list[ModInfo] = []
    for mod_entry in mods_data:
        if not isinstance(mod_entry, dict):
            continue
        modid = mod_entry.get("modId", "")
        if not modid or not isinstance(modid, str):
            continue
        version = str(mod_entry.get("version", ""))
        neoforge_range: str | None = None
        for dep in deps_map.get(modid, []):
            if not isinstance(dep, dict):
                continue
            if dep.get("modId") == "neoforge":
                neoforge_range = dep.get("versionRange")
                break
        results.append(
            ModInfo(
                modid=modid,
                version=version,
                jar_filename=jar_filename,
                neoforge_range=neoforge_range,
                embedded_in=embedded_in,
            )
        )
    return results


def _read_toml_from_zip(zf: zipfile.ZipFile) -> str | None:
    """从已打开的 zip 中读取 mods.toml 内容,无则返回 None。

    优先 META-INF/neoforge.mods.toml,fallback 到 META-INF/mods.toml。
    """
    names = zf.namelist()
    for candidate in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
        if candidate in names:
            return zf.read(candidate).decode("utf-8")
    return None


def scan_mods(version_dir: Path) -> ModRegistry:
    """扫描版本目录的 mods/*.jar,提取 mod 元数据。

    优先读 META-INF/neoforge.mods.toml,fallback 到 META-INF/mods.toml。
    jar 无 mods.toml / 格式损坏 → 跳过(不崩溃,记 warning)。

    Args:
        version_dir: 版本文件夹路径(含 mods/ 子目录)。

    Returns:
        ModRegistry: 已注册的 mod 信息。
    """
    registry = ModRegistry()
    mods_dir = version_dir / "mods"
    if not mods_dir.is_dir():
        return registry

    for jar_path in sorted(mods_dir.glob("*.jar")):
        try:
            with zipfile.ZipFile(jar_path) as z:
                content = _read_toml_from_zip(z)
                if content is not None:
                    mods = _parse_mods_toml(content, jar_path.name)
                    for info in mods:
                        registry.add(info)
                # jar-in-jar:内嵌依赖的 modid 也登记(孤儿判定需要),
                # 顶层已注册的 modid 优先,内层同名跳过
                for inner_name in (
                    n for n in z.namelist()
                    if n.startswith("META-INF/jarjar/") and n.endswith(".jar")
                ):
                    try:
                        with zipfile.ZipFile(io.BytesIO(z.read(inner_name))) as iz:
                            inner_content = _read_toml_from_zip(iz)
                            if inner_content is None:
                                continue
                            inner_mods = _parse_mods_toml(
                                inner_content, inner_name.rsplit("/", 1)[-1],
                                embedded_in=jar_path.name,
                            )
                    except (
                        zipfile.BadZipFile, OSError, UnicodeDecodeError, zlib.error,
                    ) as e:
                        log.warning("jar %s 的内嵌 %s 读取失败: %s", jar_path.name, inner_name, e)
                        continue
                    for info in inner_mods:
                        if info.modid not in registry:
                            registry.add(info)
        except (zipfile.BadZipFile, OSError, UnicodeDecodeError, zlib.error) as e:
            log.warning("jar %s 读取失败: %s", jar_path.name, e)
            continue

    return registry


# 核心配置前缀(mod 非管辖,rebuild 规则管)
_CORE_PREFIXES = frozenset({"fml", "neoforge", "minecraft"})

# 合法 modid 正则(小写字母开头,仅含小写字母/数字/下划线)
_VALID_MODID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def extract_modid_candidate(path: str) -> str | None:
    """从 config 路径提取 modid 候选(纯文件名约定,不查注册表)。

    - 子目录: config/jade/foo.json → "jade"
    - 顶层文件: config/create-client.toml → "create" (第一个 "-" 前)
    - 核心配置(fml/neoforge/minecraft)→ None
    - .bak 文件 → None
    - 含空格/非合法 modid → None

    Returns:
        小写 modid 候选,或 None(无法确定)。
    """
    if not path.startswith("config/"):
        return None
    if path.endswith(".bak"):
        return None
    rest = path[len("config/"):]
    parts = rest.split("/", 1)
    if len(parts) == 2:
        candidate = parts[0].lower()
    else:
        filename = parts[0]
        dot = filename.rfind(".")
        stem = filename[:dot] if dot != -1 else filename
        candidate = stem.split("-")[0].lower()
    if candidate in _CORE_PREFIXES:
        return None
    if not _VALID_MODID_RE.match(candidate):
        return None
    return candidate


class OverrideTable:
    """config→modid 覆盖表(路径 glob → modid)。"""

    def __init__(self, entries: list[tuple[str, str, str]]) -> None:
        """初始化覆盖表。

        Args:
            entries: (match_glob, modid, reason) 三元组列表。
        """
        self._entries = entries
        self._specs: list[tuple[pathspec.PathSpec, str]] = [
            (pathspec.PathSpec.from_lines("gitignore", [m]), mid)
            for m, mid, _ in entries
        ]

    def lookup(self, path: str) -> str | None:
        """按路径查找覆盖的 modid。"""
        norm = path.replace("\\", "/")
        for spec, modid in self._specs:
            if spec.match_file(norm):
                return modid
        return None


def load_mod_config_map() -> OverrideTable:
    """加载打包在内的覆盖表(importlib.resources,PyInstaller 安全)。"""
    try:
        text = resources.files("migration").joinpath("data/mod_config_map.yaml").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return OverrideTable([])
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return OverrideTable([])
    if not isinstance(doc, dict):
        return OverrideTable([])
    entries: list[tuple[str, str, str]] = []
    for m in doc.get("mappings") or []:
        if not isinstance(m, dict):
            continue
        match = m.get("match")
        modid = m.get("modid")
        if not match or not modid:
            continue
        entries.append((match, modid, str(m.get("reason", ""))))
    return OverrideTable(entries)


def map_config_to_mod(
    path: str, dst_mods: ModRegistry, override: OverrideTable
) -> tuple[str | None, bool]:
    """将 config 路径映射到 modid,并判断是否为孤儿。

    Returns:
        (modid, is_orphan): modid 为 None 表示无法确定(保守不判);
        is_orphan=True 表示 mod 未安装在 dst。
    """
    mapped = override.lookup(path)
    if mapped is not None:
        return mapped, mapped not in dst_mods

    candidate = extract_modid_candidate(path)
    if candidate is None:
        return None, False

    if candidate in dst_mods:
        return candidate, False

    if "_" in candidate:
        prefix = candidate.rsplit("_", 1)[0]
        if prefix in dst_mods:
            return prefix, False

    return candidate, True


def generate_orphan_rules(
    src_entries: list[FileEntry],
    dst_mods: ModRegistry,
    override: OverrideTable,
) -> list[Rule]:
    """对 src 的 config 文件生成 orphan 规则(mod 不在 dst)。

    - 仅处理 config/ 前缀的非 .bak 文件
    - 无法确定 modid → 跳过(保守)
    - mod 在 dst → 跳过
    - mod 不在 dst → 生成精确路径 Rule(decide=ORPHAN)

    Returns:
        orphan 规则列表(精确路径 match)。
    """
    rules: list[Rule] = []
    for entry in src_entries:
        path = entry.path
        modid, is_orphan = map_config_to_mod(path, dst_mods, override)
        if modid is None:
            continue
        if is_orphan:
            rules.append(
                Rule(
                    match=path,
                    decide=Category.ORPHAN,
                    reason=f"mod '{modid}' not installed in dst",
                    source="orphan",
                )
            )
    return rules


@dataclass(frozen=True)
class CompatWarning:
    """mod 版本兼容性警告。

    Attributes:
        modid: mod 标识符。
        jar_filename: jar 文件名。
        mod_version: mod 版本号。
        required_range: 要求的 NeoForge 版本范围。
        dst_neoforge: 目标 NeoForge 版本号。
    """

    modid: str
    jar_filename: str
    mod_version: str
    required_range: str
    dst_neoforge: str


def _parse_version_tuple(v: str) -> tuple[int, ...]:
    """将版本字符串解析为整数元组(如 '21.1.233' → (21, 1, 233))。"""
    parts: list[int] = []
    for seg in v.split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            # 非数字段 → 用 0 占底,保证不崩
            parts.append(0)
    return tuple(parts) if parts else (0,)


def check_version_range(version: str, range_str: str) -> bool:
    """检查版本是否在 Maven 版本范围内。

    支持: [x,) / (x,) / [x,y) / [x,y] / (x,y) / (x,y] / [x] / [,y)

    格式异常 → 返回 True(保守认为兼容,不阻断迁移)。

    Args:
        version: 待检查版本(如 "21.1.233")。
        range_str: Maven 版本范围(如 "[21.1.219,)")。

    Returns:
        True = 在范围内(兼容); False = 不在范围内(不兼容)。
    """
    range_str = range_str.strip()
    if not range_str:
        return True
    if len(range_str) < 2:
        return True
    # Maven 范围必须以 [/( 开头、]/) 结尾;否则视为格式异常 → 保守兼容
    if range_str[0] not in "[(" or range_str[-1] not in "])":
        return True
    inclusive_start = range_str[0] == "["
    inclusive_end = range_str[-1] == "]"
    inner = range_str[1:-1]
    parts = inner.split(",")
    if len(parts) == 1:
        # 无逗号 → 单版本精确匹配(如 [21.1.228]),上下界均为该值
        start = parts[0].strip()
        end = start
    else:
        start = parts[0].strip()
        end = parts[1].strip() if len(parts) > 1 else ""

    v = _parse_version_tuple(version)

    if start:
        sv = _parse_version_tuple(start)
        if inclusive_start:
            if v < sv:
                return False
        else:
            if v <= sv:
                return False

    if end:
        ev = _parse_version_tuple(end)
        if inclusive_end:
            if v > ev:
                return False
        else:
            if v >= ev:
                return False

    return True


def read_neoforge_version(version_dir: Path) -> str | None:
    """从版本 json 读取 NeoForge 版本号(--fml.neoforgeVersion 参数)。

    Args:
        version_dir: 版本文件夹路径(含 <version_name>.json)。

    Returns:
        NeoForge 版本号字符串,或 None(文件缺失/无参数)。
    """
    version_name = version_dir.name
    json_path = version_dir / f"{version_name}.json"
    if not json_path.exists():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    args_section = data.get("arguments", {})
    if not isinstance(args_section, dict):
        return None
    args = args_section.get("game", [])
    if not isinstance(args, list):
        return None
    for i, arg in enumerate(args):
        if arg == "--fml.neoforgeVersion" and i + 1 < len(args):
            return str(args[i + 1])
    return None


def check_mod_compat(
    mod_added_paths: list[str],
    src_mods: ModRegistry,
    dst_neoforge: str | None,
) -> list[CompatWarning]:
    """对 mod_added 检查 NeoForge 版本兼容性。

    仅检查有 neoforge_range 的 mod。dst_neoforge 为 None 时跳过。

    Args:
        mod_added_paths: mod_added 的文件路径列表(如 ["mods/extra.jar"])。
        src_mods: 源版本的 mod 注册表。
        dst_neoforge: 目标 NeoForge 版本号(如 "21.1.228"),None 表示未知。

    Returns:
        不兼容的 mod 警告列表。
    """
    if dst_neoforge is None:
        return []
    warnings: list[CompatWarning] = []
    for path in mod_added_paths:
        # mod_added_paths 是文件路径(如 "mods/cp_lib.jar"),按 jar_filename 查找
        jar_name = path.split("/")[-1]
        for modid in src_mods.modids:
            mi = src_mods.get(modid)
            if mi is None:
                continue
            if mi.jar_filename != jar_name:
                continue
            if mi.neoforge_range is None:
                continue
            if not check_version_range(dst_neoforge, mi.neoforge_range):
                warnings.append(
                    CompatWarning(
                        modid=mi.modid,
                        jar_filename=mi.jar_filename,
                        mod_version=mi.version,
                        required_range=mi.neoforge_range,
                        dst_neoforge=dst_neoforge,
                    )
                )
    return warnings
