import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

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


def test_resolve_game_root_flag_wins(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MCMIG_GAME_ROOT", "/from/env")  # 设 env 以证明 flag 压过它
    args = cli.build_parser().parse_args(["scan", "v", "--game-root", "/from/flag"])
    assert cli._resolve_game_root(args) == Path("/from/flag")


def test_resolve_game_root_env(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 无 config
    monkeypatch.setenv("MCMIG_GAME_ROOT", "/from/env")
    args = cli.build_parser().parse_args(["scan", "v"])  # 无 flag
    assert cli._resolve_game_root(args) == Path("/from/env")


def test_resolve_game_root_config(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MCMIG_GAME_ROOT", raising=False)
    (tmp_path / ".mcmig").mkdir()
    (tmp_path / ".mcmig" / "config.yaml").write_text("game_root: /from/config\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = cli.build_parser().parse_args(["scan", "v"])
    assert cli._resolve_game_root(args) == Path("/from/config")


def test_resolve_game_root_error(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("MCMIG_GAME_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)  # 无 config
    args = cli.build_parser().parse_args(["scan", "v"])
    with pytest.raises(SystemExit) as exc:
        cli._resolve_game_root(args)
    assert exc.value.code == 2
    msg = capsys.readouterr().out
    assert "--game-root" in msg and "MCMIG_GAME_ROOT" in msg and "config.yaml" in msg


def test_plan_writes_plan_file(mini_version: Path, tmp_path: Path, monkeypatch):
    import shutil
    from migration import cli
    from migration.plan import plan_path

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version), str(versions / "mini"))
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])

    code = cli.main(["plan", "mini", "target"])
    assert code == 0
    assert plan_path(tmp_path, "mini", "target").exists()


def test_plan_missing_snapshot_friendly_error(tmp_path: Path, monkeypatch, capsys):
    from migration import cli

    monkeypatch.chdir(tmp_path)
    code = cli.main(["plan", "a", "b"])
    out = capsys.readouterr().out
    assert code != 0
    assert "scan" in out


def test_plan_json_output(tmp_path: Path, mini_version: Path, monkeypatch, capsys):
    import json
    import shutil
    from migration import cli

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version), str(versions / "mini"))
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])
    capsys.readouterr()  # 清空 scan 输出,仅捕获 plan --json

    code = cli.main(["plan", "mini", "target", "--json"])
    out = capsys.readouterr().out
    assert code == 0
    doc = json.loads(out)
    assert doc["src"] == "mini" and doc["dst"] == "target"
    assert "summary" in doc and "actions" in doc


def test_plan_no_save_skips_file(tmp_path: Path, mini_version: Path, monkeypatch):
    import shutil
    from migration import cli
    from migration.plan import plan_path

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version), str(versions / "mini"))
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])

    cli.main(["plan", "mini", "target", "--no-save"])
    assert not plan_path(tmp_path, "mini", "target").exists()


def test_plan_show_skip_includes_skip_actions(tmp_path, mini_version, monkeypatch, capsys):
    import shutil
    from migration import cli

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    shutil.move(str(mini_version), str(versions / "mini"))
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])

    cli.main(["plan", "mini", "target", "--show-skip"])
    out = capsys.readouterr().out
    assert "默认配置" in out or "不迁" in out or "一致" in out


def test_safe_reconfigure_streams_prevents_gbk_emoji_crash():
    """GBK strict stdout 经 _safe_reconfigure_streams 后 rich emoji 不再 UnicodeEncodeError。

    回归测试:无此修复时 rich 输出 emoji 到 gbk 控制台必崩(✅📦 等不可编码)。
    """
    import io
    import sys

    from migration.cli import _safe_reconfigure_streams

    orig = (sys.stdout, sys.stderr)
    try:
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
        sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
        assert sys.stdout.errors == "strict"
        _safe_reconfigure_streams()
        assert sys.stdout.errors == "replace"
        from rich.console import Console

        Console().print("[bold]test ✅ 中文 📦 🔄 ⚙️[/]")  # 不应 raise
    finally:
        sys.stdout, sys.stderr = orig


def test_safe_reconfigure_streams_swallows_non_textiowrapper():
    """_safe_reconfigure_streams 对无 reconfigure 方法的流(如 StringIO)静默跳过不崩。"""
    import sys

    from migration.cli import _safe_reconfigure_streams

    orig = (sys.stdout, sys.stderr)
    try:
        sys.stdout = io.StringIO()  # StringIO 无 reconfigure 方法
        sys.stderr = io.StringIO()
        _safe_reconfigure_streams()  # 不应 raise
    finally:
        sys.stdout, sys.stderr = orig


def test_plan_rebuild_files_go_rebuild_origin(tmp_path: Path, monkeypatch, capsys):
    """fml.toml 等命中 rebuild.yaml → plan 中 origin=rebuild(不进 candidate)。"""
    import json
    from migration import cli

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    mini = versions / "mini"
    mini.mkdir()
    (mini / "config").mkdir()
    (mini / "config" / "fml.toml").write_text("x=1\n", encoding="utf-8")
    (mini / "options.txt").write_text("v\n", encoding="utf-8")
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])
    capsys.readouterr()
    cli.main(["plan", "mini", "target", "--json"])
    doc = json.loads(capsys.readouterr().out)
    origins = {a["path"]: a["origin"] for a in doc["actions"]}
    assert origins.get("config/fml.toml") == "rebuild"


def test_plan_whitelist_sodium_options_goes_must_migrate(tmp_path: Path, monkeypatch, capsys):
    """sodium-options.json 命中白名单 → must_migrate(不进 rebuild/default_config)。"""
    import json
    from migration import cli

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    mini = versions / "mini"
    mini.mkdir()
    (mini / "config").mkdir()
    (mini / "config" / "sodium-options.json").write_text("{}", encoding="utf-8")
    (mini / "options.txt").write_text("v\n", encoding="utf-8")
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])
    capsys.readouterr()
    cli.main(["plan", "mini", "target", "--json"])
    doc = json.loads(capsys.readouterr().out)
    origins = {a["path"]: a["origin"] for a in doc["actions"]}
    assert origins.get("config/sodium-options.json") == "must_migrate"


def test_plan_rebuild_yields_to_user_rules(tmp_path: Path, monkeypatch, capsys):
    """user rules.yaml 写 fml.toml→must_migrate 时压过 rebuild(P2 用户主权)。"""
    import json
    from migration import cli

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    mini = versions / "mini"
    mini.mkdir()
    (mini / "config").mkdir()
    (mini / "config" / "fml.toml").write_text("x=1\n", encoding="utf-8")
    (mini / "options.txt").write_text("v\n", encoding="utf-8")
    (versions / "target").mkdir()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcmig").mkdir()
    (tmp_path / ".mcmig" / "rules.yaml").write_text(
        "version: 1\nrules:\n  - match: 'config/fml.toml'\n    decide: must_migrate\n    reason: 'user override'\n",
        encoding="utf-8",
    )
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])
    capsys.readouterr()
    cli.main(["plan", "mini", "target", "--json"])
    doc = json.loads(capsys.readouterr().out)
    origins = {a["path"]: a["origin"] for a in doc["actions"]}
    assert origins.get("config/fml.toml") == "must_migrate"
