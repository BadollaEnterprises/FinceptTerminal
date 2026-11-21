#include "services/agent_tasks/ByoChatExecutor.h"
#include "services/agent_tasks/ByoChatPort.h"
#include "services/agent_tasks/TaskRunTypes.h"

#include <QtTest>

#include <memory>

using namespace fincept::agent_tasks;

namespace fincept::agent_tasks {
ByoChatExecutor::ByoChatExecutor(QObject* parent)
    : ByoChatExecutor(std::unique_ptr<IByoChatEngine>(), parent) {}
} // namespace fincept::agent_tasks

namespace {

class FakeByoChatEngine final : public IByoChatEngine {
  public:
    void set_listener(IByoChatListener* value) override { listener = value; }
    ByoChatReadiness readiness() const override { return ready; }
    void start(const QString& run_id, const QString& prompt) override {
        started_id = run_id;
        started_prompt = prompt;
    }
    void cancel(const QString& run_id) override { cancelled_id = run_id; }

    void chunk(const QString& text) { listener->byo_chat_chunk(started_id, text); }
    void finish(const ByoChatResult& result) { listener->byo_chat_finished(started_id, result); }

    IByoChatListener* listener = nullptr;
    ByoChatReadiness ready{true, {}, {}, QStringLiteral("DeepSeek · deepseek-flash")};
    QString started_id;
    QString started_prompt;
    QString cancelled_id;
};

TaskRun chat_run() {
    TaskRun run;
    run.id = QStringLiteral("run-1");
    run.title = QStringLiteral("Follow-up");
    run.prompt = QStringLiteral("What changed since yesterday?");
    run.engine = QString::fromLatin1(kEngineLocal);
    run.origin = QString::fromLatin1(kOriginChat);
    return run;
}

} // namespace

class TestByoChatExecutor : public QObject {
    Q_OBJECT

  private slots:
    void plain_local_turn_routes_without_python();
    void streams_and_finishes_with_zero_fincept_credits();
    void missing_byo_model_fails_before_start();
    void cancellation_reaches_the_active_request();
};

void TestByoChatExecutor::plain_local_turn_routes_without_python() {
    QCOMPARE(executor_for(QString::fromLatin1(kEngineLocal), false), QString::fromLatin1(kExecutorByoChat));
    QCOMPARE(executor_for(QString::fromLatin1(kEngineLocal), true), QString::fromLatin1(kExecutorLocal));
}

void TestByoChatExecutor::streams_and_finishes_with_zero_fincept_credits() {
    auto* engine = new FakeByoChatEngine;
    ByoChatExecutor executor{std::unique_ptr<IByoChatEngine>(engine)};
    QVector<QString> chunks;
    QVector<QPair<double, int>> usage;
    RunStatus status = RunStatus::Failed;
    QString final;
    connect(&executor, &IRunExecutor::text_delta, &executor,
            [&](const QString&, int step, const QString& text) {
                QCOMPARE(step, 0);
                chunks.append(text);
            });
    connect(&executor, &IRunExecutor::usage, &executor,
            [&](const QString&, double credits, int tokens) { usage.append({credits, tokens}); });
    connect(&executor, &IRunExecutor::finished, &executor,
            [&](const QString&, RunStatus result, const QString& text, const QString&) {
                status = result;
                final = text;
            });

    executor.start(chat_run(), {});
    QCOMPARE(engine->started_id, QStringLiteral("run-1"));
    QCOMPARE(engine->started_prompt, QStringLiteral("What changed since yesterday?"));
    engine->chunk(QStringLiteral("Partial "));
    engine->chunk(QStringLiteral("answer"));
    engine->finish(ByoChatResult{.success = true,
                                .content = QStringLiteral("Partial answer"),
                                .prompt_tokens = 40,
                                .completion_tokens = 12});

    QCOMPARE(chunks, QVector<QString>({QStringLiteral("Partial "), QStringLiteral("answer")}));
    QCOMPARE(status, RunStatus::Completed);
    QCOMPARE(final, QStringLiteral("Partial answer"));
    QCOMPARE(usage.size(), 1);
    QCOMPARE(usage.first().first, 0.0);
    QCOMPARE(usage.first().second, 52);
}

void TestByoChatExecutor::missing_byo_model_fails_before_start() {
    auto* engine = new FakeByoChatEngine;
    engine->ready = {false, QStringLiteral("llm_not_configured"), QStringLiteral("Add your DeepSeek API key."), {}};
    ByoChatExecutor executor{std::unique_ptr<IByoChatEngine>(engine)};
    RunStatus status = RunStatus::Completed;
    QString error;
    QVector<RunEvent> events;
    connect(&executor, &IRunExecutor::event, &executor,
            [&](const QString&, const RunEvent& event) { events.append(event); });
    connect(&executor, &IRunExecutor::finished, &executor,
            [&](const QString&, RunStatus result, const QString&, const QString& message) {
                status = result;
                error = message;
            });

    executor.start(chat_run(), {});
    QVERIFY(engine->started_id.isEmpty());
    QCOMPARE(status, RunStatus::Failed);
    QCOMPARE(error, QStringLiteral("Add your DeepSeek API key."));
    QCOMPARE(events.size(), 1);
    QCOMPARE(events.first().data.value(QStringLiteral("code")).toString(), QStringLiteral("llm_not_configured"));
}

void TestByoChatExecutor::cancellation_reaches_the_active_request() {
    auto* engine = new FakeByoChatEngine;
    ByoChatExecutor executor{std::unique_ptr<IByoChatEngine>(engine)};
    RunStatus status = RunStatus::Failed;
    connect(&executor, &IRunExecutor::finished, &executor,
            [&](const QString&, RunStatus result, const QString&, const QString&) { status = result; });

    executor.start(chat_run(), {});
    executor.cancel(QStringLiteral("run-1"));
    QCOMPARE(engine->cancelled_id, QStringLiteral("run-1"));
    engine->finish(ByoChatResult{.cancelled = true, .content = QStringLiteral("Partial")});
    QCOMPARE(status, RunStatus::Cancelled);
}

QTEST_GUILESS_MAIN(TestByoChatExecutor)
#include "tst_byo_chat_executor.moc"
