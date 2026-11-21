import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]


def load_module(testcase):
    path = ROOT / "scripts/byo_public_data.py"
    testcase.assertTrue(path.exists(), "zero-credit public-data adapter is missing")
    spec = importlib.util.spec_from_file_location("byo_public_data", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublicDataTest(unittest.TestCase):
    def test_catalog_has_zero_credit_fallbacks(self):
        catalog = load_module(self).catalog()
        self.assertEqual(catalog["fincept_credits"], 0)
        ids = {row["id"] for row in catalog["sources"]}
        self.assertTrue({"fred", "world_bank", "imf", "gdelt", "rss", "yahoo", "stooq"}.issubset(ids))
        self.assertTrue(all(row["cost_route"] == "public_zero_credit" for row in catalog["sources"]))

    def test_fred_csv_normalizes_missing_observations(self):
        module = load_module(self)
        text = "DATE,GDP\n2024-01-01,28000.0\n2024-04-01,.\n2024-07-01,28500.5\n"
        result = module.parse_fred_csv(text, "GDP")
        self.assertEqual(result["source"], "FRED")
        self.assertEqual(result["rows"], [{"date": "2024-01-01", "value": 28000.0},
                                           {"date": "2024-07-01", "value": 28500.5}])

    def test_world_bank_json_normalizes_indicator_rows(self):
        module = load_module(self)
        payload = [{"page": 1}, [{"date": "2024", "value": 3.2, "countryiso3code": "IND"},
                                  {"date": "2023", "value": None, "countryiso3code": "IND"}]]
        result = module.parse_world_bank(payload, "NY.GDP.MKTP.KD.ZG")
        self.assertEqual(result["rows"], [{"date": "2024", "value": 3.2, "country": "IND"}])

    def test_gdelt_json_normalizes_articles(self):
        module = load_module(self)
        payload = {"articles": [{"title": "Central bank signals pause", "url": "https://example.test/a",
                                  "seendate": "20261120T120000Z", "domain": "example.test",
                                  "language": "English"}]}
        result = module.parse_gdelt(payload)
        self.assertEqual(result["rows"][0]["title"], "Central bank signals pause")
        self.assertEqual(result["source"], "GDELT DOC 2.0")

    def test_stooq_csv_normalizes_prices(self):
        module = load_module(self)
        text = "Date,Open,High,Low,Close,Volume\n2026-11-19,100,103,99,102,1234\n"
        result = module.parse_stooq_csv(text, "aapl.us")
        self.assertEqual(result["rows"][0]["close"], 102.0)
        self.assertEqual(result["symbol"], "AAPL.US")

    def test_imf_pivot_normalizes_country_series(self):
        module = load_module(self)
        payload = {"values": {"NGDP_RPCH": {"IND": {"2025": 6.5, "2026": 6.3}}}}
        result = module.parse_imf(payload, "NGDP_RPCH", "IND")
        self.assertEqual(result["rows"], [{"date": "2025", "value": 6.5},
                                           {"date": "2026", "value": 6.3}])

    def test_stooq_provider_block_falls_back_to_yahoo(self):
        module = load_module(self)
        yahoo = {"chart": {"result": [{"timestamp": [1], "indicators": {"quote": [{"close": [102.0]}]}}]}}
        original = module._get
        module._get = lambda url, json_response=False: yahoo if "yahoo" in url else "<html>blocked</html>"
        try:
            result = module.fetch("stooq", {"symbol": "aapl.us"})
        finally:
            module._get = original
        self.assertEqual(result["source"], "Yahoo Finance")
        self.assertEqual(result["rows"][0]["close"], 102.0)

    def test_gdelt_failure_falls_back_to_public_geopolitics_rss(self):
        module = load_module(self)
        rss = """<rss><channel><item><title>Global update</title>
                 <link>https://example.test/world</link><pubDate>today</pubDate></item></channel></rss>"""
        original = module._get
        module._get = lambda url, json_response=False: rss if "rss" in url else (_ for _ in ()).throw(OSError("down"))
        try:
            result = module.fetch("gdelt", {"query": "geopolitics"})
        finally:
            module._get = original
        self.assertEqual(result["source"], "RSS / Atom fallback")
        self.assertEqual(result["fallback_from"], "GDELT DOC 2.0")
        self.assertEqual(result["rows"][0]["domain"], "example.test")


if __name__ == "__main__":
    unittest.main()
