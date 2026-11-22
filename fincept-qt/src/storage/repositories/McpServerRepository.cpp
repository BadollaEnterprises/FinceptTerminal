#include "storage/repositories/McpServerRepository.h"

#include "storage/secure/SecureStorage.h"

namespace fincept {

namespace {
// The `env` blob holds an MCP server's environment variables, which routinely
// include API tokens. Encrypt it at rest in SecureStorage; reads fall back to
// the plaintext column so legacy rows keep working and nothing is lost.
QString mcp_env_secret_key(const QString& id) {
    return QStringLiteral("mcp:env:") + id;
}
} // namespace

McpServerRepository& McpServerRepository::instance() {
    static McpServerRepository s;
    return s;
}

McpServer McpServerRepository::map_row(QSqlQuery& q) {
    McpServer s{q.value(0).toString(), q.value(1).toString(), q.value(2).toString(),  q.value(3).toString(),
                q.value(4).toString(), q.value(5).toString(), q.value(6).toString(),  q.value(7).toString(),
                q.value(8).toBool(),   q.value(9).toBool(),   q.value(10).toString(), q.value(11).toString(),
                q.value(12).toString()};
    if (s.env.isEmpty() && !s.id.isEmpty()) {
        // Error ≠ "nothing stored": env stays empty for this load only; save() stores
        // the env only when one is given, so an unread env is never overwritten.
        const SecretRead sr = SecureStorage::instance().read(mcp_env_secret_key(s.id));
        if (sr.found())
            s.env = sr.value;
    }
    return s;
}

static const char* kCols = "id, name, description, command, args, env, category, icon, enabled, "
                           "auto_start, status, created_at, updated_at";

Result<QVector<McpServer>> McpServerRepository::list_all() {
    return query_list(QString("SELECT %1 FROM mcp_servers ORDER BY name").arg(kCols), {}, map_row);
}

Result<McpServer> McpServerRepository::get(const QString& id) {
    return query_one(QString("SELECT %1 FROM mcp_servers WHERE id = ?").arg(kCols), {id}, map_row);
}

Result<void> McpServerRepository::save(const McpServer& s) {
    // Save the recoverable row first, then move its environment blob into
    // SecureStorage and scrub the column only after verification.
    // REPLACE would delete the server row first and cascade-delete every
    // discovered mcp_tools row. Update the existing row in place instead.
    auto saved = exec_write(
        "INSERT INTO mcp_servers "
        "(id, name, description, command, args, env, category, icon, enabled, auto_start, status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET "
        "name = excluded.name, description = excluded.description, command = excluded.command, "
        "args = excluded.args, env = excluded.env, category = excluded.category, icon = excluded.icon, "
        "enabled = excluded.enabled, auto_start = excluded.auto_start, status = excluded.status, "
        "updated_at = datetime('now')",
        {s.id, s.name, s.description, s.command, s.args, s.env, s.category, s.icon, s.enabled ? 1 : 0,
         s.auto_start ? 1 : 0, s.status});
    if (saved.is_err())
        return saved;
    if (!s.env.isEmpty() && SecureStorage::instance().store_verified(mcp_env_secret_key(s.id), s.env).is_ok())
        (void)exec_write("UPDATE mcp_servers SET env = '' WHERE id = ?", {s.id});
    return saved;
}

Result<void> McpServerRepository::remove(const QString& id) {
    auto deleted = exec_write("DELETE FROM mcp_servers WHERE id = ?", {id});
    if (deleted.is_err())
        return deleted;
    (void)SecureStorage::instance().remove(mcp_env_secret_key(id));
    return deleted;
}

Result<void> McpServerRepository::set_status(const QString& id, const QString& status) {
    return exec_write("UPDATE mcp_servers SET status = ?, updated_at = datetime('now') WHERE id = ?", {status, id});
}

Result<void> McpServerRepository::set_enabled(const QString& id, bool enabled) {
    return exec_write("UPDATE mcp_servers SET enabled = ?, updated_at = datetime('now') WHERE id = ?",
                      {enabled ? 1 : 0, id});
}

} // namespace fincept
