# tests/fixtures/server_corpus/synth_v2/_gen.py
"""synth_v2 夹具生成:四对 v2 快照(全部合成脱敏,目录不可达=复放语义)。

不引用任何 observations 路径;重跑本脚本幂等重写八份 JSON。
写出统一钉 LF(newline="\\n")——否则 Windows 默认换行翻译产出 CRLF,
跨平台再生成字节不稳定(终审递延 Minor)。
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
ROOT = "C:\\fixture\\sanitized"

def _mod(modid: str, version: str, jar: str) -> dict:
    return {"modid": modid, "version": version, "jar_filename": jar,
            "neoforge_range": None, "embedded_in": None}

def _snap(version: str, scanned_at: str, files: list[dict], mods: list[dict],
          resolved_root: str | None) -> dict:
    return {
        "tool_version": "0.10.1", "snapshot_format": 2, "version": version,
        "game_root": ROOT, "scanned_at": scanned_at, "resolved_root": resolved_root,
        "world_dirs": [], "hash_mode": "tiered", "file_count": len(files),
        "files": files, "mods": mods,
    }

def _f(path: str, size: int, md5: str | None, mtime: int | None = None) -> dict:
    return {"path": path, "size": size, "md5": md5, "mtime": mtime}

def main() -> None:
    shadow = ROOT + "\\versions\\shadow"
    # ① junction 修复黄金锚:同 resolved_root 不同刻;foo 经 registry 配对(1.0→2.0),
    #    jar 家族名 alpha/beta 异 → filename 通道 0.10.0 下也配不出(对照=0 对)
    pre = _snap("junction_pre", "2026-10-01T10:00:00+08:00",
                [_f("mods/alpha-mod.jar", 100, None, 1000),
                 _f("options.txt", 10, "aaaa5055555050505050505050505050")],
                [_mod("foo", "1.0", "alpha-mod.jar")], shadow)
    post = _snap("junction_post", "2026-10-01T12:00:00+08:00",
                 [_f("mods/beta-mod.jar", 120, None, 1100),
                  _f("options.txt", 10, "aaaa5055555050505050505050505050")],
                 [_mod("foo", "2.0", "beta-mod.jar")], shadow)
    # ② 复放保真锚:dst 名册缺 bar(config/bar.toml 约定式映射 → modid bar)→ orphan/never
    ro_src = _snap("replay_orphan_src", "2026-10-01T10:00:00+08:00",
                   [_f("config/bar.toml", 5, "bbbb5050505050505050505050505050"),
                    _f("config/foo.toml", 6, "cccc5050505050505050505050505050"),
                    _f("mods/bar-1.0.jar", 80, None, 900),
                    _f("mods/foo-1.0.jar", 90, None, 900)],
                   [_mod("bar", "1.0", "bar-1.0.jar"), _mod("foo", "1.0", "foo-1.0.jar")],
                   ROOT + "\\versions\\ro_src")
    ro_dst = _snap("replay_orphan_dst", "2026-10-01T11:00:00+08:00",
                   [_f("config/foo.toml", 6, "cccc5050505050505050505050505050"),
                    _f("mods/foo-1.0.jar", 90, None, 950)],
                   [_mod("foo", "1.0", "foo-1.0.jar")],
                   ROOT + "\\versions\\ro_dst")
    # ③ 冻结语义边界锚:双侧嵌入+不可达 → 新提示行;server.properties md5 异 → 无 semantics note
    fs_src = _snap("frozen_semantics_src", "2026-10-01T10:00:00+08:00",
                   [_f("server.properties", 20, "dddd5050505050505050505050505050"),
                    _f("mods/foo-1.0.jar", 90, None, 900)],
                   [_mod("foo", "1.0", "foo-1.0.jar")], ROOT + "\\versions\\fs_src")
    fs_dst = _snap("frozen_semantics_dst", "2026-10-01T12:00:00+08:00",
                   [_f("server.properties", 22, "eeee5050505050505050505050505050"),
                    _f("mods/foo-1.0.jar", 90, None, 950)],
                   [_mod("foo", "1.0", "foo-1.0.jar")], ROOT + "\\versions\\fs_dst")
    # ④ 混合 v1/v2 锚:src 为 v1(删 mods 键、format=1)→ 行为与 v1+v1 逐字节一致
    mx_src = _snap("mx_src", "2026-10-01T10:00:00+08:00",
                   [_f("options.txt", 10, "ffff5050505050505050505050505050")],
                   [_mod("foo", "1.0", "foo-1.0.jar")], ROOT + "\\versions\\mx_src")
    mx_src.pop("mods")
    mx_src["snapshot_format"] = 1
    mx_dst = _snap("mx_dst", "2026-10-01T12:00:00+08:00",
                   [_f("options.txt", 12, "abcd5050505050505050505050505050")],
                   [_mod("foo", "1.0", "foo-1.0.jar")], ROOT + "\\versions\\mx_dst")
    for snap in (pre, post, ro_src, ro_dst, fs_src, fs_dst, mx_src, mx_dst):
        p = HERE / f"{snap['version']}.snapshot.json"
        p.write_text(json.dumps(snap, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8", newline="\n")
        print("wrote", p.name)

if __name__ == "__main__":
    main()
