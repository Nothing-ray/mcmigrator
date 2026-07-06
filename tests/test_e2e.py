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
