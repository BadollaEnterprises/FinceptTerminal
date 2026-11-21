import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SOURCE = ROOT / "src" / "services" / "quantlib" / "QuantLibClient.cpp"
SCREEN_SOURCE = ROOT / "src" / "screens" / "quantlib" / "QuantLibScreen.cpp"


class QuantLibLocalWiringTest(unittest.TestCase):
    def test_nonfree_calls_use_local_adapter(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('QStringLiteral("byo_quantlib.py")', source)
        self.assertIn("quantlib_endpoint_tier(endpoint) == QuantLibTier::Free", source)
        self.assertIn("options.stdin_data =", source)

    def test_all_manifest_routes_dispatch_locally_before_cloud_logic(self):
        source = SOURCE.read_text(encoding="utf-8")
        call = source[source.index("quint64 QuantLibClient::call("):
                      source.index("void QuantLibClient::cancel")]
        self.assertLess(call.index("has_local_route(endpoint)"), call.index("is_get_endpoint(endpoint)"))
        self.assertLess(call.index("fincept_metered_services_enabled"), call.index("is_get_endpoint(endpoint)"))
        self.assertIn("return call_local(endpoint, body", call)

    def test_local_unsupported_route_never_falls_back_to_cloud(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('QStringLiteral("LOCAL_UNSUPPORTED")', source)
        local_branch = source[source.index('QStringLiteral("byo_quantlib.py")'):]
        self.assertNotIn("network()->post", local_branch)

    def test_screen_explains_local_zero_credit_route(self):
        source = SCREEN_SOURCE.read_text(encoding="utf-8")
        self.assertIn("LOCAL ENGINE · 0 Fincept credits", source)
        self.assertIn("Local deterministic route · 0 credits", source)
        badge_block = source[source.index("void QuantLibScreen::refresh_tree_badges"):
                             source.index("void QuantLibScreen::decorate_endpoint_items")]
        self.assertIn("%1 local · %2 free cloud fallback", badge_block)
        self.assertNotIn("1 credit per call", badge_block)
        self.assertIn("QuantLibClient::has_local_route", source)
        self.assertIn("NOT YET LOCAL", source)

    def test_community_quantlib_chrome_does_not_advertise_a_paid_plan(self):
        source = SCREEN_SOURCE.read_text(encoding="utf-8")
        refresh = source[source.index("void QuantLibScreen::refresh_plan_ui"):
                         source.index("QStringList QuantLibScreen::endpoints_for")]
        self.assertIn("fincept_metered_services_enabled", refresh)
        self.assertIn("LOCAL + FREE", refresh)
        self.assertIn("0 Fincept credits", refresh)
        self.assertIn("upgrade_btn_->setVisible(false)", refresh)

        result = (ROOT / "src/screens/quantlib/QuantLibResultView.cpp").read_text(encoding="utf-8")
        welcome = result[result.index("void QuantLibResultView::show_idle"):
                         result.index("void QuantLibResultView::show_preview")]
        self.assertIn("fincept_metered_services_enabled", welcome)
        self.assertIn("deterministic engine", welcome)
        self.assertIn("no paid fallback", welcome)

    def test_local_quantlib_routes_can_run_without_fincept_sign_in(self):
        source = SCREEN_SOURCE.read_text(encoding="utf-8")
        can_run = source[source.index("bool QuantLibScreen::can_auto_run"):
                         source.index("void QuantLibScreen::show_default_view")]
        self.assertIn("QuantLibClient::has_local_route(endpoint)", can_run)
        self.assertLess(can_run.index("QuantLibClient::has_local_route(endpoint)"),
                        can_run.index("AuthManager::instance().is_authenticated()"))

        execute = source[source.index("void QuantLibScreen::on_execute"):
                         source.index("void QuantLibScreen::on_exec_clicked")]
        self.assertIn("!services::QuantLibClient::has_local_route(endpoint)", execute)

    def test_local_routes_are_never_presented_as_free_cloud(self):
        source = SCREEN_SOURCE.read_text(encoding="utf-8")
        decorate = source[source.index("void QuantLibScreen::decorate_endpoint_items"):
                          source.index("void QuantLibScreen::update_access_label")]
        self.assertIn('const QString badge = local ? tr("LOCAL")', decorate)
        self.assertIn('local ? tr("Local deterministic route · 0 credits")', decorate)

        access = source[source.index("void QuantLibScreen::update_access_label"):
                        source.index("void QuantLibScreen::open_plans")]
        self.assertLess(access.index("has_local_route"), access.index("quantlib_endpoint_tier"))

        run = source[source.index("void QuantLibScreen::execute_api"):
                     source.index("void QuantLibScreen::set_loading")]
        self.assertLess(run.index("has_local_route(endpoint)"), run.index("quantlib_endpoint_tier(endpoint)"))

    def test_quantlib_docs_describe_the_bundled_local_engine(self):
        docs = (ROOT / "src/screens/docs/DocsScreen_Pages_Trading.cpp").read_text(encoding="utf-8")
        page = docs[docs.index("QWidget* DocsScreen::page_quantlib()"):
                    docs.index("QWidget* DocsScreen::page_ai_chat()")]
        self.assertIn("bundled deterministic engine", page)
        self.assertIn("590 local routes", page)
        self.assertIn("0 Fincept credits", page)
        self.assertIn("no paid", page)
        self.assertIn("fallback.", page)
        self.assertNotIn("REST API backend", page)


if __name__ == "__main__":
    unittest.main()
