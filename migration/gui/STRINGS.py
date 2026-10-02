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
    "err_no_game_root.what": "未配置游戏目录",
    "err_no_game_root.why": "请在上方「游戏根目录」输入框填写整合包客户端根目录(含 versions/ 的文件夹)并保存",
    "err_bad_game_root.what": "游戏根目录不可用",
    "err_bad_game_root.why": "路径不存在或缺少 versions/ 子目录,请填写整合包客户端根目录(如「冒险活动客户端」所在文件夹)后重试",
    "err_version_missing.what": "版本不存在",
    "err_version_missing.why": "指定的版本文件夹不存在,请从版本下拉列表中重新选择",
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
    "job_err_preflight.what": "迁移执行预检未通过",
    "job_err_preflight.why": (
        "计划已执行、快照过期、版本文件夹缺失或目标文件疑似被占用等防护检查未通过,"
        "请按各阻断项提示处理后重试"
    ),
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
    # --- 实例锁(批次I-T5,spec §4.2) ---
    "job_err_instlock.what": "实例被其他 mcmig 进程占用",
    "job_err_instlock.why": (
        "另一个 mcmig 进程正在操作所选源/目标实例,请等待其完成"
        "(或关闭其他 mcmig 窗口/命令)后重试"
    ),
    "notice.instlock_abandoned": (
        "检测到上次异常退出的实例锁,请核对 {jobs_dir} 下的中断记录"
    ),
    # --- 取消/退出(批次I-T6,spec §4.3) ---
    "err_cancel_unsupported.what": "该任务不支持取消",
    "err_cancel_unsupported.why": (
        "只有执行迁移(③)可以取消;扫描与生成计划阶段很快结束,请稍候"
    ),
    "err_cancel_terminal.what": "任务已结束",
    "err_cancel_terminal.why": "任务已完成或失败,没有可取消的内容;请查看任务结果",
    "err_shutdown_busy.what": "有任务正在执行",
    "err_shutdown_busy.why": "请等待任务完成(迁移任务可先取消)后再退出服务",
    # --- journal 写失败停发(批次I-T6 修复 finding 1;重试引导按终审修复 I3 对齐) ---
    "job_err_journal.what": "迁移日志写入失败",
    "job_err_journal.why": (
        "记录迁移进度的日志文件写入失败,已安全停止迁移后续文件,本次迁移未完整执行;"
        "请检查磁盘空间与杀毒软件拦截后,重新生成计划再执行"
        "(计划未锁定,重新生成的计划中已迁移文件会按「内容一致」跳过)"
    ),
    # --- 过程提示事件(SSE notice 事件,批次I-T1;页面按需展示/忽略未知型) ---
    "notice.legacy_snapshot": (
        "检测到旧布局快照({path}),本次扫描结果将写入新位置"
        "(游戏根目录/.mcmig/snapshots),旧文件仅作参考,确认无误后可手动清理"
    ),
    # --- 迁移完成 PCL 提醒(与 CLI `_cmd_migrate` 文案一致) ---
    "reminder.line1": "迁移完成。若要让启动器默认打开新版本,需同步两处配置:",
    "reminder.line_pcl_ini": "  1. {pcl_ini} 的 Version: 行 → 改为 {dst}",
    "reminder.line_setup_ini": "  2. {setup_ini} 的 LaunchVersionSelect: 行 → 改为 {dst}",
    "reminder.line_tail": "工具不代改启动器配置(改错会导致无法启动任何版本),请手动确认后修改。",
}
