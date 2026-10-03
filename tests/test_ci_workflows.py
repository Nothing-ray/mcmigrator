"""CI workflow 结构测试:安装口径分段/三 job/J7 退役锚(spec §4.2)。"""

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]


def test_build_yaml_three_jobs_and_lockfile_build():
    """build.yml:win 测试/linux 测试(flock 真跑)/win 锁定构建;构建段只用锁定文件。"""
    doc = yaml.safe_load((_ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8"))
    jobs = doc["jobs"]
    assert set(jobs) == {"test-windows", "test-linux", "build-windows"}
    # 触发面:push/PR/手动(spec §4.2.1;yaml 1.1 把裸 on 解析为 True,兼容取法)
    on = doc.get("on") or doc.get(True)
    assert {"push", "pull_request", "workflow_dispatch"} <= set(on)
    # 测试 job:可编辑安装(dev,gui)——ruff+pytest
    test_steps = "\n".join(
        s.get("run", "") for s in jobs["test-windows"]["steps"]
    )
    assert 'pip install -e ".[dev,gui]"' in test_steps
    assert "ruff check migration/ tests/" in test_steps
    assert "pytest tests/" in test_steps
    # 构建 job:只装锁定文件(发行产物不在宽松解析环境产生,v3 统一口径),且须 needs 测试
    build_steps = "\n".join(
        s.get("run", "") for s in jobs["build-windows"]["steps"]
    )
    assert "pip install -r tools/packaging/requirements-win-build.txt" in build_steps
    assert "-e " not in build_steps
    assert jobs["build-windows"]["needs"] == ["test-windows"]
    assert "--form onefile" in build_steps and "--form onedir" in build_steps
    # linux job 也跑 ruff+pytest(J7:POSIX flock 用例真跑)
    linux_steps = "\n".join(
        s.get("run", "") for s in jobs["test-linux"]["steps"]
    )
    assert "ruff check migration/ tests/" in linux_steps and "pytest tests/" in linux_steps


def test_release_yaml_guard_and_sums():
    """release.yml:守卫先行+锁定构建+SUMS+gh release(三资产)。"""
    doc = yaml.safe_load((_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    jobs = doc["jobs"]
    assert {"check-windows", "check-linux", "release"} <= set(jobs)
    assert jobs["release"]["needs"] == ["check-windows", "check-linux"]
    assert jobs["release"]["permissions"] == {"contents": "write"}
    steps = "\n".join(s.get("run", "") for s in jobs["release"]["steps"])
    assert "check_release.py" in steps
    # 终审 I2:spec §8.7——Release 页须标注「自动应用更新随后续版本提供…」
    body = jobs["release"]["steps"][-1]["with"]["body"]
    assert "手动替换" in body and "自动应用" in body
    assert "pip install -r tools/packaging/requirements-win-build.txt" in steps
    assert "--sums" in steps
    check_steps = "\n".join(s.get("run", "") for s in jobs["check-windows"]["steps"])
    assert "ruff check migration/ tests/" in check_steps and "pytest tests/" in check_steps


def test_release_check_jobs_cover_both_platforms():
    """spec §4.2.2「同 build 全检」:release 的 check 须含 Linux(POSIX flock 真跑)。"""
    doc = yaml.safe_load((_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    jobs = doc["jobs"]
    assert {"check-windows", "check-linux", "release"} <= set(jobs)
    assert jobs["release"]["needs"] == ["check-windows", "check-linux"]
    linux_steps = "\n".join(s.get("run", "") for s in jobs["check-linux"]["steps"])
    assert "pytest tests/" in linux_steps and "ruff check migration/ tests/" in linux_steps
