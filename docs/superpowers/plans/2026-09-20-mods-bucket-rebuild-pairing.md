# mods 桶 rebuilt 检出与文件名配对 实现计划(批次 C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** diff 的 mods 桶检出"同名重建 jar"(rebuilt)并让升级/改名配对摆脱活体目录依赖(文件名家族配对),附 json/toml 语义比较、崩溃残留 never 规则、新语料回归夹具。

**Architecture:** 三处接线零 schema 变更——differ 的 mods 桶同名条目比 size/md5 出 `rebuilt` note;moddb 新增纯快照可用的 `pair_mods_by_filename` 与 registry 配对合并进现有 `mod_pairs`(条目加 `source` 字段);textcompare 增加 json/toml 键序无关比较并入 Differ 语义复核 dispatch。planner 对 rebuilt 仅 SKIP+警告,迁移执行行为零变化。

**Tech Stack:** Python 3.11+(stdlib `tomllib`/`json`/`re`/`zipfile`),pytest,rich。无新第三方依赖。

**Spec:** `Reference/specs/2026-09-19-mods-bucket-rebuild-pairing-design.md`(计划从 spec 出发,执行者需同读 spec)

## Global Constraints

- 所有注释/docstring 中文;文件 UTF-8 无 BOM;路径用 `pathlib.Path`。
- 新解析仅用标准库(`tomllib`/`json`),不加第三方依赖。
- 迁移执行行为零变化:planner 对 `rebuilt` 的行为是 SKIP(不 COPY);`to_add`→COPY 等既有映射不动。
- 既有测试只允许两处例外(spec §5 已记录):`tests/test_cli.py::test_diff_degraded_when_game_root_unreachable` 一次有意更新;`tests/test_corpus_regression.py` 新增 0912 rebuilt 断言(新增,非改动)。
- 每任务收尾跑 `pytest tests/<对应文件> -q` 全绿后提交;提交信息用仓库惯例(conventional commits + 中文描述)。
- 语料 zip 在项目根(`mcmigrator_服务端测试_20260914.zip` 等),解包目标 `Reference/observations/`(gitignore 覆盖,不入库);入库的只有脱敏快照夹具。

---

### Task 1: textcompare 增加 json/toml 语义等价函数

**Files:**
- Modify: `migration/textcompare.py`(文件末尾追加)
- Test: `tests/test_textcompare.py`(文件末尾追加)

**Interfaces:**
- Consumes: 无(纯函数)。
- Produces: `json_semantic_equal(a: bytes, b: bytes) -> bool`、`toml_semantic_equal(a: bytes, b: bytes) -> bool`(Task 2 的 dispatch 表引用这两个名字)。

- [ ] **Step 1: 写失败测试**

在 `tests/test_textcompare.py` 末尾追加:

```python
from migration.textcompare import json_semantic_equal, toml_semantic_equal


def test_json_semantic_equal_ignores_key_order_and_whitespace():
    a = b'{"logInterval": 60, "debug": false}'
    b = b'{\n  "debug": false,\n  "logInterval": 60\n}'
    assert json_semantic_equal(a, b)


def test_json_semantic_equal_tolerates_bom():
    assert json_semantic_equal(b'\xef\xbb\xbf{"a": 1}', b'{"a": 1}')


def test_json_semantic_not_equal_value_diff():
    assert not json_semantic_equal(b'{"logInterval": 10}', b'{"logInterval": 60}')


def test_json_semantic_not_equal_malformed():
    assert not json_semantic_equal(b'{oops', b'{"a": 1}')


def test_toml_semantic_equal_ignores_table_order_and_comments():
    a = b'[server]\nport = 25565\n[client]\nfov = 90\n'
    b = b'# 重排注释\n[client]\nfov = 90\n\n[server]\nport = 25565\n'
    assert toml_semantic_equal(a, b)


def test_toml_semantic_not_equal_value_diff():
    assert not toml_semantic_equal(b'x = 1\n', b'x = 2\n')


def test_toml_semantic_not_equal_malformed():
    assert not toml_semantic_equal(b'= = =\n', b'x = 1\n')
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_textcompare.py -q`
Expected: FAIL(ImportError: cannot import name 'json_semantic_equal')

- [ ] **Step 3: 最小实现**

在 `migration/textcompare.py` 顶部补 import(与现有 import 合并,不重复块):

```python
import json
import tomllib
```

文件末尾追加:

```python
def json_semantic_equal(a: bytes, b: bytes) -> bool:
    """判定两份 JSON 内容语义等价(解析后深度相等,键序/空白/BOM 差异消解)。

    Args:
        a: 源侧文件字节内容。
        b: 目标侧文件字节内容。

    Returns:
        True 表示语义等价;任一侧解析失败(畸形 JSON/编码错误)返回 False,
        由调用方退回字节级 modified 判定。
    """
    try:
        ja = json.loads(a.decode("utf-8-sig"))
        jb = json.loads(b.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        return False
    return ja == jb


def toml_semantic_equal(a: bytes, b: bytes) -> bool:
    """判定两份 TOML 内容语义等价(tomllib 解析后比较,表序/注释/空白差异消解)。"""
    try:
        ta = tomllib.loads(a.decode("utf-8-sig"))
        tb = tomllib.loads(b.decode("utf-8-sig"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return False
    return ta == tb
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_textcompare.py -q`
Expected: PASS(原有 properties 测试一并全绿)

- [ ] **Step 5: 提交**

```bash
git add migration/textcompare.py tests/test_textcompare.py
git commit -m "feat(textcompare): json/toml 语义等价判定(F16) — 键序/表序/注释/空白噪声消解"
```

---

### Task 2: Differ 语义复核 dispatch 扩展(.json/.toml)

**Files:**
- Modify: `migration/differ.py:70-86`(`_same_content_semantic`)与模块顶部 import 区
- Test: `tests/test_differ.py`(文件末尾追加)

**Interfaces:**
- Consumes: Task 1 的 `json_semantic_equal`/`toml_semantic_equal`;现有 `properties_semantic_equal`。
- Produces: 模块级 `SEMANTIC_EQUAL_BY_SUFFIX: dict[str, Callable[[bytes, bytes], bool]]`(键为小写后缀含点,如 `.json`);行为:md5 异且后缀命中且 reader 可读双侧且语义等价 → `(True, "semantics")`,其余不变。

- [ ] **Step 1: 写失败测试**

在 `tests/test_differ.py` 末尾追加(`_clf`/`_e` 为文件既有助手,`FileEntry(path, size, md5)` 位置构造):

```python
def test_json_semantic_rewrite_lands_identical_semantics():
    """F16: mod 启动重写 json(键序/空白差异)→ identical/semantics,不误报 candidate。"""
    clf = _clf()
    reader = lambda p, side: (b'{"logInterval": 60, "debug": false}' if side == "src"
                              else b'{\n  "debug": false,\n  "logInterval": 60\n}')
    d = Differ([_e("config/atleaks.json", 398, "aa")],
               [_e("config/atleaks.json", 401, "bb")], clf,
               content_reader=reader).diff()
    assert any(i.path == "config/atleaks.json" and i.note == "semantics" for i in d.identical)
    assert not any(i.path == "config/atleaks.json" for i in d.candidate)


def test_toml_semantic_rewrite_lands_identical_semantics():
    clf = _clf()
    reader = lambda p, side: (b"[server]\nport = 1\n[client]\nfov = 90\n" if side == "src"
                              else b"[client]\nfov = 90\n[server]\nport = 1\n")
    d = Differ([_e("config/xx.toml", 10, "aa")],
               [_e("config/xx.toml", 12, "bb")], clf,
               content_reader=reader).diff()
    assert any(i.path == "config/xx.toml" and i.note == "semantics" for i in d.identical)


def test_json_real_value_diff_stays_modified():
    clf = _clf()
    reader = lambda p, side: b'{"logInterval": 10}' if side == "src" else b'{"logInterval": 60}'
    d = Differ([_e("config/atleaks.json", 398, "aa")],
               [_e("config/atleaks.json", 401, "bb")], clf,
               content_reader=reader).diff()
    assert any(i.path == "config/atleaks.json" and i.note == "modified" for i in d.candidate)


def test_semantic_check_not_fired_without_reader():
    """reader 缺失(复放/plan 管线)→ 字节比较不变(plan 管线不传 reader 的既定语义)。"""
    clf = _clf()
    d = Differ([_e("config/atleaks.json", 398, "aa")],
               [_e("config/atleaks.json", 401, "bb")], clf).diff()
    assert any(i.path == "config/atleaks.json" and i.note == "modified" for i in d.candidate)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_differ.py -q -k semantic`
Expected: 前两个 FAIL(落 candidate/modified 而非 identical/semantics),后两个 PASS

- [ ] **Step 3: 实现 dispatch 表**

`migration/differ.py` 顶部 import 区改(合并进现有 from-import,不新开重复块):

```python
from .textcompare import (
    json_semantic_equal,
    properties_semantic_equal,
    toml_semantic_equal,
)
```

`_same_content_semantic` 上方加模块级表(类的同一缩进层级,模块顶层):

```python
# F12/F16: 语义等价判定按后缀分发(仅 md5 异 + 双侧内容可读时触发)
SEMANTIC_EQUAL_BY_SUFFIX = {
    ".properties": properties_semantic_equal,
    ".json": json_semantic_equal,
    ".toml": toml_semantic_equal,
}
```

`_same_content_semantic` 方法体改为:

```python
    def _same_content_semantic(self, path: str, s: FileEntry, d: FileEntry) -> tuple[bool, str]:
        """内容比较:F12/F16 在 md5 异、后缀命中、读取成功时做语义复核。"""
        if s.md5 is not None and d.md5 is not None and s.md5 != d.md5:
            suffix = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
            check = SEMANTIC_EQUAL_BY_SUFFIX.get(suffix)
            if check is not None and self.content_reader is not None:
                a = self.content_reader(path, "src")
                b = self.content_reader(path, "dst")
                if a is not None and b is not None and check(a, b):
                    return True, "semantics"
            return False, "verified"
        return self._same_content(s, d)
```

(若原 import 已含 `properties_semantic_equal` 单行形式,替换为上述括号形式。)

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_differ.py tests/test_textcompare.py -q`
Expected: PASS(既有 F12 properties 集成测试零改动全绿)

- [ ] **Step 5: 提交**

```bash
git add migration/differ.py tests/test_differ.py
git commit -m "feat(differ): 语义复核 dispatch 扩展 json/toml(F16) — mod 启动重写噪声归 semantics"
```

---

### Task 3: F17 rebuilt note(differ 判定 + planner SKIP 警告)

**Files:**
- Modify: `migration/differ.py:88-96`(`_mod_item`)
- Modify: `migration/planner.py:185-198`(`_for_mod`)
- Test: `tests/test_differ.py`、`tests/test_planner.py`(各追加)

**Interfaces:**
- Consumes: 无。
- Produces: mods 桶 note 词汇新增 `rebuilt`(双侧同名 + size 异,或双侧 md5 非空且异);planner 对 rebuilt 显式 SKIP 且 reason 含警告文案。后续任务(报表/夹具)依赖该 note 值。

- [ ] **Step 1: 写失败测试(differ)**

`tests/test_differ.py` 末尾追加:

```python
def test_mod_same_name_diff_size_is_rebuilt():
    """F17: 同名同版本 jar 重新打包(±size)→ note=rebuilt,不再漏检为 shared。"""
    clf = _clf()
    d = Differ([_e("mods/x-1.0.jar", 7233263, None)],
               [_e("mods/x-1.0.jar", 7233336, None)], clf).diff()
    assert d.mods[0].note == "rebuilt"


def test_mod_same_name_same_size_md5_diff_is_rebuilt():
    clf = _clf()
    d = Differ([_e("mods/x-1.0.jar", 100, "aa")],
               [_e("mods/x-1.0.jar", 100, "bb")], clf).diff()
    assert d.mods[0].note == "rebuilt"


def test_mod_same_name_same_size_null_md5_stays_shared():
    """tiered 快照 md5=null 且 size 相同 → shared(同尺寸异构建为记录在案的盲区)。"""
    clf = _clf()
    d = Differ([_e("mods/x-1.0.jar", 100, None)],
               [_e("mods/x-1.0.jar", 100, None)], clf).diff()
    assert d.mods[0].note == "shared"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_differ.py -q -k rebuilt`
Expected: 前两个 FAIL(note 为 shared),第三个 PASS

- [ ] **Step 3: 实现 differ 判定**

`_mod_item` 改为:

```python
    def _mod_item(self, path: str, s: FileEntry | None, d: FileEntry | None) -> DiffItem:
        """mods 目录条目按文件名集合分桶:shared / rebuilt / to_add / target_only。"""
        if s and d:
            if s.size != d.size or (s.md5 and d.md5 and s.md5 != d.md5):
                note = "rebuilt"  # F17: 同名同版本号、内容不同(上游重新打包)
            else:
                note = "shared"
        elif s:
            note = "to_add"
        else:
            note = "target_only"
        return DiffItem(path=path, src=s, dst=d, note=note)
```

- [ ] **Step 4: 写 planner 失败测试**

`tests/test_planner.py` 末尾追加(Planner 实际签名 `Planner(report, src_index).plan()`,与文件内既有测试一致):

```python
def test_plan_rebuilt_mod_skips_with_warning():
    """F17: rebuilt 不自动覆盖目标,SKIP + reason 警告(用户拍板方案 A)。"""
    from migration.differ import DiffItem, DiffReport
    from migration.planner import Planner
    from migration.snapshot import FileEntry

    report = DiffReport()
    report.mods = [DiffItem(path="mods/x-1.0.jar",
                            src=FileEntry("mods/x-1.0.jar", 100, None),
                            dst=FileEntry("mods/x-1.0.jar", 173, None), note="rebuilt")]
    plan = Planner(report, {}).plan()
    rec = {a.path: a for a in plan.actions}["mods/x-1.0.jar"]
    assert rec.behavior.value == "skip"
    assert "rebuilt" in rec.reason and "保留目标侧" in rec.reason
```

- [ ] **Step 5: 实现 planner 映射**

改 `migration/planner.py` 的 `_for_mod`:

```python
    def _for_mod(self, item: DiffItem) -> ActionRecord:
        behavior, origin = {
            "to_add": (Behavior.COPY, Origin.MOD_ADDED),
            "shared": (Behavior.SKIP, Origin.MOD_SHARED),
            "rebuilt": (Behavior.SKIP, Origin.MOD_SHARED),
            "target_only": (Behavior.SKIP, Origin.MOD_TARGET_ONLY),
        }.get(item.note, (Behavior.SKIP, Origin.MOD_SHARED))
        reason = (
            "mods (rebuilt: 同名同版本异构建,默认保留目标侧,如需源侧构建请手工处理)"
            if item.note == "rebuilt"
            else f"mods ({item.note})"
        )
        return ActionRecord(
            path=item.path, behavior=behavior, origin=origin,
            src_size=item.src.size if item.src else None,
            dst_size=item.dst.size if item.dst else None,
            md5_match=None, confidence="high", reason=reason,
            backup_target=None,
        )
```

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_differ.py tests/test_planner.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add migration/differ.py migration/planner.py tests/test_differ.py tests/test_planner.py
git commit -m "feat(differ/planner): F17 rebuilt 检出 — 同名 jar 比 size/md5,plan 层 SKIP+警告不自动覆盖"
```

---

### Task 4: moddb 文件名家族归一函数

**Files:**
- Modify: `migration/moddb.py`(`ModPair` 类定义之前追加)
- Test: `tests/test_moddb.py`(末尾追加)

**Interfaces:**
- Consumes: 无。
- Produces: `normalize_jar_family(jar_rel_path: str) -> tuple[str, str]` — 返回 (家族键, 版本签名);Task 5 的 `pair_mods_by_filename` 引用。

- [ ] **Step 1: 写失败测试**

```python
def test_normalize_jar_family_strips_tag_prefix_and_versions():
    from migration.moddb import normalize_jar_family as nf
    # 中文标签前缀 + 多段版本 → 家族键只留纯字母词
    assert nf("mods/[传送石碑／指路石] waystones-neoforge-1.21.1-21.1.44.jar") == \
        ("waystones-neoforge", "1.21.1-21.1.44")
    assert nf("mods/[圆石生成器] cobblestone_generator-1.2.1-mc1.21.1-neoforge.jar") == \
        ("cobblestone-generator-neoforge", "1.2.1-mc1.21.1-neoforge")
    assert nf("mods/cobblestone_generator-1.2.0-mc1.21.1-neoforge.jar") == \
        ("cobblestone-generator-neoforge", "1.2.0-mc1.21.1")
    # + 连接的版本段、日期段、all 等字母词
    assert nf("mods/DragonSurvival-1.21.1-v2.0.69-02.09.2026-all.jar") == \
        ("dragonsurvival-all", "1.21.1-v2.0.69-02.09.2026")
    assert nf("mods/immersive_melodies-neoforge-0.7.1+1.21.1.jar") == \
        ("immersive-melodies-neoforge", "0.7.1+1.21.1".replace("+", "-"))
    # 大小写归一;无版本段的裸名
    assert nf("mods/Mekanism-1.21.1-10.7.19.85.jar") == ("mekanism", "1.21.1-10.7.19.85")
    assert nf("mods/mekmm-1.21.1-1.4.1.jar") == ("mekmm", "1.21.1-1.4.1")


def test_normalize_jar_family_renamed_same_version():
    from migration.moddb import normalize_jar_family as nf
    a = nf("mods/infernalmobs-1.21.1.3NF.jar")
    b = nf("mods/[稀有精英怪] infernalmobs-1.21.1.3NF.jar")
    assert a[0] == b[0] == "infernalmobs"
    assert a[1] == b[1]  # 版本签名相同 → renamed 判定依据
```

注意:`re.split(r"[-_+]")` 把 `+` 也当分隔符,故 `0.7.1+1.21.1` 的签名实际是 `"0.7.1-1.21.1"`(上例断言已按此写)。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_moddb.py -q -k normalize`
Expected: FAIL(ImportError/AttributeError)

- [ ] **Step 3: 实现**

`migration/moddb.py` 在 `ModPair` 类前追加(该文件已 import `re` 则复用,否则顶部补 `import re`):

```python
_TAG_PREFIX_RE = re.compile(r"^\s*\[[^\]]*\]\s*")


def normalize_jar_family(jar_rel_path: str) -> tuple[str, str]:
    """jar 相对路径 → (家族键, 版本签名),文件名配对的归一化基础(F4 fallback)。

    家族键 = 剥 [中文标签] 前缀、小写、按 -_+ 切词后仅保留纯字母词;
    版本签名 = 含数字的词按序拼接(区分 upgrade/renamed 用)。

    Args:
        jar_rel_path: 版本内相对路径(正斜杠,如 "mods/[标签] x-1.0.jar")。

    Returns:
        (家族键, 版本签名);家族键可能为空串(调用方需跳过)。
    """
    name = jar_rel_path.rsplit("/", 1)[-1]
    name = _TAG_PREFIX_RE.sub("", name)
    stem = name.rsplit(".", 1)[0].lower()
    tokens = [t for t in re.split(r"[-_+]", stem) if t]
    family = "-".join(t for t in tokens if t.isalpha())
    version_sig = "-".join(t for t in tokens if not t.isalpha())
    return family, version_sig
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_moddb.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add migration/moddb.py tests/test_moddb.py
git commit -m "feat(moddb): normalize_jar_family 文件名家族归一(F4 fallback 基础)"
```

---

### Task 5: ModPair.source + pair_mods_by_filename + merge_mod_pairs

**Files:**
- Modify: `migration/moddb.py`(`ModPair` 数据类、`pair_mods` 之后追加两函数)
- Test: `tests/test_moddb.py`(末尾追加)

**Interfaces:**
- Consumes: Task 4 的 `normalize_jar_family`。
- Produces:
  - `ModPair.source: str`(默认 `"registry"`;`to_dict()` 输出 `source` 键);
  - `pair_mods_by_filename(src_only: list[str], dst_only: list[str]) -> list[ModPair]`;
  - `merge_mod_pairs(registry_pairs: list[ModPair], filename_pairs: list[ModPair]) -> list[ModPair]`。
  - Task 6 的 cli 接线引用这三个名字。

- [ ] **Step 1: 写失败测试**

```python
def test_pair_mods_by_filename_five_upgrade_families():
    from migration.moddb import pair_mods_by_filename
    src_only = [
        "mods/[传送石碑／指路石] waystones-neoforge-1.21.1-21.1.44.jar",
        "mods/[稀有度核心] raritycore-1211.14.6.jar",
        "mods/[龙之生存] DragonSurvival-1.21.1-v2.0.69-02.09.2026-all.jar",
        "mods/alexscaves-up-0.1.2.jar",
        "mods/logisticsnetworks-1.21.1-1.16.0.jar",
        "mods/[FTB 区块] ftb-chunks-neoforge-2101.1.22.jar",   # 删除件,无对侧
        "mods/tinydragons-1.0-neoforge-1.21.1.jar",            # 删除件
    ]
    dst_only = [
        "mods/[传送石碑／指路石] waystones-neoforge-1.21.1-21.1.45.jar",
        "mods/[稀有度核心] raritycore-1211.14.7.jar",
        "mods/[龙之生存] DragonSurvival-1.21.1-v2.0.70-13.09.2026-all.jar",
        "mods/alexscaves-up-0.1.3.jar",
        "mods/logisticsnetworks-1.21.1-1.16.1.jar",
        "mods/field-emitters-neoforge-1.21.1-1.1.0.jar",       # 新增件,无源侧
    ]
    pairs = pair_mods_by_filename(src_only, dst_only)
    fams = {p.modid for p in pairs}
    assert fams == {"waystones-neoforge", "raritycore", "dragonsurvival-all",
                    "alexscaves-up", "logisticsnetworks"}
    assert all(p.kind == "upgrade" and p.source == "filename" for p in pairs)


def test_pair_mods_by_filename_renamed_and_prefix_added():
    from migration.moddb import pair_mods_by_filename
    pairs = pair_mods_by_filename(
        ["mods/cobblestone_generator-1.2.0-mc1.21.1-neoforge.jar"],
        ["mods/[圆石生成器] cobblestone_generator-1.2.1-mc1.21.1-neoforge.jar"],
    )
    # 版本段不同(1.2.0 vs 1.2.1)→ upgrade(前缀差异被剥,spec §3 T2 样例)
    assert len(pairs) == 1 and pairs[0].kind == "upgrade"
    pairs2 = pair_mods_by_filename(
        ["mods/infernalmobs-1.21.1.3NF.jar"],
        ["mods/[稀有精英怪] infernalmobs-1.21.1.3NF.jar"],
    )
    assert len(pairs2) == 1 and pairs2[0].kind == "renamed"  # 版本签名相同


def test_pair_mods_by_filename_ambiguous_family_skipped():
    from migration.moddb import pair_mods_by_filename
    # 同家族源侧两个候选 → 歧义放弃,不猜
    pairs = pair_mods_by_filename(
        ["mods/x-1.0.jar", "mods/[他] x-2.0.jar"], ["mods/x-3.0.jar"])
    assert pairs == []


def test_modpair_source_in_to_dict_and_registry_default():
    from migration.moddb import ModPair
    p = ModPair(modid="x", kind="upgrade", src_files=["mods/x-1.jar"],
                dst_files=["mods/x-2.jar"], src_version="1", dst_version="2")
    assert p.source == "registry"
    assert p.to_dict()["source"] == "registry"


def test_merge_mod_pairs_registry_wins():
    from migration.moddb import ModPair, merge_mod_pairs
    reg = [ModPair(modid="waystones", kind="upgrade", source="registry",
                   src_files=["mods/[tw] waystones-1.0.1.jar"],
                   dst_files=["mods/[tw] waystones-1.0.2.jar"],
                   src_version="1.0.1", dst_version="1.0.2")]
    fn = [ModPair(modid="waystones", kind="upgrade", source="filename",
                  src_files=["mods/[tw] waystones-1.0.1.jar"],
                  dst_files=["mods/[tw] waystones-1.0.2.jar"],
                  src_version="1.0.1", dst_version="1.0.2"),
          ModPair(modid="caves", kind="upgrade", source="filename",
                  src_files=["mods/caves-1.jar"], dst_files=["mods/caves-2.jar"],
                  src_version="1", dst_version="2")]
    merged = merge_mod_pairs(reg, fn)
    assert len(merged) == 2
    by_id = {p.modid: p for p in merged}
    assert by_id["waystones"].source == "registry"  # 覆盖文件冲突时 registry 优先
    assert by_id["caves"].source == "filename"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_moddb.py -q -k "filename or merge or source"`
Expected: FAIL(ImportError/TypeError: unexpected keyword 'source')

- [ ] **Step 3: 实现**

`ModPair` 数据类加默认字段(dataclass 字段顺序:有默认值的放最后),并改 `to_dict`:

```python
@dataclass(frozen=True)
class ModPair:
    """跨侧配对的同一 mod(升级或改名)。"""

    modid: str
    kind: str
    src_files: list[str]
    dst_files: list[str]
    src_version: str | None
    dst_version: str | None
    source: str = "registry"  # "registry"(读 jar mods.toml) | "filename"(快照文件名归一)

    def to_dict(self) -> dict:
        """转为 JSON 可序列化字典(diff --json 的 mod_pairs 元素)。"""
        return {
            "modid": self.modid,
            "kind": self.kind,
            "src_files": self.src_files,
            "dst_files": self.dst_files,
            "src_version": self.src_version,
            "dst_version": self.dst_version,
            "source": self.source,
        }
```

(docstring 的 Attributes 段保留原有内容并补一行 `source` 说明。)`pair_mods` 函数体**零改动**(默认值使其自动 source="registry")。其后追加:

```python
def pair_mods_by_filename(src_only: list[str], dst_only: list[str]) -> list[ModPair]:
    """mods 桶 to_add/target_only 按归一化家族键配对(纯快照数据,无活体依赖)。

    junction 同体/跨机复放下注册表配对不可用,本函数以文件名为唯一依据;
    同家族任一侧多候选(歧义)→ 整族放弃。版本签名不同 → upgrade,相同 → renamed。

    Args:
        src_only: 源侧独有 jar 相对路径(to_add 条目)。
        dst_only: 目标侧独有 jar 相对路径(target_only 条目)。

    Returns:
        ModPair 列表(source="filename",modid=家族键,按家族键升序)。
    """
    by_src: dict[str, list[str]] = {}
    by_dst: dict[str, list[str]] = {}
    for p in src_only:
        fam = normalize_jar_family(p)[0]
        if fam:
            by_src.setdefault(fam, []).append(p)
    for p in dst_only:
        fam = normalize_jar_family(p)[0]
        if fam:
            by_dst.setdefault(fam, []).append(p)
    pairs: list[ModPair] = []
    for fam in sorted(set(by_src) & set(by_dst)):
        s_list, d_list = by_src[fam], by_dst[fam]
        if len(s_list) != 1 or len(d_list) != 1:
            continue  # 歧义放弃,不猜
        s_sig = normalize_jar_family(s_list[0])[1]
        d_sig = normalize_jar_family(d_list[0])[1]
        pairs.append(
            ModPair(
                modid=fam,
                kind="upgrade" if s_sig != d_sig else "renamed",
                src_files=[s_list[0]],
                dst_files=[d_list[0]],
                src_version=s_sig or None,
                dst_version=d_sig or None,
                source="filename",
            )
        )
    return pairs


def merge_mod_pairs(
    registry_pairs: list[ModPair], filename_pairs: list[ModPair]
) -> list[ModPair]:
    """合并两源配对:registry 优先,其覆盖的文件不再保留 filename 对。"""
    covered = {f for p in registry_pairs for f in (*p.src_files, *p.dst_files)}
    merged = list(registry_pairs)
    for p in filename_pairs:
        if any(f in covered for f in (*p.src_files, *p.dst_files)):
            continue
        merged.append(p)
    return sorted(merged, key=lambda p: p.modid)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_moddb.py tests/test_reporter.py -q`
Expected: PASS(reporter 的 `_pairs()` 无 source 构造靠默认值兼容)

- [ ] **Step 5: 提交**

```bash
git add migration/moddb.py tests/test_moddb.py
git commit -m "feat(moddb): 文件名家族配对 pair_mods_by_filename + ModPair.source + merge(F4 fallback)"
```

---

### Task 6: DiffContext.same_dir + CLI 接线 + 降级测试更新

**Files:**
- Modify: `migration/pipeline.py:295-348`(`DiffContext` 与 `resolve_diff_context`)
- Modify: `migration/cli.py:296-325`(`_cmd_diff` 配对接线与提示)
- Test: `tests/test_pipeline.py`(追加)、`tests/test_cli.py:607-625`(有意更新)

**Interfaces:**
- Consumes: Task 5 的 `pair_mods_by_filename`/`merge_mod_pairs`;现有 `pair_mods`。
- Produces: `DiffContext.same_dir: bool`(默认 False);`diff --json` 的 `mod_pairs` 在复放/降级场景非空(filename 来源)。孤儿规则行为不变(不受 same_dir 影响)。

- [ ] **Step 1: 写 pipeline 失败测试**

`tests/test_pipeline.py` 末尾追加(沿用文件内既有 `_snap(version, game_root)` 助手,见 :271):

```python
def test_resolve_diff_context_same_dir_detected(tmp_path):
    """junction 同体:两侧版本目录 resolve 后同路径 → same_dir=True(ctx 仍可用,孤儿照常)。"""
    root = tmp_path / "root"
    (root / "versions" / "a" / "mods").mkdir(parents=True)
    (root / "versions" / "b").symlink_to(root / "versions" / "a", target_is_directory=True)
    from migration.pipeline import resolve_diff_context

    ctx = resolve_diff_context(_snap("a", str(root)), _snap("b", str(root)))
    assert ctx is not None and ctx.same_dir is True


def test_resolve_diff_context_distinct_dirs_same_dir_false(tmp_path):
    root = tmp_path / "root"
    (root / "versions" / "a" / "mods").mkdir(parents=True)
    (root / "versions" / "b" / "mods").mkdir(parents=True)
    from migration.pipeline import resolve_diff_context

    ctx = resolve_diff_context(_snap("a", str(root)), _snap("b", str(root)))
    assert ctx is not None and ctx.same_dir is False
```

(Windows 下 `symlink_to` 可能因权限失败——若 CI 环境报 OSError,改为 `subprocess.run(["cmd", "/c", "mklink", "/J", ...], check=True)` 建 junction,junction 无需管理员权限。)

- [ ] **Step 2: 写 cli junction 全链路失败测试(复刻服务端工作流)**

`tests/test_cli.py` 末尾追加(复刻六轮语料暴露问题的场景:junction 两侧同体,快照分时拍摄):

```python
def test_diff_junction_same_dir_orphan_ok_registry_pairs_skipped(
        tmp_path, monkeypatch, capsys):
    """junction 同体: 孤儿标注照常(dst=现役恰为判定基准),注册表配对作废,文件名配对兜底。"""
    import subprocess
    import sys
    import os
    from tests.conftest import write_mod_jar

    root = tmp_path / "root"
    va = root / "versions" / "a"
    (va / "mods").mkdir(parents=True)
    (va / "config" / "jade").mkdir(parents=True)
    (va / "config" / "jade" / "presets.json").write_text("{}", encoding="utf-8")
    write_mod_jar(va / "mods" / "create-1.0.jar", "create", "1.0")
    write_mod_jar(va / "mods" / "[tw] waystones-1.0.1.jar", "waystones", "1.0.1")
    # junction b → a(Windows junction 无需管理员权限;非 NT 跳过)
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
    # 换装:a(=b 同体)内的 waystones 换新版——b 快照将拍到新状态
    (va / "mods" / "[tw] waystones-1.0.1.jar").unlink()
    write_mod_jar(va / "mods" / "[tw] waystones-1.0.2.jar", "waystones", "1.0.2")
    assert main(["scan", "b", "--game-root", str(root), "-q"]) == 0
    capsys.readouterr()

    assert main(["diff", "a", "b", "--json"]) == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    # 注册表配对被 same_dir 废止,文件名配对兜底
    assert len(doc["mod_pairs"]) == 1
    assert (doc["mod_pairs"][0]["source"], doc["mod_pairs"][0]["kind"]) == ("filename", "upgrade")
    assert "junction" in captured.err
    # 孤儿规则不受 same_dir 影响:jade 未安装于现役 → config 落 never/orphan
    orphan = [i for i in doc["buckets"]["never"] if i["path"] == "config/jade/presets.json"]
    assert len(orphan) == 1 and orphan[0]["note"] == "orphan"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline.py tests/test_cli.py -q -k "same_dir or junction"`
Expected: pipeline 两个 FAIL(AttributeError: same_dir);junction 测试 FAIL(mod_pairs 为空——注册表恒等零配对,正是语料暴露的现状)

- [ ] **Step 4: 实现 pipeline**

`DiffContext` dataclass 增加默认字段(放最后,不破坏既有构造):

```python
    same_dir: bool = False  # 两侧版本目录 resolve 后同路径(junction 同体)→ 注册表配对不可信
```

`resolve_diff_context` 的 return 改为:

```python
    from .moddb import scan_mods  # 延迟导入避免循环
    return DiffContext(
        src_mods=scan_mods(dirs[0]),
        dst_mods=scan_mods(dirs[1]),
        src_dir=dirs[0],
        dst_dir=dirs[1],
        same_dir=dirs[0].resolve() == dirs[1].resolve(),
    )
```

- [ ] **Step 5: 更新降级测试(spec 记录的一次有意更新)**

`tests/test_cli.py::test_diff_degraded_when_game_root_unreachable` 改为:

```python
def test_diff_degraded_when_game_root_unreachable(tmp_path, monkeypatch, capsys):
    """脱敏 game_root → ctx=None:孤儿/注册表配对降级,文件名配对仍可用(F4 fallback)。"""
    root = _prep_diff_game_root(tmp_path)
    _scan_versions(root, tmp_path, monkeypatch, capsys)
    for name in ("a", "b"):
        p = tmp_path / ".mcmig" / "snapshots" / f"{name}.snapshot.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["game_root"] = r"C:\\definitely\\missing"
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    from migration.cli import main

    assert main(["diff", "a", "b", "--json"]) == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    # 注册表配对降级,但文件名配对仍产出 waystones 1.0.1→1.0.2
    assert len(doc["mod_pairs"]) == 1
    assert doc["mod_pairs"][0]["source"] == "filename"
    assert doc["mod_pairs"][0]["modid"] == "waystones"
    assert "mods 扫描不可用" in captured.err
    # 孤儿不再标注(降级 = 0.6.1 行为)
    orphan = [i for i in doc["buckets"]["never"] if i["path"] == "config/jade/presets.json"]
```

(断言块尾部孤儿断言保持原样,只改 docstring 与 mod_pairs 断言。)

- [ ] **Step 6: 实现 cli 接线**

`migration/cli.py` `_cmd_diff` 中,`pair_mods` import 行扩为:

```python
    from .moddb import (
        generate_orphan_rules,
        load_mod_config_map,
        merge_mod_pairs,
        pair_mods,
        pair_mods_by_filename,
    )
```

降级提示行(:307)改为:

```python
        _print_err("[提示] mods 扫描不可用(game_root 不可达):孤儿标注与注册表配对已跳过,文件名配对仍可用")
```

`report = Differ(...)` 之后、`DiffReporter(...)` 之前的配对段(:324-325)改为:

```python
    # F4 双源配对:registry(读 jar,目录真实独立时)优先;filename(纯快照)兜底
    registry_pairs: list = []
    if ctx is not None and not ctx.same_dir:
        registry_pairs = pair_mods(ctx.src_mods, ctx.dst_mods)
    elif ctx is not None and ctx.same_dir:
        _print_err("[提示] 两侧版本目录指向同一路径(junction?):注册表配对不可用,已使用文件名配对")
    filename_pairs = pair_mods_by_filename(
        [i.path for i in report.mods if i.note == "to_add"],
        [i.path for i in report.mods if i.note == "target_only"],
    )
    pairs = merge_mod_pairs(registry_pairs, filename_pairs)
```

- [ ] **Step 7: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline.py tests/test_cli.py -q`
Expected: PASS(含 `test_diff_json_mod_pairs_and_orphan`:registry 对覆盖 filename 对,仍恰 1 对 modid=waystones)

- [ ] **Step 8: 提交**

```bash
git add migration/pipeline.py migration/cli.py tests/test_pipeline.py tests/test_cli.py
git commit -m "feat(pipeline/cli): F4 双源配对接线 — same_dir 检测/文件名兜底/降级提示更新"
```

---

### Task 7: default_rules never 桶补崩溃残留 glob

**Files:**
- Modify: `migration/data/default_rules.yaml`(never 列表)
- Test: `tests/test_rules.py`(末尾追加)

**Interfaces:**
- Consumes: 无。
- Produces: `hs_err_pid*.log`、`replay_pid*.log` 归 NEVER(Task 10 夹具断言依赖)。

- [ ] **Step 1: 写失败测试**

```python
def test_default_rules_never_crash_dump_logs():
    """F18: JVM 崩溃产物(hs_err/replay)默认不迁,落 never 桶。"""
    from migration.classifier import Classifier
    from migration.classifier import Category
    from migration import rules

    default, _ = rules.load_default_rules("x")
    clf = Classifier(rules.RuleSet.from_layers(default))
    assert clf.classify_path("hs_err_pid60956.log") == Category.NEVER
    assert clf.classify_path("replay_pid42364.log") == Category.NEVER
```

(对齐 `tests/test_rules.py` 既有 import 风格;若文件已导入 Classifier/Category 则合并 import。)

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rules.py -q -k crash`
Expected: FAIL(分类为 UNKNOWN)

- [ ] **Step 3: 改规则文件**

`migration/data/default_rules.yaml` 的 `never:` 列表,`crash-reports/**` 行之后追加:

```yaml
  - hs_err_pid*.log                 # JVM 崩溃转储(五/六轮语料实测落根目录,无迁移价值;F18)
  - replay_pid*.log                 # JVM 崩溃回放记录(同上)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rules.py tests/test_differ.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add migration/data/default_rules.yaml tests/test_rules.py
git commit -m "feat(rules): never 桶收录 hs_err/replay 崩溃残留(F18/F19议题3)"
```

---

### Task 8: reporter rebuilt ⚠ 显示 + mods to_add 裁剪脚注

**Files:**
- Modify: `migration/reporter.py:58-72`(`_display_note`)、`:105-126`(`render`)
- Test: `tests/test_reporter.py`(末尾追加)

**Interfaces:**
- Consumes: Task 3 的 `rebuilt` note。
- Produces: rich 显示 `⚠ rebuilt`;mods 桶含 to_add 时渲染一行脚注。JSON 结构零变化。

- [ ] **Step 1: 写失败测试**

`tests/test_reporter.py` 末尾追加(沿用文件既有 Console 捕获方式;若已有 `_render_to_str` 类助手则复用,否则按下面写):

```python
def test_render_rebuilt_marked_with_warning():
    r = _report_with_mods()
    r.mods.append(DiffItem("mods/x-1.0.jar", None, None, note="rebuilt"))
    from rich.console import Console
    import io as _io
    buf = _io.StringIO()
    DiffReporter(r, src_version="a", dst_version="b").render(
        ReportOptions(), console=Console(file=buf, force_terminal=False, width=200))
    out = buf.getvalue()
    assert "rebuilt" in out and "⚠" in out


def test_render_mods_to_add_footnote():
    r = _report_with_mods()  # 已含 to_add 条目
    from rich.console import Console
    import io as _io
    buf = _io.StringIO()
    DiffReporter(r, src_version="a", dst_version="b").render(
        ReportOptions(), console=Console(file=buf, force_terminal=False, width=200))
    assert "有意删除" in buf.getvalue()


def test_render_no_footnote_without_to_add():
    r = DiffReport()
    r.mods = [DiffItem("mods/x-1.0.jar", None, None, note="shared")]
    from rich.console import Console
    import io as _io
    buf = _io.StringIO()
    DiffReporter(r, src_version="a", dst_version="b").render(
        ReportOptions(), console=Console(file=buf, force_terminal=False, width=200))
    assert "有意删除" not in buf.getvalue()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reporter.py -q -k "rebuilt or footnote"`
Expected: FAIL

- [ ] **Step 3: 实现**

`_display_note` 的 mods 分支改为:

```python
        if bucket == "mods":
            kind = self._pair_by_path.get(item.path)
            if kind:
                note = f"{note} ⇄{kind}"
            if item.note == "rebuilt":
                note = f"⚠ {note}"  # F17: 同名同版本异构建
```

`render` 的桶循环之后(:126 附近,配对汇总行之后)追加:

```python
        if "mods" in self._visible_buckets(opts) and any(
            i.note == "to_add" for i in self.report.mods
        ):
            console.print(
                "[dim]注: to_add=源独有(迁移语义:目标缺→补);若为有意删除/裁剪的 mod 请忽略对应行[/]"
            )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reporter.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add migration/reporter.py tests/test_reporter.py
git commit -m "feat(reporter): rebuilt ⚠标记 + mods to_add 裁剪语义脚注(F19议题1)"
```

---

### Task 9: 三包语料入库(observations 解包 + 夹具脱敏)

**Files:**
- Create: `Reference/observations/mcmigrator_服务端测试_20260914/`(gitignore,本地)
- Create: `Reference/observations/mcmigrator_服务端测试_20260914b/`
- Create: `Reference/observations/mcmigrator_服务端测试_20260919/`
- Create: `tests/fixtures/server_corpus/20260914/r4_pre.json`、`r4_post.json`
- Create: `tests/fixtures/server_corpus/20260914b/r5_pre.json`、`r5_post.json`
- Create: `tests/fixtures/server_corpus/20260919/r6_pre.json`、`r6_post.json`(入库)

**Interfaces:**
- Consumes: 项目根三个语料 zip。
- Produces: Task 10 依赖的六份脱敏夹具(game_root=`C:\fixture\sanitized`,结构与既有 `20260913/` 夹具一致)。

- [ ] **Step 1: 解包三包到 observations(zip 内文件名为 GBK,unzip 会乱码,必须用 Python 解)**

```bash
.venv/Scripts/python.exe - <<'EOF'
import zipfile, os
from pathlib import Path
DST = Path("Reference/observations")
for name in ["mcmigrator_服务端测试_20260914", "mcmigrator_服务端测试_20260914b", "mcmigrator_服务端测试_20260919"]:
    d = DST / name
    with zipfile.ZipFile(f"{name}.zip") as z:
        for info in z.infolist():
            fn = info.filename if info.flag_bits & 0x800 else info.filename.encode("cp437").decode("gbk")
            fn = fn.replace("\\", "/")
            target = d / fn
            if info.is_dir() or fn.endswith("/"):
                target.mkdir(parents=True, exist_ok=True); continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                import shutil; shutil.copyfileobj(src, dst)
    print(name, "OK")
EOF
```

验证:`ls Reference/observations/mcmigrator_服务端测试_20260919/` 应见 `README.md data 报告-服务端测试-20260919.md 环境` 且中文名无乱码;`git status` 应**不含** observations(gitignore 覆盖)。

- [ ] **Step 2: 快照脱敏入库**

```bash
.venv/Scripts/python.exe - <<'EOF'
import json
from pathlib import Path
FIX = Path("tests/fixtures/server_corpus")
OBS = Path("Reference/observations")
PLAN = {
    "20260914":  {"r4-pre.snapshot.json": "r4_pre.json",  "r4-post.snapshot.json": "r4_post.json"},
    "20260914b": {"r5-pre.snapshot.json": "r5_pre.json",  "r5-post.snapshot.json": "r5_post.json"},
    "20260919":  {"r6-pre.snapshot.json": "r6_pre.json",  "r6-post.snapshot.json": "r6_post.json"},
}
for pkg, mapping in PLAN.items():
    out = FIX / pkg; out.mkdir(exist_ok=True)
    for src_name, dst_name in mapping.items():
        doc = json.loads((OBS / f"mcmigrator_服务端测试_{pkg}" / "data" / src_name).read_text("utf-8"))
        doc["game_root"] = "C:\\fixture\\sanitized"  # 脱敏,与既有夹具一致
        (out / dst_name).write_text(json.dumps(doc, ensure_ascii=False, indent=1), "utf-8")
        print(pkg, dst_name, doc["file_count"], "files")
EOF
```

验证:六份文件生成;`game_root` 均为 `C:\fixture\sanitized`;快照 `files` 内路径均为版本内相对路径(无绝对路径泄漏,语料指路已证)。

- [ ] **Step 3: 提交(仅夹具;observations 被 gitignore 挡住)**

```bash
git add tests/fixtures/server_corpus/20260914 tests/fixtures/server_corpus/20260914b tests/fixtures/server_corpus/20260919
git commit -m "test(corpus): 四/五/六轮语料脱敏快照入库 — rebuilt/升级对/混合变更/空转四组黄金对基座"
```

---

### Task 10: 新夹具锚定测试(六桶计数 + 四组黄金对)

**Files:**
- Modify: `tests/test_corpus_regression.py`(ROUNDS 扩展 + 新测试)

**Interfaces:**
- Consumes: Task 9 夹具;Task 3 `rebuilt`;Task 5 `pair_mods_by_filename`;Task 7 never 规则。
- Produces: 无(终态锚定)。

- [ ] **Step 1: 扩展 ROUNDS 与六桶参数化**

`ROUNDS` 字典追加三项(锚定值为**复放语义**实测——纯默认规则、ctx=None;与报告数字的差异是孤儿层与 semantics 两个活体效应,docstring 已注明):

```python
    "20260914": ("r4_pre.json", "r4_post.json",
                 {"to_migrate": 12, "candidate": 1, "mods": 117,
                  "only_in_dst": 0, "identical": 737, "never": 41}),
    "20260914b": ("r5_pre.json", "r5_post.json",
                  {"to_migrate": 0, "candidate": 0, "mods": 112,
                   "only_in_dst": 0, "identical": 750, "never": 47}),  # T4 后 hs_err/replay 3 件 only_in_dst→never
    "20260919": ("r6_pre.json", "r6_post.json",
                 {"to_migrate": 11, "candidate": 16, "mods": 120,
                  "only_in_dst": 8, "identical": 1126, "never": 55}),  # T4 后 candidate 19→16
```

模块 docstring 的"两轮真实生产服实测"改为"六轮真实生产服实测(2026-09)",并补一行:「20260914/0914b/0919 三轮锚定值为复放语义(纯默认规则,无活体目录);与采集报告数字的差异=孤儿层与 properties 语义比较两个活体效应,见 spec §0。」

- [ ] **Step 2: 跑参数化六桶测试**

Run: `.venv/Scripts/python.exe -m pytest tests/test_corpus_regression.py -q`
Expected: 旧 7 项全过;新增 3 项若计数有出入,**以实际输出复核差异并更新锚定值为实测**(差异只允许来自:to_migrate±1(server.properties 语义,复放下应计入)、identical/never 的孤儿层差额——任何 mods/candidate/only_in_dst 数值对不上都是实现 bug,须回查 Task 3/5/7)。

- [ ] **Step 3: 追加黄金对测试**

文件末尾追加:

```python
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
    assert all(p.kind == "upgrade" for p in pairs)


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
```

- [ ] **Step 4: 跑全部语料测试**

Run: `.venv/Scripts/python.exe -m pytest tests/test_corpus_regression.py -q`
Expected: PASS(旧 7 + 新 3 参数化 + 6 黄金对测试)

- [ ] **Step 5: 提交**

```bash
git add tests/test_corpus_regression.py
git commit -m "test(corpus): 四/五/六轮锚定 — rebuilt 黄金对×2/文件名配对×2/崩溃 never/空转心跳"
```

---

### Task 11: README 补充 + 版本收尾 + 全量验证

**Files:**
- Modify: `README.zh-CN.md`、`README.en.md`(mods 桶语义小节补 rebuilt 与配对来源)
- Modify: `pyproject.toml`(version `0.6.2` → `0.6.3`)

**Interfaces:**
- Consumes: 前 10 个任务的全部行为。
- Produces: 发布就绪的批次 C 收尾。

- [ ] **Step 1: README 两份补小节**

在 mods 桶语义小节(批次 B 已建)追加三行语义说明(中文版示例,英文版对译):

```markdown
- `rebuilt`:两侧同名同版本号但内容不同(上游重新打包)——diff 标记,plan 默认保留目标侧并警告,不自动覆盖。
- `mod_pairs` 条目含 `source` 字段:`registry`(读取 jar 内 mods.toml,需两侧版本目录真实独立)或 `filename`(快照文件名家族归一,复放/junction 场景可用)。
- 两侧版本目录指向同一路径(NTFS junction)时,注册表配对自动失效并提示,文件名配对兜底。
```

- [ ] **Step 2: 版本号 0.6.2 → 0.6.3**

`pyproject.toml` 的 `version` 字段;README 两份中出现的版本号字符串同步(搜 `0.6.2`)。

- [ ] **Step 3: 全量验证**

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
.venv/Scripts/python.exe -m ruff check .
```

Expected: pytest 全绿(含既有全部测试,唯一改动是 Task 6 的降级测试);ruff 零告警。

- [ ] **Step 4: 提交**

```bash
git add README.zh-CN.md README.en.md pyproject.toml
git commit -m "chore: README rebuilt/配对来源说明 + 版本 0.6.3(批次 C 收尾)"
```

---

## 收尾核对(对照 spec 验收标准)

1. 既有测试全绿,改动仅 Task 6 降级测试一处(spec §5 预告)。
2. 四个新夹具锚定测试 + 黄金对测试全绿。
3. F17 两例(enigmaticlegacyplus / ScorchedGuns)rebuilt 检出;plan SKIP+警告。
4. F14 五对 / F19 六对文件名配对;删除件新增件不成对。
5. json/toml 键序噪声 → identical/semantics;真实值差异 → modified。
6. 崩溃残留归 never。
7. pytest/ruff 全绿,版本 0.6.3。
