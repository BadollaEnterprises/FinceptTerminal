#pragma once

#include <QDateTime>
#include <QJsonArray>
#include <QJsonObject>

namespace fincept::services {

/// Convert Forex Factory's public weekly export to the stable schema consumed
/// by EconomicCalendarWidget. Source timestamps include an offset; dashboard
/// rows are stored in UTC because the widget applies the user's display zone.
inline QJsonArray normalize_public_macro_calendar(const QJsonArray& rows) {
    QJsonArray events;
    for (const QJsonValue& value : rows) {
        const QJsonObject row = value.toObject();
        const QString title = row.value(QStringLiteral("title")).toString().trimmed();
        const QDateTime source_time =
            QDateTime::fromString(row.value(QStringLiteral("date")).toString(), Qt::ISODate);
        if (title.isEmpty() || !source_time.isValid())
            continue;

        const QString impact = row.value(QStringLiteral("impact")).toString().trimmed();
        const int importance = impact.compare(QStringLiteral("high"), Qt::CaseInsensitive) == 0     ? 3
                               : impact.compare(QStringLiteral("medium"), Qt::CaseInsensitive) == 0 ? 2
                               : impact.compare(QStringLiteral("low"), Qt::CaseInsensitive) == 0    ? 1
                                                                                                      : 0;
        const QDateTime utc = source_time.toUTC();
        QJsonObject event{{QStringLiteral("event"), title},
                          {QStringLiteral("category"), QStringLiteral("Economic calendar")},
                          {QStringLiteral("country"), row.value(QStringLiteral("country")).toString()},
                          {QStringLiteral("date"), utc.date().toString(Qt::ISODate)},
                          {QStringLiteral("time"), utc.time().toString(QStringLiteral("HH:mm"))},
                          {QStringLiteral("actual"), QString()},
                          {QStringLiteral("forecast"), row.value(QStringLiteral("forecast")).toString()},
                          {QStringLiteral("consensus"), QString()},
                          {QStringLiteral("previous"), row.value(QStringLiteral("previous")).toString()},
                          {QStringLiteral("importance"), importance},
                          {QStringLiteral("impact_label"), impact},
                          {QStringLiteral("source"), QStringLiteral("Forex Factory public weekly export")}};
        events.append(event);
    }
    return events;
}

} // namespace fincept::services
