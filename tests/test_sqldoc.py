"""Tests for sqldoc. Run with:  python -m unittest discover -s tests -t ."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from sqldoc import analyse, to_markdown, to_json          # noqa: E402
from sqldoc.lexer import (                                 # noqa: E402
    find_top_level,
    split_top_level,
    strip_comments,
)


class TestLexer(unittest.TestCase):
    def test_strips_line_comments(self):
        self.assertNotIn("secret", strip_comments("SELECT 1 -- secret\nFROM t"))

    def test_strips_block_comments(self):
        self.assertNotIn("secret", strip_comments("SELECT /* secret */ 1 FROM t"))

    def test_keeps_comment_markers_inside_strings(self):
        sql = "SELECT '-- not a comment' AS x FROM t"
        self.assertIn("-- not a comment", strip_comments(sql))

    def test_split_ignores_nested_commas(self):
        parts = split_top_level("a, fn(b, c), d")
        self.assertEqual(parts, ["a", "fn(b, c)", "d"])

    def test_split_ignores_commas_in_strings(self):
        parts = split_top_level("a, 'x,y', b")
        self.assertEqual(parts, ["a", "'x,y'", "b"])

    def test_find_top_level_skips_subquery(self):
        sql = "SELECT * FROM (SELECT 1 FROM inner_t WHERE z=1) s WHERE a=1"
        idx = find_top_level(sql, "where")
        self.assertGreater(idx, sql.index(") s"))

    def test_find_top_level_requires_word_boundary(self):
        self.assertEqual(find_top_level("SELECT wherever FROM t", "where"), -1)


class TestSimpleSelect(unittest.TestCase):
    def setUp(self):
        self.doc = analyse(
            """
            SELECT DISTINCT
                c.customer_id,
                c.full_name AS customer_name,
                UPPER(c.region) AS region
            FROM crm.customers c
            WHERE c.active = 1
              AND c.region IS NOT NULL
            ORDER BY c.full_name
            """,
            name="customers",
        )

    def test_reads_base_table(self):
        self.assertEqual(self.doc.base_tables, ["crm.customers"])

    def test_column_count(self):
        self.assertEqual(len(self.doc.final.output_columns), 3)

    def test_alias_becomes_name(self):
        names = [c.name for c in self.doc.final.output_columns]
        self.assertEqual(names, ["customer_id", "customer_name", "region"])

    def test_lineage_resolves_alias_to_table(self):
        col = self.doc.final.output_columns[1]
        self.assertEqual(col.sources, ["crm.customers.full_name"])

    def test_detects_distinct(self):
        self.assertTrue(self.doc.final.is_distinct)

    def test_splits_filters_on_and(self):
        self.assertEqual(len(self.doc.final.filters), 2)


class TestJoinsAndCtes(unittest.TestCase):
    def setUp(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "examples" / \
            "monthly_category_revenue.sql"
        self.doc = analyse(path.read_text(encoding="utf-8"), name="monthly_category_revenue")

    def test_finds_all_ctes(self):
        names = [s.name for s in self.doc.stages]
        self.assertEqual(names, ["monthly_orders", "order_revenue", "final"])

    def test_base_tables_exclude_ctes(self):
        for t in self.doc.base_tables:
            self.assertNotIn(t, ("monthly_orders", "order_revenue"))

    def test_base_tables_found(self):
        self.assertIn("sales.orders", self.doc.base_tables)
        self.assertIn("sales.customers", self.doc.base_tables)

    def test_join_kinds(self):
        kinds = {j.kind for j in self.doc.final.joins}
        self.assertIn("inner join", kinds)
        self.assertIn("left join", kinds)

    def test_join_conditions_captured(self):
        for j in self.doc.final.joins:
            self.assertTrue(j.condition, f"{j.target} has no ON condition")

    def test_cte_marked_as_cte(self):
        kinds = {s.name: s.kind for s in self.doc.final.sources}
        self.assertEqual(kinds.get("monthly_orders"), "cte")

    def test_aggregate_detected(self):
        by_name = {c.name: c for c in self.doc.final.output_columns}
        self.assertTrue(by_name["gross_revenue"].is_aggregate)

    def test_case_expression_noted(self):
        by_name = {c.name: c for c in self.doc.final.output_columns}
        self.assertEqual(by_name["revenue_band"].note, "conditional logic")

    def test_group_by_captured(self):
        self.assertEqual(len(self.doc.final.group_by), 3)

    def test_parameters_found(self):
        self.assertIn("start_date", self.doc.parameters)
        self.assertIn("end_date", self.doc.parameters)


class TestRendering(unittest.TestCase):
    def setUp(self):
        self.doc = analyse(
            "SELECT a.id, SUM(a.amt) AS total FROM fin.ledger a GROUP BY a.id",
            name="ledger_totals",
        )

    def test_markdown_has_sections(self):
        md = to_markdown(self.doc)
        self.assertIn("# ledger_totals", md)
        self.assertIn("## Reads from", md)
        self.assertIn("`fin.ledger`", md)
        self.assertIn("## Output columns", md)

    def test_json_round_trips(self):
        import json
        data = json.loads(to_json(self.doc))
        self.assertEqual(data["name"], "ledger_totals")
        self.assertEqual(data["base_tables"], ["fin.ledger"])
        self.assertEqual(len(data["output_columns"]), 2)


class TestEdgeCases(unittest.TestCase):
    def test_empty_input(self):
        doc = analyse("", name="empty")
        self.assertEqual(doc.stages, [])
        self.assertIsNone(doc.final)

    def test_comment_only_input(self):
        doc = analyse("-- nothing here\n", name="c")
        self.assertIsNone(doc.final)

    def test_star_select(self):
        doc = analyse("SELECT * FROM t", name="star")
        self.assertEqual(doc.final.output_columns[0].name, "(expression)")

    def test_subquery_in_from_does_not_leak_table(self):
        doc = analyse(
            "SELECT s.id FROM (SELECT id FROM inner_tbl) s",
            name="sub",
        )
        self.assertNotIn("inner_tbl", doc.base_tables)


if __name__ == "__main__":
    unittest.main(verbosity=2)
