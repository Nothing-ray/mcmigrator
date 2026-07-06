"""分层哈希策略:文本全量 MD5、mods/bulk 走 size 代理。"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1 << 16  # 64 KiB 流式读取块

# bulk 整体替换型二进制:走 size 代理,不哈希
_BULK_EXTS = {".sqlite", ".zip", ".mca"}


def should_hash(path: Path, *, strict: bool = False) -> bool:
    """判断某文件是否需要计算 MD5(分层策略)。

    Args:
        path: 文件绝对/相对路径。
        strict: 为 True 时强制全量哈希(忽略分层)。

    Returns:
        True 表示该文件应计算 MD5;False 表示走 size 代理(mods jar / bulk)。
    """
    if strict:
        return True
    suffix = path.suffix.lower()
    # mods jar:玩家不改内部,按文件名集合比,不哈希(大小写不敏感:Mods/、MODS/ 也算)
    if suffix == ".jar" and any(p.lower() == "mods" for p in path.parts):
        return False
    # bulk 二进制(sqlite/zip/mca):整体替换型,size 是好代理
    if suffix in _BULK_EXTS:
        return False
    return True


def compute_md5(path: Path) -> str:
    """流式全量计算文件 MD5。"""
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()
