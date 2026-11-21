#pragma once

#include <QString>

namespace fincept::agent_tasks {

struct ByoChatReadiness {
    bool ready = false;
    QString code;
    QString message;
    QString llm_label;
};

struct ByoChatResult {
    bool success = false;
    bool cancelled = false;
    QString content;
    QString error;
    QString provider;
    QString model;
    int prompt_tokens = 0;
    int completion_tokens = 0;
};

class IByoChatListener {
  public:
    virtual ~IByoChatListener() = default;
    virtual void byo_chat_chunk(const QString& run_id, const QString& chunk) = 0;
    virtual void byo_chat_finished(const QString& run_id, const ByoChatResult& result) = 0;
};

/// Narrow seam around LlmService. Tests inject a deterministic engine; the app
/// implementation sends directly to the configured user-owned API provider.
class IByoChatEngine {
  public:
    virtual ~IByoChatEngine() = default;
    virtual void set_listener(IByoChatListener* listener) = 0;
    virtual ByoChatReadiness readiness() const = 0;
    virtual void start(const QString& run_id, const QString& prompt) = 0;
    virtual void cancel(const QString& run_id) = 0;
};

} // namespace fincept::agent_tasks
