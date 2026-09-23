# 批次 E:配对完整性与降噪 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 文件名配对补三/四级(F21 版本+尾缀同变、F22 平台装饰词),`--modpack-swap` 保留配对标记(F23/F26),junction 提示门控(F27),autocrlf 三重修(F24),`**/*.bak` 归 never(F28),八/九轮语料黄金对,版本 0.8.0。

**Architecture:** 全部改动收敛在既有模块内:moddb.py 配对函数加两级(仅消费上级残余,一级/二级字节级不变);cli.py 配对输入改快照推导 + 提示门控;reporter.py 补 never 桶标记;data 层一行规则 + manifest 重生成;gen_manifest/doctor 哈希前归一化行尾。

**Tech Stack:** Python 3.11+(.venv)、pytest、rich、pathspec(gitignore 语义 glob)。

**Spec:** `Reference/specs/2026-09-23-batch-e-pairing-completion-denoise-design.md`(含 T6 与 planner 判定法交互定案,执行者必读 §0/§3)

## Global Constraints

- 所有注释/docstring 中文;文件 UTF-8 无 BOM;路径一律 pathlib.Path
- `migration/data/*.yaml` 任何改动后必须重跑 `.venv/Scripts/python tools/gen_manifest.py` 并随同提交 manifest
- `TOOL_VERSION`(migration/snapshot.py,当前 "0.6.0")**不动**——快照格式无变化;版本号 pyproject.toml 与 `migration/__init__.py` 的 `__version__` **两处同步**(`mcmig -V` 读后者,批次D 教训)
- 配对一级/二级语义零变化:20260914/0919/0920 既有黄金 modid/kind 断言不改;三/四级只消费上级残余
- 配对纯标注不改六桶/planner 行为(批次B 妥协不变);唯一桶级行为变更是 T5 的 `**/*.bak`(spec 已定案)
- 测试命令一律 `.venv/Scripts/python -m pytest`(Git Bash;系统 python 无依赖);ruff 检查 `.venv/Scripts/python -m ruff check .`
- 提交信息中文 conventional commits(`feat(diff): …`/`fix(doctor): …`/`test(corpus): …`/`docs: …`)
- 本计划在 worktree `F:\code\mcmigrator-be`(分支 `feat/batch-e`)执行,基于 main(2bd79fb)

## Review Focus

以下五类输入最可能咬人,均已由对应任务的测试钉死:

1. **三级误配歧义家族**:同减尾键一侧 2 候选(如 `x-1.0-a` + `x-1.0-b` 对 `x-2.0-c`)→ 必须整族放弃不得猜 → T1 步骤 1 `test_pair_filename_tier3_ambiguity_guard`
2. **平台词剥离产生空键**(`neoforge-1.0.jar` 家族键纯平台词)→ 空键不得与空键配对 → T1 步骤 1 `test_pair_filename_tier4_empty_stripped_key_skipped`
3. **swap 视角 JSON note 纯度**:配对标记只进 rich 渲染,`--json` 的 `note` 字段必须保持原始值(`modpack_swap`/`target_only`)→ T2 步骤 1 e2e 断言 JSON note
4. **LF 哈希数值不变**:归一化对 LF 文件是 no-op,否则全量 manifest 失效 → T4 步骤 1 `test_sha256_of_lf_equals_repository_hash`
5. **scanned_at 秒级精度抖动**:两次 scan 同秒会翻转门控分支 → 既有 junction 测试必须显式钉死 scanned_at,不得依赖真实时钟 → T3 步骤 3/4

---

### Task 1: moddb 三级/四级文件名配对(F21/F22)

**Files:**
- Modify: `migration/moddb.py`(`pair_mods_by_filename` 及其 docstring;新增 `_PLATFORM_WORDS`/`_platform_stripped`)
- Test: `tests/test_moddb.py`(文件末尾追加)

**Interfaces:**
- Consumes: 既有 `normalize_jar_family(path) -> tuple[family, sig, tail]`、`_reduced_family(family, tail)`(本文件内,不改)
- Produces: `pair_mods_by_filename(src_only, dst_only) -> list[ModPair]` 签名不变,新增 kind 产出路径(三级 upgrade/四级 upgrade|renamed);私有 `_platform_stripped(family: str, tail: str) -> str`(T1 测试直接引用)

- [ ] **Step 1: 写失败测试**

在 `tests/test_moddb.py` 末尾追加(文件顶部已有 `pair_mods_by_filename` 导入则复用,否则补 `from migration.moddb import pair_mods_by_filename`):

```python
# ---- 批次E F21/F22:三级(版本+尾缀同变)与四级(平台词剥离)配对 ----


def test_pair_filename_tier3_version_and_tail_both_changed():
    """F21:版本号与尾缀同时变化 → 减尾键相等 + 版本签名不同 → upgrade(三级)。"""
    src = ["mods/kaleidoscope_world_liquor-1.1.8-neoforge+1.21.1-feature.jar"]
    dst = ["mods/kaleidoscope_world_liquor-1.1.9-neoforge+1.21.1-fix.jar"]
    pairs = pair_mods_by_filename(src, dst)
    assert len(pairs) == 1
    p = pairs[0]
    assert (p.modid, p.kind, p.source) == (
        "kaleidoscope-world-liquor-neoforge", "upgrade", "filename")
    assert (p.src_version, p.dst_version) == ("1.1.8-1.21.1", "1.1.9-1.21.1")


def test_pair_filename_tier3_not_triggered_when_tail_equal():
    """尾缀相同仅版本变 → 一级职责(全家族键相等),modid 仍为全家族键。"""
    pairs = pair_mods_by_filename(["mods/x-1.0-fix.jar"], ["mods/x-2.0-fix.jar"])
    assert [(p.modid, p.kind) for p in pairs] == [("x-fix", "upgrade")]


def test_pair_filename_tier3_ambiguity_guard():
    """三级歧义守卫:减尾键同、一侧多候选 → 整族放弃(不猜)。"""
    src = ["mods/x-1.0-a.jar", "mods/x-1.0-b.jar"]  # 减尾键均为 x
    dst = ["mods/x-2.0-c.jar"]
    assert pair_mods_by_filename(src, dst) == []


def test_platform_stripped_helper():
    """平台词剥离:减尾键内枚举词剥除;纯平台词家族 → 空串(调用方跳过)。"""
    from migration.moddb import _platform_stripped
    assert _platform_stripped("field-emitters-neoforge", "") == "field-emitters"
    assert _platform_stripped("field-emitters-neoforge", "patch") == "field-emitters"
    assert _platform_stripped("kaleidoscope-world-liquor-neoforge", "feature") == \
        "kaleidoscope-world-liquor"
    assert _platform_stripped("neoforge", "") == ""


def test_pair_filename_tier4_platform_word_renamed_style():
    """F22:命名风格全变(去平台中段)→ 剥平台词后相等 + sig 异 → upgrade(四级)。"""
    pairs = pair_mods_by_filename(
        ["mods/field-emitters-neoforge-1.21.1-1.1.0.jar"],
        ["mods/field-emitters-1.2.1.jar"])
    assert len(pairs) == 1
    p = pairs[0]
    assert (p.modid, p.kind, p.source) == ("field-emitters", "upgrade", "filename")
    assert (p.src_version, p.dst_version) == ("1.21.1-1.1.0", "1.2.1")


def test_pair_filename_tier4_same_version_platform_only_rename():
    """四级 sig 相等(仅平台词差)→ renamed。"""
    pairs = pair_mods_by_filename(["mods/x-neoforge-1.0.jar"], ["mods/x-1.0.jar"])
    assert [(p.modid, p.kind) for p in pairs] == [("x", "renamed")]


def test_pair_filename_tier4_empty_stripped_key_skipped():
    """剥平台词后空键(家族键纯平台词)不参与四级配对。"""
    assert pair_mods_by_filename(["mods/neoforge-1.0.jar"], ["mods/mc-2.0.jar"]) == []


def test_pair_filename_tiers_do_not_touch_tier1_2_semantics():
    """零回归哨兵:同版异尾缀(rebuilt)与纯升级(upgrade)判定与 0.7.0 一致。"""
    src = ["mods/compat-2.9.7-Patch-final.jar", "mods/lootr-1.0.jar"]
    dst = ["mods/compat-2.9.7-Patch.jar", "mods/lootr-2.0.jar"]
    by = {p.modid: p for p in pair_mods_by_filename(src, dst)}
    assert by["compat"].kind == "rebuilt"  # 二级(减尾键相等+同签名+异尾缀)
    assert by["lootr"].kind == "upgrade"   # 一级(全家族键相等)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_moddb.py -k "tier3 or tier4 or platform_stripped" -v`
Expected: FAIL(`test_platform_stripped_helper` ImportError;tier3/tier4 断言 0 对)

- [ ] **Step 3: 实现**

`migration/moddb.py`,在 `_reduced_family` 函数之后追加:

```python
# 平台装饰词(F22):上游改命名风格时中段平台词干扰家族键,四级配对前剥除(枚举闭集)
_PLATFORM_WORDS = frozenset({"neoforge", "forge", "fabric", "quilt", "mc", "minecraft"})


def _platform_stripped(family: str, tail: str) -> str:
    """减尾键再剥平台装饰词(第四级配对键);剥后为空返回空串(调用方跳过)。

    Args:
        family: 家族键(纯字母词 "-" 连接,含尾缀词)。
        tail: 变体尾缀(空串表示无)。

    Returns:
        剥离平台词后的减尾键;家族词全为平台词时为空串。
    """
    reduced = _reduced_family(family, tail)
    if not reduced:
        return ""
    return "-".join(w for w in reduced.split("-") if w not in _PLATFORM_WORDS)
```

`pair_mods_by_filename` 改造(三级在二级后、四级收尾;二级命中处补 `paired.update`):

docstring 的两级描述改为四级(`第一级…第二级…第三级(F21)减尾键相等+版本签名不同→upgrade…第四级(F22)减尾键剥平台装饰词后相等→upgrade/renamed`),函数体在现有第二级循环之后追加:

```python
    # 第二级循环内的 rebuilt append 之后补一行(使三级看不到二级已消费的文件):
    #     paired.update((s, d))
    # 第三级(F21):减尾键相等 + 版本签名不同 → upgrade(版本与尾缀同变;
    # 一级因家族键含尾缀词而失配,二级因要求同签名而失配,本级收口)
    for red in sorted(set(r_src) & set(r_dst)):
        s_list = [p for p in r_src[red] if p not in paired]
        d_list = [p for p in r_dst[red] if p not in paired]
        if len(s_list) != 1 or len(d_list) != 1:
            continue  # 歧义放弃,不猜
        s, d = s_list[0], d_list[0]
        if src_norm[s][1] == dst_norm[d][1]:
            continue  # 同签名属第二级语义(此处理论死枝守卫)
        pairs.append(
            ModPair(
                modid=red,
                kind="upgrade",
                src_files=[s], dst_files=[d],
                src_version=src_norm[s][1] or None,
                dst_version=dst_norm[d][1] or None,
                source="filename",
            )
        )
        paired.update((s, d))
    # 第四级(F22):减尾键剥平台装饰词后相等 → upgrade/renamed(作者改命名风格)
    p_src: dict[str, list[str]] = {}
    p_dst: dict[str, list[str]] = {}
    for p, (fam, _, tail) in src_norm.items():
        if p in paired or not fam:
            continue
        stripped = _platform_stripped(fam, tail)
        if stripped:
            p_src.setdefault(stripped, []).append(p)
    for p, (fam, _, tail) in dst_norm.items():
        if p in paired or not fam:
            continue
        stripped = _platform_stripped(fam, tail)
        if stripped:
            p_dst.setdefault(stripped, []).append(p)
    for key in sorted(set(p_src) & set(p_dst)):
        s_list, d_list = p_src[key], p_dst[key]
        if len(s_list) != 1 or len(d_list) != 1:
            continue  # 歧义放弃,不猜
        s, d = s_list[0], d_list[0]
        s_sig, d_sig = src_norm[s][1], dst_norm[d][1]
        pairs.append(
            ModPair(
                modid=key,
                kind="upgrade" if s_sig != d_sig else "renamed",
                src_files=[s], dst_files=[d],
                src_version=s_sig or None, dst_version=d_sig or None,
                source="filename",
            )
        )
```

同时 `ModPair` docstring 的 kind 词汇表补一句三级/四级来源(`upgrade 可来自四级以下路径…`不必,仅在 `pair_mods_by_filename` docstring 说清即可)。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_moddb.py -v`
Expected: 全部 PASS(含既有测试——零回归哨兵)

- [ ] **Step 5: 全量回归 + 提交**

Run: `.venv/Scripts/python -m pytest tests/ -q && .venv/Scripts/python -m ruff check .`
Expected: 406+9=415 passed,ruff 无发现

```bash
git add migration/moddb.py tests/test_moddb.py
git commit -m "feat(moddb): 文件名配对三/四级 — F21 版本+尾缀同变 upgrade/F22 平台装饰词剥离(仅消费上级残余)"
```

---

### Task 2: swap 视角配对通透(F23/F26)

**Files:**
- Modify: `migration/differ.py:52`(`_is_mod` 公开为 `is_mod_jar`,更新文件内唯一调用点 `migration/differ.py:129`)
- Modify: `migration/cli.py:363-366`(配对输入改快照推导;`_cmd_diff` 内 Differ 的导入行补 `is_mod_jar`)
- Modify: `migration/reporter.py:58-74`(`_display_note` 补 never/modpack_swap 分支)
- Test: `tests/test_cli.py`(末尾追加 e2e)、`tests/test_reporter.py`(末尾追加)

**Interfaces:**
- Consumes: Task 1 的 `pair_mods_by_filename`(签名不变)
- Produces: `migration.differ.is_mod_jar(path: str) -> bool`(公开函数,T7 语料测试与后续批次复用)

- [ ] **Step 1: 写失败测试**

`tests/test_cli.py` 末尾追加:

```python
# ---- 批次E F23/F26:swap 视角配对标记保留 ----


def test_diff_modpack_swap_keeps_pairing_markers(tmp_path, monkeypatch, capsys):
    """F23/F26:swap 下配对输入取快照集合差——mod_pairs 非空、渲染保 ⇄upgrade 与汇总行。

    两侧 jar modid 故意不同(模拟作者改名/无活体复放)→ registry 配对为空,
    纯文件名配对路径正是九轮 5/5 复现的盲区。
    """
    game_root = _setup_game(tmp_path, ["old", "new"])
    for v in ("old", "new"):
        d = game_root / "versions" / v / "mods"
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.jar"):
            f.unlink()
    from tests.conftest import write_mod_jar
    write_mod_jar(game_root / "versions" / "old" / "mods" / "a-1.0.jar", "mod_old", "1.0")
    write_mod_jar(game_root / "versions" / "new" / "mods" / "a-2.0.jar", "mod_new", "2.0")
    monkeypatch.chdir(tmp_path)
    from migration.cli import main
    assert main(["scan", "old", "--game-root", str(game_root), "-q"]) == 0
    assert main(["scan", "new", "--game-root", str(game_root), "-q"]) == 0
    capsys.readouterr()

    # --json:mod_pairs 保留(0.7.0 为空),note 字段保持原始值不被标记污染
    assert main(["diff", "old", "new", "--game-root", str(game_root),
                 "--modpack-swap", "--json"]) == 0
    res = capsys.readouterr()
    doc = json.loads(res.out)
    assert len(doc["mod_pairs"]) == 1
    mp = doc["mod_pairs"][0]
    assert (mp["modid"], mp["kind"], mp["source"]) == ("a", "upgrade", "filename")
    notes = {i["path"]: i["note"] for i in doc["buckets"]["mods"]}
    assert notes["mods/a-2.0.jar"] == "target_only"  # JSON note 纯度
    never_notes = {i["path"]: i["note"]
                   for i in doc["buckets"]["never"] if i["note"] == "modpack_swap"}
    assert list(never_notes) == ["mods/a-1.0.jar"]
    assert "换包模式" in res.err

    # rich 渲染:target_only 带 ⇄upgrade + 配对汇总行(0.7.0 均丢失)
    assert main(["diff", "old", "new", "--game-root", str(game_root),
                 "--modpack-swap"]) == 0
    out = capsys.readouterr().out
    assert "target_only ⇄upgrade" in out
    assert "配对: ⇄upgrade ×1" in out
```

`tests/test_reporter.py` 末尾追加:

```python
def test_display_note_never_modpack_swap_gets_pair_marker():
    """F23:never 桶 modpack_swap 条目(--show-never 可见)亦带 ⇄ 配对标记。"""
    report = DiffReport()
    report.never.append(DiffItem(path="mods/a-1.0.jar", src=None, dst=None,
                                 note="modpack_swap"))
    pair = ModPair(modid="a", kind="upgrade", src_files=["mods/a-1.0.jar"],
                   dst_files=["mods/a-2.0.jar"], src_version="1.0", dst_version="2.0",
                   source="filename")
    r = DiffReporter(report, src_version="a", dst_version="b", mod_pairs=[pair])
    assert r._display_note("never", report.never[0]) == "modpack_swap ⇄upgrade"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py::test_diff_modpack_swap_keeps_pairing_markers tests/test_reporter.py::test_display_note_never_modpack_swap_gets_pair_marker -v`
Expected: FAIL(mod_pairs 为空 / note 无标记)

- [ ] **Step 3: 实现**

1. `migration/differ.py`:`def _is_mod(path)` 改名 `def is_mod_jar(path) -> bool`,docstring 改为「是否为 mods 目录下的 jar(按文件名集合处理;CLI 配对输入推导同源,F23)」,调用点 `migration/differ.py:129` 同步改 `is_mod_jar(path)`。
2. `migration/cli.py` `_cmd_diff`:Differ 导入行补 `is_mod_jar`(若 Differ 在函数内导入则同处;在模块顶部则顶部),原 363-366 行:

```python
    filename_pairs = pair_mods_by_filename(
        [i.path for i in report.mods if i.note == "to_add"],
        [i.path for i in report.mods if i.note == "target_only"],
    )
```

替换为:

```python
    # F23/F26:配对输入从快照集合直接推导(与 Differ._is_mod 同源判定)——
    # modpack_swap 只改分桶,不再饿死配对(swap 下 target_only 保留 ⇄upgrade)
    src_paths = {e.path for e in src.files}
    dst_paths = {e.path for e in dst.files}
    filename_pairs = pair_mods_by_filename(
        sorted(p for p in src_paths - dst_paths if is_mod_jar(p)),
        sorted(p for p in dst_paths - src_paths if is_mod_jar(p)),
    )
```

3. `migration/reporter.py` `_display_note`:在 `if bucket == "mods":` 分支之后、`elif bucket == "candidate"` 之前插入:

```python
        elif bucket == "never" and note == "modpack_swap":
            # F23:换包排除的旧 jar 若参与配对,--show-never 视角同样可见 ⇄ 标记
            kind = self._pair_by_path.get(item.path)
            if kind:
                note = f"{note} ⇄{kind}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py tests/test_reporter.py -v`
Expected: 全部 PASS(含批次D 的 swap e2e 既有断言——registry 路径不受影响)

- [ ] **Step 5: 全量回归 + 提交**

Run: `.venv/Scripts/python -m pytest tests/ -q && .venv/Scripts/python -m ruff check .`
Expected: 全绿,ruff 无发现

```bash
git add migration/differ.py migration/cli.py migration/reporter.py tests/test_cli.py tests/test_reporter.py
git commit -m "feat(diff): swap 视角配对通透 — 配对输入改快照推导(F23/F26),is_mod_jar 公开,never 桶 ⇄ 标记"
```

---

### Task 3: junction 提示 scanned_at 门控(F27)

**Files:**
- Modify: `migration/cli.py:361-362`(same_dir 提示分支;模块顶部若无可用的 `log = logging.getLogger(__name__)` 则补——cli.py 已 `import logging`)
- Test: `tests/test_cli.py:631`/`676` 两个 junction 测试钉死 scanned_at + 新增静默测试

**Interfaces:**
- Consumes: `Snapshot.scanned_at: str`(既有字段,ISO 秒级)
- Produces: 无新接口(行为变更:不同刻双快照提示静默为 debug 日志)

- [ ] **Step 1: 写失败测试**

`tests/test_cli.py` 在 junction 测试区域之后追加:

```python
def _force_scanned_at(snapshot_file: Path, value: str) -> None:
    """改写快照 JSON 的 scanned_at(钉死时间戳,消除秒级精度的门控抖动,F27)。"""
    doc = json.loads(snapshot_file.read_text(encoding="utf-8"))
    doc["scanned_at"] = value
    snapshot_file.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def test_diff_junction_same_dir_different_scan_times_hint_silent(
        tmp_path, monkeypatch, capsys):
    """F27:不同刻双快照(标准影子根用法)降级提示静默;同刻保留提示。"""
    import subprocess
    import os
    from tests.conftest import write_mod_jar

    root = tmp_path / "root"
    va = root / "versions" / "a"
    (va / "mods").mkdir(parents=True)
    write_mod_jar(va / "mods" / "x-1.0.jar", "x", "1.0")
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J",
                        str(root / "versions" / "b"), str(va)],
                       check=True, capture_output=True)
    else:
        (root / "versions" / "b").symlink_to(va, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    from migration.cli import main

    assert main(["scan", "a", "--game-root", str(root), "-q"]) == 0
    capsys.readouterr()
    (va / "mods" / "x-1.0.jar").unlink()
    write_mod_jar(va / "mods" / "x-2.0.jar", "x", "2.0")
    assert main(["scan", "b", "--game-root", str(root), "-q"]) == 0
    capsys.readouterr()
    # 钉死不同刻(实际两次 scan 可能同秒,显式改写消除抖动)
    _force_scanned_at(snapshot_path(root, "a"), "2026-09-23T11:00:00+08:00")
    _force_scanned_at(snapshot_path(root, "b"), "2026-09-23T11:40:00+08:00")

    assert main(["diff", "a", "b", "--json", "--game-root", str(root)]) == 0
    captured = capsys.readouterr()
    assert len(json.loads(captured.out)["mod_pairs"]) == 1  # 文件名配对照常
    assert "junction" not in captured.err   # 提示静默(F27)
    assert "语义复核" not in captured.err

    # 同刻(疑似自比对错误)→ 提示保留
    _force_scanned_at(snapshot_path(root, "b"), "2026-09-23T11:00:00+08:00")
    assert main(["diff", "a", "b", "--json", "--game-root", str(root)]) == 0
    captured = capsys.readouterr()
    assert "junction" in captured.err
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py::test_diff_junction_same_dir_different_scan_times_hint_silent -v`
Expected: FAIL(第一个断言处 `junction` 仍在 err——0.7.0 恒提示)

- [ ] **Step 3: 实现**

`migration/cli.py` `_cmd_diff` 中原分支:

```python
    elif ctx is not None and ctx.same_dir:
        _print_err("[提示] 两侧版本目录指向同一路径(junction?):注册表配对与语义复核不可用,已使用文件名配对/字节比较")
```

替换为(模块无 logger 时在 import 区后补 `log = logging.getLogger(__name__)`):

```python
    elif ctx is not None and ctx.same_dir:
        # F27:不同刻双快照=「同目录前后两时刻」标准影子根用法,提示恒噪声 → 降 debug;
        # 同刻=疑似自比对错误 → 保留 stderr 提示
        if src.scanned_at == dst.scanned_at:
            _print_err(
                "[提示] 两侧快照同刻且版本目录指向同一路径(junction 同体):"
                "注册表配对与语义复核不可用,已使用文件名配对/字节比较"
            )
        else:
            log.debug(
                "junction 同体双快照(不同刻,标准影子根用法):"
                "注册表配对与语义复核不可用,已使用文件名配对/字节比较"
            )
```

- [ ] **Step 4: 既有 junction 测试钉死 scanned_at(防抖)**

`tests/test_cli.py:631` `test_diff_junction_same_dir_orphan_ok_registry_pairs_skipped` 与 `tests/test_cli.py:676` `test_diff_junction_same_dir_semantic_recheck_falls_back_to_bytes`:两个测试在 `scan b` 之后、`diff` 之前各加一行,把 b 快照 scanned_at 钉成与 a 相同(保持「同刻→提示在」的既有断言确定性):

```python
    # F27:钉死同刻(两次 scan 可能同秒也可能异秒,显式改写消除抖动)
    _force_scanned_at(snapshot_path(root, "b"),
                      json.loads(snapshot_path(root, "a").read_text(encoding="utf-8"))["scanned_at"])
```

(`_force_scanned_at` helper 已在 Step 1 定义于同文件;`snapshot_path` 顶部已导入。)

- [ ] **Step 5: 跑测试确认通过 + 全量回归 + 提交**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -k junction -v && .venv/Scripts/python -m pytest tests/ -q && .venv/Scripts/python -m ruff check .`
Expected: junction 三测全绿;全量全绿

```bash
git add migration/cli.py tests/test_cli.py
git commit -m "feat(diff): junction 降级提示 scanned_at 门控 — 不同刻双快照静默为 debug,同刻保留(F27)"
```

---

### Task 4: autocrlf 三重修(F24)

**Files:**
- Create: `.gitattributes`(仓库根,首件)
- Modify: `tools/gen_manifest.py:28-33`(`sha256_file` 归一化)
- Modify: `migration/doctor.py:38-46`(`_sha256_of` 归一化)
- Test: `tests/test_doctor.py`(末尾追加)

**Interfaces:**
- Consumes: 无
- Produces: `sha256_file`/`_sha256_of` 行尾归一化语义(CRLF→LF 后哈希);`.gitattributes` 锁 `migration/data/**` 检出行尾

- [ ] **Step 1: 写失败测试**

`tests/test_doctor.py` 末尾追加(顶部已 `import doctor` 风格沿用;`hashlib`/`Path` 按需补导入):

```python
# ---- 批次E F24:autocrlf 行尾伪差容忍 ----


def test_verify_manifest_tolerates_crlf_checkout(tmp_path, monkeypatch):
    """F24:manifest(LF 提交字节)对 CRLF 检出工作区不误报损坏。

    直接复刻八轮事故形态:仓库 manifest 原文 + 数据文件整体 CRLF 化。
    """
    real = doctor._data_dir()
    manifest = (real / "manifest.sha256").read_text(encoding="utf-8")
    for name in doctor._parse_manifest(manifest):
        (tmp_path / name).write_bytes(
            (real / name).read_bytes().replace(b"\n", b"\r\n"))
    (tmp_path / "manifest.sha256").write_text(manifest, encoding="utf-8")
    monkeypatch.setattr(doctor, "_data_dir", lambda: tmp_path)
    assert doctor.verify_data_manifest() == []


def test_sha256_of_lf_file_equals_repository_hash():
    """归一化对 LF 文件是 no-op:哈希数值与仓库 manifest 完全一致(防全量重生成)。"""
    real = doctor._data_dir()
    expected = doctor._parse_manifest(
        (real / "manifest.sha256").read_text(encoding="utf-8"))
    for name, want in expected.items():
        assert doctor._sha256_of(real / name) == want


def test_gen_manifest_sha256_file_normalizes_crlf(tmp_path):
    """生成侧同语义:CRLF 文件哈希 == 其 LF 归一化内容的哈希。"""
    import hashlib
    import importlib.util
    gm_path = Path(__file__).resolve().parents[1] / "tools" / "gen_manifest.py"
    spec = importlib.util.spec_from_file_location("gen_manifest", gm_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    f = tmp_path / "a.yaml"
    f.write_bytes(b"version: 1\nrules: []\r\n")
    assert mod.sha256_file(f) == hashlib.sha256(b"version: 1\nrules: []\n").hexdigest()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_doctor.py -k "crlf or lf_file or normalizes" -v`
Expected: `test_verify_manifest_tolerates_crlf_checkout` FAIL(4 件「损坏」);另两个 PASS(基线成立)

- [ ] **Step 3: 实现**

1. 新建 `.gitattributes`:

```
# 数据清单按 LF 字节哈希生成/校验;锁检出行尾,防 autocrlf 伪差(F24)
migration/data/*.yaml text eol=lf
migration/data/manifest.sha256 text eol=lf
```

2. `tools/gen_manifest.py` `sha256_file` 改为:

```python
def sha256_file(path: Path) -> str:
    """计算文件 SHA-256(hex 小写;CRLF→LF 归一化后哈希,F24)。

    归一化使 LF 提交字节与 autocrlf=true 检出的 CRLF 工作区算出同一哈希;
    对 LF 文件是 no-op,故 manifest 数值与既有清单一致,无需全量重生成。
    """
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk.replace(b"\r\n", b"\n"))
    return digest.hexdigest()
```

3. `migration/doctor.py` `_sha256_of` 同样改造(docstring 注明「CRLF→LF 归一化后哈希,与 tools/gen_manifest.py 同语义(F24)」,循环体同上 `chunk.replace(b"\r\n", b"\n")`)。

- [ ] **Step 4: 跑测试确认通过 + doctor 实机验证**

Run: `.venv/Scripts/python -m pytest tests/test_doctor.py -v && .venv/Scripts/python -m pytest tests/ -q && .venv/Scripts/python -m ruff check .`
Expected: 全绿(本任务不改 data yaml,manifest 数值不变,无需重生成)

- [ ] **Step 5: 提交**

```bash
git add .gitattributes tools/gen_manifest.py migration/doctor.py tests/test_doctor.py
git commit -m "fix(doctor): autocrlf 行尾伪差三重修 — .gitattributes 锁 LF + 生成/校验两侧归一化哈希(F24)"
```

---

### Task 5: `**/*.bak` 默认 never 规则(F28)

**Files:**
- Modify: `migration/data/default_rules.yaml`(never 列表追加)
- Regenerate: `migration/data/manifest.sha256`(`.venv/Scripts/python tools/gen_manifest.py`)
- Test: `tests/test_corpus_regression.py`(ROUNDS 数字 + 跨轮测试数字更新 + 新判定法测试)、`tests/test_e2e.py:242-243`(bak_file 断言改 never/skip + 父提升断言)

**Interfaces:**
- Consumes: Task 4 的归一化哈希(regen 后 manifest 与 LF 字节一致)
- Produces: 默认规则层 `.bak` → never(planner pass-2 `.bak` 机制保留,用户规则提升路径仍可达)

- [ ] **Step 1: 写失败测试**

`tests/test_corpus_regression.py`:① ROUNDS 字典五个条目的期望值按下表整体替换(20260920 不变);② `test_corpus_r3_evolution_fully_explained` 的 identical 636→593、never 37→80;③ `test_corpus_r3_pure_config_drift_golden` 的 identical 748→705、never 37→80;④ 末尾追加判定法保持测试:

| 轮 | to_migrate | candidate | mods | only_in_dst | identical | never |
|----|----|----|----|----|----|----|
| 20260908 | 257(不变) | 5→**3** | 130(不变) | 1(不变) | 636→**592** | 143→**189** |
| 20260912 | 550(不变) | 7→**6** | 122(不变) | 0(不变) | 631→**588** | 149→**193** |
| 20260914 | 12(不变) | 1(不变) | 117(不变) | 0(不变) | 737→**694** | 41→**84** |
| 20260914b | 0(不变) | 0(不变) | 112(不变) | 0(不变) | 750→**707** | 47→**90** |
| 20260919 | 11(不变) | 16→**14** | 120(不变) | 8→**2** | 1126→**1085** | 55→**104** |

```python
def test_corpus_bak_files_in_never_and_judgement_intact():
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
```

`tests/test_e2e.py:242-243` 原断言:

```python
    assert origins.get("config/create-1.toml.bak") == "bak_file"
    assert behaviors.get("config/create-1.toml.bak") == "copy"
```

替换为:

```python
    # F28(批次E):.bak 本体归 never 默认规则 → plan 为 skip/never;
    # 判定法父提升不受影响(读快照全集):create.toml 仍 config_modified/copy
    assert origins.get("config/create-1.toml.bak") == "never"
    assert behaviors.get("config/create-1.toml.bak") == "skip"
    assert origins.get("config/create.toml") == "config_modified"
    assert behaviors.get("config/create.toml") == "copy"
```

(若 `config/create.toml` 原断言已存在则只补缺;`config_modified` 是 Origin 枚举的既有值,先查 `migration/planner.py` Origin 定义确认字符串。)

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_corpus_regression.py tests/test_e2e.py -q`
Expected: 桶锚定数字与 bak_file 断言 FAIL(规则未改)

- [ ] **Step 3: 实现 + 重生成 manifest**

`migration/data/default_rules.yaml` never 列表末尾(`mods_*/**` 两行之后)追加:

```yaml
  - "**/*.bak"                        # 配置备份(-N 世代累积不清理;F28):标记物本体不迁,
                                     # 判定法父提升不受影响(planner 读快照全集);用户规则可覆盖
```

然后:

```bash
.venv/Scripts/python tools/gen_manifest.py
```

并确认 `git diff migration/data/manifest.sha256` 仅 default_rules.yaml 一行哈希变化。

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `.venv/Scripts/python -m pytest tests/ -q && .venv/Scripts/python -m ruff check .`
Expected: 全绿(test_planner.py 直调 Planner 的 pass-2 测试不受规则层影响,应原样通过;若有 e2e 其他 .bak 断言残留,按同一语义更新并在提交信息列出)

- [ ] **Step 5: 提交**

```bash
git add migration/data/default_rules.yaml migration/data/manifest.sha256 tests/test_corpus_regression.py tests/test_e2e.py
git commit -m "feat(rules): **/*.bak 归默认 never — F28 降噪;判定法父提升保持(planner 读快照全集),桶锚定随迁"
```

---

### Task 6: 八/九轮语料黄金对(T7 资产化)

**Files:**
- Create: `tests/fixtures/server_corpus/20260921/r8_pre.json`、`r8_post.json`
- Create: `tests/fixtures/server_corpus/20260923/r9_pre.json`、`r9_post.json`
- Test: `tests/test_corpus_regression.py`(末尾追加两个黄金测试)

**Interfaces:**
- Consumes: Task 1 的三/四级配对、Task 2 的 swap 推导语义(`_mods_jar_paths` 快照差集)
- Produces: 黄金夹具(后续批次回归基线)

- [ ] **Step 1: 写失败测试**

`tests/test_corpus_regression.py` 末尾追加:

```python
def test_corpus_20260921_three_tiers_and_bucket_rebuilt():
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


def test_corpus_20260923_five_upgrades_and_swap_keeps_pairs():
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_corpus_regression.py -k 20260921 -v`
Expected: FAIL(FileNotFoundError 夹具缺失)

- [ ] **Step 3: 创建夹具(真值取自交付包 diff JSON 的 size 字段)**

`tests/fixtures/server_corpus/20260921/r8_pre.json`:

```json
{
 "tool_version": "0.6.0",
 "snapshot_format": 1,
 "version": "r8-pre",
 "game_root": "C:\\fixture\\sanitized",
 "scanned_at": "2026-09-21T22:15:00+08:00",
 "hash_mode": "tiered",
 "file_count": 5,
 "files": [
  {"path": "mods/[森罗酒馆：世界名酒] kaleidoscope_world_liquor-1.1.8-neoforge+1.21.1-feature.jar", "size": 6772207, "md5": null},
  {"path": "mods/field-emitters-neoforge-1.21.1-1.1.0.jar", "size": 501878, "md5": null},
  {"path": "mods/lootr-neoforge-1.21.1-1.11.38.125.jar", "size": 1000464, "md5": null},
  {"path": "mods/[灾变] L_Ender's Cataclysm 1.21.1-3.33.jar", "size": 73375661, "md5": null},
  {"path": "mods/[机械动力] create-1.21.1-6.0.10.jar", "size": 777777, "md5": null}
 ]
}
```

`r8_post.json`(version "r8-post",scanned_at "2026-09-21T22:35:00+08:00",file_count 5):

```json
{
 "tool_version": "0.6.0",
 "snapshot_format": 1,
 "version": "r8-post",
 "game_root": "C:\\fixture\\sanitized",
 "scanned_at": "2026-09-21T22:35:00+08:00",
 "hash_mode": "tiered",
 "file_count": 5,
 "files": [
  {"path": "mods/[森罗酒馆：世界名酒] kaleidoscope_world_liquor-1.1.9-neoforge+1.21.1-fix.jar", "size": 6783865, "md5": null},
  {"path": "mods/field-emitters-1.2.1.jar", "size": 789839, "md5": null},
  {"path": "mods/lootr-neoforge-1.21.1-1.11.38.126.jar", "size": 1034358, "md5": null},
  {"path": "mods/[灾变] L_Ender's Cataclysm 1.21.1-3.33.jar", "size": 73375667, "md5": null},
  {"path": "mods/[机械动力] create-1.21.1-6.0.10.jar", "size": 777777, "md5": null}
 ]
}
```

`tests/fixtures/server_corpus/20260923/r9_pre.json`:

```json
{
 "tool_version": "0.6.0",
 "snapshot_format": 1,
 "version": "r9-pre",
 "game_root": "C:\\fixture\\sanitized",
 "scanned_at": "2026-09-23T11:37:51+08:00",
 "hash_mode": "tiered",
 "file_count": 6,
 "files": [
  {"path": "mods/[森罗物语：厨房] kaleidoscopecookery-1.5.0-neoforge+mc1.21.1.jar", "size": 3486047, "md5": null},
  {"path": "mods/resource_replicator-1.1.1-mc1.21.1-neoforge.jar", "size": 434504, "md5": null},
  {"path": "mods/alltheleaks-1.1.12+1.21.1-neoforge.jar", "size": 562895, "md5": null},
  {"path": "mods/irons_spellbooks-1.21.1-3.14.8.jar", "size": 12529766, "md5": null},
  {"path": "mods/mekltgt-1.5.3.jar", "size": 357923, "md5": null},
  {"path": "mods/[机械动力] create-1.21.1-6.0.10.jar", "size": 777777, "md5": null}
 ]
}
```

`r9_post.json`(version "r9-post",scanned_at "2026-09-23T11:40:08+08:00",file_count 6):

```json
{
 "tool_version": "0.6.0",
 "snapshot_format": 1,
 "version": "r9-post",
 "game_root": "C:\\fixture\\sanitized",
 "scanned_at": "2026-09-23T11:40:08+08:00",
 "hash_mode": "tiered",
 "file_count": 6,
 "files": [
  {"path": "mods/[森罗物语：厨房] kaleidoscopecookery-1.5.1-neoforge+mc1.21.1.jar", "size": 3486612, "md5": null},
  {"path": "mods/[资源复制机] resource_replicator-1.1.2-mc1.21.1-neoforge.jar", "size": 434596, "md5": null},
  {"path": "mods/alltheleaks-1.1.13+1.21.1-neoforge.jar", "size": 565062, "md5": null},
  {"path": "mods/irons_spellbooks-1.21.1-3.15.4.jar", "size": 13529099, "md5": null},
  {"path": "mods/mekltgt-1.5.4.jar", "size": 448037, "md5": null},
  {"path": "mods/[机械动力] create-1.21.1-6.0.10.jar", "size": 777777, "md5": null}
 ]
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_corpus_regression.py -v`
Expected: 全部 PASS(含既有六轮+0920 黄金——零回归)

- [ ] **Step 5: 全量回归 + 提交**

Run: `.venv/Scripts/python -m pytest tests/ -q && .venv/Scripts/python -m ruff check .`
Expected: 全绿

```bash
git add tests/fixtures/server_corpus/20260921 tests/fixtures/server_corpus/20260923 tests/test_corpus_regression.py
git commit -m "test(corpus): 八/九轮语料夹具 — F21 三级/F22 四级黄金对 + r9 五升级零扰动 + swap 配对不丢锚定"
```

---

### Task 7: 文档 + 版本 0.8.0(T8)

**Files:**
- Modify: `pyproject.toml`(0.7.0→0.8.0)
- Modify: `migration/__init__.py`(`__version__` 0.7.0→0.8.0)
- Modify: `README.zh-CN.md` / `README.en.md`(配对四级、swap 保留标记、.bak never、doctor 行尾归一化、junction 提示门控;Last synced v0.8.0)
- Create(本地不入库,`.gitignore` 已含 `/Reference/observations/`):`Reference/observations/mcmigrator_服务端测试_20260921/README.md`、`Reference/observations/mcmigrator_服务端测试_20260923/README.md`

**Interfaces:**
- Consumes: 前六任务全部落地的行为
- Produces: v0.8.0 发布态

- [ ] **Step 1: 版本号两处同步**

`pyproject.toml` `version = "0.7.0"` → `"0.8.0"`;`migration/__init__.py` `__version__ = "0.7.0"` → `"0.8.0"`。验证:`.venv/Scripts/python -m migration -V` 或 `.venv/Scripts/mcmig -V` 输出 0.8.0。

- [ ] **Step 2: README 双语更新**

`README.zh-CN.md`(英文版逐条对译):

1. 「diff 的 mods 桶语义与配对」节(130 行附近):配对种类描述后补三级/四级来源——
   「文件名配对共四级兜底:①全名同族 ②剥变体尾缀(同版本→rebuilt)③剥尾缀后版本升级(如 `1.1.8-feature`→`1.1.9-fix`)④再剥 `neoforge/forge/fabric/mc` 等平台装饰词(作者改命名风格);上级配不上的才进下一级,registry(modid)配对始终优先」
2. `--modpack-swap` 行(55 行)与 88 行换包排除行:补「换包视角保留 `⇄` 配对标记——新 mod 标 `⇄upgrade` 表示是升级而非全新增」
3. never 规则示例区(146 行附近的 JVM 崩溃残留 bullet 旁):补「`*.bak` 配置备份(世代累积标记物,本体不迁;判定法不受影响)」
4. junction 说明(141 行):补「双快照不同时刻(标准影子根用法)不再提示,同刻自比对仍提醒」
5. 开发/贡献相关段落(若有):补一句「数据文件哈希按 LF 归一化生成/校验,`autocrlf=true` 检出不会误报(F24)」
6. 文末同步标记 `Last synced v0.8.0`

- [ ] **Step 3: 观察目录索引(本地文件,不提交)**

两个 README.md 各 10 行内:指向交付包内 `报告-F21.md`/`报告-F25.md`、时间线、diff JSON;20260923 的补一行「F25 NTFS 隧道 CreationTime 失真:启动验证脚本判新会话须用 debug.log 首行时间戳或 LastWriteTime,勿用 CreationTime(运维侧知识)」。

- [ ] **Step 4: 全量回归 + 提交**

Run: `.venv/Scripts/python -m pytest tests/ -q && .venv/Scripts/python -m ruff check .`
Expected: 全绿(若存在版本号断言测试,随本任务有意更新)

```bash
git add pyproject.toml migration/__init__.py README.zh-CN.md README.en.md
git commit -m "docs: 批次E 收尾 — 配对四级/swap 标记/bak·junction·autocrlf 说明 + 版本 0.8.0"
```

---

## 自查记录(计划完成后)

- Spec 覆盖:E1→T1、E2→T2(计划 Task 1)、E3→Task 2、E4→Task 4、E5→Task 3、E6→Task 5、语料→Task 6、文档/版本→Task 7;spec §3 T3/T4 编号与本计划 Task 2/Task 3 对应(命名避让 differ 的 T 序)
- 占位符扫描:无 TBD/TODO;所有测试与实现代码完整给出
- 类型一致:`is_mod_jar(path: str) -> bool`、`_platform_stripped(family: str, tail: str) -> str`、`pair_mods_by_filename` 签名不变;`_force_scanned_at(snapshot_file: Path, value: str)` 在 Task 3 定义后供两个既有测试复用
- Review Focus 五项均有归属测试(见各条标注)
