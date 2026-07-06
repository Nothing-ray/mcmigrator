"""命令行入口(stub)。"""

import argparse

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    """构建顶层 argparse 解析器(stub 版)。"""
    parser = argparse.ArgumentParser(prog="mcmig", description="Minecraft 整合包版本迁移工具")
    parser.add_argument("-V", "--version", action="version", version=f"mcmig {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口(stub 版)。"""
    build_parser().parse_args(argv)
    return 0
