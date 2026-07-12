"""Mod 感知模块:jar 解析 + config→modid 映射 + orphan 规则生成 + 版本兼容检查。

读取 mods/*.jar 的 META-INF/neoforge.mods.toml 提取 modid/version/依赖,
用于识别孤儿 config、检查 mod 版本兼容性。
"""

from __future__ import annotations

import logging
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModInfo:
    """单个 mod 的元数据。

    Attributes:
        modid: mod 标识符(如 "create")。
        version: mod 版本号(如 "6.0.10")。
        jar_filename: jar 文件名(如 "create-1.21.1-6.0.10.jar")。
        neoforge_range: NeoForge 版本范围要求(如 "[21.1.219,)"),无要求时 None。
    """

    modid: str
    version: str
    jar_filename: str
    neoforge_range: str | None


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


def _parse_mods_toml(content: str, jar_filename: str) -> list[ModInfo]:
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
            )
        )
    return results


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
                names = z.namelist()
                toml_entry = None
                for candidate in (
                    "META-INF/neoforge.mods.toml",
                    "META-INF/mods.toml",
                ):
                    if candidate in names:
                        toml_entry = candidate
                        break
                if toml_entry is None:
                    continue
                content = z.read(toml_entry).decode("utf-8")
        except (zipfile.BadZipFile, OSError, UnicodeDecodeError) as e:
            log.warning("jar %s 读取失败: %s", jar_path.name, e)
            continue

        mods = _parse_mods_toml(content, jar_path.name)
        for info in mods:
            registry.add(info)

    return registry
