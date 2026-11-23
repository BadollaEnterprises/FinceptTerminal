// v067_agent_config_secrets — remove credentials and run-scoped terminal
// capabilities from agent_configs.config_json.
//
// Agent configs live in plain SQLite and can be cloud-synced. Older builds
// snapshotted model.api_key, and direct repository writers could also store
// nested API keys or bridge tokens. Provider credentials are resolved from the
// encrypted LLM profile store for every run, so none of those values belongs in
// durable agent policy.

#include "storage/repositories/AgentConfigSecretPolicy.h"
#include "storage/sqlite/migrations/MigrationRunner.h"

#include <QSqlError>
#include <QSqlQuery>
#include <QVariant>

namespace fincept {

void register_migration_v067();

namespace {

Result<void> apply_v067(QSqlDatabase& db) {
    QSqlQuery select(db);
    if (!select.exec(QStringLiteral("SELECT id, config_json FROM agent_configs")))
        return Result<void>::err(
            QStringLiteral("v067: read agent configs — %1").arg(select.lastError().text()).toStdString());

    QVector<QPair<QString, QString>> updates;
    while (select.next()) {
        const QString id = select.value(0).toString();
        const QString raw = select.value(1).toString();
        const QString sanitized = agent_config_secret_policy::sanitize_json(raw);
        if (sanitized != raw)
            updates.append({id, sanitized});
    }

    QSqlQuery update(db);
    update.prepare(QStringLiteral("UPDATE agent_configs SET config_json = ? WHERE id = ?"));
    for (const auto& [id, config] : updates) {
        update.bindValue(0, config);
        update.bindValue(1, id);
        if (!update.exec())
            return Result<void>::err(
                QStringLiteral("v067: scrub agent config — %1").arg(update.lastError().text()).toStdString());
    }
    return Result<void>::ok();
}

} // namespace

void register_migration_v067() {
    static bool done = false;
    if (done)
        return;
    done = true;
    MigrationRunner::register_migration({67, "agent_config_secrets", apply_v067});
}

} // namespace fincept
