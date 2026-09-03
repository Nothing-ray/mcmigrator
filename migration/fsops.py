"""fsops:文件操作统一门(事务式原子复制/原子写/长路径/磁盘检查/类型化异常)。

所有落盘操作集中于此模块,executor/plan/snapshot/cli(装包)统一接入:
- copy_atomic: 事务式复制——冲突备份 → 写 .mcmig-tmp → MD5 校验 → os.replace 原子换名;
  任一步失败删 tmp、必要时从备份回滚目标原件(备份本身保留,绝不删)
- write_json_atomic: JSON 原子写(tmp+replace),避免半截文件损坏快照/计划
- 类型化异常族 FsOpsError(OSError):调用方按失败原因精确提示(GUI 弹窗/CLI 报错),
  且均为 OSError 子类,旧代码 `except OSError` 兼容不破坏
- long_path: win32 绝对路径加 \\\\?\\ 前缀,突破 260 字符限制(整合包路径常超长)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

# 临时文件统一后缀:copy_atomic/write_json_atomic 的中转文件均用此命名,
# 崩溃残留由 clean_stale_tmp 递归清理
TMP_SUFFIX = ".mcmig-tmp"


class FsOpsError(OSError):
    """文件操作失败基类。

    Attributes:
        what: 操作对象(通常为文件路径)。
        why: 中文失败原因(面向用户展示)。
    """

    def __init__(self, what: str, why: str) -> None:
        """初始化类型化异常。

        Args:
            what: 操作对象(通常为文件路径)。
            why: 中文失败原因(面向用户展示)。
        """
        # 只把 why 传给 OSError,保证 str(e) 即用户可读的中文(不被 errno 包装)
        super().__init__(why)
        self.what = what
        self.why = why


class SourceMissingError(FsOpsError):
    """源文件不存在(可能在规划后被移动/删除)。"""


class TargetLockedError(FsOpsError):
    """目标被其他进程占用(典型:游戏未退出,Windows 共享冲突)。"""


class PermissionDeniedError(FsOpsError):
    """无权限读写(目录 ACL/只读属性等)。"""


class DiskSpaceError(FsOpsError):
    """磁盘剩余空间不足。"""


def long_path(p: Path) -> Path:
    """win32 下为绝对路径加 \\\\?\\ 长路径前缀,其余平台/相对路径原样返回。

    Args:
        p: 待转换的路径。

    Returns:
        加前缀后的新路径(win32 绝对路径),或原路径(其余情况)。

    UNC 路径(\\\\server\\share)按 Windows 约定转成 \\\\?\\UNC\\server\\share。
    """
    if os.name != "nt":
        return p
    s = str(p)
    if s.startswith("\\\\?\\"):
        return p  # 已带前缀,勿重复
    if not p.is_absolute():
        return p  # \\?\ 前缀不支持相对路径,相对路径本就受限较短
    if s.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + s.lstrip("\\"))
    return Path("\\\\?\\" + s)


def md5_of(path: Path) -> str | None:
    """分块计算文件 MD5(1MB 块,适配大 mod/存档)。

    Args:
        path: 目标文件。

    Returns:
        16 进制 MD5 字符串;文件不存在/不可读返回 None。
    """
    h = hashlib.md5()
    try:
        with long_path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _map_oserror(e: OSError, phase: str, src: Path, dst: Path) -> FsOpsError:
    """把底层 OSError 映射为类型化异常(中文 why,保留原始异常为 __cause__)。

    Args:
        e: 底层异常。
        phase: 失败阶段("backup"/"copy"/"verify"/"replace")。
        src: 事务源文件。
        dst: 事务目标文件。

    Returns:
        映射后的类型化异常(调用方负责 raise ... from e)。
    """
    if isinstance(e, FsOpsError):
        return e  # 内部已构造好的类型化异常(如 MD5 校验不一致),不二次包装
    if isinstance(e, FileNotFoundError):
        return SourceMissingError(str(src), f"源文件在复制过程中消失: {src}({e})")
    if isinstance(e, PermissionError) and phase == "replace":
        # Windows 共享冲突(errno 13)在换名阶段出现,几乎必然是目标被游戏进程占用
        return TargetLockedError(str(dst), f"目标被占用,无法换名(游戏可能未退出): {dst}({e})")
    if isinstance(e, PermissionError):
        return PermissionDeniedError(str(dst), f"{phase} 阶段无权限: {dst}({e})")
    return FsOpsError(str(dst), f"复制失败({phase} 阶段): {e}")


def _rollback_if_touched(
    dst: Path, backup_dir: Path | None, rel: str, orig_md5: str | None
) -> None:
    """失败回滚:目标原件若在事务中被改动,从备份镜像恢复(备份保留,绝不删)。

    Args:
        dst: 事务目标文件。
        backup_dir: 冲突备份目录(None=无备份可回滚)。
        rel: dst 的相对路径(定位 backup_dir/<rel>)。
        orig_md5: 事务开始时目标的 MD5(None=事务前目标不存在,无可回滚语义)。
    """
    if orig_md5 is None or backup_dir is None:
        return
    if not dst.is_file() or md5_of(dst) == orig_md5:
        return  # 目标未被改动(常态:os.replace 失败本身是原子的,目标保持原件)
    bak = backup_dir / rel
    if not bak.is_file():
        log.warning("回滚失败:冲突备份缺失 %s", bak)
        return
    rtmp = dst.parent / f"{dst.name}{TMP_SUFFIX}"
    shutil.copy2(long_path(bak), long_path(rtmp))
    os.replace(long_path(rtmp), long_path(dst))
    log.warning("已从备份回滚目标原件: %s ← %s", dst, bak)


def copy_atomic(
    src: Path, dst: Path, *, rel: str, backup_dir: Path | None = None
) -> bool:
    """事务式原子复制 src→dst,返回是否发生了冲突备份。

    事务序列(任一步失败:删 tmp;目标原件若被改动且存在备份则回滚,备份保留):
    1. 源校验:src 缺失 → SourceMissingError;不可读 → PermissionDeniedError
    2. identical 短路:dst 存在且 MD5 与 src 相同 → 零写盘,返回 False
    3. 冲突备份:dst 存在且内容不同且给定 backup_dir → 备份到 backup_dir/<rel>
       (首份备份=目标原始值不可逆,已存在即跳过;返回值仅统计本次新发生的备份)
    4. 写临时:src → dst.parent/<dst.name>.mcmig-tmp,写后 MD5 校验
    5. 换名:os.replace(tmp, dst)(文件系统原子操作)

    Args:
        src: 源文件。
        dst: 目标文件(存在且不同则被覆盖,原件先备份)。
        rel: dst 相对根目录的相对路径,决定备份镜像位置(backup_dir/<rel>)。
        backup_dir: 冲突备份目录;None=不备份(调用方自行保证覆盖安全)。

    Returns:
        True=本次发生了冲突备份;False=未备份(无冲突/identical/首份备份已存在)。

    Raises:
        SourceMissingError: 源文件不存在(或复制过程中消失)。
        PermissionDeniedError: 源不可读,或备份/写临时阶段无权限。
        TargetLockedError: 换名阶段目标被其他进程占用(游戏未退出)。
        FsOpsError: 复制后 MD5 校验不一致,或其他 OSError。
    """
    if not src.is_file():
        raise SourceMissingError(str(src), f"源文件不存在: {src}")
    src_md5 = md5_of(src)
    if src_md5 is None:
        raise PermissionDeniedError(str(src), f"源文件不可读(无法计算 MD5): {src}")
    # identical 短路 + 冲突判定(顺带记录原件 MD5,供失败回滚比对)
    orig_md5: str | None = None
    if dst.is_file():
        orig_md5 = md5_of(dst)
        if orig_md5 == src_md5:
            return False  # 可重入:目标已与源一致,零写盘
    tmp = dst.parent / f"{dst.name}{TMP_SUFFIX}"
    backed_up = False
    phase = "backup"
    try:
        if orig_md5 is not None and backup_dir is not None:
            bak = backup_dir / rel
            if not bak.exists():
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(long_path(dst), long_path(bak))
                backed_up = True
        phase = "copy"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(long_path(src), long_path(tmp))
        phase = "verify"
        if md5_of(tmp) != src_md5:
            raise FsOpsError(str(dst), f"复制后 MD5 校验不一致: {src} → {tmp}")
        phase = "replace"
        os.replace(long_path(tmp), long_path(dst))
    except OSError as e:
        # 回滚(常态为 no-op:replace 失败是原子的,目标未被改动)+ 类型化上抛
        _rollback_if_touched(dst, backup_dir, rel, orig_md5)
        raise _map_oserror(e, phase, src, dst) from e
    finally:
        # 无论成败清掉残留 tmp(成功路径 os.replace 已移走,此处幂等兜底)
        try:
            long_path(tmp).unlink()
        except OSError:
            pass  # tmp 不存在或清理失败,不掩盖主流程异常
    return backed_up


def write_json_atomic(path: Path, payload: dict) -> None:
    """JSON 原子写(utf-8/ensure_ascii=False/indent=2):tmp 写入后 os.replace 换名。

    失败时删除 tmp,目标文件保持原样(绝无半截 JSON)。

    Args:
        path: 目标 JSON 文件(自动创建父目录)。
        payload: 待序列化的字典。

    Raises:
        PermissionDeniedError: 写入无权限。
        FsOpsError: 其他写入/换名 OSError。
    """
    tmp = path.parent / f"{path.name}{TMP_SUFFIX}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with long_path(tmp).open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(long_path(tmp), long_path(path))
    except PermissionError as e:
        raise PermissionDeniedError(str(path), f"JSON 写入无权限: {path}({e})") from e
    except OSError as e:
        raise FsOpsError(str(path), f"JSON 原子写失败: {path}({e})") from e
    finally:
        try:
            long_path(tmp).unlink()
        except OSError:
            pass  # tmp 不存在或清理失败,不掩盖主流程异常


def clean_stale_tmp(root: Path) -> int:
    """递归清理 root 下所有 *.mcmig-tmp 残留临时文件,返回清除数。

    仅删除文件形态的残留(目录不属于本工具产物,保守不动);
    单个清理失败仅记 warning 跳过,绝不中断。

    Args:
        root: 递归清理的根目录。

    Returns:
        实际删除的临时文件数。
    """
    removed = 0
    for p in sorted(root.rglob(f"*{TMP_SUFFIX}")):
        if not p.is_file():
            continue
        try:
            long_path(p).unlink()
            removed += 1
        except OSError as e:
            log.warning("临时文件清理失败 %s: %s", p, e)
    return removed


def check_disk_space(dst_root: Path, needed_bytes: int) -> None:
    """检查 dst_root 所在磁盘剩余空间是否满足需要,不足抛 DiskSpaceError。

    Args:
        dst_root: 目标根目录(常为待创建的版本文件夹;不存在时沿祖先找现有目录探测)。
        needed_bytes: 需要的字节数。

    Raises:
        DiskSpaceError: 剩余空间不足(why 含缺口 MB 数)。
        FsOpsError: 无法定位可探测的现有目录。
    """
    probe = dst_root
    while not probe.exists() and probe != probe.anchor:
        probe = probe.parent
    if not probe.exists():
        raise FsOpsError(str(dst_root), f"无法定位可探测的磁盘目录: {dst_root}")
    free = shutil.disk_usage(probe).free
    if free < needed_bytes:
        gap = needed_bytes - free
        raise DiskSpaceError(
            str(dst_root),
            f"磁盘空间不足:需要 {needed_bytes / (1 << 20):.1f} MB,"
            f"剩余 {free / (1 << 20):.1f} MB,缺口 {gap / (1 << 20):.1f} MB",
        )
