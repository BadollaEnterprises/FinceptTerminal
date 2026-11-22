#include "screens/equity_research/EquityResearchLinkPolicy.h"

#include <QTest>

using fincept::screens::equity_links::OpenInSymbolForm;
using fincept::screens::equity_links::open_in_destinations;

class TstEquityResearchLinkPolicy : public QObject {
    Q_OBJECT

  private slots:
    void exposes_company_relationship_research();
    void keeps_symbol_forms_explicit();
};

void TstEquityResearchLinkPolicy::exposes_company_relationship_research() {
    bool found = false;
    for (const auto& destination : open_in_destinations()) {
        if (QLatin1String(destination.screen_id) == QLatin1String("relationship_map")) {
            found = true;
            QCOMPARE(destination.symbol_form, OpenInSymbolForm::Research);
        }
    }
    QVERIFY(found);
}

void TstEquityResearchLinkPolicy::keeps_symbol_forms_explicit() {
    const auto destinations = open_in_destinations();
    QCOMPARE(destinations.size(), std::size_t{4});
    QCOMPARE(QLatin1String(destinations[0].screen_id), QLatin1String("equity_trading"));
    QCOMPARE(destinations[0].symbol_form, OpenInSymbolForm::Trade);
    QCOMPARE(QLatin1String(destinations[1].screen_id), QLatin1String("relationship_map"));
    QCOMPARE(destinations[1].symbol_form, OpenInSymbolForm::Research);
    QCOMPARE(QLatin1String(destinations[2].screen_id), QLatin1String("news"));
    QCOMPARE(destinations[2].symbol_form, OpenInSymbolForm::Bare);
    QCOMPARE(QLatin1String(destinations[3].screen_id), QLatin1String("backtesting"));
    QCOMPARE(destinations[3].symbol_form, OpenInSymbolForm::Research);
}

QTEST_GUILESS_MAIN(TstEquityResearchLinkPolicy)

#include "tst_equity_research_link_policy.moc"
