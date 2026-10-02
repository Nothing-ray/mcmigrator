"""实例锁:双锁排序/junction 归一/第二锁失败回滚/跨进程互斥/abandoned 标记(spec §4.2)。

持锁方用子进程模拟(跨进程互斥是本模块的存在理由,同进程复用已由
JobStore 单任务锁 409 先行拦截);子进程进入 instance_locks 后打印
``HELD`` 作同步点,主进程据此确认「已持锁」再发起竞争获取。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from migration.instlock import InstanceLockError, instance_locks

# 仓库根(子进程 ``python -c`` 的 sys.path[0] 是 cwd,测试可能 chdir 到
# tmp_path,须显式注入仓库根才能 import migration)
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _holder_script(game_root: Path, versions: tuple[str, ...], hold_seconds: float) -> str:
    """持锁子进程脚本:取锁成功打印 HELD(同步点),再睡 hold_seconds 模拟占用。"""
    return textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(_REPO_ROOT)!r})
        import time
        from migration.instlock import instance_locks
        with instance_locks({str(game_root)!r}, {", ".join(repr(v) for v in versions)}):
            print("HELD", flush=True)
            time.sleep({hold_seconds})
    """)


def _holder_script_vers(game_root: Path, version: str, hold_seconds: float) -> str:
    """以版本名(可传别名)调用 instance_locks 的持锁脚本变体(junction 归一用)。

    与 ``_holder_script`` 同构,单版本名便捷封装:别名/真名经 resolve 后
    应归一为同一锁键,故持锁方传哪个名字都能与对方冲突。
    """
    return _holder_script(game_root, (version,), hold_seconds)


def _start_holder(script: str) -> subprocess.Popen[bytes]:
    """起持锁子进程并等到 HELD(确保其已真正持有锁);同步失败即杀掉防泄漏。"""
    p = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE)
    assert p.stdout is not None
    try:
        assert p.stdout.readline().strip() == b"HELD"
    except BaseException:
        _stop_holder(p)
        raise
    return p


def _stop_holder(p: subprocess.Popen[bytes]) -> None:
    """清理持锁子进程(kill+wait+关管道;测试 finally 必调,防子进程/句柄泄漏)。"""
    p.kill()
    p.wait()
    if p.stdout is not None:
        p.stdout.close()


def test_cross_process_second_acquire_fails(tmp_path):
    """子进程持 A→B 锁期间,主进程 A→B 获取失败(跨进程互斥,A→B 与 B→C 见下一条)。"""
    p = _start_holder(_holder_script(tmp_path, ("A", "B"), 3.0))
    try:
        with pytest.raises(InstanceLockError):
            with instance_locks(tmp_path, "A", "B", timeout=0.5):
                pass
    finally:
        _stop_holder(p)


def test_ab_cross_bc_second_lock_conflict(tmp_path):
    """甲 A→B 写 B、乙 B→C 读 B:乙第二把锁(B)被甲持有→失败(spec §4.2 交叉场景)。"""
    p = _start_holder(_holder_script(tmp_path, ("A", "B"), 3.0))
    try:
        with pytest.raises(InstanceLockError):
            with instance_locks(tmp_path, "B", "C", timeout=0.5):  # B 与甲冲突
                pass
    finally:
        _stop_holder(p)


def test_second_lock_failure_releases_first(tmp_path):
    """第二把锁失败→第一把已释放(之后可单独重新获取第一把)(spec §4.2/评审 P2-3)。"""
    p = _start_holder(_holder_script(tmp_path, ("B",), 3.0))
    try:
        with pytest.raises(InstanceLockError):
            with instance_locks(tmp_path, "A", "B", timeout=0.5):
                pass
        with instance_locks(tmp_path, "A", timeout=1.0):  # A 未被遗留占用
            pass
    finally:
        _stop_holder(p)


def test_same_instance_dedups_single_key(tmp_path):
    """src==dst(同实例)→键去重为单锁(评审 P2-3)。"""
    with instance_locks(tmp_path, "A", "A") as info:
        assert info["abandoned"] == []


@pytest.mark.skipif(sys.platform != "win32", reason="junction 经 cmd /c mklink /J 创建,仅 Windows")
def test_junction_alias_same_key(tmp_path):
    """junction 指向同实例→锁键相同,不能绕过(Windows mklink /J)。

    持锁方以别名 ``Alias`` 取锁,主进程以真名 ``A`` 取锁:两侧键同为
    ``resolve(game_root/versions/A)``(junction 被 resolve 消解),必冲突。
    """
    real = tmp_path / "versions" / "A"
    real.mkdir(parents=True)
    link = tmp_path / "versions" / "Alias"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(real)], check=True, capture_output=True
    )
    p = _start_holder(_holder_script_vers(tmp_path, "Alias", 3.0))
    try:
        with pytest.raises(InstanceLockError):
            with instance_locks(tmp_path, "A", timeout=0.5):  # 别名↔真名同键
                pass
    finally:
        _stop_holder(p)


def test_abandoned_mutex_flagged(tmp_path):
    """持有者异常退出→等待方获取成功且 abandoned 标记(不当干净任务)。

    Windows 机制注记(实测):命名互斥体随末个句柄关闭而销毁——无人等待时
    持有者崩溃,对象直接消失(后续进程重建干净互斥体,无从标记);abandoned
    只在「他方等待中」可观测(等待方已持开句柄,持有者死亡 → 其等待返回
    WAIT_ABANDONED)。故本用例让等待方先进入等待再 kill 持有者——这正是
    spec §4.2 关心的真实场景(乙在等甲,甲崩了,乙不得当干净任务)。
    POSIX 回退同构:等待方轮询中发现持有者 pid 已死 → 陈旧锁接管并标记。
    """
    p = _start_holder(_holder_script(tmp_path, ("A",), 30.0))
    result: dict[str, object] = {}

    def _acquirer() -> None:
        try:
            with instance_locks(tmp_path, "A", timeout=10.0) as info:
                result["abandoned"] = list(info["abandoned"])
        except Exception as e:  # noqa: BLE001 — 线程边界转发异常供断言
            result["error"] = e

    t = threading.Thread(target=_acquirer)
    t.start()
    try:
        time.sleep(0.5)  # 等待方已进入 WaitForSingleObject/锁文件轮询
        _stop_holder(p)  # 持有者不释放退出(kill 模拟崩溃)
    finally:
        t.join(timeout=5.0)
    assert "error" not in result
    abandoned = result.get("abandoned")
    assert abandoned  # 键为 resolve 后路径,非空即已标记
