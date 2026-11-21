#include "screens/chat_mode/LocalChatHistory.h"

#include <QtTest>

using fincept::ChatMessage;
using fincept::chat_mode::compose_local_chat_prompt;

class TestLocalChatHistory : public QObject {
    Q_OBJECT

  private slots:
    void first_turn_stays_clean();
    void prior_turns_are_labeled_for_the_local_agent();
    void recent_turns_win_when_history_is_bounded();
};

void TestLocalChatHistory::first_turn_stays_clean() {
    const QString current = QStringLiteral("Analyze RELIANCE downside risk");
    QCOMPARE(compose_local_chat_prompt({}, current), current);
}

void TestLocalChatHistory::prior_turns_are_labeled_for_the_local_agent() {
    QVector<ChatMessage> history;
    history.append(ChatMessage{.role = QStringLiteral("user"), .content = QStringLiteral("Compare TCS and INFY")});
    history.append(ChatMessage{.role = QStringLiteral("assistant"),
                               .content = QStringLiteral("TCS has the steadier margin profile.")});
    history.append(ChatMessage{.role = QStringLiteral("system"), .content = QStringLiteral("Do not carry this over")});

    const QString prompt = compose_local_chat_prompt(history, QStringLiteral("What about valuation?"));
    QVERIFY(prompt.contains(QStringLiteral("<conversation_history>")));
    QVERIFY(prompt.contains(QStringLiteral("USER:\nCompare TCS and INFY")));
    QVERIFY(prompt.contains(QStringLiteral("ASSISTANT:\nTCS has the steadier margin profile.")));
    QVERIFY(!prompt.contains(QStringLiteral("Do not carry this over")));
    QVERIFY(prompt.contains(QStringLiteral("<current_message>\nWhat about valuation?\n</current_message>")));
}

void TestLocalChatHistory::recent_turns_win_when_history_is_bounded() {
    QVector<ChatMessage> history;
    history.append(ChatMessage{.role = QStringLiteral("user"), .content = QStringLiteral("OLDEST-") + QString(120, 'a')});
    history.append(ChatMessage{.role = QStringLiteral("assistant"),
                               .content = QStringLiteral("RECENT-") + QString(80, 'b')});

    const QString prompt = compose_local_chat_prompt(history, QStringLiteral("Continue"), 360, 140);
    QVERIFY(prompt.size() <= 360);
    QVERIFY(!prompt.contains(QStringLiteral("OLDEST-")));
    QVERIFY(prompt.contains(QStringLiteral("RECENT-")));
    QVERIFY(prompt.endsWith(QStringLiteral("<current_message>\nContinue\n</current_message>")));
}

QTEST_GUILESS_MAIN(TestLocalChatHistory)
#include "tst_local_chat_history.moc"
