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
        sessions = (ROOT / "src/screens/ai_chat/AiChatScreen_Sessions.cpp").read_text(encoding="utf-8")
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
        self.assertIn('#include "core/privacy/LogRedact.h"', sessions)
        self.assertGreaterEqual(sessions.count("privacy::redact_text(QString::fromStdString(result.error()))"), 3)
        self.assertNotIn('"list_sessions failed: " + QString::fromStdString(result.error())', sessions)
        self.assertNotIn('"get_messages failed: " + QString::fromStdString(result.error())', sessions)
        self.assertGreaterEqual(bubble.count("privacy::redact_text"), 3)
        self.assertIn("privacy::redact_text(message)", cloud_agent)
        self.assertIn('#include "core/privacy/LogRedact.h"', quant_lab)
        self.assertGreaterEqual(quant_lab.count("privacy::redact_text"), 4)

    def test_ai_chat_surfaces_local_history_write_failures(self):
        screen = (ROOT / "src/screens/ai_chat/AiChatScreen_Messaging.cpp").read_text(encoding="utf-8")
        for result in ("persisted_user", "persisted_partial", "persisted_answer"):
            self.assertRegex(
                screen,
                rf"const auto {result}\s*=\s*ChatRepository::instance\(\)\.add_message",
            )
            self.assertIn(f"if ({result}.is_err())", screen)
            self.assertIn(f"privacy::redact_text(QString::fromStdString({result}.error()))", screen)
        self.assertGreaterEqual(screen.count("Conversation could not be saved locally"), 3)
        self.assertGreaterEqual(screen.count("status_->show_notice"), 3)

    def test_ai_chat_surfaces_saved_prompt_delete_failures(self):
        tools = (ROOT / "src/screens/ai_chat/AiChatScreen_Tools.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(tools.count("if (!aichat::store_saved_prompts(saved_prompts_))"), 2)
        self.assertIn("Could not delete the prompt.", tools)

    def test_ai_chat_rolls_back_failed_pin_writes(self):
        header = (ROOT / "src/screens/ai_chat/AiChatScreen.h").read_text(encoding="utf-8")
        sessions = (ROOT / "src/screens/ai_chat/AiChatScreen_Sessions.cpp").read_text(encoding="utf-8")
        self.assertIn("bool store_pins() const;", header)
        self.assertIn("const bool was_pinned = pinned_.contains(id);", sessions)
        self.assertIn("if (!store_pins())", sessions)
        self.assertIn("Could not save pinned sessions.", sessions)
        self.assertIn("if (was_pinned)", sessions)

    def test_sign_out_reports_credential_cleanup_failures(self):
        auth = (ROOT / "src/auth/AuthManager.cpp").read_text(encoding="utf-8")
        self.assertIn("const auto log_cleanup_failure", auth)
        self.assertGreaterEqual(auth.count("log_cleanup_failure("), 5)
        self.assertIn("privacy::redact_text", auth)
        self.assertIn("during sign-out", auth)

    def test_coding_agent_schedule_delete_reports_storage_failures(self):
        service = (ROOT / "src/services/coding_agents/CodingAgentService.cpp").read_text(encoding="utf-8")
        delete_path = service[
            service.index("void CodingAgentService::delete_schedule"):
            service.index("namespace coding_agent_service_detail", service.index("void CodingAgentService::delete_schedule"))
        ]
        self.assertIn('#include "core/privacy/LogRedact.h"', service)
        self.assertIn("auto r = CodingAgentRepository::instance().delete_schedule(id);", delete_path)
        self.assertIn("if (r.is_err())", delete_path)
        self.assertIn("privacy::redact_text(QString::fromStdString(r.error()))", delete_path)

    def test_feed_save_stops_before_runtime_state_changes_when_storage_fails(self):
        header = (ROOT / "src/services/feeds/FeedMonitor.h").read_text(encoding="utf-8")
        service = (ROOT / "src/services/feeds/FeedMonitor.cpp").read_text(encoding="utf-8")
        panel = (ROOT / "src/screens/common/feeds/FeedPanel.cpp").read_text(encoding="utf-8")
        save_start = service.index("FeedMonitor::add_or_update") - 20
        save_path = service[save_start:service.index("FeedMonitor::remove") - 20]
        self.assertIn("Result<void> add_or_update", header)
        self.assertIn("const auto saved = FeedSubscriptionRepository::instance().upsert(sub);", save_path)
        self.assertIn("if (saved.is_err())", save_path)
        self.assertLess(save_path.index("if (saved.is_err())"), save_path.index("arm_timer(sub)"))
        self.assertIn("return saved;", save_path)
        self.assertIn("const auto saved = FeedMonitor::instance().add_or_update(s);", panel)
        self.assertIn("Could not save the feed", panel)
        self.assertIn("privacy::redact_text", panel)

    def test_feed_delete_keeps_runtime_state_when_subscription_delete_fails(self):
        header = (ROOT / "src/services/feeds/FeedMonitor.h").read_text(encoding="utf-8")
        service = (ROOT / "src/services/feeds/FeedMonitor.cpp").read_text(encoding="utf-8")
        panel = (ROOT / "src/screens/common/feeds/FeedPanel.cpp").read_text(encoding="utf-8")
        remove_start = service.index("FeedMonitor::remove") - 20
        remove_path = service[remove_start:service.index("void FeedMonitor::set_enabled")]
        panel_delete = panel[panel.index("if (dlg.delete_requested())"):panel.index("const FeedSubscription s")]
        self.assertIn("Result<void> remove", header)
        self.assertIn("const auto removed = FeedSubscriptionRepository::instance().remove(id);", remove_path)
        self.assertIn("if (removed.is_err())", remove_path)
        self.assertLess(remove_path.index("if (removed.is_err())"), remove_path.index("disarm_timer(id)"))
        self.assertIn("return removed;", remove_path)
        self.assertIn("const auto removed = FeedMonitor::instance().remove(feed_id);", panel_delete)
        self.assertLess(panel_delete.index("if (removed.is_err())"), panel_delete.index("floating_[feed_id]->close()"))
        self.assertIn("Could not delete the feed", panel_delete)

    def test_feed_refresh_reports_offline_cache_write_failures(self):
        service = (ROOT / "src/services/feeds/FeedMonitor.cpp").read_text(encoding="utf-8")
        fetch_path = service[
            service.index("void FeedMonitor::fetch"):
            service.index("FeedMonitor::add_or_update") - 20
        ]
        self.assertIn("const auto stored = FeedItemRepository::instance().upsert", fetch_path)
        self.assertIn("if (stored.is_err())", fetch_path)
        self.assertIn("const auto pruned = FeedItemRepository::instance().prune", fetch_path)
        self.assertIn("if (pruned.is_err())", fetch_path)
        self.assertIn("offline cache unavailable", fetch_path)
        self.assertIn("privacy::redact_text", fetch_path)

    def test_feed_load_retries_after_subscription_storage_failure(self):
        service = (ROOT / "src/services/feeds/FeedMonitor.cpp").read_text(encoding="utf-8")
        load_path = service[
            service.index("void FeedMonitor::ensure_loaded"):
            service.index("QVector<FeedSubscription> FeedMonitor::subscriptions")
        ]
        self.assertIn("if (res.is_err())", load_path)
        self.assertIn("privacy::redact_text(QString::fromStdString(res.error()))", load_path)
        self.assertLess(load_path.index("if (res.is_err())"), load_path.index("loaded_ = true"))
        self.assertIn("return;", load_path[load_path.index("if (res.is_err())"):load_path.index("loaded_ = true")])

    def test_notification_provider_save_propagates_secret_and_settings_write_failures(self):
        section = (ROOT / "src/screens/settings/NotificationsSection.cpp").read_text(encoding="utf-8")
        helper = section[section.index("notif_store_secret"):section.index("const ProviderDef* notif_find_def")]
        save = section[section.index("bool NotificationsSection::save_provider_fields"):]
        self.assertIn("Result<void> notif_store_secret", section)
        self.assertIn("return rm;", helper)
        self.assertIn("const auto fallback", helper)
        self.assertIn("const auto cleanup", helper)
        self.assertIn("if (cleanup.is_err())", helper)
        self.assertIn("const auto enabled_saved", save)
        self.assertIn("const auto field_saved", save)
        self.assertGreaterEqual(save.count(".is_err()"), 2)
        self.assertIn("Could not save", save)
        self.assertIn("privacy::redact_text", save)

    def test_notification_trigger_save_reports_every_settings_write_failure(self):
        section = (ROOT / "src/screens/settings/NotificationsSection.cpp").read_text(encoding="utf-8")
        save_handler = section[
            section.index("connect(save_btn_, &QPushButton::clicked"):
            section.index("void NotificationsSection::reload()")
        ]
        self.assertIn("const auto saved = repo.set", save_handler)
        self.assertIn("if (saved.is_err())", save_handler)
        self.assertIn("privacy::redact_text(QString::fromStdString(saved.error()))", save_handler)
        self.assertIn("Alert triggers were not saved", save_handler)
        for key in (
            "notifications.inapp", "notifications.price_alerts", "notifications.news_alerts",
            "notifications.order_fills", "notifications.news_breaking", "notifications.news_monitors",
            "notifications.news_deviations", "notifications.news_flash",
        ):
            self.assertIn(key, save_handler)

    def test_general_close_setting_rolls_back_when_storage_fails(self):
        section = (ROOT / "src/screens/settings/GeneralSection.cpp").read_text(encoding="utf-8")
        handler = section[
            section.index("connect(on_close_combo_"):
            section.index("// ── LANGUAGE")
        ]
        reload_path = section[section.index("void GeneralSection::reload"):]
        self.assertIn("const auto saved = SettingsRepository::instance().set", handler)
        self.assertIn("if (saved.is_err())", handler)
        self.assertIn("kSavedOnCloseProp", handler)
        self.assertIn("QSignalBlocker", handler)
        self.assertIn("Could not save the window setting", handler)
        self.assertIn("privacy::redact_text", handler)
        self.assertIn("setProperty(kSavedOnCloseProp", reload_path)

    def test_language_switches_only_after_persistence_succeeds(self):
        header = (ROOT / "src/core/i18n/LanguageManager.h").read_text(encoding="utf-8")
        manager = (ROOT / "src/core/i18n/LanguageManager.cpp").read_text(encoding="utf-8")
        general = (ROOT / "src/screens/settings/GeneralSection.cpp").read_text(encoding="utf-8")
        switcher = (ROOT / "src/ui/widgets/LanguageSwitcher.cpp").read_text(encoding="utf-8")

        self.assertIn("Result<void> set_language", header)
        setter = manager[manager.index("Result<void> LanguageManager::set_language"):]
        self.assertLess(setter.index("SettingsRepository::instance().set"), setter.index("removeTranslator"))
        self.assertIn("if (saved.is_err())", setter)
        self.assertIn("return Result<void>::err", setter)
        self.assertIn("return Result<void>::ok()", setter)

        language_handler = general[general.index("connect(language_combo_"):general.index("// ── CURRENCY")]
        self.assertIn("const auto changed", language_handler)
        self.assertIn("if (changed.is_err())", language_handler)
        self.assertIn("QSignalBlocker", language_handler)
        self.assertIn("Could not save the interface language", language_handler)
        self.assertIn("privacy::redact_text", language_handler)

        self.assertIn("const auto changed", switcher)
        self.assertIn("if (changed.is_err())", switcher)
        self.assertIn("Could not save the interface language", switcher)
        self.assertIn("privacy::redact_text", switcher)

    def test_keybindings_mutate_only_after_storage_succeeds(self):
        header = (ROOT / "src/core/keys/KeyConfigManager.h").read_text(encoding="utf-8")
        manager = (ROOT / "src/core/keys/KeyConfigManager.cpp").read_text(encoding="utf-8")
        section = (ROOT / "src/screens/settings/KeybindingsSection.cpp").read_text(encoding="utf-8")

        self.assertIn("Result<void> set_key", header)
        self.assertIn("Result<void> reset_key", header)
        self.assertIn("Result<void> reset_all", header)
        setter = manager[manager.index("Result<void> KeyConfigManager::set_key"):manager.index("Result<void> KeyConfigManager::reset_key")]
        self.assertLess(setter.index("persist(a, seq)"), setter.index("act->setShortcut(seq)"))
        reset_one = manager[manager.index("Result<void> KeyConfigManager::reset_key"):manager.index("Result<void> KeyConfigManager::reset_all")]
        self.assertLess(reset_one.index("remove(storage_key(a))"), reset_one.index("act->setShortcut(def)"))
        reset_all = manager[manager.index("Result<void> KeyConfigManager::reset_all"):manager.index("find_conflict")]
        self.assertLess(reset_all.index("clear_category"), reset_all.index("act->setShortcut"))

        self.assertGreaterEqual(section.count("Could not save the keybinding"), 2)
        self.assertIn("Could not reset keybindings", section)
        self.assertIn("privacy::redact_text", section)

    def test_llm_settings_diagnostics_scrub_storage_errors_and_profile_identity(self):
        providers = (ROOT / "src/screens/settings/LlmConfigSection_Providers.cpp").read_text(encoding="utf-8")
        profiles = (ROOT / "src/screens/settings/LlmConfigSection_Profiles.cpp").read_text(encoding="utf-8")
        router = (ROOT / "src/services/llm/ModelRouter_Live.cpp").read_text(encoding="utf-8")
        self.assertIn('#include "core/privacy/LogRedact.h"', providers)
        self.assertGreaterEqual(providers.count("privacy::redact_text"), 3)
        self.assertIn("privacy::redact_text(QString::fromStdString(detail))", router)
        self.assertGreaterEqual(profiles.count("log_mask(editing_profile_id_)"), 1)
        self.assertIn("log_mask(profile_id)", profiles)
        self.assertIn("log_mask(profile.id)", profiles)
        self.assertNotIn("LLM profile saved: %1", profiles)
        self.assertNotIn('"LLM profile deleted: " + editing_profile_id_', profiles)

    def test_llm_provider_add_remove_checks_persistence_results(self):
        source = (ROOT / "src/screens/settings/LlmConfigSection_Providers.cpp").read_text(encoding="utf-8")
        add = source[source.index("QWidget* LlmConfigSection::build_provider_list_panel()"):
                     source.index("QWidget* LlmConfigSection::build_form_panel()")]
        remove = source[source.index("void LlmConfigSection::on_delete_provider()"):
                        source.index("void LlmConfigSection::on_save_global()")]
        self.assertIn("auto saved = LlmConfigRepository::instance().save_provider(cfg)", add)
        self.assertIn("if (saved.is_err())", add)
        self.assertGreaterEqual(add.count("privacy::redact_text(error)"), 2)
        self.assertIn("auto removed = LlmConfigRepository::instance().delete_provider(provider)", remove)
        self.assertIn("if (removed.is_err())", remove)
        self.assertIn("privacy::redact_text(error)", remove)
        self.assertNotRegex(remove, r"(?m)^\s*LlmConfigRepository::instance\(\)\.delete_provider\(provider\);$")

    def test_llm_config_deletion_preserves_secret_when_database_delete_fails(self):
        source = (ROOT / "src/storage/repositories/LlmConfigRepository.cpp").read_text(encoding="utf-8")
        provider_delete = source[source.index("Result<void> LlmConfigRepository::delete_provider"):
                                 source.index("Result<LlmGlobalSettings>")]
        model_delete = source[source.index("Result<void> LlmConfigRepository::delete_model"):]
        for name, body in (("provider", provider_delete), ("model", model_delete)):
            with self.subTest(config=name):
                self.assertIn("auto deleted = exec_write", body)
                self.assertIn("if (deleted.is_err())", body)
                self.assertLess(body.index("if (deleted.is_err())"), body.index("SecureStorage::instance().remove"))
                self.assertIn("return deleted", body)

    def test_credential_repositories_remove_secrets_only_after_database_success(self):
        cases = {
            "src/storage/repositories/LlmProfileRepository.cpp": (
                "Result<void> LlmProfileRepository::delete_profile", "Result<void> LlmProfileRepository::set_default"),
            "src/storage/repositories/McpServerRepository.cpp": (
                "Result<void> McpServerRepository::remove", "Result<void> McpServerRepository::set_status"),
            "src/storage/repositories/DataSourceRepository.cpp": (
                "Result<void> DataSourceRepository::remove", "Result<void> DataSourceRepository::update_health"),
        }
        for relative, (start, end) in cases.items():
            with self.subTest(source=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                body = source[source.index(start):source.index(end)]
                self.assertIn("auto deleted = exec_write", body)
                self.assertIn("if (deleted.is_err())", body)
                self.assertLess(body.index("if (deleted.is_err())"), body.index("SecureStorage::instance().remove"))

        profiles = (ROOT / "src/storage/repositories/LlmProfileRepository.cpp").read_text(encoding="utf-8")
        save = profiles[profiles.index("Result<void> LlmProfileRepository::save_profile"):
                        profiles.index("Result<void> LlmProfileRepository::delete_profile")]
        self.assertIn("auto saved = exec_write", save)
        self.assertIn("if (saved.is_err())", save)
        self.assertLess(save.index("if (saved.is_err())"), save.index("SecureStorage::instance().remove"))

    def test_llm_secret_cleanup_failures_are_surfaced_and_reconciled(self):
        config = (ROOT / "src/storage/repositories/LlmConfigRepository.cpp").read_text(encoding="utf-8")
        profiles = (ROOT / "src/storage/repositories/LlmProfileRepository.cpp").read_text(encoding="utf-8")
        secure = (ROOT / "src/storage/secure/SecureStorage.h").read_text(encoding="utf-8")
        self.assertIn("kCleanupFailed", secure)
        self.assertIn("is_cleanup_failure", secure)

        spans = (
            (config, "Result<void> LlmConfigRepository::delete_provider", "Result<LlmGlobalSettings>"),
            (config, "Result<void> LlmConfigRepository::delete_model", None),
            (profiles, "Result<void> LlmProfileRepository::delete_profile", "Result<void> LlmProfileRepository::set_default"),
        )
        for source, start, end in spans:
            with self.subTest(method=start):
                body = source[source.index(start):source.index(end) if end else len(source)]
                self.assertIn("const auto cleaned = SecureStorage::instance().remove", body)
                self.assertIn("if (cleaned.is_err())", body)
                self.assertIn("SecureStorage::kCleanupFailed", body)
                self.assertNotIn("(void)SecureStorage::instance().remove", body)

        profile_save = profiles[profiles.index("Result<void> LlmProfileRepository::save_profile"):
                                profiles.index("Result<void> LlmProfileRepository::delete_profile")]
        self.assertIn("if (cleaned.is_err())", profile_save)
        self.assertIn("SecureStorage::kCleanupFailed", profile_save)

        provider_ui = (ROOT / "src/screens/settings/LlmConfigSection_Providers.cpp").read_text(encoding="utf-8")
        provider_delete = provider_ui[provider_ui.index("void LlmConfigSection::on_delete_provider"):
                                      provider_ui.index("void LlmConfigSection::on_save_global")]
        self.assertIn("SecureStorage::is_cleanup_failure(removed.error())", provider_delete)
        self.assertIn("load_providers()", provider_delete)
        self.assertIn("emit config_changed()", provider_delete)

        profile_ui = (ROOT / "src/screens/settings/LlmConfigSection_Profiles.cpp").read_text(encoding="utf-8")
        profile_delete = profile_ui[profile_ui.index("void LlmConfigSection::on_delete_profile"):
                                    profile_ui.index("void LlmConfigSection::on_set_default_profile")]
        self.assertIn("SecureStorage::is_cleanup_failure(r.error())", profile_delete)
        self.assertIn("clear_profile_form()", profile_delete)
        self.assertIn("load_profiles()", profile_delete)
        self.assertIn("emit config_changed()", profile_delete)

    def test_data_mapping_delete_is_atomic_and_keeps_secret_until_commit(self):
        source = (ROOT / "src/storage/repositories/DataMappingRepository.cpp").read_text(encoding="utf-8")
        body = source[source.index("Result<void> DataMappingRepository::remove"):
                      source.index("Result<void> DataMappingRepository::update_run_stats")]
        self.assertIn("db().begin_transaction()", body)
        self.assertGreaterEqual(body.count("if (deleted.is_err())"), 3)
        self.assertGreaterEqual(body.count("db().rollback()"), 4)
        self.assertIn("auto committed = db().commit()", body)
        self.assertLess(body.index("auto committed = db().commit()"),
                        body.index("SecureStorage::instance().remove"))
        self.assertIn("dm_mark_unreadable(id, false)", body)

    def test_data_mapping_save_commits_row_before_mutating_secure_token(self):
        source = (ROOT / "src/storage/repositories/DataMappingRepository.cpp").read_text(encoding="utf-8")
        body = source[source.index("Result<void> DataMappingRepository::save(const DataMapping& dm)"):
                      source.index("Result<int> DataMappingRepository::save_revision")]
        self.assertNotIn("dm_token_for_column", source)
        self.assertIn("auto saved = exec_write", body)
        self.assertIn("if (saved.is_err())", body)
        for mutation in ("SecureStorage::instance().remove", "SecureStorage::instance().store_verified"):
            self.assertIn(mutation, body)
            self.assertLess(body.index("if (saved.is_err())"), body.index(mutation))
        self.assertIn("UPDATE data_mappings SET auth_token = ''", body)

    def test_data_source_save_commits_row_before_mutating_secure_credentials(self):
        source = (ROOT / "src/storage/repositories/DataSourceRepository.cpp").read_text(encoding="utf-8")
        body = source[source.index("Result<void> DataSourceRepository::save(const DataSource& ds)"):
                      source.index("Result<void> DataSourceRepository::remove")]
        self.assertIn("ds_prepare_save", body)
        self.assertIn("auto result =", body)
        self.assertIn("if (result.is_err())", body)
        for mutation in ("SecureStorage::instance().remove", "SecureStorage::instance().store_verified"):
            self.assertIn(mutation, body)
            self.assertLess(body.index("if (result.is_err())"), body.index(mutation))
        self.assertIn("UPDATE data_sources SET config = ? WHERE id = ?", body)

    def test_llm_and_mcp_saves_commit_rows_before_secure_storage(self):
        cases = {
            "src/storage/repositories/LlmConfigRepository.cpp": (
                ("Result<void> LlmConfigRepository::save_provider", "Result<void> LlmConfigRepository::set_active"),
                ("Result<void> LlmConfigRepository::save_model", "Result<void> LlmConfigRepository::delete_model"),
            ),
            "src/storage/repositories/LlmProfileRepository.cpp": (
                ("Result<void> LlmProfileRepository::save_profile", "Result<void> LlmProfileRepository::delete_profile"),
            ),
            "src/storage/repositories/McpServerRepository.cpp": (
                ("Result<void> McpServerRepository::save", "Result<void> McpServerRepository::remove"),
            ),
        }
        for relative, spans in cases.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            for start, end in spans:
                with self.subTest(source=relative, method=start):
                    body = source[source.index(start):source.index(end)]
                    self.assertIn("auto saved = exec_write", body)
                    self.assertIn("if (saved.is_err())", body)
                    self.assertIn("SecureStorage::instance().store_verified", body)
                    self.assertLess(body.index("if (saved.is_err())"),
                                    body.index("SecureStorage::instance().store_verified"))
                    self.assertIn("UPDATE ", body)

    def test_llm_settings_ui_contains_no_retired_local_backend_wording(self):
        for relative in (
            "src/screens/settings/LlmConfigSection_Providers.cpp",
            "src/screens/settings/LlmConfigSection_Profiles.cpp",
        ):
            with self.subTest(source=relative):
                source = (ROOT / relative).read_text(encoding="utf-8").lower()
                self.assertNotIn("ollama", source)

    def test_llm_profile_save_is_true_upsert_and_preserves_assignments(self):
        source = (ROOT / "src/storage/repositories/LlmProfileRepository.cpp").read_text(encoding="utf-8")
        body = source[source.index("Result<void> LlmProfileRepository::save_profile"):
                      source.index("Result<void> LlmProfileRepository::delete_profile")]
        self.assertNotIn("INSERT OR REPLACE INTO llm_profiles", body)
        self.assertIn("ON CONFLICT(id) DO UPDATE SET", body)
        self.assertIn("reasoning_effort = excluded.reasoning_effort", body)

    def test_mcp_server_save_is_true_upsert_and_preserves_discovered_tools(self):
        source = (ROOT / "src/storage/repositories/McpServerRepository.cpp").read_text(encoding="utf-8")
        body = source[source.index("Result<void> McpServerRepository::save"):
                      source.index("Result<void> McpServerRepository::remove")]
        self.assertNotIn("INSERT OR REPLACE INTO mcp_servers", body)
        self.assertIn("ON CONFLICT(id) DO UPDATE SET", body)
        self.assertIn("status = excluded.status", body)

    def test_llm_provider_activation_rejects_unknown_provider(self):
        source = (ROOT / "src/storage/repositories/LlmConfigRepository.cpp").read_text(encoding="utf-8")
        body = source[source.index("Result<void> LlmConfigRepository::set_active"):
                      source.index("Result<void> LlmConfigRepository::delete_provider")]
        self.assertIn("auto updated = db().execute", body)
        self.assertIn("numRowsAffected() == 0", body)
        self.assertIn('Result<void>::err("Provider not found")', body)

    def test_llm_type_default_assignment_replaces_nullable_row_atomically(self):
        source = (ROOT / "src/storage/repositories/LlmProfileRepository.cpp").read_text(encoding="utf-8")
        body = source[source.index("Result<void> LlmProfileRepository::assign_profile"):
                      source.index("Result<void> LlmProfileRepository::remove_assignment")]
        self.assertIn("if (context_id.isEmpty())", body)
        self.assertIn("db().begin_transaction()", body)
        self.assertIn("context_id IS NULL", body)
        self.assertIn("db().commit()", body)
        self.assertIn("ON CONFLICT(context_type, context_id) DO UPDATE SET", body)

    def test_llm_and_mcp_saves_surface_secure_store_and_scrub_failures(self):
        cases = {
            "src/storage/repositories/LlmConfigRepository.cpp": (
                ("Result<void> LlmConfigRepository::save_provider", "Result<void> LlmConfigRepository::set_active"),
                ("Result<void> LlmConfigRepository::save_model", "Result<void> LlmConfigRepository::delete_model"),
            ),
            "src/storage/repositories/LlmProfileRepository.cpp": (
                ("Result<void> LlmProfileRepository::save_profile", "Result<void> LlmProfileRepository::delete_profile"),
            ),
            "src/storage/repositories/McpServerRepository.cpp": (
                ("Result<void> McpServerRepository::save", "Result<void> McpServerRepository::remove"),
            ),
        }
        for relative, spans in cases.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            for start, end in spans:
                with self.subTest(source=relative, method=start):
                    body = source[source.index(start):source.index(end)]
                    self.assertIn("const auto stored =", body)
                    self.assertIn("if (stored.is_err())", body)
                    self.assertIn("auto scrubbed = exec_write", body)
                    self.assertIn("if (scrubbed.is_err())", body)

    def test_provider_save_preserves_active_state_until_atomic_activation(self):
        source = (ROOT / "src/screens/settings/LlmConfigSection_Providers.cpp").read_text(encoding="utf-8")
        body = source[source.index("void LlmConfigSection::on_save_provider()"):
                      source.index("void LlmConfigSection::on_delete_provider()")]
        self.assertNotIn("cfg.is_active = true", body)
        self.assertIn("bool was_active = false", body)
        self.assertIn("cfg.is_active = was_active", body)
        self.assertLess(body.index("cfg.is_active = was_active"), body.index("save_provider(cfg)"))
        self.assertLess(body.index("save_provider(cfg)"), body.index("set_active(provider)"))

    def test_provider_repository_updates_rows_without_replace_delete(self):
        source = (ROOT / "src/storage/repositories/LlmConfigRepository.cpp").read_text(encoding="utf-8")
        save = source[source.index("Result<void> LlmConfigRepository::save_provider"):
                      source.index("Result<void> LlmConfigRepository::set_active")]
        self.assertNotIn("INSERT OR REPLACE", save)
        self.assertIn("ON CONFLICT(provider) DO UPDATE SET", save)
        self.assertIn("tools_enabled = excluded.tools_enabled", save)

    def test_model_repository_updates_rows_without_replace_delete(self):
        source = (ROOT / "src/storage/repositories/LlmConfigRepository.cpp").read_text(encoding="utf-8")
        save = source[source.index("Result<void> LlmConfigRepository::save_model"):
                      source.index("Result<void> LlmConfigRepository::delete_model")]
        self.assertNotIn("INSERT OR REPLACE", save)
        self.assertIn("ON CONFLICT(id) DO UPDATE SET", save)
        self.assertIn("is_default = excluded.is_default", save)

    def test_llm_config_reads_propagate_secure_storage_failures(self):
        source = (ROOT / "src/storage/repositories/LlmConfigRepository.cpp").read_text(encoding="utf-8")
        self.assertIn("Result<void> hydrate_provider_key", source)
        self.assertIn("Result<void> hydrate_model_key", source)
        self.assertGreaterEqual(source.count("if (sr.failed())"), 2)
        self.assertIn("Could not read secure storage for LLM provider", source)
        self.assertIn("Could not read secure storage for LLM model", source)
        self.assertGreaterEqual(source.count("if (hydrated.is_err())"), 4)

    def test_llm_profile_reads_propagate_secure_storage_failures(self):
        source = (ROOT / "src/storage/repositories/LlmProfileRepository.cpp").read_text(encoding="utf-8")
        self.assertIn("Result<void> hydrate_profile_key", source)
        self.assertIn("Could not read secure storage for LLM profile", source)
        mapper = source[source.index("LlmProfile LlmProfileRepository::map_profile"):
                        source.index("static const char* kSelectCols")]
        self.assertNotIn("SecureStorage::instance().read", mapper)
        self.assertGreaterEqual(source.count("hydrate_profile_key("), 5)
        self.assertGreaterEqual(source.count("if (hydrated.is_err())"), 4)

    def test_mcp_reads_propagate_environment_failures(self):
        source = (ROOT / "src/storage/repositories/McpServerRepository.cpp").read_text(encoding="utf-8")
        self.assertIn("Result<void> hydrate_mcp_env", source)
        self.assertIn("Could not read secure storage for MCP server", source)
        mapper = source[source.index("McpServer McpServerRepository::map_row"):
                        source.index("static const char* kCols")]
        self.assertNotIn("SecureStorage::instance().read", mapper)
        self.assertGreaterEqual(source.count("hydrate_mcp_env("), 3)
        self.assertGreaterEqual(source.count("if (hydrated.is_err())"), 2)

    def test_mcp_reload_preserves_live_configuration_on_read_failure(self):
        source = (ROOT / "src/mcp/McpManager.cpp").read_text(encoding="utf-8")
        initialize = source[source.index("void McpManager::initialize()"):
                            source.index("std::vector<McpServerConfig> McpManager::get_servers")]
        self.assertLess(initialize.index("list_all()"), initialize.index("configs_ = std::move(loaded)"))
        self.assertIn("QHash<QString, McpServerConfig> loaded", initialize)
        self.assertIn("loaded.insert(cfg.id, cfg)", initialize)
        self.assertNotIn("configs_.clear()", initialize)
        swapped = initialize.index("configs_ = std::move(loaded)")
        emitted = initialize.index("emit servers_changed()")
        self.assertIn("}\n    LOG_DEBUG", initialize[swapped:emitted])

    def test_mcp_save_reconciles_rows_committed_before_secret_failure(self):
        source = (ROOT / "src/mcp/McpManager.cpp").read_text(encoding="utf-8")
        save = source[source.index("Result<void> McpManager::save_server"):
                      source.index("Result<void> McpManager::remove_server")]
        failed = save[save.index("if (r.is_err())"):save.index("return Result<void>::ok()")]
        self.assertIn("McpServerRepository::instance().get(srv.id)", failed)
        self.assertIn("persisted_matches", failed)
        self.assertIn("apply_config()", failed)
        self.assertIn("emit servers_changed()", failed)

    def test_mcp_screen_diagnostics_redact_backend_errors(self):
        for relative, minimum in (
            ("src/screens/mcp_servers/McpServersScreen_Actions.cpp", 2),
            ("src/screens/mcp_servers/McpServersScreen_Layout.cpp", 4),
        ):
            with self.subTest(source=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn('#include "core/privacy/LogRedact.h"', source)
                self.assertGreaterEqual(source.count("privacy::redact_text"), minimum)

    def test_llm_requests_surface_configuration_read_failures(self):
        header = (ROOT / "src/services/llm/LlmService.h").read_text(encoding="utf-8")
        source = (ROOT / "src/services/llm/LlmService.cpp").read_text(encoding="utf-8")
        self.assertIn("mutable QString config_error_", header)
        snapshot = header[header.index("struct ConfigSnapshot"):header.index("snapshot_config_locked")]
        self.assertIn("QString config_error", snapshot)
        self.assertIn("config_error_.clear()", source)
        self.assertIn("Could not read LLM configuration", source)
        self.assertIn("Could not read selected LLM profile", source)
        self.assertIn("Could not read global LLM settings", source)
        self.assertIn("s.config_error = config_error_", source)
        self.assertIn("config_error_ = snap.config_error", source)
        self.assertGreaterEqual(source.count("if (!config_error_.isEmpty())"), 2)

    def test_model_router_fails_closed_on_partial_configuration_reads(self):
        header = (ROOT / "src/services/llm/ModelRouter.h").read_text(encoding="utf-8")
        rule = (ROOT / "src/services/llm/ModelRouter.cpp").read_text(encoding="utf-8")
        live = (ROOT / "src/services/llm/ModelRouter_Live.cpp").read_text(encoding="utf-8")
        service = (ROOT / "src/services/llm/LlmService.cpp").read_text(encoding="utf-8")

        self.assertGreaterEqual(header.count("QString config_error"), 2)
        gather = live[live.index("void mrl_gather_db"):live.index("qint64 mrl_gather_harnesses")]
        self.assertIn("s.config_error.clear()", gather)
        self.assertIn('add_config_error(QStringLiteral("LLM profiles")', gather)
        self.assertIn('add_config_error(QStringLiteral("LLM model assignments")', gather)
        self.assertIn('add_config_error(QStringLiteral("LLM providers")', gather)
        resolve = rule[rule.index("RouteResult ModelRouter::resolve_with"):
                       rule.index("FallbackDecision ModelRouter::decide_fallback")]
        self.assertIn("if (!state.config_error.isEmpty())", resolve)
        self.assertIn('QStringLiteral("configuration_error")', resolve)
        snapshot = service[service.index("LlmService::ConfigSnapshot LlmService::snapshot_for"):
                           service.index("void LlmService::stamp_route")]
        self.assertIn("if (!choice.config_error.isEmpty())", snapshot)
        self.assertIn("s.tools_enabled = false", snapshot)
        self.assertLess(snapshot.index("if (!choice.config_error.isEmpty())"),
                        snapshot.index("ensure_config()"))

    def test_agent_profile_resolution_fails_closed_on_storage_errors(self):
        header = (ROOT / "src/storage/repositories/LlmProfileRepository.h").read_text(encoding="utf-8")
        source = (ROOT / "src/storage/repositories/LlmProfileRepository.cpp").read_text(encoding="utf-8")
        self.assertIn("QString config_error", header)
        self.assertIn("Result<QString> read_assignment", header)
        self.assertIn("Result<QString> LlmProfileRepository::read_assignment", source)
        resolver = source[source.index("ResolvedLlmProfile LlmProfileRepository::resolve_for_context"):]
        self.assertGreaterEqual(resolver.count("if (assignment.is_err())"), 2)
        self.assertGreaterEqual(resolver.count("return resolution_error("), 4)
        fallback = source[source.index("ResolvedLlmProfile LlmProfileRepository::legacy_fallback"):
                          source.index("ResolvedLlmProfile LlmProfileRepository::resolve_for_context")]
        self.assertIn("configs.list_providers()", fallback)
        self.assertIn("if (rows.is_err())", fallback)
        self.assertIn("return resolution_error(", fallback)

        llm_service = (ROOT / "src/services/llm/LlmService.cpp").read_text(encoding="utf-8")
        profile_json = llm_service[llm_service.index("QJsonObject LlmService::profile_to_json"):
                                   llm_service.index("int LlmService::resolved_max_tokens")]
        self.assertIn("p.config_error", profile_json)
        self.assertIn('obj[QStringLiteral("configuration_error")]', profile_json)

        quant = (ROOT / "src/services/ai_quant_lab/AIQuantLabService.cpp").read_text(encoding="utf-8")
        config = quant[quant.index("QJsonObject AIQuantLabService::agent_llm_config"):
                       quant.index("// ── Qlib core")]
        self.assertIn("p.config_error", config)
        self.assertIn("aiq_llm_error_config(p.config_error)", config)
        self.assertIn('QStringLiteral("llm_configuration_error")', quant)
        self.assertIn("if (profiles.is_err())", config)
        self.assertGreaterEqual(quant.count("aiq_llm_configuration_error(llm)"), 3)

    def test_selected_agent_profiles_share_runtime_provider_policy(self):
        header = (ROOT / "src/storage/repositories/LlmProfileRepository.h").read_text(encoding="utf-8")
        source = (ROOT / "src/storage/repositories/LlmProfileRepository.cpp").read_text(encoding="utf-8")
        self.assertIn("ResolvedLlmProfile resolve_profile_id", header)
        selected = source[source.index("ResolvedLlmProfile LlmProfileRepository::resolve_profile_id"):
                          source.index("Result<void> LlmProfileRepository::save_profile")]
        self.assertIn("get_profile(id)", selected)
        self.assertIn("runtime_profile_allowed", selected)
        self.assertIn("return resolution_error(", selected)
        self.assertIn("return profile_to_resolved", selected)

    def test_llm_api_keys_never_use_remote_plaintext_endpoints(self):
        service = (ROOT / "src/services/llm/LlmService.cpp").read_text(encoding="utf-8")
        models = (ROOT / "src/services/llm/LlmModelsApi.cpp").read_text(encoding="utf-8")
        providers = (ROOT / "src/screens/settings/LlmConfigSection_Providers.cpp").read_text(encoding="utf-8")
        profiles = (ROOT / "src/screens/settings/LlmConfigSection_Profiles.cpp").read_text(encoding="utf-8")
        http = (ROOT / "src/services/llm/LlmHttpHelpers.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(service.count("is_allowed_llm_endpoint(url)"), 2)
        self.assertIn("is_allowed_llm_endpoint(url)", models)
        self.assertGreaterEqual(providers.count("is_allowed_llm_endpoint"), 3)
        self.assertIn("is_allowed_llm_endpoint(base_url)", profiles)
        self.assertIn("SameOriginRedirectPolicy", models)
        self.assertIn("SameOriginRedirectPolicy", http)

    def test_router_does_not_save_profiles_after_provider_key_read_failure(self):
        source = (ROOT / "src/services/llm/ModelRouter_Live.cpp").read_text(encoding="utf-8")
        body = source[source.index("QString mrl_profile_for"):source.index("} // namespace model_router_live_detail")]
        self.assertIn("const auto rows = LlmConfigRepository::instance().list_providers()", body)
        self.assertIn("if (rows.is_err())", body)
        error_guard = body[body.index("if (rows.is_err())"):body.index("for (const LlmConfig& c", body.index("if (rows.is_err())"))]
        self.assertIn("*error = QString::fromStdString(rows.error())", error_guard)
        self.assertIn("return {}", error_guard)

    def test_clearing_model_choice_surfaces_assignment_delete_failures(self):
        header = (ROOT / "src/services/llm/ModelRouter.h").read_text(encoding="utf-8")
        source = (ROOT / "src/services/llm/ModelRouter_Live.cpp").read_text(encoding="utf-8")
        badge = (ROOT / "src/ui/widgets/ModelBadge.cpp").read_text(encoding="utf-8")
        self.assertIn("bool clear_choice(", header)
        body = source[source.index("bool ModelRouter::clear_choice"):source.index("QString ModelRouter::choice_profile_id")]
        self.assertIn("const auto removed =", body)
        self.assertIn("if (removed.is_err())", body)
        self.assertIn("*error = QString::fromStdString(removed.error())", body)
        self.assertIn("return false", body)
        self.assertIn("return true", body)
        self.assertIn("if (!mb_ai::ModelRouter::instance().clear_choice", badge)
        self.assertIn("Could not reset the model", badge)

    def test_model_picker_surfaces_assignment_save_failures(self):
        badge = (ROOT / "src/ui/widgets/ModelBadge.cpp").read_text(encoding="utf-8")
        pick = badge[badge.index("const auto pick ="):badge.index("QAction* auto_action")]
        self.assertIn("if (!mb_ai::ModelRouter::instance().set_choice", pick)
        self.assertIn("privacy::redact_text(error)", pick)
        self.assertIn("QMessageBox::warning", pick)
        self.assertIn("Could not switch the model", pick)
        self.assertLess(pick.index("QMessageBox::warning"), pick.index("return"))

    def test_deleting_active_provider_selects_deterministic_byo_fallback(self):
        source = (ROOT / "src/screens/settings/LlmConfigSection_Providers.cpp").read_text(encoding="utf-8")
        body = source[source.index("void LlmConfigSection::on_delete_provider()"):
                      source.index("void LlmConfigSection::on_save_global()")]
        self.assertIn("const bool was_active", body)
        self.assertIn("policy::default_llm_provider()", body)
        self.assertIn("provider_is_visible_in_community", body)
        self.assertIn("set_active(fallback)", body)

    def test_llm_settings_surfaces_provider_read_failures(self):
        source = (ROOT / "src/screens/settings/LlmConfigSection_Providers.cpp").read_text(encoding="utf-8")
        load = source[source.index("void LlmConfigSection::load_providers()"):
                      source.index("void LlmConfigSection::populate_form")]
        form = source[source.index("void LlmConfigSection::populate_form"):
                      source.index("void LlmConfigSection::on_provider_selected")]
        self.assertIn("if (result.is_err())", load)
        self.assertIn("Could not read provider settings", load)
        self.assertIn("privacy::redact_text(error)", load)
        provider_error = load[load.index("if (result.is_err())"):load.index("} else {")]
        self.assertIn("api_key_edit_->clear()", provider_error)
        self.assertIn("save_btn_->setEnabled(false)", provider_error)
        self.assertIn("test_btn_->setEnabled(false)", provider_error)
        self.assertIn("if (result.is_err())", form)
        self.assertIn("Could not load provider settings", form)
        self.assertIn("privacy::redact_text(error)", form)
        read = form.index("LlmConfigRepository::instance().list_providers()")
        self.assertLess(form.index("api_key_edit_->clear()"), read)
        self.assertLess(form.index("save_btn_->setEnabled(false)"), read)
        self.assertLess(form.index("test_btn_->setEnabled(false)"), read)
        self.assertGreater(form.index("save_btn_->setEnabled(true)"), form.index("if (result.is_err())"))
        self.assertGreater(form.index("test_btn_->setEnabled(true)"), form.index("if (result.is_err())"))

        self.assertIn("if (gs.is_err())", load)
        self.assertIn("Could not read global LLM settings", load)
        self.assertIn("auto fixed_saved", load)
        self.assertIn("if (fixed_saved.is_err())", load)
        self.assertIn("Could not persist Max Tokens correction", load)
        self.assertEqual(load.count("gs = LlmConfigRepository::instance().get_global_settings()"), 1)

        save_global = source[source.index("void LlmConfigSection::on_save_global()"):
                             source.index("void LlmConfigSection::on_test_connection()")]
        self.assertIn("Failed to save global settings: ", save_global)
        self.assertIn("privacy::redact_text(error)", save_global)

    def test_llm_settings_surfaces_profile_read_failures(self):
        source = (ROOT / "src/screens/settings/LlmConfigSection_Profiles.cpp").read_text(encoding="utf-8")
        self.assertIn('#include "core/privacy/LogRedact.h"', source)

        load = source[source.index("void LlmConfigSection::load_profiles()"):
                      source.index("void LlmConfigSection::populate_profile_form")]
        self.assertIn("if (result.is_err())", load)
        self.assertIn("Could not read LLM profiles", load)
        self.assertIn("privacy::redact_text(error)", load)
        profile_error = load[load.index("if (result.is_err())"):load.index("} else {")]
        self.assertIn("editing_profile_id_.clear()", profile_error)
        self.assertIn("profile_api_key_edit_->clear()", profile_error)
        self.assertIn("profile_save_btn_->setEnabled(false)", profile_error)

        selected = source[source.index("void LlmConfigSection::on_profile_selected"):
                          source.index("void LlmConfigSection::on_profile_provider_changed")]
        self.assertIn("if (r.is_err())", selected)
        self.assertIn("Could not load LLM profile", selected)
        self.assertIn("privacy::redact_text(error)", selected)
        read = selected.index("get_profile(id)")
        self.assertLess(selected.index("editing_profile_id_.clear()"), read)
        self.assertLess(selected.index("profile_api_key_edit_->clear()"), read)
        self.assertLess(selected.index("profile_save_btn_->setEnabled(false)"), read)

        populate = source[source.index("void LlmConfigSection::populate_profile_form"):
                          source.index("void LlmConfigSection::clear_profile_form")]
        clear = source[source.index("void LlmConfigSection::clear_profile_form"):
                       source.index("void LlmConfigSection::show_profile_status")]
        self.assertIn("profile_save_btn_->setEnabled(true)", populate)
        self.assertIn("profile_save_btn_->setEnabled(true)", clear)

        changed = source[source.index("void LlmConfigSection::on_profile_provider_changed"):
                         source.index("void LlmConfigSection::on_save_profile()")]
        self.assertIn("if (providers.is_err())", changed)
        self.assertIn("Could not read provider credentials", changed)
        self.assertIn("privacy::redact_text(error)", changed)

        save = source[source.index("void LlmConfigSection::on_save_profile()"):
                      source.index("void LlmConfigSection::on_delete_profile()")]
        self.assertIn("if (providers.is_err())", save)
        self.assertIn("Could not read provider credentials", save)
        self.assertIn("if (existing.is_err())", save)
        self.assertIn("Could not reload profile before save", save)
        self.assertGreaterEqual(save.count("privacy::redact_text(error)"), 2)

    def test_profile_save_reconciles_rows_committed_before_secret_failure(self):
        source = (ROOT / "src/screens/settings/LlmConfigSection_Profiles.cpp").read_text(encoding="utf-8")
        save = source[source.index("void LlmConfigSection::on_save_profile()"):
                      source.index("void LlmConfigSection::on_delete_profile()")]
        failed = save[save.index("if (r.is_err())"):save.index("show_profile_status(tr(\"Profile saved\")")]
        self.assertIn("get_profile(profile.id)", failed)
        self.assertIn("persisted_matches", failed)
        self.assertIn("editing_profile_id_ = profile.id", failed)
        self.assertIn("populate_profile_form(persisted.value())", failed)
        self.assertIn("emit config_changed()", failed)
        self.assertIn("privacy::redact_text(error)", failed)

    def test_profile_delete_and_default_diagnostics_keep_identity_and_scrub_errors(self):
        source = (ROOT / "src/screens/settings/LlmConfigSection_Profiles.cpp").read_text(encoding="utf-8")
        delete = source[source.index("void LlmConfigSection::on_delete_profile()"):
                        source.index("void LlmConfigSection::on_set_default_profile()")]
        self.assertIn("const QString profile_id = editing_profile_id_", delete)
        self.assertIn("delete_profile(profile_id)", delete)
        self.assertIn("log_mask(profile_id)", delete)
        self.assertNotIn("log_mask(editing_profile_id_)", delete)
        self.assertIn("privacy::redact_text(error)", delete)

        default = source[source.index("void LlmConfigSection::on_set_default_profile()"):
                         source.index("} // namespace fincept::screens")]
        self.assertIn("privacy::redact_text(error)", default)

    def test_local_chat_mode_diagnostics_scrub_conversation_storage_errors(self):
        source = (ROOT / "src/screens/chat_mode/ChatModeScreen.cpp").read_text(encoding="utf-8")
        self.assertIn('#include "core/privacy/LogRedact.h"', source)
        for name in ("history.error()", "saved.error()"):
            self.assertIn(f"privacy::redact_text(QString::fromStdString({name}))", source)
        self.assertGreaterEqual(source.count("privacy::redact_text(QString::fromStdString(saved.error()))"), 2)
        self.assertNotIn(".arg(QString::fromStdString(history.error()))", source)
        self.assertNotIn(".arg(QString::fromStdString(saved.error()))", source)

    def test_agent_task_persistence_diagnostics_scrub_errors_and_identifiers(self):
        expected = {
            "src/services/agent_tasks/TaskRunStore.cpp": 1,
            "src/services/agent_tasks/RunTemplateLibrary.cpp": 1,
            "src/services/agent_tasks/ArtefactService.cpp": 4,
            "src/services/agent_tasks/TaskRunScheduler.cpp": 4,
            "src/services/agent_tasks/TaskRunService.cpp": 6,
        }
        for relative, minimum in expected.items():
            with self.subTest(source=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn('#include "core/privacy/LogRedact.h"', source)
                self.assertGreaterEqual(source.count("privacy::redact_text"), minimum)

        scheduler = (ROOT / "src/services/agent_tasks/TaskRunScheduler.cpp").read_text(encoding="utf-8")
        artefacts = (ROOT / "src/services/agent_tasks/ArtefactService.cpp").read_text(encoding="utf-8")
        self.assertIn("log_mask(copy.id)", scheduler)
        self.assertIn("log_mask(id)", scheduler)
        self.assertGreaterEqual(artefacts.count("log_mask("), 4)
        self.assertNotIn(".arg(copy.id.left(8), QString::fromStdString(r.error()))", scheduler)
        self.assertNotIn(".arg(id.left(8), QString::fromStdString(r.error()))", scheduler)

    def test_agent_task_transactions_release_failed_commits(self):
        source = (ROOT / "src/services/agent_tasks/TaskRunStore.cpp").read_text(encoding="utf-8")
        spans = (
            ("Result<void> TaskRunStore::delete_runs", "Result<int> TaskRunStore::prune_runs"),
            ("Result<int> TaskRunStore::prune_runs", "Result<int> TaskRunStore::mark_unfinished_interrupted"),
            ("Result<void> TaskRunStore::replace_steps", "Result<void> TaskRunStore::upsert_step"),
            ("Result<void> TaskRunStore::delete_artefacts", "// ── Templates"),
        )
        for start, end in spans:
            with self.subTest(method=start):
                body = source[source.index(start):source.index(end)]
                self.assertNotIn("return db().commit()", body)
                commit = body[body.rindex("db().commit()") :]
                self.assertIn("if (committed.is_err())", commit)
                self.assertIn("db().rollback()", commit)
                self.assertLess(commit.index("db().rollback()"), commit.index("return"))

        coding = (ROOT / "src/services/coding_agents/CodingAgentRepository.cpp").read_text(encoding="utf-8")
        for start, end in (
            ("Result<void> CodingAgentRepository::delete_runs", "Result<int> CodingAgentRepository::prune_runs"),
            ("Result<int> CodingAgentRepository::prune_runs",
             "Result<int> CodingAgentRepository::mark_unfinished_interrupted"),
        ):
            with self.subTest(method=start):
                body = coding[coding.index(start):coding.index(end)]
                self.assertNotIn("return db().commit()", body)
                commit = body[body.rindex("db().commit()") :]
                self.assertIn("if (committed.is_err())", commit)
                self.assertIn("db().rollback()", commit)
                self.assertLess(commit.index("db().rollback()"), commit.index("return"))

    def test_broker_auth_diagnostics_redact_provider_errors(self):
        for relative, minimum in (
            ("src/trading/brokers/angelone/AngelOneBroker.cpp", 2),
            ("src/trading/brokers/zerodha/ZerodhaBroker.cpp", 2),
            ("src/trading/brokers/upstox/UpstoxBroker.cpp", 2),
            ("src/trading/brokers/kotak/KotakBroker.cpp", 4),
        ):
            with self.subTest(broker=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn('#include "core/privacy/LogRedact.h"', source)
                self.assertGreaterEqual(source.count("privacy::redact_text"), minimum)

    def test_broker_stream_auth_diagnostics_redact_provider_frames(self):
        paths = {
            "src/trading/auth/RedirectServer.cpp": 1,
            "src/trading/brokers/alpaca/AlpacaWebSocket.cpp": 4,
            "src/trading/websocket/AngelOneWebSocket.cpp": 1,
            "src/trading/websocket/FyersWebSocket.cpp": 2,
            "src/trading/websocket/UpstoxWebSocket.cpp": 2,
        }
        for relative, minimum in paths.items():
            with self.subTest(stream=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn('#include "core/privacy/LogRedact.h"', source)
                self.assertGreaterEqual(source.count("privacy::redact_text"), minimum)
        angel = (ROOT / "src/trading/websocket/AngelOneWebSocket.cpp").read_text(encoding="utf-8")
        self.assertNotIn("msg.left(200)", angel)

    def test_broker_http_diagnostics_redact_external_errors(self):
        source = (ROOT / "src/trading/brokers/BrokerHttp.cpp").read_text(encoding="utf-8")
        self.assertIn('#include "core/privacy/LogRedact.h"', source)
        self.assertGreaterEqual(source.count("privacy::redact_text(result.error)"), 2)
        self.assertNotIn('{"error", result.error}', source)

    def test_broker_response_redactor_covers_identity_fields(self):
        source = (ROOT / "src/trading/brokers/BrokerLogRedact.h").read_text(encoding="utf-8")
        for key in (
            "dhanclientid",
            "client_id",
            "user_id",
            "account_number",
            "first_name",
            "last_name",
            "address",
            "dateofbirth",
        ):
            with self.subTest(key=key):
                self.assertIn(f'QStringLiteral("{key}")', source)

    def test_portfolio_import_logs_do_not_persist_rejected_rows(self):
        source = (ROOT / "src/services/portfolio/PortfolioService_ImportExport.cpp").read_text(encoding="utf-8")
        self.assertIn('#include "core/privacy/LogRedact.h"', source)
        self.assertIn("privacy::redact_text(raw)", source)
        self.assertIn("privacy::mask_id(target_id)", source)
        self.assertNotIn("arg(problems.first())", source)
        self.assertNotIn('"Import: " + raw_errors.join', source)
        self.assertNotIn('" + file_path', source)

    def test_portfolio_summary_logs_mask_ids_errors_and_holdings(self):
        source = (ROOT / "src/services/portfolio/PortfolioService_Summary.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("log_mask(portfolio_id)"), 8)
        self.assertGreaterEqual(source.count("log_mask(portfolio.broker_account_id)"), 2)
        self.assertGreaterEqual(source.count("log_scrub("), 4)
        self.assertNotIn("summary.unpriced.join", source)
        self.assertNotIn("summary.fx_missing.join", source)

    def test_paper_mark_diagnostics_mask_portfolios_and_external_errors(self):
        source = (ROOT / "src/trading/PaperMarkService.cpp").read_text(encoding="utf-8")
        self.assertIn('#include "core/privacy/LogRedact.h"', source)
        self.assertGreaterEqual(source.count("log_mask(portfolio_id)"), 4)
        self.assertGreaterEqual(source.count("log_mask(pid)"), 2)
        self.assertIn("privacy::redact_text(e.what())", source)
        self.assertNotIn(".arg(portfolio_id, b.account_currency)", source)
        self.assertNotIn(".arg(n).arg(pid)", source)

    def test_account_stream_diagnostics_mask_accounts_and_broker_errors(self):
        source = (ROOT / "src/trading/AccountDataStream.cpp").read_text(encoding="utf-8")
        self.assertIn('#include "core/privacy/LogRedact.h"', source)
        self.assertIn("return privacy::mask_id(account_id);", source)
        self.assertIn("return privacy::redact_text(error);", source)
        self.assertGreaterEqual(source.count("ads_log_account("), 20)
        self.assertGreaterEqual(source.count("ads_log_error("), 12)
        self.assertNotIn(".arg(bid, acct_id, result.error)", source)
        self.assertNotIn('QString("WS error: %1").arg(e)', source)

    def test_data_stream_manager_diagnostics_mask_accounts_and_topics(self):
        source = (ROOT / "src/trading/DataStreamManager.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("log_mask(account_id)"), 6)
        self.assertGreaterEqual(source.count("dsm_log_topic(topic)"), 3)
        self.assertNotIn('"refresh: ignoring malformed topic: " + topic', source)
        self.assertNotIn('"refresh: no stream for " + topic', source)
        self.assertNotIn('"refresh: throttled " + topic', source)

    def test_trading_screen_diagnostics_mask_ids_and_external_errors(self):
        source = (ROOT / "src/screens/equity_trading/EquityTradingScreen_Handlers.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("log_mask(order_id)"), 5)
        self.assertGreaterEqual(source.count("log_mask(result.order_id)"), 2)
        self.assertGreaterEqual(source.count("log_scrub("), 8)
        self.assertIn("log_mask(account.paper_portfolio_id)", source)
        self.assertNotIn('QString("Order failed: %1").arg(result.message)', source)
        self.assertNotIn('{"order_id", order_id}', source)

    def test_equity_stream_diagnostics_mask_accounts_and_provider_errors(self):
        screen = (ROOT / "src/screens/equity_trading/EquityTradingScreen.cpp").read_text(encoding="utf-8")
        streams = (ROOT / "src/screens/equity_trading/EquityTradingScreen_Streams.cpp").read_text(encoding="utf-8")
        for source in (screen, streams):
            self.assertIn('#include "core/privacy/LogRedact.h"', source)
        self.assertIn("log_mask(focused_account_id_)", screen)
        self.assertGreaterEqual(streams.count("log_mask(focused_account_id_)"), 2)
        self.assertGreaterEqual(streams.count("privacy::redact_text("), 5)
        self.assertNotIn(".arg(broker_id_for_focused(), focused_account_id_, why)", streams)
        self.assertNotIn("focused_account_id_, why", streams)

    def test_workflow_trading_diagnostics_mask_order_ids_and_exceptions(self):
        source = (ROOT / "src/services/workflow/adapters/ServiceBridges.cpp").read_text(encoding="utf-8")
        self.assertIn('#include "core/privacy/LogRedact.h"', source)
        self.assertGreaterEqual(source.count("privacy::redact_text(ex.what())"), 8)
        self.assertGreaterEqual(source.count("log_mask(order_id)"), 4)
        self.assertIn("log_mask(restored)", source)
        self.assertNotIn('{{"order_id", order_id}, {"error", ex.what()}}', source)
        self.assertNotIn('{{"error", ex.what()}}', source)

    def test_workflow_database_diagnostics_scrub_driver_details(self):
        source = (ROOT / "src/services/workflow/adapters/ServiceBridges.cpp").read_text(encoding="utf-8")
        self.assertIn("privacy::redact_text(r.error)", source)
        self.assertIn("privacy::redact_text(r.log_detail)", source)
        self.assertNotIn('QString("%1 — %2").arg(r.error, r.log_detail)', source)

    def test_workflow_file_diagnostics_scrub_platform_errors(self):
        source = (ROOT / "src/services/workflow/adapters/ServiceBridges.cpp").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("privacy::redact_text(file.errorString())"), 4)
        self.assertGreaterEqual(source.count("privacy::redact_text(out_file.errorString())"), 2)
        self.assertNotIn('{{"error", file.errorString()}}', source)
        self.assertNotIn('{{"error", out_file.errorString()}}', source)

    def test_exchange_process_diagnostics_redact_external_errors(self):
        pool = (ROOT / "src/trading/ExchangeDaemonPool.cpp").read_text(encoding="utf-8")
        session = (ROOT / "src/trading/ExchangeSession.cpp").read_text(encoding="utf-8")
        for source in (pool, session):
            self.assertIn('#include "core/privacy/LogRedact.h"', source)
        self.assertGreaterEqual(pool.count("privacy::redact_text"), 2)
        self.assertGreaterEqual(session.count("privacy::redact_text"), 5)
        self.assertNotIn('"Daemon stderr: " + err', pool)
        self.assertNotIn('QString("[%1] WS stderr: %2").arg(exchange_id_, err)', session)
        self.assertNotIn('QString("[%1] WS error: %2").arg(exchange_id_, ws_last_error_)', session)

    def test_ma_analytics_diagnostics_redact_python_errors(self):
        source = (ROOT / "src/services/ma_analytics/MAAnalyticsService.cpp").read_text(encoding="utf-8")
        self.assertIn('#include "core/privacy/LogRedact.h"', source)
        self.assertGreaterEqual(source.count("privacy::redact_text(result.error)"), 3)
        self.assertNotIn('.arg(context, result.error)', source)
        self.assertNotIn('.arg(id).arg(result.error)', source)

    def test_public_market_diagnostics_redact_external_errors(self):
        data = (ROOT / "src/services/markets/MarketDataService.cpp").read_text(encoding="utf-8")
        search = (ROOT / "src/services/markets/MarketSearchService.cpp").read_text(encoding="utf-8")
        for source in (data, search):
            self.assertIn('#include "core/privacy/LogRedact.h"', source)
        self.assertGreaterEqual(data.count("privacy::redact_text"), 3)
        self.assertIn("privacy::redact_text(result.error)", search)
        self.assertNotIn('"Batch fetch failed: " + result.error', data)
        self.assertNotIn('"quote_names failed: " + result.error', data)
        self.assertNotIn('"public/local search failed: " + result.error', search)

    def test_news_diagnostics_redact_feed_and_persistence_errors(self):
        service = (ROOT / "src/services/news/NewsService.cpp").read_text(encoding="utf-8")
        live = (ROOT / "src/services/news/NewsService_LiveFeed.cpp").read_text(encoding="utf-8")
        for source in (service, live):
            self.assertIn('#include "core/privacy/LogRedact.h"', source)
        self.assertGreaterEqual(service.count("privacy::redact_text"), 7)
        self.assertIn("privacy::redact_text(text)", live)
        self.assertNotIn('{"error", reason}', service)
        self.assertNotIn('"WebSocket live feed error: " + text', live)
        self.assertNotIn('"Failed to persist BYO analysis: " + QString::fromStdString', service)

    def test_report_persistence_diagnostics_redact_paths_and_database_errors(self):
        inbox = (ROOT / "src/services/report_builder/ReportInbox.cpp").read_text(encoding="utf-8")
        builder = (ROOT / "src/services/report_builder/ReportBuilderService.cpp").read_text(encoding="utf-8")
        library = (ROOT / "src/services/report_builder/ReportLibrary.cpp").read_text(encoding="utf-8")
        for source in (inbox, builder, library):
            self.assertIn('#include "core/privacy/LogRedact.h"', source)
        self.assertIn("privacy::redact_text(r.error)", inbox)
        self.assertIn("privacy::redact_text(f.errorString())", builder)
        self.assertIn("privacy::redact_text(out.error)", builder)
        self.assertIn("privacy::redact_text(rl_error(r.error()))", library)
        self.assertNotIn('QStringLiteral("append failed: %1").arg(r.error)', inbox)
        self.assertNotIn('"Autosave could not be written: " + f.errorString()', builder)
        self.assertNotIn('"Library save failed: " + out.error', builder)
        self.assertNotIn('.arg(id).arg(rl_error(r.error()))', library)

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
