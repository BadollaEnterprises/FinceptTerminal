#include "screens/equity_research/EquitySegmentsData.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>

#include <algorithm>
#include <cmath>

namespace fincept::screens {

namespace {

void set_error(QString* error, const QString& message) {
    if (error)
        *error = message;
}

QString string_value(const QJsonObject& object, const char* key) {
    return object.value(QLatin1String(key)).toString().trimmed();
}

bool finite_number(const QJsonObject& object, const char* key, double* value) {
    const QJsonValue item = object.value(QLatin1String(key));
    if (!item.isDouble() || !std::isfinite(item.toDouble()))
        return false;
    *value = item.toDouble();
    return true;
}

} // namespace

std::optional<EquitySegmentRevenueData> parse_segment_revenue(const QByteArray& json, QString* error) {
    if (error)
        error->clear();
    QJsonParseError parse_error;
    const QJsonDocument document = QJsonDocument::fromJson(json, &parse_error);
    if (parse_error.error != QJsonParseError::NoError || !document.isObject()) {
        set_error(error, QStringLiteral("Invalid SEC segment response: %1").arg(parse_error.errorString()));
        return std::nullopt;
    }

    const QJsonObject root = document.object();
    if (!root.value(QStringLiteral("success")).toBool(false)) {
        const QString message = string_value(root, "error");
        set_error(error, message.isEmpty() ? QStringLiteral("SEC segment request failed.") : message);
        return std::nullopt;
    }
    if (!root.value(QStringLiteral("data")).isObject()) {
        set_error(error, QStringLiteral("SEC segment response has no data object."));
        return std::nullopt;
    }

    const QJsonObject data_object = root.value(QStringLiteral("data")).toObject();
    if (!data_object.value(QStringLiteral("periods")).isArray()) {
        set_error(error, QStringLiteral("SEC segment response has no periods array."));
        return std::nullopt;
    }

    EquitySegmentRevenueData data;
    data.symbol = string_value(data_object, "symbol");
    data.name = string_value(data_object, "name");
    data.reason = string_value(data_object, "reason");
    data.as_of = string_value(data_object, "as_of");
    const QJsonObject source = data_object.value(QStringLiteral("source")).toObject();
    data.source.name = string_value(source, "name");
    data.source.form = string_value(source, "form");
    data.source.filed = string_value(source, "filed");
    data.source.accession = string_value(source, "accession");
    data.source.url = string_value(source, "url");

    const QJsonArray periods = data_object.value(QStringLiteral("periods")).toArray();
    if (!periods.isEmpty() &&
        (data.source.name.isEmpty() || data.source.form.isEmpty() || data.source.filed.isEmpty() ||
         data.source.url.isEmpty())) {
        set_error(error, QStringLiteral("SEC segment response is missing filing provenance."));
        return std::nullopt;
    }
    for (qsizetype period_index = 0; period_index < periods.size(); ++period_index) {
        if (!periods[period_index].isObject()) {
            set_error(error, QStringLiteral("SEC segment period %1 is not an object.").arg(period_index + 1));
            return std::nullopt;
        }
        const QJsonObject period_object = periods[period_index].toObject();
        if (!period_object.value(QStringLiteral("breakdowns")).isArray()) {
            set_error(error, QStringLiteral("SEC segment period %1 has no breakdowns array.").arg(period_index + 1));
            return std::nullopt;
        }

        EquitySegmentPeriod period;
        period.start = string_value(period_object, "start");
        period.end = string_value(period_object, "end");
        period.unit = string_value(period_object, "unit");
        period.revenue_concept = string_value(period_object, "concept");
        period.total_revenue_concept = string_value(period_object, "total_concept");
        if (period.end.isEmpty() || period.unit.isEmpty() ||
            !finite_number(period_object, "total", &period.total) || period.total <= 0.0) {
            set_error(error, QStringLiteral("SEC segment period %1 has an invalid total.").arg(period_index + 1));
            return std::nullopt;
        }

        const QJsonArray breakdowns = period_object.value(QStringLiteral("breakdowns")).toArray();
        if (breakdowns.isEmpty()) {
            set_error(error, QStringLiteral("SEC segment period %1 has no breakdowns.").arg(period_index + 1));
            return std::nullopt;
        }
        for (qsizetype breakdown_index = 0; breakdown_index < breakdowns.size(); ++breakdown_index) {
            if (!breakdowns[breakdown_index].isObject()) {
                set_error(error, QStringLiteral("SEC segment breakdown is not an object."));
                return std::nullopt;
            }
            const QJsonObject breakdown_object = breakdowns[breakdown_index].toObject();
            if (!breakdown_object.value(QStringLiteral("members")).isArray()) {
                set_error(error, QStringLiteral("SEC segment breakdown has no members array."));
                return std::nullopt;
            }

            EquitySegmentBreakdown breakdown;
            breakdown.axis = string_value(breakdown_object, "axis");
            breakdown.label = string_value(breakdown_object, "label");
            if (!finite_number(breakdown_object, "reported_sum", &breakdown.reported_sum) ||
                !finite_number(breakdown_object, "reconciliation_delta", &breakdown.reconciliation_delta) ||
                breakdown.reported_sum <= 0.0) {
                set_error(error, QStringLiteral("SEC segment breakdown has invalid reconciliation values."));
                return std::nullopt;
            }

            const QJsonArray members = breakdown_object.value(QStringLiteral("members")).toArray();
            if (members.size() < 2) {
                set_error(error, QStringLiteral("SEC segment breakdown has fewer than two members."));
                return std::nullopt;
            }
            double member_sum = 0.0;
            for (const QJsonValue& member_value : members) {
                if (!member_value.isObject()) {
                    set_error(error, QStringLiteral("SEC segment member is not an object."));
                    return std::nullopt;
                }
                const QJsonObject member_object = member_value.toObject();
                EquitySegmentMember member;
                member.id = string_value(member_object, "member");
                member.label = string_value(member_object, "label");
                if (!finite_number(member_object, "value", &member.value) ||
                    !finite_number(member_object, "share", &member.share) || member.value < 0.0 ||
                    member.share < 0.0 || member.share > 1.001) {
                    set_error(error, QStringLiteral("SEC segment member has invalid values."));
                    return std::nullopt;
                }
                member_sum += member.value;
                breakdown.members.append(std::move(member));
            }
            const double sum_tolerance = std::max(std::abs(breakdown.reported_sum) * 1e-9, 1e-6);
            const double delta_tolerance = std::max(std::abs(period.total) * 1e-9, 1e-6);
            const double allowed_reconciliation = std::max(std::abs(period.total) * 0.001, 1.0);
            const double expected_delta = breakdown.reported_sum - period.total;
            if (std::abs(member_sum - breakdown.reported_sum) > sum_tolerance ||
                std::abs(breakdown.reconciliation_delta - expected_delta) > delta_tolerance ||
                std::abs(breakdown.reconciliation_delta) > allowed_reconciliation) {
                set_error(error, QStringLiteral("SEC segment breakdown does not reconcile to its reported total."));
                return std::nullopt;
            }
            period.breakdowns.append(std::move(breakdown));
        }
        data.periods.append(std::move(period));
    }
    return data;
}

} // namespace fincept::screens
