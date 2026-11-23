#include "storage/repositories/AgentConfigSecretPolicy.h"
#include "storage/sqlite/migrations/MigrationRunner.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QSqlDatabase>
#include <QSqlQuery>
#include <QUuid>
#include <QtTest>

#include <algorithm>

class TestAgentConfigSecretPolicy : public QObject {
    Q_OBJECT

  private slots:
    void recursively_removes_credentials_and_runtime_capabilities() {
        const QString raw = QStringLiteral(
            R"({"model":{"provider":"deepseek","api_key":"model-secret","max_tokens":2048},)"
            R"("api_keys":{"DEEPSEEK_API_KEY":"container-secret"},)"
            R"("nested":[{"authorization":"Bearer auth-secret","temperature":0.2},)"
            R"({"OPENAI_API_KEY":"provider-secret","token_budget":4000},)"
            R"({"credentials":{"client_secret":"oauth-secret","client_id":"safe-id"}}],)"
            R"("terminal_mcp_endpoint":"http://127.0.0.1:9999",)"
            R"("terminal_mcp_token":"bridge-secret","terminal_tools":[{"name":"quote"}],)"
            R"("max_tokens":4096})");

        const QString sanitized = fincept::agent_config_secret_policy::sanitize_json(raw);
        const QJsonObject obj = QJsonDocument::fromJson(sanitized.toUtf8()).object();

        QCOMPARE(obj.value("model").toObject().value("provider").toString(), QStringLiteral("deepseek"));
        QCOMPARE(obj.value("model").toObject().value("max_tokens").toInt(), 2048);
        QVERIFY(!obj.contains("api_keys"));
        QCOMPARE(obj.value("nested").toArray().at(0).toObject().value("temperature").toDouble(), 0.2);
        QCOMPARE(obj.value("nested").toArray().at(1).toObject().value("token_budget").toInt(), 4000);
        QCOMPARE(obj.value("nested").toArray().at(2).toObject().value("credentials").toObject().value("client_id").toString(),
                 QStringLiteral("safe-id"));
        QVERIFY(!obj.contains("terminal_mcp_endpoint"));
        QVERIFY(!obj.contains("terminal_mcp_token"));
        QVERIFY(!obj.contains("terminal_tools"));
        QCOMPARE(obj.value("max_tokens").toInt(), 4096);
        for (const QByteArray secret : {"model-secret", "container-secret", "auth-secret", "provider-secret",
                                        "oauth-secret", "bridge-secret"})
            QVERIFY2(!sanitized.toUtf8().contains(secret), secret.constData());
    }

    void invalid_or_non_object_json_is_preserved_for_validation() {
        QCOMPARE(fincept::agent_config_secret_policy::sanitize_json(QStringLiteral("not-json")),
                 QStringLiteral("not-json"));
        QCOMPARE(fincept::agent_config_secret_policy::sanitize_json(QStringLiteral("[1,2,3]")),
                 QStringLiteral("[1,2,3]"));
    }

    void migration_scrubs_credentials_already_stored_in_plain_sqlite() {
        const QString connection = QStringLiteral("agent-config-secret-%1").arg(QUuid::createUuid().toString());
        {
            QSqlDatabase db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), connection);
            db.setDatabaseName(QStringLiteral(":memory:"));
            QVERIFY(db.open());
            QSqlQuery query(db);
            QVERIFY(query.exec(QStringLiteral(
                "CREATE TABLE agent_configs (id TEXT PRIMARY KEY, config_json TEXT NOT NULL)")));
            query.prepare(QStringLiteral("INSERT INTO agent_configs(id, config_json) VALUES(?, ?)"));
            query.addBindValue(QStringLiteral("legacy"));
            query.addBindValue(QStringLiteral(
                R"({"model":{"provider":"deepseek","api_key":"legacy-secret"},"max_tokens":99})"));
            QVERIFY(query.exec());

            fincept::register_migration_v067();
            const auto migrations = fincept::MigrationRunner::all_migrations();
            const auto it = std::find_if(migrations.cbegin(), migrations.cend(),
                                         [](const auto& migration) { return migration.version == 67; });
            QVERIFY(it != migrations.cend());
            const auto applied = it->apply(db);
            QVERIFY2(applied.is_ok(), applied.is_err() ? applied.error().c_str() : "");
            const auto applied_again = it->apply(db);
            QVERIFY2(applied_again.is_ok(), applied_again.is_err() ? applied_again.error().c_str() : "");

            QVERIFY(query.exec(QStringLiteral("SELECT config_json FROM agent_configs WHERE id = 'legacy'")));
            QVERIFY(query.next());
            const QString stored = query.value(0).toString();
            QVERIFY(!stored.contains(QStringLiteral("legacy-secret")));
            QCOMPARE(QJsonDocument::fromJson(stored.toUtf8()).object().value("max_tokens").toInt(), 99);
        }
        QSqlDatabase::removeDatabase(connection);
    }
};

QTEST_GUILESS_MAIN(TestAgentConfigSecretPolicy)
#include "tst_agent_config_secret_policy.moc"
