#pragma once
#include "storage/repositories/BaseRepository.h"

namespace fincept {

struct ChatSession {
    QString id;
    QString title;
    QString provider;
    QString model;
    int message_count = 0;
    QString created_at;
    QString updated_at;
};

struct ChatMessage {
    QString id;
    QString session_id;
    QString role; // "user", "assistant", "system"
    QString content;
    QString provider;
    QString model;
    int tokens_used = 0;
    QString timestamp;
};

class ChatRepository : public BaseRepository<ChatSession> {
  public:
    static ChatRepository& instance();

    // Sessions
    Result<ChatSession> create_session(const QString& title, const QString& provider = {}, const QString& model = {});
    Result<ChatSession> get_session(const QString& id);
    Result<QVector<ChatSession>> list_sessions();
    Result<void> update_session_title(const QString& id, const QString& title);
    Result<void> delete_session(const QString& id);

    // Messages
    Result<void> add_message(const QString& session_id, const QString& role, const QString& content,
                             const QString& provider = {}, const QString& model = {}, int tokens = 0);
    /// Insert a message with a caller-owned stable id. Returns true when a row
    /// was added and false when that id was already persisted. The session count
    /// is updated in the same transaction only for a new row.
    Result<bool> add_message_once(const QString& message_id, const QString& session_id, const QString& role,
                                  const QString& content, const QString& provider = {}, const QString& model = {},
                                  int tokens = 0);
    Result<QVector<ChatMessage>> get_messages(const QString& session_id);
    Result<void> delete_messages(const QString& session_id);

  private:
    ChatRepository() = default;
    static ChatSession map_session(QSqlQuery& q);
    static ChatMessage map_message(QSqlQuery& q);
};

} // namespace fincept
