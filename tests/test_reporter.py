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


def test_plan_reporter_to_json_parseable():
    import json
    from migration.differ import DiffItem, DiffReport
    from migration.planner import Planner
    from migration.reporter import PlanReporter
    from migration.snapshot import FileEntry

    report = DiffReport(
        to_migrate=[DiffItem("options.txt", FileEntry("options.txt", 10, "a"), None, "new")]
    )
    plan = Planner(report, {"options.txt": FileEntry("options.txt", 10, "a")}).plan()
    plan.src, plan.dst = "227", "229"
    doc = json.loads(PlanReporter(plan, src_version="227", dst_version="229").to_json())
    assert doc["src"] == "227" and doc["dst"] == "229"
    assert doc["summary"]["copy_new"] == 1
    assert any(a["path"] == "options.txt" and a["action"] == "copy_new" for a in doc["actions"])


def test_plan_reporter_render_no_error(capsys):
    from migration.differ import DiffItem, DiffReport
    from migration.planner import Planner
    from migration.reporter import PlanOptions, PlanReporter
    from migration.snapshot import FileEntry

    report = DiffReport(
        to_migrate=[
            DiffItem("options.txt", FileEntry("options.txt", 10, "a"), None, "new"),
            DiffItem("config/foo.toml", FileEntry("config/foo.toml", 5, "a"),
                     FileEntry("config/foo.toml", 4, "b"), "modified"),
        ],
        candidate=[DiffItem("kubejs/x.js", FileEntry("kubejs/x.js", 1, "a"), None, "new")],
    )
    plan = Planner(report, {"options.txt": FileEntry("options.txt", 10, "a")}).plan()
    plan.src, plan.dst = "227", "229"
    PlanReporter(plan, src_version="227", dst_version="229").render(PlanOptions())


def test_plan_options_defaults_hide_skip():
    from migration.reporter import PlanOptions

    opts = PlanOptions()
    assert opts.show_skip is False
    assert opts.category is None


def test_plan_reporter_show_skip_renders_skip_actions(capsys):
    from migration.differ import DiffItem, DiffReport
    from migration.planner import Planner
    from migration.reporter import PlanOptions, PlanReporter
    from migration.snapshot import FileEntry

    report = DiffReport(
        candidate=[DiffItem("config/d.toml", FileEntry("config/d.toml", 1, "a"),
                            FileEntry("config/d.toml", 1, "b"), "modified")]
    )
    plan = Planner(report, {"config/d.toml": FileEntry("config/d.toml", 1, "a")}).plan()
    plan.src, plan.dst = "a", "b"
    PlanReporter(plan, src_version="a", dst_version="b").render(PlanOptions(show_skip=True))
    out = capsys.readouterr().out
    assert "skip_default" in out


def test_action_meta_covers_all_action_values():
    """ACTION_META 必须覆盖所有 Action enum 值,否则 render 会静默丢弃。"""
    from migration.plan import Action
    from migration.reporter import ACTION_META

    assert set(ACTION_META.keys()) == {a.value for a in Action}
