#include "core/policy/CommunityPolicy.h"
#include "core/metrics/LocalUsageMetrics.h"
#include "services/llm/ModelRouter.h"

#include <QtTest>
#include <QTemporaryDir>

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

    void fincept_cloud_sync_is_disabled() {
        QCOMPARE(fincept::policy::fincept_cloud_sync_enabled(), false);
    }

    void fincept_cloud_agents_are_disabled() {
        QCOMPARE(fincept::policy::fincept_cloud_agents_enabled(), false);
    }

    void fincept_chat_api_is_disabled() {
        QCOMPARE(fincept::policy::fincept_chat_api_enabled(), false);
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
        LocalUsageMetrics::record_llm(QStringLiteral("deepseek"), 120, 30, true);
        LocalUsageMetrics::record_llm(QStringLiteral("deepseek"), 20, 10, false);
        LocalUsageMetrics::record_local_quantlib();
        LocalUsageMetrics::record_public_data(QStringLiteral("gdelt"));

        const auto snapshot = LocalUsageMetrics::snapshot();
        QCOMPARE(snapshot.llm_requests, 2);
        QCOMPARE(snapshot.successful_llm_requests, 1);
        QCOMPARE(snapshot.prompt_tokens, 140);
        QCOMPARE(snapshot.completion_tokens, 40);
        QCOMPARE(snapshot.local_quantlib_calls, 1);
        QCOMPARE(snapshot.public_data_calls, 1);
        QCOMPARE(snapshot.provider_tokens.value(QStringLiteral("deepseek")), 180);
        QCOMPARE(snapshot.fincept_credits_used, 0);
    }

    void local_usage_metrics_persist_monthly_totals_and_provider_health() {
        using fincept::metrics::LocalUsageMetrics;
        QTemporaryDir temp;
        QVERIFY(temp.isValid());
        LocalUsageMetrics::set_storage_path(temp.filePath(QStringLiteral("usage.json")));
        LocalUsageMetrics::reset_for_test();
        LocalUsageMetrics::record_llm(QStringLiteral("deepseek"), 100, 25, true);
        LocalUsageMetrics::record_local_quantlib();
        LocalUsageMetrics::record_public_data(QStringLiteral("rss"));

        LocalUsageMetrics::reload_for_test();
        const auto restored = LocalUsageMetrics::snapshot();
        QCOMPARE(restored.monthly_llm_requests, 1);
        QCOMPARE(restored.monthly_provider_tokens.value(QStringLiteral("deepseek")), 125);
        QCOMPARE(restored.monthly_local_quantlib_calls, 1);
        QCOMPARE(restored.monthly_public_data_calls, 1);
        QVERIFY(!restored.provider_last_success.value(QStringLiteral("deepseek")).isEmpty());
        QVERIFY(!restored.source_last_success.value(QStringLiteral("rss")).isEmpty());
        QCOMPARE(restored.fincept_credits_used, 0);
    }
};

QTEST_MAIN(CommunityPolicyTest)
#include "tst_community_policy.moc"
