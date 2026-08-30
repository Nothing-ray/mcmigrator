# Mod 感知 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add mod awareness to the migration tool — read `mods/*.jar` metadata, identify orphan configs (mod not installed), fix .bak false positives via MD5 content comparison, and check mod version compatibility.

**Architecture:** New `moddb.py` module parses jar files' `META-INF/neoforge.mods.toml` to build a `ModRegistry`. Config files are mapped to modids via filename convention + override table. Orphan configs (modid not in target's registry) generate dynamic rules inserted at `user > ORPHAN > REBUILD > whitelist` priority. The planner compares parent config MD5 with .bak MD5 to detect auto-generated .bak. Version compatibility is a report-layer warning (no plan data change).

**Tech Stack:** Python 3.11+, `tomllib` (stdlib), `zipfile` (stdlib), `pathspec`, `PyYAML`, `rich`, pytest

## Global Constraints

- All code comments/docstrings in Chinese; variable/function names in English
- All file paths use `pathlib.Path`, never string concatenation
- UTF-8 encoding for all file I/O (explicit `encoding="utf-8"`)
- `PLAN_FORMAT` stays 2; `TOOL_VERSION` bumps 0.3.0 → 0.4.0
- `SNAPSHOT_FORMAT` stays 1 (scan/diff zero regression)
- Orphan rules are plan-only (like whitelist); scan/diff unaffected
- `.bak` files already have MD5 hashes (not in bulk exclusion list)
- jar parsing must never crash on malformed input (skip + warn)

**Spec:** `Reference/specs/2026-07-13-mod-awareness-design.md`

---

### Task 1: Category.ORPHAN + Origin.ORPHAN + Version Bump

**Files:**
- Modify: `migration/rules.py:17-24` (Category enum)
- Modify: `migration/plan.py:55-68` (Origin enum)
- Modify: `migration/plan.py:72-84` (_ORIGIN_SEED dict)
- Modify: `migration/snapshot.py:12` (TOOL_VERSION)
- Modify: `migration/__init__.py:3` (__version__)
- Modify: `pyproject.toml:7` (version)
- Test: `tests/test_rules.py` (add ORPHAN assertion)
- Test: `tests/test_planner.py` (add ORPHAN assertion)

**Interfaces:**
- Produces: `Category.ORPHAN` (enum member, value `"orphan"`)
- Produces: `Origin.ORPHAN` (enum member, value `"orphan"`)
- Produces: `ORIGIN_REGISTRY["orphan"]` = `OriginSpec("👻 孤儿数据", False, False, Behavior.SKIP)`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_rules.py`:

```python
def test_category_orphan_exists():
    assert Category.ORPHAN.value == "orphan"
    assert Category.ORPHAN in {c for c in Category}
```

Add to `tests/test_planner.py` (after existing origin assertions, near line 68):

```python
def test_origin_orphan_exists():
    from migration.plan import Origin, ORIGIN_REGISTRY
    assert Origin.ORPHAN.value == "orphan"
    spec = ORIGIN_REGISTRY["orphan"]
    assert spec.behavior == Behavior.SKIP
    assert spec.default_visible is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rules.py::test_category_orphan_exists tests/test_planner.py::test_origin_orphan_exists -v`
Expected: FAIL with `AttributeError: ORPHAN is not a valid Category` / `Origin`

- [ ] **Step 3: Add Category.ORPHAN to rules.py**

In `migration/rules.py`, add `ORPHAN` to the `Category` enum (after `REBUILD`):

```python
class Category(Enum):
    """文件迁移决策类别。"""

    NEVER = "never"
    MUST_MIGRATE = "must_migrate"
    REBUILD = "rebuild"
    ORPHAN = "orphan"  # mod 未安装的孤儿 config
    UNKNOWN = "unknown"
    ASK = "ask"
```

- [ ] **Step 4: Add Origin.ORPHAN to plan.py**

In `migration/plan.py`, add `ORPHAN` to the `Origin` enum (after `REBUILD`):

```python
class Origin(str, Enum):
    """单个文件的语义来源(reporter 关心,随路线图增长)。"""

    MUST_MIGRATE = "must_migrate"
    CONFIG_MODIFIED = "config_modified"
    BAK_FILE = "bak_file"
    MOD_ADDED = "mod_added"
    IDENTICAL = "identical"
    NEVER = "never"
    DEFAULT_CONFIG = "default_config"
    REBUILD = "rebuild"
    ORPHAN = "orphan"
    MOD_SHARED = "mod_shared"
    MOD_TARGET_ONLY = "mod_target_only"
    NEEDS_REVIEW = "needs_review"
```

Add to `_ORIGIN_SEED` dict (after `"rebuild"` entry):

```python
    "orphan":          OriginSpec("👻 孤儿数据",         False, False, Behavior.SKIP),
```

- [ ] **Step 5: Bump versions**

In `migration/snapshot.py`, change:
```python
TOOL_VERSION = "0.4.0"
```

In `migration/__init__.py`, change:
```python
__version__ = "0.4.0"
```

In `pyproject.toml`, change line 7:
```python
version = "0.4.0"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_rules.py::test_category_orphan_exists tests/test_planner.py::test_origin_orphan_exists -v`
Expected: PASS

- [ ] **Step 7: Run full test suite to verify zero regression**

Run: `pytest -q && ruff check .`
Expected: All existing tests pass, ruff clean.

- [ ] **Step 8: Commit**

```bash
git add migration/rules.py migration/plan.py migration/snapshot.py migration/__init__.py pyproject.toml tests/test_rules.py tests/test_planner.py
git commit -m "feat: Category.ORPHAN + Origin.ORPHAN + version bump 0.4.0"
```

---

### Task 2: moddb.py — ModInfo + ModRegistry + scan_mods (Jar Parsing)

**Files:**
- Create: `migration/moddb.py`
- Create: `tests/test_moddb.py`
- Modify: `tests/conftest.py` (add fake jar fixture)

**Interfaces:**
- Produces: `ModInfo(modid, version, jar_filename, neoforge_range)` — frozen dataclass
- Produces: `ModRegistry` — container with `__contains__`, `get`, `modids`
- Produces: `scan_mods(version_dir: Path) -> ModRegistry` — reads `mods/*.jar`
- Consumes: `pathlib.Path`, `zipfile`, `tomllib` (stdlib)

- [ ] **Step 1: Write failing tests**

Create `tests/test_moddb.py`:

```python
"""moddb 模块测试:jar 解析 + config→modid 映射 + orphan 规则 + 版本兼容。"""

from pathlib import Path

from migration.moddb import ModInfo, ModRegistry, scan_mods


def make_fake_jar(
    mods_dir: Path,
    jar_name: str,
    modid: str,
    version: str = "1.0.0",
    nf_range: str | None = "[21.1.0,)",
) -> Path:
    """创建含 META-INF/neoforge.mods.toml 的合成 jar。"""
    import zipfile

    mods_dir.mkdir(parents=True, exist_ok=True)
    jar_path = mods_dir / jar_name
    deps_line = ""
    if nf_range is not None:
        deps_line = f'''
[[dependencies.{modid}]]
modId="neoforge"
type="required"
versionRange="{nf_range}"
'''
    toml = f'''modLoader="javafml"
loaderVersion="[1,)"
[[mods]]
modId="{modid}"
version="{version}"
{deps_line}
'''
    with zipfile.ZipFile(jar_path, "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", toml)
    return jar_path


def test_scan_mods_single_jar():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    make_fake_jar(tmp / "mods", "create-1.21.1-6.0.10.jar", "create", "6.0.10", "[21.1.219,)")
    reg = scan_mods(tmp)
    assert "create" in reg
    info = reg.get("create")
    assert info is not None
    assert info.version == "6.0.10"
    assert info.jar_filename == "create-1.21.1-6.0.10.jar"
    assert info.neoforge_range == "[21.1.219,)"


def test_scan_mods_multiple_jars():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    make_fake_jar(tmp / "mods", "create.jar", "create")
    make_fake_jar(tmp / "mods", "waystones.jar", "waystones", "21.1.36")
    reg = scan_mods(tmp)
    assert "create" in reg
    assert "waystones" in reg
    assert len(reg.modids) == 2


def test_scan_mods_case_insensitive_lookup():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    make_fake_jar(tmp / "mods", "x.jar", "CSC")
    reg = scan_mods(tmp)
    assert "csc" in reg  # 小写查询
    assert "CSC" in reg  # 原始查询
    assert reg.get("csc").modid == "CSC"


def test_scan_mods_jar_without_toml_skipped():
    import tempfile
    import zipfile

    tmp = Path(tempfile.mkdtemp())
    mods = tmp / "mods"
    mods.mkdir(parents=True)
    # 无 mods.toml 的 jar
    with zipfile.ZipFile(mods / "empty.jar", "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
    reg = scan_mods(tmp)
    assert len(reg.modids) == 0


def test_scan_mods_multiple_mods_in_one_jar():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    mods = tmp / "mods"
    mods.mkdir(parents=True)
    import zipfile

    toml = '''modLoader="javafml"
loaderVersion="[1,)"
[[mods]]
modId="lib"
version="1.0"
[[mods]]
modId="addon"
version="2.0"
'''
    with zipfile.ZipFile(mods / "bundle.jar", "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", toml)
    reg = scan_mods(tmp)
    assert "lib" in reg
    assert "addon" in reg


def test_scan_mods_no_neoforge_dependency():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    # nf_range=None → mods.toml 无 dependencies 段
    make_fake_jar(tmp / "mods", "simple.jar", "simple", "1.0", nf_range=None)
    reg = scan_mods(tmp)
    info = reg.get("simple")
    assert info.neoforge_range is None


def test_scan_mods_fallback_to_mods_toml():
    """旧 Forge 格式: META-INF/mods.toml (无 neoforge 前缀)。"""
    import tempfile
    import zipfile

    tmp = Path(tempfile.mkdtemp())
    mods = tmp / "mods"
    mods.mkdir(parents=True)
    toml = '''modLoader="javafml"
loaderVersion="[1,)"
[[mods]]
modId="oldmod"
version="0.1"
'''
    with zipfile.ZipFile(mods / "old.jar", "w") as z:
        z.writestr("META-INF/mods.toml", toml)
    reg = scan_mods(tmp)
    assert "oldmod" in reg


def test_scan_mods_empty_mods_dir():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    (tmp / "mods").mkdir(parents=True)
    reg = scan_mods(tmp)
    assert len(reg.modids) == 0


def test_scan_mods_no_mods_dir():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    reg = scan_mods(tmp)
    assert len(reg.modids) == 0


def test_mod_registry_contains_case_insensitive():
    reg = ModRegistry()
    reg.add(ModInfo(modid="Create", version="1.0", jar_filename="x.jar", neoforge_range=None))
    assert "create" in reg
    assert "CREATE" in reg
    assert "Create" in reg
    assert "other" not in reg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_moddb.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'migration.moddb'`

- [ ] **Step 3: Implement moddb.py — ModInfo + ModRegistry + scan_mods**

Create `migration/moddb.py`:

```python
"""Mod 感知模块:jar 解析 + config→modid 映射 + orphan 规则生成 + 版本兼容检查。

读取 mods/*.jar 的 META-INF/neoforge.mods.toml 提取 modid/version/依赖,
用于识别孤儿 config、检查 mod 版本兼容性。
"""

from __future__ import annotations

import logging
import re
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_moddb.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest -q && ruff check .`
Expected: All tests pass, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add migration/moddb.py tests/test_moddb.py
git commit -m "feat(moddb): jar 解析 — ModInfo/ModRegistry/scan_mods"
```

---

### Task 3: moddb.py — Config→Modid Mapping + Override Table + Orphan Rule Generation

**Files:**
- Modify: `migration/moddb.py` (add mapping + orphan functions)
- Create: `migration/data/mod_config_map.yaml`
- Modify: `tests/test_moddb.py` (add mapping + orphan tests)
- Modify: `pyproject.toml` (ensure package-data includes new yaml — already covered by `data/*.yaml`)

**Interfaces:**
- Produces: `extract_modid_candidate(path: str) -> str | None`
- Produces: `OverrideTable` class with `.lookup(path) -> str | None`
- Produces: `load_mod_config_map() -> OverrideTable`
- Produces: `map_config_to_mod(path, dst_mods, override) -> tuple[str | None, bool]`
- Produces: `generate_orphan_rules(src_entries, dst_mods, override) -> list[Rule]`
- Consumes: `ModRegistry` (Task 2), `Rule` from `migration.rules`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_moddb.py`:

```python
from migration.moddb import (
    OverrideTable,
    extract_modid_candidate,
    generate_orphan_rules,
    load_mod_config_map,
    map_config_to_mod,
)
from migration.moddb import ModRegistry, ModInfo
from migration.rules import Category, Rule
from migration.snapshot import FileEntry


# --- extract_modid_candidate ---


def test_extract_candidate_subdirectory():
    assert extract_modid_candidate("config/jade/foo.json") == "jade"


def test_extract_candidate_top_level_with_suffix():
    assert extract_modid_candidate("config/create-client.toml") == "create"


def test_extract_candidate_no_suffix():
    assert extract_modid_candidate("config/distanthorizons.toml") == "distanthorizons"


def test_extract_candidate_distant_horizons_camelcase():
    assert extract_modid_candidate("config/DistantHorizons.toml") == "distanthorizons"


def test_extract_candidate_core_config_returns_none():
    assert extract_modid_candidate("config/fml.toml") is None
    assert extract_modid_candidate("config/neoforge-client.toml") is None
    assert extract_modid_candidate("config/neoforge-common.toml") is None


def test_extract_candidate_bak_returns_none():
    assert extract_modid_candidate("config/create-1.toml.bak") is None


def test_extract_candidate_spaces_returns_none():
    assert extract_modid_candidate("config/Vital Herbs Config.toml") is None


def test_extract_candidate_non_config_returns_none():
    assert extract_modid_candidate("options.txt") is None
    assert extract_modid_candidate("kubejs/x.js") is None


def test_extract_candidate_underscore_preserved():
    assert extract_modid_candidate("config/ars_nouveau-client.toml") == "ars_nouveau"


# --- OverrideTable ---


def test_override_table_lookup_match():
    table = OverrideTable([("config/xaero/**", "xaerominimap", "test")])
    assert table.lookup("config/xaero/minimap.json") == "xaerominimap"


def test_override_table_lookup_no_match():
    table = OverrideTable([("config/xaero/**", "xaerominimap", "test")])
    assert table.lookup("config/create.toml") is None


def test_override_table_empty():
    table = OverrideTable([])
    assert table.lookup("config/anything.toml") is None


def test_load_mod_config_map_has_xaero():
    table = load_mod_config_map()
    assert table.lookup("config/xaero/minimap.json") == "xaerominimap"
    assert table.lookup("config/xaerohud.txt") == "xaerominimap"


# --- map_config_to_mod ---


def _reg(*modids: str) -> ModRegistry:
    reg = ModRegistry()
    for mid in modids:
        reg.add(ModInfo(modid=mid, version="1.0", jar_filename=f"{mid}.jar", neoforge_range=None))
    return reg


def test_map_config_mod_in_dst_not_orphan():
    reg = _reg("create")
    override = OverrideTable([])
    modid, is_orphan = map_config_to_mod("config/create-client.toml", reg, override)
    assert modid == "create"
    assert is_orphan is False


def test_map_config_mod_not_in_dst_is_orphan():
    reg = _reg("other")
    override = OverrideTable([])
    modid, is_orphan = map_config_to_mod("config/jade/foo.json", reg, override)
    assert modid == "jade"
    assert is_orphan is True


def test_map_config_underscore_fallback():
    reg = _reg("tide")
    override = OverrideTable([])
    modid, is_orphan = map_config_to_mod("config/tide_client.json5", reg, override)
    assert modid == "tide"
    assert is_orphan is False


def test_map_config_underscore_both_not_in_dst():
    reg = _reg("other")
    override = OverrideTable([])
    modid, is_orphan = map_config_to_mod("config/ars_nouveau-client.toml", reg, override)
    assert modid == "ars_nouveau"
    assert is_orphan is True


def test_map_config_override_table():
    reg: ModRegistry = ModRegistry()  # 空 dst, xaerominimap 也不在
    override = OverrideTable([("config/xaero/**", "xaerominimap", "test")])
    modid, is_orphan = map_config_to_mod("config/xaero/minimap.json", reg, override)
    assert modid == "xaerominimap"
    assert is_orphan is True


def test_map_config_override_mod_in_dst():
    reg = _reg("xaerominimap")
    override = OverrideTable([("config/xaero/**", "xaerominimap", "test")])
    modid, is_orphan = map_config_to_mod("config/xaero/minimap.json", reg, override)
    assert modid == "xaerominimap"
    assert is_orphan is False


def test_map_config_cannot_determine_returns_none():
    reg = _reg("create")
    override = OverrideTable([])
    modid, is_orphan = map_config_to_mod("config/Vital Herbs Config.toml", reg, override)
    assert modid is None
    assert is_orphan is False


# --- generate_orphan_rules ---


def test_generate_orphan_rules_basic():
    dst = _reg("create")  # jade 不在 dst
    override = OverrideTable([])
    src_entries = [
        FileEntry("config/create-client.toml", 10, "a"),
        FileEntry("config/jade/foo.json", 5, "b"),
    ]
    rules = generate_orphan_rules(src_entries, dst, override)
    paths = [r.match for r in rules]
    assert "config/jade/foo.json" in paths
    assert "config/create-client.toml" not in paths
    assert all(r.decide == Category.ORPHAN for r in rules)
    assert all(r.source == "orphan" for r in rules)


def test_generate_orphan_rules_skips_bak_files():
    dst: ModRegistry = ModRegistry()  # 空
    override = OverrideTable([])
    src_entries = [
        FileEntry("config/create.toml", 10, "a"),
        FileEntry("config/create-1.toml.bak", 8, "b"),
    ]
    rules = generate_orphan_rules(src_entries, dst, override)
    paths = [r.match for r in rules]
    assert "config/create.toml" in paths
    assert "config/create-1.toml.bak" not in paths  # .bak 不生成 orphan 规则


def test_generate_orphan_rules_skips_non_config():
    dst: ModRegistry = ModRegistry()
    override = OverrideTable([])
    src_entries = [
        FileEntry("options.txt", 10, "a"),
        FileEntry("config/jade/foo.json", 5, "b"),
    ]
    rules = generate_orphan_rules(src_entries, dst, override)
    paths = [r.match for r in rules]
    assert "options.txt" not in paths
    assert "config/jade/foo.json" in paths


def test_generate_orphan_rules_skips_indeterminate():
    """无法确定 modid 的 config → 不生成 orphan 规则(保守)。"""
    dst: ModRegistry = ModRegistry()
    override = OverrideTable([])
    src_entries = [
        FileEntry("config/Vital Herbs Config.toml", 10, "a"),
    ]
    rules = generate_orphan_rules(src_entries, dst, override)
    assert len(rules) == 0


def test_generate_orphan_rules_override_not_in_dst():
    dst: ModRegistry = ModRegistry()  # xaerominimap 不在 dst
    override = OverrideTable([("config/xaero/**", "xaerominimap", "test")])
    src_entries = [
        FileEntry("config/xaero/minimap.json", 5, "b"),
    ]
    rules = generate_orphan_rules(src_entries, dst, override)
    assert len(rules) == 1
    assert rules[0].match == "config/xaero/minimap.json"
    assert rules[0].decide == Category.ORPHAN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_moddb.py -k "extract_candidate or override_table or map_config or generate_orphan" -v`
Expected: FAIL with `ImportError: cannot import name 'extract_modid_candidate'`

- [ ] **Step 3: Create mod_config_map.yaml**

Create `migration/data/mod_config_map.yaml`:

```yaml
# 非约定式 config→modid 映射覆盖表
# 约定: config/<modid>* 或 config/<modid>/* → modid (自动匹配)
# 此表仅列不遵循约定的例外
version: 1
mappings:
  - match: "config/xaero/**"
    modid: "xaerominimap"
    reason: "目录名 xaero ≠ modid xaerominimap"
  - match: "config/xaerohud.txt"
    modid: "xaerominimap"
    reason: "文件名无 modid 前缀特征"
  - match: "config/xaeropatreon.txt"
    modid: "xaerominimap"
    reason: "同上"
```

- [ ] **Step 4: Implement mapping + override + orphan functions in moddb.py**

Append to `migration/moddb.py`:

```python
import fnmatch
import json
from importlib import resources

import pathspec
import yaml

from .rules import Category, Rule
from .snapshot import FileEntry

# 核心配置前缀(mod 非管辖,rebuild 规则管)
_CORE_PREFIXES = frozenset({"fml", "neoforge", "minecraft"})

# 合法 modid 正则(小写字母开头,仅含小写字母/数字/下划线)
_VALID_MODID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# 已知扩展名(用于剥离)
_CONFIG_EXTENSIONS = frozenset({
    ".toml", ".json", ".cfg", ".snbt", ".properties",
    ".jsonc", ".json5", ".yaml", ".txt", ".ini",
})


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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_moddb.py -v`
Expected: All tests PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest -q && ruff check .`
Expected: All pass, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add migration/moddb.py migration/data/mod_config_map.yaml tests/test_moddb.py
git commit -m "feat(moddb): config→modid 映射 + 覆盖表 + orphan 规则生成"
```

---

### Task 4: moddb.py — Version Compatibility Check

**Files:**
- Modify: `migration/moddb.py` (add compat functions)
- Modify: `tests/test_moddb.py` (add compat tests)

**Interfaces:**
- Produces: `CompatWarning(modid, jar_filename, mod_version, required_range, dst_neoforge)` — frozen dataclass
- Produces: `read_neoforge_version(version_dir: Path) -> str | None`
- Produces: `check_version_range(version: str, range_str: str) -> bool`
- Produces: `check_mod_compat(mod_added_paths, src_mods, dst_nf_version) -> list[CompatWarning]`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_moddb.py`:

```python
from migration.moddb import (
    CompatWarning,
    check_mod_compat,
    check_version_range,
    read_neoforge_version,
)


# --- check_version_range ---


def test_version_range_open_right():
    assert check_version_range("21.1.233", "[21.1.219,)") is True
    assert check_version_range("21.1.219", "[21.1.219,)") is True  # 边界:含下界
    assert check_version_range("21.1.218", "[21.1.219,)") is False


def test_version_range_closed_right():
    assert check_version_range("21.1.228", "[21.1.0,21.1.228]") is True
    assert check_version_range("21.1.229", "[21.1.0,21.1.228]") is False


def test_version_range_exclusive_left():
    assert check_version_range("21.1.1", "(21.1.0,)") is True
    assert check_version_range("21.1.0", "(21.1.0,)") is False  # 排除下界


def test_version_range_exact():
    assert check_version_range("21.1.228", "[21.1.228]") is True
    assert check_version_range("21.1.229", "[21.1.228]") is False


def test_version_range_malformed_returns_true():
    """格式异常 → 保守认为兼容(不阻断迁移)。"""
    assert check_version_range("21.1.233", "garbage") is True
    assert check_version_range("21.1.233", "") is True


# --- read_neoforge_version ---


def test_read_neoforge_version_from_json(tmp_path):
    import json

    version_name = "1.21.1-NeoForge_21.1.233"
    ver_dir = tmp_path / version_name
    ver_dir.mkdir()
    json_data = {
        "arguments": {
            "game": ["--fml.neoforgeVersion", "21.1.233", "--fml.mcVersion", "1.21.1"],
        }
    }
    (ver_dir / f"{version_name}.json").write_text(json.dumps(json_data), encoding="utf-8")
    assert read_neoforge_version(ver_dir) == "21.1.233"


def test_read_neoforge_version_missing_json(tmp_path):
    ver_dir = tmp_path / "empty"
    ver_dir.mkdir()
    assert read_neoforge_version(ver_dir) is None


def test_read_neoforge_version_no_arg(tmp_path):
    import json

    ver_dir = tmp_path / "noversion"
    ver_dir.mkdir()
    (ver_dir / "noversion.json").write_text(
        json.dumps({"arguments": {"game": ["--fml.mcVersion", "1.21.1"]}}),
        encoding="utf-8",
    )
    assert read_neoforge_version(ver_dir) is None


# --- check_mod_compat ---


def test_check_mod_compat_incompatible():
    src_mods = ModRegistry()
    src_mods.add(ModInfo("cp_lib", "5.0.18", "cp_lib.jar", "[21.1.233,)"))
    warnings = check_mod_compat(
        mod_added_paths=["mods/cp_lib.jar"],
        src_mods=src_mods,
        dst_neoforge="21.1.228",
    )
    assert len(warnings) == 1
    assert warnings[0].modid == "cp_lib"
    assert warnings[0].required_range == "[21.1.233,)"
    assert warnings[0].dst_neoforge == "21.1.228"


def test_check_mod_compat_compatible():
    src_mods = ModRegistry()
    src_mods.add(ModInfo("create", "6.0.10", "create.jar", "[21.1.219,)"))
    warnings = check_mod_compat(
        mod_added_paths=["mods/create.jar"],
        src_mods=src_mods,
        dst_neoforge="21.1.233",
    )
    assert len(warnings) == 0


def test_check_mod_compat_no_range():
    src_mods = ModRegistry()
    src_mods.add(ModInfo("simple", "1.0", "simple.jar", None))
    warnings = check_mod_compat(
        mod_added_paths=["mods/simple.jar"],
        src_mods=src_mods,
        dst_neoforge="21.1.228",
    )
    assert len(warnings) == 0  # 无 range → 不检查


def test_check_mod_compat_empty():
    src_mods = ModRegistry()
    warnings = check_mod_compat(
        mod_added_paths=[],
        src_mods=src_mods,
        dst_neoforge="21.1.228",
    )
    assert len(warnings) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_moddb.py -k "version_range or neoforge_version or mod_compat" -v`
Expected: FAIL with `ImportError: cannot import name 'CompatWarning'`

- [ ] **Step 3: Implement compat functions in moddb.py**

Append to `migration/moddb.py`:

```python
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
    inclusive_start = range_str[0] == "["
    inclusive_end = range_str[-1] == "]"
    inner = range_str[1:-1]
    parts = inner.split(",")
    start = parts[0].strip() if parts else ""
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
    args = data.get("arguments", {}).get("game", [])
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_moddb.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest -q && ruff check .`
Expected: All pass, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add migration/moddb.py tests/test_moddb.py
git commit -m "feat(moddb): 版本兼容检查 — CompatWarning/check_version_range/read_neoforge_version"
```

---

### Task 5: Differ ORPHAN Routing + Planner .bak MD5 Comparison

**Files:**
- Modify: `migration/differ.py:96-101` (add ORPHAN routing)
- Modify: `migration/planner.py:38-55` (has_bak_sibling → find_bak_siblings)
- Modify: `migration/planner.py:164-172` (_for_never: add orphan note)
- Modify: `migration/planner.py:188-219` (_for_candidate: MD5 comparison)
- Modify: `tests/test_differ.py` (add ORPHAN routing test)
- Modify: `tests/test_planner.py` (update has_bak_sibling tests, add MD5 tests)

**Interfaces:**
- Consumes: `Category.ORPHAN` (Task 1), `Origin.ORPHAN` (Task 1)
- Produces: `find_bak_siblings(path, src_paths) -> list[str]` (replaces `has_bak_sibling`)
- Produces: Differ routes `Category.ORPHAN` → `never` bucket with `note="orphan"`
- Produces: Planner `_for_never` maps `note="orphan"` → `Origin.ORPHAN`
- Produces: Planner `_for_candidate` compares parent MD5 with .bak MD5

- [ ] **Step 1: Write failing tests**

Add to `tests/test_differ.py`:

```python
def test_orphan_classified_goes_never_bucket_with_orphan_note():
    from migration.rules import Category, Rule, RuleSet

    rs = RuleSet(rules=[Rule(match="config/jade/foo.json", decide=Category.ORPHAN)])
    clf = Classifier(rs)
    d = Differ([_e("config/jade/foo.json", md5="a")], [], clf).diff()
    matches = [i for i in d.never if i.path == "config/jade/foo.json"]
    assert len(matches) == 1
    assert matches[0].note == "orphan"
    assert not any(i.path == "config/jade/foo.json" for i in d.candidate)
```

Add to `tests/test_planner.py`:

```python
def test_never_note_orphan_goes_skip_orphan():
    report = DiffReport(
        never=[DiffItem("config/jade/foo.json", _e("config/jade/foo.json"), None, "orphan")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "config/jade/foo.json")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.ORPHAN


def test_find_bak_siblings_returns_paths():
    from migration.planner import find_bak_siblings

    src = {"config/create.toml", "config/create-1.toml.bak", "config/create-2.toml.bak"}
    result = find_bak_siblings("config/create.toml", src)
    assert "config/create-1.toml.bak" in result
    assert "config/create-2.toml.bak" in result
    assert len(result) == 2


def test_find_bak_siblings_plain_bak():
    from migration.planner import find_bak_siblings

    src = {"config/create.toml", "config/create.toml.bak"}
    result = find_bak_siblings("config/create.toml", src)
    assert result == ["config/create.toml.bak"]


def test_find_bak_siblings_no_bak_returns_empty():
    from migration.planner import find_bak_siblings

    assert find_bak_siblings("config/foo.toml", {"config/foo.toml"}) == []


def test_find_bak_siblings_does_not_false_match_stem_with_hyphens():
    from migration.planner import find_bak_siblings

    src = {"config/dragon-survival.toml", "config/dragon-survival-extra.toml.bak"}
    assert find_bak_siblings("config/dragon-survival.toml", src) == []


def test_bak_md5_equal_downgrades_to_default_config():
    """parent config MD5 == .bak MD5 → 自动生成 → default_config (SKIP)。"""
    report = DiffReport(
        candidate=[DiffItem("config/royal.toml", _e("config/royal.toml", md5="aaa"), None, "new")]
    )
    src = [_e("config/royal.toml", md5="aaa"), _e("config/royal-1.toml.bak", md5="aaa")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/royal.toml")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.DEFAULT_CONFIG
    assert "identical" in a.reason


def test_bak_md5_different_keeps_config_modified():
    """parent config MD5 != .bak MD5 → 玩家改过 → config_modified (COPY)。"""
    report = DiffReport(
        candidate=[DiffItem("config/create.toml", _e("config/create.toml", md5="aaa"), None, "new")]
    )
    src = [_e("config/create.toml", md5="aaa"), _e("config/create-1.toml.bak", md5="bbb")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/create.toml")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.CONFIG_MODIFIED
    assert "differs" in a.reason


def test_bak_md5_multiple_all_equal_downgrades():
    """多个 .bak 全部与 config 相同 → 降级。"""
    report = DiffReport(
        candidate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="aaa"), None, "new")]
    )
    src = [
        _e("config/foo.toml", md5="aaa"),
        _e("config/foo-1.toml.bak", md5="aaa"),
        _e("config/foo-2.toml.bak", md5="aaa"),
    ]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.DEFAULT_CONFIG


def test_bak_md5_multiple_one_differs_keeps_modified():
    """多个 .bak 任一与 config 不同 → 保持 config_modified。"""
    report = DiffReport(
        candidate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="aaa"), None, "new")]
    )
    src = [
        _e("config/foo.toml", md5="aaa"),
        _e("config/foo-1.toml.bak", md5="aaa"),
        _e("config/foo-2.toml.bak", md5="bbb"),
    ]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.CONFIG_MODIFIED


def test_bak_no_md5_keeps_old_behavior():
    """无 MD5(size-based)→ 保守走旧逻辑(有 .bak → config_modified)。"""
    report = DiffReport(
        candidate=[DiffItem("config/big.toml", _e("config/big.toml", size=100, md5=None), None, "new")]
    )
    src = [
        _e("config/big.toml", size=100, md5=None),
        _e("config/big-1.toml.bak", size=90, md5=None),
    ]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/big.toml")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.CONFIG_MODIFIED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_differ.py::test_orphan_classified_goes_never_bucket_with_orphan_note tests/test_planner.py -k "orphan or find_bak_siblings or bak_md5" -v`
Expected: FAIL (ORPHAN routing missing, find_bak_siblings not defined, MD5 comparison not implemented)

- [ ] **Step 3: Add ORPHAN routing to differ.py**

In `migration/differ.py`, add ORPHAN routing after the REBUILD check (around line 100). The current code has:

```python
            if cat == Category.NEVER:
                report.never.append(DiffItem(path, s, d, note="never"))
                continue
            if cat == Category.REBUILD:
                report.never.append(DiffItem(path, s, d, note="rebuild"))
                continue
```

Add after the REBUILD block:

```python
            if cat == Category.ORPHAN:
                report.never.append(DiffItem(path, s, d, note="orphan"))
                continue
```

- [ ] **Step 4: Replace has_bak_sibling with find_bak_siblings in planner.py**

In `migration/planner.py`, replace the `has_bak_sibling` function (lines 38-55) with:

```python
def find_bak_siblings(path: str, src_paths: set[str]) -> list[str]:
    """找到所有 .bak 兄弟文件路径(plain + versioned),可能为空。

    - plain: path + ".bak"(如 config/foo.toml.bak)
    - versioned: stem + "-[0-9]*" + suffix + ".bak"(如 config/foo-1.toml.bak)

    与 resolve_bak_parent 对偶:后者从 .bak 反推父,本函数从父找 .bak。
    """
    results: list[str] = []
    plain = path + ".bak"
    if plain in src_paths:
        results.append(plain)
    dot = path.rfind(".")
    if dot == -1:
        stem, suffix = path, ""
    else:
        stem, suffix = path[:dot], path[dot:]
    pattern = f"{stem}-[0-9]*{suffix}.bak"
    for p in src_paths:
        if fnmatch.fnmatch(p, pattern) and p not in results:
            results.append(p)
    return results
```

- [ ] **Step 5: Update _for_never to handle orphan note**

In `migration/planner.py`, update `_for_never` (line 164):

```python
    def _for_never(self, item: DiffItem) -> ActionRecord:
        if item.note == "orphan":
            origin = Origin.ORPHAN
        elif item.note == "rebuild":
            origin = Origin.REBUILD
        else:
            origin = Origin.NEVER
        return ActionRecord(
            path=item.path, behavior=Behavior.SKIP, origin=origin,
            src_size=item.src.size if item.src else None,
            dst_size=item.dst.size if item.dst else None,
            md5_match=None, confidence="high",
            reason=f"classified {origin.value}", backup_target=None,
        )
```

- [ ] **Step 6: Update _for_candidate with MD5 comparison**

In `migration/planner.py`, replace `_for_candidate` (lines 188-219):

```python
    def _for_candidate(self, item: DiffItem) -> ActionRecord:
        """candidate 决策树(config/ 前缀走 .bak+MD5 判定,非 config → ask)。

        白名单命中的文件已在规则层归 must_migrate(不进 candidate),故此处
        candidate 已是「白名单未命中的残余」。.bak 存在时进一步检查 MD5:
        parent MD5 == .bak MD5 → 自动生成(降级 default_config);
        parent MD5 != .bak MD5 → 玩家改过(config_modified)。
        """
        if not item.path.startswith(CONFIG_PREFIX):
            return self._ask(item, reason="candidate (non-config, needs user confirm)")
        src_paths = set(self.src_index.keys())
        bak_paths = find_bak_siblings(item.path, src_paths)
        if bak_paths:
            parent_md5 = item.src.md5 if item.src else None
            bak_md5s: list[str | None] = []
            for p in bak_paths:
                bak_entry = self.src_index.get(p)
                bak_md5s.append(bak_entry.md5 if bak_entry else None)
            has_md5 = parent_md5 is not None and all(m is not None for m in bak_md5s)
            if has_md5 and all(m == parent_md5 for m in bak_md5s):
                return ActionRecord(
                    path=item.path, behavior=Behavior.SKIP, origin=Origin.DEFAULT_CONFIG,
                    src_size=item.src.size if item.src else None,
                    dst_size=item.dst.size if item.dst else None,
                    md5_match=_md5_match(item.src, item.dst), confidence="high",
                    reason=".bak content identical to config (auto-generated)",
                    backup_target=None,
                )
            if item.note == "new":
                return ActionRecord(
                    path=item.path, behavior=Behavior.COPY, origin=Origin.CONFIG_MODIFIED,
                    src_size=item.src.size if item.src else None, dst_size=None,
                    md5_match=None, confidence="high",
                    reason=".bak content differs (player modified)", backup_target=None,
                )
            return ActionRecord(
                path=item.path, behavior=Behavior.COPY, origin=Origin.CONFIG_MODIFIED,
                src_size=item.src.size if item.src else None,
                dst_size=item.dst.size if item.dst else None,
                md5_match=_md5_match(item.src, item.dst), confidence="high",
                reason=".bak content differs (player modified)",
                backup_target=_backup_target(item.path),
            )
        return ActionRecord(
            path=item.path, behavior=Behavior.SKIP, origin=Origin.DEFAULT_CONFIG,
            src_size=item.src.size if item.src else None,
            dst_size=item.dst.size if item.dst else None,
            md5_match=_md5_match(item.src, item.dst), confidence="high",
            reason="no .bak, not in whitelist", backup_target=None,
        )
```

- [ ] **Step 7: Update existing tests that reference has_bak_sibling**

In `tests/test_planner.py`, the test `test_bak_does_not_false_match_stem_with_hyphens` (line 204) references `has_bak_sibling`. Replace it:

```python
def test_find_bak_siblings_does_not_false_match_stem_with_hyphens():
    from migration.planner import find_bak_siblings

    src = {
        "config/dragon-survival.toml",
        "config/dragon-survival-extra.toml.bak",
    }
    assert find_bak_siblings("config/dragon-survival.toml", src) == []
```

Also update `test_config_candidate_with_plain_bak_goes_copy_config_modified` (line 129) — the reason changed from `".bak sibling exists"` to `".bak content differs (player modified)"`. Update the assertion:

```python
    assert "differs" in a.reason
```

And update `test_config_candidate_with_versioned_bak_goes_copy_with_backup` (line 142) — same reason change. The test doesn't assert on reason, so it should pass as-is. Verify.

And update `test_multiple_bak_versions_also_match` (line 193) — the .bak files have default md5="x" and parent has md5="a" → MD5 differs → config_modified. Test should still pass. Verify.

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_differ.py tests/test_planner.py -v`
Expected: All tests PASS

- [ ] **Step 9: Run full test suite**

Run: `pytest -q && ruff check .`
Expected: All pass, ruff clean.

- [ ] **Step 10: Commit**

```bash
git add migration/differ.py migration/planner.py tests/test_differ.py tests/test_planner.py
git commit -m "feat: differ ORPHAN 路由 + planner .bak MD5 内容比对 + find_bak_siblings"
```

---

### Task 6: Reporter — Orphan Group + Footnote + Compat Warnings

**Files:**
- Modify: `migration/reporter.py:107-177` (PlanReporter)
- Modify: `tests/test_reporter.py` (add orphan + compat tests)

**Interfaces:**
- Produces: `PlanReporter.render()` shows orphan group + footnote
- Produces: `PlanReporter.render_compat_warnings(warnings)` method
- Consumes: `Origin.ORPHAN` (Task 1), `CompatWarning` (Task 4)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_reporter.py`:

```python
def test_plan_reporter_orphan_in_summary():
    from migration.differ import DiffItem, DiffReport
    from migration.plan import Behavior, Origin
    from migration.planner import Planner
    from migration.reporter import PlanReporter, PlanOptions
    from migration.snapshot import FileEntry

    report = DiffReport(
        never=[DiffItem("config/jade/foo.json", FileEntry("config/jade/foo.json", 5, "a"),
                        None, "orphan")]
    )
    plan = Planner(report, {"config/jade/foo.json": FileEntry("config/jade/foo.json", 5, "a")}).plan()
    plan.src, plan.dst = "228", "233"
    doc = json.loads(PlanReporter(plan, src_version="228", dst_version="233").to_json())
    assert doc["summary"].get("orphan", 0) == 1


def test_plan_reporter_render_orphan_group(capsys):
    from migration.differ import DiffItem, DiffReport
    from migration.plan import Behavior, Origin
    from migration.planner import Planner
    from migration.reporter import PlanReporter, PlanOptions
    from migration.snapshot import FileEntry

    report = DiffReport(
        never=[DiffItem("config/jade/foo.json", FileEntry("config/jade/foo.json", 5, "a"),
                        None, "orphan")]
    )
    plan = Planner(report, {"config/jade/foo.json": FileEntry("config/jade/foo.json", 5, "a")}).plan()
    plan.src, plan.dst = "228", "233"
    PlanReporter(plan, src_version="228", dst_version="233").render(
        PlanOptions(show_skip=True)
    )
    out = capsys.readouterr().out
    assert "孤儿" in out


def test_plan_reporter_compat_warnings_render(capsys):
    from migration.moddb import CompatWarning
    from migration.reporter import PlanReporter
    from migration.plan import MigrationPlan, ActionRecord, Behavior, Origin
    from datetime import datetime, timezone

    plan = MigrationPlan(
        src="228", dst="233",
        generated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        actions=[],
    )
    warnings = [
        CompatWarning("cp_lib", "cp_lib.jar", "5.0.18", "[21.1.233,)", "21.1.228"),
    ]
    reporter = PlanReporter(plan, src_version="228", dst_version="233")
    reporter.render_compat_warnings(warnings)
    out = capsys.readouterr().out
    assert "cp_lib" in out
    assert "21.1.233" in out
    assert "21.1.228" in out


def test_plan_reporter_compat_warnings_empty_no_output(capsys):
    from migration.reporter import PlanReporter
    from migration.plan import MigrationPlan
    from datetime import datetime, timezone

    plan = MigrationPlan(
        src="228", dst="233",
        generated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        actions=[],
    )
    reporter = PlanReporter(plan, src_version="228", dst_version="233")
    reporter.render_compat_warnings([])
    out = capsys.readouterr().out
    assert out == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reporter.py -k "orphan or compat" -v`
Expected: FAIL (render_compat_warnings not defined)

- [ ] **Step 3: Add render_compat_warnings to PlanReporter**

In `migration/reporter.py`, add this method to `PlanReporter` class (after `render` method, before end of class):

```python
    def render_compat_warnings(
        self, warnings: list, console: Console | None = None
    ) -> None:
        """渲染 mod 版本兼容警告段(如有)。"""
        if not warnings:
            return
        console = console or Console()
        console.print()
        console.print("[bold yellow]⚠️ Mod 版本兼容警告[/]")
        console.print(
            "[dim]以下玩家额外添加的 mod 可能与目标 NeoForge 版本不兼容,"
            "迁移后可能导致游戏崩溃。[/]"
        )
        tbl = Table(title="兼容警告", title_style="bold yellow")
        tbl.add_column("Mod")
        tbl.add_column("版本")
        tbl.add_column("要求 NeoForge")
        tbl.add_column("目标 NeoForge")
        for w in warnings:
            tbl.add_row(w.modid, w.mod_version, w.required_range, w.dst_neoforge)
        console.print(tbl)
```

Also add the import for `Table` if not already imported (it is already imported at line 9).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reporter.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest -q && ruff check .`
Expected: All pass, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add migration/reporter.py tests/test_reporter.py
git commit -m "feat(reporter): orphan 分组 + 兼容警告段渲染"
```

---

### Task 7: CLI — Wire Mod Awareness into Plan Command

**Files:**
- Modify: `migration/cli.py:108-143` (build_ruleset: add orphan_rules param)
- Modify: `migration/cli.py:244-279` (_cmd_plan: add mod scanning + orphan rules + compat check)
- Modify: `tests/test_cli.py` (add plan-with-mods test, if exists) or `tests/test_e2e.py`

**Interfaces:**
- Consumes: `scan_mods`, `generate_orphan_rules`, `load_mod_config_map`, `check_mod_compat`, `read_neoforge_version` (Tasks 2-4)
- Produces: `build_ruleset(..., orphan_rules=None)` — new keyword arg
- Produces: `_cmd_plan` scans mods, generates orphan rules, checks compat, passes warnings to reporter

- [ ] **Step 1: Write failing test**

Add to `tests/test_e2e.py` (or create if not exists — check first):

```python
def test_plan_with_orphan_rules_applied(tmp_path, monkeypatch):
    """plan 命令应用 orphan 规则:dst 无 jade mod → jade config 标为 orphan。"""
    import json
    import zipfile
    from pathlib import Path
    from migration.cli import main

    # 构建游戏目录
    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    src_dir.mkdir(parents=True)
    dst_dir.mkdir(parents=True)

    # src 有 jade config 但 dst 无 jade mod
    (src_dir / "config").mkdir()
    (src_dir / "config" / "jade").mkdir()
    (src_dir / "config" / "jade" / "presets.json").write_text("{}", encoding="utf-8")
    (src_dir / "options.txt").write_text("fps:120", encoding="utf-8")

    # dst 有 create mod(无 jade)
    dst_mods = dst_dir / "mods"
    dst_mods.mkdir()
    toml = 'modLoader="javafml"\nloaderVersion="[1,)"\n[[mods]]\nmodId="create"\nversion="1.0"\n'
    with zipfile.ZipFile(dst_mods / "create.jar", "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", toml)

    # src 也有 create mod
    src_mods = src_dir / "mods"
    src_mods.mkdir()
    with zipfile.ZipFile(src_mods / "create.jar", "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", toml)

    # 配置 + scan + plan
    mcmig_dir = tmp_path / ".mcmig"
    mcmig_dir.mkdir()
    (mcmig_dir / "config.yaml").write_text(f"game_root: '{game_root}'", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    assert main(["scan", "src", "--game-root", str(game_root)]) == 0
    assert main(["scan", "dst", "--game-root", str(game_root)]) == 0
    assert main(["plan", "src", "dst", "--game-root", str(game_root), "--json"]) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_e2e.py::test_plan_with_orphan_rules_applied -v`
Expected: FAIL (orphan rules not generated in plan command)

- [ ] **Step 3: Update build_ruleset to accept orphan_rules**

In `migration/cli.py`, update `build_ruleset` function signature and body:

```python
def build_ruleset(
    versions: str | list[str],
    args: argparse.Namespace,
    mcmig_dir: Path,
    *,
    with_whitelist: bool = False,
    orphan_rules: list[rules.Rule] | None = None,
) -> tuple[rules.RuleSet, list[str]]:
    """按优先级(CLI > extra > user > ORPHAN > REBUILD > whitelist > default)组装 RuleSet。

    rebuild 层对所有命令(scan/diff/plan)常开;whitelist 仅 plan 命令启用;
    orphan 规则仅 plan 命令启用(plan-only)。
    """
    from importlib import resources

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
    orphan = orphan_rules or []
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
```

- [ ] **Step 4: Update _cmd_plan to scan mods and generate orphan rules**

In `migration/cli.py`, update `_cmd_plan`:

```python
def _cmd_plan(args: argparse.Namespace) -> int:
    """plan 子命令:load snapshots → scan mods → orphan rules → diff → plan → 兼容检查 → 渲染。"""
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
    game_root = _resolve_game_root(args)
    mcmig_dir = cwd / ".mcmig"
    # 扫描 src/dst mods → 建 mod 注册表
    from .moddb import (
        check_mod_compat,
        generate_orphan_rules,
        load_mod_config_map,
        read_neoforge_version,
        scan_mods,
    )

    src_dir = _version_dir(game_root, args.src)
    dst_dir = _version_dir(game_root, args.dst)
    dst_mods = scan_mods(dst_dir)
    override = load_mod_config_map()
    orphan_rules = generate_orphan_rules(src.files, dst_mods, override)
    rs, errs = build_ruleset(
        [args.src, args.dst], args, mcmig_dir, with_whitelist=True, orphan_rules=orphan_rules
    )
    for e in errs:
        _print(f"[规则警告] {e}")
    clf = Classifier(rs)
    report = Differ(src.files, dst.files, clf).diff()
    src_index = {e.path: e for e in src.files}
    plan = Planner(report, src_index).plan()
    plan.src, plan.dst = args.src, args.dst
    # 版本兼容检查
    src_mods = scan_mods(src_dir)
    dst_nf_version = read_neoforge_version(dst_dir)
    mod_added_paths = [
        r.path for r in plan.actions if r.behavior == Behavior.COPY and r.origin == Origin.MOD_ADDED
    ]
    compat_warnings = check_mod_compat(mod_added_paths, src_mods, dst_nf_version)
    reporter = PlanReporter(plan, src_version=args.src, dst_version=args.dst)
    if args.json:
        _print(reporter.to_json())
    else:
        reporter.render(PlanOptions(show_skip=args.show_skip, category=args.category))
        reporter.render_compat_warnings(compat_warnings)
    if not args.no_save:
        try:
            plan.save(plan_path(cwd, args.src, args.dst))
        except OSError as e:
            _print(f"[警告] plan 文件写入失败(已忽略,stdout 仍有效): {e}")
    return 0
```

Add necessary imports at the top of `_cmd_plan` (or ensure they're available):

The `Behavior` and `Origin` are already imported in cli.py via `from .plan import plan_path` — need to add `Behavior, Origin` to the import. Update line 14:

```python
from .plan import Behavior, Origin, plan_path
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_e2e.py::test_plan_with_orphan_rules_applied -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest -q && ruff check .`
Expected: All pass, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add migration/cli.py tests/test_e2e.py
git commit -m "feat(cli): plan 命令接入 mod 感知 — scan_mods + orphan 规则 + 兼容检查"
```

---

### Task 8: E2E Test + README Documentation

**Files:**
- Modify: `tests/test_e2e.py` (add comprehensive plan-with-mods test)
- Modify: `README.zh-CN.md` (add classification system section)
- Modify: `README.en.md` (add classification system section)

**Interfaces:**
- Produces: E2E test verifying orphan config marking + .bak MD5 comparison + compat warnings
- Produces: README documentation of classification system

- [ ] **Step 1: Write comprehensive E2E test**

Add to `tests/test_e2e.py`:

```python
def test_plan_full_mod_awareness_e2e(tmp_path, monkeypatch):
    """端到端:orphan config 标记 + .bak MD5 降级 + 兼容检查。"""
    import json
    import zipfile
    from pathlib import Path
    from migration.cli import main

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    src_dir.mkdir(parents=True)
    dst_dir.mkdir(parents=True)

    # dst 有 create mod
    dst_mods = dst_dir / "mods"
    dst_mods.mkdir()
    create_toml = (
        'modLoader="javafml"\nloaderVersion="[1,)"\n'
        '[[mods]]\nmodId="create"\nversion="6.0.10"\n'
        '[[dependencies.create]]\nmodId="neoforge"\n'
        'type="required"\nversionRange="[21.1.0,)"\n'
    )
    with zipfile.ZipFile(dst_mods / "create.jar", "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", create_toml)

    # src 有 create mod + jade config(孤儿) + royal config(.bak MD5 相同)
    src_mods = src_dir / "mods"
    src_mods.mkdir()
    with zipfile.ZipFile(src_mods / "create.jar", "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", create_toml)

    src_config = src_dir / "config"
    src_config.mkdir()
    # 孤儿 config(jade 不在 dst mods)
    (src_config / "jade").mkdir()
    (src_config / "jade" / "presets.json").write_text("{}", encoding="utf-8")
    # .bak MD5 相同(自动生成 → 降级)
    (src_config / "royal.toml").write_text("default=true\n", encoding="utf-8")
    (src_config / "royal-1.toml.bak").write_text("default=true\n", encoding="utf-8")
    # .bak MD5 不同(玩家改过 → 保持 config_modified)
    (src_config / "create-client.toml").write_text("edited=true\n", encoding="utf-8")
    (src_config / "create-1.toml.bak").write_text("edited=false\n", encoding="utf-8")

    # options.txt
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (dst_dir / "options.txt").write_text("fps:60\n", encoding="utf-8")

    mcmig_dir = tmp_path / ".mcmig"
    mcmig_dir.mkdir()
    (mcmig_dir / "config.yaml").write_text(f"game_root: '{game_root}'", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    assert main(["scan", "src", "--game-root", str(game_root)]) == 0
    assert main(["scan", "dst", "--game-root", str(game_root)]) == 0

    # 捕获 JSON plan 输出
    import io
    import sys
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rc = main(["plan", "src", "dst", "--game-root", str(game_root), "--json"])
    finally:
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
    assert rc == 0

    plan_data = json.loads(output)
    actions = {a["path"]: a for a in plan_data["actions"]}

    # 孤儿 config 标为 orphan
    jade = actions.get("config/jade/presets.json")
    assert jade is not None
    assert jade["origin"] == "orphan"
    assert jade["behavior"] == "skip"

    # .bak MD5 相同 → 降级 default_config
    royal = actions.get("config/royal.toml")
    assert royal is not None
    assert royal["origin"] == "default_config"
    assert royal["behavior"] == "skip"

    # .bak MD5 不同 → 保持 config_modified
    create = actions.get("config/create-client.toml")
    assert create is not None
    assert create["origin"] == "config_modified"
    assert create["behavior"] == "copy"
```

- [ ] **Step 2: Run E2E test**

Run: `pytest tests/test_e2e.py::test_plan_full_mod_awareness_e2e -v`
Expected: PASS

- [ ] **Step 3: Add classification section to README.zh-CN.md**

In `README.zh-CN.md`, after the "工作方式" section (around line 53), add:

```markdown
## 分类系统

`mcmig plan` 把每个文件归入一个 **origin**(语义来源),决定迁移行为:

| Origin | 行为 | 含义 | 举例 |
|--------|------|------|------|
| ✅ 必迁 | 复制 | 玩家核心数据,丢失不可逆 | `options.txt`、`saves/`、`local/ftbchunks/` |
| ✏️ 改过的 config | 复制 | 有 `.bak` 且内容不同=玩家游戏内改过 | `config/create-client.toml` |
| 📋 备份文件 | 复制 | `.bak` 文件,跟随父 config 迁移 | `config/create-1.toml.bak` |
| 📦 补 Mod | 复制 | 源独有 mod(玩家额外添加的) | `mods/extra.jar` |
| ❓ 待确认 | 询问 | 无可靠自动判定,需人工确认 | `kubejs/**`、`resourcepacks/*.zip` |
| 👻 孤儿数据 | 跳过 | 对应的 mod 未安装在目标版本,迁移无意义 | `config/jade/**`(Jade 已移除) |
| 🔒 版本敏感 | 跳过 | 版本/硬件派生,跨版本迁移高危,让目标重建 | `config/fml.toml` |
| ⚙️ 默认配置 | 跳过 | 无 `.bak` 的 mod 默认值,或 `.bak` 内容与 config 相同(自动生成) | `config/patchouli-client.toml` |
| ⛔ 不迁 | 跳过 | 临时产物/版本二进制/缓存 | `logs/`、`<ver>.jar` |
| ⏭ 一致 | 跳过 | 两边内容一致 | MD5 相同的文件 |
| 📦 共有 Mod | 跳过 | 两边都有的 mod | — |
| 📦 目标独有 Mod | 跳过 | 目标比源多出的 mod | — |

### 优先级

规则按优先级从高到低匹配(first-match-wins):

```
CLI(--include/--exclude) > 额外规则文件 > 用户 rules.yaml > 孤儿检测 > 版本敏感 > 白名单 > 内置默认
```

- **用户显式规则 > 孤儿检测**:在 `.mcmig/rules.yaml` 中写 `config/jade/** → must_migrate` 可强制迁移孤儿 config
- **孤儿检测 > 白名单**:白名单中对应 mod 已删除的条目自动失效
- **孤儿检测 = 事实判断**:mod 物理上不在目标 `mods/` 目录 → config 无人认领 → 迁移无意义

### .bak 判定法

NeoForge 在玩家游戏内修改 config 时自动生成 `.bak` 备份。工具通过比较 config 与 `.bak` 的 MD5 判断:

- **MD5 不同** → `.bak` 存的是改前的旧版本 → 玩家确实改过 → 迁移
- **MD5 相同** → `.bak` 备份的与当前一致 → mod 自动生成(非玩家修改) → 跳过
```

- [ ] **Step 4: Add same section to README.en.md** (English translation, same structure)

- [ ] **Step 5: Run full test suite**

Run: `pytest -q && ruff check .`
Expected: All pass, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add tests/test_e2e.py README.zh-CN.md README.en.md
git commit -m "test(e2e): mod 感知端到端验证 + README 分类系统文档"
```

---

## Self-Review Checklist

After all tasks are complete, verify:

1. **Spec coverage**: Every section in `Reference/specs/2026-07-13-mod-awareness-design.md` has a corresponding task
2. **Placeholder scan**: No TBD/TODO in any task
3. **Type consistency**: `find_bak_siblings` returns `list[str]` in all references; `ModRegistry.__contains__` takes `str` everywhere
4. **Test coverage**: orphan routing, .bak MD5 comparison (equal/different/multiple/no-md5), config→modid mapping (convention/underscore/override/core), version range parsing, jar parsing (single/multi/no-toml/fallback)
5. **Zero regression**: `mcmig scan`/`diff` unaffected (no orphan_rules, no mod scanning)
