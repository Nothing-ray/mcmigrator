# 服务端场景回归语料（脱敏版）

来源：2026-09 多轮真实生产服务器（首两轮 NeoForge 21.1.249 / MC 1.21.1）换装实测，
由服务器维护 Agent 采集（原始交付包见 `Reference/observations/mcmigrator_服务端测试_*/`，
该目录不入库；本目录为其可入库子集）。

## 内容

| 文件 | 原件 | 说明 |
|---|---|---|
| `20260908/snapshot_before.json` | snapshot_9.4-换包前.json | 一轮换装前（1040 文件，旧 world 269 个） |
| `20260908/snapshot_after.json` | snapshot_9.8-换装后.json | 一轮换装后（919 文件，删 6 mod/备份目录污染） |
| `20260912/snapshot_9_8_player.json` | snapshot_9.8玩家档_换9.11前.json | 二轮换装前（1450 文件，含 4 天玩家 world 561 个） |
| `20260912/snapshot_9_11_fresh.json` | snapshot_9.11-换装后.json | 二轮换装后（802 文件，删档重建 + 7 mod 升级） |
| `20260913/r3_live.json` | r3-live.snapshot.json | 三轮 20.4h 运行后（899 文件，F10 演化对 dst） |
| `20260913/r3_post.json` | r3-post.snapshot.json | 三轮 agent 微调后（899 文件，F11 漂移对） |
| `20260914/r4_pre.json` | r4-pre.snapshot.json | 四轮换装前（902 文件，前接三轮微调态的 12.2h 空转 = F15 空转对 dst） |
| `20260914/r4_post.json` | r4-post.snapshot.json | 四轮换装后（903 文件，5 对升级 = F14 黄金对） |
| `20260914b/r5_pre.json` | r5-pre.snapshot.json | 五轮换装前（905 文件） |
| `20260914b/r5_post.json` | r5-post.snapshot.json | 五轮换装后（909 文件，enigmaticlegacyplus 同名 rebuilt = F17 黄金对） |
| `20260919/r6_pre.json` | r6-pre.snapshot.json | 六轮换装前（1320 文件，承接五轮后近 5 天运行态） |
| `20260919/r6_post.json` | r6-post.snapshot.json | 六轮换装后（1315 文件，6 对升级含 cobblestone 前缀对 + hs_err/replay 崩溃残留 = F18/F19） |
| `20260920/r7_pre.json` | 未随包，按 `旧件指纹.md` 合成 | 七轮换装前（mods-only 5 件，md5=null 分层快照语义） |
| `20260920/r7_post.json` | 未随包，按 `旧件指纹.md` 合成 | 七轮换装后（5 件：3 对升级 + compat 同版变体 rebuilt = F20 黄金对） |
| `20260921/r8_pre.json` | 未随包，按报告明细合成 | 八轮换装前（mods-only 5 件：酒馆/field/lootr/灾变 + 1 shared） |
| `20260921/r8_post.json` | 未随包，按报告明细合成 | 八轮换装后（4 换 4：lootr 一级 + 酒馆三级 F21 + field 四级 F22 + 灾变同名 rebuilt） |
| `20260923/r9_pre.json` | 未随包，按报告明细合成 | 九轮换装前（mods-only 6 件：5 待换 + 1 shared） |
| `20260923/r9_post.json` | 未随包，按报告明细合成 | 九轮换装后（5 换 5 纯一级升级，swap 视角配对不丢 = F23/F26 黄金对） |

脱敏：`game_root` 字段替换为 `C:\fixture\sanitized`（原为测试机个人路径）；
其余内容（相对路径/尺寸/MD5）与原件逐字节一致。
上表「原件」列为「未随包…合成」的七/八/九轮（`20260920/`、`20260921/`、`20260923/`）为 mods-only
合成夹具，非逐字节脱敏快照：md5 留空（分层快照语义），仅 rebuilt 件给 size 差。

## 覆盖的场景价值

- **F1**：`world/**`/`server.properties` 等服务端核心资产 → 必迁（新规则后 to_migrate 0→257/550）
- **F4**：7 对同 mod 跨版本升级 jar（waystones/sophisticatedbackpacks 等）+ 1 对同字节改名 jar
- **F5**：`mods_9.4_旧/` 运维备份目录 118 jar（一轮 dst 侧污染 / 二轮 src 侧污染）
- **F2**：被删 mod 的孤儿 config（二轮删除态 → candidate；一轮保留态 → identical）
- 同尺寸不同内容的手改 config（alexscaves/infernalmobs/scguns）→ 验证内容哈希而非尺寸判等
- **F10/F11**：同包演化全量可解释（only_in_dst=91）+ 纯配置漂移最小对照（candidate 恰=2）
- **F12**：server.properties 的 Properties.store 规范化噪声在 R1/R2 中与真实改动混合（无 ctx 字节判定锚定）

消费方：`tests/test_corpus_regression.py`（六桶计数 + 关键路径 spot check）。
