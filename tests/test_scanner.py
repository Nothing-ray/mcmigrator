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
