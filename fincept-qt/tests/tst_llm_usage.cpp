#include "services/llm/LlmUsage.h"

#include <QtTest>

using namespace fincept::ai_chat;

class TestLlmUsage : public QObject {
    Q_OBJECT
  private slots:
    void deepseek_cache_hits_are_preserved();
    void openai_nested_cache_hits_are_preserved();
    void gemini_cached_content_is_preserved();
    void multi_round_usage_includes_every_cache_hit();
    void scoped_usage_survives_early_return();
    void malformed_usage_cannot_reduce_accounting();
    void cache_hit_percent_is_safe_for_display();
    void streamed_usage_survives_cancel_or_late_failure();
    void provider_request_attempts_survive_missing_usage();
};

void TestLlmUsage::deepseek_cache_hits_are_preserved() {
    const QJsonObject response{{QStringLiteral("usage"),
                                QJsonObject{{QStringLiteral("prompt_tokens"), 12000},
                                            {QStringLiteral("completion_tokens"), 500},
                                            {QStringLiteral("total_tokens"), 12500},
                                            {QStringLiteral("prompt_cache_hit_tokens"), 9000},
                                            {QStringLiteral("prompt_cache_miss_tokens"), 3000}}}};

    const ProviderUsage usage = parse_provider_usage(response, QStringLiteral("deepseek"));
    QCOMPARE(usage.prompt_tokens, 12000);
    QCOMPARE(usage.completion_tokens, 500);
    QCOMPARE(usage.total_tokens, 12500);
    QCOMPARE(usage.cached_prompt_tokens, 9000);
    QCOMPARE(usage.provider_requests, 1);
}

void TestLlmUsage::openai_nested_cache_hits_are_preserved() {
    const QJsonObject response{{QStringLiteral("usage"),
                                QJsonObject{{QStringLiteral("prompt_tokens"), 8000},
                                            {QStringLiteral("completion_tokens"), 400},
                                            {QStringLiteral("prompt_tokens_details"),
                                             QJsonObject{{QStringLiteral("cached_tokens"), 6000}}}}}};

    const ProviderUsage usage = parse_provider_usage(response, QStringLiteral("openai"));
    QCOMPARE(usage.total_tokens, 8400);
    QCOMPARE(usage.cached_prompt_tokens, 6000);
}

void TestLlmUsage::gemini_cached_content_is_preserved() {
    const QJsonObject response{{QStringLiteral("usageMetadata"),
                                QJsonObject{{QStringLiteral("promptTokenCount"), 7000},
                                            {QStringLiteral("candidatesTokenCount"), 350},
                                            {QStringLiteral("totalTokenCount"), 7350},
                                            {QStringLiteral("cachedContentTokenCount"), 5000}}}};

    const ProviderUsage usage = parse_provider_usage(response, QStringLiteral("gemini"));
    QCOMPARE(usage.cached_prompt_tokens, 5000);
}

void TestLlmUsage::multi_round_usage_includes_every_cache_hit() {
    ProviderUsage total{10000, 500, 10500, 8000, 1};
    const ProviderUsage follow_up{12000, 700, 12700, 11000, 1};

    add_provider_usage(total, follow_up);

    QCOMPARE(total.prompt_tokens, 22000);
    QCOMPARE(total.completion_tokens, 1200);
    QCOMPARE(total.total_tokens, 23200);
    QCOMPARE(total.cached_prompt_tokens, 19000);
    QCOMPARE(total.provider_requests, 2);
}

void TestLlmUsage::scoped_usage_survives_early_return() {
    ProviderUsage response{10, 2, 12, 4, 1};
    auto fail_after_billed_round = [&response]() {
        const ProviderUsage accrued{100, 20, 120, 80, 1};
        ProviderUsageScope preserve(response, accrued);
        return;
    };

    fail_after_billed_round();

    QCOMPARE(response.prompt_tokens, 110);
    QCOMPARE(response.completion_tokens, 22);
    QCOMPARE(response.total_tokens, 132);
    QCOMPARE(response.cached_prompt_tokens, 84);
    QCOMPARE(response.provider_requests, 2);
}

void TestLlmUsage::malformed_usage_cannot_reduce_accounting() {
    const QJsonObject response{{QStringLiteral("usage"),
                                QJsonObject{{QStringLiteral("prompt_tokens"), -100},
                                            {QStringLiteral("completion_tokens"), 30},
                                            {QStringLiteral("total_tokens"), -70},
                                            {QStringLiteral("prompt_cache_hit_tokens"), 900}}}};
    const ProviderUsage parsed = parse_provider_usage(response, QStringLiteral("deepseek"));
    QCOMPARE(parsed.prompt_tokens, 0);
    QCOMPARE(parsed.completion_tokens, 30);
    QCOMPARE(parsed.total_tokens, 30);
    QCOMPARE(parsed.cached_prompt_tokens, 0);

    ProviderUsage total{100, 20, 120, 80};
    add_provider_usage(total, ProviderUsage{-50, -10, -60, 500});
    QCOMPARE(total.prompt_tokens, 100);
    QCOMPARE(total.completion_tokens, 20);
    QCOMPARE(total.total_tokens, 120);
    QCOMPARE(total.cached_prompt_tokens, 80);
}

void TestLlmUsage::cache_hit_percent_is_safe_for_display() {
    QCOMPARE(prompt_cache_hit_percent(12000, 9000), 75);
    QCOMPARE(prompt_cache_hit_percent(100, 120), 100);
    QCOMPARE(prompt_cache_hit_percent(qint64(3'000'000'000), qint64(1'500'000'000)), 50);
    QCOMPARE(prompt_cache_hit_percent(0, 0), -1);
}

void TestLlmUsage::streamed_usage_survives_cancel_or_late_failure() {
    const QJsonObject anthropic_tail{{QStringLiteral("output_tokens"), 12},
                                     {QStringLiteral("cache_read_input_tokens"), 50}};
    const auto anthropic =
        parse_stream_provider_usage(anthropic_tail, QStringLiteral("anthropic"), 100);
    QCOMPARE(anthropic.prompt_tokens, 100);
    QCOMPARE(anthropic.completion_tokens, 12);
    QCOMPARE(anthropic.total_tokens, 112);
    QCOMPARE(anthropic.cached_prompt_tokens, 50);
    QCOMPARE(anthropic.provider_requests, 1);

    const QJsonObject openai_tail{{QStringLiteral("prompt_tokens"), 40},
                                  {QStringLiteral("completion_tokens"), 8},
                                  {QStringLiteral("total_tokens"), 48}};
    const auto openai = parse_stream_provider_usage(openai_tail, QStringLiteral("deepseek"), 0);
    QCOMPARE(openai.prompt_tokens, 40);
    QCOMPARE(openai.completion_tokens, 8);
    QCOMPARE(openai.total_tokens, 48);
    QCOMPARE(openai.provider_requests, 1);
}

void TestLlmUsage::provider_request_attempts_survive_missing_usage() {
    ProviderUsage usage;
    note_provider_request(usage);
    note_provider_request(usage);

    QCOMPARE(usage.provider_requests, 2);
    QCOMPARE(usage.prompt_tokens, 0);
    QCOMPARE(usage.completion_tokens, 0);
}

QTEST_GUILESS_MAIN(TestLlmUsage)
#include "tst_llm_usage.moc"
