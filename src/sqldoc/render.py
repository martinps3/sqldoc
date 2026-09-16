"""Render a QueryDoc as Markdown or JSON."""

from __future__ import annotations

import json

from .analyze import QueryDoc, Stage


def to_json(doc: QueryDoc, indent: int = 2) -> str:
    return json.dumps(doc.to_dict(), indent=indent)


def _plain_english(stage: Stage) -> str:
    bits = []
    n = len(stage.output_columns)
    bits.append(f"Returns {n} column{'s' if n != 1 else ''}")

    tables = [s.name for s in stage.sources if s.kind == "table"]
    ctes = [s.name for s in stage.sources if s.kind == "cte"]
    if tables:
        bits.append("from " + ", ".join(tables))
    if ctes:
        bits.append("using " + ", ".join(ctes))
    if stage.is_distinct:
        bits.append("deduplicated")
    if stage.group_by:
        bits.append("grouped by " + ", ".join(stage.group_by))
    if stage.filters:
        bits.append(f"with {len(stage.filters)} filter condition"
                    f"{'s' if len(stage.filters) != 1 else ''}")
    return ", ".join(bits) + "."


def to_markdown(doc: QueryDoc) -> str:
    L: list[str] = []
    L.append(f"# {doc.name}")
    L.append("")

    final = doc.final
    if final:
        L.append("## In one line")
        L.append("")
        L.append(_plain_english(final))
        L.append("")

    if doc.base_tables:
        L.append("## Reads from")
        L.append("")
        for t in doc.base_tables:
            L.append(f"- `{t}`")
        L.append("")

    if doc.parameters:
        L.append("## Parameters")
        L.append("")
        for p in doc.parameters:
            L.append(f"- `{p}`")
        L.append("")

    if final and final.output_columns:
        L.append("## Output columns")
        L.append("")
        L.append("| Column | Comes from | Notes |")
        L.append("| --- | --- | --- |")
        for c in final.output_columns:
            src = ", ".join(f"`{s}`" for s in c.sources) or "—"
            note = c.note or ""
            L.append(f"| `{c.name}` | {src} | {note} |")
        L.append("")

    if final and final.joins:
        L.append("## Joins")
        L.append("")
        L.append("| Type | Table | On |")
        L.append("| --- | --- | --- |")
        for j in final.joins:
            cond = f"`{j.condition}`" if j.condition else "—"
            L.append(f"| {j.kind.upper()} | `{j.target}` | {cond} |")
        L.append("")

    if final and final.filters:
        L.append("## Filters")
        L.append("")
        for f in final.filters:
            L.append(f"- `{f}`")
        L.append("")

    if final and (final.group_by or final.is_distinct or final.row_limit):
        L.append("## Grain and limits")
        L.append("")
        if final.is_distinct:
            L.append("- `DISTINCT` — duplicate rows removed")
        for g in final.group_by:
            L.append(f"- Grouped by `{g}`")
        if final.row_limit:
            L.append(f"- Row limit: `{final.row_limit}`")
        L.append("")

    multi = [s for s in doc.stages if s.name != "final"]
    if multi:
        L.append("## Intermediate steps")
        L.append("")
        for s in multi:
            L.append(f"### `{s.name}`")
            L.append("")
            L.append(_plain_english(s))
            L.append("")

    return "\n".join(L).rstrip() + "\n"
