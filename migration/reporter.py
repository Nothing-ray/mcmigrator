"""报告渲染:rich 终端 + JSON。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from .differ import DiffItem, DiffReport
from .plan import Behavior, MigrationPlan, ORIGIN_REGISTRY

BUCKETS = ["to_migrate", "candidate", "mods", "only_in_dst", "identical", "never"]
BUCKET_TITLE = {
    "to_migrate": "✅ 必迁移(to_migrate)",
    "candidate": "◐ 待确认(candidate)",
    "mods": "📦 Mod 变化(mods)",
    "only_in_dst": "📍 目标自带(only_in_dst)",
    "identical": "⏭ 一致(identical)",
    "never": "⛔ 不迁移(never)",
}


@dataclass
class ReportOptions:
    """报告可见性控制。"""

    show_identical: bool = False
    show_never: bool = False
    mods_only: bool = False
    category: str | None = None  # 仅显示某一桶


class DiffReporter:
    """把 DiffReport 渲染成 rich 终端表格或 JSON。"""

    def __init__(self, report: DiffReport, *, src_version: str, dst_version: str) -> None:
        self.report = report
        self.src_version = src_version
        self.dst_version = dst_version

    def _item_dict(self, item: DiffItem) -> dict:
        return {
            "path": item.path,
            "note": item.note,
            "src_size": item.src.size if item.src else None,
            "dst_size": item.dst.size if item.dst else None,
        }

    def to_json(self) -> str:
        """生成可解析的 JSON 报告(含 summary 与各桶明细)。"""
        payload = {
            "src": self.src_version,
            "dst": self.dst_version,
            "summary": {b: len(getattr(self.report, b)) for b in BUCKETS},
            "buckets": {b: [self._item_dict(i) for i in getattr(self.report, b)] for b in BUCKETS},
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _visible_buckets(self, opts: ReportOptions) -> list[str]:
        if opts.category:
            return [opts.category] if opts.category in BUCKETS else []
        buckets = ["to_migrate", "candidate", "mods", "only_in_dst"]
        if opts.show_identical:
            buckets.append("identical")
        if opts.show_never:
            buckets.append("never")
        if opts.mods_only:
            buckets = ["mods"]
        return buckets

    def render(self, opts: ReportOptions, console: Console | None = None) -> None:
        """渲染 rich 终端报告。"""
        console = console or Console()
        console.print(f"[bold]diff:[/] [cyan]{self.src_version}[/] → [cyan]{self.dst_version}[/]")
        summary = ", ".join(
            f"{BUCKET_TITLE[b].split('(')[0]}{len(getattr(self.report, b))}" for b in BUCKETS
        )
        console.print(f"[dim]汇总: {summary}[/]")
        for b in self._visible_buckets(opts):
            items = getattr(self.report, b)
            if not items:
                continue
            tbl = Table(title=BUCKET_TITLE[b], title_style="bold")
            tbl.add_column("路径")
            tbl.add_column("标记", style="dim")
            for it in items:
                tbl.add_row(it.path, it.note)
            console.print(tbl)


def _default_visible_origins() -> list[str]:
    """延迟计算默认可见 origin(运行时反映注册表最新状态)。"""
    return [k for k, m in ORIGIN_REGISTRY.items() if m.default_visible]


@dataclass
class PlanOptions:
    """Plan 报告可见性控制。"""

    show_skip: bool = False
    category: str | None = None
    visible_actions: set[str] | None = None  # 预留(key 为 origin 字符串)


class PlanReporter:
    """把 MigrationPlan 渲染成 rich 终端表格或 JSON(= plan 文件内容)。"""

    def __init__(self, plan: MigrationPlan, *, src_version: str, dst_version: str) -> None:
        self.plan = plan
        self.src_version = src_version
        self.dst_version = dst_version

    def to_json(self) -> str:
        """JSON 输出 = plan 文件内容。"""
        payload = {
            "tool_version": self.plan.tool_version,
            "plan_format": self.plan.plan_format,
            "src": self.src_version,
            "dst": self.dst_version,
            "generated_at": self.plan.generated_at,
            "summary": self.plan.summary(),
            "actions": [r.to_dict() for r in self.plan.actions],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _visible_origins(self, opts: PlanOptions) -> list[str]:
        if opts.category:
            return [opts.category] if opts.category in ORIGIN_REGISTRY else []
        if opts.visible_actions is not None:
            return [o for o in ORIGIN_REGISTRY if o in opts.visible_actions]
        if opts.show_skip:
            return list(ORIGIN_REGISTRY.keys())
        return _default_visible_origins()

    def render(self, opts: PlanOptions, console: Console | None = None) -> None:
        """渲染 rich 终端报告(按 origin 分组)。"""
        console = console or Console()
        console.print(
            f"[bold]plan:[/] [cyan]{self.src_version}[/] → [cyan]{self.dst_version}[/]"
        )
        summary = self.plan.summary()
        # 仅显示非零 origin(全显示会过长)
        summary_str = ", ".join(
            f"{ORIGIN_REGISTRY[o].title}{summary.get(o, 0)}"
            for o in ORIGIN_REGISTRY
            if summary.get(o, 0) > 0
        )
        console.print(f"[dim]汇总: {summary_str}[/]")
        for origin_key in self._visible_origins(opts):
            items = [r for r in self.plan.actions if r.origin.value == origin_key]
            if not items:
                continue
            meta = ORIGIN_REGISTRY[origin_key]
            # COPY 类 origin 按 backup_target 推导 new/modified 子计数(SKIP/ASK 类不拆)。
            # behavior 来自注册表单一来源(2D 模型 1:1 不变量),非硬编码 origin 列表。
            if meta.behavior == Behavior.COPY:
                new_count = sum(1 for r in items if not r.backup_target)
                mod_count = sum(1 for r in items if r.backup_target)
                title = f"{meta.title} ({len(items)}: 新增 {new_count} · 覆盖 {mod_count} 已备份)"
            else:
                title = f"{meta.title} ({len(items)})"
            tbl = Table(title=title, title_style="bold")
            tbl.add_column("路径")
            tbl.add_column("置信度", style="dim")
            tbl.add_column("原因", style="dim")
            if meta.show_backup:
                tbl.add_column("备份目标")
            for r in items:
                row = [r.path, r.confidence, r.reason]
                if meta.show_backup:
                    row.append(r.backup_target or "")
                tbl.add_row(*row)
            console.print(tbl)
        if not opts.show_skip and not opts.category:
            console.print("[dim]默认隐藏 skip 类 origin,用 --show-skip 查看[/]")
