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
