#pragma once

// External MCP servers speak JSON-RPC over stdout. Those protocol messages can
// contain complete tool inputs/results, account data, or provider credentials,
// so the diagnostics pane must never retain them verbatim. Keep only transport
// metadata for JSON payloads; ordinary process output is still useful after the
// shared log scrubber removes URLs, tokens, paths, and other secret-shaped text.

#include "core/privacy/LogRedact.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QString>

namespace fincept::mcp {

inline QString safe_process_log_line(const QString& line, bool standard_error) {
    const QString prefix = standard_error ? QStringLiteral("[stderr] ") : QStringLiteral("[stdout] ");
    const QByteArray encoded = line.toUtf8();
    QJsonParseError parse_error;
    const QJsonDocument document = QJsonDocument::fromJson(encoded, &parse_error);

    if (parse_error.error == QJsonParseError::NoError && !document.isNull()) {
        const QString bytes = QString::number(encoded.size());
        if (!document.isObject())
            return prefix + QStringLiteral("JSON payload bytes=") + bytes;

        const QJsonObject object = document.object();
        if (object.contains(QStringLiteral("error"))) {
            const QJsonObject error = object.value(QStringLiteral("error")).toObject();
            const QString code = error.value(QStringLiteral("code")).toVariant().toString();
            return prefix + QStringLiteral("JSON-RPC error") +
                   (code.isEmpty() ? QString() : QStringLiteral(" code=") + privacy::redact_text(code)) +
                   QStringLiteral(" bytes=") + bytes;
        }
        if (object.contains(QStringLiteral("result")))
            return prefix + QStringLiteral("JSON-RPC response bytes=") + bytes;
        if (object.contains(QStringLiteral("method"))) {
            const QString method = privacy::redact_text(object.value(QStringLiteral("method")).toString()).left(80);
            return prefix + QStringLiteral("JSON-RPC method=") +
                   (method.isEmpty() ? QStringLiteral("<unknown>") : method) + QStringLiteral(" bytes=") + bytes;
        }
        return prefix + QStringLiteral("JSON object bytes=") + bytes;
    }

    return prefix + privacy::redact_text(line);
}

} // namespace fincept::mcp
