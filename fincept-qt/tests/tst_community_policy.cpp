#include "core/policy/CommunityPolicy.h"
#include "core/metrics/LocalUsageMetrics.h"
#include "services/ai_quant_lab/AIQuantCachePolicy.h"
#include "services/ai_quant_lab/AIQuantUsage.h"
#include "services/llm/LlmEndpointPolicy.h"
#include "services/llm/ModelRouter.h"

#include <QFile>
#include <QtTest>
#include <QTemporaryDir>

#ifndef FINCEPT_SOURCE_DIR
#    error "FINCEPT_SOURCE_DIR (fincept-qt/) must be defined by tests/CMakeLists.txt"
#endif

class CommunityPolicyTest : public QObject {
    Q_OBJECT

  private slots:
    void commercial_prompts_are_opt_in() {
        QCOMPARE(fincept::policy::automatic_commercial_prompts_enabled(), false);
        QCOMPARE(fincept::policy::fincept_metered_services_enabled(), false);
    }

    void deprecated_ai_backends_are_disabled() {
        QCOMPARE(fincept::policy::deprecated_ai_backends_enabled(), false);
        QVERIFY(fincept::policy::is_deprecated_llm_provider(QStringLiteral("ollama")));
        QVERIFY(fincept::policy::is_deprecated_llm_provider(QStringLiteral("harness:codex")));
        QVERIFY(fincept::policy::is_deprecated_llm_provider(QStringLiteral("CODEX")));
        QVERIFY(!fincept::policy::is_deprecated_llm_provider(QStringLiteral("deepseek")));
    }

    void retired_ai_backends_are_never_selectable() {
        QVERIFY(!fincept::policy::is_ai_backend_selectable(QStringLiteral("ollama")));
        QVERIFY(!fincept::policy::is_ai_backend_selectable(QStringLiteral("codex")));
        QVERIFY(!fincept::policy::is_ai_backend_selectable(QStringLiteral("harness:codex")));
        QVERIFY(!fincept::policy::is_ai_backend_selectable(QStringLiteral(" HARNESS:CODEX ")));
        QVERIFY(!fincept::policy::is_ai_backend_selectable(QStringLiteral("fincept")));
        QVERIFY(!fincept::policy::is_ai_backend_selectable(QStringLiteral(" FINCEPT ")));
        QVERIFY(fincept::policy::is_ai_backend_selectable(QStringLiteral("deepseek")));
        QVERIFY(fincept::policy::is_ai_backend_selectable(QStringLiteral("harness:claude")));
    }

    void shipped_component_catalog_uses_provider_neutral_copy() {
        QFile catalog(QStringLiteral(FINCEPT_SOURCE_DIR "/resources/component_catalog.json"));
        QVERIFY2(catalog.open(QIODevice::ReadOnly), qPrintable(catalog.errorString()));
        const QByteArray copy = catalog.readAll().toLower();
        QVERIFY(!copy.contains("ollama"));
        QVERIFY(!copy.contains("codex"));
    }

    void shipped_marketing_metadata_omits_retired_backends() {
        const QStringList paths = {
            QStringLiteral(FINCEPT_SOURCE_DIR "/../README.md"),
            QStringLiteral(FINCEPT_SOURCE_DIR "/scripts/ai_quant_lab/README.md"),
            QStringLiteral(FINCEPT_SOURCE_DIR "/scripts/agents/finagent_core/README.md"),
            QStringLiteral(FINCEPT_SOURCE_DIR "/packaging/linux/fincept-terminal.appdata.xml"),
            QStringLiteral(FINCEPT_SOURCE_DIR "/packaging/flatpak/in.fincept.FinceptTerminal.metainfo.xml"),
        };
        for (const QString& path : paths) {
            QFile file(path);
            QVERIFY2(file.open(QIODevice::ReadOnly), qPrintable(path + QStringLiteral(": ") + file.errorString()));
            const QByteArray copy = file.readAll().toLower();
            QVERIFY2(!copy.contains("ollama"), qPrintable(path));
            QVERIFY2(!copy.contains("codex"), qPrintable(path));
        }
    }

    void llm_retry_logs_redact_provider_errors() {
        QFile source(QStringLiteral(FINCEPT_SOURCE_DIR "/src/services/llm/LlmService.cpp"));
        QVERIFY2(source.open(QIODevice::ReadOnly), qPrintable(source.errorString()));
        const QByteArray code = source.readAll();
        QVERIFY2(!code.contains("{\"error\", resp.error}"),
                 "Provider errors can include credentials, URLs, and response bodies; redact them before logging");
        QVERIFY(code.contains("{\"error\", privacy::redact_text(resp.error)}"));
    }

    void text_tool_fallback_does_not_persist_raw_provider_errors() {
        QFile source(QStringLiteral(FINCEPT_SOURCE_DIR "/src/services/llm/LlmToolLoop.cpp"));
        QVERIFY2(source.open(QIODevice::ReadOnly), qPrintable(source.errorString()));
        const QByteArray code = source.readAll();
        QVERIFY2(!code.contains("could not summarise — \" + reason"),
                 "Provider errors can contain secrets and must not be copied raw into chat history");
        QVERIFY(code.contains("could not summarise — \" + safe_reason"));
    }

    void native_tool_rounds_never_bypass_the_aggregate_result_budget() {
        QFile source(QStringLiteral(FINCEPT_SOURCE_DIR "/src/services/llm/LlmService.cpp"));
        QVERIFY2(source.open(QIODevice::ReadOnly), qPrintable(source.errorString()));
        const QByteArray code = source.readAll();
        QVERIFY2(!code.contains("detail::encode_tool_result_for_llm("),
                 "Provider-facing tool rounds must shape the whole result batch; per-result encoding can multiply "
                 "the transcript and re-bill oversized evidence on every follow-up");
        QVERIFY(code.count("detail::encode_tool_results_for_llm(") >= 3);
    }

    void text_tool_rounds_use_the_same_aggregate_result_budget() {
        QFile source(QStringLiteral(FINCEPT_SOURCE_DIR "/src/services/llm/LlmToolLoop.cpp"));
        QVERIFY2(source.open(QIODevice::ReadOnly), qPrintable(source.errorString()));
        const QByteArray code = source.readAll();
        QVERIFY2(!code.contains("mcp::shrink_json(payload, 4000)"),
                 "Text/XML tool calls must not apply an independent per-result cap that multiplies across a round");
        QVERIFY(code.count("detail::encode_tool_results_for_llm(") >= 2);
    }

    void ai_quant_agents_obey_the_global_turn_output_ceiling() {
        QFile source(QStringLiteral(FINCEPT_SOURCE_DIR "/src/services/ai_quant_lab/AIQuantLabService.cpp"));
        QVERIFY2(source.open(QIODevice::ReadOnly), qPrintable(source.errorString()));
        const QByteArray code = source.readAll();
        QVERIFY2(code.contains("kLlmCompletionTokensKey"),
                 "AI Quant Lab launches recurring paid model calls and must read the global turn-output budget");
        QVERIFY2(code.contains("resolve_llm_turn_output_tokens("),
                 "Deep Agent and RD-Agent must not bypass the same per-turn output ceiling as AI Chat");
    }

    void python_agents_receive_the_global_turn_input_ceiling() {
        QFile source(QStringLiteral(FINCEPT_SOURCE_DIR "/src/services/agents/AgentService.cpp"));
        QVERIFY2(source.open(QIODevice::ReadOnly), qPrintable(source.errorString()));
        const QByteArray code = source.readAll();
        QVERIFY(code.contains("kLlmPromptTokensKey"));
        QVERIFY(code.contains("payload[\"input_max_tokens\"]"));
        QVERIFY(code.contains("kLlmCompletionTokensKey"));
        QVERIFY(code.contains("payload[\"output_max_tokens\"]"));
    }

    void harness_chat_obeys_the_global_turn_input_ceiling() {
        QFile source(QStringLiteral(FINCEPT_SOURCE_DIR "/src/services/llm/LlmHarnessBridge.cpp"));
        QVERIFY2(source.open(QIODevice::ReadOnly), qPrintable(source.errorString()));
        const QByteArray code = source.readAll();
        QVERIFY2(code.contains("kLlmPromptTokensKey"),
                 "Harness-backed chat must read the same complete-input budget as direct API providers");
        QVERIFY2(code.contains("llm_request_fits_prompt_budget("),
                 "Harness-backed chat must reject an oversized request before launching an external model process");
    }

    void fincept_cloud_sync_is_disabled() {
        QCOMPARE(fincept::policy::fincept_cloud_sync_enabled(), false);
    }

    void fincept_cloud_agents_are_disabled() {
        QCOMPARE(fincept::policy::fincept_cloud_agents_enabled(), false);
    }

    void fincept_chat_api_is_disabled() {
        QCOMPARE(fincept::policy::fincept_chat_api_enabled(), false);
    }

    void llm_api_keys_only_use_secure_or_loopback_endpoints() {
        using fincept::ai_chat::is_allowed_llm_endpoint;
        QVERIFY(is_allowed_llm_endpoint(QStringLiteral("https://api.deepseek.com/v1")));
        QVERIFY(is_allowed_llm_endpoint(QStringLiteral("http://localhost:8080/v1")));
        QVERIFY(is_allowed_llm_endpoint(QStringLiteral("http://worker.localhost:8080/v1")));
        QVERIFY(is_allowed_llm_endpoint(QStringLiteral("http://127.4.3.2:8080/v1")));
        QVERIFY(is_allowed_llm_endpoint(QStringLiteral("http://[::1]:8080/v1")));
        QVERIFY(!is_allowed_llm_endpoint(QStringLiteral("http://api.deepseek.com/v1")));
        QVERIFY(!is_allowed_llm_endpoint(QStringLiteral("http://192.168.1.10:8080/v1")));
        QVERIFY(!is_allowed_llm_endpoint(QStringLiteral("ftp://localhost/models")));
        QVERIFY(!is_allowed_llm_endpoint(QStringLiteral("not a URL")));
    }

    void explicit_fincept_llm_is_blocked_by_credit_firewall() {
        fincept::ai_chat::RouterState state;
        state.profiles.insert(QStringLiteral("metered"),
                              {QStringLiteral("metered"), QStringLiteral("Fincept"), QStringLiteral("fincept"),
                               QString(), QString(), true, false, false});
        state.assignments.insert(
            fincept::ai_chat::ModelRouter::assignment_key(QString::fromLatin1(fincept::ai_chat::kFeatureAiChat), {}),
            QStringLiteral("metered"));
        const auto route = fincept::ai_chat::ModelRouter::resolve_with(
            state, QString::fromLatin1(fincept::ai_chat::kFeatureAiChat));
        QVERIFY(std::none_of(route.chain.cbegin(), route.chain.cend(),
                             [](const auto& choice) { return choice.is_fincept(); }));
        QCOMPARE(route.chain.constLast().provider_id(), QStringLiteral("deepseek"));
    }

    void unconfigured_ai_is_local() {
        QCOMPARE(fincept::policy::default_llm_provider(), QStringLiteral("deepseek"));
        QCOMPARE(fincept::policy::default_llm_base_url(), QString());
        QCOMPARE(fincept::policy::default_llm_model(), QStringLiteral("deepseek-flash"));
        QCOMPARE(fincept::policy::normalize_llm_model(QStringLiteral("deepseek"), QStringLiteral("deepseek-flash")),
                 QStringLiteral("deepseek-flash"));
        QCOMPARE(fincept::policy::normalize_llm_model(QStringLiteral("DEEPSEEK"), QStringLiteral("DeepSeek-Flash")),
                 QStringLiteral("deepseek-flash"));
        QCOMPARE(fincept::policy::normalize_llm_model(QStringLiteral("deepseek"), QStringLiteral("deepseek-chat")),
                 QStringLiteral("deepseek-flash"));
        QCOMPARE(fincept::policy::normalize_llm_model(QStringLiteral("deepseek"), QStringLiteral("deepseek-reasoner")),
                 QStringLiteral("deepseek-flash"));
        QCOMPARE(fincept::policy::normalize_llm_model(QStringLiteral("deepseek"), QStringLiteral("deepseek-v4-pro")),
                 QStringLiteral("deepseek-v4-pro"));
        QCOMPARE(fincept::policy::normalize_llm_model(QStringLiteral("openai"), QStringLiteral("gpt-5")),
                 QStringLiteral("gpt-5"));
    }

    void router_never_adds_metered_fallback_implicitly() {
        const auto route = fincept::ai_chat::ModelRouter::resolve_with(
            {}, QString::fromLatin1(fincept::ai_chat::kFeatureAiChat));
        QCOMPARE(route.chain.size(), 1);
        QCOMPARE(route.chain.constFirst().provider_id(), QStringLiteral("deepseek"));
        QCOMPARE(route.chain.constFirst().model, QStringLiteral("deepseek-flash"));
        QCOMPARE(route.chain.constFirst().reason, QStringLiteral("byo_fallback"));
        QCOMPARE(fincept::ai_chat::ModelRouter::billing_note(route.chain.constFirst()),
                 QStringLiteral("0 credits · billed to your DeepSeek API key"));
    }

    void local_usage_metrics_track_provider_tokens_and_local_calls() {
        using fincept::metrics::LocalUsageMetrics;
        LocalUsageMetrics::reset_for_test();
        LocalUsageMetrics::record_llm(QStringLiteral("deepseek"), 120, 30, true, 90, 3);
        LocalUsageMetrics::record_llm(QStringLiteral("deepseek"), 20, 10, false);
        LocalUsageMetrics::record_local_quantlib();
        LocalUsageMetrics::record_public_data(QStringLiteral("gdelt"));

        const auto snapshot = LocalUsageMetrics::snapshot();
        QCOMPARE(snapshot.llm_requests, 4);
        QCOMPARE(snapshot.successful_llm_requests, 3);
        QCOMPARE(snapshot.prompt_tokens, 140);
        QCOMPARE(snapshot.completion_tokens, 40);
        QCOMPARE(snapshot.cached_prompt_tokens, 90);
        QCOMPARE(snapshot.local_quantlib_calls, 1);
        QCOMPARE(snapshot.public_data_calls, 1);
        QCOMPARE(snapshot.provider_tokens.value(QStringLiteral("deepseek")), 180);
        QCOMPARE(snapshot.provider_cached_prompt_tokens.value(QStringLiteral("deepseek")), 90);
        QCOMPARE(snapshot.fincept_credits_used, 0);
    }

    void ai_quant_usage_is_bounded_before_local_accounting() {
        using fincept::services::quant::parse_ai_quant_llm_usage;
        const QJsonObject response{
            {QStringLiteral("usage"),
             QJsonObject{{QStringLiteral("provider"), QStringLiteral(" DeepSeek ")},
                         {QStringLiteral("prompt_tokens"), 150.0},
                         {QStringLiteral("completion_tokens"), 30.0},
                         {QStringLiteral("requests"), 7.0},
                         {QStringLiteral("total_tokens"), 1.0},
                         {QStringLiteral("cached_prompt_tokens"), 999.0}}}};

        const auto usage = parse_ai_quant_llm_usage(response);
        QVERIFY(usage.reported);
        QCOMPARE(usage.provider, QStringLiteral("deepseek"));
        QCOMPARE(usage.prompt_tokens, qint64(150));
        QCOMPARE(usage.completion_tokens, qint64(30));
        QCOMPARE(usage.cached_prompt_tokens, qint64(150));
        QCOMPARE(usage.requests, qint64(7));
    }

    void ai_quant_cache_never_persists_failure_envelopes() {
        using fincept::services::quant::is_cacheable_ai_quant_result;
        QVERIFY(is_cacheable_ai_quant_result(
            QJsonObject{{QStringLiteral("success"), true}, {QStringLiteral("models"), QJsonArray{}}}));
        QVERIFY(is_cacheable_ai_quant_result(QJsonObject{{QStringLiteral("models"), QJsonArray{}}}));
        QVERIFY(!is_cacheable_ai_quant_result(
            QJsonObject{{QStringLiteral("success"), false},
                        {QStringLiteral("error"), QStringLiteral("package not installed")}}));
    }

    void local_usage_metrics_persist_monthly_totals_and_provider_health() {
        using fincept::metrics::LocalUsageMetrics;
        QTemporaryDir temp;
        QVERIFY(temp.isValid());
        LocalUsageMetrics::set_storage_path(temp.filePath(QStringLiteral("usage.json")));
        LocalUsageMetrics::reset_for_test();
        LocalUsageMetrics::record_llm(QStringLiteral("deepseek"), 100, 25, true, 80);
        LocalUsageMetrics::record_local_quantlib();
        LocalUsageMetrics::record_public_data(QStringLiteral("rss"));

        LocalUsageMetrics::reload_for_test();
        const auto restored = LocalUsageMetrics::snapshot();
        QCOMPARE(restored.monthly_llm_requests, 1);
        QCOMPARE(restored.monthly_provider_tokens.value(QStringLiteral("deepseek")), 125);
        QCOMPARE(restored.cached_prompt_tokens, 80);
        QCOMPARE(restored.monthly_cached_prompt_tokens, 80);
        QCOMPARE(restored.provider_cached_prompt_tokens.value(QStringLiteral("deepseek")), 80);
        QCOMPARE(restored.monthly_provider_cached_prompt_tokens.value(QStringLiteral("deepseek")), 80);
        QCOMPARE(restored.monthly_local_quantlib_calls, 1);
        QCOMPARE(restored.monthly_public_data_calls, 1);
        QVERIFY(!restored.provider_last_success.value(QStringLiteral("deepseek")).isEmpty());
        QVERIFY(!restored.source_last_success.value(QStringLiteral("rss")).isEmpty());
        QCOMPARE(restored.fincept_credits_used, 0);
    }

    void local_usage_metrics_report_persistence_failures() {
        using fincept::metrics::LocalUsageMetrics;
        QTemporaryDir temp;
        QVERIFY(temp.isValid());
        const QString blocker_path = temp.filePath(QStringLiteral("not-a-directory"));
        QFile blocker(blocker_path);
        QVERIFY(blocker.open(QIODevice::WriteOnly));
        QCOMPARE(blocker.write("x"), qint64(1));
        blocker.close();

        LocalUsageMetrics::set_storage_path(blocker_path + QStringLiteral("/usage.json"));
        LocalUsageMetrics::reset_for_test();
        LocalUsageMetrics::record_llm(QStringLiteral("deepseek"), 10, 2, true, 5);

        const auto snapshot = LocalUsageMetrics::snapshot();
        QVERIFY(!snapshot.persistence_error.isEmpty());
        QCOMPARE(snapshot.llm_requests, 1);
    }

    void local_usage_metrics_never_overwrite_unreadable_totals() {
        using fincept::metrics::LocalUsageMetrics;
        QTemporaryDir temp;
        QVERIFY(temp.isValid());
        const QString path = temp.filePath(QStringLiteral("usage.json"));
        QFile file(path);
        QVERIFY(file.open(QIODevice::WriteOnly));
        QCOMPARE(file.write("not-json"), qint64(8));
        file.close();

        LocalUsageMetrics::set_storage_path(path);
        QVERIFY(!LocalUsageMetrics::snapshot().persistence_error.isEmpty());
        LocalUsageMetrics::record_llm(QStringLiteral("deepseek"), 10, 2, true, 5);

        QVERIFY(file.open(QIODevice::ReadOnly));
        QCOMPARE(file.readAll(), QByteArray("not-json"));
    }
};

QTEST_MAIN(CommunityPolicyTest)
#include "tst_community_policy.moc"
