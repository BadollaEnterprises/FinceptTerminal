#pragma once

#include "services/harness/HarnessChatTypes.h"
#include "services/llm/LlmBudgetSettings.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

namespace fincept::harness {

/// Conservative wire-size approximation of every caller-controlled value sent
/// to a harness. Keeping this at the shared execution boundary prevents a new
/// direct HarnessChatBackend consumer from bypassing the terminal-wide input
/// ceiling.
inline qint64 harness_chat_request_payload_bytes(const HarnessChatRequest& req) {
    QJsonArray history;
    for (const ChatTurn& turn : req.history) {
        history.append(QJsonObject{{QStringLiteral("role"), turn.role},
                                   {QStringLiteral("content"), turn.content}});
    }
    const QJsonObject request{{QStringLiteral("system_prompt"), req.system_prompt},
                              {QStringLiteral("user_message"), req.user_message},
                              {QStringLiteral("history"), history},
                              {QStringLiteral("json_schema"), req.json_schema},
                              {QStringLiteral("model"), req.model},
                              {QStringLiteral("effort"), req.effort}};
    return QJsonDocument(request).toJson(QJsonDocument::Compact).size();
}

inline QString harness_chat_request_budget_error(const HarnessChatRequest& req, int prompt_token_budget) {
    const qint64 bytes = harness_chat_request_payload_bytes(req);
    if (ai_chat::llm_request_fits_prompt_budget(bytes, prompt_token_budget))
        return {};
    return QStringLiteral("This complete AI request is conservatively estimated at %1 input tokens, above the "
                          "configured %2-token Turn Input limit. No external model process was started. Shorten "
                          "the system prompt/message or raise Turn Input Tokens in Settings → LLM Configuration.")
        .arg(ai_chat::budgeted_llm_tokens(0, bytes))
        .arg(prompt_token_budget);
}

} // namespace fincept::harness
