#include "storage/repositories/ChatRepository.h"
#include "storage/sqlite/Database.h"

#include <QTemporaryDir>
#include <QtTest>

namespace fincept {
void register_migration_v002();
void register_migration_v013();
} // namespace fincept

using namespace fincept;

class TestChatRepository : public QObject {
    Q_OBJECT
  private slots:
    void initTestCase();
    void stable_message_id_is_inserted_once();
    void token_usage_round_trips_as_64_bit();
    void add_message_rolls_back_when_session_update_fails();
    void add_message_once_releases_failed_commit();
    void cleanupTestCase();

  private:
    QTemporaryDir dir_;
};

void TestChatRepository::initTestCase() {
    QVERIFY(dir_.isValid());
    register_migration_v002();
    register_migration_v013();
    const auto opened = Database::instance().open(dir_.filePath(QStringLiteral("chat.db")));
    QVERIFY2(opened.is_ok(), opened.is_ok() ? "" : opened.error().c_str());
}

void TestChatRepository::stable_message_id_is_inserted_once() {
    auto& repo = ChatRepository::instance();
    const auto created = repo.create_session(QStringLiteral("Local research"), QStringLiteral("deepseek"),
                                             QStringLiteral("deepseek-flash"));
    QVERIFY(created.is_ok());

    const QString message_id = QStringLiteral("run-42:user");
    const auto first = repo.add_message_once(message_id, created.value().id, QStringLiteral("user"),
                                             QStringLiteral("Analyze downside risk"));
    const auto duplicate = repo.add_message_once(message_id, created.value().id, QStringLiteral("user"),
                                                 QStringLiteral("Analyze downside risk"));
    QVERIFY(first.is_ok());
    QVERIFY(first.value());
    QVERIFY(duplicate.is_ok());
    QVERIFY(!duplicate.value());

    const auto messages = repo.get_messages(created.value().id);
    QVERIFY(messages.is_ok());
    QCOMPARE(messages.value().size(), 1);
    const auto session = repo.get_session(created.value().id);
    QVERIFY(session.is_ok());
    QCOMPARE(session.value().message_count, 1);
}

void TestChatRepository::token_usage_round_trips_as_64_bit() {
    auto& repo = ChatRepository::instance();
    const auto created = repo.create_session(QStringLiteral("Long-running usage"));
    QVERIFY(created.is_ok());

    constexpr qint64 large_usage = 3'000'000'000LL;
    const auto saved = repo.add_message(created.value().id, QStringLiteral("assistant"),
                                        QStringLiteral("Persist the complete token count"),
                                        QStringLiteral("deepseek"), QStringLiteral("deepseek-chat"), large_usage);
    QVERIFY(saved.is_ok());

    const auto messages = repo.get_messages(created.value().id);
    QVERIFY(messages.is_ok());
    QCOMPARE(messages.value().size(), 1);
    QCOMPARE(messages.value().constFirst().tokens_used, large_usage);
}

void TestChatRepository::add_message_rolls_back_when_session_update_fails() {
    auto& repo = ChatRepository::instance();
    const auto created = repo.create_session(QStringLiteral("Atomic history"), QStringLiteral("deepseek"),
                                             QStringLiteral("deepseek-chat"));
    QVERIFY(created.is_ok());

    auto trigger = Database::instance().execute(
        "CREATE TRIGGER fail_chat_session_update BEFORE UPDATE ON chat_sessions "
        "BEGIN SELECT RAISE(ABORT, 'forced session update failure'); END");
    QVERIFY(trigger.is_ok());

    const auto saved = repo.add_message(created.value().id, QStringLiteral("assistant"),
                                        QStringLiteral("This row must roll back"), QStringLiteral("deepseek"),
                                        QStringLiteral("deepseek-chat"), 12);
    QVERIFY(saved.is_err());

    const auto messages = repo.get_messages(created.value().id);
    QVERIFY(messages.is_ok());
    QCOMPARE(messages.value().size(), 0);

    const auto dropped = Database::instance().execute("DROP TRIGGER fail_chat_session_update");
    QVERIFY(dropped.is_ok());
}

void TestChatRepository::add_message_once_releases_failed_commit() {
    auto& database = Database::instance();
    auto& repo = ChatRepository::instance();
    const auto created = repo.create_session(QStringLiteral("Commit recovery"));
    QVERIFY(created.is_ok());

    QVERIFY(database.execute("CREATE TABLE deferred_parent (id TEXT PRIMARY KEY)").is_ok());
    QVERIFY(database.execute(
                         "CREATE TABLE deferred_child (parent_id TEXT, "
                         "FOREIGN KEY(parent_id) REFERENCES deferred_parent(id) DEFERRABLE INITIALLY DEFERRED)")
                .is_ok());
    QVERIFY(database.execute(
                         "CREATE TRIGGER fail_chat_message_commit AFTER INSERT ON chat_messages "
                         "BEGIN INSERT INTO deferred_child(parent_id) VALUES ('missing'); END")
                .is_ok());

    const auto saved = repo.add_message_once(QStringLiteral("commit-failure"), created.value().id,
                                             QStringLiteral("assistant"), QStringLiteral("Not committed"));
    QVERIFY(saved.is_err());

    const auto next = database.begin_transaction();
    database.rollback();
    QVERIFY2(next.is_ok(), next.is_ok() ? "" : next.error().c_str());

    QVERIFY(database.execute("DROP TRIGGER fail_chat_message_commit").is_ok());
    QVERIFY(database.execute("DROP TABLE deferred_child").is_ok());
    QVERIFY(database.execute("DROP TABLE deferred_parent").is_ok());
}

void TestChatRepository::cleanupTestCase() {
    Database::instance().close();
}

QTEST_GUILESS_MAIN(TestChatRepository)
#include "tst_chat_repository.moc"
