#pragma once

#include <QJsonObject>
#include <QString>
#include <QtGlobal>

#include <algorithm>
#include <cmath>

namespace fincept::services::quant {

struct AiQuantLlmUsage {
    QString provider;
    qint64 prompt_tokens = 0;
    qint64 completion_tokens = 0;
    qint64 cached_prompt_tokens = 0;
    bool reported = false;
};

/// Parse the provider-neutral usage envelope emitted by bundled AI Quant
/// agents. The child process is an accounting input, not a trusted authority:
/// reject non-finite/negative values, cap one run, and never allow cache reads
/// to exceed prompt tokens.
inline AiQuantLlmUsage parse_ai_quant_llm_usage(const QJsonObject& response) {
    AiQuantLlmUsage out;
    const QJsonValue value = response.value(QStringLiteral("usage"));
    if (!value.isObject())
        return out;

    const QJsonObject usage = value.toObject();
    auto counter = [&usage](const char* key) {
        constexpr qint64 kMaxRunTokens = 10'000'000;
        const double raw = usage.value(QLatin1String(key)).toDouble();
        if (!std::isfinite(raw) || raw <= 0)
            return qint64{0};
        return static_cast<qint64>(std::min(raw, static_cast<double>(kMaxRunTokens)));
    };

    out.provider = usage.value(QStringLiteral("provider")).toString().trimmed().toLower().left(64);
    if (out.provider.isEmpty())
        out.provider = QStringLiteral("unknown");
    out.prompt_tokens = counter("prompt_tokens");
    out.completion_tokens = counter("completion_tokens");
    out.cached_prompt_tokens = qMin(out.prompt_tokens, counter("cached_prompt_tokens"));
    out.reported = true;
    return out;
}

} // namespace fincept::services::quant
