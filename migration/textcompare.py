""".properties 语义级比较(F12):消灭 java.util.Properties.store 规范化噪声。

server.properties 被 vanilla 重写后产生同值异字节(冒号转义/时间戳表头/BOM),
字节比较误报 modified;本模块按 key→value 语义比对,忽略注释与转义差异。
"""

from __future__ import annotations

_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "f": "\f"}


def _unescape(s: str) -> str:
    """反转义 Properties 序列(\\: \\= \\\\ \\uXXXX 等);孤立反斜杠按字面保留。"""
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in _ESCAPES:
                out.append(_ESCAPES[nxt])
                i += 2
                continue
            if nxt == "u" and i + 6 <= len(s):
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass  # 非 4 位十六进制,反斜杠按字面处理
            out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse_properties(data: bytes) -> dict[str, str]:
    """解析 properties 字节流为 key→value 字典(忽略注释/空行/BOM;不处理续行)。"""
    text = data.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    props: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        # 首个未转义的 '=' 或 ':' 为分隔符(MC 写出的均为 '=';空白分隔不出现,不做)
        sep = -1
        for i, ch in enumerate(line):
            if ch in "=:" and (i == 0 or line[i - 1] != "\\"):
                sep = i
                break
        if sep == -1:
            props[_unescape(line)] = ""
        else:
            props[_unescape(line[:sep].strip())] = _unescape(line[sep + 1:].strip())
    return props


def properties_semantic_equal(a: bytes, b: bytes) -> bool:
    """判定两份 .properties 内容语义等价(键值集合全等,忽略注释/转义/键序/BOM)。

    Args:
        a: 源侧文件字节内容。
        b: 目标侧文件字节内容。

    Returns:
        True 表示语义等价(差异仅为规范化噪声)。
    """
    return _parse_properties(a) == _parse_properties(b)
