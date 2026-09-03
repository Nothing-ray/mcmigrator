"""doctor 模块测试:manifest 校验 + 综合体检。"""

from migration import doctor


def test_verify_manifest_ok():
    assert doctor.verify_data_manifest() == []


def test_verify_manifest_detects_tamper():
    """篡改检测(brief 修正版):临时改写 rebuild.yaml 字节,finally 还原。

    不用 monkeypatch Path.read_bytes(brief 原文写法脆弱),直接
    读原文→改写→try/finally 还原,断言 findings 非空且含文件名。
    """
    real = doctor._data_dir() / "rebuild.yaml"
    backup = real.read_bytes()
    real.write_bytes(b"tampered")
    try:
        findings = doctor.verify_data_manifest()
    finally:
        real.write_bytes(backup)
    assert findings, "篡改后应产生校验发现"
    assert any("rebuild.yaml" in line for line in findings)


def test_verify_manifest_missing_manifest(tmp_path, monkeypatch):
    """manifest 本身缺失时返回单条「清单文件缺失」。"""
    # 空临时目录充当数据目录:无 manifest.sha256 → 单条缺失行
    monkeypatch.setattr(doctor, "_data_dir", lambda: tmp_path)
    findings = doctor.verify_data_manifest()
    assert len(findings) == 1
    assert "缺失" in findings[0]


def test_run_doctor_green_mode(tmp_path, monkeypatch):
    import migration.workdir as wd
    monkeypatch.setattr(wd, "_is_frozen", lambda: True)
    monkeypatch.setattr(wd, "_exe_dir", lambda: tmp_path)
    game = tmp_path / "game"
    (game / "versions" / "v1").mkdir(parents=True)
    w = wd.resolve_workdir(game_root=game)
    w.save_game_root(game)
    ok, lines = doctor.run_doctor(workdir=w)
    assert ok is True
    assert len(lines) >= 4


def test_run_doctor_missing_game_root_fails(tmp_path, monkeypatch):
    import migration.workdir as wd
    monkeypatch.setattr(wd, "_is_frozen", lambda: True)
    monkeypatch.setattr(wd, "_exe_dir", lambda: tmp_path)
    w = wd.resolve_workdir(game_root=tmp_path / "ghost-game")
    w.save_game_root(tmp_path / "ghost-game")
    ok, lines = doctor.run_doctor(workdir=w)
    assert ok is False
    assert any("❌" in line for line in lines)


def test_run_doctor_compat_mode_without_config(tmp_path, monkeypatch):
    """兼容模式(非 frozen、无 config)可用:game_root 未配置 → 该项 ❌ 但不崩。"""
    import migration.workdir as wd
    monkeypatch.setattr(wd, "_is_frozen", lambda: False)
    monkeypatch.chdir(tmp_path)  # 无 .mcmig/config.yaml
    ok, lines = doctor.run_doctor()
    assert ok is False
    assert any("❌" in line and "游戏根目录" in line for line in lines)
    # 其余检查照常给出结论(数据完整性/可写/磁盘等)
    assert len(lines) >= 4


def test_cli_doctor_green_exits_zero(tmp_path, monkeypatch, capsys):
    """CLI doctor:全绿退出 0(绿色模式,game_root 已配置且存在)。"""
    import migration.workdir as wd
    from migration import cli
    monkeypatch.setattr(wd, "_is_frozen", lambda: True)
    monkeypatch.setattr(wd, "_exe_dir", lambda: tmp_path)
    game = tmp_path / "game"
    (game / "versions" / "v1").mkdir(parents=True)
    w = wd.resolve_workdir(game_root=game)
    w.save_game_root(game)
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "✅" in out
    assert "❌" not in out


def test_cli_doctor_red_exits_one(tmp_path, monkeypatch, capsys):
    """CLI doctor:有 ❌ 退出 1(兼容模式未配置 game_root)。"""
    import migration.workdir as wd
    from migration import cli
    monkeypatch.setattr(wd, "_is_frozen", lambda: False)
    monkeypatch.chdir(tmp_path)  # 无 .mcmig/config.yaml
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "❌" in out
