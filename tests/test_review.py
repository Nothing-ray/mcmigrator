"""审阅有效性:计划身份 vs 执行前置条件分离;审阅状态校验(spec §3.3)。

- plan_fingerprint 只证「这份计划文件的内容」(剔除 executed_at/execution_summary/
  tool_version 等运行结果字段——运行记录≠动作定义);
- validate_review / check_action_states 证「审阅时看到的世界仍是执行时的世界」:
  实例身份/双侧快照/规则指纹逐项比对,动作路径的源/目标当前状态 vs 快照记录状态
  逐条比对,失配阻断并要求重新审阅——复制完整性校验(identical/MD5)不能替代之。
"""

from __future__ import annotations

import json

from migration.plan import MigrationPlan
from migration.pipeline import scan_version
from migration.review import (
    check_action_states,
    file_sha256,
    issue_review,
    plan_fingerprint,
    rules_fingerprint,
    validate_review,
)


def test_file_sha256_stable_and_sensitive(tmp_path):
    """file_sha256:同内容稳定,内容变即变(审阅守卫的指纹基元)。"""
    p = tmp_path / "f.json"
    p.write_text("{}", encoding="utf-8")
    h1 = file_sha256(p)
    assert h1 == file_sha256(p) and len(h1) == 64
    p.write_text('{"a":1}', encoding="utf-8")
    assert file_sha256(p) != h1


def test_rules_fingerprint_path_independent(tmp_path):
    """W2.5 复审 B2:指纹只哈希内容——路径拼写差异不得触发假阳性 rules_changed。

    同内容不同位置/不同拼写 → 同指纹;内容变 → 指纹变;缺失来源零贡献
    (签名时缺失+校验时仍缺失 = 无变化)。
    """
    a = tmp_path / "a" / "rules.yaml"
    b = tmp_path / "b" / "RULES.yaml"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_text("rules: []\n", encoding="utf-8")
    b.write_text("rules: []\n", encoding="utf-8")
    assert rules_fingerprint([a]) == rules_fingerprint([b])  # 路径/大小写无关
    missing = tmp_path / "nowhere" / "rules.yaml"
    assert rules_fingerprint([missing]) == rules_fingerprint([])  # 缺失零贡献
    b.write_text("rules: [x]\n", encoding="utf-8")
    assert rules_fingerprint([a]) != rules_fingerprint([b])  # 内容敏感


def test_issue_review_records_rule_sources(tmp_path):
    """W2.5 复审 B2:rule_sources 只作失配诊断记录,不参与指纹。"""
    game = tmp_path / "game"
    game.mkdir()
    snap = tmp_path / "s.json"
    snap.write_text("{}", encoding="utf-8")
    r1 = tmp_path / "r1.yaml"
    r1.write_text("rules: []\n", encoding="utf-8")
    guard = issue_review(
        game_root=game, snapshot_paths={"v": snap},
        rule_sources=[r1], modpack_swap=False,
    )
    assert guard["rule_sources"] == [str(r1)]
    # 同内容异名来源:指纹一致(诊断键不同但指纹不受路径影响)
    r2 = tmp_path / "r2.yaml"
    r2.write_text("rules: []\n", encoding="utf-8")
    guard2 = issue_review(
        game_root=game, snapshot_paths={"v": snap},
        rule_sources=[r2], modpack_swap=False,
    )
    assert guard2["rules"] == guard["rules"]


def test_fingerprint_excludes_runtime_fields(built_plan_layout):
    """mark_executed 改写运行结果字段后指纹不变(运行记录≠动作定义)。"""
    plan = built_plan_layout.plan
    fp1 = plan_fingerprint(plan)
    plan.mark_executed({"copied": 1})
    assert plan_fingerprint(plan) == fp1


def test_rescan_invalidates_review(built_plan_layout):
    """计划 JSON 不变+重扫(快照文件内容变)→ review 守卫阻断(spec 验收用例)。"""
    lay = built_plan_layout
    blockers = validate_review(
        lay.plan, game_root=lay.game,
        snapshot_paths=lay.snapshot_paths, rule_sources=lay.rule_sources,
    )
    assert blockers == []
    (lay.dst_dir / "options.txt").write_text("fps:30\n", encoding="utf-8")
    scan_version(lay.game, "dst", lay.data / "snapshots")  # 重扫落盘
    codes = [
        b.code
        for b in validate_review(
            lay.plan, game_root=lay.game,
            snapshot_paths=lay.snapshot_paths, rule_sources=lay.rule_sources,
        )
    ]
    assert "snapshot_changed" in codes


def test_validate_review_guard_codes(built_plan_layout):
    """守卫码齐备:review_missing / instance_mismatch / snapshot_changed / rules_changed。"""
    lay = built_plan_layout
    kwargs = dict(game_root=lay.game,
                  snapshot_paths=lay.snapshot_paths, rule_sources=lay.rule_sources)
    assert [b.code for b in validate_review(lay.plan, **kwargs)] == []
    original = lay.plan.review
    assert original is not None
    # ① 旧 plan(无守卫)→ review_missing
    lay.plan.review = None
    assert [b.code for b in validate_review(lay.plan, **kwargs)] == ["review_missing"]
    lay.plan.review = original
    # ② 实例漂移(game_root 与签发时不一致)
    codes = [b.code for b in validate_review(lay.plan, game_root=lay.game / "moved",
                                             snapshot_paths=lay.snapshot_paths,
                                             rule_sources=lay.rule_sources)]
    assert codes == ["instance_mismatch"]
    # ③ 规则来源变化(签发时不存在 → 出现即指纹变)
    (lay.data / "rules.yaml").write_text("rules: []\n", encoding="utf-8")
    codes = [b.code for b in validate_review(lay.plan, **kwargs)]
    assert codes == ["rules_changed"]
    # ④ 快照文件内容变化(不重扫,直接篡改快照文件)→ snapshot_changed
    (lay.data / "rules.yaml").unlink()
    (lay.snapshot_paths["dst"]).write_text("{}", encoding="utf-8")
    codes = [b.code for b in validate_review(lay.plan, **kwargs)]
    assert codes == ["snapshot_changed"]


def test_target_state_changed_blocks(built_plan_layout):
    """审阅后目标被改(未重扫)→ check_action_states 阻断;完整性校验不能替代审阅状态校验。"""
    lay = built_plan_layout
    assert check_action_states(
        lay.plan.actions, lay.src_snap, lay.dst_snap, lay.src_dir, lay.dst_dir
    ) == []
    (lay.dst_dir / "options.txt").write_text("tampered\n", encoding="utf-8")
    codes = [
        b.code
        for b in check_action_states(
            lay.plan.actions, lay.src_snap, lay.dst_snap, lay.src_dir, lay.dst_dir
        )
    ]
    assert "target_state_changed" in codes


def test_target_created_after_review_blocks(built_plan_layout):
    """审阅时目标不存在、执行前出现→阻断(「不存在」是被记录状态)。"""
    lay = built_plan_layout
    rel = "mods/extra.jar"  # 夹具契约:dst 缺失的 COPY 动作路径(mod_added)
    assert rel not in {e.path for e in lay.dst_snap.files}
    (lay.dst_dir / rel).write_bytes(b"x" * 16)
    codes = [
        b.code
        for b in check_action_states(
            lay.plan.actions, lay.src_snap, lay.dst_snap, lay.src_dir, lay.dst_dir
        )
    ]
    assert "target_state_changed" in codes


def test_weak_entry_size_only_change_blocks(built_plan_layout):
    """size 代理条目(size+mtime 弱检)在其范围内变化→阻断;同 size 同 mtime 的内容替换不承诺发现。"""
    lay = built_plan_layout
    rel = "Distant_Horizons_server_data/lod.sqlite"
    dst_entry = next(e for e in lay.dst_snap.files if e.path == rel)
    assert dst_entry.md5 is None  # 夹具契约:bulk 条目走 size 代理(弱检范围)
    # 目标侧重写(size/mtime 越弱检范围)→ target 阻断
    (lay.dst_dir / rel).write_bytes(b"\x01" * 8)
    codes = [
        b.code
        for b in check_action_states(
            lay.plan.actions, lay.src_snap, lay.dst_snap, lay.src_dir, lay.dst_dir
        )
    ]
    assert "target_state_changed" in codes
    # 源侧同类变化 → source 阻断
    (lay.src_dir / rel).write_bytes(b"\x01" * 24)
    codes2 = [
        b.code
        for b in check_action_states(
            lay.plan.actions, lay.src_snap, lay.dst_snap, lay.src_dir, lay.dst_dir
        )
    ]
    assert "source_state_changed" in codes2


def test_plan_review_roundtrip(built_plan_layout, tmp_path):
    """review 随 plan 持久化往返(save payload 含 "review";旧 plan 缺省 None)。"""
    lay = built_plan_layout
    p = tmp_path / "rt.plan.json"
    lay.plan.save(p)
    loaded = MigrationPlan.load(p)
    assert loaded.review == lay.plan.review
    # 旧 plan(无 review 键)加载 → None(渐进采用)
    doc = json.loads(p.read_text(encoding="utf-8"))
    del doc["review"]
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert MigrationPlan.load(p).review is None
