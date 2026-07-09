"""端到端验收:scan 完整源 → 空壳目标 → diff 验证 6 桶(模拟 227→229 真实场景)。"""

import io
import json
import shutil
from contextlib import redirect_stdout
from pathlib import Path

from migration import cli


def _run(argv: list[str], buf: io.StringIO | None = None) -> int:
    if buf is not None:
        with redirect_stdout(buf):
            return cli.main(argv)
    return cli.main(argv)


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
    monkeypatch.chdir(tmp_path)
    _run(["scan", "mini", "--game-root", str(game_root)])
    _run(["scan", "target", "--game-root", str(game_root)])

    buf = io.StringIO()
    _run(["plan", "mini", "target", "--json"], buf)
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
    monkeypatch.chdir(tmp_path)
    _run(["scan", "mini", "--game-root", str(game_root)])
    _run(["scan", "target", "--game-root", str(game_root)])

    buf = io.StringIO()
    _run(["plan", "mini", "target", "--json"], buf)
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
    _run(["plan", "mini", "target"])
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
    monkeypatch.chdir(tmp_path)
    _run(["scan", "mini", "--game-root", str(game_root)])
    _run(["scan", "target", "--game-root", str(game_root)])

    buf = io.StringIO()
    _run(["plan", "mini", "target", "--json"], buf)
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
    monkeypatch.chdir(tmp_path)

    # scan/diff 零回归:先 scan 再 diff 不报错
    assert _run(["scan", "mini", "--game-root", str(game_root)]) == 0
    assert _run(["scan", "target", "--game-root", str(game_root)]) == 0
    buf = io.StringIO()
    assert _run(["diff", "mini", "target", "--game-root", str(game_root), "--json"], buf) == 0
    json.loads(buf.getvalue())  # 可解析

    # plan
    capsys.readouterr()
    assert _run(["plan", "mini", "target", "--json"]) == 0
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
