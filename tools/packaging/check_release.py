"""发布守卫(spec §5/决策 D8):tag==pyproject.version==migration.__version__ 三处一致才放行。

防「内置版本与资产名/更新比较基准漂移」——换版本必须改源码两处,不许只换 tag。
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _pyproject_version(root: Path) -> str:
    """读 pyproject 的 project.version。"""
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _dunder_version(root: Path) -> str:
    """读 migration/__init__.py 的 __version__(正则解析,不 import 以免依赖环境)。"""
    m = re.search(r'__version__\s*=\s*"([^"]+)"',
                  (root / "migration" / "__init__.py").read_text(encoding="utf-8"))
    if m is None:
        raise SystemExit("migration/__init__.py 未找到 __version__")
    return m.group(1)


def check(tag: str, root: Path = _ROOT) -> list[str]:
    """校验三处版本一致;返回中文错误列表(空列表=通过)。"""
    t = tag[1:] if tag.startswith("v") else tag
    pv, mv = _pyproject_version(root), _dunder_version(root)
    errors: list[str] = []
    if t != pv:
        errors.append(f"tag({t}) 与 pyproject.version({pv}) 不一致")
    if t != mv:
        errors.append(f"tag({t}) 与 migration.__version__({mv}) 不一致")
    return errors


def _reconfigure_stdio() -> None:
    """输出编码护栏(CI 首跑实测):英文 Windows runner 默认 cp1252,守卫中文
    报错行打印即 UnicodeEncodeError 吞掉真实原因——重定向/管道强制 UTF-8,
    真实控制台保原生编码仅降级不可编码字符(与 build.py 同策)。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass  # 非 TextIOWrapper 或不支持 reconfigure


def main() -> int:
    """CLI:``check_release.py <tag>``;非零退出=守卫拒绝。"""
    _reconfigure_stdio()
    if len(sys.argv) != 2:
        print("用法: check_release.py <tag>", file=sys.stderr)
        return 2
    errors = check(sys.argv[1])
    for e in errors:
        print(f"[发布守卫] {e}——换版本必须同步改源码两处后重打 tag", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
