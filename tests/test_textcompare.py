"""textcompare 单测:properties 语义等价判定(F12)。"""

from migration.textcompare import properties_semantic_equal


def test_identical_bytes_equal():
    assert properties_semantic_equal(b"a=1\nb=2\n", b"a=1\nb=2\n")


def test_escape_difference_ignored():
    # vanilla Properties.store 会把值内冒号转义;语义相同
    assert properties_semantic_equal(b"level-type=minecraft:normal\n",
                                     b"level-type=minecraft\\:normal\n")


def test_comment_and_timestamp_ignored():
    # 时间戳表头行每次保存必变,不参与语义
    a = b"#Minecraft server properties\n#Sat Sep 12 23:56:00 CST 2026\nview-distance=8\n"
    b = b"#Minecraft server properties\n#Sun Sep 13 20:00:00 CST 2026\nview-distance=8\n"
    assert properties_semantic_equal(a, b)


def test_bom_difference_ignored():
    # bytes 字面量不支持 \u 转义,UTF-8 BOM 须写为 \xef\xbb\xbf(即 U+FEFF 的编码)
    assert properties_semantic_equal(b"\xef\xbb\xbfview-distance=8\n", b"view-distance=8\n")


def test_key_order_ignored():
    assert properties_semantic_equal(b"a=1\nb=2\n", b"b=2\na=1\n")


def test_real_value_change_detected():
    assert not properties_semantic_equal(b"view-distance=8\n", b"view-distance=10\n")


def test_key_added_detected():
    assert not properties_semantic_equal(b"a=1\n", b"a=1\npause-when-empty-seconds=60\n")


def test_unicode_escape_vs_raw_equal():
    # \uXXXX 转义与原生 UTF-8 同值
    assert properties_semantic_equal("motd=龙域群岛\n".encode("utf-8"),
                                     "motd=\\u9f99\\u57df\\u7fa4\\u5c9b\n".encode("ascii"))
