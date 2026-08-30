"""moddb 模块测试:jar 解析 + config→modid 映射 + orphan 规则 + 版本兼容。"""

from pathlib import Path

from migration.moddb import (
    CompatWarning,
    ModInfo,
    ModRegistry,
    OverrideTable,
    check_mod_compat,
    check_version_range,
    extract_modid_candidate,
    generate_orphan_rules,
    load_mod_config_map,
    map_config_to_mod,
    read_neoforge_version,
    scan_mods,
)
from migration.rules import Category
from migration.snapshot import FileEntry


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


def test_scan_mods_single_jar(tmp_path):
    tmp = tmp_path
    make_fake_jar(tmp / "mods", "create-1.21.1-6.0.10.jar", "create", "6.0.10", "[21.1.219,)")
    reg = scan_mods(tmp)
    assert "create" in reg
    info = reg.get("create")
    assert info is not None
    assert info.version == "6.0.10"
    assert info.jar_filename == "create-1.21.1-6.0.10.jar"
    assert info.neoforge_range == "[21.1.219,)"


def test_scan_mods_multiple_jars(tmp_path):
    tmp = tmp_path
    make_fake_jar(tmp / "mods", "create.jar", "create")
    make_fake_jar(tmp / "mods", "waystones.jar", "waystones", "21.1.36")
    reg = scan_mods(tmp)
    assert "create" in reg
    assert "waystones" in reg
    assert len(reg.modids) == 2


def test_scan_mods_case_insensitive_lookup(tmp_path):
    tmp = tmp_path
    make_fake_jar(tmp / "mods", "x.jar", "CSC")
    reg = scan_mods(tmp)
    assert "csc" in reg  # 小写查询
    assert "CSC" in reg  # 原始查询
    assert reg.get("csc").modid == "CSC"


def test_scan_mods_jar_without_toml_skipped(tmp_path):
    import zipfile

    tmp = tmp_path
    mods = tmp / "mods"
    mods.mkdir(parents=True)
    # 无 mods.toml 的 jar
    with zipfile.ZipFile(mods / "empty.jar", "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
    reg = scan_mods(tmp)
    assert len(reg.modids) == 0


def test_scan_mods_multiple_mods_in_one_jar(tmp_path):
    mods = tmp_path / "mods"
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
    reg = scan_mods(tmp_path)
    assert "lib" in reg
    assert "addon" in reg


def test_scan_mods_no_neoforge_dependency(tmp_path):
    tmp = tmp_path
    # nf_range=None → mods.toml 无 dependencies 段
    make_fake_jar(tmp / "mods", "simple.jar", "simple", "1.0", nf_range=None)
    reg = scan_mods(tmp)
    info = reg.get("simple")
    assert info.neoforge_range is None


def test_scan_mods_fallback_to_mods_toml(tmp_path):
    """旧 Forge 格式: META-INF/mods.toml (无 neoforge 前缀)。"""
    import zipfile

    tmp = tmp_path
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


def test_scan_mods_empty_mods_dir(tmp_path):
    tmp = tmp_path
    (tmp / "mods").mkdir(parents=True)
    reg = scan_mods(tmp)
    assert len(reg.modids) == 0


def test_scan_mods_no_mods_dir(tmp_path):
    tmp = tmp_path
    reg = scan_mods(tmp)
    assert len(reg.modids) == 0


def test_scan_mods_zlib_error_skipped(tmp_path, monkeypatch):
    """jar entry 的 deflate 流损坏(zlib.error)→ 跳过不崩溃(spec §5.3)。"""
    import zipfile
    import zlib

    from migration import moddb

    mods = tmp_path / "mods"
    mods.mkdir(parents=True)
    # 先建一个合法 jar
    with zipfile.ZipFile(mods / "x.jar", "w") as z:
        z.writestr(
            "META-INF/neoforge.mods.toml",
            'modLoader="javafml"\n[[mods]]\nmodId="x"\nversion="1"\n',
        )

    # 模拟 z.read 抛 zlib.error(独立异常树,不被 OSError 捕获)
    real_zipfile = zipfile.ZipFile

    class FakeZipFile(real_zipfile):
        def read(self, name, pwd=None):  # noqa: ANN001, ARG002
            raise zlib.error("simulated deflate corruption")

    monkeypatch.setattr(moddb.zipfile, "ZipFile", FakeZipFile)
    # 不应抛出异常,返回空注册表
    reg = scan_mods(tmp_path)
    assert len(reg.modids) == 0


def test_mod_registry_contains_case_insensitive():
    reg = ModRegistry()
    reg.add(ModInfo(modid="Create", version="1.0", jar_filename="x.jar", neoforge_range=None))
    assert "create" in reg
    assert "CREATE" in reg
    assert "Create" in reg
    assert "other" not in reg


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


def test_load_mod_config_map_has_dragon_survival():
    """目录名 dragon-survival ≠ modid dragonsurvival(无分隔符),靠覆盖表修正。

    子目录 config/dragon-survival/** 的目录名含连字符,extract_modid_candidate
    正则 ^[a-z][a-z0-9_]*$ 拒绝 'dragon-survival' → 返回 None(无法确定)。
    没有覆盖表则 orphan 规则不会生成,这些 config 会落到默认行为(可能误迁或
    漏判)。覆盖表显式映射到真实 modid dragonsurvival,orphan 判定才能正确。
    """
    table = load_mod_config_map()
    assert table.lookup("config/dragon-survival/preset.nbt") == "dragonsurvival"
    assert table.lookup("config/dragon-survival/sub/foo.json") == "dragonsurvival"


def test_extract_candidate_hyphen_subdir_returns_none():
    """已知限制:含 `-` 的子目录名 → 正则拒绝 → None(无法确定)。

    spec §2.1 规则 6 合法性检查 `^[a-z][a-z0-9_]*$` 拒绝含连字符的候选。
    此场景靠 mod_config_map.yaml 覆盖表兜底(见 test_load_mod_config_map_has_dragon_survival)。
    """
    assert extract_modid_candidate("config/dragon-survival/foo.nbt") is None


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


def test_read_neoforge_version_arguments_is_string(tmp_path):
    """arguments 是字符串而非 dict → 返回 None(不崩溃,spec §5.4)。"""
    import json

    ver_dir = tmp_path / "broken"
    ver_dir.mkdir()
    (ver_dir / "broken.json").write_text(
        json.dumps({"arguments": "oops"}), encoding="utf-8"
    )
    assert read_neoforge_version(ver_dir) is None


def test_read_neoforge_version_top_level_is_list(tmp_path):
    """JSON 顶层非 dict(如数组)→ 返回 None(不崩溃)。"""
    import json

    ver_dir = tmp_path / "array"
    ver_dir.mkdir()
    (ver_dir / "array.json").write_text(
        json.dumps([1, 2, 3]), encoding="utf-8"
    )
    assert read_neoforge_version(ver_dir) is None


def test_read_neoforge_version_game_is_not_list(tmp_path):
    """arguments.game 是字符串而非 list → 返回 None(不崩溃)。"""
    import json

    ver_dir = tmp_path / "gamestr"
    ver_dir.mkdir()
    (ver_dir / "gamestr.json").write_text(
        json.dumps({"arguments": {"game": "--fml.neoforgeVersion"}}),
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
    assert isinstance(warnings[0], CompatWarning)
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


# --- jar-in-jar 内嵌解析 ---


def make_jarjar_outer(mods_dir: Path, outer_name: str, inner_name: str, inner_modid: str) -> Path:
    """创建含 META-INF/jarjar/<inner> 内嵌 jar 的合成外层 jar(外层自身无 mods.toml)。"""
    import io
    import zipfile

    mods_dir.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as inner:
        toml = f'modLoader="javafml"\nloaderVersion="[1,)"\n[[mods]]\nmodId="{inner_modid}"\nversion="1.0"\n'
        inner.writestr("META-INF/neoforge.mods.toml", toml)
    jar_path = mods_dir / outer_name
    with zipfile.ZipFile(jar_path, "w") as z:
        z.writestr(f"META-INF/jarjar/{inner_name}", buf.getvalue())
    return jar_path


def test_scan_mods_embedded_jar_registered(tmp_path):
    """内嵌 jar(jar-in-jar)的 modid 应进注册表,embedded_in 记宿主 jar 名。"""
    make_jarjar_outer(tmp_path / "mods", "create-6.0.10.jar", "flywheel-1.0.6.jar", "flywheel")
    reg = scan_mods(tmp_path)
    assert "flywheel" in reg
    info = reg.get("flywheel")
    assert info is not None
    assert info.embedded_in == "create-6.0.10.jar"


def test_scan_mods_top_level_wins_over_embedded_same_modid(tmp_path):
    """顶层 jar 与内嵌 jar 同 modid → 顶层信息优先(版本/来源)。"""
    import io
    import zipfile

    mods = tmp_path / "mods"
    mods.mkdir(parents=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as inner:
        inner.writestr(
            "META-INF/neoforge.mods.toml",
            'modLoader="javafml"\nloaderVersion="[1,)"\n[[mods]]\nmodId="flywheel"\nversion="1.0"\n',
        )
    with zipfile.ZipFile(mods / "host.jar", "w") as z:
        z.writestr(
            "META-INF/neoforge.mods.toml",
            'modLoader="javafml"\nloaderVersion="[1,)"\n[[mods]]\nmodId="flywheel"\nversion="2.0"\n',
        )
        z.writestr("META-INF/jarjar/flywheel-1.0.jar", buf.getvalue())
    reg = scan_mods(tmp_path)
    info = reg.get("flywheel")
    assert info is not None
    assert info.version == "2.0"
    assert info.embedded_in is None


def test_scan_mods_corrupted_embedded_jar_skipped(tmp_path):
    """内嵌 jar 内容损坏(非 zip)→ 跳过不崩,宿主 mod 正常注册。"""
    import zipfile

    mods = tmp_path / "mods"
    mods.mkdir(parents=True)
    with zipfile.ZipFile(mods / "host.jar", "w") as z:
        z.writestr(
            "META-INF/neoforge.mods.toml",
            'modLoader="javafml"\nloaderVersion="[1,)"\n[[mods]]\nmodId="hostmod"\nversion="1.0"\n',
        )
        z.writestr("META-INF/jarjar/broken-1.0.jar", b"this is not a zip file")
    reg = scan_mods(tmp_path)
    assert "hostmod" in reg
    assert "brokemod" not in reg


def test_load_mod_config_map_b_class_mappings():
    """modswap-830 观察实测的 B 类映射(文件名≠modid)应在覆盖表中。"""
    table = load_mod_config_map()
    assert table.lookup("config/fetzis_displays/fetzis-displays-config.json") == "fetzisdisplays"
    assert table.lookup("config/gun_scaling/main.toml") == "scguns"
    assert table.lookup("config/resourceful-config-web.json") == "resourcefulconfig"
    assert table.lookup("config/l2configs/l2core-client.toml") == "l2core"
