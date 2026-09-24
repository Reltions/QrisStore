"""Saving research reports to disk."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .base import ToolContext, tool


@tool(
    "save_report",
    "Save a finished research report as a Markdown file in the reports folder. Write the complete report: "
    "title, summary, findings with inline citations, data tables, limitations, and a numbered reference "
    "list with DOIs/URLs. Embed drawings made with draw_molecule as ![name](file.svg).",
    {
        "title": {"type": "string", "minLength": 1},
        "markdown": {"type": "string", "minLength": 1, "description": "The full report body in Markdown."},
    },
    ["title", "markdown"],
)
def save_report(title: str, markdown: str, ctx: ToolContext) -> dict[str, Any]:
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:60] or "report"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = ctx.output_dir / f"{stamp}-{slug}.md"
    body = markdown if markdown.lstrip().startswith("#") else f"# {title}\n\n{markdown}"
    path.write_text(body.rstrip() + "\n", encoding="utf-8")
    return {"saved": str(path), "characters": len(body)}
