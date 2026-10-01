"""synth_v2 夹具锚定:行为矩阵第 3/4/5 行(spec §3.2/§4)——v1 哨兵由十轮语料回归承担。"""
import json
import shutil
from pathlib import Path

from migration.pipeline import DiffOutcome, run_diff

FIX = Path(__file__).parent / "fixtures" / "server_corpus" / "synth_v2"


def _run(tmp_path: Path, src: str, dst: str) -> DiffOutcome:
    """夹具复放:拷入 tmp/.mcmig/snapshots 后走 run_diff(game_root=None 直取布局)。"""
    snaps = tmp_path / ".mcmig" / "snapshots"
    snaps.mkdir(parents=True)
    for n in (src, dst):
        shutil.copy(FIX / f"{n}.snapshot.json", snaps / f"{n}.snapshot.json")
    return run_diff(tmp_path, src=src, dst=dst, mcmig_dir=tmp_path / ".mcmig")


def test_junction_pair_recovered_via_registry(tmp_path: Path) -> None:
    """黄金锚①:同根不同刻(junction 影子根)+双侧 v2 → foo 经 registry 配对(修 F14)。"""
    out = _run(tmp_path, "junction_pre", "junction_post")
    assert len(out.mod_pairs) == 1
    p = out.mod_pairs[0]
    assert (p.modid, p.kind, p.source) == ("foo", "upgrade", "registry")
    assert p.src_files == ["mods/alpha-mod.jar"] and p.dst_files == ["mods/beta-mod.jar"]
    assert (p.src_version, p.dst_version) == ("1.0", "2.0")


def test_replay_orphan_survives_unreachable(tmp_path: Path) -> None:
    """黄金锚②:双侧 v2 不可达 → config/bar.toml 落 never(孤儿),非 candidate(修痛点 1)。"""
    out = _run(tmp_path, "replay_orphan_src", "replay_orphan_dst")
    assert any(i.path == "config/bar.toml" for i in out.report.never)
    assert not any(i.path == "config/bar.toml" for i in out.report.candidate)


def test_frozen_semantics_notice_and_no_semantics_note(tmp_path: Path) -> None:
    """黄金锚③:嵌入+不可达 → 新提示行出现;md5 异无 semantics note(字节比较,§7 妥协 4)。"""
    out = _run(tmp_path, "frozen_semantics_src", "frozen_semantics_dst")
    assert any("已使用快照内嵌 mod 名册" in n for n in out.notices)
    assert not any("语义等价" in i.note for bucket in
                   ("to_migrate", "candidate") for i in getattr(out.report, bucket))


def test_mixed_v1v2_keeps_v1_path_byte_identical(tmp_path: Path) -> None:
    """混合 v1/v2:不可达 → ctx=None 走老降级行,不出现新嵌入行(Review Focus 2)。"""
    out = _run(tmp_path, "mx_src", "mx_dst")
    assert any("mods 扫描不可用(game_root 不可达)" in n for n in out.notices)
    assert not any("内嵌 mod 名册" in n for n in out.notices)


def test_junction_same_time_frozen_no_identity_notice(tmp_path: Path) -> None:
    """终审修复锚:同刻 junction 双侧 v2 → 无「注册表配对不可用」身份通告,配对存活。

    矛盾场景本体:junction_pre/post 拷贝后双侧 scanned_at 改同值(名册仍不同
    foo 1.0→2.0、resolved_root 仍同指 shadow)——修复前回退臂误发 junction 同体
    降级文案,与嵌入名册提示行自相矛盾且 mod_pairs 恰有 registry 对;修复后
    回退臂随冻结闸门收敛(live 同刻主判不触发:两份不同文件;佐证臂不触发:
    ctx 非 None,正是冻结通道本体)。
    """
    snaps = tmp_path / ".mcmig" / "snapshots"
    snaps.mkdir(parents=True)
    for n in ("junction_pre", "junction_post"):
        data = json.loads((FIX / f"{n}.snapshot.json").read_text(encoding="utf-8"))
        data["scanned_at"] = "2026-10-01T10:00:00+08:00"  # 双侧同刻,名册/文件保持原差异
        (snaps / f"{n}.snapshot.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    out = run_diff(tmp_path, src="junction_pre", dst="junction_post",
                   mcmig_dir=tmp_path / ".mcmig")
    # 三臂文案(自比对/junction 同体/疑似自比对)共享「注册表配对与语义复核不可用」
    # 片段——一条断言挡住全部误发路径
    assert not any("注册表配对与语义复核不可用" in n for n in out.notices)
    # registry 配对存活:alpha/beta 家族异构,filename 兜底不可能,唯一来源是嵌入名册
    assert len(out.mod_pairs) == 1
    assert (out.mod_pairs[0].modid, out.mod_pairs[0].kind, out.mod_pairs[0].source) \
        == ("foo", "upgrade", "registry")


def test_frozen_channel_client_only_host_reverse_lookup(tmp_path: Path) -> None:
    """client_only 冻结锚(spec §5.3「client_only 存活」):双侧 v2 不可达 + 名册
    anima(embedded_in=宿主,modid 取自 data/client_mods.yaml 实存清单) →
    F32 宿主反查命中 mods/host-mod.jar。

    宿主 jar 家族键 host-mod 不在 client 清单——命中只能来自 modid 通道
    (match_client_only_paths 的 ctx 反查),即冻结通道 ctx 确实存活;
    若通道哑掉(ctx=None)家族/反查双通道皆空,client_only_paths 必为空集。
    """
    snaps = tmp_path / ".mcmig" / "snapshots"
    snaps.mkdir(parents=True)

    def _write(name: str, scanned_at: str, resolved: str,
               files: list[dict], mods: list[dict]) -> None:
        payload = {
            "tool_version": "0.11.0", "snapshot_format": 2, "version": name,
            "game_root": "C:\\fixture\\sanitized", "scanned_at": scanned_at,
            "resolved_root": resolved, "world_dirs": [], "hash_mode": "tiered",
            "file_count": len(files), "files": files, "mods": mods,
        }
        (snaps / f"{name}.snapshot.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    _write(
        "co_src", "2026-10-01T10:00:00+08:00",
        "C:\\fixture\\sanitized\\versions\\co_src",
        files=[{"path": "mods/host-mod.jar", "size": 200, "md5": None, "mtime": 900}],
        mods=[{"modid": "anima", "version": "1.0", "jar_filename": "anima-1.0.jar",
               "neoforge_range": None, "embedded_in": "host-mod.jar"}],
    )
    _write(
        "co_dst", "2026-10-01T11:00:00+08:00",
        "C:\\fixture\\sanitized\\versions\\co_dst",
        files=[{"path": "mods/servermod-1.0.jar", "size": 90, "md5": None,
                "mtime": 950}],
        mods=[{"modid": "servermod", "version": "1.0",
               "jar_filename": "servermod-1.0.jar", "neoforge_range": None,
               "embedded_in": None}],
    )
    out = run_diff(tmp_path, src="co_src", dst="co_dst",
                   mcmig_dir=tmp_path / ".mcmig")
    # F32 宿主反查:anima(客户端清单 modid)内嵌于 host-mod.jar → 标注宿主实体;
    # servermod 不在清单,host-mod 家族键也不在清单——恰一条且仅来自 modid 通道
    assert out.client_only_paths == {"mods/host-mod.jar"}
    assert any("已知客户端 mod" in n and "mods/host-mod.jar" in n
               for n in out.notices)


def test_v2_reachable_silent_no_extra_notice(tmp_path: Path, monkeypatch) -> None:
    """嵌入+目录可达:静默走冻结通道,无新增提示(输出无扰动,spec §3.2);
    且 mod_pairs 恰 1 条 source=registry——行 3「mods 来源=嵌入」的正向证词
    (spec §5.3):a-mod/b-mod 家族异构,filename 兜底不可能配对,唯一来源
    是被消费的嵌入名册 registry 通道。"""
    # 活体版目录 + 双侧 v2 快照(手写 game_root 指向 tmp):重扫会覆盖快照——改为
    # 直接构造:tmp 下建 versions/a、versions/b 各放 mods jar,scan 两侧产 v2 快照,
    # 再 run_diff → notices 不含「内嵌」也不含「不可达」
    from migration.pipeline import scan_version
    from tests.conftest import write_mod_jar

    for ver, mod_version, jar in (("a", "1.0", "a-mod.jar"), ("b", "2.0", "b-mod.jar")):
        vdir = tmp_path / "versions" / ver
        (vdir / "mods").mkdir(parents=True)
        write_mod_jar(vdir / "mods" / jar, "foo", mod_version)
    snaps = tmp_path / ".mcmig" / "snapshots"
    scan_version(tmp_path, "a", snaps)
    scan_version(tmp_path, "b", snaps)
    out = run_diff(tmp_path, src="a", dst="b", game_root=tmp_path)
    assert not any("内嵌" in n for n in out.notices)
    assert not any("不可达" in n for n in out.notices)
    # 冻结通道确被消费(终审 2b):同名 modid foo 1.0→2.0 经 registry 配成 upgrade
    assert len(out.mod_pairs) == 1
    p = out.mod_pairs[0]
    assert (p.modid, p.kind, p.source) == ("foo", "upgrade", "registry")
    assert p.src_files == ["mods/a-mod.jar"] and p.dst_files == ["mods/b-mod.jar"]
