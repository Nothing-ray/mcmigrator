"""版本目录扫描器:遍历目录生成分层哈希的 FileEntry 清单。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import hashing
from .snapshot import FileEntry, Snapshot

log = logging.getLogger(__name__)


@dataclass
class ScanError:
    """单个文件扫描失败记录。"""

    path: str
    reason: str


def detect_world_dirs(entries: list[FileEntry], properties_text: bytes | None) -> list[str]:
    """探测世界目录(F34①):server.properties level-name + 顶层含 level.dat 的目录。

    ① level-name 指向目录(单段安全名且在文件清单中存在)——活跃世界;
    ② 顶层直接包含 level.dat 的目录——覆盖任意命名/改名留存/多世界形态;
    并集去重升序。properties_text=None(缺文件/不可读)仅走②。
    客户端版本文件夹世界在 saves/<名>/ 二层,①②均不误触。

    Args:
        entries: 扫描所得文件条目(相对路径,正斜杠)。
        properties_text: server.properties 原始字节;None 表示不可用。

    Returns:
        世界目录名列表(升序);无探测结果为空列表。
    """
    dirs: set[str] = set()
    if properties_text is not None:
        from .textcompare import parse_properties

        name = (parse_properties(properties_text) or {}).get("level-name", "")
        # 单段安全名:非空、无路径分隔、非点号段,且目录确实在清单中
        if (name and "/" not in name and "\\" not in name
                and name not in (".", "..")
                and any(e.path.startswith(name + "/") for e in entries)):
            dirs.add(name)
    for e in entries:
        top, sep, rest = e.path.partition("/")
        if sep and rest == "level.dat":
            dirs.add(top)
    return sorted(dirs)


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
                st = p.stat()
            except OSError as e:
                errors.append(ScanError(rel, f"{rel} stat 失败: {e}"))
                continue
            size = st.st_size
            md5: str | None = None
            mtime: int | None = None
            if hashing.should_hash(p, strict=self.strict):
                try:
                    md5 = hashing.compute_md5(p)
                except OSError as e:
                    errors.append(ScanError(rel, f"{rel} 读取失败: {e}"))
                    continue
            else:
                # F35:未哈希文件(bulk/mods)记 mtime——同尺寸重写的演化信号
                mtime = int(st.st_mtime)
            entries.append(FileEntry(path=rel, size=size, md5=md5, mtime=mtime))
        return entries, errors

    def build_snapshot(self, game_root: str) -> tuple[Snapshot, list[ScanError]]:
        """扫描并构造 Snapshot 对象。"""
        entries, errors = self.scan()
        # F34①:世界目录探测(server.properties 优先,顶层 level.dat 兜底)
        try:
            properties_text = (self.version_dir / "server.properties").read_bytes()
        except OSError:
            properties_text = None
        world_dirs = detect_world_dirs(entries, properties_text)
        snap = Snapshot(
            version=self.version_name,
            game_root=game_root,
            scanned_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            hash_mode="strict" if self.strict else "tiered",
            file_count=len(entries),
            files=entries,
            resolved_root=str(self.version_dir.resolve()),
            world_dirs=world_dirs,
        )
        return snap, errors
