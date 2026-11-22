#include "screens/equity_research/EquitySegmentsData.h"

#include <QTest>

using fincept::screens::parse_segment_revenue;

class TstEquitySegmentsData : public QObject {
    Q_OBJECT

  private slots:
    void parses_source_attributed_breakdowns();
    void preserves_honest_empty_state();
    void rejects_failed_and_malformed_envelopes();
};

void TstEquitySegmentsData::parses_source_attributed_breakdowns() {
    const QByteArray json = R"JSON({
      "success": true,
      "data": {
        "symbol": "GOOG",
        "name": "Alphabet Inc.",
        "source": {
          "name": "SEC EDGAR inline XBRL",
          "form": "10-K",
          "filed": "2026-02-05",
          "url": "https://www.sec.gov/Archives/example.htm"
        },
        "periods": [{
          "start": "2025-01-01",
          "end": "2025-12-31",
          "unit": "usd",
          "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
          "total_concept": "us-gaap:Revenues",
          "total": 402836000000,
          "breakdowns": [{
            "axis": "us-gaap:StatementBusinessSegmentsAxis",
            "label": "Statement Business Segments",
            "reported_sum": 402963000000,
            "reconciliation_delta": 127000000,
            "members": [
              {"member":"goog:GoogleServicesMember","label":"Google Services","value":342721000000,"share":0.8505},
              {"member":"goog:GoogleCloudMember","label":"Google Cloud","value":58705000000,"share":0.1457},
              {"member":"goog:OtherMember","label":"Other","value":1537000000,"share":0.0038}
            ]
          }]
        }]
      }
    })JSON";

    QString error;
    const auto parsed = parse_segment_revenue(json, &error);

    QVERIFY2(parsed.has_value(), qPrintable(error));
    QCOMPARE(parsed->symbol, QStringLiteral("GOOG"));
    QCOMPARE(parsed->source.form, QStringLiteral("10-K"));
    QCOMPARE(parsed->periods.size(), 1);
    const auto& period = parsed->periods.front();
    QCOMPARE(period.total, 402836000000.0);
    QCOMPARE(period.breakdowns.size(), 1);
    QCOMPARE(period.breakdowns.front().members.size(), 3);
    QCOMPARE(period.breakdowns.front().members.front().label, QStringLiteral("Google Services"));
    QCOMPARE(period.breakdowns.front().reconciliation_delta, 127000000.0);
}

void TstEquitySegmentsData::preserves_honest_empty_state() {
    const QByteArray json = R"JSON({
      "success": true,
      "data": {
        "symbol": "ACME",
        "name": "Acme",
        "reason": "No single-axis revenue breakdown reconciled to a reported annual total.",
        "source": {"name":"SEC EDGAR inline XBRL","form":"10-K","filed":"2026-01-01","url":"https://www.sec.gov/example"},
        "periods": []
      }
    })JSON";

    QString error;
    const auto parsed = parse_segment_revenue(json, &error);

    QVERIFY2(parsed.has_value(), qPrintable(error));
    QVERIFY(parsed->periods.isEmpty());
    QVERIFY(parsed->reason.contains(QStringLiteral("No single-axis")));
}

void TstEquitySegmentsData::rejects_failed_and_malformed_envelopes() {
    QString error;
    QVERIFY(!parse_segment_revenue(R"({"success":false,"error":"not found"})", &error));
    QCOMPARE(error, QStringLiteral("not found"));

    error.clear();
    QVERIFY(!parse_segment_revenue(R"({"success":true,"data":{"periods":"not-an-array"}})", &error));
    QVERIFY(!error.isEmpty());

    error.clear();
    QVERIFY(!parse_segment_revenue("not-json", &error));
    QVERIFY(!error.isEmpty());

    error.clear();
    const QByteArray unreconciled = R"({
      "success":true,"data":{"symbol":"ACME","source":{"name":"SEC","form":"10-K","filed":"2026-01-01","url":"https://www.sec.gov/x"},
      "periods":[{"start":"2025-01-01","end":"2025-12-31","unit":"usd","concept":"Revenue","total_concept":"Revenue","total":100,
      "breakdowns":[{"axis":"Axis","label":"Segments","reported_sum":80,"reconciliation_delta":-20,
      "members":[{"member":"A","label":"A","value":50,"share":0.625},{"member":"B","label":"B","value":30,"share":0.375}]}]}]}}
    )";
    QVERIFY(!parse_segment_revenue(unreconciled, &error));
    QVERIFY(error.contains(QStringLiteral("reconcile"), Qt::CaseInsensitive));
}

QTEST_GUILESS_MAIN(TstEquitySegmentsData)

#include "tst_equity_segments_data.moc"
