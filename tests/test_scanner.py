from pathlib import Path

from migration.scanner import Scanner


def test_scan_collects_all_files(mini_version: Path):
    entries, errors = Scanner(mini_version, "mini", strict=False).scan()
    paths = {e.path for e in entries}
    assert "options.txt" in paths
    assert "logs/latest.log" in paths
    assert "mods/create.jar" in paths
    assert "Distant_Horizons_server_data/lod.sqlite" in paths
    assert errors == []


def test_tiered_hash_text_hashed_jar_not(mini_version: Path):
    entries, _ = Scanner(mini_version, "mini", strict=False).scan()
    by = {e.path: e for e in entries}
    assert by["options.txt"].md5 is not None
    assert by["mods/create.jar"].md5 is None
    assert by["Distant_Horizons_server_data/lod.sqlite"].md5 is None


def test_strict_hashes_everything(mini_version: Path):
    entries, _ = Scanner(mini_version, "mini", strict=True).scan()
    by = {e.path: e for e in entries}
    assert by["mods/create.jar"].md5 is not None
    assert by["Distant_Horizons_server_data/lod.sqlite"].md5 is not None


def test_build_snapshot_fields(mini_version: Path):
    snap, errors = Scanner(mini_version, "mini", strict=False).build_snapshot(
        str(mini_version.parent)
    )
    assert errors == []
    assert snap.version == "mini"
    assert snap.hash_mode == "tiered"
    assert snap.file_count == len(snap.files)
    assert snap.scanned_at != ""


def test_strict_snapshot_mode_label(mini_version: Path):
    snap, _ = Scanner(mini_version, "mini", strict=True).build_snapshot("g")
    assert snap.hash_mode == "strict"


def test_unreadable_file_skipped_with_error(tmp_path: Path, monkeypatch):
    # 构造一个读会失败的文件:patch compute_md5 抛 OSError
    p = tmp_path / "bad.txt"
    p.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "migration.scanner.hashing.compute_md5", lambda _: (_ for _ in ()).throw(OSError("locked"))
    )
    entries, errors = Scanner(tmp_path, "v", strict=False).scan()
    # bad.txt 应被跳过并列入 errors
    assert all(e.path != "bad.txt" for e in entries)
    assert any("bad.txt" in e.reason for e in errors)


# ---- 批次G Task 4:动态世界目录探测(F34①) ----


def test_detect_world_dirs_level_name_and_level_dat() -> None:
    """F34①:level-name 指向 + 顶层含 level.dat 双源探测,并集升序;客户端 saves/ 不误触。"""
    from migration.scanner import detect_world_dirs
    from migration.snapshot import FileEntry

    entries = [
        FileEntry("server.properties", 10, None),
        FileEntry("f1-shanghai/level.dat", 100, None),
        FileEntry("f1-shanghai/region/r.0.0.mca", 5, None),
        FileEntry("world_backup_20261001/level.dat", 100, None),
        FileEntry("saves/myworld/level.dat", 100, None),  # 客户端二层:非顶层,不命中
        FileEntry("config/foo.toml", 1, None),
    ]
    text = b"level-name=f1-shanghai\nmotd=x\n"
    assert detect_world_dirs(entries, text) == ["f1-shanghai", "world_backup_20261001"]
    # properties 缺失 → 仅 level.dat 启发
    assert detect_world_dirs(entries, None) == ["f1-shanghai", "world_backup_20261001"]
    # level-name 指向不存在的目录 → 跳过①
    assert detect_world_dirs(entries, b"level-name=ghost\n") == \
        ["f1-shanghai", "world_backup_20261001"]


def test_detect_world_dirs_rejects_unsafe_level_name() -> None:
    """F34① 安全:level-name 含路径段/点号/空 → 不入探测结果(防 glob 注入)。"""
    from migration.scanner import detect_world_dirs
    from migration.snapshot import FileEntry

    entries = [FileEntry("server.properties", 10, None),
               FileEntry("world/level.dat", 1, None)]
    for bad in (b"level-name=../evil\n", b"level-name=a/b\n", b"level-name=.\n",
                b"level-name=\n"):
        assert detect_world_dirs(entries, bad) == ["world"]


def test_detect_world_dirs_level_name_requires_level_dat() -> None:
    """W2.5 复审 A1:①通道须有 <名>/level.dat 佐证,异常 level-name 不得注入非世界目录。

    level-name=config(管理员误写/模板异常值)时,config/ 下确有文件可满足
    「前缀存在」检查,但无 config/level.dat → 不得把 config 当世界目录
    (否则 config/** 从 UNKNOWN(ASK 人工确认)静默翻 must_migrate 自动拷贝,
    「问用户」变成「替用户决策」,违背保守默认)。
    """
    from migration.scanner import detect_world_dirs
    from migration.snapshot import FileEntry

    entries = [FileEntry("server.properties", 10, None),
               FileEntry("config/foo.toml", 1, None),
               FileEntry("config/sub/x.dat", 2, None),
               FileEntry("world/level.dat", 1, None)]
    # level-name 指向有文件但无 level.dat 的保留目录 → ①不触发,仅②的 world 生效
    assert detect_world_dirs(entries, b"level-name=config\n") == ["world"]
    # 同名目录有 level.dat → ①照常命中(①②互为佐证的双通道)
    assert detect_world_dirs(
        entries + [FileEntry("config/level.dat", 4, None)],
        b"level-name=config\n",
    ) == ["config", "world"]


def test_scanner_records_world_dirs(tmp_path) -> None:
    """F34① 接线:Scanner 活体扫描写入 world_dirs(server.properties 实读)。"""
    from migration.scanner import Scanner

    root = tmp_path / "v"
    (root / "f1-shanghai").mkdir(parents=True)
    (root / "world_backup").mkdir()
    (root / "saves" / "w").mkdir(parents=True)
    (root / "f1-shanghai" / "level.dat").write_bytes(b"x")
    (root / "world_backup" / "level.dat").write_bytes(b"x")
    (root / "saves" / "w" / "level.dat").write_bytes(b"x")
    (root / "server.properties").write_bytes(b"level-name=f1-shanghai\n")
    (root / "config.toml").write_bytes(b"a=1\n")
    snap, errs = Scanner(root, "v").build_snapshot(str(tmp_path))
    assert errs == []
    assert snap.world_dirs == ["f1-shanghai", "world_backup"]


# ---- 批次G Task 6:未哈希文件记 mtime(F35) ----


def test_scanner_records_mtime_only_for_unhashed(tmp_path) -> None:
    """F35:未哈希文件(.mca/mods jar)记 mtime;哈希文件(.txt/.json)mtime=None。"""
    import os

    from migration.scanner import Scanner

    root = tmp_path / "v"
    (root / "world" / "region").mkdir(parents=True)
    (root / "mods").mkdir()
    mca = root / "world" / "region" / "r.0.0.mca"
    mca.write_bytes(b"x" * 16)
    jar = root / "mods" / "demo-1.0.jar"
    jar.write_bytes(b"y" * 16)
    txt = root / "options.txt"
    txt.write_text("lang:zh_cn\n", encoding="utf-8")
    for f in (mca, jar, txt):
        os.utime(f, (1759300000, 1759300000))
    snap, errs = Scanner(root, "v").build_snapshot(str(tmp_path))
    assert errs == []
    by = {e.path: e for e in snap.files}
    assert by["world/region/r.0.0.mca"].mtime == 1759300000
    assert by["mods/demo-1.0.jar"].mtime == 1759300000
    assert by["options.txt"].mtime is None


# ---- 批次H Task 2:快照 v2 接线(build_snapshot 内嵌 mod 名册) ----


def test_build_snapshot_embeds_mods(tmp_path) -> None:
    """scan 真目录:快照内嵌名册,五字段齐(spec §5.2)。"""
    # 注:brief 签名以 write_mod_jar 为参数,但 conftest 中它是普通助手函数而非
    # pytest fixture,按既有风格(test_cli/test_pipeline)函数内导入使用
    from tests.conftest import write_mod_jar

    vd = tmp_path / "versions" / "v"
    (vd / "mods").mkdir(parents=True)
    write_mod_jar(vd / "mods" / "foo-1.0.jar", "foo", "1.0")
    snap, errs = Scanner(vd, "v").build_snapshot(str(tmp_path))
    assert not errs
    assert snap.mods and snap.mods[0]["modid"] == "foo"
    assert snap.mods[0]["jar_filename"] == "foo-1.0.jar"
    assert set(snap.mods[0]) == {"modid", "version", "jar_filename",
                                 "neoforge_range", "embedded_in"}
