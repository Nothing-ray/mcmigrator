# 批次H 实现计划:快照自包含(schema v2 内嵌 mod 名册)+ 批次G 遗留收口

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 快照「拍全」——scan 时把 mod 名册五字段序列化进快照(schema v2),diff/plan 消费**快照优先**(修 junction 配对恒失效/跨机复放失真/时刻不配套三痛点);顺带收口批次G 终审遗留(world_rename 门槛/glob 转义/守卫直测/README)与整洁项(build_ruleset 搬迁/_LATTICE tuple 化)。

**Architecture:** snapshot.py 加 `mods` 字段(原始 dict,`moddb → snapshot` 单向依赖不破);moddb 补 `registry_to_dicts/from_dicts/entries()` 转换对;`resolve_diff_context` 双侧嵌入走冻结通道(`DiffContext.mods_frozen`),v1 组合走 0.10.0 路径逐字节不变;`compute_mod_pairs` 零改动,闸门收敛到调用方。纯重构项(搬迁/tuple)零行为。

**Tech Stack:** Python 3.11+ / pytest / pathspec(gitignore 转义语义)。

**Spec:** `Reference/specs/2026-10-01-batch-h-selfcontained-snapshot-registry-design.md`(commit 466d2de)——执行者两个文件都读;本计划从 spec §3.1-§3.8/§4/§5 立论。

**基线:** 执行时以 `pytest --collect-only -q` 实际值为准(计划撰写时 **481**(spec 撰写时 476,其间 r14 轮 +5);预期完成后 ≈519)。

## Global Constraints

- 注释/docstring 全中文;公有函数中文 docstring;类型提示全覆盖;路径一律 pathlib;UTF-8 无 BOM。
- 每任务退出条件: **全量测试绿 + `ruff check .` 零告警**(ruff 钉 0.15.20)。
- 每任务一个中文 conventional commit(格式 `feat(batch-h): … (批次H-Tn)`/`test(batch-h): …`/`docs(batch-h): …`)。
- **不改动任何既有夹具**(`tests/fixtures/server_corpus/` 十余轮语料全 v1,是「v1 路径零回归」哨兵,spec §4);新夹具只增 `synth_v2/`。
- `migration/data/*.yaml` **不动**;若动必须重跑 `python tools/gen_manifest.py` 并提交(本批预计零改动)。
- **版本号只在 T7 收口任务动**(0.10.1 → 0.11.0 双文件:pyproject.toml + migration/__init__.py)。
- 行为承诺红线(spec §5 验收 3): **v1 组合输出与 0.10.0 逐字节一致**——凡触碰 run_diff/resolve_diff_context 的任务,T3 的 v1 零回归锚与十轮语料回归必须全绿才算过。
- `compute_mod_pairs` **函数签名与内部逻辑零改动**(spec §5 验收 4;闸门在调用方)。
- snapshot.load 容错哲学: `mods` 结构级损坏(非 list)降级 `[]` 不抛;元素级(非 dict/缺 `modid`/`jar_filename`)跳过(与 world_dirs 同哲学)。
- 测试文件命名按模块对应;新夹具锚定测试放 `tests/test_synth_v2_anchors.py`。

## Review Focus

1. **v1 路径逐字节回归**: 十轮语料夹具(全 v1)是零回归哨兵——任何任务后 `tests/test_corpus_regression.py` 必须全绿;禁止为通过测试修改既有夹具(归属 T3 的守护依赖,无新增测试)。
2. **混合 v1/v2 不单侧嵌入**: mx_src(v1)+mx_dst(v2) 不可达 → ctx=None 走**老**降级行,不出现新嵌入提示行(spec 行为矩阵第 5 行)——T3 `test_mixed_v1v2_keeps_v1_path_byte_identical`。
3. **same_dir 内容短路不因嵌入松动**: frozen_semantics 对(server.properties 双侧 md5 异、不可达)不得出现 semantics note——嵌入只有名册没有内容,不可达必须退字节比较(spec §7 妥协 4)——T3 `test_frozen_semantics_no_semantics_note`。
4. **load 兼容窗口**: fmt∈{1,2} 接受、3 拒绝且文案保留「请重新 scan」;mods 非 list 降级 []、元素缺键跳过——T2 四个直测。
5. **搬迁等价**: build_ruleset 函数体逐字节等价搬迁(仅缩进/位置变),搬迁后 ruff 无循环依赖告警、`migration.cli.build_ruleset` re-export 可用——T6。
6. **转义不产生新非法路径**: `world\[1\]/**` 是合法 gitignore 模式,只精确匹配 `world[1]/`,不连带 `world1/`——T5 `test_world_glob_escape_scoped`。

---

### Task 1: 夹具 synth_v2 四对(资产)

**Files:**
- Create: `tests/fixtures/server_corpus/synth_v2/_gen.py`(生成脚本,随夹具入库供再生成)
- Create: `tests/fixtures/server_corpus/synth_v2/{junction_pre,junction_post,replay_orphan_src,replay_orphan_dst,frozen_semantics_src,frozen_semantics_dst,mx_src,mx_dst}.snapshot.json`(脚本产出)

**Interfaces:**
- Consumes: `Snapshot.save` 的 payload 键序(snapshot.py:50-61)+ v2 新键 `mods`(T2 落地前先按目标 schema 手写 JSON——夹具是数据,不依赖代码)。
- Produces(T3 锚定测试依赖): 八份 JSON;版本名=文件名去 `.snapshot.json`;`game_root="C:\\fixture\\sanitized"`(不可达,复放语义);四对语义见 spec §4 表。

- [ ] **Step 1: 写生成脚本并落盘**

```python
# tests/fixtures/server_corpus/synth_v2/_gen.py
"""synth_v2 夹具生成:四对 v2 快照(全部合成脱敏,目录不可达=复放语义)。

不引用任何 observations 路径;重跑本脚本幂等重写八份 JSON。
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
ROOT = "C:\\fixture\\sanitized"

def _mod(modid: str, version: str, jar: str) -> dict:
    return {"modid": modid, "version": version, "jar_filename": jar,
            "neoforge_range": None, "embedded_in": None}

def _snap(version: str, scanned_at: str, files: list[dict], mods: list[dict],
          resolved_root: str | None, *, fmt: int = 2) -> dict:
    return {
        "tool_version": "0.10.1", "snapshot_format": fmt, "version": version,
        "game_root": ROOT, "scanned_at": scanned_at, "resolved_root": resolved_root,
        "world_dirs": [], "hash_mode": "tiered", "file_count": len(files),
        "files": files, "mods": mods,
    }

def _f(path: str, size: int, md5: str | None, mtime: int | None = None) -> dict:
    return {"path": path, "size": size, "md5": md5, "mtime": mtime}

def main() -> None:
    shadow = ROOT + "\\versions\\shadow"
    # ① junction 修复黄金锚:同 resolved_root 不同刻;foo 经 registry 配对(1.0→2.0),
    #    jar 家族名 alpha/beta 异 → filename 通道 0.10.0 下也配不出(对照=0 对)
    pre = _snap("junction_pre", "2026-10-01T10:00:00+08:00",
                [_f("mods/alpha-mod.jar", 100, None, 1000),
                 _f("options.txt", 10, "aaaa5055555050505050505050505050")],
                [_mod("foo", "1.0", "alpha-mod.jar")], shadow)
    post = _snap("junction_post", "2026-10-01T12:00:00+08:00",
                 [_f("mods/beta-mod.jar", 120, None, 1100),
                  _f("options.txt", 10, "aaaa5055555050505050505050505050")],
                 [_mod("foo", "2.0", "beta-mod.jar")], shadow)
    # ② 复放保真锚:dst 名册缺 bar(config/bar.toml 约定式映射 → modid bar)→ orphan/never
    ro_src = _snap("replay_orphan_src", "2026-10-01T10:00:00+08:00",
                   [_f("config/bar.toml", 5, "bbbb5050505050505050505050505050"),
                    _f("config/foo.toml", 6, "cccc5050505050505050505050505050"),
                    _f("mods/bar-1.0.jar", 80, None, 900),
                    _f("mods/foo-1.0.jar", 90, None, 900)],
                   [_mod("bar", "1.0", "bar-1.0.jar"), _mod("foo", "1.0", "foo-1.0.jar")],
                   ROOT + "\\versions\\ro_src")
    ro_dst = _snap("replay_orphan_dst", "2026-10-01T11:00:00+08:00",
                   [_f("config/foo.toml", 6, "cccc5050505050505050505050505050"),
                    _f("mods/foo-1.0.jar", 90, None, 950)],
                   [_mod("foo", "1.0", "foo-1.0.jar")],
                   ROOT + "\\versions\\ro_dst")
    # ③ 冻结语义边界锚:双侧嵌入+不可达 → 新提示行;server.properties md5 异 → 无 semantics note
    fs_src = _snap("frozen_semantics_src", "2026-10-01T10:00:00+08:00",
                   [_f("server.properties", 20, "dddd5050505050505050505050505050"),
                    _f("mods/foo-1.0.jar", 90, None, 900)],
                   [_mod("foo", "1.0", "foo-1.0.jar")], ROOT + "\\versions\\fs_src")
    fs_dst = _snap("frozen_semantics_dst", "2026-10-01T12:00:00+08:00",
                   [_f("server.properties", 22, "eeee5050505050505050505050505050"),
                    _f("mods/foo-1.0.jar", 90, None, 950)],
                   [_mod("foo", "1.0", "foo-1.0.jar")], ROOT + "\\versions\\fs_dst")
    # ④ 混合 v1/v2 锚:src 为 v1(删 mods 键、format=1)→ 行为与 v1+v1 逐字节一致
    mx_src = _snap("mx_src", "2026-10-01T10:00:00+08:00",
                   [_f("options.txt", 10, "ffff5050505050505050505050505050")],
                   [_mod("foo", "1.0", "foo-1.0.jar")], ROOT + "\\versions\\mx_src")
    mx_src.pop("mods"); mx_src["snapshot_format"] = 1
    mx_dst = _snap("mx_dst", "2026-10-01T12:00:00+08:00",
                   [_f("options.txt", 12, "abcd5050505050505050505050505050")],
                   [_mod("foo", "1.0", "foo-1.0.jar")], ROOT + "\\versions\\mx_dst")
    for snap in (pre, post, ro_src, ro_dst, fs_src, fs_dst, mx_src, mx_dst):
        p = HERE / f"{snap['version']}.snapshot.json"
        p.write_text(json.dumps(snap, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote", p.name)

if __name__ == "__main__":
    main()
```

Run: `python tests/fixtures/server_corpus/synth_v2/_gen.py`(用主仓 venv 解释器)。

- [ ] **Step 2: 校验资产**

Run: `ls tests/fixtures/server_corpus/synth_v2/*.snapshot.json | wc -l` → 8;抽查 junction_pre.json 含 `"mods"`、mx_src.json 无 `"mods"` 且 `"snapshot_format": 1`。

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/server_corpus/synth_v2/
git commit -m "test(batch-h): synth_v2 四对夹具(junction/复放orphan/冻结语义/混合v1v2)+生成脚本 (批次H-T1)"
```

---

### Task 2: 快照 schema v2——mods 字段/转换对/scanner 接线

**Files:**
- Modify: `migration/snapshot.py:15`(SNAPSHOT_FORMAT 2)、`Snapshot`(字段/save/load)
- Modify: `migration/moddb.py`(ModRegistry.entries + registry_to_dicts/from_dicts)
- Modify: `migration/scanner.py:92-111`(build_snapshot 接线)
- Test: `tests/test_snapshot.py`、`tests/test_moddb.py`

**Interfaces:**
- Consumes: `ModInfo` 五字段(moddb.py:31-46)、`scan_mods(version_dir) -> ModRegistry`(moddb.py:136)。
- Produces(T3/T4 与批次I 依赖):
```python
# snapshot.py
SNAPSHOT_FORMAT = 2
@dataclass
class Snapshot:
    ...
    mods: list[dict] = field(default_factory=list)  # v2 内嵌名册;键=modid/version/
    #   jar_filename/neoforge_range/embedded_in;load 容错见 docstring
# moddb.py
def registry_to_dicts(registry: ModRegistry) -> list[dict]   # 按 modid 升序
def registry_from_dicts(items: list[dict]) -> ModRegistry    # 缺键条目跳过
# ModRegistry 增:
def entries(self) -> list[ModInfo]                            # 按 modid 升序
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_snapshot.py 追加
def test_save_load_v2_roundtrip_mods(tmp_path):
    """v2 快照 save/load 往返:mods 五字段保真;SNAPSHOT_FORMAT==2(spec §5.1)。"""
    from migration.snapshot import SNAPSHOT_FORMAT, Snapshot, FileEntry
    snap = Snapshot(version="v", game_root="g", scanned_at="t", hash_mode="tiered",
                    file_count=1, files=[FileEntry("a", 1, "x" * 32)],
                    mods=[{"modid": "foo", "version": "1.0", "jar_filename": "foo.jar",
                           "neoforge_range": None, "embedded_in": None}])
    snap.save(tmp_path / "s.json")
    assert json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))["snapshot_format"] == 2
    loaded = Snapshot.load(tmp_path / "s.json")
    assert loaded.mods == snap.mods and SNAPSHOT_FORMAT == 2

def test_load_v1_snapshot_mods_empty(tmp_path):
    """v1 快照(无 mods 键)load 正常且 mods==[](spec §5.1)。"""
    payload = json.loads((Path(__file__).parent / "fixtures" / "server_corpus" /
                          "synth_v2" / "mx_src.snapshot.json").read_text(encoding="utf-8"))
    (tmp_path / "v1.json").write_text(json.dumps(payload), encoding="utf-8")
    assert Snapshot.load(tmp_path / "v1.json").mods == []

def test_load_mods_structural_damage_degrades(tmp_path):
    """mods 非 list → 降级 [] 不抛;元素非 dict/缺 modid → 跳过该条(spec §3.1 容错)。"""
    ...  # 构造 mods="garbage" → [] ;mods=[{"modid":"a"},{"junk":1},完整条] → 仅完整条存活

def test_load_format3_rejected(tmp_path):
    """fmt=3 拒绝,文案含「请重新 scan」(spec §3.1)。"""
    ...  # snapshot_format=3 → SnapshotFormatError, "请重新 scan" in str(e)
```

```python
# tests/test_moddb.py 追加
def test_registry_dicts_roundtrip():
    """registry_to_dicts/from_dicts 往返;升序;缺键条目跳过(spec §3.1)。"""
    reg = ModRegistry()
    reg.add(ModInfo("b", "1.0", "b.jar", None))
    reg.add(ModInfo("a", "2.0", "a.jar", "[21.1.219,)"))
    dicts = registry_to_dicts(reg)
    assert [d["modid"] for d in dicts] == ["a", "b"]           # 升序
    assert dicts[0]["neoforge_range"] == "[21.1.219,)"
    back = registry_from_dicts(dicts + [{"modid": "x"}])       # 缺 jar_filename → 跳过
    assert set(back.modids) == {"a", "b"}
    assert back.get("a").version == "2.0"

def test_registry_entries_sorted():
    reg = ModRegistry(); reg.add(ModInfo("b", "1", "b.jar", None)); reg.add(ModInfo("a", "1", "a.jar", None))
    assert [m.modid for m in reg.entries()] == ["a", "b"]
```

```python
# tests/test_scanner.py 追加(或 test_snapshot.py,按现有归属)
def test_build_snapshot_embeds_mods(tmp_path, write_mod_jar):
    """scan 真目录:快照内嵌名册,五字段齐(spec §5.2)。"""
    vd = tmp_path / "versions" / "v"; (vd / "mods").mkdir(parents=True)
    write_mod_jar(vd / "mods" / "foo-1.0.jar", "foo", "1.0")
    snap, errs = Scanner(vd, "v").build_snapshot(str(tmp_path))
    assert not errs
    assert snap.mods and snap.mods[0]["modid"] == "foo"
    assert snap.mods[0]["jar_filename"] == "foo-1.0.jar"
    assert set(snap.mods[0]) == {"modid", "version", "jar_filename",
                                 "neoforge_range", "embedded_in"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_snapshot.py tests/test_moddb.py tests/test_scanner.py -v`
Expected: FAIL(`SNAPSHOT_FORMAT==1`/无 `mods` 字段/无转换函数)。

- [ ] **Step 3: 实现**

- `snapshot.py`: `SNAPSHOT_FORMAT = 2`;`Snapshot.mods: list[dict] = field(default_factory=list)`(docstring 注明容错哲学);`save` payload 增 `"mods": self.mods`(置于 `"files"` 之后);`load` 版本闸门改 `if fmt not in (1, SNAPSHOT_FORMAT)`,mods 解析:

```python
                mods=(
                    [m for m in payload.get("mods", [])
                     if isinstance(m, dict) and m.get("modid") and m.get("jar_filename")]
                    if isinstance(payload.get("mods", []), list) else []
                ),
```

- `moddb.py`(ModRegistry 同文件区):

```python
    def entries(self) -> list[ModInfo]:
        """按 modid 升序返回全部条目(嵌入序列化与测试用)。"""
        return [self._mods[k] for k in sorted(self._mods)]

def registry_to_dicts(registry: ModRegistry) -> list[dict]:
    """注册表 → v2 快照可序列化的 dict 列表(按 modid 升序;五字段与 ModInfo 一一对应)。"""
    return [asdict(m) for m in registry.entries()]

def registry_from_dicts(items: list[dict]) -> ModRegistry:
    """dict 列表 → 注册表;缺 modid/jar_filename 条目跳过(数据级宽松,snapshot.load 同哲学)。"""
    reg = ModRegistry()
    for m in items:
        if not isinstance(m, dict) or not m.get("modid") or not m.get("jar_filename"):
            continue
        reg.add(ModInfo(modid=m["modid"], version=m.get("version", ""),
                        jar_filename=m["jar_filename"],
                        neoforge_range=m.get("neoforge_range"),
                        embedded_in=m.get("embedded_in")))
    return reg
```

(`from dataclasses import asdict` 按需补 import。)
- `scanner.py` build_snapshot(101 行 Snapshot 构造前):

```python
        # 批次H:mod 名册随快照落盘(v2)——名册与 files 同刻同源;scan_mods 对 mods/
        # 缺失与 jar 损坏已有容错(warning+跳过);119 jar 量级预期 +1~3 秒(spec §7.1)
        from .moddb import registry_to_dicts, scan_mods
        mods = registry_to_dicts(scan_mods(self.version_dir))
```

构造参数增 `mods=mods`。(scanner → moddb 顶层依赖,无循环:moddb → snapshot、scanner → {hashing,snapshot,moddb}。)

- [ ] **Step 4: 全量回归(重点 v1 兼容)+ Commit**

Run: `pytest -q && ruff check .` → 全绿(既有快照测试全 v1,load 兼容路径掩护)。

```bash
git add migration/snapshot.py migration/moddb.py migration/scanner.py tests/
git commit -m "feat(batch-h): 快照 schema v2 内嵌 mod 名册+转换对+scanner 接线(容错与 v1 兼容) (批次H-T2)"
```

---

### Task 3: 消费策略 B——冻结通道/提示行/build_plan 接线 + 三黄金锚

**Files:**
- Modify: `migration/pipeline.py`(DiffContext:343-372 增字段;resolve_diff_context:374-404 重写;run_diff:643-651 提示行与 :687-692 配对闸门;build_plan:241-242/274-277 嵌入优先)
- Test: `tests/test_synth_v2_anchors.py`(新)、`tests/test_pipeline.py`

**Interfaces:**
- Consumes: T2 `registry_from_dicts`;`compute_mod_pairs`(零改动)。
- Produces(批次I/复放工作流依赖):
```python
@dataclass(frozen=True)
class DiffContext:
    src_mods: "ModRegistry"; dst_mods: "ModRegistry"
    src_dir: Path; dst_dir: Path
    same_dir: bool = False    # read_file 短路语义不变(物理同目录读数恒等)
    mods_frozen: bool = False # 双侧名册来自快照嵌入(v2)→ 配对闸门改 same_dir and not mods_frozen
# resolve_diff_context(src_snap, dst_snap) -> DiffContext | None:
#   双侧有嵌入 → 冻结通道(目录可达与否不影响 mods 来源;same_dir=活体可达→resolve 比较,
#   否则快照 resolved_root 双侧均有且相等);否则完全走 0.10.0 路径(任一不可达→None)
# run_diff 新增提示行(嵌入+不可达):"[提示] 版本目录不可达,已使用快照内嵌 mod 名册(语义复核退回字节比较)"
# build_plan: src_mods/dst_mods 嵌入优先(scan 回退);配对闸门 same_dir=resolve 相等 and not 双侧嵌入
```

- [ ] **Step 1: 写失败测试(三黄金锚 + 混合回归)**

```python
# tests/test_synth_v2_anchors.py(新)
"""synth_v2 夹具锚定:行为矩阵第 3/4/5 行(spec §3.2/§4)——v1 哨兵由十轮语料回归承担。"""
import shutil
from pathlib import Path

from migration.pipeline import run_diff

FIX = Path(__file__).parent / "fixtures" / "server_corpus" / "synth_v2"

def _run(tmp_path: Path, src: str, dst: str):
    """夹具复放:拷入 tmp/.mcmig/snapshots 后走 run_diff(game_root=None 直取布局)。"""
    snaps = tmp_path / ".mcmig" / "snapshots"
    snaps.mkdir(parents=True)
    for n in (src, dst):
        shutil.copy(FIX / f"{n}.snapshot.json", snaps / f"{n}.snapshot.json")
    return run_diff(tmp_path, src=src, dst=dst, mcmig_dir=tmp_path / ".mcmig")

def test_junction_pair_recovered_via_registry(tmp_path):
    """黄金锚①:同根不同刻(junction 影子根)+双侧 v2 → foo 经 registry 配对(修 F14)。"""
    out = _run(tmp_path, "junction_pre", "junction_post")
    assert len(out.mod_pairs) == 1
    p = out.mod_pairs[0]
    assert (p.modid, p.kind, p.source) == ("foo", "upgrade", "registry")
    assert p.src_files == ["mods/alpha-mod.jar"] and p.dst_files == ["mods/beta-mod.jar"]
    assert (p.src_version, p.dst_version) == ("1.0", "2.0")

def test_replay_orphan_survives_unreachable(tmp_path):
    """黄金锚②:双侧 v2 不可达 → config/bar.toml 落 never(孤儿),非 candidate(修痛点 1)。"""
    out = _run(tmp_path, "replay_orphan_src", "replay_orphan_dst")
    assert any(i.path == "config/bar.toml" for i in out.report.never)
    assert not any(i.path == "config/bar.toml" for i in out.report.candidate)

def test_frozen_semantics_notice_and_no_semantics_note(tmp_path):
    """黄金锚③:嵌入+不可达 → 新提示行出现;md5 异无 semantics note(字节比较,§7 妥协 4)。"""
    out = _run(tmp_path, "frozen_semantics_src", "frozen_semantics_dst")
    assert any("已使用快照内嵌 mod 名册" in n for n in out.notices)
    assert not any("语义等价" in i.note for bucket in
                   ("to_migrate", "candidate") for i in getattr(out.report, bucket))

def test_mixed_v1v2_keeps_v1_path_byte_identical(tmp_path):
    """混合 v1/v2:不可达 → ctx=None 走老降级行,不出现新嵌入行(Review Focus 2)。"""
    out = _run(tmp_path, "mx_src", "mx_dst")
    assert any("mods 扫描不可用(game_root 不可达)" in n for n in out.notices)
    assert not any("内嵌 mod 名册" in n for n in out.notices)

def test_v2_reachable_silent_no_extra_notice(tmp_path, monkeypatch):
    """嵌入+目录可达:静默走冻结通道,无新增提示(输出无扰动,spec §3.2)。"""
    # 活体版目录 + 双侧 v2 快照(手写 game_root 指向 tmp):重扫会覆盖快照——改为
    # 直接构造:tmp 下建 versions/a、versions/b 各放 mods jar,scan 两侧产 v2 快照,
    # 再 run_diff → notices 不含「内嵌」也不含「不可达」
    ...  # conftest write_mod_jar + scan_version 两次 + run_diff(game_root=tmp)
```

```python
# tests/test_pipeline.py 追加
def test_resolve_diff_context_frozen_branch(tmp_path):
    """双侧嵌入 → mods_frozen=True 且 mods 来自嵌入(不触盘);单侧 v1 → 活体/None 老路径。"""
    from migration.pipeline import resolve_diff_context
    from migration.snapshot import Snapshot
    src = Snapshot.load(FIX / "junction_pre.snapshot.json")   # FIX 同上(或相对路径注入)
    dst = Snapshot.load(FIX / "junction_post.snapshot.json")
    ctx = resolve_diff_context(src, dst)
    assert ctx is not None and ctx.mods_frozen is True
    assert set(ctx.src_mods.modids) == {"foo"} and ctx.src_mods.get("foo").version == "1.0"
    assert ctx.same_dir is True          # resolved_root 双侧相等
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_synth_v2_anchors.py tests/test_pipeline.py -v` → FAIL(无 mods_frozen/嵌入通道;junction 锚 0 配对;orphan 落 candidate)。

- [ ] **Step 3: 实现 pipeline.py**

- `DiffContext` 增 `mods_frozen: bool = False`(docstring 注明:read_file 的 same_dir 短路语义不变——嵌入不救内容读取)。
- `resolve_diff_context` 重写(spec §3.2 伪代码直译):

```python
def resolve_diff_context(src_snap: Snapshot, dst_snap: Snapshot) -> DiffContext | None:
    """…(docstring 更新:双侧嵌入走冻结通道,v1 组合逐字节保持 0.10.0 行为)。"""
    for snap in (src_snap, dst_snap):
        if not snap.game_root or not snap.version:
            return None
    src_vdir = Path(src_snap.game_root) / "versions" / src_snap.version
    dst_vdir = Path(dst_snap.game_root) / "versions" / dst_snap.version
    from .moddb import registry_from_dicts, scan_mods
    if src_snap.mods and dst_snap.mods:                      # 冻结通道(双侧 v2)
        live = src_vdir.is_dir() and dst_vdir.is_dir()
        same_dir = (src_vdir.resolve() == dst_vdir.resolve() if live else
                    src_snap.resolved_root is not None
                    and src_snap.resolved_root == dst_snap.resolved_root)
        return DiffContext(
            src_mods=registry_from_dicts(src_snap.mods),
            dst_mods=registry_from_dicts(dst_snap.mods),
            src_dir=src_vdir, dst_dir=dst_vdir,
            same_dir=same_dir, mods_frozen=True)
    if not (src_vdir.is_dir() and dst_vdir.is_dir()):        # v1 路径:逐字节现状
        return None
    return DiffContext(
        src_mods=scan_mods(src_vdir), dst_mods=scan_mods(dst_vdir),
        src_dir=src_vdir, dst_dir=dst_vdir,
        same_dir=src_vdir.resolve() == dst_vdir.resolve())
```

- `run_diff`(643-651 区域)提示行三分支 + 配对闸门(687-692):

```python
    ctx = resolve_diff_context(src_snap, dst_snap)
    orphan_rules: list[rules.Rule] = []
    if ctx is not None:
        orphan_rules = generate_orphan_rules(src_snap.files, ctx.dst_mods, load_mod_config_map())
        # 批次H:嵌入+目录不可达 → 名册照常,仅语义复核退字节比较(向用户说明缺席原因)
        if ctx.mods_frozen and not (ctx.src_dir.is_dir() and ctx.dst_dir.is_dir()):
            notices.append("[提示] 版本目录不可达,已使用快照内嵌 mod 名册(语义复核退回字节比较)")
    else:
        notices.append(
            "[提示] mods 扫描不可用(game_root 不可达):孤儿标注与注册表配对已跳过,文件名配对仍可用"
        )
    ...
    mod_pairs = compute_mod_pairs(
        src_snap, dst_snap,
        ctx.src_mods if ctx is not None else None,
        ctx.dst_mods if ctx is not None else None,
        # 批次H:闸门收敛——嵌入名册来自各自 scan 时刻,物理同目录不同刻也不恒等,配对有意义
        same_dir=(ctx.same_dir and not ctx.mods_frozen) if ctx is not None else False,
    )
```

(orphan/client_only 自动吃到嵌入——`ctx.dst_mods`/双侧即嵌入名册;`diff_identity_notices` **不动点**:回退臂 F27 文案逐字节保留。)
- `build_plan`(241-242 与 274-277):

```python
    from .moddb import registry_from_dicts  # (并入既有 import 组)
    src_mods = registry_from_dicts(src_snap.mods) if src_snap.mods else scan_mods(src_dir)
    dst_mods = registry_from_dicts(dst_snap.mods) if dst_snap.mods else scan_mods(dst_dir)
    ...
    mod_pairs = compute_mod_pairs(
        src_snap, dst_snap, src_mods, dst_mods,
        same_dir=src_dir.resolve() == dst_dir.resolve()
        and not (bool(src_snap.mods) and bool(dst_snap.mods)),
    )
```

(rescan_dst=True 时 dst 快照刚经 scan_version 重扫为 v2 含嵌入,自然一致;compat 检查吃 `src_mods`——`neoforge_range` 在嵌入五字段内,自动生效。)

- [ ] **Step 4: 全量回归(v1 零回归红线)+ Commit**

Run: `pytest -q && ruff check .` → 全绿(十轮语料回归 + 三个新锚)。

```bash
git add migration/pipeline.py tests/
git commit -m "feat(batch-h): 消费策略 B 快照优先——冻结通道/junction 配对修复/复放保真/提示行三分支 (批次H-T3)"
```

---

### Task 4: F32 守卫直测 + world_rename 门槛 + client_only 截断

**Files:**
- Modify: `migration/pipeline.py:449-483`(world_rename_notices 门槛)、`:708-711`(截断,抽 helper)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: T3 后的 `match_client_only_paths(report, ctx, modids, families)`(签名不变)。
- Produces:
```python
# pipeline.py 模块级
_MIN_WORLD_FILES = 5   # 微小世界不评估改名(r14 后可再标定,spec §7 妥协 6)
_CO_MAX_SHOWN = 5      # client_only 警示行最多列示件数
def _format_client_warning(client: set[str]) -> str
    # sorted 后 ≤5 全列;>5 截断 + " …等 N 件";仅 stderr 行,JSON/client_only_paths 不变
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline.py 追加
def test_client_only_embedded_host_guard_negative():
    """F32 守卫直测·负例:嵌入件命中清单但宿主 jar 不在 mods 桶 → 交集守卫挡住,不误报。"""
    from migration.differ import DiffReport, DiffItem
    from migration.moddb import ModInfo, ModRegistry
    from migration.pipeline import DiffContext, match_client_only_paths
    reg = ModRegistry()
    reg.add(ModInfo("damage-engine-neoforge", "1.0", "phys-embedded.jar",
                    None, embedded_in="host.jar"))
    report = DiffReport()  # mods 桶仅含无关 jar(宿主 host.jar 不在——双侧共有不进桶)
    report.mods.append(DiffItem(path="mods/other.jar", src=None, dst=None))
    ctx = DiffContext(src_mods=reg, dst_mods=ModRegistry(),
                      src_dir=Path("x"), dst_dir=Path("y"))
    assert match_client_only_paths(report, ctx, {"damage-engine-neoforge"}, set()) == set()

def test_client_only_embedded_host_guard_positive():
    """F32 守卫直测·正例(对照):宿主在 mods 桶 → 命中 mods/host.jar。"""
    ...  # report.mods 改含 DiffItem(path="mods/host.jar",...) → 断言命中 {"mods/host.jar"}

def test_world_rename_tiny_world_suppressed():
    """1 文件世界 100% 命中 → 无提示(_MIN_WORLD_FILES=5,spec §3.4)。"""
    ...  # 构造 src.world_dirs=["a"] dst=["b"],各 1 文件同子路径同尺寸 → notices == []

def test_world_rename_five_files_still_detects():
    """≥5 文件 90% 命中 → 照常提示(r13 夹具 660 文件回归由既有测试守护)。"""
    ...  # 5 文件 5 命中 → 一条 "a → B" 提示

def test_client_warning_truncated_over_five():
    """警示行 >5 件截断 +「…等 N 件」;≤5 全列(spec §3.6)。"""
    from migration.pipeline import _format_client_warning
    five = {f"mods/m{i}.jar" for i in range(5)}
    assert _format_client_warning(five).endswith("mods/m4.jar") or "等" not in _format_client_warning(five)
    seven = {f"mods/m{i}.jar" for i in range(7)}
    line = _format_client_warning(seven)
    assert "…等 7 件" in line and "m6" not in line.split("…")[0]
```

- [ ] **Step 2: 确认失败** → FAIL(无 `_MIN_WORLD_FILES`/`_format_client_warning`;tiny world 现状发提示)。

- [ ] **Step 3: 实现**

- `world_rename_notices`: `denom = min(len(a_files), len(b_files))` 之后插 `if denom < _MIN_WORLD_FILES: continue`(docstring 注明阈值来源=拍脑袋下限+r14 可标定)。
- `run_diff` 708-711 行改调 `_format_client_warning(client)`:

```python
def _format_client_warning(client: set[str]) -> str:
    """client_only 警示行文案:≤5 全列,>5 截断+计数(仅 stderr,JSON 与集合不变)。"""
    shown = sorted(client)
    parts = ", ".join(shown[:_CO_MAX_SHOWN]) + (
        f" …等 {len(shown)} 件" if len(shown) > _CO_MAX_SHOWN else "")
    return (f"[警示] {len(client)} 件已知客户端 mod(专服启动部署风险,F30): " + parts)
```

- [ ] **Step 4: 全量回归 + Commit**

Run: `pytest -q && ruff check .`。

```bash
git add migration/pipeline.py tests/test_pipeline.py
git commit -m "feat(batch-h): F32 守卫直测+world_rename 最小分母门槛+client_only 警示行截断 (批次H-T4)"
```

---

### Task 5: world_dirs glob 元字符转义

**Files:**
- Modify: `migration/cli.py:227-236`(world 层注入)——函数随 T6 一起搬到 pipeline
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_ruleset(..., world_dirs=...)`(cli.py:170)。
- Produces:
```python
def escape_world_glob(name: str) -> str
    # 对 \ * ? [ ] # ! 逐字符反斜杠转义(gitignore 语义:[]字符类/*?通配/行首#注释、!否定;
    # 非特殊位置转义 pathspec 容忍,全位置转义最简);非法名守卫与提示行用原名,不受影响
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cli.py 追加(或 test_rules.py,按 build_ruleset 现有测试归属)
def test_escape_world_glob_units():
    """转义单元:字符类/通配/行首注释与否定全位置转义(spec §3.5)。"""
    from migration.cli import escape_world_glob
    assert escape_world_glob("world[1]") == "world\\[1\\]"
    assert escape_world_glob("a*b?c") == "a\\*b\\?c"
    assert escape_world_glob("#w") == "\\#w" and escape_world_glob("!w") == "\\!w"
    assert escape_world_glob("普通世界") == "普通世界"

def test_world_glob_escape_scoped():
    """world[1] 注入规则后仅匹配 world[1]/ 下文件,world1/ 不受影响(spec §5.6)。"""
    from migration.cli import build_ruleset
    from migration.rules import Category
    rs, _ = build_ruleset(["s", "d"], exclude=[], include=[], rule_files=[],
                          mcmig_dir=Path("nowhere"), world_dirs=["world[1]"])
    assert rs.classify("world[1]/level.dat") == Category.MUST_MIGRATE
    assert rs.classify("world1/level.dat") != Category.MUST_MIGRATE   # 不被字符类连带
```

(`rs.classify` 的路径参数形态按 rules.py 现有测试用法对齐——正斜杠相对路径。)

- [ ] **Step 2: 确认失败** → FAIL(无 escape_world_glob;world[1] 现状把 world1/ 也吃进)。

- [ ] **Step 3: 实现(cli.py build_ruleset 的 world 层)**

```python
_WORLD_GLOB_SPECIALS = frozenset("\\*?[]#!")

def escape_world_glob(name: str) -> str:
    """转义世界目录名中的 gitignore 元字符(spec §3.5;全位置转义最简,pathspec 容忍)。"""
    return "".join(f"\\{ch}" if ch in _WORLD_GLOB_SPECIALS else ch for ch in name)
```

227-236 行的 `world_layer.append(rules.Rule(match=f"{wd}/**", ...))` 改 `match=f"{escape_world_glob(wd)}/**"`;非法名守卫与 warning 文案保持用原名。

- [ ] **Step 4: 全量回归 + Commit**

Run: `pytest -q && ruff check .`。

```bash
git add migration/cli.py tests/
git commit -m "feat(batch-h): world_dirs glob 元字符转义 escape_world_glob——world[1] 不再连带 world1/ (批次H-T5)"
```

---

### Task 6: build_ruleset 搬迁 pipeline + _LATTICE tuple 化(纯重构)

**Files:**
- Modify: `migration/pipeline.py`(build_ruleset+escape_world_glob 落位;删 :235/:637 两处延迟导入)
- Modify: `migration/cli.py`(删原函数,留 re-export)
- Modify: `migration/moddb.py:699`(`_LATTICE` tuple 化)
- Test: `tests/test_rules.py`、`tests/test_corpus_regression.py`(导入位置)、`tests/test_moddb.py`

**Interfaces:**
- Consumes: T5 后的 `escape_world_glob`。
- Produces:
```python
# migration.pipeline.build_ruleset / escape_world_glob(主位置;函数体逐字节等价搬迁)
# migration.cli: from .pipeline import build_ruleset, escape_world_glob  # 批次H 搬迁,历史位置保兼容
# migration.moddb: _LATTICE: tuple[_Level, ...]
```

- [ ] **Step 1: 先写守卫测试(搬迁前后等价的锚)**

```python
# tests/test_pipeline.py 追加
def test_build_ruleset_importable_from_pipeline():
    """主位置迁至 pipeline;cli re-export 不断裂(spec §5.8)。"""
    from migration import cli, pipeline
    assert pipeline.build_ruleset is cli.build_ruleset      # 同一函数对象
    assert pipeline.escape_world_glob("a[1]") == "a\\[1\\]"
```

- [ ] **Step 2: 确认失败** → FAIL(pipeline 无 build_ruleset)。

- [ ] **Step 3: 搬迁(零行为)**

1. `cli.py` 的 `build_ruleset`+`escape_world_glob`+`_WORLD_GLOB_SPECIALS` 整段剪切,粘入 `pipeline.py`(建议置于 `build_plan` 之前;函数体逐字节不动,仅缩进语境不变——**diff 审查以函数体逐字节等价为准**,spec §3.7);
2. `pipeline.py` 顶部按需补 `from importlib import resources`;删除 `build_plan`(:235)与 `run_diff`(:637)内两处 `from .cli import build_ruleset` 延迟导入,改直调;
3. `cli.py` 原位置留 `from .pipeline import build_ruleset, escape_world_glob  # 批次H 搬迁,历史位置保兼容`(cli 顶层已 import pipeline,无循环);
4. `tests/test_rules.py`、`tests/test_corpus_regression.py` 中 `from migration.cli import build_ruleset` 改 `from migration.pipeline import build_ruleset`;
5. `moddb.py:699`: `_LATTICE: list[_Level] = [` → `_LATTICE: tuple[_Level, ...] = (`,列表字面量闭合 `]` → `)`。

- [ ] **Step 4: 全量回归 + ruff(循环依赖告警必须为 0)+ Commit**

Run: `pytest -q && ruff check .`。

```bash
git add migration/pipeline.py migration/cli.py migration/moddb.py tests/
git commit -m "refactor(batch-h): build_ruleset 搬迁 pipeline(消循环依赖,cli re-export 保兼容)+_LATTICE tuple 化 (批次H-T6)"
```

---

### Task 7: README 双语 + 版本 0.11.0 + 收口回归

**Files:**
- Modify: `README.zh-CN.md`(快照说明段 + 优先级图补世界层)
- Modify: `README.en.md`(同义两点 + Last synced)
- Modify: `pyproject.toml`、`migration/__init__.py`(0.11.0 双文件)
- Test: 无新增(全量回归即验收)

**Interfaces:**
- Consumes: T1-T6 全部。
- Produces: v0.11.0 发布就绪;批次I W1W2 计划的执行前置达成。

- [ ] **Step 1: README.zh-CN.md**

- 「快照」说明段(64 行附近)追加一句: v2 起快照内嵌 mod 名册(scan 时顺带扫 jar,耗时约 +1~3 秒;旧 v1 快照完全兼容,重扫即可升级);
- 「优先级」图(104 行)末尾补一行 `> 世界目录(动态探测)`,并注: 该层垫底——default never 仍可压过,世界内 `.bak` 不迁。

- [ ] **Step 2: README.en.md 同步两点 + `Last synced: v0.11.0`**

- [ ] **Step 3: 版本双文件 0.11.0**

`pyproject.toml:7` 与 `migration/__init__.py:3` 改 `0.11.0`;Run: `.venv/Scripts/python.exe -m migration -V` → `mcmig 0.11.0`(输出格式以现状为准)。

- [ ] **Step 4: 收口全量回归**

Run: `pytest -q && ruff check .`;记录测试总数(预期 481 → ≈519,以实际为准,写入 commit body)。

- [ ] **Step 5: Commit**

```bash
git add README.zh-CN.md README.en.md pyproject.toml migration/__init__.py
git commit -m "docs(batch-h): README 双语 v2 名册说明+世界层图+版本 0.11.0 收口 (批次H-T7)"
```

---

## 计划自审记录

1. **Spec 覆盖**: §3.1(T2)/§3.2(T3)/§3.3+§3.4+§3.6(T4)/§3.5(T5)/§3.7(T6)/§3.8(T7)/§4 夹具(T1)→ 全覆盖;spec §5 验收 10 条逐一有任务对应(1→T2、2→T2、3→T3+语料哨兵、4→T3 闸门收敛零改动、5/6→T4/T5、7→T4、8/9→T6、10→T7)。
2. **占位符扫描**: 无 TBD;T3 `test_v2_reachable_silent_no_extra_notice` 与 T4 两个用例以「构造要点+断言」给出(夹具组合机械,断言完整);T5 `rs.classify` 参数形态标注「按 rules.py 现有用法对齐」并给出正斜杠约定——均为可执行指引非悬空。
3. **类型一致性**: `registry_to_dicts/from_dicts/entries`(T2)在 T3 消费同名;`mods_frozen` 字段 T3 定义、锚定测试断言一致;`escape_world_glob` T5 定义、T6 搬迁后 `pipeline.escape_world_glob` 同名;`_format_client_warning`/`_MIN_WORLD_FILES`/`_CO_MAX_SHOWN` 命名与 docstring 一致。
4. **Review Focus ↔ 测试归属**: 六条分别锚定 T3×3(junction/orphan/frozen+mixed)/T2(load 兼容)/T5(scoped)/T6(同一函数对象断言);第 1 条由既有十轮语料回归承担(明确不改夹具红线)。
5. **两端核对(批次G 教训)**: 夹具 JSON 键序按 `Snapshot.save` payload 逐字对齐;`config/bar.toml` 依赖约定式映射(mod_config_map.yaml 头注约定,无需改 data);junction 夹具 jar 家族名 alpha/beta 异构确保 filename 通道不产生对照噪声;ModPair 断言字段(modid/kind/source/src_files/dst_version)与 moddb.py 类定义逐字核对。
