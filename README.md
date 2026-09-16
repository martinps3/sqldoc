# sqldoc

Explain what a SQL report does, without reading the SQL.

Point it at a `.sql` file and it tells you what the query returns, where each
output column comes from, which tables it reads, how it joins them and what it
filters on. Written for the situation where a scrum master, analyst or support
engineer needs to answer "what's actually in this report?" and the only source
of truth is 300 lines of SQL.

No dependencies. Python 3.9+.

```bash
python -m sqldoc reports/monthly_revenue.sql
python -m sqldoc reports/ --format json --out docs/
```

## Example

Given a query with two CTEs, three joins and a `CASE` expression, you get:

```markdown
## In one line

Returns 7 columns, from sales.customers, sales.products, using monthly_orders,
order_revenue, grouped by mo.order_month, p.category_name, c.region, with 1
filter condition.

## Reads from

- `sales.orders`
- `sales.order_items`
- `sales.customers`
- `sales.products`

## Output columns

| Column         | Comes from                        | Notes              |
| -------------- | --------------------------------- | ------------------ |
| `order_month`  | `monthly_orders.order_month`      |                    |
| `category`     | `sales.products.category_name`    |                    |
| `order_count`  | `monthly_orders.order_id`         | aggregated         |
| `revenue_band` | `order_revenue.gross_revenue`     | conditional logic  |
```

Note `category` resolves through the alias `p` back to `sales.products`. Column
lineage follows aliases rather than reporting whatever letter the author happened
to use.

## What it extracts

- **Output columns** with the expression behind each one, and which source
  columns feed it
- **Base tables** — real tables only, with CTEs and subqueries excluded
- **Joins** — type and `ON` condition
- **Filters** — `WHERE` split into individual conditions
- **Grain** — `GROUP BY`, `DISTINCT`, row limits
- **Parameters** — `:name` and `@name` placeholders
- **CTEs** documented as separate stages

Output is Markdown (for humans) or JSON (for anything else — a search index, a
data catalogue, a retrieval layer for an LLM).

## Design notes

**Why no parser library.** For full dialect coverage you want
[sqlglot](https://github.com/tobymao/sqlglot), and if you're building something
serious you should use it. This is deliberately a zero-dependency tool: it drops
into a locked-down environment with nothing but a Python install, which is
frequently the situation in corporate BI.

The trade-off is real. `sqldoc` uses a depth- and literal-aware scanner rather
than a full grammar. It understands enough structure to be reliable on the
reporting SQL people actually write, and it will not cope with everything.

**What it handles well**

- `SELECT` with CTEs, joins, subqueries in `FROM`
- String literals and comments containing SQL keywords or commas
- Bracketed T-SQL identifiers, quoted identifiers
- Aliases resolved back to source tables
- `DISTINCT`, `TOP n`, `LIMIT`

**What it doesn't**

- `INSERT` / `UPDATE` / `MERGE` — reporting queries only
- Set operations (`UNION`, `INTERSECT`) are not split into branches
- `SELECT *` is reported as an opaque expression; it can't expand without a
  schema
- Deeply nested correlated subqueries are summarised, not traced

It errs toward saying less rather than guessing, because a documentation tool
that invents lineage is worse than no tool.

## Tests

```bash
python tests/test_sqldoc.py
```

29 tests covering the lexer (comment stripping, literal-safe splitting,
depth-aware keyword search), lineage resolution, CTE handling, join parsing,
rendering and edge cases.

## Layout

```
src/sqldoc/
    lexer.py      comment stripping, depth-aware splitting
    analyze.py    clause parsing, lineage, the QueryDoc model
    render.py     Markdown and JSON output
    cli.py        command line entry point
tests/
examples/
```

## Licence

MIT.
