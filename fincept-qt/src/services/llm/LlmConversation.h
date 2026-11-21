#pragma once

#include <QString>

namespace fincept::ai_chat {

struct ConversationMessage {
    QString role; // system | user | assistant
    QString content;
};

} // namespace fincept::ai_chat
