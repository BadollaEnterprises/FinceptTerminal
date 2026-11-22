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

    def test_generic_one_shot_analytics_are_secretless(self):
        source = (ROOT / "src/services/python_cli/PythonCliService.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("credential_allowlist = QStringList{}"), 2)

    def test_market_stream_processes_do_not_inherit_unrelated_credentials(self):
        session = (ROOT / "src/trading/ExchangeSession.cpp").read_text(encoding="utf-8")
        daemon = (ROOT / "src/trading/ExchangeDaemonPool.cpp").read_text(encoding="utf-8")
        self.assertIn("build_python_env(QStringList{})", session)
        self.assertIn("build_python_env(QStringList{})", daemon)

    def test_agent_subprocesses_receive_model_keys_only_in_private_payloads(self):
        agents = (ROOT / "src/services/agents/AgentService.cpp").read_text(encoding="utf-8")
        quant = (ROOT / "src/services/ai_quant_lab/AIQuantLabService.cpp").read_text(encoding="utf-8")
        self.assertIn("build_python_env(QStringList{})", agents)
        self.assertIn("py.build_python_env(QStringList{})", quant)
        self.assertGreaterEqual(quant.count("credential_allowlist = QStringList{}"), 2)

    def test_deterministic_analytics_services_are_secretless(self):
        for relative in (
            "src/services/akshare/AkShareService.cpp",
            "src/services/asia_markets/AsiaMarketsService.cpp",
            "src/services/portfolio/PortfolioAnalyticsService.cpp",
            "src/services/backtesting/BacktestingService.cpp",
        ):
            with self.subTest(service=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("credential_allowlist = QStringList{}", source)

        backtesting = (ROOT / "src/services/backtesting/BacktestingService.cpp").read_text(encoding="utf-8")
        self.assertNotIn("PythonRunner::instance().run(", backtesting)

    def test_market_analytics_subprocesses_are_secretless(self):
        for relative in (
            "src/services/options/CurveDataService.cpp",
            "src/services/options/FreeOptionChainService.cpp",
            "src/services/screener/ScreenerService.cpp",
            "src/services/company_snapshot/CompanySnapshotService.cpp",
        ):
            with self.subTest(service=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("credential_allowlist = QStringList{}", source)

        chain = (ROOT / "src/services/options/OptionChainService.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(chain.count("credential_allowlist = QStringList{}"), 2)

    def test_dynamic_economic_dispatchers_use_per_script_credentials(self):
        gov = (ROOT / "src/services/gov_data/GovDataService.cpp").read_text(encoding="utf-8")
        economics = (ROOT / "src/services/economics/EconomicsService.cpp").read_text(encoding="utf-8")
        self.assertIn("gov_svc_credentials_for_script", gov)
        self.assertIn("opts.credential_allowlist = gov_svc_credentials_for_script(script)", gov)
        self.assertIn("econ_svc_credentials_for_script", economics)
        self.assertIn("opts.credential_allowlist = econ_svc_credentials_for_script(script)", economics)

    def test_corporate_finance_analytics_are_secretless(self):
        source = (ROOT / "src/services/ma_analytics/MAAnalyticsService.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("credential_allowlist = QStringList{}"), 3)

    def test_prediction_bridges_receive_credentials_only_via_stdin(self):
        for relative in (
            "src/services/prediction/kalshi/KalshiAdapter.cpp",
            "src/services/prediction/polymarket/PolymarketAdapter.cpp",
        ):
            with self.subTest(service=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("opts.stdin_data = payload_bytes", source)
                self.assertIn("opts.credential_allowlist = QStringList{}", source)
                self.assertNotIn("PythonRunner::instance().run(\n", source)

    def test_equity_and_sec_public_workers_are_secretless(self):
        core = (ROOT / "src/services/equity/EquityResearchService.cpp").read_text(encoding="utf-8")
        extension = (ROOT / "src/services/equity/EquityResearchService_Ext.cpp").read_text(encoding="utf-8")
        filings = (ROOT / "src/services/equity/SecFilingsService.cpp").read_text(encoding="utf-8")
        segments = (ROOT / "src/screens/equity_research/EquitySegmentsTab.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(core.count("credential_allowlist = QStringList{}"), 2)
        self.assertIn("credential_allowlist = QStringList{}", extension)
        self.assertIn("credential_allowlist = QStringList{}", filings)
        self.assertIn("credential_allowlist = QStringList{}", segments)

    def test_workflow_python_code_cannot_read_stored_credentials(self):
        runner = (ROOT / "src/python/PythonRunner.h").read_text(encoding="utf-8")
        bridge = (ROOT / "src/services/workflow/adapters/ServiceBridges.cpp").read_text(encoding="utf-8")
        self.assertIn("run_code_with_options", runner)
        self.assertGreaterEqual(bridge.count("code_opts.credential_allowlist = QStringList{}"), 2)
        self.assertGreaterEqual(bridge.count("run_code_with_options"), 2)

    def test_workflow_script_nodes_use_least_privilege_credentials(self):
        analytics = (ROOT / "src/services/workflow/nodes/AnalyticsNodes.cpp").read_text(encoding="utf-8")
        triggers = (ROOT / "src/services/workflow/nodes/TriggerNodes.cpp").read_text(encoding="utf-8")
        integration = (ROOT / "src/services/workflow/nodes/IntegrationNodes.cpp").read_text(encoding="utf-8")
        market = (ROOT / "src/services/workflow/nodes/MarketDataNodes.cpp").read_text(encoding="utf-8")
        geopolitics = (ROOT / "src/services/geopolitics/GeopoliticsService.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(analytics.count("credential_allowlist = QStringList{}"), 2)
        self.assertGreaterEqual(triggers.count("credential_allowlist = QStringList{}"), 2)
        self.assertIn("opts.stdin_data = args_json", integration)
        self.assertIn("opts.credential_allowlist = QStringList{}", integration)
        self.assertIn("market_data_credentials_for_script", market)
        self.assertIn("opts.credential_allowlist = market_data_credentials_for_script(script)", market)
        self.assertIn("opts.credential_allowlist = QStringList{}", geopolitics)

    def test_yahoo_market_workers_are_explicitly_secretless(self):
        market = (ROOT / "src/services/markets/MarketDataService.cpp").read_text(encoding="utf-8")
        resolver = (ROOT / "src/services/instruments/InstrumentResolverSources.cpp").read_text(encoding="utf-8")
        self.assertIn("mds_public_python_options", market)
        self.assertNotIn("PythonRunner::instance().run(", market)
        self.assertIn("isrs_public_python_options", resolver)
        self.assertNotIn("PythonRunner::instance().run(", resolver)

    def test_portfolio_and_relationship_workers_are_secretless(self):
        portfolio = (ROOT / "src/services/portfolio/PortfolioService_Metrics.cpp").read_text(encoding="utf-8")
        relationship = (ROOT / "src/services/relationship_map/RelationshipMapService.cpp").read_text(encoding="utf-8")
        self.assertIn("opts.credential_allowlist = QStringList{}", portfolio)
        self.assertIn("opts.credential_allowlist = QStringList{}", relationship)

    def test_voice_workers_start_from_scrubbed_environments(self):
        for relative in (
            "src/services/stt/SpeechService.cpp",
            "src/services/tts/TtsService.cpp",
            "src/services/voice_trigger/ClapDetectorService.cpp",
        ):
            with self.subTest(service=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("build_python_env(QStringList{})", source)
                self.assertNotIn("QProcessEnvironment::systemEnvironment()", source)
                self.assertIn('#include "core/privacy/LogRedact.h"', source)
                self.assertGreaterEqual(source.count("privacy::redact_text"), 4)
                self.assertNotIn('QString("Provider[%1] stdout: %2")', source)
                self.assertNotIn('QString("stdout: %1")', source)

    def test_external_mcp_servers_only_receive_declared_credentials(self):
        source = (ROOT / "src/mcp/McpClient.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("process::strip_unmanaged_credentials"), 2)
        self.assertIn("config_.env.keys()", source)
        self.assertGreaterEqual(source.count("safe_process_log_line"), 2)
        self.assertNotIn('append_log("[stdout] "', source)
        self.assertNotIn('append_log("[stderr] "', source)

    def test_mcp_self_check_only_passes_its_declared_token(self):
        source = (ROOT / "src/mcp/server/McpServerSelfCheck.cpp").read_text(encoding="utf-8")
        self.assertIn("process::strip_unmanaged_credentials", source)
        self.assertIn("edition::kMcpTokenEnvVar", source)

    def test_venv_verification_does_not_inherit_credentials(self):
        source = (ROOT / "src/python/PythonEnvRelocation.cpp").read_text(encoding="utf-8")
        self.assertIn("process::strip_unmanaged_credentials", source)

    def test_video_resolver_does_not_inherit_credentials(self):
        source = (ROOT / "src/screens/dashboard/widgets/VideoPlayerWidget.cpp").read_text(encoding="utf-8")
        self.assertIn("process::strip_unmanaged_credentials", source)
        self.assertIn("setProcessEnvironment", source)

    def test_updater_helpers_do_not_inherit_credentials(self):
        paths = [
            "src/services/updater/InstallContext.cpp",
            "src/services/updater/UpdateInstaller_mac.cpp",
            "src/services/updater/UpdateInstaller_linux.cpp",
            "src/services/updater/UpdateInstaller_win.cpp",
        ]
        for path in paths:
            with self.subTest(path=path):
                source = (ROOT / path).read_text(encoding="utf-8")
                self.assertIn("process::strip_unmanaged_credentials", source)

    def test_python_package_manager_does_not_inherit_credentials(self):
        source = (ROOT / "src/python/PythonSetupManager.cpp").read_text(encoding="utf-8")
        start = source.index("QProcessEnvironment PythonSetupManager::uv_environment() const")
        end = source.index("\n}\n", start)
        implementation = source[start:end]
        self.assertIn("process::strip_unmanaged_credentials", implementation)

    def test_llm_diagnostics_redact_provider_errors(self):
        service = (ROOT / "src/services/llm/LlmService.cpp").read_text(encoding="utf-8")
        http = (ROOT / "src/services/llm/LlmHttpHelpers.cpp").read_text(encoding="utf-8")
        tools = (ROOT / "src/services/llm/LlmToolLoop.cpp").read_text(encoding="utf-8")
        fincept_async = (ROOT / "src/services/llm/LlmFinceptAsync.cpp").read_text(encoding="utf-8")
        live_smoke = (ROOT / "src/services/llm/DeepSeekLiveSmoke.cpp").read_text(encoding="utf-8")
        screen = (ROOT / "src/screens/ai_chat/AiChatScreen_Messaging.cpp").read_text(encoding="utf-8")
        bubble = (ROOT / "src/screens/ai_chat/AiChatBubble.cpp").read_text(encoding="utf-8")
        cloud_agent = (ROOT / "src/services/agent_tasks/CloudAgentClient.cpp").read_text(encoding="utf-8")
        quant_lab = (ROOT / "src/services/ai_quant_lab/AIQuantLabService.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(service.count("privacy::redact_text"), 5)
        self.assertIn("privacy::redact_text(result.error)", http)
        self.assertIn("privacy::redact_text(tr.error)", tools)
        self.assertGreaterEqual(tools.count("privacy::redact_text"), 2)
        self.assertIn('#include "core/privacy/LogRedact.h"', fincept_async)
        self.assertGreaterEqual(fincept_async.count("privacy::redact_text"), 7)
        self.assertNotIn('{{"body", QString::fromUtf8(', fincept_async)
        self.assertIn("privacy::redact_text(response.error)", live_smoke)
        self.assertIn("privacy::redact_text(response.error)", screen)
        self.assertGreaterEqual(bubble.count("privacy::redact_text"), 3)
        self.assertIn("privacy::redact_text(message)", cloud_agent)
        self.assertIn('#include "core/privacy/LogRedact.h"', quant_lab)
        self.assertGreaterEqual(quant_lab.count("privacy::redact_text"), 4)

    def test_broker_auth_diagnostics_redact_provider_errors(self):
        for relative, minimum in (
            ("src/trading/brokers/angelone/AngelOneBroker.cpp", 2),
            ("src/trading/brokers/zerodha/ZerodhaBroker.cpp", 2),
        ):
            with self.subTest(broker=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn('#include "core/privacy/LogRedact.h"', source)
                self.assertGreaterEqual(source.count("privacy::redact_text"), minimum)

    def test_portfolio_import_logs_do_not_persist_rejected_rows(self):
        source = (ROOT / "src/services/portfolio/PortfolioService_ImportExport.cpp").read_text(encoding="utf-8")
        self.assertIn('#include "core/privacy/LogRedact.h"', source)
        self.assertIn("privacy::redact_text(raw)", source)
        self.assertIn("privacy::mask_id(target_id)", source)
        self.assertNotIn("arg(problems.first())", source)
        self.assertNotIn('"Import: " + raw_errors.join', source)
        self.assertNotIn('" + file_path', source)

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
