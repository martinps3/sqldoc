"""Turn a SELECT statement into structured facts.

The output is deliberately descriptive rather than clever: what the query
returns, where each column comes from, what it reads, how it joins and what it
filters on. No opinions, no scoring - just the things a non-technical reader
needs in order to understand a report without reading SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from .lexer import (
    find_top_level,
    normalise_ws,
    split_statements,
    split_top_level,
    strip_comments,
)

# Clause keywords in the order they may appear in a SELECT.
_CLAUSES = [
    "select",
    "from",
    "where",
    "group by",
    "having",
    "qualify",
    "order by",
    "limit",
    "offset",
    "fetch",
]

_JOIN_RE = re.compile(
    r"\b((?:inner|left|right|full|cross)?\s*(?:outer\s+)?join)\b",
    re.IGNORECASE,
)

_AGGREGATES = ("sum", "count", "avg", "min", "max", "median", "stddev", "array_agg")


@dataclass
class OutputColumn:
    name: str
    expression: str
    sources: list[str] = field(default_factory=list)
    is_aggregate: bool = False
    note: str = ""


@dataclass
class SourceTable:
    name: str
    alias: str = ""
    kind: str = "table"  # table | cte | subquery


@dataclass
class Join:
    kind: str
    target: str
    condition: str = ""


@dataclass
class Stage:
    """A CTE, or the final SELECT."""
    name: str
    output_columns: list[OutputColumn] = field(default_factory=list)
    sources: list[SourceTable] = field(default_factory=list)
    joins: list[Join] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    having: list[str] = field(default_factory=list)
    order_by: list[str] = field(default_factory=list)
    is_distinct: bool = False
    row_limit: str = ""


@dataclass
class QueryDoc:
    name: str
    stages: list[Stage] = field(default_factory=list)
    base_tables: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)

    @property
    def final(self) -> Stage | None:
        return self.stages[-1] if self.stages else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["output_columns"] = [asdict(c) for c in self.final.output_columns] if self.final else []
        return d


# --------------------------------------------------------------------------
# clause splitting
# --------------------------------------------------------------------------
def _split_clauses(select_sql: str) -> dict[str, str]:
    """Map clause name -> clause body for one SELECT."""
    positions = []
    for kw in _CLAUSES:
        idx = find_top_level(select_sql, kw)
        if idx >= 0:
            positions.append((idx, kw))
    positions.sort()

    clauses: dict[str, str] = {}
    for n, (idx, kw) in enumerate(positions):
        start = idx + len(kw)
        end = positions[n + 1][0] if n + 1 < len(positions) else len(select_sql)
        clauses[kw] = select_sql[start:end].strip()
    return clauses


def _extract_ctes(sql: str) -> tuple[list[tuple[str, str]], str]:
    """Return ([(cte_name, cte_body)], remaining_sql)."""
    if find_top_level(sql, "with") != 0:
        return [], sql

    body = sql[4:].lstrip()
    ctes = []

    while True:
        m = re.match(r"([A-Za-z_][\w$]*)\s+as\s*\(", body, re.IGNORECASE)
        if not m:
            break
        name = m.group(1)
        open_idx = m.end() - 1

        depth = 0
        close_idx = -1
        for i in range(open_idx, len(body)):
            if body[i] == "(":
                depth += 1
            elif body[i] == ")":
                depth -= 1
                if depth == 0:
                    close_idx = i
                    break
        if close_idx < 0:
            break

        ctes.append((name, body[open_idx + 1:close_idx].strip()))
        rest = body[close_idx + 1:].lstrip()
        if rest.startswith(","):
            body = rest[1:].lstrip()
            continue
        body = rest
        break

    return ctes, body


# --------------------------------------------------------------------------
# piece parsers
# --------------------------------------------------------------------------
_ALIAS_RE = re.compile(r"\s+as\s+([A-Za-z_\"\[][\w$\"\]]*)\s*$", re.IGNORECASE)


def _parse_output_column(item: str, known_aliases: dict[str, str]) -> OutputColumn:
    expr = normalise_ws(item)

    name = ""
    m = _ALIAS_RE.search(expr)
    if m:
        name = m.group(1).strip('"[]')
        body = expr[: m.start()].strip()
    else:
        # trailing bare alias: "table.col alias"
        parts = expr.rsplit(" ", 1)
        if (
            len(parts) == 2
            and re.fullmatch(r"[A-Za-z_\"\[][\w$\"\]]*", parts[1])
            and not parts[1].lower() in ("end", "desc", "asc", "null")
            and not parts[0].rstrip().endswith((",", "(", "+", "-", "*", "/"))
        ):
            name = parts[1].strip('"[]')
            body = parts[0].strip()
        else:
            body = expr

    if not name:
        if "." in body and re.fullmatch(r"[\w$\"\[\]]+\.[\w$\"\[\]]+", body):
            name = body.split(".")[-1].strip('"[]')
        elif re.fullmatch(r"[A-Za-z_][\w$]*", body):
            name = body
        else:
            name = "(expression)"

    sources = []
    for ref in re.findall(r"\b([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*)\b", body):
        qualifier, col = ref
        table = known_aliases.get(qualifier.lower(), qualifier)
        entry = f"{table}.{col}"
        if entry not in sources:
            sources.append(entry)

    if not sources and re.fullmatch(r"[A-Za-z_][\w$]*", body):
        sources.append(body)

    lowered = body.lower()
    is_agg = any(re.search(rf"\b{fn}\s*\(", lowered) for fn in _AGGREGATES)

    note = ""
    if re.search(r"\bcase\s+when\b", lowered):
        note = "conditional logic"
    elif re.search(r"\bover\s*\(", lowered):
        note = "window function"
    elif is_agg:
        note = "aggregated"

    return OutputColumn(name=name, expression=body, sources=sources,
                        is_aggregate=is_agg, note=note)


def _parse_from(from_sql: str) -> tuple[list[SourceTable], list[Join]]:
    sources: list[SourceTable] = []
    joins: list[Join] = []

    tokens = _JOIN_RE.split(from_sql)
    first = tokens[0].strip()
    if first:
        for ref in split_top_level(first, ","):
            st = _parse_table_ref(ref)
            if st:
                sources.append(st)

    i = 1
    while i + 1 < len(tokens) + 1 and i < len(tokens):
        kind = normalise_ws(tokens[i]).lower()
        body = tokens[i + 1] if i + 1 < len(tokens) else ""
        on_idx = find_top_level(body, "on")
        condition = ""
        target_sql = body
        if on_idx >= 0:
            target_sql = body[:on_idx]
            condition = normalise_ws(body[on_idx + 2:])
        st = _parse_table_ref(target_sql)
        if st:
            sources.append(st)
            joins.append(Join(kind=kind or "join", target=st.name, condition=condition))
        i += 2

    return sources, joins


def _parse_table_ref(ref: str) -> SourceTable | None:
    ref = normalise_ws(ref).strip()
    if not ref:
        return None

    if ref.startswith("("):
        depth = 0
        end = len(ref)
        for i, ch in enumerate(ref):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        alias = ref[end + 1:].strip()
        alias = re.sub(r"^as\s+", "", alias, flags=re.IGNORECASE).strip('"[] ')
        return SourceTable(name=alias or "(subquery)", alias=alias, kind="subquery")

    parts = ref.split()
    name = parts[0].strip('"[]')
    alias = ""
    if len(parts) >= 3 and parts[1].lower() == "as":
        alias = parts[2].strip('"[]')
    elif len(parts) == 2:
        alias = parts[1].strip('"[]')
    return SourceTable(name=name, alias=alias)


def _split_conditions(text: str) -> list[str]:
    if not text.strip():
        return []
    # collapse newlines/tabs first so " and " matches regardless of formatting
    return [normalise_ws(p) for p in split_top_level(normalise_ws(text), " and ")]


# --------------------------------------------------------------------------
# main entry points
# --------------------------------------------------------------------------
def _analyse_select(name: str, select_sql: str,
                    cte_names: set[str]) -> Stage:
    clauses = _split_clauses(select_sql)
    stage = Stage(name=name)

    from_sql = clauses.get("from", "")
    sources, joins = _parse_from(from_sql)
    for s in sources:
        if s.name.lower() in cte_names:
            s.kind = "cte"
    stage.sources = sources
    stage.joins = joins

    alias_map: dict[str, str] = {}
    for s in sources:
        if s.alias:
            alias_map[s.alias.lower()] = s.name
        alias_map.setdefault(s.name.lower(), s.name)

    select_body = clauses.get("select", "")
    if re.match(r"\s*distinct\b", select_body, re.IGNORECASE):
        stage.is_distinct = True
        select_body = re.sub(r"^\s*distinct\b", "", select_body, flags=re.IGNORECASE)
    m_top = re.match(r"\s*top\s+(\d+)\b", select_body, re.IGNORECASE)
    if m_top:
        stage.row_limit = f"TOP {m_top.group(1)}"
        select_body = select_body[m_top.end():]

    for item in split_top_level(select_body, ","):
        stage.output_columns.append(_parse_output_column(item, alias_map))

    stage.filters = _split_conditions(clauses.get("where", ""))
    stage.having = _split_conditions(clauses.get("having", ""))
    stage.group_by = [normalise_ws(x) for x in split_top_level(clauses.get("group by", ""), ",")]
    stage.order_by = [normalise_ws(x) for x in split_top_level(clauses.get("order by", ""), ",")]

    if clauses.get("limit"):
        stage.row_limit = f"LIMIT {normalise_ws(clauses['limit'])}"

    return stage


def analyse(sql: str, name: str = "query") -> QueryDoc:
    """Analyse the first statement of ``sql`` into a QueryDoc."""
    cleaned = strip_comments(sql).strip()
    statements = split_statements(cleaned)
    if not statements:
        return QueryDoc(name=name)

    statement = statements[0].strip()
    ctes, main = _extract_ctes(statement)
    cte_names = {c[0].lower() for c in ctes}

    doc = QueryDoc(name=name)
    for cte_name, cte_body in ctes:
        doc.stages.append(_analyse_select(cte_name, cte_body, cte_names))
    doc.stages.append(_analyse_select("final", main, cte_names))

    base: list[str] = []
    for stage in doc.stages:
        for s in stage.sources:
            if s.kind == "table" and s.name not in base:
                base.append(s.name)
    doc.base_tables = base

    params = []
    for p in re.findall(r"[:@]([A-Za-z_][\w$]*)", cleaned):
        if p not in params:
            params.append(p)
    doc.parameters = params

    return doc
