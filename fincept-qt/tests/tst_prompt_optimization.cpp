#include "screens/chat_mode/PromptOptimizationPolicy.h"

#include <QtTest>

using namespace fincept::chat_mode;

class TestPromptOptimization : public QObject {
    Q_OBJECT
  private slots:
    void request_preserves_the_original_and_demands_prompt_only_output();
    void deep_mode_requests_research_rigor();
    void response_cleanup_removes_common_model_wrappers();
};

void TestPromptOptimization::request_preserves_the_original_and_demands_prompt_only_output() {
    const QString original = QStringLiteral("Compare AAPL and MSFT for a five-year investor.");
    const QString request = prompt_optimization_request(original, QStringLiteral("lite"));

    QVERIFY(request.contains(original));
    QVERIFY(request.contains(QStringLiteral("Return only the rewritten prompt")));
    QVERIFY(request.contains(QStringLiteral("Do not answer the prompt")));
}

void TestPromptOptimization::deep_mode_requests_research_rigor() {
    const QString request = prompt_optimization_request(QStringLiteral("Analyze NVDA"), QStringLiteral("deep"));

    QVERIFY(request.contains(QStringLiteral("evidence"), Qt::CaseInsensitive));
    QVERIFY(request.contains(QStringLiteral("risks"), Qt::CaseInsensitive));
    QVERIFY(request.contains(QStringLiteral("sources"), Qt::CaseInsensitive));
}

void TestPromptOptimization::response_cleanup_removes_common_model_wrappers() {
    QCOMPARE(clean_optimized_prompt(QStringLiteral("```text\nAnalyze valuation and downside risk.\n```")),
             QStringLiteral("Analyze valuation and downside risk."));
    QCOMPARE(clean_optimized_prompt(QStringLiteral("Optimized prompt:\nCompare margins by segment.")),
             QStringLiteral("Compare margins by segment."));
    QCOMPARE(clean_optimized_prompt(QStringLiteral("\"Estimate intrinsic value with three scenarios.\"")),
             QStringLiteral("Estimate intrinsic value with three scenarios."));
}

QTEST_GUILESS_MAIN(TestPromptOptimization)
#include "tst_prompt_optimization.moc"
