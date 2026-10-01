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

# F28(批次E)后 `**/*.bak` 归默认 never:identical/candidate/only_in_dst 相应流入 never
ROUNDS = {
    "20260908": ("snapshot_before.json", "snapshot_after.json",
                 {"to_migrate": 257, "candidate": 3, "mods": 130,
                  "only_in_dst": 1, "identical": 592, "never": 189}),
    "20260912": ("snapshot_9_8_player.json", "snapshot_9_11_fresh.json",
                 {"to_migrate": 550, "candidate": 6, "mods": 122,
                  "only_in_dst": 0, "identical": 588, "never": 193}),
    "20260914": ("r4_pre.json", "r4_post.json",
                 {"to_migrate": 12, "candidate": 1, "mods": 117,
                  "only_in_dst": 0, "identical": 694, "never": 84}),
    "20260914b": ("r5_pre.json", "r5_post.json",
                  {"to_migrate": 0, "candidate": 0, "mods": 112,
                   "only_in_dst": 0, "identical": 707, "never": 90}),  # T4 后 hs_err/replay 3 件 only_in_dst→never
    "20260919": ("r6_pre.json", "r6_post.json",
                 {"to_migrate": 11, "candidate": 14, "mods": 120,
                  "only_in_dst": 2, "identical": 1085, "never": 104}),  # T4 后 candidate 19→16;F28 后 6 件 dst 侧 .bak only_in_dst→never
    "20260930": ("r12_pre.json", "r12_post.json",
                 {"to_migrate": 0, "candidate": 0, "mods": 23,
                  "only_in_dst": 0, "identical": 0, "never": 0}),
    "20261001": ("r13_pre.json", "r13_mid.json",
                 {"to_migrate": 6, "candidate": 0, "mods": 2,
                  "only_in_dst": 10, "identical": 0, "never": 1}),
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
                      "only_in_dst": 91, "identical": 593, "never": 80}  # F28:43 件 .bak identical→never
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
                      "only_in_dst": 0, "identical": 705, "never": 80}  # F28:43 件 .bak identical→never
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


def test_corpus_20260920_four_pairs_three_upgrade_one_rebuilt() -> None:
    """F20 黄金对:七轮换装 4 件按指纹合成 — 3×upgrade + 1×rebuilt(compat -Patch)。

    七轮原始快照未随交付包,夹具以《旧件指纹.md》md5/size 真值合成(mods-only);
    size 差 250B 的 compat 变体是 F20-3 盲区的最小复现。
    """
    from migration.moddb import pair_mods_by_filename

    d = FIXTURES / "20260920"
    src = Snapshot.load(d / "r7_pre.json")
    dst = Snapshot.load(d / "r7_post.json")
    pairs = pair_mods_by_filename(sorted(_mods_jar_paths(src) - _mods_jar_paths(dst)),
                                  sorted(_mods_jar_paths(dst) - _mods_jar_paths(src)))
    by_modid = {p.modid: p for p in pairs}
    assert len(pairs) == 4
    # 3 个升级对(真实升级,版本签名不同)
    for modid, (sv, dv) in {
        "kaleidoscopecookery-neoforge": ("1.4.1-mc1.21.1", "1.5.0-mc1.21.1"),
        "letsdo-beachparty-neoforge": ("2.1.4", "2.1.5"),
        "letsdo-wildernature-neoforge": ("1.1.5", "1.1.6"),
    }.items():
        p = by_modid[modid]
        assert (p.kind, p.source) == ("upgrade", "filename")
        assert (p.src_version, p.dst_version) == (sv, dv)
    # compat 同版变体 → rebuilt(F20-3 核心)
    p = by_modid["kaleidoscope-compat-neoforge"]
    assert (p.kind, p.source) == ("rebuilt", "filename")
    assert p.src_files == ["mods/[森罗物语：兼容] kaleidoscope_compat-2.9.7-neoforge+mc1.21.1.jar"]
    assert p.dst_files == ["mods/[森罗物语：兼容] kaleidoscope_compat-2.9.7-neoforge+mc1.21.1-Patch.jar"]
    assert p.src_version == p.dst_version == "2.9.7-mc1.21.1"


def test_corpus_20260920_bucket_notes_unchanged_by_pairing() -> None:
    """配对纯标注不变:compat 两条目在 mods 桶内仍是 to_add/target_only(spec §7 妥协)。"""
    d = FIXTURES / "20260920"
    src = Snapshot.load(d / "r7_pre.json")
    dst = Snapshot.load(d / "r7_post.json")
    rs, errs = build_ruleset(["r7_src", "r7_dst"], mcmig_dir=Path("__nonexistent__"),
                             exclude=(), include=(), rule_files=())
    assert errs == []
    report = Differ(src.files, dst.files, Classifier(rs)).diff()
    notes = {i.path: i.note for i in report.mods}
    assert notes["mods/[森罗物语：兼容] kaleidoscope_compat-2.9.7-neoforge+mc1.21.1.jar"] == "to_add"
    assert notes["mods/[森罗物语：兼容] kaleidoscope_compat-2.9.7-neoforge+mc1.21.1-Patch.jar"] == "target_only"
    assert notes["mods/create-6.0.10-neoforge+mc1.21.1.jar"] == "shared"
    assert len(report.mods) == 9  # 4 to_add + 4 target_only + 1 shared


def test_corpus_bak_files_in_never_and_judgement_intact() -> None:
    """F28:.bak 全量归 never;判定法父提升不受影响(planner 读快照全集,实证于 0912 轮)。"""
    from migration.planner import Planner, find_bak_siblings
    d = FIXTURES / "20260912"
    src = Snapshot.load(d / "snapshot_9_8_player.json")
    dst = Snapshot.load(d / "snapshot_9_11_fresh.json")
    rs, errs = build_ruleset(["a", "b"], mcmig_dir=Path("__nonexistent__"),
                             exclude=(), include=(), rule_files=())
    assert errs == []
    report = Differ(src.files, dst.files, Classifier(rs)).diff()
    # ① .bak 全量进 never,其余五桶零残留
    assert [i for i in report.never if i.path.endswith(".bak")]
    for b in ("to_migrate", "candidate", "only_in_dst", "identical", "mods"):
        assert not [i for i in getattr(report, b) if i.path.endswith(".bak")]
    # ② 判定法父提升保持:infernalmobs.cfg(candidate 且 src 有 .bak 兄弟,勘察实证唯一件)
    #    仍被 planner 提升为 config_modified/copy(find_bak_siblings 读 src_index 快照全集)
    src_index = {e.path: e for e in src.files}
    assert find_bak_siblings("config/infernalmobs.cfg", set(src_index))
    plan = Planner(report, src_index).plan()
    act = {a.path: a for a in plan.actions}
    assert (act["config/infernalmobs.cfg"].origin.value,
            act["config/infernalmobs.cfg"].behavior.value) == ("config_modified", "copy")
    # ③ .bak 本体在 plan 中 skip/never(0.7.0 为 bak_file/copy 或 identical/skip)
    baks = [a for a in plan.actions if a.path.endswith(".bak")]
    assert baks and all(
        a.behavior.value == "skip" and a.origin.value == "never" for a in baks)


def test_corpus_20260921_three_tiers_and_bucket_rebuilt() -> None:
    """F21/F22 黄金对:八轮换装按 diff JSON 真值合成 —
    lootr 一级 + 酒馆三级(版本+尾缀同变)+ field 四级(平台词剥离)+ 灾变桶内 rebuilt。"""
    from migration.moddb import pair_mods_by_filename
    d = FIXTURES / "20260921"
    src = Snapshot.load(d / "r8_pre.json")
    dst = Snapshot.load(d / "r8_post.json")
    pairs = pair_mods_by_filename(sorted(_mods_jar_paths(src) - _mods_jar_paths(dst)),
                                  sorted(_mods_jar_paths(dst) - _mods_jar_paths(src)))
    by = {p.modid: p for p in pairs}
    assert len(pairs) == 3
    assert (by["lootr-neoforge"].kind, by["lootr-neoforge"].source) == \
        ("upgrade", "filename")  # 一级
    assert by["kaleidoscope-world-liquor-neoforge"].kind == "upgrade"  # 三级(F21)
    assert by["kaleidoscope-world-liquor-neoforge"].src_version == "1.1.8-1.21.1"
    assert by["field-emitters"].kind == "upgrade"  # 四级(F22,剥 neoforge 平台词)
    # 桶语义不变:灾变同名重建(size 73375661→73375667)留在 mods 桶标 rebuilt
    rs, errs = build_ruleset(["r8a", "r8b"], mcmig_dir=Path("__nonexistent__"),
                             exclude=(), include=(), rule_files=())
    assert errs == []
    report = Differ(src.files, dst.files, Classifier(rs)).diff()
    notes = {i.path: i.note for i in report.mods}
    assert notes["mods/[灾变] L_Ender's Cataclysm 1.21.1-3.33.jar"] == "rebuilt"
    assert len(report.mods) == 8  # 3 to_add + 3 target_only + 1 rebuilt + 1 shared


def test_corpus_20260923_five_upgrades_and_swap_keeps_pairs() -> None:
    """r9 黄金:5 对纯升级全一级(三/四级零扰动哨兵);swap 分桶后配对仍取快照差集(F23 语料级)。"""
    from migration.moddb import pair_mods_by_filename
    d = FIXTURES / "20260923"
    src = Snapshot.load(d / "r9_pre.json")
    dst = Snapshot.load(d / "r9_post.json")
    src_only = sorted(_mods_jar_paths(src) - _mods_jar_paths(dst))
    dst_only = sorted(_mods_jar_paths(dst) - _mods_jar_paths(src))
    assert len(src_only) == len(dst_only) == 5
    pairs = pair_mods_by_filename(src_only, dst_only)
    assert {p.modid: p.kind for p in pairs} == {
        "kaleidoscopecookery-neoforge": "upgrade",
        "resource-replicator-neoforge": "upgrade",  # 非对称 CJK 前缀仍一级(标签剥离)
        "alltheleaks-neoforge": "upgrade",
        "irons-spellbooks": "upgrade",
        "mekltgt": "upgrade",
    }
    # swap 视角:分桶改变(to_add 归 never),配对输入取快照差集 → 5 对不丢
    rs, errs = build_ruleset(["r9a", "r9b"], mcmig_dir=Path("__nonexistent__"),
                             exclude=(), include=(), rule_files=())
    assert errs == []
    report = Differ(src.files, dst.files, Classifier(rs), modpack_swap=True).diff()
    assert not [i for i in report.mods if i.note == "to_add"]
    assert sum(1 for i in report.never if i.note == "modpack_swap") == 5
    assert sum(1 for i in report.mods if i.note == "target_only") == 5
    assert len(pair_mods_by_filename(src_only, dst_only)) == 5


def test_corpus_20260929_twelve_pairs_renamed_first_show() -> None:
    """r11 黄金对(第十轮语料):12/12 配对 — 11 upgrade + torchmaster renamed 首秀。

    DragonSurvival 为一级配对(-all 两侧同在,spec §0 更正);3 件真移除裸 to_add
    + 4 件净增 target_only;swap 视角 15 旧件归换包排除且配对不丢(F23 哨兵)。
    """
    from migration.moddb import pair_mods_by_filename
    d = FIXTURES / "20260929"
    src = Snapshot.load(d / "r11_pre.json")
    dst = Snapshot.load(d / "r11_post.json")
    src_only = sorted(_mods_jar_paths(src) - _mods_jar_paths(dst))
    dst_only = sorted(_mods_jar_paths(dst) - _mods_jar_paths(src))
    assert (len(src_only), len(dst_only)) == (15, 16)
    pairs = pair_mods_by_filename(src_only, dst_only)
    by = {p.modid: p for p in pairs}
    assert len(pairs) == 12
    assert sum(1 for p in pairs if p.kind == "upgrade") == 11
    # torchmaster:裸名 → [火炬大师] 前缀,同版本同 md5 纯改名 → renamed(而非 upgrade)
    assert by["torchmaster-neoforge"].kind == "renamed"
    assert by["torchmaster-neoforge"].src_version == "1.21.1-21.1.13"
    # DragonSurvival:版本+日期双变但 -all 两侧同在 → 一级(非三级),modid 含 -all
    assert by["dragonsurvival-all"].kind == "upgrade"
    assert by["dragonsurvival-all"].src_version == "1.21.1-v2.0.70-13.09.2026"
    assert by["dragonsurvival-all"].dst_version == "1.21.1-v2.0.71-25.09.2026"
    # 桶语义:3 删 + 4 增(frost_dragon 为 F30 客户端件,T5 在此夹具上再锚 client_only)
    rs, errs = build_ruleset(["a", "b"], mcmig_dir=Path("__nonexistent__"),
                             exclude=(), include=(), rule_files=())
    assert errs == []
    report = Differ(src.files, dst.files, Classifier(rs)).diff()
    notes = {i.path: i.note for i in report.mods}
    for p in ("mods/[机械动力：食物注药] Create-Food-Filling-1.21.1-1.5.1.jar",
              "mods/fzzy_config-0.7.7+1.21+neoforge.jar",
              "mods/tarotcards-2.3.5-neoforge-1.21.1.jar"):
        assert notes[p] == "to_add"
    for p in ("mods/Azotosaurus-freesky-1.4.5.jar",
              "mods/[FTB 区块] ftb-chunks-neoforge-2101.1.22.jar",
              "mods/[FTB 团队] ftb-teams-neoforge-2101.1.11.jar",
              "mods/frost_dragon-1.0.0.jar"):
        assert notes[p] == "target_only"
    # swap 视角:to_add 全归换包排除,配对输入取快照差集 → 12 对不丢
    sw = Differ(src.files, dst.files, Classifier(rs), modpack_swap=True).diff()
    assert not [i for i in sw.mods if i.note == "to_add"]
    assert sum(1 for i in sw.never if i.note == "modpack_swap") == 15
    assert len(pair_mods_by_filename(src_only, dst_only)) == 12


def test_corpus_20260929_frost_dragon_client_only_annotation(tmp_path) -> None:
    """F30①:复放 diff(无活体)下已知客户端件按家族键命中 —
    frost_dragon 行标 client_only + stderr 警示行;其余行不变;--json 不受影响。"""
    import io
    import json

    from rich.console import Console
    from migration.pipeline import run_diff
    from migration.reporter import DiffReporter, ReportOptions
    d = FIXTURES / "20260929"
    # 复放布置:夹具快照按标准命名落到 tmp 的 .mcmig/snapshots(game_root 不传 → 无活体 ctx)
    snaps = tmp_path / ".mcmig" / "snapshots"
    snaps.mkdir(parents=True)
    Snapshot.load(d / "r11_pre.json").save(snaps / "r11-pre.snapshot.json")
    Snapshot.load(d / "r11_post.json").save(snaps / "r11-post.snapshot.json")
    out = run_diff(tmp_path, src="r11-pre", dst="r11-post")
    assert any("客户端" in n and "frost_dragon" in n for n in out.notices)
    buf = io.StringIO()
    reporter = DiffReporter(out.report, src_version="r11-pre", dst_version="r11-post",
                            mod_pairs=out.mod_pairs, client_only_paths=out.client_only_paths)
    reporter.render(ReportOptions(mods_only=True), console=Console(file=buf, width=200))
    text = buf.getvalue()
    assert "frost_dragon-1.0.0.jar" in text and "client_only" in text
    # 共享件不受污染:抽一条 shared 行不含 client_only
    assert "BlockBox-1.21.1-0.1.3.jar" in text
    # json 无 client_only 字段(结构不变)
    payload = json.loads(reporter.to_json())
    assert "client_only" not in payload and set(payload) == {"src", "dst", "summary", "buckets", "mod_pairs"}


def test_corpus_20260930_six_pairs_kinds_and_versions() -> None:
    """r12 黄金对(第十一轮语料):6/6 配对 — renamed×3(前缀剥离)+ upgrade×3。

    create_connected 双侧版本全同(1.3.3-mc1.21.1)是 F33 的形态基础;
    content_differs 注记在 T2 落地后由 T2 的测试在此夹具上追加。
    """
    from migration.moddb import pair_mods_by_filename
    d = FIXTURES / "20260930"
    src = Snapshot.load(d / "r12_pre.json")
    dst = Snapshot.load(d / "r12_post.json")
    pairs = pair_mods_by_filename(sorted(_mods_jar_paths(src) - _mods_jar_paths(dst)),
                                  sorted(_mods_jar_paths(dst) - _mods_jar_paths(src)))
    by = {p.modid: p for p in pairs}
    assert {m: p.kind for m, p in by.items()} == {
        "create-connected": "renamed",
        "createlazytick-neoforge": "renamed",
        "ftb-chunks-neoforge": "renamed",
        "sophisticatedbackpacks": "upgrade",
        "ftb-library-neoforge": "upgrade",
        "geckolib-neoforge": "upgrade",
    }
    assert by["create-connected"].src_version == by["create-connected"].dst_version == "1.3.3-mc1.21.1"


def test_corpus_20260930_bucket_notes_and_swap() -> None:
    """r12 桶语义:5 真移除 to_add + 12 目标独有(含 damage-engine);swap 11 排除且配对不丢。"""
    from migration.moddb import pair_mods_by_filename
    d = FIXTURES / "20260930"
    src = Snapshot.load(d / "r12_pre.json")
    dst = Snapshot.load(d / "r12_post.json")
    rs, errs = build_ruleset(["a", "b"], mcmig_dir=Path("__nonexistent__"),
                             exclude=(), include=(), rule_files=())
    assert errs == []
    report = Differ(src.files, dst.files, Classifier(rs)).diff()
    notes = {i.path: i.note for i in report.mods}
    for p in ("mods/[潮汐] tide-neoforge-1.21.1-2.1.1.jar",
              "mods/automobility-0.5.0.h+1.21.1-neoforge.jar",
              "mods/create_wrapped-1.0.3-neoforge-1.21.1.jar",
              "mods/fetzisdisplays-neoforge-1.1.0-1.21.jar",
              "mods/xaeroworldmap-neoforge-1.21.1-1.41.2.jar"):
        assert notes[p] == "to_add"
    assert notes["mods/damage-engine-2.1.1-NeoForge-1.21.1.jar"] == "target_only"
    assert sum(1 for n in notes.values() if n == "target_only") == 12
    sw = Differ(src.files, dst.files, Classifier(rs), modpack_swap=True).diff()
    assert sum(1 for i in sw.never if i.note == "modpack_swap") == 11
    assert len(pair_mods_by_filename(sorted(_mods_jar_paths(src) - _mods_jar_paths(dst)),
                                     sorted(_mods_jar_paths(dst) - _mods_jar_paths(src)))) == 6


def test_corpus_20261001_world_switch_and_mods() -> None:
    """r13 切换段(0.9.0 语义即批次G 后不变):world/ 整体 to_migrate,两新目录 dst-only;
    automobility 回插 target_only;无配对(纯新增)。"""
    report, _, _ = _diff_round("20261001")
    tm = {i.path for i in report.to_migrate}
    assert "server.properties" in tm
    assert {p for p in tm if p.startswith("world/")} == {
        "world/level.dat", "world/region/r.0.0.mca", "world/session.lock",
        "world/serverconfig/ftbessentials.snbt", "world/data/scoreboard.dat"}
    od = {i.path for i in report.only_in_dst}
    assert "f1-shanghai/level.dat" in od
    assert "world_backup_20261001/level.dat" in od
    assert len(od) == 10
    notes = {i.path: i.note for i in report.mods}
    assert notes["mods/automobility-0.5.0.h+1.21.1-neoforge.jar"] == "target_only"
    assert notes["mods/create-6.0.10-neoforge+mc1.21.1.jar"] == "shared"


def test_corpus_20260930_renamed_rebuilt_annotation() -> None:
    """F33 语料级:create_connected(剥前缀+差1字节)注记 content_differs;
    LazyTick/FTB 区块(纯改名同尺寸)不注记;升级对不注记;JSON 键条件出现。"""
    from migration.pipeline import compute_mod_pairs

    src = Snapshot.load(FIXTURES / "20260930" / "r12_pre.json")
    dst = Snapshot.load(FIXTURES / "20260930" / "r12_post.json")
    by = {p.modid: p for p in compute_mod_pairs(src, dst, None, None)}
    assert by["create-connected"].content_differs is True
    assert by["createlazytick-neoforge"].content_differs is False
    assert by["ftb-chunks-neoforge"].content_differs is False
    assert by["sophisticatedbackpacks"].content_differs is False
    d = by["create-connected"].to_dict()
    assert d["content_differs"] is True and d["kind"] == "renamed"
    assert "content_differs" not in by["createlazytick-neoforge"].to_dict()


def test_corpus_20260930_damage_engine_client_only_replay(tmp_path) -> None:
    """F32 复放:family 通道命中 damage-engine 宿主 jar — 行标 client_only + 警示行。"""
    from migration.pipeline import run_diff

    d = FIXTURES / "20260930"
    snaps = tmp_path / ".mcmig" / "snapshots"
    snaps.mkdir(parents=True)
    Snapshot.load(d / "r12_pre.json").save(snaps / "r12-pre.snapshot.json")
    Snapshot.load(d / "r12_post.json").save(snaps / "r12-post.snapshot.json")
    out = run_diff(tmp_path, src="r12-pre", dst="r12-post")
    assert any("客户端" in n and "damage-engine" in n for n in out.notices)
    assert "mods/damage-engine-2.1.1-NeoForge-1.21.1.jar" in out.client_only_paths


def test_corpus_20261001_world_dynamic_classification() -> None:
    """F34① 语料级:mid→post 的 f1-shanghai 演化按世界语义落 to_migrate
    (0.9.0 时落 candidate;分叉是有意行为,spec §4)。"""
    d = FIXTURES / "20261001"
    src = Snapshot.load(d / "r13_mid.json")
    dst = Snapshot.load(d / "r13_post.json")
    rs, errs = build_ruleset(["m", "p"], mcmig_dir=Path("__nonexistent__"),
                             exclude=(), include=(), rule_files=(),
                             world_dirs=sorted(set(src.world_dirs) | set(dst.world_dirs)))
    assert errs == []
    report = Differ(src.files, dst.files, Classifier(rs)).diff()
    tm = {i.path: i.note for i in report.to_migrate}
    assert "f1-shanghai/level.dat" in tm and tm["f1-shanghai/level.dat"] == "modified"
    assert "f1-shanghai/raids_overworld.dat" in tm
    assert not [i for i in report.candidate]  # 世界演化不再落 unknown
    # pre→mid 桶分布不变(ROUNDS 锚);dst-only 世界文件仍 only_in_dst


def test_corpus_20261001_world_rename_notice_replay(tmp_path) -> None:
    """F34② 语料级:pre→mid 复放恰一条改名提示(world→world_backup);
    mid→post 无改名段不发。"""
    from migration.pipeline import run_diff

    d = FIXTURES / "20261001"
    snaps = tmp_path / ".mcmig" / "snapshots"
    snaps.mkdir(parents=True)
    for name in ("r13_pre", "r13_mid", "r13_post"):
        # 夹具文件名用下划线,run_diff 版本名按本文件惯例用连字符(r12 复放同),存盘时归一
        Snapshot.load(d / f"{name}.json").save(
            snaps / f"{name.replace('_', '-')}.snapshot.json")
    out1 = run_diff(tmp_path, src="r13-pre", dst="r13-mid")
    rename = [n for n in out1.notices if "疑似世界目录改名" in n]
    assert len(rename) == 1
    assert "world → world_backup_20261001" in rename[0]
    out2 = run_diff(tmp_path, src="r13-mid", dst="r13-post")
    assert not [n for n in out2.notices if "疑似世界目录改名" in n]


def test_corpus_20261001_region_mtime_evolution_replay(tmp_path) -> None:
    """F35 语料级:mid→post 同根复放 — r.-1.-1.mca(同尺寸 mtime 异)落 to_migrate
    note=mtime;r.-1.-2.mca(未触碰)仍 identical;跨根闸门关 → 回 identical。
    并锚 mid→post 最终桶分布(ROUNDS 补行)。"""
    from migration.pipeline import run_diff

    d = FIXTURES / "20261001"
    snaps = tmp_path / ".mcmig" / "snapshots"
    snaps.mkdir(parents=True)
    for name in ("r13_mid", "r13_post"):
        # 夹具文件名用下划线,run_diff 版本名按本文件惯例用连字符(存盘时归一,同上)
        Snapshot.load(d / f"{name}.json").save(
            snaps / f"{name.replace('_', '-')}.snapshot.json")
    out = run_diff(tmp_path, src="r13-mid", dst="r13-post")
    tm = {i.path: i.note for i in out.report.to_migrate}
    assert tm["f1-shanghai/region/r.-1.-1.mca"] == "mtime"
    idn = {i.path: i.note for i in out.report.identical}
    assert idn["f1-shanghai/region/r.-1.-2.mca"] == "size-based"
    assert {k: len(getattr(out.report, k)) for k in
            ("to_migrate", "candidate", "mods", "only_in_dst", "identical", "never")} == {
        "to_migrate": 5, "candidate": 0, "mods": 2,
        "only_in_dst": 3, "identical": 6, "never": 1}
    # 跨根闸门:改 dst 的 resolved_root → mtime 通道关,r.-1.-1 回 identical
    from migration.snapshot import Snapshot as Snap

    dst2 = Snap.load(d / "r13_post.json")
    dst2.resolved_root = "C:\\another\\root"
    dst2.save(snaps / "r13-post.snapshot.json")
    out2 = run_diff(tmp_path, src="r13-mid", dst="r13-post")
    assert not [i for i in out2.report.to_migrate if i.path.endswith("r.-1.-1.mca")]
    assert any(i.path.endswith("r.-1.-1.mca") for i in out2.report.identical)
