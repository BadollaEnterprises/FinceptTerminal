#pragma once
// Device-local cache for successful Report Builder AI drafts. Cache keys bind
// the exact prompt to the selected provider/model/profile; API keys are never stored.

#include <QString>

#include <optional>

namespace fincept::services::report_ai_cache {

inline constexpr qint64 kDraftCacheMaxAgeMs = 30LL * 24 * 60 * 60 * 1000;
inline constexpr int kDraftCacheMaxEntries = 96;

struct DraftCacheEntry {
    QString text;
    QString route_label;
    qint64 created_at_ms = 0;
};

QString draft_cache_key(const QString& system, const QString& user, const QString& provider,
                        const QString& model, int prompt_version = 1,
                        const QString& route_identity = {});

std::optional<DraftCacheEntry> load_draft_cache(const QString& directory, const QString& key,
                                                qint64 now_ms, qint64 max_age_ms = kDraftCacheMaxAgeMs);

bool store_draft_cache(const QString& directory, const QString& key, const DraftCacheEntry& entry,
                       int max_entries = kDraftCacheMaxEntries);

} // namespace fincept::services::report_ai_cache
