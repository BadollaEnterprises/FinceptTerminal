#include "screens/equity_research/EquityResearchFormat.h"

#include <QtTest/QtTest>

using fincept::screens::equity_fmt::earnings_session;

class EquityResearchFormatTest final : public QObject {
    Q_OBJECT

private slots:
    void earnings_session_codes() {
        QCOMPARE(earnings_session(QStringLiteral("BMO")), QStringLiteral("Before market open"));
        QCOMPARE(earnings_session(QStringLiteral(" amc ")), QStringLiteral("After market close"));
        QCOMPARE(earnings_session(QStringLiteral("TAS")), QStringLiteral("Time not supplied"));
        QVERIFY(earnings_session(QStringLiteral("UNKNOWN")).isEmpty());
        QVERIFY(earnings_session(QString()).isEmpty());
    }
};

QTEST_MAIN(EquityResearchFormatTest)
#include "tst_equity_research_format.moc"
