import io
import json
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from migration import cli
from migration.snapshot import snapshot_path


def _write_mod_jar(path: Path, modid: str) -> None:
    """写一个含 META-INF/neoforge.mods.toml 的有效 jar(zip)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    toml = (
        f'modLoader="javafml"\nloaderVersion="[1,)"\n'
        f'[[mods]]\nmodId="{modid}"\nversion="1.0"\n'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", toml)


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
        "edited=true\n" if variant_b else "edited=false\n",
        encoding="utf-8",
    )
    (root / "mods").mkdir(exist_ok=True)
    _write_mod_jar(root / "mods" / "create.jar", "create")
    if variant_b:
        _write_mod_jar(root / "mods" / "extra.jar", "extra")


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


def test_scan_unreadable_reporting_restored(tmp_path, monkeypatch, capsys):
    """回归(v0 spec §7「报告单列 unreadable」):不可读文件经 on_error 收集后,
    --json 输出含 unreadable 计数字段,非 JSON 模式含汇总警告行(与重构前一致)。"""
    from migration.pipeline import scan_version as real_scan_version

    game_root = _setup_game(tmp_path, ["mini"])
    monkeypatch.chdir(tmp_path)

    def fake_scan_version(game_root_, version, workdir_snapshots, *, strict=False, on_error=None):
        # 模拟 1 个不可读文件:真扫描照常执行,额外触发一次 on_error 上报
        snap = real_scan_version(game_root_, version, workdir_snapshots, strict=strict)
        if on_error is not None:
            on_error("saves/locked.dat")
        return snap

    monkeypatch.setattr(cli, "scan_version", fake_scan_version)

    # --json:unreadable 字段(与重构前同 shape)
    assert cli.main(["scan", "mini", "--game-root", str(game_root), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["unreadable"] == 1

    # 非 JSON:汇总警告行(位置/文案与重构前一致)
    assert cli.main(["scan", "mini", "--game-root", str(game_root)]) == 0
    assert "[警告] 1 个文件无法读取(已跳过)" in capsys.readouterr().out



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

    code = cli.main(["plan", "mini", "target", "--game-root", str(game_root)])
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

    code = cli.main(["plan", "mini", "target", "--json", "--game-root", str(game_root)])
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

    cli.main(["plan", "mini", "target", "--no-save", "--game-root", str(game_root)])
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

    cli.main(["plan", "mini", "target", "--show-skip", "--game-root", str(game_root)])
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
    cli.main(["plan", "mini", "target", "--json", "--game-root", str(game_root)])
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
    # dst 装有 sodium mod → sodium-options.json 不是孤儿,白名单可生效
    _write_mod_jar(versions / "target" / "mods" / "sodium.jar", "sodium")
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])
    capsys.readouterr()
    cli.main(["plan", "mini", "target", "--json", "--game-root", str(game_root)])
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
    cli.main(["plan", "mini", "target", "--json", "--game-root", str(game_root)])
    doc = json.loads(capsys.readouterr().out)
    origins = {a["path"]: a["origin"] for a in doc["actions"]}
    assert origins.get("config/fml.toml") == "must_migrate"


def test_gui_subcommand_registered():
    from migration.cli import build_parser

    p = build_parser()
    args = p.parse_args(["gui"])
    assert args.no_browser is False


def test_gui_manifest_tamper_exits_2(tmp_path, monkeypatch, capsys):
    """启动自检 ②:数据清单校验不通过 → 打印清单问题退 2,不起服务。

    patch 目标是 doctor 模块属性(cli 经 doctor 模块对象调用,查找发生在调用时)。
    """
    from migration import cli, doctor

    monkeypatch.setattr(
        doctor, "verify_data_manifest", lambda: ["rebuild.yaml 内容与清单不符"]
    )
    rc = cli.main(["gui", "--no-browser"])
    assert rc == 2
    assert "清单" in capsys.readouterr().out


def test_gui_workdir_error_exits_2(tmp_path, monkeypatch, capsys):
    """启动自检 ①:工作目录解析失败(绿色模式未配置)→ 真实指引文案退 2(终审 I-1)。"""
    from migration import cli
    from migration.workdir import WorkdirError

    def _boom():
        # 与 workdir._resolve_green 未配置分支同文案(指引指向真实入口)
        raise WorkdirError(
            "未配置游戏目录", '启动图形界面后设置,或手动创建 data/config.toml 写入 game_root = "路径"'
        )

    monkeypatch.setattr(cli, "resolve_workdir", _boom)
    rc = cli.main(["gui", "--no-browser"])
    assert rc == 2
    out = capsys.readouterr().out
    assert "未配置游戏目录" in out and "doctor" in out
    # 指引内容完整可见(界面入口 + 手动 config.toml 两条路径)
    assert "data/config.toml" in out and "game_root" in out


def test_migrate_fsops_error_friendly_exit_2(tmp_path, monkeypatch, capsys):
    """携带项 a:执行段 FsOpsError(磁盘不足预检等)→ [错误] what:why 短文案退 2。

    回归:此前 DiskSpaceError 直接以 traceback 顶穿 main,玩家看到的是堆栈而非提示。
    """
    from migration.fsops import DiskSpaceError

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    for d in (src_dir, dst_dir):
        d.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "src", "--game-root", str(game_root)])
    cli.main(["scan", "dst", "--game-root", str(game_root)])
    cli.main(["plan", "src", "dst", "--game-root", str(game_root)])
    capsys.readouterr()

    def _no_disk(*args, **kwargs):
        raise DiskSpaceError(str(dst_dir), "磁盘空间不足:需要 100.0 MB,剩余 1.0 MB")

    monkeypatch.setattr(cli, "execute_migration", _no_disk)
    rc = cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "[错误]" in out and "磁盘空间不足" in out


def test_plan_user_rule_overrides_orphan(tmp_path: Path, monkeypatch, capsys):
    """user rules.yaml 写 config/jade/**→must_migrate 时压过 orphan(P2 用户主权)。

    spec 验收 #2/#8:dst 无 jade mod → 默认 jade 应是 orphan;但用户显式写规则
    时,jade config 应落 must_migrate(orphan 让位于用户意图)。
    """
    import json
    import zipfile

    from migration import cli

    game_root = tmp_path / "game"
    versions = game_root / "versions"
    versions.mkdir(parents=True)
    # src 有 jade config,jade 不在 dst mods
    mini = versions / "mini"
    mini.mkdir()
    (mini / "config").mkdir()
    (mini / "config" / "jade").mkdir()
    (mini / "config" / "jade" / "presets.json").write_text("{}", encoding="utf-8")
    (mini / "options.txt").write_text("v\n", encoding="utf-8")
    (mini / "mods").mkdir()
    target = versions / "target"
    target.mkdir()
    (target / "mods").mkdir()
    # dst 只装 create(无 jade)→ 默认 jade 应是 orphan
    with zipfile.ZipFile(target / "mods" / "create.jar", "w") as z:
        z.writestr(
            "META-INF/neoforge.mods.toml",
            'modLoader="javafml"\nloaderVersion="[1,)"\n'
            '[[mods]]\nmodId="create"\nversion="1.0"\n',
        )

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcmig").mkdir()
    # 用户显式规则:强制迁移 jade
    (tmp_path / ".mcmig" / "rules.yaml").write_text(
        "version: 1\nrules:\n"
        "  - match: 'config/jade/**'\n"
        "    decide: must_migrate\n"
        "    reason: 'user override'\n",
        encoding="utf-8",
    )
    cli.main(["scan", "mini", "--game-root", str(game_root)])
    cli.main(["scan", "target", "--game-root", str(game_root)])
    capsys.readouterr()
    cli.main(["plan", "mini", "target", "--json", "--game-root", str(game_root)])
    doc = json.loads(capsys.readouterr().out)
    origins = {a["path"]: a["origin"] for a in doc["actions"]}
    # 用户规则压过 orphan:jade 落 must_migrate 而非 orphan
    assert origins.get("config/jade/presets.json") == "must_migrate"
