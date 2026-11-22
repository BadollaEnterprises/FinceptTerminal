#include "screens/ai_chat/AiChatEffortPolicy.h"
#include "services/llm/DeepSeekRequestPolicy.h"
#include "services/llm/LlmTransportPolicy.h"

#include <QtTest>

using namespace fincept::screens::aichat;
using fincept::ai_chat::apply_deepseek_thinking;

class TestAiChatEffortPolicy : public QObject {
    Q_OBJECT
  private slots:
    void ordinary_effort_has_no_usage_warning();
    void byo_high_effort_mentions_provider_usage_not_fincept_credits();
    void metered_fincept_high_effort_mentions_fincept_credits();
    void deepseek_effort_builds_current_thinking_fields();
    void tool_calls_use_a_single_request_transport();
    void text_tool_compatibility_respects_the_toggle();
};

void TestAiChatEffortPolicy::ordinary_effort_has_no_usage_warning() {
    QCOMPARE(effort_usage(QStringLiteral("low"), QStringLiteral("deepseek"), false), EffortUsage::Normal);
    QCOMPARE(effort_usage(QStringLiteral("medium"), QStringLiteral("deepseek"), false), EffortUsage::Normal);
}

void TestAiChatEffortPolicy::byo_high_effort_mentions_provider_usage_not_fincept_credits() {
    for (const QString& effort : {QStringLiteral("high"), QStringLiteral("xhigh"), QStringLiteral("max")})
        QCOMPARE(effort_usage(effort, QStringLiteral("deepseek"), false), EffortUsage::ProviderUsage);
    QCOMPARE(effort_usage(QStringLiteral("high"), QStringLiteral("openai"), true), EffortUsage::ProviderUsage);
}

void TestAiChatEffortPolicy::metered_fincept_high_effort_mentions_fincept_credits() {
    QCOMPARE(effort_usage(QStringLiteral("high"), QStringLiteral("fincept"), true),
             EffortUsage::FinceptCredits);
    QCOMPARE(effort_usage(QStringLiteral("high"), QStringLiteral("fincept"), false),
             EffortUsage::ProviderUsage);
}

void TestAiChatEffortPolicy::deepseek_effort_builds_current_thinking_fields() {
    QJsonObject request;
    apply_deepseek_thinking(request, QStringLiteral("medium"));
    QCOMPARE(request.value(QStringLiteral("reasoning_effort")).toString(), QStringLiteral("none"));
    QCOMPARE(request.value(QStringLiteral("thinking")).toObject().value(QStringLiteral("type")).toString(),
             QStringLiteral("disabled"));

    request = {};
    apply_deepseek_thinking(request, QStringLiteral("high"));
    QCOMPARE(request.value(QStringLiteral("reasoning_effort")).toString(), QStringLiteral("high"));
    QCOMPARE(request.value(QStringLiteral("thinking")).toObject().value(QStringLiteral("type")).toString(),
             QStringLiteral("enabled"));

    request = {};
    apply_deepseek_thinking(request, QStringLiteral("xhigh"));
    QCOMPARE(request.value(QStringLiteral("reasoning_effort")).toString(), QStringLiteral("high"));

    request = {};
    apply_deepseek_thinking(request, QStringLiteral("max"));
    QCOMPARE(request.value(QStringLiteral("reasoning_effort")).toString(), QStringLiteral("max"));

    // DeepSeek requires every prior assistant reasoning_content to be replayed
    // when thinking and tools are combined. Persisted Fincept chat history
    // intentionally contains only the visible answer, so a resumed/high-effort
    // conversation must fall back to non-thinking rather than receive HTTP 400.
    request = {
        {QStringLiteral("messages"),
         QJsonArray{QJsonObject{{QStringLiteral("role"), QStringLiteral("assistant")},
                                {QStringLiteral("content"), QStringLiteral("visible answer")}}}},
        {QStringLiteral("tools"), QJsonArray{QJsonObject{{QStringLiteral("type"), QStringLiteral("function")}}}},
    };
    apply_deepseek_thinking(request, QStringLiteral("high"));
    QCOMPARE(request.value(QStringLiteral("reasoning_effort")).toString(), QStringLiteral("none"));
    QCOMPARE(request.value(QStringLiteral("thinking")).toObject().value(QStringLiteral("type")).toString(),
             QStringLiteral("disabled"));

    // Within the live tool loop the complete provider message is available,
    // including reasoning_content, so thinking remains enabled.
    request = {
        {QStringLiteral("messages"),
         QJsonArray{QJsonObject{{QStringLiteral("role"), QStringLiteral("assistant")},
                                {QStringLiteral("content"), QStringLiteral("working")},
                                {QStringLiteral("reasoning_content"), QStringLiteral("private reasoning")}}}},
        {QStringLiteral("tools"), QJsonArray{QJsonObject{{QStringLiteral("type"), QStringLiteral("function")}}}},
    };
    apply_deepseek_thinking(request, QStringLiteral("high"));
    QCOMPARE(request.value(QStringLiteral("reasoning_effort")).toString(), QStringLiteral("high"));
    QCOMPARE(request.value(QStringLiteral("thinking")).toObject().value(QStringLiteral("type")).toString(),
             QStringLiteral("enabled"));
}

void TestAiChatEffortPolicy::tool_calls_use_a_single_request_transport() {
    QVERIFY(!fincept::ai_chat::should_stream_initial_request(true));
    QVERIFY(fincept::ai_chat::should_stream_initial_request(false));
}

void TestAiChatEffortPolicy::text_tool_compatibility_respects_the_toggle() {
    QVERIFY(fincept::ai_chat::should_execute_text_tool_calls(true));
    QVERIFY(!fincept::ai_chat::should_execute_text_tool_calls(false));
}

QTEST_GUILESS_MAIN(TestAiChatEffortPolicy)
#include "tst_ai_chat_effort_policy.moc"
