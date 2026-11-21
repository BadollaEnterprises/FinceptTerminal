#pragma once

#include "services/maritime/MaritimeTypes.h"

#include <QDateTime>

#include <algorithm>
#include <cmath>

namespace fincept::services::maritime {

inline VesselData community_vessel_position(const QString& requested_imo) {
    const QString imo = requested_imo.trimmed().isEmpty() ? QStringLiteral("9000001") : requested_imo.trimmed();
    const quint32 seed = static_cast<quint32>(qHash(imo, size_t{0}));
    const double lat = -55.0 + static_cast<double>(seed % 11000) / 100.0;
    const double lng = -175.0 + static_cast<double>((seed / 17) % 35000) / 100.0;
    const QStringList ports = {QStringLiteral("Singapore"), QStringLiteral("Rotterdam"),
                               QStringLiteral("Shanghai"), QStringLiteral("Mumbai"),
                               QStringLiteral("Los Angeles"), QStringLiteral("Dubai")};

    VesselData vessel;
    vessel.id = static_cast<int>(seed & 0x7fffffff);
    vessel.imo = imo;
    vessel.name = QStringLiteral("Community Vessel %1").arg(imo.right(4));
    vessel.latitude = lat;
    vessel.longitude = lng;
    vessel.speed = 7.0 + static_cast<double>(seed % 120) / 10.0;
    vessel.angle = static_cast<double>(seed % 360);
    vessel.from_port = ports.at(static_cast<int>(seed % ports.size()));
    vessel.to_port = ports.at(static_cast<int>((seed / 7 + 1) % ports.size()));
    vessel.route_progress = static_cast<double>(seed % 100) / 100.0;
    vessel.draught = 6.0 + static_cast<double>(seed % 90) / 10.0;
    vessel.last_updated = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    vessel.fetched_at = vessel.last_updated;
    return vessel;
}

inline VesselsPage community_vessels_for_area(const AreaSearchParams& params) {
    const double min_lat = std::min(params.min_lat, params.max_lat);
    const double max_lat = std::max(params.min_lat, params.max_lat);
    const double min_lng = std::min(params.min_lng, params.max_lng);
    const double max_lng = std::max(params.min_lng, params.max_lng);
    const int count = std::clamp(params.limit > 0 ? params.limit : 80, 1, 200);
    const QDateTime now = QDateTime::currentDateTimeUtc();

    VesselsPage page;
    page.vessels.reserve(count);
    for (int i = 0; i < count; ++i) {
        VesselData vessel = community_vessel_position(QString::number(9'000'000 + i + 1));
        const double lat_fraction = std::fmod(0.5 + i * 0.61803398875, 1.0);
        const double lng_fraction = std::fmod(0.25 + i * 0.41421356237, 1.0);
        vessel.latitude = min_lat + (max_lat - min_lat) * lat_fraction;
        vessel.longitude = min_lng + (max_lng - min_lng) * lng_fraction;
        vessel.last_updated = now.addSecs(-i * 90).toString(Qt::ISODate);
        vessel.fetched_at = now.toString(Qt::ISODate);
        page.vessels.append(std::move(vessel));
    }
    page.total_count = page.vessels.size();
    page.found_count = page.vessels.size();
    page.credits_used = 0.0;
    page.remaining_credits = -1.0;
    page.fetched_at_ms = now.toMSecsSinceEpoch();
    return page;
}

inline VesselHistoryPage community_vessel_history(const QString& imo) {
    const QDateTime now = QDateTime::currentDateTimeUtc();
    const VesselData current = community_vessel_position(imo);
    VesselHistoryPage page;
    page.imo = current.imo;
    page.history.reserve(24);
    for (int i = 0; i < 24; ++i) {
        VesselData point = current;
        point.latitude = std::clamp(current.latitude - i * 0.12, -89.0, 89.0);
        point.longitude = current.longitude - i * 0.18;
        if (point.longitude < -180.0)
            point.longitude += 360.0;
        point.route_progress = std::max(0.0, current.route_progress - i * 0.02);
        point.last_updated = now.addSecs(-i * 3600).toString(Qt::ISODate);
        point.fetched_at = now.toString(Qt::ISODate);
        page.history.append(std::move(point));
    }
    page.total_records = page.history.size();
    page.credits_used = 0.0;
    page.remaining_credits = -1.0;
    return page;
}

} // namespace fincept::services::maritime
