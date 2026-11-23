#pragma once

#include <QJsonObject>

namespace fincept::services::quant {

/// Read commands may use an older provider that omits a `success` flag, but an
/// explicit failure must never be cached: the missing package/dataset/model can
/// be fixed before the list TTL expires.
inline bool is_cacheable_ai_quant_result(const QJsonObject& result) {
    return !result.contains(QStringLiteral("success")) || result.value(QStringLiteral("success")).toBool();
}

} // namespace fincept::services::quant
