#pragma once

#include "mcp/McpTypes.h"

#include <QCoreApplication>
#include <QJsonObject>
#include <QString>

namespace fincept::services {

inline mcp::ToolResult local_quantlib_failure(QString message, const QString& code) {
    if (message.trimmed().isEmpty())
        message = QCoreApplication::translate("QuantLibClient",
                                              "The local QuantLib adapter returned an incomplete answer.");
    auto failure = mcp::ToolResult::fail(message);
    failure.data = QJsonObject{{QStringLiteral("error_code"), code},
                               {QStringLiteral("local_only"), true},
                               {QStringLiteral("fincept_credits"), 0}};
    return failure;
}

/// Convert the deterministic Python adapter's envelope into the same ToolResult
/// contract as the network client. Pure so error envelopes cannot accidentally
/// become successful blank calculations (or increment successful local usage).
inline mcp::ToolResult parse_local_quantlib_response(const QJsonObject& answer) {
    if (!answer.value(QStringLiteral("supported")).toBool()) {
        return local_quantlib_failure(
            answer.value(QStringLiteral("message"))
                .toString(QCoreApplication::translate(
                    "QuantLibClient", "No zero-credit local implementation exists for this endpoint yet. "
                                      "No paid Fincept request was sent.")),
            QStringLiteral("LOCAL_UNSUPPORTED"));
    }

    const QString error = answer.value(QStringLiteral("error")).toString().trimmed();
    if (!error.isEmpty())
        return local_quantlib_failure(error, QStringLiteral("LOCAL_INPUT_ERROR"));

    if (!answer.contains(QStringLiteral("data"))) {
        return local_quantlib_failure(QCoreApplication::translate("QuantLibClient",
                                                                  "The local QuantLib adapter returned no result."),
                                      QStringLiteral("LOCAL_INVALID_RESPONSE"));
    }

    QJsonValue data = answer.value(QStringLiteral("data"));
    if (data.isObject()) {
        QJsonObject object = data.toObject();
        object.insert(QStringLiteral("_local"), answer.value(QStringLiteral("meta")));
        data = object;
    }
    return mcp::ToolResult::ok_data(data);
}

} // namespace fincept::services
