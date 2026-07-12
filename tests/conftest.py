"""共享 fixture:程序化构建 mini 版本目录(固定内容→可断言 MD5)。"""

from __future__ import annotations

import zipfile
from pathlib import Path

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


@pytest.fixture
def origin_registry_snapshot():
    """快照/还原 ORIGIN_REGISTRY,隔离 register_origin 写入对其他测试的污染。"""
    from migration.plan import ORIGIN_REGISTRY

    snapshot = dict(ORIGIN_REGISTRY)
    yield
    ORIGIN_REGISTRY.clear()
    ORIGIN_REGISTRY.update(snapshot)
