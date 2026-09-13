"""服务端场景回归语料对拍(2026-09 两轮真实生产服实测)。

夹具为脱敏快照(tests/fixtures/server_corpus/),六桶期望值在真实语料上实测锚定。
防回归目标:服务端规则组(F1/F5)、mods 桶三态分桶(F4 现状)、内容哈希判等。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from migration.classifier import Classifier
from migration.cli import build_ruleset
from migration.differ import Differ, DiffReport
from migration.snapshot import Snapshot

FIXTURES = Path(__file__).parent / "fixtures" / "server_corpus"

ROUNDS = {
    "20260908": ("snapshot_before.json", "snapshot_after.json",
                 {"to_migrate": 257, "candidate": 5, "mods": 130,
                  "only_in_dst": 1, "identical": 636, "never": 143}),
    "20260912": ("snapshot_9_8_player.json", "snapshot_9_11_fresh.json",
                 {"to_migrate": 550, "candidate": 7, "mods": 122,
                  "only_in_dst": 0, "identical": 631, "never": 149}),
}


def _diff_round(round_id: str) -> tuple[DiffReport, Snapshot, Snapshot]:
    """加载夹具快照并用内置默认规则跑 diff,返回 (报告, src, dst)。"""
    src_name, dst_name, _ = ROUNDS[round_id]
    d = FIXTURES / round_id
    src = Snapshot.load(d / src_name)
    dst = Snapshot.load(d / dst_name)
    rs, errs = build_ruleset(
        ["fixture_src", "fixture_dst"],
        mcmig_dir=Path("__nonexistent__"),  # 不存在 → 无用户规则,纯默认层
        exclude=(), include=(), rule_files=(),
    )
    assert errs == []
    return Differ(src.files, dst.files, Classifier(rs)).diff(), src, dst


@pytest.mark.parametrize("round_id", sorted(ROUNDS))
def test_corpus_six_buckets(round_id: str) -> None:
    """六桶计数与真实语料锚定值一致(规则语义变化的哨兵)。"""
    _, _, _ = ROUNDS[round_id]
    report, _, _ = _diff_round(round_id)
    expected = ROUNDS[round_id][2]
    actual = {k: len(getattr(report, k)) for k in expected}
    assert actual == expected, f"{round_id} 六桶漂移: {actual} != {expected}"


def test_corpus_0908_server_assets_and_backup_dir() -> None:
    """一轮:F1 修复可见 — world/server.properties 进 to_migrate,备份目录进 never。"""
    report, _, _ = _diff_round("20260908")
    tm = {i.path: i for i in report.to_migrate}
    assert "server.properties" in tm
    assert any(p.startswith("world/") for p in tm)
    nv = {i.path: i for i in report.never}
    backup = [p for p in nv if p.startswith("mods_9.4_")]
    assert len(backup) == 118  # F5: 运维回滚备份目录整目录不迁
    assert all(i.note == "never" for i in nv.values() if i.path in backup)
    # 唯一的 target_only = create_power_loader 服务端新 mod 的 config(真目标自带)
    assert [i.path for i in report.only_in_dst] == ["config/create_power_loader-server.toml"]


def test_corpus_0912_world_and_upgrade_pairs() -> None:
    """二轮:含玩家数据的 world 进 to_migrate;7 对升级 jar 以 to_add/target_only 成对裸列(F4 现状锚定)。"""
    report, _, _ = _diff_round("20260912")
    tm = {i.path: i for i in report.to_migrate}
    assert "world/level.dat" in tm  # 删档重建 → modified
    assert tm["server.properties"].note == "modified"  # 含 seed 的手改
    mods = {i.path: i.note for i in report.mods}
    assert mods["mods/[传送石碑／指路石] waystones-neoforge-1.21.1-21.1.42.jar"] == "to_add"
    assert mods["mods/[传送石碑／指路石] waystones-neoforge-1.21.1-21.1.44.jar"] == "target_only"
    # 同 mod 跨版本成对出现(升级未配对是 F4 已知现状,此处锚定防意外变化)
    assert mods["mods/logisticsnetworks-1.21.1-1.15.0.jar"] == "to_add"
    assert mods["mods/logisticsnetworks-1.21.1-1.16.0.jar"] == "target_only"


def test_corpus_0912_orphan_config_deleted_and_hand_edits() -> None:
    """二轮:被删 mod 的孤儿 config 以 candidate/new 可见(F2 删除态);同尺寸异内容靠内容哈希判等。"""
    report, _, _ = _diff_round("20260912")
    cd = {i.path: i for i in report.candidate}
    assert cd["config/create_pillagers_arise-common.toml"].note == "new"
    assert cd["config/modern_glass_doors_mod-common.toml"].note == "new"
    # 同尺寸不同内容(6533→6533)必须落 modified 而非 identical —— 内容哈希哨兵
    assert cd["config/alexscaves-general.toml"].note == "modified"
    assert cd["config/infernalmobs.cfg"].note == "modified"


def test_corpus_r3_evolution_fully_explained() -> None:
    """F10(三轮): 同包 20.4h 演化 — 世界/服务器资产入 to_migrate,91 个演化文件全量可解释。"""
    src = Snapshot.load(FIXTURES / "20260912" / "snapshot_9_11_fresh.json")
    dst = Snapshot.load(FIXTURES / "20260913" / "r3_live.json")
    rs, errs = build_ruleset(["evo_src", "evo_dst"], mcmig_dir=Path("__nonexistent__"),
                             exclude=(), include=(), rule_files=())
    assert errs == []
    report = Differ(src.files, dst.files, Classifier(rs)).diff()
    actual = {k: len(getattr(report, k))
              for k in ["to_migrate", "candidate", "mods", "only_in_dst", "identical", "never"]}
    assert actual == {"to_migrate": 22, "candidate": 1, "mods": 112,
                      "only_in_dst": 91, "identical": 636, "never": 37}
    tm = {i.path: i for i in report.to_migrate}
    assert "server.properties" in tm  # E1/E2 真实改动 + F12 噪声成分(无 ctx 时字节判定)
    assert any(p.startswith("world/") for p in tm)  # 世界演化数据
    assert [i.path for i in report.candidate] == ["user_jvm_args.txt"]  # 唯一 unknown 漂移


def test_corpus_r3_pure_config_drift_golden() -> None:
    """F11(三轮黄金对): agent 只改 2 个配置 ⇒ diff 恰好 1+1,零误报零漏报。"""
    src = Snapshot.load(FIXTURES / "20260913" / "r3_live.json")
    dst = Snapshot.load(FIXTURES / "20260913" / "r3_post.json")
    rs, errs = build_ruleset(["drift_src", "drift_dst"], mcmig_dir=Path("__nonexistent__"),
                             exclude=(), include=(), rule_files=())
    assert errs == []
    report = Differ(src.files, dst.files, Classifier(rs)).diff()
    actual = {k: len(getattr(report, k))
              for k in ["to_migrate", "candidate", "mods", "only_in_dst", "identical", "never"]}
    assert actual == {"to_migrate": 1, "candidate": 1, "mods": 112,
                      "only_in_dst": 0, "identical": 748, "never": 37}
    assert [i.path for i in report.to_migrate] == ["server.properties"]
    assert [i.path for i in report.candidate] == ["config/alltheleaks.json"]
