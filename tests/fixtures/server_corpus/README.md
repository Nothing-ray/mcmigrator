# 服务端场景回归语料（脱敏版）

来源：2026-09 两轮真实生产服务器（NeoForge 21.1.249 / MC 1.21.1）换装实测，
由服务器维护 Agent 采集（原始交付包见 `Reference/observations/mcmigrator_服务端测试_*/`，
该目录不入库；本目录为其可入库子集）。

## 内容

| 文件 | 原件 | 说明 |
|---|---|---|
| `20260908/snapshot_before.json` | snapshot_9.4-换包前.json | 一轮换装前（1040 文件，旧 world 269 个） |
| `20260908/snapshot_after.json` | snapshot_9.8-换装后.json | 一轮换装后（919 文件，删 6 mod/备份目录污染） |
| `20260912/snapshot_9_8_player.json` | snapshot_9.8玩家档_换9.11前.json | 二轮换装前（1450 文件，含 4 天玩家 world 561 个） |
| `20260912/snapshot_9_11_fresh.json` | snapshot_9.11-换装后.json | 二轮换装后（802 文件，删档重建 + 7 mod 升级） |

脱敏：`game_root` 字段替换为 `C:\fixture\sanitized`（原为测试机个人路径）；
其余内容（相对路径/尺寸/MD5）与原件逐字节一致。

## 覆盖的场景价值

- **F1**：`world/**`/`server.properties` 等服务端核心资产 → 必迁（新规则后 to_migrate 0→257/550）
- **F4**：7 对同 mod 跨版本升级 jar（waystones/sophisticatedbackpacks 等）+ 1 对同字节改名 jar
- **F5**：`mods_9.4_旧/` 运维备份目录 118 jar（一轮 dst 侧污染 / 二轮 src 侧污染）
- **F2**：被删 mod 的孤儿 config（二轮删除态 → candidate；一轮保留态 → identical）
- 同尺寸不同内容的手改 config（alexscaves/infernalmobs/scguns）→ 验证内容哈希而非尺寸判等

消费方：`tests/test_corpus_regression.py`（六桶计数 + 关键路径 spot check）。
