#include "storage/repositories/ChatRepository.h"

#include <QUuid>

namespace fincept {

ChatRepository& ChatRepository::instance() {
    static ChatRepository s;
    return s;
}

ChatSession ChatRepository::map_session(QSqlQuery& q) {
    return {q.value(0).toString(), q.value(1).toString(), q.value(2).toString(), q.value(3).toString(),
            q.value(4).toInt(),    q.value(5).toString(), q.value(6).toString()};
}

ChatMessage ChatRepository::map_message(QSqlQuery& q) {
    return {q.value(0).toString(), q.value(1).toString(), q.value(2).toString(), q.value(3).toString(),
            q.value(4).toString(), q.value(5).toString(), q.value(6).toInt(),    q.value(7).toString()};
}

Result<ChatSession> ChatRepository::create_session(const QString& title, const QString& provider,
                                                   const QString& model) {
    QString id = QUuid::createUuid().toString(QUuid::WithoutBraces);
    auto r = exec_write("INSERT INTO chat_sessions (id, title, provider, model) VALUES (?, ?, ?, ?)",
                        {id, title, provider, model});
    if (r.is_err())
        return Result<ChatSession>::err(r.error());
    return get_session(id);
}

Result<ChatSession> ChatRepository::get_session(const QString& id) {
    return query_one("SELECT id, title, provider, model, message_count, created_at, updated_at "
                     "FROM chat_sessions WHERE id = ?",
                     {id}, map_session);
}

Result<QVector<ChatSession>> ChatRepository::list_sessions() {
    // Old sessions stored only the route selected when the session was created.
    // Derive the visible route from the newest assistant answer so existing
    // conversations migrate without rewriting or deleting their history.
    return query_list("SELECT s.id, s.title, "
                      "COALESCE((SELECT m.provider FROM chat_messages m "
                      "          WHERE m.session_id = s.id AND m.role = 'assistant' AND TRIM(m.provider) <> '' "
                      "          ORDER BY m.timestamp DESC, m.rowid DESC LIMIT 1), s.provider), "
                      "COALESCE((SELECT m.model FROM chat_messages m "
                      "          WHERE m.session_id = s.id AND m.role = 'assistant' AND TRIM(m.provider) <> '' "
                      "          ORDER BY m.timestamp DESC, m.rowid DESC LIMIT 1), s.model), "
                      "s.message_count, s.created_at, s.updated_at "
                      "FROM chat_sessions s ORDER BY s.updated_at DESC",
                      {}, map_session);
}

Result<void> ChatRepository::update_session_title(const QString& id, const QString& title) {
    return exec_write("UPDATE chat_sessions SET title = ?, updated_at = datetime('now') WHERE id = ?", {title, id});
}

Result<void> ChatRepository::delete_session(const QString& id) {
    return exec_write("DELETE FROM chat_sessions WHERE id = ?", {id});
}

Result<void> ChatRepository::add_message(const QString& session_id, const QString& role, const QString& content,
                                         const QString& provider, const QString& model, int tokens) {
    auto tx = db().begin_transaction();
    if (tx.is_err())
        return tx;

    QString id = QUuid::createUuid().toString(QUuid::WithoutBraces);
    auto r = exec_write("INSERT INTO chat_messages (id, session_id, role, content, provider, model, tokens_used) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        {id, session_id, role, content, provider, model, tokens});
    if (r.is_err()) {
        db().rollback();
        return r;
    }

    // Keep the session route aligned with the answer that users actually see.
    // User/system rows must not overwrite it with the currently selected route.
    r = role == QLatin1String("assistant") && !provider.trimmed().isEmpty()
            ? exec_write("UPDATE chat_sessions SET message_count = message_count + 1, "
                         "updated_at = datetime('now'), provider = ?, model = ? WHERE id = ?",
                         {provider, model, session_id})
            : exec_write("UPDATE chat_sessions SET message_count = message_count + 1, "
                         "updated_at = datetime('now') WHERE id = ?",
                         {session_id});
    if (r.is_err()) {
        db().rollback();
        return r;
    }

    const auto committed = db().commit();
    if (committed.is_err()) {
        db().rollback();
        return committed;
    }
    return Result<void>::ok();
}

Result<bool> ChatRepository::add_message_once(const QString& message_id, const QString& session_id,
                                              const QString& role, const QString& content, const QString& provider,
                                              const QString& model, int tokens) {
    auto tx = db().begin_transaction();
    if (tx.is_err())
        return Result<bool>::err(tx.error());

    auto inserted = db().execute(
        "INSERT OR IGNORE INTO chat_messages (id, session_id, role, content, provider, model, tokens_used) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        {message_id, session_id, role, content, provider, model, tokens});
    if (inserted.is_err()) {
        db().rollback();
        return Result<bool>::err(inserted.error());
    }
    const bool added = inserted.value().numRowsAffected() > 0;
    if (added) {
        Result<QSqlQuery> updated =
            role == QLatin1String("assistant") && !provider.trimmed().isEmpty()
                ? db().execute("UPDATE chat_sessions SET message_count = message_count + 1, "
                               "updated_at = datetime('now'), provider = ?, model = ? WHERE id = ?",
                               {provider, model, session_id})
                : db().execute("UPDATE chat_sessions SET message_count = message_count + 1, "
                               "updated_at = datetime('now') WHERE id = ?",
                               {session_id});
        if (updated.is_err()) {
            db().rollback();
            return Result<bool>::err(updated.error());
        }
    }
    auto committed = db().commit();
    if (committed.is_err())
        return Result<bool>::err(committed.error());
    return Result<bool>::ok(added);
}

Result<QVector<ChatMessage>> ChatRepository::get_messages(const QString& session_id) {
    auto r = db().execute("SELECT id, session_id, role, content, provider, model, tokens_used, timestamp "
                          "FROM chat_messages WHERE session_id = ? ORDER BY timestamp ASC, rowid ASC",
                          {session_id});
    if (r.is_err())
        return Result<QVector<ChatMessage>>::err(r.error());
    QVector<ChatMessage> result;
    auto& q = r.value();
    while (q.next())
        result.append(map_message(q));
    return Result<QVector<ChatMessage>>::ok(std::move(result));
}

Result<void> ChatRepository::delete_messages(const QString& session_id) {
    return exec_write("DELETE FROM chat_messages WHERE session_id = ?", {session_id});
}

} // namespace fincept
