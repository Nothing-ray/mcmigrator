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
    "err_no_game_root.why": "请先在配置中指定游戏根目录(含 versions/ 的客户端根目录)",
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
    "job_err_failed.what": "任务失败",
    # --- 迁移完成 PCL 提醒(与 CLI `_cmd_migrate` 文案一致) ---
    "reminder.line1": "迁移完成。若要让启动器默认打开新版本,需同步两处配置:",
    "reminder.line_pcl_ini": "  1. {pcl_ini} 的 Version: 行 → 改为 {dst}",
    "reminder.line_setup_ini": "  2. {setup_ini} 的 LaunchVersionSelect: 行 → 改为 {dst}",
    "reminder.line_tail": "工具不代改启动器配置(改错会导致无法启动任何版本),请手动确认后修改。",
}
