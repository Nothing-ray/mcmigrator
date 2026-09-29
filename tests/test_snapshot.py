import json
from pathlib import Path

import pytest

from migration.scanner import Scanner
from migration.snapshot import FileEntry, Snapshot, SnapshotFormatError


def _sample() -> Snapshot:
    return Snapshot(
        version="v1",
        game_root="C:/game",
        scanned_at="2026-07-02T12:00:00+08:00",
        hash_mode="tiered",
        file_count=2,
        files=[
            FileEntry(path="options.txt", size=10, md5="abcd"),
            FileEntry(path="mods/x.jar", size=999, md5=None),
        ],
    )


def test_save_load_roundtrip(tmp_path: Path):
    sp = tmp_path / "v1.snapshot.json"
    _sample().save(sp)
    loaded = Snapshot.load(sp)
    assert loaded.version == "v1"
    assert loaded.hash_mode == "tiered"
    assert loaded.files == [
        FileEntry(path="options.txt", size=10, md5="abcd"),
        FileEntry(path="mods/x.jar", size=999, md5=None),
    ]


def test_save_creates_parent_dirs(tmp_path: Path):
    sp = tmp_path / ".mcmig" / "snapshots" / "v1.snapshot.json"
    _sample().save(sp)
    assert sp.exists()


def test_md5_none_roundtrip_preserved(tmp_path: Path):
    sp = tmp_path / "s.json"
    _sample().save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    assert doc["files"][1] == {"path": "mods/x.jar", "size": 999, "md5": None}


def test_load_rejects_unsupported_format(tmp_path: Path):
    sp = tmp_path / "bad.json"
    sp.write_text(
        json.dumps(
            {
                "snapshot_format": 999,
                "version": "v",
                "game_root": "",
                "scanned_at": "",
                "hash_mode": "tiered",
                "file_count": 0,
                "files": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SnapshotFormatError):
        Snapshot.load(sp)


def test_snapshot_path_helper():
    from migration.snapshot import snapshot_path

    p = snapshot_path(Path("C:/work"), "v1")
    assert p == Path("C:/work/.mcmig/snapshots/v1.snapshot.json")


def test_load_rejects_file_entry_missing_path(tmp_path: Path):
    """快照某条文件记录缺必需字段(path/size)→ SnapshotFormatError(非裸 KeyError)。"""
    sp = tmp_path / "bad_entry.json"
    sp.write_text(
        json.dumps(
            {
                "snapshot_format": 1,
                "version": "v",
                "game_root": "",
                "scanned_at": "",
                "hash_mode": "tiered",
                "file_count": 1,
                "files": [{"size": 10, "md5": "x"}],  # 缺 path
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SnapshotFormatError):
        Snapshot.load(sp)


# ---- 批次F Task 3:快照身份字段 resolved_root ----


def test_snapshot_resolved_root_roundtrip_and_legacy(tmp_path: Path) -> None:
    """resolved_root 往返持久化;旧快照(无该键)加载得 None 不炸。"""
    snap = Snapshot(version="v", game_root="C:\\g", scanned_at="2026-09-29T10:00:00+08:00",
                    hash_mode="tiered", file_count=0, files=[], resolved_root="C:\\real\\v")
    p = tmp_path / "s.json"
    snap.save(p)
    loaded = Snapshot.load(p)
    assert loaded.resolved_root == "C:\\real\\v"
    # 旧布局:手写无 resolved_root 键的 JSON
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({
        "tool_version": "0.6.0", "snapshot_format": 1, "version": "v",
        "game_root": "C:\\g", "scanned_at": "t", "hash_mode": "tiered",
        "file_count": 0, "files": []}, ensure_ascii=False), encoding="utf-8")
    assert Snapshot.load(legacy).resolved_root is None


def test_scanner_records_resolved_root(tmp_path: Path) -> None:
    """scan 落盘 resolved_root=版本目录 resolve()(junction 解析后的物理路径)。"""
    ver = tmp_path / "versions" / "v1"
    (ver / "mods").mkdir(parents=True)
    snap, _ = Scanner(ver, "v1").build_snapshot(str(tmp_path))
    assert snap.resolved_root == str(ver.resolve())
