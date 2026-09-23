"""doctor:mcmig 环境体检 — 数据完整性校验 + 运行环境综合检查。

两个能力(GUI 与 CLI ``mcmig doctor`` 复用):

1. ``verify_data_manifest``:对 ``migration/data/`` 下的规则数据文件(*.yaml)
   做 SHA-256 完整性校验。清单由 ``tools/gen_manifest.py`` 生成,随仓库提交、
   随发行包分发,用于发现规则文件被杀毒软件误删、下载损坏或篡改等情况;
2. ``run_doctor``:五项综合体检(数据完整性 / game_root 配置 / 工作目录可写 /
   versions 可读 / 磁盘剩余空间),任一项失败整体 False。
"""

from __future__ import annotations

import hashlib
import importlib.resources
import shutil
from pathlib import Path

from .workdir import WorkDir, WorkdirError, _ensure_writable, resolve_workdir

# 数据清单文件名(与 tools/gen_manifest.py 的约定一致)
MANIFEST_NAME = "manifest.sha256"

# 磁盘剩余空间阈值(字节):1GB,低于该值迁移(存档拷贝)易中途失败
_MIN_FREE_BYTES = 1024 * 1024 * 1024


def _data_dir() -> Path:
    """定位 ``migration/data`` 数据目录。

    用 importlib.resources 定位(而非 ``__file__`` 拼接),源码运行与
    PyInstaller frozen 单文件(资源解包到 ``sys._MEIPASS``)下均有效。

    Returns:
        数据目录路径。
    """
    return Path(str(importlib.resources.files("migration").joinpath("data")))


def _sha256_of(path: Path) -> str:
    """计算文件 SHA-256(hex 小写;CRLF→LF 归一化后哈希,与 tools/gen_manifest.py 同语义(F24))。"""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk.replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def _parse_manifest(text: str) -> dict[str, str]:
    """解析清单文本为 {相对文件名: 期望 sha256hex}。

    Args:
        text: 清单原文(每行 ``<sha256hex>  <相对文件名>``,两个空格分隔)。

    Returns:
        文件名 → 期望哈希映射;空行与格式异常行跳过
        (异常行对应的文件会经「多余」分支兜底暴露)。
    """
    expected: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        digest, sep, name = line.partition("  ")
        # sha256hex 恒为 64 个十六进制字符;不满足即格式异常行
        if sep and name and len(digest) == 64:
            expected[name] = digest.lower()
    return expected


def verify_data_manifest() -> list[str]:
    """校验 ``migration/data/`` 数据文件与清单的一致性。

    Returns:
        中文发现行列表(空列表=完好):

        - 清单文件本身缺失 → 单条「清单文件缺失」;
        - 清单内文件缺失 / 哈希不一致(损坏);
        - 数据目录存在清单外的 ``*.yaml``(多余:清单过期或文件被误放)。
    """
    data_dir = _data_dir()
    manifest = data_dir / MANIFEST_NAME
    if not manifest.is_file():
        return [f"清单文件缺失:{manifest}(请用 tools/gen_manifest.py 重新生成并随包分发)"]
    expected = _parse_manifest(manifest.read_text(encoding="utf-8"))
    findings: list[str] = []
    # ① 清单内每个文件:缺失 / 损坏(哈希不一致)
    for name, want in sorted(expected.items()):
        p = data_dir / name
        if not p.is_file():
            findings.append(f"{name} 缺失")
        elif _sha256_of(p) != want:
            findings.append(f"{name} 损坏(哈希不一致)")
    # ② 数据目录里清单外的 yaml → 多余
    for p in sorted(data_dir.glob("*.yaml")):
        if p.name not in expected:
            findings.append(f"{p.name} 多余(不在清单中)")
    return findings


def _nearest_existing(p: Path) -> Path:
    """返回 p 本身或其最近存在的祖先目录(shutil.disk_usage 要求路径已存在)。"""
    cur = p
    while not cur.exists():
        if cur.parent == cur:
            return cur  # 到盘根仍不存在,交由调用方捕获 OSError
        cur = cur.parent
    return cur


def run_doctor(workdir: WorkDir | None = None) -> tuple[bool, list[str]]:
    """运行五项环境体检。

    检查项:①数据文件完整性 ②game_root 已配置且存在 ③工作目录可写
    ④versions 目录可读 ⑤磁盘剩余空间 > 1GB。

    Args:
        workdir: 工作目录布局;None 时自动解析(frozen→绿色模式,
            源码→兼容模式)。兼容模式下未配置 game_root 只报 ❌ 不崩溃。

    Returns:
        (是否全部通过, 体检行列表);每项一行,格式 ``[✅|❌] 项目: 说明``,
        任一 ❌ 则整体 False。
    """
    lines: list[str] = []
    ok = True

    def add(good: bool, item: str, detail: str) -> None:
        """追加一行体检结论,并累计整体结果。"""
        nonlocal ok
        ok = ok and good
        lines.append(f"{'[✅]' if good else '[❌]'} {item}: {detail}")

    # ① 数据文件完整性(SHA-256 清单校验)
    findings = verify_data_manifest()
    add(not findings, "数据文件完整性", "清单校验通过" if not findings else ";".join(findings))

    # 解析工作目录:显式传入优先;自动解析失败(frozen 未配置/目录不可写)报 ❌ 并结束
    if workdir is not None:
        wd = workdir
    else:
        try:
            wd = resolve_workdir()
        except WorkdirError as e:
            add(False, "工作目录", e.why)
            return ok, lines

    # ② game_root 已配置且存在
    game_root = wd.game_root()
    if game_root is None:
        add(False, "游戏根目录", "未配置(请先保存 game_root 到工作目录配置)")
    elif game_root.is_dir():
        add(True, "游戏根目录", str(game_root))
    else:
        add(False, "游戏根目录", f"已配置但不存在:{game_root}")

    # ③ 工作目录可写(探测文件试写即删;目录不存在时顺手创建)
    try:
        _ensure_writable(wd.root)
        add(True, "工作目录可写", str(wd.root))
    except OSError as e:
        add(False, "工作目录可写", f"{wd.root} 不可写({e})")

    # ④ versions 目录可读(迁移的真正目标,不可读则 scan/plan 全不可用)
    if game_root is None:
        add(False, "versions 目录", "游戏根目录未配置,无法检查")
    else:
        vdir = game_root / "versions"
        if not vdir.is_dir():
            add(False, "versions 目录", f"不存在:{vdir}")
        else:
            try:
                list(vdir.iterdir())
                add(True, "versions 目录", f"可读({vdir})")
            except OSError as e:
                add(False, "versions 目录", f"不可读({e})")

    # ⑤ 磁盘剩余空间 > 1GB(工作目录所在盘;目录不存在则向上取最近存在的祖先)
    try:
        usage = shutil.disk_usage(_nearest_existing(wd.root))
        free_gb = usage.free / (1024**3)
        add(
            usage.free > _MIN_FREE_BYTES,
            "磁盘剩余空间",
            f"{free_gb:.1f}GB 可用(要求 > 1GB)",
        )
    except OSError as e:
        add(False, "磁盘剩余空间", f"无法查询({e})")
    return ok, lines
