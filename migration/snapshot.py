"""快照数据模型与 JSON 持久化。

快照只存原始清单(无分类),分类在读快照→出报告时按当前规则现算。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

TOOL_VERSION = "0.1.0"
SNAPSHOT_FORMAT = 1


class SnapshotFormatError(Exception):
    """快照格式版本不支持或文件损坏。"""


@dataclass(frozen=True)
class FileEntry:
    """相对版本根的一个文件条目。md5 为 None 表示分层策略未哈希。"""

    path: str
    size: int
    md5: str | None


@dataclass
class Snapshot:
    """一个版本文件夹的扫描快照(原始清单)。"""

    version: str
    game_root: str
    scanned_at: str
    hash_mode: str  # "tiered" | "strict"
    file_count: int
    files: list[FileEntry]
    tool_version: str = TOOL_VERSION
    snapshot_format: int = SNAPSHOT_FORMAT

    def save(self, path: Path) -> None:
        """将快照写入 JSON(自动创建父目录)。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tool_version": self.tool_version,
            "snapshot_format": self.snapshot_format,
            "version": self.version,
            "game_root": self.game_root,
            "scanned_at": self.scanned_at,
            "hash_mode": self.hash_mode,
            "file_count": self.file_count,
            "files": [asdict(f) for f in self.files],
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Path) -> "Snapshot":
        """从 JSON 读快照;格式版本不支持或字段缺失/损坏时抛 SnapshotFormatError。"""
        with path.open("r", encoding="utf-8") as f:
            try:
                payload = json.load(f)
            except json.JSONDecodeError as e:
                raise SnapshotFormatError(f"快照 JSON 解析失败: {e}") from e
        if not isinstance(payload, dict):
            raise SnapshotFormatError(f"快照顶层非对象: {type(payload).__name__}")
        fmt = payload.get("snapshot_format")
        if fmt != SNAPSHOT_FORMAT:
            raise SnapshotFormatError(
                f"快照格式版本 {fmt} 不支持(当前 {SNAPSHOT_FORMAT}),请重新 scan"
            )
        try:
            files = [
                FileEntry(path=d["path"], size=d["size"], md5=d.get("md5"))
                for d in payload["files"]
            ]
            return cls(
                version=payload["version"],
                game_root=payload["game_root"],
                scanned_at=payload["scanned_at"],
                hash_mode=payload["hash_mode"],
                file_count=payload["file_count"],
                files=files,
            )
        except (KeyError, TypeError) as e:
            raise SnapshotFormatError(f"快照内容字段缺失或类型错误: {e}") from e


def snapshot_path(workdir: Path, version: str) -> Path:
    """返回某版本快照的标准路径:<workdir>/.mcmig/snapshots/<ver>.snapshot.json。"""
    return workdir / ".mcmig" / "snapshots" / f"{version}.snapshot.json"
