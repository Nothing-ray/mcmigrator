# mcmigrator

[中文](README.zh-CN.md) | [English](README.en.md)

> Minecraft 整合包版本迁移工具(只读 scan/diff)— 在同一整合包的版本隔离文件夹之间,比对玩家状态差异。
>
> A read-only scan/diff tool for Minecraft modpack version migration — compare player state across version-isolated folders of the same modpack.

## 核心特性 / Features

- **分层哈希 / Tiered hashing**:文本全量 MD5、mods 按文件名集合、bulk 按 size——快且精确
- **数据驱动分类 / Data-driven classification**:规则引擎(pathspec,gitignore 语义),分层 first-match-wins
- **迁移导向 6 桶 diff / Migration-oriented 6-bucket diff**:`to_migrate` / `candidate` / `mods` / `only_in_dst` / `identical` / `never`
- **零写入 / Zero writes**:对游戏目录只读;回退/重复试验天然满足

## 快速上手 / Quick Start

```bash
pip install -e .
mcmig scan <version>
mcmig diff <src> <dst>
```

## 完整文档 / Full Documentation

- [中文](README.zh-CN.md)
- [English](README.en.md)

## License

MIT — see [LICENSE](LICENSE).
