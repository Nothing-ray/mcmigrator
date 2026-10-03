"""构建编排(spec §4.1.2/§4.1.4):独立工作副本改写 _form → PyInstaller → 后处理。

用法(须在 .venv-build 内运行——该 venv 只按锁定文件装依赖,刻意**不做**
``pip install -e .``:可编辑安装的查找器会抢在工作副本之前解析 ``migration``,
使产物固化错误的 FORM 标记):

    python tools/packaging/build.py --form onefile [--out dist-release]
    python tools/packaging/build.py --form onedir  [--out dist-release]
    python tools/packaging/build.py --sums dist-release

改写纪律(spec §4.1.4 v3):构建在 ``.build-work/<form>/`` 工作副本上进行
(源码树零污染,两形态独立缓存互不残留);产物按资产命名契约落 ``--out``。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
_ROOT = _TOOLS.parents[1]
_WORK_ROOT = _ROOT / ".build-work"
_FORM_VALUES = ("source", "onefile", "onedir")


def asset_names(form: str, version: str) -> tuple[str, str]:
    """资产命名契约(spec §4.1.1,更新契约一处锁定):返回 (onefile 名, onedir 名)。"""
    return (f"mcmig-gui-{version}-win-x64.exe", f"mcmig-{version}-win-x64.zip")


def _read_version(work: Path) -> str:
    """从工作副本解析 ``__version__``(产物文件名的版本来源)。"""
    text = (work / "migration" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if m is None:
        raise SystemExit("未能在 migration/__init__.py 解析 __version__")
    return m.group(1)


def _rewrite_form(work: Path, form: str) -> None:
    """把工作副本内 ``_form.FORM`` 改写为目标形态(构建前调用;产物内固化)。"""
    p = work / "migration" / "_form.py"
    new = re.sub(r'FORM: str = "[^"]*"', f'FORM: str = "{form}"',
                 p.read_text(encoding="utf-8"))
    if f'FORM: str = "{form}"' not in new:
        raise SystemExit("_form.py 改写失败:源文件内容与预期结构不符")
    p.write_text(new, encoding="utf-8", newline="\n")


def _read_embedded_form(exe: Path) -> str:
    """从 EXE 内嵌 PYZ 读出 ``migration._form`` 的 FORM 常量(真产物内验证通道)。

    PyInstaller 6 把 PYZ 以 ``PYZ.pyz`` 条目嵌在 EXE 的 CArchive 内;模块代码
    对象的常量表里含 FORM 的赋值字面量。spec 以 hiddenimports 强制包含
    ``migration._form``(即使暂无模块 import 它,也要可验)。
    """
    import os
    import tempfile

    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    car = CArchiveReader(str(exe))
    pyz_name = next((n for n in car.toc if n.endswith(".pyz")), None)
    if pyz_name is None:
        raise SystemExit(f"{exe.name} 内未找到 PYZ 归档——产物结构异常")
    fd, tmp = tempfile.mkstemp(suffix=".pyz")
    with os.fdopen(fd, "wb") as f:
        f.write(car.extract(pyz_name))
    try:
        zar = ZlibArchiveReader(tmp)
        if "migration._form" not in zar.toc:
            raise SystemExit("产物 PYZ 缺 migration._form(spec hiddenimports 未生效)")
        extracted = zar.extract("migration._form")
        # PyInstaller 版本差异:老版返回 (typecode, data),新版直接返回 code 对象
        code = extracted[1] if isinstance(extracted, tuple) else extracted
    finally:
        os.unlink(tmp)
    values = [c for c in code.co_consts if c in _FORM_VALUES]
    if len(values) != 1:
        raise SystemExit(f"产物内 FORM 常量解析异常: {values!r}")
    return values[0]


def _verify_form_in_dist(app_dir: Path, form: str) -> None:
    """onedir 后验(双通道):标记文件 + 产物内 PYZ 真值。

    标记文件(``build-form.txt``,datas 通道)证明「工作副本被改写、且 datas
    来自工作副本」;PYZ 真值证明「模块解析到的 ``migration._form`` 就是本形态」
    ——后者才是遮蔽故障(解析到源码树/可编辑副本)的直证(spec §4.1.4 v3)。
    """
    marker = (app_dir / "_internal" / "build-form.txt").read_text(encoding="utf-8").strip()
    if marker != form:
        raise SystemExit(f"产物内构建标记与形态不符(期望 {form},实际 {marker})——疑似解析到了源码树/可编辑副本")
    embedded = _read_embedded_form(app_dir / "mcmig.exe")
    if embedded != form:
        raise SystemExit(f"产物内 FORM 真值与形态不符(期望 {form},实际 {embedded})——模块解析被遮蔽")


def _assert_work_copy_resolution(work: Path, form: str) -> None:
    """构建前守卫:以工作副本为 cwd 的 import 必须解析到工作副本且 FORM 正确。

    同时断言 ``migration._form.__file__`` 落在工作副本内——覆盖「可编辑安装/
    同名包遮蔽工作副本」的两种形态共因;命中即拒,不让错误形态的产物被构建。
    """
    code = (
        "import pathlib\n"
        "import migration._form as f\n"
        f"assert f.FORM == {form!r}, f.FORM\n"
        "p = pathlib.Path(f.__file__).resolve()\n"
        f"assert p.is_relative_to(pathlib.Path({str(work)!r}).resolve()), p\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=work, check=False,
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise SystemExit(f"工作副本 import 解析异常(疑似被其它安装遮蔽):\n{proc.stderr}")


def make_filelist(dist_app: Path) -> None:
    """生成受管清单 filelist.txt(spec §4.1.3):zip 展开后程序文件树(相对 mcmig/ 根)。"""
    lines = sorted(
        p.relative_to(dist_app).as_posix()
        for p in dist_app.rglob("*")
        if p.is_file() and p.name != "filelist.txt"
    )
    (dist_app / "filelist.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def make_sums(out_dir: Path, names: Sequence[str]) -> Path:
    """生成 SHA256SUMS.txt(仅列契约资产;UTF-8 无 BOM,``<sha256hex>  <文件名>`` 两空格)。

    只列 ``names``(spec §4.1.1 两资产)——目录里的陈旧/杂项文件不得混入
    (release 的 files glob 会全量上传);任一契约资产缺失即拒(发布不完整)。
    """
    lines = []
    for name in names:
        p = out_dir / name
        if not p.is_file():
            raise SystemExit(f"SUMS 生成失败:契约资产缺失 {p}")
        lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {name}")
    sums = out_dir / "SHA256SUMS.txt"
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return sums


def _zip_onedir(app_dir: Path, target: Path) -> None:
    """打包 onedir 目录为发行 zip(顶层 ``mcmig/``,解压即得绿色目录)。"""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(app_dir.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(app_dir.parent).as_posix())


def build(form: str, out: Path) -> list[Path]:
    """单形态构建:工作副本 → 改写 FORM → PyInstaller → 命名/打包;返回产物路径。"""
    spec_name = "gui-onefile.spec" if form == "onefile" else "app-onedir.spec"
    work = _WORK_ROOT / form
    if work.exists():
        shutil.rmtree(work)  # 两形态独立缓存,且杜绝上次残留(改写纪律)
    work.mkdir(parents=True)
    shutil.copytree(_ROOT / "migration", work / "migration")
    for name in ("entry_gui.py", "entry_cli.py", spec_name):
        shutil.copy2(_TOOLS / name, work / name)
    _rewrite_form(work, form)
    # 构建标记数据文件(spec datas 收入;onedir 产物内后验通道,见 _verify_form_in_dist)
    (work / "build-form.txt").write_text(form, encoding="utf-8", newline="\n")
    _assert_work_copy_resolution(work, form)
    version = _read_version(work)
    dist, build_dir = work / "dist", work / "build"
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(dist), "--workpath", str(build_dir), spec_name],
        cwd=work, check=True,
    )
    out.mkdir(parents=True, exist_ok=True)
    if form == "onefile":
        embedded = _read_embedded_form(dist / "mcmig-gui.exe")  # 真产物内验证(onefile 无目录后验)
        if embedded != form:
            raise SystemExit(f"onefile 产物内 FORM 真值与形态不符(期望 {form},实际 {embedded})")
        one, _ = asset_names("onefile", version)
        target = out / one
        shutil.copy2(dist / "mcmig-gui.exe", target)
        return [target]
    app_dir = dist / "mcmig"
    _verify_form_in_dist(app_dir, form)
    make_filelist(app_dir)
    _, zipped = asset_names("onedir", version)
    target = out / zipped
    _zip_onedir(app_dir, target)
    return [target]


def main() -> int:
    """CLI 入口:--form 构建单形态;--sums 对目录生成 SHA256SUMS.txt(仅两契约资产)。"""
    parser = argparse.ArgumentParser(description="mcmigrator 发行构建编排(spec W4 T11)")
    parser.add_argument("--form", choices=("onefile", "onedir"), help="构建形态")
    parser.add_argument("--out", type=Path, default=_ROOT / "dist-release", help="产物输出目录")
    parser.add_argument("--sums", type=Path, metavar="DIR", help="对目录生成 SHA256SUMS.txt 后退出")
    args = parser.parse_args()
    if args.sums is not None:
        version = _read_version(_ROOT)  # 版本取自源码树(发布守卫已保证与 tag 一致)
        one, zipped = asset_names("onefile", version)
        print(make_sums(args.sums, [one, zipped]))
        return 0
    if args.form is None:
        parser.error("须提供 --form 或 --sums")
    for artifact in build(args.form, args.out):
        print(f"[产物] {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
