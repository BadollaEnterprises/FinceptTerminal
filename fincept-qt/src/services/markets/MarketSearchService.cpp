#include "services/markets/MarketSearchService.h"

#include "core/logging/Logger.h"
#include "services/dbnomics/DBnomicsService.h"
#include "services/instruments/InstrumentResolver.h"

#include <QPointer>

#include <algorithm>
#include <utility>

namespace fincept::services {

namespace {

constexpr int kMinLimit = 1;
constexpr int kMaxLimit = 50;

QList<MarketSearchService::Item> market_items(const QVector<core::instrument::Instrument>& instruments) {
    QList<MarketSearchService::Item> out;
    out.reserve(instruments.size());
    for (const auto& inst : instruments) {
        if (!inst.is_valid())
            continue;
        const QString display = inst.display.isEmpty() ? inst.symbol : inst.display;
        out.push_back({display, inst.name, inst.venue, core::instrument::asset_class_slug(inst.asset_class),
                       inst.country, inst.symbol});
    }
    return out;
}

} // namespace

MarketSearchService& MarketSearchService::instance() {
    static MarketSearchService s;
    return s;
}

MarketSearchService::MarketSearchService() {
    auto& dbnomics = DBnomicsService::instance();
    connect(&dbnomics, &DBnomicsService::search_results_loaded, this,
            [this](const QString& query, const QVector<DbnSearchResult>& results, const DbnPagination&, bool append) {
                if (append)
                    return;
                const QString key = query.trimmed().toCaseFolded();
                const QVector<EconomicRequest> requests = economic_requests_.take(key);
                for (const EconomicRequest& request : requests) {
                    QList<Item> items;
                    const int count = std::min(request.limit, int(results.size()));
                    items.reserve(count);
                    for (int i = 0; i < count; ++i) {
                        const DbnSearchResult& row = results.at(i);
                        const QString code = row.provider_code + QLatin1Char('/') + row.dataset_code;
                        const QString name = row.dataset_name.isEmpty() ? code : row.dataset_name;
                        items.push_back({code, name, row.provider_code, QStringLiteral("economic"), QString(), QString()});
                    }
                    emit results_ready(request.request_id, request.query, items);
                }
            });
    connect(&dbnomics, &DBnomicsService::error_occurred, this, [this](const QString& context, const QString& error) {
        if (context != QLatin1String("search"))
            return;
        const auto pending = std::exchange(economic_requests_, {});
        for (auto it = pending.cbegin(); it != pending.cend(); ++it)
            for (const EconomicRequest& request : it.value())
                emit search_failed(request.request_id, request.query, error);
    });
}

void MarketSearchService::search(const QString& query, const QString& type, int limit, const QString& request_id) {
    const QString q = query.trimmed();
    if (q.isEmpty()) {
        emit results_ready(request_id, query, {});
        return;
    }
    const int clamped = std::clamp(limit, kMinLimit, kMaxLimit);

    if (type.compare(QLatin1String("economic"), Qt::CaseInsensitive) == 0) {
        const QString key = q.toCaseFolded();
        const bool first = !economic_requests_.contains(key);
        economic_requests_[key].push_back(EconomicRequest{request_id, query, clamped});
        if (first)
            DBnomicsService::instance().global_search(q);
        return;
    }

    ResolveOptions options;
    options.limit = clamped;
    const core::instrument::AssetClass wanted = core::instrument::asset_class_from_slug(type);
    if (wanted != core::instrument::AssetClass::Unknown) {
        options.ctx.allowed_classes = {wanted};
        options.ctx.class_priority = {wanted};
        options.ctx.include_derivatives = wanted == core::instrument::AssetClass::Future ||
                                          wanted == core::instrument::AssetClass::Option;
    }

    QPointer<MarketSearchService> self = this;
    auto* request_context = new QObject(this);
    InstrumentResolver::instance().search(
        q, options, request_context,
        [self, request_context, request_id, query](const ResolveResult& result) {
            if (!self)
                return;
            const QList<Item> items = market_items(result.matches);
            if (!items.isEmpty() || result.final) {
                if (result.final && items.isEmpty() && !result.error.isEmpty()) {
                    LOG_WARN("MarketSearch", "public/local search failed: " + result.error);
                    emit self->search_failed(request_id, query, result.error);
                } else {
                    emit self->results_ready(request_id, query, items);
                }
            }
            if (result.final)
                request_context->deleteLater();
        });
}

} // namespace fincept::services
