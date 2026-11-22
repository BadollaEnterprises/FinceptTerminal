import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]


class ZeroCreditDashboardWiringTest(unittest.TestCase):
    def test_dashboard_data_tiles_fetch_public_adapters_in_community_mode(self):
        source = (ROOT / "src/screens/dashboard/widgets/DataApiTiles.cpp").read_text(encoding="utf-8")
        self.assertIn('#include "python/PythonRunner.h"', source)
        self.assertIn('QStringLiteral("byo_dashboard_data.py")', source)
        self.assertIn("fd::parse_envelope", source)
        community = source[source.index("if (!policy::fincept_metered_services_enabled())"):
                           source.index("// A plan that includes /data", source.index("if (!policy::fincept_metered_services_enabled())"))]
        self.assertNotIn('footer_->setText(tr("PUBLIC QUOTES', community)
        self.assertIn("run_with_options", community)

    def test_dashboard_cached_public_data_never_claims_refresh_is_billed(self):
        source = (ROOT / "src/screens/dashboard/widgets/DataApiTiles.cpp").read_text(encoding="utf-8")
        block = source[source.index("void DataApiTile::set_footer"):
                       source.index("void DataApiTile::on_theme_changed")]
        self.assertIn("fincept_metered_services_enabled", block)
        self.assertIn("0 Fincept credits", block)

    def test_dashboard_health_is_device_local_in_community_mode(self):
        policy = (ROOT / "src/core/policy/CommunityPolicy.h").read_text(encoding="utf-8")
        health_policy = policy[policy.index("fincept_hosted_health_enabled"):]
        self.assertIn("return false", health_policy[:300])

        service = (ROOT / "src/services/connectivity/ConnectivityService.cpp").read_text(encoding="utf-8")
        probe = service[service.index("void ConnectivityService::probe"):]
        self.assertLess(probe.index("fincept_hosted_health_enabled"), probe.index("new QNetworkAccessManager"))
        self.assertIn('"community_local", true', probe[:1800])
        self.assertIn("hub.publish", probe[:1800])

        widget = (ROOT / "src/screens/dashboard/widgets/ConnectionHealthWidget.cpp").read_text(encoding="utf-8")
        api_row = widget[widget.index("void ConnectionHealthWidget::append_api_row"):
                         widget.index("void ConnectionHealthWidget::append_python_row")]
        self.assertIn("community_local", api_row)
        self.assertIn("COMMUNITY RUNTIME", api_row)
        self.assertIn("BYO AI", api_row)

        status = (ROOT / "src/screens/dashboard/DashboardStatusBar.cpp").read_text(encoding="utf-8")
        health = status[status.index("void DashboardStatusBar::on_health"):
                        status.index("void DashboardStatusBar::set_api_state")]
        self.assertIn("community_local", health)
        self.assertIn("LAT: LOCAL", health)

    def test_crypto_dashboard_names_its_actual_public_source(self):
        source = (ROOT / "src/screens/dashboard/widgets/DataApiTiles.cpp").read_text(encoding="utf-8")
        block = source[source.index("CryptoOverviewWidget::CryptoOverviewWidget"):
                       source.index("// ── Earnings calendar")]
        self.assertIn("CoinPaprika", block)
        self.assertNotIn("CoinGecko", block)

    def test_settings_show_source_and_cost_dashboard(self):
        source = (ROOT / "src/screens/settings/DataSourcesSection.cpp").read_text(encoding="utf-8")
        self.assertIn("ZERO-CREDIT ROUTES", source)
        self.assertIn("DeepSeek", source)
        self.assertIn("GDELT", source)
        self.assertIn("World Bank", source)
        self.assertIn("Stooq", source)
        self.assertIn("METERED FINCEPT CALLS", source)
        self.assertIn("BLOCKED", source)
        self.assertIn("PROVIDER HEALTH", source)
        self.assertIn("PERSISTENT USAGE", source)
        self.assertIn("DEEPSEEK TOKENS", source)
        self.assertIn("AUTO FALLBACK", source)
        self.assertIn("GDELT → RSS / Atom", source)
        self.assertIn("QuantLib (590 routes)", source)
        self.assertIn("MONTHLY SAVINGS", source)
        self.assertIn("METERED CALLS AVOIDED", source)
        self.assertIn("LAST SUCCESS", source)

    def test_usage_ledger_is_configured_in_the_profile_data_directory(self):
        main = (ROOT / "src/app/main.cpp").read_text(encoding="utf-8")
        self.assertIn("LocalUsageMetrics::set_storage_path", main)
        self.assertIn("local_usage.json", main)

    def test_successful_public_research_fetches_update_the_usage_ledger(self):
        equity = (ROOT / "src/services/fincept_data/FinceptDataService.cpp").read_text(encoding="utf-8")
        community = equity[equity.index("void FinceptDataService::fetch_community") :
                           equity.index("void FinceptDataService::dispatch")]
        self.assertIn('core/metrics/LocalUsageMetrics.h', equity)
        self.assertIn("record_public_data(result.meta.source)", community)
        self.assertLess(community.index("if (result.ok())"),
                        community.index("record_public_data(result.meta.source)"))

        calendar = (ROOT / "src/services/economics/MacroCalendarService.cpp").read_text(encoding="utf-8")
        public_calendar = calendar[calendar.index("if (!fincept::policy::fincept_metered_services_enabled())") :
                                   calendar.index("// Account-only data", calendar.index(
                                       "if (!fincept::policy::fincept_metered_services_enabled())"))]
        self.assertIn('core/metrics/LocalUsageMetrics.h', calendar)
        self.assertIn('record_public_data(QStringLiteral("forex_factory"))', public_calendar)
        self.assertLess(public_calendar.index("if (events.isEmpty())"),
                        public_calendar.index('record_public_data(QStringLiteral("forex_factory"))'))

        news = (ROOT / "src/services/news/NewsService.cpp").read_text(encoding="utf-8")
        finish = news[news.index("void NewsService::finish_fetch") :
                      news.index("void NewsService::load_recent_from_db")]
        self.assertIn('core/metrics/LocalUsageMetrics.h', news)
        self.assertIn('record_public_data(QStringLiteral("rss"))', finish)
        self.assertIn("if (!all.isEmpty())", finish)

    def test_community_chat_mode_hides_hosted_credit_controls(self):
        form = (ROOT / "src/screens/chat_mode/RunRequestForm.cpp").read_text(encoding="utf-8")
        build = form[form.index("void RunRequestForm::Impl::build()") :
                     form.index("void RunRequestForm::Impl::retranslate()")]
        self.assertIn("credits->parentWidget()->setVisible(policy::fincept_cloud_agents_enabled())", build)

        dialog = (ROOT / "src/screens/chat_mode/NewRunDialog.cpp").read_text(encoding="utf-8")
        constructor = dialog[dialog.index("NewRunDialog::NewRunDialog") :
                             dialog.index("NewRunDialog::~NewRunDialog")]
        self.assertIn("if (policy::fincept_cloud_agents_enabled())", constructor)
        self.assertLess(constructor.index("if (policy::fincept_cloud_agents_enabled())"),
                        constructor.index("ChatModeService::instance().get_credits"))

        status = (ROOT / "src/screens/chat_mode/RunStatusBar.cpp").read_text(encoding="utf-8")
        refresh = status[status.index("void RunStatusBar::Impl::refresh()") :]
        self.assertIn("!policy::fincept_cloud_agents_enabled()", refresh)
        self.assertIn("bar->set_item_visible(rsb_id(kRsbCredits), false)", refresh)
        self.assertLess(refresh.index("!policy::fincept_cloud_agents_enabled()"),
                        refresh.index('RunStatusBar::tr("CREDITS %1")'))

    def test_public_adapter_is_packaged_and_never_reads_credentials(self):
        source = (ROOT / "scripts/byo_public_data.py").read_text(encoding="utf-8")
        self.assertNotIn("api.fincept", source.lower())
        self.assertNotIn("API_KEY", source)
        self.assertIn("public_zero_credit", source)

    def test_ollama_and_codex_are_hidden_from_product_model_pickers(self):
        llm = (ROOT / "src/screens/settings/LlmConfigSection.cpp").read_text(encoding="utf-8")
        harness = (ROOT / "src/screens/settings/LlmConfigSection_Harness.cpp").read_text(encoding="utf-8")
        arena = (ROOT / "src/services/alpha_arena/ArenaModelRegistry.cpp").read_text(encoding="utf-8")
        self.assertIn("is_ai_backend_selectable", llm)
        self.assertIn("is_ai_backend_selectable", harness)
        self.assertGreaterEqual(arena.count("is_ai_backend_selectable"), 3)

        picker_files = [
            "src/screens/agent_config/HarnessListWidget.cpp",
            "src/screens/agent_config/HarnessRunOptionsWidget.cpp",
            "src/screens/agent_config/AgentRail.cpp",
            "src/screens/agent_config/AgentConfigScreen_Actions.cpp",
            "src/screens/agent_config/AgentRunPanel.cpp",
            "src/screens/settings/AgentConnectSection.cpp",
            "src/screens/settings/LlmConfigSection_Profiles.cpp",
        ]
        for relative in picker_files:
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("is_ai_backend_selectable", source, relative)

    def test_shared_provider_catalog_only_advertises_user_owned_api_backends(self):
        source = (ROOT / "src/services/llm/ProviderCatalog.cpp").read_text(encoding="utf-8")
        catalog = source[source.index("ProviderCatalog::known_providers"):
                         source.index("ProviderCatalog::is_blocked")]
        self.assertNotIn('"ollama"', catalog)
        self.assertNotIn('"fincept"', catalog)
        self.assertIn('"deepseek"', catalog)

    def test_saved_legacy_provider_rows_cannot_reactivate_old_backends(self):
        settings = (ROOT / "src/screens/settings/LlmConfigSection_Providers.cpp").read_text(encoding="utf-8")
        load = settings[settings.index("void LlmConfigSection::load_providers"):
                        settings.index("void LlmConfigSection::on_provider_selected")]
        self.assertIn("provider_is_visible_in_community", load)
        self.assertIn("is_ai_backend_selectable", settings)
        self.assertIn("fincept_metered_services_enabled", settings)
        self.assertIn("default_llm_provider", load)

        service = (ROOT / "src/services/llm/LlmService.cpp").read_text(encoding="utf-8")
        ensure = service[service.index("void LlmService::ensure_config"):
                         service.index("LlmService::ConfigSnapshot LlmService::snapshot_config_locked")]
        self.assertIn("llm_svc_provider_allowed", ensure)
        self.assertIn("is_deprecated_llm_provider", service)
        self.assertIn("fincept_metered_services_enabled", service)

        snapshot = service[service.index("LlmService::ConfigSnapshot LlmService::snapshot_for"):
                           service.index("void LlmService::stamp_route")]
        self.assertIn("s.provider = choice.provider", snapshot)
        self.assertIn("list_providers", snapshot)

    def test_provider_settings_actions_reject_retired_backends_before_io(self):
        source = (ROOT / "src/screens/settings/LlmConfigSection_Providers.cpp").read_text(encoding="utf-8")
        save = source[source.index("void LlmConfigSection::on_save_provider()"):
                      source.index("void LlmConfigSection::on_delete_provider()")]
        test = source[source.index("void LlmConfigSection::on_test_connection()"):
                      source.index("void LlmConfigSection::on_fetch_models()")]
        fetch = source[source.index("void LlmConfigSection::on_fetch_models()"):
                       source.index("void LlmConfigSection::on_models_fetched")]
        for action in (save, test, fetch):
            self.assertIn("is_ai_backend_selectable(provider)", action)
        self.assertLess(save.index("is_ai_backend_selectable(provider)"), save.index("save_provider(cfg)"))
        self.assertLess(test.index("is_ai_backend_selectable(provider)"), test.index("fetch_models(provider"))
        self.assertLess(fetch.index("is_ai_backend_selectable(provider)"), fetch.index("fetch_models(provider"))

    def test_retired_ai_backends_are_blocked_at_network_boundaries(self):
        catalog = (ROOT / "src/services/llm/ProviderCatalog.cpp").read_text(encoding="utf-8")
        endpoint = catalog[catalog.index("QString ProviderCatalog::chat_endpoint"):]
        self.assertLess(endpoint.index("is_deprecated_llm_provider"), endpoint.index("if (!base_url.isEmpty())"))

        models = (ROOT / "src/services/llm/LlmModelsApi.cpp").read_text(encoding="utf-8")
        models_url = models[models.index("QString LlmService::get_models_url"):
                            models.index("QMap<QString, QString> LlmService::get_models_headers")]
        self.assertLess(models_url.index("is_deprecated_llm_provider"), models_url.index('if (p == "ollama")'))

        service = (ROOT / "src/services/llm/LlmService.cpp").read_text(encoding="utf-8")
        request = service[service.index("LlmResponse LlmService::do_request"):
                          service.index("LlmResponse LlmService::do_streaming_request")]
        streaming = service[service.index("LlmResponse LlmService::do_streaming_request"):
                            service.index("// Public API")]
        self.assertLess(request.index("llm_svc_provider_allowed"), request.index("get_endpoint_url()"))
        self.assertLess(streaming.index("llm_svc_provider_allowed"), streaming.index("QNetworkReply* reply"))

    def test_ai_chat_is_not_ready_without_the_selected_byo_api_key(self):
        service = (ROOT / "src/services/llm/LlmService.cpp").read_text(encoding="utf-8")
        configured = service[service.index("bool LlmService::is_configured() const"):
                             service.index("ResolvedLlmProfile LlmService::resolve_profile")]
        self.assertIn("snapshot_for(c", configured)
        self.assertIn("ProviderCatalog::requires_api_key", configured)
        self.assertIn("api_key", configured)
        self.assertNotIn("A route always ends with Fincept LLM", configured)

        request = service[service.index("LlmResponse LlmService::do_request"):
                          service.index("LlmResponse LlmService::do_streaming_request")]
        streaming = service[service.index("LlmResponse LlmService::do_streaming_request"):
                            service.index("// Public API")]
        self.assertLess(request.index("requires_api_key"), request.index("get_endpoint_url()"))
        self.assertLess(streaming.index("requires_api_key"), streaming.index("QNetworkReply* reply"))
        self.assertIn("No LLM provider configured", request)
        self.assertIn("No LLM provider configured", streaming)

    def test_auth_retires_saved_fincept_llm_instead_of_auto_provisioning_it(self):
        header = (ROOT / "src/auth/AuthManager.h").read_text(encoding="utf-8")
        source = (ROOT / "src/auth/AuthManager.cpp").read_text(encoding="utf-8")

        self.assertNotIn("auto_configure_fincept_llm", header)
        self.assertNotIn("auto_configure_fincept_llm", source)
        self.assertIn("reconcile_llm_provider_policy", header)
        self.assertIn("reconcile_llm_provider_policy", source)

        reconcile = source[source.index("void AuthManager::reconcile_llm_provider_policy()") :]
        self.assertIn("fincept_metered_services_enabled", reconcile[:5000])
        self.assertIn('delete_provider(QStringLiteral("fincept"))', reconcile[:5000])
        self.assertIn("is_ai_backend_selectable", reconcile[:5000])
        self.assertIn("default_llm_provider", reconcile[:5000])
        self.assertIn("LlmProfileRepository::instance()", reconcile[:5000])
        self.assertIn("list_all_profiles", reconcile[:5000])
        self.assertIn("delete_profile", reconcile[:5000])
        self.assertNotIn('fincept_llm.provider = "fincept"', reconcile[:5000])

        initialize = source[source.index("void AuthManager::initialize()") :
                            source.index("void AuthManager::validate_saved_session()")]
        self.assertIn("reconcile_llm_provider_policy();", initialize)
        self.assertLess(initialize.index("reconcile_llm_provider_policy();"),
                        initialize.index("saved_session_failure_.failed()"))

    def test_report_drafting_preflights_its_own_byo_route(self):
        header = (ROOT / "src/services/llm/LlmService.h").read_text(encoding="utf-8")
        self.assertIn("bool is_configured(const ChatRoute& route) const", header)

        backend = (ROOT / "src/services/report_builder/ReportAiLlmBackend.cpp").read_text(encoding="utf-8")
        model_info = backend[backend.index("AiModelInfo ReportAiLlmBackend::model_info() const"):
                             backend.index("void ReportAiLlmBackend::deliver_chunk")]
        self.assertIn("is_configured", model_info)
        self.assertIn("kReportDraftFeature", model_info)
        self.assertIn("needs_setup = true", model_info)
        self.assertIn("own API key", model_info)

    def test_report_drafting_reuses_identical_byo_results_but_retry_bypasses_cache(self):
        session = (ROOT / "src/services/report_builder/ReportAiSession.cpp").read_text(encoding="utf-8")
        backend = (ROOT / "src/services/report_builder/ReportAiLlmBackend.cpp").read_text(encoding="utf-8")

        send = session[session.index("bool ReportAiSession::send()") :
                       session.index("void ReportAiSession::cancel()")]
        self.assertIn("AiCachePolicy::PreferCache", send)
        self.assertIn("AiCachePolicy::BypassCache", send)

        start = backend[backend.index("void ReportAiLlmBackend::start") :
                        backend.index("void ReportAiLlmBackend::cancel")]
        self.assertIn("ReportAiDraftCache.h", backend)
        self.assertIn("AppPaths::cache()", backend)
        self.assertIn("draft_cache_key", start)
        self.assertIn("load_draft_cache", start)
        self.assertIn("AiCachePolicy::PreferCache", start)
        self.assertIn("no API call", start)

        finished = backend[backend.index("void ReportAiLlmBackend::on_finished") :]
        self.assertIn("store_draft_cache", finished)
        self.assertIn("fallback_notice.isEmpty()", finished)

    def test_quick_ai_surfaces_preflight_the_route_they_execute(self):
        bubble = (ROOT / "src/screens/ai_chat/AiChatBubble.cpp").read_text(encoding="utf-8")
        send = bubble[bubble.index("void AiChatBubble::on_send()"):
                      bubble.index("void AiChatBubble::on_request_started")]
        self.assertIn("is_configured(quick_route)", send)
        self.assertIn("kFeatureQuickChat", send)

        quick = (ROOT / "src/screens/dashboard/widgets/AiQuickAskWidget.cpp").read_text(encoding="utf-8")
        refresh = quick[quick.index("void AiQuickAskWidget::refresh_config_state()"):
                        quick.index("void AiQuickAskWidget::cancel_in_flight")]
        self.assertGreaterEqual(refresh.count("is_configured(quick_route)"), 2)
        self.assertIn("kFeatureQuickAsk", refresh)

    def test_quick_ask_reuses_exact_byo_answers_without_suggesting_fincept_setup(self):
        quick = (ROOT / "src/screens/dashboard/widgets/AiQuickAskWidget.cpp").read_text(encoding="utf-8")
        self.assertNotIn("choose the Fincept provider", quick)
        self.assertIn("your own provider API key", quick)

        ask = quick[quick.index("void AiQuickAskWidget::on_ask()"):
                    quick.index("void AiQuickAskWidget::cancel_in_flight()")]
        self.assertIn("quick_ask_answer_cache_key", ask)
        self.assertIn("CacheManager::instance().try_get", ask)
        self.assertIn("Saved answer reused", quick)
        self.assertLess(ask.index("CacheManager::instance().try_get"), ask.index("llm.chat_streaming"))

        finished = quick[quick.index("void AiQuickAskWidget::on_finished"):
                         quick.index("void AiQuickAskWidget::set_state")]
        self.assertIn("quick_ask_response_is_cacheable", finished)
        self.assertIn("fallback_notice", finished)
        self.assertIn("CacheManager::instance().put", finished)

    def test_settings_never_display_the_retired_deepseek_flash_alias(self):
        providers = (ROOT / "src/screens/settings/LlmConfigSection_Providers.cpp").read_text(encoding="utf-8")
        load = providers[providers.index("void LlmConfigSection::load_providers"):
                         providers.index("void LlmConfigSection::populate_form")]
        self.assertIn("normalize_llm_model(p.provider, p.model)", load)

        profiles = (ROOT / "src/screens/settings/LlmConfigSection_Profiles.cpp").read_text(encoding="utf-8")
        load_profiles = profiles[profiles.index("void LlmConfigSection::load_profiles"):
                                 profiles.index("void LlmConfigSection::populate_profile_form")]
        self.assertIn("normalize_llm_model(p.provider, p.model_id)", load_profiles)

    def test_profiles_hide_deprecated_local_and_codex_harness_choices(self):
        profiles = (ROOT / "src/screens/settings/LlmConfigSection_Profiles.cpp").read_text(encoding="utf-8")
        self.assertIn('lph_profile_provider_is_hidden', profiles)
        hidden = profiles[profiles.index("bool lph_profile_provider_is_hidden") :
                          profiles.index("// ── Harness profiles")]
        self.assertIn("is_ai_backend_selectable", hidden)
        self.assertNotIn("Claude Code / Codex setup", profiles)

    def test_agent_profile_repository_filters_retired_and_metered_routes_before_payloads(self):
        source = (ROOT / "src/storage/repositories/LlmProfileRepository.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', source)

        self.assertIn("bool runtime_profile_allowed", source)
        helper = source[source.index("bool runtime_profile_allowed") :
                        source.index("} // namespace")]
        self.assertIn("is_deprecated_llm_provider", helper)
        self.assertIn("fincept_metered_services_enabled", helper)

        listing = source[source.index("Result<QVector<LlmProfile>> LlmProfileRepository::list_profiles") :
                         source.index("Result<QVector<LlmProfile>> LlmProfileRepository::list_all_profiles")]
        self.assertIn("runtime_profile_allowed", listing)

        resolve = source[source.index("ResolvedLlmProfile LlmProfileRepository::resolve_for_context") :]
        self.assertGreaterEqual(resolve.count("runtime_profile_allowed"), 3)

        fallback = source[source.index("ResolvedLlmProfile LlmProfileRepository::legacy_fallback") :
                          source.index("ResolvedLlmProfile LlmProfileRepository::resolve_for_context")]
        self.assertIn("default_llm_provider", fallback)
        self.assertIn("list_providers", fallback)

    def test_quantlib_credit_failures_do_not_offer_paid_upgrade(self):
        result_view = (ROOT / "src/screens/quantlib/QuantLibResultView.cpp").read_text(encoding="utf-8")
        self.assertIn("fincept_metered_services_enabled", result_view)
        self.assertIn("NO PAID FALLBACK", result_view)

    def test_help_explains_zero_credit_community_policy(self):
        help_source = (ROOT / "src/screens/info/HelpScreen.cpp").read_text(encoding="utf-8")
        section = help_source[help_source.index("What uses credits"):]
        self.assertIn("community credit firewall", section)
        self.assertIn("DeepSeek", section)
        self.assertNotIn("top up from Plans", section[:900])

    def test_metered_feature_failures_point_to_zero_credit_alternatives(self):
        cases = {
            "src/screens/economics/panels/FinceptMacroPanel.cpp": "World Bank / IMF / FRED",
            "src/services/economics/MacroCalendarService.cpp": "community credit firewall",
            "src/screens/geopolitics/GeopoliticsScreen.cpp": "GDELT / RSS",
            "src/services/maritime/MaritimeService.cpp": "community credit firewall",
            "src/screens/maritime/MaritimeScreen_Handlers.cpp": "community credit firewall",
        }
        for relative, expected in cases.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(expected, source, relative)
            self.assertNotIn("Top up or", source, relative)

    def test_calendar_credit_failure_does_not_offer_plans(self):
        source = (ROOT / "src/screens/calendars/CalendarPanel.cpp").read_text(encoding="utf-8")
        no_credits = source[source.index("case ErrorKind::NoCredits:"):source.index("case ErrorKind::NotSignedIn:")]
        self.assertIn("community credit firewall", no_credits)
        self.assertNotIn("VIEW PLANS", no_credits)
        self.assertNotIn("open_setup_link", no_credits)

    def test_community_chrome_hides_commercial_promotions(self):
        policy = (ROOT / "src/core/policy/CommunityPolicy.h").read_text(encoding="utf-8")
        toolbar = (ROOT / "src/ui/navigation/ToolBar.cpp").read_text(encoding="utf-8")
        status = (ROOT / "src/ui/navigation/StatusBar.cpp").read_text(encoding="utf-8")
        settings = (ROOT / "src/screens/settings/SettingsScreen.cpp").read_text(encoding="utf-8")
        lock_screen = (ROOT / "src/screens/auth/LockScreen.cpp").read_text(encoding="utf-8")
        dashboard = (ROOT / "src/screens/dashboard/DashboardScreen.cpp").read_text(encoding="utf-8")
        start_page = (ROOT / "src/screens/ai_chat/AiChatStartPage.cpp").read_text(encoding="utf-8")
        messaging = (ROOT / "src/screens/ai_chat/AiChatScreen_Messaging.cpp").read_text(encoding="utf-8")
        launchpad = (ROOT / "src/screens/launchpad/LaunchpadScreen.cpp").read_text(encoding="utf-8")
        self.assertIn("commercial_promotions_enabled", policy)
        self.assertIn("commercial_promotions_enabled", toolbar)
        self.assertIn("commercial_promotions_enabled", status)
        self.assertIn("commercial_promotions_enabled", settings)
        self.assertIn("commercial_promotions_enabled", lock_screen)
        self.assertIn("commercial_promotions_enabled", start_page)
        self.assertIn("commercial_promotions_enabled", messaging)
        self.assertIn("commercial_promotions_enabled", launchpad)
        strip = dashboard[dashboard.index("// ── Enterprise strip"):
                          dashboard.index("// ── Main Content")]
        self.assertIn("commercial_promotions_enabled", strip)
        market_hint = status[status.index('PlanHintLabel(QStringLiteral("market_data")') - 300:]
        self.assertIn("if (policy::commercial_promotions_enabled())", market_hint[:500])

    def test_onboarding_and_about_do_not_advertise_paid_editions_in_community_mode(self):
        tour = (ROOT / "src/screens/launchpad/OnboardingTour.cpp").read_text(encoding="utf-8")
        about = (ROOT / "src/screens/about/AboutScreen.cpp").read_text(encoding="utf-8")

        self.assertIn('core/policy/CommunityPolicy.h', tour)
        build = tour[tour.index("void OnboardingTour::build_ui") :
                     tour.index("QWidget* OnboardingTour::build_step")]
        self.assertIn("commercial_promotions_enabled", build)
        self.assertIn("? 5 : 4", build)

        translate = tour[tour.index("void OnboardingTour::retranslateUi") :
                         tour.index("void OnboardingTour::on_next")]
        paid_step = translate[translate.index("step_labels_.size() > 4") - 80 :]
        self.assertIn("commercial_promotions_enabled", paid_step)

        self.assertIn('core/policy/CommunityPolicy.h', about)
        enterprise = about[about.index("// Enterprise —") :
                           about.index("vl->addWidget(row)")]
        self.assertIn("if (policy::commercial_promotions_enabled())", enterprise)

    def test_contact_and_historical_credit_cards_have_no_paid_cta_in_community_mode(self):
        contact = (ROOT / "src/screens/info/ContactScreen.cpp").read_text(encoding="utf-8")
        transcript = (ROOT / "src/screens/chat_mode/TranscriptBlocks.cpp").read_text(encoding="utf-8")

        self.assertIn('core/policy/CommunityPolicy.h', contact)
        enterprise_card = contact[contact.index('make_contact_card(tr("ENTERPRISE') - 200 :
                                  contact.index('make_contact_card(tr("ENTERPRISE') + 900]
        self.assertIn("commercial_promotions_enabled", enterprise_card)

        self.assertIn('core/policy/CommunityPolicy.h', transcript)
        credits = transcript[transcript.index("class CtbCredits") :
                             transcript.index("class CtbError")]
        self.assertIn("commercial_promotions_enabled", credits)
        community = credits[:credits.index("GET PRO")]
        self.assertIn("configured DeepSeek", community)
        self.assertIn("return", community)

    def test_community_toolbar_hides_fincept_balance_plan_and_pricing_entry(self):
        toolbar = (ROOT / "src/ui/navigation/ToolBar.cpp").read_text(encoding="utf-8")

        constructor = toolbar[:toolbar.index("void ToolBar::changeEvent")]
        self.assertIn("credits_label_->setVisible(policy::commercial_promotions_enabled())", constructor)
        self.assertIn("plan_btn_->setVisible(policy::commercial_promotions_enabled())", constructor)

        responsive = toolbar[toolbar.index("auto wanted ="):
                             toolbar.index("auto width_of =", toolbar.index("auto wanted ="))]
        self.assertIn("wd == credits_label_ || wd == plan_btn_", responsive)
        self.assertIn("commercial_promotions_enabled", responsive)

        overflow = toolbar[toolbar.index("void ToolBar::fill_overflow_menu"):
                           toolbar.index("void ToolBar::update_clock")]
        self.assertIn("policy::commercial_promotions_enabled() && credits_label_", overflow)
        self.assertIn("policy::commercial_promotions_enabled() && plan_btn_", overflow)

        help_menu = toolbar[toolbar.index("QMenu* ToolBar::build_help_menu") :]
        self.assertIn("if (policy::commercial_promotions_enabled())", help_menu)
        self.assertIn('m->addAction(tr("Plans && Pricing…")', help_menu)

    def test_commercial_actions_are_absent_from_community_command_discovery(self):
        actions = (ROOT / "src/core/actions/builtin_actions.cpp").read_text(encoding="utf-8")
        seeds = (ROOT / "src/core/command/CommandSeed.cpp").read_text(encoding="utf-8")
        search = (ROOT / "src/ui/navigation/search/SearchSources.cpp").read_text(encoding="utf-8")

        actions_start = actions.index('// ── Help-level actions')
        commercial_actions = actions[actions_start:actions.index('LOG_DEBUG(kBuiltinTag', actions_start)]
        self.assertIn("policy::commercial_promotions_enabled()", commercial_actions)
        self.assertIn('"help.enterprise"', commercial_actions)
        self.assertIn('"billing.plans"', commercial_actions)

        sections = seeds[seeds.index("QList<CommandItem> section_items()"):
                         seeds.index("QList<CommandItem> setting_items()")]
        self.assertIn("commercial_promotions_enabled", sections)
        self.assertIn('section == QLatin1String("billing")', sections)

        listed = search[search.index("bool ss_action_listed"):
                        search.index("void ss_sync_action")]
        self.assertIn("commercial_promotions_enabled", listed)
        self.assertIn('def.id == QLatin1String("billing.plans")', listed)
        self.assertIn('def.id == QLatin1String("help.enterprise")', listed)

    def test_community_profile_cannot_revive_credit_usage_or_billing_panels(self):
        profile = (ROOT / "src/screens/profile/ProfileScreen.cpp").read_text(encoding="utf-8")

        header = profile[profile.index("void ProfileScreen::build_header"):
                         profile.index("void ProfileScreen::build_tab_nav")]
        self.assertIn("credits_badge_->setVisible(policy::commercial_promotions_enabled())", header)
        self.assertIn("plan_badge_->setVisible(policy::commercial_promotions_enabled())", header)

        overview = profile[profile.index("QWidget* ProfileScreen::build_overview"):
                           profile.index("QWidget* ProfileScreen::build_usage")]
        self.assertIn("cred->setVisible(policy::commercial_promotions_enabled())", overview)

        navigation = profile[profile.index("void ProfileScreen::build_tab_nav"):
                             profile.index("QWidget* ProfileScreen::build_overview")]
        self.assertIn("commercial_profile_section_enabled", navigation)

        open_section = profile[profile.index("bool ProfileScreen::open_section"):
                               profile.index("// ── Search box rows")]
        self.assertIn("commercial_profile_section_enabled", open_section)

        commands = profile[profile.index("QList<command::CommandItem> prof_command_rows") :]
        self.assertIn("commercial_promotions_enabled", commands)
        self.assertIn("if (policy::commercial_promotions_enabled())", commands)

    def test_core_symbol_search_uses_local_and_public_sources(self):
        policy = (ROOT / "src/core/policy/CommunityPolicy.h").read_text(encoding="utf-8")
        self.assertIn("fincept_hosted_symbol_services_enabled", policy)

        sources = (ROOT / "src/services/instruments/InstrumentResolverSources.cpp").read_text(encoding="utf-8")
        production = sources[sources.index("ResolverSources isrs_production_sources()"):
                             sources.index("kIsrsRegistered")]
        self.assertIn("fincept_hosted_symbol_services_enabled", production)
        self.assertIn("if (policy::fincept_hosted_symbol_services_enabled())", production)

        market = (ROOT / "src/services/markets/MarketSearchService.cpp").read_text(encoding="utf-8")
        self.assertIn("InstrumentResolver::instance().search", market)
        self.assertIn("DBnomicsService::instance().global_search", market)
        self.assertNotIn("HttpClient::instance()", market)
        self.assertNotIn('QString("/market/search', market)

        main = (ROOT / "src/app/main.cpp").read_text(encoding="utf-8")
        sync = main[main.index("InstrumentSync::instance().start") - 200:
                    main.index("InstrumentSync::instance().start") + 100]
        self.assertIn("fincept_hosted_symbol_services_enabled", sync)

    def test_ollama_is_not_part_of_the_managed_python_environment(self):
        requirements = (ROOT / "resources/requirements-numpy2.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("ollama", requirements)

    def test_deprecated_backends_are_not_user_facing(self):
        files = [
            "src/screens/settings/LlmConfigSection_Providers.cpp",
            "src/screens/settings/LlmConfigSection_Profiles.cpp",
            "src/screens/settings/AgentConnectSection.cpp",
            "src/screens/docs/DocsScreen_Pages_Data.cpp",
            "src/screens/agent_config/AgentRunsPanel.cpp",
            "src/screens/chat_mode/ComposerContextBar.cpp",
            "src/screens/chat_mode/RunStatusBar.cpp",
            "src/screens/chat_mode/ChatComposer.cpp",
            "src/screens/chat_mode/ChatContextPage.cpp",
            "src/screens/chat_mode/RunViewLogic.h",
            "src/ui/widgets/ModelBadge.cpp",
            "src/core/command/CommandSeed.cpp",
        ]
        visible_literal = re.compile(r'(?:tr|QT_TRANSLATE_NOOP)\([^\n]*"[^"\n]*(?:Codex|Ollama)', re.IGNORECASE)
        for relative in files:
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIsNone(visible_literal.search(source), relative)

    def test_codex_harness_registration_obeys_community_policy(self):
        source = (ROOT / "src/services/harness/HarnessRegistry.cpp").read_text(encoding="utf-8")
        registration = source[source.index("HarnessRegistry::HarnessRegistry"):]
        self.assertIn("deprecated_ai_backends_enabled", registration[:1200])

    def test_ai_surfaces_route_credit_failures_to_byo_instead_of_billing(self):
        cases = [
            "src/screens/ai_chat/AiChatBubble.cpp",
            "src/screens/ai_chat/AiChatScreen_Messaging.cpp",
            "src/screens/dashboard/widgets/AiQuickAskWidget.cpp",
            "src/screens/chat_mode/ChatModeService.cpp",
        ]
        for relative in cases:
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("Settings", source, relative)
            self.assertNotIn("Top up", source, relative)

    def test_portfolio_insights_identifies_byo_provider_cost_without_fincept_credits(self):
        source = (ROOT / "src/screens/portfolio/PortfolioInsightsPanel.cpp").read_text(encoding="utf-8")

        self.assertNotIn('tr("Uses your LLM provider — may use credits")', source)
        self.assertNotIn('translate(ctx, "Uses your LLM provider — may use credits")', source)
        self.assertIn("0 Fincept credits", source)
        self.assertGreaterEqual(source.count("insights_ai_cost_note()"), 3)  # definition + both previews
        self.assertGreaterEqual(source.count('"fincept_cost_route", "byo_api"'), 2)

    def test_ai_credit_notice_does_not_offer_view_plans(self):
        source = (ROOT / "src/screens/ai_chat/AiChatTranscript.cpp").read_text(encoding="utf-8")
        credits = source[source.index("case NoticeKind::Credits:"):source.index("case NoticeKind::SignIn:")]
        self.assertIn("return {llm, retry}", credits)
        self.assertNotIn("ViewPlans", credits)
        self.assertNotIn("VIEW PLANS", credits)

    def test_normal_workspace_commercial_actions_obey_community_policy(self):
        cases = [
            "src/ui/widgets/ModelBadge.cpp",
            "src/ui/widgets/PlanHint.cpp",
            "src/ui/widgets/ProAnalytics.cpp",
            "src/screens/news/NewsDetailPanel.cpp",
        ]
        for relative in cases:
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("commercial_promotions_enabled", source, relative)

    def test_shared_plan_hints_describe_the_community_credit_firewall(self):
        source = (ROOT / "src/ui/widgets/PlanHint.cpp").read_text(encoding="utf-8")
        badge = source[source.index("QString badge_tooltip()") : source.index("QString credits_tooltip()")]
        credits = source[source.index("QString credits_tooltip()") : source.index("QString out_of_credits_note")]
        render = source[source.index("void PlanHintLabel::render()") : source.index("// ── PlanBenefitsView")]

        self.assertIn("fincept_metered_services_enabled", badge)
        self.assertIn("community credit firewall", badge.lower())
        self.assertIn("fincept_metered_services_enabled", credits)
        self.assertIn("DeepSeek", credits)
        self.assertIn("commercial_promotions_enabled", render)

    def test_shared_credit_requirements_never_route_community_users_to_plans(self):
        source = (ROOT / "src/ui/widgets/Requirements.cpp").read_text(encoding="utf-8")
        start = source.index("case RequirementKind::Credits:")
        credits = source[start:source.index("        st.ok = false", start)]
        self.assertIn("fincept_metered_services_enabled", credits)
        self.assertIn("community credit firewall", credits)

    def test_metered_data_screens_hide_credit_and_plan_chrome(self):
        cases = [
            "src/screens/maritime/MaritimeScreen_Layout.cpp",
            "src/screens/maritime/MaritimeScreen_Handlers.cpp",
            "src/screens/geopolitics/GeopoliticsScreen.cpp",
        ]
        for relative in cases:
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("fincept_metered_services_enabled", source, relative)
        maritime = (ROOT / cases[0]).read_text(encoding="utf-8")
        geopolitics = (ROOT / cases[2]).read_text(encoding="utf-8")
        self.assertIn("LOCAL SAMPLE · 0 FINCEPT CREDITS", maritime)
        self.assertIn("PUBLIC DATA · 0 FINCEPT CREDITS", geopolitics)

    def test_equity_research_community_adapters_auto_load_without_billed_chrome(self):
        source = (ROOT / "src/screens/equity_research/EquityDataTab.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', source)
        remembered = source[source.index("void EquityDataTab::show_remembered_or_offer"):]
        self.assertLess(remembered.index("fincept_metered_services_enabled"), remembered.index("request(/*refresh=*/false)"))
        frame = source[source.index("void EquityDataTab::retranslate_frame"):
                       source.index("void EquityDataTab::set_symbol")]
        self.assertIn("0 Fincept credits", frame)
        freshness = source[source.index("void EquityDataTab::update_freshness"):]
        self.assertIn("public/local source", freshness)

        options = (ROOT / "src/screens/equity_research/EquityOptionsTab.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', options)
        self.assertGreaterEqual(options.count("if (policy::fincept_metered_services_enabled())"), 3)

    def test_unconfigured_model_badge_never_implies_a_fincept_credit_fallback(self):
        source = (ROOT / "src/ui/widgets/ModelBadge.cpp").read_text(encoding="utf-8")
        self.assertIn('tr("No AI configured")', source)
        self.assertIn("your configured API provider", source)

    def test_historical_fincept_chat_cannot_reenable_the_metered_model(self):
        source = (ROOT / "src/screens/ai_chat/AiChatScreen_Sessions.cpp").read_text(encoding="utf-8")
        self.assertIn("fincept_metered_services_enabled", source)
        self.assertIn("configured = policy::fincept_metered_services_enabled()", source)
        self.assertIn("disabled by the community credit firewall", source)

    def test_historical_chat_routes_are_normalized_and_deprecated_routes_cannot_be_reenabled(self):
        policy = (ROOT / "src/core/policy/CommunityPolicy.h").read_text(encoding="utf-8")
        types = (ROOT / "src/screens/ai_chat/AiChatTypes.cpp").read_text(encoding="utf-8")
        rail = (ROOT / "src/screens/ai_chat/AiChatSessionRail.cpp").read_text(encoding="utf-8")
        sessions = (ROOT / "src/screens/ai_chat/AiChatScreen_Sessions.cpp").read_text(encoding="utf-8")
        repository = (ROOT / "src/storage/repositories/ChatRepository.cpp").read_text(encoding="utf-8")

        self.assertIn("is_deprecated_llm_provider", policy)
        self.assertIn("is_deprecated_llm_provider", types)
        self.assertIn("normalize_llm_model", types)
        self.assertIn("is_deprecated_llm_provider", rail)
        self.assertIn("normalize_llm_model", rail)
        self.assertIn("is_deprecated_llm_provider(conv_provider_)", sessions)
        self.assertIn("normalize_llm_model(msg.provider, msg.model)", sessions)
        self.assertIn("SELECT m.provider FROM chat_messages", repository)
        self.assertIn("ORDER BY m.timestamp DESC, m.rowid DESC", repository)
        self.assertIn("provider = ?, model = ?", repository)

    def test_cloud_sync_is_local_only_and_never_offers_a_paid_recovery(self):
        engine = (ROOT / "src/services/cloud/CloudSyncEngine.cpp").read_text(encoding="utf-8")
        settings = (ROOT / "src/screens/settings/CloudSyncSection.cpp").read_text(encoding="utf-8")
        self.assertIn("fincept_cloud_sync_enabled", engine)
        self.assertIn("LOCAL-ONLY MODE", settings)
        self.assertNotIn("Get more credits with Pro", settings)

    def test_chat_mode_never_registers_or_posts_to_fincept_cloud_agents(self):
        registry = (ROOT / "src/services/agent_tasks/TaskRunService.cpp").read_text(encoding="utf-8")
        client = (ROOT / "src/services/agent_tasks/CloudAgentClient.cpp").read_text(encoding="utf-8")
        self.assertIn("fincept_cloud_agents_enabled", registry)
        self.assertIn("fincept_cloud_agents_enabled", client)
        send = client[client.index("void CloudAgentClient::send"):]
        self.assertLess(send.index("fincept_cloud_agents_enabled"), send.index("stream_network()->post"))

    def test_chat_mode_never_registers_legacy_agent_harnesses(self):
        registry = (ROOT / "src/services/agent_tasks/TaskRunService.cpp").read_text(encoding="utf-8")
        initialization = registry[registry.index("void TaskRunService::initialize_deferred") :
                                  registry.index("ArtefactService::instance().initialize_deferred")]
        self.assertIn("chat_mode_harnesses_enabled", initialization)
        self.assertLess(initialization.index("chat_mode_harnesses_enabled"),
                        initialization.index("register_executor(new HarnessRunExecutor)"))

        composer = (ROOT / "src/screens/chat_mode/ComposerContextBar.cpp").read_text(encoding="utf-8")
        set_engine = composer[composer.index("void ComposerContextBar::set_engine") :
                              composer.index("bool ComposerContextBar::run_as_task")]
        self.assertIn("chat_mode_harnesses_enabled", set_engine)
        self.assertLess(set_engine.index("chat_mode_harnesses_enabled"),
                        set_engine.index("CcbChoice::Harness"))

        form = (ROOT / "src/screens/chat_mode/RunRequestForm.cpp").read_text(encoding="utf-8")
        engine_value = form[form.index("int RunRequestForm::engine_value") :
                            form.index("QString RunRequestForm::executor_id")]
        self.assertIn("chat_mode_harnesses_enabled", engine_value)
        self.assertLess(engine_value.index("chat_mode_harnesses_enabled"),
                        engine_value.index("return kRrfHarness"))

    def test_chat_mode_defaults_to_the_local_byo_executor(self):
        composer = (ROOT / "src/screens/chat_mode/ComposerContextBar.cpp").read_text(encoding="utf-8")
        form = (ROOT / "src/screens/chat_mode/RunRequestForm.cpp").read_text(encoding="utf-8")
        service = (ROOT / "src/services/agent_tasks/TaskRunService.cpp").read_text(encoding="utf-8")
        scheduler = (ROOT / "src/services/agent_tasks/TaskRunScheduler.cpp").read_text(encoding="utf-8")
        self.assertIn("fincept_cloud_agents_enabled", composer)
        self.assertIn("CcbChoice::Local", composer)
        self.assertIn("fincept_cloud_agents_enabled", form)
        self.assertIn("kRrfLocal", form)
        start = service[service.index("QString TaskRunService::start_run") :
                        service.index("QString TaskRunService::enqueue")]
        self.assertIn("runtime_engine(request.engine)", start)
        self.assertLess(start.index("runtime_engine(request.engine)"), start.index("executor_for(engine"))
        retry = service[service.index("QString TaskRunService::retry_from_step") :]
        self.assertIn("run.engine = runtime_engine(src->engine)", retry[:5000])
        self.assertLess(retry.index("run.engine = runtime_engine(src->engine)"), retry.index("return enqueue(run, steps)"))
        fire = scheduler[scheduler.index("void TaskRunScheduler::Impl::fire") :]
        self.assertIn("runtime_engine(s->engine)", fire[:6500])
        self.assertLess(fire.index("runtime_engine(s->engine)"), fire.index("start_blocker"))
        self.assertLess(fire.index("runtime_engine(s->engine)"), fire.index("executor_for"))

    def test_local_only_schedule_editor_hides_fincept_credit_cap_chrome(self):
        source = (ROOT / "src/screens/chat_mode/RunSchedulesDialog.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', source)
        build = source[source.index("void RunSchedulesDialog::Impl::build") :
                       source.index("void RunSchedulesDialog::Impl::wire")]
        self.assertIn("fincept_cloud_agents_enabled", build)
        self.assertIn("cap_lbl->setVisible(cloud_agents)", build)
        self.assertIn("cap->setVisible(cloud_agents)", build)
        self.assertIn("cap_today->setVisible(cloud_agents)", build)
        preview = source[source.index("void RunSchedulesDialog::Impl::update_preview") :
                         source.index("void RunSchedulesDialog::Impl::save")]
        self.assertIn("if (policy::fincept_cloud_agents_enabled())", preview)
        self.assertLess(preview.index("if (policy::fincept_cloud_agents_enabled())"),
                        preview.index("daily cap of scheduled runs still applies"))

    def test_hosted_chat_api_is_blocked_before_any_network_request(self):
        source = (ROOT / "src/screens/chat_mode/ChatModeService.cpp").read_text(encoding="utf-8")
        self.assertIn("fincept_chat_api_enabled", source)
        for marker, network_call in [
            ("void ChatModeService::get_status", "nam_->get"),
            ("void ChatModeService::post_status", "nam_->post"),
            ("void ChatModeService::put", "nam_->put"),
            ("void ChatModeService::del", "nam_->deleteResource"),
        ]:
            section = source[source.index(marker):]
            self.assertLess(section.index("fincept_chat_api_enabled"), section.index(network_call), marker)

    def test_chat_mode_creates_device_local_sessions_without_fincept(self):
        source = (ROOT / "src/screens/chat_mode/ChatModeScreen.cpp").read_text(encoding="utf-8")
        service = (ROOT / "src/screens/chat_mode/ChatModeService.cpp").read_text(encoding="utf-8")
        self.assertNotIn("local-", source)
        ensure = source[source.index("void ChatModeScreen::ensure_active_session"):]
        self.assertIn("ChatModeService::instance().create_session", ensure)
        create = service[service.index("void ChatModeService::create_session"):]
        self.assertLess(create.index("fincept_chat_api_enabled"), create.index("ChatRepository::instance().create_session"))

    def test_chat_mode_hides_hosted_conversations_memory_and_mcp_sections(self):
        source = (ROOT / "src/screens/chat_mode/ChatModeLeftRail.cpp").read_text(encoding="utf-8")
        self.assertIn("fincept_chat_api_enabled", source)
        self.assertIn("setVisible", source)
        select = source[source.index("void ChatModeLeftRail::Impl::select"):]
        self.assertIn("kLrRuns", select[:500])

    def test_terminal_tool_bridge_never_polls_the_disabled_hosted_chat_api(self):
        source = (ROOT / "src/screens/chat_mode/TerminalToolBridge.cpp").read_text(encoding="utf-8")
        self.assertIn("fincept_chat_api_enabled", source)
        wanted = source[source.index("bool TerminalToolBridge::wanted() const"):]
        self.assertIn("fincept_chat_api_enabled", wanted[:300])
        start = source[source.index("void TerminalToolBridge::start()"):]
        self.assertLess(start.index("fincept_chat_api_enabled"), start.index("active_ = true"))

    def test_local_chat_context_does_not_offer_hosted_investment_profiles(self):
        source = (ROOT / "src/screens/chat_mode/ComposerContextBar.cpp").read_text(encoding="utf-8")
        menu = source[source.index("void ComposerContextBar::Impl::fill_add_menu()"):]
        self.assertIn("fincept_chat_api_enabled", menu[:800])
        loader = source[source.index("void ComposerContextBar::Impl::load_profiles()"):]
        self.assertLess(loader.index("fincept_chat_api_enabled"), loader.index("list_profiles"))

    def test_local_chat_ui_hides_stale_cloud_setup_and_controls(self):
        start = (ROOT / "src/screens/chat_mode/ChatStartPage.cpp").read_text(encoding="utf-8")
        context = (ROOT / "src/screens/chat_mode/ChatContextPage.cpp").read_text(encoding="utf-8")
        composer = (ROOT / "src/screens/chat_mode/ChatComposer.cpp").read_text(encoding="utf-8")
        context_bar = (ROOT / "src/screens/chat_mode/ComposerContextBar.cpp").read_text(encoding="utf-8")
        self.assertNotIn("Connect a coding agent (optional)", start)
        self.assertIn("fincept_chat_api_enabled", context)
        self.assertIn("mcp_refresh->setVisible", context)
        self.assertIn("optimize->setVisible", composer)
        self.assertIn("Add a symbol, portfolio or a text file", context_bar)

    def test_ai_quant_agents_reject_fincept_hosted_llm_profiles(self):
        service = (ROOT / "src/services/ai_quant_lab/AIQuantLabService.cpp").read_text(encoding="utf-8")
        panel = (ROOT / "src/screens/ai_quant_lab/QuantModulePanel.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', service)
        resolver = service[service.index("QJsonObject AIQuantLabService::agent_llm_config"):]
        self.assertLess(resolver.index("fincept_metered_services_enabled"), resolver.index("return finalize(chosen)"))
        picker = panel[panel.index("QWidget* QuantModulePanel::build_llm_picker"):]
        self.assertLess(picker.index("fincept_metered_services_enabled"), picker.index("combo->addItem"))
        self.assertIn("default_llm_provider", resolver[:4000])

    def test_ai_quant_agents_migrate_all_deprecated_ai_profiles(self):
        service = (ROOT / "src/services/ai_quant_lab/AIQuantLabService.cpp").read_text(encoding="utf-8")
        resolver = service[service.index("QJsonObject AIQuantLabService::agent_llm_config"):]
        before_return = resolver[:resolver.index("return finalize(chosen)")]
        self.assertIn("is_deprecated_llm_provider(provider)", before_return)
        self.assertIn("default_llm_provider", resolver[:4000])

    def test_settings_migrate_the_legacy_deepseek_model_alias(self):
        providers = (ROOT / "src/screens/settings/LlmConfigSection_Providers.cpp").read_text(encoding="utf-8")
        profiles = (ROOT / "src/screens/settings/LlmConfigSection_Profiles.cpp").read_text(encoding="utf-8")
        router_live = (ROOT / "src/services/llm/ModelRouter_Live.cpp").read_text(encoding="utf-8")
        self.assertIn("normalize_llm_model", providers)
        self.assertIn("normalize_llm_model", profiles)
        provider_form = providers[providers.index("void LlmConfigSection::populate_form"):]
        provider_save = providers[providers.index("void LlmConfigSection::on_save_provider"):]
        profile_form = profiles[profiles.index("void LlmConfigSection::populate_profile_form"):]
        profile_save = profiles[profiles.index("void LlmConfigSection::on_save_profile"):]
        self.assertLess(provider_form.index("normalize_llm_model"), provider_form.index("findText"))
        self.assertLess(provider_save.index("normalize_llm_model"), provider_save.index("cfg.base_url"))
        self.assertIn("setCurrentText(policy::normalize_llm_model", profile_form)
        self.assertLess(profile_save.index("normalize_llm_model"), profile_save.index("profile.model_id"))
        self.assertIn("rp.model = policy::normalize_llm_model", router_live)
        self.assertIn("s.active_row.model = policy::normalize_llm_model", router_live)

    def test_ai_quant_describes_only_the_selected_byo_provider(self):
        source = (ROOT / "src/screens/ai_quant_lab/QuantModulePanel_DeepAgent.cpp").read_text(encoding="utf-8")
        self.assertNotIn("PlanAccess", source)
        self.assertNotIn("hosted model", source)
        self.assertNotIn("included with your plan", source)
        self.assertIn("selected BYO LLM profile", source)

    def test_geopolitics_relations_uses_monitor_data_instead_of_paid_reference_lists(self):
        source = (ROOT / "src/screens/geopolitics/RelationshipPanel.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', source)
        rebuild = source[source.index("void RelationshipPanel::rebuild_sections"):]
        retranslate = source[source.index("void RelationshipPanel::retranslateUi"):]
        self.assertIn("const bool metered = policy::fincept_metered_services_enabled()", rebuild)
        self.assertIn("PUBLIC MONITOR EVENTS", rebuild)
        self.assertIn("0 Fincept credits", rebuild)
        self.assertIn("load_stats_btn_->setVisible(metered && !showing_live_)", retranslate)
        self.assertIn("if (metered)", rebuild)

    def test_geopolitics_monitor_labels_public_actions_as_zero_credit(self):
        source = (ROOT / "src/screens/geopolitics/GeopoliticsScreen.cpp").read_text(encoding="utf-8")
        preview = source[source.index("void GeopoliticsScreen::configure_events_preview"):]
        no_match = source[source.index("void GeopoliticsScreen::configure_no_match_state"):]
        self.assertIn('tr("LOAD PUBLIC EVENTS")', preview[:3000])
        self.assertIn("0 Fincept credits", preview[:3000])
        self.assertLess(preview.index("fincept_metered_services_enabled"), preview.index("PlanAccess"))
        self.assertIn("0 Fincept credits", no_match[:1800])
        self.assertIn('metered ? tr("⟳ REFRESH · 3 CR") : tr("⟳ REFRESH")', source)
        self.assertIn('QStringLiteral("GDELT / RSS + HDX")', source)
        filter_panel = source[source.index("QWidget* GeopoliticsScreen::build_filter_panel"):]
        self.assertLess(filter_panel.index("fincept_metered_services_enabled"),
                        filter_panel.index('setProperty("fincept_agent_risk", "spend")'))
        self.assertIn("LOAD PUBLIC EVENTS", source[source.index("state_changed"):source.index("state_changed") + 1300])

    def test_maritime_community_mode_runs_local_vessels_instead_of_a_paid_api(self):
        service = (ROOT / "src/services/maritime/MaritimeService.cpp").read_text(encoding="utf-8")
        layout = (ROOT / "src/screens/maritime/MaritimeScreen_Layout.cpp").read_text(encoding="utf-8")
        handlers = (ROOT / "src/screens/maritime/MaritimeScreen_Handlers.cpp").read_text(encoding="utf-8")
        self.assertIn("MaritimeCommunityData.h", service)
        area = service[service.index("void MaritimeService::search_vessels_by_area"):]
        history = service[service.index("void MaritimeService::get_vessel_history"):]
        self.assertLess(area.index("community_vessels_for_area"), area.index("post_large"))
        self.assertLess(history.index("community_vessel_history"), history.index("HttpClient::instance().post"))
        self.assertIn("set_cost_credits(metered ? 8 : 0", layout)
        self.assertIn("LOCAL VESSELS", layout)
        self.assertIn("0 Fincept credits", handlers)
        self.assertIn("LOAD LOCAL", handlers)

    def test_maritime_community_results_never_restore_paid_refresh_wording(self):
        handlers = (ROOT / "src/screens/maritime/MaritimeScreen_Handlers.cpp").read_text(encoding="utf-8")
        result = handlers[handlers.index("void MaritimeScreen::apply_vessels_page") :]
        self.assertIn("constexpr bool metered = policy::fincept_metered_services_enabled()", result[:1600])
        paid_cache = result.index('set_status(tr("AS OF %1 · ⟳ REFRESH = 8 CR")')
        guard = result.rfind("if constexpr (!metered)", 0, paid_cache)
        self.assertGreater(guard, paid_cache - 800)
        self.assertIn("LOCAL SAMPLE · 0 FINCEPT CREDITS", result[:12000])

    def test_agent_knowledge_embedder_never_offers_ollama_and_migrates_saved_rows(self):
        layout = (ROOT / "src/screens/agent_config/CreateAgentPanel_Layout.cpp").read_text(encoding="utf-8")
        form = layout[layout.index('knowledge_embedder_combo_ = new QComboBox') :]
        self.assertNotIn('addItem(QStringLiteral("ollama"))', form[:900])
        self.assertIn("sentence-transformers", form[:900])

        data_ops = (ROOT / "src/screens/agent_config/CreateAgentPanel_DataOps.cpp").read_text(encoding="utf-8")
        load = data_ops[data_ops.index('QJsonObject kc = c["knowledge_config"].toObject()') :]
        self.assertIn('QLatin1String("ollama")', load[:1300])
        self.assertIn('QStringLiteral("sentence-transformers")', load[:1300])

    def test_bundled_agents_share_the_deepseek_first_provider_policy(self):
        files = {
            "scripts/agents/finagent_core/core_agent.py": "resolve_byo_model_config",
            "scripts/agents/finagent_core/core_agent_stream.py": "resolve_byo_provider",
            "scripts/agents/finagent_core/super_agent.py": "resolve_byo_model_config",
            "scripts/agents/finagent_core/modules/workflow_module.py": "resolve_byo_provider",
        }
        for relative, helper in files.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("finagent_core.provider_policy", source, relative)
            self.assertIn(helper, source, relative)
            self.assertNotIn('["fincept", "ollama"', source, relative)
        core = (ROOT / "scripts/agents/finagent_core/core_agent.py").read_text(encoding="utf-8")
        create_model = core[core.index("def _create_model"):core.index("def _create_agent")]
        create_agent = core[core.index("def _create_agent"):]
        self.assertIn("normalize_byo_model_config", create_model)
        self.assertIn("normalize_byo_model_config", create_agent[:2500])

    def test_agent_subprocess_never_receives_the_fincept_session_key_in_community_mode(self):
        source = (ROOT / "src/services/agents/AgentService.cpp").read_text(encoding="utf-8")
        start = source.index("QJsonObject AgentService::build_api_keys() const")
        block = source[start : source.index("// ── Payload builder", start)]

        self.assertIn("fincept_metered_services_enabled", block)
        self.assertIn("deprecated_ai_backends_enabled", block)
        session_key = block.index('keys["FINCEPT_API_KEY"]')
        policy_gate = block.index("fincept_metered_services_enabled")
        self.assertLess(policy_gate, session_key)

    def test_agent_subprocess_filters_credentials_for_every_retired_provider(self):
        source = (ROOT / "src/services/agents/AgentService.cpp").read_text(encoding="utf-8")
        start = source.index("QJsonObject AgentService::build_api_keys() const")
        block = source[start : source.index("// ── Payload builder", start)]
        loop = block[block.index("for (const auto& p : providers.value())") : block.index("// The login credential")]

        self.assertIn("is_deprecated_llm_provider", loop)
        self.assertLess(loop.index("is_deprecated_llm_provider"), loop.index("keys[lower]"))

    def test_llm_service_empty_route_uses_byo_deepseek_not_fincept(self):
        source = (ROOT / "src/services/llm/LlmService.cpp").read_text(encoding="utf-8")
        route = source[source.index("QVector<ModelChoice> LlmService::route_chain") :
                       source.index("LlmService::ConfigSnapshot LlmService::snapshot_for")]

        self.assertNotIn("fincept_fallback", route)
        self.assertNotIn("ModelChoice::Kind::Fincept", route)
        self.assertIn("policy::default_llm_provider()", route)
        self.assertIn("policy::default_llm_model()", route)

    def test_alpha_arena_blocks_every_retired_provider_before_harness_or_http_execution(self):
        client = (ROOT / "src/services/alpha_arena/ArenaLlmClient.cpp").read_text(encoding="utf-8")
        complete = client[client.index("void ArenaLlmClient::complete") :]
        self.assertIn("is_deprecated_llm_provider", complete)
        self.assertLess(complete.index("is_deprecated_llm_provider"),
                        complete.index("ProviderCatalog::is_harness"))

        registry = (ROOT / "src/services/alpha_arena/ArenaModelRegistry.cpp").read_text(encoding="utf-8")
        available = registry[registry.index("QVector<ArenaModelOption> ArenaModelRegistry::available_models") :]
        self.assertIn("is_ai_backend_selectable", available)
        self.assertLess(available.index("is_ai_backend_selectable"),
                        available.index("out.append"))

        credentials = registry[registry.index("Result<ArenaCredentials> ArenaModelRegistry::resolve_credentials") :]
        self.assertIn("is_deprecated_llm_provider", credentials)
        self.assertIn("fincept_metered_services_enabled", credentials)
        self.assertLess(credentials.index("is_deprecated_llm_provider"),
                        credentials.index("ProviderCatalog::is_harness"))
        self.assertLess(credentials.index("fincept_metered_services_enabled"),
                        credentials.index("fincept_api_key"))

    def test_alpha_arena_credential_reads_fail_closed_with_storage_error(self):
        registry = (ROOT / "src/services/alpha_arena/ArenaModelRegistry.cpp").read_text(encoding="utf-8")
        credentials = registry[registry.index("Result<ArenaCredentials> ArenaModelRegistry::resolve_credentials") :]

        self.assertGreaterEqual(credentials.count("if (rows.is_err())"), 3)
        self.assertIn("Could not read LLM profiles", credentials)
        self.assertIn("Could not read LLM model configurations", credentials)
        self.assertIn("Could not read LLM providers", credentials)
        self.assertLess(credentials.index("if (rows.is_err())"),
                        credentials.index("no API key configured"))

    def test_historical_arena_rows_hide_retired_provider_names(self):
        store = (ROOT / "src/services/alpha_arena/ArenaStore.cpp").read_text(encoding="utf-8")
        parser = store[store.index("AgentRow map_agent") :
                       store.index("constexpr const char* kAgentCols")]
        self.assertIn("is_deprecated_llm_provider", parser)
        self.assertIn("Legacy AI (disabled)", parser)
        self.assertLess(parser.index("is_deprecated_llm_provider"), parser.index("return a"))

    def test_historical_harness_runs_use_a_generic_retired_label(self):
        registry = (ROOT / "src/services/harness/HarnessRegistry.cpp").read_text(encoding="utf-8")
        public_name = registry[registry.index("QString HarnessRegistry::display_name") :
                               registry.index("QStringList HarnessRegistry::installed_ids")]
        self.assertIn("is_deprecated_llm_provider", public_name)
        self.assertIn("Legacy AI (disabled)", public_name)
        self.assertLess(public_name.index("is_deprecated_llm_provider"),
                        public_name.index("d_->entries.constFind"))

    def test_agent_system_view_counts_and_shows_only_byo_provider_rows(self):
        source = (ROOT / "src/screens/agent_config/SystemViewPanel.cpp").read_text(encoding="utf-8")
        populate = source[source.index("void SystemViewPanel::populate_llm_list") :
                          source.index("// ── Tools list population")]
        self.assertIn("is_deprecated_llm_provider", populate)
        self.assertIn("fincept_metered_services_enabled", populate)
        self.assertLess(populate.index("is_deprecated_llm_provider"),
                        populate.index("llms_count_->setText"))

    def test_shared_provider_labels_and_agent_rail_hide_retired_backend_names(self):
        catalog = (ROOT / "src/services/llm/ProviderCatalog.cpp").read_text(encoding="utf-8")
        display = catalog[catalog.index("QString ProviderCatalog::display_name") :
                          catalog.index("QString ProviderCatalog::default_base_url")]
        self.assertIn("is_deprecated_llm_provider", display)
        self.assertIn("Legacy AI (disabled)", display)
        self.assertLess(display.index("is_deprecated_llm_provider"),
                        display.index("kHarnessNames"))

        rail = (ROOT / "src/screens/agent_config/AgentRail.cpp").read_text(encoding="utf-8")
        agents = rail[rail.index("// ── AGENTS (by category)") :
                      rail.index("// ── TEAMS")]
        self.assertIn("ProviderCatalog::display_name(a.provider)", agents)
        self.assertNotIn("a.provider.toUpper()", agents)

        actions = (ROOT / "src/screens/agent_config/AgentConfigScreen_Actions.cpp").read_text(encoding="utf-8")
        selected_agent = actions[actions.index("section_ == QLatin1String(sec::kAgents)") :
                                 actions.index("status_bar_->set_text", actions.index("section_ == QLatin1String(sec::kAgents)"))]
        self.assertIn("ProviderCatalog::display_name(a.provider)", selected_agent)
        self.assertNotIn("m.isEmpty() ? a.provider", selected_agent)

    def test_user_facing_help_no_longer_advertises_deprecated_ai_backends(self):
        visible_files = [
            "src/screens/docs/DocsScreen_Pages_Intro.cpp",
            "src/screens/docs/DocsScreen_Pages_Trading.cpp",
            "src/screens/docs/DocsScreen.cpp",
            "src/screens/settings/SettingsScreen.cpp",
        ]
        for relative in visible_files:
            source = (ROOT / relative).read_text(encoding="utf-8").lower()
            self.assertNotIn("codex", source, relative)
            self.assertNotIn("ollama", source, relative)
        knowledge = (ROOT / "scripts/agents/finagent_core/modules/knowledge_module.py").read_text(encoding="utf-8")
        self.assertIn("normalize_embedder_config", knowledge)
        self.assertNotIn("choose the 'ollama' embedder", knowledge)

    def test_derivatives_quantlib_cross_check_is_local_and_zero_credit(self):
        source = (ROOT / "src/screens/derivatives/DerivPricerCompute.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', source)
        result_ui = source[source.index("Optional cross-check in QuantLib"):]
        run = source[source.index("void DerivPricerPage::run_quantlib_cross_check"):]
        self.assertIn("CROSS-CHECK LOCALLY", result_ui[:3500])
        self.assertIn("0 Fincept credits", result_ui[:3500])
        self.assertLess(result_ui.index("if (metered)"),
                        result_ui.index('setProperty("fincept_agent_risk", "spend")'))
        self.assertIn("Running local QuantLib", run[:1200])
        self.assertIn("Local QuantLib cross-check failed", run[:2200])

    def test_economics_hides_the_blocked_fincept_macro_source(self):
        source = (ROOT / "src/screens/economics/EconomicsScreen.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', source)
        self.assertIn("bool economics_source_enabled", source)
        self.assertIn("int visible_economics_source_count", source)
        build = source[source.index("void EconomicsScreen::build_ui") :]
        self.assertLess(build.index("economics_source_enabled(src.id)"), build.index("SourceEntry entry"))
        self.assertIn("visible_economics_source_count()", build[:2500])
        commands = source[source.index("QList<command::CommandItem> econ_command_rows") :]
        self.assertLess(commands.index("economics_source_enabled(r.id)"), commands.index("add(r)"))
        self.assertIn("policy::fincept_metered_services_enabled()", source)

    def test_dashboard_public_geo_and_local_maritime_tiles_are_not_billed(self):
        geo = (ROOT / "src/screens/dashboard/widgets/GeopoliticsEventsWidget.cpp").read_text(encoding="utf-8")
        maritime = (ROOT / "src/screens/dashboard/widgets/MaritimeVesselsWidget.cpp").read_text(encoding="utf-8")
        for source in (geo, maritime):
            self.assertIn('core/policy/CommunityPolicy.h', source)
            self.assertIn("policy::fincept_metered_services_enabled()", source)
            self.assertIn("set_agent_risk(metered ?", source)
            show_at = source.index("showEvent")
            show = source[source.rfind("void ", 0, show_at) :]
            self.assertIn("!policy::fincept_metered_services_enabled()", show[:1800])
        self.assertIn("PUBLIC EVENTS · 0 Fincept credits", geo)
        self.assertIn("LOCAL POSITIONS · 0 Fincept credits", maritime)

    def test_maritime_default_places_do_not_advertise_credit_spend_in_community_mode(self):
        source = (ROOT / "src/screens/maritime/MaritimeScreen_Layout.cpp").read_text(encoding="utf-8")
        places = source[source.index("void MaritimeScreen::fill_default_places") :
                        source.index("void MaritimeScreen::fill_default_ports")]
        self.assertIn("fincept_metered_services_enabled", places)
        self.assertIn("local vessel sample", places.lower())
        self.assertIn("0 Fincept credits", places)
        self.assertLess(places.index("fincept_metered_services_enabled"),
                        places.index("name->setToolTip"))

    def test_maritime_local_voyage_history_is_marked_zero_credit_before_running(self):
        source = (ROOT / "src/screens/maritime/MaritimeScreen_Layout.cpp").read_text(encoding="utf-8")
        history = source[source.index("history_btn_ = new QPushButton") :
                         source.index("vl->addWidget(history_btn_)")]
        self.assertIn("fincept_metered_services_enabled", history)
        self.assertIn("set_cost_credits(metered ? 5 : 0", history)
        self.assertLess(history.index("set_cost_credits"), history.index("vessels_pane_->run()"))

    def test_dashboard_calendar_uses_public_feed_in_community_mode(self):
        service = (ROOT / "src/services/economics/MacroCalendarService.cpp").read_text(encoding="utf-8")
        widget = (ROOT / "src/screens/dashboard/widgets/EconomicCalendarWidget.cpp").read_text(encoding="utf-8")
        refresh = service[service.index("void MacroCalendarService::refresh") :]
        self.assertIn("MacroCalendarCommunityData.h", service)
        self.assertIn("ff_calendar_thisweek.json", service)
        self.assertLess(refresh.index("normalize_public_macro_calendar"), refresh.index("kMacroCalPath"))
        self.assertIn('core/policy/CommunityPolicy.h', widget)
        self.assertIn("set_agent_risk(metered ?", widget)
        show_at = widget.index("showEvent")
        show = widget[widget.rfind("void ", 0, show_at) :]
        self.assertIn("!policy::fincept_metered_services_enabled()", show[:1800])
        self.assertIn("PUBLIC CALENDAR · 0 Fincept credits", widget)

    def test_calendars_route_exposes_every_public_zero_credit_tab(self):
        setup = (ROOT / "src/app/WindowFrame_Setup.cpp").read_text(encoding="utf-8")
        screen = (ROOT / "src/screens/calendars/CalendarsScreen.cpp").read_text(encoding="utf-8")
        panel = (ROOT / "src/screens/calendars/CalendarPanel.cpp").read_text(encoding="utf-8")
        service = (ROOT / "src/services/fincept_data/FinceptDataService.cpp").read_text(encoding="utf-8")
        toolbar = (ROOT / "src/ui/navigation/ToolBar.cpp").read_text(encoding="utf-8")
        factory = setup[setup.index('register_factory("calendars"') :]
        self.assertIn("new screens::CalendarsScreen", factory[:300])
        self.assertNotIn('open_section(QStringLiteral("econ_calendar")', factory[:900])
        build = screen[screen.index("for (const auto& spec : kSubTabs)"):
                       screen.index("connect(tabs_", screen.index("for (const auto& spec : kSubTabs)"))]
        self.assertNotIn("continue;", build)
        commands = screen[screen.index("QList<command::CommandItem> cal_command_rows") :]
        self.assertIn("fincept_metered_services_enabled", commands[:1800])
        for section in ("earnings", "ipos", "splits", "economic"):
            self.assertIn(f'QStringLiteral("{section}")', commands[:3500])
        self.assertIn('nav(res, tr("Calendars"), "calendars")', toolbar)
        self.assertIn("!policy::fincept_metered_services_enabled()", panel)
        requirements = panel[panel.index("pane_->set_requirements"):
                             panel.index("pane_->set_start")]
        self.assertIn("fincept_metered_services_enabled", requirements)
        self.assertIn("ui::Requirement::signed_in()", requirements)
        fetch = service[service.index("void FinceptDataService::fetch_calendar") :]
        self.assertIn("calendar_name(kind)", fetch[:2600])
        adapter = (ROOT / "scripts/byo_equity_data.py").read_text(encoding="utf-8")
        for operation in ("earnings", "ipos", "splits", "economic"):
            self.assertIn(f'operation == "{operation}"', adapter)

    def test_equity_research_exposes_public_data_tabs_in_community_mode(self):
        source = (ROOT / "src/screens/equity_research/EquityResearchScreen.cpp").read_text(encoding="utf-8")
        self.assertIn("bool research_tab_enabled", source)
        policy = source[source.index("static bool research_tab_enabled"):
                        source.index("static const char* const kLegacyTabOrder")]
        self.assertIn("return true", policy)
        self.assertNotIn('id != QLatin1String("options")', policy)
        tabs = source[source.index("for (size_t i = 0; i < std::size(tabs)") :]
        self.assertLess(tabs.index("research_tab_enabled"), tabs.index("addTab"))
        retranslate = source[source.index("void EquityResearchScreen::retranslateUi") :]
        self.assertIn("kResearchTabKeyProp", retranslate[:6000])
        commands = source[source.index("QList<command::CommandItem> er_command_rows") :]
        self.assertIn("research_tab_enabled", commands[:4500])

        service = (ROOT / "src/services/fincept_data/FinceptDataService.cpp").read_text(encoding="utf-8")
        self.assertIn("fetch_community", service)
        self.assertIn('QStringLiteral("byo_equity_data.py")', service)
        self.assertIn("credential_allowlist = QStringList{}", service)
        for operation in ("options", "analyst", "ownership"):
            self.assertIn(f'QStringLiteral("{operation}")', service)

    def test_generic_fincept_data_cannot_start_hosted_discovery_in_community_mode(self):
        source = (ROOT / "src/services/fincept_data/FinceptDataService.cpp").read_text(encoding="utf-8")
        discovery = source[source.index("void FinceptDataService::start_discovery()") :
                           source.index("void FinceptDataService::on_discovery")]
        self.assertLess(discovery.index("fincept_metered_services_enabled"),
                        discovery.index("HttpClient::instance().get"))

        dispatch = source[source.index("void FinceptDataService::dispatch(Request r)") :
                          source.index("void FinceptDataService::send")]
        self.assertLess(dispatch.index("fincept_metered_services_enabled"), dispatch.index("signed_in"))
        self.assertLess(dispatch.index("fincept_metered_services_enabled"), dispatch.index("start_discovery"))

    def test_dashboard_catalog_exposes_public_data_tiles_in_community_mode(self):
        registry = (ROOT / "src/screens/dashboard/canvas/WidgetRegistry.cpp").read_text(encoding="utf-8")
        tiles = (ROOT / "src/screens/dashboard/widgets/DataApiTiles.cpp").read_text(encoding="utf-8")
        for type_id in ("yield_curve", "crypto_overview", "earnings_calendar"):
            entry_at = registry.index(f'register_widget({{"{type_id}"')
            entry = registry[max(0, entry_at - 100) : entry_at + 140]
            self.assertNotIn("fincept_metered_services_enabled", entry)
        self.assertIn('core/policy/CommunityPolicy.h', tiles)
        ctor = tiles[tiles.index("DataApiTile::DataApiTile") :]
        self.assertIn("set_agent_risk(metered ?", ctor[:2800])
        fetch = tiles[tiles.index("void DataApiTile::fetch_data") :]
        self.assertIn("!policy::fincept_metered_services_enabled()", fetch[:1400])
        self.assertIn("byo_dashboard_data.py", tiles)
        self.assertIn("credential_allowlist = QStringList{}", tiles)

    def test_dashboard_refresh_has_no_fincept_spend_marker_in_community_mode(self):
        source = (ROOT / "src/screens/dashboard/DashboardToolBar.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', source)
        refresh = source[source.index('refresh_btn_ = new QPushButton') :]
        self.assertIn("fincept_metered_services_enabled", refresh[:650])
        self.assertIn('QVariant(QStringLiteral("spend"))', refresh[:650])

    def test_community_chat_status_bar_hides_hosted_credit_and_bridge_chrome(self):
        source = (ROOT / "src/screens/chat_mode/RunStatusBar.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', source)

        build = source[source.index("void RunStatusBar::Impl::build()") :
                       source.index("void RunStatusBar::Impl::wire()")]
        self.assertIn("fincept_cloud_agents_enabled", build)
        for item in ("kRsbCredits", "kRsbBalance", "kRsbTools", "kRsbMcp"):
            self.assertIn(f"set_item_visible(rsb_id({item}), hosted)", build)

        balance = source[source.index("void RunStatusBar::Impl::refresh_balance") :
                         source.index("void RunStatusBar::Impl::refresh_tools")]
        self.assertIn("!policy::fincept_cloud_agents_enabled()", balance)
        self.assertIn("set_item_visible(rsb_id(kRsbBalance), false)", balance)

        no_run = source[source.index("if (!r) {") : source.index("} else {", source.index("if (!r) {"))]
        self.assertIn("fincept_cloud_agents_enabled", no_run)
        self.assertIn('RunStatusBar::tr("LOCAL")', no_run)

    def test_community_chat_task_budget_has_no_fincept_credit_controls(self):
        source = (ROOT / "src/screens/chat_mode/ComposerContextBar.cpp").read_text(encoding="utf-8")
        translate = source[source.index("void ComposerContextBar::Impl::retranslate()") :
                           source.index("void ComposerContextBar::Impl::show_note")]
        self.assertIn("max_credits->parentWidget()->setVisible(policy::fincept_cloud_agents_enabled())", translate)
        self.assertIn("policy::fincept_cloud_agents_enabled()", translate)
        self.assertIn("steps, no time limit", translate)

        composer = (ROOT / "src/screens/chat_mode/ChatComposer.cpp").read_text(encoding="utf-8")
        perm = composer[composer.index("void ChatComposer::Impl::update_perm()") :
                        composer.index("void ChatComposer::Impl::update_meter()")]
        self.assertIn("your BYO model", perm)
        self.assertNotIn("no Fincept credits", perm)

    def test_community_chat_cannot_render_legacy_credit_upsells(self):
        source = (ROOT / "src/screens/chat_mode/ChatMessagePanel.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', source)

        card = source[source.index("void ChatMessagePanel::add_credits_card()") :
                      source.index("void ChatMessagePanel::add_question")]
        self.assertIn("commercial_promotions_enabled", card)
        self.assertLess(card.index("commercial_promotions_enabled"), card.index("CreditsCta"))

        failure = source[source.index("void ChatMessagePanel::on_insufficient_credits") :]
        self.assertIn("commercial_promotions_enabled", failure)
        self.assertIn("community credit firewall", failure)

    def test_direct_commercial_entry_points_refuse_community_mode(self):
        frame = (ROOT / "src/app/WindowFrame_Auth.cpp").read_text(encoding="utf-8")
        show = frame[frame.index("void WindowFrame::show_pricing()") :
                     frame.index("void WindowFrame::leave_pricing()")]
        self.assertIn("commercial_promotions_enabled", show)
        self.assertLess(show.index("commercial_promotions_enabled"), show.index("enter_auth_stack(3)"))

        promo = (ROOT / "src/ui/widgets/EnterprisePromo.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', promo)
        plans = promo[promo.index("void open_plans") : promo.index("} // namespace billing")]
        self.assertIn("commercial_promotions_enabled", plans)
        show_now = promo[promo.index("void UpgradeDialog::show_now") :
                         promo.index("void UpgradeDialog::maybe_show")]
        self.assertIn("commercial_promotions_enabled", show_now)
        self.assertLess(show_now.index("commercial_promotions_enabled"), show_now.index("new UpgradeDialog"))

    def test_plan_access_never_fetches_or_emits_upgrade_copy_in_community_mode(self):
        source = (ROOT / "src/services/billing/PlanAccess.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', source)

        note = source[source.index("QString PlanAccess::upgrade_note") :
                      source.index("QString PlanAccess::with_upgrade_note")]
        self.assertIn("commercial_promotions_enabled", note)
        self.assertLess(note.index("commercial_promotions_enabled"), note.index("price(category)"))

        auth = source[source.index("void PlanAccess::on_auth_changed") :
                      source.index("void PlanAccess::clear")]
        self.assertIn("commercial_promotions_enabled", auth)
        self.assertLess(auth.index("commercial_promotions_enabled"), auth.index("account_key()"))

        refresh = source[source.index("void PlanAccess::refresh") :]
        self.assertIn("commercial_promotions_enabled", refresh)
        self.assertLess(refresh.index("commercial_promotions_enabled"), refresh.index("/user/entitlements"))

    def test_shared_model_badge_never_lists_retired_saved_backends(self):
        source = (ROOT / "src/ui/widgets/ModelBadge.cpp").read_text(encoding="utf-8")

        harnesses = source[source.index("for (const mb_ai::RouterHarness& h") :
                           source.index("if (!any_harness)")]
        self.assertIn("is_deprecated_llm_provider(h.id)", harnesses)

        profiles = source[source.index("QVector<mb_ai::RouterProfile> profiles") :
                          source.index("std::sort(profiles")]
        self.assertIn("is_deprecated_llm_provider(p.provider)", profiles)

        active = source[source.index("const mb_ai::RouterActiveRow& row") :
                        source.index("if (policy::commercial_promotions_enabled()")]
        self.assertIn("!policy::is_deprecated_llm_provider(row.provider)", active)

    def test_report_drafting_blocks_legacy_fincept_credit_routes_at_the_view_boundary(self):
        source = (ROOT / "src/services/report_builder/ReportAiLlmBackend.cpp").read_text(encoding="utf-8")
        self.assertIn('core/policy/CommunityPolicy.h', source)
        model_info = source[source.index("AiModelInfo ReportAiLlmBackend::model_info() const") :
                            source.index("void ReportAiLlmBackend::deliver_chunk")]
        self.assertIn("fincept_metered_services_enabled", model_info)
        self.assertIn("No AI configured", model_info)
        self.assertLess(model_info.index("fincept_metered_services_enabled"),
                        model_info.index('QStringLiteral("Fincept LLM")'))

    def test_shared_model_badge_blocks_legacy_fincept_credit_routes_at_render_time(self):
        source = (ROOT / "src/ui/widgets/ModelBadge.cpp").read_text(encoding="utf-8")
        refresh = source[source.index("void ModelBadge::refresh()") :
                         source.index("QString ModelBadge::elided_text")]
        self.assertIn("blocked_fincept", refresh)
        self.assertIn("fincept_metered_services_enabled", refresh)
        self.assertIn('tr("No AI configured")', refresh)
        self.assertIn("c.is_fincept() && policy::fincept_metered_services_enabled()", refresh)

    def test_alpha_arena_readiness_uses_only_the_filtered_byo_registry(self):
        source = (ROOT / "src/screens/alpha_arena/AlphaArenaScreen.cpp").read_text(encoding="utf-8")
        refresh = source[source.index("void AlphaArenaScreen::refresh_start_page()") :]
        self.assertIn("ready_byo", refresh[:1800])
        self.assertIn("DeepSeek API key", refresh[:1800])
        self.assertNotIn('QLatin1String("ollama")', refresh[:1800])
        self.assertNotIn('QLatin1String("fincept")', refresh[:1800])
        self.assertNotIn("Fincept credits", refresh[:1800])

    def test_bundled_docs_describe_the_community_byo_product_not_hidden_billing_ui(self):
        intro = (ROOT / "src/screens/docs/DocsScreen_Pages_Intro.cpp").read_text(encoding="utf-8")
        self.assertIn("COMMUNITY & BYO COSTS", intro)
        self.assertIn("DeepSeek API key", intro)
        self.assertIn("community credit firewall", intro)
        self.assertNotIn("plan badge in the toolbar", intro)
        self.assertNotIn("Profile → Billing", intro)

        data = (ROOT / "src/screens/docs/DocsScreen_Pages_Data.cpp").read_text(encoding="utf-8")
        profile = data[data.index("QWidget* DocsScreen::page_profile()") :]
        self.assertIn("device-local profile", profile)
        self.assertIn("BYO provider credentials", profile)
        self.assertNotIn("UPGRADE TO PRO", profile)
        self.assertNotIn("Move from Free to Pro", profile)
        self.assertNotIn("payment history", profile)

    def test_live_model_router_snapshot_excludes_retired_and_metered_backends(self):
        source = (ROOT / "src/services/llm/ModelRouter_Live.cpp").read_text(encoding="utf-8")
        self.assertIn("mrl_provider_allowed", source)

        gather = source[source.index("void mrl_gather_db") :
                        source.index("qint64 mrl_gather_harnesses")]
        profiles = gather[gather.index("list_all_profiles") : gather.index("list_assignments")]
        self.assertIn("mrl_provider_allowed(p.provider)", profiles)

        active = gather[gather.index("list_providers") : gather.index("auto& cfg")]
        self.assertIn("mrl_provider_allowed(c.provider)", active)
        self.assertIn("const LlmConfig* pick = nullptr", active)

        harnesses = source[source.index("for (const QString& id : reg.ids())") :
                           source.index("s.default_harness = reg.default_harness()")]
        self.assertIn("is_deprecated_llm_provider(id)", harnesses)
        self.assertIn("is_deprecated_llm_provider(s.default_harness)", source)

    def test_local_pro_reports_offer_only_explicit_byo_ai_explanations(self):
        source = (ROOT / "src/ui/widgets/ProAnalytics.cpp").read_text(encoding="utf-8")
        self.assertIn("EXPLAIN WITH BYO AI", source)
        self.assertIn("kFeatureProAnalyticsExplain", source)
        self.assertIn("pro_ai::explanation_prompt", source)
        self.assertIn('"fincept_cost_route", QStringLiteral("byo_api")', source)
        self.assertIn("0 Fincept credits", source)
        self.assertIn("QtConcurrent::run", source)
        self.assertNotIn("fincept_credentials", source)
        self.assertIn("ProAnalyticsAiCache.h", source)
        self.assertIn("explanation_cache_key", source)
        self.assertIn("load_explanation_cache", source)
        self.assertIn("store_explanation_cache", source)
        self.assertIn("AppPaths::cache()", source)
        self.assertIn("Saved explanation reused", source)


if __name__ == "__main__":
    unittest.main()
