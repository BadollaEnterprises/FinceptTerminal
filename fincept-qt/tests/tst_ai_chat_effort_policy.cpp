#include "screens/ai_chat/AiChatEffortPolicy.h"
#include "services/llm/DeepSeekRequestPolicy.h"

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
}

QTEST_GUILESS_MAIN(TestAiChatEffortPolicy)
#include "tst_ai_chat_effort_policy.moc"
