#pragma once

#include "services/llm/LlmBudgetSettings.h"
#include "services/llm/LlmConversation.h"

#include <algorithm>
#include <cstddef>
#include <vector>

namespace fincept::ai_chat {

inline constexpr qsizetype default_history_request_budget_chars() { return kDefaultLlmHistoryChars; }

/// The new message cannot be trimmed without changing the user's request. If
/// it alone exceeds the shared request ceiling, reject it before any provider
/// call instead of silently bypassing the BYO cost guardrail. Zero disables.
inline bool current_message_fits_request_budget(qsizetype current_message_chars, qsizetype request_budget_chars) {
    return request_budget_chars <= 0 || std::max<qsizetype>(0, current_message_chars) <= request_budget_chars;
}

struct HistoryTrimResult {
    std::vector<ConversationMessage> messages;
    qsizetype original_chars = 0;
    qsizetype kept_chars = 0;
    int dropped_messages = 0;
};

/// Keep the newest complete dialogue turns inside a shared request budget.
/// Leading system messages are retained; `request_budget_chars == 0` disables
/// trimming. The current user message is not in `history`, so its size is
/// reserved before choosing old turns.
inline HistoryTrimResult trim_history_for_request(const std::vector<ConversationMessage>& history,
                                                  qsizetype current_message_chars,
                                                  qsizetype request_budget_chars) {
    HistoryTrimResult result;
    for (const auto& message : history)
        result.original_chars += message.content.size();

    if (request_budget_chars <= 0 || history.empty()) {
        result.messages = history;
        result.kept_chars = result.original_chars;
        return result;
    }

    std::size_t first_turn = 0;
    qsizetype system_chars = 0;
    while (first_turn < history.size() && history[first_turn].role == QLatin1String("system")) {
        system_chars += history[first_turn].content.size();
        ++first_turn;
    }

    const qsizetype available_for_turns =
        std::max<qsizetype>(0, request_budget_chars - std::max<qsizetype>(0, current_message_chars) - system_chars);
    std::size_t keep_from = history.size();
    qsizetype turn_chars = 0;
    while (keep_from > first_turn) {
        const qsizetype size = history[keep_from - 1].content.size();
        if (turn_chars + size > available_for_turns)
            break;
        turn_chars += size;
        --keep_from;
    }

    // Provider message arrays must not start with an orphaned assistant turn.
    while (keep_from < history.size() && history[keep_from].role == QLatin1String("assistant")) {
        turn_chars -= history[keep_from].content.size();
        ++keep_from;
    }

    result.messages.assign(history.begin(), history.begin() + static_cast<std::ptrdiff_t>(first_turn));
    result.messages.insert(result.messages.end(), history.begin() + static_cast<std::ptrdiff_t>(keep_from),
                           history.end());
    result.kept_chars = system_chars + turn_chars;
    result.dropped_messages = static_cast<int>(keep_from - first_turn);
    return result;
}

} // namespace fincept::ai_chat
