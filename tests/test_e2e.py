"""端到端验收:scan 完整源 → 空壳目标 → diff 验证 6 桶(模拟 227→229 真实场景)。"""

import io
import json
import shutil
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from migration import cli


def _run(argv: list[str], buf: io.StringIO | None = None) -> int:
    if buf is not None:
        with redirect_stdout(buf):
            return cli.main(argv)
    return cli.main(argv)


def _write_mod_jar(path: Path, modid: str) -> None:
    """写一个含 META-INF/neoforge.mods.toml 的有效 jar(zip)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    toml = (
        f'modLoader="javafml"\nloaderVersion="[1,)"\n'
        f'[[mods]]\nmodId="{modid}"\nversion="1.0"\n'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", toml)


def _setup_full_and_empty_target(mini_version: Path, tmp_path: Path) -> Path:
    """源=完整玩家状态(mini);目标=空壳(模拟全新版本文件夹)。返回 game_root。"""
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version), str(versions / "mini"))
    (versions / "target").mkdir()  # 空目标
    return game_root


def _scan_both(game_root: Path) -> None:
    assert _run(["scan", "mini", "--game-root", str(game_root)]) == 0
    assert _run(["scan", "target", "--game-root", str(game_root)]) == 0


def test_e2e_full_source_to_empty_target_buckets(mini_version: Path, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    game_root = _setup_full_and_empty_target(mini_version, tmp_path)
    _scan_both(game_root)

    buf = io.StringIO()
    rc = _run(["diff", "mini", "target", "--game-root", str(game_root), "--json"], buf)
    assert rc == 0
    doc = json.loads(buf.getvalue())

    migrate = {i["path"] for i in doc["buckets"]["to_migrate"]}
    candidate = {i["path"] for i in doc["buckets"]["candidate"]}
    mods = {i["path"]: i["note"] for i in doc["buckets"]["mods"]}
    never = {i["path"] for i in doc["buckets"]["never"]}

    # 必迁类(must_migrate):目标缺失 → to_migrate
    assert "options.txt" in migrate
    assert "servers.dat" in migrate
    # 未知类(unknown):目标缺失 → candidate
    assert "config/create.toml" in candidate
    # mods 按文件名集合:目标无 create.jar → to_add
    assert mods.get("mods/create.jar") == "to_add"
    # 不迁类:源里的 logs → never
    assert "logs/latest.log" in never


def test_e2e_rule_change_without_rescan(mini_version: Path, tmp_path: Path, monkeypatch):
    """改规则后直接 diff(不重扫)结果即时反映——验收标准 3。"""
    monkeypatch.chdir(tmp_path)
    game_root = _setup_full_and_empty_target(mini_version, tmp_path)
    _scan_both(game_root)  # 只扫一次

    # 基线:options.txt 在 to_migrate
    buf1 = io.StringIO()
    _run(["diff", "mini", "target", "--game-root", str(game_root), "--json"], buf1)
    d1 = json.loads(buf1.getvalue())
    assert "options.txt" in {i["path"] for i in d1["buckets"]["to_migrate"]}

    # 加 --exclude options.txt(不重扫)→ options.txt 离开 to_migrate、进入 never
    buf2 = io.StringIO()
    _run(
        [
            "diff",
            "mini",
            "target",
            "--game-root",
            str(game_root),
            "--json",
            "--exclude",
            "options.txt",
        ],
        buf2,
    )
    d2 = json.loads(buf2.getvalue())
    assert "options.txt" not in {i["path"] for i in d2["buckets"]["to_migrate"]}
    assert "options.txt" in {i["path"] for i in d2["buckets"]["never"]}

    # 再次不带 exclude → 恢复(证明快照未被修改,分类是现算的)
    buf3 = io.StringIO()
    _run(["diff", "mini", "target", "--game-root", str(game_root), "--json"], buf3)
    d3 = json.loads(buf3.getvalue())
    assert "options.txt" in {i["path"] for i in d3["buckets"]["to_migrate"]}


def test_e2e_plan_bak_judgment(mini_version_with_bak: Path, tmp_path: Path, monkeypatch):
    """.bak 命中 → config candidate 升级为 copy_new。"""
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version_with_bak), str(versions / "mini"))
    (versions / "target").mkdir()
    # dst 装有 create mod → create.toml 不是孤儿,.bak 判定可生效
    _write_mod_jar(versions / "target" / "mods" / "create.jar", "create")
    monkeypatch.chdir(tmp_path)
    _run(["scan", "mini", "--game-root", str(game_root)])
    _run(["scan", "target", "--game-root", str(game_root)])

    buf = io.StringIO()
    _run(["plan", "mini", "target", "--json", "--game-root", str(game_root)], buf)
    doc = json.loads(buf.getvalue())
    actions = {a["path"]: a["behavior"] for a in doc["actions"]}
    assert actions.get("config/create.toml") == "copy"


def test_e2e_plan_whitelist_upgrades_to_migrate(mini_version_with_whitelist: Path, tmp_path: Path, monkeypatch):
    """白名单命中的文件在规则层归 must_migrate → copy_new(不进 candidate)。"""
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version_with_whitelist), str(versions / "mini"))
    (versions / "target").mkdir()
    # dst 装有 create+jade mod → 对应 config 不是孤儿,白名单可生效
    _write_mod_jar(versions / "target" / "mods" / "create.jar", "create")
    _write_mod_jar(versions / "target" / "mods" / "jade.jar", "jade")
    monkeypatch.chdir(tmp_path)
    _run(["scan", "mini", "--game-root", str(game_root)])
    _run(["scan", "target", "--game-root", str(game_root)])

    buf = io.StringIO()
    _run(["plan", "mini", "target", "--json", "--game-root", str(game_root)], buf)
    doc = json.loads(buf.getvalue())
    actions = {a["path"]: a["behavior"] for a in doc["actions"]}
    assert actions.get("iris.properties") == "copy"
    assert actions.get("config/jade/preset.json") == "copy"


def test_e2e_plan_no_write_to_game_dir(mini_version: Path, tmp_path: Path, monkeypatch):
    """plan 命令对游戏目录零写入(验收标准 3)。"""
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version), str(versions / "mini"))
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    _run(["scan", "mini", "--game-root", str(game_root)])
    _run(["scan", "target", "--game-root", str(game_root)])

    before = {p: p.stat().st_mtime_ns for p in game_root.rglob("*") if p.is_file()}
    _run(["plan", "mini", "target", "--game-root", str(game_root)])
    after = {p: p.stat().st_mtime_ns for p in game_root.rglob("*") if p.is_file()}
    assert before == after


def test_e2e_plan_default_config_skipped(tmp_path: Path, monkeypatch):
    """config 下无 .bak 且不在白名单 → skip_default_config。"""
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    mini = versions / "mini"
    mini.mkdir(parents=True)
    (mini / "config").mkdir()
    (mini / "config" / "default.toml").write_text("a=1\n", encoding="utf-8")
    (mini / "options.txt").write_text("v\n", encoding="utf-8")
    (versions / "target").mkdir()
    # dst 装有 default mod → default.toml 不是孤儿,走 default_config 路径
    _write_mod_jar(versions / "target" / "mods" / "default.jar", "default")
    monkeypatch.chdir(tmp_path)
    _run(["scan", "mini", "--game-root", str(game_root)])
    _run(["scan", "target", "--game-root", str(game_root)])

    buf = io.StringIO()
    _run(["plan", "mini", "target", "--json", "--game-root", str(game_root)], buf)
    doc = json.loads(buf.getvalue())
    origins = {a["path"]: a["origin"] for a in doc["actions"]}
    assert origins.get("config/default.toml") == "default_config"


def test_e2e_acceptance_plan_format_and_origins(tmp_path: Path, monkeypatch, capsys):
    """spec 验收标准整合:plan_format=2;.bak→bak_file;rebuild→rebuild;白名单→must_migrate;
    scan/diff 零回归(snapshot 可读)。"""
    import json
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    mini = versions / "mini"
    mini.mkdir(parents=True)
    (mini / "config").mkdir(parents=True)
    # 玩家改过的 config + 其 versioned .bak
    (mini / "config" / "create.toml").write_text("a=1\n", encoding="utf-8")
    (mini / "config" / "create-1.toml.bak").write_bytes(b"\x00")
    # 高危 rebuild 文件
    (mini / "config" / "fml.toml").write_text("x=1\n", encoding="utf-8")
    # 白名单文件(无 .bak 玩家偏好)
    (mini / "config" / "sodium-options.json").write_text("{}", encoding="utf-8")
    # 必迁
    (mini / "options.txt").write_text("v\n", encoding="utf-8")
    (versions / "target").mkdir()
    # dst 装有 create+sodium mod → 对应 config 不是孤儿,规则层可正确判定
    _write_mod_jar(versions / "target" / "mods" / "create.jar", "create")
    _write_mod_jar(versions / "target" / "mods" / "sodium.jar", "sodium")
    monkeypatch.chdir(tmp_path)

    # scan/diff 零回归:先 scan 再 diff 不报错
    assert _run(["scan", "mini", "--game-root", str(game_root)]) == 0
    assert _run(["scan", "target", "--game-root", str(game_root)]) == 0
    buf = io.StringIO()
    assert _run(["diff", "mini", "target", "--game-root", str(game_root), "--json"], buf) == 0
    json.loads(buf.getvalue())  # 可解析

    # plan
    capsys.readouterr()
    assert _run(["plan", "mini", "target", "--json", "--game-root", str(game_root)]) == 0
    doc = json.loads(capsys.readouterr().out)
    # 验收 2:plan_format=2
    assert doc["plan_format"] == 2
    origins = {a["path"]: a["origin"] for a in doc["actions"]}
    behaviors = {a["path"]: a["behavior"] for a in doc["actions"]}
    # 验收 1:.bak → bak_file(非 default_config)
    assert origins.get("config/create-1.toml.bak") == "bak_file"
    assert behaviors.get("config/create-1.toml.bak") == "copy"
    # 验收 1:高危文件 → rebuild
    assert origins.get("config/fml.toml") == "rebuild"
    assert behaviors.get("config/fml.toml") == "skip"
    # 验收 1:白名单 → must_migrate
    assert origins.get("config/sodium-options.json") == "must_migrate"


def test_e2e_scan_zero_regression_snapshot_format_unchanged(tmp_path: Path, monkeypatch):
    """验收 3:SNAPSHOT_FORMAT 不动,scan 产物可读。"""
    from migration.snapshot import SNAPSHOT_FORMAT, Snapshot, snapshot_path

    assert SNAPSHOT_FORMAT == 1  # 未改动
    game_root = tmp_path / "game"
    (game_root / "versions" / "mini").mkdir(parents=True)
    (game_root / "versions" / "mini" / "options.txt").write_text("v\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _run(["scan", "mini", "--game-root", str(game_root)]) == 0
    snap = Snapshot.load(snapshot_path(tmp_path, "mini"))  # 旧 snapshot 仍可读
    assert snap.file_count >= 1


def test_plan_with_orphan_rules_applied(tmp_path, monkeypatch):
    """plan 命令应用 orphan 规则:dst 无 jade mod → jade config 标为 orphan。"""
    import zipfile

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    src_dir.mkdir(parents=True)
    dst_dir.mkdir(parents=True)

    # src 有 jade config 但 dst 无 jade mod
    (src_dir / "config").mkdir()
    (src_dir / "config" / "jade").mkdir()
    (src_dir / "config" / "jade" / "presets.json").write_text("{}", encoding="utf-8")
    (src_dir / "options.txt").write_text("fps:120", encoding="utf-8")

    # dst 有 create mod(无 jade)
    dst_mods = dst_dir / "mods"
    dst_mods.mkdir()
    toml = 'modLoader="javafml"\nloaderVersion="[1,)"\n[[mods]]\nmodId="create"\nversion="1.0"\n'
    with zipfile.ZipFile(dst_mods / "create.jar", "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", toml)

    # src 也有 create mod
    src_mods = src_dir / "mods"
    src_mods.mkdir()
    with zipfile.ZipFile(src_mods / "create.jar", "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", toml)

    # 配置 + scan + plan
    mcmig_dir = tmp_path / ".mcmig"
    mcmig_dir.mkdir()
    (mcmig_dir / "config.yaml").write_text(f"game_root: '{game_root}'", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    assert _run(["scan", "src", "--game-root", str(game_root)]) == 0
    assert _run(["scan", "dst", "--game-root", str(game_root)]) == 0

    buf = io.StringIO()
    assert _run(["plan", "src", "dst", "--game-root", str(game_root), "--json"], buf) == 0
    doc = json.loads(buf.getvalue())
    origins = {a["path"]: a["origin"] for a in doc["actions"]}
    behaviors = {a["path"]: a["behavior"] for a in doc["actions"]}
    # dst 无 jade mod → jade config 被标为 orphan(skip)
    assert origins.get("config/jade/presets.json") == "orphan"
    assert behaviors.get("config/jade/presets.json") == "skip"


def test_plan_full_mod_awareness_e2e(tmp_path, monkeypatch):
    """端到端:orphan config 标记 + .bak MD5 降级 + 兼容检查。"""
    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    src_dir.mkdir(parents=True)
    dst_dir.mkdir(parents=True)

    # dst 有 create mod + royal mod(royal 须在 dst,否则 royal.toml 被判孤儿,无法测 .bak 降级)
    dst_mods = dst_dir / "mods"
    dst_mods.mkdir()
    create_toml = (
        'modLoader="javafml"\nloaderVersion="[1,)"\n'
        '[[mods]]\nmodId="create"\nversion="6.0.10"\n'
        '[[dependencies.create]]\nmodId="neoforge"\n'
        'type="required"\nversionRange="[21.1.0,)"\n'
    )
    with zipfile.ZipFile(dst_mods / "create.jar", "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", create_toml)
    royal_toml = (
        'modLoader="javafml"\nloaderVersion="[1,)"\n'
        '[[mods]]\nmodId="royal"\nversion="1.0"\n'
    )
    with zipfile.ZipFile(dst_mods / "royal.jar", "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", royal_toml)

    # src 有 create mod + jade config(孤儿) + royal config(.bak MD5 相同)
    src_mods = src_dir / "mods"
    src_mods.mkdir()
    with zipfile.ZipFile(src_mods / "create.jar", "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", create_toml)

    src_config = src_dir / "config"
    src_config.mkdir()
    # 孤儿 config(jade 不在 dst mods)
    (src_config / "jade").mkdir()
    (src_config / "jade" / "presets.json").write_text("{}", encoding="utf-8")
    # .bak MD5 相同(自动生成 → 降级)
    (src_config / "royal.toml").write_text("default=true\n", encoding="utf-8")
    (src_config / "royal-1.toml.bak").write_text("default=true\n", encoding="utf-8")
    # .bak MD5 不同(玩家改过 → 保持 config_modified)
    # .bak 命名须为 <stem>-<N>.<suffix>.bak:create-client-1.toml.bak(非 create-1.toml.bak)
    (src_config / "create-client.toml").write_text("edited=true\n", encoding="utf-8")
    (src_config / "create-client-1.toml.bak").write_text("edited=false\n", encoding="utf-8")

    # options.txt
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (dst_dir / "options.txt").write_text("fps:60\n", encoding="utf-8")

    mcmig_dir = tmp_path / ".mcmig"
    mcmig_dir.mkdir()
    (mcmig_dir / "config.yaml").write_text(f"game_root: '{game_root}'", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    assert _run(["scan", "src", "--game-root", str(game_root)]) == 0
    assert _run(["scan", "dst", "--game-root", str(game_root)]) == 0

    # 捕获 JSON plan 输出
    buf = io.StringIO()
    rc = _run(["plan", "src", "dst", "--game-root", str(game_root), "--json"], buf)
    output = buf.getvalue()
    assert rc == 0

    plan_data = json.loads(output)
    actions = {a["path"]: a for a in plan_data["actions"]}

    # 孤儿 config 标为 orphan
    jade = actions.get("config/jade/presets.json")
    assert jade is not None
    assert jade["origin"] == "orphan"
    assert jade["behavior"] == "skip"

    # .bak MD5 相同 → 降级 default_config
    royal = actions.get("config/royal.toml")
    assert royal is not None
    assert royal["origin"] == "default_config"
    assert royal["behavior"] == "skip"

    # .bak MD5 不同 → 保持 config_modified
    create = actions.get("config/create-client.toml")
    assert create is not None
    assert create["origin"] == "config_modified"
    assert create["behavior"] == "copy"


def test_plan_compat_warnings_e2e(tmp_path, monkeypatch):
    """端到端:不兼容 mod 的兼容警告在 --json 和 rich 输出中都出现(spec #5)。

    场景:dst 版本 json 声明 NeoForge 21.1.228,src 玩家额外加 cp_lib.jar
    声明 versionRange [21.1.233,) → cp_lib 不兼容 dst。
    """
    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    src_dir.mkdir(parents=True)
    dst_dir.mkdir(parents=True)

    # dst 版本 json:声明 NeoForge 21.1.228
    dst_json = {
        "arguments": {
            "game": ["--fml.neoforgeVersion", "21.1.228", "--fml.mcVersion", "1.21.1"],
        }
    }
    (dst_dir / "dst.json").write_text(json.dumps(dst_json), encoding="utf-8")

    # dst 有 create mod(无 cp_lib)
    dst_mods = dst_dir / "mods"
    dst_mods.mkdir()
    create_toml = (
        'modLoader="javafml"\nloaderVersion="[1,)"\n'
        '[[mods]]\nmodId="create"\nversion="6.0.10"\n'
        '[[dependencies.create]]\nmodId="neoforge"\n'
        'type="required"\nversionRange="[21.1.0,)"\n'
    )
    with zipfile.ZipFile(dst_mods / "create.jar", "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", create_toml)

    # src 有 create(兼容)+ cp_lib(不兼容,versionRange 要求 21.1.233+)
    src_mods = src_dir / "mods"
    src_mods.mkdir()
    with zipfile.ZipFile(src_mods / "create.jar", "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", create_toml)
    cp_lib_toml = (
        'modLoader="javafml"\nloaderVersion="[1,)"\n'
        '[[mods]]\nmodId="cp_lib"\nversion="5.0.18"\n'
        '[[dependencies.cp_lib]]\nmodId="neoforge"\n'
        'type="required"\nversionRange="[21.1.233,)"\n'
    )
    with zipfile.ZipFile(src_mods / "cp_lib.jar", "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", cp_lib_toml)

    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (dst_dir / "options.txt").write_text("fps:60\n", encoding="utf-8")

    mcmig_dir = tmp_path / ".mcmig"
    mcmig_dir.mkdir()
    (mcmig_dir / "config.yaml").write_text(f"game_root: '{game_root}'", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    assert _run(["scan", "src", "--game-root", str(game_root)]) == 0
    assert _run(["scan", "dst", "--game-root", str(game_root)]) == 0

    # Run A: --json 模式 → compat_warnings 字段含 cp_lib(I3 + I2)
    buf = io.StringIO()
    rc = _run(["plan", "src", "dst", "--game-root", str(game_root), "--json"], buf)
    assert rc == 0
    doc = json.loads(buf.getvalue())
    assert "compat_warnings" in doc, "JSON 输出应含 compat_warnings 字段"
    cw = doc["compat_warnings"]
    assert len(cw) == 1
    assert cw[0]["modid"] == "cp_lib"
    assert cw[0]["required_range"] == "[21.1.233,)"
    assert cw[0]["dst_neoforge"] == "21.1.228"

    # Run B: 非 JSON 模式 → rich 渲染含 cp_lib / 21.1.233(I2)
    buf2 = io.StringIO()
    rc2 = _run(["plan", "src", "dst", "--game-root", str(game_root)], buf2)
    assert rc2 == 0
    rendered = buf2.getvalue()
    assert "cp_lib" in rendered
    assert "21.1.233" in rendered
    assert "21.1.228" in rendered


def test_plan_modpack_swap_flag(tmp_path, monkeypatch, capsys):
    """--modpack-swap:src 独有 mod 不回迁(mod_swapped_out);无 flag 时提示。"""
    import json
    from migration.cli import main

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    for d in (src_dir, dst_dir):
        d.mkdir(parents=True)
        (d / "mods").mkdir()
    # src 有 1 个共有 + 21 个独有 jar(超过提示阈值 20)
    for i in range(21):
        (src_dir / "mods" / f"old-only-{i}.jar").write_bytes(b"j")
    (src_dir / "mods" / "shared.jar").write_bytes(b"s")
    (dst_dir / "mods" / "shared.jar").write_bytes(b"s")
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")

    mcmig_dir = tmp_path / ".mcmig"
    mcmig_dir.mkdir()
    (mcmig_dir / "config.yaml").write_text(f"game_root: '{game_root}'", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["scan", "src", "--game-root", str(game_root)]) == 0
    assert main(["scan", "dst", "--game-root", str(game_root)]) == 0
    capsys.readouterr()  # 排空 scan 输出

    # 无 flag:src 独有 mod 走 mod_added(回迁),且输出换包提示
    rc = main(["plan", "src", "dst", "--game-root", str(game_root), "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    acts = {a["path"]: a for a in doc["actions"]}
    assert acts["mods/old-only-0.jar"]["origin"] == "mod_added"
    assert "--modpack-swap" in captured.err  # 提示走 stderr,stdout 保持纯 JSON

    # 有 flag:src 独有 mod → mod_swapped_out(skip),共有不受影响
    rc = main(["plan", "src", "dst", "--game-root", str(game_root), "--json", "--modpack-swap"])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    acts = {a["path"]: a for a in doc["actions"]}
    assert acts["mods/old-only-0.jar"]["origin"] == "mod_swapped_out"
    assert acts["mods/old-only-0.jar"]["behavior"] == "skip"
    assert acts["mods/shared.jar"]["origin"] == "mod_shared"


def test_migrate_full_chain(tmp_path, monkeypatch, capsys):
    """scan→plan→migrate 全链路:复制/冲突备份/重入全 identical/执行状态回写。"""
    import json
    from migration.cli import main

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    for d in (src_dir, dst_dir):
        d.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (dst_dir / "options.txt").write_text("fps:60\n", encoding="utf-8")
    (src_dir / "saves").mkdir()
    (src_dir / "saves" / "w.dat").write_bytes(b"world")

    monkeypatch.chdir(tmp_path)
    assert main(["scan", "src", "--game-root", str(game_root)]) == 0
    assert main(["scan", "dst", "--game-root", str(game_root)]) == 0
    assert main(["plan", "src", "dst", "--game-root", str(game_root)]) == 0
    capsys.readouterr()

    # 执行(-y 跳过确认;确认输入用 stdin 隔离)
    assert main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"]) == 0
    out = capsys.readouterr().out
    assert (dst_dir / "options.txt").read_text(encoding="utf-8") == "fps:120\n"
    assert (dst_dir / "_conflict_backup" / "options.txt").read_text(encoding="utf-8") == "fps:60\n"
    assert (dst_dir / "saves" / "w.dat").exists()
    assert "PCL.ini" in out and "LaunchVersionSelect" in out  # PCL 提醒文案

    # plan 已回写执行状态
    plan_file = tmp_path / ".mcmig" / "plans" / "src__dst.plan.json"
    doc = json.loads(plan_file.read_text(encoding="utf-8"))
    assert doc.get("executed_at")

    # 重跑:不加 --force 应拒绝(码 2);提示已执行
    rc = main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"])
    assert rc == 2
    assert "已执行" in capsys.readouterr().out

    # --force 重跑:全部 identical,无新增备份
    assert main(["migrate", "src", "dst", "--game-root", str(game_root), "-y", "--force"]) == 0
    out = capsys.readouterr().out
    assert "identical" in out
    backups = list((dst_dir / "_conflict_backup").rglob("*"))
    assert len([b for b in backups if b.is_file()]) == 1


def test_migrate_missing_plan_exit_2(tmp_path, monkeypatch, capsys):
    from migration.cli import main

    game_root = tmp_path / "game"
    (game_root / "versions" / "src").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    rc = main(["migrate", "src", "dst", "--game-root", str(game_root)])
    assert rc == 2
    assert "plan" in capsys.readouterr().out


def _mk_jar(path: Path, modid: str, nf_range: str | None = None) -> None:
    """合成含 neoforge.mods.toml 的 jar。"""
    import zipfile
    deps = ""
    if nf_range:
        deps = (f'[[dependencies.{modid}]]\nmodId="neoforge"\ntype="required"\n'
                f'versionRange="{nf_range}"\n')
    toml = (f'modLoader="javafml"\nloaderVersion="[1,)"\n[[mods]]\n'
            f'modId="{modid}"\nversion="1.0"\n{deps}')
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", toml)


def test_swap_preflight_blocks_incompatible(tmp_path, monkeypatch, capsys):
    """新包 mod 要求 NF≥25x、目标 233 → 默认中止列出 mod;--force 后装包。"""
    from migration.cli import main

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    src_dir.mkdir(parents=True)
    dst_dir.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    # 目标版本 json:声明 NeoForge 21.1.233
    import json
    (dst_dir / "dst.json").write_text(json.dumps(
        {"arguments": {"game": ["--fml.neoforgeVersion", "21.1.233"]}}), encoding="utf-8")

    new_mods = tmp_path / "newpack" / "mods"
    _mk_jar(new_mods / "create.jar", "create", "[21.1.0,)")
    _mk_jar(new_mods / "cp_lib.jar", "cp_lib", "[21.1.248,)")

    monkeypatch.chdir(tmp_path)
    assert main(["scan", "src", "--game-root", str(game_root)]) == 0
    capsys.readouterr()

    # 默认中止(码 2),列出 cp_lib
    rc = main(["swap", "src", "dst", str(new_mods.parent), "--game-root", str(game_root)])
    assert rc == 2
    out = capsys.readouterr().out
    assert "cp_lib" in out and "21.1.248" in out
    assert not (dst_dir / "mods").exists()  # 中止时未装包
