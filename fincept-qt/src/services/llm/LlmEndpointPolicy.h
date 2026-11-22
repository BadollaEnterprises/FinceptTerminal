#pragma once

// Transport policy for endpoints that receive user-owned LLM API keys.
// Remote endpoints must use TLS. Plain HTTP remains available only for a
// service bound to this machine, which keeps local development/proxy workflows
// working without allowing a typo to disclose a key over the network.

#include <QUrl>
#include <QStringList>

namespace fincept::ai_chat {

inline bool is_loopback_llm_host(const QString& host) {
    const QString h = host.trimmed().toLower();
    if (h == QLatin1String("localhost") || h.endsWith(QLatin1String(".localhost")) ||
        h == QLatin1String("::1"))
        return true;

    // Every address in 127.0.0.0/8 is loopback. QUrl::host() has already
    // removed brackets from an IPv6 literal and rejects a malformed host.
    const QStringList octets = h.split(QLatin1Char('.'));
    if (octets.size() != 4 || octets.constFirst() != QLatin1String("127"))
        return false;
    for (const QString& octet : octets) {
        bool ok = false;
        const int value = octet.toInt(&ok);
        if (!ok || value < 0 || value > 255)
            return false;
    }
    return true;
}

inline bool is_allowed_llm_endpoint(const QString& endpoint) {
    const QUrl url(endpoint, QUrl::StrictMode);
    if (!url.isValid() || url.host().isEmpty())
        return false;
    const QString scheme = url.scheme().toLower();
    return scheme == QLatin1String("https") ||
           (scheme == QLatin1String("http") && is_loopback_llm_host(url.host()));
}

} // namespace fincept::ai_chat
