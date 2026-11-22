#pragma once

#include <QJsonArray>
#include <QJsonObject>
#include <QString>

namespace fincept::ai_chat {

// Translate the terminal's five UI effort levels onto DeepSeek's current V4
// contract. Low/medium intentionally use the fast non-thinking path; the
// higher levels opt into thinking and progressively larger reasoning budgets.
inline void apply_deepseek_thinking(QJsonObject& body, const QString& effort) {
    const QString normalized = effort.trimmed().toLower();

    // Scheduled/background features do not expose an effort picker and pass an
    // empty value. DeepSeek currently defaults an omitted control to expensive
    // high-effort thinking, so unspecified (and unknown) values must follow the
    // same non-thinking path as low/medium. Explicit high/xhigh/max remain
    // opt-ins below.
    QString provider_effort = QStringLiteral("none");
    QString thinking_type = QStringLiteral("disabled");
    if (normalized == QLatin1String("high") || normalized == QLatin1String("xhigh")) {
        provider_effort = QStringLiteral("high");
        thinking_type = QStringLiteral("enabled");
    } else if (normalized == QLatin1String("max")) {
        provider_effort = QStringLiteral("max");
        thinking_type = QStringLiteral("enabled");
    }

    // DeepSeek requires reasoning_content from every earlier assistant turn
    // when thinking and tools are combined; omitting it makes the API return
    // HTTP 400. Fincept deliberately persists only visible answer text, not
    // private chain of thought. A resumed conversation can therefore lack this
    // provider-only field. Use non-thinking mode for that request so it remains
    // reliable without storing hidden reasoning. Fresh turns and live tool-loop
    // turns retain thinking because the latter carry the complete API message.
    if (thinking_type == QLatin1String("enabled") && !body.value(QStringLiteral("tools")).toArray().isEmpty()) {
        const QJsonArray messages = body.value(QStringLiteral("messages")).toArray();
        for (const QJsonValue& value : messages) {
            const QJsonObject message = value.toObject();
            if (message.value(QStringLiteral("role")).toString() == QLatin1String("assistant") &&
                !message.contains(QStringLiteral("reasoning_content"))) {
                provider_effort = QStringLiteral("none");
                thinking_type = QStringLiteral("disabled");
                break;
            }
        }
    }

    body[QStringLiteral("reasoning_effort")] = provider_effort;
    body[QStringLiteral("thinking")] = QJsonObject{{QStringLiteral("type"), thinking_type}};
}

} // namespace fincept::ai_chat
