#include "services/maritime/MaritimeCommunityData.h"

#include <QtTest>

using namespace fincept::services::maritime;

class MaritimeCommunityDataTest : public QObject {
    Q_OBJECT

  private slots:
    void area_samples_are_deterministic_bounded_and_free() {
        AreaSearchParams area;
        area.min_lat = 5.0;
        area.max_lat = 25.0;
        area.min_lng = 60.0;
        area.max_lng = 90.0;
        area.limit = 12;

        const VesselsPage first = community_vessels_for_area(area);
        const VesselsPage second = community_vessels_for_area(area);
        QCOMPARE(first.vessels.size(), 12);
        QCOMPARE(first.total_count, 12);
        QCOMPARE(first.credits_used, 0.0);
        QCOMPARE(first.remaining_credits, -1.0);
        QCOMPARE(first.vessels.first().imo, second.vessels.first().imo);
        QCOMPARE(first.vessels.first().latitude, second.vessels.first().latitude);
        for (const auto& vessel : first.vessels) {
            QVERIFY(vessel.latitude >= area.min_lat && vessel.latitude <= area.max_lat);
            QVERIFY(vessel.longitude >= area.min_lng && vessel.longitude <= area.max_lng);
            QVERIFY(!vessel.name.isEmpty());
            QVERIFY(!vessel.imo.isEmpty());
        }
    }

    void history_is_a_free_ordered_local_track() {
        const VesselHistoryPage page = community_vessel_history(QStringLiteral("9000123"));
        QCOMPARE(page.imo, QStringLiteral("9000123"));
        QCOMPARE(page.history.size(), 24);
        QCOMPARE(page.total_records, 24);
        QCOMPARE(page.credits_used, 0.0);
        for (int i = 0; i < page.history.size(); ++i) {
            QCOMPARE(page.history[i].imo, page.imo);
            if (i > 0)
                QVERIFY(page.history[i - 1].last_updated >= page.history[i].last_updated);
        }
    }
};

QTEST_MAIN(MaritimeCommunityDataTest)
#include "tst_maritime_community_data.moc"
