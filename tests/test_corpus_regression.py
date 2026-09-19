"""服务端场景回归语料对拍(2026-09 六轮真实生产服实测)。

夹具为脱敏快照(tests/fixtures/server_corpus/),六桶期望值在真实语料上实测锚定。
防回归目标:服务端规则组(F1/F5)、mods 桶三态分桶(F4 现状)、内容哈希判等。
20260914/0914b/0919 三轮锚定值为复放语义(纯默认规则,无活体目录);
与采集报告数字的差异=孤儿层与 properties 语义比较两个活体效应,见 spec §0。
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
    "20260914": ("r4_pre.json", "r4_post.json",
                 {"to_migrate": 12, "candidate": 1, "mods": 117,
                  "only_in_dst": 0, "identical": 737, "never": 41}),
    "20260914b": ("r5_pre.json", "r5_post.json",
                  {"to_migrate": 0, "candidate": 0, "mods": 112,
                   "only_in_dst": 0, "identical": 750, "never": 47}),  # T4 后 hs_err/replay 3 件 only_in_dst→never
    "20260919": ("r6_pre.json", "r6_post.json",
                 {"to_migrate": 11, "candidate": 16, "mods": 120,
                  "only_in_dst": 8, "identical": 1126, "never": 55}),  # T4 后 candidate 19→16
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


def test_corpus_0914b_rebuilt_golden() -> None:
    """F17 黄金对: 同名重建 jar(enigmaticlegacyplus, size 7233263→7233336)必须检出。"""
    report, _, _ = _diff_round("20260914b")
    notes = {i.path: i.note for i in report.mods}
    assert notes["mods/[神秘遗物+] enigmaticlegacyplus-1.21.1-1.1.1.jar"] == "rebuilt"


def test_corpus_0912_rebuilt_second_instance() -> None:
    """F17 二次实例: 二轮语料 ScorchedGuns-1.5.jar 同名重建(18972536→19006006)当年漏检。"""
    report, _, _ = _diff_round("20260912")
    assert {i.path: i.note for i in report.mods}["mods/ScorchedGuns-1.5.jar"] == "rebuilt"


def _mods_jar_paths(snap: Snapshot) -> set[str]:
    return {f.path for f in snap.files if f.path.startswith("mods/") and f.path.endswith(".jar")}


def test_corpus_0914_filename_pairs_five_upgrades() -> None:
    """F14 黄金对: 换装段 5 对升级按文件名配对(source=filename,复放语义)。"""
    d = FIXTURES / "20260914"
    src = Snapshot.load(d / "r4_pre.json")
    dst = Snapshot.load(d / "r4_post.json")
    from migration.moddb import pair_mods_by_filename

    pairs = pair_mods_by_filename(sorted(_mods_jar_paths(src) - _mods_jar_paths(dst)),
                                  sorted(_mods_jar_paths(dst) - _mods_jar_paths(src)))
    assert {p.modid for p in pairs} == {
        "waystones-neoforge", "raritycore", "dragonsurvival-all",
        "alexscaves-up", "logisticsnetworks"}
    assert all(p.kind == "upgrade" and p.source == "filename" for p in pairs)


def test_corpus_0919_filename_pairs_six_upgrades() -> None:
    """F19 混合变更: 6 对升级(含 cobblestone 无前缀→有前缀)配对,4 删除件+2 新增件不成对。"""
    d = FIXTURES / "20260919"
    src = Snapshot.load(d / "r6_pre.json")
    dst = Snapshot.load(d / "r6_post.json")
    from migration.moddb import pair_mods_by_filename

    pairs = pair_mods_by_filename(sorted(_mods_jar_paths(src) - _mods_jar_paths(dst)),
                                  sorted(_mods_jar_paths(dst) - _mods_jar_paths(src)))
    assert {p.modid for p in pairs} == {
        "cobblestone-generator-neoforge", "immersive-melodies-neoforge", "alexscaves-up",
        "citadel-up", "fzzy-config-neoforge", "logisticsnetworks"}
    assert all(p.kind == "upgrade" and p.source == "filename" for p in pairs)


def test_corpus_0919_crash_dumps_in_never() -> None:
    """F18: 崩溃残留 hs_err×2+replay×1 归 never(此前落 candidate/only_in_dst)。"""
    report, _, _ = _diff_round("20260919")
    nev = {i.path for i in report.never}
    assert {"hs_err_pid42364.log", "hs_err_pid60956.log", "replay_pid42364.log"} <= nev


def test_corpus_0914_idle_heartbeat_all_world() -> None:
    """F15 空转对: 12.2h 零玩家心跳演化,to_migrate 除 server.properties 外全为 world 数据。"""
    src = Snapshot.load(FIXTURES / "20260913" / "r3_post.json")
    dst = Snapshot.load(FIXTURES / "20260914" / "r4_pre.json")
    rs, errs = build_ruleset(["a", "b"], mcmig_dir=Path("__nonexistent__"),
                             exclude=(), include=(), rule_files=())
    assert errs == []
    report = Differ(src.files, dst.files, Classifier(rs)).diff()
    assert len(report.to_migrate) == 16  # 15 world 心跳 + server.properties(复放无活体语义层)
    assert {i.path for i in report.to_migrate if not i.path.startswith("world/")} == \
        {"server.properties"}
    assert [i.path for i in report.candidate] == ["config/alltheleaks.json"]
    # 空转段 mod_pairs=0 锚定:两侧 mods 集合差均为空(12.2h 无 mod 变化),
    # 文件名配对对空差集必产 0 对 —— 防未来归一化规则过度碰撞出伪对
    from migration.moddb import pair_mods_by_filename

    src_only = _mods_jar_paths(src) - _mods_jar_paths(dst)
    dst_only = _mods_jar_paths(dst) - _mods_jar_paths(src)
    assert src_only == set() and dst_only == set()
    assert pair_mods_by_filename(sorted(src_only), sorted(dst_only)) == []
