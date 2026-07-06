import json

from migration import rules
from migration.classifier import Classifier
from migration.differ import Differ
from migration.reporter import DiffReporter, ReportOptions
from migration.snapshot import FileEntry


def _report():
    clf = Classifier(rules.RuleSet.from_layers(*rules.load_default_rules("mini")))
    d = Differ(
        [FileEntry("options.txt", 10, "a"), FileEntry("logs/latest.log", 1, None)],
        [FileEntry("options.txt", 10, "b")],
        clf,
    ).diff()
    return DiffReporter(d, src_version="mini", dst_version="mini_b")


def test_to_json_is_parseable_and_has_summary():
    doc = json.loads(_report().to_json())
    assert doc["src"] == "mini" and doc["dst"] == "mini_b"
    assert "summary" in doc and "buckets" in doc
    assert doc["summary"]["to_migrate"] >= 1


def test_render_runs_without_error(capsys):
    _report().render(ReportOptions())  # 不抛即通过


def test_render_with_show_never(capsys):
    _report().render(ReportOptions(show_never=True))  # 含 never 桶也不抛


def test_options_defaults_hide_identical_and_never():
    opts = ReportOptions()
    assert opts.show_identical is False
    assert opts.show_never is False
    assert opts.category is None
