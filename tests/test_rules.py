from pathlib import Path

from migration import rules
from migration.rules import Category, Rule, RuleSet


def test_category_values():
    assert Category.NEVER.value == "never"
    assert Category.MUST_MIGRATE.value == "must_migrate"


def test_ruleset_first_match_wins():
    rs = RuleSet(
        rules=[
            Rule(match="config/*.toml", decide=Category.NEVER),
            Rule(match="config/create.toml", decide=Category.MUST_MIGRATE),
        ]
    )
    # 第一条命中 config/create.toml → NEVER(尽管第二条更具体)
    assert rs.classify("config/create.toml") == Category.NEVER


def test_ruleset_no_match_returns_unknown():
    rs = RuleSet(rules=[Rule(match="logs/**", decide=Category.NEVER)])
    assert rs.classify("options.txt") == Category.UNKNOWN


def test_classify_normalizes_backslash():
    rs = RuleSet(rules=[Rule(match="config/*.toml", decide=Category.NEVER)])
    assert rs.classify("config\\a.toml") == Category.NEVER


def test_glob_suffix_does_not_collapse_to_subdirs():
    # config/*.toml 不会命中子目录文件:.toml 后缀不匹配目录名 'sub',
    # 故不触发 gitignore 目录级联 —— 设计文档承诺的 "config/*.toml 仅命中 config/foo.toml"
    rs = RuleSet(rules=[Rule(match="config/*.toml", decide=Category.NEVER)])
    assert rs.classify("config/a.toml") == Category.NEVER
    assert rs.classify("config/sub/b.toml") == Category.UNKNOWN


def test_glob_double_star_recursive():
    rs = RuleSet(rules=[Rule(match="logs/**", decide=Category.NEVER)])
    assert rs.classify("logs/a/b/c.log") == Category.NEVER


def test_glob_no_slash_matches_any_depth():
    rs = RuleSet(rules=[Rule(match="*.bak", decide=Category.MUST_MIGRATE)])
    assert rs.classify("config/a.bak") == Category.MUST_MIGRATE
    assert rs.classify("config/sub/b.bak") == Category.MUST_MIGRATE


def test_load_cli_rules_exclude_include():
    rs = RuleSet.from_layers(
        rules.load_cli_rules(excludes=["screenshots/**"], includes=["my/**"]),
        [Rule(match="options.txt", decide=Category.MUST_MIGRATE)],
    )
    assert rs.classify("screenshots/a.png") == Category.NEVER
    assert rs.classify("my/note.txt") == Category.MUST_MIGRATE


def test_load_user_rules_verbose(tmp_path: Path):
    f = tmp_path / "r.yaml"
    f.write_text(
        "version: 1\nrules:\n  - match: 'secret/**'\n    decide: never\n    reason: '私货'\n",
        encoding="utf-8",
    )
    layer, errs = rules.load_user_rules(f)
    assert errs == []
    assert len(layer) == 1
    assert layer[0].decide == Category.NEVER
    assert layer[0].reason == "私货"


def test_load_user_rules_bad_decide_is_error(tmp_path: Path):
    f = tmp_path / "bad.yaml"
    f.write_text("rules:\n  - match: 'x'\n    decide: bogus\n", encoding="utf-8")
    layer, errs = rules.load_user_rules(f)
    assert layer == []
    assert len(errs) == 1 and "decide" in errs[0]


def test_load_default_rules_with_ver_substitution():
    layer, errs = rules.load_default_rules("1.21.1-NeoForge_21.1.227")
    assert errs == []
    matches = {r.match for r in layer}
    assert "1.21.1-NeoForge_21.1.227.jar" in matches
    assert "1.21.1-NeoForge_21.1.227-natives/**" in matches
    assert "options.txt" in {r.match for r in layer if r.decide == Category.MUST_MIGRATE}


def test_load_default_rules_has_never_and_must():
    layer, _ = rules.load_default_rules("v1")
    by_cat = {c: [] for c in Category}
    for r in layer:
        by_cat[r.decide].append(r.match)
    assert "logs/**" in by_cat[Category.NEVER]
    assert "options.txt" in by_cat[Category.MUST_MIGRATE]


def test_from_layers_priority_order():
    high = [Rule(match="x", decide=Category.NEVER, source="cli")]
    low = [Rule(match="x", decide=Category.MUST_MIGRATE, source="default")]
    rs = RuleSet.from_layers(high, low)
    assert rs.classify("x") == Category.NEVER  # 高层先命中


def test_load_default_rules_multiple_versions():
    # diff 上下文:规则集应同时展开 src 与 dst 的 <ver> 占位
    layer, _ = rules.load_default_rules(["v227", "v228"])
    matches = {r.match for r in layer}
    assert "v227.jar" in matches
    assert "v228.jar" in matches
    assert "v227-natives/**" in matches
    assert "v228-natives/**" in matches
    # 每条 <ver> 规则应展开为 2 条(两版本),非 <ver> 规则不变
    # logs/** 等非 <ver> 规则各 1 条;<ver>.jar 类应出现 2 条(v227/v228)
    assert sum(1 for r in layer if r.match in ("v227.jar", "v228.jar")) == 2


def test_load_whitelist_rules_returns_must_migrate(tmp_path: Path):
    from migration import rules
    from migration.rules import Category

    f = tmp_path / "wl.yaml"
    f.write_text(
        "version: 1\nrules:\n"
        "  - match: 'iris.properties'\n    reason: 'Iris 设置'\n"
        "  - match: 'config/jade/**/*.json'\n    reason: 'Jade 偏好'\n",
        encoding="utf-8",
    )
    layer, errs = rules.load_whitelist_rules(f)
    assert errs == []
    assert len(layer) == 2
    assert all(r.decide == Category.MUST_MIGRATE for r in layer)
    assert layer[0].match == "iris.properties"
    assert layer[0].reason == "Iris 设置"
    assert layer[0].source == "whitelist"


def test_load_whitelist_rules_missing_file_returns_empty(tmp_path: Path):
    from migration import rules

    layer, errs = rules.load_whitelist_rules(tmp_path / "nope.yaml")
    assert layer == [] and errs == []


def test_load_whitelist_rules_bad_match_skipped(tmp_path: Path):
    from migration import rules

    f = tmp_path / "bad.yaml"
    f.write_text(
        "rules:\n  - match: ''\n    reason: '空 match'\n  - match: 'ok.txt'\n",
        encoding="utf-8",
    )
    layer, errs = rules.load_whitelist_rules(f)
    assert len(layer) == 1
    assert layer[0].match == "ok.txt"
    assert len(errs) == 1 and "match" in errs[0]


def test_whitelist_priority_above_default_below_user():
    """白名单层 > default,< user(rules.yaml)。"""
    from migration.rules import Category, Rule, RuleSet

    user = [Rule(match="iris.properties", decide=Category.NEVER, source="user")]
    whitelist = [Rule(match="iris.properties", decide=Category.MUST_MIGRATE, source="whitelist")]
    default = [Rule(match="iris.properties", decide=Category.UNKNOWN, source="default")]
    rs = RuleSet.from_layers(user, whitelist, default)
    assert rs.classify("iris.properties") == Category.NEVER  # user 优先

    rs2 = RuleSet.from_layers(whitelist, default)
    assert rs2.classify("iris.properties") == Category.MUST_MIGRATE  # whitelist 升级


def test_rebuild_beats_whitelist_same_path():
    """同 path 既命中 rebuild 又命中 whitelist → rebuild 赢(spec §4.4 / §8.2 安全护栏)。"""
    rebuild = [Rule(match="config/fml.toml", decide=Category.REBUILD, source="rebuild")]
    whitelist = [Rule(match="config/fml.toml", decide=Category.MUST_MIGRATE, source="whitelist")]
    assert RuleSet.from_layers(rebuild, whitelist).classify("config/fml.toml") == Category.REBUILD


def test_load_whitelist_rules_from_text_returns_must_migrate():
    """文本入口功能等价——验证基本解析(PyInstaller 安全路径)。"""
    from migration import rules
    from migration.rules import Category

    text = "version: 1\nrules:\n  - match: 'iris.properties'\n    reason: 'Iris'\n"
    layer, errs = rules.load_whitelist_rules_from_text(text, "units")
    assert errs == []
    assert len(layer) == 1
    assert layer[0].match == "iris.properties"
    assert layer[0].decide == Category.MUST_MIGRATE
    assert layer[0].source == "whitelist"


def test_load_whitelist_rules_from_text_bad_yaml():
    """YAML 语法错误返回空列表+错误消息。"""
    from migration import rules

    layer, errs = rules.load_whitelist_rules_from_text(": broken", "bad")
    assert layer == []
    assert len(errs) == 1 and "YAML" in errs[0]


def test_category_has_rebuild():
    assert Category.REBUILD.value == "rebuild"


def test_rebuild_decide_accepted_in_verbose_rules(tmp_path: Path):
    f = tmp_path / "r.yaml"
    f.write_text(
        "version: 1\nrules:\n  - match: 'config/fml.toml'\n    decide: rebuild\n    reason: '版本绑定'\n",
        encoding="utf-8",
    )
    layer, errs = rules.load_user_rules(f)
    assert errs == []
    assert layer[0].decide == Category.REBUILD


def test_load_rebuild_rules_from_text_forces_rebuild():
    text = "version: 1\nrules:\n  - match: 'config/fml.toml'\n    reason: 'FML'\n"
    layer, errs = rules.load_rebuild_rules_from_text(text, "rebuild.yaml")
    assert errs == []
    assert len(layer) == 1
    assert layer[0].match == "config/fml.toml"
    assert layer[0].decide == Category.REBUILD
    assert layer[0].source == "rebuild"


def test_load_rebuild_rules_from_text_bad_match_skipped():
    text = "rules:\n  - match: ''\n    reason: '空'\n  - match: 'config/ok.toml'\n"
    layer, errs = rules.load_rebuild_rules_from_text(text, "rebuild.yaml")
    assert len(layer) == 1
    assert layer[0].match == "config/ok.toml"
    assert len(errs) == 1 and "match" in errs[0]


def test_load_rebuild_rules_from_text_bad_yaml():
    layer, errs = rules.load_rebuild_rules_from_text(": broken", "bad")
    assert layer == []
    assert len(errs) == 1 and "YAML" in errs[0]


def test_rebuild_yaml_loads_six_entries():
    from importlib import resources
    text = resources.files("migration").joinpath("data/rebuild.yaml").read_text(encoding="utf-8")
    layer, errs = rules.load_rebuild_rules_from_text(text, "rebuild.yaml")
    assert errs == []
    matches = {r.match for r in layer}
    assert "config/fml.toml" in matches
    assert "config/sodium-fingerprint.json" in matches
    assert len(layer) == 6
    assert all(r.decide == Category.REBUILD for r in layer)


def test_whitelist_yaml_loads_sodium_options():
    from importlib import resources
    text = resources.files("migration").joinpath("data/whitelist.yaml").read_text(encoding="utf-8")
    layer, errs = rules.load_whitelist_rules_from_text(text, "whitelist.yaml")
    assert errs == []
    matches = {r.match for r in layer}
    assert "config/sodium-options.json" in matches
    assert "config/jei/*.ini" in matches
    # 旧条目已被 *.ini 收编删除
    assert "config/jei/*sort-order*" not in matches
