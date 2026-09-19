from migration import rules
from migration.classifier import Classifier
from migration.differ import Differ
from migration.rules import Category, Rule
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


def test_rebuild_classified_goes_never_bucket_with_rebuild_note():
    from migration.rules import Category, Rule, RuleSet

    rs = RuleSet(rules=[Rule(match="config/fml.toml", decide=Category.REBUILD)])
    clf = Classifier(rs)
    d = Differ([_e("config/fml.toml", md5="a")], [_e("config/fml.toml", md5="b")], clf).diff()
    # 进 never 桶,note="rebuild"(与普通 never 区分,供 planner 定 origin)
    matches = [i for i in d.never if i.path == "config/fml.toml"]
    assert len(matches) == 1
    assert matches[0].note == "rebuild"
    # 不应进 candidate
    assert not any(i.path == "config/fml.toml" for i in d.candidate)


def test_orphan_classified_goes_never_bucket_with_orphan_note():
    from migration.rules import Category, Rule, RuleSet

    rs = RuleSet(rules=[Rule(match="config/jade/foo.json", decide=Category.ORPHAN)])
    clf = Classifier(rs)
    d = Differ([_e("config/jade/foo.json", md5="a")], [], clf).diff()
    matches = [i for i in d.never if i.path == "config/jade/foo.json"]
    assert len(matches) == 1
    assert matches[0].note == "orphan"
    assert not any(i.path == "config/jade/foo.json" for i in d.candidate)


# --- modpack-swap 模式 ---


def test_modpack_swap_src_only_mod_goes_never_bucket():
    """换包模式:src 独有 mod → never 桶(note=modpack_swap),不进 mods 桶回迁。"""
    rs = rules.RuleSet(rules=[])
    clf = Classifier(rs)
    d = Differ(
        [_e("mods/old-pack-mod.jar", 10)],
        [_e("mods/shared.jar", 10)],
        clf,
        modpack_swap=True,
    ).diff()
    assert all(i.path != "mods/old-pack-mod.jar" for i in d.mods)
    m = next(i for i in d.never if i.path == "mods/old-pack-mod.jar")
    assert m.note == "modpack_swap"


def test_modpack_swap_shared_mod_unaffected():
    """换包模式:两边共有的 mod 不受影响(仍 mods 桶 shared)。"""
    clf = Classifier(rules.RuleSet(rules=[]))
    d = Differ(
        [_e("mods/shared.jar", 10)], [_e("mods/shared.jar", 10)], clf, modpack_swap=True
    ).diff()
    assert any(i.path == "mods/shared.jar" and i.note == "shared" for i in d.mods)


def test_modpack_swap_off_keeps_old_behavior():
    """未开换包模式:src 独有 mod 仍进 mods 桶 to_add(默认回迁)。"""
    clf = Classifier(rules.RuleSet(rules=[]))
    d = Differ(
        [_e("mods/extra.jar", 10)], [], clf
    ).diff()
    assert any(i.path == "mods/extra.jar" and i.note == "to_add" for i in d.mods)


def test_modpack_swap_user_rule_rescues_specific_jar():
    """换包模式:用户显式 must_migrate 规则命中的 jar 仍进 to_migrate(用户主权)。"""
    from migration.rules import Rule, RuleSet

    rs = RuleSet(rules=[Rule(match="mods/my-keep.jar", decide=Category.MUST_MIGRATE)])
    clf = Classifier(rs)
    d = Differ(
        [_e("mods/my-keep.jar", 10), _e("mods/old-pack.jar", 10)],
        [],
        clf,
        modpack_swap=True,
    ).diff()
    assert any(i.path == "mods/my-keep.jar" and i.note == "to_add" for i in d.mods)
    assert any(i.path == "mods/old-pack.jar" and i.note == "modpack_swap" for i in d.never)


# F12:Differ properties 语义复核(注入 content_reader)
# (导入复用文件顶部:rules / Classifier / Differ / Category / Rule / FileEntry)


def _clf():
    return Classifier(rules.RuleSet.from_layers(*rules.load_default_rules("mini")))


def _must_migrate_clf():
    return Classifier(rules.RuleSet(rules=[Rule(match="server.properties", decide=Category.MUST_MIGRATE)]))


def _reader(pairs: dict[tuple[str, str], bytes]):
    """构造假 content_reader;未登记的读取返回 None。"""
    return lambda rel, side: pairs.get((rel, side))


A = b"view-distance=8\n"          # 未转义
B = b"view-distance=8\n#Sat Sep 12\n"  # 多了时间戳注释,语义等价
C = b"view-distance=10\n"          # 真实差异


def test_semantics_equal_properties_lands_identical():
    d = Differ([FileEntry("config/x.properties", 1, "a"), ], [FileEntry("config/x.properties", 2, "b")],
               _clf(), content_reader=_reader({("config/x.properties", "src"): A,
                                               ("config/x.properties", "dst"): B})).diff()
    assert [i.note for i in d.identical] == ["semantics"]


def test_must_migrate_semantics_equal_skips_migration():
    # 语义等价的 server.properties: identical(SKIP) 而非 to_migrate(COPY)
    d = Differ([FileEntry("server.properties", 1, "a")], [FileEntry("server.properties", 2, "b")],
               _must_migrate_clf(), content_reader=_reader({("server.properties", "src"): A,
                                                            ("server.properties", "dst"): B})).diff()
    assert [i.note for i in d.identical] == ["semantics"]
    assert d.to_migrate == []


def test_real_difference_still_modified():
    d = Differ([FileEntry("config/x.properties", 1, "a")], [FileEntry("config/x.properties", 2, "b")],
               _clf(), content_reader=_reader({("config/x.properties", "src"): A,
                                               ("config/x.properties", "dst"): C})).diff()
    assert [i.note for i in d.candidate] == ["modified"]


def test_reader_none_falls_back_to_byte_compare():
    d = Differ([FileEntry("config/x.properties", 1, "a")], [FileEntry("config/x.properties", 2, "b")],
               _clf(), content_reader=_reader({})).diff()
    assert [i.note for i in d.candidate] == ["modified"]


def test_undispatched_suffix_never_triggers_reader():
    # F16 起语义复核 dispatch 扩为 .properties/.json/.toml;以表外后缀 .txt 验证表外不触发 reader
    d = Differ([FileEntry("config/x.txt", 1, "a")], [FileEntry("config/x.txt", 2, "b")],
               _clf(), content_reader=_reader({("config/x.txt", "src"): A,
                                               ("config/x.txt", "dst"): B})).diff()
    assert [i.note for i in d.candidate] == ["modified"]


def test_md5_equal_ignores_reader():
    d = Differ([FileEntry("config/x.properties", 1, "same")], [FileEntry("config/x.properties", 1, "same")],
               _clf(), content_reader=_reader({("config/x.properties", "src"): A,
                                               ("config/x.properties", "dst"): C})).diff()
    assert [i.note for i in d.identical] == ["verified"]


# F16:json/toml 语义复核 dispatch(.properties 扩为三格式)
# (reader 以局部 def 构造,避免 lambda 赋值违反 ruff E731;逻辑与简报 lambda 逐字等价)


def test_json_semantic_rewrite_lands_identical_semantics():
    """F16: mod 启动重写 json(键序/空白差异)→ identical/semantics,不误报 candidate。"""
    clf = _clf()

    def reader(p, side):
        return (b'{"logInterval": 60, "debug": false}' if side == "src"
                else b'{\n  "debug": false,\n  "logInterval": 60\n}')

    d = Differ([_e("config/atleaks.json", 398, "aa")],
               [_e("config/atleaks.json", 401, "bb")], clf,
               content_reader=reader).diff()
    assert any(i.path == "config/atleaks.json" and i.note == "semantics" for i in d.identical)
    assert not any(i.path == "config/atleaks.json" for i in d.candidate)


def test_toml_semantic_rewrite_lands_identical_semantics():
    clf = _clf()

    def reader(p, side):
        return (b"[server]\nport = 1\n[client]\nfov = 90\n" if side == "src"
                else b"[client]\nfov = 90\n[server]\nport = 1\n")

    d = Differ([_e("config/xx.toml", 10, "aa")],
               [_e("config/xx.toml", 12, "bb")], clf,
               content_reader=reader).diff()
    assert any(i.path == "config/xx.toml" and i.note == "semantics" for i in d.identical)


def test_json_real_value_diff_stays_modified():
    clf = _clf()

    def reader(p, side):
        return b'{"logInterval": 10}' if side == "src" else b'{"logInterval": 60}'

    d = Differ([_e("config/atleaks.json", 398, "aa")],
               [_e("config/atleaks.json", 401, "bb")], clf,
               content_reader=reader).diff()
    assert any(i.path == "config/atleaks.json" and i.note == "modified" for i in d.candidate)


def test_semantic_check_not_fired_without_reader():
    """reader 缺失(复放/plan 管线)→ 字节比较不变(plan 管线不传 reader 的既定语义)。"""
    clf = _clf()
    d = Differ([_e("config/atleaks.json", 398, "aa")],
               [_e("config/atleaks.json", 401, "bb")], clf).diff()
    assert any(i.path == "config/atleaks.json" and i.note == "modified" for i in d.candidate)


# F17:rebuilt 检出(mods 桶同名 jar 比 size / 双侧可得时比 md5)


def test_mod_same_name_diff_size_is_rebuilt():
    """F17: 同名同版本 jar 重新打包(±size)→ note=rebuilt,不再漏检为 shared。"""
    clf = _clf()
    d = Differ([_e("mods/x-1.0.jar", 7233263, None)],
               [_e("mods/x-1.0.jar", 7233336, None)], clf).diff()
    assert d.mods[0].note == "rebuilt"


def test_mod_same_name_same_size_md5_diff_is_rebuilt():
    clf = _clf()
    d = Differ([_e("mods/x-1.0.jar", 100, "aa")],
               [_e("mods/x-1.0.jar", 100, "bb")], clf).diff()
    assert d.mods[0].note == "rebuilt"


def test_mod_same_name_same_size_null_md5_stays_shared():
    """tiered 快照 md5=null 且 size 相同 → shared(同尺寸异构建为记录在案的盲区)。"""
    clf = _clf()
    d = Differ([_e("mods/x-1.0.jar", 100, None)],
               [_e("mods/x-1.0.jar", 100, None)], clf).diff()
    assert d.mods[0].note == "shared"
