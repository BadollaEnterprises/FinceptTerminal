#include "ui/widgets/ProAnalyticsAiCache.h"

#include <QDir>
#include <QFile>
#include <QTemporaryDir>
#include <QtTest>

using namespace fincept::ui::pro_ai;

class TestProAnalyticsAiCache : public QObject {
    Q_OBJECT

  private slots:
    void key_is_prompt_and_route_specific();
    void fallback_answers_are_not_cacheable_under_the_primary_route();
    void round_trip_and_expiry();
    void rejects_empty_and_corrupt_entries();
    void retention_removes_the_oldest_entry();
};

void TestProAnalyticsAiCache::key_is_prompt_and_route_specific() {
    const QString a = explanation_cache_key(QStringLiteral("report-a"), QStringLiteral("deepseek"),
                                            QStringLiteral("deepseek-chat"));
    QCOMPARE(a.size(), 64);
    QVERIFY(a != explanation_cache_key(QStringLiteral("report-b"), QStringLiteral("deepseek"),
                                       QStringLiteral("deepseek-chat")));
    QVERIFY(a != explanation_cache_key(QStringLiteral("report-a"), QStringLiteral("openai"),
                                       QStringLiteral("gpt-5")));
    QVERIFY(a != explanation_cache_key(QStringLiteral("report-a"), QStringLiteral("deepseek"),
                                       QStringLiteral("deepseek-reasoner")));
    QVERIFY(explanation_cache_key(QStringLiteral("report-a"), QStringLiteral("deepseek"),
                                  QStringLiteral("deepseek-chat"), 1, QStringLiteral("profile-a")) !=
            explanation_cache_key(QStringLiteral("report-a"), QStringLiteral("deepseek"),
                                  QStringLiteral("deepseek-chat"), 1, QStringLiteral("profile-b")));
}

void TestProAnalyticsAiCache::fallback_answers_are_not_cacheable_under_the_primary_route() {
    QVERIFY(explanation_response_is_cacheable(true, false, QStringLiteral("A useful explanation."), {}));
    QVERIFY(!explanation_response_is_cacheable(false, false, QStringLiteral("partial"), {}));
    QVERIFY(!explanation_response_is_cacheable(true, true, QStringLiteral("partial"), {}));
    QVERIFY(!explanation_response_is_cacheable(true, false, QStringLiteral("  "), {}));
    QVERIFY(!explanation_response_is_cacheable(true, false, QStringLiteral("fallback answer"),
                                               QStringLiteral("Primary unavailable")));
}

void TestProAnalyticsAiCache::round_trip_and_expiry() {
    QTemporaryDir tmp;
    QVERIFY(tmp.isValid());
    const QString key = explanation_cache_key(QStringLiteral("same report"), QStringLiteral("deepseek"),
                                              QStringLiteral("deepseek-chat"));
    CachedExplanation saved;
    saved.text = QStringLiteral("Concentration is elevated.");
    saved.route_label = QStringLiteral("DeepSeek · deepseek-chat");
    saved.billing_note = QStringLiteral("billed to your DeepSeek API key");
    saved.created_at_ms = 1'000'000;
    QVERIFY(store_explanation_cache(tmp.path(), key, saved));

    const auto loaded = load_explanation_cache(tmp.path(), key, 1'000'500, 1000);
    QVERIFY(loaded.has_value());
    QCOMPARE(loaded->text, saved.text);
    QCOMPARE(loaded->route_label, saved.route_label);
    QCOMPARE(loaded->billing_note, saved.billing_note);
    QCOMPARE(loaded->created_at_ms, saved.created_at_ms);

    QVERIFY(!load_explanation_cache(tmp.path(), key, 1'001'001, 1000).has_value());
    QVERIFY(!QFile::exists(QDir(tmp.path()).filePath(key + QStringLiteral(".json"))));
}

void TestProAnalyticsAiCache::rejects_empty_and_corrupt_entries() {
    QTemporaryDir tmp;
    QVERIFY(tmp.isValid());
    const QString key = explanation_cache_key(QStringLiteral("report"), QStringLiteral("deepseek"),
                                              QStringLiteral("deepseek-chat"));
    CachedExplanation empty;
    empty.created_at_ms = 100;
    QVERIFY(!store_explanation_cache(tmp.path(), key, empty));
    QVERIFY(!store_explanation_cache(tmp.path(), QStringLiteral("../escape"),
                                     CachedExplanation{QStringLiteral("x"), {}, {}, 100}));

    QFile broken(QDir(tmp.path()).filePath(key + QStringLiteral(".json")));
    QVERIFY(broken.open(QIODevice::WriteOnly));
    QVERIFY(broken.write("{not-json") > 0);
    broken.close();
    QVERIFY(!load_explanation_cache(tmp.path(), key, 100, 1000).has_value());
}

void TestProAnalyticsAiCache::retention_removes_the_oldest_entry() {
    QTemporaryDir tmp;
    QVERIFY(tmp.isValid());
    QStringList keys;
    for (int i = 0; i < 3; ++i) {
        const QString key = explanation_cache_key(QStringLiteral("report-%1").arg(i), QStringLiteral("deepseek"),
                                                  QStringLiteral("deepseek-chat"));
        keys << key;
        CachedExplanation entry{QStringLiteral("answer-%1").arg(i), QStringLiteral("DeepSeek"), {}, 100 + i};
        QVERIFY(store_explanation_cache(tmp.path(), key, entry, 2));
    }
    QVERIFY(!load_explanation_cache(tmp.path(), keys.at(0), 200, 1000).has_value());
    QVERIFY(load_explanation_cache(tmp.path(), keys.at(1), 200, 1000).has_value());
    QVERIFY(load_explanation_cache(tmp.path(), keys.at(2), 200, 1000).has_value());
    QCOMPARE(QDir(tmp.path()).entryList({QStringLiteral("*.json")}, QDir::Files).size(), 2);
}

QTEST_APPLESS_MAIN(TestProAnalyticsAiCache)
#include "tst_pro_analytics_ai_cache.moc"
