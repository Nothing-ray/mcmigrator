mcmig 内置规则数据说明
======================

本文件夹存放 mcmig 迁移工具的内置规则数据,全部为工具正常运行所必需。
「mcmig doctor」与界面(mcmig gui)启动时会按 manifest.sha256 清单逐个校验
这些文件的完整性(防止杀毒软件误删、下载损坏或被篡改)。

各文件用途
----------
- default_rules.yaml    内置默认分类规则:哪些文件必迁(options.txt/saves 等)、
                        哪些不迁(logs/ 缓存等)、哪些需人工确认。
- rebuild.yaml          版本敏感文件清单(config/fml.toml 等):与 NeoForge/硬件
                        版本绑定,跨版本迁移高危,默认不迁、让新版本自动重建。
- whitelist.yaml        「无 .bak 但属于玩家偏好」的配置白名单
                        (如 iris.properties、jade/jei 的偏好文件)。
- mod_config_map.yaml   mod → 其配置目录的映射,用于识别"孤儿配置"
                        (对应 mod 已不在新版本中,迁过去无意义)。
- manifest.sha256       上述 yaml 的 SHA-256 完整性清单(每行"校验值  文件名")。

删除/修改的后果
--------------
- 删除或改动任一 yaml:mcmig doctor 报「缺失/损坏」,迁移计划的分类判定退化;
  mcmig gui 启动自检不通过会直接拒绝启动。
- 删除 manifest.sha256:doctor 与界面启动自检报「清单文件缺失」。
- 恢复方法:重新下载同版本发行包,用其中同名文件覆盖本文件夹;
  或整个重新解压/重装 mcmig。

说明:本文件(README.txt)是给人看的说明,不参与完整性校验,
删除它不影响任何功能;但建议保留,方便日后对照。
