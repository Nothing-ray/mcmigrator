# 批次 D 实现计划:变体尾缀配对 / diff 换装通透 / .mcmig 锚定 game-root

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 spec 的 D1(F20-3 同版本尾缀变体配对 kind=rebuilt)、D2(diff --modpack-swap)、D3(.mcmig 生成物锚定 game-root)、D5(文档),发布 0.7.0。

**Architecture:** moddb 归一化扩为三元组并做两级匹配(第一级全键不变,第二级减尾键新增 rebuilt);registry 配对同判对齐;cli 的 diff 通透换装开关;pipeline.build_plan 增 data_dir 参数分离「规则层 .mcmig(CWD)」与「生成物 .mcmig(game_root)」,读取带旧布局回退。

**Tech Stack:** Python 3.11+/pytest/ruff,无新增依赖。

**Spec:** `Reference/specs/2026-09-20-batch-d-variant-pair-diffswap-outdir-design.md`(拍板①rebuilt ②自动锚定 game-root ③范围 D1+D2+D3+D5;§5 既有测试有意更新清单;§7 妥协)

## Global Constraints

- 所有注释/docstring 中文;文件 UTF-8 无 BOM;路径一律 pathlib.Path
- `migration/data/*.yaml` 本批**零改动** → 无需重跑 `tools/gen_manifest.py`
- 既有测试改动仅限本计划各任务列出的有意更新;全量 pytest 基线 387 项全绿(实现前确认)
- 版本号只改 `pyproject.toml`(0.6.3→0.7.0);`migration/snapshot.py` 的 `TOOL_VERSION="0.6.0"` **不动**(快照格式戳,近三批未随包版本走,本批格式未变——对 spec T6「TOOL_VERSION 同步」的更正)
- 每任务 TDD:先写测试看红,再实现看绿;提交信息 conventional commits 中文
- 工作目录 `F:\code\mcmigrator`;venv `.venv`(测试命令用 `python -m pytest`,或直接 `pytest` 若 PATH 可用)

---

### Task 1: normalize_jar_family 三元组 + 减尾键辅助

**Files:**
- Modify: `migration/moddb.py:501-522`(`_TAG_PREFIX_RE`/`normalize_jar_family`)
- Test: `tests/test_moddb.py`(710 行起 normalize 测试区,追加不改动既有断言——既有用例按索引取值,3 元组兼容;若有解包 2 元组的写法就地改 3 元组)

**Interfaces:**
- Produces: `normalize_jar_family(jar_rel_path: str) -> tuple[str, str, str]` 返回 `(family, version_sig, tail)`;`_reduced_family(family: str, tail: str) -> str`(私有,Task 2 消费)。family/sig 计算规则**不变**(家族键仍含尾缀词——零回归关键),tail 为新增第三元。

- [ ] **Step 1: 写失败测试**(追加到 test_moddb.py normalize 测试区)

```python
def test_normalize_jar_family_tail_extraction():
    """批次D F20-3:最后一个含数字词之后的连续纯字母词 = 变体尾缀(-Patch/-feature)。"""
    from migration.moddb import normalize_jar_family as nf

    # 经典案例:kaleidoscope_compat 2.9.7 → 2.9.7-Patch
    fam_old, sig_old, tail_old = nf("mods/[森罗物语：兼容] kaleidoscope_compat-2.9.7-neoforge+mc1.21.1.jar")
    fam_new, sig_new, tail_new = nf("mods/[森罗物语：兼容] kaleidoscope_compat-2.9.7-neoforge+mc1.21.1-Patch.jar")
    assert tail_old == ""
    assert tail_new == "patch"
    assert fam_old == "kaleidoscope-compat-neoforge"          # 家族键规则不变
    assert fam_new == "kaleidoscope-compat-neoforge-patch"    # 尾缀词仍在家族键内
    assert sig_old == sig_new == "2.9.7-mc1.21.1"

    # 多词尾缀 + -feature 案例
    assert nf("mods/x-1.0-Patch-final.jar")[2] == "patch-final"
    assert nf("mods/kaleidoscope_world_liquor-1.1.8-neoforge+1.21.1-feature.jar")[2] == "feature"

    # 无数字词 / 尾缀位在版本词前 → 空(退化安全)
    assert nf("mods/somejar.jar") == ("somejar", "", "")
    assert nf("mods/x-neoforge-1.21.1.jar")[2] == ""  # neoforge 在最后一个数字词之前,不是尾缀


def test_reduced_family_strips_tail_words():
    """减尾键:家族键剥掉尾部尾缀词(第二级匹配基础);tail 空 → 原样。"""
    from migration.moddb import _reduced_family

    assert _reduced_family("kaleidoscope-compat-neoforge-patch", "patch") == "kaleidoscope-compat-neoforge"
    assert _reduced_family("x-patch-final", "patch-final") == "x"
    assert _reduced_family("kaleidoscope-compat-neoforge", "") == "kaleidoscope-compat-neoforge"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_moddb.py -k "tail_extraction or reduced_family" -v`
Expected: FAIL(`ValueError: too many values to unpack` 或 `ImportError: _reduced_family`)

- [ ] **Step 3: 实现**(moddb.py,替换 normalize_jar_family 全函数,新增 _reduced_family)

```python
def normalize_jar_family(jar_rel_path: str) -> tuple[str, str, str]:
    """jar 相对路径 → (家族键, 版本签名, 变体尾缀),文件名配对的归一化基础(F4/F20-3)。

    家族键 = 剥 [中文标签] 前缀、小写、按 -_+ 切词后仅保留纯字母词(**含尾缀词,规则不变**);
    版本签名 = 含数字的词按序拼接(区分 upgrade/renamed 用);
    变体尾缀 = 最后一个含数字词**之后**的连续纯字母词(如 -Patch/-feature),无则空串。
    家族键仍含尾缀词是两级匹配零回归的关键:第一级全键配对行为与 0.6.3 完全一致,
    尾缀只作为第二级(减尾键)配对的新信息。

    Args:
        jar_rel_path: 版本内相对路径(正斜杠,如 "mods/[标签] x-1.0.jar")。

    Returns:
        (家族键, 版本签名, 变体尾缀);家族键可能为空串(调用方需跳过)。
    """
    name = jar_rel_path.rsplit("/", 1)[-1]
    name = _TAG_PREFIX_RE.sub("", name)
    stem = name.rsplit(".", 1)[0].lower()
    tokens = [t for t in re.split(r"[-_+]", stem) if t]
    # 尾缀定位:最后一个含数字词(非纯字母词)之后的连续纯字母词
    last_digit_idx = -1
    for i, t in enumerate(tokens):
        if not t.isalpha():
            last_digit_idx = i
    tail = "-".join(tokens[last_digit_idx + 1 :]) if last_digit_idx >= 0 else ""
    family = "-".join(t for t in tokens if t.isalpha())
    version_sig = "-".join(t for t in tokens if not t.isalpha())
    return family, version_sig, tail


def _reduced_family(family: str, tail: str) -> str:
    """家族键剥掉尾部尾缀词(第二级匹配的减尾键);tail 为空时原样返回。

    纯字母词经 "-" 连接成家族键,split("-") 可无损还原词表;
    尾缀词必在词表末尾,故直接截断对应长度。
    """
    if not tail:
        return family
    n = len(tail.split("-"))
    return "-".join(family.split("-")[:-n])
```

同时把 `pair_mods_by_filename` 内三处 `normalize_jar_family(p)[0]` 调用先保持不动(Task 2 会重构),`pair_mods` 不动(Task 3 处理)。

- [ ] **Step 4: 跑 normalize 相关全部测试**

Run: `python -m pytest tests/test_moddb.py -k "normalize or reduced" -v`
Expected: PASS(含既有 2 个 normalize 用例;若有解包失败的既有用例,将其 `a, b = nf(...)` 改 `a, b, _ = nf(...)`,属计划内更新)

- [ ] **Step 5: Commit**

```bash
git add migration/moddb.py tests/test_moddb.py
git commit -m "feat(moddb): normalize_jar_family 三元组 — 变体尾缀提取(F20-3 第二级配对基础)"
```

---

### Task 2: pair_mods_by_filename 两级匹配(kind=rebuilt)

**Files:**
- Modify: `migration/moddb.py:601-642`(`pair_mods_by_filename` 全函数重构)
- Test: `tests/test_moddb.py`(786 行起 pair_mods_by_filename 测试区追加)

**Interfaces:**
- Consumes: Task 1 的 `normalize_jar_family -> tuple[str, str, str]`、`_reduced_family(family, tail) -> str`
- Produces: `pair_mods_by_filename(src_only: list[str], dst_only: list[str]) -> list[ModPair]`,kind 词表扩为 `"upgrade" | "renamed" | "rebuilt"`;rebuilt 对的 `modid` = 减尾键、`source="filename"`、src_version/dst_version 相同。ModPair 结构不变。

- [ ] **Step 1: 写失败测试**

```python
def test_pair_mods_by_filename_same_version_variant_rebuilt():
    """批次D F20-3:同版本号文件名变体(2.9.7 → 2.9.7-Patch)第二级配对 kind=rebuilt。"""
    from migration.moddb import pair_mods_by_filename

    pairs = pair_mods_by_filename(
        ["mods/[森罗物语：兼容] kaleidoscope_compat-2.9.7-neoforge+mc1.21.1.jar"],
        ["mods/[森罗物语：兼容] kaleidoscope_compat-2.9.7-neoforge+mc1.21.1-Patch.jar"],
    )
    assert len(pairs) == 1
    p = pairs[0]
    assert (p.kind, p.source, p.modid) == ("rebuilt", "filename", "kaleidoscope-compat-neoforge")
    assert p.src_version == p.dst_version == "2.9.7-mc1.21.1"


def test_pair_mods_by_filename_feature_tail_rebuilt():
    """-feature 尾缀(客户端 9.19 案例)同样走第二级 rebuilt。"""
    from migration.moddb import pair_mods_by_filename

    pairs = pair_mods_by_filename(
        ["mods/kaleidoscope_world_liquor-1.1.8-neoforge+1.21.1.jar"],
        ["mods/kaleidoscope_world_liquor-1.1.8-neoforge+1.21.1-feature.jar"],
    )
    assert len(pairs) == 1 and pairs[0].kind == "rebuilt"


def test_pair_mods_by_filename_tier1_wins_over_tier2():
    """第一级已配上的不再进第二级(零回归:升级/改名行为与 0.6.3 一致)。"""
    from migration.moddb import pair_mods_by_filename

    # 老版本带 -Patch、新版本不带(反方向):第一级族键不同,第二级减尾后应配上 rebuilt
    pairs = pair_mods_by_filename(
        ["mods/x-1.0-Patch.jar", "mods/y-2.0.jar"],
        ["mods/x-1.0.jar", "mods/y-3.0.jar"],
    )
    kinds = {p.modid: p.kind for p in pairs}
    assert kinds == {"x": "rebuilt", "y": "upgrade"}


def test_pair_mods_by_filename_tier2_ambiguity_skipped():
    """第二级歧义(减尾键一侧多候选)整族放弃,不猜。"""
    from migration.moddb import pair_mods_by_filename

    pairs = pair_mods_by_filename(
        ["mods/x-1.0.jar"],
        ["mods/x-1.0-Patch.jar", "mods/x-1.0-Hotfix.jar"],
    )
    assert pairs == []


def test_pair_mods_by_filename_tier2_sig_diff_not_rebuilt():
    """减尾键相同但版本签名不同 → 不配(属第一级职责,第一级没配上即非同族)。"""
    from migration.moddb import pair_mods_by_filename

    pairs = pair_mods_by_filename(
        ["mods/x-1.0.jar"],
        ["mods/x-2.0-Patch.jar"],
    )
    assert pairs == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_moddb.py -k "pair_mods_by_filename" -v`
Expected: 新 5 用例 FAIL(现有 rebuilt 不存在,返回 [] 或 kind=无);既有 3 用例(five_upgrade/renamed/ambiguous)PASS

- [ ] **Step 3: 实现两级匹配**(替换 pair_mods_by_filename 函数体)

```python
def pair_mods_by_filename(src_only: list[str], dst_only: list[str]) -> list[ModPair]:
    """mods 桶 to_add/target_only 按归一化家族键两级配对(纯快照数据,无活体依赖)。

    第一级(0.6.3 语义,键与判定完全不变):家族键全等配对;
    版本签名不同 → upgrade,相同 → renamed。歧义(同键任一侧多候选)整族放弃。
    第二级(批次D F20-3,仅对第一级未配上的残余):家族键剥掉尾缀词(减尾键)后相等,
    且双方版本签名相同、尾缀不同(含一侧空)→ kind="rebuilt"(同版本重打包/变体)。

    Args:
        src_only: 源侧独有 jar 相对路径(to_add 条目)。
        dst_only: 目标侧独有 jar 相对路径(target_only 条目)。

    Returns:
        ModPair 列表(source="filename",modid=家族键(第二级为减尾键),按 modid 升序)。
    """
    src_norm = {p: normalize_jar_family(p) for p in src_only}
    dst_norm = {p: normalize_jar_family(p) for p in dst_only}

    def _group(norm: dict[str, tuple[str, str, str]]) -> dict[str, list[str]]:
        by: dict[str, list[str]] = {}
        for p, (fam, _, _) in norm.items():
            if fam:
                by.setdefault(fam, []).append(p)
        return by

    pairs: list[ModPair] = []
    paired: set[str] = set()
    # 第一级:家族键全等(0.6.3 行为原样)
    by_src, by_dst = _group(src_norm), _group(dst_norm)
    for fam in sorted(set(by_src) & set(by_dst)):
        s_list, d_list = by_src[fam], by_dst[fam]
        if len(s_list) != 1 or len(d_list) != 1:
            continue  # 歧义放弃,不猜
        s, d = s_list[0], d_list[0]
        s_sig, d_sig = src_norm[s][1], dst_norm[d][1]
        pairs.append(
            ModPair(
                modid=fam,
                kind="upgrade" if s_sig != d_sig else "renamed",
                src_files=[s], dst_files=[d],
                src_version=s_sig or None, dst_version=d_sig or None,
                source="filename",
            )
        )
        paired.update((s, d))
    # 第二级:减尾键相等 + 同版本签名 + 异尾缀 → rebuilt(仅第一级残余)
    r_src: dict[str, list[str]] = {}
    r_dst: dict[str, list[str]] = {}
    for p, (fam, _, tail) in src_norm.items():
        if p in paired or not fam:
            continue
        red = _reduced_family(fam, tail)
        if red:
            r_src.setdefault(red, []).append(p)
    for p, (fam, _, tail) in dst_norm.items():
        if p in paired or not fam:
            continue
        red = _reduced_family(fam, tail)
        if red:
            r_dst.setdefault(red, []).append(p)
    for red in sorted(set(r_src) & set(r_dst)):
        s_list, d_list = r_src[red], r_dst[red]
        if len(s_list) != 1 or len(d_list) != 1:
            continue  # 歧义放弃,不猜
        s, d = s_list[0], d_list[0]
        s_sig, d_sig = src_norm[s][1], dst_norm[d][1]
        s_tail, d_tail = src_norm[s][2], dst_norm[d][2]
        if s_sig != d_sig or s_tail == d_tail:
            continue  # 版本不同非本级职责;尾缀相同属第一级改名语义(此处理论死枝守卫)
        pairs.append(
            ModPair(
                modid=red,
                kind="rebuilt",
                src_files=[s], dst_files=[d],
                src_version=s_sig or None, dst_version=d_sig or None,
                source="filename",
            )
        )
    pairs.sort(key=lambda p: p.modid)
    return pairs
```

- [ ] **Step 4: 跑本组与回归**

Run: `python -m pytest tests/test_moddb.py -k "pair_mods" -v && python -m pytest tests/test_corpus_regression.py -q`
Expected: 全 PASS(批次C 4 组语料黄金对——r4 五升级/r6 六升级——证明第一级零回归)

- [ ] **Step 5: Commit**

```bash
git add migration/moddb.py tests/test_moddb.py
git commit -m "feat(moddb): 文件名配对两级匹配 — 减尾键同版异尾缀 kind=rebuilt(F20-3)"
```

---

### Task 3: registry 配对同判对齐(pair_mods 尾缀启发式)

**Files:**
- Modify: `migration/moddb.py:560-598`(`pair_mods` 内 kind 判定分支)
- Test: `tests/test_moddb.py`(686 行 `test_renamed_pair` 附近追加)

**Interfaces:**
- Consumes: Task 1 `normalize_jar_family`(只用第三元 tail)
- Produces: `pair_mods` kind 判定变更:同 modid 且 version 相同且 jar_filename 不同时,尾缀不同 → `"rebuilt"`,相同 → `"renamed"`(签名与返回结构不变)。**有意变更**:客户端 9.19 `-feature` 案例由 renamed 翻转为 rebuilt(spec §3 T3)。

- [ ] **Step 1: 写失败测试**

```python
def test_registry_pair_same_version_variant_tail_rebuilt():
    """registry 同判对齐:同 modid 同版本、文件名仅尾缀差 → rebuilt(非 renamed)。"""
    pairs = pair_mods(
        _mkreg(_info("kaleidoscope_compat", "2.9.7",
                     "kaleidoscope_compat-2.9.7-neoforge+mc1.21.1.jar")),
        _mkreg(_info("kaleidoscope_compat", "2.9.7",
                     "[森罗物语：兼容] kaleidoscope_compat-2.9.7-neoforge+mc1.21.1-Patch.jar")),
    )
    assert len(pairs) == 1 and pairs[0].kind == "rebuilt"


def test_registry_pair_prefix_only_rename_stays_renamed():
    """仅 [中文标签] 前缀差的同版改名仍是 renamed(既有 test_renamed_pair 语义)。"""
    pairs = pair_mods(
        _mkreg(_info("x", "1.0", "x-1.0.jar")),
        _mkreg(_info("x", "1.0", "[标签] x-1.0.jar")),
    )
    assert len(pairs) == 1 and pairs[0].kind == "renamed"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_moddb.py -k "registry_pair_same_version_variant or prefix_only_rename" -v`
Expected: 第 1 个 FAIL(kind=="renamed"),第 2 个 PASS(现状已 renamed)

- [ ] **Step 3: 实现**(pair_mods 内 `elif s.jar_filename != d.jar_filename:` 分支替换)

```python
        if s.version != d.version:
            kind = "upgrade"
        elif s.jar_filename != d.jar_filename:
            # 同版异名:文件名尾缀不同(如 -Patch/-feature)→ rebuilt(同版本重打包);
            # 尾缀相同(如仅 [中文标签] 前缀差)→ renamed。与文件名配对两源判定统一(F20-3)。
            s_tail = normalize_jar_family("mods/" + s.jar_filename)[2]
            d_tail = normalize_jar_family("mods/" + d.jar_filename)[2]
            kind = "rebuilt" if s_tail != d_tail else "renamed"
        else:
            continue
```

同时更新 `pair_mods` docstring 中「版本相同但 jar 文件名不同 → renamed」一句为「版本相同但 jar 文件名不同 → 按尾缀启发式判 rebuilt(尾缀不同)或 renamed(尾缀相同)」。

- [ ] **Step 4: 跑测试**

Run: `python -m pytest tests/test_moddb.py -q && python -m pytest tests/test_cli.py tests/test_e2e.py -q`
Expected: 全 PASS(既有 `test_renamed_pair` 两侧尾缀均空 → 仍 renamed,不受影响)

- [ ] **Step 5: Commit**

```bash
git add migration/moddb.py tests/test_moddb.py
git commit -m "feat(moddb): registry 配对同版异名按尾缀启发式判 rebuilt — 两源判定统一(F20-3)"
```

---

### Task 4: reporter ⇄rebuilt 警示标记

**Files:**
- Modify: `migration/reporter.py:60-73`(`_display_note` mods 分支)
- Test: `tests/test_reporter.py`(找到既有 pair/⇄ 相关测试文件区追加;若无对应文件则按现有 reporter 测试所在文件)

**Interfaces:**
- Consumes: ModPair.kind 新值 `"rebuilt"`(Task 2/3 产出)
- Produces: rich 显示 `⇄rebuilt` 前加 `⚠ `(与同名桶 note=rebuilt 警示一致);JSON 输出不变(kind 原样)。

- [ ] **Step 1: 写失败测试**(在 reporter 测试文件追加;先 `grep -n "pair_by_path\|⇄" tests/test_reporter.py` 找现有构造 DiffReporter+mod_pairs 的用例模式,仿照之)

```python
def test_display_note_pair_rebuilt_gets_warning_marker():
    """配对 kind=rebuilt 的 mods 条目显示 ⚠ ⇄rebuilt(与同名桶 rebuilt 警示一致)。"""
    from migration.differ import DiffItem
    from migration.moddb import ModPair
    from migration.reporter import DiffReporter

    # 构造最小 report:mods 桶一条 to_add(路径出现在 pair.src_files)
    from migration.differ import DiffReport
    report = DiffReport()
    report.mods.append(DiffItem(path="mods/x-1.0.jar", src=None, dst=None, note="to_add"))
    pair = ModPair(modid="x", kind="rebuilt", src_files=["mods/x-1.0.jar"],
                   dst_files=["mods/x-1.0-Patch.jar"], src_version="1.0", dst_version="1.0",
                   source="filename")
    r = DiffReporter(report, src_version="a", dst_version="b", mod_pairs=[pair])
    assert r._display_note("mods", report.mods[0]) == "to_add ⇄rebuilt".join(["⚠ ", ""])
    # 上面 join 写法易错,直接断言目标串:
    assert r._display_note("mods", report.mods[0]) == "⚠ to_add ⇄rebuilt"


def test_display_note_pair_upgrade_no_warning_marker():
    """配对 kind=upgrade 不加 ⚠(既有行为不变)。"""
    # 仿上一测试,kind="upgrade",断言 _display_note == "to_add ⇄upgrade"(无 ⚠ 前缀)
```

> 实现者注:第二个测试按第一个的模式补全构造代码;DiffItem/DiffReport 构造签名以 `migration/differ.py` 实际定义为准(若 DiffItem 需要真实 FileEntry 而非 None,用最小 FileEntry(path=…, size=1, md5=None))。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_reporter.py -k "pair_rebuilt or pair_upgrade" -v`
Expected: rebuilt 用例 FAIL(显示为 `to_add ⇄rebuilt` 无 ⚠)

- [ ] **Step 3: 实现**(reporter.py `_display_note` mods 分支改造)

```python
        if bucket == "mods":
            kind = self._pair_by_path.get(item.path)
            if kind:
                note = f"{note} ⇄{kind}"
            if kind == "rebuilt" or item.note == "rebuilt":
                note = f"⚠ {note}"  # F17/F20-3: 同名同版本异构建(配对或单条)统一警示
```

- [ ] **Step 4: 跑 reporter 全部测试 + 语料回归**

Run: `python -m pytest tests/test_reporter.py -q && python -m pytest tests/test_corpus_regression.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add migration/reporter.py tests/test_reporter.py
git commit -m "feat(reporter): 配对 kind=rebuilt 显示 ⚠ 警示 — 与同名桶 rebuilt 语义统一"
```

---

### Task 5: 七轮合成夹具 + 黄金断言(4 对 = 3 upgrade + 1 rebuilt)

**Files:**
- Create: `tests/fixtures/server_corpus/20260920/r7_pre.json`
- Create: `tests/fixtures/server_corpus/20260920/r7_post.json`
- Test: `tests/test_corpus_regression.py`(文件末尾追加;ROUNDS 不加新条目——合成夹具只做配对黄金断言,不做六桶计数锚定,因 mods-only 夹具的六桶值由本测试内联断言)

**Interfaces:**
- Consumes: Task 2 `pair_mods_by_filename`(rebuilt);`Differ` mods 桶 note 语义(不变)
- Produces: 夹具 JSON(结构对齐既有快照:tool_version/snapshot_format=1/version/game_root/scanned_at/hash_mode/file_count/files;files 元素 {path,size,md5});md5 全 null(mods jar 分层快照语义),size 取七轮《旧件指纹.md》真值。

- [ ] **Step 1: 写夹具两份 JSON**

`r7_pre.json`(src,快照时刻=换装前;size 真值来自指纹表):

```json
{
  "tool_version": "0.6.0",
  "snapshot_format": 1,
  "version": "r7-pre",
  "game_root": "C:\\fixture\\sanitized",
  "scanned_at": "2026-09-20T14:20:00+08:00",
  "hash_mode": "tiered",
  "file_count": 5,
  "files": [
    {"path": "mods/kaleidoscopecookery-1.4.1-neoforge+mc1.21.1.jar", "size": 5018991, "md5": null},
    {"path": "mods/letsdo-beachparty-neoforge-2.1.4.jar", "size": 7759926, "md5": null},
    {"path": "mods/letsdo-wildernature-neoforge-1.1.5.jar", "size": 3352801, "md5": null},
    {"path": "mods/[森罗物语：兼容] kaleidoscope_compat-2.9.7-neoforge+mc1.21.1.jar", "size": 1357431, "md5": null},
    {"path": "mods/create-6.0.10-neoforge+mc1.21.1.jar", "size": 777777, "md5": null}
  ]
}
```

`r7_post.json`(dst,快照时刻=换装后):

```json
{
  "tool_version": "0.6.0",
  "snapshot_format": 1,
  "version": "r7-post",
  "game_root": "C:\\fixture\\sanitized",
  "scanned_at": "2026-09-20T14:40:00+08:00",
  "hash_mode": "tiered",
  "file_count": 5,
  "files": [
    {"path": "mods/[森罗物语：厨房] kaleidoscopecookery-1.5.0-neoforge+mc1.21.1.jar", "size": 3486047, "md5": null},
    {"path": "mods/[沙滩派对] letsdo-beachparty-neoforge-2.1.5.jar", "size": 7766490, "md5": null},
    {"path": "mods/[野性自然] letsdo-wildernature-neoforge-1.1.6.jar", "size": 3335904, "md5": null},
    {"path": "mods/[森罗物语：兼容] kaleidoscope_compat-2.9.7-neoforge+mc1.21.1-Patch.jar", "size": 1357181, "md5": null},
    {"path": "mods/create-6.0.10-neoforge+mc1.21.1.jar", "size": 777777, "md5": null}
  ]
}
```

- [ ] **Step 2: 写失败测试**(test_corpus_regression.py 末尾;先写测试再跑——此时 Task 2 已实现应直接绿,**若无 Task 2 则红**;本任务的价值是把七轮真实指纹固化进常驻回归)

```python
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
```

- [ ] **Step 3: 跑测试**

Run: `python -m pytest tests/test_corpus_regression.py -k 20260920 -v`
Expected: PASS(Task 2 已实现;若失败说明夹具文件名/结构与断言不符,修夹具)

- [ ] **Step 4: 全量语料回归**

Run: `python -m pytest tests/test_corpus_regression.py -q`
Expected: 全 PASS(含批次C 全部锚定)

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/server_corpus/20260920/ tests/test_corpus_regression.py
git commit -m "test(corpus): 七轮合成夹具 — 4 对配对黄金断言(3 upgrade+1 rebuilt,F20-3 最小复现)"
```

---

### Task 6: diff --modpack-swap 通透(F19)

**Files:**
- Modify: `migration/cli.py:48-56`(p_diff 参数)、`cli.py:326-329`(_cmd_diff 的 Differ 构造)、diff 渲染尾部
- Test: `tests/test_cli.py`(追加端到端;测试区在既有 swap/diff 测试附近)

**Interfaces:**
- Consumes: `Differ(..., modpack_swap=...)` 既有参数(differ.py:73,plan/migrate 流程已用)
- Produces: `mcmig diff <src> <dst> --modpack-swap`;src 独有 mod(非用户显式 must_migrate)→ never 桶 note=`modpack_swap`;开启且 N>0 时 stderr 一行提示。不带 flag 输出与 0.6.3 逐字段一致。

- [ ] **Step 1: 写失败测试**

```python
def test_diff_modpack_swap_routes_src_only_mods_to_never(tmp_path, monkeypatch, capsys):
    """F19 批次D:diff --modpack-swap 下源独有旧 jar 归 never/modpack_swap,mods 桶零 to_add。"""
    game_root = _setup_game(tmp_path, ["old", "new"])
    # old 有 A-1.0.jar + B-1.0.jar;new 有 A-2.0.jar —— 手工摆 jar(_setup_game 的 create.jar 会干扰,直接覆盖 mods)
    (game_root / "versions" / "old" / "mods").mkdir(parents=True, exist_ok=True)
    (game_root / "versions" / "new" / "mods").mkdir(parents=True, exist_ok=True)
    for f in (game_root / "versions" / "old" / "mods").glob("*.jar"):
        f.unlink()
    for f in (game_root / "versions" / "new" / "mods").glob("*.jar"):
        f.unlink()
    from tests.conftest import write_mod_jar
    write_mod_jar(game_root / "versions" / "old" / "mods" / "a-1.0.jar", "a")
    write_mod_jar(game_root / "versions" / "old" / "mods" / "b-1.0.jar", "b")
    write_mod_jar(game_root / "versions" / "new" / "mods" / "a-2.0.jar", "a")
    monkeypatch.chdir(tmp_path)
    from migration.cli import main
    assert main(["scan", "old", "--game-root", str(game_root), "-q"]) == 0
    assert main(["scan", "new", "--game-root", str(game_root), "-q"]) == 0
    capsys.readouterr()

    # 带 flag:旧 jar(b-1.0 源独有)落 never/modpack_swap,mods 桶无 to_add
    assert main(["diff", "old", "new", "--game-root", str(game_root),
                 "--modpack-swap", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    swapped = [i for i in doc["buckets"]["never"] if i["note"] == "modpack_swap"]
    assert [i["path"] for i in swapped] == ["mods/b-1.0.jar"]
    assert not [i for i in doc["buckets"]["mods"] if i["note"] == "to_add"]
    assert "换包模式" in capsys.readouterr().err  # 注:err 已被上一 readouterr 消费,改为先读 err 再读 out(见实现者注)

    # 不带 flag:与 0.6.3 一致 —— b-1.0 是 to_add,never 无 modpack_swap
    assert main(["diff", "old", "new", "--game-root", str(game_root), "--json"]) == 0
    out, err = capsys.readouterr().out, capsys.readouterr().err
    doc2 = json.loads(out)
    assert doc2["summary"]["mods"] == 3
    assert {i["path"]: i["note"] for i in doc2["buckets"]["mods"]}["mods/b-1.0.jar"] == "to_add"
    assert not [i for i in doc2["buckets"]["never"] if i["note"] == "modpack_swap"]
```

> 实现者注:capsys 读取顺序——`capsys.readouterr()` 一次同时拿 out/err;上面第一段断言提示行时按 `res = capsys.readouterr(); json.loads(res.out); "换包模式" in res.err` 组织,不要连续调用两次 readouterr。write_mod_jar 的实际签名以 `tests/conftest.py` 为准(`grep -n "def write_mod_jar" tests/conftest.py`)。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_cli.py -k modpack_swap -v`
Expected: FAIL(unrecognized arguments: --modpack-swap)

- [ ] **Step 3: 实现**

3a. `cli.py` p_diff 参数区(p_diff.add_argument("--category"…) 之后)加:

```python
    p_diff.add_argument("--modpack-swap", action="store_true",
                        help="换包验收视角:源独有 mod 视为旧包自带,归「换包排除」而非 to_add")
```

3b. `_cmd_diff` 的 Differ 构造(cli.py:326-329)加 modpack_swap:

```python
    report = Differ(
        src.files, dst.files, clf,
        content_reader=ctx.read_file if ctx is not None else None,
        modpack_swap=args.modpack_swap,
    ).diff()
```

3c. `_cmd_diff` 渲染前(reporter 构造之前)加提示:

```python
    if args.modpack_swap:
        n_swap = sum(1 for i in report.never if i.note == "modpack_swap")
        if n_swap:
            _print_err(
                f"[提示] 换包模式: {n_swap} 个源独有 mod 按旧包自带排除"
                "(never/换包排除,--show-never 可见)"
            )
```

- [ ] **Step 4: 跑测试 + diff 相关回归**

Run: `python -m pytest tests/test_cli.py -k "diff or modpack" -q && python -m pytest tests/test_e2e.py -k swap -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add migration/cli.py tests/test_cli.py
git commit -m "feat(cli): diff --modpack-swap 通透 — 换装验收旧 jar 归换包排除(F19)"
```

---

### Task 7: pipeline 锚定基座(find_snapshot + build_plan data_dir)

**Files:**
- Modify: `migration/pipeline.py:85-87`(`_snapshot_file` 后新增 `find_snapshot`)、`pipeline.py:123-215`(build_plan 签名与快照读写)
- Test: `tests/test_pipeline.py`(找到既有 build_plan 测试文件;若叫其他名,`grep -rn "build_plan" tests/ --include=*.py -l` 定位)

**Interfaces:**
- Produces:
  - `find_snapshot(data_dir: Path, legacy_dir: Path | None, version: str) -> tuple[Path, bool]`:返回 (快照路径, 是否旧布局命中)。锚定路径存在 → (anchored, False);否则 legacy 存在 → (legacy, True);均无 → (anchored, False)(调用方以此报缺少快照)。
  - `build_plan(..., mcmig_dir: Path, plans_dir: Path, data_dir: Path | None = None, ...)`:data_dir=None → data_dir=mcmig_dir(**GUI 调用零改动**);src/dst 快照读取走 find_snapshot(data_dir, mcmig_dir if data_dir != mcmig_dir else None, v),旧布局命中时 `log.warning` 一行;rescan_dst 写 `data_dir/"snapshots"`。
- Consumes: 既有 `_snapshot_file(mcmig_dir, version)`。

- [ ] **Step 1: 写失败测试**(pipeline 测试文件追加)

```python
def test_find_snapshot_anchored_first_then_legacy(tmp_path: Path) -> None:
    """锚定目录优先;锚定缺失时回退旧布局;均无返回锚定路径(报错指向新位置)。"""
    from migration.pipeline import find_snapshot

    data = tmp_path / "game" / ".mcmig"
    legacy = tmp_path / "cwd" / ".mcmig"
    data_snap = data / "snapshots" / "v.snapshot.json"
    legacy_snap = legacy / "snapshots" / "v.snapshot.json"
    data_snap.parent.mkdir(parents=True)
    legacy_snap.parent.mkdir(parents=True)

    # 两侧都有 → 锚定优先
    data_snap.write_text("{}", encoding="utf-8")
    legacy_snap.write_text("{}", encoding="utf-8")
    p, is_legacy = find_snapshot(data, legacy, "v")
    assert (p, is_legacy) == (data_snap, False)
    # 仅旧布局 → 回退命中
    data_snap.unlink()
    p, is_legacy = find_snapshot(data, legacy, "v")
    assert (p, is_legacy) == (legacy_snap, True)
    # 均无 → 返回锚定路径(调用方报缺少快照)
    legacy_snap.unlink()
    p, is_legacy = find_snapshot(data, legacy, "v")
    assert (p, is_legacy) == (data_snap, False)
    # legacy_dir=None(同目录语义/GUI)→ 永不回退
    assert find_snapshot(data, None, "v") == (data_snap, False)


def test_build_plan_data_dir_reads_anchored_and_falls_back(tmp_path, monkeypatch) -> None:
    """data_dir 与 mcmig_dir 分离:快照优先读 data_dir,缺失回退 mcmig_dir(旧布局)。"""
    # 复用该文件既有 build_plan 测试的最小游戏夹具构造方式(grep "def test_build_plan" 参考);
    # 关键断言:
    # 1) 快照放 data_dir/snapshots → build_plan(data_dir=data) 成功
    # 2) 快照只放 mcmig_dir/snapshots(旧布局) → build_plan(data_dir=data) 仍成功(caplog 捕捉 warning 提示)
    # 3) rescan_dst=True → 新快照写 data_dir/snapshots(而非 mcmig_dir)
    ...
```

> 实现者注:第二个测试的具体夹具构造(建版本目录/写 options.txt/mod jar)仿照同文件既有 build_plan 用例;`...` 处补全三段断言的完整代码。若该文件没有直接 build_plan 用例而在 test_e2e,则新测试放本文件并从 tests/conftest.py 取 `write_mod_jar` 等辅助。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_pipeline.py -k "find_snapshot or data_dir" -v`
Expected: FAIL(ImportError: find_snapshot / TypeError: unexpected keyword data_dir)

- [ ] **Step 3: 实现**

3a. pipeline.py `_snapshot_file` 之后新增:

```python
def find_snapshot(data_dir: Path, legacy_dir: Path | None, version: str) -> tuple[Path, bool]:
    """查找快照文件(F8 批次D 锚定):data_dir(锚定)优先,legacy_dir(旧 CWD 布局)回退。

    Args:
        data_dir: 生成物锚定 .mcmig 目录(game_root 侧)。
        legacy_dir: 旧布局 .mcmig 目录(通常 CWD 侧);None 或与 data_dir 相同表示无回退。
        version: 版本名。

    Returns:
        (快照路径, 是否旧布局命中);两侧均不存在时返回 (锚定路径, False),
        调用方以此路径报「缺少快照」。
    """
    anchored = _snapshot_file(data_dir, version)
    if anchored.exists() or legacy_dir is None or legacy_dir == data_dir:
        return anchored, False
    legacy = _snapshot_file(legacy_dir, version)
    if legacy.exists():
        return legacy, True
    return anchored, False
```

3b. build_plan 签名加参数(docstring 同步):

```python
    mcmig_dir: Path,
    plans_dir: Path,
    data_dir: Path | None = None,  # 生成物锚定目录(快照);None → mcmig_dir(GUI 兼容布局)
```

3c. build_plan 内快照读取段替换(原 `src_path = _snapshot_file(mcmig_dir, src)` 一带):

```python
    data = data_dir or mcmig_dir
    legacy = mcmig_dir if data != mcmig_dir else None
    src_path, src_legacy = find_snapshot(data, legacy, src)
    if not src_path.exists():
        raise FileNotFoundError(f"缺少 {src} 快照")
    if src_legacy:
        log.warning("[提示] %s 使用旧布局快照(%s),建议整体迁移至 %s",
                    src, src_path.parent.parent, data)
    try:
        src_snap = Snapshot.load(src_path)
    except FileNotFoundError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"{src} 快照读取失败: {e}") from e
```

dst 段同理(rescan_dst=True 时 `scan_version(game_root, dst, data / "snapshots")`;否则 find_snapshot + 同款 legacy 提示)。

- [ ] **Step 4: 跑 pipeline + e2e + GUI 回归**

Run: `python -m pytest tests/test_pipeline.py tests/test_e2e.py tests/test_gui_server.py -q`
Expected: PASS(GUI 走 data_dir=None → 行为不变;e2e 的 plan/swap 未传 data_dir → mcmig_dir 兼容布局不变)

- [ ] **Step 5: Commit**

```bash
git add migration/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): find_snapshot 锚定优先+旧布局回退;build_plan 增 data_dir(F8 基座)"
```

---

### Task 8: cli 锚定接线(scan 写锚定 / diff 宽容定位 / plan·swap·migrate)

**Files:**
- Modify: `migration/cli.py:127-146`(_resolve_game_root 拆出宽容版)、`cli.py:221-250`(_cmd_scan)、`cli.py:280-296`(_cmd_diff 快照定位)、`cli.py:355-380`(_cmd_plan)、`cli.py:489`(swap 预检)、`cli.py:532-546`(swap build_plan)、`cli.py:558`(swap p_path)、`cli.py:566-584`(migrate p_path/stale)
- Test: `tests/test_cli.py`(有意更新 + 新增)、`tests/test_e2e.py`(路径断言有意更新)

**Interfaces:**
- Consumes: Task 7 `find_snapshot`、`build_plan(data_dir=…)`;既有 `snapshot_path(workdir, v)`/`plan_path(workdir, src, dst)`(workdir 传 game_root 即锚定路径,传 cwd 即旧布局——两布局复用同一助手)
- Produces(全部 cli 内部,无对外新 API):
  - `_try_resolve_game_root(args) -> Path | None`(同解析链,None 不退出)
  - scan:快照写 `<game_root>/.mcmig/snapshots/`(规则仍读 CWD/.mcmig/rules.yaml)
  - diff:game-root 可解析 → 锚定优先+旧布局回退(+stderr 一次性提示);不可解析 → CWD-only(夹具复放零扰动)
  - plan/swap:plans 写锚定;build_plan 传 data_dir
  - migrate:plan 文件锚定优先+旧布局回退;stale 检查取两侧实际存在文件

- [ ] **Step 1: 写失败测试**(test_cli.py 追加)

```python
def test_scan_anchors_snapshots_to_game_root(tmp_path, monkeypatch):
    """F8 批次D:scan 快照落 game_root/.mcmig/snapshots(不再随 CWD 散落);规则仍读 CWD。"""
    game_root = _setup_game(tmp_path, ["mini"])
    monkeypatch.chdir(tmp_path)
    from migration.cli import main
    assert main(["scan", "mini", "--game-root", str(game_root)]) == 0
    assert snapshot_path(game_root, "mini").exists()      # 锚定位置
    assert not snapshot_path(tmp_path, "mini").exists()   # CWD 不再出现


def test_diff_reads_anchored_and_legacy_fallback(tmp_path, monkeypatch, capsys):
    """diff 锚定优先;快照只在旧 CWD 布局时回退读 + stderr 一次性提示。"""
    game_root = _setup_game(tmp_path, ["mini", "mini_b"], variant_b_for="mini_b")
    monkeypatch.chdir(tmp_path)
    from migration.cli import main
    assert main(["scan", "mini", "--game-root", str(game_root), "-q"]) == 0
    assert main(["scan", "mini_b", "--game-root", str(game_root), "-q"]) == 0
    capsys.readouterr()
    # 锚定命中:正常 diff,无旧布局提示
    assert main(["diff", "mini_b", "mini", "--game-root", str(game_root), "--json"]) == 0
    res = capsys.readouterr()
    assert "旧布局" not in res.err
    # 把锚定快照挪到 CWD 旧布局 → 回退读 + 提示
    legacy_dir = tmp_path / ".mcmig" / "snapshots"
    legacy_dir.mkdir(parents=True)
    import shutil
    for f in (game_root / ".mcmig" / "snapshots").glob("*.json"):
        shutil.move(str(f), str(legacy_dir / f.name))
    assert main(["diff", "mini_b", "mini", "--game-root", str(game_root), "--json"]) == 0
    res = capsys.readouterr()
    assert "旧布局快照" in res.err and "建议整体迁移" in res.err
    # game-root 不可解析(无 flag/env/config)→ CWD-only,快照在 CWD 仍可用(夹具复放语义)
    monkeypatch.delenv("MCMIG_GAME_ROOT", raising=False)
    assert main(["diff", "mini_b", "mini", "--json"]) == 0


def test_plan_writes_plan_to_game_root(tmp_path, monkeypatch):
    """plan 的 plan 文件写锚定目录;迁移链路 migrate 能从锚定位置找回。"""
    game_root = _setup_game(tmp_path, ["mini", "mini_b"], variant_b_for="mini_b")
    monkeypatch.chdir(tmp_path)
    from migration.cli import main
    from migration.plan import plan_path
    assert main(["scan", "mini", "--game-root", str(game_root), "-q"]) == 0
    assert main(["scan", "mini_b", "--game-root", str(game_root), "-q"]) == 0
    assert main(["plan", "mini_b", "mini", "--game-root", str(game_root)]) == 0
    assert plan_path(game_root, "mini_b", "mini").exists()
    assert not plan_path(tmp_path, "mini_b", "mini").exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_cli.py -k "anchors or anchored or game_root_plan" -v`
Expected: FAIL(现状全部写 CWD)

- [ ] **Step 3: 实现**

3a. `_resolve_game_root` 拆宽容版(cli.py:127 前):

```python
def _try_resolve_game_root(args: argparse.Namespace) -> Path | None:
    """宽容解析游戏根目录(链同 _resolve_game_root,失败返回 None 不退出)。

    diff 用:夹具复放/无 game-root 配置场景退回 CWD-only 查找,保持 0.6.x 行为。
    """
    if args.game_root:
        return Path(args.game_root)
    env = os.environ.get("MCMIG_GAME_ROOT")
    if env:
        return Path(env)
    cfg = Path.cwd() / ".mcmig" / "config.yaml"
    if cfg.is_file():
        import yaml

        doc = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        gr = doc.get("game_root")
        if gr:
            return Path(gr)
    return None
```

`_resolve_game_root` 重构为:`gr = _try_resolve_game_root(args)`;None 时打印原错误并 `raise SystemExit(2)`(错误文案不变,既有 test_resolve_game_root_* 用例不动)。

3b. `_cmd_scan`:mcmig_dir 拆两用——规则仍 `cwd/.mcmig`,快照写锚定:

```python
    cwd = Path.cwd()
    rules_dir = cwd / ".mcmig"          # 静态配置跟工作区(bootstrap 两分法,spec T5b)
    data_dir = game_root / ".mcmig"     # 生成物跟实例(F8)
    rs, errs = build_ruleset(..., mcmig_dir=rules_dir)
    ...
    snap = scan_version(game_root, args.version, data_dir / "snapshots", ...)
    spath = snapshot_path(game_root, args.version)
```

3c. `_cmd_diff` 快照定位(cli.py:282-283 替换):

```python
    cwd = Path.cwd()
    game_root = _try_resolve_game_root(args)
    if game_root is not None:
        data_dir = game_root / ".mcmig"
        legacy_dir = cwd / ".mcmig"
        src_path, src_leg = find_snapshot(data_dir, legacy_dir, args.src)
        dst_path, dst_leg = find_snapshot(data_dir, legacy_dir, args.dst)
        if src_leg or dst_leg:
            _print_err(f"[提示] 使用旧布局快照({legacy_dir}),建议整体迁移至 {data_dir}")
    else:
        # game-root 不可定位(夹具复放/纯快照对比):CWD-only,0.6.x 行为
        src_path = snapshot_path(cwd, args.src)
        dst_path = snapshot_path(cwd, args.dst)
```

(顶部 `from .pipeline import find_snapshot` 与既有 `resolve_diff_context` 导入合并。)

3d. `_cmd_plan` build_plan 调用(cli.py:370-380):

```python
    data_dir = game_root / ".mcmig"
    plan, compat_warnings = build_plan(
        cwd, game_root, args.src, args.dst,
        modpack_swap=args.modpack_swap, rescan_dst=False, save=not args.no_save,
        mcmig_dir=cwd / ".mcmig",
        plans_dir=data_dir / "plans",
        data_dir=data_dir,
        exclude=args.exclude, ...
    )
```

3e. `_cmd_swap`:预检 `src_snap = snapshot_path(Path.cwd(), args.src)`(cli.py:489)→ `find_snapshot(game_root / ".mcmig", Path.cwd() / ".mcmig", args.src)[0]`;build_plan 调用(532-546)加 `data_dir=game_root / ".mcmig"`、`plans_dir=game_root / ".mcmig" / "plans"`;`p_path = plan_path(Path.cwd(), ...)`(558)→ `plan_path(game_root, args.src, args.dst)`。

3f. `_cmd_migrate`(566-584):

```python
    data_dir = game_root / ".mcmig"
    p_path, p_legacy = (lambda a, l: (a if a.exists() else (l if l.exists() else a)))(
        plan_path(game_root, args.src, args.dst), plan_path(cwd, args.src, args.dst))
    if p_legacy := (not p_path.exists()) and False:  # 占位说明:见实现者注
        ...
```

> 实现者注:上面 lambda 写法仅示意「锚定优先,旧布局回退」;实际直接写四行:
> ```python
>     p_anchored = plan_path(game_root, args.src, args.dst)
>     p_legacy = plan_path(cwd, args.src, args.dst)
>     p_path = p_anchored if p_anchored.exists() else (p_legacy if p_legacy.exists() else p_anchored)
> ```
> stale 检查(583-584)同步:src_snap/dst_snap 用 `find_snapshot(data_dir, cwd / ".mcmig", v)[0]`(files 存在才比 mtime,原逻辑保留);plan 文件旧布局命中时打印一行「[提示] 使用旧布局 plan,建议整体迁移至 …」。

- [ ] **Step 4: 全量测试并完成有意更新清单**

Run: `python -m pytest tests/ -q`
Expected: 既有用例中以下几类**有意更新**(超出此清单的失败 = 设计偏差,停下回查):
1. `test_cli.py:62` `snapshot_path(tmp_path, "mini")` → `snapshot_path(game_root, "mini")`
2. `test_cli.py:173-186 / 225-237` plan_path 断言 → `plan_path(game_root, …)`(以测试内 game_root 变量名为准)
3. `test_cli.py:595/618/664/713` 四处裸 `main(["diff","a","b","--json"])` → 追加 `"--game-root", str(root)`(锚定后 diff 需能定位 game-root,否则只查 CWD——README 会写明)
4. `test_e2e.py` 中凡以 `snapshot_path(tmp_path,…)`/`plan_path(tmp_path,…)` 定位 scan/plan 产物的断言 → 改用测试内 game_root 变量(逐条列出实际改动行)
5. 其余(e2e 主链路本身全程带 --game-root)应零改动通过

Run again: `python -m pytest tests/ -q && python -m ruff check .`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add migration/cli.py tests/test_cli.py tests/test_e2e.py
git commit -m "feat(cli): .mcmig 生成物锚定 game-root — scan/diff/plan/swap/migrate 全链接线+旧布局回退(F8)"
```

---

### Task 9: 版本 0.7.0 + README 中英 + 观察文档

**Files:**
- Modify: `pyproject.toml:7`(version = "0.7.0")
- Modify: `README.zh-CN.md` / `README.en.md`(diff/modpack-swap、mods 桶 ⇄rebuilt、.mcmig 布局)
- Create(本地不入库):`Reference/observations/mcmigrator_服务端测试_20260920/README.md`、编辑 `Reference/observations/mcmigrator_服务端测试_20260919/README.md`(F12 附注)

**Interfaces:** 无代码;文档内容以 spec §0/§3 为准。

- [ ] **Step 1: 版本号** — pyproject.toml `version = "0.6.3"` → `"0.7.0"`(TOOL_VERSION 不动,见 Global Constraints)

- [ ] **Step 2: README.zh-CN.md**(找到 diff 命令说明与 mods 桶语义小节,按现有文风补三点;README.en.md 同步英文)

  1. diff 子命令表加一行:`--modpack-swap  换包验收视角:源独有 mod 归「换包排除」而非 to_add(F19)`
  2. 配对小节:`⇄rebuilt = 同版本号、文件名带 -Patch/-feature 类尾缀的重新打包(F20-3);升级 ⇄upgrade / 改名 ⇄renamed 不变`
  3. `.mcmig` 布局小节(新增或并入现有目录结构说明):
     `快照与 plan 写入 <game_root>/.mcmig(生成物跟游戏实例走);config.yaml 与 rules.yaml 仍在工作目录 .mcmig(引导配置跟工作区走)。读取时锚定位置优先,找不到自动回退旧 CWD 布局并提示迁移。diff 需能定位 game-root(--game-root / MCMIG_GAME_ROOT / .mcmig/config.yaml 三选一),否则仅查 CWD(夹具复放兼容)。`

- [ ] **Step 3: 观察文档(本地,gitignored 不提交)**

  0920 README 骨架:场景(4换4,无启动验证)/语料构成表(演化段+纯换装段)/F20 六点(实测结论按本计划 §0 表)/演化谱系第 5 点(1 天=91 心跳+1 candidate)/旧件指纹表回链/快照位置与复放说明(原始快照未随包,夹具为合成)。
  0919 README 末尾附注:「**勘误(2026-09-20)**:五/六轮 server.properties 的 identical/semantics 判定系 junction 同体伪影的可能性已证实方向(0.6.3 起 same_dir 语义复核短路);相关裁定不宜作 ground truth 复用。」

- [ ] **Step 4: 验证**

Run: `python -m pytest tests/ -q && python -m ruff check . && python -m migration doctor`
Expected: 全绿(doctor 验证 data/*.yaml 未动 → manifest 校验通过)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml README.zh-CN.md README.en.md
git commit -m "chore(release): 0.7.0 — 批次D rebuilt 配对/diff 换装通透/.mcmig 锚定 + README 同步"
```

---

## 收尾(控制器执行,不派实现者)

- 全量 `pytest` + `ruff` + `doctor` 终验
- spec §6 验收标准逐条核对(尤其:批次C 四组语料回归全绿=两级匹配零回归;`--modpack-swap` 不带 flag 与 0.6.3 逐字段一致)
- 终审(requesting-code-review)→ squash 合并 main(用户委托:单干净提交)→ 推送 → 记忆更新

## Self-Review 记录(计划自审,已修正)

- **spec 覆盖**:D1→T1-T5、D2→T6、D3→T7-T8、D5→T9、版本→T9;spec §5 例外清单并入 T8 Step 4。✓
- **类型一致**:normalize 3 元组在 T2/T3 消费方式已写明;find_snapshot 返回 tuple[Path, bool] 在 T7 定义、T8 消费。✓
- **修正过的问题**:①原稿 T4 测试里 join 写法易错,改为直接断言目标串+实现者注;②T8 3f 的 lambda 占位改为四行直写;③TOOL_VERSION 不动(与 spec T6 字面冲突,已在 Global Constraints 记录更正理由)。
