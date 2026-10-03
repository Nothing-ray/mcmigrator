"""集中文案字典(原型期妥协,spec §9):server 侧全部用户可见文案在此维护。

原型期只做中文,不做语言切换;分发期加语言切换只动本字典。
页面(HTML/JS)文案由 Task 7 内嵌在 index.html,不经过本文件。
键约定:``<用途>.<段落>``,段落取 what(失败对象,弹窗标题级)/why(原因与建议)。
"""

from __future__ import annotations

STRINGS: dict[str, str] = {
    # --- API 三段式错误(what/why;details 由各调用点补充) ---
    "err_host.what": "拒绝访问",
    "err_host.why": "检测到非本机来源的请求(Host 头校验失败),mcmig 界面仅允许本机访问",
    "err_origin.what": "拒绝访问",
    "err_origin.why": "检测到跨站发起的请求(Origin 校验失败),mcmig 界面仅允许本机页面访问",
    "err_content_type.what": "请求类型不受支持",
    "err_content_type.why": "写操作请求必须以 application/json 提交(跨站页面无法伪造此类型)",
    "err_no_game_root.what": "未配置游戏目录",
    "err_no_game_root.why": "请在上方「游戏根目录」输入框填写整合包客户端根目录(含 versions/ 的文件夹)并保存",
    "err_bad_game_root.what": "游戏根目录不可用",
    "err_bad_game_root.why": "路径不存在或缺少 versions/ 子目录,请填写整合包客户端根目录(如「冒险活动客户端」所在文件夹)后重试",
    "err_version_missing.what": "版本不存在",
    "err_version_missing.why": "指定的版本文件夹不存在,请从版本下拉列表中重新选择",
    "err_version_bad_name.what": "版本名不合法",
    "err_version_bad_name.why": (
        "版本名只能是单个文件夹名(不含路径分隔符/盘符/..),"
        "请从版本下拉列表中重新选择"
    ),
    "err_job_busy.what": "已有任务在执行",
    "err_job_busy.why": "同一时刻只允许一个任务(防止双页面并发写盘),请等当前任务完成后再试",
    "err_job_not_found.what": "任务不存在",
    "err_job_not_found.why": "任务已过期或从未存在,请刷新页面重新开始",
    "err_bad_request.what": "请求参数有误",
    "err_bad_request.why": "请求体缺少必填字段或字段类型不正确,请刷新页面重试",
    "err_internal.what": "服务器内部错误",
    "err_internal.why": "发生未预期的错误,请查看日志面板或重试",
    # --- job 错误事件(SSE error 事件) ---
    "job_err_plan_missing.what": "缺少迁移计划",
    "job_err_plan_missing.why": "未找到已保存的迁移计划文件,请先执行第②步「生成计划」",
    "job_err_plan_corrupt.what": "迁移计划读取失败",
    "job_err_plan_corrupt.why": "计划文件损坏或格式版本不支持,请重新生成计划",
    # --- 审阅有效性(批次I-T3,spec §3.3) ---
    "job_err_plan_persist.what": "迁移计划保存失败",
    "job_err_plan_persist.why": (
        "计划文件写入失败,本次生成的计划不可执行;请检查游戏目录的磁盘空间与写权限后,"
        "重新生成计划"
    ),
    "job_err_plan_id.what": "缺少计划校验标识",
    "job_err_plan_id.why": "请先执行第②步「生成计划」,并从最新的计划页面发起迁移(旧页面请刷新)",
    "job_err_guards.what": "计划审阅守卫未通过",
    "job_err_guards.why": (
        "计划生成后,游戏目录/快照/规则或待迁移文件的状态发生了变化,"
        "为避免「审旧执新」已阻止执行;请重新生成并审阅计划"
    ),
    "job_err_failed.what": "任务失败",
    # --- 防御性收口(修复 A3②):job 线程未产出终态事件即退出的兜底 error ---
    "job_err_aborted.what": "任务异常中止",
    "job_err_aborted.why": (
        "任务线程在产出结果前结束;若为迁移/装包任务,目标实例可能已被部分修改,"
        "请按界面中断清单核对后再重试;若反复出现请查看运行日志"
    ),
    # --- 实例锁(批次I-T5,spec §4.2) ---
    "job_err_instlock.what": "实例被其他 mcmig 进程占用",
    "job_err_instlock.why": (
        "另一个 mcmig 进程正在操作所选源/目标实例,请等待其完成"
        "(或关闭其他 mcmig 窗口/命令)后重试"
    ),
    "notice.instlock_abandoned": (
        "检测到上次异常退出的实例锁,请核对 {jobs_dir} 下的中断记录"
    ),
    # --- 取消/退出(批次I-T6,spec §4.3;W3-T7 扩 swap 装包可取消+取消窗) ---
    "err_cancel_unsupported.what": "该任务不支持取消",
    "err_cancel_unsupported.why": (
        "只有执行迁移(③)与换包装包可以取消;扫描与生成计划阶段很快结束,请稍候"
    ),
    "err_cancel_closed.what": "装包已完成,正在重新规划",
    "err_cancel_closed.why": (
        "此阶段不可取消;若不需要该计划,可在审阅页放弃"
    ),
    "err_cancel_terminal.what": "任务已结束",
    "err_cancel_terminal.why": "任务已完成或失败,没有可取消的内容;请查看任务结果",
    "err_shutdown_busy.what": "有任务正在执行",
    "err_shutdown_busy.why": "请等待任务完成(迁移任务可先取消)后再退出服务",
    # 排空态拒新 job(批次I-W3 T9,评审 v4 P2-2,白名单⑪):窗口关闭边界
    # begin_shutdown 置 draining 后,job 创建端点捕获 StoreDraining → 503
    "err_shutting_down.what": "服务正在退出",
    "err_shutting_down.why": (
        "界面窗口已关闭,服务正在停止,不再接受新任务;如需继续使用,"
        "请重新打开窗口(mcmig-gui)或使用 mcmig gui"
    ),
    # --- 中断记录清除通道(批次I-W3 T3,#9/评审 P2-8) ---
    "err_dismiss_live.what": "任务仍在执行",
    "err_dismiss_live.why": (
        "该记录对应的迁移仍在运行(可能是其他窗口或命令行),完成后再清除"
    ),
    "err_dismiss_failed.what": "该记录无法删除",
    "err_dismiss_failed.why": (
        "文件被其他程序占用或为系统保留名;请关闭占用程序后重试,"
        "或手动删除 jobs/ 下的该文件"
    ),
    # --- journal 写失败停发(批次I-T6 修复 finding 1;重试引导按终审修复 I3 对齐) ---
    "job_err_journal.what": "迁移日志写入失败",
    "job_err_journal.why": (
        "记录迁移进度的日志文件写入失败,已安全停止迁移后续文件,本次迁移未完整执行;"
        "请检查磁盘空间与杀毒软件拦截后,重新生成计划再执行"
        "(计划未锁定,重新生成的计划中已迁移文件会按「内容一致」跳过)"
    ),
    # --- 换包 swap 两阶段(批次I-W3 T7,spec §5.2;评审 P2-3/契约A/契约B) ---
    "err_swap_bad_pack.what": "新整合包目录无效",
    "err_swap_bad_pack.why": (
        "新整合包目录必须包含 mods/ 子目录,请确认选择的目录后重试"
    ),
    "err_swap_preflight_unknown.what": "预检记录不存在或已过期",
    "err_swap_preflight_unknown.why": (
        "预检完成后服务仅保留最近若干条记录,或请求的版本对与预检时不一致;"
        "请重新执行第①步换包预检"
    ),
    "err_swap_overwrite.what": "覆盖清单超出预检结果",
    "err_swap_overwrite.why": (
        "勾选覆盖的 jar 必须来自预检识别的同名冲突清单;请重新预检后再选择"
    ),
    "job_err_swap_preflight.what": "换包预检失败",
    "job_err_swap_preflight.why": (
        "预检未通过(如目标版本缺少版本 json 或缺少源版本快照);"
        "请按提示补齐后重新预检"
    ),
    "job_err_swap_incompat.what": "存在与目标 NeoForge 版本不兼容的 mod",
    "job_err_swap_incompat.why": (
        "装包前复核发现新包仍有不兼容 mod 且未勾选「接受不兼容」;"
        "请勾选接受或更换新包"
    ),
    "job_err_swap_extras.what": "目标存在新包外的残留 jar",
    "job_err_swap_extras.why": (
        "装包前复核发现目标 mods/ 仍有新包外 jar 且未勾选「接受残留」;"
        "请勾选接受或先清理目标 mods/"
    ),
    "job_err_swap_inputs_changed.what": "换包输入已发生变化",
    "job_err_swap_inputs_changed.why": (
        "预检后,目标实例(游戏根/版本对/mods/ 内容或版本 json)发生了变化,"
        "为避免「审旧装新」已阻止装包;请重新执行换包预检"
    ),
    "job_err_swap_replan.what": "装包已完成,但生成换包迁移计划失败",
    "job_err_swap_replan.why": (
        "目标 mods/ 已被修改(装包统计与备份位置见错误详情);"
        "请解决失败原因后重走换包流程,被覆盖的 jar 可从备份目录找回"
    ),
    "job_err_swap_install.what": "装包未完成",
    "job_err_swap_install.why": (
        "装包在首个/中途 jar 处失败(常见:磁盘空间不足、文件被占用);"
        "已改动的 jar 与备份位置见错误详情,请解决后重新执行换包预检"
    ),
    # --- 更新基础档(批次I-W4 T13;spec §4.3.4) ---
    "err_update_source.what": "源码运行模式不支持下载更新",
    "err_update_source.why": (
        "当前为源码运行形态,请 git pull 更新到目标版本;"
        "自动下载仅面向打包发行形态"
    ),
    "err_update_open_outside.what": "拒绝打开暂存目录之外的路径",
    "err_update_open_outside.why": "打开位置仅限本次下载的暂存资产;请从更新面板重新发起",
    "job_err_update.what": "更新下载失败",
    "job_err_update.why": (
        "更新过程发生未预期错误(详情见日志);可重试,"
        "或到项目发布页手动下载替换"
    ),
    # --- 过程提示事件(SSE notice 事件,批次I-T1;页面按需展示/忽略未知型) ---
    "notice.legacy_snapshot": (
        "检测到旧布局快照({path}),本次扫描结果将写入新位置"
        "(游戏根目录/.mcmig/snapshots),旧文件仅作参考,确认无误后可手动清理"
    ),
    # 旧版计划提示(批次I-W3 T1;与 CLI `_cmd_migrate` 的「缺少审阅守卫」提示同文案)
    "notice.review_missing": "计划缺少审阅守卫(旧版生成),建议重跑 plan 启用保护",
    # --- 迁移完成 PCL 提醒(与 CLI `_cmd_migrate` 文案一致) ---
    "reminder.line1": "迁移完成。若要让启动器默认打开新版本,需同步两处配置:",
    "reminder.line_pcl_ini": "  1. {pcl_ini} 的 Version: 行 → 改为 {dst}",
    "reminder.line_setup_ini": "  2. {setup_ini} 的 LaunchVersionSelect: 行 → 改为 {dst}",
    "reminder.line_tail": "工具不代改启动器配置(改错会导致无法启动任何版本),请手动确认后修改。",
    # --- ②审阅页 diff 摘要(批次I-W3 T6,spec §5.1/§3.3 #15) ---
    # 检测范围分级说明(固定文案,随 plan job done 事件的 diff.guard_scope 下发):
    # 说明「本摘要承诺了什么、没承诺什么」,避免把摘要条当全量清单误读
    "review.scope_note": (
        "检测范围说明:已哈希条目全量校验源/目标状态;bulk/mods 代理条目"
        "在其 size+mtime 范围内承诺;「目标不存在」亦为被记录状态"
        "——不承诺发现范围外的任何改动。"
    ),
}
