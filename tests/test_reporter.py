import json

from migration import rules
from migration.classifier import Classifier
from migration.differ import DiffItem, DiffReport, Differ
from migration.moddb import ModPair
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
    assert doc["summary"]["must_migrate"] == 1
    assert any(
        a["path"] == "options.txt" and a["behavior"] == "copy" and a["origin"] == "must_migrate"
        for a in doc["actions"]
    )


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


def test_plan_reporter_show_skip_renders_skip_origins(capsys):
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
    assert "默认配置" in out  # origin=default_config 的标题
    assert "新增" not in out and "覆盖" not in out  # SKIP 类 origin 不显示 new/modified 子计数


def test_origin_registry_covers_all_origin_values():
    """ORIGIN_REGISTRY 必须覆盖所有 Origin enum 值,否则 render 会静默丢弃。"""
    from migration.plan import Origin, ORIGIN_REGISTRY

    assert set(ORIGIN_REGISTRY.keys()) == {o.value for o in Origin}


def test_plan_reporter_new_modified_subcount(capsys):
    from migration.differ import DiffItem, DiffReport
    from migration.planner import Planner
    from migration.reporter import PlanOptions, PlanReporter
    from migration.snapshot import FileEntry

    report = DiffReport(
        to_migrate=[
            DiffItem("options.txt", FileEntry("options.txt", 10, "a"), None, "new"),
            DiffItem("server.dat", FileEntry("server.dat", 5, "a"),
                     FileEntry("server.dat", 4, "b"), "modified"),
        ],
    )
    plan = Planner(report, {"options.txt": FileEntry("options.txt", 10, "a")}).plan()
    plan.src, plan.dst = "a", "b"
    PlanReporter(plan, src_version="a", dst_version="b").render(PlanOptions())
    out = capsys.readouterr().out
    # must_migrate 组标题含新增/覆盖子计数
    assert "新增" in out and "覆盖" in out


def test_plan_reporter_orphan_in_summary():
    from migration.differ import DiffItem, DiffReport
    from migration.planner import Planner
    from migration.reporter import PlanReporter
    from migration.snapshot import FileEntry

    report = DiffReport(
        never=[DiffItem("config/jade/foo.json", FileEntry("config/jade/foo.json", 5, "a"),
                        None, "orphan")]
    )
    plan = Planner(report, {"config/jade/foo.json": FileEntry("config/jade/foo.json", 5, "a")}).plan()
    plan.src, plan.dst = "228", "233"
    doc = json.loads(PlanReporter(plan, src_version="228", dst_version="233").to_json())
    assert doc["summary"].get("orphan", 0) == 1


def test_plan_reporter_render_orphan_group(capsys):
    from migration.differ import DiffItem, DiffReport
    from migration.planner import Planner
    from migration.reporter import PlanReporter, PlanOptions
    from migration.snapshot import FileEntry

    report = DiffReport(
        never=[DiffItem("config/jade/foo.json", FileEntry("config/jade/foo.json", 5, "a"),
                        None, "orphan")]
    )
    plan = Planner(report, {"config/jade/foo.json": FileEntry("config/jade/foo.json", 5, "a")}).plan()
    plan.src, plan.dst = "228", "233"
    PlanReporter(plan, src_version="228", dst_version="233").render(
        PlanOptions(show_skip=True)
    )
    out = capsys.readouterr().out
    assert "孤儿" in out
    assert "迁移无意义" in out
    assert "rules.yaml" in out


def test_plan_reporter_compat_warnings_render(capsys):
    from migration.moddb import CompatWarning
    from migration.reporter import PlanReporter
    from migration.plan import MigrationPlan
    from datetime import datetime, timezone

    plan = MigrationPlan(
        src="228", dst="233",
        generated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        actions=[],
    )
    warnings = [
        CompatWarning("cp_lib", "cp_lib.jar", "5.0.18", "[21.1.233,)", "21.1.228"),
    ]
    reporter = PlanReporter(plan, src_version="228", dst_version="233")
    reporter.render_compat_warnings(warnings)
    out = capsys.readouterr().out
    assert "cp_lib" in out
    assert "21.1.233" in out
    assert "21.1.228" in out


def test_plan_reporter_compat_warnings_empty_no_output(capsys):
    from migration.reporter import PlanReporter
    from migration.plan import MigrationPlan
    from datetime import datetime, timezone

    plan = MigrationPlan(
        src="228", dst="233",
        generated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        actions=[],
    )
    reporter = PlanReporter(plan, src_version="228", dst_version="233")
    reporter.render_compat_warnings([])
    out = capsys.readouterr().out
    assert out == ""


def test_plan_reporter_to_json_includes_compat_warnings():
    """to_json(compat_warnings) 输出含 compat_warnings 数组(I3:JSON 消费者可见)。"""
    from datetime import datetime, timezone

    from migration.moddb import CompatWarning
    from migration.plan import MigrationPlan
    from migration.reporter import PlanReporter

    plan = MigrationPlan(
        src="228", dst="233",
        generated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        actions=[],
    )
    reporter = PlanReporter(plan, src_version="228", dst_version="233")
    warnings = [
        CompatWarning(
            modid="cp_lib",
            jar_filename="cp_lib.jar",
            mod_version="5.0.18",
            required_range="[21.1.233,)",
            dst_neoforge="21.1.228",
        )
    ]
    doc = json.loads(reporter.to_json(warnings))
    assert "compat_warnings" in doc
    assert len(doc["compat_warnings"]) == 1
    w = doc["compat_warnings"][0]
    assert w["modid"] == "cp_lib"
    assert w["jar_filename"] == "cp_lib.jar"
    assert w["mod_version"] == "5.0.18"
    assert w["required_range"] == "[21.1.233,)"
    assert w["dst_neoforge"] == "21.1.228"


def test_plan_reporter_to_json_omits_compat_warnings_when_empty():
    """to_json() 无参/空列表 → compat_warnings 字段不出现(向后兼容)。"""
    from migration.plan import MigrationPlan
    from migration.reporter import PlanReporter

    plan = MigrationPlan(
        src="228", dst="233",
        generated_at="2026-01-01T00:00:00+00:00",
        actions=[],
    )
    reporter = PlanReporter(plan, src_version="228", dst_version="233")
    # 无参数调用(老调用方)
    doc_no_arg = json.loads(reporter.to_json())
    assert "compat_warnings" not in doc_no_arg
    # 显式空列表
    doc_empty = json.loads(reporter.to_json([]))
    assert "compat_warnings" not in doc_empty


# 批次 B 渲染层测试:F4 配对标记 + F3 方向提示 + mod_pairs JSON。
# (import 已合并至文件顶部:json / DiffItem / DiffReport / ModPair / DiffReporter / ReportOptions)


def _pairs():
    return [
        ModPair(modid="waystones", kind="upgrade",
                src_files=["mods/waystones-42.jar"], dst_files=["mods/waystones-44.jar"],
                src_version="21.1.42", dst_version="21.1.44"),
    ]


def _report_with_mods():
    r = DiffReport()
    r.mods = [
        DiffItem("mods/waystones-42.jar", None, None, note="to_add"),
        DiffItem("mods/waystones-44.jar", None, None, note="target_only"),
    ]
    r.candidate = [DiffItem("config/new-stuff.toml", None, None, note="new")]
    r.only_in_dst = [DiffItem("config/dst-only.toml", None, None, note="target_only")]
    return r


def test_to_json_contains_mod_pairs_additive():
    doc = json.loads(DiffReporter(_report_with_mods(), src_version="a", dst_version="b",
                                  mod_pairs=_pairs()).to_json())
    assert doc["mod_pairs"][0]["modid"] == "waystones"
    assert doc["mod_pairs"][0]["kind"] == "upgrade"
    # 六桶键与 note 词汇不变
    assert set(doc["buckets"]) == {"to_migrate", "candidate", "mods", "only_in_dst", "identical", "never"}
    assert doc["buckets"]["mods"][0]["note"] == "to_add"


def test_to_json_mod_pairs_default_empty():
    doc = json.loads(DiffReporter(_report_with_mods(), src_version="a", dst_version="b").to_json())
    assert doc["mod_pairs"] == []


def test_render_pair_marker_and_direction_hints(capsys):
    DiffReporter(_report_with_mods(), src_version="a", dst_version="b",
                 mod_pairs=_pairs()).render(ReportOptions(show_identical=True, show_never=True))
    out = capsys.readouterr().out
    assert "to_add ⇄upgrade" in out
    assert "target_only ⇄upgrade" in out
    assert "new ←仅源" in out
    assert "target_only →仅目标" in out
    assert "配对: ⇄upgrade ×1" in out


def test_render_rebuilt_marked_with_warning():
    r = _report_with_mods()
    r.mods.append(DiffItem("mods/x-1.0.jar", None, None, note="rebuilt"))
    from rich.console import Console
    import io as _io
    buf = _io.StringIO()
    DiffReporter(r, src_version="a", dst_version="b").render(
        ReportOptions(), console=Console(file=buf, force_terminal=False, width=200))
    out = buf.getvalue()
    assert "rebuilt" in out and "⚠" in out


def test_render_mods_to_add_footnote():
    r = _report_with_mods()  # 已含 to_add 条目
    from rich.console import Console
    import io as _io
    buf = _io.StringIO()
    DiffReporter(r, src_version="a", dst_version="b").render(
        ReportOptions(), console=Console(file=buf, force_terminal=False, width=200))
    assert "有意删除" in buf.getvalue()


def test_render_no_footnote_without_to_add():
    r = DiffReport()
    r.mods = [DiffItem("mods/x-1.0.jar", None, None, note="shared")]
    from rich.console import Console
    import io as _io
    buf = _io.StringIO()
    DiffReporter(r, src_version="a", dst_version="b").render(
        ReportOptions(), console=Console(file=buf, force_terminal=False, width=200))
    assert "有意删除" not in buf.getvalue()
