#include "services/report_builder/ReportAiDraftCache.h"

#include <QDir>
#include <QFile>
#include <QTemporaryDir>
#include <QtTest>

using namespace fincept::services::report_ai_cache;

class TestReportAiCache : public QObject {
    Q_OBJECT

  private slots:
    void key_is_prompt_and_route_specific();
    void round_trip_and_expiry();
    void corrupt_entries_are_removed();
    void retention_removes_the_oldest_entry();
};

void TestReportAiCache::key_is_prompt_and_route_specific() {
    const QString key = draft_cache_key(QStringLiteral("rules"), QStringLiteral("draft this"),
                                        QStringLiteral("deepseek"), QStringLiteral("deepseek-flash"));
    QCOMPARE(key.size(), 64);
    QVERIFY(key != draft_cache_key(QStringLiteral("new rules"), QStringLiteral("draft this"),
                                   QStringLiteral("deepseek"), QStringLiteral("deepseek-flash")));
    QVERIFY(key != draft_cache_key(QStringLiteral("rules"), QStringLiteral("draft that"),
                                   QStringLiteral("deepseek"), QStringLiteral("deepseek-flash")));
    QVERIFY(key != draft_cache_key(QStringLiteral("rules"), QStringLiteral("draft this"),
                                   QStringLiteral("openai"), QStringLiteral("gpt-5")));
}

void TestReportAiCache::round_trip_and_expiry() {
    QTemporaryDir tmp;
    QVERIFY(tmp.isValid());
    const QString key = draft_cache_key(QStringLiteral("rules"), QStringLiteral("prompt"),
                                        QStringLiteral("deepseek"), QStringLiteral("deepseek-flash"));
    const DraftCacheEntry saved{QStringLiteral("A locally saved draft."),
                                QStringLiteral("DeepSeek · deepseek-flash"), 1'000'000};
    QVERIFY(store_draft_cache(tmp.path(), key, saved));

    const auto loaded = load_draft_cache(tmp.path(), key, 1'000'500, 1000);
    QVERIFY(loaded.has_value());
    QCOMPARE(loaded->text, saved.text);
    QCOMPARE(loaded->route_label, saved.route_label);
    QCOMPARE(loaded->created_at_ms, saved.created_at_ms);

    QVERIFY(!load_draft_cache(tmp.path(), key, 1'001'001, 1000).has_value());
    QVERIFY(!QFile::exists(QDir(tmp.path()).filePath(key + QStringLiteral(".json"))));
}

void TestReportAiCache::corrupt_entries_are_removed() {
    QTemporaryDir tmp;
    QVERIFY(tmp.isValid());
    const QString key = draft_cache_key(QStringLiteral("rules"), QStringLiteral("prompt"),
                                        QStringLiteral("deepseek"), QStringLiteral("deepseek-flash"));
    QVERIFY(!store_draft_cache(tmp.path(), key, DraftCacheEntry{{}, {}, 100}));
    QVERIFY(!store_draft_cache(tmp.path(), QStringLiteral("../escape"),
                               DraftCacheEntry{QStringLiteral("x"), {}, 100}));

    QFile broken(QDir(tmp.path()).filePath(key + QStringLiteral(".json")));
    QVERIFY(broken.open(QIODevice::WriteOnly));
    QVERIFY(broken.write("{not-json") > 0);
    broken.close();
    QVERIFY(!load_draft_cache(tmp.path(), key, 100, 1000).has_value());
    QVERIFY(!broken.exists());
}

void TestReportAiCache::retention_removes_the_oldest_entry() {
    QTemporaryDir tmp;
    QVERIFY(tmp.isValid());
    QStringList keys;
    for (int i = 0; i < 3; ++i) {
        const QString key = draft_cache_key(QStringLiteral("rules"), QStringLiteral("prompt-%1").arg(i),
                                            QStringLiteral("deepseek"), QStringLiteral("deepseek-flash"));
        keys << key;
        QVERIFY(store_draft_cache(tmp.path(), key,
                                  DraftCacheEntry{QStringLiteral("answer-%1").arg(i), {}, 100 + i}, 2));
    }
    QVERIFY(!load_draft_cache(tmp.path(), keys.at(0), 200, 1000).has_value());
    QVERIFY(load_draft_cache(tmp.path(), keys.at(1), 200, 1000).has_value());
    QVERIFY(load_draft_cache(tmp.path(), keys.at(2), 200, 1000).has_value());
    QCOMPARE(QDir(tmp.path()).entryList({QStringLiteral("*.json")}, QDir::Files).size(), 2);
}

QTEST_APPLESS_MAIN(TestReportAiCache)
#include "tst_report_ai_cache.moc"
