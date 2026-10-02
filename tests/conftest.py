"""共享 fixture:程序化构建 mini 版本目录(固定内容→可断言 MD5)。"""

from __future__ import annotations

import os
import sys
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

OPTS = "version:I am a config\n"  # 固定内容


def write_mod_jar(path: Path, modid: str, version: str = "1.0") -> None:
    """写一个含 META-INF/neoforge.mods.toml 的有效 jar(zip),供 scan_mods 解析。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    toml = (
        f'modLoader="javafml"\nloaderVersion="[1,)"\n'
        f'[[mods]]\nmodId="{modid}"\nversion="{version}"\n'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", toml)


def build_mini_version(
    root: Path,
    *,
    variant_b: bool = False,
    bak_files: list[str] | None = None,
    whitelist_files: list[str] | None = None,
) -> Path:
    """构建一个迷你版本文件夹,返回其路径。

    Args:
        variant_b: 做改动用于 diff。
        bak_files: 要创建的 .bak 文件相对路径列表(模拟玩家改过的 config)。
        whitelist_files: 要创建的白名单文件相对路径列表(无 .bak 的玩家偏好)。
    """
    root.mkdir(parents=True, exist_ok=True)
    # 必迁类
    (root / "options.txt").write_text(OPTS, encoding="utf-8")
    (root / "servers.dat").write_bytes(b"\x0a\x00\x00")
    (root / "saves" / "world1").mkdir(parents=True, exist_ok=True)
    (root / "saves" / "world1" / "level.dat").write_bytes(b"\x00")
    # 不迁类
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / "latest.log").write_text("noise", encoding="utf-8")
    (root / "crash-reports").mkdir(exist_ok=True)
    (root / "crash-reports" / "c1.txt").write_text("boom", encoding="utf-8")
    # 未知类(config)
    (root / "config").mkdir(exist_ok=True)
    cfg = "edited=true\n" if variant_b else "edited=false\n"
    (root / "config" / "create.toml").write_text(cfg, encoding="utf-8")
    # mods jar(有效 zip,含 mods.toml,供 scan_mods 解析)
    (root / "mods").mkdir(exist_ok=True)
    write_mod_jar(root / "mods" / "create.jar", "create")
    if variant_b:
        write_mod_jar(root / "mods" / "extra.jar", "extra")  # b 版额外 mod
    # bulk size 代理
    (root / "Distant_Horizons_server_data").mkdir(exist_ok=True)
    (root / "Distant_Horizons_server_data" / "lod.sqlite").write_bytes(b"\x00" * 16)
    # 命中 **/cache/**
    (root / "xaero" / "cache").mkdir(parents=True)
    (root / "xaero" / "cache" / "c.zip").write_bytes(b"\x00")
    for bak_rel in bak_files or []:
        p = root / bak_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00")
    for wl_rel in whitelist_files or []:
        p = root / wl_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
    return root


@pytest.fixture
def mini_version(tmp_path: Path) -> Path:
    return build_mini_version(tmp_path / "mini")


@pytest.fixture
def mini_version_b(tmp_path: Path) -> Path:
    return build_mini_version(tmp_path / "mini_b", variant_b=True)


@pytest.fixture
def mini_version_with_bak(tmp_path: Path) -> Path:
    """带 .bak 的 mini(模拟玩家改过 config/create.toml)。"""
    return build_mini_version(
        tmp_path / "mini_bak",
        bak_files=["config/create-1.toml.bak"],
    )


@pytest.fixture
def mini_version_with_whitelist(tmp_path: Path) -> Path:
    """带白名单文件的 mini(iris.properties + jade preset)。"""
    return build_mini_version(
        tmp_path / "mini_wl",
        whitelist_files=["iris.properties", "config/jade/preset.json"],
    )


@contextmanager
def hold_exclusive(path: Path) -> Iterator[None]:
    """以独占句柄持有文件,模拟运行中游戏占用(供「疑似占用」探测测试)。

    - Windows:普通 open(r+b) 为共享打开(两个句柄可同时持有,无法模拟独占),
      故用 CreateFileW dwShareMode=0 取真独占句柄——探测方的 open(r+b)
      将得 PermissionError;退出时 CloseHandle 释放。
    - 非 Windows:退化为只读属性(chmod 0o444)——open(r+b) 同样报
      PermissionError,属 probe_maybe_running docstring 明示的「权限错误
      误报」路径,语义等价(调用方一律按「疑似占用」呈现)。

    Args:
        path: 待独占持有的文件(须已存在)。
    """
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ]
        generic_read_write = 0xC0000000  # GENERIC_READ | GENERIC_WRITE
        open_existing = 3
        handle = kernel32.CreateFileW(
            str(path), generic_read_write, 0, None, open_existing, 0, None
        )
        if not handle or handle == 0xFFFFFFFFFFFFFFFF:
            raise OSError(f"独占句柄获取失败: {path}")
        try:
            yield
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    else:
        import stat

        os.chmod(path, stat.S_IREAD)
        try:
            yield
        finally:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def build_mini_plan(tmp_path: Path, n_files: int = 3) -> "SimpleNamespace":
    """构建迷你执行计划:n 个 COPY 动作 + 对应源文件(目标为空,零依赖直建)。

    批次I-T6 供 journal/取消检查点用例:文件互不相同(重跑 identical 锚点
    需要逐文件独立内容),计划不经 scan/diff 管线,ActionRecord 手工拼装。

    Args:
        tmp_path: 测试临时目录(src/dst 建在其下)。
        n_files: COPY 动作数(默认 3,brief §T6 journal 用例口径)。

    Returns:
        SimpleNamespace(plan, src, dst):plan.actions 按文件名升序。
    """
    from migration.plan import ActionRecord, Behavior, MigrationPlan, Origin

    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    actions: list[ActionRecord] = []
    for i in range(n_files):
        rel = f"f{i}.txt"
        (src / rel).write_text(f"内容{i}\n", encoding="utf-8")
        actions.append(
            ActionRecord(
                path=rel, behavior=Behavior.COPY, origin=Origin.MUST_MIGRATE,
                src_size=1, dst_size=None, md5_match=None, confidence="high",
                reason="t", backup_target=None,
            )
        )
    plan = MigrationPlan(src="s", dst="d", generated_at="t", actions=actions)
    return SimpleNamespace(plan=plan, src=src, dst=dst)


@pytest.fixture
def mini_plan(tmp_path: Path) -> "SimpleNamespace":
    """journal 用例夹具:3 文件 COPY 计划 + src/dst 目录。"""
    return build_mini_plan(tmp_path)


# 同物别名:executor 取消用例以 mini_plan_dirs 命名(brief §T6 Step 5 口径)
mini_plan_dirs = mini_plan


@pytest.fixture
def origin_registry_snapshot():
    """快照/还原 ORIGIN_REGISTRY,隔离 register_origin 写入对其他测试的污染。"""
    from migration.plan import ORIGIN_REGISTRY

    snapshot = dict(ORIGIN_REGISTRY)
    yield
    ORIGIN_REGISTRY.clear()
    ORIGIN_REGISTRY.update(snapshot)


@pytest.fixture
def built_plan_layout(tmp_path: Path):
    """批次I-T3:两版本 + scan + build_plan 的完整夹具(plan 已签发审阅守卫)。

    布局针对审阅状态校验的三类用例各备一条动作路径(spec §3.3 检测范围分级):
    - options.txt:src 改写为与 dst 不同内容 → must_migrate COPY(已哈希条目,md5 全检);
    - mods/extra.jar:src 独有(variant_b)→ mod_added COPY(「目标不存在」是记录状态);
    - Distant_Horizons_server_data/lod.sqlite:dst 改写为不同长度 → must_migrate COPY
      (size 代理弱检条目,bulk 扩展名不哈希)。
    """
    from migration.pipeline import build_plan, scan_version

    game = tmp_path / "game"
    src_dir = build_mini_version(game / "versions" / "src", variant_b=True)
    dst_dir = build_mini_version(game / "versions" / "dst")
    (src_dir / "options.txt").write_text(OPTS + "fps:120\n", encoding="utf-8")
    (dst_dir / "Distant_Horizons_server_data" / "lod.sqlite").write_bytes(b"\x00" * 32)
    data = game / ".mcmig"
    src_snap = scan_version(game, "src", data / "snapshots")
    dst_snap = scan_version(game, "dst", data / "snapshots")
    plan, _compat, _pairs = build_plan(
        tmp_path, game, "src", "dst",
        mcmig_dir=data, plans_dir=data / "plans", data_dir=data,
    )
    return SimpleNamespace(
        cwd=tmp_path, game=game, data=data,
        src_dir=src_dir, dst_dir=dst_dir,
        src_snap=src_snap, dst_snap=dst_snap,
        snapshot_paths={"src": data / "snapshots" / "src.snapshot.json",
                        "dst": data / "snapshots" / "dst.snapshot.json"},
        rule_sources=[data / "rules.yaml"],
        plan=plan,
    )
