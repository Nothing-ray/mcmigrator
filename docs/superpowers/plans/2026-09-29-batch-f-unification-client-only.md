# 批次F：三机制收口 + client_only 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把配对阶梯/diff 编排/自比对检测三处特判补丁统一为键格、管线下沉、快照身份三个正常机制，并落地 F30① client_only 标注与五级配对。

**Architecture:** moddb 内部改表驱动键格（1-4 级行为零变化 + 新增装饰词闭集第五级）；`cli._cmd_diff` 编排下沉 `pipeline.run_diff`（DiffOutcome 含 notices），reporter 注记一等化；Snapshot 增可选 `resolved_root` 字段支撑自比对主判；`data/client_mods.yaml` 清单经统一注记通道渲染。plan 命令顺带获得 ⇄ 注记（plan.json 不持久化配对）。

**Tech Stack:** Python 3.11+（venv）、pytest、ruff 0.15.20、rich/PyYAML/pathspec。

**Spec:** `Reference/specs/2026-09-29-batch-f-unification-client-only-design.md`（含 §0 验证表/§3 机制设计/§5 验收标准；执行者须先读 spec 再动手）

## Global Constraints

- 全部代码注释/docstring **中文**；文件 **UTF-8 无 BOM**；路径一律 `pathlib.Path`
- `migration/data/*.yaml` 任何改动 → **同任务**内重跑 `tools/gen_manifest.py` 并提交更新后的 `migration/data/manifest.sha256`
- `TOOL_VERSION = "0.6.0"`（snapshot.py）与 `SNAPSHOT_FORMAT = 1` **不变**（`resolved_root` 为可选加字段，不 bump 格式版本）
- 版本号 **0.9.0** 两处同步：`pyproject.toml` 的 `[project] version` 与 `migration/__init__.py` 的 `__version__`（仅 T7 动）
- **diff 渲染字节级不变**，唯一允许的例外：client_only 命中行新增 ` client_only` 标记 + 对应 stderr 警示行（T5）；`--json` 输出结构不变
- **配对 1-4 级行为零变化**：六套黄金对夹具（20260914/0919/0920/0921/0923/20260929）断言原样全绿——这是键格重构的等价性哨兵
- 测试基线 **426 项**；唯一有意行为变更是 T6 plan 渲染新增 ⇄ 注记（相关断言随改）与 T3 自比对主判文案（新增场景，不覆盖既有 junction 文案）
- ruff 检查用主仓虚拟环境（`F:\code\mcmigrator\.venv`，ruff==0.15.20）；worktree 里跑测试须用主仓 Python 重建 venv（见任务内说明），Path 默认 python 是 3.10 会缺 tomllib
- 提交信息：中文 conventional commits（如 `feat(moddb): ...` / `refactor(pipeline): ...`）；每个任务一个提交
- 语料原件在主仓（gitignored，worktree 可按绝对路径读）：`F:\code\mcmigrator\Reference\observations\mcmigrator_服务端测试_20260929\`

## Review Focus

规格隐含但无任务测试覆盖、最可能咬人的五类输入（每条已钉到所属任务的测试）：

1. **旧快照（无 `resolved_root` 字段）与新代码混跑**——加载不炸、自比对门控回退到现状行为 → T3 测试 pin（既有九套夹具全量加载即隐式哨兵 + 显式回退单测）
2. **`mcmig diff X X`（同一快照文件自比对）**——复放模式（无活体目录）也要给提示，而非静默 → T3 CLI 测试 pin
3. **闭集外装饰词伪配对**（`create-goggles` vs `create`）——五级不得收敛，否则错误标注满天飞 → T2 反例测试 pin
4. **无活体目录的复放 diff（ctx=None）**——client_only 家族键匹配仍须生效（Frost_dragon 只在快照里） → T5 用 r11 夹具 pin
5. **plan 消费方兼容**——⇄ 注记不得进 plan.json（executor/GUI/旧 plan 文件加载不受影响） → T6 断言 json 键集合 + 旧 plan from_dict 往返

---

### Task 1: r11 黄金对夹具（现状锚定，先于一切重构）

**Files:**
- Create: `tests/fixtures/server_corpus/20260929/r11_pre.json`
- Create: `tests/fixtures/server_corpus/20260929/r11_post.json`
- Modify: `tests/fixtures/server_corpus/README.md`
- Modify: `tests/test_corpus_regression.py`

**Interfaces:**
- Consumes: 语料真值 `F:\code\mcmigrator\Reference\observations\mcmigrator_服务端测试_20260929\diff_r11pre_r11post.json`（主仓绝对路径，gitignored 但磁盘存在；夹具生成后仓库自含，不再依赖该文件）
- Produces: 夹具路径 `FIXTURES / "20260929"` 下的 `r11_pre.json`/`r11_post.json`（T5 的 client_only 测试复用）；`_mods_jar_paths` 已存在于 test_corpus_regression.py:152

- [ ] **Step 1: 生成两份 mods-only 夹具**

用下面的脚本生成（在 worktree 根目录跑，读主仓语料 JSON；size 真值取自 diff 的 src_size/dst_size——旧件侧与《旧件指纹.md》一致，无需二次来源）：

```python
# gen_r11_fixture.py(临时脚本,不提交;或 python - <<'EOF' 直接跑)
import json
from pathlib import Path

SRC = Path(r"F:\code\mcmigrator\Reference\observations\mcmigrator_服务端测试_20260929\diff_r11pre_r11post.json")
OUT = Path("tests/fixtures/server_corpus/20260929")
OUT.mkdir(parents=True, exist_ok=True)
d = json.loads(SRC.read_text(encoding="utf-8"))
mods = d["buckets"]["mods"]

def dump(name: str, version: str, scanned: str, side: str, notes: set[str]) -> None:
    files = [
        {"path": it["path"], "size": it[f"{side}_size"], "md5": None}
        for it in mods if it["note"] in notes and it[f"{side}_size"] is not None
    ]
    payload = {
        "tool_version": "0.6.0", "snapshot_format": 1, "version": version,
        "game_root": "C:\\fixture\\sanitized", "scanned_at": scanned,
        "hash_mode": "tiered", "file_count": len(files), "files": files,
    }
    (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(name, len(files))

dump("r11_pre.json", "r11-pre", "2026-09-29T13:04:43+08:00", "src", {"shared", "to_add"})
dump("r11_post.json", "r11-post", "2026-09-29T13:10:00+08:00", "dst", {"shared", "target_only"})
```

生成后核对：`r11_pre.json` 110 条（95 shared + 15 to_add）、`r11_post.json` 111 条（95 shared + 16 target_only）；`file_count` 与条数一致；UTF-8 无 BOM。再与语料真值交叉验证 15 旧件 size（抽 3 件对照《旧件指纹.md》：waystones .45=1219809、DragonSurvival 2.0.70=13736896、torchmaster=161205）。

- [ ] **Step 2: 写失败测试**

在 `tests/test_corpus_regression.py` 末尾追加（沿用既有 import 与 `_mods_jar_paths`）：

```python
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
    assert by["torchmaster-neoforge"].src_version == "21.1.13"
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
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_corpus_regression.py::test_corpus_20260929_twelve_pairs_renamed_first_show -v`
Expected: FAIL（`FileNotFoundError`：夹具尚未提交——先生成文件再跑则应直接 PASS；本任务红绿顺序为「测试先行、夹具生成即实现」，允许生成后一步到位）

- [ ] **Step 4: 跑通并全量回归**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: 427 passed（426 基线 + 本测试 1）

- [ ] **Step 5: 夹具 README 登记**

`tests/fixtures/server_corpus/README.md` 追加一行（沿用既有表格格式）：`| 20260929 | r11_pre/r11_post | 第十轮语料(服务端会话 r11)合成 mods-only;12 对(11 upgrade+1 renamed)+3删4增;swap 15 排除 |`

- [ ] **Step 6: 提交**

```bash
git add tests/fixtures/server_corpus/20260929/ tests/fixtures/server_corpus/README.md tests/test_corpus_regression.py
git commit -m "test(corpus): r11 黄金对夹具 — 12 对配对/3删4增/swap 锚定(第十轮语料)"
```

---

### Task 2: moddb 键格重构（表驱动五级 + kind 单点）

**Files:**
- Modify: `migration/moddb.py:501-783`（normalize/reduced/platform 区段与 `pair_mods_by_filename`）
- Modify: `migration/moddb.py:601-644`（`pair_mods` kind 判定共用）
- Test: `tests/test_moddb.py`

**Interfaces:**
- Consumes: `normalize_jar_family / _reduced_family / _platform_stripped`（签名不变）；`pair_mods_by_filename(src_only, dst_only) -> list[ModPair]` 公开签名与 1-4 级语义**不变**
- Produces: 模块级 `_DECORATION_WORDS: frozenset[str]`、`_decoration_stripped(family: str, tail: str) -> str`（剥后为空返回 ""）、键格表 `_LATTICE`（`(键函数, 前置条件, kind 函数)` 三元组列表）、`_kind_by_sig(s_sig: str, d_sig: str) -> str`、`_variant_kind_by_tail(s_tail: str, d_tail: str) -> str`（尾缀异→"rebuilt"/同→"renamed"，`pair_mods` 与键格 L2 共用语义单点）

- [ ] **Step 1: 写失败测试（五级 + 覆盖面钉死 + 反例）**

`tests/test_moddb.py` 追加：

```python
def test_decoration_stripped_unit() -> None:
    """五级键函数:减尾键剥平台词+闭集装饰词(任意位置);剥空返回空串。"""
    from migration.moddb import _decoration_stripped
    assert _decoration_stripped("foo-up-neoforge", "") == "foo"
    assert _decoration_stripped("foo", "up") == "foo"          # 尾缀 up 被减尾后剩 foo
    assert _decoration_stripped("frost-dragon", "") == "frost-dragon"  # 非闭集词不动
    assert _decoration_stripped("patch-lib", "") == ""          # 全剥空 → 调用方跳过


def test_pair_level5_decoration_word_forms() -> None:
    """五级(F 批次):非平台装饰词增删形态 — 版本变→upgrade,同版→renamed。"""
    got = pair_mods_by_filename(["mods/foo-up-1.2.3.jar"], ["mods/foo-1.2.4.jar"])
    assert [(p.kind, p.modid) for p in got] == [("upgrade", "foo")]
    got = pair_mods_by_filename(["mods/foo-1.2.3.jar"], ["mods/foo-release-1.2.3.jar"])
    assert [(p.kind, p.modid) for p in got] == [("renamed", "foo")]


def test_pair_level5_closed_set_guard() -> None:
    """闭集外装饰词不得收敛:create-goggles 与 create 是两个 mod,不许伪配。"""
    assert pair_mods_by_filename(
        ["mods/create-goggles-1.0.0.jar"], ["mods/create-6.0.10.jar"]) == []


def test_pair_level5_ambiguity_guard() -> None:
    """五级歧义守卫:同键任一侧多候选整族放弃(与 1-4 级同纪律)。"""
    got = pair_mods_by_filename(
        ["mods/foo-up-1.0.0.jar", "mods/foo-up-2.0.0.jar"],
        ["mods/foo-1.5.0.jar"])
    assert got == []


def test_pair_covered_forms_pinned() -> None:
    """覆盖面实证钉死(spec §3.1):以下形态在一级即配,防未来重构退化。"""
    # 平台词尾缀↔中段互换(版本变)→ upgrade;同版 → renamed
    got = pair_mods_by_filename(["mods/foo-1.2.3-neoforge.jar"], ["mods/foo-neoforge-1.2.4.jar"])
    assert [(p.kind, p.modid) for p in got] == [("upgrade", "foo-neoforge")]
    got = pair_mods_by_filename(["mods/foo-1.2.3-neoforge.jar"], ["mods/foo-neoforge-1.2.3.jar"])
    assert [(p.kind, p.modid) for p in got] == [("renamed", "foo-neoforge")]
    # 变体词换位(版本变)→ upgrade(家族键位置无关)
    got = pair_mods_by_filename(["mods/foo-1.2.3-patch.jar"], ["mods/foo-patch-1.2.4.jar"])
    assert [(p.kind, p.modid) for p in got] == [("upgrade", "foo-patch")]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_moddb.py -v -k "level5 or decoration or covered_forms"`
Expected: FAIL（`ImportError: _decoration_stripped`；五级形态未配对）

- [ ] **Step 3: 实现键格重构**

`migration/moddb.py` 改造（保持 1-4 级行为逐字节等价——既有全部配对测试与六套夹具是哨兵）：

1. `_PLATFORM_WORDS` 之后新增：

```python
# 装饰词闭集(五级,F 批次):语料实见的变体/打包形态词,家族键里任意位置出现即剥除;
# 枚举闭集与 _PLATFORM_WORDS 同哲学——新词随语料出现再扩,防盲目截词伪配
_DECORATION_WORDS = frozenset(
    {"all", "patch", "fix", "feature", "release", "up", "port", "api", "lib", "compat"}
)


def _decoration_stripped(family: str, tail: str) -> str:
    """减尾键先剥平台词再删闭集装饰词(第五级配对键);剥后为空返回空串(调用方跳过)。

    Args:
        family: 家族键(纯字母词 "-" 连接,含尾缀词)。
        tail: 变体尾缀(空串表示无)。

    Returns:
        剥离平台词与装饰词后的键;词全被剥掉时为空串。
    """
    reduced = _reduced_family(family, tail)
    if not reduced:
        return ""
    return "-".join(
        w for w in reduced.split("-")
        if w not in _PLATFORM_WORDS and w not in _DECORATION_WORDS
    )
```

2. kind 判定单点：

```python
def _kind_by_sig(s_sig: str, d_sig: str) -> str:
    """版本签名比较:异→upgrade,同→renamed(键格第 1/4/5 级共用)。"""
    return "upgrade" if s_sig != d_sig else "renamed"


def _variant_kind_by_tail(s_tail: str, d_tail: str) -> str:
    """同版本异名变体判定:尾缀异→rebuilt,同→renamed(键格第 2 级与 pair_mods 共用)。"""
    return "rebuilt" if s_tail != d_tail else "renamed"
```

3. `pair_mods_by_filename` 主体替换为表驱动（docstring 更新为五级说明；公开签名与返回语义不变）：

```python
# 键格:每级 = (键函数, 前置条件, kind 函数);通用循环逐级消费上级残余,
# 任一侧多候选整族放弃。新增配对形态 = 加一行条目,不再复制整段流程(批次F 收口)。
# key: (family, tail) -> str;pred/kind: 接收 normalize_jar_family 三元组 (family, sig, tail)
_LATTICE: list[_Level] = [
    _Level(key=lambda fam, tail: fam, pred=None,
           kind=lambda s, d: _kind_by_sig(s[1], d[1])),                        # 一级(0.6.3)
    _Level(key=_reduced_family,
           pred=lambda s, d: s[1] == d[1] and s[2] != d[2],                    # 同签名+异尾缀
           kind=lambda s, d: "rebuilt"),                                       # 二级(批次D)
    _Level(key=_reduced_family,
           pred=lambda s, d: s[1] != d[1],                                     # 异签名
           kind=lambda s, d: "upgrade"),                                       # 三级(批次E)
    _Level(key=_platform_stripped, pred=None,
           kind=lambda s, d: _kind_by_sig(s[1], d[1])),                        # 四级(批次E)
    _Level(key=_decoration_stripped, pred=None,
           kind=lambda s, d: _kind_by_sig(s[1], d[1])),                        # 五级(批次F)
]
```

其中 `_Level` 为模块内 `@dataclass(frozen=True)`（字段 `key: Callable[[str, str], str]`、`pred: Callable[[tuple[str, str, str], tuple[str, str, str]], bool] | None`、`kind: Callable[[tuple[str, str, str], tuple[str, str, str]], str]`）。循环体：

```python
    pairs: list[ModPair] = []
    paired: set[str] = set()
    for level in _LATTICE:
        ks: dict[str, list[str]] = {}
        kd: dict[str, list[str]] = {}
        for p, (fam, _, tail) in src_norm.items():
            if p in paired or not fam:
                continue
            k = level.key(fam, tail)
            if k:
                ks.setdefault(k, []).append(p)
        for p, (fam, _, tail) in dst_norm.items():
            if p in paired or not fam:
                continue
            k = level.key(fam, tail)
            if k:
                kd.setdefault(k, []).append(p)
        for key in sorted(set(ks) & set(kd)):
            s_list, d_list = ks[key], kd[key]
            if len(s_list) != 1 or len(d_list) != 1:
                continue  # 歧义放弃,不猜
            s, d = s_list[0], d_list[0]
            s_norm_v, d_norm_v = src_norm[s], dst_norm[d]
            if level.pred is not None and not level.pred(s_norm_v, d_norm_v):
                continue
            pairs.append(
                ModPair(
                    modid=key,
                    kind=level.kind(s_norm_v, d_norm_v),
                    src_files=[s], dst_files=[d],
                    src_version=s_norm_v[1] or None, dst_version=d_norm_v[1] or None,
                    source="filename",
                )
            )
            paired.update((s, d))
    pairs.sort(key=lambda p: p.modid)
    return pairs
```

> 等价性注意：现实现的二级/三级共用一次分组、三级在迭代时过滤已配对；键格逐级重分组在数学上等价（分组是未配集合的确定函数）。二级的理论死枝守卫（`s_tail == d_tail → continue`）与三级（`sig 相等 → continue`）分别落在 `pred` 中，语义原样。

4. `pair_mods`（注册表配对）的 rebuilt/renamed 判定改用 `_variant_kind_by_tail`：

```python
        elif s.jar_filename != d.jar_filename:
            # 同版异名:与文件名配对第 2 级共用变体判定单点(F20-3 语义不变)
            s_tail = normalize_jar_family("mods/" + s.jar_filename)[2]
            d_tail = normalize_jar_family("mods/" + d.jar_filename)[2]
            kind = _variant_kind_by_tail(s_tail, d_tail)
```

- [ ] **Step 4: 全量等价回归**

Run: `.venv/Scripts/python -m pytest tests/ -q && .venv/Scripts/python -m ruff check migration/`
Expected: 全绿（426 + T1 的 1 + 本任务 5 个新测试 = 432）；ruff clean

- [ ] **Step 5: 提交**

```bash
git add migration/moddb.py tests/test_moddb.py
git commit -m "refactor(moddb): 配对键格化 — 表驱动五级(装饰词闭集)+kind 判定单点,1-4 级零变化"
```

---

### Task 3: 快照身份字段 resolved_root + 自比对主判

**Files:**
- Modify: `migration/snapshot.py`（Snapshot 字段/save/load）
- Modify: `migration/scanner.py:55-66`（build_snapshot 落 resolved_root）
- Modify: `migration/pipeline.py`（新增 `diff_identity_notices`）
- Modify: `migration/cli.py`（`_cmd_diff` 的 F27 门控改调统一函数）
- Test: `tests/test_snapshot.py`、`tests/test_cli.py`

**Interfaces:**
- Consumes: `DiffContext.same_dir`（pipeline 既有）
- Produces: `Snapshot.resolved_root: str | None = None`（新字段，save 恒写入该键、load 容缺省 None）；`pipeline.diff_identity_notices(src_path: Path, dst_path: Path, src: Snapshot, dst: Snapshot, ctx: DiffContext | None) -> str | None`（返回 stderr 提示行或 None；静默分支由调用方 log.debug）——T4 的 run_diff 消费

- [ ] **Step 1: 写失败测试**

`tests/test_snapshot.py` 追加（无该文件则新建，模块 docstring 说明快照模型测试）：

```python
def test_snapshot_resolved_root_roundtrip_and_legacy(tmp_path: Path) -> None:
    """resolved_root 往返持久化;旧快照(无该键)加载得 None 不炸。"""
    snap = Snapshot(version="v", game_root="C:\\g", scanned_at="2026-09-29T10:00:00+08:00",
                    hash_mode="tiered", file_count=0, files=[], resolved_root="C:\\real\\v")
    p = tmp_path / "s.json"
    snap.save(p)
    loaded = Snapshot.load(p)
    assert loaded.resolved_root == "C:\\real\\v"
    # 旧布局:手写无 resolved_root 键的 JSON
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({
        "tool_version": "0.6.0", "snapshot_format": 1, "version": "v",
        "game_root": "C:\\g", "scanned_at": "t", "hash_mode": "tiered",
        "file_count": 0, "files": []}, ensure_ascii=False), encoding="utf-8")
    assert Snapshot.load(legacy).resolved_root is None


def test_scanner_records_resolved_root(tmp_path: Path) -> None:
    """scan 落盘 resolved_root=版本目录 resolve()(junction 解析后的物理路径)。"""
    ver = tmp_path / "versions" / "v1"
    (ver / "mods").mkdir(parents=True)
    snap, _ = Scanner(ver, "v1").build_snapshot(str(tmp_path))
    assert snap.resolved_root == str(ver.resolve())
```

`tests/test_cli.py` 追加（参照既有 `_force_scanned_at` helper 的夹具搭建方式）：

```python
def test_diff_identity_notices_paths_and_replay() -> None:
    """自比对检测统一:① 同一快照文件 → 提示;② 复放模式(无活体)resolved_root+同刻 → 提示;
    ③ resolved_root 同、刻不同 → None(静默);④ 旧快照缺字段 → 回退 ctx.same_dir+scanned_at。"""
    from migration.pipeline import diff_identity_notices
    # 最小快照工厂(见既有测试的 _write_snapshot 风格)
    ...
    # ① 同文件
    assert diff_identity_notices(p, p, snap_a, snap_a, ctx=None) is not None
    # ② 复放:两文件不同,但 resolved_root 相同 + scanned_at 相同,ctx=None
    assert "自比对" in diff_identity_notices(pa, pb, snap_same_rr_same_time_a, snap_same_rr_same_time_b, None)
    # ③ 同物理根不同刻 → None
    assert diff_identity_notices(pa, pb, snap_rr_t1, snap_rr_t2, None) is None
    # ④ 回退:resolved_root 均 None,ctx.same_dir=True 且同刻 → 现行 F27 文案
    msg = diff_identity_notices(pa, pb, snap_no_rr, snap_no_rr_same, ctx_same_dir)
    assert "同刻" in msg and "junction 同体" in msg


def test_diff_same_snapshot_file_self_hint(capsys=None) -> None:
    """CLI:mcmig diff X X(同名快照)stderr 出自比对提示(复放模式同样触发)。"""
    # 用 CliRunner 跑 diff <名> <名>(快照已存在),断言 stderr 含「自比对」
    ...
```

（测试体内的快照工厂/runner 搭建沿用 `tests/test_cli.py` 既有模式——junction 系列测试 `_force_scanned_at` 附近；实现者按邻接代码风格补全，不许引入新 helper 文件。）

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_snapshot.py tests/test_cli.py -v -k "resolved_root or identity or self_hint"`
Expected: FAIL（`Snapshot.__init__` 无 resolved_root 参数 / `diff_identity_notices` 不存在）

- [ ] **Step 3: 实现**

1. `snapshot.py`：`Snapshot` 增字段 `resolved_root: str | None = None`（docstring 注明「scan 时版本目录 resolve() 结果,自比对/junction 同体判定用;旧快照缺省 None」）；`save()` payload 增 `"resolved_root": self.resolved_root`；`load()` 构造处增 `resolved_root=payload.get("resolved_root")`。`SNAPSHOT_FORMAT` 不变。
2. `scanner.py` `build_snapshot`：Snapshot(...) 增 `resolved_root=str(self.version_dir.resolve())`。
3. `pipeline.py` 新增：

```python
def diff_identity_notices(
    src_path: Path, dst_path: Path,
    src: Snapshot, dst: Snapshot, ctx: DiffContext | None,
) -> str | None:
    """自比对/junction 同体检测(单点,live 与复放同源)。

    主判:两侧为同一快照文件(自比对错误用法);
    佐证:resolved_root 相等(同物理目录)且 scanned_at 相等 → 疑似自比对;
    回退:旧快照无 resolved_root 时用 ctx.same_dir + scanned_at(F27 现状)。
    同物理目录不同刻(影子根标准用法)返回 None,调用方按需 log.debug。
    """
    if src_path.resolve() == dst_path.resolve():
        return ("[提示] 两侧为同一份快照文件(自比对):注册表配对与语义复核不可用,"
                "已使用文件名配对/字节比较")
    same_root = (
        src.resolved_root is not None and src.resolved_root == dst.resolved_root
    ) or (ctx is not None and ctx.same_dir)
    if same_root and src.scanned_at == dst.scanned_at:
        return ("[提示] 两侧快照同刻且版本目录指向同一路径(junction 同体):"
                "注册表配对与语义复核不可用,已使用文件名配对/字节比较")
    return None
```

4. `cli.py` `_cmd_diff` 的 F27 门控段重构为**独立于 ctx 分支**的统一调用（自比对主判在复放模式也要触发）：

```python
    # F27→批次F:自比对/junction 同体检测单点(同文件主判,同根同刻佐证,旧快照回退)
    hint = diff_identity_notices(src_path, dst_path, src, dst, ctx)
    if hint is not None:
        _print_err(hint)
    elif ctx is not None and ctx.same_dir:
        log.debug(
            "junction 同体双快照(不同刻,标准影子根用法):"
            "注册表配对与语义复核不可用,已使用文件名配对/字节比较"
        )
```

原 `elif ctx is not None and ctx.same_dir:` 内的 scanned_at 比较分支整体删除（其逻辑已内聚进 `diff_identity_notices` 的回退臂，文案逐字节相同）。registry 配对条件 `ctx is not None and not ctx.same_dir` 保持不变。**既有 junction 文案逐字节保留**（两个既有测试钉死；同刻 junction 用例走回退臂命中同一文案）。

- [ ] **Step 4: 全量回归**

Run: `.venv/Scripts/python -m pytest tests/ -q && .venv/Scripts/python -m ruff check migration/`
Expected: 全绿（432 + 本任务 4 个新测试 = 436）；junction 既有测试不改动仍绿

- [ ] **Step 5: 提交**

```bash
git add migration/snapshot.py migration/scanner.py migration/pipeline.py migration/cli.py tests/
git commit -m "feat(snapshot): resolved_root 身份字段 + 自比对检测单点(主判同文件,佐证同根同刻)"
```

---

### Task 4: run_diff 管线下沉 + reporter 注记一等化

**Files:**
- Modify: `migration/pipeline.py`（`DiffOutcome` + `run_diff` + `compute_mod_pairs`；`build_plan` 的 `src_only_mods` 统计改集合差+`is_mod_jar`）
- Modify: `migration/cli.py`（`_cmd_diff` 瘦身为参数展开+渲染）
- Modify: `migration/reporter.py`（`DiffReporter._display_note` 注记 parts 化，输出字节不变）
- Test: `tests/test_pipeline.py`（run_diff 单测）、既有 `tests/test_cli.py` 全量为字节锚定

**Interfaces:**
- Consumes: `diff_identity_notices`（T3）、`pair_mods / pair_mods_by_filename / merge_mod_pairs`（T2 后形态）、`Differ.is_mod_jar`、`build_ruleset`、`resolve_diff_context`
- Produces（T5/T6 消费）:

```python
@dataclass
class DiffOutcome:
    """diff 管线产物:六桶报告 + 配对 + 双侧快照 + stderr 提示行。"""
    report: DiffReport
    mod_pairs: list["ModPair"]
    src: Snapshot
    dst: Snapshot
    notices: list[str]

def run_diff(
    cwd: Path, *, src: str, dst: str, modpack_swap: bool = False,
    exclude: Sequence[str] = (), include: Sequence[str] = (),
    rule_files: Sequence[Path] = (),
    mcmig_dir: Path | None = None,        # None → cwd/.mcmig
    game_root: Path | None = None,        # None → 快照走 cwd/.mcmig 且无活体 ctx
) -> DiffOutcome: ...
    # Raises: FileNotFoundError(缺快照,消息 "缺少 {名} 快照") / ValueError(读取失败)

def compute_mod_pairs(
    src: Snapshot, dst: Snapshot,
    src_mods: "ModRegistry | None", dst_mods: "ModRegistry | None",
    *, same_dir: bool = False,
) -> list["ModPair"]:
    """双源配对单点:filename(快照差集+is_mod_jar 过滤)恒算;registry 仅当双侧注册表
    都给出且 same_dir=False 时叠加(merge_mod_pairs registry 优先)。
    run_diff 传 ctx.same_dir;build_plan 传 <src_dir>.resolve()==<dst_dir>.resolve()。"""
```

快照定位语义（与现状 CLI 逐字节对齐）：`game_root` 给出 → data_dir=`game_root/.mcmig`、legacy=`mcmig_dir`（默认 `cwd/.mcmig`），`find_snapshot(data_dir, legacy, name)`；`game_root=None` → `snapshot_path(mcmig_dir or cwd/.mcmig, name)` 直取（无回退、无活体 ctx、孤儿规则跳过并出「mods 扫描不可用」提示行入 notices）。

- [ ] **Step 1: 写失败测试**

`tests/test_pipeline.py` 追加（搭一个 tmp game_root：两个版本目录各放 1-2 个假 jar + 写快照；参照该文件既有 build_plan 测试的目录搭法）：

```python
def test_run_diff_outcome_and_notices(tmp_path: Path) -> None:
    """run_diff 下沉:六桶/配对/快照齐备;swap 提示与「mods 扫描不可用」进 notices。"""
    from migration.pipeline import run_diff
    # tmp game_root:versions/a(旧 jar+options.txt)/versions/b(新 jar);scan 落盘快照
    ...
    out = run_diff(cwd, src="a", dst="b", game_root=game_root)
    assert out.src.version == "a" and out.dst.version == "b"
    assert any(i.path.startswith("mods/") for i in out.report.mods)
    assert isinstance(out.mod_pairs, list)
    # swap 模式:排除提示进 notices(有排除时)
    out_sw = run_diff(cwd, src="a", dst="b", modpack_swap=True, game_root=game_root)
    assert any("换包模式" in n for n in out_sw.notices)


def test_run_diff_missing_snapshot_raises(tmp_path: Path) -> None:
    """缺快照 → FileNotFoundError(消息含版本名),CLI 层保持既有退出码 2 文案。"""
    from migration.pipeline import run_diff
    with pytest.raises(FileNotFoundError, match="b"):
        run_diff(tmp_path, src="a", dst="b", game_root=tmp_path)
```

（测试目录搭建具体化：`game_root/versions/a/mods/x-1.0.0.jar`、`game_root/versions/b/mods/x-1.1.0.jar`，各写一个空文件占位 jar；用 `pipeline.scan_version(game_root, "a", data/"snapshots")` 落盘快照。）

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_pipeline.py -v -k run_diff`
Expected: FAIL（`ImportError: run_diff`）

- [ ] **Step 3: 实现下沉**

1. `pipeline.py`：新增 `DiffOutcome` 与 `run_diff`——把 `cli._cmd_diff` 中「快照定位（find_snapshot 锚定+旧布局回退，legacy 命中提示入 notices）→ load → game_root 可达时 resolve_diff_context + 孤儿规则（不可达提示入 notices）→ build_ruleset → Differ(modpack_swap) → `compute_mod_pairs` → `diff_identity_notices`（非 None 入 notices；ctx.same_dir 且 None 时 log.debug）→ swap 排除计数提示入 notices」整体搬移，逻辑不改。`Differ` 需要的 `content_reader=ctx.read_file` 同现状透传。
2. `cli.py` `_cmd_diff` 瘦身为：解析参数 → `game_root = _try_resolve_game_root(args)` → try `run_diff(...)` except FileNotFoundError/ValueError → 既有错误文案 + return 2 → notices 逐行 `_print_err` → reporter/render/json（`DiffReporter(report, ..., mod_pairs=pairs)`）。**stdout/stderr 输出与现状逐字节一致**（提示行文本原样搬运）。
3. `reporter.py` `_display_note` 的 mods 分支改 parts 化（为 T5 预留 `self._client_only: set[str]`，本任务先置空集）：

```python
        if bucket == "mods":
            kind = self._pair_by_path.get(item.path)
            parts = [f"⇄{kind}"] if kind else []
            if item.path in self._client_only:
                parts.append("client_only")
            if parts:
                note = f"{note} {' '.join(parts)}"
            if kind == "rebuilt" or item.note == "rebuilt":
                note = f"⚠ {note}"
```

never/modpack_swap 镜像分支原样保留（输出不变）。
4. `build_plan` 的 `src_only_mods` 统计改：`dst_paths = {d.path for d in dst_snap.files}`，`sum(1 for p in src_snap.files if is_mod_jar(p.path) and p.path not in dst_paths)`（P6/P8 收口：判定同源 + O(n)）。
5. **P7 收口——CRLF 规范化哈希单点**：`migration/fsops.py` 增公有函数

```python
def sha256_normalized(path: Path) -> str:
    """计算文件 SHA-256(hex 小写;CRLF→LF 归一化后哈希,F24)。

    gen_manifest(清单生成)与 doctor(清单校验)共用同一实现,
    避免 F24 归一化逻辑两处漂移;LF 文件为 no-op。
    """
```

`tools/gen_manifest.py` 的 `sha256_file` 与 `migration/doctor.py` 的 `_sha256_of` 改为委托调用（docstring 注明单点来源）。行为零变化——`tests/test_doctor.py` 三个 F24 测试即哨兵。

- [ ] **Step 4: 全量字节锚定回归**

Run: `.venv/Scripts/python -m pytest tests/ -q && .venv/Scripts/python -m ruff check migration/`
Expected: 全绿（436 + 本任务 2 = 438；test_cli 全套即输出字节哨兵，不允许任何断言改动）

- [ ] **Step 5: 提交**

```bash
git add migration/pipeline.py migration/cli.py migration/reporter.py tests/test_pipeline.py
git commit -m "refactor(pipeline): diff 编排下沉 run_diff — 配对单点/notes 结构化/reporter 注记一等化"
```

---

### Task 5: client_only 清单 + 标注 + 警示（F30①）

**Files:**
- Create: `migration/data/client_mods.yaml`
- Modify: `migration/moddb.py`（`load_client_mods`）
- Modify: `migration/pipeline.py`（`run_diff` 匹配 + notice；`match_client_only_paths` 纯函数）
- Modify: `migration/reporter.py`（`DiffReporter` 增 `client_only_paths` 参数）
- Modify: `migration/data/manifest.sha256`（重跑 gen_manifest）
- Test: `tests/test_moddb.py`（loader）、`tests/test_corpus_regression.py`（r11 夹具复放断言）

**Interfaces:**
- Consumes: `run_diff`（T4）；`DiffReporter` 注记通道（T4 的 `_client_only`）；`normalize_jar_family`（家族键）
- Produces: `moddb.load_client_mods() -> tuple[set[str], set[str]]`（(modid 集, 家族键集)，data 缺失/损坏返回空集不炸）；`pipeline.match_client_only_paths(report, ctx, client_modids, client_families) -> set[str]`（registry 可达按 modid 反查 jar 文件名 + 全量按家族键；∩ report.mods 路径）

- [ ] **Step 1: 写失败测试**

`tests/test_moddb.py` 追加：

```python
def test_load_client_mods_entries() -> None:
    """清单加载:返回 (modid 集, 家族键集);打包数据可读。"""
    from migration.moddb import load_client_mods
    modids, families = load_client_mods()
    assert "glacier_dragon" in modids
    assert "frost-dragon" in families
```

`tests/test_corpus_regression.py` 追加（复放模式：无活体目录，r11 夹具）：

```python
def test_corpus_20260929_frost_dragon_client_only_annotation(tmp_path) -> None:
    """F30①:复放 diff(无活体)下已知客户端件按家族键命中 —
    frost_dragon 行标 client_only + stderr 警示行;其余行不变;--json 不受影响。"""
    import io, json
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
```

> 注：`out.client_only_paths` 字段由本任务在 `DiffOutcome` 上增配（`client_only_paths: set[str] = field(default_factory=set)`）；夹具快照经 `save()` 重落盘后含 `resolved_root` 键（值为夹具目录 resolve 结果）且两侧不同 → 不触发自比对提示，不影响断言。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/ -q -k "client_only"`
Expected: FAIL（`ImportError: load_client_mods`；清单文件不存在）

- [ ] **Step 3: 实现**

1. `migration/data/client_mods.yaml`（UTF-8、LF 行尾）：

```yaml
# 已知客户端 mod 清单(F30):专服部署时构造期崩溃风险件。
# 仅标注警示、不自动排除(工具是对比器不是部署器);匹配键二选一即可:
#   modid  = 活体 mods.toml 的 modId(registry 可达时命中)
#   family = 文件名家族键(normalize_jar_family,复放模式命中)
client_only:
  - modid: glacier_dragon
    family: frost-dragon
    reason: r11 专服构造期加载 LocalPlayer 于 DEDICATED_SERVER 崩溃(F30 实证,已隔离)
```

2. `moddb.py`（`load_mod_config_map` 之后）：

```python
def load_client_mods() -> tuple[set[str], set[str]]:
    """加载已知客户端 mod 清单(data/client_mods.yaml)。

    Returns:
        (modid 集合, 家族键集合);文件缺失/格式异常返回空集(不阻断 diff)。
    """
    try:
        text = resources.files("migration").joinpath("data/client_mods.yaml").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return set(), set()
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return set(), set()
    entries = doc.get("client_only") if isinstance(doc, dict) else None
    modids: set[str] = set()
    families: set[str] = set()
    for e in entries or []:
        if isinstance(e, dict):
            if e.get("modid"):
                modids.add(str(e["modid"]))
            if e.get("family"):
                families.add(str(e["family"]))
    return modids, families
```

3. `pipeline.py`：

```python
def match_client_only_paths(
    report: DiffReport, ctx: DiffContext | None,
    client_modids: set[str], client_families: set[str],
) -> set[str]:
    """mods 桶内命中客户端清单的路径集合(registry modid 反查 + 家族键,双通道)。"""
    if not client_modids and not client_families:
        return set()
    paths = {i.path for i in report.mods}
    hit: set[str] = set()
    if client_families:
        for p in paths:
            fam = normalize_jar_family(p)[0]
            if fam and fam in client_families:
                hit.add(p)
    if client_modids and ctx is not None:
        for mods in (ctx.src_mods, ctx.dst_mods):
            for mid in mods.modids:
                if mid in client_modids:
                    hit.add(f"mods/{mods.get(mid).jar_filename}")
    return hit & paths
```

`run_diff` 内：`modids, families = load_client_mods()`（延迟导入）→ `client = match_client_only_paths(report, ctx, modids, families)` → 非空时 notices 追加：

```python
            notices.append(
                f"[警示] {len(client)} 件已知客户端 mod(专服启动部署风险,F30): "
                + ", ".join(sorted(client))
            )
```

`DiffOutcome` 增 `client_only_paths: set[str] = field(default_factory=set)` 并赋值。
4. `reporter.py` `DiffReporter.__init__` 增参数 `client_only_paths: set[str] | None = None` → `self._client_only = client_only_paths or set()`（T4 已预留消费点）。
5. **重跑清单**：`.venv/Scripts/python tools/gen_manifest.py` → `git diff migration/data/manifest.sha256` 应只新增 `client_mods.yaml` 一行。

- [ ] **Step 4: 全量回归 + doctor 锚定**

Run: `.venv/Scripts/python -m pytest tests/ -q && .venv/Scripts/python -m ruff check migration/`
Expected: 全绿（438 + 本任务 2 = 440；tests/test_doctor.py 的 manifest 锚定随新清单通过）

- [ ] **Step 5: 提交**

```bash
git add migration/data/client_mods.yaml migration/data/manifest.sha256 migration/moddb.py migration/pipeline.py migration/reporter.py tests/
git commit -m "feat(mods): F30① client_only 清单标注 — 家族键/注册表双通道匹配+stderr 警示(仅标注)"
```

---

### Task 6: plan 命令 ⇄ 配对注记

**Files:**
- Modify: `migration/pipeline.py`（`build_plan` 返回三元组 + 接入 `compute_mod_pairs`）
- Modify: `migration/cli.py`（`_cmd_plan` 解包）
- Modify: `migration/gui/server.py:263`（解包）
- Modify: `migration/reporter.py`（`PlanReporter` 增 `mod_pairs` 参数 + COPY 行路径装饰）
- Modify: `tests/test_pipeline.py:96,114,163,178,187`（5 处解包改三元组）
- Test: `tests/test_e2e.py`（plan 渲染 ⇄ 断言 + plan.json 键集合）

**Interfaces:**
- Consumes: `compute_mod_pairs`（T4）；`PlanReporter` 既有结构
- Produces: `build_plan(...) -> tuple[MigrationPlan, list[CompatWarning], list[ModPair]]`（**返回形状变更**，全部调用方本任务内更新）；`PlanReporter(plan, *, src_version, dst_version, mod_pairs: list[ModPair] | None = None)`；plan.json 持久化内容**不含** mod_pairs（schema 零变化）

- [ ] **Step 1: 写失败测试**

`tests/test_e2e.py` 追加（沿用该文件既有 tmp game_root e2e 搭法：src 版本放 `mods/x-1.0.0.jar`、dst 版本放 `mods/x-1.1.0.jar` + 两份快照）：

```python
def test_e2e_plan_render_pairs_annotation(tmp_path) -> None:
    """plan ⇄:MOD_ADDED 行路径标 ⇄upgrade;plan.json 不持久化配对(消费方零影响)。"""
    plan, warns, pairs = build_plan(...)          # 既有 e2e 搭建参数
    assert any(p.kind == "upgrade" for p in pairs)
    reporter = PlanReporter(plan, src_version=..., dst_version=..., mod_pairs=pairs)
    buf = io.StringIO()
    reporter.render(PlanOptions(), console=Console(file=buf, width=200))
    assert "⇄upgrade" in buf.getvalue()
    payload = json.loads(reporter.to_json(warns))
    assert "mod_pairs" not in payload            # 持久化结构不变
    # 未传 mod_pairs 时渲染与旧版一致(默认参数向后兼容)
    plain = PlanReporter(plan, src_version=..., dst_version=...).render  # 零异常零标记
```

（`from migration.plan import PlanOptions`、`build_plan` 的具体搭建参数照抄邻接 e2e 测试——实现者按既有用例补全实参，不许改其他用例。）

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_e2e.py -v -k plan_render_pairs`
Expected: FAIL（`ValueError: too many values to unpack`——build_plan 仍返回二元组）

- [ ] **Step 3: 实现**

1. `pipeline.build_plan`：
   - 把 `src_mods = scan_mods(src_dir)` 从函数后段（compat 检查处）上移到 `dst_mods` 扫描旁（各扫一次，不重复 IO）；Differ 之后增 `pairs = compute_mod_pairs(src_snap, dst_snap, src_mods, dst_mods, same_dir=src_dir.resolve() == dst_dir.resolve())`（T4 已定型该签名；`same_dir=True` 时只走 filename 源，与 run_diff 传 `ctx.same_dir` 行为一致）。
   - 返回 `(plan, compat_warnings, pairs)`。
2. `cli._cmd_plan`：`plan, compat_warnings, pairs = build_plan(...)`；`PlanReporter(..., mod_pairs=pairs)`；JSON 分支 `to_json(compat_warnings)` 不变。
3. `gui/server.py:263`：`plan, compat_warnings, _pairs = build_plan(...)`（GUI 暂不渲染注记,行为不变）。
4. `reporter.PlanReporter`：`__init__` 增 `mod_pairs: list[ModPair] | None = None` → `self._pair_by_path` 同 DiffReporter 构建；render 行装配处：

```python
            for r in items:
                kind = self._pair_by_path.get(r.path)
                path_cell = f"{r.path} ⇄{kind}" if kind else r.path
                row = [path_cell, r.confidence, r.reason]
```

rebuilt 的 `⚠ ` 前缀镜像 diff 语义（`kind == "rebuilt"` 时 `path_cell = f"⚠ {path_cell}"`）。to_json **不改动**。
5. `tests/test_pipeline.py` 5 处 `plan, warns = build_plan(` / `plan, _ = build_plan(` 改三元组解包；`tests/test_gui*.py` 如有直调同步更新（grep `build_plan(` 兜底确认零遗漏）。

- [ ] **Step 4: 全量回归**

Run: `.venv/Scripts/python -m pytest tests/ -q && .venv/Scripts/python -m ruff check migration/`
Expected: 全绿（440 + 本任务 1 = 441；既有 plan 渲染断言若因新增 ⇄ 变化,仅限含配对 jar 的行,随改并在提交信息注明）

- [ ] **Step 5: 提交**

```bash
git add migration/pipeline.py migration/cli.py migration/gui/server.py migration/reporter.py tests/
git commit -m "feat(plan): plan 报告 ⇄ 配对注记 — build_plan 三元组返回,渲染级注记不进 plan.json"
```

---

### Task 7: 版本 0.9.0 + dev 依赖组 + 文档收口

**Files:**
- Modify: `pyproject.toml`（version 0.8.0→0.9.0；增 `[project.optional-dependencies]`）
- Modify: `migration/__init__.py`（`__version__ = "0.9.0"`）
- Modify: `README.zh-CN.md`、`README.en.md`

**Interfaces:**
- Consumes: 前六任务全部落地后的最终行为
- Produces: 无代码接口

- [ ] **Step 1: pyproject**

```toml
version = "0.9.0"

[project.optional-dependencies]
# 开发/测试依赖组:r11 教训 —— `uv sync` 精确模式只装运行时依赖会剪掉 pytest,
# 测试环境请用 `uv pip install -e ".[dev]"`(或 pip 等价命令)
dev = ["pytest>=8.0", "ruff==0.15.20"]
```

- [ ] **Step 2: `migration/__init__.py`** `__version__ = "0.9.0"`

- [ ] **Step 3: README 双语同步**

`README.zh-CN.md`：
- 配对段：四级→**五级键格**表述（表驱动、装饰词闭集、新增形态=加格条目）
- 新增「已知客户端 mod 清单」小节：`data/client_mods.yaml` 用途、仅标注不拦截、扩清单方式
- plan 段：⇄ 注记说明（渲染级,plan.json 不含）
- 快照段：`resolved_root` 字段说明（可选、旧快照兼容）
- 开发段：`uv pip install -e ".[dev]"`（uv sync 剪 pytest 坑）

`README.en.md` 同步以上五点 + 末尾 `Last synced: v0.9.0`（zh 历史无此标记,不加）。

- [ ] **Step 4: 终验**

Run: `.venv/Scripts/python -m pytest tests/ -q && .venv/Scripts/python -m ruff check . && .venv/Scripts/python -m pytest tests/test_doctor.py -q && .venv/Scripts/python -c "from migration import __version__; assert __version__ == '0.9.0', __version__"`
Expected: 441 全绿；ruff clean；doctor manifest 锚定通过；版本断言无输出（成功）

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml migration/__init__.py README.zh-CN.md README.en.md
git commit -m "chore(release): 0.9.0 — dev 依赖组/README 键格五级+client_only+resolved_root 同步"
```

---

## 任务依赖与执行顺序

T1 → T2 →（T3 可与 T2 并行）→ T4（依赖 T2+T3）→ T5、T6（依赖 T4,可并行）→ T7（收口）。串行执行按编号即可。

## 完成定义（对照 spec §5）

1. 441 项测试全绿 + ruff clean
2. 六套黄金对夹具断言零改动通过（键格等价）
3. r1-r11 语料六桶数字全部不变
4. diff 渲染字节级不变（client_only 命中行除外）；`--json` 结构不变
5. 五级合成形态 + 反例 + 歧义守卫钉死
6. 旧快照兼容 + 自比对主判双模式
7. client_only 复放命中 + 无命中零输出变化
8. plan ⇄ 渲染 + plan.json 无 mod_pairs
9. `mcmig -V` → 0.9.0；manifest 含 client_mods.yaml；`.[dev]` 可安装
