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
    # F35:asdict 序列化补出 mtime 键(None;旧快照无键加载同样得 None)
    assert doc["files"][1] == {"path": "mods/x.jar", "size": 999, "md5": None, "mtime": None}


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


# ---- 批次G Task 4:世界目录探测字段 world_dirs(F34①) ----


def test_snapshot_world_dirs_roundtrip(tmp_path) -> None:
    """F34①:world_dirs 随快照持久化往返。"""
    from migration.snapshot import FileEntry, Snapshot

    snap = Snapshot(version="v", game_root="g", scanned_at="t", hash_mode="tiered",
                    file_count=1, files=[FileEntry("a", 1, None)],
                    world_dirs=["f1-shanghai", "world"])
    p = tmp_path / "s.json"
    snap.save(p)
    assert Snapshot.load(p).world_dirs == ["f1-shanghai", "world"]


def test_snapshot_legacy_world_dirs_absent_and_garbage(tmp_path) -> None:
    """F34① 兼容:旧快照无键 → [];非 list / 元素非 str → [](容错降级不抛)。"""
    import json

    from migration.snapshot import FileEntry, Snapshot

    snap = Snapshot(version="v", game_root="g", scanned_at="t", hash_mode="tiered",
                    file_count=1, files=[FileEntry("a", 1, None)])
    p = tmp_path / "s.json"
    snap.save(p)
    assert Snapshot.load(p).world_dirs == []
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["world_dirs"] = ["ok", 3, None]
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert Snapshot.load(p).world_dirs == ["ok"]
    raw["world_dirs"] = "oops"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert Snapshot.load(p).world_dirs == []


# ---- 批次G Task 6:未哈希文件 mtime 字段(F35) ----


def test_file_entry_mtime_optional_and_load_guard(tmp_path) -> None:
    """F35:FileEntry.mtime 可选往返;旧条目无键 → None;非 int → None。"""
    from migration.snapshot import FileEntry, Snapshot

    snap = Snapshot(version="v", game_root="g", scanned_at="t", hash_mode="tiered",
                    file_count=2,
                    files=[FileEntry("a.mca", 5, None, mtime=1759343400),
                           FileEntry("b.txt", 5, "x" * 32)])
    p = tmp_path / "s.json"
    snap.save(p)
    loaded = Snapshot.load(p)
    assert [f.mtime for f in loaded.files] == [1759343400, None]


# ---- 批次H Task 2:快照 schema v2 内嵌 mod 名册 ----


def test_save_load_v2_roundtrip_mods(tmp_path) -> None:
    """v2 快照 save/load 往返:mods 五字段保真;SNAPSHOT_FORMAT==2(spec §5.1)。"""
    from migration.snapshot import SNAPSHOT_FORMAT, Snapshot, FileEntry

    snap = Snapshot(version="v", game_root="g", scanned_at="t", hash_mode="tiered",
                    file_count=1, files=[FileEntry("a", 1, "x" * 32)],
                    mods=[{"modid": "foo", "version": "1.0", "jar_filename": "foo.jar",
                           "neoforge_range": None, "embedded_in": None}])
    snap.save(tmp_path / "s.json")
    assert json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))["snapshot_format"] == 2
    loaded = Snapshot.load(tmp_path / "s.json")
    assert loaded.mods == snap.mods and SNAPSHOT_FORMAT == 2


def test_load_v1_snapshot_mods_empty(tmp_path) -> None:
    """v1 快照(无 mods 键)load 正常且 mods==[](spec §5.1)。"""
    payload = json.loads((Path(__file__).parent / "fixtures" / "server_corpus" /
                          "synth_v2" / "mx_src.snapshot.json").read_text(encoding="utf-8"))
    (tmp_path / "v1.json").write_text(json.dumps(payload), encoding="utf-8")
    assert Snapshot.load(tmp_path / "v1.json").mods == []


def test_load_mods_structural_damage_degrades(tmp_path) -> None:
    """mods 非 list → 降级 [] 不抛;元素非 dict/缺 modid → 跳过该条(spec §3.1 容错)。"""
    from migration.snapshot import FileEntry, Snapshot

    snap = Snapshot(version="v", game_root="g", scanned_at="t", hash_mode="tiered",
                    file_count=1, files=[FileEntry("a", 1, None)])
    p = tmp_path / "s.json"
    snap.save(p)
    full = {"modid": "ok", "version": "1.0", "jar_filename": "ok.jar",
            "neoforge_range": None, "embedded_in": None}
    raw = json.loads(p.read_text(encoding="utf-8"))
    # mods 整体非 list → 结构级降级 []
    raw["mods"] = "garbage"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert Snapshot.load(p).mods == []
    # 元素级:缺 jar_filename / 非 dict / 缺 modid 均跳过,仅完整条存活
    raw["mods"] = [{"modid": "a"}, "notadict", {"junk": 1}, full]
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert Snapshot.load(p).mods == [full]


def test_load_format3_rejected(tmp_path) -> None:
    """fmt=3 拒绝,文案含「请重新 scan」(spec §3.1)。"""
    payload = {"snapshot_format": 3, "version": "v", "game_root": "g", "scanned_at": "t",
               "hash_mode": "tiered", "file_count": 0, "files": []}
    p = tmp_path / "v3.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SnapshotFormatError) as ei:
        Snapshot.load(p)
    assert "请重新 scan" in str(ei.value)
