import json
from pathlib import Path

import pytest

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
