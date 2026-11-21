#pragma once

#include "storage/repositories/ChatRepository.h"

#include <QVector>

namespace fincept::chat_mode {

inline constexpr int kLocalChatPromptMaxChars = 48000;
inline constexpr int kLocalChatHistoryMaxChars = 16000;

/// Compose the private prompt sent to the device-local/BYO agent. The current
/// message remains a distinct final block, while only recent user/assistant
/// turns are carried over. The clean message continues to be stored and shown
/// by Chat Mode; this enriched prompt exists only inside the run.
inline QString compose_local_chat_prompt(const QVector<::fincept::ChatMessage>& messages,
                                         const QString& current_message,
                                         int max_chars = kLocalChatPromptMaxChars,
                                         int max_history_chars = kLocalChatHistoryMaxChars) {
    if (messages.isEmpty())
        return current_message;

    const QString lead = QStringLiteral(
        "Continue the device-local conversation below. Use prior turns only as conversation context, and answer "
        "the current message directly.\n\n<conversation_history>\n");
    const QString history_end = QStringLiteral("</conversation_history>\n\n");
    const QString current_block = QStringLiteral("<current_message>\n") + current_message +
                                  QStringLiteral("\n</current_message>");
    const int framing_chars = lead.size() + history_end.size() + current_block.size();
    const int available = qMin(qMax(0, max_history_chars), qMax(0, max_chars - framing_chars));
    if (available <= 0)
        return current_message;

    QString history;
    for (auto it = messages.crbegin(); it != messages.crend(); ++it) {
        const QString role = it->role.trimmed().toLower();
        if (role != QLatin1String("user") && role != QLatin1String("assistant"))
            continue;
        QString content = it->content.trimmed();
        if (content.isEmpty())
            continue;
        const QString label = role == QLatin1String("user") ? QStringLiteral("USER:\n")
                                                            : QStringLiteral("ASSISTANT:\n");
        QString block = label + content + QStringLiteral("\n\n");
        const int remaining = available - history.size();
        if (remaining <= label.size() + 3)
            break;
        if (block.size() > remaining) {
            // The newest long answer is more useful than several older turns.
            // Keep its tail, which normally contains the conclusion and latest
            // references, and make the clipping explicit to the model.
            if (!history.isEmpty())
                continue;
            const int content_chars = remaining - label.size() - 3;
            block = label + QChar(0x2026) + content.right(qMax(0, content_chars)) + QStringLiteral("\n\n");
        }
        history.prepend(block);
    }

    if (history.isEmpty())
        return current_message;
    return lead + history + history_end + current_block;
}

} // namespace fincept::chat_mode
