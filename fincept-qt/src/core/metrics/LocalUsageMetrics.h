#pragma once

#include <QHash>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMutex>
#include <QMutexLocker>
#include <QSaveFile>
#include <QString>

namespace fincept::metrics {

struct LocalUsageSnapshot {
    qint64 llm_requests = 0;
    qint64 successful_llm_requests = 0;
    qint64 prompt_tokens = 0;
    qint64 completion_tokens = 0;
    qint64 local_quantlib_calls = 0;
    qint64 public_data_calls = 0;
    qint64 fincept_credits_used = 0;
    QHash<QString, qint64> provider_tokens;
    QHash<QString, qint64> source_calls;
    QString monthly_period;
    qint64 monthly_llm_requests = 0;
    qint64 monthly_successful_llm_requests = 0;
    qint64 monthly_local_quantlib_calls = 0;
    qint64 monthly_public_data_calls = 0;
    QHash<QString, qint64> monthly_provider_tokens;
    QHash<QString, QString> provider_last_success;
    QHash<QString, QString> source_last_success;
};

/// Thread-safe, process-local accounting for BYO/public/local routes. Provider
/// tokens are measured, never converted into an invented invoice estimate.
class LocalUsageMetrics {
  public:
    static void set_storage_path(const QString& path) {
        QMutexLocker lock(&mutex());
        storage_path() = path;
        data() = load_locked();
        roll_month_locked();
    }

    static void record_llm(QString provider, qint64 prompt_tokens, qint64 completion_tokens, bool success) {
        provider = provider.trimmed().toLower();
        if (provider.isEmpty())
            provider = QStringLiteral("unknown");
        const qint64 input = qMax<qint64>(0, prompt_tokens);
        const qint64 output = qMax<qint64>(0, completion_tokens);
        QMutexLocker lock(&mutex());
        auto& value = data();
        roll_month_locked();
        ++value.llm_requests;
        value.successful_llm_requests += success ? 1 : 0;
        value.prompt_tokens += input;
        value.completion_tokens += output;
        value.provider_tokens[provider] += input + output;
        ++value.monthly_llm_requests;
        value.monthly_successful_llm_requests += success ? 1 : 0;
        value.monthly_provider_tokens[provider] += input + output;
        if (success)
            value.provider_last_success[provider] = now_iso();
        save_locked();
    }

    static void record_local_quantlib() {
        QMutexLocker lock(&mutex());
        roll_month_locked();
        ++data().local_quantlib_calls;
        ++data().monthly_local_quantlib_calls;
        data().source_last_success[QStringLiteral("quantlib")] = now_iso();
        save_locked();
    }

    static void record_public_data(QString source) {
        source = source.trimmed().toLower();
        if (source.isEmpty())
            source = QStringLiteral("public");
        QMutexLocker lock(&mutex());
        roll_month_locked();
        ++data().public_data_calls;
        ++data().source_calls[source];
        ++data().monthly_public_data_calls;
        data().source_last_success[source] = now_iso();
        save_locked();
    }

    static LocalUsageSnapshot snapshot() {
        QMutexLocker lock(&mutex());
        roll_month_locked();
        return data();
    }

    static void reset_for_test() {
        QMutexLocker lock(&mutex());
        data() = {};
        if (!storage_path().isEmpty())
            QFile::remove(storage_path());
    }

    static void reload_for_test() {
        QMutexLocker lock(&mutex());
        data() = load_locked();
        roll_month_locked();
    }

  private:
    static QMutex& mutex() {
        static QMutex value;
        return value;
    }

    static LocalUsageSnapshot& data() {
        static LocalUsageSnapshot value;
        return value;
    }

    static QString& storage_path() {
        static QString value;
        return value;
    }

    static QString current_month() {
        return QDate::currentDate().toString(QStringLiteral("yyyy-MM"));
    }

    static QString now_iso() {
        return QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    }

    static QJsonObject integer_map(const QHash<QString, qint64>& values) {
        QJsonObject object;
        for (auto it = values.cbegin(); it != values.cend(); ++it)
            object.insert(it.key(), double(it.value()));
        return object;
    }

    static QHash<QString, qint64> read_integer_map(const QJsonObject& object) {
        QHash<QString, qint64> values;
        for (auto it = object.constBegin(); it != object.constEnd(); ++it)
            values.insert(it.key(), qint64(it.value().toDouble()));
        return values;
    }

    static QJsonObject string_map(const QHash<QString, QString>& values) {
        QJsonObject object;
        for (auto it = values.cbegin(); it != values.cend(); ++it)
            object.insert(it.key(), it.value());
        return object;
    }

    static QHash<QString, QString> read_string_map(const QJsonObject& object) {
        QHash<QString, QString> values;
        for (auto it = object.constBegin(); it != object.constEnd(); ++it)
            values.insert(it.key(), it.value().toString());
        return values;
    }

    static void roll_month_locked() {
        auto& value = data();
        const QString month = current_month();
        if (value.monthly_period == month)
            return;
        value.monthly_period = month;
        value.monthly_llm_requests = 0;
        value.monthly_successful_llm_requests = 0;
        value.monthly_local_quantlib_calls = 0;
        value.monthly_public_data_calls = 0;
        value.monthly_provider_tokens.clear();
    }

    static LocalUsageSnapshot load_locked() {
        LocalUsageSnapshot value;
        QFile file(storage_path());
        if (storage_path().isEmpty() || !file.open(QIODevice::ReadOnly))
            return value;
        const QJsonObject root = QJsonDocument::fromJson(file.readAll()).object();
        value.llm_requests = qint64(root.value(QStringLiteral("llm_requests")).toDouble());
        value.successful_llm_requests = qint64(root.value(QStringLiteral("successful_llm_requests")).toDouble());
        value.prompt_tokens = qint64(root.value(QStringLiteral("prompt_tokens")).toDouble());
        value.completion_tokens = qint64(root.value(QStringLiteral("completion_tokens")).toDouble());
        value.local_quantlib_calls = qint64(root.value(QStringLiteral("local_quantlib_calls")).toDouble());
        value.public_data_calls = qint64(root.value(QStringLiteral("public_data_calls")).toDouble());
        value.provider_tokens = read_integer_map(root.value(QStringLiteral("provider_tokens")).toObject());
        value.source_calls = read_integer_map(root.value(QStringLiteral("source_calls")).toObject());
        value.monthly_period = root.value(QStringLiteral("monthly_period")).toString();
        value.monthly_llm_requests = qint64(root.value(QStringLiteral("monthly_llm_requests")).toDouble());
        value.monthly_successful_llm_requests =
            qint64(root.value(QStringLiteral("monthly_successful_llm_requests")).toDouble());
        value.monthly_local_quantlib_calls =
            qint64(root.value(QStringLiteral("monthly_local_quantlib_calls")).toDouble());
        value.monthly_public_data_calls =
            qint64(root.value(QStringLiteral("monthly_public_data_calls")).toDouble());
        value.monthly_provider_tokens =
            read_integer_map(root.value(QStringLiteral("monthly_provider_tokens")).toObject());
        value.provider_last_success =
            read_string_map(root.value(QStringLiteral("provider_last_success")).toObject());
        value.source_last_success = read_string_map(root.value(QStringLiteral("source_last_success")).toObject());
        return value;
    }

    static void save_locked() {
        if (storage_path().isEmpty())
            return;
        QDir().mkpath(QFileInfo(storage_path()).absolutePath());
        const auto& value = data();
        QJsonObject root{{QStringLiteral("version"), 1},
                         {QStringLiteral("llm_requests"), double(value.llm_requests)},
                         {QStringLiteral("successful_llm_requests"), double(value.successful_llm_requests)},
                         {QStringLiteral("prompt_tokens"), double(value.prompt_tokens)},
                         {QStringLiteral("completion_tokens"), double(value.completion_tokens)},
                         {QStringLiteral("local_quantlib_calls"), double(value.local_quantlib_calls)},
                         {QStringLiteral("public_data_calls"), double(value.public_data_calls)},
                         {QStringLiteral("provider_tokens"), integer_map(value.provider_tokens)},
                         {QStringLiteral("source_calls"), integer_map(value.source_calls)},
                         {QStringLiteral("monthly_period"), value.monthly_period},
                         {QStringLiteral("monthly_llm_requests"), double(value.monthly_llm_requests)},
                         {QStringLiteral("monthly_successful_llm_requests"),
                          double(value.monthly_successful_llm_requests)},
                         {QStringLiteral("monthly_local_quantlib_calls"),
                          double(value.monthly_local_quantlib_calls)},
                         {QStringLiteral("monthly_public_data_calls"), double(value.monthly_public_data_calls)},
                         {QStringLiteral("monthly_provider_tokens"), integer_map(value.monthly_provider_tokens)},
                         {QStringLiteral("provider_last_success"), string_map(value.provider_last_success)},
                         {QStringLiteral("source_last_success"), string_map(value.source_last_success)}};
        QSaveFile file(storage_path());
        if (file.open(QIODevice::WriteOnly)) {
            file.write(QJsonDocument(root).toJson(QJsonDocument::Compact));
            file.commit();
        }
    }
};

} // namespace fincept::metrics
