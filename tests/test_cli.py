import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from migration import cli
from migration.snapshot import snapshot_path


def _build_version(root: Path, *, variant_b: bool = False) -> None:
    """在 root 下就地构建一个迷你版本(覆盖各分类)。"""
    root.mkdir(parents=True, exist_ok=True)
    # variant_b 改动 options.txt(MUST_MIGRATE),使 diff 的 to_migrate 桶非空
    (root / "options.txt").write_text(
        "version:I am a config\n" if not variant_b else "version:I am a config, tweaked\n",
        encoding="utf-8",
    )
    (root / "servers.dat").write_bytes(b"\x0a\x00\x00")
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / "latest.log").write_text("noise", encoding="utf-8")
    (root / "crash-reports").mkdir(exist_ok=True)
    (root / "crash-reports" / "c1.txt").write_text("boom", encoding="utf-8")
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "create.toml").write_text(
        "edited=true\n" if variant_b else "edited=false\n", encoding="utf-8"
    )
    (root / "mods").mkdir(exist_ok=True)
    (root / "mods" / "create.jar").write_bytes(b"")
    if variant_b:
        (root / "mods" / "extra.jar").write_bytes(b"")


def _setup_game(tmp_path: Path, names: list[str], variant_b_for: str | None = None) -> Path:
    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    for n in names:
        _build_version(versions / n, variant_b=(n == variant_b_for))
    return game_root


def test_scan_writes_snapshot(tmp_path: Path, monkeypatch):
    game_root = _setup_game(tmp_path, ["mini"])
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["scan", "mini", "--game-root", str(game_root)])
    assert rc == 0
    assert snapshot_path(tmp_path, "mini").exists()


def test_scan_missing_version_lists_available(tmp_path: Path, monkeypatch, capsys):
    game_root = _setup_game(tmp_path, ["real"])
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["scan", "ghost", "--game-root", str(game_root)])
    out = capsys.readouterr().out
    assert rc != 0
    assert "real" in out  # 列出可用版本


def test_diff_missing_snapshot_friendly_error(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["diff", "a", "b", "--game-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc != 0
    assert "scan" in out  # 提示先 scan


def test_diff_json_parseable(tmp_path: Path, monkeypatch):
    game_root = _setup_game(tmp_path, ["mini", "mini_b"], variant_b_for="mini_b")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["scan", "mini", "--game-root", str(game_root)]) == 0
    assert cli.main(["scan", "mini_b", "--game-root", str(game_root)]) == 0
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["diff", "mini_b", "mini", "--game-root", str(game_root), "--json"])
    assert rc == 0
    doc = json.loads(buf.getvalue())
    assert doc["src"] == "mini_b" and doc["dst"] == "mini"
    assert doc["summary"]["to_migrate"] >= 1


def test_scan_json_output(tmp_path: Path, monkeypatch):
    game_root = _setup_game(tmp_path, ["mini"])
    monkeypatch.chdir(tmp_path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["scan", "mini", "--game-root", str(game_root), "--json"])
    assert rc == 0
    doc = json.loads(buf.getvalue())
    assert doc["version"] == "mini"
    assert doc["file_count"] >= 1
    assert "by_category" in doc
