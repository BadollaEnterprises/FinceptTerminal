#pragma once

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QString>
#include <QStringList>

#include <algorithm>

namespace fincept::ai_chat {

namespace detail {
inline QString summary_content(const QJsonValue& value) {
    if (value.isString())
        return value.toString().trimmed();
    if (value.isArray())
        return QString::fromUtf8(QJsonDocument(value.toArray()).toJson(QJsonDocument::Compact));
    if (value.isObject())
        return QString::fromUtf8(QJsonDocument(value.toObject()).toJson(QJsonDocument::Compact));
    return {};
}

inline QJsonArray summary_wire(const QString& instruction, const QStringList& evidence) {
    QJsonArray out{QJsonObject{{QStringLiteral("role"), QStringLiteral("system")},
                               {QStringLiteral("content"), instruction}}};
    if (!evidence.isEmpty()) {
        out.append(QJsonObject{
            {QStringLiteral("role"), QStringLiteral("user")},
            {QStringLiteral("content"),
             QStringLiteral("Recent conversation evidence (oldest to newest):\n") + evidence.join(QLatin1Char('\n'))}});
    }
    return out;
}

inline int summary_wire_bytes(const QString& instruction, const QStringList& evidence) {
    return QJsonDocument(summary_wire(instruction, evidence)).toJson(QJsonDocument::Compact).size();
}

inline QString summary_prompt(const QString& instruction, const QStringList& evidence) {
    QString out = instruction.trimmed();
    if (!evidence.isEmpty())
        out += QStringLiteral("\n\nRecent conversation evidence (oldest to newest):\n") +
               evidence.join(QLatin1Char('\n'));
    return out;
}

inline int summary_prompt_bytes(const QString& instruction, const QStringList& evidence) {
    return summary_prompt(instruction, evidence).toUtf8().size();
}
} // namespace detail

/// Build a small, valid OpenAI-compatible context for the forced summary after
/// provider token exhaustion. Tool messages cannot be copied directly because
/// they require their matching assistant tool_calls; flattening only their
/// textual evidence avoids orphaned protocol objects and keeps the newest data.
inline QJsonArray bounded_tool_summary_messages(const QJsonArray& messages, QString instruction,
                                                int max_json_bytes) {
    max_json_bytes = std::max(128, max_json_bytes);

    // An unusually long configured instruction must not defeat the hard wire
    // budget before evidence is considered.
    while (detail::summary_wire_bytes(instruction, {}) > max_json_bytes && instruction.size() > 32)
        instruction.chop(std::min<qsizetype>(64, instruction.size() - 32));

    QStringList newest_first;
    for (qsizetype i = messages.size() - 1; i >= 0; --i) {
        const QJsonObject message = messages.at(i).toObject();
        const QString role = message.value(QStringLiteral("role")).toString().trimmed().toLower();
        if (role.isEmpty() || role == QLatin1String("system"))
            continue;
        QString content = detail::summary_content(message.value(QStringLiteral("content")));
        if (content.isEmpty())
            continue;
        const QString prefix = QLatin1Char('[') + role + QStringLiteral("] ");
        QString line = prefix + content;

        QStringList candidate = newest_first;
        candidate.append(line);
        std::reverse(candidate.begin(), candidate.end());
        if (detail::summary_wire_bytes(instruction, candidate) <= max_json_bytes) {
            newest_first.append(line);
            continue;
        }

        // Preserve as much as possible of the first message that crosses the
        // ceiling, then stop: older evidence is less useful than the newest
        // complete/truncated evidence already selected.
        int low = 0;
        int high = content.size();
        int best = 0;
        while (low <= high) {
            const int mid = low + (high - low) / 2;
            QStringList partial = newest_first;
            partial.append(prefix + content.left(mid) + QStringLiteral("…"));
            std::reverse(partial.begin(), partial.end());
            if (detail::summary_wire_bytes(instruction, partial) <= max_json_bytes) {
                best = mid;
                low = mid + 1;
            } else {
                high = mid - 1;
            }
        }
        if (best > 0)
            newest_first.append(prefix + content.left(best) + QStringLiteral("…"));
        break;
    }

    std::reverse(newest_first.begin(), newest_first.end());
    return detail::summary_wire(instruction, newest_first);
}

/// Build the equivalent compact summary request as plain text for native
/// provider dialects (Anthropic/Gemini) and text-tool fallbacks. Keeping this
/// separate from the OpenAI message wire avoids invalid protocol objects while
/// applying the same newest-evidence and byte-ceiling policy everywhere.
inline QString bounded_tool_summary_prompt(const QJsonArray& messages, QString instruction, int max_utf8_bytes) {
    max_utf8_bytes = std::max(128, max_utf8_bytes);

    while (detail::summary_prompt_bytes(instruction, {}) > max_utf8_bytes && instruction.size() > 32)
        instruction.chop(std::min<qsizetype>(64, instruction.size() - 32));

    QStringList newest_first;
    for (qsizetype i = messages.size() - 1; i >= 0; --i) {
        const QJsonObject message = messages.at(i).toObject();
        const QString role = message.value(QStringLiteral("role")).toString().trimmed().toLower();
        if (role.isEmpty() || role == QLatin1String("system"))
            continue;
        const QString content = detail::summary_content(message.value(QStringLiteral("content")));
        if (content.isEmpty())
            continue;
        const QString prefix = QLatin1Char('[') + role + QStringLiteral("] ");
        const QString line = prefix + content;

        QStringList candidate = newest_first;
        candidate.append(line);
        std::reverse(candidate.begin(), candidate.end());
        if (detail::summary_prompt_bytes(instruction, candidate) <= max_utf8_bytes) {
            newest_first.append(line);
            continue;
        }

        int low = 0;
        int high = content.size();
        int best = 0;
        while (low <= high) {
            const int mid = low + (high - low) / 2;
            QStringList partial = newest_first;
            partial.append(prefix + content.left(mid) + QStringLiteral("…"));
            std::reverse(partial.begin(), partial.end());
            if (detail::summary_prompt_bytes(instruction, partial) <= max_utf8_bytes) {
                best = mid;
                low = mid + 1;
            } else {
                high = mid - 1;
            }
        }
        if (best > 0)
            newest_first.append(prefix + content.left(best) + QStringLiteral("…"));
        break;
    }

    std::reverse(newest_first.begin(), newest_first.end());
    return detail::summary_prompt(instruction, newest_first);
}

} // namespace fincept::ai_chat
