"""Offline contracts for source-attributed SEC XBRL revenue segments."""

from __future__ import annotations

import importlib.util
import io
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "corporateFinance"
    / "valuation"
    / "sec_segment_revenue.py"
)


def load_module():
    if not SCRIPT.exists():
        raise AssertionError(f"SEC segment extractor is missing: {SCRIPT}")
    spec = importlib.util.spec_from_file_location("sec_segment_revenue", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def context(context_id: str, start: str, end: str, members=()):
    dimensions = "".join(
        f'<xbrldi:explicitMember dimension="{axis}">{member}</xbrldi:explicitMember>'
        for axis, member in members
    )
    segment = f"<xbrli:segment>{dimensions}</xbrli:segment>" if dimensions else ""
    return f"""
      <xbrli:context id="{context_id}">
        <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier>{segment}</xbrli:entity>
        <xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period>
      </xbrli:context>
    """


def fact(context_id: str, value: str, *, concept="us-gaap:Revenues", scale="6", sign=""):
    sign_attr = f' sign="{sign}"' if sign else ""
    return (
        f'<ix:nonFraction unitRef="usd" contextRef="{context_id}" decimals="-6" '
        f'name="{concept}" scale="{scale}"{sign_attr}>{value}</ix:nonFraction>'
    )


class SecSegmentRevenueTest(unittest.TestCase):
    def test_cli_refresh_flag_bypasses_the_twelve_hour_cache(self):
        mod = load_module()
        with mock.patch.object(mod, "segments", return_value={"success": True, "data": {}}) as segments, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(mod.main(["sec_segment_revenue.py", "segments", "GOOG", "--refresh"]), 0)
        segments.assert_called_once_with("GOOG", refresh=True)

    def test_single_axis_segments_must_reconcile_to_reported_total(self):
        mod = load_module()
        html = "<html>" + "".join([
            context("total", "2025-01-01", "2025-12-31"),
            context("services", "2025-01-01", "2025-12-31",
                    [("us-gaap:StatementBusinessSegmentsAxis", "acme:ServicesMember")]),
            context("cloud", "2025-01-01", "2025-12-31",
                    [("us-gaap:StatementBusinessSegmentsAxis", "acme:CloudMember")]),
            context("other", "2025-01-01", "2025-12-31",
                    [("us-gaap:StatementBusinessSegmentsAxis", "us-gaap:AllOtherSegmentsMember")]),
            fact("total", "100"), fact("services", "70"), fact("cloud", "25"), fact("other", "5"),
        ]) + "</html>"

        answer = mod.extract_revenue_segments(html)

        self.assertEqual(len(answer["periods"]), 1)
        period = answer["periods"][0]
        self.assertEqual(period["end"], "2025-12-31")
        self.assertEqual(period["total"], 100_000_000.0)
        breakdown = period["breakdowns"][0]
        self.assertEqual(breakdown["axis"], "us-gaap:StatementBusinessSegmentsAxis")
        self.assertEqual(breakdown["label"], "Statement Business Segments")
        self.assertEqual([row["label"] for row in breakdown["members"]],
                         ["Services", "Cloud", "All Other Segments"])
        self.assertEqual([row["share"] for row in breakdown["members"]], [0.7, 0.25, 0.05])
        self.assertEqual(breakdown["reconciliation_delta"], 0.0)

    def test_multidimensional_and_non_reconciling_facts_are_not_invented_into_a_breakdown(self):
        mod = load_module()
        html = "<html>" + "".join([
            context("total", "2025-01-01", "2025-12-31"),
            context("a", "2025-01-01", "2025-12-31",
                    [("srt:ProductOrServiceAxis", "acme:AdsMember")]),
            context("b", "2025-01-01", "2025-12-31",
                    [("srt:ProductOrServiceAxis", "acme:CloudMember")]),
            context("nested", "2025-01-01", "2025-12-31",
                    [("srt:ProductOrServiceAxis", "acme:SearchMember"),
                     ("us-gaap:StatementBusinessSegmentsAxis", "acme:ServicesMember")]),
            fact("total", "100"), fact("a", "65"), fact("b", "25"), fact("nested", "70"),
        ]) + "</html>"

        answer = mod.extract_revenue_segments(html)

        self.assertEqual(answer["periods"], [])
        self.assertIn("No single-axis revenue breakdown reconciled", answer["reason"])

    def test_contract_revenue_segments_can_reconcile_to_nearby_total_revenue_with_provenance(self):
        mod = load_module()
        contract = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
        html = "<html>" + "".join([
            context("total", "2025-01-01", "2025-12-31"),
            context("one", "2025-01-01", "2025-12-31",
                    [("us-gaap:StatementBusinessSegmentsAxis", "acme:OneMember")]),
            context("two", "2025-01-01", "2025-12-31",
                    [("us-gaap:StatementBusinessSegmentsAxis", "acme:TwoMember")]),
            fact("total", "99,950", scale="3"),
            fact("one", "60", concept=contract), fact("two", "40", concept=contract),
        ]) + "</html>"

        answer = mod.extract_revenue_segments(html)

        period = answer["periods"][0]
        self.assertEqual(period["concept"], contract)
        self.assertEqual(period["total_concept"], "us-gaap:Revenues")
        self.assertEqual(period["total"], 99_950_000.0)
        self.assertEqual(period["breakdowns"][0]["reported_sum"], 100_000_000.0)
        self.assertEqual(period["breakdowns"][0]["reconciliation_delta"], 50_000.0)

    def test_non_annual_periods_negative_segments_and_unscaled_text_are_rejected_or_parsed(self):
        mod = load_module()
        html = "<html>" + "".join([
            context("total", "2025-01-01", "2025-12-31"),
            context("positive", "2025-01-01", "2025-12-31",
                    [("us-gaap:StatementBusinessSegmentsAxis", "acme:PositiveMember")]),
            context("negative", "2025-01-01", "2025-12-31",
                    [("us-gaap:StatementBusinessSegmentsAxis", "acme:NegativeMember")]),
            context("quarter", "2025-10-01", "2025-12-31",
                    [("us-gaap:StatementBusinessSegmentsAxis", "acme:QuarterMember")]),
            fact("total", "1,000", scale="0"), fact("positive", "1,100", scale="0"),
            fact("negative", "100", scale="0", sign="-"), fact("quarter", "250", scale="0"),
        ]) + "</html>"

        answer = mod.extract_revenue_segments(html)

        self.assertEqual(answer["periods"], [])


if __name__ == "__main__":
    unittest.main()
