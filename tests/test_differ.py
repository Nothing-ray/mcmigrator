from migration import rules
from migration.classifier import Classifier
from migration.differ import Differ
from migration.rules import Category
from migration.snapshot import FileEntry


def _clf():
    default, _ = rules.load_default_rules("mini")
    return Classifier(rules.RuleSet.from_layers(default))


def _e(path, size=1, md5="x"):
    return FileEntry(path=path, size=size, md5=md5)


def test_must_migrate_dst_missing_goes_to_migrate():
    clf = _clf()
    d = Differ([_e("options.txt")], [], clf).diff()
    assert any(i.path == "options.txt" for i in d.to_migrate)


def test_must_migrate_modified_goes_to_migrate():
    clf = _clf()
    d = Differ([_e("options.txt", md5="a")], [_e("options.txt", md5="b")], clf).diff()
    assert any(i.path == "options.txt" and i.note == "modified" for i in d.to_migrate)


def test_must_migrate_identical_goes_identical_verified():
    clf = _clf()
    d = Differ([_e("options.txt", md5="a")], [_e("options.txt", md5="a")], clf).diff()
    assert any(i.path == "options.txt" and i.note == "verified" for i in d.identical)


def test_unknown_modified_goes_candidate():
    clf = _clf()
    d = Differ([_e("config/foo.toml", md5="a")], [_e("config/foo.toml", md5="b")], clf).diff()
    assert any(i.path == "config/foo.toml" for i in d.candidate)


def test_unknown_dst_only_goes_only_in_dst():
    clf = _clf()
    d = Differ([], [_e("config/foo.toml")], clf).diff()
    assert any(i.path == "config/foo.toml" for i in d.only_in_dst)


def test_never_goes_never_bucket():
    clf = _clf()
    d = Differ([_e("logs/latest.log")], [], clf).diff()
    assert any(i.path == "logs/latest.log" for i in d.never)


def test_size_based_identical_when_md5_none():
    clf = _clf()
    # lod.sqlite md5=None,size 相同 → size-based identical
    d = Differ(
        [_e("Distant_Horizons_server_data/lod.sqlite", size=16, md5=None)],
        [_e("Distant_Horizons_server_data/lod.sqlite", size=16, md5=None)],
        clf,
    ).diff()
    assert any(i.note == "size-based" for i in d.identical)


def test_mods_bucket_by_filename_set():
    clf = _clf()
    d = Differ(
        [_e("mods/create.jar"), _e("mods/extra.jar")],
        [_e("mods/create.jar")],
        clf,
    ).diff()
    notes = {i.path: i.note for i in d.mods}
    assert notes.get("mods/create.jar") == "shared"
    assert notes.get("mods/extra.jar") == "to_add"


def test_mods_target_only():
    clf = _clf()
    d = Differ([], [_e("mods/target_only.jar")], clf).diff()
    assert any(i.note == "target_only" for i in d.mods)


def test_empty_dst_all_must_to_migrate():
    clf = _clf()
    d = Differ([_e("options.txt"), _e("servers.dat")], [], clf).diff()
    paths = {i.path for i in d.to_migrate}
    assert {"options.txt", "servers.dat"} <= paths


def test_src_and_dst_version_binaries_classified_never():
    # diff 上下文:规则集同时识别 src 与 dst 的版本二进制 → 都判 never(非 unknown)
    default, _ = rules.load_default_rules(["1.21.1-NeoForge_21.1.227", "1.21.1-NeoForge_21.1.228"])
    clf = Classifier(rules.RuleSet.from_layers(default))
    # 源版本二进制
    assert clf.classify_path("1.21.1-NeoForge_21.1.227.jar") == Category.NEVER
    assert clf.classify_path("1.21.1-NeoForge_21.1.227.json") == Category.NEVER
    assert clf.classify_path("1.21.1-NeoForge_21.1.227-natives/lwjgl.dll") == Category.NEVER
    # 目标版本二进制
    assert clf.classify_path("1.21.1-NeoForge_21.1.228.jar") == Category.NEVER
