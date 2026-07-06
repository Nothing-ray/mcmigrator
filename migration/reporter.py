"""报告渲染:rich 终端 + JSON。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from .differ import DiffItem, DiffReport

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
