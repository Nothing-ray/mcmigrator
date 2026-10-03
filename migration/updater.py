"""更新基础档(spec §4.3):检查/选资产/比版/SUMS 校验/下载暂存——纯库,CLI/GUI 平级消费。

行为红线(spec §4.3.2):本模块**不自动运行暂存文件**、不提供任何「运行」
入口;产物只到「暂存+返回路径」,由前端给「打开位置+替换说明」。
形态选择唯一事实来源=``migration._form.FORM``(构建时固化,决策 D7)。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import _form

_REPO = "Nothing-ray/mcmigrator"  # 仓标识(非版本号,不违「不硬编码版本」)
_RELEASES_LATEST = f"https://api.github.com/repos/{_REPO}/releases/latest"
_ACCEPT = "application/vnd.github+json"
_TIMEOUT = 10.0
_DOWNLOAD_CHUNK = 256 * 1024  # 进度通知节流步长(评审④ P2-7:读取/取消按网络块)


class UpdateError(Exception):
    """更新失败(中文三段式载体:``what`` 短标题/``why`` 原因+建议)。"""

    def __init__(self, what: str, why: str) -> None:
        super().__init__(f"{what}:{why}")
        self.what = what
        self.why = why


@dataclass(frozen=True)
class AssetInfo:
    """Release 资产(名称/字节数/浏览器下载地址)。"""

    name: str
    size: int
    url: str


@dataclass(frozen=True)
class ReleaseInfo:
    """latest Release 摘要(tag+资产列表)。"""

    tag: str
    assets: tuple[AssetInfo, ...]


class UpdateCancelled(Exception):
    """用户取消(本次 uuid 子目录已清理后抛出;调用方按 cancelled 终态呈现)。"""


@dataclass(frozen=True)
class UpdatePlan:
    """一次成功下载的完整结果(done 载荷与 GET 同源,spec §4.3.4)。"""

    version: str
    asset_name: str
    path: Path
    size: int
    sha256: str


def _open_client() -> httpx.Client:
    """构造生产 httpx 客户端(跟随重定向——GitHub 资产实为 302;测试注入点)。"""
    return httpx.Client(follow_redirects=True, timeout=_TIMEOUT,
                        headers={"Accept": _ACCEPT})


def local_version() -> str:
    """动态读当前版本(函数内 import,每次解析模块属性——测试可 monkeypatch)。"""
    from . import __version__

    return __version__


def fetch_latest_release() -> ReleaseInfo | None:
    """取 latest Release;404(尚无发布)→ None;网络/限额/5xx → UpdateError。

    响应解析边界(评审④ P2-6):非 JSON(代理/门户拦截返回 HTML)、结构/字段/
    类型异常一律转三段式 UpdateError——此前 JSONDecodeError/KeyError 会裸逃逸
    (CLI 打 traceback、GUI 检查端 500)。
    """
    try:
        with _open_client() as client:
            resp = client.get(_RELEASES_LATEST)
    except httpx.HTTPError as e:
        raise UpdateError("更新检查失败", f"无法连接 GitHub({e.__class__.__name__})。"
                                         "请检查网络后重试,或到项目发布页手动查看。") from e
    if resp.status_code == 404:
        return None
    if resp.status_code == 403:
        raise UpdateError("更新检查被限流",
                          "GitHub 匿名访问限额(60 次/小时·每来源 IP)已用尽,请稍后再试。")
    if resp.status_code != 200:
        raise UpdateError("更新检查失败", f"GitHub 返回状态码 {resp.status_code},请稍后重试。")
    try:
        data = resp.json()
    except ValueError as e:  # JSONDecodeError:代理/门户拦截返回 HTML 登录页
        raise UpdateError("更新检查失败",
                          "GitHub 返回的内容不是有效 JSON(可能被网络代理或登录页拦截);"
                          "请检查网络后重试,或到项目发布页手动查看。") from e
    if not isinstance(data, dict):
        raise UpdateError("更新检查响应异常", "GitHub 响应结构不是对象,已拒绝本次检查;请稍后重试。")
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise UpdateError("更新检查响应异常",
                          "GitHub 响应缺少有效的 tag_name 字段,已拒绝本次检查;请稍后重试。")
    raw_assets = data.get("assets", [])
    if not isinstance(raw_assets, list):
        raise UpdateError("更新检查响应异常",
                          "GitHub 响应的 assets 字段不是列表,已拒绝本次检查;请稍后重试。")
    assets: list[AssetInfo] = []
    for a in raw_assets:
        if not isinstance(a, dict):
            raise UpdateError("更新检查响应异常",
                              "GitHub 响应含非对象资产条目,已拒绝本次检查;请稍后重试。")
        name = a.get("name")
        url = a.get("browser_download_url")
        size = a.get("size", 0)
        if not isinstance(name, str) or not isinstance(url, str) or not isinstance(size, int):
            raise UpdateError("更新检查响应异常",
                              f"GitHub 资产条目字段缺失或类型异常({name!r}),已拒绝本次检查;请稍后重试。")
        assets.append(AssetInfo(name, size, url))
    return ReleaseInfo(tag=tag, assets=tuple(assets))


def pick_asset(assets: Sequence[AssetInfo]) -> AssetInfo | None:
    """按资产命名契约匹配本形态资产;source 或缺失 → None(调用方按「该形态未发布」呈现)。"""
    if _form.FORM == "onefile":
        matches = [a for a in assets
                   if a.name.startswith("mcmig-gui-") and a.name.endswith("-win-x64.exe")]
    elif _form.FORM == "onedir":
        matches = [a for a in assets
                   if a.name.startswith("mcmig-") and a.name.endswith("-win-x64.zip")
                   and not a.name.startswith("mcmig-gui-")]
    else:
        return None
    return matches[0] if matches else None


def _version_tuple(ver: str) -> tuple[int, ...]:
    """版本串→数值段元组(去 v 前缀;每段取前导数字,rc 等后缀忽略但有序)。"""
    v = ver.lstrip("vV")
    segs = []
    for seg in v.split("."):
        m = re.match(r"(\d+)", seg)
        segs.append(int(m.group(1)) if m else 0)
    return tuple(segs)


def is_newer(remote_tag: str, local_ver: str) -> bool:
    """数值比较远端 tag 是否高于本地版本(0.9 < 0.10 < 1.0;后缀忽略记录于决策)。"""
    return _version_tuple(remote_tag) > _version_tuple(local_ver)


_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


def fetch_sums(url: str) -> dict[str, str]:
    """下载并解析 SHA256SUMS.txt → {资产名: sha256hex小写}。

    四种失败形态一律 UpdateError 拒绝(spec §4.3.1):请求失败/清单空、
    非法十六进制、同名重复条目(条目缺失由调用方查 dict 时判定)。
    """
    try:
        with _open_client() as client:
            resp = client.get(url)
    except httpx.HTTPError as e:
        raise UpdateError("校验清单下载失败",
                          f"SHA256SUMS.txt 下载失败({e.__class__.__name__}),请重试。") from e
    if resp.status_code != 200:
        raise UpdateError("校验清单下载失败",
                          f"SHA256SUMS.txt 返回 {resp.status_code};发布不完整,请稍后再试。")
    sums: dict[str, str] = {}
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise UpdateError("校验清单格式异常", "SHA256SUMS.txt 存在无法解析的行,已拒绝本次更新。")
        digest, name = parts[0].lower(), parts[1].strip()
        if not _HEX.match(digest):
            raise UpdateError("校验清单格式异常",
                              f"资产 {name} 的 SHA256 非合法十六进制,已拒绝本次更新。")
        if name in sums:
            raise UpdateError("校验清单格式异常",
                              f"资产 {name} 在清单中重复出现,已拒绝本次更新。")
        sums[name] = digest
    if not sums:
        raise UpdateError("校验清单为空", "SHA256SUMS.txt 不含任何条目,已拒绝本次更新。")
    return sums


def staging_root_at(base: Path) -> Path:
    """暂存根(无回退版):``<base>/data/update-staging``——GUI 消费(不承诺 TEMP)。"""
    root = base / "data" / "update-staging"
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_staging() -> Path:
    """CLI 暂存根(spec §4.3.1 两端分治):冻结=<exe目录>/data/update-staging,
    不可写回退 %TEMP%\\mcmig-update\\,再失败 → UpdateError「请手动下载」。"""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    try:
        root = staging_root_at(base)
        # 写探测(终审 I3,spec §4.3.1):已存在但不可写(ACL/只读介质)时
        # mkdir 不报错——不探测会延迟到下载期才报「写盘失败」,回退承诺落空
        probe = root / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return root
    except OSError:
        pass
    tmp = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "mcmig-update"
    try:
        tmp.mkdir(parents=True, exist_ok=True)
        probe = tmp / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return tmp
    except OSError as e:
        raise UpdateError("暂存目录不可写",
                          "软件目录与系统临时目录都不可写,无法暂存更新;"
                          "请到项目发布页手动下载替换。") from e


def download_and_verify(asset: AssetInfo, sha256: str, staging_dir: Path,
                        progress_cb: Callable[[int, int], None] | None = None,
                        should_cancel: Callable[[], bool] | None = None) -> Path:
    """流式下载+SHA256 校验,终路径=``<staging_dir>/<uuid4>/<资产名>``(独占,v3)。

    成功产物也留在 uuid 子目录——GitHub 资产不可变须显式启用,同版本重传时
    共享路径会被后任务改写、破坏先任务已校验内容(spec §4.3.1 v3)。
    失败/取消只清理**本任务的 uuid 子目录**;成功产物保留至用户应用,不跨任务清理。

    取消响应(评审④ P2-7):``iter_bytes(chunk_size=None)`` 按实际网络块产出——
    固定 256KiB 聚合缓冲会把取消检查推迟到缓冲满/流结束(慢速/涓流连接下长
    时间不响应,后台单锁无法释放);进度通知另行节流,事件节奏与既往一致。
    """
    sub = staging_dir / str(uuid.uuid4())
    digest = hashlib.sha256()
    received = 0
    last_notified = 0
    try:
        sub.mkdir(parents=True)
        part = sub / (asset.name + ".part")
        with _open_client() as client, client.stream("GET", asset.url) as resp:
            if resp.status_code != 200:
                raise UpdateError("下载失败", f"资产下载返回 {resp.status_code},请稍后重试。")
            total = int(resp.headers.get("content-length", 0))
            with part.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=None):
                    if should_cancel is not None and should_cancel():
                        raise UpdateCancelled()
                    f.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if progress_cb is not None and (received - last_notified >= _DOWNLOAD_CHUNK
                                                    or received == total):
                        last_notified = received
                        progress_cb(received, total)
        if digest.hexdigest().lower() != sha256.lower():
            raise UpdateError("校验失败",
                              "下载内容 SHA256 与发布清单不一致,本次下载已丢弃;请重试,"
                              "持续失败请到项目发布页手动下载。")
        final = sub / asset.name
        part.replace(final)
        return final
    except (UpdateCancelled, UpdateError):
        shutil.rmtree(sub, ignore_errors=True)
        raise
    except Exception as e:  # noqa: BLE001 — 评审② I2:网络中断(httpx 流异常)/写盘(OSError)
        # 等一切未预期失败统一转三段式并清理**本任务**子目录,绝不裸逃逸
        shutil.rmtree(sub, ignore_errors=True)
        raise UpdateError("下载失败",
                          f"网络中断或写盘失败({e.__class__.__name__}),本次下载已丢弃;"
                          "请重试,持续失败请到项目发布页手动下载。") from e


def plan_update(staging_root: Path, *,
                progress_cb: Callable[[int, int], None] | None = None,
                should_cancel: Callable[[], bool] | None = None) -> UpdatePlan | None:
    """编排:检查→比版→选资产→SUMS→下载校验;无新版/无发布 → None。

    有新版但本形态资产缺失 → UpdateError(「该形态未发布」);源码模式由调用方前置拦截。
    """
    rel = fetch_latest_release()
    if rel is None or not is_newer(rel.tag, local_version()):
        return None
    asset = pick_asset(rel.assets)
    if asset is None:
        raise UpdateError("该形态未发布",
                          "本次发布未包含当前安装形态的资产;请到项目发布页手动查看。")
    sums_url = next((a.url for a in rel.assets if a.name == "SHA256SUMS.txt"), None)
    if sums_url is None:
        raise UpdateError("校验清单缺失", "本次发布未附 SHA256SUMS.txt,已拒绝自动下载。")
    entry = fetch_sums(sums_url).get(asset.name)
    if entry is None:
        raise UpdateError("校验清单缺条目",
                          f"SHA256SUMS.txt 未包含 {asset.name},已拒绝自动下载。")
    path = download_and_verify(asset, entry, staging_root, progress_cb, should_cancel)
    return UpdatePlan(version=rel.tag.lstrip("vV"), asset_name=asset.name,
                      path=path, size=asset.size, sha256=entry)
