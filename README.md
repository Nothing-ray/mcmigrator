# mcmigrator

[中文](README.zh-CN.md) | [English](README.en.md)

> Minecraft 整合包版本迁移工具:扫描 / 比对 / 计划 / 执行 / 换包 / 本地向导——在同一整合包的版本隔离文件夹之间迁移玩家状态。
>
> A Minecraft modpack version-migration tool — scan, diff, plan, migrate, modpack swap, plus a local web wizard.

## 下载 / Download

发行物在 [Releases](https://github.com/Nothing-ray/mcmigrator/releases) 页(附 `SHA256SUMS.txt` 校验清单):

| 资产 | 形态 | 说明 |
|---|---|---|
| `mcmig-<版本>-win-x64.zip` | onedir 绿色目录(推荐) | 解压即用:含 `mcmig.exe`(CLI)与 `mcmig-gui.exe`(窗口);启动快、体积省 |
| `mcmig-gui-<版本>-win-x64.exe` | onefile 单文件(尝鲜) | 单文件窗口版;每次启动解压到临时目录,较慢 |

下载后校验:`certutil -hashfile <文件> SHA256`,与 `SHA256SUMS.txt` 中的值比对。

## 更新 / Update

- CLI:`mcmig update [--check]` —— 检查 → 下载 → SHA256 校验 → 暂存,再按提示**手动替换**(按安装形态:onedir 解压覆盖程序目录并保留 `data/config.toml`;onefile 替换现有 `mcmig-gui*.exe`;均先退出旧版、替换后重启)。
- GUI:步①「关于 / 更新」面板走同一流程(可取消、刷新可恢复)。
- 行为约定:工具**不会自动运行下载的文件**;完成态显示「已下载,尚未应用」并给出替换指引。自动应用更新随后续版本提供,当前为下载-校验-手动替换。

## 快速上手 / Quick Start

```bash
pip install -e .            # 源码运行;窗口壳需 pip install -e ".[gui]"
mcmig scan <version>
mcmig diff <src> <dst>
mcmig plan <src> <dst> && mcmig migrate <src> <dst>
```

## 完整文档 / Full Documentation

- [中文](README.zh-CN.md)
- [English](README.en.md)

## License

MIT — see [LICENSE](LICENSE).
