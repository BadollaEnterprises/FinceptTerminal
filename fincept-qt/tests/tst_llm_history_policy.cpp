#include "services/llm/LlmHistoryPolicy.h"

#include <QtTest>

using namespace fincept::ai_chat;

class TestLlmHistoryPolicy : public QObject {
    Q_OBJECT
  private slots:
    void cost_control_default_is_bounded();
    void current_message_reserves_request_budget();
    void oversized_current_message_is_rejected();
};

void TestLlmHistoryPolicy::cost_control_default_is_bounded() {
    QCOMPARE(default_history_request_budget_chars(), qsizetype(48000));
}

void TestLlmHistoryPolicy::current_message_reserves_request_budget() {
    const std::vector<ConversationMessage> history = {
        {QStringLiteral("user"), QString(40, QLatin1Char('a'))},
        {QStringLiteral("assistant"), QString(40, QLatin1Char('b'))},
        {QStringLiteral("user"), QString(30, QLatin1Char('c'))},
        {QStringLiteral("assistant"), QString(30, QLatin1Char('d'))},
    };

    const HistoryTrimResult result = trim_history_for_request(history, /*current_message_chars=*/50,
                                                              /*request_budget_chars=*/120);

    QCOMPARE(result.messages.size(), std::size_t(2));
    QCOMPARE(result.messages[0].content, QString(30, QLatin1Char('c')));
    QCOMPARE(result.messages[1].content, QString(30, QLatin1Char('d')));
    QCOMPARE(result.dropped_messages, 2);
    QVERIFY(result.kept_chars + 50 <= 120);
}

void TestLlmHistoryPolicy::oversized_current_message_is_rejected() {
    QVERIFY(current_message_fits_request_budget(/*current_message_chars=*/120, /*request_budget_chars=*/120));
    QVERIFY(!current_message_fits_request_budget(/*current_message_chars=*/121, /*request_budget_chars=*/120));
    QVERIFY(current_message_fits_request_budget(/*current_message_chars=*/1000000, /*request_budget_chars=*/0));
}

QTEST_GUILESS_MAIN(TestLlmHistoryPolicy)
#include "tst_llm_history_policy.moc"
