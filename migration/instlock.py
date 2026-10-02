"""跨进程实例锁:同一时刻至多一个 mcmig 进程操作给定实例(spec §4.2)。

背景:CLI 与 GUI 可能并发操作同一对源/目标实例(双开命令行、双开网页向导、
脚本编排),若「检查→执行」不原子,预检通过后另一进程写盘即产生「审旧执新」。
本模块以实例的规范化身份为键获取排他锁,封住竞争窗:

- 锁键 = ``str((game_root / "versions" / 版本名).resolve())`` —— resolve 消解
  junction/别名(``mklink /J`` 指向同实例的两个名字归一为同一键,不可绕过);
  多实例按键排序去重后依序获取(全局一致顺序,交叉场景 甲 A→B / 乙 B→C
  不会互相持环等待;src==dst 同实例去重为单把锁)。
- Windows 实现:命名互斥体 ``Local\\\\mcmig-inst-<sha1(键)[:16]>``
  (ctypes 调 kernel32 的 CreateMutexW/WaitForSingleObject/ReleaseMutex/
  CloseHandle;Local 命名空间按用户会话隔离——本工具只需防同一用户双开)。
  前持有者异常退出时 WaitForSingleObject 返回 WAIT_ABANDONED:获取成功但
  标记 abandoned(前持有者可能是中断的迁移,须查 journal,不当干净任务)。
  named mutex 拿不到对端 pid,争用错误只能说明「另一 mcmig 进程正在操作
  该实例」。已知局限(实测 Windows 10):命名互斥体随末个句柄关闭而销毁,
  无人等待时持有者崩溃 → 对象直接消失、后续进程重建干净互斥体,abandoned
  无从标记——该场景的中断证据归 journal(T6);abandoned 只覆盖「他方
  等待中持有者死亡」这一可观测场景(恰为 spec §4.2 关心的交叉竞争场景)。
- POSIX 回退(开发/测试用):临时目录锁文件 O_CREAT|O_EXCL + 内容 pid;
  陈旧判定 pid 不存活(持有者死亡→接管并标记 abandoned,与 Windows 的
  WAIT_ABANDONED 语义对齐)。
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# WaitForSingleObject 返回值(Win32 常量)
_WAIT_OBJECT_0 = 0x00000000  # 获取成功(干净)
_WAIT_ABANDONED = 0x00000080  # 获取成功但前持有者异常退出(未 ReleaseMutex)
_WAIT_TIMEOUT = 0x00000102  # 超时(仍被他方持有)

# POSIX 回退:锁文件轮询间隔与权限
_POSIX_POLL_SECONDS = 0.05
_POSIX_LOCK_MODE = 0o644


class InstanceLockError(Exception):
    """实例锁获取失败(超时争用/系统调用失败),携带三段式信息。

    Attributes:
        what: 失败对象(争用/失败的实例键,即 resolve 后的版本目录路径)。
        why: 中文原因与建议(Windows named mutex 拿不到对端 pid,只能说明
            「另一 mcmig 进程正在操作该实例」)。
        details: 结构化补充,至少含 ``holders``(争用实例键列表)。
    """

    def __init__(self, what: str, why: str, details: dict[str, object] | None = None) -> None:
        """初始化锁错误。

        Args:
            what: 失败对象(实例键)。
            why: 中文原因与建议。
            details: 结构化补充(含 holders 争用实例键列表)。
        """
        super().__init__(f"{what}:{why}")
        self.what = what
        self.why = why
        self.details = dict(details or {})


def _lock_keys(game_root: Path, versions: Sequence[str]) -> list[str]:
    """规范化锁键:resolve 消 junction/别名,排序去重(spec §4.2;同实例去重)。

    Args:
        game_root: 游戏根目录(含 versions/)。
        versions: 版本名列表(可含指向同实例的 junction 别名)。

    Returns:
        排序去重后的键列表(键 = resolve 后的版本目录绝对路径字符串)。
    """
    return sorted({str((Path(game_root) / "versions" / v).resolve()) for v in versions})


# ---------------------------------------------------------------------------
# Windows 后端:命名互斥体(ctypes,标准库零新增依赖)
# ---------------------------------------------------------------------------

_kernel32_cache: Any | None = None


def _kernel32() -> Any:
    """取配置好签名的 kernel32(首次调用配置并缓存;仅 Windows 路径调用)。

    Returns:
        已设置 argtypes/restype 的 ``ctypes.windll.kernel32``。
    """
    global _kernel32_cache
    if _kernel32_cache is not None:
        return _kernel32_cache
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k32.ReleaseMutex.restype = wintypes.BOOL
    k32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    _kernel32_cache = k32
    return k32


def _mutex_name(key: str) -> str:
    """由实例键派生命名互斥体名:``Local\\mcmig-inst-<sha1(键)[:16]>``。

    Args:
        key: 实例键(resolve 后的版本目录路径)。

    Returns:
        互斥体名(sha1 截断 16 位 hex,名称长度与键内容解耦,规避名称长度限制)。
    """
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"Local\\mcmig-inst-{digest}"


def _acquire_mutex(key: str, timeout: float) -> tuple[int, bool]:
    """创建/打开命名互斥体并等待所有权(Windows)。

    Args:
        key: 实例键(互斥体名由此派生)。
        timeout: 等待超时秒数。

    Returns:
        (互斥体句柄, 是否 abandoned——前持有者异常退出)。

    Raises:
        InstanceLockError: 超时(仍被他方持有)/ 系统调用失败。
    """
    import ctypes

    k32 = _kernel32()
    handle = k32.CreateMutexW(None, False, _mutex_name(key))
    if not handle:
        raise InstanceLockError(
            f"实例锁({key})",
            f"创建互斥体失败(GetLastError={k32.GetLastError()}),请重试",
            {"holders": [key]},
        )
    rc = k32.WaitForSingleObject(ctypes.c_void_p(handle), int(timeout * 1000))
    if rc == _WAIT_OBJECT_0:
        return int(handle), False
    if rc == _WAIT_ABANDONED:
        # 前持有者异常退出(句柄被 OS 回收但未 ReleaseMutex):获取成功,
        # 但须让调用方按 abandoned 处理(查 journal,不当干净任务)
        return int(handle), True
    k32.CloseHandle(ctypes.c_void_p(handle))
    if rc == _WAIT_TIMEOUT:
        # named mutex 拿不到对端 pid:只能说明「另一 mcmig 进程正在操作该实例」
        raise InstanceLockError(
            f"实例锁({key})",
            "获取实例排他锁超时:另一 mcmig 进程正在操作该实例,"
            "请等待其完成(或关闭其他 mcmig 窗口/命令)后重试",
            {"holders": [key], "timeout": timeout},
        )
    raise InstanceLockError(
        f"实例锁({key})",
        f"等待互斥体失败(WaitForSingleObject 返回 {rc},"
        f"GetLastError={k32.GetLastError()}),请重试",
        {"holders": [key]},
    )


def _release_mutex(handle: int) -> None:
    """释放并关闭互斥体句柄(Windows;尽力而为,不抛出)。"""
    import ctypes

    k32 = _kernel32()
    try:
        k32.ReleaseMutex(ctypes.c_void_p(handle))
        k32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:  # noqa: BLE001 — 释放路径绝不向上抛(finally 中调用)
        log.warning("互斥体释放异常(handle=%s)", handle, exc_info=True)


# ---------------------------------------------------------------------------
# POSIX 回退后端:临时目录锁文件 O_CREAT|O_EXCL + pid 陈旧判定(开发/测试用)
# ---------------------------------------------------------------------------


def _lock_file_path(key: str) -> Path:
    """由实例键派生锁文件路径(临时目录下按 sha1 截断命名)。

    Args:
        key: 实例键(resolve 后的版本目录路径)。

    Returns:
        锁文件路径(与键一一对应;临时目录跨进程共享,同键同文件)。
    """
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"mcmig-inst-{digest}.lock"


def _pid_alive(pid: int) -> bool:
    """判定 pid 是否存活(``os.kill(pid, 0)`` 探测,不发信号)。

    Args:
        pid: 待探测的进程号。

    Returns:
        进程存在返回 True;不存在返回 False(无权限属「存在但属他人」)。
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stale_unlink(path: Path) -> bool:
    """陈旧锁文件摘除:持有者 pid 已死 → unlink 成功即视为接管权到手。

    Args:
        path: 锁文件路径。

    Returns:
        本方 unlink 成功(可安全重建锁文件)返回 True;文件已被他人
        接管/正常释放返回 False(调用方按普通争用继续轮询)。
    """
    try:
        pid = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        pid = -1  # 内容不可读/非数字:视同陈旧(无从证明持有者存活)
    if pid > 0 and _pid_alive(pid):
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False  # 他方已接管/正常释放:按普通争用处理
    return True


def _acquire_lockfile(key: str, timeout: float) -> tuple[Path, bool]:
    """O_EXCL 创建锁文件获取所有权(POSIX 回退;陈旧锁接管并标记 abandoned)。

    Args:
        key: 实例键(锁文件路径由此派生)。
        timeout: 等待超时秒数(轮询,间隔 ``_POSIX_POLL_SECONDS``)。

    Returns:
        (锁文件路径, 是否 abandoned——接管了持有者已死亡的陈旧锁)。

    Raises:
        InstanceLockError: 超时(锁文件仍被存活持有者占用)/ 系统调用失败。
    """
    path = _lock_file_path(key)
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _POSIX_LOCK_MODE)
        except FileExistsError:
            if _stale_unlink(path):
                # 抢到陈旧锁接管权:立即重建;若又被他方抢先,回落普通争用
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _POSIX_LOCK_MODE)
                except FileExistsError:
                    continue
                except OSError as e:
                    raise InstanceLockError(
                        f"实例锁({key})", f"锁文件创建失败({e})", {"holders": [key]}
                    ) from e
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                return path, True
            if time.monotonic() >= deadline:
                raise InstanceLockError(
                    f"实例锁({key})",
                    "获取实例排他锁超时:另一 mcmig 进程正在操作该实例,"
                    "请等待其完成(或关闭其他 mcmig 窗口/命令)后重试",
                    {"holders": [key], "timeout": timeout},
                )
            time.sleep(_POSIX_POLL_SECONDS)
        except OSError as e:
            raise InstanceLockError(
                f"实例锁({key})", f"锁文件创建失败({e})", {"holders": [key]}
            ) from e
        else:
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            return path, False


def _release_lockfile(path: Path) -> None:
    """删除锁文件释放所有权(POSIX 回退;尽力而为,不抛出)。"""
    try:
        path.unlink()
    except FileNotFoundError:
        pass  # 已被陈旧接管逻辑摘除:无需重复
    except OSError:
        log.warning("锁文件删除异常(%s)", path, exc_info=True)


# ---------------------------------------------------------------------------
# 统一入口:排序获取 + 失败回滚 + abandoned 汇总
# ---------------------------------------------------------------------------


def _acquire(key: str, timeout: float) -> tuple[int | Path, bool]:
    """获取单个实例键的排他锁(按平台分派后端)。

    Args:
        key: 实例键。
        timeout: 等待超时秒数。

    Returns:
        (释放令牌——Windows 句柄 / POSIX 锁文件路径, 是否 abandoned)。

    Raises:
        InstanceLockError: 超时争用/系统调用失败。
    """
    if _IS_WINDOWS:
        return _acquire_mutex(key, timeout)
    return _acquire_lockfile(key, timeout)


def _release(token: int | Path) -> None:
    """释放单个实例锁(按令牌类型分派;尽力而为,不抛出)。"""
    if isinstance(token, Path):
        _release_lockfile(token)
    else:
        _release_mutex(token)


@contextmanager
def instance_locks(
    game_root: Path, *versions: str, timeout: float = 5.0
) -> Iterator[dict[str, list[str]]]:
    """对涉及的源/目标实例按规范化身份排序获取排他锁(spec §4.2)。

    语义:
    - 键 = ``str((game_root/"versions"/v).resolve())``,resolve 消 junction/
      别名;排序去重后依序获取(全局一致顺序防交叉死锁,同实例去重单锁)。
    - 第二把获取失败 → 释放已获取的第一把并抛 :class:`InstanceLockError`
      (失败回滚,不留半套锁)。
    - yields ``{"abandoned": [实例键...]}``:abandoned = 前持有者异常退出
      (Windows WAIT_ABANDONED / POSIX 陈旧锁接管),调用方须提示用户核对
      journal 中断记录,不当干净任务处理。

    Args:
        game_root: 游戏根目录(含 versions/)。
        versions: 参与本次操作的版本名(scan 传单版本;plan/migrate/swap
            传 src+dst)。
        timeout: 单把锁的获取超时秒数(无争用时即时通过,不拖慢常规路径)。

    Yields:
        ``{"abandoned": [实例键...]}``。

    Raises:
        InstanceLockError: 任一把锁获取失败(details.holders 为争用键列表)。
    """
    keys = _lock_keys(game_root, versions)
    acquired: list[int | Path] = []
    abandoned: list[str] = []
    try:
        for key in keys:
            token, was_abandoned = _acquire(key, timeout)
            acquired.append(token)
            if was_abandoned:
                abandoned.append(key)
        yield {"abandoned": abandoned}
    finally:
        # 无论成功 / 第二把失败 / 体内异常:依获取逆序释放已持有的锁
        for token in reversed(acquired):
            _release(token)
