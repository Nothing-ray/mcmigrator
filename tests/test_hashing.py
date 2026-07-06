from pathlib import Path

from migration import hashing


def test_text_file_should_hash(tmp_path: Path):
    assert hashing.should_hash(tmp_path / "options.txt", strict=False) is True


def test_config_toml_should_hash(tmp_path: Path):
    assert hashing.should_hash(tmp_path / "config" / "a.toml", strict=False) is True


def test_mods_jar_should_not_hash(tmp_path: Path):
    p = tmp_path / "mods" / "create.jar"
    assert hashing.should_hash(p, strict=False) is False


def test_sqlite_should_not_hash(tmp_path: Path):
    assert hashing.should_hash(tmp_path / "dh" / "lod.sqlite", strict=False) is False


def test_zip_and_mca_should_not_hash(tmp_path: Path):
    assert hashing.should_hash(tmp_path / "xaero" / "cache.zip", strict=False) is False
    assert hashing.should_hash(tmp_path / "saves" / "r.0.0.mca", strict=False) is False


def test_strict_forces_all(tmp_path: Path):
    assert hashing.should_hash(tmp_path / "mods" / "x.jar", strict=True) is True
    assert hashing.should_hash(tmp_path / "a.sqlite", strict=True) is True


def test_compute_md5_stable(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"hello world")
    assert hashing.compute_md5(p) == "5eb63bbbe01eeed093cb22bb8f5acdc3"


def test_compute_md5_large_streaming(tmp_path: Path):
    p = tmp_path / "big.bin"
    p.write_bytes(b"x" * (1 << 18))  # 256 KiB,触发分块
    h1 = hashing.compute_md5(p)
    assert h1 == hashing.compute_md5(p)  # 稳定
