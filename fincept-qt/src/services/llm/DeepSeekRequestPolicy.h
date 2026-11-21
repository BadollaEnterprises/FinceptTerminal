#pragma once

#include <QJsonObject>
#include <QString>

namespace fincept::ai_chat {

// Translate the terminal's five UI effort levels onto DeepSeek's current V4
// contract. Low/medium intentionally use the fast non-thinking path; the
// higher levels opt into thinking and progressively larger reasoning budgets.
inline void apply_deepseek_thinking(QJsonObject& body, const QString& effort) {
    const QString normalized = effort.trimmed().toLower();
    if (normalized.isEmpty())
        return;

    QString provider_effort = QStringLiteral("none");
    QString thinking_type = QStringLiteral("disabled");
    if (normalized == QLatin1String("high") || normalized == QLatin1String("xhigh")) {
        provider_effort = QStringLiteral("high");
        thinking_type = QStringLiteral("enabled");
    } else if (normalized == QLatin1String("max")) {
        provider_effort = QStringLiteral("max");
        thinking_type = QStringLiteral("enabled");
    }

    body[QStringLiteral("reasoning_effort")] = provider_effort;
    body[QStringLiteral("thinking")] = QJsonObject{{QStringLiteral("type"), thinking_type}};
}

} // namespace fincept::ai_chat
