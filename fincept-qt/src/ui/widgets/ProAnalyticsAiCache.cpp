#include "ui/widgets/ProAnalyticsAiCache.h"

#include <QCryptographicHash>
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>

#include <algorithm>

namespace fincept::ui::pro_ai {
namespace {

constexpr int kCacheFormatVersion = 1;
constexpr qint64 kMaxCacheFileBytes = 512 * 1024;

bool valid_key(const QString& key) {
    if (key.size() != 64)
        return false;
    for (const QChar c : key) {
        if (!c.isDigit() && (c < QLatin1Char('a') || c > QLatin1Char('f')))
            return false;
    }
    return true;
}

QString cache_path(const QString& directory, const QString& key) {
    return QDir(directory).filePath(key + QStringLiteral(".json"));
}

std::optional<CachedExplanation> parse_entry(const QByteArray& bytes) {
    if (bytes.isEmpty() || bytes.size() > kMaxCacheFileBytes)
        return std::nullopt;
    QJsonParseError error;
    const QJsonDocument document = QJsonDocument::fromJson(bytes, &error);
    if (error.error != QJsonParseError::NoError || !document.isObject())
        return std::nullopt;
    const QJsonObject object = document.object();
    if (object.value(QStringLiteral("version")).toInt() != kCacheFormatVersion)
        return std::nullopt;
    CachedExplanation entry;
    entry.text = object.value(QStringLiteral("text")).toString().trimmed();
    entry.route_label = object.value(QStringLiteral("route_label")).toString();
    entry.billing_note = object.value(QStringLiteral("billing_note")).toString();
    entry.created_at_ms = object.value(QStringLiteral("created_at_ms")).toVariant().toLongLong();
    if (entry.text.isEmpty() || entry.created_at_ms <= 0)
        return std::nullopt;
    return entry;
}

std::optional<CachedExplanation> read_entry(const QString& path) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly) || file.size() > kMaxCacheFileBytes)
        return std::nullopt;
    return parse_entry(file.readAll());
}

void prune_cache(const QString& directory, int max_entries) {
    QDir dir(directory);
    const QStringList files = dir.entryList({QStringLiteral("*.json")}, QDir::Files, QDir::Name);
    struct Item {
        QString path;
        qint64 created_at_ms = 0;
    };
    QVector<Item> items;
    items.reserve(files.size());
    for (const QString& file_name : files) {
        const QString path = dir.filePath(file_name);
        const auto entry = read_entry(path);
        if (!entry) {
            QFile::remove(path);
            continue;
        }
        items.push_back({path, entry->created_at_ms});
    }
    std::sort(items.begin(), items.end(), [](const Item& a, const Item& b) {
        if (a.created_at_ms != b.created_at_ms)
            return a.created_at_ms < b.created_at_ms;
        return a.path < b.path;
    });
    const int remove_count = std::max(0, static_cast<int>(items.size()) - std::max(1, max_entries));
    for (int i = 0; i < remove_count; ++i)
        QFile::remove(items.at(i).path);
}

} // namespace

QString explanation_cache_key(const QString& prompt, const QString& provider, const QString& model,
                              int prompt_version) {
    QByteArray material = QByteArray::number(prompt_version);
    material += '\0';
    material += provider.trimmed().toLower().toUtf8();
    material += '\0';
    material += model.trimmed().toUtf8();
    material += '\0';
    material += prompt.toUtf8();
    return QString::fromLatin1(QCryptographicHash::hash(material, QCryptographicHash::Sha256).toHex());
}

std::optional<CachedExplanation> load_explanation_cache(const QString& directory, const QString& key,
                                                        qint64 now_ms, qint64 max_age_ms) {
    if (!valid_key(key) || now_ms <= 0 || max_age_ms <= 0)
        return std::nullopt;
    const QString path = cache_path(directory, key);
    const auto entry = read_entry(path);
    if (!entry) {
        QFile::remove(path);
        return std::nullopt;
    }
    // A clock jump far into the future is not a valid way to keep a cache entry
    // forever. Five minutes allows ordinary clock adjustment around a write.
    constexpr qint64 kFutureToleranceMs = 5 * 60 * 1000;
    if (entry->created_at_ms > now_ms + kFutureToleranceMs || now_ms - entry->created_at_ms > max_age_ms) {
        QFile::remove(path);
        return std::nullopt;
    }
    return entry;
}

bool store_explanation_cache(const QString& directory, const QString& key, const CachedExplanation& entry,
                             int max_entries) {
    if (!valid_key(key) || entry.text.trimmed().isEmpty() || entry.created_at_ms <= 0 || max_entries <= 0)
        return false;
    if (!QDir().mkpath(directory))
        return false;
    const QJsonObject object{{QStringLiteral("version"), kCacheFormatVersion},
                             {QStringLiteral("text"), entry.text.trimmed()},
                             {QStringLiteral("route_label"), entry.route_label},
                             {QStringLiteral("billing_note"), entry.billing_note},
                             {QStringLiteral("created_at_ms"), QString::number(entry.created_at_ms)}};
    const QByteArray bytes = QJsonDocument(object).toJson(QJsonDocument::Compact);
    if (bytes.size() > kMaxCacheFileBytes)
        return false;
    QSaveFile file(cache_path(directory, key));
    if (!file.open(QIODevice::WriteOnly) || file.write(bytes) != bytes.size() || !file.commit())
        return false;
    prune_cache(directory, max_entries);
    return true;
}

} // namespace fincept::ui::pro_ai
