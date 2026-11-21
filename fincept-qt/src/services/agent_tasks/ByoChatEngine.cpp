#include "services/agent_tasks/ByoChatExecutor.h"

#include "services/llm/LlmService.h"
#include "services/llm/ModelRouter.h"

#include <QJsonObject>
#include <QMetaObject>
#include <QPointer>
#include <QSet>

namespace fincept::agent_tasks {

namespace {

class LlmByoChatEngine final : public QObject, public IByoChatEngine {
  public:
    LlmByoChatEngine() {
        connect(&ai_chat::LlmService::instance(), &ai_chat::LlmService::finished_streaming, this,
                [this](const ai_chat::LlmResponse& response) {
                    const QString run_id = response.origin_session_id;
                    if (!active_.remove(run_id) || !listener_)
                        return;
                    ByoChatResult result;
                    result.success = response.success;
                    result.cancelled = response.cancelled;
                    result.content = response.content;
                    result.error = response.error;
                    result.provider = response.provider;
                    result.model = response.model;
                    result.prompt_tokens = response.prompt_tokens;
                    result.completion_tokens = response.completion_tokens;
                    listener_->byo_chat_finished(run_id, result);
                });
    }

    void set_listener(IByoChatListener* listener) override { listener_ = listener; }

    ByoChatReadiness readiness() const override {
        ai_chat::ChatRoute route;
        route.feature = QString::fromLatin1(ai_chat::kFeatureAgent);
        auto& llm = ai_chat::LlmService::instance();
        if (!llm.is_configured(route))
            return {false, QStringLiteral("llm_not_configured"),
                    tr("No BYO LLM is configured. Add your DeepSeek API key in Settings › LLM Config."), {}};
        const QJsonObject config = llm.resolved_config_json(route);
        const QString provider = config.value(QStringLiteral("provider")).toString();
        const QString model = config.value(QStringLiteral("model_id")).toString();
        return {true, {}, {}, model.isEmpty() ? provider : QStringLiteral("%1 · %2").arg(provider, model)};
    }

    void start(const QString& run_id, const QString& prompt) override {
        active_.insert(run_id);
        ai_chat::ChatRoute route;
        route.feature = QString::fromLatin1(ai_chat::kFeatureAgent);
        QPointer<LlmByoChatEngine> self = this;
        ai_chat::LlmService::instance().chat_streaming(
            prompt, {},
            [self, run_id](const QString& chunk, bool done) {
                if (!self || done || chunk.isEmpty() || chunk.startsWith(ai_chat::think_stream_prefix()) ||
                    chunk.startsWith(ai_chat::tool_stream_prefix()))
                    return;
                QMetaObject::invokeMethod(
                    self,
                    [self, run_id, chunk]() {
                        if (self && self->listener_ && self->active_.contains(run_id))
                            self->listener_->byo_chat_chunk(run_id, chunk);
                    },
                    Qt::QueuedConnection);
            },
            ai_chat::LlmService::ToolPolicy::None, run_id, route);
    }

    void cancel(const QString& run_id) override {
        if (active_.contains(run_id))
            ai_chat::LlmService::instance().cancel_active_request(run_id);
    }

  private:
    IByoChatListener* listener_ = nullptr;
    QSet<QString> active_;
};

} // namespace

ByoChatExecutor::ByoChatExecutor(QObject* parent)
    : ByoChatExecutor(std::make_unique<LlmByoChatEngine>(), parent) {}

} // namespace fincept::agent_tasks
