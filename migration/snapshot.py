"""快照数据模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FileEntry:
    """相对版本根的一个文件条目。"""

    path: str
    size: int
    md5: str | None
