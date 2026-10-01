"""快照数据模型与 JSON 持久化。

快照只存原始清单(无分类),分类在读快照→出报告时按当前规则现算。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .fsops import write_json_atomic

TOOL_VERSION = "0.6.0"
SNAPSHOT_FORMAT = 2


class SnapshotFormatError(Exception):
    """快照格式版本不支持或文件损坏。"""


@dataclass(frozen=True)
class FileEntry:
    """相对版本根的一个文件条目。md5 为 None 表示分层策略未哈希;
    mtime 为未哈希文件的修改时刻(epoch 秒,F35 演化检出用),哈希文件为 None。"""

    path: str
    size: int
    md5: str | None
    mtime: int | None = None


@dataclass
class Snapshot:
    """一个版本文件夹的扫描快照(原始清单)。"""

    version: str
    game_root: str
    scanned_at: str
    hash_mode: str  # "tiered" | "strict"
    file_count: int
    files: list[FileEntry]
    resolved_root: str | None = None  # scan 时版本目录 resolve() 结果,自比对/junction 同体判定用;旧快照缺省 None
    world_dirs: list[str] = field(default_factory=list)  # F34①:scan 探测的世界目录(level-name/level.dat)
    # 批次H(v2):内嵌 mod 名册;键=modid/version/jar_filename/neoforge_range/embedded_in;
    # load 容错见 Snapshot.load docstring(v1 快照无此键 → [])
    mods: list[dict] = field(default_factory=list)
    tool_version: str = TOOL_VERSION
    snapshot_format: int = SNAPSHOT_FORMAT

    def save(self, path: Path) -> None:
        """将快照写入 JSON(原子写:tmp+replace,自动创建父目录,失败不留半截文件)。"""
        payload = {
            "tool_version": self.tool_version,
            "snapshot_format": self.snapshot_format,
            "version": self.version,
            "game_root": self.game_root,
            "scanned_at": self.scanned_at,
            "resolved_root": self.resolved_root,
            "world_dirs": self.world_dirs,
            "hash_mode": self.hash_mode,
            "file_count": self.file_count,
            "files": [asdict(f) for f in self.files],
            "mods": self.mods,
        }
        write_json_atomic(path, payload)

    @classmethod
    def load(cls, path: Path) -> "Snapshot":
        """从 JSON 读快照;格式版本不支持或字段缺失/损坏时抛 SnapshotFormatError。

        版本闸门接受 v1(历史快照)与当前 v2;mods 容错与 world_dirs 同哲学:
        键缺失(v1)→ [];整体非 list → [](结构级降级);元素非 dict 或缺
        modid/jar_filename → 跳过该条(数据级宽松,不因单条损坏弃整个快照)。
        """
        with path.open("r", encoding="utf-8") as f:
            try:
                payload = json.load(f)
            except json.JSONDecodeError as e:
                raise SnapshotFormatError(f"快照 JSON 解析失败: {e}") from e
        if not isinstance(payload, dict):
            raise SnapshotFormatError(f"快照顶层非对象: {type(payload).__name__}")
        fmt = payload.get("snapshot_format")
        if fmt not in (1, SNAPSHOT_FORMAT):
            raise SnapshotFormatError(
                f"快照格式版本 {fmt} 不支持(当前 {SNAPSHOT_FORMAT}),请重新 scan"
            )
        try:
            files = [
                FileEntry(
                    path=d["path"], size=d["size"], md5=d.get("md5"),
                    mtime=m if isinstance(m := d.get("mtime"), int) else None,
                )
                for d in payload["files"]
            ]
            return cls(
                version=payload["version"],
                game_root=payload["game_root"],
                scanned_at=payload["scanned_at"],
                hash_mode=payload["hash_mode"],
                file_count=payload["file_count"],
                files=files,
                resolved_root=payload.get("resolved_root"),
                world_dirs=(
                    [w for w in payload.get("world_dirs") if isinstance(w, str)]
                    if isinstance(payload.get("world_dirs"), list) else []
                ),
                mods=(
                    [m for m in payload.get("mods", [])
                     if isinstance(m, dict) and m.get("modid") and m.get("jar_filename")]
                    if isinstance(payload.get("mods", []), list) else []
                ),
            )
        except (KeyError, TypeError) as e:
            raise SnapshotFormatError(f"快照内容字段缺失或类型错误: {e}") from e


def snapshot_path(workdir: Path, version: str) -> Path:
    """返回某版本快照的标准路径:<workdir>/.mcmig/snapshots/<ver>.snapshot.json。"""
    return workdir / ".mcmig" / "snapshots" / f"{version}.snapshot.json"
