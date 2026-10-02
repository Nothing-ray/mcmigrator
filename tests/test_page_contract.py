"""index.html 源码契约测试(终审修复 C1/I3)。

无 JS 测试基建下的接缝钉:页面行为无法在 pytest 中执行,改为对页面源码断言
**语义特征**(正则特征而非整段字符串比对)——锁定三件事:
1. migrate 请求体必须携带 plan_id(批次I-T3:缺失必被 plan_id_missing 拒绝);
2. plan job 的 onDone 必须捕获 ev.plan_id,且 persisted===false 分支渲染三段式
   错误并禁用「执行迁移」(未落盘的计划不可执行);
3. 部分执行后的三处重试引导均以「重新生成计划」为前提(I3:直接重跑会被目标
   状态校验阻断,文案不得再暗示可直接重执行)。

读取路径与 server._read_index_html 同源(importlib.resources),打包形态同测。
"""

from __future__ import annotations

import re
from importlib import resources

_PAGE = resources.files("migration.gui").joinpath("index.html").read_text(encoding="utf-8")


def test_migrate_request_body_carries_plan_id() -> None:
    """C1:POST /api/migrate 请求体构造必须含 plan_id 键(语义特征,非整段比对)。"""
    m = re.search(r'postJson\("/api/migrate",\s*\{([^}]*)\}', _PAGE)
    assert m, "页面必须以 postJson 构造 /api/migrate 请求"
    body = m.group(1)
    for key in ("src", "dst", "ask_yes", "dry_run", "plan_id"):
        assert re.search(rf"\b{key}\s*:", body), f"migrate 请求体缺少 {key} 键"


def test_plan_done_captures_plan_id() -> None:
    """C1:onDone 必须把 done 事件的 plan_id 存入页面状态(migrate 请求的数据源)。"""
    assert re.search(r"planId\s*=\s*ev\.plan_id", _PAGE), "onDone 应捕获 ev.plan_id"
    # 状态声明存在且初始为 null(页面刷新丢失后依赖 plan_id_missing 错误引导重新生成)
    assert re.search(r"var\s+planId\s*=\s*null", _PAGE)
    # 重新生成计划时旧 planId 作废(resetStep2 内)
    reset = re.search(r"function\s+resetStep2\(\)\s*\{(.*?)\n\}", _PAGE, re.S)
    assert reset and re.search(r"planId\s*=\s*null", reset.group(1))


def test_plan_done_persisted_false_blocks_execution() -> None:
    """C1:persisted===false 分支存在——渲染三段式错误、禁用执行按钮并提前返回。"""
    m = re.search(r"if\s*\(ev\.persisted\s*===\s*false\)\s*\{(.*?)return;", _PAGE, re.S)
    assert m, "onDone 必须有 persisted===false 分支且提前 return(不渲染空分组)"
    branch = m.group(1)
    assert re.search(r'renderError\(\$\("step2-error"\),\s*ev\.what,\s*ev\.why,\s*ev\.details\)',
                     branch), "persisted:false 须以 done 事件自带的三段式字段渲染错误"
    assert re.search(r'\$\("btn-migrate"\)\.disabled\s*=\s*true', branch), \
        "persisted:false 须禁用「执行迁移」按钮"
    assert re.search(r"planId\s*=\s*null", branch), "persisted:false 须作废计划标识"


def test_retry_guidance_requires_replan() -> None:
    """I3:三处部分执行后的重试引导(失败清单/取消注记/中断横幅)均以重新生成计划为前提。"""
    assert "重新生成计划再执行" in _PAGE        # 失败清单注记(步③)
    assert "重新生成计划后再执行" in _PAGE      # 取消终态注记(步③)
    assert "重新生成计划再继续迁移" in _PAGE    # 中断横幅(页面加载)
    # 旧措辞「可返回重新执行」(无前提的直接重执行暗示)必须清除
    assert "可返回重新执行" not in _PAGE


def test_retry_guidance_keeps_identical_skip_explanation() -> None:
    """I3:保留 identical 跳过说明,但前提明确挂在「重新生成的计划」上。"""
    assert _PAGE.count("按「内容一致」跳过") >= 3  # 失败清单/取消注记/中断横幅三处
    for m in re.finditer(r"[^\n]*按「内容一致」跳过[^\n]*", _PAGE):
        assert "重新生成" in m.group(0), f"identical 跳过说明须以重新 plan 为前提: {m.group(0)}"


def test_shutdown_copy_does_not_claim_exit() -> None:
    """W2.5 复审 B6:退出按钮成功文案不得声称「服务已退出」。

    停机标志已置但进程退出由启动方轮询执行(T10 接线前仍在运行)——假承诺
    会让玩家关页后留下存活的控制台进程。
    """
    branch = re.search(r"function\s+shutdownServer[\s\S]*?\}\)\.catch", _PAGE)
    assert branch, "页面必须有 shutdownServer 函数"
    body = branch.group(0)
    assert "已请求退出" in body            # 陈述请求而非完成
    assert "服务已退出" not in body        # 假承诺清除


def test_hidden_attribute_beats_display_rules() -> None:
    """W2.6 复审附带(预存缺陷):`[hidden]{display:none!important}` 必须存在。

    .warn{display:inline-block} 等类样式会压制 UA 对 hidden 属性的默认
    display:none(MDN hidden 属性说明)——源与目标不同时「不能是同一个版本」
    警告仍显示(reviewer 实测)。全局 [hidden] 提升 + !important 一处收口。
    """
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important;?\s*\}", _PAGE), (
        "CSS 须含 [hidden]{display:none!important} 压制类样式对 hidden 属性的覆盖"
    )
