from migration.differ import DiffItem, DiffReport
from migration.plan import Behavior, Origin
from migration.planner import Planner
from migration.snapshot import FileEntry


def _e(path, size=1, md5="x"):
    return FileEntry(path=path, size=size, md5=md5)


def _plan(report: DiffReport, src_entries: list[FileEntry] | None = None) -> list:
    src_index = {e.path: e for e in (src_entries or [])}
    return Planner(report, src_index).plan().actions


def test_to_migrate_new_goes_copy_must_migrate():
    report = DiffReport(to_migrate=[DiffItem("options.txt", _e("options.txt"), None, "new")])
    actions = _plan(report, [_e("options.txt")])
    a = next(a for a in actions if a.path == "options.txt")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.MUST_MIGRATE
    assert a.backup_target is None
    assert a.confidence == "high"


def test_to_migrate_modified_goes_copy_with_backup():
    report = DiffReport(
        to_migrate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="a"),
                             _e("config/foo.toml", md5="b"), "modified")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.MUST_MIGRATE
    assert a.backup_target == "_conflict_backup/config/foo.toml"
    assert a.md5_match is False


def test_identical_verified_goes_skip_identical_high():
    report = DiffReport(
        identical=[DiffItem("options.txt", _e("options.txt", md5="a"),
                            _e("options.txt", md5="a"), "verified")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "options.txt")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.IDENTICAL
    assert a.md5_match is True
    assert a.confidence == "high"


def test_identical_size_based_goes_skip_identical_medium():
    report = DiffReport(
        identical=[DiffItem("dh/lod.sqlite", _e("dh/lod.sqlite", size=16, md5=None),
                            _e("dh/lod.sqlite", size=16, md5=None), "size-based")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "dh/lod.sqlite")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.IDENTICAL
    assert a.md5_match is None
    assert a.confidence == "medium"


def test_never_note_never_goes_skip_never():
    report = DiffReport(never=[DiffItem("logs/latest.log", _e("logs/latest.log"), None, "never")])
    actions = _plan(report)
    a = next(a for a in actions if a.path == "logs/latest.log")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.NEVER


def test_never_note_rebuild_goes_skip_rebuild():
    report = DiffReport(never=[DiffItem("config/fml.toml", _e("config/fml.toml"),
                                        None, "rebuild")])
    actions = _plan(report)
    a = next(a for a in actions if a.path == "config/fml.toml")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.REBUILD


def test_mods_to_add_goes_copy_mod_added():
    report = DiffReport(mods=[DiffItem("mods/extra.jar", _e("mods/extra.jar"), None, "to_add")])
    actions = _plan(report)
    a = next(a for a in actions if a.path == "mods/extra.jar")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.MOD_ADDED


def test_mods_shared_goes_skip_mod_shared():
    report = DiffReport(
        mods=[DiffItem("mods/create.jar", _e("mods/create.jar"), _e("mods/create.jar"), "shared")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "mods/create.jar")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.MOD_SHARED


def test_mods_target_only_goes_skip_mod_target_only():
    report = DiffReport(
        mods=[DiffItem("mods/x.jar", None, _e("mods/x.jar"), "target_only")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "mods/x.jar")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.MOD_TARGET_ONLY


def test_only_in_dst_not_in_actions():
    report = DiffReport(
        only_in_dst=[DiffItem("config/target_only.toml", None, _e("config/target_only.toml"), "target_only")]
    )
    actions = _plan(report)
    assert all(a.path != "config/target_only.toml" for a in actions)


def test_candidate_non_config_goes_ask_needs_review():
    report = DiffReport(
        candidate=[DiffItem("kubejs/my.js", _e("kubejs/my.js"), None, "new")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "kubejs/my.js")
    assert a.behavior == Behavior.ASK
    assert a.origin == Origin.NEEDS_REVIEW
    assert a.confidence == "low"


def test_config_candidate_with_plain_bak_goes_copy_config_modified():
    report = DiffReport(
        candidate=[DiffItem("config/create.toml", _e("config/create.toml", md5="a"), None, "new")]
    )
    src = [_e("config/create.toml"), _e("config/create.toml.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/create.toml")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.CONFIG_MODIFIED
    assert a.reason == ".bak sibling exists"
    assert a.confidence == "high"


def test_config_candidate_with_versioned_bak_goes_copy_with_backup():
    report = DiffReport(
        candidate=[DiffItem("config/create.toml", _e("config/create.toml", md5="a"),
                            _e("config/create.toml", md5="b"), "modified")]
    )
    src = [_e("config/create.toml"), _e("config/create-1.toml.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/create.toml")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.CONFIG_MODIFIED
    assert a.backup_target == "_conflict_backup/config/create.toml"


def test_config_candidate_no_bak_goes_skip_default_config():
    report = DiffReport(
        candidate=[DiffItem("config/default.toml", _e("config/default.toml", md5="a"),
                            _e("config/default.toml", md5="b"), "modified")]
    )
    src = [_e("config/default.toml")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/default.toml")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.DEFAULT_CONFIG
    assert a.confidence == "high"
    assert "no .bak" in a.reason


def test_bak_judgment_only_applies_to_config_prefix():
    report = DiffReport(
        candidate=[DiffItem("kubejs/my.js", _e("kubejs/my.js"), None, "new")]
    )
    src = [_e("kubejs/my.js"), _e("kubejs/my.js.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "kubejs/my.js")
    assert a.behavior == Behavior.ASK
    assert a.origin == Origin.NEEDS_REVIEW
    assert a.confidence == "low"


def test_bak_in_dst_only_does_not_count():
    report = DiffReport(
        candidate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="a"),
                            _e("config/foo.toml", md5="b"), "modified")]
    )
    src = [_e("config/foo.toml")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.behavior == Behavior.SKIP
    assert a.origin == Origin.DEFAULT_CONFIG


def test_multiple_bak_versions_also_match():
    report = DiffReport(
        candidate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="a"), None, "new")]
    )
    src = [_e("config/foo.toml"), _e("config/foo-1.toml.bak"), _e("config/foo-2.toml.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.behavior == Behavior.COPY
    assert a.origin == Origin.CONFIG_MODIFIED


def test_bak_does_not_false_match_stem_with_hyphens():
    from migration.planner import has_bak_sibling

    src = {
        "config/dragon-survival.toml",
        "config/dragon-survival-extra.toml.bak",
    }
    assert has_bak_sibling("config/dragon-survival.toml", src) is False


def test_resolve_bak_parent_versioned():
    from migration.planner import resolve_bak_parent
    src = {"config/create.toml"}
    assert resolve_bak_parent("config/create-1.toml.bak", src) == "config/create.toml"


def test_resolve_bak_parent_plain():
    from migration.planner import resolve_bak_parent
    src = {"config/ali_common.json"}
    assert resolve_bak_parent("config/ali_common.json.bak", src) == "config/ali_common.json"


def test_resolve_bak_parent_orphan_returns_none():
    from migration.planner import resolve_bak_parent
    assert resolve_bak_parent("config/ghost-1.toml.bak", {"config/other.toml"}) is None


def test_resolve_bak_parent_non_bak_returns_none():
    from migration.planner import resolve_bak_parent
    assert resolve_bak_parent("config/foo.toml", {"config/foo.toml"}) is None


def test_bak_file_follows_migrated_parent_to_bak_file_copy_new():
    """父 config 迁(COPY)→ .bak 落 bak_file COPY。"""
    report = DiffReport(
        candidate=[
            DiffItem("config/create.toml", _e("config/create.toml", md5="a"), None, "new"),
            DiffItem("config/create-1.toml.bak", _e("config/create-1.toml.bak"), None, "new"),
        ]
    )
    src = [_e("config/create.toml"), _e("config/create-1.toml.bak")]
    actions = _plan(report, src)
    bak = next(a for a in actions if a.path == "config/create-1.toml.bak")
    assert bak.behavior == Behavior.COPY
    assert bak.origin == Origin.BAK_FILE
    assert bak.backup_target is None  # new → 无备份


def test_bak_file_follows_migrated_parent_to_bak_file_copy_modified():
    """父 config 迁(COPY, modified)→ .bak 落 bak_file COPY + backup_target。"""
    report = DiffReport(
        candidate=[
            DiffItem("config/create.toml", _e("config/create.toml", md5="a"),
                     _e("config/create.toml", md5="b"), "modified"),
            DiffItem("config/create-1.toml.bak", _e("config/create-1.toml.bak"),
                     _e("config/create-1.toml.bak", md5="b"), "modified"),
        ]
    )
    src = [_e("config/create.toml"), _e("config/create-1.toml.bak")]
    actions = _plan(report, src)
    bak = next(a for a in actions if a.path == "config/create-1.toml.bak")
    assert bak.behavior == Behavior.COPY
    assert bak.origin == Origin.BAK_FILE
    assert bak.backup_target == "_conflict_backup/config/create-1.toml.bak"


def test_bak_file_follows_rebuild_parent_to_skip_rebuild():
    """父是 rebuild(SKIP)→ .bak 完整继承父 (SKIP, rebuild)。"""
    report = DiffReport(
        never=[DiffItem("config/fml.toml", _e("config/fml.toml"), None, "rebuild")],
        candidate=[DiffItem("config/fml-1.toml.bak", _e("config/fml-1.toml.bak"), None, "new")],
    )
    src = [_e("config/fml.toml"), _e("config/fml-1.toml.bak")]
    actions = _plan(report, src)
    bak = next(a for a in actions if a.path == "config/fml-1.toml.bak")
    assert bak.behavior == Behavior.SKIP
    assert bak.origin == Origin.REBUILD


def test_bak_file_orphan_goes_ask_needs_review():
    """父不在 src(孤儿)→ ASK / needs_review。"""
    report = DiffReport(
        candidate=[DiffItem("config/ghost-1.toml.bak", _e("config/ghost-1.toml.bak"), None, "new")]
    )
    src = [_e("config/ghost-1.toml.bak")]
    actions = _plan(report, src)
    bak = next(a for a in actions if a.path == "config/ghost-1.toml.bak")
    assert bak.behavior == Behavior.ASK
    assert bak.origin == Origin.NEEDS_REVIEW


def test_bak_file_outside_config_goes_ask_not_bak_file():
    """config/ 外的 .bak 不走本逻辑 → 普通候选 → ASK / needs_review。"""
    report = DiffReport(
        candidate=[DiffItem("kubejs/data.js.bak", _e("kubejs/data.js.bak"), None, "new")]
    )
    src = [_e("kubejs/data.js.bak")]
    actions = _plan(report, src)
    bak = next(a for a in actions if a.path == "kubejs/data.js.bak")
    assert bak.behavior == Behavior.ASK
    assert bak.origin == Origin.NEEDS_REVIEW


def test_planner_actions_match_registry_behavior():
    """planner 发射的每条 action,其 (origin, behavior) 必须与注册表一致(2D 模型 1:1 不变量)。

    reporter 据注册表 behavior 决定是否显示 new/modified 子计数;若某条 planner 路径
    违反不变量(如给 SKIP origin 发了 COPY),reporter 计数会与 executor 实际行为不符。
    """
    from migration.plan import ORIGIN_REGISTRY
    report = DiffReport(
        to_migrate=[DiffItem("options.txt", _e("options.txt"), None, "new")],
        candidate=[
            DiffItem("config/create.toml", _e("config/create.toml", md5="a"),
                     _e("config/create.toml", md5="b"), "modified"),
            DiffItem("config/default.toml", _e("config/default.toml", md5="a"),
                     _e("config/default.toml", md5="b"), "modified"),
            DiffItem("kubejs/x.js", _e("kubejs/x.js"), None, "new"),
            DiffItem("config/create-1.toml.bak", _e("config/create-1.toml.bak"), None, "new"),
        ],
        identical=[DiffItem("shared.dat", _e("shared.dat", md5="c"),
                           _e("shared.dat", md5="c"), "verified")],
        never=[
            DiffItem("logs/latest.log", _e("logs/latest.log"), None, "never"),
            DiffItem("config/fml.toml", _e("config/fml.toml"), None, "rebuild"),
        ],
        mods=[
            DiffItem("mods/a.jar", _e("mods/a.jar"), None, "to_add"),
            DiffItem("mods/b.jar", _e("mods/b.jar"), _e("mods/b.jar"), "shared"),
            DiffItem("mods/c.jar", None, _e("mods/c.jar"), "target_only"),
        ],
    )
    src = [
        _e("options.txt"), _e("config/create.toml"), _e("config/create-1.toml.bak"),
        _e("config/default.toml"), _e("kubejs/x.js"), _e("shared.dat"),
        _e("logs/latest.log"), _e("config/fml.toml"), _e("mods/a.jar"), _e("mods/b.jar"),
    ]
    actions = _plan(report, src)
    for a in actions:
        spec = ORIGIN_REGISTRY[a.origin.value]
        assert spec.behavior == a.behavior, (
            f"{a.path}: origin={a.origin.value} 注册表={spec.behavior.value} "
            f"实际={a.behavior.value}"
        )
