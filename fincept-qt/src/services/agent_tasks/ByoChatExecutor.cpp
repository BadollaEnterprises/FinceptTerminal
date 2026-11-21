#include "services/agent_tasks/ByoChatExecutor.h"

#include <QCoreApplication>
#include <QJsonObject>

namespace fincept::agent_tasks {

namespace {
QString bce_tr(const char* text) {
    return QCoreApplication::translate("fincept::agent_tasks::ByoChatExecutor", text);
}
} // namespace

ByoChatExecutor::ByoChatExecutor(std::unique_ptr<IByoChatEngine> engine, QObject* parent)
    : IRunExecutor(parent), engine_(std::move(engine)) {
    if (engine_)
        engine_->set_listener(this);
    setProperty("available", true);
}

ByoChatExecutor::~ByoChatExecutor() {
    if (engine_)
        engine_->set_listener(nullptr);
}

QString ByoChatExecutor::engine_id() const {
    return QString::fromLatin1(kExecutorByoChat);
}

void ByoChatExecutor::start(const TaskRun& run, const QVector<RunStep>& steps) {
    Q_UNUSED(steps)
    if (!engine_) {
        fail(run.id, QStringLiteral("byo_engine_unavailable"), bce_tr("The BYO chat engine is unavailable."));
        return;
    }
    const ByoChatReadiness ready = engine_->readiness();
    if (!ready.ready) {
        fail(run.id, ready.code.isEmpty() ? QStringLiteral("llm_not_configured") : ready.code,
             ready.message.isEmpty() ? bce_tr("Configure your DeepSeek API key in Settings › LLM Config.")
                                     : ready.message);
        return;
    }
    active_.insert(run.id);
    RunEvent status;
    status.kind = RunEventKind::Status;
    status.title = ready.llm_label.isEmpty() ? bce_tr("Asking your BYO model")
                                             : bce_tr("Asking %1").arg(ready.llm_label);
    status.data.insert(QStringLiteral("billing"), QStringLiteral("0 Fincept credits"));
    emit event(run.id, status);
    engine_->start(run.id, run.prompt);
}

void ByoChatExecutor::answer(const QString& run_id, const QString& text) {
    Q_UNUSED(text)
    fail(run_id, QStringLiteral("not_waiting"), bce_tr("This chat reply is not waiting for an answer."));
}

void ByoChatExecutor::request_pause(const QString& run_id) {
    Q_UNUSED(run_id)
}

bool ByoChatExecutor::can_pause(const QString& run_id) const {
    Q_UNUSED(run_id)
    return false;
}

void ByoChatExecutor::cancel(const QString& run_id) {
    if (!active_.contains(run_id)) {
        emit finished(run_id, RunStatus::Cancelled, {}, {});
        return;
    }
    cancelling_.insert(run_id);
    if (engine_)
        engine_->cancel(run_id);
}

void ByoChatExecutor::byo_chat_chunk(const QString& run_id, const QString& chunk) {
    if (active_.contains(run_id) && !cancelling_.contains(run_id) && !chunk.isEmpty())
        emit text_delta(run_id, 0, chunk);
}

void ByoChatExecutor::byo_chat_finished(const QString& run_id, const ByoChatResult& result) {
    if (!active_.remove(run_id))
        return;
    const bool cancelled = cancelling_.remove(run_id) || result.cancelled;
    const int tokens = qMax(0, result.prompt_tokens) + qMax(0, result.completion_tokens);
    if (tokens > 0)
        emit usage(run_id, 0.0, tokens);
    if (cancelled) {
        emit finished(run_id, RunStatus::Cancelled, result.content, {});
        return;
    }
    if (result.success) {
        emit finished(run_id, RunStatus::Completed, result.content, {});
        return;
    }
    fail(run_id, QStringLiteral("byo_request_failed"),
         result.error.isEmpty() ? bce_tr("The configured BYO model did not return an answer.") : result.error);
}

void ByoChatExecutor::fail(const QString& run_id, const QString& code, const QString& message) {
    active_.remove(run_id);
    cancelling_.remove(run_id);
    RunEvent error;
    error.kind = RunEventKind::Error;
    error.title = bce_tr("BYO model request failed");
    error.body = message;
    error.is_error = true;
    error.data.insert(QStringLiteral("code"), code);
    emit event(run_id, error);
    emit finished(run_id, RunStatus::Failed, {}, message);
}

} // namespace fincept::agent_tasks
