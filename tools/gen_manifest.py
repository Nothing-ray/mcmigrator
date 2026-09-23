"""生成数据完整性清单 migration/data/manifest.sha256(开发工具脚本)。

对 ``migration/data/`` 下所有 ``*.yaml`` 计算 SHA-256,写入同目录的
``manifest.sha256``,每行格式 ``<sha256hex>  <相对文件名>``(两个空格分隔、
LF 行尾、按文件名排序)。清单随仓库提交、随发行包分发,由
``migration/doctor.py`` 的 ``verify_data_manifest`` 逐行校验。

用法:``.venv/Scripts/python tools/gen_manifest.py``
(数据文件有任何改动后重跑一次并提交产物,否则 mcmig doctor 会报「损坏」)。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

# 数据目录:本脚本位于 <仓库>/tools/,数据位于 <仓库>/migration/data/
# (按脚本自身位置定位,与运行时 cwd 无关)
DATA_DIR = Path(__file__).resolve().parents[1] / "migration" / "data"

# 清单文件名(与 migration/doctor.py 的 MANIFEST_NAME 约定一致)
MANIFEST_NAME = "manifest.sha256"

log = logging.getLogger(__name__)


def sha256_file(path: Path) -> str:
    """计算文件 SHA-256(hex 小写;CRLF→LF 归一化后哈希,F24)。

    归一化使 LF 提交字节与 autocrlf=true 检出的 CRLF 工作区算出同一哈希;
    对 LF 文件是 no-op,故 manifest 数值与既有清单一致,无需全量重生成。
    """
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk.replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def build_manifest_text(data_dir: Path) -> str:
    """构建清单文本。

    Args:
        data_dir: 数据目录(只收录顶层 ``*.yaml``,不含清单自身)。

    Returns:
        清单全文:每行 ``<sha256hex>  <文件名>``,LF 行尾,按文件名排序。
    """
    names = sorted(p.name for p in data_dir.glob("*.yaml"))
    return "".join(f"{sha256_file(data_dir / name)}  {name}\n" for name in names)


def main() -> int:
    """生成/覆写清单文件并输出摘要。

    Returns:
        进程退出码:0=成功;数据目录不存在或没有任何 yaml 视为配置错误返回 2
        (拒绝生成空清单,空清单会让 doctor 把所有数据文件误报为「多余」)。
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not DATA_DIR.is_dir():
        log.error("[错误] 数据目录不存在:%s", DATA_DIR)
        return 2
    text = build_manifest_text(DATA_DIR)
    if not text:
        log.error("[错误] %s 下没有任何 *.yaml,拒绝生成空清单", DATA_DIR)
        return 2
    # write_bytes 显式 LF,规避 Windows 文本模式自动转换 CRLF
    (DATA_DIR / MANIFEST_NAME).write_bytes(text.encode("utf-8"))
    log.info("[完成] 已收录 %d 个文件 → %s", text.count("\n"), DATA_DIR / MANIFEST_NAME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
