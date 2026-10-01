# 批次G 实现计划：世界感知 + 内嵌客户端件收口 + renamed(rebuilt) + mtime 演化通道

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落实 F32-F35 四项语料发现——client_only 内嵌反查、renamed 配对 content_differs 注记、动态世界目录识别（含改名配对提示）、未哈希文件 mtime 演化检出。

**Architecture:** 全部走加法式路线：ModPair/FileEntry/Snapshot 加可选字段，Differ 加默认关闭的 `mtime_evidence` 闸门，规则引擎在 default 之下追加动态 world 层；配对键格 `_LATTICE` 与六桶语义零改动。旧快照/旧 JSON 消费者完全兼容（SNAPSHOT_FORMAT 保持 1）。

**Tech Stack:** Python 3.11+（开发环境 3.13 venv）、pytest、ruff 0.15.20、pathspec/PyYAML。

**Spec:** `Reference/specs/2026-10-01-batch-g-world-aware-embedded-client-only-design.md`（本计划从 spec 出发，执行者两个文件都读）

## Global Constraints

- 所有注释/docstring 中文；文件 UTF-8 无 BOM；路径一律 `pathlib.Path`。
- SNAPSHOT_FORMAT 保持 **1**；TOOL_VERSION 保持 "0.6.0"；所有新 JSON 键可选/条件出现。
- `migration/data/*.yaml` 改动后必须重跑 `python tools/gen_manifest.py`（根目录运行）。
- 版本号 0.10.0 只在 T7 改（`pyproject.toml` + `migration/__init__.py` 两处同步）；此前任务不得动版本。
- 测试绝对导入 `from migration.xxx import ...`；`pytest tests/` 基线 443 全绿、`ruff check .` 0 违例是每个任务的退出条件。
- ruff 固定 0.15.20（`.venv/Scripts/ruff`）；worktree 内 venv 若缺依赖，用主仓 `F:\code\mcmigrator\.venv\Scripts\python.exe -m pytest`（3.13）。
- 语料 zip 与 `Reference/observations/` 为 gitignored 本地资料：**任何任务不得引用其路径**，夹具数据已全部内联在本计划里。
- 每个任务一个提交，中文 conventional commit（feat/fix/test/docs）。

## Review Focus

1. **旧快照三无混合复放**（无 world_dirs / 无 mtime / 无 resolved_root）：所有新通道必须惰性。→ T4 `test_snapshot_legacy_world_dirs_absent_and_garbage`；T6 `test_differ_mtime_inert_on_old_snapshots`；既有九轮回放全绿即总体哨兵。
2. **动态世界层不得翻案 default never**：世界目录内 `.bak` 仍须 NEVER（世界备份里就有 .bak）。→ T4 `test_ruleset_world_layer_below_default_never`。
3. **level-name 畸形值注入规则层**（`..`、含 `/`、空串、乱码路径段）：不得生成恶意 glob。→ T4 `test_detect_world_dirs_rejects_unsafe_level_name` + `test_build_ruleset_world_dirs_invalid_name_error`。
4. **跨实例 mtime 假阳性**：复制必然改变 mtime，闸门必须按「两侧 resolved_root 相同」判同体演化。→ T6 `test_differ_mtime_gate_off_cross_root`。
5. **size 代理边界 + JSON 加法式**：renamed 同尺寸真异容不可见（文档化局限，不得为它给 mods 加哈希）；`content_differs` 键仅在 True 时出现。→ T2 `test_annotate_content_differs_same_size_no_flag` + `test_mod_pair_to_dict_content_differs_conditional`。

---

### Task 1: 四套语料夹具 + r12/r13 稳定锚（绿色）

**Files:**
- Create: `tests/fixtures/server_corpus/20260930/r12_pre.json`、`r12_post.json`
- Create: `tests/fixtures/server_corpus/20261001/r13_pre.json`、`r13_mid.json`、`r13_post.json`
- Modify: `tests/fixtures/server_corpus/README.md`
- Test: `tests/test_corpus_regression.py`

**Interfaces:**
- Consumes: 既有 `Snapshot.load` / `build_ruleset` / `Differ` / `pair_mods_by_filename` / `run_diff`（签名不变）。
- Produces: 五个夹具快照（r12 双侧 mods-only；r13 三段世界切换，均含 `resolved_root`/`world_dirs`，.mca 条目含 `mtime`——0.9.0 加载器忽略未知键，T4/T6 起被读取）。后续任务在此夹具上追加断言。

- [ ] **Step 1: 生成五个夹具文件**

在仓库根运行以下脚本（一次性，不提交脚本本身；`json.dump(..., ensure_ascii=False, indent=1)` 与既有夹具风格一致）：

```python
import json
from pathlib import Path

BASE = Path("tests/fixtures/server_corpus")

def entry(path, size, md5=None, mtime=None):
    d = {"path": path, "size": size, "md5": md5}
    if mtime is not None:
        d["mtime"] = mtime
    return d

def snap(version, scanned_at, files, world_dirs):
    return {
        "tool_version": "0.6.0", "snapshot_format": 1, "version": version,
        "game_root": "C:\\fixture\\sanitized", "scanned_at": scanned_at,
        "hash_mode": "tiered", "file_count": len(files),
        "resolved_root": "C:\\fixture\\sanitized\\versions\\r13",
        "world_dirs": world_dirs, "files": files,
    }

# ---- 20260930: r12 换装(mods-only;size 取自《旧件指纹.md》实测) ----
r12_src = [
    entry("mods/[潮汐] tide-neoforge-1.21.1-2.1.1.jar", 2155309),
    entry("mods/[机械动力：创意传动] create_connected-1.3.3-mc1.21.1.jar", 6786783),
    entry("mods/[机械动力：懒惰刻] CreateLazyTick-2.7.30-6.0.10-neoforge-1.21.1.jar", 488862),
    entry("mods/[精妙背包] sophisticatedbackpacks-1.21.1-3.26.5.2171.jar", 1239518),
    entry("mods/[FTB 区块] ftb-chunks-neoforge-2101.1.22.jar", 655807),
    entry("mods/automobility-0.5.0.h+1.21.1-neoforge.jar", 1331432),
    entry("mods/create_wrapped-1.0.3-neoforge-1.21.1.jar", 725708),
    entry("mods/fetzisdisplays-neoforge-1.1.0-1.21.jar", 1374210),
    entry("mods/ftb-library-neoforge-2101.1.36.jar", 1439427),
    entry("mods/geckolib-neoforge-1.21.1-4.9.2.jar", 630584),
    entry("mods/xaeroworldmap-neoforge-1.21.1-1.41.2.jar", 1387921),
]
r12_dst = [
    entry("mods/create_connected-1.3.3-mc1.21.1.jar", 6786784),  # F33:散件重建版(差1字节)
    entry("mods/CreateLazyTick-2.7.30-6.0.10-neoforge-1.21.1.jar", 488862),
    entry("mods/ftb-chunks-neoforge-2101.1.22.jar", 655807),
    entry("mods/sophisticatedbackpacks-1.21.1-3.26.6.2174.jar", 1240000),
    entry("mods/ftb-library-neoforge-2101.1.37.jar", 1440000),
    entry("mods/geckolib-neoforge-1.21.1-4.9.3.jar", 631000),
    entry("mods/damage-engine-2.1.1-NeoForge-1.21.1.jar", 769529),  # F32:内嵌 anima 客户端件
    entry("mods/easy_villagers_iron_spells_compat-1.0.jar", 50000),
    entry("mods/hoverboards-1.0.0.jar", 60000),
    entry("mods/lmft-1.1.1.jar", 70000),
    entry("mods/more_orn_plants-1.3.3-Zhongqiu.jar", 80000),
    entry("mods/TerraBlender-neoforge-1.21.1-4.1.0.8.jar", 90000),
]

d = BASE / "20260930"; d.mkdir(exist_ok=True)
for name, files in (("r12_pre.json", r12_src), ("r12_post.json", r12_dst)):
    payload = {
        "tool_version": "0.6.0", "snapshot_format": 1,
        "version": "r12-pre" if "pre" in name else "r12-post",
        "game_root": "C:\\fixture\\sanitized",
        "scanned_at": "2026-09-30T13:04:00+08:00" if "pre" in name else "2026-09-30T13:15:30+08:00",
        "hash_mode": "tiered", "file_count": len(files), "files": files,
    }
    (d / name).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

# ---- 20261001: r13 世界切换三段(裁剪合成;mtime 仅 .mca) ----
d = BASE / "20261001"; d.mkdir(exist_ok=True)
pre = [
    entry("server.properties", 1200, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
    entry("mods/create-6.0.10-neoforge+mc1.21.1.jar", 1464759),
    entry("world/level.dat", 55000, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
    entry("world/region/r.0.0.mca", 4206592, mtime=1759303200),
    entry("world/session.lock", 5, "cccccccccccccccccccccccccccccccc"),
    entry("world/serverconfig/ftbessentials.snbt", 800, "dddddddddddddddddddddddddddddddd"),
    entry("world/data/scoreboard.dat", 300, "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"),
    entry("logs/latest.log", 100, "ffffffffffffffffffffffffffffffff"),
]
mid = [
    entry("server.properties", 1205, "a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2"),
    entry("mods/create-6.0.10-neoforge+mc1.21.1.jar", 1464759),
    entry("mods/automobility-0.5.0.h+1.21.1-neoforge.jar", 1331432),
    entry("world_backup_20261001/level.dat", 55000, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
    entry("world_backup_20261001/region/r.0.0.mca", 4206592, mtime=1759303200),
    entry("world_backup_20261001/session.lock", 5, "cccccccccccccccccccccccccccccccc"),
    entry("world_backup_20261001/serverconfig/ftbessentials.snbt", 800, "dddddddddddddddddddddddddddddddd"),
    entry("world_backup_20261001/data/scoreboard.dat", 300, "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"),
    entry("f1-shanghai/level.dat", 66492, "hhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhh"),
    entry("f1-shanghai/level.dat_old", 66491, "h2h2h2h2h2h2h2h2h2h2h2h2h2h2h2h2"),
    entry("f1-shanghai/region/r.-1.-1.mca", 4206592, mtime=1759338540),
    entry("f1-shanghai/region/r.-1.-2.mca", 0, mtime=1759338540),
    entry("f1-shanghai/raids_overworld.dat", 100, "r1r1r1r1r1r1r1r1r1r1r1r1r1r1r1r1"),
    entry("logs/latest.log", 100, "ffffffffffffffffffffffffffffffff"),
]
post = [
    entry("server.properties", 1206, "a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3"),
    entry("mods/create-6.0.10-neoforge+mc1.21.1.jar", 1464759),
    entry("mods/automobility-0.5.0.h+1.21.1-neoforge.jar", 1331432),
    entry("world_backup_20261001/level.dat", 55000, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
    entry("world_backup_20261001/region/r.0.0.mca", 4206592, mtime=1759303200),
    entry("world_backup_20261001/session.lock", 5, "cccccccccccccccccccccccccccccccc"),
    entry("world_backup_20261001/serverconfig/ftbessentials.snbt", 800, "dddddddddddddddddddddddddddddddd"),
    entry("world_backup_20261001/data/scoreboard.dat", 300, "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"),
    entry("f1-shanghai/level.dat", 5519, "h9h9h9h9h9h9h9h9h9h9h9h9h9h9h9h9"),
    entry("f1-shanghai/level.dat_old", 5374, "h8h8h8h8h8h8h8h8h8h8h8h8h8h8h8h8"),
    entry("f1-shanghai/region/r.-1.-1.mca", 4206592, mtime=1759343400),  # F35:同尺寸重写
    entry("f1-shanghai/region/r.-1.-2.mca", 0, mtime=1759338540),
    entry("f1-shanghai/raids_overworld.dat", 110, "r2r2r2r2r2r2r2r2r2r2r2r2r2r2r2r2"),
    entry("f1-shanghai/session.lock", 5, "s5s5s5s5s5s5s5s5s5s5s5s5s5s5s5s5"),
    entry("f1-shanghai/data/neoforge_data_attachments.dat", 50, "n0n0n0n0n0n0n0n0n0n0n0n0n0n0n0n0"),
    entry("f1-shanghai/serverconfig/ftbessentials.snbt", 820, "f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7"),
    entry("logs/latest.log", 102, "f3f3f3f3f3f3f3f3f3f3f3f3f3f3f3f3"),
]
for name, files, wd, ts in (
    ("r13_pre.json", pre, ["world"], "2026-10-01T13:26:00+08:00"),
    ("r13_mid.json", mid, ["f1-shanghai", "world_backup_20261001"], "2026-10-01T13:28:10+08:00"),
    ("r13_post.json", post, ["f1-shanghai", "world_backup_20261001"], "2026-10-01T13:33:00+08:00"),
):
    (d / name).write_text(
        json.dumps(snap("r13-" + name[4:-5], ts, files, wd), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
print("done")
```

- [ ] **Step 2: 夹具 README 补两行**

`tests/fixtures/server_corpus/README.md` 的轮次表按既有行风格追加（口径：合成自 diff JSON 真值 + 《旧件指纹.md》/r13 观察，规模裁剪）：

```markdown
| 20260930 | `r12_pre.json` / `r12_post.json` | r12 换装段:mods-only,11 源独有(旧件指纹实测 size)+ 12 目标独有(6 配对+6 新增);F33 create_connected 差 1 字节、F32 damage-engine |
| 20261001 | `r13_pre.json` / `r13_mid.json` / `r13_post.json` | r13 世界切换三段:world→world_backup 改名(同路径同尺寸)+ f1-shanghai 部署/首启演化(level.dat 尺变、r.-1.-1.mca 同尺寸 mtime 异);含 world_dirs/resolved_root/mtime(裁剪合成) |
```

（若 README 表列结构与上述不符，以既有结构为准改写，保持信息量不变。）

- [ ] **Step 3: 写稳定锚测试（必须绿色）**

`tests/test_corpus_regression.py`：`ROUNDS` 字典追加两个条目（注意 20261001 只锚 pre→mid——该段桶分布在批次G 全程稳定；mid→post 的锚在 T6 落）：

```python
    "20260930": ("r12_pre.json", "r12_post.json",
                 {"to_migrate": 0, "candidate": 0, "mods": 23,
                  "only_in_dst": 0, "identical": 0, "never": 0}),
    "20261001": ("r13_pre.json", "r13_mid.json",
                 {"to_migrate": 6, "candidate": 0, "mods": 2,
                  "only_in_dst": 10, "identical": 0, "never": 1}),
```

文件末尾追加测试：

```python
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
```

- [ ] **Step 4: 全量验证（必须全绿——本任务锚定既有行为）**

Run: `python -m pytest tests/ -q`
Expected: 443 + 5 = **448 passed**（ROUNDS 参数化 +2，新测试 +3）

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/server_corpus/ tests/test_corpus_regression.py
git commit -m "test(corpus): 第十一/十二轮语料夹具 — r12 换装对+r13 世界切换三段(稳定锚)"
```

---

### Task 2: F33 — renamed 配对 content_differs 注记

**Files:**
- Modify: `migration/moddb.py`（ModPair 字段 + to_dict + 新函数 `annotate_content_differs`）
- Modify: `migration/pipeline.py:469-483`（compute_mod_pairs 尾段接线）
- Modify: `migration/reporter.py:52-90`（DiffReporter）与 `:185-200,271-278`（PlanReporter）
- Test: `tests/test_moddb.py`、`tests/test_reporter.py`、`tests/test_corpus_regression.py`

**Interfaces:**
- Consumes: T1 夹具（r12 create_connected 6786783 vs 6786784）。
- Produces: `ModPair.content_differs: bool = False`（frozen 字段，默认值全部现有构造点零改动）；`annotate_content_differs(pairs, src_sizes: dict[str,int], dst_sizes: dict[str,int]) -> list[ModPair]`（moddb 公有函数）；`to_dict()` 仅 True 时含 `"content_differs": true`。T7 的 README 与 JSON 文档消费它。

- [ ] **Step 1: 写失败测试（moddb 单元）**

`tests/test_moddb.py` 追加：

```python
def test_annotate_content_differs_renamed_size_differ() -> None:
    """F33:同版本改名对 size 异 → content_differs=True;kind 不变(证据注记非新类别)。"""
    from migration.moddb import ModPair, annotate_content_differs
    p = ModPair(modid="cc", kind="renamed", src_files=["mods/a.jar"],
                dst_files=["mods/b.jar"], src_version="1.0", dst_version="1.0",
                source="filename")
    out = annotate_content_differs([p], {"mods/a.jar": 100}, {"mods/b.jar": 101})
    assert out[0].content_differs is True
    assert out[0].kind == "renamed"


def test_annotate_content_differs_same_size_no_flag() -> None:
    """F33 size 代理边界:同尺寸改名对不注记(字节异不可见,spec §7 妥协 2)。"""
    from migration.moddb import ModPair, annotate_content_differs
    p = ModPair(modid="cc", kind="renamed", src_files=["mods/a.jar"],
                dst_files=["mods/b.jar"], src_version="1.0", dst_version="1.0")
    out = annotate_content_differs([p], {"mods/a.jar": 100}, {"mods/b.jar": 100})
    assert out[0].content_differs is False


def test_annotate_content_differs_upgrade_and_unknown_size_untouched() -> None:
    """F33:upgrade 隐含内容变化不注记;任一侧 size 未知不注记(不猜)。"""
    from migration.moddb import ModPair, annotate_content_differs
    up = ModPair(modid="u", kind="upgrade", src_files=["mods/u1.jar"],
                 dst_files=["mods/u2.jar"], src_version="1.0", dst_version="1.1")
    out = annotate_content_differs([up], {"mods/u1.jar": 1}, {"mods/u2.jar": 999})
    assert out[0].content_differs is False
    rn = ModPair(modid="r", kind="renamed", src_files=["mods/r1.jar"],
                 dst_files=["mods/r2.jar"], src_version="2.0", dst_version="2.0")
    out2 = annotate_content_differs([rn], {}, {"mods/r2.jar": 5})
    assert out2[0].content_differs is False


def test_mod_pair_to_dict_content_differs_conditional() -> None:
    """F33 JSON 加法式:仅 content_differs=True 时出现该键。"""
    from migration.moddb import ModPair
    base = dict(modid="cc", kind="renamed", src_files=["mods/a.jar"],
                dst_files=["mods/b.jar"], src_version="1.0", dst_version="1.0")
    assert "content_differs" not in ModPair(**base).to_dict()
    assert ModPair(**base, content_differs=True).to_dict()["content_differs"] is True
```

- [ ] **Step 2: 跑红**

Run: `python -m pytest tests/test_moddb.py -q -k content_differs`
Expected: FAIL（`annotate_content_differs` 未定义 / to_dict 无键）

- [ ] **Step 3: 实现 moddb 侧**

`migration/moddb.py`：顶部 `from dataclasses import dataclass, replace`；`ModPair` 字段区（`source` 之后）加：

```python
    content_differs: bool = False  # F33:同版本改名对 size 异(上游重建版证据;仅注记不改 kind)
```

`to_dict` 改为：

```python
    def to_dict(self) -> dict:
        """转为 JSON 可序列化字典(diff --json 的 mod_pairs 元素)。"""
        d = {
            "modid": self.modid,
            "kind": self.kind,
            "src_files": self.src_files,
            "dst_files": self.dst_files,
            "src_version": self.src_version,
            "dst_version": self.dst_version,
            "source": self.source,
        }
        if self.content_differs:
            d["content_differs"] = True  # F33:仅差异时出现(schema 加法式,消费方零噪声)
        return d
```

`merge_mod_pairs` 之后新增：

```python
def annotate_content_differs(
    pairs: list[ModPair], src_sizes: dict[str, int], dst_sizes: dict[str, int],
) -> list[ModPair]:
    """同版本改名对 size 异时注记 content_differs(F33)。

    仅 kind=="renamed" 参与:upgrade 隐含内容变化,rebuilt 已有 ⚠ 谱系;
    任一侧 size 未知(路径不在快照)不注记(不猜)。size 相等而字节异的
    改名对不可见——mods 桶 md5=null 的既定代理边界(spec §7 妥协 2)。

    Args:
        pairs: 待注记配对(双通道合并后,通道无关)。
        src_sizes: 源侧快照 path→size 映射。
        dst_sizes: 目标侧快照 path→size 映射。

    Returns:
        注记后的新列表(frozen dataclass 经 replace 重建,原列表不改)。
    """
    out: list[ModPair] = []
    for p in pairs:
        if p.kind == "renamed" and p.src_files and p.dst_files:
            s = src_sizes.get(p.src_files[0])
            d = dst_sizes.get(p.dst_files[0])
            if s is not None and d is not None and s != d:
                p = replace(p, content_differs=True)
        out.append(p)
    return out
```

- [ ] **Step 4: compute_mod_pairs 接线**

`migration/pipeline.py` `compute_mod_pairs` 内，局部导入行改为
`from .moddb import annotate_content_differs, merge_mod_pairs, pair_mods, pair_mods_by_filename`，函数尾 `return merge_mod_pairs(registry_pairs, filename_pairs)` 改为：

```python
    merged = merge_mod_pairs(registry_pairs, filename_pairs)
    # F33:同版本改名对补 size 证据注记(通道无关,快照为唯一 size 源)
    return annotate_content_differs(
        merged,
        {e.path: e.size for e in src.files},
        {e.path: e.size for e in dst.files},
    )
```

- [ ] **Step 5: reporter 双呈现 + 失败测试**

`tests/test_reporter.py` 追加（沿用文件既有的 `_report_with_mods`/`_pairs` 风格与 rich Console 捕获写法——参考既有 `test_render_*` 用 `Console(file=io.StringIO(), width=200)`；PlanReporter 用 `capsys`）：

```python
def test_diff_reporter_renamed_rebuilt_display() -> None:
    """F33:content_differs 改名对渲染 ⇄renamed(rebuilt) + ⚠;普通改名对无标注无警示。"""
    import io

    from rich.console import Console

    r = _report_with_mods()
    r.mods = [
        DiffItem("mods/cc-old.jar", None, None, note="to_add"),
        DiffItem("mods/cc-new.jar", None, None, note="target_only"),
        DiffItem("mods/plain-old.jar", None, None, note="to_add"),
        DiffItem("mods/plain-new.jar", None, None, note="target_only"),
    ]
    pairs = [
        ModPair(modid="cc", kind="renamed", src_files=["mods/cc-old.jar"],
                dst_files=["mods/cc-new.jar"], src_version="1.0", dst_version="1.0",
                source="filename", content_differs=True),
        ModPair(modid="plain", kind="renamed", src_files=["mods/plain-old.jar"],
                dst_files=["mods/plain-new.jar"], src_version="2.0", dst_version="2.0"),
    ]
    buf = io.StringIO()
    DiffReporter(r, src_version="a", dst_version="b", mod_pairs=pairs).render(
        ReportOptions(mods_only=True), console=Console(file=buf, width=200))
    text = buf.getvalue()
    assert "⇄renamed(rebuilt)" in text and "⚠" in text
    assert "to_add ⇄renamed" in text  # 普通改名对:原样,无 (rebuilt)
    row_rebuilt = [ln for ln in text.splitlines() if "cc-new.jar" in ln][0]
    assert "(rebuilt)" in row_rebuilt and "⚠" in row_rebuilt


def test_plan_reporter_renamed_rebuilt_mirror(capsys) -> None:
    """F33 镜像:plan COPY 行路径 ⇄renamed(rebuilt) + ⚠(与 diff 语义同源)。"""
    from datetime import datetime, timezone

    from migration.plan import ActionRecord, Behavior, MigrationPlan, Origin
    from migration.reporter import PlanOptions, PlanReporter

    plan = MigrationPlan(
        src="a", dst="b",
        generated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        actions=[ActionRecord(path="mods/cc-new.jar", behavior=Behavior.COPY,
                              origin=Origin.MOD_ADDED, src_size=100, dst_size=None,
                              md5_match=None, confidence="high", reason="mod 新增",
                              backup_target=None)],
    )
    pairs = [ModPair(modid="cc", kind="renamed", src_files=["mods/cc-old.jar"],
                     dst_files=["mods/cc-new.jar"], src_version="1.0", dst_version="1.0",
                     source="filename", content_differs=True)]
    PlanReporter(plan, src_version="a", dst_version="b", mod_pairs=pairs).render(
        PlanOptions())
    out = capsys.readouterr().out
    assert "mods/cc-new.jar ⇄renamed(rebuilt)" in out and "⚠" in out
```

（若 `ReportOptions(mods_only=True)`/`PlanOptions()` 构造与既有测试不符，以 `tests/test_reporter.py` 既有用法为准对齐。）

实现：`DiffReporter.__init__`（reporter.py:54-57 一带）改为：

```python
        self._pair_by_path: dict[str, str] = {}
        self._pair_content_differs: set[str] = set()  # F33:改名对 size 异证据路径集
        for p in self.mod_pairs:
            for f in (*p.src_files, *p.dst_files):
                self._pair_by_path[f] = p.kind
                if p.content_differs:
                    self._pair_content_differs.add(f)
```

`_display_note` mods 分支（72-78 行）改为：

```python
            kind = self._pair_by_path.get(item.path)
            rebuilt = item.path in self._pair_content_differs
            parts = [f"⇄{kind}{'(rebuilt)' if rebuilt else ''}"] if kind else []
            if item.path in self._client_only:
                parts.append("client_only")
            if parts:
                note = f"{note} {' '.join(parts)}"
            if kind == "rebuilt" or item.note == "rebuilt" or rebuilt:
                note = f"⚠ {note}"  # F17/F20-3/F33:同版本异构建统一警示
```

never/modpack_swap 镜像分支（79-85 行）改为：

```python
        elif bucket == "never" and note == "modpack_swap":
            # F23:换包排除的旧 jar 若参与配对,--show-never 视角同样可见 ⇄ 标记
            kind = self._pair_by_path.get(item.path)
            if kind:
                suffix = "(rebuilt)" if item.path in self._pair_content_differs else ""
                note = f"{note} ⇄{kind}{suffix}"
                if kind == "rebuilt" or item.path in self._pair_content_differs:
                    note = f"⚠ {note}"
```

`PlanReporter.__init__`（193-196 行）同样补 `_pair_content_differs` 集合；render 行单元格（274-277 行）改为：

```python
                kind = self._pair_by_path.get(r.path)
                if kind:
                    suffix = "(rebuilt)" if r.path in self._pair_content_differs else ""
                    path_cell = f"{r.path} ⇄{kind}{suffix}"
                else:
                    path_cell = r.path
                if kind == "rebuilt" or r.path in self._pair_content_differs:
                    path_cell = f"⚠ {path_cell}"
```

- [ ] **Step 6: 语料级断言（r12 夹具）**

`tests/test_corpus_regression.py` 追加：

```python
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
```

- [ ] **Step 7: 全量验证**

Run: `python -m pytest tests/ -q` → **455 passed**（448+7）；`ruff check .` 0 违例。

- [ ] **Step 8: Commit**

```bash
git add migration/moddb.py migration/pipeline.py migration/reporter.py tests/
git commit -m "feat(pair): F33 renamed 配对 content_differs 注记 — size 证据/⇄renamed(rebuilt)⚠/双 reporter 呈现"
```

---

### Task 3: F32 — client_only 内嵌反查 + yaml 双收编

**Files:**
- Modify: `migration/pipeline.py:502-507`（match_client_only_paths 注册表通道）
- Modify: `migration/data/client_mods.yaml`（+2 条目）
- Regenerate: `migration/data/manifest.sha256`
- Test: `tests/test_moddb.py`、`tests/test_corpus_regression.py`

**Interfaces:**
- Consumes: 既有 `ModInfo.embedded_in`（moddb.py:46，scan_mods 已填）；T1 r12 夹具。
- Produces: `match_client_only_paths` 对内嵌 modid 命中**宿主 jar 路径**；`load_client_mods()` 返回集合含 `damageengine`/`anima` modid 与 `damage-engine`/`anima` family。

- [ ] **Step 1: 写失败测试**

`tests/test_moddb.py` 追加：

```python
def test_load_client_mods_r12_entries() -> None:
    """F32 收编:damageengine/anima 双条目可加载(宿主与内嵌 modid 双录)。"""
    from migration.moddb import load_client_mods

    modids, families = load_client_mods()
    assert {"damageengine", "anima", "glacier_dragon"} <= modids
    assert {"damage-engine", "anima", "frost-dragon"} <= families
```

`tests/test_pipeline.py` 追加（沿用该文件既有 `DiffContext` 构造方式；若其从 moddb 构造 registry 的 helper 已存在则复用）：

```python
def test_match_client_only_embedded_modid_hits_host_jar() -> None:
    """F32:内嵌(jar-in-jar) modid 反查宿主物理 jar——anima 命中 damage-engine 路径。"""
    from pathlib import Path

    from migration.differ import DiffItem, DiffReport
    from migration.moddb import ModInfo, ModRegistry
    from migration.pipeline import DiffContext, match_client_only_paths

    reg = ModRegistry()
    reg.add(ModInfo(modid="damageengine", version="2.1.1",
                    jar_filename="damage-engine-2.1.1-NeoForge-1.21.1.jar",
                    neoforge_range=None))
    reg.add(ModInfo(modid="anima", version="1.0.5", jar_filename="anima-1.0.5.jar",
                    neoforge_range=None,
                    embedded_in="damage-engine-2.1.1-NeoForge-1.21.1.jar"))
    ctx = DiffContext(src_mods=reg, dst_mods=reg,
                      src_dir=Path("x"), dst_dir=Path("y"))
    report = DiffReport()
    report.mods = [DiffItem("mods/damage-engine-2.1.1-NeoForge-1.21.1.jar",
                            None, None, note="to_add")]
    hit = match_client_only_paths(report, ctx, {"anima"}, set())
    assert hit == {"mods/damage-engine-2.1.1-NeoForge-1.21.1.jar"}
    # 顶层 modid 反查行为不变
    hit2 = match_client_only_paths(report, ctx, {"damageengine"}, set())
    assert hit2 == {"mods/damage-engine-2.1.1-NeoForge-1.21.1.jar"}
```

- [ ] **Step 2: 跑红**

Run: `python -m pytest tests/test_moddb.py tests/test_pipeline.py -q -k "client_mods_r12 or embedded"`
Expected: FAIL（yaml 无条目；embedded 反查 miss——hit 为空集）

- [ ] **Step 3: 实现**

`migration/pipeline.py` `match_client_only_paths` 注册表通道（502-507 行）改为：

```python
    if client_modids and ctx is not None:
        for mods in (ctx.src_mods, ctx.dst_mods):
            for mid in mods.modids:
                if mid in client_modids:
                    info = mods.get(mid)
                    # F32:内嵌(jar-in-jar)件反查宿主物理 jar——专服要隔离的是宿主实体
                    physical = info.embedded_in or info.jar_filename
                    hit.add(f"mods/{physical}")
```

`migration/data/client_mods.yaml` 追加（保持既有注释风格）：

```yaml
  - modid: damageengine
    family: damage-engine
    reason: r12 专服构造期 MultiBufferSource 崩溃(F32 实证,已隔离;内嵌 anima)
  - modid: anima
    family: anima
    reason: r12 damage-engine 内嵌 JarInJar 客户端渲染件(F32;modid 通道命中宿主,
      family 兜底独立散装 anima jar)
```

重跑清单（仓库根）：`python tools/gen_manifest.py`，`git diff migration/data/manifest.sha256` 应只增两行（新条目哈希）。

- [ ] **Step 4: 语料级复放测试（家族键通道）**

`tests/test_corpus_regression.py` 追加（复放无活体 ctx → 走 family 通道，验证 yaml 收编实效）：

```python
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
```

- [ ] **Step 5: 全量验证**

Run: `python -m pytest tests/ -q` → **458 passed**（455+3）；`ruff check .` 干净。

- [ ] **Step 6: Commit**

```bash
git add migration/pipeline.py migration/data/client_mods.yaml migration/data/manifest.sha256 tests/
git commit -m "feat(client-only): F32 内嵌 modid 反查宿主 jar + damageengine/anima 收编"
```

---

### Task 4: F34① — 动态世界目录探测与规则层注入

**Files:**
- Modify: `migration/textcompare.py:42-43`（`_parse_properties` 提公有 `parse_properties`）
- Modify: `migration/snapshot.py`（Snapshot.world_dirs 字段 + save/load）
- Modify: `migration/scanner.py`（`detect_world_dirs` + stat 单次复用准备 + build_snapshot 接线）
- Modify: `migration/cli.py:170-225`（build_ruleset world_dirs 参数）与 `:241-277`（_cmd_scan 调序）
- Modify: `migration/pipeline.py`（run_diff/build_plan 两处 build_ruleset 传参）
- Test: `tests/test_textcompare.py`、`tests/test_snapshot.py`、`tests/test_scanner.py`、`tests/test_rules.py`、`tests/test_corpus_regression.py`

**Interfaces:**
- Consumes: 既有 `Rule`/`RuleSet.from_layers`（rules.py）；`parse_properties(data: bytes) -> dict[str, str]`（textcompare 公有化）。
- Produces: `Snapshot.world_dirs: list[str]`（默认 `[]`）；`detect_world_dirs(entries: list[FileEntry], properties_text: bytes | None) -> list[str]`（scanner.py 公有）；`build_ruleset(..., world_dirs: Sequence[str] = ())`（cli.py，新关键字参数，默认空=行为不变）。T5 消费 `world_dirs` 做改名配对；T6 夹具已带该字段。

- [ ] **Step 1: 写失败测试（snapshot + textcompare）**

`tests/test_textcompare.py` 追加：

```python
def test_parse_properties_public_smoke() -> None:
    """F34①:properties 解析器公有化(世界目录探测复用)。"""
    from migration.textcompare import parse_properties

    d = parse_properties(b"# c\r\nlevel-name=f1-shanghai\r\nmotd=a:b\r\n")
    assert d == {"level-name": "f1-shanghai", "motd": "a:b"}
```

`tests/test_snapshot.py` 追加（沿用该文件既有 save/load tmp_path 模式）：

```python
def test_snapshot_world_dirs_roundtrip(tmp_path) -> None:
    """F34①:world_dirs 随快照持久化往返。"""
    from migration.snapshot import FileEntry, Snapshot

    snap = Snapshot(version="v", game_root="g", scanned_at="t", hash_mode="tiered",
                    file_count=1, files=[FileEntry("a", 1, None)],
                    world_dirs=["f1-shanghai", "world"])
    p = tmp_path / "s.json"
    snap.save(p)
    assert Snapshot.load(p).world_dirs == ["f1-shanghai", "world"]


def test_snapshot_legacy_world_dirs_absent_and_garbage(tmp_path) -> None:
    """F34① 兼容:旧快照无键 → [];非 list / 元素非 str → [](容错降级不抛)。"""
    import json

    from migration.snapshot import FileEntry, Snapshot

    snap = Snapshot(version="v", game_root="g", scanned_at="t", hash_mode="tiered",
                    file_count=1, files=[FileEntry("a", 1, None)])
    p = tmp_path / "s.json"
    snap.save(p)
    assert Snapshot.load(p).world_dirs == []
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["world_dirs"] = ["ok", 3, None]
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert Snapshot.load(p).world_dirs == ["ok"]
    raw["world_dirs"] = "oops"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert Snapshot.load(p).world_dirs == []
```

- [ ] **Step 2: 写失败测试（scanner 探测）**

`tests/test_scanner.py` 追加：

```python
def test_detect_world_dirs_level_name_and_level_dat() -> None:
    """F34①:level-name 指向 + 顶层含 level.dat 双源探测,并集升序;客户端 saves/ 不误触。"""
    from migration.scanner import detect_world_dirs
    from migration.snapshot import FileEntry

    entries = [
        FileEntry("server.properties", 10, None),
        FileEntry("f1-shanghai/level.dat", 100, None),
        FileEntry("f1-shanghai/region/r.0.0.mca", 5, None),
        FileEntry("world_backup_20261001/level.dat", 100, None),
        FileEntry("saves/myworld/level.dat", 100, None),  # 客户端二层:非顶层,不命中
        FileEntry("config/foo.toml", 1, None),
    ]
    text = b"level-name=f1-shanghai\nmotd=x\n"
    assert detect_world_dirs(entries, text) == ["f1-shanghai", "world_backup_20261001"]
    # properties 缺失 → 仅 level.dat 启发
    assert detect_world_dirs(entries, None) == ["f1-shanghai", "world_backup_20261001"]
    # level-name 指向不存在的目录 → 跳过①
    assert detect_world_dirs(entries, b"level-name=ghost\n") == \
        ["f1-shanghai", "world_backup_20261001"]


def test_detect_world_dirs_rejects_unsafe_level_name() -> None:
    """F34① 安全:level-name 含路径段/点号/空 → 不入探测结果(防 glob 注入)。"""
    from migration.scanner import detect_world_dirs
    from migration.snapshot import FileEntry

    entries = [FileEntry("server.properties", 10, None),
               FileEntry("world/level.dat", 1, None)]
    for bad in (b"level-name=../evil\n", b"level-name=a/b\n", b"level-name=.\n",
                b"level-name=\n"):
        assert detect_world_dirs(entries, bad) == ["world"]


def test_scanner_records_world_dirs(tmp_path) -> None:
    """F34① 接线:Scanner 活体扫描写入 world_dirs(server.properties 实读)。"""
    from migration.scanner import Scanner

    root = tmp_path / "v"
    (root / "f1-shanghai").mkdir(parents=True)
    (root / "world_backup").mkdir()
    (root / "saves" / "w").mkdir(parents=True)
    (root / "f1-shanghai" / "level.dat").write_bytes(b"x")
    (root / "world_backup" / "level.dat").write_bytes(b"x")
    (root / "saves" / "w" / "level.dat").write_bytes(b"x")
    (root / "server.properties").write_bytes(b"level-name=f1-shanghai\n")
    (root / "config.toml").write_bytes(b"a=1\n")
    snap, errs = Scanner(root, "v").build_snapshot(str(tmp_path))
    assert errs == []
    assert snap.world_dirs == ["f1-shanghai", "world_backup"]
```

- [ ] **Step 3: 跑红**

Run: `python -m pytest tests/test_textcompare.py tests/test_snapshot.py tests/test_scanner.py -q -k "world_dirs or parse_properties_public"`
Expected: FAIL（ImportError/AttributeError + world_dirs 缺字段）

- [ ] **Step 4: 实现 textcompare / snapshot / scanner**

`migration/textcompare.py`：`_parse_properties` 更名 `parse_properties`（docstring 补一句「公有:F34① 世界目录探测复用」），`properties_semantic_equal` 内部调用同步更名；全仓 grep `_parse_properties` 确认无残留引用。

`migration/snapshot.py`：`Snapshot` 字段区 `resolved_root` 之后加：

```python
    world_dirs: list[str] = field(default_factory=list)  # F34①:scan 探测的世界目录(level-name/level.dat)
```

（顶部 import 补 `field`。）save payload `"resolved_root"` 之后加 `"world_dirs": self.world_dirs,`；load 的 `cls(...)` 加：

```python
                world_dirs=(
                    [w for w in payload.get("world_dirs") if isinstance(w, str)]
                    if isinstance(payload.get("world_dirs"), list) else []
                ),
```

`migration/scanner.py`：新增公有函数（`Scanner` 类之前）：

```python
def detect_world_dirs(entries: list[FileEntry], properties_text: bytes | None) -> list[str]:
    """探测世界目录(F34①):server.properties level-name + 顶层含 level.dat 的目录。

    ① level-name 指向目录(单段安全名且在文件清单中存在)——活跃世界;
    ② 顶层直接包含 level.dat 的目录——覆盖任意命名/改名留存/多世界形态;
    并集去重升序。properties_text=None(缺文件/不可读)仅走②。
    客户端版本文件夹世界在 saves/<名>/ 二层,①②均不误触。

    Args:
        entries: 扫描所得文件条目(相对路径,正斜杠)。
        properties_text: server.properties 原始字节;None 表示不可用。

    Returns:
        世界目录名列表(升序);无探测结果为空列表。
    """
    dirs: set[str] = set()
    if properties_text is not None:
        from .textcompare import parse_properties

        name = (parse_properties(properties_text) or {}).get("level-name", "")
        # 单段安全名:非空、无路径分隔、非点号段,且目录确实在清单中
        if (name and "/" not in name and "\\" not in name
                and name not in (".", "..")
                and any(e.path.startswith(name + "/") for e in entries)):
            dirs.add(name)
    for e in entries:
        top, sep, rest = e.path.partition("/")
        if sep and rest == "level.dat":
            dirs.add(top)
    return sorted(dirs)
```

`build_snapshot` 在 `entries, errors = self.scan()` 之后、构造 `snap` 之前加：

```python
        # F34①:世界目录探测(server.properties 优先,顶层 level.dat 兜底)
        try:
            properties_text = (self.version_dir / "server.properties").read_bytes()
        except OSError:
            properties_text = None
        world_dirs = detect_world_dirs(entries, properties_text)
```

`Snapshot(...)` 构造加 `world_dirs=world_dirs,`。

- [ ] **Step 5: 规则层 + 三接线（含失败测试）**

`tests/test_rules.py` 追加：

```python
def test_build_ruleset_world_layer_below_default_never() -> None:
    """F34① 位序:动态世界层在 default 之下——世界内 .bak 仍 NEVER,世界目录 MUST_MIGRATE。"""
    from pathlib import Path

    from migration.cli import build_ruleset
    from migration.rules import Category

    rs, errs = build_ruleset(
        ["a"], exclude=(), include=(), rule_files=(),
        mcmig_dir=Path("__nonexistent__"), world_dirs=("f1-shanghai",),
    )
    assert errs == []
    assert rs.classify("f1-shanghai/region/r.0.0.mca") is Category.MUST_MIGRATE
    assert rs.classify("f1-shanghai/backup.bak") is Category.NEVER  # default never 优先
    assert rs.classify("world/level.dat") is Category.MUST_MIGRATE  # 静态规则照旧


def test_build_ruleset_world_dirs_invalid_name_error() -> None:
    """F34① 安全:非法目录名跳过并出警告,不生成规则。"""
    from pathlib import Path

    from migration.cli import build_ruleset
    from migration.rules import Category

    rs, errs = build_ruleset(
        ["a"], exclude=(), include=(), rule_files=(),
        mcmig_dir=Path("__nonexistent__"), world_dirs=("../evil", "a/b", ""),
    )
    assert errs and any("world" in e for e in errs)
    assert rs.classify("../evil/x") is Category.UNKNOWN
```

实现 `migration/cli.py` `build_ruleset`：签名加 `world_dirs: Sequence[str] = (),`（import `Sequence`）；函数尾 `from_layers` 之前：

```python
    # F34①:动态世界目录层(default 之下最低优先级——default never 规则可压过,
    # 用户/CLI 规则可覆盖;仅 default 未命中的路径落此层)
    world_layer: list[rules.Rule] = []
    for wd in world_dirs:
        if not wd or "/" in wd or "\\" in wd or wd in (".", ".."):
            errors.append(f"world_dirs: 非法目录名已跳过 {wd!r}")
            continue
        world_layer.append(
            rules.Rule(match=f"{wd}/**", decide=rules.Category.MUST_MIGRATE,
                       reason="世界目录探测(F34:level-name/level.dat)", source="world"))
    rs = rules.RuleSet.from_layers(cli_rules, extra, user, orphan, rebuild, whitelist,
                                   default, world_layer)
```

三处接线：
1. `cli.py _cmd_scan`：把 `build_ruleset(...)` 调用移到 `snap = scan_version(...)` 之后，并加 `world_dirs=snap.world_dirs,`（`[规则警告]` 打印随之移到扫描输出后——stdout 顺序变化是有意为之）。
2. `pipeline.py run_diff`（604-611 行）`build_ruleset(...)` 加 `world_dirs=sorted(set(src_snap.world_dirs) | set(dst_snap.world_dirs)),`。
3. `pipeline.py build_plan`（245-253 行）`build_ruleset(...)` 加 `world_dirs=sorted(set(src_snap.world_dirs) | set(dst_snap.world_dirs)),`（变量名 `src_snap`/`dst_snap` 与该函数既有一致）。

`tests/test_corpus_regression.py` 追加（新行为语料锚——r13 mid→post 世界文件从 candidate 迁到 to_migrate）：

```python
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
```

- [ ] **Step 6: 全量验证（含九轮旧语料零回归 = 旧快照无 world_dirs 惰性哨兵）**

Run: `python -m pytest tests/ -q` → **467 passed**（458+9）；`ruff check .` 干净。

- [ ] **Step 7: Commit**

```bash
git add migration/textcompare.py migration/snapshot.py migration/scanner.py migration/cli.py migration/pipeline.py tests/
git commit -m "feat(world): F34① 动态世界目录 — level-name/level.dat 探测入快照,规则层最低优先级注入"
```

---

### Task 5: F34② — 世界改名配对提示

**Files:**
- Modify: `migration/pipeline.py`（新函数 `world_rename_notices` + run_diff 接线）
- Test: `tests/test_pipeline.py`、`tests/test_corpus_regression.py`

**Interfaces:**
- Consumes: T4 的 `Snapshot.world_dirs`；快照 `files` 的 path/size。
- Produces: `world_rename_notices(src: Snapshot, dst: Snapshot) -> list[str]`（pipeline 公有；每命中改名对一条，消息含 `疑似世界目录改名`）；`run_diff` 在 diff_identity 提示之后把它并入 `notices`。

- [ ] **Step 1: 写失败测试**

`tests/test_pipeline.py` 追加：

```python
def _wsnap(version: str, world_dirs: list[str], files: dict[str, int]) -> "Snapshot":
    """构造只含 path→size 的迷你快射(F34② 测试用)。"""
    from migration.snapshot import FileEntry, Snapshot

    return Snapshot(version=version, game_root="C:\\fixture", scanned_at="t",
                    hash_mode="tiered", file_count=len(files),
                    files=[FileEntry(p, s, None) for p, s in files.items()],
                    world_dirs=world_dirs)


def test_world_rename_notices_full_match() -> None:
    """F34②:world → world_backup 全量同路径同尺寸 → 一条改名提示。"""
    from migration.pipeline import world_rename_notices

    src = _wsnap("a", ["world"], {"world/level.dat": 100, "world/region/r.0.0.mca": 5})
    dst = _wsnap("b", ["world_backup"],
                 {"world_backup/level.dat": 100, "world_backup/region/r.0.0.mca": 5})
    notices = world_rename_notices(src, dst)
    assert len(notices) == 1
    assert "疑似世界目录改名" in notices[0]
    assert "world → world_backup" in notices[0] and "2" in notices[0]


def test_world_rename_notices_threshold_and_mismatch() -> None:
    """F34②:占比 <0.9 不发(跨世界重合极低);同路径但 size 异不计入匹配。"""
    from migration.pipeline import world_rename_notices

    # 10 件中 8 件匹配(80%) → 不发
    src = _wsnap("a", ["world"], {f"world/{i}.mca": i for i in range(10)})
    dst = _wsnap("b", ["nb"], {f"nb/{i}.mca": (i if i < 8 else 999) for i in range(10)})
    assert world_rename_notices(src, dst) == []
    # 10/10 匹配 → 发
    dst2 = _wsnap("b", ["nb"], {f"nb/{i}.mca": i for i in range(10)})
    assert len(world_rename_notices(src, dst2)) == 1
    # 旧目录双侧都在(未消失) → 不参与
    both = _wsnap("b", ["world"], {"world/level.dat": 1})
    assert world_rename_notices(src, both) == []


def test_world_rename_notices_empty_and_no_dirs() -> None:
    """F34②:无 world_dirs(旧快照)或无候选对 → 空。"""
    from migration.pipeline import world_rename_notices

    assert world_rename_notices(_wsnap("a", [], {}), _wsnap("b", [], {})) == []
```

- [ ] **Step 2: 跑红**

Run: `python -m pytest tests/test_pipeline.py -q -k world_rename`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现**

`migration/pipeline.py` 在 `diff_identity_notices` 之后新增：

```python
def world_rename_notices(src: Snapshot, dst: Snapshot) -> list[str]:
    """世界目录改名探测(F34②):src 独有世界 A → dst 独有世界 B,同路径同尺寸
    占比 ≥0.9 时发提示(仅提示不重分类——假警报降级为可见解释)。

    候选:A ∈ src.world_dirs 且 ∉ dst.world_dirs(旧路径消失),B 反之(新路径出现);
    匹配 = 相对子路径双侧存在且 size 相等;占比分母 min(双侧文件数),≥1 件才评估。
    阈值 0.9 为 r13 语料标定(660/660;容忍 session.lock 类零星重写)。

    Args:
        src: 源侧快照(需 world_dirs,旧快照缺省为空表自然不发)。
        dst: 目标侧快照。

    Returns:
        提示行列表(每命中改名对一条,按 "A → B" 排序)。
    """
    notices: list[str] = []
    for a in sorted(set(src.world_dirs) - set(dst.world_dirs)):
        a_files = {e.path[len(a) + 1:]: e.size for e in src.files
                   if e.path.startswith(a + "/")}
        if not a_files:
            continue
        for b in sorted(set(dst.world_dirs) - set(src.world_dirs)):
            b_files = {e.path[len(b) + 1:]: e.size for e in dst.files
                       if e.path.startswith(b + "/")}
            if not b_files:
                continue
            denom = min(len(a_files), len(b_files))
            matched = sum(1 for sub, sz in a_files.items()
                          if b_files.get(sub) == sz)
            if matched and matched / denom >= 0.9:
                notices.append(
                    f"[提示] 疑似世界目录改名: {a} → {b}"
                    f"({matched} 件同路径同尺寸,内容未消失,已随目录改名迁移)"
                )
    return sorted(notices)
```

`run_diff` 内 `hint = diff_identity_notices(...)` 段之后（`elif ctx ... log.debug` 块之后、`mod_pairs = compute_mod_pairs(...)` 之前）加：

```python
    # F34②:世界目录改名探测(仅提示;pre→mid 切换段的千级 to_migrate 假警报降级)
    notices.extend(world_rename_notices(src_snap, dst_snap))
```

- [ ] **Step 4: 语料级断言**

`tests/test_corpus_regression.py` 追加：

```python
def test_corpus_20261001_world_rename_notice_replay(tmp_path) -> None:
    """F34② 语料级:pre→mid 复放恰一条改名提示(world→world_backup);
    mid→post 无改名段不发。"""
    from migration.pipeline import run_diff

    d = FIXTURES / "20261001"
    snaps = tmp_path / ".mcmig" / "snapshots"
    snaps.mkdir(parents=True)
    for name in ("r13_pre", "r13_mid", "r13_post"):
        Snapshot.load(d / f"{name}.json").save(snaps / f"{name}.snapshot.json")
    out1 = run_diff(tmp_path, src="r13-pre", dst="r13-mid")
    rename = [n for n in out1.notices if "疑似世界目录改名" in n]
    assert len(rename) == 1
    assert "world → world_backup_20261001" in rename[0]
    out2 = run_diff(tmp_path, src="r13-mid", dst="r13-post")
    assert not [n for n in out2.notices if "疑似世界目录改名" in n]
```

- [ ] **Step 5: 全量验证**

Run: `python -m pytest tests/ -q` → **471 passed**（467+4）；`ruff check .` 干净。

- [ ] **Step 6: Commit**

```bash
git add migration/pipeline.py tests/
git commit -m "feat(world): F34② 世界目录改名配对提示 — 同路径同尺寸≥90% 降假警报"
```

---

### Task 6: F35 — 未哈希文件 mtime + 同根演化通道

**Files:**
- Modify: `migration/snapshot.py:22-28`（FileEntry.mtime）
- Modify: `migration/scanner.py:36-52`（单次 stat,未哈希记 mtime）
- Modify: `migration/differ.py:68-100,115-175`（mtime_evidence 闸门 + 通道）
- Modify: `migration/pipeline.py`（run_diff/build_plan 两处 Differ 传参）
- Modify: `migration/reporter.py:62-90`（note 显示映射）
- Test: `tests/test_snapshot.py`、`tests/test_scanner.py`、`tests/test_differ.py`、`tests/test_corpus_regression.py`

**Interfaces:**
- Consumes: T1 夹具的 `mtime`/`resolved_root` 字段；`hashing.should_hash` 既有分层。
- Produces: `FileEntry.mtime: int | None = None`（epoch 秒；仅未哈希文件非 None）；`Differ(..., mtime_evidence: bool = False)`（默认关——既有调用点行为不变）；diff note 新值 `"mtime"`（reporter 显示 `mtime(同尺寸重写)`，JSON 原样）。

- [ ] **Step 1: 写失败测试（snapshot + scanner）**

`tests/test_snapshot.py` 追加：

```python
def test_file_entry_mtime_optional_and_load_guard(tmp_path) -> None:
    """F35:FileEntry.mtime 可选往返;旧条目无键 → None;非 int → None。"""
    from migration.snapshot import FileEntry, Snapshot

    snap = Snapshot(version="v", game_root="g", scanned_at="t", hash_mode="tiered",
                    file_count=2,
                    files=[FileEntry("a.mca", 5, None, mtime=1759343400),
                           FileEntry("b.txt", 5, "x" * 32)])
    p = tmp_path / "s.json"
    snap.save(p)
    loaded = Snapshot.load(p)
    assert [f.mtime for f in loaded.files] == [1759343400, None]
```

（`FileEntry` 加 mtime 后，load 里 `FileEntry(path=..., size=..., md5=..., mtime=...)` 需容错非 int——实现里用 `d.get("mtime") if isinstance(d.get("mtime"), int) else None`。）

`tests/test_scanner.py` 追加：

```python
def test_scanner_records_mtime_only_for_unhashed(tmp_path) -> None:
    """F35:未哈希文件(.mca/mods jar)记 mtime;哈希文件(.txt/.json)mtime=None。"""
    import os

    from migration.scanner import Scanner

    root = tmp_path / "v"
    (root / "world" / "region").mkdir(parents=True)
    (root / "mods").mkdir()
    mca = root / "world" / "region" / "r.0.0.mca"
    mca.write_bytes(b"x" * 16)
    jar = root / "mods" / "demo-1.0.jar"
    jar.write_bytes(b"y" * 16)
    txt = root / "options.txt"
    txt.write_text("lang:zh_cn\n", encoding="utf-8")
    for f in (mca, jar, txt):
        os.utime(f, (1759300000, 1759300000))
    snap, errs = Scanner(root, "v").build_snapshot(str(tmp_path))
    assert errs == []
    by = {e.path: e for e in snap.files}
    assert by["world/region/r.0.0.mca"].mtime == 1759300000
    assert by["mods/demo-1.0.jar"].mtime == 1759300000
    assert by["options.txt"].mtime is None
```

- [ ] **Step 2: 写失败测试（differ 闸门矩阵）**

`tests/test_differ.py` 追加（沿用该文件既有 Classifier/RUleset 构造方式；规则集可用 `build_ruleset` 纯默认或该文件既有 helper——以文件现状为准，下面按纯默认写）：

```python
def _clf() -> "Classifier":
    from pathlib import Path

    from migration.cli import build_ruleset
    from migration.classifier import Classifier

    rs, _ = build_ruleset(["a"], exclude=(), include=(), rule_files=(),
                          mcmig_dir=Path("__nonexistent__"))
    return Classifier(rs)


def test_differ_mtime_channel_gated() -> None:
    """F35 闸门矩阵:同尺寸+md5 None+mtime 异 → 仅 mtime_evidence 开时检出 note=mtime。"""
    from migration.snapshot import FileEntry

    s = FileEntry("world/region/r.0.0.mca", 100, None, mtime=1759300000)
    d = FileEntry("world/region/r.0.0.mca", 100, None, mtime=1759340000)
    # 闸门关(默认):size 代理 → identical
    r0 = Differ([s], [d], _clf()).diff()
    assert r0.identical and r0.identical[0].note == "size-based"
    # 闸门开:同根演化 → to_migrate(note=mtime)
    r1 = Differ([s], [d], _clf(), mtime_evidence=True).diff()
    assert [i.note for i in r1.to_migrate] == ["mtime"]
    # size 异本就 modified,mtime 无关
    s2 = FileEntry("world/region/r.0.0.mca", 100, None, mtime=1759300000)
    d2 = FileEntry("world/region/r.0.0.mca", 101, None, mtime=1759300000)
    r2 = Differ([s2], [d2], _clf(), mtime_evidence=True).diff()
    assert [i.note for i in r2.to_migrate] == ["modified"]
    # mods jar 即便闸门开也不走 mtime 通道(F33 域)
    sj = FileEntry("mods/a-1.0.jar", 100, None, mtime=1)
    dj = FileEntry("mods/a-1.0.jar", 100, None, mtime=2)
    r3 = Differ([sj], [dj], _clf(), mtime_evidence=True).diff()
    assert r3.mods and r3.mods[0].note == "shared"


def test_differ_mtime_inert_on_old_snapshots() -> None:
    """F35 兼容:双侧 mtime None(旧快照)即便闸门开也 identical(size-based)。"""
    from migration.snapshot import FileEntry

    s = FileEntry("world/region/r.0.0.mca", 100, None)
    d = FileEntry("world/region/r.0.0.mca", 100, None)
    r = Differ([s], [d], _clf(), mtime_evidence=True).diff()
    assert r.identical and r.identical[0].note == "size-based"
```

（`Differ` 已 import 于该文件顶部则不重复；测试文件如无 `_clf` 同名 helper 则用此名。）

- [ ] **Step 3: 跑红**

Run: `python -m pytest tests/test_snapshot.py tests/test_scanner.py tests/test_differ.py -q -k mtime`
Expected: FAIL（FileEntry 无 mtime / Differ 无 mtime_evidence）

- [ ] **Step 4: 实现 snapshot / scanner / differ**

`migration/snapshot.py` `FileEntry`：

```python
@dataclass(frozen=True)
class FileEntry:
    """相对版本根的一个文件条目。md5 为 None 表示分层策略未哈希;
    mtime 为未哈希文件的修改时刻(epoch 秒,F35 演化检出用),哈希文件为 None。"""

    path: str
    size: int
    md5: str | None
    mtime: int | None = None
```

load 的 files 列表推导改为：

```python
            files = [
                FileEntry(
                    path=d["path"], size=d["size"], md5=d.get("md5"),
                    mtime=m if isinstance(m := d.get("mtime"), int) else None,
                )
                for d in payload["files"]
            ]
```

`migration/scanner.py` `scan()` 循环内：`size = p.stat().st_size` 改为单次 stat 取双值，未哈希时记 mtime：

```python
        for p in sorted(self.version_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.version_dir).as_posix()
            try:
                st = p.stat()
            except OSError as e:
                errors.append(ScanError(rel, f"{rel} stat 失败: {e}"))
                continue
            size = st.st_size
            md5: str | None = None
            mtime: int | None = None
            if hashing.should_hash(p, strict=self.strict):
                try:
                    md5 = hashing.compute_md5(p)
                except OSError as e:
                    errors.append(ScanError(rel, f"{rel} 读取失败: {e}"))
                    continue
            else:
                # F35:未哈希文件(bulk/mods)记 mtime——同尺寸重写的演化信号
                mtime = int(st.st_mtime)
            entries.append(FileEntry(path=rel, size=size, md5=md5, mtime=mtime))
```

`migration/differ.py`：`Differ.__init__` 加参数 `mtime_evidence: bool = False` 存为属性；`_same_content_semantic` 在 md5 分支之后（size-based 判定前）插入通道：

```python
    def _same_content_semantic(self, path: str, s: FileEntry, d: FileEntry) -> tuple[bool, str]:
        """内容比较:F12/F16 在 md5 异、后缀命中、读取成功时做语义复核;
        F35 在 md5 双 None、size 等、mtime 异且闸门开时判同尺寸重写。"""
        if s.md5 is not None and d.md5 is not None and s.md5 != d.md5:
            suffix = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
            check = SEMANTIC_EQUAL_BY_SUFFIX.get(suffix)
            if check is not None and self.content_reader is not None:
                a = self.content_reader(path, "src")
                b = self.content_reader(path, "dst")
                if a is not None and b is not None and check(a, b):
                    return True, "semantics"
            return False, "verified"
        if (self.mtime_evidence and s.md5 is None and d.md5 is None
                and s.mtime is not None and d.mtime is not None
                and s.mtime != d.mtime and s.size == d.size
                and not is_mod_jar(path)):
            return False, "mtime"  # F35:同尺寸重写(懒升级/世界编辑),非字节比对
        return self._same_content(s, d)
```

（模块 docstring 的 note 契约注释同步补一行：`to_migrate/candidate 另有 mtime(F35 同尺寸重写)`。）

- [ ] **Step 5: 双管线接线 + reporter 显示**

`pipeline.py run_diff`：`report = Differ(...)` 之前加闸门计算并传参：

```python
    # F35:mtime 证据闸门——两侧快照同物理根(同实例随时间演化)才开;
    # 跨实例(复制必变 mtime)恒关,杜绝假阳性
    mtime_ev = (src_snap.resolved_root is not None
                and src_snap.resolved_root == dst_snap.resolved_root)
    report = Differ(
        src_snap.files, dst_snap.files, clf,
        content_reader=ctx.read_file if ctx is not None else None,
        modpack_swap=modpack_swap,
        mtime_evidence=mtime_ev,
    ).diff()
```

`pipeline.py build_plan`：找到 `Differ(...)` 构造（ruleset/diff 段），加 `mtime_evidence=src_dir.resolve() == dst_dir.resolve(),`（`src_dir`/`dst_dir` 在 237-238 行已定义）。

`reporter.py _display_note`：mods 分支之前（函数体开头 `note = item.note` 之后）加显示映射：

```python
        if note == "mtime":
            note = "mtime(同尺寸重写)"  # F35:显示层解释;JSON 仍为原始 mtime
```

- [ ] **Step 6: 语料级断言 + 跨根闸门**

`tests/test_corpus_regression.py` 追加：

```python
def test_corpus_20261001_region_mtime_evolution_replay(tmp_path) -> None:
    """F35 语料级:mid→post 同根复放 — r.-1.-1.mca(同尺寸 mtime 异)落 to_migrate
    note=mtime;r.-1.-2.mca(未触碰)仍 identical;跨根闸门关 → 回 identical。
    并锚 mid→post 最终桶分布(ROUNDS 补行)。"""
    from migration.pipeline import run_diff

    d = FIXTURES / "20261001"
    snaps = tmp_path / ".mcmig" / "snapshots"
    snaps.mkdir(parents=True)
    for name in ("r13_mid", "r13_post"):
        Snapshot.load(d / f"{name}.json").save(snaps / f"{name}.snapshot.json")
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
```

注意：**不要**为此给 `ROUNDS` 追加 mid→post 条目——`_diff_round` 是全轮共用 helper 且不带 `world_dirs` 参数，改它会影响其他轮；mid→post 的终态锚就是上面测试内联的六桶字典断言。

- [ ] **Step 7: 全量验证**

Run: `python -m pytest tests/ -q` → **476 passed**（471+5）；`ruff check .` 干净。

- [ ] **Step 8: Commit**

```bash
git add migration/snapshot.py migration/scanner.py migration/differ.py migration/pipeline.py migration/reporter.py tests/
git commit -m "feat(evolve): F35 未哈希文件 mtime 记录 + 同根演化通道 — 同尺寸重写检出(note=mtime)"
```

---

### Task 7: 收口 — 版本 0.10.0 + README 双语 + manifest 复核

**Files:**
- Modify: `pyproject.toml`、`migration/__init__.py`（版本双文件）
- Modify: `README.zh-CN.md`、`README.en.md`
- Verify: `migration/data/manifest.sha256`

**Interfaces:**
- Consumes: T2-T6 全部产出（README 描述以 spec §3 为准）。
- Produces: 版本 0.10.0；文档五点（世界感知/内嵌反查/renamed(rebuilt)/mtime 通道/改名提示）。

- [ ] **Step 1: 版本双文件 0.10.0**

`pyproject.toml` `version = "0.9.0"` → `"0.10.0"`；`migration/__init__.py` `__version__` 同步（grep 0.9.0 确认仅此两处）。

- [ ] **Step 2: README 双语同步**

`README.zh-CN.md` 与 `README.en.md` 按既有章节风格各补/改五点（与批次F 的五点写法对齐，一处一小段或一行）：
1. 动态世界目录识别（level-name + 顶层 level.dat → 快照 `world_dirs` → 规则最低层 must_migrate；世界内 .bak 等默认 never 语义不变）
2. 世界改名配对提示（`疑似世界目录改名` stderr 提示，仅提示不重分类）
3. client_only 内嵌（JarInJar）modid 反查宿主 jar + damageengine/anima 收编
4. mod 配对 `⇄renamed(rebuilt)`（content_differs：同版本改名但 size 异；JSON 条件键）
5. mtime 演化通道（未哈希文件记 mtime；仅两侧快照同物理根时启用；note=mtime）
`README.en.md` 行尾 `Last synced: v0.9.0` → `v0.10.0`。

- [ ] **Step 3: manifest 复核 + 全量验证**

Run: `python tools/gen_manifest.py && git diff --stat migration/data/manifest.sha256`（应无变化——T3 已生成）；`python -m pytest tests/ -q` → **476 passed**；`ruff check .` → 0；`python -m migration -V` → `mcmig 0.10.0`。

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml migration/__init__.py README.zh-CN.md README.en.md
git commit -m "chore(release): 0.10.0 — 批次G 收口(世界感知/内嵌客户端件/renamed-rebuilt/mtime 演化)"
```

---

## 任务依赖与执行说明

- 依赖：T1 先行（夹具全绿）；T4 → T5（world_dirs 字段）；T2/T3/T6 相互独立；T7 最后。
- 每任务退出条件：全量 pytest 绿 + ruff 0 + 单提交；评审按 SDD 流程（fresh reviewer + fix loop ≤5）。
- 合并：全部任务完成后整分支终审，squash 单提交（用户已授权委托收尾：单干净提交 + 推送）。
- 观察记录（spec §4 提到的 `Reference/observations/` 两轮收官 README）属主仓 gitignored 本地资料，**不在 SDD 任务内**——由主持者在合并收尾阶段于主仓补写。
