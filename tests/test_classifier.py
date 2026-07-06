from migration import rules
from migration.classifier import Classifier
from migration.rules import Category, Rule
from migration.snapshot import FileEntry


def _clf(*layers):
    return Classifier(rules.RuleSet.from_layers(*layers))


def test_classify_path_never_wins_over_must_via_priority():
    clf = _clf(
        [Rule(match="config/embeddium-options.json", decide=Category.NEVER, source="cli")],
        [Rule(match="config/*.json", decide=Category.MUST_MIGRATE, source="user")],
    )
    assert clf.classify_path("config/embeddium-options.json") == Category.NEVER


def test_classify_path_falls_back_to_default_then_unknown():
    default, _ = rules.load_default_rules("v1")
    clf = _clf(default)
    assert clf.classify_path("options.txt") == Category.MUST_MIGRATE
    assert clf.classify_path("logs/latest.log") == Category.NEVER
    assert clf.classify_path("config/unknown.toml") == Category.UNKNOWN


def test_classify_entry_uses_path():
    clf = _clf([Rule(match="options.txt", decide=Category.MUST_MIGRATE)])
    e = FileEntry(path="options.txt", size=10, md5="abc")
    assert clf.classify(e) == Category.MUST_MIGRATE


def test_classify_all_preserves_order():
    clf = _clf([Rule(match="*.txt", decide=Category.MUST_MIGRATE)])
    entries = [
        FileEntry(path="a.txt", size=1, md5=None),
        FileEntry(path="b.log", size=1, md5=None),
    ]
    out = clf.classify_all(entries)
    assert [c.category for c in out] == [Category.MUST_MIGRATE, Category.UNKNOWN]
