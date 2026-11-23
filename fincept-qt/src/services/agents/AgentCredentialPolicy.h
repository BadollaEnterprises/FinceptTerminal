#pragma once

#include <QJsonArray>
#include <QJsonObject>
#include <QSet>
#include <QString>

namespace fincept::agent_credential_policy {

inline QString canonical_provider(QString provider) {
    provider = provider.trimmed().toLower();
    if (provider == QLatin1String("gemini"))
        return QStringLiteral("google");
    if (provider == QLatin1String("claude"))
        return QStringLiteral("anthropic");
    if (provider == QLatin1String("kimi"))
        return QStringLiteral("moonshot");
    return provider;
}

inline void collect_embedder_providers(const QJsonValue& value, QSet<QString>& providers) {
    if (value.isArray()) {
        for (const QJsonValue& item : value.toArray())
            collect_embedder_providers(item, providers);
        return;
    }
    if (!value.isObject())
        return;
    const QJsonObject object = value.toObject();
    for (auto it = object.constBegin(); it != object.constEnd(); ++it) {
        if (it.key().compare(QLatin1String("embedder"), Qt::CaseInsensitive) == 0) {
            QString provider;
            if (it.value().isString())
                provider = it.value().toString();
            else if (it.value().isObject())
                provider = it.value().toObject().value(QStringLiteral("provider")).toString();
            provider = canonical_provider(provider);
            if (!provider.isEmpty())
                providers.insert(provider);
        }
        if (it.key().compare(QLatin1String("tools"), Qt::CaseInsensitive) == 0 && it.value().isArray()) {
            for (const QJsonValue& tool : it.value().toArray()) {
                const QString provider = canonical_provider(tool.toString());
                if (provider == QLatin1String("tavily") || provider == QLatin1String("financial_datasets"))
                    providers.insert(provider);
            }
        }
        collect_embedder_providers(it.value(), providers);
    }
}

inline QString provider_for_key(QString key) {
    key = key.trimmed().toLower();
    if (key.endsWith(QLatin1String("_api_key")))
        key.chop(8);
    else if (key.endsWith(QLatin1String("_base_url")))
        key.chop(9);
    return canonical_provider(key);
}

/// Limit the credential bag sent to one agent subprocess. The resolved model
/// key remains available for legacy agent paths; explicitly selected embedders
/// and the two keyed tool providers keep their own credentials. Unrelated LLM
/// keys never cross the process boundary and cannot become an implicit fallback.
inline QJsonObject scope_api_keys(const QJsonObject& keys, const QString& active_provider,
                                  const QJsonObject& config) {
    QSet<QString> allowed{canonical_provider(active_provider)};
    allowed.remove(QString());
    collect_embedder_providers(config, allowed);

    QJsonObject scoped;
    for (auto it = keys.constBegin(); it != keys.constEnd(); ++it) {
        if (allowed.contains(provider_for_key(it.key())))
            scoped.insert(it.key(), it.value());
    }
    return scoped;
}

} // namespace fincept::agent_credential_policy
