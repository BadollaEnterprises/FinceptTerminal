#pragma once

#include "services/agent_tasks/ByoChatPort.h"
#include "services/agent_tasks/IRunExecutor.h"

#include <QSet>

#include <memory>

namespace fincept::agent_tasks {

/// One ordinary Chat Mode message answered directly by the configured BYO LLM.
/// It deliberately does not expose tools: advanced/tool-using runs continue to
/// use LocalAgenticExecutor with its read-only policy and approval boundary.
class ByoChatExecutor final : public IRunExecutor, private IByoChatListener {
    Q_OBJECT

  public:
    explicit ByoChatExecutor(QObject* parent = nullptr);
    explicit ByoChatExecutor(std::unique_ptr<IByoChatEngine> engine, QObject* parent = nullptr);
    ~ByoChatExecutor() override;

    QString engine_id() const override;
    void start(const TaskRun& run, const QVector<RunStep>& steps) override;
    void answer(const QString& run_id, const QString& text) override;
    void request_pause(const QString& run_id) override;
    void cancel(const QString& run_id) override;
    bool can_pause(const QString& run_id) const override;

  private:
    void byo_chat_chunk(const QString& run_id, const QString& chunk) override;
    void byo_chat_finished(const QString& run_id, const ByoChatResult& result) override;
    void fail(const QString& run_id, const QString& code, const QString& message);

    std::unique_ptr<IByoChatEngine> engine_;
    QSet<QString> active_;
    QSet<QString> cancelling_;
};

} // namespace fincept::agent_tasks
