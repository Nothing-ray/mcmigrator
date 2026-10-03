"""updater 纯函数层测试(spec §4.3.1):mock httpx 经 _open_client 注入。"""

from __future__ import annotations

import hashlib
import sys

import httpx
import pytest

from migration import _form, updater

_H1 = "a" * 64  # 合法 64-hex 摘要(校验要求全 64 位)
_H2 = "b" * 64
_H3 = "0" * 63 + "1"


def _install_transport(monkeypatch, handler) -> None:
    """把 _open_client 替换为 MockTransport 客户端(跟随重定向与生产一致)。"""
    monkeypatch.setattr(
        updater, "_open_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                             timeout=updater._TIMEOUT),
    )


def _release_json(tag: str = "v0.13.1") -> dict:
    return {
        "tag_name": tag,
        "assets": [
            {"name": "mcmig-gui-0.13.1-win-x64.exe", "size": 31, "browser_download_url": "https://dl/e.exe"},
            {"name": "mcmig-0.13.1-win-x64.zip", "size": 32, "browser_download_url": "https://dl/m.zip"},
            {"name": "SHA256SUMS.txt", "size": 3, "browser_download_url": "https://dl/sums.txt"},
        ],
    }


def test_fetch_latest_parses_assets(monkeypatch):
    _install_transport(monkeypatch, lambda req: httpx.Response(200, json=_release_json()))
    rel = updater.fetch_latest_release()
    assert rel is not None and rel.tag == "v0.13.1"
    assert [a.name for a in rel.assets] == [
        "mcmig-gui-0.13.1-win-x64.exe", "mcmig-0.13.1-win-x64.zip", "SHA256SUMS.txt",
    ]


def test_fetch_latest_404_returns_none(monkeypatch):
    _install_transport(monkeypatch, lambda req: httpx.Response(404))
    assert updater.fetch_latest_release() is None


def test_fetch_latest_403_rate_limit_message(monkeypatch):
    _install_transport(monkeypatch, lambda req: httpx.Response(403))
    with pytest.raises(updater.UpdateError) as ei:
        updater.fetch_latest_release()
    assert "限额" in ei.value.why


def test_fetch_latest_network_error_is_updateerror(monkeypatch):
    def _handler(req):
        raise httpx.ConnectError("断网")
    _install_transport(monkeypatch, _handler)
    with pytest.raises(updater.UpdateError):
        updater.fetch_latest_release()


def test_fetch_latest_follows_redirect(monkeypatch):
    """302→200 链必须跟随(httpx 默认不跟随;browser_download_url 实为 302)。"""
    def handler(req):
        if "latest" in str(req.url):
            return httpx.Response(302, headers={"Location": "https://api/real"})
        return httpx.Response(200, json=_release_json())
    _install_transport(monkeypatch, handler)
    assert updater.fetch_latest_release() is not None


def test_pick_asset_reads_form_only(monkeypatch):
    """资产形态唯一事实来源=_form.FORM(D7);source 不选任何资产。"""
    assets = [
        updater.AssetInfo("mcmig-gui-0.13.1-win-x64.exe", 31, "u1"),
        updater.AssetInfo("mcmig-0.13.1-win-x64.zip", 32, "u2"),
    ]
    monkeypatch.setattr(_form, "FORM", "onefile")
    assert updater.pick_asset(assets).name == "mcmig-gui-0.13.1-win-x64.exe"
    monkeypatch.setattr(_form, "FORM", "onedir")
    assert updater.pick_asset(assets).name == "mcmig-0.13.1-win-x64.zip"
    monkeypatch.setattr(_form, "FORM", "source")
    assert updater.pick_asset(assets) is None


@pytest.mark.parametrize(
    ("remote", "local", "expect"),
    [("v0.13.1", "0.13.0", True), ("0.13.0", "0.13.0", False), ("0.9", "0.10", False),
     ("1.0.0", "0.13.1", True), ("0.14.0rc1", "0.13.9", True)],
)
def test_is_newer_boundaries(remote, local, expect):
    assert updater.is_newer(remote, local) is expect


def test_fetch_sums_parses_with_tolerance(monkeypatch):
    body = (
        f"{_H1}  mcmig-0.13.1-win-x64.zip\n"
        "\n"
        f"{_H2}  mcmig-gui-0.13.1-win-x64.exe\n"
        "# 注释行忽略\n"
        f"{_H3}  中文名资产.zip\n"
    )
    _install_transport(monkeypatch, lambda req: httpx.Response(200, text=body))
    sums = updater.fetch_sums("https://dl/sums.txt")
    assert sums["中文名资产.zip"] == _H3
    assert sums["mcmig-0.13.1-win-x64.zip"] == _H1


@pytest.mark.parametrize(
    ("body", "why_fragment"),
    [
        ("", "不含任何条目"),
        ("zz nothex  a.zip\n", "十六进制"),
        (f"{_H1}  bb.zip\n{_H1}  bb.zip\n", "重复"),
    ],
)
def test_fetch_sums_rejects_bad_forms(monkeypatch, body, why_fragment):
    _install_transport(monkeypatch, lambda req: httpx.Response(200, text=body))
    with pytest.raises(updater.UpdateError) as ei:
        updater.fetch_sums("https://dl/sums.txt")
    assert why_fragment in ei.value.why


def test_fetch_sums_404_rejected(monkeypatch):
    _install_transport(monkeypatch, lambda req: httpx.Response(404))
    with pytest.raises(updater.UpdateError):
        updater.fetch_sums("https://dl/sums.txt")


# ---- Task 7:下载暂存(uuid 独占终路径/取消/staging 解析/编排) ----


def _stream_handler(content: bytes):
    def handler(req):
        return httpx.Response(200, content=content,
                              headers={"Content-Length": str(len(content))})
    return handler


def test_download_and_verify_success_exclusive_path(monkeypatch, tmp_path):
    """成功产物落 <uuid>/<资产名>(独占终路径,v3),.part 已消失。"""
    payload = b"x" * 1000
    _install_transport(monkeypatch, _stream_handler(payload))
    asset = updater.AssetInfo("mcmig-0.13.1-win-x64.zip", len(payload), "https://dl/m.zip")
    path = updater.download_and_verify(asset, hashlib.sha256(payload).hexdigest(), tmp_path)
    assert path.parent.parent == tmp_path and path.name == asset.name
    assert not list(path.parent.glob("*.part"))
    assert path.read_bytes() == payload


def test_download_bad_sha_cleans_own_subdir(monkeypatch, tmp_path):
    _install_transport(monkeypatch, _stream_handler(b"payload"))
    asset = updater.AssetInfo("a.zip", 7, "https://dl/a.zip")
    with pytest.raises(updater.UpdateError) as ei:
        updater.download_and_verify(asset, "0" * 64, tmp_path)
    assert "校验" in ei.value.what
    assert list(tmp_path.iterdir()) == []  # 本任务子目录已清理


def test_download_cancel_cleans_and_raises_cancelled(monkeypatch, tmp_path):
    _install_transport(monkeypatch, _stream_handler(b"payload"))
    asset = updater.AssetInfo("a.zip", 7, "https://dl/a.zip")
    with pytest.raises(updater.UpdateCancelled):
        updater.download_and_verify(asset, "0" * 64, tmp_path, should_cancel=lambda: True)
    assert list(tmp_path.iterdir()) == []


def test_download_progress_callback_reports_bytes(monkeypatch, tmp_path):
    payload = b"y" * 5000
    _install_transport(monkeypatch, _stream_handler(payload))
    seen: list[tuple[int, int]] = []
    asset = updater.AssetInfo("a.zip", len(payload), "https://dl/a.zip")
    updater.download_and_verify(asset, hashlib.sha256(payload).hexdigest(), tmp_path,
                                progress_cb=lambda r, t: seen.append((r, t)))
    assert seen and seen[-1] == (len(payload), len(payload))


def test_same_version_reupload_does_not_touch_earlier_path(monkeypatch, tmp_path):
    """Review Focus:v3——同版本资产重传时,后任务不得改写先任务已返回的独占路径。"""
    v1, v2 = b"version-one", b"version-two-reuploaded"
    state = {"content": v1}
    sums = {"content": hashlib.sha256(v1).hexdigest()}

    def handler(req):
        if "sums" in str(req.url):
            return httpx.Response(200, text=f"{sums['content']}  a.zip\n")
        return httpx.Response(200, content=state["content"])

    _install_transport(monkeypatch, handler)
    asset = updater.AssetInfo("a.zip", 0, "https://dl/a.zip")
    first = updater.download_and_verify(asset, sums["content"], tmp_path)
    # 资产被重传(不可变性未启用),SUMS 同步换内容
    state["content"] = v2
    sums["content"] = hashlib.sha256(v2).hexdigest()
    second = updater.download_and_verify(asset, sums["content"], tmp_path)
    assert second != first and second.parent != first.parent
    assert first.read_bytes() == v1  # 先任务路径内容不被后任务改写


def test_download_two_runs_use_distinct_uuid_dirs(monkeypatch, tmp_path):
    """跨进程/双任务并发:两次下载各占独立 uuid 子目录,互不可见。"""
    payload = b"z" * 100
    _install_transport(monkeypatch, _stream_handler(payload))
    asset = updater.AssetInfo("a.zip", len(payload), "https://dl/a.zip")
    digest = hashlib.sha256(payload).hexdigest()
    p1 = updater.download_and_verify(asset, digest, tmp_path)
    p2 = updater.download_and_verify(asset, digest, tmp_path)
    assert p1.parent != p2.parent


def test_resolve_staging_frozen_then_temp_fallback(monkeypatch, tmp_path):
    r"""冻结:exe 旁 data/update-staging;不可写→回退 %TEMP%\mcmig-update。

    失败腿注入用 monkeypatch(Windows 对目录 chmod 不生效,只对文件切只读位)。
    """
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    (exe_dir / "mcmig.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "mcmig.exe"))
    root = updater.resolve_staging()
    assert root == exe_dir / "data" / "update-staging" and root.is_dir()
    # exe 目录不可写(staging_root_at 注入 OSError)→ 回退 %TEMP%\mcmig-update
    with monkeypatch.context() as m:
        m.setattr(updater, "staging_root_at",
                  lambda base: (_ for _ in ()).throw(OSError("read-only")))
        m.setenv("TEMP", str(tmp_path / "tmp"))
        root2 = updater.resolve_staging()
        assert root2 == tmp_path / "tmp" / "mcmig-update" and root2.is_dir()


def test_resolve_staging_probes_first_leg_writability(monkeypatch, tmp_path):
    """终审 I3(spec §4.3.1):首腿须写探测——已存在但不可写(以文件占位模拟)
    必须回落 %TEMP%,不得延迟到下载期报「写盘失败」。"""
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "mcmig.exe"))
    blocked = tmp_path / "blocked-root"
    blocked.write_bytes(b"")   # 占位为文件:其下写探测必失败(NotADirectoryError)
    monkeypatch.setattr(updater, "staging_root_at", lambda base: blocked)
    monkeypatch.setenv("TEMP", str(tmp_path / "tmp"))
    root = updater.resolve_staging()
    assert root == tmp_path / "tmp" / "mcmig-update"


def test_resolve_staging_both_fail_rejects(monkeypatch, tmp_path):
    """两腿皆失败 → UpdateError「请手动下载」(TEMP 父路径被同名文件占位→mkdir 必败)。"""
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "mcmig.exe"))
    (tmp_path / "nope").write_bytes(b"")  # 同名文件占位:TEMP 子路径 mkdir 必失败
    monkeypatch.setattr(updater, "staging_root_at",
                        lambda base: (_ for _ in ()).throw(OSError("read-only")))
    monkeypatch.setenv("TEMP", str(tmp_path / "nope" / "deep"))
    with pytest.raises(updater.UpdateError) as ei:
        updater.resolve_staging()
    assert "手动下载" in ei.value.why


def test_plan_update_orchestrates(monkeypatch, tmp_path):
    """编排:latest→SUMS→下载→UpdatePlan 全字段;无新版→None。"""
    payload = b"firmware"
    digest = hashlib.sha256(payload).hexdigest()

    def handler(req):
        url = str(req.url)
        if url.endswith("/latest"):
            if handler.mode == "none":
                return httpx.Response(200, json=_release_json("v0.12.0"))
            rel = _release_json()
            rel["assets"] = [
                {"name": "mcmig-gui-0.13.1-win-x64.exe", "size": len(payload),
                 "browser_download_url": "https://dl/e.exe"},
                {"name": "SHA256SUMS.txt", "size": 1, "browser_download_url": "https://dl/sums.txt"},
            ]
            return httpx.Response(200, json=rel)
        if "sums" in url:
            return httpx.Response(200, text=f"{digest}  mcmig-gui-0.13.1-win-x64.exe\n")
        return httpx.Response(200, content=payload)

    handler.mode = "new"
    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(_form, "FORM", "onefile")
    import migration
    monkeypatch.setattr(migration, "__version__", "0.12.0")
    plan = updater.plan_update(tmp_path)
    assert plan is not None
    assert (plan.version, plan.asset_name, plan.sha256, plan.size) == (
        "0.13.1", "mcmig-gui-0.13.1-win-x64.exe", digest, len(payload))
    assert plan.path.read_bytes() == payload
    handler.mode = "none"
    assert updater.plan_update(tmp_path) is None  # 无新版


# ---- 评审② 修复波:下载中途网络异常必须转 UpdateError 并清理本子目录 ----


def test_download_midstream_network_error_maps_to_updateerror(monkeypatch, tmp_path):
    """httpx 流中途断(ReadError)不得裸逃逸:转三段式 UpdateError + 清理本 uuid 子目录。"""
    class _BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial"
            raise httpx.ReadError("断流")

    def handler(req):
        return httpx.Response(200, stream=_BrokenStream())

    _install_transport(monkeypatch, handler)
    asset = updater.AssetInfo("a.zip", 0, "https://dl/a.zip")
    with pytest.raises(updater.UpdateError) as ei:
        updater.download_and_verify(asset, "0" * 64, tmp_path)
    assert "下载失败" in ei.value.what
    assert list(tmp_path.iterdir()) == []   # 无 .part/子目录残留


# ---- 评审④ P2-6:响应解析边界(格式/结构/字段/类型异常一律 UpdateError) ----


def test_fetch_latest_html_body_rejected(monkeypatch):
    """代理登录页(200 + HTML)→ UpdateError(此前 JSONDecodeError 裸逃逸)。"""
    _install_transport(monkeypatch, lambda req: httpx.Response(
        200, content=b"<html>login</html>", headers={"content-type": "text/html"}))
    with pytest.raises(updater.UpdateError) as ei:
        updater.fetch_latest_release()
    assert "JSON" in ei.value.why


@pytest.mark.parametrize("payload", [
    [1, 2],                                                       # 非对象
    {"assets": []},                                               # 缺 tag_name
    {"tag_name": "", "assets": []},                               # tag_name 空串
    {"tag_name": "v1", "assets": "x"},                            # assets 非列表
    {"tag_name": "v1", "assets": [1]},                            # 条目非对象
    {"tag_name": "v1", "assets": [{"name": "a.zip"}]},            # 条目缺 url
    {"tag_name": "v1", "assets": [{"name": "a.zip",
                                   "browser_download_url": "u",
                                   "size": "abc"}]},              # size 非法
])
def test_fetch_latest_bad_structure_rejected(monkeypatch, payload):
    """结构/字段/类型异常一律 UpdateError(已拒绝本次检查,不给 KeyError/TypeError 留缝)。"""
    _install_transport(monkeypatch, lambda req: httpx.Response(200, json=payload))
    with pytest.raises(updater.UpdateError) as ei:
        updater.fetch_latest_release()
    assert "已拒绝本次检查" in ei.value.why


# ---- 评审④ P2-7:取消按实际接收块响应(256KiB 聚合缓冲延迟取消检查) ----


class _SlowStream(httpx.SyncByteStream):
    """按小块产出的慢速流:记录实际被消费的网络块数。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.consumed = 0

    def __iter__(self):
        for c in self._chunks:
            self.consumed += 1
            yield c


def test_download_cancel_checked_per_network_chunk(monkeypatch, tmp_path):
    """评审④ P2-7:取消须在首个网络块到达后立即生效——此前 iter_bytes 的
    256KiB 聚合缓冲把取消检查推迟到缓冲满/流结束(慢速/涓流连接下长时间
    不响应,后台单锁无法释放)。"""
    stream = _SlowStream([b"x" * 16] * 6)
    _install_transport(monkeypatch, lambda req: httpx.Response(200, stream=stream))
    asset = updater.AssetInfo("a.zip", 96, "https://dl/a.zip")
    with pytest.raises(updater.UpdateCancelled):
        updater.download_and_verify(asset, "0" * 64, tmp_path,
                                    should_cancel=lambda: True)
    assert stream.consumed == 1, "取消须在首个网络块到达后立即生效(不等待 256KiB 聚合)"
    assert list(tmp_path.iterdir()) == []   # 本任务子目录已清理
