import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]


class CreditFirewallWiringTest(unittest.TestCase):
    def test_community_policy_disables_fincept_metered_services(self):
        source = (ROOT / "src/core/policy/CommunityPolicy.h").read_text(encoding="utf-8")
        self.assertIn("fincept_metered_services_enabled", source)
        self.assertIn("return false", source[source.index("fincept_metered_services_enabled"):])

    def test_paid_data_and_maritime_services_check_firewall(self):
        data = (ROOT / "src/services/fincept_data/FinceptDataService.cpp").read_text(encoding="utf-8")
        maritime = (ROOT / "src/services/maritime/MaritimeService.cpp").read_text(encoding="utf-8")
        self.assertIn("fincept_metered_services_enabled", data)
        self.assertGreaterEqual(maritime.count("fincept_metered_services_enabled"), 4)
        self.assertIn("community_credit_firewall", data)
        self.assertIn("community credit firewall", maritime.lower())

    def test_catalogue_connectors_receive_only_their_declared_credentials(self):
        source = (ROOT / "src/services/connectors/ConnectorService.cpp").read_text(encoding="utf-8")
        run = source[
            source.index("void ConnectorService::start_on_service_thread"):
            source.index("void ConnectorService::finish")
        ]
        self.assertIn("opts.credential_allowlist = info->env_keys", run)

    def test_macro_and_geopolitics_services_check_firewall(self):
        geopolitics = (ROOT / "src/services/geopolitics/GeopoliticsService.cpp").read_text(encoding="utf-8")
        macro = (ROOT / "src/services/economics/FinceptMacroService.cpp").read_text(encoding="utf-8")
        calendar = (ROOT / "src/services/economics/MacroCalendarService.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(geopolitics.count("fincept_metered_services_enabled"), 4)
        self.assertIn("fincept_metered_services_enabled", macro)
        self.assertIn("fincept_metered_services_enabled", calendar)
        self.assertIn("community credit firewall", geopolitics.lower())
        self.assertIn("community credit firewall", macro.lower())
        self.assertIn("community credit firewall", calendar.lower())
        self.assertIn("byo_public_data.py", geopolitics)
        self.assertIn("QStringLiteral(\"gdelt\")", geopolitics)
        self.assertIn("public_zero_credit", geopolitics)

    def test_all_fincept_llm_entrypoints_check_firewall(self):
        router = (ROOT / "src/services/llm/ModelRouter.cpp").read_text(encoding="utf-8")
        llm = (ROOT / "src/services/llm/LlmService.cpp").read_text(encoding="utf-8")
        arena = (ROOT / "src/services/alpha_arena/ArenaLlmClient.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(router.count("fincept_metered_services_enabled"), 2)
        self.assertIn("fincept_metered_services_enabled", llm)
        self.assertIn("fincept_metered_services_enabled", arena)

    def test_fincept_async_transport_checks_firewall_before_network(self):
        source = (ROOT / "src/services/llm/LlmFinceptAsync.cpp").read_text(encoding="utf-8")
        submit = source[
            source.index("LlmResponse LlmService::fincept_submit_poll"):
            source.index("LlmResponse LlmService::fincept_async_request")
        ]
        self.assertLess(
            submit.index("fincept_metered_services_enabled"),
            submit.index("eventloop_request("),
        )

    def test_bundled_deep_agents_have_no_hosted_fincept_llm_fallback(self):
        package = (ROOT / "scripts/agents/deepagents/__init__.py").read_text(encoding="utf-8")
        fallback = (ROOT / "scripts/agents/deepagents/orchestrator.py").read_text(encoding="utf-8")
        self.assertNotIn("FinceptOrchestrator", package)
        self.assertNotIn("api.fincept.in", fallback)
        self.assertNotIn("/research/llm", fallback)
        self.assertNotIn("import requests", fallback)
        self.assertIn("DeepSeek", fallback)

    def test_interactive_ai_surfaces_route_through_byo_llm_service(self):
        surfaces = {
            "src/screens/ai_chat/AiChatScreen_Messaging.cpp": "kFeatureAiChat",
            "src/screens/dashboard/widgets/AiQuickAskWidget.cpp": "kFeatureQuickAsk",
            "src/services/news/NewsService.cpp": "kFeatureNewsAnalysis",
            "src/services/report_builder/ReportAiLlmBackend.cpp": "kReportDraftFeature",
            "src/ui/widgets/ProAnalytics.cpp": "kFeatureProAnalyticsExplain",
        }
        for relative, feature in surfaces.items():
            with self.subTest(surface=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("LlmService::instance()", source)
                self.assertIn(feature, source)

        news = (ROOT / "src/services/news/NewsService.cpp").read_text(encoding="utf-8")
        self.assertNotIn('QStringLiteral("/news/analyze")', news)

    def test_llm_execution_boundary_rejects_retired_and_metered_providers(self):
        source = (ROOT / "src/services/llm/LlmService.cpp").read_text(encoding="utf-8")
        request = source[
            source.index("LlmResponse LlmService::do_request"):
            source.index("LlmResponse LlmService::do_streaming_request")
        ]
        self.assertIn("llm_svc_provider_allowed(provider_)", request)
        self.assertLess(
            request.index("llm_svc_provider_allowed(provider_)"),
            request.index("blocking_post("),
        )


if __name__ == "__main__":
    unittest.main()
