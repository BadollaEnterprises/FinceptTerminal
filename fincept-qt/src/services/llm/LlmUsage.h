#pragma once

#include <QJsonObject>
#include <QString>

#include <algorithm>

namespace fincept::ai_chat {

struct ProviderUsage {
    int prompt_tokens = 0;
    int completion_tokens = 0;
    int total_tokens = 0;
    int cached_prompt_tokens = 0;
};

/// Sum provider-reported usage over every request in a multi-round turn.
/// Works for ProviderUsage and LlmResponse (the latter intentionally exposes
/// the same four accounting fields).
template <typename Usage>
inline void add_provider_usage(Usage& total, const Usage& round) {
    total.prompt_tokens += round.prompt_tokens;
    total.completion_tokens += round.completion_tokens;
    total.total_tokens +=
        round.total_tokens > 0 ? round.total_tokens : round.prompt_tokens + round.completion_tokens;
    total.cached_prompt_tokens += round.cached_prompt_tokens;
}

inline int prompt_cache_hit_percent(int prompt_tokens, int cached_prompt_tokens) {
    if (prompt_tokens <= 0)
        return -1;
    return std::clamp(static_cast<int>((100LL * std::max(0, cached_prompt_tokens)) / prompt_tokens), 0, 100);
}

/// Normalize token accounting from the provider dialects used by LlmService.
/// Cached input is included in prompt_tokens by providers; it is preserved as
/// a separate subset so the UI can show whether prompt caching is working.
inline ProviderUsage parse_provider_usage(const QJsonObject& response, const QString& provider) {
    ProviderUsage out;
    if (response.contains(QStringLiteral("usageMetadata"))) {
        const QJsonObject usage = response.value(QStringLiteral("usageMetadata")).toObject();
        out.prompt_tokens = usage.value(QStringLiteral("promptTokenCount")).toInt();
        out.completion_tokens = usage.value(QStringLiteral("candidatesTokenCount")).toInt();
        out.total_tokens = usage.contains(QStringLiteral("totalTokenCount"))
                               ? usage.value(QStringLiteral("totalTokenCount")).toInt()
                               : out.prompt_tokens + out.completion_tokens;
        out.cached_prompt_tokens = usage.value(QStringLiteral("cachedContentTokenCount")).toInt();
        return out;
    }

    const QJsonObject usage = response.value(QStringLiteral("usage")).toObject();
    if (usage.isEmpty())
        return out;

    if (provider == QLatin1String("anthropic")) {
        out.prompt_tokens = usage.value(QStringLiteral("input_tokens")).toInt();
        out.completion_tokens = usage.value(QStringLiteral("output_tokens")).toInt();
        out.cached_prompt_tokens = usage.value(QStringLiteral("cache_read_input_tokens")).toInt();
    } else {
        out.prompt_tokens = usage.value(QStringLiteral("prompt_tokens")).toInt();
        out.completion_tokens = usage.value(QStringLiteral("completion_tokens")).toInt();
        out.cached_prompt_tokens = usage.value(QStringLiteral("prompt_cache_hit_tokens")).toInt();
        if (out.cached_prompt_tokens <= 0) {
            out.cached_prompt_tokens = usage.value(QStringLiteral("prompt_tokens_details"))
                                           .toObject()
                                           .value(QStringLiteral("cached_tokens"))
                                           .toInt();
        }
    }
    out.total_tokens = usage.contains(QStringLiteral("total_tokens"))
                           ? usage.value(QStringLiteral("total_tokens")).toInt()
                           : out.prompt_tokens + out.completion_tokens;
    return out;
}

} // namespace fincept::ai_chat
