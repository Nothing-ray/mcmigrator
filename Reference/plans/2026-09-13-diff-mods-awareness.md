# diff 层 mod 感知(批次 B)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让独立 `diff` 命令获得 mod 配对(F4)/孤儿标注(F2)/方向语义显示(F3)/properties 语义比较(F12)能力,且迁移行为零变化。

**Architecture:** 新增 `resolve_diff_context`(从快照元数据解析真实版本目录并 scan_mods)作为 F2/F4/F12 共同基座;Differ 通过注入 `content_reader` 回调做 F12 语义复核,分桶逻辑不动;配对作为 additive JSON 键 `mod_pairs` + rich 标记注入渲染层。game_root 不可达时全部降级为今天的行为。

**Tech Stack:** Python 3.11+ 标准库(zipfile/pathlib),pytest,ruff;无新依赖。

**Spec:** `Reference/specs/2026-09-13-diff-mods-awareness-design.md`(实现前先读,本计划从它出发)。

## Global Constraints

- 所有注释/docstring 中文;公有函数 Google 风格中文 docstring;全部函数签名带类型提示
- 文件 UTF-8 无 BOM;路径一律 `pathlib.Path`;JSON 读写显式 `encoding="utf-8"`
- 规则/映射不硬编码;每条规则带 `reason`
- stderr 用 `cli._print_err`,不污染 `--json` stdout
- **行为红线**: planner `_MOD_NOTE_MAP`、executor、六桶结构、现有 JSON 键名零变化;plan/swap/migrate 管线不传 `content_reader`(字节比较,与今天一致)
- 现有测试(尤其 `tests/test_corpus_regression.py` 5 个)不允许改动且必须全过
- 测试导入用绝对路径 `from migration.xxx import ...`;运行命令 `.venv/Scripts/python -m pytest`,lint `.venv/Scripts/python -m ruff check .`
- 分支 `feat/server-scenario`(延续批次 A),每任务一提交

---

### Task 1: `textcompare.py` — properties 语义等价判定(F12 核心)

**Files:**
- Create: `migration/textcompare.py`
- Test: `tests/test_textcompare.py`

**Interfaces:**
- Consumes: 无(纯函数,零依赖)
- Produces: `properties_semantic_equal(a: bytes, b: bytes) -> bool` — Task 3 的 Differ 依赖此签名

- [ ] **Step 1: 写失败测试**

```python
"""textcompare 单测:properties 语义等价判定(F12)。"""

from migration.textcompare import properties_semantic_equal


def test_identical_bytes_equal():
    assert properties_semantic_equal(b"a=1\nb=2\n", b"a=1\nb=2\n")


def test_escape_difference_ignored():
    # vanilla Properties.store 会把值内冒号转义;语义相同
    assert properties_semantic_equal(b"level-type=minecraft:normal\n",
                                     b"level-type=minecraft\\:normal\n")


def test_comment_and_timestamp_ignored():
    # 时间戳表头行每次保存必变,不参与语义
    a = b"#Minecraft server properties\n#Sat Sep 12 23:56:00 CST 2026\nview-distance=8\n"
    b = b"#Minecraft server properties\n#Sun Sep 13 20:00:00 CST 2026\nview-distance=8\n"
    assert properties_semantic_equal(a, b)


def test_bom_difference_ignored():
    assert properties_semantic_equal(b"\ufeffview-distance=8\n", b"view-distance=8\n")


def test_key_order_ignored():
    assert properties_semantic_equal(b"a=1\nb=2\n", b"b=2\na=1\n")


def test_real_value_change_detected():
    assert not properties_semantic_equal(b"view-distance=8\n", b"view-distance=10\n")


def test_key_added_detected():
    assert not properties_semantic_equal(b"a=1\n", b"a=1\npause-when-empty-seconds=60\n")


def test_unicode_escape_vs_raw_equal():
    # \uXXXX 转义与原生 UTF-8 同值
    assert properties_semantic_equal("motd=龙域群岛\n".encode("utf-8"),
                                     "motd=\\u9f99\\u57df\\u7fa4\\u5c9b\n".encode("ascii"))
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_textcompare.py -q`
Expected: FAIL `ModuleNotFoundError: No module named 'migration.textcompare'`

- [ ] **Step 3: 最小实现**

```python
""".properties 语义级比较(F12):消灭 java.util.Properties.store 规范化噪声。

server.properties 被 vanilla 重写后产生同值异字节(冒号转义/时间戳表头/BOM),
字节比较误报 modified;本模块按 key→value 语义比对,忽略注释与转义差异。
"""

from __future__ import annotations

_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "f": "\f"}


def _unescape(s: str) -> str:
    """反转义 Properties 序列(\\: \\= \\\\ \\uXXXX 等);孤立反斜杠按字面保留。"""
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in _ESCAPES:
                out.append(_ESCAPES[nxt])
                i += 2
                continue
            if nxt == "u" and i + 6 <= len(s):
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass  # 非 4 位十六进制,反斜杠按字面处理
            out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse_properties(data: bytes) -> dict[str, str]:
    """解析 properties 字节流为 key→value 字典(忽略注释/空行/BOM;不处理续行)。"""
    text = data.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    props: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        # 首个未转义的 '=' 或 ':' 为分隔符(MC 写出的均为 '=';空白分隔不出现,不做)
        sep = -1
        for i, ch in enumerate(line):
            if ch in "=:" and (i == 0 or line[i - 1] != "\\"):
                sep = i
                break
        if sep == -1:
            props[_unescape(line)] = ""
        else:
            props[_unescape(line[:sep].strip())] = _unescape(line[sep + 1:].strip())
    return props


def properties_semantic_equal(a: bytes, b: bytes) -> bool:
    """判定两份 .properties 内容语义等价(键值集合全等,忽略注释/转义/键序/BOM)。

    Args:
        a: 源侧文件字节内容。
        b: 目标侧文件字节内容。

    Returns:
        True 表示语义等价(差异仅为规范化噪声)。
    """
    return _parse_properties(a) == _parse_properties(b)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_textcompare.py -q`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add migration/textcompare.py tests/test_textcompare.py
git commit -m "feat(textcompare): properties 语义等价判定(F12) — 忽略转义/注释/键序/BOM 噪声"
```

---

### Task 2: `resolve_diff_context` — diff 扫描上下文(F2/F4/F12 基座)

**Files:**
- Modify: `migration/pipeline.py`(模块尾追加)
- Test: `tests/test_pipeline.py`(文件尾追加)

**Interfaces:**
- Consumes: `moddb.scan_mods(version_dir: Path) -> ModRegistry`;`Snapshot.game_root: str`、`Snapshot.version: str`
- Produces:
  - `DiffContext`(frozen dataclass): 字段 `src_mods: ModRegistry` / `dst_mods: ModRegistry` / `src_dir: Path` / `dst_dir: Path`;方法 `read_file(rel_path: str, side: str) -> bytes | None`
  - `resolve_diff_context(src_snap: Snapshot, dst_snap: Snapshot) -> DiffContext | None`
  - Task 6 的 `_cmd_diff` 与 Task 3 的 Differ 注入依赖这两个名字

- [ ] **Step 1: 写失败测试**

```python
"""resolve_diff_context 测试(批次 B F2/F4/F12 基座)。"""

from migration.snapshot import Snapshot


def _snap(version: str, game_root: str) -> Snapshot:
    """构造最小快照对象(scanned_at/hash_mode/file_count 为必填,填占位值)。"""
    return Snapshot(version=version, game_root=game_root,
                    scanned_at="2026-09-13T00:00:00+00:00", hash_mode="tiered",
                    file_count=0, files=[])


def _make_game_root(tmp_path, version: str, modid: str, mod_version: str):
    """建一个含 1 个 mod jar 的最小版本目录,返回 (game_root, 版本目录)。"""
    from tests.conftest import write_mod_jar

    root = tmp_path / "root"
    vdir = root / "versions" / version
    vdir.mkdir(parents=True)
    write_mod_jar(vdir / "mods" / f"{modid}-{mod_version}.jar", modid, mod_version)
    return root, vdir


def test_resolve_context_success(tmp_path):
    from migration.pipeline import resolve_diff_context

    ra, _ = _make_game_root(tmp_path, "a", "waystones", "21.1.42")
    rb, _ = _make_game_root(tmp_path, "b", "waystones", "21.1.44")
    ctx = resolve_diff_context(_snap("a", str(ra)), _snap("b", str(rb)))
    assert ctx is not None
    assert "waystones" in ctx.src_mods and "waystones" in ctx.dst_mods
    assert ctx.src_mods.get("waystones").version == "21.1.42"


def test_resolve_context_missing_dir_returns_none(tmp_path):
    from migration.pipeline import resolve_diff_context

    ra, _ = _make_game_root(tmp_path, "a", "x", "1.0")
    # dst 指向不存在的版本目录(跨机复放/夹具场景)
    assert resolve_diff_context(_snap("a", str(ra)), _snap("ghost", str(ra))) is None


def test_resolve_context_empty_game_root_returns_none():
    from migration.pipeline import resolve_diff_context

    assert resolve_diff_context(_snap("a", ""), _snap("b", "C:\\nonexistent")) is None


def test_context_read_file_roundtrip_and_missing(tmp_path):
    from migration.pipeline import resolve_diff_context

    ra, va = _make_game_root(tmp_path, "a", "x", "1.0")
    (va / "server.properties").write_bytes(b"motd=hi\n")
    rb, _ = _make_game_root(tmp_path, "b", "x", "1.0")
    ctx = resolve_diff_context(_snap("a", str(ra)), _snap("b", str(rb)))
    assert ctx.read_file("server.properties", "src") == b"motd=hi\n"
    assert ctx.read_file("server.properties", "dst") is None  # 读取失败 → None
    assert ctx.read_file("server.properties", "unknown-side") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_pipeline.py -k resolve_context -q`
Expected: FAIL `ImportError: cannot import name 'resolve_diff_context'`

- [ ] **Step 3: 最小实现**(追加到 `migration/pipeline.py` 模块尾)

```python
@dataclass(frozen=True)
class DiffContext:
    """独立 diff 的扫描上下文:两侧 mod 注册表 + 真实版本目录(F2/F4/F12 共同基座)。"""

    src_mods: "ModRegistry"
    dst_mods: "ModRegistry"
    src_dir: Path
    dst_dir: Path

    def read_file(self, rel_path: str, side: str) -> bytes | None:
        """按侧读取版本目录内文件字节内容;文件缺失/IO 失败返回 None。

        Args:
            rel_path: 版本内相对路径(正斜杠)。
            side: "src" 或 "dst";其他值视为不存在。
        """
        root = self.src_dir if side == "src" else self.dst_dir
        try:
            return (root / rel_path).read_bytes()
        except OSError:
            return None


def resolve_diff_context(src_snap: Snapshot, dst_snap: Snapshot) -> DiffContext | None:
    """从两份快照的 game_root+version 解析各自版本目录并扫描 mods。

    任一侧目录不可达(跨机复放/夹具/手动删除)→ 返回 None,调用方降级为
    纯快照对比(与 0.6.1 行为逐字节一致)。版本目录 = <game_root>/versions/<version>,
    服务端 NTFS Junction 影子根同样成立。

    Args:
        src_snap: 源侧快照。
        dst_snap: 目标侧快照。

    Returns:
        DiffContext,或 None(无法解析/不可达)。
    """
    dirs: list[Path] = []
    for snap in (src_snap, dst_snap):
        if not snap.game_root:
            return None
        vdir = Path(snap.game_root) / "versions" / snap.version
        if not vdir.is_dir():
            return None
        dirs.append(vdir)
    from .moddb import scan_mods  # 延迟导入避免循环
    return DiffContext(
        src_mods=scan_mods(dirs[0]),
        dst_mods=scan_mods(dirs[1]),
        src_dir=dirs[0],
        dst_dir=dirs[1],
    )
```

若 `pipeline.py` 顶部缺 `from dataclasses import dataclass`,补上;`ModRegistry` 仅作类型注解
(字符串形式 `"ModRegistry"`),无需顶层导入。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_pipeline.py -q`
Expected: 全部通过(既有测试不回归)

- [ ] **Step 5: 提交**

```bash
git add migration/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): resolve_diff_context — 快照元数据→版本目录扫描(F2/F4/F12 基座)"
```

---

### Task 3: Differ 注入 `content_reader` + `semantics` note(F12 接入)

**Files:**
- Modify: `migration/differ.py`
- Test: `tests/test_differ.py`(文件尾追加)

**Interfaces:**
- Consumes: Task 1 的 `properties_semantic_equal(a: bytes, b: bytes) -> bool`
- Produces:
  - `Differ(src_entries, dst_entries, classifier, modpack_swap=False, content_reader=None)`,
    其中 `content_reader: Callable[[str, str], bytes | None]`(参数 `rel_path, side`)
  - identical 桶新 note 值 `"semantics"`(additive;planner 对非 verified 一律 medium,无需改)

- [ ] **Step 1: 写失败测试**

```python
"""F12:Differ properties 语义复核(注入 content_reader)。"""

import pytest

from migration import rules
from migration.classifier import Classifier
from migration.differ import Differ
from migration.rules import Category, Rule
from migration.snapshot import FileEntry


def _clf():
    return Classifier(rules.RuleSet.from_layers(*rules.load_default_rules("mini")))


def _must_migrate_clf():
    return Classifier(rules.RuleSet(rules=[Rule(match="server.properties", decide=Category.MUST_MIGRATE)]))


def _reader(pairs: dict[tuple[str, str], bytes]):
    """构造假 content_reader;未登记的读取返回 None。"""
    return lambda rel, side: pairs.get((rel, side))


A = b"view-distance=8\n"          # 未转义
B = b"view-distance=8\n#Sat Sep 12\n"  # 多了时间戳注释,语义等价
C = b"view-distance=10\n"          # 真实差异


def test_semantics_equal_properties_lands_identical():
    d = Differ([FileEntry("config/x.properties", 1, "a"), ], [FileEntry("config/x.properties", 2, "b")],
               _clf(), content_reader=_reader({("config/x.properties", "src"): A,
                                               ("config/x.properties", "dst"): B})).diff()
    assert [i.note for i in d.identical] == ["semantics"]


def test_must_migrate_semantics_equal_skips_migration():
    # 语义等价的 server.properties: identical(SKIP) 而非 to_migrate(COPY)
    d = Differ([FileEntry("server.properties", 1, "a")], [FileEntry("server.properties", 2, "b")],
               _must_migrate_clf(), content_reader=_reader({("server.properties", "src"): A,
                                                            ("server.properties", "dst"): B})).diff()
    assert [i.note for i in d.identical] == ["semantics"]
    assert d.to_migrate == []


def test_real_difference_still_modified():
    d = Differ([FileEntry("config/x.properties", 1, "a")], [FileEntry("config/x.properties", 2, "b")],
               _clf(), content_reader=_reader({("config/x.properties", "src"): A,
                                               ("config/x.properties", "dst"): C})).diff()
    assert [i.note for i in d.candidate] == ["modified"]


def test_reader_none_falls_back_to_byte_compare():
    d = Differ([FileEntry("config/x.properties", 1, "a")], [FileEntry("config/x.properties", 2, "b")],
               _clf(), content_reader=_reader({})).diff()
    assert [i.note for i in d.candidate] == ["modified"]


def test_non_properties_never_triggers_reader():
    d = Differ([FileEntry("config/x.toml", 1, "a")], [FileEntry("config/x.toml", 2, "b")],
               _clf(), content_reader=_reader({("config/x.toml", "src"): A,
                                               ("config/x.toml", "dst"): B})).diff()
    assert [i.note for i in d.candidate] == ["modified"]


def test_md5_equal_ignores_reader():
    d = Differ([FileEntry("config/x.properties", 1, "same")], [FileEntry("config/x.properties", 1, "same")],
               _clf(), content_reader=_reader({("config/x.properties", "src"): A,
                                               ("config/x.properties", "dst"): C})).diff()
    assert [i.note for i in d.identical] == ["verified"]
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_differ.py -k semantics -q`
Expected: FAIL `TypeError: Differ.__init__() got an unexpected keyword argument 'content_reader'`

- [ ] **Step 3: 最小实现**

`migration/differ.py` 三处修改:

1. 导入区追加:
```python
from collections.abc import Callable

from .textcompare import properties_semantic_equal

ContentReader = Callable[[str, str], "bytes | None"]  # (rel_path, side) → 内容;None=读取失败
```

2. `DiffItem` docstring 的 identical 行改为:
```
    - identical:              verified / size-based / semantics
```

3. `Differ.__init__` 追加参数并保存;新增方法;两处调用点替换:

```python
    def __init__(
        self,
        src_entries: list[FileEntry],
        dst_entries: list[FileEntry],
        classifier: Classifier,
        modpack_swap: bool = False,
        content_reader: ContentReader | None = None,
    ) -> None:
        self.src = {e.path: e for e in src_entries}
        self.dst = {e.path: e for e in dst_entries}
        self.classifier = classifier
        self.modpack_swap = modpack_swap
        self.content_reader = content_reader
```

```python
    def _same_content_semantic(self, path: str, s: FileEntry, d: FileEntry) -> tuple[bool, str]:
        """内容比较:F12 仅在 md5 都存在且不同、且为 .properties、且读取成功时做语义复核。"""
        if s.md5 is not None and d.md5 is not None and s.md5 != d.md5:
            if path.endswith(".properties") and self.content_reader is not None:
                a = self.content_reader(path, "src")
                b = self.content_reader(path, "dst")
                if a is not None and b is not None and properties_semantic_equal(a, b):
                    return True, "semantics"
            return False, "verified"
        return self._same_content(s, d)
```

`diff()` 内两处 `same, how = self._same_content(s, d)`(MUST_MIGRATE 分支与 UNKNOWN 分支)
替换为 `same, how = self._same_content_semantic(path, s, d)`。

- [ ] **Step 4: 运行确认通过 + 既有回归**

Run: `.venv/Scripts/python -m pytest tests/test_differ.py tests/test_corpus_regression.py tests/test_planner.py -q`
Expected: 全部通过(corpus 夹具无 content_reader → 字节路径,计数不变)

- [ ] **Step 5: 提交**

```bash
git add migration/differ.py tests/test_differ.py
git commit -m "feat(differ): content_reader 注入 + semantics note — properties 语义复核(F12)"
```

---

### Task 4: `pair_mods` — 升级/改名配对(F4 核心)

**Files:**
- Modify: `migration/moddb.py`(模块尾追加)
- Test: `tests/test_moddb.py`(文件尾追加)

**Interfaces:**
- Consumes: `ModRegistry.add(ModInfo(...))`、`ModRegistry.modids()`、`ModRegistry.get(modid)`、`ModInfo(modid, version, jar_filename, neoforge_range, embedded_in)`
- Produces:
  - `ModPair`(frozen dataclass): `modid: str` / `kind: str`("upgrade"|"renamed") / `src_files: list[str]` / `dst_files: list[str]` / `src_version: str | None` / `dst_version: str | None`;方法 `to_dict() -> dict`
  - `pair_mods(src_mods: ModRegistry, dst_mods: ModRegistry) -> list[ModPair]`
  - Task 5 的 reporter 与 Task 6 的 `_cmd_diff` 依赖

- [ ] **Step 1: 写失败测试**

```python
"""pair_mods 测试(批次 B F4):同 modid 跨版本升级/同 jar 改名配对。"""

from migration.moddb import ModInfo, ModRegistry, pair_mods


def _reg(*infos: ModInfo) -> ModRegistry:
    r = ModRegistry()
    for i in infos:
        r.add(i)
    return r


def _info(modid, version, jar):
    return ModInfo(modid=modid, version=version, jar_filename=jar, neoforge_range=None)


def test_upgrade_pair():
    pairs = pair_mods(
        _reg(_info("waystones", "21.1.42", "[传送石碑／指路石] waystones-neoforge-1.21.1-21.1.42.jar")),
        _reg(_info("waystones", "21.1.44", "[传送石碑／指路石] waystones-neoforge-1.21.1-21.1.44.jar")),
    )
    assert len(pairs) == 1
    p = pairs[0]
    assert (p.modid, p.kind) == ("waystones", "upgrade")
    assert p.src_files == ["mods/[传送石碑／指路石] waystones-neoforge-1.21.1-21.1.42.jar"]
    assert p.dst_files == ["mods/[传送石碑／指路石] waystones-neoforge-1.21.1-21.1.44.jar"]
    assert (p.src_version, p.dst_version) == ("21.1.42", "21.1.44")
    assert p.to_dict()["kind"] == "upgrade"


def test_renamed_pair():
    pairs = pair_mods(
        _reg(_info("infernalmobs", "1.21.1.3NF", "infernalmobs-1.21.1.3NF.jar")),
        _reg(_info("infernalmobs", "1.21.1.3NF", "[稀有精英怪] infernalmobs-1.21.1.3NF.jar")),
    )
    assert len(pairs) == 1 and pairs[0].kind == "renamed"


def test_same_version_same_name_not_paired():
    pairs = pair_mods(_reg(_info("x", "1.0", "x-1.0.jar")), _reg(_info("x", "1.0", "x-1.0.jar")))
    assert pairs == []


def test_one_side_only_not_paired():
    pairs = pair_mods(_reg(_info("a", "1.0", "a-1.0.jar")),
                      _reg(_info("b", "1.0", "b-1.0.jar")))
    assert pairs == []


def test_empty_version_treated_as_none():
    pairs = pair_mods(_reg(_info("x", "", "x.jar")), _reg(_info("x", "1.0", "x-1.0.jar")))
    assert len(pairs) == 1 and pairs[0].src_version is None
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_moddb.py -k pair_mods -q`
Expected: FAIL `ImportError: cannot import name 'pair_mods'`

- [ ] **Step 3: 最小实现**(追加到 `migration/moddb.py`)

```python
@dataclass(frozen=True)
class ModPair:
    """跨侧配对的同一 mod(升级或改名)。

    Attributes:
        modid: mod 标识符。
        kind: "upgrade"(同 modid 异版本) 或 "renamed"(同 modid 同版本异文件名)。
        src_files: 源侧 jar 相对路径(通常 1 个)。
        dst_files: 目标侧 jar 相对路径。
        src_version: 源侧版本(空串视为 None)。
        dst_version: 目标侧版本(空串视为 None)。
    """

    modid: str
    kind: str
    src_files: list[str]
    dst_files: list[str]
    src_version: str | None
    dst_version: str | None

    def to_dict(self) -> dict:
        """转为 JSON 可序列化字典(diff --json 的 mod_pairs 元素)。"""
        return {
            "modid": self.modid,
            "kind": self.kind,
            "src_files": self.src_files,
            "dst_files": self.dst_files,
            "src_version": self.src_version,
            "dst_version": self.dst_version,
        }


def pair_mods(src_mods: ModRegistry, dst_mods: ModRegistry) -> list[ModPair]:
    """按 modid 配对两侧注册表,产出升级/改名清单。

    - 两侧均有该 modid:版本不同 → upgrade;版本相同但 jar 文件名不同 → renamed;
      版本与文件名均相同 → 不配对(已是 shared)。
    - 仅一侧有 → 不配对(维持 to_add/target_only 原语义,planner 行为不变)。
    - 多 jar 同 modid(注册表按 modid 去重,罕见)整组按单条处理,不做逐 jar 拆分。

    Args:
        src_mods: 源侧 mod 注册表。
        dst_mods: 目标侧 mod 注册表。

    Returns:
        ModPair 列表(按 modid 升序)。
    """
    pairs: list[ModPair] = []
    for modid in sorted(src_mods.modids() & dst_mods.modids()):
        s = src_mods.get(modid)
        d = dst_mods.get(modid)
        if s is None or d is None:
            continue
        if s.version != d.version:
            kind = "upgrade"
        elif s.jar_filename != d.jar_filename:
            kind = "renamed"
        else:
            continue
        pairs.append(
            ModPair(
                modid=modid,
                kind=kind,
                src_files=[f"mods/{s.jar_filename}"],
                dst_files=[f"mods/{d.jar_filename}"],
                src_version=s.version or None,
                dst_version=d.version or None,
            )
        )
    return pairs
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_moddb.py -q`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add migration/moddb.py tests/test_moddb.py
git commit -m "feat(moddb): pair_mods 升级/改名配对(F4) — ModPair 数据类 + modid 分组判定"
```

---

### Task 5: DiffReporter 渲染层 — `mod_pairs` JSON + F4 标记 + F3 方向提示

**Files:**
- Modify: `migration/reporter.py`
- Test: `tests/test_reporter.py`(文件尾追加)

**Interfaces:**
- Consumes: Task 4 的 `ModPair` 与其 `to_dict()`;`DiffItem.note` 现有词汇
- Produces:
  - `DiffReporter(report, *, src_version, dst_version, mod_pairs: list[ModPair] | None = None)`
  - `to_json()` 顶层恒含 `"mod_pairs": [...]`(无配对为 `[]`,additive)
  - rich 渲染: candidate `new` → 显示 `new ←仅源`;only_in_dst `target_only` → `target_only →仅目标`;mods 桶配对行 → `to_add ⇄upgrade` / `target_only ⇄upgrade` / `to_add ⇄renamed` 等;表尾脚注 `配对: ⇄upgrade ×N · ⇄renamed ×M`
  - Task 6 的 `_cmd_diff` 依赖新构造参数

- [ ] **Step 1: 写失败测试**

```python
"""批次 B 渲染层测试:F4 配对标记 + F3 方向提示 + mod_pairs JSON。"""

import json

from migration.differ import DiffItem, DiffReport
from migration.moddb import ModPair
from migration.reporter import DiffReporter, ReportOptions


def _pairs():
    return [
        ModPair(modid="waystones", kind="upgrade",
                src_files=["mods/waystones-42.jar"], dst_files=["mods/waystones-44.jar"],
                src_version="21.1.42", dst_version="21.1.44"),
    ]


def _report_with_mods():
    r = DiffReport()
    r.mods = [
        DiffItem("mods/waystones-42.jar", None, None, note="to_add"),
        DiffItem("mods/waystones-44.jar", None, None, note="target_only"),
    ]
    r.candidate = [DiffItem("config/new-stuff.toml", None, None, note="new")]
    r.only_in_dst = [DiffItem("config/dst-only.toml", None, None, note="target_only")]
    return r


def test_to_json_contains_mod_pairs_additive():
    doc = json.loads(DiffReporter(_report_with_mods(), src_version="a", dst_version="b",
                                  mod_pairs=_pairs()).to_json())
    assert doc["mod_pairs"][0]["modid"] == "waystones"
    assert doc["mod_pairs"][0]["kind"] == "upgrade"
    # 六桶键与 note 词汇不变
    assert set(doc["buckets"]) == {"to_migrate", "candidate", "mods", "only_in_dst", "identical", "never"}
    assert doc["buckets"]["mods"][0]["note"] == "to_add"


def test_to_json_mod_pairs_default_empty():
    doc = json.loads(DiffReporter(_report_with_mods(), src_version="a", dst_version="b").to_json())
    assert doc["mod_pairs"] == []


def test_render_pair_marker_and_direction_hints(capsys):
    DiffReporter(_report_with_mods(), src_version="a", dst_version="b",
                 mod_pairs=_pairs()).render(ReportOptions(show_identical=True, show_never=True))
    out = capsys.readouterr().out
    assert "to_add ⇄upgrade" in out
    assert "target_only ⇄upgrade" in out
    assert "new ←仅源" in out
    assert "target_only →仅目标" in out
    assert "配对: ⇄upgrade ×1" in out
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_reporter.py -k "mod_pairs or direction" -q`
Expected: FAIL `TypeError: ... unexpected keyword argument 'mod_pairs'`

- [ ] **Step 3: 最小实现**

`migration/reporter.py` 修改:

1. 导入区追加 `from collections import Counter` 与 `from .moddb import ModPair`。
2. `DiffReporter.__init__` 与新增显示逻辑:

```python
    def __init__(
        self,
        report: DiffReport,
        *,
        src_version: str,
        dst_version: str,
        mod_pairs: list[ModPair] | None = None,
    ) -> None:
        self.report = report
        self.src_version = src_version
        self.dst_version = dst_version
        self.mod_pairs = mod_pairs or []
        # path → 配对类型(F4 rich 标记用)
        self._pair_by_path: dict[str, str] = {}
        for p in self.mod_pairs:
            for f in (*p.src_files, *p.dst_files):
                self._pair_by_path[f] = p.kind

    def _display_note(self, bucket: str, item: DiffItem) -> str:
        """rich 显示用 note:F3 方向提示(candidate/only_in_dst)+ F4 配对标记(mods)。

        JSON 输出仍用原始 item.note,消费方兼容不受影响。
        """
        note = item.note
        if bucket == "mods":
            kind = self._pair_by_path.get(item.path)
            if kind:
                note = f"{note} ⇄{kind}"
        elif bucket == "candidate" and note == "new":
            note = "new ←仅源"
        elif bucket == "only_in_dst" and note == "target_only":
            note = "target_only →仅目标"
        return note
```

3. `to_json` 的 payload 增加一行(additive,恒存在):
```python
            "mod_pairs": [p.to_dict() for p in self.mod_pairs],
```

4. `render` 中 `tbl.add_row(it.path, it.note)` 改为 `tbl.add_row(it.path, self._display_note(b, it))`;
   `render` 方法体末尾(`for b in ...` 循环之后)追加脚注:
```python
        if self.mod_pairs and "mods" in self._visible_buckets(opts):
            counts = Counter(p.kind for p in self.mod_pairs)
            summary = " · ".join(f"⇄{k} ×{v}" for k, v in sorted(counts.items()))
            console.print(f"[dim]配对: {summary}[/]")
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_reporter.py tests/test_cli.py -q`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add migration/reporter.py tests/test_reporter.py
git commit -m "feat(reporter): mod_pairs JSON(additive) + F4 配对标记 + F3 方向提示与脚注"
```

---

### Task 6: `_cmd_diff` 接线 — ctx + 孤儿规则(F2)+ 配对注入

**Files:**
- Modify: `migration/cli.py` 的 `_cmd_diff`(约 270-308 行)
- Test: `tests/test_cli.py`(文件尾追加)

**Interfaces:**
- Consumes: Task 2 `resolve_diff_context`/`DiffContext.read_file`;Task 3 Differ 新参数;Task 4 `pair_mods`;Task 5 reporter 新参数;既有 `build_ruleset(..., orphan_rules=)` 与 `moddb.generate_orphan_rules(src_entries, dst_mods, override)`、`moddb.load_mod_config_map()`
- Produces: 独立 `diff` 命令全链路行为(JSON `mod_pairs` + never/orphan + semantics + 降级提示)

- [ ] **Step 1: 写失败测试**

```python
"""批次 B _cmd_diff 接线测试:F2 孤儿 / F4 mod_pairs / 降级提示。"""

import json

from tests.conftest import write_mod_jar


def _prep_diff_game_root(tmp_path):
    """建 双版本游戏根:a 有 waystones 旧版 + 共享 create + 孤儿 config;b 有 waystones 新版 + create。

    config/waystones-common.toml 只在 a(源)→ 孤儿;create 两侧都有 → 非孤儿。
    """
    root = tmp_path / "root"
    for ver, wv in (("a", "1.0.1"), ("b", "1.0.2")):
        vdir = root / "versions" / ver
        (vdir / "config").mkdir(parents=True)
        (vdir / "options.txt").write_text("version:x\n", encoding="utf-8")
        write_mod_jar(vdir / "mods" / f"create-1.0.jar", "create", "1.0")
        write_mod_jar(vdir / "mods" / f"[tw] waystones-{wv}.jar", "waystones", wv)
    (root / "versions" / "a" / "config" / "waystones-common.toml").write_text(
        "x = 1\n", encoding="utf-8")
    return root


def _scan_versions(root, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # scan 落 cwd/.mcmig
    from migration.cli import main

    assert main(["scan", "a", "--game-root", str(root), "-q"]) == 0
    assert main(["scan", "b", "--game-root", str(root), "-q"]) == 0


def test_diff_json_mod_pairs_and_orphan(tmp_path, monkeypatch, capsys):
    root = _prep_diff_game_root(tmp_path)
    _scan_versions(root, tmp_path, monkeypatch)
    from migration.cli import main

    assert main(["diff", "a", "b", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    # F4: waystones 1.0.1→1.0.2 升级对(文件名带 [tw] 前缀也按 modid 配对)
    assert len(doc["mod_pairs"]) == 1
    assert (doc["mod_pairs"][0]["modid"], doc["mod_pairs"][0]["kind"]) == ("waystones", "upgrade")
    # F2: 源独有 config/waystones-common.toml 落 never/orphan
    orphan = [i for i in doc["buckets"]["never"] if i["path"] == "config/waystones-common.toml"]
    assert len(orphan) == 1 and orphan[0]["note"] == "orphan"


def test_diff_degraded_when_game_root_unreachable(tmp_path, monkeypatch, capsys):
    # 夹具式脱敏 game_root → ctx=None → mod_pairs=[] + stderr 提示,六桶照常输出
    root = _prep_diff_game_root(tmp_path)
    _scan_versions(root, tmp_path, monkeypatch)
    # 篡改两份快照的 game_root 为不可达路径
    for name in ("a", "b"):
        p = tmp_path / ".mcmig" / "snapshots" / f"{name}.snapshot.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["game_root"] = r"C:\\definitely\\missing"
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    from migration.cli import main

    assert main(["diff", "a", "b", "--json"]) == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert doc["mod_pairs"] == []
    assert "mods 扫描不可用" in captured.err
    # 孤儿也不再标注(降级 = 0.6.1 行为)
    orphan = [i for i in doc["buckets"]["never"] if i["path"] == "config/waystones-common.toml"]
    assert orphan == []
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -k "mod_pairs or degraded" -q`
Expected: FAIL(`mod_pairs` 键不存在)

- [ ] **Step 3: 最小实现**(重写 `_cmd_diff` 中段)

`migration/cli.py` 顶部已有 `from .differ import Differ`;在 `_print` 定义(约 211 行)之后追加:

```python
def _print_err(text: str) -> None:
    """stderr 输出(提示/警告类),不污染 --json 的 stdout。"""
    print(text, file=sys.stderr)
```

`_cmd_diff` 中 `mcmig_dir = cwd / ".mcmig"` 之后、`rs, errs = build_ruleset(...)` 之前插入:

```python
    from .moddb import generate_orphan_rules, load_mod_config_map, pair_mods
    from .pipeline import resolve_diff_context

    ctx = resolve_diff_context(src, dst)
    orphan_rules: list[rules.Rule] = []
    if ctx is not None:
        orphan_rules = generate_orphan_rules(src.files, ctx.dst_mods, load_mod_config_map())
    else:
        _print_err("[提示] mods 扫描不可用(game_root 不可达),配对与孤儿标注已跳过")
```

`build_ruleset(...)` 调用追加参数 `orphan_rules=orphan_rules`(该参数已存在,plan-only 注释
更新为「plan 与独立 diff 共用」)。

`report = Differ(src.files, dst.files, clf).diff()` 与 reporter 构造两行替换为:

```python
    report = Differ(
        src.files, dst.files, clf,
        content_reader=ctx.read_file if ctx is not None else None,
    ).diff()
    pairs = pair_mods(ctx.src_mods, ctx.dst_mods) if ctx is not None else []
    reporter = DiffReporter(report, src_version=args.src, dst_version=args.dst, mod_pairs=pairs)
```

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py tests/test_e2e.py tests/test_corpus_regression.py -q`
Expected: 全部通过(corpus 降级路径锚定不变)

- [ ] **Step 5: 提交**

```bash
git add migration/cli.py tests/test_cli.py
git commit -m "feat(cli): _cmd_diff 接线 — ctx 扫描/孤儿规则(F2)/配对注入(F4)/降级提示"
```

---

### Task 7: 三轮语料夹具(F10 演化对 + F11 漂移黄金对)

**Files:**
- Create: `tests/fixtures/server_corpus/20260913/r3_live.json`、`tests/fixtures/server_corpus/20260913/r3_post.json`
- Modify: `tests/fixtures/server_corpus/README.md`、`tests/test_corpus_regression.py`

**Interfaces:**
- Consumes: 原始语料 `Reference/observations/mcmigrator_服务端测试_20260913/data/r3-{live,post}.snapshot.json`(脱敏 game_root 后入库)
- Produces: 两个新回归测试,期望值(当前规则+无 ctx,已实测):
  - F10 演化对(`20260912/snapshot_9_11_fresh` → `20260913/r3_live`):
    `{to_migrate: 22, candidate: 1, mods: 112, only_in_dst: 91, identical: 636, never: 37}`
  - F11 漂移对(`r3_live` → `r3_post`):
    `{to_migrate: 1, candidate: 1, mods: 112, only_in_dst: 0, identical: 748, never: 37}`

- [ ] **Step 1: 生成脱敏夹具**

```bash
.venv/Scripts/python -c "
import json
from pathlib import Path
jobs = [
    ('Reference/observations/mcmigrator_服务端测试_20260913/data/r3-live.snapshot.json',
     'tests/fixtures/server_corpus/20260913/r3_live.json'),
    ('Reference/observations/mcmigrator_服务端测试_20260913/data/r3-post.snapshot.json',
     'tests/fixtures/server_corpus/20260913/r3_post.json'),
]
for src, dst in jobs:
    d = json.loads(Path(src).read_text(encoding='utf-8'))
    d['game_root'] = r'C:\\fixture\\sanitized'
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8', newline='\n')
    print('OK', dst, d['file_count'])
"
```

Expected: 两行 OK,r3_live=899 / r3_post=899

- [ ] **Step 2: 写回归测试**(追加到 `tests/test_corpus_regression.py`)

```python
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
```

(文件顶部已导入 `Snapshot/Differ/Classifier/build_ruleset/FIXTURES/Path`,直接使用。)

- [ ] **Step 3: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_corpus_regression.py -q`
Expected: 7 passed(原 5 + 新 2;若计数不符,以实测值复核报告后修正期望值——期望值必须来自实测而非臆断)

- [ ] **Step 4: 更新夹具 README**

`tests/fixtures/server_corpus/README.md` 的内容表追加两行:

```markdown
| `20260913/r3_live.json` | r3-live.snapshot.json | 三轮 20.4h 运行后(899 文件,F10 演化对 dst) |
| `20260913/r3_post.json` | r3-post.snapshot.json | 三轮 agent 微调后(899 文件,F11 漂移对) |
```

覆盖场景清单追加一行:

```markdown
- **F10/F11**: 同包演化全量可解释(only_in_dst=91)+ 纯配置漂移最小对照(candidate 恰=2)
- **F12**: server.properties 的 Properties.store 规范化噪声在 R1/R2 中与真实改动混合(无 ctx 字节判定锚定)
```

- [ ] **Step 5: 提交**

```bash
git add tests/fixtures/server_corpus tests/test_corpus_regression.py
git commit -m "test(corpus): 三轮语料夹具 — F10 演化对锚定 + F11 漂移黄金对"
```

---

### Task 8: 文档 + 版本 0.6.2 + 收尾验证

**Files:**
- Modify: `README.zh-CN.md`、`README.en.md`(分类系统章节后追加小节)、`pyproject.toml:7`、`migration/__init__.py:3`

**Interfaces:**
- Consumes: 全部前序任务的最终行为
- Produces: 发布态 0.6.2

- [ ] **Step 1: README(中文)追加 mods 桶语义与 properties 说明**

在「分类系统」章节(「.bak 判定法」小节之后)插入:

```markdown
### diff 的 mods 桶语义与配对

diff 以**迁移源视角**报告:src=迁移源(旧实例),dst=目标(新实例)。
mods 桶标记:`shared`=两侧同名 jar;`to_add`=**源有目标无**(迁移时会补齐);
`target_only`=目标自带。升级/改名由 modid 配对识别(rich 表 `⇄upgrade`/`⇄renamed` 标记 +
表尾配对脚注;`--json` 输出顶层 `mod_pairs` 数组),不再表现为无关的"删旧+增新"。

- 源侧 mod 已被目标移除时,其 config 会被标注为孤儿(`never/orphan`)——独立 `diff` 与 `plan` 语义一致
- `*.properties`(如服务端 `server.properties`)在字节不同但键值语义相同时
  (vanilla 重写导致的转义/时间戳/编码噪声)报告为 `identical/semantics` 而非 modified
- 以上两项依赖快照的 `game_root` 可达;不可达时(跨机复放)自动降级为纯字节对比,stderr 提示一行
```

英文 README 同步(「Classification system」章节):

```markdown
### diff mods-bucket semantics & pairing

diff reports from the **migration-source frame**: src = source (old instance), dst = target (new instance).
mods-bucket notes: `shared` = same-named jar on both sides; `to_add` = **src-only** (migrated over);
`target_only` = shipped by target. Upgrades/renames are paired by modid (rich-table `⇄upgrade`/`⇄renamed`
markers + a pairing footnote; top-level `mod_pairs` array in `--json` output) instead of unrelated
remove+add pairs.

- When a mod was removed on the target side, its config is flagged as orphan (`never/orphan`) — standalone `diff` now matches `plan`
- `*.properties` files (e.g. server `server.properties`) that differ in bytes but not in key/value
  semantics (vanilla rewrite noise: escaping/timestamp/BOM) are reported as `identical/semantics`
- Both features require the snapshot's `game_root` to be reachable; otherwise diff degrades to pure
  byte comparison with a one-line stderr hint
```

- [ ] **Step 2: 版本号**

`pyproject.toml` `version = "0.6.1"` → `"0.6.2"`;`migration/__init__.py` `__version__ = "0.6.1"` → `"0.6.2"`。

- [ ] **Step 3: 全量验证**

Run:
```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m migration doctor
```
Expected: 全部 passed(批次 A 后 317 + 本批新增约 25 = 340+ 量级);ruff 零问题;doctor 清单校验通过(批次 B 未改 data/ 文件,manifest 无需重生成)。

- [ ] **Step 4: spec 验收核对**

逐条核对 spec §6 七项验收标准,在提交信息外的总结中列出核对结果(尤其:语料夹具 5 测试未改动、plan/swap/migrate 测试零改动)。

- [ ] **Step 5: 提交**

```bash
git add README.zh-CN.md README.en.md pyproject.toml migration/__init__.py
git commit -m "chore: README mods 桶语义/properties 说明 + 版本 0.6.2(批次 B 收尾)"
```
