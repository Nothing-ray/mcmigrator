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
    # 重试引导(终审修复 I3):以「重新 plan 再执行」为前提,不再暗示直接重跑
    assert "重新运行 mcmig plan 再执行" in out
    assert "已完成文件会自动跳过" not in out


# ---- W2.5 复审 B7:CLI migrate write-ahead journal(spec §4.3,与 GUI 同构) ----


def _w25_journal_setup(tmp_path: Path, monkeypatch, capsys) -> Path:
    """建 src/dst 两版本+scan+plan 的公共夹具,返回 game_root。"""
    from migration import cli

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    for d in (src_dir, dst_dir):
        d.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (dst_dir / "options.txt").write_text("fps:60\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "src", "--game-root", str(game_root)])
    cli.main(["scan", "dst", "--game-root", str(game_root)])
    cli.main(["plan", "src", "dst", "--game-root", str(game_root)])
    capsys.readouterr()
    return game_root


def test_migrate_writes_finished_journal(tmp_path: Path, monkeypatch, capsys):
    """W2.5 复审 B7:成功迁移落 journal 于 <game_root>/.mcmig/jobs/,收尾且不入中断清单。

    批次I-W3 T3 机械更新:存储改 JSONL 追加(首行 start 携身份),断言改为
    逐行折叠(不再解全量 JSON 对象);start 行携带 src/dst/game_root 身份
    (scan/dismiss 锁探针判活依据)。
    """
    import json as _json

    from migration import cli
    from migration.journal import scan_interrupted

    game_root = _w25_journal_setup(tmp_path, monkeypatch, capsys)
    assert cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"]) == 0
    out = capsys.readouterr().out
    jobs = list((game_root / ".mcmig" / "jobs").glob("cli-*.jsonl"))
    assert len(jobs) == 1  # job_id 形如 cli-<UTC时间戳>-<pid>(升序=时间序)
    rows = [_json.loads(line) for line
            in jobs[0].read_text(encoding="utf-8").splitlines() if line]
    assert rows[0]["op"] == "start" and rows[0]["kind"] == "migrate"
    assert rows[0]["src"] == "src" and rows[0]["dst"] == "dst"  # start 行携身份
    assert rows[-1]["op"] == "finish"
    assert any(r["op"] == "completion" and r["rel"] == "options.txt" for r in rows)
    assert scan_interrupted(game_root / ".mcmig" / "jobs") == []  # finished 清扫后为空
    assert "迁移完成" in out  # 正常成功路径不受 journal 接线影响


def test_migrate_dry_run_creates_no_journal(tmp_path: Path, monkeypatch, capsys):
    """W2.5 复审 B7:dry-run 零写盘不建 journal(意图会让待核对清单失真)。"""
    from migration import cli

    game_root = _w25_journal_setup(tmp_path, monkeypatch, capsys)
    assert cli.main(
        ["migrate", "src", "dst", "--game-root", str(game_root), "-y", "--dry-run"]) == 0
    capsys.readouterr()
    assert not (game_root / ".mcmig" / "jobs").exists()


def test_migrate_journal_write_failure_not_locked_as_executed(
    tmp_path: Path, monkeypatch, capsys
):
    """W2.5 复审 B7:journal 写失败 → 中止呈现退 2、executed_at 不回写、意图留待核对。

    注入点(批次I-W3 T3 机械更新):``JobJournal._append``——JSONL 追加原语,
    替代旧版全量重写锚点 write_json_atomic;start 行不计次(构造期追加),
    首条 intent 落盘后失败,撑开「操作已发生/完成记录未写」的崩溃窗口
    (与 GUI 同型注入)。
    """
    from migration import cli
    from migration.journal import JobJournal, JournalError, scan_interrupted
    from migration.plan import MigrationPlan

    game_root = _w25_journal_setup(tmp_path, monkeypatch, capsys)
    calls = {"n": 0}
    real_append = JobJournal._append

    def _flaky(self, line: dict) -> None:
        if line.get("op") != "start":  # start 行不计次(构造期追加,非记录)
            calls["n"] += 1
        if calls["n"] > 1:  # 首条 intent 落盘后失败
            self.write_failed = True  # 模拟真实 _append 的 OSError 留痕语义
            raise JournalError("journal 写入失败: disk full (injected)")
        real_append(self, line)

    monkeypatch.setattr(JobJournal, "_append", _flaky)
    rc = cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "journal 写入失败" in captured.err
    assert "迁移完成" not in captured.out
    # 计划未回写执行状态(重跑不被 plan_executed 拦,修复后可重新 plan)
    plan_file = game_root / ".mcmig" / "plans" / "src__dst.plan.json"
    assert MigrationPlan.load(plan_file).executed_at is None
    # 落盘的意图无完成记录 → 中断清单「待核对」(≠未执行,结局须人工核对)
    items = scan_interrupted(game_root / ".mcmig" / "jobs")
    assert items and items[0]["kind"] == "migrate"
    assert items[0]["entries"][0]["rel"] == "options.txt"


def test_migrate_journal_finish_failure_friendly(tmp_path: Path, monkeypatch, capsys):
    """修复 A4:journal 收尾(finish)写失败不再裸穿 traceback——中文引导退 2;
    迁移文件操作属实已完成(进度未丢),仅收尾记录缺失(该记录留待核对)。"""
    from migration import cli
    from migration.journal import JobJournal, JournalError, scan_interrupted

    game_root = _w25_journal_setup(tmp_path, monkeypatch, capsys)
    real_append = JobJournal._append

    def _finish_flaky(self, line: dict) -> None:
        if line.get("op") == "finish":
            self.write_failed = True  # 模拟真实 _append 的 OSError 留痕语义
            raise JournalError("journal 写入失败: disk full (injected)")
        real_append(self, line)

    monkeypatch.setattr(JobJournal, "_append", _finish_flaky)
    rc = cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "journal 收尾写入失败" in captured.err
    assert "待核对" in captured.err
    assert "迁移完成" not in captured.out
    # 迁移本身已执行(options.txt 已复制到目标)
    assert (game_root / "versions" / "dst" / "options.txt").read_text(
        encoding="utf-8") == "fps:120\n"
    # 盘上无 finish 行 → 该记录仍待核对(引导文案属实)
    items = scan_interrupted(game_root / ".mcmig" / "jobs")
    assert items and items[0]["kind"] == "migrate"


def test_migrate_plan_save_failure_friendly(tmp_path: Path, monkeypatch, capsys):
    """修复 A4:迁移完成后计划回写失败 → 中文引导退 2(不再 traceback);
    迁移文件已完成、executed_at 未回写。"""
    from migration import cli
    from migration.fsops import FsOpsError
    from migration.plan import MigrationPlan

    game_root = _w25_journal_setup(tmp_path, monkeypatch, capsys)

    def _boom(self, path: Path) -> None:
        raise FsOpsError(str(path), "磁盘空间不足(注入)")

    monkeypatch.setattr(MigrationPlan, "save", _boom)
    rc = cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "计划文件回写失败" in captured.err and "磁盘空间不足(注入)" in captured.err
    assert "迁移文件已完成" in captured.err
    # 文件确实已迁移(回写失败只影响「已执行」标记)
    assert (game_root / "versions" / "dst" / "options.txt").read_text(
        encoding="utf-8") == "fps:120\n"


def test_migrate_force_maps_three_decisions(tmp_path, monkeypatch, capsys):
    """--force = 三项显式决策(白名单③):已执行/快照过期/疑似占用均放行,目录缺失仍拒。

    三项全中构造:首跑回写 executed_at(已执行)→ 重扫 src(快照 mtime 变新,过期)
    → 独占句柄持有 dst/usercache.json(疑似占用,真独占句柄见 conftest.hold_exclusive)。
    批次I-W3 T1:前置检查经 precheck_execution 单点后,多阻断并存时全部逐条
    呈现且首错序统一为「守卫→预检→状态校验」(本用例重扫 src 先触发守卫
    snapshot_changed 居首;「已执行」文案仍在输出中,断言不依赖顺序)。
    """
    import shutil as _shutil

    from tests.conftest import hold_exclusive

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    for d in (src_dir, dst_dir):
        d.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (dst_dir / "options.txt").write_text("fps:60\n", encoding="utf-8")
    (dst_dir / "usercache.json").write_text("[]", encoding="utf-8")  # 不入 plan,只供占用探测
    monkeypatch.chdir(tmp_path)
    assert cli.main(["scan", "src", "--game-root", str(game_root)]) == 0
    assert cli.main(["scan", "dst", "--game-root", str(game_root)]) == 0
    assert cli.main(["plan", "src", "dst", "--game-root", str(game_root)]) == 0
    capsys.readouterr()
    assert cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"]) == 0
    capsys.readouterr()
    # 三项全中:已执行(刚回写)+ 重扫 src 后快照比 plan 新 + 独占持有 usercache.json
    assert cli.main(["scan", "src", "--game-root", str(game_root)]) == 0
    capsys.readouterr()
    with hold_exclusive(dst_dir / "usercache.json"):
        # 不加 --force:多阻断并存全部逐条呈现(批次I-W3 T1,守卫 snapshot_changed
        # 居首),「已执行」文案逐字不变
        rc = cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"])
        out = capsys.readouterr().out
        assert rc == 2 and "该计划已执行" in out
        # --force:三项均降级放行,实际执行成功;降级警告走 stderr(疑似措辞,白名单④)
        rc2 = cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y", "--force"])
        captured = capsys.readouterr()
        assert rc2 == 0
        assert "疑似被占用" in captured.err
    # 目录缺失:--force 也拒(永不可强制,白名单③)
    _shutil.rmtree(dst_dir)
    rc3 = cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y", "--force"])
    out3 = capsys.readouterr().out
    assert rc3 == 2 and "源/目标版本文件夹不存在" in out3


# ---- 批次I-T3:审阅有效性(persisted 阻断 + 渐进采用) ----


def test_plan_persist_failure_exits_2(tmp_path, monkeypatch, capsys):
    """白名单②:plan 保存失败上抛 PlanPersistError → [错误] + 退出 2(不再吞)。"""
    from migration.plan import MigrationPlan

    game_root = tmp_path / "game"
    (game_root / "versions" / "src").mkdir(parents=True)
    (game_root / "versions" / "dst").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "src", "--game-root", str(game_root)])
    cli.main(["scan", "dst", "--game-root", str(game_root)])
    capsys.readouterr()

    def _boom(self, path):
        raise OSError("disk full (注入)")

    monkeypatch.setattr(MigrationPlan, "save", _boom)
    rc = cli.main(["plan", "src", "dst", "--game-root", str(game_root)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "[错误]" in out and "plan 文件写入失败" in out


def test_migrate_old_plan_without_review_hint_and_continue(tmp_path, monkeypatch, capsys):
    """渐进采用:旧 plan(无 review 键)→ stderr 提示后继续执行,不阻断既有流程。"""
    import json as _json

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    src_dir.mkdir(parents=True)
    dst_dir.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cli.main(["scan", "src", "--game-root", str(game_root)])
    cli.main(["scan", "dst", "--game-root", str(game_root)])
    cli.main(["plan", "src", "dst", "--game-root", str(game_root)])
    capsys.readouterr()
    # 剥掉 review 键 → 模拟旧版工具生成的 plan
    plan_file = game_root / ".mcmig" / "plans" / "src__dst.plan.json"
    doc = _json.loads(plan_file.read_text(encoding="utf-8"))
    doc.pop("review", None)
    plan_file.write_text(_json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    rc = cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "缺少审阅守卫" in captured.err  # 提示走 stderr,不污染 stdout
    assert (dst_dir / "options.txt").read_text(encoding="utf-8") == "fps:120\n"  # 照常执行


def test_migrate_review_rules_change_blocks_exit_2(tmp_path: Path, monkeypatch, capsys):
    """终审修复 I1:plan 后 rules.yaml 出现/变化 → migrate 审阅守卫阻断(rules_changed)退 2,零执行。

    CLI 自批次I 起执行与 GUI 同源的 validate_review(spec §3.3 白名单增补):
    规则指纹失配时提示重跑 plan;重新 plan(规则纳入指纹)后同一链路放行。
    """
    from migration import cli

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    for d in (src_dir, dst_dir):
        d.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (dst_dir / "options.txt").write_text("fps:60\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["scan", "src", "--game-root", str(game_root)]) == 0
    assert cli.main(["scan", "dst", "--game-root", str(game_root)]) == 0
    assert cli.main(["plan", "src", "dst", "--game-root", str(game_root)]) == 0
    capsys.readouterr()
    # 计划签发后规则出现(签发时无 rules.yaml,指纹为空)→ 规则指纹失配
    (game_root / ".mcmig" / "rules.yaml").write_text(
        "version: 1\nrules:\n  - match: 'config/nope.toml'\n    decide: never\n"
        "    reason: 'test'\n",
        encoding="utf-8",
    )
    rc = cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "规则文件在计划签发后已变化" in out
    assert (dst_dir / "options.txt").read_text(encoding="utf-8") == "fps:60\n"  # 零执行
    # 重新 plan(规则纳入指纹)→ 守卫两侧同构,migrate 放行(不改规则时链路不破坏)
    assert cli.main(["plan", "src", "dst", "--game-root", str(game_root)]) == 0
    capsys.readouterr()
    rc2 = cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"])
    assert rc2 == 0
    assert (dst_dir / "options.txt").read_text(encoding="utf-8") == "fps:120\n"


def test_migrate_review_guard_legacy_snapshots_no_false_positive(
    tmp_path: Path, monkeypatch, capsys
):
    """复审修复(I1 遗留回归):快照仅存旧布局(cwd/.mcmig/snapshots)→ 守卫不再假阳性。

    签发侧 build_plan 在锚定快照缺失时回退载入旧布局快照并把**旧快照哈希**记入
    review(pipeline.issue_review);重验侧必须对称取 find_snapshot 的同一实际命中位
    ——修复前硬编码锚定路径(不存在)→ 双侧 snapshot_changed 退 2,而「重跑 plan」
    仍读旧布局,死循环。修复后守卫放行,migrate 正常执行(锚定布局的漂移检测由
    test_migrate_review_rules_change_blocks_exit_2 / GUI 漂移用例继续覆盖,不弱化)。
    """
    from migration import cli

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    for d in (src_dir, dst_dir):
        d.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (dst_dir / "options.txt").write_text("fps:60\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["scan", "src", "--game-root", str(game_root)]) == 0
    assert cli.main(["scan", "dst", "--game-root", str(game_root)]) == 0
    # 快照仅留旧布局:锚定位文件挪到 cwd/.mcmig/snapshots(rename 保 mtime,不触发过期)
    legacy_snaps = tmp_path / ".mcmig" / "snapshots"
    legacy_snaps.mkdir(parents=True)
    anchored = game_root / ".mcmig" / "snapshots"
    for ver in ("src", "dst"):
        (anchored / f"{ver}.snapshot.json").rename(legacy_snaps / f"{ver}.snapshot.json")
    anchored.rmdir()
    # 新版 plan:回退读旧布局快照,review 记录的是旧快照哈希
    assert cli.main(["plan", "src", "dst", "--game-root", str(game_root)]) == 0
    capsys.readouterr()
    rc = cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "在计划签发后已变化" not in out  # 无假阳性 snapshot_changed
    assert (dst_dir / "options.txt").read_text(encoding="utf-8") == "fps:120\n"  # 真实执行


def test_migrate_legacy_snapshot_state_change_blocked(
    tmp_path: Path, monkeypatch, capsys
):
    """W2.6 复审 P1-1(reviewer 复现):旧布局快照下签发后改目标文件 → 无 --force 必须阻断。

    修复前:守卫哈希重验经 find_snapshot 回退取旧布局(比对通过),而执行侧
    _load_guard_snapshots 只认锚定布局 → 缺失即静默跳过审阅状态校验 → 目标
    options.txt 的 fps:999 漂移不被发现,被无 force 覆盖回 fps:120。修复后
    CLI 把同一 legacy_dir 传入执行管线,状态校验在旧布局快照上照常拦截,
    零写盘退出 2。
    """
    from migration import cli

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    dst_dir = game_root / "versions" / "dst"
    for d in (src_dir, dst_dir):
        d.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (dst_dir / "options.txt").write_text("fps:60\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["scan", "src", "--game-root", str(game_root)]) == 0
    assert cli.main(["scan", "dst", "--game-root", str(game_root)]) == 0
    # 快照仅留旧布局(与上一测试同法:rename 保 mtime,不触发快照过期预检)
    legacy_snaps = tmp_path / ".mcmig" / "snapshots"
    legacy_snaps.mkdir(parents=True)
    anchored = game_root / ".mcmig" / "snapshots"
    for ver in ("src", "dst"):
        (anchored / f"{ver}.snapshot.json").rename(legacy_snaps / f"{ver}.snapshot.json")
    anchored.rmdir()
    assert cli.main(["plan", "src", "dst", "--game-root", str(game_root)]) == 0
    capsys.readouterr()
    # 签发后外部改动目标:状态校验必须在旧布局快照上拦截
    (dst_dir / "options.txt").write_text("fps:999\n", encoding="utf-8")
    rc = cli.main(["migrate", "src", "dst", "--game-root", str(game_root), "-y"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "与审阅时不一致" in out
    assert (dst_dir / "options.txt").read_text(encoding="utf-8") == "fps:999\n"  # 零执行


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
        # 批次H:v2 快照内嵌名册在不可达时走冻结通道(复放保真,spec §3.2)——
        # 本测试锚定 v1 快照(无名册)的降级路径,故同步剔除 mods 键降 v1;
        # v2 不可达保真由 tests/test_synth_v2_anchors.py 锚定
        doc.pop("mods", None)
        doc["snapshot_format"] = 1
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
    # F27:钉死同刻(两次 scan 可能同秒也可能异秒,显式改写消除抖动)
    _force_scanned_at(snapshot_path(root, "b"),
                      json.loads(snapshot_path(root, "a").read_text(encoding="utf-8"))["scanned_at"])
    # 批次H:v2 快照内嵌名册来自各自 scan 时刻,会让 junction 配对走冻结通道(修 F14,
    # spec §3.2 行为矩阵)——本测试锚定 v1 快照(无名册)的 junction 现扫路径:
    # 双侧注册表恒等(都拍当前状态)→ 配对作废,文件名兜底。剔除 mods 键降 v1;
    # v2 junction 配对修复由 tests/test_synth_v2_anchors.py 黄金锚①承担
    for name in ("a", "b"):
        p = snapshot_path(root, name)
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc.pop("mods", None)
        doc["snapshot_format"] = 1
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

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
    # F27:钉死同刻(两次 scan 可能同秒也可能异秒,显式改写消除抖动)
    _force_scanned_at(snapshot_path(root, "b"),
                      json.loads(snapshot_path(root, "a").read_text(encoding="utf-8"))["scanned_at"])

    assert main(["diff", "a", "b", "--json", "--game-root", str(root)]) == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    # 核心断言:快照记录的 v1→v2 真实变化必须以字节判定为 modified(candidate),
    # 不得因 junction 同体活体读数恒等被语义复核掩盖成 identical/semantics
    cand = [i for i in doc["buckets"]["candidate"] if i["path"] == "config/create.json"]
    assert len(cand) == 1 and cand[0]["note"] == "modified"
    ident = [i for i in doc["buckets"]["identical"] if i["path"] == "config/create.json"]
    assert ident == []
    # 终审收敛(批次H):双侧 scan 产 v2 嵌入名册 → 冻结通道下 junction 同刻不再发
    # 「注册表配对与语义复核不可用」降级通告(registry 配对实际存活,文案与行为
    # 矛盾);内容层字节判定不受影响(上方 candidate/modified 断言即证)
    assert "junction" not in captured.err
    assert "注册表配对" not in captured.err


def _force_scanned_at(snapshot_file: Path, value: str) -> None:
    """改写快照 JSON 的 scanned_at(钉死时间戳,消除秒级精度的门控抖动,F27)。"""
    doc = json.loads(snapshot_file.read_text(encoding="utf-8"))
    doc["scanned_at"] = value
    snapshot_file.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def test_diff_junction_same_dir_different_scan_times_hint_silent(
        tmp_path, monkeypatch, capsys) -> None:
    """F27:不同刻双快照(标准影子根用法)降级提示静默;同刻亦静默——双侧 v2 走
    冻结通道,registry 配对存活,降级文案与行为矛盾(批次H 终审收敛)。"""
    import subprocess
    import os
    from tests.conftest import write_mod_jar

    root = tmp_path / "root"
    va = root / "versions" / "a"
    (va / "mods").mkdir(parents=True)
    write_mod_jar(va / "mods" / "x-1.0.jar", "x", "1.0")
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
    (va / "mods" / "x-1.0.jar").unlink()
    write_mod_jar(va / "mods" / "x-2.0.jar", "x", "2.0")
    assert main(["scan", "b", "--game-root", str(root), "-q"]) == 0
    capsys.readouterr()
    # 钉死不同刻(实际两次 scan 可能同秒,显式改写消除抖动)
    _force_scanned_at(snapshot_path(root, "a"), "2026-09-23T11:00:00+08:00")
    _force_scanned_at(snapshot_path(root, "b"), "2026-09-23T11:40:00+08:00")

    assert main(["diff", "a", "b", "--json", "--game-root", str(root)]) == 0
    captured = capsys.readouterr()
    assert len(json.loads(captured.out)["mod_pairs"]) == 1  # 文件名配对照常
    assert "junction" not in captured.err   # 提示静默(F27)
    assert "语义复核" not in captured.err

    # 同刻 + 冻结通道(双侧 v2 嵌入名册)→ 终审收敛(批次H):junction 同体降级
    # 通告不再发出——registry 配对实际存活(source=registry 即证),旧文案与
    # 真实行为矛盾;v1 快照(无名册,mods_frozen 恒 False)的回退臂仍由
    # test_diff_identity_notices_paths_and_replay ④ 直接钉住
    _force_scanned_at(snapshot_path(root, "b"), "2026-09-23T11:00:00+08:00")
    assert main(["diff", "a", "b", "--json", "--game-root", str(root)]) == 0
    captured = capsys.readouterr()
    assert "junction" not in captured.err
    pairs = json.loads(captured.out)["mod_pairs"]
    assert len(pairs) == 1 and pairs[0]["source"] == "registry"


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


# ---- 批次E F23/F26:swap 视角配对标记保留 ----


def test_diff_modpack_swap_keeps_pairing_markers(tmp_path, monkeypatch, capsys) -> None:
    """F23/F26:swap 下配对输入取快照集合差——mod_pairs 非空、渲染保 ⇄upgrade 与汇总行。

    两侧 jar modid 故意不同(模拟作者改名/无活体复放)→ registry 配对为空,
    纯文件名配对路径正是九轮 5/5 复现的盲区。
    """
    game_root = _setup_game(tmp_path, ["old", "new"])
    for v in ("old", "new"):
        d = game_root / "versions" / v / "mods"
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.jar"):
            f.unlink()
    from tests.conftest import write_mod_jar
    write_mod_jar(game_root / "versions" / "old" / "mods" / "a-1.0.jar", "mod_old", "1.0")
    write_mod_jar(game_root / "versions" / "new" / "mods" / "a-2.0.jar", "mod_new", "2.0")
    monkeypatch.chdir(tmp_path)
    from migration.cli import main
    assert main(["scan", "old", "--game-root", str(game_root), "-q"]) == 0
    assert main(["scan", "new", "--game-root", str(game_root), "-q"]) == 0
    capsys.readouterr()

    # --json:mod_pairs 保留(0.7.0 为空),note 字段保持原始值不被标记污染
    assert main(["diff", "old", "new", "--game-root", str(game_root),
                 "--modpack-swap", "--json"]) == 0
    res = capsys.readouterr()
    doc = json.loads(res.out)
    assert len(doc["mod_pairs"]) == 1
    mp = doc["mod_pairs"][0]
    assert (mp["modid"], mp["kind"], mp["source"]) == ("a", "upgrade", "filename")
    notes = {i["path"]: i["note"] for i in doc["buckets"]["mods"]}
    assert notes["mods/a-2.0.jar"] == "target_only"  # JSON note 纯度
    never_notes = {i["path"]: i["note"]
                   for i in doc["buckets"]["never"] if i["note"] == "modpack_swap"}
    assert list(never_notes) == ["mods/a-1.0.jar"]
    assert "换包模式" in res.err

    # rich 渲染:target_only 带 ⇄upgrade + 配对汇总行(0.7.0 均丢失)
    assert main(["diff", "old", "new", "--game-root", str(game_root),
                 "--modpack-swap"]) == 0
    out = capsys.readouterr().out
    assert "target_only ⇄upgrade" in out
    assert "配对: ⇄upgrade ×1" in out


# ---- 批次F Task 3:自比对/junction 同体检测单点 diff_identity_notices ----


def test_diff_identity_notices_paths_and_replay(tmp_path: Path) -> None:
    """自比对检测统一:① 同一快照文件 → 提示;② 复放模式(无活体)resolved_root+同刻 → 提示;
    ③ resolved_root 同、刻不同 → None(静默);④ 旧快照缺字段 → 回退 ctx.same_dir+scanned_at。"""
    from migration.moddb import ModRegistry
    from migration.pipeline import DiffContext, diff_identity_notices
    from migration.snapshot import Snapshot

    def mk(scanned_at: str, resolved_root: str | None = None) -> Snapshot:
        """最小快照工厂:只填身份相关字段(diff_identity_notices 的输入面)。"""
        return Snapshot(version="v", game_root="C:/g", scanned_at=scanned_at,
                        hash_mode="tiered", file_count=0, files=[],
                        resolved_root=resolved_root)

    p = tmp_path / "a.snapshot.json"
    pa = tmp_path / "pa.snapshot.json"
    pb = tmp_path / "pb.snapshot.json"
    for f in (p, pa, pb):
        f.write_text("{}", encoding="utf-8")
    snap_a = mk("2026-09-29T10:00:00+08:00")
    ctx_same_dir = DiffContext(
        src_mods=ModRegistry(), dst_mods=ModRegistry(),
        src_dir=tmp_path, dst_dir=tmp_path, same_dir=True,
    )
    # ① 同文件
    assert diff_identity_notices(p, p, snap_a, snap_a, ctx=None) is not None
    # ② 复放:两文件不同,但 resolved_root 相同 + scanned_at 相同,ctx=None
    assert "自比对" in diff_identity_notices(
        pa, pb,
        mk("2026-09-29T10:00:00+08:00", "C:/real/v"),
        mk("2026-09-29T10:00:00+08:00", "C:/real/v"),
        None)
    # ③ 同物理根不同刻 → None
    assert diff_identity_notices(
        pa, pb,
        mk("2026-09-29T10:00:00+08:00", "C:/real/v"),
        mk("2026-09-29T11:00:00+08:00", "C:/real/v"),
        None) is None
    # ④ 回退:resolved_root 均 None,ctx.same_dir=True 且同刻 → 现行 F27 文案
    msg = diff_identity_notices(
        pa, pb,
        mk("2026-09-29T10:00:00+08:00"),
        mk("2026-09-29T10:00:00+08:00"),
        ctx_same_dir)
    assert "同刻" in msg and "junction 同体" in msg


def test_diff_same_snapshot_file_self_hint(tmp_path: Path, monkeypatch, capsys) -> None:
    """CLI:mcmig diff X X(同名快照)stderr 出自比对提示(复放模式同样触发)。"""
    game_root = _setup_game(tmp_path, ["solo"])
    monkeypatch.chdir(tmp_path)
    from migration.cli import main

    assert main(["scan", "solo", "--game-root", str(game_root), "-q"]) == 0
    capsys.readouterr()
    # live 模式:同一快照文件装入两侧 → 主判提示(stderr),stdout 仍为合法 JSON
    assert main(["diff", "solo", "solo", "--game-root", str(game_root), "--json"]) == 0
    captured = capsys.readouterr()
    json.loads(captured.out)
    assert "自比对" in captured.err

    # 复放模式(无 game-root,快照挪到 CWD 旧布局)同样触发
    import shutil
    replay_dir = tmp_path / ".mcmig" / "snapshots"
    replay_dir.mkdir(parents=True)
    shutil.copy(str(snapshot_path(game_root, "solo")), str(replay_dir / "solo.snapshot.json"))
    monkeypatch.delenv("MCMIG_GAME_ROOT", raising=False)
    assert main(["diff", "solo", "solo", "--json"]) == 0
    captured = capsys.readouterr()
    json.loads(captured.out)
    assert "自比对" in captured.err


# ---- 批次I-T1 路径契约 v3:plan 用户规则锚定 game_root/.mcmig(_rules_dir 选择) ----

# never 规则模板:命中 options.txt(variant_b 使其必迁,规则生效即归零)
_T1_NEVER_RULE = (
    "version: 1\n"
    "rules:\n"
    "  - match: options.txt\n"
    "    decide: never\n"
    "    reason: 测试锚定\n"
)


def _t1_plan_origins(game_root: Path, tmp_path: Path, capsys) -> dict[str, str]:
    """跑一次 plan --json,返回 {路径: origin}(断言 never 生效与否的公共helper)。"""
    import json

    from migration import cli

    assert cli.main(
        ["plan", "src", "dst", "--game-root", str(game_root), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    return {a["path"]: a["origin"] for a in doc["actions"]}


def test_plan_rules_anchored_to_game_root(tmp_path, monkeypatch, capsys):
    """批次I-T1 路径契约 v3:用户规则读取 <game_root>/.mcmig/rules.yaml(而非 cwd/.mcmig)(spec §3.1)。

    cwd/.mcmig 不存在 → never 生效只能来自 game_root 侧规则;断言以 plan
    --json 的 origin 表达(options.txt 落 never、must_migrate 计数归零)。
    """
    from migration import cli

    game_root = _setup_game(tmp_path, ["src", "dst"], variant_b_for="dst")
    rr = game_root / ".mcmig"
    rr.mkdir(parents=True)
    (rr / "rules.yaml").write_text(_T1_NEVER_RULE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # cwd/.mcmig 不存在 → 只能来自 game_root 侧
    assert cli.main(["scan", "src", "--game-root", str(game_root), "-q"]) == 0
    assert cli.main(["scan", "dst", "--game-root", str(game_root), "-q"]) == 0
    capsys.readouterr()

    import json

    assert cli.main(["plan", "src", "dst", "--game-root", str(game_root), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    origins = {a["path"]: a["origin"] for a in doc["actions"]}
    assert origins.get("options.txt") == "never"  # never 生效=规则被读取
    assert doc["summary"].get("must_migrate", 0) == 0
    assert not (tmp_path / ".mcmig").exists()  # cwd 侧从未创建


def test_plan_rules_legacy_fallback_and_precedence(tmp_path, monkeypatch, capsys, caplog):
    """批次I-T1 路径契约 v3:仅旧布局(cwd/.mcmig)规则→回退读+提示建议迁移;并存→新位置优先+提示忽略旧。"""
    from migration import cli

    game_root = _setup_game(tmp_path, ["src", "dst"], variant_b_for="dst")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["scan", "src", "--game-root", str(game_root), "-q"]) == 0
    assert cli.main(["scan", "dst", "--game-root", str(game_root), "-q"]) == 0
    capsys.readouterr()

    # ① 仅旧布局存在 → 回退读旧规则(never 生效)+ warning 提示建议迁移
    legacy = tmp_path / ".mcmig"
    legacy.mkdir()
    (legacy / "rules.yaml").write_text(_T1_NEVER_RULE, encoding="utf-8")
    caplog.clear()
    with caplog.at_level("WARNING", logger="migration.pipeline"):
        origins = _t1_plan_origins(game_root, tmp_path, capsys)
    assert origins.get("options.txt") == "never"
    assert any("旧布局规则" in m for m in caplog.messages)

    # ② 新旧并存 → 新位置优先(旧规则被忽略,never 不再生效)+ warning 提示已忽略
    new = game_root / ".mcmig"
    new.mkdir(parents=True, exist_ok=True)
    (new / "rules.yaml").write_text("version: 1\nrules: []\n", encoding="utf-8")
    caplog.clear()
    with caplog.at_level("WARNING", logger="migration.pipeline"):
        origins = _t1_plan_origins(game_root, tmp_path, capsys)
    assert origins.get("options.txt") != "never"  # 旧规则被忽略,恢复默认判定
    assert any("已忽略" in m for m in caplog.messages)


# ---- W2.5 复审 B3:diff/scan 规则目录与 plan 同口径(spec §3.1 T1 全入口) ----


def test_scan_rules_anchored_to_game_root(tmp_path, monkeypatch, capsys):
    """W2.5 复审 B3:scan 分类读取 <game_root>/.mcmig/rules.yaml(此前仅读 cwd/.mcmig)。

    cwd/.mcmig 不存在时 never 生效只能来自 game_root 侧规则——options.txt 从
    must_migrate 翻 never(计数此消彼长),diff 预演/plan 正片/scan 汇总三口径一致。
    """
    import json

    from migration import cli

    game_root = _setup_game(tmp_path, ["mini"])
    monkeypatch.chdir(tmp_path)  # cwd/.mcmig 不存在
    assert cli.main(["scan", "mini", "--game-root", str(game_root), "--json"]) == 0
    base = json.loads(capsys.readouterr().out)["by_category"]

    rr = game_root / ".mcmig"
    rr.mkdir(parents=True, exist_ok=True)  # 首轮 scan 已建 snapshots/
    (rr / "rules.yaml").write_text(_T1_NEVER_RULE, encoding="utf-8")
    assert cli.main(["scan", "mini", "--game-root", str(game_root), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)["by_category"]
    assert doc.get("never", 0) == base.get("never", 0) + 1      # options.txt 翻 never
    assert doc.get("must_migrate", 0) == base.get("must_migrate", 0) - 1
    assert not (tmp_path / ".mcmig").exists()                    # cwd 侧从未创建


def test_diff_rules_anchored_to_game_root(tmp_path, monkeypatch, capsys):
    """W2.5 复审 B3:mcmig diff 读取 <game_root>/.mcmig/rules.yaml(与 plan 同口径)。

    规则生效 → options.txt 落 never 桶(--category never 可见),不再入
    to_migrate——「diff 预演与 plan 正片口径分裂」(复审 Important 3)收口。
    """
    from migration import cli

    game_root = _setup_game(tmp_path, ["src", "dst"], variant_b_for="dst")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["scan", "src", "--game-root", str(game_root), "-q"]) == 0
    assert cli.main(["scan", "dst", "--game-root", str(game_root), "-q"]) == 0
    capsys.readouterr()

    # 基线:无用户规则 → never 桶无 options.txt
    assert cli.main(
        ["diff", "src", "dst", "--game-root", str(game_root), "--category", "never"]) == 0
    assert "options.txt" not in capsys.readouterr().out

    rr = game_root / ".mcmig"
    rr.mkdir(parents=True, exist_ok=True)  # scan 已建 snapshots/
    (rr / "rules.yaml").write_text(_T1_NEVER_RULE, encoding="utf-8")
    assert cli.main(
        ["diff", "src", "dst", "--game-root", str(game_root), "--category", "never"]) == 0
    assert "options.txt" in capsys.readouterr().out  # never 生效=规则被读取


# ---- 批次I-T5:跨进程实例锁接线(锁体真实现由 test_instlock.py 覆盖) ----


def test_scan_acquires_instance_lock(tmp_path: Path, monkeypatch, capsys):
    """批次I-T5:scan 以 game_root+单版本获取实例锁(monkeypatch 记录参数);无争用输出不变。"""
    from contextlib import contextmanager

    game_root = _setup_game(tmp_path, ["mini"])
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[Path, tuple[str, ...]]] = []

    @contextmanager
    def _recording_locks(game_root, *versions, timeout=5.0):
        calls.append((Path(game_root), versions))
        yield {"abandoned": []}

    monkeypatch.setattr(cli, "instance_locks", _recording_locks)
    assert cli.main(["scan", "mini", "--game-root", str(game_root), "--json"]) == 0
    assert calls == [(game_root, ("mini",))]
    # --json stdout 不受锁接线影响(无争用无新增 stdout 行)
    doc = json.loads(capsys.readouterr().out)
    assert doc["version"] == "mini"


def test_scan_abandoned_lock_warns_stderr(tmp_path: Path, monkeypatch, capsys):
    """批次I-T5:abandoned 锁 → stderr [警告](jobs/ 为 T6 journal 前向引用占位)。"""
    from contextlib import contextmanager

    game_root = _setup_game(tmp_path, ["mini"])
    monkeypatch.chdir(tmp_path)

    @contextmanager
    def _abandoned_locks(game_root, *versions, timeout=5.0):
        yield {"abandoned": [str(Path(game_root) / "versions" / versions[0])]}

    monkeypatch.setattr(cli, "instance_locks", _abandoned_locks)
    assert cli.main(["scan", "mini", "--game-root", str(game_root), "--json"]) == 0
    captured = capsys.readouterr()
    assert "上次异常退出的实例锁" in captured.err
    assert ".mcmig" in captured.err and "jobs" in captured.err
    assert captured.out.startswith("{")  # stdout 仍为纯 JSON


def test_lock_error_exits_2_with_stderr(tmp_path: Path, monkeypatch, capsys):
    """批次I-T5:实例锁争用(注入)→ CLI 统一出口 stderr [错误] 三段式、退出码 2。"""
    from contextlib import contextmanager

    from migration.instlock import InstanceLockError

    game_root = _setup_game(tmp_path, ["mini"])
    monkeypatch.chdir(tmp_path)

    @contextmanager
    def _contended_locks(game_root, *versions, timeout=5.0):
        key = str(Path(game_root) / "versions" / versions[0])
        raise InstanceLockError(
            f"实例锁({key})",
            "获取实例排他锁超时:另一 mcmig 进程正在操作该实例",
            {"holders": [key]},
        )
        yield  # pragma: no cover — 仅为满足 contextmanager 生成器语法

    monkeypatch.setattr(cli, "instance_locks", _contended_locks)
    assert cli.main(["scan", "mini", "--game-root", str(game_root)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout 零污染(--json 机器可读输出不受影响)
    assert "[错误] 实例锁(" in captured.err
    assert "另一 mcmig 进程" in captured.err


# ---- 批次I-W3 T7:swap 覆盖备份位置输出(白名单⑥) ----


def test_cli_swap_prints_backup_location(tmp_path: Path, monkeypatch, capsys):
    """T7/spec §5.2:覆盖发生时,装包统计后输出备份位置行(backups/swap/<时间戳>)。

    Windows 下 str(Path) 用反斜杠,断言按正斜杠归一(路径语义等价)。
    """
    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    dst_dir = game_root / "versions" / "dst"
    dst_dir.mkdir()
    (dst_dir / "dst.json").write_text(
        '{"arguments": {"game": ["--fml.neoforgeVersion", "21.1.228"]}}',
        encoding="utf-8")
    (dst_dir / "mods").mkdir()
    (dst_dir / "mods" / "victim-1.0.jar").write_bytes(b"OLD")
    pack_mods = tmp_path / "newpack" / "mods"
    pack_mods.mkdir(parents=True)
    (pack_mods / "victim-1.0.jar").write_bytes(b"NEW")   # 同名不同内容→覆盖+备份
    (pack_mods / "fresh-1.0.jar").write_bytes(b"F")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["scan", "src", "--game-root", str(game_root)]) == 0
    capsys.readouterr()
    # 冲突决策一律覆盖(绕开交互),覆盖前旧件进备份目录
    monkeypatch.setattr(cli.Confirm, "ask", lambda *a, **k: True)
    assert cli.main(["swap", "src", "dst", str(pack_mods.parent),
                     "--game-root", str(game_root)]) == 0
    out = capsys.readouterr().out
    assert "backups/swap/" in out.replace("\\", "/")
    assert (dst_dir / "mods" / "victim-1.0.jar").read_bytes() == b"NEW"
    backup_dirs = list((game_root / ".mcmig" / "backups" / "swap").iterdir())
    assert backup_dirs and (backup_dirs[0] / "victim-1.0.jar").read_bytes() == b"OLD"


def test_cli_swap_journal_write_failure_friendly(tmp_path: Path, monkeypatch, capsys):
    """终审I-1:swap journal 写失败 → 不 traceback、退 2、stderr 两行中文引导。

    注入点与 migrate 的 journal 失败用例同型(``JobJournal._append``,start 行
    不计次):首条 intent 落盘后失败,撑开「jar 已装/完成记录未写」窗口——
    swap_install 挂点不吞 JournalError(与 executor 不同),run_swap 裸调若无
    CLI 层防护即以 traceback 逃逸(本用例将直接报错而非断言失败)。
    """
    from migration import cli
    from migration.journal import JobJournal, JournalError

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    dst_dir = game_root / "versions" / "dst"
    dst_dir.mkdir()
    (dst_dir / "dst.json").write_text(
        '{"arguments": {"game": ["--fml.neoforgeVersion", "21.1.228"]}}',
        encoding="utf-8")
    (dst_dir / "mods").mkdir()
    pack_mods = tmp_path / "newpack" / "mods"
    pack_mods.mkdir(parents=True)
    (pack_mods / "fresh-1.0.jar").write_bytes(b"F")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["scan", "src", "--game-root", str(game_root)]) == 0
    capsys.readouterr()
    monkeypatch.setattr(cli.Confirm, "ask", lambda *a, **k: True)
    calls = {"n": 0}
    real_append = JobJournal._append

    def _flaky(self, line: dict) -> None:
        if line.get("op") != "start":  # start 行不计次(构造期追加,非记录)
            calls["n"] += 1
        if calls["n"] > 1:  # 首条 intent 落盘后失败(复制完成、完成记录未写)
            self.write_failed = True  # 模拟真实 _append 的 OSError 留痕语义
            raise JournalError("journal 写入失败: disk full (injected)")
        real_append(self, line)

    monkeypatch.setattr(JobJournal, "_append", _flaky)
    rc = cli.main(["swap", "src", "dst", str(pack_mods.parent),
                   "--game-root", str(game_root)])
    captured = capsys.readouterr()
    assert rc == 2
    # 0.12.0 复审#5:装包段内的 journal 失败收敛进 outcome.error——部分结果
    # (已复制 1)随「装包未完成」呈现,不再笼统报「进度未知」
    assert "装包未完成" in captured.out
    assert "已复制 1 个 jar" in captured.out
    assert "journal 写入失败" in captured.out
    assert (dst_dir / "mods" / "fresh-1.0.jar").exists()  # jar 已装属实
    assert "Traceback" not in captured.err                # 无 traceback

    # 构造期失败(start 行即失败,零装包):仍走 CLI 层 JournalError 引导
    def _fail_start(self: JobJournal, line: dict) -> None:
        if line.get("op") == "start":
            raise JournalError("journal 写入失败: disk full (start 注入)")
        real_append(self, line)

    monkeypatch.setattr(JobJournal, "_append", _fail_start)
    for old in (game_root / ".mcmig" / "jobs").glob("*.jsonl"):
        old.unlink()   # 清档:构造期走「新建 start」路径,注入才会命中
    (dst_dir / "mods" / "fresh-1.0.jar").unlink()         # 清场重装
    rc2 = cli.main(["swap", "src", "dst", str(pack_mods.parent),
                    "--game-root", str(game_root)])
    captured2 = capsys.readouterr()
    assert rc2 == 2
    assert "journal 写入失败,装包中止" in captured2.err   # 两行式引导仍在
    assert "重新运行 mcmig swap" in captured2.err
    assert not (dst_dir / "mods" / "fresh-1.0.jar").exists()  # 零装包


def test_cli_swap_fsops_error_friendly(tmp_path: Path, monkeypatch, capsys):
    """修复 A4:swap 装包段 FsOpsError(磁盘不足/目标被占用)→ 中文引导退 2,
    不再 traceback(与 GUI 同路径的三段式防护对称)。"""
    from migration import cli
    from migration.fsops import FsOpsError

    game_root = tmp_path / "game"
    src_dir = game_root / "versions" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "options.txt").write_text("fps:120\n", encoding="utf-8")
    dst_dir = game_root / "versions" / "dst"
    dst_dir.mkdir()
    (dst_dir / "dst.json").write_text(
        '{"arguments": {"game": ["--fml.neoforgeVersion", "21.1.228"]}}',
        encoding="utf-8")
    (dst_dir / "mods").mkdir()
    pack_mods = tmp_path / "newpack" / "mods"
    pack_mods.mkdir(parents=True)
    (pack_mods / "fresh-1.0.jar").write_bytes(b"F")
    monkeypatch.chdir(tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise FsOpsError("mods/fresh-1.0.jar", "目标文件被占用(注入)")

    monkeypatch.setattr(cli, "run_swap", _boom)
    rc = cli.main(["swap", "src", "dst", str(pack_mods.parent),
                   "--game-root", str(game_root)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "装包文件操作失败" in captured.err
    assert "目标文件被占用(注入)" in captured.err
    assert "重新运行 mcmig swap" in captured.err


def test_serve_until_shutdown_stops_on_flag() -> None:
    """修复 A5:浏览器模式主循环消费 shutdown_requested——页面「退出服务」
    置位后引导 uvicorn 停机(should_exit)并等线程收尾;线程自然结束也返回。"""
    import threading
    import time

    from migration import cli

    class _State:
        def __init__(self) -> None:
            self.shutdown_requested = False

    class _App:
        def __init__(self) -> None:
            self.state = _State()

    class _Server:
        def __init__(self) -> None:
            self.should_exit = False

    app, server = _App(), _Server()

    def _run() -> None:
        # 模拟 uvicorn 服务循环:should_exit 置位即收尾
        while not server.should_exit:
            time.sleep(0.02)

    worker = threading.Thread(target=_run)
    worker.start()
    app.state.shutdown_requested = True       # 页面已点「退出服务」
    cli._serve_until_shutdown(app, server, worker)
    assert server.should_exit is True
    assert not worker.is_alive()

    # 线程自然结束(未置位)也立即返回,不空转
    app2, server2 = _App(), _Server()
    finished = threading.Thread(target=lambda: None)
    finished.start()
    finished.join()
    cli._serve_until_shutdown(app2, server2, finished)
    assert server2.should_exit is False


def test_cmd_gui_reports_server_startup_failure(tmp_path, monkeypatch, capsys):
    """评审 I2:服务线程内 uvicorn 启动失败(以 sys.exit 报告,如端口占用)会被
    threading 静默吞掉——不捕获则 _cmd_gui 返回 0 谎报成功(修复前主线程
    uvicorn.run 会以 3 退出);现捕获后打印中文错误退 2。"""
    import sys as _sys
    import types as _types

    import migration.workdir as wd

    from migration import cli

    monkeypatch.setattr(wd, "_is_frozen", lambda: False)
    monkeypatch.chdir(tmp_path)
    game = tmp_path / "game"
    (game / "versions").mkdir(parents=True)
    w = wd.resolve_workdir(game_root=game)
    w.save_game_root(game)
    monkeypatch.setattr(cli.doctor, "verify_data_manifest", lambda: [])

    class _FakeServer:
        """替身:uvicorn.Server 的启动失败形态(STARTUP_FAILURE=sys.exit(3))。"""

        def __init__(self, _config: object) -> None:
            self.should_exit = False

        def run(self) -> None:
            raise SystemExit(3)

    fake_uvicorn = _types.SimpleNamespace(
        Server=_FakeServer, Config=lambda app, **kw: kw)
    monkeypatch.setitem(_sys.modules, "uvicorn", fake_uvicorn)

    rc = cli.main(["gui", "--no-browser", "--port", "0"])
    captured = capsys.readouterr()
    assert rc == 2                                            # 修复前:0(谎报成功)
    assert "本地服务异常退出" in captured.err
    assert "端口" in captured.err


# ---- Task 8:mcmig update [--check](spec §4.3.3) ----


def _fake_release(tag: str):
    """最小 ReleaseInfo(无资产;--check 分支不消费资产)。"""
    from migration import updater

    return updater.ReleaseInfo(tag, ())


def test_update_check_reports_newer(capsys, monkeypatch):
    """--check:发现新版打印版本并退 0(不下载)。"""
    from migration import updater

    monkeypatch.setattr(updater, "fetch_latest_release", lambda: _fake_release("v0.13.1"))
    import migration

    monkeypatch.setattr(migration, "__version__", "0.12.0")
    rc = cli.main(["update", "--check"])
    assert rc == 0 and "0.13.1" in capsys.readouterr().out


def test_update_check_latest(capsys, monkeypatch):
    from migration import updater

    monkeypatch.setattr(updater, "fetch_latest_release", lambda: _fake_release("v0.12.0"))
    import migration

    monkeypatch.setattr(migration, "__version__", "0.12.0")
    rc = cli.main(["update", "--check"])
    assert rc == 0 and "已是最新" in capsys.readouterr().out


def test_update_source_mode_download_gives_git_pull(capsys, monkeypatch):
    """源码模式:--check 可用;不带 --check 时给 git pull 指引而非下载(退 0)。"""
    from migration import _form, updater

    monkeypatch.setattr(_form, "FORM", "source")
    monkeypatch.setattr(updater, "fetch_latest_release", lambda: _fake_release("v9.9.9"))
    import migration

    monkeypatch.setattr(migration, "__version__", "0.12.0")
    rc = cli.main(["update"])
    assert rc == 0 and "git pull" in capsys.readouterr().out


def test_update_frozen_download_flow(capsys, monkeypatch, tmp_path):
    """冻结 onefile 全流程:暂存落 exe/data/update-staging/<uuid>/,输出三步替换指引。"""
    import sys

    from migration import _form, updater

    monkeypatch.setattr(_form, "FORM", "onefile")
    exe = tmp_path / "app" / "mcmig.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    payload = b"new-binary"

    def _fake_plan(root, *, progress_cb=None, should_cancel=None):
        sub = root / "uuid-1"
        sub.mkdir(parents=True)
        p = sub / "mcmig-gui-0.13.1-win-x64.exe"
        p.write_bytes(payload)
        return updater.UpdatePlan("0.13.1", p.name, p, len(payload), "ab" * 32)

    monkeypatch.setattr(updater, "plan_update", _fake_plan)
    rc = cli.main(["update"])
    out = capsys.readouterr().out
    assert rc == 0
    staged = exe.parent / "data" / "update-staging" / "uuid-1" / "mcmig-gui-0.13.1-win-x64.exe"
    assert staged.read_bytes() == payload
    assert "替换" in out and "重新启动" in out   # 三步指引要素
    assert "mcmig-gui" in out or ".exe" in out   # 评审④ P2-2:onefile 指引明确替换现有 exe


def test_update_onedir_steps_mention_unzip(capsys, monkeypatch, tmp_path):
    """评审④ P2-2:onedir 指引必须给「解压覆盖」——下载物是含顶层 mcmig/ 的
    zip,「替换到 mcmig 所在目录」不足以完成更新(旧指引照做只是放入 zip)。"""
    import sys

    from migration import _form, updater

    monkeypatch.setattr(_form, "FORM", "onedir")
    exe = tmp_path / "app" / "mcmig.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    def _fake_plan(root, *, progress_cb=None, should_cancel=None):
        sub = root / "uuid-1"
        sub.mkdir(parents=True)
        p = sub / "mcmig-0.13.1-win-x64.zip"
        p.write_bytes(b"zip")
        return updater.UpdatePlan("0.13.1", p.name, p, 3, "ab" * 32)

    monkeypatch.setattr(updater, "plan_update", _fake_plan)
    rc = cli.main(["update"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "解压" in out and "config.toml" in out and "重新启动" in out


def test_update_check_proxy_html_no_traceback(capsys, monkeypatch):
    """评审④ P2-6:代理返回 HTML(200)→ 三段式 rc2,不裸 traceback。"""
    import httpx

    from migration import updater

    monkeypatch.setattr(
        updater, "_open_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, content=b"<html>login</html>",
                                           headers={"content-type": "text/html"})),
            follow_redirects=True))
    rc = cli.main(["update", "--check"])
    out = capsys.readouterr().out
    assert rc == 2 and "[错误]" in out and "Traceback" not in out


def test_setup_logging_silences_httpx_info():
    """K3 真机手测:httpx 每次请求的 INFO 日志(「HTTP Request: GET …」)不得
    混入玩家输出——CLI 根日志为 INFO 时,httpx 仍须保持 WARNING。"""
    import logging

    root = logging.getLogger()
    httpx_logger = logging.getLogger("httpx")
    old_root, old_httpx = root.level, httpx_logger.level
    try:
        root.setLevel(logging.INFO)          # 模拟 CLI 默认(会放行 httpx 的 INFO)
        httpx_logger.setLevel(logging.NOTSET)
        cli._setup_logging(quiet=False)
        assert httpx_logger.getEffectiveLevel() >= logging.WARNING
    finally:
        root.setLevel(old_root)
        httpx_logger.setLevel(old_httpx)


def test_cmd_gui_fatal_callback_reports_workdir_reason(monkeypatch):
    """评审④ P2-4:_cmd_gui 致命分支经 on_fatal 回调结构化 what/why——
    降级壳据此弹真实原因(只读目录→移动指引),而非通用 doctor 文案。"""
    from migration.workdir import WorkdirError

    ns = cli.build_parser().parse_args(["gui"])

    def _boom():
        raise WorkdirError("软件目录不可写", "请把 mcmig 移动到可写的文件夹后重试")

    monkeypatch.setattr(cli, "resolve_workdir", _boom)
    seen: list[tuple[str, str]] = []
    rc = cli._cmd_gui(ns, on_fatal=lambda w, y: seen.append((w, y)))
    assert rc == 2
    assert seen == [("软件目录不可写", "请把 mcmig 移动到可写的文件夹后重试")]


def test_update_failure_exit_2(capsys, monkeypatch):
    from migration import updater

    def _boom():
        raise updater.UpdateError("更新检查失败", "网络不可达")

    monkeypatch.setattr(updater, "fetch_latest_release", _boom)
    rc = cli.main(["update", "--check"])
    assert rc == 2 and "网络不可达" in capsys.readouterr().out


def test_cmd_swap_passes_progress_cb_unless_dry_run(tmp_path, monkeypatch, capsys):
    """T13.5:装包进度回调接线——非 dry-run 传 callable,dry-run 传 None。"""
    from migration.fsops import FsOpsError

    game_root = tmp_path / "game"
    dst_dir = game_root / "versions" / "dst"
    dst_dir.mkdir(parents=True)
    (dst_dir / "dst.json").write_text(
        '{"arguments": {"game": ["--fml.neoforgeVersion", "21.1.228"]}}',
        encoding="utf-8")
    (dst_dir / "mods").mkdir()
    src_dir = game_root / "versions" / "src"
    src_dir.mkdir(parents=True)
    pack_mods = tmp_path / "newpack" / "mods"
    pack_mods.mkdir(parents=True)
    (pack_mods / "fresh-1.0.jar").write_bytes(b"F")
    monkeypatch.chdir(tmp_path)
    captured: dict = {}

    def _capture(*_a: object, **k: object) -> None:
        captured.update(k)
        raise FsOpsError("mods/fresh-1.0.jar", "注入中止")

    monkeypatch.setattr(cli, "run_swap", _capture)
    rc = cli.main(["swap", "src", "dst", str(pack_mods.parent),
                   "--game-root", str(game_root)])
    assert rc == 2 and callable(captured.get("progress_cb"))
    captured.clear()
    rc = cli.main(["swap", "src", "dst", str(pack_mods.parent),
                   "--game-root", str(game_root), "--dry-run"])
    assert rc == 2 and captured.get("progress_cb") is None


def test_gui_window_forwards_port_and_no_browser(monkeypatch):
    """T13.5-5:--window 不得丢 --port/--no-browser(此前 window_main() 裸调)。"""
    seen: dict = {}
    import migration.gui.app as gui_app

    monkeypatch.setattr(gui_app, "main", lambda argv: seen.update(argv=argv) or 0)
    rc = cli.main(["gui", "--window", "--port", "8123", "--no-browser"])
    assert rc == 0
    assert "--port" in seen["argv"] and "8123" in seen["argv"] and "--no-browser" in seen["argv"]
