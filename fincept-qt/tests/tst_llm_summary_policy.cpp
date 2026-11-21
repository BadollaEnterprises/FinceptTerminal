#include "services/llm/LlmSummaryPolicy.h"

#include <QtTest>

using namespace fincept::ai_chat;

class TestLlmSummaryPolicy : public QObject {
    Q_OBJECT
  private slots:
    void exhausted_summary_is_small_and_protocol_safe();
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

QTEST_GUILESS_MAIN(TestLlmSummaryPolicy)
#include "tst_llm_summary_policy.moc"
