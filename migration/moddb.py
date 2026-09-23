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


_TAG_PREFIX_RE = re.compile(r"^\s*\[[^\]]*\]\s*")


def normalize_jar_family(jar_rel_path: str) -> tuple[str, str, str]:
    """jar 相对路径 → (家族键, 版本签名, 变体尾缀),文件名配对的归一化基础(F4/F20-3)。

    家族键 = 剥 [中文标签] 前缀、小写、按 -_+ 切词后仅保留纯字母词(**含尾缀词,规则不变**);
    版本签名 = 含数字的词按序拼接(区分 upgrade/renamed 用);
    变体尾缀 = 最后一个含数字词**之后**的连续纯字母词(如 -Patch/-feature),无则空串。
    家族键仍含尾缀词是两级匹配零回归的关键:第一级全键配对行为与 0.6.3 完全一致,
    尾缀只作为第二级(减尾键)配对的新信息。

    Args:
        jar_rel_path: 版本内相对路径(正斜杠,如 "mods/[标签] x-1.0.jar")。

    Returns:
        (家族键, 版本签名, 变体尾缀);家族键可能为空串(调用方需跳过)。
    """
    name = jar_rel_path.rsplit("/", 1)[-1]
    name = _TAG_PREFIX_RE.sub("", name)
    stem = name.rsplit(".", 1)[0].lower()
    tokens = [t for t in re.split(r"[-_+]", stem) if t]
    # 尾缀定位:最后一个含数字词(非纯字母词)之后的连续纯字母词
    last_digit_idx = -1
    for i, t in enumerate(tokens):
        if not t.isalpha():
            last_digit_idx = i
    tail = "-".join(tokens[last_digit_idx + 1 :]) if last_digit_idx >= 0 else ""
    family = "-".join(t for t in tokens if t.isalpha())
    version_sig = "-".join(t for t in tokens if not t.isalpha())
    return family, version_sig, tail


def _reduced_family(family: str, tail: str) -> str:
    """家族键剥掉尾部尾缀词(第二级匹配的减尾键);tail 为空时原样返回。

    纯字母词经 "-" 连接成家族键,split("-") 可无损还原词表;
    尾缀词必在词表末尾,故直接截断对应长度。
    """
    if not tail:
        return family
    n = len(tail.split("-"))
    return "-".join(family.split("-")[:-n])


# 平台装饰词(F22):上游改命名风格时中段平台词干扰家族键,四级配对前剥除(枚举闭集)
_PLATFORM_WORDS = frozenset({"neoforge", "forge", "fabric", "quilt", "mc", "minecraft"})


def _platform_stripped(family: str, tail: str) -> str:
    """减尾键再剥平台装饰词(第四级配对键);剥后为空返回空串(调用方跳过)。

    Args:
        family: 家族键(纯字母词 "-" 连接,含尾缀词)。
        tail: 变体尾缀(空串表示无)。

    Returns:
        剥离平台词后的减尾键;家族词全为平台词时为空串。
    """
    reduced = _reduced_family(family, tail)
    if not reduced:
        return ""
    return "-".join(w for w in reduced.split("-") if w not in _PLATFORM_WORDS)


@dataclass(frozen=True)
class ModPair:
    """跨侧配对的同一 mod(升级或改名)。

    Attributes:
        modid: mod 标识符。
        kind: "upgrade"(同 modid 异版本) / "renamed"(同 modid 同版本异文件名) / "rebuilt"(同版本异尾缀重打包,批次D)。
        src_files: 源侧 jar 相对路径(通常 1 个)。
        dst_files: 目标侧 jar 相对路径。
        src_version: 源侧版本(空串视为 None)。
        dst_version: 目标侧版本(空串视为 None)。
        source: 配对来源:"registry"(读 jar mods.toml) | "filename"(快照文件名归一)。
    """

    modid: str
    kind: str
    src_files: list[str]
    dst_files: list[str]
    src_version: str | None
    dst_version: str | None
    source: str = "registry"  # "registry"(读 jar mods.toml) | "filename"(快照文件名归一)

    def to_dict(self) -> dict:
        """转为 JSON 可序列化字典(diff --json 的 mod_pairs 元素)。"""
        return {
            "modid": self.modid,
            "kind": self.kind,
            "src_files": self.src_files,
            "dst_files": self.dst_files,
            "src_version": self.src_version,
            "dst_version": self.dst_version,
            "source": self.source,
        }


def pair_mods(src_mods: ModRegistry, dst_mods: ModRegistry) -> list[ModPair]:
    """按 modid 配对两侧注册表,产出升级/改名清单。

    - 两侧均有该 modid:版本不同 → upgrade;版本相同但 jar 文件名不同 →
      按尾缀启发式判 rebuilt(尾缀不同)或 renamed(尾缀相同);
      版本与文件名均相同 → 不配对(已是 shared)。
    - 仅一侧有 → 不配对(维持 to_add/target_only 原语义,planner 行为不变)。
    - 多 jar 同 modid(注册表按 modid 去重,罕见)整组按单条处理,不做逐 jar 拆分。

    Args:
        src_mods: 源侧 mod 注册表。
        dst_mods: 目标侧 mod 注册表。

    Returns:
        ModPair 列表(按 modid 升序)。
    """
    pairs: list[ModPair] = []
    # 注:modids 是 ModRegistry 的 property(非方法),直接取集合做交集
    for modid in sorted(src_mods.modids & dst_mods.modids):
        s = src_mods.get(modid)
        d = dst_mods.get(modid)
        if s is None or d is None:
            continue
        if s.version != d.version:
            kind = "upgrade"
        elif s.jar_filename != d.jar_filename:
            # 同版异名:文件名尾缀不同(如 -Patch/-feature)→ rebuilt(同版本重打包);
            # 尾缀相同(如仅 [中文标签] 前缀差)→ renamed。与文件名配对两源判定统一(F20-3)。
            s_tail = normalize_jar_family("mods/" + s.jar_filename)[2]
            d_tail = normalize_jar_family("mods/" + d.jar_filename)[2]
            kind = "rebuilt" if s_tail != d_tail else "renamed"
        else:
            continue
        pairs.append(
            ModPair(
                modid=modid,
                kind=kind,
                src_files=[f"mods/{s.jar_filename}"],
                dst_files=[f"mods/{d.jar_filename}"],
                src_version=s.version or None,
                dst_version=d.version or None,
            )
        )
    return pairs


def pair_mods_by_filename(src_only: list[str], dst_only: list[str]) -> list[ModPair]:
    """mods 桶 to_add/target_only 按归一化家族键四级配对(纯快照数据,无活体依赖)。

    第一级(0.6.3 语义,键与判定完全不变):家族键全等配对;
    版本签名不同 → upgrade,相同 → renamed。歧义(同键任一侧多候选)整族放弃。
    第二级(批次D F20-3,仅对第一级未配上的残余):家族键剥掉尾缀词(减尾键)后相等,
    且双方版本签名相同、尾缀不同(含一侧空)→ kind="rebuilt"(同版本重打包/变体)。
    第三级(F21,仅消费上级残余):减尾键相等 + 版本签名不同 → upgrade
    (版本与尾缀同时变化:一级因家族键含尾缀词而失配,二级因要求同签名而失配)。
    第四级(F22,仅消费上级残余):减尾键剥平台装饰词(neoforge/forge/fabric/quilt/
    mc/minecraft)后相等 → 版本签名不同 → upgrade,相同 → renamed(作者改命名风格)。

    Args:
        src_only: 源侧独有 jar 相对路径(to_add 条目)。
        dst_only: 目标侧独有 jar 相对路径(target_only 条目)。

    Returns:
        ModPair 列表(source="filename",modid=家族键(第二/三级为减尾键,
        第四级为剥平台词后的减尾键),按 modid 升序)。
    """
    src_norm = {p: normalize_jar_family(p) for p in src_only}
    dst_norm = {p: normalize_jar_family(p) for p in dst_only}

    def _group(norm: dict[str, tuple[str, str, str]]) -> dict[str, list[str]]:
        by: dict[str, list[str]] = {}
        for p, (fam, _, _) in norm.items():
            if fam:
                by.setdefault(fam, []).append(p)
        return by

    pairs: list[ModPair] = []
    paired: set[str] = set()
    # 第一级:家族键全等(0.6.3 行为原样)
    by_src, by_dst = _group(src_norm), _group(dst_norm)
    for fam in sorted(set(by_src) & set(by_dst)):
        s_list, d_list = by_src[fam], by_dst[fam]
        if len(s_list) != 1 or len(d_list) != 1:
            continue  # 歧义放弃,不猜
        s, d = s_list[0], d_list[0]
        s_sig, d_sig = src_norm[s][1], dst_norm[d][1]
        pairs.append(
            ModPair(
                modid=fam,
                kind="upgrade" if s_sig != d_sig else "renamed",
                src_files=[s], dst_files=[d],
                src_version=s_sig or None, dst_version=d_sig or None,
                source="filename",
            )
        )
        paired.update((s, d))
    # 第二级:减尾键相等 + 同版本签名 + 异尾缀 → rebuilt(仅第一级残余)
    r_src: dict[str, list[str]] = {}
    r_dst: dict[str, list[str]] = {}
    for p, (fam, _, tail) in src_norm.items():
        if p in paired or not fam:
            continue
        red = _reduced_family(fam, tail)
        if red:
            r_src.setdefault(red, []).append(p)
    for p, (fam, _, tail) in dst_norm.items():
        if p in paired or not fam:
            continue
        red = _reduced_family(fam, tail)
        if red:
            r_dst.setdefault(red, []).append(p)
    for red in sorted(set(r_src) & set(r_dst)):
        s_list, d_list = r_src[red], r_dst[red]
        if len(s_list) != 1 or len(d_list) != 1:
            continue  # 歧义放弃,不猜
        s, d = s_list[0], d_list[0]
        s_sig, d_sig = src_norm[s][1], dst_norm[d][1]
        s_tail, d_tail = src_norm[s][2], dst_norm[d][2]
        if s_sig != d_sig or s_tail == d_tail:
            continue  # 版本不同非本级职责;尾缀相同属第一级改名语义(此处理论死枝守卫)
        pairs.append(
            ModPair(
                modid=red,
                kind="rebuilt",
                src_files=[s], dst_files=[d],
                src_version=s_sig or None, dst_version=d_sig or None,
                source="filename",
            )
        )
        paired.update((s, d))
    # 第三级(F21):减尾键相等 + 版本签名不同 → upgrade(版本与尾缀同变;
    # 一级因家族键含尾缀词而失配,二级因要求同签名而失配,本级收口)
    for red in sorted(set(r_src) & set(r_dst)):
        s_list = [p for p in r_src[red] if p not in paired]
        d_list = [p for p in r_dst[red] if p not in paired]
        if len(s_list) != 1 or len(d_list) != 1:
            continue  # 歧义放弃,不猜
        s, d = s_list[0], d_list[0]
        if src_norm[s][1] == dst_norm[d][1]:
            continue  # 同签名属第二级语义(此处理论死枝守卫)
        pairs.append(
            ModPair(
                modid=red,
                kind="upgrade",
                src_files=[s], dst_files=[d],
                src_version=src_norm[s][1] or None,
                dst_version=dst_norm[d][1] or None,
                source="filename",
            )
        )
        paired.update((s, d))
    # 第四级(F22):减尾键剥平台装饰词后相等 → upgrade/renamed(作者改命名风格)
    p_src: dict[str, list[str]] = {}
    p_dst: dict[str, list[str]] = {}
    for p, (fam, _, tail) in src_norm.items():
        if p in paired or not fam:
            continue
        stripped = _platform_stripped(fam, tail)
        if stripped:
            p_src.setdefault(stripped, []).append(p)
    for p, (fam, _, tail) in dst_norm.items():
        if p in paired or not fam:
            continue
        stripped = _platform_stripped(fam, tail)
        if stripped:
            p_dst.setdefault(stripped, []).append(p)
    for key in sorted(set(p_src) & set(p_dst)):
        s_list, d_list = p_src[key], p_dst[key]
        if len(s_list) != 1 or len(d_list) != 1:
            continue  # 歧义放弃,不猜
        s, d = s_list[0], d_list[0]
        s_sig, d_sig = src_norm[s][1], dst_norm[d][1]
        pairs.append(
            ModPair(
                modid=key,
                kind="upgrade" if s_sig != d_sig else "renamed",
                src_files=[s], dst_files=[d],
                src_version=s_sig or None, dst_version=d_sig or None,
                source="filename",
            )
        )
    pairs.sort(key=lambda p: p.modid)
    return pairs


def merge_mod_pairs(
    registry_pairs: list[ModPair], filename_pairs: list[ModPair]
) -> list[ModPair]:
    """合并两源配对:registry 优先,其覆盖的文件不再保留 filename 对。"""
    covered = {f for p in registry_pairs for f in (*p.src_files, *p.dst_files)}
    merged = list(registry_pairs)
    for p in filename_pairs:
        if any(f in covered for f in (*p.src_files, *p.dst_files)):
            continue
        merged.append(p)
    return sorted(merged, key=lambda p: p.modid)
