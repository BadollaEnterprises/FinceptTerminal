#pragma once

#include <QVariantMap>

#include <algorithm>

namespace fincept::ai_chat {

inline constexpr char kLlmHistoryCharsKey[] = "llm/history_max_chars";
inline constexpr char kLlmPromptTokensKey[] = "mcp/tool_loop_prompt_token_budget";
inline constexpr char kLlmCompletionTokensKey[] = "mcp/tool_loop_completion_token_budget";
inline constexpr char kLlmSummaryContextBytesKey[] = "mcp/tool_loop_summary_context_max_bytes";
inline constexpr char kLlmSummaryOutputTokensKey[] = "mcp/tool_loop_summary_max_tokens";

inline constexpr int kDefaultLlmHistoryChars = 48000;
inline constexpr int kDefaultLlmPromptTokens = 32000;
inline constexpr int kDefaultLlmCompletionTokens = 12000;
inline constexpr int kDefaultLlmSummaryContextBytes = 12000;
inline constexpr int kDefaultLlmSummaryOutputTokens = 1024;

inline constexpr int kMaxLlmTurnTokens = 2000000;
inline constexpr int kMinLlmSummaryContextBytes = 1024;
inline constexpr int kMaxLlmSummaryContextBytes = 64000;
inline constexpr int kMinLlmSummaryOutputTokens = 128;
inline constexpr int kMaxLlmSummaryOutputTokens = 8192;

struct LlmBudgetSettings {
    int history_chars = kDefaultLlmHistoryChars;
    int prompt_tokens = kDefaultLlmPromptTokens;
    int completion_tokens = kDefaultLlmCompletionTokens;
    int summary_context_bytes = kDefaultLlmSummaryContextBytes;
    int summary_output_tokens = kDefaultLlmSummaryOutputTokens;
};

inline bool operator==(const LlmBudgetSettings& lhs, const LlmBudgetSettings& rhs) {
    return lhs.history_chars == rhs.history_chars && lhs.prompt_tokens == rhs.prompt_tokens &&
           lhs.completion_tokens == rhs.completion_tokens &&
           lhs.summary_context_bytes == rhs.summary_context_bytes &&
           lhs.summary_output_tokens == rhs.summary_output_tokens;
}

inline LlmBudgetSettings validate_llm_budget_settings(LlmBudgetSettings settings) {
    settings.history_chars = std::clamp(settings.history_chars, 0, kMaxLlmTurnTokens);
    settings.prompt_tokens = std::clamp(settings.prompt_tokens, 0, kMaxLlmTurnTokens);
    settings.completion_tokens = std::clamp(settings.completion_tokens, 0, kMaxLlmTurnTokens);
    settings.summary_context_bytes = std::clamp(settings.summary_context_bytes, kMinLlmSummaryContextBytes,
                                                kMaxLlmSummaryContextBytes);
    settings.summary_output_tokens = std::clamp(settings.summary_output_tokens, kMinLlmSummaryOutputTokens,
                                                kMaxLlmSummaryOutputTokens);
    return settings;
}

/// Resolve a per-call output allowance: a non-positive profile value uses the
/// cost-conscious default, then both the absolute runtime ceiling and the
/// provider/model ceiling are applied.
inline int resolve_llm_output_tokens(int requested, int provider_cap, int fallback = 4096) {
    const int safe_fallback = std::clamp(fallback, 1, kMaxLlmTurnTokens);
    int resolved = requested > 0 ? requested : safe_fallback;
    resolved = std::clamp(resolved, 1, kMaxLlmTurnTokens);
    if (provider_cap > 0)
        resolved = std::min(resolved, provider_cap);
    return resolved;
}

inline QVariantMap llm_budget_settings_to_config_map(LlmBudgetSettings settings) {
    settings = validate_llm_budget_settings(settings);
    return {{QString::fromLatin1(kLlmHistoryCharsKey), settings.history_chars},
            {QString::fromLatin1(kLlmPromptTokensKey), settings.prompt_tokens},
            {QString::fromLatin1(kLlmCompletionTokensKey), settings.completion_tokens},
            {QString::fromLatin1(kLlmSummaryContextBytesKey), settings.summary_context_bytes},
            {QString::fromLatin1(kLlmSummaryOutputTokensKey), settings.summary_output_tokens}};
}

inline LlmBudgetSettings llm_budget_settings_from_config_map(const QVariantMap& values) {
    LlmBudgetSettings settings;
    settings.history_chars = values.value(QString::fromLatin1(kLlmHistoryCharsKey), kDefaultLlmHistoryChars).toInt();
    settings.prompt_tokens = values.value(QString::fromLatin1(kLlmPromptTokensKey), kDefaultLlmPromptTokens).toInt();
    settings.completion_tokens =
        values.value(QString::fromLatin1(kLlmCompletionTokensKey), kDefaultLlmCompletionTokens).toInt();
    settings.summary_context_bytes =
        values.value(QString::fromLatin1(kLlmSummaryContextBytesKey), kDefaultLlmSummaryContextBytes).toInt();
    settings.summary_output_tokens =
        values.value(QString::fromLatin1(kLlmSummaryOutputTokensKey), kDefaultLlmSummaryOutputTokens).toInt();
    return validate_llm_budget_settings(settings);
}

} // namespace fincept::ai_chat
