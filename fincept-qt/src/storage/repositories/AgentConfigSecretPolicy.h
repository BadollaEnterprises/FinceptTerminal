#pragma once

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QString>

namespace fincept::agent_config_secret_policy {

inline QString normalized_key(const QString& key) {
    QString out;
    out.reserve(key.size());
    bool last_was_separator = false;
    for (const QChar ch : key.trimmed().toLower()) {
        if (ch.isLetterOrNumber()) {
            out.append(ch);
            last_was_separator = false;
        } else if (!out.isEmpty() && !last_was_separator) {
            out.append(QLatin1Char('_'));
            last_was_separator = true;
        }
    }
    while (out.endsWith(QLatin1Char('_')))
        out.chop(1);
    return out;
}

inline bool is_sensitive_key(const QString& key) {
    const QString normalized = normalized_key(key);
    return normalized == QLatin1String("api_key") || normalized == QLatin1String("api_keys") ||
           normalized == QLatin1String("authorization") || normalized == QLatin1String("proxy_authorization") ||
           normalized == QLatin1String("password") || normalized == QLatin1String("secret") ||
           normalized == QLatin1String("secret_key") || normalized == QLatin1String("cookie") ||
           normalized == QLatin1String("set_cookie") || normalized == QLatin1String("terminal_mcp_endpoint") ||
           normalized == QLatin1String("terminal_mcp_token") ||
           normalized == QLatin1String("terminal_mcp_destructive_token") ||
           normalized == QLatin1String("terminal_tools") || normalized.endsWith(QLatin1String("_api_key")) ||
           normalized.endsWith(QLatin1String("_api_keys")) || normalized == QLatin1String("token") ||
           normalized.endsWith(QLatin1String("_token")) || normalized.endsWith(QLatin1String("_password")) ||
           normalized.endsWith(QLatin1String("_secret"));
}

inline QJsonValue sanitize_value(const QJsonValue& value) {
    if (value.isObject()) {
        QJsonObject sanitized;
        const QJsonObject object = value.toObject();
        for (auto it = object.constBegin(); it != object.constEnd(); ++it) {
            if (!is_sensitive_key(it.key()))
                sanitized.insert(it.key(), sanitize_value(it.value()));
        }
        return sanitized;
    }
    if (value.isArray()) {
        QJsonArray sanitized;
        const QJsonArray array = value.toArray();
        for (const QJsonValue& child : array)
            sanitized.append(sanitize_value(child));
        return sanitized;
    }
    return value;
}

inline QString sanitize_json(const QString& config_json) {
    QJsonParseError error;
    const QJsonDocument document = QJsonDocument::fromJson(config_json.toUtf8(), &error);
    if (error.error != QJsonParseError::NoError || !document.isObject())
        return config_json;
    const QJsonObject sanitized = sanitize_value(document.object()).toObject();
    if (sanitized == document.object())
        return config_json;
    return QString::fromUtf8(QJsonDocument(sanitized).toJson(QJsonDocument::Compact));
}

} // namespace fincept::agent_config_secret_policy
