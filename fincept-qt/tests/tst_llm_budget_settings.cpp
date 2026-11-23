#include "services/llm/LlmBudgetSettings.h"

#include <QtTest>

using namespace fincept::ai_chat;

class TestLlmBudgetSettings : public QObject {
    Q_OBJECT
  private slots:
    void defaults_are_cost_conscious();
    void values_are_clamped_to_runtime_bounds();
    void config_map_round_trip_uses_stable_keys();
    void output_limit_respects_defaults_and_provider_caps();
    void missing_provider_usage_gets_a_budget_only_estimate();
};

void TestLlmBudgetSettings::defaults_are_cost_conscious() {
    const LlmBudgetSettings settings;

    QCOMPARE(settings.history_chars, 48000);
    QCOMPARE(settings.prompt_tokens, 32000);
    QCOMPARE(settings.completion_tokens, 12000);
    QCOMPARE(settings.summary_context_bytes, 12000);
    QCOMPARE(settings.summary_output_tokens, 1024);
}

void TestLlmBudgetSettings::values_are_clamped_to_runtime_bounds() {
    const LlmBudgetSettings settings = validate_llm_budget_settings({-1, 3000000, -20, 1, 9000});
    const LlmBudgetSettings high_output =
        validate_llm_budget_settings({48000, 32000, 3000000, 12000, 1024});

    QCOMPARE(settings.history_chars, 0);
    QCOMPARE(settings.prompt_tokens, 2000000);
    QCOMPARE(settings.completion_tokens, 0);
    QCOMPARE(settings.summary_context_bytes, 1024);
    QCOMPARE(settings.summary_output_tokens, 8192);
    QCOMPARE(high_output.completion_tokens, 131072);
}

void TestLlmBudgetSettings::config_map_round_trip_uses_stable_keys() {
    const LlmBudgetSettings wanted{64000, 45000, 14000, 16000, 1536};
    const QVariantMap values = llm_budget_settings_to_config_map(wanted);

    QCOMPARE(values.value(QString::fromLatin1(kLlmHistoryCharsKey)).toInt(), 64000);
    QCOMPARE(values.value(QString::fromLatin1(kLlmPromptTokensKey)).toInt(), 45000);
    QCOMPARE(values.value(QString::fromLatin1(kLlmCompletionTokensKey)).toInt(), 14000);
    QCOMPARE(values.value(QString::fromLatin1(kLlmSummaryContextBytesKey)).toInt(), 16000);
    QCOMPARE(values.value(QString::fromLatin1(kLlmSummaryOutputTokensKey)).toInt(), 1536);
    QCOMPARE(llm_budget_settings_from_config_map(values), wanted);
}

void TestLlmBudgetSettings::output_limit_respects_defaults_and_provider_caps() {
    QCOMPARE(resolve_llm_output_tokens(0, 65536), 4096);
    QCOMPARE(resolve_llm_output_tokens(12000, 65536), 12000);
    QCOMPARE(resolve_llm_output_tokens(12000, 2048), 2048);
    QCOMPARE(resolve_llm_output_tokens(3000000, 0), 131072);
    QCOMPARE(resolve_llm_output_tokens(-1, 0), 4096);
}

void TestLlmBudgetSettings::missing_provider_usage_gets_a_budget_only_estimate() {
    QCOMPARE(budgeted_llm_tokens(25, 300), 25);
    QCOMPARE(budgeted_llm_tokens(0, 12), 4);
    QCOMPARE(budgeted_llm_tokens(0, 10), 4);
    QCOMPARE(budgeted_llm_tokens(-1, 0), 0);
    QCOMPARE(budgeted_llm_tokens(0, qint64(kMaxLlmTurnTokens) * 6), kMaxLlmTurnTokens);
}

QTEST_GUILESS_MAIN(TestLlmBudgetSettings)
#include "tst_llm_budget_settings.moc"
