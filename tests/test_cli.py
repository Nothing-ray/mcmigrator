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
    assert snapshot_path(game_root, "mini").exists()


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
    assert plan_path(game_root, "mini", "target").exists()


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
    assert not plan_path(game_root, "mini", "target").exists()


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


def test_safe_reconfigure_streams_forces_utf8_when_redirected():
    """重定向/管道(非 tty)时强制 UTF-8 编码(F7 回归:2026-09 服务端语料 diff JSON 被 GBK 污染)。

    GBK 控制台下 `mcmig diff ... --json > out.json` 若沿用原生编码,机器可读输出
    会变成 GBK 字节,跨机消费即乱码。重定向输出必须恒为 UTF-8(项目编码规范)。
    """
    import io
    import sys

    from migration.cli import _safe_reconfigure_streams

    orig = (sys.stdout, sys.stderr)
    try:
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
        sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
        _safe_reconfigure_streams()
        assert sys.stdout.encoding == "utf-8"
        assert sys.stdout.errors == "replace"
        assert sys.stderr.encoding == "utf-8"
    finally:
        sys.stdout, sys.stderr = orig


def test_safe_reconfigure_streams_keeps_native_encoding_on_tty():
    """真实控制台(tty)保持原生编码,仅 errors 降级 replace(F7 修复不得倒退交互体验)。

    GBK 控制台直接显示时强制 UTF-8 会中文乱码;原生编码 + replace 才是正确语义。
    """
    import io
    import sys

    from migration.cli import _safe_reconfigure_streams

    class FakeTty(io.TextIOWrapper):
        """isatty()=True 的 GBK 文本流,模拟真实 GBK 控制台。"""

        def isatty(self) -> bool:
            return True

    orig = (sys.stdout, sys.stderr)
    try:
        sys.stdout = FakeTty(io.BytesIO(), encoding="gbk", errors="strict")
        sys.stderr = FakeTty(io.BytesIO(), encoding="gbk", errors="strict")
        _safe_reconfigure_streams()
        assert sys.stdout.encoding == "gbk"  # 原生编码保留
        assert sys.stdout.errors == "replace"  # 仅错误处理降级
    finally:
        sys.stdout, sys.stderr = orig


# ---- 批次 B _cmd_diff 接线测试:F2 孤儿 / F4 mod_pairs / 降级提示 ----


def _prep_diff_game_root(tmp_path):
    """建 双版本游戏根:a 有 waystones 旧版 + 共享 create + 孤儿 jade config;b 有 waystones 新版 + create。

    孤儿判定按 spec F2 语义(dst 缺 mod 才标注):config/jade/presets.json 只在 a 且
    jade 未安装于 dst → 孤儿;config/waystones-common.toml 只在 a,但 waystones 在 b
    仍安装(F4 升级对所需)→ 不标注;create 两侧都有 → 非孤儿。
    """
    from tests.conftest import write_mod_jar

    root = tmp_path / "root"
    for ver, wv in (("a", "1.0.1"), ("b", "1.0.2")):
        vdir = root / "versions" / ver
        (vdir / "config").mkdir(parents=True)
        (vdir / "options.txt").write_text("version:x\n", encoding="utf-8")
        write_mod_jar(vdir / "mods" / "create-1.0.jar", "create", "1.0")
        write_mod_jar(vdir / "mods" / f"[tw] waystones-{wv}.jar", "waystones", wv)
    (root / "versions" / "a" / "config" / "waystones-common.toml").write_text(
        "x = 1\n", encoding="utf-8")
    # 孤儿样本:config 只在源,jade mod 两侧均未安装 → 应落 never/orphan
    (root / "versions" / "a" / "config" / "jade").mkdir()
    (root / "versions" / "a" / "config" / "jade" / "presets.json").write_text(
        "{}", encoding="utf-8")
    return root


def _scan_versions(root, tmp_path, monkeypatch, capsys):
    """扫描 a/b 两版(快照落 cwd/.mcmig/snapshots/);排空 capsys 防 scan 输出污染 diff JSON 断言。"""
    monkeypatch.chdir(tmp_path)  # scan 落 cwd/.mcmig
    from migration.cli import main

    assert main(["scan", "a", "--game-root", str(root), "-q"]) == 0
    assert main(["scan", "b", "--game-root", str(root), "-q"]) == 0
    capsys.readouterr()  # 丢弃 scan 的 [完成]/分类汇总 stdout(-q 仅静日志不静 stdout)


def test_diff_json_mod_pairs_and_orphan(tmp_path, monkeypatch, capsys):
    """ctx 可用:diff --json 含 F4 mod_pairs 升级对与 F2 never/orphan 孤儿标注。"""
    root = _prep_diff_game_root(tmp_path)
    _scan_versions(root, tmp_path, monkeypatch, capsys)
    from migration.cli import main

    assert main(["diff", "a", "b", "--json", "--game-root", str(root)]) == 0
    doc = json.loads(capsys.readouterr().out)
    # F4: waystones 1.0.1→1.0.2 升级对(文件名带 [tw] 前缀也按 modid 配对)
    assert len(doc["mod_pairs"]) == 1
    assert (doc["mod_pairs"][0]["modid"], doc["mod_pairs"][0]["kind"]) == ("waystones", "upgrade")
    # F2: dst 未安装 jade → 其 config 落 never/orphan
    orphan = [i for i in doc["buckets"]["never"] if i["path"] == "config/jade/presets.json"]
    assert len(orphan) == 1 and orphan[0]["note"] == "orphan"
    # F2 后半(spec): dst 仍安装 waystones → 其 config 不标注(落 candidate 而非 never)
    assert not [i for i in doc["buckets"]["never"] if i["path"] == "config/waystones-common.toml"]


def test_diff_degraded_when_game_root_unreachable(tmp_path, monkeypatch, capsys):
    """脱敏 game_root → ctx=None:孤儿/注册表配对降级,文件名配对仍可用(F4 fallback)。"""
    root = _prep_diff_game_root(tmp_path)
    _scan_versions(root, tmp_path, monkeypatch, capsys)
    for name in ("a", "b"):
        p = root / ".mcmig" / "snapshots" / f"{name}.snapshot.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["game_root"] = r"C:\\definitely\\missing"
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    from migration.cli import main

    assert main(["diff", "a", "b", "--json", "--game-root", str(root)]) == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    # 注册表配对降级,但文件名配对仍产出 waystones 1.0.1→1.0.2
    assert len(doc["mod_pairs"]) == 1
    assert doc["mod_pairs"][0]["source"] == "filename"
    assert doc["mod_pairs"][0]["modid"] == "waystones"
    assert "mods 扫描不可用" in captured.err
    # 孤儿不再标注(降级 = 0.6.1 行为)
    orphan = [i for i in doc["buckets"]["never"] if i["path"] == "config/jade/presets.json"]
    assert orphan == []


def test_diff_junction_same_dir_orphan_ok_registry_pairs_skipped(
        tmp_path, monkeypatch, capsys):
    """junction 同体: 孤儿标注照常(dst=现役恰为判定基准),注册表配对作废,文件名配对兜底。"""
    import subprocess
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

    assert main(["diff", "a", "b", "--json", "--game-root", str(root)]) == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    # 注册表配对被 same_dir 废止,文件名配对兜底
    assert len(doc["mod_pairs"]) == 1
    assert (doc["mod_pairs"][0]["source"], doc["mod_pairs"][0]["kind"]) == ("filename", "upgrade")
    assert "junction" in captured.err
    # 孤儿规则不受 same_dir 影响:jade 未安装于现役 → config 落 never/orphan
    orphan = [i for i in doc["buckets"]["never"] if i["path"] == "config/jade/presets.json"]
    assert len(orphan) == 1 and orphan[0]["note"] == "orphan"


def test_diff_junction_same_dir_semantic_recheck_falls_back_to_bytes(
        tmp_path, monkeypatch, capsys):
    """junction 同体: 语义复核短路作废,分时快照间的真实 .json 变化按字节判 modified。

    服务端工作流复刻:scan a → 改配置 → scan b(junction b→a 同体,两次快照 md5 不同)。
    若不短路,read_file(rel,"src") 与 read_file(rel,"dst") 读同一物理文件(改后内容),
    语义复核"当前字节 vs 当前字节"恒等 → 真实变化被误报 identical/semantics(终审 Issue 1)。
    文件名用 create.json:create mod 已安装 → 不触发孤儿规则,保持 UNKNOWN→candidate 路径。
    """
    import subprocess
    import os
    from tests.conftest import write_mod_jar

    root = tmp_path / "root"
    va = root / "versions" / "a"
    (va / "mods").mkdir(parents=True)
    (va / "config").mkdir(parents=True)
    (va / "config" / "create.json").write_text('{"v": 1}', encoding="utf-8")
    write_mod_jar(va / "mods" / "create-1.0.jar", "create", "1.0")
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
    # 两次快照之间玩家改了配置:活体文件(=src=dst 同一物理文件)随之变为 v2
    (va / "config" / "create.json").write_text('{"v": 2}', encoding="utf-8")
    assert main(["scan", "b", "--game-root", str(root), "-q"]) == 0
    capsys.readouterr()

    assert main(["diff", "a", "b", "--json", "--game-root", str(root)]) == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    # 核心断言:快照记录的 v1→v2 真实变化必须以字节判定为 modified(candidate),
    # 不得因 junction 同体活体读数恒等被语义复核掩盖成 identical/semantics
    cand = [i for i in doc["buckets"]["candidate"] if i["path"] == "config/create.json"]
    assert len(cand) == 1 and cand[0]["note"] == "modified"
    ident = [i for i in doc["buckets"]["identical"] if i["path"] == "config/create.json"]
    assert ident == []
    # 提示行须同时声明语义复核降级(与注册表配对降级一并告知)
    assert "语义复核" in captured.err


def test_diff_modpack_swap_routes_src_only_mods_to_never(tmp_path, monkeypatch, capsys):
    """F19 批次D:diff --modpack-swap 下源独有旧 jar 归 never/modpack_swap,mods 桶零 to_add。"""
    game_root = _setup_game(tmp_path, ["old", "new"])
    # old 有 a-1.0.jar + b-1.0.jar;new 有 a-2.0.jar —— 手工摆 jar(_setup_game 的 create.jar 会干扰,直接覆盖 mods)
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

    # 带 flag:a-1.0 与 b-1.0 按文件名均源独有(new 只有 a-2.0)→ 全部 never/modpack_swap,
    # mods 桶无 to_add(注册表配对 a-1.0↔a-2.0 只进 mod_pairs,不改桶)
    assert main(["diff", "old", "new", "--game-root", str(game_root),
                 "--modpack-swap", "--json"]) == 0
    res = capsys.readouterr()
    doc = json.loads(res.out)
    swapped = [i for i in doc["buckets"]["never"] if i["note"] == "modpack_swap"]
    assert [i["path"] for i in swapped] == ["mods/a-1.0.jar", "mods/b-1.0.jar"]
    assert not [i for i in doc["buckets"]["mods"] if i["note"] == "to_add"]
    assert "换包模式" in res.err

    # 不带 flag:与 0.6.3 一致 —— a-1.0/b-1.0 是 to_add,never 无 modpack_swap
    assert main(["diff", "old", "new", "--game-root", str(game_root), "--json"]) == 0
    res2 = capsys.readouterr()
    doc2 = json.loads(res2.out)
    assert doc2["summary"]["mods"] == 3
    assert {i["path"]: i["note"] for i in doc2["buckets"]["mods"]}["mods/b-1.0.jar"] == "to_add"
    assert not [i for i in doc2["buckets"]["never"] if i["note"] == "modpack_swap"]


# ---- 批次D F8:.mcmig 生成物锚定 game-root(scan/diff/plan 接线) ----


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
