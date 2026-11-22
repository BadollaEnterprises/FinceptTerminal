#include "core/command/CommandSeed.h"

#include <QSet>
#include <QTest>

namespace seed = fincept::command::seed;

class TestCommandSeed : public QObject {
    Q_OBJECT

  private slots:
    void ids_are_unique();
    void terminal_aliases_are_discoverable();
    void relationship_views_are_discoverable();
};

void TestCommandSeed::ids_are_unique() {
    const QList<fincept::command::CommandItem> items = seed::all();
    QSet<QString> ids;
    for (const auto& item : items) {
        QVERIFY2(!item.id.isEmpty(), "seeded command id must not be empty");
        QVERIFY2(!ids.contains(item.id), qPrintable(QStringLiteral("duplicate seeded command id: %1").arg(item.id)));
        ids.insert(item.id);
    }
}

void TestCommandSeed::terminal_aliases_are_discoverable() {
    const QList<fincept::command::CommandItem> items = seed::section_items();
    const auto find = [&items](const QString& id) -> const fincept::command::CommandItem* {
        for (const auto& item : items)
            if (item.id == id)
                return &item;
        return nullptr;
    };

    const auto* credit = find(QStringLiteral("derivatives.credit"));
    QVERIFY(credit);
    QVERIFY(credit->keywords.contains(QStringLiteral("wcds")));

    const auto* financials = find(QStringLiteral("equity_research.financials"));
    QVERIFY(financials);
    QVERIFY(financials->keywords.contains(QStringLiteral("gf")));
    QVERIFY(financials->keywords.contains(QStringLiteral("fa")));

    const auto* overview = find(QStringLiteral("equity_research.overview"));
    QVERIFY(overview);
    QVERIFY(overview->keywords.contains(QStringLiteral("des")));
    QVERIFY(overview->keywords.contains(QStringLiteral("hp")));

    const auto* filings = find(QStringLiteral("equity_research.filings"));
    QVERIFY(filings);
    QVERIFY(filings->keywords.contains(QStringLiteral("cf")));

    const auto* dividends = find(QStringLiteral("equity_research.dividends"));
    QVERIFY(dividends);
    QVERIFY(dividends->keywords.contains(QStringLiteral("dvd")));

    const auto* earnings = find(QStringLiteral("equity_research.earnings"));
    QVERIFY(earnings);
    QVERIFY(earnings->keywords.contains(QStringLiteral("ern")));

    const auto* ownership = find(QStringLiteral("equity_research.ownership"));
    QVERIFY(ownership);
    QVERIFY(ownership->keywords.contains(QStringLiteral("hds")));
    QVERIFY(ownership->keywords.contains(QStringLiteral("tran")));

    const auto* peers = find(QStringLiteral("equity_research.peers"));
    QVERIFY(peers);
    QVERIFY(peers->keywords.contains(QStringLiteral("hms")));

    const auto* options = find(QStringLiteral("equity_research.options"));
    QVERIFY(options);
    QVERIFY(options->keywords.contains(QStringLiteral("omon")));

    const auto* time_sales = find(QStringLiteral("equity_trading.time_sales"));
    QVERIFY(time_sales);
    QVERIFY(time_sales->keywords.contains(QStringLiteral("tas")));
}

void TestCommandSeed::relationship_views_are_discoverable() {
    const QList<fincept::command::CommandItem> items = seed::section_items();
    const auto find = [&items](const QString& id) -> const fincept::command::CommandItem* {
        for (const auto& item : items)
            if (item.id == id)
                return &item;
        return nullptr;
    };

    const auto* holders = find(QStringLiteral("relationship_map.holders"));
    QVERIFY(holders);
    QVERIFY(holders->keywords.contains(QStringLiteral("top holders")));

    const auto* peers = find(QStringLiteral("relationship_map.peers"));
    QVERIFY(peers);
    QVERIFY(peers->keywords.contains(QStringLiteral("competitors")));

    const auto* supply_chain = find(QStringLiteral("relationship_map.supply_chain"));
    QVERIFY(supply_chain);
    QVERIFY(supply_chain->keywords.contains(QStringLiteral("customers")));
    QVERIFY(supply_chain->keywords.contains(QStringLiteral("suppliers")));
}

QTEST_GUILESS_MAIN(TestCommandSeed)

#include "tst_command_seed.moc"
