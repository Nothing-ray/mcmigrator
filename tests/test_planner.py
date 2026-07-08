from migration.differ import DiffItem, DiffReport
from migration.planner import Planner
from migration.plan import Action
from migration.snapshot import FileEntry


def _e(path, size=1, md5="x"):
    return FileEntry(path=path, size=size, md5=md5)


def _plan(report: DiffReport, src_entries: list[FileEntry] | None = None) -> list:
    src_index = {e.path: e for e in (src_entries or [])}
    return Planner(report, src_index).plan().actions


def test_to_migrate_new_goes_copy_new():
    report = DiffReport(to_migrate=[DiffItem("options.txt", _e("options.txt"), None, "new")])
    actions = _plan(report, [_e("options.txt")])
    a = next(a for a in actions if a.path == "options.txt")
    assert a.action == Action.COPY_NEW
    assert a.backup_target is None
    assert a.confidence == "high"


def test_to_migrate_modified_goes_overwrite_with_backup():
    report = DiffReport(
        to_migrate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="a"),
                             _e("config/foo.toml", md5="b"), "modified")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.action == Action.OVERWRITE
    assert a.backup_target == "_conflict_backup/config/foo.toml"
    assert a.md5_match is False


def test_identical_verified_goes_skip_identical_high():
    report = DiffReport(
        identical=[DiffItem("options.txt", _e("options.txt", md5="a"),
                            _e("options.txt", md5="a"), "verified")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "options.txt")
    assert a.action == Action.SKIP_IDENTICAL
    assert a.md5_match is True
    assert a.confidence == "high"


def test_identical_size_based_goes_skip_identical_medium():
    report = DiffReport(
        identical=[DiffItem("dh/lod.sqlite", _e("dh/lod.sqlite", size=16, md5=None),
                            _e("dh/lod.sqlite", size=16, md5=None), "size-based")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "dh/lod.sqlite")
    assert a.action == Action.SKIP_IDENTICAL
    assert a.md5_match is None
    assert a.confidence == "medium"


def test_never_goes_skip_never():
    report = DiffReport(never=[DiffItem("logs/latest.log", _e("logs/latest.log"), None, "never")])
    actions = _plan(report)
    a = next(a for a in actions if a.path == "logs/latest.log")
    assert a.action == Action.SKIP_NEVER


def test_mods_to_add_goes_add_mod():
    report = DiffReport(mods=[DiffItem("mods/extra.jar", _e("mods/extra.jar"), None, "to_add")])
    actions = _plan(report)
    a = next(a for a in actions if a.path == "mods/extra.jar")
    assert a.action == Action.ADD_MOD


def test_mods_shared_goes_keep_mod():
    report = DiffReport(
        mods=[DiffItem("mods/create.jar", _e("mods/create.jar"), _e("mods/create.jar"), "shared")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "mods/create.jar")
    assert a.action == Action.KEEP_MOD


def test_mods_target_only_goes_ignore_target_mod():
    report = DiffReport(
        mods=[DiffItem("mods/x.jar", None, _e("mods/x.jar"), "target_only")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "mods/x.jar")
    assert a.action == Action.IGNORE_TARGET_MOD


def test_only_in_dst_not_in_actions():
    """only_in_dst(目标独有)不产生 action——迁移只关心源→目标。"""
    report = DiffReport(
        only_in_dst=[DiffItem("config/target_only.toml", None, _e("config/target_only.toml"), "target_only")]
    )
    actions = _plan(report)
    assert all(a.path != "config/target_only.toml" for a in actions)


def test_candidate_non_config_goes_ask():
    """非 config/ 前缀的 candidate 一律 ask(config candidate 走 .bak 判定,见 Task 4 测试)。"""
    report = DiffReport(
        candidate=[DiffItem("kubejs/my.js", _e("kubejs/my.js"), None, "new")]
    )
    actions = _plan(report)
    a = next(a for a in actions if a.path == "kubejs/my.js")
    assert a.action == Action.ASK
    assert a.confidence == "low"


def test_config_candidate_with_plain_bak_goes_copy_new():
    """config/ 下 candidate + src 有 plain .bak → copy_new(dst 缺)。"""
    report = DiffReport(
        candidate=[DiffItem("config/create.toml", _e("config/create.toml", md5="a"), None, "new")]
    )
    src = [_e("config/create.toml"), _e("config/create.toml.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/create.toml")
    assert a.action == Action.COPY_NEW
    assert a.reason == ".bak sibling exists"
    assert a.confidence == "high"


def test_config_candidate_with_versioned_bak_goes_overwrite():
    """versioned .bak(foo-N.toml.bak)也算命中。"""
    report = DiffReport(
        candidate=[DiffItem("config/create.toml", _e("config/create.toml", md5="a"),
                            _e("config/create.toml", md5="b"), "modified")]
    )
    src = [_e("config/create.toml"), _e("config/create-1.toml.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/create.toml")
    assert a.action == Action.OVERWRITE
    assert a.backup_target == "_conflict_backup/config/create.toml"


def test_config_candidate_no_bak_goes_skip_default_config():
    """config/ 下 candidate + 无 .bak → skip_default_config。"""
    report = DiffReport(
        candidate=[DiffItem("config/default.toml", _e("config/default.toml", md5="a"),
                            _e("config/default.toml", md5="b"), "modified")]
    )
    src = [_e("config/default.toml")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/default.toml")
    assert a.action == Action.SKIP_DEFAULT_CONFIG
    assert a.confidence == "high"
    assert "no .bak" in a.reason


def test_bak_judgment_only_applies_to_config_prefix():
    """kubejs/ 下 candidate 即使有 .bak 兄弟也 → ask。"""
    report = DiffReport(
        candidate=[DiffItem("kubejs/my.js", _e("kubejs/my.js"), None, "new")]
    )
    src = [_e("kubejs/my.js"), _e("kubejs/my.js.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "kubejs/my.js")
    assert a.action == Action.ASK
    assert a.confidence == "low"


def test_bak_in_dst_only_does_not_count():
    """.bak 只查 src(dst 有 .bak 不算:判定玩家是否改过以源为准)。"""
    report = DiffReport(
        candidate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="a"),
                            _e("config/foo.toml", md5="b"), "modified")]
    )
    src = [_e("config/foo.toml")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.action == Action.SKIP_DEFAULT_CONFIG


def test_multiple_bak_versions_also_match():
    """foo-1.bak + foo-2.bak 都算改过。"""
    report = DiffReport(
        candidate=[DiffItem("config/foo.toml", _e("config/foo.toml", md5="a"), None, "new")]
    )
    src = [_e("config/foo.toml"), _e("config/foo-1.toml.bak"), _e("config/foo-2.toml.bak")]
    actions = _plan(report, src)
    a = next(a for a in actions if a.path == "config/foo.toml")
    assert a.action == Action.COPY_NEW


def test_bak_does_not_false_match_stem_with_hyphens():
    """stem 本身含连字符时不误匹配其他文件(如 dragon-survival 不匹配 dragon-survival-extra)。"""
    from migration.planner import has_bak_sibling

    src = {
        "config/dragon-survival.toml",
        "config/dragon-survival-extra.toml.bak",
    }
    assert has_bak_sibling("config/dragon-survival.toml", src) is False
