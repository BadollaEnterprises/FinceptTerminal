#include "core/command/CommandSeed.h"

#include <QSet>
#include <QTest>

namespace seed = fincept::command::seed;

class TestCommandSeed : public QObject {
    Q_OBJECT

  private slots:
    void ids_are_unique();
    void terminal_aliases_are_discoverable();
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

    const auto* options = find(QStringLiteral("equity_research.options"));
    QVERIFY(options);
    QVERIFY(options->keywords.contains(QStringLiteral("omon")));
}

QTEST_GUILESS_MAIN(TestCommandSeed)

#include "tst_command_seed.moc"
