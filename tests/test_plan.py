import json
from pathlib import Path

import pytest

from migration.plan import (
    ActionRecord,
    Behavior,
    MigrationPlan,
    Origin,
    ORIGIN_REGISTRY,
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
                behavior=Behavior.COPY,
                origin=Origin.MUST_MIGRATE,
                src_size=1234,
                dst_size=None,
                md5_match=None,
                confidence="high",
                reason="must_migrate + dst missing",
                backup_target=None,
            ),
            ActionRecord(
                path="config/create.toml",
                behavior=Behavior.COPY,
                origin=Origin.CONFIG_MODIFIED,
                src_size=100,
                dst_size=98,
                md5_match=False,
                confidence="high",
                reason=".bak sibling exists",
                backup_target="_conflict_backup/config/create.toml",
            ),
        ],
    )


def test_behavior_values():
    assert Behavior.COPY.value == "copy"
    assert Behavior.SKIP.value == "skip"
    assert Behavior.ASK.value == "ask"


def test_origin_values_count():
    assert {o.value for o in Origin} == {
        "must_migrate", "config_modified", "bak_file", "mod_added",
        "identical", "never", "default_config", "rebuild",
        "mod_shared", "mod_target_only", "needs_review",
    }


def test_origin_registry_covers_all_origins():
    assert set(ORIGIN_REGISTRY.keys()) == {o.value for o in Origin}


def test_origin_seed_behavior_matches_spec():
    """_ORIGIN_SEED 的 behavior 必须与 spec §2 表一致(单一事实来源锁)。"""
    expected = {
        "must_migrate": Behavior.COPY,
        "config_modified": Behavior.COPY,
        "bak_file": Behavior.COPY,
        "mod_added": Behavior.COPY,
        "needs_review": Behavior.ASK,
        "rebuild": Behavior.SKIP,
        "default_config": Behavior.SKIP,
        "never": Behavior.SKIP,
        "identical": Behavior.SKIP,
        "mod_shared": Behavior.SKIP,
        "mod_target_only": Behavior.SKIP,
    }
    for key, beh in expected.items():
        assert ORIGIN_REGISTRY[key].behavior == beh, f"{key} behavior mismatch"


def test_register_origin_adds_entry(origin_registry_snapshot):
    from migration.plan import register_origin
    register_origin(
        "custom_x", title="自定义", visible=False, show_backup=False, behavior=Behavior.SKIP
    )
    assert "custom_x" in ORIGIN_REGISTRY
    assert ORIGIN_REGISTRY["custom_x"].behavior == Behavior.SKIP


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


def test_plan_format_is_2(tmp_path: Path):
    sp = tmp_path / "p.json"
    _sample().save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    assert doc["plan_format"] == 2


def test_summary_counts_by_origin(tmp_path: Path):
    sp = tmp_path / "p.json"
    _sample().save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    assert doc["summary"]["must_migrate"] == 1
    assert doc["summary"]["config_modified"] == 1
    assert doc["summary"]["rebuild"] == 0


def test_actions_serialize_behavior_and_origin(tmp_path: Path):
    sp = tmp_path / "p.json"
    _sample().save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    a = doc["actions"][0]
    assert a["behavior"] == "copy"
    assert a["origin"] == "must_migrate"
    assert "action" not in a


def test_load_rejects_unsupported_format(tmp_path: Path):
    sp = tmp_path / "bad.json"
    sp.write_text(
        json.dumps({"plan_format": 1, "src": "a", "dst": "b", "generated_at": "", "actions": []}),
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
            ActionRecord("x1", Behavior.COPY, Origin.MUST_MIGRATE, 1, None, None, "high", "r", None),
            ActionRecord("x2", Behavior.COPY, Origin.MUST_MIGRATE, 1, 1, True, "high", "r", None),
            ActionRecord("x3", Behavior.COPY, Origin.MUST_MIGRATE, 1, 1, False, "high", "r", None),
        ],
    )
    plan.save(sp)
    doc = json.loads(sp.read_text(encoding="utf-8"))
    assert doc["actions"][0]["md5_match"] is None
    assert doc["actions"][1]["md5_match"] is True
    assert doc["actions"][2]["md5_match"] is False
