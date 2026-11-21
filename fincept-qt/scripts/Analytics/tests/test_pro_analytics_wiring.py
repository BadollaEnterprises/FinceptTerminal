import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SOURCE = ROOT / "src" / "ui" / "widgets" / "ProAnalytics.cpp"


class ProAnalyticsWiringTests(unittest.TestCase):
    def test_reports_use_local_adapter_instead_of_paid_gateway(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('QStringLiteral("byo_pro_analytics.py")', source)
        self.assertNotIn('QStringLiteral("/data/pro/', source)

    def test_buttons_are_marked_as_local_zero_credit_features(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('QStringLiteral("LOCAL  ")', source)
        self.assertIn('QStringLiteral("local_public_data")', source)


if __name__ == "__main__":
    unittest.main()
