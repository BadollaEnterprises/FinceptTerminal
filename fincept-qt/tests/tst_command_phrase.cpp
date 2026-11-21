#include "core/command/CommandPhrase.h"

#include <QTest>

namespace phrase = fincept::command::phrase;

class TestCommandPhrase : public QObject {
    Q_OBJECT

  private slots:
    void symbol_research_views_data();
    void symbol_research_views();
    void keeps_existing_symbol_views();
};

void TestCommandPhrase::symbol_research_views_data() {
    QTest::addColumn<QString>("query");
    QTest::addColumn<QString>("symbol");
    QTest::addColumn<QString>("view");

    QTest::newRow("description-code") << QStringLiteral("AAPL DES") << QStringLiteral("AAPL")
                                       << QStringLiteral("research:overview");
    QTest::newRow("financials") << QStringLiteral("MSFT financials") << QStringLiteral("MSFT")
                                 << QStringLiteral("research:financials");
    QTest::newRow("filings-code") << QStringLiteral("CF NVDA") << QStringLiteral("NVDA")
                                   << QStringLiteral("research:filings");
    QTest::newRow("technical") << QStringLiteral("TSLA technicals") << QStringLiteral("TSLA")
                                << QStringLiteral("research:technicals");
    QTest::newRow("valuation-first") << QStringLiteral("valuation AMZN") << QStringLiteral("AMZN")
                                      << QStringLiteral("research:valuation");
    QTest::newRow("analyst-two-words") << QStringLiteral("META analyst ratings") << QStringLiteral("META")
                                       << QStringLiteral("research:analyst");
    QTest::newRow("holders-code") << QStringLiteral("HDS GOOG") << QStringLiteral("GOOG")
                                   << QStringLiteral("research:ownership");
    QTest::newRow("short-interest") << QStringLiteral("GME short interest") << QStringLiteral("GME")
                                     << QStringLiteral("research:overview");
    QTest::newRow("earnings-code") << QStringLiteral("NFLX ERN") << QStringLiteral("NFLX")
                                    << QStringLiteral("research:earnings");
}

void TestCommandPhrase::symbol_research_views() {
    QFETCH(QString, query);
    QFETCH(QString, symbol);
    QFETCH(QString, view);

    const QList<phrase::Phrase> parsed = phrase::parse(query);
    QCOMPARE(parsed.size(), 1);
    QCOMPARE(parsed.first().kind, phrase::Kind::SymbolView);
    QCOMPARE(parsed.first().symbols, QStringList{symbol});
    QCOMPARE(parsed.first().view, view);
}

void TestCommandPhrase::keeps_existing_symbol_views() {
    struct Case {
        const char* query;
        const char* view;
    };
    const Case cases[] = {
        {"AAPL chart", "chart"},
        {"news MSFT", "news"},
        {"SPY option chain", "options"},
        {"TSLA peers", "peers"},
    };
    for (const Case& c : cases) {
        const QList<phrase::Phrase> parsed = phrase::parse(QString::fromLatin1(c.query));
        QCOMPARE(parsed.size(), 1);
        QCOMPARE(parsed.first().kind, phrase::Kind::SymbolView);
        QCOMPARE(parsed.first().view, QString::fromLatin1(c.view));
    }
}

QTEST_GUILESS_MAIN(TestCommandPhrase)

#include "tst_command_phrase.moc"
