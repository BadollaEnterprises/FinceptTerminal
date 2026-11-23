#pragma once

#include <QJsonObject>
#include <QString>

#include <algorithm>
#include <limits>

namespace fincept::ai_chat {

struct ProviderUsage {
    int prompt_tokens = 0;
    int completion_tokens = 0;
    int total_tokens = 0;
    int cached_prompt_tokens = 0;
    int provider_requests = 0;
};

inline int bounded_usage_count(qint64 value) {
    return static_cast<int>(std::clamp<qint64>(value, 0, std::numeric_limits<int>::max()));
}

inline ProviderUsage normalize_provider_usage(ProviderUsage usage) {
    usage.prompt_tokens = bounded_usage_count(usage.prompt_tokens);
    usage.completion_tokens = bounded_usage_count(usage.completion_tokens);
    const int measured_total = bounded_usage_count(qint64(usage.prompt_tokens) + usage.completion_tokens);
    usage.total_tokens = std::max(bounded_usage_count(usage.total_tokens), measured_total);
    usage.cached_prompt_tokens = std::clamp(usage.cached_prompt_tokens, 0, usage.prompt_tokens);
    usage.provider_requests = std::clamp(usage.provider_requests, 0, 1'000'000);
    return usage;
}

/// Sum provider-reported usage over every request in a multi-round turn.
/// Works for ProviderUsage and LlmResponse (the latter intentionally exposes
/// the same five accounting fields).
template <typename Usage>
inline void add_provider_usage(Usage& total, const Usage& round) {
    const int round_prompt = bounded_usage_count(round.prompt_tokens);
    const int round_completion = bounded_usage_count(round.completion_tokens);
    const int round_total = std::max(bounded_usage_count(round.total_tokens),
                                     bounded_usage_count(qint64(round_prompt) + round_completion));
    const int round_cached = std::clamp(round.cached_prompt_tokens, 0, round_prompt);
    total.prompt_tokens = bounded_usage_count(qint64(bounded_usage_count(total.prompt_tokens)) + round_prompt);
    total.completion_tokens =
        bounded_usage_count(qint64(bounded_usage_count(total.completion_tokens)) + round_completion);
    total.total_tokens = bounded_usage_count(qint64(bounded_usage_count(total.total_tokens)) + round_total);
    total.cached_prompt_tokens =
        bounded_usage_count(qint64(bounded_usage_count(total.cached_prompt_tokens)) + round_cached);
    total.provider_requests = bounded_usage_count(
        qint64(bounded_usage_count(total.provider_requests)) + bounded_usage_count(round.provider_requests));
}

/// Preserve usage accrued by a multi-request operation on every exit path.
/// Network, parse, cancellation, and budget failures are still billable, so a
/// caller must not lose completed-round usage merely because the final result
/// is unsuccessful.
template <typename Usage>
class ProviderUsageScope {
  public:
    ProviderUsageScope(Usage& target, const Usage& accrued) : target_(target), accrued_(accrued) {}
    ~ProviderUsageScope() { add_provider_usage(target_, accrued_); }

    ProviderUsageScope(const ProviderUsageScope&) = delete;
    ProviderUsageScope& operator=(const ProviderUsageScope&) = delete;

  private:
    Usage& target_;
    const Usage& accrued_;
};

inline int prompt_cache_hit_percent(qint64 prompt_tokens, qint64 cached_prompt_tokens) {
    if (prompt_tokens <= 0)
        return -1;
    const long double percent =
        (100.0L * static_cast<long double>(std::max<qint64>(0, cached_prompt_tokens))) /
        static_cast<long double>(prompt_tokens);
    return std::clamp(static_cast<int>(percent), 0, 100);
}

/// Normalize token accounting from the provider dialects used by LlmService.
/// Cached input is included in prompt_tokens by providers; it is preserved as
/// a separate subset so the UI can show whether prompt caching is working.
inline ProviderUsage parse_provider_usage(const QJsonObject& response, const QString& provider) {
    ProviderUsage out;
    // This function is called for one completed provider response. Preserve
    // the request count even when a gateway omits token usage metadata.
    out.provider_requests = 1;
    if (response.contains(QStringLiteral("usageMetadata"))) {
        const QJsonObject usage = response.value(QStringLiteral("usageMetadata")).toObject();
        out.prompt_tokens = usage.value(QStringLiteral("promptTokenCount")).toInt();
        out.completion_tokens = usage.value(QStringLiteral("candidatesTokenCount")).toInt();
        out.total_tokens = usage.contains(QStringLiteral("totalTokenCount"))
                               ? usage.value(QStringLiteral("totalTokenCount")).toInt()
                               : out.prompt_tokens + out.completion_tokens;
        out.cached_prompt_tokens = usage.value(QStringLiteral("cachedContentTokenCount")).toInt();
        return normalize_provider_usage(out);
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
    return normalize_provider_usage(out);
}

/// Parse the usage object observed while consuming an SSE response. Anthropic
/// splits input tokens into message_start and output/cache tokens into later
/// events, while OpenAI-compatible providers send the complete object at the
/// tail. Keeping this provider-neutral lets callers account for an observed
/// usage event even when the stream is subsequently cancelled or fails.
inline ProviderUsage parse_stream_provider_usage(QJsonObject tail_usage, const QString& provider,
                                                 int anthropic_input_tokens) {
    if (provider == QLatin1String("anthropic") && anthropic_input_tokens > 0 &&
        !tail_usage.contains(QStringLiteral("input_tokens"))) {
        tail_usage.insert(QStringLiteral("input_tokens"), anthropic_input_tokens);
    }
    if (tail_usage.isEmpty())
        return {};
    return parse_provider_usage(QJsonObject{{QStringLiteral("usage"), tail_usage}}, provider);
}

} // namespace fincept::ai_chat
