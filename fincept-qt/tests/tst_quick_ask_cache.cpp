#include "screens/dashboard/widgets/QuickAskAnswerCache.h"

#include <QtTest>

using namespace fincept::screens::widgets;

class TestQuickAskCache : public QObject {
    Q_OBJECT

  private slots:
    void key_is_question_and_route_specific();
    void successful_primary_answers_only();
};

void TestQuickAskCache::key_is_question_and_route_specific() {
    const QString key = quick_ask_answer_cache_key(QStringLiteral("  Explain VaR  "),
                                                   QStringLiteral("DeepSeek"),
                                                   QStringLiteral("deepseek-chat"));
    QCOMPARE(key.size(), 64);
    QCOMPARE(key, quick_ask_answer_cache_key(QStringLiteral("Explain VaR"),
                                             QStringLiteral("deepseek"),
                                             QStringLiteral("deepseek-chat")));
    QVERIFY(key != quick_ask_answer_cache_key(QStringLiteral("Explain expected shortfall"),
                                              QStringLiteral("deepseek"),
                                              QStringLiteral("deepseek-chat")));
    QVERIFY(key != quick_ask_answer_cache_key(QStringLiteral("Explain VaR"),
                                              QStringLiteral("openai"),
                                              QStringLiteral("gpt-5")));
    QVERIFY(key != quick_ask_answer_cache_key(QStringLiteral("Explain VaR"),
                                              QStringLiteral("deepseek"),
                                              QStringLiteral("deepseek-reasoner")));
    QVERIFY(quick_ask_answer_cache_key(QStringLiteral("Explain VaR"), QStringLiteral("deepseek"),
                                       QStringLiteral("deepseek-chat"), 1, QStringLiteral("profile-a")) !=
            quick_ask_answer_cache_key(QStringLiteral("Explain VaR"), QStringLiteral("deepseek"),
                                       QStringLiteral("deepseek-chat"), 1, QStringLiteral("profile-b")));
}

void TestQuickAskCache::successful_primary_answers_only() {
    QVERIFY(quick_ask_response_is_cacheable(true, false, QStringLiteral("A useful answer."), {}));
    QVERIFY(!quick_ask_response_is_cacheable(false, false, QStringLiteral("partial"), {}));
    QVERIFY(!quick_ask_response_is_cacheable(true, true, QStringLiteral("partial"), {}));
    QVERIFY(!quick_ask_response_is_cacheable(true, false, QStringLiteral("   "), {}));
    QVERIFY(!quick_ask_response_is_cacheable(true, false, QStringLiteral("fallback"),
                                             QStringLiteral("Primary unavailable")));
}

QTEST_APPLESS_MAIN(TestQuickAskCache)
#include "tst_quick_ask_cache.moc"
