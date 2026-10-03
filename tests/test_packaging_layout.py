"""打包基建结构测试:依赖分组/锁定文件/入口与 spec 契约(spec §4.1)。"""

import re
from pathlib import Path

import tomllib

_ROOT = Path(__file__).resolve().parents[1]
_PKG = _ROOT / "tools" / "packaging"


def _pyproject() -> dict:
    return tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_moves_pywebview_to_gui_group():
    """D5:核心依赖不含 pywebview;可选组 gui 收容(源码默认=CLI+浏览器模式)。"""
    data = _pyproject()
    names = [d.split(">=")[0].split("<")[0].strip().lower() for d in data["project"]["dependencies"]]
    assert "pywebview" not in names
    gui = data["project"]["optional-dependencies"]["gui"]
    assert any(d.lower().startswith("pywebview") for d in gui)


def test_win_build_lockfile_pins_runtime_and_tools():
    """锁定文件(spec §4.1.2):运行依赖+PyInstaller 工具链全部精确钉版(==)。"""
    lines = (_PKG / "requirements-win-build.txt").read_text(encoding="utf-8").splitlines()
    pins = {ln.split("==")[0].strip().lower() for ln in lines if "==" in ln and not ln.startswith("#")}
    for name in (
        "rich", "pyyaml", "pathspec", "fastapi", "uvicorn", "httpx",
        "pywebview", "pyinstaller", "pyinstaller-hooks-contrib",
    ):
        assert name in pins, f"锁定文件缺 {name} 钉版"


def test_gitignore_covers_build_workspaces():
    """构建工作区不入库:独立 venv/工作副本/产物目录(spec §4.1.4 改写纪律)。"""
    text = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in (".venv-build/", ".build-work/", "dist-release/"):
        assert entry in text


import importlib.util  # noqa: E402


def _load_build_py():
    """从文件路径加载 build.py(script 非包,测试经 importlib 直取)。"""
    spec = importlib.util.spec_from_file_location("mcmig_build", _PKG / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_asset_names_contract():
    """资产命名=更新契约(spec §4.1.1,一处锁定)。"""
    mod = _load_build_py()
    assert mod.asset_names("onefile", "0.13.0") == ("mcmig-gui-0.13.0-win-x64.exe", "mcmig-0.13.0-win-x64.zip")


def test_make_filelist_posix_sorted_and_excludes_self(tmp_path):
    """受管清单(spec §4.1.3):POSIX 相对路径、排序、不含自身;UTF-8 无 BOM。"""
    mod = _load_build_py()
    (tmp_path / "_internal" / "migration" / "data").mkdir(parents=True)
    (tmp_path / "mcmig.exe").write_bytes(b"1")
    (tmp_path / "mcmig-gui.exe").write_bytes(b"2")
    (tmp_path / "_internal" / "migration" / "data" / "rules.yaml").write_bytes(b"3")
    mod.make_filelist(tmp_path)
    text = (tmp_path / "filelist.txt").read_text(encoding="utf-8")
    assert not text.startswith("\ufeff")
    assert text.splitlines() == [
        "_internal/migration/data/rules.yaml",
        "mcmig-gui.exe",
        "mcmig.exe",
    ]


def test_make_sums_lists_only_contract_assets(tmp_path):
    """SUMS 格式与范围(spec §4.1.1/§4.2.2):只列两契约资产,sha256+两空格,UTF-8 无 BOM。

    目录里的陈旧/杂项文件不得混入清单(release 的 files glob 会全量上传)。
    """
    import codecs
    import hashlib

    import pytest

    mod = _load_build_py()
    (tmp_path / "a.exe").write_bytes(b"hello")
    (tmp_path / "b.zip").write_bytes(b"world")
    (tmp_path / "stale-old.zip").write_bytes(b"old")   # 杂项:不入清单
    sums = mod.make_sums(tmp_path, ["a.exe", "b.zip"])
    lines = sums.read_text(encoding="utf-8").splitlines()
    assert not sums.read_bytes().startswith(codecs.BOM_UTF8)
    assert lines == [
        f"{hashlib.sha256(b'hello').hexdigest()}  a.exe",
        f"{hashlib.sha256(b'world').hexdigest()}  b.zip",
    ]
    with pytest.raises(SystemExit):
        mod.make_sums(tmp_path, ["a.exe", "missing.zip"])   # 契约资产缺失即拒


def test_entry_scripts_absolute_import_and_stdio_guard():
    """薄入口(评审 P1-1/P1-2):绝对导入;GUI 入口先兜底 stdio 再 import 主程序。"""
    gui = (_PKG / "entry_gui.py").read_text(encoding="utf-8")
    assert "from migration.gui.app import main" in gui
    assert "devnull" in gui
    cli = (_PKG / "entry_cli.py").read_text(encoding="utf-8")
    assert "from migration.cli import main" in cli


def test_spec_files_split_by_form_with_console_flags():
    """两形态各自 spec(一张 spec 无法同时固化两种 FORM 标记):gui=console=False。"""
    one = (_PKG / "gui-onefile.spec").read_text(encoding="utf-8")
    two = (_PKG / "app-onedir.spec").read_text(encoding="utf-8")
    assert "entry_gui.py" in one and "console=False" in one
    assert "webview.platforms.winforms" in one
    assert "entry_cli.py" in two and "entry_gui.py" in two
    assert 'name="mcmig"' in two and 'name="mcmig-gui"' in two


def test_build_script_workcopy_discipline():
    """改写纪律(spec §4.1.4 v3):独立工作副本(.build-work/<form>),两形态分离;onedir 后验 FORM。"""
    text = (_PKG / "build.py").read_text(encoding="utf-8")
    assert ".build-work" in text
    assert "_verify_form_in_dist" in text
    assert "--form" in text and "onefile" in text and "onedir" in text


def test_verify_form_in_dist_reads_build_marker(tmp_path, monkeypatch):
    """onedir 后验的标记通道:标记不符即拒(embedded 通道以桩隔离,专测标记)。"""
    import pytest

    mod = _load_build_py()
    monkeypatch.setattr(mod, "_read_embedded_form", lambda exe: "onedir")
    (tmp_path / "_internal").mkdir()
    (tmp_path / "_internal" / "build-form.txt").write_text("onedir\n", encoding="utf-8")
    mod._verify_form_in_dist(tmp_path, "onedir")  # 双通道一致 → 通过
    with pytest.raises(SystemExit):
        mod._verify_form_in_dist(tmp_path, "onefile")  # 标记不符即拒


def test_spec_files_carry_build_form_marker():
    """两 spec 均以 datas 收入 build-form.txt(build.py 写入工作副本;后验数据通道)。"""
    for name in ("gui-onefile.spec", "app-onedir.spec"):
        text = (_PKG / name).read_text(encoding="utf-8")
        assert "build-form.txt" in text, f"{name} 缺构建标记数据项"


def test_release_guard_three_way_consistency(tmp_path):
    """发布守卫(spec §5/D8):tag==pyproject==__version__,任一不一致即拒。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("mcmig_check_release", _PKG / "check_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.13.0"\n', encoding="utf-8")
    (tmp_path / "migration").mkdir()
    (tmp_path / "migration" / "__init__.py").write_text('__version__ = "0.13.0"\n', encoding="utf-8")
    assert mod.check("v0.13.0", tmp_path) == []
    assert mod.check("v0.13.1", tmp_path) == [
        "tag(0.13.1) 与 pyproject.version(0.13.0) 不一致",
        "tag(0.13.1) 与 migration.__version__(0.13.0) 不一致",
    ]
    (tmp_path / "migration" / "__init__.py").write_text('__version__ = "0.13.1"\n', encoding="utf-8")
    # pyproject 与 tag 同版、__version__ 不同:仅一条错误
    assert mod.check("v0.13.0", tmp_path) == ["tag(0.13.0) 与 migration.__version__(0.13.1) 不一致"]


def test_verify_form_in_dist_calls_embedded_check(tmp_path, monkeypatch):
    """后验必须双通道:标记文件 + 产物内 PYZ 真值(_read_embedded_form 接线)。"""
    import pytest

    mod = _load_build_py()
    (tmp_path / "_internal").mkdir()
    (tmp_path / "_internal" / "build-form.txt").write_text("onedir\n", encoding="utf-8")
    (tmp_path / "mcmig.exe").write_bytes(b"stub")
    monkeypatch.setattr(mod, "_read_embedded_form", lambda exe: "onedir")
    mod._verify_form_in_dist(tmp_path, "onedir")  # 双通道一致 → 过
    monkeypatch.setattr(mod, "_read_embedded_form", lambda exe: "source")
    with pytest.raises(SystemExit):
        mod._verify_form_in_dist(tmp_path, "onedir")  # PYZ 真值不符 → 拒(即便标记对)


def test_build_reads_embedded_form_from_pyz():
    """build.py 含 PYZ 读取实现(CArchive→ZlibArchive→migration._form 常量)。"""
    text = (_PKG / "build.py").read_text(encoding="utf-8")
    assert "_read_embedded_form" in text
    assert "ZlibArchiveReader" in text and "migration._form" in text
    for name in ("gui-onefile.spec", "app-onedir.spec"):
        assert "migration._form" in (_PKG / name).read_text(encoding="utf-8"), name


def test_zip_onedir_top_level_prefix(tmp_path):
    """zip 顶层=mcmig/(更新契约:解压即得绿色目录)。"""
    mod = _load_build_py()
    app_dir = tmp_path / "dist" / "mcmig"
    (app_dir / "_internal").mkdir(parents=True)
    (app_dir / "mcmig.exe").write_bytes(b"exe")
    (app_dir / "_internal" / "x.txt").write_bytes(b"x")
    target = tmp_path / "out.zip"
    mod._zip_onedir(app_dir, target)
    import zipfile

    assert sorted(zipfile.ZipFile(target).namelist()) == ["mcmig/_internal/x.txt", "mcmig/mcmig.exe"]


def test_entry_gui_stdio_guard_under_none_streams(tmp_path):
    """spec §9:注入 sys.stdout/stderr=None 后执行入口不炸,且被兜底为真句柄。"""
    import subprocess
    import sys as _sys

    marker = tmp_path / "guard.txt"
    code = (
        "import sys\n"
        "sys.stdout = None\n"
        "sys.stderr = None\n"
        "import migration.gui.app as app\n"
        "app.main = lambda argv=None: 0\n"          # 主程序替换为桩:只验入口自身
        "import runpy\n"
        "rc = None\n"
        "try:\n"
        f"    runpy.run_path({str(_PKG / 'entry_gui.py')!r}, run_name='__main__')\n"
        "except SystemExit as e:\n"
        "    rc = e.code or 0\n"
        "ok = sys.stdout is not None and sys.stderr is not None\n"
        f"open({str(marker)!r}, 'w', encoding='utf-8').write(f'{{rc}}|{{ok}}')\n"
    )
    proc = subprocess.run([_sys.executable, "-c", code], cwd=_ROOT,
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    assert marker.read_text(encoding="utf-8") == "0|True"


def test_pyproject_description_refreshed():
    """T13.5-6:description 不再是「只读 scan/diff」旧标语。"""
    data = _pyproject()
    desc = data["project"]["description"]
    assert "只读" not in desc
    assert "迁移" in desc and "换包" in desc


def test_version_two_places_consistent():
    """版本双源一致(发布守卫的本地常驻形态,D8)。

    终审 C2:断言版本无关——硬编码字面量会让每次升版(0.13.1/1.0.0)在
    release CI 全检里必红,堵死两连发试发协议;此处只钉「两处一致+格式合法」,
    具体值由发布守卫(tag 对比)与 bump 提交把关。
    """
    data = _pyproject()
    mv = re.search(r'__version__\s*=\s*"([^"]+)"',
                   (_ROOT / "migration" / "__init__.py").read_text(encoding="utf-8")).group(1)
    assert data["project"]["version"] == mv
    assert re.fullmatch(r"\d+\.\d+\.\d+", mv), f"版本格式异常: {mv}"


def test_backlog_doc_exists_with_required_sections():
    """递延账本落库(spec §4.5):含事务档滑移与 0.12.0 复审④两组锚。"""
    text = (_ROOT / "docs" / "backlog.md").read_text(encoding="utf-8")
    assert "事务档" in text and "服务线程" in text
