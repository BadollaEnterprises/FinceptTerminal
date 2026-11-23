#pragma once
// Small device-local cache for optional BYO explanations of deterministic
// analytics reports. Keys include the exact prompt and selected
// provider/model/profile, so changing any of them never reuses an explanation
// from another route.

#include <QString>

#include <optional>

namespace fincept::ui::pro_ai {

inline constexpr qint64 kExplanationCacheMaxAgeMs = 30LL * 24 * 60 * 60 * 1000;
inline constexpr int kExplanationCacheMaxEntries = 64;

struct CachedExplanation {
    QString text;
    QString route_label;
    QString billing_note;
    qint64 created_at_ms = 0;
};

/// SHA-256 hex key. `prompt_version` invalidates old entries after a prompt
/// contract change without reading or rewriting them.
QString explanation_cache_key(const QString& prompt, const QString& provider, const QString& model,
                              int prompt_version = 1, const QString& route_identity = {});

bool explanation_response_is_cacheable(bool success, bool cancelled, const QString& explanation,
                                       const QString& fallback_notice);

/// Load a fresh entry. Expired or malformed files are removed and missed.
std::optional<CachedExplanation> load_explanation_cache(const QString& directory, const QString& key,
                                                        qint64 now_ms, qint64 max_age_ms = kExplanationCacheMaxAgeMs);

/// Atomically save one entry, then retain only the newest `max_entries` files.
bool store_explanation_cache(const QString& directory, const QString& key, const CachedExplanation& entry,
                             int max_entries = kExplanationCacheMaxEntries);

} // namespace fincept::ui::pro_ai
