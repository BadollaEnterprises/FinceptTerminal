#include "services/llm/LlmSummaryPolicy.h"

#include <QtTest>

using namespace fincept::ai_chat;

class TestLlmSummaryPolicy : public QObject {
    Q_OBJECT
  private slots:
    void exhausted_summary_is_small_and_protocol_safe();
    void native_provider_summary_prompt_is_bounded_and_keeps_recent_evidence();
};

void TestLlmSummaryPolicy::exhausted_summary_is_small_and_protocol_safe() {
    QJsonArray messages{
        QJsonObject{{QStringLiteral("role"), QStringLiteral("system")},
                    {QStringLiteral("content"), QStringLiteral("original system prompt")}},
        QJsonObject{{QStringLiteral("role"), QStringLiteral("user")},
                    {QStringLiteral("content"), QStringLiteral("OLD_") + QString(1800, QLatin1Char('x'))}},
        QJsonObject{{QStringLiteral("role"), QStringLiteral("assistant")},
                    {QStringLiteral("content"), QString()},
                    {QStringLiteral("tool_calls"),
                     QJsonArray{QJsonObject{{QStringLiteral("id"), QStringLiteral("call_1")},
                                            {QStringLiteral("function"), QJsonObject{{QStringLiteral("name"),
                                                                                     QStringLiteral("python")}}}}}}},
        QJsonObject{{QStringLiteral("role"), QStringLiteral("tool")},
                    {QStringLiteral("tool_call_id"), QStringLiteral("call_1")},
                    {QStringLiteral("content"), QStringLiteral("OLDER_RESULT_") + QString(900, QLatin1Char('y'))}},
        QJsonObject{{QStringLiteral("role"), QStringLiteral("assistant")},
                    {QStringLiteral("content"), QStringLiteral("intermediate observation")}},
        QJsonObject{{QStringLiteral("role"), QStringLiteral("tool")},
                    {QStringLiteral("tool_call_id"), QStringLiteral("call_2")},
                    {QStringLiteral("content"), QStringLiteral("LATEST_SIGNAL=42")}},
    };

    const QJsonArray compact = bounded_tool_summary_messages(
        messages, QStringLiteral("Summarise completed work; do not call tools."), /*max_json_bytes=*/900);
    const QByteArray wire = QJsonDocument(compact).toJson(QJsonDocument::Compact);

    QVERIFY2(wire.size() <= 900, wire.constData());
    QVERIFY(wire.contains("LATEST_SIGNAL=42"));
    QVERIFY(!wire.contains("OLD_"));
    for (const QJsonValue& value : compact) {
        const QJsonObject message = value.toObject();
        QVERIFY(message.value(QStringLiteral("role")).toString() == QLatin1String("system") ||
                message.value(QStringLiteral("role")).toString() == QLatin1String("user"));
        QVERIFY(!message.contains(QStringLiteral("tool_calls")));
        QVERIFY(!message.contains(QStringLiteral("tool_call_id")));
    }
}

void TestLlmSummaryPolicy::native_provider_summary_prompt_is_bounded_and_keeps_recent_evidence() {
    QJsonArray messages{
        QJsonObject{{QStringLiteral("role"), QStringLiteral("user")},
                    {QStringLiteral("content"), QStringLiteral("OLD_") + QString(2400, QLatin1Char('x'))}},
        QJsonObject{{QStringLiteral("role"), QStringLiteral("assistant")},
                    {QStringLiteral("content"),
                     QJsonArray{QJsonObject{{QStringLiteral("type"), QStringLiteral("tool_use")},
                                            {QStringLiteral("name"), QStringLiteral("market_pulse")},
                                            {QStringLiteral("input"), QJsonObject{}}}}}},
        QJsonObject{{QStringLiteral("role"), QStringLiteral("user")},
                    {QStringLiteral("content"),
                     QJsonArray{QJsonObject{{QStringLiteral("type"), QStringLiteral("tool_result")},
                                            {QStringLiteral("content"), QStringLiteral("LATEST_SIGNAL=42")}}}}},
    };

    const QString prompt = bounded_tool_summary_prompt(
        messages, QStringLiteral("Summarise completed work; do not call tools."), /*max_utf8_bytes=*/700);

    QVERIFY2(prompt.toUtf8().size() <= 700, qPrintable(prompt));
    QVERIFY(prompt.contains(QStringLiteral("LATEST_SIGNAL=42")));
    QVERIFY(prompt.count(QLatin1Char('x')) < 1000);
    QVERIFY(prompt.startsWith(QStringLiteral("Summarise completed work")));
}

QTEST_GUILESS_MAIN(TestLlmSummaryPolicy)
#include "tst_llm_summary_policy.moc"
