"""版本目录扫描器:遍历目录生成分层哈希的 FileEntry 清单。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import hashing
from .snapshot import FileEntry, Snapshot

log = logging.getLogger(__name__)


@dataclass
class ScanError:
    """单个文件扫描失败记录。"""

    path: str
    reason: str


class Scanner:
    """遍历一个版本文件夹,产出 FileEntry 清单(分层哈希)。"""

    def __init__(self, version_dir: Path, version_name: str, *, strict: bool = False) -> None:
        self.version_dir = version_dir
        self.version_name = version_name
        self.strict = strict

    def scan(self) -> tuple[list[FileEntry], list[ScanError]]:
        """扫描目录,返回 (文件清单, 错误列表)。失败文件跳过且不致全崩。"""
        entries: list[FileEntry] = []
        errors: list[ScanError] = []
        for p in sorted(self.version_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.version_dir).as_posix()
            try:
                size = p.stat().st_size
            except OSError as e:
                errors.append(ScanError(rel, f"{rel} stat 失败: {e}"))
                continue
            md5: str | None = None
            if hashing.should_hash(p, strict=self.strict):
                try:
                    md5 = hashing.compute_md5(p)
                except OSError as e:
                    errors.append(ScanError(rel, f"{rel} 读取失败: {e}"))
                    continue
            entries.append(FileEntry(path=rel, size=size, md5=md5))
        return entries, errors

    def build_snapshot(self, game_root: str) -> tuple[Snapshot, list[ScanError]]:
        """扫描并构造 Snapshot 对象。"""
        entries, errors = self.scan()
        snap = Snapshot(
            version=self.version_name,
            game_root=game_root,
            scanned_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            hash_mode="strict" if self.strict else "tiered",
            file_count=len(entries),
            files=entries,
        )
        return snap, errors
