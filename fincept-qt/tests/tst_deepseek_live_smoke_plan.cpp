#include "services/llm/DeepSeekLiveSmoke.h"

#include <QtTest>

using namespace fincept::ai_chat;

class TestDeepSeekLiveSmokePlan : public QObject {
    Q_OBJECT
  private slots:
    void plan_covers_product_routes_with_one_tool_probe();
};

void TestDeepSeekLiveSmokePlan::plan_covers_product_routes_with_one_tool_probe() {
    const QVector<DeepSeekLiveSmokeCase> cases = deepseek_live_smoke_plan();
    QCOMPARE(cases.size(), 4);
    QCOMPARE(cases[0].feature, QStringLiteral("ai_chat"));
    QCOMPARE(cases[1].feature, QStringLiteral("ai_chat"));
    QCOMPARE(cases[2].feature, QStringLiteral("news_analysis"));
    QCOMPARE(cases[3].feature, QStringLiteral("pro_analytics_explain"));

    int tool_cases = 0;
    for (const auto& item : cases) {
        QVERIFY(!item.id.trimmed().isEmpty());
        QVERIFY(!item.prompt.trimmed().isEmpty());
        QVERIFY(item.prompt.size() < 4000);
        tool_cases += item.use_tools ? 1 : 0;
    }
    QCOMPARE(tool_cases, 1);
    QVERIFY(cases[1].prompt.contains(QStringLiteral("live_deepseek_smoke_probe")));
}

QTEST_GUILESS_MAIN(TestDeepSeekLiveSmokePlan)
#include "tst_deepseek_live_smoke_plan.moc"
