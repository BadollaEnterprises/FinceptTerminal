#include "services/economics/MacroCalendarCommunityData.h"

#include <QtTest>

using namespace fincept::services;

class MacroCalendarCommunityDataTest : public QObject {
    Q_OBJECT

  private slots:
    void normalizes_forex_factory_rows_for_the_dashboard() {
        const QJsonArray raw = {
            QJsonObject{{"title", "US Consumer Price Index"},
                        {"country", "USD"},
                        {"date", "2026-11-20T08:30:00-05:00"},
                        {"impact", "High"},
                        {"forecast", "0.3%"},
                        {"previous", "0.2%"}},
            QJsonObject{{"title", "Bank Holiday"},
                        {"country", "JPY"},
                        {"date", "2026-11-20T00:00:00+09:00"},
                        {"impact", "Holiday"}},
        };

        const QJsonArray events = normalize_public_macro_calendar(raw);
        QCOMPARE(events.size(), 2);
        const QJsonObject cpi = events.at(0).toObject();
        QCOMPARE(cpi.value("event").toString(), QStringLiteral("US Consumer Price Index"));
        QCOMPARE(cpi.value("country").toString(), QStringLiteral("USD"));
        QCOMPARE(cpi.value("date").toString(), QStringLiteral("2026-11-20"));
        QCOMPARE(cpi.value("time").toString(), QStringLiteral("13:30"));
        QCOMPARE(cpi.value("importance").toInt(), 3);
        QCOMPARE(cpi.value("forecast").toString(), QStringLiteral("0.3%"));
        QCOMPARE(cpi.value("source").toString(), QStringLiteral("Forex Factory public weekly export"));
        QCOMPARE(events.at(1).toObject().value("importance").toInt(), 0);
    }

    void drops_rows_without_a_title_or_valid_timestamp() {
        const QJsonArray raw = {
            QJsonObject{{"country", "USD"}, {"date", "2026-11-20T08:30:00Z"}},
            QJsonObject{{"title", "Malformed"}, {"date", "not-a-date"}},
        };
        QVERIFY(normalize_public_macro_calendar(raw).isEmpty());
    }
};

QTEST_MAIN(MacroCalendarCommunityDataTest)
#include "tst_macro_calendar_community_data.moc"
