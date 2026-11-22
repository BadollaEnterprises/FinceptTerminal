#pragma once

#include <QProcessEnvironment>
#include <QStringList>

namespace fincept::process {

/// Whether a parent-process environment variable looks like a credential that
/// a child must not inherit implicitly. `explicitly_allowed` contains names the
/// caller deliberately configured for this exact child.
inline bool should_strip_unmanaged_credential(const QString& key, const QStringList& explicitly_allowed = {}) {
    for (const QString& allowed : explicitly_allowed) {
        if (key.compare(allowed, Qt::CaseInsensitive) == 0)
            return false;
    }

    const QString upper = key.trimmed().toUpper();
    if (upper.isEmpty())
        return false;

    static const QStringList kExact = {
        QStringLiteral("AWS_ACCESS_KEY_ID"),
        QStringLiteral("DOCKER_AUTH_CONFIG"),
        QStringLiteral("GOOGLE_APPLICATION_CREDENTIALS"),
    };
    if (kExact.contains(upper))
        return true;

    static const QStringList kSuffixes = {
        QStringLiteral("_API_KEY"),       QStringLiteral("_SECRET"),
        QStringLiteral("_SECRET_KEY"),    QStringLiteral("_ACCESS_KEY"),
        QStringLiteral("_ACCESS_KEY_ID"), QStringLiteral("_ACCESS_TOKEN"),
        QStringLiteral("_AUTH_TOKEN"),    QStringLiteral("_SESSION_TOKEN"),
        QStringLiteral("_PASSWORD"),      QStringLiteral("_PRIVATE_KEY"),
        QStringLiteral("_TOKEN"),         QStringLiteral("_PAT"),
    };
    for (const QString& suffix : kSuffixes) {
        if (upper.endsWith(suffix))
            return true;
    }
    return false;
}

/// Remove credential-shaped inherited variables while preserving variables
/// explicitly declared for this child process. Matching is case-insensitive so
/// a lower-case configured key also protects its platform-normalized form.
inline void strip_unmanaged_credentials(QProcessEnvironment& env, const QStringList& explicitly_allowed = {}) {
    const QStringList keys = env.keys();
    for (const QString& key : keys) {
        if (should_strip_unmanaged_credential(key, explicitly_allowed))
            env.remove(key);
    }
}

} // namespace fincept::process
