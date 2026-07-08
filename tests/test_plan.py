import json
from pathlib import Path

import pytest

from migration.plan import (
    Action,
    ActionRecord,
    MigrationPlan,
    PlanFormatError,
    plan_path,
)


def _sample() -> MigrationPlan:
    return MigrationPlan(
        src="227",
        dst="229",
        generated_at="2026-07-07T12:00:00+08:00",
        actions=[
            ActionRecord(
                path="options.txt",
                action=Action.COPY_NEW,
                src_size=1234,
                dst_size=None,
                md5_match=None,
                confidence="high",
                reason="must_migrate + dst missing",
                backup_target=None,
            ),
            ActionRecord(
                path="config/create.toml",
                action=Action.OVERWRITE,
                src_size=100,
                dst_size=98,
                md5_match=False,
                confidence="high",
                reason=".bak sibling exists",
                backup_target="_conflict_backup/config/create.toml",
            ),
        ],
    )


def test_action_values():
    assert Action.COPY_NEW.value == "copy_new"
    assert Action.OVERWRITE.value == "overwrite"
    assert Action.SKIP_DEFAULT_CONFIG.value == "skip_default_config"
    assert Action.ASK.value == "ask"
    assert Action.ADD_MOD.value == "add_mod"


def test_save_load_roundtrip(tmp_path: Path):
    sp = tmp_path / "p.plan.json"
    _sample().save(sp)
    loaded = MigrationPlan.load(sp)
    assert loaded.src == "227" and loaded.dst == "229"
    assert loaded.actions == _sample().actions


def test_save_creates_parent_dirs(tmp_path: Path):
    sp = tmp_path / ".mcmig" / "plans" / "227__229.plan.json"
    _sample().save(sp)
    assert sp.exists()


def test_summary_counts_by_action(tmp_path: Path):
    sp = tmp_path / "p.json"
    _sample().save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    assert doc["summary"]["copy_new"] == 1
    assert doc["summary"]["overwrite"] == 1
    assert doc["summary"]["skip_default_config"] == 0


def test_load_rejects_unsupported_format(tmp_path: Path):
    sp = tmp_path / "bad.json"
    sp.write_text(
        json.dumps({"plan_format": 999, "src": "a", "dst": "b", "generated_at": "", "actions": []}),
        encoding="utf-8",
    )
    with pytest.raises(PlanFormatError):
        MigrationPlan.load(sp)


def test_plan_path_helper():
    p = plan_path(Path("C:/work"), "227", "229")
    assert p == Path("C:/work/.mcmig/plans/227__229.plan.json")


def test_md5_match_three_states_roundtrip(tmp_path: Path):
    sp = tmp_path / "p.json"
    plan = MigrationPlan(
        src="a", dst="b", generated_at="t",
        actions=[
            ActionRecord("x1", Action.COPY_NEW, 1, None, None, "high", "r", None),
            ActionRecord("x2", Action.OVERWRITE, 1, 1, True, "high", "r", None),
            ActionRecord("x3", Action.OVERWRITE, 1, 1, False, "high", "r", None),
        ],
    )
    plan.save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    assert doc["actions"][0]["md5_match"] is None
    assert doc["actions"][1]["md5_match"] is True
    assert doc["actions"][2]["md5_match"] is False
